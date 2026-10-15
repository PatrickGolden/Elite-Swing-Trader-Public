"""
Standalone daily health check for the swing-trader system.

Runs all 7 staleness/error checks, writes data/health_report.json,
then emails the report via AlertSystem (SMTP + Telegram).

Designed to be called by Windows Task Scheduler — no Claude Code required.

Usage:
    python scripts/run_health_check.py
    python scripts/run_health_check.py --dry-run   # skip email
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

STATUS_PRIORITY = {"ok": 0, "warn": 1, "critical": 2}


def _hours_ago(ts_str: str, *, naive_local: bool = False) -> float:
    """Return how many hours ago a timestamp string was. Handles ISO-8601 and space-separated.

    naive_local: if True, interpret naive timestamps as local wall time (matches scheduler_config
    last_refresh from datetime.now().isoformat()). If False, naive timestamps are UTC — used for
    fields that are explicitly stored as UTC elsewhere.
    """
    now = datetime.now(timezone.utc)
    ts_str = ts_str.strip()[:19]
    local_tz = datetime.now().astimezone().tzinfo
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            ts = datetime.strptime(ts_str, fmt)
            if ts.tzinfo is None:
                if naive_local and local_tz is not None:
                    ts = ts.replace(tzinfo=local_tz).astimezone(timezone.utc)
                else:
                    ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            return round((now - ts).total_seconds() / 3600, 1)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts_str!r}")


def _most_recent_live_journal_event_hours_ago() -> float | None:
    """Hours since the latest live (non-test) entry or exit in positions.json.

    Returns None if there are no parseable live journal timestamps. Used to decide whether
    model_performance.json is expected to have been touched recently.
    """
    try:
        with open("positions.json") as f:
            positions = json.load(f)
    except Exception:
        return None
    best: float | None = None
    for p in positions:
        if p.get("is_test"):
            continue
        for key in ("entry_time", "exit_time"):
            ts = p.get(key)
            if not ts or not str(ts).strip():
                continue
            if key == "exit_time" and p.get("status") != "CLOSED":
                continue
            try:
                h = _hours_ago(str(ts).strip(), naive_local=True)
            except Exception:
                continue
            if best is None or h < best:
                best = h
    return best


# ── Checks ─────────────────────────────────────────────────────────────────

def check_position_freshness() -> dict:
    try:
        with open("positions.json") as f:
            positions = json.load(f)
        open_pos = [p for p in positions if p.get("status") == "OPEN" and not p.get("is_test")]
        if not open_pos:
            return {"status": "ok", "detail": "No open positions."}
        stale, fresh = [], []
        for p in open_pos:
            sym = p.get("coin_symbol", "?")
            lr = p.get("last_refreshed") or p.get("entry_time", "")
            try:
                # Journal timestamps are local wall time (strftime from datetime.now()).
                age_h = _hours_ago(lr, naive_local=True)
                if age_h > 4:
                    stale.append(f"{sym} ({age_h}h)")
                else:
                    fresh.append(sym)
            except Exception:
                stale.append(f"{sym} (unparseable timestamp)")
        if stale:
            return {
                "status": "critical",
                "detail": (
                    f"{len(open_pos)} open positions, {len(stale)} stale >4h: "
                    f"{', '.join(stale)}. Fresh: {', '.join(fresh) or 'none'}."
                ),
            }
        return {"status": "ok", "detail": f"{len(open_pos)} open positions all fresh: {', '.join(fresh)}."}
    except Exception as e:
        return {"status": "warn", "detail": f"Could not check positions: {e}"}


def check_signal_freshness() -> dict:
    try:
        with open("data/signal_history.json") as f:
            history = json.load(f)
        if not history:
            return {"status": "critical", "detail": "signal_history.json is empty."}
        last = history[-1]
        ts_str = last.get("ts", "")
        # signal_history.py uses datetime.now() local wall time (no TZ in file).
        age_h = _hours_ago(ts_str, naive_local=True)
        status = "critical" if age_h > 24 else "ok"
        return {
            "status": status,
            "detail": (
                f"Latest signal {age_h}h ago ({ts_str}). "
                f"buy={last.get('buy', 0)}, hold={last.get('hold', 0)}, "
                f"moderate={last.get('moderate', 0)}. {len(history)} total entries."
            ),
        }
    except Exception as e:
        return {"status": "warn", "detail": f"Could not read signal_history.json: {e}"}


def check_alert_health() -> dict:
    try:
        with open("alert_dedup_cache.json") as f:
            cache = json.load(f)
        count = len(cache)
        bloat = count > 1000
        latest_ts = max(cache.values()) if cache else ""
        age_h = _hours_ago(latest_ts[:19].replace("T", " ")) if latest_ts else 9999
        status = "critical" if bloat else "ok"
        return {
            "status": status,
            "detail": (
                f"{count} dedup entries (limit 1000). "
                f"Latest alert {age_h}h ago."
                + (" BLOAT — prune needed." if bloat else "")
            ),
        }
    except Exception as e:
        return {"status": "warn", "detail": f"Could not read alert_dedup_cache.json: {e}"}


def check_model_performance() -> dict:
    try:
        with open("model_performance.json") as f:
            mp = json.load(f)
        upd = mp.get("updated", "") or ""
        age_h = _hours_ago(upd[:19].replace("T", " ")) if upd else 9999.0
        outcomes = mp.get("outcomes", {})
        out_detail = len(outcomes) if isinstance(outcomes, dict) else outcomes

        recent_live_h = _most_recent_live_journal_event_hours_ago()
        # No live open/close in 48h: idle; file age does not indicate a telemetry fault.
        if recent_live_h is None or recent_live_h > 48:
            return {
                "status": "ok",
                "detail": (
                    f"Last updated {age_h}h ago ({upd[:19] or 'never'}). "
                    f"No live (non-test) journal entry/exit in last 48h — staleness expected when idle. "
                    f"outcomes={out_detail}."
                ),
            }

        status = "critical" if age_h > 48 else "ok"
        detail = (
            f"Last updated {age_h}h ago ({upd[:19]}). "
            f"Live journal activity within 48h (most recent entry/exit {recent_live_h:.1f}h ago). "
            f"outcomes={out_detail}."
        )
        if status == "critical":
            detail += (
                " Telemetry should refresh on live opens/closes — check model_performance_tracker "
                "and trade_journal wiring."
            )
        return {"status": status, "detail": detail}
    except Exception as e:
        return {"status": "warn", "detail": f"Could not read model_performance.json: {e}"}


def _standby_minutes_in_window(hours_back: float) -> Optional[float]:
    """Minutes this machine spent in Modern Standby over the last `hours_back`.

    This hardware only supports Standby (S0 Low Power Idle): when the display sleeps
    the whole system suspends and the network is cut, so the app cannot refresh. That
    is a SLEEPING MACHINE, not a stalled scheduler — alerting on it trains the owner
    to ignore the one alert that matters. Returns None if the log can't be read
    (then we fail LOUD and keep the original severity).
    """
    if os.name != "nt":
        return None
    try:
        import subprocess
        ps = (
            "$s=(Get-Date).AddHours(-{h});"
            "Get-WinEvent -FilterHashtable @{{LogName='System';"
            "ProviderName='Microsoft-Windows-Kernel-Power';Id=506,507;StartTime=$s}} "
            "-ErrorAction SilentlyContinue | Sort-Object TimeCreated | "
            "ForEach-Object {{ \"$($_.Id) $($_.TimeCreated.ToString('o'))\" }}"
        ).format(h=hours_back)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60).stdout
        entered = None
        total = 0.0
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            eid, tstr = parts
            try:
                t = datetime.fromisoformat(tstr.strip())
            except ValueError:
                continue
            if eid == "506":            # entering standby
                entered = t
            elif eid == "507" and entered:   # exiting standby
                total += (t - entered).total_seconds() / 60.0
                entered = None
        if entered:   # still asleep at window end (shouldn't happen while we run)
            total += (datetime.now(entered.tzinfo) - entered).total_seconds() / 60.0
        return round(total, 1)
    except Exception:
        return None


def check_scheduler_health() -> dict:
    try:
        with open("scheduler_config.json") as f:
            sc = json.load(f)
        lr = sc.get("last_refresh", "")
        # Scheduler persists naive local ISO times (scheduler_manager datetime.now().isoformat()).
        age_h = _hours_ago(lr[:19].replace("T", " "), naive_local=True)
        interval = sc.get("interval", "?")
        # 15-min interval: warn >1h, critical >2h
        status = "critical" if age_h > 2 else ("warn" if age_h > 1 else "ok")
        detail = (
            f"last_refresh {age_h}h ago ({lr[:19]}). interval={interval}. "
            f"weekly={sc.get('last_weekly_run', '?')}, monthly={sc.get('last_learning_run', '?')}."
        )
        # Standby-aware (2026-08-10): if the machine was asleep for most of the gap,
        # the scheduler didn't stall — the OS suspended it. Downgrade so the alert
        # keeps its meaning. Only ever downgrades; never hides a real stall.
        if status != "ok":
            asleep_min = _standby_minutes_in_window(age_h + 0.5)
            if asleep_min is not None and asleep_min >= (age_h * 60 * 0.5):
                detail += (f" NOTE: machine was in Modern Standby ~{asleep_min:.0f} min of the "
                           f"{age_h * 60:.0f} min gap — suspended, not stalled; downgraded.")
                status = "ok" if asleep_min >= (age_h * 60 * 0.8) else "warn"
            elif asleep_min is not None:
                detail += f" (standby only {asleep_min:.0f} min of gap — genuine stall.)"
        return {"status": status, "detail": detail}
    except Exception as e:
        return {"status": "warn", "detail": f"Could not read scheduler_config.json: {e}"}


def check_execution_errors() -> dict:
    _WINDOW_H = 24
    try:
        log_path = Path("data/execution_log.jsonl")
        lines = log_path.read_text(encoding="utf-8").splitlines()
        cutoff = datetime.now(timezone.utc).timestamp() - _WINDOW_H * 3600
        errors = []
        for line in lines[-500:]:
            try:
                entry = json.loads(line)
                ts_str = (entry.get("ts") or entry.get("timestamp") or "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.timestamp() < cutoff:
                    continue
                if entry.get("status") == "error":
                    err = str(entry.get("error", ""))
                    if "INSUFFICIENT_FUND" in err:
                        label = "INSUFFICIENT_FUND"
                    elif "STOP_PRICE_ABOVE" in err:
                        label = "STOP_PRICE_ABOVE"
                    else:
                        label = entry.get("action", "error")
                    date = ts_str[:10]
                    errors.append(f"{label}@{date}")
            except Exception:
                pass
        if not errors:
            return {"status": "ok", "detail": f"No errors in last {_WINDOW_H}h of execution log."}
        counts = Counter(errors)
        summary = ", ".join(f"{k} x{v}" for k, v in counts.most_common())
        return {"status": "warn", "detail": f"{len(errors)} errors in last {_WINDOW_H}h: {summary}."}
    except Exception as e:
        return {"status": "warn", "detail": f"Could not read execution_log.jsonl: {e}"}


def check_optimizer_state(
    *,
    optimizer_log_file: Path | None = None,
    live_thresholds_file: Path | None = None,
    scheduler_config_file: Path | None = None,
) -> dict:
    """Staleness from last optimizer cycle in data/optimizer_log.json.

    Do not use live_thresholds.json \"updated\" for pass/fail: that field only changes when
    numeric thresholds are written (auto-apply or manual). Weekly cycles that produce no
    apply still append to optimizer_log — mirroring app.py _optimizer_loop semantics.
    Legacy ai_recommendations_v2 (MCP-BTC training sink) is unrelated to quant gating and is
    not checked here.
    """
    # Weekly lean run + slack; align loosely with app.py catch-up (>7d) without 48h false alarms.
    _AGE_WARN_H = 8 * 24
    _AGE_CRITICAL_H = 14 * 24
    log_path = optimizer_log_file or (PROJECT_ROOT / "data" / "optimizer_log.json")
    lt_path = live_thresholds_file or (PROJECT_ROOT / "live_thresholds.json")

    # Optimizer staleness is only a problem if the optimizer is SUPPOSED to be running.
    # Per STRATEGY.md (v1.5) the weekly optimizer/roadmap cycle is deliberately frozen
    # (scheduler_config.weekly_tasks_enabled=False) until a new registered hypothesis
    # passes the gates — so a stale last-cycle is expected, not a failure. Without this,
    # the check emails CRITICAL daily forever. Re-arm naturally when weekly tasks resume.
    try:
        sc_path = scheduler_config_file or (PROJECT_ROOT / "scheduler_config.json")
        if sc_path.is_file():
            with open(sc_path, encoding="utf-8") as f:
                if json.load(f).get("weekly_tasks_enabled") is False:
                    return {
                        "status": "ok",
                        "detail": "Optimizer intentionally frozen (weekly_tasks_enabled=False, "
                                  "STRATEGY.md) — staleness expected, not checked.",
                    }
    except Exception:
        pass  # fall through to normal staleness check if config unreadable

    try:
        if not log_path.is_file():
            return {
                "status": "warn",
                "detail": "data/optimizer_log.json missing — cannot verify last optimizer cycle.",
            }
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
        if not isinstance(log, list) or not log:
            return {
                "status": "warn",
                "detail": "data/optimizer_log.json is empty.",
            }
        last_ts = ""
        for entry in reversed(log):
            ts = (entry or {}).get("timestamp") or ""
            if ts:
                last_ts = ts
                break
        if not last_ts:
            return {
                "status": "warn",
                "detail": "No timestamp on optimizer log entries.",
            }
        age_h = _hours_ago(last_ts[:19].replace("T", " "))
        age_d = round(age_h / 24, 1)
        if age_h > _AGE_CRITICAL_H:
            status = "critical"
        elif age_h > _AGE_WARN_H:
            status = "warn"
        else:
            status = "ok"
        detail = f"Last optimizer cycle {age_d}d ago ({last_ts[:19]})."
        # Supplementary: when live file was written (informational only).
        try:
            if not lt_path.is_file():
                raise FileNotFoundError
            with open(lt_path, encoding="utf-8") as f:
                lt = json.load(f)
            upd = (lt.get("updated") or "")[:19]
            if upd:
                lt_age_d = round(_hours_ago(upd.replace("T", " ")) / 24, 1)
                detail += (
                    f" live_thresholds.json last written {lt_age_d}d ago "
                    f"(only when thresholds change, not every cycle)."
                )
        except Exception:
            pass
        return {"status": status, "detail": detail}
    except Exception as e:
        return {"status": "warn", "detail": f"Could not check optimizer state: {e}"}


# ── Main ────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    checks = {
        "position_freshness":        check_position_freshness(),
        "signal_history_freshness":  check_signal_freshness(),
        "alert_system_health":       check_alert_health(),
        "model_performance_staleness": check_model_performance(),
        "scheduler_health":          check_scheduler_health(),
        "execution_log_errors":      check_execution_errors(),
        "optimizer_state":           check_optimizer_state(),
    }

    worst = max(checks.values(), key=lambda c: STATUS_PRIORITY.get(c["status"], 0))
    if worst["status"] == "critical":
        overall = "critical"
    elif worst["status"] == "warn":
        overall = "warning"
    else:
        overall = "healthy"

    critical_checks = [k for k, v in checks.items() if v["status"] == "critical"]
    warn_checks     = [k for k, v in checks.items() if v["status"] == "warn"]
    if critical_checks:
        summary = f"CRITICAL: {', '.join(critical_checks)}"
        if warn_checks:
            summary += f" | WARN: {', '.join(warn_checks)}"
    elif warn_checks:
        summary = f"WARNING: {', '.join(warn_checks)}"
    else:
        summary = "All checks healthy."

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "checks": checks,
        "summary": summary,
    }

    report_path = Path("data/health_report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[health-check] {overall.upper()} — {summary}")

    if not dry_run:
        try:
            from scripts.send_report_email import _build_health_html
            from alert_system import AlertSystem
            subject, html = _build_health_html(report)
            alerter = AlertSystem()
            if alerter.email_configured:
                alerter.send_daily_status_report(html)
                print(f"[health-check] Email sent: {subject}")
            else:
                print("[health-check] Email not configured — skipping send")
        except Exception as e:
            print(f"[health-check] Email failed (non-fatal): {e}", file=sys.stderr)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run swing-trader daily health check")
    parser.add_argument("--dry-run", action="store_true", help="Skip email, just write report")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
        sys.exit(0)  # Script ran successfully — status is in the report/email
    except Exception as e:
        print(f"[health-check] FATAL: {e}", file=sys.stderr)
        sys.exit(2)  # Only non-zero if script itself crashed
