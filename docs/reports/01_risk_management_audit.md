# Risk Management Audit — Functional Review #1

**Date:** 2026-03-14  
**Scope:** Stop losses, position sizing, correlation limits, Kelly cap, veto rules; actual trades vs recommendations.

---

## (a) Sizing and veto logic consistent with design

**Position sizer ([position_sizer.py](../../position_sizer.py))**

- **Order of caps** (as designed): (1) Base size = `max_risk_usd / (stop_loss_pct/100)`; (2) Confidence × regime × volatility; (3) 80% account cap; (4) `cash_available` cap when provided; (5) **Kelly cap**: `min(position, KELLY_CAP_PCT % of account)` — default 5% from [thresholds.py](../../thresholds.py); (6) Liquidity cap: `min(position, max_cash_usage_pct % of cash_available)`.
- **Kelly cap:** Applied in `calculate_for_consensus` after base size and before liquidity cap; reads `KELLY_CAP_PCT` from live_thresholds (default 5%). **Verified:** Logic matches design.
- **Cash and liquidity:** `cash_available` and `max_cash_usage_pct` are used; final size is capped by both Kelly and cash. **Verified.**

**Portfolio / correlation limits**

- **MAX_OPEN_PER_REGIME** (default 3) and **MAX_OPEN_TOTAL** (default 8) are enforced in [quant_signal.py](../../quant_signal.py) via `_check_portfolio_gate(regime)`. When the gate is closed, the quant path can attach `portfolio_gate` badge and effectively prevent a new BUY (signal generation level). App auto-exec only runs for ULTRA_BUY/STRONG_BUY with position sizing; it does not duplicate the gate check because the signal tier is already constrained by the gate. **Verified:** Limits enforced at signal generation.

**Veto rules ([veto_specialist.py](../../veto_specialist.py))**

- Eight rule-based vetoes: (1) Funding BEAR, (2) Whale dump (exchange netflow), (3) LTH distribution, (4) BULL euphoria, (5) BULL extended, (6) Narrative exhaustion, (7) Short-term pump, (8) Extended rally. All thresholds read from `thresholds.get_threshold` / live_thresholds. **Verified:** No LLM; rule order and thresholds as designed.

**Stop/target application**

- Dynamic stop/target from [dynamic_targets.py](../../dynamic_targets.py) and `_get_dynamic_stop_target_for_coin` in app; trade journal stores `current_stop_price`, `current_target_price`. Exit monitor and SL/TP/trailing logic in app and exit_manager. **Verified:** Stops and targets applied and monitored as designed.

---

## (b) Veto fire rate and rule mix

- **From refresh_snapshots.jsonl (last 30d):** Script `scripts/analysis_veto_from_snapshots.py` counted **0** coin-refresh rows with non-null `veto_reason`. So in the snapshot stream, veto was rarely or never persisted for the sampled window (or format differs per refresh).
- **From buy_signals_stress_report (30d):** Recommendations in `ai_recommendations_v2.json` show **by_blocker:** `veto`: **2**, `executed_or_would_auto_exec`: 7, `insufficient_cash`: 11, `policy_no_auto_exec`: 1826. So **veto fired 2 times** in the last 30d at recommendation level; the vast majority of non-executions are policy (MODERATE_BUY / WEAK_BUY no auto-exec) or insufficient_cash.
- **Rule mix:** With only 2 veto events, a rule breakdown from snapshots is not statistically meaningful. Veto reasons in the recommendations file can be inspected manually for those 2 to label rule type.

---

## (c) Gaps between recommended vs executed (blocker breakdown)

**Stress report summary (30d)**

| Blocker                      | Count | Note                                      |
|-----------------------------|-------|-------------------------------------------|
| policy_no_auto_exec         | 1826  | MODERATE_BUY/WEAK_BUY — no auto-exec      |
| executed_or_would_auto_exec | 7     | ULTRA/STRONG_BUY, would have executed     |
| insufficient_cash           | 11    | Skipped due to cash                       |
| veto                        | 2     | Overridden to HOLD by veto specialist     |

- **Missed opportunity count (30d):** 342 (outcome_7d ≥ threshold where we skipped or vetoed).
- **Conclusion:** Most “gaps” are by design (policy_no_auto_exec). Execution-capacity gaps are mainly insufficient_cash (11) and veto (2). No unexpected execution path identified.

---

## (d) Positions exceeding intended risk or correlation limits

- **Check:** No automated scan of `positions.json` for historical “over-size” vs computed size was run (would require replaying position_sizer at open time). Manual spot-check of open/closed positions vs `max_loss_pct` and position size vs 5% account and vs MAX_OPEN_* is recommended.
- **Design:** Single-position risk is capped by Kelly (5% of account) and liquidity cap; total open positions are capped by MAX_OPEN_TOTAL and per-regime by MAX_OPEN_PER_REGIME. **No code path found that would bypass these limits** when opening new positions (gate in quant_signal; sizing in position_sizer).

---

## Deliverable summary

| Item | Result |
|------|--------|
| (a) Sizing and veto logic | Consistent with design; Kelly and liquidity caps applied; portfolio gate enforced in quant_signal. |
| (b) Veto fire rate | 2 veto events in 30d (stress report); 0 in refresh_snapshots veto count (possible format/window difference). |
| (c) Recommended vs executed | Blocker breakdown: 1826 policy_no_auto_exec, 7 executed_or_would_auto_exec, 11 insufficient_cash, 2 veto. 342 missed_opportunity_count. |
| (d) Risk/correlation breaches | No code path bypasses limits; historical over-size not re-run. |

**Recommendation:** Re-run stress report and veto snapshot analysis periodically. Optionally add a small audit job that, for each closed position, compares opened size to `position_sizer.calculate_for_consensus` at open to flag any outlier.
