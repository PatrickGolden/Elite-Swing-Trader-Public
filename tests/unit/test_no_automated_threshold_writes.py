# Audit F2 (2026-07-04): NO automated code path may write live_thresholds.json.
# "Disarmed for good" must be true by construction, not by dormant flags.
# Allowed writers: the human paths (apply_single / apply_recommendation) and the
# freeze-gated bounds-clamp guardrail (writes only TOWARD safety bounds).

import json

from unittest.mock import patch

import pytest


@pytest.fixture
def _lt(tmp_path, monkeypatch):
    p = tmp_path / "live_thresholds.json"
    p.write_text(json.dumps({"LEAN_ENTRY_MODE": 1.0, "freeze_thresholds": True}), encoding="utf-8")
    monkeypatch.setattr("thresholds.LIVE_THRESHOLDS_PATH", p)
    return p


def test_optuna_apply_disarmed_never_writes(_lt):
    from optuna_optimizer import apply_optuna_results
    before = _lt.read_text(encoding="utf-8")
    study = {
        "changes": [{"param": "DIP_MIN_SCORE", "proposed": 60, "current": 65}],
        "best_metrics": {"profit_factor": 3.0},
        "baseline_metrics": {"profit_factor": 1.0},  # +200% improvement — would have applied
    }
    assert apply_optuna_results(study) == []
    assert _lt.read_text(encoding="utf-8") == before


def test_revert_change_disarmed_never_writes(_lt):
    import recursive_optimizer as ro
    before = _lt.read_text(encoding="utf-8")
    ro._revert_change({"parameter": "DIP_MIN_SCORE", "old_value": 65, "new_value": 60})
    assert _lt.read_text(encoding="utf-8") == before


def test_no_7d_metadata_write_in_cycle_source():
    """The 7d win-rate metadata write was removed from _run_cycle_impl — pin that the
    only _save_json(LIVE_THRESHOLDS_PATH,...) call sites left in recursive_optimizer
    are the human apply path and the freeze-gated bounds guardrail."""
    import inspect
    import recursive_optimizer as ro
    src = inspect.getsource(ro)
    writes = src.count("_save_json(LIVE_THRESHOLDS_PATH")
    # 1: _reload_thresholds_or_rollback (restore-on-failure helper for human applies)
    # 2: validate_live_thresholds_bounds (freeze-gated clamp toward safety)
    # 3: apply_single (human path)
    assert writes == 3, (
        f"found {writes} live_thresholds writers in recursive_optimizer — a new automated "
        "writer may have been added; audit it against STRATEGY.md F2 before allowing"
    )
