# Data Pipeline Resilience — Functional Review #4

**Date:** 2026-03-14  
**Scope:** When CoinGecko, FRED, CryptoQuant (and other sources) fail: graceful vs silent stale; single points of failure.

---

## Source map and failure behavior

All 10 sources are tracked in [app.py](../../app.py) via `data_source_status` and `mark_source(name, status, error)`. Status is set during the refresh flow after `fetcher.get_all_data()` and downstream steps.

| Source | Where fetched | On failure | Fallback / behavior | Single point of failure? |
|--------|----------------|------------|---------------------|---------------------------|
| **coingecko** | [enhanced_data_fetcher.py](../../enhanced_data_fetcher.py) `get_top_50_coins()`, `get_coins_page()`, `get_global_market_cap()` | Rate limit / error | CoinPaprika (+ CoinCap for sparklines), then CoinMarketCap (when API key set) for top coins, global, and batch prices | **Partial:** If all three fail, coins/cache or [] remain. |
| **fear_greed** | `get_fear_greed()` — Alternative.me (CMC does not expose F&G via API) | No data / error | Pipeline continues; quant uses default F&G (50, Neutral) | **Yes:** Single source; no fallback API. |
| **fred** | `get_macro_data()` — FRED API | No key / error | Warning "No macro data"; macro empty; regime/quant can still run | **Yes:** No fallback for macro; dashboard shows no macro tiles. |
| **alpha_vantage** | `get_news_sentiment()` | No sentiment | `mark_source('alpha_vantage','warning','No sentiment data')`; sentiment optional for quant | **Yes:** Sentiment is enrichment only. |
| **btc_correlation** | Computed from coin sparkline vs BTC in `get_enhanced_data_for_coin()` | No prices | `mark_source('btc_correlation','warning')`; enhanced.btc_correlation can be None | No: Derived from same price data as coins. |
| **onchain** | [onchain_data.py](../../onchain_data.py) CryptoQuant in `get_enhanced_data_for_coin()` | No key / API error | Fallback placeholders (sopr 1.0, netflow 0, etc.); `status: 'fallback'`; STRONG_BUY can be downgraded by data_quality gate | No: Placeholders allow pipeline to continue. |
| **indicators** | `calculate_indicators(raw)` in app after fetch | Exception | `mark_source('indicators','error', e)`; then **raise** — refresh fails | **Yes:** No fallback; refresh aborts. |
| **scoring** | `score_tokens(with_indicators)` in app | Exception | `mark_source('scoring','error', e)`; then **raise** — refresh fails | **Yes:** No fallback; refresh aborts. |
| **dynamic_targets** | Later in pipeline when computing stop/target | Exception | `mark_source('dynamic_targets','error')`; per-coin fallback 2.5% stop in app | Partial: One coin failing doesn’t kill refresh; bulk failure could. |
| **ai_consensus** | After consensus/veto in app | Set at end | `mark_source('ai_consensus','ok')` | No: Final step. |

---

## Fallback table (concise)

| Source | On failure | Fallback / behavior |
|--------|------------|---------------------|
| CoinGecko | 429 / error | CoinPaprika (and CoinCap for sparklines), then CoinMarketCap if key set. If all fail: [] or stale cache. |
| Fear & Greed | No data | Warning; pipeline continues; quant uses default F&G. |
| FRED | No key / error | Warning; no macro data; no crash. |
| Alpha Vantage | No sentiment | Warning; sentiment optional. |
| BTC correlation | No data | Warning; derived from coins. |
| CryptoQuant (onchain) | No key / API error | Fallback placeholders; status 'fallback'; data_quality ok_count can drop so STRONG_BUY → MODERATE_BUY. |
| Indicators | Exception | Refresh fails (raise). |
| Scoring | Exception | Refresh fails (raise). |
| Dynamic targets | Exception | Per-coin fallback 2.5% stop; mark_source warning. |
| AI consensus | — | Set ok at end of refresh. |

---

## Single points of failure

1. **Price data:** CoinGecko → CoinPaprika → CoinMarketCap (when key set). If all fail: cache or [].
2. **Fear & Greed:** Alternative.me only (CMC does not expose F&G via API); default 50/Neutral on error.
3. **FRED:** No fallback for macro data.
4. **Alpha Vantage:** Single source for sentiment (enrichment only).
5. **Indicators / scoring:** No fallback; any uncaught exception aborts the whole refresh.

---

## Staleness

- **data_source_status** is in-memory and updated each refresh. If a refresh never runs (e.g. app down), there is no persistent “last ok” timestamp; next refresh overwrites status.
- **Caches in enhanced_data_fetcher:** TTLs (e.g. coins 10 min) prevent using data older than that for *new* fetches; but if every fetch fails, the pipeline can still use whatever was last returned (e.g. previous refresh’s raw in cache). So prolonged API failure could result in repeated use of the last successful payload until a fetch succeeds.
- **quant_signal data_quality:** Uses `ok_count` from enhanced (funding_ok, order_book_ok, onchain_ok). If a source goes down, ok_count drops and STRONG_BUY can be downgraded to MODERATE_BUY; no silent “stale live” for tier — we demote instead.

---

## CryptoQuant / FRED / CoinGecko summary

- **CryptoQuant down:** onchain uses placeholders; `mark_source('onchain','warning')`; STRONG_BUY may downgrade to MODERATE_BUY when ok_count < REQUIRED_SOURCES_FOR_STRONG_BUY. No uncaught exception.
- **FRED down:** `mark_source('fred','warning','No macro data')`; macro empty; no crash.
- **CoinGecko down:** CoinPaprika (and CoinCap) used; if both fail, get_top_50_coins returns [] or cache; get_all_data can still return that; app marks coingecko 'ok' if no exception. If get_all_data throws (e.g. later step), we mark coingecko 'error' and refresh fails.

---

## Recommendations

1. **Monitoring:** Add optional alert or dashboard tile when a critical source (e.g. coingecko, indicators, scoring) has been in `error` or `warning` for more than N minutes (e.g. 15–30).
2. **Third price source:** Consider a third fallback for top-50 prices (e.g. CoinCap only for list, or another free tier) to reduce single point of failure when both CoinGecko and CoinPaprika are down.
3. **Persist source status:** If you want to audit “source X was down at time T”, persist `data_source_status` (e.g. to a small JSON or log) on each refresh so history is available.
