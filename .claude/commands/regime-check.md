---
description: Check current market regime vs active thresholds — catch mismatches and transitions
---

Read the following files and produce the report below. Do not execute any trades, modify any files, or change any thresholds.

**Files to read:**
1. `ai_recommendations_v2.json` — get the 10 most recent entries, extract `market_regime`, `timestamp`, and `confidence` (or `final_score`)
2. `live_thresholds.json` — get `BULL_REGIME_MIN_SCORE`, `BEAR_REGIME_MIN_SCORE`, `SIDEWAYS_REGIME_MIN_SCORE`, `DIP_MIN_SCORE`, `BULL_SUSPENDED`, and any regime multipliers
3. `data/monitoring_expectancy.json` — get per-regime expectancy and win rate trends
4. `model_performance.json` — get regime-specific win rates and profit factors from recent predictions with outcomes
5. `data/signal_history.json` — get last 10 signal events and their regime labels

**Report format:**

### Market Regime Check

**Current Regime:** (most recent entry's `market_regime` and timestamp)
**Confidence:** (score or confidence from most recent recommendation)

**Active Thresholds for Current Regime:**
- BULL_REGIME_MIN_SCORE / BEAR_REGIME_MIN_SCORE / SIDEWAYS_REGIME_MIN_SCORE (whichever applies)
- DIP_MIN_SCORE
- BULL_SUSPENDED status

**Regime Consistency (last 10 signals):**
- List the regimes seen in the last 10 signals
- Flag `STABLE` if all 10 agree, `OSCILLATING` if 3+ different regimes appear, `TRANSITIONING` if last 3 differ from prior 7

**Regime Performance (from model_performance.json, last 90 days with outcomes):**
| Regime | Win Rate | Profit Factor | Total Trades |
- Flag SIDEWAYS if WR < 45% (structurally unprofitable)
- Flag BULL if WR < 40%

**Threshold Alignment:**
- Are score floors appropriate? Compare to regime WR:
  - If SIDEWAYS WR < 45%: is SIDEWAYS_REGIME_MIN_SCORE >= 68? (should be — lower floor in weak regime is dangerous)
  - If BEAR WR > 60%: is BEAR_REGIME_MIN_SCORE too high (missing edge)?
- Note any recently-relaxed thresholds from `data/monitoring_expectancy.json`

**Red Flags:**
- BULL_SUSPENDED = true while we have open BULL positions
- SIDEWAYS threshold recently relaxed despite negative expectancy
- Regime oscillating (instability = higher false signal rate)

End with: "No files were modified. This is a read-only check."
