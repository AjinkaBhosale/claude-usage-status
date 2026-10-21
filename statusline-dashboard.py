#!/usr/bin/env python3
"""Generate a self-contained HTML spend dashboard with inline SVG charts.

Usage: python statusline-dashboard.py [--days 30] [--output claude-dashboard.html]
"""
import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, iter_all_lines, default_projects_dir

TICKET_RE = re.compile(r'\b([A-Z]{2,8}-\d+)\b')

# Token prefixes that match the Jira regex pattern but are NOT real Jira tickets.
_TICKET_BLOCKLIST = frozenset({
    # AI model names / versions
    "SONNET", "HAIKU", "OPUS", "CLAUDE", "GPT", "GEMINI", "MISTRAL", "LLAMA",
    # Tech / format terms
    "UTF", "ASCII", "JSON", "YAML", "HTML", "HTTP", "HTTPS", "SQL",
    "ISBN", "UUID", "GUID", "RFC", "ISO", "API", "SDK", "IDE", "MFE",
    # CSS properties / layout
    "HEIGHT", "WIDTH", "MIN", "MAX", "TOP", "LEFT", "RIGHT", "BOTTOM",
    "MARGIN", "PADDING", "BORDER", "GAP", "SIZE", "FLEX", "GRID",
    "RADIUS", "OPACITY", "SCALE", "ROTATE", "TRANSLATE",
    # Tailwind / design-system color names
    "RED", "BLUE", "GREEN", "GRAY", "GREY", "WHITE", "BLACK", "PURPLE",
    "YELLOW", "PINK", "ORANGE", "TEAL", "CYAN", "INDIGO", "VIOLET",
    "SLATE", "ZINC", "STONE", "AMBER", "LIME", "EMERALD", "SKY", "ROSE",
    # Locale / language codes
    "ES", "EN", "FR", "DE", "ZH", "JA", "KO", "PT", "IT", "NL",
    # Other short / generic tokens
    "ID", "NO", "OK", "VS", "V",
    # Common test / placeholder terms
    "ACCT", "TEST", "DEMO", "MOCK", "TEMP", "SAMPLE", "FOO", "BAR",
})


def _valid_ticket(ticket: str) -> bool:
    """Return False for model-name false positives (SONNET-4, OPUS-4, etc.)."""
    try:
        prefix, num = ticket.rsplit("-", 1)
        return prefix not in _TICKET_BLOCKLIST and len(num) >= 2
    except ValueError:
        return False


def bar_chart_svg(daily_data, width=700, height=140):
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
        + "".join(bars)
        + f'<line x1="20" y1="{height-10}" x2="{width-20}" y2="{height-10}" '
          f'stroke="#30363d" stroke-width="1"/></svg>'
    )


def donut_svg(slices, size=120):
    total = sum(v for _, v, _ in slices) or 1
    cx = cy = size // 2
    r = size // 2 - 10
    parts = []
    start = -90.0
    for label, val, color in slices:
        angle = (val / total) * 360
        end = start + angle
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
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r//2}" fill="#0d1117"/>')
    return (
        f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:{size}px;height:{size}px">' + "".join(parts) + "</svg>"
    )


def load_budget():
    """Load .claude-budget.json from cwd or ~/.claude/, return dict or {}."""
    for path in [".claude-budget.json", os.path.expanduser("~/.claude/.claude-budget.json")]:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
    return {}


def get_epic_costs(projects_dir, since_date):
    """Return ticket -> {cost, tokens, sessions} using conversation-text ticket matching."""
    conv_tickets = defaultdict(set)
    for line in iter_all_lines(projects_dir, since_date=since_date):
        if line.get("type") != "user":
            continue
        msg = line.get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        tickets = [t for t in TICKET_RE.findall(str(content).upper()) if _valid_ticket(t)]
        if tickets:
            conv_id = line.get("requestId") or line.get("project_key", "")
            conv_tickets[conv_id].update(tickets)
            conv_tickets[line.get("project_key", "")].update(tickets)

    ticket_stats = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "sessions": set()})
    for rec in iter_usage_records(projects_dir, since_date=since_date):
        conv_id = rec.get("requestId", rec["project_key"])
        tickets = conv_tickets.get(conv_id, set()) | conv_tickets.get(rec["project_key"], set())
        for t in tickets:
            ticket_stats[t]["cost"]   += rec["cost"]
            ticket_stats[t]["tokens"] += rec["total_tokens"]
            ticket_stats[t]["sessions"].add(rec["project_key"] + str(rec["date"]))
    return ticket_stats


def budget_banner_html(budget, day_cost, mtd_cost):
    """Return warning banner HTML if any budget threshold is >=80%, else ''."""
    daily_limit = budget.get("daily", 0)
    mtd_limit   = budget.get("mtd", 0)
    issues = []
    max_pct = 0.0

    if daily_limit:
        pct = day_cost / daily_limit * 100
        max_pct = max(max_pct, pct)
        label = "EXCEEDED" if pct >= 100 else f"at {pct:.0f}%"
        if pct >= 80:
            issues.append(f"Daily {label}: ${day_cost:.2f} / ${daily_limit:.2f}")

    if mtd_limit:
        pct = mtd_cost / mtd_limit * 100
        max_pct = max(max_pct, pct)
        label = "EXCEEDED" if pct >= 100 else f"at {pct:.0f}%"
        if pct >= 80:
            issues.append(f"MTD {label}: ${mtd_cost:.2f} / ${mtd_limit:.2f}")

    if not issues:
        return ""

    is_over   = max_pct >= 100
    color     = "#f85149" if is_over else "#e3b341"
    bg        = "rgba(248,81,73,0.10)" if is_over else "rgba(227,179,65,0.10)"
    icon      = "&#x1F6A8;" if is_over else "&#x26A0;&#xFE0F;"
    title     = "Budget Exceeded" if is_over else "Budget Warning"
    msgs      = " &nbsp;&middot;&nbsp; ".join(issues)

    return (
        f'<div style="background:{bg};border:1px solid {color};border-radius:8px;'
        f'padding:13px 18px;margin-bottom:20px;color:{color};font-size:0.88rem;'
        f'display:flex;align-items:center;gap:10px">'
        f'<span style="font-size:1.1rem">{icon}</span>'
        f'<span><strong>{title}</strong> &mdash; {msgs}</span>'
        f'</div>'
    )


def budget_meters_html(budget, day_cost, mtd_cost):
    """Return compact progress-bar cards for daily and MTD budgets."""
    daily_limit = budget.get("daily", 0)
    mtd_limit   = budget.get("mtd", 0)
    if not daily_limit and not mtd_limit:
        return ""

    def meter(label, spent, limit):
        pct = min(100, spent / limit * 100) if limit else 0
        bar_color = "#f85149" if pct >= 100 else "#e3b341" if pct >= 80 else "#3fb950"
        remaining = max(0, limit - spent)
        return (
            f'<div style="margin-bottom:14px">'
            f'<div style="display:flex;justify-content:space-between;font-size:0.8rem;'
            f'color:#8b949e;margin-bottom:5px">'
            f'<span>{label}</span>'
            f'<span style="color:#e6edf3">${spent:.2f} <span style="color:#8b949e">/ ${limit:.2f}</span></span></div>'
            f'<div style="background:#21262d;border-radius:4px;height:8px">'
            f'<div style="width:{pct:.1f}%;background:{bar_color};height:8px;border-radius:4px;'
            f'transition:width 0.3s"></div></div>'
            f'<div style="font-size:0.73rem;color:#8b949e;margin-top:4px">'
            f'${remaining:.2f} remaining &nbsp;·&nbsp; {pct:.0f}% used</div>'
            f'</div>'
        )

    cards = ""
    if daily_limit:
        cards += f'<div class="card">{meter("Daily Budget", day_cost, daily_limit)}</div>'
    if mtd_limit:
        cards += f'<div class="card">{meter("Month-to-Date Budget", mtd_cost, mtd_limit)}</div>'
    return f'<div class="grid" style="margin-bottom:16px">{cards}</div>'


def epic_section_html(ticket_stats, budget):
    """Return the Epic Cost Attribution card, or '' if no attributed spend."""
    epic_budgets = budget.get("epic", {})
    rows = sorted(ticket_stats.items(), key=lambda x: x[1]["cost"], reverse=True)
    rows = [(t, s) for t, s in rows if s["cost"] >= 0.01]
    if not rows:
        return ""

    row_html = ""
    for ticket, s in rows:
        limit        = epic_budgets.get(ticket, 0)
        tokens_fmt   = f"{s['tokens']/1000:.1f}K" if s['tokens'] >= 1000 else str(s['tokens'])
        sessions     = len(s["sessions"])

        if limit:
            pct       = min(100, s["cost"] / limit * 100)
            bar_color = "#f85149" if pct >= 100 else "#e3b341" if pct >= 80 else "#3fb950"
            budget_td = (
                f'<td style="min-width:140px">'
                f'<div style="display:flex;align-items:center;gap:8px">'
                f'<div style="flex:1;background:#21262d;border-radius:3px;height:6px">'
                f'<div style="width:{pct:.0f}%;background:{bar_color};height:6px;border-radius:3px"></div></div>'
                f'<span style="font-size:0.73rem;color:#8b949e;white-space:nowrap">'
                f'${s["cost"]:.2f}/${limit:.0f}</span>'
                f'</div></td>'
            )
        else:
            budget_td = '<td style="color:#30363d;text-align:center">—</td>'

        row_html += (
            f'<tr>'
            f'<td><span style="background:#1f6feb22;color:#79c0ff;padding:2px 10px;'
            f'border-radius:20px;font-size:0.79rem;font-weight:600;'
            f'border:1px solid #1f6feb55;white-space:nowrap">{ticket}</span></td>'
            f'<td style="color:#e3b341;text-align:right">${s["cost"]:.2f}</td>'
            f'<td style="color:#79c0ff;text-align:right">{tokens_fmt}</td>'
            f'<td style="text-align:right;color:#8b949e">{sessions}</td>'
            f'{budget_td}'
            f'</tr>'
        )

    total_cost   = sum(s["cost"]   for _, s in rows)
    total_tokens = sum(s["tokens"] for _, s in rows)
    total_sess   = sum(len(s["sessions"]) for _, s in rows)
    tf = f"{total_tokens/1000:.1f}K" if total_tokens >= 1000 else str(total_tokens)

    return (
        f'<div class="card" style="margin-bottom:16px">'
        f'<h2>Epic Cost Attribution</h2>'
        f'<table>'
        f'<tr><th>Ticket</th><th>Cost</th><th>Tokens</th><th>Sessions</th><th>Budget</th></tr>'
        f'{row_html}'
        f'<tr style="border-top:1px solid #30363d">'
        f'<td style="color:#8b949e;font-size:0.78rem">Total attributed</td>'
        f'<td style="color:#e3b341;text-align:right">${total_cost:.2f}</td>'
        f'<td style="color:#79c0ff;text-align:right">{tf}</td>'
        f'<td style="text-align:right;color:#8b949e">{total_sess}</td>'
        f'<td></td></tr>'
        f'</table>'
        f'</div>'
    )


def main():
    parser = argparse.ArgumentParser(description='Generate HTML spend dashboard')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--output', default='claude-dashboard.html')
    args = parser.parse_args()

    since = (datetime.now().astimezone() - timedelta(days=args.days)).date()
    projects_dir = default_projects_dir()
    budget = load_budget()

    daily = defaultdict(float)
    model_cost = defaultdict(float)
    project_cost = defaultdict(float)
    project_tokens = defaultdict(int)
    cache_read_tok = cache_fresh_tok = total_cost = total_tokens = 0
    day_cost = mtd_cost = 0.0
    today      = datetime.now().astimezone().date()
    month_start = today.replace(day=1)

    for rec in iter_usage_records(projects_dir, since_date=since):
        daily[str(rec["date"])]            += rec["cost"]
        short = rec["model"].split("-20")[0] if "-20" in rec["model"] else rec["model"]
        model_cost[short]                  += rec["cost"]
        project_cost[rec["project_key"]]   += rec["cost"]
        project_tokens[rec["project_key"]] += rec["total_tokens"]
        cache_read_tok  += rec["cache_read_tokens"]
        cache_fresh_tok += rec["input_tokens"]
        total_cost      += rec["cost"]
        total_tokens    += rec["total_tokens"]
        if rec["date"] == today:
            day_cost += rec["cost"]
        if rec["date"] >= month_start:
            mtd_cost += rec["cost"]

    # Epic attribution — look back further than the chart window for completeness
    epic_since    = (datetime.now().astimezone() - timedelta(days=max(args.days, 90))).date()
    ticket_stats  = get_epic_costs(projects_dir, epic_since)

    all_days = []
    cur = since
    while cur <= today:
        all_days.append((str(cur), daily.get(str(cur), 0.0)))
        cur = date.fromordinal(cur.toordinal() + 1)

    bar_svg_str = bar_chart_svg(all_days)
    model_colors = ["#79c0ff", "#3fb950", "#d2a8ff", "#ffa657", "#f85149", "#e3b341"]
    model_slices = [
        (m, c, model_colors[i % len(model_colors)])
        for i, (m, c) in enumerate(sorted(model_cost.items(), key=lambda x: -x[1]))
    ]
    donut_svg_str = donut_svg(model_slices)
    top_projects  = sorted(project_cost.items(), key=lambda x: -x[1])[:10]
    proj_rows     = "".join(
        f"<tr><td>{k[:40]}</td><td>${v:.2f}</td><td>{project_tokens[k]//1000}K</td></tr>"
        for k, v in top_projects
    )
    hit_rate     = cache_read_tok / (cache_read_tok + cache_fresh_tok) if (cache_read_tok + cache_fresh_tok) else 0
    legend_html  = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:0.8rem">'
        f'<span style="width:10px;height:10px;border-radius:2px;background:{c}"></span>{m}</span>'
        for m, _, c in model_slices
    )

    banner_html  = budget_banner_html(budget, day_cost, mtd_cost)
    meters_html  = budget_meters_html(budget, day_cost, mtd_cost)
    epic_html    = epic_section_html(ticket_stats, budget)

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Usage Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;padding:24px}}
h1{{font-size:1.4rem;color:#79c0ff;margin-bottom:4px}}
.sub{{color:#8b949e;font-size:0.85rem;margin-bottom:20px}}
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
.donut-row{{display:flex;align-items:center;gap:20px;flex-wrap:wrap}}
</style>
</head><body>
<h1>Claude Usage Dashboard</h1>
<p class="sub">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;&middot;&nbsp; last {args.days} days</p>
{banner_html}
{meters_html}
<div class="grid">
  <div class="card"><h2>Total Spend</h2><div class="big">${total_cost:.2f}</div><div class="small">last {args.days} days</div></div>
  <div class="card"><h2>Total Tokens</h2><div class="big">{total_tokens//1000:,}K</div><div class="small">input + output + cache</div></div>
  <div class="card"><h2>Cache Hit Rate</h2><div class="big">{hit_rate*100:.1f}%</div><div class="small">{cache_read_tok//1000}K cached reads</div></div>
  <div class="card"><h2>Active Projects</h2><div class="big">{len(project_cost)}</div><div class="small">with usage this period</div></div>
</div>
<div class="card" style="margin-bottom:16px"><h2>Daily Spend &mdash; last {args.days} days</h2>{bar_svg_str}</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
  <div class="card"><h2>Model Mix</h2><div class="donut-row">{donut_svg_str}<div>{legend_html}</div></div></div>
  <div class="card"><h2>Top Projects</h2><table><tr><th>Project</th><th>Cost</th><th>Tokens</th></tr>{proj_rows}</table></div>
</div>
{epic_html}
</body></html>"""

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(html)
    size_kb = os.path.getsize(args.output) // 1024
    print(f"  Saved: {args.output} ({size_kb} KB) — open in any browser\n")


if __name__ == "__main__":
    main()
