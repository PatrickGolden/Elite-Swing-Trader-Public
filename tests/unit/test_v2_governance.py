# STRATEGY.md v2.0 governance fixes — each pins a defect found 2026-09-26.

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import falsifier_cycle as fc  # noqa: E402


def test_screening_log_has_one_cap_owner():
    # The writer carried its own 10,000 cap, silently overriding the 75,000 in ml/config.
    import screening_log
    from ml.config import SCREENING_LOG_MAX_ENTRIES
    assert screening_log.MAX_LOG_ENTRIES == SCREENING_LOG_MAX_ENTRIES


def test_quarter_math():
    assert fc.quarter_of("2026-09-30T23:59:59+00:00") == "2026Q3"
    assert fc.quarter_of("2026-10-01T00:00:00+00:00") == "2026Q4"
    assert fc.next_quarter("2026Q4") == "2027Q1"
    assert fc.quarter_of("") == "?"


def test_slots_charged_to_registered_quarter_and_audits_free(tmp_path, monkeypatch):
    # Before v2.0 the counter was "ts >= 2026-07-01" forever: Q4 trials registered in late
    # September would have been charged to Q3, and the count never reset.
    rows = [
        {"ts": "2026-07-02T00:00:00", "kind": "registered", "trial_id": "H1", "config_hash": "a"},
        {"ts": "2026-07-02T00:00:00", "kind": "registered_audit", "trial_id": "A1", "config_hash": "b"},
        {"ts": "2026-09-26T00:00:00", "kind": "registered", "quarter": "2026Q4", "trial_id": "Q4a", "config_hash": "c"},
        {"ts": "2026-09-26T00:00:00", "kind": "registered", "quarter": "2026Q4", "trial_id": "Q4b", "config_hash": "d"},
    ]
    f = tmp_path / "trials.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(fc, "TRIALS", str(f))
    from datetime import datetime, timezone
    st = fc.trials_state(now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert st["current_quarter"] == "2026Q3"
    assert st["slots_used"] == 1                  # H1 only; audit is free, Q4 rows are not Q3's
    assert st["next_quarter_slots_remaining"] == fc.TRIAL_BUDGET_PER_QUARTER - 2


def _loop_body(src: str, name: str) -> str:
    start = src.index(f"def {name}():")
    return src[start:start + 2500]


def test_legacy_loops_gated_on_weekly_tasks_enabled():
    # Found running ungated 2026-09-26; stage1b emailed "Decision: GO" on retired criteria.
    src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    for loop in ("_weekly_ml_loop", "_weekly_replay_loop", "_promotion_monitor_loop"):
        assert "scheduler.weekly_tasks_enabled" in _loop_body(src, loop), f"{loop} is not gated"


def test_market_state_capture_wired_into_refresh():
    src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert "record_market_state(" in src


def test_tripwire_refuses_production_writes_under_pytest(tmp_path):
    # The chokepoint that stops the suite writing live state (found 2026-09-26: ~98 tests,
    # including a fake $500 BTC TWAP entry in data/pending_orders.json).
    import pytest
    import safe_json
    before = len(safe_json.TEST_WRITE_VIOLATIONS)
    for prod in (safe_json.PROJECT_ROOT / "data" / "anything.jsonl",
                 safe_json.PROJECT_ROOT / "positions.json"):
        assert safe_json.is_production_path(prod)
        with pytest.raises(safe_json.ProductionWriteUnderTest):
            safe_json.check_test_write(prod)
    assert not safe_json.is_production_path(tmp_path / "x.json")
    safe_json.check_test_write(tmp_path / "x.json")          # tmp writes are fine
    assert not safe_json.is_production_path(safe_json.PROJECT_ROOT / "tests" / "fixture.json")
    # Consume the violations this test deliberately produced so the autouse tripwire
    # doesn't fail it.
    del safe_json.TEST_WRITE_VIOLATIONS[before:]


def test_rotation_benchmark_is_hodl_in_memo(monkeypatch, tmp_path):
    pos = [{"source": "paper_rotation", "status": "CLOSED", "entry_time": "2026-08-20 22:43:33",
            "entry_price": 100.0, "exit_price": 110.0, "quantity": 1.0, "pnl_pct": 7.5}]
    out = fc._rotation_vs_hodl(pos)["positions"][0]
    assert out["hodl_btc_pct"] == 10.0 and out["strategy_net_pct"] == 7.5 and out["gap_pp"] == -2.5
