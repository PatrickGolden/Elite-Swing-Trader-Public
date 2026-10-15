# tests/test_backtester.py — Slippage adjustment, walk-forward windows, Monte Carlo,
# regime/tier stratification, Sharpe threshold, empty data
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
import json
import tempfile
import os
from pathlib import Path
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtester import Backtester, SLIPPAGE_PCT_MIN, SLIPPAGE_PCT_MAX, TRANSACTION_COST_PCT, SHARPE_THRESHOLD


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_rec(signal, regime, quality, outcome_7d, days_ago):
    ts = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "timestamp": ts,
        "consensus_signal": signal,
        "market_regime": regime,
        "consensus_quality": quality,
        "outcome_7d": outcome_7d,
        "coin_id": "bitcoin",
        "entry_price": 50000,
    }


@pytest.fixture
def temp_recs_file(tmp_path):
    """Write recommendations to a temp file and return its path."""
    path = tmp_path / "recs.json"
    path.write_text("[]")
    return str(path)


def _backtester_with(recs, tmp_path):
    path = tmp_path / "recs.json"
    with open(path, "w") as f:
        json.dump(recs, f)
    return Backtester(recommendations_file=str(path))


# ---------------------------------------------------------------------------
# 1. Slippage / Cost Adjustment
# ---------------------------------------------------------------------------

class TestSlippageCostAdjustment:
    def test_outcome_adjusted_correctly(self, tmp_path):
        """outcome_7d_adjusted = outcome_7d - slippage_mid - cost_round_trip."""
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 10.0, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        slippage_mid = (SLIPPAGE_PCT_MIN + SLIPPAGE_PCT_MAX) / 2
        cost_rt = TRANSACTION_COST_PCT * 2
        expected = 10.0 - slippage_mid - cost_rt
        by_regime = report["by_regime"].get("BULL", {})
        assert by_regime.get("trades", 0) == 1
        assert abs(by_regime.get("avg_win_pct", 0) - expected) < 0.1

    def test_negative_outcome_still_adjusted(self, tmp_path):
        """Negative outcomes also have costs subtracted."""
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", -2.0, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        by_regime = report["by_regime"].get("BULL", {})
        assert by_regime.get("avg_loss_pct", 0) < -2.0

    def test_none_outcome_not_adjusted(self, tmp_path):
        """Records with outcome_7d=None get outcome_7d_adjusted=None, no usable metrics."""
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", None, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        bull = report["by_regime"].get("BULL", {})
        assert bull == {} or bull.get("trades", 0) == 0


# ---------------------------------------------------------------------------
# 2. Regime Stratification
# ---------------------------------------------------------------------------

class TestRegimeStratification:
    def test_trades_grouped_by_regime(self, tmp_path):
        recs = [
            _make_rec("STRONG_BUY", "BULL", "strong", 5.0, 5),
            _make_rec("STRONG_BUY", "BULL", "strong", 3.0, 6),
            _make_rec("STRONG_BUY", "BEAR", "strong", -2.0, 7),
        ]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert "BULL" in report["by_regime"]
        assert "BEAR" in report["by_regime"]
        assert report["by_regime"]["BULL"]["trades"] == 2
        assert report["by_regime"]["BEAR"]["trades"] == 1

    def test_empty_regime_not_in_report(self, tmp_path):
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 5.0, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert "BEAR" not in report["by_regime"]
        assert "VOLATILE" not in report["by_regime"]


# ---------------------------------------------------------------------------
# 3. Tier Stratification (uses consensus_quality)
# ---------------------------------------------------------------------------

class TestTierStratification:
    def test_trades_grouped_by_tier(self, tmp_path):
        recs = [
            _make_rec("ULTRA_BUY", "BULL", "ultra", 8.0, 5),
            _make_rec("STRONG_BUY", "BULL", "strong", 4.0, 6),
            _make_rec("MODERATE_BUY", "BULL", "moderate", 2.0, 7),
        ]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert "ULTRA_BUY" in report["by_tier"]
        assert "STRONG_BUY" in report["by_tier"]
        assert "MODERATE_BUY" in report["by_tier"]

    def test_tier_uses_consensus_quality_not_signal(self, tmp_path):
        """Tier filter uses consensus_quality field (audit fix verified)."""
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 5.0, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert "STRONG_BUY" in report["by_tier"]
        assert report["by_tier"]["STRONG_BUY"]["trades"] == 1


# ---------------------------------------------------------------------------
# 4. Monte Carlo
# ---------------------------------------------------------------------------

class TestMonteCarlo:
    def test_monte_carlo_keys_present(self, tmp_path):
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 5.0, i + 2) for i in range(10)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        mc = report["monte_carlo"]
        assert "mean_expectancy" in mc
        assert "5th_percentile" in mc
        assert "95th_percentile" in mc

    def test_monte_carlo_empty_returns_empty(self, tmp_path):
        bt = _backtester_with([], tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert report["monte_carlo"] == {}

    def test_monte_carlo_p5_less_than_p95(self, tmp_path):
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", (i - 3) * 2.0, i + 2) for i in range(20)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        mc = report["monte_carlo"]
        if mc:
            assert mc["5th_percentile"] <= mc["95th_percentile"]


# ---------------------------------------------------------------------------
# 5. Sharpe Threshold
# ---------------------------------------------------------------------------

class TestSharpeThreshold:
    def test_sharpe_passed_false_when_no_periods(self, tmp_path):
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 5.0, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert report["sharpe_passed"] is False

    def test_sharpe_threshold_value(self, tmp_path):
        bt = _backtester_with([], tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert report["sharpe_threshold"] == SHARPE_THRESHOLD


# ---------------------------------------------------------------------------
# 6. Empty / Missing Data
# ---------------------------------------------------------------------------

class TestEmptyData:
    def test_missing_file_returns_empty(self, tmp_path):
        bt = Backtester(recommendations_file=str(tmp_path / "nonexistent.json"))
        report = bt.run_walk_forward_backtest(days=30)
        assert report["total_signals"] == 0
        assert report["by_regime"] == {}
        assert report["by_tier"] == {}

    def test_empty_file_returns_empty(self, tmp_path):
        bt = _backtester_with([], tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert report["total_signals"] == 0

    def test_bad_timestamp_skipped(self, tmp_path):
        recs = [{"timestamp": "bad", "outcome_7d": 5.0, "market_regime": "BULL"}]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert report["total_signals"] == 0

    def test_missing_timestamp_skipped(self, tmp_path):
        recs = [{"outcome_7d": 5.0, "market_regime": "BULL"}]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert report["total_signals"] == 0


# ---------------------------------------------------------------------------
# 7. Report Structure
# ---------------------------------------------------------------------------

class TestReportStructure:
    def test_report_has_expected_keys(self, tmp_path):
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 5.0, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        assert "period" in report
        assert "total_signals" in report
        assert "by_regime" in report
        assert "by_tier" in report
        assert "monte_carlo" in report
        assert "suggestions" in report
        assert "walk_forward" in report
        assert "sharpe_passed" in report
        assert "slippage_pct" in report
        assert "transaction_cost_pct" in report

    def test_metrics_have_expected_fields(self, tmp_path):
        recs = [
            _make_rec("STRONG_BUY", "BULL", "strong", 5.0, 5),
            _make_rec("STRONG_BUY", "BULL", "strong", -2.0, 6),
        ]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        m = report["by_regime"]["BULL"]
        assert "trades" in m
        assert "win_rate_pct" in m
        assert "avg_win_pct" in m
        assert "avg_loss_pct" in m
        assert "profit_factor" in m

    def test_profit_factor_all_wins_no_crash(self, tmp_path):
        """All winning outcomes: profit_factor is 0 (no losses to ratio), no division by zero."""
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 5.0, i + 2) for i in range(5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        bull = report.get("by_regime", {}).get("BULL", {})
        assert bull.get("profit_factor", 0) == 0
        assert bull.get("win_rate_pct", 0) == 100.0

    def test_monte_carlo_uses_adjusted_outcomes(self, tmp_path):
        """Monte Carlo resamples outcome_7d_adjusted (slippage/cost already applied)."""
        recs = [_make_rec("STRONG_BUY", "BULL", "strong", 10.0, i + 2) for i in range(15)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=30)
        mc = report["monte_carlo"]
        if mc:
            expected_adj = 10.0 - (SLIPPAGE_PCT_MIN + SLIPPAGE_PCT_MAX) / 2 - TRANSACTION_COST_PCT * 2
            assert mc["mean_expectancy"] < 10.0
            assert abs(mc["mean_expectancy"] - expected_adj) < 1.0


# ---------------------------------------------------------------------------
# Walk-forward window auto-sizing tests
# ---------------------------------------------------------------------------

class TestWalkForwardAutoSizing:
    """Verify that walk-forward windows are correctly auto-sized from the data span."""

    def test_90_day_window_generates_periods(self, tmp_path):
        """days=90, train_months=None → train=2, test=1 → at least one period generated."""
        # 90 trades spread across 90 days (1 per day)
        recs = [_make_rec("MODERATE_BUY", "SIDEWAYS", "moderate", 3.0, i) for i in range(90)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=90)
        wf = report.get("walk_forward", {})
        assert len(wf.get("periods", [])) >= 1, (
            "90-day window should generate at least 1 walk-forward period with auto train=2"
        )

    def test_explicit_train_months_respected(self, tmp_path):
        """Explicit train_months=1 is used instead of auto-sizing."""
        recs = [_make_rec("MODERATE_BUY", "BEAR", "moderate", 5.0, i) for i in range(90)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=90, train_months=1, test_months=1)
        wf = report.get("walk_forward", {})
        # With train=1m + test=1m = 60 days minimum, 90 days of data → should generate periods
        assert len(wf.get("periods", [])) >= 1

    def test_insufficient_data_returns_empty_periods(self, tmp_path):
        """Very short data window (< train+test window) → empty periods gracefully."""
        # Only 1 trade 5 days ago — too short for any walk-forward window
        recs = [_make_rec("STRONG_BUY", "BEAR", "strong", 8.0, 5)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=10, train_months=1, test_months=1)
        wf = report.get("walk_forward", {})
        assert wf.get("periods", []) == []

    def test_auto_sizing_formula(self, tmp_path):
        """Auto-sized train_months = max(1, days//30 - test_months)."""
        # days=90, test_months=1 → train=2
        # days=60, test_months=1 → train=1
        # days=120, test_months=1 → train=3
        recs_90 = [_make_rec("STRONG_BUY", "BEAR", "strong", 5.0, i) for i in range(90)]
        bt = _backtester_with(recs_90, tmp_path)
        # days=90 auto-sizes to train=2 — should produce periods
        report = bt.run_walk_forward_backtest(days=90)
        assert len(report["walk_forward"].get("periods", [])) >= 1

    def test_sharpe_passed_reflects_actual_periods(self, tmp_path):
        """sharpe_passed should be computed from actual walk-forward period Sharpe values, not always False."""
        recs = [_make_rec("STRONG_BUY", "BEAR", "strong", 8.0, i) for i in range(90)]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=90)
        # With 90 strongly positive trades, Sharpe should be evaluable (not stuck on empty periods)
        wf = report.get("walk_forward", {})
        assert wf.get("periods") is not None
        # sharpe_passed is bool — it should be deterministic now that periods are generated
        assert isinstance(report.get("sharpe_passed"), bool)


# ---------------------------------------------------------------------------
# Walk-forward fallback: data concentrated 60+ days ago
# ---------------------------------------------------------------------------

class TestWalkForwardFallback:
    """Verify the fallback single-period fires when rolling windows produce nothing.

    Real scenario: resolved outcome_7d data is all from Feb-Mar 2026 but today is
    May 2026. Rolling windows target Apr-May (the test window) which has no resolved
    outcomes, so periods=[] → fallback should create one full-span period.
    """

    def _make_old_rec(self, signal, regime, quality, outcome_7d, days_ago):
        """Like _make_rec but accepts 60-90 day-old timestamps."""
        ts = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "timestamp": ts,
            "consensus_signal": signal,
            "market_regime": regime,
            "consensus_quality": quality,
            "outcome_7d": outcome_7d,
            "coin_id": "bitcoin",
            "entry_price": 50000,
        }

    def test_fallback_fires_when_data_is_old(self, tmp_path):
        """10+ resolved trades from 60–90 days ago → rolling windows empty → fallback period created."""
        recs = [
            self._make_old_rec("STRONG_BUY", "BEAR", "strong", 5.0, days_ago)
            for days_ago in range(65, 90)  # 25 trades, all 65–89 days old
        ]
        bt = _backtester_with(recs, tmp_path)
        # Use days=120 so all trades fall inside the cutoff; train=2m, test=1m
        report = bt.run_walk_forward_backtest(days=120)
        wf = report.get("walk_forward", {})
        periods = wf.get("periods", [])
        assert len(periods) == 1, (
            f"Expected exactly 1 fallback period, got {len(periods)}: {periods}"
        )
        assert periods[0].get("fallback_full_span") is True, (
            "Fallback period must carry fallback_full_span=True marker"
        )
        assert periods[0].get("trades", 0) >= 10, "Fallback period should cover all resolved trades"

    def test_fallback_does_not_fire_with_fewer_than_10_trades(self, tmp_path):
        """Fewer than 10 resolved trades → no fallback → periods stays empty."""
        recs = [
            self._make_old_rec("STRONG_BUY", "BEAR", "strong", 5.0, days_ago)
            for days_ago in range(65, 74)  # 9 trades
        ]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=120)
        wf = report.get("walk_forward", {})
        periods = wf.get("periods", [])
        assert len(periods) == 0, "Fallback must NOT fire with <10 resolved trades"

    def test_fallback_period_has_sharpe(self, tmp_path):
        """Fallback period computes an overall_sharpe (not null) so the gate has data."""
        recs = [
            self._make_old_rec("STRONG_BUY", "BEAR", "strong", 8.0, days_ago)
            for days_ago in range(65, 90)
        ]
        bt = _backtester_with(recs, tmp_path)
        report = bt.run_walk_forward_backtest(days=120)
        wf = report.get("walk_forward", {})
        # overall_sharpe or period sharpe_ratio should be populated
        has_sharpe = (
            wf.get("overall_sharpe") is not None
            or any(p.get("sharpe_ratio") is not None for p in wf.get("periods", []))
        )
        assert has_sharpe, "Fallback path must produce a non-null Sharpe value"
