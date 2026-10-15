import json, os, datetime as dt
exec(open('scripts/replay_exit_policies.py').read().split('bt = json.load')[0])  # load sim funcs/consts

bt = json.load(open('data/backtest_trades.json'))
rows = bt['trades'] if isinstance(bt, dict) and 'trades' in bt else bt
rows = [r for r in rows if r.get('ts')]
rows.sort(key=lambda r: r['ts'])
half = len(rows) // 2
firstH, secondH = rows[:half], rows[half:]

def exp_for(rowset, trail):
    res = []
    for r in rowset:
        pts = series(r['coin_id'])
        if not pts:
            continue
        i0 = entry_index(pts, r['ts'])
        if i0 is None or i0 >= len(pts) - 2:
            continue
        net, _ = simulate(pts, i0, 5, None, trail, 504)
        res.append(net)
    if not res:
        return (0, 0, 0)
    return (sum(res) / len(res), 100 * sum(1 for x in res if x > 0) / len(res), len(res))

print("=== FINE TRAIL SWEEP (no cap, 21d) — is 15% a spike or a plateau? ===")
print(f'{"trail%":>7}{"exp_all":>10}{"wr":>7}')
for t in [8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 25]:
    e, w, n = exp_for(rows, t)
    print(f'{t:7}{e:+9.2f}%{w:6.1f}%')

print("\n=== TIME-SPLIT (out-of-sample): does trail hold in BOTH halves? ===")
d1 = firstH[0]['ts'][:10] + '..' + firstH[-1]['ts'][:10]
d2 = secondH[0]['ts'][:10] + '..' + secondH[-1]['ts'][:10]
print(f'1st half: {d1}   2nd half: {d2}')
for t in [13, 15, 17]:
    e1, w1, n1 = exp_for(firstH, t)
    e2, w2, n2 = exp_for(secondH, t)
    print(f'  trail{t}: 1stHalf exp={e1:+.2f}% (n={n1})   2ndHalf exp={e2:+.2f}% (n={n2})')
