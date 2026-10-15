"""Alert system API routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from admin_api_guard import require_admin_write_access

alerts_bp = Blueprint("alerts", __name__)


@alerts_bp.route("/api/alerts/settings", methods=["GET"])
def get_alert_settings():
    """Get current alert settings and configuration status"""
    import app as app_module

    try:
        settings = app_module.alerts.get_settings()
        return jsonify(settings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@alerts_bp.route("/api/alerts/enable/<channel>", methods=["POST"])
def enable_alert_channel(channel):
    """Enable an alert channel (email or telegram)"""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        if channel == "email":
            success = app_module.alerts.enable_email()
        elif channel == "telegram":
            success = app_module.alerts.enable_telegram()
        else:
            return jsonify({"error": "Invalid channel"}), 400

        return jsonify({"success": success, "channel": channel, "enabled": success})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@alerts_bp.route("/api/alerts/disable/<channel>", methods=["POST"])
def disable_alert_channel(channel):
    """Disable an alert channel (email or telegram)"""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        if channel == "email":
            success = app_module.alerts.disable_email()
        elif channel == "telegram":
            success = app_module.alerts.disable_telegram()
        else:
            return jsonify({"error": "Invalid channel"}), 400

        return jsonify({"success": success, "channel": channel, "enabled": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@alerts_bp.route("/api/alerts/settings", methods=["POST"])
def update_alert_settings():
    """
    Update alert settings. Body: { email_enabled?, telegram_enabled?, alert_type_enabled?: { ai_signal?, ... }, email_recipients?: [...] }
    """
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        alerts = app_module.alerts
        if data.get("email_globally_disabled") is not None:
            if data["email_globally_disabled"]:
                alerts.disable_all_email()
            else:
                alerts.enable_all_email()
        if data.get("email_enabled") is not None:
            if data["email_enabled"]:
                alerts.enable_email()
            else:
                alerts.disable_email()
        if data.get("telegram_enabled") is not None:
            if data["telegram_enabled"]:
                alerts.enable_telegram()
            else:
                alerts.disable_telegram()
        if data.get("alert_type_enabled") is not None and isinstance(data["alert_type_enabled"], dict):
            alerts.set_alert_types(
                data["alert_type_enabled"],
                email_map=data.get("alert_type_email") if isinstance(data.get("alert_type_email"), dict) else None,
                telegram_map=data.get("alert_type_telegram") if isinstance(data.get("alert_type_telegram"), dict) else None,
            )
        if data.get("email_recipients") is not None and isinstance(data["email_recipients"], list):
            alerts.set_email_recipients(data["email_recipients"])
        return jsonify(alerts.get_settings())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@alerts_bp.route("/api/alerts/email/add", methods=["POST"])
def add_alert_email():
    """Add an email recipient. Body: { email: "user@example.com" }"""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        email = (data.get("email") or "").strip()
        if not email or "@" not in email:
            return jsonify({"error": "Valid email address required"}), 400
        success = app_module.alerts.add_email_recipient(email)
        return jsonify({"success": success, "settings": app_module.alerts.get_settings()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@alerts_bp.route("/api/alerts/email/remove", methods=["POST"])
def remove_alert_email():
    """Remove an email recipient. Body: { email: "user@example.com" }"""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        email = (data.get("email") or "").strip()
        if not email:
            return jsonify({"error": "Email address required"}), 400
        success = app_module.alerts.remove_email_recipient(email)
        return jsonify({"success": success, "settings": app_module.alerts.get_settings()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@alerts_bp.route("/api/alerts/test", methods=["POST"])
def send_test_alert():
    """Send a test alert. By default sends to all configured channels (email + Telegram) to verify both work."""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        alerts = app_module.alerts
        use_all_configured = data.get("all_configured", True)
        if use_all_configured:
            result = alerts.send_test_to_all_configured()
        else:
            test_coin = {
                "id": "bitcoin",
                "name": "Bitcoin",
                "symbol": "BTC",
                "signal": "BUY",
                "current_price": 45000.50,
                "stop_loss_pct": 2.5,
                "take_profit_pct": 7.5,
                "stop_loss_price": 43875.49,
                "take_profit_price": 48375.54,
                "risk_reward_ratio": 3.0,
                "fear_greed": 68,
                "indicators": {"ch24h": 3.2},
                "price_change_percentage_7d_in_currency": 8.1,
                "price_change_percentage_30d_in_currency": 15.4,
                "enhanced": {"funding_rate": {"current": 0.012}},
                "ai_recommendation": {
                    "consensus": {
                        "consensus_signal": "STRONG_BUY",
                        "reasoning": "TEST ALERT - This is a test from your trading system.",
                        "market_regime": "BULL",
                    },
                    "quant": {
                        "quant_confidence": 78,
                        "reasoning": "TEST ALERT - This is a test from your trading system.",
                        "signal_badges": ["pullback_in_trend", "light_sniper_entry"],
                        "quant_path": "pullback_in_trend",
                        "confirmation_score": 82,
                        "edge_score": 0.68,
                    },
                    "position_sizing": {"pct_of_account": 2.0, "position_size_usd": 1000.00},
                },
            }
            result = alerts.alert_ai_signal(test_coin)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "status": "failed"}), 500
