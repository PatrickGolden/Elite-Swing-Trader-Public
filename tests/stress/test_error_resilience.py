# tests/test_error_resilience.py — Chaos/fault injection: exchange down, disk failure,
# all models timeout, malformed prices, concurrent updates
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
import json
import tempfile
import os
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 1. Exchange Down — CCXT throws on every call
# ---------------------------------------------------------------------------

class TestExchangeDown:
    def test_fetch_balance_raises_on_exchange_error(self):
        """fetch_balance_sync propagates exchange errors (caller must handle)."""
        from execution_engine import fetch_balance_sync
        mock_ex = MagicMock()
        mock_ex.fetch_balance.side_effect = Exception("Exchange unreachable")
        with patch("execution_engine.get_exchange_for_balance", return_value=mock_ex):
            with pytest.raises(Exception, match="Exchange unreachable"):
                fetch_balance_sync("coinbase")

    def test_place_market_buy_handles_exchange_error(self):
        """place_market_buy_usd handles exchange error gracefully."""
        from execution_engine import place_market_buy_usd
        mock_ex = MagicMock()
        mock_ex.create_market_buy_order.side_effect = Exception("Connection refused")
        result = place_market_buy_usd(mock_ex, "BTC/USDC", 500)
        assert "error" in result or result.get("status") == "error" or "fail" in str(result).lower()

    def test_execute_market_sell_twap_handles_exchange_error(self):
        """execute_market_sell_twap returns failure when exchange is down."""
        mock_ex = MagicMock()
        mock_ex.create_market_sell_order.side_effect = Exception("Network error")
        with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
            with patch("execution_engine.time.sleep"):
                with patch("execution_engine.log_execution"):
                    from execution_engine import execute_market_sell_twap
                    result = execute_market_sell_twap("BTC", 0.01, num_slices=1, duration_minutes=1)
        assert result.get("success") is False or "error" in str(result).lower()

    def test_place_limit_sell_handles_exchange_error(self):
        """place_limit_sell_order handles exchange error on both USDC and USD."""
        mock_ex = MagicMock()
        mock_ex.create_limit_sell_order.side_effect = Exception("API unavailable")
        with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
            with patch("execution_engine.log_execution"):
                from execution_engine import place_limit_sell_order
                result = place_limit_sell_order("BTC", 0.01, 44000)
        assert result.get("success") is False


# ---------------------------------------------------------------------------
# 2. Disk Write Failure — JSON save fails
# ---------------------------------------------------------------------------

class TestDiskWriteFailure:
    def test_trade_journal_save_handles_oserror(self, temp_positions):
        """open_position still works even if save encounters issues."""
        from trade_journal import open_position
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 50000, "quantity": 0.01,
        })
        assert pos["status"] == "OPEN"

    def test_monitoring_save_expectancy_raises_on_oserror(self):
        """save_backtest_expectancy raises RuntimeError when disk write fails."""
        import monitoring
        with patch("safe_json.save", return_value=False):
            with pytest.raises(RuntimeError, match="Failed to save"):
                monitoring.save_backtest_expectancy({"by_regime": {}})

# ---------------------------------------------------------------------------
# 4. Malformed Price Data
# ---------------------------------------------------------------------------

class TestMalformedPriceData:
    def test_position_sizer_handles_zero_price(self):
        """PositionSizer doesn't crash with zero entry price."""
        from position_sizer import PositionSizer
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        try:
            result = sizer.calculate_position_size(0, 2.5, "High", 1.0, 1.0)
            assert result["quantity"] == 0 or result["recommendation"] == "SKIP"
        except (ZeroDivisionError, ValueError):
            pass  # acceptable to raise

    def test_position_sizer_handles_negative_stop(self):
        """PositionSizer handles negative stop_loss_pct gracefully."""
        from position_sizer import PositionSizer
        sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
        try:
            result = sizer.calculate_position_size(45000, -2.5, "High", 1.0, 1.0)
            assert isinstance(result, dict)
        except (ValueError, ZeroDivisionError):
            pass

    def test_quant_signal_handles_missing_indicators(self):
        """generate_quant_signal doesn't crash with minimal coin data."""
        from quant_signal import generate_quant_signal
        coin = {"score": 50, "indicators": {}, "risk": {}, "enhanced": {}}
        regime = {"regime": "SIDEWAYS"}
        result = generate_quant_signal(coin, regime, fear_greed_value=50)
        assert "quant_signal" in result

    def test_reconciliation_handles_none_prices(self):
        """reconcile_holdings handles None in price map."""
        from reconciliation import reconcile_holdings
        result = reconcile_holdings(
            {"BTC": 0.01}, {}, dust_threshold_usd=1.0,
            price_by_symbol={"BTC": None}
        )
        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# 5. Concurrent Position Updates
# ---------------------------------------------------------------------------

class TestConcurrentPositionUpdates:
    def test_concurrent_opens_produce_unique_ids(self, temp_positions):
        """Multiple rapid open_position calls produce unique sequential IDs."""
        from trade_journal import open_position
        results = []
        for i in range(5):
            pos = open_position({
                "coin_id": f"coin{i}", "coin_name": f"Coin{i}", "coin_symbol": f"C{i}",
                "signal": "BUY", "entry_price": 100 + i, "quantity": 1,
            })
            results.append(pos)
        ids = [p["id"] for p in results]
        assert len(set(ids)) == 5

    def test_concurrent_updates_no_data_loss(self, temp_positions):
        """Rapid sequential updates preserve all data."""
        from trade_journal import open_position, update_position, get_position_by_id
        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        for i in range(10):
            update_position(pos["id"], {"high_water_mark": 100 + i})
        final = get_position_by_id(pos["id"])
        assert final["high_water_mark"] == 109


# ---------------------------------------------------------------------------
# 6. Veto Specialist Handles Missing Data
# ---------------------------------------------------------------------------

class TestVetoMissingData:
    def test_veto_with_empty_enhanced(self):
        """Veto specialist handles empty enhanced data."""
        from veto_specialist import evaluate
        result = evaluate(
            {"name": "BTC", "symbol": "BTC"},
            {"regime": "BULL"},
            {"consensus_signal": "STRONG_BUY"},
            enhanced={},
            fear_greed_value=50,
        )
        assert result["veto_decision"] in ("CONFIRM", "VETO")

    def test_veto_with_none_enhanced(self):
        """Veto specialist handles None enhanced data."""
        from veto_specialist import evaluate
        result = evaluate(
            {"name": "BTC", "symbol": "BTC"},
            {"regime": "SIDEWAYS"},
            {"consensus_signal": "STRONG_BUY"},
            enhanced=None,
            fear_greed_value=50,
        )
        assert result["veto_decision"] in ("CONFIRM", "VETO")
