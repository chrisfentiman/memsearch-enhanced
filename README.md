# memsearch-enhanced

[![License](https://img.shields.io/github/license/chrisfentiman/memsearch-enhanced?style=flat-square)](LICENSE)

> Enhanced memory hooks and skills for [memsearch](https://github.com/zilliztech/memsearch). Better summarization, smarter context injection, self-improving classification.

## Problem

The default memsearch plugin produces action logs ("Claude edited file X, ran command Y"). This is noise for future recall. What's valuable is **durable knowledge**: corrections, preferences, decisions, blockers.

It also has no intelligence about WHEN to inject context. Every prompt gets the same "[memsearch] Memory available" hint regardless of whether context would actually help.

## What's Different

### Smart Context Injection (Semantic Router)

A shared ONNX-based classifier daemon decides whether each prompt needs context injection. Four categories:

| Category | What it means | What happens |
|---|---|---|
| `needs_context_project` | Prompt needs code/architecture context | Inject memsearch memories + code search results |
| `needs_context_generic` | Prompt needs session history/memory | Inject memsearch memories only |
| `no_context_project` | Routine project work | Skip injection |
| `no_context_generic` | General knowledge / casual | Skip injection |

The classifier uses `bge-small-en-v1.5` embeddings with cosine similarity against exemplar sets. One daemon serves all Claude Code sessions (shared ONNX model, ~10-25ms per classification).

### Self-Improving Exemplars

The classifier gets better over time:

1. **Bootstrap** (`scripts/exemplars.toml`) ships with the plugin as a quality floor
2. **Global** (`~/.claude/context/exemplars.toml`) learns generic patterns across all projects
3. **Project** (`<project>/.claude/context/exemplars.toml`) learns project-specific patterns

On SessionEnd, the self-improvement loop:
- Analyzes the session transcript to infer ground truth from tool usage
- Compares against classifier decisions to find misclassifications
- Validates new candidates against the bootstrap quality gate
- Re-scores ALL exemplars (new + existing), keeps only the top N per category
- Splits into global vs project based on similarity to bootstrap

Project exemplars get 15% weight boost over global at classification time.

### Stop Hook: Observations over Actions

The default prompt asks "what happened." Ours asks "what was learned."

| Default memsearch | memsearch-enhanced |
|---|---|
| "Claude Code read file X and modified Y" | "CORRECTION: AI assumed REST, user wanted GraphQL" |
| "User asked about auth module" | "DECISION: Going forward, use OAuth2 not JWT" |
| "Claude ran npm test" | "PREFERENCE: User prefers running single tests, not full suite" |

Categories: `CORRECTION`, `PREFERENCE`, `DECISION`, `BLOCKER`, `FINDING`, `CONTEXT`

Prompt uses XML-structured instructions with 6 few-shot examples matching actual transcript format. Preamble stripping ensures clean output even when Haiku adds commentary.

### Memory Recall: Multi-Query + Reranking

The default skill sends one query and returns top 5. Ours uses a retrieval pipeline:

1. **Intent classification** - what kind of memory is needed
2. **Multi-query expansion** - 3 search variants including HyDE
3. **Broad retrieval** - top-k 20 per query
4. **Noise filtering** - skip routine status, tool output, session boundaries
5. **LLM reranking** - score candidates 1-5 for relevance
6. **Expand top 3** - full context only for the best matches

### Insights Skill: Pattern Promotion

Mine memsearch for recurring corrections and preferences, promote them to Claude's auto-memory system. Also prunes stale memories.

### SubagentStop Support

Captures subagent work (research agents, code reviewers, test runners) that the default plugin ignores entirely.

## Prerequisites

- [memsearch](https://github.com/zilliztech/memsearch) installed globally (`uv tool install memsearch[onnx]`)
- [uv](https://docs.astral.sh/uv/) for running the classifier daemon
- The default memsearch plugin should be **uninstalled** to avoid double-summarization
- Optional: [claude-context-cli](https://github.com/chrisfentiman/claude-context-cli) (`ctx`) for code search injection

## Install

### Claude Code plugin

```bash
/plugin marketplace add chrisfentiman/claudesplace
/plugin install memsearch-enhanced
```

### Test locally

```bash
claude --plugin-dir /path/to/memsearch-enhanced
```

## Architecture

```
SessionStart
  |-> Start memsearch watch (index memory files)
  |-> Start classifier daemon (shared ONNX model, Unix socket)
  |-> Inject recent memories as cold-start context

UserPromptSubmit (every turn)
  |-> Send prompt to classifier daemon (~15ms)
  |-> needs_context_project? -> memsearch search + ctx search -> inject
  |-> needs_context_generic? -> memsearch search -> inject
  |-> no_context_*? -> "[memsearch] Memory available" hint

Stop / SubagentStop (after each response)
  |-> Parse transcript, extract last turn
  |-> Haiku summarizes with categorized extraction prompt
  |-> Strip preamble, validate output
  |-> Append to .memsearch/memory/YYYY-MM-DD.md

SessionEnd
  |-> Run self-improvement loop (analyze transcript, update exemplars)
  |-> Stop memsearch watch
  |-> Classifier daemon stays running (idle timeout: 30 min)
```

## Configuration

### Exemplar files

| Location | Purpose | Updated by |
|---|---|---|
| `scripts/exemplars.toml` | Bootstrap quality gate | Plugin releases |
| `~/.claude/context/exemplars.toml` | Global patterns | Self-improvement loop |
| `<project>/.claude/context/exemplars.toml` | Project patterns | Self-improvement loop |

### Stop hook prompt

Edit `prompts/stop.txt` to change what the Stop hook extracts. Read from disk on each invocation, changes take effect immediately.

### Classifier

The daemon uses `BAAI/bge-small-en-v1.5` (384-dim ONNX embeddings). Shared across all Claude Code sessions via Unix socket at `/tmp/memsearch-classify.sock`. 30-minute idle timeout auto-exits.

## Structure

```
memsearch-enhanced/
|-- .claude-plugin/
|   |-- plugin.json
|-- hooks/
|   |-- hooks.json              # SessionStart, UserPromptSubmit, Stop,
|   |                           # SubagentStop, SessionEnd
|   |-- common.sh
|   |-- session-start.sh        # Watch + classifier daemon startup
|   |-- user-prompt-submit.sh   # Smart context injection
|   |-- stop.sh                 # Durable knowledge extraction
|   |-- session-end.sh          # Self-improvement + cleanup
|   |-- parse-transcript.sh     # Transcript parser
|-- prompts/
|   |-- stop.txt                # Customizable extraction prompt
|-- scripts/
|   |-- classifier.py           # Shared ONNX classifier daemon
|   |-- improve.py              # Self-improvement loop
|   |-- exemplars.toml          # Bootstrap exemplar set
|-- skills/
|   |-- memory-recall/
|   |   |-- SKILL.md            # Multi-query retrieval pipeline
|   |-- insights/
|       |-- SKILL.md            # Pattern promotion to auto-memory
|-- README.md
|-- LICENSE
```

## License

[MIT](LICENSE)
