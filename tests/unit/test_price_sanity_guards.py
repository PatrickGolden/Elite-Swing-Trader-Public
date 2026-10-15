# tests/unit/test_price_sanity_guards.py
# Tests for price corruption defenses across paper and live trade paths.
# Run: pytest tests/unit/test_price_sanity_guards.py -v

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_position(pid=1, coin_id='bitcoin', symbol='BTC', entry_price=50000.0,
                   quantity=0.002, status='OPEN', signal='BUY', is_test=False,
                   source=None, stop_pct=5.0, target_pct=10.0, sl_order_id=None):
    stop_price = entry_price * (1 - stop_pct / 100)
    target_price = entry_price * (1 + target_pct / 100)
    return {
        'id': pid, 'trade_id': f'TRD-{pid:05d}', 'status': status,
        'coin_id': coin_id, 'coin_name': symbol, 'coin_symbol': symbol,
        'signal': signal, 'entry_price': entry_price, 'quantity': quantity,
        'capital_usd': entry_price * quantity,
        'entry_fee_usd': 0.1,
        'ai_stop_pct': stop_pct, 'ai_stop_price': stop_price,
        'ai_target_pct': target_pct, 'ai_target_price': target_price,
        'current_stop_pct': stop_pct, 'current_stop_price': stop_price,
        'current_target_pct': target_pct, 'current_target_price': target_price,
        'rr_ratio': target_pct / stop_pct if stop_pct else 0,
        'let_winners_run': False, 'is_test': is_test,
        'source': source, 'sl_order_id': sl_order_id, 'tp_order_id': None,
        'entry_time': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'quant_tier': 'MODERATE_BUY', 'quant_path': 'default',
        'market_regime': 'BULL', 'specialist_veto': 'CONFIRM',
        'confirmation_score': 60, 'fear_greed': 50,
    }


# =========================================================================
# Test: CoinPaprika cross-validation rejects wrong price
# =========================================================================
class TestCoinPaprikaCrossValidation:
    """Phase 2: CoinPaprika prices that disagree with coins cache are rejected."""

    def test_paprika_price_rejected_when_coins_cache_disagrees(self):
        import time as _time
        from enhanced_data_fetcher import EnhancedDataFetcherV2
        fetcher = EnhancedDataFetcherV2()

        # Coins cache has reference data, but mark it stale so initial pass skips it
        fetcher._cache['coins'] = [
            {'id': 'pepe', 'symbol': 'PEPE', 'current_price': 0.000004},
        ]
        fetcher._cache_times['coins'] = 0  # epoch -- stale
        fetcher._simple_price_cache = {'ts': 0, 'prices': {}}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {'symbol': 'PEPE', 'rank': 50, 'quotes': {'USD': {'price': 7.64}}},
        ]

        with patch('enhanced_data_fetcher.resilient_get', return_value=mock_resp):
            result = fetcher.get_current_prices_fast(['pepe'])

        assert 'pepe' in result
        assert abs(result['pepe'] - 0.000004) < 0.00001, \
            f"Should use coins_cache reference price, got {result['pepe']}"
        assert fetcher._price_source_log.get('pepe') == 'coins_cache_override'

    def test_paprika_price_accepted_when_close_to_cache(self):
        from enhanced_data_fetcher import EnhancedDataFetcherV2
        fetcher = EnhancedDataFetcherV2()

        # Coins cache stale for initial pass, but has reference data for cross-validation
        fetcher._cache['coins'] = [
            {'id': 'bitcoin', 'symbol': 'BTC', 'current_price': 50000},
        ]
        fetcher._cache_times['coins'] = 0  # stale
        fetcher._simple_price_cache = {'ts': 0, 'prices': {}}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {'symbol': 'BTC', 'rank': 1, 'quotes': {'USD': {'price': 50100}}},
        ]

        with patch('enhanced_data_fetcher.resilient_get', return_value=mock_resp):
            result = fetcher.get_current_prices_fast(['bitcoin'])

        assert 'bitcoin' in result
        assert abs(result['bitcoin'] - 50100) < 1, \
            f"Should use CoinPaprika price when close, got {result['bitcoin']}"
        assert fetcher._price_source_log.get('bitcoin') == 'coinpaprika'


# =========================================================================
# Test: CoinPaprika symbol collision prefers highest rank
# =========================================================================
class TestCoinPaprikaRankSelection:
    """CoinPaprika returns multiple tokens with same symbol; prefer lowest rank."""

    def test_picks_highest_market_cap_when_symbol_collision(self):
        from enhanced_data_fetcher import EnhancedDataFetcherV2
        fetcher = EnhancedDataFetcherV2()
        fetcher._cache.clear()
        fetcher._cache_times.clear()
        fetcher._simple_price_cache = {'ts': 0, 'prices': {}}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {'symbol': 'PEPE', 'rank': 1500, 'quotes': {'USD': {'price': 7.64}}},
            {'symbol': 'PEPE', 'rank': 52, 'quotes': {'USD': {'price': 0.000004}}},
        ]

        with patch('enhanced_data_fetcher.resilient_get', return_value=mock_resp):
            result = fetcher.get_current_prices_fast(['pepe'])

        assert 'pepe' in result
        assert abs(result['pepe'] - 0.000004) < 0.00001, \
            f"Should pick rank-52 PEPE, got {result['pepe']}"


# =========================================================================
# Test: Paper trade exit sanity guard
# =========================================================================
class TestPaperExitSanityGuard:
    """Paper position exit rejects prices >50x or <0.001x entry."""

    @patch('app.get_open_positions')
    @patch('app._close_paper_position_simulated')
    def test_rejects_50x_price_for_paper(self, mock_close, mock_positions):
        from app import _check_paper_position_exits

        paper_pos = _make_position(
            pid=200, coin_id='pepe', symbol='PEPE',
            entry_price=0.000004, quantity=25000000, is_test=True, source='paper_trade',
        )
        mock_positions.return_value = [paper_pos]

        # 80x the entry price -- should be rejected
        prices = {'pepe': 0.00032}
        _check_paper_position_exits(prices)

        mock_close.assert_not_called()

    @patch('app.get_open_positions')
    @patch('app._close_paper_position_simulated')
    def test_accepts_normal_stop_for_paper(self, mock_close, mock_positions):
        from app import _check_paper_position_exits

        paper_pos = _make_position(
            pid=201, coin_id='pepe', symbol='PEPE',
            entry_price=0.000004, quantity=25000000, is_test=True, source='paper_trade',
        )
        mock_positions.return_value = [paper_pos]

        stop_price = paper_pos['current_stop_price']
        prices = {'pepe': stop_price * 0.99}
        _check_paper_position_exits(prices)

        mock_close.assert_called_once()


# =========================================================================
# Test: Live position exit sanity guard
# =========================================================================
class TestLiveExitSanityGuard:
    """Live position exit rejects prices >20x or <0.02x entry."""

    @patch('app.get_execution_config', return_value={'auto_execution_enabled': True})
    @patch('app._check_sl_tp_order_status')
    @patch('app.get_open_positions')
    @patch('app._handle_stop_loss_hit')
    @patch('app._handle_take_profit_hit')
    @patch('app.fetcher')
    def test_rejects_absurd_low_price_for_live(self, mock_fetcher, mock_tp, mock_sl,
                                                mock_positions, mock_sl_tp, mock_cfg):
        from app import check_position_exits

        # Coinbase spot overlay must not override the test price — return empty to disable it
        mock_fetcher.get_coinbase_spot_prices.return_value = {}

        live_pos = _make_position(
            pid=300, coin_id='ethereum', symbol='ETH', entry_price=2300.0, quantity=0.04,
        )
        mock_positions.return_value = [live_pos]

        # Price says ETH is $0.08 -- 0.00003x entry, should be rejected by ratio guard
        prices = {'ethereum': 0.08}
        check_position_exits(prices)

        mock_sl.assert_not_called()
        mock_tp.assert_not_called()

    @patch('app.get_execution_config', return_value={'auto_execution_enabled': True})
    @patch('app._check_sl_tp_order_status')
    @patch('app.get_open_positions')
    @patch('app._handle_stop_loss_hit')
    @patch('app.fetcher')
    def test_accepts_normal_stop_for_live(self, mock_fetcher, mock_sl, mock_positions, mock_sl_tp, mock_cfg):
        from app import check_position_exits

        live_pos = _make_position(
            pid=301, coin_id='ethereum', symbol='ETH', entry_price=2300.0, quantity=0.04,
        )
        mock_positions.return_value = [live_pos]

        stop_price = live_pos['current_stop_price']
        below_stop = stop_price * 0.99
        # Coinbase spot returns consistent price
        mock_fetcher.get_coinbase_spot_prices.return_value = {'ETH': below_stop}
        prices = {'ethereum': below_stop}
        check_position_exits(prices)

        mock_sl.assert_called_once()


# =========================================================================
# Test: Gap-down fallback Coinbase cross-check
# =========================================================================
class TestGapDownCoinbaseCrossCheck:
    """Gap-down fallback is blocked when Coinbase spot disagrees with fetched price."""

    @patch('app.get_execution_config', return_value={
        'auto_execution_enabled': True,
        'auto_execution_exchange': 'coinbase',
    })
    @patch('app.get_open_positions', return_value=[])
    @patch('app.fetcher')
    @patch('app.execute_market_sell')
    @patch('app.cancel_order')
    @patch('app.fetch_order_status', return_value='open')
    def test_gap_blocked_when_coinbase_disagrees(self, mock_fetch_status, mock_cancel,
                                                  mock_sell, mock_fetcher,
                                                  mock_positions, mock_cfg):
        from app import _check_sl_tp_order_status

        position = _make_position(
            pid=400, coin_id='bitcoin', symbol='BTC', entry_price=70000,
            sl_order_id='test-sl-order-123',
        )

        with patch('app.get_open_positions', return_value=[position]):
            mock_fetcher.get_coinbase_spot_prices.return_value = {'BTC': 69500}

            # Fetched price says $50 (absurdly low), but Coinbase says $69500
            prices = {'bitcoin': 50.0}
            _check_sl_tp_order_status(prices)

        mock_cancel.assert_not_called()
        mock_sell.assert_not_called()


# =========================================================================
# Test: exit_manager trailing stop sanity guard
# =========================================================================
class TestExitManagerSanityGuard:
    """exit_manager.check_trailing_stops rejects absurd prices."""

    @patch('exit_manager._get_execution_config', return_value={
        'auto_execution_enabled': True,
        'auto_execution_exchange': 'coinbase',
    })
    @patch('exit_manager._get_open_positions')
    @patch('exit_manager._execute_market_sell_twap')
    def test_trailing_rejects_bad_price(self, mock_sell, mock_positions, mock_cfg):
        from exit_manager import check_trailing_stops

        pos = _make_position(
            pid=500, coin_id='solana', symbol='SOL', entry_price=100.0, quantity=1.0,
        )
        mock_positions.return_value = [pos]

        # Price claims SOL is $0.001 (0.00001x entry)
        prices = {'solana': 0.001}
        check_trailing_stops(prices)

        mock_sell.assert_not_called()
