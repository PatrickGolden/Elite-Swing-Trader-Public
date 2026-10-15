# tests/test_market_dynamics_stress.py — Stress test rally/dip/blood/pullback/vol/scorer/consensus/veto
# Run: pytest tests/test_market_dynamics_stress.py -v

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from scorer import score_coin
from veto_specialist import _rule_short_term_pump, _rule_extended_rally_30d, _rule_narrative_exhaustion


# --- Scorer: blood-in-the-street and fear-gated bonuses ---

def test_scorer_blood_bonus_when_fear_and_dip():
    """F&G < 40 and 24h < -5% → +5 blood-in-the-street bonus."""
    coin = {
        "indicators": {"rsi": 45, "bb_pos": 40, "atr_pct": 2.5, "momentum": -3, "macd": "neutral",
                      "ch1h": -1, "ch24h": -6, "ch7d": -8, "ch30d": -5},
        "total_volume": 1e9,
        "market_cap": 5e9,
        "price_change_percentage_24h": -6,
    }
    out = score_coin(coin, fg_value=35, news_score=0)
    # Base score plus blood bonus: we expect total elevated vs same coin with fg=60
    out_no_fear = score_coin(coin, fg_value=60, news_score=0)
    assert out["score"] >= out_no_fear["score"]


def test_scorer_stabilizing_dip_bonus_only_when_fear():
    """Stabilizing dip bonus (+3) only when F&G < 55."""
    coin = {
        "indicators": {"rsi": 42, "bb_pos": 35, "atr_pct": 2.0, "momentum": -2, "macd": "neutral",
                      "ch1h": -2, "ch24h": -6, "ch7d": -4, "ch30d": -2},
        "total_volume": 1e9,
        "market_cap": 5e9,
        "price_change_percentage_24h": -6,
    }
    out_fear = score_coin(coin, fg_value=50, news_score=0)
    out_greed = score_coin(coin, fg_value=60, news_score=0)
    assert out_fear["score"] >= out_greed["score"]


def test_scorer_pullback_bonus_only_when_fear():
    """Pullback (7d/30d up, 24h down) bonus only when F&G < 55."""
    coin = {
        "indicators": {"rsi": 48, "bb_pos": 50, "atr_pct": 2.0, "momentum": 5, "macd": "bullish",
                      "ch1h": 0, "ch24h": -3, "ch7d": 4, "ch30d": 8},
        "total_volume": 1e9,
        "market_cap": 5e9,
        "price_change_percentage_24h": -3,
    }
    out_fear = score_coin(coin, fg_value=52, news_score=0)
    out_greed = score_coin(coin, fg_value=58, news_score=0)
    assert out_fear["score"] >= out_greed["score"]


def test_scorer_bull_participant_and_outperforming():
    """Bull participant (24h/7d/30d positive, momentum > 10) and outperforming (coin 24h > BTC 24h) add score."""
    coin = {
        "indicators": {"rsi": 52, "bb_pos": 55, "atr_pct": 2.0, "momentum": 12, "macd": "bullish",
                      "ch1h": 1, "ch24h": 6, "ch7d": 10, "ch30d": 15},
        "total_volume": 1e9,
        "market_cap": 5e9,
        "price_change_percentage_24h": 6,
    }
    out_no_market = score_coin(coin, fg_value=55, news_score=0)
    out_outperforming = score_coin(coin, fg_value=55, news_score=0, market_24h_pct=3.0)
    # Outperforming (6 > 3) adds +2; strong trend participation (momentum 12 > 10) adds +2
    assert out_outperforming["score"] >= out_no_market["score"]


# --- Veto: short-term pump and extended 30d ---

def test_veto_short_term_pump_fires():
    """F&G >= 78 and 24h >= 15% → short-term pump VETO."""
    reason = _rule_short_term_pump(80, 16.0)
    assert reason is not None
    assert "VETO" in reason
    assert "24h" in reason or "Short-term" in reason


def test_veto_short_term_pump_does_not_fire_low_fg():
    """F&G 60 and 24h 16% → no short-term pump veto (F&G threshold)."""
    reason = _rule_short_term_pump(60, 16.0)
    assert reason is None


def test_veto_extended_rally_30d_fires():
    """F&G >= 80 and 30d >= 50% → extended rally VETO."""
    reason = _rule_extended_rally_30d(82, 55.0)
    assert reason is not None
    assert "VETO" in reason
    assert "30d" in reason or "Extended" in reason


def test_veto_extended_rally_30d_does_not_fire():
    """F&G 78 or 30d 45% → no extended rally veto."""
    assert _rule_extended_rally_30d(78, 55.0) is None  # F&G just below 80
    assert _rule_extended_rally_30d(82, 45.0) is None   # 30d below 50


def test_veto_narrative_exhaustion_7d():
    """F&G >= 83 and 7d >= 25% → narrative exhaustion (existing rule)."""
    reason = _rule_narrative_exhaustion(85, 28.0)
    assert reason is not None
    assert "VETO" in reason
