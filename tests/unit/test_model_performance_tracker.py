"""Tests for model_performance_tracker.record_trade_outcome — the synthetic fallback path."""
import pytest

import model_performance_tracker as mpt


@pytest.fixture(autouse=True)
def _isolate_perf_file(monkeypatch, tmp_path):
    """Redirect PERF_FILE to a temp path so tests never touch the real file."""
    monkeypatch.setattr(mpt, "PERF_FILE", str(tmp_path / "model_performance.json"))


def test_record_outcome_matches_existing_prediction():
    """Prediction exists → outcome written in-place with source='trade_close'."""
    ts = mpt.log_prediction(coin_id="bitcoin", symbol="BTC", signal="STRONG_BUY",
                             confidence=80, regime="BEAR")
    result = mpt.record_trade_outcome(
        coin_id="bitcoin", symbol="BTC", signal="STRONG_BUY",
        confidence=80, regime="BEAR", pnl_pct=5.2, pred_ts=ts,
    )
    assert result is True
    data = mpt._load()
    match = next(p for p in data["predictions"] if p["ts"] == ts)
    assert match["outcome_3d"] == pytest.approx(5.2)
    assert match["outcome_7d"] == pytest.approx(5.2)
    assert match["outcome_source"] == "trade_close"


def test_record_outcome_idempotent_when_already_recorded():
    """Second call on an already-recorded prediction returns True without overwriting the outcome."""
    ts = mpt.log_prediction(coin_id="bitcoin", symbol="BTC", signal="STRONG_BUY",
                             confidence=80, regime="BEAR")
    mpt.record_trade_outcome(coin_id="bitcoin", symbol="BTC", signal="STRONG_BUY",
                              confidence=80, regime="BEAR", pnl_pct=5.2, pred_ts=ts)
    result = mpt.record_trade_outcome(coin_id="bitcoin", symbol="BTC", signal="STRONG_BUY",
                                       confidence=80, regime="BEAR", pnl_pct=-99.0, pred_ts=ts)
    assert result is True
    data = mpt._load()
    match = next(p for p in data["predictions"] if p["ts"] == ts)
    assert match["outcome_3d"] == pytest.approx(5.2), "original outcome must not be overwritten"


def test_record_outcome_synthetic_when_prediction_rotated_out():
    """pred_ts not in buffer → synthetic entry created with outcome and source='trade_close_synthetic'."""
    stale_ts = "2026-01-01T00:00:00+00:00"
    result = mpt.record_trade_outcome(
        coin_id="ethereum", symbol="ETH", signal="STRONG_BUY",
        confidence=75, regime="SIDEWAYS", pnl_pct=-3.1, pred_ts=stale_ts,
    )
    assert result is True
    data = mpt._load()
    synthetic = next((p for p in data["predictions"] if p["ts"] == stale_ts), None)
    assert synthetic is not None
    assert synthetic["outcome_source"] == "trade_close_synthetic"
    assert synthetic["outcome_3d"] == pytest.approx(-3.1)
    assert synthetic["outcome_7d"] == pytest.approx(-3.1)
    assert synthetic["coin_id"] == "ethereum"
    assert synthetic["symbol"] == "ETH"


def test_record_outcome_synthetic_respects_500_cap():
    """Buffer at capacity: synthetic entry appended, oldest entries trimmed, cap held at 500."""
    data = mpt._load()
    data["predictions"] = [
        {"ts": f"2026-01-{i // 60 + 1:02d}T00:{i % 60:02d}:00+00:00",
         "coin_id": f"real-asset-{i}", "symbol": f"REAL{i}", "signal": "HOLD",
         "confidence": 50, "regime": "BULL", "models": ["Quant"], "veto_decision": None}
        for i in range(500)
    ]
    mpt._save(data)

    result = mpt.record_trade_outcome(
        coin_id="newcoin", symbol="NEW", signal="STRONG_BUY",
        confidence=80, regime="BEAR", pnl_pct=2.0,
        pred_ts="2025-06-01T00:00:00+00:00",
    )
    assert result is True
    data = mpt._load()
    assert len(data["predictions"]) == 500
    synthetic = next(
        (p for p in data["predictions"] if p.get("outcome_source") == "trade_close_synthetic"),
        None,
    )
    assert synthetic is not None, "synthetic entry must survive trimming (appended last)"


def test_log_prediction_still_works_independently():
    """log_prediction baseline: returns a ts string and entry is persisted."""
    ts = mpt.log_prediction(coin_id="solana", symbol="SOL", signal="MODERATE_BUY",
                             confidence=65, regime="SIDEWAYS")
    assert isinstance(ts, str) and len(ts) > 0
    data = mpt._load()
    assert any(p["ts"] == ts for p in data["predictions"])


def test_log_prediction_unknown_regime_logs_warning_but_saves(caplog):
    """regime='UNKNOWN' must emit a warning log yet still persist the entry."""
    import logging
    with caplog.at_level(logging.WARNING, logger="model_performance_tracker"):
        ts = mpt.log_prediction(
            coin_id="bitcoin", symbol="BTC", signal="STRONG_BUY",
            confidence=78, regime="UNKNOWN",
        )
    assert ts is not None and len(ts) > 0, "should return a timestamp string"
    data = mpt._load()
    assert any(p["ts"] == ts for p in data["predictions"]), "entry must be persisted despite invalid regime"
    assert any("UNKNOWN" in r.message for r in caplog.records), (
        "expected a warning mentioning 'UNKNOWN' but none found"
    )


def test_load_scrubs_test_artifacts():
    """Entries matching coin\\d+ / C\\d+ patterns are stripped during _load()."""
    import model_performance_tracker as _mpt
    # Seed the file with one real entry and two test artifacts
    data = _mpt._load()
    data["predictions"] = [
        {"ts": "2026-01-01T00:00:00+00:00", "coin_id": "bitcoin", "symbol": "BTC",
         "signal": "STRONG_BUY", "confidence": 80, "regime": "BEAR", "models": ["Quant"], "veto_decision": None},
        {"ts": "2026-01-02T00:00:00+00:00", "coin_id": "coin3", "symbol": "C3",
         "signal": "HOLD", "confidence": 50, "regime": "SIDEWAYS", "models": ["Quant"], "veto_decision": None},
        {"ts": "2026-01-03T00:00:00+00:00", "coin_id": "COIN7", "symbol": "C7",
         "signal": "HOLD", "confidence": 50, "regime": "SIDEWAYS", "models": ["Quant"], "veto_decision": None},
    ]
    _mpt._save(data)
    loaded = _mpt._load()
    assert len(loaded["predictions"]) == 1, "test artifacts (coin3, COIN7) must be scrubbed"
    assert loaded["predictions"][0]["coin_id"] == "bitcoin"
