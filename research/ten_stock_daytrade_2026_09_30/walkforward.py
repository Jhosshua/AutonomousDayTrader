# @steered SNARE-2 2026-09-30
"""Walk forward "without luck" record (PLAN.md). Anchored quarterly refit, primary objective t.
Sensitivity (never used to pick) profit factor objective, and a trailing six month fit window.
Writes data/walkforward.json."""
import json
import math
import pickle

import numpy as np

import core
from core import DAYS, QUARTERS, WIN_START, daily_series, summarize, ttest
from fam import FAMILIES, FAMILY_ORDER, eligible, neighbours


def fit_pick(T, sym, lo, hi, objective):
    d0, d1 = core.day_index(DAYS, lo, hi)
    sessions = d1 - d0 + 1
    best, best_key = None, None
    for fam in FAMILY_ORDER:
        if not eligible(fam, sym):
            continue
        grid = FAMILIES[fam]["grid"]
        st = {j: summarize(T[(sym, fam, j)], DAYS, lo, hi) for j in range(len(grid))}
        freq = {j: st[j]["n"] >= sessions / 5 for j in st}
        for j in range(len(grid)):
            s = st[j]
            if not (freq[j] and s["mean"] > 0 and s["pf"] >= core.DESIGN_PF and s["t"] >= core.DESIGN_T):
                continue
            nb = [k for k in neighbours(fam, j) if freq[k]]
            if nb:
                if np.median([st[k]["t"] for k in nb]) < core.NEIGH_T:
                    continue
            elif s["t"] < core.NEIGH_T_NONE:
                continue
            score = s["t"] if objective == "t" else s["pf"]
            if best is None or score > best:  # strict, so ties keep the earlier family and grid order
                best, best_key = score, (fam, j, s)
    return best_key


def run(T, objective="t", window="anchored"):
    out = {}
    pooled = []
    for sym in core.NAMES:
        recs = []
        for q0, q1 in QUARTERS:
            fit_hi = int(DAYS[np.searchsorted(DAYS, q0) - 1])
            fit_lo = WIN_START if window == "anchored" else int(DAYS[max(np.searchsorted(DAYS, q0) - 126, 0)])
            pick = fit_pick(T, sym, fit_lo, fit_hi, objective)
            if pick is None:
                ser = daily_series(core.EMPTY, DAYS, q0, q1)
                recs.append({"q": q0, "pick": None, "oos": summarize(core.EMPTY, DAYS, q0, q1), "series": ser.tolist()})
                continue
            fam, j, s = pick
            tr = T[(sym, fam, j)]
            ser = daily_series(tr, DAYS, q0, q1)
            recs.append({"q": q0, "pick": [fam, j, FAMILIES[fam]["grid"][j]], "fit": s,
                         "oos": summarize(tr, DAYS, q0, q1), "series": ser.tolist()})
        out[sym] = recs
    # success test, pooled over the four holdout quarters
    hold = [i for i, (q0, _) in enumerate(QUARTERS) if q0 >= core.HOLD_START]
    per_name = [np.concatenate([np.array(out[s][i]["series"]) for i in hold]) for s in core.NAMES]
    pool = np.mean(per_name, axis=0)
    t, p = ttest(pool)
    picks = sum(1 for s in core.NAMES for r in out[s] if r["pick"])
    return {"objective": objective, "window": window, "by_name": out, "pool_t": t, "pool_p": p,
            "pool_mean": float(pool.mean()), "pool_total": float(pool.sum()), "picks": picks}


def main():
    T = pickle.load(open("data/trades.pkl", "rb"))
    res = [run(T, "t", "anchored"), run(T, "pf", "anchored"), run(T, "t", "trailing6m")]
    json.dump(res, open("data/walkforward.json", "w"), default=float)
    for r in res:
        print(f"{r['objective']:3s} {r['window']:10s} name-quarter picks {r['picks']:2d} of 60, "
              f"holdout pooled mean {r['pool_mean'] * 1e4:+.2f} bps a day, t {r['pool_t']:.2f}, p {r['pool_p']:.3f}")
        for s in core.NAMES:
            line = []
            for rec in r["by_name"][s]:
                if rec["pick"]:
                    o = rec["oos"]
                    line.append(f"{rec['q']}:{rec['pick'][0]}#{rec['pick'][1]} n{o['n']} {o['total'] * 100:+.1f}%")
                else:
                    line.append(f"{rec['q']}:sit")
            print("   ", s, " | ".join(line))


if __name__ == "__main__":
    main()
