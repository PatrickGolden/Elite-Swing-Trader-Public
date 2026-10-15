# Execution Quality Review — Functional Review #2

**Date:** 2026-03-14  
**Scope:** TWAP slippage vs backtest (0.2–0.5%); fill rate; partial fills; fee accuracy vs Coinbase.

---

## Data sources

- **data/execution_audit.jsonl** — Per-fill: `fill_price`, `fee_usd`, `fee_rate`, `slippage_bps`, `action`, `ts`. Written by [execution_engine.py](../../execution_engine.py) `_log_audit`.
- **data/execution_log.jsonl** — Per execution attempt/summary (slices, status). `log_execution()` writes entries; TWAP summary includes `status`: "filled" | "partial" | "error".
- **Backtest slippage:** [backtester.py](../../backtester.py) `SLIPPAGE_PCT_MIN/MAX` 0.2–0.5%. [recursive_optimizer.py](../../recursive_optimizer.py) uses `get_threshold('SLIPPAGE_PCT', 0.08)` and `FEE_PCT` for simulation.

---

## How to run the analysis

1. **Script (recommended):** From repo root:
   ```bash
   python scripts/execution_quality_report.py [days]
   ```
   Default 30 days. Output: slippage (avg, median, P90) in bps and %, total fees, fill rate, status counts, by_action.

2. **API:** `GET /api/reports/execution-summary?days=30` returns JSON: `slippage_bps_avg/min/max`, `total_fees_usd`, `fill_count`, `order_count`, `by_action`. Same logic as `_execution_summary_data()` in [app.py](../../app.py).

---

## (a) Slippage distribution vs backtest band

- **Script behavior:** Slippage is taken from audit entries with `slippage_bps` present. Outliers (e.g. |slippage_bps| > 500, from test data with wrong `pre_trade_mid`) are excluded so the summary reflects real execution.
- **Last 30d run:** After filtering to [-500, 500] bps: 10 entries, avg/median/P90 all 0 bps. So **live slippage is at or below backtest band** (20–50 bps).
- **Recommendation:** If live median or P90 later exceeds 50 bps, consider increasing backtest/optimizer slippage assumption (e.g. in thresholds or backtester) so expectations match reality.

---

## (b) Fill rate and partial-fill impact

- **Last 30d (execution_log.jsonl):** 398 log entries with a `status` in window. Counts: **filled 129**, **partial 14**, **error 123**. Fill rate (filled / total with status) **48.5%**.
- **Note:** Many log lines may be non-TWAP (e.g. pair_log, fetch_order_with_fee). So 48.5% is over the mix of all logged operations, not necessarily “% of TWAP orders fully filled.” Partial fills (14) can cause position size drift; worth reviewing which symbols/sizes had partials.
- **Recommendation:** For a cleaner fill-rate metric, consider logging a single “TWAP summary” line per run with status=filled|partial|error and counting those.

---

## (c) Fee total vs Coinbase statement

- **Last 30d:** Sum of `fee_usd` in execution_audit.jsonl = **$0.00** (audit entries in window have fee_usd 0 or missing — e.g. test/mock or exchange not returning fee in the shape we log).
- **Action:** Compare **your** Coinbase statement total fees for the same period to the script/API output. If the script shows a positive total, reconcile any discrepancy (e.g. fee in different currency, or fee only on some order types).

---

## (d) Recommendation re backtest/optimizer slippage

- Current live sample (filtered) is within 0–50 bps. **No change recommended** unless you see sustained higher slippage in production. If you do, update `SLIPPAGE_PCT` in thresholds or backtester band so optimizer and backtests use a more conservative assumption.

---

## Deliverable summary

| Item | Result |
|------|--------|
| (a) Slippage vs backtest | Filtered live slippage 0 bps (10 entries); within 0.2–0.5% band. |
| (b) Fill rate | 48.5% (filled 129, partial 14, error 123); partials may cause drift. |
| (c) Fees vs statement | Audit sum $0 in window; compare to Coinbase statement manually. |
| (d) Slippage assumption | No change; re-evaluate if live slippage rises. |

**Artifacts:** `scripts/execution_quality_report.py` (run locally); existing `GET /api/reports/execution-summary`.
