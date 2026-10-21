#!/usr/bin/env python3
"""Shared core for Claude Code usage scripts.

Provides RATES, pricing helpers, and transcript scanning utilities imported
by all statusline-*.py scripts. Not intended to be run directly.
"""
import glob
import json
import os
from datetime import datetime, timedelta


# Per-million-token rates: input, output, cache-write, cache-read.
# Keys matched as substrings of the model ID — more specific keys first.
RATES = {
    "opus-4-1":        {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "opus-4-20250514": {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "opus":            {"in": 5.0,  "out": 25.0, "cw": 6.25,  "cr": 0.5},
    "fable":           {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    "mythos":          {"in": 10.0, "out": 50.0, "cw": 12.5,  "cr": 1.0},
    "sonnet":          {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.3},
    "haiku":           {"in": 1.0,  "out": 5.0,  "cw": 1.25,  "cr": 0.1},
}


def rate_for(model):
    if not model:
        return None
    for key, rate in RATES.items():
        if key in model:
            return rate
    return None


def price_tokens(model, inp, out, cw, cr):
    """Return cost in USD for the given token counts and model."""
    rate = rate_for(model)
    if not rate:
        return 0.0
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
            project_key = os.path.relpath(path, projects_dir).split(os.sep)[0]
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
                        "cost":               price_tokens(model, inp, out, cw, cr),
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
            project_key = os.path.relpath(path, projects_dir).split(os.sep)[0]
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
