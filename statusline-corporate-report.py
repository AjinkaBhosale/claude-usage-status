#!/usr/bin/env python3
"""Corporate usage report — budget, model mix, cache, waste, and epics in one view.

Usage:
  python statusline-corporate-report.py [--period week|month] [--month YYYY-MM]
                                        [--format text|html|both] [--output FILE]
                                        [--budget PATH]

Examples:
  python statusline-corporate-report.py --period week
  python statusline-corporate-report.py --month 2026-06 --format html
  python statusline-corporate-report.py --month 2026-06 --format both
"""
import argparse
import calendar
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import (
    iter_usage_records, iter_all_lines, price_tokens, default_projects_dir,
)

SONNET_MODEL = "claude-sonnet-5-5"
TICKET_RE    = re.compile(r'\b([A-Z]{2,8}-\d+)\b')
FILE_PATH_RE = re.compile(r'[\w./\\-]{6,}\.(?:ts|tsx|js|py|json|sh|md|html|css|yaml|yml)\b')
MODEL_COLORS = ["#79c0ff", "#3fb950", "#d2a8ff", "#ffa657", "#f85149", "#e3b341"]


# ─── Period ────────────────────────────────────────────────────────────────────

def parse_period(args):
    today = datetime.now().astimezone().date()
    if args.month:
        y, m = map(int, args.month.split("-"))
        since = date(y, m, 1)
        until = date(y, m, calendar.monthrange(y, m)[1])
        label = since.strftime("%B %Y")
    elif args.period == "week":
        since = today - timedelta(days=6)
        until = today
        s = since.strftime("%d %b").lstrip("0")
        u = until.strftime("%d %b %Y").lstrip("0")
        label = f"Week  {s} – {u}"
    else:
        since = today.replace(day=1)
        until = today
        label = today.strftime("%B %Y") + " (MTD)"
    return since, until, label, (until - since).days + 1


# ─── Data collection ───────────────────────────────────────────────────────────

def collect(projects_dir, since, until):
    """Three passes: (1) usage records for all cost/token/model data,
    (2) all lines for waste patterns + ticket mentions,
    (3) usage records again for epic cost attribution."""

    # ── Pass 1: usage records ────────────────────────────────────────────────
    total_cost = total_tok = 0.0
    total_inp = total_out = total_cw = total_cr = 0

    day_costs   = defaultdict(float)
    day_tokens  = defaultdict(int)
    model_stats = defaultdict(lambda: {
        "cost": 0.0, "input": 0, "output": 0, "cw": 0, "cr": 0, "count": 0
    })
    project_stats = defaultdict(lambda: {"cost": 0.0, "tokens": 0})
    project_cache = defaultdict(lambda: {
        "cr": 0, "input": 0, "cw": 0, "cost": 0.0, "cost_no_cache": 0.0
    })
    session_cache = defaultdict(int)
    session_cost  = defaultdict(float)

    opus_waste     = 0.0
    model_savings  = 0.0
    model_savings_n = 0

    # detailed per-call records for drill-down tables
    opus_trivial_detail = []   # output < 200 tokens
    advisor_detail      = []   # input ≤ 5000 AND output ≤ 500

    for rec in iter_usage_records(projects_dir, since_date=since):
        if rec["date"] > until:
            continue
        d  = rec["date"]
        m  = rec["model"] or "unknown"
        sk = (rec["project_key"], str(d))

        total_cost += rec["cost"]
        total_tok  += rec["total_tokens"]
        total_inp  += rec["input_tokens"]
        total_out  += rec["output_tokens"]
        total_cw   += rec["cache_write_tokens"]
        total_cr   += rec["cache_read_tokens"]

        day_costs[d]  += rec["cost"]
        day_tokens[d] += rec["total_tokens"]

        ms = model_stats[m]
        ms["cost"]   += rec["cost"]
        ms["input"]  += rec["input_tokens"]
        ms["output"] += rec["output_tokens"]
        ms["cw"]     += rec["cache_write_tokens"]
        ms["cr"]     += rec["cache_read_tokens"]
        ms["count"]  += 1

        ps = project_stats[rec["project_key"]]
        ps["cost"]   += rec["cost"]
        ps["tokens"] += rec["total_tokens"]

        pc = project_cache[rec["project_key"]]
        pc["cr"]     += rec["cache_read_tokens"]
        pc["input"]  += rec["input_tokens"]
        pc["cw"]     += rec["cache_write_tokens"]
        pc["cost"]   += rec["cost"]
        pc["cost_no_cache"] += price_tokens(
            m,
            rec["input_tokens"] + rec["cache_write_tokens"] + rec["cache_read_tokens"],
            rec["output_tokens"], 0, 0,
        )

        session_cache[sk] += rec["cache_read_tokens"]
        session_cost[sk]  += rec["cost"]

        if "opus" in m.lower() and rec["output_tokens"] < 200:
            sc = price_tokens(
                SONNET_MODEL,
                rec["input_tokens"], rec["output_tokens"],
                rec["cache_write_tokens"], rec["cache_read_tokens"],
            )
            opus_waste += rec["cost"] - sc
            opus_trivial_detail.append({
                "date":    str(d),
                "project": rec["project_key"][:32],
                "model":   _short(m),
                "in_tok":  rec["input_tokens"],
                "out_tok": rec["output_tokens"],
                "cr_tok":  rec["cache_read_tokens"],
                "actual":  rec["cost"],
                "sonnet":  sc,
                "saving":  rec["cost"] - sc,
            })

        if "opus" in m.lower() and rec["input_tokens"] <= 5000 and rec["output_tokens"] <= 500:
            sc = price_tokens(
                SONNET_MODEL,
                rec["input_tokens"], rec["output_tokens"],
                rec["cache_write_tokens"], rec["cache_read_tokens"],
            )
            model_savings   += rec["cost"] - sc
            model_savings_n += 1
            advisor_detail.append({
                "date":    str(d),
                "project": rec["project_key"][:32],
                "model":   _short(m),
                "in_tok":  rec["input_tokens"],
                "out_tok": rec["output_tokens"],
                "actual":  rec["cost"],
                "sonnet":  sc,
                "saving":  rec["cost"] - sc,
            })

    cache_savings  = 0.0
    for m, s in model_stats.items():
        no_cache = price_tokens(m, s["input"] + s["cw"] + s["cr"], s["output"], 0, 0)
        cache_savings += max(0.0, no_cache - s["cost"])

    cache_hit_rate = total_cr / (total_cr + total_inp) if (total_cr + total_inp) else 0.0

    proj_cache_stats = {
        k: {
            "cr": v["cr"], "input": v["input"], "cw": v["cw"],
            "cost": v["cost"],
            "savings": max(0.0, v["cost_no_cache"] - v["cost"]),
            "hit_rate": v["cr"] / (v["cr"] + v["input"]) if (v["cr"] + v["input"]) else 0.0,
        }
        for k, v in project_cache.items()
    }

    usage = {
        "total_cost": total_cost, "total_tokens": total_tok,
        "total_input": total_inp, "total_output": total_out,
        "total_cw": total_cw,     "total_cr": total_cr,
        "day_costs": day_costs,   "day_tokens": day_tokens,
        "model_stats": model_stats, "project_stats": project_stats,
        "project_cache": proj_cache_stats,
        "active_days": len(day_costs),
        "cache_hit_rate": cache_hit_rate,
        "cache_savings": cache_savings,
        "model_savings": model_savings, "model_savings_n": model_savings_n,
        "advisor_detail": sorted(advisor_detail, key=lambda x: -x["saving"]),
    }

    # ── Pass 2: all lines (waste patterns + epic ticket mentions) ────────────
    session_file_counts = defaultdict(lambda: defaultdict(int))
    session_turn_counts = defaultdict(int)
    conv_tickets        = defaultdict(set)

    for line in iter_all_lines(projects_dir, since_date=since):
        ts = line.get("timestamp", "")
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
        except Exception:
            d = since
        if d > until:
            continue
        sk  = (line["project_key"], str(d))
        msg = line.get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        text = str(content)

        if line.get("type") == "user":
            session_turn_counts[sk] += 1
            for fp in FILE_PATH_RE.findall(text):
                session_file_counts[sk][fp] += 1
            for t in TICKET_RE.findall(text.upper()):
                conv_tickets[line["project_key"]].add(t)

    rereads = sorted(
        [
            {"session": sess, "file": fp, "count": cnt,
             "est_cost": round(0.02 * cnt, 3)}
            for sess, files in session_file_counts.items()
            for fp, cnt in files.items()
            if cnt >= 5
        ],
        key=lambda x: -x["count"],
    )
    cold_sessions = sorted(
        [
            {"session": k,
             "turns": session_turn_counts[k],
             "cost": session_cost.get(k, 0.0),
             "est_waste": round(session_cost.get(k, 0.0) * 0.30, 4)}
            for k, turns in session_turn_counts.items()
            if turns > 10 and session_cache.get(k, 0) == 0
        ],
        key=lambda x: -x["cost"],
    )

    total_waste = (
        sum(r["est_cost"] for r in rereads)
        + sum(s["est_waste"] for s in cold_sessions)
        + opus_waste
    )

    waste = {
        "rereads": rereads,
        "cold_sessions": cold_sessions,
        "trivial_opus_count": len(opus_trivial_detail),
        "opus_waste": opus_waste,
        "opus_detail": sorted(opus_trivial_detail, key=lambda x: -x["saving"]),
        "total_waste": total_waste,
    }

    # ── Pass 3: epic attribution ─────────────────────────────────────────────
    ticket_stats  = defaultdict(lambda: {"cost": 0.0, "tokens": 0})
    untagged_cost = untagged_tok = 0.0

    for rec in iter_usage_records(projects_dir, since_date=since):
        if rec["date"] > until:
            continue
        tickets = conv_tickets.get(rec["project_key"], set())
        if tickets:
            share     = rec["cost"]         / len(tickets)
            share_tok = rec["total_tokens"] // max(1, len(tickets))
            for t in tickets:
                ticket_stats[t]["cost"]   += share
                ticket_stats[t]["tokens"] += share_tok
        else:
            untagged_cost += rec["cost"]
            untagged_tok  += rec["total_tokens"]

    epics = {
        "tickets":  dict(sorted(ticket_stats.items(), key=lambda x: -x[1]["cost"])),
        "untagged": {"cost": untagged_cost, "tokens": int(untagged_tok)},
    }

    return usage, waste, epics


# ─── Budget ────────────────────────────────────────────────────────────────────

def load_budget(path=None):
    for p in ([path] if path else []) + [
        ".claude-budget.json",
        os.path.join(os.path.expanduser("~"), ".claude", ".claude-budget.json"),
    ]:
        if p and os.path.exists(p):
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception:
                pass
    return None


# ─── Insights ─────────────────────────────────────────────────────────────────

def generate_insights(usage, waste, budget):
    insights = []
    total = usage["total_cost"]
    chr_  = usage["cache_hit_rate"]
    cs    = usage["cache_savings"]
    ms    = usage["model_savings"]
    msn   = usage["model_savings_n"]
    wt    = waste["total_waste"]
    active = usage["active_days"]

    if chr_ < 0.2:
        insights.append(
            f"Cache hit rate is low ({chr_*100:.1f}%). Keep sessions open longer "
            f"so prior context is reused — each fresh turn re-sends all context at full price."
        )
    elif chr_ >= 0.5:
        insights.append(
            f"Cache efficiency is strong ({chr_*100:.1f}% hit rate), saving "
            f"${cs:.2f} vs a no-cache baseline. Prompt caching is working as intended."
        )

    if wt > 5.0:
        top_waste = []
        if waste["rereads"]:
            top_waste.append(f"{len(waste['rereads'])} files read repeatedly")
        if waste["cold_sessions"]:
            top_waste.append(f"{len(waste['cold_sessions'])} cold-cache sessions")
        if waste["trivial_opus_count"]:
            top_waste.append(f"{waste['trivial_opus_count']} trivial Opus calls")
        insights.append(
            f"${wt:.2f} estimated avoidable waste: {', '.join(top_waste)}. "
            f"Expand 'Waste Analysis' below for the full breakdown."
        )
    elif wt < 1.0 and active > 2:
        insights.append("Waste signals look clean — no significant anti-patterns detected.")

    if ms > 1.0:
        insights.append(
            f"${ms:.2f} reachable savings: {msn} Opus calls had ≤5K input and ≤500 output tokens. "
            f"Sonnet 4.6 handles this range at ~5× lower cost with comparable quality. "
            f"See 'Model Advisor' for the full candidate list."
        )

    if budget:
        mtd_lim = budget.get("mtd", 0)
        if mtd_lim:
            pct = total / mtd_lim * 100
            if pct >= 100:
                insights.append(
                    f"BUDGET EXCEEDED: ${total:.2f} vs ${mtd_lim:.2f} limit ({pct:.0f}%). "
                    f"Consider pausing non-critical sessions."
                )
            elif pct >= 80:
                insights.append(
                    f"Approaching budget: ${total:.2f} of ${mtd_lim:.2f} ({pct:.0f}% used). "
                    f"${mtd_lim-total:.2f} remaining."
                )
            else:
                insights.append(
                    f"On budget: ${total:.2f} of ${mtd_lim:.2f} ({pct:.0f}%). "
                    f"${mtd_lim-total:.2f} remaining."
                )

    top = sorted(usage["model_stats"].items(), key=lambda x: -x[1]["cost"])
    if top and total > 0:
        tm, ts_ = top[0]
        pct = ts_["cost"] / total * 100
        if pct > 80:
            insights.append(
                f"{_short(tm)} drives {pct:.0f}% of spend — "
                f"cost profile is concentrated and predictable."
            )

    if active == 0:
        insights.append("No usage data found for this period.")

    return insights


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _short(m):
    return m.split("-20")[0] if "-20" in m else m

def _fmt(n):
    if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def _abar(pct, w=20):
    f = min(w, int(pct / 100 * w))
    return "█" * f + "░" * (w - f)


# ─── Text report ──────────────────────────────────────────────────────────────

def print_text_report(usage, waste, epics, budget, insights, since, until, label, period_days):
    W, SEP = 68, "─" * 68

    def section(t):
        print(f"\n  {t}\n  {SEP}")

    print(f"\n{'═'*W}")
    print(f"  Claude Code · Corporate Usage Report")
    print(f"  Period : {label}  ({since} → {until}, {period_days} days)")
    print(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'═'*W}")

    total, active = usage["total_cost"], usage["active_days"]
    avg, chr_, cs = total/active if active else 0, usage["cache_hit_rate"], usage["cache_savings"]

    section("EXECUTIVE SUMMARY")
    print(f"  {'Total Spend':<28}: ${total:.4f}")
    print(f"  {'Total Tokens':<28}: {_fmt(usage['total_tokens'])}")
    print(f"  {'Active Days':<28}: {active} / {period_days}")
    print(f"  {'Avg Daily Spend':<28}: ${avg:.2f}")
    print(f"  {'Active Projects':<28}: {len(usage['project_stats'])}")
    print(f"  {'Cache Hit Rate':<28}: {chr_*100:.1f}%")
    print(f"  {'Cache Savings':<28}: ${cs:.2f}")
    print(f"  {'Estimated Waste':<28}: ${waste['total_waste']:.2f}")
    print(f"  {'Model Savings Potential':<28}: ${usage['model_savings']:.2f}")

    if budget:
        section("BUDGET STATUS")
        today_cost  = usage["day_costs"].get(datetime.now().astimezone().date(), 0.0)
        for lbl, spent, lim in [("Daily", today_cost, budget.get("daily", 0)),
                                  ("MTD",   total,      budget.get("mtd", 0))]:
            if lim:
                pct = spent / lim * 100
                s   = "✗ OVER" if pct >= 100 else "⚠ watch" if pct >= 80 else "✓ OK"
                print(f"  {lbl:<6} ${spent:.2f} / ${lim:.2f}  {_abar(pct, 15)}  {pct:>3.0f}%  {s}")

    section("MODEL BREAKDOWN")
    print(f"  {'Model':<36} {'Cost':>8}  {'Share':>6}  {'Tokens':>9}  {'Cache%':>7}  {'Calls':>6}")
    print(f"  {SEP}")
    for m, s in sorted(usage["model_stats"].items(), key=lambda x: -x[1]["cost"]):
        share = s["cost"] / total * 100 if total else 0
        denom = s["cr"] + s["input"]
        hit   = s["cr"] / denom * 100 if denom else 0
        print(f"  {_short(m):<36} ${s['cost']:>7.2f}  {share:>5.1f}%  "
              f"{_fmt(s['input']+s['output']+s['cw']+s['cr']):>9}  {hit:>6.1f}%  {s['count']:>6,}")

    section("CACHE EFFICIENCY")
    print(f"  Hit rate : {chr_*100:.1f}%   Savings vs no-cache : ${cs:.2f}")
    print(f"  Reads    : {_fmt(usage['total_cr'])}   Fresh input : {_fmt(usage['total_input'])}   Writes : {_fmt(usage['total_cw'])}")

    section("WASTE ANALYSIS")
    if waste["rereads"]:
        print(f"  File re-reads ({len(waste['rereads'])} files ≥5×, est. ${sum(r['est_cost'] for r in waste['rereads']):.2f}):")
        for r in waste["rereads"][:6]:
            print(f"    {os.path.basename(r['file']):<34} {r['count']:>3}×  ${r['est_cost']:.3f}")
    else:
        print("  File re-reads     : none ✓")

    if waste["cold_sessions"]:
        print(f"\n  Cold-cache sessions ({len(waste['cold_sessions'])}, >10 turns, 0 reads):")
        for s in waste["cold_sessions"][:4]:
            print(f"    {s['session'][0][:32]}  {s['turns']} turns  cost ${s['cost']:.2f}  waste ~${s['est_waste']:.2f}")
    else:
        print("  Cold sessions     : none ✓")

    if waste["trivial_opus_count"]:
        print(f"\n  Trivial Opus ({waste['trivial_opus_count']} calls, output <200 tok)  ~${waste['opus_waste']:.2f} wasted:")
        for r in waste["opus_detail"][:5]:
            print(f"    {r['date']}  out={r['out_tok']:>4}  actual ${r['actual']:.4f}  sonnet ${r['sonnet']:.4f}  save ${r['saving']:.4f}")
    else:
        print("  Trivial Opus      : none ✓")

    print(f"\n  Estimated total waste : ${waste['total_waste']:.2f}")

    if usage["model_savings"] > 0:
        section("MODEL ADVISOR  (Opus → Sonnet candidates)")
        print(f"  {usage['model_savings_n']} calls  ·  potential saving ${usage['model_savings']:.2f}")
        print(f"  {'Date':<12} {'Project':<28} {'In':>8}  {'Out':>6}  {'Actual':>8}  {'Sonnet':>8}  {'Save':>8}")
        print(f"  {SEP}")
        for r in usage["advisor_detail"][:15]:
            print(f"  {r['date']:<12} {r['project'][:28]:<28} {r['in_tok']:>8,}  {r['out_tok']:>6,}  "
                  f"${r['actual']:>7.4f}  ${r['sonnet']:>7.4f}  ${r['saving']:>7.4f}")
        if len(usage["advisor_detail"]) > 15:
            print(f"  … ({len(usage['advisor_detail'])-15} more)")

    if epics["tickets"]:
        section("EPIC ATTRIBUTION  (top 12)")
        print(f"  {'Ticket':<16} {'Cost':>9}  {'Tokens':>10}")
        print(f"  {SEP}")
        for t, s in list(epics["tickets"].items())[:12]:
            print(f"  {t:<16} ${s['cost']:>8.2f}  {_fmt(s['tokens']):>10}")
        if epics["untagged"]["cost"] > 0.01:
            print(f"  {'Untagged':<16} ${epics['untagged']['cost']:>8.2f}  {_fmt(epics['untagged']['tokens']):>10}")

    if usage["day_costs"]:
        section("DAILY TREND")
        items = sorted(usage["day_costs"].items())
        max_v = max(v for _, v in items) or 1
        for d, v in items:
            print(f"  {d}  {'█' * int(v/max_v*35):<35}  ${v:.2f}")

    section("KEY INSIGHTS & RECOMMENDATIONS")
    for i, text in enumerate(insights, 1):
        words, lines_, cur = text.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 > 62:
                lines_.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur: lines_.append(cur)
        print(f"  {i}. {lines_[0]}")
        for extra in lines_[1:]:
            print(f"     {extra}")

    print(f"\n{'═'*W}\n")


# ─── HTML helpers ──────────────────────────────────────────────────────────────

def _svg_bar(daily_data, width=820, height=160):
    if not daily_data:
        return "<svg></svg>"
    max_v = max(v for _, v in daily_data) or 1
    n     = len(daily_data)
    gw    = max(4, (width - 40) // n)
    bar_w = max(3, gw - 3)
    parts = []
    for i, (d, v) in enumerate(daily_data):
        x   = 20 + i * gw
        bh  = int((v / max_v) * (height - 30))
        y   = height - 18 - bh
        col = "#e3b341" if v == max_v else "#79c0ff"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bh}" fill="{col}" '
            f'rx="2" opacity="0.9"><title>{d}\n${v:.4f}</title></rect>'
        )
        if n <= 35:
            parts.append(
                f'<text x="{x+bar_w//2}" y="{height-4}" text-anchor="middle" '
                f'fill="#8b949e" font-size="9">{str(d)[-5:]}</text>'
            )
    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:{height}px">' + "".join(parts)
        + f'<line x1="20" y1="{height-18}" x2="{width-20}" y2="{height-18}" '
          f'stroke="#30363d" stroke-width="1"/></svg>'
    )


def _svg_donut(slices, size=130):
    total = sum(v for _, v, _ in slices) or 1
    cx = cy = size // 2
    r  = size // 2 - 10
    if len(slices) == 1:
        _, val, color = slices[0]
        return (
            f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" '
            f'style="width:{size}px;height:{size}px">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}" opacity="0.9"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r//2}" fill="#0d1117"/></svg>'
        )
    parts, start = [], -90.0
    for lbl, val, color in slices:
        angle = (val / total) * 360
        end   = start + angle
        x1 = cx + r * math.cos(math.radians(start))
        y1 = cy + r * math.sin(math.radians(start))
        x2 = cx + r * math.cos(math.radians(end))
        y2 = cy + r * math.sin(math.radians(end))
        large = 1 if angle > 180 else 0
        parts.append(
            f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large},1 '
            f'{x2:.1f},{y2:.1f} Z" fill="{color}" opacity="0.9">'
            f'<title>{lbl}: {val/total*100:.1f}%  ${val:.2f}</title></path>'
        )
        start = end
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r//2}" fill="#0d1117"/>')
    return (
        f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:{size}px;height:{size}px">' + "".join(parts) + "</svg>"
    )


def _th(label, tip=""):
    title = f' title="{tip}"' if tip else ""
    return f'<th onclick="sortTbl(this)"{title}>{label} <span class="sort-icon">⇅</span></th>'


def _td(val, color="", data_val=None):
    dv = f' data-val="{data_val}"' if data_val is not None else ""
    style = f' style="color:{color}"' if color else ""
    return f"<td{dv}{style}>{val}</td>"


def _badge(text, color="amber"):
    colors = {
        "red":   ("rgba(248,81,73,.15)",  "#f85149"),
        "amber": ("rgba(227,179,65,.15)", "#e3b341"),
        "green": ("rgba(63,185,80,.15)",  "#3fb950"),
        "blue":  ("rgba(121,192,255,.15)","#79c0ff"),
    }
    bg, fg = colors.get(color, colors["amber"])
    return (f'<span style="background:{bg};color:{fg};border:1px solid {fg}33;'
            f'border-radius:4px;padding:1px 7px;font-size:0.72rem;font-weight:600;'
            f'white-space:nowrap">{text}</span>')


def _budget_bar(label, spent, limit, pct, color):
    return (
        f'<div style="margin-bottom:16px">'
        f'<div style="display:flex;justify-content:space-between;font-size:0.82rem;margin-bottom:5px">'
        f'<span>{label}</span>'
        f'<span style="color:#e3b341">${spent:.2f} / ${limit:.2f} &nbsp; {pct:.0f}%</span></div>'
        f'<div style="background:#21262d;border-radius:4px;height:8px">'
        f'<div style="background:{color};width:{min(pct,100):.1f}%;height:8px;border-radius:4px"></div>'
        f'</div></div>'
    )


# ─── HTML generator ────────────────────────────────────────────────────────────

def generate_html(usage, waste, epics, budget, insights, since, until, label, period_days):
    total  = usage["total_cost"]
    active = usage["active_days"]
    avg    = total / active if active else 0
    chr_   = usage["cache_hit_rate"]
    cs     = usage["cache_savings"]
    wt     = waste["total_waste"]
    today  = datetime.now().astimezone().date()

    hrcol = "#3fb950" if chr_ >= 0.5 else "#e3b341" if chr_ >= 0.2 else "#f85149"
    wcol  = "#f85149" if wt > 5 else "#e3b341" if wt > 1 else "#3fb950"

    # ── Daily chart ──────────────────────────────────────────────────────────
    all_days = []
    cur = since
    while cur <= min(until, today):
        all_days.append((str(cur), usage["day_costs"].get(cur, 0.0)))
        cur = date.fromordinal(cur.toordinal() + 1)
    bar_svg = _svg_bar(all_days)

    # ── Model donut ──────────────────────────────────────────────────────────
    model_items  = sorted(usage["model_stats"].items(), key=lambda x: -x[1]["cost"])
    model_slices = [
        (_short(m), s["cost"], MODEL_COLORS[i % len(MODEL_COLORS)])
        for i, (m, s) in enumerate(model_items)
    ]
    donut  = _svg_donut(model_slices)
    legend = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:5px 0;font-size:0.79rem">'
        f'<span style="width:10px;height:10px;border-radius:2px;flex-shrink:0;background:{c}"></span>'
        f'<span>{m}</span>'
        f'<span style="color:#8b949e;margin-left:auto">${s["cost"]:.2f} '
        f'({s["cost"]/total*100:.0f}%)</span></div>'
        for (m, _, c), (_, s) in zip(model_slices, model_items)
    )

    # ── Model breakdown table ────────────────────────────────────────────────
    model_rows = ""
    for (m, s), (_, _, color) in zip(model_items, model_slices):
        share = s["cost"] / total * 100 if total else 0
        denom = s["cr"] + s["input"]
        hit   = s["cr"] / denom * 100 if denom else 0
        dot   = f'<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:{color};margin-right:7px;flex-shrink:0"></span>'
        model_rows += (
            f"<tr>"
            f"<td style='display:flex;align-items:center'>{dot}{_short(m)}</td>"
            + _td(f"${s['cost']:.2f}", "#e3b341", s["cost"])
            + _td(f"{share:.1f}%", data_val=share)
            + _td(_fmt(s["input"]+s["output"]+s["cw"]+s["cr"]), data_val=s["input"]+s["output"]+s["cw"]+s["cr"])
            + _td(f"{hit:.1f}%", "#3fb950" if hit >= 50 else "#e3b341" if hit >= 20 else "#f85149", hit)
            + _td(f"{s['count']:,}", data_val=s["count"])
            + "</tr>"
        )

    # ── Per-project cache table ──────────────────────────────────────────────
    proj_cache_rows = ""
    for proj, pc in sorted(usage["project_cache"].items(), key=lambda x: -x[1]["savings"])[:20]:
        hr   = pc["hit_rate"] * 100
        hcol = "#3fb950" if hr >= 50 else "#e3b341" if hr >= 20 else "#f85149"
        status = "✓ warm" if hr >= 50 else "~ cool" if hr >= 20 else "✗ cold"
        proj_cache_rows += (
            f"<tr>"
            + _td(proj[:40])
            + _td(f"{hr:.1f}%", hcol, hr)
            + _td(_fmt(pc["cr"]), data_val=pc["cr"])
            + _td(_fmt(pc["input"]), data_val=pc["input"])
            + _td(f"${pc['savings']:.2f}", "#3fb950", pc["savings"])
            + f"<td style='color:{hcol}'>{status}</td>"
            + "</tr>"
        )

    # ── Budget ───────────────────────────────────────────────────────────────
    budget_html = ""
    if budget:
        bars      = []
        today_cost = usage["day_costs"].get(today, 0.0)
        for lbl, spent, lim in [("Daily (today)", today_cost, budget.get("daily", 0)),
                                  ("Month-to-date", total,      budget.get("mtd", 0))]:
            if lim:
                pct   = spent / lim * 100
                color = "#f85149" if pct >= 100 else "#e3b341" if pct >= 80 else "#3fb950"
                bars.append(_budget_bar(lbl, spent, lim, pct, color))
        if bars:
            budget_html = (
                '<details class="card" open><summary>'
                '<div class="sh-left"><span class="sh-title">Budget Status</span></div>'
                '</summary><div class="detail-body">' + "".join(bars) + "</div></details>"
            )

    # ── Waste: file rereads table ────────────────────────────────────────────
    reread_total_cost = sum(r["est_cost"] for r in waste["rereads"])
    reread_rows = "".join(
        f"<tr>"
        + _td(os.path.basename(r["file"]))
        + _td(r["file"][:50])
        + _td(r["session"][0][:30])
        + _td(r["session"][1])
        + _td(str(r["count"]), data_val=r["count"])
        + _td(f"${r['est_cost']:.3f}", "#e3b341", r["est_cost"])
        + "</tr>"
        for r in waste["rereads"]
    )

    # ── Waste: cold sessions table ───────────────────────────────────────────
    cold_total_waste = sum(s["est_waste"] for s in waste["cold_sessions"])
    cold_rows = "".join(
        f"<tr>"
        + _td(s["session"][0][:38])
        + _td(s["session"][1])
        + _td(str(s["turns"]), data_val=s["turns"])
        + _td(f"${s['cost']:.4f}", "#e3b341", s["cost"])
        + _td(f"${s['est_waste']:.4f}", "#f85149", s["est_waste"])
        + "</tr>"
        for s in waste["cold_sessions"]
    )

    # ── Waste: trivial opus table ────────────────────────────────────────────
    opus_proj_totals = defaultdict(lambda: {"calls": 0, "waste": 0.0})
    for r in waste["opus_detail"]:
        opus_proj_totals[r["project"]]["calls"] += 1
        opus_proj_totals[r["project"]]["waste"] += r["saving"]

    opus_rows = "".join(
        f"<tr>"
        + _td(r["date"])
        + _td(r["project"][:32])
        + _td(r["model"])
        + _td(f"{r['in_tok']:,}", data_val=r["in_tok"])
        + _td(f"{r['out_tok']:,}", data_val=r["out_tok"])
        + _td(f"${r['actual']:.4f}", "#e3b341", r["actual"])
        + _td(f"${r['sonnet']:.4f}", "#3fb950", r["sonnet"])
        + _td(f"${r['saving']:.4f}", "#d2a8ff", r["saving"])
        + "</tr>"
        for r in waste["opus_detail"]
    )
    opus_proj_rows = "".join(
        f"<tr>"
        + _td(proj[:40])
        + _td(str(v["calls"]), data_val=v["calls"])
        + _td(f"${v['waste']:.4f}", "#d2a8ff", v["waste"])
        + "</tr>"
        for proj, v in sorted(opus_proj_totals.items(), key=lambda x: -x[1]["waste"])
    )

    # ── Model advisor table ──────────────────────────────────────────────────
    adv_proj_totals = defaultdict(lambda: {"n": 0, "saving": 0.0})
    for r in usage["advisor_detail"]:
        adv_proj_totals[r["project"]]["n"]      += 1
        adv_proj_totals[r["project"]]["saving"] += r["saving"]

    adv_rows = "".join(
        f"<tr>"
        + _td(r["date"])
        + _td(r["project"][:32])
        + _td(r["model"])
        + _td(f"{r['in_tok']:,}", data_val=r["in_tok"])
        + _td(f"{r['out_tok']:,}", data_val=r["out_tok"])
        + _td(f"${r['actual']:.4f}", "#e3b341", r["actual"])
        + _td(f"${r['sonnet']:.4f}", "#3fb950", r["sonnet"])
        + _td(f"${r['saving']:.4f}", "#d2a8ff", r["saving"])
        + "</tr>"
        for r in usage["advisor_detail"]
    )
    adv_proj_rows = "".join(
        f"<tr>"
        + _td(proj[:40])
        + _td(str(v["n"]), data_val=v["n"])
        + _td(f"${v['saving']:.4f}", "#d2a8ff", v["saving"])
        + "</tr>"
        for proj, v in sorted(adv_proj_totals.items(), key=lambda x: -x[1]["saving"])
    )

    # ── Epic table ───────────────────────────────────────────────────────────
    epic_rows = "".join(
        f"<tr>"
        + _td(t)
        + _td(f"${s['cost']:.2f}", "#e3b341", s["cost"])
        + _td(_fmt(s["tokens"]), data_val=s["tokens"])
        + _td(f"{s['cost']/total*100:.1f}%" if total else "0%", data_val=s["cost"]/total*100 if total else 0)
        + "</tr>"
        for t, s in list(epics["tickets"].items())[:20]
    )
    if epics["untagged"]["cost"] > 0.01:
        uc, ut = epics["untagged"]["cost"], epics["untagged"]["tokens"]
        epic_rows += (
            f'<tr style="color:#8b949e">'
            + _td("Untagged / no ticket")
            + _td(f"${uc:.2f}", data_val=uc)
            + _td(_fmt(ut), data_val=ut)
            + _td(f"{uc/total*100:.1f}%" if total else "0%", data_val=uc/total*100 if total else 0)
            + "</tr>"
        )

    # ── Insights ─────────────────────────────────────────────────────────────
    insights_html = "".join(
        f'<li><span class="insight-num">{i}</span>{text}</li>'
        for i, text in enumerate(insights, 1)
    )

    # ── Waste summary badges ─────────────────────────────────────────────────
    w_badges = " ".join(filter(None, [
        _badge(f"${wt:.2f} total", "red" if wt > 5 else "amber" if wt > 1 else "green"),
        _badge(f"{len(waste['rereads'])} re-reads", "amber") if waste["rereads"] else "",
        _badge(f"{len(waste['cold_sessions'])} cold sessions", "amber") if waste["cold_sessions"] else "",
        _badge(f"{waste['trivial_opus_count']} trivial Opus", "amber") if waste["trivial_opus_count"] else "",
        _badge("Clean ✓", "green") if not waste["rereads"] and not waste["cold_sessions"] and not waste["trivial_opus_count"] else "",
    ]))

    # ── Advisor summary badges ───────────────────────────────────────────────
    adv_badges = " ".join(filter(None, [
        _badge(f"${usage['model_savings']:.2f} potential saving", "blue") if usage["model_savings"] > 0 else "",
        _badge(f"{usage['model_savings_n']} candidates", "blue") if usage["model_savings_n"] > 0 else "",
        _badge("No candidates ✓", "green") if usage["model_savings_n"] == 0 else "",
    ]))

    # ── Reread / cold / no-waste conditional display ─────────────────────────
    def _drill(title, badge_html, body_html, open_=False):
        op = " open" if open_ else ""
        return (
            f'<details class="drill"{op}>'
            f'<summary><span>{title}</span><span style="display:flex;gap:6px;align-items:center">'
            f'{badge_html}</span></summary>'
            f'<div class="drill-body">{body_html}</div>'
            f'</details>'
        )

    def _expl(text):
        return f'<p class="explainer">{text}</p>'

    def _tbl(headers, rows, empty_msg="No data."):
        if not rows:
            return f'<p style="color:#8b949e;font-size:0.82rem;padding:12px 0">{empty_msg}</p>'
        hrow = "".join(f"<th onclick='sortTbl(this)'>{h} <span class='sort-icon'>⇅</span></th>" for h in headers)
        return f'<div class="tbl-wrap"><table><thead><tr>{hrow}</tr></thead><tbody>{rows}</tbody></table></div>'

    # file rereads drill
    reread_drill = _drill(
        "📁 File Re-reads",
        " ".join([_badge(f"{len(waste['rereads'])} files", "amber" if waste["rereads"] else "green"),
                  _badge(f"est. ${reread_total_cost:.2f}", "amber") if waste["rereads"] else ""]),
        _expl("A file read ≥5× in the same session means its content was sent to the model multiple times. "
              "This inflates input tokens without adding value. "
              "<strong>Fix:</strong> read files once, pass content forward, or keep the session alive so the cache covers it.")
        + _tbl(["File", "Full Path", "Project", "Date", "Count ↓", "Est. Cost"],
               reread_rows,
               "No file re-reads detected ✓"),
        open_=bool(waste["rereads"]),
    )

    # cold sessions drill
    cold_drill = _drill(
        "❄ Cold-Cache Sessions",
        " ".join([_badge(f"{len(waste['cold_sessions'])} sessions", "amber" if waste["cold_sessions"] else "green"),
                  _badge(f"est. ${cold_total_waste:.2f}", "amber") if waste["cold_sessions"] else ""]),
        _expl("Sessions with >10 turns and <strong>zero cache reads</strong>: the model re-read all context from scratch each turn. "
              "This is the most expensive pattern. "
              "<strong>Fix:</strong> avoid starting fresh sessions mid-task; use <code>--resume</code> or keep the terminal open.")
        + _tbl(["Project", "Date", "Turns", "Actual Cost", "Est. Waste (30%)"],
               cold_rows,
               "No cold-cache sessions detected ✓"),
        open_=bool(waste["cold_sessions"]),
    )

    # trivial opus drill
    opus_drill = _drill(
        "🎭 Trivial Opus Calls",
        " ".join([_badge(f"{waste['trivial_opus_count']} calls", "amber" if waste["trivial_opus_count"] else "green"),
                  _badge(f"${waste['opus_waste']:.2f} wasted", "amber") if waste["trivial_opus_count"] else ""]),
        _expl("Opus calls that produced <strong>fewer than 200 output tokens</strong>. "
              "Short answers don't justify Opus pricing — Sonnet 4.6 handles Q&amp;A, "
              "quick edits, and short generation at ~5× lower cost with comparable quality. "
              "<strong>Fix:</strong> set <code>model: sonnet</code> as your default and promote to Opus only for complex multi-step reasoning.")
        + "<h4 style='margin:14px 0 8px;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e'>All Trivial Calls</h4>"
        + _tbl(["Date", "Project", "Model", "Input Tok", "Output Tok", "Actual $", "Sonnet $", "Saving $"],
               opus_rows,
               "No trivial Opus calls ✓")
        + "<h4 style='margin:18px 0 8px;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e'>By Project</h4>"
        + _tbl(["Project", "Calls", "Total Waste $"], opus_proj_rows),
        open_=bool(waste["trivial_opus_count"]),
    )

    # advisor section (outside waste)
    adv_section_body = (
        _expl("These Opus calls had <strong>≤5,000 input tokens</strong> and <strong>≤500 output tokens</strong> — "
              "a range where Sonnet 4.6 consistently performs comparably at ~5× lower price. "
              "The table shows exactly what each call cost, what it would have cost on Sonnet, and the per-call saving. "
              "<strong>Action:</strong> use <code>--model claude-sonnet-5-5</code> by default and escalate to Opus only when "
              "the task needs deep multi-step reasoning, complex code synthesis, or long output.")
        + "<h4 style='margin:14px 0 8px;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e'>All Candidates</h4>"
        + _tbl(["Date", "Project", "Model", "Input Tok", "Output Tok", "Actual $", "Sonnet $", "Saving $"],
               adv_rows,
               "No model-switching candidates found ✓")
        + "<h4 style='margin:18px 0 8px;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e'>Savings by Project</h4>"
        + _tbl(["Project", "Candidates", "Potential Saving $"], adv_proj_rows)
    )

    # cache per-project drill
    cache_drill = _drill(
        "Per-project breakdown",
        _badge(f"{len(usage['project_cache'])} projects tracked", "blue"),
        _tbl(["Project", "Hit Rate", "Cache Reads", "Fresh Input", "Savings $", "Status"],
             proj_cache_rows),
    )

    has_waste_data = (waste["rereads"] or waste["cold_sessions"] or waste["trivial_opus_count"])

    # ── Assemble full HTML ────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Code — {label} Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;
      padding:28px 32px;max-width:1320px;margin:0 auto;font-size:14px}}

h1{{font-size:1.55rem;color:#79c0ff;margin-bottom:4px}}
h3{{font-size:0.95rem;color:#e6edf3;margin-bottom:12px}}
h4{{font-size:0.75rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e}}
.meta{{color:#8b949e;font-size:0.82rem;margin-bottom:24px}}
.meta strong{{color:#e6edf3}}

/* ── layout ── */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:16px}}
.row2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
.row3{{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-bottom:14px}}

/* ── card / details ── */
.card, details.card{{
  background:#161b22;border:1px solid #30363d;border-radius:10px;
  padding:18px;margin-bottom:14px
}}
details.card > summary{{
  cursor:pointer;list-style:none;display:flex;align-items:flex-start;
  justify-content:space-between;gap:12px
}}
details.card > summary::-webkit-details-marker{{display:none}}
details.card > summary::after{{
  content:'▼';color:#8b949e;font-size:0.72rem;flex-shrink:0;margin-top:3px
}}
details.card[open] > summary::after{{content:'▲'}}
details.card[open] > summary{{
  margin-bottom:16px;border-bottom:1px solid #30363d;padding-bottom:14px
}}
.sh-left{{display:flex;flex-direction:column;gap:6px;min-width:0}}
.sh-title{{font-size:0.70rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e;display:block}}
.sh-badges{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}}
.detail-body{{}}

/* ── drill-down ── */
details.drill{{
  background:#0d1117;border:1px solid #21262d;border-radius:7px;margin-top:12px
}}
details.drill > summary{{
  padding:11px 15px;cursor:pointer;list-style:none;
  display:flex;align-items:center;justify-content:space-between;
  font-size:0.86rem;font-weight:500;gap:10px
}}
details.drill > summary::-webkit-details-marker{{display:none}}
details.drill > summary::after{{content:'▼';color:#8b949e;font-size:0.68rem;flex-shrink:0}}
details.drill[open] > summary::after{{content:'▲'}}
details.drill[open] > summary{{border-bottom:1px solid #21262d}}
.drill-body{{padding:14px 15px 16px}}

/* ── KPI ── */
.kpi-card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}}
.kpi-label{{font-size:0.68rem;text-transform:uppercase;letter-spacing:1px;color:#8b949e;margin-bottom:8px}}
.kpi-val{{font-size:1.85rem;font-weight:700;color:#e3b341;line-height:1.1}}
.kpi-sub{{font-size:0.73rem;color:#8b949e;margin-top:5px}}

/* ── tables ── */
.tbl-wrap{{overflow-x:auto;margin-top:8px}}
table{{width:100%;border-collapse:collapse;font-size:0.79rem;min-width:480px}}
thead th{{
  text-align:left;color:#8b949e;padding:8px 9px;
  border-bottom:1px solid #30363d;font-weight:500;
  font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px;
  cursor:pointer;user-select:none;white-space:nowrap
}}
thead th:hover{{color:#e6edf3}}
th.sorted-asc .sort-icon::after{{content:'↑';color:#79c0ff}}
th.sorted-desc .sort-icon::after{{content:'↓';color:#79c0ff}}
.sort-icon{{color:#30363d;font-size:0.7rem}}
tbody td{{padding:7px 9px;border-bottom:1px solid #161b22;vertical-align:middle}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr:hover td{{background:#1c2128}}
td:not(:first-child){{text-align:right}}
thead th:not(:first-child){{text-align:right}}

/* ── explainer ── */
.explainer{{
  font-size:0.82rem;color:#8b949e;line-height:1.6;
  padding:10px 14px;background:#0d1117;border-left:3px solid #30363d;
  border-radius:0 4px 4px 0;margin-bottom:12px
}}
.explainer strong{{color:#e6edf3}}
.explainer code{{
  background:#21262d;padding:1px 5px;border-radius:3px;
  font-family:monospace;font-size:0.8rem;color:#79c0ff
}}

/* ── cache metric boxes ── */
.cm-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}}
.cm{{background:#0d1117;border-radius:6px;padding:11px 14px}}
.cm-lbl{{font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px;color:#8b949e;margin-bottom:5px}}
.cm-v{{font-size:1.1rem;font-weight:600}}

/* ── donut layout ── */
.donut-wrap{{display:flex;align-items:center;gap:20px;flex-wrap:wrap}}
.donut-legend{{flex:1;min-width:120px}}

/* ── insights list ── */
.insights-list{{list-style:none;padding:0}}
.insights-list li{{
  padding:11px 0;border-bottom:1px solid #21262d;
  font-size:0.85rem;line-height:1.6;display:flex;gap:10px
}}
.insights-list li:last-child{{border-bottom:none}}
.insight-num{{
  background:#79c0ff1a;color:#79c0ff;border-radius:50%;
  width:22px;height:22px;display:flex;align-items:center;justify-content:center;
  font-size:0.72rem;font-weight:700;flex-shrink:0;margin-top:1px
}}

/* ── toolbar ── */
.toolbar{{
  display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;align-items:center
}}
.btn{{
  background:#21262d;border:1px solid #30363d;color:#e6edf3;
  padding:5px 14px;border-radius:5px;font-size:0.79rem;cursor:pointer
}}
.btn:hover{{background:#30363d}}

@media print{{
  body{{background:#fff;color:#000;padding:10px}}
  .card,details.card,details.drill{{border-color:#ccc;background:#fafafa}}
  .btn,.toolbar{{display:none}}
  details{{display:block}}
  details > summary::after{{display:none}}
  .kpi-card{{background:#f0f0f0;border-color:#ccc}}
}}
</style>
</head><body>

<h1>Claude Code &mdash; Corporate Usage Report</h1>
<p class="meta">
  Period: <strong>{label}</strong> &nbsp;·&nbsp; {since} &rarr; {until}
  &nbsp;·&nbsp; Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}
</p>

<div class="toolbar">
  <button class="btn" onclick="document.querySelectorAll('details').forEach(d=>d.open=true)">Expand All</button>
  <button class="btn" onclick="document.querySelectorAll('details').forEach(d=>d.open=false)">Collapse All</button>
  <button class="btn" onclick="window.print()">🖨 Print / PDF</button>
</div>

<!-- KPI row -->
<div class="kpi-grid">
  <div class="kpi-card"><div class="kpi-label">Total Spend</div>
    <div class="kpi-val">${total:.2f}</div>
    <div class="kpi-sub">{active} active of {period_days} days</div></div>
  <div class="kpi-card"><div class="kpi-label">Total Tokens</div>
    <div class="kpi-val">{_fmt(usage['total_tokens'])}</div>
    <div class="kpi-sub">input + output + cache</div></div>
  <div class="kpi-card"><div class="kpi-label">Avg Daily</div>
    <div class="kpi-val">${avg:.2f}</div>
    <div class="kpi-sub">per active day</div></div>
  <div class="kpi-card"><div class="kpi-label">Cache Hit Rate</div>
    <div class="kpi-val" style="color:{hrcol}">{chr_*100:.1f}%</div>
    <div class="kpi-sub">${cs:.2f} saved</div></div>
  <div class="kpi-card"><div class="kpi-label">Est. Waste</div>
    <div class="kpi-val" style="color:{wcol}">${wt:.2f}</div>
    <div class="kpi-sub">avoidable spend</div></div>
  <div class="kpi-card"><div class="kpi-label">Model Savings</div>
    <div class="kpi-val" style="color:#d2a8ff">${usage['model_savings']:.2f}</div>
    <div class="kpi-sub">{usage['model_savings_n']} Opus→Sonnet</div></div>
</div>

{budget_html}

<!-- Daily chart -->
<details class="card" open>
  <summary>
    <div class="sh-left">
      <span class="sh-title">Daily Spend</span>
      <div class="sh-badges">
        {_badge(f"Peak ${max((v for _,v in all_days), default=0):.2f}", "amber")}
        {_badge(f"{len([v for _,v in all_days if v>0])} active days", "blue")}
      </div>
    </div>
  </summary>
  <div class="detail-body">{bar_svg}</div>
</details>

<!-- Model breakdown -->
<details class="card" open>
  <summary>
    <div class="sh-left">
      <span class="sh-title">Model Breakdown</span>
      <div class="sh-badges">
        {"".join(_badge(f"{_short(m)} {s['cost']/total*100:.0f}%", "blue") for m, s in model_items[:3] if total > 0)}
      </div>
    </div>
  </summary>
  <div class="detail-body">
    <div class="row3" style="margin-bottom:0">
      <div>
        {_tbl(["Model","Cost","Share","Tokens","Cache%","Calls"], model_rows)}
      </div>
      <div>
        <div class="donut-wrap">
          {donut}
          <div class="donut-legend">{legend}</div>
        </div>
      </div>
    </div>
  </div>
</details>

<!-- Cache efficiency -->
<details class="card">
  <summary>
    <div class="sh-left">
      <span class="sh-title">Cache Efficiency</span>
      <div class="sh-badges">
        {_badge(f"{chr_*100:.1f}% hit rate", "green" if chr_>=0.5 else "amber" if chr_>=0.2 else "red")}
        {_badge(f"${cs:.2f} saved", "green")}
      </div>
    </div>
  </summary>
  <div class="detail-body">
    {_expl("Prompt caching lets Claude reuse prior context instead of re-processing it each turn. "
           "The hit rate is <strong>cache reads ÷ (cache reads + fresh input)</strong>. "
           "Savings = what you would have paid without any caching. "
           "A rate above 50% is healthy; below 20% means sessions are too short or too fragmented.")}
    <div class="cm-grid">
      <div class="cm"><div class="cm-lbl">Hit Rate</div><div class="cm-v" style="color:{hrcol}">{chr_*100:.1f}%</div></div>
      <div class="cm"><div class="cm-lbl">Savings</div><div class="cm-v" style="color:#3fb950">${cs:.2f}</div></div>
      <div class="cm"><div class="cm-lbl">Cache Reads</div><div class="cm-v">{_fmt(usage['total_cr'])}</div></div>
      <div class="cm"><div class="cm-lbl">Fresh Input</div><div class="cm-v">{_fmt(usage['total_input'])}</div></div>
      <div class="cm"><div class="cm-lbl">Cache Writes</div><div class="cm-v">{_fmt(usage['total_cw'])}</div></div>
      <div class="cm"><div class="cm-lbl">Output</div><div class="cm-v">{_fmt(usage['total_output'])}</div></div>
    </div>
    {cache_drill}
  </div>
</details>

<!-- Waste analysis -->
<details class="card" {'open' if has_waste_data else ''}>
  <summary>
    <div class="sh-left">
      <span class="sh-title">Waste Analysis</span>
      <div class="sh-badges">{w_badges}</div>
    </div>
  </summary>
  <div class="detail-body">
    {_expl("Waste = spend that could be eliminated without changing your workflow. "
           "Three patterns are tracked: repeated file reads within a session, "
           "sessions that never hit the cache, and Opus calls where a short response "
           "reveals the task didn't need Opus. Expand each section for the full record list.")}
    {reread_drill}
    {cold_drill}
    {opus_drill}
    <div style="margin-top:16px;padding-top:14px;border-top:1px solid #30363d;
                display:flex;justify-content:space-between;align-items:center">
      <span style="color:#8b949e;font-size:0.82rem">Estimated total avoidable waste</span>
      <strong style="font-size:1.1rem;color:{wcol}">${wt:.2f}</strong>
    </div>
  </div>
</details>

<!-- Model advisor -->
<details class="card" {'open' if usage['model_savings'] > 1 else ''}>
  <summary>
    <div class="sh-left">
      <span class="sh-title">Model Advisor — Opus → Sonnet Opportunities</span>
      <div class="sh-badges">{adv_badges}</div>
    </div>
  </summary>
  <div class="detail-body">
    {adv_section_body}
  </div>
</details>

<!-- Epic attribution -->
{'<details class="card"><summary><div class="sh-left"><span class="sh-title">Epic Attribution</span><div class="sh-badges">' + _badge(f"{len(epics['tickets'])} tickets tracked", "blue") + "</div></div></summary><div class='detail-body'>" + _expl("Cost is attributed by scanning session transcripts for Jira-style ticket IDs (e.g. CSA-1234). When multiple tickets appear in the same project, cost is split evenly. 'Untagged' means no ticket ID was found in any user message for that project.") + _tbl(["Ticket","Cost","Tokens","Share %"], epic_rows) + "</div></details>" if epics["tickets"] else ""}

<!-- Insights -->
<details class="card" open>
  <summary>
    <div class="sh-left">
      <span class="sh-title">Key Insights &amp; Recommendations</span>
      <div class="sh-badges">{_badge(f"{len(insights)} findings", "blue")}</div>
    </div>
  </summary>
  <div class="detail-body">
    <ul class="insights-list">{insights_html}</ul>
  </div>
</details>

<script>
function sortTbl(th) {{
  const tbl  = th.closest('table');
  const tb   = tbl.querySelector('tbody');
  const rows = Array.from(tb.querySelectorAll('tr'));
  const col  = Array.from(th.parentElement.children).indexOf(th);
  const asc  = th.dataset.asc !== 'true';
  th.dataset.asc = asc;
  tbl.querySelectorAll('thead th').forEach(h => {{
    h.classList.remove('sorted-asc','sorted-desc');
    h.querySelector('.sort-icon').textContent = '⇅';
  }});
  th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');
  th.querySelector('.sort-icon').textContent = asc ? '↑' : '↓';
  rows.sort((a, b) => {{
    const av = a.cells[col]?.dataset.val ?? a.cells[col]?.textContent.trim() ?? '';
    const bv = b.cells[col]?.dataset.val ?? b.cells[col]?.textContent.trim() ?? '';
    const an = parseFloat(av), bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  }});
  rows.forEach(r => tb.appendChild(r));
}}
</script>
</body></html>"""


# ─── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Corporate Claude Code usage report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--period", choices=["week", "month"], default="month")
    ap.add_argument("--month", default=None, metavar="YYYY-MM")
    ap.add_argument("--format", choices=["text", "html", "both"], default="text", dest="fmt")
    ap.add_argument("--output", default=None)
    ap.add_argument("--budget", default=None, metavar="PATH")
    args = ap.parse_args()

    since, until, label, period_days = parse_period(args)
    projects_dir = default_projects_dir()

    print(f"  Scanning {projects_dir}")
    print(f"  Period  : {since}  →  {until}", flush=True)

    usage, waste, epics = collect(projects_dir, since, until)
    budget   = load_budget(args.budget)
    insights = generate_insights(usage, waste, budget)

    if args.fmt in ("text", "both"):
        print_text_report(usage, waste, epics, budget, insights, since, until, label, period_days)

    if args.fmt in ("html", "both"):
        html = generate_html(usage, waste, epics, budget, insights, since, until, label, period_days)
        tag  = since.strftime("%Y%m") if (args.month or args.period == "month") \
               else since.strftime("%Y%m%d")
        out  = args.output or f"claude-report-{tag}.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        kb = os.path.getsize(out) // 1024
        print(f"  HTML report saved: {out}  ({kb} KB)\n")


if __name__ == "__main__":
    main()
