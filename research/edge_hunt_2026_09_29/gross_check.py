# @steered SNARE-2 2026-09-29
"""Descriptive only. Every family on discovery sessions before cost (gross = net + that trade's
cost), to tell apart 'no signal' from 'signal smaller than cost'. Nothing here feeds a pass rule."""
import os
from multiprocessing import Pool

import numpy as np

import lib
from families import ALL_FAMILIES, RUNNERS

STOCKS = [s for f in ("universe.txt", "universe_round3.txt")
          for s in open(os.path.join(lib.HERE, f)).read().split()]


def work(sym):
    days = lib.calendar()
    d0 = int(np.searchsorted(days, lib.IS_START))
    d1 = int(np.searchsorted(days, lib.IS_END, side="right")) - 1
    tp, b = lib.load(sym, days), lib.load(lib.benchmark(sym), days)
    out = []
    for fam, spec in ALL_FAMILIES.items():
        for cfg in spec["grid"]:
            tr = RUNNERS[fam](tp, cfg, d0, d1, bench=b)
            if len(tr) < 30:
                continue
            g = tr[:, 4] + np.array([lib.cost_frac(p) for p in tr[:, 2]])
            out.append((fam, g.mean() * 1e4, g.mean() / g.std(ddof=1) * np.sqrt(len(g))))
    return out


if __name__ == "__main__":
    with Pool(7) as p:
        rows = [r for part in p.imap_unordered(work, STOCKS) for r in part]
    for fam in ALL_FAMILIES:
        rr = [r for r in rows if r[0] == fam]
        t = np.array([r[2] for r in rr])
        bps = np.array([r[1] for r in rr])
        print(f"{fam:18} tests={len(rr):5} gross {np.mean(bps):+6.1f} bps/trade  mean t {t.mean():+.2f}  "
              f"t>2 {np.mean(t > 2):5.1%}  t<-2 {np.mean(t < -2):5.1%}  (chance about 2.3% each)")
