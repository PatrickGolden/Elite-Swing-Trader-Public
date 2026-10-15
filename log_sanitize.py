# =============================================================================
# log_sanitize — redact secrets before logging (URLs with api_key, Bearer, env keys)
#
# If an API key ever appeared in logs before this layer, rotate it in the provider
# dashboard (e.g. FRED: https://fred.stlouisfed.org/docs/api/api_key.html).
# =============================================================================

import re
from typing import Iterable, Optional

# Query-string parameters that must not appear with real values in logs
_RE_QUERY_SECRET = re.compile(
    r"([?&])(api_key|apikey|token|access_token|client_secret|secret|password)=([^&\s#\"']+)",
    re.IGNORECASE,
)

_RE_BEARER = re.compile(r"(Bearer\s+)([^\s'\"]+)", re.IGNORECASE)


def redact_secrets_in_text(text: str, extra_secrets: Optional[Iterable[str]] = None) -> str:
    """Strip API keys from URLs and remove known literal secret strings (e.g. env values in error text)."""
    if text is None:
        return ""
    s = str(text)
    s = _RE_QUERY_SECRET.sub(r"\1\2=<redacted>", s)
    s = _RE_BEARER.sub(r"\1<redacted>", s)
    for secret in extra_secrets or ():
        if secret and len(secret) >= 4:
            s = s.replace(secret, "<redacted>")
    return s
