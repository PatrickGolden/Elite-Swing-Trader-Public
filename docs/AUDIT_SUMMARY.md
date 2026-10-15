# Swing Trader — Code Audit Summary

**Date:** February 2026  
**Scope:** Errors, bugs, redundancies, efficiencies across the project.

---

## Fixes Applied During Audit

### 1. **portfolio_manager.py — Correlation adjustment bug**
- **Issue:** `apply_correlation_adjustment()` was scaling `max_loss_pct` by the correlation factor. The stop-loss % is per-trade risk (e.g. 2.5%); only position size (USD, quantity, pct_of_account, risk_usd) should be scaled.
- **Fix:** `max_loss_pct` is now copied through unchanged; comment added.

### 2. **consensus_engine.py — Backtester by_tier was empty**
- **Issue:** Backtester stratifies by `consensus_quality` (ultra/strong/moderate), but recommendation records did not store `consensus_quality`, so `by_tier` was always empty.
- **Fix:** Both 3-model and 6-model recommendation records now set `consensus_quality` derived from `consensus_signal` (ULTRA_BUY→ultra, STRONG_BUY→strong, etc.).

### 3. **backtester.py — Timestamp parsing and tier filter**
- **Issue:** `datetime.strptime(r['timestamp'][:19], ...)` could raise if `timestamp` was missing or malformed.
- **Fix:** Loop with try/except; skip records with missing or invalid timestamps. Tier filter now uses `consensus_quality` (e.g. `ultra` for ULTRA_BUY) so `by_tier` works.

### 4. **monitoring.py — save_backtest_expectancy**
- **Issue:** No try/except on file write; inconsistent with `load_backtest_expectancy` which catches and returns None.
- **Fix:** Wrapped write in try/except; raise `RuntimeError` on failure.

---

## Other Findings (No Code Change or Optional)

### Errors & Bugs
- **execution_engine.py:** TWAP summary status checks `f.get("status") == "closed" or f.get("status") == "filled"`. CCXT can return either; both are handled. No change.
- **Time zones:** `monitoring.get_live_stats_by_regime` uses `datetime.utcnow()` for cutoff; consensus timestamps are naive local. If the server is not UTC, drift window could be off by hours. Consider using UTC consistently for recommendation timestamps or document that server should run in UTC.
- **Backtester recommendations path:** `Backtester()` uses default `recommendations_file='ai_recommendations_v2.json'` (relative to cwd). Consensus engine uses an absolute path under `os.path.dirname(__file__)`. If the app is run from a different cwd, backtester could read a different file. Running from project root keeps them aligned; consider passing the same path as consensus_engine in app.py for consistency.

### Redundancies
- **Config read pattern:** app.py has repeated “if file exists → load JSON → get key → default” for brain_config, execution_config, test_mode_config. Could be one small helper, e.g. `load_json_config(path, defaults)` to reduce duplication. Optional refactor.
- **Veto logic:** Already centralized in `apply_full_veto_logic()`; no duplication found.
- **Execution safety:** Single path via `_start_twap_if_safe()`; no redundant checks.

### Efficiencies
- **enhanced_data_fetcher.py:** Cache TTL is keyed by prefix (`key.startswith(cache_type)`); multiple keys can share the same TTL. Fine as-is.
- **Consensus engine:** Loads full recommendations list on init and on every update_outcomes save. For very large histories, consider appending new outcomes to a log or batching writes. Not critical at current scale.
- **refresh_data():** Single sequential flow; AI calls could be parallelized for top N (already sequential with small sleep). Optional future improvement.

### Dead / Legacy
- **app_not working.py:** Appears to be an old copy; safe to remove or move to `backup/` if not needed.
- No remaining legacy Sonnet “fallback” code; 3-model path (Haiku + Grok + Sonnet) is still the non–6-model path and is used when 6-model is off.

### Code Quality
- **Linting:** No linter errors reported on edited files.
- **Type hints:** Used in several modules (execution_engine, portfolio_manager, veto_specialist); could be extended for stricter static checking.
- **Docstrings:** Main functions and classes are documented; a few helpers could use one-line docs.

---

## Verification Checklist

| Item | Status |
|------|--------|
| Veto logic only in `apply_full_veto_logic()` | OK |
| Execution safety before every TWAP via `_start_twap_if_safe()` | OK |
| Drift monitoring + kill switch wired in refresh_data + API | OK |
| Correlation adjustment does not scale stop-loss % | Fixed |
| Backtester by_tier uses stored consensus_quality | Fixed |
| Backtester timestamp parse safe | Fixed |
| monitoring save_backtest_expectancy error handling | Fixed |

---

## Recommended Next Steps (Optional)

1. Run the app from the project root so backtester and consensus_engine use the same recommendations file, or pass `RECOMMENDATIONS_FILE` from consensus_engine into `Backtester()`.
2. Standardize on UTC for recommendation timestamps (or document server timezone) for drift and backtest consistency.
3. Add a shared `load_json_config(path, defaults)` in app.py to reduce config-load duplication.
4. Remove or archive `app_not working.py` if it is no longer needed.
