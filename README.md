# Claude Code Status Line

A two-line status bar for [Claude Code](https://claude.com/claude-code) showing
the model, current repo/branch, a context-usage meter, session cost + timer, and
your **today / month-to-date** token spend across all sessions.

```
[Opus 4.8] 📁 my-repo 🌿 main ████████░░ 80% ⏱ 12m34s
Jun 12 ┃ sess $0.42 ┃ today $3.10·1.2M tok ┃ MTD $48.70·19.4M tok ┃ ⚡ 1.2M/19.4M
```

The today/MTD figures are computed by scanning your local Claude Code transcript
files (`~/.claude/projects/**/*.jsonl`) and pricing each assistant message by
model. Nothing leaves your machine.

## How it works

Five scripts and two cache files:

| File | Role |
|------|------|
| `statusline.sh` | Entry point Claude runs on every render. Reads the JSON Claude pipes to stdin, draws the bar, fires native notifications (macOS/Linux/Windows) at cost/context thresholds, and triggers a background refresh when the cache is stale (>60s). Never blocks the prompt. |
| `statusline-refresh.sh` | Background worker. Runs the Python aggregator and (optionally) the `rtk` savings parse under an atomic lock. |
| `statusline-usage.py` | Scans transcripts, sums per-model token usage, prices it, buckets by local date, writes the usage cache. |
| `statusline-toggle.sh` | Toggle the status bar on/off. Creates/removes `~/.claude/.statusline-hidden`. |
| `statusline-show.sh` | Force-display the last cached render regardless of the hidden flag. Shows a staleness warning if the cache is older than 5 minutes. |
| `~/.claude/.statusline-usage-cache.json` | Cached today/MTD cost + tokens (auto-created). |
| `~/.claude/.statusline-rtk-cache.json` | Cached `rtk` savings (auto-created, only if `rtk` is installed). |
| `~/.claude/.statusline-last.ansi` | Last rendered output (auto-created, used by `statusline-show.sh`). |

The cache is refreshed in the background under a `mkdir`-based lock so the scan
never delays your prompt. A crashed refresh leaves a stale lock that's reaped
after 5 minutes.

## Requirements

- **Claude Code**
- **`python3`** — for JSON parsing and the usage aggregator
- `git`, `awk`, `date`, `sed` — standard, already on macOS/Linux/WSL
- *(optional)* **`rtk`** — a token-saving CLI proxy. If it's not installed, the
  `⚡ rtk` segment is simply omitted; everything else works.
- *(optional, Windows only)* **`powershell.exe`** + **`cygpath`** — for toast
  notifications and Windows-native path conversion. Notifications are silently
  skipped if unavailable.

> **Note**: `jq` is no longer required. All JSON parsing uses `python3`.

## Install

1. Copy the five scripts into your Claude config dir:

   ```bash
   cp statusline.sh statusline-refresh.sh statusline-usage.py \
      statusline-show.sh statusline-toggle.sh ~/.claude/
   chmod +x ~/.claude/statusline.sh ~/.claude/statusline-refresh.sh \
            ~/.claude/statusline-show.sh ~/.claude/statusline-toggle.sh
   ```

2. Point Claude Code at the status line. Edit `~/.claude/settings.json` and add
   the `statusLine` block, replacing `YOUR_USERNAME` with your actual home path:

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "/Users/YOUR_USERNAME/.claude/statusline.sh"
     }
   }
   ```

   Tip: `echo "$HOME/.claude/statusline.sh"` prints the exact path to use.

3. Start (or restart) Claude Code. The first render shows
   `today / MTD — computing…`; the figures appear once the background scan
   finishes (a second or two).

## Toggle & peek

```bash
# Hide the status bar (it keeps running in the background)
bash ~/.claude/statusline-toggle.sh   # → "Status line: OFF"

# Show it again
bash ~/.claude/statusline-toggle.sh   # → "Status line: ON"

# Peek at the last rendered output while hidden
bash ~/.claude/statusline-show.sh
```

When the bar is hidden, `statusline-show.sh` still prints the last cached render
and warns you if it is more than 5 minutes stale.

## Notifications (macOS, Linux, Windows)

The status line fires native notifications at the thresholds below. The backend
is picked automatically:

| OS | Backend | Needs |
|----|---------|-------|
| macOS | `osascript` (Notification Center) | built in |
| Linux | `notify-send` | `libnotify-bin` / `libnotify` |
| Windows (Git Bash/WSL) | PowerShell toast | `powershell.exe` (+ `cygpath`) |

If the backend isn't available, notifications are silently skipped.

| Threshold (default) | Title | Message |
|---------------------|-------|---------|
| Context ≥ 80% | Claude Code ⚠️ | Context window at X% |
| Context ≥ 90% | Claude Code 🔴 | Context at X% — run /compact |
| Session cost ≥ $5 | Claude Code | Session cost $X.XX — over $5 |
| Session cost ≥ $10 | Claude Code 💸 | Session cost $X.XX — over $10 |
| Daily spend ≥ $30 | Claude Code ⚠️ | Daily spend $X.XX — over $30 |
| Daily spend ≥ $40 | Claude Code 🔴 | Daily spend $X.XX — over $40 |

Each threshold fires at most once per cooldown window (default 10 minutes,
enforced by a flag file under `~/.claude/`).

### Configuring thresholds

Add an optional `notify` block to `~/.claude/.claude-budget.json`. Every key is
optional; missing or invalid values fall back to the defaults shown here.

```json
{
  "notify": {
    "enabled": true,
    "cooldown_min": 10,
    "context_warn": 80, "context_crit": 90,
    "session_warn": 5,  "session_crit": 10,
    "daily_warn": 30,   "daily_crit": 40
  },
  "git_status": true
}
```

Set `"enabled": false` to turn notifications off while keeping the bar.

## Git state

Next to the branch the bar shows `●N` (modified/staged files), `?N` (untracked),
`↑N` (commits ahead of upstream) and `↓N` (behind). Zero values are hidden. It
reads local state only (no fetch), so `↓` reflects your last `git fetch`. The
result is cached for 5 seconds per repo so large repos don't slow rendering.
Set `"git_status": false` in `~/.claude/.claude-budget.json` to disable it.

## Worktree detection

When the current directory is a **linked git worktree** (`.git` is a file rather
than a directory), the bar appends a `⑂ wt` indicator to the repo name so you
always know which worktree you're in.

## Pricing

The today/MTD **cost** figures are estimated from a hard-coded rate table in
`statusline-usage.py`. The shipped defaults are Anthropic list prices as of
**June 2026** (USD per million tokens):

All prices are per million tokens (MTok).

| Model | API ID | Input | Output | Cache write | Cache read | Status |
|-------|--------|------:|-------:|------------:|-----------:|--------|
| Claude Fable 5   | `claude-fable-5`              | $10.00 | $50.00 | $12.50 | $1.00 | Current |
| Claude Opus 4.8  | `claude-opus-4-8`             |  $5.00 | $25.00 |  $6.25 | $0.50 | Current |
| Claude Sonnet 4.6| `claude-sonnet-4-6`           |  $3.00 | $15.00 |  $3.75 | $0.30 | Current |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001`   |  $1.00 |  $5.00 |  $1.25 | $0.10 | Current |
| Claude Opus 4.7  | `claude-opus-4-7`             |  $5.00 | $25.00 |  $6.25 | $0.50 | Legacy |
| Claude Opus 4.6  | `claude-opus-4-6`             |  $5.00 | $25.00 |  $6.25 | $0.50 | Legacy |
| Claude Sonnet 4.5| `claude-sonnet-4-5-20250929`  |  $3.00 | $15.00 |  $3.75 | $0.30 | Legacy |
| Claude Opus 4.5  | `claude-opus-4-5-20251101`    |  $5.00 | $25.00 |  $6.25 | $0.50 | Legacy |
| Claude Opus 4.1  | `claude-opus-4-1-20250805`    | $15.00 | $75.00 | $18.75 | $1.50 | Deprecated (Aug 2026) |
| Claude Sonnet 4  | `claude-sonnet-4-20250514`    |  $3.00 | $15.00 |  $3.75 | $0.30 | Retired Jun 2026 |
| Claude Opus 4    | `claude-opus-4-20250514`      | $15.00 | $75.00 | $18.75 | $1.50 | Retired Jun 2026 |

The key is matched as a **substring** of the model id (e.g. `"opus"` matches
`claude-opus-4-8`). More specific keys (e.g. `"opus-4-1"`) take priority over
broader ones. A model whose family isn't in the table contributes **tokens
but $0 cost** — so an unknown model silently undercounts spend.

> ⚠️ **UPDATE THESE RATES FOR YOUR OWN PLAN BEFORE TRUSTING THE COST.**
> These are public list prices and **will drift** as Anthropic changes pricing
> or adds models. Your actual rate may differ (volume discounts, enterprise
> agreements, a model family not listed). Edit the `RATES` dict at the top of
> `statusline-usage.py` to match what *you* pay. The token counts are always
> accurate; the dollar figures are only as good as this table.

## Customizing

- **Colors / layout** — the ANSI color vars and the final two `echo -e` lines in
  `statusline.sh` control the look.
- **Refresh interval** — the `-mmin +1` (60s) test in `statusline.sh` controls
  how often the background scan runs.
- **Notification thresholds and cooldown** — the `notify` block in
  `~/.claude/.claude-budget.json` (see above), no script edits needed.

## New Analysis Scripts

Run any of these from the repo directory (or copy to `~/.claude/`).
First copy `statusline_core.py` to `~/.claude/` alongside the existing scripts.

| Script | Purpose | Usage |
|--------|---------|-------|
| `statusline-epic-report.py` | Cost by Jira epic/ticket | `python statusline-epic-report.py --days 30` |
| `statusline-cache-analyzer.py` | Cache hit-rate per project | `python statusline-cache-analyzer.py` |
| `statusline-model-advisor.py` | Flag over-qualified Opus sessions | `python statusline-model-advisor.py` |
| `statusline-dashboard.py` | Generate HTML spend dashboard | `python statusline-dashboard.py --output dashboard.html` |
| `statusline-budget-check.sh` | Budget gate (Stop hook friendly) | `statusline-budget-check.sh [--epic CSA-12345]` |
| `statusline-waste-detector.py` | Detect re-reads, cold cache, trivial Opus | `python statusline-waste-detector.py --days 7` |
| `statusline-jira-post.py` | Post epic cost to Jira (dry-run safe) | `python statusline-jira-post.py --epic CSA-12345` |

### Installation

```bash
cp statusline_core.py ~/.claude/
cp statusline-epic-report.py ~/.claude/
cp statusline-cache-analyzer.py ~/.claude/
cp statusline-model-advisor.py ~/.claude/
cp statusline-dashboard.py ~/.claude/
cp statusline-budget-check.sh ~/.claude/
cp statusline-waste-detector.py ~/.claude/
cp statusline-jira-post.py ~/.claude/
chmod +x ~/.claude/statusline-budget-check.sh
```

### Budget config

Copy `.claude-budget.json.example` to `.claude-budget.json` in any project dir (or `~/.claude/`):

```json
{ "daily": 20, "mtd": 200, "epic": { "CSA-12345": 50 } }
```

### Jira integration

Set env vars before running `statusline-jira-post.py`:

```bash
export JIRA_URL=https://yourorg.atlassian.net
export JIRA_EMAIL=you@example.com
export JIRA_API_TOKEN=your_token_here
```

Or write them to `~/.claude/.jira-config` (one `KEY=value` per line).

---

## Notes

- Token cost is an **estimate** based on the rates you configure; it won't match
  a billing dashboard to the penny.
- The MTD scan only reads files modified in the current month, so it stays fast
  even with a large transcript history.
- Duplicate API responses across compacted/resumed sessions are deduplicated on
  `(message.id, requestId)` so each response is counted exactly once.
