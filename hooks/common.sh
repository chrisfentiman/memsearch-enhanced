#!/usr/bin/env bash
# Shared setup for memsearch-enhanced hooks.
# Sourced by all hook scripts — not executed directly.

set -euo pipefail

INPUT="$(cat)"

# Ensure common user bin paths are in PATH
for p in "/opt/homebrew/bin" "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/bin" "/usr/local/bin"; do
  [[ -d "$p" ]] && [[ ":$PATH:" != *":$p:"* ]] && export PATH="$p:$PATH"
done

# Memory directory is project-scoped
MEMSEARCH_DIR="${CLAUDE_PROJECT_DIR:-.}/.memsearch"
MEMORY_DIR="$MEMSEARCH_DIR/memory"

# Find memsearch: prefer installed binary, never use uvx
MEMSEARCH_CMD=""
if command -v memsearch &>/dev/null; then
  MEMSEARCH_CMD="memsearch"
fi

# Plugin root for accessing prompt files
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Derive per-project collection name (same logic as memsearch)
COLLECTION_NAME=""
if [ -n "$MEMSEARCH_CMD" ]; then
  COLLECTION_NAME=$("$PLUGIN_ROOT/../memsearch-plugins/scripts/derive-collection.sh" "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true)
  # Fallback: derive from directory name
  if [ -z "$COLLECTION_NAME" ]; then
    COLLECTION_NAME="ms_$(basename "${CLAUDE_PROJECT_DIR:-.}" | tr '[:upper:]' '[:lower:]' | tr -c '[:alnum:]' '_')_$(echo -n "${CLAUDE_PROJECT_DIR:-.}" | shasum -a 256 | head -c 8)"
  fi
fi

# --- JSON helpers ---

_json_val() {
  local json="$1" key="$2" default="${3:-}"
  local result=""
  if command -v jq &>/dev/null; then
    result=$(printf '%s' "$json" | jq -r ".${key} // empty" 2>/dev/null) || true
  else
    result=$(python3 -c "
import json, sys
try:
    obj = json.loads(sys.argv[1])
    val = obj
    for k in sys.argv[2].split('.'):
        val = val[k]
    if val is None: print('')
    else: print(val)
except: print('')
" "$json" "$key" 2>/dev/null) || true
  fi
  if [ -z "$result" ]; then printf '%s' "$default"; else printf '%s' "$result"; fi
}

_json_encode_str() {
  local str="$1"
  if command -v jq &>/dev/null; then
    printf '%s' "$str" | jq -Rs . 2>/dev/null && return 0
  fi
  printf '%s' "$str" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" 2>/dev/null && return 0
  printf '"%s"' "$str"
}

ensure_memory_dir() {
  mkdir -p "$MEMORY_DIR"
}

run_memsearch() {
  if [ -n "$MEMSEARCH_CMD" ] && [ -n "$COLLECTION_NAME" ]; then
    $MEMSEARCH_CMD "$@" --collection "$COLLECTION_NAME" 2>/dev/null || true
  elif [ -n "$MEMSEARCH_CMD" ]; then
    $MEMSEARCH_CMD "$@" 2>/dev/null || true
  fi
}
