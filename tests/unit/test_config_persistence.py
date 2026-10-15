# tests/test_config_persistence.py — Execution config, test mode persist/load and corrupt handling
# Run from project root: pytest tests/test_config_persistence.py -v

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _patch_execution_config_paths(monkeypatch, tmp_path):
    """Isolate main + sidecar so tests never read the developer's data/last_auto_sync_at.json."""
    import app
    import app_config

    monkeypatch.setattr(app, "EXECUTION_CONFIG_FILE", str(tmp_path / "execution_config.json"))
    monkeypatch.setattr(app, "LAST_AUTO_SYNC_FILE", str(tmp_path / "last_auto_sync_at.json"))
    monkeypatch.setattr(app_config, "EXECUTION_CONFIG_FILE", str(tmp_path / "execution_config.json"))
    monkeypatch.setattr(app_config, "LAST_AUTO_SYNC_FILE", str(tmp_path / "last_auto_sync_at.json"))


class TestExecutionConfigPersistence:
    """Execution config: set_execution_config / get_execution_config."""

    def test_set_and_get_execution_config(self, tmp_path):
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            from app import set_execution_config, get_execution_config

            assert set_execution_config(auto_execution_enabled=True, auto_execution_exchange="kraken", auto_sync_balance=False) is True
            cfg = get_execution_config()
            assert cfg["auto_execution_enabled"] is True
            assert cfg["auto_execution_exchange"] == "kraken"
            assert cfg["auto_sync_balance"] is False
            assert "last_auto_sync_at" in cfg
            assert cfg.get("stale_position_rule_enabled") is True  # default when not set
            main = json.loads((tmp_path / "execution_config.json").read_text(encoding="utf-8"))
            assert "last_auto_sync_at" not in main

    def test_execution_config_stale_position_rule_enabled(self, tmp_path):
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            from app import set_execution_config, get_execution_config

            assert set_execution_config(stale_position_rule_enabled=False) is True
            cfg = get_execution_config()
            assert cfg["stale_position_rule_enabled"] is False
            assert set_execution_config(stale_position_rule_enabled=True) is True
            cfg = get_execution_config()
            assert cfg["stale_position_rule_enabled"] is True

    def test_last_auto_sync_sidecar(self, tmp_path):
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            from app import set_execution_config, get_execution_config, _set_last_auto_sync_at

            assert set_execution_config(auto_execution_enabled=True) is True
            _set_last_auto_sync_at("2026-01-01T00:00:00.000Z")
            cfg = get_execution_config()
            assert cfg["last_auto_sync_at"] == "2026-01-01T00:00:00.000Z"
            main = json.loads((tmp_path / "execution_config.json").read_text(encoding="utf-8"))
            assert "last_auto_sync_at" not in main
            side = json.loads((tmp_path / "last_auto_sync_at.json").read_text(encoding="utf-8"))
            assert side["last_auto_sync_at"] == "2026-01-01T00:00:00.000Z"

    def test_legacy_last_auto_sync_migrates_on_set(self, tmp_path):
        """Old layout: timestamp only in execution_config.json — first set_execution_config copies to sidecar."""
        path = tmp_path / "execution_config.json"
        path.write_text(
            json.dumps(
                {
                    "auto_execution_enabled": True,
                    "auto_execution_exchange": "coinbase",
                    "auto_sync_balance": True,
                    "last_auto_sync_at": "2025-12-01T12:00:00.000Z",
                    "twap_min_usd": 500.0,
                    "stale_position_rule_enabled": True,
                }
            ),
            encoding="utf-8",
        )
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            from app import set_execution_config, get_execution_config

            assert set_execution_config(twap_min_usd=400.0) is True
            cfg = get_execution_config()
            assert cfg["last_auto_sync_at"] == "2025-12-01T12:00:00.000Z"
            assert cfg["twap_min_usd"] == 400.0
            main = json.loads(path.read_text(encoding="utf-8"))
            assert "last_auto_sync_at" not in main

    def test_execution_config_missing_file_defaults(self, tmp_path):
        """FAIL-CLOSED (STRATEGY.md 2026-06-11): a missing config file must never arm
        live trading. Only an explicit `"auto_execution_enabled": true` on disk turns it on."""
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            # Point main file to a path that does not exist (sidecar path still under tmp)
            import app

            m.setattr(app, "EXECUTION_CONFIG_FILE", str(tmp_path / "nonexistent_exec.json"))
            from app import get_execution_config

            cfg = get_execution_config()
            assert cfg["auto_execution_enabled"] is False
            assert cfg["auto_execution_exchange"] == "coinbase"
            assert cfg["auto_sync_balance"] is True
            assert cfg["last_auto_sync_at"] is None
            assert cfg.get("stale_position_rule_enabled") is True  # default when file missing

    def test_execution_config_corrupt_file_defaults(self, tmp_path):
        """FAIL-CLOSED: a corrupt config file must never arm live trading."""
        path = tmp_path / "execution_config.json"
        path.write_text("invalid", encoding="utf-8")
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            from app import get_execution_config

            cfg = get_execution_config()
            assert cfg["auto_execution_enabled"] is False
            assert cfg["auto_execution_exchange"] == "coinbase"
            assert cfg.get("stale_position_rule_enabled") is True  # default when file corrupt

    def test_execution_config_null_auto_execution_enabled_fails_closed(self, tmp_path):
        """FAIL-CLOSED: JSON null/malformed is not consent to trade — only explicit true is."""
        path = tmp_path / "execution_config.json"
        path.write_text(
            json.dumps(
                {
                    "auto_execution_enabled": None,
                    "auto_execution_exchange": "coinbase",
                    "auto_sync_balance": True,
                }
            ),
            encoding="utf-8",
        )
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            from app import get_execution_config

            cfg = get_execution_config()
            assert cfg["auto_execution_enabled"] is False

    def test_execution_config_explicit_true_enables(self, tmp_path):
        """Explicit true on disk is the one and only way auto-execution turns on."""
        path = tmp_path / "execution_config.json"
        path.write_text(
            json.dumps({"auto_execution_enabled": True}), encoding="utf-8"
        )
        with pytest.MonkeyPatch.context() as m:
            _patch_execution_config_paths(m, tmp_path)
            from app import get_execution_config

            cfg = get_execution_config()
            assert cfg["auto_execution_enabled"] is True


class TestTestModePersistence:
    """Test mode: set_test_mode / get_test_mode (test_mode.py)."""

    def test_set_and_get_test_mode_on(self, tmp_path):
        import test_mode
        with pytest.MonkeyPatch.context() as m:
            m.setattr(test_mode, "TEST_MODE_CONFIG_FILE", str(tmp_path / "test_mode_config.json"))
            from test_mode import set_test_mode, get_test_mode
            assert set_test_mode(True) is True
            assert get_test_mode() is True

    def test_set_and_get_test_mode_off(self, tmp_path):
        import test_mode
        with pytest.MonkeyPatch.context() as m:
            m.setattr(test_mode, "TEST_MODE_CONFIG_FILE", str(tmp_path / "test_mode_config.json"))
            from test_mode import set_test_mode, get_test_mode
            assert set_test_mode(False) is True
            assert get_test_mode() is False

    def test_set_test_mode_preserves_paper_fields(self, tmp_path):
        import test_mode
        path = tmp_path / "test_mode_config.json"
        path.write_text(
            json.dumps(
                {
                    "test_mode": True,
                    "paper_trading": True,
                    "paper_trade_notional_usd": 150.0,
                }
            ),
            encoding="utf-8",
        )
        with pytest.MonkeyPatch.context() as m:
            m.setattr(test_mode, "TEST_MODE_CONFIG_FILE", str(path))
            from test_mode import set_test_mode, get_test_mode, get_paper_trading, get_paper_trade_notional_usd

            assert set_test_mode(False) is True
            assert get_test_mode() is False
            assert get_paper_trading() is True
            assert get_paper_trade_notional_usd() == 150.0

    def test_set_paper_trading_preserves_test_mode(self, tmp_path):
        import test_mode
        path = tmp_path / "test_mode_config.json"
        path.write_text(json.dumps({"test_mode": False, "paper_trading": False}), encoding="utf-8")
        with pytest.MonkeyPatch.context() as m:
            m.setattr(test_mode, "TEST_MODE_CONFIG_FILE", str(path))
            from test_mode import set_paper_trading, get_test_mode, get_paper_trading

            assert set_paper_trading(True) is True
            assert get_test_mode() is False
            assert get_paper_trading() is True

    def test_test_mode_missing_file_defaults_to_true(self, tmp_path):
        import test_mode
        with pytest.MonkeyPatch.context() as m:
            m.setattr(test_mode, "TEST_MODE_CONFIG_FILE", str(tmp_path / "nonexistent_test.json"))
            from test_mode import get_test_mode
            assert get_test_mode() is True

    def test_test_mode_corrupt_file_defaults_to_true(self, tmp_path):
        import test_mode
        path = tmp_path / "test_mode_config.json"
        path.write_text("{")
        with pytest.MonkeyPatch.context() as m:
            m.setattr(test_mode, "TEST_MODE_CONFIG_FILE", str(path))
            from test_mode import get_test_mode
            assert get_test_mode() is True
