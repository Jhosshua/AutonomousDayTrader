"""How much money would faster order delivery recover? Re-runs the frozen 373 candidates
through the same PolicyReplay at lower order/cancel latencies. Signal, sizing, exits unchanged."""
import argparse, json, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.microstructure.optimization_sweep import PolicyReplay, PlacementPolicy, cache_events

B = Path('/Users/mo/analysis/AutonomousDayTrader-microstructure-2026-09-26')
SRC = B / 'grand_audit_v1'
LAT = {'250ms': 250_000_000, '50ms': 50_000_000, '1ms': 1_000_000}


def execute(job):
    day, symbol, policy, alpha, lat = job
    cache = SRC / 'caches' / day / symbol
    c = json.loads((cache / 'candidate.json').read_text()); snap = json.loads((cache / (symbol + '_snapshot.json')).read_text())
    pieces = policy.split(':'); p = PlacementPolicy(pieces[0], int(pieces[1]) if len(pieces) > 1 else 0)
    ns = LAT[lat]
    replay = PolicyReplay(c, p, alpha, snap, outbound_ns=ns, cancel_outbound_ns=ns)
    events = cache_events(cache / (symbol + '.pickle'))
    try: row = replay.run_policy(events)
    finally: events.close()
    return dict(candidate_id=c['candidate_id'], day=day, symbol=symbol, policy=policy, alpha=alpha, latency=lat,
                valid=row['valid'], net=row['net_price_units'], gross=row['gross_price_units'],
                entry_shares=row['entry_shares'], maker_shares=row['maker_shares'], taker_shares=row['taker_shares'])


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('--out', type=Path); a.add_argument('--latencies', default='50ms,1ms')
    a.add_argument('--limit', type=int, default=0); a.add_argument('--workers', type=int, default=12); a = a.parse_args()
    panel = [json.loads(l) for l in open(B / 'final_velocity_pivot_v1/candidate_panel.jsonl')]
    if a.limit: panel = panel[:a.limit]
    jobs = [(r['session_date'], r['symbol'], pol, 10000, lat) for r in panel for pol in ('post_only:0', 'ladder:2', 'market') for lat in a.latencies.split(',')]
    done = 0; t = time.monotonic()
    with open(a.out, 'w') as f, ProcessPoolExecutor(a.workers) as ex:
        for fut in as_completed([ex.submit(execute, j) for j in jobs]):
            f.write(json.dumps(fut.result()) + '\n'); f.flush(); done += 1
            if done % 100 == 0: print('PROGRESS', done, len(jobs), round(time.monotonic() - t), flush=True)
    print('DONE', done)
