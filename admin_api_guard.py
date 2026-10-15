"""Admin / localhost guard for mutating control-plane HTTP APIs."""
from __future__ import annotations

import hmac
import logging
import os

from flask import jsonify, request

logger = logging.getLogger(__name__)

# Emit a startup warning when no token is configured so operators are aware
# that the guard falls back to direct connection IP only (see require_admin_write_access).
if not os.getenv("ADMIN_API_TOKEN"):
    logger.warning(
        "ADMIN_API_TOKEN is not set. Admin write guard allows mutating APIs only "
        "from direct localhost connections (request.remote_addr). "
        "Set ADMIN_API_TOKEN for any host reachable from untrusted networks."
    )


def _client_ip_for_localhost_fallback() -> str:
    """Direct peer IP only. Never use X-Forwarded-For here — it is caller-spoofable."""
    return (request.remote_addr or "").strip()


def is_local_request(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "localhost")


def require_admin_write_access():
    """
    Enforce write-access protection for sensitive control-plane APIs.
    - If ADMIN_API_TOKEN is set: require exact X-Admin-Token match (fails closed).
    - If not set: allow localhost only (request.remote_addr); reject non-local callers.
      X-Forwarded-For is not consulted — unsafe behind some reverse proxies without a token.
    """
    configured_token = (os.getenv("ADMIN_API_TOKEN") or "").strip()
    if configured_token:
        provided = (request.headers.get("X-Admin-Token") or "").strip()
        if not provided or not hmac.compare_digest(provided, configured_token):
            return jsonify({"error": "Unauthorized"}), 403
        return None
    # No token configured — fall back to IP check.  Fail closed: any ambiguous
    # or non-local IP is denied rather than allowed through.
    client_ip = _client_ip_for_localhost_fallback()
    if not is_local_request(client_ip):
        return jsonify({"error": "Forbidden: write API requires localhost or ADMIN_API_TOKEN"}), 403
    return None
