import json
from pathlib import Path


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _framework():
    return {
        "version": 2,
        "evaluation": {"fail_closed_on_missing_data": True},
        "rollback_triggers": {
            "regime_catastrophe_min_trades": 3,
            "regime_catastrophe_min_wr_pct": 15.0,
        },
        "features": [
            {
                "id": "f_low",
                "risk_level": "low",
                "activation_type": "auto_low_risk",
                "stage": "A1",
                "priority": 10,
                "prerequisites": {
                    "closed_trades_gte": 2,
                    "attr_completeness_pct_gte": 50.0,
                },
                "stability_window_weeks": 1,
                "actions": {
                    "on_activate": [{"type": "state_flag", "key": "f_low_enabled", "value": True}],
                    "on_deactivate": [{"type": "state_flag", "key": "f_low_enabled", "value": False}],
                },
            },
            {
                "id": "f_high",
                "risk_level": "high",
                "activation_type": "requires_approval",
                "stage": "B",
                "priority": 20,
                "prerequisites": {
                    "depends_on_features": ["f_low"],
                    "closed_trades_gte": 2,
                    "attr_completeness_pct_gte": 50.0,
                    "expectancy_pct_30d_gte": 0.0,
                },
                "stability_window_weeks": 1,
                "actions": {"on_activate": [], "on_deactivate": []},
            },
        ],
    }


def _positions():
    """Two closed trades with full attribution and positive pnl (for expectancy)."""
    return [
        {
            "id": 1,
            "status": "CLOSED",
            "is_test": False,
            "outcome": "win",
            "pnl_pct": 3.5,
            "market_regime": "SIDEWAYS",
            "fear_greed": 20,
            "funding_rate": -0.01,
            "price_change_24h": -1,
            "price_change_7d": 3,
            "price_change_30d": 8,
            "edge_score": 0.4,
        },
        {
            "id": 2,
            "status": "CLOSED",
            "is_test": False,
            "outcome": "win",
            "pnl_pct": 2.0,
            "market_regime": "SIDEWAYS",
            "fear_greed": 30,
            "funding_rate": 0.01,
            "price_change_24h": 1,
            "price_change_7d": 2,
            "price_change_30d": 5,
            "edge_score": 0.5,
        },
    ]


def _setup(monkeypatch, tmp_path):
    """Common test setup: write framework + positions, monkeypatch paths."""
    import roadmap_automation as ra

    framework_path = tmp_path / "config" / "roadmap_framework.json"
    state_path = tmp_path / "data" / "roadmap_state.json"
    transitions_path = tmp_path / "data" / "roadmap_transitions.jsonl"
    positions_path = tmp_path / "positions.json"

    _write_json(framework_path, _framework())
    _write_json(positions_path, _positions())

    monkeypatch.setattr(ra, "FRAMEWORK_FILE", str(framework_path))
    monkeypatch.setattr(ra, "STATE_FILE", str(state_path))
    monkeypatch.setattr(ra, "TRANSITIONS_FILE", str(transitions_path))
    monkeypatch.setattr(ra, "POSITIONS_FILE", str(positions_path))
    return ra, positions_path


def test_roadmap_cycle_promotes_low_and_queues_high(monkeypatch, tmp_path):
    ra, _ = _setup(monkeypatch, tmp_path)

    result = ra.run_roadmap_cycle(dry_run=False)
    assert result["success"] is True
    st = result["state"]
    assert st["feature_states"]["f_low"]["status"] == "active"
    assert st["state_flags"]["f_low_enabled"] is True
    # Stage B depends on Stage A1 being active first, so it should not queue
    # in the same evaluation cycle.
    assert not any(p["feature_id"] == "f_high" for p in st["pending_approvals"])


def test_roadmap_fail_closed_on_missing_inputs(monkeypatch, tmp_path):
    import roadmap_automation as ra

    framework_path = tmp_path / "config" / "roadmap_framework.json"
    state_path = tmp_path / "data" / "roadmap_state.json"
    positions_path = tmp_path / "positions.json"

    # Positions with no pnl_pct -> expectancy is None -> missing input
    _write_json(framework_path, _framework())
    _write_json(positions_path, [
        {"id": 1, "status": "CLOSED", "is_test": False, "fear_greed": 20,
         "funding_rate": -0.01, "price_change_24h": -1, "price_change_7d": 3,
         "price_change_30d": 8, "edge_score": 0.4},
    ])

    monkeypatch.setattr(ra, "FRAMEWORK_FILE", str(framework_path))
    monkeypatch.setattr(ra, "STATE_FILE", str(state_path))
    monkeypatch.setattr(ra, "POSITIONS_FILE", str(positions_path))

    result = ra.run_roadmap_cycle(dry_run=True)
    assert "missing_inputs" in result and result["missing_inputs"]
    assert all(d["decision"] == "blocked" for d in result["decisions"])


def test_roadmap_idempotent_no_duplicate_pending_approval(monkeypatch, tmp_path):
    ra, _ = _setup(monkeypatch, tmp_path)

    # First cycle activates low-risk feature.
    first = ra.run_roadmap_cycle(dry_run=False)
    assert first["state"]["feature_states"]["f_low"]["status"] == "active"

    # Second cycle should promote high-risk stage to pending approval (once).
    second = ra.run_roadmap_cycle(dry_run=False)
    third = ra.run_roadmap_cycle(dry_run=False)
    pending = third["state"]["pending_approvals"]
    high = [p for p in pending if p.get("feature_id") == "f_high" and p.get("status") == "pending_approval"]
    assert len(high) == 1


def test_decide_pending_approval_activates_feature(monkeypatch, tmp_path):
    ra, _ = _setup(monkeypatch, tmp_path)

    # First activate low and queue high
    ra.run_roadmap_cycle(dry_run=False)
    ra.run_roadmap_cycle(dry_run=False)

    out = ra.decide_pending_approval("f_high", approve=True, actor="tester", rationale="validated by risk committee")
    assert out["success"] is True
    assert out["state"]["feature_states"]["f_high"]["status"] == "active"


def test_decide_pending_approval_requires_rationale(monkeypatch, tmp_path):
    ra, _ = _setup(monkeypatch, tmp_path)

    ra.run_roadmap_cycle(dry_run=False)
    ra.run_roadmap_cycle(dry_run=False)
    out = ra.decide_pending_approval("f_high", approve=True, actor="tester", rationale="")
    assert out["success"] is False
    assert "rationale" in out["error"]


def test_regime_catastrophe_warning(monkeypatch, tmp_path):
    """Regime with <15% WR over min_trades triggers a warning."""
    import roadmap_automation as ra

    framework_path = tmp_path / "config" / "roadmap_framework.json"
    state_path = tmp_path / "data" / "roadmap_state.json"
    transitions_path = tmp_path / "data" / "roadmap_transitions.jsonl"
    positions_path = tmp_path / "positions.json"

    _write_json(framework_path, _framework())
    # 4 BULL trades: 0 wins = 0% WR, exceeds min_trades=3
    positions = [
        {"id": i, "status": "CLOSED", "is_test": False, "outcome": "loss",
         "pnl_pct": -5.0, "market_regime": "BULL",
         "fear_greed": 60, "funding_rate": 0.01,
         "price_change_24h": 2, "price_change_7d": 10,
         "price_change_30d": 30, "edge_score": 0.3}
        for i in range(4)
    ]
    _write_json(positions_path, positions)

    monkeypatch.setattr(ra, "FRAMEWORK_FILE", str(framework_path))
    monkeypatch.setattr(ra, "STATE_FILE", str(state_path))
    monkeypatch.setattr(ra, "TRANSITIONS_FILE", str(transitions_path))
    monkeypatch.setattr(ra, "POSITIONS_FILE", str(positions_path))

    result = ra.run_roadmap_cycle(dry_run=True)
    assert result["success"] is True
    warnings = result.get("regime_warnings", [])
    assert len(warnings) >= 1
    assert warnings[0]["regime"] == "BULL"
    assert warnings[0]["win_rate_pct"] == 0.0


def test_expectancy_uses_live_trades_only(monkeypatch, tmp_path):
    """Expectancy calculation must use positions.json, not monitoring_expectancy.json."""
    import roadmap_automation as ra

    positions_path = tmp_path / "positions.json"
    _write_json(positions_path, [
        {"id": 1, "status": "CLOSED", "is_test": False, "pnl_pct": 5.0},
        {"id": 2, "status": "CLOSED", "is_test": False, "pnl_pct": -2.0},
        {"id": 3, "status": "CLOSED", "is_test": True, "pnl_pct": 100.0},  # test trade excluded
    ])
    monkeypatch.setattr(ra, "POSITIONS_FILE", str(positions_path))

    metrics, _ = ra.collect_metrics()
    assert metrics["closed_trades"] == 2  # test trade excluded
    assert metrics["expectancy_pct_30d"] == 1.5  # (5 + -2) / 2 = 1.5


def test_rollback_trigger_demotes_active_feature(monkeypatch, tmp_path):
    """When rollback trigger trips, active feature is demoted immediately."""
    ra, positions_path = _setup(monkeypatch, tmp_path)

    # Add attr completeness rollback trigger.
    fw = _framework()
    fw["rollback_triggers"]["min_attr_completeness_pct"] = 95.0
    _write_json(Path(ra.FRAMEWORK_FILE), fw)

    # First run activates low-risk feature.
    first = ra.run_roadmap_cycle(dry_run=False)
    assert first["state"]["feature_states"]["f_low"]["status"] == "active"

    # Degrade attribution completeness while keeping pnl present (no missing expectancy input).
    degraded = [
        {"id": 1, "status": "CLOSED", "is_test": False, "outcome": "win", "pnl_pct": 1.0, "market_regime": "BULL"},
        {"id": 2, "status": "CLOSED", "is_test": False, "outcome": "loss", "pnl_pct": -1.0, "market_regime": "BULL"},
    ]
    _write_json(positions_path, degraded)

    second = ra.run_roadmap_cycle(dry_run=False)
    assert second["state"]["feature_states"]["f_low"]["status"] == "inactive"
    rollback = second.get("rollback", {})
    assert any(t.get("trigger") == "min_attr_completeness_pct" for t in rollback.get("triggered", []))


def test_clean_slippage_does_not_trigger_rollback(monkeypatch, tmp_path):
    """Regression: 0.0% observed slippage must not fire the 2.0% ceiling trigger.

    Pre-fix bug: threshold was -1.0, so 0.0 > -1.0 fired every cycle and
    demoted/blocked all features including SHAP and cash deployment tracking.
    """
    ra, _ = _setup(monkeypatch, tmp_path)
    fw = _framework()
    fw["rollback_triggers"]["max_dynamic_stop_slippage_to_intended_pct"] = 2.0
    _write_json(Path(ra.FRAMEWORK_FILE), fw)

    triggered = ra._evaluate_rollback_triggers(
        framework=fw,
        state={"rollback_counters": {}},
        metrics={
            "expectancy_pct_30d": 1.0,
            "attr_completeness_pct": 100.0,
            "dynamic_stop_slippage_to_intended_pct": 0.0,
        },
    )
    assert not any(
        t.get("trigger") == "max_dynamic_stop_slippage_to_intended_pct"
        for t in triggered.get("triggered", [])
    )


def test_slippage_above_ceiling_triggers_rollback(monkeypatch, tmp_path):
    """Above-ceiling slippage (3.5% with 2.0% ceiling) must fire the trigger."""
    ra, _ = _setup(monkeypatch, tmp_path)
    fw = _framework()
    fw["rollback_triggers"]["max_dynamic_stop_slippage_to_intended_pct"] = 2.0
    _write_json(Path(ra.FRAMEWORK_FILE), fw)

    triggered = ra._evaluate_rollback_triggers(
        framework=fw,
        state={"rollback_counters": {}},
        metrics={
            "expectancy_pct_30d": 1.0,
            "attr_completeness_pct": 100.0,
            "dynamic_stop_slippage_to_intended_pct": 3.5,
        },
    )
    fired = [t for t in triggered.get("triggered", [])
             if t.get("trigger") == "max_dynamic_stop_slippage_to_intended_pct"]
    assert len(fired) == 1
    assert fired[0]["observed"] == 3.5
    assert fired[0]["threshold"] == 2.0


# ---------------------------------------------------------------------------
# Self-healing: orphan state entries pruned at load_state
# ---------------------------------------------------------------------------

def test_load_state_prunes_orphan_feature_states_and_streaks(monkeypatch, tmp_path):
    """Stale feature_states / pass_streak_weeks / state_flags entries that no
    longer correspond to a framework feature must be pruned at load time."""
    import roadmap_automation as ra

    framework_path = tmp_path / "config" / "roadmap_framework.json"
    state_path = tmp_path / "data" / "roadmap_state.json"
    _write_json(framework_path, _framework())
    monkeypatch.setattr(ra, "FRAMEWORK_FILE", str(framework_path))
    monkeypatch.setattr(ra, "STATE_FILE", str(state_path))
    ra._LOGGED_ORPHAN_KEYS.clear()

    stale_state = {
        "version": 1,
        "feature_states": {
            "f_low": {"status": "active"},
            "bull_execution_suspend": {"status": "rejected"},
            "regime_pf_kill_switch": {"status": "inactive"},
        },
        "pass_streak_weeks": {
            "f_low": 3,
            "bull_execution_suspend": 2,
            "regime_pf_kill_switch": 1,
        },
        "state_flags": {
            "f_low_enabled": True,
            "bull_auto_execution_suspended": True,
        },
    }
    _write_json(state_path, stale_state)

    state = ra.load_state()

    assert "f_low" in state["feature_states"]
    assert "bull_execution_suspend" not in state["feature_states"]
    assert "regime_pf_kill_switch" not in state["feature_states"]

    assert state["pass_streak_weeks"]["f_low"] == 3
    assert "bull_execution_suspend" not in state["pass_streak_weeks"]
    assert "regime_pf_kill_switch" not in state["pass_streak_weeks"]

    assert state["state_flags"]["f_low_enabled"] is True
    assert "bull_auto_execution_suspended" not in state["state_flags"]


def test_load_state_round_trip_persists_pruned_state(monkeypatch, tmp_path):
    """After a load -> save round-trip the orphan keys are gone from disk too."""
    import roadmap_automation as ra
    import safe_json

    framework_path = tmp_path / "config" / "roadmap_framework.json"
    state_path = tmp_path / "data" / "roadmap_state.json"
    _write_json(framework_path, _framework())
    monkeypatch.setattr(ra, "FRAMEWORK_FILE", str(framework_path))
    monkeypatch.setattr(ra, "STATE_FILE", str(state_path))
    ra._LOGGED_ORPHAN_KEYS.clear()

    _write_json(state_path, {
        "feature_states": {
            "f_low": {"status": "active"},
            "ghost_feature": {"status": "active"},
        },
        "pass_streak_weeks": {"ghost_feature": 5},
        "state_flags": {"ghost_flag": True},
    })

    state = ra.load_state()
    safe_json.save(str(state_path), state, default=str)

    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert "ghost_feature" not in on_disk["feature_states"]
    assert "ghost_feature" not in on_disk["pass_streak_weeks"]
    assert "ghost_flag" not in on_disk["state_flags"]


def test_load_state_no_framework_does_not_prune(monkeypatch, tmp_path):
    """Defensive: if the framework file is missing/empty, load_state leaves state alone."""
    import roadmap_automation as ra

    framework_path = tmp_path / "config" / "roadmap_framework.json"
    state_path = tmp_path / "data" / "roadmap_state.json"
    framework_path.parent.mkdir(parents=True, exist_ok=True)
    framework_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ra, "FRAMEWORK_FILE", str(framework_path))
    monkeypatch.setattr(ra, "STATE_FILE", str(state_path))
    ra._LOGGED_ORPHAN_KEYS.clear()

    _write_json(state_path, {
        "feature_states": {"unknown_feature": {"status": "active"}},
    })

    state = ra.load_state()
    assert "unknown_feature" in state["feature_states"], \
        "with no framework features, prune must be a no-op"


def test_load_state_orphan_log_emitted_only_once(monkeypatch, tmp_path, caplog):
    """Long-lived process should not re-emit the same orphan-prune INFO line on every load_state call."""
    import logging
    import roadmap_automation as ra

    framework_path = tmp_path / "config" / "roadmap_framework.json"
    state_path = tmp_path / "data" / "roadmap_state.json"
    _write_json(framework_path, _framework())
    monkeypatch.setattr(ra, "FRAMEWORK_FILE", str(framework_path))
    monkeypatch.setattr(ra, "STATE_FILE", str(state_path))
    ra._LOGGED_ORPHAN_KEYS.clear()

    _write_json(state_path, {
        "feature_states": {"ghost": {"status": "active"}},
    })

    with caplog.at_level(logging.INFO, logger="roadmap_automation"):
        ra.load_state()
        ra.load_state()
        ra.load_state()

    prune_lines = [r for r in caplog.records
                   if "pruned orphan" in r.getMessage() and "ghost" in r.getMessage()]
    assert len(prune_lines) == 1, \
        f"expected exactly one prune log for 'ghost', got {len(prune_lines)}"
