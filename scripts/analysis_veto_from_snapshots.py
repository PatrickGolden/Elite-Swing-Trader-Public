#!/usr/bin/env python3
"""One-off: count veto_reason from data/refresh_snapshots.jsonl (last N days)."""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

REPO = Path(__file__).resolve().parent.parent
SNAPSHOTS = REPO / "data" / "refresh_snapshots.jsonl"

def main(days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    veto_total = 0
    by_rule = {}
    lines_read = 0
    with open(SNAPSHOTS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            lines_read += 1
            try:
                row = json.loads(line)
            except Exception:
                continue
            ts = (row.get("ts") or "")[:19].replace("T", " ")
            if ts and ts < cutoff[:19].replace("T", " "):
                continue
            for c in (row.get("coins") or []):
                r = (c.get("veto_reason") or "").strip()
                if not r or r.lower() == "null":
                    continue
                veto_total += 1
                # Classify by rule prefix
                if "BEAR regime" in r or "elevated funding" in r:
                    key = "funding_bear"
                elif "Heavy exchange" in r or "exchange inflow" in r:
                    key = "whale_dump"
                elif "LTH" in r and "SOPR" in r:
                    key = "lth_distribution"
                elif "BULL euphoria" in r:
                    key = "bull_euphoria"
                elif "BULL extended" in r or "Parabolic" in r:
                    key = "bull_extended"
                elif "Narrative exhaustion" in r:
                    key = "narrative_exhaustion"
                elif "Short-term overbought" in r or "24h" in r and "chase" in r:
                    key = "short_term_pump"
                elif "Extended rally" in r or "30d" in r:
                    key = "extended_rally"
                else:
                    key = "other"
                by_rule[key] = by_rule.get(key, 0) + 1
    print(f"Snapshots (last {days}d): lines_checked={lines_read}")
    print(f"Veto count (coin-refresh pairs): {veto_total}")
    print("By rule:", json.dumps(by_rule, sort_keys=True))
    return {"veto_total": veto_total, "by_rule": by_rule, "days": days}

if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    main(d)
