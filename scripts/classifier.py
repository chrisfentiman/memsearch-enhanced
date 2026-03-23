#!/usr/bin/env python3
# /// script
# dependencies = ["fastembed>=0.3.0", "numpy>=1.24.0"]
# ///
"""
Shared semantic classifier daemon for memsearch-enhanced.

Four-category routing:
  - needs_memory: inject memsearch memories (decisions, corrections, preferences)
  - needs_code: inject code search results (architecture, debugging, finding code)
  - needs_both: inject memories AND code search
  - no_context: skip injection entirely

One daemon serves ALL Claude Code sessions. First session starts it,
others reuse. Idle timeout auto-exits after no requests.

Protocol (Unix socket, JSON):
  Request:  {"prompt": "...", "project": "/path/to/repo"}
  Response: {"category": "needs_memory", "scores": {...}, "inject": "memory"}
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

import numpy as np

try:
    from fastembed import TextEmbedding
except ImportError:
    print("fastembed not installed", file=sys.stderr)
    sys.exit(1)

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

SOCKET_PATH = "/tmp/memsearch-classify.sock"
LOCK_PATH = "/tmp/memsearch-classify.lock"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
IDLE_TIMEOUT_SECONDS = 1800
SCRIPT_DIR = Path(__file__).parent
PLUGIN_VERSION = (SCRIPT_DIR.parent / "version.txt").read_text().strip() if (SCRIPT_DIR.parent / "version.txt").exists() else "unknown"
THRESHOLD = 0.40

CATEGORIES = [
    "needs_memory",
    "needs_code",
    "needs_both",
    "no_context",
]

# What each category injects
INJECT_MAP = {
    "needs_memory": "memory",
    "needs_code": "code",
    "needs_both": "both",
    "no_context": False,
}


# --- Exemplar loading ---


def _load_toml(path: Path) -> dict[str, list[str]] | None:
    """Load exemplars from a four-category TOML file."""
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        result = {}
        for cat in CATEGORIES:
            examples = data.get(cat, {}).get("examples", [])
            if examples:
                result[cat] = examples
        if len(result) >= 2:  # need at least 2 categories
            return result
    except Exception:
        pass
    return None


def load_exemplars_for_project(project: str) -> dict[str, list[str]]:
    """Load exemplar lists from config chain for a specific project."""
    project_path = Path(project)
    sources = [
        project_path / ".claude" / "context" / "exemplars.toml",
        Path.home() / ".claude" / "context" / "exemplars.toml",
        SCRIPT_DIR / "exemplars.toml",
    ]
    for path in sources:
        result = _load_toml(path)
        if result:
            return result

    # Minimal fallback
    return {
        "needs_memory": ["why did we decide that", "what corrections did you get from me"],
        "needs_code": ["how does the auth middleware work", "where is the config validation"],
        "needs_both": ["we discussed refactoring that module, show me the code", "what did we decide about the database schema, find the migration"],
        "no_context": ["thanks", "yes do it", "run the tests", "fix the typo"],
    }


# --- Per-project embedding cache ---


class EmbeddingCache:
    """Caches exemplar embeddings per source file, auto-regenerates when stale."""

    def __init__(self, model: TextEmbedding):
        self.model = model
        # path_str -> {category -> (embeddings, mtime)}
        self._cache: dict[str, tuple[dict[str, np.ndarray], float]] = {}

    def _load_and_embed(self, path: Path) -> tuple[dict[str, np.ndarray], float]:
        """Load a TOML file and embed all categories."""
        mtime = path.stat().st_mtime if path.exists() else 0.0
        result: dict[str, np.ndarray] = {}

        loaded = _load_toml(path)
        if loaded:
            for cat, examples in loaded.items():
                if examples:
                    result[cat] = np.array(list(self.model.embed(examples)))

        return result, mtime

    def get_for_file(self, path: Path) -> dict[str, np.ndarray]:
        """Get embeddings for a specific TOML file, regenerating if stale."""
        key = str(path)
        current_mtime = path.stat().st_mtime if path.exists() else 0.0

        if key in self._cache:
            cached_embeds, cached_mtime = self._cache[key]
            if cached_mtime >= current_mtime and current_mtime > 0:
                return cached_embeds

        if not path.exists():
            return {}

        embeds, mtime = self._load_and_embed(path)
        self._cache[key] = (embeds, mtime)
        return embeds

    def get_for_project(self, project: str) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
        """Get project, global, and bootstrap embeddings separately.

        Returns: (project_embeds, global_embeds, bootstrap_embeds)
        """
        project_path = Path(project) / ".claude" / "context" / "exemplars.toml"
        global_path = Path.home() / ".claude" / "context" / "exemplars.toml"
        bootstrap_path = SCRIPT_DIR / "exemplars.toml"

        return (
            self.get_for_file(project_path),
            self.get_for_file(global_path),
            self.get_for_file(bootstrap_path),
        )


# --- Decision logging ---


def log_decision(project: str, prompt: str, result: dict) -> None:
    log_dir = Path(project) / ".claude" / "context"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "classifier.jsonl"
    entry = {"timestamp": time.time(), "prompt": prompt[:200], **result}
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# --- Classification ---


PROJECT_WEIGHT = 1.15  # project exemplars get 15% boost


def classify(
    model: TextEmbedding,
    cache: EmbeddingCache,
    prompt: str,
    project: str,
) -> dict:
    """Classify a prompt into one of four categories.

    Scoring priority: project > global > bootstrap.
    Project exemplars get a weight boost so they override global when close.
    """
    if len(prompt.strip()) < 10:
        return {
            "category": "no_context",
            "inject": False,
            "scores": {},
            "reason": "too_short",
        }

    project_embeds, global_embeds, bootstrap_embeds = cache.get_for_project(project)
    query_emb = np.array(list(model.embed([prompt])))[0]

    # Score against each tier, take the best score per category
    scores: dict[str, float] = {cat: 0.0 for cat in CATEGORIES}

    for cat in CATEGORIES:
        cat_scores = []

        # Project exemplars (weighted higher)
        if cat in project_embeds and project_embeds[cat].shape[0] > 0:
            s = float(np.max(query_emb @ project_embeds[cat].T))
            cat_scores.append(s * PROJECT_WEIGHT)

        # Global exemplars
        if cat in global_embeds and global_embeds[cat].shape[0] > 0:
            s = float(np.max(query_emb @ global_embeds[cat].T))
            cat_scores.append(s)

        # Bootstrap exemplars (fallback)
        if cat in bootstrap_embeds and bootstrap_embeds[cat].shape[0] > 0:
            s = float(np.max(query_emb @ bootstrap_embeds[cat].T))
            cat_scores.append(s)

        if cat_scores:
            scores[cat] = round(max(cat_scores), 4)

    # Best category
    best_cat = max(scores, key=scores.get)
    best_score = scores[best_cat]

    if best_score < THRESHOLD:
        best_cat = "no_context"

    inject = INJECT_MAP.get(best_cat, False)

    # Ambiguity: if top two are within 0.05 and one needs context, prefer injection
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_scores) >= 2:
        _, top_score = sorted_scores[0]
        second_cat, second_score = sorted_scores[1]
        if top_score - second_score < 0.05 and INJECT_MAP.get(second_cat, False):
            if not inject:
                inject = INJECT_MAP[second_cat]
                best_cat = second_cat

    result = {
        "category": best_cat,
        "inject": inject,
        "scores": scores,
    }

    try:
        log_decision(project, prompt, result)
    except Exception:
        pass

    return result


# --- Daemon ---


def acquire_lock() -> bool:
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            with open(LOCK_PATH) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            os.unlink(LOCK_PATH)
            return acquire_lock()


def serve() -> None:
    if not acquire_lock():
        print("[classifier] Another daemon is already running", file=sys.stderr)
        sys.exit(0)

    model = TextEmbedding(MODEL_NAME, threads=2)
    cache = EmbeddingCache(model)

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    sock.listen(10)
    sock.settimeout(IDLE_TIMEOUT_SECONDS)

    def cleanup(*_):
        for p in [SOCKET_PATH, LOCK_PATH]:
            try:
                os.unlink(p)
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    print(f"[classifier] 4-category router on {SOCKET_PATH}", file=sys.stderr)

    while True:
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            print("[classifier] Idle timeout, shutting down", file=sys.stderr)
            cleanup()
            break

        try:
            conn.settimeout(5.0)
            data = b""
            while True:
                try:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                    # Check if we have complete JSON (ends with })
                    stripped = data.strip()
                    if stripped.endswith(b"}"):
                        try:
                            json.loads(stripped)
                            break  # Valid JSON received
                        except json.JSONDecodeError:
                            continue  # Incomplete, keep reading
                except socket.timeout:
                    break

            request = json.loads(data.decode("utf-8", errors="replace"))
            req_type = request.get("type", "classify")

            if req_type == "version":
                conn.sendall(json.dumps({"version": PLUGIN_VERSION}).encode())
            else:
                prompt = request.get("prompt", "")
                project = request.get("project", ".")
                result = classify(model, cache, prompt, project)
                conn.sendall(json.dumps(result).encode())
        except Exception as e:
            print(f"[classifier] Error: {e}", file=sys.stderr)
            try:
                conn.sendall(json.dumps({"category": "no_context", "inject": False}).encode())
            except Exception:
                pass
        finally:
            conn.close()


def main() -> None:
    if "--daemon" in sys.argv:
        serve()
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        prompt = sys.argv[1]
        project = sys.argv[2] if len(sys.argv) > 2 else "."
        model = TextEmbedding(MODEL_NAME, threads=2)
        cache = EmbeddingCache(model)
        result = classify(model, cache, prompt, project)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: classifier.py [--daemon | 'prompt' [project_path]]")
        sys.exit(1)


if __name__ == "__main__":
    main()
