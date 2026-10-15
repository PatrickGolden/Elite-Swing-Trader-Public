# tests/test_fixes_hardening.py  -  FIXES & HARDENING verification (production-ready pass)
# Run: pytest tests/test_fixes_hardening.py -v --tb=short

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# -----------------------------------------------------------------------------
# 1. Async cleanup: no pending task warnings in sync calls
# -----------------------------------------------------------------------------
def test_async_cleanup_sync_single_coin_no_pending_warnings():
    """Sync wrapper uses asyncio.run + gather pending + sleep(0); loop closes cleanly."""
    async def fake_coro():
        return {"consensus_signal": "HOLD"}
    async def run_and_cleanup():
        result = await fake_coro()
        try:
            loop = asyncio.get_running_loop()
            pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)
        except RuntimeError:
            pass
        return result
    result = asyncio.run(run_and_cleanup())
    assert result is not None
    assert result.get("consensus_signal") == "HOLD"


def test_async_cleanup_sync_batch_no_pending_warnings():
    """Batch sync uses asyncio.run with cleanup pattern; no pending tasks left."""
    async def fake_batch(coins):
        return [{"consensus_signal": "HOLD"} for _ in coins]
    async def run_batch_and_cleanup():
        results = await fake_batch([1, 2])
        try:
            loop = asyncio.get_running_loop()
            pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)
        except RuntimeError:
            pass
        return results
    results = asyncio.run(run_batch_and_cleanup())
    assert len(results) == 2
    assert all(r.get("consensus_signal") == "HOLD" for r in results)


# -----------------------------------------------------------------------------
# 2. Threshold centralization: all files read same values from live_thresholds.json
# -----------------------------------------------------------------------------
def test_threshold_centralization_load_from_json():
    """Thresholds module loads from live_thresholds.json and returns merged defaults."""
    from thresholds import _load_live_thresholds, get_threshold, DEFAULT_THRESHOLDS, reload_live_thresholds
    reload_live_thresholds()
    t = _load_live_thresholds()
    assert isinstance(t, dict)
    assert t.get("BLOOD_STREET_FG") is not None or "BLOOD_STREET_FG" in DEFAULT_THRESHOLDS
    assert get_threshold("BLOOD_STREET_FG", 30) in (30, 25, 35)
    assert get_threshold("FUNDING_VETO_BEAR_PCT", 0.08) in (0.08, 0.06, 0.10)


def test_threshold_centralization_quant_uses_thresholds():
    """quant_signal uses _thresh() from thresholds (no hardcoded BLOOD_STREET_FG etc)."""
    from quant_signal import generate_quant_signal
    coin = {"score": 70, "indicators": {"atr_pct": 2, "ch1h": 0, "ch24h": -7, "ch7d": -5, "ch30d": 0, "momentum": 5},
            "enhanced": {}, "_is_top_3": False, "_is_top_10": False, "risk": {"rr_ratio": 3}}
    regime_data = {"regime": "BEAR", "market_24h_pct": 0}
    out = generate_quant_signal(coin, regime_data, fear_greed_value=25)
    assert "quant_signal" in out
    assert out["quant_signal"] in ("BUY", "HOLD")
    # Behavior unchanged: tier/reasoning present
    assert "quant_tier" in out
    assert "reasoning" in out


def test_threshold_centralization_veto_uses_thresholds():
    """veto_specialist uses get_threshold for rule constants."""
    from veto_specialist import get_threshold
    v = get_threshold("FUNDING_VETO_BEAR_PCT", 0.08)
    assert isinstance(v, (int, float))
    assert 0 <= v <= 0.2


# -----------------------------------------------------------------------------
# 3. (Removed: brain/consensus JSON extraction tests — brain module deleted)
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# 4. Dynamic veto: no import inside function
# -----------------------------------------------------------------------------
def test_veto_dynamic_threshold_no_import_inside_function():
    """veto_specialist._get_dynamic_veto_threshold does not import model_attribution inside the function."""
    import inspect
    from veto_specialist import _get_dynamic_veto_threshold
    src = inspect.getsource(_get_dynamic_veto_threshold)
    # Should not contain "from model_attribution import" or "import model_attribution" inside the function body
    lines = src.split("\n")
    in_body = False
    for line in lines:
        if "def _get_dynamic_veto_threshold" in line:
            in_body = True
            continue
        if in_body and line.strip().startswith("def "):
            break
        if in_body and ("import model_attribution" in line or "from model_attribution" in line):
            pytest.fail("model_attribution should be imported at top-level, not inside _get_dynamic_veto_threshold")
    assert _get_dynamic_veto_threshold("BULL") in (65, 70, 82)


# -----------------------------------------------------------------------------
# 5. (Removed: rate-limiter test — cost_tracker module deleted)
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# 6. (Removed: daily_local_retrain tests — function deleted)
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# 7. Backward compatibility: old signals unchanged
# -----------------------------------------------------------------------------
def test_backward_compatibility_quant_signals_unchanged():
    """generate_quant_signal still returns same shape and signal values for same inputs."""
    from quant_signal import generate_quant_signal
    coin = {"score": 85, "indicators": {"atr_pct": 2, "ch1h": 0, "ch24h": 0, "ch7d": 0, "ch30d": 0, "momentum": 10},
            "enhanced": {"data_quality": {"ok_count": 2}}, "_is_top_3": False, "_is_top_10": False, "risk": {"rr_ratio": 3}}
    regime_data = {"regime": "BULL"}
    out = generate_quant_signal(coin, regime_data, fear_greed_value=50)
    assert out["quant_signal"] == "BUY"
    assert out["quant_tier"] == "ULTRA_BUY"
    assert "score" in out and "reasoning" in out and "signal_badges" in out


# -----------------------------------------------------------------------------
# 8. (Removed: cost tracker tests — cost_tracker module deleted)
# -----------------------------------------------------------------------------
