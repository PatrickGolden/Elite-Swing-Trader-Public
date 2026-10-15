from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))


def test_sl_auction_mode_sets_retry_metadata(temp_positions):
    from app import _place_sl_tp_orders_for_position
    from trade_journal import open_position, get_position_by_id

    pos = open_position({
        "coin_id": "ethereum",
        "coin_name": "Ethereum",
        "coin_symbol": "ETH",
        "signal": "BUY",
        "entry_price": 2400.0,
        "quantity": 0.01,
        "ai_target_pct": 6.0,
        "ai_stop_pct": 2.5,
        "current_stop_price": 2340.0,
    })

    with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
        with patch("app.random.uniform", return_value=1.0):
            with patch("app.place_stop_limit_sell_order", return_value={
                "success": False,
                "status": "deferred",
                "reason": "auction_mode",
                "retry_after_sec": 90,
                "error": "auction mode",
            }):
                sl_ok, tp_ok, sl_err, *_ = _place_sl_tp_orders_for_position(pos)

    updated = get_position_by_id(pos["id"])
    assert sl_ok is False
    assert tp_ok is False
    assert sl_err == "auction mode"
    assert updated.get("sl_retry_reason") == "auction_mode"
    assert int(updated.get("sl_retry_count") or 0) == 1
    assert int(updated.get("sl_retry_after_sec") or 0) == 90
    assert updated.get("sl_retry_exhausted_alerted") in (False, None)
    assert updated.get("sl_retry_next_at")


def test_check_status_retries_deferred_sl_when_due(temp_positions):
    from app import _check_sl_tp_order_status
    from trade_journal import open_position, update_position, get_position_by_id

    pos = open_position({
        "coin_id": "ethereum",
        "coin_name": "Ethereum",
        "coin_symbol": "ETH",
        "signal": "BUY",
        "entry_price": 2400.0,
        "quantity": 0.01,
        "ai_target_pct": 6.0,
        "ai_stop_pct": 2.5,
        "current_stop_price": 2340.0,
    })
    update_position(pos["id"], {
        "sl_order_id": None,
        "tp_order_id": None,
        "sl_retry_reason": "auction_mode",
        "sl_retry_count": 1,
        "sl_retry_next_at": "2000-01-01T00:00:00Z",
    })

    with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
        with patch("app.place_stop_limit_sell_order", return_value={"success": True, "order_id": "sl-new-1"}):
            _check_sl_tp_order_status(current_prices={})

    updated = get_position_by_id(pos["id"])
    assert updated.get("sl_order_id") == "sl-new-1"
    assert updated.get("sl_retry_next_at") is None
    assert int(updated.get("sl_retry_count") or 0) == 0


def test_retry_exhaustion_alert_sent_once(temp_positions):
    from app import _check_sl_tp_order_status, SL_AUCTION_RETRY_MAX_ATTEMPTS
    from trade_journal import open_position, update_position, get_position_by_id

    pos = open_position({
        "coin_id": "ethereum",
        "coin_name": "Ethereum",
        "coin_symbol": "ETH",
        "signal": "BUY",
        "entry_price": 2400.0,
        "quantity": 0.01,
        "ai_target_pct": 6.0,
        "ai_stop_pct": 2.5,
        "current_stop_price": 2340.0,
    })
    update_position(pos["id"], {
        "sl_order_id": None,
        "tp_order_id": None,
        "sl_retry_reason": "auction_mode",
        "sl_retry_count": SL_AUCTION_RETRY_MAX_ATTEMPTS,
        "sl_retry_next_at": "2000-01-01T00:00:00Z",
        "sl_last_error": "auction mode",
        "sl_retry_exhausted_alerted": False,
    })

    with patch("app.get_execution_config", return_value={"auto_execution_exchange": "coinbase"}):
        with patch("app._alerts_ready", return_value=True):
            with patch("app.alerts.send_critical_alert") as mock_alert:
                _check_sl_tp_order_status(current_prices={})
                _check_sl_tp_order_status(current_prices={})

    updated = get_position_by_id(pos["id"])
    assert updated.get("sl_retry_exhausted_alerted") is True
    assert mock_alert.call_count == 1
