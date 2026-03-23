#!/usr/bin/env python3
# /// script
# dependencies = ["fastembed>=0.3.0", "numpy>=1.24.0"]
# ///
"""
Self-improvement for the four-category semantic classifier.

1. Analyzes session transcripts
2. Uses classifier prediction + weighted tool heuristic to finalize category
3. Drops ambiguous cases where signals disagree
4. Validates candidates against bootstrap quality gate
5. Merges into global exemplars (no project-specific split)

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

CATEGORIES = [
    "needs_memory",
    "needs_code",
    "needs_both",
    "no_context",
]

# Tools that indicate code exploration was needed
CODE_TOOLS = {"Read", "Glob", "Grep", "Agent"}

# Tools that indicate memory/research context was needed
MEMORY_TOOLS = {"Skill"}

# Tools that indicate direct execution (no context needed)
ROUTINE_TOOLS = {"Bash", "Edit", "Write"}

# MCP tools that indicate memory was needed
MEMORY_MCP = {"mcp__memsearch", "mcp__perplexity"}

# Any tool starting with mcp__ is an MCP tool
MCP_PREFIX = "mcp__"

# Weight for tool heuristic vs classifier prediction
# 0.0 = trust classifier only, 1.0 = trust tools only
TOOL_WEIGHT = 0.4

# Minimum agreement score to accept a candidate (0-1)
# If classifier and tools disagree too much, drop the prompt
AGREEMENT_THRESHOLD = 0.6


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


def infer_category_from_tools(tools: set[str]) -> str:
    """Infer category from tool usage as a heuristic signal."""
    used_code = tools & CODE_TOOLS
    used_memory = tools & MEMORY_TOOLS
    used_mcp = {t for t in tools if t.startswith(MCP_PREFIX)}

    used_memory_mcp = {t for t in used_mcp if any(t.startswith(m) for m in MEMORY_MCP)}
    used_code_mcp = used_mcp - used_memory_mcp

    has_memory = bool(used_memory or used_memory_mcp)
    has_code = bool(used_code or used_code_mcp)

    if has_memory and has_code:
        return "needs_both"
    if has_memory:
        return "needs_memory"
    if has_code:
        return "needs_code"
    return "no_context"


def resolve_category(
    classifier_cat: str | None,
    tool_cat: str,
) -> str | None:
    """Resolve final category using classifier + weighted tool heuristic.

    Returns None if the signals disagree too much (drop the prompt).
    """
    if classifier_cat is None:
        # No classifier decision available, use tools alone
        return tool_cat

    if classifier_cat == tool_cat:
        # Full agreement
        return classifier_cat

    # Partial agreement: check if they're "close enough"
    # needs_memory and needs_code are far apart
    # needs_both is compatible with either needs_memory or needs_code
    # no_context is far from any needs_* category

    compatible_pairs = {
        ("needs_memory", "needs_both"),
        ("needs_both", "needs_memory"),
        ("needs_code", "needs_both"),
        ("needs_both", "needs_code"),
        ("needs_memory", "needs_code"),  # could be needs_both
    }

    if (classifier_cat, tool_cat) in compatible_pairs:
        # Close enough - prefer the tool signal since it's ground truth
        if tool_cat == "no_context":
            return classifier_cat  # tools say nothing, trust classifier
        return tool_cat

    # Strong disagreement (e.g. classifier says needs_memory, tools say no_context)
    # The tool signal is behavioral ground truth, but could be misleading
    # (e.g. a memory question answered from the model's own knowledge without tools)
    # Drop these ambiguous cases
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
    dropped = 0

    for turn in turns:
        prompt = turn["prompt"]
        if prompt is None:
            continue
        tools = turn["tools"]
        tool_cat = infer_category_from_tools(tools)

        # Get classifier's prediction if available
        decision = decisions.get(prompt[:200])  # decisions store truncated prompts
        classifier_cat = decision.get("category") if decision else None

        # Resolve using both signals
        final_cat = resolve_category(classifier_cat, tool_cat)

        if final_cat is None:
            dropped += 1
            print(
                f"  DROPPED: '{prompt[:60]}' classifier={classifier_cat} tools={tool_cat}",
                file=sys.stderr,
            )
            continue

        candidates[final_cat].append(prompt)

    total_candidates = sum(len(v) for v in candidates.values())
    if total_candidates == 0:
        print(f"No new candidates found ({dropped} dropped).", file=sys.stderr)
        return

    for cat in CATEGORIES:
        if candidates[cat]:
            print(f"  {cat}: {len(candidates[cat])} candidates", file=sys.stderr)
    if dropped:
        print(f"  dropped (ambiguous): {dropped}", file=sys.stderr)

    # Load model and bootstrap embeddings
    model = TextEmbedding("BAAI/bge-small-en-v1.5", threads=2)
    bootstrap_embeds = {
        cat: np.array(list(model.embed(examples))) if examples else np.empty((0, 384))
        for cat, examples in bootstrap.items()
    }

    # Load existing global exemplars
    global_path = Path.home() / ".claude" / "context" / "exemplars.toml"
    existing_global = load_existing_exemplars(global_path)

    # Re-score: new candidates + existing exemplars all re-compete
    best: dict[str, list[str]] = {cat: [] for cat in CATEGORIES}

    for cat in CATEGORIES:
        cat_bootstrap = bootstrap_embeds.get(cat, np.empty((0, 384)))

        all_pool = list(set(
            candidates[cat]
            + existing_global.get(cat, [])
        ))

        ranked = score_and_rank(model, all_pool, [], cat_bootstrap, MAX_EXEMPLARS_PER_CATEGORY)
        best[cat] = ranked

    # Write global exemplars
    has_exemplars = any(best[cat] for cat in CATEGORIES)
    if has_exemplars:
        write_exemplars(global_path, best)
        counts = ", ".join(f"{cat}={len(best[cat])}" for cat in CATEGORIES if best[cat])
        print(f"Global exemplars: {counts} -> {global_path}", file=sys.stderr)

    print("Exemplars updated. Embeddings regenerate on next session.", file=sys.stderr)


if __name__ == "__main__":
    main()
