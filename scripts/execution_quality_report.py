#!/usr/bin/env python3
"""
Execution Quality Review — parse data/execution_audit.jsonl and data/execution_log.jsonl.
Output: slippage (avg, median, P90) vs backtest band 0.2–0.5%; fill rate; total fees.
Run from repo root: python scripts/execution_quality_report.py [days]
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

REPO = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO / "data" / "execution_audit.jsonl"
LOG_PATH = REPO / "data" / "execution_log.jsonl"

BACKTEST_SLIPPAGE_PCT_LO, BACKTEST_SLIPPAGE_PCT_HI = 0.2, 0.5  # backtester.py
BPS_PER_PCT = 100.0  # 1% = 100 bps


def main(days: int = 30):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z") if days else None

    # --- Audit: fills, fees, slippage_bps ---
    fills = []
    fees_total = 0.0
    slippage_bps_list = []
    by_action = {}
    if AUDIT_PATH.exists():
        with open(AUDIT_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                ts = entry.get("ts") or ""
                if since and ts < since:
                    continue
                action = entry.get("action") or "unknown"
                by_action[action] = by_action.get(action, 0) + 1
                if entry.get("fill_price") is not None:
                    fills.append(entry)
                fee = entry.get("fee_usd")
                if fee is not None:
                    fees_total += float(fee)
                sbps = entry.get("slippage_bps")
                if sbps is not None:
                    bps = float(sbps)
                    # Exclude absurd outliers (e.g. pre_trade_mid=0 or wrong scale from tests)
                    if -500 <= bps <= 500:
                        slippage_bps_list.append(bps)

    # --- Log: status counts (filled / partial / error) ---
    status_counts = {"filled": 0, "partial": 0, "error": 0, "other": 0}
    order_count = 0
    if LOG_PATH.exists():
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                ts = entry.get("ts") or entry.get("timestamp") or ""
                if since and ts < since:
                    continue
                order_count += 1
                s = (entry.get("status") or "").strip().lower()
                if s in ("filled", "closed", "done"):
                    status_counts["filled"] += 1
                elif s == "partial":
                    status_counts["partial"] += 1
                elif s == "error":
                    status_counts["error"] += 1
                elif s:
                    status_counts["other"] += 1

    # --- Output ---
    print(f"Execution Quality Report (last {days} days)")
    print("=" * 60)
    print(f"Audit file: {AUDIT_PATH.name}  |  Log file: {LOG_PATH.name}")
    print()
    print("Slippage (from audit entries with slippage_bps):")
    if slippage_bps_list:
        slippage_bps_list.sort()
        n = len(slippage_bps_list)
        avg_bps = sum(slippage_bps_list) / n
        median_bps = slippage_bps_list[n // 2] if n else 0
        p90_idx = int(n * 0.90) if n else 0
        p90_bps = slippage_bps_list[min(p90_idx, n - 1)] if n else 0
        avg_pct = avg_bps / BPS_PER_PCT
        median_pct = median_bps / BPS_PER_PCT
        p90_pct = p90_bps / BPS_PER_PCT
        print(f"  Count:    {n}")
        print(f"  Avg:      {avg_bps:.2f} bps  ({avg_pct:.3f}%)")
        print(f"  Median:   {median_bps:.2f} bps  ({median_pct:.3f}%)")
        print(f"  P90:      {p90_bps:.2f} bps  ({p90_pct:.3f}%)")
        print(f"  Backtest band: {BACKTEST_SLIPPAGE_PCT_LO}–{BACKTEST_SLIPPAGE_PCT_HI}%  ({BACKTEST_SLIPPAGE_PCT_LO*BPS_PER_PCT:.0f}–{BACKTEST_SLIPPAGE_PCT_HI*BPS_PER_PCT:.0f} bps)")
        if median_bps > BACKTEST_SLIPPAGE_PCT_HI * BPS_PER_PCT:
            print("  >>> FLAG: Live median slippage above backtest upper bound.")
        else:
            print("  OK: Within or below backtest band.")
    else:
        print("  No slippage_bps data in window.")
    print()
    print("Fees:")
    print(f"  Total fee_usd (audit): ${fees_total:.4f}")
    print("  Compare to Coinbase statement for same period.")
    print()
    print("Fill rate (execution_log.jsonl entries with 'status'):")
    total_status = sum(status_counts.values())
    if total_status > 0:
        print(f"  Orders/entries in window: {order_count}")
        for k, v in status_counts.items():
            if v:
                print(f"    {k}: {v}")
        filled = status_counts["filled"]
        fill_rate = (filled / total_status * 100) if total_status else 0
        print(f"  Fill rate (filled / total with status): {fill_rate:.1f}%")
        if status_counts["partial"]:
            print("  >>> Note: Partial fills may cause position size drift; review manually.")
    else:
        print(f"  Order count (any log line in window): {order_count}")
        print("  No 'status' key in log entries (log format may not include status).")
    print()
    print("By action (audit):", json.dumps(by_action, sort_keys=True))
    print()
    print("Fill count (audit rows with fill_price):", len(fills))
    return {
        "days": days,
        "slippage_bps_avg": sum(slippage_bps_list) / len(slippage_bps_list) if slippage_bps_list else None,
        "slippage_bps_median": slippage_bps_list[len(slippage_bps_list) // 2] if slippage_bps_list else None,
        "total_fees_usd": round(fees_total, 4),
        "fill_count": len(fills),
        "order_count": order_count,
        "status_counts": status_counts,
        "by_action": by_action,
    }


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    main(d)
