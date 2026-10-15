# paper_evidence.py — Paper trade evidence for optimizer, ML pipeline, and roadmap.
# Paper closes are counterfactual observations for signals live execution policy refused.

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = _PROJECT_DIR / "data"
SHADOW_SKIPS_FILE = DATA_DIR / "shadow_skips.jsonl"
SHADOW_SKIPS_MAX_LINES = 10_000
PAPER_EVIDENCE_REPORT_FILE = DATA_DIR / "paper_evidence_report.json"
ROADMAP_STATE_FILE = DATA_DIR / "roadmap_state.json"

BEAR_ENTRY_PATHS = frozenset({
    "blood_in_the_street", "dip", "pullback_in_trend", "volatility_harvest",
    "capitulation", "vol_contraction",
})
MOMENTUM_PATHS = frozenset({"momentum_breakout", "early_momentum"})
BULL_PATHS = frozenset({"bull_participant", "rally"})

DEFAULT_PAPER_WEIGHT = 0.75
DEFAULT_PROMO_MIN_CLOSED = 20
DEFAULT_PROMO_MIN_WR = 45.0
DEFAULT_PROMO_WILSON_LB = 40.0
DEFAULT_PROMO_MIN_EXPECTANCY = 0.0


def _get_threshold(key: str, default: float) -> float:
    try:
        from thresholds import get_threshold
        v = get_threshold(key, default)
        return float(v if v is not None else default)
    except Exception:
        return float(default)


def path_group(quant_path: Optional[str]) -> str:
    p = (quant_path or "default").strip().lower()
    if p in BEAR_ENTRY_PATHS:
        return "BEAR_ENTRY"
    if p in MOMENTUM_PATHS:
        return "MOMENTUM"
    if p in BULL_PATHS:
        return "BULL_PATH"
    return "DEFAULT"


def slice_key(trade: Dict[str, Any], *, tier_field: str = "quant_tier") -> Tuple[str, str, str]:
    tier = (trade.get(tier_field) or trade.get("final_signal") or "UNKNOWN").strip().upper()
    regime = (trade.get("market_regime") or trade.get("regime") or "UNKNOWN").strip().upper()
    return tier, regime, path_group(trade.get("quant_path"))


def wilson_lower_bound(wins: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom) * 100.0


def kish_effective_n(weights: Sequence[float]) -> float:
    ws = [float(w) for w in weights if w > 0]
    if not ws:
        return 0.0
    s = sum(ws)
    s2 = sum(w * w for w in ws)
    return (s * s) / s2 if s2 > 0 else 0.0


@dataclass
class SliceStats:
    tier: str
    regime: str
    path_group: str
    n_closed: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate_pct: float = 0.0
    expectancy_pct: float = 0.0
    total_pnl_usd: float = 0.0
    wilson_wr_lb_pct: float = 0.0
    effective_n: float = 0.0
    walk_forward_pass: bool = False
    walk_forward_train_expectancy: Optional[float] = None
    walk_forward_val_expectancy: Optional[float] = None
    promotion_eligible: bool = False
    promotion_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _trade_time_sort_key(trade: Dict[str, Any]) -> str:
    ts = trade.get("exit_time") or trade.get("ts") or trade.get("entry_time") or ""
    return str(ts)[:19]


def _expectancy_pct(trades: Sequence[Dict[str, Any]], weights: Optional[Sequence[float]] = None) -> float:
    if not trades:
        return 0.0
    total_w = 0.0
    exp = 0.0
    for i, t in enumerate(trades):
        w = weights[i] if weights is not None else 1.0
        pnl = t.get("pnl_pct")
        if pnl is None:
            continue
        total_w += w
        exp += w * float(pnl)
    return exp / total_w if total_w > 0 else 0.0


def walk_forward_validate(
    trades: List[Dict[str, Any]],
    train_frac: float = 0.6,
    min_train: int = 8,
    min_val: int = 5,
) -> Tuple[bool, Optional[float], Optional[float]]:
    if len(trades) < min_train + min_val:
        return False, None, None
    ordered = sorted(trades, key=_trade_time_sort_key)
    split = max(min_train, int(len(ordered) * train_frac))
    if split >= len(ordered):
        split = len(ordered) - min_val
    train = ordered[:split]
    val = ordered[split:]
    if len(val) < min_val:
        return False, None, None
    train_exp = _expectancy_pct(train)
    val_exp = _expectancy_pct(val)
    return val_exp >= 0.0, round(train_exp, 3), round(val_exp, 3)


def compute_slice_stats(
    trades: List[Dict[str, Any]],
    tier: str,
    regime: str,
    pg: str,
    *,
    promo_min_closed: int = DEFAULT_PROMO_MIN_CLOSED,
    promo_min_wr: float = DEFAULT_PROMO_MIN_WR,
    promo_wilson_lb: float = DEFAULT_PROMO_WILSON_LB,
    promo_min_expectancy: float = DEFAULT_PROMO_MIN_EXPECTANCY,
) -> SliceStats:
    closed = [t for t in trades if t.get("outcome") in ("win", "loss")]
    n = len(closed)
    wins = sum(1 for t in closed if t.get("outcome") == "win")
    losses = n - wins
    wr = (wins / n * 100.0) if n else 0.0
    exp = _expectancy_pct(closed)
    pnl_usd = sum(float(t.get("pnl_usd") or 0) for t in closed)
    w_lb = wilson_lower_bound(float(wins), float(n))
    wf_pass, tr_exp, va_exp = walk_forward_validate(closed)

    reasons: List[str] = []
    eligible = True
    if n < promo_min_closed:
        eligible = False
        reasons.append(f"n_closed {n} < {promo_min_closed}")
    if wr < promo_min_wr:
        eligible = False
        reasons.append(f"win_rate {wr:.1f}% < {promo_min_wr}%")
    if w_lb < promo_wilson_lb:
        eligible = False
        reasons.append(f"wilson_lb {w_lb:.1f}% < {promo_wilson_lb}%")
    if exp < promo_min_expectancy:
        eligible = False
        reasons.append(f"expectancy {exp:.3f}% < {promo_min_expectancy}%")
    if not wf_pass:
        eligible = False
        reasons.append("walk_forward_validation_failed")
    if eligible:
        reasons.append("all_promotion_gates_passed")

    weights = [
        float(t.get("_evidence_weight", DEFAULT_PAPER_WEIGHT if t.get("_evidence_source") == "paper" else 1.0))
        for t in closed
    ]
    eff_n = kish_effective_n(weights) if weights else 0.0

    return SliceStats(
        tier=tier,
        regime=regime,
        path_group=pg,
        n_closed=n,
        n_wins=wins,
        n_losses=losses,
        win_rate_pct=round(wr, 1),
        expectancy_pct=round(exp, 3),
        total_pnl_usd=round(pnl_usd, 2),
        wilson_wr_lb_pct=round(w_lb, 1),
        effective_n=round(eff_n, 2),
        walk_forward_pass=wf_pass,
        walk_forward_train_expectancy=tr_exp,
        walk_forward_val_expectancy=va_exp,
        promotion_eligible=eligible,
        promotion_reasons=reasons,
    )


def load_paper_closed() -> List[Dict[str, Any]]:
    try:
        from trade_journal import get_all_positions
        all_pos = get_all_positions(exclude_test=False)
    except Exception:
        return []
    return [
        p for p in all_pos
        if (p.get("status") or "").upper() == "CLOSED"
        and p.get("source") == "paper_trade"
    ]


def aggregate_slices(
    trades: List[Dict[str, Any]],
    *,
    min_slice_n: int = 1,
) -> Dict[str, SliceStats]:
    buckets: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        buckets[slice_key(t)].append(t)
    out: Dict[str, SliceStats] = {}
    for (tier, regime, pg), group in buckets.items():
        if len(group) < min_slice_n:
            continue
        key = f"{tier}|{regime}|{pg}"
        out[key] = compute_slice_stats(group, tier, regime, pg)
    return out


def tag_evidence_weights(
    live_trades: List[Dict[str, Any]],
    paper_trades: List[Dict[str, Any]],
    paper_weight: float = DEFAULT_PAPER_WEIGHT,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    live_out = []
    for t in live_trades:
        c = dict(t)
        c["_evidence_weight"] = 1.0
        c["_evidence_source"] = "live"
        live_out.append(c)
    paper_out = []
    for t in paper_trades:
        c = dict(t)
        c["_evidence_weight"] = paper_weight
        c["_evidence_source"] = "paper"
        paper_out.append(c)
    return live_out, paper_out


def combine_for_relaxation(
    live_trades: List[Dict[str, Any]],
    paper_trades: List[Dict[str, Any]],
    paper_weight: Optional[float] = None,
) -> List[Dict[str, Any]]:
    w = paper_weight if paper_weight is not None else _get_threshold("PAPER_TRADE_WEIGHT", DEFAULT_PAPER_WEIGHT)
    live_tagged, paper_tagged = tag_evidence_weights(live_trades, paper_trades, w)
    return live_tagged + paper_tagged


def is_param_relaxation(param_type: str, cur_val: float, cand: float) -> bool:
    if param_type == "pct_cap":
        return cand > cur_val
    if param_type == "fg_gate":
        return cand > cur_val
    return cand < cur_val


def paper_regime_expectancy(paper_trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_regime: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in paper_trades:
        r = (t.get("market_regime") or t.get("regime") or "UNKNOWN").strip().upper()
        by_regime[r].append(t)
    out: Dict[str, Dict[str, Any]] = {}
    for regime, group in by_regime.items():
        st = compute_slice_stats(group, "ALL", regime, "ALL")
        out[regime] = {
            "expectancy": st.expectancy_pct,
            "win_rate_pct": st.win_rate_pct,
            "total_trades": st.n_closed,
            "wilson_wr_lb_pct": st.wilson_wr_lb_pct,
        }
    return out


def promotion_recommendations(slices: Dict[str, SliceStats]) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    for key, st in slices.items():
        if not st.promotion_eligible:
            continue
        tier = st.tier
        action = None
        if "MODERATE" in tier:
            action = {
                "type": "execution_policy",
                "policy": "moderate_buy_accumulation",
                "slice": key,
                "description": (
                    f"Paper evidence supports reviewing MODERATE auto-exec in {st.regime} "
                    f"({st.path_group}): n={st.n_closed}, WR={st.win_rate_pct}%, "
                    f"expectancy={st.expectancy_pct}%, Wilson LB={st.wilson_wr_lb_pct}%"
                ),
            }
        elif st.expectancy_pct > 0 and st.n_closed >= DEFAULT_PROMO_MIN_CLOSED:
            action = {
                "type": "threshold_review",
                "policy": "slice_positive_expectancy",
                "slice": key,
                "description": (
                    f"Paper slice {key}: positive expectancy {st.expectancy_pct}% — "
                    "review related score floors (pending only)."
                ),
            }
        if action:
            action["stats"] = st.to_dict()
            action["promotion_eligible"] = True
            recs.append(action)
    return recs


def build_paper_attribution_report(days: int = 90) -> Dict[str, Any]:
    paper = load_paper_closed()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    paper_recent = [p for p in paper if (p.get("exit_time") or "")[:10] >= cutoff]

    slices = aggregate_slices(paper_recent)
    by_regime_and_tier: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for key, st in slices.items():
        tier, regime, _pg = key.split("|", 2)
        by_regime_and_tier[regime][tier] = {
            "trades": st.n_closed,
            "win_rate_pct": st.win_rate_pct,
            "expectancy_pct": st.expectancy_pct,
            "wilson_wr_lb_pct": st.wilson_wr_lb_pct,
            "promotion_eligible": st.promotion_eligible,
        }

    mod = sum(1 for p in paper_recent if "MODERATE" in str(p.get("quant_tier") or "").upper())
    su = len(paper_recent) - mod
    wins = sum(1 for p in paper_recent if p.get("outcome") == "win")

    return {
        "period_days": days,
        "closed_count": len(paper_recent),
        "wins": wins,
        "losses": len(paper_recent) - wins,
        "win_rate_pct": round(wins / len(paper_recent) * 100, 1) if paper_recent else 0.0,
        "total_pnl_usd": round(sum(float(p.get("pnl_usd") or 0) for p in paper_recent), 2),
        "moderate_count": mod,
        "strong_ultra_count": su,
        "by_regime_and_tier": dict(by_regime_and_tier),
        "slices": {k: v.to_dict() for k, v in slices.items()},
        "promotion_recommendations": promotion_recommendations(slices),
        "regime_expectancy": paper_regime_expectancy(paper_recent),
    }


def summarize_policy_skips(
    shadow_path: Optional[Path] = None,
    *,
    min_forward_pct: float = 5.0,
) -> Dict[str, Any]:
    path = shadow_path or SHADOW_SKIPS_FILE
    if not path.exists():
        return {"total": 0, "with_forward": 0, "avg_forward_7d": None, "by_skip_reason": {}, "regret_count": 0}
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    with_fwd = [r for r in rows if r.get("forward_return_7d") is not None]
    regrets = [r for r in with_fwd if float(r.get("forward_return_7d", 0)) >= min_forward_pct]
    by_reason: Dict[str, int] = defaultdict(int)
    for r in rows:
        by_reason[r.get("live_skip_reason") or "unknown"] += 1
    avg_fwd = None
    if with_fwd:
        avg_fwd = round(sum(float(r["forward_return_7d"]) for r in with_fwd) / len(with_fwd), 2)
    return {
        "total": len(rows),
        "with_forward": len(with_fwd),
        "regret_count": len(regrets),
        "avg_forward_7d": avg_fwd,
        "by_skip_reason": dict(by_reason),
        "threshold_pct": min_forward_pct,
    }


def append_shadow_skip(row: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Append one live policy-skip row (rotated JSONL via safe_json)."""
    import safe_json
    target = str(path or SHADOW_SKIPS_FILE)
    entry = dict(row)
    if "ts" not in entry:
        entry["ts"] = datetime.now(timezone.utc).isoformat()
    safe_json.append_jsonl(target, entry, max_lines=SHADOW_SKIPS_MAX_LINES, default=str)


def sync_paper_promotion_pending(
    promotions: List[Dict[str, Any]],
    *,
    state_path: Optional[Path] = None,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Upsert roadmap pending_approvals for MODERATE paper promotions (never auto-enable flags)."""
    import safe_json
    from roadmap_automation import load_state

    moderate_promos = [
        p for p in promotions
        if isinstance(p, dict)
        and (p.get("policy") == "moderate_buy_accumulation"
             or (p.get("action") or {}).get("policy") == "moderate_buy_accumulation")
    ]
    if not moderate_promos:
        return []

    path = str(state_path or ROADMAP_STATE_FILE)
    state = load_state(path)
    pending = list(state.get("pending_approvals") or [])
    added: List[Dict[str, Any]] = []
    changed = False
    now = datetime.now(timezone.utc).isoformat()

    for promo in moderate_promos:
        stats = promo.get("stats") or {}
        slice_key = promo.get("slice") or (promo.get("action") or {}).get("slice") or "unknown"
        evidence = {
            "slice": slice_key,
            "n_closed": stats.get("n_closed"),
            "win_rate_pct": stats.get("win_rate_pct"),
            "expectancy_pct": stats.get("expectancy_pct"),
            "wilson_wr_lb_pct": stats.get("wilson_wr_lb_pct"),
            "description": promo.get("description") or (promo.get("action") or {}).get("description"),
        }
        existing = [
            p for p in pending
            if isinstance(p, dict)
            and p.get("feature_id") == "moderate_buy_accumulation"
            and p.get("status") == "pending_approval"
            and p.get("evidence", {}).get("slice") == slice_key
        ]
        if existing:
            existing[0]["evidence"] = evidence
            existing[0]["updated_at"] = now
            changed = True
            continue
        req = {
            "feature_id": "moderate_buy_accumulation",
            "requested_at": now,
            "status": "pending_approval",
            "reason": "paper_evidence_promotion",
            "evidence": evidence,
        }
        pending.append(req)
        added.append(req)
        changed = True

    if changed and not dry_run:
        state["pending_approvals"] = pending
        safe_json.save(path, state, default=str)

    return added


def _parse_shadow_ts(ts: str) -> datetime:
    if not ts:
        raise ValueError("empty timestamp")
    s = str(ts).strip()
    if "T" in s:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def load_shadow_skips(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = path or SHADOW_SKIPS_FILE
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def backfill_shadow_skips(
    get_prices_fn,
    *,
    min_age_days: int = 7,
    shadow_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Backfill forward_return_7d on shadow skip rows using current prices vs entry_price."""
    path = shadow_path or SHADOW_SKIPS_FILE
    if not path.exists():
        return {"shadow_skips_updated": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=min_age_days)
    rows = load_shadow_skips(path)
    need_price: set = set()
    indices: List[Tuple[int, float, str]] = []
    for i, row in enumerate(rows):
        if row.get("forward_return_7d") is not None:
            continue
        ts = row.get("ts") or row.get("timestamp")
        entry_price = row.get("entry_price")
        coin_id = row.get("coin_id")
        if not ts or entry_price is None or not coin_id:
            continue
        try:
            t = _parse_shadow_ts(str(ts))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t >= cutoff:
                continue
        except (ValueError, TypeError):
            continue
        need_price.add(coin_id)
        indices.append((i, float(entry_price), coin_id))

    updated = 0
    if indices:
        prices = get_prices_fn(list(need_price)) if need_price else {}
        for i, entry_price, coin_id in indices:
            current = prices.get(coin_id)
            if current is None:
                continue
            pct = round(((float(current) - entry_price) / entry_price) * 100, 2)
            rows[i]["forward_return_7d"] = pct
            rows[i]["forward_backfill_note"] = "mtm_at_backfill"
            updated += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
    return {"shadow_skips_updated": updated}


def shadow_skip_coin_ids_needing_prices(
    *,
    min_age_days: int = 7,
    shadow_path: Optional[Path] = None,
) -> set:
    """Coin IDs that backfill_shadow_skips would request prices for."""
    path = shadow_path or SHADOW_SKIPS_FILE
    if not path.exists():
        return set()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=min_age_days)
    need: set = set()
    for row in load_shadow_skips(path):
        if row.get("forward_return_7d") is not None:
            continue
        ts = row.get("ts") or row.get("timestamp")
        entry_price = row.get("entry_price")
        coin_id = row.get("coin_id")
        if not ts or entry_price is None or not coin_id:
            continue
        try:
            t = _parse_shadow_ts(str(ts))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t >= cutoff:
                continue
        except (ValueError, TypeError):
            continue
        need.add(coin_id)
    return need


def write_paper_evidence_report(report: Dict[str, Any]) -> Path:
    import safe_json
    safe_json.save(str(PAPER_EVIDENCE_REPORT_FILE), report, default=str)
    return PAPER_EVIDENCE_REPORT_FILE


def build_full_report() -> Dict[str, Any]:
    paper_report = build_paper_attribution_report()
    policy = summarize_policy_skips()
    promos = paper_report.get("promotion_recommendations") or []
    try:
        sync_paper_promotion_pending(promos)
    except Exception:
        pass
    full = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_attribution": paper_report,
        "policy_skip_summary": policy,
        "promotion_recommendations": promos,
    }
    write_paper_evidence_report(full)
    return full
