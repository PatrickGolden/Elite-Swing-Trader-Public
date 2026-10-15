# =============================================================================
# gate_watch_report.py — "was the risk-on gate right?" (STRATEGY.md v1.6d)
#
# The cohorts have been deliberately FLAT while BTC is risk-off. This report
# grades that decision with live data, on a schedule, instead of vibes:
#
#   1. Regime scoreboard — for every day since the watch phase began (2026-06-11),
#      the rule's call (risk-on/off) vs BTC's realized forward return. The gate is
#      "earning its keep" while the market it kept us out of goes nowhere or down.
#   2. Suppressed-signal outcomes — lean-pruned BUYs (logged by the refresh loop
#      since v1.6d) with forward returns net of measured costs: the trades we
#      didn't take. Empty until instrumentation accrues; says so honestly.
#
# OBSERVATIONAL ONLY: this report never changes behavior. It exists so that if
# the gate is costing real money, the evidence accumulates in the open — and if
# it is saving money, the funding decision can cite it. Honesty guard: one
# risk-off stretch is ~one independent observation; conclusions are labeled
# accordingly (no significance claims from n≈1).
#
# Run:  python scripts/gate_watch_report.py        (writes data/gate_watch_report.json)
# =============================================================================

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tls_setup  # noqa: F401

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BTC_CACHE = os.path.join(PROJECT, "data", "historical_cache", "daily", "BTC.json")
SHADOW = os.path.join(PROJECT, "data", "shadow_skips.jsonl")
OUT = os.path.join(PROJECT, "data", "gate_watch_report.json")

WATCH_START = "2026-06-11"   # paper-only overhaul (STRATEGY.md v1.4)
MA_DAYS, BAND_PCT = 200, 1.5  # incumbent rule (AUDIT-2026Q3-3: PLATEAU)
MEASURED_RT_COST_PCT = 2.4


def _refresh_btc_cache_if_stale(max_age_days: int = 2) -> Optional[str]:
    """The replay cache is only written when a backtest runs, so a weekly report
    reading it silently grades a stale window (found 2026-08-08: scoreboard ended
    Jul 2, five weeks behind). Top it up from the same CCXT path the replay uses.
    Returns a note if refreshed/failed, else None."""
    try:
        with open(BTC_CACHE, encoding="utf-8") as f:
            rows = json.load(f)
        newest = datetime.fromtimestamp(int(rows[-1][0]) / 1000, tz=timezone.utc)
        age_d = (datetime.now(timezone.utc) - newest).days
        if age_d <= max_age_days:
            return None
    except Exception:
        age_d = None
    try:
        from stage1c_replay import _make_exchange, fetch_daily_ohlcv, _save_daily_cache
        ohlcv = fetch_daily_ohlcv(_make_exchange(), "BTC/USD")
        if ohlcv and len(ohlcv) > 200:
            _save_daily_cache("BTC", ohlcv)
            return f"BTC daily cache refreshed (was {age_d}d stale)"
        return f"BTC cache refresh returned too few bars; using stale data ({age_d}d)"
    except Exception as e:
        return f"BTC cache refresh failed ({e}); using stale data ({age_d}d)"


def _load_btc() -> Dict[str, Any]:
    note = _refresh_btc_cache_if_stale()
    with open(BTC_CACHE, encoding="utf-8") as f:
        rows = [r for r in json.load(f) if r and len(r) >= 5 and r[4] and float(r[4]) > 0]
    closes = [float(r[4]) for r in rows]
    dates = [datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for r in rows]
    return {"closes": closes, "dates": dates, "by_date": {d: i for i, d in enumerate(dates)},
            "cache_note": note}


def _risk_on(closes: List[float], d: int) -> Optional[bool]:
    if d < MA_DAYS:
        return None
    ma = sum(closes[d - MA_DAYS + 1: d + 1]) / MA_DAYS
    ch30 = (closes[d] / closes[d - 30] - 1.0) * 100 if d >= 30 else 0.0
    return closes[d] > ma * (1 + BAND_PCT / 100.0) and ch30 > 0


def regime_scoreboard(btc: Dict[str, Any], start: str) -> Dict[str, Any]:
    closes, dates = btc["closes"], btc["dates"]
    i0 = next((i for i, d in enumerate(dates) if d >= start), None)
    if i0 is None:
        return {"error": f"no BTC data after {start}"}
    days = []
    for d in range(i0, len(closes)):
        ro = _risk_on(closes, d)
        fwd7 = ((closes[min(d + 7, len(closes) - 1)] / closes[d]) - 1.0) * 100 if d + 1 < len(closes) else None
        days.append({"date": dates[d], "risk_on": ro, "fwd7_pct": round(fwd7, 2) if fwd7 is not None else None})
    off = [x for x in days if x["risk_on"] is False and x["fwd7_pct"] is not None]
    on = [x for x in days if x["risk_on"] is True and x["fwd7_pct"] is not None]
    btc_period = (closes[-1] / closes[i0] - 1.0) * 100
    return {
        "window": f"{dates[i0]} to {dates[-1]}",
        "days_observed": len(days),
        "risk_off_days": sum(1 for x in days if x["risk_on"] is False),
        "risk_on_days": sum(1 for x in days if x["risk_on"] is True),
        "btc_return_over_window_pct": round(btc_period, 2),
        "avg_fwd7_on_risk_off_days_pct": (round(sum(x["fwd7_pct"] for x in off) / len(off), 2) if off else None),
        "avg_fwd7_on_risk_on_days_pct": (round(sum(x["fwd7_pct"] for x in on) / len(on), 2) if on else None),
        "gate_verdict_so_far": (
            "SAVED us from a falling/flat market (staying out was right)"
            if btc_period <= 0 else
            f"COST us {round(btc_period, 1)}% of BTC upside so far (watch if this grows)"
        ),
        "honesty_note": "Overlapping fwd-7d windows and a single regime stretch: ~1 independent "
                        "observation. Direction is informative; significance is not claimable.",
    }


def gate_discrimination(start: str) -> Dict[str, Any]:
    """Does the NON-regime half of the lean gate discriminate, or is it vestigial?

    risk_on has been False on 100% of prunes, so the path allowlist and the 30d
    momentum floor have never been the binding constraint. This grades them
    counterfactually: among pruned candidates, split by whether path/momentum
    WOULD have allowed entry (with risk_on forced True) and compare realized
    forward returns. A discriminating rule should show allowed > blocked.
    """
    from lean_policy import LEAN_ALLOWED_PATHS

    rows: List[Dict[str, Any]] = []
    try:
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("ts") or "") >= start and r.get("live_skip_reason") == "lean_pruned":
                    rows.append(r)
    except FileNotFoundError:
        pass
    if not rows:
        return {"n": 0, "note": "no lean-pruned rows yet"}

    def _mean(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    # Path dimension — answerable from historical rows (quant_path always logged)
    by_path: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        p = r.get("quant_path") or "unknown"
        b = by_path.setdefault(p, {"n": 0, "rets": [], "in_allowlist": p in LEAN_ALLOWED_PATHS})
        b["n"] += 1
        if r.get("forward_return_7d") is not None:
            b["rets"].append(float(r["forward_return_7d"]))
    for b in by_path.values():
        b["mean_fwd7_pct"] = _mean(b.pop("rets"))

    allow_rets = [float(r["forward_return_7d"]) for r in rows
                  if r.get("forward_return_7d") is not None
                  and (r.get("quant_path") or "") in LEAN_ALLOWED_PATHS]
    block_rets = [float(r["forward_return_7d"]) for r in rows
                  if r.get("forward_return_7d") is not None
                  and (r.get("quant_path") or "") not in LEAN_ALLOWED_PATHS]

    # Full shadow verdict — only on rows logged after the 2026-08-08 instrumentation
    shadow = [r for r in rows if r.get("lean_shadow_allowed") is not None]
    sh_allow = [float(r["forward_return_7d"]) for r in shadow
                if r.get("lean_shadow_allowed") and r.get("forward_return_7d") is not None]
    sh_block = [float(r["forward_return_7d"]) for r in shadow
                if not r.get("lean_shadow_allowed") and r.get("forward_return_7d") is not None]

    return {
        "n": len(rows),
        "path_check": {
            "would_pass": sum(1 for r in rows if (r.get("quant_path") or "") in LEAN_ALLOWED_PATHS),
            "would_block": sum(1 for r in rows if (r.get("quant_path") or "") not in LEAN_ALLOWED_PATHS),
            "mean_fwd7_allowed_pct": _mean(allow_rets),
            "mean_fwd7_blocked_pct": _mean(block_rets),
            "discriminates": (
                None if not (allow_rets and block_rets)
                else _mean(allow_rets) > _mean(block_rets)
            ),
            "by_path": by_path,
        },
        "full_shadow_verdict": {
            "rows_instrumented": len(shadow),
            "would_allow": len(sh_allow), "would_block": len(sh_block),
            "mean_fwd7_allowed_pct": _mean(sh_allow),
            "mean_fwd7_blocked_pct": _mean(sh_block),
            "note": ("path+momentum counterfactual; instrumented 2026-08-08 — "
                     "needs ~7d of rows before returns are gradeable"),
        },
        "honesty_note": ("Forward returns here are unadjusted for market beta: the whole "
                         "window is risk-off, so ALL buckets skew negative. Compare buckets "
                         "to each other, never to zero."),
    }


def suppressed_signals(start: str) -> Dict[str, Any]:
    rows = []
    try:
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("ts") or "") >= start and r.get("live_skip_reason") == "lean_pruned":
                    rows.append(r)
    except FileNotFoundError:
        pass
    return {
        "lean_pruned_signals_logged": len(rows),
        "distinct_symbols": len({r.get("symbol") for r in rows}),
        "note": ("instrumentation added 2026-07-02 (v1.6d) — stream was empty before; "
                 "forward-return grading becomes possible ~7d after rows accrue, "
                 f"net of measured {MEASURED_RT_COST_PCT}% round-trip costs"),
    }


def main() -> None:
    btc = _load_btc()
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule": {"ma_days": MA_DAYS, "band_pct": BAND_PCT,
                 "robustness": "PLATEAU (AUDIT-2026Q3-3)"},
        "btc_cache_note": btc.get("cache_note"),
        "regime_scoreboard": regime_scoreboard(btc, WATCH_START),
        "suppressed_signals": suppressed_signals(WATCH_START),
        "gate_discrimination": gate_discrimination(WATCH_START),
        "kind": "observational — never changes behavior",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if os.path.exists(OUT):
        os.replace(OUT, f"{OUT}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    sb = out["regime_scoreboard"]
    print("=" * 70)
    print("GATE WATCH — was staying flat the right call?")
    print("=" * 70)
    for k in ("window", "days_observed", "risk_off_days", "risk_on_days",
              "btc_return_over_window_pct", "avg_fwd7_on_risk_off_days_pct",
              "gate_verdict_so_far"):
        print(f"  {k}: {sb.get(k)}")
    ss = out["suppressed_signals"]
    print(f"  suppressed signals logged: {ss['lean_pruned_signals_logged']} "
          f"({ss['distinct_symbols']} symbols)")
    print(f"  note: {sb['honesty_note']}")
    gd = out.get("gate_discrimination") or {}
    if gd.get("n"):
        pc = gd["path_check"]
        print(f"\nGATE DISCRIMINATION (n={gd['n']} pruned candidates)")
        print(f"  path allowlist: would pass {pc['would_pass']}, block {pc['would_block']}")
        print(f"  mean fwd-7d: allowed {pc['mean_fwd7_allowed_pct']}% vs blocked "
              f"{pc['mean_fwd7_blocked_pct']}%  -> discriminates: {pc['discriminates']}")
        fs = gd["full_shadow_verdict"]
        print(f"  path+momentum shadow: {fs['rows_instrumented']} rows instrumented "
              f"(allow {fs['would_allow']} / block {fs['would_block']})")
        print(f"  {gd['honesty_note']}")


if __name__ == "__main__":
    main()
