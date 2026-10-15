# tests/test_performance_analytics.py — Sharpe, Sortino, Max Drawdown, expectancy, win rate by regime
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from performance_analytics import (
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    max_drawdown,
    win_rate_by_regime,
    win_rate_by_regime_scenario,
    expectancy,
    PerformanceAnalytics,
    calculate_kelly_fraction,
)


class TestSharpeRatio:
    """Test Sharpe ratio calculation."""

    def test_sharpe_empty_returns_zero(self):
        """Empty returns list yields 0."""
        assert calculate_sharpe_ratio([]) == 0.0

    def test_sharpe_positive_returns_positive_ratio(self):
        """Consistently positive returns yield positive Sharpe."""
        returns = [0.5, 0.3, 0.4, 0.2, 0.6]
        sharpe = calculate_sharpe_ratio(returns)
        assert sharpe > 0

    def test_sharpe_zero_volatility_returns_zero(self):
        """Constant returns (zero std) yield 0 Sharpe."""
        returns = [0.1, 0.1, 0.1, 0.1]
        sharpe = calculate_sharpe_ratio(returns)
        assert sharpe == 0.0

    def test_sharpe_negative_returns_negative_ratio(self):
        """Consistently negative returns yield negative Sharpe."""
        returns = [-0.5, -0.3, -0.4]
        sharpe = calculate_sharpe_ratio(returns)
        assert sharpe < 0


class TestSortinoRatio:
    """Test Sortino ratio (downside deviation)."""

    def test_sortino_empty_returns_zero(self):
        """Empty returns yields 0."""
        assert calculate_sortino_ratio([]) == 0.0

    def test_sortino_no_negative_returns_high_value(self):
        """All positive returns yield high Sortino (no downside)."""
        returns = [0.5, 0.3, 0.4]
        sortino = calculate_sortino_ratio(returns)
        assert sortino >= 0
        assert sortino > 1 or sortino == 10.0  # 10.0 is cap when no downside

    def test_sortino_mixed_returns(self):
        """Mixed returns produce finite Sortino."""
        returns = [0.5, -0.2, 0.3, -0.1, 0.4]
        sortino = calculate_sortino_ratio(returns)
        assert isinstance(sortino, (int, float))


class TestMaxDrawdown:
    """Test max drawdown from equity curve."""

    def test_max_drawdown_empty_returns_zero(self):
        """Empty or single-point curve yields 0."""
        assert max_drawdown([]) == {'max_drawdown_pct': 0.0, 'max_drawdown_duration': 0}
        assert max_drawdown([100]) == {'max_drawdown_pct': 0.0, 'max_drawdown_duration': 0}

    def test_max_drawdown_peak_then_drop(self):
        """Peak then drop computes drawdown correctly."""
        equity = [100, 120, 90, 80, 95]
        dd = max_drawdown(equity)
        # Peak 120, trough 80 -> (120-80)/120 = 33.33%
        assert dd['max_drawdown_pct'] == 33.33
        assert dd['max_drawdown_duration'] == 0

    def test_max_drawdown_no_drawdown(self):
        """Monotonically increasing equity has 0 drawdown."""
        equity = [100, 110, 120, 130]
        dd = max_drawdown(equity)
        assert dd['max_drawdown_pct'] == 0.0


class TestExpectancy:
    """Test expectancy (win_rate * avg_win - loss_rate * avg_loss)."""

    def test_expectancy_empty_positions(self):
        """Empty closed positions yields 0 expectancy."""
        assert expectancy([]) == {'expectancy_pct': 0.0, 'expectancy_usd': 0.0}

    def test_expectancy_positive_from_wins(self):
        """More wins than losses yields positive expectancy."""
        positions = [
            {'status': 'CLOSED', 'outcome': 'win', 'pnl_pct': 5.0, 'pnl_usd': 100},
            {'status': 'CLOSED', 'outcome': 'win', 'pnl_pct': 3.0, 'pnl_usd': 60},
            {'status': 'CLOSED', 'outcome': 'loss', 'pnl_pct': -2.0, 'pnl_usd': -40},
        ]
        exp = expectancy(positions)
        assert exp['expectancy_pct'] > 0
        assert exp['expectancy_usd'] > 0

    def test_expectancy_negative_from_losses(self):
        """More/larger losses yields negative expectancy."""
        positions = [
            {'status': 'CLOSED', 'outcome': 'win', 'pnl_pct': 2.0, 'pnl_usd': 20},
            {'status': 'CLOSED', 'outcome': 'loss', 'pnl_pct': -5.0, 'pnl_usd': -50},
            {'status': 'CLOSED', 'outcome': 'loss', 'pnl_pct': -4.0, 'pnl_usd': -40},
        ]
        exp = expectancy(positions)
        assert exp['expectancy_pct'] < 0
        assert exp['expectancy_usd'] < 0


class TestWinRateByRegime:
    """Test win rate by market regime."""

    def test_win_rate_by_regime_groups_correctly(self):
        """Positions grouped by market_regime with win rate."""
        positions = [
            {'status': 'CLOSED', 'market_regime': 'BULL', 'outcome': 'win', 'pnl_pct': 5.0},
            {'status': 'CLOSED', 'market_regime': 'BULL', 'outcome': 'loss', 'pnl_pct': -2.0},
            {'status': 'CLOSED', 'market_regime': 'BEAR', 'outcome': 'loss', 'pnl_pct': -3.0},
        ]
        by_regime = win_rate_by_regime(positions)
        assert 'BULL' in by_regime
        assert by_regime['BULL']['trades'] == 2
        assert by_regime['BULL']['win_rate_pct'] == 50.0
        assert 'BEAR' in by_regime
        assert by_regime['BEAR']['trades'] == 1
        assert by_regime['BEAR']['win_rate_pct'] == 0.0

    def test_win_rate_unknown_regime(self):
        """Missing regime defaults to UNKNOWN."""
        positions = [
            {'status': 'CLOSED', 'outcome': 'win', 'pnl_pct': 4.0},
        ]
        by_regime = win_rate_by_regime(positions)
        assert 'UNKNOWN' in by_regime
        assert by_regime['UNKNOWN']['win_rate_pct'] == 100.0


class TestWinRateByRegimeScenario:
    """Test win rate by regime + trading scenario (quant path / sniper)."""

    def test_win_rate_by_regime_scenario_groups_correctly(self):
        """Positions grouped by regime · scenario with win rate."""
        positions = [
            {'status': 'CLOSED', 'market_regime': 'BULL', 'quant_path': 'default', 'outcome': 'win', 'pnl_pct': 5.0},
            {'status': 'CLOSED', 'market_regime': 'BULL', 'quant_path': 'default', 'outcome': 'loss', 'pnl_pct': -2.0},
            {'status': 'CLOSED', 'market_regime': 'BEAR', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 4.0},
        ]
        by_rs = win_rate_by_regime_scenario(positions)
        assert 'BULL · default' in by_rs
        assert by_rs['BULL · default']['trades'] == 2
        assert by_rs['BULL · default']['win_rate_pct'] == 50.0
        assert 'BEAR · dip' in by_rs
        assert by_rs['BEAR · dip']['trades'] == 1
        assert by_rs['BEAR · dip']['win_rate_pct'] == 100.0

    def test_win_rate_by_regime_scenario_uses_trading_scenario_when_present(self):
        """When trading_scenario is set (e.g. sniper), it is used instead of quant_path."""
        positions = [
            {'status': 'CLOSED', 'market_regime': 'BULL', 'trading_scenario': 'sniper', 'quant_path': 'dip', 'outcome': 'win', 'pnl_pct': 3.0},
        ]
        by_rs = win_rate_by_regime_scenario(positions)
        assert 'BULL · sniper' in by_rs
        assert by_rs['BULL · sniper']['trades'] == 1

    def test_win_rate_by_regime_scenario_unknown_default(self):
        """Missing regime/scenario defaults to UNKNOWN · default."""
        positions = [
            {'status': 'CLOSED', 'outcome': 'win', 'pnl_pct': 4.0},
        ]
        by_rs = win_rate_by_regime_scenario(positions)
        assert 'UNKNOWN · default' in by_rs
        assert by_rs['UNKNOWN · default']['win_rate_pct'] == 100.0


class TestPerformanceAnalytics:
    """Test aggregate PerformanceAnalytics.from_positions."""

    def test_from_positions_empty(self):
        """Empty positions yields zeros."""
        result = PerformanceAnalytics.from_positions([], account_size=20000)
        assert result['sharpe_ratio'] == 0.0
        assert result['sortino_ratio'] == 0.0
        assert result['max_drawdown_pct'] == 0.0
        assert result['trades'] == 0
        assert result['win_rate_by_regime'] == {}
        assert result['win_rate_by_regime_scenario'] == {}

    def test_from_positions_full_metrics(self):
        """Closed positions produce full analytics."""
        positions = [
            {'status': 'CLOSED', 'pnl_pct': 5.0, 'pnl_usd': 100, 'market_regime': 'BULL', 'quant_path': 'default', 'outcome': 'win'},
            {'status': 'CLOSED', 'pnl_pct': -2.0, 'pnl_usd': -40, 'market_regime': 'BULL', 'quant_path': 'default', 'outcome': 'loss'},
        ]
        result = PerformanceAnalytics.from_positions(positions, account_size=20000)
        assert result['trades'] == 2
        assert 'sharpe_ratio' in result
        assert 'sortino_ratio' in result
        assert 'max_drawdown_pct' in result
        assert 'expectancy_pct' in result
        assert 'expectancy_usd' in result
        assert 'win_rate_by_regime' in result
        assert 'BULL' in result['win_rate_by_regime']
        assert 'win_rate_by_regime_scenario' in result
        assert 'BULL · default' in result['win_rate_by_regime_scenario']


class TestKellyFraction:
    """Test Kelly criterion from trade history."""

    def test_kelly_insufficient_trades_returns_conservative(self):
        """Fewer than 20 trades returns 0.01 (conservative)."""
        positions = [{'status': 'CLOSED', 'pnl_usd': 10, 'pnl_pct': 2}] * 10
        kelly = calculate_kelly_fraction(positions)
        assert kelly == 0.01

    def test_kelly_enough_trades_returns_fraction(self):
        """20+ trades with wins/losses yields fractional Kelly."""
        wins = [{'status': 'CLOSED', 'pnl_usd': 50, 'pnl_pct': 5.0}] * 12
        losses = [{'status': 'CLOSED', 'pnl_usd': -30, 'pnl_pct': -3.0}] * 8
        positions = wins + losses
        kelly = calculate_kelly_fraction(positions)
        assert 0.01 <= kelly <= 0.05

    def test_kelly_formula_exact(self):
        """Kelly = (wr*avg_win - (1-wr)*avg_loss) / avg_win; 25% frac, capped 0.01-0.05."""
        wins = [{'status': 'CLOSED', 'pnl_usd': 1, 'pnl_pct': 10.0}] * 14
        losses = [{'status': 'CLOSED', 'pnl_usd': -1, 'pnl_pct': -5.0}] * 6
        positions = wins + losses
        kelly = calculate_kelly_fraction(positions)
        wr = 14 / 20
        full_kelly = (wr * 10 - (1 - wr) * 5) / 10
        expected_frac = min(max(full_kelly * 0.25, 0.01), 0.05)
        assert abs(kelly - round(expected_frac, 4)) < 0.0001


class TestSharpePeriodsPerYear:
    """Sharpe annualization uses periods_per_year correctly."""

    def test_sharpe_daily_vs_weekly_annualization(self):
        """Same return series: 52 periods/year gives lower Sharpe than 252 (fewer periods)."""
        returns = [0.1, -0.05, 0.2, 0.0, 0.15]
        sharpe_252 = calculate_sharpe_ratio(returns, periods_per_year=252)
        sharpe_52 = calculate_sharpe_ratio(returns, periods_per_year=52)
        assert sharpe_52 < sharpe_252

    def test_sharpe_zero_risk_free_in_percent(self):
        """With risk_free=0, Sharpe = mean/std * sqrt(periods). Implementation uses population variance (n)."""
        returns = [1.0, 2.0, 1.5]
        sharpe = calculate_sharpe_ratio(returns, risk_free_rate_annual=0.0, periods_per_year=4)
        n = len(returns)
        mean_r = sum(returns) / n
        variance = sum((r - mean_r) ** 2 for r in returns) / n
        std_r = variance ** 0.5 if variance > 0 else 0
        expected = (mean_r / std_r) * (4 ** 0.5) if std_r > 0 else 0
        assert abs(sharpe - round(expected, 2)) < 0.02
