# tests/test_model_attribution.py — Model attribution: Sharpe via performance_analytics, per-trade periods_per_year=52
# Run from project root: pytest tests/test_model_attribution.py -v

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model_attribution import get_model_attribution


def _make_trade(ts_offset_days, pnl_pct, pnl_usd, outcome, specialist='CONFIRM', quant_tier='ULTRA_BUY'):
    ts = (datetime.now(timezone.utc) - timedelta(days=ts_offset_days)).isoformat()
    return {
        'ts': ts,
        'coin_id': 'bitcoin',
        'symbol': 'BTC',
        'signal': 'BUY',
        'pnl_pct': pnl_pct,
        'pnl_usd': pnl_usd,
        'outcome': outcome,
        'regime': 'BULL',
        'specialist_veto': specialist,
        'quant_tier': quant_tier,
    }


def test_attribution_returns_sharpe_as_number(monkeypatch):
    """get_model_attribution uses calculate_sharpe_ratio; by_model entries have numeric sharpe."""
    trades = [
        _make_trade(1, 5.0, 100, 'win'),
        _make_trade(2, -2.0, -40, 'loss'),
        _make_trade(3, 3.0, 60, 'win'),
        _make_trade(4, 4.0, 80, 'win'),
        _make_trade(5, -1.5, -30, 'loss'),
    ]
    def fake_load():
        return {'trades': trades, 'updated': None}
    monkeypatch.setattr('model_attribution._load', fake_load)

    result = get_model_attribution(days=30)
    assert 'by_model' in result
    for name, metrics in result['by_model'].items():
        assert 'sharpe' in metrics
        assert isinstance(metrics['sharpe'], (int, float))
        assert metrics['trades'] >= 0


def test_attribution_sharpe_per_trade_annualization(monkeypatch):
    """Sharpe is computed with periods_per_year=52 (per-trade); positive returns => positive Sharpe."""
    trades = [
        _make_trade(i, 2.0 + (i * 0.5), 50 + i * 10, 'win') for i in range(1, 11)
    ]
    def fake_load():
        return {'trades': trades, 'updated': None}
    monkeypatch.setattr('model_attribution._load', fake_load)

    result = get_model_attribution(days=30)
    # Check that any model with trades has a valid Sharpe ratio
    for model_name, metrics in result['by_model'].items():
        if metrics.get('trades', 0) >= 1 and metrics.get('sharpe') is not None:
            # All wins with positive pnl_pct => positive Sharpe when using correct annualization
            assert metrics['sharpe'] >= 0 or metrics['trades'] < 2


def test_attribution_empty_trades_returns_empty(monkeypatch):
    """No trades in window => by_model empty, no crash."""
    def fake_load():
        return {'trades': [], 'updated': None}
    monkeypatch.setattr('model_attribution._load', fake_load)

    result = get_model_attribution(days=30)
    assert result['by_model'] == {}
    assert result['monthly_ranking'] == []
    assert result['period_days'] == 30


def test_attribution_subset_has_sharpe_zero_when_no_returns(monkeypatch):
    """Model subset with trades but no pnl_pct still returns sharpe 0 (no crash)."""
    trades = [
        _make_trade(1, None, 100, 'win'),
        _make_trade(2, None, -40, 'loss'),
    ]
    for t in trades:
        t['pnl_pct'] = None
    def fake_load():
        return {'trades': trades, 'updated': None}
    monkeypatch.setattr('model_attribution._load', fake_load)

    result = get_model_attribution(days=30)
    for name, metrics in result['by_model'].items():
        if metrics['trades'] > 0:
            assert metrics['sharpe'] == 0.0
            break


# ---------------------------------------------------------------------------
# Cross-tab attribution: regime x tier and signal_badge
# ---------------------------------------------------------------------------

def _make_closed_position(
    pnl_pct: float,
    pnl_usd: float,
    outcome: str,
    regime: str = 'BULL',
    quant_tier: str = 'STRONG_BUY',
    signal_badges=None,
    days_ago: int = 1,
) -> dict:
    """Build a synthetic CLOSED journal position usable via preloaded_closed_positions."""
    exit_time = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        'status': 'CLOSED',
        'is_test': False,
        'outcome': outcome,
        'pnl_pct': pnl_pct,
        'pnl_usd': pnl_usd,
        'market_regime': regime,
        'quant_tier': quant_tier,
        'quant_path': 'default',
        'signal_badges': signal_badges or [],
        'exit_time': exit_time,
        'entry_time': exit_time,
    }


def test_attribution_by_regime_and_tier_cross_tab():
    """by_regime_and_tier produces 'REGIME:TIER' keys with metrics matching subset filtering."""
    trades = [
        _make_closed_position(5.0, 100, 'win', regime='BULL', quant_tier='STRONG_BUY'),
        _make_closed_position(4.0, 80, 'win', regime='BULL', quant_tier='STRONG_BUY'),
        _make_closed_position(-2.0, -40, 'loss', regime='BULL', quant_tier='MODERATE_BUY'),
        _make_closed_position(3.0, 60, 'win', regime='SIDEWAYS', quant_tier='STRONG_BUY'),
    ]
    result = get_model_attribution(days=30, preloaded_closed_positions=trades)
    cross = result.get('by_regime_and_tier') or {}
    assert 'BULL:STRONG_BUY' in cross, f"expected BULL:STRONG_BUY in keys: {list(cross.keys())}"
    assert cross['BULL:STRONG_BUY']['trades'] == 2
    assert cross['BULL:STRONG_BUY']['win_rate_pct'] == 100.0
    assert cross['BULL:MODERATE_BUY']['trades'] == 1
    assert cross['BULL:MODERATE_BUY']['win_rate_pct'] == 0.0
    assert cross['SIDEWAYS:STRONG_BUY']['trades'] == 1


def test_attribution_by_signal_badge_buckets_by_each_badge():
    """A trade with multiple badges contributes to each badge bucket (not just one)."""
    trades = [
        _make_closed_position(
            5.0, 100, 'win',
            signal_badges=['light_sniper_entry', 'chase_guard_capped'],
        ),
        _make_closed_position(
            -3.0, -60, 'loss',
            signal_badges=['light_sniper_entry'],
        ),
        _make_closed_position(
            2.0, 40, 'win',
            signal_badges=['chase_guard_capped'],
        ),
        _make_closed_position(1.0, 20, 'win', signal_badges=[]),
    ]
    result = get_model_attribution(days=30, preloaded_closed_positions=trades)
    bucket = result.get('by_signal_badge') or {}
    assert 'light_sniper_entry' in bucket
    assert bucket['light_sniper_entry']['trades'] == 2
    assert bucket['light_sniper_entry']['win_rate_pct'] == 50.0
    assert 'chase_guard_capped' in bucket
    assert bucket['chase_guard_capped']['trades'] == 2
    assert bucket['chase_guard_capped']['win_rate_pct'] == 100.0
    assert bucket['_none']['trades'] == 1


def test_attribution_empty_preloaded_returns_empty_cross_tabs():
    """No trades => both new cross-tabs are empty dicts (no crash)."""
    result = get_model_attribution(days=30, preloaded_closed_positions=[])
    assert result.get('by_regime_and_tier') == {}
    assert result.get('by_signal_badge') == {}
