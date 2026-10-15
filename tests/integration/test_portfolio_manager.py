# tests/test_portfolio_manager.py — Correlation cap, VaR, adjustment factor
# Run from project root: pytest tests/test_portfolio_manager.py -v

import sys
import math
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from portfolio_manager import (
    get_portfolio_risk_summary,
    apply_correlation_adjustment,
    _historical_var_usd,
    CORRELATION_CAP,
    BTC_CORR_THRESHOLD,
)


class TestAdjustmentFactorWhenOverCap:
    """Adjustment factor f when correlated risk exceeds 35% of total."""

    def test_adjustment_factor_when_over_cap(self):
        """Total risk 50, correlated 25 (50%) → f < 1 so correlated becomes ≤35% of total."""
        account_size = 10_000.0
        # Correlated: 25% total (two coins with btc_correlation > 0.85, 12.5% each)
        high_corr_coins = [
            {
                "id": "high1",
                "name": "HighCorr1",
                "symbol": "HC1",
                "enhanced": {"btc_correlation": 0.90},
                "ai_recommendation": {
                    "position_sizing": {"pct_of_account": 12.5},
                },
            },
            {
                "id": "high2",
                "name": "HighCorr2",
                "symbol": "HC2",
                "enhanced": {"btc_correlation": 0.88},
                "ai_recommendation": {
                    "position_sizing": {"pct_of_account": 12.5},
                },
            },
        ]
        # Non-correlated: 25% total (two coins with btc_correlation <= 0.85)
        low_corr_coins = [
            {
                "id": "low1",
                "name": "LowCorr1",
                "symbol": "LC1",
                "enhanced": {"btc_correlation": 0.50},
                "ai_recommendation": {
                    "position_sizing": {"pct_of_account": 12.5},
                },
            },
            {
                "id": "low2",
                "name": "LowCorr2",
                "symbol": "LC2",
                "enhanced": {"btc_correlation": 0.30},
                "ai_recommendation": {
                    "position_sizing": {"pct_of_account": 12.5},
                },
            },
        ]
        coins_with_recommendations = high_corr_coins + low_corr_coins
        open_positions = []

        summary = get_portfolio_risk_summary(
            open_positions, coins_with_recommendations, account_size
        )

        assert summary["total_risk_pct"] == 50.0
        assert summary["correlated_risk_pct"] == 25.0
        assert summary["max_correlated_pct"] == 50.0  # 25/50 * 100

        # f = (0.35 * non_corr) / (0.65 * correlated_risk_pct) = (0.35*25)/(0.65*25) = 0.35/0.65
        expected_f = (CORRELATION_CAP * 25) / ((1 - CORRELATION_CAP) * 25)
        assert 0 < expected_f < 1

        adjustment_factors = summary["adjustment_factors"]
        assert adjustment_factors["high1"] == pytest.approx(expected_f, rel=1e-5)
        assert adjustment_factors["high2"] == pytest.approx(expected_f, rel=1e-5)
        # When over cap, only high-corr coins get an adjustment factor; low-corr are not in the dict

        # Effective correlated share after applying f: (f * 25) / (25 - 25 + f*25) = f; we want f*corr <= 0.35 * new_total
        # new_total = 25 + f*25 = 25*(1+f), so we need f*25 <= 0.35 * 25*(1+f) => f <= 0.35*(1+f) => f <= 0.35/0.65
        # So effective correlated share = (f*25) / (25 + f*25) = f/(1+f). With f = 0.35/0.65, f/(1+f) = 0.35
        effective_corr = (expected_f * 25) / (25 + expected_f * 25)
        assert effective_corr <= CORRELATION_CAP + 1e-6


class TestHistoricalVaR:
    """Historical VaR uses 5th percentile of per-trade returns, scaled to 1-day."""

    def test_historical_var_uses_percentile(self):
        """20+ returns: VaR is 5th percentile scaled to 1-day and portfolio_value."""
        portfolio_value = 10_000.0
        confidence = 0.95
        # 20 returns: 5th percentile index = int((1-0.95)*20) = 1 (0-based). Sorted, 2nd element.
        returns_pct = [10.0, -3.0, 2.0, 5.0, -1.0, 4.0, 3.0, -2.0, 1.0, 6.0,
                       -4.0, 0.0, 7.0, -5.0, 8.0, 9.0, -6.0, 11.0, 12.0, -7.0]
        assert len(returns_pct) >= 20

        var_usd = _historical_var_usd(returns_pct, portfolio_value, confidence)

        sorted_ret = sorted(returns_pct)
        idx = int((1 - confidence) * len(sorted_ret))
        idx = max(0, min(idx, len(sorted_ret) - 1))
        worst_pct = sorted_ret[idx]
        daily_equiv = worst_pct / math.sqrt(7) if worst_pct != 0 else 0
        expected_var_usd = abs(portfolio_value * (daily_equiv / 100))

        assert var_usd == pytest.approx(expected_var_usd, rel=1e-9)
        assert var_usd >= 0

    def test_historical_var_returns_zero_with_few_returns(self):
        """With < 20 returns, historical VaR returns 0."""
        var_usd = _historical_var_usd(
            [1.0] * 10, 10_000.0, 0.95
        )
        assert var_usd == 0.0


class TestApplyCorrelationAdjustment:
    """apply_correlation_adjustment scales size/risk by factor; max_loss_pct unchanged."""

    def test_apply_correlation_adjustment_scales_size_and_risk(self):
        """Factor 0.5 → position_size_usd and risk_usd halved; max_loss_pct unchanged."""
        position_sizing = {
            "position_size_usd": 1000,
            "quantity": 0.5,
            "risk_usd": 50,
            "pct_of_account": 5.0,
            "max_loss_pct": 2.5,
        }
        factor = 0.5

        out = apply_correlation_adjustment(position_sizing, factor)

        assert out["position_size_usd"] == 500
        assert out["quantity"] == 0.25
        assert out["risk_usd"] == 25
        assert out["pct_of_account"] == 2.5
        assert out["max_loss_pct"] == 2.5
        assert out["correlation_factor"] == 0.5
        assert "correlation_note" in out
        assert str(BTC_CORR_THRESHOLD) in out["correlation_note"]
