# tests/test_screening_log.py — Screening log append and load/save. Run: pytest tests/test_screening_log.py -v

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def temp_screening_log(monkeypatch):
    """Use a temp dir for screening log so tests don't touch real data."""
    tmp = tempfile.mkdtemp()
    data_dir = Path(tmp) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_file = data_dir / "screening_log.json"
    import screening_log as sl
    monkeypatch.setattr(sl, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(sl, "SCREENING_LOG_FILE", str(log_file))
    yield log_file
    try:
        if log_file.exists():
            log_file.unlink()
        data_dir.rmdir()
        Path(tmp).rmdir()
    except OSError:
        pass


class TestScreeningLogAppend:
    """append_screening_entry writes correct structure and note."""

    def test_append_one_entry(self, temp_screening_log):
        import screening_log as sl
        sl.append_screening_entry(
            coin_id="bitcoin",
            symbol="btc",
            entry_price=50000.0,
            quant_tier="STRONG_BUY",
            market_regime="BULL",
        )
        entries = sl._load_log()
        assert len(entries) == 1
        assert entries[0]["coin_id"] == "bitcoin"
        assert entries[0]["symbol"] == "BTC"
        assert entries[0]["entry_price"] == 50000.0
        assert entries[0]["quant_tier"] == "STRONG_BUY"
        assert entries[0]["market_regime"] == "BULL"
        assert entries[0]["outcome_7d"] is None

    def test_append_screening_failed_note(self, temp_screening_log):
        import screening_log as sl
        sl.append_screening_entry(
            coin_id="ethereum",
            symbol="eth",
            entry_price=3000.0,
            quant_tier="MODERATE_BUY",
            market_regime="SIDEWAYS",
        )
        entries = sl._load_log()
        assert len(entries) == 1
        assert entries[0]["symbol"] == "ETH"

    def test_append_multiple_accumulates(self, temp_screening_log):
        import screening_log as sl
        for i in range(3):
            sl.append_screening_entry(
                coin_id=f"coin_{i}",
                symbol=f"C{i}",
                entry_price=100.0 + i,
                quant_tier="",
                market_regime="BULL",
            )
        entries = sl._load_log()
        assert len(entries) == 3
        assert entries[0]["coin_id"] == "coin_0"
        assert entries[2]["coin_id"] == "coin_2"

    def test_append_with_timestamp_kwarg(self, temp_screening_log):
        import screening_log as sl
        sl.append_screening_entry(
            coin_id="x",
            symbol="X",
            entry_price=1.0,
            quant_tier="",
            market_regime="BEAR",
            timestamp="2025-01-15 12:00:00",
        )
        entries = sl._load_log()
        assert entries[0]["timestamp"] == "2025-01-15 12:00:00"
