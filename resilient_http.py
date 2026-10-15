# =============================================================================
# resilient_http.py — Resilient HTTP Client for API Calls
# Provides: retry with exponential backoff, circuit breaker, degradation tracking,
#           and per-source credit budget tracking (CoinGecko Demo plan: 10k/month).
#
# Usage:
#   from resilient_http import resilient_get, api_health_report
#   resp = resilient_get("https://api.example.com/data", timeout=10, source="example")
#   health = api_health_report()  # {"example": {"status": "healthy", ...}}
# =============================================================================

import json
import logging
import os
import time
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Circuit Breaker — per-source tracking
# ---------------------------------------------------------------------------
_breaker_lock = threading.Lock()
_breaker_state: Dict[str, Dict[str, Any]] = {}
# Config
BREAKER_FAILURE_THRESHOLD = 5    # Consecutive failures to open circuit
BREAKER_RECOVERY_TIMEOUT = 300   # 5 min before half-open retry
BREAKER_SUCCESS_RESET = 2        # Successes in half-open to close circuit


def _get_breaker(source: str) -> Dict[str, Any]:
    """Get or create circuit breaker state for a source."""
    with _breaker_lock:
        if source not in _breaker_state:
            _breaker_state[source] = {
                'state': 'closed',           # closed | open | half_open
                'consecutive_failures': 0,
                'consecutive_successes': 0,
                'last_failure_time': 0,
                'total_failures': 0,
                'total_successes': 0,
                'opened_at': 0,
            }
        return _breaker_state[source]


def reset_breaker(source: str) -> None:
    """Manually reset a circuit breaker to closed state."""
    with _breaker_lock:
        if source in _breaker_state:
            _breaker_state[source] = {
                'state': 'closed',
                'consecutive_failures': 0,
                'consecutive_successes': 0,
                'last_failure_time': 0,
                'total_failures': 0,
                'total_successes': 0,
                'opened_at': 0,
            }
    _mark_healthy(source)


def _record_success(source: str) -> None:
    breaker = _get_breaker(source)
    with _breaker_lock:
        breaker['consecutive_failures'] = 0
        breaker['consecutive_successes'] += 1
        breaker['total_successes'] += 1
        if breaker['state'] == 'half_open' and breaker['consecutive_successes'] >= BREAKER_SUCCESS_RESET:
            breaker['state'] = 'closed'
            logger.info(f"[CircuitBreaker] {source}: CLOSED (recovered)")


def _record_failure(source: str) -> None:
    breaker = _get_breaker(source)
    with _breaker_lock:
        breaker['consecutive_failures'] += 1
        breaker['consecutive_successes'] = 0
        breaker['total_failures'] += 1
        breaker['last_failure_time'] = time.time()
        if breaker['state'] == 'closed' and breaker['consecutive_failures'] >= BREAKER_FAILURE_THRESHOLD:
            breaker['state'] = 'open'
            breaker['opened_at'] = time.time()
            logger.warning(f"[CircuitBreaker] {source}: OPEN after {breaker['consecutive_failures']} consecutive failures — pausing calls for {BREAKER_RECOVERY_TIMEOUT}s")
        elif breaker['state'] == 'half_open':
            breaker['state'] = 'open'
            breaker['opened_at'] = time.time()
            logger.warning(f"[CircuitBreaker] {source}: re-OPENED (half-open probe failed)")


def _is_allowed(source: str) -> bool:
    """Check if a request to this source is allowed by the circuit breaker."""
    breaker = _get_breaker(source)
    with _breaker_lock:
        if breaker['state'] == 'closed':
            return True
        if breaker['state'] == 'open':
            elapsed = time.time() - breaker['opened_at']
            if elapsed >= BREAKER_RECOVERY_TIMEOUT:
                breaker['state'] = 'half_open'
                breaker['consecutive_successes'] = 0
                logger.info(f"[CircuitBreaker] {source}: HALF-OPEN (probing)")
                return True
            return False
        # half_open — allow (probe request)
        return True


# ---------------------------------------------------------------------------
# Degradation Tracking — visible to dashboard
# ---------------------------------------------------------------------------
_degradation_lock = threading.Lock()
_degraded_sources: Dict[str, Dict[str, Any]] = {}


def _mark_degraded(source: str, reason: str) -> None:
    with _degradation_lock:
        already = source in _degraded_sources
        _degraded_sources[source] = {
            'reason': reason,
            'since': _degraded_sources.get(source, {}).get('since', time.time()),
            'last_updated': time.time(),
        }
        if not already:
            logger.warning(f"[API_DEGRADED] {source}: {reason} — data may be stale or using fallback defaults")


def _mark_healthy(source: str) -> None:
    with _degradation_lock:
        if source in _degraded_sources:
            del _degraded_sources[source]
            logger.info(f"[API_RECOVERED] {source}: back online")


def get_degraded_sources() -> Dict[str, Dict[str, Any]]:
    """Return current degraded API sources. Used by dashboard/monitoring."""
    with _degradation_lock:
        return dict(_degraded_sources)


def api_health_report() -> Dict[str, Dict[str, Any]]:
    """Return health status for all tracked API sources, including credit budgets."""
    report = {}
    with _breaker_lock:
        for source, breaker in _breaker_state.items():
            report[source] = {
                'circuit': breaker['state'],
                'consecutive_failures': breaker['consecutive_failures'],
                'total_failures': breaker['total_failures'],
                'total_successes': breaker['total_successes'],
            }
    with _degradation_lock:
        for source, info in _degraded_sources.items():
            if source not in report:
                report[source] = {}
            report[source]['degraded'] = True
            report[source]['degraded_reason'] = info['reason']
            report[source]['degraded_since'] = info['since']
    # Merge credit budget data
    for source, budget in get_credit_budget_report().items():
        if source not in report:
            report[source] = {}
        report[source]['credit_budget'] = budget
    return report


# ---------------------------------------------------------------------------
# Credit Budget Tracker — per-source monthly credit limits
# ---------------------------------------------------------------------------
_budget_lock = threading.RLock()  # Reentrant: _get_budget -> _load_budget both acquire
_budget_state: Dict[str, Dict[str, Any]] = {}
_budget_loaded = False
_BUDGET_FILE = Path(os.getenv('CREDIT_BUDGET_FILE', Path(__file__).resolve().parent / 'data' / 'coingecko_credits.json'))

_DEFAULT_BUDGETS: Dict[str, Dict[str, Any]] = {
    'coingecko': {'monthly_limit': 10000, 'threshold_pct': 70},
}


def _load_budget() -> None:
    """Load persisted credit counts from disk once."""
    global _budget_loaded
    with _budget_lock:
        if _budget_loaded:
            return
        _budget_loaded = True
        try:
            if _BUDGET_FILE.is_file():
                raw = json.loads(_BUDGET_FILE.read_text(encoding='utf-8'))
                if isinstance(raw, dict):
                    _budget_state.update(raw)
        except Exception:
            pass


def _save_budget() -> None:
    """Persist current credit counts to disk."""
    try:
        _BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _budget_lock:
            snapshot = dict(_budget_state)
        _BUDGET_FILE.write_text(json.dumps(snapshot, indent=2) + '\n', encoding='utf-8')
    except Exception:
        pass


def _get_budget(source: str) -> Optional[Dict[str, Any]]:
    """Get budget state for a source, with monthly auto-reset."""
    if source not in _DEFAULT_BUDGETS:
        return None
    _load_budget()
    now_month = datetime.now(timezone.utc).strftime('%Y-%m')
    with _budget_lock:
        if source not in _budget_state or _budget_state[source].get('month') != now_month:
            _budget_state[source] = {
                'month': now_month,
                'credits_used': 0,
                'monthly_limit': _DEFAULT_BUDGETS[source]['monthly_limit'],
                'threshold_pct': _DEFAULT_BUDGETS[source]['threshold_pct'],
                'last_updated': time.time(),
            }
            _save_budget()
        return _budget_state[source]


def record_credit(source: str) -> None:
    """Record one credit used for a source."""
    budget = _get_budget(source)
    if budget is None:
        return
    with _budget_lock:
        budget['credits_used'] += 1
        budget['last_updated'] = time.time()
    _save_budget()


def is_over_budget(source: str) -> bool:
    """True if source has exceeded its threshold_pct of monthly credits."""
    budget = _get_budget(source)
    if budget is None:
        return False
    with _budget_lock:
        limit = budget.get('monthly_limit', 10000)
        threshold = budget.get('threshold_pct', 70) / 100.0
        return budget.get('credits_used', 0) >= limit * threshold


def get_credit_budget_report() -> Dict[str, Dict[str, Any]]:
    """Return credit budget status for all tracked sources."""
    _load_budget()
    report = {}
    now_month = datetime.now(timezone.utc).strftime('%Y-%m')
    with _budget_lock:
        for source, defaults in _DEFAULT_BUDGETS.items():
            state = _budget_state.get(source, {})
            if state.get('month') != now_month:
                state = {'month': now_month, 'credits_used': 0,
                         'monthly_limit': defaults['monthly_limit'],
                         'threshold_pct': defaults['threshold_pct']}
            limit = state.get('monthly_limit', defaults['monthly_limit'])
            used = state.get('credits_used', 0)
            threshold_pct = state.get('threshold_pct', defaults['threshold_pct'])
            report[source] = {
                'month': state.get('month', now_month),
                'credits_used': used,
                'monthly_limit': limit,
                'credits_remaining': max(0, limit - used),
                'usage_pct': round(used / limit * 100, 1) if limit > 0 else 0,
                'threshold_pct': threshold_pct,
                'over_budget': used >= limit * (threshold_pct / 100.0),
                'auto_fallback_active': used >= limit * (threshold_pct / 100.0),
            }
    return report


class CreditBudgetExceeded(Exception):
    """Raised when a source's credit budget threshold is exceeded."""
    pass


# ---------------------------------------------------------------------------
# Per-source rate limiting — minimum gap between requests to the same source
# ---------------------------------------------------------------------------
# Bug 7 — thread-safe per-source rate limiting to prevent concurrent threads
# from collapsing the inter-request sleep.
_RATE_LOCK = threading.Lock()
_last_request_time: Dict[str, float] = {}

# Minimum seconds between consecutive requests to the same source.
# Sources not listed here default to 0 (no enforced gap).
_SOURCE_MIN_INTERVAL: Dict[str, float] = {
    'coingecko': 0.5,
    'coincap': 0.5,
    'coinpaprika': 0.3,
    'alternative_me': 1.0,
}


def _rate_limit_wait(source: str) -> None:
    """Block the calling thread if needed to honour the per-source minimum interval."""
    min_interval = _SOURCE_MIN_INTERVAL.get(source, 0.0)
    if min_interval <= 0:
        return
    with _RATE_LOCK:
        last = _last_request_time.get(source, 0.0)
        now = time.time()
        wait = min_interval - (now - last)
        if wait > 0:
            time.sleep(wait)
        _last_request_time[source] = time.time()


# ---------------------------------------------------------------------------
# Resilient GET — drop-in replacement for requests.get()
# ---------------------------------------------------------------------------
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _assert_json_content_type(resp: requests.Response, source: str) -> None:
    """
    Bug 8 — raise before callers call .json() on non-JSON responses (e.g. HTML error pages).
    Surfaces the real error rather than a misleading JSONDecodeError.
    Only fires on 2xx responses — non-2xx status codes are handled by raise_for_status() elsewhere.
    """
    if not resp.ok:
        return  # Let raise_for_status() handle non-2xx
    content_type = resp.headers.get('content-type', '') or ''
    # Only raise when we have an explicit non-JSON content-type string — skip when
    # content-type is absent (empty string) or not a str (e.g. unit-test mocks).
    if not isinstance(content_type, str) or not content_type.strip():
        return
    if 'application/json' not in content_type and 'text/json' not in content_type:
        # A 200 OK with non-JSON body (e.g. HTML rate-limit page) — raise so the retry
        # loop treats it as a failure rather than letting .json() blow up downstream.
        raise requests.exceptions.ContentDecodingError(
            f"[{source}] Expected application/json but got '{content_type}' "
            f"(status {resp.status_code}) — raw body preview: {resp.text[:200]!r}"
        )


def resilient_get(
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 10,
    source: str = "unknown",
    max_retries: int = 3,
    base_backoff: float = 0.5,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """
    requests.get() with retry, exponential backoff, and circuit breaker.

    Args:
        url: Request URL
        params: Query parameters
        headers: HTTP headers
        timeout: Request timeout in seconds
        source: API source name (for circuit breaker + degradation tracking)
        max_retries: Max retry attempts (default 3)
        base_backoff: Base backoff in seconds (doubles each retry)
        session: Optional requests.Session (for auth headers etc.)

    Returns:
        requests.Response on success

    Raises:
        requests.exceptions.RequestException: After all retries exhausted
        CircuitOpenError: If circuit breaker is open for this source
    """
    # Credit budget gate — skip expensive source when over threshold
    if is_over_budget(source):
        _mark_degraded(source, f"credit budget threshold reached — auto-fallback active")
        raise CreditBudgetExceeded(f"Credit budget exceeded for {source} — use fallback provider")

    if not _is_allowed(source):
        _mark_degraded(source, f"circuit breaker open — paused for {BREAKER_RECOVERY_TIMEOUT}s")
        raise CircuitOpenError(f"Circuit breaker OPEN for {source} — skipping request")

    # Bug 7 — enforce per-source minimum interval before proceeding
    _rate_limit_wait(source)

    do_get = session.get if session else requests.get
    last_exc = None

    # Bug 6 — use (connect_timeout, read_timeout) tuple to prevent hung TCP handshakes
    effective_timeout = (5, timeout)

    for attempt in range(max_retries):
        try:
            resp = do_get(url, params=params, headers=headers, timeout=effective_timeout)

            # Transient server errors — retry with backoff
            if resp.status_code in _TRANSIENT_STATUS_CODES:
                wait = base_backoff * (2 ** attempt)
                logger.debug(f"[resilient_http] {source} returned {resp.status_code}, retry {attempt+1}/{max_retries} in {wait:.1f}s")
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
                # Last attempt — fall through to failure
                _record_failure(source)
                _mark_degraded(source, f"HTTP {resp.status_code} after {max_retries} retries")
                resp.raise_for_status()

            # Bug 8 — validate Content-Type before handing back to callers that call .json()
            _assert_json_content_type(resp, source)

            # Success — record credit usage for budgeted sources
            _record_success(source)
            _mark_healthy(source)
            record_credit(source)
            return resp

        except CircuitOpenError:
            raise
        except requests.exceptions.RequestException as e:
            last_exc = e
            wait = base_backoff * (2 ** attempt)
            logger.debug(f"[resilient_http] {source} error: {e}, retry {attempt+1}/{max_retries} in {wait:.1f}s")
            if attempt < max_retries - 1:
                time.sleep(wait)
            else:
                _record_failure(source)
                _mark_degraded(source, f"{type(e).__name__}: {e}")

    # All retries exhausted
    raise last_exc or requests.exceptions.ConnectionError(f"All {max_retries} retries failed for {source}")


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and the request is blocked."""
    pass
