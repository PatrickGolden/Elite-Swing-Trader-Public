# Disaster Recovery Config Defaults

These are known-good starting templates for runtime config files.
The live versions (in the project root) are gitignored and backed up
to Google Drive daily by `scripts/backup_to_gdrive.ps1`.

## Recovery Steps

1. Clone the repo from GitHub
2. Copy each `*.defaults.json` to the project root (drop the `.defaults` suffix)
3. Update `account_config.json` with your actual cash balance
4. Update `user_alert_settings.json` with your email address
5. Add your `.env` file with API keys
6. Set `auto_execution_enabled: false` in `execution_config.json` (verify before enabling)
7. Reconcile `positions.json` with exchange — check open orders on Coinbase/Kraken
8. Restore `data/` from latest Google Drive backup if available
9. Run `python app.py` to verify dashboard loads
10. Enable auto-execution only after confirming positions match exchange state
