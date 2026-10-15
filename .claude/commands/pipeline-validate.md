---
description: Dry-run the signal pipeline for a single coin — trace every step, no execution
---

Accept a coin symbol as the argument (e.g. `/pipeline-validate BTC`). If no argument is provided, use the most recently recommended coin from `ai_recommendations_v2.json`.

Read the following files. Do not execute any trades, modify any files, or call any APIs.

**Files to read:**
1. `ai_recommendations_v2.json` — find the most recent entry for the requested coin symbol (match on `symbol` or `coin_id`)
2. `live_thresholds.json` — get active score floors, regime multipliers, DIP_MIN_SCORE, BULL_SUSPENDED
3. `data/signal_history.json` — find the coin's last 3 signal events
4. `model_performance.json` — get regime win rate for the coin's last signal regime
5. `positions.json` — check if coin already has an open position

**Report format (DRY RUN — no execution):**

### Pipeline Validation: [SYMBOL]

**Signal Data (most recent recommendation):**
- Timestamp, price, volume_24h
- RSI, momentum indicator, Bollinger Band position
- Fear & Greed index at signal time
- Market regime + confidence

**Score Breakdown:**
Reconstruct the final_score from component fields available in the recommendation:
- RSI contribution (field: `rsi_score` or similar)
- Momentum contribution
- Volume contribution
- Sentiment/Fear-Greed contribution
- Bollinger contribution
- **Total score** vs threshold for current regime

**Signal Path:**
- Which path triggered: dip / blood / pullback / momentum / default
- Explain WHY that path fired (e.g. "RSI < 35 triggered dip path")

**Threshold Gate:**
- Regime: [BULL/BEAR/SIDEWAYS/VOLATILE]
- Required score: (from live_thresholds.json for this regime)
- Actual score: (from recommendation)
- Margin: score - threshold (positive = cleared gate, negative = blocked)
- BULL_SUSPENDED check if regime is BULL

**Veto Check:**
- Was a veto logged in signal_history? What was the reason?
- Would any current veto conditions apply? (check `veto_decision` field in recommendation)

**Position Check:**
- Is there already an open position for this coin? If yes: size, entry price, current stop

**Size Estimate (if no open position):**
- Kelly fraction from recommendation (`kelly_fraction` or `position_size_pct` field)
- Regime multiplier from live_thresholds.json
- Estimated position size % of portfolio

**Verdict:**
- `WOULD EXECUTE` — score cleared gate, no veto, no duplicate position, BULL not suspended
- `WOULD HOLD` — and the specific reason (gate missed / veto / duplicate / BULL suspended)

End with: "No files were modified. This is a read-only dry run."
