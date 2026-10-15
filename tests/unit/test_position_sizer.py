# tests/test_position_sizer.py — Risk-based sizing, cash cap, Kelly cap, volatility adjustments
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from position_sizer import PositionSizer


class TestPositionSizerRiskBasedSizing:
    """Test risk-based position sizing."""

    def test_base_position_size_from_risk(self):
        """Position size = max_risk_usd / (stop_loss_pct / 100)."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_position_size(
            entry_price=45000,
            stop_loss_pct=2.5,
            confidence_level='Very High',
            regime_adjustment=1.0,
            volatility_factor=1.0,
        )
        # max_risk_usd = 400, base = 400 / 0.025 = 16000
        assert result['position_size_usd'] > 0
        assert result['risk_usd'] <= 400
        assert result['max_loss_pct'] <= 2.5
        assert result['quantity'] > 0

    def test_confidence_reduces_size(self):
        """Lower confidence reduces position size."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        full = sizer.calculate_position_size(45000, 2.5, 'Very High', 1.0, 1.0)
        reduced = sizer.calculate_position_size(45000, 2.5, 'Medium', 1.0, 1.0)
        assert reduced['position_size_usd'] < full['position_size_usd']
        assert reduced['breakdown']['confidence_mult'] == 0.5
        assert full['breakdown']['confidence_mult'] == 1.0

    def test_regime_adjustment_applied(self):
        """Regime adjustment multiplies position size."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        base = sizer.calculate_position_size(45000, 2.5, 'Very High', 1.0, 1.0)
        bear = sizer.calculate_position_size(45000, 2.5, 'Very High', 0.6, 1.0)
        assert bear['position_size_usd'] < base['position_size_usd']
        assert bear['breakdown']['regime_mult'] == 0.6

    def test_volatility_factor_reduces_size(self):
        """High volatility reduces position size."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        normal = sizer.calculate_position_size(45000, 2.5, 'Very High', 1.0, 1.0)
        high_vol = sizer.calculate_position_size(45000, 2.5, 'Very High', 1.0, 0.6)
        assert high_vol['position_size_usd'] < normal['position_size_usd']
        assert high_vol['breakdown']['volatility_mult'] == 0.6


class TestPositionSizerCashCap:
    """Test cash availability cap."""

    def test_capped_by_cash_when_insufficient(self):
        """Position is capped when cash_available is less than calculated size."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_position_size(
            entry_price=45000,
            stop_loss_pct=2.5,
            confidence_level='Very High',
            regime_adjustment=1.0,
            volatility_factor=1.0,
            cash_available=500,
        )
        assert result['position_size_usd'] <= 500
        assert result['capped_by_cash'] is True
        assert abs(result['quantity'] - (500 / 45000)) < 0.0001

    def test_skip_when_no_cash(self):
        """Recommendation is SKIP when cash_available is 0."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_position_size(
            entry_price=45000,
            stop_loss_pct=2.5,
            confidence_level='Very High',
            regime_adjustment=1.0,
            volatility_factor=1.0,
            cash_available=0,
        )
        assert result['recommendation'] == 'SKIP'
        assert result['position_size_usd'] == 0
        assert result['quantity'] == 0


class TestPositionSizerKellyCap:
    """Test Kelly fraction cap."""

    def test_kelly_cap_applied_when_exceeded(self):
        """Kelly cap (5% of account) limits position when exceeded."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        # STRONG_BUY with low stop = large base size; Kelly cap = 1000
        result = sizer.calculate_for_consensus(
            entry_price=100,
            stop_loss_pct=0.5,  # Very tight stop = huge base size
            consensus_signal='ULTRA_BUY',
            coin_volatility=30,
        )
        assert result['kelly_cap_usd'] == 1000  # 5% of 20000
        # Position should be capped at 1000 or less
        assert result['position_size_usd'] <= 1000

    def test_kelly_cap_not_applied_when_under(self):
        """Kelly cap not applied when position is under cap."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_for_consensus(
            entry_price=45000,
            stop_loss_pct=8.0,  # Wide stop = smaller base size
            consensus_signal='WEAK_BUY',
            coin_volatility=50,
        )
        # WEAK_BUY + 8% stop = small base; expect under 1000 Kelly cap
        assert result['position_size_usd'] <= 1000
        assert result['kelly_cap_usd'] == 1000


class TestPositionSizerVolatilityAdjustment:
    """Test volatility adjustment in calculate_for_consensus."""

    def test_high_volatility_reduces_size(self):
        """High coin volatility (>100) reduces size via volatility_factor 0.6."""
        sizer = PositionSizer(account_size=5000, max_risk_pct=2.0)  # Smaller account so Kelly cap doesn't dominate
        low_vol = sizer.calculate_for_consensus(45000, 2.5, 'STRONG_BUY', coin_volatility=30)
        high_vol = sizer.calculate_for_consensus(45000, 2.5, 'STRONG_BUY', coin_volatility=120)
        assert high_vol['position_size_usd'] <= low_vol['position_size_usd']
        assert high_vol['breakdown']['volatility_mult'] <= low_vol['breakdown']['volatility_mult']

    def test_low_volatility_increases_size(self):
        """Low volatility (<50) allows slightly larger size (factor 1.2)."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        normal = sizer.calculate_for_consensus(45000, 2.5, 'STRONG_BUY', coin_volatility=60)
        low_vol = sizer.calculate_for_consensus(45000, 2.5, 'STRONG_BUY', coin_volatility=30)
        assert low_vol['position_size_usd'] >= normal['position_size_usd']


class TestRiskOffHaircut:
    """Stage 1b: risk-off (BTC < 200d-MA) momentum entries are sized down by RISK_OFF_SIZE_MULT."""

    def test_risk_off_halves_size(self):
        # Wide stop (30%) keeps the base below the Kelly cap so the haircut is visible.
        sizer = PositionSizer(account_size=5000, max_risk_pct=2.0)
        risk_on = sizer.calculate_for_consensus(
            45000, 30.0, 'ULTRA_BUY', regime_data={'characteristics': {'risk_on': True}},
            coin_volatility=60)
        risk_off = sizer.calculate_for_consensus(
            45000, 30.0, 'ULTRA_BUY', regime_data={'characteristics': {'risk_on': False}},
            coin_volatility=60)
        assert risk_off['position_size_usd'] < risk_on['position_size_usd']
        assert risk_off['breakdown']['regime_mult'] == 0.5
        assert risk_on['breakdown']['regime_mult'] == 1.0

    def test_risk_on_none_no_haircut(self):
        """Legacy/tests with no risk_on flag: sizing unchanged."""
        sizer = PositionSizer(account_size=5000, max_risk_pct=2.0)
        no_flag = sizer.calculate_for_consensus(45000, 30.0, 'ULTRA_BUY', coin_volatility=60)
        assert no_flag['breakdown']['regime_mult'] == 1.0


class TestPositionSizerSettings:
    """Test settings and updates."""

    def test_get_settings(self):
        """get_settings returns account_size, max_risk_pct, max_risk_usd."""
        sizer = PositionSizer(account_size=25000, max_risk_pct=1.5)
        settings = sizer.get_settings()
        assert settings['account_size'] == 25000
        assert settings['max_risk_pct'] == 1.5
        assert settings['max_risk_usd'] == 375
        assert 'max_cash_usage_pct' in settings

    def test_update_account_size(self):
        """update_account_size updates account and max_risk_usd."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        sizer.update_account_size(30000)
        assert sizer.account_size == 30000
        assert sizer.max_risk_usd == 600


class TestPositionSizerRecommendationBands:
    """Recommendation bands: FULL >= 80% of base, REDUCED >= 50%, MINIMAL >= 25%, else SKIP."""

    def test_full_when_final_near_base(self):
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_position_size(10000, 2.0, 'Very High', 1.0, 1.0)
        assert result['recommendation'] == 'FULL'
        assert result['position_size_usd'] >= result['breakdown']['base_size'] * 0.8

    def test_reduced_at_medium_confidence(self):
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_position_size(10000, 2.0, 'Medium', 1.0, 1.0)
        assert result['recommendation'] == 'REDUCED'
        assert result['breakdown']['confidence_mult'] == 0.5

    def test_skip_when_confidence_too_low(self):
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_position_size(10000, 2.0, 'Low', 1.0, 1.0)
        assert result['recommendation'] in ('MINIMAL', 'SKIP')


class TestPositionSizerCapOrder:
    """Kelly cap then liquidity cap (max_cash_usage_pct) both applied in calculate_for_consensus."""

    def test_kelly_then_cash_cap_both_applied(self):
        """When both Kelly (5% of 20k=1000) and cash cap (40% of 500=200) bind, final = min."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        result = sizer.calculate_for_consensus(
            entry_price=50,
            stop_loss_pct=1.0,
            consensus_signal='ULTRA_BUY',
            coin_volatility=30,
            cash_available=500,
            max_cash_usage_pct=40.0,
        )
        assert result['kelly_cap_usd'] == 1000
        assert result['position_size_usd'] <= 200  # 40% of 500
        assert result['cash_cap_applied'] is True
