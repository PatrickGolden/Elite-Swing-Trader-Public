---
description: Core coding principles for the swing-trader project
globs: ["**/*.py"]
---

# Coding Standards

## Principles
- **Simplicity First** — Make every change as simple as possible. Impact minimal code.
- **No Laziness** — Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact** — Changes should only touch what's necessary. Avoid introducing bugs.

## Python Conventions
- Python 3.14+ features are available
- Flask for web dashboard (app.py, port 5000)
- CCXT for exchange abstraction
- JSON files for state persistence (not a database)
- Use `python-dotenv` for environment variables

## Code Quality
- No magic numbers in trading logic — use named constants or config values
- Signal pipeline must remain deterministic — no randomness in scoring/ranking
- Error handling at system boundaries (API calls, file I/O), not internal functions
- Prefer explicit over clever — trading code must be auditable
