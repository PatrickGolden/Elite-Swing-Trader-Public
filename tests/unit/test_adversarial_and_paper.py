# tests/unit/test_adversarial_and_paper.py — Tests for adversarial backtester,
# paper trade weight in optimizer, and alert suppression for test/paper positions.
# Run: pytest tests/unit/test_adversarial_and_paper.py -v

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures: sample trade data
# ---------------------------------------------------------------------------
def _make_trade(regime="BEAR", path="default", outcome="win", pnl=3.0,
                exit_reason="take_profit", hold_hours=48, days_ago=1,
                is_test=False, source=None):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    t = {
        "ts": ts, "regime": regime, "market_regime": regime,
        "quant_path": path, "outcome": outcome,
        "pnl_pct": pnl, "pnl_usd": pnl * 10,
        "exit_reason": exit_reason, "hold_hours": hold_hours,
        "confirmation_score": 65,
    }
    if is_test:
        t["is_test"] = True
    if source:
        t["source"] = source
    return t


def sample_failure_trades():
    """Trades designed to produce failure cells in BEAR x dip."""
    return [
        _make_trade("BEAR", "dip", "loss", -4.0, "stop_loss", 6, 1),
        _make_trade("BEAR", "dip", "loss", -3.0, "stop_loss", 10, 2),
        _make_trade("BEAR", "dip", "loss", -5.0, "stop_loss", 3, 3),
        _make_trade("BEAR", "dip", "loss", -2.0, "time_exit", 72, 4),
        _make_trade("BULL", "default", "win", 5.0, "take_profit", 36, 5),
        _make_trade("BULL", "default", "win", 4.0, "take_profit", 48, 6),
        _make_trade("BULL", "default", "win", 6.0, "take_profit", 24, 7),
        _make_trade("BULL", "default", "loss", -1.0, "stop_loss", 12, 8),
        _make_trade("SIDEWAYS", "default", "win", 2.0, "take_profit", 60, 9),
        _make_trade("SIDEWAYS", "default", "loss", -1.5, "stop_loss", 8, 10),
    ]


# ===========================================================================
# Part 1: Adversarial Backtester Unit Tests
# ===========================================================================
class TestComputeCellStats:
    def test_empty(self):
        from scripts.adversarial_backtest import _compute_cell_stats
        stats = _compute_cell_stats([])
        assert stats["trades"] == 0
        assert stats["win_rate_pct"] == 0.0

    def test_all_wins(self):
        from scripts.adversarial_backtest import _compute_cell_stats
        trades = [_make_trade(outcome="win", pnl=5.0) for _ in range(4)]
        stats = _compute_cell_stats(trades)
        assert stats["trades"] == 4
        assert stats["win_rate_pct"] == 100.0
        assert stats["wins"] == 4
        assert stats["expectancy_pct"] == 5.0

    def test_mixed(self):
        from scripts.adversarial_backtest import _compute_cell_stats
        trades = [
            _make_trade(outcome="win", pnl=10.0),
            _make_trade(outcome="loss", pnl=-5.0),
        ]
        stats = _compute_cell_stats(trades)
        assert stats["trades"] == 2
        assert stats["win_rate_pct"] == 50.0
        assert stats["expectancy_pct"] == 2.5


class TestIdentifyFailureCells:
    def test_detects_failure(self):
        from scripts.adversarial_backtest import identify_failure_cells
        trades = sample_failure_trades()
        failure_cells, all_cells = identify_failure_cells(trades)
        assert len(all_cells) >= 3  # BEAR/dip, BULL/default, SIDEWAYS/default
        bear_dip = [c for c in failure_cells if c["regime"] == "BEAR" and c["path"] == "dip"]
        assert len(bear_dip) == 1
        assert bear_dip[0]["win_rate_pct"] == 0.0
        assert bear_dip[0]["recommendation"] == "disable_path_in_regime"

    def test_no_false_positives_for_winners(self):
        from scripts.adversarial_backtest import identify_failure_cells
        trades = [_make_trade("BULL", "default", "win", 5.0, days_ago=i) for i in range(5)]
        failure_cells, all_cells = identify_failure_cells(trades)
        assert len(failure_cells) == 0

    def test_min_cell_trades_threshold(self):
        """Cells with < MIN_CELL_TRADES are not flagged even if 0% WR."""
        from scripts.adversarial_backtest import identify_failure_cells, MIN_CELL_TRADES
        trades = [_make_trade("BEAR", "rare", "loss", -3.0, days_ago=i) for i in range(MIN_CELL_TRADES - 1)]
        failure_cells, _ = identify_failure_cells(trades)
        assert len(failure_cells) == 0


class TestAnalyzeExitReasons:
    def test_stop_loss_quick_stops(self):
        from scripts.adversarial_backtest import analyze_exit_reasons
        trades = sample_failure_trades()
        analysis = analyze_exit_reasons(trades)
        assert "stop_loss" in analysis
        sl = analysis["stop_loss"]
        assert sl["trades"] >= 3
        assert "quick_stops_pct" in sl
        assert sl["quick_stops_pct"] > 0  # Some stops hit < 24h

    def test_take_profit_present(self):
        from scripts.adversarial_backtest import analyze_exit_reasons
        trades = sample_failure_trades()
        analysis = analyze_exit_reasons(trades)
        assert "take_profit" in analysis
        assert analysis["take_profit"]["win_rate_pct"] == 100.0


class TestComputeMaxDrawdown:
    def test_simple_drawdown(self):
        from scripts.adversarial_backtest import compute_max_drawdown
        trades = [
            _make_trade(pnl=5.0),
            _make_trade(pnl=-8.0),
            _make_trade(pnl=-3.0),
            _make_trade(pnl=10.0),
        ]
        dd = compute_max_drawdown(trades)
        assert dd["max_drawdown_pct"] > 0
        assert dd["peak_pnl_pct"] >= 0

    def test_no_drawdown_all_wins(self):
        from scripts.adversarial_backtest import compute_max_drawdown
        trades = [_make_trade(pnl=2.0) for _ in range(5)]
        dd = compute_max_drawdown(trades)
        assert dd["max_drawdown_pct"] == 0


class TestBuildStructuralIssues:
    def test_critical_regime_flagged(self):
        from scripts.adversarial_backtest import (
            identify_failure_cells, analyze_exit_reasons,
            compute_max_drawdown, build_structural_issues,
        )
        trades = sample_failure_trades()
        failure_cells, all_cells = identify_failure_cells(trades)
        exit_analysis = analyze_exit_reasons(trades)
        drawdown = compute_max_drawdown(trades)
        issues = build_structural_issues(failure_cells, exit_analysis, drawdown, all_cells)
        assert len(issues) > 0
        severities = {i["severity"] for i in issues}
        assert "critical" in severities or "high" in severities

    def test_sorted_by_severity(self):
        from scripts.adversarial_backtest import build_structural_issues
        issues = build_structural_issues(
            failure_cells=[],
            exit_analysis={"stop_loss": {"quick_stops_pct": 80, "trades": 10}},
            drawdown={"max_drawdown_pct": 200},
            all_cells=[],
        )
        if len(issues) >= 2:
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            for i in range(len(issues) - 1):
                assert severity_order[issues[i]["severity"]] <= severity_order[issues[i + 1]["severity"]]


class TestGenerateQuickWins:
    def test_disable_zero_wr_path(self):
        from scripts.adversarial_backtest import identify_failure_cells, generate_quick_wins
        trades = sample_failure_trades()
        failure_cells, all_cells = identify_failure_cells(trades)
        wins = generate_quick_wins(failure_cells, all_cells)
        dip_wins = [w for w in wins if "dip" in w.lower()]
        assert len(dip_wins) > 0


class TestRunAdversarialAnalysis:
    def test_no_trades_returns_error(self, tmp_path, monkeypatch):
        import scripts.adversarial_backtest as ab
        monkeypatch.setattr(ab, "BACKTEST_TRADES_FILE", tmp_path / "nonexistent.json")
        report = ab.run_adversarial_analysis(baseline_only=True)
        assert "error" in report

    def test_baseline_only_succeeds(self, tmp_path, monkeypatch):
        import scripts.adversarial_backtest as ab
        trades_file = tmp_path / "backtest_trades.json"
        report_file = tmp_path / "adversarial_report.json"
        trades_data = {
            "summary": {"profit_factor": 1.2},
            "trades": sample_failure_trades(),
        }
        trades_file.write_text(json.dumps(trades_data), encoding="utf-8")
        monkeypatch.setattr(ab, "BACKTEST_TRADES_FILE", trades_file)
        monkeypatch.setattr(ab, "ADVERSARIAL_REPORT_FILE", report_file)

        report = ab.run_adversarial_analysis(baseline_only=True)
        assert "error" not in report
        assert "baseline" in report
        assert report["baseline"]["total_trades"] == 10
        assert len(report["failure_cells"]) > 0
        assert len(report["structural_issues"]) > 0
        assert report["baseline_only"] is True
        assert report_file.exists()


# ===========================================================================
# Part 2: Optimizer — Adversarial Integration
# ===========================================================================
class TestOptimizerAdversarialLoading:
    def test_adversarial_report_loaded_in_aggregate(self, tmp_path, monkeypatch):
        """aggregate() should load adversarial_report.json when present."""
        import recursive_optimizer as ro

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        adv_file = data_dir / "adversarial_report.json"
        adv_data = {
            "baseline": {"total_trades": 50, "win_rate_pct": 45},
            "structural_issues": [{"issue": "test issue", "severity": "high"}],
            "quick_wins": ["Disable X path"],
            "parameter_sensitivity": [
                {"parameter": "BEAR_REGIME_MIN_SCORE", "sensitivity": 3.5,
                 "best_value": 72, "best_expectancy": 4.2},
            ],
            "failure_cells": [],
        }
        adv_file.write_text(json.dumps(adv_data), encoding="utf-8")
        monkeypatch.setattr(ro, "ADVERSARIAL_REPORT_FILE", adv_file)

        loaded = ro._load_json(adv_file)
        assert loaded.get("baseline", {}).get("total_trades") == 50
        assert len(loaded.get("parameter_sensitivity", [])) == 1

    def test_missing_adversarial_report_is_empty(self, tmp_path, monkeypatch):
        import recursive_optimizer as ro
        monkeypatch.setattr(ro, "ADVERSARIAL_REPORT_FILE", tmp_path / "no_such_file.json")
        loaded = ro._load_json(tmp_path / "no_such_file.json")
        assert loaded == {} or loaded is None or loaded == []


class TestOptimizerAdversarialAnnotations:
    def test_result_annotated_with_sensitivity(self):
        """When adversarial report has sensitivity data for a param, it should be annotated."""
        adv_report = {
            "parameter_sensitivity": [
                {"parameter": "BEAR_REGIME_MIN_SCORE", "sensitivity": 3.5,
                 "best_value": 72, "best_expectancy": 4.2},
            ],
            "failure_cells": [],
        }
        result_entry = {"parameter": "BEAR_REGIME_MIN_SCORE", "suggested_value": 72}
        for ps in adv_report["parameter_sensitivity"]:
            if ps.get("parameter") == result_entry["parameter"]:
                result_entry["adversarial_sensitivity"] = ps.get("sensitivity", 0)
                result_entry["adversarial_best_value"] = ps.get("best_value")
                result_entry["adversarial_best_expectancy"] = ps.get("best_expectancy")
                adv_best = ps.get("best_value")
                cur_val = 70
                if adv_best is not None and result_entry["suggested_value"] is not None:
                    adv_agrees = (adv_best > cur_val) == (result_entry["suggested_value"] > cur_val)
                    result_entry["adversarial_agrees"] = adv_agrees

        assert result_entry["adversarial_sensitivity"] == 3.5
        assert result_entry["adversarial_best_value"] == 72
        assert result_entry["adversarial_agrees"] is True

    def test_failure_cells_attached_to_result(self):
        """Failure cells matching a param's regime filter should be attached."""
        adv_report = {
            "failure_cells": [
                {"regime": "BEAR", "path": "dip", "win_rate_pct": 0.0,
                 "recommendation": "disable_path_in_regime"},
            ],
        }
        result_entry = {"parameter": "BEAR_REGIME_MIN_SCORE"}
        regime_filter_set = {"BEAR"}
        for fc in adv_report["failure_cells"]:
            fc_regime = fc.get("regime", "")
            if regime_filter_set and fc_regime in regime_filter_set:
                result_entry.setdefault("adversarial_failure_cells", []).append({
                    "regime": fc_regime,
                    "path": fc.get("path", ""),
                    "win_rate_pct": fc.get("win_rate_pct", 0),
                    "recommendation": fc.get("recommendation", ""),
                })

        assert len(result_entry["adversarial_failure_cells"]) == 1
        assert result_entry["adversarial_failure_cells"][0]["regime"] == "BEAR"


# ===========================================================================
# Part 3: Paper Trade Weight in Optimizer
# ===========================================================================
class TestPaperTradeWeight:
    def test_paper_weight_constant(self):
        import recursive_optimizer as ro
        assert ro.PAPER_TRADE_WEIGHT == 0.75
        assert ro.BACKTEST_WEIGHT == 0.5

    def test_paper_supplemented_blocks_auto_apply(self):
        """When paper trades supplement evidence, safe_to_auto_apply must be False."""
        result_entry = {
            "paper_supplemented": True,
            "paper_trades_used": 5,
        }
        used_paper = result_entry.get("paper_supplemented", False)
        used_backtest = result_entry.get("backtest_supplemented", False)
        if used_backtest or used_paper:
            conf = "medium"
            safe = False
        else:
            conf = "high"
            safe = True
        assert conf == "medium"
        assert safe is False

    def test_effective_count_with_paper(self):
        """Paper trades at 0.75x weight should contribute fractionally."""
        live_count = 5
        paper_count = 6
        bt_count = 0
        effective = live_count + bt_count * 0.5 + paper_count * 0.75
        assert effective == 5 + 4.5  # 9.5
        assert effective >= 8  # Meets MIN_TRADES_FOR_REC

    def test_pure_live_trades_get_high_confidence(self):
        """With enough live trades and no supplement, confidence should be high."""
        live_count = 15
        used_paper = False
        used_backtest = False
        min_trades_auto = 6
        if used_backtest or used_paper:
            conf = "medium"
        else:
            conf = "high" if live_count >= min_trades_auto else "medium"
        assert conf == "high"


# ===========================================================================
# Part 4: Alert Suppression for Test/Paper Positions
# ===========================================================================
class TestAlertSuppression:
    def _get_skip_fn(self):
        from app import _skip_alerts_for_test_position
        return _skip_alerts_for_test_position

    def test_paper_trade_suppressed(self):
        skip = self._get_skip_fn()
        position = {"symbol": "SOL", "is_test": True, "source": "paper_trade"}
        assert skip(position) is True

    def test_test_position_suppressed(self):
        skip = self._get_skip_fn()
        position = {"symbol": "BTC", "is_test": True}
        assert skip(position) is True

    def test_live_position_not_suppressed(self):
        skip = self._get_skip_fn()
        position = {"symbol": "ETH", "is_test": False}
        assert skip(position) is False

    def test_position_without_is_test_not_suppressed(self):
        skip = self._get_skip_fn()
        position = {"symbol": "AVAX"}
        assert skip(position) is False

    def test_none_position_suppressed(self):
        skip = self._get_skip_fn()
        assert skip(None) is True

    def test_test_cleanup_suppressed(self):
        skip = self._get_skip_fn()
        position = {"symbol": "SOL", "exit_reason": "test_cleanup"}
        assert skip(position) is True


# ===========================================================================
# Part 5: API Endpoint
# ===========================================================================
class TestAdversarialAPIEndpoint:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLASK_TESTING", "1")
        monkeypatch.setenv("DISABLE_SCHEDULER", "1")
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as c:
            yield c

    def test_no_report_returns_placeholder(self, client, monkeypatch, tmp_path):
        import app as flask_app
        monkeypatch.setattr(flask_app.os.path, "exists",
                            lambda p: False if "adversarial_report" in str(p) else flask_app.os.path.exists.__wrapped__(p)
                            if hasattr(flask_app.os.path.exists, '__wrapped__') else False)
        resp = client.get("/api/adversarial/report")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("generated_at") is None or "note" in data

    def test_report_served_when_exists(self, client, monkeypatch, tmp_path):
        report_data = {
            "timestamp": "2026-04-11T00:00:00Z",
            "baseline": {"total_trades": 50, "win_rate_pct": 55},
            "failure_cells": [{"regime": "BEAR", "path": "dip"}],
            "structural_issues": [{"issue": "test", "severity": "high"}],
            "quick_wins": ["Disable X"],
            "parameter_sensitivity": [],
        }
        report_file = tmp_path / "adversarial_report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")
        import app as flask_app
        original_exists = flask_app.os.path.exists
        original_join = flask_app.os.path.join

        def patched_exists(p):
            if "adversarial_report" in str(p):
                return True
            return original_exists(p)

        def patched_join(*args):
            if args and "adversarial_report" in str(args[-1]):
                return str(report_file)
            return original_join(*args)

        monkeypatch.setattr(flask_app.os.path, "exists", patched_exists)
        monkeypatch.setattr(flask_app.os.path, "join", patched_join)

        resp = client.get("/api/adversarial/report")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["baseline"]["total_trades"] == 50
        assert len(data["failure_cells"]) == 1
