# =============================================================================
# scheduler_manager.py — Configurable Refresh Scheduler with Cost Tracking
# Manages auto-refresh intervals, cost tracking, and optional monthly ML learning run
# =============================================================================

import logging
import threading
import time
from datetime import datetime
import os
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class SchedulerManager:
    """
    Manages configurable refresh scheduling and cost tracking.
    
    Features:
    - Configurable refresh intervals (5min to daily)
    - Can be completely disabled
    - Tracks refresh count
    - Calculates API costs
    - Persists settings
    """
    
    # API Cost Structure (per refresh)
    API_COSTS = {
        'alpha_vantage': {
            'name': 'Alpha Vantage (News)',
            'cost_per_refresh': 0.00,
            'note': 'Free tier (500 calls/day)'
        },
        'coingecko': {
            'name': 'CoinGecko (Price Data)',
            'cost_per_refresh': 0.00,
            'note': 'Free tier; CoinPaprika + CoinCap fallback when rate-limited'
        }
    }
    
    # Refresh interval options (in seconds)
    INTERVALS = {
        'off': {'seconds': 0, 'label': 'Off (Manual Only)', 'per_month': 0},
        '5min': {'seconds': 300, 'label': '5 Minutes', 'per_month': 8640},
        '15min': {'seconds': 900, 'label': '15 Minutes', 'per_month': 2880},
        '30min': {'seconds': 1800, 'label': '30 Minutes', 'per_month': 1440},
        '1hour': {'seconds': 3600, 'label': '1 Hour (Recommended)', 'per_month': 720},
        '2hour': {'seconds': 7200, 'label': '2 Hours', 'per_month': 360},
        '4hour': {'seconds': 14400, 'label': '4 Hours', 'per_month': 180},
        '6hour': {'seconds': 21600, 'label': '6 Hours', 'per_month': 120},
        'daily': {'seconds': 86400, 'label': 'Daily (9am ET)', 'per_month': 30}
    }
    
    def __init__(self, config_file='scheduler_config.json'):
        """Initialize scheduler manager. Default interval is 1 hour unless overridden by saved config."""
        self.config_file = config_file
        self.current_interval = '1hour'  # default unless user changes in Schedule tab
        self.is_running = False
        self.timer = None
        self._refresh_executing = threading.Lock()  # prevents overlapping refresh callbacks
        self.refresh_callback = None
        self.learning_callback = None
        self.refresh_count_today = 0
        self.refresh_count_month = 0
        self.last_refresh_time = None
        self.next_refresh_time = None
        # Monthly ML learning report: run pipeline on a set day of month
        self.monthly_learning_enabled = False
        self.learning_run_day = 1  # 1–28
        self.last_learning_run = None  # YYYY-MM

        # Weekly tasks: replay, optimizer, roadmap (Saturday mornings ET)
        self.weekly_tasks_enabled = True
        self.weekly_run_day = 5  # 0=Mon, 6=Sun
        self.weekly_run_hour_et = 3  # 3 AM ET — ml at 2, replay at 3, optimizer at 4, roadmap at 5
        self.last_weekly_run = None  # ISO week string e.g. "2026-W13"
        self._weekly_callbacks = {}  # name -> callable

        # Status email: send daily status report at configurable hours (ET)
        self.status_email_enabled = True
        self.status_email_hours_et = [9, 13, 17]  # 9am, 1pm, 5pm ET default

        # Load saved configuration
        self._load_config()
    
    
    # =========================================================================
    # CONFIGURATION MANAGEMENT
    # =========================================================================
    
    def _load_config(self):
        """Load configuration from file"""
        try:
            import safe_json
            config = safe_json.load(self.config_file, default=dict)
            if config and isinstance(config, dict):
                self.current_interval = config.get('interval', '1hour')
                self.refresh_count_today = config.get('count_today', 0)
                self.refresh_count_month = config.get('count_month', 0)
                self.last_refresh_time = config.get('last_refresh', None)
                self.monthly_learning_enabled = config.get('monthly_learning_enabled', False)
                self.learning_run_day = max(1, min(28, int(config.get('learning_run_day', 1))))
                self.last_learning_run = config.get('last_learning_run')
                self.weekly_tasks_enabled = config.get('weekly_tasks_enabled', True)
                self.weekly_run_day = max(0, min(6, int(config.get('weekly_run_day', 6))))
                self.weekly_run_hour_et = max(0, min(23, int(config.get('weekly_run_hour_et', 3))))
                self.last_weekly_run = config.get('last_weekly_run')
                self.status_email_enabled = config.get('status_email_enabled', True)
                raw_hours = config.get('status_email_hours_et', [9, 13, 17])
                self.status_email_hours_et = [max(0, min(23, int(h))) for h in raw_hours if str(h).strip() != '']
                if not self.status_email_hours_et:
                    self.status_email_hours_et = [9, 13, 17]
        except Exception as e:
            logger.error(f"Error loading scheduler config: {e}")


    def _save_config(self):
        """Save configuration to file"""
        import safe_json
        config = {
            'interval': self.current_interval,
            'count_today': self.refresh_count_today,
            'count_month': self.refresh_count_month,
            'last_refresh': self.last_refresh_time,
            'monthly_learning_enabled': self.monthly_learning_enabled,
            'learning_run_day': self.learning_run_day,
            'last_learning_run': self.last_learning_run,
            'weekly_tasks_enabled': self.weekly_tasks_enabled,
            'weekly_run_day': self.weekly_run_day,
            'weekly_run_hour_et': self.weekly_run_hour_et,
            'last_weekly_run': self.last_weekly_run,
            'status_email_enabled': self.status_email_enabled,
            'status_email_hours_et': self.status_email_hours_et,
            'updated_at': datetime.now().isoformat()
        }
        if not safe_json.save(self.config_file, config):
            logger.error("Error saving scheduler config")
    
    
    # =========================================================================
    # SCHEDULER CONTROL
    # =========================================================================
    
    def set_interval(self, interval_key):
        """
        Set refresh interval and restart scheduler.
        
        Args:
            interval_key: One of 'off', '5min', '15min', '30min', '1hour', 
                         '2hour', '4hour', '6hour', 'daily'
        
        Returns:
            dict: Status of operation
        """
        if interval_key not in self.INTERVALS:
            return {'success': False, 'error': f'Invalid interval: {interval_key}'}
        
        # Stop current scheduler
        self.stop()
        
        # Update interval
        self.current_interval = interval_key
        
        # Start new scheduler if not 'off'
        if interval_key != 'off':
            self.start()
        
        # Save config
        self._save_config()
        
        return {
            'success': True,
            'interval': interval_key,
            'label': self.INTERVALS[interval_key]['label'],
            'is_running': self.is_running,
            'next_refresh': self.next_refresh_time
        }
    
    
    def start(self):
        """Start the scheduler"""
        if self.current_interval == 'off':
            return False
        
        if self.is_running:
            return False
        
        interval_seconds = self.INTERVALS[self.current_interval]['seconds']
        
        if interval_seconds > 0:
            self.is_running = True
            self._schedule_next_refresh()
            return True
        
        return False
    
    
    def stop(self):
        """Stop the scheduler. Sets is_running=False first to prevent _execute_refresh from rescheduling."""
        self.is_running = False
        self.next_refresh_time = None
        timer = self.timer
        self.timer = None
        if timer:
            timer.cancel()
        return True
    
    
    def _schedule_next_refresh(self):
        """Schedule the next refresh"""
        if not self.is_running or self.current_interval == 'off':
            return
        
        interval_seconds = self.INTERVALS[self.current_interval]['seconds']
        if self.current_interval == 'daily':
            interval_seconds = self._seconds_until_next_daily_refresh()
        
        # Calculate next refresh time
        self.next_refresh_time = datetime.now().timestamp() + interval_seconds
        
        # Schedule timer
        self.timer = threading.Timer(interval_seconds, self._execute_refresh)
        self.timer.daemon = True
        self.timer.start()

    def _seconds_until_next_daily_refresh(self, now: datetime | None = None) -> int:
        """
        Return seconds until next 9:00 AM America/New_York refresh.
        Uses local-now if no datetime is provided.
        """
        now_dt = now or datetime.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.astimezone()
        now_et = now_dt.astimezone(ZoneInfo("America/New_York"))
        target_et = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
        if now_et >= target_et:
            from datetime import timedelta
            target_et = target_et + timedelta(days=1)
        delta = (target_et - now_et).total_seconds()
        # Keep timer > 0 to avoid immediate recursive scheduling.
        return max(1, int(delta))
    
    
    def _execute_refresh(self):
        """Execute refresh and reschedule. Uses lock to prevent overlapping executions."""
        if not self._refresh_executing.acquire(blocking=False):
            logger.warning("Skipping scheduled refresh — previous refresh still running")
            # Do NOT reschedule here: the in-flight refresh will schedule the next tick.
            # Rescheduling from both paths can duplicate timer chains and trigger double refreshes.
            return
        try:
            # Call the refresh callback
            refresh_ok = True
            if self.refresh_callback:
                logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] Scheduled refresh triggered")
                result = self.refresh_callback()
                if result is False:
                    refresh_ok = False

            # Update counters only when callback reports success.
            if refresh_ok:
                self.refresh_count_today += 1
                self.refresh_count_month += 1
                self.last_refresh_time = datetime.now().isoformat()
            else:
                logger.warning("Scheduled refresh callback reported failure; counters not incremented")

            # Save config
            self._save_config()

        except Exception as e:
            logger.error(f"Error during scheduled refresh: {e}")
        finally:
            self._refresh_executing.release()

        # Guarantee reschedule even if monthly/weekly task callbacks throw.
        try:
            self._maybe_run_monthly_learning()
            self._maybe_run_weekly_tasks()
        except Exception as e:
            logger.error(f"Error in post-refresh tasks: {e}")
        finally:
            if self.is_running:
                self._schedule_next_refresh()
    
    
    def _maybe_run_monthly_learning(self):
        """If monthly learning is enabled and due, run the learning callback once in a background thread."""
        if not self.learning_callback or not self.monthly_learning_enabled:
            return
        now = datetime.now()
        current_month = now.strftime('%Y-%m')
        if self.last_learning_run == current_month:
            return
        if now.day < self.learning_run_day:
            return
        def _run():
            try:
                logger.info(f"[{now.strftime('%H:%M:%S')}] Monthly learning pipeline started")
                self.learning_callback()
                self.last_learning_run = current_month
                self._save_config()
                # FINAL POLISH: reload thresholds after daily retrain so next read gets fresh values
                try:
                    from thresholds import reload_live_thresholds
                    reload_live_thresholds()
                except Exception:
                    pass
                logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] Monthly learning pipeline finished")
            except Exception as e:
                logger.error(f"Error during monthly learning run: {e}")
        t = threading.Thread(target=_run, daemon=True)
        t.start()
    
    
    def set_refresh_callback(self, callback):
        """Set the function to call on refresh"""
        self.refresh_callback = callback

    def set_learning_callback(self, callback):
        """Set the function to call for monthly ML learning report (runs in background)."""
        self.learning_callback = callback

    def set_monthly_learning(self, enabled: bool, day_of_month: int = 1) -> dict:
        """
        Enable or disable monthly ML learning run, and set day of month (1–28).
        Returns status dict.
        """
        self.monthly_learning_enabled = bool(enabled)
        self.learning_run_day = max(1, min(28, int(day_of_month)))
        self._save_config()
        return self.get_monthly_learning_status()

    def get_monthly_learning_status(self) -> dict:
        """Get monthly learning schedule status."""
        return {
            'enabled': self.monthly_learning_enabled,
            'day_of_month': self.learning_run_day,
            'last_learning_run': self.last_learning_run,
        }

    def check_and_run_monthly_learning(self):
        """
        Check whether monthly learning is due and run it if so.
        Call from a periodic thread so learning runs even when refresh is off.
        """
        self._maybe_run_monthly_learning()

    # =========================================================================
    # WEEKLY TASKS (replay, optimizer, roadmap — Sunday mornings ET)
    # =========================================================================

    def set_weekly_callback(self, name: str, callback):
        """Register a named weekly callback (e.g. 'replay', 'optimizer', 'roadmap')."""
        self._weekly_callbacks[name] = callback

    def _maybe_run_weekly_tasks(self):
        """If weekly tasks are enabled and it's the right day/hour, run them once per ISO week."""
        if not self.weekly_tasks_enabled or not self._weekly_callbacks:
            return
        try:
            et = ZoneInfo("America/New_York")
        except Exception:
            return
        now_et = datetime.now(et)
        if now_et.weekday() != self.weekly_run_day:
            return
        if now_et.hour < self.weekly_run_hour_et:
            return
        current_week = now_et.strftime('%G-W%V')
        if self.last_weekly_run == current_week:
            return

        # Mark as run BEFORE spawning to prevent duplicate spawns if _maybe_run_weekly_tasks
        # is called again before the thread gets scheduled (e.g. rapid refresh ticks).
        self.last_weekly_run = current_week
        self._save_config()

        def _run():
            try:
                for name in ('replay', 'optimizer', 'roadmap'):
                    cb = self._weekly_callbacks.get(name)
                    if not cb:
                        continue
                    logger.info("[weekly] Running %s", name)
                    last_exc = None
                    for attempt in range(1, 3):  # 2 attempts max
                        try:
                            cb()
                            logger.info("[weekly] %s completed", name)
                            last_exc = None
                            break
                        except Exception as e:
                            last_exc = e
                            if attempt < 2:
                                logger.warning("[weekly] %s failed (attempt %d), retrying in 30s: %s", name, attempt, e)
                                time.sleep(30)
                            else:
                                logger.error("[weekly] %s failed after 2 attempts: %s", name, e)
                try:
                    from thresholds import reload_live_thresholds
                    reload_live_thresholds()
                except Exception:
                    pass
                logger.info("[weekly] All weekly tasks completed for %s", current_week)
            except Exception as e:
                logger.error("[weekly] Weekly task runner error: %s", e)
        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def get_weekly_tasks_status(self) -> dict:
        """Get weekly tasks schedule status."""
        return {
            'enabled': self.weekly_tasks_enabled,
            'run_day': self.weekly_run_day,
            'run_hour_et': self.weekly_run_hour_et,
            'last_weekly_run': self.last_weekly_run,
            'registered_tasks': list(self._weekly_callbacks.keys()),
        }

    def set_weekly_tasks(self, enabled: bool = None, run_day: int = None, run_hour_et: int = None) -> dict:
        """Configure weekly task schedule."""
        if enabled is not None:
            self.weekly_tasks_enabled = bool(enabled)
        if run_day is not None:
            self.weekly_run_day = max(0, min(6, int(run_day)))
        if run_hour_et is not None:
            self.weekly_run_hour_et = max(0, min(23, int(run_hour_et)))
        self._save_config()
        return self.get_weekly_tasks_status()

    def get_status_email_schedule(self) -> dict:
        """Get status email schedule (enabled, hours_et in Eastern)."""
        return {
            'enabled': self.status_email_enabled,
            'hours_et': self.status_email_hours_et,
        }

    def set_status_email_schedule(self, enabled: bool = None, hours_et: list = None) -> dict:
        """
        Set status email schedule. hours_et: list of hours (0-23) in Eastern time.
        Returns current status.
        """
        if enabled is not None:
            self.status_email_enabled = bool(enabled)
        if hours_et is not None:
            validated = [max(0, min(23, int(h))) for h in hours_et if str(h).strip() != '']
            self.status_email_hours_et = sorted(set(validated)) if validated else [9, 13, 17]
        self._save_config()
        return self.get_status_email_schedule()
    
    
    # =========================================================================
    # COST CALCULATIONS
    # =========================================================================
    
    def calculate_costs(self, interval_key=None):
        """
        Calculate cost projections for a given interval.
        
        Args:
            interval_key: Interval to calculate for (or current if None)
        
        Returns:
            dict: Complete cost breakdown
        """
        if interval_key is None:
            interval_key = self.current_interval
        
        if interval_key not in self.INTERVALS:
            return {'error': 'Invalid interval'}
        
        interval_data = self.INTERVALS[interval_key]
        refreshes_per_month = interval_data['per_month']
        
        # Calculate per-source costs
        sources = []
        total_per_refresh = 0
        
        for key, source in self.API_COSTS.items():
            cost = source['cost_per_refresh']
            monthly = cost * refreshes_per_month
            total_per_refresh += cost
            
            sources.append({
                'key': key,
                'name': source['name'],
                'cost_per_refresh': round(cost, 4),
                'monthly_cost': round(monthly, 2),
                'note': source.get('note', '')
            })
        
        total_monthly = total_per_refresh * refreshes_per_month
        
        return {
            'interval': interval_key,
            'interval_label': interval_data['label'],
            'refreshes_per_month': refreshes_per_month,
            'cost_per_refresh': round(total_per_refresh, 4),
            'total_monthly': round(total_monthly, 2),
            'sources': sources
        }
    
    
    def get_all_interval_costs(self):
        """Get cost projections for all intervals"""
        return {
            key: self.calculate_costs(key)
            for key in self.INTERVALS.keys()
        }
    
    
    # =========================================================================
    # STATUS & INFO
    # =========================================================================
    
    def get_status(self):
        """Get current scheduler status"""
        time_until_next = None
        if self.next_refresh_time:
            time_until_next = max(0, int(self.next_refresh_time - datetime.now().timestamp()))
        
        return {
            'interval': self.current_interval,
            'interval_label': self.INTERVALS[self.current_interval]['label'],
            'is_running': self.is_running,
            'last_refresh': self.last_refresh_time,
            'next_refresh_in_seconds': time_until_next,
            'refresh_count_today': self.refresh_count_today,
            'refresh_count_month': self.refresh_count_month,
            'costs': self.calculate_costs(),
            'status_email': self.get_status_email_schedule(),
        }
    
    
    def get_interval_options(self):
        """Get all available interval options"""
        return [
            {
                'key': key,
                'label': data['label'],
                'seconds': data['seconds'],
                'per_month': data['per_month']
            }
            for key, data in self.INTERVALS.items()
        ]
    
    
    def reset_counters(self):
        """Reset daily/monthly counters (call this from cron)"""
        self.refresh_count_today = 0
        self.refresh_count_month = 0
        self._save_config()


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

if __name__ == '__main__':
    # Example usage
    scheduler = SchedulerManager()
    
    print("=" * 70)
    print("SCHEDULER MANAGER - COST PROJECTIONS")
    print("=" * 70)
    
    # Show all interval costs
    all_costs = scheduler.get_all_interval_costs()
    
    for interval_key, costs in all_costs.items():
        print(f"\n{costs['interval_label']}")
        print(f"  Refreshes/month: {costs['refreshes_per_month']}")
        print(f"  Cost per refresh: ${costs['cost_per_refresh']}")
        print(f"  Total monthly: ${costs['total_monthly']}")
    
    print("\n" + "=" * 70)
    print("RECOMMENDATION: 1 Hour intervals")
    print("  Cost: $131/month")
    print("  Perfect for swing trading")
    print("=" * 70)
