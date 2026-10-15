"""Tests for ml/outcome_backfill.py — horizon accuracy."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_recs(tmp_path):
    recs_file = tmp_path / "ai_recommendations_v2.json"
    screening_file = tmp_path / "screening_log.json"
    recs_file.write_text("[]")
    screening_file.write_text("[]")
    return recs_file, screening_file


def _make_rec(days_ago, entry_price, coin_id="bitcoin"):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "timestamp": ts,
        "entry_price": entry_price,
        "coin_id": coin_id,
        "outcome_7d": None,
        "outcome_3d": None,
        "outcome_14d": None,
    }


def test_backfill_does_not_copy_7d_into_3d_14d(tmp_recs):
    """outcome_3d and outcome_14d must NOT be set from a single current price."""
    recs_file, screening_file = tmp_recs
    rec = _make_rec(days_ago=10, entry_price=100.0, coin_id="bitcoin")
    recs_file.write_text(json.dumps([rec]))

    with patch("ml.outcome_backfill.RECOMMENDATIONS_FILE", str(recs_file)), \
         patch("ml.outcome_backfill.SCREENING_LOG_FILE", str(screening_file)):
        from ml.outcome_backfill import run_backfill
        run_backfill(lambda ids: {"bitcoin": 110.0})

    updated = json.loads(recs_file.read_text())
    assert updated[0]["outcome_7d"] is not None, "outcome_7d should be set"
    assert updated[0]["outcome_3d"] is None, "outcome_3d must NOT be copied from 7d"
    assert updated[0]["outcome_14d"] is None, "outcome_14d must NOT be copied from 7d"


def test_backfill_adds_mtm_field(tmp_recs):
    """Backfill should add outcome_mtm_pct as the honest mark-to-market return."""
    recs_file, screening_file = tmp_recs
    rec = _make_rec(days_ago=10, entry_price=100.0, coin_id="bitcoin")
    recs_file.write_text(json.dumps([rec]))

    with patch("ml.outcome_backfill.RECOMMENDATIONS_FILE", str(recs_file)), \
         patch("ml.outcome_backfill.SCREENING_LOG_FILE", str(screening_file)):
        from ml.outcome_backfill import run_backfill
        run_backfill(lambda ids: {"bitcoin": 110.0})

    updated = json.loads(recs_file.read_text())
    assert updated[0]["outcome_mtm_pct"] == 10.0


def test_backfill_skips_recent_records(tmp_recs):
    """Records younger than 7 days should not be backfilled."""
    recs_file, screening_file = tmp_recs
    rec = _make_rec(days_ago=3, entry_price=100.0, coin_id="bitcoin")
    recs_file.write_text(json.dumps([rec]))

    with patch("ml.outcome_backfill.RECOMMENDATIONS_FILE", str(recs_file)), \
         patch("ml.outcome_backfill.SCREENING_LOG_FILE", str(screening_file)):
        from ml.outcome_backfill import run_backfill
        stats = run_backfill(lambda ids: {"bitcoin": 150.0})

    updated = json.loads(recs_file.read_text())
    assert updated[0]["outcome_7d"] is None
    assert stats["recommendations_updated"] == 0
