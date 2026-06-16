#!/bin/bash
# Claude Code status line. Reads the JSON Claude pipes in on stdin and renders a
# two-line bar: model, dir/repo/branch/worktree, context-usage meter, session
# cost/timer, plus cached today/MTD usage. Never blocks — a stale cache triggers
# a background refresh under an atomic lock.
input=$(cat)

# Show/hide: touch ~/.claude/.statusline-hidden to suppress output; use statusline-show.sh to peek.
HIDDEN_FLAG="$HOME/.claude/.statusline-hidden"
LAST_RENDER="$HOME/.claude/.statusline-last.ansi"
[ -f "$HIDDEN_FLAG" ] && exit 0

# Parse all top-level fields from the input JSON via Python (no jq required).
eval "$(echo "$input" | python3 -c "
import json, sys, shlex
d = json.load(sys.stdin)
pct_raw = d.get('context_window', {}).get('used_percentage', 0) or 0
pct = int(float(str(pct_raw).split('.')[0])) if pct_raw else 0
print('MODEL=' + shlex.quote(str(d.get('model', {}).get('display_name', ''))))
print('DIR=' + shlex.quote(str(d.get('workspace', {}).get('current_dir', ''))))
print('COST=' + shlex.quote(str(d.get('cost', {}).get('total_cost_usd', 0) or 0)))
print('PCT=' + shlex.quote(str(pct)))
print('DURATION_MS=' + shlex.quote(str(int(d.get('cost', {}).get('total_duration_ms', 0) or 0))))
")"

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
# Pick bar color based on context usage
if [ "$PCT" -ge 90 ]; then BAR_COLOR="$RED"
elif [ "$PCT" -ge 70 ]; then BAR_COLOR="$YELLOW"
else BAR_COLOR="$GREEN"; fi
FILLED=$((PCT / 10)); EMPTY=$((10 - FILLED))
printf -v FILL "%${FILLED}s"; printf -v PAD "%${EMPTY}s"
BAR="${FILL// /█}${PAD// /░}"
MINS=$((DURATION_MS / 60000)); SECS=$(printf '%02d' $(((DURATION_MS % 60000) / 1000)))
# ---- Directory / repo / branch / worktree ----
DIM='\033[2m'; BOLD='\033[1m'
cd "$DIR" 2>/dev/null
TOP=$(git rev-parse --show-toplevel 2>/dev/null)
BRANCH=""
WT=""
if [ -n "$TOP" ]; then
  PLACE="${TOP##*/}"
  [ ${#PLACE} -gt 24 ] && PLACE="${PLACE:0:22}…"
  BR=$(git branch --show-current 2>/dev/null)
  [ ${#BR} -gt 28 ] && BR="${BR:0:26}…"
  [ -n "$BR" ] && BRANCH=" 🌿 $BR"
  [ -f "$TOP/.git" ] && WT=" ${DIM}⑂ wt${RESET}"   # a linked worktree has a .git file, not a dir
elif [ "$DIR" = "$HOME" ]; then PLACE="~"
else
  PLACE="${DIR##*/}"
  [ ${#PLACE} -gt 24 ] && PLACE="${PLACE:0:22}…"
fi
COST_FMT=$(printf '$%.2f' "$COST")

# ---- Notifications (Windows toast, 10-min cooldown per threshold) ----
_notify() {
  local key="$1" title="$2" msg="$3"
  local flag="$HOME/.claude/.notify-${key}"
  [ -f "$flag" ] && [ -z "$(find "$flag" -mmin +10 2>/dev/null)" ] && return
  touch "$flag"
  local ps="$HOME/.claude/.notify-${key}.ps1"
  cat > "$ps" << 'PSEOF'
param($T, $M)
try {
  [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
  $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $xml.GetElementsByTagName('text')[0].InnerText = $T
  $xml.GetElementsByTagName('text')[1].InnerText = $M
  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Claude Code').Show($toast)
} catch {
  Add-Type -AssemblyName System.Windows.Forms
  $n = New-Object System.Windows.Forms.NotifyIcon
  $n.Icon = [System.Drawing.SystemIcons]::Warning
  $n.BalloonTipTitle = $T
  $n.BalloonTipText = $M
  $n.Visible = $true
  $n.ShowBalloonTip(8000)
  Start-Sleep -Milliseconds 8500
  $n.Dispose()
}
PSEOF
  local ps_win
  ps_win=$(cygpath -w "$ps" 2>/dev/null || echo "$ps")
  powershell.exe -WindowStyle Hidden -NonInteractive -File "$ps_win" -T "$title" -M "$msg" 2>/dev/null &
  disown 2>/dev/null
}
cost_gte() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a+0 >= b+0)}'; }

[ "$PCT" -ge 90 ] && _notify "ctx90" "Claude Code 🔴" "Context at ${PCT}% — run /compact"
[ "$PCT" -ge 80 ] && [ "$PCT" -lt 90 ] && _notify "ctx80" "Claude Code ⚠️" "Context window at ${PCT}%"
cost_gte "$COST" 10 && _notify "sess10" "Claude Code 💸" "Session cost ${COST_FMT} — over \$10"
cost_gte "$COST" 5 && ! cost_gte "$COST" 10 && _notify "sess5" "Claude Code" "Session cost ${COST_FMT} — over \$5"

# ---- Day / MTD usage (cached, refreshed in the background) ----
USAGE_CACHE="$HOME/.claude/.statusline-usage-cache.json"
RTK_CACHE="$HOME/.claude/.statusline-rtk-cache.json"
USAGE_LOCK="$HOME/.claude/.statusline-usage.lock"
REFRESH="$HOME/.claude/statusline-refresh.sh"
PROJECTS_DIR="$HOME/.claude/projects"
# Windows-native paths for Python's open() (cygpath converts /c/... → C:\...)
USAGE_CACHE_WIN=$(cygpath -w "$USAGE_CACHE" 2>/dev/null || echo "$USAGE_CACHE")
RTK_CACHE_WIN=$(cygpath -w "$RTK_CACHE" 2>/dev/null || echo "$RTK_CACHE")

# Refresh in the background when the cache is missing or older than 60s, so the
# transcript scan never blocks the render. mkdir is the atomic lock.
NEED_REFRESH=0
if [ ! -f "$USAGE_CACHE" ]; then NEED_REFRESH=1
elif [ -n "$(find "$USAGE_CACHE" -mmin +1 2>/dev/null)" ]; then NEED_REFRESH=1; fi
if [ "$NEED_REFRESH" -eq 1 ]; then
  if mkdir "$USAGE_LOCK" 2>/dev/null; then
    nohup bash "$REFRESH" "$USAGE_CACHE" "$RTK_CACHE" "$USAGE_LOCK" "$PROJECTS_DIR" >/dev/null 2>&1 &
    disown 2>/dev/null
  elif [ -n "$(find "$USAGE_LOCK" -mmin +5 2>/dev/null)" ]; then
    rmdir "$USAGE_LOCK" 2>/dev/null  # reap a stale lock from a crashed refresh
  fi
fi

humanize() {
  local n=${1:-0}
  if [ "$n" -ge 1000000000 ]; then awk -v n="$n" 'BEGIN{printf "%.1fB", n/1000000000}'
  elif [ "$n" -ge 1000000 ]; then awk -v n="$n" 'BEGIN{printf "%.1fM", n/1000000}'
  elif [ "$n" -ge 1000 ]; then awk -v n="$n" 'BEGIN{printf "%.1fK", n/1000}'
  else printf "%d" "$n"; fi
}

DATELBL=$(date '+%b %d' | sed 's/ 0/ /')
DATE_SEG="${BOLD}${CYAN}${DATELBL}${RESET}"
SESSION="${DIM}sess${RESET} ${YELLOW}${COST_FMT}${RESET}"
if [ -f "$USAGE_CACHE" ]; then
  eval "$(python3 -c "
import json, sys, shlex
try:
    with open(r'$USAGE_CACHE_WIN') as f:
        d = json.load(f)
    print('D_COST=' + shlex.quote(str(d.get('day_cost', 0) or 0)))
    print('M_COST=' + shlex.quote(str(d.get('mtd_cost', 0) or 0)))
    print('D_TOKENS=' + shlex.quote(str(int(d.get('day_tokens', 0) or 0))))
    print('M_TOKENS=' + shlex.quote(str(int(d.get('mtd_tokens', 0) or 0))))
    print('USAGE_OK=1')
except Exception:
    print('USAGE_OK=0')
" 2>/dev/null)"
  if [ "${USAGE_OK:-0}" = "1" ]; then
    D_TOK=$(humanize "$D_TOKENS")
    M_TOK=$(humanize "$M_TOKENS")
    D_COST_FMT=$(printf '$%.2f' "$D_COST")
    M_COST_FMT=$(printf '$%.2f' "$M_COST")
    LINE2="${DATE_SEG} ${DIM}┃${RESET} ${SESSION} ${DIM}┃${RESET} today ${YELLOW}${D_COST_FMT}${RESET}${DIM}·${RESET}${CYAN}${D_TOK}${RESET} ${DIM}tok ┃${RESET} MTD ${YELLOW}${M_COST_FMT}${RESET}${DIM}·${RESET}${CYAN}${M_TOK}${RESET} ${DIM}tok${RESET}"
    cost_gte "$D_COST" 40 && _notify "day40" "Claude Code 🔴" "Daily spend ${D_COST_FMT} — over \$40"
    cost_gte "$D_COST" 30 && ! cost_gte "$D_COST" 40 && _notify "day30" "Claude Code ⚠️" "Daily spend ${D_COST_FMT} — over \$30"
  else
    LINE2="${DATE_SEG} ${DIM}┃${RESET} ${SESSION} ${DIM}┃${RESET} today/MTD — computing…"
  fi
else
  LINE2="${DATE_SEG} ${DIM}┃${RESET} ${SESSION} ${DIM}┃${RESET} today/MTD — computing…"
fi

# Optional: token-savings from the "rtk" CLI, only shown if its cache exists.
if [ -f "$RTK_CACHE" ]; then
  RTK_OK=0
  eval "$(python3 -c "
import json, sys, shlex
try:
    with open(r'$RTK_CACHE_WIN') as f:
        d = json.load(f)
    print('R_TODAY=' + shlex.quote(str(d.get('today_saved', '0'))))
    print('R_TOTAL=' + shlex.quote(str(d.get('total_saved', '0'))))
    print('RTK_OK=1')
except Exception:
    print('RTK_OK=0')
" 2>/dev/null)"
  [ "$RTK_OK" = "1" ] && LINE2="$LINE2 ${DIM}┃${RESET} ${GREEN}⚡${RESET} ${CYAN}${R_TODAY}${RESET}${DIM}/${RESET}${CYAN}${R_TOTAL}${RESET}"
fi

LINE1="${CYAN}[$MODEL]${RESET} 📁 ${PLACE}${BRANCH}${WT} ${BAR_COLOR}${BAR}${RESET} ${PCT}% ⏱ ${MINS}m${SECS}s"
echo -e "$LINE1"
echo -e "  $LINE2"
{ echo -e "$LINE1"; echo -e "  $LINE2"; } > "$LAST_RENDER" 2>/dev/null
