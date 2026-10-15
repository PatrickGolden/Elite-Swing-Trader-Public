# shap_analysis.py — SHAP feature importance for the regression scorer model.
# Runs after weekly regression retraining to explain which features drive win/loss predictions.
# Uses shap.LinearExplainer (exact for LogisticRegression, <1 sec).
# Output: data/shap_analysis/summary.json + optional PNG plots.

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path(os.path.dirname(__file__))
DATA_DIR = _PROJECT_DIR / 'data'
OUTPUT_DIR = DATA_DIR / 'shap_analysis'
MODEL_PATH = DATA_DIR / 'regression_model.pkl'
META_PATH = DATA_DIR / 'regression_model_meta.json'

# Minimum samples to run SHAP (matches regression scorer threshold)
MIN_SAMPLES_FOR_SHAP = 50


def run_shap_analysis(
    model_path: Optional[Path] = None,
    meta_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    generate_plots: bool = True,
) -> Dict[str, Any]:
    """Run SHAP analysis on the trained regression model.

    Returns dict with global importance, per-regime importance, and output paths.
    Returns {'success': False, 'reason': ...} on any failure.
    """
    model_path = model_path or MODEL_PATH
    meta_path = meta_path or META_PATH
    output_dir = output_dir or OUTPUT_DIR

    # --- Load model ---
    try:
        import joblib
        import numpy as np
    except ImportError as e:
        logger.warning("[shap_analysis] Missing dependency: %s", e)
        return {'success': False, 'reason': f'missing dependency: {e}'}

    if not model_path.exists():
        return {'success': False, 'reason': 'model not found'}
    if not meta_path.exists():
        return {'success': False, 'reason': 'model metadata not found'}

    try:
        import safe_json
        meta = safe_json.load(str(meta_path), default=None)
    except Exception:
        meta = None
    if meta is None:
        return {'success': False, 'reason': 'could not load model metadata'}

    n_samples = meta.get('n_samples', 0)
    if n_samples < MIN_SAMPLES_FOR_SHAP:
        return {'success': False, 'reason': f'insufficient samples ({n_samples} < {MIN_SAMPLES_FOR_SHAP})'}

    feature_names = meta.get('feature_names') or []
    if not feature_names:
        return {'success': False, 'reason': 'no feature_names in metadata'}

    try:
        model = joblib.load(model_path)
    except Exception as e:
        return {'success': False, 'reason': f'could not load model: {e}'}

    # --- Reconstruct training data ---
    X, trades = _load_training_data(meta)
    if X is None or len(X) < MIN_SAMPLES_FOR_SHAP:
        return {'success': False, 'reason': 'insufficient training data for SHAP'}

    # --- Compute SHAP values ---
    try:
        import shap
    except ImportError:
        return {'success': False, 'reason': 'shap not installed'}

    try:
        explainer = shap.LinearExplainer(model, X)
        shap_values = explainer.shap_values(X)
    except Exception as e:
        logger.warning("[shap_analysis] SHAP computation failed: %s", e)
        return {'success': False, 'reason': f'SHAP computation failed: {e}'}

    # --- Global importance ---
    global_importance = _compute_global_importance(shap_values, feature_names, np)

    # --- Per-regime importance ---
    regime_importance = _compute_regime_importance(shap_values, feature_names, trades, np)

    # --- Save results ---
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        'n_samples': len(X),
        'feature_names': feature_names,
        'global_importance': global_importance,
        'regime_importance': regime_importance,
        'generated_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
    }

    summary_path = output_dir / 'summary.json'
    try:
        safe_json.save(str(summary_path), summary)
    except Exception:
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)

    result = {
        'success': True,
        'n_samples': len(X),
        'global_importance': global_importance,
        'regime_importance': regime_importance,
        'output_files': [str(summary_path)],
    }

    # --- Optional plots ---
    if generate_plots:
        plot_paths = _save_plots(shap_values, X, feature_names, output_dir, trades, np, shap)
        result['output_files'].extend(plot_paths)

    logger.info("[shap_analysis] Complete: n_samples=%d, top_feature=%s",
                len(X), global_importance[0]['feature'] if global_importance else 'none')
    return result


def _load_training_data(meta: Dict[str, Any]) -> Tuple[Optional[Any], List[Dict]]:
    """Reconstruct the training feature matrix from closed positions.

    Returns (X as numpy array, trades list) or (None, []) on failure.
    """
    try:
        from model_attribution import _get_closed_positions_from_journal
        from regression_scorer import _trade_to_features, _rolling_score_stats
        import numpy as np
    except ImportError as e:
        logger.warning("[shap_analysis] Cannot load training data: %s", e)
        return None, []

    trades = _get_closed_positions_from_journal(90)
    trades = [t for t in trades if t.get('outcome') in ('win', 'loss')]
    if len(trades) < MIN_SAMPLES_FOR_SHAP:
        return None, trades

    rolling_stats = _rolling_score_stats(trades)
    X_list = []
    valid_trades = []
    for t in trades:
        feat = _trade_to_features(t, rolling_stats)
        if feat is not None:
            X_list.append(feat)
            valid_trades.append(t)

    if len(X_list) < MIN_SAMPLES_FOR_SHAP:
        return None, valid_trades

    return np.array(X_list, dtype=float), valid_trades


def _compute_global_importance(
    shap_values: Any,
    feature_names: List[str],
    np: Any,
) -> List[Dict[str, Any]]:
    """Compute mean |SHAP| per feature, sorted descending."""
    mean_abs = np.abs(shap_values).mean(axis=0)
    total = mean_abs.sum()
    importance = []
    for i, name in enumerate(feature_names):
        importance.append({
            'feature': name,
            'mean_abs_shap': round(float(mean_abs[i]), 6),
            'pct_contribution': round(float(mean_abs[i] / total * 100), 2) if total > 0 else 0.0,
        })
    importance.sort(key=lambda x: x['mean_abs_shap'], reverse=True)
    return importance


def _compute_regime_importance(
    shap_values: Any,
    feature_names: List[str],
    trades: List[Dict],
    np: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """Compute per-regime feature importance by slicing SHAP values."""
    regime_map = {
        'BULL': 'regime_bull',
        'BEAR': 'regime_bear',
        'SIDEWAYS': 'regime_sideways',
        'VOLATILE': 'regime_volatile',
    }
    result = {}
    for regime_name, dummy_col in regime_map.items():
        indices = [
            i for i, t in enumerate(trades)
            if (t.get('regime') or t.get('market_regime') or 'UNKNOWN').upper() == regime_name
        ]
        if len(indices) < 5:
            continue
        subset = shap_values[indices]
        mean_abs = np.abs(subset).mean(axis=0)
        total = mean_abs.sum()
        importance = []
        for j, name in enumerate(feature_names):
            importance.append({
                'feature': name,
                'mean_abs_shap': round(float(mean_abs[j]), 6),
                'pct_contribution': round(float(mean_abs[j] / total * 100), 2) if total > 0 else 0.0,
            })
        importance.sort(key=lambda x: x['mean_abs_shap'], reverse=True)
        result[regime_name] = importance
    return result


def _save_plots(
    shap_values: Any,
    X: Any,
    feature_names: List[str],
    output_dir: Path,
    trades: List[Dict],
    np: Any,
    shap: Any,
) -> List[str]:
    """Save SHAP summary plots to PNG files. Returns list of paths."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        logger.info("[shap_analysis] matplotlib not available — skipping plots")
        return []

    paths = []

    # Global summary plot
    try:
        plt.figure(figsize=(10, 6))
        shap.summary_plot(shap_values, X, feature_names=feature_names, show=False)
        path = str(output_dir / 'global_importance.png')
        plt.savefig(path, dpi=100, bbox_inches='tight')
        plt.close()
        paths.append(path)
    except Exception as e:
        logger.warning("[shap_analysis] Failed to save global plot: %s", e)

    # Per-regime plots
    for regime_name in ('BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE'):
        indices = [
            i for i, t in enumerate(trades)
            if (t.get('regime') or t.get('market_regime') or 'UNKNOWN').upper() == regime_name
        ]
        if len(indices) < 5:
            continue
        try:
            plt.figure(figsize=(10, 6))
            shap.summary_plot(shap_values[indices], X[indices], feature_names=feature_names, show=False)
            plt.title(f'SHAP Feature Importance — {regime_name} Regime')
            path = str(output_dir / f'regime_{regime_name.lower()}.png')
            plt.savefig(path, dpi=100, bbox_inches='tight')
            plt.close()
            paths.append(path)
        except Exception as e:
            logger.warning("[shap_analysis] Failed to save %s plot: %s", regime_name, e)

    return paths


def generate_shap_report() -> Dict[str, Any]:
    """CLI / scheduler entry point. Runs SHAP analysis with default paths."""
    return run_shap_analysis()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = generate_shap_report()
    if result.get('success'):
        print(f"SHAP analysis complete: {result.get('n_samples')} samples")
        print("Top features (global):")
        for feat in result.get('global_importance', [])[:5]:
            print(f"  {feat['feature']}: {feat['pct_contribution']}%")
    else:
        print(f"SHAP analysis failed: {result.get('reason')}")
