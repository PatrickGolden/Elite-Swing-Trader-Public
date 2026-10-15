import json
from pathlib import Path
from unittest.mock import patch

import regression_scorer as rs


def test_trade_to_features_preserves_zero_fear_greed():
    trade = {
        "confirmation_score": 62,
        "fear_greed": 99,
        "regression_features": {
            "fear_greed": 0,
            "base_score": 62,
            "ob_ratio": 1.2,
            "atr_pct": 2.5,
        },
        "regime": "BULL",
        "quant_path": "default",
        "coin_symbol": "SOL",
        "coin_id": "solana",
    }
    feats = rs._trade_to_features(trade, rolling_stats=None)
    assert feats is not None
    assert feats[4] == 0.0


def test_get_regression_score_rejects_feature_name_mismatch(tmp_path, monkeypatch):
    meta_path = tmp_path / "meta.json"
    model_path = tmp_path / "model.pkl"
    meta_path.write_text(json.dumps({
        "n_samples": rs.MIN_TRADES_FOR_REGRESSION,
        "feature_names": list(reversed(rs.FEATURE_ORDER)),
    }), encoding="utf-8")
    model_path.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(rs, "META_PATH", meta_path)
    monkeypatch.setattr(rs, "MODEL_PATH", model_path)

    out = rs.get_regression_score(
        coin={"symbol": "SOL", "id": "solana"},
        regime_data={"regime": "BULL"},
        fear_greed_value=50,
        quant_path="default",
        base_score=65,
    )
    assert out is None


def test_get_regression_score_uses_meta_score_z_stats(tmp_path, monkeypatch):
    meta_path = tmp_path / "meta.json"
    model_path = tmp_path / "model.pkl"
    meta_path.write_text(json.dumps({
        "n_samples": rs.MIN_TRADES_FOR_REGRESSION,
        "feature_names": rs.FEATURE_ORDER,
        "score_z_stats": {"mid|BULL": [50.0, 10.0]},
    }), encoding="utf-8")
    model_path.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(rs, "META_PATH", meta_path)
    monkeypatch.setattr(rs, "MODEL_PATH", model_path)

    captured = {}

    class DummyModel:
        def predict_proba(self, x):
            captured["x"] = x
            return [[0.2, 0.8]]

    with patch("joblib.load", return_value=DummyModel()):
        out = rs.get_regression_score(
            coin={"symbol": "SOL", "id": "solana"},
            regime_data={"regime": "BULL"},
            fear_greed_value=55,
            quant_path="default",
            base_score=70,
        )
    assert out == 0.8
    score_z_idx = rs.FEATURE_ORDER.index('score_z')
    assert round(float(captured["x"][0][score_z_idx]), 6) == 2.0
