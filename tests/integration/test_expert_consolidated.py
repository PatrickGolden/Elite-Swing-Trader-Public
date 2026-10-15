"""
Expert / quant consolidated tests — active cases only.

Removed March 2026: eleven skipped tests that asserted removed expert overlay behavior
(expert_signals on payloads, volume/cycle/M2 badge combos superseded by current quant_signal).
See git history for prior expectations.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from indicators import calculate_weekly_rsi
from quant_signal import generate_quant_signal


def test_calculate_weekly_rsi_neutral_when_insufficient_data():
    """Weekly RSI defaults to 50 when there is not enough history."""
    prices = [100.0] * 100  # < (period+1)*24
    rsi = calculate_weekly_rsi(prices, period=14)
    assert rsi == 50.0


def test_calculate_weekly_rsi_returns_value_with_sufficient_data():
    """Weekly RSI returns a finite value between 0 and 100 with enough data."""
    base = 100.0
    # 16 days of hourly data, slow drift
    prices = [base + i * 0.5 for i in range(16 * 24)]
    rsi = calculate_weekly_rsi(prices, period=14)
    assert 0.0 <= rsi <= 100.0


def test_backward_compatibility_without_expert_signals():
    """Coins without expert_signals behave like before (no expert badges)."""
    coin = {
        "score": 60,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
    }
    regime = {"regime": "SIDEWAYS"}
    out = generate_quant_signal(coin, regime, fear_greed_value=50)
    badges = out.get("signal_badges", [])
    for b in [
        "volume_accum_shift",
        "cycle_extension_confirm",
        "debasement_hedge",
        "flow_volume_confirm",
        "data_cycle_confirm",
        "weekly_rsi_oversold",
        "liquidity_expansion",  # M2 LIQUIDITY INTEGRATION
    ]:
        assert b not in badges


# ---------- M2 LIQUIDITY INTEGRATION ----------


def test_m2_fetched_and_present_in_macro_data(monkeypatch):
    """M2 fetched and present in macro data when FRED returns valid M2SL observations."""
    import resilient_http
    from enhanced_data_fetcher import EnhancedDataFetcherV2
    from unittest.mock import Mock

    # M2SL observations: 13+ valid values for YoY (index 0 = latest, 12 = year ago)
    base_val = 21000.0
    m2_obs = [{"date": "2024-01-01", "value": "."}] * 25
    for i in range(25):
        m2_obs[i] = {"date": f"2024-{max(1,12-i):02d}-01", "value": str(base_val + i * 10)}
    m2_obs[0]["value"] = str(base_val + 50)
    m2_obs[12]["value"] = str(base_val)

    call_count = [0]

    def fake_get(url, *, params=None, timeout=10, **kwargs):
        call_count[0] += 1
        r = Mock()
        r.raise_for_status = lambda: None
        r.status_code = 200
        if params and params.get("series_id") == "M2SL":
            r.json.return_value = {"observations": m2_obs}
        elif params and params.get("series_id") == "CPIAUCSL":
            r.json.return_value = {"observations": [{"date": "2024-01-01", "value": str(300 + i)} for i in range(14)]}
        else:
            r.json.return_value = {"observations": [{"date": "2024-01-01", "value": "5.0"}]}
        return r

    monkeypatch.setattr(EnhancedDataFetcherV2, "_is_stale", lambda self, k: True)
    monkeypatch.setattr(EnhancedDataFetcherV2, "_set_cache", lambda self, k, v: None)
    monkeypatch.setattr(resilient_http, "resilient_get", fake_get)
    monkeypatch.setattr("enhanced_data_fetcher.resilient_get", fake_get)
    fetcher = EnhancedDataFetcherV2()
    fetcher.fred_key = "test_key"
    result = fetcher.get_macro_data()
    assert "m2" in result
    assert result["m2"].get("label") == "M2 YoY %"
    assert result["m2"].get("value") is not None
    assert isinstance(result["m2"]["value"], (int, float))


def test_m2_no_boost_when_m2_low():
    """No liquidity boost or badge when M2 is low (< 5%)."""
    coin = {
        "score": 68,
        "indicators": {"atr_pct": 2.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
        "expert_signals": {"debasement_hedge": True},
    }
    regime = {"regime": "BEAR", "m2_yoy": 2.0}
    out = generate_quant_signal(coin, regime, fear_greed_value=40)
    badges = out.get("signal_badges", [])
    assert "liquidity_expansion" not in badges
    # Score should only have debasement_hedge +4, not +6 from M2
    assert out["score"] <= 73  # 68 + 4 debasement only


def test_no_extra_cost_for_non_top3_from_expert_boosts(monkeypatch):
    """Expert boosts do not change top-3 gating for historical win prob (no extra cost)."""
    import quant_signal as qs

    calls = {"n": 0}

    def fake_get_historical_win_prob(*args, **kwargs):
        calls["n"] += 1
        return 0.6

    monkeypatch.setattr(qs, "get_historical_win_prob", fake_get_historical_win_prob)

    coin = {
        "score": 70,
        "_is_top_3": False,
        "indicators": {"atr_pct": 2.0, "weekly_rsi": 40.0},
        "risk": {"rr_ratio": 3.0},
        "enhanced": {},
        "expert_signals": {"volume_accum_shift": True, "cycle_extension_confirm": True},
    }
    regime = {"regime": "BEAR"}
    _ = generate_quant_signal(coin, regime, fear_greed_value=30)
    # Non-top-3 coins should not invoke get_historical_win_prob, even with expert overlays
    assert calls["n"] == 0
