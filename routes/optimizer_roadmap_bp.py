"""Recursive optimizer and roadmap automation API routes."""
from __future__ import annotations

import logging
import os
import traceback
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from admin_api_guard import require_admin_write_access

logger = logging.getLogger("app")

optimizer_roadmap_bp = Blueprint("optimizer_roadmap", __name__)


@optimizer_roadmap_bp.route("/api/optimizer/last-report")
def api_optimizer_last_report():
    """Return the last recursive optimizer report as JSON (same structure as the email)."""
    try:
        from recursive_optimizer import get_last_report_data

        data = get_last_report_data(fresh=True)
        if data.get("error"):
            return jsonify(data), 404
        return jsonify(data)
    except Exception as e:
        logger.error("Error building last optimizer report: %s", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/paper-evidence")
def api_paper_evidence():
    """Paper shadow portfolio stats and promotion eligibility (tier x regime x path)."""
    try:
        from paper_evidence import build_full_report, PAPER_EVIDENCE_REPORT_FILE
        import safe_json

        if PAPER_EVIDENCE_REPORT_FILE.exists():
            cached = safe_json.load(str(PAPER_EVIDENCE_REPORT_FILE), default=dict)
            if cached and cached.get("paper_attribution"):
                return jsonify(cached)
        return jsonify(build_full_report())
    except Exception as e:
        logger.error("paper-evidence API error: %s", e)
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/optimizer/lean-status")
def api_optimizer_lean_status():
    """Return lean mode readiness milestones for advanced optimizer features."""
    import app as app_module

    try:
        status = app_module._get_lean_profit_status()
        status["lean_mode"] = app_module.LEAN_PROFIT_FIRST_MODE
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/roadmap/doc")
def api_roadmap_doc():
    """Return canonical roadmap markdown so Roadmap tab stays aligned with docs."""
    import app as app_module

    try:
        path = app_module.ROADMAP_DOC_PATH
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat()
        return jsonify({"path": "docs/LEAN_PROFIT_ROADMAP.md", "markdown": text, "updated_at_utc": mtime})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/roadmap/automation")
def api_roadmap_automation_status():
    """Roadmap automation status: feature states, decisions, and pending approvals."""
    try:
        from roadmap_automation import get_roadmap_automation_status

        return jsonify(get_roadmap_automation_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/roadmap/automation/run", methods=["POST"])
def api_roadmap_automation_run():
    """Run roadmap automation cycle manually (dry-run by default)."""
    try:
        auth = require_admin_write_access()
        if auth:
            return auth
        payload = request.get_json(silent=True) or {}
        dry_run = bool(payload.get("dry_run", True))
        from roadmap_automation import run_roadmap_cycle

        result = run_roadmap_cycle(dry_run=dry_run)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/roadmap/automation/decision", methods=["POST"])
def api_roadmap_automation_decision():
    """Approve/reject pending high-risk roadmap feature activation."""
    try:
        auth = require_admin_write_access()
        if auth:
            return auth
        payload = request.get_json(silent=True) or {}
        feature_id = payload.get("feature_id")
        decision = (payload.get("decision") or "").strip().lower()
        rationale = (payload.get("rationale") or "").strip()
        if decision not in ("approve", "reject"):
            return jsonify({"error": "decision must be 'approve' or 'reject'"}), 400
        if not rationale:
            return jsonify({"error": "rationale is required"}), 400
        from roadmap_automation import decide_pending_approval

        actor = request.remote_addr or "api"
        result = decide_pending_approval(
            feature_id=feature_id,
            approve=(decision == "approve"),
            actor=str(actor),
            rationale=rationale,
        )
        code = 200 if result.get("success") else 400
        return jsonify(result), code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/optimizer/resend", methods=["POST"])
def api_optimizer_resend():
    """Resend the latest optimizer report with fresh data. Bypasses dedup."""
    try:
        auth = require_admin_write_access()
        if auth:
            return auth
        from recursive_optimizer import resend_last_optimizer_report

        data = request.get_json() or {}
        for_today = data.get("for_today_only", True)
        sent = resend_last_optimizer_report(for_today_only=for_today)
        if sent:
            return jsonify({"status": "sent", "message": "Optimizer report resent with fresh data"})
        return jsonify({"status": "skipped", "message": "No optimizer cycle found to resend"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@optimizer_roadmap_bp.route("/api/optimizer/status")
def api_optimizer_status():
    """Unified optimizer status: maturing changes, recommendations, veto health, model calibration."""
    import app as app_module

    try:
        result = {}

        try:
            from recursive_optimizer import get_last_report_data

            report = get_last_report_data() or {}
            result["last_cycle"] = {
                "timestamp": report.get("timestamp"),
                "recommendations": report.get("pending_review", []),
                "applied": report.get("auto_applied", []),
                "reverted": report.get("evaluations", []),
            }
            result["model_calibration"] = report.get("model_calibration", {})
            result["veto_accuracy"] = report.get("veto_accuracy", {})
        except Exception:
            result["last_cycle"] = None

        try:
            from roadmap_automation import load_state as load_roadmap_state

            state = load_roadmap_state()
            feature_states = state.get("feature_states", {})
            result["roadmap_features"] = {
                fid: {
                    "status": f.get("status", "unknown"),
                    "active": f.get("status") == "active",
                    "updated_at": f.get("updated_at"),
                }
                for fid, f in feature_states.items()
            }
        except Exception:
            result["roadmap_features"] = {}

        try:
            result["veto_log_30d"] = app_module.summarize_veto_log_30d()
        except Exception:
            result["veto_log_30d"] = {}

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
