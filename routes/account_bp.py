"""Account overview, settings, balance sync, reconciliation, and HELOC funding routes."""
from __future__ import annotations

import traceback
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, request

from admin_api_guard import require_admin_write_access
from app_config import get_execution_config, load_account_config
from trade_journal import get_open_positions, get_closed_positions, close_position, update_position

account_bp = Blueprint("account", __name__)


# =============================================================================
# Account overview & settings
# =============================================================================

@account_bp.route('/api/account/overview', methods=['GET'])
def get_account_overview():
    """Account Overview for dashboard widget."""
    try:
        import app as app_module
        return jsonify(app_module._build_account_overview())
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Error building account overview: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@account_bp.route('/api/account/update', methods=['POST'])
def update_account():
    """
    Update trading parameters: risk_pct, max_cash_usage_pct, dust_threshold_usd.
    Cash is managed exclusively via exchange sync (/api/account/sync-balance).
    HELOC funding config is managed via /api/funding/update.
    Exit strategy is fixed: winners always run (no fixed take-profit; trailing stop only).
    POST body: { risk_pct?, max_cash_usage_pct?, dust_threshold_usd? }
    """
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        import app as app_module
        data = request.get_json() or {}
        risk_pct = data.get('risk_pct')
        max_cash_usage_pct = data.get('max_cash_usage_pct')
        dust_threshold_usd = data.get('dust_threshold_usd')
        if risk_pct is None and max_cash_usage_pct is None and dust_threshold_usd is None:
            return jsonify({'error': 'Provide risk_pct, max_cash_usage_pct, and/or dust_threshold_usd'}), 400
        if risk_pct is not None:
            risk_pct = float(risk_pct)
            if risk_pct < 0.1 or risk_pct > 5.0:
                return jsonify({'error': 'Risk % must be between 0.1 and 5.0'}), 400
            app_module.position_sizer.max_risk_pct = risk_pct
            app_module.position_sizer.max_risk_usd = app_module.position_sizer.account_size * (risk_pct / 100)
        if max_cash_usage_pct is not None:
            max_cash_usage_pct = float(max_cash_usage_pct)
            if max_cash_usage_pct < 20 or max_cash_usage_pct > 65:
                return jsonify({'error': 'Max Cash Per Trade % must be between 20 and 65'}), 400
            app_module.position_sizer.update_max_cash_usage_pct(max_cash_usage_pct)
        cur_acct = load_account_config()
        dust_to_save = cur_acct.get('dust_threshold_usd', 1.0)
        if dust_threshold_usd is not None:
            dust_to_save = float(dust_threshold_usd)
            if dust_to_save < 0.01:
                return jsonify({'error': 'Dust threshold must be at least 0.01'}), 400
        app_module.save_account_config(
            total_cash=app_module.position_sizer.account_size,
            risk_pct=app_module.position_sizer.max_risk_pct,
            max_cash_usage_pct=getattr(app_module.position_sizer, 'max_cash_usage_pct', 40.0),
            dust_threshold_usd=dust_to_save,
        )
        app_module._sync_sizer_account_size()
        overview = app_module._build_account_overview()
        overview['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return jsonify(overview)
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Error updating account: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Balance sync
# =============================================================================

@account_bp.route('/api/account/sync-balance', methods=['GET'])
def sync_balance():
    """
    Fetch total cash (USD + USDC + USDT) and spot holdings from the selected exchange.
    On success: update account_config.json and position_sizer.account_size.
    Returns: { success, total_cash, holdings, message } or error.
    """
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        import app as app_module
        cfg = get_execution_config()
        keys = app_module._exchange_keys_configured()
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        result = app_module.fetch_balance_sync(exchange_id)
        total_cash = float(result.get('total_cash', 0) or 0)
        holdings = result.get('holdings') or {}
        if total_cash <= 0:
            return jsonify({
                'success': False,
                'error': 'Exchange reported no cash (USD/USDC/USDT). Check balance on exchange.',
                'auto_execution_exchange': exchange_id,
                'coinbase_configured': keys.get('coinbase', False),
                'kraken_configured': keys.get('kraken', False),
            }), 400
        app_module.position_sizer.update_account_size(total_cash)
        app_module.save_account_config(total_cash=total_cash)
        app_module._set_last_auto_sync_at(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z')
        app_module._reconcile_cache['data'] = None
        exchange_label = (result.get('exchange') or exchange_id).capitalize()
        return jsonify({
            'success': True,
            'total_cash': total_cash,
            'holdings': holdings,
            'message': f'Synced from {exchange_label}',
            'auto_execution_exchange': exchange_id,
            'coinbase_configured': keys.get('coinbase', False),
            'kraken_configured': keys.get('kraken', False),
        })
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Sync balance error: {e}")
        traceback.print_exc()
        cfg = get_execution_config()
        keys = app_module._exchange_keys_configured()
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        return jsonify({
            'success': False,
            'error': str(e),
            'auto_execution_exchange': exchange_id,
            'coinbase_configured': keys.get('coinbase', False),
            'kraken_configured': keys.get('kraken', False),
        }), 500


# =============================================================================
# Reconciliation
# =============================================================================

@account_bp.route('/api/account/reconcile', methods=['GET'])
def reconcile_account():
    """
    Reconcile exchange (Coinbase/Kraken) with Trade Journal. Syncs cash, then compares exchange
    spot holdings vs open positions in journal. Positions below dust_threshold_usd are ignored.
    Optional: ?sync_positions=1 — when discrepancies are only quantity_mismatch, update journal
    quantities to match exchange (exchange = source of truth) and return updated status.
    Returns: total_cash (synced), counts, discrepancies, status.
    """
    try:
        import app as app_module
        import time as _time
        do_sync_positions = request.args.get('sync_positions', '').strip().lower() in ('1', 'true', 'yes')
        force = request.args.get('force', '').strip().lower() in ('1', 'true', 'yes')
        guard = require_admin_write_access()
        if guard:
            return guard
        if (not force and not do_sync_positions
                and app_module._reconcile_cache['data']
                and (_time.time() - app_module._reconcile_cache['ts']) < app_module._RECONCILE_CACHE_TTL):
            return jsonify(app_module._reconcile_cache['data'])
        cfg = get_execution_config()
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        result = app_module.fetch_balance_sync(exchange_id)
        total_cash = float(result.get('total_cash', 0) or 0)
        exchange_holdings = result.get('holdings') or {}
        if total_cash > 0:
            app_module.position_sizer.update_account_size(total_cash)
            app_module.save_account_config(total_cash=total_cash)
            app_module._set_last_auto_sync_at(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z')
        _ex = {}
        for k, v in (exchange_holdings or {}).items():
            cur = (k or '').strip().upper()
            if not cur:
                continue
            _ex[cur] = round(_ex.get(cur, 0) + float(v or 0), 8)
        exchange_holdings = _ex

        open_positions = get_open_positions(exclude_test=True)
        journal_holdings = {}
        for p in open_positions:
            sym = (p.get('coin_symbol') or '').strip().upper()
            if not sym:
                continue
            qty = round(float(p.get('quantity', 0) or 0), 8)
            journal_holdings[sym] = round(journal_holdings.get(sym, 0) + qty, 8)
        journal_holdings = {s: round(q, 8) for s, q in journal_holdings.items()}

        acct_cfg = load_account_config()
        dust_threshold = float(acct_cfg.get('dust_threshold_usd', 1.0) or 1.0)
        price_by_symbol = app_module._price_by_symbol_from_cached()

        def value_usd(sym, qty):
            p = price_by_symbol.get((sym or '').strip().upper())
            if p is None:
                return None
            return float(qty) * float(p)

        def qty_matched(jq, eq):
            if jq <= 0 and eq <= 0:
                return True
            if jq <= 0 or eq <= 0:
                return False
            return abs(float(jq) - float(eq)) < 1e-8

        external = []
        in_journal_only = []
        quantity_mismatch = []
        for sym, qty in exchange_holdings.items():
            if qty <= 0:
                continue
            jq = journal_holdings.get(sym, 0)
            if jq <= 0:
                v = value_usd(sym, qty)
                if v is not None and v < dust_threshold:
                    continue
                if v is None:
                    continue
                external.append({"type": "external", "symbol": sym, "exchange_quantity": round(qty, 8)})
            elif not qty_matched(jq, qty):
                delta_usd = value_usd(sym, abs(jq - qty))
                if delta_usd is None:
                    pass
                elif delta_usd < dust_threshold:
                    pass
                else:
                    quantity_mismatch.append({"type": "quantity_mismatch", "symbol": sym, "journal_quantity": round(jq, 8), "exchange_quantity": round(qty, 8)})
        for sym, qty in journal_holdings.items():
            if qty <= 0:
                continue
            eq = exchange_holdings.get(sym, 0)
            if eq <= 0:
                v = value_usd(sym, qty)
                if v is not None and v < dust_threshold:
                    continue
                if v is None:
                    continue
                in_journal_only.append({"type": "in_journal_not_on_exchange", "symbol": sym, "journal_quantity": round(qty, 8)})
            elif not qty_matched(qty, eq) and not any(m.get("symbol") == sym for m in quantity_mismatch):
                delta_usd = value_usd(sym, abs(qty - eq))
                if delta_usd is None:
                    pass
                elif delta_usd < dust_threshold:
                    pass
                else:
                    quantity_mismatch.append({"type": "quantity_mismatch", "symbol": sym, "journal_quantity": round(qty, 8), "exchange_quantity": round(eq, 8)})
        discrepancies = external + in_journal_only + quantity_mismatch
        status = "clean" if not discrepancies else "discrepancy_detected"

        if do_sync_positions and status == "discrepancy_detected":
            by_symbol = {}
            for p in open_positions:
                sym = (p.get('coin_symbol') or '').strip().upper()
                if not sym:
                    continue
                pid, jq = p.get('id'), float(p.get('quantity', 0) or 0)
                if sym not in by_symbol:
                    by_symbol[sym] = []
                by_symbol[sym].append((pid, jq))
            for sym in by_symbol:
                by_symbol[sym].sort(key=lambda x: x[0])
            if quantity_mismatch and not external and not in_journal_only:
                for sym, eq in exchange_holdings.items():
                    if eq <= 0:
                        continue
                    positions_for_sym = by_symbol.get(sym, [])
                    if not positions_for_sym:
                        continue
                    first_id, first_jq = positions_for_sym[0]
                    if not qty_matched(first_jq, eq):
                        pos, err = update_position(first_id, quantity=round(eq, 8))
                        if not err:
                            for p in open_positions:
                                if p.get('id') == first_id:
                                    p['quantity'] = eq
                                    break
                    for pid_rest, jq in positions_for_sym[1:]:
                        if jq != 0:
                            update_position(pid_rest, quantity=0)
                            for p in open_positions:
                                if p.get('id') == pid_rest:
                                    p['quantity'] = 0
                                    break
            if in_journal_only:
                for d in in_journal_only:
                    sym = (d.get("symbol") or "").strip().upper()
                    if not sym:
                        continue
                    for pid_close, jq in by_symbol.get(sym, []):
                        if jq == 0:
                            continue
                        pos_obj = next((p for p in open_positions if p.get("id") == pid_close), None)
                        if pos_obj and (pos_obj.get("sl_order_id") or pos_obj.get("tp_order_id")):
                            try:
                                if pos_obj.get("sl_order_id"):
                                    app_module.cancel_order(pos_obj["sl_order_id"])
                                if pos_obj.get("tp_order_id"):
                                    app_module.cancel_order(pos_obj["tp_order_id"])
                                update_position(pid_close, {"sl_order_id": None, "tp_order_id": None})
                                pos_obj["sl_order_id"] = None
                                pos_obj["tp_order_id"] = None
                            except Exception as e:
                                app_module.logger.warning("  Reconcile: cancel SL/TP for %s (id=%s): %s", sym, pid_close, e)
                        exit_price_fallback = (price_by_symbol.get(sym) if price_by_symbol else None) or (float(pos_obj.get("entry_price", 0) or 0) if pos_obj else 0.01)
                        if exit_price_fallback <= 0:
                            exit_price_fallback = 0.01
                        closed, err = close_position(
                            pid_close,
                            exit_price=exit_price_fallback,
                            exit_reason="exchange_sold",
                            notes="Reconcile: exchange balance 0 or dust; position closed.",
                        )
                        if not err:
                            for p in open_positions:
                                if p.get("id") == pid_close:
                                    p["quantity"] = 0
                                    p["status"] = "CLOSED"
                                    break
            open_positions = get_open_positions(exclude_test=True)
            journal_holdings = {}
            for p in open_positions:
                sym = (p.get('coin_symbol') or '').strip().upper()
                if not sym:
                    continue
                qty = round(float(p.get('quantity', 0) or 0), 8)
                journal_holdings[sym] = round(journal_holdings.get(sym, 0) + qty, 8)
            quantity_mismatch = []
            for sym, qty in exchange_holdings.items():
                if qty <= 0:
                    continue
                jq = journal_holdings.get(sym, 0)
                if jq > 0 and not qty_matched(jq, qty):
                    quantity_mismatch.append({"type": "quantity_mismatch", "symbol": sym, "journal_quantity": round(jq, 8), "exchange_quantity": round(qty, 8)})
            discrepancies = external + in_journal_only + quantity_mismatch
            status = "clean" if not discrepancies else "discrepancy_detected"

        app_module._append_reconciliation_history({
            "source": "reconcile",
            "status": status,
            "discrepancies": discrepancies,
        })
        if status == 'discrepancy_detected' and discrepancies:
            try:
                app_module.alerts.send_balance_mismatch_alert(discrepancies)
            except Exception:
                pass
        resp = {
            "total_cash": total_cash,
            "dashboard_open_positions_count": len(open_positions),
            "exchange_open_positions_count": len(exchange_holdings),
            "discrepancies": discrepancies,
            "status": status,
        }
        app_module._reconcile_cache['data'] = resp
        app_module._reconcile_cache['ts'] = _time.time()
        return jsonify(resp)
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Reconcile error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@account_bp.route('/api/account/sync-position-quantities', methods=['POST'])
def sync_position_quantities():
    """
    Update journal open positions so their quantity matches the exchange (exchange = source of truth).
    Returns: { success, updated, skipped, errors }.
    """
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        import app as app_module
        cfg = get_execution_config()
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        result = app_module.fetch_balance_sync(exchange_id)
        exchange_holdings = result.get('holdings') or {}
        _ex = {}
        for k, v in exchange_holdings.items():
            cur = (k or '').strip().upper()
            if cur:
                _ex[cur] = _ex.get(cur, 0) + float(v or 0)
        exchange_holdings = _ex

        acct_cfg = load_account_config()
        dust_threshold = float(acct_cfg.get('dust_threshold_usd', 1.0) or 1.0)
        price_by_symbol = app_module._price_by_symbol_from_cached()

        open_positions = get_open_positions(exclude_test=True)
        by_symbol = {}
        for p in open_positions:
            sym = (p.get('coin_symbol') or '').strip().upper()
            if not sym:
                continue
            pid = p.get('id')
            qty = float(p.get('quantity', 0) or 0)
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append((pid, qty))
        for sym in by_symbol:
            by_symbol[sym].sort(key=lambda x: x[0])

        def qty_matched(jq, eq):
            if jq <= 0 and eq <= 0:
                return True
            if jq <= 0 or eq <= 0:
                return False
            jq, eq = float(jq), float(eq)
            return abs(jq - eq) < 1e-8

        def value_usd(sym, qty):
            p = price_by_symbol.get((sym or '').strip().upper())
            if p is None:
                return None
            return float(qty) * float(p)

        updated = []
        skipped = []
        errors = []
        for sym, eq in exchange_holdings.items():
            if eq <= 0:
                continue
            val = value_usd(sym, eq)
            if val is not None and val < dust_threshold:
                skipped.append({"symbol": sym, "reason": "below_dust_threshold"})
                continue
            # Owner-held coin: the exchange balance is the OWNER's, not the system's. Adopting
            # it would set a journal position to the whole balance and then place a stop on
            # all of it (protected_assets.py, owner instruction 2026-09-27).
            from protected_assets import is_protected
            if is_protected(sym):
                skipped.append({"symbol": sym, "reason": "protected_asset_owner_held"})
                continue
            positions_for_sym = by_symbol.get(sym, [])
            if len(positions_for_sym) == 0:
                skipped.append({"symbol": sym, "reason": "no_open_position_in_journal"})
                continue
            first_id, first_jq = positions_for_sym[0]
            rest = positions_for_sym[1:]
            if not qty_matched(first_jq, eq):
                pos, err = update_position(first_id, quantity=round(eq, 8))
                if err:
                    errors.append({"symbol": sym, "position_id": first_id, "error": err})
                else:
                    updated.append({
                        "position_id": first_id,
                        "symbol": sym,
                        "old_quantity": round(first_jq, 8),
                        "new_quantity": round(eq, 8),
                    })
                    for p in open_positions:
                        if p.get('id') == first_id:
                            p['quantity'] = eq
                            break
            for position_id, jq in rest:
                if jq != 0:
                    pos, err = update_position(position_id, quantity=0)
                    if err:
                        errors.append({"symbol": sym, "position_id": position_id, "error": err})
                    else:
                        updated.append({
                            "position_id": position_id,
                            "symbol": sym,
                            "old_quantity": round(jq, 8),
                            "new_quantity": 0,
                        })
                        for p in open_positions:
                            if p.get('id') == position_id:
                                p['quantity'] = 0
                                break

        for sym, positions_for_sym in by_symbol.items():
            eq = float(exchange_holdings.get(sym, 0) or 0)
            val = value_usd(sym, eq)
            if eq > 0 and val is not None and val >= dust_threshold:
                continue
            for position_id, jq in positions_for_sym:
                if jq != 0:
                    pos_obj = next((p for p in open_positions if p.get('id') == position_id), None)
                    if pos_obj and (pos_obj.get('sl_order_id') or pos_obj.get('tp_order_id')):
                        try:
                            if pos_obj.get('sl_order_id'):
                                app_module.cancel_order(pos_obj['sl_order_id'])
                            if pos_obj.get('tp_order_id'):
                                app_module.cancel_order(pos_obj['tp_order_id'])
                            update_position(position_id, {'sl_order_id': None, 'tp_order_id': None})
                            pos_obj['sl_order_id'] = None
                            pos_obj['tp_order_id'] = None
                        except Exception as e:
                            app_module.logger.warning(f"  Cancel SL/TP for closed {sym}: {e}")
                    exit_price_fallback = (price_by_symbol.get(sym) if price_by_symbol else None) or (float(pos_obj.get('entry_price', 0) or 0) if pos_obj else 0)
                    if exit_price_fallback <= 0 and pos_obj:
                        exit_price_fallback = float(pos_obj.get('entry_price', 0) or 0)
                    if exit_price_fallback <= 0:
                        exit_price_fallback = 0.01
                    closed, err = close_position(
                        position_id,
                        exit_price=exit_price_fallback,
                        exit_reason='exchange_sold',
                        notes='Sync: exchange balance 0 or dust; position closed.',
                    )
                    if err:
                        errors.append({"symbol": sym, "position_id": position_id, "error": err})
                    else:
                        updated.append({
                            "position_id": position_id,
                            "symbol": sym,
                            "old_quantity": round(jq, 8),
                            "new_quantity": 0,
                            "closed": True,
                        })
                        for p in open_positions:
                            if p.get('id') == position_id:
                                p['quantity'] = 0
                                p['status'] = 'CLOSED'
                                break

        if updated and (cfg.get('auto_execution_exchange') or 'coinbase').lower() == 'coinbase':
            for u in updated:
                if u['new_quantity'] <= 0:
                    continue
                pos = next((p for p in open_positions if p.get('id') == u['position_id']), None)
                if not pos:
                    continue
                sl_id = pos.get('sl_order_id')
                tp_id = pos.get('tp_order_id')
                if sl_id or tp_id:
                    try:
                        if sl_id:
                            app_module.cancel_order(sl_id)
                        if tp_id:
                            app_module.cancel_order(tp_id)
                        update_position(pos['id'], {'sl_order_id': None, 'tp_order_id': None})
                        pos['sl_order_id'] = None
                        pos['tp_order_id'] = None
                        pos['quantity'] = u['new_quantity']
                        app_module._place_sl_tp_with_retry(pos)
                        app_module.logger.info(f"  Re-placed SL/TP for {u['symbol']} after qty sync ({u['old_quantity']} → {u['new_quantity']})")
                    except Exception as e:
                        app_module.logger.warning(f"  Failed to re-place SL/TP for {u['symbol']}: {e}")

        app_module._reconcile_cache['data'] = None
        app_module._append_reconciliation_history({
            "source": "sync_position_quantities",
            "status": "clean" if not errors else "errors",
            "summary": {"updated": len(updated), "skipped": len(skipped), "errors": len(errors)},
        })
        return jsonify({
            "success": True,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        })
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Sync position quantities error: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# HELOC Funding
# =============================================================================

@account_bp.route('/api/funding', methods=['GET'])
def get_funding():
    """Return HELOC funding config + computed cost-of-capital metrics."""
    try:
        import app as app_module
        cfg = load_account_config()
        open_positions = get_open_positions(exclude_test=True)
        metrics = app_module._compute_heloc_metrics(cfg, open_positions)
        if cfg.get('heloc_mode') and cfg.get('usdc_yield_auto_refresh', True):
            refreshed = app_module._fetch_usdc_yield()
            if refreshed is not None:
                cfg = load_account_config()
                metrics = app_module._compute_heloc_metrics(cfg, open_positions)
        return jsonify({
            'heloc_mode': cfg.get('heloc_mode', False),
            'heloc_notional': round(float(cfg.get('heloc_notional', 0) or 0), 2),
            'heloc_rate_pct': round(float(cfg.get('heloc_rate_pct', 8.0) or 8.0), 2),
            'usdc_yield_pct': round(float(cfg.get('usdc_yield_pct', 4.1) or 4.1), 2),
            'usdc_yield_auto_refresh': cfg.get('usdc_yield_auto_refresh', True),
            'usdc_yield_last_updated': cfg.get('usdc_yield_last_updated'),
            'heloc_drawn': round(float(cfg.get('heloc_drawn', 0) or 0), 2),
            'heloc_drawn_peak': round(float(cfg.get('heloc_drawn_peak', 0) or 0), 2),
            'max_drawn_drawdown_pct': round(float(cfg.get('max_drawn_drawdown_pct', 30.0) or 30.0), 1),
            'hurdle_mode': cfg.get('hurdle_mode', 'per_trade'),
            'metrics': metrics,
        })
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Error getting funding config: {e}")
        return jsonify({'error': str(e)}), 500


@account_bp.route('/api/funding/update', methods=['POST'])
def update_funding():
    """Update HELOC funding configuration. All fields optional."""
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        import app as app_module
        data = request.get_json() or {}
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        allowed = {
            'heloc_mode', 'heloc_notional', 'heloc_rate_pct', 'usdc_yield_pct',
            'usdc_yield_auto_refresh', 'heloc_drawn', 'max_drawn_drawdown_pct', 'hurdle_mode',
        }
        unknown = set(data.keys()) - allowed
        if unknown:
            return jsonify({'error': f'Unknown fields: {unknown}'}), 400
        if 'heloc_notional' in data:
            v = float(data['heloc_notional'])
            if v < 0:
                return jsonify({'error': 'Notional amount cannot be negative'}), 400
        if 'heloc_rate_pct' in data:
            v = float(data['heloc_rate_pct'])
            if v < 0 or v > 30:
                return jsonify({'error': 'HELOC rate must be between 0 and 30%'}), 400
        if 'usdc_yield_pct' in data:
            v = float(data['usdc_yield_pct'])
            if v < 0 or v > 20:
                return jsonify({'error': 'USDC yield must be between 0 and 20%'}), 400
        if 'heloc_drawn' in data:
            v = float(data['heloc_drawn'])
            if v < 0:
                return jsonify({'error': 'Drawn amount cannot be negative'}), 400
        if 'max_drawn_drawdown_pct' in data:
            v = float(data['max_drawn_drawdown_pct'])
            if v < 5 or v > 100:
                return jsonify({'error': 'Drawdown limit must be between 5 and 100%'}), 400
        if 'hurdle_mode' in data and data['hurdle_mode'] not in ('per_trade', 'off'):
            return jsonify({'error': 'hurdle_mode must be "per_trade" or "off"'}), 400
        save_kwargs = {}
        if 'heloc_mode' in data:
            save_kwargs['heloc_mode'] = bool(data['heloc_mode'])
        if 'heloc_notional' in data:
            save_kwargs['heloc_notional'] = float(data['heloc_notional'])
        if 'heloc_rate_pct' in data:
            save_kwargs['heloc_rate_pct'] = float(data['heloc_rate_pct'])
        if 'usdc_yield_pct' in data:
            save_kwargs['usdc_yield_pct'] = float(data['usdc_yield_pct'])
        if 'usdc_yield_auto_refresh' in data:
            save_kwargs['usdc_yield_auto_refresh'] = bool(data['usdc_yield_auto_refresh'])
        if 'heloc_drawn' in data:
            save_kwargs['heloc_drawn'] = float(data['heloc_drawn'])
        if 'max_drawn_drawdown_pct' in data:
            save_kwargs['max_drawn_drawdown_pct'] = float(data['max_drawn_drawdown_pct'])
        if 'hurdle_mode' in data:
            save_kwargs['hurdle_mode'] = data['hurdle_mode']
        app_module.save_account_config(**save_kwargs)
        app_module._sync_sizer_account_size()
        cfg = load_account_config()
        metrics = app_module._compute_heloc_metrics(cfg)
        result = {
            'heloc_mode': cfg.get('heloc_mode', False),
            'heloc_notional': round(float(cfg.get('heloc_notional', 0) or 0), 2),
            'heloc_rate_pct': round(float(cfg.get('heloc_rate_pct', 8.0) or 8.0), 2),
            'usdc_yield_pct': round(float(cfg.get('usdc_yield_pct', 4.1) or 4.1), 2),
            'usdc_yield_auto_refresh': cfg.get('usdc_yield_auto_refresh', True),
            'usdc_yield_last_updated': cfg.get('usdc_yield_last_updated'),
            'heloc_drawn': round(float(cfg.get('heloc_drawn', 0) or 0), 2),
            'heloc_drawn_peak': round(float(cfg.get('heloc_drawn_peak', 0) or 0), 2),
            'max_drawn_drawdown_pct': round(float(cfg.get('max_drawn_drawdown_pct', 30.0) or 30.0), 1),
            'hurdle_mode': cfg.get('hurdle_mode', 'per_trade'),
            'metrics': metrics,
            'saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        return jsonify(result)
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Error updating funding config: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@account_bp.route('/api/funding/performance', methods=['GET'])
def get_funding_performance():
    """Return trading P&L vs carry cost for a given period."""
    try:
        import app as app_module
        period = request.args.get('period', '30')
        if period == 'all':
            days = 9999
        else:
            days = int(period)
        cfg = load_account_config()
        heloc_rate = float(cfg.get('heloc_rate_pct', 8.0) or 8.0)
        usdc_yield = float(cfg.get('usdc_yield_pct', 4.1) or 4.1)
        drawn = float(cfg.get('heloc_drawn', 0) or 0)
        cutoff = datetime.now() - timedelta(days=days)
        closed = get_closed_positions(exclude_test=True)
        period_closed = []
        for p in closed:
            exit_t = p.get('exit_time')
            if exit_t:
                try:
                    exit_dt = datetime.strptime(str(exit_t)[:19], '%Y-%m-%d %H:%M:%S')
                    if exit_dt >= cutoff:
                        period_closed.append(p)
                except (ValueError, TypeError):
                    pass
        trading_pnl = sum(float(p.get('pnl_usd', 0) or 0) for p in period_closed)
        actual_days = min(days, (datetime.now() - cutoff).days) if days < 9999 else days
        if period_closed:
            try:
                earliest = min(
                    datetime.strptime(str(p['exit_time'])[:19], '%Y-%m-%d %H:%M:%S')
                    for p in period_closed if p.get('exit_time')
                )
                actual_days = max(1, (datetime.now() - earliest).days)
            except Exception:
                actual_days = max(1, days)
        else:
            actual_days = max(1, days)
        open_positions = get_open_positions(exclude_test=True)
        deployed = sum(float(p.get('capital_usd', 0) or 0) for p in open_positions)
        idle_approx = max(0, drawn - deployed)
        carry_deployed = deployed * (heloc_rate / 100) * (actual_days / 365)
        carry_idle = idle_approx * ((heloc_rate - usdc_yield) / 100) * (actual_days / 365)
        carry_cost = carry_deployed + carry_idle
        net_after_carry = trading_pnl - carry_cost
        annualized_roc = (net_after_carry / drawn * 365 / actual_days * 100) if drawn > 0 and actual_days > 0 else 0
        return jsonify({
            'period_days': actual_days,
            'trades_closed': len(period_closed),
            'trading_pnl': round(trading_pnl, 2),
            'carry_cost': round(carry_cost, 2),
            'net_after_carry': round(net_after_carry, 2),
            'annualized_roc_pct': round(annualized_roc, 2),
            'deployed_usd': round(deployed, 2),
            'idle_usd': round(idle_approx, 2),
        })
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Error computing funding performance: {e}")
        return jsonify({'error': str(e)}), 500


@account_bp.route('/api/funding/refresh-yield', methods=['POST'])
def refresh_usdc_yield():
    """Force-refresh USDC yield rate."""
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        import app as app_module
        rate = app_module._fetch_usdc_yield(force=True)
        if rate is not None:
            return jsonify({'usdc_yield_pct': rate, 'updated_at': datetime.now().strftime('%Y-%m-%d')})
        cfg = load_account_config()
        return jsonify({
            'usdc_yield_pct': cfg.get('usdc_yield_pct', 4.1),
            'updated_at': cfg.get('usdc_yield_last_updated'),
            'warning': 'Could not fetch latest rate; using stored value',
        })
    except Exception as e:
        import app as app_module
        app_module.logger.error(f"Error refreshing USDC yield: {e}")
        return jsonify({'error': str(e)}), 500
