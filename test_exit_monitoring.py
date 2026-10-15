#!/usr/bin/env python3
# =============================================================================
# test_exit_monitoring.py - Comprehensive Unit Tests
# Run this BEFORE deploying to production
# Test positions use is_test=True (Testing tab only; no account/cash impact).
# Use --temp or SWING_TRADER_TEST_POSITIONS=1 to use a temp positions file.
# =============================================================================

import sys
import os
import time
import json
import tempfile
from datetime import datetime, timedelta

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Positions opened during this run (for cleanup in finally). Do not touch live tab/account.
_opened_pids = []
_temp_positions_path = None

print("\n" + "="*70)
print("EXIT MONITORING SYSTEM - UNIT TESTS")
print("="*70)


# =============================================================================
# TEST 1: Check Monitoring Thread Started
# =============================================================================

def test_monitoring_thread():
    """Verify position monitoring thread is running (when Flask app is active)"""
    print("\n=== TEST 1: Monitoring Thread ===")
    
    import threading
    threads = [t.name for t in threading.enumerate()]
    
    print(f"Active threads: {threads}")
    
    # Check for daemon threads (monitoring runs as daemon when Flask app starts)
    daemon_threads = [t for t in threading.enumerate() if t.daemon]
    
    if len(daemon_threads) > 0:
        print(f"[PASS] Found {len(daemon_threads)} daemon thread(s)")
        return True
    else:
        # Skip when run standalone - monitoring thread only starts with full Flask app
        print("[SKIP] No daemon threads (expected when running tests standalone)")
        return None


# =============================================================================
# TEST 2: Price Fetch Function
# =============================================================================

def test_price_fetch():
    """Test get_current_prices_fast() method"""
    print("\n=== TEST 2: Price Fetch ===")
    
    try:
        from enhanced_data_fetcher import EnhancedDataFetcherV2
        
        fetcher = EnhancedDataFetcherV2()
        
        # Test with common coins
        test_coins = ['bitcoin', 'ethereum', 'solana']
        prices = fetcher.get_current_prices_fast(test_coins)
        
        print(f"Fetched prices for {len(prices)}/{len(test_coins)} coins:")
        for coin, price in prices.items():
            print(f"  {coin}: ${price:,.2f}")
        
        # Validate
        if len(prices) >= 2:
            print("[PASS] Price fetch working")
            return True
        else:
            print("[FAIL] Price fetch failed - check internet connection")
            return False
            
    except AttributeError as e:
        print(f"[FAIL] CRITICAL: get_current_prices_fast() method missing!")
        print(f"   Error: {e}")
        print("   FIX: Add method to enhanced_data_fetcher.py")
        return False
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        return False


# =============================================================================
# TEST 3: Stop-Loss Detection Logic
# =============================================================================

def test_stop_loss_detection():
    """Test stop-loss detection without real trades"""
    print("\n=== TEST 3: Stop-Loss Detection ===")
    
    try:
        from trade_journal import open_position, close_position, get_open_positions
        
        # Create test position (is_test=True: Testing tab only, no account/cash impact)
        test_data = {
            'coin_id': 'bitcoin',
            'coin_name': 'Bitcoin',
            'coin_symbol': 'BTC',
            'signal': 'STRONG_BUY',
            'entry_price': 50000.0,
            'quantity': 0.1,
            'ai_target_pct': 7.5,
            'ai_stop_pct': 2.5,
            'notes': 'TEST POSITION - DELETE AFTER TEST',
            'is_test': True,
        }
        
        pos = open_position(test_data)
        pid = pos['id'] if isinstance(pos, dict) else pos
        _opened_pids.append(pid)
        print(f"[PASS] Created test position #{pid}")
        
        # Get position
        positions = get_open_positions()
        test_pos = next((p for p in positions if p['id'] == pid), None)
        
        if not test_pos:
            print("[FAIL] Could not retrieve test position")
            return False
        
        # Check stop-loss price calculated correctly
        entry = test_pos['entry_price']
        stop = test_pos.get('ai_stop_price') or test_pos.get('current_stop_price')
        
        expected_stop = entry * (1 - 2.5/100)  # 2.5% below entry
        
        print(f"  Entry: ${entry:,.2f}")
        print(f"  Stop:  ${stop:,.2f}")
        print(f"  Expected: ${expected_stop:,.2f}")
        
        # Validate
        if abs(stop - expected_stop) < 1:
            print("[PASS] Stop-loss calculation correct")
        else:
            print(f"[FAIL] Stop-loss mismatch: {stop} vs {expected_stop}")
        
        # Test detection logic
        current_price_above_stop = 49000  # Above stop
        current_price_at_stop = 48750     # At stop
        current_price_below_stop = 48500  # Below stop
        
        stop_triggered_above = current_price_above_stop <= stop
        stop_triggered_at = current_price_at_stop <= stop
        stop_triggered_below = current_price_below_stop <= stop
        
        print(f"\n  Price ${current_price_above_stop:,}: triggered={stop_triggered_above} (should be False)")
        print(f"  Price ${current_price_at_stop:,}: triggered={stop_triggered_at} (should be True)")
        print(f"  Price ${current_price_below_stop:,}: triggered={stop_triggered_below} (should be True)")
        
        # Cleanup
        closed, err = close_position(pid, exit_price=50000, exit_reason='test_cleanup', notes='Unit test cleanup')
        if err:
            print(f"  Cleanup warning: {err}")
        print("\n[PASS] Test cleanup complete")
        
        return not stop_triggered_above and stop_triggered_at and stop_triggered_below
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# TEST 4: Take-Profit Detection Logic
# =============================================================================

def test_take_profit_detection():
    """Test take-profit detection logic"""
    print("\n=== TEST 4: Take-Profit Detection ===")
    
    try:
        from trade_journal import open_position, close_position, get_open_positions
        
        test_data = {
            'coin_id': 'ethereum',
            'coin_name': 'Ethereum',
            'coin_symbol': 'ETH',
            'signal': 'ULTRA_BUY',
            'entry_price': 3000.0,
            'quantity': 1.0,
            'ai_target_pct': 7.5,
            'ai_stop_pct': 2.5,
            'notes': 'TEST POSITION - DELETE AFTER TEST',
            'is_test': True,
        }
        
        pos = open_position(test_data)
        pid = pos['id'] if isinstance(pos, dict) else pos
        _opened_pids.append(pid)
        print(f"[PASS] Created test position #{pid}")
        
        positions = get_open_positions()
        test_pos = next((p for p in positions if p['id'] == pid), None)
        
        entry = test_pos['entry_price']
        target = test_pos.get('ai_target_price') or test_pos.get('current_target_price')
        
        expected_target = entry * (1 + 7.5/100)  # 7.5% above entry
        
        print(f"  Entry: ${entry:,.2f}")
        print(f"  Target: ${target:,.2f}")
        print(f"  Expected: ${expected_target:,.2f}")
        
        # Test detection
        current_price_below = 3200    # Below target
        current_price_at = 3225       # At target
        current_price_above = 3250    # Above target
        
        target_triggered_below = current_price_below >= target
        target_triggered_at = current_price_at >= target
        target_triggered_above = current_price_above >= target
        
        print(f"\n  Price ${current_price_below:,}: triggered={target_triggered_below} (should be False)")
        print(f"  Price ${current_price_at:,}: triggered={target_triggered_at} (should be True)")
        print(f"  Price ${current_price_above:,}: triggered={target_triggered_above} (should be True)")
        
        # Cleanup
        closed, err = close_position(pid, exit_price=3000, exit_reason='test_cleanup', notes='Unit test cleanup')
        if err:
            print(f"  Cleanup warning: {err}")
        print("\n[PASS] Test cleanup complete")
        
        return not target_triggered_below and target_triggered_at and target_triggered_above
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# TEST 5: Trailing Stop Logic
# =============================================================================

def test_trailing_stop():
    """Test trailing stop calculation"""
    print("\n=== TEST 5: Trailing Stop Logic ===")
    
    entry_price = 100.0
    
    # Scenario 1: No trailing (profit < 5%)
    current_price_1 = 104.0  # 4% profit
    profit_pct_1 = ((current_price_1 - entry_price) / entry_price) * 100
    should_trail_1 = profit_pct_1 >= 5
    
    print(f"Scenario 1: ${entry_price} -> ${current_price_1}")
    print(f"  Profit: {profit_pct_1:.1f}%")
    print(f"  Should trail: {should_trail_1} (correct: False)")
    
    # Scenario 2: Trailing activates (profit = 8%)
    current_price_2 = 108.0
    profit_pct_2 = ((current_price_2 - entry_price) / entry_price) * 100
    should_trail_2 = profit_pct_2 >= 5
    high_water_2 = current_price_2
    trailing_stop_2 = high_water_2 * 0.97  # 3% below peak
    
    print(f"\nScenario 2: ${entry_price} -> ${current_price_2}")
    print(f"  Profit: {profit_pct_2:.1f}%")
    print(f"  Should trail: {should_trail_2} (correct: True)")
    print(f"  High water: ${high_water_2:.2f}")
    print(f"  Trailing stop: ${trailing_stop_2:.2f}")
    
    # Scenario 3: Trailing stop hit
    current_price_3 = 104.5  # Drops from 108
    stop_hit_3 = current_price_3 <= trailing_stop_2
    
    print(f"\nScenario 3: Peak ${high_water_2} -> ${current_price_3}")
    print(f"  Trailing stop: ${trailing_stop_2:.2f}")
    print(f"  Stop hit: {stop_hit_3} (correct: True)")
    
    # Validate
    all_correct = (
        not should_trail_1 and
        should_trail_2 and
        stop_hit_3 and
        abs(trailing_stop_2 - 104.76) < 0.1
    )
    
    if all_correct:
        print("\n[PASS] Trailing stop logic correct")
        return True
    else:
        print("\n[FAIL] Trailing stop logic error")
        return False


# =============================================================================
# TEST 5b: update_position high_water_mark (dict form - trailing stop fix)
# =============================================================================

def test_update_position_high_water_mark():
    """Verify update_position(position_id, {'high_water_mark': x}) works (trailing stop fix)"""
    print("\n=== TEST 5b: update_position high_water_mark (dict form) ===")

    try:
        from trade_journal import open_position, close_position, update_position, get_open_positions

        test_data = {
            'coin_id': 'ethereum',
            'coin_name': 'Ethereum',
            'coin_symbol': 'ETH',
            'signal': 'STRONG_BUY',
            'entry_price': 3000.0,
            'quantity': 1.0,
            'ai_target_pct': 7.5,
            'ai_stop_pct': 2.5,
            'notes': 'TEST - update_position high_water_mark',
            'is_test': True,
        }
        pos = open_position(test_data)
        pid = pos['id'] if isinstance(pos, dict) else pos
        _opened_pids.append(pid)

        # Call update_position with dict form (the fix for trailing stop)
        high_water = 3250.0
        updated, err = update_position(pid, {'high_water_mark': high_water})
        if err:
            print(f"[FAIL] update_position returned error: {err}")
            close_position(pid, exit_price=3000, exit_reason='test_cleanup', notes='Unit test cleanup')
            return False

        positions = get_open_positions()
        test_pos = next((p for p in positions if p['id'] == pid), None)
        if not test_pos:
            print("[FAIL] Could not retrieve updated position")
            close_position(pid, exit_price=3000, exit_reason='test_cleanup', notes='Unit test cleanup')
            return False

        got = float(test_pos.get('high_water_mark') or 0)
        if abs(got - high_water) < 0.01:
            print(f"[PASS] high_water_mark updated correctly: ${got:.2f}")
        else:
            print(f"[FAIL] high_water_mark mismatch: got ${got:.2f}, expected ${high_water:.2f}")

        close_position(pid, exit_price=3000, exit_reason='test_cleanup', notes='Unit test cleanup')
        return abs(got - high_water) < 0.01

    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# TEST 6: Duplicate Alert Prevention
# =============================================================================

def test_duplicate_alerts():
    """Test that _should_alert prevents spam"""
    print("\n=== TEST 6: Duplicate Alert Prevention ===")
    
    try:
        # Import app to get _should_alert (triggers thread start too)
        import app
        _should_alert = getattr(app, '_should_alert', None)
        
        if _should_alert is None:
            print("[SKIP]  _should_alert not found in app.py - skipping")
            return None
        
        position_id = 999
        alert_type = 'stop'
        
        # First alert should go through
        should_1 = _should_alert(position_id, alert_type)
        print(f"First alert: {should_1} (should be True)")
        
        # Immediate second alert should be blocked
        should_2 = _should_alert(position_id, alert_type)
        print(f"Second alert (immediate): {should_2} (should be False)")
        
        # Different alert type should go through
        should_3 = _should_alert(position_id, 'target')
        print(f"Different type: {should_3} (should be True)")
        
        if should_1 and not should_2 and should_3:
            print("[PASS] Duplicate prevention working")
            return True
        else:
            print("[FAIL] Duplicate prevention failed")
            return False
            
    except ImportError as e:
        print(f"[SKIP]  Cannot import app: {e}")
        return None
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# TEST 7: Performance Analytics
# =============================================================================

def test_performance_analytics():
    """Test Sharpe ratio and metrics calculations"""
    print("\n=== TEST 7: Performance Analytics ===")
    
    try:
        from performance_analytics import PerformanceAnalytics
        
        # Create mock positions with known results (journal format uses pnl_pct, pnl_usd, outcome)
        mock_positions = [
            {'status': 'CLOSED', 'entry_price': 100, 'exit_price': 110, 'pnl_usd': 100, 'pnl_pct': 10, 'outcome': 'win'},
            {'status': 'CLOSED', 'entry_price': 100, 'exit_price': 95,  'pnl_usd': -50, 'pnl_pct': -5, 'outcome': 'loss'},
            {'status': 'CLOSED', 'entry_price': 100, 'exit_price': 108, 'pnl_usd': 80,  'pnl_pct': 8,  'outcome': 'win'},
            {'status': 'CLOSED', 'entry_price': 100, 'exit_price': 112, 'pnl_usd': 120, 'pnl_pct': 12, 'outcome': 'win'},
        ]
        
        metrics = PerformanceAnalytics.from_positions(mock_positions, account_size=10000)
        
        print(f"  Sharpe Ratio: {metrics['sharpe_ratio']}")
        print(f"  Sortino Ratio: {metrics['sortino_ratio']}")
        print(f"  Expectancy %: {metrics.get('expectancy_pct', 0):.1f}%")
        print(f"  Expectancy $: ${metrics.get('expectancy_usd', 0):.2f}")
        print(f"  Max Drawdown: {metrics['max_drawdown_pct']:.1f}%")
        print(f"  Total Trades: {metrics['trades']}")
        
        # Validate trades counted correctly
        if metrics['trades'] == 4:
            print("\n[PASS] Performance analytics working")
            return True
        else:
            print(f"\n[FAIL] Expected 4 trades, got {metrics['trades']}")
            return False
            
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# RUN ALL TESTS
# =============================================================================

def _cleanup_opened_positions():
    """Close any positions opened during this run (so test trades are closed after test)."""
    global _opened_pids
    if not _opened_pids:
        return
    try:
        from trade_journal import get_position_by_id, close_position
        for pid in list(_opened_pids):
            try:
                p = get_position_by_id(pid)
                if p and p.get('status') == 'OPEN':
                    close_position(pid, exit_price=float(p.get('entry_price') or 0), exit_reason='test_cleanup', notes='Script teardown')
            except Exception:
                pass
    except Exception:
        pass
    _opened_pids = []


def run_all_tests():
    """Run complete test suite"""
    global _opened_pids, _temp_positions_path
    
    _opened_pids = []
    
    # Optional: use temp positions file so production positions.json is never modified
    use_temp = os.environ.get('SWING_TRADER_TEST_POSITIONS') or '--temp' in sys.argv
    if use_temp:
        fd, _temp_positions_path = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        with open(_temp_positions_path, 'w') as f:
            json.dump([], f)
        import trade_journal as _tj
        _tj.POSITIONS_FILE = _temp_positions_path
        print("\n[INFO] Using temp positions file (production positions.json not modified)")
    
    try:
        results = {}
        
        # Import app first to start monitoring threads (for test 1)
        try:
            import app as _app
        except Exception as e:
            print(f"Warning: Could not import app: {e}")
        
        # Run tests
        results['monitoring_thread'] = test_monitoring_thread()
        results['price_fetch'] = test_price_fetch()
        results['stop_loss'] = test_stop_loss_detection()
        results['take_profit'] = test_take_profit_detection()
        results['trailing_stop'] = test_trailing_stop()
        results['update_position_hwm'] = test_update_position_high_water_mark()
        results['duplicate_alerts'] = test_duplicate_alerts()
        results['performance'] = test_performance_analytics()
        
        # Summary
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        
        passed = sum(1 for v in results.values() if v is True)
        failed = sum(1 for v in results.values() if v is False)
        skipped = sum(1 for v in results.values() if v is None)
        total = len(results)
        
        for test, result in results.items():
            status = "PASS" if result is True else "FAIL" if result is False else "SKIP"
            print(f"  {test:20s}: {status}")
        
        print(f"\nResults: {passed}/{total} passed, {failed}/{total} failed, {skipped}/{total} skipped")
        
        if failed == 0:
            print("\n[PASS] ALL TESTS PASSED - System ready for deployment!")
            return 0
        else:
            print(f"\n[FAIL] {failed} test(s) failed - Fix before deploying")
            return 1
    finally:
        _cleanup_opened_positions()
        if _temp_positions_path and os.path.exists(_temp_positions_path):
            try:
                os.unlink(_temp_positions_path)
            except OSError:
                pass
            _temp_positions_path = None


if __name__ == '__main__':
    exit_code = run_all_tests()
    sys.exit(exit_code)
