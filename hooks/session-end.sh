#!/usr/bin/env bash
# SessionEnd hook: stop the memsearch watch singleton and classifier daemon.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

stop_watch
kill_orphaned_index

# Run self-improvement: analyze this session's transcript for misclassifications
IMPROVE_SCRIPT="$SCRIPT_DIR/../scripts/improve.py"
if [ -f "$IMPROVE_SCRIPT" ] && command -v uv &>/dev/null; then
  TRANSCRIPT_PATH=$(_json_val "$INPUT" "transcript_path" "")
  if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
    CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}" uv run "$IMPROVE_SCRIPT" "$TRANSCRIPT_PATH" 2>/dev/null || true
  fi
fi

# NOTE: Classifier daemon is shared across sessions. Do NOT kill it here.
# It will idle-timeout after 30 minutes of no requests.

exit 0
