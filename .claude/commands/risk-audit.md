---
description: Audit portfolio risk — open positions, heat, stop distances, regime exposure
---

Read the following files and produce the report below. Do not execute any trades, modify any files, or change any thresholds.

**Files to read:**
1. `positions.json` — filter to entries where `status == "OPEN"`
2. `execution_config.json` — get `total_capital_usd` (or `paper_capital_usd` if in paper mode) and `auto_execution_enabled`
3. `live_thresholds.json` — get `BULL_POSITION_SIZE_MULT`, `BULL_SUSPENDED`, and any `MAX_PORTFOLIO_RISK_PCT` or `MAX_POSITION_RISK_PCT`
4. `data/monitoring_expectancy.json` — get regime expectancy for open position regimes
5. `model_performance.json` — check recent regime win rates for open positions

**Report format:**

### Open Positions Risk Audit

**Account:** `auto_execution_enabled` status, total capital

**Open Positions Table:**
| Coin | Signal | Regime | Entry | Stop% | Capital $ | Risk $ | WR% |
For each open position compute:
- `risk_$` = `capital_usd * stop_loss_pct / 100`  (use `current_stop_pct` or `stop_loss_pct` field)
- `WR%` = regime win rate from model_performance.json predictions (BULL/BEAR/SIDEWAYS/VOLATILE filter, last 90 days with outcomes)

**Portfolio Heat:**
- Total risk $ = sum of all risk_$ values
- Total capital = from execution_config.json
- Heat % = total_risk_$ / total_capital_usd * 100
- Flag: `APPROACHING LIMIT` if heat > 4%, `AT LIMIT` if heat > 6%

**Regime Exposure:**
- Break down open position capital by regime (BULL / BEAR / SIDEWAYS / VOLATILE)
- Note: BULL positions have `BULL_SUSPENDED` flag status

**Conviction Check:**
- Flag any open positions where regime WR < 40% — name the coin and regime
- Flag any positions with stop > 8% distance (high risk per unit)

**BULL Suspended Status:**
- If `BULL_SUSPENDED` is true in live_thresholds.json: warn that new BULL entries are blocked

End with: "No files were modified. This is a read-only audit."
