# The latent hazard behind protected_assets.py: sync-position-quantities treats the exchange
# balance as truth. With an open system XRP position, it would set that position to the
# owner's ENTIRE XRP balance and place a stop-loss sell on all of it.

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def client():
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_sync_never_adopts_owner_xrp_balance(client, temp_positions):
    import app as app_module
    rows = [{"id": 900, "status": "OPEN", "source": None, "is_test": False,
             "coin_symbol": "XRP", "coin_id": "ripple", "quantity": 10.0,
             "entry_price": 1.30, "entry_time": "2026-09-27 00:00:00"}]
    with open(temp_positions, "w", encoding="utf-8") as f:
        json.dump(rows, f)

    place_sl = MagicMock()
    with patch.object(app_module, "fetch_balance_sync", return_value={"holdings": {"XRP": 50000.0}}), \
         patch.object(app_module, "_price_by_symbol_from_cached", return_value={"XRP": 1.37}), \
         patch.object(app_module, "_place_sl_tp_with_retry", place_sl):
        r = client.post("/api/account/sync-position-quantities")

    body = r.get_json()
    assert r.status_code == 200, body
    assert {"symbol": "XRP", "reason": "protected_asset_owner_held"} in body.get("skipped", [])
    assert not any(u.get("symbol") == "XRP" for u in body.get("updated", []))
    place_sl.assert_not_called()
    after = json.load(open(temp_positions, encoding="utf-8"))
    assert after[0]["quantity"] == 10.0          # journal untouched, NOT 50,000
