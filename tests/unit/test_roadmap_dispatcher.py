"""Tests for roadmap_dispatcher — verifies flag gating, isolation, and Optuna safety."""

from unittest.mock import patch, MagicMock
import pytest


def _make_state(flags: dict) -> dict:
    """Build a minimal roadmap state with the given state_flags."""
    return {
        'version': 1,
        'last_evaluated_at': None,
        'last_applied_at': None,
        'feature_states': {},
        'pass_streak_weeks': {},
        'pending_approvals': [],
        'state_flags': flags,
        'last_metrics': {},
    }


class TestFlagGating:
    """Only enabled modules should be called."""

    def test_shap_runs_when_enabled(self):
        mock_shap = MagicMock(return_value={'success': True, 'n_samples': 120})
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({'shap_weekly_enabled': True})):
            with patch.dict('sys.modules', {'shap_analysis': MagicMock(run_shap_analysis=mock_shap)}):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        assert 'shap' in result
        assert result['shap']['success'] is True
        mock_shap.assert_called_once()

    def test_vectorbt_runs_when_enabled(self):
        mock_vbt = MagicMock(return_value={'success': True, 'best_combo': {'stop_pct': 6, 'target_pct': 12}})
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({'vectorbt_weekly_enabled': True})):
            with patch.dict('sys.modules', {'vectorbt_sweeps': MagicMock(run_parameter_sweep=mock_vbt)}):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        assert 'vectorbt' in result
        assert result['vectorbt']['success'] is True
        mock_vbt.assert_called_once()

    def test_nothing_runs_when_all_flags_false(self):
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({})):
            from roadmap_dispatcher import run_roadmap_gated_analyses
            result = run_roadmap_gated_analyses()
        assert result == {}

    def test_nothing_runs_when_flags_explicitly_false(self):
        flags = {
            'shap_weekly_enabled': False,
            'vectorbt_weekly_enabled': False,
            'optuna_weekly_enabled': False,
        }
        with patch('roadmap_dispatcher.load_state', return_value=_make_state(flags)):
            from roadmap_dispatcher import run_roadmap_gated_analyses
            result = run_roadmap_gated_analyses()
        assert result == {}

    def test_only_enabled_modules_run(self):
        """Enable SHAP only — VectorBT and Optuna should NOT be called."""
        mock_shap = MagicMock(return_value={'success': True})
        mock_vbt = MagicMock(return_value={'success': True})
        flags = {'shap_weekly_enabled': True, 'vectorbt_weekly_enabled': False}
        with patch('roadmap_dispatcher.load_state', return_value=_make_state(flags)):
            with patch.dict('sys.modules', {
                'shap_analysis': MagicMock(run_shap_analysis=mock_shap),
                'vectorbt_sweeps': MagicMock(run_parameter_sweep=mock_vbt),
            }):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        assert 'shap' in result
        assert 'vectorbt' not in result
        mock_shap.assert_called_once()
        mock_vbt.assert_not_called()


class TestIsolation:
    """One module failing should not block others."""

    def test_shap_failure_does_not_block_vectorbt(self):
        mock_vbt = MagicMock(return_value={'success': True, 'best_combo': {'stop_pct': 6, 'target_pct': 12}})
        flags = {'shap_weekly_enabled': True, 'vectorbt_weekly_enabled': True}
        with patch('roadmap_dispatcher.load_state', return_value=_make_state(flags)):
            with patch.dict('sys.modules', {
                'shap_analysis': MagicMock(run_shap_analysis=MagicMock(side_effect=RuntimeError("SHAP crashed"))),
                'vectorbt_sweeps': MagicMock(run_parameter_sweep=mock_vbt),
            }):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        assert result['shap']['success'] is False
        assert 'error' in result['shap']
        assert result['vectorbt']['success'] is True
        mock_vbt.assert_called_once()


class TestOptunaSafety:
    """Optuna apply should only happen when improvement is sufficient."""

    def test_optuna_does_not_apply_below_threshold(self):
        mock_study = MagicMock(return_value={'success': True, 'improvement_pct': 1.5, 'changes': []})
        mock_apply = MagicMock(return_value=[])
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({'optuna_weekly_enabled': True})):
            with patch.dict('sys.modules', {
                'optuna_optimizer': MagicMock(
                    run_optuna_study=mock_study,
                    apply_optuna_results=mock_apply,
                ),
            }):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        assert result['optuna']['success'] is True
        assert 'optuna_applied' not in result
        mock_apply.assert_not_called()

    def test_optuna_applies_above_threshold(self):
        mock_study = MagicMock(return_value={'success': True, 'improvement_pct': 3.5, 'changes': [{'param': 'X'}]})
        mock_apply = MagicMock(return_value=[{'param': 'X', 'applied': True}])
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({'optuna_weekly_enabled': True})):
            with patch.dict('sys.modules', {
                'optuna_optimizer': MagicMock(
                    run_optuna_study=mock_study,
                    apply_optuna_results=mock_apply,
                ),
            }):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        assert result['optuna']['success'] is True
        assert 'optuna_applied' in result
        mock_apply.assert_called_once_with(mock_study.return_value)

    def test_optuna_failure_does_not_crash(self):
        mock_study = MagicMock(side_effect=RuntimeError("Optuna timeout"))
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({'optuna_weekly_enabled': True})):
            with patch.dict('sys.modules', {
                'optuna_optimizer': MagicMock(
                    run_optuna_study=mock_study,
                    apply_optuna_results=MagicMock(),
                ),
            }):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        assert result['optuna']['success'] is False
        assert 'error' in result['optuna']

    def test_optuna_zero_improvement_skips_apply(self):
        mock_study = MagicMock(return_value={'success': True, 'improvement_pct': 0.0})
        mock_apply = MagicMock()
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({'optuna_weekly_enabled': True})):
            with patch.dict('sys.modules', {
                'optuna_optimizer': MagicMock(
                    run_optuna_study=mock_study,
                    apply_optuna_results=mock_apply,
                ),
            }):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        mock_apply.assert_not_called()

    def test_optuna_none_improvement_skips_apply(self):
        """improvement_pct may be None if study failed to establish baseline."""
        mock_study = MagicMock(return_value={'success': True, 'improvement_pct': None})
        mock_apply = MagicMock()
        with patch('roadmap_dispatcher.load_state', return_value=_make_state({'optuna_weekly_enabled': True})):
            with patch.dict('sys.modules', {
                'optuna_optimizer': MagicMock(
                    run_optuna_study=mock_study,
                    apply_optuna_results=mock_apply,
                ),
            }):
                from roadmap_dispatcher import run_roadmap_gated_analyses
                result = run_roadmap_gated_analyses()
        mock_apply.assert_not_called()
