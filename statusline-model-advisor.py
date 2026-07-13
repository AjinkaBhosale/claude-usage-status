#!/usr/bin/env python3
"""Identify Opus sessions that Sonnet could have handled, estimate savings.

Usage: python statusline-model-advisor.py [--days 30] [--threshold-tokens 5000]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statusline_core import iter_usage_records, price_tokens, default_projects_dir

SONNET_MODEL = "claude-sonnet-4-6"


def main():
    parser = argparse.ArgumentParser(description='Model advisor')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--threshold-tokens', type=int, default=5000)
    args = parser.parse_args()

    since = (datetime.now().astimezone() - timedelta(days=args.days)).date()
    projects_dir = default_projects_dir()

    candidates = []
    total_actual = total_sonnet = 0.0

    for rec in iter_usage_records(projects_dir, since_date=since):
        if "opus" not in rec["model"].lower():
            continue
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
