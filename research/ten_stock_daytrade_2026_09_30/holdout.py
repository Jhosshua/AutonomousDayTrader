# @steered SNARE-2 2026-09-30
"""Holdout step (PLAN.md). Scores every design candidate once, then BH and the pass rules.

If there are no candidates, the pass list is empty by protocol. As labelled DESCRIPTION ONLY (never a
test, never used to change a rule), it also scores each name's nearest miss (highest design t among
screened configs) and the live TSLA plan benchmark, with deflated Sharpe, bootstrap and drawdown.
Writes data/holdout.json.
"""
import json
import math
import pickle

import numpy as np
from scipy import stats as sstats

import core
from core import (DAYS, DESIGN_END, HALVES, HOLD_END, HOLD_START, WIN_START, benjamini_hochberg,
                  daily_series, deflated_sharpe, max_drawdown, stationary_bootstrap, summarize)
from fam import FAMILIES


def effective_n(series: np.ndarray, thr: float = 0.9) -> int:
    x = series[series.std(axis=1) > 0]
    c = np.corrcoef(x)
    left = set(range(len(x)))
    n = 0
    while left:
        i = left.pop()
        left -= set(np.flatnonzero(np.abs(c[i]) > thr).tolist())
        n += 1
    return n


def score(tr: np.ndarray) -> dict:
    h = summarize(tr, DAYS, HOLD_START, HOLD_END)
    h2 = summarize(tr, DAYS, HOLD_START, HOLD_END, 2.0)
    halves = [summarize(tr, DAYS, a, b)["total"] for a, b in HALVES]
    full = summarize(tr, DAYS, WIN_START, HOLD_END)
    long_only = tr[tr[:, 1] > 0] if tr.size else tr
    ser = daily_series(tr, DAYS, HOLD_START, HOLD_END)
    lo, hi, bp = stationary_bootstrap(ser, draws=10000)
    full_ser = daily_series(tr, DAYS, WIN_START, HOLD_END)
    mdd, open_dd = max_drawdown(full_ser)
    return {"holdout": h, "holdout_2x_total": h2["total"], "halves": halves, "full": full,
            "full_long_only": summarize(long_only, DAYS, WIN_START, HOLD_END),
            "boot90": [lo, hi], "boot_p": bp, "max_dd": mdd, "open_dd": open_dd,
            "full_growth_10k": float(10000 * np.prod(1 + full_ser))}


def main():
    T = pickle.load(open("data/trades.pkl", "rb"))
    cands = json.load(open("data/candidates.json"))
    ds = np.load("data/design_series.npz")
    series, keys = ds["series"], ds["keys"]
    screened = np.array([k.endswith("|1") for k in keys])
    n_raw = len(keys) + 158
    n_eff = effective_n(series[screened]) if screened.any() else 0
    sr_all = np.array([s.mean() / s.std(ddof=1) if s.std() > 0 else 0.0 for s in series[screened]])
    var_sr = float(sr_all.var(ddof=1))
    out = {"n_raw": n_raw, "n_screened": int(screened.sum()), "n_effective": n_eff, "var_sr": var_sr,
           "candidates": [], "passes": []}
    # protocol step: candidates only
    ps = []
    for c in cands:
        tr = T[(c["sym"], c["fam"], c["cfg"])] if c["track"] == "name" else None
        if tr is None:
            continue  # pooled candidates would be scored here (none exist in this run)
        s = score(tr)
        ps.append(s["holdout"]["p"])
        out["candidates"].append({**c, **s})
    q = benjamini_hochberg(np.array(ps)) if ps else []
    for c, qq in zip(out["candidates"], q):
        h = c["holdout"]
        c["bh_q"] = float(qq)
        c["pass"] = bool(h["per_week"] >= core.MIN_PER_WEEK and h["mean"] > 0 and h["pf"] >= core.HOLD_PF
                         and h["p"] < core.HOLD_P and qq < core.BH_Q and c["holdout_2x_total"] > 0
                         and c["halves"][2] > 0 and c["halves"][3] > 0)
        if c["pass"]:
            out["passes"].append(c)
    # description only: nearest miss per name and the TSLA benchmark
    import csv
    rows = list(csv.DictReader(open("data/design.csv")))
    out["nearest_miss"] = {}
    for sym in core.NAMES:
        r = [x for x in rows if x["sym"] == sym and x["screened"] == "True"]
        r.sort(key=lambda x: -float(x["t"]))
        top = r[0]
        fam, j = top["fam"], int(top["cfg"])
        tr = T[(sym, fam, j)]
        k = list(keys).index(f"{sym}|{fam}|{j}|1")
        sd = series[k]
        sr = sd.mean() / sd.std(ddof=1)
        sk, ku = float(sstats.skew(sd)), float(sstats.kurtosis(sd, fisher=False))
        dsr = {lab: deflated_sharpe(sr, len(sd), n, var_sr, sk, ku)
               for lab, n in (("raw", n_raw), ("effective", n_eff), ("with_edge_hunt", n_raw + 12600))}
        design = summarize(tr, DAYS, WIN_START, DESIGN_END)
        out["nearest_miss"][sym] = {"fam": fam, "cfg": j, "params": FAMILIES[fam]["grid"][j], "design": design,
                                    "why_failed": "design t below 2.0" if design["t"] < core.DESIGN_T else "other gate",
                                    "dsr": dsr, **score(tr)}
    out["tsla_benchmark"] = {sym: {"design": summarize(T[(sym, "TSLA_OR15", 0)], DAYS, WIN_START, DESIGN_END),
                                   **score(T[(sym, "TSLA_OR15", 0)])} for sym in core.NAMES}
    json.dump(out, open("data/holdout.json", "w"), indent=1, default=float)
    print(f"trials raw {n_raw}, screened {out['n_screened']}, effective {n_eff}")
    print(f"holdout candidates {len(out['candidates'])}, passes {len(out['passes'])}")
    print("nearest misses (DESCRIPTION ONLY)")
    for sym, m in out["nearest_miss"].items():
        d, h, f = m["design"], m["holdout"], m["full"]
        print(f"  {sym:5s} {m['fam']:15s} {json.dumps(m['params'])}")
        print(f"        design wk {d['per_week']:.2f} win {d['win']:.2f} pf {d['pf']:.2f} t {d['t']:.2f} | "
              f"holdout wk {h['per_week']:.2f} win {h['win']:.2f} mean {h['mean'] * 1e4:+.1f}bps pf {h['pf']:.2f} p {h['p']:.3f} "
              f"2x {m['holdout_2x_total'] * 100:+.1f}% | full pf {f['pf']:.2f} maxdd {m['max_dd'] * 100:.1f}% "
              f"$10k->{m['full_growth_10k']:.0f} | dsr raw {m['dsr']['raw']:.3f}")
    print("TSLA plan benchmark (DESCRIPTION ONLY)")
    for sym, m in out["tsla_benchmark"].items():
        d, h, f = m["design"], m["holdout"], m["full"]
        print(f"  {sym:5s} design pf {d['pf']:.2f} t {d['t']:.2f} | holdout wk {h['per_week']:.2f} win {h['win']:.2f} "
              f"pf {h['pf']:.2f} p {h['p']:.3f} | full wk {f['per_week']:.2f} win {f['win']:.2f} pf {f['pf']:.2f} "
              f"mean {f['mean'] * 1e4:+.1f}bps maxdd {m['max_dd'] * 100:.1f}% $10k->{m['full_growth_10k']:.0f}")


if __name__ == "__main__":
    main()
