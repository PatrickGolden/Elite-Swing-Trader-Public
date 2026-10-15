# Admin API access (`ADMIN_API_TOKEN`)

Mutating control-plane routes use `require_admin_write_access()` in `admin_api_guard.py`.

## Production or remote access

1. Set environment variable **`ADMIN_API_TOKEN`** to a long random secret (never commit it).
2. For each mutating HTTP request, send header **`X-Admin-Token`** with the exact same value.

Without the token env var, the guard falls back to **localhost-only** checks using **`request.remote_addr` only** (the direct TCP peer). `X-Forwarded-For` is **not** used, so callers cannot spoof localhost through that header; if you terminate TLS or proxy in front of the app, the peer IP is often the proxy — set **`ADMIN_API_TOKEN`** in that case.

## Dashboard (browser)

The dashboard cannot read server env vars. When `ADMIN_API_TOKEN` is set:

```javascript
sessionStorage.setItem('admin_api_token', '<same value as ADMIN_API_TOKEN>');
```

Reload the page. Scripts merge this into `X-Admin-Token` for guarded actions via `mergeAdminHeaders()` / `adminFetchJson()` in `static/dashboard.js` (journal **close/delete/open**, position edit, SL/TP placement, account sync, execution-config toggles, roadmap/optimizer/backtest writes, etc.). On first 403 the dashboard prompts once to paste the token into `sessionStorage`.

Local development without `ADMIN_API_TOKEN` continues to allow writes from `127.0.0.1` / `::1` only.

## Representative guarded endpoints

- `POST /api/roadmap/automation/decision`, `POST /api/roadmap/automation/run`
- `POST /api/optimizer/resend`
- `POST /api/backtest/run`
- `POST /api/position-size/update`
- Journal / execution mutations as marked in route handlers

Refer to `tests/integration/test_api_routes.py` for examples using `X-Admin-Token`.

## Telegram webhook (`TELEGRAM_WEBHOOK_SECRET_TOKEN`)

`POST /api/telegram-webhook` is only active when **`TELEGRAM_WEBHOOK_SECRET_TOKEN`** is set to the same string you pass as `secret_token` in [setWebhook](https://core.telegram.org/bots/api#setwebhook). Telegram sends it on each update as **`X-Telegram-Bot-Api-Secret-Token`**. Without the env var the handler returns **503** (disabled).
