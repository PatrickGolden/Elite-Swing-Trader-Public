# backtester.py — WALK-FORWARD + REGIME-STRATIFIED + MONTE-CARLO (PHASE 3)
# Rolling windows (3m train, 1m test), slippage 0.2–0.5%, costs 0.1–0.26%, require Sharpe >1.0
#
# UNITS: outcome_7d = 7-day return in PERCENT (e.g. 10.0 = 10%). outcome_7d_adjusted subtracts
#   slippage (mid 0.35%) + round-trip cost (TRANSACTION_COST_PCT * 2). Monte Carlo resamples
#   adjusted outcomes and reports mean, 5th, 95th percentiles (in percent).
# SHARPE: 7d returns → use periods_per_year=52 for correct annualization.
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np

# Realistic costs (low-cost improvement)
SLIPPAGE_PCT_MIN = 0.2
SLIPPAGE_PCT_MAX = 0.5
TRANSACTION_COST_PCT = 0.18  # ~0.1–0.26% midpoint (Coinbase/Kraken)
SHARPE_THRESHOLD = 1.0


class Backtester:
    def __init__(self, recommendations_file='ai_recommendations_v2.json'):
        self.recommendations_file = recommendations_file
        self.recommendations = self._load_recommendations()

    def _load_recommendations(self):
        import safe_json
        data = safe_json.load(self.recommendations_file, default=list)
        return data if isinstance(data, list) else []

    def run_walk_forward_backtest(self, days=90, window_months=3, train_months=None, test_months=1):
        """
        Walk-forward: rolling windows (train_months train, test_months test).
        Applies slippage (0.2–0.5%) and transaction costs (0.1–0.26%).
        Requires positive Sharpe >1.0 across walk-forward periods.

        train_months defaults to None, which auto-sizes to (days//30 - test_months) so the
        walk-forward window fits within the actual data span (e.g. days=90 → train=2, test=1).
        """
        if train_months is None:
            # Reserve test_months + 1 extra month from the data span so the walk-forward
            # loop fires at least once (actual trade span ≈ days-1 days, not days).
            available_months = max(1, days // 30)
            train_months = max(1, available_months - test_months - 1)
        cutoff = datetime.now() - timedelta(days=days)
        trades = []
        for r in self.recommendations:
            ts = r.get('timestamp')
            if not ts or len(ts) < 19:
                continue
            try:
                t = datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')
                if t >= cutoff:
                    trades.append(r)
            except (ValueError, TypeError):
                continue

        # Apply slippage and costs to outcomes (deterministic)
        slippage_mid = (SLIPPAGE_PCT_MIN + SLIPPAGE_PCT_MAX) / 2
        cost_round_trip = TRANSACTION_COST_PCT * 2  # entry + exit
        def _adjust_outcome(outcome_pct):
            if outcome_pct is None:
                return None
            return outcome_pct - slippage_mid - cost_round_trip

        adjusted_trades = []
        for t in trades:
            o = t.get('outcome_7d')
            if o is not None:
                adj = _adjust_outcome(float(o))
                adjusted_trades.append({**t, 'outcome_7d_adjusted': adj})
            else:
                adjusted_trades.append({**t, 'outcome_7d_adjusted': None})

        report = {
            'period': f'Last {days} days (walk-forward)',
            'total_signals': len(trades),
            'by_regime': {},
            'by_tier': {},
            'monte_carlo': self._run_monte_carlo(adjusted_trades),
            'suggestions': [],
            'walk_forward': {},
            'sharpe_passed': False,
        }

        # Regime-stratified
        for regime in ['BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE']:
            reg_trades = [t for t in adjusted_trades if t.get('market_regime') == regime]
            if reg_trades:
                report['by_regime'][regime] = self._calculate_metrics(reg_trades, use_adjusted=True)

        # Tier-stratified
        for tier in ['ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY']:
            q = tier.lower().replace('_buy', '')
            tier_trades = [t for t in adjusted_trades if (t.get('consensus_quality') or '').lower() == q]
            if tier_trades:
                report['by_tier'][tier] = self._calculate_metrics(tier_trades, use_adjusted=True)

        # Walk-forward rolling windows
        wf_results = self._run_walk_forward_windows(adjusted_trades, train_months, test_months)
        report['walk_forward'] = wf_results
        report['slippage_pct'] = f'{SLIPPAGE_PCT_MIN}-{SLIPPAGE_PCT_MAX}'
        report['transaction_cost_pct'] = TRANSACTION_COST_PCT
        report['sharpe_threshold'] = SHARPE_THRESHOLD

        # Sharpe check: require >1.0 across periods
        sharpe_vals = [p.get('sharpe_ratio') for p in wf_results.get('periods', []) if p.get('sharpe_ratio') is not None]
        report['sharpe_passed'] = len(sharpe_vals) > 0 and all(s >= SHARPE_THRESHOLD for s in sharpe_vals)
        if not report['sharpe_passed'] and sharpe_vals:
            report['suggestions'].append(
                f"⚠ Walk-forward Sharpe {min(sharpe_vals):.2f} below threshold {SHARPE_THRESHOLD}. Consider reducing exposure."
            )

        # Performance analytics integration (7d returns → 52 periods/year)
        try:
            from performance_analytics import calculate_sharpe_ratio, max_drawdown
            rets = [t['outcome_7d_adjusted'] for t in adjusted_trades if t.get('outcome_7d_adjusted') is not None]
            if rets:
                report['sharpe_ratio'] = calculate_sharpe_ratio(rets, periods_per_year=52.0)
                equity = [10000]
                for r in rets:
                    equity.append(equity[-1] * (1 + r / 100))
                dd = max_drawdown(equity)
                report['max_drawdown_pct'] = dd['max_drawdown_pct']
        except Exception:
            report['sharpe_ratio'] = None
            report['max_drawdown_pct'] = None

        return report

    def _run_walk_forward_windows(self, trades, train_months, test_months):
        """Rolling windows: train_months train, test_months test.

        Anchors to the actual resolved-outcome date range, not today's date. This prevents
        the test window from falling in recent weeks where outcome_7d is not yet resolved,
        which would produce empty periods and a null Sharpe.

        Fallback: if rolling windows produce no periods but ≥10 resolved trades exist,
        returns a single full-span period so the Sharpe gate has data to evaluate.
        """
        if not trades:
            return {'periods': [], 'overall_sharpe': None}

        # Filter to trades with both a parseable timestamp AND a resolved outcome.
        # Anchoring on resolved trades prevents the test window from landing in the
        # "unresolved recent" zone (outcome_7d_adjusted is None).
        resolved = []
        for t in trades:
            ts = t.get('timestamp')
            if not ts:
                continue
            if t.get('outcome_7d_adjusted') is None:
                continue
            try:
                datetime.strptime(str(ts)[:10], '%Y-%m-%d')
                resolved.append(t)
            except (ValueError, TypeError):
                continue

        sorted_trades = sorted(resolved, key=lambda x: x.get('timestamp', ''))

        if not sorted_trades:
            import logging
            logging.getLogger(__name__).warning(
                "[backtester] walk-forward: no trades with resolved outcome_7d — "
                "outcome_backfill may not have run yet. Returning empty periods."
            )
            return {'periods': [], 'overall_sharpe': None}

        start = datetime.strptime(sorted_trades[0]['timestamp'][:10], '%Y-%m-%d')
        end = datetime.strptime(sorted_trades[-1]['timestamp'][:10], '%Y-%m-%d')
        periods = []
        curr = start
        while curr + timedelta(days=30 * (train_months + test_months)) <= end:
            train_end = curr + timedelta(days=30 * train_months)
            test_end = curr + timedelta(days=30 * (train_months + test_months))
            test_trades = [
                t for t in sorted_trades
                if train_end <= datetime.strptime(t['timestamp'][:10], '%Y-%m-%d') < test_end
            ]
            if test_trades:
                m = self._calculate_metrics(test_trades, use_adjusted=True)
                rets = [t.get('outcome_7d_adjusted') for t in test_trades if t.get('outcome_7d_adjusted') is not None]
                sharpe = None
                if rets:
                    try:
                        from performance_analytics import calculate_sharpe_ratio
                        sharpe = calculate_sharpe_ratio(rets, periods_per_year=52.0)
                    except Exception:
                        pass
                periods.append({
                    'start': curr.strftime('%Y-%m-%d'),
                    'end': (test_end - timedelta(days=1)).strftime('%Y-%m-%d'),
                    'trades': len(test_trades),
                    **m,
                    'sharpe_ratio': sharpe,
                })
            curr = curr + timedelta(days=30 * test_months)

        # Fallback: rolling windows produced no periods (data span < one full window).
        # Use a single full-span period over all resolved trades so the Sharpe gate fires.
        if not periods and len(sorted_trades) >= 10:
            import logging
            logging.getLogger(__name__).warning(
                "[backtester] walk-forward: rolling windows produced no periods "
                "(resolved data span %s→%s, window=%d+%d months). "
                "Using full-span fallback period with %d trades.",
                sorted_trades[0]['timestamp'][:10],
                sorted_trades[-1]['timestamp'][:10],
                train_months, test_months,
                len(sorted_trades),
            )
            m = self._calculate_metrics(sorted_trades, use_adjusted=True)
            rets = [t.get('outcome_7d_adjusted') for t in sorted_trades if t.get('outcome_7d_adjusted') is not None]
            sharpe = None
            if rets:
                try:
                    from performance_analytics import calculate_sharpe_ratio
                    sharpe = calculate_sharpe_ratio(rets, periods_per_year=52.0)
                except Exception:
                    pass
            periods.append({
                'start': sorted_trades[0]['timestamp'][:10],
                'end': sorted_trades[-1]['timestamp'][:10],
                'trades': len(sorted_trades),
                'fallback_full_span': True,
                **m,
                'sharpe_ratio': sharpe,
            })

        overall_sharpe = None
        if periods:
            all_rets = [t.get('outcome_7d_adjusted') for t in sorted_trades if t.get('outcome_7d_adjusted') is not None]
            if all_rets:
                try:
                    from performance_analytics import calculate_sharpe_ratio
                    overall_sharpe = calculate_sharpe_ratio(all_rets, periods_per_year=52.0)
                except Exception:
                    pass
        return {'periods': periods, 'overall_sharpe': overall_sharpe}

    def run_full_backtest(self, days=90, window_months=3):
        """Alias for API compatibility; same as walk-forward over the period."""
        return self.run_walk_forward_backtest(days=days, window_months=window_months)

    def _calculate_metrics(self, trades, use_adjusted=False):
        key = 'outcome_7d_adjusted' if use_adjusted else 'outcome_7d'
        outcomes = [t.get(key, 0) for t in trades if t.get(key) is not None]
        if not outcomes:
            return {}
        wins = sum(1 for o in outcomes if o > 0)
        win_rate = (wins / len(outcomes)) * 100
        avg_win = np.mean([o for o in outcomes if o > 0]) if any(o > 0 for o in outcomes) else 0
        avg_loss = np.mean([o for o in outcomes if o < 0]) if any(o < 0 for o in outcomes) else 0
        profit_factor = (sum(o for o in outcomes if o > 0) / abs(sum(o for o in outcomes if o < 0))) if any(o < 0 for o in outcomes) else 0
        return {
            'trades': len(outcomes),
            'win_rate_pct': round(win_rate, 1),
            'avg_win_pct': round(avg_win, 2),
            'avg_loss_pct': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
        }

    def _run_monte_carlo(self, trades, simulations=1000):
        outcomes = [t.get('outcome_7d_adjusted') or t.get('outcome_7d', 0) for t in trades if (t.get('outcome_7d_adjusted') is not None or t.get('outcome_7d') is not None)]
        if not outcomes:
            return {}
        vals = [t.get('outcome_7d_adjusted') if t.get('outcome_7d_adjusted') is not None else t.get('outcome_7d', 0) for t in trades if t.get('outcome_7d_adjusted') is not None or t.get('outcome_7d') is not None]
        sim_results = []
        for _ in range(simulations):
            sample = np.random.choice(vals, len(vals), replace=True)
            sim_results.append(np.mean(sample))
        return {
            'mean_expectancy': round(np.mean(sim_results), 2),
            '5th_percentile': round(np.percentile(sim_results, 5), 2),
            '95th_percentile': round(np.percentile(sim_results, 95), 2),
        }

    def get_optimization_suggestions(self, report: dict | None = None):
        if report is None:
            report = self.run_walk_forward_backtest()
        suggestions = []
        for tier, metrics in report.get('by_tier', {}).items():
            if metrics.get('win_rate_pct', 0) > 85:
                suggestions.append(f"🎯 Focus on {tier} — {metrics['win_rate_pct']}% win rate")
        if report.get('monte_carlo', {}).get('5th_percentile', 0) > 0:
            suggestions.append("✅ Positive expectancy even in worst 5% scenarios")
        if report.get('sharpe_passed'):
            suggestions.append("✅ Walk-forward Sharpe >1.0 across periods")
        return suggestions
