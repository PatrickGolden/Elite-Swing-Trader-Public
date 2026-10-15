# roadmap_dispatcher.py — Runs analysis modules gated by roadmap feature flags.
# Called after run_roadmap_cycle() in the weekly _optimizer_loop (Sunday 4 AM ET).
# Each module is isolated: one failure doesn't cascade to others.

import json as _json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from roadmap_automation import load_state

logger = logging.getLogger(__name__)

# Minimum improvement threshold for Optuna auto-apply (matches optuna_optimizer default)
OPTUNA_MIN_IMPROVEMENT_PCT = 2.0

# Persistence directory for gated analysis results
_DATA_DIR = Path(__file__).parent / 'data'
GATED_RESULTS_FILE = _DATA_DIR / 'gated_analysis_results.json'


def run_roadmap_gated_analyses(replay_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read roadmap state flags and conditionally run analysis modules.

    Returns dict of {module_name: result_dict} for each module that ran.
    Modules that aren't enabled by roadmap flags are silently skipped.
    """

    flags = load_state().get('state_flags', {})
    results: Dict[str, Any] = {}

    # SHAP weekly diagnostics (Stage A1: 30 trades + 90% attribution)
    if flags.get('shap_weekly_enabled'):
        try:
            from shap_analysis import run_shap_analysis
            logger.info("[roadmap-gated] Running SHAP analysis...")
            results['shap'] = run_shap_analysis()
        except Exception as e:
            logger.warning("[roadmap-gated] SHAP failed: %s", e)
            results['shap'] = {'success': False, 'error': str(e)}

    # VectorBT stop/target sweeps (Stage A2: 60 trades + positive expectancy)
    # Results saved for dashboard review — no auto-apply to live_thresholds.json
    if flags.get('vectorbt_weekly_enabled'):
        try:
            from vectorbt_sweeps import run_parameter_sweep
            logger.info("[roadmap-gated] Running VectorBT parameter sweep...")
            results['vectorbt'] = run_parameter_sweep(replay_result=replay_result)
        except Exception as e:
            logger.warning("[roadmap-gated] VectorBT failed: %s", e)
            results['vectorbt'] = {'success': False, 'error': str(e)}

    # Regression win-probability model (Stage B: 100 trades, auto_low_risk)
    # Trains regression_scorer.py weekly; pipeline falls back to additive scoring when absent.
    if flags.get('regression_model_enabled'):
        try:
            from regression_scorer import train_weekly_regression
            logger.info("[roadmap-gated] Training regression win-probability model...")
            results['regression_model'] = train_weekly_regression()
        except Exception as e:
            logger.warning("[roadmap-gated] Regression model training failed: %s", e)
            results['regression_model'] = {'success': False, 'error': str(e)}

    # Optuna joint multi-param optimization (Stage B: 100 trades, requires_approval)
    # Auto-applies to live_thresholds.json only if improvement >= 2%
    if flags.get('optuna_weekly_enabled'):
        try:
            from optuna_optimizer import run_optuna_study, apply_optuna_results
            logger.info("[roadmap-gated] Running Optuna study...")
            study = run_optuna_study()
            results['optuna'] = study
            if study.get('success') and (study.get('improvement_pct') or 0) >= OPTUNA_MIN_IMPROVEMENT_PCT:
                logger.info("[roadmap-gated] Optuna improvement %.1f%% >= %.1f%%, applying...",
                            study['improvement_pct'], OPTUNA_MIN_IMPROVEMENT_PCT)
                applied = apply_optuna_results(study)
                results['optuna_applied'] = applied
            elif study.get('success'):
                logger.info("[roadmap-gated] Optuna improvement %.1f%% < %.1f%%, skipping apply",
                            study.get('improvement_pct', 0), OPTUNA_MIN_IMPROVEMENT_PCT)
        except Exception as e:
            logger.warning("[roadmap-gated] Optuna failed: %s", e)
            results['optuna'] = {'success': False, 'error': str(e)}

    # dynamic_stop_baseline_enabled: no-op (attribution data collection is automatic)
    # dynamic_stop_optimizer_tuning_enabled: no-op (future: dynamic-stop-specific Optuna params)

    # Veto data integrity audit (Stage A2: 60 trades + 30 veto entries)
    if flags.get('veto_data_integrity_audit_enabled'):
        try:
            results['veto_integrity'] = _run_veto_integrity_audit()
        except Exception as e:
            logger.warning("[roadmap-gated] Veto integrity audit failed: %s", e)
            results['veto_integrity'] = {'success': False, 'error': str(e)}

    # Cash deployment efficiency tracking (Stage A1: 30 trades)
    if flags.get('cash_deployment_tracking_enabled'):
        try:
            results['cash_deployment'] = _run_cash_deployment_tracking()
        except Exception as e:
            logger.warning("[roadmap-gated] Cash deployment tracking failed: %s", e)
            results['cash_deployment'] = {'success': False, 'error': str(e)}

    # Gate score relaxation study (Stage B: 100 trades, read-only analysis)
    if flags.get('gate_score_relaxation_enabled'):
        try:
            results['gate_score_study'] = _run_gate_score_relaxation_study()
        except Exception as e:
            logger.warning("[roadmap-gated] Gate score relaxation study failed: %s", e)
            results['gate_score_study'] = {'success': False, 'error': str(e)}

    # trailing_stop_target_scaling_enabled: handled by VectorBT sweep parameter set
    # moderate_buy_accumulation_enabled: handled by app.py execution logic
    # atr_volatile_stops_enabled: handled by dynamic_targets.py at entry time

    # Persist all results so downstream consumers (managed agents, dashboard) can read them.
    # Previously these were returned but never saved — orphaned feedback loops.
    _persist_gated_results(results)

    return results


def _persist_gated_results(results: Dict[str, Any]) -> None:
    """Save gated analysis results to data/gated_analysis_results.json.

    Merges into existing file to preserve history from prior modules.
    Each module's results are timestamped for tracking across weeks.
    """
    ts = datetime.now(timezone.utc).isoformat()
    existing: Dict[str, Any] = {}
    if GATED_RESULTS_FILE.exists():
        try:
            with open(GATED_RESULTS_FILE, 'r', encoding='utf-8') as f:
                existing = _json.load(f)
        except (ValueError, _json.JSONDecodeError):
            existing = {}

    existing['timestamp'] = ts
    for module_name, result in results.items():
        existing[module_name] = result
        if isinstance(result, dict):
            result['last_run'] = ts

    try:
        with open(GATED_RESULTS_FILE, 'w', encoding='utf-8') as f:
            _json.dump(existing, f, indent=2, default=str)
        logger.info("[roadmap-gated] Persisted %d module results to %s", len(results), GATED_RESULTS_FILE.name)
    except OSError as e:
        logger.warning("[roadmap-gated] Failed to persist results: %s", e)


def _run_veto_integrity_audit() -> Dict[str, Any]:
    """Scan veto_log.jsonl for synthetic-looking entries and report proportion."""
    import json as _json
    from pathlib import Path

    veto_path = Path(__file__).parent / 'data' / 'veto_log.jsonl'
    if not veto_path.exists():
        return {'success': True, 'total': 0, 'suspicious': 0, 'pct': 0.0}

    total = 0
    suspicious = 0
    with open(veto_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            total += 1
            flags_hit = 0
            if entry.get('current_price') is None:
                flags_hit += 1
            if entry.get('score') == 0:
                flags_hit += 1
            fg = entry.get('fear_greed')
            if fg in (50, 85, 0):
                flags_hit += 1
            if flags_hit >= 2:
                suspicious += 1

    pct = round(100.0 * suspicious / total, 1) if total else 0.0
    result = {
        'success': True,
        'total': total,
        'suspicious': suspicious,
        'suspicious_pct': pct,
        'needs_review': pct > 25.0,
    }
    if pct > 25.0:
        logger.warning(
            "[roadmap-gated] Veto integrity: %.1f%% of %d vetoes appear synthetic (score=0, price=null, round F&G)",
            pct, total,
        )
    else:
        logger.info("[roadmap-gated] Veto integrity: %.1f%% suspicious out of %d vetoes", pct, total)
    return result


def _run_cash_deployment_tracking() -> Dict[str, Any]:
    """Compute idle-capital metrics from positions.json."""
    import json as _json
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    positions_path = Path(__file__).parent / 'positions.json'
    positions = []
    if positions_path.exists():
        with open(positions_path, 'r', encoding='utf-8') as f:
            positions = _json.load(f)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)

    open_intervals = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        entry_str = p.get('entry_time')
        if not entry_str:
            continue
        try:
            entry_t = datetime.fromisoformat(str(entry_str).replace('Z', '+00:00'))
            if entry_t.tzinfo is None:
                entry_t = entry_t.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        exit_str = p.get('exit_time')
        if exit_str:
            try:
                exit_t = datetime.fromisoformat(str(exit_str).replace('Z', '+00:00'))
                if exit_t.tzinfo is None:
                    exit_t = exit_t.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                exit_t = now
        else:
            exit_t = now
        if exit_t < cutoff:
            continue
        open_intervals.append((max(entry_t, cutoff), min(exit_t, now)))

    total_hours = (now - cutoff).total_seconds() / 3600.0
    deployed_hours = 0.0
    for start, end in open_intervals:
        deployed_hours += max(0.0, (end - start).total_seconds() / 3600.0)

    max_gap_hours = 0.0
    if open_intervals:
        sorted_intervals = sorted(open_intervals, key=lambda x: x[0])
        prev_end = cutoff
        for start, end in sorted_intervals:
            gap = (start - prev_end).total_seconds() / 3600.0
            if gap > max_gap_hours:
                max_gap_hours = gap
            if end > prev_end:
                prev_end = end
        final_gap = (now - prev_end).total_seconds() / 3600.0
        if final_gap > max_gap_hours:
            max_gap_hours = final_gap

    deployment_pct = round(100.0 * min(deployed_hours, total_hours) / total_hours, 1) if total_hours > 0 else 0.0

    result = {
        'success': True,
        'period_hours': round(total_hours, 1),
        'deployed_hours': round(deployed_hours, 1),
        'deployment_pct': deployment_pct,
        'max_idle_gap_hours': round(max_gap_hours, 1),
    }
    logger.info(
        "[roadmap-gated] Cash deployment: %.1f%% deployed, max idle gap %.1fh over %.0fh window",
        deployment_pct, max_gap_hours, total_hours,
    )
    return result


def _run_gate_score_relaxation_study() -> Dict[str, Any]:
    """Replay screening_log outcome_7d data to estimate impact of lower gate scores."""
    import json as _json
    from pathlib import Path

    screening_path = Path(__file__).parent / 'data' / 'screening_log.json'
    if not screening_path.exists():
        return {'success': False, 'error': 'screening_log.json not found'}

    with open(screening_path, 'r', encoding='utf-8') as f:
        entries = _json.load(f)

    if not isinstance(entries, list):
        return {'success': False, 'error': 'invalid screening_log format'}

    holds_with_outcome = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get('quant_tier') != 'HOLD':
            continue
        outcome = e.get('outcome_7d')
        if outcome is None:
            continue
        holds_with_outcome.append({
            'symbol': e.get('symbol', ''),
            'outcome_7d': float(outcome),
            'entry_price': e.get('entry_price', 0),
        })

    if not holds_with_outcome:
        return {
            'success': True,
            'note': 'no HOLD entries with backfilled outcome_7d yet',
            'holds_analyzed': 0,
        }

    total = len(holds_with_outcome)
    profitable = sum(1 for h in holds_with_outcome if h['outcome_7d'] > 0)
    big_movers = [h for h in holds_with_outcome if h['outcome_7d'] >= 5.0]
    avg_outcome = round(sum(h['outcome_7d'] for h in holds_with_outcome) / total, 2)

    by_symbol: Dict[str, list] = {}
    for h in big_movers:
        by_symbol.setdefault(h['symbol'], []).append(h['outcome_7d'])
    top_missed = sorted(by_symbol.items(), key=lambda x: -max(x[1]))[:10]

    result = {
        'success': True,
        'holds_analyzed': total,
        'profitable_pct': round(100.0 * profitable / total, 1),
        'avg_outcome_7d_pct': avg_outcome,
        'big_movers_5pct_plus': len(big_movers),
        'top_missed_symbols': {sym: round(max(outcomes), 1) for sym, outcomes in top_missed},
    }
    logger.info(
        "[roadmap-gated] Gate score study: %d HOLDs analyzed, %.1f%% profitable, %d moved 5%%+",
        total, result['profitable_pct'], len(big_movers),
    )
    return result
