# =============================================================================
# tls_setup.py — Make Python's SSL stack use the OS trust store
#
# Why this exists:
#   Python's `ssl` module (and therefore `requests`, `urllib3`, `ccxt`, etc.)
#   uses the static `certifi` CA bundle by default. On machines where
#   antivirus / corporate proxy software performs HTTPS inspection (Norton,
#   Zscaler, BitDefender, etc.), the inspecting proxy re-signs every TLS
#   certificate with its own root CA. That root lives in the OS trust store
#   (so browsers work) but is NOT in `certifi`, so every Python request fails
#   with `CERTIFICATE_VERIFY_FAILED`.
#
#   `truststore` wires Python's `ssl` module up to the OS trust store
#   (Windows certificate store / macOS keychain / Linux system bundle), which
#   trusts both the standard public CAs AND any locally-installed roots —
#   exactly what browsers do.
#
# Usage:
#   This module MUST be imported BEFORE any other module that creates an
#   SSL context (requests, urllib3, ccxt, websockets, aiohttp, ...). Import
#   it as the first non-stdlib import in any process entry point.
#
# Safe to import multiple times: `inject_into_ssl()` is idempotent.
# Safe on systems without truststore installed: falls back silently to
# certifi behaviour and logs a debug message.
# =============================================================================

import logging

_log = logging.getLogger(__name__)

try:
    import truststore

    truststore.inject_into_ssl()
    _log.debug("truststore: OS trust store wired into ssl module")
except ImportError:
    _log.debug(
        "truststore not installed — using certifi bundle. "
        "If running behind HTTPS-inspecting AV/proxy (e.g. Norton, Zscaler), "
        "install with: pip install truststore"
    )
except Exception as e:  # pragma: no cover - defensive: never break startup
    _log.warning("truststore.inject_into_ssl() failed: %s", e)
