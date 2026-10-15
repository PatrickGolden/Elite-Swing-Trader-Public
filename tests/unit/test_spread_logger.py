# spread_logger: best-effort by contract — a broken book response must never
# raise into the refresh loop, and a good one must record a sane row.

from unittest.mock import MagicMock, patch

import spread_logger


def _resp(payload, status=200):
    m = MagicMock()
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


def test_records_sane_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(spread_logger, "SPREAD_FILE", str(tmp_path / "s.jsonl"))
    book = {"bids": [["62376.16", "1", 1]], "asks": [["62376.17", "1", 1]]}
    with patch("resilient_http.resilient_get", return_value=_resp(book)):
        row = spread_logger.capture_spread_snapshot()
    assert row is not None
    assert row["bid"] == 62376.16 and row["ask"] == 62376.17
    assert 0 <= row["spread_pct"] < 1
    assert (tmp_path / "s.jsonl").exists()


def test_crossed_book_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(spread_logger, "SPREAD_FILE", str(tmp_path / "s.jsonl"))
    book = {"bids": [["100", "1", 1]], "asks": [["99", "1", 1]]}  # ask < bid: bad data
    with patch("resilient_http.resilient_get", return_value=_resp(book)):
        assert spread_logger.capture_spread_snapshot() is None
    assert not (tmp_path / "s.jsonl").exists()


def test_alt_product_recorded_under_its_own_name(tmp_path, monkeypatch):
    # v2.0: alt paper fills assume 0.05% slip on every coin; the only way to check that
    # is to record alt books too — tagged by product so BTC history stays separable.
    monkeypatch.setattr(spread_logger, "SPREAD_FILE", str(tmp_path / "s.jsonl"))
    book = {"bids": [["1.000", "1", 1]], "asks": [["1.010", "1", 1]]}
    with patch("resilient_http.resilient_get", return_value=_resp(book)) as g:
        row = spread_logger.capture_spread_snapshot("SOL-USD")
    assert row["product"] == "SOL-USD"
    assert "SOL-USD" in g.call_args[0][0]
    assert 0.9 < row["spread_pct"] < 1.1


def test_network_failure_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(spread_logger, "SPREAD_FILE", str(tmp_path / "s.jsonl"))
    with patch("resilient_http.resilient_get", side_effect=RuntimeError("boom")):
        assert spread_logger.capture_spread_snapshot() is None  # no exception escapes
