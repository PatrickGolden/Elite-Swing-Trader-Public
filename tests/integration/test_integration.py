# tests/test_integration.py — Integration tests (medium)
# Proves components integrate correctly, not just work in isolation
# Uses absolute imports; run pytest from project root (c:\swing-trader)
# All execution mocked — no real trades

import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestSignalToExecutionFlow:
    """Full flow: quant signal -> position sizing -> open position."""

    def test_signal_to_execution_flow(self, temp_positions):
        """Quant signal leads to position size, then position can be opened."""
        from quant_signal import generate_quant_signal
        from position_sizer import PositionSizer
        from trade_journal import open_position, get_open_positions

        coin = {"score": 78, "indicators": {"atr_pct": 2.0}, "risk": {"rr_ratio": 3.0}, "enhanced": {}}
        regime = {"regime": "SIDEWAYS"}
        quant = generate_quant_signal(coin, regime, fear_greed_value=50)
        assert quant["quant_signal"] == "BUY"
        assert quant["quant_tier"] in ("STRONG_BUY", "ULTRA_BUY", "MODERATE_BUY")

        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        sizing = sizer.calculate_for_consensus(
            entry_price=45000, stop_loss_pct=2.5,
            consensus_signal=quant["quant_tier"], coin_volatility=50,
        )
        assert sizing["position_size_usd"] > 0
        assert sizing["quantity"] > 0

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 45000, "quantity": sizing["quantity"],
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        assert pos["status"] == "OPEN"
        assert pos["quantity"] == sizing["quantity"]


class TestPositionOpenedAndMonitored:
    """Position appears in open list and has stop/target for monitoring."""

    def test_position_opened_and_monitored(self, temp_positions):
        """Open position is in get_open_positions and has a stop (no fixed target — winners run)."""
        from trade_journal import open_position, get_open_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        pid = pos["id"]

        open_list = get_open_positions()
        found = next((p for p in open_list if p["id"] == pid), None)
        assert found is not None
        assert found["status"] == "OPEN"
        assert found["current_stop_price"] is not None
        assert found["current_stop_price"] < found["entry_price"]
        # Winners always run: no fixed take-profit; trailing stop is the only profit exit.
        assert found["current_target_price"] is None


class TestStopLossClosesPosition:
    """Stop-loss hit closes position (mocked execution)."""

    def test_stop_loss_closes_position(self, temp_positions):
        """When stop hit, position is closed via close_position (mock execute_market_sell)."""
        from trade_journal import open_position, close_position, get_open_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        pid = pos["id"]
        stop_price = float(pos["current_stop_price"])
        current_price = stop_price - 100  # Below stop

        with patch("app.execute_market_sell") as mock_sell:
            mock_sell.return_value = {"success": True, "fill_price": current_price}
            with patch("app.alerts") as mock_alerts:
                with patch("app._should_alert", return_value=True):
                    from app import _handle_stop_loss_hit
                    _handle_stop_loss_hit(pos, current_price, auto_exec=True)

        mock_sell.assert_called_once()
        closed_list = [p for p in get_open_positions() if p["id"] == pid]
        # Position should be closed (removed from open)
        all_positions = __import__("trade_journal").get_all_positions()
        closed_pos = next((p for p in all_positions if p["id"] == pid), None)
        assert closed_pos is not None
        assert closed_pos["status"] == "CLOSED"
        assert closed_pos["exit_reason"] == "stop_loss"


class TestTakeProfitClosesPosition:
    """Take-profit hit closes position (mocked execution)."""

    def test_take_profit_closes_position(self, temp_positions):
        """The take-profit handler still closes a LEGACY position that carries a fixed target.
        (New positions never get a target — winners run — but the handler must still work for
        pre-existing positions opened before the change.)"""
        from trade_journal import open_position, update_position, get_all_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        pid = pos["id"]
        # Simulate a legacy position that still has a fixed target.
        target_price = 50000 * 1.06
        update_position(pid, {"current_target_price": target_price, "ai_target_price": target_price})
        pos["current_target_price"] = target_price
        pos["ai_target_price"] = target_price
        current_price = target_price + 500  # Above target

        with patch("app.execute_market_sell") as mock_sell:
            mock_sell.return_value = {"success": True, "fill_price": current_price}
            with patch("app.alerts"):
                with patch("app._should_alert", return_value=True):
                    from app import _handle_take_profit_hit
                    _handle_take_profit_hit(pos, current_price, auto_exec=True)

        mock_sell.assert_called_once()
        closed_pos = next((p for p in get_all_positions() if p["id"] == pid), None)
        assert closed_pos is not None
        assert closed_pos["status"] == "CLOSED"
        assert closed_pos["exit_reason"] == "take_profit"


class TestTrailingStopClosesPosition:
    """Trailing stop hit closes position (mocked execution)."""

    def test_trailing_stop_closes_position(self, temp_positions):
        """When trailing stop hit (after 7% profit, 5% trail), position is closed."""
        from trade_journal import open_position, update_position, get_open_positions, get_all_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 10, "ai_stop_pct": 2.5,
        })
        pid = pos["id"]
        # Phase 1 (trail 13% / activate 8%): peak ran to 130 (+30%), high water = 130
        update_position(pid, {"high_water_mark": 130})
        pos = next((p for p in get_open_positions() if p["id"] == pid), pos)
        pos["high_water_mark"] = 130  # Ensure dict has it
        # Trailing stop = 130 * 0.87 = 113.1; price drops to 108 (profit 8% >= 8% activation, below trail line)
        current_price = 108.0
        entry_price = 100.0

        with patch("app.execute_market_sell") as mock_sell:
            mock_sell.return_value = {"success": True, "fill_price": current_price}
            with patch("app.alerts"):
                with patch("app._should_alert", return_value=True):
                    from app import _check_trailing_stop
                    _check_trailing_stop(pos, current_price, entry_price, auto_exec=True)

        mock_sell.assert_called_once()
        closed_pos = next((p for p in get_all_positions() if p["id"] == pid), None)
        assert closed_pos is not None
        assert closed_pos["status"] == "CLOSED"
        assert closed_pos["exit_reason"] == "stop_loss"  # trailing uses _handle_stop_loss_hit
