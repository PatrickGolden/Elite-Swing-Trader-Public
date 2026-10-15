---
description: Run the full test suite with coverage and report results
---

Run the full test suite and report results:

```
python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

Summarize:
- Total tests passed/failed/skipped
- Any failures with file:line and error message
- If all pass, confirm the count matches or exceeds 636
