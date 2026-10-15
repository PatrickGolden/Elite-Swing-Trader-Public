# quant_signal.py — DETERMINISTIC PRIMARY SIGNAL ENGINE
# LLM can ONLY veto or explain. Never creates or upgrades signals.
# COST-OPTIMIZED UPGRADE — Feb 2026: in-memory win_prob cache, edge/sniper only top-3, narrative/whale top-10.
# FIX: Threshold centralization — read from thresholds.py (live_thresholds.json).

from typing import Dict, Any, Optional
import time
import os
import logging
import threading

logger = logging.getLogger(__name__)

# FIX: Top-level imports with safe defaults
try:
    from model_attribution import get_historical_win_prob, _get_closed_positions_from_journal
except ImportError:
    get_historical_win_prob = None
    _get_closed_positions_from_journal = None
try:
    from short_tf_sniper import get_short_tf_sniper
except ImportError:
    get_short_tf_sniper = None
try:
    from thresholds import _load_live_thresholds
except ImportError:
    def _load_live_thresholds() -> Dict[str, Any]:
        return {}

# (regression fallback warn throttle removed with the blend — §5.8, 2026-07-03)

# COST-OPTIMIZED UPGRADE — light in-memory cache for win_prob (from model_attribution last 30d), TTL 4 min
_WIN_PROB_CACHE_LOCK = threading.Lock()
_WIN_PROB_CACHE: Dict[str, float] = {}
_WIN_PROB_CACHE_TS: float = 0
_WIN_PROB_CACHE_TTL: float = 240.0
_WIN_PROB_CACHE_MAX_SIZE = 500
_BASE_WIN_PROB_BY_REGIME: Dict[str, float] = {'BULL': 0.68, 'SIDEWAYS': 0.60, 'BEAR': 0.52, 'VOLATILE': 0.50}

# Momentum/trend-family entry paths kept under LEAN_ENTRY_MODE (Stage 1c prune).
# Defined in lean_policy.py so the signal gate (here) and the fail-closed fill
# guard (trade_journal.open_position) share one source of truth and cannot drift.
from lean_policy import LEAN_ALLOWED_PATHS

# --- Path Kill Switch (audit: auto-disable underperforming paths) ---
_PATH_KILL_CACHE_LOCK = threading.Lock()
_PATH_KILL_CACHE: Dict[str, tuple] = {}  # key -> (disabled, timestamp)
_PATH_KILL_CACHE_TTL: float = 600.0  # 10 min

def _is_path_disabled(path: str, regime: str) -> bool:
    """Check if a quant path is disabled due to poor win rate AND negative expectancy. Cached 10 min per key."""
    now = time.time()
    key = f"{regime}_{path}"
    with _PATH_KILL_CACHE_LOCK:
        entry = _PATH_KILL_CACHE.get(key)
        if entry is not None and (now - entry[1]) < _PATH_KILL_CACHE_TTL:
            return entry[0]
    # Compute from trade journal
    disabled = False
    try:
        if get_historical_win_prob and _get_closed_positions_from_journal:
            wr = get_historical_win_prob(regime, path)
            live = _load_live_thresholds()
            min_trades = int(live.get('PATH_KILL_MIN_TRADES', 20))  # raised from 10 for statistical significance
            kill_wr = float(live.get('PATH_KILL_WIN_RATE', 0.40))
            # get_historical_win_prob returns base rate if no trades, so check if we have data
            recent = _get_closed_positions_from_journal(90)
            path_trades = [t for t in recent if t.get('regime') == regime.upper() and t.get('quant_path') == path]
            if len(path_trades) >= min_trades and wr < kill_wr:
                # Require negative expectancy too — a low-WR path with large wins can be profitable
                wins = [t for t in path_trades if t.get('outcome') == 'win']
                losses = [t for t in path_trades if t.get('outcome') == 'loss']
                avg_win = sum(float(t.get('pnl_pct', 0) or 0) for t in wins) / len(wins) if wins else 0
                avg_loss = sum(abs(float(t.get('pnl_pct', 0) or 0)) for t in losses) / len(losses) if losses else 1
                expectancy = (wr * avg_win) - ((1 - wr) * avg_loss)
                if expectancy < 0:
                    disabled = True
                    logger.warning("[PATH_KILL] Disabling path %s in %s: win_rate=%.1f%%, expectancy=%.2f (%d trades)",
                                   path, regime, wr*100, expectancy, len(path_trades))
                else:
                    logger.info("[PATH_KILL] Keeping path %s in %s despite low win_rate=%.1f%%: expectancy=+%.2f (%d trades)",
                                path, regime, wr*100, expectancy, len(path_trades))
    except Exception:
        logger.exception("Error in _is_path_disabled")
    with _PATH_KILL_CACHE_LOCK:
        _PATH_KILL_CACHE[key] = (disabled, now)
    return disabled


# --- Portfolio Risk Gates (audit: prevent concentrated regime exposure) ---
def _check_portfolio_gate(regime: str) -> bool:
    """Return True if portfolio can accept another position in this regime. False = gate closed."""
    try:
        from trade_journal import get_open_positions
        live = _load_live_thresholds()
        max_per_regime = int(live.get('MAX_OPEN_PER_REGIME', 3))
        max_total = int(live.get('MAX_OPEN_TOTAL', 8))
        open_pos = get_open_positions(exclude_test=True)
        if len(open_pos) >= max_total:
            return False
        regime_count = sum(1 for p in open_pos if (p.get('market_regime') or p.get('regime') or '').upper() == regime.upper())
        if regime_count >= max_per_regime:
            return False
        return True
    except Exception:
        logger.exception("Error in _check_portfolio_gate")
        logger.warning(
            "[PORTFOLIO_GATE] Exception caused fail-closed: ALL buy signals suppressed until gate recovers. "
            "Check trade_journal / live_thresholds availability."
        )
        return False  # fail closed on error


def _check_same_day_duplicate(coin_id: str) -> bool:
    """Return True if this coin already has an entry today — blocks repeated same-day entries."""
    try:
        from trade_journal import get_all_positions
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        for p in get_all_positions(exclude_test=True):
            if (p.get('coin_id') or '').lower() != (coin_id or '').lower():
                continue
            if (p.get('entry_time') or '')[:10] == today:
                return True
        return False
    except Exception:
        logger.exception("Error in _check_same_day_duplicate")
        return False  # fail open — don't block on error


def _get_cached_historical_win_prob(regime: str, path: str, atr_pct: float, fg: int, score: float) -> float:
    """Pure in-memory / attribution.json cache; no heavy DB."""
    global _WIN_PROB_CACHE_TS, _WIN_PROB_CACHE
    now = time.time()
    with _WIN_PROB_CACHE_LOCK:
        if now - _WIN_PROB_CACHE_TS > _WIN_PROB_CACHE_TTL:
            _WIN_PROB_CACHE.clear()
            _WIN_PROB_CACHE_TS = now
        key = f"{regime}_{path}"
        if key in _WIN_PROB_CACHE:
            return _WIN_PROB_CACHE[key]
    try:
        if get_historical_win_prob:
            p = get_historical_win_prob(regime, path, atr_pct, fg, score)
            with _WIN_PROB_CACHE_LOCK:
                # Re-check TTL: cache may have been cleared while we were computing
                _now2 = time.time()
                if _now2 - _WIN_PROB_CACHE_TS > _WIN_PROB_CACHE_TTL:
                    _WIN_PROB_CACHE.clear()
                    _WIN_PROB_CACHE_TS = _now2
                if len(_WIN_PROB_CACHE) > _WIN_PROB_CACHE_MAX_SIZE:
                    _WIN_PROB_CACHE.clear()
                    _WIN_PROB_CACHE_TS = _now2
                _WIN_PROB_CACHE[key] = p
            return p
    except Exception:
        logger.exception("Error in _get_cached_historical_win_prob")
    base = _BASE_WIN_PROB_BY_REGIME.get((regime or '').upper(), 0.55)
    return min(0.75, base)


def _funding_pct(coin: Dict[str, Any]) -> Optional[float]:
    """Extract current funding rate in percent from coin.enhanced."""
    try:
        enh = coin.get('enhanced') or {}
        fr = enh.get('funding_rate')
        if fr is None:
            return None
        if isinstance(fr, (int, float)):
            return float(fr)
        return float((fr or {}).get('current', 0) or 0)
    except (TypeError, ValueError):
        return None


def _order_book_ratio(coin: Dict[str, Any]) -> Optional[float]:
    """Extract bid_ask_ratio from coin.enhanced (e.g. 1.05 = bid-heavy)."""
    try:
        enh = coin.get('enhanced') or {}
        ob = enh.get('order_book') or {}
        r = ob.get('bid_ask_ratio')
        if r is None:
            return None
        return float(r)
    except (TypeError, ValueError):
        return None


def _mtf_aligned(coin: Dict[str, Any]) -> bool:
    """True if 1h, 24h, 7d price changes are same sign (multi-timeframe confirmation)."""
    from coin_utils import get_price_changes
    pc = get_price_changes(coin)
    ch1, ch24, ch7 = pc['ch1h'], pc['ch24h'], pc['ch7d']
    return (ch1 > 0 and ch24 > 0 and ch7 > 0) or (ch1 < 0 and ch24 < 0 and ch7 < 0)


def _mtf_medium_aligned(coin: Dict[str, Any]) -> bool:
    """True if 24h, 7d, 30d (1d/1w/1m) price changes are same sign (medium-term trend)."""
    from coin_utils import get_price_changes
    pc = get_price_changes(coin)
    ch24, ch7, ch30 = pc['ch24h'], pc['ch7d'], pc['ch30d']
    return (ch24 > 0 and ch7 > 0 and ch30 > 0) or (ch24 < 0 and ch7 < 0 and ch30 < 0)


def _support_near_bonus(coin: Dict[str, Any], live: Optional[Dict[str, Any]] = None) -> float:
    """Score bonus when price is near support (low BB position = near lower band)."""
    ind = coin.get('indicators') or {}
    bb_pos = ind.get('bb_pos')
    if bb_pos is None:
        return 0
    try:
        import math
        p = float(bb_pos)
        if math.isnan(p):
            return 0
        if p < 15:
            return (live or _load_live_thresholds()).get('SUPPORT_NEAR_BONUS', 2)
        if p < 30:
            return 1.0
    except (TypeError, ValueError):
        pass
    return 0


def _estimated_win_prob(regime: str, enhanced: Dict[str, Any]) -> float:
    """Rough win probability from regime + on-chain (for edge score). Kept for tier boost backward compat."""
    base = _BASE_WIN_PROB_BY_REGIME.get((regime or '').upper(), 0.55)
    enh = enhanced or {}
    sopr = enh.get('sopr', 1.0)
    if isinstance(sopr, (int, float)) and sopr < 0.98:
        base += 0.03  # capitulation
    return min(0.75, base)


def _apply_adj(score: float, adj: float, cumulative_pos_adj: float, max_adj: float) -> tuple:
    """Apply score adjustment, capping cumulative positive boosts at max_adj.
    Negative adjustments (protective) always pass through uncapped."""
    if adj > 0:
        remaining = max_adj - cumulative_pos_adj
        if remaining <= 0:
            return score, cumulative_pos_adj
        clamped = min(adj, remaining)
        cumulative_pos_adj += clamped
        return max(0, min(100, score + clamped)), cumulative_pos_adj
    else:
        return max(0, min(100, score + adj)), cumulative_pos_adj


# NEW: Dynamic Probabilistic Calibration v1 — historical path-based win prob for edge_probability
def _resolve_quant_path(
    blood_path_taken: bool,
    dip_path_taken: bool,
    pullback_path_taken: bool,
    vol_harvest_path_taken: bool,
    capitulation_taken: bool,
    bull_participant_path: bool,
    vol_contraction_taken: bool,
    rally_floor_72: bool,
    momentum_breakout_taken: bool = False,
    early_momentum_taken: bool = False,
) -> str:
    """Single path label for attribution and get_historical_win_prob lookup."""
    if blood_path_taken:
        return 'blood_in_the_street'
    if capitulation_taken:
        return 'capitulation'
    if momentum_breakout_taken:
        return 'momentum_breakout'
    if early_momentum_taken:
        return 'early_momentum'
    if dip_path_taken:
        return 'dip'
    if pullback_path_taken:
        return 'pullback_in_trend'
    if vol_harvest_path_taken:
        return 'volatility_harvest'
    if vol_contraction_taken:
        return 'vol_contraction'
    if bull_participant_path:
        return 'bull_participant'
    if rally_floor_72:
        return 'rally'
    return 'default'


def generate_quant_signal(
    coin: Dict[str, Any],
    regime_data: Dict[str, Any],
    fear_greed_value: int = 50,
    position_sizer=None,
    cash_available: Optional[float] = None,
) -> Dict[str, Any]:
    """Produce final quant signal from base score + OB/MTF/support adjustments. Regression blend, position_sizing at end."""
    # COST-OPTIMIZED UPGRADE — load thresholds once per call to avoid repeated file reads
    live = _load_live_thresholds()
    _t = lambda k, d: live.get(k, d)
    blood_fg = _t('BLOOD_STREET_FG', 30)
    dip_min = _t('DIP_MIN_SCORE', 65)
    funding_veto_pct = _t('FUNDING_VETO_BEAR_PCT', 0.08)

    score = float(coin.get('score', 0) or 0)
    _max_adj = _t('MAX_CUMULATIVE_ADJ', 15)
    _cum_adj = 0.0  # tracks total positive boosts for inflation guard
    regime = (regime_data or {}).get('regime', 'SIDEWAYS')
    # --- Stage 1b: market-trend (risk-on/off) master gate (blueprint Sec 8-9) ---
    # risk_on None => gate inactive (legacy/tests preserve prior behavior); True/False => active.
    _chars = (regime_data or {}).get('characteristics') or {}
    risk_on = _chars.get('risk_on', None)
    _block_contrarian = (risk_on is True)   # don't buy fear-dips when BTC is risk-on (-9..-11% leak)
    mom30_min = float(_t('MOM30_MIN_CH30D', 15.0))
    fg = fear_greed_value
    ind = coin.get('indicators') or {}
    atr_pct = float(ind.get('atr_pct', 2.0) or 2.0)
    enhanced = coin.get('enhanced') or {}
    is_top_3 = bool(coin.get('_is_top_3', False))
    is_top_10 = bool(coin.get('_is_top_10', False))
    # Win prob computed for all coins — cached lookup is cheap; improves sizer edge_probability
    # for the full execution universe, not just top-3/top-10.
    win_prob_top_n = int(_t('WIN_PROB_TOP_N', 10))  # kept for backward compat / logging
    is_win_prob_eligible = True

    # Volume gate: penalize low-volume coins (Cycle 17)
    # NOTE: This penalty is applied before MTF bonuses. If MTF bonuses are intended to partially
    # offset volume concerns (e.g. strong multi-timeframe alignment on a thin coin), this should
    # move to after the MTF _apply_adj calls (~line 350). Currently kept here to avoid reordering
    # several interdependent blocks; revisit if volume-gate / MTF interaction is miscalibrated.
    volume_gate_hit = False
    vol_gate_ratio = _t('VOLUME_GATE_RATIO', 0.5)
    vol_gate_penalty = _t('VOLUME_GATE_PENALTY', 5)
    coin_volume = float(coin.get('total_volume', 0) or 0)
    vol_median = float((regime_data or {}).get('volume_median', 0) or 0)
    if vol_median > 0 and coin_volume > 0 and coin_volume < vol_median * vol_gate_ratio:
        score = max(0, score - vol_gate_penalty)
        volume_gate_hit = True

    # ATH proximity gate: penalize coins near all-time high (reversion risk)
    ath_proximity_hit = False
    ath_change_pct = coin.get('ath_change_percentage')
    if ath_change_pct is not None:
        try:
            ath_val = float(ath_change_pct)
            ath_veto_pct = float(_t('ATH_PROXIMITY_VETO_PCT', -5.0))
            if ath_val >= ath_veto_pct:
                score = max(0, score - _t('ATH_PROXIMITY_PENALTY', 3))
                ath_proximity_hit = True
        except (TypeError, ValueError):
            pass

    # FDV dilution gate: penalize tokens with FDV >> market cap (future unlock sell pressure)
    fdv_dilution_hit = False
    fdv_raw = coin.get('fully_diluted_valuation')
    mcap_raw = coin.get('market_cap')
    fdv_ratio = None
    if fdv_raw and mcap_raw:
        try:
            fdv_ratio = float(fdv_raw) / float(mcap_raw)
            if fdv_ratio > float(_t('FDV_DILUTION_RATIO_MAX', 3.0)):
                score = max(0, score - _t('FDV_DILUTION_PENALTY', 4))
                fdv_dilution_hit = True
        except (TypeError, ValueError, ZeroDivisionError):
            fdv_ratio = None

    # Order-book imbalance: weight score (bid-heavy = slight boost, ask-heavy = slight cut)
    ob_ratio = _order_book_ratio(coin)
    if ob_ratio is not None:
        ob_adj = _t('OB_IMBALANCE_MAX_ADJ', 4)
        adj = (ob_ratio - 1.0) * (ob_adj * 10)
        adj = max(-ob_adj, min(ob_adj, round(adj, 1)))
        score, _cum_adj = _apply_adj(score, adj, _cum_adj, _max_adj)

    # Sentiment modifier removed — pure quant scoring

    # Multi-timeframe confirmation: 1h/24h/7d aligned; 24h/7d/30d (1d/1w/1m) aligned = extra
    if _mtf_aligned(coin):
        score, _cum_adj = _apply_adj(score, _t('MTF_ALIGNMENT_BONUS', 3), _cum_adj, _max_adj)
    if _mtf_medium_aligned(coin):
        score, _cum_adj = _apply_adj(score, _t('MTF_MEDIUM_BONUS', 2), _cum_adj, _max_adj)

    # Z-score / volume profile proxy: near support (low BB position) = small boost
    _sup_bonus = _support_near_bonus(coin, live)
    if _sup_bonus > 0:
        score, _cum_adj = _apply_adj(score, _sup_bonus, _cum_adj, _max_adj)

    # Volatility regime filter: high ATR in BEAR = stricter gate (require higher score for BUY)
    high_vol_bear = (regime == 'BEAR' and atr_pct >= _t('HIGH_ATR_PCT', 4.0))
    if high_vol_bear and score < _t('HIGH_ATR_BEAR_MIN_SCORE', 80):
        score = min(score, 77)  # force below STRONG threshold unless already very high

    # Regression blend DELETED (STRATEGY.md §5.8, 2026-07-03): a 17-feature logistic
    # model fit on 71 trades that trained on trades its own score selected — rejected
    # by the June audit as unsupportable overfit. Do not reintroduce any learned score
    # blend until 500+ closed trades exist AND it passes the registered-trial gates.

    if score >= 85:
        quant_signal = 'BUY'
        quant_conf = 92
        tier = 'ULTRA_BUY'
    elif score >= 72:
        quant_signal = 'BUY'
        quant_conf = 78
        tier = 'STRONG_BUY'
    elif score >= 58:
        quant_signal = 'BUY'
        quant_conf = 65
        tier = 'MODERATE_BUY'
    else:
        quant_signal = 'HOLD'
        quant_conf = 50
        tier = 'HOLD'

    # Edge score: fractional Kelly criterion — only with real R:R data
    risk = coin.get('risk') or {}
    rr_ratio = float(risk.get('rr_ratio') or 0)
    has_real_rr = rr_ratio > 0 and risk.get('stop_loss') is not None
    win_prob = _estimated_win_prob(regime, enhanced)
    kelly_frac = float(_t('KELLY_FRACTION', 0.5))
    if has_real_rr and rr_ratio > 0:
        # Kelly edge: f* = (b*p - q) / b  where b=rr, p=win_prob, q=1-p; scaled by fraction
        raw_kelly = (rr_ratio * win_prob - (1 - win_prob)) / rr_ratio
        edge_score = raw_kelly * kelly_frac
    else:
        edge_score = 0.0  # no tier promotion without real R:R data
    # Only promote tier when edge is genuinely strong AND we have real data
    if has_real_rr and quant_signal == 'BUY' and edge_score >= _t('EDGE_BOOST_THRESHOLD', 0.5):
        if tier == 'MODERATE_BUY' and edge_score >= 0.35:
            tier = 'STRONG_BUY'
            quant_conf = min(85, quant_conf + 3)
        elif tier == 'STRONG_BUY' and edge_score >= 0.45:
            tier = 'ULTRA_BUY'
            quant_conf = min(92, quant_conf + 3)

    # Regime safety: BEAR/VOLATILE require minimum score for BUY. Rally (market_24h > 5%) uses floor 72; else BEAR_REGIME_MIN_SCORE (default 72).
    market_24h_pct = (regime_data or {}).get('market_24h_pct')
    regime_floor = 72 if (market_24h_pct is not None and float(market_24h_pct) > 5.0) else _t('BEAR_REGIME_MIN_SCORE', 72)
    if regime in ['BEAR', 'VOLATILE'] and score < regime_floor:
        quant_signal = 'HOLD'
        quant_conf = 35
        tier = 'HOLD'

    # SIDEWAYS regime protection (audit: 11.1% win rate in SIDEWAYS)
    sideways_floor = _t('SIDEWAYS_REGIME_MIN_SCORE', 68)
    if regime == 'SIDEWAYS' and score < sideways_floor:
        quant_signal = 'HOLD'
        quant_conf = 40
        tier = 'HOLD'

    # BULL regime: score floor is the only gate. No blanket suspension —
    # quality is enforced per-coin by structural fixes from commit 8d291f4
    # (chase guard, wider stops, phantom-upgrade clamp).
    bull_floor = _t('BULL_REGIME_MIN_SCORE', 62)
    bull_floor_hit = False
    if regime == 'BULL' and score < bull_floor:
        quant_signal = 'HOLD'
        quant_conf = 40
        tier = 'HOLD'
        bull_floor_hit = True

    # BULL: favor tokens contributing to the bull (momentum patterns — 24h/7d/30d aligned, strong momentum)
    bull_participant_path = False
    if regime == 'BULL' and quant_signal == 'HOLD' and score >= bull_floor:
        try:
            mom = float(ind.get('momentum') or 0)
            if _mtf_medium_aligned(coin) and mom > 10:
                quant_signal = 'BUY'
                quant_conf = 62
                tier = 'MODERATE_BUY'
                bull_participant_path = True
        except (TypeError, ValueError):
            pass

    # Short-term: dip opportunity — in BEAR/VOLATILE, sharp 24h dip with 1h stabilizing and 7d not in freefall (1d/1w context)
    ch1h = ind.get('ch1h')
    ch24h_coin = ind.get('ch24h')
    ch7d_coin = ind.get('ch7d')
    ch30d_coin = ind.get('ch30d')
    dip_path_taken = False
    pullback_path_taken = False
    vol_harvest_path_taken = False
    # Blood in the street first (highest conviction): F&G < 30 + dip → STRONG_BUY; then other paths
    blood_path_taken = False
    if regime in ['BEAR', 'VOLATILE'] and quant_signal == 'HOLD' and not _block_contrarian and fg < blood_fg and score >= _t('BLOOD_STREET_MIN_SCORE', 62):
        try:
            c1 = float(ch1h if ch1h is not None else 0)
            c24 = float(ch24h_coin if ch24h_coin is not None else 0)
            c7 = float(ch7d_coin if ch7d_coin is not None else 0)
            # FINAL FIX: thresholds centralized (was c1 > -4.0)
            if c24 < _t('BLOOD_STREET_24H_MAX', -5.0) and c1 > _t('BLOOD_STREET_1H_MIN', -4.0) and c7 > _t('DIP_7D_MAX', -15.0):
                quant_signal = 'BUY'
                quant_conf = 80
                tier = 'STRONG_BUY'
                blood_path_taken = True
        except (TypeError, ValueError):
            pass
    # Sniper: only take dip-style paths when there is fear (blood in the street — buy when others are fearful)
    if regime in ['BEAR', 'VOLATILE'] and quant_signal == 'HOLD' and not _block_contrarian and score >= dip_min and fg <= _t('BLOOD_FG_MAX', 45):
        try:
            c1 = float(ch1h if ch1h is not None else 0)
            c24 = float(ch24h_coin if ch24h_coin is not None else 0)
            c7 = float(ch7d_coin if ch7d_coin is not None else 0)
            if c24 < _t('DIP_24H_PCT', -6.0) and c1 > _t('DIP_1H_STABILIZING', -3.0) and c7 > _t('DIP_7D_MAX', -15.0):
                quant_signal = 'BUY'
                quant_conf = 65
                tier = 'MODERATE_BUY'
                dip_path_taken = True
        except (TypeError, ValueError):
            pass
    # Pullback-in-trend: only when F&G not greedy (sniper)
    if regime in ['BEAR', 'VOLATILE'] and quant_signal == 'HOLD' and score >= _t('PULLBACK_MIN_SCORE', 66) and fg <= _t('PULLBACK_FG_MAX', 50):
        try:
            c24 = float(ch24h_coin if ch24h_coin is not None else 0)
            c7 = float(ch7d_coin if ch7d_coin is not None else 0)
            c30 = float(ch30d_coin if ch30d_coin is not None else 0)
            if c7 > 0 and c30 > 0 and _t('PULLBACK_24H_MIN', -8.0) < c24 <= _t('PULLBACK_24H_MAX', -2.0):
                quant_signal = 'BUY'
                quant_conf = 68
                tier = 'MODERATE_BUY'
                pullback_path_taken = True
        except (TypeError, ValueError):
            pass
    # Volatility harvesting: only when fear present (sniper)
    if regime in ['BEAR', 'VOLATILE'] and quant_signal == 'HOLD' and not _block_contrarian and score >= _t('VOL_HARVEST_MIN_SCORE', 68) and fg <= _t('VOL_HARVEST_FG_MAX', 48) and atr_pct >= _t('VOL_HARVEST_ATR_PCT', 3.5):
        try:
            c1 = float(ch1h if ch1h is not None else 0)
            c24 = float(ch24h_coin if ch24h_coin is not None else 0)
            c7 = float(ch7d_coin if ch7d_coin is not None else 0)
            # FINAL FIX: thresholds centralized (was c1 > -4.0)
            if c24 < 0 and c1 > _t('VOL_HARVEST_1H_MIN', -4.0) and c7 > _t('VOL_HARVEST_7D_MAX', -12.0):
                quant_signal = 'BUY'
                quant_conf = 66
                tier = 'MODERATE_BUY'
                vol_harvest_path_taken = True
        except (TypeError, ValueError):
            pass
    # =========================================================================
    # MOMENTUM DETECTION — two-tier system (works in ALL regimes)
    # Tier 1: Early Momentum (MODERATE_BUY) — fires within hours via acceleration
    # Tier 2: Momentum Breakout (STRONG_BUY) — fires after confirmed multi-TF trend
    # =========================================================================
    momentum_breakout_taken = False
    early_momentum_taken = False
    mom_accel = ind.get('momentum_accel') or {}
    mom_quality = float(ind.get('momentum_quality') or 0)
    early_mom = float(ind.get('early_momentum') or 0)
    rel_str = ind.get('relative_strength') or {}
    rsi_val = float(ind.get('rsi') or 50)
    mom_val = float(ind.get('momentum') or 0)
    # Stage 1b: in risk-on, require medium-horizon (30d) momentum on momentum entries
    # (the validated edge is "risk-on AND 30d-momentum"). Inactive when risk_on is not True.
    _c30_mom = float(ch30d_coin) if ch30d_coin is not None else 0.0
    _mom30_ok = (risk_on is not True) or (_c30_mom >= mom30_min)

    if quant_signal == 'HOLD':
        try:
            c24_m = float(ch24h_coin if ch24h_coin is not None else 0)
            c7_m = float(ch7d_coin if ch7d_coin is not None else 0)

            # Tier 2 first (higher conviction): confirmed momentum breakout
            if (score >= _t('MOMENTUM_BREAKOUT_MIN_SCORE', 52)
                    and _mom30_ok
                    and mom_val > _t('MOMENTUM_BREAKOUT_MIN_MOM', 8)
                    and mom_accel.get('is_accelerating')
                    and mom_quality > _t('MOMENTUM_BREAKOUT_MIN_QUALITY', 40)
                    and _mtf_aligned(coin)
                    and rsi_val < _t('MOMENTUM_BREAKOUT_MAX_RSI', 78)
                    and c24_m < _t('MOMENTUM_BREAKOUT_MAX_24H', 15.0)
                    and c7_m < _t('MOMENTUM_BREAKOUT_MAX_7D', 25.0)):
                if mom_quality > _t('MOMENTUM_BREAKOUT_STRONG_QUALITY', 60) and rel_str.get('strong'):
                    quant_signal = 'BUY'
                    quant_conf = 78
                    tier = 'STRONG_BUY'
                else:
                    quant_signal = 'BUY'
                    quant_conf = 68
                    tier = 'MODERATE_BUY'
                momentum_breakout_taken = True

            # Tier 1: early momentum (lower conviction, fires hours into a move)
            # SIDEWAYS regime gate: require higher score (0/5 wins, -2.85%/trade in SIDEWAYS)
            elif (score >= (_t('EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE', _t('SIDEWAYS_REGIME_MIN_SCORE', 68))
                           if regime == 'SIDEWAYS'
                           else _t('EARLY_MOMENTUM_MIN_SCORE', 48))
                    and _mom30_ok
                    and early_mom > _t('EARLY_MOMENTUM_MIN_EARLY_MOM', 3.0)
                    and mom_accel.get('is_accelerating')
                    and mom_quality > _t('EARLY_MOMENTUM_MIN_QUALITY', 20)
                    and _mtf_aligned(coin)
                    and rsi_val < _t('EARLY_MOMENTUM_MAX_RSI', 78)
                    and c24_m < _t('EARLY_MOMENTUM_MAX_24H', 15.0)
                    and c7_m < _t('EARLY_MOMENTUM_MAX_7D', 30.0)):
                quant_signal = 'BUY'
                quant_conf = 62
                tier = 'MODERATE_BUY'
                early_momentum_taken = True
        except (TypeError, ValueError):
            pass

    # BULL chase guard: cap tier when chasing extended 24h moves in BULL regime.
    if regime == 'BULL' and quant_signal == 'BUY' and (momentum_breakout_taken or early_momentum_taken):
        try:
            _chase_24h = float(ch24h_coin if ch24h_coin is not None else 0)
            if _chase_24h > _t('BULL_CHASE_24H_MAX_PCT', 5.0):
                if tier in ('ULTRA_BUY', 'STRONG_BUY'):
                    tier = 'MODERATE_BUY'
                    quant_conf = min(quant_conf, 65)
        except (TypeError, ValueError):
            pass

    # Funding basis: compute flag for guards (whale accum, reasoning). Veto handled by veto_specialist.
    funding_pct = _funding_pct(coin)
    funding_veto_active = bool(regime == 'BEAR' and funding_pct is not None and funding_pct > funding_veto_pct)
    # Negative funding on dip: shorts paying = favorable for long; bump confidence when we took dip path
    if dip_path_taken and funding_pct is not None and float(funding_pct) < 0:
        quant_conf = 68

    # Capitulation exception (after funding check so we don't override the funding veto)
    capitulation_taken = False
    # FINAL FIX: thresholds centralized (was fg < 15, score >= 68)
    if regime == 'BEAR' and not _block_contrarian and fg < _t('CAPITULATION_FG_MAX', 15) and score >= _t('CAPITULATION_MIN_SCORE', 68) and (funding_pct is None or funding_pct <= funding_veto_pct):
        quant_signal = 'BUY'
        quant_conf = 82
        tier = 'ULTRA_BUY'
        capitulation_taken = True

    # Short-term + medium-term: pump caution — don't chase; cap tier when 24h/7d/30d extended.
    # BULL exempt: strong moves in BULL are momentum, not pumps to avoid.
    if regime != 'BULL':
        try:
            c24 = float(ind.get('ch24h') or 0)
            c7 = float(ind.get('ch7d') or 0)
            c30 = float(ind.get('ch30d') or 0)
            if quant_signal == 'BUY' and tier == 'ULTRA_BUY' and (c24 > _t('PUMP_24H_PCT', 14.0) or c7 > _t('PUMP_7D_PCT', 20.0) or c30 > _t('PUMP_30D_PCT', 35.0)):
                tier = 'STRONG_BUY'
                quant_conf = min(quant_conf, 85)
            if quant_signal == 'BUY' and tier == 'STRONG_BUY' and (c24 > _t('PUMP_STRONG_24H_PCT', 20.0) or c7 > _t('PUMP_STRONG_7D_PCT', 30.0)):
                tier = 'MODERATE_BUY'
                quant_conf = min(quant_conf, 75)
            elif quant_signal == 'BUY' and tier == 'STRONG_BUY' and (c7 > _t('PUMP_7D_PCT', 20.0) or c30 > _t('PUMP_30D_PCT', 35.0)):
                tier = 'MODERATE_BUY'
                quant_conf = min(quant_conf, 78)
        except (TypeError, ValueError):
            pass

    # NEW: High-Vol Regime v1 — VOLATILE contraction breakout; FINAL FIX: thresholds centralized
    vol_contraction_taken = False
    if regime == 'VOLATILE' and quant_signal == 'BUY' and atr_pct >= _t('HIGH_ATR_PCT', 4.0) and _mtf_medium_aligned(coin):
        try:
            fund = _funding_pct(coin)
            if fund is None or float(fund) < _t('VOL_CONTRACTION_FUNDING_MAX', 0.05):
                tier = 'STRONG_BUY'
                quant_conf = min(85, max(quant_conf, 78))
                vol_contraction_taken = True
        except (TypeError, ValueError):
            pass

    # narrative_velocity_spike and sentiment_delta removed — fields were never populated by any
    # data fetcher (phantom references). Re-add when real data source is available.

    # COST-OPTIMIZED UPGRADE — light whale accum in BEAR (top-10 only, 5-min cached flow)
    if (not funding_veto_active) and regime == 'BEAR' and is_top_10:
        netflow = enhanced.get('exchange_netflow_24h')
        whale = (enhanced.get('whale_netflow') or '').lower() if isinstance(enhanced.get('whale_netflow'), str) else None
        if (isinstance(netflow, (int, float)) and float(netflow) < 0) or (whale and 'accum' in whale):
            score, _cum_adj = _apply_adj(score, 3, _cum_adj, _max_adj)
            if quant_signal == 'BUY' and score >= 58:
                pass
            elif score >= 58 and quant_signal == 'HOLD':
                quant_signal = 'BUY'
                quant_conf = 65
                tier = 'MODERATE_BUY'

    # Data quality gate: STRONG_BUY/ULTRA_BUY require min N of (funding_ok, order_book_ok, onchain_ok)
    # Gate must be HONEST — log which sources are missing so journal tracks data completeness
    dq = enhanced.get('data_quality') or {}
    ok_count = dq.get('ok_count', 0)
    dq_missing = [src for src in ('funding_ok', 'order_book_ok', 'onchain_ok') if not dq.get(src)]
    _dq_sym = ind.get('symbol') or coin.get('symbol', '?')
    # Momentum bypass: strong 24h gainers get relaxed data requirements (score already penalizes low-data)
    _coin_24h_pct = float(coin.get('price_change_percentage_24h') or coin.get('price_change_percentage_24h_in_currency') or 0)
    _momentum_bypass = _coin_24h_pct >= _t('DATA_QUALITY_MOMENTUM_BYPASS_PCT', 10.0)
    if tier in ('STRONG_BUY', 'ULTRA_BUY'):
        required = _t('REQUIRED_SOURCES_FOR_STRONG_BUY', 2)
        if _momentum_bypass:
            required = max(1, required - 1)
        if ok_count < required:
            tier = 'MODERATE_BUY'
            quant_conf = min(quant_conf, 72)
            logger.warning(f"  [DATA_QUALITY] {_dq_sym}: downgraded to MODERATE_BUY — only {ok_count}/3 sources OK (missing: {', '.join(dq_missing)})")
        elif dq_missing:
            # Gate passes but sources are degraded — apply confidence penalty per missing source
            conf_penalty = len(dq_missing) * 3
            quant_conf = max(50, quant_conf - conf_penalty)
            logger.info(f"  [DATA_QUALITY] {_dq_sym}: {tier} with {ok_count}/3 sources (missing: {', '.join(dq_missing)}, conf -{conf_penalty})")
    # MODERATE_BUY data quality gate deferred to after signal_badges init (line ~680)
    # Only fires when data_quality dict is present (sources were actually checked)
    moderate_dq_hold = False
    if tier == 'MODERATE_BUY' and dq:
        mod_required = int(_t('REQUIRED_SOURCES_FOR_MODERATE_BUY', 1))
        if ok_count < mod_required:
            quant_signal = 'HOLD'
            quant_conf = 35
            tier = 'HOLD'
            moderate_dq_hold = True
            logger.warning(f"  [DATA_QUALITY] {_dq_sym}: MODERATE_BUY downgraded to HOLD — {ok_count}/3 sources (need {mod_required})")

    # Historical win prob and edge_probability — COST-OPTIMIZED: top-N (configurable, in-memory cache)
    rally_floor_72 = bool(market_24h_pct is not None and float(market_24h_pct) > 5.0)
    quant_path = _resolve_quant_path(
        blood_path_taken, dip_path_taken, pullback_path_taken, vol_harvest_path_taken,
        capitulation_taken, bull_participant_path, vol_contraction_taken, rally_floor_72,
        momentum_breakout_taken, early_momentum_taken,
    )
    # Improve attribution granularity: distinguish default buys with momentum context.
    if quant_path == 'default' and quant_signal == 'BUY' and momentum_breakout_taken is False and early_momentum_taken is False:
        if mom_accel.get('is_accelerating') and _mtf_aligned(coin) and mom_quality > 10:
            quant_path = 'momentum_context'
    # (Path-corrected regression re-blend deleted with the blend — §5.8, 2026-07-03.)
    if is_win_prob_eligible:
        try:
            historical_win_prob = _get_cached_historical_win_prob(regime, quant_path, atr_pct, fg, score)
        except Exception:
            logger.exception("Error getting historical win prob")
            historical_win_prob = _estimated_win_prob(regime, enhanced)
    else:
        historical_win_prob = _estimated_win_prob(regime, enhanced)
    # Final edge probability: fractional Kelly using historical win prob
    if has_real_rr and rr_ratio > 0:
        final_edge_prob = ((rr_ratio * historical_win_prob - (1 - historical_win_prob)) / rr_ratio) * kelly_frac
    else:
        final_edge_prob = 0.0

    reasoning = f"Deterministic score {score:.0f} → {tier} in {regime} (F&G {fg}, edge={edge_score:.2f})"
    if market_24h_pct is not None and float(market_24h_pct) > 5.0:
        reasoning += f"; rally 24h {float(market_24h_pct):.1f}% → floor 72."
    if dip_path_taken:
        reasoning += "; short-term dip (24h<{:.0f}%, 1h stabilizing).".format(_t('DIP_24H_PCT', -6.0))
    if pullback_path_taken:
        reasoning += "; pullback-in-trend (7d/30d up, 24h down)."
    if vol_harvest_path_taken:
        reasoning += "; volatility harvest (high ATR + dip)."
    if blood_path_taken:
        reasoning += "; blood in the street (F&G<30 + dip, asymmetric)."
    if tier == 'STRONG_BUY' and ind.get('ch24h') is not None and float(ind.get('ch24h')) > _t('PUMP_24H_PCT', 14.0):
        reasoning += "; pump cap (24h>{:.0f}%).".format(_t('PUMP_24H_PCT', 14.0))
    if regime == 'BEAR' and funding_pct is not None and funding_pct > funding_veto_pct:
        reasoning += f"; funding {funding_pct:.3f}% > {funding_veto_pct}% (veto)."
    if high_vol_bear and score < _t('HIGH_ATR_BEAR_MIN_SCORE', 80):
        reasoning += "; high vol BEAR gate applied."
    if bull_participant_path:
        reasoning += "; BULL participant (56+ score, MTF aligned, momentum>10)."
    if momentum_breakout_taken:
        reasoning += f"; MOMENTUM BREAKOUT (quality={mom_quality:.0f}, mom={mom_val:.1f}, RS={'strong' if rel_str.get('strong') else 'ok'})."
    if early_momentum_taken:
        reasoning += f"; EARLY MOMENTUM (accel detected, early_mom={early_mom:.1f}, quality={mom_quality:.0f})."
    if vol_contraction_taken:
        reasoning += "; VOLATILE contraction (ATR>=thresh, MTF aligned, funding<thresh)."
    if ath_proximity_hit and ath_change_pct is not None:
        reasoning += f"; ATH proximity penalty ({float(ath_change_pct):.1f}% from ATH)."
    if fdv_dilution_hit and fdv_ratio is not None:
        reasoning += f"; FDV dilution penalty (ratio={fdv_ratio:.1f}x)."
    if live:
        reasoning += "; live_thresholds applied."
    if is_top_3:
        reasoning += f" Historical win prob: {historical_win_prob*100:.1f}% (path={quant_path}, regime={regime})."

    # EXPERT SIGNALS REMOVED — audit: no statistical edge from influencer overlays.

    # Signal badges for frontend (sniper / strategy origin)
    signal_badges = []
    if moderate_dq_hold:
        signal_badges.append('data_quality_hold')
    if blood_path_taken:
        signal_badges.append('blood_in_the_street')
    if dip_path_taken:
        signal_badges.append('dip')
    if pullback_path_taken:
        signal_badges.append('pullback_in_trend')
    if vol_harvest_path_taken:
        signal_badges.append('volatility_harvest')
    if market_24h_pct is not None and float(market_24h_pct) > 5.0:
        signal_badges.append('rally')
    if quant_signal == 'BUY' and ind.get('ch24h') is not None and float(ind.get('ch24h', 0)) > _t('PUMP_24H_PCT', 14.0):
        signal_badges.append('pump_cap')
    if capitulation_taken:
        signal_badges.append('capitulation')
    if vol_contraction_taken:
        signal_badges.append('vol_contraction')
    if momentum_breakout_taken:
        signal_badges.append('momentum_breakout')
    if early_momentum_taken:
        signal_badges.append('early_momentum')
    if bull_floor_hit:
        signal_badges.append('bull_score_floor')
    if volume_gate_hit:
        signal_badges.append('low_volume')
    if ath_proximity_hit:
        signal_badges.append('ath_near_top')
    if fdv_dilution_hit:
        signal_badges.append('fdv_diluted')
    if get_historical_win_prob and quant_path != 'default' and is_win_prob_eligible:
        signal_badges.append('dynamic_prob')
    # narrative_spike badge removed — phantom fields never populated in production
    # Expert theme badges removed — no statistical edge from influencer overlays
    if regime == 'BEAR' and is_top_10:
        netflow = enhanced.get('exchange_netflow_24h')
        whale = (enhanced.get('whale_netflow') or '').lower() if isinstance(enhanced.get('whale_netflow'), str) else None
        if (isinstance(netflow, (int, float)) and float(netflow) < 0) or (whale and 'accum' in whale):
            signal_badges.append('light_whale_accum')
    # COST-OPTIMIZED UPGRADE — light sniper (pure local, top-3 + BUY only): ch1h, OB, BB_pos
    light_sniper_entry = {}
    if is_top_3 and quant_signal == 'BUY':
        ch1h = float(ind.get('ch1h') or 0)
        ch24h = float(ind.get('ch24h') or coin.get('price_change_percentage_24h') or coin.get('price_change_percentage_24h_in_currency') or 0)
        ob_ok = ob_ratio is not None and float(ob_ratio) > _t('OB_RATIO_SNIPER_MIN', 1.08)
        bb_pos = ind.get('bb_pos')
        bb_ok = bb_pos is not None and float(bb_pos) < 25
        if ch1h > -3.0 and (abs(ch24h) < 0.001 or ch1h > ch24h / 2.0) and ob_ok and bb_ok:
            signal_badges.append('light_sniper_entry')
            price = coin.get('current_price')
            light_sniper_entry = {'active': True, 'entry_suggestion': f"Next 15m close above ${float(price):,.2f}" if price else "Next 15m close above current price"}
    # Bull market: tokens contributing to the move; momentum continuation (1d/1w/1m aligned, positive momentum)
    if regime == 'BULL':
        try:
            c24 = float(ind.get('ch24h') or 0)
            mom = float(ind.get('momentum') or 0)
            if _mtf_medium_aligned(coin) and c24 > 0 and mom > 10 and 'bull_participant' not in signal_badges:
                signal_badges.append('bull_participant')
            if _mtf_medium_aligned(coin) and c24 > 0 and mom > 5 and 'momentum_continuation' not in signal_badges:
                signal_badges.append('momentum_continuation')
        except (TypeError, ValueError):
            pass
    # Momentum context badge: tag any BUY that had acceleration + MTF alignment,
    # even if it came through the default score path (for attribution tracking).
    if (quant_signal == 'BUY'
            and not momentum_breakout_taken and not early_momentum_taken
            and mom_accel.get('is_accelerating')
            and _mtf_aligned(coin)
            and mom_quality > 10
            and 'momentum_context' not in signal_badges):
        signal_badges.append('momentum_context')

    # Short-TF Sniper (legacy): when not cost-optimized path use external module if present
    short_tf_sniper = light_sniper_entry if light_sniper_entry else {}
    if not short_tf_sniper and get_short_tf_sniper and quant_signal == 'BUY':
        try:
            short_tf_sniper = get_short_tf_sniper(coin, quant_signal)
            if short_tf_sniper.get('active'):
                signal_badges.append('intra_swing_sniper')
        except Exception:
            logger.exception("Error in get_short_tf_sniper")

    # Final polish: confirmation_score (tunable weights, capped bonuses)
    _conf_base_w = float(_t('CONF_SCORE_BASE_WEIGHT', 0.70))
    _conf_bonus = 0
    if light_sniper_entry.get('active'):
        _conf_bonus += _t('CONF_SNIPER_BONUS', 10)
    # narrative_spike bonus removed — phantom field never populated in production
    if 'light_whale_accum' in signal_badges:
        _conf_bonus += _t('CONF_WHALE_BONUS', 5)
    _conf_bonus = min(_conf_bonus, _t('CONF_MAX_BONUS', 15))
    confirmation_score = min(100, (score * _conf_base_w) + _conf_bonus)
    # Only promote if the BASE score justifies it
    if confirmation_score >= 85 and is_top_3 and tier == 'MODERATE_BUY' and score >= _t('CONF_PROMOTE_MIN_SCORE', 70):
        tier = 'STRONG_BUY'
        quant_conf = min(85, quant_conf + 3)

    # PATH KILL SWITCH: auto-disable paths with <40% win rate over 10+ trades (audit fix)
    if quant_signal == 'BUY' and quant_path != 'default':
        if _is_path_disabled(quant_path, regime):
            quant_signal = 'HOLD'
            quant_conf = 35
            tier = 'HOLD'
            signal_badges.append('path_killed')
            reasoning += f"; PATH KILLED: {quant_path} disabled in {regime} (win rate below threshold)."

    # LEAN ENTRY MODE (Stage 1c prune): keep only momentum/trend-family BUYs while
    # BTC is risk-on; suppress mean-reversion/contrarian/default paths and block all
    # new longs in risk-off. Off by default (flag=0) → legacy behavior + tests unchanged.
    if quant_signal == 'BUY' and float(_t('LEAN_ENTRY_MODE', 0.0) or 0.0) >= 1.0:
        _lean_min_mom = float(_t('LEAN_ENTRY_MIN_MOM30', 15.0))
        if not (risk_on is True and quant_path in LEAN_ALLOWED_PATHS and _c30_mom >= _lean_min_mom):
            quant_signal = 'HOLD'
            quant_conf = 30
            tier = 'HOLD'
            signal_badges.append('lean_pruned')
            reasoning += f"; LEAN MODE: {quant_path} suppressed (risk_on={risk_on}, ch30d={_c30_mom:.1f})."

    # PORTFOLIO RISK GATE: max open positions per regime (audit fix)
    if quant_signal == 'BUY' and is_top_3:
        if not _check_portfolio_gate(regime):
            quant_signal = 'HOLD'
            quant_conf = 30
            tier = 'HOLD'
            signal_badges.append('portfolio_gate')
            reasoning += f"; PORTFOLIO GATE: max positions in {regime} reached."

    # SAME-DAY DUPLICATE GATE: block re-entry into a coin already entered today
    if quant_signal == 'BUY' and is_top_3:
        _coin_id = coin.get('id') or coin.get('coin_id') or ''
        if _coin_id and _check_same_day_duplicate(_coin_id):
            quant_signal = 'HOLD'
            quant_conf = 30
            tier = 'HOLD'
            signal_badges.append('same_day_duplicate')
            reasoning += f"; SAME-DAY GATE: {_coin_id} already entered today."

    # Position sizing at end using final_edge_prob, rr_ratio, regime, stop_loss
    position_sizing = {}
    if position_sizer is not None:
        entry_price = coin.get('current_price')
        stop_loss_pct = coin.get('stop_loss_pct') or (coin.get('risk') or {}).get('stop_loss_pct')
        if stop_loss_pct is not None:
            try:
                stop_loss_pct = float(stop_loss_pct)
            except (TypeError, ValueError):
                stop_loss_pct = 2.5
        else:
            stop_loss_pct = 2.5
        if entry_price is not None and float(entry_price) > 0 and stop_loss_pct > 0:
            try:
                annualized_vol = float(ind.get('volatility', 0) or 0)
                # Use proper annualized vol when available; fall back to ATR proxy
                passed_vol = annualized_vol if annualized_vol > 0 else (atr_pct * 25 if atr_pct else 50)
                position_sizing = position_sizer.calculate_for_consensus(
                    entry_price=float(entry_price),
                    stop_loss_pct=stop_loss_pct,
                    consensus_signal=tier,
                    regime_data=regime_data,
                    coin_volatility=passed_vol,
                    cash_available=cash_available,
                    edge_probability=final_edge_prob,
                    rr_ratio=rr_ratio,
                )
                position_sizing["_vol_debug"] = {
                    "atr_pct": round(atr_pct, 4),
                    "passed_vol_atr25": round(passed_vol, 2),
                    "annualized_vol": round(annualized_vol, 2),
                    "vol_bucket_used": "high" if passed_vol > 100 else "med-high" if passed_vol > 75 else "med" if passed_vol > 50 else "low",
                    "vol_bucket_correct": "high" if annualized_vol > 100 else "med-high" if annualized_vol > 75 else "med" if annualized_vol > 50 else "low",
                }
            except Exception:
                logger.exception("Error in position_sizer.calculate_for_consensus")

    out = {
        'quant_signal': quant_signal,
        'quant_confidence': quant_conf,
        'quant_tier': tier,
        'score': score,
        'edge_score': round(edge_score, 3),
        'reasoning': reasoning,
        'signal_badges': signal_badges,
        'quant_path': quant_path,
        'edge_probability': round(final_edge_prob, 4) if is_win_prob_eligible else 0.0,
        'confirmation_score': round(confirmation_score, 1),
        'short_tf_sniper': short_tf_sniper or {},
        'position_sizing': position_sizing,
        'data_quality_ok_count': ok_count,
        'data_quality_missing': dq_missing,
        'ath_change_pct': float(ath_change_pct) if ath_change_pct is not None else None,
        'fdv_ratio': round(fdv_ratio, 2) if fdv_ratio is not None else None,
        'risk_on': risk_on,
        'risk_regime': ('RISK_ON' if risk_on is True else ('RISK_OFF' if risk_on is False else None)),
    }
    if is_win_prob_eligible:
        out['historical_win_prob'] = round(historical_win_prob, 4)
    return out
