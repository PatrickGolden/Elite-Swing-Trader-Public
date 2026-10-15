# Alert Coverage Gaps — Functional Review #5

**Date:** 2026-03-14  
**Scope:** Ensure alerts exist for every critical scenario: exchange outage, balance mismatch, missed exit, API key issue, etc.

---

## Scenario → alert type / call site matrix

| Scenario | Covered? | Alert type / method | Call site |
|----------|----------|---------------------|-----------|
| Exchange outage / balance sync fails | Yes | `send_sync_balance_failure_alert(exchange_id, error, is_zero_cash)` | app.py ~2936, ~2945 (after sync fails or exchange reports no cash) |
| Balance mismatch (journal vs exchange) | Partial | Drift can trigger `send_drift_alert(reason)` | app.py ~1853. No dedicated "balance mismatch" alert; depends on drift logic. |
| Stop-loss hit (alert only) | Yes | `send_stop_loss_alert(position, current_price)` | app.py ~1005, ~1008, ~1010 (_handle_stop_loss_hit) |
| Stop-loss executed | Yes | `send_stop_loss_executed_alert(position, result)` | app.py ~819, ~986, ~1003 |
| Take-profit hit (alert only) | Yes | `send_take_profit_alert(position, current_price)` | app.py ~1048, ~1051, ~1053 |
| Take-profit executed | Yes | `send_take_profit_executed_alert(position, result)` | app.py ~821, ~1029, ~1046 |
| Trailing stop triggered (alert only) | Yes | `send_trailing_stop_alert(position, current_price, high_water)` | app.py ~1080 |
| Trailing stop executed | Yes | `send_critical_alert(subject, body)` | exit_manager.py ~304 |
| Trailing stop skipped (TP/SL already filled) | Yes | `send_critical_alert` | exit_manager.py ~225 |
| Trailing stop sell failed | Yes | `send_critical_alert` | exit_manager.py ~249, ~257 |
| Trailing stop – journal close failed | Yes | `send_critical_alert` | exit_manager.py ~298 |
| Missed exit (monitor down, order not filled) | Partial | Alerts when SL/TP/trailing fire; if sell fails we alert. No explicit "missed exit window" or "monitor not running" alert. | — |
| API key invalid / expired | No dedicated | Exchange errors surface as sync or execution failures (e.g. 401 → sync_balance_failure). No proactive "API key expiring" or "401" alert. | — |
| High slippage on fill | Yes | `send_critical_alert(subject, body)` | app.py ~3051 (_on_twap_done_open_position_and_orders, slippage > 5%) |
| Low cash (portfolio-level) | Yes | `send_low_cash_alert(cash_avail, total, pct)` | app.py ~1734 |
| Insufficient cash for a signal | Yes | `send_insufficient_cash_alert(coin_symbol, position_usd, cash_available)` | app.py ~575, ~4680 |
| Threshold/signal drift | Yes | `send_drift_alert(reason)` | app.py ~1853 |
| AI rate limit (Claude/Grok) | Yes | `send_critical_alert` | app.py ~1555 |
| Stress report (30d/60d summary) | Yes | `send_stress_report_alert(combined)` | app.py ~1977 |
| Daily status report | Yes | `send_daily_status_report(summary_html)` | app.py ~2746, ~5082 |
| Optimizer report | Yes | `send_optimizer_report(subject, html)` | recursive_optimizer.py ~1446, ~1619 |
| AI / technical signal (per-coin) | Yes | `send_alert(coin_data, alert_type='AI'|'SIGNAL')` | app.py ~1272 |

---

## Alert types (ALERT_TYPES in alert_system.py)

- `ai_signal`, `technical_signal`, `stop_loss`, `take_profit`, `trailing_stop`, `insufficient_cash`, `low_cash`, `drift`, `sync_balance_failure`, `stress_report`.

---

## Gaps

1. **Exchange outage:** No explicit "exchange unreachable" alert. Covered implicitly when balance sync fails (`send_sync_balance_failure_alert`). If you want a distinct "exchange down" vs "sync error," you could add a check (e.g. connection timeout) and a dedicated message.
2. **Balance mismatch:** Only covered if drift logic detects it and calls `send_drift_alert`. There is no dedicated "journal balance ≠ exchange balance" alert (e.g. after reconciliation). Optional: periodic reconciliation that alerts when delta > X%.
3. **Missed exit:** When SL/TP/trailing fire we alert (and optionally execute). If the exit monitor stops (e.g. app crash) or a position should have exited but didn’t, there is no separate "missed exit window" or "position still open past stop" alert. Optional: heartbeat or "exit monitor alive" plus a check for positions that have been past stop/target for > N minutes.
4. **API key invalid/expired:** No dedicated alert. Exchange 401/403 surface as sync or execution failures and thus trigger `send_sync_balance_failure_alert` or execution-failure alerts. No proactive "check API keys" or "API key expiring soon" check.

---

## Optional improvements (no code change in this review)

1. **Repeated sync failure:** After N consecutive balance sync failures, send an explicit "exchange unreachable" or "repeated sync failure" alert (in addition to the per-failure sync_balance_failure).
2. **Balance reconciliation:** If you have a reconciliation step that compares journal to exchange, add an alert when the delta exceeds a threshold (e.g. position size or cash).
3. **Exit monitor heartbeat:** Optional periodic "exit monitor alive" ping (e.g. to a log or low-priority channel); or a job that flags positions that are past stop/target for > N minutes and sends "possible missed exit" alert.
4. **401 on exchange:** When balance sync or order call returns 401, include a line in the alert body: "Check exchange API keys and permissions."

---

## Deliverable summary

| Item | Result |
|------|--------|
| (a) Scenario → alert matrix | See table above; all critical exit and cash scenarios have an alert except dedicated "balance mismatch," "missed exit," and "API key" alerts. |
| (b) Gaps | Four gaps: explicit exchange outage, dedicated balance mismatch, missed exit window, API key expiration/401. |
| (c) Optional improvements | Repeated sync alert, reconciliation alert, exit monitor heartbeat, 401-specific wording. |
