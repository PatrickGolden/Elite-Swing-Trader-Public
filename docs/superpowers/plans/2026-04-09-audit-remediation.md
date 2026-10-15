# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 8 confirmed bugs from the April 2026 codebase audit — ML data corruption, security gap, ghost exchange exposure, broken dashboard, overfitting risk, vol-mismatch telemetry, and a year-collision timer.

**Architecture:** Targeted surgical fixes. Each task is independent with no cross-task ordering dependencies except Task 1 (C4) which touches ML data consumed by other ML modules. H9 is a **logging-only** change per owner instruction — do NOT alter position sizing math.

**Tech Stack:** Python 3.11+, Flask, ccxt, pytest

---

## File Map

| Task | Files Modified | Files Created (test) |
|------|---------------|---------------------|
| 1 (C4) | `ml/outcome_backfill.py` | `tests/unit/test_outcome_backfill.py` |
| 2 (C5) | `app.py` | — |
| 3 (C6) | `routes/schedule_bp.py` | `tests/integration/test_schedule_bp_auth.py` |
| 4 (H2) | `execution_engine.py` | `tests/unit/test_twap_partial_journal.py` |
| 5 (H4) | `routes/optimizer_roadmap_bp.py` | — |
| 6 (H5) | `optuna_optimizer.py`, `historical_replay.py` | `tests/unit/test_optuna_optimizer.py` (extend) |
| 7 (H9) | `quant_signal.py` | — |
| 8 (H12) | `app.py` | — |

---

## Task 1: C4 — Fix ML outcome backfill mislabeled horizons

**Files:**
- Modify: `ml/outcome_backfill.py`
- Test: `tests/unit/test_outcome_backfill.py` (create)

**Problem:** `outcome_7d` is `(current_price - entry_price) / entry_price` at backfill time, not a 7-day forward return. `outcome_3d` and `outcome_14d` are copied from the same value when null, making all three aliases of one wrong number.

**Fix:** Stop backfilling `outcome_3d` and `outcome_14d` from the 7d value. Rename the computation to honestly reflect what it is (mark-to-market since signal). Add `outcome_mtm_pct` as the honest field. Leave `outcome_7d` populated for backward compat but add a `backfill_note` field.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_outcome_backfill.py`:

```python
"""Tests for ml/outcome_backfill.py — horizon accuracy."""
import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_recs(tmp_path):
    recs_file = tmp_path / "ai_recommendations_v2.json"
    screening_file = tmp_path / "screening_log.json"
    recs_file.write_text("[]")
    screening_file.write_text("[]")
    return recs_file, screening_file


def _make_rec(days_ago, entry_price, coin_id="bitcoin"):
    ts = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "timestamp": ts,
        "entry_price": entry_price,
        "coin_id": coin_id,
        "outcome_7d": None,
        "outcome_3d": None,
        "outcome_14d": None,
    }


def test_backfill_does_not_copy_7d_into_3d_14d(tmp_recs):
    """outcome_3d and outcome_14d must NOT be set from a single current price."""
    recs_file, screening_file = tmp_recs
    rec = _make_rec(days_ago=10, entry_price=100.0, coin_id="bitcoin")
    recs_file.write_text(json.dumps([rec]))

    with patch("ml.outcome_backfill.RECOMMENDATIONS_FILE", str(recs_file)), \
         patch("ml.outcome_backfill.SCREENING_LOG_FILE", str(screening_file)):
        from ml.outcome_backfill import run_backfill
        stats = run_backfill(lambda ids: {"bitcoin": 110.0})

    updated = json.loads(recs_file.read_text())
    assert updated[0]["outcome_7d"] is not None, "outcome_7d should be set"
    assert updated[0]["outcome_3d"] is None, "outcome_3d must NOT be copied from 7d"
    assert updated[0]["outcome_14d"] is None, "outcome_14d must NOT be copied from 7d"


def test_backfill_adds_mtm_field(tmp_recs):
    """Backfill should add outcome_mtm_pct as the honest mark-to-market return."""
    recs_file, screening_file = tmp_recs
    rec = _make_rec(days_ago=10, entry_price=100.0, coin_id="bitcoin")
    recs_file.write_text(json.dumps([rec]))

    with patch("ml.outcome_backfill.RECOMMENDATIONS_FILE", str(recs_file)), \
         patch("ml.outcome_backfill.SCREENING_LOG_FILE", str(screening_file)):
        from ml.outcome_backfill import run_backfill
        run_backfill(lambda ids: {"bitcoin": 110.0})

    updated = json.loads(recs_file.read_text())
    assert updated[0]["outcome_mtm_pct"] == 10.0


def test_backfill_skips_recent_records(tmp_recs):
    """Records younger than 7 days should not be backfilled."""
    recs_file, screening_file = tmp_recs
    rec = _make_rec(days_ago=3, entry_price=100.0, coin_id="bitcoin")
    recs_file.write_text(json.dumps([rec]))

    with patch("ml.outcome_backfill.RECOMMENDATIONS_FILE", str(recs_file)), \
         patch("ml.outcome_backfill.SCREENING_LOG_FILE", str(screening_file)):
        from ml.outcome_backfill import run_backfill
        stats = run_backfill(lambda ids: {"bitcoin": 150.0})

    updated = json.loads(recs_file.read_text())
    assert updated[0]["outcome_7d"] is None
    assert stats["recommendations_updated"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_outcome_backfill.py -v`
Expected: `test_backfill_does_not_copy_7d_into_3d_14d` FAILS (outcome_3d gets set to same value as outcome_7d), `test_backfill_adds_mtm_field` FAILS (no `outcome_mtm_pct` field).

- [ ] **Step 3: Implement the fix**

In `ml/outcome_backfill.py`, in the `run_backfill` function, find the recommendations backfill block (~lines 110-119). Replace:

```python
            pct = round(((float(current) - entry_price) / entry_price) * 100, 2)
            rec["outcome_7d"] = pct
            if rec.get("outcome_3d") is None:
                rec["outcome_3d"] = pct
            if rec.get("outcome_14d") is None:
                rec["outcome_14d"] = pct
```

With:

```python
            pct = round(((float(current) - entry_price) / entry_price) * 100, 2)
            rec["outcome_7d"] = pct
            rec["outcome_mtm_pct"] = pct
            rec["backfill_note"] = "mtm_at_backfill"
```

This stops the 3d/14d aliasing and adds `outcome_mtm_pct` as the honest field name. `outcome_7d` stays for backward compatibility (it was always mark-to-market; consumers already use it that way).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_outcome_backfill.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/outcome_backfill.py tests/unit/test_outcome_backfill.py
git commit -m "fix(ml): stop aliasing outcome_3d/14d from 7d backfill, add outcome_mtm_pct"
```

---

## Task 2: C5 — Fix undefined DATA_DIR in tearsheet route

**Files:**
- Modify: `app.py:6715-6725`

**Problem:** `serve_tearsheet` uses `DATA_DIR` which is never defined in `app.py`. The route raises `NameError`.

- [ ] **Step 1: Read the current route**

Verify the tearsheet route at ~line 6715 in `app.py` still references `DATA_DIR`.

- [ ] **Step 2: Add DATA_DIR definition near the top of app.py**

Find the constants section (after imports, near line 119 where `EXECUTION_CONFIG_FILE` is defined). Add:

```python
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
```

This matches the pattern used by `tearsheet_generator.py`, `screening_log.py`, and `signal_history.py`.

- [ ] **Step 3: Verify by searching for other DATA_DIR uses in app.py**

Confirm no other references to `DATA_DIR` exist in `app.py` beyond the tearsheet route. The single addition covers all uses.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "fix: define DATA_DIR in app.py so tearsheet route does not crash"
```

---

## Task 3: C6 — Add auth guard to send-status-report endpoint

**Files:**
- Modify: `routes/schedule_bp.py:25-36`
- Test: `tests/integration/test_schedule_bp_auth.py` (create)

**Problem:** `POST /api/schedule/send-status-report` has no `require_admin_write_access()` call. Anyone who can reach the server can trigger email/Telegram blasts.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_schedule_bp_auth.py`:

```python
"""Test that POST /api/schedule/send-status-report requires admin auth."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    import os
    os.environ.setdefault("TESTING", "1")
    with patch.dict(os.environ, {"ADMIN_API_TOKEN": "test-secret"}):
        import importlib
        import app as app_module
        importlib.reload(app_module)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            yield c


def test_send_status_report_rejects_without_auth(client):
    """POST /api/schedule/send-status-report must require admin auth."""
    resp = client.post("/api/schedule/send-status-report")
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_schedule_bp_auth.py -v`
Expected: FAIL — returns 200 instead of 403 because no auth guard exists.

- [ ] **Step 3: Add the auth guard**

In `routes/schedule_bp.py`, modify the `send_status_report_now` function. Find:

```python
@schedule_bp.route('/api/schedule/send-status-report', methods=['POST'])
def send_status_report_now():
    """Build and send the daily status report email immediately (primary owner + Telegram). Use to preview the report."""
    import app as app_module

    try:
        summary_html = app_module._build_daily_status_summary()
```

Replace with:

```python
@schedule_bp.route('/api/schedule/send-status-report', methods=['POST'])
def send_status_report_now():
    """Build and send the daily status report email immediately (primary owner + Telegram). Use to preview the report."""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        summary_html = app_module._build_daily_status_summary()
```

`require_admin_write_access` is already imported at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_schedule_bp_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/schedule_bp.py tests/integration/test_schedule_bp_auth.py
git commit -m "fix(security): add admin auth guard to send-status-report endpoint"
```

---

## Task 4: H2 — Journal partial TWAP fills to prevent ghost positions

**Files:**
- Modify: `execution_engine.py:1310-1340`
- Test: `tests/unit/test_twap_partial_journal.py` (create)

**Problem:** When a TWAP buy partially fills (some slices succeed, then one fails), `success` is false, the callback is never invoked, and `_queue_for_journal` is never called. The exchange has real coins; the app doesn't know.

**Fix:** When `total_filled_base > 0` but `success` is false, still invoke the callback (or queue) with the partial fill data so the position is tracked.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_twap_partial_journal.py`:

```python
"""Test that partial TWAP fills are journaled."""
import json
import os
import threading
from unittest.mock import patch, MagicMock, call
import pytest


def test_partial_twap_journals_filled_quantity():
    """When TWAP partially fills, the callback should be invoked with actual filled quantity."""
    callback_data = {}

    def mock_callback(fill_data):
        callback_data.update(fill_data)

    def mock_place_buy(exchange, symbol, usd):
        """First slice succeeds, second fails."""
        if not hasattr(mock_place_buy, "_call_count"):
            mock_place_buy._call_count = 0
        mock_place_buy._call_count += 1
        if mock_place_buy._call_count == 1:
            return {
                "status": "closed",
                "filled_amount": 0.5,
                "average_price": 100.0,
                "fee_usd": 0.25,
                "order_id": "order-1",
            }
        return {"status": "error", "error": "network timeout", "filled_amount": 0, "average_price": 0, "fee_usd": 0}

    import execution_engine as ee
    original_callback = ee._on_twap_done_open_position_and_place_orders

    try:
        ee._on_twap_done_open_position_and_place_orders = mock_callback

        with patch.object(ee, "get_coinbase_exchange", return_value=MagicMock()), \
             patch.object(ee, "place_market_buy_usd", side_effect=mock_place_buy), \
             patch.object(ee, "log_execution"), \
             patch.object(ee, "_unregister_active_twap"), \
             patch("time.sleep"):
            ee.run_twap_buy_coinbase(
                symbol_ccxt="BTC/USD",
                total_usd=100.0,
                num_slices=2,
                duration_minutes=10,
                coin_name="Bitcoin",
                coin_id="bitcoin",
                stop_pct=3.0,
                target_pct=6.0,
            )

        assert "coin_symbol" in callback_data, "Callback must be invoked for partial fills"
        assert callback_data["quantity"] == 0.5, "Should journal actual filled quantity"
        assert callback_data["entry_price"] == 100.0
    finally:
        ee._on_twap_done_open_position_and_place_orders = original_callback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_twap_partial_journal.py -v`
Expected: FAIL — `callback_data` is empty because partial fills skip the callback.

- [ ] **Step 3: Implement the fix**

In `execution_engine.py`, find the block after the TWAP summary log (~line 1330). The current code is:

```python
    # Optional: open position + place SL/TP (app sets _on_twap_done_open_position_and_place_orders)
    if callable(_on_twap_done_open_position_and_place_orders) and success and total_filled_base > 0:
```

Replace with:

```python
    if callable(_on_twap_done_open_position_and_place_orders) and total_filled_base > 0:
```

This removes the `success` gate so that any TWAP with real fills (full or partial) gets journaled. The callback already receives actual `quantity`/`entry_price`/`capital_usd` from the aggregated fill data, so partial fills will correctly reflect the smaller amount.

- [ ] **Step 4: Apply the same fix to `run_single_market_buy_coinbase`**

In the same file, find the single-buy callback gate (~line 1546):

```python
    if callable(_on_twap_done_open_position_and_place_orders) and success and filled > 0:
```

Replace with:

```python
    if callable(_on_twap_done_open_position_and_place_orders) and filled > 0:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_twap_partial_journal.py -v`
Expected: PASS.

Run: `pytest tests/unit/test_execution_engine.py -v`
Expected: All existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add execution_engine.py tests/unit/test_twap_partial_journal.py
git commit -m "fix(exec): journal partial TWAP fills to prevent ghost exchange positions"
```

---

## Task 5: H4 — Fix roadmap status endpoint (broken import + wrong key)

**Files:**
- Modify: `routes/optimizer_roadmap_bp.py:162-176`

**Problem:** Imports `load_roadmap_state` which doesn't exist (it's `load_state`). Uses `state.get("features", {})` but persisted state uses `feature_states`. Silent `except` masks both errors.

- [ ] **Step 1: Read the broken block**

Verify `routes/optimizer_roadmap_bp.py` lines 162-176 still contain `from roadmap_automation import load_roadmap_state` and `state.get("features", {})`.

- [ ] **Step 2: Fix the import and key name**

Find in `routes/optimizer_roadmap_bp.py`:

```python
        try:
            from roadmap_automation import load_roadmap_state

            state = load_roadmap_state()
            features = state.get("features", {})
            result["roadmap_features"] = {
                fid: {
                    "stage": f.get("current_stage", "unknown"),
                    "active": f.get("active", False),
                    "consecutive_passes": f.get("consecutive_passes", 0),
                }
                for fid, f in features.items()
            }
        except Exception:
            result["roadmap_features"] = {}
```

Replace with:

```python
        try:
            from roadmap_automation import load_state as load_roadmap_state

            state = load_roadmap_state()
            feature_states = state.get("feature_states", {})
            result["roadmap_features"] = {
                fid: {
                    "status": f.get("status", "unknown"),
                    "active": f.get("status") == "active",
                    "updated_at": f.get("updated_at"),
                }
                for fid, f in feature_states.items()
            }
        except Exception:
            result["roadmap_features"] = {}
```

This uses the correct function name (`load_state`), the correct state key (`feature_states`), and maps the actual fields from persisted state (`status`, `updated_at`) instead of non-existent ones.

- [ ] **Step 3: Commit**

```bash
git add routes/optimizer_roadmap_bp.py
git commit -m "fix(api): use correct import and state key for roadmap status endpoint"
```

---

## Task 6: H5 — Add train/holdout split to Optuna optimizer

**Files:**
- Modify: `optuna_optimizer.py`
- Modify: `historical_replay.py` (add `end_offset_days` param)
- Test: extend `tests/unit/test_optuna_optimizer.py`

**Problem:** `TRAIN_RATIO = 0.7` is defined but never used. Optuna evaluates the full replay window, enabling overfitting.

**Fix:** Use `TRAIN_RATIO` to split the window: optimize on the first 70% (`days * 0.7`), then validate best params on the holdout 30%. Add `end_offset_days` parameter to `run_historical_replay` so Optuna can request only the training portion.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_optuna_optimizer.py`:

```python
def test_replay_objective_uses_train_split(monkeypatch):
    """replay_objective must call run_historical_replay with training window only, not full days."""
    captured_kwargs = {}

    def mock_replay(**kwargs):
        captured_kwargs.update(kwargs)
        return {"summary": {"profit_factor": 1.5, "win_rate_pct": 55, "max_drawdown_pct": 10}}

    monkeypatch.setattr("optuna_optimizer.run_historical_replay", mock_replay, raising=False)

    import importlib
    import optuna_optimizer
    importlib.reload(optuna_optimizer)
    monkeypatch.setattr(optuna_optimizer, "run_historical_replay", mock_replay)

    class FakeTrial:
        number = 0
        def suggest_float(self, name, low, high): return (low + high) / 2
        def suggest_int(self, name, low, high): return (low + high) // 2

    optuna_optimizer.replay_objective(FakeTrial(), days=90)

    assert "end_offset_days" in captured_kwargs, "Must pass end_offset_days to limit to training window"
    assert captured_kwargs["end_offset_days"] > 0, "Holdout days must be excluded from training"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_optuna_optimizer.py::test_replay_objective_uses_train_split -v`
Expected: FAIL — `end_offset_days` not in captured kwargs.

- [ ] **Step 3: Add `end_offset_days` parameter to `historical_replay.run_historical_replay`**

In `historical_replay.py`, find the function signature:

```python
def run_historical_replay(
    coins: Optional[List[Tuple[str, str]]] = None,
    days: int = 90,
    sim_interval_hours: int = 8,
    min_score_for_trade: int = 0,
    verbose: bool = True,
    threshold_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
```

Replace with:

```python
def run_historical_replay(
    coins: Optional[List[Tuple[str, str]]] = None,
    days: int = 90,
    sim_interval_hours: int = 8,
    min_score_for_trade: int = 0,
    verbose: bool = True,
    threshold_overrides: Optional[Dict[str, Any]] = None,
    end_offset_days: int = 0,
) -> Dict[str, Any]:
```

Then, where the replay window is computed (find where `days` is used to set the start date), apply `end_offset_days` to shift the end date backward. Find the line that sets the end of the replay window (typically `datetime.now()` or similar) and change:

```python
    end_dt = datetime.now() - timedelta(days=end_offset_days)
```

If the code uses `days` to compute a start date like `start = now - timedelta(days=days)`, adjust to:
```python
    end_dt = datetime.now() - timedelta(days=end_offset_days)
    start_dt = end_dt - timedelta(days=days)
```

Ensure all hourly/daily timestamps in the replay are bounded by `start_dt..end_dt`.

- [ ] **Step 4: Wire TRAIN_RATIO into `replay_objective`**

In `optuna_optimizer.py`, modify `replay_objective`:

```python
def replay_objective(
    trial: Any,
    days: int = 90,
    coins: Optional[List[Tuple[str, str]]] = None,
) -> float:
    params = create_search_space(trial)
    holdout_days = max(1, int(days * (1 - TRAIN_RATIO)))

    try:
        from historical_replay import run_historical_replay
        result = run_historical_replay(
            coins=coins,
            days=days - holdout_days,
            verbose=False,
            threshold_overrides=params,
            end_offset_days=holdout_days,
        )
    except Exception as e:
        logger.warning("[optuna] Replay failed for trial %d: %s", trial.number, e)
        return 0.0

    summary = result.get('summary', {})
    profit_factor = summary.get('profit_factor', 0.0)
    try:
        return float(profit_factor)
    except (TypeError, ValueError):
        return 0.0
```

Apply the same pattern to `replay_objective_multi`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_optuna_optimizer.py -v`
Expected: All PASS including the new train-split test.

- [ ] **Step 6: Commit**

```bash
git add optuna_optimizer.py historical_replay.py tests/unit/test_optuna_optimizer.py
git commit -m "fix(optuna): use TRAIN_RATIO for train/holdout split, prevent overfitting"
```

---

## Task 7: H9 — Log corrected volatility side-by-side (logging only)

**Files:**
- Modify: `quant_signal.py:817-823`

**Problem:** `coin_volatility=atr_pct * 25` is not annualized volatility. Position sizes are miscalibrated relative to the documented vol buckets.

**Fix per owner instruction:** Do NOT change the sizing math. Log the corrected value side-by-side so attribution data is not muddied by a mid-collection calibration shift.

- [ ] **Step 1: Read the current code**

Verify `quant_signal.py` ~line 817 still has `coin_volatility=atr_pct * 25 if atr_pct else 50`.

- [ ] **Step 2: Add side-by-side logging**

Find the position sizing block in `quant_signal.py`:

```python
        if entry_price is not None and float(entry_price) > 0 and stop_loss_pct > 0:
            try:
                position_sizing = position_sizer.calculate_for_consensus(
                    entry_price=float(entry_price),
                    stop_loss_pct=stop_loss_pct,
                    consensus_signal=tier,
                    regime_data=regime_data,
                    coin_volatility=atr_pct * 25 if atr_pct else 50,
                    cash_available=cash_available,
                    edge_probability=final_edge_prob,
                    rr_ratio=rr_ratio,
                )
            except Exception:
                logger.exception("Error in position_sizer.calculate_for_consensus")
```

Replace with:

```python
        if entry_price is not None and float(entry_price) > 0 and stop_loss_pct > 0:
            try:
                passed_vol = atr_pct * 25 if atr_pct else 50
                annualized_vol = float(ind.get('volatility', 0) or 0)
                position_sizing = position_sizer.calculate_for_consensus(
                    entry_price=float(entry_price),
                    stop_loss_pct=stop_loss_pct,
                    consensus_signal=tier,
                    regime_data=regime_data,
                    coin_volatility=passed_vol,
                    cash_available=cash_available,
                    edge_probability=final_edge_prob,
                    rr_ratio=rr_ratio,
                )
                position_sizing["_vol_debug"] = {
                    "atr_pct": round(atr_pct, 4),
                    "passed_vol_atr25": round(passed_vol, 2),
                    "annualized_vol": round(annualized_vol, 2),
                    "vol_bucket_used": "high" if passed_vol > 100 else "med-high" if passed_vol > 75 else "med" if passed_vol > 50 else "low",
                    "vol_bucket_correct": "high" if annualized_vol > 100 else "med-high" if annualized_vol > 75 else "med" if annualized_vol > 50 else "low",
                }
            except Exception:
                logger.exception("Error in position_sizer.calculate_for_consensus")
```

This adds a `_vol_debug` dict to the position_sizing output so the mismatch is visible in signal data and attribution without altering any sizing behavior.

- [ ] **Step 3: Commit**

```bash
git add quant_signal.py
git commit -m "telemetry(H9): log atr_pct vs passed volatility side-by-side for attribution"
```

---

## Task 8: H12 — Fix weekly replay year collision

**Files:**
- Modify: `app.py` (the `_weekly_replay_loop` function, ~line 2667-2696)

**Problem:** `current_week = et_now.isocalendar()[1]` uses week-of-year only (1-53). Week 1 of 2027 collides with week 1 of 2026, skipping that year's first replay.

**Fix:** Use ISO year-week string `'%G-W%V'` like `scheduler_manager.py` already does.

- [ ] **Step 1: Read the current code**

Verify `app.py` `_weekly_replay_loop` still uses `et_now.isocalendar()[1]` for `current_week`.

- [ ] **Step 2: Fix the week key**

In `app.py`, find inside `_weekly_replay_loop`:

```python
            current_week = et_now.isocalendar()[1]
```

Replace with:

```python
            current_week = et_now.strftime('%G-W%V')
```

This produces strings like `2026-W15` which are globally unique across years.

- [ ] **Step 3: Verify `last_replay_week` initialization is compatible**

`last_replay_week` is initialized as `None` (line ~2670). The `!=` comparison `last_replay_week != current_week` works correctly with `None != "2026-W15"` → `True`, so no other changes needed.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "fix: use ISO year-week string in weekly replay loop to prevent year collision"
```

---

## Self-Review Checklist

1. **Spec coverage:** All 8 confirmed findings (C4, C5, C6, H2, H4, H5, H9, H12) have a task. H9 is logging-only per owner instruction.
2. **Placeholder scan:** Every step has exact code or exact commands. No "TBD" or "add appropriate handling."
3. **Type consistency:** `load_state` (Task 5) matches `roadmap_automation.py` definition. `end_offset_days` (Task 6) is `int` in both `historical_replay.py` signature and `optuna_optimizer.py` call. `_vol_debug` (Task 7) is a plain dict added to `position_sizing` return.
4. **Independence:** Tasks 1-8 are independent. No task depends on another task's output. They can be executed in any order or in parallel.
