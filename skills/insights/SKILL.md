---
name: insights
description: Mine memsearch history for recurring patterns and promote them to auto-memory. Run periodically to distill durable knowledge from session logs.
user-invocable: true
---

# Insights

Mine memsearch history for recurring patterns and promote valuable ones
into auto-memory. If no patterns are found, say so. Do not invent patterns.

## What to look for

Search memsearch for patterns that repeat across sessions:

- **Corrections** that appear 3+ times become feedback memories
- **Preferences** stated or demonstrated repeatedly become user memories
- **Ongoing goals and constraints** become project memories
- **External system pointers** become reference memories

## How memsearch search works

Memsearch has three layers of progressively deeper recall:

### L1: Search
Run `memsearch search "<query>" --top-k 10 --json-output --collection <collection>`
to find relevant memory chunks. Start with broad queries:
- "CORRECTION:" or "user corrected" or "user wanted"
- "PREFERENCE:" or "user prefers" or "always"
- "DECISION:" or "going forward" or "agreed"
- "BLOCKER:" or "do not" or "doesn't work"

### L2: Expand
For promising results, run `memsearch expand <chunk_hash> --collection <collection>`
to see the full markdown section with context.

### L3: Transcript drill-down
If you need exact wording:
- `memsearch transcript <jsonl_path>` to list all turns
- `memsearch transcript <jsonl_path> --turn <uuid> --context 3` for a specific turn

## Process

1. **Search** (L1): Run broad queries across the project collection to find
   candidate patterns. Look for themes that appear in multiple results.

2. **Expand** (L2): For each candidate, expand the chunk to see full context.
   Verify the pattern is real, not a one-off.

3. **Drill down** (L3, optional): If the pattern involves a specific correction
   or decision, drill into the transcript for exact wording and reasoning.

4. **Check existing memory**: Read the current auto-memory files to know what's
   already captured. Don't duplicate.

5. **Promote**: For each new pattern worth keeping, save it using the auto-memory
   system following the instructions in your system prompt. Use the appropriate type:
   - `feedback` for corrections, preferences, and style guidance
   - `project` for ongoing initiatives, goals, and constraints
   - `user` for role, expertise, and working style
   - `reference` for pointers to external systems and resources

6. **Prune**: Check existing memories against current memsearch data. If a memory
   claims something that contradicts recent sessions, update or remove it.

## Judgment calls

- Only promote patterns that appear **3+ times** or that the user explicitly
  emphasized. One-off corrections are noise.
- Prefer **why** over **what**. "Don't use em dashes" is less useful than
  "Don't use em dashes. Why: user finds them visually noisy."
- If a pattern is already in CLAUDE.md or derivable from code, skip it.
- If unsure, don't save it. Under-saving is better than cluttering memory.

## Output

Report what you found and what you promoted. Be brief.
