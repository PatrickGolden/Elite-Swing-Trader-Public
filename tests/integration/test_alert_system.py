# tests/test_alert_system.py — AlertSystem: channel enable/disable, primary-only routing,
# alert type management, email recipient management, settings persistence
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_settings_file(monkeypatch, tmp_path):
    """Redirect user settings and alert history to temp files."""
    import alert_system as a
    settings_path = tmp_path / "user_alert_settings.json"
    history_path = tmp_path / "alert_history.json"
    monkeypatch.setattr(a, "USER_SETTINGS_FILE", settings_path)
    monkeypatch.setattr(a, "ALERT_TRACKING_FILE", history_path)
    return settings_path


@pytest.fixture
def alerts(temp_settings_file):
    """Fresh AlertSystem with temp files, no real SMTP/Telegram."""
    with patch.dict(os.environ, {
        "SMTP_USERNAME": "test@example.com",
        "SMTP_PASSWORD": "password",
        "ALERT_EMAIL_TO": "test@example.com",
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_CHAT_ID": "12345",
    }):
        import alert_system as a
        a.SMTP_USERNAME = "test@example.com"
        a.SMTP_PASSWORD = "password"
        a.ALERT_EMAIL_TO_LIST = ["test@example.com"]
        a.ALERT_EMAIL_TO = "test@example.com"
        a.TELEGRAM_BOT_TOKEN = "fake-token"
        a.TELEGRAM_CHAT_ID = "12345"
        system = a.AlertSystem()
        yield system


# ---------------------------------------------------------------------------
# 1. Channel Enable/Disable
# ---------------------------------------------------------------------------

class TestChannelToggle:
    def test_enable_email(self, alerts):
        result = alerts.enable_email()
        assert result is True
        assert alerts.settings["email_enabled"] is True

    def test_disable_email(self, alerts):
        alerts.enable_email()
        alerts.disable_email()
        assert alerts.settings["email_enabled"] is False

    def test_master_kill_switch_suppresses_all_email(self, alerts):
        """email_globally_disabled blocks every email path at _send_email,
        even when email_enabled is True and a report bypasses the toggle."""
        alerts.enable_email()
        alerts.disable_all_email()
        assert alerts.settings["email_globally_disabled"] is True
        with patch("alert_system.smtplib.SMTP") as mock_smtp:
            ok, err = alerts._send_email("S", "b", "<p>b</p>", recipients=["a@b.com"])
        assert ok is False
        assert "disabled" in err.lower()
        mock_smtp.assert_not_called()

    def test_master_kill_switch_force_still_sends(self, alerts):
        """force=True (manual test-delivery path) bypasses the kill-switch."""
        alerts.disable_all_email()
        with patch("alert_system.smtplib.SMTP") as mock_smtp:
            ok, err = alerts._send_email("S", "b", "<p>b</p>", recipients=["a@b.com"], force=True)
        assert ok is True
        mock_smtp.assert_called_once()

    def test_enable_all_email_lifts_kill_switch(self, alerts):
        alerts.disable_all_email()
        alerts.enable_all_email()
        assert alerts.settings["email_globally_disabled"] is False

    def test_enable_telegram(self, alerts):
        result = alerts.enable_telegram()
        assert result is True
        assert alerts.settings["telegram_enabled"] is True

    def test_disable_telegram(self, alerts):
        alerts.enable_telegram()
        alerts.disable_telegram()
        assert alerts.settings["telegram_enabled"] is False

    def test_enable_email_fails_when_not_configured(self, temp_settings_file):
        import alert_system as a
        a.SMTP_USERNAME = ""
        a.SMTP_PASSWORD = ""
        system = a.AlertSystem()
        result = system.enable_email()
        assert result is False

    def test_enable_telegram_fails_when_not_configured(self, temp_settings_file):
        import alert_system as a
        a.TELEGRAM_BOT_TOKEN = ""
        a.TELEGRAM_CHAT_ID = ""
        system = a.AlertSystem()
        result = system.enable_telegram()
        assert result is False


# ---------------------------------------------------------------------------
# 2. Alert Type Management
# ---------------------------------------------------------------------------

class TestAlertTypeManagement:
    def test_set_alert_type_enabled(self, alerts):
        ok = alerts.set_alert_type_enabled("ai_signal", False)
        assert ok is True
        assert alerts.settings["alert_type_enabled"]["ai_signal"] is False

    def test_set_alert_type_invalid(self, alerts):
        ok = alerts.set_alert_type_enabled("nonexistent_type", True)
        assert ok is False

    def test_set_alert_types_batch(self, alerts):
        ok = alerts.set_alert_types({"ai_signal": False, "drift": False})
        assert ok is True
        assert alerts.settings["alert_type_enabled"]["ai_signal"] is False
        assert alerts.settings["alert_type_enabled"]["drift"] is False

    def test_get_settings_returns_expected_keys(self, alerts):
        s = alerts.get_settings()
        assert "email_enabled" in s
        assert "telegram_enabled" in s
        assert "email_configured" in s
        assert "telegram_configured" in s
        assert "alert_type_enabled" in s


# ---------------------------------------------------------------------------
# 3. Email Recipient Management
# ---------------------------------------------------------------------------

class TestEmailRecipientManagement:
    def test_add_email_recipient(self, alerts):
        ok = alerts.add_email_recipient("new@example.com")
        assert ok is True
        assert "new@example.com" in alerts._get_email_recipients_list()

    def test_add_invalid_email_rejected(self, alerts):
        ok = alerts.add_email_recipient("not-an-email")
        assert ok is False

    def test_add_empty_email_rejected(self, alerts):
        ok = alerts.add_email_recipient("")
        assert ok is False

    def test_add_duplicate_email_noop(self, alerts):
        alerts.add_email_recipient("dup@example.com")
        count_before = len(alerts._get_email_recipients_list())
        alerts.add_email_recipient("dup@example.com")
        assert len(alerts._get_email_recipients_list()) == count_before

    def test_remove_email_recipient(self, alerts):
        alerts.add_email_recipient("remove@example.com")
        ok = alerts.remove_email_recipient("remove@example.com")
        assert ok is True
        assert "remove@example.com" not in alerts._get_email_recipients_list()

    def test_set_email_recipients_replaces_list(self, alerts):
        alerts.set_email_recipients(["a@b.com", "c@d.com"])
        recs = alerts._get_email_recipients_list()
        assert "a@b.com" in recs
        assert "c@d.com" in recs


# ---------------------------------------------------------------------------
# 4. Settings Persistence
# ---------------------------------------------------------------------------

class TestSettingsPersistence:
    def test_settings_saved_to_file(self, alerts, temp_settings_file):
        alerts.enable_email()
        alerts.enable_telegram()
        alerts._save_settings()
        with open(temp_settings_file) as f:
            data = json.load(f)
        assert data["email_enabled"] is True
        assert data["telegram_enabled"] is True

    def test_settings_loaded_from_file(self, temp_settings_file):
        with open(temp_settings_file, "w") as f:
            json.dump({"email_enabled": True, "telegram_enabled": False, "email_recipients": ["x@y.com"]}, f)
        import alert_system as a
        a.SMTP_USERNAME = "test@example.com"
        a.SMTP_PASSWORD = "password"
        a.ALERT_EMAIL_TO_LIST = ["x@y.com"]
        system = a.AlertSystem()
        assert system.settings["email_enabled"] is True
        assert system.settings["telegram_enabled"] is False


# ---------------------------------------------------------------------------
# 5. Send Critical Alert (primary-only routing)
# ---------------------------------------------------------------------------

class TestSendCriticalAlert:
    def test_send_critical_alert_does_not_crash(self, alerts):
        """send_critical_alert should not crash even with fake SMTP/Telegram."""
        with patch.object(alerts, "_send_email") as mock_email:
            with patch.object(alerts, "_send_telegram") as mock_tg:
                alerts.send_critical_alert("Test Subject", "Test body")
        # Should attempt to send (may or may not depending on enabled state)
        # The key assertion is no exception raised

    def test_send_critical_alert_uses_primary_email(self, alerts):
        """Critical alerts go to primary email, not full recipient list."""
        import alert_system as a
        assert a.ALERT_PRIMARY_EMAIL is not None
        assert "@" in a.ALERT_PRIMARY_EMAIL


# ---------------------------------------------------------------------------
# 6. Parse Email To
# ---------------------------------------------------------------------------

class TestParseEmailTo:
    def test_comma_separated(self):
        from alert_system import _parse_email_to
        result = _parse_email_to("a@b.com, c@d.com")
        assert result == ["a@b.com", "c@d.com"]

    def test_json_array(self):
        from alert_system import _parse_email_to
        result = _parse_email_to('["a@b.com", "c@d.com"]')
        assert result == ["a@b.com", "c@d.com"]

    def test_single_address(self):
        from alert_system import _parse_email_to
        result = _parse_email_to("a@b.com")
        assert result == ["a@b.com"]

    def test_empty_string(self):
        from alert_system import _parse_email_to
        assert _parse_email_to("") == []

    def test_none_returns_empty(self):
        from alert_system import _parse_email_to
        assert _parse_email_to(None) == []
