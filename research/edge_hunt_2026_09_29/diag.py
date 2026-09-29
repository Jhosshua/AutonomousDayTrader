# @steered SNARE-2 2026-09-29
"""Descriptive checks on the near misses. Changes no rule and selects nothing.
1. Is the overnight edge a timing effect or just being long a stock that rallied?
2. Does it survive dropping the best nights (earnings gaps)?
3. How much is market overnight drift (QQQ) vs stock specific?
"""

import json
import math
import os

import numpy as np

import lib
from families import ALL_FAMILIES, RUNNERS

days = lib.calendar()
d0 = int(np.searchsorted(days, lib.IS_START))
d1 = int(np.searchsorted(days, lib.OOS_END, side="right")) - 1
isd = (days >= lib.IS_START) & (days <= lib.IS_END)
oosd = (days >= lib.OOS_START) & (days <= lib.OOS_END)


def ann(x):
    x = x[np.isfinite(x)]
    return x.mean() * 252, x.mean() / x.std(ddof=1) * math.sqrt(252) if len(x) > 2 else float("nan")


def decomposition(sym):
    tp = lib.load(sym, days)
    q = lib.load("QQQ", days)
    night = tp.open_ / tp.prev_close - 1           # earned at the open of day d
    day = tp.close / tp.open_ - 1
    c2c = tp.close / tp.prev_close - 1
    qn = q.open_ / q.prev_close - 1
    print(f"\n{sym}")
    for name, m in (("discovery", isd), ("holdout", oosd)):
        a = {k: ann(v[m]) for k, v in (("overnight", night), ("intraday", day), ("close to close", c2c))}
        print(f"  {name:9} " + "  ".join(f"{k} {v[0]:+.0%}/yr sharpe {v[1]:.2f}" for k, v in a.items()))
    m = (isd | oosd) & np.isfinite(night) & np.isfinite(qn)
    beta = np.cov(night[m], qn[m])[0, 1] / np.var(qn[m], ddof=1)
    resid = night[m] - beta * qn[m]
    print(f"  overnight beta to QQQ overnight {beta:.2f}; QQQ overnight {qn[m].mean()*1e4:+.1f} bps/night; "
          f"stock specific overnight {resid.mean()*1e4:+.1f} bps/night (t {resid.mean()/resid.std(ddof=1)*math.sqrt(m.sum()):.2f})")


def trim_check(tid, sym, fam, cfg):
    import datetime
    tp, b = lib.load(sym, days), lib.load(lib.benchmark(sym), days)
    tr = RUNNERS[fam](tp, cfg, d0, d1, bench=b)
    r = np.sort(tr[:, 4])
    out = []
    for drop in (0, 3, 5, 10):
        x = r[:len(r) - drop] if drop else r
        t = x.mean() / x.std(ddof=1) * math.sqrt(len(x))
        pf = x[x > 0].sum() / -x[x < 0].sum()
        out.append(f"drop best {drop}: mean {x.mean()*1e4:+.1f} bps pf {pf:.2f} t {t:.2f}")
    gross = tr[:, 4].mean() + np.mean([lib.cost_frac(p) for p in tr[:, 2]])
    dow = np.array([datetime.date(int(days[int(d)]) // 10000, int(days[int(d)]) // 100 % 100,
                                  int(days[int(d)]) % 100).weekday() for d in tr[:, 0]])
    print(f"  {tid}: " + "; ".join(out) + f"; break even cost {gross*1e4:.1f} bps round trip; "
          f"Friday entries {tr[dow == 4, 4].mean()*1e4:+.1f} bps vs other days {tr[dow != 4, 4].mean()*1e4:+.1f} bps")


if __name__ == "__main__":
    for s in ("IREN", "NVDA", "HUT", "QQQ"):
        decomposition(s)
    print("\nrobustness of the near misses (full period 2023-10 to 2026-09)")
    res = json.load(open(os.path.join(lib.HERE, "data", "holdout.json")))
    for r in res:
        if r["rules"]["1_holdout"] and r["rules"]["3_full"] and r["rules"]["4_stable"]:
            trim_check(r["id"], r["sym"], r["family"], r["params"])
