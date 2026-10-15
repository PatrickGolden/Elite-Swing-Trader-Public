# scripts/buy_signals_stress_report.py — 30- and 60-day buy signal stress analysis: blockers, outcomes, verdicts.
# Run from repo root: python scripts/buy_signals_stress_report.py
# Writes data/buy_signals_stress_30d.json, buy_signals_stress_60d.json, and optional .md for each.

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.config import (
    RECOMMENDATIONS_FILE,
    SCREENING_LOG_FILE,
    DATA_DIR,
    MISSED_OPPORTUNITY_THRESHOLD_PCT,
)


def _parse_ts(ts: str) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()[:19].replace("T", " ")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _load_json(path: Path):
    import safe_json
    data = safe_json.load(str(path), default=list)
    return data if isinstance(data, list) else []


def _classify_recommendation_blocker(rec: dict) -> str:
    """Classify why a recommendation did not execute. Uses consensus_signal (pre-veto) and new fields if present."""
    skipped = (rec.get("skipped_reason") or "").strip()
    if skipped == "insufficient_cash":
        return "insufficient_cash"
    final = (rec.get("final_signal") or rec.get("consensus_signal") or "").strip()
    veto = (rec.get("veto_reason") or "").strip()
    if veto and final not in ("ULTRA_BUY", "STRONG_BUY", "MODERATE_BUY"):
        return "veto"
    consensus = (rec.get("consensus_signal") or "").strip()
    if consensus in ("WEAK_BUY", "HOLD", "SELL", "CONFLICT", ""):
        return "policy_no_auto_exec"
    if consensus == "MODERATE_BUY":
        return "policy_no_auto_exec"  # MODERATE_BUY does not auto-exec
    if consensus in ("ULTRA_BUY", "STRONG_BUY"):
        return "executed_or_would_auto_exec"  # would have auto-exec if cash/safety ok
    return "unknown"


def _verdict_recommendation(rec: dict, blocker: str) -> str:
    o7 = rec.get("outcome_7d")
    if o7 is None:
        return "unknown_no_outcome_data"
    o7 = float(o7)
    if blocker in ("executed_or_would_auto_exec", "insufficient_cash"):
        if o7 >= MISSED_OPPORTUNITY_THRESHOLD_PCT:
            return "missed_opportunity" if blocker == "insufficient_cash" else "would_have_been_profitable"
        if o7 < 0:
            return "correct_skip" if blocker == "insufficient_cash" else "would_have_been_loss"
        return "neutral"
    if blocker == "veto":
        if o7 >= MISSED_OPPORTUNITY_THRESHOLD_PCT:
            return "possible_missed_opportunity"
        if o7 < 0:
            return "correct_veto"
        return "neutral"
    if blocker == "policy_no_auto_exec":
        if o7 >= MISSED_OPPORTUNITY_THRESHOLD_PCT:
            return "possible_missed_opportunity"
        return "neutral"
    return "unknown"


def _verdict_screening(entry: dict) -> str:
    o7 = entry.get("outcome_7d")
    if o7 is None:
        return "unknown_no_outcome_data"
    o7 = float(o7)
    if o7 >= MISSED_OPPORTUNITY_THRESHOLD_PCT:
        return "possible_missed_opportunity"
    if o7 < 0:
        return "correct_skip"
    return "neutral"


def _current_params(repo_root: Path) -> dict:
    """Current thresholds and account config for report (as of report run). Parameters at signal time are not stored."""
    out = {"note": "Parameters at signal time are not stored; values below are as of report run.", "thresholds": {}, "account": {}}
    th_path = repo_root / "live_thresholds.json"
    if th_path.exists():
        try:
            with open(th_path, "r", encoding="utf-8") as f:
                out["thresholds"] = json.load(f)
        except Exception:
            pass
    try:
        from thresholds import get_threshold
        for key in ("FUNDING_VETO_BEAR_PCT", "VETO_CONFIDENCE_THRESHOLD", "DIP_MIN_SCORE"):
            try:
                out["thresholds"][key] = get_threshold(key)
            except Exception:
                pass
    except ImportError:
        pass
    try:
        out["thresholds"]["MISSED_OPPORTUNITY_THRESHOLD_PCT"] = MISSED_OPPORTUNITY_THRESHOLD_PCT
    except Exception:
        pass
    acct_path = repo_root / "account_config.json"
    if acct_path.exists():
        try:
            with open(acct_path, "r", encoding="utf-8") as f:
                out["account"] = json.load(f)
        except Exception:
            pass
    return out


def run(days: int = 30, write_md: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    report = {
        "report_timestamp": now.isoformat(),
        "window_days": days,
        "cutoff": cutoff.isoformat(),
        "recommendations": [],
        "screening_failed_with_buy": [],
        "current_params": _current_params(REPO_ROOT),
        "summary": {"recommendations_total": 0, "by_blocker": {}, "screening_failed_with_buy_total": 0, "missed_opportunity_count": 0},
    }

    # --- Recommendations (last N days) ---
    recs = _load_json(Path(RECOMMENDATIONS_FILE))
    for r in recs:
        if not isinstance(r, dict):
            continue
        ts = r.get("timestamp")
        t = _parse_ts(ts)
        if t is None:
            continue
        t_utc = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
        if t_utc < cutoff:
            continue
        blocker = _classify_recommendation_blocker(r)
        verdict = _verdict_recommendation(r, blocker)
        report["summary"]["by_blocker"][blocker] = report["summary"]["by_blocker"].get(blocker, 0) + 1
        if verdict in ("missed_opportunity", "possible_missed_opportunity"):
            report["summary"]["missed_opportunity_count"] = report["summary"]["missed_opportunity_count"] + 1
        report["recommendations"].append({
            "id": r.get("id"),
            "timestamp": ts,
            "coin_id": r.get("coin_id"),
            "coin_symbol": r.get("coin_symbol"),
            "entry_price": r.get("entry_price"),
            "consensus_signal": r.get("consensus_signal"),
            "final_signal": r.get("final_signal"),
            "veto_reason": r.get("veto_reason"),
            "skipped_reason": r.get("skipped_reason"),
            "market_regime": r.get("market_regime"),
            "blocker": blocker,
            "outcome_3d": r.get("outcome_3d"),
            "outcome_7d": r.get("outcome_7d"),
            "outcome_14d": r.get("outcome_14d"),
            "verdict": verdict,
        })
    report["summary"]["recommendations_total"] = len(report["recommendations"])

    # --- Screening log: screening_failed with at least one BUY (last N days) ---
    screening_path = Path(SCREENING_LOG_FILE)
    screening = _load_json(screening_path)
    for s in screening:
        if not isinstance(s, dict):
            continue
        if (s.get("note") or "").strip() != "screening_failed":
            continue
        ts = s.get("timestamp")
        t = _parse_ts(ts)
        if t is None:
            continue
        t_utc = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
        if t_utc < cutoff:
            continue
        verdict = _verdict_screening(s)
        if verdict in ("possible_missed_opportunity",):
            report["summary"]["missed_opportunity_count"] = report["summary"]["missed_opportunity_count"] + 1
        report["screening_failed_with_buy"].append({
            "timestamp": ts,
            "coin_id": s.get("coin_id"),
            "symbol": s.get("symbol"),
            "entry_price": s.get("entry_price"),
            "quant_tier": s.get("quant_tier"),
            "market_regime": s.get("market_regime"),
            "blocker": "screening_failed",
            "outcome_7d": s.get("outcome_7d"),
            "verdict": verdict,
        })
    report["summary"]["screening_failed_with_buy_total"] = len(report["screening_failed_with_buy"])

    # --- Write JSON ---
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    import safe_json
    json_path = DATA_DIR / f"buy_signals_stress_{days}d.json"
    safe_json.save(str(json_path), report, default=str)
    print(f"  Wrote {json_path}")

    # --- Optional Markdown ---
    if write_md:
        md_path = DATA_DIR / f"buy_signals_stress_{days}d.md"
        lines = [
            f"# {days}-day buy signal stress report",
            f"Generated: {report['report_timestamp']}",
            f"Window: last {report['window_days']} days.",
            "",
            "## Parameters (as of report run)",
            "Parameters at signal time are not stored; values below are for reference only.",
            "```json",
            json.dumps(report["current_params"], indent=2, default=str),
            "```",
            "",
            "## Summary",
            f"- Recommendations (last {report['window_days']}d): {report['summary']['recommendations_total']}",
            f"- By blocker: {report['summary']['by_blocker']}",
            f"- Screening failed with at least one BUY: {report['summary']['screening_failed_with_buy_total']}",
            f"- Missed opportunity count (recs + screening): {report['summary']['missed_opportunity_count']}",
            "",
            "## Recommendations",
            ""
        ]
        for r in report["recommendations"]:
            lines.append(f"- **{r.get('coin_symbol', '')}** {r.get('timestamp', '')} | consensus={r.get('consensus_signal')} final={r.get('final_signal')} | blocker={r.get('blocker')} | outcome_7d={r.get('outcome_7d')} | verdict={r.get('verdict')}")
        lines.extend(["", "## Screening failed", ""])
        for s in report["screening_failed_with_buy"][:100]:  # cap for readability
            lines.append(f"- **{s.get('symbol', '')}** {s.get('timestamp', '')} | quant_tier={s.get('quant_tier')} | outcome_7d={s.get('outcome_7d')} | verdict={s.get('verdict')}")
        if len(report["screening_failed_with_buy"]) > 100:
            lines.append(f"... and {len(report['screening_failed_with_buy']) - 100} more (see JSON).")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"  Wrote {md_path}")

    return report


def run_both(write_md: bool = True) -> dict:
    """Run 30-day and 60-day stress reports. Returns combined summary for notifications."""
    r30 = run(days=30, write_md=write_md)
    r60 = run(days=60, write_md=write_md)
    return {
        "report_timestamp": r30.get("report_timestamp"),
        "30d": r30["summary"],
        "60d": r60["summary"],
    }


def main():
    combined = run_both(write_md=True)
    s30 = combined["30d"]
    s60 = combined["60d"]
    print(f"  30d: recs={s30['recommendations_total']}, screening_failed={s30['screening_failed_with_buy_total']}, missed={s30['missed_opportunity_count']}")
    print(f"  60d: recs={s60['recommendations_total']}, screening_failed={s60['screening_failed_with_buy_total']}, missed={s60['missed_opportunity_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
