"""Compare Coinbase spot holdings vs trade journal open positions (non-test).

Does not import app.py (avoids Flask startup). Requires: pip install ccxt

Write alignment: use POST /api/account/sync-position-quantities or
scripts/sync_journal_from_coinbase.py with the dashboard env loaded.

Usage:
  PYTHONUTF8=1 python scripts/audit_coinbase_journal.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _price_by_symbol_from_disk():
    """Mirror app._price_by_symbol_from_cached using data/coins_cache.json."""
    path = os.path.join(ROOT, "data", "coins_cache.json")
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return out
    coins = data.get("coins") if isinstance(data, dict) else None
    if not isinstance(coins, list):
        return out
    for c in coins:
        if not isinstance(c, dict):
            continue
        sym = (c.get("symbol") or "").strip().upper()
        price = c.get("current_price")
        if sym and price is not None:
            try:
                out[sym] = float(price)
            except (TypeError, ValueError):
                pass
    return out


def main():
    from execution_engine import fetch_balance_sync
    from trade_journal import get_open_positions
    from app_config import load_account_config

    cfg = load_account_config()
    dust = float(cfg.get("dust_threshold_usd", 1.0) or 1.0)

    try:
        result = fetch_balance_sync("coinbase")
    except Exception as e:
        print("FETCH_BALANCE_FAILED:", type(e).__name__, e)
        return 1

    ex_holdings = {}
    for k, v in (result.get("holdings") or {}).items():
        cur = (k or "").strip().upper()
        if cur:
            ex_holdings[cur] = round(ex_holdings.get(cur, 0) + float(v or 0), 8)

    open_pos = get_open_positions(exclude_test=True)
    journal = {}
    for p in open_pos:
        sym = (p.get("coin_symbol") or "").strip().upper()
        if not sym:
            continue
        journal[sym] = round(journal.get(sym, 0) + float(p.get("quantity", 0) or 0), 8)

    prices = _price_by_symbol_from_disk()
    if not prices:
        print(
            "NOTE: data/coins_cache.json missing or empty — dust filter may treat unknown-priced assets as discrepancies."
        )

    def value_usd(sym, qty):
        p = prices.get((sym or "").strip().upper())
        if p is None:
            return None
        return float(qty) * float(p)

    def qty_matched(jq, eq):
        if jq <= 0 and eq <= 0:
            return True
        if jq <= 0 or eq <= 0:
            return False
        return abs(float(jq) - float(eq)) < 1e-8

    external, in_journal_only, qty_mismatch = [], [], []
    for sym, qty in ex_holdings.items():
        if qty <= 0:
            continue
        jq = journal.get(sym, 0)
        if jq <= 0:
            v = value_usd(sym, qty)
            if v is not None and v < dust:
                continue
            if v is None:
                continue
            external.append({"symbol": sym, "exchange_quantity": qty})
        elif not qty_matched(jq, qty):
            delta_usd = value_usd(sym, abs(jq - qty))
            if delta_usd is not None and delta_usd >= dust:
                qty_mismatch.append({"symbol": sym, "journal": jq, "exchange": qty})

    for sym, qty in journal.items():
        if qty <= 0:
            continue
        eq = ex_holdings.get(sym, 0)
        if eq <= 0:
            v = value_usd(sym, qty)
            if v is not None and v < dust:
                continue
            if v is None:
                continue
            in_journal_only.append({"symbol": sym, "journal_quantity": qty})
        elif not qty_matched(qty, eq) and not any(m["symbol"] == sym for m in qty_mismatch):
            delta_usd = value_usd(sym, abs(qty - eq))
            if delta_usd is not None and delta_usd >= dust:
                qty_mismatch.append({"symbol": sym, "journal": qty, "exchange": eq})

    print("=== COINBASE vs TRADE JOURNAL (live) ===")
    print("total_cash (USD+stable):", result.get("total_cash"))
    print("exchange spot holdings (non-cash):", json.dumps(ex_holdings, indent=2))
    print("journal aggregates (open, non-test):", json.dumps(journal, indent=2))
    print("open position rows:", len(open_pos))
    for p in open_pos:
        print(
            "  id=%s %s qty=%s status=%s"
            % (p.get("id"), p.get("coin_symbol"), p.get("quantity"), p.get("status"))
        )
    print()
    print("dust_threshold_usd:", dust)
    print("external (on Coinbase, not in journal):", json.dumps(external, indent=2))
    print("in_journal_not_on_exchange:", json.dumps(in_journal_only, indent=2))
    print("quantity_mismatch:", json.dumps(qty_mismatch, indent=2))
    status = "clean" if not (external or in_journal_only or qty_mismatch) else "discrepancy_detected"
    print("status:", status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
