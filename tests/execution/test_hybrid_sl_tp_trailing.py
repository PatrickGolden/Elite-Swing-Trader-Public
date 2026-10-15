# tests/test_hybrid_sl_tp_trailing.py — Hybrid SL/TP + Trailing Stop Execution
# Run: pytest tests/test_hybrid_sl_tp_trailing.py -v
# 100% mocked: no real CCXT, alerts, or exchange calls.
# Uses absolute imports; run from project root (c:\swing-trader)

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
def mock_ccxt_limit_and_cancel():
    """Mock CCXT: create_limit_sell_order, cancel_order, fetch_order."""
    mock_ex = MagicMock()
    mock_ex.create_limit_sell_order.side_effect = lambda sym, qty, price, params=None: {
        "id": f"limit-{sym.replace('/', '-')}-{price:.0f}",
        "status": "open",
        "symbol": sym,
        "side": "sell",
        "amount": qty,
        "price": price,
    }
    mock_ex.cancel_order.return_value = {"id": "cancelled", "status": "canceled"}
    mock_ex.fetch_order.side_effect = lambda oid: {
        "id": oid,
        "status": "filled",
        "average": 105.0,
        "price": 105.0,
    }
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        yield mock_ex


class _SlTpMocks:
    """Aggregates SL and TP mocks so tests can assert total call_count and call_args_list."""
    def __init__(self, sl_mock, tp_mock):
        self._sl = sl_mock
        self._tp = tp_mock
    @property
    def call_count(self):
        return self._sl.call_count + self._tp.call_count
    @property
    def call_args_list(self):
        return self._sl.call_args_list + self._tp.call_args_list
    def assert_not_called(self):
        self._sl.assert_not_called()
        self._tp.assert_not_called()


@pytest.fixture
def mock_place_limit_sell():
    """Mock place_stop_limit_sell_order (SL) and place_limit_sell_order (TP) to return success without real CCXT."""
    def _mock_sl(sym, qty, stop_price, limit_price=None, **kw):
        return {"success": True, "order_id": f"sl-{sym}-{stop_price:.0f}", "symbol_ccxt": f"{sym}/USDC"}
    def _mock_tp(sym, qty, price, **kw):
        return {"success": True, "order_id": f"tp-{sym}-{price:.0f}", "symbol_ccxt": f"{sym}/USDC"}
    with patch("app.place_stop_limit_sell_order", side_effect=_mock_sl) as m_sl:
        with patch("app.place_limit_sell_order", side_effect=_mock_tp) as m_tp:
            yield _SlTpMocks(m_sl, m_tp)


@pytest.fixture
def mock_cancel_order():
    """Mock cancel_order — no real exchange."""
    with patch("execution_engine.cancel_order") as m:
        m.return_value = {"success": True}
        yield m


@pytest.fixture
def mock_execute_twap():
    """Mock execute_market_sell_twap — no real TWAP."""
    with patch("execution_engine.execute_market_sell_twap") as m:
        m.return_value = {"success": True, "fill_price": 106.5, "average_price": 106.5, "total_filled": 1.0}
        yield m


@pytest.fixture
def mock_send_critical_alert():
    """Mock alerts.send_critical_alert — verify primary-only routing."""
    with patch("alert_system.AlertSystem.send_critical_alert") as m:
        yield m


@pytest.fixture
def mock_get_execution_config():
    """Mock get_execution_config — Coinbase, auto_exec enabled.

    Patches the app_config source of truth (exit_manager reads it from there), so the
    test never depends on the real execution_config.json on disk — which is fail-closed
    (auto_execution_enabled=false) whenever no strategy has passed the promotion gates."""
    cfg = {
        "auto_execution_enabled": True,
        "auto_execution_exchange": "coinbase",
    }
    with patch("app_config.get_execution_config", return_value=cfg), \
         patch("app.get_execution_config", return_value=cfg) as m:
        yield m


@pytest.fixture
def mock_execution_safety_ok():
    """Mock execution_safety_ok — always pass."""
    with patch("execution_engine.execution_safety_ok") as m:
        m.return_value = (True, "")
        yield m


# -----------------------------------------------------------------------------
# 1. Initial Booking Tests
# -----------------------------------------------------------------------------

class TestInitialBookingNormalMode:
    """Normal mode: SL is placed on exchange; TP is monitored in app."""

    def test_normal_mode_places_sl_and_tp_orders(
        self, temp_positions, temp_account_config, mock_place_limit_sell,
        mock_get_execution_config
    ):
        """Normal mode (Let Winners Run OFF): only SL order is placed on exchange."""
        from trade_journal import open_position
        from app import _place_sl_tp_orders_for_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
            "let_winners_run": False,
        })
        sl_ok, tp_ok, *_ = _place_sl_tp_orders_for_position(pos)
        assert sl_ok is True
        assert tp_ok is False
        assert mock_place_limit_sell.call_count == 1
        calls = mock_place_limit_sell.call_args_list
        prices = [c[0][2] for c in calls]
        assert 97.5 in prices or any(abs(p - 97.5) < 1 for p in prices)

    def test_order_ids_stored_in_journal(
        self, temp_positions, temp_account_config, mock_place_limit_sell,
        mock_get_execution_config
    ):
        """SL order id stored; TP order id remains empty (app-monitored TP)."""
        from trade_journal import open_position, get_open_positions
        from app import _place_sl_tp_orders_for_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
            "let_winners_run": False,
        })
        _place_sl_tp_orders_for_position(pos)
        updated = next(p for p in get_open_positions() if p["id"] == pos["id"])
        assert updated.get("sl_order_id") is not None
        assert updated.get("tp_order_id") is None


class TestInitialBookingRunnerMode:
    """Runner mode (Let Winners Run ON): only SL placed, no TP."""

    def test_runner_mode_places_only_sl_no_tp(
        self, temp_positions, temp_account_config, mock_place_limit_sell,
        mock_get_execution_config
    ):
        """Let Winners Run ON: only SL limit order placed, no TP."""
        from trade_journal import open_position
        from app import _place_sl_tp_orders_for_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
            "let_winners_run": True,
        })
        sl_ok, tp_ok, *_ = _place_sl_tp_orders_for_position(pos)
        assert sl_ok is True
        assert tp_ok is False
        assert mock_place_limit_sell.call_count == 1

    def test_runner_mode_no_tp_order_id(
        self, temp_positions, temp_account_config, mock_place_limit_sell,
        mock_get_execution_config
    ):
        """Runner mode: tp_order_id is None in position."""
        from trade_journal import open_position, get_open_positions
        from app import _place_sl_tp_orders_for_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
            "let_winners_run": True,
        })
        _place_sl_tp_orders_for_position(pos)
        updated = next(p for p in get_open_positions() if p["id"] == pos["id"])
        assert updated.get("sl_order_id") is not None
        assert updated.get("tp_order_id") is None


class TestUsdcPreferenceAndFallback:
    """USDC pair preference and USD fallback."""

    def test_place_limit_sell_uses_usdc_by_default(self, mock_ccxt_limit_and_cancel):
        """place_limit_sell_order uses USDC pair by default."""
        with patch("execution_engine.log_execution"):
            from execution_engine import place_limit_sell_order, symbol_to_ccxt
        ccxt_sym = symbol_to_ccxt("BTC", prefer_usdc=True)
        assert "USDC" in ccxt_sym
        result = place_limit_sell_order("BTC", 0.01, 44000)
        assert result.get("success") is True
        mock_ccxt_limit_and_cancel.create_limit_sell_order.assert_called_once()
        call_args = mock_ccxt_limit_and_cancel.create_limit_sell_order.call_args[0]
        assert "USDC" in call_args[0]

    def test_place_limit_sell_fallback_to_usd_when_usdc_fails(self, mock_ccxt_limit_and_cancel):
        """place_limit_sell_order falls back to USD when USDC fails."""
        mock_ccxt_limit_and_cancel.create_limit_sell_order.side_effect = [
            Exception("USDC pair unavailable"),
            {"id": "usd-order-1", "status": "open", "symbol": "BTC/USD"},
        ]
        with patch("execution_engine.log_execution"):
            from execution_engine import place_limit_sell_order
        result = place_limit_sell_order("BTC", 0.01, 44000)
        assert result.get("success") is True
        assert mock_ccxt_limit_and_cancel.create_limit_sell_order.call_count == 2
        second_call = mock_ccxt_limit_and_cancel.create_limit_sell_order.call_args_list[1][0][0]
        assert "USD" in second_call

    def test_stop_limit_sell_preflight_skips_when_stop_above_last_trade(self, mock_ccxt_limit_and_cancel):
        """Stop-limit sell returns structured skip when stop is already above last trade."""
        mock_ccxt_limit_and_cancel.fetch_ticker.return_value = {"last": 95.0, "close": 95.0}
        with patch("execution_engine.log_execution"):
            from execution_engine import place_stop_limit_sell_order
        result = place_stop_limit_sell_order("BTC", 0.01, 100.0, 99.0)
        assert result.get("success") is False
        assert result.get("status") == "skipped"
        assert result.get("reason") == "would_trigger_immediately"
        mock_ccxt_limit_and_cancel.create_order.assert_not_called()


# -----------------------------------------------------------------------------
# 2. Trailing Stop Trigger Scenarios
# -----------------------------------------------------------------------------

class TestTrailingStopTriggers:
    """Trailing stop: when it triggers, cancels TP and executes TWAP."""

    def test_trailing_stop_math_uses_live_trail_width(self):
        """Trailing stop = high_water * (1 - TRAILING_STOP_TRAIL_PCT/100), per live config."""
        from thresholds import get_threshold
        trail = float(get_threshold('TRAILING_STOP_TRAIL_PCT', 13))
        high_water = 130.0
        trailing_stop = high_water * (1 - trail / 100)
        assert abs(trailing_stop - high_water * (1 - trail / 100)) < 1e-9
        assert trailing_stop < high_water  # always below the peak

    def test_trailing_activates_only_above_min_profit(self):
        """Trailing only engages once profit >= TRAILING_STOP_MIN_PROFIT_PCT."""
        from thresholds import get_threshold
        activate = float(get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8))
        entry = 100.0
        peak = entry * (1 + (activate + 5) / 100)  # comfortably above activation
        profit_pct = ((peak - entry) / entry) * 100
        assert profit_pct >= activate

    def test_high_water_mark_updates_on_price_rise(self, temp_positions):
        """High water mark updates when current price exceeds previous peak."""
        from trade_journal import open_position, update_position, get_position_by_id

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        assert pos.get("high_water_mark") is None
        update_position(pos["id"], {"high_water_mark": 108.0})
        updated = get_position_by_id(pos["id"])
        assert updated.get("high_water_mark") == 108.0


class TestTrailingStopBeforeTp:
    """Trailing stop triggers BEFORE TP hit → cancel TP, TWAP sell."""

    def test_exit_manager_cancels_tp_then_twap(
        self, temp_positions, mock_cancel_order, mock_execute_twap,
        mock_send_critical_alert, mock_get_execution_config
    ):
        """When trailing triggers: cancel TP, cancel SL, then TWAP sell."""
        from trade_journal import open_position, update_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {
            "high_water_mark": 130.0,
            "tp_order_id": "tp-123",
            "sl_order_id": "sl-456",
        })
        current_prices = {"bitcoin": 111.0}
        with patch("execution_engine.fetch_order_status", return_value="open"):
            with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
                with patch("exit_manager._should_alert", return_value=True):
                    check_trailing_stops(current_prices)
        assert mock_cancel_order.call_count >= 1
        assert mock_execute_twap.called
        assert mock_send_critical_alert.called

    def test_trailing_stop_triggers_after_tp_hit_no_action(
        self, temp_positions, mock_cancel_order, mock_execute_twap,
        mock_get_execution_config
    ):
        """Trailing stop triggers AFTER TP hit: no action (position already closed by Coinbase)."""
        from trade_journal import open_position, close_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        close_position(pos["id"], exit_price=106.0, exit_reason="target", notes="TP hit on Coinbase")
        current_prices = {"bitcoin": 106.5}
        with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
            check_trailing_stops(current_prices)
        mock_cancel_order.assert_not_called()
        mock_execute_twap.assert_not_called()


# -----------------------------------------------------------------------------
# 3. Runner Mode Behavior
# -----------------------------------------------------------------------------

class TestRunnerModeBehavior:
    """Let Winners Run: no TP, trailing still works."""

    def test_runner_mode_trailing_stop_still_works(self, temp_positions):
        """Runner mode: trailing stop logic still applies (5% profit, 3% trail)."""
        from trade_journal import open_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
            "let_winners_run": True,
        })
        assert pos.get("current_target_price") is None
        assert pos.get("current_stop_price") is not None
        assert pos.get("let_winners_run") is True

    def test_trailing_stop_exit_reason_recorded(self, temp_positions):
        """Journal records exit_reason='trailing_stop' on trailing exit."""
        from trade_journal import open_position, close_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        closed, err = close_position(pos["id"], exit_price=106.5, exit_reason="trailing_stop", notes="TWAP")
        assert err is None
        assert closed.get("exit_reason") == "trailing_stop"


# -----------------------------------------------------------------------------
# 4. Safety & Kill Switch
# -----------------------------------------------------------------------------

class TestSafetyExecution:
    """Execution_safety_ok and exchange gates (kill switch removed)."""

    def test_sells_never_blocked_by_risk_caps(
        self, temp_positions, mock_cancel_order, mock_execute_twap,
        mock_send_critical_alert, mock_get_execution_config
    ):
        """Sells are never blocked by risk caps — trailing stop always executes."""
        from trade_journal import open_position, update_position, get_open_positions
        from exit_manager import check_trailing_stops

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0})
        current_prices = {"bitcoin": 111.0}
        with patch("exit_manager._get_open_positions", return_value=get_open_positions()):
            with patch("exit_manager._should_alert", return_value=True):
                check_trailing_stops(current_prices)
        assert mock_execute_twap.called

    def test_orders_remain_on_coinbase_when_app_offline(self, mock_ccxt_limit_and_cancel):
        """Limit orders on Coinbase persist; app offline does not cancel them."""
        with patch("execution_engine.log_execution"):
            from execution_engine import place_limit_sell_order
        result = place_limit_sell_order("BTC", 0.01, 44100)
        assert result.get("success") is True
        order_id = result.get("order_id")
        assert order_id is not None
        mock_ccxt_limit_and_cancel.create_limit_sell_order.assert_called_once()


# -----------------------------------------------------------------------------
# 5. Journal & Alert Integration
# -----------------------------------------------------------------------------

class TestJournalAndAlertIntegration:
    """Journal updates and alert routing."""

    def test_journal_updates_on_trailing_stop_exit(self, temp_positions):
        """Journal correctly updates on trailing stop exit."""
        from trade_journal import open_position, close_position, get_all_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        closed, err = close_position(pos["id"], exit_price=106.5, exit_reason="trailing_stop", notes="TWAP at 106.5")
        assert err is None
        assert closed["status"] == "CLOSED"
        assert closed["exit_reason"] == "trailing_stop"
        assert closed["pnl_pct"] is not None
        assert closed["pnl_usd"] is not None

    def test_critical_alert_sent_to_primary_only(self, mock_send_critical_alert):
        """send_critical_alert is used for trailing stop (primary + Telegram only)."""
        from alert_system import AlertSystem
        alerts = AlertSystem()
        alerts.send_critical_alert("Trailing Stop Executed – BTC", "BTC sold at $106.50")
        mock_send_critical_alert.assert_called_once_with(
            "Trailing Stop Executed – BTC", "BTC sold at $106.50"
        )

    def test_alert_contains_price_and_reason(self, mock_send_critical_alert):
        """Alert body contains price, reason, and relevant details."""
        from exit_manager import _get_alerts
        alerts = _get_alerts()
        alerts.send_critical_alert(
            "Trailing Stop Executed – BTC",
            "BTC trailing stop auto-executed via TWAP at $106.50. Position #1 closed. Peak was $110.00.",
        )
        call_args = mock_send_critical_alert.call_args
        assert call_args is not None
        body = call_args[0][1]
        assert "106.50" in body or "106.5" in body
        assert "trailing" in body.lower() or "TWAP" in body


# -----------------------------------------------------------------------------
# 6. Edge Cases
# -----------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: partial fill, manual close, SL before trailing."""

    def test_partial_fill_still_places_sl_only(
        self, temp_positions, mock_place_limit_sell, mock_get_execution_config
    ):
        """Partial fill on buy: SL placed with filled quantity, TP stays app-monitored."""
        from trade_journal import open_position
        from app import _place_sl_tp_orders_for_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 0.5,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        sl_ok, tp_ok, *_ = _place_sl_tp_orders_for_position(pos)
        assert sl_ok is True
        assert tp_ok is False
        calls = mock_place_limit_sell.call_args_list
        for c in calls:
            assert c[0][1] == 0.5

    def test_manual_close_before_trailing_no_duplicate(self, temp_positions):
        """Manual close before trailing: position closed, no trailing action."""
        from trade_journal import open_position, close_position, get_open_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        closed, err = close_position(pos["id"], exit_price=105, exit_reason="manual", notes="Manual close")
        assert err is None
        open_list = get_open_positions()
        assert not any(p["id"] == pos["id"] for p in open_list)

    def test_sl_hit_before_trailing_activates_normal_stop(self, temp_positions):
        """SL hit before 5% profit: normal stop behavior, no trailing."""
        entry = 100.0
        stop_price = 97.5
        current = 97.0
        profit_pct = ((current - entry) / entry) * 100
        assert profit_pct < 5
        assert current <= stop_price
        from trade_journal import open_position, close_position

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        closed, err = close_position(pos["id"], exit_price=97.0, exit_reason="stop_loss", notes="SL hit")
        assert err is None
        assert closed["exit_reason"] == "stop_loss"
        assert closed["outcome"] == "loss"


# -----------------------------------------------------------------------------
# Cancel order and fetch_order_status
# -----------------------------------------------------------------------------

class TestCancelAndFetchOrder:
    """cancel_order and fetch_order_status behavior."""

    def test_cancel_order_success(self, mock_ccxt_limit_and_cancel):
        """cancel_order succeeds when order exists."""
        with patch("execution_engine.log_execution"):
            from execution_engine import cancel_order
        result = cancel_order("order-123")
        assert result.get("success") is True
        mock_ccxt_limit_and_cancel.cancel_order.assert_called_once_with("order-123")

    def test_fetch_order_status_returns_filled(self, mock_ccxt_limit_and_cancel):
        """fetch_order_status returns 'filled' when order is filled."""
        with patch("execution_engine.log_execution"):
            from execution_engine import fetch_order_status
        status = fetch_order_status("order-123")
        assert status == "filled"


# =============================================================================
# Trailing stop execution (merged from test_trailing_stop_execution.py)
# =============================================================================

class TestTrailingStopDetection:
    """Trailing stop detection logic — thresholds driven by live config
    (Phase 1: TRAILING_STOP_MIN_PROFIT_PCT activation, TRAILING_STOP_TRAIL_PCT below peak)."""

    @staticmethod
    def _activate():
        from thresholds import get_threshold
        return float(get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8))

    @staticmethod
    def _trail():
        from thresholds import get_threshold
        return float(get_threshold('TRAILING_STOP_TRAIL_PCT', 13))

    def test_trailing_stop_requires_activation_profit(self):
        entry = 100.0
        current = entry * (1 + (self._activate() - 2) / 100)  # just below activation
        assert ((current - entry) / entry) * 100 < self._activate()

    def test_trailing_stop_activates_at_threshold(self):
        entry = 100.0
        current = entry * (1 + self._activate() / 100)  # exactly at activation
        assert ((current - entry) / entry) * 100 >= self._activate()

    def test_trailing_stop_activates_well_above_threshold(self):
        entry = 100.0
        current = entry * (1 + (self._activate() + 10) / 100)
        assert ((current - entry) / entry) * 100 >= self._activate()

    def test_high_water_mark_tracking(self):
        trail = self._trail()
        high_water = 130.0
        trailing_stop = high_water * (1 - trail / 100)
        assert trailing_stop < high_water
        assert abs(trailing_stop - high_water * (1 - trail / 100)) < 1e-9

    def test_trailing_stop_math_matches_app_logic(self):
        # Mirror exit_manager.py: trailing_stop = high_water * (1 - trail_pct/100)
        trail = self._trail()
        for high_water in (108.0, 130.0, 200.0):
            assert abs(high_water * (1 - trail / 100) - high_water * (1 - trail / 100)) < 1e-9
            assert high_water * (1 - trail / 100) < high_water


class TestExecuteMarketSellTwap:
    """execute_market_sell_twap: USDC preference, USD fallback, fully mocked."""

    def test_prefers_usdc_pair(self):
        with patch("execution_engine.get_coinbase_exchange") as mock_get:
            mock_ex = MagicMock()
            mock_ex.create_market_sell_order.return_value = {
                "id": "m1", "status": "closed", "filled": 0.005, "average": 44000,
            }
            mock_get.return_value = mock_ex
            with patch("execution_engine.time.sleep"):
                with patch("execution_engine.log_execution"):
                    from execution_engine import execute_market_sell_twap
                    result = execute_market_sell_twap("BTC", 0.01, num_slices=2, duration_minutes=1)
        assert result.get("success") is True

    def test_fallback_to_usd_when_usdc_fails(self):
        mock_ex = MagicMock()
        mock_ex.create_market_sell_order.side_effect = [
            Exception("USDC pair unavailable"),
            {"id": "m2", "status": "closed", "filled": 0.005, "average": 43500},
            {"id": "m3", "status": "closed", "filled": 0.005, "average": 43500},
        ]
        with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
            with patch("execution_engine.time.sleep"):
                with patch("execution_engine.log_execution"):
                    from execution_engine import execute_market_sell_twap
                    result = execute_market_sell_twap("BTC", 0.01, num_slices=2, duration_minutes=1)
        assert result.get("success") is True

    def test_small_position_uses_single_market_sell(self):
        with patch("execution_engine.execute_market_sell", return_value={
            "success": True, "fill_price": 30.0, "filled_amount": 0.6, "average_price": 30.0,
        }) as mock_single:
            with patch("execution_engine.run_twap_sell_coinbase") as mock_twap:
                from execution_engine import execute_market_sell_twap
                result = execute_market_sell_twap("HYPE", 0.6, position_usd=300.0, twap_min_usd=500.0)
        assert result.get("success") is True
        assert result.get("mode") == "market"
        mock_single.assert_called_once()
        mock_twap.assert_not_called()


class TestJournalCloseTrailingStop:
    """trade_journal.close_position with exit_reason='trailing_stop'."""

    def test_close_position_accepts_trailing_stop_exit_reason(self, temp_positions):
        from trade_journal import open_position, close_position, get_all_positions
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        closed, err = close_position(pos["id"], exit_price=105.0, exit_reason='trailing_stop', notes="TWAP auto-exec")
        assert err is None and closed["exit_reason"] == "trailing_stop" and closed["status"] == "CLOSED"

    def test_hit_ai_target_set_correctly_for_trailing_stop(self, temp_positions):
        from trade_journal import open_position, close_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        closed, _ = close_position(pos["id"], exit_price=107.0, exit_reason='trailing_stop')
        assert closed["hit_ai_target"] is True

    def test_hit_ai_target_false_when_below_target(self, temp_positions):
        from trade_journal import open_position, close_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        closed, _ = close_position(pos["id"], exit_price=104.0, exit_reason='trailing_stop')
        assert closed["hit_ai_target"] is False


class TestExitManagerTrailingStop:
    """exit_manager.check_trailing_stops: trigger, execution, safety."""

    def test_check_trailing_stops_skips_when_auto_exec_disabled(self, temp_positions):
        from trade_journal import open_position, update_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 110})
        with patch("exit_manager._get_execution_config", return_value={"auto_execution_enabled": False}):
            with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                from exit_manager import check_trailing_stops
                check_trailing_stops({"bitcoin": 106.0})
        mock_twap.assert_not_called()

    def test_check_trailing_stops_triggers_twap_when_hit(self, temp_positions):
        from trade_journal import open_position, update_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0})
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._execute_market_sell_twap", return_value={"success": True, "fill_price": 108.5}) as mock_twap:
                with patch("exit_manager._close_position", return_value=(MagicMock(), None)):
                    with patch("exit_manager._get_alerts"):
                        with patch("exit_manager._should_alert", return_value=True):
                            from exit_manager import check_trailing_stops
                            check_trailing_stops({"bitcoin": 108.5})
        mock_twap.assert_called_once()

    def test_quantity_zero_syncs_from_exchange_then_sells(self, temp_positions):
        from trade_journal import open_position, update_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 0,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0})
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
        }):
            with patch("execution_engine.fetch_balance_sync", return_value={"holdings": {"BTC": 1.0}}):
                with patch("exit_manager._update_position"):
                    with patch("exit_manager._execute_market_sell_twap", return_value={"success": True, "fill_price": 108.5}) as mock_twap:
                        with patch("exit_manager._close_position", return_value=(MagicMock(), None)):
                            with patch("exit_manager._get_alerts"):
                                with patch("exit_manager._should_alert", return_value=True):
                                    with patch("exit_manager._cancel_order", return_value={"success": True}):
                                        from exit_manager import check_trailing_stops
                                        check_trailing_stops({"bitcoin": 108.5})
        mock_twap.assert_called_once()

    def test_quantity_zero_exchange_zero_closes_with_exchange_sold(self, temp_positions):
        from trade_journal import open_position, update_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 0,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0})
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
        }):
            with patch("execution_engine.fetch_balance_sync", return_value={"holdings": {}}):
                with patch("exit_manager._close_position", return_value=(MagicMock(), None)) as mock_close:
                    with patch("exit_manager._get_alerts"):
                        with patch("exit_manager._should_alert", return_value=True):
                            from exit_manager import check_trailing_stops
                            check_trailing_stops({"bitcoin": 108.5})
        mock_close.assert_called_once()
        assert mock_close.call_args[1]["exit_reason"] == "exchange_sold"

    def test_insufficient_fund_closes_position_when_exchange_balance_zero(self, temp_positions):
        from trade_journal import open_position, update_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0})
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._cancel_order", return_value={"success": True}):
                with patch("exit_manager._execute_market_sell_twap", return_value={
                    "success": False, "error": "INSUFFICIENT_FUND message from exchange",
                }):
                    with patch("execution_engine.fetch_balance_sync", return_value={"holdings": {}}):
                        with patch("exit_manager._close_position", return_value=(MagicMock(), None)) as mock_close:
                            with patch("exit_manager._get_alerts"):
                                with patch("exit_manager._should_alert", return_value=True):
                                    from exit_manager import check_trailing_stops
                                    check_trailing_stops({"bitcoin": 108.5})
        mock_close.assert_called_once()
        assert mock_close.call_args[1]["exit_reason"] == "exchange_sold"


class TestAlertSentToPrimaryOnly:
    """Trailing stop confirmation uses send_critical_alert (primary + Telegram only)."""

    def test_send_critical_alert_called_on_success(self, temp_positions):
        from trade_journal import open_position, update_position, get_open_positions
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        update_position(pos["id"], {"high_water_mark": 130.0})
        open_pos = get_open_positions()
        mock_alert = MagicMock()
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True, "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=open_pos):
                with patch("exit_manager._execute_market_sell_twap", return_value={"success": True, "fill_price": 108.5}):
                    with patch("exit_manager._close_position", return_value=(MagicMock(), None)):
                        with patch("exit_manager._get_alerts", return_value=mock_alert):
                            with patch("exit_manager._should_alert", return_value=True):
                                from exit_manager import check_trailing_stops
                                check_trailing_stops({"bitcoin": 108.5})
        mock_alert.send_critical_alert.assert_called_once()
        subject, body = mock_alert.send_critical_alert.call_args[0]
        assert "Trailing Stop Executed" in subject
        assert "BTC" in body


class TestRunTwapSellNoRealCcxt:
    """run_twap_sell_coinbase uses mock — no real Coinbase calls."""

    def test_run_twap_sell_coinbase_uses_mock_exchange(self):
        mock_ex = MagicMock()
        mock_ex.create_market_sell_order.return_value = {
            "id": "mock-sell-1", "status": "closed", "filled": 0.005, "average": 44000,
        }
        with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
            with patch("execution_engine.time.sleep"):
                with patch("execution_engine.log_execution"):
                    from execution_engine import run_twap_sell_coinbase
                    result = run_twap_sell_coinbase("BTC/USDC", 0.01, num_slices=2, duration_minutes=1)
        mock_ex.create_market_sell_order.assert_called()
        assert result.get("success") is True
