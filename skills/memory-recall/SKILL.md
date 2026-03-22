---
name: memory-recall
description: Search and recall relevant memories with multi-query retrieval, noise filtering, and reranking. Replaces the default memsearch memory-recall skill.
user-invocable: true
---

# Memory Recall (Enhanced)

Search past memories for relevant context using a multi-stage retrieval pipeline.
If nothing relevant is found, say so. Do not fabricate memories.

## Pipeline

### Step 1: Intent Classification

Before searching, classify what kind of memory the user needs:

| Intent | Strategy |
|---|---|
| Past decision | Search for "decided", "chose", "because", "going forward" |
| User preference | Search for "prefers", "corrected", "don't", "always" |
| What happened | Search by topic keywords + date references |
| How something works | Search for architecture, implementation terms |
| Bug/error history | Search for "error", "fix", "root cause", "blocker" |

### Step 2: Multi-Query Expansion

Generate 3 search queries that capture different aspects of the question:
1. The original question rephrased as a factual statement
2. Key technical terms extracted as a keyword query
3. A hypothetical memory entry that would answer the question (HyDE)

### Step 3: Broad Retrieval

For each query, run:
```bash
memsearch search "<query>" --top-k 20 --json-output```

Merge all results. Deduplicate by `chunk_hash`, keeping the highest score.

### Step 4: Noise Filtering

Remove results that are primarily:
- Routine status updates ("started working on...", "completed task...")
- Tool output or command logs
- Session boundaries or greetings
- Generic acknowledgments

Keep results that contain:
- Decision language ("decided to", "chose X because")
- Correction language ("user corrected", "changed approach")
- Preference signals ("prefers", "always does", "don't use")
- Outcome descriptions ("the result was", "this fixed")

### Step 5: Rerank

Score each remaining result 1-5 for relevance to the original question:
- 5: Directly answers the question
- 4: Provides important context
- 3: Somewhat relevant
- 2: Tangentially related
- 1: Not useful

Keep only results scored 4 or 5.

### Step 6: Expand

For the top 3 results, run:
```bash
memsearch expand <chunk_hash>```

This gives the full markdown section with surrounding context.

### Step 7: Deep Drill (optional)

If the expanded result contains transcript anchors and you need exact wording:
```bash
memsearch transcript <jsonl_path> --turn <uuid> --context 3
```

### Step 8: Synthesize

Return a curated summary organized by relevance. For each memory include:
- The key information (decisions, corrections, preferences, outcomes)
- Source reference (file name, date) for traceability

Be concise. Only include information genuinely useful for the user's question.
If nothing relevant is found, say "No relevant memories found."
