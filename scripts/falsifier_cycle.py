# =============================================================================
# falsifier_cycle.py — the weekly self-learning cycle (STRATEGY.md Phase 3, v1.6e).
#
# This is the replacement for the retired autonomous optimizer, built to the
# opposite philosophy: a DETERMINISTIC script that grades standing decisions
# against accumulating evidence and writes a dated decision memo. It never
# changes config, never tunes thresholds, never trades. Humans (or a future
# registered trial) act on what it reports.
#
# Each cycle answers, with numbers and sample-size honesty:
#   1. Are the guards still in place? (auto-exec, optimizer freeze, app alive)
#   2. What has each paper cohort done? (n, net P&L — cost-honest since v1.6c)
#   3. Was the risk-on gate right so far? (gate_watch_report)
#   4. What's the trials-registry state? (verdicts, Q3 slots remaining)
#   5. Is any registered decision now contradicted by evidence? (flags only)
#
# Output: data/decision_memos/YYYY-MM-DD.md  (+ JSON alongside)
# Run:    python scripts/falsifier_cycle.py
# Schedule: SwingTrader-WeeklyMemo task (see register_memo_task.ps1), Sunday 08:00.
# =============================================================================

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tls_setup  # noqa: F401

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMO_DIR = os.path.join(PROJECT, "data", "decision_memos")
TRIALS = os.path.join(PROJECT, "data", "trials.jsonl")
WATCH_START = "2026-06-11"
TRIAL_BUDGET_PER_QUARTER = 4  # STRATEGY.md G5: ≤4 candidate registrations per quarter


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "?"


def guards() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        with open(os.path.join(PROJECT, "execution_config.json"), encoding="utf-8") as f:
            out["auto_execution_enabled"] = json.load(f).get("auto_execution_enabled")
    except Exception as e:
        out["auto_execution_enabled"] = f"unreadable: {e}"
    try:
        with open(os.path.join(PROJECT, "scheduler_config.json"), encoding="utf-8") as f:
            sc = json.load(f)
        out["weekly_tasks_enabled"] = sc.get("weekly_tasks_enabled")
        out["last_refresh"] = sc.get("last_refresh")
        try:
            age_h = (datetime.now() - datetime.fromisoformat(sc["last_refresh"])).total_seconds() / 3600
            out["refresh_age_h"] = round(age_h, 1)
        except Exception:
            out["refresh_age_h"] = None
    except Exception as e:
        out["weekly_tasks_enabled"] = f"unreadable: {e}"
    try:
        with open(os.path.join(PROJECT, "live_thresholds.json"), encoding="utf-8") as f:
            out["optimizer_freeze"] = bool(json.load(f).get("freeze_thresholds"))
    except Exception:
        out["optimizer_freeze"] = None
    out["flags"] = []
    if out.get("auto_execution_enabled") is not False:
        out["flags"].append("AUTO-EXEC IS NOT OFF — investigate immediately")
    if out.get("weekly_tasks_enabled") is not False:
        out["flags"].append("optimizer weekly tasks re-enabled — was this deliberate + registered?")
    if out.get("optimizer_freeze") is not True:
        out["flags"].append("threshold freeze not active — was this deliberate + registered?")
    if (out.get("refresh_age_h") or 0) > 3:
        out["flags"].append(f"refresh stale ({out.get('refresh_age_h')}h) — app may be down")
    return out


def cohorts() -> Dict[str, Any]:
    with open(os.path.join(PROJECT, "positions.json"), encoding="utf-8") as f:
        pos = json.load(f)
    def _cohort(rows: List[dict]) -> Dict[str, Any]:
        closed = [p for p in rows if p.get("status") == "CLOSED"]
        pnl = [float(p.get("pnl_pct") or 0) for p in closed]
        n = len(pnl)
        mde = None
        if n >= 2:
            mean = sum(pnl) / n
            sd = (sum((x - mean) ** 2 for x in pnl) / (n - 1)) ** 0.5
            mde = round(2.5 * sd / (n ** 0.5), 2)
        return {"open": sum(1 for p in rows if p.get("status") == "OPEN"),
                "closed": n,
                "net_expectancy_pct": round(sum(pnl) / n, 2) if n else None,
                "mde_pct": mde}
    since = [p for p in pos if (p.get("entry_time") or "") >= WATCH_START]
    rot_rows = [p for p in since if (p.get("source") or "") == "paper_rotation"]
    rotation = _cohort(rot_rows)
    rotation["vs_hodl_btc"] = _rotation_vs_hodl(rot_rows)
    return {
        "rotation": rotation,
        "lean_paper": _cohort([p for p in since if (p.get("source") or "") == "paper_trade"]),
        "note": "cost-honest fills since v1.6c; cohorts empty while risk-off is EXPECTED; "
                "rotation's benchmark is HOLDING BTC over the same window, not cash (v2.0)",
    }


def _btc_price_now() -> Optional[float]:
    """Latest BTC price from the hourly market cache (no network)."""
    try:
        with open(os.path.join(PROJECT, "data", "coins_cache.json"), encoding="utf-8") as f:
            cc = json.load(f)
        coins = cc if isinstance(cc, list) else (cc.get("coins") or cc.get("data") or [])
        if len(coins) < 20:  # same floor as the fetcher's own cold-start loader: a
            return None      # 1-coin file is test residue, not a market snapshot
        btc = next((c for c in coins if c.get("id") == "bitcoin"), None)
        return float(btc["current_price"]) if btc else None
    except Exception:
        return None


def _rotation_vs_hodl(rows: List[dict]) -> Dict[str, Any]:
    """Per rotation position: strategy net % vs simply holding BTC over the identical window.

    STRATEGY.md v2.0: rotation is a drawdown-reduction overlay on BTC, so 'did it make
    money' is the wrong question — 'did it keep up with BTC net of its own costs' is the
    one the funding decision needs. Expected to LAG in an uninterrupted bull leg (it pays
    the round trip and sits out whipsaws); expected to WIN across a full cycle.
    """
    from fill_cost_model import TAKER_FEE_PCT_PER_SIDE
    out = []
    px_now = _btc_price_now()
    for p in rows:
        try:
            entry = float(p["entry_price"]); qty = float(p.get("quantity") or 0)
            if p.get("status") == "CLOSED" and p.get("exit_price"):
                hodl = (float(p["exit_price"]) / entry - 1) * 100
                strat = float(p.get("pnl_pct") or 0)
            elif px_now:
                hodl = (px_now / entry - 1) * 100
                fees = float(p.get("entry_fee_usd") or 0) + qty * px_now * TAKER_FEE_PCT_PER_SIDE / 100
                strat = ((px_now - entry) * qty - fees) / (entry * qty) * 100 if qty else None
            else:
                continue
            out.append({"entry": p.get("entry_time"), "status": p.get("status"),
                        "strategy_net_pct": round(strat, 2) if strat is not None else None,
                        "hodl_btc_pct": round(hodl, 2),
                        "gap_pp": round(strat - hodl, 2) if strat is not None else None})
        except Exception:
            continue
    return {"positions": out, "note": "gap_pp = strategy - HODL; negative in bull legs is the expected cost of the overlay"}


def gate_watch() -> Dict[str, Any]:
    from scripts.gate_watch_report import _load_btc, regime_scoreboard, suppressed_signals
    btc = _load_btc()
    return {"scoreboard": regime_scoreboard(btc, WATCH_START),
            "suppressed": suppressed_signals(WATCH_START)}


def trials_state(now: Optional[datetime] = None) -> Dict[str, Any]:
    regs, results = [], []
    try:
        with open(TRIALS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                kind = r.get("kind", "result" if "verdict" in r else "?")
                if kind and kind.startswith("registered"):
                    regs.append(r)
                elif r.get("verdict"):
                    results.append(r)
    except FileNotFoundError:
        pass
    # Slots are charged to the quarter a trial is registered FOR. Before v2.0 this was
    # "ts >= 2026-07-01" with no upper bound, so it never reset and would have charged the
    # Q4 trials (registered 2026-09-26, run from 10-01) to Q3. Audits ('registered_audit')
    # don't consume slots (v1.6d) but still raise N via their config hash.
    now_q = quarter_of((now or datetime.now(timezone.utc)).isoformat())
    by_q: Dict[str, int] = {}
    for r in regs:
        if r.get("kind") == "registered":
            q = r.get("quarter") or quarter_of(r.get("ts") or "")
            by_q[q] = by_q.get(q, 0) + 1
    next_q = next_quarter(now_q)
    return {
        "registered_total": len(regs), "results_total": len(results),
        "last_verdicts": [{"id": r.get("trial_id") or r.get("config_hash"), "verdict": r["verdict"]}
                          for r in results[-5:]],
        "current_quarter": now_q,
        "slots_used": by_q.get(now_q, 0),
        "slots_remaining": max(0, TRIAL_BUDGET_PER_QUARTER - by_q.get(now_q, 0)),
        "next_quarter": next_q,
        "next_quarter_slots_remaining": max(0, TRIAL_BUDGET_PER_QUARTER - by_q.get(next_q, 0)),
    }


def quarter_of(ts: str) -> str:
    """'2026-09-26T..' -> '2026Q3'. Empty/unparseable -> '?'."""
    try:
        y, m = int(ts[:4]), int(ts[5:7])
        return f"{y}Q{(m - 1) // 3 + 1}"
    except (ValueError, IndexError):
        return "?"


def next_quarter(q: str) -> str:
    try:
        y, n = int(q[:4]), int(q[-1])
        return f"{y + 1}Q1" if n == 4 else f"{y}Q{n + 1}"
    except (ValueError, IndexError):
        return "?"


def main() -> None:
    now = datetime.now(timezone.utc)
    data = {
        "generated_at": now.isoformat(), "git_sha": _git_sha(),
        "guards": guards(), "cohorts": cohorts(),
        "gate_watch": gate_watch(), "trials": trials_state(),
    }
    flags = list(data["guards"]["flags"])
    sb = data["gate_watch"]["scoreboard"]
    lines = [
        f"# Decision memo — {now.strftime('%Y-%m-%d')} (falsifier cycle, git {data['git_sha']})",
        "",
        "Deterministic weekly grading of standing decisions. Observational only.",
        "",
        "## Guards",
        f"- auto_execution_enabled: {data['guards']['auto_execution_enabled']}",
        f"- optimizer weekly tasks: {data['guards']['weekly_tasks_enabled']} | freeze: {data['guards']['optimizer_freeze']}",
        f"- last refresh: {data['guards']['last_refresh']} ({data['guards'].get('refresh_age_h')}h ago)",
        "",
        "## Cohorts (since 2026-06-11, cost-honest)",
        f"- rotation: { {k: v for k, v in data['cohorts']['rotation'].items() if k != 'vs_hodl_btc'} }",
        *[f"  - vs HODL BTC ({p['status']}, entry {p['entry']}): strategy {p['strategy_net_pct']:+.2f}% "
          f"vs hold {p['hodl_btc_pct']:+.2f}% -> gap {p['gap_pp']:+.2f}pp"
          for p in data['cohorts']['rotation']['vs_hodl_btc']['positions'] if p.get('gap_pp') is not None],
        f"- lean_paper: {data['cohorts']['lean_paper']}",
        "",
        "## Gate watch",
        f"- window: {sb.get('window')} | risk-off days: {sb.get('risk_off_days')}/{sb.get('days_observed')}",
        f"- BTC over window: {sb.get('btc_return_over_window_pct')}% | verdict: {sb.get('gate_verdict_so_far')}",
        f"- suppressed signals logged: {data['gate_watch']['suppressed']['lean_pruned_signals_logged']}",
        "",
        "## Trials registry",
        f"- results: {data['trials']['results_total']} | last: {data['trials']['last_verdicts']}",
        f"- {data['trials']['current_quarter']} slots remaining: {data['trials']['slots_remaining']}"
        f" | {data['trials']['next_quarter']} slots remaining: {data['trials']['next_quarter_slots_remaining']}",
        "",
        "## Attention needed" if flags else "## Attention needed\n- none — all standing decisions consistent with evidence",
    ]
    lines += [f"- {f}" for f in flags]
    os.makedirs(MEMO_DIR, exist_ok=True)
    stem = os.path.join(MEMO_DIR, now.strftime("%Y-%m-%d"))
    memo_text = "\n".join(lines) + "\n"
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write(memo_text)
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(memo_text)

    # Deliver the memo to the owner's inbox (STRATEGY.md v1.7b) — the last human
    # gap: a memo nobody reads grades nothing. Uses the daily-report path (bypasses
    # routine-alert toggles, respects the master kill-switch). Best-effort: email
    # failure must never fail the cycle; the memo on disk is the source of truth.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(PROJECT, ".env"))
        from alert_system import AlertSystem
        alerter = AlertSystem()
        if alerter.email_configured:
            subject_flag = "ATTENTION NEEDED" if flags else "all clear"
            html = (f"<h3>Weekly decision memo — {now.strftime('%Y-%m-%d')} ({subject_flag})</h3>"
                    f"<pre style='font-family:monospace;font-size:13px'>{memo_text}</pre>")
            alerter.send_daily_status_report(html)
            print(f"[falsifier] memo emailed ({subject_flag})")
    except Exception as e:
        print(f"[falsifier] memo email failed (non-fatal): {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
