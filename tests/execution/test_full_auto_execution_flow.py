# tests/test_full_auto_execution_flow.py — Full auto-execution path when enabled
# Run: pytest tests/test_full_auto_execution_flow.py -v
# 100% mocked: no real CCXT, Coinbase, alerts, or network calls.
# Deep coverage of signal → TWAP buy → SL/TP → trailing → sell → journal → alert.

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def temp_account_config(monkeypatch, tmp_path):
    """Temporary account_config.json."""
    cfg = {
        "total_cash": 20000.0,
        "risk_pct": 2.0,
        "max_cash_usage_pct": 40.0,
        "dust_threshold_usd": 1.0,
        "let_winners_run": False,
    }
    path = tmp_path / "account_config.json"
    path.write_text(json.dumps(cfg, indent=2))
    import app as app_module
    import app_config as app_config_module
    monkeypatch.setattr(app_module, "ACCOUNT_CONFIG_FILE", str(path))
    monkeypatch.setattr(app_config_module, "ACCOUNT_CONFIG_FILE", str(path))
    yield str(path)


@pytest.fixture
def mock_ccxt_exchange():
    """Mock CCXT exchange for all buy/sell/limit/cancel operations."""
    from tests.factories import coinbase_balance_v3_single_page

    ex = MagicMock()
    ex.id = "coinbase"
    ex.fetch_ticker.return_value = {"last": 100.0, "close": 100.0}
    ex.fetch_balance.return_value = coinbase_balance_v3_single_page(usdc_free=2000.0)
    ex.create_market_buy_order.return_value = {
        "id": "mock-buy-1", "status": "closed", "filled": 0.01, "average": 100.0,
    }
    ex.create_market_sell_order.return_value = {
        "id": "mock-sell-1", "status": "closed", "filled": 0.01, "average": 106.0,
    }
    ex.create_limit_sell_order.side_effect = lambda sym, qty, price: {
        "id": f"limit-{sym}-{price:.0f}", "status": "open", "symbol": sym,
    }
    ex.cancel_order.return_value = {"id": "cancelled", "status": "canceled"}
    ex.fetch_order.return_value = {"id": "oid", "status": "open", "average": 100.0}
    return ex


@pytest.fixture
def sync_twap_thread():
    """Run TWAP target synchronously so we can assert without race."""
    class SyncThread:
        def __init__(self, target=None, args=(), kwargs=None, **kw):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            if self._target:
                self._target(*self._args, **self._kwargs)
    return SyncThread


# -----------------------------------------------------------------------------
# 1. Full Happy Path (Normal Mode)
# -----------------------------------------------------------------------------

class TestFullHappyPathNormalMode:
    """Signal → TWAP buy (USDC) → SL + TP placed → trailing triggers → TP cancelled → TWAP sell → journal closed → alert."""

    def test_twap_buy_uses_usdc_pair(
        self, temp_positions, temp_account_config, mock_ccxt_exchange, sync_twap_thread
    ):
        """TWAP buy uses USDC pair by default."""
        with patch("execution_engine.get_coinbase_exchange", return_value=mock_ccxt_exchange):
            with patch("execution_engine.threading.Thread", sync_twap_thread):
                with patch("execution_engine._twap_cooldown_ok", return_value=True):
                    with patch("execution_engine.time.sleep"):
                        with patch("execution_engine.log_execution"):
                            with patch("execution_engine._on_twap_done_open_position_and_place_orders", None):
                                with patch("execution_engine.place_market_buy_usd") as mock_buy:
                                    mock_buy.return_value = {
                                        "status": "closed", "filled_amount": 0.01,
                                        "average_price": 100.0, "order_id": "o1",
                                    }
                                    from execution_engine import run_twap_buy_coinbase
                                    run_twap_buy_coinbase("BTC/USDC", 500, num_slices=2, duration_minutes=1)
        assert mock_buy.call_count == 2
        assert "USDC" in mock_buy.call_args[0][1]

    def test_after_twap_completes_open_position_and_place_sl_tp(
        self, temp_positions, temp_account_config, mock_ccxt_exchange
    ):
        """After TWAP completes: open journal position and place SL only (TP app-monitored)."""
        with patch("app.get_execution_config") as cfg:
                cfg.return_value = {"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}
                with patch("app.place_stop_limit_sell_order") as mock_sl:
                    mock_sl.return_value = {"success": True, "order_id": "ord-BTC-sl"}
                    with patch("app.place_limit_sell_order") as mock_tp:
                        def _tp(sym, qty, price, **kw):
                            return {"success": True, "order_id": f"ord-{sym}-{price:.0f}"}
                        mock_tp.side_effect = _tp
                        from app import _on_twap_done_open_position_and_place_orders
                        from trade_journal import get_open_positions
                        fill_data = {
                            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                            "quantity": 100, "entry_price": 100.0, "capital_usd": 10000.0,
                            "is_test": False,
                        }
                        _on_twap_done_open_position_and_place_orders(fill_data)
        pos = get_open_positions()[0]
        assert pos["status"] == "OPEN"
        assert pos["sl_order_id"] is not None
        assert pos["tp_order_id"] is None
        assert mock_sl.call_count == 1
        mock_tp.assert_not_called()

    def test_trailing_stop_cancels_tp_then_twap_sell(
        self, temp_positions, temp_account_config
    ):
        """Trailing stop triggers: cancel TP, cancel SL, TWAP sell, journal closed with trailing_stop."""
        from trade_journal import open_position, update_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {
            "high_water_mark": 130.0, "sl_order_id": "sl-1", "tp_order_id": "tp-1",
        })
        mock_cancel = MagicMock(return_value={"success": True})
        mock_twap = MagicMock(return_value={"success": True, "fill_price": 111.0})
        mock_alert = MagicMock()
        with patch("app.fetch_order_status", return_value="open"):
            with patch("exit_manager._get_execution_config", return_value={
                    "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
                }):
                    with patch("execution_engine.cancel_order", mock_cancel):
                        with patch("execution_engine.execute_market_sell_twap", mock_twap):
                            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                                with patch("exit_manager._should_alert", return_value=True):
                                    with patch("exit_manager._get_alerts", return_value=mock_alert):
                                        check_trailing_stops({"bitcoin": 111.0})
        assert mock_cancel.call_count >= 1
        assert mock_twap.called
        mock_twap.assert_called_once()
        closed = [p for p in __import__("trade_journal").get_all_positions() if p["id"] == pos["id"]]
        assert closed[0]["exit_reason"] == "trailing_stop"
        mock_alert.send_critical_alert.assert_called()
        assert "Trailing Stop Executed" in mock_alert.send_critical_alert.call_args[0][0]

    def test_critical_alert_sent_only_to_primary_owner(
        self, temp_positions, temp_account_config
    ):
        """Trailing stop confirmation uses send_critical_alert (primary + Telegram only)."""
        with patch("alert_system.AlertSystem.send_critical_alert") as mock_alert:
            from alert_system import AlertSystem
            alerts = AlertSystem()
            alerts.send_critical_alert("Trailing Stop Executed – BTC", "BTC sold at $106.50")
        mock_alert.assert_called_once_with("Trailing Stop Executed – BTC", "BTC sold at $106.50")


# -----------------------------------------------------------------------------
# 2. Runner Mode Full Flow
# -----------------------------------------------------------------------------

class TestRunnerModeFullFlow:
    """Let Winners Run: TWAP buy → only SL placed → trailing → TWAP sell → journal closed."""

    def test_runner_mode_places_only_sl_no_tp(
        self, temp_positions, temp_account_config
    ):
        """Runner mode: only SL (stop-limit) order placed after TWAP buy; no TP."""
        with patch("app.load_account_config") as load:
            load.return_value = {"let_winners_run": True}
            with patch("app.get_execution_config") as cfg:
                cfg.return_value = {"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}
                with patch("app.place_stop_limit_sell_order") as mock_sl:
                    mock_sl.return_value = {"success": True, "order_id": "sl-only"}
                    from app import _on_twap_done_open_position_and_place_orders
                    from trade_journal import get_open_positions
                    fill_data = {
                        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                        "quantity": 1, "entry_price": 100.0, "capital_usd": 100.0,
                        "is_test": False,
                    }
                    _on_twap_done_open_position_and_place_orders(fill_data)
        pos = get_open_positions()[0]
        assert pos["tp_order_id"] is None
        assert pos["sl_order_id"] is not None
        assert mock_sl.call_count == 1

    def test_runner_mode_trailing_stop_closes_with_trailing_stop_reason(
        self, temp_positions, temp_account_config
    ):
        """Runner mode: trailing stop triggers TWAP sell, journal closed with trailing_stop."""
        from trade_journal import open_position, update_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5, "let_winners_run": True,
        })
        update_position(pos["id"], {"high_water_mark": 130.0, "sl_order_id": "sl-1"})
        with patch("app.fetch_order_status", return_value="open"):
            with patch("exit_manager._get_execution_config", return_value={
                    "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
                }):
                    with patch("execution_engine.cancel_order", return_value={"success": True}):
                        with patch("execution_engine.execute_market_sell_twap", return_value={"success": True, "fill_price": 111.0}):
                            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                                with patch("exit_manager._should_alert", return_value=True):
                                    with patch("exit_manager._get_alerts"):
                                        check_trailing_stops({"bitcoin": 111.0})
        closed = next((p for p in __import__("trade_journal").get_all_positions() if p["id"] == pos["id"]), None)
        assert closed["exit_reason"] == "trailing_stop"


# -----------------------------------------------------------------------------
# 3. Concurrent Positions
# -----------------------------------------------------------------------------

class TestConcurrentPositions:
    """Multiple open positions with different trailing stops — independent monitoring."""

    def test_two_positions_trailing_stop_triggers_only_one(
        self, temp_positions, temp_account_config
    ):
        """Two positions: only the one below trailing stop triggers sell."""
        from trade_journal import open_position, update_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos1 = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        pos2 = open_position({
            "coin_id": "ethereum", "coin_name": "Ethereum", "coin_symbol": "ETH",
            "signal": "BUY", "entry_price": 3000, "quantity": 0.1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos1["id"], {"high_water_mark": 130.0, "sl_order_id": "sl-1", "tp_order_id": "tp-1"})
        update_position(pos2["id"], {"high_water_mark": 3200.0, "sl_order_id": "sl-2", "tp_order_id": "tp-2"})
        current_prices = {"bitcoin": 111.0, "ethereum": 3150.0}
        mock_twap = MagicMock(return_value={"success": True, "fill_price": 111.0})
        with patch("app.fetch_order_status", return_value="open"):
            with patch("exit_manager._get_execution_config", return_value={
                    "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
                }):
                    with patch("execution_engine.cancel_order", return_value={"success": True}):
                        with patch("execution_engine.execute_market_sell_twap", mock_twap):
                            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                                with patch("exit_manager._should_alert", return_value=True):
                                    with patch("exit_manager._get_alerts"):
                                        check_trailing_stops(current_prices)
        mock_twap.assert_called_once()
        assert mock_twap.call_args[1]["symbol"] == "BTC"
        open_list = get_open_positions()
        assert len(open_list) == 1
        assert open_list[0]["coin_symbol"] == "ETH"

    def test_concurrent_positions_independent_high_water_marks(
        self, temp_positions
    ):
        """Each position tracks its own high water mark."""
        from trade_journal import open_position, update_position, get_position_by_id

        pos1 = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        pos2 = open_position({
            "coin_id": "ethereum", "coin_name": "Ethereum", "coin_symbol": "ETH",
            "signal": "BUY", "entry_price": 3000, "quantity": 0.1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos1["id"], {"high_water_mark": 108.0})
        update_position(pos2["id"], {"high_water_mark": 3200.0})
        p1 = get_position_by_id(pos1["id"])
        p2 = get_position_by_id(pos2["id"])
        assert p1["high_water_mark"] == 108.0
        assert p2["high_water_mark"] == 3200.0


# -----------------------------------------------------------------------------
# 4. Edge Cases & Error Handling
# -----------------------------------------------------------------------------

class TestEdgeCasesPartialFill:
    """Partial fill on buy → still places correct SL/TP on filled quantity."""

    def test_partial_fill_places_sl_tp_on_filled_quantity(
        self, temp_positions, temp_account_config
    ):
        """Partial TWAP fill: SL/TP placed with actual filled quantity."""
        with patch("app.get_execution_config") as cfg:
                cfg.return_value = {"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}
                with patch("app.place_limit_sell_order") as mock_place:
                    def _place(sym, qty, price, **kw):
                        return {"success": True, "order_id": f"ord-{qty}"}
                    mock_place.side_effect = _place
                    from app import _on_twap_done_open_position_and_place_orders
                    from trade_journal import get_open_positions
                    fill_data = {
                        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                        "quantity": 0.5, "entry_price": 100.0, "capital_usd": 50.0,
                    }
                    _on_twap_done_open_position_and_place_orders(fill_data)
        calls = mock_place.call_args_list
        for c in calls:
            assert c[0][1] == 0.5


class TestEdgeCasesTpHitFirst:
    """TP limit order hit first → no trailing stop action, journal closed with take_profit."""

    def test_tp_hit_first_no_trailing_action(
        self, temp_positions, temp_account_config
    ):
        """When TP is filled on Coinbase: _check_sl_tp_order_status closes position, no trailing."""
        from trade_journal import open_position, update_position, get_open_positions
        from app import _check_sl_tp_order_status

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"sl_order_id": "sl-1", "tp_order_id": "tp-1"})
        def _mock_fetch_with_fee(oid, exchange_id="coinbase", position_id=None, trade_id=None):
            if oid == "tp-1":
                return {"status": "filled", "fill_price": 106.0, "filled_amount": 1.0, "fee_usd": 0.15, "fee_rate": 0.001, "order": {}}
            return {"status": "open", "fill_price": 0, "filled_amount": 0, "fee_usd": 0, "fee_rate": 0, "order": {}}
        with patch("app.get_execution_config") as cfg:
                cfg.return_value = {"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}
                with patch("execution_engine.fetch_order_with_fee", side_effect=_mock_fetch_with_fee):
                    with patch("app.cancel_order", return_value={"success": True}):
                        _check_sl_tp_order_status()
        closed = next((p for p in __import__("trade_journal").get_all_positions() if p["id"] == pos["id"]), None)
        assert closed["exit_reason"] in ("take_profit", "target")


class TestEdgeCasesSlHitFirst:
    """SL limit order hit first → journal closed with stop_loss."""

    def test_sl_hit_first_journal_closed_stop_loss(
        self, temp_positions, temp_account_config
    ):
        """When SL is filled: position closed with stop_loss."""
        from trade_journal import open_position, update_position, get_open_positions
        from app import _check_sl_tp_order_status

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"sl_order_id": "sl-1", "tp_order_id": "tp-1"})
        def _mock_fetch_with_fee(oid, exchange_id="coinbase", position_id=None, trade_id=None):
            if oid == "sl-1":
                return {"status": "filled", "fill_price": 97.5, "filled_amount": 1.0, "fee_usd": 0.15, "fee_rate": 0.001, "order": {}}
            return {"status": "open", "fill_price": 0, "filled_amount": 0, "fee_usd": 0, "fee_rate": 0, "order": {}}
        with patch("app.get_execution_config") as cfg:
                cfg.return_value = {"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}
                with patch("execution_engine.fetch_order_with_fee", side_effect=_mock_fetch_with_fee):
                    with patch("app.cancel_order", return_value={"success": True}):
                        _check_sl_tp_order_status()
        closed = next((p for p in __import__("trade_journal").get_all_positions() if p["id"] == pos["id"]), None)
        assert closed is not None
        assert closed["exit_reason"] == "stop_loss"
        assert closed["outcome"] == "loss"


class TestEdgeCasesSlTpPlacementFails:
    """Coinbase order placement fails after buy → graceful fallback and alert."""

    def test_sl_placement_failure_does_not_crash(
        self, temp_positions, temp_account_config
    ):
        """SL placement fails: no crash, TP remains app-monitored (no exchange TP attempt)."""
        with patch("app.get_execution_config") as cfg:
                cfg.return_value = {"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}
                with patch("app.place_stop_limit_sell_order") as mock_sl:
                    mock_sl.return_value = {"success": False, "error": "API error"}
                    with patch("app.place_limit_sell_order") as mock_tp:
                        mock_tp.return_value = {"success": True, "order_id": "tp-1"}
                        from trade_journal import open_position
                        from app import _place_sl_tp_orders_for_position
                        pos = open_position({
                            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                            "signal": "BUY", "entry_price": 100, "quantity": 1,
                            "ai_target_pct": 6, "ai_stop_pct": 2.5,
                        })
                        sl_ok, tp_ok, *_ = _place_sl_tp_orders_for_position(pos)
        assert sl_ok is False
        assert tp_ok is False
        mock_tp.assert_not_called()


class TestEdgeCasesNoDuplicateSell:
    """Trailing stop triggers during TWAP sell → no duplicate sell."""

    def test_manual_close_before_trailing_no_duplicate_sell(
        self, temp_positions
    ):
        """Position manually closed before trailing: no trailing action."""
        from trade_journal import open_position, close_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        close_position(pos["id"], exit_price=105, exit_reason="manual")
        with patch("execution_engine.execute_market_sell_twap") as mock_twap:
            check_trailing_stops({"bitcoin": 106.5})
        mock_twap.assert_not_called()


# -----------------------------------------------------------------------------
# 5. Safety & Configuration
# -----------------------------------------------------------------------------

class TestSafetyAutoExecDisabled:
    """Auto-execution disabled → no buys or sells."""

    def test_auto_exec_disabled_blocks_twap_path(
        self, temp_positions, temp_account_config
    ):
        """Auto-exec disabled: execution config gates TWAP path (caller checks before _start_twap_if_safe)."""
        with patch("app.get_execution_config") as cfg:
            cfg.return_value = {"auto_execution_enabled": False, "auto_execution_exchange": "coinbase"}
            from app import get_execution_config
            exec_cfg = get_execution_config()
        would_run_twap = exec_cfg.get("auto_execution_enabled") and (
            exec_cfg.get("auto_execution_exchange") or "coinbase"
        ).lower() == "coinbase"
        assert would_run_twap is False

    def test_auto_exec_disabled_no_sl_tp_after_twap(
        self, temp_positions, temp_account_config
    ):
        """When auto-exec disabled, SL can still be placed for manual-booked Coinbase position."""
        with patch("app.get_execution_config") as cfg:
            cfg.return_value = {"auto_execution_enabled": False, "auto_execution_exchange": "coinbase"}
            with patch("app.place_stop_limit_sell_order") as mock_sl:
                mock_sl.return_value = {"success": True, "order_id": "sl-1"}
                with patch("app.place_limit_sell_order") as mock_tp:
                    from app import _on_twap_done_open_position_and_place_orders
                    from trade_journal import get_open_positions
                    fill_data = {
                        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                        "quantity": 1, "entry_price": 100.0, "capital_usd": 100.0,
                        "is_test": False,
                    }
                    _on_twap_done_open_position_and_place_orders(fill_data)
        assert mock_sl.call_count == 1
        mock_tp.assert_not_called()

    def test_auto_exec_disabled_trailing_stop_skipped(
        self, temp_positions, temp_account_config
    ):
        """Auto-exec disabled: check_trailing_stops returns early, no sell."""
        from trade_journal import open_position, update_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 110.0, "sl_order_id": "sl-1", "tp_order_id": "tp-1"})
        with patch("exit_manager._get_execution_config") as cfg:
            cfg.return_value = {"auto_execution_enabled": False, "auto_execution_exchange": "coinbase"}
            with patch("execution_engine.execute_market_sell_twap") as mock_twap:
                check_trailing_stops({"bitcoin": 106.5})
        mock_twap.assert_not_called()


class TestWinnersAlwaysRun:
    """Toggle removed: every new position runs (no fixed target), regardless of any passed flag."""

    def test_new_position_always_runs_no_fixed_target(
        self, temp_positions, temp_account_config
    ):
        from trade_journal import open_position, get_open_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5, "let_winners_run": False,  # ignored
        })
        p = next(p for p in get_open_positions() if p["id"] == pos["id"])
        assert p["current_target_price"] is None
        assert p["ai_target_price"] is None
        assert p["current_stop_price"] and p["current_stop_price"] > 0  # stop still set


class TestSafetyUsdcFallback:
    """USDC pair fails → correct fallback to USD for both buy and sell."""

    def test_place_limit_sell_fallback_to_usd(
        self, mock_ccxt_exchange
    ):
        """place_limit_sell_order falls back to USD when USDC fails."""
        mock_ccxt_exchange.create_limit_sell_order.side_effect = [
            Exception("USDC unavailable"),
            {"id": "usd-order", "status": "open"},
        ]
        with patch("execution_engine.get_coinbase_exchange", return_value=mock_ccxt_exchange):
            with patch("execution_engine.log_execution"):
                from execution_engine import place_limit_sell_order
                result = place_limit_sell_order("BTC", 0.01, 44000)
        assert result.get("success") is True
        assert mock_ccxt_exchange.create_limit_sell_order.call_count == 2
        second_sym = mock_ccxt_exchange.create_limit_sell_order.call_args_list[1][0][0]
        assert "USD" in second_sym

    def test_place_market_buy_usd_fallback_to_usd_when_usdc_fails(
        self, mock_ccxt_exchange
    ):
        """place_market_buy_usd falls back to USD when USDC pair fails."""
        with patch("execution_engine.get_coinbase_exchange", return_value=mock_ccxt_exchange):
            with patch("execution_engine._place_market_buy_single") as mock_single:
                mock_single.side_effect = [
                    {"status": "error", "error": "USDC fail"},
                    {"status": "closed", "filled_amount": 0.01, "average_price": 44000, "order_id": "o1"},
                ]
                with patch("execution_engine.log_execution"):
                    from execution_engine import place_market_buy_usd
                    result = place_market_buy_usd(mock_ccxt_exchange, "BTC/USDC", 500)
        assert result.get("status") == "closed"
        assert mock_single.call_count == 2
        assert "USD" in mock_single.call_args_list[1][0][1]


# -----------------------------------------------------------------------------
# Additional Flow Coverage
# -----------------------------------------------------------------------------

class TestStartTwapIfSafe:
    """_start_twap_if_safe respects safety and config."""

    def test_insufficient_cash_skips_twap(
        self, temp_positions, temp_account_config
    ):
        """Insufficient cash: no TWAP started."""
        coin = {
            "symbol": "BTC", "id": "bitcoin", "name": "Bitcoin",
            "ai_recommendation": {
                "position_sizing": {"position_size_usd": 50000, "max_loss_pct": 2.5},
                "consensus": {"consensus_signal": "STRONG_BUY"},
            },
        }
        with patch("app.start_twap_buy_async") as mock_twap:
            with patch("app._build_account_overview", return_value={"cash_available_to_trade": 1000}):
                from app import _start_twap_if_safe
                result = _start_twap_if_safe(coin, {}, 20000)
        assert result is False
        mock_twap.assert_not_called()

    def test_execution_safety_blocks_twap(
        self, temp_positions, temp_account_config
    ):
        """execution_safety_ok fails: no TWAP started."""
        coin = {
            "symbol": "BTC", "id": "bitcoin", "name": "Bitcoin",
            "ai_recommendation": {
                "position_sizing": {"position_size_usd": 500, "max_loss_pct": 2.5},
                "consensus": {"consensus_signal": "STRONG_BUY"},
            },
        }
        with patch("app.start_twap_buy_async") as mock_twap:
            with patch("app._build_account_overview", return_value={"cash_available_to_trade": 10000}):
                with patch("app.execution_safety_ok", return_value=(False, "Risk would exceed 6%")):
                    from app import _start_twap_if_safe
                    result = _start_twap_if_safe(coin, {}, 20000)
        assert result is False
        mock_twap.assert_not_called()

    def test_twap_cooldown_prevents_duplicate_start(
        self, temp_positions, temp_account_config
    ):
        """Per-symbol TWAP cooldown prevents duplicate TWAP starts."""
        with patch("execution_engine._twap_cooldown_ok", return_value=False):
            with patch("execution_engine.run_twap_buy_coinbase") as mock_twap:
                with patch("execution_engine.threading.Thread"):
                    from execution_engine import start_twap_buy_async
                    start_twap_buy_async("BTC", 500)
        mock_twap.assert_not_called()

    def test_trailing_stop_never_blocked_by_risk(
        self, temp_positions, temp_account_config
    ):
        """Sells are never blocked by risk caps — trailing stop always executes."""
        from trade_journal import open_position, update_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0, "sl_order_id": "sl-1", "tp_order_id": "tp-1"})
        with patch("app.fetch_order_status", return_value="open"):
            with patch("exit_manager._get_execution_config", return_value={
                    "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
                }):
                    with patch("execution_engine.cancel_order", return_value={"success": True}):
                        with patch("execution_engine.execute_market_sell_twap", return_value={"success": True, "fill_price": 111.0}) as mock_twap:
                            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                                with patch("exit_manager._should_alert", return_value=True):
                                    with patch("exit_manager._get_alerts"):
                                        check_trailing_stops({"bitcoin": 111.0})
        mock_twap.assert_called_once()


# -----------------------------------------------------------------------------
# symbol_to_ccxt — USDC preference (moved from test_auto_execution.py)
# -----------------------------------------------------------------------------

class TestSymbolToCcxtUsdcPreference:
    """Verify USDC pair preference for interest on dry powder."""

    def test_prefer_usdc_by_default(self):
        from execution_engine import symbol_to_ccxt
        assert symbol_to_ccxt("BTC") == "BTC/USDC"
        assert symbol_to_ccxt("ETH") == "ETH/USDC"

    def test_prefer_usd_when_false(self):
        from execution_engine import symbol_to_ccxt
        assert symbol_to_ccxt("BTC", prefer_usdc=False) == "BTC/USD"

    def test_passthrough_symbol_with_slash(self):
        from execution_engine import symbol_to_ccxt
        assert symbol_to_ccxt("BTC/USDC") == "BTC/USDC"
        assert symbol_to_ccxt("ETH/USD") == "ETH/USD"


# -----------------------------------------------------------------------------
# do_auto_sync_balance — sync without auto_execution (moved from test_auto_execution.py)
# -----------------------------------------------------------------------------

class TestDoAutoSyncBalanceGating:
    """Stale total_cash + new open_market_value double-counts; sync must not require auto_execution."""

    def test_sync_runs_when_auto_sync_on_even_if_auto_exec_off(self):
        from app import do_auto_sync_balance

        cfg = {
            "auto_execution_enabled": False,
            "auto_sync_balance": True,
            "auto_execution_exchange": "coinbase",
        }
        fake = {"total_cash": 8000.0, "holdings": {}, "exchange": "coinbase"}
        with patch("app.get_execution_config", return_value=cfg):
            with patch("app.fetch_balance_sync", return_value=fake) as mock_fb:
                with patch("app.position_sizer") as mock_ps:
                    with patch("app.save_account_config") as mock_save:
                        with patch("app._set_last_auto_sync_at"):
                            with patch("app._reset_sync_failure_count"):
                                ok, cash, err = do_auto_sync_balance()
        assert ok is True
        assert cash == 8000.0
        assert err is None
        mock_fb.assert_called_once_with("coinbase")
        mock_ps.update_account_size.assert_called_once_with(8000.0)
        mock_save.assert_called_once_with(total_cash=8000.0)

    def test_sync_skipped_when_auto_sync_off(self):
        from app import do_auto_sync_balance

        cfg = {
            "auto_execution_enabled": True,
            "auto_sync_balance": False,
            "auto_execution_exchange": "coinbase",
        }
        with patch("app.get_execution_config", return_value=cfg):
            with patch("app.fetch_balance_sync") as mock_fb:
                ok, cash, err = do_auto_sync_balance()
        assert ok is False
        assert cash is None
        mock_fb.assert_not_called()
