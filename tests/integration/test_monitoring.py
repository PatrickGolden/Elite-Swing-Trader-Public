# tests/test_monitoring.py — Backtest expectancy save/load, live stats by regime, drift detection
# Kill switch removed; emergency stop via exchange API disable.
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
import json
import tempfile
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import monitoring


# ---------------------------------------------------------------------------
# Fixtures — redirect file paths to temp dir
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def temp_data_dir(monkeypatch, tmp_path):
    """Redirect DATA_DIR and EXPECTANCY_FILE to temp."""
    monkeypatch.setattr(monitoring, "DATA_DIR", tmp_path)
    monkeypatch.setattr(monitoring, "EXPECTANCY_FILE", tmp_path / "monitoring_expectancy.json")
    yield tmp_path


# ---------------------------------------------------------------------------
# 1. Backtest Expectancy Save/Load
# ---------------------------------------------------------------------------

class TestBacktestExpectancy:
    def test_load_returns_none_when_no_file(self):
        assert monitoring.load_backtest_expectancy() is None

    def test_save_and_load_round_trip(self):
        report = {
            "by_regime": {
                "BULL": {"win_rate_pct": 70.0, "trades": 20},
                "BEAR": {"win_rate_pct": 55.0, "trades": 10},
            }
        }
        monitoring.save_backtest_expectancy(report)
        result = monitoring.load_backtest_expectancy()
        assert result is not None
        by_regime, updated = result
        assert by_regime["BULL"]["win_rate_pct"] == 70.0
        assert by_regime["BEAR"]["trades"] == 10
        assert updated is not None

    def test_save_empty_report(self):
        monitoring.save_backtest_expectancy({})
        result = monitoring.load_backtest_expectancy()
        by_regime, _ = result
        assert by_regime == {}

    def test_corrupt_file_returns_none(self, temp_data_dir):
        (temp_data_dir / "monitoring_expectancy.json").write_text("{broken")
        assert monitoring.load_backtest_expectancy() is None


# ---------------------------------------------------------------------------
# 2. Live Stats by Regime
# ---------------------------------------------------------------------------

class TestLiveStatsByRegime:
    def _make_rec(self, regime, outcome_7d, days_ago=5):
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        return {"timestamp": ts, "market_regime": regime, "outcome_7d": outcome_7d}

    def test_groups_by_regime(self):
        recs = [
            self._make_rec("BULL", 5.0),
            self._make_rec("BULL", -2.0),
            self._make_rec("BEAR", -3.0),
        ]
        stats = monitoring.get_live_stats_by_regime(recs, days=30)
        assert stats["BULL"]["trades"] == 2
        assert stats["BULL"]["win_rate_pct"] == 50.0
        assert stats["BEAR"]["trades"] == 1
        assert stats["BEAR"]["win_rate_pct"] == 0.0

    def test_skips_old_records(self):
        recs = [self._make_rec("BULL", 5.0, days_ago=60)]
        stats = monitoring.get_live_stats_by_regime(recs, days=30)
        assert stats == {}

    def test_skips_no_outcome(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        recs = [{"timestamp": ts, "market_regime": "BULL", "outcome_7d": None}]
        stats = monitoring.get_live_stats_by_regime(recs, days=30)
        assert stats == {}

    def test_defaults_regime_to_sideways(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        recs = [{"timestamp": ts, "market_regime": None, "outcome_7d": 3.0}]
        stats = monitoring.get_live_stats_by_regime(recs, days=30)
        assert "SIDEWAYS" in stats

    def test_empty_list(self):
        assert monitoring.get_live_stats_by_regime([], days=30) == {}


# ---------------------------------------------------------------------------
# 3. Drift Detection
# ---------------------------------------------------------------------------

class TestDriftDetection:
    def test_no_drift_when_no_expectancy_file(self):
        drifted, reason = monitoring.check_for_drift({"BULL": {"win_rate_pct": 50, "trades": 10}}, "BULL")
        assert drifted is False

    def test_no_drift_when_regime_not_in_expectancy(self):
        monitoring.save_backtest_expectancy({"by_regime": {"BEAR": {"win_rate_pct": 60}}})
        drifted, _ = monitoring.check_for_drift({"BULL": {"win_rate_pct": 50, "trades": 10}}, "BULL")
        assert drifted is False

    def test_no_drift_when_insufficient_trades(self):
        monitoring.save_backtest_expectancy({"by_regime": {"BULL": {"win_rate_pct": 70}}})
        drifted, _ = monitoring.check_for_drift(
            {"BULL": {"win_rate_pct": 40, "trades": 3}}, "BULL", min_trades=5,
        )
        assert drifted is False

    def test_drift_detected_when_live_below_expected(self):
        monitoring.save_backtest_expectancy({"by_regime": {"BULL": {"win_rate_pct": 70}}})
        drifted, reason = monitoring.check_for_drift(
            {"BULL": {"win_rate_pct": 50, "trades": 10}}, "BULL", threshold_pct=12.0,
        )
        assert drifted is True
        assert "Drift" in reason
        assert "50" in reason

    def test_no_drift_within_tolerance(self):
        monitoring.save_backtest_expectancy({"by_regime": {"BULL": {"win_rate_pct": 70}}})
        drifted, _ = monitoring.check_for_drift(
            {"BULL": {"win_rate_pct": 65, "trades": 10}}, "BULL", threshold_pct=12.0,
        )
        assert drifted is False

    def test_drift_with_custom_threshold(self):
        monitoring.save_backtest_expectancy({"by_regime": {"BEAR": {"win_rate_pct": 60}}})
        drifted, _ = monitoring.check_for_drift(
            {"BEAR": {"win_rate_pct": 53, "trades": 8}}, "BEAR", threshold_pct=5.0,
        )
        assert drifted is True
