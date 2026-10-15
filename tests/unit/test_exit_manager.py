"""Tests for exit_manager.py — trailing stops, breakeven stops, stale position exits."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone, timedelta
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import exit_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(**overrides):
    """Build a minimal open position dict with sensible defaults."""
    pos = {
        'id': 1,
        'coin_id': 'bitcoin',
        'coin_symbol': 'BTC',
        'coin_name': 'Bitcoin',
        'symbol': 'BTC',
        'entry_price': 100.0,
        'quantity': 1.0,
        'capital_usd': 100.0,
        'is_test': False,
        'high_water_mark': None,
        'current_stop_pct': 10.0,
        'ai_stop_pct': None,
        'tp_order_id': None,
        'sl_order_id': None,
        'trade_id': 'trade-1',
        'entry_time': '2026-01-01 00:00:00',
    }
    pos.update(overrides)
    return pos


def _default_cfg(**overrides):
    cfg = {
        'auto_execution_enabled': True,
        'auto_execution_exchange': 'coinbase',
        'twap_min_usd': 500.0,
        'stale_position_rule_enabled': True,
    }
    cfg.update(overrides)
    return cfg


def _mock_alerts():
    alerts = MagicMock()
    alerts.send_critical_alert = MagicMock()
    alerts._format_sell_confirmation_extras = MagicMock(return_value='extras-text')
    return alerts


# ---------------------------------------------------------------------------
# TestBreakevenStops
# ---------------------------------------------------------------------------

class TestBreakevenStops:
    """Tests for _check_breakeven_stops."""

    @patch('exit_manager._update_position')
    @patch('exit_manager.get_threshold' if False else 'thresholds.get_threshold')
    def _call(self, position, current_price, entry_price, profit_pct,
              mock_get_thresh, mock_update, thresholds_map=None):
        """Helper — patches thresholds.get_threshold (lazy import) and _update_position."""
        defaults = {
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
        }
        if thresholds_map:
            defaults.update(thresholds_map)
        mock_get_thresh.side_effect = lambda key, default=None: defaults.get(key, default)
        exit_manager._check_breakeven_stops(position, current_price, entry_price, profit_pct)
        return mock_update

    def test_no_action_below_trigger(self):
        """Profit below 1.5% → no update."""
        pos = _make_position()
        mock_update = self._call(pos, 101.0, 100.0, 1.0)
        mock_update.assert_not_called()

    def test_no_action_in_trailing_zone(self):
        """Profit >= 3% → handled by trailing, not breakeven."""
        pos = _make_position()
        mock_update = self._call(pos, 104.0, 100.0, 4.0)
        mock_update.assert_not_called()

    def test_tightens_stop_to_regime_floor_sideways(self):
        """Profit in [1.5, 3), SIDEWAYS regime → tighten to floor (3.5%), not raw buffer (0.3%)."""
        pos = _make_position(current_stop_pct=10.0, market_regime='SIDEWAYS')
        mock_update = self._call(pos, 102.0, 100.0, 2.0)
        mock_update.assert_called_once()
        args = mock_update.call_args
        assert args[0][0] == 1  # position_id
        updates = args[0][1]
        assert updates['current_stop_pct'] == 3.5
        assert updates['current_stop_price'] == round(100.0 * (1 - 3.5 / 100), 8)

    def test_bear_regime_clamps_to_floor(self):
        """BEAR regime: breakeven must not compress below 5.0% floor (the SOL scenario)."""
        pos = _make_position(current_stop_pct=7.8, market_regime='BEAR')
        mock_update = self._call(pos, 102.0, 100.0, 2.0)
        mock_update.assert_called_once()
        updates = mock_update.call_args[0][1]
        assert updates['current_stop_pct'] == 5.0
        assert updates['current_stop_price'] == round(100.0 * (1 - 5.0 / 100), 8)

    def test_no_action_if_stop_at_or_below_floor(self):
        """Stop already at or below regime floor → no update (only-tightens invariant)."""
        pos = _make_position(current_stop_pct=3.0, market_regime='SIDEWAYS')
        mock_update = self._call(pos, 102.0, 100.0, 2.0)
        mock_update.assert_not_called()

    def test_uses_ai_stop_pct_fallback(self):
        """Falls back to ai_stop_pct when current_stop_pct is missing."""
        pos = _make_position(current_stop_pct=None, ai_stop_pct=8.0)
        mock_update = self._call(pos, 102.0, 100.0, 2.0)
        mock_update.assert_called_once()

    def test_uses_99_fallback_when_no_stop(self):
        """If both stop fields are None → defaults to 99, still tightens."""
        pos = _make_position(current_stop_pct=None, ai_stop_pct=None)
        mock_update = self._call(pos, 102.0, 100.0, 2.0)
        mock_update.assert_called_once()

    def test_exactly_at_trigger_boundary(self):
        """profit_pct == trigger exactly → should tighten."""
        pos = _make_position(current_stop_pct=5.0)
        mock_update = self._call(pos, 101.5, 100.0, 1.5)
        mock_update.assert_called_once()

    def test_exactly_at_trailing_boundary_no_action(self):
        """profit_pct == min_profit exactly → in trailing zone, no breakeven."""
        pos = _make_position()
        mock_update = self._call(pos, 103.0, 100.0, 3.0)
        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# TestTrailingStops
# ---------------------------------------------------------------------------

class TestTrailingStops:
    """Tests for check_trailing_stops."""

    def _patch_all(self):
        """Return dict of patch objects for all exit_manager wrappers."""
        return {
            'cfg': patch('exit_manager._get_execution_config', return_value=_default_cfg()),
            'positions': patch('exit_manager._get_open_positions', return_value=[]),
            'prices': patch('exit_manager._get_current_prices', return_value={}),
            'update': patch('exit_manager._update_position'),
            'close': patch('exit_manager._close_position', return_value=({}, None)),
            'sell': patch('exit_manager._execute_market_sell_twap', return_value={
                'success': True, 'fill_price': 120.0, 'total_fee_usd': 0.5, 'mode': 'twap',
            }),
            'cancel': patch('exit_manager._cancel_order', return_value={'success': True}),
            'alerts': patch('exit_manager._get_alerts', return_value=_mock_alerts()),
            'should_alert': patch('exit_manager._should_alert', return_value=True),
            'threshold': patch('thresholds.get_threshold'),
        }

    def test_returns_early_if_auto_execution_disabled(self):
        cfg = _default_cfg(auto_execution_enabled=False)
        with patch('exit_manager._get_execution_config', return_value=cfg) as mock_cfg, \
             patch('exit_manager._get_open_positions') as mock_pos:
            exit_manager.check_trailing_stops()
            mock_pos.assert_not_called()

    def test_returns_early_if_exchange_not_coinbase(self):
        cfg = _default_cfg(auto_execution_exchange='kraken')
        with patch('exit_manager._get_execution_config', return_value=cfg), \
             patch('exit_manager._get_open_positions') as mock_pos:
            exit_manager.check_trailing_stops()
            mock_pos.assert_not_called()

    def test_returns_early_if_no_positions(self):
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[]), \
             patch('exit_manager._get_current_prices') as mock_prices:
            exit_manager.check_trailing_stops()
            mock_prices.assert_not_called()

    def test_skips_test_positions(self):
        pos = _make_position(is_test=True, entry_price=100.0)
        thresholds_map = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 120.0}), \
             patch('exit_manager._update_position') as mock_update, \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds_map.get(k, d)), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap') as mock_sell:
            exit_manager.check_trailing_stops()
            mock_sell.assert_not_called()

    def test_skips_position_without_coin_id(self):
        pos = _make_position(coin_id=None)
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={}), \
             patch('exit_manager._update_position') as mock_update, \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: d), \
             patch('exit_manager._should_alert', return_value=True):
            exit_manager.check_trailing_stops()
            mock_update.assert_not_called()

    def test_skips_position_with_zero_entry_price(self):
        pos = _make_position(entry_price=0)
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 50.0}), \
             patch('exit_manager._update_position') as mock_update, \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: d), \
             patch('exit_manager._should_alert', return_value=True):
            exit_manager.check_trailing_stops()
            mock_update.assert_not_called()

    def test_skips_below_min_profit(self):
        """Position with only 2% profit (below trailing activation 3%) → no trailing action."""
        pos = _make_position(entry_price=100.0)
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 102.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap') as mock_sell, \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)), \
             patch('exit_manager._should_alert', return_value=True):
            exit_manager.check_trailing_stops()
            mock_sell.assert_not_called()

    def test_updates_high_water_mark(self):
        """Current price above high water → update position."""
        pos = _make_position(entry_price=100.0, high_water_mark=108.0)
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        # Price 110 is above high_water 108, and trailing stop = 110 * 0.97 = 106.7 → price 110 > 106.7 → no sell
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position') as mock_update, \
             patch('exit_manager._execute_market_sell_twap') as mock_sell, \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('execution_engine._log_audit'):
            exit_manager.check_trailing_stops()
            # Should have been called for high water mark update
            update_calls = [c for c in mock_update.call_args_list if 'high_water_mark' in (c[0][1] if len(c[0]) > 1 else {})]
            assert len(update_calls) >= 1
            assert update_calls[0][0][1]['high_water_mark'] == 110.0
            mock_sell.assert_not_called()

    def test_triggers_sell_when_trailing_stop_hit(self):
        """Price drops below trailing stop → executes sell."""
        # entry=100, high_water=115, trail 3% → stop at 111.55, current=110 < 111.55 → sell
        pos = _make_position(entry_price=100.0, high_water_mark=115.0, quantity=2.0)
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        mock_alerts = _mock_alerts()
        sell_result = {'success': True, 'fill_price': 110.0, 'total_fee_usd': 0.5, 'mode': 'twap'}
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap', return_value=sell_result) as mock_sell, \
             patch('exit_manager._close_position', return_value=({'id': 1}, None)), \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)):
            exit_manager.check_trailing_stops()
            mock_sell.assert_called_once()
            assert mock_sell.call_args[1]['symbol'] == 'BTC'
            assert mock_sell.call_args[1]['quantity'] == 2.0

    def test_cancels_tp_sl_before_sell(self):
        """TP and SL orders cancelled before TWAP sell."""
        pos = _make_position(
            entry_price=100.0, high_water_mark=115.0,
            tp_order_id='tp-123', sl_order_id='sl-456',
        )
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        sell_result = {'success': True, 'fill_price': 110.0, 'total_fee_usd': 0.5, 'mode': 'market'}
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap', return_value=sell_result), \
             patch('exit_manager._close_position', return_value=({}, None)), \
             patch('exit_manager._cancel_order', return_value={'success': True}) as mock_cancel, \
             patch('exit_manager._get_alerts', return_value=_mock_alerts()), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)):
            exit_manager.check_trailing_stops()
            cancel_ids = [c[0][0] for c in mock_cancel.call_args_list]
            assert 'tp-123' in cancel_ids
            assert 'sl-456' in cancel_ids

    def test_cancel_failure_filled_skips_sell(self):
        """If TP cancel fails because it's already filled → skip sell to prevent double-sell."""
        pos = _make_position(
            entry_price=100.0, high_water_mark=115.0,
            tp_order_id='tp-123',
        )
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        mock_alerts = _mock_alerts()
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap') as mock_sell, \
             patch('exit_manager._cancel_order', return_value={'success': False, 'error': 'fail'}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)), \
             patch('execution_engine.fetch_order_status', return_value='filled'):
            exit_manager.check_trailing_stops()
            mock_sell.assert_not_called()
            mock_alerts.send_critical_alert.assert_called()
            alert_title = mock_alerts.send_critical_alert.call_args[0][0]
            assert 'SKIPPED' in alert_title

    def test_sell_failure_sends_alert(self):
        """TWAP sell returns success=False → sends failure alert."""
        pos = _make_position(entry_price=100.0, high_water_mark=115.0)
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        mock_alerts = _mock_alerts()
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap', return_value={'success': False, 'error': 'nope'}), \
             patch('exit_manager._close_position') as mock_close, \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)):
            exit_manager.check_trailing_stops()
            mock_close.assert_not_called()
            mock_alerts.send_critical_alert.assert_called()
            assert 'FAILED' in mock_alerts.send_critical_alert.call_args[0][0]

    def test_sell_exception_sends_alert(self):
        """TWAP sell raises → sends failure alert, continues."""
        pos = _make_position(entry_price=100.0, high_water_mark=115.0)
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        mock_alerts = _mock_alerts()
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap', side_effect=RuntimeError('boom')), \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)):
            exit_manager.check_trailing_stops()
            mock_alerts.send_critical_alert.assert_called()
            assert 'FAILED' in mock_alerts.send_critical_alert.call_args[0][0]

    def test_insufficient_fund_closes_if_balance_zero(self):
        """INSUFFICIENT_FUND error + exchange balance 0 → closes position from exchange state."""
        pos = _make_position(entry_price=100.0, high_water_mark=115.0, coin_symbol='BTC')
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        mock_alerts = _mock_alerts()
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap', return_value={'success': False, 'error': 'INSUFFICIENT_FUND'}), \
             patch('exit_manager._close_position', return_value=({}, None)) as mock_close, \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)), \
             patch('execution_engine.fetch_balance_sync', return_value={'holdings': {}}):
            exit_manager.check_trailing_stops()
            mock_close.assert_called_once()
            assert mock_close.call_args[1]['exit_reason'] == 'exchange_sold'

    def test_success_sends_confirmation_alert(self):
        """Successful trailing stop sell → sends confirmation with extras."""
        pos = _make_position(entry_price=100.0, high_water_mark=115.0)
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        mock_alerts = _mock_alerts()
        sell_result = {'success': True, 'fill_price': 110.0, 'total_fee_usd': 0.5, 'mode': 'twap'}
        closed_pos = {'id': 1, 'exit_price': 110.0}
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap', return_value=sell_result), \
             patch('exit_manager._close_position', return_value=(closed_pos, None)), \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)):
            exit_manager.check_trailing_stops()
            # Last alert call should be the confirmation
            calls = mock_alerts.send_critical_alert.call_args_list
            confirm_calls = [c for c in calls if 'Executed' in c[0][0]]
            assert len(confirm_calls) == 1
            assert 'trailing_stop' in str(confirm_calls[0])

    def test_close_position_error_sends_journal_alert(self):
        """close_position returns error → sends journal error alert."""
        pos = _make_position(entry_price=100.0, high_water_mark=115.0)
        thresholds = {
            'TRAILING_STOP_MIN_PROFIT_PCT': 3.0,
            'TRAILING_STOP_TRAIL_PCT': 3.0,
            'BREAKEVEN_STOP_TRIGGER_PCT': 1.5,
            'BREAKEVEN_STOP_BUFFER_PCT': 0.3,
        }
        mock_alerts = _mock_alerts()
        sell_result = {'success': True, 'fill_price': 110.0, 'total_fee_usd': 0.5, 'mode': 'twap'}
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 110.0}), \
             patch('exit_manager._update_position'), \
             patch('exit_manager._execute_market_sell_twap', return_value=sell_result), \
             patch('exit_manager._close_position', return_value=(None, 'db error')), \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: thresholds.get(k, d)):
            exit_manager.check_trailing_stops()
            alert_title = mock_alerts.send_critical_alert.call_args[0][0]
            assert 'Journal Error' in alert_title

    def test_passes_current_prices_when_provided(self):
        """When current_prices kwarg is given, does not fetch prices."""
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[_make_position()]), \
             patch('exit_manager._get_current_prices') as mock_fetch, \
             patch('exit_manager._update_position'), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('thresholds.get_threshold', side_effect=lambda k, d=None: d):
            exit_manager.check_trailing_stops(current_prices={'bitcoin': 103.0})
            mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# TestStalePositions
# ---------------------------------------------------------------------------

class TestStalePositions:
    """Tests for check_stale_positions."""

    def _old_entry_time(self, days_ago=20):
        t = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return t.strftime('%Y-%m-%d %H:%M:%S')

    def test_returns_early_if_auto_execution_disabled(self):
        cfg = _default_cfg(auto_execution_enabled=False)
        with patch('exit_manager._get_execution_config', return_value=cfg), \
             patch('exit_manager._get_open_positions') as mock_pos:
            exit_manager.check_stale_positions()
            mock_pos.assert_not_called()

    def test_returns_early_if_stale_rule_disabled(self):
        cfg = _default_cfg(stale_position_rule_enabled=False)
        with patch('exit_manager._get_execution_config', return_value=cfg), \
             patch('exit_manager._get_open_positions') as mock_pos:
            exit_manager.check_stale_positions()
            mock_pos.assert_not_called()

    def test_returns_early_if_exchange_not_coinbase(self):
        cfg = _default_cfg(auto_execution_exchange='kraken')
        with patch('exit_manager._get_execution_config', return_value=cfg), \
             patch('exit_manager._get_open_positions') as mock_pos:
            exit_manager.check_stale_positions()
            mock_pos.assert_not_called()

    def test_skips_position_below_max_days(self):
        """Position held only 5 days (max 14) → no exit."""
        pos = _make_position(entry_time=self._old_entry_time(days_ago=5))
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap') as mock_sell, \
             patch('thresholds.get_threshold', return_value=14):
            exit_manager.check_stale_positions(current_prices={'bitcoin': 105.0})
            mock_sell.assert_not_called()

    def test_auto_exits_stale_position(self):
        """Position held 20 days (max 14) → triggers sell."""
        pos = _make_position(entry_time=self._old_entry_time(days_ago=20))
        mock_alerts = _mock_alerts()
        sell_result = {'success': True, 'fill_price': 105.0, 'total_fee_usd': 0.3, 'mode': 'market'}
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap', return_value=sell_result) as mock_sell, \
             patch('exit_manager._close_position', return_value=({'id': 1}, None)), \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('exit_manager._get_current_prices', return_value={'bitcoin': 105.0}), \
             patch('thresholds.get_threshold', return_value=14):
            exit_manager.check_stale_positions(current_prices={'bitcoin': 105.0})
            mock_sell.assert_called_once()

    def test_cancels_tp_sl_before_stale_exit(self):
        """TP and SL orders cancelled before stale exit sell."""
        pos = _make_position(
            entry_time=self._old_entry_time(days_ago=20),
            tp_order_id='tp-stale', sl_order_id='sl-stale',
        )
        sell_result = {'success': True, 'fill_price': 105.0, 'total_fee_usd': 0.3, 'mode': 'twap'}
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap', return_value=sell_result), \
             patch('exit_manager._close_position', return_value=({}, None)), \
             patch('exit_manager._cancel_order', return_value={'success': True}) as mock_cancel, \
             patch('exit_manager._get_alerts', return_value=_mock_alerts()), \
             patch('thresholds.get_threshold', return_value=14):
            exit_manager.check_stale_positions(current_prices={'bitcoin': 105.0})
            cancel_ids = [c[0][0] for c in mock_cancel.call_args_list]
            assert 'tp-stale' in cancel_ids
            assert 'sl-stale' in cancel_ids

    def test_stale_cancel_failure_skips_sell(self):
        """TP already filled during stale exit → skip sell."""
        pos = _make_position(
            entry_time=self._old_entry_time(days_ago=20),
            tp_order_id='tp-filled',
        )
        mock_alerts = _mock_alerts()
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap') as mock_sell, \
             patch('exit_manager._cancel_order', return_value={'success': False, 'error': 'fail'}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('thresholds.get_threshold', return_value=14), \
             patch('execution_engine.fetch_order_status', return_value='filled'):
            exit_manager.check_stale_positions(current_prices={'bitcoin': 105.0})
            mock_sell.assert_not_called()
            alert_title = mock_alerts.send_critical_alert.call_args[0][0]
            assert 'SKIPPED' in alert_title

    def test_stale_sends_confirmation_alert(self):
        """Successful stale exit → sends confirmation with extras."""
        pos = _make_position(entry_time=self._old_entry_time(days_ago=20))
        mock_alerts = _mock_alerts()
        sell_result = {'success': True, 'fill_price': 95.0, 'total_fee_usd': 0.2, 'mode': 'twap'}
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap', return_value=sell_result), \
             patch('exit_manager._close_position', return_value=({'id': 1}, None)), \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('thresholds.get_threshold', return_value=14):
            exit_manager.check_stale_positions(current_prices={'bitcoin': 95.0})
            confirm_calls = [c for c in mock_alerts.send_critical_alert.call_args_list if 'Executed' in c[0][0]]
            assert len(confirm_calls) == 1
            assert 'stop_loss' in str(confirm_calls[0])

    def test_stale_sell_failure_sends_alert(self):
        """TWAP sell fails during stale exit → sends failure alert."""
        pos = _make_position(entry_time=self._old_entry_time(days_ago=20))
        mock_alerts = _mock_alerts()
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap', return_value={'success': False, 'error': 'oops'}), \
             patch('exit_manager._cancel_order', return_value={'success': True}), \
             patch('exit_manager._get_alerts', return_value=mock_alerts), \
             patch('thresholds.get_threshold', return_value=14):
            exit_manager.check_stale_positions(current_prices={'bitcoin': 105.0})
            assert 'FAILED' in mock_alerts.send_critical_alert.call_args[0][0]

    def test_stale_skips_test_positions(self):
        """is_test=True → skipped by stale check."""
        pos = _make_position(is_test=True, entry_time=self._old_entry_time(days_ago=20))
        with patch('exit_manager._get_execution_config', return_value=_default_cfg()), \
             patch('exit_manager._get_open_positions', return_value=[pos]), \
             patch('exit_manager._should_alert', return_value=True), \
             patch('exit_manager._execute_market_sell_twap') as mock_sell, \
             patch('thresholds.get_threshold', return_value=14):
            exit_manager.check_stale_positions(current_prices={'bitcoin': 105.0})
            mock_sell.assert_not_called()
