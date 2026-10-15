# tests/test_ml_learning.py — ML config, outcome backfill, missed_opportunity, suggestions, run_pipeline, scheduler learning.
# Run: pytest tests/test_ml_learning.py -v

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# -----------------------------------------------------------------------------
# Fixtures: temp paths for ml pipeline (no real data)
# -----------------------------------------------------------------------------

@pytest.fixture
def temp_ml_paths(monkeypatch, tmp_path):
    """Point ml.config to tmp_path so tests don't touch real recommendations/screening."""
    import importlib
    import ml.config as ml_config
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    recs_file = tmp_path / "ai_recommendations_v2.json"
    screening_file = data_dir / "screening_log.json"
    report_file = data_dir / "learning_report.json"

    monkeypatch.setattr(ml_config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ml_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(ml_config, "SCREENING_LOG_FILE", screening_file)
    monkeypatch.setattr(ml_config, "LEARNING_REPORT_FILE", report_file)
    monkeypatch.setattr(ml_config, "RECOMMENDATIONS_FILE", recs_file)
    # Reload modules that read config at import time so they see patched paths
    import ml.outcome_backfill as ob
    import ml.missed_opportunity as mo
    importlib.reload(ob)
    importlib.reload(mo)
    return {
        "recs": recs_file,
        "screening": screening_file,
        "report": report_file,
        "data_dir": data_dir,
    }


def _old_ts(days_ago):
    t = datetime.now() - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


# -----------------------------------------------------------------------------
# ml/config
# -----------------------------------------------------------------------------

class TestMLConfig:
    def test_config_paths_exist_after_temp_ml_paths(self, temp_ml_paths):
        import ml.config as ml_config
        assert ml_config.DATA_DIR.exists()
        assert ml_config.MISSED_OPPORTUNITY_THRESHOLD_PCT == 5.0
        assert ml_config.BACKTEST_DAYS == 90
        assert ml_config.QUANT_SIGNAL_FILE == "quant_signal.py"
        assert ml_config.VETO_SPECIALIST_FILE == "veto_specialist.py"


# -----------------------------------------------------------------------------
# ml/outcome_backfill
# -----------------------------------------------------------------------------

class TestOutcomeBackfill:
    def test_backfill_empty_files_returns_zero_updates(self, temp_ml_paths):
        from ml.outcome_backfill import run_backfill
        def no_prices(coin_ids):
            return {}
        stats = run_backfill(no_prices)
        assert stats["recommendations_updated"] == 0
        assert stats["screening_updated"] == 0

    def test_backfill_updates_recommendations_when_old_and_missing_outcome(self, temp_ml_paths):
        recs = [
            {
                "timestamp": _old_ts(10),
                "coin_id": "bitcoin",
                "entry_price": 50000.0,
                "outcome_7d": None,
            },
        ]
        temp_ml_paths["recs"].write_text(json.dumps(recs), encoding="utf-8")
        from ml.outcome_backfill import run_backfill
        def prices(coin_ids):
            return {"bitcoin": 55000.0}
        stats = run_backfill(prices)
        assert stats["recommendations_updated"] == 1
        updated = json.loads(temp_ml_paths["recs"].read_text(encoding="utf-8"))
        assert updated[0]["outcome_7d"] == 10.0  # (55000-50000)/50000 * 100

    def test_backfill_skips_recent_recommendations(self, temp_ml_paths):
        recs = [
            {
                "timestamp": _old_ts(3),
                "coin_id": "bitcoin",
                "entry_price": 50000.0,
                "outcome_7d": None,
            },
        ]
        temp_ml_paths["recs"].write_text(json.dumps(recs), encoding="utf-8")
        from ml.outcome_backfill import run_backfill
        stats = run_backfill(lambda ids: {"bitcoin": 55000.0})
        assert stats["recommendations_updated"] == 0
        data = json.loads(temp_ml_paths["recs"].read_text(encoding="utf-8"))
        assert data[0]["outcome_7d"] is None

    def test_backfill_screening_log_updates_outcome(self, temp_ml_paths):
        screening = [
            {
                "timestamp": _old_ts(8),
                "coin_id": "ethereum",
                "entry_price": 3000.0,
                "outcome_7d": None,
            },
        ]
        temp_ml_paths["screening"].write_text(json.dumps(screening), encoding="utf-8")
        from ml.outcome_backfill import run_backfill
        def prices(coin_ids):
            return {"ethereum": 3300.0}
        stats = run_backfill(prices)
        assert stats["screening_updated"] == 1
        updated = json.loads(temp_ml_paths["screening"].read_text(encoding="utf-8"))
        assert updated[0]["outcome_7d"] == 10.0


# -----------------------------------------------------------------------------
# ml/missed_opportunity
# -----------------------------------------------------------------------------

class TestMissedOpportunity:
    def test_analyze_empty_data(self, temp_ml_paths):
        from ml.missed_opportunity import analyze
        out = analyze()
        assert out["threshold_pct"] == 5.0
        assert out["from_recommendations"]["total"] == 0
        assert out["from_screening"]["total"] == 0
        assert out["summary"]["recommendations_missed"] == 0
        assert out["summary"]["screening_missed"] == 0

    def test_analyze_finds_hold_with_high_outcome(self, temp_ml_paths):
        recs = [
            {
                "consensus_signal": "HOLD",
                "outcome_7d": 8.0,
                "market_regime": "BULL",
                "consensus_quality": "hold",
            },
        ]
        temp_ml_paths["recs"].write_text(json.dumps(recs), encoding="utf-8")
        from ml.missed_opportunity import analyze
        out = analyze(threshold_pct=5.0)
        assert out["from_recommendations"]["total"] == 1
        assert out["summary"]["recommendations_missed"] == 1
        assert "BULL" in out["from_recommendations"]["by_regime"]

    def test_analyze_ignores_buy_signals(self, temp_ml_paths):
        recs = [
            {
                "consensus_signal": "STRONG_BUY",
                "outcome_7d": 15.0,
                "market_regime": "BULL",
            },
        ]
        temp_ml_paths["recs"].write_text(json.dumps(recs), encoding="utf-8")
        from ml.missed_opportunity import analyze
        out = analyze(threshold_pct=5.0)
        assert out["from_recommendations"]["total"] == 0

    def test_analyze_screening_missed(self, temp_ml_paths):
        # HOLD-tier entries that subsequently rallied are the missed opportunities
        # (STRONG_BUY was acted on — it shouldn't count as missed)
        screening = [
            {
                "outcome_7d": 12.0,
                "quant_tier": "HOLD",
                "coin_id": "bitcoin",
                "market_regime": "BEAR",
            },
        ]
        temp_ml_paths["screening"].write_text(json.dumps(screening), encoding="utf-8")
        from ml.missed_opportunity import analyze
        out = analyze(threshold_pct=5.0)
        assert out["from_screening"]["total"] == 1
        assert out["summary"]["screening_missed"] == 1


# -----------------------------------------------------------------------------
# ml/suggestions
# -----------------------------------------------------------------------------

class TestSuggestions:
    def test_build_suggestions_includes_backtest_text(self):
        from ml.suggestions import build_suggestions
        missed = {"threshold_pct": 5.0, "from_recommendations": {"total": 0}, "from_screening": {"total": 0}}
        backtest = ["Focus on ULTRA_BUY", "Sharpe >1.0"]
        out = build_suggestions(missed, backtest)
        assert len(out) == 2
        assert out[0]["category"] == "backtest"
        assert "ULTRA_BUY" in out[0]["summary"]
        assert out[1]["category"] == "backtest"

    def test_build_suggestions_adds_quant_and_veto_when_missed(self):
        from ml.suggestions import build_suggestions
        missed = {
            "threshold_pct": 5.0,
            "from_recommendations": {
                "total": 6,
                "by_regime": {"BULL": 4, "BEAR": 2},
                "by_consensus_quality": {"hold": 6},
            },
            "from_screening": {"total": 0},
        }
        out = build_suggestions(missed, [])
        categories = [s["category"] for s in out]
        assert "quant" in categories
        assert "veto" in categories
        assert any("VETO_CONFIDENCE" in str(s.get("parameter", "")) for s in out)


# -----------------------------------------------------------------------------
# ml/run_pipeline (mocked backtester and fetcher)
# -----------------------------------------------------------------------------

class TestRunPipeline:
    def test_run_pipeline_writes_report(self, temp_ml_paths):
        from ml.run_pipeline import run
        with patch("ml.run_pipeline.run_backfill", return_value={"recommendations_updated": 0, "screening_updated": 0}):
            with patch("backtester.Backtester") as MockBT:
                mock_bt = MagicMock()
                mock_bt.run_walk_forward_backtest.return_value = {
                    "period": "Last 90 days",
                    "total_signals": 0,
                    "sharpe_passed": True,
                    "by_regime": {},
                    "by_tier": {},
                    "walk_forward": {"periods": []},
                }
                mock_bt.get_optimization_suggestions.return_value = []
                MockBT.return_value = mock_bt
                report = run(save_expectancy=False)
        assert report["run_timestamp"]
        assert "backtest" in report
        assert report["backtest"]["sharpe_passed"] is True
        assert "suggestions" in report
        assert temp_ml_paths["report"].exists()
        content = json.loads(temp_ml_paths["report"].read_text(encoding="utf-8"))
        assert content["run_timestamp"] == report["run_timestamp"]


# -----------------------------------------------------------------------------
# SchedulerManager monthly learning
# -----------------------------------------------------------------------------

class TestSchedulerMonthlyLearning:
    @pytest.fixture
    def scheduler_temp_config(self, tmp_path):
        path = tmp_path / "scheduler_config.json"
        path.write_text("{}")
        from scheduler_manager import SchedulerManager
        sm = SchedulerManager(config_file=str(path))
        yield sm

    def test_get_monthly_learning_status_default(self, scheduler_temp_config):
        status = scheduler_temp_config.get_monthly_learning_status()
        assert "enabled" in status
        assert "day_of_month" in status
        assert "last_learning_run" in status
        assert status["enabled"] is False
        assert status["day_of_month"] == 1

    def test_set_monthly_learning_enabled_and_day(self, scheduler_temp_config):
        sm = scheduler_temp_config
        out = sm.set_monthly_learning(True, 15)
        assert out["enabled"] is True
        assert out["day_of_month"] == 15
        out2 = sm.get_monthly_learning_status()
        assert out2["enabled"] is True
        assert out2["day_of_month"] == 15

    def test_set_monthly_learning_clamps_day_to_1_28(self, scheduler_temp_config):
        sm = scheduler_temp_config
        sm.set_monthly_learning(True, 0)
        assert sm.learning_run_day == 1
        sm.set_monthly_learning(True, 99)
        assert sm.learning_run_day == 28

    def test_check_and_run_monthly_learning_no_callback_no_op(self, scheduler_temp_config):
        sm = scheduler_temp_config
        sm.monthly_learning_enabled = True
        sm.learning_run_day = 1
        sm.last_learning_run = None
        sm.check_and_run_monthly_learning()
        assert sm.last_learning_run is None
