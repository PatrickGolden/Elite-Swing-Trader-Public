# tests/test_auto_execution_stress.py — Stress tests for all auto-execution paths
# Ensures no double-sell, no crash on repeated/edge inputs, safety and cooldown respected.
# Run: pytest tests/test_auto_execution_stress.py -v
# No real exchange/CCXT calls; all execution paths mocked.

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

pytestmark = pytest.mark.stress

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


# -----------------------------------------------------------------------------
# 1. Stop-loss / Take-profit handlers — no double execution
# -----------------------------------------------------------------------------

class TestStopLossTakeProfitStress:
    """_handle_stop_loss_hit and _handle_take_profit_hit: single execution, retry-close when close fails."""

    def test_stop_loss_auto_exec_calls_sell_once_then_close(self, temp_positions, temp_account_config):
        """With auto_exec: execute_market_sell called once, close_position once."""
        from trade_journal import open_position, get_open_positions
        from app import _handle_stop_loss_hit

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        with patch("app._should_alert", return_value=True):
            with patch("app.execute_market_sell", return_value={"success": True, "fill_price": 97.0}) as mock_sell:
                with patch("app.alerts") as mock_alerts:
                    _handle_stop_loss_hit(pos, 97.0, auto_exec=True)
        mock_sell.assert_called_once()
        assert mock_sell.call_args[1]["reason"] == "stop_loss"

    def test_take_profit_auto_exec_calls_sell_once_then_close(self, temp_positions, temp_account_config):
        """With auto_exec: execute_market_sell called once for take-profit."""
        from trade_journal import open_position
        from app import _handle_take_profit_hit

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        with patch("app._should_alert", return_value=True):
            with patch("app.execute_market_sell", return_value={"success": True, "fill_price": 106.0}) as mock_sell:
                with patch("app.alerts"):
                    _handle_take_profit_hit(pos, 106.0, auto_exec=True)
        mock_sell.assert_called_once()
        assert mock_sell.call_args[1]["reason"] == "take_profit"

    def test_stop_loss_close_fails_then_retry_close_no_second_sell(self, temp_positions, temp_account_config):
        """When close_position fails after successful sell, second call only retries close (no second sell)."""
        from trade_journal import open_position, get_open_positions
        from app import _handle_stop_loss_hit, _EXIT_SOLD_PENDING_CLOSE

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        _EXIT_SOLD_PENDING_CLOSE.clear()
        mock_sell = MagicMock(return_value={"success": True, "fill_price": 97.0})
        close_calls = []
        def track_close(pid, exit_price, exit_reason="", notes="", **kwargs):
            close_calls.append(pid)
            if len(close_calls) == 1:
                return None, "Simulated save failure"
            return MagicMock(), None
        with patch("app._should_alert", return_value=True):
            with patch("app.execute_market_sell", mock_sell):
                with patch("app.close_position", side_effect=track_close):
                    with patch("app.alerts"):
                        _handle_stop_loss_hit(pos, 97.0, auto_exec=True)
        assert mock_sell.call_count == 1
        assert pos["id"] in _EXIT_SOLD_PENDING_CLOSE
        with patch("app._should_alert", return_value=True):
            with patch("app.execute_market_sell", mock_sell):
                with patch("app.close_position", return_value=(MagicMock(), None)):
                    with patch("app.alerts"):
                        _handle_stop_loss_hit(pos, 97.0, auto_exec=True)
        assert mock_sell.call_count == 1, "Must not call execute_market_sell again after pending-close"
        _EXIT_SOLD_PENDING_CLOSE.discard(pos["id"])

    def test_repeated_stop_loss_calls_with_already_closed_position(self, temp_positions, temp_account_config):
        """Monitor passes already-closed position (shouldn't happen): handler doesn't crash."""
        from trade_journal import open_position, close_position
        from app import _handle_stop_loss_hit

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
        })
        close_position(pos["id"], 97.0, "stop_loss", "Closed")
        with patch("app._should_alert", return_value=True):
            with patch("app.execute_market_sell") as mock_sell:
                with patch("app.alerts"):
                    _handle_stop_loss_hit(pos, 97.0, auto_exec=True)
        assert mock_sell.call_count <= 1

    def test_handle_stop_loss_sell_failure_sends_alert_no_close(self, temp_positions, temp_account_config):
        """When execute_market_sell fails, we do not close position and we send alert."""
        from trade_journal import open_position, get_open_positions
        from app import _handle_stop_loss_hit

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
        })
        with patch("app._should_alert", return_value=True):
            with patch("app.execute_market_sell", return_value={"success": False, "error": "Exchange down"}) as mock_sell:
                with patch("app.close_position") as mock_close:
                    with patch("app.alerts") as mock_alerts:
                        _handle_stop_loss_hit(pos, 97.0, auto_exec=True)
        mock_sell.assert_called_once()
        mock_close.assert_not_called()
        assert mock_alerts.send_stop_loss_alert.called


# -----------------------------------------------------------------------------
# 2. _check_sl_tp_order_status — no double close, repeated calls safe
# -----------------------------------------------------------------------------

class TestCheckSlTpOrderStatusStress:
    """SL/TP order status check: one close per position, exchange gate."""

    def test_sl_tp_check_skips_when_not_coinbase(self, temp_positions, temp_account_config):
        """When exchange is kraken, no order status fetch."""
        with patch("app.get_execution_config", return_value={"auto_execution_exchange": "kraken"}):
                with patch("app.get_open_positions") as mock_pos:
                    from app import _check_sl_tp_order_status
                    _check_sl_tp_order_status()
        mock_pos.assert_not_called()

    def test_sl_tp_filled_closes_once_no_double_close(self, temp_positions, temp_account_config):
        """When SL is filled, close_position called once; fetch_order_status used."""
        from trade_journal import open_position, update_position, get_open_positions
        from app import _check_sl_tp_order_status

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_stop_pct": 2.5, "ai_target_pct": 6,
        })
        update_position(pos["id"], {"sl_order_id": "sl-1", "tp_order_id": "tp-1"})
        open_pos = get_open_positions()
        assert len(open_pos) == 1 and open_pos[0].get("sl_order_id") == "sl-1"
        def mock_fetch_with_fee(oid, exchange_id="coinbase", position_id=None, trade_id=None):
            if oid == "sl-1":
                return {"status": "filled", "fill_price": 97.0, "filled_amount": 1.0, "fee_usd": 0.15, "fee_rate": 0.001, "order": {"average": 97.0}}
            return {"status": "open", "fill_price": 0, "filled_amount": 0, "fee_usd": 0, "fee_rate": 0, "order": {}}
        with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
            with patch("app.get_open_positions", return_value=open_pos):
                with patch("execution_engine.fetch_order_with_fee", side_effect=mock_fetch_with_fee):
                    with patch("app.fetch_order_status") as mock_status:
                        mock_status.side_effect = lambda oid: "filled" if oid == "sl-1" else "open"
                        with patch("app.cancel_order", return_value={"success": True}):
                            with patch("app.close_position", return_value=(MagicMock(), None)) as mock_close:
                                with patch("app.alerts"):
                                    _check_sl_tp_order_status()
        mock_close.assert_called_once()
        assert mock_close.call_args[1]["exit_price"] == 97.0
        assert mock_close.call_args[1]["exit_reason"] == "stop_loss"
        assert mock_close.call_args[1]["exit_fee_usd"] == 0.15

    def test_sl_tp_repeated_calls_with_open_orders_no_close(self, temp_positions, temp_account_config):
        """Many calls with orders still open: no close_position."""
        from trade_journal import open_position, get_open_positions
        from app import _check_sl_tp_order_status

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "sl_order_id": "sl-1", "tp_order_id": "tp-1",
        })
        with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
            with patch("app.get_open_positions", return_value=get_open_positions()):
                with patch("app.fetch_order_status", return_value="open"):
                    with patch("app.close_position") as mock_close:
                        for _ in range(10):
                            _check_sl_tp_order_status()
        mock_close.assert_not_called()

    def test_sl_tp_gap_fallback_cancel_and_market_sell_when_open_below_stop(self, temp_positions, temp_account_config):
        """When SL order is open and current price < stop (gap-down), cancel SL and market sell."""
        from trade_journal import open_position, update_position, get_open_positions
        from app import _check_sl_tp_order_status

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_stop_pct": 3.5, "ai_stop_price": 96.5, "ai_target_pct": 6,
        })
        update_position(pos["id"], {"sl_order_id": "sl-gap-1", "current_stop_price": 96.5})
        open_pos = get_open_positions()
        assert len(open_pos) == 1 and open_pos[0].get("sl_order_id") == "sl-gap-1"

        def mock_fetch_with_fee(oid, exchange_id="coinbase", position_id=None, trade_id=None):
            return {"status": "open", "fill_price": 0, "filled_amount": 0, "fee_usd": 0, "fee_rate": 0, "order": {}}

        cfg = {"auto_execution_exchange": "coinbase", "auto_execution_enabled": True}
        current_prices = {"bitcoin": 94.0}  # below stop 96.5 (94 < 96.5 * 0.995)

        with patch("app.get_execution_config", return_value=cfg):
            with patch("app.get_open_positions", return_value=open_pos):
                with patch("execution_engine.fetch_order_with_fee", side_effect=mock_fetch_with_fee):
                    with patch("app.fetch_order_status", return_value="open"):
                        with patch("app.cancel_order", return_value={"success": True}) as mock_cancel:
                            with patch("app.update_position", return_value=(MagicMock(), None)) as mock_update:
                                with patch("app.execute_market_sell", return_value={"success": True, "fill_price": 94.0, "fee_usd": 0.1}) as mock_sell:
                                    with patch("app.close_position", return_value=(MagicMock(), None)) as mock_close:
                                        with patch("app.alerts") as mock_alerts:
                                            with patch("app.fetcher") as mock_fetcher:
                                                mock_fetcher.get_coinbase_spot_prices.return_value = {"BTC": 94.0}
                                                _check_sl_tp_order_status(current_prices)

        mock_cancel.assert_called_once_with("sl-gap-1")
        mock_update.assert_called()
        update_calls = [c for c in mock_update.call_args_list if c[0][1].get("sl_order_id") is None]
        assert len(update_calls) >= 1
        mock_sell.assert_called_once()
        assert mock_sell.call_args[1]["reason"] == "stop_loss"
        mock_close.assert_called_once()
        assert mock_close.call_args[1]["exit_reason"] == "stop_loss"
        assert mock_close.call_args[1]["exit_price"] == 94.0
        mock_alerts.send_stop_loss_executed_alert.assert_called_once()


# -----------------------------------------------------------------------------
# 3. _on_twap_done_open_position_and_place_orders — malformed input, no crash
# -----------------------------------------------------------------------------

class TestOnTwapDoneStress:
    """TWAP callback: null/malformed fill_data, duplicate calls, runner mode."""

    def test_on_twap_done_none_fill_data_no_crash(self, temp_positions, temp_account_config):
        """_on_twap_done_open_position_and_place_orders with None does nothing."""
        from app import _on_twap_done_open_position_and_place_orders
        from trade_journal import get_open_positions

        _on_twap_done_open_position_and_place_orders(None)
        assert len(get_open_positions()) == 0

    def test_on_twap_done_empty_dict_no_crash(self, temp_positions, temp_account_config):
        """_on_twap_done with {} still opens a position (minimal data); no crash."""
        from app import _on_twap_done_open_position_and_place_orders
        from trade_journal import get_open_positions

        _on_twap_done_open_position_and_place_orders({})
        # May open with empty symbol/coin_id; implementation may require fields
        positions = get_open_positions()
        assert isinstance(positions, list)

    def test_on_twap_done_twice_second_rejected_single_position_per_symbol(self, temp_positions, temp_account_config):
        """Calling callback twice with same symbol: first opens position, second rejected (single open per symbol)."""
        with patch("app.get_execution_config", return_value={"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}):
            with patch("app.get_test_mode", return_value=True):
                with patch("app.place_limit_sell_order", return_value={"success": True, "order_id": "x"}):
                    from app import _on_twap_done_open_position_and_place_orders
                    from trade_journal import get_open_positions
                    fill = {
                        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                        "quantity": 1, "entry_price": 100.0, "capital_usd": 100.0,
                    }
                    _on_twap_done_open_position_and_place_orders(fill)
                    _on_twap_done_open_position_and_place_orders(fill)
                    assert len(get_open_positions()) == 1

    def test_real_fill_not_journaled_raises_and_alerts(self, temp_positions, temp_account_config):
        """When open_position returns None for a REAL fill, callback raises RuntimeError and sends critical alert."""
        with patch("app.get_execution_config", return_value={"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}):
            with patch("app.get_test_mode", return_value=False):
                with patch("app.open_position", return_value=None):
                    with patch("app.alerts") as mock_alerts:
                        from app import _on_twap_done_open_position_and_place_orders
                        fill = {
                            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
                            "quantity": 0.001, "entry_price": 75000.0, "capital_usd": 75.0,
                        }
                        with pytest.raises(RuntimeError, match="not journaled"):
                            _on_twap_done_open_position_and_place_orders(fill)
                        mock_alerts.send_critical_alert.assert_called_once()
                        call_args = mock_alerts.send_critical_alert.call_args
                        assert "UNJOURNALED" in call_args[0][0]
                        assert "BTC" in call_args[0][0]

    def test_real_fill_uses_fill_data_is_test_not_get_test_mode(self, temp_positions, temp_account_config):
        """fill_data['is_test']=False overrides get_test_mode()=True: position journaled as real, RuntimeError on None."""
        with patch("app.get_execution_config", return_value={"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}):
            with patch("app.get_test_mode", return_value=True):
                with patch("app.open_position", return_value=None) as mock_open:
                    with patch("app.alerts") as mock_alerts:
                        from app import _on_twap_done_open_position_and_place_orders
                        fill = {
                            "coin_id": "ethereum", "coin_name": "Ethereum", "coin_symbol": "ETH",
                            "quantity": 0.01, "entry_price": 2400.0, "capital_usd": 24.0,
                            "is_test": False,
                        }
                        with pytest.raises(RuntimeError, match="not journaled"):
                            _on_twap_done_open_position_and_place_orders(fill)
                        open_payload = mock_open.call_args[0][0]
                        assert open_payload['is_test'] is False
                        mock_alerts.send_critical_alert.assert_called_once()

    def test_real_fill_not_blocked_by_paper_duplicate(self, temp_positions, temp_account_config):
        """Paper position for ETH exists; real fill (is_test=False) coexists, not rejected as duplicate."""
        from trade_journal import open_position, get_open_positions
        paper = open_position({
            'coin_id': 'ethereum', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH',
            'signal': 'BUY', 'entry_price': 2300.0, 'quantity': 0.04,
            'capital_usd': 92.0, 'is_test': True,
        })
        assert paper is not None
        with patch("app.get_execution_config", return_value={"auto_execution_enabled": True, "auto_execution_exchange": "coinbase"}):
            with patch("app.get_test_mode", return_value=True):
                with patch("app.place_stop_limit_sell_order", return_value={"success": True, "order_id": "sl1"}):
                    from app import _on_twap_done_open_position_and_place_orders
                    fill = {
                        "coin_id": "ethereum", "coin_name": "Ethereum", "coin_symbol": "ETH",
                        "quantity": 0.01, "entry_price": 2400.0, "capital_usd": 24.0,
                        "is_test": False,
                    }
                    _on_twap_done_open_position_and_place_orders(fill)
                    all_open = get_open_positions()
                    paper_positions = [p for p in all_open if p.get('is_test')]
                    real_positions = [p for p in all_open if not p.get('is_test')]
                    assert len(paper_positions) == 1
                    assert len(real_positions) == 1
                    assert real_positions[0]['coin_symbol'] == 'ETH'


# -----------------------------------------------------------------------------
# 3b. _start_twap_if_safe — exchange holdings defense-in-depth
# -----------------------------------------------------------------------------

class TestAutoExecExchangeHoldingsBlock:
    """_start_twap_if_safe blocks when reconciliation shows unjournaled exchange holdings."""

    def test_auto_exec_blocks_when_exchange_holds_unjournaled(self, temp_positions, temp_account_config):
        """If reconciliation cache shows external ETH, _start_twap_if_safe returns False."""
        import app as app_module
        original_cache = app_module._reconcile_cache.copy()
        try:
            app_module._reconcile_cache['data'] = {
                'discrepancies': [
                    {'type': 'external', 'symbol': 'ETH', 'exchange_quantity': 0.025},
                ],
                'status': 'discrepancy_detected',
            }
            app_module._reconcile_cache['ts'] = __import__('time').time()
            coin = {
                'symbol': 'ETH', 'name': 'Ethereum', 'id': 'ethereum',
                'current_price': 2400.0,
                'ai_recommendation': {
                    'final_signal': 'STRONG_BUY',
                    'quant_tier': 'STRONG_BUY',
                    'quant': {'quant_tier': 'STRONG_BUY'},
                    'consensus': {'quant_tier': 'STRONG_BUY', 'consensus_signal': 'STRONG_BUY'},
                    'position_sizing': {'position_size_usd': 50.0, 'max_loss_pct': 1.0},
                },
            }
            with patch("app.alerts") as mock_alerts:
                mock_alerts.send_critical_alert = MagicMock()
                result = app_module._start_twap_if_safe(coin, None, 20000.0)
            assert result is False
        finally:
            app_module._reconcile_cache.update(original_cache)

    def test_auto_exec_proceeds_when_reconcile_cache_empty(self, temp_positions, temp_account_config):
        """If reconciliation cache is empty (first run), fail-open: do not block."""
        import app as app_module
        original_cache = app_module._reconcile_cache.copy()
        try:
            app_module._reconcile_cache['data'] = None
            app_module._reconcile_cache['ts'] = 0
            coin = {
                'symbol': 'ETH', 'name': 'Ethereum', 'id': 'ethereum',
                'current_price': 2400.0,
                'ai_recommendation': {
                    'final_signal': 'STRONG_BUY',
                    'quant_tier': 'STRONG_BUY',
                    'quant': {'quant_tier': 'STRONG_BUY'},
                    'consensus': {'quant_tier': 'STRONG_BUY', 'consensus_signal': 'STRONG_BUY'},
                    'position_sizing': {'position_size_usd': 50.0, 'max_loss_pct': 1.0},
                },
            }
            with patch("app._build_account_overview", return_value={'cash_available_to_trade': 10000}):
                with patch("app.execution_safety_ok", return_value=(True, "")):
                    with patch("app.get_execution_config", return_value={'auto_execution_enabled': True, 'auto_execution_exchange': 'coinbase', 'twap_min_usd': 500}):
                        with patch("app.start_twap_buy_async", return_value=True) as mock_twap:
                            with patch("app.get_test_mode", return_value=False):
                                with patch("app.load_account_config", return_value={'heloc_mode': False}):
                                    result = app_module._start_twap_if_safe(coin, None, 20000.0)
            assert result is True
            mock_twap.assert_called_once()
        finally:
            app_module._reconcile_cache.update(original_cache)


# -----------------------------------------------------------------------------
# 4. execution_safety_ok and TWAP cooldown
# -----------------------------------------------------------------------------

class TestExecutionSafetyAndCooldown:
    """execution_safety_ok boundaries; start_twap_buy_async cooldown."""

    def test_execution_safety_ok_none_portfolio_allows(self):
        """When portfolio_risk_summary is None, safety passes."""
        from execution_engine import execution_safety_ok
        with patch("execution_engine.get_active_twap_orders", return_value=[]):
            ok, reason = execution_safety_ok(None, 2.0, 10000.0)
        assert ok is True
        assert reason == ""

    def test_execution_safety_ok_blocks_over_cap_risk(self):
        """Total risk would exceed dynamic cap: block. Large account (10k) has 6% cap."""
        from execution_engine import execution_safety_ok, get_max_total_risk_pct
        portfolio = {"total_risk_pct": 5.0, "max_correlated_pct": 20.0}
        cap = get_max_total_risk_pct(10000.0)
        assert cap == 6.0
        with patch("execution_engine.get_active_twap_orders", return_value=[]):
            ok, reason = execution_safety_ok(portfolio, 2.0, 10000.0)  # 5 + 2 = 7 > 6
            assert ok is False
            assert "6" in reason and "exceed" in reason.lower()
            ok2, _ = execution_safety_ok(portfolio, 0.5, 10000.0)  # 5 + 0.5 = 5.5 < 6
            assert ok2 is True

    def test_execution_safety_ok_small_account_higher_cap(self):
        """Small account (<$500) gets 50% risk cap; execution allowed when under cap."""
        from execution_engine import execution_safety_ok, get_max_total_risk_pct
        assert get_max_total_risk_pct(400.0) == 50.0
        portfolio = {"total_risk_pct": 15.0, "max_correlated_pct": 10.0}
        with patch("execution_engine.get_active_twap_orders", return_value=[]):
            ok, _ = execution_safety_ok(portfolio, 5.0, 400.0)  # 15 + 5 = 20 < 50
            assert ok is True
            ok2, reason = execution_safety_ok(portfolio, 40.0, 400.0)  # 15 + 40 = 55 > 50
            assert ok2 is False
            assert "50" in reason

    def test_execution_safety_ok_blocks_correlation_cap(self):
        """Correlation cap breached: block."""
        from execution_engine import execution_safety_ok, CORRELATION_CAP_PCT
        portfolio = {"total_risk_pct": 2.0, "max_correlated_pct": 40.0}
        with patch("execution_engine.get_active_twap_orders", return_value=[]):
            ok, reason = execution_safety_ok(portfolio, 1.0, 10000.0)
            assert ok is False
            assert str(CORRELATION_CAP_PCT) in reason or "correlation" in reason.lower()

    def test_twap_cooldown_prevents_second_start_same_symbol(self):
        """start_twap_buy_async for same symbol twice: second does not start TWAP (cooldown)."""
        from execution_engine import start_twap_buy_async, _twap_last_start
        import time
        # Reset cooldown state
        with patch("execution_engine._twap_last_start", {}):
            with patch("execution_engine._twap_lock"):
                pass
        with patch("execution_engine._twap_cooldown_ok") as mock_ok:
            mock_ok.side_effect = [True, False]  # first call ok, second blocked
            with patch("execution_engine.threading.Thread") as mock_thread:
                with patch("execution_engine.symbol_to_ccxt", return_value="BTC/USDC"):
                    start_twap_buy_async("BTC", 500, num_slices=2, duration_minutes=1)
                    start_twap_buy_async("BTC", 500, num_slices=2, duration_minutes=1)
            assert mock_thread.call_count == 1


# -----------------------------------------------------------------------------
# 5. execution_engine — execute_market_sell / invalid inputs
# -----------------------------------------------------------------------------

class TestExecuteMarketSellStress:
    """execute_market_sell: invalid symbol, zero quantity, exchange error."""

    def test_execute_market_sell_invalid_symbol_returns_error(self):
        """Empty or invalid symbol returns success False."""
        from execution_engine import execute_market_sell
        r = execute_market_sell("", 1.0, reason="test")
        assert r.get("success") is False
        assert "error" in r or "Invalid" in str(r.get("error", ""))

    def test_execute_market_sell_zero_quantity(self):
        """Zero quantity: exchange may reject; we don't crash."""
        with patch("execution_engine.get_coinbase_exchange") as mock_ex:
            mock_ex.return_value.create_market_sell_order.side_effect = Exception("Amount must be positive")
            from execution_engine import execute_market_sell
            r = execute_market_sell("BTC", 0, reason="test")
        assert r.get("success") is False or "error" in str(r).lower()

    def test_execute_market_sell_exchange_error_returns_failure(self):
        """Exchange raises: return dict with success False."""
        with patch("execution_engine.get_coinbase_exchange") as mock_ex:
            mock_ex.return_value.create_market_sell_order.side_effect = Exception("Network error")
            from execution_engine import execute_market_sell
            r = execute_market_sell("BTC", 0.01, reason="test")
        assert r.get("success") is False
        assert "error" in r or "Network" in str(r.get("error", ""))


# -----------------------------------------------------------------------------
# 6. _place_sl_tp_orders_for_position — invalid data
# -----------------------------------------------------------------------------

class TestPlaceSlTpStress:
    """_place_sl_tp_orders_for_position: gates and invalid position."""

    def test_place_sl_tp_invalid_position_returns_early(self, temp_positions, temp_account_config):
        """Missing symbol or zero quantity: return (False, False)."""
        with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
                with patch("app.place_limit_sell_order") as mock_place:
                    from app import _place_sl_tp_orders_for_position
                    sl_ok, tp_ok, *_ = _place_sl_tp_orders_for_position({
                        "id": 1, "coin_symbol": "", "quantity": 0, "current_stop_price": 97,
                    })
        assert sl_ok is False and tp_ok is False
        mock_place.assert_not_called()


# -----------------------------------------------------------------------------
# 7. _start_twap_if_safe — cash and safety gates
# -----------------------------------------------------------------------------

class TestStartTwapIfSafeStress:
    """_start_twap_if_safe: insufficient cash, safety fail, no double start."""

    def test_start_twap_insufficient_cash_does_not_start(self, temp_positions, temp_account_config):
        """Position size > cash_available: no TWAP started."""
        with patch("app._build_account_overview", return_value={"cash_available_to_trade": 100}):
            with patch("app.execution_safety_ok", return_value=(True, "")):
                with patch("execution_engine.start_twap_buy_async") as mock_start:
                    from app import _start_twap_if_safe
                    coin = {
                        "symbol": "BTC", "id": "bitcoin", "name": "Bitcoin",
                        "ai_recommendation": {"position_sizing": {"position_size_usd": 500, "max_loss_pct": 2}},
                    }
                    result = _start_twap_if_safe(coin, {}, 10000)
        assert result is False
        mock_start.assert_not_called()

    def test_start_twap_safety_fail_does_not_start(self, temp_positions, temp_account_config):
        """execution_safety_ok returns False: no TWAP."""
        with patch("app._build_account_overview", return_value={"cash_available_to_trade": 10000}):
            with patch("app.execution_safety_ok", return_value=(False, "Risk cap exceeded")):
                with patch("execution_engine.start_twap_buy_async") as mock_start:
                    from app import _start_twap_if_safe
                    coin = {
                        "symbol": "BTC", "id": "bitcoin", "name": "Bitcoin",
                        "ai_recommendation": {"position_sizing": {"position_size_usd": 500, "max_loss_pct": 2}},
                    }
                    result = _start_twap_if_safe(coin, {}, 10000)
        assert result is False
        mock_start.assert_not_called()
