#!/usr/bin/env python3
"""Aggregate Claude Code token usage and cost for "today" and "month-to-date".

Scans the transcript JSONL files under the projects dir, sums token usage from
every assistant message, prices it per-model, and buckets by local date. Writes
a small cache JSON consumed by statusline.sh.

Usage: statusline-usage.py <projects_dir> <cache_path> [lock_path]

Pricing is per million tokens (cache write = 1.25x input, cache read = 0.1x
input). Update RATES when Anthropic changes prices.
"""
import glob
import json
import os
import sys
from datetime import datetime

# Per-million-token rates: input, output, cache-write (1.25x in), cache-read (0.1x in).
# Keys are matched as substrings of the model ID — more specific keys must come first.
# Adjust these to match current Anthropic pricing for the models you use.
RATES = {
    # Opus 4.0 (claude-opus-4-20250514) and Opus 4.1 (claude-opus-4-1-20250805) —
    # these older deprecated models had higher pricing before Opus 4.5.
    "opus-4-1":        {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "opus-4-20250514": {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    # Opus 4.5 / 4.6 / 4.7 / 4.8 and any future Opus 4.x
    "opus":            {"in": 5.0,  "out": 25.0, "cw": 6.25,  "cr": 0.5},
    # Fable 5 / Mythos 5
    "fable":           {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    "mythos":          {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    # Sonnet (all versions — 4.0, 4.5, 4.6 all share the same rate)
    "sonnet":          {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.3},
    # Haiku
    "haiku":           {"in": 1.0,  "out": 5.0,  "cw": 1.25,  "cr": 0.1},
}


def rate_for(model):
    if not model:
        return None
    for key, rate in RATES.items():
        if key in model:
            return rate
    return None


def main():
    projects_dir = sys.argv[1]
    cache_path = sys.argv[2]
    lock_path = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        now_local = datetime.now().astimezone()
        today = now_local.date()
        month_start = today.replace(day=1)
        month_start_epoch = datetime(
            today.year, today.month, 1,
            tzinfo=now_local.tzinfo,
        ).timestamp()

        day_cost = mtd_cost = 0.0
        day_tok = mtd_tok = 0
        # Claude Code writes one transcript line per streamed content block, each
        # carrying the full message.usage; resumed/compacted sessions also replay
        # prior messages into new files. Dedup on (message.id, requestId) so each
        # API response is counted exactly once across every file.
        seen = set()

        for path in glob.iglob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True):
            try:
                if os.path.getmtime(path) < month_start_epoch:
                    continue
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
                            d = datetime.fromisoformat(
                                ts.replace("Z", "+00:00")
                            ).astimezone().date()
                        except ValueError:
                            continue
                        if d < month_start:
                            continue

                        inp = usage.get("input_tokens", 0) or 0
                        out = usage.get("output_tokens", 0) or 0
                        cw = usage.get("cache_creation_input_tokens", 0) or 0
                        cr = usage.get("cache_read_input_tokens", 0) or 0
                        tokens = inp + out + cw + cr

                        rate = rate_for(msg.get("model"))
                        cost = 0.0
                        if rate:
                            cost = (
                                inp * rate["in"]
                                + out * rate["out"]
                                + cw * rate["cw"]
                                + cr * rate["cr"]
                            ) / 1_000_000

                        mtd_cost += cost
                        mtd_tok += tokens
                        if d == today:
                            day_cost += cost
                            day_tok += tokens
            except (OSError, ValueError):
                continue

        result = {
            "computed_at": now_local.timestamp(),
            "day_cost": round(day_cost, 2),
            "day_tokens": day_tok,
            "mtd_cost": round(mtd_cost, 2),
            "mtd_tokens": mtd_tok,
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
