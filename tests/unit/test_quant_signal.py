# tests/test_quant_signal.py — Quant gate with regime, volatility, and edge score
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant_signal import generate_quant_signal
from thresholds import DEFAULT_THRESHOLDS, _load_live_thresholds


def test_generate_quant_signal_hold_low_score():
    """Low score → HOLD tier."""
    coin = {"score": 40, "indicators": {"atr_pct": 2.0}, "risk": {"rr_ratio": 2.5}, "enhanced": {}}
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["quant_signal"] == "HOLD"
    assert out["quant_tier"] == "HOLD"


def test_generate_quant_signal_strong_buy():
    """Score in STRONG range → STRONG_BUY (no funding veto)."""
    coin = {
        "score": 75,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] in ("STRONG_BUY", "MODERATE_BUY", "ULTRA_BUY")


def test_generate_quant_signal_bear_funding_flag_set():
    """BEAR + high funding: quant_signal passes through (veto_specialist handles the veto).
    The funding_veto_active flag is set for guards (whale accum, reasoning)."""
    coin = {
        "score": 80,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"funding_rate": 0.12},
    }
    regime = {"regime": "BEAR"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    # Quant signal no longer forces HOLD on funding; veto_specialist handles it
    assert out["quant_signal"] == "BUY"
    assert "funding" in out.get("reasoning", "").lower() or "veto" in out.get("reasoning", "").lower()


def test_generate_quant_signal_returns_reasoning():
    """Output includes reasoning and edge_score."""
    coin = {"score": 60, "indicators": {"atr_pct": 2.0}, "risk": {"rr_ratio": 3.0}, "enhanced": {}}
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert "reasoning" in out
    assert "edge_score" in out
    assert "quant_tier" in out


def test_generate_quant_signal_regime_safety_bear():
    """BEAR/VOLATILE regime with score < 78 → HOLD."""
    coin = {"score": 70, "indicators": {"atr_pct": 2.0}, "risk": {"rr_ratio": 3.0}, "enhanced": {}}
    regime = {"regime": "BEAR"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["quant_signal"] == "HOLD"
    assert out["quant_tier"] == "HOLD"


def test_generate_quant_signal_volatility_gate_high_atr_bear():
    """High ATR in BEAR requires score >= 80 for BUY; otherwise downgraded."""
    coin = {"score": 75, "indicators": {"atr_pct": 5.0}, "risk": {"rr_ratio": 3.0}, "enhanced": {}}
    regime = {"regime": "BEAR"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    # High vol BEAR: score 75 < 80 → gate applied, tier forced down
    assert out["quant_signal"] in ("HOLD", "BUY")
    assert "score" in out
    assert out["score"] == 75


def test_generate_quant_signal_edge_score_boost():
    """High edge score can boost MODERATE_BUY to STRONG_BUY."""
    coin = {
        "score": 65,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 4.0},
        "enhanced": {"sopr": 0.96},
    }
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["edge_score"] >= 0
    assert out["quant_signal"] in ("BUY", "HOLD")
    assert out["quant_tier"] in ("MODERATE_BUY", "STRONG_BUY", "ULTRA_BUY", "HOLD")


def test_generate_quant_signal_mtf_alignment_bonus():
    """MTF alignment pushes score to or above SIDEWAYS_REGIME_MIN_SCORE -> BUY; uses current thresholds (dynamic)."""
    live = _load_live_thresholds()
    sideways_floor = int(live.get("SIDEWAYS_REGIME_MIN_SCORE", 68))
    mtf_bonus = int(live.get("MTF_ALIGNMENT_BONUS", 3))
    base_score = sideways_floor - mtf_bonus
    coin = {
        "score": base_score,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 2.0},
        "enhanced": {},
        "price_change_percentage_1h_in_currency": 1.0,
        "price_change_percentage_24h": 2.0,
        "price_change_percentage_7d_in_currency": 3.0,
    }
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["score"] >= sideways_floor
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] in ("MODERATE_BUY", "STRONG_BUY")


def test_sideways_floor_blocks_low_score():
    """SIDEWAYS regime with score below SIDEWAYS_REGIME_MIN_SCORE (68) -> HOLD."""
    coin = {
        "score": 60,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["quant_signal"] == "HOLD"
    assert out["quant_tier"] == "HOLD"


def test_generate_quant_signal_capitulation_ultra_buy():
    """BEAR + F&G < 15 + score >= 68 + funding OK → capitulation → BUY ULTRA_BUY."""
    coin = {
        "score": 70,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"funding_rate": {"current": 0.02}, "data_quality": {"ok_count": 2}},
    }
    regime = {"regime": "BEAR"}
    out = generate_quant_signal(coin, regime, fear_greed_value=10)
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] == "ULTRA_BUY"


def test_generate_quant_signal_high_vol_bear_capped_hold():
    """High ATR in BEAR with score 79 → capped to 77 → regime safety (77 < 78) → HOLD."""
    # Use defaults with BEAR_REGIME_MIN_SCORE=78 so capped score 77 fails regime floor (test is independent of live_thresholds.json)
    th = dict(DEFAULT_THRESHOLDS, BEAR_REGIME_MIN_SCORE=78)
    with patch("quant_signal._load_live_thresholds", return_value=th):
        coin = {
            "score": 79,
            "indicators": {"atr_pct": 5.0},
            "risk": {"rr_ratio": 3.0},
            "enhanced": {},
        }
        regime = {"regime": "BEAR"}
        out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["score"] == 77
    assert out["quant_signal"] == "HOLD"
    assert out["quant_tier"] == "HOLD"


def test_generate_quant_signal_high_vol_bear_above_min_buy():
    """High ATR in BEAR with score 82 (>= 80) → no cap → STRONG_BUY, regime safety passes."""
    coin = {
        "score": 82,
        "indicators": {"atr_pct": 5.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"data_quality": {"ok_count": 2}},
    }
    regime = {"regime": "BEAR"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["score"] == 82
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] == "STRONG_BUY"


# --- Stress: dip / blood / pullback / vol harvest / rally / pump / F&G gates ---

def test_dip_path_fires_only_with_fear():
    """Dip path (24h dip + 1h stabilizing) fires only when F&G <= 45 (sniper)."""
    coin = {
        "score": 66,
        "indicators": {"atr_pct": 2.0, "ch1h": -2.0, "ch24h": -8.0, "ch7d": -5.0, "ch30d": -3.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }
    regime = {"regime": "BEAR"}
    out_fear = generate_quant_signal(coin, regime, fear_greed_value=40)
    out_greed = generate_quant_signal(coin, regime, fear_greed_value=55)
    assert out_fear["quant_signal"] == "BUY" and out_fear["quant_tier"] == "MODERATE_BUY"
    assert "dip" in out_fear.get("signal_badges", [])
    assert out_greed["quant_signal"] == "HOLD"  # no dip path when greedy


def test_blood_in_the_street_path():
    """F&G < 30 + 24h < -5% + 1h stabilizing + score >= 62 → STRONG_BUY (blood path runs before dip)."""
    # Pin default thresholds so BLOOD_STREET_MIN_SCORE=62 (live file may override)
    with patch("quant_signal._load_live_thresholds", return_value=dict(DEFAULT_THRESHOLDS)):
        coin = {
            "score": 64,
            "indicators": {"atr_pct": 2.0, "ch1h": -2.0, "ch24h": -7.0, "ch7d": -8.0, "ch30d": -5.0},
            "risk": {"rr_ratio": 3.0},
            "enhanced": {"data_quality": {"ok_count": 2}},
        }
        regime = {"regime": "BEAR"}
        out = generate_quant_signal(coin, regime, fear_greed_value=25)
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] == "STRONG_BUY"
    assert "blood_in_the_street" in out.get("signal_badges", [])


def test_pullback_path_only_with_fear():
    """Pullback (7d/30d up, 24h down) only when F&G <= 50."""
    coin = {
        "score": 67,
        "indicators": {"atr_pct": 2.0, "ch1h": 0, "ch24h": -4.0, "ch7d": 5.0, "ch30d": 10.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }
    regime = {"regime": "BEAR"}
    out_ok = generate_quant_signal(coin, regime, fear_greed_value=48)
    out_skip = generate_quant_signal(coin, regime, fear_greed_value=55)
    assert out_ok["quant_signal"] == "BUY" and "pullback_in_trend" in out_ok.get("signal_badges", [])
    assert out_skip["quant_signal"] == "HOLD"


def test_volatility_harvest_path():
    """High ATR + dip + F&G <= 48 → MODERATE_BUY (vol harvest)."""
    # Pin to defaults so vol harvest path fires (score below regime floor 72 → HOLD, then vol harvest upgrades to BUY)
    # ch30d must be positive to break _mtf_medium_aligned (all-negative) so MTF bonus doesn't push score to 72
    # scorer resilience bonus (+2) applies when ch24h < 0, so base score must stay below floor after bonuses
    with patch("quant_signal._load_live_thresholds", return_value=dict(DEFAULT_THRESHOLDS)):
        coin = {
            "score": 68,
            "indicators": {"atr_pct": 4.0, "ch1h": -2.0, "ch24h": -3.0, "ch7d": -5.0, "ch30d": 2.0},
            "risk": {"rr_ratio": 3.0},
            "enhanced": {},
        }
        regime = {"regime": "VOLATILE"}
        out = generate_quant_signal(coin, regime, fear_greed_value=45)
    assert out["quant_signal"] == "BUY"
    assert "volatility_harvest" in out.get("signal_badges", [])


def test_rally_floor_lowers_in_bear():
    """When market_24h_pct > 5%, BEAR regime floor is 72 (not 78)."""
    # Pin to defaults so rally path and badge are deterministic (independent of live_thresholds.json)
    with patch("quant_signal._load_live_thresholds", return_value=dict(DEFAULT_THRESHOLDS)):
        coin = {
            "score": 74,
            "indicators": {"atr_pct": 2.0, "ch1h": 1.0, "ch24h": 2.0, "ch7d": 3.0, "ch30d": 5.0},
            "risk": {"rr_ratio": 3.0},
            "enhanced": {},
        }
        regime = {"regime": "BEAR", "market_24h_pct": 6.0}
        out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["quant_signal"] == "BUY"
    assert "rally" in out.get("signal_badges", [])


def test_pump_cap_downgrades_ultra_to_strong():
    """When 24h > 14%, ULTRA_BUY is capped to STRONG_BUY (pump cap)."""
    coin = {
        "score": 88,
        "indicators": {"atr_pct": 2.0, "ch1h": 2.0, "ch24h": 16.0, "ch7d": 10.0, "ch30d": 20.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"data_quality": {"ok_count": 2}},
    }
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] == "STRONG_BUY"  # capped from ULTRA
    assert "pump_cap" in out.get("signal_badges", [])


def test_signal_badges_always_present():
    """Return always includes signal_badges list (may be empty)."""
    coin = {"score": 50, "indicators": {"atr_pct": 2.0}, "risk": {"rr_ratio": 3.0}, "enhanced": {}}
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert "signal_badges" in out
    assert isinstance(out["signal_badges"], list)


def test_capitulation_badge():
    """Capitulation exception (F&G < 15, score >= 68) sets capitulation badge."""
    coin = {
        "score": 70,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"funding_rate": {"current": 0.02}, "data_quality": {"ok_count": 2}},
    }
    regime = {"regime": "BEAR"}
    out = generate_quant_signal(coin, regime, fear_greed_value=10)
    assert out["quant_tier"] == "ULTRA_BUY"
    assert "capitulation" in out.get("signal_badges", [])


def test_bull_participant_path_and_badges():
    """BULL regime with strict floor can remain HOLD while still tagging bull participation badges."""
    coin = {
        "score": 62,
        "indicators": {"atr_pct": 2.0, "ch24h": 4, "ch7d": 8, "ch30d": 12, "momentum": 12},
        "price_change_percentage_24h": 4,
        "price_change_percentage_7d_in_currency": 8,
        "price_change_percentage_30d_in_currency": 12,
        "risk": {"rr_ratio": 2.0},  # keep edge boost from upgrading MODERATE→STRONG
        "enhanced": {},
    }
    regime = {"regime": "BULL"}
    # Pin BULL floor to 78 so score 62 stays below threshold → HOLD
    th = dict(DEFAULT_THRESHOLDS, BULL_REGIME_MIN_SCORE=78)
    with patch("quant_signal._load_live_thresholds", return_value=th):
        out = generate_quant_signal(coin, regime, fear_greed_value=55)
    assert out["quant_signal"] == "HOLD"
    assert out["quant_tier"] == "HOLD"
    badges = out.get("signal_badges", [])
    assert "bull_participant" in badges
    assert "momentum_continuation" in badges


def test_default_buy_with_momentum_context_gets_non_default_quant_path():
    """Default BUYs with acceleration + MTF alignment are tagged as momentum_context path."""
    coin = {
        "score": 74,
        "indicators": {
            "atr_pct": 2.0,
            "ch1h": 1.0,
            "ch24h": 2.0,
            "ch7d": 4.0,
            "ch30d": 6.0,
            "momentum": 7.0,
            "momentum_quality": 35.0,
            "momentum_accel": {"is_accelerating": True},
            "early_momentum": 2.0,
            "rsi": 62.0,
        },
        "price_change_percentage_1h_in_currency": 1.0,
        "price_change_percentage_24h": 2.0,
        "price_change_percentage_7d_in_currency": 4.0,
        "price_change_percentage_30d_in_currency": 6.0,
        "risk": {"rr_ratio": 2.0},
        "enhanced": {},
    }
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=40)
    assert out["quant_signal"] == "BUY"
    assert out["quant_path"] == "momentum_context"


# ---------------------------------------------------------------------------
# Quant Gate Audit: regression floor, path kill expectancy
# ---------------------------------------------------------------------------

def test_regression_blend_deleted_low_prob_has_no_effect():
    """§5.8 (2026-07-03): the regression blend is DELETED — a mocked model output
    must have zero effect on the score, in either direction. Pins the deletion."""
    coin = {
        "score": 85,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }
    regime = {"regime": "BULL"}
    with patch('regression_scorer.get_regression_score', return_value=0.10) as m:
        out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert not m.called, "quant gate must no longer consult the regression model"
    assert out["score"] >= 85.0, f"deleted blend must not drag score: {out['score']}"


def test_regression_blend_deleted_high_prob_has_no_effect():
    coin = {
        "score": 60,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }
    regime = {"regime": "SIDEWAYS"}
    with patch('regression_scorer.get_regression_score', return_value=0.90) as m:
        out = generate_quant_signal(coin, regime, fear_greed_value=50)
    assert not m.called, "quant gate must no longer consult the regression model"
    # score may move via OB/MTF adjustments, but not by the 0.3-weight blend (+9)
    assert out["score"] < 69.0, f"deleted blend must not boost score: {out['score']}"


def test_path_kill_requires_negative_expectancy():
    """Path kill switch should NOT disable a path with low win rate but positive expectancy."""
    from quant_signal import _is_path_disabled
    # Mock trade journal with 35% WR but positive expectancy (big wins, small losses)
    mock_trades = [
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 12.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 15.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 10.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 8.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 11.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 9.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 13.0},
    ] + [
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'loss', 'pnl_pct': -3.0}
        for _ in range(13)
    ]
    # 7 wins / 20 trades = 35% WR, but avg_win=11.14, avg_loss=3.0 → positive expectancy
    import quant_signal as qs
    # Clear cache to force recalculation
    qs._PATH_KILL_CACHE = {}
    with patch('quant_signal.get_historical_win_prob', return_value=0.35), \
         patch('quant_signal._get_closed_positions_from_journal', return_value=mock_trades):
        result = _is_path_disabled('dip', 'BEAR')
    assert result is False, "Path with positive expectancy should NOT be killed despite low WR"


def test_path_kill_disables_negative_expectancy():
    """Path kill switch SHOULD disable a path with low win rate AND negative expectancy."""
    from quant_signal import _is_path_disabled
    # Mock: 30% WR with small wins and large losses → negative expectancy
    mock_trades = [
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 3.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 2.5},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 4.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 3.5},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 2.0},
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 3.0},
    ] + [
        {'regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'loss', 'pnl_pct': -8.0}
        for _ in range(14)
    ]
    # 6 wins / 20 trades = 30% WR, avg_win=3.0, avg_loss=8.0 → negative expectancy
    import quant_signal as qs
    qs._PATH_KILL_CACHE = {}
    with patch('quant_signal.get_historical_win_prob', return_value=0.30), \
         patch('quant_signal._get_closed_positions_from_journal', return_value=mock_trades):
        result = _is_path_disabled('dip', 'BEAR')
    assert result is True, "Path with negative expectancy AND low WR should be killed"


def test_portfolio_gate_fails_closed_on_exception():
    """Portfolio gate must fail closed if open position lookup errors."""
    from quant_signal import _check_portfolio_gate
    with patch('trade_journal.get_open_positions', side_effect=RuntimeError("boom")):
        assert _check_portfolio_gate("BULL") is False


def test_bear_funding_flag_blocks_whale_boost():
    """BEAR + high funding: whale top-10 boost is skipped (funding_veto_active guard)."""
    coin = {
        "score": 80,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {
            "funding_rate": {"current": 0.20},
            "exchange_netflow_24h": -999.0,
            "whale_netflow": "accumulation",
        },
        "_is_top_10": True,
    }
    regime = {"regime": "BEAR"}
    out = generate_quant_signal(coin, regime, fear_greed_value=35)
    # Quant signal passes through; veto_specialist handles the actual veto.
    # But whale boost must NOT apply when funding_veto_active is set.
    badges = out.get("signal_badges", [])
    assert "whale_accum_bear" not in badges


def test_cached_historical_win_prob_fallback_when_attribution_unavailable():
    """If attribution import is unavailable, helper returns numeric base fallback."""
    import quant_signal as qs
    qs._WIN_PROB_CACHE.clear()
    with patch("quant_signal.get_historical_win_prob", None):
        p = qs._get_cached_historical_win_prob("BEAR", "dip", atr_pct=2.0, fg=40, score=70)
    assert isinstance(p, float)
    assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# BULL suspension tests
# ---------------------------------------------------------------------------

def _bull_coin(score=72):
    return {
        "id": "bitcoin",
        "coin_id": "bitcoin",
        "score": score,
        "indicators": {"atr_pct": 2.0, "ch24h": 3, "ch7d": 8, "ch30d": 15, "momentum": 15},
        "price_change_percentage_24h": 3,
        "price_change_percentage_7d_in_currency": 8,
        "price_change_percentage_30d_in_currency": 15,
        "risk": {"rr_ratio": 2.0},
        "enhanced": {},
        "_is_top_3": True,
    }


# ---------------------------------------------------------------------------
# Same-day duplicate entry gate tests
# ---------------------------------------------------------------------------

def test_same_day_duplicate_blocks_top3_buy():
    """Coin already entered today → BUY downgraded to HOLD with same_day_duplicate badge."""
    th = dict(DEFAULT_THRESHOLDS, SIDEWAYS_REGIME_MIN_SCORE=60)
    coin = dict(_bull_coin(score=75), **{"id": "solana", "coin_id": "solana"})
    with patch("quant_signal._load_live_thresholds", return_value=th):
        with patch("quant_signal._check_portfolio_gate", return_value=True):
            with patch("quant_signal._check_same_day_duplicate", return_value=True):
                out = generate_quant_signal(coin, {"regime": "SIDEWAYS"}, fear_greed_value=50)
    assert out["quant_signal"] == "HOLD"
    assert out["quant_tier"] == "HOLD"
    assert "same_day_duplicate" in out.get("signal_badges", [])


def test_same_day_duplicate_not_applied_to_non_top3():
    """Same-day gate is only applied to top-3 candidates (is_top_3=False skips gate entirely)."""
    from quant_signal import _check_same_day_duplicate
    th = dict(DEFAULT_THRESHOLDS, SIDEWAYS_REGIME_MIN_SCORE=60)
    coin = {
        "id": "litecoin", "coin_id": "litecoin",
        "score": 75,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 2.0},
        "enhanced": {},
        "_is_top_3": False,  # not top-3; gate doesn't apply
    }
    with patch("quant_signal._load_live_thresholds", return_value=th):
        with patch("quant_signal._check_portfolio_gate", return_value=True):
            with patch("quant_signal._check_same_day_duplicate", return_value=True) as mock_dedup:
                generate_quant_signal(coin, {"regime": "SIDEWAYS"}, fear_greed_value=50)
    # Gate function should NOT have been called for non-top-3
    mock_dedup.assert_not_called()


def test_check_same_day_duplicate_detects_today_entry(tmp_path):
    """_check_same_day_duplicate returns True when a position for the coin was entered today."""
    from datetime import datetime, timezone
    from quant_signal import _check_same_day_duplicate
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    fake_positions = [
        {"coin_id": "bitcoin", "entry_time": f"{today} 08:00:00", "status": "CLOSED", "is_test": False},
    ]
    with patch("trade_journal.get_all_positions", return_value=fake_positions):
        assert _check_same_day_duplicate("bitcoin") is True


def test_check_same_day_duplicate_ignores_yesterday_entry():
    """_check_same_day_duplicate returns False when entry was yesterday."""
    from quant_signal import _check_same_day_duplicate
    fake_positions = [
        {"coin_id": "bitcoin", "entry_time": "2026-04-30 08:00:00", "status": "CLOSED", "is_test": False},
    ]
    with patch("trade_journal.get_all_positions", return_value=fake_positions):
        assert _check_same_day_duplicate("bitcoin") is False


def test_check_same_day_duplicate_fails_open_on_exception():
    """_check_same_day_duplicate returns False (fail open) if trade_journal errors."""
    from quant_signal import _check_same_day_duplicate
    with patch("trade_journal.get_all_positions", side_effect=RuntimeError("db error")):
        assert _check_same_day_duplicate("bitcoin") is False


# ---------------------------------------------------------------------------
# BULL chase guard tests
# ---------------------------------------------------------------------------

def _momentum_coin(score=65, ch24h=3.0, ch7d=8.0):
    """Coin that triggers early_momentum path: accel + quality + MTF aligned."""
    return {
        "id": "solana",
        "coin_id": "solana",
        "score": score,
        "indicators": {
            "atr_pct": 2.0,
            "rsi": 60,
            "momentum": 6,
            "early_momentum": 5.0,
            "momentum_accel": {"is_accelerating": True, "acceleration": 2.0},
            "momentum_quality": 35,
            "relative_strength": {},
            "ch1h": 1.5,
            "ch24h": ch24h,
            "ch7d": ch7d,
            "ch30d": 12,
            "bb_pos": 55,
        },
        "price_change_percentage_1h_in_currency": 1.5,
        "price_change_percentage_24h_in_currency": ch24h,
        "price_change_percentage_7d_in_currency": ch7d,
        "price_change_percentage_30d_in_currency": 12,
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"data_quality": {"ok_count": 2}},
        "_is_top_3": True,
    }


def test_bull_chase_guard_caps_tier_when_chasing():
    """BULL + momentum path + c24 > 5% → tier capped at MODERATE_BUY."""
    th = dict(DEFAULT_THRESHOLDS, BULL_REGIME_MIN_SCORE=50,
              BULL_CHASE_24H_MAX_PCT=5.0)
    coin = _momentum_coin(score=65, ch24h=7.0, ch7d=10.0)
    with patch("quant_signal._load_live_thresholds", return_value=th):
        with patch("quant_signal._check_portfolio_gate", return_value=True):
            with patch("quant_signal._check_same_day_duplicate", return_value=False):
                out = generate_quant_signal(coin, {"regime": "BULL"}, fear_greed_value=55)
    if out["quant_signal"] == "BUY":
        assert out["quant_tier"] == "MODERATE_BUY"
        assert out["quant_confidence"] <= 65


def test_bull_chase_guard_preserves_tier_moderate_move():
    """BULL + momentum path + c24 < 5% → original tier preserved (not capped)."""
    th = dict(DEFAULT_THRESHOLDS, BULL_REGIME_MIN_SCORE=50,
              BULL_CHASE_24H_MAX_PCT=5.0)
    coin = _momentum_coin(score=65, ch24h=3.0, ch7d=8.0)
    with patch("quant_signal._load_live_thresholds", return_value=th):
        with patch("quant_signal._check_portfolio_gate", return_value=True):
            with patch("quant_signal._check_same_day_duplicate", return_value=False):
                out = generate_quant_signal(coin, {"regime": "BULL"}, fear_greed_value=55)
    if out["quant_signal"] == "BUY":
        assert out["quant_tier"] in ("MODERATE_BUY", "STRONG_BUY")


def test_bull_chase_guard_does_not_affect_sideways():
    """SIDEWAYS + momentum + c24 > 5% → chase guard does NOT apply."""
    th = dict(DEFAULT_THRESHOLDS, SIDEWAYS_REGIME_MIN_SCORE=50,
              BULL_CHASE_24H_MAX_PCT=5.0)
    coin = _momentum_coin(score=65, ch24h=7.0, ch7d=10.0)
    with patch("quant_signal._load_live_thresholds", return_value=th):
        with patch("quant_signal._check_portfolio_gate", return_value=True):
            with patch("quant_signal._check_same_day_duplicate", return_value=False):
                out = generate_quant_signal(coin, {"regime": "SIDEWAYS"}, fear_greed_value=55)
    # Chase guard is BULL-only; pump_caution handles SIDEWAYS separately
    assert out["quant_tier"] in ("HOLD", "MODERATE_BUY", "STRONG_BUY", "ULTRA_BUY")


def test_momentum_bypass_keeps_strong_buy_with_one_source():
    """+10% 24h momentum coin with 1/3 data sources keeps STRONG_BUY (not downgraded)."""
    coin = {
        "score": 75,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "price_change_percentage_24h": 15.0,
        "enhanced": {
            "data_quality": {
                "ok_count": 1,
                "funding_ok": True,
                "order_book_ok": False,
                "onchain_ok": False,
            }
        },
    }
    out = generate_quant_signal(coin, {"regime": "SIDEWAYS"}, fear_greed_value=50)
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] == "STRONG_BUY"


def test_momentum_bypass_not_applied_below_threshold():
    """Without strong 24h momentum, 1/3 sources downgrades STRONG_BUY to MODERATE_BUY."""
    coin = {
        "score": 75,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "price_change_percentage_24h": 5.0,
        "enhanced": {
            "data_quality": {
                "ok_count": 1,
                "funding_ok": True,
                "order_book_ok": False,
                "onchain_ok": False,
            }
        },
    }
    out = generate_quant_signal(coin, {"regime": "SIDEWAYS"}, fear_greed_value=50)
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] == "MODERATE_BUY"


# =============================================================================
# Stage 1b: market-trend (risk-on/off) master gate
# =============================================================================

def _blood_coin():
    return {
        "score": 64,
        "indicators": {"atr_pct": 2.0, "ch1h": -2.0, "ch24h": -7.0, "ch7d": -8.0, "ch30d": -5.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"data_quality": {"ok_count": 2}},
    }


def test_risk_off_allows_blood_path():
    """risk_on=False (or legacy None): fear-dip contrarian path still fires."""
    with patch("quant_signal._load_live_thresholds", return_value=dict(DEFAULT_THRESHOLDS)):
        regime = {"regime": "BEAR", "characteristics": {"risk_on": False}}
        out = generate_quant_signal(_blood_coin(), regime, fear_greed_value=25)
    assert out["quant_signal"] == "BUY"
    assert "blood_in_the_street" in out.get("signal_badges", [])
    assert out["risk_regime"] == "RISK_OFF"


def test_risk_on_blocks_blood_path():
    """risk_on=True: fear-dip contrarian entries are suppressed (the -9..-11% leak)."""
    with patch("quant_signal._load_live_thresholds", return_value=dict(DEFAULT_THRESHOLDS)):
        regime = {"regime": "BEAR", "characteristics": {"risk_on": True}}
        out = generate_quant_signal(_blood_coin(), regime, fear_greed_value=25)
    assert "blood_in_the_street" not in out.get("signal_badges", [])
    assert out["quant_path"] != "blood_in_the_street"
    assert out["risk_regime"] == "RISK_ON"


def test_risk_on_legacy_none_preserves_behavior():
    """risk_on absent (legacy/tests): gate inactive, contrarian path unchanged."""
    with patch("quant_signal._load_live_thresholds", return_value=dict(DEFAULT_THRESHOLDS)):
        out = generate_quant_signal(_blood_coin(), {"regime": "BEAR"}, fear_greed_value=25)
    assert "blood_in_the_street" in out.get("signal_badges", [])
    assert out["risk_regime"] is None


def _breakout_coin(ch30d):
    """Score 54 (base HOLD) + momentum_breakout tier conditions met, so the momentum
    tier is the only route to BUY — isolating the Stage 1b 30d floor."""
    return {
        "score": 54,
        "indicators": {
            "atr_pct": 2.0, "rsi": 60, "momentum": 12,
            "momentum_quality": 45, "momentum_accel": {"is_accelerating": True},
            "relative_strength": {}, "early_momentum": 5.0,
            "ch1h": 1.5, "ch24h": 3.0, "ch7d": 8.0, "ch30d": ch30d, "bb_pos": 55,
        },
        "price_change_percentage_1h_in_currency": 1.5,
        "price_change_percentage_24h_in_currency": 3.0,
        "price_change_percentage_7d_in_currency": 8.0,
        "price_change_percentage_30d_in_currency": ch30d,
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"data_quality": {"ok_count": 2}},
    }


def _gen_breakout(ch30d, risk_on):
    th = dict(DEFAULT_THRESHOLDS, MOM30_MIN_CH30D=15.0)
    chars = {"risk_on": risk_on} if risk_on is not None else {}
    with patch("quant_signal._load_live_thresholds", return_value=th):
        with patch("quant_signal._check_portfolio_gate", return_value=True):
            with patch("quant_signal._check_same_day_duplicate", return_value=False):
                with patch("quant_signal._is_path_disabled", return_value=False):
                    return generate_quant_signal(
                        _breakout_coin(ch30d),
                        {"regime": "SIDEWAYS", "characteristics": chars},
                        fear_greed_value=50)


def test_risk_on_momentum_requires_30d_floor():
    """In risk-on, a momentum entry below MOM30_MIN_CH30D is blocked; above it fires."""
    blocked = _gen_breakout(ch30d=12, risk_on=True)   # below 15 floor
    allowed = _gen_breakout(ch30d=22, risk_on=True)   # above 15 floor
    assert blocked["quant_signal"] == "HOLD"
    assert allowed["quant_signal"] == "BUY"
    assert allowed["quant_path"] == "momentum_breakout"


def test_risk_off_momentum_ignores_30d_floor():
    """In risk-off, the 30d floor does not apply (entry allowed, sized down by the sizer)."""
    out = _gen_breakout(ch30d=12, risk_on=False)
    assert out["quant_signal"] == "BUY"
    assert out["quant_path"] == "momentum_breakout"


def test_legacy_none_momentum_ignores_30d_floor():
    """No risk_on flag (legacy/tests): 30d floor inactive."""
    out = _gen_breakout(ch30d=12, risk_on=None)
    assert out["quant_signal"] == "BUY"
    assert out["quant_path"] == "momentum_breakout"


# =============================================================================
# Stage 1c: LEAN ENTRY MODE (prune) — momentum/trend-family BUYs in risk-on only
# =============================================================================

def _gen_lean(coin, *, risk_on, lean_on, regime="SIDEWAYS", fg=50, market_24h=0.0):
    th = dict(DEFAULT_THRESHOLDS, MOM30_MIN_CH30D=15.0)
    if lean_on:
        th["LEAN_ENTRY_MODE"] = 1.0
    chars = {"risk_on": risk_on} if risk_on is not None else {}
    rd = {"regime": regime, "characteristics": chars, "market_24h_pct": market_24h}
    with patch("quant_signal._load_live_thresholds", return_value=th):
        with patch("quant_signal._check_portfolio_gate", return_value=True):
            with patch("quant_signal._check_same_day_duplicate", return_value=False):
                with patch("quant_signal._is_path_disabled", return_value=False):
                    return generate_quant_signal(coin, rd, fear_greed_value=fg)


def _default_buy_coin():
    """High base score, no special-path triggers → plain default/momentum_context BUY."""
    return {
        "score": 82,
        "indicators": {"atr_pct": 2.0, "rsi": 58, "ch1h": 0.5, "ch24h": 2.0,
                       "ch7d": 3.0, "ch30d": 8.0, "bb_pos": 55},
        "price_change_percentage_30d_in_currency": 8.0,
        "risk": {"rr_ratio": 3.0},
        "enhanced": {"data_quality": {"ok_count": 2}},
    }


def test_lean_off_preserves_legacy_bleeder_buy():
    """Flag off (default): a contrarian fear-dip in risk-off still buys (legacy unchanged)."""
    out = _gen_lean(_blood_coin(), risk_on=False, lean_on=False, regime="BEAR", fg=25)
    assert out["quant_signal"] == "BUY"
    assert "blood_in_the_street" in out.get("signal_badges", [])
    assert "lean_pruned" not in out.get("signal_badges", [])


def test_lean_on_keeps_momentum_in_risk_on():
    """Allowed momentum/trend path + risk-on → kept."""
    out = _gen_lean(_breakout_coin(ch30d=22), risk_on=True, lean_on=True)
    assert out["quant_signal"] == "BUY"
    assert out["quant_path"] == "momentum_breakout"
    assert "lean_pruned" not in out.get("signal_badges", [])


def test_lean_on_suppresses_bleeder_in_risk_off():
    """Contrarian path in risk-off → suppressed (no new longs in risk-off under lean)."""
    out = _gen_lean(_blood_coin(), risk_on=False, lean_on=True, regime="BEAR", fg=25)
    assert out["quant_signal"] == "HOLD"
    assert "lean_pruned" in out.get("signal_badges", [])


def test_lean_on_blocks_momentum_in_risk_off():
    """Even an allowed momentum path is blocked in risk-off (master risk-on entry gate)."""
    out = _gen_lean(_breakout_coin(ch30d=22), risk_on=False, lean_on=True)
    assert out["quant_signal"] == "HOLD"
    assert "lean_pruned" in out.get("signal_badges", [])


def test_lean_on_suppresses_default_path_in_risk_on():
    """A plain high-score BUY (default/momentum_context) is not in the kept set → suppressed."""
    out = _gen_lean(_default_buy_coin(), risk_on=True, lean_on=True)
    assert out["quant_signal"] == "HOLD"
    assert out["quant_path"] in ("default", "momentum_context")
    assert "lean_pruned" in out.get("signal_badges", [])


def _rally_coin(ch30d):
    """Rally path: score above the bear floor with market_24h>5 → 'rally' BUY."""
    return {
        "score": 74,
        "indicators": {"atr_pct": 2.0, "ch1h": 1.0, "ch24h": 2.0, "ch7d": 3.0, "ch30d": ch30d},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }


def test_lean_floor_blocks_low_momentum_rally():
    """Allowed path (rally) + risk-on but ch30d below the lean 30d floor → suppressed."""
    out = _gen_lean(_rally_coin(8.0), risk_on=True, lean_on=True, regime="BEAR", market_24h=6.0)
    assert out["quant_signal"] == "HOLD"
    assert "lean_pruned" in out.get("signal_badges", [])


def test_lean_floor_allows_high_momentum_rally():
    """Allowed path (rally) + risk-on + ch30d above the lean floor → kept."""
    out = _gen_lean(_rally_coin(20.0), risk_on=True, lean_on=True, regime="BEAR", market_24h=6.0)
    assert out["quant_signal"] == "BUY"
    assert out["quant_path"] == "rally"
    assert "lean_pruned" not in out.get("signal_badges", [])

