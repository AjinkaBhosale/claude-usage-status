# Claude Usage Status — Feature Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 8 new analysis scripts and a self-contained interactive HTML feature showcase to the `claude-usage-status` repo.

**Architecture:** Extract shared JSONL scanning + pricing logic into `statusline_core.py`; all new Python scripts import from it. The HTML showcase is a single zero-dependency file with static mockup data. Each script is independently runnable from the command line.

**Tech Stack:** Python 3 stdlib only (`json`, `os`, `re`, `glob`, `datetime`, `pathlib`, `urllib.request`), Bash 4+, plain HTML/CSS/JS (no frameworks).

**Repo path:** `C:/Users/abhosale/OneDrive - NICE Ltd/Documents/Actions/QA/Repos/UI/claude-usage-status/`

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `statusline_core.py` | CREATE | Shared JSONL scanning, dedup, pricing — imported by all new scripts |
| `statusline-usage.py` | MODIFY | Refactor to import from core (no behavior change) |
| `feature-showcase.html` | CREATE | Interactive feature showcase presentation |
| `statusline-epic-report.py` | CREATE | Cost attribution by Jira epic/ticket |
| `statusline-cache-analyzer.py` | CREATE | Cache hit-rate + dollar savings per project |
| `statusline-model-advisor.py` | CREATE | Flag over-qualified Opus sessions, estimate savings |
| `statusline-dashboard.py` | CREATE | Generate self-contained HTML spend dashboard |
| `statusline-budget-check.sh` | CREATE | Budget gate (exits non-zero when over threshold) |
| `.claude-budget.json.example` | CREATE | Config template for budget check |
| `statusline-waste-detector.py` | CREATE | Detect file re-reads, cold cache, trivial Opus |
| `statusline-refresh.sh` | MODIFY | Add RTK per-command savings drill-down |
| `statusline-jira-post.py` | CREATE | Post epic cost summary as Jira comment |
| `README.md` | MODIFY | Add new scripts table |

---

## Task 1: Create `statusline_core.py`

**Files:**
- Create: `statusline_core.py`

This module is the foundation. All other tasks depend on it. It exposes `RATES`, `rate_for()`, `price_tokens()`, `iter_usage_records()`, and `iter_all_lines()`.

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Shared core for Claude Code usage scripts.

Provides RATES, pricing helpers, and transcript scanning utilities imported
by all statusline-*.py scripts. Not intended to be run directly.
"""
import glob
import json
import os
from datetime import datetime, timedelta, date


# Per-million-token rates: input, output, cache-write, cache-read.
# Keys matched as substrings of the model ID — more specific keys first.
RATES = {
    "opus-4-1":        {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "opus-4-20250514": {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "opus":            {"in": 5.0,  "out": 25.0, "cw": 6.25,  "cr": 0.5},
    "fable":           {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    "mythos":          {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    "sonnet":          {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.3},
    "haiku":           {"in": 1.0,  "out": 5.0,  "cw": 1.25,  "cr": 0.1},
}


def rate_for(model):
    if not model:
        return None
    for key, rate in RATES.items():
        if key in model:
            return rate
    return None


def price_tokens(model, inp, out, cw, cr):
    """Return cost in USD for the given token counts and model."""
    rate = rate_for(model)
    if not rate:
        return 0.0
    return (
        inp * rate["in"] + out * rate["out"] + cw * rate["cw"] + cr * rate["cr"]
    ) / 1_000_000


def default_projects_dir():
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def iter_usage_records(projects_dir=None, since_date=None):
    """Yield deduped dicts for every assistant message on or after since_date.

    since_date defaults to the 1st of the current month (MTD).

    Each dict:
        project_key      str   — directory name under ~/.claude/projects/
        timestamp        datetime (local, timezone-aware)
        date             date
        model            str
        input_tokens     int
        output_tokens    int
        cache_write_tokens int
        cache_read_tokens  int
        total_tokens     int
        cost             float  USD
    """
    if projects_dir is None:
        projects_dir = default_projects_dir()
    if since_date is None:
        since_date = datetime.now().astimezone().date().replace(day=1)

    cutoff_epoch = datetime(
        since_date.year, since_date.month, since_date.day,
        tzinfo=datetime.now().astimezone().tzinfo,
    ).timestamp()

    seen = set()
    for path in glob.iglob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff_epoch:
                continue
            project_key = os.path.basename(os.path.dirname(path))
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"usage"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("type") != "assistant":
                        continue
                    msg = obj.get("message") or {}
                    usage = msg.get("usage")
                    ts = obj.get("timestamp")
                    if not usage or not ts:
                        continue
                    mid = msg.get("id")
                    if mid:
                        key = (mid, obj.get("requestId"))
                        if key in seen:
                            continue
                        seen.add(key)
                    try:
                        dt = datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        ).astimezone()
                    except ValueError:
                        continue
                    if dt.date() < since_date:
                        continue
                    inp = usage.get("input_tokens", 0) or 0
                    out = usage.get("output_tokens", 0) or 0
                    cw  = usage.get("cache_creation_input_tokens", 0) or 0
                    cr  = usage.get("cache_read_input_tokens", 0) or 0
                    model = msg.get("model", "")
                    yield {
                        "project_key":        project_key,
                        "timestamp":          dt,
                        "date":               dt.date(),
                        "model":              model,
                        "input_tokens":       inp,
                        "output_tokens":      out,
                        "cache_write_tokens": cw,
                        "cache_read_tokens":  cr,
                        "total_tokens":       inp + out + cw + cr,
                        "cost":               price_tokens(model, inp, out, cw, cr),
                    }
        except (OSError, ValueError):
            continue


def iter_all_lines(projects_dir=None, since_date=None):
    """Yield raw parsed JSON dicts for ALL lines (user + assistant) since since_date.

    Each dict gets an extra 'project_key' field. Used by scripts that need
    user message text (epic attribution, waste detection).
    since_date defaults to 30 days ago.
    """
    if projects_dir is None:
        projects_dir = default_projects_dir()
    if since_date is None:
        since_date = (datetime.now().astimezone() - timedelta(days=30)).date()

    cutoff_epoch = datetime(
        since_date.year, since_date.month, since_date.day,
        tzinfo=datetime.now().astimezone().tzinfo,
    ).timestamp()

    for path in glob.iglob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff_epoch:
                continue
            project_key = os.path.basename(os.path.dirname(path))
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    obj["project_key"] = project_key
                    yield obj
        except (OSError, ValueError):
            continue
```

- [ ] **Step 2: Verify it is importable**

```bash
cd "C:/Users/abhosale/OneDrive - NICE Ltd/Documents/Actions/QA/Repos/UI/claude-usage-status"
python3 -c "import statusline_core; print('RATES keys:', list(statusline_core.RATES.keys()))"
```

Expected output:
```
RATES keys: ['opus-4-1', 'opus-4-20250514', 'opus', 'fable', 'mythos', 'sonnet', 'haiku']
```

---

## Task 2: Refactor `statusline-usage.py` to import from core

**Files:**
- Modify: `statusline-usage.py`

Replace the inline RATES dict, `rate_for()`, and scanning loop with an import from `statusline_core`. Output to cache must be byte-for-byte identical.

- [ ] **Step 1: Note current cache output for comparison**

```bash
python3 ~/.claude/statusline-usage.py ~/.claude/projects ~/.claude/.statusline-usage-cache-before.json
cat ~/.claude/.statusline-usage-cache-before.json
```

Record the output values.

- [ ] **Step 2: Replace statusline-usage.py**

```python
#!/usr/bin/env python3
"""Aggregate Claude Code token usage and cost for "today" and "month-to-date".

Scans transcript JSONL files, prices by model, buckets by local date.
Writes a small cache JSON consumed by statusline.sh.

Usage: statusline-usage.py <projects_dir> <cache_path> [lock_path]
"""
import json
import os
import sys
from datetime import datetime

# Same-directory import — works whether installed to ~/.claude/ or run from repo.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records  # noqa: E402


def main():
    projects_dir = sys.argv[1]
    cache_path   = sys.argv[2]
    lock_path    = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        now_local = datetime.now().astimezone()
        today = now_local.date()
        month_start = today.replace(day=1)

        day_cost = mtd_cost = 0.0
        day_tok  = mtd_tok  = 0

        for rec in iter_usage_records(projects_dir, since_date=month_start):
            mtd_cost += rec["cost"]
            mtd_tok  += rec["total_tokens"]
            if rec["date"] == today:
                day_cost += rec["cost"]
                day_tok  += rec["total_tokens"]

        result = {
            "computed_at": now_local.timestamp(),
            "day_cost":    round(day_cost, 2),
            "day_tokens":  day_tok,
            "mtd_cost":    round(mtd_cost, 2),
            "mtd_tokens":  mtd_tok,
        }
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        os.replace(tmp, cache_path)
    finally:
        if lock_path and os.path.exists(lock_path):
            try:
                os.rmdir(lock_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify output is identical**

```bash
python3 statusline-usage.py ~/.claude/projects ~/.claude/.statusline-usage-cache-after.json
python3 -c "
import json
before = json.load(open('$HOME/.claude/.statusline-usage-cache-before.json'))
after  = json.load(open('$HOME/.claude/.statusline-usage-cache-after.json'))
for k in ['day_cost','day_tokens','mtd_cost','mtd_tokens']:
    match = '✓' if before[k] == after[k] else '✗ MISMATCH'
    print(f'{match}  {k}: {before[k]} == {after[k]}')
"
```

Expected: all four keys show `✓`.

- [ ] **Step 4: Deploy to ~/.claude/ and smoke-test**

```bash
cp statusline_core.py ~/.claude/statusline_core.py
cp statusline-usage.py ~/.claude/statusline-usage.py
python3 ~/.claude/statusline-usage.py ~/.claude/projects ~/.claude/.statusline-usage-cache.json
echo "Exit code: $?"
```

Expected: exit code 0, cache file updated.

---

## Task 3: Create `feature-showcase.html`

**Files:**
- Create: `feature-showcase.html`

Self-contained interactive HTML — all CSS and JS inline, no network requests, no CDN.

- [ ] **Step 1: Create the file**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Usage Status — Feature Roadmap</title>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --surface2: #21262d;
  --border: #30363d; --text: #e6edf3; --dim: #8b949e;
  --cyan: #79c0ff; --green: #3fb950; --yellow: #e3b341;
  --red: #f85149; --purple: #d2a8ff; --orange: #ffa657;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;line-height:1.6}
a{color:var(--cyan);text-decoration:none}

/* Header */
header{padding:60px 40px 40px;text-align:center;border-bottom:1px solid var(--border)}
header h1{font-size:2.2rem;font-weight:700;color:var(--cyan);letter-spacing:-0.5px}
header h1 span{color:var(--text)}
header p{color:var(--dim);margin-top:8px;font-size:1.05rem}
.status-bar{display:inline-block;margin-top:20px;padding:10px 20px;background:var(--surface);border:1px solid var(--border);border-radius:8px;font-family:'Cascadia Code','Fira Code',monospace;font-size:0.82rem;color:var(--dim)}
.status-bar .c{color:var(--cyan)}.status-bar .g{color:var(--green)}.status-bar .y{color:var(--yellow)}

/* Grid */
main{max-width:1200px;margin:0 auto;padding:40px 24px}
.section-title{font-size:0.78rem;font-weight:600;text-transform:uppercase;letter-spacing:1.2px;color:var(--dim);margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:16px}

/* Cards */
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;overflow:hidden;transition:border-color .2s,box-shadow .2s}
.card:hover{border-color:var(--cyan);box-shadow:0 0 0 1px var(--cyan)20}
.card-header{display:flex;align-items:center;gap:14px;padding:18px 20px;cursor:pointer;user-select:none}
.card-header:hover .card-title{color:var(--cyan)}
.badge{width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:0.75rem;font-weight:700;flex-shrink:0}
.card-meta{flex:1}
.card-title{font-size:0.95rem;font-weight:600;font-family:'Cascadia Code','Fira Code',monospace;transition:color .2s}
.card-desc{font-size:0.82rem;color:var(--dim);margin-top:2px}
.impact{font-size:0.7rem;font-weight:600;padding:2px 8px;border-radius:4px;letter-spacing:0.5px}
.impact-high{background:#3fb95020;color:var(--green);border:1px solid #3fb95040}
.impact-med{background:#e3b34120;color:var(--yellow);border:1px solid #e3b34140}
.chevron{color:var(--dim);font-size:0.8rem;transition:transform .25s;flex-shrink:0}
.card.open .chevron{transform:rotate(180deg)}

/* Expanded body */
.card-body{display:none;padding:0 20px 20px}
.card.open .card-body{display:block}
.terminal{background:#0a0d13;border:1px solid var(--border);border-radius:6px;padding:14px 16px;font-family:'Cascadia Code','Fira Code',monospace;font-size:0.78rem;line-height:1.7;overflow-x:auto;margin-bottom:14px}
.terminal .p{color:var(--green)}.terminal .c{color:var(--cyan)}.terminal .y{color:var(--yellow)}
.terminal .g{color:var(--green)}.terminal .r{color:var(--red)}.terminal .d{color:var(--dim)}
.terminal .w{color:var(--text)}.terminal .o{color:var(--orange)}
.how-it-works{font-size:0.82rem;color:var(--dim)}
.how-it-works h4{font-size:0.73rem;text-transform:uppercase;letter-spacing:0.8px;color:var(--dim);margin-bottom:8px;font-weight:600}
.how-it-works ul{list-style:none;display:flex;flex-direction:column;gap:5px}
.how-it-works ul li::before{content:'→ ';color:var(--cyan)}
.savings{margin-top:10px;padding:8px 12px;background:var(--surface2);border-radius:6px;font-size:0.8rem;color:var(--green);border-left:2px solid var(--green)}

/* Roadmap */
.roadmap{margin-top:48px}
.roadmap h2{font-size:1.1rem;font-weight:600;margin-bottom:20px;color:var(--text)}
.road-list{display:flex;flex-wrap:wrap;gap:10px}
.road-item{display:flex;align-items:center;gap:10px;padding:10px 16px;background:var(--surface);border:1px solid var(--border);border-radius:8px;font-size:0.82rem}
.road-num{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:700}
.road-item .label{font-family:'Cascadia Code','Fira Code',monospace}
.road-item .why{color:var(--dim);font-size:0.76rem}

footer{text-align:center;padding:32px;color:var(--dim);font-size:0.8rem;border-top:1px solid var(--border);margin-top:40px}
</style>
</head>
<body>

<header>
  <h1><span>claude-usage-</span>status</h1>
  <p>Feature Roadmap — 8 new scripts for deeper cost visibility &amp; savings</p>
  <div class="status-bar">
    [<span class="c">claude-sonnet-4-6</span>] 📁 claude-usage-status 🌿 <span class="c">main</span>
    <span class="g">██████████</span> 12% ⏱ 3m22s<br>
    &nbsp;&nbsp;<span class="c">Jun 23</span> ┃ sess <span class="y">$0.14</span> ┃ today <span class="y">$4.21</span>·<span class="c">1.4M</span> tok ┃ MTD <span class="y">$89.40</span>·<span class="c">29.8M</span> tok ┃ <span class="g">⚡</span> <span class="c">4.2K</span>/<span class="c">89.1K</span>
  </div>
</header>

<main>
  <p class="section-title">New Features</p>
  <div class="grid" id="grid"></div>

  <div class="roadmap">
    <h2>Build Priority</h2>
    <div class="road-list" id="roadmap"></div>
  </div>
</main>

<footer>All scripts run locally · no data leaves your machine · stdlib only</footer>

<script>
const features = [
  {
    n:1, title:"statusline-epic-report.py",
    desc:"Cost attribution by Jira epic or ticket",
    impact:"high", color:"#79c0ff",
    terminal:`<span class="p">$</span> <span class="w">python statusline-epic-report.py --days 30</span>

<span class="d">  Epic Cost Report · last 30 days</span>
<span class="d">  ─────────────────────────────────────────────────</span>
<span class="d">  Ticket        Cost      Tokens   Sessions  Duration</span>
<span class="d">  ─────────────────────────────────────────────────</span>
  <span class="c">CSA-12345</span>   <span class="y">$4.23</span>    <span class="c">142.3K</span>       3       2h 14m
  <span class="c">CSA-12890</span>   <span class="y">$1.07</span>     <span class="c">38.1K</span>       1         45m
  <span class="c">ILLUM-5678</span>  <span class="y">$0.89</span>     <span class="c">29.4K</span>       2       1h 02m
<span class="d">  ─────────────────────────────────────────────────</span>
  Total       <span class="y">$6.19</span>    <span class="c">209.8K</span>       6       4h 01m`,
    bullets:["Scans all transcript JSONL files for Jira ticket refs (CSA-\\d+, ILLUM-\\d+) in user messages","Also decodes project path and runs git log to detect branch-encoded tickets","Groups cost + tokens by ticket, sorted by spend descending"],
    savings:"Answers \"how much did this sprint cost in Claude?\" instantly"
  },
  {
    n:2, title:"statusline-cache-analyzer.py",
    desc:"Cache hit-rate and dollar savings per project",
    impact:"high", color:"#3fb950",
    terminal:`<span class="p">$</span> <span class="w">python statusline-cache-analyzer.py --days 30</span>

<span class="d">  Cache Efficiency · last 30 days</span>
<span class="d">  ────────────────────────────────────────────────────</span>
<span class="d">  Project                  Hit Rate   Saved     Status</span>
<span class="d">  ────────────────────────────────────────────────────</span>
  <span class="c">illum-webapp-actions</span>     <span class="g">72.4%</span>    <span class="y">$3.41</span>    <span class="g">✓ warm</span>
  <span class="c">autoinsights-playwright</span>  <span class="y">41.2%</span>    <span class="y">$0.89</span>    <span class="y">~ cool</span>
  <span class="c">claude-usage-status</span>       <span class="r">8.1%</span>    <span class="y">$0.12</span>    <span class="r">✗ cold</span>
<span class="d">  ────────────────────────────────────────────────────</span>
  Overall: <span class="g">58.3%</span> hit rate · <span class="y">$4.42</span> saved via cache`,
    bullets:["Aggregates cache_creation and cache_read tokens per project from transcripts","Computes hit rate = cache_read / (cache_read + fresh_input)","Projects at 0% hit rate flagged as cold — likely missing /compact or context reuse"],
    savings:"Often reveals $3–8/month in cache savings already happening — and where to get more"
  },
  {
    n:3, title:"statusline-model-advisor.py",
    desc:"Flag over-qualified Opus sessions, quantify savings",
    impact:"high", color:"#d2a8ff",
    terminal:`<span class="p">$</span> <span class="w">python statusline-model-advisor.py --days 30</span>

<span class="d">  Model Advisor · last 30 days</span>
<span class="d">  ─────────────────────────────────────────────────────</span>
  <span class="y">12 sessions</span> used Opus where Sonnet likely sufficed:

  <span class="d">2026-06-18</span>  <span class="c">illum-webapp</span>   412 out tok  <span class="r">$0.08</span> → <span class="g">$0.02</span>
  <span class="d">2026-06-19</span>  <span class="c">autoinsights</span>   389 out tok  <span class="r">$0.07</span> → <span class="g">$0.01</span>
  <span class="d">... (10 more)</span>
<span class="d">  ─────────────────────────────────────────────────────</span>
  Potential monthly savings: <span class="g">$0.72</span>
  <span class="d">Tip: use claude-sonnet-4-6 for short Q&amp;A &lt;5K tokens</span>`,
    bullets:["Identifies sessions where Opus was used but output < 500 tokens AND input < 5K tokens","Calculates what those sessions would have cost on Sonnet","Surfaces the saving without guessing — only flags sessions that fit the low-complexity profile"],
    savings:"Average user saves $0.40–1.20/month by moving trivial queries to Sonnet"
  },
  {
    n:4, title:"statusline-dashboard.py",
    desc:"Generate self-contained HTML spend dashboard",
    impact:"high", color:"#ffa657",
    terminal:`<span class="p">$</span> <span class="w">python statusline-dashboard.py --days 30</span>

  Generating <span class="c">claude-dashboard.html</span>...
  <span class="g">✓</span> 30-day daily spend chart <span class="d">(inline SVG)</span>
  <span class="g">✓</span> Model mix donut <span class="d">(inline SVG)</span>
  <span class="g">✓</span> Top 10 projects by cost
  <span class="g">✓</span> Cache savings summary
  <span class="g">✓</span> MTD vs prior month comparison
  <span class="d">──────────────────────────────</span>
  Saved: <span class="c">claude-dashboard.html</span> <span class="d">(42 KB)</span>
  <span class="d">Open in any browser — no network needed</span>`,
    bullets:["Reads your real usage cache and transcript data to produce a dated snapshot","All charts are inline SVG — no Chart.js, no CDN, opens offline","Shareable with managers or finance without exposing raw data"],
    savings:"One command for a shareable monthly spend report — no spreadsheet needed"
  },
  {
    n:5, title:"statusline-budget-check.sh",
    desc:"Budget gate — exits non-zero when over threshold",
    impact:"med", color:"#e3b341",
    terminal:`<span class="p">$</span> <span class="w">cat .claude-budget.json</span>
<span class="d">{ "daily": 20, "mtd": 200, "epic": { "CSA-12345": 50 } }</span>

<span class="p">$</span> <span class="w">statusline-budget-check.sh</span>

  Budget Check · 2026-06-23
  <span class="d">──────────────────────────────────────────────────</span>
  Daily  <span class="y">$8.41</span> / <span class="c">$20.00</span>  <span class="g">████████░░</span>  42%  <span class="g">✓ OK</span>
  MTD   <span class="y">$89.20</span> / <span class="c">$200.00</span>  <span class="y">████████░░</span>  44%  <span class="y">⚠ watch</span>

<span class="p">$</span> <span class="w">statusline-budget-check.sh --epic CSA-12345</span>
  CSA-12345  <span class="y">$4.23</span> / <span class="c">$50.00</span>  <span class="g">███░░░░░░░</span>   8%  <span class="g">✓ OK</span>`,
    bullets:["Reads .claude-budget.json from current dir or ~/.claude/","Exits 0 = OK, 1 = warning (>80%), 2 = over budget — wire into Claude Code Stop hook","Supports daily, MTD, and per-epic budget thresholds in one config file"],
    savings:"Prevents bill shock before it hits — works as a pre-commit hook or Claude Code stop hook"
  },
  {
    n:6, title:"statusline-waste-detector.py",
    desc:"Detect file re-reads, cold-cache sessions, trivial Opus use",
    impact:"med", color:"#f85149",
    terminal:`<span class="p">$</span> <span class="w">python statusline-waste-detector.py --days 7</span>

<span class="d">  Waste Report · last 7 days</span>
<span class="d">  ──────────────────────────────────────────────────</span>
  <span class="y">File re-reads</span> (5+ times per session):
  <span class="d">  reducers.ts</span>  read <span class="r">8×</span> in one session  <span class="r">~$0.14</span> waste

  <span class="y">Cold cache sessions</span> (&gt;10 turns, 0 cache reads):
  <span class="d">  illum-webapp 2026-06-21 14:32</span>  18 turns  <span class="r">~$0.32</span> waste

  <span class="y">Trivial Opus</span> (output &lt;200 tok):
  <span class="d">  3 sessions  could have used Sonnet</span>  <span class="r">~$0.18</span> saved
<span class="d">  ──────────────────────────────────────────────────</span>
  Estimated weekly waste: <span class="r">$0.64</span>`,
    bullets:["Scans user message text for repeated file path strings within a session","Detects conversations with 10+ turns but zero cache_read_input_tokens","Flags Opus sessions with fewer than 200 output tokens as over-qualified"],
    savings:"Identifies behavioural patterns that quietly compound into $3–5/month waste"
  },
  {
    n:7, title:"statusline-refresh.sh (extended)",
    desc:"RTK per-command savings drill-down",
    impact:"med", color:"#79c0ff",
    terminal:`<span class="p">$</span> <span class="w">cat ~/.claude/.statusline-rtk-detail-cache.json</span>
{
  <span class="c">"total_saved_usd"</span>: <span class="y">"4.21"</span>,
  <span class="c">"top_command"</span>: <span class="g">"git"</span>,
  <span class="c">"by_command"</span>: {
    <span class="c">"git"</span>:  { <span class="c">"calls"</span>: <span class="y">234</span>, <span class="c">"saved_tokens"</span>: <span class="y">"89.2K"</span>, <span class="c">"saved_usd"</span>: <span class="y">"2.18"</span> },
    <span class="c">"npm"</span>:  { <span class="c">"calls"</span>:  <span class="y">41</span>, <span class="c">"saved_tokens"</span>: <span class="y">"28.1K"</span>, <span class="c">"saved_usd"</span>: <span class="y">"0.97"</span> },
    <span class="c">"find"</span>: { <span class="c">"calls"</span>:  <span class="y">89</span>, <span class="c">"saved_tokens"</span>: <span class="y">"19.4K"</span>, <span class="c">"saved_usd"</span>: <span class="y">"0.61"</span> }
  }
}`,
    bullets:["Extends the existing rtk integration in statusline-refresh.sh","Parses rtk gain --history output to build per-command-type savings breakdown","Caches to .statusline-rtk-detail-cache.json alongside the existing rtk cache"],
    savings:"Shows which commands save the most — git typically accounts for 50%+ of RTK savings"
  },
  {
    n:8, title:"statusline-jira-post.py",
    desc:"Post epic cost summary as a Jira comment",
    impact:"med", color:"#3fb950",
    terminal:`<span class="p">$</span> <span class="w">python statusline-jira-post.py --epic CSA-12345 --dry-run</span>

  Epic Cost Summary — <span class="c">CSA-12345</span>
  <span class="d">──────────────────────────────────────────────────</span>
  Period:    <span class="c">2026-06-01</span> → <span class="c">2026-06-23</span>
  Sessions:  <span class="y">3</span>
  Tokens:    <span class="c">142,341</span>
  Cost:      <span class="y">$4.23</span>
  Models:    <span class="c">claude-sonnet-4-6</span> (89%)  <span class="c">claude-opus-4-7</span> (11%)
  <span class="d">──────────────────────────────────────────────────</span>
  <span class="d">[dry-run] Would post to CSA-12345</span>
  <span class="d">Run with --post to submit</span>`,
    bullets:["Reuses epic-report logic to attribute sessions to the given ticket ID","Formats a clean summary: date range, sessions, tokens, cost, model mix","Requires JIRA_URL + JIRA_EMAIL + JIRA_API_TOKEN env vars; --dry-run always safe"],
    savings:"One command to attach AI cost data to sprint epics for retros and capacity planning"
  }
];

const roadmap = [
  {n:1,label:"statusline_core.py",why:"foundation",color:"#79c0ff"},
  {n:2,label:"statusline-usage.py refactor",why:"backward compat",color:"#3fb950"},
  {n:3,label:"statusline-epic-report.py",why:"highest value",color:"#79c0ff"},
  {n:4,label:"statusline-cache-analyzer.py",why:"low effort",color:"#3fb950"},
  {n:5,label:"statusline-model-advisor.py",why:"quick wins",color:"#d2a8ff"},
  {n:6,label:"statusline-dashboard.py",why:"visibility",color:"#ffa657"},
  {n:7,label:"statusline-budget-check.sh",why:"control",color:"#e3b341"},
  {n:8,label:"statusline-waste-detector.py",why:"behavioural",color:"#f85149"},
  {n:9,label:"statusline-refresh.sh ext.",why:"extends RTK",color:"#79c0ff"},
  {n:10,label:"statusline-jira-post.py",why:"opt-in only",color:"#3fb950"},
];

const grid = document.getElementById('grid');
features.forEach(f => {
  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = `
    <div class="card-header" onclick="this.parentElement.classList.toggle('open')">
      <div class="badge" style="background:${f.color}20;color:${f.color};border:1px solid ${f.color}40">${f.n}</div>
      <div class="card-meta">
        <div class="card-title">${f.title}</div>
        <div class="card-desc">${f.desc}</div>
      </div>
      <span class="impact impact-${f.impact==='high'?'high':'med'}">${f.impact.toUpperCase()}</span>
      <span class="chevron">▼</span>
    </div>
    <div class="card-body">
      <div class="terminal">${f.terminal}</div>
      <div class="how-it-works">
        <h4>How it works</h4>
        <ul>${f.bullets.map(b=>`<li>${b}</li>`).join('')}</ul>
      </div>
      <div class="savings">💡 ${f.savings}</div>
    </div>`;
  grid.appendChild(card);
});

const rl = document.getElementById('roadmap');
roadmap.forEach(r => {
  const el = document.createElement('div');
  el.className = 'road-item';
  el.innerHTML = `<div class="road-num" style="background:${r.color}20;color:${r.color}">${r.n}</div><span class="label">${r.label}</span><span class="why">— ${r.why}</span>`;
  rl.appendChild(el);
});
</script>
</body>
</html>
```

- [ ] **Step 2: Verify it opens cleanly in a browser**

Open `feature-showcase.html` in any browser. Verify:
- 8 cards render in a 2-column grid
- Clicking a card expands its terminal block and bullets
- No console errors
- No network requests in the Network tab

---

## Task 4: Create `statusline-epic-report.py`

**Files:**
- Create: `statusline-epic-report.py`

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Report Claude Code costs attributed to Jira epics/tickets.

Scans user message content for Jira ticket refs (CSA-NNNN, ILLUM-NNNN) and
groups assistant-message costs by ticket.

Usage: python statusline-epic-report.py [--days 30] [--json] [--min-cost 0.10]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_all_lines, iter_usage_records, default_projects_dir

TICKET_RE = re.compile(r'\b([A-Z]{2,8}-\d+)\b')


def get_branch_tickets(project_key, projects_dir):
    """Try to extract ticket refs from the git branch of the project directory."""
    # Project key is a path-encoded dir name (e.g. C--Users-foo-Documents-myproject).
    # Attempt to reverse-decode to find the actual path and read the current branch.
    tickets = set()
    try:
        # Claude Code encodes paths by replacing path separators with --
        # Try common decode strategies
        candidate = project_key.replace('--', os.sep)
        if os.path.isdir(candidate):
            result = subprocess.run(
                ['git', '-C', candidate, 'branch', '--show-current'],
                capture_output=True, text=True, timeout=3
            )
            branch = result.stdout.strip()
            tickets.update(TICKET_RE.findall(branch.upper()))
    except Exception:
        pass
    return tickets


def main():
    parser = argparse.ArgumentParser(description='Epic cost report')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--json', action='store_true', dest='as_json')
    parser.add_argument('--min-cost', type=float, default=0.01)
    args = parser.parse_args()

    since = (datetime.now().astimezone() - timedelta(days=args.days)).date()
    projects_dir = default_projects_dir()

    # Build a map: conversation_id (requestId) → set of ticket refs
    conv_tickets = defaultdict(set)
    for line in iter_all_lines(projects_dir, since_date=since):
        if line.get("type") != "user":
            continue
        msg = line.get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        tickets = TICKET_RE.findall(str(content).upper())
        if tickets:
            conv_id = line.get("requestId") or line.get("project_key", "")
            conv_tickets[conv_id].update(tickets)
        # Also check branch
        branch_tickets = get_branch_tickets(line.get("project_key", ""), projects_dir)
        if branch_tickets:
            conv_tickets[line.get("project_key", "")].update(branch_tickets)

    # Aggregate cost per ticket
    ticket_stats = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "sessions": set()})
    untagged = {"cost": 0.0, "tokens": 0}

    for rec in iter_usage_records(projects_dir, since_date=since):
        conv_id = rec.get("requestId", rec["project_key"])
        # Find tickets from any matching conv_id or project_key
        tickets = conv_tickets.get(conv_id, set()) | conv_tickets.get(rec["project_key"], set())
        if tickets:
            for t in tickets:
                ticket_stats[t]["cost"]    += rec["cost"]
                ticket_stats[t]["tokens"]  += rec["total_tokens"]
                ticket_stats[t]["sessions"].add(rec["project_key"] + str(rec["date"]))
        else:
            untagged["cost"]   += rec["cost"]
            untagged["tokens"] += rec["total_tokens"]

    rows = sorted(ticket_stats.items(), key=lambda x: x[1]["cost"], reverse=True)
    rows = [(t, s) for t, s in rows if s["cost"] >= args.min_cost]

    if args.as_json:
        out = [{"ticket": t, "cost": round(s["cost"], 4),
                "tokens": s["tokens"], "sessions": len(s["sessions"])} for t, s in rows]
        print(json.dumps(out, indent=2))
        return

    print(f"\n  Epic Cost Report · last {args.days} days")
    print(f"  {'─'*51}")
    print(f"  {'Ticket':<14} {'Cost':>8}  {'Tokens':>9}  {'Sessions':>8}")
    print(f"  {'─'*51}")
    for ticket, s in rows:
        tokens_fmt = f"{s['tokens']/1000:.1f}K" if s['tokens'] >= 1000 else str(s['tokens'])
        print(f"  {ticket:<14} ${s['cost']:>7.2f}  {tokens_fmt:>9}  {len(s['sessions']):>8}")
    print(f"  {'─'*51}")
    total_cost   = sum(s["cost"] for _, s in rows)
    total_tokens = sum(s["tokens"] for _, s in rows)
    total_sess   = sum(len(s["sessions"]) for _, s in rows)
    tf = f"{total_tokens/1000:.1f}K" if total_tokens >= 1000 else str(total_tokens)
    print(f"  {'Total':<14} ${total_cost:>7.2f}  {tf:>9}  {total_sess:>8}")
    if untagged["cost"] > 0.01:
        uf = f"{untagged['tokens']/1000:.1f}K"
        print(f"\n  Untagged sessions: ${untagged['cost']:.2f} · {uf} tokens")
    print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test**

```bash
python3 statusline-epic-report.py --days 30
```

Expected: a table with at least headers, no Python errors. If no tickets found, untagged line shows.

---

## Task 5: Create `statusline-cache-analyzer.py`

**Files:**
- Create: `statusline-cache-analyzer.py`

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Report cache hit rates and dollar savings per project.

Usage: python statusline-cache-analyzer.py [--days 30] [--project KEY]
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, price_tokens, default_projects_dir


def main():
    parser = argparse.ArgumentParser(description='Cache efficiency report')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--project', default=None, help='Filter by project key substring')
    args = parser.parse_args()

    since = (datetime.now().astimezone() - timedelta(days=args.days)).date()
    projects_dir = default_projects_dir()

    stats = defaultdict(lambda: {
        "input": 0, "cache_write": 0, "cache_read": 0,
        "cost_actual": 0.0, "cost_no_cache": 0.0,
    })

    for rec in iter_usage_records(projects_dir, since_date=since):
        key = rec["project_key"]
        if args.project and args.project.lower() not in key.lower():
            continue
        s = stats[key]
        s["input"]       += rec["input_tokens"]
        s["cache_write"] += rec["cache_write_tokens"]
        s["cache_read"]  += rec["cache_read_tokens"]
        s["cost_actual"] += rec["cost"]
        # What it would have cost with no cache (all cache_read becomes fresh input)
        s["cost_no_cache"] += price_tokens(
            rec["model"],
            rec["input_tokens"] + rec["cache_read_tokens"],
            rec["output_tokens"], 0, 0,
        )

    if not stats:
        print("No usage data found.")
        return

    rows = sorted(stats.items(), key=lambda x: x[1]["cost_actual"], reverse=True)

    print(f"\n  Cache Efficiency · last {args.days} days")
    print(f"  {'─'*60}")
    print(f"  {'Project':<28} {'Hit Rate':>9}  {'Saved':>8}  {'Status':>8}")
    print(f"  {'─'*60}")
    total_saved = 0.0
    total_read = total_fresh = 0
    for key, s in rows:
        fresh  = s["input"]
        reads  = s["cache_read"]
        denom  = fresh + reads
        rate   = reads / denom if denom else 0.0
        saved  = s["cost_no_cache"] - s["cost_actual"]
        total_saved  += saved
        total_read   += reads
        total_fresh  += fresh
        rate_pct = f"{rate*100:.1f}%"
        if rate >= 0.5:
            status = "\033[32m✓ warm\033[0m"
        elif rate >= 0.2:
            status = "\033[33m~ cool\033[0m"
        else:
            status = "\033[31m✗ cold\033[0m"
        label = key[:26] + "…" if len(key) > 27 else key
        print(f"  {label:<28} {rate_pct:>9}  ${saved:>7.2f}  {status}")
    print(f"  {'─'*60}")
    overall_denom = total_read + total_fresh
    overall_rate  = total_read / overall_denom if overall_denom else 0.0
    print(f"  Overall: {overall_rate*100:.1f}% hit rate · ${total_saved:.2f} saved via cache\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test**

```bash
python3 statusline-cache-analyzer.py --days 30
```

Expected: table with project keys, hit rates, and saved amounts. No Python errors.

---

## Task 6: Create `statusline-model-advisor.py`

**Files:**
- Create: `statusline-model-advisor.py`

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Identify Opus sessions that Sonnet could have handled, estimate savings.

Usage: python statusline-model-advisor.py [--days 30] [--threshold-tokens 5000]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, price_tokens, default_projects_dir

SONNET_MODEL = "claude-sonnet-4-6"


def main():
    parser = argparse.ArgumentParser(description='Model advisor')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--threshold-tokens', type=int, default=5000,
                        help='Max input tokens to flag as over-qualified on Opus')
    args = parser.parse_args()

    since = (datetime.now().astimezone() - timedelta(days=args.days)).date()
    projects_dir = default_projects_dir()

    candidates = []
    total_actual = total_sonnet = 0.0

    for rec in iter_usage_records(projects_dir, since_date=since):
        if "opus" not in rec["model"].lower():
            continue
        # Flag: small input, small output — hallmarks of a trivial task
        if rec["input_tokens"] <= args.threshold_tokens and rec["output_tokens"] <= 500:
            sonnet_cost = price_tokens(
                SONNET_MODEL,
                rec["input_tokens"], rec["output_tokens"],
                rec["cache_write_tokens"], rec["cache_read_tokens"],
            )
            candidates.append({
                "date":    rec["date"],
                "project": rec["project_key"][:22],
                "out_tok": rec["output_tokens"],
                "actual":  rec["cost"],
                "sonnet":  sonnet_cost,
            })
            total_actual += rec["cost"]
            total_sonnet += sonnet_cost

    print(f"\n  Model Advisor · last {args.days} days")
    if not candidates:
        print("  No over-qualified Opus sessions found. Good.\n")
        return

    print(f"  {len(candidates)} Opus sessions suited to Sonnet:\n")
    print(f"  {'Date':<12} {'Project':<24} {'Out tok':>8}  {'Actual':>8}  {'Sonnet':>8}")
    print(f"  {'─'*64}")
    for c in candidates[:15]:
        print(f"  {str(c['date']):<12} {c['project']:<24} {c['out_tok']:>8}  "
              f"${c['actual']:>7.4f}  ${c['sonnet']:>7.4f}")
    if len(candidates) > 15:
        print(f"  ... ({len(candidates)-15} more)")
    print(f"  {'─'*64}")
    saved = total_actual - total_sonnet
    print(f"  Potential savings: ${saved:.2f} (was ${total_actual:.2f}, "
          f"Sonnet would be ${total_sonnet:.2f})")
    print(f"  Tip: use {SONNET_MODEL} for short Q&A and quick edits\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test**

```bash
python3 statusline-model-advisor.py --days 30
```

Expected: either a table of flagged sessions, or "No over-qualified Opus sessions found." No errors.

---

## Task 7: Create `statusline-dashboard.py`

**Files:**
- Create: `statusline-dashboard.py`

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Generate a self-contained HTML spend dashboard with inline SVG charts.

Usage: python statusline-dashboard.py [--days 30] [--output claude-dashboard.html]
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, default_projects_dir


def bar_chart_svg(daily_data, width=700, height=140):
    """Return an inline SVG bar chart for daily spend."""
    if not daily_data:
        return "<svg></svg>"
    max_val = max(v for _, v in daily_data) or 1
    n = len(daily_data)
    bar_w = max(2, (width - 40) // n - 2)
    bars = []
    for i, (d, v) in enumerate(daily_data):
        x = 20 + i * ((width - 40) // n)
        bh = int((v / max_val) * (height - 20))
        y = height - 10 - bh
        color = "#e3b341" if v == max_val else "#79c0ff"
        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bh}" '
            f'fill="{color}" rx="1" opacity="0.85">'
            f'<title>{d}: ${v:.2f}</title></rect>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:{height}px">'
        + "".join(bars) +
        f'<line x1="20" y1="{height-10}" x2="{width-20}" y2="{height-10}" '
        f'stroke="#30363d" stroke-width="1"/></svg>'
    )


def donut_svg(slices, size=120):
    """Return an inline SVG donut chart. slices = [(label, value, color)]."""
    total = sum(v for _, v, _ in slices) or 1
    cx = cy = size // 2
    r = size // 2 - 10
    parts = []
    start = -90.0
    for label, val, color in slices:
        angle = (val / total) * 360
        end = start + angle
        # Convert to radians
        import math
        x1 = cx + r * math.cos(math.radians(start))
        y1 = cy + r * math.sin(math.radians(start))
        x2 = cx + r * math.cos(math.radians(end))
        y2 = cy + r * math.sin(math.radians(end))
        large = 1 if angle > 180 else 0
        parts.append(
            f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large},1 '
            f'{x2:.1f},{y2:.1f} Z" fill="{color}" opacity="0.85">'
            f'<title>{label}: {val/total*100:.1f}%</title></path>'
        )
        start = end
    # Donut hole
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r//2}" fill="#0d1117"/>')
    return (
        f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:{size}px;height:{size}px">' + "".join(parts) + "</svg>"
    )


def main():
    parser = argparse.ArgumentParser(description='Generate HTML spend dashboard')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--output', default='claude-dashboard.html')
    args = parser.parse_args()

    since = (datetime.now().astimezone() - timedelta(days=args.days)).date()
    projects_dir = default_projects_dir()

    daily = defaultdict(float)
    model_cost = defaultdict(float)
    project_cost = defaultdict(float)
    project_tokens = defaultdict(int)
    cache_read_tok = cache_fresh_tok = 0
    total_cost = total_tokens = 0

    for rec in iter_usage_records(projects_dir, since_date=since):
        daily[str(rec["date"])]          += rec["cost"]
        short_model = rec["model"].split("-20")[0] if "-20" in rec["model"] else rec["model"]
        model_cost[short_model]          += rec["cost"]
        project_cost[rec["project_key"]] += rec["cost"]
        project_tokens[rec["project_key"]] += rec["total_tokens"]
        cache_read_tok  += rec["cache_read_tokens"]
        cache_fresh_tok += rec["input_tokens"]
        total_cost   += rec["cost"]
        total_tokens += rec["total_tokens"]

    # Fill missing days
    all_days = []
    cur = since
    today = datetime.now().astimezone().date()
    while cur <= today:
        all_days.append((str(cur), daily.get(str(cur), 0.0)))
        cur = date.fromordinal(cur.toordinal() + 1)

    bar_svg   = bar_chart_svg(all_days)
    model_colors = ["#79c0ff","#3fb950","#d2a8ff","#ffa657","#f85149","#e3b341"]
    model_slices = [(m, c, model_colors[i % len(model_colors)])
                    for i, (m, c) in enumerate(
                        sorted(model_cost.items(), key=lambda x: -x[1]))]
    donut_svg_str = donut_svg(model_slices)

    top_projects = sorted(project_cost.items(), key=lambda x: -x[1])[:10]
    proj_rows = "".join(
        f"<tr><td>{k[:40]}</td><td>${v:.2f}</td>"
        f"<td>{project_tokens[k]//1000}K</td></tr>"
        for k, v in top_projects
    )

    hit_rate = cache_read_tok / (cache_read_tok + cache_fresh_tok) if (cache_read_tok + cache_fresh_tok) else 0
    legend_html = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:0.8rem">'
        f'<span style="width:10px;height:10px;border-radius:2px;background:{c}"></span>{m}</span>'
        for m, _, c in model_slices
    )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Usage Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;padding:24px}}
h1{{font-size:1.4rem;color:#79c0ff;margin-bottom:4px}}
.sub{{color:#8b949e;font-size:0.85rem;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;margin-bottom:24px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px}}
.card h2{{font-size:0.78rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e;margin-bottom:12px}}
.big{{font-size:2rem;font-weight:700;color:#e3b341}}
.small{{font-size:0.82rem;color:#8b949e;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:0.82rem}}
th{{text-align:left;color:#8b949e;padding:6px 8px;border-bottom:1px solid #30363d;font-weight:500}}
td{{padding:6px 8px;border-bottom:1px solid #21262d}}
td:nth-child(2),th:nth-child(2){{color:#e3b341;text-align:right}}
td:nth-child(3),th:nth-child(3){{color:#79c0ff;text-align:right}}
.donut-row{{display:flex;align-items:center;gap:20px}}
</style>
</head><body>
<h1>Claude Usage Dashboard</h1>
<p class="sub">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · last {args.days} days</p>
<div class="grid">
  <div class="card"><h2>Total Spend</h2><div class="big">${total_cost:.2f}</div><div class="small">last {args.days} days</div></div>
  <div class="card"><h2>Total Tokens</h2><div class="big">{total_tokens//1000:,}K</div><div class="small">input + output + cache</div></div>
  <div class="card"><h2>Cache Hit Rate</h2><div class="big">{hit_rate*100:.1f}%</div><div class="small">{cache_read_tok//1000}K cached reads</div></div>
  <div class="card"><h2>Active Projects</h2><div class="big">{len(project_cost)}</div><div class="small">with usage this period</div></div>
</div>
<div class="card" style="margin-bottom:16px"><h2>Daily Spend — last {args.days} days</h2>{bar_svg}</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
  <div class="card">
    <h2>Model Mix</h2>
    <div class="donut-row">{donut_svg_str}<div>{legend_html}</div></div>
  </div>
  <div class="card">
    <h2>Top Projects</h2>
    <table><tr><th>Project</th><th>Cost</th><th>Tokens</th></tr>{proj_rows}</table>
  </div>
</div>
</body></html>"""

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved: {args.output}")
    size_kb = os.path.getsize(args.output) // 1024
    print(f"  Size: {size_kb} KB — open in any browser\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test**

```bash
python3 statusline-dashboard.py --days 30 --output /tmp/test-dashboard.html
```

Expected: "Saved: /tmp/test-dashboard.html" printed, file exists. Open in browser to verify 4 stat cards and bar chart render.

---

## Task 8: Create `statusline-budget-check.sh` and `.claude-budget.json.example`

**Files:**
- Create: `statusline-budget-check.sh`
- Create: `.claude-budget.json.example`

- [ ] **Step 1: Create `.claude-budget.json.example`**

```json
{
  "daily": 20.00,
  "mtd": 200.00,
  "epic": {
    "CSA-12345": 50.00,
    "ILLUM-5678": 30.00
  }
}
```

- [ ] **Step 2: Create `statusline-budget-check.sh`**

```bash
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

# Locate budget config
BUDGET_FILE=""
[ -f ".claude-budget.json" ]        && BUDGET_FILE=".claude-budget.json"
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

result=$(python3 - <<PYEOF
import json, sys, os

budget = json.load(open(r'$BUDGET_WIN'))
cache_file = r'$USAGE_CACHE_WIN'
if not os.path.exists(cache_file):
    print("NO_CACHE")
    sys.exit(0)
cache = json.load(open(cache_file))

day_cost = cache.get('day_cost', 0)
mtd_cost = cache.get('mtd_cost', 0)
daily_limit = budget.get('daily', 0)
mtd_limit   = budget.get('mtd', 0)

exit_code = 0
lines = []
lines.append(f"  Budget Check · {__import__('datetime').date.today()}")
lines.append(f"  {'─'*50}")

def bar(pct):
    filled = int(pct / 10)
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

epic_id = '$EPIC_ID'
if epic_id:
    epics = budget.get('epic', {})
    limit = epics.get(epic_id)
    if limit:
        import subprocess, sys as _sys
        script = os.path.join(os.path.dirname(os.path.abspath('$0')), 'statusline-epic-report.py')
        r = subprocess.run([_sys.executable, script, '--json', '--days', '90'],
                           capture_output=True, text=True)
        epic_data = json.loads(r.stdout or '[]')
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
```

- [ ] **Step 3: Make script executable**

```bash
chmod +x statusline-budget-check.sh
```

- [ ] **Step 4: Smoke test**

```bash
cp .claude-budget.json.example /tmp/.claude-budget-test.json
cd /tmp && bash "C:/Users/abhosale/OneDrive - NICE Ltd/Documents/Actions/QA/Repos/UI/claude-usage-status/statusline-budget-check.sh"
```

Expected: budget table printed, exit code 0 or 1. No Python errors.

---

## Task 9: Create `statusline-waste-detector.py`

**Files:**
- Create: `statusline-waste-detector.py`

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Detect expensive anti-patterns in Claude Code session transcripts.

Checks for: file re-reads (same path 5+ times in one session),
cold-cache sessions (>10 turns, 0 cache reads), trivial Opus use.

Usage: python statusline-waste-detector.py [--days 7] [--json]
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_all_lines, iter_usage_records, price_tokens, default_projects_dir

FILE_PATH_RE = re.compile(r'[\w./\\-]{6,}\.(?:ts|tsx|js|py|json|sh|md|html|css|yaml|yml)\b')
SONNET_MODEL = "claude-sonnet-4-6"


def main():
    parser = argparse.ArgumentParser(description='Waste detector')
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()

    since = (datetime.now().astimezone() - timedelta(days=args.days)).date()
    projects_dir = default_projects_dir()

    # --- Pass 1: file re-reads per (project_key, date) session ---
    session_file_counts = defaultdict(lambda: defaultdict(int))
    session_turn_counts = defaultdict(int)
    for line in iter_all_lines(projects_dir, since_date=since):
        msg = line.get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text","") for c in content if isinstance(c,dict))
        ts = line.get("timestamp","")
        try:
            d = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone().date()
        except Exception:
            d = since
        sess_key = (line["project_key"], str(d))
        if line.get("type") == "user":
            session_turn_counts[sess_key] += 1
            for fp in FILE_PATH_RE.findall(str(content)):
                session_file_counts[sess_key][fp] += 1

    rereads = []
    for sess, files in session_file_counts.items():
        for fp, count in files.items():
            if count >= 5:
                rereads.append({"session": sess, "file": fp, "count": count})

    # --- Pass 2: cold-cache sessions ---
    session_cache = defaultdict(int)
    session_cost = defaultdict(float)
    for rec in iter_usage_records(projects_dir, since_date=since):
        sess_key = (rec["project_key"], str(rec["date"]))
        session_cache[sess_key] += rec["cache_read_tokens"]
        session_cost[sess_key]  += rec["cost"]

    cold_sessions = [
        {"session": k, "turns": session_turn_counts[k], "cost": session_cost[k]}
        for k, turns in session_turn_counts.items()
        if turns > 10 and session_cache.get(k, 0) == 0
    ]

    # --- Pass 3: trivial Opus ---
    trivial_opus = []
    opus_waste = 0.0
    for rec in iter_usage_records(projects_dir, since_date=since):
        if "opus" not in rec["model"].lower():
            continue
        if rec["output_tokens"] < 200:
            sonnet_cost = price_tokens(
                SONNET_MODEL,
                rec["input_tokens"], rec["output_tokens"],
                rec["cache_write_tokens"], rec["cache_read_tokens"],
            )
            trivial_opus.append(rec)
            opus_waste += rec["cost"] - sonnet_cost

    total_waste = (
        sum(0.02 * r["count"] for r in rereads) +
        sum(s["cost"] * 0.3 for s in cold_sessions) +
        opus_waste
    )

    if args.as_json:
        print(json.dumps({
            "rereads": [{"file": r["file"], "count": r["count"],
                         "session": list(r["session"])} for r in rereads],
            "cold_sessions": [{"session": list(s["session"]), "turns": s["turns"],
                               "cost": round(s["cost"],4)} for s in cold_sessions],
            "trivial_opus": len(trivial_opus),
            "opus_waste_usd": round(opus_waste, 4),
            "estimated_total_waste_usd": round(total_waste, 2),
        }, indent=2))
        return

    print(f"\n  Waste Report · last {args.days} days")
    print(f"  {'─'*50}")

    if rereads:
        print(f"\n  File re-reads (5+ times in one session):")
        for r in sorted(rereads, key=lambda x: -x["count"])[:5]:
            fname = os.path.basename(r["file"])
            print(f"    {fname:<30} read {r['count']}× in {r['session'][0]}")
    else:
        print("\n  File re-reads: none detected ✓")

    if cold_sessions:
        print(f"\n  Cold-cache sessions (>10 turns, 0 cache reads):")
        for s in sorted(cold_sessions, key=lambda x: -x["cost"])[:5]:
            print(f"    {s['session'][0][:30]}  {s['turns']} turns  ${s['cost']:.2f}")
    else:
        print("\n  Cold-cache sessions: none ✓")

    if trivial_opus:
        print(f"\n  Trivial Opus sessions (output <200 tokens):")
        print(f"    {len(trivial_opus)} sessions — could have used Sonnet  ~${opus_waste:.2f} saved")
    else:
        print("\n  Trivial Opus: none ✓")

    print(f"\n  {'─'*50}")
    print(f"  Estimated waste this period: ${total_waste:.2f}\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test**

```bash
python3 statusline-waste-detector.py --days 7
```

Expected: waste report printed with at least one section. No Python errors.

---

## Task 10: Extend `statusline-refresh.sh` with RTK drill-down

**Files:**
- Modify: `statusline-refresh.sh`

- [ ] **Step 1: Replace `statusline-refresh.sh`**

```bash
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
    printf '%s\n' "$HISTORY" | python3 - "$RTK_DETAIL_CACHE" << 'PYEOF'
import sys, json, re
from collections import defaultdict

out_path = sys.argv[1]
text = sys.stdin.read()
by_cmd = defaultdict(lambda: {"calls": 0, "saved_tokens": 0, "saved_usd": 0.0})

# Parse lines like: "2026-06-23  git status  saved 1234 tokens ($0.00)"
pattern = re.compile(r'\b(\w+)\b.*?saved\s+([\d,]+)\s+tokens.*?\$([0-9.]+)', re.IGNORECASE)
for m in pattern.finditer(text):
    cmd = m.group(1).lower()
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
  fi
fi

rmdir "$LOCK" 2>/dev/null
```

- [ ] **Step 2: Smoke test**

```bash
bash statusline-refresh.sh \
  ~/.claude/.statusline-usage-cache.json \
  ~/.claude/.statusline-rtk-cache.json \
  /tmp/test-lock \
  ~/.claude/projects
echo "Exit: $?"
```

Expected: exit 0, usage cache updated. If RTK installed, detail cache also updated.

---

## Task 11: Create `statusline-jira-post.py`

**Files:**
- Create: `statusline-jira-post.py`

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Post a Claude Code cost summary for a Jira epic as a comment.

Requires env vars: JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN
Or a config file at ~/.claude/.jira-config with the same keys.

Usage: python statusline-jira-post.py --epic CSA-12345 [--days 90] [--post]
       --post is required to actually submit; default is dry-run.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, default_projects_dir

TICKET_RE = __import__('re').compile(r'\b([A-Z]{2,8}-\d+)\b')


def load_jira_config():
    cfg = {}
    cfg_file = os.path.join(os.path.expanduser("~"), ".claude", ".jira-config")
    if os.path.exists(cfg_file):
        for line in open(cfg_file):
            if "=" in line:
                k, v = line.strip().split("=", 1)
                cfg[k.strip()] = v.strip()
    cfg["JIRA_URL"]        = os.environ.get("JIRA_URL",        cfg.get("JIRA_URL", ""))
    cfg["JIRA_EMAIL"]      = os.environ.get("JIRA_EMAIL",      cfg.get("JIRA_EMAIL", ""))
    cfg["JIRA_API_TOKEN"]  = os.environ.get("JIRA_API_TOKEN",  cfg.get("JIRA_API_TOKEN", ""))
    return cfg


def get_epic_cost(epic_id, days, projects_dir):
    """Return aggregated stats for sessions attributed to epic_id."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "statusline-epic-report.py")
    r = subprocess.run(
        [sys.executable, script, "--json", "--days", str(days), "--min-cost", "0"],
        capture_output=True, text=True,
    )
    rows = json.loads(r.stdout or "[]")
    row = next((x for x in rows if x["ticket"] == epic_id), None)
    return row


def build_comment(epic_id, row, days):
    if not row:
        return f"No Claude Code sessions attributed to {epic_id} in the last {days} days."
    today = datetime.now().astimezone().date()
    since = (datetime.now().astimezone() - timedelta(days=days)).date()
    return (
        f"*Claude Code Cost Summary — {epic_id}*\n\n"
        f"||Period||Sessions||Tokens||Cost||\n"
        f"|{since} → {today}|{row['sessions']}|{row['tokens']:,}|${row['cost']:.2f}|\n\n"
        f"_Generated by statusline-jira-post.py_"
    )


def post_comment(jira_url, email, token, issue_key, body):
    url = f"{jira_url.rstrip('/')}/rest/api/2/issue/{issue_key}/comment"
    payload = json.dumps({"body": body}).encode()
    credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def main():
    parser = argparse.ArgumentParser(description='Post epic cost summary to Jira')
    parser.add_argument('--epic',  required=True, help='Jira ticket ID e.g. CSA-12345')
    parser.add_argument('--days',  type=int, default=90)
    parser.add_argument('--post',  action='store_true', help='Actually post the comment')
    args = parser.parse_args()

    projects_dir = default_projects_dir()
    row  = get_epic_cost(args.epic, args.days, projects_dir)
    body = build_comment(args.epic, row, args.days)

    print(f"\n  Epic Cost Summary — {args.epic}")
    print(f"  {'─'*50}")
    if row:
        print(f"  Sessions : {row['sessions']}")
        print(f"  Tokens   : {row['tokens']:,}")
        print(f"  Cost     : ${row['cost']:.2f}")
    else:
        print(f"  No sessions found for {args.epic} in last {args.days} days")
    print(f"  {'─'*50}\n")
    print("  Comment body:")
    print("  " + body.replace("\n", "\n  "))
    print()

    if not args.post:
        print("  [dry-run] Use --post to submit the comment to Jira")
        return

    cfg = load_jira_config()
    if not all([cfg["JIRA_URL"], cfg["JIRA_EMAIL"], cfg["JIRA_API_TOKEN"]]):
        print("  Error: set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN (env or ~/.claude/.jira-config)")
        sys.exit(1)

    status = post_comment(cfg["JIRA_URL"], cfg["JIRA_EMAIL"], cfg["JIRA_API_TOKEN"],
                          args.epic, body)
    print(f"  Posted → HTTP {status}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test (dry-run — safe, no network call)**

```bash
python3 statusline-jira-post.py --epic CSA-12345 --days 30
```

Expected: prints a cost summary table and "dry-run" message. No HTTP call made.

---

## Task 12: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current README and append the new scripts table**

After the existing scripts table in README.md, add a new section:

```markdown
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
```

- [ ] **Step 2: Verify README renders correctly**

```bash
# Quick check for obvious formatting issues
grep -n "statusline-" README.md | tail -20
```

Expected: new script entries visible in README.

---

## Self-Review Checklist

- [ ] `statusline_core.py` is named with underscore (importable as Python module)
- [ ] Every Python script has `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` before importing core
- [ ] `statusline-usage.py` refactor passes the before/after comparison (Task 2 Step 3)
- [ ] `feature-showcase.html` opens with no network requests and 8 cards expand
- [ ] `statusline-budget-check.sh` is `chmod +x`
- [ ] `statusline-jira-post.py` default is dry-run (no `--post` flag = no HTTP call)
- [ ] All scripts handle empty `~/.claude/projects/` gracefully (no crash)
