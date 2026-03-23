#!/usr/bin/env bash
# UserPromptSubmit hook: classify the prompt and optionally inject context.
# Uses the semantic classifier daemon for fast yes/no decisions.
#
# Categories:
#   needs_memory -> inject memsearch memories only
#   needs_code   -> inject code search results only
#   needs_both   -> inject memories + code search
#   no_context   -> skip injection

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Extract the user's prompt
PROMPT=$(_json_val "$INPUT" "prompt" "")

# Skip very short prompts
if [ -z "$PROMPT" ] || [ ${#PROMPT} -lt 10 ]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# Skip non-user prompts: system/XML, slash commands, transcript summarization
if [[ "$PROMPT" == "<"* ]] || [[ "$PROMPT" == "{"* ]] || [[ "$PROMPT" == "/"* ]] || [[ "$PROMPT" == *"===BEGIN_TRANSCRIPT==="* ]]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# Need memsearch available
if [ -z "$MEMSEARCH_CMD" ]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# Check if classifier daemon is running (and version matches)
CLASSIFIER_SOCKET="/tmp/memsearch-classify.sock"
CLASSIFIER_SCRIPT="$SCRIPT_DIR/../scripts/classifier.py"

# Mid-session version check: if plugin was updated via /reload-plugins,
# the daemon may be running old exemplars. Compare version stamp to
# current plugin version and restart if mismatched.
if [ -S "$CLASSIFIER_SOCKET" ] && [ -f "$SCRIPT_DIR/../version.txt" ]; then
  _CURRENT_VER=$(cat "$SCRIPT_DIR/../version.txt" 2>/dev/null | tr -d '[:space:]')
  _DAEMON_VER=$(cat /tmp/memsearch-classify.version 2>/dev/null | tr -d '[:space:]')
  if [ -n "$_CURRENT_VER" ] && [ "$_CURRENT_VER" != "$_DAEMON_VER" ]; then
    pkill -f "classifier.py --daemon" 2>/dev/null || true
    sleep 1
    [ -S "$CLASSIFIER_SOCKET" ] && rm -f "$CLASSIFIER_SOCKET"
    if [ -f "$CLASSIFIER_SCRIPT" ] && command -v uv &>/dev/null; then
      nohup uv run "$CLASSIFIER_SCRIPT" --daemon </dev/null &>/dev/null &
      echo "$_CURRENT_VER" > /tmp/memsearch-classify.version
      sleep 3  # wait for daemon to initialize
    fi
  fi
fi

CATEGORY="no_context"
INJECT="false"

if [ -S "$CLASSIFIER_SOCKET" ]; then
  RESULT=$(python3 -c "
import socket, json, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(sys.argv[1])
s.sendall(json.dumps({'prompt': sys.argv[2], 'project': sys.argv[3]}).encode())
s.shutdown(socket.SHUT_WR)
print(s.recv(16384).decode())
s.close()
" "$CLASSIFIER_SOCKET" "$PROMPT" "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || echo '{}')
  CATEGORY=$(_json_val "$RESULT" "category" "no_context")
  INJECT=$(_json_val "$RESULT" "inject" "false")
fi

if [ "$INJECT" = "false" ]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# --- Context injection based on category ---

CONTEXT=""

# Memory injection (needs_memory or needs_both)
if [ "$INJECT" = "memory" ] || [ "$INJECT" = "both" ]; then
  MEMORY_RESULTS=$($MEMSEARCH_CMD search "$PROMPT" --top-k 3 ${COLLECTION_NAME:+--collection "$COLLECTION_NAME"} 2>/dev/null || true)
  if [ -n "$MEMORY_RESULTS" ] && [ "$MEMORY_RESULTS" != "No results found." ]; then
    CONTEXT+="## Relevant memories\n${MEMORY_RESULTS}\n\n"
  fi
fi

# Code injection (needs_code or needs_both)
if [ "$INJECT" = "code" ] || [ "$INJECT" = "both" ]; then
  if command -v ctx &>/dev/null; then
    CODE_RESULTS=$(ctx search "$PROMPT" -n 3 "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true)
    if [ -n "$CODE_RESULTS" ]; then
      CONTEXT+="## Relevant code\n${CODE_RESULTS}\n\n"
    fi
  fi
fi

if [ -n "$CONTEXT" ]; then
  json_context=$(_json_encode_str "$CONTEXT")
  echo "{\"systemMessage\": \"[memsearch] Context injected (${CATEGORY})\", \"hookSpecificOutput\": {\"hookEventName\": \"UserPromptSubmit\", \"additionalContext\": $json_context}}"
else
  echo '{"systemMessage": "[memsearch] Memory available"}'
fi
