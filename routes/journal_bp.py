"""Journal (positions) API routes."""
from __future__ import annotations

import traceback
from flask import Blueprint, jsonify, request, Response

from admin_api_guard import require_admin_write_access
from app_config import get_execution_config, load_account_config
from trade_journal import (
    get_all_positions,
    get_open_positions,
    open_position,
    close_position,
    delete_position,
    update_position,
    update_targets,
    enrich_open_positions,
    calculate_stats,
    export_csv,
    export_xlsx,
    is_paper_trade,
)

journal_bp = Blueprint("journal", __name__)


def _is_dashboard_live_journal_position(p: dict) -> bool:
    """Positions tab + stats: real journal rows only (exclude test cleanup, test tab, and paper trades)."""
    if p.get('is_test') or p.get('exit_reason') == 'test_cleanup':
        return False
    if is_paper_trade(p):
        return False
    return True


@journal_bp.route('/api/journal/positions', methods=['GET'])
def journal_get_positions():
    """Live journal rows only: excludes test-tab trades and paper_trading simulations. Unrealized P&L from cache. Includes pending TWAP orders."""
    import app as app_module
    try:
        all_pos = get_all_positions()
        live_pos = [p for p in all_pos if _is_dashboard_live_journal_position(p)]
        prices = app_module._live_price_map()
        if not prices and any(p.get('status') == 'OPEN' for p in live_pos):
            try:
                from test_mode import get_test_mode
                n_enhanced = 5 if get_test_mode() else 10
                raw = app_module.fetcher.get_all_data(n_enhanced=n_enhanced)
                prices = {c['id']: c.get('current_price') for c in raw.get('coins', []) if c.get('id') and c.get('current_price') is not None}
            except Exception:
                pass
        open_coin_ids = list({p.get('coin_id') for p in live_pos if p.get('status') == 'OPEN' and p.get('coin_id')})

        def _valid_price(cid):
            v = (prices or {}).get(cid)
            try:
                return v is not None and float(v) > 0
            except (TypeError, ValueError):
                return False

        missing = [cid for cid in open_coin_ids if not _valid_price(cid)]
        if missing:
            try:
                extra = app_module.fetcher.get_current_prices_fast(missing, max_coins_cache_age_sec=0)
                if extra:
                    prices = {**(prices or {}), **extra}
            except Exception:
                pass
        try:
            cfg = get_execution_config()
            exchange = (cfg.get('auto_execution_exchange') or 'coinbase').strip().lower()
            if exchange == 'coinbase':
                open_pos = [p for p in live_pos if p.get('status') == 'OPEN']
                symbols = list(set((p.get('coin_symbol') or '').strip().upper() for p in open_pos if (p.get('coin_symbol') or '').strip()))
                if symbols:
                    coinbase_prices = app_module.fetcher.get_coinbase_spot_prices(symbols)
                    if coinbase_prices:
                        prices = prices or {}
                        for p in open_pos:
                            sym = (p.get('coin_symbol') or '').strip().upper()
                            cid = p.get('coin_id')
                            if sym in coinbase_prices and cid:
                                prices[cid] = coinbase_prices[sym]
        except Exception:
            pass
        positions = enrich_open_positions(live_pos, prices)
        stats = calculate_stats(live_pos)
        pending_twap = app_module.get_active_twap_orders()
        return jsonify({
            'positions': positions,
            'stats': stats,
            'pending_twap_orders': pending_twap,
        })
    except Exception as e:
        app_module.logger.error(f"Error getting positions: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/position/<int:position_id>/place-sl-tp', methods=['POST'])
def journal_place_sl_tp(position_id):
    """Place SL and TP limit orders on Coinbase for an existing open position. Idempotent."""
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        positions = get_all_positions()
        position = next((p for p in positions if p.get('id') == position_id), None)
        if not position:
            return jsonify({'error': 'Position not found'}), 404
        if position.get('status') != 'OPEN':
            return jsonify({'error': 'Position is not OPEN'}), 400
        if position.get('is_test'):
            return jsonify({'error': 'Test positions do not place exchange orders'}), 400
        cfg = get_execution_config()
        if (cfg.get('auto_execution_exchange') or 'coinbase').lower() != 'coinbase':
            return jsonify({'error': 'Coinbase execution not configured'}), 400
        position = next((p for p in get_open_positions() if p.get('id') == position_id), position)
        sl_ok, tp_ok, sl_error, tp_attempted, tp_error = app_module._place_sl_tp_with_retry(position)
        position = next((p for p in get_open_positions() if p.get('id') == position_id), position)
        return jsonify({
            'status': 'ok',
            'position_id': position_id,
            'sl_ok': sl_ok,
            'tp_ok': tp_ok,
            'sl_error': sl_error,
            'tp_attempted': tp_attempted,
            'tp_error': tp_error,
            'sl_order_id': position.get('sl_order_id'),
            'tp_order_id': position.get('tp_order_id'),
        })
    except Exception as e:
        app_module.logger.error(f"Place SL/TP error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/open', methods=['POST'])
def journal_open_position():
    """Open new position. Rejects if trade size exceeds cash available to trade."""
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        overview = app_module._build_account_overview()
        cash_available = overview.get('cash_available_to_trade', 0) or 0
        capital = float(data.get('capital_usd') or 0)
        if not capital:
            entry = float(data.get('entry_price') or 0)
            qty = float(data.get('quantity') or 0)
            capital = entry * qty
        if cash_available <= 0:
            return jsonify({
                'error': 'Insufficient cash: no cash available for new trades. Close positions or update Total Cash in Account Overview.'
            }), 400
        if capital > cash_available:
            try:
                app_module.log_skipped_insufficient_cash(
                    coin_id=data.get('coin_id', ''),
                    coin_name=data.get('coin_name', ''),
                    symbol=data.get('coin_symbol', ''),
                    position_usd=capital,
                    cash_available=cash_available,
                    consensus_signal=data.get('signal', ''),
                )
                if cash_available > 0 and app_module._alerts_ready():
                    app_module.alerts.send_insufficient_cash_alert(
                        coin_symbol=data.get('coin_symbol', ''),
                        position_usd=capital,
                        cash_available=cash_available,
                    )
            except Exception as e:
                app_module.logger.warning(f"  ⚠ Skipped log/alert failed: {e}")
            return jsonify({
                'error': f'Insufficient cash: trade requires ${capital:,.2f} but only ${cash_available:,.2f} is available. Close positions or reduce size. (Logged and alert sent.)'
            }), 400
        acct_cfg = load_account_config()
        data = dict(data)
        entry_price = float(data.get('entry_price') or 0)
        coin_id = data.get('coin_id') or ''
        symbol = (data.get('coin_symbol') or '').strip().upper()
        dyn = app_module._get_dynamic_stop_target_for_coin(coin_id, symbol, entry_price)
        if dyn:
            data['ai_stop_pct'] = dyn['stop_loss_pct']
            data['ai_target_pct'] = dyn['take_profit_pct']
        # Winners always run — open_position sets no fixed target regardless of any passed flag.
        from test_mode import get_test_mode
        data['is_test'] = get_test_mode()
        data['lean_override'] = True  # manual user open — exempt from the lean fail-closed fill guard
        cached_signals = app_module._signal_fields_from_cached(symbol)
        for k, v in cached_signals.items():
            if data.get(k) is None and v is not None:
                data[k] = v
        position = open_position(data)
        if not position:
            return jsonify({'error': f'Could not open position for {symbol} — duplicate check rejected it. Close the existing open position first.'}), 409
        sl_tp_warning = None
        if not position.get('is_test'):
            cfg = get_execution_config()
            if (cfg.get('auto_execution_exchange') or 'coinbase').lower() == 'coinbase':
                try:
                    exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
                    result = app_module.fetch_balance_sync(exchange_id)
                    holdings = result.get('holdings') or {}
                    ex_qty = sum(float(v or 0) for k, v in holdings.items() if (k or '').strip().upper() == symbol)
                    if ex_qty > 0:
                        app_module._place_sl_tp_with_retry(position)
                        position = next((p for p in get_open_positions() if p['id'] == position['id']), position)
                    else:
                        sl_tp_warning = f'{symbol} not found on exchange — SL/TP orders not placed. Add the asset to Coinbase and use "Place SL/TP" button.'
                        app_module.logger.warning(f"  ⚠ {sl_tp_warning}")
                except Exception as e:
                    app_module.logger.warning(f"  ⚠ SL/TP placement failed: {e}")
                    traceback.print_exc()
        resp = {'status': 'ok', 'position': position}
        if sl_tp_warning:
            resp['warning'] = sl_tp_warning
        return jsonify(resp)
    except Exception as e:
        app_module.logger.error(f"Error opening position: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/close/<int:position_id>', methods=['POST'])
def journal_close_position(position_id):
    """Close position"""
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json()
        if data is None:
            return jsonify({'error': 'Invalid or missing JSON body'}), 400
        exit_price = data.get('exit_price')
        exit_reason = data.get('exit_reason', 'manual')
        notes = data.get('notes', '')
        if exit_price is None:
            return jsonify({'error': 'exit_price required'}), 400
        pos_before = next((p for p in get_open_positions() if p.get('id') == position_id), None)
        if pos_before:
            for oid in [pos_before.get('tp_order_id'), pos_before.get('sl_order_id')]:
                if oid:
                    try:
                        app_module.cancel_order(oid)
                    except Exception:
                        pass
        position, err = close_position(position_id, exit_price, exit_reason, notes)
        if err:
            return jsonify({'error': err}), 404
        app_module.do_auto_sync_balance()
        return jsonify({'status': 'ok', 'position': position})
    except Exception as e:
        app_module.logger.error(f"Error closing position: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/update-targets/<int:position_id>', methods=['POST'])
def journal_update_targets(position_id):
    """Update position targets"""
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json()
        if data is None:
            return jsonify({'error': 'Invalid or missing JSON body'}), 400
        new_target_pct = data.get('target_pct')
        new_stop_pct = data.get('stop_pct')
        position, err = update_targets(position_id, new_target_pct, new_stop_pct)
        if err:
            return jsonify({'error': err}), 404
        return jsonify({'status': 'ok', 'position': position})
    except Exception as e:
        app_module.logger.error(f"Error updating targets: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/position/<int:position_id>', methods=['PATCH'])
def journal_update_position(position_id):
    """Update position details (notes, quantity, entry price, target %, stop %)."""
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        position, err = update_position(
            position_id,
            notes=data.get('notes'),
            quantity=data.get('quantity'),
            entry_price_new=data.get('entry_price'),
            target_pct=data.get('target_pct'),
            stop_pct=data.get('stop_pct'),
        )
        if err:
            return jsonify({'error': err}), 404
        return jsonify({'status': 'ok', 'position': position})
    except Exception as e:
        app_module.logger.error(f"Error updating position: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/delete/<int:position_id>', methods=['DELETE'])
def journal_delete_position(position_id):
    """Delete position"""
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        ok, err = delete_position(position_id)
        if not ok:
            return jsonify({'error': err}), 404
        return jsonify({'status': 'ok', 'deleted_id': position_id})
    except Exception as e:
        app_module.logger.error(f"Error deleting position: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/export', methods=['GET'])
def journal_export_csv():
    """Export to CSV"""
    import app as app_module
    try:
        csv_data, filename = export_csv()
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        app_module.logger.error(f"Error exporting CSV: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/export/xlsx', methods=['GET'])
def journal_export_xlsx():
    """Export to Excel"""
    import app as app_module
    try:
        data, filename = export_xlsx()
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if filename.endswith('.xlsx') else 'application/vnd.ms-excel'
        return Response(
            data,
            mimetype=mimetype,
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        app_module.logger.error(f"Error exporting XLSX: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/open-from-exchange', methods=['POST'])
def journal_open_from_exchange():
    """
    Add an existing exchange holding to the journal (e.g. after restart when a Book-to-Coinbase fill
    wasn't recorded). Skips cash check. Body: { symbol, quantity, entry_price? }.
    """
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        symbol = (data.get('symbol') or '').strip().upper()
        quantity = float(data.get('quantity') or 0)
        if not symbol:
            return jsonify({'error': 'symbol required'}), 400
        if quantity <= 0:
            return jsonify({'error': 'quantity must be positive'}), 400
        entry_price = data.get('entry_price')
        if entry_price is not None:
            entry_price = float(entry_price)
        if entry_price is None or entry_price <= 0:
            prices = app_module._price_by_symbol_from_cached()
            entry_price = prices.get(symbol)
            if entry_price is None or entry_price <= 0:
                return jsonify({
                    'error': f'Could not get price for {symbol}. Provide entry_price or refresh dashboard cache first.'
                }), 400
        coin_id, coin_name = app_module._coin_id_and_name_for_symbol(symbol)
        if not coin_id:
            coin_id = symbol.lower()
        if not coin_name:
            coin_name = symbol
        dyn = app_module._get_dynamic_stop_target_for_coin(coin_id, symbol, entry_price)
        if dyn:
            ai_stop_pct = dyn['stop_loss_pct']
            ai_target_pct = dyn['take_profit_pct']
        else:
            ai_stop_pct = float(data.get('ai_stop_pct', 4.0))
            ai_target_pct = float(data.get('ai_target_pct', 6.0))
        acct_cfg = load_account_config()
        cached_signals = app_module._signal_fields_from_cached(symbol)
        open_data = {
            'coin_id': coin_id,
            'coin_name': coin_name,
            'coin_symbol': symbol,
            'signal': 'BUY',
            'entry_price': entry_price,
            'quantity': quantity,
            'capital_usd': round(entry_price * quantity, 2),
            'ai_stop_pct': ai_stop_pct,
            'ai_target_pct': ai_target_pct,
            'is_test': False,
            'lean_override': True,  # manual import (Add from Coinbase) — exempt from lean fill guard
            **cached_signals,
        }
        position = open_position(open_data)
        if not position:
            return jsonify({'error': f'Could not open position for {symbol} — duplicate check rejected it. Close the existing open position first.'}), 409
        cfg = get_execution_config()
        if (cfg.get('auto_execution_exchange') or 'coinbase').lower() == 'coinbase':
            try:
                app_module._place_sl_tp_with_retry(position)
                position = next((p for p in get_open_positions() if p['id'] == position['id']), position)
            except Exception as e:
                app_module.logger.warning(f"  ⚠ SL/TP placement for imported position failed: {e}")
                traceback.print_exc()
        return jsonify({'status': 'ok', 'position': position})
    except Exception as e:
        app_module.logger.error(f"Error opening position from exchange: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@journal_bp.route('/api/journal/add-from-exchange-clear-pending', methods=['POST'])
def journal_add_from_exchange_clear_pending():
    """
    Add a symbol to the journal from Coinbase: sync quantity and price from exchange,
    remove from pending TWAP list, open position, sync quantity to match exchange, place SL/TP.
    """
    import app as app_module
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        symbol = (data.get('symbol') or '').strip().upper()
        if not symbol:
            return jsonify({'error': 'symbol required'}), 400
        cfg = get_execution_config()
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        result = app_module.fetch_balance_sync(exchange_id)
        holdings = result.get('holdings') or {}
        quantity = float(holdings.get(symbol, 0) or 0)
        if quantity <= 0:
            return jsonify({
                'error': f'No {symbol} balance on exchange. Sync balance first or check symbol.'
            }), 400
        entry_price = app_module._price_by_symbol_from_cached().get(symbol)
        if entry_price is None or entry_price <= 0:
            try:
                from execution_engine import symbol_to_ccxt
                exchange = app_module.get_coinbase_exchange()
                symbol_ccxt = symbol_to_ccxt(symbol, prefer_usdc=True)
                if not symbol_ccxt:
                    symbol_ccxt = f"{symbol}/USD"
                ticker = exchange.fetch_ticker(symbol_ccxt)
                entry_price = float(ticker.get('last') or ticker.get('close') or 0)
            except Exception as e:
                return jsonify({
                    'error': f'Could not get price for {symbol}. Refresh dashboard cache or check exchange. ({e})'
                }), 400
        if entry_price <= 0:
            return jsonify({'error': f'Invalid price for {symbol}'}), 400
        app_module.unregister_pending_order(symbol)
        coin_id, coin_name = app_module._coin_id_and_name_for_symbol(symbol)
        if not coin_id:
            coin_id = symbol.lower()
        if not coin_name:
            coin_name = symbol
        ai_stop_pct = float(data.get('ai_stop_pct') or 0)
        ai_target_pct = float(data.get('ai_target_pct') or 0)
        if not ai_stop_pct or not ai_target_pct:
            coin_from_cache = None
            for c in (app_module.cached_data or {}).get('coins') or []:
                if ((c.get('id') or '').lower() == coin_id or
                        (c.get('symbol') or '').strip().upper() == symbol):
                    coin_from_cache = c
                    break
            if coin_from_cache:
                dt = coin_from_cache.get('dynamic_targets') or {}
                risk = coin_from_cache.get('risk') or {}
                if dt:
                    ai_stop_pct = ai_stop_pct or float(dt.get('stop_loss_pct', 0))
                    ai_target_pct = ai_target_pct or float(dt.get('take_profit_pct', 0))
                if (not ai_stop_pct or not ai_target_pct) and risk:
                    ai_stop_pct = ai_stop_pct or float(risk.get('stop_loss_pct', 0))
                    ai_target_pct = ai_target_pct or float(risk.get('take_profit_pct', 0))
            if not ai_stop_pct or not ai_target_pct:
                dyn = app_module._get_dynamic_stop_target_for_coin(coin_id, symbol, entry_price)
                if dyn:
                    ai_stop_pct = ai_stop_pct or dyn['stop_loss_pct']
                    ai_target_pct = ai_target_pct or dyn['take_profit_pct']
            ai_stop_pct = ai_stop_pct or 4.0
            ai_target_pct = ai_target_pct or 6.0
        acct_cfg = load_account_config()
        cached_signals = app_module._signal_fields_from_cached(symbol)
        open_data = {
            'coin_id': coin_id,
            'coin_name': coin_name,
            'coin_symbol': symbol,
            'signal': 'BUY',
            'entry_price': entry_price,
            'quantity': quantity,
            'capital_usd': round(entry_price * quantity, 2),
            'ai_stop_pct': ai_stop_pct,
            'ai_target_pct': ai_target_pct,
            'is_test': False,
            'lean_override': True,  # manual import (Add from Coinbase) — exempt from lean fill guard
            **cached_signals,
        }
        position = open_position(open_data)
        if not position:
            return jsonify({'error': f'Could not open position for {symbol} — duplicate check rejected it. Close the existing open position first.'}), 409
        app_module._sync_new_position_quantity_from_exchange(position, symbol)
        if (cfg.get('auto_execution_exchange') or 'coinbase').lower() == 'coinbase':
            try:
                app_module._place_sl_tp_with_retry(position)
                position = next((p for p in get_open_positions() if p['id'] == position['id']), position)
            except Exception as e:
                app_module.logger.warning(f"  ⚠ SL/TP placement for add-from-exchange failed: {e}")
                traceback.print_exc()
        return jsonify({
            'status': 'ok',
            'position': position,
            'message': f'Added {symbol} to journal (qty from Coinbase, price from market), removed from pending.',
        })
    except Exception as e:
        app_module.logger.error(f"Add from exchange clear pending error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
