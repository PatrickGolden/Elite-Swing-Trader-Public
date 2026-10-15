# tests/test_reconciliation.py — Dust filtering, external positions detection, clean status
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reconciliation import reconcile_holdings


class TestDustFiltering:
    """Positions below dust_threshold_usd are filtered out."""

    def test_dust_filtered_external(self):
        """External position below dust threshold is excluded."""
        exchange = {'DUST': 0.001}  # tiny amount
        journal = {}
        prices = {'DUST': 0.50}  # 0.0005 USD
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert result['status'] == 'clean'
        assert len(result['external']) == 0
        assert len(result['discrepancies']) == 0

    def test_dust_filtered_journal_only(self):
        """Journal-only position below dust is excluded."""
        exchange = {}
        journal = {'TINY': 0.0001}
        prices = {'TINY': 10}  # 0.001 USD
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert result['status'] == 'clean'
        assert len(result['in_journal_only']) == 0

    def test_above_dust_included(self):
        """Position above dust threshold is included."""
        exchange = {'BTC': 0.0001}
        journal = {}
        prices = {'BTC': 50000}  # 5 USD
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert len(result['external']) == 1
        assert result['external'][0]['symbol'] == 'BTC'
        assert result['status'] == 'discrepancy_detected'


class TestExternalPositionsDetection:
    """On exchange but not in journal = external."""

    def test_external_detected(self):
        """Exchange holding not in journal is external."""
        exchange = {'ETH': 0.5, 'BTC': 0.01}
        journal = {'BTC': 0.01}
        prices = {'ETH': 3000, 'BTC': 45000}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert len(result['external']) == 1
        assert result['external'][0]['symbol'] == 'ETH'
        assert result['external'][0]['exchange_quantity'] == 0.5
        assert result['status'] == 'discrepancy_detected'

    def test_external_unknown_price_surfaced(self):
        """External with unknown price (no price_by_symbol) is surfaced as external (conservative — don't silently hide)."""
        exchange = {'UNKNOWN': 1.0}
        journal = {}
        prices = {}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert len(result['external']) == 1
        assert result['external'][0]['symbol'] == 'UNKNOWN'
        assert result['status'] == 'discrepancy_detected'


class TestInJournalOnlyDetection:
    """In journal but not on exchange = in_journal_only."""

    def test_journal_only_detected(self):
        """Journal position not on exchange is in_journal_only."""
        exchange = {'BTC': 0.01}
        journal = {'BTC': 0.01, 'SOL': 10}
        prices = {'BTC': 45000, 'SOL': 100}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert len(result['in_journal_only']) == 1
        assert result['in_journal_only'][0]['symbol'] == 'SOL'
        assert result['in_journal_only'][0]['journal_quantity'] == 10
        assert result['status'] == 'discrepancy_detected'


class TestCleanStatus:
    """No discrepancies = clean status."""

    def test_clean_when_matched(self):
        """Matching holdings yield clean status."""
        exchange = {'BTC': 0.01, 'ETH': 0.5}
        journal = {'BTC': 0.01, 'ETH': 0.5}
        prices = {'BTC': 45000, 'ETH': 3000}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert result['status'] == 'clean'
        assert len(result['discrepancies']) == 0

    def test_clean_when_empty(self):
        """Empty both sides yields clean."""
        result = reconcile_holdings({}, {}, dust_threshold_usd=1.0, price_by_symbol={})
        assert result['status'] == 'clean'
        assert result['discrepancies'] == []

    def test_discrepancy_detected_when_mismatch(self):
        """Exchange has coin not in journal yields discrepancy_detected."""
        exchange = {'BTC': 0.02}
        journal = {}
        prices = {'BTC': 45000}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert result['status'] == 'discrepancy_detected'
        assert len(result['external']) == 1
        assert result['external'][0]['symbol'] == 'BTC'


class TestQuantityMismatch:
    """Same symbol on both sides but different quantities → quantity_mismatch with delta."""

    def test_quantity_mismatch_detected_with_delta(self):
        """Exchange 0.01 BTC, journal 0.02 BTC → discrepancy, delta = 0.01."""
        exchange = {'BTC': 0.01}
        journal = {'BTC': 0.02}
        prices = {'BTC': 45000}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert result['status'] == 'discrepancy_detected'
        assert len(result['quantity_mismatch']) == 1
        m = result['quantity_mismatch'][0]
        assert m['symbol'] == 'BTC'
        assert m['exchange_quantity'] == 0.01
        assert m['journal_quantity'] == 0.02
        assert m['delta'] == 0.01
        assert m['type'] == 'quantity_mismatch'
        assert len(result['external']) == 0
        assert len(result['in_journal_only']) == 0
        assert len(result['discrepancies']) == 1

    def test_quantity_mismatch_journal_less_than_exchange(self):
        """Exchange 0.05 BTC, journal 0.03 BTC → delta = -0.02."""
        exchange = {'BTC': 0.05}
        journal = {'BTC': 0.03}
        prices = {'BTC': 50000}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert len(result['quantity_mismatch']) == 1
        assert result['quantity_mismatch'][0]['delta'] == -0.02


class TestMultipleExternalCoins:
    """Three or more coins on exchange not in journal → all in external."""

    def test_three_external_coins(self):
        """ETH, SOL, AVAX on exchange, only BTC in journal → 3 externals."""
        exchange = {'BTC': 0.01, 'ETH': 1.0, 'SOL': 50, 'AVAX': 20}
        journal = {'BTC': 0.01}
        prices = {'BTC': 45000, 'ETH': 3000, 'SOL': 100, 'AVAX': 35}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert result['status'] == 'discrepancy_detected'
        assert len(result['external']) == 3
        symbols = {e['symbol'] for e in result['external']}
        assert symbols == {'ETH', 'SOL', 'AVAX'}
        assert len(result['discrepancies']) == 3


class TestStablecoinExclusion:
    """USDC/USDT on exchange not in journal can be excluded from external."""

    def test_stablecoin_excluded_from_external(self):
        """USDC and USDT on exchange, exclude_symbols → not in external."""
        exchange = {'BTC': 0.01, 'USDC': 1000.0, 'USDT': 500.0}
        journal = {'BTC': 0.01}
        prices = {'BTC': 45000, 'USDC': 1.0, 'USDT': 1.0}
        result = reconcile_holdings(
            exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices,
            exclude_symbols={'USDC', 'USDT'},
        )
        assert result['status'] == 'clean'
        assert len(result['external']) == 0
        assert result['discrepancies'] == []

    def test_stablecoin_without_exclude_flagged_as_external(self):
        """Without exclude_symbols, USDC on exchange not in journal is external."""
        exchange = {'BTC': 0.01, 'USDC': 1000.0}
        journal = {'BTC': 0.01}
        prices = {'BTC': 45000, 'USDC': 1.0}
        result = reconcile_holdings(exchange, journal, dust_threshold_usd=1.0, price_by_symbol=prices)
        assert len(result['external']) == 1
        assert result['external'][0]['symbol'] == 'USDC'
        assert result['status'] == 'discrepancy_detected'
