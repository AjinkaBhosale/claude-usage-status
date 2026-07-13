#!/usr/bin/env python3
"""Report cache hit rates and dollar savings per project.

Usage: python statusline-cache-analyzer.py [--days 30] [--project KEY]
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, price_tokens, default_projects_dir


def main():
    parser = argparse.ArgumentParser(description='Cache efficiency report')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--project', default=None)
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
        s["input"]         += rec["input_tokens"]
        s["cache_write"]   += rec["cache_write_tokens"]
        s["cache_read"]    += rec["cache_read_tokens"]
        s["cost_actual"]   += rec["cost"]
        s["cost_no_cache"] += price_tokens(
            rec["model"],
            rec["input_tokens"] + rec["cache_write_tokens"] + rec["cache_read_tokens"],
            rec["output_tokens"], 0, 0,
        )

    if not stats:
        print("No usage data found.")
        return

    rows = sorted(stats.items(), key=lambda x: x[1]["cost_actual"], reverse=True)
    print(f"\n  Cache Efficiency · last {args.days} days")
    print(f"  {'─'*60}")
    print(f"  {'Project':<28} {'Hit Rate':>9}  {'Saved':>8}  Status")
    print(f"  {'─'*60}")
    total_saved = total_read = total_fresh = 0.0
    for key, s in rows:
        fresh = s["input"]
        reads = s["cache_read"]
        denom = fresh + reads
        rate  = reads / denom if denom else 0.0
        saved = s["cost_no_cache"] - s["cost_actual"]
        total_saved += saved
        total_read  += reads
        total_fresh += fresh
        rate_pct = f"{rate*100:.1f}%"
        if rate >= 0.5:   status = "✓ warm"
        elif rate >= 0.2: status = "~ cool"
        else:             status = "✗ cold"
        label = (key[:26] + "…") if len(key) > 27 else key
        print(f"  {label:<28} {rate_pct:>9}  ${saved:>7.2f}  {status}")
    print(f"  {'─'*60}")
    overall_denom = total_read + total_fresh
    overall_rate  = total_read / overall_denom if overall_denom else 0.0
    print(f"  Overall: {overall_rate*100:.1f}% hit rate · ${total_saved:.2f} saved via cache\n")


if __name__ == "__main__":
    main()
