---
name: attribution-quality-guard
description: RETIRED 2026-06-11: 100-trade data-collection milestone defunct (live trading retired at 85 closes; paper-only mode)
---

Run the attribution quality check script and report results:

```
python scripts/attribution_quality_check.py
```

This script:
- Reads `positions.json` and filters to closed non-test trades post 2026-04-02
- Checks all 6 required attribution fields (fear_greed, funding_rate, price_change_24h, price_change_7d, price_change_30d, edge_score)
- Compares against prior run in `data/attribution_quality.json` for trend detection
- Writes updated results to `data/attribution_quality.json`
- Exits 0 (clean), 1 (error), or 2 (alert condition triggered)

Report the summary line from stdout and flag any alerts from stderr. Do NOT modify positions.json or any trading state.