# recursive_optimizer.py — Recursive quant gate optimizer
# Runs daily: aggregates trade outcomes, performs counterfactual threshold analysis,
# auto-applies safe changes to live_thresholds.json, tracks cycle impact, emails report.
# Consumes existing pipeline outputs — does NOT re-implement backtest/missed-opp/attribution.

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from safety_gate import run_unit_safety_gate

logger = logging.getLogger(__name__)

_cycle_lock = threading.Lock()

_PROJECT_DIR = Path(os.path.dirname(__file__))
DATA_DIR = _PROJECT_DIR / 'data'
OPTIMIZER_LOG_FILE = DATA_DIR / 'optimizer_log.json'
FOLLOWUPS_FILE = DATA_DIR / 'optimizer_followups.json'
RECOMMENDATIONS_LOG_FILE = DATA_DIR / 'recommendations_log.json'
LEARNING_REPORT_FILE = DATA_DIR / 'learning_report.json'
STRESS_REPORT_FILE = DATA_DIR / 'buy_signals_stress_30d.json'
# Legacy; optimizer no longer reads this (single source = journal). Kept for test env patching.
ATTRIBUTION_FILE = str(_PROJECT_DIR / 'model_attribution.json')
BACKTEST_TRADES_FILE = DATA_DIR / 'backtest_trades.json'
ADVERSARIAL_REPORT_FILE = DATA_DIR / 'adversarial_report.json'
BACKTEST_WEIGHT = 0.5  # Backtest trades count as 0.5x weight vs live trades
PAPER_TRADE_WEIGHT = 0.75  # Simulated paper trades: supplementary evidence weight vs live

# MIN_TRADES_FOR_REC: min evidence trades to surface a recommendation.
# MIN_TRADES_FOR_AUTO: min evidence trades for full auto-apply — raised to 15
# to reduce multiple-comparisons risk at low trade volumes.
MIN_TRADES_FOR_REC = 15
MIN_TRADES_FOR_AUTO = 15
MIN_TRADES_FOR_PERF_TABLE = 3
MAX_AUTO_CHANGES_PER_CYCLE = 3
MATURITY_TRADE_TARGET = 100  # fallback when live thresholds unavailable
REVERT_DROP_THRESHOLD = 5.0   # percentage-point drop to trigger auto-revert
WATCH_DROP_THRESHOLD = 1.0
DEFAULT_MIN_IMPROVEMENT = 0.02  # min net expected P&L improvement (e.g. 0.02 = 2%) for full-auto apply


def _get_optimizer_threshold_int(key: str, fallback: int) -> int:
    """Read integer optimizer threshold from live thresholds with safe fallback."""
    try:
        from thresholds import get_threshold
        value = get_threshold(key, fallback)
        return int(value if value is not None else fallback)
    except Exception:
        return int(fallback)


# Lock for serializing writes to live_thresholds.json
_thresholds_write_lock = threading.Lock()


def get_data_collection_status() -> Dict[str, Any]:
    """Freeze state for optimizer and UI. Paper closes are tracked but excluded from lift gates.

    Returns dict with: frozen, live_closed, paper_closed, instrumented_live, raw_target,
    instrumented_target (optional int).
    """
    from thresholds import LIVE_THRESHOLDS_PATH
    lt = _load_json(LIVE_THRESHOLDS_PATH, default={})
    status: Dict[str, Any] = {
        'frozen': False,
        'live_closed': 0,
        'paper_closed': 0,
        'instrumented_live': 0,
        'raw_target': MATURITY_TRADE_TARGET,
        'instrumented_target': None,
    }
    if not lt.get('freeze_thresholds'):
        return status

    raw_target = int(lt.get('freeze_until_trades', MATURITY_TRADE_TARGET))
    instrumented_target = lt.get('freeze_until_instrumented_trades')
    status['raw_target'] = raw_target
    status['instrumented_target'] = int(instrumented_target) if instrumented_target is not None else None

    live_closed = 0
    paper_closed = 0
    instrumented_live = 0
    try:
        from trade_journal import get_all_positions
        for p in get_all_positions(exclude_test=False):
            if (p.get('status') or '').upper() != 'CLOSED':
                continue
            if p.get('is_test'):
                continue
            if p.get('source') == 'paper_trade':
                paper_closed += 1
            else:
                live_closed += 1
                if p.get('fear_greed') is not None:
                    instrumented_live += 1
    except Exception:
        pass

    status['live_closed'] = live_closed
    status['paper_closed'] = paper_closed
    status['instrumented_live'] = instrumented_live

    raw_gate_met = live_closed >= raw_target
    instrumented_gate_met = (
        instrumented_target is not None and instrumented_live >= int(instrumented_target)
    )

    # STRATEGY.md 2026-06-11: lifting the freeze is a deliberate human action, never a
    # side effect of reading status. Auto-apply on a book of legacy losing trades is how
    # the optimizer previously fit noise; the gate target being reached only means
    # "enough data to DISCUSS lifting", so we report it and keep the freeze in place.
    if raw_gate_met or instrumented_gate_met:
        lift_reason = (
            f"instrumented live trades {instrumented_live} >= target {instrumented_target}"
            if instrumented_gate_met and not raw_gate_met
            else f"live closed trades {live_closed} >= target {raw_target}"
        )
        status['lift_eligible'] = True
        status['lift_reason'] = lift_reason
        logger.info(
            "[optimizer] Data collection target reached (%s) — freeze REMAINS active; "
            "lift manually by removing freeze_thresholds from live_thresholds.json "
            "after the STRATEGY.md promotion gates pass.",
            lift_reason,
        )

    status['frozen'] = True
    return status


def is_data_collection_mode() -> tuple[bool, int, int]:
    """Return (frozen, live_closed_trades, raw_target). Paper trades excluded from the count."""
    st = get_data_collection_status()
    return st['frozen'], st['live_closed'], st['raw_target']


def _data_collection_cycle_fields() -> Dict[str, Any]:
    """Fields for cycle_data / email when thresholds are frozen."""
    st = get_data_collection_status()
    if not st['frozen']:
        return {'data_collection_mode': False}
    return {
        'data_collection_mode': True,
        'data_collection_closed_trades': st['live_closed'],
        'data_collection_target': st['raw_target'],
        'data_collection_paper_closed': st['paper_closed'],
        'data_collection_instrumented_live': st['instrumented_live'],
        'data_collection_instrumented_target': st['instrumented_target'],
    }


def _reload_thresholds_or_rollback(previous_file: Dict[str, Any], context: str) -> bool:
    """Reload in-memory thresholds; if reload fails, restore previous file and retry reload."""
    from thresholds import LIVE_THRESHOLDS_PATH, reload_live_thresholds
    try:
        reload_live_thresholds()
        return True
    except Exception as exc:
        logger.error("[optimizer] %s: reload failed (%s); rolling back threshold file", context, exc)
    with _thresholds_write_lock:
        _save_json(LIVE_THRESHOLDS_PATH, previous_file)
    try:
        reload_live_thresholds()
    except Exception as rollback_exc:
        logger.error("[optimizer] %s: rollback reload also failed (%s)", context, rollback_exc)
    return False


def validate_live_thresholds_bounds() -> List[Dict]:
    """Check all live thresholds against OPTIMIZER_BOUNDS. Clamp out-of-bounds values.
    Returns list of clamped parameters for logging/reporting.
    Skips writes during data collection mode — violations are flagged but not corrected."""
    from thresholds import LIVE_THRESHOLDS_PATH, OPTIMIZER_BOUNDS, reload_live_thresholds

    live = _load_json(LIVE_THRESHOLDS_PATH, default={})
    clamped = []
    changed = False

    for param, bounds in OPTIMIZER_BOUNDS.items():
        if param not in live:
            continue
        val = live[param]
        if not isinstance(val, (int, float)):
            continue
        lo, hi = bounds
        if val < lo:
            logger.warning("[optimizer] BOUNDS VIOLATION: %s=%s below min %s — clamping to %s", param, val, lo, lo)
            live[param] = lo
            clamped.append({'parameter': param, 'was': val, 'clamped_to': lo, 'bounds': bounds})
            changed = True
        elif val > hi:
            logger.warning("[optimizer] BOUNDS VIOLATION: %s=%s above max %s — clamping to %s", param, val, hi, hi)
            live[param] = hi
            clamped.append({'parameter': param, 'was': val, 'clamped_to': hi, 'bounds': bounds})
            changed = True

    if changed:
        frozen, closed, target = is_data_collection_mode()
        if frozen:
            logger.warning(
                "[optimizer] Bounds validation found %d violation(s) but data collection mode is active "
                "(%d/%d trades) — NOT writing clamps to avoid threshold mutation during freeze",
                len(clamped), closed, target,
            )
            return clamped
        live['updated'] = datetime.now(timezone.utc).isoformat()
        live['updated_by'] = 'bounds_validation'
        _save_json(LIVE_THRESHOLDS_PATH, live)
        reload_live_thresholds()
        logger.info("[optimizer] Bounds validation clamped %d parameter(s)", len(clamped))

    return clamped


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default if default is not None else {}


def _save_json(path, data):
    """Atomic JSON write via safe_json to prevent corruption."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    import safe_json
    if not safe_json.save(str(path), data, default=str):
        logger.error("[recursive_optimizer] Failed to save %s", path)


def _load_optimizer_log() -> List[Dict]:
    return _load_json(OPTIMIZER_LOG_FILE, default=[])


def _load_paper_evidence_report() -> Dict[str, Any]:
    """Load cached paper evidence report (written each aggregate cycle)."""
    try:
        from paper_evidence import PAPER_EVIDENCE_REPORT_FILE
        import safe_json
        data = safe_json.load(str(PAPER_EVIDENCE_REPORT_FILE), default={})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_optimizer_log(log: List[Dict]):
    _save_json(OPTIMIZER_LOG_FILE, log)


def _load_followups() -> List[Dict]:
    """Load optimizer followup reminders (persisted revisit-later queue)."""
    raw = _load_json(FOLLOWUPS_FILE, default=[])
    return raw if isinstance(raw, list) else []


def _save_followups(followups: List[Dict]) -> None:
    _save_json(FOLLOWUPS_FILE, followups)


def add_followup(
    topic: str,
    notes: str,
    revisit_on: Optional[str] = None,
    revisit_after_closed_trades: Optional[int] = None,
    context: Optional[Dict] = None,
) -> Dict:
    """Append a followup reminder. The optimizer surfaces due followups each cycle
    in cycle_data['due_followups'] and the emailed report.

    Trigger is OR-ed: due when revisit_on <= today OR closed_trades >= revisit_after_closed_trades.
    At least one trigger must be provided.
    """
    if not revisit_on and revisit_after_closed_trades is None:
        raise ValueError("add_followup requires revisit_on or revisit_after_closed_trades")
    followups = _load_followups()
    now_iso = datetime.now(timezone.utc).isoformat()
    fid = f"fu_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{len(followups)}"
    entry = {
        'id': fid,
        'created': now_iso,
        'topic': topic,
        'notes': notes,
        'revisit_on': revisit_on,
        'revisit_after_closed_trades': revisit_after_closed_trades,
        'context': context or {},
        'status': 'open',
    }
    followups.append(entry)
    _save_followups(followups)
    logger.info("[optimizer] Added followup %s: %s (revisit_on=%s after_trades=%s)",
                fid, topic, revisit_on, revisit_after_closed_trades)
    return entry


def _is_followup_due(fu: Dict, today: str, closed_trades: int) -> bool:
    if fu.get('status') != 'open':
        return False
    revisit_on = fu.get('revisit_on')
    if revisit_on and str(revisit_on) <= today:
        return True
    after_trades = fu.get('revisit_after_closed_trades')
    if after_trades is not None and closed_trades >= int(after_trades):
        return True
    return False


def get_due_followups(closed_trades: int = 0, today: Optional[str] = None) -> List[Dict]:
    """Return all open followups whose revisit trigger has fired."""
    today = today or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return [fu for fu in _load_followups() if _is_followup_due(fu, today, closed_trades)]


def resolve_followup(followup_id: str, resolution: str = '') -> bool:
    """Mark a followup resolved. Returns True if found."""
    followups = _load_followups()
    for fu in followups:
        if fu.get('id') == followup_id:
            fu['status'] = 'resolved'
            fu['resolved_on'] = datetime.now(timezone.utc).isoformat()
            if resolution:
                fu['resolution'] = resolution
            _save_followups(followups)
            return True
    return False


def _load_recommendations_log() -> Dict[str, Any]:
    """Load audit log of deferred/applied recommendations. Used to hide them from future emails."""
    raw = _load_json(RECOMMENDATIONS_LOG_FILE, default={})
    if isinstance(raw, list):
        raw = {'recommendations': raw}
    return raw if isinstance(raw, dict) else {'recommendations': []}


def _save_recommendations_log(data: Dict[str, Any]):
    Path(RECOMMENDATIONS_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    _save_json(RECOMMENDATIONS_LOG_FILE, data)


def _rec_key(param: str, suggested_value: Any) -> Tuple[str, Any]:
    """Normalized key for (parameter, suggested_value) to match deferred/applied."""
    if isinstance(suggested_value, (int, float)):
        return (param, suggested_value)
    return (param, str(suggested_value) if suggested_value is not None else '')


def _get_deferred_applied_set() -> set:
    """Set of (parameter, normalized suggested_value) that user has deferred or applied."""
    data = _load_recommendations_log()
    out = set()
    for r in data.get('recommendations', []):
        if r.get('status') in ('deferred', 'applied'):
            out.add(_rec_key(r.get('parameter', ''), r.get('suggested_value')))
    return out


def _rec_id(cycle: int, param: str, suggested_value: Any) -> str:
    """Stable id for a recommendation so user can defer/apply by id."""
    if isinstance(suggested_value, (int, float)):
        val_part = str(int(suggested_value) if isinstance(suggested_value, float) and suggested_value == int(suggested_value) else suggested_value)
    else:
        val_part = str(hash(str(suggested_value)) % (10 ** 8)) if suggested_value else '0'
    safe_param = (param or 'unknown').replace(' ', '_')
    return f"rec-{cycle}-{safe_param}-{val_part}"


def _filter_recommendations_for_display(
    loss_based: List[Dict],
    pending: List[Dict],
    cycle_num: int,
) -> Tuple[List[Dict], List[Dict]]:
    """Remove recommendations that are already applied/deferred or already satisfied in live config.
    So implemented or deferred items do not keep reappearing in the email."""
    deferred_applied = _get_deferred_applied_set()
    try:
        from thresholds import get_threshold
    except Exception:
        get_threshold = lambda k: None

    def should_show(r: Dict) -> bool:
        param = r.get('parameter') or ''
        suggested = r.get('suggested_value')
        if _rec_key(param, suggested) in deferred_applied:
            return False
        # Already satisfied: numeric param and live value already >= suggested
        if isinstance(suggested, (int, float)):
            current = r.get('current_value')
            if current is None and param:
                current = get_threshold(param)
            if current is not None and current >= suggested:
                return False
        return True

    return (
        [r for r in loss_based if should_show(r)],
        [r for r in pending if should_show(r)],
    )


# ---------------------------------------------------------------------------
# Step 1: Aggregate (reuses existing functions + reads existing data files)
# ---------------------------------------------------------------------------

def aggregate() -> Dict[str, Any]:
    import model_attribution as _ma
    from thresholds import get_threshold, DEFAULT_THRESHOLDS, OPTIMIZER_BOUNDS, PARAM_PATH_FILTER

    learning = _load_json(LEARNING_REPORT_FILE)
    stress = _load_json(STRESS_REPORT_FILE)

    # Single journal read for attribution, win_rates grid, and position stats (was 40+ reloads).
    _system_pos: List[Any] = []
    try:
        from trade_journal import get_all_positions
        _system_pos = get_all_positions(exclude_test=True)
    except Exception:
        pass

    closed_positions = [p for p in _system_pos if (p.get('status') or '').upper() == 'CLOSED']
    attribution_trades_90 = _ma.attribution_records_from_closed_positions(closed_positions, 90)
    attribution = _ma.get_model_attribution(days=90, preloaded_closed_positions=closed_positions)

    regimes = ['BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE']
    paths = ['dip', 'blood_in_the_street', 'pullback_in_trend', 'volatility_harvest',
             'capitulation', 'rally', 'bull_participant', 'vol_contraction',
             'momentum_breakout', 'early_momentum', 'default']

    win_rates = {}
    for r in regimes:
        for p in paths:
            win_rates[(r, p)] = _ma.historical_win_prob_from_attribution_trades(attribution_trades_90, r, p)

    current_thresholds = {k: get_threshold(k) for k in OPTIMIZER_BOUNDS}

    # Single source: trade journal. trades_90d, regime_tier, current_regime set from system_trades_closed below.
    wr_7d = 0
    wr_30d = 0
    trades_7d = []
    trades_30d = []
    trades_90d = []
    regime_tier = {}
    current_regime = 'UNKNOWN'
    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')

    # System positions (trade journal = single source of truth)
    # V5: system trades = exclude manual overrides; manual overrides listed separately
    open_positions = []
    system_trades_closed = []  # closed, non-override (for loss analysis & perf table)
    manual_override_positions = []  # closed, manual_override=True
    system_closed = 0
    system_open = 0
    manual_override_count = 0
    perf_stats = {}
    loss_analysis = {}
    loss_based_recommendations = []
    try:
        open_positions = [p for p in _system_pos if p.get('status') == 'OPEN']
        system_trades_closed = [p for p in closed_positions if not p.get('manual_override')]
        manual_override_positions = [p for p in closed_positions if p.get('manual_override')]
        manual_override_count = len(manual_override_positions)
        system_closed = len(system_trades_closed)
        system_open = len(open_positions)  # open positions (manual_override typically on entry, not stored on open)
        # 7d/30d win rate from system booked trades (same source as Positions tab)
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
        cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
        trades_7d = [p for p in system_trades_closed if (p.get('exit_time') or '')[:10] >= cutoff_7d]
        trades_30d = [p for p in system_trades_closed if (p.get('exit_time') or '')[:10] >= cutoff_30d]
        wr_7d = round((sum(1 for p in trades_7d if p.get('outcome') == 'win') / len(trades_7d) * 100), 1) if trades_7d else 0
        wr_30d = round((sum(1 for p in trades_30d if p.get('outcome') == 'win') / len(trades_30d) * 100), 1) if trades_30d else 0
        # 90d closed for counterfactual and data_quality (same population, no duplication)
        cutoff_90d = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
        trades_90d = [p for p in system_trades_closed if (p.get('exit_time') or '')[:10] >= cutoff_90d]
        for t in system_trades_closed:
            key = ((t.get('market_regime') or t.get('regime') or 'UNKNOWN').upper(), t.get('quant_tier') or 'UNKNOWN')
            regime_tier.setdefault(key, []).append(t)
        _excluded_regimes = {'UNKNOWN', 'PRE_PIPELINE', ''}
        by_exit = sorted([p for p in system_trades_closed if p.get('exit_time')], key=lambda x: (x.get('exit_time') or ''), reverse=True)
        recent_40 = by_exit[:40]
        recent_regimes = [
            (p.get('market_regime') or p.get('regime') or 'UNKNOWN').upper()
            for p in recent_40
            if (p.get('market_regime') or p.get('regime') or '').strip() and (p.get('market_regime') or p.get('regime') or '').upper() not in _excluded_regimes
        ]
        current_regime = max(set(recent_regimes), key=recent_regimes.count) if recent_regimes else 'UNKNOWN'
        _wins = [p for p in system_trades_closed if p.get('outcome') == 'win']
        _losses = [p for p in system_trades_closed if p.get('outcome') == 'loss']
        _total_pnl = sum(p.get('pnl_usd', 0) or 0 for p in system_trades_closed)
        _gross_win = sum(p.get('pnl_usd', 0) or 0 for p in _wins)
        _gross_loss = abs(sum(p.get('pnl_usd', 0) or 0 for p in _losses))
        perf_stats = {
            'wins': len(_wins),
            'losses': len(_losses),
            'win_rate': round(len(_wins) / len(system_trades_closed) * 100, 1) if system_trades_closed else 0,
            'total_pnl_usd': round(_total_pnl, 2),
            'avg_win_pct': round(sum(p.get('pnl_pct', 0) or 0 for p in _wins) / len(_wins), 2) if _wins else 0,
            'avg_loss_pct': round(sum(p.get('pnl_pct', 0) or 0 for p in _losses) / len(_losses), 2) if _losses else 0,
            'profit_factor': round(_gross_win / _gross_loss, 2) if _gross_loss > 0 else 0,
        }
        # Loss analysis (system trades only)
        if _losses:
            _loss_scores = [p.get('confirmation_score') for p in _losses if p.get('confirmation_score') is not None]
            _win_scores = [p.get('confirmation_score') for p in _wins if p.get('confirmation_score') is not None]
            _avg_loss_score = sum(_loss_scores) / len(_loss_scores) if _loss_scores else None
            _avg_win_score = sum(_win_scores) / len(_win_scores) if _win_scores else None
            _score_gap = round(_avg_win_score - _avg_loss_score, 1) if (_avg_win_score is not None and _avg_loss_score is not None) else None
            _sl_hits = sum(1 for p in _losses if (p.get('exit_reason') or '').lower() in ('stop_loss', 'stop'))
            _loss_regimes = {}
            for p in _losses:
                r = (p.get('market_regime') or p.get('regime') or 'UNKNOWN').upper()
                if r and r not in ('UNKNOWN', 'PRE_PIPELINE'):
                    _loss_regimes[r] = _loss_regimes.get(r, 0) + 1
            loss_analysis = {
                'losses': _losses,
                'wins': _wins,
                'avg_loss_pct': perf_stats.get('avg_loss_pct'),
                'avg_win_pct': perf_stats.get('avg_win_pct'),
                'loss_regimes': _loss_regimes,
                'avg_loss_score': _avg_loss_score,
                'avg_win_score': _avg_win_score,
                'score_gap': _score_gap,
                'stop_loss_hits': _sl_hits,
            }
            # AUDIT FIX: Regime-specific loss-based recommendations (was blindly raising BEAR floor for SIDEWAYS losses)
            from thresholds import get_threshold
            # Group losses by regime and recommend the correct floor for each
            _REGIME_FLOOR_PARAMS = {
                'BEAR': ('BEAR_REGIME_MIN_SCORE', 'Score floor for BEAR/VOLATILE regimes'),
                'VOLATILE': ('BEAR_REGIME_MIN_SCORE', 'Score floor for BEAR/VOLATILE regimes'),
                'SIDEWAYS': ('SIDEWAYS_REGIME_MIN_SCORE', 'Score floor for SIDEWAYS regime'),
            }
            for _regime_name, _loss_count in _loss_regimes.items():
                if _loss_count < 2:
                    continue  # need at least 2 losses in a regime to recommend
                _param_info = _REGIME_FLOOR_PARAMS.get(_regime_name)
                if not _param_info:
                    continue
                _param_name, _param_desc = _param_info
                _current_floor = get_threshold(_param_name)
                if _current_floor is None:
                    continue
                _suggested = min(_current_floor + 3, 95)
                if _suggested > _current_floor:
                    loss_based_recommendations.append({
                        'parameter': _param_name,
                        'description': _param_desc,
                        'current_value': _current_floor,
                        'suggested_value': _suggested,
                        'reason': f'{_loss_count} of {len(_losses)} losses occurred in {_regime_name} regime',
                    })
            # AI sentiment recommendations removed — pure quant pipeline
    except Exception:
        pass

    # Screening volume (30d) + breakdown
    signals_screened_30d = 0
    screening_summary = {}
    try:
        from screening_log import _load_log as _load_screening_log
        _screening = _load_screening_log()
        _screen_recent = [s for s in _screening if s.get('timestamp', '') >= cutoff_30d[:10]]
        signals_screened_30d = len(_screen_recent)
        _tiers = {}
        for s in _screen_recent:
            t = s.get('quant_tier', 'UNKNOWN')
            _tiers[t] = _tiers.get(t, 0) + 1
        screening_summary = {
            'total': signals_screened_30d,
            'tier_breakdown': _tiers,
            'unique_coins': len(set(s.get('symbol', '') for s in _screen_recent)),
        }
    except Exception:
        pass

    # Veto accuracy from stress report
    veto_accuracy = _extract_veto_accuracy(stress)

    # Missed opportunities from learning report
    missed = learning.get('missed_opportunity', {})
    missed_summary = missed.get('summary', {})
    # Top N by regret (outcome_7d as proxy for cost of false-negative)
    top_n = int(get_threshold('TOP_MISSED_OPPS_TO_REPORT') or 10)
    rec_list = missed.get('from_recommendations', {}).get('list', [])
    sell_list = missed.get('from_sell_signals', {}).get('list', [])
    screen_list = missed.get('from_screening', {}).get('list', [])
    merged = []
    for m in rec_list:
        o7 = m.get('outcome_7d')
        if o7 is not None:
            merged.append({**m, 'outcome_7d': float(o7), 'source': 'rec', 'symbol': m.get('symbol') or m.get('coin_symbol')})
    for m in sell_list:
        o7 = m.get('outcome_7d')
        if o7 is not None:
            merged.append({**m, 'outcome_7d': float(o7), 'source': 'sell', 'symbol': m.get('symbol') or m.get('coin_symbol')})
    for m in screen_list:
        o7 = m.get('outcome_7d')
        if o7 is not None:
            merged.append({**m, 'outcome_7d': float(o7), 'source': 'screen', 'symbol': m.get('symbol') or m.get('coin_id')})
    merged.sort(key=lambda x: x['outcome_7d'], reverse=True)
    # Deduplicate: keep best outcome per symbol (one row per symbol; list is already sorted by outcome_7d desc)
    seen_coin = {}
    deduped = []
    for m in merged:
        key = m.get('coin_id') or m.get('symbol', '')
        if key not in seen_coin:
            seen_coin[key] = True
            deduped.append(m)
    top_missed_by_regret = deduped[:top_n]

    # Model calibration diagnostic: predicted confidence vs actual win rate
    model_calibration = {}
    try:
        from model_performance_tracker import get_model_accuracy, get_calibration_curve
        model_calibration = {
            'accuracy': get_model_accuracy(),
            'calibration_curve': get_calibration_curve(bins=5),
        }
        # Flag significant calibration drift (predicted vs actual diverge >10pp)
        for _bin in model_calibration.get('calibration_curve', []):
            if _bin['count'] >= 5:
                drift = abs(_bin['predicted_conf'] / 100.0 - _bin['actual_win_rate'])
                if drift > 0.10:
                    model_calibration.setdefault('drift_warnings', []).append({
                        'bin': f"{_bin['bin_low']:.0f}-{_bin['bin_high']:.0f}%",
                        'predicted': _bin['predicted_conf'],
                        'actual_wr': round(_bin['actual_win_rate'] * 100, 1),
                        'drift_pp': round(drift * 100, 1),
                    })
    except Exception as e:
        logger.debug("[optimizer] Model calibration unavailable: %s", e)

    # Load backtest trades as supplementary evidence (0.5x weight, never mixed with live)
    backtest_trades = []
    try:
        bt_data = _load_json(BACKTEST_TRADES_FILE)
        if bt_data and isinstance(bt_data, dict):
            bt_list = bt_data.get('trades', [])
            if isinstance(bt_list, list):
                backtest_trades = [t for t in bt_list if isinstance(t, dict) and t.get('source') == 'historical_replay']
                if backtest_trades:
                    logger.info(f"[optimizer] Loaded {len(backtest_trades)} backtest trades (weight={BACKTEST_WEIGHT}x)")
    except Exception as e:
        logger.debug(f"[optimizer] No backtest trades loaded: {e}")

    # Load backtest missed opportunities for regret analysis
    backtest_missed = []
    try:
        missed_data = _load_json(DATA_DIR / 'backtest_missed.json')
        if missed_data and isinstance(missed_data, dict):
            backtest_missed = missed_data.get('top_regrets', [])
            if backtest_missed:
                logger.info(f"[optimizer] Loaded {len(backtest_missed)} backtest missed opportunities")
    except Exception:
        pass

    # Load adversarial backtester report (structural vulnerabilities + quick wins)
    adversarial_report: Dict[str, Any] = {}
    try:
        adv_raw = _load_json(ADVERSARIAL_REPORT_FILE)
        if adv_raw and isinstance(adv_raw, dict) and 'baseline' in adv_raw:
            adversarial_report = adv_raw
            n_issues = len(adv_raw.get('structural_issues', []))
            n_qw = len(adv_raw.get('quick_wins', []))
            logger.info(f"[optimizer] Loaded adversarial report: {n_issues} structural issues, {n_qw} quick wins")
    except Exception:
        pass

    # Paper trades (simulated journal): separate from live headline perf_stats
    paper_trades_closed: List[Dict] = []
    paper_perf: Dict[str, Any] = {
        'closed_count': 0,
        'wins': 0,
        'losses': 0,
        'win_rate': 0.0,
        'total_pnl_usd': 0.0,
        'moderate_count': 0,
        'strong_ultra_count': 0,
    }
    try:
        from trade_journal import get_all_positions
        _all_j = get_all_positions(exclude_test=False)
        paper_trades_closed = [
            p for p in _all_j
            if (p.get('status') or '').upper() == 'CLOSED' and p.get('source') == 'paper_trade'
        ]
        if paper_trades_closed:
            _pw = [p for p in paper_trades_closed if p.get('outcome') == 'win']
            _pl = [p for p in paper_trades_closed if p.get('outcome') == 'loss']
            mod = sum(
                1 for p in paper_trades_closed
                if 'MODERATE' in str(p.get('quant_tier') or '').upper()
            )
            su = len(paper_trades_closed) - mod
            paper_perf = {
                'closed_count': len(paper_trades_closed),
                'wins': len(_pw),
                'losses': len(_pl),
                'win_rate': round(len(_pw) / len(paper_trades_closed) * 100, 1) if paper_trades_closed else 0.0,
                'total_pnl_usd': round(sum(float(p.get('pnl_usd') or 0) for p in paper_trades_closed), 2),
                'moderate_count': mod,
                'strong_ultra_count': su,
            }
            logger.info(
                "[optimizer] Paper trades closed=%d win_rate=%s%% (MODERATE=%d STRONG/ULTRA=%d)",
                len(paper_trades_closed), paper_perf['win_rate'], mod, su,
            )
    except Exception as e:
        logger.debug("[optimizer] Paper trade stats unavailable: %s", e)

    paper_evidence_report: Dict[str, Any] = {}
    try:
        from paper_evidence import build_full_report
        paper_evidence_report = build_full_report()
    except Exception as e:
        logger.debug("[optimizer] paper_evidence report failed: %s", e)

    # Per-regime live expectancy — used by _filter_full_auto() to block threshold relaxations
    # in regimes where live performance is net-negative.
    regime_expectancy: Dict[str, Any] = {}
    for _t in system_trades_closed:
        _r = (_t.get('market_regime') or _t.get('regime') or 'UNKNOWN').upper()
        if _r in ('UNKNOWN', 'PRE_PIPELINE', ''):
            continue
        _s = regime_expectancy.setdefault(_r, {'wins': 0, 'losses': 0, 'win_pnl': 0.0, 'loss_pnl': 0.0})
        _pnl = float(_t.get('pnl_pct') or 0)
        if _t.get('outcome') == 'win':
            _s['wins'] += 1
            _s['win_pnl'] += _pnl
        else:
            _s['losses'] += 1
            _s['loss_pnl'] += _pnl
    for _r, _s in regime_expectancy.items():
        _total = _s['wins'] + _s['losses']
        if _total == 0:
            continue
        _wr = _s['wins'] / _total
        _avg_w = _s['win_pnl'] / _s['wins'] if _s['wins'] else 0.0
        _avg_l = _s['loss_pnl'] / _s['losses'] if _s['losses'] else 0.0
        _s['expectancy'] = round(_wr * _avg_w + (1 - _wr) * _avg_l, 3)
        _s['win_rate_pct'] = round(_wr * 100, 1)
        _s['total_trades'] = _total

    return {
        'trades': trades_90d,
        'trades_7d': trades_7d,
        'trades_30d': trades_30d,
        'win_rate_7d': round(wr_7d, 1),
        'win_rate_30d': round(wr_30d, 1),
        'win_rates_by_regime_path': win_rates,
        'regime_tier': regime_tier,
        'current_thresholds': current_thresholds,
        'current_regime': current_regime,
        'attribution': attribution,
        'veto_accuracy': veto_accuracy,
        'missed_summary': missed_summary,
        'learning_report': learning,
        'top_missed_by_regret': top_missed_by_regret,
        'backtest_trades': backtest_trades,
        'backtest_missed': backtest_missed,
        'system_closed': system_closed,
        'system_open': system_open,
        'signals_screened_30d': signals_screened_30d,
        'manual_override_count': manual_override_count,
        'open_positions': open_positions,
        'closed_positions': closed_positions,
        'system_trades_closed': system_trades_closed,
        'manual_override_positions': manual_override_positions,
        'perf_stats': perf_stats,
        'screening_summary': screening_summary,
        'loss_analysis': loss_analysis,
        'loss_based_recommendations': loss_based_recommendations,
        'model_calibration': model_calibration,
        'paper_trades_closed': paper_trades_closed,
        'paper_perf': paper_perf,
        'paper_evidence_report': paper_evidence_report,
        'adversarial_report': adversarial_report,
        'regime_expectancy': regime_expectancy,
        'paper_regime_expectancy': (paper_evidence_report.get('paper_attribution') or {}).get('regime_expectancy', {}),
    }


def _extract_veto_accuracy(stress: Dict) -> Dict[str, Any]:
    recs = stress.get('recommendations', [])
    if not recs:
        return {'total': 0}
    vetoed = [r for r in recs if r.get('blocker') == 'veto']
    correct_veto = sum(1 for r in vetoed if r.get('verdict') in ('correct_veto', 'correct_skip'))
    missed_opp = sum(1 for r in vetoed if r.get('verdict') == 'missed_opportunity')
    unknown = sum(1 for r in vetoed if 'unknown' in (r.get('verdict') or ''))
    return {
        'total_vetoed': len(vetoed),
        'correct_veto': correct_veto,
        'missed_by_veto': missed_opp,
        'unknown': unknown,
    }


# ---------------------------------------------------------------------------
# Step 2: Counterfactual threshold analysis
# ---------------------------------------------------------------------------

def _get_param_type(param: str) -> str:
    """Classify parameter into its functional type for counterfactual logic."""
    from thresholds import PARAM_FIELD_MAP
    field = PARAM_FIELD_MAP.get(param, 'confirmation_score')
    if field == 'confirmation_score':
        return 'score_floor'
    elif field == 'fear_greed':
        return 'fg_gate'
    elif field in ('funding_rate', 'edge_score', 'ath_change_pct', 'fdv_ratio'):
        return 'float_threshold'
    elif field in ('price_change_24h', 'price_change_7d', 'price_change_30d'):
        return 'pct_cap'
    return 'score_floor'


def counterfactual_analysis(data: Dict) -> List[Dict[str, Any]]:
    from thresholds import OPTIMIZER_BOUNDS, PARAM_PATH_FILTER, PARAM_REGIME_FILTER, PARAM_DESCRIPTIONS, PARAM_FIELD_MAP, get_threshold

    trades = data['trades']
    backtest_trades = data.get('backtest_trades', [])
    paper_trades = data.get('paper_trades_closed') or []
    current = data['current_thresholds']
    results = []
    min_trades_rec = _get_optimizer_threshold_int('MIN_TRADES_FOR_REC', MIN_TRADES_FOR_REC)
    min_trades_auto = _get_optimizer_threshold_int('MIN_TRADES_FOR_AUTO', MIN_TRADES_FOR_AUTO)

    for param, (lo, hi) in OPTIMIZER_BOUNDS.items():
        path_filter = PARAM_PATH_FILTER.get(param, '*')
        cur_val = current.get(param)
        if cur_val is None:
            continue

        trade_field = PARAM_FIELD_MAP.get(param, 'confirmation_score')
        subset = _filter_trades_for_param(trades, param, path_filter)
        # Regime filter — only trades in these regimes for regime-specific params
        regime_filter = (PARAM_REGIME_FILTER or {}).get(param)
        if regime_filter:
            subset = [t for t in subset if (t.get('market_regime') or t.get('regime') or '').strip().upper() in regime_filter]
        # Only include trades that have the relevant field
        subset = [t for t in subset if t.get(trade_field) is not None]

        paper_subset: List[Dict] = []
        if paper_trades:
            paper_subset = _filter_trades_for_param(paper_trades, param, path_filter)
            if regime_filter:
                paper_subset = [t for t in paper_subset if (t.get('market_regime') or t.get('regime') or '').strip().upper() in regime_filter]
            paper_subset = [t for t in paper_subset if t.get(trade_field) is not None]

        # Supplementary paper + backtest evidence when live trades < MIN_TRADES_FOR_REC.
        # Paper 0.75x, backtest 0.5x. Neither enables auto-apply when supplement drives the count.
        used_backtest = False
        used_paper = False
        bt_subset_count = 0
        paper_subset_count = 0
        if len(subset) < min_trades_rec:
            bt_filtered: List[Dict] = []
            if backtest_trades:
                bt_filtered = _filter_trades_for_param(backtest_trades, param, path_filter)
                if regime_filter:
                    bt_filtered = [t for t in bt_filtered if (t.get('market_regime') or t.get('regime') or '').strip().upper() in regime_filter]
                bt_filtered = [t for t in bt_filtered if t.get(trade_field) is not None]
            bt_subset_count = len(bt_filtered)
            paper_subset_count = len(paper_subset)
            effective_count = (
                len(subset)
                + bt_subset_count * BACKTEST_WEIGHT
                + paper_subset_count * PAPER_TRADE_WEIGHT
            )
            if effective_count >= min_trades_rec:
                used_backtest = bt_subset_count > 0
                used_paper = paper_subset_count > 0
            else:
                continue

        wins = [t for t in subset if t.get('outcome') == 'win']
        losses = [t for t in subset if t.get('outcome') == 'loss']
        avg_win = _avg_pnl(wins)
        avg_loss = abs(_avg_pnl(losses))
        if avg_win == 0 and avg_loss == 0 and paper_subset:
            pw = [t for t in paper_subset if t.get('outcome') == 'win']
            pl = [t for t in paper_subset if t.get('outcome') == 'loss']
            avg_win = _avg_pnl(pw)
            avg_loss = abs(_avg_pnl(pl))
        if avg_win == 0 and avg_loss == 0:
            continue

        if paper_subset and not used_paper:
            used_paper = True

        param_type = _get_param_type(param)
        best = _find_best_candidate(
            param, cur_val, lo, hi, subset, avg_win, avg_loss,
            trade_field=trade_field, param_type=param_type,
            paper_trades=paper_subset if paper_subset else None,
            paper_weight=PAPER_TRADE_WEIGHT,
        )
        if best and best['net_impact'] > 0:
            desc = PARAM_DESCRIPTIONS.get(param, param)
            live_count = len(subset)
            paper_count = len(paper_subset)
            is_relax = False
            try:
                from paper_evidence import is_param_relaxation
                is_relax = is_param_relaxation(param_type, float(cur_val), float(best['value']))
            except Exception:
                pass
            slippage = float(get_threshold('SLIPPAGE_PCT', 0.08) or 0.08)
            fee = float(get_threshold('FEE_PCT', 0.075) or 0.075)
            missed_w = float(get_threshold('MISSED_EDGE_WEIGHT', 0.25) or 0.25)
            live_only_wg = 0.0
            if is_relax and subset:
                live_only_res = _evaluate_candidate_on_trades(
                    best['value'], cur_val, subset, trade_field, param_type,
                    avg_win, avg_loss, slippage, fee, missed_w,
                )
                live_only_wg = float(live_only_res.get('winners_gained', 0) or 0)
            total_wg = float(best.get('winners_gained', 0) or 0)
            paper_share_of_impact = None
            if is_relax and total_wg > 0:
                paper_share_of_impact = round(max(0.0, (total_wg - live_only_wg) / total_wg), 3)
            paper_led = is_relax and paper_count > 0 and live_only_wg <= 0
            if used_backtest or (used_paper and live_count < min_trades_auto):
                conf = 'medium'
                safe = False
                evidence_tier = 'B'
            elif is_relax and paper_count > 0:
                conf = 'medium'
                safe = False
                evidence_tier = 'C' if paper_led else 'B'
            elif live_count >= min_trades_auto:
                conf = 'high'
                safe = (best['winners_lost'] == 0
                        and lo <= best['value'] <= hi and best['net_impact'] > 0)
                evidence_tier = 'A'
            else:
                conf = 'medium'
                safe = False
                evidence_tier = 'B'
            top_missed = data.get('top_missed_by_regret', [])
            missed_sum_pct = sum(m.get('outcome_7d', 0) for m in top_missed) / 100.0 if top_missed else 0.0
            missed_edge_reduction = missed_sum_pct if path_filter == '*' else missed_sum_pct * 0.3
            result_entry = {
                'parameter': param,
                'description': desc,
                'current_value': cur_val,
                'suggested_value': best['value'],
                'losses_avoided': best['losses_avoided'],
                'winners_lost': best['winners_lost'],
                'winners_gained': best.get('winners_gained', 0),
                'net_expected_pnl_pct': round(best['net_impact'], 2),
                'evidence_trades': live_count + paper_count,
                'live_evidence_trades': live_count,
                'paper_evidence_trades': paper_count,
                'confidence': conf,
                'safe_to_auto_apply': safe,
                'evidence_tier': evidence_tier,
                'paper_led': paper_led,
                'live_only_winners_gained': round(live_only_wg, 4),
                'paper_share_of_impact': paper_share_of_impact,
                'path_filter': path_filter,
                'missed_edge_reduction': round(missed_edge_reduction, 4),
            }
            if used_backtest:
                result_entry['backtest_supplemented'] = True
                result_entry['backtest_trades_used'] = bt_subset_count
            if used_paper:
                result_entry['paper_supplemented'] = True
                result_entry['paper_trades_used'] = paper_subset_count

            # Annotate with adversarial sensitivity data if available
            adv = data.get('adversarial_report') or {}
            adv_sensitivity = adv.get('parameter_sensitivity', [])
            for ps in adv_sensitivity:
                if ps.get('parameter') == param:
                    result_entry['adversarial_sensitivity'] = ps.get('sensitivity', 0)
                    result_entry['adversarial_best_value'] = ps.get('best_value')
                    result_entry['adversarial_best_expectancy'] = ps.get('best_expectancy')
                    # Boost confidence when adversarial agrees with suggestion direction
                    adv_best = ps.get('best_value')
                    if adv_best is not None and best['value'] is not None:
                        # Both suggest same direction from current? (both higher or both lower)
                        adv_agrees = (adv_best > cur_val) == (best['value'] > cur_val)
                        result_entry['adversarial_agrees'] = adv_agrees
                    break

            # Check if adversarial report flags this parameter's regime/path as a failure cell
            adv_failure_cells = adv.get('failure_cells', [])
            regime_filter_set = (PARAM_REGIME_FILTER or {}).get(param, set())
            for fc in adv_failure_cells:
                fc_regime = fc.get('regime', '')
                if regime_filter_set and fc_regime in regime_filter_set:
                    result_entry.setdefault('adversarial_failure_cells', []).append({
                        'regime': fc_regime,
                        'path': fc.get('path', ''),
                        'win_rate_pct': fc.get('win_rate_pct', 0),
                        'recommendation': fc.get('recommendation', ''),
                    })

            results.append(result_entry)

    return results


def _filter_trades_for_param(trades: List[Dict], param: str, path_filter: str) -> List[Dict]:
    """Filter trades relevant to a parameter. AUDIT FIX: collapse path-level granularity to
    regime-level groups so the optimizer has enough trades per bucket to learn.
    Path groups: BEAR_entry (blood/dip/pullback/vol_harvest/capitulation/vol_contraction),
    MOMENTUM (momentum_breakout/early_momentum), BULL (bull_participant/rally), default (*).
    """
    if path_filter == '*':
        return trades

    # Collapse individual paths into regime-level groups for statistical power
    BEAR_ENTRY_PATHS = {'blood_in_the_street', 'dip', 'pullback_in_trend', 'volatility_harvest',
                        'capitulation', 'vol_contraction'}
    MOMENTUM_PATHS = {'momentum_breakout', 'early_momentum'}
    BULL_PATHS = {'bull_participant', 'rally'}

    if path_filter in BEAR_ENTRY_PATHS:
        return [t for t in trades if (t.get('quant_path') or 'default') in BEAR_ENTRY_PATHS]
    if path_filter in MOMENTUM_PATHS:
        return [t for t in trades if (t.get('quant_path') or 'default') in MOMENTUM_PATHS]
    if path_filter in BULL_PATHS:
        return [t for t in trades if (t.get('quant_path') or 'default') in BULL_PATHS]
    # Exact match fallback for any new paths
    return [t for t in trades if (t.get('quant_path') or 'default') == path_filter]


def _avg_pnl(trades: List[Dict]) -> float:
    pnls = [t.get('pnl_pct', 0) for t in trades if t.get('pnl_pct') is not None]
    return sum(pnls) / len(pnls) if pnls else 0


def _weighted_avg_pnl(trades: List[Dict], outcome: str) -> float:
    """Weighted mean pnl_pct for trades with given outcome (uses _evidence_weight, default 1.0)."""
    hits = [t for t in trades if t.get('outcome') == outcome and t.get('pnl_pct') is not None]
    if not hits:
        return 0.0
    total_w = sum(float(t.get('_evidence_weight', 1.0)) for t in hits)
    if total_w <= 0:
        return _avg_pnl(hits)
    return sum(float(t['pnl_pct']) * float(t.get('_evidence_weight', 1.0)) for t in hits) / total_w


def _evaluate_candidate_on_trades(cand, cur_val, trades, trade_field, param_type, avg_win, avg_loss, slippage, fee, missed_weight):
    """Score a single candidate threshold against a set of trades. Returns metrics dict.

    BIDIRECTIONAL: evaluates both tightening AND relaxing.
    - Tightening (more restrictive): blocks trades between cur_val and cand → avoids losses but may lose winners
    - Relaxing (less restrictive): unblocks trades between cand and cur_val → captures missed wins but may add losses
    """
    losses_avoided = 0
    winners_lost = 0
    losses_added = 0      # New: tracks cost of relaxing
    winners_gained = 0    # New: tracks benefit of relaxing
    total_regret_captured = 0.0

    for t in trades:
        val = t.get(trade_field)
        if val is None:
            continue

        would_be_blocked = False
        would_be_unblocked = False

        if param_type == 'score_floor':
            # Tightening: cand > cur_val → blocks trades with val in [cur_val, cand)
            # Relaxing: cand < cur_val → unblocks trades with val in [cand, cur_val)
            if cand > cur_val:
                would_be_blocked = val >= cur_val and val < cand
            elif cand < cur_val:
                would_be_unblocked = val >= cand and val < cur_val
        elif param_type in ('fg_gate', 'pct_cap'):
            # Tightening: cand < cur_val → blocks trades with val in [cand, cur_val)
            # Relaxing: cand > cur_val → unblocks trades with val in [cur_val, cand)
            if cand < cur_val:
                would_be_blocked = val >= cand and val < cur_val
            elif cand > cur_val:
                would_be_unblocked = val >= cur_val and val < cand
        elif param_type == 'float_threshold':
            # Same direction as score_floor
            if cand > cur_val:
                would_be_blocked = val >= cur_val and val < cand
            elif cand < cur_val:
                would_be_unblocked = val >= cand and val < cur_val

        w = float(t.get('_evidence_weight', 1.0) or 1.0)

        if would_be_blocked:
            if t.get('outcome') == 'loss':
                losses_avoided += w
            elif t.get('outcome') == 'win':
                winners_lost += w
        elif would_be_unblocked:
            if t.get('outcome') == 'win':
                winners_gained += w
            elif t.get('outcome') == 'loss':
                losses_added += w
        else:
            pnl = float(t.get('pnl_pct', 0) or 0)
            simulated_pnl = pnl - slippage - fee
            if t.get('outcome') == 'win' and simulated_pnl > 0:
                total_regret_captured += simulated_pnl * w

    # Tightening: net = losses_avoided * avg_loss - winners_lost * avg_win
    # Relaxing: net = winners_gained * avg_win - losses_added * avg_loss
    # Combined: both effects sum together
    net_pnl = ((losses_avoided * avg_loss) - (winners_lost * avg_win)
               + (winners_gained * avg_win) - (losses_added * avg_loss))
    regret_score = total_regret_captured * missed_weight
    objective = net_pnl + regret_score
    return {
        'losses_avoided': losses_avoided, 'winners_lost': winners_lost,
        'winners_gained': winners_gained, 'losses_added': losses_added,
        'net_impact': net_pnl, 'regret_captured': round(total_regret_captured, 4),
        'objective': objective,
    }


def _find_best_candidate(param: str, cur_val, lo, hi, trades, avg_win, avg_loss,
                         trade_field: str = 'confirmation_score',
                         param_type: str = 'score_floor',
                         paper_trades: Optional[List[Dict]] = None,
                         paper_weight: float = PAPER_TRADE_WEIGHT) -> Optional[Dict]:
    """Walk-forward validation. Sort by time; train on older 70%, validate on newer 30%.
    Only accept candidate if BOTH train and validation improve (anti-overfit).
    Relaxation candidates use live+paper weighted pool; tightening uses live trades only."""
    from paper_evidence import combine_for_relaxation, is_param_relaxation
    from thresholds import get_threshold
    slippage = float(get_threshold('SLIPPAGE_PCT', 0.08) or 0.08)
    fee = float(get_threshold('FEE_PCT', 0.075) or 0.075)
    missed_weight = float(get_threshold('MISSED_EDGE_WEIGHT', 0.25) or 0.25)
    min_wf_trades = int(get_threshold('MIN_WALK_FORWARD_TRADES', 15) or 15)

    # Explicit time-ordered split: oldest first so train = past, val = recent
    def _trade_time(t):
        ts = t.get('exit_time') or t.get('ts') or ''
        return (ts[:19] if 'T' in str(ts) else str(ts).replace(' ', 'T')[:19]) if ts else ''

    trades_sorted = sorted(trades, key=_trade_time)
    use_walk_forward = len(trades_sorted) >= min_wf_trades
    if use_walk_forward:
        split_idx = max(1, int(len(trades_sorted) * 0.7))
        train_trades = trades_sorted[:split_idx]
        val_trades = trades_sorted[split_idx:]
    else:
        train_trades = trades_sorted
        val_trades = []

    # Symmetric candidate ranges: search equally in both directions (tighten AND relax)
    if param_type == 'pct_cap':
        step = 0.5
        candidates = _frange(max(lo, cur_val - 3.0), min(hi, cur_val + 3.0), step)
    elif param_type == 'fg_gate':
        step = 1.0 if isinstance(cur_val, float) else 1
        candidates = _frange(max(lo, cur_val - 4), min(hi, cur_val + 4), step)
    elif isinstance(cur_val, float):
        step = 0.05
        candidates = _frange(max(lo, cur_val - 0.20), min(hi, cur_val + 0.20), step)
    else:
        candidates = range(max(int(lo), int(cur_val) - 4), min(int(hi), int(cur_val) + 5))

    # Compute avg_win/avg_loss for train set specifically
    train_wins = [t for t in train_trades if t.get('outcome') == 'win']
    train_losses = [t for t in train_trades if t.get('outcome') == 'loss']
    train_avg_win = _avg_pnl(train_wins) if train_wins else avg_win
    train_avg_loss = abs(_avg_pnl(train_losses)) if train_losses else avg_loss

    best = None
    for cand in candidates:
        if isinstance(cur_val, int):
            cand = int(cand)
        else:
            cand = round(cand, 4)
        if isinstance(cand, float):
            cand = max(lo, min(hi, cand))
            cand = round(cand, 4)
        if cand == cur_val:
            continue
        if not (lo <= cand <= hi):
            continue

        relax = is_param_relaxation(param_type, float(cur_val), float(cand))
        if relax and paper_trades:
            eval_trades = combine_for_relaxation(trades, paper_trades, paper_weight)
            eval_train = combine_for_relaxation(train_trades, paper_trades, paper_weight)
            eval_val = combine_for_relaxation(val_trades, paper_trades, paper_weight) if val_trades else []
            w_win = _weighted_avg_pnl(eval_train, 'win')
            w_loss = _weighted_avg_pnl(eval_train, 'loss')
            cand_avg_win = w_win if w_win else train_avg_win
            cand_avg_loss = abs(w_loss) if w_loss else train_avg_loss
        else:
            eval_trades = trades
            eval_train = train_trades
            eval_val = val_trades
            cand_avg_win = train_avg_win
            cand_avg_loss = train_avg_loss

        # Evaluate on train set
        train_result = _evaluate_candidate_on_trades(
            cand, cur_val, eval_train, trade_field, param_type,
            cand_avg_win, cand_avg_loss, slippage, fee, missed_weight)

        if train_result['objective'] <= 0:
            continue

        # Walk-forward validation: must also improve on validation set
        if use_walk_forward and val_trades:
            val_wins = [t for t in val_trades if t.get('outcome') == 'win']
            val_losses = [t for t in val_trades if t.get('outcome') == 'loss']
            val_avg_win = _avg_pnl(val_wins) if val_wins else avg_win
            val_avg_loss = abs(_avg_pnl(val_losses)) if val_losses else avg_loss
            val_result = _evaluate_candidate_on_trades(
                cand, cur_val, eval_val, trade_field, param_type,
                val_avg_win, val_avg_loss, slippage, fee, missed_weight)
            # Reject if validation shows negative impact (overfitting indicator)
            if val_result['net_impact'] < 0:
                continue
            # Use combined train+val for final metrics
            full_result = _evaluate_candidate_on_trades(
                cand, cur_val, eval_trades, trade_field, param_type,
                avg_win, avg_loss, slippage, fee, missed_weight)
        else:
            full_result = train_result

        if best is None or full_result['objective'] > best['objective']:
            best = {
                'value': cand,
                'losses_avoided': full_result['losses_avoided'],
                'winners_lost': full_result['winners_lost'],
                'winners_gained': full_result.get('winners_gained', 0),
                'net_impact': full_result['net_impact'],
                'regret_captured': full_result['regret_captured'],
                'objective': full_result['objective'],
                'walk_forward_validated': use_walk_forward,
            }

    return best


def _frange(start, stop, step):
    vals = []
    v = start
    while v < stop + step / 2:
        vals.append(round(v, 4))
        v += step
    return vals


# ---------------------------------------------------------------------------
# Step 3: Rank and filter
# ---------------------------------------------------------------------------

def rank_and_filter(results: List[Dict], data: Optional[Dict] = None) -> Tuple[List[Dict], List[Dict]]:
    from thresholds import get_threshold
    weight = float(get_threshold('MISSED_EDGE_WEIGHT') or 0.25)
    def _combined(r):
        net = r.get('net_expected_pnl_pct') or 0
        missed = r.get('missed_edge_reduction') or 0
        return net + weight * missed
    results.sort(key=_combined, reverse=True)
    auto = []
    pending = []
    for r in results:
        if r['safe_to_auto_apply'] and len(auto) < MAX_AUTO_CHANGES_PER_CYCLE:
            auto.append(r)
        else:
            pending.append(r)
    return auto, pending


def _filter_full_auto(
    results: List[Dict],
    min_improvement: float = DEFAULT_MIN_IMPROVEMENT,
    max_changes_per_cycle: Optional[int] = None,
    regime_expectancy: Optional[Dict[str, Any]] = None,
    paper_regime_expectancy: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Filter counterfactual results for full-auto apply: min_improvement, 0 winners lost, in bounds.
    Also blocks threshold relaxations for regimes with negative live expectancy (regime_expectancy gate).
    When live expectancy blocks a relax but paper expectancy for the same regime is positive, the
  recommendation is annotated in pending (never auto-applied).
    Returns (list_to_apply, pending_for_display). Sorts by combined (net_expected_pnl_pct + MISSED_EDGE_WEIGHT * missed_edge_reduction)."""
    from thresholds import OPTIMIZER_BOUNDS, PARAM_REGIME_FILTER, get_threshold
    weight = float(get_threshold('MISSED_EDGE_WEIGHT') or 0.25)

    # Log regime expectancy snapshot so every cycle shows whether the relaxation gate is armed.
    # If a regime is missing here, its trades have market_regime=UNKNOWN/PRE_PIPELINE in positions.json
    # and the gate cannot fire for that regime — a sign that Bug 1c (UNKNOWN regime) is still active.
    if regime_expectancy:
        for _re, _rd in sorted(regime_expectancy.items()):
            logger.info(
                "[optimizer] regime_expectancy gate: %s  WR=%.1f%%  expectancy=%.3f%%  n=%d  %s",
                _re,
                _rd.get('win_rate_pct', 0),
                _rd.get('expectancy', 0),
                _rd.get('total_trades', 0),
                "GATE ARMED (negative)" if (_rd.get('expectancy', 0) < 0 and _rd.get('total_trades', 0) >= 5)
                else "gate inactive (positive or insufficient data)",
            )
    else:
        logger.warning(
            "[optimizer] regime_expectancy is empty — relaxation gate cannot fire. "
            "Check that closed positions have market_regime set (not UNKNOWN/PRE_PIPELINE)."
        )
    def _combined(r):
        net = r.get('net_expected_pnl_pct') or 0
        missed = r.get('missed_edge_reduction') or 0
        return net + weight * missed
    min_trades_auto = _get_optimizer_threshold_int('MIN_TRADES_FOR_AUTO', MIN_TRADES_FOR_AUTO)
    eligible = []
    pending_extra: List[Dict] = []  # regime-gated items — go to pending display, not auto-apply
    for r in results:
        if r.get('winners_lost', 0) != 0:
            continue
        lift = r.get('net_expected_pnl_pct') or 0
        if lift < min_improvement:
            continue
        # Require minimum live evidence — prevents tightening on noise (e.g. 4 trades at 25% WR)
        if r.get('live_evidence_trades', 0) < min_trades_auto:
            continue
        val = r.get('suggested_value')
        param = r.get('parameter')
        bounds = OPTIMIZER_BOUNDS.get(param) if param else None
        if bounds is not None and val is not None and not (bounds[0] <= val <= bounds[1]):
            continue

        # Regime-expectancy gate: block relaxations (lowering a score floor) for regimes
        # where live expectancy is negative. Requires >= 5 live trades for the gate to fire
        # so it doesn't block on noise with 1-2 trades.
        if regime_expectancy and param:
            curr_val = r.get('current_value')
            is_relaxation = (
                curr_val is not None
                and val is not None
                and isinstance(val, (int, float))
                and isinstance(curr_val, (int, float))
                and val < curr_val
            )
            if is_relaxation:
                associated_regimes = PARAM_REGIME_FILTER.get(param, [])
                blocked = False
                blocked_regime = None
                for regime in associated_regimes:
                    exp_data = regime_expectancy.get(regime, {})
                    exp = exp_data.get('expectancy')
                    n = exp_data.get('total_trades', 0)
                    if exp is not None and exp < 0 and n >= 5:
                        logger.info(
                            "[optimizer] Regime-expectancy gate: blocking %s relaxation (%s→%s) "
                            "— %s live expectancy %.3f%% (%d trades)",
                            param, curr_val, val, regime, exp, n,
                        )
                        blocked = True
                        blocked_regime = regime
                        break
                if blocked:
                    gated = dict(r)
                    if paper_regime_expectancy and blocked_regime:
                        paper_data = paper_regime_expectancy.get(blocked_regime, {})
                        paper_exp = paper_data.get('expectancy')
                        paper_n = paper_data.get('total_trades', 0)
                        if paper_exp is not None and paper_exp > 0 and paper_n >= 5:
                            gated['paper_regime_positive'] = True
                            gated['paper_regime_expectancy_pct'] = paper_exp
                            gated['paper_regime_trades'] = paper_n
                            gated['gate_note'] = (
                                'live negative; paper positive — manual review only'
                            )
                            logger.info(
                                "[optimizer] Paper regime hint for %s: %s paper expectancy %.3f%% (%d trades)",
                                param, blocked_regime, paper_exp, paper_n,
                            )
                    pending_extra.append(gated)
                    continue

        eligible.append(r)
    eligible.sort(key=_combined, reverse=True)
    if max_changes_per_cycle is not None:
        eligible = eligible[:max_changes_per_cycle]
    applied_keys = {(r['parameter'], r.get('suggested_value')) for r in eligible}
    pending = [r for r in results if (r.get('parameter'), r.get('suggested_value')) not in applied_keys]
    # Merge regime-gated items: annotated copy replaces unannotated duplicate (same param+value)
    pending_by_key = {(r.get('parameter'), r.get('suggested_value')): r for r in pending}
    for gated in pending_extra:
        key = (gated.get('parameter'), gated.get('suggested_value'))
        pending_by_key[key] = gated
    pending = list(pending_by_key.values())
    return eligible, pending


# ---------------------------------------------------------------------------
# Step 4: Apply safe changes
# ---------------------------------------------------------------------------

def apply_safe_changes(auto: List[Dict]) -> List[Dict]:
    """PERMANENTLY DISARMED (STRATEGY.md §5.8, 2026-07-03).

    This function used to write optimizer-selected threshold changes into
    live_thresholds.json. The June 2026 audit + honest-universe adjudication
    established that those "learnings" were noise fitted on 11-16-trade windows
    — the component that actively lost money. Deleted, not just frozen: no
    combination of freeze flags, trade counts, or weekly-task toggles can ever
    re-enable automated threshold writes. Candidate changes surface as
    pending-review recommendations only; the sole write paths are the HUMAN
    ones (apply_single / apply_recommendation) and registered trials that pass
    the STRATEGY.md gates.
    """
    if auto:
        logger.info(
            "[optimizer] apply_safe_changes: auto-apply is permanently disarmed "
            "(STRATEGY.md §5.8) — %d candidate change(s) left as pending review",
            len(auto),
        )
    return []


def apply_single(parameter: str, value) -> Dict:
    """Manual apply for a single parameter (called from CLI for pending recommendations)."""
    from thresholds import LIVE_THRESHOLDS_PATH, OPTIMIZER_BOUNDS

    bounds = OPTIMIZER_BOUNDS.get(parameter)
    if bounds and not (bounds[0] <= value <= bounds[1]):
        return {'success': False, 'error': f'Value {value} outside bounds {bounds}'}

    with _thresholds_write_lock:
        current_file = _load_json(LIVE_THRESHOLDS_PATH, default={})
        previous_file = dict(current_file)
        old_val = current_file.get(parameter)
        current_file[parameter] = value
        current_file['updated'] = datetime.now(timezone.utc).isoformat()
        current_file['updated_by'] = 'manual_optimizer'
        _save_json(LIVE_THRESHOLDS_PATH, current_file)

    if not _reload_thresholds_or_rollback(previous_file, "apply_single"):
        return {'success': False, 'error': 'Failed to reload live thresholds after write; changes rolled back'}

    logger.info(f"[optimizer] Manual apply: {parameter} = {value} (was {old_val})")
    return {'success': True, 'parameter': parameter, 'old_value': old_val, 'new_value': value}


# ---------------------------------------------------------------------------
# Step 5: Evaluate previous cycle (trade-based maturity)
# ---------------------------------------------------------------------------

def _trade_time_for_compare(t: Dict) -> str:
    """Return a comparable timestamp for a trade (journal uses exit_time, legacy used ts). Normalized to YYYY-MM-DDTHH:MM:SS."""
    raw = t.get('ts') or t.get('exit_time') or ''
    if not raw:
        return ''
    if 'T' in str(raw):
        return str(raw)[:19]
    return str(raw).replace(' ', 'T')[:19]


def evaluate_previous_cycle(data: Dict) -> List[Dict]:
    maturity_target = _get_optimizer_threshold_int('MATURITY_TRADE_TARGET', MATURITY_TRADE_TARGET)
    log = _load_optimizer_log()
    trades = data['trades']
    evaluations = []
    applied_at_cutoff = 19  # compare YYYY-MM-DDTHH:MM:SS only

    # Only scan cycles that have non-terminal changes (skip fully resolved cycles)
    for cycle in log:
        if all(c.get('status') in ('confirmed', 'reverted') for c in cycle.get('auto_applied', [])):
            continue
        for change in cycle.get('auto_applied', []):
            if change.get('status') in ('confirmed', 'reverted'):
                continue

            applied_at = change.get('applied_at', '')
            applied_at_compare = applied_at[:applied_at_cutoff] if applied_at else ''
            path_filter = change.get('path_filter', '*')
            param = change.get('parameter', '')

            trades_since = [
                t for t in trades
                if _trade_time_for_compare(t) > applied_at_compare
                and (path_filter == '*' or (t.get('quant_path') or 'default') == path_filter)
                and t.get('outcome') in ('win', 'loss')
            ]

            trade_count = len(trades_since)
            ev = {
                'parameter': param,
                'old_value': change.get('old_value'),
                'new_value': change.get('new_value'),
                'applied_at': applied_at,
                'trades_since': trade_count,
                'target_trades': maturity_target,
            }

            if trade_count < maturity_target:
                # AUDIT FIX: Early warning revert — if win rate below 35% at 10 trades, revert immediately
                if trade_count >= 10:
                    early_wins = sum(1 for t in trades_since if t.get('outcome') == 'win')
                    early_wr = early_wins / trade_count * 100
                    if early_wr < 35.0:
                        ev['status'] = 'reverted'
                        ev['verdict'] = f'Early revert: win rate {early_wr:.1f}% at {trade_count} trades (< 35%)'
                        change['status'] = 'reverted'
                        _revert_change(change)
                        evaluations.append(ev)
                        continue
                ev['status'] = 'maturing'
                ev['verdict'] = f'Still maturing ({trade_count}/{maturity_target} trades)'
                change['status'] = 'maturing'
            else:
                wins = sum(1 for t in trades_since if t.get('outcome') == 'win')
                wr_after = wins / trade_count * 100

                # Win rate of same path BEFORE the change (journal trades use exit_time)
                trades_before = [
                    t for t in trades
                    if _trade_time_for_compare(t) <= applied_at_compare
                    and (path_filter == '*' or (t.get('quant_path') or 'default') == path_filter)
                    and t.get('outcome') in ('win', 'loss')
                ]
                wr_before = (sum(1 for t in trades_before if t.get('outcome') == 'win')
                             / len(trades_before) * 100) if trades_before else 50.0

                delta = wr_after - wr_before
                ev['win_rate_before'] = round(wr_before, 1)
                ev['win_rate_after'] = round(wr_after, 1)
                ev['delta_pp'] = round(delta, 1)

                if delta < -REVERT_DROP_THRESHOLD:
                    ev['status'] = 'reverted'
                    ev['verdict'] = f'Win rate dropped {abs(delta):.1f}pp — auto-reverted'
                    change['status'] = 'reverted'
                    _revert_change(change)
                elif delta < -WATCH_DROP_THRESHOLD:
                    ev['status'] = 'watch'
                    ev['verdict'] = f'Win rate dipped {abs(delta):.1f}pp — watching (re-eval at 20 trades)'
                    change['status'] = 'watch'
                else:
                    ev['status'] = 'confirmed'
                    ev['verdict'] = f'Win rate {"improved" if delta > 0 else "held"} ({delta:+.1f}pp)'
                    change['status'] = 'confirmed'

            evaluations.append(ev)

    _save_optimizer_log(log)
    return evaluations


def _revert_change(change: Dict):
    """PERMANENTLY DISARMED (audit F2, 2026-07-04): a status/evaluation pass must
    not write live_thresholds.json — same invariant as get_data_collection_status.
    With auto-apply deleted there are no new 'maturing' changes, but stale maturing
    entries in optimizer_log.json could still have triggered automated reverts.
    Reverts are now a recommendation for the human apply path (apply_single)."""
    logger.warning(
        "[optimizer] _revert_change disarmed (audit F2) — recommend MANUAL revert of %s "
        "from %s back to %s via apply_single",
        change.get('parameter'), change.get('new_value'), change.get('old_value'),
    )


# ---------------------------------------------------------------------------
# Step 5b: Weekly consolidated recommendations (Friday only)
# ---------------------------------------------------------------------------

def _build_weekly_consolidated_recommendations(log: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """Aggregate the last 7 days' recommendations (incl. weekend), resolve conflicts, return (consolidated, conflicts).
    Runs on Friday only; includes Sat–Sun since crypto trades 24/7."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if now.weekday() != 4:  # 0=Mon, 4=Fri
        return [], []
    # Rolling 7 days (incl. weekend)
    cutoff = (now - timedelta(days=6)).strftime('%Y-%m-%d')
    week_entries = [e for e in log if (e.get('snapshot', {}).get('date', '') >= cutoff)]
    if len(week_entries) < 2:
        return [], []

    # Collect all recommendations
    all_recs = []
    for e in week_entries:
        date_str = e.get('snapshot', {}).get('date', '')
        for r in e.get('loss_based_recommendations', []):
            all_recs.append((date_str, r.get('parameter'), r.get('current_value'),
                             r.get('suggested_value'), r.get('reason', ''), 'loss'))
        for r in e.get('pending_review', []):
            all_recs.append((date_str, r.get('parameter'), r.get('current_value'),
                             r.get('suggested_value'), r.get('reason', ''), 'counterfactual'))

    # Group by parameter
    by_param = {}
    for date_str, param, current, suggested, reason, source in all_recs:
        if not param:
            continue
        by_param.setdefault(param, []).append({
            'date': date_str, 'current_value': current, 'suggested_value': suggested,
            'reason': reason, 'source': source,
        })

    try:
        from thresholds import get_threshold
    except Exception:
        get_threshold = lambda k: None

    consolidated = []
    conflicts = []
    for param, recs in by_param.items():
        if len(recs) < 2:
            continue  # Only include if appeared 2+ days
        # Fill current_value from thresholds when missing
        fallback_current = get_threshold(param)
        for r in recs:
            if r['current_value'] is None and param:
                r['current_value'] = fallback_current
        # For numeric params: check direction and conflicts
        numeric_suggested = []
        non_numeric = []
        for r in recs:
            sv = r['suggested_value']
            cv = r['current_value'] if r['current_value'] is not None else fallback_current
            if isinstance(sv, (int, float)):
                numeric_suggested.append((cv if isinstance(cv, (int, float)) else (fallback_current or 0), sv))
            else:
                non_numeric.append(r)

        if numeric_suggested:
            # All (current, suggested) pairs - check for conflict
            directions = []
            for cv, sv in numeric_suggested:
                if sv > cv:
                    directions.append('raise')
                elif sv < cv:
                    directions.append('lower')
                else:
                    directions.append('same')
            if 'raise' in directions and 'lower' in directions:
                conflicts.append(f'{param}: conflicting directions (some raise, some lower)')
                continue
            # Same direction: take mean of suggested values; preserve float type for float params
            suggested_vals = [sv for _, sv in numeric_suggested]
            current_vals = [cv for cv, _ in numeric_suggested]
            mean_val = sum(suggested_vals) / len(suggested_vals)
            if any(isinstance(sv, float) for sv in suggested_vals):
                final_suggested = round(mean_val, 4)
            else:
                final_suggested = int(round(mean_val))
            final_current = current_vals[0]  # assume same current
            days_count = len(set(r['date'] for r in recs))
            consolidated.append({
                'parameter': param,
                'current_value': final_current,
                'suggested_value': final_suggested,
                'reason': f'Appeared {days_count} days this week; consensus: {final_current} → {final_suggested}',
                'days_appeared': days_count,
                'total_occurrences': len(recs),
            })
        elif non_numeric:
            # e.g. qualitative "increase penalty"
            days_count = len(set(r['date'] for r in recs))
            first = non_numeric[0]
            consolidated.append({
                'parameter': param,
                'current_value': first.get('current_value'),
                'suggested_value': first.get('suggested_value'),
                'reason': f'Appeared {days_count} days this week; {first.get("reason", "")}',
                'days_appeared': days_count,
                'total_occurrences': len(recs),
            })
    # Exclude deferred/applied so Friday report does not repeat them
    deferred_applied = _get_deferred_applied_set()
    consolidated = [c for c in consolidated if _rec_key(c.get('parameter', ''), c.get('suggested_value')) not in deferred_applied]
    return consolidated, conflicts


# ---------------------------------------------------------------------------
# Step 6: Format email report
# ---------------------------------------------------------------------------

def _build_safety_gate_cycle_data(cycle_num: int, timestamp_iso: str, date_str: str) -> Dict[str, Any]:
    """Minimal cycle_data when unit preflight blocks the run (same email pipeline as a normal cycle)."""
    min_req = _get_optimizer_threshold_int('MIN_TRADES_FOR_REC', MIN_TRADES_FOR_REC)
    snapshot = {
        'cycle': cycle_num,
        'date': date_str,
        'regime': 'UNKNOWN',
        'system_closed': 0,
        'system_open': 0,
        'total_trades': 0,
        'win_rate_7d': 0,
        'win_rate_30d': 0,
        'booked_trades_7d': 0,
        'booked_trades_30d': 0,
        'signals_screened_30d': 0,
        'manual_override_count': 0,
    }
    return {
        'timestamp': timestamp_iso,
        'cycle': cycle_num,
        'safety_gate_blocked': True,
        'snapshot': snapshot,
        'auto_applied': [],
        'pending_review': [],
        'evaluations': [],
        'path_performance': [],
        'missed_summary': {},
        'learning_report': {},
        'data_quality': {
            'system_trades_count': 0,
            'has_confirmation_score': 0,
            'has_fear_greed': 0,
            'has_funding_rate': 0,
            'has_price_change_24h': 0,
            'min_required': min_req,
            'params_analyzed': 0,
        },
        'open_positions': [],
        'closed_positions': [],
        'system_trades_closed': [],
        'manual_override_positions': [],
        'perf_stats': {},
        'screening_summary': {},
        'loss_analysis': {},
        'loss_based_recommendations': [],
        'top_missed_by_regret': [],
    }


def _format_safety_gate_blocked_html(cycle_data: Dict[str, Any]) -> str:
    """Same daily optimizer email layout: header + auto-applied section explains gate (no second email)."""
    snap = cycle_data['snapshot']
    parts = [
        '<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 700px;">',
        '<div style="background: #1a1a2e; color: #eee; padding: 20px; border-radius: 8px; margin-bottom: 16px;">',
        f'<h2 style="margin: 0; color: #00d4aa;">Recursive Optimizer &mdash; Cycle #{snap["cycle"]}</h2>',
        f'<p style="margin: 8px 0 0; font-size: 15px;">{snap["date"]} &nbsp;|&nbsp; '
        '<span style="color:#fca5a5;font-weight:bold;">Safety gate blocked</span></p>',
        '<p style="margin: 8px 0 0; font-size: 14px; color: #ccc;">The unit-test preflight '
        '(<code style="color:#93c5fd;">pytest tests/unit</code>) failed. No journal aggregation, '
        'counterfactual analysis, or threshold writes ran. <b>Live thresholds are unchanged.</b></p>',
        '<p style="margin: 8px 0 0; font-size: 13px; color: #aaa;">Fix failing tests and redeploy. '
        'Use <code>SKIP_UNIT_SAFETY_GATE=1</code> or <code>SKIP_OPTIMIZER_GATE=1</code> only in an emergency.</p>',
        '</div>',
        '<div style="border-left: 4px solid #28a745; padding: 16px; margin-bottom: 16px; background: #f0fff4;">',
        '<h3 style="margin: 0 0 8px; color: #28a745;">Auto-applied threshold changes</h3>',
        '<p style="margin: 4px 0; color: #666;"><b>None</b> &mdash; run stopped at the safety gate before any changes could be evaluated or applied.</p>',
        '</div>',
        '</div>',
    ]
    return ''.join(parts)


def _log_and_email_safety_gate_blocked(cycle_num: int) -> Dict[str, Any]:
    """Append log entry, send the usual optimizer email with in-body gate notice (not a separate message type)."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime('%Y-%m-%d')
    ts = now.isoformat()
    cycle_data = _build_safety_gate_cycle_data(cycle_num, ts, date_str)
    log = _load_optimizer_log()
    log.append({
        'timestamp': ts,
        'cycle': cycle_num,
        'snapshot': cycle_data['snapshot'],
        'auto_applied': [],
        'pending_review': [],
        'loss_based_recommendations': [],
        'evaluations': [],
        'safety_gate_blocked': True,
    })
    _save_optimizer_log(log)
    try:
        body = format_report_html(cycle_data)
        from alert_system import AlertSystem
        AlertSystem().send_optimizer_report(
            f"Recursive Optimizer — Cycle #{cycle_num} — {date_str}",
            body,
        )
        logger.info("[optimizer] Cycle #%s — safety gate blocked — optimizer email sent", cycle_num)
    except Exception as e:
        logger.error("[optimizer] Email send failed (safety gate blocked): %s", e)
    return {
        'error': 'unit tests failed — thresholds not applied',
        'safety_gate_blocked': True,
        'cycle': cycle_num,
        'snapshot': cycle_data['snapshot'],
        'auto_applied': [],
        'pending_review': [],
        'evaluations': [],
    }


def format_report_html(cycle_data: Dict) -> str:
    """Format report to match V5 structure exactly. Report structure is mirrored on the dashboard Performance tab; keep in sync."""
    if cycle_data.get('safety_gate_blocked'):
        return _format_safety_gate_blocked_html(cycle_data)
    snap = cycle_data['snapshot']
    auto = cycle_data.get('auto_applied', [])
    pending = cycle_data.get('pending_review', [])
    evals = cycle_data.get('evaluations', [])
    perf = cycle_data.get('path_performance', [])
    missed = cycle_data.get('missed_summary', {})
    screen_sum = cycle_data.get('screening_summary', {})
    loss_analysis = cycle_data.get('loss_analysis', {})
    loss_based_recs = cycle_data.get('loss_based_recommendations', [])
    manual_overrides = cycle_data.get('manual_override_positions', [])

    _td = 'style="padding: 5px 8px; border-bottom: 1px solid #eee;"'
    _th = 'style="padding: 6px 8px; text-align: left; border-bottom: 2px solid #ddd;"'

    html = []
    html.append('<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 700px;">')

    # --- Data Collection Mode banner (shown when thresholds are frozen) ---
    if cycle_data.get('data_collection_mode'):
        _dc_live = cycle_data.get('data_collection_closed_trades', 0)
        _dc_paper = cycle_data.get('data_collection_paper_closed', 0)
        _dc_target = cycle_data.get('data_collection_target', MATURITY_TRADE_TARGET)
        _dc_remaining = max(0, _dc_target - _dc_live)
        _dc_inst = cycle_data.get('data_collection_instrumented_live', 0)
        _dc_inst_tgt = cycle_data.get('data_collection_instrumented_target')
        _inst_line = ''
        if _dc_inst_tgt is not None:
            _inst_rem = max(0, int(_dc_inst_tgt) - _dc_inst)
            _inst_line = (
                f' Instrumented live (fear/greed at entry): <b>{_dc_inst}/{_dc_inst_tgt}</b>'
                f' ({_inst_rem} remaining on quality gate).'
            )
        html.append(
            '<div style="background: #fff3cd; border-left: 5px solid #ffc107; padding: 14px 16px; '
            'margin-bottom: 16px; border-radius: 4px;">'
            '<strong style="color: #856404;">&#9888; Data Collection Mode — Thresholds Frozen</strong>'
            f'<p style="margin: 6px 0 0; color: #664d03; font-size: 14px;">'
            f'<b>{_dc_live}/{_dc_target}</b> live closed trades toward auto-apply'
            f' (<b>{_dc_remaining} remaining</b>). '
            f'Paper closes (<b>{_dc_paper}</b>) are excluded from this gate.{_inst_line} '
            f'Analysis ran in full; changes below are <em>observed only</em> and were NOT written to live thresholds.</p>'
            '</div>'
        )

    # --- TOP PRIORITY: Followup Reminders (carved out above everything) ---
    due_fus_top = cycle_data.get('due_followups', [])
    if due_fus_top:
        html.append(
            '<div style="background: #fff0f6; border: 2px solid #d63384; border-left: 8px solid #d63384; '
            'padding: 16px 18px; margin-bottom: 16px; border-radius: 6px;">'
        )
        html.append(
            f'<div style="font-size: 11px; letter-spacing: 1px; color: #d63384; font-weight: bold; margin-bottom: 4px;">'
            f'&#9873; ACTION REQUIRED &mdash; FOLLOWUP REMINDERS</div>'
        )
        html.append(
            f'<h3 style="margin: 0 0 10px; color: #6f1d4b;">{len(due_fus_top)} open followup(s) due for review</h3>'
        )
        html.append('<ol style="margin: 0 0 4px 18px; padding: 0;">')
        for fu in due_fus_top:
            topic = fu.get('topic', '(no topic)')
            notes = fu.get('notes', '')
            created = (fu.get('created') or '')[:10]
            fid = fu.get('id', '')
            trigger = []
            if fu.get('revisit_on'):
                trigger.append(f'revisit_on {fu["revisit_on"]}')
            if fu.get('revisit_after_closed_trades') is not None:
                trigger.append(f'&ge; {fu["revisit_after_closed_trades"]} closed trades')
            ctx = fu.get('context') or {}
            ctx_bits = []
            for k, v in list(ctx.items())[:6]:
                ctx_bits.append(f'<code style="font-size:11px;background:#fce8f1;padding:1px 4px;border-radius:3px;">{k}={v}</code>')
            html.append(
                f'<li style="margin-bottom: 12px;">'
                f'<div style="font-size: 15px; font-weight: bold; color: #222;">{topic}</div>'
                f'<div style="font-size: 11px; color: #888; margin: 2px 0 6px;">id {fu.get("id","")} &middot; created {created} &middot; trigger: {"; ".join(trigger) or "n/a"}</div>'
                f'<div style="font-size: 13px; color: #444; line-height: 1.45;">{notes}</div>'
            )
            if ctx_bits:
                html.append(f'<div style="margin-top: 6px;">{" ".join(ctx_bits)}</div>')
            if fid:
                html.append(
                    f'<div style="margin-top: 8px; font-size: 11px; color: #666;">'
                    f'Resolve: <code>python -c "from recursive_optimizer import resolve_followup; resolve_followup(\'{fid}\', \'done\')"</code>'
                    f'</div>'
                )
            html.append('</li>')
        html.append('</ol>')
        html.append(
            '<p style="margin: 8px 0 0; font-size: 11px; color: #888;">'
            'These are carved-out reminders surfaced because their revisit trigger (date or trade-count) has fired. '
            'Review, act, then resolve so the next cycle is clean.</p>'
        )
        html.append('</div>')

    # --- V5 Header (dark box) ---
    html.append('<div style="background: #1a1a2e; color: #eee; padding: 20px; border-radius: 8px; margin-bottom: 16px;">')
    html.append(f'<h2 style="margin: 0; color: #00d4aa;">Recursive Optimizer &mdash; Cycle #{snap["cycle"]}</h2>')
    html.append(f'<p style="margin: 8px 0 0; font-size: 15px;">{snap["date"]}  |  Regime: <b>{snap["regime"]}</b>  |  '
                f'{snap.get("system_closed", 0)} closed / {snap.get("system_open", 0)} open (system trades)</p>')
    b7 = snap.get('booked_trades_7d', 0)
    b30 = snap.get('booked_trades_30d', 0)
    html.append(f'<p style="margin: 4px 0; font-size: 14px; color: #ccc;"><b>Booked (system trades)</b> — win rate (% of closed trades that were winners): 7d <b>{snap.get("win_rate_7d", 0)}%</b> ({b7} trades)  |  30d <b>{snap.get("win_rate_30d", 0)}%</b> ({b30} trades)</p>')
    html.append(f'<p style="margin: 4px 0; font-size: 14px; color: #ccc;">{snap.get("signals_screened_30d", 0)} signals screened (30d)</p>')
    maturing = sum(1 for e in evals if e.get('status') == 'maturing')
    override_line = f'  |  {snap["manual_override_count"]} manual override trades (excluded from analysis)' if snap.get('manual_override_count') else ''
    html.append(f'<p style="margin: 4px 0 0; font-size: 14px; color: #aaa;">Threshold changes: {len(auto)} auto-applied  |  {len(pending) + len(loss_based_recs)} optional (below bar)  |  '
                f'{maturing} still maturing (waiting for more trades){override_line}</p>')
    html.append('</div>')

    # --- Paper evidence (MODERATE counterfactual + policy skips) ---
    paper_report = cycle_data.get('paper_evidence_report') or {}
    paper_attr = paper_report.get('paper_attribution') or {}
    policy_skips = paper_report.get('policy_skip_summary') or {}
    promos = cycle_data.get('promotion_recommendations') or paper_attr.get('promotion_recommendations') or []
    if paper_attr.get('closed_count') or policy_skips.get('total'):
        html.append(
            '<div style="border-left: 4px solid #6f42c1; padding: 16px; margin-bottom: 16px; background: #f8f5ff;">'
        )
        html.append('<h3 style="margin: 0 0 8px; color: #5a32a3;">Paper Evidence (30d)</h3>')
        pc = paper_attr.get('closed_count', 0)
        pwr = paper_attr.get('win_rate_pct', 0)
        pexp = paper_attr.get('expectancy_pct')
        pexp_s = f'{pexp:.2f}%' if pexp is not None else 'n/a'
        html.append(
            f'<p style="margin: 0 0 8px; font-size: 13px; color: #444;">'
            f'Closed: <b>{pc}</b>  |  Win rate: <b>{pwr}%</b>  |  Expectancy: <b>{pexp_s}</b>  |  '
            f'MODERATE: <b>{paper_attr.get("moderate_count", 0)}</b></p>'
        )
        sk_total = policy_skips.get('total', 0)
        if sk_total:
            html.append(
                f'<p style="margin: 0 0 8px; font-size: 13px; color: #555;">'
                f'Live policy skips logged: <b>{sk_total}</b>  |  With 7d forward: <b>{policy_skips.get("with_forward", 0)}</b>  |  '
                f'Regrets (&ge;5% fwd): <b>{policy_skips.get("regret_count", 0)}</b>'
                f'{f"  |  Avg fwd: <b>{policy_skips.get('avg_forward_7d')}%</b>" if policy_skips.get("avg_forward_7d") is not None else ""}'
                f'</p>'
            )
        if promos:
            html.append('<p style="margin: 8px 0 4px; font-weight: bold; font-size: 13px;">Promotion-ready slices:</p><ul style="margin: 0;">')
            for pr in promos[:5]:
                html.append(
                    f'<li style="font-size: 12px;"><b>{pr.get("tier")}</b> / {pr.get("regime")} — '
                    f'n={pr.get("n_closed")}, WR={pr.get("win_rate_pct")}%, '
                    f'expectancy={pr.get("expectancy_pct")}%</li>'
                )
            html.append('</ul>')
        html.append(
            '<p style="margin: 8px 0 0; font-size: 11px; color: #888;">'
            'Relaxation counterfactuals may use paper at 0.75× weight; auto-apply requires live confirmation. '
            'Full report: <code>data/paper_evidence_report.json</code></p>'
        )
        html.append('</div>')

    # --- Friday: Weekly Consolidated Recommendations ---
    from datetime import datetime as _dt, timezone as _tz
    if _dt.now(_tz.utc).weekday() == 4:  # Friday
        log = _load_optimizer_log()
        weekly_recs, weekly_conflicts = _build_weekly_consolidated_recommendations(log)
        if weekly_recs or weekly_conflicts:
            html.append('<div style="border-left: 4px solid #17a2b8; padding: 16px; margin-bottom: 16px; background: #e8f4f8;">')
            html.append('<h3 style="margin: 0 0 8px; color: #117a8b;">Weekly Consolidated Recommendations (Fri)</h3>')
            html.append('<p style="margin: 0 0 12px; font-size: 13px; color: #555;">Recommendations from the last 7 days (incl. weekend) aggregated and conflict-checked. '
                        'Apply these once per week after review.</p>')
            if weekly_conflicts:
                html.append('<p style="margin: 0 0 8px; font-weight: bold; color: #856404;">Conflicts (excluded):</p><ul style="margin: 0 0 12px;">')
                for c in weekly_conflicts:
                    html.append(f'<li>{c}</li>')
                html.append('</ul>')
            if weekly_recs:
                html.append('<p style="margin: 0 0 8px; font-weight: bold;">Consolidated (2+ days, no conflicts):</p><ul style="margin: 0 0 8px;">')
                for r in weekly_recs:
                    param = r.get('parameter', '?')
                    sv = r.get('suggested_value')
                    if r.get('current_value') is not None and sv is not None:
                        html.append(f'<li><b>{param}</b>: {r["current_value"]} &rarr; {sv} &mdash; {r.get("reason", "")}')
                    else:
                        html.append(f'<li><b>{param}</b>: {sv} &mdash; {r.get("reason", "")}')
                    if isinstance(sv, (int, float)):
                        html.append(f'<br><span style="font-size: 12px;">To apply: <code>python -c "from recursive_optimizer import apply_single; '
                                    f'apply_single(\'{param}\', {sv})"</code></span>')
                    html.append('</li>')
                html.append('</ul>')
            html.append('</div>')

    # --- Loss Analysis (system trades only) ---
    losses = loss_analysis.get('losses', [])
    wins = loss_analysis.get('wins', [])
    if losses or wins:
        html.append('<div style="border-left: 4px solid #dc3545; padding: 16px; margin-bottom: 16px; background: #fff5f5;">')
        html.append('<h3 style="margin: 0 0 8px; color: #c82333;">Loss Analysis (system trades only)</h3>')
        html.append(f'<p style="margin: 4px 0;">{len(losses)} losses vs {len(wins)} wins  |  '
                    f'Avg loss: {loss_analysis.get("avg_loss_pct", 0):.1f}%  |  Avg win: +{loss_analysis.get("avg_win_pct", 0):.1f}%</p>')
        if losses:
            html.append('<table style="border-collapse: collapse; width: 100%; font-size: 13px;">')
            html.append(f'<tr style="background:#f5f5f5;"><th {_th}>Trade</th><th {_th}>Coin</th><th {_th}>P&amp;L</th>'
                        f'<th {_th}>Regime</th><th {_th}>Path</th><th {_th}>Score</th><th {_th}>Exit</th></tr>')
            for p in losses:
                tid = p.get('trade_id', f"TRD-{p.get('id', 0):05d}")
                sym = p.get('coin_symbol', p.get('coin_id', '?'))
                pnl = p.get('pnl_pct', 0) or 0
                regime = (p.get('market_regime') or p.get('regime') or '?').upper()
                path = p.get('quant_path') or 'default'
                score = p.get('confirmation_score', '')
                exit_r = (p.get('exit_reason') or '?').replace('_', ' ')
                html.append(f'<tr><td {_td}>{tid}</td><td {_td}>{sym}</td><td {_td} style="color:#dc3545">{pnl:.1f}%</td>'
                            f'<td {_td}>{regime}</td><td {_td}>{path}</td><td {_td}>{score}</td>'
                            f'<td {_td}>{exit_r}</td></tr>')
            html.append('</table>')
            # Patterns detected
            html.append('<p style="margin: 8px 0 4px; font-weight: bold;">Patterns detected:</p><ul style="margin: 0 0 8px;">')
            regimes = loss_analysis.get('loss_regimes', {})
            if regimes:
                html.append(f'<li>All {len(losses)} losses occurred in {", ".join(regimes.keys())} regime</li>')
            if loss_analysis.get('score_gap') is not None:
                html.append(f'<li>Avg confirmation score: losses={loss_analysis.get("avg_loss_score", 0):.1f} vs wins={loss_analysis.get("avg_win_score", 0):.1f} '
                            f'(gap: {loss_analysis["score_gap"]} pts)</li>')
            sl_hits = loss_analysis.get('stop_loss_hits', 0)
            html.append(f'<li>Stop-loss hits: {sl_hits}/{len(losses)}</li>')
            html.append('</ul>')
            if loss_based_recs:
                html.append('<p style="margin: 8px 0 4px; font-weight: bold;">Threshold recommendations from losses:</p><ul style="margin: 0 0 8px;">')
                for r in loss_based_recs:
                    reason = r.get('reason', '')
                    if r.get('current_value') is not None:
                        html.append(f'<li><b>{r["parameter"]}</b>: {r["current_value"]} &rarr; {r["suggested_value"]} &mdash; {reason}</li>')
                    else:
                        html.append(f'<li><b>{r["parameter"]}</b>: {r.get("suggested_value", reason)}</li>')
                html.append('</ul>')
        html.append('</div>')

    # --- Hold & Missed Opportunity Analysis (top N by regret, two-line explanations) ---
    html.append('<div style="border-left: 4px solid #20c997; padding: 16px; margin-bottom: 16px; background: #f0fff8;">')
    html.append('<h3 style="margin: 0 0 8px; color: #0ca678;">Hold &amp; Missed Opportunity Analysis</h3>')
    total = screen_sum.get('total', 0)
    html.append(f'<p style="margin: 4px 0;">{total} signals screened in last 30d</p>')
    tiers = screen_sum.get('tier_breakdown', {})
    if tiers:
        parts = [f'{k}: {v}' for k, v in sorted(tiers.items(), key=lambda x: -x[1])]
        html.append(f'<p style="margin: 4px 0;">Signal breakdown: {", ".join(parts)}</p>')
    rec_missed = missed.get('recommendations_missed', 0)
    sell_missed = missed.get('sell_signals_missed', 0)
    screen_missed = missed.get('screening_missed', 0)
    if rec_missed or sell_missed or screen_missed:
        html.append(f'<p style="margin: 4px 0;">{rec_missed} HOLD/WEAK_BUY signals later gained &gt;5% in 7 days; '
                    f'{sell_missed} SELL signals later gained &gt;5%; '
                    f'{screen_missed} screening failures later gained &gt;5%</p>')
    top_missed = cycle_data.get('top_missed_by_regret', [])
    try:
        from thresholds import PARAM_IMPACT, PARAM_REGRET_REASON
    except Exception:
        PARAM_IMPACT = {}
        PARAM_REGRET_REASON = {}
    _TIER_TO_PARAM = {'MODERATE_BUY': 'DIP_MIN_SCORE', 'STRONG_BUY': 'PULLBACK_MIN_SCORE', 'ULTRA_BUY': 'BEAR_REGIME_MIN_SCORE', 'HOLD': 'BEAR_REGIME_MIN_SCORE', 'WEAK_BUY': 'DIP_MIN_SCORE'}
    if top_missed:
        html.append('<p style="margin: 8px 0 4px; font-weight: bold;">Top missed by regret (7d gain %):</p>')
        for m in top_missed:
            sym = m.get('symbol') or m.get('coin_id') or '?'
            coin_name = m.get('coin_name') or ''
            label = f'{coin_name} ({sym})' if coin_name and coin_name != sym else sym
            ts = (m.get('timestamp') or '')[:10]
            date_str = f' on {ts}' if ts else ''
            o7 = m.get('outcome_7d', 0)
            src = m.get('source', '')
            if src == 'sell':
                impact = 'Consensus said SELL but the coin rallied; the bearish read was wrong.'
                regret = 'Sentiment models may have been too negative; check if regime or model weights caused the SELL.'
            else:
                tier = (m.get('quant_tier') or m.get('consensus_quality') or '').strip().upper() or 'HOLD'
                param = _TIER_TO_PARAM.get(tier, 'DIP_MIN_SCORE')
                impact = PARAM_IMPACT.get(param, 'Threshold affects which signals become BUY.')
                regret = PARAM_REGRET_REASON.get(param, 'Relaxing may capture more of these moves.')
            html.append(f'<p style="margin: 6px 0 2px;"><b>{label}</b> +{o7:.1f}% (7d){date_str} &nbsp; [{src}]</p>')
            html.append(f'<p style="margin: 0 0 2px; font-size: 12px; color: #555;"><b>Impact:</b> {impact}</p>')
            html.append(f'<p style="margin: 0 0 8px; font-size: 12px; color: #555;"><b>Regret:</b> {regret}</p>')
    if not rec_missed and not screen_missed:
        html.append('<p style="margin: 4px 0; color: #666;">No outcome data available yet. The system needs 7+ days of screening data '
                    'before outcome_7d can be backfilled with real prices.</p>')
    html.append('</div>')

    # --- Performance by Regime & Quant Path (system trades only) ---
    html.append('<div style="border-left: 4px solid #6f42c1; padding: 16px; margin-bottom: 16px; background: #f8f0ff;">')
    html.append('<h3 style="margin: 0 0 8px; color: #6f42c1;">Performance by Regime &amp; Quant Path (system trades only)</h3>')
    if perf:
        html.append('<table style="border-collapse: collapse; width: 100%; font-size: 13px;">')
        html.append(f'<tr style="background:#f5f5f5;"><th {_th}>Regime</th><th {_th}>Path</th><th {_th}>Trades</th>'
                    f'<th {_th}>Win Rate</th><th {_th}>Avg Win</th><th {_th}>Avg Loss</th><th {_th}>Net P&amp;L</th></tr>')
        for row in perf:
            wrc = '#28a745' if row['win_rate'] >= 55 else '#dc3545' if row['win_rate'] < 45 else '#333'
            pc = '#28a745' if row['net_pnl'] >= 0 else '#dc3545'
            html.append(f'<tr><td {_td}>{row["regime"]}</td><td {_td}>{row["path"]}</td><td {_td}>{row["trades"]}</td>'
                        f'<td style="padding:5px 8px;border-bottom:1px solid #eee;color:{wrc}">{row["win_rate"]:.1f}%</td>'
                        f'<td style="padding:5px 8px;border-bottom:1px solid #eee;color:#28a745">+{row["avg_win"]:.1f}%</td>'
                        f'<td style="padding:5px 8px;border-bottom:1px solid #eee;color:#dc3545">{row["avg_loss"]:.1f}%</td>'
                        f'<td style="padding:5px 8px;border-bottom:1px solid #eee;color:{pc}">${row["net_pnl"]:.2f}</td></tr>')
        html.append('</table>')
    else:
        html.append('<p style="color: #666;">Insufficient system trades for path performance table.</p>')
    html.append('</div>')

    # --- Manual Override Trades (excluded from analysis) ---
    if manual_overrides:
        html.append('<div style="border-left: 4px solid #fd7e14; padding: 16px; margin-bottom: 16px; background: #fff8f0;">')
        html.append('<h3 style="margin: 0 0 8px; color: #e8590c;">Manual Override Trades (excluded from analysis)</h3>')
        html.append('<p style="margin: 4px 0; font-size: 13px; color: #666;">These positions were entered when the system said HOLD. '
                    'They are your discretionary trades and are NOT included in system win rates, loss analysis, or threshold optimization.</p>')
        for p in manual_overrides:
            sym = p.get('coin_symbol', p.get('coin_id', '?'))
            regime = (p.get('market_regime') or p.get('regime') or '?').upper()
            pnl = p.get('pnl_pct', 0) or 0
            html.append(f'<p style="margin: 2px 0;">{sym} ({regime}) &mdash; {pnl:+.1f}% &mdash; CLOSED</p>')
        html.append('</div>')

    # --- Auto-applied threshold changes (what, why important, how it improves) ---
    try:
        from thresholds import PARAM_DESCRIPTIONS, PARAM_IMPACT
    except Exception:
        PARAM_DESCRIPTIONS = {}
        PARAM_IMPACT = {}
    html.append('<div style="border-left: 4px solid #28a745; padding: 16px; margin-bottom: 16px; background: #f0fff4;">')
    html.append('<h3 style="margin: 0 0 8px; color: #28a745;">Auto-applied threshold changes</h3>')
    if auto:
        for a in auto:
            param = a.get('parameter', '')
            desc = PARAM_DESCRIPTIONS.get(param, param or 'Threshold')
            impact = PARAM_IMPACT.get(param, 'Improves expected P&L by filtering marginal trades.')
            html.append(f'<p style="margin: 8px 0 2px;"><b>{param}</b>: {a.get("old_value")} &rarr; {a.get("new_value")}</p>')
            html.append(f'<p style="margin: 0 0 2px; font-size: 13px; color: #555;"><b>Why this change was made:</b> {desc}</p>')
            html.append(f'<p style="margin: 0 0 8px; font-size: 13px; color: #555;"><b>How it\'s expected to improve signals in the future:</b> {impact}</p>')
    else:
        html.append('<p style="margin: 4px 0; color: #666;">No changes met the safety criteria this cycle.</p>')
    html.append('</div>')

    # --- Optional recommendations (below auto-apply bar) ---
    html.append('<div style="border-left: 4px solid #f0ad4e; padding: 16px; margin-bottom: 16px; background: #fffbf0;">')
    all_recs = loss_based_recs + pending
    cycle_num = snap.get('cycle', 0)
    html.append('<h3 style="margin: 0 0 8px; color: #d4920a;">Optional recommendations (below auto-apply bar)</h3>')
    html.append('<p style="margin: 0 0 8px; font-size: 12px; color: #666;">These did not meet the auto-apply bar (min improvement, 0 winners lost, bounds). Apply manually or defer to hide from future emails.</p>')
    if all_recs:
        for r in all_recs:
            param = r.get('parameter', '?')
            desc = r.get('description', '')
            suggested = r.get('suggested_value')
            rec_id = _rec_id(cycle_num, param, suggested)
            html.append(f'<p style="margin: 8px 0 4px;"><b>{param}</b>')
            if desc:
                html.append(f' ({desc})')
            html.append('</p>')
            if r.get('current_value') is not None and suggested is not None:
                html.append(f'<p style="margin: 0 0 4px;">Suggested: {r["current_value"]} &rarr; {suggested}</p>')
            elif suggested:
                html.append(f'<p style="margin: 0 0 4px;">Suggested: {suggested}</p>')
            html.append(f'<p style="margin: 0 0 4px; color: #666;">{r.get("reason", "")}</p>')
            if r in loss_based_recs:
                html.append('<p style="margin: 0 0 4px; font-size: 12px;">From loss analysis</p>')
            html.append(f'<p style="margin: 0 0 4px; font-size: 12px;">ID: <code>{rec_id}</code></p>')
            if param and isinstance(suggested, (int, float)):
                html.append(f'<p style="margin: 0 0 4px; font-size: 12px;">To apply: <code>python -c "from recursive_optimizer import apply_single; '
                            f'apply_single(\'{param}\', {suggested})"</code> or <code>apply_recommendation(\'{rec_id}\')</code></p>')
            html.append(f'<p style="margin: 0 0 8px; font-size: 12px;">To defer (hide from future emails): <code>python -c "from recursive_optimizer import defer_recommendation; defer_recommendation(\'{rec_id}\')"</code></p>')
    else:
        html.append('<p style="margin: 4px 0; color: #666;">No recommendations below the bar this cycle.</p>')
    html.append('</div>')

    # --- How are applied changes performing? ---
    html.append('<div style="border-left: 4px solid #2196F3; padding: 16px; margin-bottom: 16px; background: #f0f7ff;">')
    html.append('<h3 style="margin: 0 0 8px; color: #1976D2;">How are applied changes performing?</h3>')
    if evals:
        for e in evals:
            html.append(f'<p style="margin: 4px 0;"><b>{e["parameter"]}</b> changed {e["old_value"]} &rarr; {e["new_value"]} on {e["applied_at"][:10]} &nbsp;|&nbsp; '
                        f'{e["trades_since"]}/{e["target_trades"]} trades &nbsp;|&nbsp; {e["status"].upper()}</p>')
            html.append(f'<p style="margin: 0 0 8px; color: #666;">{e["verdict"]}</p>')
    else:
        html.append('<p style="margin: 4px 0; color: #666;">No previous changes to evaluate yet.</p>')
    html.append('</div>')

    # -----------------------------------------------------------------------
    # Phase 1 Diagnostics: measurement-only sections (no auto-action)
    # -----------------------------------------------------------------------

    # 1A: Model Calibration Diagnostic
    model_cal = cycle_data.get('model_calibration', {})
    accuracy = model_cal.get('accuracy', {})
    cal_curve = model_cal.get('calibration_curve', [])
    drift_warnings = model_cal.get('drift_warnings', [])
    if accuracy.get('count', 0) > 0 or cal_curve:
        html.append('<div style="border-left: 4px solid #9c27b0; padding: 16px; margin-bottom: 16px; background: #f9f5ff;">')
        html.append('<h3 style="margin: 0 0 8px; color: #9c27b0;">Model Calibration (diagnostic)</h3>')
        if accuracy.get('count', 0) > 0:
            wr7 = accuracy.get('win_rate_7d', 0) * 100
            avg_c = accuracy.get('avg_confidence', 0)
            html.append(f'<p style="margin: 4px 0;"><b>Predictions tracked</b>: {accuracy["count"]} &nbsp;|&nbsp; '
                        f'<b>7d win rate</b>: {wr7:.1f}% &nbsp;|&nbsp; <b>Avg confidence</b>: {avg_c:.1f}%</p>')
        if drift_warnings:
            html.append('<p style="margin: 4px 0; color: #d32f2f;"><b>Calibration drift detected</b>:</p>')
            for dw in drift_warnings:
                html.append(f'<p style="margin: 2px 0 2px 12px; color: #d32f2f;">'
                            f'Bin {dw["bin"]}: predicted {dw["predicted"]:.0f}%, actual {dw["actual_wr"]:.1f}% '
                            f'(drift {dw["drift_pp"]:.1f}pp)</p>')
        if cal_curve:
            html.append('<table style="border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 8px;">')
            html.append(f'<tr style="background:#f5f5f5;"><th {_th}>Confidence Bin</th><th {_th}>Predicted</th>'
                        f'<th {_th}>Actual Win Rate</th><th {_th}>N</th></tr>')
            for row in cal_curve:
                html.append(f'<tr><td {_td}>{row["bin_low"]:.0f}&ndash;{row["bin_high"]:.0f}%</td>'
                            f'<td {_td}>{row["predicted_conf"]:.0f}%</td>'
                            f'<td {_td}>{row["actual_win_rate"]*100:.1f}%</td>'
                            f'<td {_td}>{row["count"]}</td></tr>')
            html.append('</table>')
        html.append('<p style="margin: 8px 0 0; color: #999; font-size: 11px;">Diagnostic only &mdash; no auto-action. '
                    'Review for 2&ndash;3 cycles before wiring into threshold tuning.</p>')
        html.append('</div>')

    # 1B: Veto Health Diagnostic
    veto_acc = cycle_data.get('veto_accuracy', {})
    if veto_acc.get('total_vetoed', 0) > 0:
        html.append('<div style="border-left: 4px solid #ff6f00; padding: 16px; margin-bottom: 16px; background: #fff8e1;">')
        html.append('<h3 style="margin: 0 0 8px; color: #ff6f00;">Veto Health (diagnostic)</h3>')
        total_v = veto_acc['total_vetoed']
        correct = veto_acc.get('correct_veto', 0)
        missed = veto_acc.get('missed_by_veto', 0)
        unknown = veto_acc.get('unknown', 0)
        ratio = correct / total_v if total_v > 0 else 0
        html.append(f'<p style="margin: 4px 0;"><b>Total vetoed</b>: {total_v} &nbsp;|&nbsp; '
                    f'<b>Correct</b>: {correct} ({ratio*100:.0f}%) &nbsp;|&nbsp; '
                    f'<b>Missed opps</b>: {missed} &nbsp;|&nbsp; <b>Unknown</b>: {unknown}</p>')
        if missed > correct:
            html.append('<p style="margin: 4px 0; color: #d32f2f;"><b>Warning</b>: veto is over-aggressive '
                        f'(missed {missed} > correct {correct}). Consider relaxing veto thresholds.</p>')
        elif correct > 2 * missed and total_v >= 5:
            html.append('<p style="margin: 4px 0; color: #2e7d32;"><b>Healthy</b>: veto accuracy is strong '
                        f'({correct} correct vs {missed} missed).</p>')
        html.append('<p style="margin: 8px 0 0; color: #999; font-size: 11px;">Diagnostic only &mdash; no auto-action. '
                    'Establishing baseline before wiring into optimizer bias.</p>')
        html.append('</div>')

    # 1C: Relaxation Candidates Diagnostic
    top_missed = cycle_data.get('top_missed_by_regret', [])
    if top_missed:
        html.append('<div style="border-left: 4px solid #00796b; padding: 16px; margin-bottom: 16px; background: #e0f2f1;">')
        html.append('<h3 style="margin: 0 0 8px; color: #00796b;">Relaxation Candidates (diagnostic)</h3>')
        html.append(f'<p style="margin: 4px 0;">Top {min(len(top_missed), 5)} missed opportunities by 7d regret:</p>')
        html.append('<table style="border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 8px;">')
        html.append(f'<tr style="background:#f5f5f5;"><th {_th}>Symbol</th><th {_th}>Source</th>'
                    f'<th {_th}>7d Gain</th><th {_th}>Regime</th></tr>')
        for m in top_missed[:5]:
            sym = m.get('symbol', '?')
            src = m.get('source', '?')
            o7 = m.get('outcome_7d', 0)
            reg = m.get('regime', '?')
            html.append(f'<tr><td {_td}>{sym}</td><td {_td}>{src}</td>'
                        f'<td {_td}>+{o7:.1f}%</td><td {_td}>{reg}</td></tr>')
        html.append('</table>')
        html.append(f'<p style="margin: 8px 0 0;">Total missed: {len(top_missed)} signals. '
                    'These could indicate over-tight thresholds.</p>')
        html.append('<p style="margin: 4px 0 0; color: #999; font-size: 11px;">Diagnostic only &mdash; no auto-relaxation. '
                    'Review for 2&ndash;3 cycles to confirm pattern is real (not noise).</p>')
        html.append('</div>')

    # --- Adversarial Backtester Findings ---
    adv_issues = cycle_data.get('adversarial_structural_issues', [])
    adv_qw = cycle_data.get('adversarial_quick_wins', [])
    if adv_issues or adv_qw:
        html.append('<div style="border-left: 4px solid #dc3545; padding: 14px; margin-bottom: 16px; background: #fff5f5;">')
        html.append('<h3 style="margin: 0 0 10px; color: #dc3545;">Adversarial Backtester Findings</h3>')
        if adv_issues:
            html.append(f'<p style="margin: 0 0 8px; font-weight: bold;">{len(adv_issues)} Structural Issue(s):</p>')
            html.append('<ul style="margin: 0 0 12px; padding-left: 20px;">')
            for si in adv_issues[:8]:
                sev = si.get('severity', 'medium').upper()
                sev_color = '#dc3545' if sev == 'CRITICAL' else '#fd7e14' if sev == 'HIGH' else '#6c757d'
                html.append(f'<li style="margin-bottom: 6px;">'
                            f'<span style="color: {sev_color}; font-weight: bold;">[{sev}]</span> '
                            f'{si.get("issue", "")}<br>'
                            f'<span style="color: #666; font-size: 12px;">&rarr; {si.get("recommendation", "")}</span>'
                            f'</li>')
            html.append('</ul>')
        if adv_qw:
            html.append(f'<p style="margin: 0 0 8px; font-weight: bold;">Quick Wins:</p>')
            html.append('<ul style="margin: 0; padding-left: 20px;">')
            for qw in adv_qw[:5]:
                html.append(f'<li style="margin-bottom: 4px; font-size: 13px;">{qw}</li>')
            html.append('</ul>')
        html.append('<p style="margin: 8px 0 0; color: #999; font-size: 11px;">From last adversarial backtest run. '
                    'These are structural findings, not parameter tweaks.</p>')
        html.append('</div>')

    html.append('<hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">')
    html.append('<p style="color: #999; font-size: 11px;">Swing Trader v1.0 &mdash; pure Python, zero LLM cost</p>')
    html.append('</div>')

    return '\n'.join(html)


def _pending_reason(rec: Dict) -> str:
    min_trades_auto = _get_optimizer_threshold_int('MIN_TRADES_FOR_AUTO', MIN_TRADES_FOR_AUTO)
    reasons = []
    if rec.get('confidence') != 'high':
        reasons.append(f'only {rec["evidence_trades"]} trades (need {min_trades_auto})')
    if rec.get('winners_lost', 0) > 0:
        reasons.append(f'{rec["winners_lost"]} winning trade(s) would be filtered')
    if not reasons:
        reasons.append('max auto-apply limit reached this cycle')
    return '; '.join(reasons)


# ---------------------------------------------------------------------------
# Path performance table builder
# ---------------------------------------------------------------------------

def _build_path_performance_system(system_trades: List[Dict]) -> List[Dict]:
    """From system trades (trade journal). Uses market_regime with regime fallback; excludes UNKNOWN."""
    _excluded_regimes = {'UNKNOWN', 'PRE_PIPELINE', ''}
    buckets = {}
    for t in system_trades:
        regime = (t.get('market_regime') or t.get('regime') or '').strip().upper()
        if not regime or regime in _excluded_regimes:
            continue
        key = ((t.get('quant_path') or 'default'), regime)
        buckets.setdefault(key, []).append(t)

    rows = []
    for (path, regime), group in sorted(buckets.items(), key=lambda x: -len(x[1])):
        if len(group) < MIN_TRADES_FOR_PERF_TABLE:
            continue
        wins = [t for t in group if t.get('outcome') == 'win']
        losses = [t for t in group if t.get('outcome') == 'loss']
        wr = len(wins) / len(group) * 100
        avg_w = _avg_pnl(wins) if wins else 0
        avg_l = _avg_pnl(losses) if losses else 0
        net = sum(t.get('pnl_usd', 0) or 0 for t in group)
        rows.append({
            'path': path,
            'regime': regime,
            'trades': len(group),
            'win_rate': wr,
            'avg_win': avg_w,
            'avg_loss': avg_l,
            'net_pnl': net,
        })
    return rows


# ---------------------------------------------------------------------------
# Orchestrator (single pipeline shared by run_optimization_cycle and run_full_recursive_optimizer_cycle)
# ---------------------------------------------------------------------------

def _run_cycle_impl(
    data: Dict,
    cf_results: List[Dict],
    list_to_apply: List[Dict],
    pending_for_display: List[Dict],
    cycle_num: int,
) -> Dict:
    """Shared implementation: apply list_to_apply, evaluate, build cycle_data, log, email. Reused by both cycle entry points."""
    # Capture freeze state before apply (apply_safe_changes also checks, but we need it for reporting)
    _dc_fields = _data_collection_cycle_fields()
    _frozen = _dc_fields.get('data_collection_mode', False)
    _closed_trades = _dc_fields.get('data_collection_closed_trades', 0)
    _trade_target = _dc_fields.get('data_collection_target', MATURITY_TRADE_TARGET)
    applied = apply_safe_changes(list_to_apply)
    # Audit F2 (2026-07-04): the 7d win-rate metadata write to live_thresholds.json is
    # removed — reporting metadata does not belong in the live trading config, and no
    # automated path may write that file. The same fields live in the cycle snapshot
    # and optimizer_log.json, which reports read.
    evaluations = evaluate_previous_cycle(data)
    path_perf = _build_path_performance_system(data.get('system_trades_closed', []))

    snapshot = {
        'cycle': cycle_num,
        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'regime': data['current_regime'],
        'system_closed': data.get('system_closed', 0),
        'system_open': data.get('system_open', 0),
        'total_trades': data.get('system_closed', 0) + data.get('system_open', 0),
        'win_rate_7d': data['win_rate_7d'],
        'win_rate_30d': data['win_rate_30d'],
        'booked_trades_7d': len(data.get('trades_7d', [])),
        'booked_trades_30d': len(data.get('trades_30d', [])),
        'signals_screened_30d': data.get('signals_screened_30d', 0),
        'manual_override_count': data.get('manual_override_count', 0),
    }

    _all_trades = data['trades']
    data_quality = {
        'system_trades_count': len(_all_trades),
        'has_confirmation_score': sum(1 for t in _all_trades if t.get('confirmation_score') is not None),
        'has_fear_greed': sum(1 for t in _all_trades if t.get('fear_greed') is not None),
        'has_funding_rate': sum(1 for t in _all_trades if t.get('funding_rate') is not None),
        'has_price_change_24h': sum(1 for t in _all_trades if t.get('price_change_24h') is not None),
        'min_required': _get_optimizer_threshold_int('MIN_TRADES_FOR_REC', MIN_TRADES_FOR_REC),
        'params_analyzed': len(cf_results),
    }

    loss_based_filtered, pending_filtered = _filter_recommendations_for_display(
        data.get('loss_based_recommendations', []),
        pending_for_display,
        cycle_num,
    )

    cycle_data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'cycle': cycle_num,
        **_dc_fields,
        'snapshot': snapshot,
        'auto_applied': applied,
        'pending_review': pending_filtered,
        'evaluations': evaluations,
        'path_performance': path_perf,
        'missed_summary': data.get('missed_summary', {}),
        'learning_report': data.get('learning_report', {}),
        'data_quality': data_quality,
        'open_positions': data.get('open_positions', []),
        'closed_positions': data.get('closed_positions', []),
        'system_trades_closed': data.get('system_trades_closed', []),
        'manual_override_positions': data.get('manual_override_positions', []),
        'perf_stats': data.get('perf_stats', {}),
        'screening_summary': data.get('screening_summary', {}),
        'loss_analysis': data.get('loss_analysis', {}),
        'loss_based_recommendations': loss_based_filtered,
        'top_missed_by_regret': data.get('top_missed_by_regret', []),
        # Phase 1 diagnostics (measurement only, no auto-action)
        'model_calibration': data.get('model_calibration', {}),
        'veto_accuracy': data.get('veto_accuracy', {}),
        # Adversarial backtester findings (structural vulnerabilities)
        'adversarial_quick_wins': (data.get('adversarial_report') or {}).get('quick_wins', []),
        'adversarial_structural_issues': (data.get('adversarial_report') or {}).get('structural_issues', []),
        # Followup reminders whose revisit trigger has fired (see add_followup)
        'due_followups': get_due_followups(closed_trades=_closed_trades or 0),
        'paper_evidence_report': data.get('paper_evidence_report', {}),
        'promotion_recommendations': (
            (data.get('paper_evidence_report') or {}).get('paper_attribution') or {}
        ).get('promotion_recommendations', []),
    }

    log = _load_optimizer_log()
    log.append({
        'timestamp': cycle_data['timestamp'],
        'cycle': cycle_num,
        'snapshot': snapshot,
        'auto_applied': applied,
        'pending_review': [{'parameter': p['parameter'], 'suggested_value': p['suggested_value'],
                            'reason': _pending_reason(p)} for p in pending_for_display],
        'loss_based_recommendations': [
            {'parameter': r['parameter'], 'current_value': r.get('current_value'),
             'suggested_value': r.get('suggested_value'), 'reason': r.get('reason', '')}
            for r in data.get('loss_based_recommendations', [])
        ],
        'evaluations': evaluations,
        'due_followups': cycle_data.get('due_followups', []),
    })
    _save_optimizer_log(log)

    try:
        body_html = format_report_html(cycle_data)
        from alert_system import AlertSystem
        alerts = AlertSystem()
        subject = f"Recursive Optimizer — Cycle #{cycle_num} — {snapshot['date']}"
        _due_count = len(cycle_data.get('due_followups', []) or [])
        if _due_count:
            subject += f" — ⚑ {_due_count} FOLLOWUP{'S' if _due_count != 1 else ''} DUE"
        if cycle_data.get('data_collection_mode'):
            _dc_closed = cycle_data.get('data_collection_closed_trades', 0)
            _dc_target = cycle_data.get('data_collection_target', 100)
            subject += f" — [DATA COLLECTION {_dc_closed}/{_dc_target} trades — no writes]"
        elif applied:
            subject += f" — {len(applied)} auto-applied"
        alerts.send_optimizer_report(subject, body_html)
        logger.info(f"[optimizer] Cycle #{cycle_num} complete — {len(applied)} applied, {len(pending_for_display)} pending, email sent")
    except Exception as e:
        logger.error(f"[optimizer] Email send failed: {e}")

    return cycle_data


def run_optimization_cycle() -> Dict:
    """Daily optimization cycle (manual-review mode): rank_and_filter limits auto-apply to 3 safe changes; rest need review."""
    if not _cycle_lock.acquire(blocking=False):
        logger.warning("[optimizer] Cycle already in progress — skipping concurrent call")
        return {'error': 'cycle already in progress'}
    try:
        return _run_optimization_cycle_locked()
    finally:
        _cycle_lock.release()


def _run_optimization_cycle_locked() -> Dict:
    logger.info("[optimizer] Starting recursive optimization cycle...")
    log = _load_optimizer_log()
    cycle_num = (log[-1]['cycle'] + 1) if log else 1
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if log and log[-1].get('snapshot', {}).get('date') == today and not log[-1].get('safety_gate_blocked'):
        logger.warning(f"[optimizer] Cycle already ran today ({today}), skipping duplicate run")
        return log[-1]
    if not run_unit_safety_gate():
        logger.error("[optimizer] Safety gate failed — aborting optimizer cycle")
        return _log_and_email_safety_gate_blocked(cycle_num)
    # Validate live thresholds are within optimizer bounds before proceeding
    clamped = validate_live_thresholds_bounds()
    if clamped:
        logger.warning("[optimizer] Clamped %d out-of-bounds threshold(s) before cycle: %s",
                        len(clamped), [c['parameter'] for c in clamped])
    try:
        data = aggregate()
    except Exception as e:
        logger.error(f"[optimizer] Aggregate failed: {e}")
        return {'error': str(e)}

    try:
        cf_results = counterfactual_analysis(data)
    except Exception as e:
        logger.error(f"[optimizer] Counterfactual failed: {e}")
        cf_results = []

    auto_recs, pending_recs = rank_and_filter(cf_results)

    frozen, closed_trades, trade_target = is_data_collection_mode()
    if frozen:
        logger.info(
            "[optimizer] Data collection mode active (%d/%d closed trades) — "
            "analysis complete but %d threshold change(s) will NOT be applied",
            closed_trades, trade_target, len(auto_recs),
        )
        pending_recs = auto_recs + pending_recs
        auto_recs = []

    return _run_cycle_impl(data, cf_results, auto_recs, pending_recs, cycle_num)


def run_full_recursive_optimizer_cycle(
    min_improvement: float = DEFAULT_MIN_IMPROVEMENT,
    max_changes_per_cycle: Optional[int] = None,
) -> Dict:
    """Fully automatic recursive optimizer: auto-applies all recommendations meeting min_improvement, 0 winners lost, and bounds. No manual step."""
    if not _cycle_lock.acquire(blocking=False):
        logger.warning("[optimizer] Cycle already in progress — skipping concurrent call")
        return {'error': 'cycle already in progress'}
    try:
        return _run_full_recursive_optimizer_cycle_locked(min_improvement, max_changes_per_cycle)
    finally:
        _cycle_lock.release()


def _run_full_recursive_optimizer_cycle_locked(
    min_improvement: float = DEFAULT_MIN_IMPROVEMENT,
    max_changes_per_cycle: Optional[int] = None,
) -> Dict:
    logger.info("[optimizer] Starting full recursive optimizer cycle (automatic)...")
    log = _load_optimizer_log()
    cycle_num = (log[-1]['cycle'] + 1) if log else 1
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if log and log[-1].get('snapshot', {}).get('date') == today and not log[-1].get('safety_gate_blocked'):
        logger.warning(f"[optimizer] Cycle already ran today ({today}), skipping duplicate run")
        return log[-1]
    if not run_unit_safety_gate():
        logger.error("[optimizer] Safety gate failed — aborting optimizer cycle")
        return _log_and_email_safety_gate_blocked(cycle_num)
    # Validate live thresholds are within optimizer bounds before proceeding
    clamped = validate_live_thresholds_bounds()
    if clamped:
        logger.warning("[optimizer] Clamped %d out-of-bounds threshold(s) before cycle: %s",
                        len(clamped), [c['parameter'] for c in clamped])
    try:
        data = aggregate()
    except Exception as e:
        logger.error(f"[optimizer] Aggregate failed: {e}")
        return {'error': str(e)}

    try:
        cf_results = counterfactual_analysis(data)
    except Exception as e:
        logger.error(f"[optimizer] Counterfactual failed: {e}")
        cf_results = []

    regime_expectancy = data.get('regime_expectancy', {})
    paper_regime_expectancy = data.get('paper_regime_expectancy', {})
    to_apply, pending_display = _filter_full_auto(
        cf_results, min_improvement, max_changes_per_cycle,
        regime_expectancy=regime_expectancy,
        paper_regime_expectancy=paper_regime_expectancy,
    )

    frozen, closed_trades, trade_target = is_data_collection_mode()
    if frozen:
        logger.info(
            "[optimizer] Data collection mode active (%d/%d closed trades) — "
            "analysis complete but %d threshold change(s) will NOT be applied",
            closed_trades, trade_target, len(to_apply),
        )
        # Move would-be changes to pending so the email shows what was considered
        pending_display = to_apply + pending_display
        to_apply = []
    else:
        logger.info(f"[optimizer] Full-auto: {len(to_apply)} to apply (min_improvement={min_improvement:.2%}), {len(pending_display)} below bar or would filter winners")

    return _run_cycle_impl(data, cf_results, to_apply, pending_display, cycle_num)


def resend_last_optimizer_report(for_today_only: bool = True, fresh: bool = True) -> bool:
    """Resend the recursive optimizer email for the latest cycle (or latest for today).
    fresh=True (default) re-aggregates live data to build the snapshot; fresh=False uses
    the stored log entry as-is. Returns True if sent, False if no log entry or send failed."""
    log = _load_optimizer_log()
    if not log:
        logger.warning("[optimizer] No optimizer log entries; nothing to resend.")
        return False
    if for_today_only:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        candidates = [e for e in log if e.get('snapshot', {}).get('date') == today]
        if not candidates:
            logger.warning("[optimizer] No cycle for today (%s); nothing to resend.", today)
            return False
        entry = candidates[-1]
    else:
        entry = log[-1]

    if entry.get('safety_gate_blocked'):
        stored_snapshot = entry.get('snapshot', {})
        cn = stored_snapshot.get('cycle') or entry.get('cycle')
        try:
            cn = int(cn) if cn is not None else 1
        except (TypeError, ValueError):
            cn = 1
        date_str = stored_snapshot.get('date') or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        cycle_data = _build_safety_gate_cycle_data(
            cn,
            entry.get('timestamp') or datetime.now(timezone.utc).isoformat(),
            date_str,
        )
        try:
            body = format_report_html(cycle_data)
            from alert_system import AlertSystem
            AlertSystem().send_optimizer_report(
                f"Recursive Optimizer — Cycle #{cn} — {date_str} (resend)",
                body,
            )
            logger.info("[optimizer] Resent gate-blocked report for cycle #%s (%s)", cn, date_str)
            return True
        except Exception as e:
            logger.error("[optimizer] Resend email failed: %s", e)
            return False

    stored_snapshot = entry.get('snapshot', {})
    cycle_num = stored_snapshot.get('cycle') or entry.get('cycle')
    date_str = stored_snapshot.get('date', '')

    data_quality = None
    data = None
    if fresh:
        try:
            data = aggregate()
            snapshot = {
                'cycle': cycle_num,
                'date': date_str,
                'regime': data['current_regime'],
                'system_closed': data.get('system_closed', 0),
                'system_open': data.get('system_open', 0),
                'total_trades': data.get('system_closed', 0) + data.get('system_open', 0),
                'win_rate_7d': data['win_rate_7d'],
                'win_rate_30d': data['win_rate_30d'],
                'booked_trades_7d': len(data.get('trades_7d', [])),
                'booked_trades_30d': len(data.get('trades_30d', [])),
                'signals_screened_30d': data.get('signals_screened_30d', 0),
                'manual_override_count': data.get('manual_override_count', 0),
            }
            path_perf = _build_path_performance_system(data.get('system_trades_closed', []))
            missed_summary = data.get('missed_summary', {})
            learning_report = data.get('learning_report', {})
            _all_trades = data['trades']
            data_quality = {
                'system_trades_count': len(_all_trades),
                'has_confirmation_score': sum(1 for t in _all_trades if t.get('confirmation_score') is not None),
                'has_fear_greed': sum(1 for t in _all_trades if t.get('fear_greed') is not None),
                'has_funding_rate': sum(1 for t in _all_trades if t.get('funding_rate') is not None),
                'has_price_change_24h': sum(1 for t in _all_trades if t.get('price_change_24h') is not None),
                'min_required': _get_optimizer_threshold_int('MIN_TRADES_FOR_REC', MIN_TRADES_FOR_REC),
                'params_analyzed': 0,
            }
        except Exception as e:
            logger.warning("[optimizer] Fresh aggregate failed, falling back to stored: %s", e)
            snapshot = stored_snapshot
            path_perf = []
            missed_summary = {}
            learning_report = {}
    else:
        snapshot = stored_snapshot
        path_perf = []
        missed_summary = {}
        learning_report = {}

    # Log-stored pending_review may lack current_value; fill from live thresholds for better report display
    pending_review = list(entry.get('pending_review', []))
    if pending_review:
        try:
            from thresholds import get_threshold
            for r in pending_review:
                if r.get('current_value') is None and r.get('parameter'):
                    r['current_value'] = get_threshold(r['parameter'])
        except Exception:
            pass

    loss_based_for_resend = data.get('loss_based_recommendations', []) if data else entry.get('loss_based_recommendations', [])
    # Filter so deferred/applied and already-satisfied do not reappear on resend
    loss_based_filtered, pending_filtered = _filter_recommendations_for_display(
        loss_based_for_resend, pending_review, cycle_num,
    )

    cycle_data = {
        'timestamp': entry.get('timestamp', ''),
        'cycle': cycle_num,
        'snapshot': snapshot,
        'auto_applied': entry.get('auto_applied', []),
        'pending_review': pending_filtered,
        'evaluations': entry.get('evaluations', []),
        'path_performance': path_perf,
        'missed_summary': missed_summary,
        'learning_report': learning_report,
        'data_quality': data_quality,
        'open_positions': data.get('open_positions', []) if data else [],
        'closed_positions': data.get('closed_positions', []) if data else [],
        'system_trades_closed': data.get('system_trades_closed', []) if data else [],
        'manual_override_positions': data.get('manual_override_positions', []) if data else [],
        'perf_stats': data.get('perf_stats', {}) if data else {},
        'screening_summary': data.get('screening_summary', {}) if data else {},
        'loss_analysis': data.get('loss_analysis', {}) if data else {},
        'loss_based_recommendations': loss_based_filtered,
        'top_missed_by_regret': data.get('top_missed_by_regret', []) if data else [],
        'paper_evidence_report': (data.get('paper_evidence_report') if data else None) or _load_paper_evidence_report(),
        'promotion_recommendations': (
            ((data.get('paper_evidence_report') if data else None) or _load_paper_evidence_report())
            .get('paper_attribution') or {}
        ).get('promotion_recommendations', []),
    }
    try:
        html = format_report_html(cycle_data)
        from alert_system import AlertSystem
        alerts = AlertSystem()
        subject = f"Recursive Optimizer — Cycle #{cycle_num} — {date_str} (resend)"
        alerts.send_optimizer_report(subject, html)
        logger.info("[optimizer] Resent report for cycle #%s (%s)", cycle_num, date_str)
        return True
    except Exception as e:
        logger.error("[optimizer] Resend email failed: %s", e)
        return False


def get_last_report_data(fresh: bool = True) -> Dict[str, Any]:
    """Build cycle_data for the latest optimizer run (same structure as the email).
    Used by the dashboard Performance tab. Report structure is mirrored on the Performance tab; keep in sync.
    fresh=True: re-aggregate for path_performance, loss_analysis, screening_summary, etc.
    Returns dict with snapshot, auto_applied, pending_review, evaluations, path_performance, etc.;
    or {'error': '...'} if no log entry."""
    log = _load_optimizer_log()
    if not log:
        return {'error': 'No optimizer cycle run yet'}
    entry = log[-1]
    if entry.get('safety_gate_blocked'):
        stored_snapshot = entry.get('snapshot', {})
        cn = stored_snapshot.get('cycle') or entry.get('cycle')
        try:
            cn = int(cn) if cn is not None else 1
        except (TypeError, ValueError):
            cn = 1
        date_str = stored_snapshot.get('date') or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        cycle_data = _build_safety_gate_cycle_data(
            cn,
            entry.get('timestamp') or datetime.now(timezone.utc).isoformat(),
            date_str,
        )
        regression_model_status = None
        try:
            from regression_scorer import get_regression_meta, MIN_TRADES_FOR_REGRESSION
            meta = get_regression_meta()
            if meta is not None:
                n_samples = meta.get('n_samples', 0)
                regression_model_status = {
                    'trained_at': meta.get('trained_at'),
                    'n_samples': n_samples,
                    'min_required': MIN_TRADES_FOR_REGRESSION,
                    'active': n_samples >= MIN_TRADES_FOR_REGRESSION,
                }
        except Exception:
            pass
        cycle_data['regression_model_status'] = regression_model_status
        from datetime import datetime as _dt, timezone as _tz
        if _dt.now(_tz.utc).weekday() == 4:
            weekly_recs, weekly_conflicts = _build_weekly_consolidated_recommendations(log)
            cycle_data['weekly_consolidated'] = weekly_recs
            cycle_data['weekly_conflicts'] = weekly_conflicts
        else:
            cycle_data['weekly_consolidated'] = []
            cycle_data['weekly_conflicts'] = []
        return cycle_data

    stored_snapshot = entry.get('snapshot', {})
    cycle_num = stored_snapshot.get('cycle') or entry.get('cycle')
    date_str = stored_snapshot.get('date', '')

    data_quality = None
    data = None
    if fresh:
        try:
            data = aggregate()
            snapshot = {
                'cycle': cycle_num,
                'date': date_str,
                'regime': data['current_regime'],
                'system_closed': data.get('system_closed', 0),
                'system_open': data.get('system_open', 0),
                'total_trades': data.get('system_closed', 0) + data.get('system_open', 0),
                'win_rate_7d': data['win_rate_7d'],
                'win_rate_30d': data['win_rate_30d'],
                'booked_trades_7d': len(data.get('trades_7d', [])),
                'booked_trades_30d': len(data.get('trades_30d', [])),
                'signals_screened_30d': data.get('signals_screened_30d', 0),
                'manual_override_count': data.get('manual_override_count', 0),
            }
            path_perf = _build_path_performance_system(data.get('system_trades_closed', []))
            missed_summary = data.get('missed_summary', {})
            learning_report = data.get('learning_report', {})
            _all_trades = data['trades']
            data_quality = {
                'system_trades_count': len(_all_trades),
                'has_confirmation_score': sum(1 for t in _all_trades if t.get('confirmation_score') is not None),
                'has_fear_greed': sum(1 for t in _all_trades if t.get('fear_greed') is not None),
                'has_funding_rate': sum(1 for t in _all_trades if t.get('funding_rate') is not None),
                'has_price_change_24h': sum(1 for t in _all_trades if t.get('price_change_24h') is not None),
                'min_required': _get_optimizer_threshold_int('MIN_TRADES_FOR_REC', MIN_TRADES_FOR_REC),
                'params_analyzed': 0,
            }
        except Exception as e:
            logger.warning("[optimizer] get_last_report_data aggregate failed: %s", e)
            snapshot = stored_snapshot
            path_perf = []
            missed_summary = {}
            learning_report = {}
    else:
        snapshot = stored_snapshot
        path_perf = []
        missed_summary = {}
        learning_report = {}

    pending_review = list(entry.get('pending_review', []))
    if pending_review:
        try:
            from thresholds import get_threshold
            for r in pending_review:
                if r.get('current_value') is None and r.get('parameter'):
                    r['current_value'] = get_threshold(r['parameter'])
        except Exception:
            pass

    loss_based_for_display = data.get('loss_based_recommendations', []) if data else entry.get('loss_based_recommendations', [])
    loss_based_filtered, pending_filtered = _filter_recommendations_for_display(
        loss_based_for_display, pending_review, cycle_num,
    )
    for r in pending_filtered:
        r['rec_id'] = _rec_id(cycle_num, r.get('parameter', ''), r.get('suggested_value'))
    for r in loss_based_filtered:
        r['rec_id'] = _rec_id(cycle_num, r.get('parameter', ''), r.get('suggested_value'))

    # Regression scorer status for Performance tab (v1.0)
    regression_model_status = None
    try:
        from regression_scorer import get_regression_meta, MIN_TRADES_FOR_REGRESSION
        meta = get_regression_meta()
        if meta is not None:
            n_samples = meta.get('n_samples', 0)
            regression_model_status = {
                'trained_at': meta.get('trained_at'),
                'n_samples': n_samples,
                'min_required': MIN_TRADES_FOR_REGRESSION,
                'active': n_samples >= MIN_TRADES_FOR_REGRESSION,
            }
    except Exception:
        pass

    cycle_data = {
        'timestamp': entry.get('timestamp', ''),
        'cycle': cycle_num,
        'snapshot': snapshot,
        'regression_model_status': regression_model_status,
        'auto_applied': entry.get('auto_applied', []),
        'pending_review': pending_filtered,
        'evaluations': entry.get('evaluations', []),
        'path_performance': path_perf,
        'missed_summary': missed_summary,
        'learning_report': learning_report,
        'data_quality': data_quality,
        'open_positions': data.get('open_positions', []) if data else [],
        'closed_positions': data.get('closed_positions', []) if data else [],
        'system_trades_closed': data.get('system_trades_closed', []) if data else [],
        'manual_override_positions': data.get('manual_override_positions', []) if data else [],
        'perf_stats': data.get('perf_stats', {}) if data else {},
        'screening_summary': data.get('screening_summary', {}) if data else {},
        'loss_analysis': data.get('loss_analysis', {}) if data else {},
        'loss_based_recommendations': loss_based_filtered,
        'top_missed_by_regret': data.get('top_missed_by_regret', []) if data else [],
        'paper_evidence_report': (data.get('paper_evidence_report') if data else None) or _load_paper_evidence_report(),
        'promotion_recommendations': (
            ((data.get('paper_evidence_report') if data else None) or _load_paper_evidence_report())
            .get('paper_attribution') or {}
        ).get('promotion_recommendations', []),
    }
    # Friday: include weekly consolidated for the tab
    from datetime import datetime as _dt, timezone as _tz
    if _dt.now(_tz.utc).weekday() == 4:
        weekly_recs, weekly_conflicts = _build_weekly_consolidated_recommendations(log)
        cycle_data['weekly_consolidated'] = weekly_recs
        cycle_data['weekly_conflicts'] = weekly_conflicts
    else:
        cycle_data['weekly_consolidated'] = []
        cycle_data['weekly_conflicts'] = []
    return cycle_data


def _find_rec_by_id(rec_id: str) -> Optional[Dict]:
    """Resolve rec_id to (parameter, suggested_value, reason) by scanning optimizer log."""
    log = _load_optimizer_log()
    for entry in reversed(log):
        cycle = entry.get('snapshot', {}).get('cycle') or entry.get('cycle')
        if cycle is None:
            continue
        for r in entry.get('loss_based_recommendations', []) + entry.get('pending_review', []):
            param = r.get('parameter')
            suggested = r.get('suggested_value')
            if _rec_id(cycle, param, suggested) == rec_id:
                return {'parameter': param, 'suggested_value': suggested, 'reason': r.get('reason', '')}
    return None


def defer_recommendation(rec_id: str) -> bool:
    """Mark a recommendation as deferred so it stops appearing in future emails.
    Call with the id shown in the email (e.g. rec-7-BEAR_REGIME_MIN_SCORE-75). Returns True if found and recorded."""
    rec = _find_rec_by_id(rec_id)
    if not rec:
        logger.warning("[optimizer] defer_recommendation: no recommendation found for id %s", rec_id)
        return False
    data = _load_recommendations_log()
    recs = data.get('recommendations', [])
    now = datetime.now(timezone.utc).isoformat()
    recs.append({
        'id': rec_id,
        'parameter': rec['parameter'],
        'suggested_value': rec['suggested_value'],
        'reason': rec.get('reason', ''),
        'status': 'deferred',
        'resolved_at': now,
    })
    data['recommendations'] = recs
    _save_recommendations_log(data)
    logger.info("[optimizer] Deferred recommendation %s (%s)", rec_id, rec['parameter'])
    return True


def apply_recommendation(rec_id: str) -> bool:
    """Apply a numeric recommendation by id (writes to live_thresholds) and mark as applied so it stops appearing.
    For qualitative recs only marks as applied; implement in code yourself.
    Returns True if found and applied or marked."""
    rec = _find_rec_by_id(rec_id)
    if not rec:
        logger.warning("[optimizer] apply_recommendation: no recommendation found for id %s", rec_id)
        return False
    param = rec['parameter']
    suggested = rec['suggested_value']
    now = datetime.now(timezone.utc).isoformat()
    if isinstance(suggested, (int, float)):
        apply_single(param, suggested)
    data = _load_recommendations_log()
    recs = data.get('recommendations', [])
    recs.append({
        'id': rec_id,
        'parameter': param,
        'suggested_value': suggested,
        'reason': rec.get('reason', ''),
        'status': 'applied',
        'resolved_at': now,
    })
    data['recommendations'] = recs
    _save_recommendations_log(data)
    logger.info("[optimizer] Applied recommendation %s (%s)", rec_id, param)
    return True


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    result = run_optimization_cycle()
    applied = result.get('auto_applied', [])
    pending = result.get('pending_review', [])
    evals = result.get('evaluations', [])
    print(f"\nCycle #{result.get('cycle', '?')} complete:")
    print(f"  Auto-applied: {len(applied)}")
    print(f"  Pending review: {len(pending)}")
    print(f"  Changes tracking: {len(evals)}")
