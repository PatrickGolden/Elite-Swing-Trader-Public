# =============================================================================
# trade_journal.py — Swing Trader Dashboard (Position-Based Model)
# Option B+ Hybrid: Positions with locked AI recommendations + adjustable targets
#
# Storage: C:\swing-trader\positions.json
# Each position: entry → (track AI target vs actual) → exit
# =============================================================================

import json
import csv
import io
import logging
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


POSITIONS_FILE = os.path.join(os.path.dirname(__file__), 'positions.json')
# Monotonic display trade_id streams (TRD / PAP / TST) — not derived from position id
JOURNAL_SEQUENCES_FILE = os.path.join(os.path.dirname(__file__), 'data', 'journal_sequences.json')
_positions_lock = threading.Lock()


# =============================================================================
# File helpers — uses safe_json for atomic writes to prevent corruption
# =============================================================================

def _load_positions():
    """Load all positions from JSON file (thread-safe, atomic). Returns empty list if file missing."""
    import safe_json
    data = safe_json.load(POSITIONS_FILE, default=list)
    return data if isinstance(data, list) else []


def _save_positions(positions):
    """Persist positions list to JSON file (atomic write via safe_json)."""
    import safe_json
    if not safe_json.save(POSITIONS_FILE, positions, default=str):
        logger.error("[trade_journal] CRITICAL: Failed to save positions.json")


def _next_id(positions):
    """Generate next sequential position ID."""
    if not positions:
        return 1
    return max(p.get('id', 0) for p in positions) + 1


def _trade_stream_kind_from_dict(d: dict) -> str:
    """
    Classify which display trade_id stream a row belongs to.
    Live = same rule as dashboard isLiveJournalPosition: not is_test and not paper_trade.
    """
    if (d.get('source') or '').strip() == 'paper_trade':
        return 'paper'
    if bool(d.get('is_test', False)):
        return 'test'
    return 'live'


def _suffix_if_prefix(tid: str, prefix: str):
    if not tid or not isinstance(tid, str) or not tid.startswith(prefix):
        return None
    rest = tid[len(prefix):]
    return int(rest) if rest.isdigit() else None


def _min_next_sequences_from_positions(positions):
    """
    Derive minimum safe next sequence numbers from existing trade_ids (max suffix + 1 per stream).
    Legacy rows used TRD-* for all streams; include TRD suffix when seeding paper/test streams.
    """
    live_max = paper_max = test_max = 0
    for p in positions:
        tid = (p.get('trade_id') or '').strip()
        kind = _trade_stream_kind_from_dict(p)
        if kind == 'live':
            n = _suffix_if_prefix(tid, 'TRD-')
            if n is not None:
                live_max = max(live_max, n)
        elif kind == 'paper':
            nums = []
            for pref in ('PAP-', 'TRD-'):
                x = _suffix_if_prefix(tid, pref)
                if x is not None:
                    nums.append(x)
            paper_max = max(paper_max, max(nums) if nums else 0)
        else:
            nums = []
            for pref in ('TST-', 'TRD-'):
                x = _suffix_if_prefix(tid, pref)
                if x is not None:
                    nums.append(x)
            test_max = max(test_max, max(nums) if nums else 0)
    return {
        'live_next': live_max + 1,
        'paper_next': paper_max + 1,
        'test_next': test_max + 1,
    }


def _merge_sequence_state(positions, stored):
    """Combine on-disk sequence file with derived mins from positions.json (never move backwards)."""
    computed = _min_next_sequences_from_positions(positions)
    stored = stored if isinstance(stored, dict) else {}
    out = {}
    for k in ('live_next', 'paper_next', 'test_next'):
        try:
            sv = int(stored.get(k) or 1)
        except (TypeError, ValueError):
            sv = 1
        out[k] = max(sv, computed[k])
    return out


def _load_sequences(positions):
    import safe_json
    raw = safe_json.load(JOURNAL_SEQUENCES_FILE, default=dict)
    if not isinstance(raw, dict):
        raw = {}
    return _merge_sequence_state(positions, raw)


def _save_sequences(seq: dict) -> None:
    import safe_json
    d = os.path.dirname(os.path.abspath(JOURNAL_SEQUENCES_FILE))
    os.makedirs(d, exist_ok=True)
    if not safe_json.save(JOURNAL_SEQUENCES_FILE, seq, default=str):
        logger.error('[trade_journal] CRITICAL: Failed to save journal_sequences.json')


def _allocate_trade_id(kind: str, seq: dict) -> str:
    key = {'live': 'live_next', 'paper': 'paper_next', 'test': 'test_next'}[kind]
    prefix = {'live': 'TRD-', 'paper': 'PAP-', 'test': 'TST-'}[kind]
    n = int(seq[key])
    seq[key] = n + 1
    return f'{prefix}{n:05d}'


def _ensure_trade_ids(positions):
    """Ensure every position has a trade_id; backfill from stream counters if missing. Returns True if any were added."""
    missing = [p for p in positions if not (p.get('trade_id') or '').strip()]
    if not missing:
        return False
    seq = _load_sequences(positions)
    modified = False
    for p in missing:
        kind = _trade_stream_kind_from_dict(p)
        p['trade_id'] = _allocate_trade_id(kind, seq)
        modified = True
    if modified:
        _save_sequences(seq)
    return modified


# =============================================================================
# Position lifecycle
# =============================================================================

def open_position(data):
    """
    Open a new position with entry + quant/signal recommendations locked in.

    Required fields:
        coin_id, coin_name, coin_symbol, signal,
        entry_price, quantity

    Quant/signal fields (if available):
        ai_target_pct, ai_stop_pct, ai_hypothesis, ai_timeframe

    Optional:
        notes, capital_usd
    """
    with _positions_lock:
        positions = _load_positions()
        new_symbol = (data.get('coin_symbol') or '').strip().upper()
        new_is_test = bool(data.get('is_test', False))
        # Paper cohorts are scoped by source (e.g. 'paper_trade' vs 'paper_rotation'):
        # one open position per symbol per cohort, so benchmark cohorts (STRATEGY.md
        # candidate #1) can hold BTC concurrently with the pipeline's paper cohort
        # without blocking each other's entries. Real positions ignore source —
        # single real position per symbol, full stop.
        new_source = (data.get('source') or '').strip()
        if new_symbol:
            existing_open = [
                p for p in positions
                if p.get('status') == 'OPEN'
                and (p.get('coin_symbol') or '').strip().upper() == new_symbol
                and bool(p.get('is_test', False)) == new_is_test
                and (not new_is_test or (p.get('source') or '').strip() == new_source)
            ]
            if existing_open:
                kind = "paper" if new_is_test else "real"
                logger.warning(f"Rejecting duplicate {kind} open position for {new_symbol} — already open (id={existing_open[0].get('id')}). Single position per symbol per mode.")
                return None

        # LEAN fail-closed guard (Stage 1c production integrity): when LEAN_ENTRY_MODE
        # is active, no automated path may journal a position that violates lean entry/
        # exit policy — even if an upstream signal was stale or bypassed. Manual/import
        # routes pass lean_override=True for emergency use. A compliant lean BUY survived
        # the quant prune (risk_on=True, allowed path), so this only blocks leaked entries.
        if not data.get('lean_override'):
            import lean_policy
            if lean_policy.is_lean_active():
                reason = lean_policy.assert_fill_compliant(data)
                if reason:
                    lean_policy.log_lean_event('fill_blocked', data, reason)
                    logger.warning(f"LEAN fill blocked for {new_symbol or '?'}: {reason}")
                    return None
                lean_policy.log_lean_event('fill_allowed', data, None)

        entry_price = float(data.get('entry_price', 0))
        quantity    = float(data.get('quantity', 0))
        # Quant/signal recommendations (locked - never change)
        ai_target_pct = float(data.get('ai_target_pct', 6.0))
        ai_stop_pct   = float(data.get('ai_stop_pct', 4.0))
        # Winners always run (Phase 1, 2026-05-30): NO fixed take-profit, ever. Every position
        # exits via stop-loss or the trailing stop only — the fixed TP cap clipped the fat tail
        # that funds a low-win-rate book. ai_target_pct is retained purely as a recommendation/
        # tracking figure (rr_ratio, hypothesis); it is never an actionable sell target.
        ai_target_price = None
        current_target_pct = None
        current_target_price = None
        ai_stop_price = round(entry_price * (1 - ai_stop_pct / 100), 8)
        current_stop_pct = ai_stop_pct
        current_stop_price = ai_stop_price
        capital = float(data.get('capital_usd', 0) or entry_price * quantity)
        entry_fee_usd = float(data.get('entry_fee_usd', 0) or 0)
        rr_ratio = round(ai_target_pct / ai_stop_pct, 2) if ai_stop_pct > 0 else 0
        next_pid = _next_id(positions)
        seq = _load_sequences(positions)
        kind = _trade_stream_kind_from_dict(data)
        trade_id = _allocate_trade_id(kind, seq)
        _save_sequences(seq)
        # Tag lean-policy compliance at open (Stage 1c validation cohort): re-derived
        # from primitives regardless of lean mode, so lean-compliant paper trades can be
        # measured forward as their own cohort. Fail-closed: missing primitives → False.
        try:
            import lean_policy
            lean_compliant = lean_policy.assert_exec_compliant(data) is None
        except Exception:
            lean_compliant = False
        position = {
        'id':              next_pid,
        'trade_id':        trade_id,
        'status':          'OPEN',
        'coin_id':         data.get('coin_id', ''),
        'coin_name':       data.get('coin_name', ''),
        'coin_symbol':     (data.get('coin_symbol') or '').upper(),
        'signal':          data.get('signal', 'BUY'),
        
        # Entry
        'entry_price':     entry_price,
        'entry_time':      datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'quantity':        quantity,
        'capital_usd':     round(capital, 2),
        'entry_fee_usd':   round(entry_fee_usd, 6),

        # AI Recommendations (LOCKED - for tracking accuracy; target_price is always None — winners run)
        'ai_target_pct':   ai_target_pct,
        'ai_target_price': ai_target_price,
        'let_winners_run': True,  # permanent: every position runs (stop/trailing only). Kept as a record marker.
        'ai_stop_pct':     ai_stop_pct,
        'ai_stop_price':   ai_stop_price,
        'ai_hypothesis':   data.get('ai_hypothesis', ''),
        'ai_timeframe':    data.get('ai_timeframe', ''),
        'ai_confidence':   data.get('ai_confidence', ''),
        
        # Current Targets (can be adjusted by user)
        'current_target_pct':   current_target_pct,
        'current_target_price': current_target_price,
        'current_stop_pct':     current_stop_pct,
        'current_stop_price':   current_stop_price,
        
        'rr_ratio':        rr_ratio,
        'notes':           data.get('notes', ''),
        'risk_pct_used':   data.get('risk_pct_used'),  # Risk parameter % when trade was opened (for display)
        
        # Model attribution (for model_attribution.py; optional at open)
        'specialist_veto':  data.get('specialist_veto'),
        'quant_tier':      data.get('quant_tier'),
        'quant_path':      data.get('quant_path'),
        'trading_scenario': data.get('trading_scenario'),
        'confirmation_score': data.get('confirmation_score'),
        'market_regime':   data.get('market_regime'),
        # Stage 1b: BTC risk-on/off at entry (gate-integrity audit for paper→live promotion)
        'risk_regime':     data.get('risk_regime'),
        # Stage 1c: lean-policy compliance at entry (validation cohort filter)
        'lean_compliant':  lean_compliant,
        # Recursive optimizer attribution (optional; from cache at open)
        'fear_greed':      data.get('fear_greed'),
        'funding_rate':    data.get('funding_rate'),
        'price_change_24h': data.get('price_change_24h'),
        'price_change_7d':  data.get('price_change_7d'),
        'price_change_30d': data.get('price_change_30d'),
        'edge_score':     data.get('edge_score'),
        'ath_change_pct': data.get('ath_change_pct'),
        'fdv_ratio':      data.get('fdv_ratio'),
        # Regression scorer: entry-time features for weekly logistic regression (win/loss)
        'regression_features': data.get('regression_features'),
        # Signal badges (attribution: chase_guard_capped, bull_score_floor, light_sniper_entry, etc.)
        'signal_badges':   data.get('signal_badges') or [],
        # Stop-policy attribution (for dynamic stop rollout analysis)
        'stop_policy_profile': data.get('stop_policy_profile'),
        'stop_policy_version': data.get('stop_policy_version'),
        'dynamic_stop_eligible': data.get('dynamic_stop_eligible'),
        'dynamic_stop_floor_pct': data.get('dynamic_stop_floor_pct'),
        'dynamic_stop_floor_applied': data.get('dynamic_stop_floor_applied'),
        
        # Exit (filled when closed)
        'exit_price':      None,
        'exit_time':       None,
        'pnl_usd':         None,
        'pnl_pct':         None,
        'outcome':         None,    # 'win' | 'loss' | 'breakeven'
        'exit_reason':     None,    # 'target' | 'stop' | 'manual' | 'time'
        'hit_ai_target':   None,    # True/False - did we hit AI's target?
        'model_prediction_ts': None,  # For model_performance_tracker outcome matching
        # Coinbase limit order IDs (hybrid SL/TP + trailing stop)
        'sl_order_id':     None,
        'sl_stop_price':   None,   # Exchange SL trigger currently placed (for reprice drift checks)
        'tp_order_id':     None,
        # Test mode: when True, position appears on Test tab, not Positions tab
        'is_test':         bool(data.get('is_test', False)),
        # Optional: "paper_trade" for simulated pipeline trades (distinct from generic test)
        'source':          (data.get('source') or '').strip() or None,
        }

        # Model performance tracker: log prediction for outcome matching on close
        # Skip for test/paper positions to avoid contaminating live model calibration
        if not bool(data.get('is_test', False)):
            try:
                from model_performance_tracker import log_prediction as model_log_prediction
                conf_map = {'Very High': 90, 'High': 80, 'Medium': 70, 'Low': 50}
                conf = conf_map.get((data.get('ai_confidence') or '').strip(), 60)
                models_list = []
                if data.get('specialist_veto'):
                    models_list.append('Specialist')
                if data.get('quant_tier'):
                    models_list.append('Quant')
                ts = model_log_prediction(
                    coin_id=data.get('coin_id', ''),
                    symbol=(data.get('coin_symbol') or '').upper(),
                    signal=data.get('signal', 'BUY'),
                    confidence=conf,
                    regime=data.get('market_regime') or 'UNKNOWN',
                    models=models_list if models_list else ['Quant'],
                    veto_decision=data.get('specialist_veto'),
                )
                if ts:
                    position['model_prediction_ts'] = ts
            except Exception:
                pass
        positions.append(position)
        _save_positions(positions)
    logger.info(f"Position opened: #{position['id']} {position['coin_symbol']} @ ${entry_price} (AI target: ${ai_target_price})")
    return position


def close_position(position_id, exit_price, exit_reason='manual', notes='', exit_time=None, exit_fee_usd=None, quantity_at_close=None):
    """
    Close a position at given exit price.
    Calculates P&L and checks if signal target was hit.

    Optional (for exchange-sourced closes / correct optimizer data):
        exit_time: datetime or str 'YYYY-MM-DD HH:MM:SS' — when the exit actually occurred
        exit_fee_usd: fee paid on the exit (stored on position for net PnL)
        quantity_at_close: use this quantity for PnL when journal quantity is 0 (e.g. from exchange fill)
    """
    with _positions_lock:
        positions = _load_positions()
        _ensure_trade_ids(positions)
        position  = next((p for p in positions if p['id'] == position_id), None)

        if not position:
            return None, f'Position #{position_id} not found'
        if position['status'] == 'CLOSED':
            return None, f'Position #{position_id} is already closed'

        exit_price  = float(exit_price)
        entry_price = float(position['entry_price'])
        quantity    = float(position['quantity'] or 0)
        if quantity <= 0 and quantity_at_close is not None and float(quantity_at_close) > 0:
            quantity = float(quantity_at_close)
        elif quantity <= 0:
            quantity = 0.0

        if exit_price <= 0 or math.isnan(exit_price) or math.isinf(exit_price):
            return None, f'Invalid exit price: {exit_price}'
        if entry_price <= 0 or math.isnan(entry_price) or math.isinf(entry_price):
            return None, f'Invalid entry price: {entry_price}'

        gross_pnl_usd = round((exit_price - entry_price) * quantity, 6)
        gross_pnl_pct = round(((exit_price - entry_price) / entry_price) * 100, 6) if entry_price else 0

        if position['signal'] == 'SELL':
            gross_pnl_pct = -gross_pnl_pct
            gross_pnl_usd = -gross_pnl_usd

        # Net-of-fees P&L: subtract entry + exit fees
        entry_fee = float(position.get('entry_fee_usd', 0) or 0)
        exit_fee = float(exit_fee_usd) if exit_fee_usd is not None else 0.0
        total_fees = entry_fee + exit_fee
        pnl_usd = round(gross_pnl_usd - total_fees, 6)
        # Net pnl_pct: net dollar P&L as % of capital invested
        capital_basis = entry_price * quantity if (entry_price and quantity) else 0
        pnl_pct = round((pnl_usd / capital_basis) * 100, 6) if capital_basis > 0 else gross_pnl_pct

        if pnl_usd > 0.01:      outcome = 'win'
        elif pnl_usd < -0.01:   outcome = 'loss'
        else:                   outcome = 'breakeven'

        # Winners always run ⇒ ai_target_price is None (no actionable sell target). We still track
        # whether the exit reached the *recommended* ai_target_pct, purely as a recommendation-
        # accuracy metric (it never drives an exit). Derive a notional target from entry + pct.
        ai_target_price = position.get('ai_target_price')
        if ai_target_price is None:
            entry_for_target = float(position.get('entry_price') or 0)
            tgt_pct = position.get('ai_target_pct')
            if entry_for_target > 0 and tgt_pct is not None:
                pct = float(tgt_pct) / 100
                ai_target_price = entry_for_target * (1 + pct) if position['signal'] == 'BUY' else entry_for_target * (1 - pct)
        if ai_target_price is not None:
            ai_target_price = float(ai_target_price)
            hit_ai_target = exit_price >= ai_target_price if position['signal'] == 'BUY' else exit_price <= ai_target_price
        else:
            hit_ai_target = None

        exit_time_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        if exit_time is not None:
            if hasattr(exit_time, 'strftime'):
                exit_time_str = exit_time.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(exit_time, str) and exit_time.strip():
                exit_time_str = exit_time.strip()

        position.update({
            'status':        'CLOSED',
            'exit_price':    exit_price,
            'exit_time':     exit_time_str,
            'pnl_usd':       round(pnl_usd, 4),
            'pnl_pct':       round(pnl_pct, 4),
            'gross_pnl_usd': round(gross_pnl_usd, 4),
            'gross_pnl_pct': round(gross_pnl_pct, 4),
            'total_fees_usd': round(total_fees, 4),
            'outcome':       outcome,
            'exit_reason':   exit_reason,
            'hit_ai_target': hit_ai_target,
        })
        if exit_fee_usd is not None:
            position['exit_fee_usd'] = round(float(exit_fee_usd), 4)
        if notes:
            position['notes'] = (position.get('notes') or '') + f' | Exit: {notes}'

        _save_positions(positions)
    # Model performance tracker: record outcome for calibration
    # Uses record_trade_outcome which creates a synthetic entry if original prediction was rotated out.
    pred_ts = position.get('model_prediction_ts')
    if pred_ts and not position.get('is_test'):
        try:
            from model_performance_tracker import record_trade_outcome
            conf_map = {'Very High': 90, 'High': 80, 'Medium': 70, 'Low': 50}
            conf = conf_map.get((position.get('ai_confidence') or '').strip(), 60)
            record_trade_outcome(
                coin_id=position.get('coin_id', ''),
                symbol=(position.get('coin_symbol') or '').upper(),
                signal=position.get('signal', 'BUY'),
                confidence=conf,
                regime=position.get('market_regime') or 'UNKNOWN',
                pnl_pct=pnl_pct,
                pred_ts=pred_ts,
            )
        except Exception:
            pass
    # Win rates and model attribution are now derived from the journal (single source of truth).
    # No longer writing to model_attribution.json.
    logger.info(f"Position closed: #{position_id} {position['coin_symbol']} -> {outcome} {pnl_pct:+.2f}% | target hit: {hit_ai_target}")
    return position, None


def update_targets(position_id, new_target_pct=None, new_stop_pct=None):
    """Adjust current target/stop while keeping quant/signal recommendations locked."""
    with _positions_lock:
        positions = _load_positions()
        _ensure_trade_ids(positions)
        position  = next((p for p in positions if p['id'] == position_id), None)

        if not position:
            return None, f'Position #{position_id} not found'
        if position['status'] == 'CLOSED':
            return None, f'Cannot update closed position'

        entry_price = float(position['entry_price'])

        if new_target_pct is not None:
            position['current_target_pct']   = float(new_target_pct)
            position['current_target_price'] = round(entry_price * (1 + float(new_target_pct) / 100), 8)

        if new_stop_pct is not None:
            position['current_stop_pct']   = float(new_stop_pct)
            position['current_stop_price'] = round(entry_price * (1 - float(new_stop_pct) / 100), 8)

        _save_positions(positions)
    return position, None


def update_position(position_id, updates=None, notes=None, quantity=None, target_pct=None, stop_pct=None, entry_price_new=None, high_water_mark=None, last_refreshed=None):
    """
    Update editable fields of an OPEN position.
    Accepts: updates (dict), or individual kwargs: notes, quantity, target_pct, stop_pct, entry_price_new, high_water_mark, last_refreshed.
    Recomputes target/stop prices and capital_usd when entry or pcts change.
    """
    sl_order_id = None
    sl_stop_price = None
    tp_order_id = None
    if isinstance(updates, dict):
        notes = updates.get('notes') if notes is None else notes
        quantity = updates.get('quantity') if quantity is None else quantity
        target_pct = updates.get('target_pct') if target_pct is None else target_pct
        stop_pct = updates.get('stop_pct') if stop_pct is None else stop_pct
        # Schema-aligned key: accept current_stop_pct from callers (e.g. exit_manager breakeven)
        if stop_pct is None and updates.get('current_stop_pct') is not None:
            stop_pct = updates['current_stop_pct']
        entry_price_new = updates.get('entry_price') if entry_price_new is None else entry_price_new
        high_water_mark = updates.get('high_water_mark') if high_water_mark is None else high_water_mark
        last_refreshed = updates.get('last_refreshed') if last_refreshed is None else last_refreshed
        sl_order_id = updates.get('sl_order_id')
        sl_stop_price = updates.get('sl_stop_price')
        tp_order_id = updates.get('tp_order_id')

    with _positions_lock:
        positions = _load_positions()
        _ensure_trade_ids(positions)
        position = next((p for p in positions if p['id'] == position_id), None)

        if not position:
            return None, f'Position #{position_id} not found'
        if position['status'] == 'CLOSED':
            return None, f'Cannot update closed position'

        entry_price = float(position['entry_price'])

        if notes is not None:
            position['notes'] = str(notes)

        if entry_price_new is not None:
            ep = float(entry_price_new)
            if ep <= 0:
                return None, 'Entry price must be positive'
            position['entry_price'] = ep
            entry_price = ep
            position['capital_usd'] = round(entry_price * float(position['quantity']), 2)
            # Winners run: ai_target_pct is kept for tracking but ai_target_price/current_target stay
            # None (no fixed target). Only recompute target prices for legacy positions that have a pct.
            position['ai_stop_price'] = round(entry_price * (1 - float(position['ai_stop_pct']) / 100), 8)
            position['current_stop_price'] = round(entry_price * (1 - float(position['current_stop_pct']) / 100), 8)
            if position.get('current_target_pct') is not None:
                position['current_target_price'] = round(entry_price * (1 + float(position['current_target_pct']) / 100), 8)
                position['ai_target_price'] = round(entry_price * (1 + float(position.get('ai_target_pct') or position['current_target_pct']) / 100), 8)

        if quantity is not None:
            qty = float(quantity)
            if qty < 0:
                return None, 'Quantity cannot be negative'
            position['quantity'] = qty
            position['capital_usd'] = round(float(position['entry_price']) * qty, 2) if qty > 0 else 0

        if target_pct is not None:
            entry_price = float(position['entry_price'])
            position['current_target_pct'] = float(target_pct)
            position['current_target_price'] = round(entry_price * (1 + float(target_pct) / 100), 8)

        if stop_pct is not None:
            entry_price = float(position['entry_price'])
            position['current_stop_pct'] = float(stop_pct)
            position['current_stop_price'] = round(entry_price * (1 - float(stop_pct) / 100), 8)

        if high_water_mark is not None:
            position['high_water_mark'] = float(high_water_mark)

        if last_refreshed is not None:
            position['last_refreshed'] = str(last_refreshed)

        # sl_order_id / tp_order_id: apply when in updates dict (allows setting or clearing to None)
        if isinstance(updates, dict):
            if 'sl_order_id' in updates:
                position['sl_order_id'] = str(updates['sl_order_id']) if updates['sl_order_id'] is not None else None
            if 'sl_stop_price' in updates:
                position['sl_stop_price'] = float(updates['sl_stop_price']) if updates['sl_stop_price'] is not None else None
            if 'tp_order_id' in updates:
                position['tp_order_id'] = str(updates['tp_order_id']) if updates['tp_order_id'] is not None else None
            if 'sl_retry_count' in updates:
                position['sl_retry_count'] = int(updates['sl_retry_count'] or 0)
            if 'sl_last_error' in updates:
                position['sl_last_error'] = str(updates['sl_last_error']) if updates['sl_last_error'] is not None else None
            if 'sl_retry_next_at' in updates:
                position['sl_retry_next_at'] = str(updates['sl_retry_next_at']) if updates['sl_retry_next_at'] is not None else None
            if 'sl_retry_reason' in updates:
                position['sl_retry_reason'] = str(updates['sl_retry_reason']) if updates['sl_retry_reason'] is not None else None
            if 'sl_retry_context' in updates:
                position['sl_retry_context'] = str(updates['sl_retry_context']) if updates['sl_retry_context'] is not None else None
            if 'sl_retry_after_sec' in updates:
                position['sl_retry_after_sec'] = int(updates['sl_retry_after_sec']) if updates['sl_retry_after_sec'] is not None else None
            if 'sl_retry_exhausted_alerted' in updates:
                position['sl_retry_exhausted_alerted'] = bool(updates['sl_retry_exhausted_alerted'])
        elif sl_order_id is not None or sl_stop_price is not None or tp_order_id is not None:
            if sl_order_id is not None:
                position['sl_order_id'] = str(sl_order_id)
            if sl_stop_price is not None:
                position['sl_stop_price'] = float(sl_stop_price)
            if tp_order_id is not None:
                position['tp_order_id'] = str(tp_order_id)

        _save_positions(positions)
    return position, None


def delete_position(position_id):
    """Delete a position (use only for data-entry mistakes)."""
    with _positions_lock:
        positions = _load_positions()
        _ensure_trade_ids(positions)
        before    = len(positions)
        positions = [p for p in positions if p['id'] != position_id]
        if len(positions) == before:
            return False, f'Position #{position_id} not found'
        _save_positions(positions)
    return True, None


# =============================================================================
# Unrealized P&L enrichment
# =============================================================================

def enrich_open_positions(positions, live_prices):
    """Attach live unrealized P&L and distance to targets for open positions."""
    enriched = []
    for p in positions:
        p = dict(p)
        if p['status'] == 'OPEN':
            current = live_prices.get(p['coin_id'])
            if current:
                entry = float(p['entry_price'])
                # Reject price when wildly inconsistent with entry (wrong coin/API id, e.g. aster-2)
                if entry and (current <= 0 or current < entry / 10 or current > entry * 10):
                    current = None
                if current:
                    qty        = float(p['quantity'])
                    unreal_pct = ((current - entry) / entry) * 100
                    unreal_usd = (current - entry) * qty
                    if p['signal'] == 'SELL':
                        unreal_pct = -unreal_pct
                        unreal_usd = -unreal_usd
                    target_dist = ((float(p['current_target_price']) - current) / current) * 100 if p.get('current_target_price') is not None else None
                    stop_dist   = ((current - float(p['current_stop_price'])) / current) * 100 if p.get('current_stop_price') is not None else None
                    # Sanity-check: reject insane distances (wrong coin_id/token split, e.g. ASTER price mismatch)
                    if stop_dist is not None and abs(stop_dist) > 500:
                        stop_dist = None
                    if target_dist is not None and abs(target_dist) > 500:
                        target_dist = None
                    p['current_price']     = current
                    p['unrealized_pct']    = round(unreal_pct, 4)
                    p['unrealized_usd']    = round(unreal_usd, 4)
                    # target_distance is None for "winners run" positions (no fixed target) — don't round None.
                    p['target_distance']   = round(target_dist, 2) if target_dist is not None else None
                    p['stop_distance']     = round(stop_dist, 2) if stop_dist is not None else None
            if not current:
                p['current_price']   = None
                p['unrealized_pct']  = None
                p['unrealized_usd']  = None
                p['target_distance'] = None
                p['stop_distance']   = None
        enriched.append(p)
    return enriched


# =============================================================================
# Statistics
# =============================================================================

def calculate_stats(positions):
    """Calculate performance stats from all closed positions.

    profit_factor: gross_profit / gross_loss when gross_loss > 0; else 0.0 (e.g. all wins).
    expectancy_usd: wr*avg_win_usd - (1-wr)*avg_loss_usd in USD (wr = wins/closed).
    avg_win_pct / avg_loss_pct: only positions with outcome win/loss and pnl_pct present
    are included; positions missing pnl_pct are excluded from those averages.
    """
    closed = [p for p in positions if p['status'] == 'CLOSED']
    open_  = [p for p in positions if p['status'] == 'OPEN']

    empty = {
        'total_positions': 0, 'open_positions': len(open_), 'closed_positions': 0,
        'wins': 0, 'losses': 0, 'breakevens': 0, 'win_rate_pct': 0.0,
        'total_pnl_usd': 0.0, 'avg_win_pct': 0.0, 'avg_loss_pct': 0.0,
        'best_trade_pct': 0.0, 'worst_trade_pct': 0.0, 'profit_factor': 0.0,
        'current_streak': 0, 'streak_type': 'none', 'expectancy_usd': 0.0,
        'ai_target_hit_rate': 0.0,  # % of times signal target was hit
    }
    if not closed:
        return empty

    wins       = [p for p in closed if p.get('outcome') == 'win']
    losses     = [p for p in closed if p.get('outcome') == 'loss']
    breakevens = [p for p in closed if p.get('outcome') == 'breakeven']

    win_pcts   = [p['pnl_pct'] for p in wins   if p.get('pnl_pct') is not None]
    loss_pcts  = [p['pnl_pct'] for p in losses if p.get('pnl_pct') is not None]
    all_pnl    = [p['pnl_usd'] for p in closed if p.get('pnl_usd') is not None]
    all_pnl_pct = [p['pnl_pct'] for p in closed if p.get('pnl_pct') is not None]

    gross_profit  = sum(p for p in all_pnl if p > 0)
    gross_loss    = abs(sum(p for p in all_pnl if p < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0

    wr = len(wins) / len(closed)
    avg_win_usd  = sum(p['pnl_usd'] for p in wins   if p['pnl_usd']) / len(wins)   if wins   else 0
    avg_loss_usd = sum(abs(p['pnl_usd']) for p in losses if p['pnl_usd']) / len(losses) if losses else 0
    expectancy   = (wr * avg_win_usd) - ((1 - wr) * avg_loss_usd)

    # Current streak
    streak, streak_type = 0, 'none'
    for p in reversed(closed):
        outcome = p.get('outcome') or 'breakeven'
        if streak == 0:
            streak_type = outcome
        if outcome == streak_type:
            streak += 1
        else:
            break

    # Target hit rate
    ai_targets_tracked = [p for p in closed if p.get('hit_ai_target') is not None]
    ai_hit_rate = 0.0
    if ai_targets_tracked:
        hits = sum(1 for p in ai_targets_tracked if p['hit_ai_target'])
        ai_hit_rate = round((hits / len(ai_targets_tracked)) * 100, 1)

    # Performance analytics (Sharpe, Sortino, max drawdown, regime win rate, expectancy)
    perf = {}
    try:
        import os
        from performance_analytics import PerformanceAnalytics
        try:
            from app_config import load_account_config
            account_size = float(load_account_config().get('total_cash', 0) or 0)
        except Exception:
            account_size = 0
        perf = PerformanceAnalytics.from_positions(positions, account_size)
    except Exception:
        pass

    return {
        'total_positions':   len(closed) + len(open_),
        'open_positions':    len(open_),
        'closed_positions':  len(closed),
        'wins':              len(wins),
        'losses':            len(losses),
        'breakevens':        len(breakevens),
        'win_rate_pct':      round(wr * 100, 1),
        'total_pnl_usd':     round(sum(all_pnl), 2),
        'avg_win_pct':       round(sum(win_pcts) / len(win_pcts), 2)   if win_pcts  else 0.0,
        'avg_loss_pct':      round(sum(loss_pcts) / len(loss_pcts), 2) if loss_pcts else 0.0,
        'best_trade_pct':    round(max(all_pnl_pct), 2) if all_pnl_pct else 0.0,
        'worst_trade_pct':   round(min(all_pnl_pct), 2) if all_pnl_pct else 0.0,
        'profit_factor':     profit_factor,
        'current_streak':    streak,
        'streak_type':       streak_type,
        'expectancy_usd':    round(expectancy, 2),
        'ai_target_hit_rate': ai_hit_rate,
        'performance_analytics': perf,
    }


# =============================================================================
# CSV export
# =============================================================================

def export_csv():
    """Export all positions to CSV. Returns (csv_string, filename)."""
    positions = get_all_positions()
    filename  = f"positions_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        'ID', 'Trade ID', 'Status', 'Coin', 'Symbol', 'Signal',
        'Entry Price', 'Entry Time', 'Quantity', 'Capital USD',
        'Quant Target %', 'Quant Target $', 'Quant Stop %', 'Quant Stop $',
        'Current Target %', 'Current Target $', 'Current Stop %', 'Current Stop $',
        'Exit Price', 'Exit Time', 'P&L USD', 'P&L %',
        'Outcome', 'Exit Reason', 'Hit Target?',
        'Signal Timeframe', 'Signal Confidence', 'Notes',
    ])

    for p in sorted(positions, key=lambda x: x.get('id', 0)):
        writer.writerow([
            p.get('id'),           p.get('trade_id', ''),
            p.get('status'),
            p.get('coin_name'),    p.get('coin_symbol'),    p.get('signal'),
            p.get('entry_price'),  p.get('entry_time'),     p.get('quantity'),
            p.get('capital_usd'),
            p.get('ai_target_pct'), p.get('ai_target_price'),
            p.get('ai_stop_pct'),   p.get('ai_stop_price'),
            p.get('current_target_pct'), p.get('current_target_price'),
            p.get('current_stop_pct'),   p.get('current_stop_price'),
            p.get('exit_price', ''), p.get('exit_time', ''),
            p.get('pnl_usd', ''),    p.get('pnl_pct', ''),
            p.get('outcome', ''),    p.get('exit_reason', ''),
            p.get('hit_ai_target', ''),
            p.get('ai_timeframe', ''), p.get('ai_confidence', ''),
            p.get('notes', ''),
        ])

    return output.getvalue(), filename

def export_xlsx():
    """Export all positions to an Excel file (XLSX). Returns (bytes, filename).
    Falls back to CSV-as-.xls if writing XLSX fails (no extra dependency required).
    """
    positions = get_all_positions()
    filename = f"positions_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"

    try:
        import pandas as pd

        # Build DataFrame and pick column order similar to CSV export
        df = pd.DataFrame(positions)
        cols = [
            'id', 'trade_id', 'status', 'coin_name', 'coin_symbol', 'signal',
            'entry_price', 'entry_time', 'quantity', 'capital_usd',
            'ai_target_pct', 'ai_target_price', 'ai_stop_pct', 'ai_stop_price',
            'current_target_pct', 'current_target_price', 'current_stop_pct', 'current_stop_price',
            'exit_price', 'exit_time', 'pnl_usd', 'pnl_pct',
            'outcome', 'exit_reason', 'hit_ai_target',
            'ai_timeframe', 'ai_confidence', 'notes'
        ]

        # Keep only columns that actually exist in the DataFrame
        cols = [c for c in cols if c in df.columns]

        # Display headers for Excel (quant-first naming; dict keys unchanged)
        display_headers = {
            'ai_target_pct': 'Quant Target %', 'ai_target_price': 'Quant Target $',
            'ai_stop_pct': 'Quant Stop %', 'ai_stop_price': 'Quant Stop $',
            'hit_ai_target': 'Hit Target?',
            'ai_timeframe': 'Signal Timeframe', 'ai_confidence': 'Signal Confidence',
        }
        rename_map = {k: v for k, v in display_headers.items() if k in cols}
        df_export = df[cols].rename(columns=rename_map)

        output = io.BytesIO()

        # Try writing a multi-sheet workbook: All / Open / Closed
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_export.to_excel(writer, index=False, sheet_name='All Positions')
            if 'status' in df.columns:
                try:
                    df_open = df[df['status'] == 'OPEN'][cols].rename(columns=rename_map)
                    df_closed = df[df['status'] == 'CLOSED'][cols].rename(columns=rename_map)
                    if not df_open.empty:
                        df_open.to_excel(writer, index=False, sheet_name='Open Positions')
                    if not df_closed.empty:
                        df_closed.to_excel(writer, index=False, sheet_name='Closed Positions')
                except Exception:
                    # Non-fatal — continue with only the All Positions sheet
                    pass

        output.seek(0)
        return output.getvalue(), filename

    except Exception:
        # Fallback: return CSV but use .xls extension so Excel can open it
        csv_data, _ = export_csv()
        return csv_data.encode('utf-8'), filename.replace('.xlsx', '.xls')

# =============================================================================
# Public read helpers
# =============================================================================

def get_all_positions(exclude_test=False):
    """Return all positions; backfill trade_id on any that lack it and save once.
    exclude_test=True returns only live positions (for Positions tab, stats, risk)."""
    with _positions_lock:
        positions = _load_positions()
        if _ensure_trade_ids(positions):
            _save_positions(positions)
    if exclude_test:
        return [p for p in positions if not _is_test_position(p)]
    return positions


def _is_test_position(p):
    """True if position is a test/paper trade (Test tab), False if live (Positions tab)."""
    if p.get('is_test') is True:
        return True
    if p.get('exit_reason') == 'test_cleanup':
        return True
    return False


def get_open_positions(exclude_test=False):
    """Return open positions. exclude_test=True filters out test positions (for Positions tab)."""
    all_pos = [p for p in get_all_positions() if p['status'] == 'OPEN']
    if exclude_test:
        return [p for p in all_pos if not _is_test_position(p)]
    return all_pos


def get_closed_positions(exclude_test=False):
    """Return closed positions. exclude_test=True filters out test positions (for Positions tab)."""
    all_pos = [p for p in get_all_positions() if p['status'] == 'CLOSED']
    if exclude_test:
        return [p for p in all_pos if not _is_test_position(p)]
    return all_pos


def get_test_positions():
    """Return only test positions (for Test tab). Includes open and closed."""
    return [p for p in get_all_positions() if _is_test_position(p)]


def is_paper_trade(p) -> bool:
    """True if position is a simulated paper trade (source tag)."""
    return isinstance(p, dict) and (p.get('source') or '') == 'paper_trade'



def get_position_by_id(i):
    return next((p for p in get_all_positions() if p['id'] == i), None)
