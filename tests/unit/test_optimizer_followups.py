# Tests for optimizer followup reminder system.

import pytest

import recursive_optimizer as ro


@pytest.fixture(autouse=True)
def _isolate_followups_file(monkeypatch, tmp_path):
    """Redirect followups file to a tmp path per-test."""
    monkeypatch.setattr(ro, "FOLLOWUPS_FILE", tmp_path / "optimizer_followups.json")
    yield


def test_add_followup_requires_a_trigger():
    with pytest.raises(ValueError):
        ro.add_followup(topic="x", notes="y")


def test_add_and_load_roundtrip():
    entry = ro.add_followup(
        topic="live cadence gap",
        notes="investigate",
        revisit_on="2099-01-01",
        revisit_after_closed_trades=25,
        context={"foo": "bar"},
    )
    assert entry["id"].startswith("fu_")
    assert entry["status"] == "open"
    stored = ro._load_followups()
    assert len(stored) == 1
    assert stored[0]["topic"] == "live cadence gap"
    assert stored[0]["context"] == {"foo": "bar"}


def test_get_due_followups_by_date():
    ro.add_followup(topic="past due", notes="n", revisit_on="2020-01-01")
    ro.add_followup(topic="future", notes="n", revisit_on="2099-01-01")
    due = ro.get_due_followups(closed_trades=0, today="2026-04-14")
    assert [fu["topic"] for fu in due] == ["past due"]


def test_get_due_followups_by_trade_count():
    ro.add_followup(topic="needs trades", notes="n", revisit_after_closed_trades=10)
    assert ro.get_due_followups(closed_trades=5, today="2026-01-01") == []
    due = ro.get_due_followups(closed_trades=10, today="2026-01-01")
    assert len(due) == 1


def test_resolved_followups_are_not_due():
    fu = ro.add_followup(topic="x", notes="n", revisit_on="2020-01-01")
    assert ro.resolve_followup(fu["id"], resolution="fixed") is True
    assert ro.get_due_followups(closed_trades=0, today="2099-01-01") == []
    stored = ro._load_followups()
    assert stored[0]["status"] == "resolved"
    assert stored[0]["resolution"] == "fixed"


def test_resolve_missing_returns_false():
    assert ro.resolve_followup("nonexistent") is False


def test_report_renders_followup_banner_at_top():
    """When due followups exist, the report must carve out a prominent top banner."""
    cycle_data = {
        'snapshot': {'cycle': 42, 'date': '2026-05-03', 'regime': 'SIDEWAYS',
                     'system_closed': 10, 'system_open': 4, 'win_rate_7d': 50, 'win_rate_30d': 45,
                     'booked_trades_7d': 6, 'booked_trades_30d': 20, 'signals_screened_30d': 100,
                     'manual_override_count': 0},
        'auto_applied': [], 'pending_review': [], 'evaluations': [], 'path_performance': [],
        'missed_summary': {}, 'screening_summary': {}, 'loss_analysis': {},
        'loss_based_recommendations': [], 'manual_override_positions': [],
        'due_followups': [{
            'id': 'fu_test_1',
            'topic': 'TEST followup topic',
            'notes': 'TEST followup notes',
            'revisit_on': '2026-04-28',
            'revisit_after_closed_trades': 25,
            'context': {'foo': 'bar'},
            'created': '2026-04-14T00:00:00+00:00',
            'status': 'open',
        }],
    }
    html = ro.format_report_html(cycle_data)
    # Banner must appear BEFORE the V5 header
    banner_pos = html.find('ACTION REQUIRED')
    header_pos = html.find('Recursive Optimizer &mdash; Cycle')
    assert banner_pos >= 0, "Top banner missing"
    assert banner_pos < header_pos, "Followup banner must appear above V5 header"
    assert 'TEST followup topic' in html
    assert 'TEST followup notes' in html
    assert 'fu_test_1' in html  # resolve command includes id
    assert 'resolve_followup' in html


def test_report_omits_banner_when_no_followups_due():
    cycle_data = {
        'snapshot': {'cycle': 1, 'date': '2026-04-14', 'regime': 'SIDEWAYS',
                     'system_closed': 0, 'system_open': 0, 'win_rate_7d': 0, 'win_rate_30d': 0,
                     'booked_trades_7d': 0, 'booked_trades_30d': 0, 'signals_screened_30d': 0,
                     'manual_override_count': 0},
        'auto_applied': [], 'pending_review': [], 'evaluations': [], 'path_performance': [],
        'missed_summary': {}, 'screening_summary': {}, 'loss_analysis': {},
        'loss_based_recommendations': [], 'manual_override_positions': [],
        'due_followups': [],
    }
    html = ro.format_report_html(cycle_data)
    assert 'ACTION REQUIRED' not in html
    assert 'FOLLOWUP REMINDERS' not in html


def test_or_trigger_semantics():
    # Future date + low trade threshold: trade trigger fires
    ro.add_followup(
        topic="either",
        notes="n",
        revisit_on="2099-01-01",
        revisit_after_closed_trades=5,
    )
    assert ro.get_due_followups(closed_trades=10, today="2026-04-14")
    # Neither trigger fires
    assert ro.get_due_followups(closed_trades=0, today="2026-04-14") == []
