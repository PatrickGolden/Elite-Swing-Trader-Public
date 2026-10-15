# RECOVERY.md — what git holds, what it can't, and how to rebuild this machine

Written 2026-08-20. Git is the golden source for **code and governance**. It is
deliberately *not* the source for secrets or live trading state — those have their
own paths, documented below. Rebuilding requires all three.

---

## 1. What git holds (golden source)

Clone `https://github.com/PatrickGolden/Elite-Swing-Trader.git` and you have:

| Layer | Files |
|---|---|
| Application | all `*.py`, `routes/`, `templates/`, `static/`, `tests/` |
| Strategy of record | `STRATEGY.md`, `WATCH_SCHEDULE.md` |
| Agent governance | `CLAUDE.md`, `.claude/rules/`, `.claude/commands/`, `.claude/agents/`, `.claude/scheduled-tasks/`, `.claude/launch.json` |
| Working memory | `tasks/lessons.md`, `tasks/todo.md`, `tasks/research/` |
| Config templates | `config/defaults/*.defaults.json` |

**Branch caveat:** `master` is frozen at 2026-06-01. All work since — the lean fill
guard, RULE 0, the rotation cohort, the ops hardening — lives on `fix/lean-fill-guard`,
48 commits ahead. **Clone and check out that branch, not master**, until it is merged.

## 2. What git will never hold

### Secrets — `.env` (currently backed up NOWHERE)

`.env` is gitignored by design and is not covered by `backup_to_gdrive.ps1`.
`backup_sensitive_configs.ps1` writes to `backup/` on the *same disk*, so it does not
survive drive failure. **If this PC dies today, every credential below must be
re-issued from its provider.**

Store a copy in a password manager or an encrypted Drive file. Keys in use:

```
COINBASE_API_KEY / COINBASE_API_SECRET   COINGECKO_API_KEY
ALPHA_VANTAGE_API_KEY  COINMARKETCAP_API_KEY  CRYPTOQUANT_API_KEY  FRED_API_KEY
ANTHROPIC_API_KEY  DEEPSEEK_API_KEY  GEMINI_API_KEY  GROQ_API_KEY  XAI_API_KEY
FIRECRAWL_API_KEY  GITHUB_PERSONAL_ACCESS_TOKEN
SMTP_SERVER / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD
ALERT_EMAIL_FROM / ALERT_EMAIL_TO  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
ADMIN_API_TOKEN  ACCOUNT_SIZE  MAX_RISK_PCT
```

Rotate `COINBASE_API_KEY/SECRET` on any suspected loss — it is the only one that can move money.

### Live state — Google Drive

`positions.json`, `data/`, and the runtime `*_config.json` files are gitignored
append-only state. Source of truth: `G:\My Drive\Trading App\Backup Files`, written
daily 06:00 and 18:00 by `SwingTrader-GDrive-Backup`.

`data/` holds the irreplaceable evidence — `execution_audit.jsonl`,
`refresh_snapshots.jsonl`, `positions.json` history, `decision_memos/`. Per
`.claude/rules/data-safety.md` RULE 0 these are **data, not garbage**, including
every `.bak` / `.bak.gz`.

## 3. Rebuild procedure

```
1. git clone <repo> && git checkout fix/lean-fill-guard
2. Restore .env from password manager (or re-issue every key in §2)
3. Restore positions.json + data/ + *_config.json from Google Drive
   (fresh install instead? copy config/defaults/*.defaults.json to their live names)
4. uv sync   (or: pip install -r requirements.txt)
5. Sync agent governance to the global dir — see §4
6. Re-register scheduled tasks:
     scripts/register_dashboard_task.ps1
     scripts/register_healthcheck_task.ps1
     scripts/register_memo_task.ps1
7. python -m pytest tests/ -q        # expect 1217 passed, 1 skipped
```

## 4. Agent governance sync (the step that bites)

Claude Code executes scheduled tasks from `~/.claude/scheduled-tasks/`, **not** from
the repo. The repo copy is the versioned source of truth; the global copy is what
runs. They drift silently — on 2026-08-20 the repo's `optimizer-audit` was a
2026-05-23 fork missing the `RETIRED 2026-06-11` marker present in the executing copy.

Restore repo → global:

```powershell
Copy-Item -Recurse -Force .claude\scheduled-tasks\* $HOME\.claude\scheduled-tasks\
```

Check for drift (should print nothing):

```powershell
Get-ChildItem .claude\scheduled-tasks -Filter SKILL.md -Recurse | ForEach-Object {
  $g = "$HOME\.claude\scheduled-tasks\$($_.Directory.Name)\SKILL.md"
  if (-not (Test-Path $g)) { "MISSING IN GLOBAL: $($_.Directory.Name)" }
  elseif ((Get-FileHash $_.FullName).Hash -ne (Get-FileHash $g).Hash) { "DRIFT: $($_.Directory.Name)" }
}
```

`.claude/settings.local.json` (permission grants, machine paths) and
`.claude/worktrees/` (~1.1 GB of stale agent worktrees) are intentionally not tracked.
Re-grant permissions as prompted on the new machine.

## 5. Not covered by any backup

- `.env` — see §2. **The one real gap.**
- `.claude/settings.local.json` — permission allowlist; regenerates through use.
- The Windows Scheduled Task registrations — recreated by the `register_*.ps1` scripts in §3.
