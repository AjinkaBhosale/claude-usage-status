#!/usr/bin/env python3
"""Shared core for Claude Code usage scripts.

Provides RATES, pricing helpers, and transcript scanning utilities imported
by all statusline-*.py scripts. Not intended to be run directly.
"""
import glob
import json
import os
from datetime import datetime, timedelta


# Per-million-token rates: input, output, cache-write (5m), cache-read.
# Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-21).
# Keys are matched as substrings of the model ID, first match wins, so list the
# most specific key first ("opus-5-5" before "opus"). Rates differ by version, so
# a family key only covers the versions that share its price (noted per row).
RATES = {
    # Fable / Mythos: 5.1 cache reads are 0.025x input; 5 and earlier are $1.00.
    "fable-5-1":       {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 0.25},
    "mythos-5-1":      {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 0.25},
    "fable":           {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    "mythos":          {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    # Opus 5.5 is cheaper than 5; cache reads 0.05x input.
    "opus-5-5":        {"in": 4.0,  "out": 20.0, "cw": 5.0,   "cr": 0.2},
    # Opus 4.1 and 4 (retired) were $15/$75.
    "opus-4-1":        {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "opus-4-20250514": {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    # Opus 5, 4.8, 4.7, 4.6, 4.5.
    "opus":            {"in": 5.0,  "out": 25.0, "cw": 6.25,  "cr": 0.5},
    # Sonnet 5.5: cache reads 0.05x input. Sonnet 5: 0.1x.
    "sonnet-5-5":      {"in": 2.0,  "out": 10.0, "cw": 2.5,   "cr": 0.1},
    "sonnet-5":        {"in": 2.0,  "out": 10.0, "cw": 2.5,   "cr": 0.2},
    # Sonnet 4.6, 4.5, 4.
    "sonnet":          {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.3},
    # Haiku 5.5 has a long-prompt tier: prompts over 100K tokens (input + cache
    # write + cache read) are billed at the "long" rates. Applied by
    # price_tokens(..., per_message=True) only, since the threshold is per prompt.
    "haiku-5":         {"in": 0.1,  "out": 0.5,  "cw": 0.125, "cr": 0.01,
                        "long_over": 100_000,
                        "long": {"in": 0.5, "out": 2.5, "cw": 0.625, "cr": 0.05}},
    # Haiku 4.5.
    "haiku":           {"in": 1.0,  "out": 5.0,  "cw": 1.25,  "cr": 0.1},
}


def rate_for(model):
    if not model:
        return None
    for key, rate in RATES.items():
        if key in model:
            return rate
    return None


def price_tokens(model, inp, out, cw, cr, per_message=False):
    """Return cost in USD for the given token counts and model.

    Pass per_message=True only when the counts belong to a single API request;
    that enables prompt-size tiers (Haiku 5.5 over 100K). Aggregated totals
    must leave it False or one big sum would be billed at the long-prompt rate.
    """
    rate = rate_for(model)
    if not rate:
        return 0.0
    if per_message and "long" in rate and (inp + cw + cr) > rate["long_over"]:
        rate = rate["long"]
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
                        "cost":               price_tokens(model, inp, out, cw, cr, per_message=True),
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
                    yield {**obj, "project_key": project_key}
        except (OSError, ValueError):
            continue
