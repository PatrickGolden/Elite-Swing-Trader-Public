#!/usr/bin/env python3
"""
Diff optimizer recommendations vs live_thresholds.json.
Reads the last cycle from data/optimizer_log.json and live_thresholds.json,
then prints parameters where recommended value differs from live.
Run after each optimizer run to review drift.
"""
import json
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
OPTIMIZER_LOG_FILE = os.path.join(DATA_DIR, "optimizer_log.json")
LIVE_THRESHOLDS_PATH = os.path.join(PROJECT_DIR, "live_thresholds.json")


def load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def main():
    live = load_json(LIVE_THRESHOLDS_PATH, {})
    # Skip metadata keys
    meta = {"updated", "updated_by"}
    live = {k: v for k, v in live.items() if k not in meta}

    log = load_json(OPTIMIZER_LOG_FILE, [])
    if not log:
        print("No optimizer log found. Run optimizer first.")
        return 0

    last = log[-1]
    recommended = {}
    for r in last.get("loss_based_recommendations", []):
        p = r.get("parameter")
        if p is not None and "suggested_value" in r:
            recommended[p] = r["suggested_value"]
    for r in last.get("pending_review", []):
        p = r.get("parameter")
        if p is not None and "suggested_value" in r:
            recommended.setdefault(p, r["suggested_value"])

    if not recommended:
        print("Last cycle has no recommendations to diff.")
        return 0

    cycle = last.get("cycle", "?")
    date = (last.get("snapshot") or {}).get("date", "?")
    print(f"Cycle #{cycle} ({date}) — recommended vs live")
    print("-" * 60)

    diffs = []
    for param in sorted(recommended.keys()):
        rec_val = recommended[param]
        live_val = live.get(param)
        if live_val != rec_val:
            diffs.append((param, live_val, rec_val))

    if not diffs:
        print("No differences: live matches last cycle recommendations.")
        return 0

    for param, live_val, rec_val in diffs:
        print(f"  {param}: live={live_val}  recommended={rec_val}")
    print(f"\nTotal: {len(diffs)} parameter(s) differ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
