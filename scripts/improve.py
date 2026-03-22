#!/usr/bin/env python3
# /// script
# dependencies = ["fastembed>=0.3.0", "numpy>=1.24.0"]
# ///
"""
Self-improvement for the semantic classifier.

1. Analyzes session transcripts to find candidate exemplars from tool usage
2. Validates candidates against the bootstrap classifier (quality gate)
3. Merges with existing exemplars, scores all against bootstrap
4. Keeps only top-N highest quality exemplars per category
5. Splits into global (generic patterns) vs project (codebase-specific)

Usage:
  uv run improve.py <transcript.jsonl>
  uv run improve.py --auto
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

try:
    from fastembed import TextEmbedding
except ImportError:
    print("fastembed not installed", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent
MAX_EXEMPLARS_PER_CATEGORY = 30
QUALITY_THRESHOLD = 0.35  # minimum similarity to bootstrap to be accepted

# Tools that indicate the prompt needed project context
CONTEXT_TOOLS = {
    "Read", "Edit", "Write", "Glob", "Grep",
    "Agent",
    "mcp__claude-context__search_code",
    "mcp__claude-context__index_codebase",
    "mcp__perplexity__search",
    "mcp__deepwiki__ask_question",
    "Skill",
}

SIMPLE_TOOLS = {"Bash"}


# --- Bootstrap exemplars (quality gate) ---


def load_bootstrap() -> tuple[list[str], list[str]]:
    """Load the plugin's bootstrap exemplars (never modified by self-improvement)."""
    bootstrap_path = SCRIPT_DIR / "exemplars.toml"
    if not bootstrap_path.exists():
        return (
            ["fix the bug", "what did we decide", "refactor the module"],
            ["hello", "thanks", "what is REST"],
        )
    with open(bootstrap_path, "rb") as f:
        data = tomllib.load(f)
    return (
        data.get("needs_context", {}).get("examples", []),
        data.get("no_context", {}).get("examples", []),
    )


def load_existing_exemplars(path: Path) -> tuple[list[str], list[str]]:
    """Load existing exemplars from a TOML file."""
    if not path.exists():
        return [], []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return (
            data.get("needs_context", {}).get("examples", []),
            data.get("no_context", {}).get("examples", []),
        )
    except Exception:
        return [], []


# --- Transcript analysis ---


def parse_transcript(path: Path) -> list[dict]:
    messages = []
    with open(path) as f:
        for line in f:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def _clean_prompt(text: str) -> str | None:
    """Clean a prompt for use as an exemplar. Returns None if unsuitable."""
    text = text.strip()

    # Too short or too long
    if len(text) < 15 or len(text) > 150:
        return None

    # Contains transcript/markdown/XML artifacts
    if any(marker in text for marker in [
        "<!--", "```", "###", "===", "<system-reminder>",
        "<channel", "<command", "<task-notification", "<local-command",
    ]):
        return None

    # Contains file paths (too specific)
    if "/Users/" in text or "/.claude/" in text or "/opt/" in text:
        return None

    # All caps / angry (not a good exemplar)
    if text.isupper():
        return None

    # Take first sentence only if multi-sentence
    for sep in [". ", "? ", "! ", "\n"]:
        if sep in text:
            text = text[:text.index(sep) + 1]
            break

    return text.strip()[:120]


def extract_turns(messages: list[dict]) -> list[dict]:
    """Extract user prompts and the tools used in the following response."""
    turns = []
    current_prompt = None
    current_tools: set[str] = set()

    for msg in messages:
        mtype = msg.get("type", "")
        content = msg.get("message", {}).get("content", "")

        if mtype == "user" and isinstance(content, str) and len(content.strip()) > 10:
            if current_prompt is not None:
                turns.append({"prompt": current_prompt, "tools": current_tools})
            # Clean the prompt for exemplar use
            cleaned = _clean_prompt(content)
            current_prompt = cleaned  # None if unsuitable
            current_tools = set()

        if mtype == "assistant" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    current_tools.add(block.get("name", ""))

    if current_prompt is not None:
        turns.append({"prompt": current_prompt, "tools": current_tools})

    return turns


def load_decisions(project_dir: Path) -> dict[str, dict]:
    log_file = project_dir / ".claude" / "context" / "classifier.jsonl"
    decisions: dict[str, dict] = {}
    if log_file.exists():
        with open(log_file) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    decisions[entry["prompt"]] = entry
                except (json.JSONDecodeError, KeyError):
                    continue
    return decisions


# --- Quality scoring ---


def score_exemplars(
    model: TextEmbedding,
    candidates: list[str],
    bootstrap_embeds: np.ndarray,
) -> list[tuple[str, float]]:
    """Score each candidate by max cosine similarity to bootstrap exemplars."""
    if not candidates:
        return []
    candidate_embeds = np.array(list(model.embed(candidates)))
    scores = candidate_embeds @ bootstrap_embeds.T
    max_scores = np.max(scores, axis=1)
    return [(c, float(s)) for c, s in zip(candidates, max_scores)]


def validate_and_rank(
    model: TextEmbedding,
    candidates: list[str],
    existing: list[str],
    bootstrap_embeds: np.ndarray,
    max_n: int,
) -> list[str]:
    """Validate candidates against bootstrap, merge with existing, keep top N."""
    # Combine all
    all_exemplars = list(set(candidates + existing))

    if not all_exemplars:
        return []

    # Score against bootstrap
    scored = score_exemplars(model, all_exemplars, bootstrap_embeds)

    # Filter by quality threshold
    qualified = [(ex, score) for ex, score in scored if score >= QUALITY_THRESHOLD]

    # Sort by score descending, keep top N
    qualified.sort(key=lambda x: x[1], reverse=True)
    return [ex for ex, _ in qualified[:max_n]]


# --- Global vs project split ---


def split_global_project(
    model: TextEmbedding,
    exemplars: list[str],
    bootstrap_embeds: np.ndarray,
    threshold: float = 0.65,
) -> tuple[list[str], list[str]]:
    """Split exemplars into global (generic) vs project (codebase-specific).

    If an exemplar is highly similar to a bootstrap exemplar, it's a generic
    pattern (global). If dissimilar, it's project-specific.
    """
    if not exemplars:
        return [], []

    embeds = np.array(list(model.embed(exemplars)))
    scores = embeds @ bootstrap_embeds.T
    max_scores = np.max(scores, axis=1)

    global_exemplars = []
    project_exemplars = []

    for ex, score in zip(exemplars, max_scores):
        if score >= threshold:
            global_exemplars.append(ex)
        else:
            project_exemplars.append(ex)

    return global_exemplars, project_exemplars


# --- Write exemplars ---


def escape_toml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def write_exemplars(path: Path, needs: list[str], no_needs: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("[needs_context]\nexamples = [\n")
        for ex in sorted(needs):
            f.write(f'    "{escape_toml(ex)}",\n')
        f.write("]\n\n[no_context]\nexamples = [\n")
        for ex in sorted(no_needs):
            f.write(f'    "{escape_toml(ex)}",\n')
        f.write("]\n")


# --- Main ---


def main() -> None:
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))

    # Find transcript
    if "--auto" in sys.argv:
        transcript_dir = Path.home() / ".claude" / "projects"
        project_key = str(project_dir.resolve()).replace("/", "-").lstrip("-")
        project_transcript_dir = transcript_dir / project_key
        if not project_transcript_dir.exists():
            print(f"No transcripts found at {project_transcript_dir}", file=sys.stderr)
            sys.exit(1)
        transcripts = sorted(project_transcript_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not transcripts:
            print("No transcript files found", file=sys.stderr)
            sys.exit(1)
        transcript_path = transcripts[-1]
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        transcript_path = Path(sys.argv[1])
    else:
        print("Usage: improve.py [--auto | <transcript.jsonl>]", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing: {transcript_path}", file=sys.stderr)

    # Load everything
    messages = parse_transcript(transcript_path)
    turns = extract_turns(messages)
    decisions = load_decisions(project_dir)
    bootstrap_needs, bootstrap_no = load_bootstrap()

    print(f"Found {len(turns)} turns, {len(decisions)} classifier decisions", file=sys.stderr)

    # Extract candidates from transcript
    candidate_needs: list[str] = []
    candidate_no: list[str] = []

    for turn in turns:
        prompt = turn["prompt"]
        if prompt is None:
            continue
        tools = turn["tools"]
        used_context_tools = tools & CONTEXT_TOOLS
        actually_needed = len(used_context_tools) > 0
        decision = decisions.get(prompt)

        if decision is None:
            # Bootstrap: learn from tool usage
            if actually_needed:
                candidate_needs.append(prompt)
            elif tools and not (tools - SIMPLE_TOOLS - {""}):
                candidate_no.append(prompt)
        else:
            classified_as_needing = decision.get("needs_context", False)
            # Misclassifications
            if not classified_as_needing and actually_needed:
                candidate_needs.append(prompt)
            elif classified_as_needing and not actually_needed and tools and not (tools - SIMPLE_TOOLS - {""}):
                candidate_no.append(prompt)

    if not candidate_needs and not candidate_no:
        print("No new candidates found.", file=sys.stderr)
        return

    print(
        f"Candidates: {len(candidate_needs)} needs_context, {len(candidate_no)} no_context",
        file=sys.stderr,
    )

    # Load model and bootstrap embeddings
    model = TextEmbedding("BAAI/bge-small-en-v1.5", threads=2)
    bootstrap_needs_embeds = np.array(list(model.embed(bootstrap_needs)))
    bootstrap_no_embeds = np.array(list(model.embed(bootstrap_no)))

    # Load existing exemplars
    project_exemplar_path = project_dir / ".claude" / "context" / "exemplars.toml"
    global_exemplar_path = Path.home() / ".claude" / "context" / "exemplars.toml"
    existing_project_needs, existing_project_no = load_existing_exemplars(project_exemplar_path)
    existing_global_needs, existing_global_no = load_existing_exemplars(global_exemplar_path)

    # Validate and rank needs_context against bootstrap needs
    best_needs = validate_and_rank(
        model, candidate_needs, existing_project_needs + existing_global_needs,
        bootstrap_needs_embeds, MAX_EXEMPLARS_PER_CATEGORY,
    )

    # Validate and rank no_context against bootstrap no
    best_no = validate_and_rank(
        model, candidate_no, existing_project_no + existing_global_no,
        bootstrap_no_embeds, MAX_EXEMPLARS_PER_CATEGORY,
    )

    # Split into global vs project
    global_needs, project_needs = split_global_project(model, best_needs, bootstrap_needs_embeds)
    global_no, project_no = split_global_project(model, best_no, bootstrap_no_embeds)

    # Write project exemplars
    if project_needs or project_no:
        write_exemplars(project_exemplar_path, project_needs, project_no)
        print(
            f"Project exemplars: {len(project_needs)} needs, {len(project_no)} no -> {project_exemplar_path}",
            file=sys.stderr,
        )

    # Write global exemplars (merge, don't replace)
    if global_needs or global_no:
        # Merge with existing global
        merged_global_needs = list(set(existing_global_needs + global_needs))[:MAX_EXEMPLARS_PER_CATEGORY]
        merged_global_no = list(set(existing_global_no + global_no))[:MAX_EXEMPLARS_PER_CATEGORY]
        write_exemplars(global_exemplar_path, merged_global_needs, merged_global_no)
        print(
            f"Global exemplars: {len(merged_global_needs)} needs, {len(merged_global_no)} no -> {global_exemplar_path}",
            file=sys.stderr,
        )

    print("Exemplars updated. Embeddings will regenerate on next session.", file=sys.stderr)


if __name__ == "__main__":
    main()
