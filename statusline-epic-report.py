#!/usr/bin/env python3
"""Report Claude Code costs attributed to Jira epics/tickets.

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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_all_lines, iter_usage_records, default_projects_dir

TICKET_RE = re.compile(r'\b([A-Z]{2,8}-\d+)\b')

_TICKET_BLOCKLIST = frozenset({
    "SONNET", "HAIKU", "OPUS", "CLAUDE", "GPT", "GEMINI", "MISTRAL", "LLAMA",
    "UTF", "ASCII", "JSON", "YAML", "HTML", "HTTP", "HTTPS", "SQL",
    "ISBN", "UUID", "GUID", "RFC", "ISO", "API", "SDK", "IDE", "MFE",
    "HEIGHT", "WIDTH", "MIN", "MAX", "TOP", "LEFT", "RIGHT", "BOTTOM",
    "MARGIN", "PADDING", "BORDER", "GAP", "SIZE", "FLEX", "GRID",
    "RADIUS", "OPACITY", "SCALE", "ROTATE", "TRANSLATE",
    "RED", "BLUE", "GREEN", "GRAY", "GREY", "WHITE", "BLACK", "PURPLE",
    "YELLOW", "PINK", "ORANGE", "TEAL", "CYAN", "INDIGO", "VIOLET",
    "SLATE", "ZINC", "STONE", "AMBER", "LIME", "EMERALD", "SKY", "ROSE",
    "ES", "EN", "FR", "DE", "ZH", "JA", "KO", "PT", "IT", "NL",
    "ID", "NO", "OK", "VS", "V",
    "ACCT", "TEST", "DEMO", "MOCK", "TEMP", "SAMPLE", "FOO", "BAR",
})


def _valid_ticket(ticket: str) -> bool:
    try:
        prefix, num = ticket.rsplit("-", 1)
        return prefix not in _TICKET_BLOCKLIST and len(num) >= 2
    except ValueError:
        return False


def get_branch_tickets(project_key):
    tickets = set()
    try:
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

    conv_tickets = defaultdict(set)
    branch_tickets_cache = {}
    for line in iter_all_lines(projects_dir, since_date=since):
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
        pk = line.get("project_key", "")
        if pk not in branch_tickets_cache:
            branch_tickets_cache[pk] = get_branch_tickets(pk)
        branch_tickets = branch_tickets_cache[pk]
        if branch_tickets:
            conv_tickets[line.get("project_key", "")].update(branch_tickets)

    ticket_stats = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "sessions": set()})
    untagged = {"cost": 0.0, "tokens": 0}

    for rec in iter_usage_records(projects_dir, since_date=since):
        conv_id = rec.get("requestId", rec["project_key"])
        tickets = conv_tickets.get(conv_id, set()) | conv_tickets.get(rec["project_key"], set())
        if tickets:
            for t in tickets:
                ticket_stats[t]["cost"]   += rec["cost"]
                ticket_stats[t]["tokens"] += rec["total_tokens"]
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
