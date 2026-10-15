"""
Align trade journal open positions with Coinbase spot balances (exchange = source of truth).

Same behavior as POST /api/account/sync-position-quantities (routes/account_bp.py):
  - For each asset on the exchange: keep lowest position id for that symbol, set qty to exchange total, zero duplicates.
  - For symbols in the journal with no (or dust) balance on the exchange: cancel SL/TP if any, close as exchange_sold.

Requires working COINBASE credentials in the environment (same as the dashboard).

Usage:
  PYTHONUTF8=1 python scripts/sync_journal_from_coinbase.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main() -> int:
    from app_config import get_execution_config, load_account_config
    from trade_journal import close_position, get_open_positions, update_position

    cfg_exec = get_execution_config()
    exchange_id = (cfg_exec.get("auto_execution_exchange") or "coinbase").lower()

    import app as app_module

    result = app_module.fetch_balance_sync(exchange_id)
    exchange_holdings = result.get("holdings") or {}
    _ex: dict[str, float] = {}
    for k, v in exchange_holdings.items():
        cur = (k or "").strip().upper()
        if cur:
            _ex[cur] = _ex.get(cur, 0) + float(v or 0)
    exchange_holdings = _ex

    acct_cfg = load_account_config()
    dust_threshold = float(acct_cfg.get("dust_threshold_usd", 1.0) or 1.0)
    price_by_symbol = app_module._price_by_symbol_from_cached()

    open_positions = get_open_positions(exclude_test=True)
    by_symbol: dict[str, list[tuple[int, float]]] = {}
    for p in open_positions:
        sym = (p.get("coin_symbol") or "").strip().upper()
        if not sym:
            continue
        pid = p.get("id")
        qty = float(p.get("quantity", 0) or 0)
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append((pid, qty))
    for sym in by_symbol:
        by_symbol[sym].sort(key=lambda x: x[0])

    def qty_matched(jq: float, eq: float) -> bool:
        if jq <= 0 and eq <= 0:
            return True
        if jq <= 0 or eq <= 0:
            return False
        jq, eq = float(jq), float(eq)
        return abs(jq - eq) < 1e-8

    def value_usd(sym: str, qty: float):
        p = price_by_symbol.get((sym or "").strip().upper())
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
                updated.append(
                    {
                        "position_id": first_id,
                        "symbol": sym,
                        "old_quantity": round(first_jq, 8),
                        "new_quantity": round(eq, 8),
                    }
                )
                for p in open_positions:
                    if p.get("id") == first_id:
                        p["quantity"] = eq
                        break
        for position_id, jq in rest:
            if jq != 0:
                pos, err = update_position(position_id, quantity=0)
                if err:
                    errors.append({"symbol": sym, "position_id": position_id, "error": err})
                else:
                    updated.append(
                        {
                            "position_id": position_id,
                            "symbol": sym,
                            "old_quantity": round(jq, 8),
                            "new_quantity": 0,
                        }
                    )
                    for p in open_positions:
                        if p.get("id") == position_id:
                            p["quantity"] = 0
                            break

    for sym, positions_for_sym in by_symbol.items():
        eq = float(exchange_holdings.get(sym, 0) or 0)
        val = value_usd(sym, eq)
        if eq > 0 and val is not None and val >= dust_threshold:
            continue
        for position_id, jq in positions_for_sym:
            if jq != 0:
                pos_obj = next((p for p in open_positions if p.get("id") == position_id), None)
                if pos_obj and (pos_obj.get("sl_order_id") or pos_obj.get("tp_order_id")):
                    try:
                        if pos_obj.get("sl_order_id"):
                            app_module.cancel_order(pos_obj["sl_order_id"])
                        if pos_obj.get("tp_order_id"):
                            app_module.cancel_order(pos_obj["tp_order_id"])
                        update_position(position_id, {"sl_order_id": None, "tp_order_id": None})
                        pos_obj["sl_order_id"] = None
                        pos_obj["tp_order_id"] = None
                    except Exception as e:
                        app_module.logger.warning("  Cancel SL/TP for closed %s: %s", sym, e)
                exit_price_fallback = (price_by_symbol.get(sym) if price_by_symbol else None) or (
                    float(pos_obj.get("entry_price", 0) or 0) if pos_obj else 0
                )
                if exit_price_fallback <= 0 and pos_obj:
                    exit_price_fallback = float(pos_obj.get("entry_price", 0) or 0)
                if exit_price_fallback <= 0:
                    exit_price_fallback = 0.01
                closed, err = close_position(
                    position_id,
                    exit_price=exit_price_fallback,
                    exit_reason="exchange_sold",
                    notes="Sync script: exchange balance 0 or dust; position closed.",
                )
                if err:
                    errors.append({"symbol": sym, "position_id": position_id, "error": err})
                else:
                    updated.append(
                        {
                            "position_id": position_id,
                            "symbol": sym,
                            "old_quantity": round(jq, 8),
                            "new_quantity": 0,
                            "closed": True,
                        }
                    )
                    for p in open_positions:
                        if p.get("id") == position_id:
                            p["quantity"] = 0
                            p["status"] = "CLOSED"
                            break

    if updated and exchange_id == "coinbase":
        open_positions = get_open_positions(exclude_test=True)
        for u in updated:
            if u.get("new_quantity", 0) <= 0:
                continue
            pos = next((p for p in open_positions if p.get("id") == u["position_id"]), None)
            if not pos:
                continue
            sl_id = pos.get("sl_order_id")
            tp_id = pos.get("tp_order_id")
            if sl_id or tp_id:
                try:
                    if sl_id:
                        app_module.cancel_order(sl_id)
                    if tp_id:
                        app_module.cancel_order(tp_id)
                    update_position(pos["id"], {"sl_order_id": None, "tp_order_id": None})
                    pos["sl_order_id"] = None
                    pos["tp_order_id"] = None
                    pos["quantity"] = u["new_quantity"]
                    app_module._place_sl_tp_with_retry(pos)
                    app_module.logger.info(
                        "  Re-placed SL/TP for %s after qty sync (%s -> %s)",
                        u["symbol"],
                        u["old_quantity"],
                        u["new_quantity"],
                    )
                except Exception as e:
                    app_module.logger.warning("  Failed to re-place SL/TP for %s: %s", u["symbol"], e)

    app_module._reconcile_cache["data"] = None
    app_module._append_reconciliation_history(
        {
            "source": "sync_journal_from_coinbase_script",
            "status": "clean" if not errors else "errors",
            "summary": {"updated": len(updated), "skipped": len(skipped), "errors": len(errors)},
        }
    )

    out = {"success": len(errors) == 0, "updated": updated, "skipped": skipped, "errors": errors}
    print(json.dumps(out, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
