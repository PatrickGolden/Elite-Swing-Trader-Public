# tests/test_probabilistic_engine.py — Probabilistic engine upgrade (dynamic win_prob, sniper, vol_contraction, veto, 6-model, edge propagation)
# Run: pytest tests/test_probabilistic_engine.py -v --tb=short

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_dynamic_win_prob_fallback():
    """Dynamic win_prob: when no attribution data, fallback to regime base."""
    from model_attribution import get_historical_win_prob
    p = get_historical_win_prob('BEAR', 'dip', 3.0, 35, 68)
    assert 0.35 <= p <= 0.75
    p_bull = get_historical_win_prob('BULL', 'default', 2.0, 60, 72)
    assert p_bull >= 0.5


def test_dynamic_win_prob_with_mock_data():
    """Dynamic win_prob: regime+path lookup returns probability in valid range (fallback when no data)."""
    from model_attribution import get_historical_win_prob
    # With no or empty attribution, fallback to regime base
    p = get_historical_win_prob('BEAR', 'dip', 0, 50, 0)
    assert 0.35 <= p <= 0.75
    p2 = get_historical_win_prob('BULL', 'bull_participant', 2.0, 60, 70)
    assert 0.35 <= p2 <= 0.75


def test_quant_returns_edge_probability_and_historical_win_prob():
    """Quant return includes edge_probability, historical_win_prob (when _is_top_3), quant_path."""
    from quant_signal import generate_quant_signal
    coin = {
        'score': 72,
        '_is_top_3': True,
        'indicators': {'atr_pct': 2.0, 'ch24h': 2, 'ch7d': 4, 'ch30d': 6, 'momentum': 8},
        'price_change_percentage_24h': 2,
        'price_change_percentage_7d_in_currency': 4,
        'price_change_percentage_30d_in_currency': 6,
        'risk': {'rr_ratio': 3.0},
        'enhanced': {},
    }
    regime = {'regime': 'SIDEWAYS'}
    out = generate_quant_signal(coin, regime, 50)
    assert 'edge_probability' in out
    assert 'historical_win_prob' in out
    assert 'quant_path' in out
    assert 'Historical win prob' in out.get('reasoning', '')


def test_short_tf_sniper_active():
    """Short-TF sniper: when BUY + 1h stabilizing + OB bid-heavy + BB < 25, returns SNIPER_ENTRY."""
    from short_tf_sniper import get_short_tf_sniper
    coin = {
        'indicators': {'ch1h': 1.0, 'ch24h': -4.0, 'bb_pos': 20},
        'enhanced': {'order_book': {'bid_ask_ratio': 1.1}},
        'current_price': 1.5,
    }
    out = get_short_tf_sniper(coin, 'BUY')
    assert out.get('active') is True
    assert out.get('sub_signal') == 'SNIPER_ENTRY'
    assert out.get('confidence', 0) > 0


def test_short_tf_sniper_inactive_when_hold():
    """Short-TF sniper: when quant_signal is HOLD, sniper is inactive."""
    from short_tf_sniper import get_short_tf_sniper
    coin = {'indicators': {}, 'enhanced': {}}
    out = get_short_tf_sniper(coin, 'HOLD')
    assert out.get('active') is False
    assert out.get('sub_signal') is None


def test_vol_contraction_badge_and_path():
    """VOLATILE + BUY + ATR >= 4 + MTF aligned + funding < 0.05 → vol_contraction badge and STRONG_BUY."""
    from quant_signal import generate_quant_signal
    # Score 80 passes VOLATILE floor (72 when rally or 78); then vol_contraction can upgrade to STRONG
    coin = {
        'score': 80,
        'indicators': {'atr_pct': 4.5, 'ch24h': 2, 'ch7d': 3, 'ch30d': 5},
        'price_change_percentage_24h': 2,
        'price_change_percentage_7d_in_currency': 3,
        'price_change_percentage_30d_in_currency': 5,
        'risk': {'rr_ratio': 3.0},
        'enhanced': {'funding_rate': 0.02},
    }
    regime = {'regime': 'VOLATILE'}
    out = generate_quant_signal(coin, regime, 50)
    assert out.get('quant_signal') == 'BUY'
    assert 'vol_contraction' in out.get('signal_badges', [])


def test_dynamic_veto_threshold():
    """Dynamic veto: _get_dynamic_veto_threshold returns 65 for BEAR/VOLATILE, else 65/82/70 from attribution."""
    from veto_specialist import _get_dynamic_veto_threshold
    assert _get_dynamic_veto_threshold('BEAR') == 65
    assert _get_dynamic_veto_threshold('VOLATILE') == 65
    t = _get_dynamic_veto_threshold('BULL')
    assert t in (65, 70, 82)


def test_edge_probability_propagates_to_position_sizer():
    """Position sizer accepts edge_probability and rr_ratio and applies fractional Kelly."""
    from position_sizer import PositionSizer
    sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
    result_without = sizer.calculate_for_consensus(
        entry_price=100, stop_loss_pct=2.5, consensus_signal='STRONG_BUY',
        regime_data={'regime': 'BULL'}, coin_volatility=50,
    )
    result_with_edge = sizer.calculate_for_consensus(
        entry_price=100, stop_loss_pct=2.5, consensus_signal='STRONG_BUY',
        regime_data={'regime': 'BULL'}, coin_volatility=50,
        edge_probability=0.5, rr_ratio=3.0,
    )
    assert result_with_edge['position_size_usd'] <= result_without['position_size_usd'] * 1.01 + 1  # edge mult can reduce


def test_backward_compatibility_old_signals():
    """Backward compatibility: coins without new fields still work; quant returns all legacy keys."""
    from quant_signal import generate_quant_signal
    coin = {'score': 55, 'indicators': {'atr_pct': 2.0}, 'risk': {'rr_ratio': 3.0}, 'enhanced': {}}
    regime = {'regime': 'SIDEWAYS'}
    out = generate_quant_signal(coin, regime, 50)
    assert out.get('quant_signal') in ('BUY', 'HOLD')
    assert out.get('quant_tier') in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY', 'HOLD')
    assert 'edge_score' in out
    assert 'reasoning' in out
    assert 'signal_badges' in out
    assert isinstance(out.get('signal_badges'), list)


def test_manual_section_exists():
    """Manual: required sections exist and a version is declared.

    The version was previously pinned to the literal '4.2', which broke on every
    legitimate version bump without protecting anything. Assert a version EXISTS
    instead, and pin the section that must never silently disappear: the
    'Current Operating State' banner added 2026-08-22, which tells the reader the
    system is paper-only and which components are disarmed. A manual that overstates
    what the system does is worse than no manual when money is involved.
    """
    import re
    path = PROJECT_ROOT / 'system_manual.py'
    text = path.read_text(encoding='utf-8', errors='ignore')
    assert re.search(r"""['"]version['"]\s*:\s*['"][\d.]+['"]""", text), "manual declares no version"
    assert 'Recent Engine Upgrades (Feb 2026)' in text
    assert '_recent_engine_upgrades_feb2026' in text
    # Operating-state banner must remain, and must remain FIRST in Part 1.
    assert '_current_operating_state' in text, "operating-state section removed from system_manual"
    assert 'Current Operating State' in text
    from system_manual import ManualGenerator
    sections = ManualGenerator().manual_content['sections']
    titles = [s.get('title', '') for s in sections if isinstance(s, dict)]
    assert any('Current Operating State' in t for t in titles), "banner not in built manual"
    idx = next(i for i, t in enumerate(titles) if 'Current Operating State' in t)
    assert idx <= 2, f"operating-state banner must lead the manual, found at index {idx}"
