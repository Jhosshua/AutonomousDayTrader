# @steered SNARE-2 2026-09-30
"""Design year selection (PLAN.md, design selection). Reads ONLY design year slices of trades.pkl.

Writes data/design.csv (every test), data/candidates.json (holdout candidates, name level and pooled)
and data/design_series.npz (design daily series for effective N and deflated Sharpe).
"""
import csv
import json
import math
import pickle

import numpy as np

import core
from core import DAYS, DESIGN_END, WIN_START, daily_series, summarize, ttest
from fam import FAMILIES, FAMILY_ORDER, eligible, neighbours

LO, HI = WIN_START, DESIGN_END


def pooled_stats(series: np.ndarray, n_trades: int, n_names: int, weeks: float) -> dict:
    wins, losses = series[series > 0].sum(), -series[series < 0].sum()
    t, p = ttest(series)
    return {"n": n_trades, "per_week": n_trades / weeks / n_names, "mean": float(series.mean()),
            "pf": float(wins / losses) if losses > 0 else float("inf"), "t": t, "p": p, "win": math.nan}


def qualifies(s: dict) -> bool:
    return s["per_week"] >= core.MIN_PER_WEEK and s["mean"] > 0 and s["pf"] >= core.DESIGN_PF and s["t"] >= core.DESIGN_T


def neighbour_ok(fam: str, j: int, tvals: dict, screened: dict) -> tuple[bool, float]:
    nb = [k for k in neighbours(fam, j) if screened.get(k, False)]
    if not nb:
        return tvals[j] >= core.NEIGH_T_NONE, math.nan
    med = float(np.median([tvals[k] for k in nb]))
    return med >= core.NEIGH_T, med


def main() -> None:
    T = pickle.load(open("data/trades.pkl", "rb"))
    d0, d1 = core.day_index(DAYS, LO, HI)
    weeks = (d1 - d0 + 1) / 5.0
    rows, cands, series, keys = [], [], [], []
    for sym in core.NAMES:
        for fam in FAMILY_ORDER:
            if not eligible(fam, sym):
                continue
            grid = FAMILIES[fam]["grid"]
            st = {j: summarize(T[(sym, fam, j)], DAYS, LO, HI) for j in range(len(grid))}
            screened = {j: st[j]["per_week"] >= core.MIN_PER_WEEK for j in st}
            tv = {j: st[j]["t"] for j in st}
            best = None
            for j in range(len(grid)):
                s = st[j]
                nok, med = neighbour_ok(fam, j, tv, screened)
                q = screened[j] and qualifies(s) and nok
                rows.append({"track": "name", "sym": sym, "fam": fam, "cfg": j, "params": json.dumps(grid[j]),
                             "screened": screened[j], "qualifies": q, "neigh_med_t": med, **s})
                series.append(daily_series(T[(sym, fam, j)], DAYS, LO, HI))
                keys.append(f"{sym}|{fam}|{j}|{int(screened[j])}")
                if q and (best is None or s["t"] > st[best]["t"]):
                    best = j
            if best is not None:
                cands.append({"track": "name", "sym": sym, "fam": fam, "cfg": best, "params": grid[best],
                              "design": st[best]})
    # pooled track: equal weight across eligible names, zero on no trade days
    for fam in FAMILY_ORDER:
        grid = FAMILIES[fam]["grid"]
        names = [s for s in core.NAMES if eligible(fam, s)]
        st, ser = {}, {}
        for j in range(len(grid)):
            m = np.mean([daily_series(T[(s, fam, j)], DAYS, LO, HI) for s in names], axis=0)
            n = sum(len(core.slice_trades(T[(s, fam, j)], DAYS, LO, HI)) for s in names)
            st[j], ser[j] = pooled_stats(m, n, len(names), weeks), m
        screened = {j: st[j]["per_week"] >= core.MIN_PER_WEEK for j in st}
        tv = {j: st[j]["t"] for j in st}
        best = None
        for j in range(len(grid)):
            nok, med = neighbour_ok(fam, j, tv, screened)
            q = screened[j] and qualifies(st[j]) and nok
            rows.append({"track": "pooled", "sym": "POOL", "fam": fam, "cfg": j, "params": json.dumps(grid[j]),
                         "screened": screened[j], "qualifies": q, "neigh_med_t": med, **st[j]})
            if q and (best is None or st[j]["t"] > st[best]["t"]):
                best = j
        if best is not None:
            cands.append({"track": "pooled", "sym": "POOL", "fam": fam, "cfg": best, "params": grid[best],
                          "names": names, "design": st[best]})
    with open("data/design.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open("data/candidates.json", "w") as f:
        json.dump(cands, f, indent=1, default=float)
    np.savez_compressed("data/design_series.npz", series=np.array(series), keys=np.array(keys))
    nq = sum(r["qualifies"] for r in rows)
    print(f"tests {len(rows)}, screened out {sum(not r['screened'] for r in rows)}, qualifying {nq}, candidates {len(cands)}")
    for c in cands:
        d = c["design"]
        print(f"  {c['track']:6s} {c['sym']:5s} {c['fam']:16s} {json.dumps(c['params'])}  n {d['n']} "
              f"wk {d['per_week']:.2f} mean {d['mean'] * 1e4:+.1f}bps pf {d['pf']:.2f} t {d['t']:.2f}")


if __name__ == "__main__":
    main()
