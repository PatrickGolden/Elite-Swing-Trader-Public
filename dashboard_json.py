"""Flask JSON provider extensions for dashboard API responses.

The pipeline keeps values such as CoinGecko trending ids as ``set`` for cheap
membership tests (see ``EnhancedDataFetcherV2.get_trending_coins``). JSON has no
set type; encoding happens at the Flask boundary via :func:`flask.jsonify`.

Subclassing :class:`~flask.json.provider.DefaultJSONProvider` keeps conversion in
one place so routes do not each remember to coerce nested structures.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from flask.json.provider import DefaultJSONProvider, _default as _flask_std_default


def _dashboard_json_default(o: Any) -> Any:
    """Handle types the stdlib encoder does not; delegate the rest to Flask."""
    if isinstance(o, (set, frozenset)):
        # Sorted for stable responses; key=str avoids TypeError on mixed-type sets.
        return sorted(o, key=str)
    if isinstance(o, deque):
        return list(o)
    return _flask_std_default(o)


class DashboardJSONProvider(DefaultJSONProvider):
    """Adds set/frozenset/deque serialization; preserves Flask date/UUID/dataclass behavior."""

    default = staticmethod(_dashboard_json_default)
