# tests/unit/test_shap_analysis.py — SHAP feature importance analysis tests
# All tests use tmp_path (Windows-safe) and mock external dependencies.

import gc
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# Lazy import pattern — avoid importing app.py globals
@pytest.fixture
def shap_mod():
    """Import shap_analysis lazily to avoid app.py side effects."""
    import shap_analysis
    return shap_analysis


@pytest.fixture
def fake_model():
    """Create a fake LogisticRegression with known coefficients."""
    from sklearn.linear_model import LogisticRegression
    from regression_scorer import FEATURE_ORDER
    n_features = len(FEATURE_ORDER)
    np.random.seed(42)
    X = np.random.randn(100, n_features)
    y = (X[:, 0] > 0).astype(int)  # base_score is the dominant feature
    model = LogisticRegression(max_iter=500, class_weight='balanced', random_state=42)
    model.fit(X, y)
    return model, X, y


@pytest.fixture
def feature_names():
    from regression_scorer import FEATURE_ORDER
    return FEATURE_ORDER


@pytest.fixture
def fake_trades():
    """Generate fake closed trades matching journal schema."""
    trades = []
    regimes = ['BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE']
    paths = ['dip', 'blood_in_the_street', 'pullback_in_trend', 'momentum_breakout', 'default']
    for i in range(120):
        trades.append({
            'outcome': 'win' if i % 3 != 0 else 'loss',
            'regime': regimes[i % 4],
            'market_regime': regimes[i % 4],
            'quant_path': paths[i % 5],
            'confirmation_score': 60 + (i % 20),
            'base_score': 60 + (i % 20),
            'fear_greed': 40 + (i % 40),
            'coin_symbol': 'BTC' if i % 10 == 0 else 'ETH' if i % 10 == 1 else 'SOL',
            'coin_id': 'bitcoin' if i % 10 == 0 else 'ethereum' if i % 10 == 1 else 'solana',
            'exit_time': f'2026-03-{1 + (i % 28):02d}T12:00:00Z',
            'regression_features': {
                'base_score': 60 + (i % 20),
                'ob_ratio': 1.0 + (i % 5) * 0.1,
                'mtf_aligned': i % 2 == 0,
                'atr_pct': 2.0 + (i % 10) * 0.5,
                'fear_greed': 40 + (i % 40),
                'coin_class': 'btc' if i % 10 == 0 else 'eth' if i % 10 == 1 else 'mid',
            },
        })
    return trades


@pytest.fixture
def setup_model_files(tmp_path, fake_model, feature_names):
    """Save a fake model and metadata to tmp_path."""
    import joblib
    model, X, y = fake_model
    model_path = tmp_path / 'regression_model.pkl'
    meta_path = tmp_path / 'regression_model_meta.json'
    joblib.dump(model, model_path)
    meta = {
        'trained_at': '2026-03-23T00:00:00Z',
        'n_samples': 100,
        'feature_names': feature_names,
        'score_z_stats': {},
    }
    with open(meta_path, 'w') as f:
        json.dump(meta, f)
    return model_path, meta_path


def teardown_function():
    gc.collect()


# --- Tests ---

class TestRunShapAnalysis:
    def test_success_with_valid_model(self, shap_mod, setup_model_files, fake_trades, tmp_path, feature_names):
        model_path, meta_path = setup_model_files
        output_dir = tmp_path / 'shap_output'
        n_features = len(feature_names)

        with patch.object(shap_mod, '_load_training_data') as mock_load:
            X = np.random.randn(120, n_features)
            mock_load.return_value = (X, fake_trades)

            result = shap_mod.run_shap_analysis(
                model_path=model_path,
                meta_path=meta_path,
                output_dir=output_dir,
                generate_plots=False,
            )

        assert result['success'] is True
        assert result['n_samples'] == 120
        assert len(result['global_importance']) == n_features
        # Summary JSON should exist
        assert (output_dir / 'summary.json').exists()

    def test_model_not_found(self, shap_mod, tmp_path):
        result = shap_mod.run_shap_analysis(
            model_path=tmp_path / 'nonexistent.pkl',
            meta_path=tmp_path / 'nonexistent.json',
            output_dir=tmp_path / 'out',
        )
        assert result['success'] is False
        assert 'not found' in result['reason']

    def test_insufficient_samples_in_meta(self, shap_mod, tmp_path, fake_model, feature_names):
        import joblib
        model, _, _ = fake_model
        model_path = tmp_path / 'model.pkl'
        meta_path = tmp_path / 'meta.json'
        joblib.dump(model, model_path)
        with open(meta_path, 'w') as f:
            json.dump({'n_samples': 50, 'feature_names': feature_names}, f)

        result = shap_mod.run_shap_analysis(
            model_path=model_path, meta_path=meta_path, output_dir=tmp_path / 'out',
        )
        assert result['success'] is False
        assert 'insufficient' in result['reason']

    def test_insufficient_training_data(self, shap_mod, setup_model_files, tmp_path):
        model_path, meta_path = setup_model_files
        with patch.object(shap_mod, '_load_training_data') as mock_load:
            mock_load.return_value = (None, [])
            result = shap_mod.run_shap_analysis(
                model_path=model_path, meta_path=meta_path, output_dir=tmp_path / 'out',
            )
        assert result['success'] is False
        assert 'training data' in result['reason']

    def test_empty_feature_names(self, shap_mod, tmp_path, fake_model):
        import joblib
        model, _, _ = fake_model
        model_path = tmp_path / 'model.pkl'
        meta_path = tmp_path / 'meta.json'
        joblib.dump(model, model_path)
        with open(meta_path, 'w') as f:
            json.dump({'n_samples': 100, 'feature_names': []}, f)

        result = shap_mod.run_shap_analysis(
            model_path=model_path, meta_path=meta_path, output_dir=tmp_path / 'out',
        )
        assert result['success'] is False
        assert 'feature_names' in result['reason']


class TestGlobalImportance:
    def test_sorted_descending(self, shap_mod):
        shap_values = np.array([[0.1, 0.5, 0.3], [-0.2, 0.4, 0.1]])
        names = ['a', 'b', 'c']
        result = shap_mod._compute_global_importance(shap_values, names, np)
        assert result[0]['feature'] == 'b'
        assert result[1]['feature'] == 'c'
        assert result[2]['feature'] == 'a'

    def test_pct_contribution_sums_to_100(self, shap_mod):
        shap_values = np.random.randn(50, 5)
        names = ['f1', 'f2', 'f3', 'f4', 'f5']
        result = shap_mod._compute_global_importance(shap_values, names, np)
        total_pct = sum(r['pct_contribution'] for r in result)
        assert abs(total_pct - 100.0) < 1.0  # rounding tolerance


class TestRegimeImportance:
    def test_regimes_with_enough_samples(self, shap_mod, fake_trades, feature_names):
        n_features = len(feature_names)
        shap_values = np.random.randn(len(fake_trades), n_features)
        names = [f'f{i}' for i in range(n_features)]
        result = shap_mod._compute_regime_importance(shap_values, names, fake_trades, np)
        assert 'BULL' in result
        assert 'BEAR' in result
        assert 'SIDEWAYS' in result
        assert 'VOLATILE' in result
        for regime_data in result.values():
            assert len(regime_data) == n_features

    def test_regime_with_too_few_samples_excluded(self, shap_mod):
        trades = [{'regime': 'BULL'}] * 3  # Only 3, below threshold of 5
        shap_values = np.random.randn(3, 5)
        names = ['f1', 'f2', 'f3', 'f4', 'f5']
        result = shap_mod._compute_regime_importance(shap_values, names, trades, np)
        assert 'BULL' not in result


class TestSummaryJsonSchema:
    def test_output_schema(self, shap_mod, setup_model_files, fake_trades, tmp_path, feature_names):
        model_path, meta_path = setup_model_files
        output_dir = tmp_path / 'shap_output'

        with patch.object(shap_mod, '_load_training_data') as mock_load:
            X = np.random.randn(120, len(feature_names))
            mock_load.return_value = (X, fake_trades)
            shap_mod.run_shap_analysis(
                model_path=model_path, meta_path=meta_path,
                output_dir=output_dir, generate_plots=False,
            )

        with open(output_dir / 'summary.json') as f:
            summary = json.load(f)

        assert 'n_samples' in summary
        assert 'feature_names' in summary
        assert 'global_importance' in summary
        assert 'regime_importance' in summary
        assert 'generated_at' in summary
        assert isinstance(summary['global_importance'], list)
        assert isinstance(summary['regime_importance'], dict)
