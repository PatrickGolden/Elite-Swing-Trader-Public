---
description: Check trading system health — scheduler, recent signals, model performance
---

Gather a system health snapshot by reading these files:

1. `scheduler_config.json` — Current refresh interval, last run, cost tracking
2. `execution_config.json` — Auto-execution enabled? Size caps, risk limits
3. `live_thresholds.json` — Current signal thresholds and regime config
4. `model_performance.json` — Win rates, expectancy by regime
5. `data/signal_history.json` — Last 5 signals generated
6. `ai_recommendations_v2.json` — Current consensus buy signals

Present a concise dashboard:
- Scheduler: running? interval? last refresh?
- Execution: auto-exec on/off? risk limits?
- Signals: any active buy signals? strengths?
- Performance: overall win rate, expectancy
- Any anomalies or warnings
