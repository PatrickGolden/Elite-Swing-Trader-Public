# Claude Cowork Safety Checklist

This app handles real money and live exchange connections. Use this checklist when preparing for and using Claude Cowork to analyze trade data.

## 1. File system access

- [ ] **Folder placement:** Keep the app folder **outside** Desktop/Documents. Use a dedicated directory (e.g. `C:\trading\swing-trader`) and grant Cowork access only to that folder.
- [ ] **Read-only .env (Windows):** Run `attrib +R .env` in a shell from the project root. To edit .env later, run `attrib -R .env`.
- [ ] **Back up critical configs daily:** Run `.\scripts\backup_sensitive_configs.ps1` daily, or schedule it (Task Scheduler). Backs up `execution_config.json` and `account_config.json` to `backup/` with date suffix.

## 2. API key exposure

- [ ] **Exchange keys:** Use minimum permissions — read + trade only, **no withdrawal**. Document in .env.example.
- [ ] **Rotation:** Rotate API keys on a schedule (e.g. monthly), independent of Cowork.

## 3. Before applying any Cowork suggestion

- [ ] **Commit first:** Commit (or stash) before applying any Cowork suggestion so you can roll back with one command.
- [ ] **Test mode verification:** After applying a change, enable Test Mode, run **at least one full refresh** (POST `/api/refresh` or wait one scheduler cycle), then re-enable auto-execution only if behavior looks correct.
- [ ] **Timing:** Do not apply Cowork suggestions during active market hours. Prefer off-hours (e.g. Sunday evening).

## 4. Threshold or config changes

- [ ] Before applying any **threshold change**, run `python -m ml.run_pipeline` and check backtest output against the proposed change.
- [ ] Treat every Cowork suggestion as a **hypothesis to test**, not a conclusion to implement. Require at least 5 data points before applying.

## 5. Resource consumption

- [ ] Run the trading app and Cowork on **separate machines** if possible; otherwise set CPU/RAM limits for Cowork.
- [ ] Set a **hard daily cost cap** in Anthropic account settings.
- [ ] **Schedule Cowork** to run once (e.g. morning) and shut down — not continuously in the background while markets are open.

## 6. Physical / machine security

- [ ] Full disk encryption (e.g. BitLocker on Windows).
- [ ] Screen lock on sleep; do not leave the dashboard open and unattended.
- [ ] Run Cowork only on a trusted network (home or VPN), not on shared/public networks.

## 7. Approval gate

**Treat the "approve before applying" step as a real gate.** Review every proposed change with the same skepticism you'd apply to a junior analyst's recommendation. The system manages real capital; Cowork is a research tool, not the decision-maker.
