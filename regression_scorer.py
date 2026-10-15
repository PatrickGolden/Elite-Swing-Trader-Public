# regression_scorer.py — Weekly logistic regression on historical trades (win/loss)
# Replaces pure additive scoring with data-driven probability (Swing Trader v1.0).
# Features: base_score, ob_ratio, mtf_aligned, atr_pct, fear_greed, regime, quant_path,
#           score_z (z-score normalized by coin_class + regime over rolling 30d).
# Pure quant scoring — no LLM influence.
# If n_samples < MIN_TRADES_FOR_REGRESSION or model missing → return None (caller uses additive score + log warning).

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path(os.path.dirname(__file__))
DATA_DIR = _PROJECT_DIR / 'data'
MODEL_PATH = DATA_DIR / 'regression_model.pkl'
META_PATH = DATA_DIR / 'regression_model_meta.json'

MIN_TRADES_FOR_REGRESSION = 50  # Below this: do not use model; fall back to additive score (71 live trades available as of 2026-05-30)
ROLLING_DAYS = 30
FEATURE_ORDER = [
    'base_score', 'ob_ratio', 'mtf_aligned', 'atr_pct', 'fear_greed',
    'regime_bull', 'regime_bear', 'regime_sideways', 'regime_volatile',
    'path_dip', 'path_blood', 'path_pullback', 'path_momentum', 'path_default',
    'score_z', 'ath_distance', 'fdv_ratio',
]


def _coalesce_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _serialize_score_stats(stats: Dict[Tuple[str, str], Tuple[float, float]]) -> Dict[str, List[float]]:
    return {f"{coin_class}|{regime}": [float(mean), float(std)] for (coin_class, regime), (mean, std) in stats.items()}


def _deserialize_score_stats(raw: Any) -> Dict[Tuple[str, str], Tuple[float, float]]:
    out: Dict[Tuple[str, str], Tuple[float, float]] = {}
    if not isinstance(raw, dict):
        return out
    for key, values in raw.items():
        if not isinstance(key, str) or "|" not in key or not isinstance(values, (list, tuple)) or len(values) < 2:
            continue
        coin_class, regime = key.split("|", 1)
        try:
            out[(coin_class, regime)] = (float(values[0]), float(values[1]))
        except (TypeError, ValueError):
            continue
    return out


def _coin_class(symbol: str, coin_id: str) -> str:
    """BTC / ETH / large / mid / small for z-score grouping."""
    s = (symbol or '').strip().upper()
    c = (coin_id or '').strip().lower()
    if s in ('BTC', 'BITCOIN') or 'bitcoin' in c:
        return 'btc'
    if s in ('ETH', 'ETHEREUM') or 'ethereum' in c:
        return 'eth'
    # Could extend with market_cap from coin; default mid
    return 'mid'


def _rolling_score_stats(trades: List[Dict], exit_time_key: str = 'exit_time') -> Dict[Tuple[str, str], Tuple[float, float]]:
    """Per (coin_class, regime) compute mean and std of base_score over rolling 30d by exit date."""
    from collections import defaultdict
    # Group by (coin_class, regime), collect (exit_date, score)
    by_key: Dict[Tuple[str, str], List[Tuple[str, float]]] = defaultdict(list)
    for t in trades:
        score = t.get('base_score')
        if score is None:
            score = t.get('confirmation_score')
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        exit_time = (t.get(exit_time_key) or t.get('ts') or '')[:10]
        if not exit_time:
            continue
        regime = (t.get('regime') or 'UNKNOWN').upper()
        coin_class = t.get('coin_class') or _coin_class(t.get('coin_symbol') or '', t.get('coin_id') or '')
        by_key[(coin_class, regime)].append((exit_time, score))
    # For each (coin_class, regime) compute overall mean/std (simplified: use all points in window)
    out = {}
    for key, pairs in by_key.items():
        if len(pairs) < 2:
            scores = [p[1] for p in pairs]
            mean = scores[0] if scores else 50.0
            std = 1.0
        else:
            scores = [p[1] for p in pairs]
            mean = sum(scores) / len(scores)
            variance = sum((x - mean) ** 2 for x in scores) / len(scores)
            std = (variance ** 0.5) or 1.0
        out[key] = (mean, std)
    return out


def _trade_to_features(t: Dict, rolling_stats: Optional[Dict[Tuple[str, str], Tuple[float, float]]] = None) -> Optional[List[float]]:
    """Build feature vector for one trade. Returns None if critical fields missing."""
    rf = t.get('regression_features') or {}
    base_score = rf.get('base_score')
    if base_score is None:
        base_score = t.get('confirmation_score')
    if base_score is None:
        return None
    try:
        base_score = float(base_score)
    except (TypeError, ValueError):
        return None
    ob_ratio = float(rf.get('ob_ratio') or 1.0)
    mtf_aligned = 1.0 if rf.get('mtf_aligned') else 0.0
    atr_pct = float(rf.get('atr_pct') or t.get('atr_pct') or 2.0)
    fear_greed_raw = _coalesce_not_none(rf.get('fear_greed'), t.get('fear_greed'), 50)
    fear_greed = float(fear_greed_raw)
    regime = (t.get('regime') or 'UNKNOWN').upper()
    quant_path = (t.get('quant_path') or 'default').strip() or 'default'
    coin_class = rf.get('coin_class') or _coin_class(t.get('coin_symbol') or '', t.get('coin_id') or '')

    regime_bull = 1.0 if regime == 'BULL' else 0.0
    regime_bear = 1.0 if regime == 'BEAR' else 0.0
    regime_sideways = 1.0 if regime == 'SIDEWAYS' else 0.0
    regime_volatile = 1.0 if regime == 'VOLATILE' else 0.0
    path_dip = 1.0 if quant_path == 'dip' else 0.0
    path_blood = 1.0 if quant_path == 'blood_in_the_street' else 0.0
    path_pullback = 1.0 if quant_path == 'pullback_in_trend' else 0.0
    path_momentum = 1.0 if quant_path in ('momentum_breakout', 'early_momentum') else 0.0
    path_default = 1.0 if quant_path == 'default' else 0.0

    score_z = 0.0
    if rolling_stats:
        key = (coin_class, regime)
        mean, std = rolling_stats.get(key, (50.0, 1.0))
        score_z = (base_score - mean) / std if std > 0 else 0.0
        score_z = max(-3.0, min(3.0, score_z))

    ath_raw = _coalesce_not_none(rf.get('ath_change_pct'), t.get('ath_change_pct'))
    ath_distance = 0.5
    if ath_raw is not None:
        try:
            ath_distance = max(0.0, min(1.0, 1.0 + float(ath_raw) / 100.0))
        except (TypeError, ValueError):
            pass
    fdv_ratio_val = float(_coalesce_not_none(rf.get('fdv_ratio'), t.get('fdv_ratio')) or 1.0)
    fdv_ratio_val = max(0.0, min(10.0, fdv_ratio_val))

    return [
        base_score, ob_ratio, mtf_aligned, atr_pct, fear_greed,
        regime_bull, regime_bear, regime_sideways, regime_volatile,
        path_dip, path_blood, path_pullback, path_momentum, path_default,
        score_z, ath_distance, fdv_ratio_val,
    ]


def train_weekly_regression(days: int = 90) -> Dict[str, Any]:
    """Train LogisticRegression on closed journal trades. Save model + metadata. Returns summary."""
    try:
        from model_attribution import _get_closed_positions_from_journal
    except ImportError:
        logger.warning("[regression_scorer] model_attribution not available")
        return {'trained': False, 'error': 'model_attribution not available', 'n_samples': 0}
    trades = _get_closed_positions_from_journal(days)
    trades = [t for t in trades if t.get('outcome') in ('win', 'loss')]
    if len(trades) < MIN_TRADES_FOR_REGRESSION:
        logger.info("[regression_scorer] n_samples=%s < %s — skipping train (use additive score until enough data)",
                    len(trades), MIN_TRADES_FOR_REGRESSION)
        return {'trained': False, 'n_samples': len(trades), 'min_required': MIN_TRADES_FOR_REGRESSION}

    rolling_stats = _rolling_score_stats(trades)
    X_list = []
    y_list = []
    for t in trades:
        feat = _trade_to_features(t, rolling_stats)
        if feat is None:
            continue
        X_list.append(feat)
        y_list.append(1 if t.get('outcome') == 'win' else 0)
    if len(X_list) < MIN_TRADES_FOR_REGRESSION:
        return {'trained': False, 'n_samples': len(X_list), 'min_required': MIN_TRADES_FOR_REGRESSION}

    try:
        from sklearn.linear_model import LogisticRegression
        import numpy as np
    except ImportError:
        return {'trained': False, 'error': 'sklearn not installed', 'n_samples': len(X_list)}

    X = np.array(X_list, dtype=float)
    y = np.array(y_list, dtype=int)
    model = LogisticRegression(max_iter=500, class_weight='balanced', random_state=42)
    model.fit(X, y)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump(model, MODEL_PATH)
    meta = {
        'trained_at': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
        'n_samples': len(X_list),
        'feature_names': FEATURE_ORDER,
        'min_trades_for_regression': MIN_TRADES_FOR_REGRESSION,
        'score_z_stats': _serialize_score_stats(rolling_stats),
    }
    import safe_json
    safe_json.save(str(META_PATH), meta)
    logger.info("[regression_scorer] Trained on n_samples=%s", len(X_list))

    # Run SHAP analysis after successful training
    try:
        from shap_analysis import run_shap_analysis
        shap_result = run_shap_analysis()
        if shap_result.get('success'):
            logger.info("[regression_scorer] SHAP analysis complete: top_feature=%s",
                        shap_result['global_importance'][0]['feature'] if shap_result.get('global_importance') else 'none')
        else:
            logger.info("[regression_scorer] SHAP analysis skipped: %s", shap_result.get('reason', 'unknown'))
    except Exception as e:
        logger.warning("[regression_scorer] SHAP analysis failed: %s", e)

    return {'trained': True, 'n_samples': len(X_list), 'meta': meta}


def get_regression_score(
    coin: Dict[str, Any],
    regime_data: Dict[str, Any],
    fear_greed_value: int,
    quant_path: str,
    base_score: float,
    ob_ratio: Optional[float] = None,
    mtf_aligned: bool = False,
    atr_pct: float = 2.0,
) -> Optional[float]:
    """
    Return P(win) 0–1 from regression model, or None if model missing or n_samples < MIN_TRADES_FOR_REGRESSION.
    Caller must fall back to additive score and log warning when None.
    """
    try:
        import joblib
        import numpy as np
    except ImportError:
        return None
    if not MODEL_PATH.exists() or not META_PATH.exists():
        return None
    import safe_json
    meta = safe_json.load(str(META_PATH), default=None)
    if meta is None:
        return None
    n_samples = meta.get('n_samples', 0)
    if n_samples < MIN_TRADES_FOR_REGRESSION:
        return None
    # Guard: stale model with different feature schema/order
    saved_features = meta.get('feature_names') or []
    if saved_features != FEATURE_ORDER:
        logger.info("[regression_scorer] feature schema mismatch — retrain needed")
        return None
    try:
        model = joblib.load(MODEL_PATH)
    except Exception:
        return None
    regime = (regime_data or {}).get('regime', 'UNKNOWN')
    regime = (regime or 'UNKNOWN').upper()
    coin_class = _coin_class(coin.get('symbol') or '', coin.get('id') or '')
    ob_ratio = float(ob_ratio if ob_ratio is not None else 1.0)
    regime_bull = 1.0 if regime == 'BULL' else 0.0
    regime_bear = 1.0 if regime == 'BEAR' else 0.0
    regime_sideways = 1.0 if regime == 'SIDEWAYS' else 0.0
    regime_volatile = 1.0 if regime == 'VOLATILE' else 0.0
    path_dip = 1.0 if quant_path == 'dip' else 0.0
    path_blood = 1.0 if quant_path == 'blood_in_the_street' else 0.0
    path_pullback = 1.0 if quant_path == 'pullback_in_trend' else 0.0
    path_momentum = 1.0 if quant_path in ('momentum_breakout', 'early_momentum') else 0.0
    path_default = 1.0 if quant_path == 'default' else 0.0
    score_stats = _deserialize_score_stats(meta.get('score_z_stats'))
    mean, std = score_stats.get((coin_class, regime), (50.0, 1.0))
    score_z = (float(base_score) - mean) / std if std > 0 else 0.0
    score_z = max(-3.0, min(3.0, score_z))
    ath_raw = coin.get('ath_change_percentage')
    ath_distance = 0.5
    if ath_raw is not None:
        try:
            ath_distance = max(0.0, min(1.0, 1.0 + float(ath_raw) / 100.0))
        except (TypeError, ValueError):
            pass
    fdv = coin.get('fully_diluted_valuation')
    mc = coin.get('market_cap')
    fdv_ratio_val = 1.0
    if fdv and mc and float(mc) > 0:
        try:
            fdv_ratio_val = max(0.0, min(10.0, float(fdv) / float(mc)))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    features = [
        base_score, ob_ratio, 1.0 if mtf_aligned else 0.0, atr_pct, float(fear_greed_value),
        regime_bull, regime_bear, regime_sideways, regime_volatile,
        path_dip, path_blood, path_pullback, path_momentum, path_default,
        score_z, ath_distance, fdv_ratio_val,
    ]
    X = np.array([features], dtype=float)
    try:
        proba = model.predict_proba(X)[0][1]
        return float(proba)
    except Exception:
        return None


def get_regression_meta() -> Optional[Dict[str, Any]]:
    """Return metadata (n_samples, etc.) for caller to decide fallback."""
    import safe_json
    return safe_json.load(str(META_PATH), default=None)
