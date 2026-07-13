#!/usr/bin/env python3
"""Generate a self-contained HTML spend dashboard with inline SVG charts.

Usage: python statusline-dashboard.py [--days 30] [--output claude-dashboard.html]
"""
import argparse
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, default_projects_dir


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
    cache_read_tok = cache_fresh_tok = total_cost = total_tokens = 0

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

    all_days = []
    cur = since
    today = datetime.now().astimezone().date()
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
    top_projects = sorted(project_cost.items(), key=lambda x: -x[1])[:10]
    proj_rows = "".join(
        f"<tr><td>{k[:40]}</td><td>${v:.2f}</td><td>{project_tokens[k]//1000}K</td></tr>"
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
.donut-row{{display:flex;align-items:center;gap:20px;flex-wrap:wrap}}
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
<div class="card" style="margin-bottom:16px"><h2>Daily Spend — last {args.days} days</h2>{bar_svg_str}</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
  <div class="card"><h2>Model Mix</h2><div class="donut-row">{donut_svg_str}<div>{legend_html}</div></div></div>
  <div class="card"><h2>Top Projects</h2><table><tr><th>Project</th><th>Cost</th><th>Tokens</th></tr>{proj_rows}</table></div>
</div>
</body></html>"""

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = os.path.getsize(args.output) // 1024
    print(f"  Saved: {args.output} ({size_kb} KB) — open in any browser\n")


if __name__ == "__main__":
    main()
