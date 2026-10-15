# tests/unit/test_stage1b_promotion.py — Stage 1b paper→live promotion monitor.

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_NOW = datetime.now(timezone.utc)

# Capture the real delivery fn before the autouse fixture stubs it, so the
# delivery test can exercise it directly.
from stage1b_promotion import _send_alert as _REAL_SEND_ALERT  # noqa: E402


def _ptrade(pnl, days_ago, *, risk="RISK_ON", path="momentum_breakout"):
    entry = (_NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    exit_ = (_NOW - timedelta(days=max(0, days_ago - 1))).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "outcome": "win" if pnl > 0 else "loss",
        "pnl_pct": float(pnl),
        "pnl_usd": float(pnl) * 10.0,
        "entry_time": entry,
        "exit_time": exit_,
        "quant_path": path,
        "risk_regime": risk,
        "status": "CLOSED",
        "source": "paper_trade",
    }


def _good_paper(n=30, win_pnl=6.0, loss_pnl=-3.0, n_losses=9):
    """A clearly-passing paper sample: high WR, positive expectancy, 29d span."""
    trades = []
    for i in range(n):
        pnl = loss_pnl if i < n_losses else win_pnl
        trades.append(_ptrade(pnl, days_ago=(n - 1 - i)))
    return trades


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect state/transition files and silence alerts for every test."""
    import stage1b_promotion as sp
    monkeypatch.setattr(sp, "PROMO_STATE_FILE", tmp_path / "promo.json")
    monkeypatch.setattr(sp, "PROMO_TRANSITIONS_FILE", tmp_path / "promo.jsonl")
    monkeypatch.setattr(sp, "_send_alert", lambda *a, **k: None)
    yield


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------

class TestEvaluate:
    def test_go_on_strong_sample(self):
        from stage1b_promotion import evaluate_promotion
        d = evaluate_promotion(paper_trades=_good_paper(), live_trades=[])
        assert d["decision"] == "GO", d["failing"]
        assert d["failing"] == []

    def test_hold_small_sample(self):
        from stage1b_promotion import evaluate_promotion
        d = evaluate_promotion(paper_trades=_good_paper(n=10, n_losses=3), live_trades=[])
        assert d["decision"] == "HOLD"
        assert "sample_size" in d["failing"]

    def test_hold_negative_expectancy(self):
        from stage1b_promotion import evaluate_promotion
        # Mostly losers: fails expectancy / pf / luck / cost.
        trades = []
        for i in range(30):
            pnl = 2.0 if i < 9 else -4.0
            trades.append(_ptrade(pnl, days_ago=(29 - i)))
        d = evaluate_promotion(paper_trades=trades, live_trades=[])
        assert d["decision"] == "HOLD"
        assert "expectancy" in d["failing"]

    def test_gate_integrity_catches_contrarian_in_risk_on(self):
        from stage1b_promotion import evaluate_promotion
        trades = _good_paper()
        # Inject a forbidden contrarian-in-risk-on entry.
        trades[0] = _ptrade(6.0, days_ago=5, risk="RISK_ON", path="blood_in_the_street")
        d = evaluate_promotion(paper_trades=trades, live_trades=[])
        assert d["metrics"]["contrarian_in_risk_on"] == 1
        assert "gate_integrity" in d["failing"]

    def test_gate_integrity_allows_contrarian_in_risk_off(self):
        from stage1b_promotion import evaluate_promotion
        trades = _good_paper()
        trades[0] = _ptrade(6.0, days_ago=5, risk="RISK_OFF", path="blood_in_the_street")
        d = evaluate_promotion(paper_trades=trades, live_trades=[])
        assert d["metrics"]["contrarian_in_risk_on"] == 0
        assert "gate_integrity" not in d["failing"]

    def test_beats_baseline_uses_live(self):
        from stage1b_promotion import evaluate_promotion
        good = _good_paper()
        # Live baseline far above paper expectancy ⇒ beats_baseline fails.
        live = [_ptrade(50.0, days_ago=2) for _ in range(5)]
        for t in live:
            t["source"] = "live"
        d = evaluate_promotion(paper_trades=good, live_trades=live)
        assert "beats_baseline" in d["failing"]


# -----------------------------------------------------------------------------
# Lean evidence isolation (Stage 1c): with LEAN_ENTRY_MODE on, the GO verdict
# is earned by lean-compliant paper trades only — legacy history is excluded.
# -----------------------------------------------------------------------------

def _lean_trade(pnl, days_ago):
    """A lean-compliant closed paper trade (allowed path, risk-on, lean stop, mom30 floor)."""
    t = _ptrade(pnl, days_ago, risk="RISK_ON", path="momentum_breakout")
    t["ai_stop_pct"] = 24.0
    t["price_change_30d"] = 25.0
    return t


class TestLeanEvidenceIsolation:
    def test_lean_off_scores_all_paper(self):
        """Default (lean off): every closed paper trade counts — no regression."""
        from stage1b_promotion import evaluate_promotion
        d = evaluate_promotion(paper_trades=_good_paper(), live_trades=[])
        assert d["metrics"]["lean_scoring_active"] is False
        assert d["metrics"]["n_closed"] == 30
        assert d["metrics"]["n_legacy_paper_excluded"] == 0

    def test_lean_on_excludes_legacy_paper(self):
        """Lean on: legacy paper (no lean tag, non-lean stop/mom) is excluded from scoring."""
        from thresholds import threshold_override
        from stage1b_promotion import evaluate_promotion
        legacy = _good_paper()  # profitable but not lean-compliant
        lean = [_lean_trade(-2.0, days_ago=i) for i in range(5)]
        with threshold_override({"LEAN_ENTRY_MODE": 1.0}):
            d = evaluate_promotion(paper_trades=legacy + lean, live_trades=[])
        assert d["metrics"]["lean_scoring_active"] is True
        assert d["metrics"]["n_closed"] == 5           # lean cohort only
        assert d["metrics"]["n_legacy_paper_excluded"] == 30
        assert d["decision"] == "HOLD"                  # 5 losers can't earn GO

    def test_lean_on_respects_explicit_tag(self):
        """lean_compliant tag written at open wins over re-derivation."""
        from thresholds import threshold_override
        from stage1b_promotion import evaluate_promotion
        tagged_in = _ptrade(6.0, days_ago=3)
        tagged_in["lean_compliant"] = True
        tagged_out = _lean_trade(6.0, days_ago=2)
        tagged_out["lean_compliant"] = False
        with threshold_override({"LEAN_ENTRY_MODE": 1.0}):
            d = evaluate_promotion(paper_trades=[tagged_in, tagged_out], live_trades=[])
        assert d["metrics"]["n_closed"] == 1
        assert d["metrics"]["n_legacy_paper_excluded"] == 1

    def test_lean_on_no_lean_trades_holds(self):
        """A profitable legacy book alone must never earn GO under lean."""
        from thresholds import threshold_override
        from stage1b_promotion import evaluate_promotion
        with threshold_override({"LEAN_ENTRY_MODE": 1.0}):
            d = evaluate_promotion(paper_trades=_good_paper(), live_trades=[])
        assert d["decision"] == "HOLD"
        assert d["metrics"]["n_closed"] == 0


# -----------------------------------------------------------------------------
# Arm / promote / revert
# -----------------------------------------------------------------------------

def _fake_mode(monkeypatch, *, paper=True, test=False):
    import test_mode as tm
    holder = {"paper": paper, "test": test}
    monkeypatch.setattr(tm, "get_paper_trading", lambda: holder["paper"])
    monkeypatch.setattr(tm, "get_test_mode", lambda: holder["test"])

    def _set_paper(v):
        holder["paper"] = bool(v)
        return True
    monkeypatch.setattr(tm, "set_paper_trading", _set_paper)
    return holder


class TestArmAndPromote:
    def test_default_disarmed(self):
        from stage1b_promotion import load_promo_state
        assert load_promo_state()["auto_promote_armed"] is False

    def test_arm_toggles_state(self):
        from stage1b_promotion import arm_auto_promote, load_promo_state
        arm_auto_promote(True)
        assert load_promo_state()["auto_promote_armed"] is True
        arm_auto_promote(False)
        assert load_promo_state()["auto_promote_armed"] is False

    def test_promotion_blocked_in_test_mode(self, monkeypatch):
        import stage1b_promotion as sp
        _fake_mode(monkeypatch, paper=True, test=True)  # test mode on ⇒ fail-closed
        res = sp._perform_promotion(sp.evaluate_promotion(paper_trades=_good_paper(), live_trades=[]))
        assert res["promoted"] is False
        assert res["reason"] == "test_mode_on"

    def test_promotion_flips_when_armed_paper_and_not_test(self, monkeypatch):
        import stage1b_promotion as sp
        holder = _fake_mode(monkeypatch, paper=True, test=False)
        res = sp._perform_promotion(sp.evaluate_promotion(paper_trades=_good_paper(), live_trades=[]))
        assert res["promoted"] is True
        assert holder["paper"] is False  # flipped to live
        assert sp.load_promo_state()["promoted_at"] is not None

    def test_unarmed_go_does_not_promote(self, monkeypatch):
        import stage1b_promotion as sp
        holder = _fake_mode(monkeypatch, paper=True, test=False)
        monkeypatch.setattr(sp, "_load_paper_closed", lambda: _good_paper())
        monkeypatch.setattr(sp, "_load_live_closed", lambda: [])
        res = sp.run_promotion_check(send_alert=True, allow_auto=True)
        assert res["decision"]["decision"] == "GO"
        assert res["armed"] is False
        assert holder["paper"] is True  # still paper
        assert sp.load_promo_state()["pending_since"] is not None

    def test_armed_go_auto_promotes_via_run_check(self, monkeypatch):
        import stage1b_promotion as sp
        holder = _fake_mode(monkeypatch, paper=True, test=False)
        monkeypatch.setattr(sp, "_load_paper_closed", lambda: _good_paper())
        monkeypatch.setattr(sp, "_load_live_closed", lambda: [])
        sp.arm_auto_promote(True)
        res = sp.run_promotion_check(send_alert=True, allow_auto=True)
        assert res["decision"]["decision"] == "GO"
        assert holder["paper"] is False  # auto-promoted
        assert any("promotion" in a for a in res["actions"])


class TestAlertDelivery:
    def test_emails_primary_even_when_toggle_disabled(self, monkeypatch):
        """A GO/arm alert must email the primary owner regardless of email_enabled."""
        import alert_system
        calls = {"email": [], "telegram": []}

        class _Fake:
            telegram_configured = True
            email_configured = True

            def _primary_recipients(self):
                return ["owner@example.com"]

            def _send_telegram(self, s, b):
                calls["telegram"].append((s, b))
                return True, None

            def _send_email(self, s, bt, bh, recipients=None):
                calls["email"].append((s, recipients))
                return True, None

        monkeypatch.setattr(alert_system, "AlertSystem", _Fake)
        _REAL_SEND_ALERT("Stage 1b Promotion: GO", "arm me")
        assert calls["telegram"], "telegram not attempted"
        assert calls["email"] and calls["email"][0][1] == ["owner@example.com"]

    def test_no_channel_configured_is_safe(self, monkeypatch):
        import alert_system

        class _Fake:
            telegram_configured = False
            email_configured = False

        monkeypatch.setattr(alert_system, "AlertSystem", _Fake)
        _REAL_SEND_ALERT("subj", "body")  # must not raise


class TestKillSwitch:
    def test_reverts_on_live_degradation(self, monkeypatch):
        import stage1b_promotion as sp
        holder = _fake_mode(monkeypatch, paper=False, test=False)  # currently live
        sp.save_promo_state({
            "promoted_at": (_NOW - timedelta(days=10)).isoformat(),
            "auto_promote_armed": True,
        })
        # 12 losing live trades since promotion ⇒ deep drawdown + negative expectancy.
        bad_live = []
        for i in range(12):
            t = _ptrade(-3.0, days_ago=(9 - i % 9), risk="RISK_ON")
            t["source"] = "live"
            bad_live.append(t)
        monkeypatch.setattr(sp, "_load_live_closed", lambda: bad_live)
        res = sp._check_kill_switch(sp.load_promo_state())
        assert res is not None and res["reverted"] is True
        assert holder["paper"] is True  # demoted back to paper
        assert sp.load_promo_state()["promoted_at"] is None

    def test_no_revert_before_min_live_trades(self, monkeypatch):
        import stage1b_promotion as sp
        _fake_mode(monkeypatch, paper=False, test=False)
        sp.save_promo_state({"promoted_at": (_NOW - timedelta(days=5)).isoformat()})
        few = [_ptrade(-5.0, days_ago=1) for _ in range(3)]
        for t in few:
            t["source"] = "live"
        monkeypatch.setattr(sp, "_load_live_closed", lambda: few)
        assert sp._check_kill_switch(sp.load_promo_state()) is None
