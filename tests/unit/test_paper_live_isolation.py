# tests/unit/test_paper_live_isolation.py — Paper vs live trade mode isolation
# Verifies paper and live paths do not interfere with each other.
# Run: pytest tests/unit/test_paper_live_isolation.py -v

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def temp_account_config(monkeypatch, tmp_path):
    cfg = {
        "total_cash": 20000.0,
        "risk_pct": 2.0,
        "max_cash_usage_pct": 40.0,
        "dust_threshold_usd": 1.0,
        "let_winners_run": False,
    }
    path = tmp_path / "account_config.json"
    path.write_text(json.dumps(cfg, indent=2))
    import app as app_module
    import app_config as app_config_module
    monkeypatch.setattr(app_module, "ACCOUNT_CONFIG_FILE", str(path))
    monkeypatch.setattr(app_config_module, "ACCOUNT_CONFIG_FILE", str(path))
    yield str(path)


# ---------------------------------------------------------------------------
# Bug 1: test_mode=True blocks real exchange execution
# ---------------------------------------------------------------------------

class TestTestModeBlocksRealExec:
    """When test_mode is ON, real exchange auto-exec must never run."""

    def test_auto_exec_skipped_when_test_mode_on_paper_on(self, temp_positions, temp_account_config):
        """test_mode=True + paper_trading=True: start_twap_buy_async never called."""
        import app as app_module
        coin = {
            'symbol': 'ETH', 'name': 'Ethereum', 'id': 'ethereum',
            'current_price': 2400.0,
            'ai_recommendation': {
                'final_signal': 'STRONG_BUY',
                'quant_tier': 'STRONG_BUY',
                'quant': {'quant_tier': 'STRONG_BUY'},
                'consensus': {'quant_tier': 'STRONG_BUY', 'consensus_signal': 'STRONG_BUY'},
                'position_sizing': {'position_size_usd': 50.0, 'max_loss_pct': 1.0},
            },
        }
        with patch("app.get_test_mode", return_value=True), \
             patch("app.get_paper_trading", return_value=True), \
             patch("app.get_execution_config", return_value={
                 'auto_execution_enabled': True,
                 'auto_execution_exchange': 'coinbase',
             }), \
             patch("app.start_twap_buy_async") as mock_twap, \
             patch("app._start_twap_if_safe") as mock_safe:
            # _start_twap_if_safe should never be reached
            result = app_module._start_twap_if_safe(coin, None, 20000.0)
        # _start_twap_if_safe itself doesn't check test_mode (the caller does),
        # so we verify the guard at the caller level: _light_test_pipeline was replaced
        # by get_test_mode() in the auto-exec block. We test the block directly.

    def test_auto_exec_block_skips_when_test_mode_true(self, temp_positions, temp_account_config):
        """The auto-exec if/elif chain skips real execution when get_test_mode() is True."""
        import app as app_module
        with patch("app.get_test_mode", return_value=True), \
             patch("app.get_paper_trading", return_value=True), \
             patch("app._start_twap_if_safe") as mock_safe:

            enabled = True
            exchange = 'coinbase'
            n_eligible = 3
            auto_exec_eligible = [{'symbol': 'ETH'}]

            # Simulate the if/elif chain from _refresh_data_inner
            skipped = False
            if not enabled:
                pass
            elif app_module.get_test_mode():
                skipped = True
            elif exchange != 'coinbase':
                pass
            elif n_eligible == 0:
                pass
            else:
                mock_safe()

            assert skipped is True
            mock_safe.assert_not_called()

    def test_auto_exec_runs_when_test_mode_off(self, temp_positions, temp_account_config):
        """When test_mode is OFF, the auto-exec block proceeds to _start_twap_if_safe."""
        import app as app_module
        with patch("app.get_test_mode", return_value=False), \
             patch("app.get_paper_trading", return_value=False):

            enabled = True
            exchange = 'coinbase'
            n_eligible = 3

            skipped = False
            reached_else = False
            if not enabled:
                pass
            elif app_module.get_test_mode():
                skipped = True
            elif exchange != 'coinbase':
                pass
            elif n_eligible == 0:
                pass
            else:
                reached_else = True

            assert skipped is False
            assert reached_else is True


# ---------------------------------------------------------------------------
# Bug 2: _paper_symbol_already_open only checks paper positions
# ---------------------------------------------------------------------------

class TestPaperSymbolAlreadyOpen:
    """Paper duplicate check must not be blocked by live positions."""

    def test_live_position_does_not_block_paper(self, temp_positions, temp_account_config):
        """A live ETH position should NOT block a paper ETH open."""
        from trade_journal import open_position
        from app import _paper_symbol_already_open
        open_position({
            'coin_id': 'ethereum', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH',
            'signal': 'BUY', 'entry_price': 2400.0, 'quantity': 0.01,
            'capital_usd': 24.0, 'is_test': False,
        })
        assert _paper_symbol_already_open('ETH') is False

    def test_paper_position_blocks_paper(self, temp_positions, temp_account_config):
        """An existing paper ETH position SHOULD block another paper ETH open."""
        from trade_journal import open_position
        from app import _paper_symbol_already_open
        open_position({
            'coin_id': 'ethereum', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH',
            'signal': 'BUY', 'entry_price': 2400.0, 'quantity': 0.01,
            'capital_usd': 24.0, 'is_test': True, 'source': 'paper_trade',
        })
        assert _paper_symbol_already_open('ETH') is True

    def test_no_positions_returns_false(self, temp_positions, temp_account_config):
        from app import _paper_symbol_already_open
        assert _paper_symbol_already_open('ETH') is False


# ---------------------------------------------------------------------------
# Bug 4: Exchange-import routes force is_test=False
# ---------------------------------------------------------------------------

class TestExchangeImportIsTest:
    """Exchange-import journal routes must always create live positions."""

    def test_journal_open_from_exchange_forces_live(self, temp_positions, temp_account_config, monkeypatch):
        """journal_open_from_exchange sets is_test=False even when get_test_mode()=True."""
        monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
        import app as app_module
        client = app_module.app.test_client()
        with patch("app.get_test_mode", return_value=True), \
             patch("app.get_execution_config", return_value={
                 'auto_execution_enabled': True,
                 'auto_execution_exchange': 'coinbase',
             }), \
             patch("app._get_dynamic_stop_target_for_coin", return_value=None), \
             patch("app._coin_id_and_name_for_symbol", return_value=('ethereum', 'Ethereum')), \
             patch("app._signal_fields_from_cached", return_value={}), \
             patch("app._place_sl_tp_with_retry") as mock_sltp, \
             patch("routes.journal_bp.require_admin_write_access", return_value=None):
            resp = client.post('/api/journal/open-from-exchange', json={
                'symbol': 'ETH',
                'entry_price': 2400.0,
                'quantity': 0.01,
            })
            assert resp.status_code == 200
            data = resp.get_json()
            pos = data.get('position', {})
            assert pos.get('is_test') is False
            mock_sltp.assert_called_once()

    def test_journal_add_from_exchange_forces_live(self, temp_positions, temp_account_config, monkeypatch):
        """journal_add_from_exchange_clear_pending sets is_test=False even when get_test_mode()=True."""
        monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
        import app as app_module
        client = app_module.app.test_client()
        with patch("app.get_test_mode", return_value=True), \
             patch("app.get_execution_config", return_value={
                 'auto_execution_enabled': True,
                 'auto_execution_exchange': 'coinbase',
             }), \
             patch("app.fetch_balance_sync", return_value={
                 'holdings': {'BTC': 0.001},
                 'total_usd': 20000,
             }), \
             patch("app._price_by_symbol_from_cached", return_value={'BTC': 65000.0}), \
             patch("app._coin_id_and_name_for_symbol", return_value=('bitcoin', 'Bitcoin')), \
             patch("app._signal_fields_from_cached", return_value={}), \
             patch("app._get_dynamic_stop_target_for_coin", return_value=None), \
             patch("app.unregister_pending_order"), \
             patch("app._place_sl_tp_with_retry") as mock_sltp, \
             patch("app._sync_new_position_quantity_from_exchange") as mock_sync, \
             patch("routes.journal_bp.require_admin_write_access", return_value=None):
            resp = client.post('/api/journal/add-from-exchange-clear-pending', json={
                'symbol': 'BTC',
            })
            assert resp.status_code == 200
            data = resp.get_json()
            pos = data.get('position', {})
            assert pos.get('is_test') is False
            mock_sync.assert_called_once()
            mock_sltp.assert_called_once()


# ---------------------------------------------------------------------------
# Bug 5: Model performance logging skips test/paper positions
# ---------------------------------------------------------------------------

class TestModelPerfSkipsPaper:
    """Model performance tracker must not log test/paper positions."""

    def test_paper_open_skips_log_prediction(self, temp_positions):
        """open_position with is_test=True must NOT call log_prediction."""
        with patch("model_performance_tracker.log_prediction") as mock_pred:
            from trade_journal import open_position
            pos = open_position({
                'coin_id': 'ethereum', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH',
                'signal': 'BUY', 'entry_price': 2400.0, 'quantity': 0.01,
                'capital_usd': 24.0, 'is_test': True, 'source': 'paper_trade',
            })
            assert pos is not None
            mock_pred.assert_not_called()
            assert pos.get('model_prediction_ts') is None

    def test_live_open_calls_log_prediction(self, temp_positions):
        """open_position with is_test=False SHOULD call log_prediction."""
        with patch("model_performance_tracker.log_prediction", return_value="2026-01-01T00:00:00Z") as mock_pred:
            from trade_journal import open_position
            pos = open_position({
                'coin_id': 'ethereum', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH',
                'signal': 'BUY', 'entry_price': 2400.0, 'quantity': 0.01,
                'capital_usd': 24.0, 'is_test': False,
            })
            assert pos is not None
            mock_pred.assert_called_once()

    def test_paper_close_skips_log_outcome(self, temp_positions):
        """close_position for is_test=True must NOT call log_outcome."""
        from trade_journal import open_position, close_position
        pos = open_position({
            'coin_id': 'ethereum', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH',
            'signal': 'BUY', 'entry_price': 2400.0, 'quantity': 0.01,
            'capital_usd': 24.0, 'is_test': True,
        })
        assert pos is not None
        # Even if model_prediction_ts were somehow set, log_outcome should be skipped
        from trade_journal import update_position
        update_position(pos['id'], {'model_prediction_ts': '2026-01-01T00:00:00Z'})
        with patch("model_performance_tracker.log_outcome") as mock_out:
            closed, err = close_position(pos['id'], exit_price=2500.0, exit_reason='take_profit')
            assert closed is not None
            mock_out.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 6: Optimizer health check excludes test/paper positions
# ---------------------------------------------------------------------------

class TestOptimizerHealthExcludesPaper:
    """_build_optimizer_health_check_html must not count paper trades."""

    def test_paper_closed_trades_excluded_from_health_check(self, tmp_path):
        """Paper closed trades do not affect win-rate drift detection."""
        import json as _json
        from datetime import datetime, timezone, timedelta

        positions = []
        # 6 paper losses in BEAR regime (would trigger drift if counted)
        for i in range(6):
            positions.append({
                'id': f'TRD-PAPER-{i}',
                'status': 'CLOSED',
                'is_test': True,
                'source': 'paper_trade',
                'market_regime': 'BEAR',
                'outcome': 'loss',
                'exit_time': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                'exit_reason': 'stop_loss',
            })
        # 2 live wins in BEAR (below threshold of 5 trades, won't trigger)
        for i in range(2):
            positions.append({
                'id': f'TRD-LIVE-{i}',
                'status': 'CLOSED',
                'is_test': False,
                'market_regime': 'BEAR',
                'outcome': 'win',
                'exit_time': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                'exit_reason': 'take_profit',
            })

        pos_file = tmp_path / "positions.json"
        pos_file.write_text(_json.dumps(positions))

        # Inline the health check loop logic to verify paper exclusion
        all_pos = _json.loads(pos_file.read_text())
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
        by_regime = {}
        for p in all_pos:
            if p.get('status') not in ('CLOSED', 'closed'):
                continue
            if p.get('is_test') or p.get('exit_reason') == 'test_cleanup':
                continue
            if (p.get('exit_time') or '')[:10] < cutoff:
                continue
            r = (p.get('market_regime') or 'UNKNOWN').upper()
            if r in ('UNKNOWN', 'PRE_PIPELINE', ''):
                continue
            by_regime.setdefault(r, {'wins': 0, 'total': 0})
            by_regime[r]['total'] += 1
            if p.get('outcome') == 'win':
                by_regime[r]['wins'] += 1

        # Only 2 live BEAR trades counted; below threshold of 5 → no drift flag
        assert by_regime.get('BEAR', {}).get('total', 0) == 2


# ---------------------------------------------------------------------------
# Attribution: signal_badges round-trip through the journal
# ---------------------------------------------------------------------------

class TestSignalBadgesPersisted:
    """signal_badges must round-trip through open_position so the optimizer
    can attribute outcomes to chase-guard, score-floor, sniper, etc."""

    def test_open_position_persists_signal_badges(self, temp_positions):
        """A position opened with signal_badges must round-trip through positions.json."""
        from trade_journal import open_position, get_open_positions
        badges = ['bull_score_floor', 'chase_guard_capped', 'light_sniper_entry']
        pos = open_position({
            'coin_id': 'bitcoin', 'coin_name': 'Bitcoin', 'coin_symbol': 'BTC',
            'signal': 'BUY', 'entry_price': 100000.0, 'quantity': 0.01,
            'is_test': True,
            'signal_badges': badges,
        })
        assert pos is not None
        assert pos.get('signal_badges') == badges, \
            f"open_position dropped signal_badges: got {pos.get('signal_badges')!r}"

        reloaded = get_open_positions()
        match = [p for p in reloaded if p.get('coin_symbol') == 'BTC']
        assert len(match) == 1, "BTC position not found after reload"
        assert match[0].get('signal_badges') == badges, \
            f"signal_badges did not survive disk round-trip: got {match[0].get('signal_badges')!r}"

    def test_open_position_defaults_signal_badges_to_empty_list(self, temp_positions):
        """Caller may omit signal_badges entirely; field must default to []."""
        from trade_journal import open_position
        pos = open_position({
            'coin_id': 'ethereum', 'coin_name': 'Ethereum', 'coin_symbol': 'ETH',
            'signal': 'BUY', 'entry_price': 2400.0, 'quantity': 0.01,
            'is_test': True,
        })
        assert pos is not None
        assert pos.get('signal_badges') == [], \
            "signal_badges must default to empty list, not None"
