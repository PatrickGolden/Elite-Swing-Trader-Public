# performance_analytics.py — Institutional-grade performance metrics (zero cost)
# Sharpe, Sortino, max drawdown, win rate by regime, expectancy, Kelly
#
# UNITS: All return inputs in PERCENT (e.g. 5.0 = 5%). risk_free_rate_annual in decimal (0.05 = 5%).
# periods_per_year: 252 for daily, 52 for weekly/7d or per-trade (~1/week). Affects annualization only.
# Journal positions: pnl_pct (%), pnl_usd (USD), outcome 'win'|'loss' by pnl_usd sign.
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import math


def calculate_sharpe_ratio(
    returns: List[float],
    risk_free_rate_annual: float = 0.05,
    periods_per_year: float = 252.0,
) -> float:
    """
    Annualized Sharpe ratio.
    returns: list of period returns in PERCENT (e.g. 5.0 for 5%, per-trade pnl_pct or outcome_7d).
    risk_free_rate_annual: in decimal (0.05 = 5%).
    periods_per_year: 252 for daily, 52 for weekly/7d outcomes, ~52 for per-trade if ~1 trade/week.
    """
    if not returns:
        return 0.0
    n = len(returns)
    mean_ret = sum(returns) / n
    variance = sum((r - mean_ret) ** 2 for r in returns) / n if n > 1 else 0
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return 0.0
    rf_per_period_pct = risk_free_rate_annual * 100.0 / periods_per_year  # risk-free in percent per period
    sharpe = (mean_ret - rf_per_period_pct) / std
    return round(sharpe * math.sqrt(periods_per_year), 2)


def calculate_sortino_ratio(
    returns: List[float],
    risk_free_rate_annual: float = 0.05,
    periods_per_year: float = 252.0,
) -> float:
    """Sortino ratio: uses downside deviation (only negative returns). returns in percent; periods_per_year as in Sharpe."""
    if not returns:
        return 0.0
    n = len(returns)
    mean_ret = sum(returns) / n
    downside = [r for r in returns if r < 0]
    if not downside:
        return 10.0 if mean_ret > 0 else 0.0
    downside_var = sum(r ** 2 for r in downside) / len(downside)
    downside_std = math.sqrt(downside_var)
    if downside_std == 0:
        return 0.0
    rf_per_period_pct = risk_free_rate_annual * 100.0 / periods_per_year
    sortino = (mean_ret - rf_per_period_pct) / downside_std
    return round(sortino * math.sqrt(periods_per_year), 2)


def max_drawdown(equity_curve: List[float]) -> Dict[str, float]:
    """
    Max drawdown from equity curve (cumulative returns or portfolio values).
    Returns: {'max_drawdown_pct': float, 'max_drawdown_duration': int (periods)}
    """
    if not equity_curve or len(equity_curve) < 2:
        return {'max_drawdown_pct': 0.0, 'max_drawdown_duration': 0}
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return {'max_drawdown_pct': round(max_dd, 2), 'max_drawdown_duration': 0}


def win_rate_by_regime(positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Win rate, avg win/loss, count by market_regime from closed positions."""
    closed = [p for p in positions if p.get('status') == 'CLOSED']
    by_regime: Dict[str, List[Dict]] = {}
    for p in closed:
        regime = p.get('market_regime') or 'UNKNOWN'
        by_regime.setdefault(regime, []).append(p)
    out = {}
    for regime, trades in by_regime.items():
        wins = [t for t in trades if t.get('outcome') == 'win']
        losses = [t for t in trades if t.get('outcome') == 'loss']
        pnl_pcts = [t['pnl_pct'] for t in trades if t.get('pnl_pct') is not None]
        win_pcts = [t['pnl_pct'] for t in wins if t.get('pnl_pct') is not None]
        loss_pcts = [t['pnl_pct'] for t in losses if t.get('pnl_pct') is not None]
        wr = len(wins) / len(trades) * 100 if trades else 0
        avg_win = sum(win_pcts) / len(win_pcts) if win_pcts else 0
        avg_loss = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0
        out[regime] = {
            'win_rate_pct': round(wr, 1),
            'trades': len(trades),
            'avg_win_pct': round(avg_win, 2),
            'avg_loss_pct': round(avg_loss, 2),
            'total_pnl_pct': round(sum(pnl_pcts), 2) if pnl_pcts else 0,
        }
    return out


def win_rate_by_regime_scenario(positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Win rate, avg win/loss, count by (market_regime, trading_scenario/quant_path) from closed positions.
    Keys are composite labels e.g. 'BULL · default', 'BEAR · dip', 'BULL · sniper'.
    """
    closed = [p for p in positions if p.get('status') == 'CLOSED']
    by_key: Dict[str, List[Dict]] = {}
    for p in closed:
        regime = p.get('market_regime') or 'UNKNOWN'
        scenario = p.get('trading_scenario') or p.get('quant_path') or 'default'
        key = f"{regime} · {scenario}"
        by_key.setdefault(key, []).append(p)
    out = {}
    for key, trades in by_key.items():
        wins = [t for t in trades if t.get('outcome') == 'win']
        losses = [t for t in trades if t.get('outcome') == 'loss']
        pnl_pcts = [t['pnl_pct'] for t in trades if t.get('pnl_pct') is not None]
        win_pcts = [t['pnl_pct'] for t in wins if t.get('pnl_pct') is not None]
        loss_pcts = [t['pnl_pct'] for t in losses if t.get('pnl_pct') is not None]
        wr = len(wins) / len(trades) * 100 if trades else 0
        avg_win = sum(win_pcts) / len(win_pcts) if win_pcts else 0
        avg_loss = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0
        out[key] = {
            'win_rate_pct': round(wr, 1),
            'trades': len(trades),
            'avg_win_pct': round(avg_win, 2),
            'avg_loss_pct': round(avg_loss, 2),
            'total_pnl_pct': round(sum(pnl_pcts), 2) if pnl_pcts else 0,
        }
    return out


def expectancy(positions: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Expectancy = (win_rate * avg_win) - (loss_rate * avg_loss).
    Returns expectancy per trade in % and USD.
    """
    closed = [p for p in positions if p.get('status') == 'CLOSED']
    if not closed:
        return {'expectancy_pct': 0.0, 'expectancy_usd': 0.0}
    wins = [p for p in closed if p.get('outcome') == 'win']
    losses = [p for p in closed if p.get('outcome') == 'loss']
    wr = len(wins) / len(closed)
    lr = len(losses) / len(closed)
    avg_win_pct = sum(p['pnl_pct'] for p in wins if p.get('pnl_pct') is not None) / len(wins) if wins else 0
    avg_loss_pct = sum(abs(p['pnl_pct']) for p in losses if p.get('pnl_pct') is not None) / len(losses) if losses else 0
    avg_win_usd = sum(p['pnl_usd'] for p in wins if p.get('pnl_usd') is not None) / len(wins) if wins else 0
    avg_loss_usd = sum(abs(p['pnl_usd']) for p in losses if p.get('pnl_usd') is not None) / len(losses) if losses else 0
    exp_pct = (wr * avg_win_pct) - (lr * avg_loss_pct)
    exp_usd = (wr * avg_win_usd) - (lr * avg_loss_usd)
    return {'expectancy_pct': round(exp_pct, 2), 'expectancy_usd': round(exp_usd, 2)}


class PerformanceAnalytics:
    """Aggregate performance metrics for journal and backtester."""

    @staticmethod
    def from_positions(positions: List[Dict[str, Any]], account_size: float = 0) -> Dict[str, Any]:
        """Compute full analytics from positions (journal format)."""
        closed = [p for p in positions if p.get('status') == 'CLOSED']
        if not closed:
            return {
                'sharpe_ratio': 0.0,
                'sortino_ratio': 0.0,
                'max_drawdown_pct': 0.0,
                'win_rate_by_regime': {},
                'win_rate_by_regime_scenario': {},
                'expectancy_pct': 0.0,
                'expectancy_usd': 0.0,
                'trades': 0,
            }
        returns_pct = [p['pnl_pct'] for p in closed if p.get('pnl_pct') is not None]
        equity = [account_size]
        for p in closed:
            pnl = p.get('pnl_usd') or 0
            equity.append(equity[-1] + pnl)
        dd = max_drawdown(equity)
        exp = expectancy(positions)
        # Per-trade returns: use ~52 periods/year for sensible annualization
        periods_per_year = 52.0
        return {
            'sharpe_ratio': calculate_sharpe_ratio(returns_pct, periods_per_year=periods_per_year),
            'sortino_ratio': calculate_sortino_ratio(returns_pct, periods_per_year=periods_per_year),
            'max_drawdown_pct': dd['max_drawdown_pct'],
            'win_rate_by_regime': win_rate_by_regime(positions),
            'win_rate_by_regime_scenario': win_rate_by_regime_scenario(positions),
            'expectancy_pct': exp['expectancy_pct'],
            'expectancy_usd': exp['expectancy_usd'],
            'trades': len(closed),
        }


def calculate_kelly_fraction(positions: List[Dict[str, Any]]) -> float:
    """
    Kelly criterion from historical trades (journal format).
    Formula: f* = (win_rate * avg_win_pct - (1 - win_rate) * avg_loss_pct) / avg_win_pct.
    Returns fractional Kelly (25% of f*) capped at 1%–5%. Conservative when <20 trades.
    pnl_pct in percent (e.g. 5.0 for 5%); wins/losses by pnl_usd sign.
    """
    closed = [p for p in positions if p.get('status') == 'CLOSED']
    if len(closed) < 20:
        return 0.01
    wins = [p for p in closed if float(p.get('pnl_usd', 0) or 0) > 0]
    losses = [p for p in closed if float(p.get('pnl_usd', 0) or 0) < 0]
    win_rate = len(wins) / len(closed)
    win_pcts = [abs(float(p.get('pnl_pct', 0) or 0)) for p in wins if p.get('pnl_pct') is not None]
    loss_pcts = [abs(float(p.get('pnl_pct', 0) or 0)) for p in losses if p.get('pnl_pct') is not None]
    avg_win_pct = sum(win_pcts) / len(win_pcts) if win_pcts else 0
    avg_loss_pct = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 1.0
    if avg_win_pct <= 0:
        return 0.01
    kelly = (win_rate * avg_win_pct - (1 - win_rate) * avg_loss_pct) / avg_win_pct
    fractional_kelly = kelly * 0.25
    return round(min(max(fractional_kelly, 0.01), 0.05), 4)
