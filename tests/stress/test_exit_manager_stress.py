# tests/test_exit_manager_stress.py — Stress-style tests for exit_manager
# Rapid calls, edge cases, trailing stop formula. Run: pytest tests/test_exit_manager_stress.py -v

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# -----------------------------------------------------------------------------
# Stress: repeated calls (no double execution, idempotent when gated)
# -----------------------------------------------------------------------------

class TestExitManagerRepeatedCalls:
    """Repeated check_trailing_stops calls do not double-execute or crash."""

    def test_auto_exec_disabled_repeated_calls_no_side_effects(self):
        """Many calls with auto_execution_enabled=False: no TWAP."""
        with patch("exit_manager._get_execution_config", return_value={"auto_execution_enabled": False, "auto_execution_exchange": "coinbase"}):
            with patch("exit_manager._get_open_positions") as mock_pos:
                with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                    from exit_manager import check_trailing_stops
                    for _ in range(50):
                        check_trailing_stops()
        mock_twap.assert_not_called()

    def test_empty_positions_repeated_calls_no_twap(self, temp_positions):
        """Many calls with no open positions: no TWAP, no crash."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=[]):
                with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                    from exit_manager import check_trailing_stops
                    for _ in range(30):
                        check_trailing_stops()
        mock_twap.assert_not_called()

    def test_non_coinbase_exchange_skips_every_time(self, temp_positions):
        """Exchange != coinbase: no positions iterated, no TWAP."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "kraken",
        }):
            with patch("exit_manager._get_open_positions") as mock_pos:
                    with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                        from exit_manager import check_trailing_stops
                        for _ in range(20):
                            check_trailing_stops()
        mock_twap.assert_not_called()
        # May or may not read positions depending on implementation; at least no TWAP
        assert mock_twap.call_count == 0


# -----------------------------------------------------------------------------
# Edge cases: missing coin_id, profit_pct < 5, empty current_prices
# -----------------------------------------------------------------------------

class TestExitManagerEdgeCases:
    """Edge inputs: missing fields, zero/empty values."""

    def test_skips_position_without_coin_id(self, temp_positions):
        """Positions with no coin_id are skipped; no TWAP."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=[
                    {"id": 1, "coin_id": None, "entry_price": 100, "quantity": 1},
                    {"id": 2, "coin_symbol": "BTC", "entry_price": 100, "quantity": 1},
                ]):
                    with patch("exit_manager._get_current_prices", return_value={"bitcoin": 50.0}):
                        with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                            with patch("exit_manager._get_alerts"):
                                with patch("exit_manager._should_alert", return_value=True):
                                    from exit_manager import check_trailing_stops
                                    check_trailing_stops()
        mock_twap.assert_not_called()

    def test_skips_when_profit_below_5_pct(self, temp_positions):
        """Trailing logic only runs when profit_pct >= 5; below that, no TWAP."""
        # entry 100, high_water 102, current 101 -> profit_pct 1% -> skip
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=[
                    {
                        "id": 1, "coin_id": "bitcoin", "coin_symbol": "BTC", "coin_name": "Bitcoin",
                        "entry_price": 100.0, "high_water_mark": 102.0, "quantity": 1.0,
                        "ai_stop_pct": 2.0,
                    },
                ]):
                    with patch("exit_manager._get_current_prices", return_value={"bitcoin": 101.0}):
                        with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                            with patch("exit_manager._update_position"):
                                from exit_manager import check_trailing_stops
                                check_trailing_stops()
        mock_twap.assert_not_called()

    def test_empty_current_prices_returns_early(self, temp_positions):
        """When current_prices is empty (and None triggers fetch that returns {}), no crash."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=[
                    {"id": 1, "coin_id": "bitcoin", "entry_price": 100, "quantity": 1},
                ]):
                    with patch("exit_manager._get_current_prices", return_value={}):
                        with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                            from exit_manager import check_trailing_stops
                            check_trailing_stops()
        mock_twap.assert_not_called()


# -----------------------------------------------------------------------------
# Trailing stop formula: high_water * 0.97; boundary at threshold
# -----------------------------------------------------------------------------

class TestExitManagerTrailingFormula:
    """Trailing stop boundary behavior — derives prices from the LIVE thresholds so the test
    stays correct as the trail width / activation are tuned (Phase 1: 13% trail, 8% activation)."""

    def test_trailing_threshold_boundary_triggers(self, temp_positions):
        """Price at/just below high_water*(1 - trail_pct/100) triggers trailing path when
        profit >= activation. Boundary derived from thresholds (not a hardcoded 0.97)."""
        from trade_journal import open_position, update_position, get_open_positions
        from thresholds import get_threshold
        trail = float(get_threshold('TRAILING_STOP_TRAIL_PCT', 13))
        activate = float(get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8))

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        # Peak well above activation; current sits just below the trailing line yet still >= activation profit.
        high_water = round(100.0 * (1 + (activate + trail + 5) / 100), 4)
        current = round(high_water * (1 - trail / 100) - 0.05, 4)
        assert (current - 100.0) >= activate  # still in the trailing-active zone
        update_position(pos["id"], {"high_water_mark": high_water})
        current_prices = {"bitcoin": current}

        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                    with patch("exit_manager._execute_market_sell_twap", return_value={"success": True, "fill_price": 108.5}) as mock_twap:
                        with patch("exit_manager._close_position", return_value=(MagicMock(), None)):
                            with patch("exit_manager._get_alerts"):
                                with patch("exit_manager._should_alert", return_value=True):
                                    from exit_manager import check_trailing_stops
                                    check_trailing_stops(current_prices)
        mock_twap.assert_called_once()
        assert mock_twap.call_args[1]["quantity"] == 1

    def test_above_trailing_threshold_no_twap(self, temp_positions):
        """Price ABOVE the trailing line does not trigger TWAP, even when trailing is active."""
        from trade_journal import open_position, update_position, get_open_positions
        from thresholds import get_threshold
        trail = float(get_threshold('TRAILING_STOP_TRAIL_PCT', 13))
        activate = float(get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8))

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        # Activated (profit >= activation) but current sits ABOVE the trailing line -> no trigger.
        high_water = round(100.0 * (1 + (activate + trail + 5) / 100), 4)
        current = round(high_water * (1 - trail / 100) + 2.0, 4)
        assert (current - 100.0) >= activate
        update_position(pos["id"], {"high_water_mark": high_water})
        current_prices = {"bitcoin": current}

        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                    with patch("exit_manager._update_position"):
                        with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                            with patch("exit_manager._get_alerts"):
                                with patch("exit_manager._should_alert", return_value=True):
                                    from exit_manager import check_trailing_stops
                                    check_trailing_stops(current_prices)
        mock_twap.assert_not_called()
