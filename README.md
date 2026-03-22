# memsearch-enhanced

[![License](https://img.shields.io/github/license/chrisfentiman/memsearch-enhanced?style=flat-square)](LICENSE)

> Enhanced memory hooks and skills for [memsearch](https://github.com/zilliztech/memsearch). Better summarization, better recall, better insights.

## Problem

The default memsearch plugin produces action logs ("Claude edited file X, ran command Y"). This is noise for future recall. What's valuable is **durable knowledge**: corrections, preferences, decisions, blockers.

## What's Different

### Stop Hook — Observations over Actions

The default prompt asks "what happened." Ours asks "what was learned."

| Default memsearch | memsearch-enhanced |
|---|---|
| "Claude Code read file X and modified Y" | "CORRECTION: AI assumed REST, user wanted GraphQL" |
| "User asked about auth module" | "DECISION: Going forward, use OAuth2 not JWT" |
| "Claude ran npm test" | "PREFERENCE: User prefers running single tests, not full suite" |

Categories: `CORRECTION`, `PREFERENCE`, `DECISION`, `BLOCKER`, `CONTEXT`, `OUTCOME`

### Memory Recall — Multi-Query + Reranking

The default skill sends one query and returns top 5. Ours uses a retrieval pipeline:

1. **Intent classification** — what kind of memory is needed
2. **Multi-query expansion** — 3 search variants including HyDE
3. **Broad retrieval** — top-k 20 per query
4. **Noise filtering** — skip routine status, tool output, session boundaries
5. **LLM reranking** — score candidates 1-5 for relevance
6. **Expand top 3** — full context only for the best matches

### Insights Skill — Pattern Promotion

Periodically mine memsearch for recurring corrections and preferences, promote them to Claude's auto-memory system. Also prunes stale memories.

### SubagentStop Support

Captures subagent work (research agents, code reviewers, test runners) that the default plugin ignores entirely.

## Prerequisites

- [memsearch](https://github.com/zilliztech/memsearch) installed (`pip install memsearch[onnx]` or `uv tool install memsearch[onnx]`)
- The default memsearch plugin should be **disabled** to avoid double-summarization

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

## Structure

```
memsearch-enhanced/
├── .claude-plugin/
│   └── plugin.json           # Plugin metadata
├── hooks/
│   ├── hooks.json            # Stop, SubagentStop
│   ├── common.sh             # Shared utilities
│   └── stop.sh               # Durable knowledge extraction
├── prompts/
│   └── stop.txt              # Customizable summarization prompt
├── skills/
│   ├── memory-recall/
│   │   └── SKILL.md          # Multi-query retrieval pipeline
│   └── insights/
│       └── SKILL.md          # Pattern promotion to auto-memory
└── README.md
```

## Customization

Edit `prompts/stop.txt` to change what the Stop hook extracts. The prompt is
read from disk on each invocation, so changes take effect immediately.

## License

[MIT](LICENSE)
