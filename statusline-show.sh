#!/bin/bash
# Force-display the last rendered status line regardless of the hidden flag.
# Useful when the status line is suppressed and you want a quick peek.
LAST_RENDER="$HOME/.claude/.statusline-last.ansi"
HIDDEN_FLAG="$HOME/.claude/.statusline-hidden"

if [ ! -f "$LAST_RENDER" ]; then
  echo "No cached status line yet — it will be saved after the next Claude Code response."
  exit 0
fi

[ -f "$HIDDEN_FLAG" ] && echo -e "\033[2m(status line is hidden — run statusline-toggle.sh to re-enable)\033[0m"
cat "$LAST_RENDER"

# Warn if the cache is older than 5 minutes
if [ -n "$(find "$LAST_RENDER" -mmin +5 2>/dev/null)" ]; then
  AGE_MINS=$(( ( $(date +%s) - $(date -r "$LAST_RENDER" +%s 2>/dev/null || echo 0) ) / 60 ))
  echo -e "\033[2m  (cached ${AGE_MINS}m ago — values reflect last Claude Code response)\033[0m"
fi
