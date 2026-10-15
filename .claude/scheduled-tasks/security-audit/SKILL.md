---
name: security-audit
description: Weekly security audit — check auth gaps, secrets, input validation, dependency CVEs
---

You are a security auditor for a crypto trading system that handles real money. Be thorough.

Checks to perform:

1. Flask route auth audit
   - Read `admin_api_guard.py` to understand the auth pattern
   - **Discover all route files**: Read `app.py` AND scan the `routes/` directory for all `*_bp.py` files (new blueprints may be extracted over time — do not hardcode the list)
   - List every POST/PATCH/DELETE endpoint across ALL discovered route files
   - Verify each state-mutating endpoint has auth protection
   - Flag any unprotected endpoints

2. Secrets in code
   - Search all `.py` and `.json` files for patterns: API keys, passwords, tokens, secrets
   - Check `.env.example` exists and doesn't contain real values
   - Verify `.gitignore` includes `.env`, `*.pem`, credentials files

3. Input validation
   - Check Flask routes that accept user input (request.json, request.args, request.form)
   - Flag any unsanitized input used in file paths, shell commands, or JSON operations
   - Check for path traversal in any file-serving routes

4. Dependency vulnerabilities
   - Read `requirements.txt`
   - Flag any packages known to have CVEs (check version pinning)
   - Flag unpinned dependencies

5. CCXT/Exchange security
   - Verify API keys are loaded from environment, not hardcoded
   - Check that exchange connections use appropriate permissions (trade-only, no withdrawal)
   - Verify rate limiting is in place for API calls

Output: Write findings to `data/security_audit.json`:
{
  "timestamp": "ISO-8601",
  "status": "pass|issues_found",
  "audit_scope": {
    "route_files_scanned": ["app.py", "routes/alerts_bp.py", "..."],
    "post_patch_delete_endpoints": 14,
    "protected_endpoints": 14,
    "unprotected_endpoints": 0,
    "py_files_scanned": 45,
    "json_files_scanned": 12,
    "dependencies_checked": 30
  },
  "findings": [
    {"severity": "high|medium|low", "category": "auth|secrets|input|deps|exchange", "detail": "..."}
  ],
  "summary": "one-line summary"
}

IMPORTANT: The `audit_scope` section is REQUIRED. It proves the depth of the audit — a "pass" with zero endpoint counts is not trustworthy. Always report what you actually scanned.

Do NOT: modify any code (report only), make external API calls, or access any exchange APIs.

After writing the report, email it:
  Run: `python scripts/send_report_email.py --report data/security_audit.json --type security`
  This sends via the configured SMTP/Telegram in AlertSystem. If not configured, it exits cleanly.