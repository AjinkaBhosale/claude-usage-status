#!/bin/bash
# Toggle the Claude Code status line on or off.
# When hidden, statusline-show.sh will still display the last cached render.
HIDDEN_FLAG="$HOME/.claude/.statusline-hidden"
if [ -f "$HIDDEN_FLAG" ]; then
  rm "$HIDDEN_FLAG"
  echo "Status line: ON"
else
  touch "$HIDDEN_FLAG"
  echo "Status line: OFF — run 'bash ~/.claude/statusline-show.sh' to peek at last state"
fi
