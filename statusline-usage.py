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
