# portfolio_manager.py — Correlation-aware portfolio risk and hedging
# Never allocate more than 35% of total risk to coins with BTC correlation > 0.85
# + Concentration limit (5% per position), total exposure check, VaR (historical simulation)
"""
Constants and VaR formulas (verify only; do not change constants here):
- CORRELATION_CAP = 0.35: max fraction of total risk from high-BTC-correlation coins.
- BTC_CORR_THRESHOLD = 0.85: exposures with btc_correlation > 0.85 count as correlated.
- VaR:
  - Historical: 5th percentile of per-trade pnl_pct returns; scaled to 1-day by 1/sqrt(7);
    used only when len(returns_pct) >= 20.
  - Parametric: vol_daily = (15/100)/sqrt(252), var_1d_pct = vol_daily * 1.65 * 100,
    var_usd = portfolio_value * (var_1d_pct/100).
"""

from typing import Dict, List, Any, Optional
import math

from execution_engine import get_max_total_risk_pct

CORRELATION_CAP = 0.35   # Max fraction of total risk from high-BTC-correlation coins
BTC_CORR_THRESHOLD = 0.85
CONCENTRATION_LIMIT_PCT = 5.0   # Default per-position limit; small accounts use get_concentration_limit_pct()
MAX_TOTAL_EXPOSURE_PCT = 100.0  # Total exposure cap (100% = all capital)


def get_concentration_limit_pct(account_size: float) -> float:
    """Per-position concentration limit; scales with account size so small accounts can hold positions."""
    if account_size <= 0:
        return CONCENTRATION_LIMIT_PCT
    total_cap = get_max_total_risk_pct(account_size)
    # Allow at least 3 positions: per-position up to total_cap/3, but not less than 5%
    return max(CONCENTRATION_LIMIT_PCT, round(total_cap / 3.0, 1))
VAR_CONFIDENCE = 0.95   # 95% VaR
MIN_TRADES_FOR_HISTORICAL_VAR = 20   # Use historical VaR when >= this many closed trades


def _get_btc_correlation(coin: Dict[str, Any]) -> float:
    """Extract BTC correlation from coin (enhanced or 0)."""
    enh = coin.get('enhanced') or {}
    return float(enh.get('btc_correlation', 0) or 0)


def _exposure_pct_from_position(position: Dict[str, Any], account_size: float) -> float:
    """Risk exposure as % of account from an open position (capital-based proxy)."""
    if account_size <= 0:
        return 0.0
    capital = float(position.get('capital_usd', 0) or 0)
    if capital <= 0:
        entry = float(position.get('entry_price', 0) or 0)
        qty = float(position.get('quantity', 0) or 0)
        capital = entry * qty
    return (capital / account_size) * 100.0


def _historical_var_usd(returns_pct: List[float], portfolio_value: float, confidence: float = 0.95) -> float:
    """
    Historical simulation VaR: 5th percentile of daily-equivalent returns.
    returns_pct = per-trade pnl_pct from closed positions.
    Converts to 1-day equivalent (divide by sqrt(trades)) and applies percentile.
    """
    if not returns_pct or len(returns_pct) < MIN_TRADES_FOR_HISTORICAL_VAR:
        return 0.0
    sorted_ret = sorted(returns_pct)
    idx = int((1 - confidence) * len(sorted_ret))
    idx = max(0, min(idx, len(sorted_ret) - 1))
    worst_pct = sorted_ret[idx]
    # Scale to 1-day: assume ~7-day hold, so 1-day vol ~ ret/sqrt(7)
    daily_equiv = worst_pct / math.sqrt(7) if worst_pct != 0 else 0
    return abs(portfolio_value * (daily_equiv / 100))


def get_portfolio_risk_summary(
    open_positions: List[Dict[str, Any]],
    coins_with_recommendations: List[Dict[str, Any]],
    account_size: float,
    closed_positions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Compute total risk exposure, correlated exposure, and hedging suggestions.
    open_positions: from trade_journal.get_open_positions()
    coins_with_recommendations: coins that have ai_recommendation.position_sizing (e.g. agreed BUY coins)
    account_size: from position_sizer / env.
    closed_positions: optional, for historical VaR. When provided with >=20 trades, uses historical simulation.
    """
    if account_size <= 0:
        return {
            'total_risk_pct': 0, 'open_exposure_pct': 0, 'correlated_risk_pct': 0, 'max_correlated_pct': 0,
            'concentration_limit_pct': CONCENTRATION_LIMIT_PCT, 'max_single_position_pct': 0,
            'total_exposure_ok': True, 'var_1d_usd': 0, 'var_1d_pct': 0, 'var_method': 'none',
            'suggestions': [], 'high_corr_count': 0, 'adjustment_factors': {},
            'concentration_breaches': [], 'exposures': [],
        }

    # Build coin_id -> btc_correlation and name/symbol from coins_with_recommendations
    coin_by_id: Dict[str, Dict[str, Any]] = {}
    for c in coins_with_recommendations:
        cid = c.get('id') or c.get('coin_id') or ''
        if cid:
            coin_by_id[cid] = {
                'btc_correlation': _get_btc_correlation(c),
                'name': c.get('name', ''),
                'symbol': (c.get('symbol') or '').upper(),
            }

    exposures: List[Dict[str, Any]] = []  # { coin_id, symbol/name, risk_pct, btc_correlation, is_open }

    # Open positions
    for p in open_positions:
        cid = p.get('coin_id', '')
        risk_pct = _exposure_pct_from_position(p, account_size)
        if risk_pct <= 0:
            continue
        info = coin_by_id.get(cid, {'btc_correlation': 0, 'name': p.get('coin_name', ''), 'symbol': (p.get('coin_symbol') or '').upper()})
        exposures.append({
            'coin_id': cid,
            'name': info.get('name', p.get('coin_name', '')),
            'symbol': info.get('symbol', (p.get('coin_symbol') or '').upper()),
            'risk_pct': risk_pct,
            'btc_correlation': info.get('btc_correlation', 0),
            'is_open': True,
        })

    # New recommendations
    for c in coins_with_recommendations:
        rec = c.get('ai_recommendation') or {}
        pos = rec.get('position_sizing') or {}
        pct = pos.get('pct_of_account')
        if pct is None:
            pos_usd = pos.get('position_size_usd')
            if pos_usd and account_size > 0:
                pct = (float(pos_usd) / account_size) * 100
            else:
                continue
        pct = float(pct)
        if pct <= 0:
            continue
        cid = c.get('id', '')
        btc_corr = _get_btc_correlation(c)
        exposures.append({
            'coin_id': cid,
            'name': c.get('name', ''),
            'symbol': (c.get('symbol') or '').upper(),
            'risk_pct': pct,
            'btc_correlation': btc_corr,
            'is_open': False,
        })

    total_risk_pct = sum(e['risk_pct'] for e in exposures)
    open_exposure_pct = sum(e['risk_pct'] for e in exposures if e.get('is_open'))
    high_corr = [e for e in exposures if (e.get('btc_correlation') or 0) > BTC_CORR_THRESHOLD]
    correlated_risk_pct = sum(e['risk_pct'] for e in high_corr)
    max_correlated_pct = (correlated_risk_pct / total_risk_pct * 100) if total_risk_pct > 0 else 0

    # Concentration limit: scales with account size (higher for small accounts)
    concentration_limit_pct = get_concentration_limit_pct(account_size)
    concentration_breaches = [e for e in exposures if (e.get('risk_pct') or 0) > concentration_limit_pct]
    max_single_position_pct = max((e.get('risk_pct') or 0) for e in exposures) if exposures else 0

    # Total exposure check
    total_exposure_ok = total_risk_pct <= MAX_TOTAL_EXPOSURE_PCT

    # VaR: historical simulation when enough closed trades, else parametric fallback
    portfolio_value = account_size
    var_1d_usd = 0.0
    var_1d_pct = 0.0
    var_method = 'parametric'

    if closed_positions:
        returns_pct = []
        for p in closed_positions:
            if p.get('status') == 'CLOSED' and p.get('pnl_pct') is not None:
                try:
                    val = float(p['pnl_pct'])
                    if not math.isnan(val) and not math.isinf(val):
                        returns_pct.append(val)
                except (TypeError, ValueError):
                    pass  # Skip non-numeric pnl_pct
        if len(returns_pct) >= MIN_TRADES_FOR_HISTORICAL_VAR:
            var_1d_usd = _historical_var_usd(returns_pct, portfolio_value, VAR_CONFIDENCE)
            var_1d_pct = (var_1d_usd / portfolio_value * 100) if portfolio_value > 0 else 0
            var_method = 'historical'

    # Guard: ensure VaR values are finite
    if math.isnan(var_1d_usd) or math.isinf(var_1d_usd):
        var_1d_usd = 0.0
        var_1d_pct = 0.0
        var_method = 'parametric'  # Fall through to parametric

    if var_method == 'parametric':
        # Fallback: parametric VaR (assume portfolio vol ~15% annual, 1-day)
        vol_annual_pct = 15.0
        vol_daily = (vol_annual_pct / 100) / math.sqrt(252)
        var_1d_pct = vol_daily * 1.65 * 100
        var_1d_usd = portfolio_value * (var_1d_pct / 100)

    suggestions: List[str] = []
    adjustment_factors: Dict[str, float] = {}

    if total_risk_pct > 0 and correlated_risk_pct > CORRELATION_CAP * total_risk_pct:
        # Need to scale down high-correlation exposure so correlated <= 35% of total
        # new_correlated = f * correlated_risk_pct, new_total = total_risk_pct - correlated_risk_pct + f*correlated_risk_pct
        # Require: f * correlated_risk_pct <= 0.35 * (total_risk_pct - correlated_risk_pct + f*correlated_risk_pct)
        # f * corr <= 0.35 * (total - corr + f*corr)  =>  f*corr*(1-0.35) <= 0.35*(total-corr)  =>  f <= 0.35*(total-corr)/(0.65*corr)
        non_corr = total_risk_pct - correlated_risk_pct
        f = (CORRELATION_CAP * non_corr) / ((1 - CORRELATION_CAP) * correlated_risk_pct) if correlated_risk_pct > 0 else 1.0
        f = max(0.0, min(1.0, f))
        for e in high_corr:
            cid = e.get('coin_id') or ''
            adjustment_factors[cid] = f
            if not e.get('is_open'):
                pct_reduce = (1 - f) * 100
                suggestions.append(f"Reduce {e.get('name', e.get('symbol', cid))} size by {pct_reduce:.0f}% due to high BTC corr")
    else:
        for e in exposures:
            adjustment_factors[e.get('coin_id', '')] = 1.0

    # Concentration limit suggestions
    for e in concentration_breaches:
        if not e.get('is_open'):
            suggestions.append(
                f"Reduce {e.get('name', e.get('symbol', ''))} — exceeds {concentration_limit_pct}% concentration limit (current {e.get('risk_pct', 0):.1f}%)"
            )

    risk_cap_pct = get_max_total_risk_pct(account_size)

    return {
        'total_risk_pct': round(total_risk_pct, 2),
        'open_exposure_pct': round(open_exposure_pct, 2),
        'correlated_risk_pct': round(correlated_risk_pct, 2),
        'max_correlated_pct': round(max_correlated_pct, 1),
        'correlation_cap_pct': CORRELATION_CAP * 100,
        'risk_cap_pct': risk_cap_pct,
        'suggestions': suggestions,
        'adjustment_factors': adjustment_factors,
        'high_corr_count': len(high_corr),
        'concentration_limit_pct': concentration_limit_pct,
        'max_single_position_pct': round(max_single_position_pct, 1),
        'concentration_breaches': len(concentration_breaches),
        'total_exposure_ok': total_exposure_ok,
        'var_1d_pct': round(var_1d_pct, 2),
        'var_1d_usd': round(var_1d_usd, 2),
        'var_confidence': VAR_CONFIDENCE,
        'var_method': var_method,
    }


def get_correlation_adjustment_factors(
    open_positions: List[Dict[str, Any]],
    coins_with_recommendations: List[Dict[str, Any]],
    account_size: float,
) -> Dict[str, float]:
    """Return coin_id -> scale factor (1.0 or less for high-BTC-correlation coins)."""
    summary = get_portfolio_risk_summary(open_positions, coins_with_recommendations, account_size)
    return summary.get('adjustment_factors', {})


def apply_correlation_adjustment(
    position_sizing: Dict[str, Any],
    factor: float,
) -> Dict[str, Any]:
    """Apply correlation scale factor to a position_sizing dict; return new dict with adjusted values and note."""
    if factor >= 1.0:
        out = dict(position_sizing)
        out['correlation_factor'] = 1.0
        return out
    out = {}
    for k, v in position_sizing.items():
        if k == 'position_size_usd' and isinstance(v, (int, float)):
            out[k] = round(float(v) * factor, 2)
        elif k == 'quantity' and isinstance(v, (int, float)):
            out[k] = round(float(v) * factor, 8)
        elif k == 'risk_usd' and isinstance(v, (int, float)):
            out[k] = round(float(v) * factor, 2)
        elif k == 'pct_of_account' and isinstance(v, (int, float)):
            out[k] = round(float(v) * factor, 1)
        elif k == 'max_loss_pct':
            # Do not scale: stop-loss % is per-trade risk; we only reduce position size
            out[k] = v
        elif k == 'breakdown' and isinstance(v, dict):
            out[k] = dict(v)
        else:
            out[k] = v
    out['correlation_factor'] = round(factor, 3)
    out['correlation_note'] = f'Size reduced by {(1 - factor) * 100:.0f}% for correlation hedging (BTC corr > {BTC_CORR_THRESHOLD})'
    return out
