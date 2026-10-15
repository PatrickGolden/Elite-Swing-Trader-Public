---
description: Testing standards and patterns — layered test architecture, conftest isolation, Windows caveats
globs: ["tests/**"]
alwaysApply: false
---

# Testing Rules

## Directory Structure
Tests are organized by type, not by module:
- `tests/unit/` — Signal generation, sizing, regime, rules (fast, isolated)
- `tests/integration/` — API routes, backtester, trade journal, data fetching
- `tests/execution/` — TWAP, order tracking, reconciliation
- `tests/stress/` — Buy signal stress, edge cases

## Conftest Isolation (Critical)
- `app.py` creates 8 global singletons + 7 locks on import
- Conftest must provide autouse isolation fixtures for ALL mutable globals
- Check `'app' in sys.modules` before resetting — most unit tests don't need Flask
- Use lazy app import pattern to avoid paying import cost unnecessarily

## Windows-Specific
- Use pytest's `tmp_path` fixture, never `tempfile.mkstemp` (leaks file descriptors on Windows)
- pytest-xdist works via subprocess spawning (no fork needed) — 24 workers, ~8 seconds full suite
- `PROJECT_ROOT` path must account for test directory depth (`.parent` chain length)

## General
- `gc.collect()` after every test is cheap (~1ms) — prevents memory accumulation
- Run `pytest tests/unit/ -x -q` for fast feedback during development
- Run full suite with `python -m pytest tests/ -x -q` before committing
- Currently 636+ tests passing — never let this regress
- New tests go in the appropriate directory by type
- Use existing factories in `tests/factories.py` for test data
- Never write to production JSON files from tests
- Any change to price fetching, caching, or exit monitoring MUST include/update tests in `tests/unit/test_price_sanity_guards.py`
