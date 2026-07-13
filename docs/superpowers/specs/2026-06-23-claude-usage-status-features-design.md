# Claude Usage Status — Feature Expansion Design

**Date:** 2026-06-23
**Status:** Approved
**Scope:** HTML feature showcase presentation + 8 new scripts

---

## Overview

Extend `claude-usage-status` with 8 new scripts that mine existing Claude Code transcript JSONL files for deeper cost attribution, efficiency analysis, and actionable savings recommendations. Deliver a companion `feature-showcase.html` as an interactive static presentation of all new capabilities.

---

## Deliverable 1: `feature-showcase.html`

A single self-contained HTML file (no CDN, no external dependencies — all CSS and JS inline). Serves as a shareable pitch/demo of every new feature.

**Design:**
- Dark terminal aesthetic (`#0d1117` background, monospace accent blocks)
- Header: project name + tagline
- 8 feature cards in a 2-column responsive grid
- Each card: number badge, feature name, impact tag (High/Medium), one-line description
- Click to expand: mockup terminal output block (ANSI-style colors) + 3 how-it-works bullets + estimated savings/benefit
- Priority roadmap strip at bottom showing recommended build order
- Smooth CSS transitions on hover and expand/collapse — no JS framework

---

## Deliverable 2: `statusline-core.py` (shared module)

Extracts the JSONL scanning, deduplication, pricing table, and date-bucketing logic currently embedded in `statusline-usage.py` into a reusable module. All 6 new Python scripts import from it.

**Public API:**
```python
load_usage(days=30)           # → list[SessionRecord]
price_tokens(model, in, out, cache_write, cache_read)  # → float USD
PRICING                       # dict of model → rates
```

**Constraint:** `statusline-usage.py` is refactored to import from `statusline-core.py` — no behavior change, just deduplication.

---

## Deliverable 3: 8 New Scripts

### 1. `statusline-epic-report.py`
Scans all transcript JSONL files. Extracts Jira ticket refs (`CSA-\d+`, `ILLUM-\d+`) from:
- Git branch name: decoded from the JSONL file path → project directory → `git log --format='%D' -1` (branch not stored in transcripts; derived at report time)
- Text content of user messages (regex scan)

Groups cost + token totals by ticket. Outputs a sorted table (by cost desc) plus optional `--json` flag.

```
Usage: python statusline-epic-report.py [--days 30] [--json] [--min-cost 0.10]
```

### 2. `statusline-cache-analyzer.py`
Aggregates `cache_creation_input_tokens` and `cache_read_input_tokens` per project directory. Computes:
- Cache hit rate % = read / (read + fresh input)
- Dollar saved via cache reads vs. full input cost
- Projects with 0% hit rate flagged as "cache cold"

```
Usage: python statusline-cache-analyzer.py [--project <path>] [--days 30]
```

### 3. `statusline-model-advisor.py`
Identifies sessions where Opus was used but the session profile suggests Sonnet would have sufficed:
- Output tokens < 500 AND input tokens < 5 000 AND session < 3 min
- Aggregates over-qualified sessions, quantifies potential savings

```
Usage: python statusline-model-advisor.py [--days 30] [--threshold-tokens 5000]
```

### 4. `statusline-dashboard.py`
Generates a fully self-contained `claude-dashboard.html` file with:
- 30-day daily spend bar chart (SVG, no JS charting lib)
- Model mix donut (SVG)
- Top 10 most expensive projects table
- Cache savings summary
- MTD vs. prior month comparison

```
Usage: python statusline-dashboard.py [--days 30] [--output claude-dashboard.html]
```

### 5. `statusline-budget-check.sh`
Reads a `.claude-budget.json` config file from the current directory or `~/.claude/`. Checks:
- `daily` budget vs. today's spend (from usage cache)
- `epic` budgets vs. `statusline-epic-report.py` output

Exits `0` = within budget, `1` = warning (>80%), `2` = over budget. Designed to wire into Claude Code `Stop` hook.

```
Usage: statusline-budget-check.sh [--epic CSA-12345]
Config: .claude-budget.json → { "daily": 20, "mtd": 200, "epic": { "CSA-12345": 50 } }
```

### 6. `statusline-waste-detector.py`
Scans transcripts for expensive anti-patterns:
- **File re-reads**: same file path appears in 5+ user messages in one session
- **Cold cache sessions**: >10 turns with 0 cache_read tokens
- **Trivial Opus**: output < 200 tokens on Opus (likely Sonnet-appropriate)

Outputs a waste report with per-session breakdown and total estimated waste in USD.

```
Usage: python statusline-waste-detector.py [--days 7] [--json]
```

### 7. `statusline-refresh.sh` (extended)
Adds RTK per-command savings drill-down to existing background refresh:
- Parses `rtk gain --history` for per-command-type savings breakdown
- Caches to `~/.claude/.statusline-rtk-detail-cache.json`
- Schema: `{ "by_command": { "git": { "calls": N, "saved_tokens": N, "saved_usd": N }, ... }, "top_command": "git", "total_saved_usd": N }`

No behavior change to the status bar render — data available for future use.

### 8. `statusline-jira-post.py`
Posts an epic cost summary as a Jira comment via REST API.
- Looks up sessions attributed to a given epic (uses epic-report logic)
- Formats: sessions count, total tokens, total cost USD, models used, date range
- Requires: `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` env vars or `~/.claude/.jira-config`

```
Usage: python statusline-jira-post.py --epic CSA-12345 [--dry-run]
```

---

## File Layout (final state)

```
claude-usage-status/
├── statusline.sh                    (existing — unchanged)
├── statusline-usage.py              (existing — refactored to import core)
├── statusline-refresh.sh            (existing — extended with RTK drill-down)
├── statusline-toggle.sh             (existing — unchanged)
├── statusline-show.sh               (existing — unchanged)
├── statusline-core.py               (NEW — shared scanning/pricing module)
├── statusline-epic-report.py        (NEW)
├── statusline-cache-analyzer.py     (NEW)
├── statusline-model-advisor.py      (NEW)
├── statusline-dashboard.py          (NEW)
├── statusline-budget-check.sh       (NEW)
├── statusline-waste-detector.py     (NEW)
├── statusline-jira-post.py          (NEW)
├── .claude-budget.json.example      (NEW — config template)
├── feature-showcase.html            (NEW — interactive presentation)
├── settings.snippet.json            (existing — unchanged)
├── README.md                        (existing — update with new scripts table)
└── CLAUDE.md                        (existing — unchanged)
```

---

## Constraints & Non-Goals

- All scripts run with no new pip dependencies (stdlib only: `json`, `os`, `re`, `datetime`, `pathlib`, `urllib`)
- `statusline-jira-post.py` uses `urllib.request` — no `requests` lib
- No data leaves the machine except `statusline-jira-post.py` (which is explicit and opt-in)
- No changes to the status bar render format (backward compatible)
- `statusline-dashboard.py` HTML output uses inline SVG — no Chart.js, no CDN
- Pricing table lives in `statusline-core.py` only (single source of truth)

---

## Success Criteria

- All 8 scripts run without errors on an existing `~/.claude/projects/` directory
- `feature-showcase.html` opens in any browser with no network requests
- `statusline-usage.py` behavior is identical after refactor (same cache output)
- `statusline-budget-check.sh` exits non-zero when daily spend exceeds threshold
- `statusline-jira-post.py --dry-run` prints the comment body without posting
