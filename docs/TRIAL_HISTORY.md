# Trial History — every registered test, what it found, what it taught

The machine record is `data/trials.jsonl` (append-only: registration rows written
BEFORE each run, result rows after). Full numbers live in each trial's artifact under
`data/`. This file is the human record: one entry per test, in plain language, with the
lesson it left behind. **Every trial with a result in `trials.jsonl` must have an entry
here** — `tests/unit/test_trial_history.py` fails otherwise.

Costs in each entry are the basis that trial ran on. The fee basis changed over time:
0.66% / 0.96% modeled round trip (June) → 2.4% measured (old Coinbase base tier,
1.20%/side) → **1.9% (0.90%/side, account's live tier since the 2026-09-16 Coinbase fee
cut, adopted 2026-10-11)**. A verdict is never re-graded silently on a new basis.

---

## At a glance

| Date | Trial | Question | Verdict |
|---|---|---|---|
| 2026-06-11 | lean-full gate `cef48ff0` | Does the lean alt strategy have an edge? (30 hand-picked coins) | PASS — later shown to be survivorship bias |
| 2026-06-11 | PIT gate `cef48ff0` | Same strategy on an honest point-in-time universe | **FAIL** |
| 2026-06-11 | Rotation benchmark (BTC / ETH) | What does simply riding the BTC trend return? | BENCHMARK |
| 2026-06-11 | Hold-period arms 7/14/21d | Does a shorter hold help the alt strategy? | ARM (7d best) |
| 2026-06-11 | Adjudication `41c90fbe` | Lean-7d, counted honestly, vs every gate | **FAIL** (8 of 10 gates) |
| 2026-07-02 | H-2026Q3-1 | Volatility-scaled BTC rotation | **FAIL** |
| 2026-07-02 | H-2026Q3-2 | BTC + ETH rotation 50/50 | **FAIL** |
| 2026-07-02 | AUDIT-2026Q3-3 | Is the trend rule fragile to its settings? | PLATEAU (robust) |
| 2026-08-27 | H-2026Q3-4 | Trailing stop on the BTC rotation | **FAIL** |
| 2026-10-11 | VENUE-2026Q4-1 | Does rotation beat holding BTC at cheaper fees? | **FAIL** → kill condition K1(a) |
| 2026-10-11 | H-2026Q4-2 | Do alts outperform in an alt-favorable environment? | **VOID** (strategy no longer existed) |

---

## 2026-06-11 — lean-full gate · `cef48ff0a52bdc0d` · PASS (misleading)
**Tested:** the lean alt-momentum strategy (BTC risk-on gate, momentum entries, 24% fixed
stop, 30-day time exit) on a 30-coin universe. **Result:** +11.5%/trade, n=135 out of sample.
**What it really showed:** the universe was 30 coins chosen in 2026 — coins that had
already survived. **Lesson:** a backtest on today's survivors is not evidence. Every later
test uses the point-in-time universe, including coins that died or were delisted.

## 2026-06-11 — PIT gate · `cef48ff0a52bdc0d` · FAIL
**Tested:** the identical strategy on a point-in-time top-50 universe (CMC monthly
snapshots 2018–2026 ∩ Coinbase-listed, delisted coins included). **Result:** +3.0%/trade,
PF ~1.30, n=178 — below the noise band and below real costs. **Lesson:** ~73% of the
"edge" was survivorship bias. The honest universe is now gate G1.

## 2026-06-11 — Rotation benchmark · `76fd87e8` (BTC), `3b291adc` (ETH) · BENCHMARK
**Tested:** long BTC (or ETH) while price > 200-day average +1.5% and 30-day change > 0,
else cash. **Result (at the then-modeled 0.66% cost):** BTC +3.52%/episode, PF 2.52, n=34
out of sample. **Lesson, learned late (2026-09-26):** these numbers were at the
pre-measurement cost. At real cost the same run fails three gates — and the benchmark had
quietly drifted into being called a "pass." **Re-grade every standing conclusion when an
input (like cost) changes — benchmarks included.**

## 2026-06-11 — Hold-period arms · `0e0b05f5` (7d), `d60e2d0a` (14d), `956c8e29` (21d) · ARM
**Tested:** the lean strategy with 7/14/21-day time exits. **Result:** +3.7% / +2.6% /
+2.1% per trade (signal-level, n=178). **Lesson:** shorter is better, matching the
academic finding that crypto momentum decays into reversal within weeks. 7d went forward.

## 2026-06-11 — Adjudication · `41c90fbe47eb2ab8` · FAIL (8 of 10 gates)
**Tested:** lean-7d with overlapping trades collapsed into independent "episodes," at the
measured 2.4% cost, against the full gate set. **Result:** n=91 episodes, +1.3%/episode vs
a noise band (MDE) of 4.9pp; bootstrap 5th percentile −1.8%; removing the top 3 winners
turns it negative (−1.1%). **Lessons:** (1) count independent bets, not trades — same-day
entries win or lose together; (2) an edge that disappears without its 3 best trades is 3
lottery tickets. **Postscript (2026-10-11):** ~83% of this strategy's entries were created
by the regression-score blend later deleted as overfit (see H-2026Q4-2) — the rejected
strategy partly depended on an overfit component.

## 2026-07-02 — H-2026Q3-1 · volatility-managed rotation · FAIL
**Tested:** BTC rotation with position size scaled to a 60% volatility target.
**Criteria:** max drawdown ≤ 0.75× plain rotation AND return ≥ 0.8×. **Result:** return
held up but drawdown fell short of the cap in all four cells (e.g. 45.6% vs cap 38%).
**Lesson:** a 60% vol target rarely binds for BTC — it barely changes the position.

## 2026-07-02 — H-2026Q3-2 · dual BTC + ETH rotation · FAIL
**Tested:** 50/50 BTC and ETH, each with its own trend rule. **Result:** great in-sample
(+431% vs +232% at measured cost) but out of sample +13.4% vs plain BTC's +32.4%.
**Lesson:** ETH added beta, not edge; in-sample wins are not evidence.

## 2026-07-02 — AUDIT-2026Q3-3 · trend-rule robustness grid · PLATEAU
**Tested:** moving average 180/200/220 × band 1.0/1.5/2.0%. **Result:** all 9 cells within
−6%/+13% of the incumbent, no sign flips. **Lesson:** the rule is not a fragile
curve-fit — its weakness is cost and sample size, not its settings. (Audits don't use a
trial slot but do raise the multiple-testing hurdle.)

## 2026-08-27 — H-2026Q3-4 · trailing stop on rotation · FAIL
**Tested:** activate at +8%, trail 13% (the legacy settings, pinned before running).
**Result:** beat the plain rule in 1 of 4 folds (needed 3); improvement −0.02pp/episode vs
a 5.2pp hurdle. Only ONE trailing exit fired in 2022–2026 — the trend exit nearly always
fires first. **Follow-up (STRATEGY §5b):** the biggest winners dipped 25–27% on the way to
+263% and +97%; a 5% trail would have cut 15 of 25 winners. **Lesson:** loose trails are
inert, tight trails destroy the right tail the strategy lives on. Settled by
characterizing the winners, not by sweeping trail widths (which would be double-dipping).

## 2026-10-11 — VENUE-2026Q4-1 · rotation vs holding BTC by fee tier · FAIL → K1(a)
**Tested:** the unchanged BTC rule at four per-side costs vs simply holding BTC,
2018-07 → 2026-10 (HODL: +1,033%, max drawdown −77%). **Criteria (all three per tier):**
(a) beat HODL on total return or on Calmar ×1.25; (b) 3 of 4 yearly folds positive;
(c) bootstrap 5th percentile of 2023+ episode returns > 0.

| Cost/side | Rotation total | Max DD | (a) | (b) | (c) boot 5th pct |
|---|---|---|---|---|---|
| 1.25% Coinbase old base | +336% | −62% | ✗ | 2/4 ✗ | −2.16% ✗ |
| 0.85% Kraken T1 | +640% | −56% | ✗ | 2/4 ✗ | −1.36% ✗ |
| 0.43% Kraken T3 | **+1,188%** | **−50%** | ✓ | 2/4 ✗ | −0.53% ✗ |
| 0.27% Kraken T3 maker | **+1,489%** | **−48%** | ✓ | 3/4 ✓ | −0.21% ✗ |

**Lessons:** (1) at low cost the trend rule beats holding BTC economically — more return,
about a third less drawdown — but 34 out-of-sample episodes cannot prove it is reliably
positive: **at scale the binding constraint is evidence, not fees**; money lowers fees but
cannot create more trend cycles. (2) Criterion design: a fold with zero trades counts as a
failure, which penalizes rotation for sitting out the 2022 crash — flagged before the run;
it did not change this verdict ((c) fails at every tier), but future fold criteria should
treat a no-trade fold as neutral. **Consequence:** kill condition K1(a) fired — rotation is
retired as a live-capital candidate; the paper cohort continues as observation.
Artifact: `data/stage1c_venue_2026q4_1.json`.

## 2026-10-11 — H-2026Q4-2 · alts conditional on ETH/BTC > 50-day average · VOID
**Tested (intended):** the owner's hypothesis — alt momentum works when the environment
favors alts, defined a priori as ETH/BTC above its 50-day average (computed through the
prior day). **What happened:** the registration pinned the strategy by its *settings*
hash. The regression-score blend deleted on 2026-07-03 had been producing ~83% of the
strategy's entries: June's code on today's data reproduces June exactly (241 trades / 91
out of sample); today's code gives 42 / 11, leaving 2 conditional episodes. The run
graded a strategy that no longer existed. Recorded `result_void`; not re-run.
**Lessons:** (1) **a config hash is not a strategy** — pin the code version too, and have
the driver check it reproduces the registered baseline before grading; (2) a result
wildly off baseline is a defect signal before it is a finding; (3) the daily backtest
takes ~5 alt trades a year while the live paper cohort took 169 in 7 weeks — the replay
lacks the live inputs, so **the forward paper record is now the only valid evidence for
this hypothesis** (bar: 25 independent episodes; 18 by 2026-10-11); (4) "compounded return
OR Calmar" can be passed by a tiny-exposure book — compare exposure-matched next time.
Artifact: `data/stage1c_alt_regime_2026q4_2.json`.

---

## What the tests taught us, overall

1. **Survivorship bias manufactured most of the original alt edge** (~73%).
2. **Count independent bets.** Same-day entries are one bet, not dozens.
3. **At a small account, fees decide; at scale, sample size decides.**
4. **Benchmarks are conclusions too** — re-grade them when costs or code change.
5. **A strategy is settings + code + data.** Pin all three, or you test something else.
6. **Overfit parts hide inside strategies** — the deleted blend carried 83% of one.
7. **Simple rules are robust to their settings** but that does not make them profitable.
8. **Pre-register, run once, and void rather than retry.** The verdicts above are only
   trustworthy because none of them could be re-run until they said something nicer.

## Adding a trial
Register in `data/trials.jsonl` before running (criteria, pinned config, code SHA,
`run_not_before`), run once with a committed driver, record the result row, then add an
entry here — plain language, the numbers that decided it, and the lesson.
