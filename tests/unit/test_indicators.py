"""Tests for indicators.py — deterministic technical indicator calculations."""

import gc
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from indicators import (
    calculate_atr_pct,
    calculate_bb_position,
    calculate_early_momentum,
    calculate_indicators,
    calculate_macd_signal,
    calculate_momentum,
    calculate_momentum_acceleration,
    calculate_momentum_quality,
    calculate_relative_strength,
    calculate_rsi,
    calculate_volatility,
    calculate_weekly_rsi,
)


@pytest.fixture(autouse=True)
def _gc_collect():
    yield
    gc.collect()


# =============================================================================
# RSI
# =============================================================================
class TestRSI:
    def test_empty_prices(self):
        assert calculate_rsi([]) == 50.0

    def test_short_prices(self):
        assert calculate_rsi(list(range(10)), period=14) == 50.0

    def test_all_gains(self):
        """Monotonically rising prices → avg_loss=0 → RSI 100."""
        prices = list(range(1, 20))
        assert calculate_rsi(prices, period=14) == 100.0

    def test_uptrend_above_50(self):
        prices = [100 + i * 0.5 + (-0.1 if i % 5 == 0 else 0) for i in range(30)]
        rsi = calculate_rsi(prices)
        assert rsi > 50

    def test_downtrend_below_50(self):
        prices = [100 - i * 0.5 + (0.1 if i % 5 == 0 else 0) for i in range(30)]
        rsi = calculate_rsi(prices)
        assert rsi < 50

    def test_known_value(self):
        """Simple alternating gains/losses with known avg_gain == avg_loss → RSI ~50."""
        # 15 deltas alternating +1, -1 → equal gains and losses
        prices = [100.0]
        for i in range(30):
            prices.append(prices[-1] + (1.0 if i % 2 == 0 else -1.0))
        rsi = calculate_rsi(prices, period=14)
        # With equal avg_gain and avg_loss the RS ≈ 1, RSI ≈ 50
        assert 45 < rsi < 55

    def test_result_is_float_in_range(self):
        np.random.seed(42)
        prices = list(np.cumsum(np.random.randn(100)) + 100)
        rsi = calculate_rsi(prices)
        assert isinstance(rsi, float)
        assert 0 <= rsi <= 100


# =============================================================================
# Weekly RSI
# =============================================================================
class TestWeeklyRSI:
    def test_empty_prices(self):
        assert calculate_weekly_rsi([]) == 50.0

    def test_insufficient_hours(self):
        """Need 24 * (14+1) = 360 hourly prices minimum."""
        prices = list(range(300))
        assert calculate_weekly_rsi(prices) == 50.0

    def test_sufficient_data_uptrend(self):
        """400 hourly prices in a steady uptrend → weekly RSI > 50."""
        prices = [100 + i * 0.05 for i in range(400)]
        rsi = calculate_weekly_rsi(prices)
        assert rsi > 50

    def test_resamples_to_daily(self):
        """Verify it picks every 24th price as daily close."""
        # Flat prices → RSI neutral; just verifying no crash with exact boundary
        prices = [100.0] * (24 * 15)  # exactly 360
        rsi = calculate_weekly_rsi(prices)
        # All same → all deltas zero → avg_loss=0 → RSI 100
        assert rsi == 100.0


# =============================================================================
# Bollinger Band Position
# =============================================================================
class TestBollingerBands:
    def test_empty_prices(self):
        assert calculate_bb_position([]) == 50.0

    def test_short_prices(self):
        assert calculate_bb_position(list(range(10)), period=20) == 50.0

    def test_zero_std(self):
        """All same prices → std=0 → return 50."""
        assert calculate_bb_position([100.0] * 25) == 50.0

    def test_price_at_lower_band(self):
        """Last price well below SMA → position near 0."""
        prices = [100.0] * 19 + [80.0]
        pos = calculate_bb_position(prices, period=20)
        assert pos < 10

    def test_price_at_upper_band(self):
        """Last price well above SMA → position near 100."""
        prices = [100.0] * 19 + [120.0]
        pos = calculate_bb_position(prices, period=20)
        assert pos > 90

    def test_clipped_to_range(self):
        """Extreme outlier still clipped to [0, 100]."""
        prices = [100.0] * 19 + [200.0]
        pos = calculate_bb_position(prices, period=20)
        assert 0 <= pos <= 100


# =============================================================================
# ATR %
# =============================================================================
class TestATR:
    def test_short_data(self):
        assert calculate_atr_pct([1, 2, 3]) == 2.0

    def test_zero_last_price(self):
        prices = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0, 0, 0, 0]
        assert calculate_atr_pct(prices) == 2.0

    def test_normal_calculation(self):
        # 16 prices with known ranges
        prices = [100.0 + i for i in range(16)]  # step size = 1
        atr = calculate_atr_pct(prices, period=14)
        # Each diff = 1, mean diff = 1, last price = 115, atr_pct = 1/115*100 ≈ 0.8696
        assert 0.5 < atr < 1.5


# =============================================================================
# Volatility
# =============================================================================
class TestVolatility:
    def test_empty_prices(self):
        assert calculate_volatility([]) == 0.0

    def test_single_price(self):
        assert calculate_volatility([100.0]) == 0.0

    def test_constant_prices(self):
        assert calculate_volatility([100.0] * 50) == 0.0

    def test_division_by_zero_in_returns(self):
        """Prices containing zero → inf returns → filtered out → 0.0."""
        assert calculate_volatility([0, 0, 0]) == 0.0

    def test_positive_result(self):
        np.random.seed(99)
        prices = list(np.cumsum(np.random.randn(100)) + 500)
        vol = calculate_volatility(prices)
        assert vol > 0


# =============================================================================
# MACD Signal
# =============================================================================
class TestMACDSignal:
    def test_short_data(self):
        assert calculate_macd_signal(list(range(20))) == 'neutral'

    def test_strong_uptrend_bullish(self):
        # Exponential uptrend so EMA12 >> EMA26 and increasing
        prices = [100 * (1.02 ** i) for i in range(40)]
        assert calculate_macd_signal(prices) == 'bullish'

    def test_strong_downtrend_bearish(self):
        # Linear decline: MACD stays negative and worsens vs 4 periods ago
        prices = [200 - i * 3 for i in range(50)]
        assert calculate_macd_signal(prices) == 'bearish'

    def test_flat_neutral(self):
        prices = [100.0] * 40
        assert calculate_macd_signal(prices) == 'neutral'


# =============================================================================
# Momentum
# =============================================================================
class TestMomentum:
    def test_all_zeros(self):
        assert calculate_momentum(0, 0, 0, 0) == 0.0

    def test_all_none(self):
        assert calculate_momentum(None, None, None, None) == 0.0

    def test_known_weights(self):
        # weights: 0.05, 0.25, 0.45, 0.25
        result = calculate_momentum(10.0, 5.0, 3.0, 2.0)
        expected = round(0.05 * 10.0 + 0.25 * 5.0 + 0.45 * 3.0 + 0.25 * 2.0, 3)
        assert result == expected


# =============================================================================
# Early Momentum
# =============================================================================
class TestEarlyMomentum:
    def test_weights(self):
        # weights: 0.20, 0.40, 0.30, 0.10
        result = calculate_early_momentum(10.0, 5.0, 3.0, 2.0)
        expected = round(0.20 * 10.0 + 0.40 * 5.0 + 0.30 * 3.0 + 0.10 * 2.0, 3)
        assert result == expected

    def test_none_handling(self):
        assert calculate_early_momentum(None, None, None, None) == 0.0


# =============================================================================
# Momentum Acceleration
# =============================================================================
class TestMomentumAcceleration:
    def test_all_zeros(self):
        result = calculate_momentum_acceleration(0, 0, 0, 0)
        assert result['short_accel'] == 0.0
        assert result['medium_accel'] == 0.0
        assert result['long_accel'] == 0.0
        assert result['is_accelerating'] is False

    def test_accelerating(self):
        # c1h=2 → daily_from_1h=48, c24h=5 → daily_from_24h=5
        # c7d=14 → daily_from_7d=2, c30d=30 → daily_from_30d=1
        # short_accel = 48-5 = 43 > 0, medium_accel = 5-2 = 3 > 0 → accelerating
        result = calculate_momentum_acceleration(2, 5, 14, 30)
        assert result['is_accelerating'] is True
        assert result['short_accel'] == 43.0
        assert result['medium_accel'] == 3.0

    def test_decelerating(self):
        # c1h=-1 → daily=-24, c24h=5 → daily=5
        # short_accel = -24-5 = -29 < 0 → not accelerating
        result = calculate_momentum_acceleration(-1, 5, 14, 30)
        assert result['is_accelerating'] is False


# =============================================================================
# Momentum Quality
# =============================================================================
class TestMomentumQuality:
    def test_negative_c7d(self):
        assert calculate_momentum_quality([100] * 100, -5) == 0.0

    def test_zero_c7d(self):
        assert calculate_momentum_quality([100] * 100, 0) == 0.0

    def test_short_prices(self):
        assert calculate_momentum_quality([100] * 10, 5) == 0.0

    def test_zero_vol(self):
        """Constant prices → vol=0 → return 0."""
        assert calculate_momentum_quality([100.0] * 200, 5) == 0.0

    def test_normalized_range(self):
        np.random.seed(7)
        prices = list(np.cumsum(np.random.randn(200) * 0.01) + 100)
        quality = calculate_momentum_quality(prices, 10)
        assert 0 <= quality <= 100


# =============================================================================
# Relative Strength
# =============================================================================
class TestRelativeStrength:
    def test_outperforming(self):
        result = calculate_relative_strength(5.0, 10.0, 1.0, 2.0)
        assert result['strong'] is True
        assert result['rs_24h'] == 4.0
        assert result['rs_7d'] == 8.0

    def test_underperforming(self):
        result = calculate_relative_strength(1.0, 1.0, 5.0, 10.0)
        assert result['strong'] is False

    def test_none_inputs(self):
        result = calculate_relative_strength(None, None, None, None)
        assert result['rs_24h'] == 0.0
        assert result['rs_7d'] == 0.0
        assert result['strong'] is False


# =============================================================================
# calculate_indicators (integration)
# =============================================================================
class TestCalculateIndicators:
    def test_empty_coins(self):
        data = {'coins': []}
        result = calculate_indicators(data)
        assert result['coins'] == []

    def test_coin_with_sparkline(self):
        """Full pipeline: coin gets all indicators attached."""
        prices = [100 + i * 0.1 for i in range(168)]
        coin = {
            'sparkline_in_7d': {'price': prices},
            'price_change_percentage_1h_in_currency': 0.5,
            'price_change_percentage_24h': 2.0,
            'price_change_percentage_7d_in_currency': 5.0,
            'price_change_percentage_30d_in_currency': 10.0,
        }
        data = {'coins': [coin], 'market_24h_pct': 1.0, 'market_7d_pct': 3.0}
        result = calculate_indicators(data)
        ind = result['coins'][0]['indicators']

        expected_keys = {
            'rsi', 'weekly_rsi', 'bb_pos', 'atr_pct', 'volatility', 'macd',
            'momentum', 'early_momentum', 'momentum_accel', 'momentum_quality',
            'relative_strength', 'ch1h', 'ch24h', 'ch7d', 'ch30d',
        }
        assert set(ind.keys()) == expected_keys
        assert isinstance(ind['rsi'], float)
        assert ind['macd'] in ('bullish', 'bearish', 'neutral')

    def test_missing_sparkline(self):
        """Coin without sparkline gets neutral/default indicators without error."""
        coin = {'price_change_percentage_24h': 1.0}
        data = {'coins': [coin]}
        result = calculate_indicators(data)
        ind = result['coins'][0]['indicators']
        assert ind['rsi'] == 50.0
        assert ind['bb_pos'] == 50.0
        assert ind['atr_pct'] == 2.0
