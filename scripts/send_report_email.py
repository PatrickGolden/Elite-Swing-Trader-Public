"""
Send a scheduled-task report via the existing AlertSystem (SMTP + Telegram).

Usage:
    python scripts/send_report_email.py --report data/health_report.json --type health
    python scripts/send_report_email.py --report data/security_audit.json --type security
    python scripts/send_report_email.py --report data/optimizer_audit.json --type optimizer

The script reads the JSON report, formats it as HTML, and sends it via AlertSystem.
Exits 0 on success or if email is not configured.  Exits 1 on unexpected errors.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Run from project root regardless of where the script is called from
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))


STATUS_COLORS = {
    "ok": "#27ae60",
    "healthy": "#27ae60",
    "pass": "#27ae60",
    "warn": "#e67e22",
    "warning": "#e67e22",
    "issues_found": "#e67e22",
    "critical": "#e74c3c",
    "error": "#e74c3c",
}

REPORT_LABELS = {
    "health": "System Health Report",
    "security": "Security Audit Report",
    "optimizer": "Optimizer Audit Report",
}


def _color(status: str) -> str:
    return STATUS_COLORS.get((status or "").lower(), "#7f8c8d")


def _build_health_html(report: dict) -> tuple[str, str]:
    status = report.get("status", "unknown")
    summary = report.get("summary", "")
    ts = report.get("timestamp", "")
    checks = report.get("checks", {})

    rows = ""
    for name, check in checks.items():
        s = check.get("status", "?")
        detail = check.get("detail", "")
        rows += (
            f"<tr>"
            f"<td style='padding:6px 10px'><b>{name}</b></td>"
            f"<td style='padding:6px 10px;color:{_color(s)};font-weight:bold'>{s.upper()}</td>"
            f"<td style='padding:6px 10px'>{detail}</td>"
            f"</tr>"
        )

    html = f"""
<h2 style='color:{_color(status)}'>System Health: {status.upper()}</h2>
<p style='font-size:15px'>{summary}</p>
<table border='1' cellpadding='0' cellspacing='0'
       style='border-collapse:collapse;width:100%;font-size:13px'>
  <tr style='background:#f0f0f0'>
    <th style='padding:6px 10px;text-align:left'>Check</th>
    <th style='padding:6px 10px;text-align:left'>Status</th>
    <th style='padding:6px 10px;text-align:left'>Detail</th>
  </tr>
  {rows}
</table>
<p style='color:#888;font-size:12px;margin-top:12px'>Generated: {ts}</p>
"""
    subject = f"[Swing Trader] Health {status.upper()} — {summary[:60]}"
    return subject, html


def _build_security_html(report: dict) -> tuple[str, str]:
    status = report.get("status", "unknown")
    summary = report.get("summary", "")
    ts = report.get("timestamp", "")
    findings = report.get("findings", [])

    sev_color = {"high": "#e74c3c", "medium": "#e67e22", "low": "#3498db"}
    rows = ""
    for f in findings:
        sev = f.get("severity", "?")
        cat = f.get("category", "?")
        detail = f.get("detail", "")
        sev_c = sev_color.get(sev, "#7f8c8d")
        rows += (
            f"<tr>"
            f"<td style='padding:6px 10px;color:{sev_c};font-weight:bold'>{sev.upper()}</td>"
            f"<td style='padding:6px 10px'>{cat}</td>"
            f"<td style='padding:6px 10px'>{detail}</td>"
            f"</tr>"
        )
    if not rows:
        rows = "<tr><td colspan='3' style='padding:10px;color:#27ae60'>No findings — all checks passed.</td></tr>"

    html = f"""
<h2 style='color:{_color(status)}'>Security Audit: {status.upper()}</h2>
<p style='font-size:15px'>{summary}</p>
<table border='1' cellpadding='0' cellspacing='0'
       style='border-collapse:collapse;width:100%;font-size:13px'>
  <tr style='background:#f0f0f0'>
    <th style='padding:6px 10px;text-align:left'>Severity</th>
    <th style='padding:6px 10px;text-align:left'>Category</th>
    <th style='padding:6px 10px;text-align:left'>Finding</th>
  </tr>
  {rows}
</table>
<p style='color:#888;font-size:12px;margin-top:12px'>Generated: {ts}</p>
"""
    subject = f"[Swing Trader] Security Audit — {status.upper()} ({len(findings)} findings)"
    return subject, html


def _build_optimizer_html(report: dict) -> tuple[str, str]:
    ts = report.get("timestamp", "")
    trend = report.get("performance_trend", "unknown")
    win_rate = report.get("recent_win_rate", 0)
    baseline = report.get("baseline_win_rate", 0)
    pending = report.get("pending_recommendations", 0)
    modified = report.get("thresholds_modified", 0)
    drift = report.get("drift_warnings", [])
    summary = report.get("summary", "")

    trend_color = {"improving": "#27ae60", "stable": "#3498db", "degrading": "#e74c3c"}.get(trend, "#7f8c8d")
    drift_html = (
        "<ul>" + "".join(f"<li>{w}</li>" for w in drift) + "</ul>"
        if drift else "<p style='color:#27ae60'>No drift warnings.</p>"
    )

    html = f"""
<h2>Optimizer Audit</h2>
<table border='1' cellpadding='0' cellspacing='0'
       style='border-collapse:collapse;width:100%;font-size:13px'>
  <tr style='background:#f0f0f0'><th style='padding:6px 10px'>Metric</th><th style='padding:6px 10px'>Value</th></tr>
  <tr><td style='padding:6px 10px'>Performance trend</td>
      <td style='padding:6px 10px;color:{trend_color};font-weight:bold'>{trend.upper()}</td></tr>
  <tr><td style='padding:6px 10px'>Recent win rate</td>
      <td style='padding:6px 10px'>{win_rate:.1%}</td></tr>
  <tr><td style='padding:6px 10px'>Baseline win rate</td>
      <td style='padding:6px 10px'>{baseline:.1%}</td></tr>
  <tr><td style='padding:6px 10px'>Thresholds modified</td>
      <td style='padding:6px 10px'>{modified}</td></tr>
  <tr><td style='padding:6px 10px'>Pending recommendations</td>
      <td style='padding:6px 10px'>{pending}</td></tr>
</table>
<h3>Drift warnings</h3>
{drift_html}
<p style='font-size:15px'><b>Summary:</b> {summary}</p>
<p style='color:#888;font-size:12px;margin-top:12px'>Generated: {ts}</p>
"""
    subject = f"[Swing Trader] Optimizer Audit — {trend.upper()} (win {win_rate:.0%})"
    return subject, html


BUILDERS = {
    "health": _build_health_html,
    "security": _build_security_html,
    "optimizer": _build_optimizer_html,
}


def main():
    parser = argparse.ArgumentParser(description="Email a scheduled-task report via AlertSystem")
    parser.add_argument("--report", required=True, help="Path to the JSON report file")
    parser.add_argument("--type", required=True, choices=["health", "security", "optimizer"],
                        help="Report type (controls formatting and send method)")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"ERROR: report file not found: {report_path}", file=sys.stderr)
        sys.exit(1)

    with open(report_path) as f:
        report = json.load(f)

    builder = BUILDERS[args.type]
    subject, html = builder(report)

    from alert_system import AlertSystem
    alerter = AlertSystem()

    if not alerter.email_configured:
        print("Email not configured (SMTP credentials missing in .env) — skipping send")
        print(f"Subject would be: {subject}")
        sys.exit(0)

    if args.type == "health":
        alerter.send_daily_status_report(html)
    else:
        alerter.send_optimizer_report(subject, html)

    print(f"Sent: {subject}")


if __name__ == "__main__":
    main()
