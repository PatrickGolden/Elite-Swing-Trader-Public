# fill_cost_model: simulated fills must carry MEASURED costs (STRATEGY.md §4.2).
# Constants are pinned here on purpose — changing them should force a conscious
# re-measurement (fee tier change), not slip through a refactor.

import pytest

from fill_cost_model import (
    MAKER_FEE_PCT_PER_SIDE,
    SLIPPAGE_PCT_PER_SIDE,
    TAKER_FEE_PCT_PER_SIDE,
    simulated_entry_fill,
    simulated_exit_fill,
)


def test_measured_constants_pinned():
    # Account's live Coinbase tier ("Intro"), read-only API 2026-10-11, after the
    # 2026-09-16 fee cut. Previous basis: 1.20/0.60 (retired old base tier).
    assert TAKER_FEE_PCT_PER_SIDE == 0.90
    assert MAKER_FEE_PCT_PER_SIDE == 0.50  # schedule; unproven until maker fills exist
    assert 0 < SLIPPAGE_PCT_PER_SIDE <= 0.1


def test_entry_fill_costs_work_against_you():
    eff, qty, fee = simulated_entry_fill(50_000.0, 100.0)
    assert eff > 50_000.0                       # slippage raises the buy price
    assert fee == pytest.approx(0.90, abs=1e-6)  # 0.9% of $100 notional
    assert qty == pytest.approx(100.0 / eff)


def test_exit_fill_costs_work_against_you():
    eff, fee = simulated_exit_fill(50_000.0, 0.002)
    assert eff < 50_000.0                        # slippage lowers the sell price
    assert fee == pytest.approx(0.002 * eff * 0.009, abs=1e-6)


def test_round_trip_cost_matches_measured_reality():
    """Flat market round trip on $100 must cost ~1.8% fees + ~0.1% slippage."""
    eff_in, qty, fee_in = simulated_entry_fill(50_000.0, 100.0)
    eff_out, fee_out = simulated_exit_fill(50_000.0, qty)
    gross = (eff_out - eff_in) * qty
    net = gross - fee_in - fee_out
    net_pct = net / 100.0 * 100
    assert net_pct == pytest.approx(-1.9, abs=0.15)  # ≈ −(2×0.9 fees + 2×0.05 slip)
