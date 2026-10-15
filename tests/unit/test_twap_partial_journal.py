"""Test that partial TWAP fills are journaled."""
from unittest.mock import patch, MagicMock


def test_partial_twap_journals_filled_quantity():
    """When TWAP partially fills, the callback should be invoked with actual filled quantity."""
    callback_data = {}

    def mock_callback(fill_data):
        callback_data.update(fill_data)

    def mock_place_buy(exchange, symbol, usd):
        """First slice succeeds, second fails."""
        if not hasattr(mock_place_buy, "_call_count"):
            mock_place_buy._call_count = 0
        mock_place_buy._call_count += 1
        if mock_place_buy._call_count == 1:
            return {
                "status": "closed",
                "filled_amount": 0.5,
                "average_price": 100.0,
                "fee_usd": 0.25,
                "order_id": "order-1",
            }
        return {"status": "error", "error": "network timeout", "filled_amount": 0, "average_price": 0, "fee_usd": 0}

    import execution_engine as ee
    original_callback = ee._on_twap_done_open_position_and_place_orders

    try:
        ee._on_twap_done_open_position_and_place_orders = mock_callback

        with patch.object(ee, "get_coinbase_exchange", return_value=MagicMock()), \
             patch.object(ee, "place_market_buy_usd", side_effect=mock_place_buy), \
             patch.object(ee, "log_execution"), \
             patch.object(ee, "_unregister_active_twap"), \
             patch("time.sleep"):
            ee.run_twap_buy_coinbase(
                symbol_ccxt="BTC/USD",
                total_usd=100.0,
                num_slices=2,
                duration_minutes=10,
                coin_name="Bitcoin",
                coin_id="bitcoin",
                stop_pct=3.0,
                target_pct=6.0,
            )

        assert "coin_symbol" in callback_data, "Callback must be invoked for partial fills"
        assert callback_data["quantity"] == 0.5, "Should journal actual filled quantity"
        assert callback_data["entry_price"] == 100.0
    finally:
        ee._on_twap_done_open_position_and_place_orders = original_callback
