# tests/unit/test_social_signals.py — Trending bonus, volume spike score, score_tokens integration,
# social_backfill volume computation, missed_opportunity screening filter fix.

import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scorer import _trending_bonus, _volume_spike_score, score_coin, score_tokens


# ---------------------------------------------------------------------------
# 1. _trending_bonus
# ---------------------------------------------------------------------------

class TestTrendingBonus:
    def test_trending_true_returns_6(self):
        assert _trending_bonus(True) == 6

    def test_trending_false_returns_0(self):
        assert _trending_bonus(False) == 0

    def test_trending_zero_value_is_false(self):
        assert _trending_bonus(0) == 0


# ---------------------------------------------------------------------------
# 2. _volume_spike_score
# ---------------------------------------------------------------------------

class TestVolumeSpikeScore:
    def test_no_spike_returns_0(self):
        assert _volume_spike_score(1.0) == 0

    def test_slight_spike_1_2(self):
        assert _volume_spike_score(1.2) == 1

    def test_moderate_spike_1_5(self):
        assert _volume_spike_score(1.5) == 3

    def test_double_volume(self):
        assert _volume_spike_score(2.0) == 5

    def test_triple_volume(self):
        assert _volume_spike_score(3.0) == 6

    def test_quad_plus_volume(self):
        assert _volume_spike_score(4.0) == 7
        assert _volume_spike_score(10.0) == 7

    def test_below_1_returns_0(self):
        assert _volume_spike_score(0.5) == 0


# ---------------------------------------------------------------------------
# 3. score_coin — trending and volume spike integration
# ---------------------------------------------------------------------------

def _minimal_coin():
    return {
        'id': 'bitcoin',
        'name': 'Bitcoin',
        'current_price': 50000,
        'total_volume': 30_000_000_000,
        'market_cap': 1_000_000_000_000,
        'price_change_percentage_24h': 1.0,
        'price_change_percentage_1h_in_currency': 0.5,
        'price_change_percentage_7d_in_currency': 3.0,
        'price_change_percentage_30d_in_currency': 5.0,
        'indicators': {
            'rsi': 48,
            'bb_pos': 35,
            'atr_pct': 2.0,
            'momentum': 4.0,
            'macd': 'neutral',
        },
    }


class TestScoreCoinSocialSignals:
    def test_trending_coin_scores_higher(self):
        coin = _minimal_coin()
        base = score_coin(coin, fg_value=50, news_score=0.0, is_trending=False)
        trending = score_coin(coin, fg_value=50, news_score=0.0, is_trending=True)
        assert trending['score'] > base['score']
        assert trending['score'] - base['score'] == 6

    def test_volume_spike_adds_score(self):
        coin = _minimal_coin()
        base = score_coin(coin, fg_value=50, news_score=0.0, volume_spike_ratio=1.0)
        spike = score_coin(coin, fg_value=50, news_score=0.0, volume_spike_ratio=3.0)
        assert spike['score'] > base['score']
        assert spike['score'] - base['score'] == 6

    def test_breakdown_includes_trending_and_spike(self):
        coin = _minimal_coin()
        result = score_coin(coin, fg_value=50, news_score=0.0, is_trending=True, volume_spike_ratio=2.0)
        assert 'trending' in result['breakdown']
        assert 'volume_spike' in result['breakdown']
        assert result['breakdown']['trending'] == 6
        assert result['breakdown']['volume_spike'] == 5

    def test_score_capped_at_100(self):
        coin = _minimal_coin()
        # Force everything high
        coin['indicators']['rsi'] = 35
        coin['indicators']['bb_pos'] = 15
        coin['indicators']['momentum'] = 25
        coin['indicators']['macd'] = 'bullish'
        result = score_coin(coin, fg_value=20, news_score=1.0, is_trending=True, volume_spike_ratio=5.0)
        assert result['score'] <= 100

    def test_default_params_unchanged_behavior(self):
        coin = _minimal_coin()
        # Calling without new params should give same result as is_trending=False, spike=1.0
        result_default = score_coin(coin, fg_value=50, news_score=0.0)
        result_explicit = score_coin(coin, fg_value=50, news_score=0.0, is_trending=False, volume_spike_ratio=1.0)
        assert result_default['score'] == result_explicit['score']


# ---------------------------------------------------------------------------
# 4. score_tokens — picks up trending_coin_ids from data dict
# ---------------------------------------------------------------------------

class TestScoreTokensTrending:
    def test_trending_coin_gets_higher_score(self):
        coin = _minimal_coin()
        data_base = {
            'coins': [dict(coin)],
            'fear_greed': {'value': 50},
            'sentiment': {'score': 0.0},
            'trending_coin_ids': [],
        }
        data_trending = {
            'coins': [dict(coin)],
            'fear_greed': {'value': 50},
            'sentiment': {'score': 0.0},
            'trending_coin_ids': ['bitcoin'],
        }
        score_tokens(data_base)
        score_tokens(data_trending)
        base_score = data_base['coins'][0]['score']
        trending_score = data_trending['coins'][0]['score']
        assert trending_score == base_score + 6

    def test_missing_trending_ids_does_not_crash(self):
        coin = _minimal_coin()
        data = {
            'coins': [dict(coin)],
            'fear_greed': {'value': 50},
            'sentiment': {'score': 0.0},
        }
        score_tokens(data)
        assert 'score' in data['coins'][0]

    def test_breakdown_keys_present(self):
        coin = _minimal_coin()
        data = {
            'coins': [dict(coin)],
            'fear_greed': {'value': 50},
            'sentiment': {'score': 0.0},
            'trending_coin_ids': ['bitcoin'],
        }
        score_tokens(data)
        bd = data['coins'][0]['breakdown']
        assert 'trending' in bd
        assert 'volume_spike' in bd


# ---------------------------------------------------------------------------
# 5. social_backfill — volume spike computation
# ---------------------------------------------------------------------------

class TestSocialBackfillVolSpike:
    """Tests for _compute_spike_ratio in ml/social_backfill.py."""

    def test_double_volume_gives_2x_ratio(self):
        from ml.social_backfill import _compute_spike_ratio
        date = "2026-03-10"
        # 7 prior days all at 1.0B, target day at 2.0B
        vol = {(datetime.strptime(date, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d"): 1e9 for i in range(1, 8)}
        vol[date] = 2e9
        ratio = _compute_spike_ratio(date, vol)
        assert abs(ratio - 2.0) < 0.01

    def test_insufficient_prior_data_returns_1(self):
        from ml.social_backfill import _compute_spike_ratio
        # Only 2 prior days — below the 3-day minimum
        vol = {"2026-03-10": 2e9, "2026-03-09": 1e9, "2026-03-08": 1e9}
        ratio = _compute_spike_ratio("2026-03-10", vol)
        assert ratio == 1.0

    def test_no_data_for_date_returns_1(self):
        from ml.social_backfill import _compute_spike_ratio
        ratio = _compute_spike_ratio("2026-03-10", {})
        assert ratio == 1.0

    def test_zero_prior_volume_returns_1(self):
        from ml.social_backfill import _compute_spike_ratio
        date = "2026-03-10"
        vol = {date: 1e9}
        for i in range(1, 8):
            d = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
            vol[d] = 0
        ratio = _compute_spike_ratio(date, vol)
        assert ratio == 1.0

    def test_run_backfill_skips_no_outcome(self, tmp_path):
        """Entries without outcome_7d should not be fetched."""
        recs = [
            {"coin_id": "bitcoin", "timestamp": "2026-03-01 00:00:00",
             "outcome_7d": None, "entry_price": 50000},
        ]
        recs_path = tmp_path / "recs.json"
        recs_path.write_text(json.dumps(recs))
        import ml.social_backfill as sb
        from unittest.mock import patch as _patch
        with _patch.object(sb, "RECOMMENDATIONS_FILE", recs_path):
            stats = sb.run()
        assert stats["entries_updated"] == 0
        assert stats["coins_fetched"] == 0

    def test_run_backfill_skips_already_backfilled(self, tmp_path):
        """Entries that already have volume_spike_ratio are skipped."""
        recs = [
            {"coin_id": "bitcoin", "timestamp": "2026-03-01 00:00:00",
             "outcome_7d": 5.0, "entry_price": 50000, "volume_spike_ratio": 1.5},
        ]
        recs_path = tmp_path / "recs.json"
        recs_path.write_text(json.dumps(recs))
        import ml.social_backfill as sb
        with patch.object(sb, "RECOMMENDATIONS_FILE", recs_path):
            stats = sb.run()
        assert stats["entries_updated"] == 0


# ---------------------------------------------------------------------------
# 6. missed_opportunity — screening filter now uses quant_tier
# ---------------------------------------------------------------------------

class TestMissedOpportunityScreeningFilter:
    def _build_screening_entry(self, coin_id, quant_tier, outcome_7d, days_ago=10):
        ts = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "coin_id": coin_id,
            "symbol": coin_id.upper()[:4],
            "timestamp": ts,
            "entry_price": 100.0,
            "quant_tier": quant_tier,
            "market_regime": "BEAR",
            "outcome_7d": outcome_7d,
        }

    def test_hold_entry_with_gain_is_surfaced(self, tmp_path, monkeypatch):
        import ml.missed_opportunity as mo
        recs_file = tmp_path / "recs.json"
        screen_file = tmp_path / "screening_log.json"
        recs_file.write_text("[]")
        screen_file.write_text(json.dumps([
            self._build_screening_entry("bitcoin", "HOLD", 25.0),
        ]))
        monkeypatch.setattr(mo, "_load_json", lambda p: json.loads(Path(p).read_text()) if Path(p).exists() else [])
        from ml.config import RECOMMENDATIONS_FILE, SCREENING_LOG_FILE
        import ml.config as mconfig
        monkeypatch.setattr(mconfig, "RECOMMENDATIONS_FILE", recs_file)
        monkeypatch.setattr(mconfig, "SCREENING_LOG_FILE", screen_file)

        from unittest.mock import patch as _patch
        with _patch("ml.missed_opportunity.RECOMMENDATIONS_FILE", str(recs_file)), \
             _patch("ml.missed_opportunity.SCREENING_LOG_FILE", str(screen_file)):
            result = mo.analyze(threshold_pct=5.0)

        assert result["from_screening"]["total"] >= 1

    def test_strong_buy_entry_not_surfaced(self, tmp_path, monkeypatch):
        import ml.missed_opportunity as mo
        recs_file = tmp_path / "recs.json"
        screen_file = tmp_path / "screening_log.json"
        recs_file.write_text("[]")
        screen_file.write_text(json.dumps([
            self._build_screening_entry("bitcoin", "STRONG_BUY", 25.0),
        ]))
        with patch("ml.missed_opportunity.RECOMMENDATIONS_FILE", str(recs_file)), \
             patch("ml.missed_opportunity.SCREENING_LOG_FILE", str(screen_file)):
            result = mo.analyze(threshold_pct=5.0)
        # STRONG_BUY was acted on — should NOT count as missed
        assert result["from_screening"]["total"] == 0
