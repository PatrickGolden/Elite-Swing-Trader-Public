# Code Audit Report — Swing Trader
**Date:** 2025-02-20

## Summary
Audit of the swing-trader codebase identified several bugs and areas for improvement. Issues are categorized by severity.

---

## Critical Bugs

### 1. **best_trade_pct / worst_trade_pct use wrong data** (`trade_journal.py`)
- **Location:** `calculate_stats()` lines 478–479
- **Issue:** Uses `all_pnl` (pnl_usd) divided by 100 instead of `pnl_pct`
- **Impact:** Best/worst trade % displayed incorrectly (e.g. $500 win shows as "+5%" instead of actual % gain)
- **Fix:** Use `pnl_pct` from closed positions for best/worst trade percentages

### 2. **model_performance_tracker outcome semantics** (`trade_journal.py`)
- **Location:** `close_position()` line 244
- **Issue:** `log_outcome(pred_ts, coin_id, pnl_pct, pnl_pct)` passes same P&L for both 3d and 7d horizons
- **Impact:** Tracker expects 3-day and 7-day price changes from prediction time; we pass exit P&L for both
- **Note:** Acceptable as pragmatic "trade outcome" approximation; consider documenting or adding a separate trade-outcome path

---

## Medium Priority

### 3. **Potential KeyError on legacy positions** (`trade_journal.py`)
- **Location:** `calculate_stats()` lines 424–426
- **Issue:** `p['outcome']` and `p['outcome'] == 'win'` can raise if legacy positions lack `outcome`
- **Fix:** Use `p.get('outcome')` with fallback for older data

### 4. **Timezone inconsistency** (`_build_monthly_review` vs `trade_journal`)
- **Location:** `app.py` uses `datetime.now(timezone.utc)` for cutoff; `trade_journal` uses `datetime.now()` (local) for exit_time
- **Impact:** Period filtering may be off by one day at timezone boundaries
- **Fix:** Use timezone-aware timestamps consistently (e.g. `datetime.now(timezone.utc)` in trade_journal)

### 5. **journal_get_positions fallback fetch** (`app.py` line 2130)
- **Location:** When cache is empty, calls `fetcher.get_all_data()` without `n_enhanced`
- **Impact:** In test mode, full 10-coin enhanced fetch instead of 5; minor API usage
- **Fix:** Pass `n_enhanced=5` when `get_test_mode()` for consistency

---

## Low Priority / Code Quality

### 6. **Frontend: getElementById without null check**
- **Location:** Various in `index.html` (e.g. `document.getElementById('manual-version').textContent`)
- **Impact:** If element missing (e.g. wrong tab), throws and breaks script
- **Status:** Performance tab elements already have null checks; other areas could benefit

### 7. **model_attribution datetime**
- **Location:** `model_attribution.py` line 24, 45, 69
- **Issue:** Uses `datetime.now()` (local) — consistent within module but differs from app.py UTC
- **Note:** Low impact; consider aligning to UTC for cross-system consistency

### 8. **Duplicate model_log_prediction import**
- **Location:** `trade_journal.py` imports inside `open_position`; `app.py` imports at top
- **Note:** trade_journal uses try/except import; app.py uses top-level. Both work; slight redundancy.

---

## Already Fixed (from prior sessions)
- `sharpeColorClass` moved to top of script
- `??` replaced with `!= null ? x : y` in Performance/Monthly Review
- Null checks for Performance tab DOM elements
- `datetime.utcnow()` → `datetime.now(timezone.utc)` in model_performance_tracker

---

## Recommendations
1. Fix best_trade_pct / worst_trade_pct immediately (user-facing data)
2. Add defensive `p.get('outcome')` in calculate_stats for legacy data
3. Align timezone usage (UTC) across app.py and trade_journal for period filtering
4. Pass n_enhanced to journal fallback fetch when in test mode
