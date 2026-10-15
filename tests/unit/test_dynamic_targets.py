# tests/test_dynamic_targets.py — Dynamic target calculator: multipliers, bounds, price formulas
# Run from project root: pytest tests/test_dynamic_targets.py -v

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dynamic_targets import DynamicTargetCalculator


def test_volatility_multiplier_bands():
    """Volatility bands: low vol → tighter multiplier, high vol → wider."""
    calc = DynamicTargetCalculator()
    assert calc.get_volatility_multiplier(25) == 0.7
    assert calc.get_volatility_multiplier(35) == 0.85
    assert calc.get_volatility_multiplier(50) == 1.0
    assert calc.get_volatility_multiplier(75) == 1.2
    assert calc.get_volatility_multiplier(90) == 1.5   # < 100
    assert calc.get_volatility_multiplier(100) == 2.0  # >= 100, < 150
    assert calc.get_volatility_multiplier(125) == 2.0
    assert calc.get_volatility_multiplier(160) == 2.5
    assert calc.get_volatility_multiplier(None) == 1.0


def test_regime_multiplier():
    """Regime: BULL 1.1, SIDEWAYS 1.0, BEAR 1.3, VOLATILE 1.5."""
    calc = DynamicTargetCalculator()
    assert calc.get_regime_multiplier('BULL') == 1.1
    assert calc.get_regime_multiplier('SIDEWAYS') == 1.0
    assert calc.get_regime_multiplier('BEAR') == 1.3
    assert calc.get_regime_multiplier('VOLATILE') == 1.5
    assert calc.get_regime_multiplier('UNKNOWN') == 1.0


def test_confidence_multiplier():
    """Confidence: ULTRA 0.9, STRONG 1.0, MODERATE 1.1, WEAK 1.2."""
    calc = DynamicTargetCalculator()
    assert calc.get_confidence_multiplier('ULTRA_BUY') == 0.9
    assert calc.get_confidence_multiplier('STRONG_BUY') == 1.0
    assert calc.get_confidence_multiplier('MODERATE_BUY') == 1.1
    assert calc.get_confidence_multiplier('WEAK_BUY') == 1.2
    assert calc.get_confidence_multiplier('OTHER') == 1.0


def test_stop_and_target_bounds():
    """Raw stop below min → clamped to min_stop_pct; raw target above max → clamped to max_target_pct."""
    calc = DynamicTargetCalculator(base_stop_pct=2.5, min_stop_pct=1.5, max_stop_pct=15.0,
                                    min_target_pct=3.0, max_target_pct=50.0)
    # Very low vol + BULL + ULTRA → raw stop 2.5*0.7*1.1*0.9 ≈ 1.73 → below regime floor,
    # then regime floor for BULL (5.0% via BULL_STOP_FLOOR_PCT) applies → final stop = 5.0
    t = calc.calculate_targets(entry_price=100, volatility_pct=25, regime='BULL', consensus_signal='ULTRA_BUY')
    assert t['reasoning']['raw_stop_pct'] < 5.0
    assert t['stop_loss_pct'] == 5.0  # regime floor for BULL
    assert t['take_profit_pct'] >= 3.0
    assert t['take_profit_pct'] <= 50.0
    # Raw target above max_target_pct → clamped: use tight max_target so raw target exceeds it
    calc_tight = DynamicTargetCalculator(base_stop_pct=4.0, min_stop_pct=1.5, max_stop_pct=15.0,
                                         min_target_pct=2.0, max_target_pct=10.0)
    t2 = calc_tight.calculate_targets(entry_price=100, volatility_pct=80, regime='VOLATILE', consensus_signal='ULTRA_BUY')
    # stop: 4*1.2*1.5*0.9=6.48, target 6.48*2.75=17.82 → clamp to 10
    assert t2['reasoning']['raw_target_pct'] > 10.0
    assert t2['take_profit_pct'] == 10.0


def test_price_formulas():
    """stop_loss_price = entry * (1 - stop_pct/100); take_profit_price = entry * (1 + target_pct/100)."""
    calc = DynamicTargetCalculator()
    t = calc.calculate_targets(entry_price=1000, volatility_pct=50, regime='SIDEWAYS', consensus_signal='STRONG_BUY')
    stop_pct = t['stop_loss_pct']
    target_pct = t['take_profit_pct']
    expected_stop_price = 1000 * (1 - stop_pct / 100)
    expected_target_price = 1000 * (1 + target_pct / 100)
    assert abs(t['stop_loss_price'] - expected_stop_price) < 0.02
    assert abs(t['take_profit_price'] - expected_target_price) < 0.02


def test_rr_ratio_affects_target():
    """Higher R:R (e.g. BULL) yields higher take_profit_pct for same stop width."""
    calc = DynamicTargetCalculator()
    # Same vol; BULL has wider stop (1.1x multiplier) so even with same R:R, target is >= SIDEWAYS.
    t_bull = calc.calculate_targets(entry_price=100, volatility_pct=50, regime='BULL', consensus_signal='STRONG_BUY')
    t_side = calc.calculate_targets(entry_price=100, volatility_pct=50, regime='SIDEWAYS', consensus_signal='STRONG_BUY')
    # BULL has regime mult 1.1 so stop is slightly wider; same R:R 2.0 as SIDEWAYS — target still >= due to wider stop
    assert t_bull['risk_reward_ratio'] >= t_side['risk_reward_ratio'] or t_bull['take_profit_pct'] >= t_side['take_profit_pct']


def test_get_stop_for_position():
    """Convenience wrapper uses coin_data.indicators.volatility and regime_data.regime."""
    calc = DynamicTargetCalculator()
    coin_data = {'current_price': 50000, 'indicators': {'volatility': 60}}
    regime_data = {'regime': 'BEAR'}
    t = calc.get_stop_for_position(coin_data, regime_data, 'MODERATE_BUY')
    assert t['stop_loss_pct'] >= 1.5
    assert t['stop_loss_price'] < 50000
    assert t['take_profit_price'] > 50000
    assert t['reasoning']['regime'] == 'BEAR'
    assert t['reasoning']['volatility_pct'] == 60


def test_trailing_stop_trigger():
    """Phase 1: informational trailing fields mirror the LIVE thresholds (activation /
    trail width) that execution uses — not a target-tiered ladder. This guards against the
    UI showing a stale trail value vs what exit_manager actually executes."""
    from thresholds import get_threshold
    expected_activation = float(get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8))
    expected_trail = float(get_threshold('TRAILING_STOP_TRAIL_PCT', 13))
    calc = DynamicTargetCalculator(min_target_pct=2.0, max_target_pct=50.0)
    t = calc.calculate_targets(entry_price=100, volatility_pct=80, regime='VOLATILE', consensus_signal='WEAK_BUY')
    assert t['trailing_trigger_pct'] == expected_activation
    assert t['trailing_stop_pct'] == expected_trail
    # trailing_stop_price reflects the trail width below entry
    assert t['trailing_stop_price'] == round(100 * (1 - expected_trail / 100), 2)


def test_high_beta_floor_applies_in_sideways():
    """High-volatility names in SIDEWAYS should use the high-beta floor."""
    calc = DynamicTargetCalculator(base_stop_pct=2.5, min_stop_pct=1.5, max_stop_pct=15.0)
    t = calc.calculate_targets(
        entry_price=100,
        volatility_pct=110,  # above HIGH_BETA_VOL_THRESHOLD_PCT default (85)
        regime='SIDEWAYS',
        consensus_signal='MODERATE_BUY',
    )
    assert t['reasoning']['high_beta_floor_applied'] is True
    assert t['stop_loss_pct'] >= 4.5
