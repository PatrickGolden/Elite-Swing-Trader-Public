# tests/unit/test_credit_budget.py — CoinGecko credit budget tracker tests
# Run: pytest tests/unit/test_credit_budget.py -v

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def clean_budget_state(tmp_path, monkeypatch):
    """Reset budget state between tests and redirect file to temp."""
    import resilient_http as rh
    monkeypatch.setattr(rh, '_BUDGET_FILE', tmp_path / 'coingecko_credits.json')
    with rh._budget_lock:
        rh._budget_state.clear()
        rh._budget_loaded = False
    yield
    with rh._budget_lock:
        rh._budget_state.clear()
        rh._budget_loaded = False


class TestCreditBudgetTracker:
    def test_initial_state_zero(self):
        from resilient_http import get_credit_budget_report
        report = get_credit_budget_report()
        cg = report['coingecko']
        assert cg['credits_used'] == 0
        assert cg['monthly_limit'] == 10000
        assert cg['over_budget'] is False

    def test_record_credits(self):
        from resilient_http import record_credit, get_credit_budget_report
        for _ in range(50):
            record_credit('coingecko')
        report = get_credit_budget_report()
        assert report['coingecko']['credits_used'] == 50
        assert report['coingecko']['credits_remaining'] == 9950

    def test_not_over_budget_below_threshold(self):
        from resilient_http import record_credit, is_over_budget
        for _ in range(100):
            record_credit('coingecko')
        assert is_over_budget('coingecko') is False

    def test_over_budget_at_threshold(self):
        import resilient_http as rh
        with rh._budget_lock:
            budget = rh._get_budget('coingecko')
            budget['credits_used'] = 7000  # 70% of 10000
        assert rh.is_over_budget('coingecko') is True

    def test_not_over_budget_just_below_threshold(self):
        import resilient_http as rh
        with rh._budget_lock:
            budget = rh._get_budget('coingecko')
            budget['credits_used'] = 6999
        assert rh.is_over_budget('coingecko') is False

    def test_unknown_source_not_budgeted(self):
        from resilient_http import is_over_budget, record_credit
        assert is_over_budget('unknown_source') is False
        record_credit('unknown_source')  # should not raise

    def test_credit_budget_exceeded_raised(self):
        import resilient_http as rh
        with rh._budget_lock:
            budget = rh._get_budget('coingecko')
            budget['credits_used'] = 8000
        with pytest.raises(rh.CreditBudgetExceeded):
            rh.resilient_get(
                'https://api.coingecko.com/api/v3/ping',
                source='coingecko', timeout=5,
            )

    def test_non_coingecko_source_not_affected(self):
        """Non-budgeted sources should pass through without budget check."""
        import resilient_http as rh
        with rh._budget_lock:
            budget = rh._get_budget('coingecko')
            budget['credits_used'] = 9999
        assert rh.is_over_budget('coinpaprika') is False

    def test_monthly_reset(self):
        import resilient_http as rh
        with rh._budget_lock:
            rh._budget_state['coingecko'] = {
                'month': '2025-01',  # old month
                'credits_used': 9000,
                'monthly_limit': 10000,
                'threshold_pct': 70,
                'last_updated': 0,
            }
        budget = rh._get_budget('coingecko')
        assert budget['credits_used'] == 0  # reset for new month

    def test_usage_pct_in_report(self):
        import resilient_http as rh
        with rh._budget_lock:
            budget = rh._get_budget('coingecko')
            budget['credits_used'] = 5000
        report = rh.get_credit_budget_report()
        assert report['coingecko']['usage_pct'] == 50.0

    def test_health_report_includes_budget(self):
        from resilient_http import api_health_report, record_credit
        record_credit('coingecko')
        report = api_health_report()
        assert 'coingecko' in report
        assert 'credit_budget' in report['coingecko']
        assert report['coingecko']['credit_budget']['credits_used'] >= 1
