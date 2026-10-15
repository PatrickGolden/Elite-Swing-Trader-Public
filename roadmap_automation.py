import copy
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import safe_json

logger = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_FILE = os.path.join(PROJECT_DIR, "config", "roadmap_framework.json")
STATE_FILE = os.path.join(PROJECT_DIR, "data", "roadmap_state.json")
TRANSITIONS_FILE = os.path.join(PROJECT_DIR, "data", "roadmap_transitions.jsonl")
POSITIONS_FILE = os.path.join(PROJECT_DIR, "positions.json")

REQUIRED_ATTR_FIELDS = (
    "fear_greed",
    "funding_rate",
    "price_change_24h",
    "price_change_7d",
    "price_change_30d",
    "edge_score",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Track orphan keys already logged so a long-lived process doesn't re-emit
# the same prune notice on every load_state() call.
_LOGGED_ORPHAN_KEYS: set = set()


def _framework_feature_ids(framework: Dict[str, Any]) -> set:
    features = framework.get("features") or []
    if not isinstance(features, list):
        return set()
    return {
        str(f.get("id") or "").strip()
        for f in features
        if isinstance(f, dict) and f.get("id")
    }


def _framework_state_flag_keys(framework: Dict[str, Any]) -> set:
    """Collect every state_flag key the current framework can set via on_activate/on_deactivate."""
    keys: set = set()
    features = framework.get("features") or []
    if not isinstance(features, list):
        return keys
    for f in features:
        if not isinstance(f, dict):
            continue
        actions = f.get("actions") or {}
        if not isinstance(actions, dict):
            continue
        for hook in ("on_activate", "on_deactivate"):
            for a in actions.get(hook) or []:
                if isinstance(a, dict) and a.get("type") == "state_flag" and a.get("key"):
                    keys.add(str(a.get("key")))
    return keys


def _prune_orphan_state(state: Dict[str, Any], framework: Dict[str, Any]) -> Dict[str, Any]:
    """Drop feature_states / pass_streak_weeks / state_flags entries that no longer
    correspond to any feature in the current framework. Pure: returns the same dict
    after in-place mutation, logs each pruned key at INFO once per process."""
    valid_feature_ids = _framework_feature_ids(framework)
    valid_flag_keys = _framework_state_flag_keys(framework)

    if not valid_feature_ids and not valid_flag_keys:
        return state

    for bucket_name, valid_set in (
        ("feature_states", valid_feature_ids),
        ("pass_streak_weeks", valid_feature_ids),
        ("state_flags", valid_flag_keys),
    ):
        bucket = state.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for orphan_key in [k for k in bucket.keys() if k not in valid_set]:
            del bucket[orphan_key]
            log_token = f"{bucket_name}:{orphan_key}"
            if log_token not in _LOGGED_ORPHAN_KEYS:
                logger.info(
                    "[roadmap] pruned orphan %s entry %r (no longer in framework)",
                    bucket_name, orphan_key,
                )
                _LOGGED_ORPHAN_KEYS.add(log_token)
    return state


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "last_evaluated_at": None,
        "last_applied_at": None,
        "feature_states": {},
        "pass_streak_weeks": {},
        "pending_approvals": [],
        "state_flags": {},
        "last_metrics": {},
        "rollback_counters": {},
    }


def load_framework(path: str | None = None) -> Dict[str, Any]:
    path = path or FRAMEWORK_FILE
    data = safe_json.load(path, default={})
    return data if isinstance(data, dict) else {}


def load_state(path: str | None = None, *, framework: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load roadmap state and self-heal orphan entries against the current framework.

    Pass `framework` to use an in-memory framework dict (e.g. tests). Otherwise the
    real framework file is loaded once and used to prune feature_states /
    pass_streak_weeks / state_flags keys that no longer correspond to any feature."""
    path = path or STATE_FILE
    data = safe_json.load(path, default=_default_state)
    if not isinstance(data, dict):
        return _default_state()
    out = _default_state()
    out.update(data)
    out["feature_states"] = out.get("feature_states") if isinstance(out.get("feature_states"), dict) else {}
    out["pass_streak_weeks"] = out.get("pass_streak_weeks") if isinstance(out.get("pass_streak_weeks"), dict) else {}
    out["pending_approvals"] = out.get("pending_approvals") if isinstance(out.get("pending_approvals"), list) else []
    out["state_flags"] = out.get("state_flags") if isinstance(out.get("state_flags"), dict) else {}
    out["last_metrics"] = out.get("last_metrics") if isinstance(out.get("last_metrics"), dict) else {}
    out["rollback_counters"] = out.get("rollback_counters") if isinstance(out.get("rollback_counters"), dict) else {}

    if framework is None:
        try:
            framework = load_framework()
        except Exception:
            framework = None
    if isinstance(framework, dict) and framework.get("features"):
        _prune_orphan_state(out, framework)
    return out


def _calc_expectancy_30d() -> float | None:
    """Compute rolling 30-day expectancy from LIVE trades only (positions.json).

    Previous implementation read monitoring_expectancy.json which mixes backtest
    and live data — inflating metrics with synthetic performance.  Roadmap gates
    must reflect real capital outcomes.
    """
    positions = safe_json.load(POSITIONS_FILE, default=[])
    if not isinstance(positions, list):
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    closed = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        if p.get("status") not in ("CLOSED", "closed"):
            continue
        if p.get("is_test"):
            continue
        pnl = p.get("pnl_pct")
        if pnl is None:
            continue
        # Use exit_time or exit_date for recency filter
        exit_ts = p.get("exit_time") or p.get("exit_date")
        if exit_ts:
            try:
                from datetime import datetime as _dt
                t = _dt.fromisoformat(str(exit_ts).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if t < cutoff:
                    continue
            except (ValueError, TypeError):
                pass  # Include trade if we can't parse its exit date
        closed.append(float(pnl))
    if not closed:
        return None
    return round(sum(closed) / len(closed), 3)


def collect_metrics() -> Tuple[Dict[str, Any], List[str]]:
    missing: List[str] = []
    metrics: Dict[str, Any] = {
        "closed_trades": 0,
        "attr_completeness_pct": 0.0,
        "expectancy_pct_30d": None,
        "paper_closed_trades": 0,
        "paper_expectancy_pct_30d": None,
        "paper_attr_completeness_pct": 0.0,
        "win_rate_drop_pp_wow": None,
        "trade_count_spike_multiplier": None,
        "source_health_ok": True,
    }

    positions = safe_json.load(POSITIONS_FILE, default=[])
    if not isinstance(positions, list):
        missing.append("positions.json")
        positions = []
    closed = [p for p in positions if isinstance(p, dict) and (p.get("status") in ("CLOSED", "closed")) and not p.get("is_test")]
    metrics["closed_trades"] = len(closed)
    if closed:
        # Only count trades from instrumented period (2026-04-02+) for completeness.
        # Pre-instrumentation trades lack attribution fields by design, not by defect.
        # Including them creates a circular dependency: completeness stays low → roadmap
        # features stay blocked → system can't improve → no new instrumented trades.
        INSTRUMENTATION_CUTOFF = "2026-04-02"
        instrumented = [
            p for p in closed
            if (p.get("entry_time") or "") >= INSTRUMENTATION_CUTOFF
        ]
        hits = 0
        completeness_pool = instrumented if instrumented else closed
        for p in completeness_pool:
            hits += sum(1 for f in REQUIRED_ATTR_FIELDS if p.get(f) is not None)
        metrics["attr_completeness_pct"] = round(100.0 * hits / (len(completeness_pool) * len(REQUIRED_ATTR_FIELDS)), 1)
        metrics["instrumented_trades"] = len(instrumented)
        metrics["total_closed_trades"] = len(closed)
        outcomes = [p.get("outcome") for p in closed if p.get("outcome") in ("win", "loss")]
        if outcomes:
            wins = sum(1 for o in outcomes if o == "win")
            metrics["win_rate_30d"] = round(100.0 * wins / len(outcomes), 1)
        else:
            metrics["win_rate_30d"] = None
    else:
        metrics["win_rate_30d"] = None

    metrics["expectancy_pct_30d"] = _calc_expectancy_30d()
    if metrics["expectancy_pct_30d"] is None and metrics["closed_trades"] > 0:
        missing.append("no_recent_closed_trades_with_pnl")

    try:
        from paper_evidence import load_paper_closed
        paper_closed = load_paper_closed()
        cutoff_p = datetime.now(timezone.utc) - timedelta(days=30)
        paper_recent = []
        for p in paper_closed:
            exit_ts = p.get("exit_time") or p.get("exit_date")
            if exit_ts:
                try:
                    t = datetime.fromisoformat(str(exit_ts).replace("Z", "+00:00"))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    if t >= cutoff_p:
                        paper_recent.append(p)
                except (ValueError, TypeError):
                    pass
        metrics["paper_closed_trades"] = len(paper_recent)
        if paper_recent:
            pnls = [float(p.get("pnl_pct")) for p in paper_recent if p.get("pnl_pct") is not None]
            if pnls:
                metrics["paper_expectancy_pct_30d"] = round(sum(pnls) / len(pnls), 3)
            hits_p = 0
            for p in paper_recent:
                hits_p += sum(1 for f in REQUIRED_ATTR_FIELDS if p.get(f) is not None)
            metrics["paper_attr_completeness_pct"] = round(
                100.0 * hits_p / (len(paper_recent) * len(REQUIRED_ATTR_FIELDS)), 1
            )
    except Exception:
        pass

    # Regime-specific win rate for catastrophe detection
    regime_stats: Dict[str, Dict[str, int]] = {}
    for p in closed:
        regime = (p.get("market_regime") or "").upper()
        if not regime:
            continue
        if regime not in regime_stats:
            regime_stats[regime] = {"wins": 0, "total": 0}
        regime_stats[regime]["total"] += 1
        if p.get("outcome") == "win":
            regime_stats[regime]["wins"] += 1
    metrics["regime_win_rates"] = {
        r: round(100.0 * s["wins"] / s["total"], 1) if s["total"] > 0 else None
        for r, s in regime_stats.items()
    }
    metrics["regime_trade_counts"] = {r: s["total"] for r, s in regime_stats.items()}

    # Per-regime profit factor (gross wins / gross losses), used by regime PF kill switch visibility
    regime_pf: Dict[str, Optional[float]] = {}
    regime_trades_grouped: Dict[str, list] = {}
    for p in closed:
        r = (p.get('market_regime') or p.get('regime') or '').upper()
        if r and not p.get('manual_override'):
            regime_trades_grouped.setdefault(r, []).append(p)
    for r, r_trades in regime_trades_grouped.items():
        wins_usd = sum(float(t.get('pnl_usd', 0) or 0) for t in r_trades if t.get('outcome') == 'win')
        loss_usd = abs(sum(float(t.get('pnl_usd', 0) or 0) for t in r_trades if t.get('outcome') == 'loss'))
        n = len(r_trades)
        if n >= 10 and loss_usd > 0:
            regime_pf[r] = round(wins_usd / loss_usd, 2)
        elif n >= 10:
            regime_pf[r] = None  # all wins — PF undefined
    metrics['regime_profit_factors'] = regime_pf

    # Dynamic stop telemetry (Phase 3C): compute slippage and reprice failure rate
    try:
        import json as _json
        _telem_path = Path(os.path.dirname(__file__)) / 'data' / 'dynamic_stop_telemetry.jsonl'
        stop_telem = []
        if _telem_path.exists():
            with open(_telem_path, 'r', encoding='utf-8') as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line:
                        try:
                            stop_telem.append(_json.loads(_line))
                        except _json.JSONDecodeError:
                            pass
        if stop_telem:
            recent_stops = [s for s in stop_telem if isinstance(s, dict)]
            if recent_stops:
                slippages = [s.get('slippage_pct', 0) for s in recent_stops if s.get('success')]
                # Filter out mock/test-environment entries (no real exchange configured).
                # "pip install ccxt" errors come from the stub exchange, not Coinbase API failures,
                # and would inflate the failure rate far above real values.
                real_stops = [s for s in recent_stops if 'pip install' not in (s.get('error') or '')]
                # Filter out stale entries for positions that are now CLOSED.
                # A reprice attempt whose intended_stop matches no currently-open position's stop
                # is from a position that was closed externally or by the journal — those historical
                # failures should not count against the current failure rate.
                try:
                    _open_stops: set[float] = set()
                    _all_pos = safe_json.load(POSITIONS_FILE, default=[])
                    for _p in _all_pos:
                        if (isinstance(_p, dict)
                                and (_p.get('status') or '').upper() == 'OPEN'
                                and not _p.get('is_test')
                                and _p.get('current_stop_price')):
                            _open_stops.add(round(float(_p['current_stop_price']), 4))
                    if _open_stops:
                        def _active_reprice(entry: dict) -> bool:
                            intended = entry.get('intended_stop')
                            if intended is None:
                                return True
                            rounded = round(float(intended), 4)
                            return any(s > 0 and abs(rounded - s) / s < 0.01 for s in _open_stops)
                        real_stops = [s for s in real_stops if _active_reprice(s)]
                except Exception:
                    pass  # Position load failed; use unfiltered real_stops
                failures = [s for s in real_stops if not s.get('success')]
                if slippages:
                    metrics['dynamic_stop_slippage_to_intended_pct'] = round(max(slippages), 2)
                if real_stops:
                    metrics['dynamic_stop_reprice_failure_rate_pct'] = round(
                        100.0 * len(failures) / len(real_stops), 1
                    )
    except Exception as e:
        logger.debug("[roadmap] Stop telemetry unavailable: %s", e)

    # Veto log entry count (for veto_accuracy_autotuning prerequisite)
    try:
        import json as _json2
        _veto_path = Path(os.path.dirname(__file__)) / 'data' / 'veto_log.jsonl'
        veto_count = 0
        if _veto_path.exists():
            with open(_veto_path, 'r', encoding='utf-8') as _f:
                for _line in _f:
                    if _line.strip():
                        veto_count += 1
        metrics['veto_log_entries'] = veto_count
    except Exception as e:
        logger.debug("[roadmap] Veto log count unavailable: %s", e)

    return metrics, missing


def _evaluate_rollback_triggers(
    framework: Dict[str, Any],
    state: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate configured rollback triggers and return trigger evidence.

    This is intentionally fail-closed for promotions: when trigger data is unavailable,
    we record it as unavailable so operators can see parity gaps explicitly.
    """
    cfg = framework.get("rollback_triggers") or {}
    if not isinstance(cfg, dict):
        return {"triggered": [], "unavailable": []}

    triggered: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []
    counters = state.setdefault("rollback_counters", {})
    last_metrics = state.get("last_metrics") if isinstance(state.get("last_metrics"), dict) else {}

    exp_days = cfg.get("expectancy_below_zero_days")
    if exp_days is not None:
        exp = metrics.get("expectancy_pct_30d")
        if exp is None:
            unavailable.append({"trigger": "expectancy_below_zero_days", "reason": "expectancy_pct_30d_unavailable"})
        else:
            key = "expectancy_below_zero_days"
            cur = int(counters.get(key, 0) or 0)
            cur = cur + 1 if float(exp) < 0 else 0
            counters[key] = cur
            if cur >= int(exp_days):
                triggered.append({
                    "trigger": "expectancy_below_zero_days",
                    "observed": cur,
                    "threshold": int(exp_days),
                    "reason": f"expectancy_pct_30d below zero for {cur} consecutive cycles",
                })

    wr_drop = cfg.get("win_rate_drop_pp_wow")
    if wr_drop is not None:
        current_wr = metrics.get("win_rate_30d")
        prev_wr = last_metrics.get("win_rate_30d")
        if current_wr is None or prev_wr is None:
            unavailable.append({"trigger": "win_rate_drop_pp_wow", "reason": "win_rate_30d_unavailable"})
        else:
            drop = float(prev_wr) - float(current_wr)
            if drop > float(wr_drop):
                triggered.append({
                    "trigger": "win_rate_drop_pp_wow",
                    "observed": round(drop, 2),
                    "threshold": float(wr_drop),
                    "reason": f"win_rate_30d dropped by {drop:.2f}pp",
                })

    spike_mult = cfg.get("trade_count_spike_multiplier")
    if spike_mult is not None:
        prev_count = last_metrics.get("closed_trades")
        cur_count = metrics.get("closed_trades")
        if prev_count in (None, 0) or cur_count is None:
            unavailable.append({"trigger": "trade_count_spike_multiplier", "reason": "insufficient_trade_count_baseline"})
        else:
            ratio = float(cur_count) / float(prev_count)
            if ratio > float(spike_mult):
                triggered.append({
                    "trigger": "trade_count_spike_multiplier",
                    "observed": round(ratio, 3),
                    "threshold": float(spike_mult),
                    "reason": f"closed trade count spiked {ratio:.2f}x",
                })

    min_attr = cfg.get("min_attr_completeness_pct")
    if min_attr is not None:
        attr = metrics.get("attr_completeness_pct")
        if attr is None:
            unavailable.append({"trigger": "min_attr_completeness_pct", "reason": "attr_completeness_pct_unavailable"})
        elif float(attr) < float(min_attr):
            triggered.append({
                "trigger": "min_attr_completeness_pct",
                "observed": float(attr),
                "threshold": float(min_attr),
                "reason": "attribution completeness below rollback floor",
            })

    # Present but currently not emitted by telemetry collectors; keep explicit in status.
    for key, metric_key in (
        ("max_dynamic_stop_slippage_to_intended_pct", "dynamic_stop_slippage_to_intended_pct"),
        ("max_dynamic_stop_reprice_failure_rate_pct", "dynamic_stop_reprice_failure_rate_pct"),
    ):
        if cfg.get(key) is not None:
            observed = metrics.get(metric_key)
            if observed is None:
                unavailable.append({"trigger": key, "reason": f"{metric_key}_unavailable"})
            elif float(observed) > float(cfg.get(key)):
                triggered.append({
                    "trigger": key,
                    "observed": float(observed),
                    "threshold": float(cfg.get(key)),
                    "reason": f"{metric_key} exceeded rollback ceiling",
                })

    # Existing regime catastrophe trigger remains active.
    cat_min_trades = cfg.get("regime_catastrophe_min_trades")
    cat_min_wr = cfg.get("regime_catastrophe_min_wr_pct")
    if cat_min_trades is not None and cat_min_wr is not None:
        for regime, wr in (metrics.get("regime_win_rates") or {}).items():
            count = (metrics.get("regime_trade_counts") or {}).get(regime, 0)
            if count >= int(cat_min_trades) and wr is not None and float(wr) < float(cat_min_wr):
                triggered.append({
                    "trigger": "regime_catastrophe_min_wr_pct",
                    "observed": {"regime": regime, "win_rate_pct": wr, "trades": count},
                    "threshold": {"min_trades": int(cat_min_trades), "min_wr_pct": float(cat_min_wr)},
                    "reason": f"regime {regime} win rate below catastrophe floor",
                })

    return {"triggered": triggered, "unavailable": unavailable}


def _prereq_check(feature: Dict[str, Any], metrics: Dict[str, Any], state: Dict[str, Any]) -> Tuple[bool, List[str]]:
    prereq = feature.get("prerequisites") or {}
    reasons: List[str] = []
    ok = True
    if not isinstance(prereq, dict):
        return False, ["invalid_prerequisites"]

    for dep_id in prereq.get("depends_on_features", []) or []:
        dep_state = (state.get("feature_states") or {}).get(dep_id) or {}
        if dep_state.get("status") != "active":
            ok = False
            reasons.append(f"dependency_not_active:{dep_id}")

    closed_req = prereq.get("closed_trades_gte")
    if closed_req is not None and metrics.get("closed_trades", 0) < int(closed_req):
        ok = False
        reasons.append("closed_trades_below_threshold")

    attr_req = prereq.get("attr_completeness_pct_gte")
    if attr_req is not None and float(metrics.get("attr_completeness_pct", 0) or 0) < float(attr_req):
        ok = False
        reasons.append("attr_completeness_below_threshold")

    exp_req = prereq.get("expectancy_pct_30d_gte")
    exp_val = metrics.get("expectancy_pct_30d")
    if exp_req is not None and (exp_val is None or float(exp_val) < float(exp_req)):
        ok = False
        reasons.append("expectancy_below_threshold")

    veto_req = prereq.get("veto_log_entries_gte")
    if veto_req is not None and int(metrics.get("veto_log_entries", 0)) < int(veto_req):
        ok = False
        reasons.append("veto_log_entries_below_threshold")

    return ok, reasons


def evaluate_decisions(framework: Dict[str, Any], state: Dict[str, Any], metrics: Dict[str, Any], missing_inputs: List[str]) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    features = framework.get("features") or []
    if not isinstance(features, list):
        return decisions
    fail_closed = bool((framework.get("evaluation") or {}).get("fail_closed_on_missing_data", True))

    ordered = sorted(
        [f for f in features if isinstance(f, dict)],
        key=lambda f: (str(f.get("stage", "")), int(f.get("priority", 9999))),
    )
    for f in ordered:
        fid = str(f.get("id") or "").strip()
        if not fid:
            continue
        current = ((state.get("feature_states") or {}).get(fid) or {}).get("status", "inactive")
        prereq_ok, reasons = _prereq_check(f, metrics, state)

        if fail_closed and missing_inputs:
            decisions.append({
                "feature_id": fid,
                "decision": "blocked",
                "current_status": current,
                "reasons": ["missing_inputs"] + list(missing_inputs),
                "feature": f,
            })
            continue

        streaks = state.get("pass_streak_weeks") or {}
        streak = int(streaks.get(fid, 0) or 0)
        required_streak = int(f.get("stability_window_weeks", 1) or 1)
        if prereq_ok:
            streak += 1
        else:
            streak = 0

        action = "hold"
        if prereq_ok and current != "active" and streak >= required_streak:
            action = "promote"
        elif (not prereq_ok) and current == "active":
            action = "demote"

        decisions.append({
            "feature_id": fid,
            "decision": action,
            "current_status": current,
            "reasons": reasons,
            "streak": streak,
            "required_streak": required_streak,
            "feature": f,
        })

    return decisions


def _apply_actions_to_state_flags(state: Dict[str, Any], actions: List[Dict[str, Any]]) -> None:
    flags = state.setdefault("state_flags", {})
    for a in actions:
        if not isinstance(a, dict):
            continue
        if a.get("type") == "state_flag":
            key = a.get("key")
            if not key:
                continue
            flags[str(key)] = a.get("value")


def apply_decisions(
    decisions: List[Dict[str, Any]],
    state: Dict[str, Any],
    metrics: Dict[str, Any],
    dry_run: bool = False,
) -> Dict[str, Any]:
    applied: List[Dict[str, Any]] = []
    approvals: List[Dict[str, Any]] = []
    states = state.setdefault("feature_states", {})
    streaks = state.setdefault("pass_streak_weeks", {})

    for d in decisions:
        fid = d["feature_id"]
        feature = d["feature"]
        streaks[fid] = int(d.get("streak", 0) or 0)
        decision = d["decision"]
        cur_state = states.get(fid, {"status": "inactive"})
        risk = feature.get("risk_level", "high")
        activation_type = feature.get("activation_type", "requires_approval")

        if decision == "promote":
            if risk == "low" and activation_type == "auto_low_risk":
                if not dry_run:
                    states[fid] = {"status": "active", "updated_at": _utc_now_iso()}
                    _apply_actions_to_state_flags(state, (feature.get("actions") or {}).get("on_activate") or [])
                applied.append({"feature_id": fid, "transition": "promote", "auto_applied": True})
            else:
                req = {
                    "feature_id": fid,
                    "requested_at": _utc_now_iso(),
                    "status": "pending_approval",
                    "reason": "high_risk_requires_approval",
                }
                existing_pending = [
                    p for p in (state.get("pending_approvals") or [])
                    if isinstance(p, dict)
                    and p.get("feature_id") == fid
                    and p.get("status") == "pending_approval"
                ]
                if not existing_pending:
                    if not dry_run:
                        state.setdefault("pending_approvals", []).append(req)
                approvals.append(req)
        elif decision == "demote":
            if not dry_run:
                states[fid] = {"status": "inactive", "updated_at": _utc_now_iso()}
                _apply_actions_to_state_flags(state, (feature.get("actions") or {}).get("on_deactivate") or [])
            applied.append({"feature_id": fid, "transition": "demote", "auto_applied": True})
        else:
            # keep state if present
            if fid not in states:
                states[fid] = copy.deepcopy(cur_state)

    return {"applied": applied, "pending_approvals": approvals}


def _log_transitions(transitions: List[Dict[str, Any]], metrics: Dict[str, Any]) -> None:
    for t in transitions:
        rec = {
            "ts": _utc_now_iso(),
            "feature_id": t.get("feature_id"),
            "transition": t.get("transition"),
            "auto_applied": bool(t.get("auto_applied")),
            "rationale": t.get("rationale"),
            "metrics": metrics,
        }
        safe_json.append_jsonl(TRANSITIONS_FILE, rec, default=str)


def _find_feature(framework: Dict[str, Any], feature_id: str) -> Dict[str, Any] | None:
    features = framework.get("features") or []
    for f in features:
        if isinstance(f, dict) and str(f.get("id") or "") == str(feature_id):
            return f
    return None


def run_roadmap_cycle(dry_run: bool = False) -> Dict[str, Any]:
    framework = load_framework()
    state = load_state()
    if not framework:
        return {"success": False, "error": "roadmap framework missing"}

    metrics, missing = collect_metrics()
    decisions = evaluate_decisions(framework, state, metrics, missing)
    rollback_eval = _evaluate_rollback_triggers(framework, state, metrics)
    rollback_triggered = rollback_eval.get("triggered", [])

    if rollback_triggered:
        # Scope-aware rollback application: each trigger only blocks/demotes features
        # whose risk_level is in the trigger's configured scope. Triggers without an
        # explicit scope entry fall back to affecting every risk class (fail-closed default).
        # Rationale: financial-performance triggers (expectancy, WR drop) should fence
        # high-risk features that add execution exposure, but must NOT block diagnostic
        # or protective features (SHAP, telemetry, suspension actions) — those are most
        # valuable precisely when performance is deteriorating.
        trigger_ids_all = [t.get("trigger") for t in rollback_triggered if isinstance(t, dict)]
        scope_cfg = framework.get("rollback_trigger_scope") or {}
        default_scope = ["low", "medium", "high"]
        states = state.get("feature_states") or {}
        features_by_id = {
            f.get("id"): f for f in (framework.get("features") or []) if isinstance(f, dict)
        }
        for d in decisions:
            fid = d.get("feature_id")
            risk_level = ((features_by_id.get(fid) or {}).get("risk_level") or "low").lower()
            applicable = [
                tid for tid in trigger_ids_all
                if risk_level in (scope_cfg.get(tid) or default_scope)
            ]
            if not applicable:
                continue
            current = ((states.get(fid) or {}).get("status") or "").lower()
            if current == "active":
                d["decision"] = "demote"
                d["reasons"] = list(d.get("reasons", [])) + [f"rollback_trigger:{tid}" for tid in applicable]
            elif d.get("decision") == "promote":
                d["decision"] = "blocked"
                d["reasons"] = list(d.get("reasons", [])) + [f"rollback_trigger_active:{tid}" for tid in applicable]

    outcome = apply_decisions(decisions, state, metrics, dry_run=dry_run)

    # Regime catastrophe detection: flag regimes with <15% WR over 20+ trades
    rollback_cfg = framework.get("rollback_triggers") or {}
    cat_min_trades = int(rollback_cfg.get("regime_catastrophe_min_trades", 20))
    cat_min_wr = float(rollback_cfg.get("regime_catastrophe_min_wr_pct", 15.0))
    regime_warnings: List[Dict[str, Any]] = []
    for regime, wr in (metrics.get("regime_win_rates") or {}).items():
        count = (metrics.get("regime_trade_counts") or {}).get(regime, 0)
        if count >= cat_min_trades and wr is not None and wr < cat_min_wr:
            regime_warnings.append({
                "regime": regime,
                "win_rate_pct": wr,
                "trades": count,
                "action": f"DISABLE recommended: {regime} regime has {wr}% WR over {count} trades",
            })
            logger.warning("[roadmap] Regime catastrophe: %s has %.1f%% WR over %d trades — recommend disabling",
                           regime, wr, count)

    if not dry_run:
        state["last_evaluated_at"] = _utc_now_iso()
        state["last_applied_at"] = _utc_now_iso()
        state["last_metrics"] = metrics
        state_dir = os.path.dirname(os.path.abspath(STATE_FILE))
        if state_dir and not os.path.isdir(state_dir):
            os.makedirs(state_dir, exist_ok=True)
        safe_json.save(STATE_FILE, state, default=str)
        _log_transitions(outcome.get("applied", []), metrics)
        # Dense logging: log ALL decisions (hold, blocked, etc.) not just applied transitions
        for d in decisions:
            if d.get("decision") not in ("promote", "demote"):
                _log_transitions([{
                    "feature_id": d.get("feature_id"),
                    "transition": d.get("decision", "hold"),
                    "auto_applied": False,
                    "rationale": "; ".join(d.get("reasons", [])) or None,
                }], metrics)
        # Log rollback trigger evaluation result
        if rollback_triggered:
            _log_transitions([{
                "feature_id": "__rollback_triggers__",
                "transition": "rollback_triggered",
                "auto_applied": True,
                "rationale": ", ".join(t.get("trigger", "") for t in rollback_triggered),
            }], metrics)

    return {
        "success": True,
        "dry_run": dry_run,
        "metrics": metrics,
        "missing_inputs": missing,
        "decisions": [
            {
                "feature_id": d.get("feature_id"),
                "decision": d.get("decision"),
                "reasons": d.get("reasons", []),
                "streak": d.get("streak"),
                "required_streak": d.get("required_streak"),
            }
            for d in decisions
        ],
        "applied": outcome.get("applied", []),
        "pending_approvals": outcome.get("pending_approvals", []),
        "regime_warnings": regime_warnings,
        "rollback": rollback_eval,
        "state": state if dry_run else load_state(),
    }


def get_roadmap_automation_status() -> Dict[str, Any]:
    state = load_state()
    framework = load_framework()
    metrics, missing = collect_metrics()
    decisions = evaluate_decisions(framework, state, metrics, missing)
    return {
        "framework_version": framework.get("version"),
        "metrics": metrics,
        "missing_inputs": missing,
        "feature_states": state.get("feature_states", {}),
        "state_flags": state.get("state_flags", {}),
        "pending_approvals": state.get("pending_approvals", []),
        "next_decisions": [
            {
                "feature_id": d.get("feature_id"),
                "decision": d.get("decision"),
                "reasons": d.get("reasons", []),
                "streak": d.get("streak"),
                "required_streak": d.get("required_streak"),
            }
            for d in decisions
        ],
        "last_evaluated_at": state.get("last_evaluated_at"),
    }


def decide_pending_approval(feature_id: str, approve: bool, actor: str = "manual", rationale: str = "") -> Dict[str, Any]:
    """Approve or reject a pending high-risk roadmap feature request."""
    framework = load_framework()
    state = load_state()
    metrics, _missing = collect_metrics()
    fid = str(feature_id or "").strip()
    if not fid:
        return {"success": False, "error": "feature_id required"}
    rationale_text = str(rationale or "").strip()
    if not rationale_text:
        return {"success": False, "error": "rationale required"}

    pending = state.get("pending_approvals") or []
    target = None
    for p in pending:
        if isinstance(p, dict) and p.get("feature_id") == fid and p.get("status") == "pending_approval":
            target = p
            break
    if target is None:
        return {"success": False, "error": f"no pending approval for {fid}"}

    target["status"] = "approved" if approve else "rejected"
    target["decided_at"] = _utc_now_iso()
    target["decided_by"] = actor
    target["rationale"] = rationale_text

    transitions: List[Dict[str, Any]] = []
    feature = _find_feature(framework, fid)
    if approve:
        state.setdefault("feature_states", {})[fid] = {"status": "active", "updated_at": _utc_now_iso()}
        if feature:
            _apply_actions_to_state_flags(state, (feature.get("actions") or {}).get("on_activate") or [])
        transitions.append({"feature_id": fid, "transition": "approve_activate", "auto_applied": False, "rationale": rationale_text})
    else:
        transitions.append({"feature_id": fid, "transition": "reject_pending", "auto_applied": False, "rationale": rationale_text})

    state["last_applied_at"] = _utc_now_iso()
    state_dir = os.path.dirname(os.path.abspath(STATE_FILE))
    if state_dir and not os.path.isdir(state_dir):
        os.makedirs(state_dir, exist_ok=True)
    safe_json.save(STATE_FILE, state, default=str)
    _log_transitions(transitions, metrics)

    return {
        "success": True,
        "feature_id": fid,
        "decision": "approved" if approve else "rejected",
        "state": state,
    }
