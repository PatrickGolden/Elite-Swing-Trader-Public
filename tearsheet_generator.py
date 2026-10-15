# tearsheet_generator.py — Generate institutional-grade tearsheets from trade journal data.
# Uses quantstats for Sharpe, Sortino, Calmar, max drawdown, monthly returns heatmap, etc.
# Compares strategy returns vs BTC buy-and-hold benchmark.
# Output: data/tearsheets/*.html

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path(os.path.dirname(__file__))
DATA_DIR = _PROJECT_DIR / 'data'
OUTPUT_DIR = DATA_DIR / 'tearsheets'


def _positions_to_daily_returns(positions: List[Dict]) -> Any:
    """Convert closed positions to a daily returns series.

    Groups by exit_date, sums pnl_pct per day, fills gaps with 0.
    Returns a pandas Series indexed by date, or None if no data.
    """
    import pandas as pd

    records = []
    for p in positions:
        exit_time = p.get('exit_time')
        pnl_pct = p.get('pnl_pct')
        if exit_time is None or pnl_pct is None:
            continue
        try:
            date_str = str(exit_time)[:10]
            date = pd.Timestamp(date_str)
            records.append({'date': date, 'return': float(pnl_pct) / 100.0})
        except (ValueError, TypeError):
            continue

    if not records:
        return None

    df = pd.DataFrame(records)
    daily = df.groupby('date')['return'].sum()
    daily = daily.sort_index()

    # Fill gaps with 0 (no trades = no return)
    full_idx = pd.date_range(daily.index.min(), daily.index.max(), freq='D')
    daily = daily.reindex(full_idx, fill_value=0.0)
    daily.index.name = None
    daily.name = 'Strategy'
    return daily


def _fetch_btc_benchmark(start_date: str, end_date: str) -> Any:
    """Fetch BTC daily returns for benchmark comparison.

    Uses CoinGecko historical prices via historical_replay cache.
    Returns pandas Series of daily returns, or None on failure.
    """
    import pandas as pd

    try:
        from historical_replay import fetch_historical_prices
    except ImportError:
        logger.warning("[tearsheet] historical_replay not available for benchmark")
        return None

    # Fetch enough days to cover the range
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    days = (end - start).days + 30  # buffer for lookback
    days = min(days, 365)

    try:
        prices = fetch_historical_prices('bitcoin', days=days)
    except Exception as e:
        logger.warning("[tearsheet] Failed to fetch BTC benchmark: %s", e)
        return None

    if not prices:
        return None

    # Convert to daily prices
    records = []
    for ts_ms, price, _ in prices:
        date = pd.Timestamp(ts_ms, unit='ms').normalize()
        records.append({'date': date, 'price': price})

    df = pd.DataFrame(records)
    daily_prices = df.groupby('date')['price'].last().sort_index()

    # Convert to returns
    btc_returns = daily_prices.pct_change().dropna()
    btc_returns.name = 'BTC'

    # Align to strategy date range
    btc_returns = btc_returns[start_date:end_date]
    return btc_returns if len(btc_returns) > 0 else None


def generate_tearsheet(
    positions: List[Dict],
    name: str = 'full',
    output_dir: Optional[Path] = None,
    include_benchmark: bool = True,
) -> Dict[str, Any]:
    """Generate a quantstats HTML tearsheet from closed positions.

    Returns dict with path, metrics summary, or error.
    """
    output_dir = output_dir or OUTPUT_DIR

    try:
        import quantstats as qs
        import pandas as pd
    except ImportError as e:
        return {'success': False, 'reason': f'missing dependency: {e}'}

    returns = _positions_to_daily_returns(positions)
    if returns is None or len(returns) < 2:
        return {'success': False, 'reason': 'insufficient position data for tearsheet'}

    # Fetch BTC benchmark
    benchmark = None
    if include_benchmark:
        start_date = str(returns.index.min().date())
        end_date = str(returns.index.max().date())
        benchmark = _fetch_btc_benchmark(start_date, end_date)
        if benchmark is not None:
            # Align benchmark to strategy dates
            common_idx = returns.index.intersection(benchmark.index)
            if len(common_idx) > 0:
                benchmark = benchmark.reindex(returns.index, fill_value=0.0)
            else:
                benchmark = None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f'{name}.html'

    try:
        qs.reports.html(
            returns,
            benchmark=benchmark,
            title=f'Swing Trader — {name.replace("_", " ").title()}',
            output=str(output_path),
        )
    except Exception as e:
        logger.warning("[tearsheet] quantstats report generation failed: %s", e)
        return {'success': False, 'reason': f'report generation failed: {e}'}

    # Compute key metrics for summary
    try:
        metrics = {
            'total_return_pct': round(float(qs.stats.comp(returns) * 100), 2),
            'sharpe': round(float(qs.stats.sharpe(returns)), 3),
            'sortino': round(float(qs.stats.sortino(returns)), 3),
            'max_drawdown_pct': round(float(qs.stats.max_drawdown(returns) * 100), 2),
            'win_rate_pct': round(float(qs.stats.win_rate(returns) * 100), 1),
            'n_days': len(returns),
        }
    except Exception:
        metrics = {}

    return {
        'success': True,
        'path': str(output_path),
        'name': name,
        'metrics': metrics,
        'n_positions': len(positions),
    }


def generate_filtered_tearsheets(
    positions: Optional[List[Dict]] = None,
    output_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Generate tearsheets filtered by regime and quant_path.

    Returns list of results from generate_tearsheet().
    """
    if positions is None:
        try:
            from trade_journal import get_closed_positions
            positions = get_closed_positions(exclude_test=True)
        except ImportError:
            return [{'success': False, 'reason': 'trade_journal not available'}]

    results = []

    # Full tearsheet
    results.append(generate_tearsheet(positions, name='full', output_dir=output_dir))

    # By regime
    for regime in ('BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE'):
        filtered = [p for p in positions
                    if (p.get('market_regime') or p.get('regime') or '').upper() == regime]
        if len(filtered) >= 5:
            results.append(generate_tearsheet(
                filtered, name=f'regime_{regime.lower()}',
                output_dir=output_dir, include_benchmark=False,
            ))

    # By quant_path
    paths_seen = set()
    for p in positions:
        qp = p.get('quant_path')
        if qp:
            paths_seen.add(qp)
    for qp in sorted(paths_seen):
        filtered = [p for p in positions if p.get('quant_path') == qp]
        if len(filtered) >= 5:
            results.append(generate_tearsheet(
                filtered, name=f'path_{qp}',
                output_dir=output_dir, include_benchmark=False,
            ))

    return results


def generate_all_tearsheets() -> Dict[str, Any]:
    """CLI / scheduler entry point. Generates all tearsheets from live trade journal."""
    try:
        from trade_journal import get_closed_positions
        positions = get_closed_positions(exclude_test=True)
    except ImportError:
        return {'success': False, 'reason': 'trade_journal not available'}

    results = generate_filtered_tearsheets(positions)
    successful = [r for r in results if r.get('success')]
    failed = [r for r in results if not r.get('success')]

    logger.info("[tearsheet] Generated %d tearsheets (%d failed)", len(successful), len(failed))
    return {
        'success': len(successful) > 0,
        'generated': len(successful),
        'failed': len(failed),
        'results': results,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = generate_all_tearsheets()
    if result.get('success'):
        print(f"Generated {result['generated']} tearsheets")
        for r in result.get('results', []):
            if r.get('success'):
                print(f"  {r['name']}: {r.get('metrics', {})}")
    else:
        print(f"Tearsheet generation failed: {result.get('reason')}")
