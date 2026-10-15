"""DashboardJSONProvider: jsonify supports sets used in cached pipeline data."""

import json

from flask import Flask, jsonify

from dashboard_json import DashboardJSONProvider


def test_jsonify_serializes_set_and_nested_set():
    app = Flask(__name__)
    app.json = DashboardJSONProvider(app)

    with app.app_context():
        payload = {
            'trending_coin_ids': {'bitcoin', 'ethereum'},
            'nested': {'inner': frozenset({'a', 'b'})},
        }
        resp = jsonify(payload)
        data = json.loads(resp.get_data(as_text=True))

    assert data['trending_coin_ids'] == ['bitcoin', 'ethereum']
    assert data['nested']['inner'] == ['a', 'b']


def test_delegates_uuid_and_datetime():
    """Flask's built-in extended types still work."""
    from datetime import date
    from uuid import UUID

    app = Flask(__name__)
    app.json = DashboardJSONProvider(app)

    u = UUID('12345678-1234-5678-1234-567812345678')
    d = date(2026, 5, 2)
    with app.app_context():
        resp = jsonify({'u': u, 'd': d})
        text = resp.get_data(as_text=True)

    assert '12345678-1234-5678-1234-567812345678' in text
    assert '2026' in text
