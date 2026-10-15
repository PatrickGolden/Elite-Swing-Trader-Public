# tests/conftest.py — Shared fixtures, isolation, and mocks
# Run pytest from project root (c:\swing-trader)
# All execution tests use mocks — no real Coinbase/CCXT calls

import gc
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup (runs before any test imports)
# ---------------------------------------------------------------------------

os.environ["TESTING"] = "1"  # Prevent app.py background threads
os.environ["ALERT_EMAIL_TO"] = "owner@example.com"
os.environ["ALERT_PRIMARY_EMAIL"] = "owner@example.com"

# Mock litellm before any brain.consensus import (litellm can't build on Python 3.14)
if 'litellm' not in sys.modules:
    _mock_litellm = ModuleType('litellm')
    _mock_litellm.acompletion = AsyncMock()
    _mock_exceptions = ModuleType('litellm.exceptions')
    _mock_exceptions.RateLimitError = type('RateLimitError', (Exception,), {})
    _mock_exceptions.APIError = type('APIError', (Exception,), {})
    _mock_exceptions.Timeout = type('Timeout', (Exception,), {})
    _mock_litellm.exceptions = _mock_exceptions
    sys.modules['litellm'] = _mock_litellm
    sys.modules['litellm.exceptions'] = _mock_exceptions

# Ensure project root is on path for absolute imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# AUTOUSE ISOLATION FIXTURES — run for EVERY test to prevent state bleed
# =============================================================================

@pytest.fixture(autouse=True)
def _no_production_writes():
    """Fail any test that tried to write live state (data/ or root-level *.json[l]).

    safe_json.check_test_write REFUSES such writes during a test, but most writers are
    best-effort and swallow the exception, so the refusal alone would be silent. This
    turns it into a test failure naming the file. Added 2026-09-26 after finding the
    integration suite had been appending fixture rows to production
    refresh_snapshots.jsonl and overwriting coins_cache.json on every full run.
    """
    import safe_json
    start = len(safe_json.TEST_WRITE_VIOLATIONS)
    yield
    new = safe_json.TEST_WRITE_VIOLATIONS[start:]
    if new:
        paths = sorted({p for _, p in new})
        pytest.fail("test wrote (or tried to write) PRODUCTION state — redirect to tmp_path: "
                    + ", ".join(paths), pytrace=False)


@pytest.fixture(autouse=True)
def _isolate_admin_api_token_from_dotenv(monkeypatch):
    """Clear ADMIN_API_TOKEN loaded from .env so guarded POST tests use localhost fallback.

    Tests that assert token-mode behavior call monkeypatch.setenv("ADMIN_API_TOKEN", ...)
    after this fixture runs.

    Set to EMPTY, not deleted: load_dotenv() (override=False) only fills variables that
    are MISSING, so after a delenv the first `import app` in an xdist worker re-read the
    real token from .env mid-test and guarded POSTs returned 403 (3 integration tests,
    consistent under -n auto, passing serially). An empty value is "present", so dotenv
    leaves it alone, and the guard strips it to "" → localhost fallback, as intended.
    """
    monkeypatch.setenv("ADMIN_API_TOKEN", "")


@pytest.fixture(autouse=True)
def temp_execution_log(monkeypatch, tmp_path):
    """Redirect execution log to temp file."""
    import execution_engine as ee
    log_path = tmp_path / "execution_log.jsonl"
    log_path.touch()
    monkeypatch.setattr(ee, "EXECUTION_LOG_PATH", str(log_path))
    yield str(log_path)


@pytest.fixture(autouse=True)
def temp_positions(tmp_path, monkeypatch):
    """Redirect positions.json to temp file. Closes open positions on teardown."""
    path = tmp_path / "positions_test.json"
    path.write_text("[]", encoding="utf-8")
    path_str = str(path)

    import trade_journal as tj
    monkeypatch.setattr(tj, "POSITIONS_FILE", path_str)
    seq_path = tmp_path / "journal_sequences_test.json"
    monkeypatch.setattr(tj, "JOURNAL_SEQUENCES_FILE", str(seq_path))
    yield path_str
    try:
        from trade_journal import get_open_positions, close_position
        for p in get_open_positions():
            close_position(
                p['id'],
                exit_price=float(p.get('entry_price', 0) or 0),
                exit_reason='test_cleanup',
                notes='Fixture teardown',
            )
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_app_globals(monkeypatch):
    """Reset app.py globals ONLY if app was imported. Most tests skip this."""
    if 'app' not in sys.modules:
        yield
        return

    app_mod = sys.modules['app']
    monkeypatch.setattr(app_mod, "cached_data", {})
    monkeypatch.setattr(app_mod, "last_updated", None)
    monkeypatch.setattr(app_mod, "refresh_count", 0)
    monkeypatch.setattr(app_mod, "current_regime", None)

    _cancel_scheduler(app_mod)
    yield
    _cancel_scheduler(app_mod)


@pytest.fixture(autouse=True)
def _isolate_fetcher_disk_cache(monkeypatch):
    """Prevent disk cache from contaminating tests with live app data."""
    import enhanced_data_fetcher as edf
    monkeypatch.setattr(edf.EnhancedDataFetcherV2, "_load_disk_cache", lambda self: None)
    yield


@pytest.fixture(autouse=True)
def _reset_resilient_http_state():
    """Reset circuit breakers between tests so full-suite runs don't block HTTP mocks."""
    import resilient_http as rh
    with rh._breaker_lock:
        rh._breaker_state.clear()
    with rh._degradation_lock:
        rh._degraded_sources.clear()
    yield
    with rh._breaker_lock:
        rh._breaker_state.clear()
    with rh._degradation_lock:
        rh._degraded_sources.clear()


@pytest.fixture(autouse=True)
def _isolate_threshold_cache(monkeypatch, tmp_path):
    """Isolate thresholds from production live_thresholds.json during tests.

    Redirects LIVE_THRESHOLDS_PATH to a nonexistent tmp file so tests always
    get DEFAULT_THRESHOLDS via the fallback path — no Windows file contention
    with the main app writing to the real file.
    """
    import thresholds as th_mod
    monkeypatch.setattr(th_mod, "LIVE_THRESHOLDS_PATH", str(tmp_path / "live_thresholds.json"))
    th_mod._loaded_once = False
    yield
    th_mod._loaded_once = False


@pytest.fixture(autouse=True)
def _no_regression_model_on_disk(tmp_path):
    """Point regression_scorer MODEL_PATH and META_PATH at non-existent files.

    Prevents tests from picking up a real trained model from data/ and producing
    different signal tiers depending on whether training was run locally.
    Tests that explicitly patch get_regression_score or MODEL_PATH are unaffected.
    """
    from unittest.mock import patch
    fake_model = tmp_path / 'regression_model.pkl'
    fake_meta = tmp_path / 'regression_model_meta.json'
    with patch('regression_scorer.MODEL_PATH', fake_model), \
         patch('regression_scorer.META_PATH', fake_meta), \
         patch('shap_analysis.MODEL_PATH', fake_model), \
         patch('shap_analysis.META_PATH', fake_meta):
        yield


@pytest.fixture(autouse=True)
def _isolate_execution_engine(monkeypatch):
    """Reset execution_engine callbacks between tests."""
    import execution_engine as ee
    monkeypatch.setattr(ee, "_on_twap_done_sync_balance", None)
    monkeypatch.setattr(ee, "_on_twap_done_open_position_and_place_orders", None)
    yield


@pytest.fixture(autouse=True)
def _isolate_alert_state(monkeypatch, tmp_path):
    """Redirect alert files to temp + reset dedup cache."""
    import alert_system as als
    monkeypatch.setattr(als, "ALERT_TRACKING_FILE", tmp_path / "alert_history.json")
    monkeypatch.setattr(als, "USER_SETTINGS_FILE", tmp_path / "user_alert_settings.json")
    monkeypatch.setattr(als, "_critical_alert_recent", {})
    yield


@pytest.fixture(autouse=True)
def _isolate_evidence_files(monkeypatch, tmp_path):
    """Redirect every forensic/evidence file to temp.

    These are the append-only files the strategy reasons FROM — the audit trail
    STRATEGY.md §1 cites for measured fill costs, the veto log, the stop telemetry,
    the paper-evidence report. Unlike positions.json (already isolated above), they
    were never redirected, so every full-suite run appended fixture rows to live
    production evidence.

    Cost of the gap (measured 2026-08-20): 1791 of 2088 veto_log.jsonl entries and
    222 of 482 dynamic_stop_telemetry.jsonl entries were test-generated. Two
    conclusions in tasks/lessons.md ("veto specialist may be broken" at 89.6%
    suspicious; "76.6% dynamic stop reprice failure rate") were computed from those
    commingled files and are not trustworthy.

    risk_regime is the sharp edge: compute_btc_risk_on() writes its result to disk
    unconditionally, and a mocked risk_on=True carries the 12h success TTL (not the
    15min failure TTL). An unpatched test could therefore leave a fabricated
    risk-on verdict in the live cache long enough for rotation_tracker to open a
    paper BTC cohort position on it — a fake entry in the track record that
    STRATEGY.md D5 defines as the project deliverable.

    Deliberately uses plain setattr (no raising=False): if one of these constants is
    renamed, this fixture must fail loudly rather than silently stop isolating.
    """
    import execution_engine as ee
    import veto_specialist as vs
    import risk_regime as rr
    import monitoring as mon
    import paper_evidence as pe

    monkeypatch.setattr(ee, "_AUDIT_LOG_FILE", str(tmp_path / "execution_audit.jsonl"))
    monkeypatch.setattr(ee, "_STOP_TELEMETRY_FILE", str(tmp_path / "dynamic_stop_telemetry.jsonl"))
    monkeypatch.setattr(ee, "SKIPPED_INSUFFICIENT_CASH_LOG", str(tmp_path / "skipped_insufficient_cash.jsonl"))
    monkeypatch.setattr(vs, "VETO_LOG_FILE", tmp_path / "veto_log.jsonl")
    monkeypatch.setattr(mon, "EXPECTANCY_FILE", tmp_path / "monitoring_expectancy.json")
    monkeypatch.setattr(pe, "PAPER_EVIDENCE_REPORT_FILE", tmp_path / "paper_evidence_report.json")

    # Disk path AND the module-level memo cache — the memo would otherwise carry a
    # mocked verdict between tests within a process.
    monkeypatch.setattr(rr, "_CACHE_PATH", str(tmp_path / "btc_risk_on_cache.json"))
    monkeypatch.setattr(rr, "_mem", {"ts": 0.0, "val": None})
    yield
    monkeypatch.setattr(rr, "_mem", {"ts": 0.0, "val": None})


@pytest.fixture(autouse=True)
def _isolate_refresh_state_files(monkeypatch, tmp_path, request):
    """Redirect every state file the refresh / journal / execution paths write.

    Found 2026-09-26 by the _no_production_writes tripwire: ~98 tests were writing live
    state on every full run — model_performance.json (~90 tests), pending_orders.json and
    sold_pending_close.json (execution state), last_auto_sync_at.json, and via the
    refresh-path integration test: refresh_snapshots.jsonl ("SIDEWAYS"/1-coin fixture
    rows), screening_log, signal_history, source_status, coins_cache, spread_snapshots.
    Same class as _isolate_evidence_files (2026-08-20); the tripwire now catches the
    NEXT one instead of waiting for someone to notice polluted data.

    Plain setattr (no raising=False): a renamed constant must fail loudly here.
    """
    import model_performance_tracker as mpt
    import execution_engine as ee
    import refresh_snapshot as rs
    import signal_history as sh
    import screening_log as sl
    import spread_logger as spl
    import market_state_logger as msl
    import enhanced_data_fetcher as edf
    from ml import config as mlc

    monkeypatch.setattr(mpt, "PERF_FILE", str(tmp_path / "model_performance.json"))
    monkeypatch.setattr(ee, "_PENDING_ORDERS_FILE", str(tmp_path / "pending_orders.json"))
    monkeypatch.setattr(rs, "SNAPSHOTS_FILE", str(tmp_path / "refresh_snapshots.jsonl"))
    monkeypatch.setattr(sh, "HISTORY_FILE", str(tmp_path / "signal_history.json"))
    monkeypatch.setattr(sl, "SCREENING_LOG_FILE", str(tmp_path / "screening_log.json"))
    monkeypatch.setattr(mlc, "SCREENING_LOG_FILE", tmp_path / "screening_log.json")
    monkeypatch.setattr(spl, "SPREAD_FILE", str(tmp_path / "spread_snapshots.jsonl"))
    monkeypatch.setattr(msl, "MARKET_STATE_FILE", str(tmp_path / "market_state.jsonl"))
    monkeypatch.setattr(msl, "MARKET_DAILY_DIR", str(tmp_path / "market_daily"))
    monkeypatch.setattr(msl, "ETH_BTC_CACHE", str(tmp_path / "eth_btc_cache.json"))
    # No network from the refresh path's environment feature during tests.
    monkeypatch.setattr(msl, "_eth_btc_live", lambda: msl.compute_eth_btc([], []))
    monkeypatch.setattr(edf, "COINS_CACHE_FILE", str(tmp_path / "coins_cache.json"))
    # app-level paths. Integration/execution tests import app themselves (inside a fixture
    # or the test body — AFTER this runs), so the redirect would miss the first such test
    # in each xdist worker; import it here for those folders only. Unit tests keep the lazy
    # pattern (.claude/rules/testing.md) and are covered once app is already loaded.
    node_path = str(request.node.fspath)
    if "app" in sys.modules or any(seg in node_path for seg in ("integration", "execution")):
        import app as app_mod
        monkeypatch.setattr(app_mod, "LAST_AUTO_SYNC_FILE", str(tmp_path / "last_auto_sync_at.json"))
        monkeypatch.setattr(app_mod, "SYNC_FAILURE_COUNT_FILE", str(tmp_path / "sync_failure_count.json"))
        monkeypatch.setattr(app_mod, "_SOLD_PENDING_CLOSE_FILE", str(tmp_path / "sold_pending_close.json"))
        monkeypatch.setattr(app_mod, "SOURCE_STATUS_FILE", str(tmp_path / "source_status.json"))
    yield


def _cancel_scheduler(app_mod):
    """Cancel any running scheduler timer to prevent thread leaks."""
    try:
        sched = getattr(app_mod, "scheduler", None)
        if sched and getattr(sched, "timer", None):
            sched.timer.cancel()
            sched.timer = None
        if sched:
            sched.is_running = False
    except Exception:
        pass


def pytest_runtest_teardown(item, nextitem):
    """GC after every test. Cheap (~1ms), prevents memory accumulation."""
    gc.collect()


# =============================================================================
# CCXT / execution_engine mocks (opt-in, no real exchange calls)
# =============================================================================

@pytest.fixture
def mock_ccxt_exchange():
    """Mock CCXT exchange instance — no real API calls."""
    from tests.factories import coinbase_balance_v3_single_page

    ex = MagicMock()
    ex.id = "coinbase"
    ex.fetch_balance.return_value = coinbase_balance_v3_single_page()
    ex.fetch_ticker.return_value = {"last": 45000, "close": 45000}
    ex.create_market_buy_order.return_value = {
        "id": "mock-order-123",
        "status": "closed",
        "filled": 0.01,
        "average": 45000,
    }
    ex.create_market_sell_order.return_value = {
        "id": "mock-sell-456",
        "status": "closed",
        "filled": 0.01,
        "average": 44000,
    }
    return ex


@pytest.fixture
def mock_get_coinbase_exchange(mock_ccxt_exchange):
    """Patch get_coinbase_exchange to return mock — no real keys needed."""
    with patch("execution_engine.get_coinbase_exchange", return_value=mock_ccxt_exchange):
        yield mock_ccxt_exchange


@pytest.fixture
def mock_fetch_balance():
    """Patch fetch_balance_sync to return mock data — no real API calls."""
    with patch("execution_engine.fetch_balance_sync") as m:
        m.return_value = {
            "total_cash": 8000.0,
            "holdings": {"BTC": 0.01},
            "exchange": "coinbase",
        }
        yield m


# =============================================================================
# Flask app and test client
# =============================================================================

@pytest.fixture
def app():
    """Create Flask app for testing."""
    try:
        from app import app as flask_app
        flask_app.config['TESTING'] = True
        return flask_app
    except Exception:
        pytest.skip("Flask app not available (missing deps or env)")


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# =============================================================================
# Trailing stop / alert mocks (opt-in)
# =============================================================================

@pytest.fixture
def mock_execute_market_sell_twap():
    """Patch execute_market_sell_twap — no real Coinbase TWAP sell."""
    with patch("execution_engine.execute_market_sell_twap") as m:
        m.return_value = {"success": True, "fill_price": 105.0, "average_price": 105.0, "total_filled": 1.0}
        yield m


@pytest.fixture
def mock_check_trailing_stops():
    """Patch exit_manager.check_trailing_stops — no real monitoring."""
    with patch("exit_manager.check_trailing_stops") as m:
        yield m


@pytest.fixture
def mock_send_critical_alert():
    """Patch alerts.send_critical_alert — no real email/Telegram."""
    with patch("alert_system.AlertSystem.send_critical_alert") as m:
        yield m


# =============================================================================
# Synthetic data fixtures (opt-in, not autouse)
# =============================================================================

@pytest.fixture(scope="function")
def synthetic_trade_journal_data():
    """Bounded synthetic CLOSED trades (deterministic, CI-sized)."""
    from tests.factories import create_synthetic_closed_trades
    data = create_synthetic_closed_trades(count=500, seed=42)
    yield data
    del data
    gc.collect()


@pytest.fixture(scope="function")
def synthetic_stress_market_data():
    """Compact per-day regime rows for stress/regime tests."""
    from tests.factories import generate_stress_market_regimes
    data = generate_stress_market_regimes(days=90, volatility=True, seed=42)
    yield data
    del data
    gc.collect()


@pytest.fixture
def sample_positions():
    """Sample position data for tests."""
    return [
        {
            'id': 1,
            'status': 'OPEN',
            'coin_symbol': 'BTC',
            'entry_price': 45000.0,
            'quantity': 0.01,
            'capital_usd': 450.0,
            'ai_target_pct': 6.0,
            'ai_target_price': 47700.0,
            'ai_stop_pct': 2.0,
            'ai_stop_price': 44100.0,
        },
        {
            'id': 2,
            'status': 'CLOSED',
            'coin_symbol': 'ETH',
            'entry_price': 2500.0,
            'quantity': 0.5,
            'exit_price': 2700.0,
            'pnl_usd': 100.0,
            'pnl_pct': 8.0,
            'outcome': 'win',
            'hit_ai_target': True,
            'market_regime': 'BULL',
        },
    ]
