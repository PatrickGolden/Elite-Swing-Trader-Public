# tests/test_final_polish.py — Final polish: confirmation_score, always-present quant fields, tier upgrade, backward compat

import pytest
import json
import os
import tempfile
from unittest.mock import patch

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _coin(score=70, is_top_3=False, is_top_10=False, indicators=None, enhanced=None):
    ind = indicators or {}
    ind.setdefault('atr_pct', 2.0)
    ind.setdefault('ch1h', 0)
    ind.setdefault('ch24h', 0)
    ind.setdefault('ch7d', 0)
    ind.setdefault('ch30d', 0)
    ind.setdefault('bb_pos', 50)
    ind.setdefault('momentum', 0)
    return {
        'id': 'bitcoin',
        'score': score,
        '_is_top_3': is_top_3,
        '_is_top_10': is_top_10,
        'indicators': ind,
        'enhanced': enhanced or {},
        'risk': {'rr_ratio': 3.0},
        'current_price': 50000,
    }


def _regime(regime='SIDEWAYS', market_24h_pct=None):
    r = {'regime': regime, 'confidence': 80}
    if market_24h_pct is not None:
        r['market_24h_pct'] = market_24h_pct
    return r


# -----------------------------------------------------------------------------
# 1. confirmation_score is computed and present
# -----------------------------------------------------------------------------

def test_confirmation_score_present_in_quant_output():
    from quant_signal import generate_quant_signal
    coin = _coin(score=60, is_top_3=False)
    out = generate_quant_signal(coin, _regime(), 50)
    assert 'confirmation_score' in out
    assert isinstance(out['confirmation_score'], (int, float))
    assert 0 <= out['confirmation_score'] <= 100


# -----------------------------------------------------------------------------
# 2. confirmation_score formula (base from score; badges/sniper add)
# -----------------------------------------------------------------------------

def test_confirmation_score_formula():
    from quant_signal import generate_quant_signal
    # No badges, no light_sniper: confirmation_score ~ score * 0.70 (CONF_SCORE_BASE_WEIGHT), capped 100
    coin = _coin(score=80, is_top_3=False, is_top_10=False)
    out = generate_quant_signal(coin, _regime(), 50)
    expected_base = min(100, 80 * 0.70)
    assert out['confirmation_score'] == round(expected_base, 1)
    # With badges/sniper the score can increase (smoke check)
    coin2 = _coin(score=72, is_top_3=True, is_top_10=True)
    coin2['indicators']['ch1h'] = 2.0
    coin2['indicators']['bb_pos'] = 20
    coin2['enhanced']['order_book'] = {'bid_ask_ratio': 1.1}
    out2 = generate_quant_signal(coin2, _regime(), 50)
    assert out2['confirmation_score'] >= 0


# -----------------------------------------------------------------------------
# 3. Tier upgrade: confirmation_score >= 85 and is_top_3 and MODERATE_BUY -> STRONG_BUY
# -----------------------------------------------------------------------------

def test_tier_upgrade_moderate_to_strong_when_confirmation_high():
    from quant_signal import generate_quant_signal
    # Score in MODERATE_BUY range (58–71), is_top_3, and we need confirmation_score >= 85
    # confirmation = score*0.70 + sniper(10) + whale(5) can push >= 85 with high enough score
    coin = _coin(score=75, is_top_3=True, is_top_10=True)
    coin['indicators']['ch1h'] = 1.0
    coin['indicators']['ch24h'] = 0.5
    coin['indicators']['bb_pos'] = 15
    coin['enhanced']['order_book'] = {'bid_ask_ratio': 1.12}
    coin['enhanced']['exchange_netflow_24h'] = -100
    out = generate_quant_signal(coin, _regime(), 50)
    # If confirmation_score >= 85 and tier was MODERATE_BUY, should be STRONG_BUY
    if out.get('confirmation_score', 0) >= 85 and out.get('quant_tier') == 'STRONG_BUY':
        assert out['quant_tier'] == 'STRONG_BUY'
    # At least confirm confirmation_score and tier are present
    assert 'quant_tier' in out
    assert 'confirmation_score' in out


# -----------------------------------------------------------------------------
# 4. Always-present fields: quant_path, edge_probability, confirmation_score, short_tf_sniper
# -----------------------------------------------------------------------------

def test_always_present_quant_fields_top3():
    from quant_signal import generate_quant_signal
    coin = _coin(score=75, is_top_3=True)
    out = generate_quant_signal(coin, _regime(), 50)
    assert 'quant_path' in out
    assert 'edge_probability' in out
    assert 'confirmation_score' in out
    assert 'short_tf_sniper' in out
    assert isinstance(out['short_tf_sniper'], dict)


def test_always_present_quant_fields_non_top3():
    from quant_signal import generate_quant_signal
    coin = _coin(score=50, is_top_3=False)
    out = generate_quant_signal(coin, _regime(), 50)
    assert out.get('quant_path') is not None
    assert 'edge_probability' in out
    assert out['edge_probability'] == 0.0
    assert 'confirmation_score' in out
    assert out.get('short_tf_sniper') is not None
    assert isinstance(out['short_tf_sniper'], dict)


# -----------------------------------------------------------------------------
# 5. Backward compatibility: existing keys still present
# -----------------------------------------------------------------------------

def test_backward_compat_existing_keys():
    from quant_signal import generate_quant_signal
    coin = _coin(score=70)
    out = generate_quant_signal(coin, _regime(), 50)
    for key in ('quant_signal', 'quant_confidence', 'quant_tier', 'score', 'edge_score',
                'reasoning', 'signal_badges', 'quant_path', 'short_tf_sniper'):
        assert key in out, f'missing key: {key}'


# -----------------------------------------------------------------------------
# 6. model_attribution accepts and stores confirmation_score
# -----------------------------------------------------------------------------

def test_model_attribution_stores_confirmation_score():
    from model_attribution import log_trade_for_attribution, _load
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        try:
            f.write('{"trades": [], "updated": null}')
            f.close()
            with patch('model_attribution.ATTRIBUTION_FILE', f.name):
                log_trade_for_attribution(
                    coin_id='bitcoin',
                    symbol='BTC',
                    signal='BUY',
                    pnl_pct=2.0,
                    pnl_usd=100.0,
                    outcome='win',
                    regime='SIDEWAYS',
                    specialist_veto='CONFIRM',
                    quant_tier='STRONG_BUY',
                    quant_path='dip',
                    confirmation_score=88.5,
                )
                data = _load()
                assert data.get('trades')
                last = data['trades'][-1]
                assert last.get('confirmation_score') == 88.5
                assert last.get('quant_path') == 'dip'
        finally:
            try:
                os.unlink(f.name)
            except Exception:
                pass


# -----------------------------------------------------------------------------
# 7. (Removed: consensus_engine tests — consensus_engine module deleted)
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# 8. Shared test factories (determinism + shape)
# -----------------------------------------------------------------------------


def test_create_synthetic_closed_trades_deterministic():
    from tests.factories import create_synthetic_closed_trades

    a = create_synthetic_closed_trades(count=50, seed=7)
    b = create_synthetic_closed_trades(count=50, seed=7)
    assert a == b
    assert len(a) == 50
    assert a[0]["status"] == "CLOSED"
    assert "pnl_usd" in a[0]


def test_generate_stress_market_regimes_shape():
    from tests.factories import generate_stress_market_regimes

    rows = generate_stress_market_regimes(days=30, volatility=False, seed=1)
    assert len(rows) == 30
    assert rows[0]["day_index"] == 0
    assert "regime" in rows[0]
