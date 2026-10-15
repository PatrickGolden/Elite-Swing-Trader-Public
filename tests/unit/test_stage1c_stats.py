# Regression tests for the 2026-06-11 audit findings on stage1c_replay's
# gate statistics (H1: stress PF/WR, H2: no-op fold purge, M1: cost constant).

import pytest

from stage1c_replay import (
    FEE_PCT,
    ROUND_TRIP_COST_PCT,
    SLIPPAGE_PCT,
    _fold_stats,
    _half_stats,
    _profit_factor,
    simulate_daily,
)


def _trade(pnl, date="2023-06-15", **kw):
    t = {
        "date": date, "year": int(date[:4]), "symbol": "TST",
        "pnl_pct": pnl, "gross_pnl_pct": pnl + ROUND_TRIP_COST_PCT,
        "outcome": "win" if pnl > 0 else "loss",
        "exit_reason": "time_exit", "hold_days": 5,
    }
    t.update(kw)
    return t


# -----------------------------------------------------------------------------
# H1 — stress stats must recompute win rate and PF under extra_cost,
# not just expectancy (STRATEGY.md G2 pre-registered requirement).
# -----------------------------------------------------------------------------

class TestHalfStatsUnderStress:
    def test_no_extra_cost_matches_raw_pnl(self):
        st = _half_stats([_trade(2.0), _trade(2.0), _trade(-1.0), _trade(-1.0)])
        assert st["n"] == 4
        assert st["win_rate_pct"] == 50.0
        assert st["expectancy_pct"] == 0.5
        assert st["profit_factor"] == 2.0

    def test_extra_cost_flips_marginal_winners_to_losers(self):
        # adjusted pnl: [0.5, 0.5, -2.5, -2.5] → WR stays 50%, PF = 1.0/5.0
        trades = [_trade(2.0), _trade(2.0), _trade(-1.0), _trade(-1.0)]
        st = _half_stats(trades, extra_cost=1.5)
        assert st["expectancy_pct"] == -1.0
        assert st["win_rate_pct"] == 50.0
        assert st["profit_factor"] == 0.2

    def test_extra_cost_changes_win_rate(self):
        # adjusted pnl: [-0.5, 2.5, -3.5] → only one winner survives stress
        trades = [_trade(1.0), _trade(4.0), _trade(-2.0)]
        st = _half_stats(trades, extra_cost=1.5)
        assert st["win_rate_pct"] == pytest.approx(33.3)
        assert st["profit_factor"] == pytest.approx(2.5 / 4.0, abs=0.01)

    def test_stressed_pf_never_exceeds_raw_pf(self):
        trades = [_trade(p) for p in (3.0, 1.2, 0.4, -0.8, -2.0, 5.5, -0.1)]
        raw = _profit_factor(trades)
        stressed = _profit_factor(trades, extra_cost=2.0)
        assert stressed < raw

    def test_all_losers_under_stress(self):
        st = _half_stats([_trade(0.5), _trade(1.0)], extra_cost=2.0)
        assert st["win_rate_pct"] == 0.0
        assert st["profit_factor"] == 0.0

    def test_empty_trades(self):
        st = _half_stats([], extra_cost=1.5)
        assert st == {"n": 0, "win_rate_pct": 0.0, "expectancy_pct": 0.0,
                      "profit_factor": 0.0}


# -----------------------------------------------------------------------------
# H2 — the G3 purge must actually drop entries whose holds leak across the
# NEXT fold boundary. (The old predicate required year==y AND a date in the
# prior December — unsatisfiable, so zero trades were ever purged.)
# -----------------------------------------------------------------------------

class TestFoldPurge:
    def test_late_december_entry_is_purged_from_its_fold(self):
        trades = [
            _trade(1.0, date="2023-06-01"),
            _trade(1.0, date="2023-12-15"),  # within 30d of 2024-01-01 → purged
            _trade(1.0, date="2023-12-31"),  # within 30d → purged
        ]
        st = _fold_stats(trades, fold_years=(2023,), purge_days=30)
        assert st["folds"]["2023"]["n"] == 1

    def test_entry_just_outside_embargo_is_kept(self):
        trades = [
            _trade(1.0, date="2023-12-01"),  # 31 days before boundary → kept
            _trade(1.0, date="2023-12-02"),  # exactly purge_days before → purged
        ]
        st = _fold_stats(trades, fold_years=(2023,), purge_days=30)
        assert st["folds"]["2023"]["n"] == 1

    def test_january_entries_belong_to_their_own_fold(self):
        trades = [_trade(1.0, date="2024-01-05"), _trade(-1.0, date="2024-07-01")]
        st = _fold_stats(trades, fold_years=(2023, 2024), purge_days=30)
        assert st["folds"]["2024"]["n"] == 2
        assert st["folds"]["2023"]["n"] == 0

    def test_purge_applies_extra_cost_to_folds(self):
        trades = [_trade(1.0, date="2023-06-01"), _trade(1.0, date="2023-07-01")]
        st = _fold_stats(trades, fold_years=(2023,), purge_days=30, extra_cost=1.5)
        assert st["folds"]["2023"]["expectancy_pct"] == -0.5
        assert st["folds"]["2023"]["win_rate_pct"] == 0.0

    def test_positive_fold_counting_unaffected_by_empty_folds(self):
        trades = [_trade(2.0, date="2023-03-01")]
        st = _fold_stats(trades, fold_years=(2022, 2023), purge_days=30)
        assert st["folds_positive"] == 1
        assert st["folds_total"] == 2


# -----------------------------------------------------------------------------
# M1 — the declared round-trip cost constant must equal what simulate_daily
# actually charges: slippage on BOTH sides plus fees on both sides.
# -----------------------------------------------------------------------------

class TestCostConstant:
    def test_constant_includes_both_slippage_sides(self):
        assert ROUND_TRIP_COST_PCT == pytest.approx(SLIPPAGE_PCT * 2 + FEE_PCT * 2)
        assert ROUND_TRIP_COST_PCT == pytest.approx(0.96)

    def test_flat_price_round_trip_loses_approximately_the_constant(self):
        # Price never moves: stop/target never hit, time exit at entry price.
        n = 40
        info = {"closes": [100.0] * n, "highs": [100.0] * n, "lows": [100.0] * n}
        res = simulate_daily(info, d=5, stop_pct=50.0, target_pct=500.0, max_hold=10)
        assert res["exit_reason"] == "time_exit"
        # Slippage compounds multiplicatively, so allow a small tolerance.
        assert res["pnl_pct"] == pytest.approx(-ROUND_TRIP_COST_PCT, abs=0.01)
        assert res["outcome"] == "loss"
