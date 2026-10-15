"""
One-shot reconciliation for ETH position #91.

Usage:
    python scripts/reconcile_eth_pos91.py            # read-only inspection
    python scripts/reconcile_eth_pos91.py --execute  # perform cancellations + new stop placement

Inspection prints:
  - Coinbase ETH balance (total/free/used)
  - Open orders on ETH/USDC and ETH/USD
  - Current positions.json state for position #91

Execution flow (--execute):
  1. Cancel every open sell order for ETH that we see
  2. Wait for all cancels to report terminal state
  3. Place a single stop-limit sell at the desired stop
  4. Update positions.json with the new sl_order_id / sl_stop_price
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from execution_engine import (  # noqa: E402
    get_coinbase_exchange,
    cancel_and_wait,
    place_stop_limit_sell_order,
    fetch_balance_sync,
)

POSITIONS_FILE = PROJECT_ROOT / "positions.json"
TARGET_POSITION_ID = 91


def load_position(position_id: int) -> dict:
    data = json.loads(POSITIONS_FILE.read_text())
    for p in data:
        if p.get("id") == position_id:
            return p
    raise SystemExit(f"position #{position_id} not found")


def save_position_update(position_id: int, updates: dict) -> None:
    data = json.loads(POSITIONS_FILE.read_text())
    for p in data:
        if p.get("id") == position_id:
            p.update(updates)
            break
    else:
        raise SystemExit(f"position #{position_id} not found on save")
    POSITIONS_FILE.write_text(json.dumps(data, indent=2))


def fetch_open_sell_orders(ex, symbol: str) -> list[dict]:
    try:
        orders = ex.fetch_open_orders(symbol) or []
    except Exception as e:
        print(f"  ! fetch_open_orders({symbol}) failed: {e}")
        return []
    return [o for o in orders if (o.get("side") or "").lower() == "sell"]


def main() -> int:
    execute = "--execute" in sys.argv

    pos = load_position(TARGET_POSITION_ID)
    print(f"=== positions.json position #{TARGET_POSITION_ID} ===")
    for k in (
        "coin_symbol",
        "status",
        "quantity",
        "entry_price",
        "current_stop_price",
        "ai_stop_price",
        "sl_order_id",
        "sl_stop_price",
        "high_water_mark",
    ):
        print(f"  {k}: {pos.get(k)}")

    if pos.get("status") != "OPEN":
        print(f"\nposition is not OPEN ({pos.get('status')}), nothing to do")
        return 0

    ex = get_coinbase_exchange()

    print("\n=== Coinbase ETH balance ===")
    try:
        bal = fetch_balance_sync("coinbase")
        eth = bal.get("ETH") or {}
        print(f"  total={eth.get('total')}  free={eth.get('free')}  used={eth.get('used')}")
    except Exception as e:
        print(f"  ! balance fetch failed: {e}")

    print("\n=== Open sell orders ===")
    pairs = ["ETH/USDC", "ETH/USD"]
    all_open: list[tuple[str, dict]] = []
    for pair in pairs:
        orders = fetch_open_sell_orders(ex, pair)
        print(f"  {pair}: {len(orders)} sell order(s)")
        for o in orders:
            print(
                f"    id={o.get('id')} type={o.get('type')} "
                f"amount={o.get('amount')} price={o.get('price')} "
                f"stop={(o.get('info') or {}).get('stop_price') or (o.get('stopPrice'))} "
                f"status={o.get('status')}"
            )
            all_open.append((pair, o))

    desired_stop = float(pos.get("current_stop_price") or pos.get("ai_stop_price") or 0)
    qty = float(pos.get("quantity") or 0)
    print(f"\nIntended new stop: qty={qty} ETH @ stop=${desired_stop}")

    if not execute:
        print("\n(dry run — pass --execute to cancel orphans and place new stop)")
        return 0

    # --------- EXECUTE ---------
    print("\n=== Cancelling open sell orders ===")
    for pair, o in all_open:
        oid = o.get("id")
        if not oid:
            continue
        print(f"  cancel {pair} {oid} ...")
        result = cancel_and_wait(oid, timeout_s=3.0)
        print(f"    -> {result}")

    if desired_stop <= 0 or qty <= 0:
        print("\n! no valid desired stop or quantity — refusing to place new order")
        return 2

    print("\n=== Placing fresh stop-limit sell ===")
    result = place_stop_limit_sell_order(
        symbol="ETH",
        quantity=qty,
        stop_price=desired_stop,
    )
    print(f"  result: {result}")
    if not result.get("success"):
        print("! placement failed — leaving positions.json untouched so monitor will retry")
        return 3

    new_sl_id = result.get("order_id")
    save_position_update(
        TARGET_POSITION_ID,
        {"sl_order_id": new_sl_id, "sl_stop_price": desired_stop},
    )
    print(f"\n✓ positions.json updated: sl_order_id={new_sl_id}, sl_stop_price={desired_stop}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
