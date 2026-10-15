"""Tests for execution_engine.py — risk tiers, fee extraction, slippage,
symbol conversion, PEM cleaning, logging, cancel/fetch, safety checks, TWAP routing."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import execution_engine as ee


# =============================================================================
# TestRiskTiers — get_max_total_risk_pct
# =============================================================================

class TestRiskTiers:
    """Account-size-based risk cap tiers."""

    def test_tiny_account_below_500(self):
        assert ee.get_max_total_risk_pct(250) == 50.0

    def test_small_account_below_2000(self):
        assert ee.get_max_total_risk_pct(1500) == 40.0

    def test_medium_account_below_5000(self):
        assert ee.get_max_total_risk_pct(3000) == 25.0

    def test_medium_large_below_10000(self):
        assert ee.get_max_total_risk_pct(8000) == 15.0

    def test_large_account_at_10000(self):
        assert ee.get_max_total_risk_pct(10000) == 6.0

    def test_large_account_above_10000(self):
        assert ee.get_max_total_risk_pct(50000) == 6.0

    def test_boundary_at_500(self):
        # 500 is NOT < 500, so it falls to next tier
        assert ee.get_max_total_risk_pct(500) == 40.0

    def test_zero_account(self):
        assert ee.get_max_total_risk_pct(0) == 6.0

    def test_negative_account(self):
        assert ee.get_max_total_risk_pct(-100) == 6.0


# =============================================================================
# TestFeeExtraction — _extract_fee
# =============================================================================

class TestFeeExtraction:
    """Extract fee info from CCXT order dicts."""

    def test_normal_fee_dict(self):
        order = {"fee": {"cost": 1.25, "currency": "USD", "rate": 0.004}}
        result = ee._extract_fee(order)
        assert result["fee_usd"] == 1.25
        assert result["fee_rate"] == 0.004
        assert result["fee_currency"] == "USD"

    def test_missing_fee_key(self):
        order = {}
        result = ee._extract_fee(order)
        assert result["fee_usd"] == 0.0
        assert result["fee_currency"] == ""

    def test_fee_none(self):
        order = {"fee": None}
        result = ee._extract_fee(order)
        assert result["fee_usd"] == 0.0

    def test_fees_list_fallback(self):
        order = {
            "fee": {"cost": 0},
            "fees": [
                {"cost": 0.5, "currency": "USDC"},
                {"cost": 0.3, "currency": "USDC"},
            ],
        }
        result = ee._extract_fee(order)
        assert result["fee_usd"] == pytest.approx(0.8, abs=1e-6)
        assert result["fee_currency"] == "USDC"

    def test_zero_cost_no_fees_list(self):
        order = {"fee": {"cost": 0, "currency": "USD", "rate": 0}}
        result = ee._extract_fee(order)
        assert result["fee_usd"] == 0.0


# =============================================================================
# TestSlippage — _compute_slippage_bps
# =============================================================================

class TestSlippage:
    """Slippage calculation in basis points."""

    def test_positive_slippage(self):
        # Paid 1% more: 10100 vs 10000 = +100 bps
        result = ee._compute_slippage_bps(10100, 10000)
        assert result == 100.0

    def test_negative_slippage(self):
        # Got a better price: 9900 vs 10000 = -100 bps
        result = ee._compute_slippage_bps(9900, 10000)
        assert result == -100.0

    def test_zero_fill_price(self):
        assert ee._compute_slippage_bps(0, 10000) is None

    def test_zero_reference_price(self):
        assert ee._compute_slippage_bps(10000, 0) is None

    def test_none_fill_price(self):
        assert ee._compute_slippage_bps(None, 10000) is None

    def test_none_reference_price(self):
        assert ee._compute_slippage_bps(10000, None) is None

    def test_no_slippage(self):
        assert ee._compute_slippage_bps(50000, 50000) == 0.0


# =============================================================================
# TestSymbolConversion — symbol_to_ccxt
# =============================================================================

class TestSymbolConversion:
    """Convert bare symbols to CCXT pairs."""

    def test_bare_symbol_usdc(self):
        assert ee.symbol_to_ccxt("BTC") == "BTC/USDC"

    def test_bare_symbol_usd(self):
        assert ee.symbol_to_ccxt("ETH", prefer_usdc=False) == "ETH/USD"

    def test_already_has_slash(self):
        assert ee.symbol_to_ccxt("BTC/USDC") == "BTC/USDC"

    def test_empty_string(self):
        assert ee.symbol_to_ccxt("") == ""

    def test_none_input(self):
        assert ee.symbol_to_ccxt(None) == ""

    def test_lowercase_uppercased(self):
        assert ee.symbol_to_ccxt("sol") == "SOL/USDC"


# =============================================================================
# TestCleanCoinbaseSecret — _clean_coinbase_secret
# =============================================================================

class TestCleanCoinbaseSecret:
    """PEM key cleaning for Coinbase CCXT."""

    def test_already_clean(self):
        clean = "-----BEGIN EC PRIVATE KEY-----\nMIGkAgEBB\n-----END EC PRIVATE KEY-----\n"  # gitleaks:allow (dummy fixture)
        result = ee._clean_coinbase_secret(clean)
        assert result.startswith(ee.PEM_BEGIN)
        assert result.strip().endswith(ee.PEM_END)

    def test_escaped_newlines(self):
        raw = "-----BEGIN EC PRIVATE KEY-----\\nMIGkAgEBB\\n-----END EC PRIVATE KEY-----"  # gitleaks:allow (dummy fixture)
        result = ee._clean_coinbase_secret(raw)
        assert "\\n" not in result
        assert ee.PEM_BEGIN in result
        assert ee.PEM_END in result

    def test_missing_headers(self):
        raw = "MIGkAgEBBSomeBase64Data"  # gitleaks:allow (dummy fixture)
        result = ee._clean_coinbase_secret(raw)
        assert result.startswith(ee.PEM_BEGIN)
        assert ee.PEM_END in result

    def test_empty_string(self):
        assert ee._clean_coinbase_secret("") == ""

    def test_none_input(self):
        assert ee._clean_coinbase_secret(None) is None

    def test_whitespace_only(self):
        assert ee._clean_coinbase_secret("   ") == "   "


# =============================================================================
# TestLogExecution — log_execution / log_skipped_insufficient_cash
# =============================================================================

class TestLogExecution:
    """Verify JSONL append for execution and skipped-cash logs."""

    def test_log_execution_appends(self, tmp_path, monkeypatch):
        log_path = tmp_path / "exec.jsonl"
        log_path.touch()
        monkeypatch.setattr(ee, "EXECUTION_LOG_PATH", str(log_path))
        ee.log_execution({"action": "test", "val": 42})
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["action"] == "test"
        assert data["val"] == 42

    def test_log_execution_multiple(self, tmp_path, monkeypatch):
        log_path = tmp_path / "exec.jsonl"
        log_path.touch()
        monkeypatch.setattr(ee, "EXECUTION_LOG_PATH", str(log_path))
        ee.log_execution({"n": 1})
        ee.log_execution({"n": 2})
        lines = [l for l in log_path.read_text().strip().split("\n") if l]
        assert len(lines) == 2

    def test_log_skipped_insufficient_cash(self, tmp_path, monkeypatch):
        log_path = tmp_path / "skipped.jsonl"
        log_path.touch()
        monkeypatch.setattr(ee, "SKIPPED_INSUFFICIENT_CASH_LOG", str(log_path))
        ee.log_skipped_insufficient_cash(
            coin_id="bitcoin",
            coin_name="Bitcoin",
            symbol="BTC",
            position_usd=500.0,
            cash_available=100.0,
            consensus_signal="BUY",
        )
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["reason"] == "insufficient_cash"
        assert data["symbol"] == "BTC"
        assert data["cash_available"] == 100.0


# =============================================================================
# TestCancelOrder — cancel_order
# =============================================================================

class TestCancelOrder:
    """Cancel exchange orders via mocked CCXT."""

    @patch("execution_engine._log_audit")
    def test_success(self, _audit, mock_get_coinbase_exchange):
        mock_get_coinbase_exchange.cancel_order.return_value = None
        result = ee.cancel_order("order-123")
        assert result["success"] is True
        mock_get_coinbase_exchange.cancel_order.assert_called_once_with("order-123")

    @patch("execution_engine._log_audit")
    def test_exchange_error(self, _audit, mock_get_coinbase_exchange):
        mock_get_coinbase_exchange.cancel_order.side_effect = Exception("Not found")
        result = ee.cancel_order("order-999")
        assert result["success"] is False
        assert "Not found" in result["error"]

    @patch("execution_engine._log_audit")
    def test_empty_order_id(self, _audit):
        result = ee.cancel_order("")
        assert result["success"] is False
        assert "Invalid" in result["error"]

    @patch("execution_engine._log_audit")
    def test_none_order_id(self, _audit):
        result = ee.cancel_order(None)
        assert result["success"] is False


# =============================================================================
# TestFetchOrderStatus — fetch_order_status
# =============================================================================

class TestFetchOrderStatus:
    """Map CCXT order statuses to normalized strings."""

    @patch("execution_engine._log_audit")
    def test_closed_maps_to_filled(self, _audit, mock_get_coinbase_exchange):
        mock_get_coinbase_exchange.fetch_order.return_value = {"status": "closed"}
        assert ee.fetch_order_status("ord-1") == "filled"

    @patch("execution_engine._log_audit")
    def test_filled_maps_to_filled(self, _audit, mock_get_coinbase_exchange):
        mock_get_coinbase_exchange.fetch_order.return_value = {"status": "filled"}
        assert ee.fetch_order_status("ord-2") == "filled"

    @patch("execution_engine._log_audit")
    def test_cancelled_maps_to_canceled(self, _audit, mock_get_coinbase_exchange):
        mock_get_coinbase_exchange.fetch_order.return_value = {"status": "cancelled"}
        assert ee.fetch_order_status("ord-3") == "canceled"

    @patch("execution_engine._log_audit")
    def test_open_remains_open(self, _audit, mock_get_coinbase_exchange):
        mock_get_coinbase_exchange.fetch_order.return_value = {"status": "open"}
        assert ee.fetch_order_status("ord-4") == "open"

    def test_empty_order_id_returns_none(self):
        assert ee.fetch_order_status("") is None

    def test_none_order_id_returns_none(self):
        assert ee.fetch_order_status(None) is None

    @patch("execution_engine._log_audit")
    def test_exchange_error_returns_none(self, _audit, mock_get_coinbase_exchange):
        mock_get_coinbase_exchange.fetch_order.side_effect = Exception("timeout")
        assert ee.fetch_order_status("ord-err") is None


# =============================================================================
# TestExecutionSafetyOk — execution_safety_ok
# =============================================================================

class TestExecutionSafetyOk:
    """Portfolio risk gate before auto-execution."""

    @patch("execution_engine.get_active_twap_orders", return_value=[])
    def test_under_cap_ok(self, _twap):
        summary = {"total_risk_pct": 3.0, "max_correlated_pct": 20.0}
        ok, reason = ee.execution_safety_ok(summary, 2.0, 50000)
        assert ok is True
        assert reason == ""

    @patch("execution_engine.get_active_twap_orders", return_value=[])
    def test_over_cap_blocked(self, _twap):
        summary = {"total_risk_pct": 5.0, "max_correlated_pct": 20.0}
        ok, reason = ee.execution_safety_ok(summary, 2.0, 50000)
        assert ok is False
        assert "exceed" in reason.lower()

    @patch("execution_engine.get_active_twap_orders", return_value=[])
    def test_correlation_breached(self, _twap):
        summary = {"total_risk_pct": 1.0, "max_correlated_pct": 40.0}
        ok, reason = ee.execution_safety_ok(summary, 1.0, 50000)
        assert ok is False
        assert "correlation" in reason.lower()

    @patch("execution_engine.get_active_twap_orders", return_value=[{"symbol": "BTC/USDC"}])
    def test_active_twap_blocks(self, _twap):
        summary = {"total_risk_pct": 1.0, "max_correlated_pct": 10.0}
        ok, reason = ee.execution_safety_ok(summary, 1.0, 50000)
        assert ok is False
        assert "TWAP" in reason

    @patch("execution_engine.get_active_twap_orders", return_value=[])
    def test_none_summary_passes(self, _twap):
        ok, reason = ee.execution_safety_ok(None, 5.0, 50000)
        assert ok is True

    @patch("execution_engine.get_active_twap_orders", return_value=[])
    def test_small_account_higher_cap(self, _twap):
        # $1000 account -> 40% cap, so 35% total + 4% new = 39% should pass
        summary = {"total_risk_pct": 35.0, "max_correlated_pct": 10.0}
        ok, reason = ee.execution_safety_ok(summary, 4.0, 1000)
        assert ok is True

    @patch("execution_engine.get_active_twap_orders", return_value=[])
    def test_small_account_still_blocked_if_over(self, _twap):
        summary = {"total_risk_pct": 38.0, "max_correlated_pct": 10.0}
        ok, reason = ee.execution_safety_ok(summary, 5.0, 1000)
        assert ok is False


# =============================================================================
# TestExecuteMarketSellTwap — routing between single sell and TWAP
# =============================================================================

class TestExecuteMarketSellTwap:
    """TWAP vs single market sell routing based on position size."""

    @patch("execution_engine._log_audit")
    @patch("execution_engine.execute_market_sell")
    def test_small_position_uses_single_sell(self, mock_sell, _audit):
        mock_sell.return_value = {
            "success": True,
            "fill_price": 100.0,
            "average_price": 100.0,
            "filled_amount": 1.0,
            "fee_usd": 0.5,
        }
        result = ee.execute_market_sell_twap(
            symbol="SOL",
            quantity=1.0,
            position_usd=50.0,
            twap_min_usd=100.0,
        )
        assert result["mode"] == "market"
        assert result["success"] is True
        mock_sell.assert_called_once()

    @patch("execution_engine._log_audit")
    @patch("execution_engine.run_twap_sell_coinbase")
    def test_large_position_uses_twap(self, mock_twap, _audit):
        mock_twap.return_value = {
            "success": True,
            "fill_price": 100.0,
            "average_price": 100.0,
            "total_filled": 5.0,
            "total_fee_usd": 1.0,
        }
        result = ee.execute_market_sell_twap(
            symbol="SOL",
            quantity=5.0,
            position_usd=500.0,
            twap_min_usd=100.0,
        )
        assert result["mode"] == "twap"
        assert result["success"] is True
        mock_twap.assert_called_once()

    def test_invalid_symbol_returns_error(self):
        result = ee.execute_market_sell_twap(
            symbol="",
            quantity=1.0,
            position_usd=500.0,
            twap_min_usd=100.0,
        )
        # Empty symbol with position_usd >= twap_min_usd goes to TWAP path, which checks symbol
        assert result["success"] is False


class TestAuctionModeTelemetry:
    """Auction mode deferred stop placement must emit telemetry for roadmap rollback tracking."""

    @patch("execution_engine.log_stop_telemetry")
    @patch("execution_engine.log_execution")
    @patch("execution_engine._log_audit")
    @patch("execution_engine.get_coinbase_exchange")
    @patch("execution_engine.symbol_to_ccxt", return_value="BTC/USDC")
    def test_auction_mode_emits_failure_telemetry(self, _sym, mock_get_ex, _audit, _log_exec, mock_telemetry):
        mock_ex = MagicMock()
        mock_ex.fetch_balance.return_value = {"BTC": {"free": 0.5}}
        mock_ex.fetch_ticker.return_value = {"last": 65000.0}
        mock_ex.create_order.side_effect = Exception(
            "INVALID_ARGUMENT: Orderbook is in auction mode - please use limit order type"
        )
        mock_get_ex.return_value = mock_ex
        result = ee.place_stop_limit_sell_order("BTC", 0.01, 60000.0, 59400.0)
        assert result["success"] is False
        assert result["reason"] == "auction_mode"
        mock_telemetry.assert_called_once_with(
            'place', 'BTC/USDC', intended_stop=60000.0, actual_stop=0.0,
            success=False, details={'error': 'auction_mode', 'reason': 'deferred_retry'},
        )
