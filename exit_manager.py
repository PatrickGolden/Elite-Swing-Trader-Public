# exit_manager.py — Automatic trailing stop execution on Coinbase
# Monitors open positions for trailing stop hits; small positions → single market sell, else TWAP (same rule as buys).
# Safety: auto_execution_enabled. Sells are never blocked by risk caps. Emergency stop: disable exchange API.

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular deps
def _get_open_positions():
    from trade_journal import get_open_positions
    return get_open_positions(exclude_test=True)

def _get_current_prices(coin_ids: list) -> Dict[str, float]:
    """Fetch prices with Coinbase spot overlay for live positions."""
    from app import fetcher
    prices = fetcher.get_current_prices_fast(coin_ids) or {}
    positions = _get_open_positions()
    sym_to_cid = {}
    live_syms = []
    for p in positions:
        s = (p.get('coin_symbol') or '').upper()
        cid = p.get('coin_id', '')
        if s and cid and cid in prices:
            sym_to_cid[s] = cid
            live_syms.append(s)
    if live_syms:
        try:
            cb = fetcher.get_coinbase_spot_prices(list(set(live_syms)))
            for sym, spot in cb.items():
                cid = sym_to_cid.get(sym)
                if cid and spot and spot > 0:
                    prices[cid] = spot
        except Exception:
            pass
    return prices

def _get_execution_config():
    from app_config import get_execution_config
    return get_execution_config()

def _execute_market_sell_twap(
    symbol: str,
    quantity: float,
    num_slices: int = 5,
    coin_name: str = "",
    position_usd: Optional[float] = None,
    twap_min_usd: Optional[float] = None,
    position_id: Optional[int] = None,
    trade_id: Optional[str] = None,
):
    """Single market sell if position_usd < twap_min_usd (same rule as buys), else TWAP sell."""
    from execution_engine import execute_market_sell_twap
    return execute_market_sell_twap(
        symbol=symbol,
        quantity=quantity,
        num_slices=num_slices,
        coin_name=coin_name,
        position_usd=position_usd,
        twap_min_usd=twap_min_usd,
        position_id=position_id,
        trade_id=trade_id,
    )


def _cancel_order(order_id: str):
    from execution_engine import cancel_order
    return cancel_order(order_id)

def _close_position(position_id, exit_price, exit_reason, notes="", exit_time=None, exit_fee_usd=None, quantity_at_close=None):
    from trade_journal import close_position
    return close_position(
        position_id,
        exit_price=exit_price,
        exit_reason=exit_reason,
        notes=notes,
        exit_time=exit_time,
        exit_fee_usd=exit_fee_usd,
        quantity_at_close=quantity_at_close,
    )

def _update_position(position_id, updates):
    from trade_journal import update_position
    return update_position(position_id, updates)

def _get_alerts():
    from app import alerts
    return alerts


def _should_alert(position_id: int, alert_type: str) -> bool:
    """Check if we should alert (duplicate prevention)."""
    try:
        from app import _should_alert
        return _should_alert(position_id, alert_type)
    except Exception:
        return True


def _check_breakeven_stops(position: Dict[str, Any], current_price: float,
                           entry_price: float, profit_pct: float) -> None:
    """
    When profit >= BREAKEVEN_STOP_TRIGGER_PCT but < TRAILING_STOP_MIN_PROFIT_PCT,
    tighten current_stop_pct toward breakeven — but never below the regime floor.
    Only tightens — never widens an existing stop.

    Intentionally inert under current config: BREAKEVEN_STOP_TRIGGER_PCT == TRAILING_STOP_MIN_PROFIT_PCT
    (both 8) so the breakeven band is empty. Kept for config-driven re-enable without code changes.
    """
    from thresholds import get_threshold
    trigger = get_threshold('BREAKEVEN_STOP_TRIGGER_PCT', 8)
    buffer = get_threshold('BREAKEVEN_STOP_BUFFER_PCT', 0.3)
    min_profit = get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8)

    if profit_pct < trigger or profit_pct >= min_profit:
        return  # Not in breakeven zone, or already in trailing zone

    current_stop = float(position.get('current_stop_pct') or position.get('ai_stop_pct') or 99)

    # Clamp to regime floor — preserve the noise-room that was set at entry
    regime = position.get('market_regime') or 'SIDEWAYS'
    from dynamic_targets import get_regime_stop_floor
    new_stop = max(buffer, get_regime_stop_floor(regime))

    if new_stop >= current_stop:
        return  # Already at or tighter than what we'd set

    _update_position(position.get('id'), {
        'current_stop_pct': round(new_stop, 2),
        'current_stop_price': round(entry_price * (1 - new_stop / 100), 8),
    })
    logger.info(
        "Breakeven stop set for #%s (%s): profit %.1f%% >= %.1f%%, stop %.1f%% → %.1f%% (regime=%s floor)",
        position.get('id'), position.get('coin_symbol', ''), profit_pct, trigger,
        current_stop, new_stop, regime,
    )

    try:
        from execution_engine import _log_audit
        _log_audit({
            "action": "breakeven_stop_set",
            "position_id": position.get('id'),
            "symbol": position.get('coin_symbol', ''),
            "profit_pct": round(profit_pct, 2),
            "new_stop_pct": round(new_stop, 2),
            "regime": regime,
            "regime_floor_applied": new_stop > buffer,
        })
    except Exception:
        pass


def check_trailing_stops(current_prices: Optional[Dict[str, float]] = None) -> None:
    """
    Monitor open positions for trailing stop hits.
    When hit and auto_execution enabled: execute via TWAP sell, close journal, send confirmation.
    Safety: auto_execution_enabled. Emergency stop: disable exchange API.
    current_prices: optional {coin_id: price} to avoid re-fetching; if None, fetches.
    """
    cfg = _get_execution_config()
    if not cfg.get('auto_execution_enabled', True):
        return

    # LEAN EXIT (Stage 1c): no trailing/breakeven under lean — a trail tight enough to
    # protect clips the right-tail winners. Exits are governed by the wide fixed stop
    # (dynamic_targets) + the lean time-exit (check_stale_positions).
    from thresholds import get_threshold
    if float(get_threshold('LEAN_ENTRY_MODE', 0.0) or 0.0) >= 1.0:
        return

    exchange = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
    if exchange != 'coinbase':
        return

    open_positions = _get_open_positions()
    if not open_positions:
        return

    # Dedupe by position id (avoid duplicate breakeven/trailing logs if journal had duplicate rows)
    seen_ids = set()
    open_positions = [p for p in open_positions if p.get('id') not in seen_ids and not seen_ids.add(p.get('id'))]

    coin_ids = list(set(p.get('coin_id') for p in open_positions if p.get('coin_id')))
    if not coin_ids:
        return

    if current_prices is None:
        current_prices = _get_current_prices(coin_ids)
    if not current_prices:
        return

    for position in open_positions:
        if position.get('is_test') is True:
            continue
        coin_id = position.get('coin_id')
        if not coin_id:
            continue
        current_price = current_prices.get(coin_id)
        if not current_price or current_price <= 0:
            continue

        entry_price = float(position.get('entry_price', 0))
        if entry_price <= 0:
            continue

        _price_ratio = current_price / entry_price
        if _price_ratio > 20 or _price_ratio < 0.02:
            logger.warning(
                f"  Trailing stop price sanity reject {position.get('coin_symbol')}: "
                f"entry=${entry_price} current=${current_price} ratio={_price_ratio:.2f}x"
            )
            continue

        profit_pct = ((current_price - entry_price) / entry_price) * 100

        # Breakeven stop: tighten stop to near-entry when profitable but below trailing zone
        _check_breakeven_stops(position, current_price, entry_price, profit_pct)

        from thresholds import get_threshold
        min_profit = get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8)
        if profit_pct < min_profit:
            continue

        high_water = float(position.get('high_water_mark') or entry_price)
        if current_price > high_water:
            prev_high = high_water
            high_water = current_price
            _update_position(position.get('id'), {'high_water_mark': high_water})
            # Audit high-water mark updates for forensic trail
            try:
                from execution_engine import _log_audit
                _log_audit({
                    "action": "trailing_high_water_update",
                    "position_id": position.get('id'),
                    "symbol": position.get('coin_symbol', ''),
                    "prev_high_water": round(prev_high, 8),
                    "new_high_water": round(high_water, 8),
                    "profit_pct": round(profit_pct, 2),
                })
            except Exception:
                pass

        trail_pct = get_threshold('TRAILING_STOP_TRAIL_PCT', 13)
        trailing_stop = high_water * (1 - trail_pct / 100)  # trail below high-water (from thresholds.py)
        if current_price > trailing_stop:
            continue

        if not _should_alert(position.get('id'), 'trailing'):
            continue

        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        quantity = float(position.get('quantity', 0) or 0)
        position_id = position.get('id')
        coin_name = position.get('coin_name', coin_id)

        if quantity <= 0:
            # Sync quantity from exchange (e.g. journal was zeroed by sync but exchange still had balance)
            try:
                from execution_engine import fetch_balance_sync
                result = fetch_balance_sync(exchange)
                holdings = result.get('holdings') or {}
                eq = 0.0
                sym_upper = (symbol or '').strip().upper()
                for k, v in holdings.items():
                    if (k or '').strip().upper() == sym_upper:
                        eq += float(v or 0)
                if eq > 0:
                    _update_position(position_id, {'quantity': round(eq, 8), 'capital_usd': round(float(position.get('entry_price', 0) or 0) * eq, 2)})
                    quantity = eq
                    position['quantity'] = eq
                else:
                    # Exchange has 0 — position already sold on exchange; close journal
                    closed, err = _close_position(
                        position_id,
                        exit_price=current_price,
                        exit_reason='exchange_sold',
                        notes='Closed from exchange (balance 0); exit price from current market.',
                        quantity_at_close=0,
                    )
                    if not err:
                        logger.info(f"Closed position #{position_id} ({symbol}) from exchange — balance 0 (trailing had been triggered).")
                    continue
            except Exception as e:
                logger.warning(f"Trailing stop: could not sync qty from exchange for {symbol}: {e}")
            if quantity <= 0:
                continue

        # Hybrid SL/TP: cancel TP and SL limit orders before TWAP sell (avoid double-sell)
        # IMPORTANT: verify cancel success before proceeding to prevent double-sell
        tp_id = position.get('tp_order_id')
        sl_id = position.get('sl_order_id')
        cancel_failed = False
        if tp_id:
            cancel_result = _cancel_order(tp_id)
            if cancel_result.get('success'):
                logger.info(f"Cancelled TP order {tp_id} (trailing stop)")
            else:
                # Check if already filled — if TP already filled, position already sold
                from execution_engine import fetch_order_status
                status = fetch_order_status(tp_id)
                if status == 'filled':
                    logger.warning(f"TP order {tp_id} already FILLED — skipping TWAP sell to avoid double-sell")
                    cancel_failed = True
                elif status != 'open':
                    # Unknown/error status — fail safe to prevent double-sell
                    logger.warning(f"TP order {tp_id} cancel failed, status unknown ({status}) — skipping to prevent double-sell")
                    cancel_failed = True
                else:
                    logger.warning(f"Cancel TP order {tp_id} failed (status={status}): {cancel_result.get('error')}")
        if sl_id:
            cancel_result = _cancel_order(sl_id)
            if cancel_result.get('success'):
                logger.info(f"Cancelled SL order {sl_id} (trailing stop)")
            else:
                from execution_engine import fetch_order_status
                status = fetch_order_status(sl_id)
                if status == 'filled':
                    logger.warning(f"SL order {sl_id} already FILLED — skipping TWAP sell to avoid double-sell")
                    cancel_failed = True
                elif status != 'open':
                    logger.warning(f"SL order {sl_id} cancel failed, status unknown ({status}) — skipping to prevent double-sell")
                    cancel_failed = True
                else:
                    logger.warning(f"Cancel SL order {sl_id} failed (status={status}): {cancel_result.get('error')}")

        if cancel_failed:
            _get_alerts().send_critical_alert(
                f"Trailing Stop SKIPPED – {symbol}",
                f"{symbol} trailing stop hit but TP/SL order already filled on exchange. Check position #{position_id} manually.",
            )
            continue

        position_usd = quantity * current_price
        cfg = _get_execution_config()
        twap_min_usd = cfg.get('twap_min_usd') if isinstance(cfg.get('twap_min_usd'), (int, float)) else 500.0
        if twap_min_usd is None:
            twap_min_usd = 500.0

        try:
            result = _execute_market_sell_twap(
                symbol=symbol,
                quantity=quantity,
                num_slices=5,
                coin_name=coin_name,
                position_usd=position_usd,
                twap_min_usd=twap_min_usd,
                position_id=position_id,
                trade_id=position.get('trade_id'),
            )
        except Exception as e:
            _get_alerts().send_critical_alert(
                f"Trailing Stop FAILED – {symbol}",
                f"{symbol} trailing stop hit at ${current_price:.8f} but sell failed: {e}",
            )
            continue

        if not result.get('success'):
            err_msg = result.get('error', '') or ''
            _get_alerts().send_critical_alert(
                f"Trailing Stop FAILED – {symbol}",
                f"{symbol} trailing stop hit at ${current_price:.8f} but sell failed: {err_msg}",
            )
            # If INSUFFICIENT_FUND, position may already be sold on exchange; close journal if balance 0
            if 'INSUFFICIENT_FUND' in str(err_msg).upper():
                try:
                    from execution_engine import fetch_balance_sync
                    bal = fetch_balance_sync(exchange)
                    holdings = bal.get('holdings') or {}
                    eq = 0.0
                    sym_upper = (symbol or '').strip().upper()
                    for k, v in holdings.items():
                        if (k or '').strip().upper() == sym_upper:
                            eq += float(v or 0)
                    if eq <= 0:
                        closed, close_err = _close_position(
                            position_id,
                            exit_price=current_price,
                            exit_reason='exchange_sold',
                            notes='Trailing sell failed (INSUFFICIENT_FUND); exchange balance 0 — closed from exchange state.',
                            quantity_at_close=quantity,
                        )
                        if not close_err:
                            logger.info(f"Closed position #{position_id} ({symbol}) after INSUFFICIENT_FUND — exchange balance 0.")
                except Exception as e:
                    logger.warning(f"Trailing stop: could not check balance after INSUFFICIENT_FUND for {symbol}: {e}")
            continue

        fill_price = result.get('fill_price') or result.get('average_price', current_price)
        exit_fee = result.get('total_fee_usd') or result.get('fee_usd') or 0.0
        sell_mode = result.get('mode', 'twap')
        execution_note = "market sell" if sell_mode == "market" else "TWAP"
        closed, err = _close_position(
            position_id,
            exit_price=fill_price,
            exit_reason='trailing_stop',
            notes=f"Auto-executed {execution_note} at ${fill_price:.8f}",
            exit_fee_usd=float(exit_fee),
        )
        if err:
            _get_alerts().send_critical_alert(
                f"Trailing Stop – Journal Error {symbol}",
                f"{symbol} sold at ${fill_price:.8f} but close_position failed: {err}",
            )
            continue

        extras = _get_alerts()._format_sell_confirmation_extras(position, fill_price, closed=closed, exit_fee_usd=float(exit_fee))
        _get_alerts().send_critical_alert(
            f"Trailing Stop Executed – {symbol}",
            f"{symbol} trailing stop auto-executed via {execution_note} at ${fill_price:.8f}. Position #{position_id} closed. Peak was ${high_water:.8f}.\n\n{extras}",
            alert_type='trailing_stop',
        )


def check_stale_positions(current_prices: Optional[Dict[str, float]] = None) -> None:
    """
    Auto-exit positions held longer than STALE_POSITION_MAX_DAYS.
    Sells at market regardless of P&L to free capital from dead trades.
    """
    cfg = _get_execution_config()
    if not cfg.get('auto_execution_enabled', True):
        return

    from thresholds import get_threshold
    _lean = float(get_threshold('LEAN_ENTRY_MODE', 0.0) or 0.0) >= 1.0
    # LEAN EXIT (Stage 1c): the time-exit is a validated pillar of the lean edge
    # (wide fixed stop + no TP + 30d hold), NOT optional cleanup — enforce it
    # regardless of the dashboard "Time exit" toggle. Off-lean it stays user-toggleable.
    if not _lean and not cfg.get('stale_position_rule_enabled', True):
        return

    exchange = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
    if exchange != 'coinbase':
        return

    open_positions = _get_open_positions()
    if not open_positions:
        return

    # Lean uses the longer research-validated hold (~30d); legacy uses the 21d stale rule.
    max_days = get_threshold('LEAN_MAX_HOLD_DAYS', 30) if _lean else get_threshold('STALE_POSITION_MAX_DAYS', 21)

    now = datetime.now(timezone.utc)

    for position in open_positions:
        if position.get('is_test') is True:
            continue

        entry_time_str = position.get('entry_time') or ''
        if not entry_time_str:
            continue

        try:
            entry_time = datetime.strptime(entry_time_str[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        days_held = (now - entry_time).total_seconds() / 86400
        if days_held < max_days:
            continue

        coin_id = position.get('coin_id')
        if not coin_id:
            continue

        if current_prices is None:
            coin_ids = list(set(p.get('coin_id') for p in open_positions if p.get('coin_id')))
            current_prices = _get_current_prices(coin_ids)
        if not current_prices:
            return

        current_price = current_prices.get(coin_id)
        if not current_price or current_price <= 0:
            continue

        entry_price = float(position.get('entry_price', 0))
        if entry_price <= 0:
            continue

        _price_ratio = current_price / entry_price
        if _price_ratio > 20 or _price_ratio < 0.02:
            logger.warning(
                f"  Stale position price sanity reject {position.get('coin_symbol')}: "
                f"entry=${entry_price} current=${current_price} ratio={_price_ratio:.2f}x"
            )
            continue

        profit_pct = ((current_price - entry_price) / entry_price) * 100
        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        quantity = float(position.get('quantity', 0) or 0)
        position_id = position.get('id')
        coin_name = position.get('coin_name', coin_id)

        if not _should_alert(position_id, 'stale_exit'):
            continue

        logger.info("Stale position #%s (%s): held %.1f days (max %d), profit %.1f%% — auto-exiting",
                     position_id, symbol, days_held, max_days, profit_pct)

        if quantity <= 0:
            continue

        # Cancel existing SL/TP limit orders to avoid double-sell
        tp_id = position.get('tp_order_id')
        sl_id = position.get('sl_order_id')
        cancel_failed = False
        if tp_id:
            cancel_result = _cancel_order(tp_id)
            if cancel_result.get('success'):
                logger.info(f"Cancelled TP order {tp_id} (stale exit)")
            else:
                from execution_engine import fetch_order_status
                status = fetch_order_status(tp_id)
                if status == 'filled':
                    logger.warning(f"TP order {tp_id} already FILLED — skipping stale exit to avoid double-sell")
                    cancel_failed = True
                elif status != 'open':
                    logger.warning(f"TP order {tp_id} cancel failed, status unknown ({status}) — skipping to prevent double-sell")
                    cancel_failed = True
                else:
                    logger.warning(f"Cancel TP order {tp_id} failed (status={status}): {cancel_result.get('error')}")
        if sl_id:
            cancel_result = _cancel_order(sl_id)
            if cancel_result.get('success'):
                logger.info(f"Cancelled SL order {sl_id} (stale exit)")
            else:
                from execution_engine import fetch_order_status
                status = fetch_order_status(sl_id)
                if status == 'filled':
                    logger.warning(f"SL order {sl_id} already FILLED — skipping stale exit to avoid double-sell")
                    cancel_failed = True
                elif status != 'open':
                    logger.warning(f"SL order {sl_id} cancel failed, status unknown ({status}) — skipping to prevent double-sell")
                    cancel_failed = True
                else:
                    logger.warning(f"Cancel SL order {sl_id} failed (status={status}): {cancel_result.get('error')}")

        if cancel_failed:
            _get_alerts().send_critical_alert(
                f"Stale Exit SKIPPED – {symbol}",
                f"{symbol} stale exit ({days_held:.0f}d) but TP/SL already filled. Check position #{position_id} manually.",
            )
            continue

        position_usd = quantity * current_price
        twap_min_usd = cfg.get('twap_min_usd') if isinstance(cfg.get('twap_min_usd'), (int, float)) else 500.0
        if twap_min_usd is None:
            twap_min_usd = 500.0

        try:
            result = _execute_market_sell_twap(
                symbol=symbol,
                quantity=quantity,
                num_slices=5,
                coin_name=coin_name,
                position_usd=position_usd,
                twap_min_usd=twap_min_usd,
                position_id=position_id,
                trade_id=position.get('trade_id'),
            )
        except Exception as e:
            _get_alerts().send_critical_alert(
                f"Stale Exit FAILED – {symbol}",
                f"{symbol} stale exit ({days_held:.0f}d held) sell failed: {e}",
            )
            continue

        if not result.get('success'):
            err_msg = result.get('error', '') or ''
            _get_alerts().send_critical_alert(
                f"Stale Exit FAILED – {symbol}",
                f"{symbol} stale exit ({days_held:.0f}d held) sell failed: {err_msg}",
            )
            continue

        fill_price = result.get('fill_price') or result.get('average_price', current_price)
        exit_fee = result.get('total_fee_usd') or result.get('fee_usd') or 0.0
        sell_mode = result.get('mode', 'twap')
        execution_note = "market sell" if sell_mode == "market" else "TWAP"
        closed, err = _close_position(
            position_id,
            exit_price=fill_price,
            exit_reason='time',
            notes=f"Stale exit ({days_held:.0f}d held, P&L {profit_pct:+.1f}%) — auto-executed {execution_note} at ${fill_price:.8f}",
            exit_fee_usd=float(exit_fee),
        )
        if err:
            _get_alerts().send_critical_alert(
                f"Stale Exit – Journal Error {symbol}",
                f"{symbol} sold at ${fill_price:.8f} but close_position failed: {err}",
            )
            continue

        extras = _get_alerts()._format_sell_confirmation_extras(position, fill_price, closed=closed, exit_fee_usd=float(exit_fee))
        _get_alerts().send_critical_alert(
            f"Stale Exit Executed – {symbol}",
            f"{symbol} auto-exited after {days_held:.0f} days via {execution_note} at ${fill_price:.8f}. Position #{position_id} closed.\n\n{extras}",
            alert_type='stop_loss',
        )
