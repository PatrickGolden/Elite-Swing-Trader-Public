# tests/stress/conftest.py — Stress test config
# Inherits isolation + synthetic data fixtures from tests/conftest.py.

import pytest

# Mark all tests in this directory tree so `pytest -m "not stress"` skips them.
pytestmark = pytest.mark.stress
