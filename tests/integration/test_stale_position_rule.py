# tests/test_stale_position_rule.py — 14-day sell rule and dashboard toggle (stale_position_rule_enabled)
# Run: pytest tests/test_stale_position_rule.py -v

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestCheckStalePositionsToggle:
    """check_stale_positions respects stale_position_rule_enabled from execution config."""

    def test_stale_position_rule_disabled_returns_immediately(self):
        """When stale_position_rule_enabled is False, check_stale_positions returns without opening positions or selling."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
            "stale_position_rule_enabled": False,
        }):
            with patch("exit_manager._get_open_positions") as mock_pos:
                with patch("exit_manager._get_current_prices") as mock_prices:
                    with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                        with patch("exit_manager._close_position") as mock_close:
                            from exit_manager import check_stale_positions
                            check_stale_positions()
        mock_pos.assert_not_called()
        mock_twap.assert_not_called()
        mock_close.assert_not_called()

    def test_stale_position_rule_enabled_default_can_proceed(self):
        """When stale_position_rule_enabled is True (default), check_stale_positions may call _get_open_positions."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
            "stale_position_rule_enabled": True,
        }):
            with patch("exit_manager._get_open_positions", return_value=[]) as mock_pos:
                from exit_manager import check_stale_positions
                check_stale_positions()
        mock_pos.assert_called_once()

    def test_auto_execution_disabled_still_skips_even_if_rule_enabled(self):
        """When auto_execution_enabled is False, check_stale_positions returns without getting positions."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": False,
            "auto_execution_exchange": "coinbase",
            "stale_position_rule_enabled": True,
        }):
            with patch("exit_manager._get_open_positions") as mock_pos:
                from exit_manager import check_stale_positions
                check_stale_positions()
        mock_pos.assert_not_called()

    def test_stale_position_rule_key_missing_defaults_to_enabled(self):
        """When config has no stale_position_rule_enabled key, behavior is enabled (get_open_positions called)."""
        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
        }):
            with patch("exit_manager._get_open_positions", return_value=[]) as mock_pos:
                from exit_manager import check_stale_positions
                check_stale_positions()
        mock_pos.assert_called_once()


class TestCheckStalePositionsFullExit:
    """check_stale_positions full path: stale position → cancel TP/SL → TWAP sell → close with exit_reason 'time'."""

    def test_stale_position_triggers_twap_and_close_with_time_reason(self):
        """A position held longer than STALE_POSITION_MAX_DAYS is sold via TWAP and closed with exit_reason='time'."""
        from thresholds import get_threshold
        max_days = get_threshold('STALE_POSITION_MAX_DAYS', 14)
        entry_time = (datetime.now(timezone.utc) - timedelta(days=max_days + 5)).strftime('%Y-%m-%d %H:%M:%S')
        stale_position = {
            'id': 42,
            'coin_id': 'bitcoin',
            'coin_name': 'Bitcoin',
            'coin_symbol': 'BTC',
            'entry_price': 100.0,
            'quantity': 0.5,
            'entry_time': entry_time,
            'tp_order_id': 'tp-1',
            'sl_order_id': 'sl-1',
        }
        current_prices = {'bitcoin': 105.0}

        with patch("exit_manager._get_execution_config", return_value={
            "auto_execution_enabled": True,
            "auto_execution_exchange": "coinbase",
            "stale_position_rule_enabled": True,
            "twap_min_usd": 500.0,
        }):
            with patch("exit_manager._get_open_positions", return_value=[stale_position]):
                with patch("exit_manager._get_current_prices", return_value=current_prices):
                    with patch("exit_manager._cancel_order", return_value={"success": True}):
                        with patch("exit_manager._execute_market_sell_twap", return_value={
                            "success": True,
                            "fill_price": 104.5,
                            "mode": "market",
                            "total_fee_usd": 0.5,
                        }) as mock_twap:
                            with patch("exit_manager._close_position", return_value=(True, None)) as mock_close:
                                with patch("exit_manager._get_alerts"):
                                    with patch("exit_manager._should_alert", return_value=True):
                                        from exit_manager import check_stale_positions
                                        check_stale_positions(None)
        mock_twap.assert_called_once()
        call_kw = mock_twap.call_args[1]
        assert call_kw["symbol"] == "BTC"
        assert call_kw["quantity"] == 0.5
        mock_close.assert_called_once()
        close_kw = mock_close.call_args[1]
        assert close_kw["exit_reason"] == "time"
        assert close_kw["exit_price"] == 104.5
        assert "Stale exit" in (close_kw.get("notes") or "")


class TestCheckStalePositionsLeanInvariant:
    """Under LEAN_ENTRY_MODE the time-exit is a validated strategy pillar: it is
    enforced regardless of the dashboard toggle, and uses LEAN_MAX_HOLD_DAYS (~30d)."""

    @staticmethod
    def _lean_gt(key, default=None):
        return {
            'LEAN_ENTRY_MODE': 1.0,
            'LEAN_MAX_HOLD_DAYS': 30,
            'STALE_POSITION_MAX_DAYS': 21,
        }.get(key, default)

    def test_lean_enforces_time_exit_even_when_toggle_off(self):
        """Toggle Off + lean on → rule still runs (does not early-return)."""
        with patch("thresholds.get_threshold", side_effect=self._lean_gt):
            with patch("exit_manager._get_execution_config", return_value={
                "auto_execution_enabled": True,
                "auto_execution_exchange": "coinbase",
                "stale_position_rule_enabled": False,  # user turned the toggle OFF
            }):
                with patch("exit_manager._get_open_positions", return_value=[]) as mock_pos:
                    from exit_manager import check_stale_positions
                    check_stale_positions()
        mock_pos.assert_called_once()

    def test_lean_uses_30d_hold_not_21(self):
        """A 25-day-old position is stale under legacy 21d but NOT under lean 30d."""
        entry_time = (datetime.now(timezone.utc) - timedelta(days=25)).strftime('%Y-%m-%d %H:%M:%S')
        pos = {
            'id': 7, 'coin_id': 'bitcoin', 'coin_name': 'Bitcoin', 'coin_symbol': 'BTC',
            'entry_price': 100.0, 'quantity': 0.5, 'entry_time': entry_time,
            'tp_order_id': 'tp-1', 'sl_order_id': 'sl-1',
        }
        with patch("thresholds.get_threshold", side_effect=self._lean_gt):
            with patch("exit_manager._get_execution_config", return_value={
                "auto_execution_enabled": True,
                "auto_execution_exchange": "coinbase",
                "stale_position_rule_enabled": True,
                "twap_min_usd": 500.0,
            }):
                with patch("exit_manager._get_open_positions", return_value=[pos]):
                    with patch("exit_manager._get_current_prices", return_value={'bitcoin': 105.0}):
                        with patch("exit_manager._execute_market_sell_twap") as mock_twap:
                            with patch("exit_manager._close_position") as mock_close:
                                from exit_manager import check_stale_positions
                                check_stale_positions(None)
        mock_twap.assert_not_called()
        mock_close.assert_not_called()
