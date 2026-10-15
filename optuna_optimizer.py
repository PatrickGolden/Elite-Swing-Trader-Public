# optuna_optimizer.py — Bayesian multi-parameter threshold optimization via Optuna.
# Complements the existing recursive_optimizer counterfactual engine (opt-in, not replacement).
# Uses historical_replay as objective function with thread-local threshold overrides.
# Study results persisted to JSON (project rule: no databases).

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path(os.path.dirname(__file__))
DATA_DIR = _PROJECT_DIR / 'data'
STUDIES_DIR = DATA_DIR / 'optuna_studies'

# Safety constants (match recursive_optimizer)
MAX_CHANGES_PER_CYCLE = 3
DEFAULT_MIN_IMPROVEMENT_PCT = 2.0
STUDY_TIMEOUT_SECONDS = 1800  # 30 min safety cap
DEFAULT_N_TRIALS = 100

# Walk-forward split ratio
TRAIN_RATIO = 0.7


def _is_int_param(lo: Any, hi: Any) -> bool:
    """Determine if a parameter should be integer based on its bounds."""
    return isinstance(lo, int) and isinstance(hi, int)


def create_search_space(trial: Any) -> Dict[str, Any]:
    """Build Optuna search space from OPTIMIZER_BOUNDS.

    Returns dict of {param_name: suggested_value}.
    """
    from thresholds import OPTIMIZER_BOUNDS

    params = {}
    for name, (lo, hi) in OPTIMIZER_BOUNDS.items():
        if _is_int_param(lo, hi):
            params[name] = trial.suggest_int(name, lo, hi)
        else:
            params[name] = trial.suggest_float(name, float(lo), float(hi))
    return params


def replay_objective(
    trial: Any,
    days: int = 90,
    coins: Optional[List[Tuple[str, str]]] = None,
) -> float:
    """Optuna objective function: run historical replay with trial params, return profit factor.

    Returns profit_factor (higher is better). Returns 0.0 for failed replays.
    Uses TRAIN_RATIO to evaluate only the training window (excludes holdout).
    """
    params = create_search_space(trial)
    holdout_days = max(1, int(days * (1 - TRAIN_RATIO)))

    try:
        from historical_replay import run_historical_replay
        result = run_historical_replay(
            coins=coins,
            days=days - holdout_days,
            verbose=False,
            threshold_overrides=params,
            end_offset_days=holdout_days,
        )
    except Exception as e:
        logger.warning("[optuna] Replay failed for trial %d: %s", trial.number, e)
        return 0.0

    summary = result.get('summary', {})
    profit_factor = summary.get('profit_factor', 0.0)
    try:
        return float(profit_factor)
    except (TypeError, ValueError):
        return 0.0


def replay_objective_multi(
    trial: Any,
    days: int = 90,
    coins: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[float, float, float]:
    """Multi-objective: (win_rate, -max_drawdown, profit_factor).

    Optuna maximizes all objectives, so max_drawdown is negated.
    Uses TRAIN_RATIO to evaluate only the training window (excludes holdout).
    """
    params = create_search_space(trial)
    holdout_days = max(1, int(days * (1 - TRAIN_RATIO)))

    try:
        from historical_replay import run_historical_replay
        result = run_historical_replay(
            coins=coins, days=days - holdout_days, verbose=False, threshold_overrides=params,
            end_offset_days=holdout_days,
        )
    except Exception:
        return (0.0, -100.0, 0.0)

    summary = result.get('summary', {})
    win_rate = float(summary.get('win_rate_pct', 0.0))
    max_dd = float(summary.get('max_drawdown_pct', 100.0))
    profit_factor = float(summary.get('profit_factor', 0.0))
    return (win_rate, -max_dd, profit_factor)


def run_optuna_study(
    n_trials: int = DEFAULT_N_TRIALS,
    days: int = 90,
    multi_objective: bool = False,
    timeout_seconds: int = STUDY_TIMEOUT_SECONDS,
    coins: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Run an Optuna study for threshold optimization.

    Creates in-memory study (no SQLite — JSON persistence only).
    Returns study results including best params and validation metrics.
    """
    try:
        import optuna
    except ImportError:
        return {'success': False, 'reason': 'optuna not installed'}

    # Suppress Optuna's verbose trial logging
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    study_name = f"threshold_opt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    if multi_objective:
        study = optuna.create_study(
            study_name=study_name,
            directions=['maximize', 'maximize', 'maximize'],
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        objective = lambda trial: replay_objective_multi(trial, days=days, coins=coins)
    else:
        study = optuna.create_study(
            study_name=study_name,
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        objective = lambda trial: replay_objective(trial, days=days, coins=coins)

    logger.info("[optuna] Starting study '%s': n_trials=%d, timeout=%ds",
                study_name, n_trials, timeout_seconds)

    try:
        study.optimize(objective, n_trials=n_trials, timeout=timeout_seconds)
    except Exception as e:
        logger.error("[optuna] Study failed: %s", e)
        return {'success': False, 'reason': f'study failed: {e}'}

    # Extract results
    if multi_objective:
        # Pareto front: pick trial with best profit_factor among top Pareto trials
        pareto_trials = study.best_trials
        if not pareto_trials:
            return {'success': False, 'reason': 'no completed trials'}
        best = max(pareto_trials, key=lambda t: t.values[2])  # profit_factor
        best_params = best.params
        best_metrics = {
            'win_rate': best.values[0],
            'max_drawdown': -best.values[1],  # un-negate
            'profit_factor': best.values[2],
        }
    else:
        if study.best_trial is None:
            return {'success': False, 'reason': 'no completed trials'}
        best_params = study.best_trial.params
        best_metrics = {'profit_factor': study.best_trial.value}

    # Compare vs current thresholds
    from thresholds import get_threshold
    changes = []
    for param, new_val in best_params.items():
        current = get_threshold(param)
        if current is not None and new_val != current:
            changes.append({
                'param': param,
                'current': current,
                'proposed': new_val,
                'delta': round(new_val - current, 4) if isinstance(new_val, (int, float)) else None,
            })

    # Sort by absolute delta (biggest changes first)
    changes.sort(key=lambda c: abs(c.get('delta') or 0), reverse=True)

    # Compute baseline replay metrics for apply-time safety gates.
    baseline_metrics: Dict[str, float] = {}
    try:
        from historical_replay import run_historical_replay
        baseline_result = run_historical_replay(
            coins=coins,
            days=days,
            verbose=False,
            threshold_overrides=None,
        )
        baseline_summary = baseline_result.get('summary', {}) if isinstance(baseline_result, dict) else {}
        baseline_metrics = {
            'profit_factor': float(baseline_summary.get('profit_factor', 0.0) or 0.0),
            'win_rate_pct': float(baseline_summary.get('win_rate_pct', 0.0) or 0.0),
            'max_drawdown_pct': float(baseline_summary.get('max_drawdown_pct', 0.0) or 0.0),
        }
    except Exception as e:
        logger.warning("[optuna] Baseline replay unavailable: %s", e)
        baseline_metrics = {}

    improvement_pct = None
    try:
        best_pf = float(best_metrics.get('profit_factor', 0.0) or 0.0)
        base_pf = float(baseline_metrics.get('profit_factor', 0.0) or 0.0)
        if base_pf > 0:
            improvement_pct = round(((best_pf - base_pf) / base_pf) * 100.0, 3)
    except Exception:
        improvement_pct = None

    result = {
        'success': True,
        'study_name': study_name,
        'n_trials': len(study.trials),
        'n_completed': len([t for t in study.trials if t.state.name == 'COMPLETE']),
        'best_params': best_params,
        'best_metrics': best_metrics,
        'baseline_metrics': baseline_metrics,
        'improvement_pct': improvement_pct,
        'changes': changes,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    # Persist to JSON
    _save_study_results(result)

    logger.info("[optuna] Study complete: %d trials, profit_factor=%.3f",
                result['n_completed'], best_metrics.get('profit_factor', 0))
    return result


def apply_optuna_results(
    study_results: Dict[str, Any],
    max_changes: int = MAX_CHANGES_PER_CYCLE,
    min_improvement_pct: float = DEFAULT_MIN_IMPROVEMENT_PCT,
) -> List[Dict[str, Any]]:
    """PERMANENTLY DISARMED (STRATEGY.md §5.8 / audit F2, 2026-07-04).

    This was the last live automated writer of live_thresholds.json with NO
    freeze check: at >=2% simulated improvement it wrote Optuna-proposed
    thresholds directly. Same failure class as the disarmed optimizer
    auto-apply — simulated improvement on tiny samples is noise, and
    "protected by dormant flags" is state, not construction. Candidate
    changes now surface in the study artifact (data/optuna_studies/) for
    human review only; threshold changes go through apply_single /
    apply_recommendation or a registered trial that passes the gates.
    """
    changes = study_results.get('changes', [])
    if changes:
        logger.info(
            "[optuna] apply_optuna_results permanently disarmed (audit F2) — "
            "%d candidate change(s) recorded in study artifact for human review",
            len(changes),
        )
    return []


def _save_study_results(result: Dict[str, Any]) -> None:
    """Save study results to JSON file in data/optuna_studies/."""
    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    path = STUDIES_DIR / f'{date_str}.json'

    # Append to daily file (multiple studies per day possible)
    existing = []
    if path.exists():
        try:
            with open(path) as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = [existing]
        except Exception:
            existing = []

    # Clean non-serializable values from best_params
    clean_result = dict(result)
    if 'best_params' in clean_result:
        clean_result['best_params'] = {
            k: round(v, 6) if isinstance(v, float) else v
            for k, v in clean_result['best_params'].items()
        }

    existing.append(clean_result)
    try:
        import safe_json
        safe_json.save(str(path), existing)
    except Exception:
        with open(path, 'w') as f:
            json.dump(existing, f, indent=2)


def load_latest_study() -> Optional[Dict[str, Any]]:
    """Load the most recent study results from JSON."""
    if not STUDIES_DIR.exists():
        return None

    files = sorted(STUDIES_DIR.glob('*.json'), reverse=True)
    if not files:
        return None

    try:
        with open(files[0]) as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data[-1]  # Most recent study in the file
        return data
    except Exception:
        return None


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print("Running Optuna threshold optimization (50 trials)...")
    result = run_optuna_study(n_trials=50)
    if result.get('success'):
        print(f"Study complete: {result['n_completed']} trials")
        print(f"Best profit_factor: {result['best_metrics'].get('profit_factor', 0):.3f}")
        print("Top changes:")
        for c in result.get('changes', [])[:5]:
            print(f"  {c['param']}: {c['current']} → {c['proposed']}")
    else:
        print(f"Study failed: {result.get('reason')}")
