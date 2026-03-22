#!/usr/bin/env bash
# UserPromptSubmit hook: classify the prompt and optionally inject context.
# Uses the semantic classifier daemon for fast yes/no decisions.
# If ctx (claude-context-cli) is installed, includes code context too.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Extract the user's prompt
PROMPT=$(_json_val "$INPUT" "prompt" "")

# Skip very short prompts
if [ -z "$PROMPT" ] || [ ${#PROMPT} -lt 10 ]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# Skip system/XML messages (task notifications, hook output, etc.)
if [[ "$PROMPT" == "<"* ]] || [[ "$PROMPT" == "{"* ]]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# Need memsearch available
if [ -z "$MEMSEARCH_CMD" ]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# Check if classifier daemon is running
CLASSIFIER_SOCKET="/tmp/memsearch-classify.sock"
NEEDS_CONTEXT=false

CATEGORY="no_context_generic"
INJECT=false

if [ -S "$CLASSIFIER_SOCKET" ]; then
  REQUEST=$(python3 -c "import json,sys; print(json.dumps({'prompt': sys.argv[1], 'project': sys.argv[2]}))" "$PROMPT" "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || echo '{}')
  RESULT=$(printf '%s' "$REQUEST" | nc -U "$CLASSIFIER_SOCKET" -w 2 2>/dev/null || echo '{}')
  CATEGORY=$(_json_val "$RESULT" "category" "no_context_generic")
  INJECT=$(_json_val "$RESULT" "inject" "false")
fi

if [ "$INJECT" != "true" ]; then
  echo '{"systemMessage": "[memsearch] Memory available"}'
  exit 0
fi

# --- Context injection based on category ---

CONTEXT=""

# Both needs_context categories get memsearch memories
MEMORY_RESULTS=$($MEMSEARCH_CMD search "$PROMPT" --top-k 3 ${COLLECTION_NAME:+--collection "$COLLECTION_NAME"} 2>/dev/null || true)
if [ -n "$MEMORY_RESULTS" ] && [ "$MEMORY_RESULTS" != "No results found." ]; then
  CONTEXT+="## Relevant memories\n${MEMORY_RESULTS}\n\n"
fi

# needs_context_project also gets code context (if ctx is installed)
if [ "$CATEGORY" = "needs_context_project" ] && command -v ctx &>/dev/null; then
  CODE_RESULTS=$(ctx search "$PROMPT" -n 3 "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true)
  if [ -n "$CODE_RESULTS" ]; then
    CONTEXT+="## Relevant code\n${CODE_RESULTS}\n\n"
  fi
fi

# Add guidance on how to go deeper
if [ -n "$CONTEXT" ]; then
  CONTEXT+="## How to go deeper\n"
  CONTEXT+="- For more memories: use /memory-recall with specific queries\n"
  if [ "$CATEGORY" = "needs_context_project" ]; then
    CONTEXT+="- For code details: use search_code to find implementations\n"
  fi
  CONTEXT+="- For exact past conversations: use memsearch expand <hash>\n"
fi

if [ -n "$CONTEXT" ]; then
  json_context=$(_json_encode_str "$CONTEXT")
  echo "{\"systemMessage\": \"[memsearch] Context injected (${CATEGORY})\", \"hookSpecificOutput\": {\"hookEventName\": \"UserPromptSubmit\", \"additionalContext\": $json_context}}"
else
  echo '{"systemMessage": "[memsearch] Memory available"}'
fi
