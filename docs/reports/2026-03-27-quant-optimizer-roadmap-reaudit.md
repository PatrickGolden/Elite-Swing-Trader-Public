# Quant/Optimizer/Roadmap Re-Audit - 2026-03-27

## Executive Verdict

- Overall posture improved versus prior audits, with strong safety coverage in optimizer and roadmap paths.
- One critical quant regression is present now: quant gate unit suite has 2 failures in BUY-path behavior.
- Strategic fail-closed objective is partially met: roadmap has fail-closed-on-missing-inputs, but rollback trigger parity is incomplete.

## Verification Evidence

### Planned test/script execution

- `pytest tests/unit/test_quant_signal.py -q` -> **FAILED** (2 failed, 26 passed)
  - `test_bull_participant_path_and_badges`
  - `test_regression_floor_prevents_excessive_drag`
- `pytest tests/stress/test_recursive_optimizer.py -q` -> **PASSED** (59/59)
- `pytest tests/stress/test_fixes_hardening.py -q` -> **PASSED** (7/7)
- `pytest tests/unit/test_roadmap_automation.py -q` -> **PASSED** (7/7)
- `pytest tests/unit/test_roadmap_dispatcher.py -q` -> **PASSED** (11/11)
- `pytest tests/execution/test_trade_execution_audit.py -q` -> **PASSED** (35/35)
- `python scripts/diff_optimizer_vs_live.py` -> 2 params differ:
  - `BEAR_REGIME_MIN_SCORE` live `78`, recommended `81`
  - `SIDEWAYS_REGIME_MIN_SCORE` live `68`, recommended `78`
- `python scripts/buy_signals_stress_report.py` -> generated 30d/60d stress reports, both with missed count `1077`

## Control Traceability Matrix (Policy -> Code -> Test -> Runtime)

| Control Objective | Policy/Doc Source | Code Path | Test Evidence | Runtime Artifact | Status |
| --- | --- | --- | --- | --- | --- |
| High-risk roadmap features never auto-apply | `docs/LEAN_PROFIT_ROADMAP.md`, `config/roadmap_framework.json` | `roadmap_automation.py` (`apply_decisions`, `decide_pending_approval`) | `tests/unit/test_roadmap_automation.py` | `data/roadmap_transitions.jsonl` | Pass |
| Fail-closed on missing roadmap inputs | `config/roadmap_framework.json` (`fail_closed_on_missing_data`) | `roadmap_automation.py` (`evaluate_decisions`) | `tests/unit/test_roadmap_automation.py::test_roadmap_fail_closed_on_missing_inputs` | roadmap status APIs/state | Pass |
| Weekly-gated roadmap feature execution | `docs/LEAN_PROFIT_ROADMAP.md` | `scheduler_manager.py`, `roadmap_dispatcher.py` | `tests/unit/test_roadmap_dispatcher.py` | scheduler/roadmap state | Pass (code/test), monitor in prod |
| Quant gate deterministic safety behavior preserved | `docs/QUANT_GATE.md` | `quant_signal.py` (`generate_quant_signal`) | `tests/unit/test_quant_signal.py` | live signals/screening logs | **Fail** |
| Optimizer anti-overfit walk-forward gate | optimizer docs + roadmap guidance | `recursive_optimizer.py` (`_find_best_candidate`) | `tests/stress/test_recursive_optimizer.py` | `data/optimizer_log.json` | Pass |
| Backtest supplement cannot auto-apply optimizer changes | optimizer safety intent | `recursive_optimizer.py` (`counterfactual_analysis`) | `tests/stress/test_recursive_optimizer.py` | optimizer logs/reports | Pass |
| Threshold writes are bounded and rollback-safe | safety intent + audit docs | `recursive_optimizer.py` (`apply_safe_changes`, `_reload_thresholds_or_rollback`) | `tests/stress/test_recursive_optimizer.py` | `live_thresholds.json`, optimizer logs | Pass |
| Execution audit guardrails remain intact | production safety requirements | execution + audit paths | `tests/execution/test_trade_execution_audit.py` | execution/runtime logs | Pass |

## Severity-Ranked Gaps

### Critical

1. Quant BUY-path regressions now failing unit tests.
   - Evidence: `tests/unit/test_quant_signal.py` failures in bull participant and regression-floor scenarios.
   - Risk: suppresses valid BUY opportunities and invalidates confidence in quant gating adjustments.
   - Priority: immediate patch + red/green proof on quant suite before next optimization cycle.

### High

2. Roadmap rollback trigger parity is incomplete.
   - Config defines multiple rollback triggers in `config/roadmap_framework.json`, but enforcement in `roadmap_automation.py` visibly covers only subset (regime catastrophe warning logic, not full trigger set).
   - Risk: strategic "auto-demote on rollback" promise can drift from runtime behavior.

3. Portfolio gate in quant currently fails open on exceptions.
   - Code: `quant_signal.py` `_check_portfolio_gate()` returns `True` on exception.
   - Risk: silent operational failures can bypass concentration controls.

### Medium

4. Optimizer-live threshold drift exists for key regime floors.
   - `scripts/diff_optimizer_vs_live.py` reports deltas for BEAR/SIDEWAYS floors.
   - Risk: recommendation and production behavior diverge without explicit governance decision.

5. Stress report indicates persistent missed-opportunity pressure (1077 in 30d/60d outputs).
   - Risk: strategy may be over-constrained despite safety gains; requires controlled loosening experiments.

### Low

6. Roadmap transition logging is sparse in current artifact snapshot.
   - `data/roadmap_transitions.jsonl` currently has a minimal sample; hold/blocked visibility may be insufficient for full audit traceability.

## Re-Audit Conclusions

- Optimizer safety and hardening controls are in materially better shape than earlier audit cycles.
- Roadmap governance scaffolding is strong, but code-policy parity for rollback triggers should be closed before trusting autonomous stage transitions.
- Quant gate is the current bottleneck: failing deterministic tests block confidence in "self-improving" loop quality.

## Immediate Remediation Order (Fail-Closed First)

1. Fix quant gate regressions and re-run `tests/unit/test_quant_signal.py`.
2. Implement full rollback trigger parity in roadmap automation against framework config.
3. Convert portfolio gate exception path to fail-closed or controlled degraded mode.
4. Resolve optimizer/live drift explicitly (apply/defer decision with rationale logging).
5. Run one full weekly dry-run pipeline and archive artifacts for audit bundle.
