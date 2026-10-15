# tests/unit/test_risk_regime.py — Stage 1b BTC risk-on/off master gate
# Run from project root: pytest tests/unit/test_risk_regime.py -q

import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import risk_regime


def test_risk_on_true_above_ma_and_30d_positive():
    closes = [100.0] * 169 + list(range(100, 131))  # 200 pts; last well above the ~ mean, 30d up
    with patch("risk_regime._fetch_btc_daily_closes", return_value=closes):
        out = risk_regime.compute_btc_risk_on(band_pct=1.5, force_refresh=True)
    assert out["source"] == "ma200"
    assert out["risk_on"] is True
    assert out["btc_vs_ma200_pct"] > 0


def test_risk_off_when_below_ma():
    closes = [200.0] * 199 + [100.0]  # last far below the 200d mean
    with patch("risk_regime._fetch_btc_daily_closes", return_value=closes):
        out = risk_regime.compute_btc_risk_on(band_pct=1.5, force_refresh=True)
    assert out["source"] == "ma200"
    assert out["risk_on"] is False


def test_fallback_to_30d_when_fetch_fails():
    def _boom(*a, **k):
        raise RuntimeError("network down")
    with patch("risk_regime._fetch_btc_daily_closes", side_effect=_boom):
        up = risk_regime.compute_btc_risk_on(btc_30d_change_fallback=5.0, force_refresh=True)
        down = risk_regime.compute_btc_risk_on(btc_30d_change_fallback=-4.0, force_refresh=True)
    assert up["source"] == "fallback_30d" and up["risk_on"] is True
    assert down["source"] == "fallback_30d" and down["risk_on"] is False


def test_unavailable_is_fail_closed():
    def _boom(*a, **k):
        raise RuntimeError("network down")
    with patch("risk_regime._fetch_btc_daily_closes", side_effect=_boom):
        out = risk_regime.compute_btc_risk_on(btc_30d_change_fallback=None, force_refresh=True)
    assert out["source"] == "unavailable"
    assert out["risk_on"] is False
