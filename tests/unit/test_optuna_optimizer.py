# tests/unit/test_optuna_optimizer.py — Optuna Bayesian threshold optimization tests
# All tests use mocked replay (no API calls) and tmp_path (Windows-safe).

import gc
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def optuna_mod():
    """Import optuna_optimizer lazily."""
    import optuna_optimizer
    return optuna_optimizer


@pytest.fixture
def thresholds_mod():
    import thresholds
    return thresholds


@pytest.fixture
def mock_replay_result():
    """Realistic replay summary for mocking."""
    return {
        'trades': [{'pnl_pct': 3.0}] * 10,
        'summary': {
            'total_trades': 59,
            'wins': 30,
            'losses': 29,
            'win_rate_pct': 50.8,
            'profit_factor': 1.25,
            'max_drawdown_pct': 15.0,
            'expectancy_pct': 0.5,
        },
        'signals_generated': 100,
        'coins_screened': 500,
    }


def teardown_function():
    gc.collect()


class TestSearchSpace:
    def test_creates_all_params_from_bounds(self, optuna_mod):
        """Search space should match OPTIMIZER_BOUNDS exactly."""
        import optuna
        from thresholds import OPTIMIZER_BOUNDS

        study = optuna.create_study(direction='maximize')
        trial = study.ask()
        params = optuna_mod.create_search_space(trial)

        assert set(params.keys()) == set(OPTIMIZER_BOUNDS.keys())

    def test_int_params_are_int(self, optuna_mod):
        import optuna
        from thresholds import OPTIMIZER_BOUNDS

        study = optuna.create_study(direction='maximize')
        trial = study.ask()
        params = optuna_mod.create_search_space(trial)

        for name, (lo, hi) in OPTIMIZER_BOUNDS.items():
            if isinstance(lo, int) and isinstance(hi, int):
                assert isinstance(params[name], int), f"{name} should be int"

    def test_float_params_are_float(self, optuna_mod):
        import optuna
        from thresholds import OPTIMIZER_BOUNDS

        study = optuna.create_study(direction='maximize')
        trial = study.ask()
        params = optuna_mod.create_search_space(trial)

        for name, (lo, hi) in OPTIMIZER_BOUNDS.items():
            if not (isinstance(lo, int) and isinstance(hi, int)):
                assert isinstance(params[name], float), f"{name} should be float"

    def test_params_within_bounds(self, optuna_mod):
        import optuna
        from thresholds import OPTIMIZER_BOUNDS

        study = optuna.create_study(direction='maximize')
        trial = study.ask()
        params = optuna_mod.create_search_space(trial)

        for name, (lo, hi) in OPTIMIZER_BOUNDS.items():
            assert lo <= params[name] <= hi, f"{name}={params[name]} not in [{lo}, {hi}]"


class TestReplayObjective:
    def test_returns_profit_factor(self, optuna_mod, mock_replay_result):
        import optuna

        with patch('historical_replay.run_historical_replay', return_value=mock_replay_result) as mock_replay:
            study = optuna.create_study(direction='maximize')
            trial = study.ask()
            value = optuna_mod.replay_objective(trial, days=90)

        assert value == 1.25
        # Verify threshold_overrides was passed
        call_kwargs = mock_replay.call_args[1]
        assert 'threshold_overrides' in call_kwargs
        assert isinstance(call_kwargs['threshold_overrides'], dict)
        assert call_kwargs['end_offset_days'] == 27  # 30% holdout of 90d
        assert call_kwargs['days'] == 63  # train window length (90 - 27)

    def test_returns_zero_on_failure(self, optuna_mod):
        import optuna

        with patch('historical_replay.run_historical_replay', side_effect=Exception("fail")):
            study = optuna.create_study(direction='maximize')
            trial = study.ask()
            value = optuna_mod.replay_objective(trial, days=90)

        assert value == 0.0


class TestMultiObjective:
    def test_returns_three_values(self, optuna_mod, mock_replay_result):
        import optuna

        with patch('historical_replay.run_historical_replay', return_value=mock_replay_result) as mock_replay:
            study = optuna.create_study(directions=['maximize', 'maximize', 'maximize'])
            trial = study.ask()
            values = optuna_mod.replay_objective_multi(trial, days=90)

        call_kwargs = mock_replay.call_args[1]
        assert call_kwargs['end_offset_days'] == 27
        assert call_kwargs['days'] == 63

        assert len(values) == 3
        assert values[0] == 50.8   # win_rate
        assert values[1] == -15.0  # -max_drawdown
        assert values[2] == 1.25   # profit_factor


class TestThresholdOverride:
    def test_override_applied_during_context(self, thresholds_mod):
        original = thresholds_mod.get_threshold('DIP_MIN_SCORE')
        with thresholds_mod.threshold_override({'DIP_MIN_SCORE': 99}):
            assert thresholds_mod.get_threshold('DIP_MIN_SCORE') == 99
        # After context: back to original
        assert thresholds_mod.get_threshold('DIP_MIN_SCORE') == original

    def test_override_thread_safe(self, thresholds_mod):
        """Overrides in one thread don't affect another."""
        results = {}

        def thread_fn(name, override_val):
            with thresholds_mod.threshold_override({'DIP_MIN_SCORE': override_val}):
                results[name] = thresholds_mod.get_threshold('DIP_MIN_SCORE')

        t1 = threading.Thread(target=thread_fn, args=('t1', 111))
        t2 = threading.Thread(target=thread_fn, args=('t2', 222))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results['t1'] == 111
        assert results['t2'] == 222

    def test_non_overridden_keys_unchanged(self, thresholds_mod):
        original = thresholds_mod.get_threshold('BEAR_REGIME_MIN_SCORE')
        with thresholds_mod.threshold_override({'DIP_MIN_SCORE': 99}):
            assert thresholds_mod.get_threshold('BEAR_REGIME_MIN_SCORE') == original


class TestRunOptunaStudy:
    def test_study_completes_with_mock_replay(self, optuna_mod, mock_replay_result, tmp_path):
        with patch('historical_replay.run_historical_replay', return_value=mock_replay_result), \
             patch.object(optuna_mod, 'STUDIES_DIR', tmp_path / 'studies'):

            result = optuna_mod.run_optuna_study(n_trials=3, days=90)

        assert result['success'] is True
        assert result['n_completed'] == 3
        assert 'best_params' in result
        assert 'best_metrics' in result
        assert 'changes' in result

    def test_study_saves_json(self, optuna_mod, mock_replay_result, tmp_path):
        studies_dir = tmp_path / 'studies'
        with patch('historical_replay.run_historical_replay', return_value=mock_replay_result), \
             patch.object(optuna_mod, 'STUDIES_DIR', studies_dir):

            optuna_mod.run_optuna_study(n_trials=3)

        json_files = list(studies_dir.glob('*.json'))
        assert len(json_files) == 1
        with open(json_files[0]) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert data[0]['success'] is True


class TestApplyResults:
    def test_apply_disarmed_never_writes(self, optuna_mod, tmp_path, monkeypatch):
        """Audit F2: apply_optuna_results is permanently disarmed — even a study
        showing large simulated improvement must not touch live_thresholds.json."""
        thresholds_path = str(tmp_path / 'live_thresholds.json')
        original = {'DIP_MIN_SCORE': 68, 'BEAR_REGIME_MIN_SCORE': 78}
        with open(thresholds_path, 'w') as f:
            json.dump(original, f)

        import thresholds as thresholds_mod
        monkeypatch.setattr(thresholds_mod, 'LIVE_THRESHOLDS_PATH', thresholds_path)

        study_results = {
            'best_metrics': {'profit_factor': 1.30},
            'baseline_metrics': {'profit_factor': 1.20},
            'changes': [
                {'param': 'DIP_MIN_SCORE', 'current': 68, 'proposed': 65, 'delta': -3},
                {'param': 'BEAR_REGIME_MIN_SCORE', 'current': 78, 'proposed': 75, 'delta': -3},
                {'param': 'BULL_REGIME_MIN_SCORE', 'current': 62, 'proposed': 60, 'delta': -2},
                {'param': 'SIDEWAYS_REGIME_MIN_SCORE', 'current': 75, 'proposed': 72, 'delta': -3},
            ],
        }

        applied = optuna_mod.apply_optuna_results(study_results, max_changes=2)

        assert applied == []
        with open(thresholds_path) as f:
            saved = json.load(f)
        assert saved == original

    def test_no_changes_returns_empty(self, optuna_mod):
        result = optuna_mod.apply_optuna_results({'changes': [], 'best_metrics': {'profit_factor': 1.0}, 'baseline_metrics': {'profit_factor': 1.0}})
        assert result == []


class TestLiveThresholdsNeverModifiedDuringTrial:
    def test_replay_objective_uses_overrides_not_file(self, optuna_mod, mock_replay_result):
        """Verify replay is called with threshold_overrides, not file modifications."""
        import optuna

        with patch('historical_replay.run_historical_replay', return_value=mock_replay_result) as mock_replay:
            study = optuna.create_study(direction='maximize')
            trial = study.ask()
            optuna_mod.replay_objective(trial, days=90)

        call_kwargs = mock_replay.call_args[1]
        assert call_kwargs.get('threshold_overrides') is not None
        assert isinstance(call_kwargs['threshold_overrides'], dict)
        assert len(call_kwargs['threshold_overrides']) > 0
        assert call_kwargs.get('end_offset_days', 0) > 0
