"""Price Impact Factor features (dPrice / traded volume) from the frozen causal L3 snapshots.

Outcome-blind: reads only the snapshot file and candidate side/date. Volume in a
lookback = visible executions on both book sides + hidden (type P) prints.
A move on zero volume is infinite impact (the purest "high impact, low volume" case).
"""
import argparse, json, math
from pathlib import Path

HORIZONS = (('100ms', 0.1), ('1s', 1.0), ('5s', 5.0))
INF = 1e12  # finite sentinel so threshold rules can include zero-volume moves


def features(snap, direction):
    s = {x['asof_ns']: x for x in snap['snapshots']}
    cut = snap['cutoff_ns']; end = s[cut]; x = {}; bad = []
    for tag, h in HORIZONS:
        st = s[cut - round(h * 1e9)]
        if st['last_ns'] > st['asof_ns'] or end['last_ns'] > cut:
            raise ValueError('future input')
        valid = (st['continuous_valid'] and end['continuous_valid'] and st['epoch'] == end['epoch']
                 and st['invalid_events'] == end['invalid_events'] and min(st['depth']) > 0)
        if not valid:
            bad.append(tag)
            for k in ('impact_usd_per_share', 'impact_bps_per_10k_usd', 'abs_impact_bps_per_10k_usd', 'volume_shares'):
                x[f'{k}_{tag}'] = None
            continue
        vol = sum(end['executions'][i] - st['executions'][i] for i in (0, 1)) + end['unknown_print_shares'] - st['unknown_print_shares']
        if vol < 0: raise ValueError('negative volume')
        mid0 = (st['bid_units'] + st['ask_units']) / 20000
        dmid = direction * ((end['bid_units'] + end['ask_units']) - (st['bid_units'] + st['ask_units'])) / 20000
        dbps = dmid / mid0 * 10000
        if vol == 0:
            usd = bps = 0.0 if dmid == 0 else math.copysign(INF, dmid)
        else:
            usd = dmid / vol
            bps = dbps / (vol * mid0 / 10000)  # bps moved per $10k traded
        x[f'impact_usd_per_share_{tag}'] = usd
        x[f'impact_bps_per_10k_usd_{tag}'] = bps
        x[f'abs_impact_bps_per_10k_usd_{tag}'] = abs(bps)
        x[f'volume_shares_{tag}'] = float(vol)
    return x, bad


def main(panel_path, snap_path, out):
    side = {}
    for l in open(panel_path):
        r = json.loads(l); side[r['candidate_id']] = (r['session_date'], r['symbol'], 1 if r['side'] == 'BUY' else -1)
    rows = []
    for l in open(snap_path):
        sn = json.loads(l); d, sym, dirn = side[sn['candidate_id']]
        x, bad = features(sn, dirn)
        rows.append(dict(candidate_id=sn['candidate_id'], session_date=d, symbol=sym, direction=dirn, x=x, invalid_windows=bad))
    assert len(rows) == len(side)
    out.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    print('FEATURES', len(rows))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--panel', type=Path); p.add_argument('--snapshots', type=Path); p.add_argument('--out', type=Path)
    a = p.parse_args(); main(a.panel, a.snapshots, a.out)
