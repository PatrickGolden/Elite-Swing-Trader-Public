# Stop-Loss Gap Fix Options

**Problem:** Coinbase only supports **stop-limit** (not stop-market) for spot. When price gaps down through the stop, the order triggers but the **limit** (currently 0.5% below trigger) never fills, so the position stays open. Trade 76 (LSETH) hit this: stop $2,517, limit ~$2,504, price at $2,323 → order still "open".

**Additional bug:** When a position has `sl_order_id` set, the exit monitor **skips** the price-based stop check (`if has_limit_orders: continue` in `check_position_exits`). So the app never ran the "price below stop → market sell" path for LSETH; it only relied on the exchange order filling.

---

## Option 1 — Monitor fallback: “Below stop + SL still open” → cancel SL + market sell (recommended)

**What:** In the exit loop, when we have an SL order and we already fetch its status in `_check_sl_tp_order_status()`, add a branch: if status is **open** (not filled, not canceled) **and** `current_price < stop_price` (with optional small tolerance, e.g. 0.5%), then treat as "triggered but limit didn’t fill". Cancel the SL order, then execute a market sell and close the position (same path as `_handle_stop_loss_hit`).

**Where:**

- `app.py`: inside `_check_sl_tp_order_status()`, after we’ve determined the SL order is not filled and not canceled. For that position, get `current_price` (from the same prices dict used by the exit loop; you may need to pass it in or fetch for that coin). If `current_price < stop_price * 0.995` (or similar), call a small helper that: cancels `sl_id`, then calls `execute_market_sell(...)` and `close_position(..., exit_reason='stop_loss')`, and sends the stop-loss executed alert.
- Alternatively, in `check_position_exits()`: **do not** `continue` when `has_limit_orders` is true. Instead, when we have SL/TP and `current_price <= stop_price`, first check with exchange whether the SL order is already filled (we could call `fetch_order_status(sl_id)`). If filled, rely on existing logic; if **open**, cancel SL and run the same market-sell + close flow as `_handle_stop_loss_hit`.

**Pros:**

- Fixes gap-downs without changing order type; works with Coinbase’s stop-limit-only API.
- No change to how orders are placed; only adds a safety net when the limit doesn’t fill.

**Cons:**

- Slightly more logic and one extra exchange call (status or cancel) when we fall back.
- Need to avoid double-sell: cancel before market sell, and ensure we don’t trigger both “filled” and “fallback” in the same cycle (e.g. only run fallback when status is open).

---

## Option 2 — Wider limit below stop (e.g. 2–5%)

**What:** In `execution_engine.py`, change the default limit from 0.5% below stop to a wider band, e.g. `stop_price * 0.98` (2%) or `stop_price * 0.95` (5%).

**Where:** `execution_engine.py` line 505:

```python
# Current:
limit_price = limit_price if limit_price is not None and limit_price > 0 else round(stop_price * 0.995, 8)
# Example 2%:
limit_price = limit_price if limit_price is not None and limit_price > 0 else round(stop_price * 0.98, 8)
```

**Pros:** One-line change; reduces chance of missing fill in small gaps.

**Cons:** In a big gap you can get a worse fill (e.g. 5% below stop). Does not fix cases where price blows through even the wider limit. Does not fix the fact that we currently skip the price-based path when `sl_order_id` is set.

---

## Option 3 — Always run price-based stop check; if below stop and we have SL, cancel SL then market sell

**What:** Remove the “if we have limit orders, skip price check” behavior. For every open position, if `current_price <= stop_price`, run the same logic as today’s `_handle_stop_loss_hit` **but** when the position has `sl_order_id`: first cancel the SL order on the exchange, then execute market sell and close. That way we never rely solely on the exchange fill when price is already below stop.

**Where:** `app.py` `check_position_exits()`. Remove or relax the `if has_limit_orders: continue` so that when `current_price <= stop_price` we always call a new helper, e.g. `_handle_stop_loss_hit_or_fallback(position, current_price, auto_exec)`, which:

1. If `position.get('sl_order_id')`: cancel that order (ignore errors if already filled/canceled), then clear `sl_order_id` in journal (or leave for next status check).
2. Call the same execute_market_sell + close_position + alert flow as `_handle_stop_loss_hit`.

**Pros:** Single code path for “price below stop”; guarantees we attempt exit when our price feed says we’re below stop.

**Cons:** Every 30s we might cancel and replace behavior; need to be careful not to double-sell (cancel first, then one market sell). Slightly more coordination with `_check_sl_tp_order_status()` so we don’t double-close (e.g. if status is filled in the same cycle).

---

## Option 4 — Hybrid: wider limit + monitor fallback

**What:** Do both Option 2 and Option 1: use a wider default limit (e.g. 2%) to reduce how often the limit is missed, and add the “below stop + SL open → cancel + market sell” fallback so that when it still happens we still exit.

**Pros:** Best protection: fewer fallbacks in small gaps, and a safety net for big gaps.

**Cons:** Slightly worse fill when the wider limit is used and the market is between old 0.5% and new 2–5%.

---

## Recommendation

1. **Implement Option 1 (monitor fallback)** so that when the exchange SL is still open and price is below stop, we cancel the SL and market sell. That fixes the LSETH-style case and doesn’t require Coinbase to support stop-market.
2. Optionally add **Option 2** with a modest band (e.g. 1–2%) to reduce how often the fallback is needed.
3. **Manual follow-up:** For trade 76 (LSETH), close the position manually (market sell), cancel order `ecead7cf-6c1c-4875-8114-2897ee783e98` on Coinbase if still open, and update the journal (close with exit_reason e.g. `stop_loss` and a note that the exchange SL did not fill).

---

## Code references


| Item                               | Location                                                   |
| ---------------------------------- | ---------------------------------------------------------- |
| Exit loop, “has limit orders” skip | `app.py` ~1005–1017 (`check_position_exits`)               |
| SL/TP status check                 | `app.py` `_check_sl_tp_order_status()` ~810–900            |
| Stop-limit placement (0.995)       | `execution_engine.py` ~505 (`place_stop_limit_sell_order`) |
| Market sell + close on stop        | `app.py` `_handle_stop_loss_hit()` ~1047–1098              |
| Cancel order                       | `execution_engine.py` `cancel_order()`                     |


