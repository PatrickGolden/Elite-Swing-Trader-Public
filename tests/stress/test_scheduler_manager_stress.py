# tests/test_scheduler_manager_stress.py — Stress-style tests for scheduler_manager
# Rapid set_interval, cost formulas, start/stop idempotency. Run: pytest tests/test_scheduler_manager_stress.py -v

import os
import sys
from pathlib import Path
from datetime import datetime

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scheduler_manager import SchedulerManager



@pytest.fixture
def scheduler_temp_config(tmp_path):
    """SchedulerManager with a temp config file so tests don't touch real config."""
    path = tmp_path / "scheduler_config.json"
    path.write_text("{}")
    sm = SchedulerManager(config_file=str(path))
    try:
        yield sm
    finally:
        try:
            sm.stop()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Stress: rapid set_interval and start/stop
# -----------------------------------------------------------------------------

class TestSchedulerManagerRapidChanges:
    """Rapid interval changes and start/stop do not leave inconsistent state."""

    def test_set_interval_rapid_off_5min_off(self, scheduler_temp_config):
        """Rapid set_interval: off -> 5min -> off leaves scheduler stopped."""
        sm = scheduler_temp_config
        for _ in range(5):
            sm.set_interval("5min")
            sm.set_interval("off")
        assert sm.is_running is False
        assert sm.current_interval == "off"
        assert sm.timer is None

    def test_start_stop_idempotent(self, scheduler_temp_config):
        """Multiple start() then stop() calls are idempotent."""
        sm = scheduler_temp_config
        sm.set_interval("15min")
        sm.start()
        sm.start()
        sm.start()
        assert sm.is_running is True
        sm.stop()
        sm.stop()
        sm.stop()
        assert sm.is_running is False
        assert sm.timer is None

    def test_stop_when_off_safe(self, scheduler_temp_config):
        """stop() when already off is safe and leaves is_running False."""
        sm = scheduler_temp_config
        sm.set_interval("off")
        assert sm.current_interval == "off"
        sm.stop()
        sm.stop()
        assert sm.is_running is False


# -----------------------------------------------------------------------------
# Cost formulas: per-interval refreshes_per_month and monthly cost
# -----------------------------------------------------------------------------

class TestSchedulerManagerCostFormulas:
    """Cost calculation: INTERVALS per_month and API_COSTS applied correctly."""

    def test_off_interval_zero_refreshes(self, scheduler_temp_config):
        """Interval 'off' has 0 refreshes per month."""
        sm = scheduler_temp_config
        costs = sm.calculate_costs("off")
        assert costs.get("refreshes_per_month") == 0
        assert costs.get("total_monthly") == 0

    def test_5min_refreshes_per_month(self, scheduler_temp_config):
        """5min: 8640 refreshes/month (24*60/5 * 30)."""
        sm = scheduler_temp_config
        costs = sm.calculate_costs("5min")
        assert costs["refreshes_per_month"] == 8640

    def test_1hour_refreshes_per_month(self, scheduler_temp_config):
        """1hour: 720 refreshes/month."""
        sm = scheduler_temp_config
        costs = sm.calculate_costs("1hour")
        assert costs["refreshes_per_month"] == 720

    def test_daily_refreshes_per_month(self, scheduler_temp_config):
        """daily: 30 refreshes/month."""
        sm = scheduler_temp_config
        costs = sm.calculate_costs("daily")
        assert costs["refreshes_per_month"] == 30

    def test_cost_per_refresh_for_5min(self, scheduler_temp_config):
        """Cost per refresh aggregates API_COSTS (zero after AI removal)."""
        sm = scheduler_temp_config
        costs = sm.calculate_costs("5min")
        assert costs.get("cost_per_refresh", 0) >= 0
        assert "sources" in costs

    def test_total_monthly_equals_per_refresh_times_refreshes(self, scheduler_temp_config):
        """total_monthly ≈ cost_per_refresh * refreshes_per_month for non-off."""
        sm = scheduler_temp_config
        for key in ("5min", "1hour", "daily"):
            c = sm.calculate_costs(key)
            if c.get("refreshes_per_month", 0) == 0:
                continue
            expected = c["cost_per_refresh"] * c["refreshes_per_month"]
            assert abs(c["total_monthly"] - expected) < 0.01


# -----------------------------------------------------------------------------
# get_status and get_all_interval_costs consistency
# -----------------------------------------------------------------------------

class TestSchedulerManagerStatusConsistency:
    """get_status and get_interval_options return consistent structure."""

    def test_get_status_after_start_has_next_refresh(self, scheduler_temp_config):
        """After start(), get_status() has next_refresh_in_seconds >= 0."""
        sm = scheduler_temp_config
        sm.set_interval("1hour")
        sm.start()
        status = sm.get_status()
        assert status["is_running"] is True
        assert status.get("next_refresh_in_seconds") is not None
        assert status["next_refresh_in_seconds"] >= 0
        sm.stop()

    def test_get_all_interval_costs_has_all_keys(self, scheduler_temp_config):
        """get_all_interval_costs returns entry for every INTERVALS key."""
        sm = scheduler_temp_config
        all_costs = sm.get_all_interval_costs()
        for key in SchedulerManager.INTERVALS:
            assert key in all_costs
            assert "refreshes_per_month" in all_costs[key]
            assert "total_monthly" in all_costs[key]

    def test_reset_counters_zeroes_today_and_month(self, scheduler_temp_config):
        """reset_counters() sets refresh_count_today and refresh_count_month to 0."""
        sm = scheduler_temp_config
        sm.refresh_count_today = 10
        sm.refresh_count_month = 100
        sm.reset_counters()
        assert sm.refresh_count_today == 0
        assert sm.refresh_count_month == 0


class TestSchedulerManagerDailyAnchoring:
    def test_daily_seconds_until_next_9am_et_before_target(self, scheduler_temp_config):
        sm = scheduler_temp_config
        now = datetime.fromisoformat("2026-03-19T08:00:00-04:00")
        secs = sm._seconds_until_next_daily_refresh(now)
        assert 3590 <= secs <= 3610

    def test_daily_seconds_until_next_9am_et_after_target(self, scheduler_temp_config):
        sm = scheduler_temp_config
        now = datetime.fromisoformat("2026-03-19T10:00:00-04:00")
        secs = sm._seconds_until_next_daily_refresh(now)
        assert 82790 <= secs <= 82810  # ~23h to next 9am ET


class TestSchedulerRefreshAccounting:
    def test_callback_failure_does_not_increment_counters(self, scheduler_temp_config):
        sm = scheduler_temp_config
        sm.refresh_count_today = 3
        sm.refresh_count_month = 9
        sm.refresh_callback = lambda: False
        sm.is_running = False  # avoid reschedule chain in direct call
        sm._execute_refresh()
        assert sm.refresh_count_today == 3
        assert sm.refresh_count_month == 9

    def test_skip_overlap_does_not_reschedule_duplicate_timer_chain(self, scheduler_temp_config):
        """When an execution is already in flight, skipped tick must not reschedule."""
        sm = scheduler_temp_config
        sm.is_running = True
        calls = {"n": 0}
        sm._schedule_next_refresh = lambda: calls.__setitem__("n", calls["n"] + 1)
        # Simulate in-flight refresh by holding lock.
        acquired = sm._refresh_executing.acquire(blocking=False)
        assert acquired is True
        try:
            sm._execute_refresh()
        finally:
            sm._refresh_executing.release()
        assert calls["n"] == 0


class TestSchedulerMonthlyLearning:
    def test_learning_failure_does_not_mark_month_complete(self, scheduler_temp_config):
        sm = scheduler_temp_config
        sm.monthly_learning_enabled = True
        sm.learning_run_day = 1
        sm.last_learning_run = None
        sm.learning_callback = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        sm._maybe_run_monthly_learning()
        # Allow background thread to execute.
        import time
        time.sleep(0.05)
        assert sm.last_learning_run is None
