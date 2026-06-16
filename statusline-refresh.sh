#!/bin/bash
# Background refresh for the status line. Runs the transcript usage aggregation
# and (optionally) an "rtk" token-savings parse under a single lock, writing the
# cache files consumed by statusline.sh.
# Args: <usage_cache> <rtk_cache> <lock_dir> <projects_dir>
USAGE_CACHE="$1"
RTK_CACHE="$2"
LOCK="$3"
PROJECTS="$4"

python3 "$HOME/.claude/statusline-usage.py" "$PROJECTS" "$USAGE_CACHE" 2>/dev/null

# Optional integration: if the "rtk" token-killer CLI is installed, cache its
# savings. Skipped entirely (no cache written) when rtk is absent, so the
# status line simply omits the rtk segment.
if command -v rtk >/dev/null 2>&1; then
  TODAY=$(date +%m-%d)
  GRAPH=$(rtk gain --graph 2>/dev/null)
  TOTAL=$(printf '%s\n' "$GRAPH" | awk '/^Tokens saved:/{print $3; exit}')
  PCT=$(printf '%s\n' "$GRAPH" | awk '/^Tokens saved:/{gsub(/[()]/,"",$4); print $4; exit}')
  TODAY_SAVED=$(printf '%s\n' "$GRAPH" | awk -v d="$TODAY" '$1==d{print $NF}')

  python3 -c "
import json, sys
data = {'total_saved': '${TOTAL:-0}', 'total_pct': '${PCT:-}', 'today_saved': '${TODAY_SAVED:-0}'}
print(json.dumps(data))
" > "$RTK_CACHE"
fi

rmdir "$LOCK" 2>/dev/null
