"""Scheduler and schedule management API routes."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from admin_api_guard import require_admin_write_access

schedule_bp = Blueprint("schedule", __name__)
logger = logging.getLogger(__name__)


@schedule_bp.route('/api/schedule/status')
def get_schedule_status():
    """Get current scheduler status and costs"""
    import app as app_module

    try:
        return jsonify(app_module.scheduler.get_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@schedule_bp.route('/api/schedule/send-status-report', methods=['POST'])
def send_status_report_now():
    """Build and send the daily status report email immediately (primary owner + Telegram). Use to preview the report."""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        summary_html = app_module._build_daily_status_summary()
        app_module.alerts.send_daily_status_report(summary_html)
        return jsonify({'ok': True, 'message': 'Status report sent to primary owner and Telegram.'})
    except Exception as e:
        logger.exception("Send status report failed")
        return jsonify({'ok': False, 'error': str(e)}), 500


@schedule_bp.route('/api/schedule/set', methods=['POST'])
def set_schedule():
    """Set refresh interval"""
    import app as app_module

    try:
        guard = require_admin_write_access()
        if guard is not None:
            return guard
        data = request.get_json() or {}
        interval = data.get('interval')
        if interval is None:
            return jsonify({'success': False, 'error': 'interval is required'}), 400

        result = app_module.scheduler.set_interval(interval)

        if result['success']:
            logger.info(f"\n⚙️  Schedule updated to: {result['label']}")
            if result['is_running']:
                logger.info(f"   Next refresh scheduled")
            else:
                logger.info(f"   Auto-refresh disabled")

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@schedule_bp.route('/api/schedule/intervals')
def get_schedule_intervals():
    """Get all available interval options"""
    import app as app_module

    try:
        return jsonify({
            'intervals': app_module.scheduler.get_interval_options()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@schedule_bp.route('/api/schedule/costs')
def get_all_costs():
    """Get cost projections for all intervals"""
    import app as app_module

    try:
        return jsonify({
            'all_costs': app_module.scheduler.get_all_interval_costs()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@schedule_bp.route('/api/schedule/costs/<interval>')
def get_interval_cost(interval):
    """Get cost projection for specific interval"""
    import app as app_module

    try:
        costs = app_module.scheduler.calculate_costs(interval)
        return jsonify(costs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@schedule_bp.route('/api/schedule/learning', methods=['GET', 'POST'])
def schedule_learning():
    """
    GET: Return monthly ML learning schedule status (enabled, day_of_month, last_learning_run).
    POST: Enable/disable and set day of month. Body: { enabled: true, day_of_month: 1 }.
    """
    import app as app_module

    try:
        if request.method == 'POST':
            guard = require_admin_write_access()
            if guard:
                return guard
            data = request.get_json() or {}
            enabled = bool(data.get('enabled', False))
            day = max(1, min(28, int(data.get('day_of_month', 1))))
            return jsonify(app_module.scheduler.set_monthly_learning(enabled, day))
        return jsonify(app_module.scheduler.get_monthly_learning_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@schedule_bp.route('/api/schedule/status-email', methods=['GET', 'POST'])
def schedule_status_email():
    """
    GET: Return status email schedule (enabled, hours_et).
    POST: Set enabled and/or hours_et. Body: { enabled: true, hours_et: [9, 13, 17] }.
    hours_et: list of hours (0-23) in Eastern time when to send the report.
    """
    import app as app_module

    try:
        if request.method == 'POST':
            guard = require_admin_write_access()
            if guard:
                return guard
            data = request.get_json() or {}
            enabled = data.get('enabled') if 'enabled' in data else None
            hours_et = data.get('hours_et')
            if hours_et is not None and not isinstance(hours_et, list):
                hours_et = [int(h) for h in str(hours_et).replace(',', ' ').split() if str(h).strip().isdigit()]
            return jsonify(app_module.scheduler.set_status_email_schedule(enabled=enabled, hours_et=hours_et))
        return jsonify(app_module.scheduler.get_status_email_schedule())
    except Exception as e:
        return jsonify({'error': str(e)}), 500
