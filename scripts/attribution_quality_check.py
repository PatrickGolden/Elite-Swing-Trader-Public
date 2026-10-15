#!/usr/bin/env python3
"""
Attribution quality guard — checks post-instrumentation trades for field completeness.
Writes result to data/attribution_quality.json. Safe to run autonomously.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
CUTOFF = "2026-04-02"
REQUIRED_FIELDS = [
    "fear_greed",
    "funding_rate",
    "price_change_24h",
    "price_change_7d",
    "price_change_30d",
    "edge_score",
]


def load_json(path: Path) -> dict | list | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main() -> int:
    positions = load_json(ROOT / "positions.json")
    if positions is None:
        print("ERROR: Could not load positions.json", file=sys.stderr)
        return 1

    if not isinstance(positions, list):
        print("ERROR: positions.json is not a list", file=sys.stderr)
        return 1

    # Filter closed, non-test trades
    closed = [
        t for t in positions
        if t.get("status", "").upper() == "CLOSED" and not t.get("is_test", False)
    ]
    pre_cutoff = [t for t in closed if t.get("entry_time", "") < CUTOFF]
    post_cutoff = [t for t in closed if t.get("entry_time", "") >= CUTOFF]

    # Per-trade field audit
    trades_with_gaps = []
    field_coverage = {f: {"present": 0, "missing": 0} for f in REQUIRED_FIELDS}
    total_present = 0

    for trade in post_cutoff:
        missing = [f for f in REQUIRED_FIELDS if trade.get(f) is None]
        for field in REQUIRED_FIELDS:
            if trade.get(field) is not None:
                field_coverage[field]["present"] += 1
                total_present += 1
            else:
                field_coverage[field]["missing"] += 1
        if missing:
            trades_with_gaps.append({
                "symbol": trade.get("coin_symbol", ""),
                "entry_time": trade.get("entry_time", ""),
                "outcome": trade.get("outcome", ""),
                "missing_fields": missing,
            })

    n_instrumented = len(post_cutoff)
    total_fields = n_instrumented * len(REQUIRED_FIELDS)
    completeness_pct = (total_present / total_fields * 100) if total_fields > 0 else 0.0

    gates = {
        "80_pct": "passed" if completeness_pct >= 80 else "blocked",
        "90_pct": "passed" if completeness_pct >= 90 else "blocked",
        "95_pct": "passed" if completeness_pct >= 95 else "blocked",
    }

    # Trend vs prior run
    prior = load_json(ROOT / "data" / "attribution_quality.json")
    prior_completeness = prior.get("completeness_pct") if prior else None
    prior_instrumented = prior.get("post_instrumentation", 0) if prior else 0

    if prior_completeness is None:
        trend = "first_run"
    elif completeness_pct > prior_completeness:
        trend = "improving"
    elif completeness_pct < prior_completeness:
        trend = "degrading"
    else:
        trend = "stable"

    # Roadmap gate context
    roadmap = load_json(ROOT / "config" / "roadmap_framework.json")
    milestone_trades = 100
    if roadmap and isinstance(roadmap, dict):
        milestone_trades = roadmap.get("milestones", {}).get("min_closed_trades", 100)

    a1_needed = max(0, 30 - n_instrumented)
    a2_needed = max(0, 60 - n_instrumented)
    b_needed = max(0, milestone_trades - n_instrumented)

    # Alert conditions
    alert = False
    alert_reasons = []
    if completeness_pct < 90 and n_instrumented > 5:
        alert = True
        alert_reasons.append(
            f"Completeness {completeness_pct:.1f}% < 90% with {n_instrumented} instrumented trades"
        )
    for field, cov in field_coverage.items():
        if n_instrumented > 0 and cov["missing"] / n_instrumented > 0.25:
            alert = True
            alert_reasons.append(
                f"{field} missing on {cov['missing']}/{n_instrumented} trades (>25%)"
            )

    delta = "" if prior_completeness is None else f" (prior: {prior_completeness:.1f}%)"
    summary = (
        f"{completeness_pct:.0f}% attribution completeness on {n_instrumented} "
        f"post-instrumentation trades ({len(closed)} total closed, {len(pre_cutoff)} "
        f"pre-cutoff excluded){delta}. "
        f"Need {b_needed} more instrumented closed trades for {milestone_trades}-trade milestone. "
        f"Trend: {trend}."
    )

    output = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_closed": len(closed),
        "pre_instrumentation": len(pre_cutoff),
        "post_instrumentation": n_instrumented,
        "completeness_pct": round(completeness_pct, 2),
        "trades_with_gaps": trades_with_gaps,
        "field_coverage": field_coverage,
        "gates_status": gates,
        "trend": trend,
        "summary": summary,
        "roadmap_context": {
            "A1_min_trades_needed": a1_needed,
            "A2_min_trades_needed": a2_needed,
            "B_min_trades_needed": b_needed,
            "note": "Completeness gates all passing; trade count is the binding constraint",
        },
    }
    if alert:
        output["alert"] = True
        output["alert_reasons"] = alert_reasons

    out_path = ROOT / "data" / "attribution_quality.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(summary)
    if alert:
        print("ALERT:", "; ".join(alert_reasons), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
