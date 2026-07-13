#!/bin/bash
# Background refresh for the status line. Runs the transcript usage aggregation
# and (optionally) rtk token-savings parse under a single lock.
# Args: <usage_cache> <rtk_cache> <lock_dir> <projects_dir>
USAGE_CACHE="$1"
RTK_CACHE="$2"
LOCK="$3"
PROJECTS="$4"
RTK_DETAIL_CACHE="$HOME/.claude/.statusline-rtk-detail-cache.json"

python3 "$HOME/.claude/statusline-usage.py" "$PROJECTS" "$USAGE_CACHE" 2>/dev/null

if command -v rtk >/dev/null 2>&1; then
  TODAY=$(date +%m-%d)
  GRAPH=$(rtk gain --graph 2>/dev/null)
  TOTAL=$(printf '%s\n' "$GRAPH" | awk '/^Tokens saved:/{print $3; exit}')
  PCT=$(printf '%s\n' "$GRAPH" | awk '/^Tokens saved:/{gsub(/[()]/,"",$4); print $4; exit}')
  TODAY_SAVED=$(printf '%s\n' "$GRAPH" | awk -v d="$TODAY" '$1==d{print $NF}')

  python3 -c "
import json
data = {'total_saved': '${TOTAL:-0}', 'total_pct': '${PCT:-}', 'today_saved': '${TODAY_SAVED:-0}'}
print(json.dumps(data))
" > "$RTK_CACHE"

  # Per-command breakdown from rtk gain --history
  HISTORY=$(rtk gain --history 2>/dev/null)
  if [ -n "$HISTORY" ]; then
    HISTORY_TMP=$(mktemp)
    printf '%s\n' "$HISTORY" > "$HISTORY_TMP"
    python3 - "$RTK_DETAIL_CACHE" "$HISTORY_TMP" << 'PYEOF'
import sys, json, re
from collections import defaultdict

out_path = sys.argv[1]
history_path = sys.argv[2]
with open(history_path) as f:
    text = f.read()
by_cmd = defaultdict(lambda: {"calls": 0, "saved_tokens": 0, "saved_usd": 0.0})

pattern = re.compile(r'\b(\w+)\b.*?saved\s+([\d,]+)\s+tokens.*?\$([0-9.]+)', re.IGNORECASE)
for m in pattern.finditer(text):
    cmd  = m.group(1).lower()
    toks = int(m.group(2).replace(',', ''))
    usd  = float(m.group(3))
    by_cmd[cmd]["calls"]        += 1
    by_cmd[cmd]["saved_tokens"] += toks
    by_cmd[cmd]["saved_usd"]    += usd

total_usd = sum(v["saved_usd"] for v in by_cmd.values())
top_cmd   = max(by_cmd, key=lambda k: by_cmd[k]["saved_tokens"]) if by_cmd else ""

result = {
    "total_saved_usd": f"{total_usd:.2f}",
    "top_command": top_cmd,
    "by_command": {
        k: {
            "calls":        v["calls"],
            "saved_tokens": f"{v['saved_tokens']/1000:.1f}K",
            "saved_usd":    f"{v['saved_usd']:.2f}",
        }
        for k, v in sorted(by_cmd.items(), key=lambda x: -x[1]["saved_tokens"])
    }
}
with open(out_path, 'w') as f:
    json.dump(result, f, indent=2)
PYEOF
    rm -f "$HISTORY_TMP"
  fi
fi

rmdir "$LOCK" 2>/dev/null
