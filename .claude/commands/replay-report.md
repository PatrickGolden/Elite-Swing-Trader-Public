---
description: Summarize latest historical replay results and optimizer learnings
---

Analyze replay and optimizer state by reading:

1. `data/monitoring_expectancy.json` — Backtest expectancy tracking
2. `model_performance.json` — Attribution data, win probability by regime/path/coin
3. `data/refresh_snapshots.jsonl` — Recent refresh snapshots (last 10 lines)

Report:
- Latest replay results: date range, coins tested, outcomes
- Optimizer learning: which parameters shifted? In what direction?
- Expectancy trend: improving, stable, or degrading?
- Any regime-specific insights (e.g., "Bear regime win rate dropped")
- Recommendations for parameter adjustments if warranted
