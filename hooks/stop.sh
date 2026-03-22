#!/usr/bin/env bash
# Stop/SubagentStop hook: extract durable knowledge from the conversation turn.
# Uses a custom prompt focused on observations, corrections, and preferences.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Prevent infinite loop
STOP_HOOK_ACTIVE=$(_json_val "$INPUT" "stop_hook_active" "false")
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  echo '{}'
  exit 0
fi

# Detect subagent context
AGENT_ID=$(_json_val "$INPUT" "agent_id" "")
IS_SUBAGENT=false
if [ -n "$AGENT_ID" ]; then
  IS_SUBAGENT=true
fi

# Skip if memsearch not available
if [ -z "$MEMSEARCH_CMD" ]; then
  echo '{}'
  exit 0
fi

# Get transcript path
if [ "$IS_SUBAGENT" = true ]; then
  TRANSCRIPT_PATH=$(_json_val "$INPUT" "agent_transcript_path" "")
else
  TRANSCRIPT_PATH=$(_json_val "$INPUT" "transcript_path" "")
fi

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  echo '{}'
  exit 0
fi

# Skip empty transcripts
LINE_COUNT=$(wc -l < "$TRANSCRIPT_PATH" 2>/dev/null || echo "0")
if [ "$LINE_COUNT" -lt 3 ]; then
  echo '{}'
  exit 0
fi

ensure_memory_dir

# Parse transcript — use our bundled parser
PARSED=""
PARSER="$SCRIPT_DIR/parse-transcript.sh"
if [ -f "$PARSER" ]; then
  PARSED=$("$PARSER" "$TRANSCRIPT_PATH" 2>/dev/null || true)
fi

if [ -z "$PARSED" ] || [ "$PARSED" = "(empty transcript)" ] || [ "$PARSED" = "(no user message found)" ] || [ "$PARSED" = "(empty turn)" ]; then
  echo '{}'
  exit 0
fi

TODAY=$(date +%Y-%m-%d)
NOW=$(date +%H:%M)
MEMORY_FILE="$MEMORY_DIR/$TODAY.md"

# Extract session ID
if [ "$IS_SUBAGENT" = true ]; then
  SESSION_ID=$(_json_val "$INPUT" "session_id" "$(basename "$TRANSCRIPT_PATH" .jsonl)")
else
  SESSION_ID=$(basename "$TRANSCRIPT_PATH" .jsonl)
fi

LAST_USER_TURN_UUID=$(python3 -c "
import json, sys
uuid = ''
with open(sys.argv[1]) as f:
    for line in f:
        try:
            obj = json.loads(line)
            if obj.get('type') == 'user' and isinstance(obj.get('message', {}).get('content'), str):
                uuid = obj.get('uuid', '')
        except: pass
print(uuid)
" "$TRANSCRIPT_PATH" 2>/dev/null || true)

# Summarize using our custom prompt focused on durable knowledge
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT_FILE="$PLUGIN_ROOT/prompts/stop.txt"
SUMMARY=""
if command -v claude &>/dev/null && [ -f "$PROMPT_FILE" ]; then
  SYSTEM_PROMPT=$(cat "$PROMPT_FILE")
  # Wrap transcript with data markers so Haiku treats it as data, not conversation.
  # The instruction to extract is placed AFTER the data (Anthropic recommendation).
  USER_MSG="===BEGIN_TRANSCRIPT===
${PARSED}
===END_TRANSCRIPT===

Extract durable knowledge from the transcript above. Output ONLY bullet points starting with - and a category prefix. Nothing else."

  # Retry up to 3 times, stripping preamble before first category marker
  for _attempt in 1 2 3; do
    RAW_SUMMARY=$(printf '%s' "$USER_MSG" | MEMSEARCH_NO_WATCH=1 CLAUDECODE= claude -p \
      --model haiku \
      --no-session-persistence \
      --no-chrome \
      --system-prompt "$SYSTEM_PROMPT" \
      2>/dev/null || true)

    # Strip everything before the first category marker
    SUMMARY=$(echo "$RAW_SUMMARY" | sed -n '/^- \(CORRECTION\|PREFERENCE\|DECISION\|BLOCKER\|FINDING\|CONTEXT\):/,$p')

    if [ -n "$SUMMARY" ]; then
      break
    fi
  done
fi

if [ -z "$SUMMARY" ]; then
  SUMMARY="$PARSED"
fi

# Append to memory file
{
  if [ "$IS_SUBAGENT" = true ]; then
    echo "### $NOW (subagent: $AGENT_ID)"
  else
    echo "### $NOW"
  fi
  if [ -n "$SESSION_ID" ]; then
    echo "<!-- session:${SESSION_ID}${AGENT_ID:+ agent:${AGENT_ID}} turn:${LAST_USER_TURN_UUID} transcript:${TRANSCRIPT_PATH} -->"
  fi
  echo "$SUMMARY"
  echo ""
} >> "$MEMORY_FILE"

# Index immediately
run_memsearch index "$MEMORY_DIR"

echo '{}'
