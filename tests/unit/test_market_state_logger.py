# market_state_logger (STRATEGY.md v2.0): the forward environment record that H-2026Q4-2
# and the D6/K2 forward bar are graded against. The features must be computed exactly as
# pinned, the daily snapshot must be write-once (never overwrite history), and nothing may
# raise into the refresh loop.

import json
from unittest.mock import patch

import market_state_logger as msl


def _coin(sym, ch7, ch30, ch24=0.0, mcap=100.0, px=10.0, cid=None):
    return {"id": cid or sym.lower(), "symbol": sym.lower(), "current_price": px, "market_cap": mcap,
            "price_change_percentage_7d_in_currency": ch7,
            "price_change_percentage_30d_in_currency": ch30,
            "price_change_percentage_24h_in_currency": ch24}


def _universe():
    return [
        _coin("BTC", 4.0, 6.0, mcap=600.0, cid="bitcoin"),
        _coin("ETH", 5.0, 20.0, ch24=1.0),    # beats BTC 7d + 30d, up 24h
        _coin("SOL", 3.0, 10.0, ch24=-1.0),   # beats 30d only
        _coin("XRP", 1.0, 1.0, ch24=2.0),     # beats neither
        _coin("USDT", 0.0, 0.0, px=1.0),      # listed stable: excluded
        _coin("FAKEUSD", 0.1, 0.0, px=1.001), # unlisted peg: excluded by behavior
    ]


def test_breadth_counts_only_real_alts_against_btc():
    b = msl.compute_breadth(_universe())
    assert b["alts_n"] == 3
    assert b["alts_beat_btc_7d_pct"] == round(100 / 3, 1)
    assert b["alts_beat_btc_30d_pct"] == round(200 / 3, 1)
    assert b["alts_up_24h_pct"] == round(200 / 3, 1)
    assert b["btc_dominance_topn_pct"] == round(100 * 600 / 1100, 2)


def test_breadth_without_btc_row_is_none_not_crash():
    b = msl.compute_breadth([_coin("ETH", 5, 5)])
    assert b["alts_beat_btc_7d_pct"] is None and b["alts_beat_btc_30d_pct"] is None


def test_eth_btc_uses_pinned_sma50():
    btc = [100.0] * 60
    eth = [5.0] * 59 + [6.0]                 # ratio 0.05 flat, then 0.06 today
    blk = msl.compute_eth_btc(eth, btc)
    sma = (0.05 * 49 + 0.06) / 50
    assert blk["eth_btc"] == 0.06
    assert abs(blk["eth_btc_sma50"] - round(sma, 6)) < 1e-9
    assert blk["eth_btc_above_sma50"] is True
    assert msl.ETH_BTC_SMA_DAYS == 50        # H-2026Q4-2 pins this; changing it is a new trial


def test_eth_btc_short_series_returns_empty_block():
    blk = msl.compute_eth_btc([1.0] * 10, [1.0] * 10)
    assert blk["eth_btc"] is None and blk["eth_btc_above_sma50"] is None


def test_daily_snapshot_is_write_once(tmp_path):
    d = str(tmp_path / "daily")
    p = msl.write_daily_snapshot(_universe(), day="2026-09-26", directory=d)
    assert p and json.load(open(p))["n"] == 6
    first = open(p).read()
    # A second refresh the same day must NOT overwrite the day's record.
    assert msl.write_daily_snapshot([_coin("BTC", 0, 0)], day="2026-09-26", directory=d) is None
    assert open(p).read() == first


def test_compact_coin_keeps_the_fields_a_backtest_needs():
    c = msl.compact_coin(_universe()[1] | {"market_cap_rank": 2, "total_volume": 5.0})
    assert {"symbol", "px", "mcap", "vol24", "ch7d", "ch30d", "rank"} <= set(c)


def test_record_market_state_appends_env_row(tmp_path, monkeypatch):
    monkeypatch.setattr(msl, "MARKET_STATE_FILE", str(tmp_path / "ms.jsonl"))
    monkeypatch.setattr(msl, "MARKET_DAILY_DIR", str(tmp_path / "daily"))
    monkeypatch.setattr(msl, "write_daily_snapshot", lambda coins: None)
    monkeypatch.setattr(msl, "_open_paper_alt_products", lambda: [])
    with patch.object(msl, "_eth_btc_live", return_value=msl.compute_eth_btc([], [])):
        row = msl.record_market_state(_universe(), {"regime": "BULL", "characteristics": {"fear_greed": 61}},
                                      risk={"risk_on": True, "btc_vs_ma200_pct": 18.3, "source": "ma200"})
    assert row["risk_on"] is True and row["fear_greed"] == 61.0 and row["alts_n"] == 3
    rows = [json.loads(l) for l in open(tmp_path / "ms.jsonl")]
    assert len(rows) == 1 and rows[0]["btc_vs_ma200_pct"] == 18.3


def test_open_alt_products_read_real_journal_shape(temp_positions):
    # Journal rows carry the ticker as 'coin_symbol' (no 'symbol' key). The first live
    # run mocked this lookup in tests and captured zero alt spreads in production.
    rows = [
        {"id": "1", "source": "paper_trade", "status": "OPEN", "coin_symbol": "MORPHO", "coin_id": "morpho"},
        {"id": "2", "source": "paper_trade", "status": "OPEN", "coin_symbol": "SEI", "coin_id": "sei"},
        {"id": "3", "source": "paper_trade", "status": "CLOSED", "coin_symbol": "LTC", "coin_id": "litecoin"},
        {"id": "4", "source": "paper_rotation", "status": "OPEN", "coin_symbol": "BTC", "coin_id": "bitcoin"},
    ]
    with open(temp_positions, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    assert sorted(msl._open_paper_alt_products()) == ["MORPHO-USD", "SEI-USD"]


def test_record_market_state_never_raises(monkeypatch):
    monkeypatch.setattr(msl, "build_env_row", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(msl, "write_daily_snapshot", lambda coins: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(msl, "_open_paper_alt_products", lambda: [])
    assert msl.record_market_state(_universe(), {}, risk={"risk_on": False}) is None
