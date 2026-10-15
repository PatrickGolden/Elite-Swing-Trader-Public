# Helpers behind the Q4 registered trials (VENUE-2026Q4-1, H-2026Q4-2). A trial runs
# ONCE, so these pin the parts that could silently bias it: look-ahead in the regime
# condition, the one-shot guard, and the daily alt-book reproducing trade P&L.

import json

import pytest

import stage1c_replay as s


def _info(sym, closes, start="2026-01-01"):
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(len(closes))]
    return {"symbol": sym, "closes": list(closes), "highs": list(closes), "lows": list(closes),
            "dates": dates, "by_date": {d: i for i, d in enumerate(dates)}, "terminal": "live_edge"}


def test_eth_btc_gate_uses_sma_through_t_minus_1_only():
    n = 60
    btc = _info("BTC", [100.0] * n)
    eth_closes = [5.0] * n
    eth_closes[55] = 6.0                      # one-day spike at t=55
    uni = {"bitcoin": btc, "ETH|0": _info("ETH", eth_closes)}
    gate, ok = s._eth_btc_gate(uni)
    d = btc["dates"]
    assert gate(d[55]) is True                # today's close vs SMA of the PRIOR 50 days
    assert gate(d[54]) is False               # flat ratio: not strictly above
    assert gate(d[56]) is False               # 5.0 < prior SMA that now includes the spike
    assert gate(d[10]) is False               # < 50 days of history: fail-closed
    assert gate("1999-01-01") is False        # unknown date: fail-closed


def test_registration_guard_is_one_shot(tmp_path, monkeypatch):
    f = tmp_path / "trials.jsonl"
    reg = {"kind": "registered", "trial_id": "T-1", "run_not_before": "2026-10-01", "config_hash": "x"}
    f.write_text(json.dumps(reg) + "\n", encoding="utf-8")
    monkeypatch.setattr(s, "TRIALS_FILE", str(f))
    assert s._load_registration("T-1")["trial_id"] == "T-1"
    with pytest.raises(RuntimeError, match="not registered"):
        s._load_registration("T-404")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "result", "trial_id": "T-1", "verdict": "FAIL"}) + "\n")
    with pytest.raises(RuntimeError, match="already has a recorded result"):
        s._load_registration("T-1")


def test_registration_guard_respects_run_not_before(tmp_path, monkeypatch):
    f = tmp_path / "trials.jsonl"
    f.write_text(json.dumps({"kind": "registered", "trial_id": "T-2", "run_not_before": "2999-01-01"}) + "\n",
                 encoding="utf-8")
    monkeypatch.setattr(s, "TRIALS_FILE", str(f))
    with pytest.raises(RuntimeError, match="may not run before"):
        s._load_registration("T-2")


def test_portfolio_curve_reproduces_trade_pnl():
    closes = [10.0, 11.0, 9.0, 12.0, 12.0]
    uni = {"ALT|0": _info("ALT", closes)}
    btc_dates = uni["ALT|0"]["dates"]
    gross = 15.0                               # e.g. slippage-adjusted 10 -> 11.5
    trade = {"gid": "ALT|0", "di": 0, "hold_days": 3, "gross_pnl_pct": gross}
    curve = s._portfolio_curve([trade], uni, btc_dates, start=btc_dates[0], extra_cost=0.0, slot_frac=1.0)
    # One full-size slot: daily returns follow closes and telescope to the trade's gross
    # factor; the exit day also carries the modeled fees, so the end value is
    # (1 + gross) - fee * p2, where p2 is the path value the day before exit.
    fee_hit = (s.FEE_PCT * 2) / 100
    p2 = closes[2] / closes[0]
    assert curve[3] == pytest.approx((1 + gross / 100) - fee_hit * p2, rel=1e-9)
    assert min(curve) < 1.0                   # intra-trade drawdown is visible (day 2 = 9.0)


def test_random_entry_baseline_ranks_a_perfect_picker_top():
    # Two eligible coins each day; the strategy always picks the winner.
    up = _info("UP", [10, 10, 12, 12, 12, 12, 12, 12, 12, 12])
    dn = _info("DN", [10, 10, 8, 8, 8, 8, 8, 8, 8, 8])
    uni = {"UP|0": up, "DN|0": dn}
    elig = {up["dates"][1]: [("UP|0", 1), ("DN|0", 1)]}
    res = s.simulate_daily(up, 1, 50.0, 500.0, max_hold=2)
    trades = [{"date": up["dates"][1], "pnl_pct": res["pnl_pct"], "stop_pct": 50.0, "target_pct": 500.0}] * 12
    out = s._random_entry_baseline(trades, uni, {**elig}, max_hold=2, extra_cost=0.0, n_resamples=500)
    assert out["strategy_mean_pct"] > out["baseline_median_pct"]
    assert out["strategy_mean_pct"] >= out["p95_mean_pct"]
