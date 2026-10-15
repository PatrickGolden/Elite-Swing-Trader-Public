# Regression: a FAILED risk-on computation ('unavailable') must not poison the cache
# for the full 12h TTL — it blinds the rotation/lean entry gates for half a day on a
# transient network error (observed 2026-06-11). Failures retry after _FAIL_TTL.

import json
import os
import time
from unittest.mock import patch

import risk_regime


def _write_cache(tmp_path, val, age_seconds, monkeypatch):
    p = tmp_path / "btc_risk_on_cache.json"
    p.write_text(json.dumps(val), encoding="utf-8")
    ts = time.time() - age_seconds
    os.utime(p, (ts, ts))
    monkeypatch.setattr(risk_regime, "_CACHE_PATH", str(p))
    monkeypatch.setattr(risk_regime, "_mem", {"ts": 0.0, "val": None})
    return p


def test_unavailable_cache_expires_fast(tmp_path, monkeypatch):
    """A 30-min-old 'unavailable' entry is stale → recompute is attempted."""
    _write_cache(tmp_path,
                 {"risk_on": False, "btc_vs_ma200_pct": None, "btc_30d_change": None,
                  "source": "unavailable"},
                 age_seconds=30 * 60, monkeypatch=monkeypatch)
    fresh = {"risk_on": True, "btc_vs_ma200_pct": 5.0, "btc_30d_change": 10.0, "source": "ma200"}
    with patch.object(risk_regime, "_compute_fresh", return_value=fresh) as m:
        out = risk_regime.compute_btc_risk_on()
    assert m.called
    assert out["source"] == "ma200"


def test_successful_cache_honors_long_ttl(tmp_path, monkeypatch):
    """A 30-min-old successful entry is still fresh → no recompute."""
    good = {"risk_on": False, "btc_vs_ma200_pct": -18.6, "btc_30d_change": -21.1, "source": "ma200"}
    _write_cache(tmp_path, good, age_seconds=30 * 60, monkeypatch=monkeypatch)
    with patch.object(risk_regime, "_compute_fresh") as m:
        out = risk_regime.compute_btc_risk_on()
    assert not m.called
    assert out == good


def test_unavailable_memory_cache_also_expires_fast(tmp_path, monkeypatch):
    """In-memory 'unavailable' older than _FAIL_TTL is stale even within 12h."""
    monkeypatch.setattr(risk_regime, "_CACHE_PATH", str(tmp_path / "nope.json"))
    bad = {"risk_on": False, "btc_vs_ma200_pct": None, "btc_30d_change": None,
           "source": "unavailable"}
    monkeypatch.setattr(risk_regime, "_mem",
                        {"ts": time.time() - 20 * 60, "val": bad})
    fresh = {"risk_on": True, "btc_vs_ma200_pct": 3.0, "btc_30d_change": 8.0, "source": "ma200"}
    with patch.object(risk_regime, "_compute_fresh", return_value=fresh) as m:
        out = risk_regime.compute_btc_risk_on()
    assert m.called
    assert out["source"] == "ma200"
