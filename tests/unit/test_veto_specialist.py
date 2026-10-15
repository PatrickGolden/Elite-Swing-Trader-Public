# tests/test_veto_specialist.py — Bear funding veto, whale distribution veto, narrative exhaustion veto, fallback logic
# Uses absolute imports; run pytest from project root (c:\swing-trader)
# Tests rule-based vetoes only ( to avoid API calls)

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from veto_specialist import evaluate, apply_veto_override


class TestBearFundingVeto:
    """BEAR regime + funding > 0.08% → VETO."""

    def test_bear_high_funding_veto(self):
        """BEAR regime with funding > 0.08% triggers VETO."""
        coin = {'name': 'Bitcoin', 'symbol': 'BTC'}
        regime_data = {'regime': 'BEAR'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'funding_rate': {'current': 0.12}}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'VETO'
        assert result['rule_triggered'] is True
        assert 'funding' in result['veto_reason'].lower() or 'squeeze' in result['veto_reason'].lower()

    def test_bear_low_funding_no_veto(self):
        """BEAR regime with funding <= 0.08% does not trigger funding veto."""
        coin = {'name': 'Bitcoin', 'symbol': 'BTC'}
        regime_data = {'regime': 'BEAR'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'funding_rate': {'current': 0.02}}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        # May still veto from other rules or LLM; with  and no other rules, should CONFIRM
        assert result['veto_decision'] in ('CONFIRM', 'VETO')
        if result['rule_triggered']:
            assert 'funding' not in result['veto_reason'].lower() or '0.02' in result['veto_reason']


class TestWhaleDistributionVeto:
    """Exchange netflow > 1500 → VETO (whale distributing)."""

    def test_whale_dump_veto(self):
        """High exchange netflow triggers VETO."""
        coin = {'name': 'Ethereum', 'symbol': 'ETH'}
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'exchange_netflow_24h': 2000}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'VETO'
        assert result['rule_triggered'] is True
        assert 'netflow' in result['veto_reason'].lower() or 'inflow' in result['veto_reason'].lower() or 'distribut' in result['veto_reason'].lower()

    def test_low_netflow_no_whale_veto(self):
        """Low netflow does not trigger whale veto."""
        coin = {'name': 'ETH', 'symbol': 'ETH'}
        regime_data = {'regime': 'BULL'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'exchange_netflow_24h': 200}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['rule_triggered'] is False or result['veto_decision'] == 'CONFIRM'


class TestNarrativeExhaustionVeto:
    """F&G >= 78 and 7d price change >= 25% → VETO."""

    def test_narrative_exhaustion_veto(self):
        """Extreme greed + big 7d rally triggers VETO (rule exempts BULL; use SIDEWAYS)."""
        coin = {
            'name': 'Solana',
            'symbol': 'SOL',
            'price_change_percentage_7d_in_currency': 30,
        }
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'ULTRA_BUY'}
        enhanced = {}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=85)
        assert result['veto_decision'] == 'VETO'
        assert result['rule_triggered'] is True
        assert 'exhaustion' in result['veto_reason'].lower() or 'greed' in result['veto_reason'].lower() or '7d' in result['veto_reason'].lower()

    def test_moderate_greed_no_exhaustion_veto(self):
        """Moderate F&G or small 7d move does not trigger."""
        coin = {'name': 'SOL', 'symbol': 'SOL', 'price_change_percentage_7d_in_currency': 5}
        regime_data = {'regime': 'BULL'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        result = evaluate(coin, regime_data, consensus, enhanced={}, fear_greed_value=60)
        assert result['rule_triggered'] is False or result['veto_decision'] == 'CONFIRM'


class TestLthDistributionVeto:
    """LTH SOPR >= 1.02 + LTH supply change > 0.3% → VETO."""

    def test_lth_distribution_veto(self):
        """LTH profit-taking triggers VETO."""
        coin = {'name': 'BTC', 'symbol': 'BTC'}
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'lth_sopr': 1.05, 'lth_supply_change_24h_pct': 0.5}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'VETO'
        assert result['rule_triggered'] is True
        assert 'LTH' in result['veto_reason'] or 'profit' in result['veto_reason'].lower() or 'distribut' in result['veto_reason'].lower()


class TestFundingVetoBoundary:
    """Boundary: funding 0.07 no veto, 0.09 veto. Threshold is > 0.08 (0.08 exactly = no veto)."""

    def test_funding_veto_boundary(self, monkeypatch):
        """Funding 0.07 in BEAR → no veto; 0.09 → veto. Patch threshold to 0.08 for deterministic test."""
        from veto_specialist import get_threshold
        _orig = get_threshold

        def mock_threshold(key, default):
            if key == 'FUNDING_VETO_BEAR_PCT':
                return 0.08
            if key == 'FUNDING_VETO_BEAR_RELAXED_PCT':
                return 0.12
            return _orig(key, default)

        monkeypatch.setattr('veto_specialist.get_threshold', mock_threshold)

        coin = {'name': 'Bitcoin', 'symbol': 'BTC'}
        regime_data = {'regime': 'BEAR'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        # Below threshold: no funding veto
        result_low = evaluate(
            coin, regime_data, consensus,
            enhanced={'funding_rate': {'current': 0.07}},
            fear_greed_value=50,
        )
        assert result_low['rule_triggered'] is False or 'funding' not in (result_low.get('veto_reason') or '').lower()
        # Above threshold: veto
        result_high = evaluate(
            coin, regime_data, consensus,
            enhanced={'funding_rate': {'current': 0.09}},
            fear_greed_value=50,
        )
        assert result_high['veto_decision'] == 'VETO'
        assert result_high['rule_triggered'] is True
        assert 'funding' in result_high['veto_reason'].lower() or 'squeeze' in result_high['veto_reason'].lower()


class TestVetoOrder:
    """Rule order: funding is checked first; if it would veto, reason must be funding not another rule."""

    def test_veto_order_funding_before_whale(self, monkeypatch):
        """When both funding (BEAR + high funding) and whale (high netflow) would fire, veto reason is funding."""
        from veto_specialist import get_threshold
        _orig = get_threshold

        def mock_threshold(key, default):
            if key == 'FUNDING_VETO_BEAR_PCT':
                return 0.08
            if key == 'FUNDING_VETO_BEAR_RELAXED_PCT':
                return 0.12
            return _orig(key, default)

        monkeypatch.setattr('veto_specialist.get_threshold', mock_threshold)

        coin = {'name': 'BTC', 'symbol': 'BTC'}
        regime_data = {'regime': 'BEAR'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {
            'funding_rate': {'current': 0.09},
            'exchange_netflow_24h': 2000,
        }
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'VETO'
        assert result['rule_triggered'] is True
        assert 'funding' in result['veto_reason'].lower() or 'squeeze' in result['veto_reason'].lower()


class TestMissingEnhancedNoCrash:
    """Empty or missing enhanced data must not crash; funding veto only when funding_pct present and > 0.08."""

    def test_missing_enhanced_no_crash(self):
        """enhanced empty or missing funding → no crash; funding veto only when funding_pct present and > 0.08."""
        coin = {'name': 'BTC', 'symbol': 'BTC'}
        regime_data = {'regime': 'BEAR'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        # Empty enhanced: no crash, no funding veto
        result_empty = evaluate(coin, regime_data, consensus, enhanced={}, fear_greed_value=50)
        assert result_empty['veto_decision'] in ('CONFIRM', 'VETO')
        if result_empty['rule_triggered']:
            assert 'funding' not in (result_empty.get('veto_reason') or '').lower()
        # funding_rate present but no 'current': no funding veto
        result_no_current = evaluate(
            coin, regime_data, consensus,
            enhanced={'funding_rate': {}},
            fear_greed_value=50,
        )
        assert result_no_current['veto_decision'] in ('CONFIRM', 'VETO')
        if result_no_current['rule_triggered']:
            assert 'funding' not in (result_no_current.get('veto_reason') or '').lower()
        # funding_rate current None: no funding veto (treated as 0)
        result_none = evaluate(
            coin, regime_data, consensus,
            enhanced={'funding_rate': {'current': None}},
            fear_greed_value=50,
        )
        assert result_none['veto_decision'] in ('CONFIRM', 'VETO')
        if result_none['rule_triggered']:
            assert 'funding' not in (result_none.get('veto_reason') or '').lower()


class TestFallbackLogic:
    """HOLD signal bypass, no-LLM fallback, apply_veto_override."""

    def test_hold_signal_bypasses_veto(self):
        """Consensus HOLD is not evaluated; returns CONFIRM with empty reason."""
        coin = {'name': 'X', 'symbol': 'X'}
        regime_data = {'regime': 'BEAR'}
        consensus = {'consensus_signal': 'HOLD'}
        enhanced = {'funding_rate': {'current': 0.15}}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'CONFIRM'
        assert result['veto_confidence'] == 0
        assert result['veto_reason'] == ''

    def test_apply_veto_override_sets_hold(self):
        """apply_veto_override sets consensus to HOLD when veto confidence >= threshold."""
        consensus = {'consensus_signal': 'STRONG_BUY', 'reasoning': 'Buy signal'}
        veto_result = {'veto_decision': 'VETO', 'veto_confidence': 85, 'veto_reason': 'Test veto reason'}
        apply_veto_override(consensus, veto_result, regime_data={'regime': 'BEAR'})
        assert consensus['consensus_signal'] == 'HOLD'
        assert 'veto_reason' in consensus
        assert 'Specialist:' in consensus.get('veto_reason', '')

    def test_apply_veto_low_confidence_no_override(self):
        """Low veto confidence does not override."""
        consensus = {'consensus_signal': 'STRONG_BUY'}
        veto_result = {'veto_decision': 'VETO', 'veto_confidence': 50, 'veto_reason': 'Weak reason'}
        apply_veto_override(consensus, veto_result, regime_data={'regime': 'BULL'})
        assert consensus['consensus_signal'] == 'STRONG_BUY'


# ---------------------------------------------------------------------------
# Whale Dump Veto: Market-Cap-Tiered Thresholds
# ---------------------------------------------------------------------------

class TestWhaleDumpTieredThresholds:
    """Whale dump veto uses market-cap-tiered thresholds (BTC/ETH: 5000, large: 3000, small: 1500)."""

    def test_btc_high_netflow_below_large_cap_threshold_no_veto(self):
        """BTC with netflow 2000 does NOT trigger veto (large-cap threshold is 5000)."""
        coin = {'name': 'Bitcoin', 'symbol': 'BTC', 'market_cap': 1_000_000_000_000}
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'exchange_netflow_24h': 2000}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        # Should NOT be vetoed by whale dump (2000 < 5000 for BTC tier)
        if result['rule_triggered']:
            assert 'netflow' not in (result.get('veto_reason') or '').lower()

    def test_btc_extreme_netflow_triggers_veto(self):
        """BTC with netflow 6000 DOES trigger veto (above 5000 large-cap threshold)."""
        coin = {'name': 'Bitcoin', 'symbol': 'BTC', 'market_cap': 1_000_000_000_000}
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'exchange_netflow_24h': 6000}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'VETO'
        assert 'netflow' in result['veto_reason'].lower() or 'inflow' in result['veto_reason'].lower()

    def test_small_cap_netflow_triggers_at_1500(self):
        """Small-cap coin with netflow 2000 triggers veto (threshold 1500)."""
        coin = {'name': 'SmallCoin', 'symbol': 'SC', 'market_cap': 500_000_000}
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'exchange_netflow_24h': 2000}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'VETO'
        assert result['rule_triggered'] is True

    def test_mid_cap_uses_mid_threshold(self):
        """Mid-cap coin (5-50B) with netflow 2000 does NOT trigger veto (threshold 3000)."""
        coin = {'name': 'Solana', 'symbol': 'SOL', 'market_cap': 20_000_000_000}
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'exchange_netflow_24h': 2000}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        if result['rule_triggered']:
            assert 'netflow' not in (result.get('veto_reason') or '').lower()

    def test_missing_market_cap_uses_default(self):
        """No market_cap in coin dict → falls through to small-cap threshold (1500)."""
        coin = {'name': 'Unknown', 'symbol': 'UNK'}
        regime_data = {'regime': 'SIDEWAYS'}
        consensus = {'consensus_signal': 'STRONG_BUY'}
        enhanced = {'exchange_netflow_24h': 2000}
        result = evaluate(coin, regime_data, consensus, enhanced=enhanced, fear_greed_value=50)
        assert result['veto_decision'] == 'VETO'
        assert result['rule_triggered'] is True
