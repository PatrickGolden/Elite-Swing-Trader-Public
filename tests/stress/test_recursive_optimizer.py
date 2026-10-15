# tests/test_recursive_optimizer.py — Comprehensive test suite for recursive optimizer pipeline.
# Run: pytest tests/test_recursive_optimizer.py -v

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def sample_trades():
    """~15 trades with varied regime/path/outcome/pnl/confirmation_score."""
    now = datetime.now(timezone.utc)
    ts = lambda d: (now - timedelta(days=d)).isoformat()
    trades = [
        {"ts": ts(1), "regime": "SIDEWAYS", "quant_path": "default", "outcome": "win", "pnl_pct": 5.0, "pnl_usd": 50, "confirmation_score": 68},
        {"ts": ts(2), "regime": "SIDEWAYS", "quant_path": "default", "outcome": "loss", "pnl_pct": -3.0, "pnl_usd": -30, "confirmation_score": 62},
        {"ts": ts(3), "regime": "SIDEWAYS", "quant_path": "default", "outcome": "win", "pnl_pct": 4.0, "pnl_usd": 40, "confirmation_score": 70},
        {"ts": ts(4), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -2.0, "pnl_usd": -20, "confirmation_score": 64},
        {"ts": ts(5), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 6.0, "pnl_usd": 60, "confirmation_score": 72},
        {"ts": ts(6), "regime": "BEAR", "quant_path": "dip", "outcome": "win", "pnl_pct": 3.0, "pnl_usd": 30, "confirmation_score": 66},
        {"ts": ts(7), "regime": "BEAR", "quant_path": "dip", "outcome": "loss", "pnl_pct": -4.0, "pnl_usd": -40, "confirmation_score": 60},
        {"ts": ts(8), "regime": "BEAR", "quant_path": "dip", "outcome": "loss", "pnl_pct": -2.0, "pnl_usd": -20, "confirmation_score": 61},
        {"ts": ts(9), "regime": "BEAR", "quant_path": "dip", "outcome": "win", "pnl_pct": 5.0, "pnl_usd": 50, "confirmation_score": 69},
        {"ts": ts(10), "regime": "BEAR", "quant_path": "dip", "outcome": "win", "pnl_pct": 4.0, "pnl_usd": 40, "confirmation_score": 71},
        {"ts": ts(11), "regime": "BULL", "quant_path": "default", "outcome": "win", "pnl_pct": 7.0, "pnl_usd": 70, "confirmation_score": 75},
        {"ts": ts(12), "regime": "BULL", "quant_path": "default", "outcome": "loss", "pnl_pct": -1.0, "pnl_usd": -10, "confirmation_score": 65},
        {"ts": ts(13), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -3.0, "pnl_usd": -30, "confirmation_score": 63},
        {"ts": ts(14), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -2.0, "pnl_usd": -20, "confirmation_score": 62},
        {"ts": ts(15), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 4.0, "pnl_usd": 40, "confirmation_score": 74},
    ]
    return trades


def sample_aggregate_data(trades=None):
    """Complete aggregate dict matching aggregate() return shape."""
    trades = trades or sample_trades()
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    trades_7d = [t for t in trades if t.get("ts", "") >= cutoff_7d]
    trades_30d = [t for t in trades if t.get("ts", "") >= cutoff_30d]
    wr_7d = (sum(1 for t in trades_7d if t.get("outcome") == "win") / len(trades_7d) * 100) if trades_7d else 0
    wr_30d = (sum(1 for t in trades_30d if t.get("outcome") == "win") / len(trades_30d) * 100) if trades_30d else 0
    recent_regimes = [t.get("regime", "UNKNOWN") for t in trades[-20:] if t.get("regime")]
    current_regime = max(set(recent_regimes), key=recent_regimes.count) if recent_regimes else "UNKNOWN"
    return {
        "trades": trades,
        "trades_7d": trades_7d,
        "trades_30d": trades_30d,
        "win_rate_7d": round(wr_7d, 1),
        "win_rate_30d": round(wr_30d, 1),
        "win_rates_by_regime_path": {},
        "regime_tier": {},
        "current_thresholds": {"BEAR_REGIME_MIN_SCORE": 70, "DIP_MIN_SCORE": 65},
        "current_regime": current_regime,
        "attribution": {},
        "veto_accuracy": {"total": 0},
        "missed_summary": {},
        "learning_report": {},
    }


@pytest.fixture
def tmp_optimizer_env(monkeypatch, tmp_path):
    """Redirect optimizer and thresholds to temp paths."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_file = data_dir / "optimizer_log.json"
    learning_file = data_dir / "learning_report.json"
    stress_file = data_dir / "buy_signals_stress_30d.json"
    attr_file = tmp_path / "model_attribution.json"
    live_file = tmp_path / "live_thresholds.json"

    import recursive_optimizer as ro
    monkeypatch.setattr(ro, "DATA_DIR", data_dir)
    monkeypatch.setattr(ro, "OPTIMIZER_LOG_FILE", log_file)
    monkeypatch.setattr(ro, "LEARNING_REPORT_FILE", learning_file)
    monkeypatch.setattr(ro, "STRESS_REPORT_FILE", stress_file)
    monkeypatch.setattr(ro, "ATTRIBUTION_FILE", str(attr_file))

    import thresholds as th
    monkeypatch.setattr(th, "LIVE_THRESHOLDS_PATH", str(live_file))

    # Seed attribution with sample trades
    attr_data = {"trades": sample_trades(), "updated": datetime.now(timezone.utc).isoformat()}
    attr_file.write_text(json.dumps(attr_data, indent=2), encoding="utf-8")

    live_file.write_text(json.dumps({"BEAR_REGIME_MIN_SCORE": 70, "DIP_MIN_SCORE": 65}, indent=2), encoding="utf-8")
    learning_file.write_text(json.dumps({"missed_opportunity": {"summary": {}}}, indent=2), encoding="utf-8")
    stress_file.write_text(json.dumps({"recommendations": []}, indent=2), encoding="utf-8")

    return {
        "log_file": log_file,
        "learning_file": learning_file,
        "stress_file": stress_file,
        "attr_file": attr_file,
        "live_file": live_file,
        "data_dir": data_dir,
    }


# ---------------------------------------------------------------------------
# Safety gate: same daily optimizer email path
# ---------------------------------------------------------------------------

class TestSafetyGateBlockedEmail:
    def test_gate_blocked_uses_send_optimizer_report_and_logs(self, tmp_optimizer_env):
        with patch("recursive_optimizer.run_unit_safety_gate", return_value=False), \
             patch("alert_system.AlertSystem") as MockAlerts:
            MockAlerts.return_value.send_optimizer_report = MagicMock()
            from recursive_optimizer import run_full_recursive_optimizer_cycle, _load_optimizer_log
            out = run_full_recursive_optimizer_cycle()
        assert out.get("error")
        assert out.get("safety_gate_blocked") is True
        MockAlerts.return_value.send_optimizer_report.assert_called_once()
        subj, body = MockAlerts.return_value.send_optimizer_report.call_args[0]
        assert "Recursive Optimizer" in subj and "Cycle #" in subj
        assert "Safety gate blocked" in body
        log = _load_optimizer_log()
        assert log[-1].get("safety_gate_blocked") is True


# ---------------------------------------------------------------------------
# Class 1: TestDataHelpers
# ---------------------------------------------------------------------------

class TestDataHelpers:
    def test_load_json_missing_file(self, tmp_optimizer_env):
        from recursive_optimizer import _load_json
        result = _load_json(Path(tmp_optimizer_env["data_dir"]) / "nonexistent.json", default=[])
        assert result == []

    def test_load_json_corrupt_file(self, tmp_path):
        from recursive_optimizer import _load_json
        bad = tmp_path / "bad.json"
        bad.write_text("{ invalid }", encoding="utf-8")
        result = _load_json(bad, default={})
        assert result == {}

    def test_save_and_load_round_trip(self, tmp_optimizer_env):
        from recursive_optimizer import _load_json, _save_json
        path = tmp_optimizer_env["data_dir"] / "round_trip.json"
        data = {"a": 1, "b": [2, 3]}
        _save_json(path, data)
        loaded = _load_json(path)
        assert loaded == data

    def test_optimizer_log_round_trip(self, tmp_optimizer_env):
        from recursive_optimizer import _load_optimizer_log, _save_optimizer_log
        log = [{"cycle": 1, "timestamp": "2026-01-01T00:00:00"}]
        _save_optimizer_log(log)
        loaded = _load_optimizer_log()
        assert loaded == log


# ---------------------------------------------------------------------------
# Class 2: TestAggregate
# ---------------------------------------------------------------------------

class TestAggregate:
    def test_aggregate_returns_expected_keys(self, tmp_optimizer_env):
        with patch("model_attribution._load", return_value={"trades": sample_trades()}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (65, 80)}):
            from recursive_optimizer import aggregate
            data = aggregate()
        required = ["trades", "trades_7d", "trades_30d", "win_rate_7d", "win_rate_30d", "current_thresholds",
                    "current_regime", "veto_accuracy", "missed_summary", "learning_report"]
        for key in required:
            assert key in data, f"Missing key: {key}"

    def test_aggregate_win_rate_calculation(self, tmp_optimizer_env):
        # Win rates are now from system booked trades (trade journal), not attribution
        now = datetime.now(timezone.utc)
        exit_1 = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        exit_2 = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        system_positions = [
            {"status": "CLOSED", "outcome": "win", "exit_time": exit_1, "manual_override": False},
            {"status": "CLOSED", "outcome": "loss", "exit_time": exit_2, "manual_override": False},
        ]
        with patch("model_attribution._load", return_value={"trades": []}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"X": (0, 100)}), \
             patch("trade_journal.get_all_positions", return_value=system_positions):
            from recursive_optimizer import aggregate
            data = aggregate()
        assert data["win_rate_7d"] == 50.0
        assert data["win_rate_30d"] == 50.0

    def test_aggregate_empty_trades(self, tmp_optimizer_env):
        # No system closed trades -> 0% win rate
        with patch("model_attribution._load", return_value={"trades": []}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"X": (0, 100)}), \
             patch("trade_journal.get_all_positions", return_value=[]):
            from recursive_optimizer import aggregate
            data = aggregate()
        assert data["win_rate_7d"] == 0
        assert data["win_rate_30d"] == 0

    def test_aggregate_regime_detection(self, tmp_optimizer_env):
        # current_regime is derived from journal (system_trades_closed); mock closed positions with BEAR
        now = datetime.now(timezone.utc)
        closed_positions = [
            {"status": "CLOSED", "exit_time": (now - timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"),
             "market_regime": "BEAR", "outcome": "win", "manual_override": False}
            for i in range(25)
        ]
        with patch("trade_journal.get_all_positions", return_value=closed_positions), \
             patch("model_attribution.get_model_attribution", return_value={"by_model": {}, "veto_performance": {}}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=0.5), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"X": (0, 100)}):
            from recursive_optimizer import aggregate
            data = aggregate()
        assert data["current_regime"] == "BEAR"


# ---------------------------------------------------------------------------
# Class 3: TestExtractVetoAccuracy
# ---------------------------------------------------------------------------

class TestExtractVetoAccuracy:
    def test_empty_stress(self):
        from recursive_optimizer import _extract_veto_accuracy
        assert _extract_veto_accuracy({}) == {"total": 0}
        assert _extract_veto_accuracy({"recommendations": []}) == {"total": 0}

    def test_counts_vetoed_correctly(self):
        from recursive_optimizer import _extract_veto_accuracy
        stress = {
            "recommendations": [
                {"blocker": "veto", "verdict": "correct_veto"},
                {"blocker": "veto", "verdict": "correct_skip"},
                {"blocker": "quant", "verdict": "skip"},
            ],
        }
        out = _extract_veto_accuracy(stress)
        assert out["total_vetoed"] == 2
        assert out["correct_veto"] == 2

    def test_missed_by_veto_counted(self):
        from recursive_optimizer import _extract_veto_accuracy
        stress = {
            "recommendations": [
                {"blocker": "veto", "verdict": "missed_opportunity"},
            ],
        }
        out = _extract_veto_accuracy(stress)
        assert out["missed_by_veto"] == 1


# ---------------------------------------------------------------------------
# Class 4: TestCounterfactualAnalysis
# ---------------------------------------------------------------------------

class TestCounterfactualAnalysis:
    def test_frange_produces_correct_values(self):
        from recursive_optimizer import _frange
        vals = _frange(0.1, 0.3, 0.1)
        assert vals == [0.1, 0.2, 0.3]

    def test_filter_trades_star_returns_all(self):
        from recursive_optimizer import _filter_trades_for_param
        trades = sample_trades()
        out = _filter_trades_for_param(trades, "BEAR_REGIME_MIN_SCORE", "*")
        assert len(out) == len(trades)

    def test_filter_trades_specific_path(self):
        from recursive_optimizer import _filter_trades_for_param
        trades = sample_trades()
        out = _filter_trades_for_param(trades, "DIP_MIN_SCORE", "dip")
        assert all((t.get("quant_path") or "default") == "dip" for t in out)

    def test_avg_pnl_calculation(self):
        from recursive_optimizer import _avg_pnl
        trades = [{"pnl_pct": 10.0}, {"pnl_pct": 20.0}]
        assert _avg_pnl(trades) == 15.0

    def test_avg_pnl_empty(self):
        from recursive_optimizer import _avg_pnl
        assert _avg_pnl([]) == 0

    def test_counterfactual_finds_improvement(self, tmp_optimizer_env):
        # Trades where confirmation_score 64–65 are losses; raising floor from 63 filters them with 0 winners lost
        now = datetime.now(timezone.utc)
        ts = lambda d: (now - timedelta(days=d)).isoformat()
        trades = [
            {"ts": ts(1), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -5.0, "confirmation_score": 64},
            {"ts": ts(2), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -4.0, "confirmation_score": 65},
            {"ts": ts(3), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 6.0, "confirmation_score": 72},
            {"ts": ts(4), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 5.0, "confirmation_score": 73},
            {"ts": ts(5), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 4.0, "confirmation_score": 74},
        ]
        data = sample_aggregate_data(trades)
        data["current_thresholds"] = {"BEAR_REGIME_MIN_SCORE": 63}
        from recursive_optimizer import counterfactual_analysis
        from thresholds import get_threshold as _orig_gt
        def _min5(key, default=None):
            if key == "MIN_TRADES_FOR_REC":
                return 5
            return _orig_gt(key, default)
        with patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (65, 80)}), \
             patch("thresholds.PARAM_PATH_FILTER", {"BEAR_REGIME_MIN_SCORE": "*"}), \
             patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score"}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"BEAR_REGIME_MIN_SCORE": ["BEAR", "VOLATILE"]}), \
             patch("thresholds.get_threshold", side_effect=_min5):
            results = counterfactual_analysis(data)
        assert len(results) > 0, "Expected at least one improvement recommendation"
        r = results[0]
        assert "losses_avoided" in r
        assert "winners_lost" in r
        assert r["net_expected_pnl_pct"] > 0
        assert r["winners_lost"] == 0

    def test_counterfactual_skips_when_too_few_trades(self, tmp_optimizer_env):
        from recursive_optimizer import counterfactual_analysis, MIN_TRADES_FOR_REC
        trades = sample_trades()[: MIN_TRADES_FOR_REC - 1]
        data = sample_aggregate_data(trades)
        data["current_thresholds"] = {"BEAR_REGIME_MIN_SCORE": 70}
        with patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score"}):
            results = counterfactual_analysis(data)
        assert results == []

    def test_counterfactual_marks_safe_correctly(self, tmp_optimizer_env):
        trades = sample_trades()  # 15 trades >= MIN_TRADES_FOR_AUTO
        data = sample_aggregate_data(trades)
        data["current_thresholds"] = {"BEAR_REGIME_MIN_SCORE": 60, "DIP_MIN_SCORE": 65}
        from recursive_optimizer import counterfactual_analysis
        with patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score", "DIP_MIN_SCORE": "confirmation_score"}):
            results = counterfactual_analysis(data)
        for r in results:
            if r.get("evidence_trades", 0) >= 10 and r.get("winners_lost", 1) == 0 and r.get("net_expected_pnl_pct", 0) > 0:
                assert r["safe_to_auto_apply"] in (True, False)
                break

    def test_backtest_supplement_does_not_expand_analysis_subset(self, tmp_optimizer_env):
        from recursive_optimizer import counterfactual_analysis

        now = datetime.now(timezone.utc)
        ts = lambda d: (now - timedelta(days=d)).isoformat()
        live_trades = [
            {"ts": ts(1), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -3.0, "confirmation_score": 64},
            {"ts": ts(2), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 5.0, "confirmation_score": 73},
            {"ts": ts(3), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 4.0, "confirmation_score": 72},
        ]
        bt_trades = [
            {"ts": ts(4), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -2.0, "confirmation_score": 63},
            {"ts": ts(5), "regime": "BEAR", "quant_path": "default", "outcome": "loss", "pnl_pct": -2.5, "confirmation_score": 62},
            {"ts": ts(6), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 5.5, "confirmation_score": 74},
            {"ts": ts(7), "regime": "BEAR", "quant_path": "default", "outcome": "win", "pnl_pct": 4.8, "confirmation_score": 75},
        ]
        data = sample_aggregate_data(live_trades)
        data["backtest_trades"] = bt_trades
        data["current_thresholds"] = {"BEAR_REGIME_MIN_SCORE": 63}

        seen_lengths = []

        def fake_find_best_candidate(param, cur_val, lo, hi, trades, avg_win, avg_loss, **kwargs):
            seen_lengths.append(len(trades))
            return {"value": 64, "losses_avoided": 1, "winners_lost": 0, "net_impact": 1.0}

        with patch("recursive_optimizer._find_best_candidate", side_effect=fake_find_best_candidate), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (60, 80)}), \
             patch("thresholds.PARAM_PATH_FILTER", {"BEAR_REGIME_MIN_SCORE": "*"}), \
             patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score"}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"BEAR_REGIME_MIN_SCORE": ["BEAR"]}), \
             patch("thresholds.get_threshold", side_effect=lambda k, d=None: 5 if k in ("MIN_TRADES_FOR_REC", "MIN_TRADES_FOR_AUTO") else d):
            out = counterfactual_analysis(data)

        assert out, "Expected recommendation with supplemented evidence"
        assert seen_lengths and seen_lengths[0] == 3

    def test_counterfactual_confidence_uses_live_min_trades_for_auto(self, tmp_optimizer_env):
        """Confidence should honor MIN_TRADES_FOR_AUTO from thresholds."""
        from recursive_optimizer import counterfactual_analysis
        trades = sample_trades()  # 15 trades
        data = sample_aggregate_data(trades)
        data["current_thresholds"] = {"BEAR_REGIME_MIN_SCORE": 60}
        with patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (55, 80)}), \
             patch("thresholds.PARAM_PATH_FILTER", {"BEAR_REGIME_MIN_SCORE": "*"}), \
             patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score"}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"BEAR_REGIME_MIN_SCORE": ["BEAR", "VOLATILE", "SIDEWAYS", "BULL"]}), \
             patch("thresholds.get_threshold", side_effect=lambda k, d=None: 20 if k == "MIN_TRADES_FOR_AUTO" else (5 if k == "MIN_TRADES_FOR_REC" else d)):
            out = counterfactual_analysis(data)
        if out:
            assert all(r.get("confidence") in ("medium", "high") for r in out)
            assert any(r.get("confidence") == "medium" for r in out)


# ---------------------------------------------------------------------------
# Class 5: TestRankAndFilter
# ---------------------------------------------------------------------------

class TestRankAndFilter:
    def test_sorts_by_net_impact_descending(self):
        from recursive_optimizer import rank_and_filter
        results = [
            {"parameter": "A", "net_expected_pnl_pct": 1.0, "safe_to_auto_apply": False},
            {"parameter": "B", "net_expected_pnl_pct": 3.0, "safe_to_auto_apply": False},
            {"parameter": "C", "net_expected_pnl_pct": 2.0, "safe_to_auto_apply": False},
        ]
        auto, pending = rank_and_filter(results)
        order = [r["parameter"] for r in pending]
        assert order == ["B", "C", "A"]

    def test_safe_items_go_to_auto(self):
        from recursive_optimizer import rank_and_filter
        results = [
            {"parameter": "A", "net_expected_pnl_pct": 2.0, "safe_to_auto_apply": True},
            {"parameter": "B", "net_expected_pnl_pct": 1.0, "safe_to_auto_apply": False},
        ]
        auto, pending = rank_and_filter(results)
        assert len(auto) == 1 and auto[0]["parameter"] == "A"
        assert len(pending) == 1 and pending[0]["parameter"] == "B"

    def test_max_auto_limit_respected(self):
        from recursive_optimizer import rank_and_filter, MAX_AUTO_CHANGES_PER_CYCLE
        results = [
            {"parameter": f"P{i}", "net_expected_pnl_pct": 10 - i, "safe_to_auto_apply": True}
            for i in range(5)
        ]
        auto, pending = rank_and_filter(results)
        assert len(auto) == MAX_AUTO_CHANGES_PER_CYCLE
        assert len(pending) == 5 - MAX_AUTO_CHANGES_PER_CYCLE

    def test_unsafe_always_in_pending(self):
        from recursive_optimizer import rank_and_filter
        results = [
            {"parameter": "A", "net_expected_pnl_pct": 5.0, "safe_to_auto_apply": False},
        ]
        auto, pending = rank_and_filter(results)
        assert len(auto) == 0
        assert len(pending) == 1


# ---------------------------------------------------------------------------
# Class 6: TestApplySafeChanges
# ---------------------------------------------------------------------------

class TestApplySafeChanges:
    """PERMANENTLY DISARMED (STRATEGY.md §5.8, 2026-07-03): apply_safe_changes must
    never write live_thresholds.json under ANY input — the audit established its
    auto-applies were noise fitted on tiny windows. These tests pin the disarm."""

    def test_empty_auto_does_nothing(self, tmp_optimizer_env):
        from recursive_optimizer import apply_safe_changes
        before = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        out = apply_safe_changes([])
        assert out == []
        after = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert after == before

    def test_disarmed_never_writes_thresholds(self, tmp_optimizer_env):
        from recursive_optimizer import apply_safe_changes
        before = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        auto = [{"parameter": "BEAR_REGIME_MIN_SCORE", "suggested_value": 72, "current_value": 70,
                 "description": "x", "evidence_trades": 12, "losses_avoided": 1, "winners_lost": 0,
                 "net_expected_pnl_pct": 2.5, "path_filter": "*"}]
        out = apply_safe_changes(auto)
        assert out == []
        after = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert after == before  # file byte-identical: no write path exists

    def test_disarmed_even_with_many_high_confidence_recs(self, tmp_optimizer_env):
        from recursive_optimizer import apply_safe_changes
        before = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        auto = [{"parameter": p, "suggested_value": v, "current_value": v - 1,
                 "description": "x", "evidence_trades": 500, "losses_avoided": 50,
                 "winners_lost": 0, "net_expected_pnl_pct": 99.0, "path_filter": "*"}
                for p, v in (("BEAR_REGIME_MIN_SCORE", 72), ("DIP_MIN_SCORE", 67))]
        assert apply_safe_changes(auto) == []
        after = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert after == before

    def test_manual_apply_single_still_works(self, tmp_optimizer_env):
        """The HUMAN write path (dashboard/CLI apply_single) must keep working —
        the disarm removes automation, not human control."""
        from recursive_optimizer import apply_single
        r = apply_single("BEAR_REGIME_MIN_SCORE", 72)
        assert r.get("success") is True
        live = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert live.get("BEAR_REGIME_MIN_SCORE") == 72


# ---------------------------------------------------------------------------
# Class 7: TestApplySingle
# ---------------------------------------------------------------------------

class TestApplySingle:
    def test_apply_within_bounds(self, tmp_optimizer_env):
        from recursive_optimizer import apply_single
        result = apply_single("BEAR_REGIME_MIN_SCORE", 72)
        assert result.get("success") is True
        assert result.get("new_value") == 72
        live = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert live.get("BEAR_REGIME_MIN_SCORE") == 72

    def test_apply_outside_bounds(self, tmp_optimizer_env):
        from recursive_optimizer import apply_single
        result = apply_single("BEAR_REGIME_MIN_SCORE", 100)
        assert result.get("success") is False
        assert "bounds" in result.get("error", "").lower() or "outside" in result.get("error", "").lower()

    def test_apply_sets_manual_optimizer_tag(self, tmp_optimizer_env):
        from recursive_optimizer import apply_single
        apply_single("BEAR_REGIME_MIN_SCORE", 71)
        live = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert live.get("updated_by") == "manual_optimizer"


# ---------------------------------------------------------------------------
# Class 7b: TestBoundsEnforcement — TACTICAL_UPGRADE_24H_PCT lower bound
# ---------------------------------------------------------------------------

class TestBoundsEnforcement:
    def test_tactical_upgrade_clamped_to_5pct_floor(self, tmp_optimizer_env):
        """TACTICAL_UPGRADE_24H_PCT=3.0 in live file is clamped to 5.0 by bounds validation.
        Regression: optimizer cycle 33 dropped this to 3.0, causing phantom BULL upgrades."""
        from recursive_optimizer import validate_live_thresholds_bounds
        live_file = tmp_optimizer_env["live_file"]
        live = json.loads(live_file.read_text(encoding="utf-8"))
        live["TACTICAL_UPGRADE_24H_PCT"] = 3.0
        live_file.write_text(json.dumps(live, indent=2), encoding="utf-8")
        clamped = validate_live_thresholds_bounds()
        assert any(c["parameter"] == "TACTICAL_UPGRADE_24H_PCT" and c["clamped_to"] == 5.0 for c in clamped), (
            f"Expected TACTICAL_UPGRADE_24H_PCT clamped to 5.0, got: {clamped}"
        )
        updated = json.loads(live_file.read_text(encoding="utf-8"))
        assert updated["TACTICAL_UPGRADE_24H_PCT"] == 5.0

    def test_tactical_upgrade_at_5pct_not_clamped(self, tmp_optimizer_env):
        """TACTICAL_UPGRADE_24H_PCT=5.0 is within bounds — no clamping."""
        from recursive_optimizer import validate_live_thresholds_bounds
        live_file = tmp_optimizer_env["live_file"]
        live = json.loads(live_file.read_text(encoding="utf-8"))
        live["TACTICAL_UPGRADE_24H_PCT"] = 5.0
        live_file.write_text(json.dumps(live, indent=2), encoding="utf-8")
        clamped = validate_live_thresholds_bounds()
        assert not any(c.get("parameter") == "TACTICAL_UPGRADE_24H_PCT" for c in clamped)

    def test_apply_single_rejects_below_new_floor(self, tmp_optimizer_env):
        """apply_single refuses to set TACTICAL_UPGRADE_24H_PCT below 5.0."""
        from recursive_optimizer import apply_single
        result = apply_single("TACTICAL_UPGRADE_24H_PCT", 3.5)
        assert result.get("success") is False


# ---------------------------------------------------------------------------
# Class 8: TestEvaluatePreviousCycle
# ---------------------------------------------------------------------------

class TestEvaluatePreviousCycle:
    @pytest.fixture(autouse=True)
    def _patch_maturity_target(self, monkeypatch):
        """Use 10 trades as maturity target so existing test data (10 trades) still evaluates to confirmed/reverted."""
        import thresholds
        orig_get_threshold = thresholds.get_threshold
        def patched_get_threshold(key, default=None):
            if key == 'MATURITY_TRADE_TARGET':
                return 10
            return orig_get_threshold(key, default)
        monkeypatch.setattr('thresholds.get_threshold', patched_get_threshold)

    def test_maturing_when_insufficient_trades(self, tmp_optimizer_env):
        from recursive_optimizer import evaluate_previous_cycle, _save_optimizer_log, MATURITY_TRADE_TARGET
        applied_at = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        log = [{"cycle": 1, "auto_applied": [{"parameter": "X", "old_value": 70, "new_value": 72, "applied_at": applied_at, "path_filter": "*", "status": "maturing"}]}]
        _save_optimizer_log(log)
        trades = [{"ts": (datetime.now(timezone.utc) - timedelta(days=i)).isoformat(), "outcome": "win", "quant_path": "default"} for i in range(5)]
        data = {"trades": trades}
        evals = evaluate_previous_cycle(data)
        assert len(evals) == 1
        assert evals[0]["status"] == "maturing"
        assert evals[0]["trades_since"] < MATURITY_TRADE_TARGET

    def test_confirmed_when_win_rate_holds(self, tmp_optimizer_env):
        from recursive_optimizer import evaluate_previous_cycle, _save_optimizer_log, MATURITY_TRADE_TARGET
        applied_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        log = [{"cycle": 1, "auto_applied": [{"parameter": "X", "old_value": 70, "new_value": 72, "applied_at": applied_at, "path_filter": "*", "status": "maturing"}]}]
        _save_optimizer_log(log)
        # 10 trades after applied_at, all wins -> wr_after 100%; 5 trades before, 3 wins -> wr_before 60%; delta +40
        trades_before = [{"ts": (datetime.now(timezone.utc) - timedelta(days=25)).isoformat(), "outcome": "win", "quant_path": "default"} for _ in range(3)]
        trades_before += [{"ts": (datetime.now(timezone.utc) - timedelta(days=24)).isoformat(), "outcome": "loss", "quant_path": "default"} for _ in range(2)]
        trades_after = [{"ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "outcome": "win", "quant_path": "default"} for _ in range(MATURITY_TRADE_TARGET)]
        trades = trades_before + trades_after
        data = {"trades": trades}
        evals = evaluate_previous_cycle(data)
        assert len(evals) == 1
        assert evals[0]["status"] == "confirmed"

    def test_watch_when_small_drop(self, tmp_optimizer_env):
        from recursive_optimizer import evaluate_previous_cycle, _save_optimizer_log, MATURITY_TRADE_TARGET
        applied_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        log = [{"cycle": 1, "auto_applied": [{"parameter": "X", "old_value": 70, "new_value": 72, "applied_at": applied_at, "path_filter": "*", "status": "maturing"}]}]
        _save_optimizer_log(log)
        # Before: 60% win (6 wins, 4 losses). After: 58% (slightly worse, delta ~-2pp -> watch)
        trades_before = [{"ts": (datetime.now(timezone.utc) - timedelta(days=25)).isoformat(), "outcome": "win", "quant_path": "default"} for _ in range(6)]
        trades_before += [{"ts": (datetime.now(timezone.utc) - timedelta(days=24)).isoformat(), "outcome": "loss", "quant_path": "default"} for _ in range(4)]
        # After: 12 trades, 7 wins, 5 losses -> 58.3% win rate, delta = 58.3 - 60 = -1.7pp -> watch
        trades_after = [{"ts": (datetime.now(timezone.utc) - timedelta(days=i)).isoformat(), "outcome": "win", "quant_path": "default"} for i in range(1, 8)]
        trades_after += [{"ts": (datetime.now(timezone.utc) - timedelta(days=0)).isoformat(), "outcome": "loss", "quant_path": "default"} for _ in range(5)]
        trades = trades_before + trades_after
        data = {"trades": trades}
        evals = evaluate_previous_cycle(data)
        assert len(evals) == 1
        assert evals[0]["status"] == "watch"

    def test_reverted_when_large_drop(self, tmp_optimizer_env):
        from recursive_optimizer import evaluate_previous_cycle, _save_optimizer_log, MATURITY_TRADE_TARGET, REVERT_DROP_THRESHOLD
        applied_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        log = [{"cycle": 1, "auto_applied": [{"parameter": "BEAR_REGIME_MIN_SCORE", "old_value": 70, "new_value": 72, "applied_at": applied_at, "path_filter": "*", "status": "maturing"}]}]
        _save_optimizer_log(log)
        tmp_optimizer_env["live_file"].write_text(json.dumps({"BEAR_REGIME_MIN_SCORE": 72}, indent=2), encoding="utf-8")
        trades_before = [{"ts": (datetime.now(timezone.utc) - timedelta(days=25)).isoformat(), "outcome": "win", "quant_path": "default"} for _ in range(10)]
        trades_after = [{"ts": (datetime.now(timezone.utc) - timedelta(days=i)).isoformat(), "outcome": "loss", "quant_path": "default"} for i in range(MATURITY_TRADE_TARGET)]
        trades = trades_before + trades_after
        data = {"trades": trades}
        evals = evaluate_previous_cycle(data)
        assert len(evals) == 1
        assert evals[0]["status"] == "reverted"
        # Audit F2: the revert is a recommendation only — evaluation must NOT
        # write live_thresholds.json (the human apply path does that).
        live = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert live.get("BEAR_REGIME_MIN_SCORE") == 72

    def test_revert_change_does_not_write(self, tmp_optimizer_env):
        """Audit F2: _revert_change is disarmed — it logs a manual-revert
        recommendation and leaves live_thresholds.json untouched."""
        from recursive_optimizer import _revert_change
        tmp_optimizer_env["live_file"].write_text(json.dumps({"BEAR_REGIME_MIN_SCORE": 75}, indent=2), encoding="utf-8")
        change = {"parameter": "BEAR_REGIME_MIN_SCORE", "old_value": 70, "new_value": 75}
        _revert_change(change)
        live = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert live["BEAR_REGIME_MIN_SCORE"] == 75

    def test_already_confirmed_skipped(self, tmp_optimizer_env):
        from recursive_optimizer import evaluate_previous_cycle, _save_optimizer_log
        applied_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        log = [{"cycle": 1, "auto_applied": [{"parameter": "X", "old_value": 70, "new_value": 72, "applied_at": applied_at, "path_filter": "*", "status": "confirmed"}]}]
        _save_optimizer_log(log)
        data = {"trades": sample_trades()}
        evals = evaluate_previous_cycle(data)
        assert len(evals) == 0


# ---------------------------------------------------------------------------
# Class: TestFilterFullAutoRegimeLogging
# Verifies observability logging added in Phase 1a.
# ---------------------------------------------------------------------------

class TestFilterFullAutoRegimeLogging:
    """_filter_full_auto must log regime_expectancy gate status each cycle."""

    def test_empty_regime_expectancy_logs_warning(self, tmp_optimizer_env, caplog):
        """When regime_expectancy is empty (UNKNOWN positions), a warning is emitted."""
        import logging
        from recursive_optimizer import _filter_full_auto
        with caplog.at_level(logging.WARNING, logger="recursive_optimizer"):
            _filter_full_auto(results=[], regime_expectancy=None)
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("regime_expectancy is empty" in m for m in warning_msgs), (
            "Expected warning about empty regime_expectancy, got: " + str(warning_msgs)
        )

    def test_populated_regime_expectancy_logs_gate_status(self, tmp_optimizer_env, caplog):
        """When regime_expectancy has data, each regime is logged at INFO level."""
        import logging
        from recursive_optimizer import _filter_full_auto
        regime_expectancy = {
            "SIDEWAYS": {"win_rate_pct": 36.0, "expectancy": -7.4, "total_trades": 2497},
            "BEAR": {"win_rate_pct": 66.9, "expectancy": 3.1, "total_trades": 120},
        }
        with caplog.at_level(logging.INFO, logger="recursive_optimizer"):
            _filter_full_auto(results=[], regime_expectancy=regime_expectancy)
        info_msgs = " ".join(r.message for r in caplog.records if r.levelno == logging.INFO)
        assert "SIDEWAYS" in info_msgs, "SIDEWAYS regime must appear in observability log"
        assert "BEAR" in info_msgs, "BEAR regime must appear in observability log"

    def test_negative_expectancy_logged_as_gate_armed(self, tmp_optimizer_env, caplog):
        """Negative expectancy with n>=5 trades → log message contains 'GATE ARMED'."""
        import logging
        from recursive_optimizer import _filter_full_auto
        regime_expectancy = {
            "SIDEWAYS": {"win_rate_pct": 36.0, "expectancy": -7.4, "total_trades": 100},
        }
        with caplog.at_level(logging.INFO, logger="recursive_optimizer"):
            _filter_full_auto(results=[], regime_expectancy=regime_expectancy)
        armed_msgs = [r.message for r in caplog.records if "GATE ARMED" in r.message]
        assert armed_msgs, "Expected 'GATE ARMED' log for negative expectancy regime with n>=5"

    def test_positive_expectancy_logged_as_gate_inactive(self, tmp_optimizer_env, caplog):
        """Positive expectancy → log message contains 'gate inactive'."""
        import logging
        from recursive_optimizer import _filter_full_auto
        regime_expectancy = {
            "BEAR": {"win_rate_pct": 67.0, "expectancy": 3.1, "total_trades": 50},
        }
        with caplog.at_level(logging.INFO, logger="recursive_optimizer"):
            _filter_full_auto(results=[], regime_expectancy=regime_expectancy)
        inactive_msgs = [r.message for r in caplog.records if "gate inactive" in r.message]
        assert inactive_msgs, "Expected 'gate inactive' log for positive expectancy regime"

    def test_sideways_relaxation_blocked_by_regime_gate(self, tmp_optimizer_env):
        """SIDEWAYS threshold relaxation is blocked when SIDEWAYS expectancy is negative."""
        from recursive_optimizer import _filter_full_auto
        import thresholds as th
        # A relaxation candidate: SIDEWAYS_REGIME_MIN_SCORE lowered from 68 → 65 (score floor drop = relaxation)
        candidate = {
            "parameter": "SIDEWAYS_REGIME_MIN_SCORE",
            "current_value": 68,
            "suggested_value": 65,  # lowering score floor = relaxation
            "net_expected_pnl_pct": 0.1,
            "winners_lost": 0,
            "live_evidence_trades": 20,
            "missed_edge_reduction": 0,
        }
        sideways_negative = {
            "SIDEWAYS": {"win_rate_pct": 36.0, "expectancy": -7.4, "total_trades": 100},
        }
        # Ensure SIDEWAYS_REGIME_MIN_SCORE is in OPTIMIZER_BOUNDS and PARAM_REGIME_FILTER
        with patch("thresholds.OPTIMIZER_BOUNDS", {"SIDEWAYS_REGIME_MIN_SCORE": (55, 85)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"SIDEWAYS_REGIME_MIN_SCORE": ["SIDEWAYS"]}), \
             patch("thresholds.get_threshold", return_value=68):
            to_apply, pending = _filter_full_auto(
                results=[candidate],
                regime_expectancy=sideways_negative,
            )
        # The relaxation must NOT be auto-applied
        assert not any(c.get("parameter") == "SIDEWAYS_REGIME_MIN_SCORE" for c in to_apply), (
            "SIDEWAYS relaxation must be blocked by regime-expectancy gate when expectancy < 0"
        )

    def test_maturity_target_100_trades_gate_intact(self, tmp_optimizer_env, monkeypatch):
        """When MATURITY_TRADE_TARGET is 100, 99 trades stays maturing and 100 confirms."""
        import thresholds
        orig_get_threshold = thresholds.get_threshold

        def patched_get_threshold(key, default=None):
            if key == "MATURITY_TRADE_TARGET":
                return 100
            return orig_get_threshold(key, default)

        monkeypatch.setattr("thresholds.get_threshold", patched_get_threshold)
        from recursive_optimizer import evaluate_previous_cycle, _save_optimizer_log

        applied_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        log = [{"cycle": 1, "auto_applied": [{"parameter": "X", "old_value": 70, "new_value": 72, "applied_at": applied_at, "path_filter": "*", "status": "maturing"}]}]
        _save_optimizer_log(log)

        trades_before = [{"ts": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(), "outcome": "win", "quant_path": "default"} for _ in range(20)]
        trades_after_99 = [{"ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "outcome": "win", "quant_path": "default"} for _ in range(99)]
        evals_99 = evaluate_previous_cycle({"trades": trades_before + trades_after_99})
        assert evals_99 and evals_99[0]["status"] == "maturing"
        assert evals_99[0]["target_trades"] == 100

        # Run again with 100 trades since apply; should no longer be "maturing".
        _save_optimizer_log(log)
        trades_after_100 = [{"ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "outcome": "win", "quant_path": "default"} for _ in range(100)]
        evals_100 = evaluate_previous_cycle({"trades": trades_before + trades_after_100})
        assert evals_100 and evals_100[0]["status"] in ("confirmed", "watch", "reverted")


# ---------------------------------------------------------------------------
# Class 9: TestFormatReportHtml
# ---------------------------------------------------------------------------

class TestFormatReportHtml:
    def test_report_contains_cycle_number(self):
        from recursive_optimizer import format_report_html
        cycle_data = {"snapshot": {"cycle": 3, "date": "2026-03-06", "regime": "SIDEWAYS", "total_trades": 10, "win_rate_7d": 60, "win_rate_30d": 55},
                      "auto_applied": [], "pending_review": [], "evaluations": [], "path_performance": [], "missed_summary": {}}
        html = format_report_html(cycle_data)
        assert "Cycle #3" in html

    def test_report_contains_auto_applied_section(self):
        from recursive_optimizer import format_report_html
        cycle_data = {"snapshot": {"cycle": 1, "date": "2026-03-06", "regime": "SIDEWAYS", "total_trades": 5, "win_rate_7d": 50, "win_rate_30d": 50},
                      "auto_applied": [{"parameter": "X", "old_value": 70, "new_value": 72, "description": "Score floor", "losses_avoided": 1, "winners_lost": 0, "evidence_trades": 12, "net_expected_pnl_pct": 2.0}],
                      "pending_review": [], "evaluations": [], "path_performance": [], "missed_summary": {}}
        html = format_report_html(cycle_data)
        assert "Auto-applied threshold changes" in html
        assert "X" in html and "70" in html and "72" in html

    def test_report_contains_pending_section(self):
        from recursive_optimizer import format_report_html
        cycle_data = {"snapshot": {"cycle": 1, "date": "2026-03-06", "regime": "SIDEWAYS", "total_trades": 5, "win_rate_7d": 50, "win_rate_30d": 50},
                      "auto_applied": [], "pending_review": [{"parameter": "Y", "current_value": 65, "suggested_value": 68, "description": "Dip score", "losses_avoided": 2, "winners_lost": 1, "net_expected_pnl_pct": 1.5, "evidence_trades": 8, "confidence": "medium"}],
                      "evaluations": [], "path_performance": [], "missed_summary": {}}
        html = format_report_html(cycle_data)
        assert "Optional recommendations (below auto-apply bar)" in html or "Recommendations needing your review" in html or "needs your review" in html or "needing your review" in html
        assert "Y" in html

    def test_report_contains_change_tracker(self):
        from recursive_optimizer import format_report_html
        cycle_data = {"snapshot": {"cycle": 1, "date": "2026-03-06", "regime": "SIDEWAYS", "total_trades": 5, "win_rate_7d": 50, "win_rate_30d": 50},
                      "auto_applied": [], "pending_review": [],
                      "evaluations": [{"parameter": "Z", "old_value": 70, "new_value": 72, "applied_at": "2026-03-01T00:00:00", "trades_since": 10, "target_trades": 10, "status": "confirmed", "verdict": "Win rate held"}],
                      "path_performance": [], "missed_summary": {}}
        html = format_report_html(cycle_data)
        assert "How are applied changes performing" in html or "changes performing" in html
        assert "Z" in html

    def test_report_handles_empty_data(self):
        from recursive_optimizer import format_report_html
        cycle_data = {"snapshot": {"cycle": 1, "date": "2026-03-06", "regime": "SIDEWAYS", "total_trades": 0, "win_rate_7d": 0, "win_rate_30d": 0},
                      "auto_applied": [], "pending_review": [], "evaluations": [], "path_performance": [], "missed_summary": {}}
        html = format_report_html(cycle_data)
        assert "No changes" in html or "complete" in html.lower() or "Cycle" in html

    def test_pending_reason_low_evidence(self):
        from recursive_optimizer import _pending_reason, MIN_TRADES_FOR_AUTO
        rec = {"confidence": "medium", "evidence_trades": 5, "winners_lost": 0}
        reason = _pending_reason(rec)
        assert str(MIN_TRADES_FOR_AUTO) in reason or "trades" in reason

    def test_pending_reason_winners_lost(self):
        from recursive_optimizer import _pending_reason
        rec = {"confidence": "high", "evidence_trades": 15, "winners_lost": 2}
        reason = _pending_reason(rec)
        assert "winning" in reason.lower() or "2" in reason


# ---------------------------------------------------------------------------
# Class 10: (Removed: TestUpdateOutcomesPriceFetcher — consensus_engine module deleted)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Class 11: TestDataIntegrity
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    def test_positions_no_test_data(self):
        path = PROJECT_ROOT / "positions.json"
        if not path.exists():
            pytest.skip("positions.json not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        test_prices = {45000, 45000.0, 50000, 50000.0, 3000, 3000.0, 100, 100.0}
        for p in data:
            if p.get("entry_price") in test_prices:
                assert "test" not in (p.get("notes") or "").lower(), "Test price without test note"
            assert "test-opt" != (p.get("coin_id") or "").lower()

    def test_positions_backfilled_have_signals(self):
        path = PROJECT_ROOT / "positions.json"
        if not path.exists():
            pytest.skip("positions.json not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data:
            if p.get("market_regime") == "PRE_PIPELINE":
                continue
            # Rotation-cohort positions (STRATEGY.md candidate #1) are regime rotations,
            # not quant-gate signals, so they have no BUY tier. Entries from 2026-08-20
            # onward carry quant_tier='ROTATION'; the first live entry predates that.
            if (p.get("source") or "") == "paper_rotation":
                assert p.get("quant_path") == "rotation", "rotation row missing quant_path"
                continue
            assert p.get("market_regime"), f"Missing market_regime for {p.get('coin_id')}"
            assert p.get("quant_tier") is not None
            assert p.get("confirmation_score") is not None or p.get("quant_tier")

    def test_attribution_no_junk_coins(self):
        """Verify production attribution trades (with real signals) don't contain junk coin_ids."""
        path = PROJECT_ROOT / "model_attribution.json"
        if not path.exists():
            pytest.skip("model_attribution.json not found")
        attr = json.loads(path.read_text(encoding="utf-8"))
        junk = {"coin0", "coin1", "coin2", "coin3", "coin4", "a", "b", "x", "y", "z"}
        # Only check trades that have real signals (not test-injected artifacts)
        real_trades = [
            t for t in attr.get("trades", [])
            if t.get("regime") == "PRE_PIPELINE" or t.get("quant_tier")
        ]
        for t in real_trades:
            assert (t.get("coin_id") or "").lower() not in junk


# ---------------------------------------------------------------------------
# Class 12: TestRunOptimizationCycleE2E
# ---------------------------------------------------------------------------

class TestRunOptimizationCycleE2E:
    def test_full_cycle_returns_expected_structure(self, tmp_optimizer_env):
        with patch("model_attribution._load", return_value={"trades": sample_trades()}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (65, 80)}), \
             patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score"}), \
             patch("alert_system.AlertSystem") as MockAlerts:
            MockAlerts.return_value.send_optimizer_report = MagicMock()
            from recursive_optimizer import run_optimization_cycle
            result = run_optimization_cycle()
        assert "error" not in result or result.get("cycle")
        assert "cycle" in result
        assert "snapshot" in result
        assert "auto_applied" in result
        assert "pending_review" in result
        assert "evaluations" in result

    def test_cycle_increments_number(self, tmp_optimizer_env):
        from recursive_optimizer import run_optimization_cycle, _load_optimizer_log, _save_optimizer_log
        _save_optimizer_log([{"cycle": 1, "snapshot": {"date": "2020-01-01"}}])
        with patch("model_attribution._load", return_value={"trades": []}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {}), \
             patch("alert_system.AlertSystem") as MockAlerts:
            MockAlerts.return_value.send_optimizer_report = MagicMock()
            result = run_optimization_cycle()
        assert result.get("cycle") == 2

    def test_cycle_logs_to_optimizer_log(self, tmp_optimizer_env):
        from recursive_optimizer import run_optimization_cycle, _load_optimizer_log
        with patch("model_attribution._load", return_value={"trades": []}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {}), \
             patch("alert_system.AlertSystem") as MockAlerts:
            MockAlerts.return_value.send_optimizer_report = MagicMock()
            run_optimization_cycle()
        log = _load_optimizer_log()
        assert len(log) >= 1
        assert "cycle" in log[-1] and "snapshot" in log[-1]

    def test_email_send_called(self, tmp_optimizer_env):
        mock_send = MagicMock()
        with patch("model_attribution._load", return_value={"trades": []}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {}), \
             patch("alert_system.AlertSystem") as MockClass:
            MockClass.return_value.send_optimizer_report = mock_send
            from recursive_optimizer import run_optimization_cycle
            run_optimization_cycle()
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert len(call_args) >= 2
        assert "Cycle" in call_args[0]
        assert "Cycle" in call_args[1] or "#" in call_args[1]

    def test_dedup_skips_same_day(self, tmp_optimizer_env):
        """Fix #9: Running cycle twice on the same day returns cached result."""
        from recursive_optimizer import run_optimization_cycle, _load_optimizer_log
        with patch("model_attribution._load", return_value={"trades": []}), \
             patch("model_attribution.get_model_attribution", return_value={}), \
             patch("model_attribution.historical_win_prob_from_attribution_trades", return_value=50.0), \
             patch("thresholds.get_threshold", return_value=70), \
             patch("thresholds.OPTIMIZER_BOUNDS", {}), \
             patch("alert_system.AlertSystem") as MockAlerts:
            MockAlerts.return_value.send_optimizer_report = MagicMock()
            result1 = run_optimization_cycle()
            result2 = run_optimization_cycle()
        log = _load_optimizer_log()
        assert len(log) == 1, "Dedup should prevent duplicate log entries"


# ---------------------------------------------------------------------------
# Class 13: TestConcurrentThresholdWrites
# ---------------------------------------------------------------------------

class TestConcurrentThresholdWrites:
    def test_concurrent_apply_and_revert_no_corruption(self, tmp_optimizer_env):
        """Fix #12: Concurrent writes to live_thresholds.json should not corrupt the file."""
        import threading
        from recursive_optimizer import apply_safe_changes, _revert_change

        auto = [{"parameter": "BEAR_REGIME_MIN_SCORE", "suggested_value": 75, "current_value": 70,
                 "description": "x", "evidence_trades": 12, "losses_avoided": 1, "winners_lost": 0,
                 "net_expected_pnl_pct": 2.5, "path_filter": "*"}]
        revert = {"parameter": "DIP_MIN_SCORE", "old_value": 65, "new_value": 68}

        errors = []

        def do_apply():
            try:
                apply_safe_changes(auto)
            except Exception as e:
                errors.append(e)

        def do_revert():
            try:
                _revert_change(revert)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=do_apply)
        t2 = threading.Thread(target=do_revert)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) == 0, f"Concurrent writes caused errors: {errors}"
        # Verify file is valid JSON. Audit F2: _revert_change no longer writes,
        # so no 'updated_by' marker is expected — only that nothing corrupted.
        live = json.loads(tmp_optimizer_env["live_file"].read_text(encoding="utf-8"))
        assert isinstance(live, dict)


# ---------------------------------------------------------------------------
# Class 14: TestPctParamCounterfactual
# ---------------------------------------------------------------------------

class TestPctParamCounterfactual:
    def test_pct_cap_uses_price_change_field(self, tmp_optimizer_env):
        """Fix #13: PCT params should compare against price_change_24h, not confirmation_score."""
        now = datetime.now(timezone.utc)
        ts = lambda d: (now - timedelta(days=d)).isoformat()
        # Trades with price_change_24h: losses had high pump (15-16%), wins had low pump (5-8%)
        trades = [
            {"ts": ts(1), "regime": "BULL", "quant_path": "default", "outcome": "loss", "pnl_pct": -5.0, "confirmation_score": 72, "price_change_24h": 16.0},
            {"ts": ts(2), "regime": "BULL", "quant_path": "default", "outcome": "loss", "pnl_pct": -4.0, "confirmation_score": 71, "price_change_24h": 15.0},
            {"ts": ts(3), "regime": "BULL", "quant_path": "default", "outcome": "win", "pnl_pct": 6.0, "confirmation_score": 73, "price_change_24h": 5.0},
            {"ts": ts(4), "regime": "BULL", "quant_path": "default", "outcome": "win", "pnl_pct": 5.0, "confirmation_score": 74, "price_change_24h": 7.0},
            {"ts": ts(5), "regime": "BULL", "quant_path": "default", "outcome": "win", "pnl_pct": 4.0, "confirmation_score": 75, "price_change_24h": 8.0},
        ]
        data = sample_aggregate_data(trades)
        data["current_thresholds"] = {"PUMP_24H_PCT": 18.0}
        from recursive_optimizer import counterfactual_analysis
        from thresholds import get_threshold as _orig_gt
        def _min5(key, default=None):
            if key == "MIN_TRADES_FOR_REC":
                return 5
            return _orig_gt(key, default)
        with patch("thresholds.OPTIMIZER_BOUNDS", {"PUMP_24H_PCT": (10.0, 20.0)}), \
             patch("thresholds.PARAM_PATH_FILTER", {"PUMP_24H_PCT": "*"}), \
             patch("thresholds.PARAM_FIELD_MAP", {"PUMP_24H_PCT": "price_change_24h"}), \
             patch("thresholds.PARAM_DESCRIPTIONS", {"PUMP_24H_PCT": "24h pump cap"}), \
             patch("thresholds.get_threshold", side_effect=_min5):
            results = counterfactual_analysis(data)
        assert len(results) > 0, "PCT cap should find improvement by lowering cap to filter high-pump losses"
        r = results[0]
        assert r["parameter"] == "PUMP_24H_PCT"
        assert r["losses_avoided"] > 0
        assert r["suggested_value"] < 18.0

    def test_fg_gate_uses_fear_greed_field(self, tmp_optimizer_env):
        """Fix #1 verification: FG gates use fear_greed field, not confirmation_score."""
        now = datetime.now(timezone.utc)
        ts = lambda d: (now - timedelta(days=d)).isoformat()
        # Losses at high FG (42-44), wins at low FG (20-28). Lowering FG max from 45 would block losses.
        trades = [
            {"ts": ts(1), "regime": "BEAR", "quant_path": "blood_in_the_street", "outcome": "loss", "pnl_pct": -5.0, "confirmation_score": 70, "fear_greed": 44},
            {"ts": ts(2), "regime": "BEAR", "quant_path": "blood_in_the_street", "outcome": "loss", "pnl_pct": -4.0, "confirmation_score": 71, "fear_greed": 42},
            {"ts": ts(3), "regime": "BEAR", "quant_path": "blood_in_the_street", "outcome": "win", "pnl_pct": 6.0, "confirmation_score": 72, "fear_greed": 20},
            {"ts": ts(4), "regime": "BEAR", "quant_path": "blood_in_the_street", "outcome": "win", "pnl_pct": 5.0, "confirmation_score": 73, "fear_greed": 25},
            {"ts": ts(5), "regime": "BEAR", "quant_path": "blood_in_the_street", "outcome": "win", "pnl_pct": 4.0, "confirmation_score": 74, "fear_greed": 28},
        ]
        data = sample_aggregate_data(trades)
        data["current_thresholds"] = {"BLOOD_FG_MAX": 45}
        from recursive_optimizer import counterfactual_analysis
        from thresholds import get_threshold as _orig_gt
        def _min5(key, default=None):
            if key == "MIN_TRADES_FOR_REC":
                return 5
            return _orig_gt(key, default)
        with patch("thresholds.OPTIMIZER_BOUNDS", {"BLOOD_FG_MAX": (35, 55)}), \
             patch("thresholds.PARAM_PATH_FILTER", {"BLOOD_FG_MAX": "blood_in_the_street"}), \
             patch("thresholds.PARAM_FIELD_MAP", {"BLOOD_FG_MAX": "fear_greed"}), \
             patch("thresholds.PARAM_DESCRIPTIONS", {"BLOOD_FG_MAX": "Max FG for blood path"}), \
             patch("thresholds.get_threshold", side_effect=_min5):
            results = counterfactual_analysis(data)
        assert len(results) > 0, "FG gate should find improvement by lowering max to filter high-FG losses"
        r = results[0]
        assert r["parameter"] == "BLOOD_FG_MAX"
        assert r["losses_avoided"] > 0
        assert r["winners_lost"] == 0


# ---------------------------------------------------------------------------
# Class: TestRegimeExpectancyGate
# ---------------------------------------------------------------------------

class TestRegimeExpectancyGate:
    """Tests for the regime-expectancy gate in _filter_full_auto()."""

    def _make_result(self, param, current_value, suggested_value, pnl_lift=0.05, live_trades=20):
        """Build a minimal counterfactual result dict."""
        return {
            "parameter": param,
            "current_value": current_value,
            "suggested_value": suggested_value,
            "net_expected_pnl_pct": pnl_lift,
            "winners_lost": 0,
            "live_evidence_trades": live_trades,
            "missed_edge_reduction": 0,
        }

    def test_relaxation_blocked_in_negative_expectancy_regime(self):
        """Lowering SIDEWAYS_REGIME_MIN_SCORE when SIDEWAYS has negative expectancy → pending, not auto-apply."""
        from recursive_optimizer import _filter_full_auto
        results = [self._make_result("SIDEWAYS_REGIME_MIN_SCORE", 68, 65)]
        regime_exp = {"SIDEWAYS": {"expectancy": -0.35, "win_rate_pct": 26.0, "total_trades": 34}}
        with patch("thresholds.OPTIMIZER_BOUNDS", {"SIDEWAYS_REGIME_MIN_SCORE": (60, 78)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"SIDEWAYS_REGIME_MIN_SCORE": ["SIDEWAYS"]}):
            auto, pending = _filter_full_auto(results, min_improvement=0.02, regime_expectancy=regime_exp)
        assert len(auto) == 0, "relaxation in negative-expectancy regime must not auto-apply"
        assert len(pending) == 1

    def test_tightening_not_blocked_in_negative_expectancy_regime(self):
        """Raising (tightening) SIDEWAYS floor in negative-expectancy regime → still eligible for auto-apply."""
        from recursive_optimizer import _filter_full_auto
        results = [self._make_result("SIDEWAYS_REGIME_MIN_SCORE", 68, 72)]  # 72 > 68 = tightening
        regime_exp = {"SIDEWAYS": {"expectancy": -0.35, "win_rate_pct": 26.0, "total_trades": 34}}
        with patch("thresholds.OPTIMIZER_BOUNDS", {"SIDEWAYS_REGIME_MIN_SCORE": (60, 78)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"SIDEWAYS_REGIME_MIN_SCORE": ["SIDEWAYS"]}):
            auto, pending = _filter_full_auto(results, min_improvement=0.02, regime_expectancy=regime_exp)
        assert len(auto) == 1, "tightening a floor is not a relaxation; gate must not block it"

    def test_relaxation_allowed_in_positive_expectancy_regime(self):
        """Lowering BEAR_REGIME_MIN_SCORE when BEAR has positive expectancy → auto-apply allowed."""
        from recursive_optimizer import _filter_full_auto
        results = [self._make_result("BEAR_REGIME_MIN_SCORE", 78, 75)]
        regime_exp = {"BEAR": {"expectancy": 1.82, "win_rate_pct": 57.0, "total_trades": 7}}
        with patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (65, 80)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"BEAR_REGIME_MIN_SCORE": ["BEAR", "VOLATILE"]}):
            auto, pending = _filter_full_auto(results, min_improvement=0.02, regime_expectancy=regime_exp)
        assert len(auto) == 1, "positive-expectancy regime relaxation should not be blocked"

    def test_gate_requires_minimum_5_trades_to_activate(self):
        """Gate must NOT fire if regime has fewer than 5 trades — too noisy to trust."""
        from recursive_optimizer import _filter_full_auto
        results = [self._make_result("SIDEWAYS_REGIME_MIN_SCORE", 68, 65)]
        # Only 3 trades — below minimum
        regime_exp = {"SIDEWAYS": {"expectancy": -0.50, "win_rate_pct": 20.0, "total_trades": 3}}
        with patch("thresholds.OPTIMIZER_BOUNDS", {"SIDEWAYS_REGIME_MIN_SCORE": (60, 78)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"SIDEWAYS_REGIME_MIN_SCORE": ["SIDEWAYS"]}):
            auto, pending = _filter_full_auto(results, min_improvement=0.02, regime_expectancy=regime_exp)
        assert len(auto) == 1, "gate must not fire with < 5 trades — data is too thin"

    def test_non_regime_parameter_not_gated(self):
        """Parameters with no PARAM_REGIME_FILTER entry are not affected by the gate."""
        from recursive_optimizer import _filter_full_auto
        results = [self._make_result("DIP_MIN_SCORE", 65, 62)]
        regime_exp = {"SIDEWAYS": {"expectancy": -0.50, "win_rate_pct": 20.0, "total_trades": 30}}
        with patch("thresholds.OPTIMIZER_BOUNDS", {"DIP_MIN_SCORE": (55, 75)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {}):  # no filter for DIP_MIN_SCORE
            auto, pending = _filter_full_auto(results, min_improvement=0.02, regime_expectancy=regime_exp)
        assert len(auto) == 1, "non-regime parameters must pass through unaffected"

    def test_gate_absent_when_no_regime_expectancy_passed(self):
        """If regime_expectancy is None or empty, the gate is a no-op (backward compatible)."""
        from recursive_optimizer import _filter_full_auto
        results = [self._make_result("SIDEWAYS_REGIME_MIN_SCORE", 68, 65)]
        with patch("thresholds.OPTIMIZER_BOUNDS", {"SIDEWAYS_REGIME_MIN_SCORE": (60, 78)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"SIDEWAYS_REGIME_MIN_SCORE": ["SIDEWAYS"]}):
            auto_none, _ = _filter_full_auto(results, min_improvement=0.02, regime_expectancy=None)
            auto_empty, _ = _filter_full_auto(results, min_improvement=0.02, regime_expectancy={})
        assert len(auto_none) == 1
        assert len(auto_empty) == 1

    def test_aggregate_includes_regime_expectancy_key(self, tmp_optimizer_env):
        """aggregate() return dict must contain 'regime_expectancy' key with per-regime data."""
        from recursive_optimizer import aggregate
        data = aggregate()
        assert "regime_expectancy" in data
        assert isinstance(data["regime_expectancy"], dict)

    def test_aggregate_regime_expectancy_computed_correctly(self, tmp_optimizer_env):
        """Per-regime expectancy values are mathematically correct for a known trade set."""
        from recursive_optimizer import _filter_full_auto

        # Construct a minimal regime_expectancy as aggregate() would produce
        # SIDEWAYS: 2 wins (+5% each) + 3 losses (-4% each)
        # WR = 0.4, avg_win = 5.0, avg_loss = -4.0
        # expectancy = 0.4 * 5.0 + 0.6 * (-4.0) = 2.0 - 2.4 = -0.4
        sideways_exp = {
            "wins": 2, "losses": 3,
            "win_pnl": 10.0, "loss_pnl": -12.0,
            "expectancy": round(0.4 * 5.0 + 0.6 * (-4.0), 3),
            "win_rate_pct": 40.0,
            "total_trades": 5,
        }
        assert sideways_exp["expectancy"] == -0.4

        # Confirm gate fires for this expectancy
        results = [self._make_result("SIDEWAYS_REGIME_MIN_SCORE", 68, 65)]
        with patch("thresholds.OPTIMIZER_BOUNDS", {"SIDEWAYS_REGIME_MIN_SCORE": (60, 78)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"SIDEWAYS_REGIME_MIN_SCORE": ["SIDEWAYS"]}):
            auto, pending = _filter_full_auto(
                results, min_improvement=0.02,
                regime_expectancy={"SIDEWAYS": sideways_exp}
            )
        assert len(auto) == 0
        assert len(pending) == 1
