# safety_gate.py — Lightweight production guard before threshold auto-apply and ML learning runs.
# Runs: python -m pytest tests/unit -q --tb=no from repo root.
# Bypass (emergencies only): SKIP_UNIT_SAFETY_GATE or SKIP_OPTIMIZER_GATE = 1 (or true/yes).
# Skipped automatically when TESTING=1 or pytest is already loaded (avoids nested pytest).

import logging
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


class SafetyGateError(RuntimeError):
    """Fast unit-test preflight failed; callers must not write learning outputs or apply thresholds."""


def run_unit_safety_gate(*, _test_force_subprocess: bool = False) -> bool:
    """
    Return True if unit tests pass or gate is skipped/bypassed.
    Return False if pytest failed — caller must abort without applying thresholds / writing learning outputs.
    """
    if not _test_force_subprocess:
        if os.environ.get("TESTING", "").strip() == "1":
            return True
        if "pytest" in sys.modules:
            return True
    for _bypass_key in ("SKIP_UNIT_SAFETY_GATE", "SKIP_OPTIMIZER_GATE"):
        bypass = os.environ.get(_bypass_key, "").strip().lower()
        if bypass in ("1", "true", "yes"):
            logger.warning("[safety_gate] %s set — unit test gate bypassed", _bypass_key)
            return True
    cmd = [sys.executable, "-m", "pytest", "tests/unit", "-q", "--tb=short"]
    proc = subprocess.run(cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        # Persist full output for post-hoc diagnosis
        try:
            if isinstance(output, str):
                (_REPO_ROOT / "data" / "safety_gate_last_output.txt").write_text(output, encoding="utf-8")
        except OSError:
            pass
        logger.error("[safety_gate] Unit tests failed (rc=%d):\n%s", proc.returncode, str(output)[-4000:])
    return proc.returncode == 0
