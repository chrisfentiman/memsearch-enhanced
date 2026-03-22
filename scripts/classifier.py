#!/usr/bin/env python3
# /// script
# dependencies = ["fastembed>=0.3.0", "numpy>=1.24.0"]
# ///
"""
Shared semantic classifier daemon for memsearch-enhanced.

One daemon serves ALL Claude Code sessions. First session starts it,
others reuse. Idle timeout auto-exits after no requests.

Protocol (Unix socket, JSON):
  Request:  {"prompt": "...", "project": "/path/to/repo"}
  Response: {"needs_context": true, "ctx_score": 0.72, "no_ctx_score": 0.41}

Usage:
  uv run classifier.py --daemon          # start shared daemon
  uv run classifier.py --generate        # generate default exemplar embeddings
  uv run classifier.py "prompt" /path    # single-shot classify
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
import threading
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
IDLE_TIMEOUT_SECONDS = 1800  # 30 minutes
SCRIPT_DIR = Path(__file__).parent
GLOBAL_EXEMPLARS_DIR = Path.home() / ".claude" / "context"
THRESHOLD = 0.40


# --- Exemplar loading ---


def _load_toml(path: Path) -> tuple[list[str], list[str]] | None:
    """Load exemplars from a TOML file. Returns None if invalid."""
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        needs = data.get("needs_context", {}).get("examples", [])
        no_needs = data.get("no_context", {}).get("examples", [])
        if needs and no_needs:
            return needs, no_needs
    except Exception:
        pass
    return None


def load_exemplars_for_project(project: str) -> tuple[list[str], list[str]]:
    """Load exemplar lists from config chain for a specific project."""
    project_path = Path(project)
    sources = [
        project_path / ".claude" / "context" / "exemplars.toml",
        GLOBAL_EXEMPLARS_DIR / "exemplars.toml",
        SCRIPT_DIR / "exemplars.toml",
    ]
    for path in sources:
        result = _load_toml(path)
        if result:
            return result

    # Minimal fallback
    return (
        ["fix the bug", "what did we decide", "refactor the module", "why is the test failing"],
        ["hello", "thanks", "what is REST", "explain git branching"],
    )


# --- Per-project embedding cache ---


class ProjectCache:
    """Caches exemplar embeddings per project, auto-regenerates when stale."""

    def __init__(self, model: TextEmbedding):
        self.model = model
        self._cache: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}  # project -> (ctx, no_ctx, mtime)

    def get(self, project: str) -> tuple[np.ndarray, np.ndarray]:
        """Get exemplar embeddings for a project, regenerating if needed."""
        project_path = Path(project)
        toml_path = project_path / ".claude" / "context" / "exemplars.toml"

        # Check if cache is valid
        current_mtime = toml_path.stat().st_mtime if toml_path.exists() else 0.0

        if project in self._cache:
            ctx, no_ctx, cached_mtime = self._cache[project]
            if cached_mtime >= current_mtime:
                return ctx, no_ctx

        # Generate fresh embeddings
        needs, no_needs = load_exemplars_for_project(project)
        ctx = np.array(list(self.model.embed(needs)))
        no_ctx = np.array(list(self.model.embed(no_needs)))
        self._cache[project] = (ctx, no_ctx, current_mtime)

        return ctx, no_ctx


# --- Decision logging ---


def log_decision(project: str, prompt: str, result: dict) -> None:
    """Append classification decision to the project's log."""
    log_dir = Path(project) / ".claude" / "context"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "classifier.jsonl"

    entry = {
        "timestamp": time.time(),
        "prompt": prompt[:200],
        **result,
    }
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
    """Classify a prompt as needing project context or not."""
    if len(prompt.strip()) < 10:
        return {"needs_context": False, "ctx_score": 0.0, "no_ctx_score": 0.0, "reason": "too_short"}

    ctx_embeds, no_ctx_embeds = cache.get(project)
    emb = np.array(list(model.embed([prompt])))[0]

    ctx_score = float(np.max(emb @ ctx_embeds.T))
    no_ctx_score = float(np.max(emb @ no_ctx_embeds.T))

    needs_context = ctx_score > no_ctx_score and ctx_score > THRESHOLD

    result = {
        "needs_context": needs_context,
        "ctx_score": round(ctx_score, 4),
        "no_ctx_score": round(no_ctx_score, 4),
    }

    try:
        log_decision(project, prompt, result)
    except Exception:
        pass

    return result


# --- Daemon ---


def acquire_lock() -> bool:
    """Try to acquire the daemon lock. Returns True if we got it."""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # Check if the existing daemon is still alive
        try:
            with open(LOCK_PATH) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # signal 0 = check if alive
            return False  # daemon is running
        except (ValueError, ProcessLookupError, PermissionError):
            # Stale lock
            os.unlink(LOCK_PATH)
            return acquire_lock()


def serve() -> None:
    """Run as a shared Unix socket daemon with idle timeout."""
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
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        try:
            os.unlink(LOCK_PATH)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    print(f"[classifier] Listening on {SOCKET_PATH} (idle timeout: {IDLE_TIMEOUT_SECONDS}s)", file=sys.stderr)

    while True:
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            print("[classifier] Idle timeout reached, shutting down", file=sys.stderr)
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
                conn.sendall(json.dumps({"needs_context": False, "error": str(e)}).encode())
            except Exception:
                pass
        finally:
            conn.close()


# --- Entry points ---


def generate_default_exemplars() -> None:
    """Generate embeddings for the default exemplar set."""
    model = TextEmbedding(MODEL_NAME, threads=2)
    needs, no_needs = load_exemplars_for_project(".")
    ctx = np.array(list(model.embed(needs)))
    no_ctx = np.array(list(model.embed(no_needs)))

    GLOBAL_EXEMPLARS_DIR.mkdir(parents=True, exist_ok=True)
    np.save(GLOBAL_EXEMPLARS_DIR / "exemplars_context.npy", ctx)
    np.save(GLOBAL_EXEMPLARS_DIR / "exemplars_no_context.npy", no_ctx)
    print(f"Generated: {len(needs)} context, {len(no_needs)} no-context", file=sys.stderr)


def main() -> None:
    if "--daemon" in sys.argv:
        serve()
    elif "--generate" in sys.argv:
        generate_default_exemplars()
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        prompt = sys.argv[1]
        project = sys.argv[2] if len(sys.argv) > 2 else "."
        model = TextEmbedding(MODEL_NAME, threads=2)
        cache = ProjectCache(model)
        result = classify(model, cache, prompt, project)
        print(json.dumps(result))
    else:
        print("Usage: classifier.py [--daemon | --generate | 'prompt' [project_path]]")
        sys.exit(1)


if __name__ == "__main__":
    main()
