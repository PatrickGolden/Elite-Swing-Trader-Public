# tests/execution/test_stop_reprice_safety.py
# Regression tests for the stop-loss reprice safety fixes:
#   1) place_stop_limit_sell_order MUST NOT recurse into the USD pair on
#      INSUFFICIENT_FUND — same base balance, retry can never succeed.
#   2) cancel_and_wait MUST poll fetch_order_status until the order reaches
#      a terminal state before returning success, so the exchange has time
#      to release reserved balance.
# 100% mocked: no real CCXT.

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))

import execution_engine


# -----------------------------------------------------------------------------
# place_stop_limit_sell_order: no USD-pair fallback on INSUFFICIENT_FUND
# -----------------------------------------------------------------------------

def test_place_stop_limit_sell_no_usd_fallback_on_insufficient_fund():
    mock_ex = MagicMock()
    # Ticker is healthy so we get past the preflight.
    mock_ex.fetch_ticker.return_value = {"last": 2100.0}
    # create_order always raises Coinbase's INSUFFICIENT_FUND payload.
    mock_ex.create_order.side_effect = Exception(
        'coinbase {"success":false,"error_response":'
        '{"error":"INSUFFICIENT_FUND","preview_failure_reason":"PREVIEW_INSUFFICIENT_FUND"}}'
    )
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.place_stop_limit_sell_order(
            symbol="ETH",
            quantity=0.01026794,
            stop_price=2016.73,
        )
    assert result["success"] is False
    assert "INSUFFICIENT_FUND" in result["error"]
    # Critical: only one create_order call. The previous bug recursed onto ETH/USD.
    assert mock_ex.create_order.call_count == 1


def test_place_stop_limit_sell_still_falls_back_on_invalid_product():
    """Genuine pair-availability errors should still trigger USDC→USD fallback."""
    mock_ex = MagicMock()
    mock_ex.fetch_ticker.return_value = {"last": 2100.0}
    mock_ex.create_order.side_effect = Exception("coinbase INVALID_PRODUCT_ID for ETH/USDC")
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.place_stop_limit_sell_order(
            symbol="ETH",
            quantity=0.01026794,
            stop_price=2016.73,
        )
    # Both attempts fail, but the fallback to USD pair was attempted.
    assert result["success"] is False
    assert mock_ex.create_order.call_count == 2


def test_place_stop_limit_sell_deferred_on_auction_mode_no_usd_fallback():
    mock_ex = MagicMock()
    mock_ex.fetch_ticker.return_value = {"last": 2100.0}
    mock_ex.create_order.side_effect = Exception(
        'coinbase {"error":"INVALID_ARGUMENT","error_details":"Orderbook is in auction mode - please use limit order type"}'
    )
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.place_stop_limit_sell_order(
            symbol="ETH",
            quantity=0.01026794,
            stop_price=2016.73,
        )
    assert result["success"] is False
    assert result.get("status") == "deferred"
    assert result.get("reason") == "auction_mode"
    assert result.get("retry_after_sec") == 90
    assert mock_ex.create_order.call_count == 1


def test_place_stop_limit_sell_deferred_on_auction_mode_without_invalid_argument():
    mock_ex = MagicMock()
    mock_ex.fetch_ticker.return_value = {"last": 2100.0}
    mock_ex.create_order.side_effect = Exception(
        'coinbase {"error_details":"Orderbook is in auction mode - please use limit order type"}'
    )
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.place_stop_limit_sell_order(
            symbol="ETH",
            quantity=0.01026794,
            stop_price=2016.73,
        )
    assert result["success"] is False
    assert result.get("status") == "deferred"
    assert result.get("reason") == "auction_mode"
    assert mock_ex.create_order.call_count == 1


def test_place_limit_sell_no_usd_fallback_on_insufficient_fund():
    mock_ex = MagicMock()
    mock_ex.create_limit_sell_order.side_effect = Exception(
        'coinbase {"error":"INSUFFICIENT_FUND"}'
    )
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.place_limit_sell_order(
            symbol="ETH",
            quantity=0.01026794,
            price=2300.0,
        )
    assert result["success"] is False
    assert mock_ex.create_limit_sell_order.call_count == 1


# -----------------------------------------------------------------------------
# cancel_and_wait: polls until terminal status
# -----------------------------------------------------------------------------

def test_cancel_and_wait_polls_until_canceled():
    mock_ex = MagicMock()
    # cancel returns immediately, but the order is briefly still 'open' before
    # the exchange records it as 'canceled'.
    mock_ex.cancel_order.return_value = None
    statuses = iter([
        {"status": "open"},
        {"status": "open"},
        {"status": "canceled"},
    ])
    mock_ex.fetch_order.side_effect = lambda oid: next(statuses)
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.cancel_and_wait(
            "test-order-id",
            timeout_s=1.0,
            poll_interval_s=0.01,
        )
    assert result["success"] is True
    assert result["status"] == "canceled"
    assert mock_ex.fetch_order.call_count == 3


def test_cancel_and_wait_times_out_when_status_never_terminal():
    mock_ex = MagicMock()
    mock_ex.cancel_order.return_value = None
    mock_ex.fetch_order.return_value = {"status": "open"}
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.cancel_and_wait(
            "test-order-id",
            timeout_s=0.1,
            poll_interval_s=0.02,
        )
    assert result["success"] is False
    assert "timeout" in result["error"].lower()


def test_cancel_and_wait_returns_success_if_order_already_filled():
    """If the order filled before our cancel landed, that's a terminal state too."""
    mock_ex = MagicMock()
    mock_ex.cancel_order.return_value = None
    mock_ex.fetch_order.return_value = {"status": "closed"}  # closed → 'filled'
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ex):
        result = execution_engine.cancel_and_wait(
            "test-order-id",
            timeout_s=1.0,
            poll_interval_s=0.01,
        )
    assert result["success"] is True
    assert result["status"] == "filled"
