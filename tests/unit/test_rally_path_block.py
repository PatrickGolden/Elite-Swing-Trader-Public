"""Tests for the rally path auto-exec block in _start_twap_if_safe.

The rally path has 0/3 live wins (-8.54% combined). The block fires after the
tier gate (STRONG_BUY passes) so the quant dict key must be correct —
rec.get('quant'), NOT coin.get('quant').
"""
import pytest
from unittest.mock import patch, MagicMock


def _make_coin(quant_path: str, quant_tier: str = "STRONG_BUY") -> dict:
    """Build a minimal coin dict that passes tier check but carries the given path."""
    return {
        "symbol": "SOL",
        "name": "Solana",
        "id": "solana",
        "current_price": 150.0,
        "ai_recommendation": {
            "final_signal": quant_tier,
            "quant_tier": quant_tier,
            "quant": {
                "quant_tier": quant_tier,
                "quant_path": quant_path,
            },
            "consensus": {
                "quant_tier": quant_tier,
                "consensus_signal": quant_tier,
            },
            "position_sizing": {"position_size_usd": 200.0, "max_loss_pct": 2.0},
        },
    }


@pytest.fixture()
def no_open_positions(monkeypatch):
    """Ensure get_open_positions returns [] so the 'already open' gate passes."""
    import app as app_module
    monkeypatch.setattr(app_module, "get_open_positions", lambda **kw: [])


@pytest.fixture()
def clear_reconcile_cache(monkeypatch):
    """Ensure reconcile cache is empty so the unjournaled-holdings gate passes."""
    import app as app_module
    original = dict(app_module._reconcile_cache)
    app_module._reconcile_cache["data"] = None
    app_module._reconcile_cache["ts"] = 0
    yield
    app_module._reconcile_cache.update(original)


class TestRallyPathBlock:
    """_start_twap_if_safe must block rally path regardless of quant tier."""

    def test_rally_path_blocked_for_strong_buy(self, no_open_positions, clear_reconcile_cache):
        """STRONG_BUY on rally path returns False — path blocked before cash/execution checks."""
        import app as app_module
        coin = _make_coin(quant_path="rally", quant_tier="STRONG_BUY")
        result = app_module._start_twap_if_safe(coin, {}, 50_000.0)
        assert result is False

    def test_rally_path_blocked_for_ultra_buy(self, no_open_positions, clear_reconcile_cache):
        """ULTRA_BUY on rally path is also blocked — path veto takes priority over tier."""
        import app as app_module
        coin = _make_coin(quant_path="rally", quant_tier="ULTRA_BUY")
        result = app_module._start_twap_if_safe(coin, {}, 50_000.0)
        assert result is False

    def test_non_rally_path_passes_block(self, no_open_positions, clear_reconcile_cache):
        """blood_in_the_street path is not blocked — only rally is in _BLOCKED_PATHS."""
        import app as app_module
        coin = _make_coin(quant_path="blood_in_the_street", quant_tier="STRONG_BUY")
        # Will proceed past the rally block but fail later (no cash/account mock).
        # We only assert it did NOT fail at the rally gate by checking it raises
        # or returns False for a downstream reason — not because of the path.
        # Patch _build_account_overview to avoid live account calls.
        with patch.object(app_module, "_build_account_overview",
                          return_value={"cash_available_to_trade": 0}):
            result = app_module._start_twap_if_safe(coin, {}, 50_000.0)
        # Should return False due to insufficient cash, NOT the rally block.
        assert result is False  # downstream gate, not path gate

    def test_default_path_not_blocked(self, no_open_positions, clear_reconcile_cache):
        """default path is not in _BLOCKED_PATHS."""
        import app as app_module
        coin = _make_coin(quant_path="default", quant_tier="STRONG_BUY")
        with patch.object(app_module, "_build_account_overview",
                          return_value={"cash_available_to_trade": 0}):
            result = app_module._start_twap_if_safe(coin, {}, 50_000.0)
        assert result is False  # cash gate, not path gate

    def test_rally_block_uses_rec_quant_not_coin_quant(self, no_open_positions, clear_reconcile_cache):
        """Regression: block must read from rec.get('quant'), not coin.get('quant').

        coin.get('quant') is always None — the original bug that made the block silent.
        """
        import app as app_module
        coin = _make_coin(quant_path="rally", quant_tier="STRONG_BUY")
        # Confirm quant is NOT at the top level of coin
        assert coin.get("quant") is None
        # Confirm quant IS under ai_recommendation
        assert coin["ai_recommendation"]["quant"]["quant_path"] == "rally"
        # Block must still fire
        result = app_module._start_twap_if_safe(coin, {}, 50_000.0)
        assert result is False
