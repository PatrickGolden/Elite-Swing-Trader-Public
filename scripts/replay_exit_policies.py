"""Re-simulate the 168 backtest entries under alternative exit policies.
Validates baseline against recorded expectancy, then tests 'let winners run'.
Price path = hourly market-cap series (mcap return ~= price return over a 7d hold)."""
import json, os, datetime as dt, statistics as st

SLIPPAGE_PCT = 0.30
FEE_PCT = 0.18
BASE_MAX_HOLD = 168  # hours

_cache = {}
def series(coin):
    if coin in _cache:
        return _cache[coin]
    p = f'data/historical_cache/mcap_{coin}_90d.json'
    if not os.path.exists(p):
        _cache[coin] = None
        return None
    raw = json.load(open(p))
    pts = sorted((int(k), float(v)) for k, v in raw.items())  # (epoch_ms, mcap)
    _cache[coin] = pts
    return pts

def entry_index(pts, ts_iso):
    t = dt.datetime.fromisoformat(ts_iso.replace('Z', '+00:00')).timestamp() * 1000
    # first index at or after entry time
    for i, (ms, _) in enumerate(pts):
        if ms >= t:
            return i
    return None

def simulate(pts, i0, stop_pct, tp_pct, trail_pct, max_hold):
    """Walk forward hourly. Stop checked before target (conservative).
    trail_pct: if set, trailing stop = peak*(1-trail_pct/100). tp_pct None => no cap."""
    entry = pts[i0][1] * (1 + SLIPPAGE_PCT / 100)
    stop = entry * (1 - stop_pct / 100)
    target = entry * (1 + tp_pct / 100) if tp_pct is not None else None
    peak = entry
    end = min(i0 + max_hold, len(pts) - 1)
    exit_px, reason = None, 'time_exit'
    for i in range(i0 + 1, end + 1):
        px = pts[i][1]
        if px > peak:
            peak = px
        trail_stop = peak * (1 - trail_pct / 100) if trail_pct else None
        eff_stop = max(stop, trail_stop) if trail_stop else stop
        if px <= eff_stop:
            exit_px = eff_stop
            reason = 'trail_stop' if (trail_stop and eff_stop == trail_stop) else 'stop_loss'
            break
        if target is not None and px >= target:
            exit_px = target
            reason = 'take_profit'
            break
    if exit_px is None:
        exit_px = pts[end][1]
    eff_exit = exit_px * (1 - SLIPPAGE_PCT / 100)
    net = ((eff_exit - entry) / entry) * 100 - FEE_PCT * 2
    return net, reason

bt = json.load(open('data/backtest_trades.json'))
rows = bt['trades'] if isinstance(bt, dict) and 'trades' in bt else bt

def run(label, stop_pct, tp_pct, trail_pct, max_hold):
    res = []
    for r in rows:
        pts = series(r['coin_id'])
        if not pts:
            continue
        i0 = entry_index(pts, r['ts'])
        if i0 is None or i0 >= len(pts) - 2:
            continue
        net, reason = simulate(pts, i0, stop_pct, tp_pct, trail_pct, max_hold)
        res.append(net)
    n = len(res)
    wr = 100 * sum(1 for x in res if x > 0) / n
    wins = [x for x in res if x > 0]
    los = [x for x in res if x <= 0]
    exp = sum(res) / n
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(los) / len(los) if los else 0
    mx = max(res)
    print(f'{label:28} n={n} exp={exp:+6.2f}% wr={wr:4.1f}% avgW={aw:+5.2f} avgL={al:+5.2f} maxW={mx:+6.1f} tot={sum(res):+7.1f}')
    return exp

print("=== VALIDATION: baseline must reproduce recorded ~-1.75% expectancy ===")
run('BASELINE sl5 tp10 168h', 5, 10, None, BASE_MAX_HOLD)
print("(recorded backtest expectancy = -1.75%)\n")
print("=== LET-WINNERS-RUN POLICIES (same 168 entries) ===")
run('no-cap, time-exit 168h', 5, None, None, BASE_MAX_HOLD)
run('no-cap, 21d hold', 5, None, None, 504)
run('trail 15%, 21d hold', 5, None, 15, 504)
run('trail 20%, 21d hold', 5, None, 20, 504)
run('trail 10%, 21d hold', 5, None, 10, 504)
run('high cap tp30, 21d', 5, 30, None, 504)
run('trail15 + tp50, 21d', 5, 50, 15, 504)
