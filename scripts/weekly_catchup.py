#!/usr/bin/env python3
"""Run Saturday pre-optimizer chain when scheduled tasks were skipped or stale.

Order: ML pipeline → historical replay (+ adversarial) → optimizer → roadmap + gated analyses.
Logs to stdout; redirect to data/weekly_catchup.log when run in background.
"""
from __future__ import annotations

import copy
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import tls_setup  # noqa: F401

_log_path = REPO / "data" / "weekly_catchup.log"
_log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_path, encoding="utf-8"),
    ],
)
logger = logging.getLogger("weekly_catchup")


def _patch_optimizer_skip_for_rerun():
    """Allow a second successful optimizer run same UTC day (after fresh ML/replay)."""
    import recursive_optimizer as ro

    orig_load = ro._load_optimizer_log
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    def _load_patched():
        log = orig_load()
        if not log:
            return log
        log = copy.deepcopy(log)
        last = log[-1]
        snap = last.get("snapshot") or {}
        if snap.get("date") == today and not last.get("safety_gate_blocked"):
            snap = {**snap, "date": yesterday}
            log[-1] = {**last, "snapshot": snap}
            logger.info(
                "[catchup] Optimizer same-day skip bypassed (last cycle %s was today)",
                last.get("cycle"),
            )
        return log

    ro._load_optimizer_log = _load_patched
    return ro


def main() -> int:
    logger.info("=== Weekly catch-up started ===")

    # 1. ML pipeline
    logger.info("[1/4] ML pipeline...")
    from ml.run_pipeline import run as run_ml

    run_ml(save_expectancy=True)
    logger.info("[1/4] ML pipeline complete")

    # 2. Historical replay + adversarial
    logger.info("[2/4] Historical replay (90d)...")
    from historical_replay import run_historical_replay
    from resilient_http import reset_breaker

    reset_breaker("coingecko")
    replay_result = run_historical_replay()
    trades = replay_result.get("signals_generated", 0)
    missed = len(replay_result.get("missed_opportunities", []))
    wr = (replay_result.get("summary") or {}).get("win_rate_pct", 0)
    logger.info("[2/4] Replay complete: %s trades (%.1f%% WR), %s missed opps", trades, wr, missed)

    try:
        from scripts.adversarial_backtest import run_adversarial_analysis

        adv = run_adversarial_analysis(baseline_only=True)
        logger.info(
            "[2/4] Adversarial: %s issues, %s quick wins",
            len(adv.get("structural_issues", [])),
            len(adv.get("quick_wins", [])),
        )
    except Exception as e:
        logger.error("[2/4] Adversarial failed: %s", e)

    # 3. Optimizer (force rerun after fresh inputs)
    logger.info("[3/4] Recursive optimizer...")
    ro = _patch_optimizer_skip_for_rerun()
    opt_result = ro.run_full_recursive_optimizer_cycle()
    if opt_result.get("safety_gate_blocked"):
        logger.error("[3/4] Optimizer blocked by safety gate")
        return 1
    if opt_result.get("error"):
        logger.error("[3/4] Optimizer error: %s", opt_result.get("error"))
        return 1
    logger.info(
        "[3/4] Optimizer cycle #%s: %s auto-applied, %s pending",
        opt_result.get("cycle", "?"),
        len(opt_result.get("auto_applied", [])),
        len(opt_result.get("pending_review", [])),
    )

    # 4. Roadmap + gated analyses
    logger.info("[4/4] Roadmap automation...")
    from roadmap_automation import run_roadmap_cycle
    from roadmap_dispatcher import run_roadmap_gated_analyses

    roadmap = run_roadmap_cycle(dry_run=False)
    if roadmap.get("success"):
        logger.info(
            "[4/4] Roadmap: %s transitions, %s pending approvals",
            len(roadmap.get("applied", [])),
            len(roadmap.get("pending_approvals", [])),
        )
    else:
        logger.warning("[4/4] Roadmap error: %s", roadmap.get("error"))

    gated = run_roadmap_gated_analyses(replay_result=replay_result)
    for mod, res in gated.items():
        if isinstance(res, dict) and res.get("success"):
            logger.info("[4/4] Gated %s: success", mod)
        elif isinstance(res, dict):
            logger.warning("[4/4] Gated %s: %s", mod, res.get("error") or res.get("reason", "failed"))

    try:
        from thresholds import reload_live_thresholds

        reload_live_thresholds()
    except Exception as e:
        logger.warning("reload_live_thresholds failed: %s", e)

    logger.info("=== Weekly catch-up finished ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
