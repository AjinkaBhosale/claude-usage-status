# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code status line plugin — five shell scripts and one Python aggregator that render a two-line bar showing model, repo/branch, context usage, session cost/timer, and today/MTD spend.

## Install

```bash
cp statusline.sh statusline-refresh.sh statusline-usage.py \
   statusline-show.sh statusline-toggle.sh ~/.claude/
chmod +x ~/.claude/statusline.sh ~/.claude/statusline-refresh.sh \
         ~/.claude/statusline-show.sh ~/.claude/statusline-toggle.sh
```

Then wire it into `~/.claude/settings.json` (use `settings.snippet.json` as a template):
```json
{
  "statusLine": {
    "type": "command",
    "command": "/Users/YOUR_USERNAME/.claude/statusline.sh"
  }
}
```

## Scripts

| Script | Role |
|--------|------|
| `statusline.sh` | Entry point called on every render. Reads JSON from stdin, draws the bar, fires Windows toast notifications, triggers background refresh when cache is stale (>60s). Must never block. |
| `statusline-refresh.sh` | Background worker. Runs `statusline-usage.py` and optionally parses `rtk gain --graph` under an atomic `mkdir` lock. |
| `statusline-usage.py` | Scans `~/.claude/projects/**/*.jsonl`, sums per-model token usage, prices it, buckets by local date, writes cache JSON. |
| `statusline-toggle.sh` | Creates/removes `~/.claude/.statusline-hidden` to show/hide the bar. |
| `statusline-show.sh` | Prints the last cached render from `~/.claude/.statusline-last.ansi`. |

## Cache files (auto-created under `~/.claude/`)

| File | Contents |
|------|----------|
| `.statusline-usage-cache.json` | `day_cost`, `day_tokens`, `mtd_cost`, `mtd_tokens`, `computed_at` |
| `.statusline-rtk-cache.json` | `total_saved`, `today_saved`, `total_pct` from `rtk gain` |
| `.statusline-last.ansi` | Last rendered ANSI output (used by `statusline-show.sh`) |
| `.statusline-usage.lock/` | `mkdir`-based atomic lock; reaped after 5 minutes if stale |
| `.statusline-hidden` | Presence suppresses all output |
| `.notify-<key>` | Per-threshold cooldown flags for Windows toast notifications |

## Architecture decisions

**Non-blocking render**: `statusline.sh` triggers `statusline-refresh.sh` via `nohup ... &` only when the cache is missing or older than 60s. The render always completes immediately using cached data.

**Deduplication**: `statusline-usage.py` deduplicates on `(message.id, requestId)` to handle the same API response appearing in multiple files (compacted/resumed sessions).

**JSON parsing**: Uses `python3 -c` everywhere; `jq` is not required.

**Windows notifications**: `statusline.sh` writes a `.ps1` file and calls `powershell.exe` via `disown`ed background process. Falls back silently if `powershell.exe` or `cygpath` are absent.

## Updating pricing

Edit the `RATES` dict at the top of `statusline-usage.py`. Keys are matched as **substrings** of the model ID — more specific keys must appear before broader ones (e.g. `"opus-4-1"` before `"opus"`).

## Customizing

- **Colors / layout**: ANSI vars and the final `echo -e` lines in `statusline.sh`
- **Cache refresh interval**: `-mmin +1` test in `statusline.sh` (currently 60s)
- **Notification cooldown**: `-mmin +10` test in the `_notify()` function (currently 10 min)
- **Notification thresholds**: context at 80%/90%, session at $5/$10, daily at $30/$40
