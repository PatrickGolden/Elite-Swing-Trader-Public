# tests/unit/test_paper_evidence.py — Paper evidence module and optimizer weighting.

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _trade(outcome="win", pnl=5.0, regime="SIDEWAYS", tier="MODERATE_BUY", path="default", days_ago=1):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "outcome": outcome,
        "pnl_pct": pnl,
        "pnl_usd": pnl * 10,
        "market_regime": regime,
        "quant_tier": tier,
        "quant_path": path,
        "exit_time": ts,
        "confirmation_score": 65,
    }


class TestWilsonAndEffectiveN:
    def test_wilson_lb_all_wins(self):
        from paper_evidence import wilson_lower_bound
        lb = wilson_lower_bound(10, 10)
        assert lb > 60

    def test_kish_effective_n(self):
        from paper_evidence import kish_effective_n
        assert kish_effective_n([1.0, 1.0, 1.0]) == 3.0
        assert kish_effective_n([1.0, 0.75, 0.75]) < 3.0


class TestSliceStats:
    def test_promotion_ineligible_small_n(self):
        from paper_evidence import compute_slice_stats
        trades = [_trade() for _ in range(5)]
        st = compute_slice_stats(trades, "MODERATE_BUY", "SIDEWAYS", "DEFAULT")
        assert st.n_closed == 5
        assert st.promotion_eligible is False

    def test_promotion_eligible_synthetic(self):
        from paper_evidence import compute_slice_stats
        trades = [_trade("win", 8.0, days_ago=i + 10) for i in range(25)]
        st = compute_slice_stats(trades, "MODERATE_BUY", "SIDEWAYS", "MOMENTUM")
        assert st.n_closed == 25
        assert st.win_rate_pct == 100.0
        assert st.expectancy_pct > 0


class TestCombineForRelaxation:
    def test_tags_weights(self):
        from paper_evidence import combine_for_relaxation
        live = [_trade(tier="STRONG_BUY")]
        paper = [_trade()]
        combined = combine_for_relaxation(live, paper, paper_weight=0.75)
        assert len(combined) == 2
        sources = {t["_evidence_source"] for t in combined}
        assert sources == {"live", "paper"}
        paper_row = [t for t in combined if t["_evidence_source"] == "paper"][0]
        assert paper_row["_evidence_weight"] == 0.75


class TestIsParamRelaxation:
    def test_score_floor_relax(self):
        from paper_evidence import is_param_relaxation
        assert is_param_relaxation("score_floor", 72, 68) is True
        assert is_param_relaxation("score_floor", 68, 72) is False


class TestEvaluateWeighted:
    def test_weighted_winners_gained(self):
        from recursive_optimizer import _evaluate_candidate_on_trades
        trades = [
            {**_trade("win", 10), "confirmation_score": 67, "_evidence_weight": 0.75},
        ]
        r = _evaluate_candidate_on_trades(
            65, 70, trades, "confirmation_score", "score_floor",
            10.0, 5.0, 0.08, 0.075, 0.25,
        )
        assert r["winners_gained"] >= 0.75


class TestWeightedAvgPnl:
    def test_weighted_avg_respects_evidence_weight(self):
        from recursive_optimizer import _weighted_avg_pnl
        trades = [
            {**_trade("win", 10), "_evidence_weight": 1.0},
            {**_trade("win", 4), "_evidence_weight": 0.75},
        ]
        w = _weighted_avg_pnl(trades, "win")
        assert 4 < w < 10


class TestShadowBackfill:
    def test_backfill_forward_return(self, tmp_path):
        from paper_evidence import backfill_shadow_skips
        shadow = tmp_path / "shadow_skips.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        shadow.write_text(
            json.dumps({
                "ts": old_ts,
                "coin_id": "bitcoin",
                "entry_price": 100.0,
                "live_skip_reason": "policy_no_auto_exec",
            }) + "\n",
            encoding="utf-8",
        )
        stats = backfill_shadow_skips(
            lambda ids: {"bitcoin": 110.0},
            shadow_path=shadow,
        )
        assert stats["shadow_skips_updated"] == 1
        row = json.loads(shadow.read_text(encoding="utf-8").strip())
        assert row["forward_return_7d"] == 10.0


class TestCounterfactualPaperOnly:
    def test_empty_live_subset_still_runs_with_paper(self):
        from unittest.mock import patch
        from recursive_optimizer import counterfactual_analysis

        paper = [
            {
                **_trade("win", 8.0, regime="BEAR", days_ago=i + 5),
                "confirmation_score": 68,
                "quant_path": "default",
            }
            for i in range(12)
        ] + [
            {
                **_trade("loss", -4.0, regime="BEAR", days_ago=i + 20),
                "confirmation_score": 66,
                "quant_path": "default",
            }
            for i in range(8)
        ]
        data = {
            "trades": [],
            "paper_trades_closed": paper,
            "backtest_trades": [],
            "current_thresholds": {"BEAR_REGIME_MIN_SCORE": 70},
            "top_missed_by_regret": [],
        }
        calls = []

        def fake_find(param, cur_val, lo, hi, trades, avg_win, avg_loss, **kwargs):
            calls.append((len(trades), len(kwargs.get("paper_trades") or [])))
            return {
                "value": 67,
                "losses_avoided": 0,
                "winners_lost": 0,
                "winners_gained": 4,
                "net_impact": 12.0,
            }

        with patch("recursive_optimizer._find_best_candidate", side_effect=fake_find), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (60, 80)}), \
             patch("thresholds.PARAM_PATH_FILTER", {"BEAR_REGIME_MIN_SCORE": "*"}), \
             patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score"}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"BEAR_REGIME_MIN_SCORE": ["BEAR", "VOLATILE"]}), \
             patch("thresholds.PARAM_DESCRIPTIONS", {"BEAR_REGIME_MIN_SCORE": "Bear min score"}), \
             patch("thresholds.get_threshold", side_effect=lambda k, d=None: 5 if k in ("MIN_TRADES_FOR_REC", "MIN_TRADES_FOR_AUTO") else d):
            out = counterfactual_analysis(data)

        assert calls, "counterfactual should invoke _find_best_candidate when only paper has outcomes"
        assert out
        assert out[0]["paper_evidence_trades"] == 20
        assert out[0]["live_evidence_trades"] == 0

    def test_relaxation_with_paper_never_auto_apply_tier_a(self):
        from unittest.mock import patch
        from recursive_optimizer import counterfactual_analysis

        now = datetime.now(timezone.utc)
        ts = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
        live = [
            {
                "exit_time": ts(i + 1),
                "market_regime": "BEAR",
                "regime": "BEAR",
                "quant_path": "default",
                "outcome": "win",
                "pnl_pct": 6.0,
                "confirmation_score": 75,
            }
            for i in range(18)
        ]
        paper = [
            {
                **_trade("win", 7.0, regime="BEAR", days_ago=i + 3),
                "confirmation_score": 68,
                "quant_path": "default",
            }
            for i in range(10)
        ]
        data = {
            "trades": live,
            "paper_trades_closed": paper,
            "backtest_trades": [],
            "current_thresholds": {"BEAR_REGIME_MIN_SCORE": 70},
            "top_missed_by_regret": [],
        }

        def fake_find(param, cur_val, lo, hi, trades, avg_win, avg_loss, **kwargs):
            return {
                "value": 67,
                "losses_avoided": 0,
                "winners_lost": 0,
                "winners_gained": 5,
                "net_impact": 15.0,
            }

        with patch("recursive_optimizer._find_best_candidate", side_effect=fake_find), \
             patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (60, 80)}), \
             patch("thresholds.PARAM_PATH_FILTER", {"BEAR_REGIME_MIN_SCORE": "*"}), \
             patch("thresholds.PARAM_FIELD_MAP", {"BEAR_REGIME_MIN_SCORE": "confirmation_score"}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"BEAR_REGIME_MIN_SCORE": ["BEAR", "VOLATILE"]}), \
             patch("thresholds.PARAM_DESCRIPTIONS", {"BEAR_REGIME_MIN_SCORE": "Bear min score"}), \
             patch("thresholds.get_threshold", side_effect=lambda k, d=None: 5 if k in ("MIN_TRADES_FOR_REC", "MIN_TRADES_FOR_AUTO") else d):
            out = counterfactual_analysis(data)

        assert out
        r = out[0]
        assert r["paper_evidence_trades"] > 0
        assert r["live_evidence_trades"] >= 15
        assert r["safe_to_auto_apply"] is False
        assert r["evidence_tier"] in ("B", "C")


class TestKishEffectiveN:
    def test_slice_stats_effective_n_equals_n_for_equal_weights(self):
        from paper_evidence import compute_slice_stats
        trades = [_trade("win", 8.0, days_ago=i + 5) for i in range(10)]
        st = compute_slice_stats(trades, "MODERATE_BUY", "SIDEWAYS", "DEFAULT")
        assert st.effective_n == 10.0
        assert st.n_closed == 10


class TestFilterFullAutoPaperRegime:
    def test_live_negative_paper_positive_annotates_pending(self):
        from unittest.mock import patch
        from recursive_optimizer import _filter_full_auto
        results = [{
            "parameter": "BEAR_REGIME_MIN_SCORE",
            "current_value": 70,
            "suggested_value": 65,
            "winners_lost": 0,
            "net_expected_pnl_pct": 2.0,
            "live_evidence_trades": 20,
            "missed_edge_reduction": 0,
        }]
        live_reg = {"SIDEWAYS": {"expectancy": -1.5, "total_trades": 10, "win_rate_pct": 40}}
        paper_reg = {"SIDEWAYS": {"expectancy": 2.0, "total_trades": 25, "win_rate_pct": 55}}
        def _gt(k, d=None):
            if k == "MISSED_EDGE_WEIGHT":
                return 0.25
            if k == "MIN_TRADES_FOR_AUTO":
                return 15
            return d

        with patch("thresholds.OPTIMIZER_BOUNDS", {"BEAR_REGIME_MIN_SCORE": (60, 80)}), \
             patch("thresholds.PARAM_REGIME_FILTER", {"BEAR_REGIME_MIN_SCORE": ["SIDEWAYS"]}), \
             patch("thresholds.get_threshold", side_effect=_gt):
            auto, pending = _filter_full_auto(
                results, min_improvement=0.01, regime_expectancy=live_reg,
                paper_regime_expectancy=paper_reg,
            )
        assert not auto
        gated = [p for p in pending if p.get("paper_regime_positive")]
        assert gated
        assert gated[0].get("gate_note")


class TestRoadmapPaperPending:
    def test_sync_creates_pending_without_enabling_flag(self, tmp_path):
        from paper_evidence import sync_paper_promotion_pending
        state_path = tmp_path / "roadmap_state.json"
        state_path.write_text(
            json.dumps({
                "feature_states": {},
                "pass_streak_weeks": {},
                "pending_approvals": [],
                "state_flags": {"moderate_buy_accumulation_enabled": False},
            }),
            encoding="utf-8",
        )
        promos = [{
            "type": "execution_policy",
            "policy": "moderate_buy_accumulation",
            "slice": "MODERATE_BUY|SIDEWAYS|DEFAULT",
            "description": "test",
            "stats": {"n_closed": 22, "win_rate_pct": 50, "expectancy_pct": 1.2, "wilson_wr_lb_pct": 42},
        }]
        added = sync_paper_promotion_pending(promos, state_path=state_path)
        assert added
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved["state_flags"].get("moderate_buy_accumulation_enabled") is False
        pending = saved.get("pending_approvals") or []
        assert any(p.get("feature_id") == "moderate_buy_accumulation" for p in pending)


class TestAppendShadowRotation:
    def test_append_shadow_skip_writes_jsonl(self, tmp_path):
        from paper_evidence import append_shadow_skip, load_shadow_skips
        shadow = tmp_path / "shadow.jsonl"
        append_shadow_skip({"coin_id": "bitcoin", "live_skip_reason": "policy_no_auto_exec"}, path=shadow)
        rows = load_shadow_skips(shadow)
        assert len(rows) == 1
        assert rows[0]["coin_id"] == "bitcoin"


class TestClassifierUnjournaled:
    def test_classify_returns_unjournaled_exchange(self):
        from unittest.mock import patch, MagicMock
        import app as app_module
        coin = {
            "id": "near",
            "symbol": "NEAR",
            "current_price": 5.0,
            "ai_recommendation": {
                "final_signal": "STRONG_BUY",
                "quant_tier": "STRONG_BUY",
                "position_sizing": {"position_size_usd": 100, "max_loss_pct": 2},
            },
        }
        app_module._reconcile_cache["data"] = {
            "discrepancies": [{"type": "external", "symbol": "NEAR", "exchange_quantity": 10}],
        }
        with patch.object(app_module, "get_open_positions", return_value=[]), \
             patch.object(app_module, "get_execution_config", return_value={"auto_execution_enabled": True}), \
             patch.object(app_module, "get_test_mode", return_value=False), \
             patch.object(app_module, "_build_account_overview", return_value={"cash_available_to_trade": 10000}), \
             patch.object(app_module, "execution_safety_ok", return_value=(True, None)):
            reason = app_module._classify_live_auto_exec_skip(coin, {}, 10000)
        assert reason == "unjournaled_exchange"
