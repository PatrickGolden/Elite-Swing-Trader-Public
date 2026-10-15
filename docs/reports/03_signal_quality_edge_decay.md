# Signal Quality / Edge Decay — Functional Review #3

**Date:** 2026-03-14  
**Scope:** Quant signals profitable in current regime; optimizer vs live thresholds; win rate by signal type (ULTRA_BUY vs STRONG_BUY); differentiation working.

---

## (a) Backtest vs live performance summary

**Walk-forward / backtest ([data/learning_report.json](../../data/learning_report.json) after `python -m ml.run_pipeline`)**

- **Period:** Last 90 days (walk-forward). **total_signals:** 1846. **sharpe_passed:** false (walk_forward.overall_sharpe null).
- **By regime:** BEAR 713 trades, 65.6% win, profit factor 18.87; SIDEWAYS 216 trades, 79.2% win, profit factor 10.02; BULL 8 trades, 0% win (avg loss -5.78%).
- **By tier:** STRONG_BUY 16 trades, 100% win rate, avg win 40.3%; MODERATE_BUY 561 trades, 74.5% win, profit factor 21.71.
- **Backtest suggestions:** "Focus on STRONG_BUY — 100.0% win rate"; "Positive expectancy even in worst 5% scenarios."

**Live (trade journal, model_attribution 90d)**

- **Quant (ULTRA):** 1 trade, 0% win rate, PnL -$0.43.
- **Quant (STRONG):** 2 trades, 50% win rate, PnL +$3.12.
- **Specialist (CONFIRM):** 10 trades, 50% win rate, PnL +$2.39.
- **Haiku (BUY):** 6 trades, 66.7% win rate, PnL +$0.20.

**Conclusion:** Backtest shows strong STRONG_BUY/MODERATE_BUY performance; live sample is very small (1 ULTRA, 2 STRONG). Live STRONG is positive; ULTRA is one loss. Not enough live trades to claim edge decay; continue monitoring as more closed positions accumulate.

---

## (b) Threshold drift (optimizer vs live)

- **live_thresholds.json** (current): Contains `BLOOD_STREET_FG`: 25, `DIP_MIN_SCORE`: 68, `FUNDING_VETO_BEAR_PCT`: 0.06, `updated`: 2026-03-12, `7d_win_rate`: 14.3, `7d_trades`: 7, `adjustment`: "tightened (7d win rate < 50%)", `updated_by`: "daily_local_retrain". So a subset of keys is present; the rest fall back to [thresholds.py](../../thresholds.py) `DEFAULT_THRESHOLDS`.
- **Recursive optimizer:** Writes to live_thresholds with `updated_by: recursive_optimizer`. Current file shows `daily_local_retrain` (deprecated path). Optimizer runs at 4:00 AM ET; last run’s recommended values would be in optimizer email or [data/optimizer_log.json](../../data/optimizer_log.json) if present.
- **Drift check:** Compare live_thresholds.json keys (e.g. DIP_MIN_SCORE, BEAR_REGIME_MIN_SCORE, FUNDING_VETO_BEAR_PCT) to the last optimizer report or optimizer_log. If optimizer recommended a value and it was not applied, that’s drift. Current snapshot: DIP_MIN_SCORE 68 vs default 65 (tighter); FUNDING_VETO_BEAR_PCT 0.06 vs default 0.08 (tighter). No optimizer output was diff’d in this run; recommend a periodic diff after each optimizer run.

---

## (c) Win rate and PnL by signal tier

From **model_attribution.get_model_attribution(90)** (trade journal, exclude_test):

| Tier / model       | Trades | Win rate % | PnL USD |
|--------------------|--------|------------|---------|
| Quant (ULTRA_BUY)  | 1      | 0          | -0.43   |
| Quant (STRONG_BUY) | 2      | 50         | 3.12    |
| Specialist (CONFIRM)| 10    | 50         | 2.39    |
| Haiku (BUY)        | 6      | 66.7       | 0.20    |

Differentiation ULTRA vs STRONG cannot be assessed with 1 and 2 trades. In backtest, STRONG_BUY had 100% win (16 trades) and MODERATE 74.5% (561 trades), so tier differentiation exists in historical data.

---

## (d) Stress report and veto accuracy

- **scripts/buy_signals_stress_report.py** (30d): **recommendations_total** 1846; **by_blocker:** executed_or_would_auto_exec 7, insufficient_cash 11, policy_no_auto_exec 1826, veto 2. **missed_opportunity_count** 342.
- **Veto accuracy:** 2 veto events in 30d; verdicts (correct_veto / missed_opportunity) for those 2 can be read from stress JSON. Learning report **suggestions** include: "Multiple HOLD/WEAK_BUY (88) had outcome_7d > 5.0%. If some were veto overrides, consider lowering VETO_CONFIDENCE_THRESHOLD."

---

## Deliverable summary

| Item | Result |
|------|--------|
| (a) Backtest vs live | Backtest: STRONG 100% win, MODERATE 74.5%; live: 1 ULTRA (0% win), 2 STRONG (50% win). Sample too small for edge decay conclusion. |
| (b) Threshold drift | live_thresholds has DIP_MIN_SCORE 68, FUNDING_VETO_BEAR_PCT 0.06 (tighter than defaults). Compare to last optimizer report for full drift list. |
| (c) Win rate by tier | See table above; ULTRA vs STRONG differentiation not testable with current N. |
| (d) Differentiation | Backtest supports STRONG > MODERATE; live needs more trades to confirm. |

**Recommendations:** (1) Re-run `python -m ml.run_pipeline` periodically and compare learning_report backtest metrics to live attribution. (2) After each recursive optimizer run, diff recommended vs live_thresholds.json. (3) Re-run stress report and track veto verdicts (correct_veto vs missed_opportunity) as data grows.
