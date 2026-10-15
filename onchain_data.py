# onchain_data.py — Advanced On-Chain Metrics (CryptoQuant 2026)
# Includes: SOPR, LTH SOPR, exchange netflow, LTH supply, HODL signals, SOPR bands
import logging
import os
import requests
from typing import Dict, Any, Optional
from resilient_http import resilient_get, CircuitOpenError

logger = logging.getLogger(__name__)

class OnChainFetcher:
    def __init__(self):
        self.api_key = os.getenv('CRYPTOQUANT_API_KEY')
        self.base_url = "https://api.cryptoquant.com/v1"
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})
            logger.info("OnChainFetcher: Real CryptoQuant data enabled")
        else:
            logger.warning("OnChainFetcher: No API key - using fallback placeholders")

    def _sopr_band_signal(self, sopr: float, lth_sopr: float) -> str:
        """Derive band signal: profit_taking (both >1), capitulation (both <1), mixed, neutral."""
        if sopr > 1.02 and lth_sopr > 1.02:
            return 'profit_taking'
        if sopr < 0.98 and lth_sopr < 0.98:
            return 'capitulation'
        if sopr > 1.02 or lth_sopr > 1.02:
            return 'mixed_profit_taking'
        if sopr < 0.98 or lth_sopr < 0.98:
            return 'mixed_capitulation'
        return 'neutral'

    def get_advanced_metrics(self, coin_symbol: str) -> Dict[str, Any]:
        symbol = (coin_symbol or "").strip().lower()  # CryptoQuant uses lowercase

        result: Dict[str, Any] = {
            'sopr': 1.0,
            'lth_sopr': 1.0,
            'exchange_netflow_24h': 0.0,
            'lth_supply_change_24h_pct': 0.0,
            'realized_cap_hodl_signal': 'neutral',
            'realized_cap_hodl_waves': None,  # Optional: UTXO age bands if API available
            'whale_netflow': 'neutral',
            'smart_money_netflow': None,  # Same as exchange netflow or whale cluster if available
            'sopr_bands': {
                'short_term': 1.0,
                'long_term': 1.0,
                'band_signal': 'neutral',
            },
            'status': 'live' if self.api_key else 'fallback'
        }

        if not self.api_key:
            result['sopr_bands']['band_signal'] = 'neutral'
            return result

        try:
            # 1. Standard SOPR (short-term)
            resp = resilient_get(f"{self.base_url}/{symbol}/market-indicator/sopr?window=day&limit=1",
                                timeout=10, source="cryptoquant", session=self.session)
            if resp.ok:
                data = resp.json().get('data') or []
                if data:
                    result['sopr'] = round(data[0].get('value', 1.0), 3)

            # 2. LTH SOPR (long-term holder SOPR)
            resp = resilient_get(f"{self.base_url}/{symbol}/market-indicator/lth-sopr?window=day&limit=1",
                                timeout=10, source="cryptoquant", session=self.session)
            if resp.ok:
                data = resp.json().get('data') or []
                if data:
                    result['lth_sopr'] = round(data[0].get('value', 1.0), 3)

            # 3. Exchange Netflow (proxy for whale/smart money when no dedicated endpoint)
            resp = resilient_get(f"{self.base_url}/{symbol}/flow/exchange-netflow?window=day&limit=1",
                                timeout=10, source="cryptoquant", session=self.session)
            if resp.ok:
                data = resp.json().get('data') or []
                if data:
                    result['exchange_netflow_24h'] = round(data[0].get('value', 0.0), 2)

            # 4. LTH Supply Change
            resp = resilient_get(f"{self.base_url}/{symbol}/supply/lth-supply?window=day&limit=2",
                                timeout=10, source="cryptoquant", session=self.session)
            if resp.ok:
                data = resp.json().get('data') or []
                if len(data) >= 2:
                    current = data[0].get('value', 0)
                    previous = data[1].get('value', 0)
                    if previous != 0:
                        result['lth_supply_change_24h_pct'] = round((current - previous) / previous * 100, 2)

            # 5. Whale / Smart money: exchange netflow as proxy; label for prompts
            netflow = result['exchange_netflow_24h']
            result['whale_netflow'] = 'accumulation' if netflow < -500 else 'distribution' if netflow > 500 else 'neutral'
            result['smart_money_netflow'] = netflow

            # 6. Realized Cap HODL signal (LTH SOPR–based; HODL waves = maturation vs distribution)
            lth = result['lth_sopr']
            result['realized_cap_hodl_signal'] = (
                'strong_accumulation' if lth > 1.05 else
                'accumulation' if lth > 1.02 else
                'strong_distribution' if lth < 0.92 else
                'distribution' if lth < 0.95 else 'neutral'
            )
            # Optional: Realized Cap UTXO age bands (CryptoQuant path may vary by plan)
            try:
                rcap = resilient_get(f"{self.base_url}/{symbol}/network-indicator/realized-cap?window=day&limit=1",
                                    timeout=5, source="cryptoquant", session=self.session, max_retries=2)
                if rcap.ok:
                    rdata = rcap.json().get('data') or []
                    if rdata:
                        result['realized_cap_hodl_waves'] = {'realized_cap': rdata[0].get('value')}
            except Exception:
                pass

            # 7. SOPR bands (short vs long term)
            s = result['sopr']
            lt = result['lth_sopr']
            result['sopr_bands'] = {
                'short_term': s,
                'long_term': lt,
                'band_signal': self._sopr_band_signal(s, lt),
            }

        except Exception as e:
            logger.warning(f"On-chain API error for {symbol}: {e}")
            result['sopr_bands'] = {'short_term': result['sopr'], 'long_term': result['lth_sopr'], 'band_signal': 'neutral'}

        return result

    def get_metrics(self, coin_symbol: str) -> Dict[str, Any]:
        """Alias for backward compatibility; returns same shape as get_advanced_metrics."""
        return self.get_advanced_metrics(coin_symbol)
