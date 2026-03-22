#!/usr/bin/env python3
# /// script
# dependencies = ["fastembed>=0.3.0", "numpy>=1.24.0"]
# ///
"""
Self-improvement for the semantic classifier.

Analyzes session transcripts to find misclassifications:
- Prompts classified as "no context needed" but followed by file lookups,
  memsearch searches, or agent spawns (should have been "needs context")
- Prompts classified as "needs context" but followed only by simple responses
  (probably didn't need context)

Updates the project-level exemplars.toml with corrections.

Usage:
  uv run improve.py <transcript.jsonl>
  uv run improve.py --auto   # find latest transcript automatically
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

try:
    import tomli_w
except ImportError:
    tomli_w = None

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

# Tools that indicate routine/simple work (no context needed)
SIMPLE_TOOLS = {
    "Bash",  # could go either way, but alone it's usually simple
}


def parse_transcript(path: Path) -> list[dict]:
    """Parse a JSONL transcript into a list of messages."""
    messages = []
    with open(path) as f:
        for line in f:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def extract_turns(messages: list[dict]) -> list[dict]:
    """Extract user prompts and the tools used in the following response."""
    turns = []
    current_prompt = None
    current_tools: set[str] = set()

    for msg in messages:
        mtype = msg.get("type", "")
        content = msg.get("message", {}).get("content", "")

        # User message with string content = a prompt
        if mtype == "user" and isinstance(content, str) and len(content.strip()) > 10:
            # Save previous turn if exists
            if current_prompt is not None:
                turns.append({"prompt": current_prompt, "tools": current_tools})
            current_prompt = content.strip()
            current_tools = set()

        # Assistant tool_use = tools used in response
        if mtype == "assistant" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    current_tools.add(tool_name)

    # Don't forget the last turn
    if current_prompt is not None:
        turns.append({"prompt": current_prompt, "tools": current_tools})

    return turns


def analyze_turns(turns: list[dict], decisions: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Compare classifier decisions against actual tool usage.

    Returns:
        new_needs_context: prompts that should be added to needs_context
        new_no_context: prompts that should be added to no_context
    """
    new_needs = []
    new_no = []

    for turn in turns:
        prompt = turn["prompt"][:200]
        tools = turn["tools"]

        # Check if we have a classifier decision for this prompt
        decision = decisions.get(prompt)

        # Determine ground truth: did this prompt actually need context?
        used_context_tools = tools & CONTEXT_TOOLS
        actually_needed = len(used_context_tools) > 0

        if decision is None:
            # No classifier decision (daemon wasn't running for this prompt)
            # Skip - we can't evaluate without a baseline decision
            continue

        classified_as_needing = decision.get("needs_context", False)

        # Misclassification: said "no" but actually needed context
        if not classified_as_needing and actually_needed:
            new_needs.append(prompt)
            print(
                f"  FALSE NEGATIVE: '{prompt[:80]}...' "
                f"(tools: {', '.join(used_context_tools)})",
                file=sys.stderr,
            )

        # Misclassification: said "yes" but only simple tools used
        if classified_as_needing and not actually_needed and len(tools) > 0:
            # Only flag if we're confident it was a false positive
            # (has tools but none are context-related)
            if tools and not (tools - SIMPLE_TOOLS - {""}):
                new_no.append(prompt)
                print(
                    f"  FALSE POSITIVE: '{prompt[:80]}...' "
                    f"(tools: {', '.join(tools)})",
                    file=sys.stderr,
                )

    return new_needs, new_no


def load_decisions(project_dir: Path) -> dict[str, dict]:
    """Load classifier decisions from the log."""
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


def update_exemplars(project_dir: Path, new_needs: list[str], new_no: list[str]) -> None:
    """Update the project-level exemplars.toml with new examples."""
    exemplar_path = project_dir / ".claude" / "context" / "exemplars.toml"
    exemplar_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing or start fresh
    existing_needs: list[str] = []
    existing_no: list[str] = []

    if exemplar_path.exists():
        with open(exemplar_path, "rb") as f:
            data = tomllib.load(f)
        existing_needs = data.get("needs_context", {}).get("examples", [])
        existing_no = data.get("no_context", {}).get("examples", [])

    # Deduplicate and merge
    needs_set = set(existing_needs)
    no_set = set(existing_no)

    added_needs = 0
    added_no = 0

    for prompt in new_needs:
        short = prompt[:120]
        if short not in needs_set and short not in no_set:
            needs_set.add(short)
            added_needs += 1

    for prompt in new_no:
        short = prompt[:120]
        if short not in no_set and short not in needs_set:
            no_set.add(short)
            added_no += 1

    if added_needs == 0 and added_no == 0:
        print("No new exemplars to add.", file=sys.stderr)
        return

    # Write updated TOML
    def escape_toml(s: str) -> str:
        """Escape a string for TOML double-quoted value."""
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")

    if tomli_w is None:
        with open(exemplar_path, "w") as f:
            f.write("[needs_context]\nexamples = [\n")
            for ex in sorted(needs_set):
                f.write(f'    "{escape_toml(ex)}",\n')
            f.write("]\n\n[no_context]\nexamples = [\n")
            for ex in sorted(no_set):
                f.write(f'    "{escape_toml(ex)}",\n')
            f.write("]\n")
    else:
        data = {
            "needs_context": {"examples": sorted(needs_set)},
            "no_context": {"examples": sorted(no_set)},
        }
        with open(exemplar_path, "wb") as f:
            tomli_w.dump(data, f)

    print(
        f"Updated {exemplar_path}: +{added_needs} needs_context, +{added_no} no_context "
        f"(total: {len(needs_set)} / {len(no_set)})",
        file=sys.stderr,
    )

    # Embeddings will regenerate on next classifier daemon start
    # (load_exemplars detects missing/stale .npy files)
    print("Exemplars updated. Embeddings will regenerate on next session.", file=sys.stderr)


def main() -> None:
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))

    if "--auto" in sys.argv:
        # Find the latest transcript
        transcript_dir = Path.home() / ".claude" / "projects"
        # Derive project path key
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

    messages = parse_transcript(transcript_path)
    turns = extract_turns(messages)
    decisions = load_decisions(project_dir)

    print(f"Found {len(turns)} turns, {len(decisions)} classifier decisions", file=sys.stderr)

    new_needs, new_no = analyze_turns(turns, decisions)

    if new_needs or new_no:
        print(
            f"\nMisclassifications: {len(new_needs)} false negatives, {len(new_no)} false positives",
            file=sys.stderr,
        )
        update_exemplars(project_dir, new_needs, new_no)
    else:
        print("No misclassifications found. Classifier is performing well.", file=sys.stderr)


if __name__ == "__main__":
    main()
