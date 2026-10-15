# Recursive Optimizer Audit

**Scope:** Correctness, hardening, code organization.  
**Single-source change:** `data['trades']` are now journal positions (have `exit_time`), not attribution records (had `ts`).

---

## 1. Correctness

### 1.1 Bug: `evaluate_previous_cycle` timestamp (fixed)

- **Issue:** `evaluate_previous_cycle` filters trades by `t.get('ts', '')` vs `applied_at`. Journal positions use `exit_time`, not `ts`, so after the single-source change every trade had `ts` missing. That made `trades_since` effectively empty and `trades_before` wrong, so maturity/confirm/revert logic was broken.
- **Fix:** Use a normalized trade timestamp: `ts = t.get('ts') or t.get('exit_time') or ''`, and normalize `exit_time` (e.g. `"YYYY-MM-DD HH:MM:SS"`) to an ISO-comparable string so comparisons with `applied_at` (ISO) are correct. Implemented via `_trade_time_for_compare(t)` and use in both `trades_since` and `trades_before` filters.

### 1.2 Rest of pipeline

- **aggregate():** Builds `trades_90d`, `regime_tier`, `current_regime` from `system_trades_closed`; uses `exit_time` for cutoffs. Consistent.
- **counterfactual_analysis:** Uses `data['trades']` (journal positions); they have `quant_path`, `confirmation_score`, `outcome`, `pnl_pct`. Correct.
- **rank_and_filter:** Sorts by net impact, caps auto at `MAX_AUTO_CHANGES_PER_CYCLE`. Correct.
- **apply_safe_changes:** Re-checks bounds before write; uses `_thresholds_write_lock`. Correct.
- **Weekly consolidated (Friday):** Uses log entries and `snapshot.date`; no dependency on trade `ts`. Correct.

---

## 2. Hardening

### 2.1 Done / already safe

- **Bounds check before apply:** `apply_safe_changes` and `apply_single` both validate value against `OPTIMIZER_BOUNDS` before writing.
- **Lock:** `_thresholds_write_lock` serializes writes to `live_thresholds.json`.
- **Atomic save:** `_save_json` uses `safe_json.save` to reduce corruption risk.
- **Empty/corrupt JSON:** `_load_json` returns a default on missing or decode error; callers use `default=[]` or `default={}` so the pipeline doesn’t crash.
- **Aggregate failure:** `run_optimization_cycle` catches aggregate failure and logs; resend/get_last_report_data fall back to stored snapshot when aggregate fails.

### 2.2 Recommendations

- **ATTRIBUTION_FILE:** Defined at top level but unused (attribution now from journal). Remove the constant or add a comment “legacy; no longer read by optimizer.”
- **_save_json return:** Currently only logs on failure. For critical paths (optimizer log, live thresholds), consider returning a bool and having callers log/warn or abort when save fails.
- **Corrupt live_thresholds:** If the file exists but is corrupt, `_load_json` returns `{}`. A later apply would write a file with only the applied keys and metadata, potentially dropping other params. Optional: when `Path.exists()` and decode fails, log a warning and optionally avoid overwriting.

---

## 3. Code organization

- **Structure:** Single file, ~1570 lines, with clear section comments (data loading, Step 1–6, weekly, format report, API helpers). Flow is easy to follow.
- **Constants:** Grouped at top; magic numbers (e.g. 5, 10, 3) are named (`MIN_TRADES_FOR_REC`, `MAX_AUTO_CHANGES_PER_CYCLE`, etc.).
- **Optional refactor:** Could split into modules (e.g. `recursive_optimizer/aggregate.py`, `counterfactual.py`, `apply_evaluate.py`, `report_html.py`) for smaller files and clearer boundaries; not required for correctness.
- **Types:** Return types use `Dict`, `List`, `Optional`; more precise typing (e.g. `Dict[str, Any]`) could be added gradually.

---

## 4. Summary


| Area                           | Status | Action                                                           |
| ------------------------------ | ------ | ---------------------------------------------------------------- |
| evaluate_previous_cycle        | Bug    | Use `exit_time` with normalized compare (done in code).          |
| aggregate/counterfactual/apply | OK     | None.                                                            |
| Bounds and lock                | OK     | None.                                                            |
| Save failure handling          | Minor  | Optional: return bool from _save_json, handle in critical paths. |
| ATTRIBUTION_FILE               | Unused | Kept for test env patching; comment added in code.               |
| Organization                   | OK     | Optional: split into submodules later.                           |
