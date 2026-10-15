# Tests for data-collection freeze: live-only gate, paper excluded from lift.

import json

import pytest

import recursive_optimizer as ro


@pytest.fixture
def _isolate_thresholds(monkeypatch, tmp_path):
    lt_path = tmp_path / "live_thresholds.json"
    monkeypatch.setattr("thresholds.LIVE_THRESHOLDS_PATH", lt_path)
    yield lt_path


def _write_freeze(lt_path, *, raw=100, instrumented=100):
    lt_path.write_text(
        json.dumps(
            {
                "freeze_thresholds": True,
                "freeze_until_trades": raw,
                "freeze_until_instrumented_trades": instrumented,
            }
        ),
        encoding="utf-8",
    )


def test_paper_excluded_from_freeze_gate(_isolate_thresholds, monkeypatch):
    _write_freeze(_isolate_thresholds, raw=100, instrumented=100)

    def _positions(**_kwargs):
        return [
            {"status": "CLOSED", "source": "paper_trade", "is_test": False},
            {"status": "CLOSED", "source": "paper_trade", "is_test": False},
            {"status": "CLOSED", "source": None, "is_test": False, "fear_greed": 42},
        ]

    monkeypatch.setattr("trade_journal.get_all_positions", _positions)
    st = ro.get_data_collection_status()
    assert st["frozen"] is True
    assert st["live_closed"] == 1
    assert st["paper_closed"] == 2
    assert st["instrumented_live"] == 1


def test_instrumented_gate_does_not_lift_before_live_target(_isolate_thresholds, monkeypatch):
    """71 instrumented live must not auto-lift when both gates target 100 (cycle 42 config)."""
    _write_freeze(_isolate_thresholds, raw=100, instrumented=100)

    def _positions(**_kwargs):
        return [
            {
                "status": "CLOSED",
                "source": None,
                "is_test": False,
                "fear_greed": 40,
            }
            for _ in range(71)
        ]

    monkeypatch.setattr("trade_journal.get_all_positions", _positions)
    st = ro.get_data_collection_status()
    assert st["frozen"] is True
    assert st["live_closed"] == 71
    assert _isolate_thresholds.read_text(encoding="utf-8").count("freeze_thresholds") > 0


def test_reaching_target_does_not_auto_lift_freeze(_isolate_thresholds, monkeypatch):
    """STRATEGY.md 2026-06-11: a status read must never mutate config. At/over target the
    freeze stays active and the file is untouched; the status only reports lift_eligible."""
    _write_freeze(_isolate_thresholds, raw=100, instrumented=100)
    before = _isolate_thresholds.read_text(encoding="utf-8")

    def _positions(**_kwargs):
        return [
            {"status": "CLOSED", "source": None, "is_test": False, "fear_greed": 40}
            for _ in range(100)
        ]

    monkeypatch.setattr("trade_journal.get_all_positions", _positions)
    st = ro.get_data_collection_status()
    assert st["frozen"] is True
    assert st["live_closed"] == 100
    assert st.get("lift_eligible") is True
    assert _isolate_thresholds.read_text(encoding="utf-8") == before


def test_format_report_html_shows_live_and_paper_counts():
    html = ro.format_report_html(
        {
            "data_collection_mode": True,
            "data_collection_closed_trades": 71,
            "data_collection_target": 100,
            "data_collection_paper_closed": 383,
            "data_collection_instrumented_live": 71,
            "data_collection_instrumented_target": 100,
            "snapshot": {
                "cycle": 42,
                "date": "2026-05-23",
                "regime": "BULL",
                "system_closed": 71,
                "system_open": 2,
                "win_rate_7d": 0,
                "win_rate_30d": 25,
            },
            "recommendations": [],
            "pending": [],
            "applied": [],
            "evaluations": [],
            "due_followups": [],
        }
    )
    assert "71/100" in html
    assert "383" in html
    assert "Paper closes" in html
    assert "excluded" in html
