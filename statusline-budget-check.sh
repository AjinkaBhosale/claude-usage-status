#!/bin/bash
# Check Claude Code spend against .claude-budget.json thresholds.
# Exit 0 = OK, 1 = warning (>80%), 2 = over budget.
# Usage: statusline-budget-check.sh [--epic TICKET-ID]

EPIC_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --epic) EPIC_ID="$2"; shift 2 ;;
    *) shift ;;
  esac
done

BUDGET_FILE=""
[ -f ".claude-budget.json" ] && BUDGET_FILE=".claude-budget.json"
[ -z "$BUDGET_FILE" ] && [ -f "$HOME/.claude/.claude-budget.json" ] && \
  BUDGET_FILE="$HOME/.claude/.claude-budget.json"

if [ -z "$BUDGET_FILE" ]; then
  echo "  No .claude-budget.json found in current dir or ~/.claude/"
  echo "  Copy .claude-budget.json.example to get started."
  exit 0
fi

USAGE_CACHE="$HOME/.claude/.statusline-usage-cache.json"
USAGE_CACHE_WIN=$(cygpath -w "$USAGE_CACHE" 2>/dev/null || echo "$USAGE_CACHE")
BUDGET_WIN=$(cygpath -w "$BUDGET_FILE" 2>/dev/null || echo "$BUDGET_FILE")
SCRIPT_DIR=$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")

result=$(PYTHONUTF8=1 CLAUDE_EPIC_ID="$EPIC_ID" CLAUDE_SCRIPT_DIR="$(dirname "$(realpath "$0" 2>/dev/null || echo "$0")")" python3 - <<PYEOF
import json, sys, os

try:
    budget = json.load(open(r'$BUDGET_WIN'))
except Exception as e:
    print(f"Error reading budget: {e}")
    print(0)
    sys.exit(0)

cache_file = r'$USAGE_CACHE_WIN'
if not os.path.exists(cache_file):
    print("  No usage cache found — run statusline-usage.py first.")
    print(0)
    sys.exit(0)

cache = json.load(open(cache_file))
day_cost    = cache.get('day_cost', 0)
mtd_cost    = cache.get('mtd_cost', 0)
daily_limit = budget.get('daily', 0)
mtd_limit   = budget.get('mtd', 0)
exit_code   = 0
lines       = []

import datetime
lines.append(f"  Budget Check · {datetime.date.today()}")
lines.append(f"  {'─'*50}")

def bar(pct):
    filled = min(10, int(pct / 10))
    return '█' * filled + '░' * (10 - filled)

def status(pct):
    if pct >= 100: return '✗ OVER'
    if pct >= 80:  return '⚠ watch'
    return '✓ OK'

if daily_limit:
    pct = day_cost / daily_limit * 100
    lines.append(f"  Daily  \${day_cost:.2f} / \${daily_limit:.2f}  {bar(pct)}  {pct:.0f}%  {status(pct)}")
    if pct >= 100 and exit_code < 2: exit_code = 2
    elif pct >= 80 and exit_code < 1: exit_code = 1

if mtd_limit:
    pct = mtd_cost / mtd_limit * 100
    lines.append(f"  MTD   \${mtd_cost:.2f} / \${mtd_limit:.2f}  {bar(pct)}  {pct:.0f}%  {status(pct)}")
    if pct >= 100 and exit_code < 2: exit_code = 2
    elif pct >= 80 and exit_code < 1: exit_code = 1

epic_id = os.environ.get('CLAUDE_EPIC_ID', '')
if epic_id:
    epics = budget.get('epic', {})
    limit = epics.get(epic_id)
    if limit:
        script = os.path.join(os.environ.get('CLAUDE_SCRIPT_DIR', '.'), 'statusline-epic-report.py')
        import subprocess
        r = subprocess.run([sys.executable, script, '--json', '--days', '90'],
                           capture_output=True, text=True)
        try:
            epic_data = json.loads(r.stdout or '[]')
        except Exception:
            epic_data = []
        epic_cost = next((e['cost'] for e in epic_data if e['ticket'] == epic_id), 0.0)
        pct = epic_cost / limit * 100
        lines.append(f"  {epic_id}  \${epic_cost:.2f} / \${limit:.2f}  {bar(pct)}  {pct:.0f}%  {status(pct)}")
        if pct >= 100 and exit_code < 2: exit_code = 2
        elif pct >= 80 and exit_code < 1: exit_code = 1

lines.append(f"  {'─'*50}")
print('\n'.join(lines))
print(exit_code)
PYEOF
)

body=$(echo "$result" | head -n -1)
code=$(echo "$result" | tail -1)
echo "$body"
exit "${code:-0}"
