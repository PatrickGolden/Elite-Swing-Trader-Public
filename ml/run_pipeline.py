# ml/run_pipeline.py — Run backfill → 90-day backtest → missed_opportunity → suggestions; write learning report.

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Ensure repo root on path when running as python -m ml.run_pipeline
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Wire OS trust store into Python's SSL stack (must come before any
# import that creates an SSLContext, e.g. requests/urllib3/ccxt).
import tls_setup  # noqa: F401,E402

from ml.config import (
    RECOMMENDATIONS_FILE,
    DATA_DIR,
    LEARNING_REPORT_FILE,
    BACKTEST_DAYS,
)
from ml.outcome_backfill import run_backfill
from ml.missed_opportunity import analyze as analyze_missed
from ml.suggestions import build_suggestions
from ml.social_backfill import run as run_social_backfill

from safety_gate import SafetyGateError, run_unit_safety_gate


def run(save_expectancy: bool = True) -> dict:
    """
    Run full pipeline: backfill → backtest → missed opportunity → suggestions.
    Writes data/learning_report.json. Raises SafetyGateError if unit preflight fails.
    """
    if not run_unit_safety_gate():
        raise SafetyGateError("unit tests failed — learning pipeline aborted")
    run_ts = datetime.now(timezone.utc).isoformat()
    report = {
        "run_timestamp": run_ts,
        "backtest": None,
        "backtest_suggestions": [],
        "missed_opportunity": None,
        "suggestions": [],
        "backfill_stats": {},
    }

    # One fetcher instance reused across both backfill passes (recommendations + screening).
    # EnhancedDataFetcherV2.__init__ loads disk cache, so reusing avoids redundant I/O.
    _fetcher_instance = None

    def _get_prices_batched(coin_ids: list, chunk_size: int = 50) -> dict:
        nonlocal _fetcher_instance
        if _fetcher_instance is None:
            from enhanced_data_fetcher import EnhancedDataFetcherV2
            _fetcher_instance = EnhancedDataFetcherV2()
        result = {}
        for i in range(0, len(coin_ids), chunk_size):
            chunk = coin_ids[i : i + chunk_size]
            result.update(_fetcher_instance.get_current_prices_fast(chunk) or {})
        return result

    # 1. Backfill outcomes
    try:
        report["backfill_stats"] = run_backfill(_get_prices_batched)
    except Exception as e:
        report["backfill_error"] = str(e)
        report["backfill_stats"] = {"recommendations_updated": 0, "screening_updated": 0}

    # 1b. Backfill social signal (volume_spike_ratio) — runs after outcome backfill so resolved entries are eligible
    try:
        social_stats = run_social_backfill()
        report["backfill_stats"]["social_entries_updated"] = social_stats.get("entries_updated", 0)
        report["backfill_stats"]["social_coins_fetched"] = social_stats.get("coins_fetched", 0)
    except Exception as e:
        logger.warning("social_backfill step failed: %s", e)
        report["backfill_stats"]["social_entries_updated"] = 0

    # 2. Run 90-day backtest
    try:
        from backtester import Backtester
        bt = Backtester(recommendations_file=str(RECOMMENDATIONS_FILE))
        bt_report = bt.run_walk_forward_backtest(days=BACKTEST_DAYS)
        report["backtest"] = {
            "period": bt_report.get("period"),
            "total_signals": bt_report.get("total_signals"),
            "sharpe_passed": bt_report.get("sharpe_passed"),
            "by_regime": bt_report.get("by_regime"),
            "by_tier": bt_report.get("by_tier"),
            "by_regime_and_tier": bt_report.get("by_regime_and_tier"),
            "walk_forward": bt_report.get("walk_forward"),
        }
        report["backtest_suggestions"] = bt.get_optimization_suggestions(report=bt_report)
        if save_expectancy:
            try:
                from monitoring import save_backtest_expectancy
                save_backtest_expectancy(bt_report)
            except Exception:
                pass
    except Exception as e:
        report["backtest_error"] = str(e)

    # 3. Missed-opportunity analysis
    try:
        report["missed_opportunity"] = analyze_missed()
    except Exception as e:
        report["missed_opportunity_error"] = str(e)
        report["missed_opportunity"] = {"summary": {"recommendations_missed": 0, "screening_missed": 0}}

    # 3b. Live attribution: regime x tier and badge cross-tabs from the trade journal,
    # using the same key shape as backtest (by_regime_and_tier) for apples-to-apples
    # comparison against the backtest block above.
    try:
        from model_attribution import get_model_attribution
        live_attr = get_model_attribution(days=BACKTEST_DAYS)
        report["live_attribution"] = {
            "period_days": live_attr.get("period_days"),
            "by_model": live_attr.get("by_model"),
            "by_regime_and_tier": live_attr.get("by_regime_and_tier"),
            "by_signal_badge": live_attr.get("by_signal_badge"),
            "veto_performance": live_attr.get("veto_performance"),
        }
    except Exception as e:
        report["live_attribution_error"] = str(e)
        report["live_attribution"] = {}

    # 3c. Paper attribution (counterfactual for live-skipped MODERATE+ signals)
    try:
        from paper_evidence import build_paper_attribution_report, summarize_policy_skips
        report["paper_attribution"] = build_paper_attribution_report(days=BACKTEST_DAYS)
        report["policy_skip_summary"] = summarize_policy_skips()
    except Exception as e:
        logger.warning("paper_attribution step failed: %s", e)
        report["paper_attribution_error"] = str(e)
        report["paper_attribution"] = {}
        report["policy_skip_summary"] = {}

    # 4. Build merged suggestions
    missed = report.get("missed_opportunity") or {}
    report["suggestions"] = build_suggestions(
        missed,
        report.get("backtest_suggestions") or [],
    )

    # 5. Write report
    import safe_json
    safe_json.save(str(LEARNING_REPORT_FILE), report, default=str)

    return report


def main():
    try:
        report = run(save_expectancy=True)
    except SafetyGateError:
        print("Safety gate failed — aborting learning pipeline", file=sys.stderr)
        return 1
    sharpe = "N/A"
    if report.get("backtest"):
        sharpe = "Yes" if report["backtest"].get("sharpe_passed") else "No"
    n_sugg = len(report.get("suggestions") or [])
    path = LEARNING_REPORT_FILE
    print(f"  Backtest Sharpe passed: {sharpe}; {n_sugg} suggestion(s). See {path}")
    if report.get("backfill_stats"):
        print(f"  Backfill: {report['backfill_stats'].get('recommendations_updated', 0)} recs, {report['backfill_stats'].get('screening_updated', 0)} screening.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
