# Auto-execution “turned off by itself” — incident record

**Purpose:** Preserve root cause and fixes so if this happens again, you can diagnose quickly without relying on chat history.

## Symptoms

- Dashboard shows **auto-execution OFF** while you expected it **ON**.
- Live `python app.py` logs may show **`Auto-exec: enabled=False`** during refresh.
- The dev server log may show **no** `POST /api/execution-config` when the flag flipped.

## Important distinction

- Refresh / scheduler **reads** `execution_config.json` via `get_execution_config()`. The log line `Auto-exec: enabled=…` reflects **disk state**, not “refresh disabled it.”
- **Persisting** the toggle goes through `set_execution_config()` → normally **`POST /api/execution-config`** (with disable contract when turning OFF).

## Root cause (2026-03-23): pytest clobbering live JSON

Integration tests in `tests/integration/test_api_routes.py` used the Flask **test client**, which still called real `app.set_execution_config` against the **repo** `execution_config.json` when `EXECUTION_CONFIG_FILE` was **not** monkeypatched.

- **`test_execution_config_post`** posted a valid **disable** (`auto_execution_enabled: false` + contract) → wrote **OFF** to your real config.
- **`test_execution_config_post_stale_position_rule`** posted stale-rule updates → **rewrote** the real file (risk of clobbering / racing with the dashboard).

Pytest does **not** hit the dev server’s TCP port, so **no Werkzeug POST lines** appear on a separately running `python app.py` — easy to mistake for a “ghost” disable.

## Fix (in repo)

- Both tests now require the **`temp_execution_config`** fixture (redirects `EXECUTION_CONFIG_FILE` + sidecar under pytest `tmp_path`).
- See `tasks/lessons.md` (testing bullet on execution config isolation).

## Other causes (if tests are ruled out)

1. **Git / sync / manual edit** of `execution_config.json`.
2. **POST to another process** that shares the same repo path (different terminal, port, or machine + synced folder).
3. **Not applicable:** scheduler turning auto-exec off (no code path writes the flag there).

## Diagnostics next time

1. **Tail** `data/execution_config_audit.jsonl` (append-only, gitignored). Each successful save logs UTC time, PID, before/after disk snapshot, and for API calls `ip` + `ua`.  
   - **No line** when the file shows `false` → change was **not** `set_execution_config` from this app (external file edit, sync, or tests before the fix).
2. Confirm **one** `python app.py` and one dashboard URL (`http://127.0.0.1:5000`).
3. **`git status execution_config.json`** — accidental checkout/merge.

## Related code

- `app.py` — `get_execution_config`, `set_execution_config`, `execution_config_api`, boot warning when OFF on disk, audit append.
- `templates/index.html` — dashboard toggle sends disable contract when turning OFF.
- `execution_config_file_lock.py` — cross-process lock for JSON RMW.
