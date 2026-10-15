# Tests for the rotation paper cohort (STRATEGY.md candidate #1).
#
# Invariants:
#   - risk-on with no cohort position → opens exactly one paper BTC position
#     (source='paper_rotation', is_test=True), idempotent across refreshes.
#   - risk-off (or unknown) with a cohort position → closes it with
#     exit_reason='risk_off_flip'; never opens on risk_on None/False (fail-closed).
#   - cohort coexists with a lean paper position in the SAME symbol (dedup is
#     scoped per source within paper mode) while real positions stay single.

from unittest.mock import patch

import pytest

import rotation_tracker
from rotation_tracker import track_rotation, ROTATION_SOURCE


@pytest.fixture
def _risk(monkeypatch):
    state = {"risk_on": True, "btc_vs_ma200_pct": 5.0, "btc_30d_change": 10.0, "source": "ma200"}

    def set_risk(value):
        state["risk_on"] = value

    with patch("risk_regime.compute_btc_risk_on", side_effect=lambda *a, **k: dict(state)):
        yield set_risk


def _cohort_open():
    from trade_journal import get_open_positions
    return [p for p in get_open_positions(exclude_test=False)
            if (p.get("source") or "") == ROTATION_SOURCE]


def test_opens_once_on_risk_on_and_is_idempotent(temp_positions, _risk):
    assert track_rotation(50000.0) == "opened"
    assert track_rotation(51000.0) is None  # already in position — no double entry
    pos = _cohort_open()
    assert len(pos) == 1
    assert pos[0]["coin_symbol"] == "BTC"
    assert pos[0]["is_test"] is True


def test_entry_and_exit_fire_milestone_email(temp_positions, _risk):
    """STRATEGY.md v1.7c: the owner is pinged the moment the cohort opens/closes.
    Pins the wiring so a refactor can't silently drop the alert (best-effort, so a
    broken emailer must NOT break the trade)."""
    with patch("alert_system.AlertSystem.send_critical_alert") as m:
        assert track_rotation(50000.0) == "opened"
        assert m.call_count == 1
        assert "ENTERED" in m.call_args.kwargs.get("subject", "")
        assert m.call_args.kwargs.get("alert_type") == "rotation_milestone"
        _risk(False)
        assert track_rotation(45000.0) == "closed"
        assert m.call_count == 2
        assert "EXITED" in m.call_args.kwargs.get("subject", "")


def test_milestone_email_failure_never_breaks_the_trade(temp_positions, _risk):
    with patch("alert_system.AlertSystem.send_critical_alert",
               side_effect=RuntimeError("smtp down")):
        assert track_rotation(50000.0) == "opened"  # trade still journals
    assert len(_cohort_open()) == 1


def test_closes_on_risk_off_flip(temp_positions, _risk):
    assert track_rotation(50000.0) == "opened"
    _risk(False)
    assert track_rotation(45000.0) == "closed"
    assert _cohort_open() == []
    from trade_journal import get_all_positions
    closed = [p for p in get_all_positions(exclude_test=False)
              if (p.get("source") or "") == ROTATION_SOURCE and p.get("status") == "CLOSED"]
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "risk_off_flip"
    # NET of measured fill costs (fill_cost_model, 0.90%/side since 2026-10-11): −10% market
    # move − ~1.71% fees − ~0.1% slippage ≈ −11.8%. The track record must be cost-honest (§4.2).
    assert closed[0]["pnl_pct"] == pytest.approx(-11.8, abs=0.5)
    assert float(closed[0].get("entry_fee_usd") or 0) > 0
    assert float(closed[0].get("exit_fee_usd") or 0) > 0


def test_fail_closed_on_unknown_risk_state(temp_positions, _risk):
    _risk(None)
    assert track_rotation(50000.0) is None
    assert _cohort_open() == []


def test_no_action_without_price(temp_positions, _risk):
    assert track_rotation(None) is None
    assert track_rotation(0) is None
    assert _cohort_open() == []


def test_cohort_coexists_with_lean_paper_btc_but_real_stays_single(temp_positions, _risk):
    from trade_journal import open_position

    lean_paper = open_position({
        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
        "signal": "BUY", "entry_price": 50000, "quantity": 0.001,
        "is_test": True, "source": "paper_trade", "lean_override": True,
        "ai_stop_pct": 24, "ai_target_pct": 150,
    })
    assert lean_paper is not None
    assert track_rotation(50000.0) == "opened"   # not blocked by the lean paper BTC

    # Same cohort still deduped: a second rotation BTC must be rejected
    dup = open_position({
        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
        "signal": "BUY", "entry_price": 50000, "quantity": 0.001,
        "is_test": True, "source": ROTATION_SOURCE, "lean_override": True,
        "ai_stop_pct": 0, "ai_target_pct": 0,
    })
    assert dup is None

    # Real positions: single per symbol regardless of source
    real1 = open_position({
        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
        "signal": "BUY", "entry_price": 50000, "quantity": 0.001,
        "is_test": False, "lean_override": True, "ai_stop_pct": 24, "ai_target_pct": 150,
    })
    assert real1 is not None
    real2 = open_position({
        "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC",
        "signal": "BUY", "entry_price": 50000, "quantity": 0.001,
        "is_test": False, "source": "another_source", "lean_override": True,
        "ai_stop_pct": 24, "ai_target_pct": 150,
    })
    assert real2 is None
