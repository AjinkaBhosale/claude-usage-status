# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code status line plugin — shell scripts and Python scripts that render a two-line bar showing model, repo/branch, context usage, session cost/timer, and today/MTD spend. Also includes a suite of analysis scripts for spend breakdowns, cache efficiency, budget enforcement, and reporting.

## Install (core status line)

```bash
cp statusline.sh statusline-refresh.sh statusline-usage.py statusline_core.py \
   statusline-show.sh statusline-toggle.sh ~/.claude/
chmod +x ~/.claude/statusline.sh ~/.claude/statusline-refresh.sh \
         ~/.claude/statusline-show.sh ~/.claude/statusline-toggle.sh
```

Wire into `~/.claude/settings.json` using `settings.snippet.json` as a template.

## Install (analysis scripts)

```bash
cp statusline_core.py statusline-epic-report.py statusline-cache-analyzer.py \
   statusline-model-advisor.py statusline-dashboard.py statusline-budget-check.sh \
   statusline-waste-detector.py statusline-jira-post.py statusline-corporate-report.py \
   ~/.claude/
chmod +x ~/.claude/statusline-budget-check.sh
```

## Scripts

### Core status line

| Script | Role |
|--------|------|
| `statusline.sh` | Entry point called on every render. Reads JSON from stdin, draws the bar, fires Windows toast notifications, triggers background refresh when cache is stale (>60s). Must never block. |
| `statusline-refresh.sh` | Background worker. Runs `statusline-usage.py` and optionally parses `rtk gain --graph` under an atomic `mkdir` lock. |
| `statusline-usage.py` | Scans `~/.claude/projects/**/*.jsonl`, sums per-model token usage, prices it, buckets by local date, writes cache JSON. |
| `statusline-toggle.sh` | Creates/removes `~/.claude/.statusline-hidden` to show/hide the bar. |
| `statusline-show.sh` | Prints the last cached render from `~/.claude/.statusline-last.ansi`. |

### Analysis scripts (run from repo dir or `~/.claude/`)

| Script | Purpose | Usage |
|--------|---------|-------|
| `statusline_core.py` | Shared library — `RATES`, `iter_usage_records()`, `iter_all_lines()`. Not run directly. |  |
| `statusline-epic-report.py` | Cost by Jira epic/ticket | `python statusline-epic-report.py --days 30` |
| `statusline-cache-analyzer.py` | Cache hit-rate per project | `python statusline-cache-analyzer.py` |
| `statusline-model-advisor.py` | Flag over-qualified Opus sessions | `python statusline-model-advisor.py` |
| `statusline-dashboard.py` | Generate self-contained HTML spend dashboard | `python statusline-dashboard.py --output dashboard.html` |
| `statusline-budget-check.sh` | Budget gate (Stop hook friendly, exits 0/1/2) | `statusline-budget-check.sh [--epic CSA-12345]` |
| `statusline-waste-detector.py` | Detect re-reads, cold cache, trivial Opus | `python statusline-waste-detector.py --days 7` |
| `statusline-jira-post.py` | Post epic cost to Jira (dry-run safe) | `python statusline-jira-post.py --epic CSA-12345` |
| `statusline-corporate-report.py` | All-in-one report: budget, model mix, cache, waste, epics | `python statusline-corporate-report.py --period week` or `--month 2026-06 --format html` |

## Cache files (auto-created under `~/.claude/`)

| File | Contents |
|------|----------|
| `.statusline-usage-cache.json` | `day_cost`, `day_tokens`, `mtd_cost`, `mtd_tokens`, `computed_at` |
| `.statusline-rtk-cache.json` | `total_saved`, `today_saved`, `total_pct` from `rtk gain` |
| `.statusline-last.ansi` | Last rendered ANSI output (used by `statusline-show.sh`) |
| `.statusline-usage.lock/` | `mkdir`-based atomic lock; reaped after 5 minutes if stale |
| `.statusline-hidden` | Presence suppresses all output |
| `.notify-<key>` | Per-threshold cooldown flags for Windows toast notifications |

## Budget config

Copy `.claude-budget.json.example` to `.claude-budget.json` in any project dir or `~/.claude/`:

```json
{ "daily": 20, "mtd": 200, "epic": { "CSA-12345": 50 } }
```

`statusline-budget-check.sh` exits `0` (OK), `1` (>80% of any limit), or `2` (over budget) — suitable for use as a Claude Code Stop hook.

## Jira integration

Set env vars or write to `~/.claude/.jira-config` (one `KEY=value` per line):

```bash
export JIRA_URL=https://yourorg.atlassian.net
export JIRA_EMAIL=you@example.com
export JIRA_API_TOKEN=your_token_here
```

## Architecture decisions

**Non-blocking render**: `statusline.sh` triggers `statusline-refresh.sh` via `nohup ... &` only when the cache is missing or older than 60s. The render always completes immediately using cached data.

**Shared library (`statusline_core.py`)**: All analysis scripts import `RATES`, `iter_usage_records()`, and `iter_all_lines()` from this module. Update pricing here once; everything picks it up.

**Deduplication**: `iter_usage_records()` deduplicates on `(message.id, requestId)` to handle the same API response appearing in multiple files (compacted/resumed sessions).

**JSON parsing**: Uses `python3` everywhere; `jq` is not required.

**Windows notifications**: `statusline.sh` writes a `.ps1` file and calls `powershell.exe` via a `disown`ed background process. Falls back silently if `powershell.exe` or `cygpath` are absent.

**Rate matching**: `RATES` keys are matched as substrings of the model ID. More specific keys (e.g. `"opus-5-5"`, `"opus-4-1"`) must appear before broader ones (e.g. `"opus"`). Haiku 5.5 carries a `long`/`long_over` tier, applied only by `price_tokens(..., per_message=True)`. An unknown model contributes tokens but $0 cost.

## Updating pricing

Edit `RATES` at the top of `statusline_core.py` — it is the only copy (`statusline-usage.py` imports it, so `statusline_core.py` must be installed alongside it). Source: the Anthropic pricing page. Rates differ by model *version*, so add version-specific keys before the family key. A model that matches no key is priced at $0.

## Customizing

- **Colors / layout**: ANSI vars and the final `echo -e` lines in `statusline.sh`
- **Cache refresh interval**: `-mmin +1` test in `statusline.sh` (currently 60s)
- **Notification cooldown**: `-mmin +10` test in the `_notify()` function (currently 10 min)
- **Notification thresholds**: context at 80%/90%, session at $5/$10, daily at $30/$40
