#!/usr/bin/env python3
"""Detect expensive anti-patterns in Claude Code session transcripts.

Usage: python statusline-waste-detector.py [--days 7] [--json]
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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

    session_file_counts = defaultdict(lambda: defaultdict(int))
    session_turn_counts = defaultdict(int)
    for line in iter_all_lines(projects_dir, since_date=since):
        msg = line.get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        ts = line.get("timestamp", "")
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
        except Exception:
            d = since
        sess_key = (line["project_key"], str(d))
        if line.get("type") == "user":
            session_turn_counts[sess_key] += 1
            for fp in FILE_PATH_RE.findall(str(content)):
                session_file_counts[sess_key][fp] += 1

    rereads = [
        {"session": sess, "file": fp, "count": count}
        for sess, files in session_file_counts.items()
        for fp, count in files.items()
        if count >= 5
    ]

    session_cache = defaultdict(int)
    session_cost  = defaultdict(float)
    for rec in iter_usage_records(projects_dir, since_date=since):
        sess_key = (rec["project_key"], str(rec["date"]))
        session_cache[sess_key] += rec["cache_read_tokens"]
        session_cost[sess_key]  += rec["cost"]

    cold_sessions = [
        {"session": k, "turns": session_turn_counts[k], "cost": session_cost[k]}
        for k, turns in session_turn_counts.items()
        if turns > 10 and session_cache.get(k, 0) == 0
    ]

    trivial_opus = []
    opus_waste   = 0.0
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
        sum(0.02 * r["count"] for r in rereads)
        + sum(s["cost"] * 0.3 for s in cold_sessions)
        + opus_waste
    )

    if args.as_json:
        print(json.dumps({
            "rereads": [{"file": r["file"], "count": r["count"],
                         "session": list(r["session"])} for r in rereads],
            "cold_sessions": [{"session": list(s["session"]), "turns": s["turns"],
                               "cost": round(s["cost"], 4)} for s in cold_sessions],
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
