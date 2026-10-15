# =============================================================================
# stage1b_promotion.py — Paper→Live promotion monitor for the Stage 1b
# momentum/trend gate.
#
# WHAT THIS DOES
#   Every cycle it reads CLOSED paper trades, scores them against a fixed set of
#   GO/NO-GO criteria, and emits a GO / HOLD verdict (Telegram + log). When (and
#   only when) the operator has explicitly ARMED auto-promotion, a GO verdict
#   flips the system from paper to live trading — with hard guardrails and an
#   automatic kill-switch that reverts to paper if live results degrade.
#
# SAFETY MODEL (per .cursor/rules/roadmap-automation-framework)
#   Enabling real-capital trading is the highest-risk action in the system, so
#   it NEVER auto-applies by default. `auto_promote_armed` defaults to False:
#   a GO only sends an alert + writes a pending record. The operator arms it
#   deliberately (one command / API call). Even armed, promotion is fail-closed:
#   criteria are re-checked at the moment of the flip, test-mode must be off, and
#   a degradation kill-switch can demote back to paper at any time.
# =============================================================================

from __future__ import annotations

import argparse
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = _PROJECT_DIR / "data"
PROMO_STATE_FILE = DATA_DIR / "stage1b_promotion.json"
PROMO_TRANSITIONS_FILE = DATA_DIR / "promotion_transitions.jsonl"

# Contrarian / fear-dip entry paths that Stage 1b forbids while BTC is risk-on.
# Used only for a belt-and-suspenders audit; the signal layer already blocks them.
CONTRARIAN_PATHS = frozenset({
    "blood_in_the_street", "dip", "capitulation", "contrarian", "fear_dip",
})


def _is_lean_compliant_paper_trade(trade: Dict[str, Any]) -> bool:
    """True when a closed paper trade belongs to the Stage 1c lean validation cohort.

    Prefers the lean_compliant tag written at open; for pre-tag trades, re-derives
    from journal primitives via lean_policy (fail-closed: missing data → False).
    """
    tagged = trade.get("lean_compliant")
    if isinstance(tagged, bool):
        return tagged
    try:
        import lean_policy
        return lean_policy.assert_fill_compliant(trade) is None
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Config / state
# -----------------------------------------------------------------------------

def _default_state() -> Dict[str, Any]:
    return {
        "auto_promote_armed": False,
        "last_decision": None,      # "GO" | "HOLD"
        "last_alert_date": None,    # YYYY-MM-DD (UTC)
        "pending_since": None,      # iso ts of first GO while disarmed
        "promoted_at": None,        # iso ts when auto-promotion flipped to live
        "promoted_metrics": None,   # metrics snapshot at promotion time
        "reverted_at": None,        # iso ts of last kill-switch revert
    }


def load_promo_state() -> Dict[str, Any]:
    import safe_json
    data = safe_json.load(str(PROMO_STATE_FILE), default=dict)
    state = _default_state()
    if isinstance(data, dict):
        state.update(data)
    return state


def save_promo_state(updates: Dict[str, Any]) -> Dict[str, Any]:
    import safe_json
    state = load_promo_state()
    state.update(updates)
    safe_json.save(str(PROMO_STATE_FILE), state, default=str)
    return state


def _t(key: str, default: float) -> float:
    try:
        from thresholds import get_threshold
        v = get_threshold(key, default)
        return float(v if v is not None else default)
    except Exception:
        return float(default)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# -----------------------------------------------------------------------------
# Trade loading
# -----------------------------------------------------------------------------

def _load_paper_closed() -> List[Dict[str, Any]]:
    try:
        from paper_evidence import load_paper_closed
        return load_paper_closed()
    except Exception:
        return []


def _load_live_closed() -> List[Dict[str, Any]]:
    """Closed REAL trades (not paper, not test) — used for baseline + kill-switch."""
    try:
        from trade_journal import get_all_positions
        rows = get_all_positions(exclude_test=True)
    except Exception:
        return []
    return [
        p for p in rows
        if (p.get("status") or "").upper() == "CLOSED"
        and p.get("source") != "paper_trade"
    ]


def _trade_ts(trade: Dict[str, Any], field: str) -> str:
    return str(trade.get(field) or "")[:19]


# -----------------------------------------------------------------------------
# Stats helpers
# -----------------------------------------------------------------------------

def _expectancy(trades: List[Dict[str, Any]]) -> float:
    vals = [float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _profit_factor(trades: List[Dict[str, Any]]) -> Optional[float]:
    gains = sum(float(t.get("pnl_usd") or 0) for t in trades if (t.get("pnl_usd") or 0) > 0)
    losses = sum(-float(t.get("pnl_usd") or 0) for t in trades if (t.get("pnl_usd") or 0) < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return gains / losses


def _wilson_lb(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    import math
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom) * 100.0


def _bootstrap_expectancy_lb(
    pnls: List[float], *, n_boot: int = 2000, pct: float = 10.0, seed: int = 12345
) -> Optional[float]:
    """Lower-tail (pct-th percentile) of the bootstrap distribution of mean pnl%."""
    if len(pnls) < 5:
        return None
    rng = random.Random(seed)
    n = len(pnls)
    means: List[float] = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += pnls[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    idx = max(0, min(len(means) - 1, int((pct / 100.0) * len(means))))
    return means[idx]


def _span_days(trades: List[Dict[str, Any]]) -> int:
    times: List[datetime] = []
    for t in trades:
        raw = t.get("entry_time") or t.get("exit_time")
        if not raw:
            continue
        s = str(raw).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            times.append(dt)
        except (ValueError, TypeError):
            continue
    if len(times) < 2:
        return 0
    return (max(times) - min(times)).days


def _max_drawdown_pct(trades_sorted: List[Dict[str, Any]]) -> float:
    """Max peak-to-trough drawdown of the cumulative pnl% curve (in pct points)."""
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades_sorted:
        if t.get("pnl_pct") is None:
            continue
        cum += float(t["pnl_pct"])
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------

def evaluate_promotion(
    paper_trades: Optional[List[Dict[str, Any]]] = None,
    live_trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Score closed paper trades against the GO/NO-GO criteria. Pure + side-effect-free."""
    paper = paper_trades if paper_trades is not None else _load_paper_closed()
    live = live_trades if live_trades is not None else _load_live_closed()

    # Lean evidence isolation (Stage 1c): when lean mode is active, the GO verdict
    # must be earned by lean-compliant paper trades only. Blending in legacy paper
    # history (535 trades at ~-0.94% expectancy) would measure the wrong strategy.
    lean_scoring = False
    try:
        import lean_policy
        lean_scoring = lean_policy.is_lean_active()
    except Exception:
        pass

    all_closed = [t for t in paper if t.get("outcome") in ("win", "loss")]
    if lean_scoring:
        closed = [t for t in all_closed if _is_lean_compliant_paper_trade(t)]
        n_legacy = len(all_closed) - len(closed)
    else:
        closed = all_closed
        n_legacy = 0
    n = len(closed)
    wins = sum(1 for t in closed if t.get("outcome") == "win")
    pnls = [float(t["pnl_pct"]) for t in closed if t.get("pnl_pct") is not None]

    expectancy = _expectancy(closed)
    pf = _profit_factor(closed)
    wilson = _wilson_lb(wins, n)
    span = _span_days(closed)
    boot_lb = _bootstrap_expectancy_lb(pnls)
    live_baseline = _expectancy([t for t in live if t.get("outcome") in ("win", "loss")])

    # Gate-integrity audit: forbidden contrarian-in-risk-on entries. The signal
    # layer already blocks these, so this verifies nothing slipped through.
    audited = [t for t in closed if t.get("risk_regime")]
    contrarian_risk_on = sum(
        1 for t in audited
        if (t.get("quant_path") or "").strip().lower() in CONTRARIAN_PATHS
        and str(t.get("risk_regime")).strip().upper() == "RISK_ON"
    )

    # Cost robustness: re-charge an extra fraction of a round-trip cost.
    rt_cost = _t("ROUND_TRIP_COST_PCT", 0.66)
    cost_stress = _t("STAGE1B_PROMO_COST_STRESS_MULT", 0.5) * rt_cost
    expectancy_stressed = expectancy - cost_stress

    # Thresholds
    min_closed = int(_t("STAGE1B_PROMO_MIN_CLOSED", 30))
    min_days = int(_t("STAGE1B_PROMO_MIN_DAYS", 21))
    min_exp = _t("STAGE1B_PROMO_MIN_EXPECTANCY", 0.30)
    min_pf = _t("STAGE1B_PROMO_MIN_PF", 1.30)
    min_wilson = _t("STAGE1B_PROMO_WILSON_LB", 45.0)
    boot_floor = _t("STAGE1B_PROMO_BOOTSTRAP_FLOOR", 0.0)

    criteria: List[Dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        criteria.append({"name": name, "passed": bool(passed), "detail": detail})

    add("sample_size", n >= min_closed, f"{n} closed / need {min_closed}")
    add("time_span", span >= min_days, f"{span}d span / need {min_days}d")
    add("expectancy", expectancy >= min_exp, f"{expectancy:.3f}% / need ≥{min_exp:.2f}%")
    add("profit_factor",
        pf is not None and pf >= min_pf,
        (f"{pf:.2f}" if pf not in (None, float('inf')) else ("∞" if pf == float('inf') else "n/a")) + f" / need ≥{min_pf:.2f}")
    add("beats_baseline", expectancy > live_baseline,
        f"paper {expectancy:.3f}% vs live baseline {live_baseline:.3f}%")
    add("gate_integrity", contrarian_risk_on == 0,
        f"{contrarian_risk_on} contrarian-in-risk-on (audited {len(audited)}/{n})")
    add("luck_robustness",
        wilson >= min_wilson and (boot_lb is not None and boot_lb > boot_floor),
        f"WilsonLB {wilson:.1f}%≥{min_wilson:.0f}%, bootP10 "
        + (f"{boot_lb:.3f}%>{boot_floor:.2f}%" if boot_lb is not None else "n/a"))
    add("cost_robustness", expectancy_stressed > 0,
        f"{expectancy_stressed:.3f}% after +{cost_stress:.2f}% cost stress")

    failing = [c["name"] for c in criteria if not c["passed"]]
    decision = "GO" if not failing else "HOLD"

    return {
        "decision": decision,
        "evaluated_at": _now_iso(),
        "failing": failing,
        "criteria": criteria,
        "metrics": {
            "n_closed": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate_pct": round(wins / n * 100, 1) if n else 0.0,
            "expectancy_pct": round(expectancy, 3),
            "expectancy_stressed_pct": round(expectancy_stressed, 3),
            "profit_factor": (round(pf, 2) if pf not in (None, float("inf")) else pf),
            "wilson_lb_pct": round(wilson, 1),
            "bootstrap_p10_pct": (round(boot_lb, 3) if boot_lb is not None else None),
            "span_days": span,
            "live_baseline_expectancy_pct": round(live_baseline, 3),
            "contrarian_in_risk_on": contrarian_risk_on,
            "lean_scoring_active": lean_scoring,
            "n_legacy_paper_excluded": n_legacy,
        },
    }


# -----------------------------------------------------------------------------
# Formatting + transition log
# -----------------------------------------------------------------------------

def format_decision_text(d: Dict[str, Any], *, armed: bool) -> Tuple[str, str]:
    m = d["metrics"]
    if d["decision"] == "GO":
        head = "Stage 1b Promotion: GO ✅"
    else:
        head = "Stage 1b Promotion: HOLD"
    lines = [
        f"Decision: {d['decision']}",
        f"Paper trades: {m['n_closed']} closed (WR {m['win_rate_pct']}%, "
        f"expectancy {m['expectancy_pct']}%, PF {m['profit_factor']}), span {m['span_days']}d",
        "",
        "Criteria:",
    ]
    for c in d["criteria"]:
        mark = "✓" if c["passed"] else "✗"
        lines.append(f"  {mark} {c['name']}: {c['detail']}")
    lines.append("")
    if d["decision"] == "GO":
        if armed:
            lines.append("Auto-promote ARMED → flipping paper→live now (kill-switch active).")
        else:
            lines.append("Auto-promote DISARMED. To go live, arm it:")
            lines.append("  python stage1b_promotion.py --arm")
    else:
        lines.append(f"Blocking: {', '.join(d['failing'])}")
    return head, "\n".join(lines)


def _log_transition(event: str, payload: Dict[str, Any]) -> None:
    try:
        import safe_json
        row = {"ts": _now_iso(), "event": event, **payload}
        safe_json.append_jsonl(str(PROMO_TRANSITIONS_FILE), row, max_lines=5000, default=str)
    except Exception:
        pass


def _send_alert(subject: str, body: str) -> None:
    """Deliver a promotion alert to the primary owner + Telegram.

    Bypasses the email_enabled UI toggle (like the daily status report): a
    "ready to arm" / promote / revert event is a rare, important system message
    the operator explicitly wants, so it always sends whenever credentials are
    configured. Orchestrator-level dedupe prevents spam.
    """
    try:
        from alert_system import AlertSystem
        a = AlertSystem()
        delivered = False
        if getattr(a, "telegram_configured", False):
            a._send_telegram(subject, body)
            delivered = True
        if getattr(a, "email_configured", False):
            body_html = f"<p>{body.replace(chr(10), '<br>')}</p>"
            a._send_email(subject, body, body_html, recipients=a._primary_recipients())
            delivered = True
        if not delivered:
            logger.warning(
                "[stage1b_promo] %s — no alert channel configured (set SMTP_* or TELEGRAM_* env): %s",
                subject, body.splitlines()[0] if body else "")
    except Exception as e:
        logger.warning("[stage1b_promo] alert send failed: %s", e)


# -----------------------------------------------------------------------------
# Arm / disarm
# -----------------------------------------------------------------------------

def arm_auto_promote(on: bool = True) -> Dict[str, Any]:
    """Explicitly arm (or disarm) automatic paper→live promotion."""
    state = save_promo_state({"auto_promote_armed": bool(on)})
    _log_transition("armed" if on else "disarmed", {"auto_promote_armed": bool(on)})
    logger.warning("[stage1b_promo] auto_promote_armed=%s", bool(on))
    return state


# -----------------------------------------------------------------------------
# Promotion / kill-switch actions
# -----------------------------------------------------------------------------

def _is_currently_paper() -> bool:
    try:
        from test_mode import get_paper_trading
        return bool(get_paper_trading())
    except Exception:
        return False


def _is_test_mode() -> bool:
    try:
        from test_mode import get_test_mode
        return bool(get_test_mode())
    except Exception:
        return True  # fail-closed: unknown ⇒ treat as test, do not promote


def _perform_promotion(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Flip paper→live. Fail-closed guards; idempotent; logs + alerts. Returns result dict."""
    if _is_test_mode():
        msg = "armed GO but TEST MODE is on — not promoting (turn test mode off first)"
        logger.warning("[stage1b_promo] %s", msg)
        _send_alert("Stage 1b Promotion: BLOCKED (test mode)", msg)
        return {"promoted": False, "reason": "test_mode_on"}
    if not _is_currently_paper():
        return {"promoted": False, "reason": "already_live"}

    try:
        from test_mode import set_paper_trading
        ok = set_paper_trading(False)
    except Exception as e:
        logger.error("[stage1b_promo] set_paper_trading failed: %s", e)
        _send_alert("Stage 1b Promotion: FAILED", f"Could not disable paper trading: {e}")
        return {"promoted": False, "reason": f"flip_failed: {e}"}
    if not ok:
        return {"promoted": False, "reason": "flip_not_saved"}

    save_promo_state({
        "promoted_at": _now_iso(),
        "promoted_metrics": decision.get("metrics"),
        "reverted_at": None,
        "pending_since": None,
    })
    _log_transition("auto_promoted", {"metrics": decision.get("metrics")})
    body = (
        "Stage 1b auto-promotion criteria satisfied and auto-promote was ARMED.\n"
        "Paper trading DISABLED — the system will now place LIVE orders.\n\n"
        f"Evidence: {decision['metrics']}\n\n"
        "Kill-switch is active: live trading auto-reverts to paper if results degrade. "
        "To revert manually: python stage1b_promotion.py --revert"
    )
    _send_alert("Stage 1b Promotion: LIVE ✅ (auto-promoted)", body)
    logger.warning("[stage1b_promo] AUTO-PROMOTED to live trading")
    return {"promoted": True}


def revert_to_paper(reason: str = "manual") -> Dict[str, Any]:
    """Demote live→paper (kill-switch / manual). Idempotent."""
    try:
        from test_mode import set_paper_trading, get_paper_trading
        if get_paper_trading():
            return {"reverted": False, "reason": "already_paper"}
        set_paper_trading(True)
    except Exception as e:
        logger.error("[stage1b_promo] revert failed: %s", e)
        return {"reverted": False, "reason": f"revert_failed: {e}"}
    save_promo_state({"reverted_at": _now_iso(), "promoted_at": None})
    _log_transition("reverted_to_paper", {"reason": reason})
    _send_alert(
        "Stage 1b Promotion: REVERTED to paper",
        f"Live trading was demoted back to paper.\nReason: {reason}",
    )
    logger.warning("[stage1b_promo] REVERTED to paper (%s)", reason)
    return {"reverted": True, "reason": reason}


def _check_kill_switch(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """If we promoted and live results degrade, revert to paper. Returns result or None."""
    promoted_at = state.get("promoted_at")
    if not promoted_at or _is_currently_paper():
        return None
    live = _load_live_closed()
    cutoff = str(promoted_at)[:19]
    since = [t for t in live
             if t.get("outcome") in ("win", "loss") and _trade_ts(t, "exit_time") >= cutoff]
    min_closed = int(_t("STAGE1B_KILL_LIVE_MIN_CLOSED", 10))
    if len(since) < min_closed:
        return None
    since.sort(key=lambda t: _trade_ts(t, "exit_time"))
    exp = _expectancy(since)
    dd = _max_drawdown_pct(since)
    kill_dd = _t("STAGE1B_KILL_DD_PCT", 8.0)
    kill_exp = _t("STAGE1B_KILL_EXPECTANCY", -0.50)
    if dd >= kill_dd or exp <= kill_exp:
        reason = (f"live degradation: expectancy={exp:.3f}% (kill≤{kill_exp}%), "
                  f"maxDD={dd:.2f}% (kill≥{kill_dd}%), n={len(since)}")
        return revert_to_paper(reason=reason)
    return None


# -----------------------------------------------------------------------------
# Orchestrator (daily entry point)
# -----------------------------------------------------------------------------

def run_promotion_check(*, send_alert: bool = True, allow_auto: bool = True) -> Dict[str, Any]:
    """Evaluate, alert (deduped), and — if armed — auto-promote or run kill-switch."""
    state = load_promo_state()

    # 1) Post-promotion safety first: demote if live is degrading.
    kill = _check_kill_switch(state)
    if kill and kill.get("reverted"):
        state = load_promo_state()

    decision = evaluate_promotion()
    armed = bool(state.get("auto_promote_armed"))
    result: Dict[str, Any] = {"decision": decision, "armed": armed, "actions": []}
    if kill:
        result["actions"].append({"kill_switch": kill})

    # 2) Auto-promote when GO + armed (re-checks guards internally).
    promoted = False
    if decision["decision"] == "GO" and armed and allow_auto:
        promo = _perform_promotion(decision)
        result["actions"].append({"promotion": promo})
        promoted = bool(promo.get("promoted"))

    # 3) Alert (deduped: on decision change, or once/day while GO).
    changed = decision["decision"] != state.get("last_decision")
    go_not_alerted_today = decision["decision"] == "GO" and state.get("last_alert_date") != _today()
    if send_alert and (changed or go_not_alerted_today) and not promoted:
        subject, body = format_decision_text(decision, armed=armed)
        _send_alert(subject, body)
        result["actions"].append({"alert": subject})

    # 4) Persist verdict + pending marker.
    updates: Dict[str, Any] = {"last_decision": decision["decision"], "last_alert_date": _today()}
    if decision["decision"] == "GO" and not armed and not state.get("pending_since"):
        updates["pending_since"] = _now_iso()
    if decision["decision"] != "GO":
        updates["pending_since"] = None
    save_promo_state(updates)
    return result


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Stage 1b paper→live promotion monitor")
    ap.add_argument("--arm", action="store_true", help="arm automatic promotion")
    ap.add_argument("--disarm", action="store_true", help="disarm automatic promotion")
    ap.add_argument("--revert", action="store_true", help="revert live→paper now")
    ap.add_argument("--run", action="store_true", help="run the full check (alerts + auto actions)")
    ap.add_argument("--no-alert", action="store_true", help="evaluate only; no alerts/actions")
    args = ap.parse_args()

    if args.arm:
        arm_auto_promote(True)
        print("Auto-promotion ARMED. A GO verdict will flip paper→live automatically.")
        return
    if args.disarm:
        arm_auto_promote(False)
        print("Auto-promotion DISARMED.")
        return
    if args.revert:
        print(revert_to_paper(reason="manual_cli"))
        return

    if args.run:
        res = run_promotion_check(send_alert=not args.no_alert, allow_auto=not args.no_alert)
        d = res["decision"]
    else:
        d = evaluate_promotion()
        res = {"decision": d, "armed": bool(load_promo_state().get("auto_promote_armed"))}

    subject, body = format_decision_text(d, armed=res.get("armed", False))
    print(subject)
    print(body)


if __name__ == "__main__":
    main()
