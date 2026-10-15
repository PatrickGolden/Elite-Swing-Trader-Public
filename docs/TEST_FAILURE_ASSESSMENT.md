# Test Failure Assessment: Real Bugs vs Test Issues

Assessment of the 21 failing batches from the full system test run. Categorized as **real bugs** (production code wrong) vs **test issues** (tests outdated, wrong assertions, or wrong mocks).

---

## 1. Batch 12 — test_quant_signal.py (1 failed, 19 passed)

**Failure:** `test_volatility_harvest_path` — expects `'volatility_harvest'` in `signal_badges`, got `['vol_contraction']`.

**Verdict: Test issue (test expectation outdated).**

- The test coin has `ch24h=-3`, `ch7d=-5`, `ch30d=-2` (all negative). In `quant_signal.py`, `_mtf_medium_aligned(coin)` is True when 24h/7d/30d are same sign (all negative), so it returns True.
- MTF alignment adds +2 to score (line 216). So score becomes 72 instead of 70.
- With score 72 in VOLATILE, the regime floor (72) is met, so the initial tier is BUY and we never force HOLD. The volatility harvest path only runs when `quant_signal == 'HOLD'`, so it is skipped.
- Later, the vol_contraction path runs (VOLATILE + BUY + high ATR + MTF aligned) and sets `vol_contraction_taken = True`, hence the `vol_contraction` badge only.
- **Fix:** Change the test coin so it is not MTF-aligned (e.g. mixed signs: `ch7d=2` or `ch30d=1`) so score stays 70, we get HOLD, and the vol_harvest path fires; or accept either `volatility_harvest` or `vol_contraction` when both conditions could apply.

---

## 2. Batch 20 — test_ai_brain.py (3 failed, 14 passed)

**Failures:**  
- `test_prompt_handles_macd_dict` / `test_prompt_handles_macd_string`: assert `'BEARISH'` / `'BUY'` in user prompt.  
- `test_analyze_returns_hold_on_error`: `KeyError: 'signal'`.

**Verdict: Test issues.**

- **MACD in prompt:** In `grok_brain.py`, `_build_prompt` computes `macd_signal` from `indicators['macd']` but **does not include it in `user_prompt`**. The user prompt only has RSI, Momentum, Volatility (lines 166–174). So the tests are wrong to expect BEARISH/BUY in the user prompt.
- **`result['signal']`:** Grok’s `analyze_coin` returns `sentiment_score` and related fields; it does **not** return a `signal` key. The error path returns a dict with `sentiment_score`, `reasoning`, `error`, `ai_source`, etc., but no `signal`. The test was written for an older API that used `signal`.
- **Fixes:** (1) Either add MACD to the Grok user prompt and keep the tests, or drop/relax the MACD prompt tests. (2) For the error case, assert `result.get('sentiment_score', 0) == 0` and `'error' in result` (or similar) instead of `result['signal'] == 'HOLD'`.

---

## 3. Batch 26 — test_error_resilience.py (2 failed, 16 passed)

**Failures:**  
- `test_place_market_buy_handles_exchange_error`: expects `"error" in result or result.get("status") == "error"`; mock does not trigger error.  
- `test_grok_brain_timeout_returns_hold`: expects `result["signal"] == "HOLD"`; Grok returns no `signal` key.

**Verdict: Test issues (wrong mocks / wrong API shape).**

- **place_market_buy:** The test sets `mock_ex.fetch_ticker.side_effect = Exception(...)`, but `place_market_buy_usd` → `_place_market_buy_single` calls `exchange.create_market_buy_order(...)`, not `fetch_ticker`. So the exception never runs and the code returns a success-shaped result (with MagicMock values). **Fix:** Use `mock_ex.create_market_buy_order.side_effect = Exception("Connection refused")` so the buy path actually fails and returns `status == "error"`.
- **Grok timeout:** Same as batch 20: Grok does not return `signal`; it returns `sentiment_score` and (on error) `error`. **Fix:** Assert on `sentiment_score` and/or presence of `error` instead of `result["signal"] == "HOLD"`.

---

## 4. Batches 4, 5, 6, 7, 8, 9, 14, 15, 23, 24, 25, 27, 28, 29, 31 — “see full_system_test_batch_<N>.txt”

These batches exited with code 1; the batch runner could not extract a “passed/failed” line from the last 6 lines of the log (e.g. long progress output with `-q`). So they are counted as failed even though some may be passing or environment-dependent.

- **4 (exit_monitoring), 31 (refresh_smoke):** Can legitimately skip when app deps are missing; may show “X skipped” or real failures.
- **8 (api_routes), 23 (config_persistence), etc.:** Need the actual pytest output from each `full_system_test_batch_<N>.txt` to say if the failure is a real bug or env/summary parsing. Recommendation: run `pytest tests/test_<module>.py -v --tb=short` for each failing batch and inspect.

**Verdict:** Unclear from summary alone; some may be **test/env issues** (e.g. truncated output, skips), others could be **real bugs**. Inspect individual batch logs.

---

## 5. Batches 39, 40, 41 — test_expert_consolidated, test_market_dynamics_stress, test_probabilistic_engine

Each has at least one failing test. Without reading each traceback we cannot classify. Recommendation: open the corresponding `full_system_test_batch_<N>.txt` and check whether the failure is an assertion on behavior that changed (test issue) or a regression in production logic (real bug).

---

## Summary Table

| Batch | Module                     | Verdict      | Action |
|-------|----------------------------|-------------|--------|
| 12    | test_quant_signal           | Test issue  | Fix test: MTF-aligned coin prevents vol_harvest path; adjust coin or accept vol_contraction. |
| 20    | test_ai_brain               | Test issues | Fix tests: no MACD in prompt; no `signal` key in Grok result. |
| 26    | test_error_resilience       | Test issues | Fix mocks: use create_market_buy_order for exchange error; assert Grok error shape without `signal`. |
| 4,5,6,7,8,9,14,15,23,24,25,27,28,29,31 | Various | Unclear     | Inspect each batch log; fix tests or code as needed. |
| 39,40,41                    | expert/market_dynamics/probabilistic | Unclear | Inspect tracebacks. |

**Conclusion:** The only refactor-related failure analyzed in detail (batch 12, quant_signal) is a **test issue**, not a production bug. The production logic (vol_harvest vs vol_contraction, MTF bonus) is consistent. Batches 20 and 26 are **test issues** (wrong expectations / mocks). The remaining failures need per-batch log inspection to classify.
