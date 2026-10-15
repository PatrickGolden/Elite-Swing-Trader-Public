# ml/suggestions.py — Turn missed-opportunity summary into actionable suggestions; merge with backtest suggestions.

from typing import Dict, List, Any

from ml.config import (
    QUANT_SIGNAL_FILE,
    VETO_SPECIALIST_FILE,
    MISSED_OPPORTUNITY_THRESHOLD_PCT,
)


def build_suggestions(
    missed: Dict[str, Any],
    backtest_suggestions: List[str],
    structured_recs: List[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Build actionable suggestion list from missed_opportunity analysis and backtest suggestions.
    Each item: category, summary, evidence, file, parameter.
    Optional structured_recs from recursive_optimizer adds current_value/suggested_value fields.
    """
    out: List[Dict[str, Any]] = []
    thresh = missed.get("threshold_pct", MISSED_OPPORTUNITY_THRESHOLD_PCT)

    for rec in (structured_recs or []):
        out.append({
            "category": rec.get("category", "optimizer"),
            "summary": rec.get("summary", ""),
            "evidence": rec.get("evidence", ""),
            "file": rec.get("file", QUANT_SIGNAL_FILE),
            "parameter": rec.get("parameter"),
            "current_value": rec.get("current_value"),
            "suggested_value": rec.get("suggested_value"),
            "evidence_trades": rec.get("evidence_trades"),
            "net_expected_pnl_pct": rec.get("net_expected_pnl_pct"),
        })

    # --- Paper promotion recommendations (from learning report) ---
    paper_attr = missed.get("from_policy_skips") or {}
    if paper_attr.get("policy_skip_regrets", 0) >= 3:
        out.append({
            "category": "paper_promotion",
            "summary": (
                f"{paper_attr.get('policy_skip_regrets', 0)} live policy skips "
                f"(MODERATE tier) had forward return >= threshold — review paper_attribution slices."
            ),
            "evidence": str(paper_attr.get("by_skip_reason", {})),
            "file": "paper_evidence.py",
            "parameter": "execution_policy",
        })

    # --- Backtest suggestions (category backtest) ---
    for text in backtest_suggestions or []:
        out.append({
            "category": "backtest",
            "summary": text,
            "evidence": None,
            "file": None,
            "parameter": None,
        })

    # --- From recommendations: HOLD/WEAK_BUY that had good outcome ---
    rec = missed.get("from_recommendations", {})
    total_rec = rec.get("total", 0)
    if total_rec > 0:
        by_regime = rec.get("by_regime", {})
        by_quality = rec.get("by_consensus_quality", {})
        out.append({
            "category": "quant",
            "summary": f"Consider relaxing quant score thresholds: {total_rec} HOLD/WEAK_BUY had outcome_7d > {thresh}%.",
            "evidence": f"By regime: {by_regime}. By quality: {by_quality}.",
            "file": QUANT_SIGNAL_FILE,
            "parameter": "score thresholds / regime_filter",
        })
        for regime, count in sorted(by_regime.items(), key=lambda x: -x[1])[:2]:
            if count >= 3:
                out.append({
                    "category": "quant",
                    "summary": f"In {regime} regime, {count} HOLD/WEAK_BUY signals had outcome_7d > {thresh}%; consider regime-specific thresholds.",
                    "evidence": f"{count} missed in {regime}.",
                    "file": QUANT_SIGNAL_FILE,
                    "parameter": "score threshold / regime",
                })

    # --- From screening: screening failures that later gained ---
    screen = missed.get("from_screening", {})
    total_screen = screen.get("total", 0)
    if total_screen > 0:
        by_quant = screen.get("by_quant_tier", {})
        if by_quant:
            out.append({
                "category": "quant",
                "summary": f"Screening failures that later gained >{thresh}% were in quant tiers: {by_quant}. Consider relaxing score threshold for top tiers.",
                "evidence": str(by_quant),
                "file": QUANT_SIGNAL_FILE,
                "parameter": "score threshold / generate_quant_signal",
            })

    # --- Veto: if we had many consensus HOLD with good outcome, might be over-vetoing (we don't store veto on rec; generic suggestion) ---
    if total_rec >= 5:
        out.append({
            "category": "veto",
            "summary": f"Multiple HOLD/WEAK_BUY ({total_rec}) had outcome_7d > {thresh}%. If some were veto overrides, consider lowering VETO_CONFIDENCE_THRESHOLD.",
            "evidence": f"{total_rec} recommendation missed.",
            "file": VETO_SPECIALIST_FILE,
            "parameter": "VETO_CONFIDENCE_THRESHOLD",
        })

    return out
