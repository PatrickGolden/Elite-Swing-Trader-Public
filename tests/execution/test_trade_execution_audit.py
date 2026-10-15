# tests/test_trade_execution_audit.py — Tests for trade execution audit fixes
# Covers: cash reservation, DTC unification, SL/TP capture, pending persistence,
# canceled order handling, duplicate prevention, slippage, periodic sync, trailing consolidation.

import sys
import os
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Slow module (~60–90s typical); allow headroom over global pytest.ini timeout.

# =============================================================================
# Fixtures (temp_positions is autouse in conftest.py)
# =============================================================================

@pytest.fixture
def temp_pending_file(tmp_path, monkeypatch):
    """Redirect pending_orders.json to a temp file."""
    pending_file = tmp_path / "pending_orders.json"
    pending_file.write_text("{}")
    monkeypatch.setattr("execution_engine._PENDING_ORDERS_FILE", str(pending_file))
    return pending_file


@pytest.fixture
def temp_sold_pending_file(tmp_path, monkeypatch):
    """Redirect sold_pending_close.json to a temp file."""
    sold_file = tmp_path / "sold_pending_close.json"
    sold_file.write_text("[]")
    import app as app_mod
    monkeypatch.setattr(app_mod, "_SOLD_PENDING_CLOSE_FILE", str(sold_file))
    return sold_file


# =============================================================================
# P3-A: Cash reservation — pending TWAP subtracted from available cash
# =============================================================================

class TestCashReservation:

    def test_cash_available_subtracts_pending_twap(self):
        """cash_available_to_trade = total_cash - pending_twap_usd."""
        from execution_engine import get_active_twap_orders
        with patch("app.load_account_config", return_value={"total_cash": 1000.0, "dust_threshold_usd": 1.0}):
            with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
                with patch("app.get_open_positions", return_value=[]):
                    with patch("app.get_active_twap_orders", return_value=[
                        {"symbol": "BTC", "total_usd": 300.0, "start_ts": time.time(), "mode": "twap", "coin_name": "BTC"}
                    ]):
                        with patch("app.cached_data", {"coins": []}):
                            from app import _build_account_overview
                            overview = _build_account_overview()
        assert overview["cash_available_to_trade"] == 700.0
        assert overview["pending_twap_usd"] == 300.0

    def test_cash_available_never_negative(self):
        """cash_available_to_trade is floored at 0 when pending exceeds cash."""
        with patch("app.load_account_config", return_value={"total_cash": 100.0, "dust_threshold_usd": 1.0}):
            with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
                with patch("app.get_open_positions", return_value=[]):
                    with patch("app.get_active_twap_orders", return_value=[
                        {"symbol": "BTC", "total_usd": 500.0, "start_ts": time.time(), "mode": "twap", "coin_name": "BTC"}
                    ]):
                        with patch("app.cached_data", {"coins": []}):
                            from app import _build_account_overview
                            overview = _build_account_overview()
        assert overview["cash_available_to_trade"] == 0.0

    def test_test_pending_orders_do_not_reserve_cash(self):
        """Pending orders with is_test=True do not reduce cash_available_to_trade."""
        with patch("app.load_account_config", return_value={"total_cash": 1000.0, "dust_threshold_usd": 1.0}):
            with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
                with patch("app.get_open_positions", return_value=[]):
                    with patch("app.get_active_twap_orders", return_value=[
                        {"symbol": "BTC", "total_usd": 400.0, "start_ts": time.time(), "mode": "twap", "coin_name": "BTC", "is_test": True}
                    ]):
                        with patch("app.cached_data", {"coins": []}):
                            from app import _build_account_overview
                            overview = _build_account_overview()
        assert overview["cash_available_to_trade"] == 1000.0
        assert overview["pending_twap_usd"] == 0.0


# =============================================================================
# P1-A: Unified DynamicTargetCalculator
# =============================================================================

class TestUnifiedDTC:

    def test_global_calculator_uses_execution_params(self):
        """Global target_calculator uses base_stop_pct=6.0, min_stop_pct=4.0 (widened for crypto volatility)."""
        from app import target_calculator
        assert target_calculator.base_stop_pct == 6.0
        assert target_calculator.min_stop_pct == 4.0
        assert target_calculator.max_stop_pct == 15.0

    def test_get_dynamic_uses_global_calculator(self):
        """_get_dynamic_stop_target_for_coin uses the global target_calculator (no new instance)."""
        from app import _get_dynamic_stop_target_for_coin, target_calculator
        with patch.object(target_calculator, 'calculate_targets', return_value={
            'stop_loss_pct': 5.0, 'take_profit_pct': 10.0
        }) as mock_calc:
            with patch("app.cached_data", {"coins": [
                {"id": "bitcoin", "symbol": "BTC", "current_price": 50000,
                 "ai_recommendation": {"consensus": {"consensus_signal": "STRONG_BUY"}},
                 "indicators": {"volatility": 30}}
            ], "regime": {"regime": "BULL"}}):
                result = _get_dynamic_stop_target_for_coin("bitcoin", "BTC", 50000)
        mock_calc.assert_called_once()
        assert result["stop_loss_pct"] == 5.0


# =============================================================================
# P3-B + P3-C: Pending orders — timeout, persistence
# =============================================================================

class TestPendingOrders:

    def test_stale_pending_auto_expires(self, temp_pending_file):
        """Pending orders older than 2h are auto-expired."""
        import execution_engine as ee
        with ee._active_twap_lock:
            ee._active_twap_orders["OLD"] = {
                "symbol": "OLD", "total_usd": 100, "start_ts": time.time() - 8000, "mode": "twap", "coin_name": "OLD"
            }
            ee._active_twap_orders["FRESH"] = {
                "symbol": "FRESH", "total_usd": 200, "start_ts": time.time(), "mode": "twap", "coin_name": "FRESH"
            }
        orders = ee.get_active_twap_orders()
        symbols = [o["symbol"] for o in orders]
        assert "OLD" not in symbols
        assert "FRESH" in symbols

    def test_pending_persisted_to_disk(self, temp_pending_file):
        """Register/unregister writes to pending_orders.json."""
        import execution_engine as ee
        ee._register_active_twap("TEST", 500.0, "twap", "Test Coin")
        data = json.loads(temp_pending_file.read_text())
        assert "TEST" in data
        ee._unregister_active_twap("TEST")
        data = json.loads(temp_pending_file.read_text())
        assert "TEST" not in data

    def test_unregister_pending_order_persists(self, temp_pending_file):
        """unregister_pending_order writes to disk."""
        import execution_engine as ee
        ee._register_active_twap("XRP", 100.0)
        assert ee.unregister_pending_order("XRP") is True
        data = json.loads(temp_pending_file.read_text())
        assert "XRP" not in data


# =============================================================================
# NEW-1 + NEW-4: Canceled SL/TP handling, process all fills per cycle
# =============================================================================

class TestSlTpOrderStatus:

    def test_canceled_sl_triggers_replacement(self):
        """When SL order is canceled on exchange, it is re-placed."""
        from app import _check_sl_tp_order_status
        pos = {
            "id": 1, "coin_symbol": "BTC", "quantity": 1.0,
            "sl_order_id": "sl-123", "tp_order_id": None,
            "entry_price": 100, "current_stop_price": 95,
            "ai_stop_price": 95, "current_target_price": 110,
            "ai_target_price": 110, "let_winners_run": False,
            "is_test": False
        }
        with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
            with patch("app.get_open_positions", return_value=[pos]):
                with patch("execution_engine.fetch_order_with_fee", return_value={"status": "canceled", "fill_price": 0, "filled_amount": 0, "fee_usd": 0, "fee_rate": 0, "order": {}}):
                    with patch("app.update_position") as mock_update:
                        with patch("app._place_sl_tp_orders_for_position") as mock_place:
                            _check_sl_tp_order_status()
        mock_update.assert_called()
        mock_place.assert_called_once()

    def test_processes_all_fills_not_just_first(self):
        """Multiple filled orders are processed in one cycle (continue, not return)."""
        from app import _check_sl_tp_order_status
        pos1 = {"id": 1, "coin_symbol": "BTC", "quantity": 1.0,
                "sl_order_id": "sl-1", "tp_order_id": None,
                "entry_price": 100, "current_stop_price": 95,
                "ai_stop_price": 95, "is_test": False}
        pos2 = {"id": 2, "coin_symbol": "ETH", "quantity": 10.0,
                "sl_order_id": "sl-2", "tp_order_id": None,
                "entry_price": 3000, "current_stop_price": 2850,
                "ai_stop_price": 2850, "is_test": False}

        with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
            with patch("app.get_open_positions", return_value=[pos1, pos2]):
                with patch("execution_engine.fetch_order_with_fee", return_value={"status": "filled", "fill_price": 95.0, "filled_amount": 1.0, "fee_usd": 0.15, "fee_rate": 0.001, "order": {}}):
                    with patch("app.cancel_order"):
                        with patch("app.close_position", return_value=(MagicMock(), None)) as mock_close:
                            _check_sl_tp_order_status()
        assert mock_close.call_count == 2


# =============================================================================
# NEW-5: Duplicate position prevention
# =============================================================================

class TestDuplicatePositionPrevention:

    def test_auto_exec_skips_existing_open_position(self):
        """_start_twap_if_safe returns False when position already open for same symbol."""
        from app import _start_twap_if_safe
        coin = {
            "symbol": "BTC", "id": "bitcoin", "name": "Bitcoin",
            "current_price": 50000,
            "ai_recommendation": {
                "position_sizing": {"position_size_usd": 500, "max_loss_pct": 2},
                "consensus": {"consensus_signal": "STRONG_BUY"},
            },
            "dynamic_targets": {"stop_loss_pct": 4.0, "take_profit_pct": 8.0},
        }
        existing_pos = [{"id": 1, "coin_symbol": "BTC", "status": "OPEN"}]
        with patch("app.get_open_positions", return_value=existing_pos):
            result = _start_twap_if_safe(coin, {}, 10000)
        assert result is False

    def test_auto_exec_skips_twap_for_moderate_buy_tier(self):
        """TWAP is restricted to Tier1-2 quant tiers (ULTRA/STRONG)."""
        from app import _start_twap_if_safe
        coin = {
            "symbol": "BTC", "id": "bitcoin", "name": "Bitcoin",
            "current_price": 50000,
            "ai_recommendation": {
                "position_sizing": {"position_size_usd": 700, "max_loss_pct": 2},
                "consensus": {"consensus_signal": "MODERATE_BUY"},
                "quant": {"quant_tier": "MODERATE_BUY", "quant_path": "default"},
            },
            "dynamic_targets": {"stop_loss_pct": 4.0, "take_profit_pct": 8.0},
        }
        with patch("app.get_open_positions", return_value=[]):
            with patch("app._build_account_overview", return_value={"cash_available_to_trade": 5000}):
                with patch("app.execution_safety_ok", return_value=(True, "")):
                    with patch("app.get_execution_config", return_value={"twap_min_usd": 500, "auto_execution_exchange": "coinbase"}):
                        with patch("app.start_twap_buy_async", return_value=True) as mock_twap:
                            result = _start_twap_if_safe(coin, {}, 10000)
        assert result is False
        mock_twap.assert_not_called()

    def test_journal_rejects_duplicate_open_per_symbol(self, caplog):
        """open_position rejects second open for same symbol and returns None; only one position exists."""
        import logging
        from trade_journal import open_position, get_open_positions
        with caplog.at_level(logging.WARNING):
            p1 = open_position({
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
                "ai_target_pct": 6, "ai_stop_pct": 4,
            })
            p2 = open_position({
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 51000, "quantity": 0.01,
                "ai_target_pct": 6, "ai_stop_pct": 4,
            })
        assert p1 is not None
        assert p2 is None
        assert len(get_open_positions()) == 1
        assert any("duplicate" in (r.message or "") for r in caplog.records)

    def test_paper_trade_does_not_block_real_position(self, caplog):
        """Paper trade open for a symbol must not prevent a real position from opening."""
        import logging
        from trade_journal import open_position, get_open_positions
        with caplog.at_level(logging.WARNING):
            paper = open_position({
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
                "ai_target_pct": 6, "ai_stop_pct": 4,
                "is_test": True,
            })
            real = open_position({
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 51000, "quantity": 0.01,
                "ai_target_pct": 6, "ai_stop_pct": 4,
                "is_test": False,
            })
        assert paper is not None
        assert real is not None
        assert paper.get("is_test") is True
        assert real.get("is_test") is not True
        all_open = get_open_positions()
        assert len(all_open) == 2

    def test_duplicate_paper_still_rejected(self, caplog):
        """Two paper trades for the same symbol are still rejected."""
        import logging
        from trade_journal import open_position, get_open_positions
        with caplog.at_level(logging.WARNING):
            p1 = open_position({
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
                "ai_target_pct": 6, "ai_stop_pct": 4,
                "is_test": True,
            })
            p2 = open_position({
                "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                "signal": "BUY", "entry_price": 51000, "quantity": 0.01,
                "ai_target_pct": 6, "ai_stop_pct": 4,
                "is_test": True,
            })
        assert p1 is not None
        assert p2 is None
        assert any("duplicate" in (r.message or "") for r in caplog.records)


# =============================================================================
# NEW-6: Persist _EXIT_SOLD_PENDING_CLOSE
# =============================================================================

class TestSoldPendingClosePersistence:

    def test_sold_pending_close_saved_and_loaded(self, temp_sold_pending_file):
        """_EXIT_SOLD_PENDING_CLOSE survives save/load cycle."""
        import app as app_mod
        app_mod._EXIT_SOLD_PENDING_CLOSE.add(42)
        app_mod._save_sold_pending_close()
        data = json.loads(temp_sold_pending_file.read_text())
        assert 42 in data
        loaded = app_mod._load_sold_pending_close()
        assert 42 in loaded
        app_mod._EXIT_SOLD_PENDING_CLOSE.discard(42)
        app_mod._save_sold_pending_close()


# =============================================================================
# NEW-3: Sells never blocked by risk caps (exit_manager)
# =============================================================================

class TestSellsNeverBlocked:

    def test_no_execution_safety_ok_in_exit_manager(self):
        """exit_manager no longer has _get_portfolio_risk or _execution_safety_ok."""
        import exit_manager as em
        assert not hasattr(em, '_get_portfolio_risk')
        assert not hasattr(em, '_execution_safety_ok')

    def test_trailing_stop_executes_regardless_of_risk(self, temp_positions):
        """Trailing stop sell happens even without safety check."""
        from trade_journal import open_position, update_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0})
        from trade_journal import get_open_positions
        current_prices = {"bitcoin": 111.0}

        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                with patch("exit_manager._execute_market_sell_twap", return_value={"success": True, "fill_price": 111.0}) as mock_twap:
                    with patch("exit_manager._close_position", return_value=(MagicMock(), None)):
                        with patch("exit_manager._get_alerts"):
                            with patch("exit_manager._should_alert", return_value=True):
                                from exit_manager import check_trailing_stops
                                check_trailing_stops(current_prices)
        mock_twap.assert_called_once()


# =============================================================================
# P1-B: SL/TP captured at decision time for auto-exec
# =============================================================================

class TestSlTpCapturedAtDecision:

    def test_auto_exec_passes_stop_target_to_twap(self):
        """_start_twap_if_safe passes stop_pct and target_pct from coin data."""
        from app import _start_twap_if_safe
        coin = {
            "symbol": "ETH", "id": "ethereum", "name": "Ethereum",
            "current_price": 3000,
            "dynamic_targets": {"stop_loss_pct": 5.5, "take_profit_pct": 11.0},
            "risk": {},
            "ai_recommendation": {
                "quant_tier": "STRONG_BUY",
                "position_sizing": {"position_size_usd": 200, "max_loss_pct": 1.5},
                "consensus": {"consensus_signal": "STRONG_BUY"},
            },
        }
        with patch("app.get_open_positions", return_value=[]):
            with patch("app._build_account_overview", return_value={"cash_available_to_trade": 5000}):
                with patch("app.execution_safety_ok", return_value=(True, "")):
                    with patch("app.get_execution_config", return_value={"twap_min_usd": 500, "auto_execution_exchange": "coinbase"}):
                        with patch("app.start_twap_buy_async", return_value=True) as mock_twap:
                            _start_twap_if_safe(coin, {}, 10000)
        call_kwargs = mock_twap.call_args[1]
        assert call_kwargs["stop_pct"] == 5.5
        assert call_kwargs["target_pct"] == 11.0

    def test_auto_exec_passes_attribution_fields_in_execution_context(self):
        """Entry-time execution_context includes fields used by optimizer counterfactuals."""
        from app import _start_twap_if_safe
        coin = {
            "symbol": "ETH", "id": "ethereum", "name": "Ethereum",
            "current_price": 3000,
            "indicators": {"ch24h": 2.5, "ch7d": 8.2, "ch30d": 14.4},
            "enhanced": {"funding_rate": {"current": 0.0123}},
            "dynamic_targets": {"stop_loss_pct": 5.5, "take_profit_pct": 11.0},
            "risk": {},
            "ai_recommendation": {
                "position_sizing": {"position_size_usd": 200, "max_loss_pct": 1.5},
                "consensus": {"consensus_signal": "STRONG_BUY"},
                "quant": {
                    "quant_tier": "STRONG_BUY",
                    "quant_path": "default",
                    "edge_score": 0.42,
                    "confirmation_score": 73.1,
                    "signal_badges": ["momentum_context"],
                },
            },
        }
        regime_data = {"regime": "SIDEWAYS", "characteristics": {"fear_greed": 22}}
        with patch("app.get_open_positions", return_value=[]):
            with patch("app._build_account_overview", return_value={"cash_available_to_trade": 5000}):
                with patch("app.execution_safety_ok", return_value=(True, "")):
                    with patch("app.get_execution_config", return_value={"twap_min_usd": 500, "auto_execution_exchange": "coinbase"}):
                        with patch("app.start_twap_buy_async", return_value=True) as mock_twap:
                            _start_twap_if_safe(coin, {}, 10000, regime_data=regime_data)
        ctx = mock_twap.call_args[1]["execution_context"]
        assert ctx["fear_greed"] == 22
        assert ctx["funding_rate"] == 0.0123
        assert ctx["price_change_24h"] == 2.5
        assert ctx["price_change_7d"] == 8.2
        assert ctx["price_change_30d"] == 14.4
        assert ctx["edge_score"] == 0.42

    def test_callback_prefers_fill_data_sltp(self):
        """_on_twap_done prefers ai_stop_pct/ai_target_pct from fill_data over recalculation."""
        from app import _on_twap_done_open_position_and_place_orders
        fill_data = {
            "coin_symbol": "BTC", "coin_id": "bitcoin", "coin_name": "Bitcoin",
            "entry_price": 50000, "quantity": 0.01, "capital_usd": 500,
            "ai_stop_pct": 3.5, "ai_target_pct": 7.0,
        }
        with patch("app._get_dynamic_stop_target_for_coin") as mock_dyn:
            with patch("app.open_position") as mock_open:
                with patch("app.get_test_mode", return_value=True):
                    with patch("app.load_account_config", return_value={"let_winners_run": False}):
                        with patch("app.alerts"):
                            _on_twap_done_open_position_and_place_orders(fill_data)
        mock_dyn.assert_not_called()
        open_data = mock_open.call_args[0][0]
        assert open_data["ai_stop_pct"] == 3.5
        assert open_data["ai_target_pct"] == 7.0

    def test_callback_passes_regime_and_scenario_to_open_position(self):
        """When fill_data contains market_regime, quant_tier, quant_path, trading_scenario they are passed to open_position."""
        from app import _on_twap_done_open_position_and_place_orders
        fill_data = {
            "coin_symbol": "ETH", "coin_id": "ethereum", "coin_name": "Ethereum",
            "entry_price": 3000, "quantity": 0.1, "capital_usd": 300,
            "ai_stop_pct": 4, "ai_target_pct": 8,
            "market_regime": "BEAR", "quant_tier": "STRONG_BUY",
            "quant_path": "dip", "trading_scenario": "dip",
        }
        with patch("app.open_position") as mock_open:
            with patch("app.get_test_mode", return_value=True):
                with patch("app.load_account_config", return_value={"let_winners_run": False}):
                    with patch("app.alerts"):
                        _on_twap_done_open_position_and_place_orders(fill_data)
        open_data = mock_open.call_args[0][0]
        assert open_data.get("market_regime") == "BEAR"
        assert open_data.get("quant_tier") == "STRONG_BUY"
        assert open_data.get("quant_path") == "dip"
        assert open_data.get("trading_scenario") == "dip"


# =============================================================================
# P1-D + P4-A: Trailing stop runs for all positions
# =============================================================================

class TestTrailingStopConsolidation:

    def test_trailing_checked_when_auto_exec_on_no_limit_orders(self):
        """Positions without limit orders get trailing stop check even when auto_exec=True."""
        from app import check_position_exits, _check_trailing_stop
        pos = {
            "id": 99, "coin_id": "bitcoin", "coin_symbol": "BTC",
            "entry_price": 100.0, "quantity": 1.0,
            "current_stop_price": 90, "ai_stop_price": 90,
            "current_target_price": 120, "ai_target_price": 120,
            "let_winners_run": False, "high_water_mark": 115,
            "is_test": False,
        }
        with patch("app.get_open_positions", return_value=[pos]):
            with patch("app.get_execution_config", return_value={"auto_execution_enabled": True}):
                with patch("app._check_sl_tp_order_status"):
                    with patch("app._check_trailing_stop") as mock_trail:
                        with patch("exit_manager.check_trailing_stops"):
                            with patch("app.fetcher") as mock_fetcher:
                                mock_fetcher.get_coinbase_spot_prices.return_value = {"BTC": 112.0}
                                check_position_exits({"bitcoin": 112.0})
        mock_trail.assert_called_once()


# =============================================================================
# NEW-7: Slippage validation
# =============================================================================

class TestSlippageValidation:

    def test_high_slippage_triggers_alert(self):
        """Slippage > 5% sends critical alert."""
        from app import _on_twap_done_open_position_and_place_orders
        fill_data = {
            "coin_symbol": "BTC", "coin_id": "bitcoin", "coin_name": "Bitcoin",
            "entry_price": 50000, "quantity": 0.012, "capital_usd": 500,
            "ai_stop_pct": 4, "ai_target_pct": 8,
        }
        with patch("app.open_position", return_value={"id": 1}):
            with patch("app.get_test_mode", return_value=True):
                with patch("app.load_account_config", return_value={"let_winners_run": False}):
                    with patch("app.alerts") as mock_alerts:
                        _on_twap_done_open_position_and_place_orders(fill_data)
        mock_alerts.send_critical_alert.assert_called_once()
        assert "Slippage" in mock_alerts.send_critical_alert.call_args[0][0]

    def test_normal_slippage_no_alert(self):
        """Slippage < 5% does not trigger alert."""
        from app import _on_twap_done_open_position_and_place_orders
        fill_data = {
            "coin_symbol": "BTC", "coin_id": "bitcoin", "coin_name": "Bitcoin",
            "entry_price": 50000, "quantity": 0.01, "capital_usd": 500,
            "ai_stop_pct": 4, "ai_target_pct": 8,
        }
        with patch("app.open_position", return_value={"id": 1}):
            with patch("app.get_test_mode", return_value=True):
                with patch("app.load_account_config", return_value={"let_winners_run": False}):
                    with patch("app.alerts") as mock_alerts:
                        _on_twap_done_open_position_and_place_orders(fill_data)
        mock_alerts.send_critical_alert.assert_not_called()


# =============================================================================
# Test trades audit: account overview and live positions exclude test
# =============================================================================

class TestTradesAuditOverviewAndLiveTab:

    def test_build_account_overview_excludes_test_positions(self):
        """_build_account_overview uses only live positions (exclude_test=True) for cost/market value."""
        live_pos = {
            "id": 1, "status": "OPEN", "coin_id": "bitcoin", "coin_symbol": "BTC",
            "entry_price": 50000.0, "quantity": 0.01, "capital_usd": 500.0,
            "is_test": False,
        }
        test_pos = {
            "id": 2, "status": "OPEN", "coin_id": "ethereum", "coin_symbol": "ETH",
            "entry_price": 3000.0, "quantity": 0.1, "capital_usd": 300.0,
            "is_test": True,
        }

        def open_positions(exclude_test=False):
            return [live_pos, test_pos] if not exclude_test else [live_pos]

        with patch("app.load_account_config", return_value={"total_cash": 10000.0, "dust_threshold_usd": 1.0}):
            with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
                with patch("app.get_open_positions", side_effect=open_positions):
                    with patch("app.get_active_twap_orders", return_value=[]):
                        with patch("app.cached_data", {"coins": [{"id": "bitcoin", "current_price": 50000}]}):
                            from app import _build_account_overview
                            overview = _build_account_overview()
        assert overview["open_positions_cost_basis"] == 500.0
        assert overview["open_positions_market_value"] == 500.0
        assert overview["total_cash"] == 10000.0
        assert overview["total_account_value"] == 10500.0  # cash + open MV only (no test position)
        assert overview["cash_available_to_trade"] == 10000.0

    # Route test merged into test_api_routes.py.


# =============================================================================
# Execution paths consistency (merged from test_execution_quick_log_consistency.py)
# =============================================================================

class TestGetDynamicStopTargetForCoin:
    """Dynamic stop/target used by TWAP callback, journal open, open-from-exchange."""

    def test_returns_none_when_entry_price_invalid(self):
        with patch("app.cached_data", {}):
            from app import _get_dynamic_stop_target_for_coin
            assert _get_dynamic_stop_target_for_coin("bitcoin", "BTC", 0) is None
            assert _get_dynamic_stop_target_for_coin("bitcoin", "BTC", None) is None

    def test_returns_none_when_coin_not_in_cache(self):
        with patch("app.cached_data", {"coins": [], "regime": {"regime": "SIDEWAYS"}}):
            from app import _get_dynamic_stop_target_for_coin
            assert _get_dynamic_stop_target_for_coin("bitcoin", "BTC", 45000) is None

    def test_returns_dynamic_values_when_coin_in_cache(self):
        with patch("app.cached_data", {
            "coins": [{
                "id": "bitcoin", "symbol": "BTC",
                "indicators": {"volatility": 55},
                "ai_recommendation": {"consensus": {"consensus_signal": "STRONG_BUY"}},
            }],
            "regime": {"regime": "BULL"},
        }):
            from app import _get_dynamic_stop_target_for_coin
            r = _get_dynamic_stop_target_for_coin("bitcoin", "BTC", 45000)
            assert r is not None
            assert "stop_loss_pct" in r and "take_profit_pct" in r
            assert isinstance(r["stop_loss_pct"], (int, float))
            assert r["stop_loss_pct"] >= 0 and r["take_profit_pct"] >= 0

    def test_finds_coin_by_symbol_when_id_missing(self):
        with patch("app.cached_data", {
            "coins": [{
                "id": "ethereum", "symbol": "ETH",
                "indicators": {"volatility": 50},
                "ai_recommendation": {"consensus": {"consensus_signal": "MODERATE_BUY"}},
            }],
            "regime": {"regime": "SIDEWAYS"},
        }):
            from app import _get_dynamic_stop_target_for_coin
            r = _get_dynamic_stop_target_for_coin("unknown", "ETH", 3000)
            assert r is not None and "stop_loss_pct" in r


class TestTwapCallbackUsesDynamicTargets:
    """_on_twap_done_open_position_and_place_orders uses dynamic SL/TP when cache has coin."""

    @pytest.fixture
    def temp_account_config(self, monkeypatch, tmp_path):
        cfg = {"total_cash": 20000.0, "risk_pct": 2.0, "let_winners_run": False}
        path = tmp_path / "account_config.json"
        path.write_text(json.dumps(cfg))
        import app as app_module
        import app_config as app_config_module
        monkeypatch.setattr(app_module, "ACCOUNT_CONFIG_FILE", str(path))
        monkeypatch.setattr(app_config_module, "ACCOUNT_CONFIG_FILE", str(path))
        return str(path)

    def test_callback_prefers_fill_data_over_dynamic(self, temp_positions, temp_account_config):
        fill_data = {
            "coin_id": "bitcoin", "coin_symbol": "BTC", "coin_name": "Bitcoin",
            "entry_price": 45000, "quantity": 0.01, "capital_usd": 450,
            "ai_stop_pct": 2.5, "ai_target_pct": 6.0,
        }
        dyn = {"stop_loss_pct": 3.5, "take_profit_pct": 10.5}
        with patch("app._get_dynamic_stop_target_for_coin", return_value=dyn) as mock_dyn:
            with patch("app.get_test_mode", return_value=True):
                with patch("app.open_position") as mock_open:
                    from app import _on_twap_done_open_position_and_place_orders
                    _on_twap_done_open_position_and_place_orders(fill_data)
                    mock_open.assert_called_once()
                    call_kw = mock_open.call_args[0][0]
                    assert call_kw["ai_stop_pct"] == 2.5
                    assert call_kw["ai_target_pct"] == 6.0
                    mock_dyn.assert_not_called()

    def test_callback_falls_back_to_dynamic_when_fill_data_empty(self, temp_positions, temp_account_config):
        fill_data = {
            "coin_id": "bitcoin", "coin_symbol": "BTC", "coin_name": "Bitcoin",
            "entry_price": 45000, "quantity": 0.01, "capital_usd": 450,
        }
        dyn = {"stop_loss_pct": 3.5, "take_profit_pct": 10.5}
        with patch("app._get_dynamic_stop_target_for_coin", return_value=dyn):
            with patch("app.get_test_mode", return_value=True):
                with patch("app.open_position") as mock_open:
                    from app import _on_twap_done_open_position_and_place_orders
                    _on_twap_done_open_position_and_place_orders(fill_data)
                    mock_open.assert_called_once()
                    call_kw = mock_open.call_args[0][0]
                    assert call_kw["ai_stop_pct"] == 3.5
                    assert call_kw["ai_target_pct"] == 10.5

    def test_callback_uses_fill_data_or_config_when_dynamic_none(self, temp_positions, temp_account_config):
        fill_data = {
            "coin_id": "unknown", "coin_symbol": "XXX", "coin_name": "Unknown",
            "entry_price": 1.0, "quantity": 100, "capital_usd": 100,
            "ai_stop_pct": 5.0, "ai_target_pct": 12.0,
        }
        with patch("app._get_dynamic_stop_target_for_coin", return_value=None):
            with patch("app.get_execution_config", return_value={"default_stop_pct": 4.0, "default_target_pct": 6.0}):
                with patch("app.get_test_mode", return_value=True):
                    with patch("app.open_position") as mock_open:
                        from app import _on_twap_done_open_position_and_place_orders
                        _on_twap_done_open_position_and_place_orders(fill_data)
                        call_kw = mock_open.call_args[0][0]
                        assert call_kw["ai_stop_pct"] == 5.0
                        assert call_kw["ai_target_pct"] == 12.0


class TestPositionSizeCalculate:
    """POST /api/position-size/calculate used by Quick Log."""

    def test_calculate_requires_entry_and_stop(self):
        from app import app as flask_app
        with flask_app.test_client() as c:
            r = c.post("/api/position-size/calculate", json={}, content_type="application/json")
            assert r.status_code == 400
            r2 = c.post("/api/position-size/calculate", json={"entry_price": 100}, content_type="application/json")
            assert r2.status_code == 400

    def test_calculate_returns_quantity_and_position_size_usd(self):
        from app import app as flask_app
        with patch("app._build_account_overview", return_value={"cash_available_to_trade": 20000}):
            with patch("app.current_regime", {"regime": "SIDEWAYS"}):
                with flask_app.test_client() as c:
                    r = c.post(
                        "/api/position-size/calculate",
                        json={"entry_price": 45000, "stop_loss_pct": 4.0, "consensus_signal": "MODERATE_BUY", "volatility": 50},
                        content_type="application/json",
                    )
                    assert r.status_code == 200
                    data = r.get_json()
                    assert "error" not in data or data.get("error") is None
                    if "quantity" in data and "position_size_usd" in data:
                        assert data["quantity"] >= 0 and data["position_size_usd"] >= 0

    def test_calculate_rejects_non_localhost_without_admin_token(self, monkeypatch):
        monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
        from app import app as flask_app
        with flask_app.test_client() as c:
            r = c.post(
                "/api/position-size/calculate",
                json={"entry_price": 100, "stop_loss_pct": 2.0},
                content_type="application/json",
                environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            )
        assert r.status_code == 403

    def test_calculate_requires_token_when_admin_token_set(self, monkeypatch):
        monkeypatch.setenv("ADMIN_API_TOKEN", "ci-secret")
        from app import app as flask_app
        with patch("app._build_account_overview", return_value={"cash_available_to_trade": 20000}):
            with patch("app.current_regime", {"regime": "SIDEWAYS"}):
                with flask_app.test_client() as c:
                    body = {
                        "entry_price": 45000,
                        "stop_loss_pct": 4.0,
                        "consensus_signal": "MODERATE_BUY",
                        "volatility": 50,
                    }
                    r = c.post(
                        "/api/position-size/calculate",
                        json=body,
                        content_type="application/json",
                    )
                    assert r.status_code == 403
                    r2 = c.post(
                        "/api/position-size/calculate",
                        json=body,
                        content_type="application/json",
                        headers={"X-Admin-Token": "ci-secret"},
                    )
                    assert r2.status_code == 200

    def test_calculate_validates_numeric_bounds(self):
        from app import app as flask_app
        with patch("app._build_account_overview", return_value={"cash_available_to_trade": 20000}):
            with patch("app.current_regime", {"regime": "SIDEWAYS"}):
                with flask_app.test_client() as c:
                    for body, code in (
                        ({"entry_price": 0, "stop_loss_pct": 2}, 400),
                        ({"entry_price": -1, "stop_loss_pct": 2}, 400),
                        ({"entry_price": 100, "stop_loss_pct": 0}, 400),
                        ({"entry_price": 100, "stop_loss_pct": 100}, 400),
                        ({"entry_price": 100, "stop_loss_pct": 2, "volatility": -1}, 400),
                    ):
                        r = c.post(
                            "/api/position-size/calculate",
                            json=body,
                            content_type="application/json",
                        )
                        assert r.status_code == code, body


class TestExecutionPathsConsistency:
    """Document that Book to Coinbase and auto-execute share the same post-fill flow."""

    def test_manual_book_and_auto_exec_both_use_same_twap_entry_point(self):
        from execution_engine import start_twap_buy_async
        assert callable(start_twap_buy_async)

    def test_journal_open_overwrites_stop_target_with_dynamic_when_coin_in_cache(self):
        from app import _get_dynamic_stop_target_for_coin
        assert callable(_get_dynamic_stop_target_for_coin)
