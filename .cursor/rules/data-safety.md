---
description: Data safety rules — never overwrite production config or state files without backup and validation
globs: ["*.json", "data/**"]
alwaysApply: false
---

# Data Safety Rules

## Critical Files — Never Overwrite Without Backup
These files contain live trading state or configuration:
- `positions.json` — Open/closed trade journal (money at risk)
- `execution_config.json` — Auto-execution toggle, size caps, risk limits
- `live_thresholds.json` — Signal thresholds, regime configurations
- `account_config.json` — API credentials reference
- `scheduler_config.json` — Refresh intervals, cost tracking

## Rules
1. **Read before modifying** — Always read current state before editing any config JSON
2. **Preserve existing keys** — When updating a JSON config, merge changes, don't replace the entire file
3. **Never delete data files** — Log files, audit trails, and snapshots in `data/` are append-only
4. **API keys live in .env** — Never hardcode credentials in JSON or Python files
5. **Validate after editing** — If you modify execution_config.json or live_thresholds.json, verify the app can still parse it
6. **No test data in production files** — Tests must use fixtures/mocks, never write to real positions.json or config files
