# scripts/adversarial_backtest.py — Full adversarial backtester.
# Probes the trading system for structural vulnerabilities by:
#   Phase 1: Stratified failure mode identification (regime × path × tier)
#   Phase 2: Threshold sensitivity sweeps across OPTIMIZER_BOUNDS
#   Phase 3: Structural vulnerability report with max drawdown, stop-loss analysis
#   Phase 4: Ranked recommendations with backtested impact
#
# Usage:
#   python scripts/adversarial_backtest.py                      # Full run (baseline + sweeps)
#   python scripts/adversarial_backtest.py --baseline-only       # Quick: just failure analysis on saved trades
#   python scripts/adversarial_backtest.py --sweep-steps 3       # Fewer sweep points (faster)

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from safe_json import save as safe_json_save

logger = logging.getLogger(__name__)

DATA_DIR = REPO_ROOT / "data"
BACKTEST_TRADES_FILE = DATA_DIR / "backtest_trades.json"
ADVERSARIAL_REPORT_FILE = DATA_DIR / "adversarial_report.json"

# Failure thresholds
FAILURE_WIN_RATE_PCT = 30.0     # Cells below this are "failure cells"
MIN_CELL_TRADES = 3             # Need at least N trades to flag a cell

# Sensitivity sweep config
DEFAULT_SWEEP_STEPS = 5         # Points per parameter (lo, 25%, mid, 75%, hi)


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _load_backtest_trades() -> Tuple[Dict, List[Dict]]:
    """Load backtest_trades.json, return (summary, trades_list)."""
    raw = _load_json(BACKTEST_TRADES_FILE)
    if not isinstance(raw, dict):
        return {}, []
    trades = raw.get("trades", [])
    if not isinstance(trades, list):
        trades = []
    summary = raw.get("summary", {})
    return summary, [t for t in trades if isinstance(t, dict)]


def _load_live_thresholds() -> Dict[str, Any]:
    lt_file = REPO_ROOT / "live_thresholds.json"
    return _load_json(lt_file) if lt_file.is_file() else {}


# ---------------------------------------------------------------------------
# Phase 1: Stratified Failure Mode Identification
# ---------------------------------------------------------------------------
def _compute_cell_stats(trades: List[Dict]) -> Dict:
    """Compute win/loss/pnl stats for a group of trades."""
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
                "expectancy_pct": 0.0, "total_pnl_pct": 0.0, "avg_pnl_pct": 0.0}
    wins = sum(1 for t in trades if t.get("outcome") == "win")
    losses = len(trades) - wins
    total_pnl = sum(float(t.get("pnl_pct", 0)) for t in trades)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / len(trades) * 100, 1),
        "expectancy_pct": round(total_pnl / len(trades), 3),
        "total_pnl_pct": round(total_pnl, 3),
        "avg_pnl_pct": round(total_pnl / len(trades), 3),
    }


def identify_failure_cells(trades: List[Dict]) -> List[Dict]:
    """Stratify trades by regime × path, flag cells with <30% WR or negative expectancy."""
    cells: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for t in trades:
        regime = (t.get("regime") or "UNKNOWN").strip().upper()
        path = t.get("quant_path") or "default"
        cells[(regime, path)].append(t)

    failure_cells = []
    all_cells = []
    for (regime, path), cell_trades in sorted(cells.items()):
        stats = _compute_cell_stats(cell_trades)
        cell = {"regime": regime, "path": path, **stats}
        all_cells.append(cell)
        if stats["trades"] >= MIN_CELL_TRADES and (
            stats["win_rate_pct"] < FAILURE_WIN_RATE_PCT or stats["expectancy_pct"] < 0
        ):
            # Determine recommendation
            if stats["win_rate_pct"] == 0:
                rec = "disable_path_in_regime"
            elif stats["win_rate_pct"] < 15:
                rec = "disable_or_raise_threshold"
            else:
                rec = "raise_threshold"
            cell["recommendation"] = rec
            cell["pnl_saved_if_disabled"] = round(abs(stats["total_pnl_pct"]), 3)
            failure_cells.append(cell)

    return failure_cells, all_cells


def analyze_exit_reasons(trades: List[Dict]) -> Dict:
    """Analyze stop-loss vs take-profit vs time-exit distribution and P&L by exit reason."""
    by_reason: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        by_reason[reason].append(t)

    result = {}
    for reason, reason_trades in sorted(by_reason.items()):
        stats = _compute_cell_stats(reason_trades)
        # Stop-loss analysis: how quickly do they hit?
        if reason == "stop_loss":
            hold_hours = [t.get("hold_hours", 0) for t in reason_trades]
            stats["avg_hold_hours"] = round(sum(hold_hours) / len(hold_hours), 1) if hold_hours else 0
            # Quick stops (< 24h) suggest stops are too tight
            quick_stops = sum(1 for h in hold_hours if h < 24)
            stats["quick_stops_pct"] = round(quick_stops / len(hold_hours) * 100, 1) if hold_hours else 0
        result[reason] = stats
    return result


def compute_max_drawdown(trades: List[Dict]) -> Dict:
    """Compute sequential max drawdown from trade P&L stream."""
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_start = 0
    max_dd_end = 0
    current_dd_start = 0

    for i, t in enumerate(trades):
        cumulative += float(t.get("pnl_pct", 0))
        if cumulative > peak:
            peak = cumulative
            current_dd_start = i
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
            max_dd_start = current_dd_start
            max_dd_end = i

    return {
        "max_drawdown_pct": round(max_dd, 2),
        "peak_pnl_pct": round(peak, 2),
        "trough_pnl_pct": round(peak - max_dd, 2),
        "dd_start_trade_idx": max_dd_start,
        "dd_end_trade_idx": max_dd_end,
    }


# ---------------------------------------------------------------------------
# Phase 2: Threshold Sensitivity Sweep
# ---------------------------------------------------------------------------
def run_sensitivity_sweep(
    sweep_steps: int = DEFAULT_SWEEP_STEPS,
    days: int = 90,
) -> List[Dict]:
    """For each parameter in OPTIMIZER_BOUNDS, run replay at multiple values and track impact."""
    from thresholds import OPTIMIZER_BOUNDS, PARAM_DESCRIPTIONS

    # Import replay — heavy import, only when needed
    from historical_replay import run_historical_replay

    results = []
    total_params = len(OPTIMIZER_BOUNDS)
    logger.info(f"[adversarial] Starting sensitivity sweep: {total_params} params × {sweep_steps} steps")

    for idx, (param, (lo, hi)) in enumerate(OPTIMIZER_BOUNDS.items(), 1):
        param_results = []
        desc = PARAM_DESCRIPTIONS.get(param, param)

        # Generate sweep values
        if isinstance(lo, float) or isinstance(hi, float):
            step_size = (hi - lo) / (sweep_steps - 1) if sweep_steps > 1 else 0
            values = [round(lo + i * step_size, 4) for i in range(sweep_steps)]
        else:
            step_size = (hi - lo) / (sweep_steps - 1) if sweep_steps > 1 else 0
            values = [int(round(lo + i * step_size)) for i in range(sweep_steps)]
            values = sorted(set(values))  # Deduplicate for small integer ranges

        logger.info(f"[adversarial] [{idx}/{total_params}] Sweeping {param}: {values}")

        for val in values:
            try:
                result = run_historical_replay(
                    days=days,
                    threshold_overrides={param: val},
                    verbose=False,
                )
                summary = result.get("summary", {})
                param_results.append({
                    "value": val,
                    "win_rate_pct": summary.get("win_rate_pct", 0),
                    "expectancy_pct": summary.get("expectancy_pct", 0),
                    "profit_factor": summary.get("profit_factor", 0),
                    "total_trades": summary.get("total_trades", 0),
                    "max_drawdown_pct": summary.get("max_drawdown_pct", 0),
                    "total_pnl_pct": summary.get("total_pnl_pct", 0),
                })
            except Exception as e:
                logger.warning(f"[adversarial] Sweep failed for {param}={val}: {e}")
                param_results.append({"value": val, "error": str(e)})

        if not param_results:
            continue

        # Find best and worst points
        valid = [r for r in param_results if "error" not in r]
        if not valid:
            continue

        best = max(valid, key=lambda r: r["expectancy_pct"])
        worst = min(valid, key=lambda r: r["expectancy_pct"])

        # Sensitivity = range of expectancy across sweep
        exp_range = best["expectancy_pct"] - worst["expectancy_pct"]

        # Cliff detection: find largest single-step drop
        max_drop = 0.0
        cliff_at = None
        for i in range(1, len(valid)):
            drop = valid[i - 1]["expectancy_pct"] - valid[i]["expectancy_pct"]
            if drop > max_drop:
                max_drop = drop
                cliff_at = valid[i]["value"]

        results.append({
            "parameter": param,
            "description": desc,
            "current": _load_live_thresholds().get(param),
            "bounds": [lo, hi],
            "sweep_results": param_results,
            "best_value": best["value"],
            "best_expectancy": best["expectancy_pct"],
            "worst_value": worst["value"],
            "worst_expectancy": worst["expectancy_pct"],
            "sensitivity": round(exp_range, 3),
            "cliff_at": cliff_at,
            "cliff_drop": round(max_drop, 3) if max_drop > 0 else None,
        })

    # Sort by sensitivity (highest impact first)
    results.sort(key=lambda r: r["sensitivity"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Phase 3: Structural Vulnerability Report
# ---------------------------------------------------------------------------
def build_structural_issues(
    failure_cells: List[Dict],
    exit_analysis: Dict,
    drawdown: Dict,
    all_cells: List[Dict],
    sensitivity: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Build prioritized list of structural issues with severity and recommendations."""
    issues = []

    # Regime-level failures (aggregate paths per regime)
    regime_agg: Dict[str, Dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
    for cell in all_cells:
        r = cell["regime"]
        regime_agg[r]["trades"] += cell["trades"]
        regime_agg[r]["wins"] += cell["wins"]
        regime_agg[r]["total_pnl"] += cell["total_pnl_pct"]

    for regime, agg in regime_agg.items():
        if agg["trades"] < MIN_CELL_TRADES:
            continue
        wr = round(agg["wins"] / agg["trades"] * 100, 1) if agg["trades"] else 0
        if wr < 20:
            issues.append({
                "issue": f"{regime} regime: {wr}% win rate ({agg['wins']}/{agg['trades']} trades)",
                "severity": "critical" if wr == 0 else "high",
                "recommendation": f"Add {regime}-specific veto or disable trading in {regime} regime",
                "trades_affected": agg["trades"],
                "pnl_saved": round(abs(agg["total_pnl"]), 2),
            })

    # Path-level failures
    path_agg: Dict[str, Dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
    for cell in all_cells:
        p = cell["path"]
        path_agg[p]["trades"] += cell["trades"]
        path_agg[p]["wins"] += cell["wins"]
        path_agg[p]["total_pnl"] += cell["total_pnl_pct"]

    for path, agg in path_agg.items():
        if agg["trades"] < MIN_CELL_TRADES:
            continue
        wr = round(agg["wins"] / agg["trades"] * 100, 1) if agg["trades"] else 0
        if wr < 15 and agg["total_pnl"] < -10:
            issues.append({
                "issue": f"'{path}' path: {wr}% win rate, {round(agg['total_pnl'], 1)}% total P&L",
                "severity": "critical" if wr == 0 else "high",
                "recommendation": f"Disable '{path}' path or raise minimum score significantly",
                "trades_affected": agg["trades"],
                "pnl_saved": round(abs(agg["total_pnl"]), 2),
            })

    # Stop-loss analysis
    sl = exit_analysis.get("stop_loss", {})
    if sl.get("quick_stops_pct", 0) > 50:
        issues.append({
            "issue": f"Stop losses too tight: {sl['quick_stops_pct']}% hit within 24h",
            "severity": "medium",
            "recommendation": "Widen stop-loss percentages or add ATR-based dynamic stops",
            "trades_affected": sl.get("trades", 0),
            "pnl_saved": 0,  # Can't quantify without re-running with wider stops
        })

    # Time exit analysis
    te = exit_analysis.get("time_exit", {})
    if te.get("trades", 0) > 5 and te.get("win_rate_pct", 0) < 30:
        issues.append({
            "issue": f"Time exits underperform: {te['win_rate_pct']}% WR on {te['trades']} trades",
            "severity": "medium",
            "recommendation": "Review max hold period or add momentum-based early exit",
            "trades_affected": te.get("trades", 0),
            "pnl_saved": round(abs(te.get("total_pnl_pct", 0)), 2),
        })

    # Extreme drawdown
    if drawdown.get("max_drawdown_pct", 0) > 100:
        issues.append({
            "issue": f"Max drawdown: {drawdown['max_drawdown_pct']}% (catastrophic)",
            "severity": "critical",
            "recommendation": "Add portfolio-level drawdown circuit breaker",
            "trades_affected": 0,
            "pnl_saved": 0,
        })

    # High-sensitivity parameters from sweep
    if sensitivity:
        for s in sensitivity[:5]:  # Top 5 most sensitive
            if s["sensitivity"] > 2.0:  # >2pp expectancy swing
                issues.append({
                    "issue": f"Parameter '{s['parameter']}' is highly sensitive (range: {s['sensitivity']}pp expectancy)",
                    "severity": "medium",
                    "recommendation": f"Current={s['current']}, best={s['best_value']} (exp: {s['best_expectancy']}pp)",
                    "trades_affected": 0,
                    "pnl_saved": round(abs(s["best_expectancy"] - s["worst_expectancy"]), 2),
                })

    # Sort by severity then pnl_saved
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda i: (severity_order.get(i["severity"], 9), -i["pnl_saved"]))
    return issues


# ---------------------------------------------------------------------------
# Phase 4: Quick Wins
# ---------------------------------------------------------------------------
def generate_quick_wins(
    failure_cells: List[Dict],
    all_cells: List[Dict],
) -> List[str]:
    """Generate actionable quick-win recommendations from failure analysis."""
    wins = []

    # Paths to disable (0% WR with enough trades)
    path_agg: Dict[str, Dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
    for cell in all_cells:
        p = cell["path"]
        path_agg[p]["trades"] += cell["trades"]
        path_agg[p]["wins"] += cell["wins"]
        path_agg[p]["total_pnl"] += cell["total_pnl_pct"]

    for path, agg in sorted(path_agg.items(), key=lambda x: x[1]["total_pnl"]):
        if agg["trades"] < MIN_CELL_TRADES:
            continue
        wr = round(agg["wins"] / agg["trades"] * 100, 1)
        if wr == 0:
            wins.append(f"Disable '{path}' path (0% WR, {agg['trades']} trades, saves {round(abs(agg['total_pnl']), 1)} pnl)")
        elif wr < 15:
            wins.append(f"Raise '{path}' threshold (only {wr}% WR, {agg['trades']} trades, {round(agg['total_pnl'], 1)} pnl)")

    # Regime-specific disabling
    regime_agg: Dict[str, Dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
    for cell in all_cells:
        r = cell["regime"]
        regime_agg[r]["trades"] += cell["trades"]
        regime_agg[r]["wins"] += cell["wins"]
        regime_agg[r]["total_pnl"] += cell["total_pnl_pct"]

    for regime, agg in sorted(regime_agg.items(), key=lambda x: x[1]["total_pnl"]):
        if agg["trades"] < MIN_CELL_TRADES:
            continue
        wr = round(agg["wins"] / agg["trades"] * 100, 1)
        if wr == 0:
            wins.append(f"Disable trading in {regime} regime (0% WR, {agg['trades']} trades, saves {round(abs(agg['total_pnl']), 1)} pnl)")
        elif wr < 15:
            wins.append(f"Restrict {regime} regime to high-conviction only ({wr}% WR, {agg['trades']} trades)")

    return wins


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_adversarial_analysis(
    baseline_only: bool = False,
    sweep_steps: int = DEFAULT_SWEEP_STEPS,
    days: int = 90,
) -> Dict[str, Any]:
    """Run the full adversarial analysis pipeline and return the report."""
    start_time = time.time()

    # Phase 1: Load baseline trades and stratify
    summary, trades = _load_backtest_trades()
    if not trades:
        logger.error("[adversarial] No backtest trades found — run historical_replay first")
        return {"error": "no_backtest_trades"}

    baseline_stats = _compute_cell_stats(trades)
    failure_cells, all_cells = identify_failure_cells(trades)
    exit_analysis = analyze_exit_reasons(trades)
    drawdown = compute_max_drawdown(trades)

    logger.info(f"[adversarial] Phase 1 complete: {len(trades)} trades, {len(failure_cells)} failure cells")

    # Phase 2: Sensitivity sweep (skip if baseline-only)
    sensitivity = []
    if not baseline_only:
        try:
            sensitivity = run_sensitivity_sweep(sweep_steps=sweep_steps, days=days)
            logger.info(f"[adversarial] Phase 2 complete: {len(sensitivity)} parameters swept")
        except Exception as e:
            logger.error(f"[adversarial] Sensitivity sweep failed: {e}")

    # Phase 3: Structural issues
    structural_issues = build_structural_issues(
        failure_cells, exit_analysis, drawdown, all_cells, sensitivity
    )
    logger.info(f"[adversarial] Phase 3 complete: {len(structural_issues)} structural issues")

    # Phase 4: Quick wins
    quick_wins = generate_quick_wins(failure_cells, all_cells)

    elapsed = round(time.time() - start_time, 1)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "baseline_only": baseline_only,
        "baseline": {
            "total_trades": baseline_stats["trades"],
            "win_rate_pct": baseline_stats["win_rate_pct"],
            "expectancy_pct": baseline_stats["expectancy_pct"],
            "total_pnl_pct": baseline_stats["total_pnl_pct"],
            "profit_factor": summary.get("profit_factor", 0),
            "max_drawdown_pct": drawdown["max_drawdown_pct"],
        },
        "failure_cells": failure_cells,
        "all_cells": all_cells,
        "exit_analysis": exit_analysis,
        "drawdown": drawdown,
        "parameter_sensitivity": sensitivity[:10] if sensitivity else [],  # Top 10 most sensitive
        "structural_issues": structural_issues,
        "quick_wins": quick_wins,
    }

    # Save report
    try:
        safe_json_save(ADVERSARIAL_REPORT_FILE, report)
        logger.info(f"[adversarial] Report saved to {ADVERSARIAL_REPORT_FILE}")
    except Exception as e:
        logger.error(f"[adversarial] Failed to save report: {e}")
        # Fallback: direct write
        with open(ADVERSARIAL_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")

    return report


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    ap = argparse.ArgumentParser(description="Adversarial backtester — probe system for structural weaknesses")
    ap.add_argument("--baseline-only", action="store_true", help="Skip sensitivity sweeps (faster)")
    ap.add_argument("--sweep-steps", type=int, default=DEFAULT_SWEEP_STEPS, help=f"Points per parameter sweep (default {DEFAULT_SWEEP_STEPS})")
    ap.add_argument("--days", type=int, default=90, help="Replay lookback days for sweeps (default 90)")
    args = ap.parse_args()

    report = run_adversarial_analysis(
        baseline_only=args.baseline_only,
        sweep_steps=args.sweep_steps,
        days=args.days,
    )

    if "error" in report:
        print(f"ERROR: {report['error']}")
        return 1

    # Print summary
    b = report["baseline"]
    print(f"\n{'='*60}")
    print(f"ADVERSARIAL ANALYSIS REPORT")
    print(f"{'='*60}")
    print(f"Baseline: {b['total_trades']} trades | WR {b['win_rate_pct']}% | "
          f"Exp {b['expectancy_pct']}% | PF {b['profit_factor']} | MaxDD {b['max_drawdown_pct']}%")
    print()

    if report["failure_cells"]:
        print(f"FAILURE CELLS ({len(report['failure_cells'])}):")
        for fc in report["failure_cells"]:
            print(f"  {fc['regime']:10} × {fc['path']:25} | "
                  f"WR {fc['win_rate_pct']:5.1f}% | {fc['trades']:3d} trades | "
                  f"PnL {fc['total_pnl_pct']:+8.1f}% | → {fc['recommendation']}")
        print()

    if report["structural_issues"]:
        print(f"STRUCTURAL ISSUES ({len(report['structural_issues'])}):")
        for si in report["structural_issues"]:
            print(f"  [{si['severity'].upper():8}] {si['issue']}")
            print(f"             → {si['recommendation']}")
        print()

    if report["quick_wins"]:
        print(f"QUICK WINS:")
        for qw in report["quick_wins"]:
            print(f"  • {qw}")
        print()

    if report.get("parameter_sensitivity"):
        print(f"TOP SENSITIVE PARAMETERS:")
        for ps in report["parameter_sensitivity"][:5]:
            print(f"  {ps['parameter']:35} | sensitivity {ps['sensitivity']:6.2f}pp | "
                  f"best={ps['best_value']} (exp {ps['best_expectancy']}%) | "
                  f"cliff at {ps.get('cliff_at', 'none')}")
        print()

    print(f"Elapsed: {report['elapsed_seconds']}s | Report: {ADVERSARIAL_REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
