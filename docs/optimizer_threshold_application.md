# Optimizer threshold application (cycle 16 baseline)

Reference for applying or deferring loss-based optimizer recommendations. See the plan in `.cursor/plans/` for full assessment.

## Baseline backtest (pre-change)

Run: `python -m ml.run_pipeline` (executed 2026-03-19). Report: `data/learning_report.json`.

| Regime   | Trades | Win rate | Avg win % | Avg loss % | Profit factor |
|----------|--------|----------|-----------|------------|---------------|
| BULL     | 8      | 0.0%     | 0         | -5.78      | 0.0           |
| BEAR     | 1002   | 73.0%    | 23.23     | -3.19      | 19.73         |
| SIDEWAYS | 508    | 74.4%    | 13.08     | -11.25     | 3.38          |

Sharpe passed: No. Use this baseline to compare after applying threshold changes.

---

## Apply a recommendation

**By parameter and value (writes to live_thresholds.json):**

```powershell
python -c "from recursive_optimizer import apply_single; apply_single('SIDEWAYS_REGIME_MIN_SCORE', 75)"
python -c "from recursive_optimizer import apply_single; apply_single('BEAR_REGIME_MIN_SCORE', 78)"
```

**By recommendation ID (also records in recommendations log):**

```powershell
python -c "from recursive_optimizer import apply_recommendation; apply_recommendation('rec-16-SIDEWAYS_REGIME_MIN_SCORE-75')"
python -c "from recursive_optimizer import apply_recommendation; apply_recommendation('rec-16-BEAR_REGIME_MIN_SCORE-78')"
```

---

## Defer a recommendation (hide from future emails)

```powershell
python -c "from recursive_optimizer import defer_recommendation; defer_recommendation('rec-16-BEAR_REGIME_MIN_SCORE-78')"
```

---

## Validation steps (required before applying)

1. **Before applying:** Run `python -m ml.run_pipeline` and note backtest metrics (by_regime, by_tier, sharpe_passed). Stored in `data/learning_report.json`.
2. **After applying:** Re-run `python -m ml.run_pipeline` and compare SIDEWAYS/BEAR win rate and trade count to baseline.
3. Treat recommendations as **hypotheses to test**; the optimizer’s +3 loss-based rule is heuristic, not counterfactual.

---

## Cycle 16 recommendations (summary)

| Recommendation       | Evidence              | Suggested action                                      |
|----------------------|-----------------------|--------------------------------------------------------|
| SIDEWAYS 72 → 75      | 17/26 losses SIDEWAYS | Validate with pipeline; reasonable to apply first.     |
| BEAR 75 → 78          | 3/26 losses BEAR      | Defer or apply only after SIDEWAYS and/or more data.   |
