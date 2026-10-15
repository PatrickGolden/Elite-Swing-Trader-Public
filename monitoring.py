# monitoring.py — Drift monitoring
# Compares live win rate by regime to backtest expectancy.
# Emergency stop: disable Coinbase API; no in-app kill switch.

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
EXPECTANCY_FILE = DATA_DIR / "monitoring_expectancy.json"


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_backtest_expectancy():
    """
    Load expected win rate (and sample size) by regime from last saved backtest report.
    Returns None if file missing or invalid.
    """
    import safe_json
    data = safe_json.load(str(EXPECTANCY_FILE))
    if data is None:
        return None
    return data.get("by_regime"), data.get("updated")


def save_backtest_expectancy(report: dict):
    """Save backtest report by_regime (and updated) for drift checks."""
    by_regime = report.get("by_regime") or {}
    _ensure_data_dir()
    import safe_json
    ok = safe_json.save(str(EXPECTANCY_FILE), {
        "by_regime": by_regime,
        "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    if not ok:
        raise RuntimeError("Failed to save backtest expectancy")


def get_live_stats_by_regime(recommendations: list, days: int = 30):
    """
    From consensus recommendations with outcome_7d, compute win_rate_pct and trades by regime.
    Returns dict: { "BULL": {"win_rate_pct": 65.0, "trades": 20}, ... }.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_regime = {}
    for rec in recommendations:
        ts = rec.get("timestamp")
        if not ts or rec.get("outcome_7d") is None:
            continue
        try:
            t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        t_utc = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
        if t_utc < cutoff:
            continue
        regime = rec.get("market_regime") or "SIDEWAYS"
        if regime not in by_regime:
            by_regime[regime] = {"wins": 0, "trades": 0}
        by_regime[regime]["trades"] += 1
        try:
            if float(rec["outcome_7d"]) > 0:
                by_regime[regime]["wins"] += 1
        except (TypeError, ValueError):
            pass  # Non-numeric outcome_7d — count as trade but not a win
    result = {}
    for regime, v in by_regime.items():
        n = v["trades"]
        result[regime] = {
            "win_rate_pct": round((v["wins"] / n * 100), 1) if n else 0,
            "trades": n,
        }
    return result


def check_for_drift(live_stats: dict, current_regime: str, threshold_pct: float = 12.0, min_trades: int = 5):
    """
    Compare live win rate by regime to backtest expectancy.
    expectancy: (by_regime_dict, updated_str) from load_backtest_expectancy(), or (None, None).
    If live win rate for current_regime is more than threshold_pct below expected (with min_trades), return (True, reason).
    Otherwise return (False, "").
    """
    raw = load_backtest_expectancy()
    if raw is None:
        return False, ""
    by_regime, _ = raw
    if not by_regime or current_regime not in by_regime:
        return False, ""
    expected = by_regime[current_regime]
    expected_wr = expected.get("win_rate_pct")
    if expected_wr is None:
        return False, ""
    live = live_stats.get(current_regime) or {}
    live_wr = live.get("win_rate_pct")
    live_trades = live.get("trades", 0)
    if live_trades < min_trades:
        return False, ""
    if live_wr is None:
        return False, ""
    if live_wr < expected_wr - threshold_pct:
        reason = (
            f"Drift: live win rate {live_wr}% (n={live_trades}) for regime {current_regime} "
            f"is >{threshold_pct}% below backtest expectancy {expected_wr}%."
        )
        return True, reason
    return False, ""
