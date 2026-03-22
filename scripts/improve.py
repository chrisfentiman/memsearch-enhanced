#!/usr/bin/env python3
# /// script
# dependencies = ["fastembed>=0.3.0", "numpy>=1.24.0"]
# ///
"""
Self-improvement for the four-category semantic classifier.

1. Analyzes session transcripts to infer ground truth from tool usage
2. Validates candidates against the bootstrap classifier (quality gate)
3. Merges with existing exemplars, scores all against bootstrap
4. Keeps only top-N highest quality exemplars per category
5. Splits into global vs project based on similarity to bootstrap

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
MAX_EXEMPLARS_PER_CATEGORY = 20

CATEGORIES = [
    "needs_context_project",
    "needs_context_generic",
    "no_context_project",
    "no_context_generic",
]

# Built-in tools that indicate project-specific context was needed
PROJECT_TOOLS = {"Read", "Edit", "Write", "Glob", "Grep"}

# Built-in tools that indicate research/memory context was needed
RESEARCH_TOOLS = {"Agent", "Skill"}

# Routine tools (no context needed when used alone)
ROUTINE_TOOLS = {"Bash"}

# Any tool starting with mcp__ is an MCP tool (context was needed)
MCP_PREFIX = "mcp__"


# --- Prompt cleaning ---


def _clean_prompt(text: str) -> str | None:
    """Clean a prompt for use as an exemplar. Returns None if unsuitable."""
    text = text.strip()

    if len(text) < 15 or len(text) > 150:
        return None

    if any(marker in text for marker in [
        "<!--", "```", "###", "===", "<system-reminder>",
        "<channel", "<command", "<task-notification", "<local-command",
    ]):
        return None

    if "/Users/" in text or "/.claude/" in text or "/opt/" in text:
        return None

    if text.isupper():
        return None

    # Take first sentence only
    for sep in [". ", "? ", "! ", "\n"]:
        if sep in text:
            text = text[:text.index(sep) + 1]
            break

    return text.strip()[:120]


# --- Bootstrap loading ---


def load_bootstrap() -> dict[str, list[str]]:
    """Load the plugin's bootstrap exemplars (never modified)."""
    bootstrap_path = SCRIPT_DIR / "exemplars.toml"
    if not bootstrap_path.exists():
        return {cat: [] for cat in CATEGORIES}
    with open(bootstrap_path, "rb") as f:
        data = tomllib.load(f)
    return {cat: data.get(cat, {}).get("examples", []) for cat in CATEGORIES}


def load_existing_exemplars(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {cat: [] for cat in CATEGORIES}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return {cat: data.get(cat, {}).get("examples", []) for cat in CATEGORIES}
    except Exception:
        return {cat: [] for cat in CATEGORIES}


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


def extract_turns(messages: list[dict]) -> list[dict]:
    turns = []
    current_prompt = None
    current_tools: set[str] = set()

    for msg in messages:
        mtype = msg.get("type", "")
        content = msg.get("message", {}).get("content", "")

        if mtype == "user" and isinstance(content, str) and len(content.strip()) > 10:
            if current_prompt is not None:
                turns.append({"prompt": current_prompt, "tools": current_tools})
            current_prompt = _clean_prompt(content)
            current_tools = set()

        if mtype == "assistant" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    current_tools.add(block.get("name", ""))

    if current_prompt is not None:
        turns.append({"prompt": current_prompt, "tools": current_tools})

    return turns


def infer_category(tools: set[str]) -> str | None:
    """Infer the ground truth category from tool usage.

    The key insight: using project tools (Read/Edit/Write/Glob/Grep) alone
    does NOT mean context was needed. It just means the user gave a direct
    instruction that involved files. Context is only "needed" when external
    sources (MCP, Agent, Skill) were also used alongside project tools.
    """
    used_project = tools & PROJECT_TOOLS
    used_research = tools & RESEARCH_TOOLS
    used_routine = tools & ROUTINE_TOOLS
    used_mcp = {t for t in tools if t.startswith(MCP_PREFIX)}
    used_context = used_mcp or used_research

    # Context tools + project tools = needed project-specific context
    if used_context and used_project:
        return "needs_context_project"
    # Context tools alone = needed memory/research but not project files
    if used_context:
        return "needs_context_generic"
    # Project tools alone = direct file work, no context needed
    if used_project or used_routine:
        return "no_context_project"
    # No tools at all = no context, generic
    if not tools:
        return "no_context_generic"
    return None


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


# --- Quality scoring and ranking ---


def score_and_rank(
    model: TextEmbedding,
    candidates: list[str],
    existing: list[str],
    bootstrap_embeds: np.ndarray,
    max_n: int,
    quality_threshold: float = 0.35,
) -> list[str]:
    """Score all against bootstrap, keep top N above threshold."""
    all_exemplars = list(set(candidates + existing))
    if not all_exemplars or bootstrap_embeds.shape[0] == 0:
        return all_exemplars[:max_n]

    embeds = np.array(list(model.embed(all_exemplars)))
    scores = np.max(embeds @ bootstrap_embeds.T, axis=1)

    qualified = [(ex, float(s)) for ex, s in zip(all_exemplars, scores) if s >= quality_threshold]
    qualified.sort(key=lambda x: x[1], reverse=True)
    return [ex for ex, _ in qualified[:max_n]]


def split_global_project(
    model: TextEmbedding,
    exemplars: list[str],
    bootstrap_embeds: np.ndarray,
    threshold: float = 0.65,
) -> tuple[list[str], list[str]]:
    """Split into global (similar to bootstrap) vs project (dissimilar)."""
    if not exemplars or bootstrap_embeds.shape[0] == 0:
        return [], exemplars

    embeds = np.array(list(model.embed(exemplars)))
    scores = np.max(embeds @ bootstrap_embeds.T, axis=1)

    global_ex = [ex for ex, s in zip(exemplars, scores) if s >= threshold]
    project_ex = [ex for ex, s in zip(exemplars, scores) if s < threshold]
    return global_ex, project_ex


# --- Write exemplars ---


def escape_toml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def write_exemplars(path: Path, cats: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for cat in CATEGORIES:
            examples = cats.get(cat, [])
            f.write(f"[{cat}]\nexamples = [\n")
            for ex in sorted(examples):
                f.write(f'    "{escape_toml(ex)}",\n')
            f.write("]\n\n")


def merge_cat_dicts(a: dict[str, list[str]], b: dict[str, list[str]]) -> dict[str, list[str]]:
    result = {}
    for cat in CATEGORIES:
        result[cat] = list(set(a.get(cat, []) + b.get(cat, [])))
    return result


# --- Main ---


def main() -> None:
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))

    if "--auto" in sys.argv:
        transcript_dir = Path.home() / ".claude" / "projects"
        project_key = str(project_dir.resolve()).replace("/", "-").lstrip("-")
        project_transcript_dir = transcript_dir / project_key
        if not project_transcript_dir.exists():
            print(f"No transcripts at {project_transcript_dir}", file=sys.stderr)
            sys.exit(1)
        transcripts = sorted(project_transcript_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not transcripts:
            print("No transcripts found", file=sys.stderr)
            sys.exit(1)
        transcript_path = transcripts[-1]
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        transcript_path = Path(sys.argv[1])
    else:
        print("Usage: improve.py [--auto | <transcript.jsonl>]", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing: {transcript_path}", file=sys.stderr)

    messages = parse_transcript(transcript_path)
    turns = extract_turns(messages)
    decisions = load_decisions(project_dir)
    bootstrap = load_bootstrap()

    print(f"Found {len(turns)} turns, {len(decisions)} classifier decisions", file=sys.stderr)

    # Collect candidates per category
    candidates: dict[str, list[str]] = {cat: [] for cat in CATEGORIES}

    for turn in turns:
        prompt = turn["prompt"]
        if prompt is None:
            continue
        tools = turn["tools"]
        inferred = infer_category(tools)
        if inferred is None:
            continue

        decision = decisions.get(prompt)

        if decision is None:
            # Bootstrap: learn from tool usage
            candidates[inferred].append(prompt)
        else:
            classified = decision.get("category", "no_context_generic")
            if classified != inferred:
                # Misclassification: use ground truth
                candidates[inferred].append(prompt)
                print(
                    f"  MISCLASS: '{prompt[:60]}' classified={classified} actual={inferred}",
                    file=sys.stderr,
                )

    total_candidates = sum(len(v) for v in candidates.values())
    if total_candidates == 0:
        print("No new candidates found.", file=sys.stderr)
        return

    for cat in CATEGORIES:
        if candidates[cat]:
            print(f"  {cat}: {len(candidates[cat])} candidates", file=sys.stderr)

    # Load model and bootstrap embeddings
    model = TextEmbedding("BAAI/bge-small-en-v1.5", threads=2)
    bootstrap_embeds = {
        cat: np.array(list(model.embed(examples))) if examples else np.empty((0, 384))
        for cat, examples in bootstrap.items()
    }

    # Load existing
    project_path = project_dir / ".claude" / "context" / "exemplars.toml"
    global_path = Path.home() / ".claude" / "context" / "exemplars.toml"
    existing_project = load_existing_exemplars(project_path)
    existing_global = load_existing_exemplars(global_path)

    # Re-score EVERYTHING: new candidates + existing exemplars from both files.
    # Only the top N survive. Old weak exemplars get pruned, strong new ones replace them.
    best_for_global: dict[str, list[str]] = {cat: [] for cat in CATEGORIES}
    best_for_project: dict[str, list[str]] = {cat: [] for cat in CATEGORIES}

    for cat in CATEGORIES:
        cat_bootstrap = bootstrap_embeds.get(cat, np.empty((0, 384)))

        # Pool: new candidates + existing project + existing global (all re-compete)
        all_pool = list(set(
            candidates[cat]
            + existing_project.get(cat, [])
            + existing_global.get(cat, [])
        ))

        # Score all against bootstrap, keep only top N * 2 (we'll split after)
        ranked = score_and_rank(model, all_pool, [], cat_bootstrap, MAX_EXEMPLARS_PER_CATEGORY * 2)

        # Split: similar to bootstrap = global, dissimilar = project
        global_ex, project_ex = split_global_project(model, ranked, cat_bootstrap)

        best_for_global[cat] = global_ex[:MAX_EXEMPLARS_PER_CATEGORY]
        best_for_project[cat] = project_ex[:MAX_EXEMPLARS_PER_CATEGORY]

    # Write PROJECT exemplars
    has_project = any(best_for_project[cat] for cat in CATEGORIES)
    if has_project:
        write_exemplars(project_path, best_for_project)
        counts = ", ".join(f"{cat}={len(best_for_project[cat])}" for cat in CATEGORIES if best_for_project[cat])
        print(f"Project exemplars: {counts} -> {project_path}", file=sys.stderr)

    # Write GLOBAL exemplars
    has_global = any(best_for_global[cat] for cat in CATEGORIES)
    if has_global:
        write_exemplars(global_path, best_for_global)
        counts = ", ".join(f"{cat}={len(best_for_global[cat])}" for cat in CATEGORIES if best_for_global[cat])
        print(f"Global exemplars: {counts} -> {global_path}", file=sys.stderr)

    print("Exemplars updated. Embeddings regenerate on next session.", file=sys.stderr)


if __name__ == "__main__":
    main()
