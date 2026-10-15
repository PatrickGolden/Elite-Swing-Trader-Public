# model_performance_tracker.py — Log predictions and outcomes for accuracy and calibration
# Stores in model_performance.json; quant-first model attribution telemetry.

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

PERF_FILE = os.path.join(os.path.dirname(__file__), 'model_performance.json')


def _normalize_models(_models: Optional[List[str]]) -> List[str]:
    """
    Quant-first hygiene: model labels should not carry deprecated LLM names.
    Keep a stable canonical label for all prediction records.
    """
    return ['Quant']


_TEST_COIN_ID_PATTERN = re.compile(r'^coin\d+$', re.IGNORECASE)
_TEST_SYMBOL_PATTERN = re.compile(r'^C\d+$')
_VALID_REGIMES = frozenset(['BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE'])


def _is_test_artifact(entry: Dict[str, Any]) -> bool:
    """Return True for entries that are clearly test fixtures (coin3, C4, etc.)."""
    coin_id = entry.get('coin_id', '')
    symbol = entry.get('symbol', '')
    return bool(_TEST_COIN_ID_PATTERN.match(coin_id) or _TEST_SYMBOL_PATTERN.match(symbol))


def _load() -> Dict[str, Any]:
    import safe_json
    data = safe_json.load(PERF_FILE, default=dict)
    if not data or not isinstance(data, dict):
        return {'predictions': [], 'outcomes': [], 'updated': None}
    data.setdefault('predictions', [])
    data.setdefault('outcomes', [])

    # One-time idempotent scrub: remove test fixture entries (coin3, C4, etc.)
    # that contaminate regime expectancy and calibration calculations.
    before = len(data['predictions'])
    data['predictions'] = [p for p in data['predictions'] if not _is_test_artifact(p)]
    scrubbed = before - len(data['predictions'])
    if scrubbed > 0:
        logger.info("[model_performance_tracker] Scrubbed %d test artifact entries from predictions", scrubbed)

    # Backward-compatible hygiene for legacy records.
    for p in data.get('predictions', []):
        p['models'] = _normalize_models(p.get('models'))
    return data


def _save(data: Dict[str, Any]) -> None:
    """Atomic save via safe_json to prevent corruption."""
    data['updated'] = datetime.now(timezone.utc).isoformat()
    import safe_json
    if not safe_json.save(PERF_FILE, data, default=str):
        logger.error("[model_performance_tracker] CRITICAL: Failed to save %s", PERF_FILE)


def log_prediction(
    coin_id: str,
    symbol: str,
    signal: str,
    confidence: float,
    regime: str,
    models: Optional[List[str]] = None,
    veto_decision: Optional[str] = None,
) -> Optional[str]:
    """Log a prediction for later outcome tracking. Returns prediction_ts for matching on close."""
    if regime not in _VALID_REGIMES:
        # UNKNOWN/PRE_PIPELINE entries are excluded from regime_expectancy in the optimizer,
        # weakening the SIDEWAYS relaxation gate. Surface this prominently so callers fix it.
        logger.warning(
            "[model_performance_tracker] regime='%s' for %s — not in %s. "
            "This entry will be excluded from regime-expectancy gate calculations. "
            "Ensure market_regime is set before calling open_position().",
            regime, symbol, sorted(_VALID_REGIMES),
        )
    data = _load()
    ts = datetime.now(timezone.utc).isoformat()
    entry = {
        'ts': ts,
        'coin_id': coin_id,
        'symbol': symbol,
        'signal': signal,
        'confidence': confidence,
        'regime': regime,
        'models': _normalize_models(models),
        'veto_decision': veto_decision,
    }
    data.setdefault('predictions', []).append(entry)
    if len(data['predictions']) > 500:
        dropped = len(data['predictions']) - 500
        logger.info("[model_performance_tracker] Truncating predictions: dropping %d oldest (keeping 500)", dropped)
        data['predictions'] = data['predictions'][-500:]
    _save(data)
    return ts


def log_outcome(prediction_ts: str, coin_id: str, pct_change_3d: float, pct_change_7d: float) -> bool:
    """Log outcome for a prior prediction. Returns True if matched."""
    data = _load()
    for p in data.get('predictions', []):
        if p.get('coin_id') == coin_id and p.get('ts') == prediction_ts:
            if 'outcome_3d' not in p:
                p['outcome_3d'] = pct_change_3d
                p['outcome_7d'] = pct_change_7d
                _save(data)
                return True
    return False


def record_trade_outcome(
    coin_id: str,
    symbol: str,
    signal: str,
    confidence: float,
    regime: str,
    pnl_pct: float,
    pred_ts: Optional[str] = None,
) -> bool:
    """
    Record outcome for a closed trade. Matches existing prediction by pred_ts+coin_id;
    creates a synthetic entry when the original was rotated out of the 500-entry buffer.
    Always succeeds (returns True) so callers can detect whether a match was found
    via the 'outcome_source' field in the resulting record.
    """
    data = _load()
    if pred_ts:
        for p in data.get('predictions', []):
            if p.get('coin_id') == coin_id and p.get('ts') == pred_ts:
                if 'outcome_3d' not in p:
                    p['outcome_3d'] = pnl_pct
                    p['outcome_7d'] = pnl_pct
                    p['outcome_source'] = 'trade_close'
                    _save(data)
                return True  # already recorded or now recorded — either way done

    # Original prediction rotated out — synthesize so calibration has real trade outcomes
    ts = pred_ts or datetime.now(timezone.utc).isoformat()
    entry = {
        'ts': ts,
        'coin_id': coin_id,
        'symbol': symbol,
        'signal': signal,
        'confidence': confidence,
        'regime': regime,
        'models': ['Quant'],
        'veto_decision': None,
        'outcome_3d': pnl_pct,
        'outcome_7d': pnl_pct,
        'outcome_source': 'trade_close_synthetic',
    }
    data.setdefault('predictions', []).append(entry)
    if len(data['predictions']) > 500:
        # Keep synthetic trade outcomes: they are at the end (just appended) so
        # trimming the oldest 200-300 refresh-scan entries is exactly what we want.
        data['predictions'] = data['predictions'][-500:]
    _save(data)
    logger.info("[model_performance_tracker] synthetic outcome recorded: %s %s pnl=%.2f%%", symbol, coin_id, pnl_pct)
    return True


def get_model_accuracy(signal_filter: Optional[str] = None) -> Dict[str, float]:
    """
    Compute accuracy metrics for predictions with outcomes.
    signal_filter: 'BUY', 'ULTRA_BUY', etc. or None for all.
    Returns: {win_rate_3d, win_rate_7d, count, avg_confidence}
    """
    data = _load()
    preds = [p for p in data.get('predictions', []) if 'outcome_3d' in p]
    if signal_filter:
        preds = [p for p in preds if signal_filter in (p.get('signal') or '')]
    if not preds:
        return {'win_rate_3d': 0.0, 'win_rate_7d': 0.0, 'count': 0, 'avg_confidence': 0.0}
    wins_3d = sum(1 for p in preds if float(p.get('outcome_3d', 0)) > 0)
    wins_7d = sum(1 for p in preds if float(p.get('outcome_7d', 0)) > 0)
    confs = [float(p.get('confidence', 0)) for p in preds]
    return {
        'win_rate_3d': wins_3d / len(preds),
        'win_rate_7d': wins_7d / len(preds),
        'count': len(preds),
        'avg_confidence': sum(confs) / len(confs) if confs else 0,
    }


def get_calibration_curve(bins: int = 5) -> List[Dict[str, Any]]:
    """
    Bin predictions by confidence and compute actual win rate per bin.
    Returns list of {bin_low, bin_high, predicted_conf, actual_win_rate, count}.
    """
    data = _load()
    preds = [p for p in data.get('predictions', []) if 'outcome_7d' in p]
    if not preds:
        return []
    step = 100.0 / bins
    result = []
    for i in range(bins):
        lo, hi = i * step, (i + 1) * step
        subset = [p for p in preds if lo <= float(p.get('confidence', 0)) < hi]
        if not subset:
            result.append({'bin_low': lo, 'bin_high': hi, 'predicted_conf': (lo + hi) / 2, 'actual_win_rate': 0, 'count': 0})
        else:
            wins = sum(1 for p in subset if float(p.get('outcome_7d', 0)) > 0)
            result.append({
                'bin_low': lo, 'bin_high': hi,
                'predicted_conf': (lo + hi) / 2,
                'actual_win_rate': wins / len(subset),
                'count': len(subset),
            })
    return result
