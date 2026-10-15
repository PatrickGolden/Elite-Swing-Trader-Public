# tests/test_loading_sequence.py — Loading sequence and daily status report
# Proves: (1) /api/data returns UNKNOWN/Never before first refresh; (2) daily status
# is not sent until at least one refresh has completed; (3) after "refresh", data and email use real regime.
# Run from project root: pytest tests/test_loading_sequence.py -v

import os
import sys
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
    """Flask test client with scheduler skipped."""
    with patch.dict(os.environ, {"ELITE_SKIP_SCHEDULER": "1"}, clear=False):
        from app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c


@pytest.fixture
def app_module():
    """App module for patching globals."""
    with patch.dict(os.environ, {"ELITE_SKIP_SCHEDULER": "1"}, clear=False):
        import app as app_module
        yield app_module


# ---------------------------------------------------------------------------
# 1. API returns initial state before any refresh
# ---------------------------------------------------------------------------

class TestDataEndpointInitialState:
    """Before any refresh, /api/data must expose last_updated 'Never' and regime UNKNOWN."""

    def test_data_returns_never_and_unknown_when_not_refreshed(self, app_module, client):
        # Force initial state as it is at server startup (before _initial_refresh completes)
        initial_cached = {
            "coins": [],
            "summary": {"total": 0, "buy": 0, "hold": 0, "sell": 0},
            "fear_greed": {},
            "macro": {},
            "sentiment": {},
            "global_market_cap": {},
            "regime": {"regime": "UNKNOWN", "confidence": 0},
            "onchain_status": "fallback",
            "onchain_overview": {},
            "performance_analytics": {},
            "portfolio_risk_summary": {},
            "model_attribution": {},
        }
        with patch.object(app_module, "cached_data", initial_cached), patch.object(
            app_module, "last_updated", "Never"
        ):
            r = client.get("/api/data")
            assert r.status_code == 200
            data = r.get_json()
            assert data.get("last_updated") == "Never"
            reg = data.get("regime") or {}
            assert (reg.get("regime") or "UNKNOWN") == "UNKNOWN"


# ---------------------------------------------------------------------------
# 2. Daily status report is not sent when last_updated is Never
# ---------------------------------------------------------------------------

class TestDailyStatusReportSequence:
    """Daily status email must not be sent until at least one refresh has completed."""

    def test_run_daily_status_skips_when_last_updated_never(self, app_module):
        with patch.object(app_module, "last_updated", "Never"):
            with patch.object(app_module.alerts, "send_daily_status_report", MagicMock()) as mock_send:
                app_module._run_daily_status_report_if_due()
                mock_send.assert_not_called()

    def test_build_daily_status_summary_uses_unknown_when_regime_missing(self, app_module):
        with patch.object(app_module, "cached_data", {"regime": {"regime": "UNKNOWN", "confidence": 0}}):
            html = app_module._build_daily_status_summary()
            assert "Unknown" in html or "UNKNOWN" in html

    def test_build_daily_status_summary_uses_real_regime_when_present(self, app_module):
        with patch.object(
            app_module,
            "cached_data",
            {"regime": {"regime": "BULL", "confidence": 85}, "onchain_status": "live"},
        ):
            html = app_module._build_daily_status_summary()
            assert "BULL" in html


# ---------------------------------------------------------------------------
# 3. After "refresh", API and summary use real regime
# ---------------------------------------------------------------------------

class TestDataEndpointAfterRefresh:
    """After a refresh, /api/data must expose last_updated and real regime."""

    def test_data_returns_regime_and_timestamp_after_refresh(self, app_module, client):
        refreshed_cached = {
            "coins": [{"id": "bitcoin", "symbol": "BTC", "current_price": 50000}],
            "summary": {"total": 1, "buy": 0, "hold": 1, "sell": 0},
            "regime": {"regime": "BULL", "confidence": 80},
            "last_updated": "2026-02-24 12:00:00",
        }
        # cached_data is merged with defaults in _safe_data_response; ensure regime is present
        with patch.object(app_module, "cached_data", refreshed_cached), patch.object(
            app_module, "last_updated", "2026-02-24 12:00:00"
        ):
            r = client.get("/api/data")
            assert r.status_code == 200
            data = r.get_json()
            assert data.get("last_updated") == "2026-02-24 12:00:00"
            reg = data.get("regime") or {}
            assert reg.get("regime") == "BULL"


# ---------------------------------------------------------------------------
# 4. Daily status counts only live open positions (exclude_test=True)
# ---------------------------------------------------------------------------

class TestDailyStatusOpenPositionsCount:
    """Daily status email must count only live positions, excluding test/paper trades."""

    def test_build_daily_status_summary_excludes_test_positions(self, app_module):
        # When exclude_test=False there are 2 open (e.g. test positions); when exclude_test=True there are 0 live.
        def get_open_positions(exclude_test=False):
            if exclude_test:
                return []
            return [{"id": 1, "status": "OPEN", "is_test": True}, {"id": 2, "status": "OPEN", "is_test": True}]

        with patch.object(app_module, "cached_data", {"regime": {"regime": "BULL", "confidence": 80}, "onchain_status": "live"}):
            with patch("app.get_open_positions", side_effect=get_open_positions):
                html = app_module._build_daily_status_summary()
        # Must show 0 open positions (live only), not 2 (accept old or new daily status HTML format)
        assert (
            "Open positions:</strong> 0 " in html
            or "Open positions:</strong> 0<" in html
            or "Open positions:</strong> 0\n" in html
            or ("Open Positions" in html and ">0</div>" in html)
        )

    def test_build_daily_status_summary_shows_live_count_when_both_exist(self, app_module):
        # 1 live + 1 test open → email should show 1
        def get_open_positions(exclude_test=False):
            if exclude_test:
                return [{"id": 1, "status": "OPEN", "is_test": False}]
            return [
                {"id": 1, "status": "OPEN", "is_test": False},
                {"id": 2, "status": "OPEN", "is_test": True},
            ]

        with patch.object(app_module, "cached_data", {"regime": {"regime": "BULL", "confidence": 80}, "onchain_status": "live"}):
            with patch("app.get_open_positions", side_effect=get_open_positions):
                html = app_module._build_daily_status_summary()
        # Accept old or new daily status HTML format
        assert (
            "Open positions:</strong> 1 " in html
            or "Open positions:</strong> 1<" in html
            or "Open positions:</strong> 1\n" in html
            or ("Open Positions" in html and ">1</div>" in html)
        )


# ---------------------------------------------------------------------------
# 5. Loading sequence: correct order of operations
# ---------------------------------------------------------------------------

class TestLoadingSequenceOrder:
    """Document and assert the intended loading sequence."""

    def test_intended_sequence_documented(self):
        # Intended order: (1) Server starts with cached_data default, last_updated 'Never'.
        # (2) Initial refresh runs in background; when it completes, cached_data and last_updated are set.
        # (3) Daily report runs every 60s but must skip when last_updated == 'Never'.
        # (4) Frontend GET /api/data: if last_updated is 'Never', show "Loading…" for regime and retry.
        assert True  # Placeholder: sequence is enforced by the tests above and code changes
