# tests/test_refresh_smoke.py — Smoke test for main refresh/decision path with mocks
# Runs refresh_data() once with all heavy deps mocked; asserts no exception and cache updated.
# Run: pytest tests/test_refresh_smoke.py -v

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _minimal_raw():
    return {
        "coins": [
            {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "current_price": 50000.0},
        ],
        "fear_greed": {"value": 50},
        "macro": {},
        "sentiment": {},
        "global_market_cap": {},
    }


def _minimal_with_indicators(raw):
    return {
        **raw,
        "coins": [{**c, "indicators": {"volatility": 50, "atr_pct": 2.0}} for c in raw["coins"]],
    }


def _minimal_scored(with_indicators):
    return {
        "coins": [
            {**c, "score": 0, "signal": "HOLD", "indicators": c.get("indicators", {})}
            for c in with_indicators["coins"]
        ],
        "summary": {"total": 1, "buy": 0, "hold": 1, "sell": 0},
        "fear_greed": with_indicators.get("fear_greed", {}),
        "global_market_cap": with_indicators.get("global_market_cap"),
    }


def _minimal_regime():
    return {
        "regime": "SIDEWAYS",
        "confidence": 70,
        "strategy_adjustment": {"recommendation": "hold"},
    }


class TestRefreshSmoke:
    """Smoke test: main refresh/decision path runs to completion with mocks."""

    def test_refresh_data_completes_with_mocks(self):
        """refresh_data() runs without exception; cached_data and last_updated set."""
        # Import app first so patches can target app.* (skip if deps missing, e.g. docx)
        try:
            import app as app_mod  # noqa: F401
        except ModuleNotFoundError as e:
            pytest.skip(f"app deps missing: {e}")
        raw = _minimal_raw()
        with_indicators = _minimal_with_indicators(raw)
        scored = _minimal_scored(with_indicators)
        regime_data = _minimal_regime()

        mock_fetcher = MagicMock()
        mock_fetcher.get_all_data.return_value = raw
        mock_regime = MagicMock()
        mock_regime.detect_regime.return_value = regime_data
        mock_consensus = MagicMock()
        mock_consensus.update_outcomes.return_value = None
        mock_consensus.get_all_recommendations.return_value = []
        mock_alerts = MagicMock()

        patches = [
            patch("app.do_auto_sync_balance"),
            patch("app.get_test_mode", return_value=True),
            patch("app.fetcher", mock_fetcher),
            patch("indicators.calculate_indicators", return_value=with_indicators),
            patch("scorer.score_tokens", return_value=scored),
            patch("app.regime_detector", mock_regime),
            patch("app.get_open_positions", return_value=[]),
            patch("app.get_all_positions", return_value=[]),
            patch("app.get_execution_config", return_value={}),
            patch("app.mark_source"),
            patch("app.position_sizer", MagicMock(account_size=20000)),
            patch("app.get_portfolio_risk_summary", return_value={"total_risk_pct": 0}),
            patch("app.get_correlation_adjustment_factors", return_value={}),
            patch("app.apply_correlation_adjustment", side_effect=lambda pos, _: pos),
            patch("app.get_live_stats_by_regime", return_value={}),
            patch("app.check_for_drift", return_value=(False, None)),
            patch("app.alerts", mock_alerts),
            patch("app._build_account_overview", return_value={"cash_available_to_trade": 10000, "total_cash": 10000}),
            patch("exit_manager.check_trailing_stops"),
        ]
        for p in patches:
            p.start()
        try:
            app_mod.refresh_data()
            assert app_mod.cached_data is not None
            assert "coins" in app_mod.cached_data
            assert "summary" in app_mod.cached_data
            assert app_mod.last_updated != "Never"
        finally:
            for p in reversed(patches):
                p.stop()
