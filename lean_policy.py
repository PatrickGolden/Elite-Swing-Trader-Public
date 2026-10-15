# =============================================================================
# lean_policy.py — single source of truth for Stage 1c LEAN mode rules.
#
# LEAN mode (LEAN_ENTRY_MODE >= 1) restricts new entries to the momentum/trend
# family while BTC is risk-on, and applies a wide fixed disaster stop with no
# take-profit. Three layers enforce it:
#   - signal layer  (quant_signal.py)   prunes non-compliant BUYs to HOLD
#   - exit layer    (dynamic_targets.py) applies the wide fixed stop / no-TP
#   - fill layer    (this module, via trade_journal.open_position) is the
#                    FAIL-CLOSED guard: no automated path may journal a position
#                    that violates lean policy when lean is active, even if an
#                    upstream signal was stale or bypassed (defense in depth).
#
# All lean rules and constants live here so the layers cannot drift apart.
# =============================================================================

import json
import os
import time
from typing import Any, Dict, Optional, Tuple

from thresholds import get_threshold

# Momentum/trend-family entry paths kept under LEAN_ENTRY_MODE (Stage 1c prune).
# Everything else (mean-reversion, contrarian/fear, the catch-all default/
# momentum_context) is suppressed: the multi-year walk-forward showed those are
# net-negative, and the book only turns positive pruned to this family in risk-on.
# quant_signal.py imports this set so the signal gate and the fill guard agree.
LEAN_ALLOWED_PATHS = frozenset({
    'rally', 'pullback_in_trend', 'momentum_breakout', 'early_momentum', 'bull_participant',
})

# Stop-profile tolerance: a compliant lean entry carries the fixed disaster stop
# (LEAN_FIXED_STOP_PCT); allow a small band for rounding/float drift.
LEAN_STOP_TOLERANCE_PCT = 1.0

_LEAN_AUDIT_PATH = os.path.join(os.path.dirname(__file__), 'data', 'lean_audit.jsonl')


def is_lean_active() -> bool:
    """True when LEAN_ENTRY_MODE >= 1 in live thresholds."""
    return float(get_threshold('LEAN_ENTRY_MODE', 0.0) or 0.0) >= 1.0


def lean_fixed_stop_pct() -> float:
    """Wide fixed disaster stop applied to every lean entry."""
    return float(get_threshold('LEAN_FIXED_STOP_PCT', 24.0))


def entry_min_mom30() -> float:
    """30d-momentum entry floor for lean entries."""
    return float(get_threshold('LEAN_ENTRY_MIN_MOM30', 15.0))


def exit_profile() -> Dict[str, Any]:
    """Lean exit mechanics: wide fixed stop, effectively no TP, no trailing."""
    return {
        'stop_pct': lean_fixed_stop_pct(),
        'tp_pct': float(get_threshold('LEAN_TAKE_PROFIT_PCT', 150.0)),
        'trailing_off': True,
    }


def entry_allowed(quant_path: Optional[str], risk_on: Optional[bool],
                  ch30d: Optional[float]) -> Tuple[bool, Optional[str]]:
    """Re-derive lean entry eligibility from primitives (mirrors the quant prune).

    Returns (True, None) if the entry is lean-compliant, else (False, reason).
    Fail-closed: missing/None inputs are treated as non-compliant.
    """
    path = (quant_path or '').strip().lower()
    if path not in LEAN_ALLOWED_PATHS:
        return False, f"path '{path or 'none'}' not in lean-allowed set"
    if risk_on is not True:
        return False, f"not risk-on (risk_on={risk_on})"
    if ch30d is None:
        return False, "missing 30d momentum"
    if float(ch30d) < entry_min_mom30():
        return False, f"30d momentum {float(ch30d):.1f} < {entry_min_mom30():.1f}"
    return True, None


def _risk_on_from_payload(payload: Dict[str, Any]) -> Optional[bool]:
    """Map the journal's risk_regime string back to the risk_on tri-state."""
    rr = (payload.get('risk_regime') or '').strip().upper()
    if rr == 'RISK_ON':
        return True
    if rr == 'RISK_OFF':
        return False
    return None


def assert_exec_compliant(context: Dict[str, Any]) -> Optional[str]:
    """Fail-closed pre-execution entry-eligibility check. Returns a reason string
    if the entry violates lean entry policy, or None if compliant.

    Re-derives from primitives (quant_path, risk_regime, price_change_30d) so it
    blocks exactly the bypassed entries and passes legitimate ones. This is the
    entry-eligibility core shared by the execution-boundary gate (before any order
    is placed) and the journal fill guard — they cannot drift. No stop check here:
    the stop profile is applied structurally by the lean exit override at fill time.
    """
    ok, reason = entry_allowed(
        context.get('quant_path'),
        _risk_on_from_payload(context),
        context.get('price_change_30d'),
    )
    return reason if not ok else None


def assert_fill_compliant(payload: Dict[str, Any]) -> Optional[str]:
    """Fail-closed fill-time check. Returns a reason string if the entry violates
    lean policy, or None if it is compliant. Delegates entry eligibility to
    assert_exec_compliant (shared core) and adds the stop-profile check.
    """
    reason = assert_exec_compliant(payload)
    if reason:
        return reason
    stop_pct = float(payload.get('ai_stop_pct', 0) or 0)
    expected = lean_fixed_stop_pct()
    if abs(stop_pct - expected) > LEAN_STOP_TOLERANCE_PCT:
        return f"stop {stop_pct:.1f}% != lean profile {expected:.1f}% (±{LEAN_STOP_TOLERANCE_PCT:.0f}pp)"
    return None


def log_lean_event(event_type: str, payload: Dict[str, Any],
                   reason: Optional[str]) -> None:
    """Append a lean audit event (best-effort, append-only). Never raises."""
    try:
        record = {
            'ts': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
            'event': event_type,
            'symbol': (payload.get('coin_symbol') or '').strip().upper(),
            'quant_path': payload.get('quant_path'),
            'risk_regime': payload.get('risk_regime'),
            'ch30d': payload.get('price_change_30d'),
            'stop_pct': payload.get('ai_stop_pct'),
            'is_test': bool(payload.get('is_test', False)),
            'source': payload.get('source'),
            'reason': reason,
        }
        os.makedirs(os.path.dirname(_LEAN_AUDIT_PATH), exist_ok=True)
        with open(_LEAN_AUDIT_PATH, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record) + '\n')
    except Exception:
        pass
