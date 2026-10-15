"""Report position id gaps and live vs paper vs test counts (read-only).

Usage: python scripts/diagnose_positions_gap.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSITIONS = os.path.join(ROOT, "positions.json")


def main() -> int:
    with open(POSITIONS, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        print("positions.json is not a list")
        return 1

    ids = [p.get("id") for p in rows if isinstance(p, dict)]
    ids_valid = [i for i in ids if i is not None]
    mx = max(ids_valid)
    full = set(range(1, mx + 1))
    have = set(ids_valid)
    missing = sorted(full - have)

    def bucket(p: dict) -> str:
        if (p.get("source") or "") == "paper_trade":
            return "paper_trade"
        if p.get("is_test"):
            return "test"
        return "live"

    ctr = Counter(bucket(p) for p in rows if isinstance(p, dict))

    band = [
        p
        for p in rows
        if isinstance(p, dict) and p.get("id") is not None and 96 <= int(p["id"]) <= 162
    ]
    band_ctr = Counter(bucket(p) for p in band)

    print("positions:", len(rows), "max_id:", mx)
    print("missing_id_count:", len(missing), "(expected legacy gap 3..37 if early ids unused)")
    print("missing_ids:", missing[:50], ("..." if len(missing) > 50 else ""))
    print("overall_mode_counts:", dict(ctr))
    print("ids_96_162_mode_counts:", dict(band_ctr), "rows_in_band:", len(band))

    today = date.today().isoformat()
    hit_entry = sum(
        1
        for p in rows
        if isinstance(p, dict) and (p.get("entry_time") or "").startswith(today)
    )
    hit_exit = sum(
        1
        for p in rows
        if isinstance(p, dict) and (p.get("exit_time") or "").startswith(today)
    )
    hit_lr = sum(
        1
        for p in rows
        if isinstance(p, dict) and (p.get("last_refreshed") or "").startswith(today)
    )
    print(f"rows_entry_time_{today}:", hit_entry)
    print(f"rows_exit_time_{today}:", hit_exit)
    print(f"rows_last_refreshed_{today}:", hit_lr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
