---
description: Data safety rules for production config and state files
globs: ["*.json", "data/**"]
---

# Data Safety Rules

## RULE 0 — ASK BEFORE DESTROYING (overrides everything below)

**No destructive or system-harming action may be taken without Patrick's explicit
approval in chat, every time.** Approval for one action never carries to the next,
and a task instruction like "clean this up", "do it", or "fix the repo" is NOT
approval to destroy anything — it authorizes proposing the destruction.

Actions that REQUIRE explicit approval first:
- Deleting or truncating **any** file under `data/`, `positions.json`, any
  `*.json` config, any `.bak` / `.bak.gz` / archive, or any log
- `git` history rewrites, force-push, `reset --hard`, `clean -fd`, stash drop
- Dropping/altering tables in `data/trade_journal.db`
- Deleting or disabling Scheduled Tasks, or killing long-running processes that
  hold state (the dashboard may be restarted via `Start-ScheduledTask` — that is
  routine and pre-approved)
- Turning **on** live execution (`auto_execution_enabled`), lifting the optimizer
  freeze, or re-enabling `weekly_tasks_enabled`
- Rotating, moving, or compressing archives in a way that removes the original

When such an action seems warranted: **stop, state exactly what would be destroyed
(file, row count, date span), state why, and wait.** Proposing is always allowed.

### Verify BEFORE destroying, never after
Learned 2026-08-08: an external review recommended deleting 296MB of "stale
backups" in `data/`. Those `.bak` files were the ONLY copy of pre-June history
(`execution_audit.jsonl.bak` = 649,948 rows Mar 13→Jun 4; `refresh_snapshots.jsonl.bak`
= 12,866 rows Mar 5→Jun 11) — none of it in the live files. Deleting would have
been permanent, silent data loss.

Therefore, before any deletion:
1. **Inspect the target** — row count, date span, and whether its contents exist
   anywhere else. A file named `.bak`/`.old`/`.tmp` is NOT evidence it is redundant.
2. **Prove redundancy or preserve** — if the data is unique, compress (`.gz`) or
   archive it; never delete it. Compression is not a licence to skip approval.
3. **Full integrity check runs BEFORE the irreversible step** — parse every row,
   confirm counts AND date spans match. A line-count match alone is insufficient.
4. **Confirm it is backed up** — `scripts/backup_to_gdrive.ps1` must already cover
   the file, or the copy in Google Drive must be verified present, first.

### Non-destructive alternatives are the default
Prefer: compress over delete, rename over remove, add-a-new-file over overwrite,
scratch/temp directories over touching production (see `scripts/rotation_dryrun.py`
— it exercises the real code path against a scratch journal and leaves production
byte-identical). Tests and dry runs must never write to real state files.

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
3. **Never delete data files** — Log files, audit trails, and snapshots in `data/` are
   append-only. Rotated archives (`*.bak`, `*.bak.gz`) are DATA, not garbage: they hold
   history trimmed out of the live file. See RULE 0.
4. **API keys live in .env** — Never hardcode credentials in JSON or Python files
5. **Validate after editing** — If you modify execution_config.json or live_thresholds.json, verify the app can still parse it
6. **No test data in production files** — Tests must use fixtures/mocks, never write to real positions.json or config files
