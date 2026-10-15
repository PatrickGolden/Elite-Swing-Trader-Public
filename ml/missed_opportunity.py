# ml/missed_opportunity.py — Identify HOLD/WEAK and screening failures that had outcome_7d above threshold.

import sys
from pathlib import Path
from typing import Dict, List, Any

from ml.config import (
    RECOMMENDATIONS_FILE,
    SCREENING_LOG_FILE,
    MISSED_OPPORTUNITY_THRESHOLD_PCT,
)

# Ensure project root is on path for safe_json import
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
import safe_json


def _load_json(path):
    data = safe_json.load(str(path), default=list)
    return data if isinstance(data, list) else []


def analyze(
    threshold_pct: float = None,
) -> Dict[str, Any]:
    """
    Analyze missed opportunities from recommendations and screening log.
    Returns dict with: from_recommendations (list + counts), from_screening (list + counts), summary counts.
    """
    threshold_pct = threshold_pct if threshold_pct is not None else MISSED_OPPORTUNITY_THRESHOLD_PCT
    recs = _load_json(RECOMMENDATIONS_FILE)
    screening = _load_json(Path(SCREENING_LOG_FILE))

    # Data coverage: how many rows have outcome_7d (required for missed-opportunity to mean anything)
    recs_with_o7 = sum(1 for r in recs if r.get("outcome_7d") is not None)
    screen_with_o7 = sum(1 for s in screening if s.get("outcome_7d") is not None)
    recs_hold_weak = sum(1 for r in recs if (r.get("consensus_signal") or "").strip() in ("HOLD", "WEAK_BUY"))

    # --- From recommendations: consensus_signal in (HOLD, WEAK_BUY) and outcome_7d > threshold ---
    rec_missed = []
    by_consensus = {}
    by_regime = {}
    by_tier = {}
    for r in recs:
        if r.get("outcome_7d") is None:
            continue
        o7 = float(r.get("outcome_7d", 0))
        if o7 < threshold_pct:
            continue
        sig = (r.get("consensus_signal") or "").strip()
        if sig not in ("HOLD", "WEAK_BUY"):
            continue
        rec_missed.append({
            "coin_id": r.get("coin_id"),
            "coin_name": r.get("coin_name"),
            "symbol": r.get("coin_symbol"),
            "timestamp": r.get("timestamp"),
            "consensus_signal": sig,
            "outcome_7d": o7,
            "market_regime": r.get("market_regime"),
            "consensus_quality": r.get("consensus_quality"),
        })
        by_consensus[sig] = by_consensus.get(sig, 0) + 1
        reg = r.get("market_regime") or "UNKNOWN"
        by_regime[reg] = by_regime.get(reg, 0) + 1
        tier = r.get("consensus_quality") or "hold"
        by_tier[tier] = by_tier.get(tier, 0) + 1

    # --- From recommendations: consensus_signal is SELL but outcome_7d > threshold (bearish call wrong) ---
    rec_sell_missed = []
    sell_by_regime = {}
    for r in recs:
        if r.get("outcome_7d") is None:
            continue
        o7 = float(r.get("outcome_7d", 0))
        if o7 < threshold_pct:
            continue
        sig = (r.get("consensus_signal") or "").strip()
        if sig != "SELL":
            continue
        rec_sell_missed.append({
            "coin_id": r.get("coin_id"),
            "coin_name": r.get("coin_name"),
            "symbol": r.get("coin_symbol"),
            "timestamp": r.get("timestamp"),
            "consensus_signal": sig,
            "outcome_7d": o7,
            "market_regime": r.get("market_regime"),
            "consensus_quality": r.get("consensus_quality"),
        })
        reg = r.get("market_regime") or "UNKNOWN"
        sell_by_regime[reg] = sell_by_regime.get(reg, 0) + 1

    # --- From screening: HOLD-tier coins that had outcome_7d > threshold ---
    # The screening log records all top-10 coins with their quant_tier; HOLD entries
    # are the ones the system evaluated but declined to trade.
    screen_missed = []
    screen_by_quant = {}
    for s in screening:
        if s.get("outcome_7d") is None:
            continue
        o7 = float(s.get("outcome_7d", 0))
        if o7 < threshold_pct:
            continue
        quant_tier = (s.get("quant_tier") or "").upper()
        if quant_tier not in ("HOLD", ""):
            continue
        screen_missed.append({
            "coin_id": s.get("coin_id"),
            "symbol": s.get("symbol"),
            "timestamp": s.get("timestamp"),
            "outcome_7d": o7,
            "quant_tier": s.get("quant_tier"),
            "market_regime": s.get("market_regime"),
        })
        qt = s.get("quant_tier") or "HOLD"
        screen_by_quant[qt] = screen_by_quant.get(qt, 0) + 1

    policy_skip = {"list": [], "total": 0, "by_skip_reason": {}, "regret_count": 0}
    try:
        from paper_evidence import summarize_policy_skips, SHADOW_SKIPS_FILE
        import json as _json
        policy_skip = summarize_policy_skips()
        if SHADOW_SKIPS_FILE.exists():
            regrets = []
            for line in SHADOW_SKIPS_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                fwd = row.get("forward_return_7d")
                if fwd is not None and float(fwd) >= threshold_pct:
                    if row.get("live_skip_reason") == "policy_no_auto_exec":
                        regrets.append({
                            "symbol": row.get("symbol"),
                            "quant_tier": row.get("quant_tier"),
                            "forward_return_7d": fwd,
                            "market_regime": row.get("market_regime"),
                        })
            policy_skip["regret_list"] = regrets[:50]
            policy_skip["policy_skip_regrets"] = len(regrets)
    except Exception:
        pass

    return {
        "threshold_pct": threshold_pct,
        "from_policy_skips": policy_skip,
        "data_coverage": {
            "recommendations_total": len(recs),
            "recommendations_with_outcome_7d": recs_with_o7,
            "recommendations_hold_or_weak_buy": recs_hold_weak,
            "screening_total": len(screening),
            "screening_with_outcome_7d": screen_with_o7,
        },
        "from_recommendations": {
            "list": rec_missed[:50],
            "total": len(rec_missed),
            "by_consensus_signal": by_consensus,
            "by_regime": by_regime,
            "by_consensus_quality": by_tier,
        },
        "from_sell_signals": {
            "list": rec_sell_missed[:50],
            "total": len(rec_sell_missed),
            "by_regime": sell_by_regime,
        },
        "from_screening": {
            "list": screen_missed[:50],
            "total": len(screen_missed),
            "by_quant_tier": screen_by_quant,
        },
        "summary": {
            "recommendations_missed": len(rec_missed),
            "sell_signals_missed": len(rec_sell_missed),
            "screening_missed": len(screen_missed),
            "policy_skip_regrets": policy_skip.get("policy_skip_regrets", 0),
        },
    }
