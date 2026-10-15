# tests/test_api_routes.py — Flask API route tests: regime, account, journal,
# execution-config, performance, health
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
import os
import json
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
def client():
    """Flask test client with heavy deps mocked."""
    with patch.dict(os.environ, {"ELITE_SKIP_SCHEDULER": "1"}, clear=False):
        from app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c


@pytest.fixture
def temp_account_config(monkeypatch, tmp_path):
    cfg = {"total_cash": 20000, "risk_pct": 2.0, "max_cash_usage_pct": 40, "dust_threshold_usd": 1.0, "let_winners_run": False}
    path = tmp_path / "account_config.json"
    path.write_text(json.dumps(cfg))
    import app as app_module
    import app_config as app_config_module
    monkeypatch.setattr(app_module, "ACCOUNT_CONFIG_FILE", str(path))
    monkeypatch.setattr(app_config_module, "ACCOUNT_CONFIG_FILE", str(path))
    yield str(path)


@pytest.fixture
def temp_execution_config(monkeypatch, tmp_path):
    cfg = {
        "auto_execution_enabled": True,
        "auto_execution_exchange": "coinbase",
        "auto_sync_balance": True,
        "twap_min_usd": 500.0,
        "stale_position_rule_enabled": True,
    }
    main_path = tmp_path / "execution_config.json"
    sidecar_path = tmp_path / "last_auto_sync_at.json"
    main_path.write_text(json.dumps(cfg), encoding="utf-8")
    import app as app_module
    import app_config as app_config_module
    monkeypatch.setattr(app_module, "EXECUTION_CONFIG_FILE", str(main_path))
    monkeypatch.setattr(app_module, "LAST_AUTO_SYNC_FILE", str(sidecar_path))
    monkeypatch.setattr(app_config_module, "EXECUTION_CONFIG_FILE", str(main_path))
    monkeypatch.setattr(app_config_module, "LAST_AUTO_SYNC_FILE", str(sidecar_path))
    yield str(main_path)


# ---------------------------------------------------------------------------
# 1. Health / Data endpoints
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.get_json()
        assert "sources" in data or "status" in data

    def test_data_returns_200(self, client):
        r = client.get("/api/data")
        assert r.status_code == 200
        data = r.get_json()
        assert "server_started_at" in data

    def test_roadmap_automation_status_returns_200(self, client):
        r = client.get("/api/roadmap/automation")
        assert r.status_code == 200
        data = r.get_json()
        assert "feature_states" in data or "error" in data

    def test_roadmap_automation_decision_validation(self, client):
        r = client.post("/api/roadmap/automation/decision", json={"feature_id": "x", "decision": "invalid"})
        assert r.status_code == 400
        data = r.get_json()
        assert "error" in data

    def test_roadmap_automation_decision_requires_rationale(self, client):
        r = client.post("/api/roadmap/automation/decision", json={"feature_id": "x", "decision": "approve"})
        assert r.status_code == 400
        data = r.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# 2. Regime endpoint
# ---------------------------------------------------------------------------

class TestRegimeEndpoint:
    def test_regime_returns_200(self, client):
        r = client.get("/api/regime")
        assert r.status_code == 200
        data = r.get_json()
        assert "regime" in data or "error" in data or isinstance(data, dict)


# ---------------------------------------------------------------------------
# 3. Account endpoints
# ---------------------------------------------------------------------------

class TestAccountEndpoints:
    def test_account_overview_returns_200(self, client):
        r = client.get("/api/account/overview")
        assert r.status_code == 200

    def test_account_update_changes_config(self, client, temp_account_config):
        r = client.post("/api/account/update", json={"total_cash": 30000, "risk_pct": 1.5})
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("success") is True or "total_cash" in data

    def test_account_overview_returns_heloc_fields(self, client):
        r = client.get("/api/account/overview")
        assert r.status_code == 200
        data = r.get_json()
        assert "heloc_mode" in data
        assert isinstance(data["heloc_mode"], bool)
        assert "heloc_notional" in data
        assert isinstance(data["heloc_notional"], (int, float))

    def test_funding_get_returns_200(self, client):
        r = client.get("/api/funding")
        assert r.status_code == 200
        data = r.get_json()
        assert "heloc_mode" in data
        assert "heloc_notional" in data
        assert "heloc_rate_pct" in data
        assert "usdc_yield_pct" in data
        assert "metrics" in data
        metrics = data["metrics"]
        assert "net_idle_cost_pct" in metrics
        assert "hurdle_per_trade_pct" in metrics

    def test_funding_update(self, client, temp_account_config):
        r = client.post(
            "/api/funding/update",
            json={"heloc_mode": True, "heloc_notional": 50000, "heloc_rate_pct": 8.5, "heloc_drawn": 5000},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("heloc_mode") is True
        assert data.get("heloc_notional") == 50000
        assert data.get("heloc_rate_pct") == 8.5
        assert data.get("heloc_drawn") == 5000

    def test_funding_performance(self, client):
        r = client.get("/api/funding/performance?period=30")
        assert r.status_code == 200
        data = r.get_json()
        assert "trading_pnl" in data
        assert "carry_cost" in data
        assert "net_after_carry" in data


# ---------------------------------------------------------------------------
# 4. Journal endpoints
# ---------------------------------------------------------------------------

class TestJournalEndpoints:
    def test_journal_list_returns_200(self, client):
        r = client.get("/api/journal/positions")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, (list, dict))

    def test_journal_open_position(self, client, temp_positions, temp_account_config):
        # position_sizer.account_size may be overwritten by exchange sync; ensure sufficient cash
        import app as app_module
        with patch.object(app_module.position_sizer, 'account_size', 20000):
            r = client.post("/api/journal/open", json={
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
                "ai_target_pct": 6, "ai_stop_pct": 2.5,
            })
        assert r.status_code == 200
        data = r.get_json()
        pos = data.get("position", data)
        assert pos.get("status") == "OPEN" or "id" in pos or data.get("status") == "ok"

    def test_journal_close_position(self, client, temp_positions, temp_account_config):
        # Open then close; patch position_sizer so cash check passes
        import app as app_module
        with patch.object(app_module.position_sizer, 'account_size', 20000):
            open_r = client.post("/api/journal/open", json={
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
            })
        assert open_r.status_code == 200
        data = open_r.get_json()
        pos = data.get("position", data)
        pid = pos.get("id", 1)
        r = client.post(f"/api/journal/close/{pid}", json={"exit_price": 52000, "exit_reason": "manual"})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 5. Brain mode toggle
# ---------------------------------------------------------------------------

class TestBrainModeEndpoint:
    """Brain mode endpoint was removed (quant-first pipeline). Verify 404."""
    def test_brain_mode_get_removed(self, client):
        r = client.get("/api/brain-mode")
        assert r.status_code == 404

    def test_brain_mode_post_removed(self, client):
        r = client.post("/api/brain-mode", json={"use_6model": True})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 6. Execution config
# ---------------------------------------------------------------------------

class TestExecutionConfigEndpoint:
    def test_execution_config_get(self, client):
        r = client.get("/api/execution-config")
        assert r.status_code == 200
        data = r.get_json()
        assert "stale_position_rule_enabled" in data

    def test_execution_config_post(self, client, temp_execution_config):
        """Must use temp file — POST persists auto_execution_enabled and would clobber the dev machine."""
        r = client.post(
            "/api/execution-config",
            json={"auto_execution_enabled": False, "disable_intent": "dashboard_manual", "disable_confirmed": True},
        )
        assert r.status_code == 200
        saved = json.loads(Path(temp_execution_config).read_text(encoding="utf-8"))
        assert saved.get("auto_execution_enabled") is False

    def test_execution_config_post_rejects_disable_without_intent(self, client):
        with patch("app.set_execution_config") as set_cfg:
            r = client.post("/api/execution-config", json={"auto_execution_enabled": False})
        assert r.status_code == 400
        data = r.get_json()
        assert "error" in data
        set_cfg.assert_not_called()

    def test_execution_config_post_disable_with_intent_persists_false(self, client, temp_execution_config):
        r = client.post(
            "/api/execution-config",
            json={"auto_execution_enabled": False, "disable_intent": "dashboard_manual", "disable_confirmed": True},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("auto_execution_enabled") is False
        saved = json.loads(Path(temp_execution_config).read_text(encoding="utf-8"))
        assert saved.get("auto_execution_enabled") is False

    def test_execution_config_post_rejects_disable_without_confirmed(self, client):
        with patch("app.set_execution_config") as set_cfg:
            r = client.post(
                "/api/execution-config",
                json={"auto_execution_enabled": False, "disable_intent": "dashboard_manual"},
            )
        assert r.status_code == 400
        data = r.get_json()
        assert "error" in data
        set_cfg.assert_not_called()

    def test_execution_config_post_stale_position_rule(self, client, temp_execution_config):
        """Isolate from repo execution_config.json so pytest does not rewrite live toggles."""
        r = client.post("/api/execution-config", json={"stale_position_rule_enabled": False})
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("stale_position_rule_enabled") is False
        r2 = client.post("/api/execution-config", json={"stale_position_rule_enabled": True})
        assert r2.status_code == 200
        assert r2.get_json().get("stale_position_rule_enabled") is True
        saved = json.loads(Path(temp_execution_config).read_text(encoding="utf-8"))
        assert saved.get("stale_position_rule_enabled") is True

    def test_execution_config_post_rejects_non_local_without_token(self, client, monkeypatch):
        monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
        r = client.post(
            "/api/execution-config",
            json={"auto_execution_enabled": True},
            environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
        )
        assert r.status_code == 403
        data = r.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# 7. Performance stats
# ---------------------------------------------------------------------------

class TestPerformanceEndpoint:
    def test_performance_returns_200(self, client):
        r = client.get("/api/backtest/performance")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 8b. Performance tab endpoints (recursive report, monthly review, learning, backtest)
# ---------------------------------------------------------------------------

class TestPerformanceTabEndpoints:
    """Endpoints used by the Performance tab: last-report, monthly-review, learning-report, backtest/run, optimizer/resend."""

    def test_optimizer_last_report_returns_200_or_404(self, client):
        r = client.get("/api/optimizer/last-report")
        assert r.status_code in (200, 404)
        data = r.get_json()
        assert isinstance(data, dict)
        if r.status_code == 404:
            assert "error" in data
        else:
            assert "snapshot" in data
            assert "auto_applied" in data
            assert "pending_review" in data
            assert "evaluations" in data
            assert "path_performance" in data

    def test_monthly_review_returns_200(self, client):
        r = client.get("/api/monthly-review?days=30")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, dict)
        assert "performance" in data
        assert "model_attribution" in data
        assert "risk" in data

    def test_monthly_review_accepts_days_param(self, client):
        for days in ("30", "90", "365", "0"):
            r = client.get("/api/monthly-review?days=" + days)
            assert r.status_code == 200, "days=%s" % days

    def test_learning_report_returns_200(self, client):
        r = client.get("/api/learning-report")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, dict)

    def test_backtest_run_post_returns_200(self, client):
        r = client.post(
            "/api/backtest/run",
            content_type="application/json",
            data=json.dumps({"days": 90}),
        )
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, dict)
        assert "error" in data or "sharpe_passed" in data or "walk_forward" in data

    def test_optimizer_resend_post_returns_200_or_404(self, client):
        r = client.post(
            "/api/optimizer/resend",
            content_type="application/json",
            data=json.dumps({"for_today_only": False}),
        )
        assert r.status_code in (200, 404)
        data = r.get_json()
        assert isinstance(data, dict)
        assert "status" in data or "error" in data or "message" in data


class TestOptimizerAndRoadmapEndpoints:
    def test_optimizer_last_report_returns_json(self, client):
        r = client.get("/api/optimizer/last-report")
        assert r.status_code in (200, 404)
        data = r.get_json()
        assert isinstance(data, dict)

    def test_optimizer_lean_status_returns_200(self, client):
        r = client.get("/api/optimizer/lean-status")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, dict)

    def test_optimizer_status_returns_veto_summary(self, client):
        r = client.get("/api/optimizer/status")
        assert r.status_code == 200
        data = r.get_json()
        assert "veto_log_30d" in data
        veto = data["veto_log_30d"]
        assert "total_vetoes" in veto
        assert "by_regime" in veto

    def test_roadmap_doc_returns_200(self, client):
        r = client.get("/api/roadmap/doc")
        assert r.status_code == 200
        data = r.get_json()
        assert "markdown" in data or "error" in data

    def test_data_sources_returns_live_payload(self, client):
        r = client.get("/api/data/sources")
        assert r.status_code == 200
        data = r.get_json()
        assert "sources" in data
        assert isinstance(data["sources"], list)
        assert len(data["sources"]) >= 1
        assert "note" in data
        row0 = data["sources"][0]
        assert "live" in row0
        assert "status" in row0


# ---------------------------------------------------------------------------
# 9. Position sizer settings
# ---------------------------------------------------------------------------

class TestPositionSizerEndpoints:
    def test_get_settings(self, client):
        r = client.get("/api/position-size/settings")
        assert r.status_code == 200

    def test_update_settings(self, client):
        r = client.post("/api/position-size/update", json={"account_size": 25000})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 10. Schedule / costs
# ---------------------------------------------------------------------------

class TestScheduleEndpoints:
    def test_schedule_status(self, client):
        r = client.get("/api/schedule/status")
        assert r.status_code == 200

    def test_cost_endpoint(self, client):
        r = client.get("/api/schedule/costs")
        assert r.status_code == 200

    def test_schedule_costs_interval(self, client):
        r = client.get("/api/schedule/costs/1hour")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, dict)

    def test_schedule_intervals(self, client):
        r = client.get("/api/schedule/intervals")
        assert r.status_code == 200

    def test_schedule_set_is_post_only(self, client):
        r = client.get("/api/schedule/set")
        assert r.status_code == 405

    def test_schedule_set_requires_interval(self, client):
        r = client.post("/api/schedule/set", json={})
        assert r.status_code == 400
        data = r.get_json()
        assert data.get("success") is False


class TestRefreshEndpoint:
    def test_manual_refresh_reports_skipped_when_lock_held(self, client):
        with patch("app.refresh_data", return_value="skipped"):
            r = client.post("/api/refresh")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("status") == "skipped"


class TestAdminGuardedPosts:
    def test_refresh_requires_token_when_admin_token_set(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_API_TOKEN", "test-secret-token-for-ci")
        r = client.post("/api/refresh")
        assert r.status_code == 403
        with patch("app.refresh_data", return_value=True):
            r2 = client.post("/api/refresh", headers={"X-Admin-Token": "test-secret-token-for-ci"})
        assert r2.status_code == 200


class TestAdminGuardLocalhostUsesRemoteAddrOnly:
    def test_spoofed_x_forwarded_for_does_not_bypass_without_token(self, client, monkeypatch):
        monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
        r = client.post(
            "/api/execution-config",
            json={"auto_execution_enabled": True},
            environ_overrides={
                "REMOTE_ADDR": "10.0.0.5",
                "HTTP_X_FORWARDED_FOR": "127.0.0.1",
            },
        )
        assert r.status_code == 403


class TestTelegramWebhook:
    def test_disabled_without_secret_token(self, client, monkeypatch):
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
        r = client.post("/api/telegram-webhook", json={}, content_type="application/json")
        assert r.status_code == 503

    def test_rejects_wrong_secret_header(self, client, monkeypatch):
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "tg-secret-ci")
        r = client.post(
            "/api/telegram-webhook",
            json={},
            content_type="application/json",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert r.status_code == 403

    def test_accepts_matching_secret(self, client, monkeypatch):
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "tg-secret-ci")
        r = client.post(
            "/api/telegram-webhook",
            json={},
            content_type="application/json",
            headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret-ci"},
        )
        assert r.status_code == 200
        assert r.get_json().get("ok") is True


class TestReconcileMutatingQueryParams:
    def test_sync_positions_requires_localhost_without_admin_token(self, client, monkeypatch):
        monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
        r = client.get(
            "/api/account/reconcile?sync_positions=1",
            environ_overrides={"REMOTE_ADDR": "10.0.0.9"},
        )
        assert r.status_code == 403


class TestAiAnalyzeRoutesGetOnly:
    def test_ai_top_picks_post_returns_405(self, client):
        r = client.post("/api/ai/top-picks")
        assert r.status_code == 405

    def test_ai_analyze_post_returns_405(self, client):
        r = client.post("/api/ai/analyze/ethereum")
        assert r.status_code == 405


# ---------------------------------------------------------------------------
# 11. Manual / docs
# ---------------------------------------------------------------------------

class TestManualEndpoint:
    def test_manual_returns_200(self, client):
        r = client.get("/api/manual")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 12. Journal positions excludes test positions (merged from test_trade_execution_audit_api.py)
# ---------------------------------------------------------------------------

class TestJournalPositionsApiExcludesTestPositions:
    def test_journal_get_positions_excludes_test_positions(self, app, client):
        """GET /api/journal/positions returns only live positions; test positions do not appear."""
        live_pos = {
            "id": 10, "status": "OPEN", "coin_id": "bitcoin", "coin_symbol": "BTC",
            "entry_price": 50000.0, "quantity": 0.01, "capital_usd": 500.0,
            "is_test": False, "exit_reason": None, "signal": "BUY",
            "current_target_price": 53000.0, "current_stop_price": 49000.0,
        }
        test_pos = {
            "id": 20, "status": "OPEN", "coin_id": "ethereum", "coin_symbol": "ETH",
            "entry_price": 3000.0, "quantity": 0.1, "capital_usd": 300.0,
            "is_test": True, "exit_reason": None, "signal": "BUY",
            "current_target_price": 3300.0, "current_stop_price": 2850.0,
        }
        with patch("routes.journal_bp.get_all_positions", return_value=[live_pos, test_pos]):
            with patch("app.cached_data", {"coins": []}):
                with patch("app.get_active_twap_orders", return_value=[]):
                    with patch("app.fetcher.get_all_data", return_value={"coins": []}):
                        r = client.get("/api/journal/positions")
        assert r.status_code == 200
        data = r.get_json()
        positions = data.get("positions") or []
        ids = [p["id"] for p in positions]
        assert 10 in ids
        assert 20 not in ids

    def test_journal_get_positions_excludes_paper_trade_source(self, app, client):
        """Paper simulations must not appear on the live Positions tab even if is_test were wrong."""
        live_pos = {
            "id": 10, "status": "OPEN", "coin_id": "bitcoin", "coin_symbol": "BTC",
            "entry_price": 50000.0, "quantity": 0.01, "capital_usd": 500.0,
            "is_test": False, "source": None, "exit_reason": None, "signal": "BUY",
            "current_target_price": 53000.0, "current_stop_price": 49000.0,
        }
        paper_pos = {
            "id": 21, "status": "OPEN", "coin_id": "dogecoin", "coin_symbol": "DOGE",
            "entry_price": 0.12, "quantity": 1000.0, "capital_usd": 120.0,
            "is_test": False, "source": "paper_trade", "exit_reason": None, "signal": "BUY",
            "current_target_price": 0.13, "current_stop_price": 0.11,
        }
        with patch("routes.journal_bp.get_all_positions", return_value=[live_pos, paper_pos]):
            with patch("app.cached_data", {"coins": []}):
                with patch("app.get_active_twap_orders", return_value=[]):
                    with patch("app.fetcher.get_all_data", return_value={"coins": []}):
                        r = client.get("/api/journal/positions")
        assert r.status_code == 200
        ids = [p["id"] for p in (r.get_json() or {}).get("positions") or []]
        assert 10 in ids
        assert 21 not in ids


# ---------------------------------------------------------------------------
# 13. No-cache headers and instance identity (merged from test_api_fresh_instance.py)
# ---------------------------------------------------------------------------

class TestApiNoCacheHeaders:
    """API responses must not be cached by browsers."""

    def test_api_data_has_no_cache_headers(self, client):
        r = client.get("/api/data")
        assert r.status_code == 200
        h = r.headers
        assert "no-store" in h.get("Cache-Control", "").lower() or "no-cache" in h.get("Cache-Control", "").lower()
        assert h.get("Pragma") == "no-cache"
        assert h.get("Expires") == "0"

    def test_api_health_has_no_cache_headers(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert "no-store" in r.headers.get("Cache-Control", "").lower() or "no-cache" in r.headers.get("Cache-Control", "").lower()


class TestApiInstanceIdentity:
    """API returns server instance identity so UI can verify fresh connection."""

    def test_api_data_returns_server_started_at(self, client):
        r = client.get("/api/data")
        assert r.status_code == 200
        data = r.get_json()
        assert "server_started_at" in data
        started = data["server_started_at"]
        assert started and "T" in started and "Z" in started

    def test_api_data_returns_server_instance_id(self, client):
        r = client.get("/api/data")
        assert r.status_code == 200
        data = r.get_json()
        assert "server_instance_id" in data
        inst = data["server_instance_id"]
        assert inst and "-" in inst
        parts = inst.split("-", 1)
        assert parts[0].isdigit()

    def test_server_instance_id_matches_current_process(self, client):
        import os
        r = client.get("/api/data")
        data = r.get_json()
        inst = data["server_instance_id"]
        pid_part = inst.split("-", 1)[0]
        assert pid_part == str(os.getpid())
