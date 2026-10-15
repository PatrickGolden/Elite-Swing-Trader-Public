import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_app_or_skip():
    try:
        import app as app_mod  # noqa: F401
        return app_mod
    except ModuleNotFoundError as e:
        pytest.skip(f"app deps missing: {e}")


def test_apply_full_veto_logic_runs_veto_in_test_mode():
    app_mod = _import_app_or_skip()
    coin = {"id": "bitcoin", "name": "Bitcoin", "enhanced": {}}
    regime = {"regime": "SIDEWAYS"}
    consensus = {"consensus_signal": None}

    quant_out = {
        "quant_signal": "BUY",
        "quant_tier": "MODERATE_BUY",
        "quant_confidence": 70,
    }
    veto_out = {"veto_decision": "VETO", "veto_reason": "rule"}

    with patch("app.generate_quant_signal", return_value=quant_out), \
         patch("app.get_test_mode", return_value=True), \
         patch("app.veto_specialist_evaluate", return_value=veto_out) as mock_veto_eval, \
         patch("app.apply_veto_override") as mock_apply_veto:
        app_mod.apply_full_veto_logic(coin, regime, consensus, fear_greed_value=50)

    assert mock_veto_eval.called is True
    assert mock_apply_veto.called is True


def test_refresh_data_returns_false_when_inner_fails():
    app_mod = _import_app_or_skip()
    with patch("app._refresh_data_inner", return_value=False):
        ok = app_mod.refresh_data()
    assert ok is False


def test_refresh_data_returns_true_when_inner_succeeds():
    app_mod = _import_app_or_skip()
    with patch("app._refresh_data_inner", return_value=True):
        ok = app_mod.refresh_data()
    assert ok is True
