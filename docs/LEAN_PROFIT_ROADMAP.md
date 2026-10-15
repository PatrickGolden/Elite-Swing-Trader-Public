# Lean Profit-First Roadmap

This is the canonical strategic roadmap for profitable and controlled live trading. The Roadmap tab must mirror this file at all times.

## Strategic North Star

- Build a cohesive, net-profitable trading engine where live edge survives fees/slippage and risk gates are enforced in production.
- Prioritize measured PnL quality over feature velocity: no advanced optimizer activation before data and attribution quality are proven.
- Keep architecture simple and testable: one source of truth for roadmap direction, one disciplined activation path.

## Roadmap Components (Mandatory)

Every roadmap update must include:

1. Data milestones (quality, completeness, freshness, and reproducibility).
2. Future feature implementation milestones (sequenced by dependency and expected edge impact).
3. Needed features that are readiness-gated and expected to improve profitability.

## Core-Only Operating Mode (Default)

- Keep live pipeline active: refresh, scoring, quant gate, veto, sizing, execution safety, and journaling.
- Keep recursive optimizer in lean cadence: weekly run (Sunday 4:00 AM ET), never daily churn.
- Keep historical replay weekly (Sunday 3:00 AM ET) to refresh evidence before optimizer run.
- Keep Optuna, VectorBT sweeps, and SHAP in gated mode until milestones are met.

## Profitability KPI Targets

- Trade volume: 2-6 closed trades per week.
- Win rate: >= 45% rolling 30-day.
- Expectancy: >= 0.0% rolling 30-day.
- Drawdown: max drawdown <= 12% rolling 30-day.
- Attribution completeness: >= 95% closed trades with non-null:
  - `fear_greed`, `funding_rate`, `price_change_24h`, `price_change_7d`, `price_change_30d`, `edge_score`.

## Data Milestones (Readiness Gates)

Gates are tiered so diagnostics unlock early (to help find profitability) while
tuning tools stay locked until performance is proven on live trades.

### Stage A1 — Data Quality (diagnostics unlock)

1. Closed-trade sample size >= 30.
2. Attribution completeness >= 90%.
3. Stable for 2 consecutive weekly reviews.

If any gate fails, remain in strict lean mode.

### Stage A2 — Performance (exit calibration unlock)

1. Closed-trade sample size >= 60.
2. Attribution completeness >= 95%.
3. Rolling 30-day expectancy >= 0.0% (live trades only).
4. Stage A1 features active for >= 2 weeks with no rollback.

### Full Milestones (tuning unlock)

1. Closed-trade sample size >= 100.
2. Attribution completeness >= 95%.
3. Rolling 30-day expectancy >= 0.0% (live trades only).
4. Source resilience is stable (no persistent degradation in core feeds).
5. Replay/optimizer runs are reproducible enough for decision confidence.

## Milestone Trigger Matrix (Future Enhancements)

Use these objective trigger states to kick off future enhancements:

1. Stage A1 trigger (Diagnostics unlock):
  - Closed trades >= 30
  - Attribution completeness >= 90%
  - No expectancy requirement — diagnostics help *find* profitability
  - Stable for 2 consecutive weekly reviews
  - Action: enable SHAP weekly diagnostics + dynamic stop baseline profiling
2. Stage A2 trigger (Exit calibration unlock):
  - Stage A1 active for >= 2 weeks
  - Closed trades >= 60, attribution >= 95%, expectancy >= 0%
  - No rollback trigger fired during Stage A1
  - Action: enable VectorBT sweeps weekly (bounded, review mode)
3. Stage B trigger (Threshold tuning unlock):
  - Stage A2 active for >= 2 weeks
  - Closed trades >= 100, attribution >= 95%, expectancy >= 0%
  - OOS validation gates pass for optimizer inputs
  - No material execution safety incidents in prior 30 days
  - Action: enable Optuna weekly with strict min-improvement and rollback guards

## What Happens at 100 Trades (Complete Checklist)

At 48 closed trades today, reaching 100 is the single biggest unlock. Here is
**everything** that changes — across the optimizer, roadmap, and scoring
pipeline — so nothing is a surprise when the counter hits triple digits.

### 1. Threshold Freeze Lifts (automatic, immediate)

`live_thresholds.json` currently has `freeze_thresholds: true`. When
`is_data_collection_mode()` sees closed trades >= 100 it **auto-removes** the
freeze keys and sets `updated_by: data_collection_auto_lift`. From that moment:

- The recursive optimizer can **write** to `live_thresholds.json` again.
- Weekly optimizer emails switch from "DATA COLLECTION — no writes" to normal
mode with applied/pending recommendations.
- `MIN_TRADES_FOR_REC` and `MIN_TRADES_FOR_AUTO` in `recursive_optimizer.py`
are currently in bootstrap mode (8/6) — the comment says restore to 15/10
at 100 trades *(manual action still needed)*.

### 2. Regression Win-Probability Model activates (automatic)

`regression_scorer.py` has `MIN_TRADES_FOR_REGRESSION = 100`. Once met:

- `train_weekly_regression()` will build a logistic regression model on
attribution features (base_score, ob_ratio, MTF alignment, ATR%, F&G,
regime, quant path, score z-score).
- `get_regression_score()` starts returning P(win) 0–1 instead of None.
- `quant_signal.py` **blends** the regression probability with the additive
score using `REGRESSION_SCORE_WEIGHT` (default 0.6). This changes which
coins cross the STRONG/MODERATE/HOLD boundaries.

**Impact**: Every refresh cycle will score coins differently than today.

### 3. Roadmap Stage B features become eligible

All Stage B features have `closed_trades_gte: 100`. They won't fire instantly —
each needs its `stability_window_weeks` of consecutive passing evaluations.
They split into two groups:

#### Auto-activate (low risk — no approval needed)


| #   | Feature                         | What it does                                                                                                                                                                                                                  | Wait  |
| --- | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| 1   | **Regression Model Training**   | Weekly retrain of win-probability model (see item 2 above)                                                                                                                                                                    | 2 wks |
| 2   | **Gate Score Relaxation Study** | Read-only replay: estimates profit impact of lowering GATE_SCORE thresholds by 2–5 points. Motivated by NEAR/RENDER/HYPE being screened as HOLD 30–60x but never entering the quant pipeline. Does not change any parameters. | 2 wks |


#### Require your approval (high risk)


| #   | Feature                                       | What it does                                                                                                                                                                        | Depends on                       | Wait  |
| --- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ----- |
| 3   | **Optuna Weekly Threshold Tuning**            | Joint multi-parameter optimization. Auto-applies to `live_thresholds.json` only when improvement >= 2%.                                                                             | VectorBT sweeps active           | 2 wks |
| 4   | **Dynamic Stop Parameter Optimization**       | Tunes dynamic stop knobs (floor, drift, beta thresholds) via VectorBT evidence.                                                                                                     | Dynamic stop baseline + VectorBT | 2 wks |
| 5   | **Veto Accuracy Auto-Tuning**                 | Measures veto precision (correct blocks vs missed opportunities) and proposes threshold adjustments.                                                                                | 50+ veto log entries             | 3 wks |
| 6   | **Missed Opportunity Auto-Relax**             | Identifies systematically over-blocked coins and relaxes scoring/veto thresholds.                                                                                                   | Positive expectancy              | 3 wks |
| 7   | **Position Sizing Counterfactual**            | Replays trades with alternative Kelly/sizing and reports which sizing would have improved PnL.                                                                                      | Positive expectancy              | 3 wks |
| 8   | **Expanded Optimizer Bounds**                 | Widens the parameter search space for Optuna beyond the initial conservative bounds.                                                                                                | Optuna active first              | 4 wks |
| 9   | **Trailing Stop Target-Proportional Scaling** | Scales trail width with target % so high-target entries get wider trails (motivated by ETH/SOL exiting at <30% of target move).                                                     | VectorBT active                  | 3 wks |
| 10  | **ATR-Based Volatile Regime Stops**           | Uses ATR to widen initial stops in VOLATILE regime (motivated by SOL whipsaw stop-out in 79 minutes at F&G 12).                                                                     | Dynamic stop baseline + VectorBT | 3 wks |
| 11  | **MODERATE_BUY Accumulation Auto-Execution**  | Auto-executes at half size when a coin hits MODERATE_BUY on 4+ consecutive refresh cycles (motivated by TAO generating 66 MODERATE signals during a 62% surge with zero execution). | Regression model active          | 3 wks |


### 4. Recursive optimizer starts making real changes

Today the optimizer runs weekly but **only reports** — it cannot write because
of the freeze. After the lift:

- **Counterfactual analysis** runs on each OPTIMIZER_BOUNDS parameter using
live trade data.
- **Loss-based recommendations** are generated for parameters correlated with
losing trades.
- Recommendations with confidence `high` (>= `MIN_TRADES_FOR_AUTO` evidence
trades) and net improvement >= `DEFAULT_MIN_IMPROVEMENT` (2%) are
**auto-applied** to `live_thresholds.json` — up to 3 per cycle.
- Lower-confidence recommendations go to **pending** status in the email for
manual review.
- Each applied change enters a **maturity window** (100 trades) where the
system monitors win-rate before/after and will **auto-revert** if win rate
drops >5pp or early win rate is <35% at 10 trades.

### 5. What does NOT change at 100 trades

- **Execution policy**: Only STRONG_BUY and ULTRA_BUY auto-execute. MODERATE_BUY
stays manual-review-only (until item 11 above is approved).
- **Veto rules**: Same rule-based vetoes fire. The veto specialist parameters
don't change until items 5–6 above are approved.
- **Position sizing**: Same risk_pct and Kelly logic. Changes only if item 7
is approved.
- **Stage A1/A2 features**: These are already eligible at 30/60 trades. If they
haven't activated yet, it's because attribution completeness (currently 12.2%)
is below the 90% threshold — that's the real blocker, not trade count.

### 6. Practical activation order (expected sequence)

Since features need stability windows and dependency chains, the realistic
activation order after hitting 100 trades is:

```
Week 0:   Freeze lifts. Optimizer starts writing. Regression model trains.
Week 2:   Regression model + Gate score study auto-activate.
Week 2+:  Optuna becomes eligible (needs VectorBT, which needs A2, which
          needs A1 — the attribution completeness blocker must clear first).
Week 4+:  If Optuna approved → trailing stop scaling, ATR stops, expanded
          bounds become eligible.
Week 5+:  If regression model active → MODERATE_BUY accumulation eligible.
Week 6+:  Sizing counterfactual, missed opp auto-relax, veto autotuning
          eligible (3-week stability windows).
```

**The critical path is attribution completeness**, not trade count. At 12.2%
today, Stage A1 (requires 90%) is the bottleneck. Stage B features that
depend on A1/A2 predecessors won't activate until that chain clears.

## Dynamic Stop Policy Alignment (Lean-Mode)

- Dynamic stops are allowed in core mode only as **bounded risk plumbing**, not as broad optimizer-driven tuning.
- Keep parameter surface minimal and global (no per-coin bespoke policies):
  - `DYNAMIC_STOP_HIGH_BETA_FLOOR_PCT`
  - `DYNAMIC_STOP_HIGH_BETA_VOL_THRESHOLD_PCT`
  - `DYNAMIC_STOP_HIGH_BETA_ATR_THRESHOLD_PCT`
  - `DYNAMIC_STOP_HIGH_BETA_MOM_7D_THRESHOLD_PCT`
  - `DYNAMIC_STOP_REPRICE_DRIFT_PCT`
- In lean mode, changes to these parameters are manual + weekly review only.
- Any move to optimizer/VectorBT tuning of dynamic-stop parameters is Stage B/C work and must stay gated.

### Dynamic Stop Risk Classification

- **Low-risk (may auto-apply when milestones pass):**
  - Telemetry additions for stop attribution (`intended_stop`, `exchange_stop`, stop-policy labels).
  - Stop-order reprice sync hardening.
  - Small bounded floor adjustments within predefined global ranges.
- **High-risk (approval required):**
  - Adding many new dynamic-stop dimensions.
  - Per-coin/per-path bespoke stop policies.
  - Auto-optimization of dynamic-stop knobs before Stage B/C evidence gates.

## Future Feature Implementation Sequence

### Horizon 0-30 Days (Capital Protection + Wiring Integrity)

- Fix execution safety gaps that can cause direct capital loss (oversell/fallback and cash re-check paths).
- Close roadmap drift: enforce doc/tab lockstep as an operational contract.
- Strengthen reconciliation and failure escalation for journal/order mismatches.
- Harden exchange parity expectations (explicitly gate unsupported exchange automation paths).

### Horizon 30-90 Days (Validation Rigor + Cost Realism)

- Upgrade backtest/optimization validation to true out-of-sample behavior.
- Improve transaction-cost realism by incorporating live execution quality telemetry.
- Add reproducibility controls for simulation and optimization runs.
- Expand test coverage for replay/optimizer path integrity and lookahead prevention.

### Horizon 90-180 Days (Edge Expansion, Still Gated)

- Activate SHAP weekly diagnostics first (feature relevance checks from real outcomes).
- Activate VectorBT weekly exit calibration second (stop/target calibration with guardrails).
- Activate Optuna weekly threshold tuning last with strict rollback and bounded changes.
- Add regime-conditional feature activation only after stable KPI performance.

## Roadmap Automation Policy

Yes, roadmap activation can be automated, but only as a fail-closed state machine:

- Automation reads milestone/KPI state from canonical data (`positions.json`, monitoring files, source-status health).
- Automation updates feature-stage state only on scheduled review cadence (weekly), not intraday.
- Automation requires consecutive-pass windows before promotion (for stability), and immediate demotion on rollback triggers.
- Recursive optimizer remains safety-gated: no auto-apply when validation gates fail or baseline comparison is unavailable.
- Every stage transition must be logged with timestamp, trigger metrics, and previous/new state.
- High-risk features require explicit approve/reject decisions from the roadmap control path.
- Daily status email must include any pending high-risk approvals so operator action is never missed.

## Needed Features (When Ready, Profitability-Focused)

- Dynamic slippage/cost model calibrated from live fills by liquidity/volatility regime.
- Robust OOS/walk-forward pipeline for threshold and model tuning decisions.
- Exchange-parity execution safety or explicit exchange-specific fail-closed behavior.
- Strategy-level performance attribution and path-level kill-switch tuning for rapid de-risking.

## System Feature Audit Grid


| Feature Area                                                     | Current Audit Verdict                                              | Integration Level | Needed Now                                                   | Needed Future                                                      |
| ---------------------------------------------------------------- | ------------------------------------------------------------------ | ----------------- | ------------------------------------------------------------ | ------------------------------------------------------------------ |
| Data ingestion (`enhanced_data_fetcher.py`, `resilient_http.py`) | Solid base with resilience; still quality-variance risk            | High              | Enforce degraded-source impact visibility in decisioning     | Expand source quality scoring + latency-aware weighting            |
| Indicators/features (`indicators.py`)                            | Functional, broadly wired                                          | High              | Keep deterministic + test coverage                           | Add feature relevance decay monitoring                             |
| Base scoring (`scorer.py`)                                       | Strong integration, deterministic                                  | High              | Maintain threshold discipline                                | Periodic recalibration with robust OOS evidence                    |
| Quant gate (`quant_signal.py`)                                   | Core engine works; complex rule interactions                       | High              | Guard against silent complexity creep                        | Regime-conditional simplification/refactor for clarity             |
| Veto layer (`veto_specialist.py`)                                | Properly wired as rule-based safety                                | High              | Keep deterministic and explainable                           | Add path-level veto efficacy analytics                             |
| Regime detection (`market_regime.py`)                            | Active and meaningful                                              | High              | Validate denominator/input integrity continuously            | Regime transition quality metrics                                  |
| Position sizing (`position_sizer.py`)                            | Integrated, used live                                              | High              | Ensure post-resize cash/risk recheck on execution path       | Size model calibration by live slippage regime                     |
| Portfolio/correlation risk (`portfolio_manager.py`)              | Good risk gate wiring                                              | High              | Strengthen conservative behavior on missing inputs           | Add dynamic correlation stress scenarios                           |
| Execution engine (`execution_engine.py`)                         | Powerful but has critical edge-case risk                           | High              | Fix TWAP fallback oversell risk now                          | Expand exchange-agnostic execution safety model                    |
| Exit automation (`exit_manager.py`)                              | Good anti-double-sell intent, exchange-asymmetric                  | Medium-High       | Clarify/fail-close unsupported exchange paths                | Full parity or explicit product separation by venue                |
| Journal/state (`trade_journal.py`, `positions.json`)             | Core source-of-truth is coherent                                   | High              | Add stronger escalation when callback retries exhaust        | Richer state consistency checks + invariants                       |
| Reconciliation (`reconciliation` + app hooks)                    | Present but can suppress some mismatches                           | Medium            | Prefer conservative mismatch flagging when price unavailable | Automated incident classification and response                     |
| Backtester (`backtester.py`)                                     | Useful but walk-forward semantics are weaker than institutional WF | Medium            | Correct framing + strengthen validation semantics            | True train/test rolling protocol with explicit carry-forward logic |
| Historical replay (`historical_replay.py`)                       | Critical infra, active                                             | High              | Increase replay integrity tests                              | Replay provenance/versioning + experiment registry                 |
| Recursive optimizer (`recursive_optimizer.py`)                   | Mature safety framework, high utility                              | High              | Keep lean + hard safety gate + persistence robustness        | Modularize and improve evidence arbitration                        |
| Optuna optimizer (`optuna_optimizer.py`)                         | Useful but overfit risk if treated as production truth             | Medium            | Add true OOS objective/gates before stronger use             | Multi-period regime-aware optimization governance                  |
| VectorBT sweeps (`vectorbt_sweeps.py`)                           | Useful tooling, entry reconstruction weakness                      | Medium            | Fix timestamp anchoring/lookahead risk                       | Integrate only with strict validation controls                     |
| Roadmap strategy (`docs/LEAN_PROFIT_ROADMAP.md` + tab)           | Previously drifting; now moved toward lockstep                     | Medium-High       | Keep doc/tab sync contract enforced each change              | Add roadmap KPI tracking audit trail over time                     |


## Weekly Review Rhythm

Every week (Sunday after replay/optimizer window):

1. Review KPI panel: trades/week, win rate, expectancy, drawdown.
2. Review attribution completeness and source reliability.
3. Decide Keep / Loosen / Reactivate:
  - Keep: metrics on target and stable.
  - Loosen: controlled increase only when signal starvation persists without risk blowout.
  - Reactivate next stage: all milestones met and stable for 2 consecutive weeks.
4. Dynamic stop review (if enabled):
  - Compare dynamic-stop cohort vs baseline cohort on expectancy, stop-fill slippage, and protective-order reliability.
  - Keep policy shape frozen for 2-4 weeks before adding dimensions.

## Rollback Triggers

Immediately return to strict lean mode if any trigger fires:

- Expectancy < 0 for 3 consecutive daily checks.
- Win rate drops by >10 percentage points week-over-week.
- Trade count spikes >2x without corresponding PnL improvement.
- Attribution completeness < 90%.
- Material execution safety incidents (reconciliation mismatch, order/journal desync, or protective-order failure spikes).
- Dynamic-stop slippage deterioration (stop fills materially worse vs intended stop over weekly review).
- Dynamic-stop protective-order instability (reprice/cancel/place failure spike).
- Regime-specific catastrophe: any regime with <15% win rate over 20+ trades auto-disables that regime's trading.

## Alignment Contract (Doc + Tab)

- This markdown file is the canonical roadmap strategy.
- The Roadmap tab must render this file content for strategic direction.
- KPI threshold labels and milestone values shown in tab must come from API thresholds, not hardcoded UI literals.
- Any roadmap change is incomplete unless both:
  1. `docs/LEAN_PROFIT_ROADMAP.md` is updated, and
  2. Roadmap tab reflects the same content/thresholds in the same deploy.

## Live Regime Risk — Open Decision Points

These are not gated future features. They are active structural problems requiring an explicit decision **before the next weekly cycle**. Silence is not a valid state.

### Decision 1 — BULL Regime: Resolved via Structural Fixes (commit `8d291f4`)

**Status**: Resolved. `bull_execution_suspend` and the `regime_pf_kill_switch` proposal were **explicitly rejected** in favor of per-coin, per-signal quality gates.

The original BULL bleed (PF=0.26 across 18 trades) was diagnosed as three compounding issues, all fixed structurally:

1. `TACTICAL_UPGRADE_24H_PCT` had drifted to 3.0 (optimizer), causing phantom SIDEWAYS→BULL upgrades on routine bounces — bounds floor raised to 5.0 and clamped in optimizer.
2. No chase guard on momentum entries — added `BULL_CHASE_24H_MAX_PCT` (default 5.0) which caps tier to MODERATE_BUY when 24h move is extended.
3. Stops too tight (0.9× ATR multiplier, 3.0% floor) — widened to 1.1×/5.0% so entries survive normal retracements.

Additional structural fixes (post-audit):

4. BULL R:R reduced from 3.0 to **2.0** — targets now ~5.5–9% (achievable) vs ~13.9% (almost never hit).
5. **24h momentum gainer screen** — top 100 gainers merged into fetch universe beyond mcap top 250; scored set is top 50 mcap plus extra momentum coins passing Coinbase/stablecoin filters.
6. **Breakeven stop** trigger at 1.5% — valid protection window below 3% trailing activation.
7. **Trailing stop activation** at 3% (`TRAILING_STOP_MIN_PROFIT_PCT`), not 7%.
8. **Data quality momentum bypass** — strong 24h gainers (≥10%, tunable via `DATA_QUALITY_MOMENTUM_BYPASS_PCT`) need one fewer data source for STRONG_BUY.

BULL trades now flow through the normal pipeline: `BULL_REGIME_MIN_SCORE` (live=72) is the only regime-level gate; quality is filtered per-coin by chase guard, score floor, funding/volume/pump guards, and the per-path kill switch (`PATH_KILL_*`). No blanket regime suspension exists. If the structural fixes prove insufficient, the response is a tunable threshold change — not an off-switch.

---

### Decision 2 — SIDEWAYS Regime: Three Structural Fixes, No 100-Trade Gate Required

**SIDEWAYS is 69% of all signals. At 36.1% WR with loss_avg 19.6% > win_avg 14.1% (negative skew), it is the primary driver of negative portfolio expectancy at scale.**

The roadmap previously had only EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE=58 as a response. Three systematic fixes are now in the framework:

| Feature | Stage | Risk | What it does | Gate |
| --- | --- | --- | --- | --- |
| `sideways_gate_floor_raise` | A1 | Low (auto) | Raises GATE_SCORE_SIDEWAYS 62→68, EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE 58→64, reducing SIDEWAYS entries until SHAP identifies winning features | 30 trades + 90% attr |
| `sideways_momentum_confirmation` | A1 | Medium (approval) | Requires `price_change_7d > 0` before any SIDEWAYS auto-execution — identical pattern to proposed BULL fix, structurally motivated by the same failure mode | 30 trades + 90% attr |
| `sideways_path_kill_switch` | A2 | High (approval) | Suspends any SIDEWAYS quant path (EARLY_MOMENTUM, STRONG_BUY, etc.) whose rolling 30-trade SIDEWAYS expectancy is negative; re-enable requires manual approval | SHAP active + 60 trades + 95% attr |

**Implementation note**: `sideways_gate_floor_raise` auto-activates once A1 gates pass and sets `sideways_gate_floor_raised=true`. The actual threshold change in `live_thresholds.json` is a separate manual action triggered by the flag — this is intentional (the flag is evidence, the apply is deliberate).

---

These three features were identified as high-value during the April 2026 system audit but are **explicitly gated** until sufficient post-fix trade data accumulates. Do not implement until the gate conditions below are met — the pre-fix sample is too small and too noisy to calibrate them correctly.

### P3.16 — BULL Momentum Veto (`bull_momentum_veto_enabled`)

**What**: Block auto-execution in BULL regime when the coin's 7d momentum is overextended (extended RSI + extended 7d % gain). Motivated by the BULL/SIDEWAYS loss pattern — the system is buying extended moves into a mean-reversion regime.

**Prerequisite decision**: Before P3.16 can be calibrated, you need 20–30 post-fix BULL trades. Those trades are expected to lose at the current structure. See "Decision 1 — BULL Regime Auto-Execution" above: either suspend BULL execution now (slows data collection but stops the bleeding) or explicitly accept the loss as data-collection cost. P3.16 implementation is blocked until that decision is logged.

**Gate**: 20–30 post-fix trades in BULL regime with attribution data. Verify via `recursive_optimizer.py` BULL regime counterfactual — look for `BULL_MOMENTUM_THRESHOLD` or similar in OPTIMIZER_BOUNDS showing statistically negative expectancy on extended entries.

**Risk**: High. Changes which BULL signals execute. Incorrect threshold = miss profitable entries or still enter losers.

**Implementation pointer**: Add `bull_momentum_veto_enabled` to `live_thresholds.json` defaults. In `_start_twap_if_safe()` (app.py), check RSI + 7d pct against threshold before auto-executing BULL regime signals.

---

### P3.17 — ATR-Based Stop Widening in VOLATILE Regime

**What**: Use ATR to set a wider initial stop floor in VOLATILE regime (motivated by SOL whipsaw stop-out in 79 minutes at F&G 12). Currently the stop floor is fixed across regimes.

**Gate**: 20–30 post-fix VOLATILE regime trades. Check whether early stop-outs in VOLATILE have ATR at entry significantly wider than the fixed floor — if so, the ATR floor is load-bearing.

**Risk**: High (approval required). Wider stops = larger realized losses on genuine losers. Must be bounded by `DYNAMIC_STOP_HIGH_BETA_FLOOR_PCT` ceiling.

**Implementation pointer**: `position_sizer.py` or `_apply_dynamic_targets_to_coin()` in app.py — detect VOLATILE regime and floor `stop_loss_pct` at `max(current_stop, ATR_PCT * ATR_FLOOR_MULTIPLIER)`. Add `ATR_VOLATILE_FLOOR_MULTIPLIER` to OPTIMIZER_BOUNDS.

**Dependency**: P3.17 must land before P3.18 (MODERATE_BUY accumulation needs reliable stops to be safe at half-size).

---

### P3.18 — MODERATE_BUY Consecutive-Signal Auto-Execution (Half Size)

**What**: Auto-execute at half size when a coin hits MODERATE_BUY on 4+ consecutive refresh cycles. Motivated by TAO generating 66 MODERATE signals during a 62% surge with zero execution — missed the system's best signal of the year.

**Gate**: P3.16 implemented AND 30+ post-fix trades with MODERATE_BUY signals in the attribution data. Need to verify the consecutive-signal pattern actually predicts wins before wiring auto-execution.

**Risk**: Very high (approval required). Opens execution to a lower-confidence tier. Requires reliable stops (P3.17) and BULL veto (P3.16) to be in place first.

**Implementation pointer**: `screening_log.json` tracks signal history. Add a consecutive-signal counter check in `_start_twap_if_safe()` — read last N screening entries for the coin and count consecutive MODERATE_BUY hits. Add `MODERATE_BUY_CONSECUTIVE_THRESHOLD` (default 4) to OPTIMIZER_BOUNDS.

**Dependency**: P3.16 + P3.17 must be active first.

