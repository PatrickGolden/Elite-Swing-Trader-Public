# tests/unit/test_tearsheet_generator.py — quantstats tearsheet generation tests
# All tests use tmp_path (Windows-safe) and mock external dependencies.

import gc
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def ts_mod():
    """Import tearsheet_generator lazily."""
    import tearsheet_generator
    return tearsheet_generator


@pytest.fixture
def fake_positions():
    """Generate realistic closed positions matching journal schema."""
    positions = []
    regimes = ['BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE']
    paths = ['dip', 'blood_in_the_street', 'pullback_in_trend', 'momentum_breakout', 'default']
    for i in range(40):
        day = 1 + (i % 28)
        pnl = 3.5 if i % 3 != 0 else -2.5
        positions.append({
            'status': 'CLOSED',
            'outcome': 'win' if pnl > 0 else 'loss',
            'pnl_pct': pnl,
            'pnl_usd': pnl * 10,
            'entry_time': f'2026-02-{day:02d}T10:00:00Z',
            'exit_time': f'2026-03-{day:02d}T14:00:00Z',
            'market_regime': regimes[i % 4],
            'regime': regimes[i % 4],
            'quant_path': paths[i % 5],
            'coin_symbol': 'BTC',
        })
    return positions


def teardown_function():
    gc.collect()


class TestPositionsToDailyReturns:
    def test_basic_conversion(self, ts_mod, fake_positions):
        returns = ts_mod._positions_to_daily_returns(fake_positions)
        assert returns is not None
        assert isinstance(returns, pd.Series)
        assert len(returns) > 0

    def test_groups_by_exit_date(self, ts_mod):
        """Two positions exiting same day should sum their returns."""
        positions = [
            {'exit_time': '2026-03-01T10:00:00Z', 'pnl_pct': 5.0},
            {'exit_time': '2026-03-01T14:00:00Z', 'pnl_pct': 3.0},
            {'exit_time': '2026-03-02T10:00:00Z', 'pnl_pct': -2.0},
        ]
        returns = ts_mod._positions_to_daily_returns(positions)
        assert returns is not None
        # Day 1: (5+3)/100 = 0.08, Day 2: -2/100 = -0.02
        day1 = returns.iloc[0]
        assert abs(day1 - 0.08) < 0.001

    def test_fills_gaps_with_zero(self, ts_mod):
        positions = [
            {'exit_time': '2026-03-01T10:00:00Z', 'pnl_pct': 5.0},
            {'exit_time': '2026-03-05T10:00:00Z', 'pnl_pct': 3.0},
        ]
        returns = ts_mod._positions_to_daily_returns(positions)
        # Should have 5 days (Mar 1-5), with zeros on 2-4
        assert len(returns) == 5
        assert returns.iloc[1] == 0.0

    def test_empty_positions(self, ts_mod):
        assert ts_mod._positions_to_daily_returns([]) is None

    def test_missing_fields_skipped(self, ts_mod):
        positions = [
            {'exit_time': '2026-03-01T10:00:00Z', 'pnl_pct': None},
            {'exit_time': None, 'pnl_pct': 5.0},
            {'exit_time': '2026-03-01T10:00:00Z', 'pnl_pct': 3.0},
        ]
        returns = ts_mod._positions_to_daily_returns(positions)
        assert returns is not None
        assert len(returns) == 1
        assert abs(returns.iloc[0] - 0.03) < 0.001


class TestGenerateTearsheet:
    def test_success_with_mock_quantstats(self, ts_mod, fake_positions, tmp_path):
        with patch('quantstats.reports.html') as mock_html, \
             patch.object(ts_mod, '_fetch_btc_benchmark', return_value=None):
            result = ts_mod.generate_tearsheet(
                fake_positions, name='test', output_dir=tmp_path,
            )

        assert result['success'] is True
        assert result['name'] == 'test'
        assert 'path' in result
        mock_html.assert_called_once()

    def test_insufficient_data(self, ts_mod, tmp_path):
        result = ts_mod.generate_tearsheet([], name='empty', output_dir=tmp_path)
        assert result['success'] is False
        assert 'insufficient' in result['reason']

    def test_single_position_insufficient(self, ts_mod, tmp_path):
        positions = [{'exit_time': '2026-03-01T10:00:00Z', 'pnl_pct': 5.0}]
        result = ts_mod.generate_tearsheet(positions, name='one', output_dir=tmp_path)
        assert result['success'] is False


class TestGenerateFilteredTearsheets:
    def test_filters_by_regime(self, ts_mod, fake_positions, tmp_path):
        with patch.object(ts_mod, 'generate_tearsheet') as mock_gen:
            mock_gen.return_value = {'success': True, 'name': 'test', 'path': 'test.html'}
            results = ts_mod.generate_filtered_tearsheets(
                fake_positions, output_dir=tmp_path,
            )

        # Should call: full + 4 regimes + 5 paths = 10 calls
        assert mock_gen.call_count == 10
        # First call should be 'full'
        first_call_name = mock_gen.call_args_list[0][1].get('name') or mock_gen.call_args_list[0][0][1]
        assert first_call_name == 'full'

    def test_skips_regimes_with_few_positions(self, ts_mod, tmp_path):
        """Regime with <5 positions should be skipped."""
        positions = [
            {'exit_time': f'2026-03-{i+1:02d}T10:00:00Z', 'pnl_pct': 3.0,
             'market_regime': 'BULL', 'quant_path': 'dip'}
            for i in range(3)  # Only 3 BULL positions
        ]
        with patch.object(ts_mod, 'generate_tearsheet') as mock_gen:
            mock_gen.return_value = {'success': True, 'name': 'test', 'path': 'test.html'}
            results = ts_mod.generate_filtered_tearsheets(positions, output_dir=tmp_path)

        # Only 'full' should be called (BULL has <5, dip has <5)
        assert mock_gen.call_count == 1
