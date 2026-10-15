---
description: Price data integrity rules — any code that fetches, caches, or acts on price data in trade-execution paths must follow these mandatory safeguards
globs: ["app.py", "enhanced_data_fetcher.py", "exit_manager.py", "execution_engine.py", "position_sizer.py"]
alwaysApply: false
---

# Price Data Integrity Rules

## Background

On 2026-04-11 a CoinPaprika symbol collision (multiple tokens sharing "PEPE") fed a $7.64 price into the paper trade monitor instead of $0.000004, producing a phantom $406M P&L. The same price function feeds live exit monitoring. This rule exists to prevent any recurrence.

## Mandatory Safeguards for Price-Dependent Code

### 1. Never trust a single price source for trade execution

Any code path that **cancels exchange orders, executes market sells, or closes positions** based on a fetched price MUST cross-check against a second independent source (e.g. Coinbase spot vs CoinPaprika). A single third-party API returning a bad price must never trigger an irreversible trade action.

### 2. Price sanity guards on every exit path

Every exit path (stop-loss, take-profit, trailing stop, stale position, gap-down fallback) MUST include a ratio check: `current_price / entry_price`. Reject and log if:
- **Live positions**: ratio > 20x or < 0.02x
- **Paper positions**: ratio > 50x or < 0.001x

These guards are the last line of defense. Never remove or weaken them without explicit user approval.

### 3. Coinbase spot is authoritative for live trades

For live position exit decisions, Coinbase spot prices (from `get_coinbase_spot_prices`) are the authoritative source. Third-party prices (CoinPaprika, CoinGecko, CoinMarketCap) are for display, scoring, and paper trades only. The `check_position_exits` Coinbase overlay and `_check_sl_tp_order_status` Coinbase cross-check enforce this.

### 4. CoinPaprika symbol collisions

CoinPaprika matches by ticker symbol, which is NOT unique. When consuming CoinPaprika data:
- Always prefer the entry with the **lowest rank** (highest market cap)
- Cross-validate against the coins cache; reject if drift > 5%
- The `COINGECKO_ID_TO_SYMBOL` map must cover all actively traded coins

### 5. Price anomaly alerting

The position monitor loop must detect >30% price changes between cycles and send a `critical_alert` before acting. This is implemented in `position_monitor_loop` — do not remove it.

### 6. When adding new price sources or consumers

If you add a new price data source or a new consumer of `get_current_prices_fast`:
- Add it to the cross-validation chain in `get_current_prices_fast`
- Add corresponding tests in `tests/unit/test_price_sanity_guards.py`
- If it feeds an execution path, add a sanity guard
- Log the source in `_price_source_log` for forensic tracing

### 7. Testing requirements for price code

Any change to price fetching, caching, or exit monitoring MUST include tests that simulate:
- Wrong price from a source (symbol collision, stale cache, API error)
- Sanity guard rejection of absurd prices
- Cross-validation rejection when sources disagree

Run `pytest tests/unit/test_price_sanity_guards.py -v` after any price-related change.
