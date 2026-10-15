# Regression tests for thread-local threshold overrides (STRATEGY.md §5 / gate drivers).
#
# Bug fixed 2026-06-11: threshold_override() only affected get_threshold(), while
# quant_signal.generate_quant_signal reads the WHOLE dict via _load_live_thresholds().
# Optuna trials and replay gate drivers that overrode quant-gate parameters therefore
# silently never changed quant-gate behavior. Overrides must now be visible through BOTH
# read paths, and must never leak into the shared cache.

from thresholds import threshold_override, _load_live_thresholds, get_threshold


def test_override_visible_via_load_live_thresholds():
    with threshold_override({"LEAN_ENTRY_MODE": 1.0, "LEAN_FIXED_STOP_PCT": 24.0}):
        live = _load_live_thresholds()
        assert live.get("LEAN_ENTRY_MODE") == 1.0
        assert live.get("LEAN_FIXED_STOP_PCT") == 24.0


def test_override_visible_via_get_threshold():
    with threshold_override({"LEAN_ENTRY_MODE": 0.0}):
        assert get_threshold("LEAN_ENTRY_MODE", 99) == 0.0


def test_override_does_not_poison_cache_after_exit():
    before = dict(_load_live_thresholds())
    with threshold_override({"LEAN_ENTRY_MODE": 12345.0, "NOT_A_REAL_KEY": 1}):
        inside = _load_live_thresholds()
        assert inside.get("LEAN_ENTRY_MODE") == 12345.0
        assert inside.get("NOT_A_REAL_KEY") == 1
    after = _load_live_thresholds()
    assert after.get("LEAN_ENTRY_MODE") == before.get("LEAN_ENTRY_MODE")
    assert "NOT_A_REAL_KEY" not in after


def test_both_read_paths_agree_under_override():
    with threshold_override({"LEAN_MAX_HOLD_DAYS": 30}):
        assert _load_live_thresholds().get("LEAN_MAX_HOLD_DAYS") == get_threshold(
            "LEAN_MAX_HOLD_DAYS"
        )
