"""Step 0: does any reconstructed decision-time feature separate big winners from duds?"""
import json, os, statistics as st, datetime as dt

_cache = {}
def load(coin):
    if coin in _cache:
        return _cache[coin]
    p = f'data/historical_cache/mcap_{coin}_90d.json'
    if not os.path.exists(p):
        _cache[coin] = None
        return None
    raw = json.load(open(p))
    # keys are epoch-ms timestamps; bucket to last reading per UTC date
    byday = {}
    for k, v in raw.items():
        try:
            day = str(dt.datetime.utcfromtimestamp(int(k) / 1000).date())
        except (ValueError, OverflowError):
            day = k  # already a date string
        byday[day] = v  # later timestamp overwrites -> last reading of day
    _cache[coin] = byday
    return byday

btc = load('bitcoin')

def asof(series, date_str, lag=0):
    d = dt.date.fromisoformat(date_str) - dt.timedelta(days=lag)
    for i in range(7):
        k = str(d - dt.timedelta(days=i))
        if k in series:
            return series[k]
    return None

def feats(coin, date_str):
    s = load(coin)
    if not s:
        return None
    p0 = asof(s, date_str, 0)
    if not p0:
        return None
    p7, p30 = asof(s, date_str, 7), asof(s, date_str, 30)
    d = dt.date.fromisoformat(date_str)
    win = [s[str(d - dt.timedelta(days=i))] for i in range(30) if str(d - dt.timedelta(days=i)) in s]
    if len(win) < 10:
        return None
    lo, hi = min(win), max(win)
    rets = [win[i] / win[i + 1] - 1 for i in range(len(win) - 1)]
    f = {}
    if p7:
        f['mom_7d'] = p0 / p7 - 1
    if p30:
        f['mom_30d'] = p0 / p30 - 1
    f['dist_from_low30'] = (p0 - lo) / lo
    f['dist_from_high30'] = (p0 - hi) / hi
    f['vol_30d'] = st.pstdev(rets) if len(rets) > 2 else None
    if btc and p7:
        b0, b7 = asof(btc, date_str, 0), asof(btc, date_str, 7)
        if b0 and b7:
            f['btc_rel_7d'] = (p0 / p7 - 1) - (b0 / b7 - 1)
    return f

m = json.load(open('data/backtest_missed.json'))['all']
W, D = [], []
for r in m:
    o = r.get('outcome_7d')
    if not isinstance(o, (int, float)):
        continue
    f = feats(r['coin_id'], r['date'])
    if not f:
        continue
    if o >= 15:
        W.append(f)
    elif o <= 0:
        D.append(f)
print(f'reconstructed: winners={len(W)} duds={len(D)}')

def auc(key):
    wv = [x[key] for x in W if x.get(key) is not None]
    dv = [x[key] for x in D if x.get(key) is not None]
    if len(wv) < 5 or len(dv) < 5:
        return None
    gt = sum(1 for a in wv for b in dv if a > b)
    eq = sum(1 for a in wv for b in dv if a == b)
    return (gt + 0.5 * eq) / (len(wv) * len(dv)), st.median(wv), st.median(dv)

print(f'{"feature":18}{"AUC":>7}{"win_med":>10}{"dud_med":>10}   (0.5=noise, >0.65/<0.35=signal)')
for k in ['mom_7d', 'mom_30d', 'dist_from_low30', 'dist_from_high30', 'vol_30d', 'btc_rel_7d']:
    r = auc(k)
    if r:
        flag = '  <-- SIGNAL' if (r[0] > 0.65 or r[0] < 0.35) else ''
        print(f'{k:18}{r[0]:7.2f}{r[1]:10.3f}{r[2]:10.3f}{flag}')
