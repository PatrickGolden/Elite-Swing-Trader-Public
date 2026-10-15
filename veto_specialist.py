# veto_specialist.py — Domain-specific veto layer (rule-based only)
# Runs after main consensus; overrides to HOLD when failure modes are detected.
# Output: veto_decision (CONFIRM/VETO), veto_confidence 0-100, veto_reason.
# Threshold centralization — read from thresholds.py. model_attribution import at top-level.

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path(os.path.dirname(__file__))
VETO_LOG_FILE = _PROJECT_DIR / 'data' / 'veto_log.jsonl'

# Top-level import for dynamic veto threshold (no import inside function)
try:
    from model_attribution import get_model_attribution
except ImportError:
    get_model_attribution = None

try:
    from thresholds import get_threshold
except ImportError:
    def get_threshold(key: str, default: Any) -> Any:
        _d = {
            'FUNDING_VETO_BEAR_PCT': 0.08, 'FUNDING_VETO_BEAR_RELAXED_PCT': 0.12,
            'EXCHANGE_NETFLOW_VETO': 1500, 'EXCHANGE_NETFLOW_VETO_MID': 3000,
            'EXCHANGE_NETFLOW_VETO_LARGE': 5000, 'LTH_DISTRIBUTION_SOPR_MIN': 1.02,
            'LTH_DISTRIBUTION_SUPPLY_PCT': 0.3, 'NARRATIVE_EXHAUSTION_FG': 83,
            'NARRATIVE_EXHAUSTION_7D_PCT': 25, 'SHORT_TERM_PUMP_FG': 78,
            'SHORT_TERM_PUMP_24H_PCT': 15, 'EXTENDED_RALLY_30D_PCT': 50, 'EXTENDED_RALLY_FG': 80,
        }
        return _d.get(key, default)

# Rule order (first match wins; returns veto reason + confidence):
# 1. Funding: BEAR + funding_pct > FUNDING_VETO_BEAR_PCT (0.08%).
# 2. Whale dump: exchange_netflow_24h > EXCHANGE_NETFLOW_VETO (1500).
# 3. LTH distribution: lth_sopr >= 1.02 and lth_supply_change > 0.3%.
# 4. BULL euphoria: F&G >= 90 and 7d >= 30% — even BULL needs protection against euphoria tops.
# 5. BULL extended: F&G >= 85 and 30d >= 60% — BULL regime late participation in parabolic move.
# 6. Narrative exhaustion: F&G >= 83 and 7d >= 25% (non-BULL).
# 7. Short-term pump: F&G >= 78 and 24h >= 15% (non-BULL).
# 8. Extended rally: F&G >= 80 and 30d >= 50% (non-BULL).


def _rule_extreme_funding_bear(regime: str, funding_pct: Optional[float]) -> Optional[str]:
    """BEAR regime + extreme positive funding = long squeeze risk. Relax when funding negative (dip path)."""
    if (regime or '').upper() != 'BEAR':
        return None
    try:
        v = float(funding_pct or 0)
        threshold = get_threshold('FUNDING_VETO_BEAR_RELAXED_PCT', 0.12) if v < 0 else get_threshold('FUNDING_VETO_BEAR_PCT', 0.08)
        if v > threshold:
            return f"VETO: BEAR regime with elevated funding ({v:.3f}% > {threshold}%). Long squeeze risk; wait for funding to normalize."
    except (TypeError, ValueError):
        pass
    return None


def _rule_whale_dump(netflow_24h: Optional[float], coin: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Strong positive exchange netflow = distribution/selling pressure. Market-cap-tiered thresholds."""
    try:
        v = float(netflow_24h or 0)
        mcap = float((coin or {}).get('market_cap', 0) or 0)
        # Tiered thresholds: large-cap coins have naturally higher netflows
        if mcap > 50e9:  # BTC/ETH tier
            threshold = get_threshold('EXCHANGE_NETFLOW_VETO_LARGE', 5000)
        elif mcap > 5e9:  # large-cap
            threshold = get_threshold('EXCHANGE_NETFLOW_VETO_MID', 3000)
        else:  # mid/small-cap
            threshold = get_threshold('EXCHANGE_NETFLOW_VETO', 1500)
        if v > threshold:
            return f"VETO: Heavy exchange inflow (netflow +{v:.0f}, threshold {threshold}). Smart money distributing; avoid buying into supply dump."
    except (TypeError, ValueError):
        pass
    return None


def _rule_lth_distribution(lth_sopr: Optional[float], lth_supply_change_pct: Optional[float]) -> Optional[str]:
    """LTH SOPR > 1 + LTH supply increasing = profit-taking by long-term holders."""
    try:
        sopr = float(lth_sopr or 1.0)
        supply = float(lth_supply_change_pct or 0)
        if sopr >= get_threshold('LTH_DISTRIBUTION_SOPR_MIN', 1.02) and supply > get_threshold('LTH_DISTRIBUTION_SUPPLY_PCT', 0.3):
            return f"VETO: LTH profit-taking (LTH SOPR {sopr:.3f}, LTH supply +{supply:.2f}%). Distribution phase; wait for stabilization."
    except (TypeError, ValueError):
        pass
    return None


def _rule_bull_euphoria(fear_greed: int, price_change_7d: Optional[float], regime: str = '') -> Optional[str]:
    """BULL regime: extreme greed + big weekly rally = euphoria top. Applies IN BULL only."""
    if (regime or '').upper() != 'BULL':
        return None
    try:
        fg = int(fear_greed or 50)
        chg = float(price_change_7d or 0)
        if fg >= get_threshold('BULL_EUPHORIA_FG', 90) and chg >= get_threshold('BULL_EUPHORIA_7D_PCT', 30):
            return f"VETO: BULL euphoria (F&G {fg}, 7d +{chg:.1f}%). Even bull markets correct from euphoric peaks."
    except (TypeError, ValueError):
        pass
    return None


def _rule_bull_extended(fear_greed: int, price_change_30d: Optional[float], regime: str = '') -> Optional[str]:
    """BULL regime: high greed + parabolic monthly move = late entry risk. Applies IN BULL only."""
    if (regime or '').upper() != 'BULL':
        return None
    try:
        fg = int(fear_greed or 50)
        chg = float(price_change_30d or 0)
        if fg >= get_threshold('BULL_EXTENDED_FG', 85) and chg >= get_threshold('BULL_EXTENDED_30D_PCT', 60):
            return f"VETO: BULL extended (F&G {fg}, 30d +{chg:.1f}%). Parabolic move with extreme greed; wait for pullback."
    except (TypeError, ValueError):
        pass
    return None


def _rule_ath_proximity(ath_change_pct: Optional[float], regime: str = '') -> Optional[str]:
    """Near-ATH entry in non-BULL regime = reversion risk. BULL exempt (breakouts are the thesis)."""
    if (regime or '').upper() == 'BULL':
        return None
    try:
        if ath_change_pct is None:
            return None
        v = float(ath_change_pct)
        threshold = get_threshold('ATH_PROXIMITY_VETO_PCT', -5.0)
        if v >= threshold:
            return f"VETO: Near all-time high ({v:+.1f}% from ATH, threshold {threshold}%). Mean reversion risk elevated outside BULL."
    except (TypeError, ValueError):
        pass
    return None


def _rule_narrative_exhaustion(fear_greed: int, price_change_7d: Optional[float], regime: str = '') -> Optional[str]:
    """Extreme greed + big recent rally = narrative exhaustion. Exempt in BULL."""
    if (regime or '').upper() == 'BULL':
        return None
    try:
        fg = int(fear_greed or 50)
        chg = float(price_change_7d or 0)
        if fg >= get_threshold('NARRATIVE_EXHAUSTION_FG', 83) and chg >= get_threshold('NARRATIVE_EXHAUSTION_7D_PCT', 25):
            return f"VETO: Narrative exhaustion (F&G {fg}, 7d +{chg:.1f}%). Chasing extended move; wait for pullback."
    except (TypeError, ValueError):
        pass
    return None


def _rule_short_term_pump(fear_greed: int, price_change_24h: Optional[float], regime: str = '') -> Optional[str]:
    """Short-term: F&G elevated + big 24h pump = don't chase. Exempt in BULL."""
    if (regime or '').upper() == 'BULL':
        return None
    try:
        fg = int(fear_greed or 50)
        ch24 = float(price_change_24h or 0)
        if fg >= get_threshold('SHORT_TERM_PUMP_FG', 78) and ch24 >= get_threshold('SHORT_TERM_PUMP_24H_PCT', 15):
            return f"VETO: Short-term overbought (F&G {fg}, 24h +{ch24:.1f}%). Don't chase; wait for pullback."
    except (TypeError, ValueError):
        pass
    return None


def _rule_extended_rally_30d(fear_greed: int, price_change_30d: Optional[float], regime: str = '') -> Optional[str]:
    """Medium-term: F&G high + big 30d move = extended rally; don't chase. Exempt in BULL."""
    if (regime or '').upper() == 'BULL':
        return None
    try:
        fg = int(fear_greed or 50)
        ch30 = float(price_change_30d or 0)
        if fg >= get_threshold('EXTENDED_RALLY_FG', 80) and ch30 >= get_threshold('EXTENDED_RALLY_30D_PCT', 50):
            return f"VETO: Extended rally (F&G {fg}, 30d +{ch30:.1f}%). Monthly move extended; wait for pullback."
    except (TypeError, ValueError):
        pass
    return None


def _apply_rules(
    coin: Dict[str, Any],
    regime_data: Dict[str, Any],
    enhanced: Dict[str, Any],
    fear_greed_value: int,
) -> Tuple[Optional[str], Optional[int]]:
    """
    Run explicit failure-mode rules. Returns (veto_reason, confidence) or (None, None).
    If any rule fires, we VETO with high confidence (85).
    """
    regime = ((regime_data or {}).get('regime', '') or '').strip()
    funding = enhanced.get('funding_rate') or {}
    funding_pct = funding.get('current')
    if funding_pct is None and isinstance(funding, (int, float)):
        funding_pct = funding

    reason = _rule_extreme_funding_bear(regime, funding_pct)
    if reason:
        return reason, 85

    reason = _rule_whale_dump(enhanced.get('exchange_netflow_24h'), coin=coin)
    if reason:
        return reason, 82

    reason = _rule_lth_distribution(
        enhanced.get('lth_sopr'),
        enhanced.get('lth_supply_change_24h_pct'),
    )
    if reason:
        return reason, 80

    # ATH proximity veto (before price change rules — independent of F&G)
    reason = _rule_ath_proximity(coin.get('ath_change_percentage'), regime=regime)
    if reason:
        return reason, 78

    # Extract price changes once for all remaining rules
    from coin_utils import get_price_changes
    pc = get_price_changes(coin)

    # BULL-specific veto rules (audit: BULL had 0% win rate with zero protection)
    reason = _rule_bull_euphoria(fear_greed_value, pc['ch7d'], regime=regime)
    if reason:
        return reason, 88

    reason = _rule_bull_extended(fear_greed_value, pc['ch30d'], regime=regime)
    if reason:
        return reason, 85

    reason = _rule_narrative_exhaustion(fear_greed_value, pc['ch7d'], regime=regime)
    if reason:
        return reason, 78

    reason = _rule_short_term_pump(fear_greed_value, pc['ch24h'], regime=regime)
    if reason:
        return reason, 75

    reason = _rule_extended_rally_30d(fear_greed_value, pc['ch30d'], regime=regime)
    if reason:
        return reason, 76

    return None, None


def evaluate(
    coin: Dict[str, Any],
    regime_data: Dict[str, Any],
    consensus_result: Dict[str, Any],
    enhanced: Optional[Dict[str, Any]] = None,
    fear_greed_value: int = 50,
) -> Dict[str, Any]:
    """
    Run the specialized veto layer (rule-based only).
    Only evaluates when consensus_signal is a BUY tier (ULTRA/STRONG/MODERATE).

    Returns:
        veto_decision: "CONFIRM" | "VETO"
        veto_confidence: 0-100
        veto_reason: str
        rule_triggered: True if a rule fired
    """
    enhanced = enhanced or coin.get('enhanced') or {}
    consensus_signal = (consensus_result or {}).get('consensus_signal', 'HOLD')
    is_buy = consensus_signal in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY')

    out = {
        'veto_decision': 'CONFIRM',
        'veto_confidence': 0,
        'veto_reason': '',
        'rule_triggered': False,
    }

    if not is_buy:
        return out

    # 1) Rules
    rule_reason, rule_conf = _apply_rules(coin, regime_data, enhanced, fear_greed_value)
    if rule_reason:
        out['veto_decision'] = 'VETO'
        out['veto_confidence'] = rule_conf or 80
        out['veto_reason'] = rule_reason
        out['rule_triggered'] = True
        # Log veto for regret tracking (Phase 3B)
        _log_veto(coin, regime_data, rule_reason, consensus_signal, fear_greed_value)
        return out

    out['veto_reason'] = 'No veto rules triggered.'
    return out


def _log_veto(coin: Dict, regime_data: Dict, veto_reason: str, consensus_signal: str, fear_greed: int) -> None:
    """Append veto event to data/veto_log.jsonl for regret tracking."""
    try:
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'coin_id': coin.get('id') or coin.get('coin_id', ''),
            'symbol': coin.get('symbol', ''),
            'regime': (regime_data or {}).get('regime', 'UNKNOWN'),
            'veto_reason': veto_reason,
            'consensus_signal': consensus_signal,
            'score': coin.get('score', 0),
            'fear_greed': fear_greed,
            'current_price': coin.get('current_price'),
        }
        with open(VETO_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        logger.debug("[veto] Failed to log veto: %s", e)


def _get_dynamic_veto_threshold(regime: str) -> int:
    """NEW: Dynamic Veto v1 — load from attribution last 30d. Specialist Sharpe > 1.2 → 65; < 0.8 → 82. FINAL FIX: VETO_CONFIDENCE_THRESHOLD from get_threshold."""
    base = get_threshold('VETO_CONFIDENCE_THRESHOLD', 70)
    regime = (regime or '').strip().upper()
    if regime in ('BEAR', 'VOLATILE'):
        return 65
    try:
        if get_model_attribution is None:
            return max(70, base)
        att = get_model_attribution(30)
        by_model = att.get('by_model', {})
        specialist_confirm = by_model.get('Specialist (CONFIRM)', {})
        sharpe = float(specialist_confirm.get('sharpe', 0) or 0)
        if sharpe > 1.2:
            return 65
        if sharpe < 0.8 and specialist_confirm.get('trades', 0) >= 3:
            return 82
    except Exception:
        logger.exception("Error in _get_dynamic_veto_threshold")
    return max(70, base)


def apply_veto_override(consensus_result: Dict[str, Any], veto_result: Dict[str, Any], regime_data: Optional[Dict[str, Any]] = None) -> None:
    """
    If veto_result says VETO and confidence >= threshold, set consensus_result to HOLD.
    Confidence weighting: in BEAR/VOLATILE use lower threshold (65); in BULL/SIDEWAYS use dynamic threshold from attribution.
    Mutates consensus_result in place.
    """
    if not consensus_result or (veto_result.get('veto_decision') or 'CONFIRM') != 'VETO':
        return
    conf = int(veto_result.get('veto_confidence', 0))
    regime = ((regime_data or {}).get('regime', '') or '').strip()
    threshold = _get_dynamic_veto_threshold(regime)
    if conf < threshold:
        return
    reason = (veto_result.get('veto_reason') or '').strip()
    if not reason:
        return
    consensus_result['consensus_signal'] = 'HOLD'
    consensus_result['veto_reason'] = f"Specialist: {reason}"
    existing = consensus_result.get('reasoning') or ''
    consensus_result['reasoning'] = f"{existing} | [Veto Specialist] {reason}".strip()
