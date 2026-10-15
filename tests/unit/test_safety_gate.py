# tests/unit/test_safety_gate.py
import sys
from unittest.mock import MagicMock, patch

import safety_gate


def test_gate_skips_subprocess_when_pytest_loaded():
    with patch.object(safety_gate.subprocess, "run") as run_mock:
        assert safety_gate.run_unit_safety_gate() is True
    run_mock.assert_not_called()


def test_gate_subprocess_failure_returns_false(monkeypatch):
    monkeypatch.delenv("SKIP_UNIT_SAFETY_GATE", raising=False)
    with patch.object(safety_gate.subprocess, "run", return_value=MagicMock(returncode=1)) as run_mock:
        assert safety_gate.run_unit_safety_gate(_test_force_subprocess=True) is False
    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][0]
    assert cmd[:3] == [sys.executable, "-m", "pytest"]
    assert "tests/unit" in cmd


def test_gate_subprocess_success_returns_true(monkeypatch):
    monkeypatch.delenv("SKIP_UNIT_SAFETY_GATE", raising=False)
    with patch.object(safety_gate.subprocess, "run", return_value=MagicMock(returncode=0)):
        assert safety_gate.run_unit_safety_gate(_test_force_subprocess=True) is True


def test_gate_respects_skip_env(monkeypatch):
    monkeypatch.setenv("SKIP_UNIT_SAFETY_GATE", "1")
    with patch.object(safety_gate.subprocess, "run") as run_mock:
        assert safety_gate.run_unit_safety_gate(_test_force_subprocess=True) is True
    run_mock.assert_not_called()
