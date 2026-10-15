# model_attribution.py — P&L contribution, win rate, Sharpe by model (Specialist/Quant)
# Monthly ranking and weight adjustment recommendations. Zero cost.
# Sharpe: uses per-trade pnl_pct returns → periods_per_year=52 for correct annualization.

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

ATTRIBUTION_FILE = os.path.join(os.path.dirname(__file__), 'model_attribution.json')


def _load() -> Dict[str, Any]:
    if not Path(ATTRIBUTION_FILE).exists():
        return {'trades': [], 'updated': None}
    try:
        with open(ATTRIBUTION_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {'trades': [], 'updated': None}


def _save(data: Dict[str, Any]) -> None:
    """Atomic save via safe_json to prevent corruption."""
    data['updated'] = datetime.now(timezone.utc).isoformat()
    import safe_json
    if not safe_json.save(ATTRIBUTION_FILE, data, default=str):
        logger.error("[model_attribution] CRITICAL: Failed to save %s", ATTRIBUTION_FILE)


def log_trade_for_attribution(
    coin_id: str,
    symbol: str,
    signal: str,
    pnl_pct: float,
    pnl_usd: float,
    outcome: str,
    regime: str,
    specialist_veto: Optional[str],
    quant_tier: str,
    quant_path: Optional[str] = None,
    confirmation_score: Optional[float] = None,
    fear_greed: Optional[float] = None,
    price_change_24h: Optional[float] = None,
    price_change_7d: Optional[float] = None,
    price_change_30d: Optional[float] = None,
    funding_rate: Optional[float] = None,
    edge_score: Optional[float] = None,
    ath_change_pct: Optional[float] = None,
    fdv_ratio: Optional[float] = None,
) -> None:
    """Log a closed trade for model attribution (call from journal close_position or similar).
    quant_path: e.g. blood_in_the_street, dip, pullback_in_trend, volatility_harvest, capitulation, rally, bull_participant, vol_contraction, momentum_breakout, early_momentum, default.
    confirmation_score: optional 0-100 conviction score from quant layer (final polish).
    fear_greed, price_change_24h/7d/30d, funding_rate, edge_score, ath_change_pct, fdv_ratio: for recursive optimizer counterfactual."""
    data = _load()
    trade = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'coin_id': coin_id,
        'symbol': symbol,
        'signal': signal,
        'pnl_pct': pnl_pct,
        'pnl_usd': pnl_usd,
        'outcome': outcome,
        'regime': regime,
        'specialist_veto': specialist_veto,
        'quant_tier': quant_tier,
        'quant_path': quant_path or 'default',
        'confirmation_score': confirmation_score,
    }
    if fear_greed is not None:
        trade['fear_greed'] = fear_greed
    if price_change_24h is not None:
        trade['price_change_24h'] = price_change_24h
    if price_change_7d is not None:
        trade['price_change_7d'] = price_change_7d
    if price_change_30d is not None:
        trade['price_change_30d'] = price_change_30d
    if funding_rate is not None:
        trade['funding_rate'] = funding_rate
    if edge_score is not None:
        trade['edge_score'] = edge_score
    if ath_change_pct is not None:
        trade['ath_change_pct'] = ath_change_pct
    if fdv_ratio is not None:
        trade['fdv_ratio'] = fdv_ratio
    data.setdefault('trades', []).append(trade)
    if len(data['trades']) > 500:
        dropped = len(data['trades']) - 500
        logger.info("[model_attribution] Truncating trade log: dropping %d oldest trades (keeping 500)", dropped)
        data['trades'] = data['trades'][-500:]
    _save(data)


def attribution_records_from_closed_positions(
    closed_positions: List[Dict[str, Any]], days: int
) -> List[Dict[str, Any]]:
    """Filter raw CLOSED journal rows into attribution records (exit window, system trades only). Pure; no I/O."""
    now = datetime.now(timezone.utc)
    cutoff_date = (now - timedelta(days=days)).strftime('%Y-%m-%d')
    out: List[Dict[str, Any]] = []
    for p in closed_positions:
        exit_time = (p.get('exit_time') or '')[:10]
        if not exit_time or exit_time < cutoff_date:
            continue
        if p.get('manual_override'):
            continue
        if p.get('outcome') not in ('win', 'loss'):
            continue
        rec = {
            'pnl_pct': p.get('pnl_pct'),
            'pnl_usd': p.get('pnl_usd', 0) or 0,
            'outcome': p.get('outcome'),
            'specialist_veto': p.get('specialist_veto'),
            'quant_tier': p.get('quant_tier') or '',
            'regime': (p.get('market_regime') or p.get('regime') or '').strip().upper() or 'UNKNOWN',
            'quant_path': (p.get('quant_path') or 'default').strip() or 'default',
            'exit_time': p.get('exit_time'),
            'ts': p.get('exit_time') or p.get('entry_time'),
            'confirmation_score': p.get('confirmation_score'),
            'fear_greed': p.get('fear_greed'),
            'price_change_24h': p.get('price_change_24h'),
            'price_change_7d': p.get('price_change_7d'),
            'price_change_30d': p.get('price_change_30d'),
            'funding_rate': p.get('funding_rate'),
            'edge_score': p.get('edge_score'),
            'ath_change_pct': p.get('ath_change_pct'),
            'fdv_ratio': p.get('fdv_ratio'),
            'coin_symbol': p.get('coin_symbol'),
            'coin_id': p.get('coin_id'),
            'signal_badges': p.get('signal_badges') or [],
        }
        if p.get('regression_features'):
            rec['regression_features'] = p['regression_features']
        out.append(rec)
    return out


def _get_closed_positions_from_journal(days: int) -> List[Dict[str, Any]]:
    """Load closed system trades from the trade journal (single source of truth). Returns list of position dicts."""
    try:
        from trade_journal import get_all_positions
    except ImportError:
        return []
    positions = get_all_positions(exclude_test=True)
    closed = [p for p in positions if (p.get('status') or '').upper() == 'CLOSED']
    return attribution_records_from_closed_positions(closed, days)


def get_model_attribution(
    days: int = 30,
    *,
    preloaded_closed_positions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    P&L contribution, win rate, Sharpe by model. Single source: trade journal (closed positions).
    Models: Specialist (CONFIRM vs VETO), Quant (tier).
    If preloaded_closed_positions is set, uses one journal snapshot (no second get_all_positions).
    """
    if preloaded_closed_positions is not None:
        trades = attribution_records_from_closed_positions(preloaded_closed_positions, days)
    else:
        trades = _get_closed_positions_from_journal(days)
    if not trades:
        return {
            'by_model': {},
            'by_regime_and_tier': {},
            'by_signal_badge': {},
            'monthly_ranking': [],
            'recommendations': [],
            'period_days': days,
            'veto_performance': {'when_CONFIRM': {'pnl_usd': 0, 'win_rate_pct': 0, 'sharpe': 0, 'trades': 0},
                               'when_VETO': {'pnl_usd': 0, 'win_rate_pct': 0, 'sharpe': 0, 'trades': 0}},
        }

    def _metrics(subset: List[Dict]) -> Dict:
        if not subset:
            return {'pnl_usd': 0, 'win_rate_pct': 0, 'sharpe': 0, 'trades': 0}
        rets = [t['pnl_pct'] for t in subset if t.get('pnl_pct') is not None]
        wins = sum(1 for t in subset if t.get('outcome') == 'win')
        pnl = sum(t.get('pnl_usd', 0) for t in subset)
        wr = wins / len(subset) * 100 if subset else 0
        from performance_analytics import calculate_sharpe_ratio
        sharpe = calculate_sharpe_ratio(rets, periods_per_year=52.0)  # per-trade returns
        return {'pnl_usd': round(pnl, 2), 'win_rate_pct': round(wr, 1), 'sharpe': sharpe, 'trades': len(subset)}

    specialist_confirm = [t for t in trades if t.get('specialist_veto') == 'CONFIRM']
    specialist_veto = [t for t in trades if t.get('specialist_veto') == 'VETO']
    quant_ultra = [t for t in trades if 'ULTRA' in (t.get('quant_tier') or '')]
    quant_strong = [t for t in trades if 'STRONG' in (t.get('quant_tier') or '')]

    by_model = {
        'Specialist (CONFIRM)': _metrics(specialist_confirm),
        'Specialist (VETO)': _metrics(specialist_veto),
        'Quant (ULTRA)': _metrics(quant_ultra),
        'Quant (STRONG)': _metrics(quant_strong),
    }

    # Regime x Tier cross-tab: same key shape as historical_replay._compute_summary
    # so backtest and live use identical dimensions for optimizer comparison.
    by_regime_and_tier: Dict[str, Dict] = {}
    for t in trades:
        regime = (t.get('regime') or 'UNKNOWN').upper()
        tier = t.get('quant_tier') or 'UNKNOWN'
        key = f"{regime}:{tier}"
        by_regime_and_tier.setdefault(key, []).append(t)
    by_regime_and_tier = {k: _metrics(v) for k, v in by_regime_and_tier.items()}

    # Signal-badge cross-tab: each trade can have multiple badges, so a trade
    # contributes to every badge bucket it carries. Empty-badge trades are
    # bucketed under '_none' so they don't disappear from totals.
    by_signal_badge: Dict[str, list] = {}
    for t in trades:
        badges = t.get('signal_badges') or []
        if not badges:
            by_signal_badge.setdefault('_none', []).append(t)
            continue
        for b in badges:
            if not isinstance(b, str) or not b:
                continue
            by_signal_badge.setdefault(b, []).append(t)
    by_signal_badge = {k: _metrics(v) for k, v in by_signal_badge.items()}

    ranking = sorted(
        [(k, v['pnl_usd'], v['win_rate_pct'], v['sharpe']) for k, v in by_model.items() if v['trades'] >= 3],
        key=lambda x: (x[1], x[2]),
        reverse=True,
    )
    monthly_ranking = [{'model': k, 'pnl_usd': p, 'win_rate_pct': w, 'sharpe': s} for k, p, w, s in ranking]

    recommendations = []
    for k, v in by_model.items():
        if v['trades'] >= 5 and v['win_rate_pct'] < 50 and v['pnl_usd'] < 0:
            recommendations.append(f"Consider reducing weight for {k} (win rate {v['win_rate_pct']}%, P&L ${v['pnl_usd']})")
        if v['trades'] >= 5 and v['win_rate_pct'] >= 70 and v['sharpe'] > 1.0:
            recommendations.append(f"Consider increasing weight for {k} (win rate {v['win_rate_pct']}%, Sharpe {v['sharpe']})")

    # NEW: Dynamic Probabilistic Calibration v1 — veto performance (when specialist VETO'd vs CONFIRM)
    veto_confirm_trades = [t for t in trades if t.get('specialist_veto') == 'CONFIRM']
    veto_veto_trades = [t for t in trades if t.get('specialist_veto') == 'VETO']
    veto_performance = {
        'when_CONFIRM': _metrics(veto_confirm_trades),
        'when_VETO': _metrics(veto_veto_trades),
    }
    # Recommendation: when we have vetoed-trade data (e.g. paper log), suggest threshold tweak
    if veto_performance['when_VETO']['trades'] >= 5 and veto_performance['when_VETO']['win_rate_pct'] >= 60:
        recommendations.append("Consider increasing veto threshold — vetoed setups are winning {:.1f}%.".format(veto_performance['when_VETO']['win_rate_pct']))

    return {
        'by_model': by_model,
        'by_regime_and_tier': by_regime_and_tier,
        'by_signal_badge': by_signal_badge,
        'monthly_ranking': monthly_ranking,
        'recommendations': recommendations[:5],
        'period_days': days,
        'veto_performance': veto_performance,
    }


def historical_win_prob_from_attribution_trades(
    attribution_trades: List[Dict[str, Any]],
    regime: str,
    path: str,
    atr_pct: float = 0,
    fg: int = 50,
    score: float = 0,
) -> float:
    """
    Win rate for regime+path from pre-built 90d attribution trade list (same semantics as get_historical_win_prob).
    atr_pct, fg, score reserved for future granular buckets.
    """
    reg = (regime or 'SIDEWAYS').upper()
    p = (path or 'default').strip() or 'default'
    subset = [t for t in attribution_trades if t.get('regime') == reg and t.get('quant_path') == p]
    if subset:
        wins = sum(1 for t in subset if t.get('outcome') == 'win')
        return min(0.75, max(0.35, wins / len(subset)))
    base = {'BULL': 0.68, 'SIDEWAYS': 0.60, 'BEAR': 0.52, 'VOLATILE': 0.50}.get(reg, 0.55)
    return min(0.75, base)


def get_historical_win_prob(regime: str, path: str, atr_pct: float = 0, fg: int = 50, score: float = 0) -> float:
    """
    Win rate for regime+path from trade journal (last 90d closed positions). Single source of truth.
    Fallback: regime base. Returns 0.0-1.0. atr_pct, fg, score reserved for future granular buckets.
    """
    recent = _get_closed_positions_from_journal(90)
    return historical_win_prob_from_attribution_trades(recent, regime, path, atr_pct, fg, score)


# FIX: Single source of truth — use thresholds module for path and defaults
try:
    from thresholds import LIVE_THRESHOLDS_PATH as LIVE_THRESHOLDS_FILE, DEFAULT_THRESHOLDS, reload_live_thresholds
except ImportError:
    LIVE_THRESHOLDS_FILE = os.path.join(os.path.dirname(__file__), 'live_thresholds.json')
    DEFAULT_THRESHOLDS = {'BLOOD_STREET_FG': 30, 'DIP_MIN_SCORE': 65, 'FUNDING_VETO_BEAR_PCT': 0.08}
    def reload_live_thresholds() -> None:
        pass


