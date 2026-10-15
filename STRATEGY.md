# STRATEGY.md — frozen strategy decisions and pre-registered gates

**Version 2.1 — 2026-10-11** (v1.1 baseline 2026-06-11; every change since is a dated
Decision Log entry; v2.0 corrects the record — see §1a).
Synthesized from the four-agent validation review in `tasks/research/` (independent
re-derivation, red team, methodology audit, data feasibility) of the 2026-06-09 audit.

## Rule for every future session (human or AI)

Execute this document; do not re-derive strategy from scratch. A recorded decision may be
challenged **only** by presenting evidence against its recorded basis, and changes bump the
version with a dated entry in the Decision Log. Advice that contradicts this file without
evidence is churn, not insight.

---

## 1. Established facts (verified by ≥2 independent passes, 2026-06-11)

| Fact | Evidence |
|---|---|
| Live book: 85 closed trades, −8.1% of account, 25.9% WR, −2.21%/trade | positions.json (is_test=false), recomputed by agents 1 & 2 |
| BULL regime PF **0.14–0.15** (worse than the 0.23 first reported) | agent2 recompute |
| The legacy multi-path pipeline has **no edge**: PF 0.85–0.87 OOS in its own replay | data/stage1c_gate_summary.json (verdict FAIL) |
| The lean +11.5%/trade validation is unreliable: 30-coin 2026-survivor universe; **97% of the claimed edge comes from the exit policy**, the component most inflated by survivorship; the generating script was never committed | agent2 A3/A4; stage1c_replay.py:63; lean_ab vs lean_full decomposition |
| Live lean was never actually traded: all 7 June entries violated lean entry policy (paths/regime/momentum) and carried legacy stops | agent1; trade forensics |
| Measured costs: **1.20%/side taker** (all logged fills) → ≥2.4% round-trip fees; backtest modeled 0.66%; entry fees not instrumented (flat $0.10 placeholder) | agent4; data/execution_audit.jsonl |
| The "~1pp stop slippage" is mostly fees, not price slippage (median true slip −0.015%) | agent4 fill analysis (n=20) — supersedes earlier claims |
| BTC has been risk-off since **2025-10-28**; zero lean-eligible entries possible since; data/lean_audit.jsonl does not exist | agent2 A9, computed from cached BTC daily bars |
| Optimizer cannot rescue this: tunes thresholds on 11–16-trade windows; regression model 71 samples / 17 features; circular feedback | agents 1 & 2; optimizer's own audit |
| Live data already rejects the lean backtest claim by **>4 standard errors** | agent3 (SE ≈ 1.6–2.7pp on n=85) |

## 1a. Established facts added 2026-09-26 (v2.0 — a re-read of artifacts that already existed)

| Fact | Evidence |
|---|---|
| **Rotation's cited stats (+3.52%/episode, PF 2.52, +469%) were computed at the OLD 0.66% round-trip cost.** At the MEASURED 2.4% cost, same run: OOS n=34, **+1.78%/episode** (MDE 6.4pp), bootstrap 5th-pct **−2.06%**, top-3-winners-removed **−2.45%**, walk-forward **2/4 folds** positive (2025 fold −1.75%). **Fails G3, G4, G6.** Rotation was the G8 *benchmark*; it never passed §3 as a candidate. The file drifted from "last one standing" to "passed"; that drift is corrected here. | `data/stage1c_adjudication.json` (2026-06-11) → `rotation_btc.test_measured_cost`, `folds_measured_cost` |
| **Rotation's honest benchmark is HOLDING BTC, not cash.** OOS 2023-01→2026-09: HODL **+384%** (maxDD −53%); rotation at Coinbase base 1.25%/side **+30%** (DD −38%); at Kraken T3 0.43%/side **+131%** (DD −26%). Full 2018-07→2026-09: HODL +997% (DD −77%); rotation +342% / +1,164% at the same two cost levels. Rotation is a **drawdown-reduction overlay** whose value is a function of per-leg cost — not an alpha source — and it lags HODL in uninterrupted bull legs (43% time in market). | BTC daily-cache replay 2026-09-26 (next-day-close fills, 64 episodes = 128 legs); cross-checks `stage1c_q3_rotation_trials.json` incumbent measured_cost total_net 340% |
| **Per-leg cost is the dominant variable in every candidate's outcome** and it is venue- and scale-dependent. Kraken Pro spot (verified 2026-09-26): T1 0.40% maker / 0.80% taker; **T3 ($10K 30d volume OR $20K assets-on-platform) 0.22% / 0.38%**; T6 ($100K) 0.12% / 0.25%. Coinbase Advanced base: 1.20% taker (measured from our fills). Kraken is already wired in `execution_engine.py`; never tested. | kraken.com/features/fee-schedule; `data/execution_audit.jsonl` |
| **Data capture had holes:** `refresh_snapshots.jsonl` kept signal flags but no price/mcap/indicators; the 250-coin market snapshot (`coins_cache.json`) was overwritten hourly; the 12 enhanced sources were never persisted; no environment feature (ETH/BTC, breadth, dominance) was computed anywhere; alt spreads were never logged (alt paper fills assume 0.05% slip on every coin — unverifiable). Fixed v2.0: `market_state_logger.py` (hourly env row, daily universe snapshot, alt top-of-book). | code audit 2026-09-26 |
| **Legacy machinery still ran ungated:** weekly ML pipeline (Sat 02:00), replay + adversarial backtest (Sat 03:00, resets the CoinGecko circuit breaker the health check relies on), stage1b promotion monitor (emailed "Decision: GO" 2026-09-25 on retired criteria: same-day clusters counted as 113 independent trades, 0.33% cost stress, benchmark = the retired live strategy). Gated v2.0 on `weekly_tasks_enabled=false`. `screening_log.py` carried its own 10,000-row cap so the Aug-08 raise to 75,000 never applied. | `app.py`; `learning_report.json` / `backtest_trades.json` mtimes 2026-09-26 03:06 |
| **Forward alt evidence (observational, n small):** 12 entry-day episodes 2026-08-20→09-18; **10 beat HODL BTC over the identical windows**; episode mean +5.6% vs MDE 8.6pp; entirely inside one alt-favorable regime (2026-09-26: ETH/BTC +8.5% vs 200d SMA, 35/51 top alts beating BTC over 30d). Not proof — but it is *forward* evidence, immune to the survivorship bias that killed the June backtest, and the first of its kind. The June test was **unconditional**; it never asked the owner's question (edge conditional on environment). | `positions.json` paper_trade rows; CoinGecko 2026-09-26 |

## 2. Decisions in force

**D1 — No live auto-execution** until a strategy passes the full gate set (§3) and the
promotion ladder (§4). Enforced as state + code, not exhortation:
`execution_config.json` set false (2026-06-11); `get_execution_config()` in both app.py and
app_config.py is fail-closed (missing/corrupt/null config → OFF; only explicit `true` → ON);
regression tests pin this.

**D2 — Optimizer threshold auto-apply is permanently disarmed** until re-pointed as a
hypothesis falsifier. The 100-trade auto-lift side effect was removed from
`get_data_collection_status()` (2026-06-11); lifting the freeze is a manual act, gated on §4.

**D3 — The 2023+ test split is burned** (lean 1a/1b/1c variants were selected against it).
The rebuilt point-in-time gate run is the one remaining clean shot; full criteria (§3) are
fixed here, in advance, before that run.

**D4 — Candidate roster for the clean run (registered trials, ≤4 per quarter):**
1. **BTC(/ETH) risk-on rotation** — long majors only when risk-on; the null-hypothesis
   strategy. Promoted from baseline to first-class candidate: it is the lean strategy minus
   coin selection, needs no pipeline, and anything we ship must beat it.
2. **Lean momentum family** (risk-on + momentum paths + wide stop + time exit) on the
   honest universe, with hold-period arms 7d/14d/21d/30d (literature: crypto momentum decays
   into reversal beyond ~1 month; each arm counts as a registered trial).
3. (reserved)
4. (reserved)

**D5 — Capital reality (DECIDED by owner, 2026-06-11).** At base-tier fees (1.2%/side) and
the 6%-portfolio-risk / 24%-stop arithmetic, a small account cannot produce meaningful P&L
even with a real edge. Owner decision: keep only a small cash balance until a proven track
record exists, then fund with meaningful capital. **The project deliverable is therefore a
verified track record** (honest backtest pass → calibrated paper cohort → min-size live per
§4); P&L at current scale is explicitly not the goal, and no effort is spent optimizing it.
**v2.0 amendment: funding and venue are coupled.** Fee tier is itself a function of capital
(Kraken T3 = $20K assets-on-platform OR $10K 30d volume), and per-leg cost decides whether
rotation beats holding BTC (§1a). So the funding decision is not "after the strategy is
proven" — the venue trial (VENUE-2026Q4-1) states which funding/venue level makes which
strategy viable, and D5 is decided with that table in hand. Paper-only stands until then.

**D6 — Kill conditions (evidence-based; replaces the 2026-09-30 date, owner-approved
2026-09-26).** The calendar date contradicted §4 ("event-keyed, never calendar-keyed"):
a low-frequency candidate cannot complete a round trip on schedule. A date that can only
be honored by killing a live position or extended by optimism is not discipline. Each
candidate is now killed by a named EVENT, and an event that has occurred cannot be undone
by extending anything:

- **K1 — Rotation.** Retired if ANY: (a) VENUE-2026Q4-1 shows no achievable venue tier at
  which rotation beats HODL BTC on the registered criteria; (b) its first completed
  live-system round trip fails the §4b fidelity gate (F1–F6); (c) its paper cohort
  underperforms HODL BTC by more than the round-trip cost over a completed risk-on →
  risk-off cycle (it is SUPPOSED to lag in bull legs and win on the exit — if the exit
  doesn't save more than it costs, the overlay has no reason to exist).
- **K2 — Alt selection (lean-7d family).** Stays observational; the June rejection stands
  until overturned by evidence. Re-enters the ladder ONLY if H-2026Q4-2 passes AND the
  forward bar (below) is met. Rejected for good if H-2026Q4-2 fails on anything other than
  sample size, OR the forward bar fails once 25 episodes exist.
  **Forward bar (fixed now, before the outcome is known):** ≥25 independent entry-day
  episodes (same-day entries = ONE episode) accrued from 2026-08-20; episode-mean net return
  > its MDE (2.5σ/√n); beats HODL BTC over the identical window in ≥60% of episodes; and
  the environment record (market_state.jsonl) shows the in-regime / out-of-regime split
  predicted by H-2026Q4-2 rather than an edge that exists regardless of environment.
- **K3 — Project level.** If K1 and K2 both fire, the standing conclusion is "no
  demonstrable edge at achievable scale and cost"; the system goes research-only and no
  live trading of these strategy classes occurs. No calendar extension exists — only a new
  registered hypothesis with its own kill events.

## 3. Pre-registered backtest gates (the clean run)

A candidate PASSES only if **all** hold on the point-in-time universe:

| # | Gate | Value |
|---|---|---|
| G1 | Universe | Point-in-time monthly top-N by mcap incl. dead coins, ∩ Coinbase-listed-at-date; delisted mid-hold → forced exit at last price −20% haircut; dead coin → −100% |
| G2 | Costs | Base = measured round-trip at the account's live tier (`fill_cost_model.py`; 1.9% since 2026-10-11, was 2.4% — verdicts keep the basis they ran on) + spread until maker fills proven; stress = 2× measured; PF must be recomputed under stress (fix `_half_stats` — current code stresses expectancy only) |
| G3 | Walk-forward | ≥4 rolling OOS folds (suggest 2022/2023/2024/2025), 30-day purge at boundaries; ≥3/4 folds positive AND pooled OOS positive |
| G4 | Sample | Pooled OOS n ≥ 100 **episode-level** trades (max 1 concurrent position per coin in replay; live position-limit parity); print MDE = 2.5σ/√n next to every expectancy; claims below MDE are void |
| G5 | Multiple testing | Trial registry (`data/trials.jsonl`: config hash, date, criteria, result — written BEFORE the run); pass requires expectancy > √(2·ln N)·σ/√n over all N registered trials |
| G6 | Robustness | Bootstrap 10k resamples: 5th-pct expectancy > 0 and 5th-pct PF > 1.0; gate must survive removal of top-3 winners |
| G7 | Entry skill | Beat random-entry baseline: same entry dates, coin drawn uniformly from that day's eligible universe, identical exits, ≥1000 resamples; strategy > 95th percentile of baseline distribution |
| G8 | Opportunity cost | Beat the better of **HODL BTC** and BTC risk-on rotation, net of costs, on compounded return OR return/max-drawdown, or be rejected in its favor (v2.0: HODL added — rotation itself fails against it at measured cost, §1a) |
| G9 | Tradeability | Net OOS expectancy ≥ measured round-trip cost + 2pp |

## 4. Promotion ladder (event-keyed, never calendar-keyed)

1. **Backtest** passes §3 →
2. **Paper** — criteria depend on strategy frequency (amended 2026-08-20, see D7) →
3. **Small live** (only if D5 funding decision made): min-size, implementation shortfall
   logged per fill (decision price vs fill vs fee), live within 1pp of paper →
4. **Sized live**. Demotion is automatic on the same gates failing.
   If risk-off prevents paper accrual, the clock **extends** — n=0 decides nothing (D-gate
   from agent2 A9).

### 4a. Paper gate — HIGH-frequency candidates (many closes/year)
≥30 closes (sign check only; MDE ~7–11pp) AND paper fill costs within 0.5pp/trade of
measured live costs on ≥20 matched fills AND expectancy > 0.

### 4b. Paper gate — LOW-frequency candidates (<15 closes/year), e.g. BTC rotation
**Fidelity gate, not a statistical gate.** ALL of the following, on the live system
(a dry run does not count):

- **F1 — ≥1 complete round trip**: entry AND exit both executed by the running app,
  the exit fired by its own rule (risk-off flip), never manually forced.
- **F2 — price fidelity**: realized entry/exit effective prices match the model's
  slippage assumption within **0.25pp** per side.
- **F3 — cost fidelity**: realized round-trip cost within **0.5pp** of the measured
  cost model (`fill_cost_model.py`); fees recorded on both legs.
- **F4 — no config drift**: the shipped config hash equals the config that passed §3
  (the June 2026 failure was shipping an unvalidated variant of a validated strategy).
- **F5 — availability**: no unexplained system gap while the position was open; any
  gap must be attributable in the ops record (standby/reboot/known cause).
- **F6 — alerting**: entry and exit milestone alerts both delivered.

**Why the change (D7):** the ≥30-closes bar was written for a high-frequency strategy
and mis-applied to one averaging ~7.6 episodes/year — it would take ~4 YEARS, and a
successful long trend produces FEWER data points, so the better the strategy performs
the slower it qualifies. That is a broken criterion, not discipline. The statistical
evidence for rotation already exists in the §3 backtest (8.5 years, 65 episodes,
4 walk-forward folds, bootstrap, deflated hurdle). Paper's remaining job is proving the
LIVE SYSTEM EXECUTES WHAT WAS TESTED — this project's actual historical failure mode.
Lowering the count does NOT lower the edge bar: §3 is unchanged, and a candidate that
never passed §3 cannot reach §4 at all.

## 5. Engineering backlog (sequenced)

1. Commit a reproducible lean gate driver (`stage1c_replay.py --lean`) embedding git SHA +
   config snapshot in every artifact — no uncommitted validation scripts, ever again.
2. Point-in-time universe build (agent4 pipeline: Kaggle CMC historical 2013–2021 + CMC
   weekly-snapshot scrape 2021–2026 + Coinbase listing dates from first candle + curated
   delist list; ~16–21h; $0; zero CoinGecko credits).
3. Cost instrumentation: record real entry fees + implementation shortfall in
   execution_engine.py (entry side is currently a $0.10 placeholder).
4. Replay fixes: episode-level position limits; ~~`_half_stats` PF-under-stress bug; fold
   parameterization + purge~~ (**fixed 2026-06-11 post-audit**: stress columns now recompute
   WR/PF on cost-adjusted P&L; the G3 purge predicate was unsatisfiable — it never dropped a
   trade — and now embargoes entries within 30d of the *next* fold boundary; declared
   `ROUND_TRIP_COST_PCT` corrected 0.66→0.96 to match what `simulate_daily` actually charges,
   making lean/rotation measured-cost overlays symmetric at 2.4%. **All gate artifacts dated
   ≤2026-06-11 predate these fixes** — stressed PFs in them are overstated, G3 folds unpurged,
   and lean bore ~0.3pp/trade extra vs rotation [conservative direction; verdicts unaffected
   per audit]. Regenerate before citing stress/fold numbers); bootstrap + MDE in
   `evaluate_gate`; trials.jsonl enforcement (unregistered config hash → refuse to evaluate).
5. Random-entry + BTC-rotation baselines in the same harness.
6. Lean-policy heartbeat (daily event to data/lean_audit.jsonl so silence ≠ breakage), and
   exercise the guards via injected staging entries instead of waiting for risk-on.
7. Cohort all expectancy reporting by config version (`lean_compliant` tag exists); pooled
   legacy number labeled "do not decide on this".
8. Deletions (after the clean run, per its results): non-surviving quant paths, regression
   blend (until 500+ trades), hardcoded win-prob priors, bonus promotions, exchange
   stop-order repricing (86% failure — software loop is the real stop until fixed).

## 5b. SETTLED: why rotation carries no stop and no trailing stop

**Do not re-litigate this without new evidence.** It has been asked, tested, and
answered (2026-08-27). The exit is the risk-off flip — nothing else.

**The mechanism.** Rotation's profit is extremely concentrated: the top 5 of 65
episodes produce ~111% of all profit (remove them and the strategy is net negative).
Those episodes survive deep drawdowns *while still winning*:

| Entry | Days held | Final return | Worst dip from its own high |
|---|---|---|---|
| 2020-10-03 | 126 | **+263.6%** | **25.2%** |
| 2019-04-02 | 111 | +97.1% | **27.1%** |
| 2024-02-09 | 54 | +42.8% | 15.4% |
| 2021-02-08 | 42 | +16.2% | 21.3% |

Any trail tight enough to feel protective ejects you from exactly these trades:

| Trail width | Winners it would cut short | Share of winning return affected |
|---|---|---|
| 5% | 15 of 25 | 96.3% |
| 8% | 10 of 25 | 80.5% |
| 10% | 7 of 25 | 70.5% |
| 13% | 5 of 25 | 66.6% |
| 20% | 3 of 25 | 58.1% |

Tightening is **monotonically worse** — it is not a tuning problem, it is the wrong
direction. (Caveat: "cut short" = the trail fires at some point, not that all return
is lost; damage depends on whether the dip came early or late. Direction is
unambiguous regardless.)

**The empirical confirmation.** H-2026Q3-4 tested the loose end (activate +8%,
trail 13%) as a registered trial: FAIL, 1/4 folds, and only ONE trail exit in
2022-2026 — at 13% the risk-off flip fires first, so 3 of 4 folds were identical.
Loose trails are inert; tight trails are destructive. There is no useful middle.

**Why the intuition is right elsewhere and wrong here.** Trailing stops work when
returns are roughly symmetric and you are protecting typical gains. This strategy is
the opposite: it is paid for surviving drawdowns that shake others out. The
discomfort IS the product. Drawdown control already comes from the regime rule —
max DD 48.6% vs 81.4% for buy-and-hold.

**What would reopen this:** evidence that the return distribution has changed (e.g.
profit no longer concentrated in a few long episodes), or a fundamentally different
exit design — not a new trail width. A width sweep after seeing these results is the
double-dipping this framework exists to prevent.

## 6. Known limitations (accepted, documented)

- Daily-bar replay validates a *cousin* of the hourly live pipeline; a backtest PASS
  authorizes paper only — paper IS the hourly test (agent2 A5).
- The honest prior is that the clean run **fails** for the alt-momentum family and the
  surviving candidate is BTC/ETH rotation. That outcome is a success of the process, not a
  failure of the project (agent2 A4/A12).
- Honest parameter count of the lean family is ~9–10, not 6; the budget cap stays and the
  count is audited at registration time.

## Candidate ranking (v1.3, after the registered runs of 2026-06-11)

| Rank | Candidate | Test OOS (honest universe) | Status |
|---|---|---|---|
| 1 | **BTC risk-on rotation** (G8 benchmark) | ~~+3.52%/episode, PF 2.52, +469% net since 2018~~ — **those were at 0.66% RT.** At measured 2.4%: +1.78%/episode, boot5 −2.06%, 2/4 folds → **fails G3/G4/G6**; lags HODL BTC (v2.0, §1a) | Paper cohort continues as fidelity plumbing + HODL comparison. Viability depends on venue cost → VENUE-2026Q4-1. Kill events: D6/K1. |
| 2 | **Lean momentum, 7d hold** | +3.73%/trade, PF 2.15, WR 55.6% | Only pipeline candidate alive. Must clear G4 (episode-level n), G5 (deflated hurdle, N=8 registered trials), G6 (bootstrap) AND beat #1 net of measured costs before any allocation. |
| — | Lean momentum, 14/21/30d hold | +2.1–3.0%, PF 1.25–1.44 | Rejected — dominated by 7d; decay matches momentum→reversal literature. |
| — | Legacy multi-path pipeline | −0.87%/trade, PF 0.86, n=5,137 | **Rejected with prejudice.** Schedule §5.8 deletions. |

## Q3 2026 registered trials (v1.6 — criteria fixed BEFORE any run)

Trial budget: ≤4 new variants this quarter (G5). Registered 2026-07-02; all
paper/backtest only; nothing changes live behavior regardless of outcome.

**H-2026Q3-1 — Vol-managed rotation sizing** (upgrade path for candidate #1).
BTC rotation with entry-time position scale = min(1, 60% ÷ realized 30d annualized
vol). Literature: momentum-crash mitigation via vol scaling (Daniel-Moskowitz;
FMPM 2025). vol_target=60% fixed a priori — NOT swept.
PASS iff, on BOTH WF halves at modeled AND measured costs:
(a) max drawdown ≤ 0.75× plain rotation's, AND (b) compounded net return ≥ 0.8×
plain rotation's. (Accepting less return for much less DD is the point.)

**H-2026Q3-2 — Dual BTC+ETH rotation.** 50/50 weights; each leg long only while
its own MA200/band/30d rule is risk-on, else that leg in cash.
PASS iff, on BOTH WF halves at modeled AND measured costs:
(a) compounded net return ≥ plain BTC rotation's, AND (b) max drawdown ≤ 1.1×
plain BTC rotation's.

**AUDIT-2026Q3-3 — Risk-on rule robustness** (not a candidate — a stress test of
the incumbent rule the whole system leans on; produces no new config).
Grid: MA ∈ {180, 200, 220} × band ∈ {1.0, 1.5, 2.0}%, plain BTC rotation.
Verdict "PLATEAU" iff every cell's compounded net return is within ±25% of the
incumbent cell AND no cell's return flips sign; else "CLIFF" → the rule is
fragile and must be redesigned before any live promotion.

**D7 — Paper gate is frequency-aware (2026-08-20, owner-approved).** Low-frequency
candidates (<15 closes/yr) use the §4b fidelity gate (F1–F6) instead of ≥30 closes.
Rationale in §4b. Guard against self-serving relaxation: this was proposed and adopted
while the sole candidate was mid-trade and the owner was watching an uptrend — so the
edge bar (§3) is explicitly UNCHANGED, the new gate adds five checks that did not exist
before, and D5 (funding is the owner's separate decision) still stands. If a future
session is tempted to relax §3 for similar reasons, that is the line that must not move.

## Q4 2026 registered trials (v2.0 — criteria fixed 2026-09-26, BEFORE any run)

Both use the Q4 budget (≤4 candidate slots; run no earlier than 2026-10-01). Neither changes
live behavior regardless of outcome; paper-only stands (D1).

**VENUE-2026Q4-1 — Rotation (and lean-7d) under achievable venue costs.** Configs UNCHANGED:
rotation = MA200, band 1.5%, 30d>0, next-day-close fills; lean-7d = config hash
`41c90fbe47eb2ab8`. Cost is the only variable, and it is exogenous (published fee
schedules), not a fitted parameter. Per-side cost levels, each = fee + 0.05% slip:
Coinbase base taker 1.25% (control), Kraken T1 taker 0.85%, Kraken T3 taker 0.43%, Kraken T3
maker 0.27%. Universe: PIT ∩ Kraken-listed-at-date if buildable from Kraken's public OHLC;
otherwise the Coinbase PIT universe, flagged "venue-cost proxy, listing mismatch unmeasured".
**Rotation passes at a tier iff ALL:** (a) full-window compounded net ≥ HODL BTC's, OR
return/max-drawdown ≥ 1.25× HODL's; (b) ≥3/4 rolling WF folds (2022–2025, 30d purge) with
positive net; (c) bootstrap 5th-pct episode expectancy > 0. **Output** = the cheapest tier
that must be reached for a pass, and the capital/volume that tier requires — this is the
input D5 was missing. Lean-7d is reported at each tier for G8/G9 context only (no pass/fail
here; its path runs through H-2026Q4-2). Predicted, stated now: rotation fails at Coinbase
base (known), and its pass/fail at T3 hinges on (a) — it will not beat HODL on raw return
in the 2023+ bull leg, so return/max-DD is the operative criterion.

**H-2026Q4-2 — Alt momentum conditional on an alt-favorable environment** (owner's
hypothesis, 2026-09-26: "alts follow a risk-on environment then run hard"). Lean-7d
(`41c90fbe47eb2ab8`, unchanged) with ONE added entry condition: **ETH/BTC daily close >
its 50-day SMA, SMA computed through t−1** (no look-ahead). The definition is pinned a
priori — standard practitioner proxy, chosen before any conditional result was seen. No
other MA length, breadth threshold, or dominance rule may be substituted after the run;
doing so is a new trial. Harness: PIT universe, episode mode, measured 2.4% RT (stress
4.8%), 30d-purged folds. **PASS iff ALL:** (a) §3 G3, G5 (N incl. this trial), G6, G7, G9
at measured cost; (b) G8 vs the better of HODL BTC / rotation; (c) CONTRAST: conditional
episode expectancy − expectancy of the SAME rule's entries rejected by the ETH/BTC
condition > 0, with bootstrap 5th-pct of the difference > 0 (this is what makes it the
owner's hypothesis rather than "lean-7d again with fewer trades"). If every criterion but
G4 passes, verdict = **INSUFFICIENT_N** (not a pass): the hypothesis continues on the
forward record (D6/K2) and G4 is re-evaluated when forward episodes accrue.
Predicted failure mode, stated now: conditioning cuts episodes ~3× (n≈30 of 91 OOS),
widening MDE ~1.7×; INSUFFICIENT_N is the most likely honest outcome.

## Decision Log

Plain-language record of every registered test and its lesson: `docs/TRIAL_HISTORY.md`
(enforced by `tests/unit/test_trial_history.py`). Machine record: `data/trials.jsonl`.

- **2026-10-11 v2.1 — Q4 trials run (git 676c251). VENUE-2026Q4-1: FAIL → kill
  condition K1(a) fires. H-2026Q4-2: VOID.** Owner asked to run both, and asked whether
  we over-index on fees "when I scale."
  **VENUE (rotation vs HODL BTC, 2018-07 → 2026-10; HODL +1,033%, Calmar 0.45, maxDD −77%):**
  no tier passes all three registered criteria. (a) return-or-Calmar PASSES at Kraken T3
  taker (+1,188%, Calmar 0.73, DD −50%) and T3 maker (+1,489%, 0.84, −48%); fails at
  Coinbase base and Kraken T1. (b) 3/4 folds passes only at T3 maker (2022 has zero
  episodes — rotation sat out the crash — and counts as non-positive; 2025 is negative at
  every taker tier). (c) bootstrap 5th-pct of 2023+ episode expectancy is NEGATIVE at every
  tier (−2.16% → −0.21% at T3 maker). The flagged 2022-fold rule does not change the
  verdict: (c) fails everywhere regardless. **Reading:** at low cost rotation beats HODL
  economically (more return, ~35% less drawdown over 8 years), but 34 OOS episodes cannot
  show it is reliably positive at the 95% bar — the binding constraint at scale is EVIDENCE,
  not fees. Per D6/K1(a), rotation is retired as a candidate for live capital; the paper
  cohort continues as free observation. Reopening requires a NEW registered hypothesis.
  **Fees (owner's question):** Coinbase cut US fees 2026-09-16; this account's live tier
  (read-only API) is "Intro": 0.90% taker / 0.50% maker, next tier at $10K 30-day volume
  or $500K assets on platform. The 1.20% "measured" cost was the retired old base tier.
  At ≈0.95%/side with slippage, current Coinbase sits between the failing Kraken T1 and old
  Coinbase tiers. fill_cost_model.py still carries 1.20% — update pending owner decision.
  **H-2026Q4-2: VOID, not FAIL.** The registration pinned lean-7d by config hash
  (thresholds only). The regression blend deleted 2026-07-03 had been generating ~83% of
  that strategy's entries: June code on today's data reproduces June exactly (241 / 91 OOS),
  today's code yields 42 / 11 — and the conditional arm had 2 OOS episodes. The run tested a
  strategy that no longer exists; nothing was learned about the owner's hypothesis. Recorded
  as `result_void` (append-only), not re-run. Two further findings: (1) the June lean-7d
  rejection was of a strategy dependent on the overfit blend; (2) the current alt rule
  takes ~5 trades/yr in replay vs 169 live paper trades in 7 weeks — replay lacks the live
  enhanced inputs, so the backtest does not represent what the live alt cohort trades.
  The forward record (D6/K2 bar: 18/25 episodes) is now the ONLY valid evidence channel for
  the alt hypothesis. K2 has not fired (a void is not a fail); K3 has not fired.
  **Process lessons:** registrations must pin a code SHA, not just a config hash; G8's
  "compounded OR Calmar" can be passed by a tiny-exposure book — use exposure-matched
  comparison in future criteria.

- **2026-09-26 v2.0 — Record corrected; deadline replaced; data capture fixed; dead
  machinery put to sleep.** Owner-approved in chat ("do it"). Triggered by the 2026-09-25
  stage1b "Decision: GO" email and the owner's question whether we are truly finding the
  edge. A deep re-read found:
  (1) **Rotation never passed §3.** Its cited stats were at the pre-measurement 0.66% RT
  cost. The measured-cost numbers sat in the same artifact since 2026-06-11 and fail G3/G4/G6
  (§1a). Rotation was the benchmark; "last candidate standing" drifted into "passed" across
  v1.3–v1.9 without anyone re-reading the gate. v1.8/D7 text calling the §3 evidence
  "already existing" is superseded by §1a. The paper cohort continues — it still proves
  plumbing (§4b) and now measures the right comparison (vs HODL BTC).
  (2) **HODL BTC is the benchmark rotation must beat**, and per-leg cost decides whether it
  can (§1a table). This reframes rotation as a drawdown overlay and makes venue/cost the
  highest-value lever — registered as VENUE-2026Q4-1 and coupled to D5.
  (3) **D6's date replaced by kill EVENTS K1–K3** (owner-approved). Motivation recorded
  honestly: rotation was +12.8% net and alts had beaten BTC in 10/12 forward episodes when
  this was decided — exactly the conditions under which a date gets extended for bad
  reasons. So the replacement is stricter where it matters: kills are events that cannot
  be un-happened, and there is no extension mechanism at all.
  (4) **Owner's alt-regime hypothesis registered (H-2026Q4-2)** with the environment
  definition pinned before any data was examined, a contrast criterion, and the likely
  INSUFFICIENT_N outcome stated in advance.
  (5) **Data capture**: `market_state_logger.py` now persists an hourly environment row
  (`data/market_state.jsonl`: BTC vs MA200, ETH/BTC vs 50d SMA, alt breadth vs BTC 7d/30d,
  top-N dominance, fear/greed), a daily compact 250-coin snapshot
  (`data/market_daily/`), and top-of-book for open paper-alt products. Screening log's
  hidden 10,000-row cap removed (75,000 per ml/config). All added to the Drive backup.
  (6) **Weekly ML, replay+adversarial, and stage1b promotion loops** gated on
  `weekly_tasks_enabled=false` — disabled, not deleted.
  (7) 26 stale agent worktrees (Mar–May, 1.1GB) removed after archiving the 4 with real
  diffs as patches (`data/task_backups/worktree_patches/`); their branches are retained.

- **2026-08-27 v1.9 — H-2026Q3-4 (activation-gated trailing stop): FAIL.** Owner asked
  why rotation carries no trailing stop, noting the pre-cohort system armed one at
  ~5-10% profit. Audit found a real gap in reasoning: the "wide fixed stop beats
  trailing" conclusion was established for the LEAN ALT strategy and INHERITED by
  rotation without ever being re-tested. Exploratory scan (12 combos) suggested
  +7pp OOS for a 13-15% trail, so it was registered as a proper trial with params
  pinned a priori to the legacy config (activate +8%, trail 13%) — no sweep, to avoid
  double-dipping on the scan that motivated it.
  Result at MEASURED 2.4% cost, 4 purged folds: **1/4 folds beat incumbent** (needed
  3), bootstrap 5th-pct −1.9% vs incumbent +1.6%, improvement −0.023pp/episode vs a
  5.16pp deflated hurdle (N=11). Cause: **only 1 trail exit in 2022-2026** — at a 13%
  trail the risk-off flip almost always fires first, so in 3 of 4 folds the two rules
  were IDENTICAL. The apparent +7pp was one 2024 trade amplified by compounding.
  **Standing conclusion:** for rotation, the regime exit already performs the
  gain-protection role a trailing stop would; adding a trail adds turnover cost
  (2.4%/round trip) without adding protection. A TIGHTER trail (5-8%) is untested and
  would need its own registered trial — sweeping widths post-hoc is the double-dipping
  this framework exists to prevent. Q3 slots remaining: 1.
  **Follow-up (same day): the "just tighten it" question is now SETTLED — see §5b.**
  Characterizing (not optimizing) the winning episodes shows the top winners dipped
  25-27% from their highs while on their way to +263% and +97%. A 5% trail would have
  cut short 15 of 25 winners; tightening is monotonically worse. No trail width helps:
  loose ones are inert (1 exit in 4 years), tight ones destroy the concentrated right
  tail the strategy depends on. This used a descriptive characterization rather than a
  parameter sweep, precisely so it did not become another fitted result.
- **2026-08-20 v1.8 — RISK-ON: track record started, and paper gate amended (D7).**
  BTC closed above the trend line for the first time since 2025-10-28 (+5.4% vs MA200,
  30d +11.5%). The rotation cohort opened its first position at 18:43 EDT: effective
  entry $72,755.36 (market $72,719 + 0.05% slippage), qty 0.0013744692, entry fee $1.20
  (1.2% measured taker), stop/target 0/0, milestone email delivered — every field
  verified against the model. Data point #1 of the fundable record. The lean cohort also
  fired 8 alt entries (rejected/observational; 7-day hold, closing ~Aug 27 — a natural
  head-to-head against BTC-only over an identical window).
  Paper gate amended per D7: >=30 closes was arithmetically unreachable (~4 years at
  7.6 episodes/yr) and perverse (a longer, better trend yields FEWER closes). Replaced
  for low-frequency candidates by the §4b fidelity gate (F1-F6). §3 edge bar untouched.
- **2026-07-04 v1.7b — Loop/data proposals triaged under the no-overengineering
  filter; 2 built, 2 rejected.** BUILT: (1) spread_logger.py — one BTC top-of-book
  snapshot per refresh; the maker-fill trial's spread history cannot be backfilled
  (first reading: top-of-book spread ≈ 1 cent). (2) Falsifier memo now emails
  itself to the owner via the daily-report path (subject flags ATTENTION NEEDED
  vs all clear) — closes the memo-nobody-reads gap with ~15 lines, no agents.
  REJECTED with reasons: hourly resolution-fidelity audit (rotation is natively
  daily in live AND backtest; the A5 gap belonged to the retired lean pipeline);
  monthly hypothesis-proposal agent (nothing to propose from 22 risk-off days —
  an idle loop is overengineering; revisit when memos accumulate substance).
  Standing rule: agent loops may observe and propose; only registered trials act.
- **2026-07-03 v1.7 — §5.8 deletions executed (the safe subset).**
  DELETED: (1) optimizer auto-apply — apply_safe_changes now unconditionally
  returns pending-review only; no flag combination can ever re-enable automated
  threshold writes; human paths (apply_single/apply_recommendation) intact;
  tests pin the disarm. (2) regression blend in quant_signal — both blend blocks
  removed (17 features / 71 samples, self-referential training); tests now assert
  the model is never consulted. Do not reintroduce learned score blends before
  500+ closed trades AND a registered-trial pass.
  DELIBERATELY KEPT (with rationale, revisit at pipeline retirement): the legacy
  multi-path signal code — it is behaviorally dead (lean prune + auto-exec off)
  but (a) serves as the baseline arm of the committed A/B gate artifacts and
  future registered trials, (b) feeds quant_path attribution for the shadow/
  gate-watch evidence stream, (c) physical removal is a large refactor of a
  1,000-line function with wide test coverage for purely cosmetic benefit.
  Exchange stop-order repricing also kept dormant (gated off; only relevant if
  live execution ever resumes, at which point it must be fixed or removed).
- **2026-07-02 v1.6e — Falsifier cycle shipped; Phase 3 loop is now LIVE.**
  scripts/falsifier_cycle.py = the weekly self-learning cycle as a DETERMINISTIC
  script (guards check, cost-honest cohort scoreboard with MDE, gate-watch verdict,
  trials-registry state, contradiction flags) → dated memo in data/decision_memos/.
  Scheduled Sundays 08:00 (SwingTrader-WeeklyMemo, scripts/register_memo_task.ps1).
  It never changes config and never trades — the anti-optimizer by construction.
  First memo: all guards green, gate verdict "flat was right," suppressed-signal
  stream flowing (3 rows in first hour). Slot-accounting rule codified: candidate
  hypotheses consume quarterly slots; AUDITs do not (they select nothing) but their
  looks still raise the G5 deflated hurdle → Q3 has 2 candidate slots remaining
  (supersedes v1.6b's "1 slot" line).
- **2026-07-02 v1.6d — Gate-watch shipped (commit 1d64d81).** Live grading of the
  risk-on gate: scripts/gate_watch_report.py (observational, n≈1 honesty note
  built in). First verdict: 22/22 watch days risk-off, BTC −3.52% over the window,
  avg fwd-7d from risk-off days −1.74% — flat was right so far. Also closed an
  instrumentation hole: lean-pruned BUYs never reached the shadow logger (stream
  empty since 06-11); the refresh loop now records them daily per coin, so the
  falsifier loop will have per-coin suppressed-signal outcomes to learn from.
- **2026-07-02 v1.6c — §4.2 fill-cost calibration SHIPPED (commit 1627505).**
  fill_cost_model.py = single source of simulated-fill costs (measured 1.20%/side
  taker + 0.05%/side slippage; maker 0.60% recorded schedule-only/unproven). Paper
  sim was 12x understated on fees; rotation tracker journaled raw prices with no
  costs. Both fixed BEFORE the first cohort trade — the track record will be net
  from entry #1. Real fills already use exchange-reported fees (verified). The
  paper→live calibration gate (§4.2) is now satisfiable by construction; remaining
  §4.2 item is matched-fill verification once live min-size fills exist. Maker-fill
  execution (halving the dominant cost per v1.6b) queued as the future execution-
  quality lever — requires live orders, so it waits for the promotion ladder.
- **2026-07-02 v1.6b — Q3 trial RESULTS (same day; artifact
  data/stage1c_q3_rotation_trials.json, trials #8-10):**
  **H-2026Q3-1 vol-managed: FAIL** — at vol_target=60% the scaler rarely binds
  (maxDD 56.8% vs required ≤46.2%; return preserved but DD reduction far short of
  the 0.75× bar). NOT re-tuning the target this quarter — that would be the
  re-rolling the G5 regime exists to prevent. 1 trial slot remains for Q3.
  **H-2026Q3-2 dual BTC+ETH: FAIL** — big in-sample gain (+431% vs +232% train)
  but OOS test half nets 13.4% vs plain 32.4%: ETH adds 2020-22 beta, not edge.
  BTC-only rotation stands.
  **AUDIT-2026Q3-3: PLATEAU** — all 9 MA/band cells within −6%/+13% of the
  incumbent's net (976-1171% vs 1040%), similar DDs, no sign flips. The risk-on
  rule the entire system leans on is robust to its parameters — the single most
  reassuring pre-registered result so far. Also noted: at MEASURED costs the
  incumbent nets +340% (vs +1040% modeled) — cost drag across ~65 round trips is
  the dominant real-world detractor; motivates future maker-fill work, NOT more
  signal variants.
- **2026-07-02 v1.6 — Q3 trials registered (above), criteria pre-fixed.** Context:
  watch phase continues (BTC ~−18% vs MA200, cohorts correctly flat); these trials
  are backtest/audit work permitted by the G5 budget and change nothing live.
  Also: 6-day outage (06-26→07-02) post-mortem — root cause was a restart from an
  editor-child shell; fixed with 30-min watchdog trigger (registered task now in
  scripts/register_dashboard_task.ps1) and ops rule: restart only via
  Start-ScheduledTask. Health check HAD detected the outage daily; the email
  alert channel failed to reach a human — alerting is not a control, self-healing is.
- **2026-06-11 v1.5b — Scheduled routines retired.** Claude routines: optimizer-audit
  (autonomous weekly "improvements" — contradicted D2), attribution-quality-guard
  (defunct 100-trade milestone), swing-trader-phase2-review + stale-data-monitor
  (orphaned) all DISABLED; security-audit kept (Mondays). In-app: Sunday optimizer/
  roadmap loop now gated by `weekly_tasks_enabled` (set false; commit 046d613) —
  previously it ran unconditionally. Kept: hourly refresh (drives paper cohorts),
  status emails, Windows DailyHealthCheck + GDrive-Backup tasks.
- **2026-06-11 v1.5 — ADJUDICATION COMPLETE: lean-7d REJECTED (8/10 gates fail).**
  Episode-level n=91 (overlap had inflated it to 178); at measured 2.4% costs
  +1.30%/trade vs MDE 4.9pp and deflated hurdle 3.7pp; bootstrap 5th-pct exp −1.8%,
  PF 0.70; **top-3-removed expectancy −1.1%** — the residual edge is three outlier
  trades. Artifact: data/stage1c_adjudication.json (trial #7).
  **Standing conclusions:** (a) rotation is the only candidate; the paper cohort is
  the path to funding; (b) the lean paper cohort is downgraded to OBSERVATIONAL —
  no promotion path without a NEW registered hypothesis clearing the (now higher)
  hurdle; (c) §5.8 deletions are UNBLOCKED — the signal pipeline has no surviving
  candidate; schedule the demolition; (d) rotation's own statistical caveat stands
  (n=34 episodes): it earns allocation as the structurally-justified null strategy
  with drawdown control, verified forward by the paper cohort — not as a mined edge.
- **2026-06-11 v1.4 — PAPER-ONLY MODE COMPLETE (owner-confirmed).** The system is now:
  live execution OFF (fail-closed), optimizer frozen with auto-lift removed, lean
  prune active. Two paper cohorts run side by side: (1) rotation cohort
  (rotation_tracker.py, source='paper_rotation', exits only on risk-off flips) —
  the fundable track record; (2) lean-7d cohort (source='paper_trade',
  LEAN_MAX_HOLD_DAYS=7 per the surviving arm). Paper exit loop now enforces the
  validated lean profile (time exit + no trailing) — previously the paper cohort
  had NO time exit (drift in the paper layer, found and fixed). Journal dedup
  scoped per cohort. "Complicated stuff asleep": legacy paths pruned by lean mode,
  deletions (§5.8) deferred until lean-7d adjudication. Next: G4 episode-level
  de-overlap + G5 deflated hurdle + G6 bootstrap in evaluate_gate, then the formal
  lean-7d vs rotation decision.
- **2026-06-11 v1.3 — Hold arms complete; candidate ranking above adopted.** 7d hold
  dominates lean (expectancy AND PF AND velocity); 30d-hold config rejected. Next
  engineering: G4 episode-level de-overlap + G6 bootstrap + MDE/G5 deflated hurdle in
  evaluate_gate, then paper-fill calibration (§4.2). Rotation paper cohort starts
  immediately — it needs no new code, only journal tagging.
- **2026-06-11 v1.2 — G1 HONEST RUN COMPLETE: lean FAILS.** PIT universe (102 monthly
  top-50 snapshots, 273 symbols, 108 with Coinbase data incl. 15 delisted):
  lean test OOS +3.047%/trade, PF 1.30 (vs +11.476% / 2.25 on the survivor universe —
  ~73% of the claimed edge was survivorship bias). Against the full gate set: FAILS
  G2 (at measured ~2.4-3% round-trip the net edge is ≈0-1.3pp), G4 (MDE at n=178 ≈
  4.7pp > the 3.0pp claim — statistically indistinguishable from zero), G6 (PF point
  estimate exactly at 1.3), G9 (edge < cost + 2pp). Legacy pipeline confirmed no-edge
  at scale: PF 0.86, n=5,137 OOS. Artifact: data/stage1c_pit_gate.json (trial #3).
  **Per the pre-registered decision tree, next candidates in the same harness:
  (1) BTC/ETH risk-on rotation (D4 #1), (2) lean hold-period arms 7d/14d/21d.**
  LEAN_ENTRY_MODE stays available for the paper cohort but carries no validated claim.
- **2026-06-11 v1.1** — D5 decided by owner: small cash balance until proven track record,
  then meaningful funding. Deliverable = verified track record.
- **2026-06-11 v1.0** — Initial freeze. Phase 0 executed as code (fail-closed exec config,
  auto-lift removed, full suite 1173 green). Four-agent review artifacts in
  `tasks/research/agent{1,2,3,4}_*.md`. Owner decision pending: D5 funding.
