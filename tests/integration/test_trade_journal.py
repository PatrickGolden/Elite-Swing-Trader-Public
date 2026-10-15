# tests/test_trade_journal.py — Open/close/update positions, P&L calculation, AI target hit tracking
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestTradeJournalOpenPosition:
    """Test opening positions."""

    def test_open_position_creates_valid_position(self, temp_positions):
        """open_position creates position with required fields and AI targets."""
        from trade_journal import open_position, get_all_positions

        pos = open_position({
            'coin_id': 'bitcoin',
            'coin_name': 'Bitcoin',
            'coin_symbol': 'BTC',
            'signal': 'BUY',
            'entry_price': 45000,
            'quantity': 0.01,
            'ai_target_pct': 6.0,
            'ai_stop_pct': 2.0,
        })
        assert pos['status'] == 'OPEN'
        assert pos['entry_price'] == 45000
        assert pos['quantity'] == 0.01
        # Winners always run: no fixed take-profit price; the pct is still recorded for tracking.
        assert pos['ai_target_price'] is None
        assert pos['ai_target_pct'] == 6.0
        assert pos['current_target_price'] is None
        assert pos['ai_stop_price'] == 44100    # 45000 * 0.98 — stop still set
        assert pos['trade_id'].startswith('TRD-')

    def test_open_position_sequential_ids(self, temp_positions):
        """Multiple opens get sequential journal IDs; live trade_ids use TRD stream."""
        from trade_journal import open_position, get_all_positions

        p1 = open_position({
            'coin_id': 'a', 'coin_name': 'A', 'coin_symbol': 'A', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1,
        })
        p2 = open_position({
            'coin_id': 'b', 'coin_name': 'B', 'coin_symbol': 'B', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1,
        })
        assert p2['id'] == p1['id'] + 1
        assert p2['trade_id'] != p1['trade_id']
        assert p1['trade_id'] == 'TRD-00001'
        assert p2['trade_id'] == 'TRD-00002'

    def test_paper_does_not_advance_live_trd(self, temp_positions):
        """Background paper_trade opens consume id/PAP slots but not live TRD numbers."""
        from trade_journal import open_position

        pap = open_position({
            'coin_id': 'solana', 'coin_name': 'Solana', 'coin_symbol': 'SOL', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1,
            'ai_target_pct': 6, 'ai_stop_pct': 2,
            'is_test': True, 'source': 'paper_trade',
        })
        live = open_position({
            'coin_id': 'bitcoin', 'coin_name': 'Bitcoin', 'coin_symbol': 'BTC', 'signal': 'BUY',
            'entry_price': 50000, 'quantity': 0.01,
            'ai_target_pct': 6, 'ai_stop_pct': 2,
        })
        assert pap['id'] == 1
        assert live['id'] == 2
        assert pap['trade_id'] == 'PAP-00001'
        assert live['trade_id'] == 'TRD-00001'

    def test_generic_test_uses_tst_stream(self, temp_positions):
        """is_test without paper_trade uses TST-* display ids."""
        from trade_journal import open_position

        t = open_position({
            'coin_id': 'x', 'coin_name': 'X', 'coin_symbol': 'X', 'signal': 'BUY',
            'entry_price': 1, 'quantity': 1,
            'ai_target_pct': 6, 'ai_stop_pct': 2,
            'is_test': True,
        })
        assert t['trade_id'] == 'TST-00001'

    def test_open_position_stores_regime_and_scenario(self, temp_positions):
        """open_position stores market_regime, quant_tier, quant_path, trading_scenario for performance by regime/scenario."""
        from trade_journal import open_position

        pos = open_position({
            'coin_id': 'sol', 'coin_name': 'Solana', 'coin_symbol': 'SOL', 'signal': 'BUY',
            'entry_price': 150, 'quantity': 1, 'ai_target_pct': 6, 'ai_stop_pct': 3,
            'market_regime': 'BULL', 'quant_tier': 'STRONG_BUY', 'quant_path': 'bull_participant',
            'trading_scenario': 'bull_participant',
        })
        assert pos.get('market_regime') == 'BULL'
        assert pos.get('quant_tier') == 'STRONG_BUY'
        assert pos.get('quant_path') == 'bull_participant'
        assert pos.get('trading_scenario') == 'bull_participant'

    def test_open_position_stores_dynamic_stop_attribution(self, temp_positions):
        """open_position persists dynamic stop profile labels for rollout attribution."""
        from trade_journal import open_position

        pos = open_position({
            'coin_id': 'tao', 'coin_name': 'BitTensor', 'coin_symbol': 'TAO', 'signal': 'BUY',
            'entry_price': 300, 'quantity': 0.1, 'ai_target_pct': 8, 'ai_stop_pct': 4.5,
            'stop_policy_profile': 'dynamic_stop_v1',
            'stop_policy_version': 1,
            'dynamic_stop_eligible': True,
            'dynamic_stop_floor_pct': 4.5,
            'dynamic_stop_floor_applied': True,
        })
        assert pos.get('stop_policy_profile') == 'dynamic_stop_v1'
        assert pos.get('stop_policy_version') == 1
        assert pos.get('dynamic_stop_eligible') is True
        assert pos.get('dynamic_stop_floor_pct') == 4.5
        assert pos.get('dynamic_stop_floor_applied') is True


class TestTradeJournalClosePosition:
    """Test closing positions and P&L."""

    def test_close_position_calculates_pnl(self, temp_positions):
        """close_position computes P&L correctly for long."""
        from trade_journal import open_position, close_position

        pos = open_position({
            'coin_id': 'eth', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH', 'signal': 'BUY',
            'entry_price': 2500, 'quantity': 0.5, 'ai_target_pct': 6, 'ai_stop_pct': 2,
        })
        closed, err = close_position(pos['id'], 2700, exit_reason='target')
        assert err is None
        assert closed['pnl_usd'] == 100  # (2700 - 2500) * 0.5
        assert closed['pnl_pct'] == 8.0  # 8% gain
        assert closed['outcome'] == 'win'

    def test_close_position_hit_ai_target_tracking(self, temp_positions):
        """hit_ai_target is True when exit >= ai_target_price for long."""
        from trade_journal import open_position, close_position

        pos = open_position({
            'coin_id': 'x', 'coin_name': 'X', 'coin_symbol': 'X', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1, 'ai_target_pct': 10, 'ai_stop_pct': 2,
        })
        # ai_target_price = 110
        closed, _ = close_position(pos['id'], 115, exit_reason='target')
        assert closed['hit_ai_target'] is True
        assert closed['outcome'] == 'win'

    def test_close_position_miss_ai_target(self, temp_positions):
        """hit_ai_target is False when exit < ai_target_price."""
        from trade_journal import open_position, close_position

        pos = open_position({
            'coin_id': 'y', 'coin_name': 'Y', 'coin_symbol': 'Y', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1, 'ai_target_pct': 10, 'ai_stop_pct': 2,
        })
        closed, _ = close_position(pos['id'], 105, exit_reason='manual')
        assert closed['hit_ai_target'] is False
        assert closed['outcome'] == 'win'

    def test_close_position_with_exit_time_and_quantity_at_close(self, temp_positions):
        """close_position accepts exit_time, exit_fee_usd, quantity_at_close for exchange-sourced closes."""
        from trade_journal import open_position, close_position

        pos = open_position({
            'coin_id': 'z', 'coin_name': 'Z', 'coin_symbol': 'Z', 'signal': 'BUY',
            'entry_price': 30.66, 'quantity': 0,
            'ai_target_pct': 6, 'ai_stop_pct': 2,
        })
        closed, err = close_position(
            pos['id'], 34.16, exit_reason='exchange_sold',
            notes='Order 123 filled',
            exit_time='2026-03-09 21:24:03',
            exit_fee_usd=0.01,
            quantity_at_close=0.025,
        )
        assert err is None
        assert closed['exit_time'] == '2026-03-09 21:24:03'
        assert closed['exit_fee_usd'] == 0.01
        # pnl_usd is net of fees (gross - exit_fee_usd)
        gross = round((34.16 - 30.66) * 0.025, 4)
        assert closed['pnl_usd'] == round(gross - 0.01, 4)
        assert closed['gross_pnl_usd'] == gross
        assert closed['outcome'] == 'win'


class TestTradeJournalUpdatePosition:
    """Test updating open positions."""

    def test_update_targets_adjusts_current_target(self, temp_positions):
        """update_targets changes current_target_pct and current_target_price."""
        from trade_journal import open_position, update_targets, get_position_by_id

        pos = open_position({
            'coin_id': 'z', 'coin_name': 'Z', 'coin_symbol': 'Z', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1, 'ai_target_pct': 6, 'ai_stop_pct': 2,
        })
        updated, err = update_targets(pos['id'], new_target_pct=10, new_stop_pct=3)
        assert err is None
        assert updated['current_target_pct'] == 10
        assert updated['current_target_price'] == 110
        assert updated['current_stop_pct'] == 3
        assert updated['ai_target_pct'] == 6  # AI locked, unchanged

    def test_update_position_quantity_recomputes_capital(self, temp_positions):
        """update_position with quantity updates capital_usd."""
        from trade_journal import open_position, update_position

        pos = open_position({
            'coin_id': 'w', 'coin_name': 'W', 'coin_symbol': 'W', 'signal': 'BUY',
            'entry_price': 50, 'quantity': 2, 'ai_target_pct': 6, 'ai_stop_pct': 2,
        })
        updated, err = update_position(pos['id'], quantity=4)
        assert err is None
        assert updated['quantity'] == 4
        assert updated['capital_usd'] == 200

    def test_update_position_accepts_current_stop_pct_from_updates(self, temp_positions):
        """update_position applies current_stop_pct from updates dict (e.g. exit_manager breakeven)."""
        from trade_journal import open_position, update_position, get_position_by_id

        pos = open_position({
            'coin_id': 'near', 'coin_name': 'NEAR', 'coin_symbol': 'NEAR', 'signal': 'BUY',
            'entry_price': 10.0, 'quantity': 1, 'ai_target_pct': 6, 'ai_stop_pct': 5,
        })
        # Schema-aligned key used by exit_manager; journal should apply it
        updated, err = update_position(pos['id'], {'current_stop_pct': 0.3})
        assert err is None
        assert updated['current_stop_pct'] == 0.3
        assert updated['current_stop_price'] == round(10.0 * (1 - 0.3 / 100), 8)
        # Reload from store to confirm persistence
        reloaded = get_position_by_id(pos['id'])
        assert reloaded['current_stop_pct'] == 0.3


class TestTradeJournalStats:
    """Test calculate_stats and AI target hit rate."""

    def test_calculate_stats_ai_target_hit_rate(self, temp_positions):
        """calculate_stats includes ai_target_hit_rate from closed positions."""
        from trade_journal import open_position, close_position, calculate_stats

        p1 = open_position({
            'coin_id': 'a', 'coin_name': 'A', 'coin_symbol': 'A', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1, 'ai_target_pct': 10, 'ai_stop_pct': 2,
        })
        p2 = open_position({
            'coin_id': 'b', 'coin_name': 'B', 'coin_symbol': 'B', 'signal': 'BUY',
            'entry_price': 100, 'quantity': 1, 'ai_target_pct': 10, 'ai_stop_pct': 2,
        })
        close_position(p1['id'], 115, exit_reason='target')  # hit
        close_position(p2['id'], 105, exit_reason='manual')  # miss
        from trade_journal import get_all_positions
        positions = get_all_positions()
        stats = calculate_stats(positions)
        assert stats['closed_positions'] == 2
        assert stats['ai_target_hit_rate'] == 50.0  # 1 of 2 hit
        assert stats['wins'] == 2
        assert 'total_pnl_usd' in stats

    def test_calculate_stats_profit_factor_all_wins(self, temp_positions):
        """Closed positions all outcome 'win' -> profit_factor 0."""
        from trade_journal import calculate_stats

        closed_all_wins = [
            {'id': 1, 'status': 'CLOSED', 'outcome': 'win', 'pnl_usd': 100.0, 'pnl_pct': 10.0},
            {'id': 2, 'status': 'CLOSED', 'outcome': 'win', 'pnl_usd': 50.0, 'pnl_pct': 5.0},
        ]
        positions = closed_all_wins
        stats = calculate_stats(positions)
        assert stats['profit_factor'] == 0.0
        assert stats['wins'] == 2
        assert stats['losses'] == 0

    def test_calculate_stats_expectancy_matches_formula(self, temp_positions):
        """2 wins, 1 loss with known pnl_usd -> expectancy_usd equals wr*avg_win - lr*avg_loss."""
        from trade_journal import calculate_stats

        # 2 wins: 100, 50 -> avg_win_usd = 75. 1 loss: -60 -> avg_loss_usd = 60.
        # wr = 2/3, lr = 1/3. expectancy = (2/3)*75 - (1/3)*60 = 50 - 20 = 30.
        closed = [
            {'id': 1, 'status': 'CLOSED', 'outcome': 'win', 'pnl_usd': 100.0, 'pnl_pct': 10.0},
            {'id': 2, 'status': 'CLOSED', 'outcome': 'win', 'pnl_usd': 50.0, 'pnl_pct': 5.0},
            {'id': 3, 'status': 'CLOSED', 'outcome': 'loss', 'pnl_usd': -60.0, 'pnl_pct': -6.0},
        ]
        stats = calculate_stats(closed)
        wr = 2 / 3
        avg_win_usd = 75.0
        avg_loss_usd = 60.0
        expected_expectancy = (wr * avg_win_usd) - ((1 - wr) * avg_loss_usd)
        assert stats['expectancy_usd'] == round(expected_expectancy, 2)
        assert stats['win_rate_pct'] == round(wr * 100, 1)

    def test_calculate_stats_handles_missing_pnl_pct(self, temp_positions):
        """One closed position without pnl_pct -> no crash, excluded from pct averages."""
        from trade_journal import calculate_stats

        # One win with pnl_pct, one win without pnl_pct. avg_win_pct should use only the one with pnl_pct.
        closed = [
            {'id': 1, 'status': 'CLOSED', 'outcome': 'win', 'pnl_usd': 20.0, 'pnl_pct': 4.0},
            {'id': 2, 'status': 'CLOSED', 'outcome': 'win', 'pnl_usd': 10.0, 'pnl_pct': None},
        ]
        stats = calculate_stats(closed)
        assert stats['avg_win_pct'] == 4.0  # only the position with pnl_pct=4.0
        assert stats['wins'] == 2
        assert stats['total_pnl_usd'] == 30.0


class TestExportHeaders:
    """Export CSV uses quant-first column labels (not AI)."""

    def test_export_csv_headers_use_quant_labels(self, temp_positions):
        """CSV export header row uses Quant Target %, Hit Target?, Signal Timeframe, Signal Confidence."""
        from trade_journal import export_csv

        csv_data, _ = export_csv()
        lines = csv_data.strip().split('\n')
        assert len(lines) >= 1
        header = lines[0]
        assert 'Quant Target %' in header
        assert 'Quant Stop %' in header
        assert 'Hit Target?' in header
        assert 'Signal Timeframe' in header
        assert 'Signal Confidence' in header
        assert 'AI Target %' not in header
        assert 'Hit AI Target?' not in header
        assert 'AI Timeframe' not in header
        assert 'AI Confidence' not in header


class TestEnrichOpenPositions:
    """Regression: serializing an open 'winners run' position (no fixed target) must not crash.

    Guards the bug where enrich_open_positions called round(target_distance) with
    target_distance=None for no-target positions, 500-ing the whole /api/journal/positions
    endpoint and emptying the dashboard positions tab.
    """

    def test_enrich_no_target_position_does_not_crash(self, temp_positions):
        from trade_journal import open_position, enrich_open_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        # Sanity: it's a runner — no fixed target.
        assert pos["current_target_price"] is None

        enriched = enrich_open_positions([pos], {"bitcoin": 112.0})
        e = enriched[0]
        # No crash; live P&L computed; target distance is None (no target), stop distance numeric.
        assert e["current_price"] == 112.0
        assert e["unrealized_pct"] == 12.0
        assert e["target_distance"] is None
        assert e["stop_distance"] is not None

    def test_enrich_legacy_position_with_target_still_computes_distance(self, temp_positions):
        from trade_journal import open_position, update_targets, enrich_open_positions

        pos = open_position({
            "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
            "signal": "BUY", "entry_price": 100, "quantity": 1,
            "ai_target_pct": 6, "ai_stop_pct": 2.5,
        })
        # A legacy/manual target still yields a numeric target_distance.
        update_targets(pos["id"], new_target_pct=20, new_stop_pct=2.5)
        from trade_journal import get_position_by_id
        pos = get_position_by_id(pos["id"])
        enriched = enrich_open_positions([pos], {"bitcoin": 110.0})
        assert enriched[0]["target_distance"] is not None
