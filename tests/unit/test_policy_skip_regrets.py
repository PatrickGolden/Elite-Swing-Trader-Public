# tests/unit/test_policy_skip_regrets.py — Shadow skip rows → ML policy_skip_regrets.

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def shadow_file(tmp_path):
    shadow = tmp_path / "shadow_skips.jsonl"
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    shadow.write_text(
        json.dumps({
            "ts": old_ts,
            "coin_id": "near",
            "symbol": "NEAR",
            "entry_price": 5.0,
            "forward_return_7d": 12.5,
            "live_skip_reason": "policy_no_auto_exec",
            "quant_tier": "MODERATE_BUY",
            "market_regime": "SIDEWAYS",
        }) + "\n",
        encoding="utf-8",
    )
    return shadow


def test_policy_skip_regrets_from_shadow(shadow_file):
    recs = shadow_file.parent / "recs.json"
    screen = shadow_file.parent / "screen.json"
    recs.write_text("[]", encoding="utf-8")
    screen.write_text("[]", encoding="utf-8")
    with patch("paper_evidence.SHADOW_SKIPS_FILE", shadow_file), \
         patch("ml.missed_opportunity.RECOMMENDATIONS_FILE", recs), \
         patch("ml.missed_opportunity.SCREENING_LOG_FILE", screen):
        from ml.missed_opportunity import analyze
        result = analyze(threshold_pct=5.0)
    policy = result.get("from_policy_skips") or {}
    assert policy.get("policy_skip_regrets", 0) >= 1
    assert policy.get("total", 0) >= 1
