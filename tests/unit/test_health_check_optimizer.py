"""Tests for optimizer staleness in scripts/run_health_check.py."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def _health_mod():
    from scripts import run_health_check as rh

    return rh


def test_check_optimizer_state_ok_when_recent_cycle(_health_mod, tmp_path: Path) -> None:
    rh = _health_mod
    data = tmp_path / "data"
    data.mkdir()
    ts = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    (data / "optimizer_log.json").write_text(
        json.dumps([{"timestamp": ts, "snapshot": {"date": "2026-04-14"}}]),
        encoding="utf-8",
    )
    (tmp_path / "live_thresholds.json").write_text(
        json.dumps({"updated": "2026-04-14T08:01:27"}),
        encoding="utf-8",
    )
    r = rh.check_optimizer_state(
        optimizer_log_file=data / "optimizer_log.json",
        live_thresholds_file=tmp_path / "live_thresholds.json",
        scheduler_config_file=_not_frozen_cfg(tmp_path),
    )
    assert r["status"] == "ok"
    assert "Last optimizer cycle" in r["detail"]
    assert "live_thresholds.json last written" in r["detail"]


def _not_frozen_cfg(tmp_path: Path) -> Path:
    """A scheduler_config with weekly tasks ENABLED — optimizer is supposed to run,
    so staleness checks apply (the freeze short-circuit must not fire)."""
    p = tmp_path / "scheduler_config.json"
    p.write_text(json.dumps({"weekly_tasks_enabled": True}), encoding="utf-8")
    return p


def test_check_optimizer_state_warn_when_stale(_health_mod, tmp_path: Path) -> None:
    rh = _health_mod
    data = tmp_path / "data"
    data.mkdir()
    ts = (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    (data / "optimizer_log.json").write_text(
        json.dumps([{"timestamp": ts}]),
        encoding="utf-8",
    )
    r = rh.check_optimizer_state(
        optimizer_log_file=data / "optimizer_log.json",
        scheduler_config_file=_not_frozen_cfg(tmp_path),
    )
    assert r["status"] == "warn"


def test_check_optimizer_state_critical_when_very_stale(_health_mod, tmp_path: Path) -> None:
    rh = _health_mod
    data = tmp_path / "data"
    data.mkdir()
    ts = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    (data / "optimizer_log.json").write_text(
        json.dumps([{"timestamp": ts}]),
        encoding="utf-8",
    )
    r = rh.check_optimizer_state(
        optimizer_log_file=data / "optimizer_log.json",
        scheduler_config_file=_not_frozen_cfg(tmp_path),
    )
    assert r["status"] == "critical"


def test_check_optimizer_state_ok_when_frozen_despite_very_stale(_health_mod, tmp_path: Path) -> None:
    """STRATEGY.md v1.5: optimizer deliberately frozen → stale last-cycle is expected,
    must NOT alert (was emailing CRITICAL daily)."""
    rh = _health_mod
    data = tmp_path / "data"
    data.mkdir()
    ts = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    (data / "optimizer_log.json").write_text(json.dumps([{"timestamp": ts}]), encoding="utf-8")
    frozen = tmp_path / "scheduler_config.json"
    frozen.write_text(json.dumps({"weekly_tasks_enabled": False}), encoding="utf-8")
    r = rh.check_optimizer_state(
        optimizer_log_file=data / "optimizer_log.json",
        scheduler_config_file=frozen,
    )
    assert r["status"] == "ok"
    assert "frozen" in r["detail"].lower()


def test_check_model_performance_ok_when_idle_despite_stale_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _health_mod
) -> None:
    """No live journal activity in 48h — do not CRITICAL on old model_performance.json."""
    rh = _health_mod
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_performance.json").write_text(
        json.dumps({"updated": "2020-01-01T00:00:00+00:00", "outcomes": []}),
        encoding="utf-8",
    )
    (tmp_path / "positions.json").write_text(
        json.dumps(
            [
                {
                    "is_test": False,
                    "status": "CLOSED",
                    "entry_time": "2020-01-01 00:00:00",
                    "exit_time": "2020-01-02 00:00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    r = rh.check_model_performance()
    assert r["status"] == "ok"
    assert "staleness expected when idle" in r["detail"]


def test_check_model_performance_critical_when_recent_live_but_stale_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _health_mod
) -> None:
    rh = _health_mod
    monkeypatch.chdir(tmp_path)
    recent = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    (tmp_path / "model_performance.json").write_text(
        json.dumps({"updated": "2020-01-01T00:00:00+00:00", "outcomes": []}),
        encoding="utf-8",
    )
    (tmp_path / "positions.json").write_text(
        json.dumps(
            [
                {
                    "is_test": False,
                    "status": "OPEN",
                    "entry_time": recent,
                }
            ]
        ),
        encoding="utf-8",
    )
    r = rh.check_model_performance()
    assert r["status"] == "critical"
    assert "Live journal activity within 48h" in r["detail"]
