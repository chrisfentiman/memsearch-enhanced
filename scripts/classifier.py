#!/usr/bin/env python3
# /// script
# dependencies = ["fastembed>=0.3.0", "numpy>=1.24.0"]
# ///
"""
Shared semantic classifier daemon for memsearch-enhanced.

Four-category routing:
  - needs_context_project: inject code context + memories
  - needs_context_global: inject memories only
  - no_context_project: skip (routine project work)
  - no_context_global: skip (general question)

One daemon serves ALL Claude Code sessions. First session starts it,
others reuse. Idle timeout auto-exits after no requests.

Protocol (Unix socket, JSON):
  Request:  {"prompt": "...", "project": "/path/to/repo"}
  Response: {"category": "needs_context_project", "scores": {...}, "inject": true}
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
THRESHOLD = 0.40

CATEGORIES = [
    "needs_context_project",
    "needs_context_global",
    "no_context_project",
    "no_context_global",
]

# Categories that trigger context injection
INJECT_CATEGORIES = {"needs_context_project", "needs_context_global"}


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

    # Minimal fallback (two categories)
    return {
        "needs_context_project": ["fix the bug in the auth module", "how does the caching work"],
        "needs_context_global": ["continue where we left off", "what did we decide"],
        "no_context_project": ["update the readme", "run the tests"],
        "no_context_global": ["hello", "what is REST"],
    }


# --- Per-project embedding cache ---


class ProjectCache:
    """Caches exemplar embeddings per project per category."""

    def __init__(self, model: TextEmbedding):
        self.model = model
        # project -> {category -> (embeddings, mtime)}
        self._cache: dict[str, dict[str, tuple[np.ndarray, float]]] = {}

    def get(self, project: str) -> dict[str, np.ndarray]:
        """Get exemplar embeddings for all categories for a project."""
        project_path = Path(project)
        toml_path = project_path / ".claude" / "context" / "exemplars.toml"
        current_mtime = toml_path.stat().st_mtime if toml_path.exists() else 0.0

        if project in self._cache:
            # Check if cache is still valid (use first category's mtime)
            first_cat = next(iter(self._cache[project]))
            _, cached_mtime = self._cache[project][first_cat]
            if cached_mtime >= current_mtime:
                return {cat: emb for cat, (emb, _) in self._cache[project].items()}

        # Generate fresh embeddings
        exemplars = load_exemplars_for_project(project)
        cached = {}
        result = {}
        for cat, examples in exemplars.items():
            emb = np.array(list(self.model.embed(examples)))
            cached[cat] = (emb, current_mtime)
            result[cat] = emb
        self._cache[project] = cached
        return result


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


def classify(
    model: TextEmbedding,
    cache: ProjectCache,
    prompt: str,
    project: str,
) -> dict:
    """Classify a prompt into one of four categories."""
    if len(prompt.strip()) < 10:
        return {
            "category": "no_context_global",
            "inject": False,
            "scores": {},
            "reason": "too_short",
        }

    cat_embeds = cache.get(project)
    query_emb = np.array(list(model.embed([prompt])))[0]

    # Score against each category
    scores = {}
    for cat, embeds in cat_embeds.items():
        similarities = query_emb @ embeds.T
        scores[cat] = round(float(np.max(similarities)), 4)

    # Best category
    best_cat = max(scores, key=scores.get)
    best_score = scores[best_cat]

    # Must exceed threshold
    if best_score < THRESHOLD:
        best_cat = "no_context_global"

    inject = best_cat in INJECT_CATEGORIES

    # Check for ambiguity: if top two scores are within 0.05,
    # and either needs context, err on the side of injecting
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_scores) >= 2:
        top_cat, top_score = sorted_scores[0]
        second_cat, second_score = sorted_scores[1]
        if top_score - second_score < 0.05 and second_cat in INJECT_CATEGORIES:
            inject = True
            best_cat = second_cat  # prefer the inject category

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
    cache = ProjectCache(model)

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
            data = b""
            while True:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                data += chunk

            request = json.loads(data.decode("utf-8", errors="replace"))
            prompt = request.get("prompt", "")
            project = request.get("project", ".")

            result = classify(model, cache, prompt, project)
            conn.sendall(json.dumps(result).encode())
        except Exception as e:
            print(f"[classifier] Error: {e}", file=sys.stderr)
            try:
                conn.sendall(json.dumps({"category": "no_context_global", "inject": False}).encode())
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
        cache = ProjectCache(model)
        result = classify(model, cache, prompt, project)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: classifier.py [--daemon | 'prompt' [project_path]]")
        sys.exit(1)


if __name__ == "__main__":
    main()
