# tests/test_market_regime.py — Regime score weights, thresholds, fallback, tactical overlay
# Run from project root: pytest tests/test_market_regime.py -v

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from market_regime import MarketRegimeDetector


def _btc_coin(change_7d, change_30d, volatility=50, rsi=50, ch24h=0, ch1h=0):
    return {
        "id": "bitcoin",
        "price_change_percentage_7d_in_currency": change_7d,
        "price_change_percentage_30d_in_currency": change_30d,
        "price_change_percentage_24h_in_currency": ch24h,
        "price_change_percentage_1h_in_currency": ch1h,
        "indicators": {"volatility": volatility, "rsi": rsi},
    }


def _coins_with_alt_momentum(btc, alt_up_ratio):
    """Build coins = [btc] + 19 alts so altcoin_momentum ≈ alt_up_ratio (0–1).
    Sets 7d change only (structural layer)."""
    n_up = max(0, min(19, int(round(alt_up_ratio * 19))))
    alts = []
    for i in range(19):
        alts.append({
            "id": f"alt_{i}",
            "price_change_percentage_7d_in_currency": 5.0 if i < n_up else -2.0,
        })
    return [btc] + alts


def _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d, alt_up_ratio_24h):
    """Build coins = [btc] + 19 alts with both 7d and 24h changes.
    Structural layer uses 7d, tactical overlay uses 24h."""
    n_up_7d = max(0, min(19, int(round(alt_up_ratio_7d * 19))))
    n_up_24h = max(0, min(19, int(round(alt_up_ratio_24h * 19))))
    alts = []
    for i in range(19):
        alts.append({
            "id": f"alt_{i}",
            "price_change_percentage_7d_in_currency": 5.0 if i < n_up_7d else -2.0,
            "price_change_percentage_24h_in_currency": 3.0 if i < n_up_24h else -1.5,
        })
    return [btc] + alts


# =====================================================================
# Stage 1b: risk-on/off master gate in characteristics
# =====================================================================

def test_risk_on_injected_from_caller():
    """When the caller injects btc_risk_on, detect_regime surfaces it in characteristics."""
    btc = _btc_coin(5, 15, volatility=35, rsi=65)
    data = {
        "coins": _coins_with_alt_momentum(btc, 0.8),
        "fear_greed": {"value": 75},
        "btc_risk_on": {"risk_on": True, "btc_vs_ma200_pct": 12.3, "source": "ma200"},
    }
    out = MarketRegimeDetector().detect_regime(data)
    chars = out["characteristics"]
    assert chars["risk_on"] is True
    assert chars["risk_regime"] == "RISK_ON"
    assert chars["btc_vs_ma200_pct"] == 12.3
    assert chars["risk_on_source"] == "ma200"


def test_risk_on_fallback_from_30d_change():
    """No injected value: fall back to BTC 30d-change > 0 (fail-closed proxy)."""
    btc_up = _btc_coin(2, 8, volatility=50, rsi=55)
    out_up = MarketRegimeDetector().detect_regime(
        {"coins": _coins_with_alt_momentum(btc_up, 0.6), "fear_greed": {"value": 55}})
    assert out_up["characteristics"]["risk_on"] is True
    assert out_up["characteristics"]["risk_on_source"] == "regime_30d_fallback"

    btc_dn = _btc_coin(-2, -8, volatility=50, rsi=45)
    out_dn = MarketRegimeDetector().detect_regime(
        {"coins": _coins_with_alt_momentum(btc_dn, 0.4), "fear_greed": {"value": 45}})
    assert out_dn["characteristics"]["risk_on"] is False
    assert out_dn["characteristics"]["risk_regime"] == "RISK_OFF"


def test_fallback_regime_is_risk_off():
    """No BTC data => UNKNOWN regime with fail-closed risk_off."""
    out = MarketRegimeDetector().detect_regime({"coins": [], "fear_greed": {"value": 50}})
    assert out["regime"] == "UNKNOWN"
    assert out["characteristics"]["risk_on"] is False


# =====================================================================
# STRUCTURAL REGIME TESTS (existing)
# =====================================================================

def test_bull_when_strong_trend_and_greed():
    """BTC 30d +15, 7d +5, vol 35, fg 75, rsi 65, alt 0.8 → regime BULL."""
    btc = _btc_coin(5, 15, volatility=35, rsi=65)
    coins = _coins_with_alt_momentum(btc, 0.8)
    data = {"coins": coins, "fear_greed": {"value": 75}}
    detector = MarketRegimeDetector()
    out = detector.detect_regime(data)
    assert out["regime"] == "BULL"
    assert out["confidence"] >= 50


def test_bear_when_downtrend_and_fear():
    """BTC 30d -12, 7d -3, fg 25, rsi 35 → BEAR."""
    btc = _btc_coin(-3, -12, volatility=50, rsi=35)
    coins = _coins_with_alt_momentum(btc, 0.2)  # few alts up → BEAR alt score
    data = {"coins": coins, "fear_greed": {"value": 25}}
    detector = MarketRegimeDetector()
    out = detector.detect_regime(data)
    assert out["regime"] == "BEAR"
    assert out["confidence"] >= 50


def test_volatile_high_vol():
    """BTC vol 90 or 110 → VOLATILE gets strong score (and wins at 110 with neutral others)."""
    detector = MarketRegimeDetector()
    # Vol 90: VOLATILE +25; ensure we see strong VOLATILE score
    btc90 = _btc_coin(2, 8, volatility=90, rsi=50)
    data90 = {"coins": _coins_with_alt_momentum(btc90, 0.5), "fear_greed": {"value": 50}}
    out90 = detector.detect_regime(data90)
    assert out90["scores"]["VOLATILE"] >= 25
    # Vol 110: VOLATILE +25+30 = 55; neutral trend/sentiment/RSI/alt → VOLATILE wins
    btc110 = _btc_coin(2, 8, volatility=110, rsi=50)
    data110 = {"coins": _coins_with_alt_momentum(btc110, 0.5), "fear_greed": {"value": 50}}
    out110 = detector.detect_regime(data110)
    assert out110["regime"] == "VOLATILE"
    assert out110["scores"]["VOLATILE"] >= 55


def test_fallback_no_btc():
    """data.coins without bitcoin → regime UNKNOWN (fallback)."""
    data_no_btc = {"coins": [{"id": "ethereum", "price_change_percentage_7d_in_currency": 0}], "fear_greed": {"value": 50}}
    detector = MarketRegimeDetector()
    out = detector.detect_regime(data_no_btc)
    assert out["regime"] == "UNKNOWN"
    assert out["structural_regime"] == "UNKNOWN"
    assert out["confidence"] == 0
    assert "position_size_multiplier" in out["strategy_adjustment"]
    out_empty = detector.detect_regime({"coins": [], "fear_greed": {}})
    assert out_empty["regime"] == "UNKNOWN"


def test_altcoin_participation_uses_available_alt_count():
    btc = _btc_coin(1, 5, volatility=45, rsi=52)
    # Only 4 alts available, 2 up -> 50%
    coins = [btc] + [
        {"id": "a1", "price_change_percentage_7d_in_currency": 4.0},
        {"id": "a2", "price_change_percentage_7d_in_currency": 3.0},
        {"id": "a3", "price_change_percentage_7d_in_currency": -1.0},
        {"id": "a4", "price_change_percentage_7d_in_currency": -2.0},
    ]
    detector = MarketRegimeDetector()
    out = detector.detect_regime({"coins": coins, "fear_greed": {"value": 50}})
    assert out["characteristics"]["altcoin_participation"] == "50%"


# =====================================================================
# TACTICAL OVERLAY TESTS — DOWNGRADES
# =====================================================================

def test_tactical_bull_severe_to_bear():
    """Structural BULL + severe 24h drop + weak alt breadth → tactical BEAR."""
    btc = _btc_coin(5, 15, volatility=35, rsi=65, ch24h=-5.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.8, alt_up_ratio_24h=0.1)
    data = {"coins": coins, "fear_greed": {"value": 75}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "BULL"
    assert out["regime"] == "BEAR"
    assert out["characteristics"]["tactical_override"] is True
    assert out["characteristics"]["structural_regime"] == "BULL"


def test_tactical_bull_moderate_to_sideways():
    """Structural BULL + moderate 24h drop + 7d weakness → tactical SIDEWAYS."""
    btc = _btc_coin(-6, 15, volatility=35, rsi=65, ch24h=-3.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.8, alt_up_ratio_24h=0.5)
    data = {"coins": coins, "fear_greed": {"value": 75}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "BULL"
    assert out["regime"] == "SIDEWAYS"


def test_tactical_sideways_severe_to_bear():
    """Structural SIDEWAYS + severe 24h drop + weak breadth → tactical BEAR."""
    btc = _btc_coin(0, 2, volatility=50, rsi=45, ch24h=-5.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.1)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "SIDEWAYS"
    assert out["regime"] == "BEAR"


def test_tactical_sideways_moderate_to_volatile():
    """Structural SIDEWAYS + moderate 24h drop + low RSI → tactical VOLATILE."""
    btc = _btc_coin(0, 2, volatility=50, rsi=30, ch24h=-3.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.5)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "SIDEWAYS"
    assert out["regime"] == "VOLATILE"


def test_tactical_no_downgrade():
    """Structural BULL + no short-term divergence → stays BULL."""
    btc = _btc_coin(5, 15, volatility=35, rsi=65, ch24h=1.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.8, alt_up_ratio_24h=0.8)
    data = {"coins": coins, "fear_greed": {"value": 75}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["regime"] == "BULL"
    assert out["structural_regime"] == "BULL"
    assert out["characteristics"]["tactical_override"] is False


def test_tactical_bear_passthrough():
    """Structural BEAR passes through even with severe divergence data."""
    btc = _btc_coin(-3, -12, volatility=50, rsi=35, ch24h=-10.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.2, alt_up_ratio_24h=0.05)
    data = {"coins": coins, "fear_greed": {"value": 25}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["regime"] == "BEAR"
    assert out["structural_regime"] == "BEAR"


def test_tactical_volatile_passthrough():
    """Structural VOLATILE passes through unchanged."""
    btc = _btc_coin(2, 8, volatility=110, rsi=50, ch24h=-5.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.1)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["regime"] == "VOLATILE"
    assert out["structural_regime"] == "VOLATILE"


def test_tactical_acute_1h_crash():
    """Acute 1h crash with weak alt breadth → BEAR regardless of structural regime."""
    # Structural BULL but 1h crash
    btc = _btc_coin(5, 15, volatility=35, rsi=65, ch24h=0, ch1h=-4.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.8, alt_up_ratio_24h=0.1)
    data = {"coins": coins, "fear_greed": {"value": 75}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "BULL"
    assert out["regime"] == "BEAR"
    assert "acute_crash" in out["characteristics"]["tactical_reason"]


def test_tactical_strategy_matches_downgrade():
    """When BULL is downgraded to BEAR, strategy should reflect BEAR params."""
    btc = _btc_coin(5, 15, volatility=35, rsi=65, ch24h=-5.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.8, alt_up_ratio_24h=0.1)
    data = {"coins": coins, "fear_greed": {"value": 75}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["regime"] == "BEAR"
    assert out["strategy_adjustment"]["position_size_multiplier"] == 0.6
    assert out["strategy_adjustment"]["ai_agreement_required"] == 3


# =====================================================================
# TACTICAL OVERLAY TESTS — UPGRADES
# =====================================================================

def test_tactical_bear_upgrade_to_sideways():
    """Structural BEAR + strong 24h rally + broad alt breadth + RSI > 55 → SIDEWAYS."""
    btc = _btc_coin(-3, -12, volatility=50, rsi=60, ch24h=6.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.2, alt_up_ratio_24h=0.85)
    data = {"coins": coins, "fear_greed": {"value": 25}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "BEAR"
    assert out["regime"] == "SIDEWAYS"
    assert out["characteristics"]["tactical_override"] is True
    assert "rally_upgrade" in out["characteristics"]["tactical_reason"]


def test_tactical_sideways_upgrade_to_bull():
    """Structural SIDEWAYS + strong rally → BULL."""
    btc = _btc_coin(0, 2, volatility=50, rsi=60, ch24h=6.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.85)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "SIDEWAYS"
    assert out["regime"] == "BULL"


def test_tactical_bear_no_skip_to_bull():
    """Structural BEAR + strong rally → SIDEWAYS only (never BEAR→BULL)."""
    btc = _btc_coin(-3, -12, volatility=50, rsi=60, ch24h=6.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.2, alt_up_ratio_24h=0.85)
    data = {"coins": coins, "fear_greed": {"value": 25}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "BEAR"
    assert out["regime"] == "SIDEWAYS"  # one level up, never BULL


# =====================================================================
# TACTICAL UPGRADE BOUNDARY TESTS (regression: threshold raised 3.0→5.0)
# =====================================================================

def test_tactical_upgrade_rejects_3pct_btc_move():
    """3.5% BTC 24h move does NOT trigger SIDEWAYS→BULL upgrade (below 5.0% threshold)."""
    btc = _btc_coin(0, 2, volatility=50, rsi=60, ch24h=3.5)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.85)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "SIDEWAYS"
    assert out["regime"] == "SIDEWAYS"


def test_tactical_upgrade_runtime_uses_live_threshold():
    """Runtime regime detection reads TACTICAL_UPGRADE_24H_PCT from live thresholds.
    Regression: if live value drifts below 5.0, phantom upgrades re-emerge."""
    from unittest.mock import patch
    btc = _btc_coin(0, 2, volatility=50, rsi=60, ch24h=3.5)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.85)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    # With default 5.0: 3.5% move does NOT upgrade
    out_default = MarketRegimeDetector().detect_regime(data)
    assert out_default["regime"] == "SIDEWAYS"
    # If live override set to 3.0: 3.5% move WOULD upgrade (the bug we fixed)
    orig_get = __import__('thresholds').get_threshold
    def mock_get(key, default=None):
        if key == 'TACTICAL_UPGRADE_24H_PCT':
            return 3.0
        return orig_get(key, default)
    with patch('thresholds.get_threshold', side_effect=mock_get):
        out_low = MarketRegimeDetector().detect_regime(data)
    assert out_low["regime"] == "BULL", "3.5% move upgrades when threshold is 3.0 — proves runtime reads live value"


def test_tactical_upgrade_fires_at_5pct():
    """5.5% BTC 24h move DOES trigger SIDEWAYS→BULL upgrade."""
    btc = _btc_coin(0, 2, volatility=50, rsi=60, ch24h=5.5)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.85)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "SIDEWAYS"
    assert out["regime"] == "BULL"


def test_tactical_upgrade_boundary_exact_5pct():
    """Exactly 5.0% BTC 24h: threshold check is >=, so this should trigger upgrade."""
    btc = _btc_coin(0, 2, volatility=50, rsi=60, ch24h=5.0)
    coins = _coins_with_alt_breadth_24h(btc, alt_up_ratio_7d=0.5, alt_up_ratio_24h=0.85)
    data = {"coins": coins, "fear_greed": {"value": 50}}
    out = MarketRegimeDetector().detect_regime(data)
    assert out["structural_regime"] == "SIDEWAYS"
    assert out["regime"] == "BULL"
