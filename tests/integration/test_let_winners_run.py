# tests/integration/test_let_winners_run.py — "Winners always run" permanent-behavior tests
# Run: pytest tests/integration/test_let_winners_run.py -v
# The Let Winners Run *toggle* was removed (2026-05-30); winners now ALWAYS run: every new
# position has no fixed take-profit and exits via stop-loss or trailing stop only.
# All tests non-destructive (no real trades, temp files only).

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def temp_account_config(monkeypatch, tmp_path):
    """Use temporary account_config.json so tests don't touch real config."""
    path = tmp_path / "account_config.json"
    config = {
        "total_cash": 20000.0,
        "risk_pct": 2.0,
        "max_cash_usage_pct": 40.0,
        "dust_threshold_usd": 1.0,
    }
    path.write_text(json.dumps(config, indent=2))

    import app as app_module
    import app_config as app_config_module
    monkeypatch.setattr(app_module, "ACCOUNT_CONFIG_FILE", str(path))
    monkeypatch.setattr(app_config_module, "ACCOUNT_CONFIG_FILE", str(path))
    yield str(path)


@pytest.fixture
def temp_account_and_positions(temp_account_config, temp_positions):
    """Both temp account config and temp positions."""
    yield


@pytest.fixture
def client(temp_account_config):
    """Flask test client with temp account config."""
    with patch.dict(os.environ, {"ELITE_SKIP_SCHEDULER": "1"}, clear=False):
        from app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c


# -----------------------------------------------------------------------------
# 1. The toggle is GONE from config
# -----------------------------------------------------------------------------

class TestToggleRemoved:
    def test_account_config_has_no_let_winners_run_key(self, temp_account_config):
        from app import load_account_config
        cfg = load_account_config()
        assert "let_winners_run" not in cfg

    def test_save_account_config_does_not_accept_flag(self):
        # The save signature no longer has a let_winners_run parameter.
        import inspect
        from app import save_account_config
        assert "let_winners_run" not in inspect.signature(save_account_config).parameters


# -----------------------------------------------------------------------------
# 2. Every new trade runs (no fixed take-profit, ever)
# -----------------------------------------------------------------------------

class TestNewTradesAlwaysRun:
    def test_new_trade_has_no_fixed_take_profit(self, temp_positions):
        from trade_journal import open_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        assert pos["ai_target_price"] is None
        assert pos["current_target_price"] is None
        assert pos["current_target_pct"] is None

    def test_new_trade_still_has_stop_loss(self, temp_positions):
        """Safety preserved: a runner still has a hard stop."""
        from trade_journal import open_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        assert pos["ai_stop_price"] is not None and pos["ai_stop_price"] > 0
        assert pos["current_stop_price"] is not None and pos["current_stop_price"] > 0

    def test_ai_target_pct_still_recorded_for_tracking(self, temp_positions):
        """The recommendation figure is retained (rr_ratio / hypothesis) even though it's not a sell target."""
        from trade_journal import open_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        assert pos["ai_target_pct"] == 6

    def test_passed_flag_is_ignored_always_runs(self, temp_positions):
        """Even if a caller passes let_winners_run=False, the position still runs (toggle is gone)."""
        from trade_journal import open_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
            "let_winners_run": False,  # ignored
        })
        assert pos["ai_target_price"] is None
        assert pos["current_target_price"] is None


# -----------------------------------------------------------------------------
# 3. API no longer exposes the toggle
# -----------------------------------------------------------------------------

class TestApiToggleRemoved:
    def test_update_account_ignores_flag_and_succeeds(self, client, temp_account_config):
        resp = client.post(
            "/api/account/update",
            json={"risk_pct": 2.5, "let_winners_run": True},  # flag ignored
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "let_winners_run" not in data
        assert "let_winners_run_confirmation" not in data

    def test_overview_does_not_return_flag(self, client, temp_account_config):
        resp = client.get("/api/account/overview")
        assert resp.status_code == 200
        assert "let_winners_run" not in resp.get_json()


# -----------------------------------------------------------------------------
# 4. Exit safety: a no-target runner still has its stop-loss honored
# -----------------------------------------------------------------------------

class TestRunnerKeepsStopLoss:
    def test_runner_position_has_stop_for_monitoring(self, temp_positions):
        from trade_journal import open_position, get_open_positions
        open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        pos = get_open_positions()[0]
        assert pos.get("current_stop_price") and pos["current_stop_price"] > 0
        assert pos.get("current_target_price") is None
