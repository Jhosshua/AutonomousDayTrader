# @steered SNARE-2 2026-09-29
"""Holdout pass. Runs each discovery candidate, unchanged, over 2023-10-02..2026-09-28 and applies
the four pass rules of PROTOCOL.md. Run once. Writes data/holdout.json and data/trades_<id>.npy."""
from __future__ import annotations

import json
import math
import os

import numpy as np

import lib
import glob

from families import ALL_FAMILIES as FAMILIES, RUNNERS

OUT = os.path.join(lib.HERE, "data")
HALVES = [(20231001, 20240331), (20240401, 20240930), (20241001, 20250331),
          (20250401, 20250930), (20251001, 20260331), (20260401, 20260930)]


def bh_qvalues(p: list[float]) -> list[float]:
    n = len(p)
    order = np.argsort(p)
    q = np.empty(n)
    prev = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        prev = min(prev, p[i] * n / rank)
        q[i] = prev
    return q.tolist()


def bootstrap_p(days: np.ndarray, tr: np.ndarray, col: int, n: int = 10000, seed: int = 11) -> float:
    """One sided p that the mean daily P&L is <= 0, resampling whole days (centered bootstrap)."""
    dsum = np.bincount(tr[:, 0].astype(int), weights=tr[:, col])
    dsum = dsum[np.bincount(tr[:, 0].astype(int)) > 0]
    rng = np.random.default_rng(seed)
    obs = dsum.mean()
    centered = dsum - obs
    means = centered[rng.integers(0, len(dsum), (n, len(dsum)))].mean(axis=1)
    return float((means >= obs).mean())


def max_drawdown(x: np.ndarray) -> float:
    eq = np.cumsum(x)
    return float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()) if len(x) else 0.0


def main() -> None:
    days = lib.calendar()
    d0 = int(np.searchsorted(days, lib.IS_START))
    d1 = int(np.searchsorted(days, lib.OOS_END, side="right")) - 1
    cands = []
    for path in sorted(glob.glob(os.path.join(OUT, "candidates_round[0-9].json"))):
        rnd = int(path.rsplit("round", 1)[1].split(".")[0])
        cands += [{**c, "round": rnd} for c in json.load(open(path))]
    results = []
    tapes = {}
    for c in cands:
        fam, cfg = c["family"], json.loads(c["params"])
        use_r = FAMILIES[fam]["use_r"]
        sym = c["sym"]
        if sym not in tapes:
            tapes[sym] = (lib.load(sym, days), lib.load(lib.benchmark(sym), days))
        tp, bench = tapes[sym]
        tr = RUNNERS[fam](tp, cfg, d0, d1, bench=bench)
        tid = f"{sym}_{fam}_{c['cfg']}"
        np.save(os.path.join(OUT, f"trades_{tid}.npy"), tr)
        ins = lib.summarize(days, tr, use_r, lib.IS_START, lib.IS_END)
        oos = lib.summarize(days, tr, use_r, lib.OOS_START, lib.OOS_END)
        full = lib.summarize(days, tr, use_r, lib.IS_START, lib.OOS_END)
        halves = [lib.summarize(days, tr, use_r, a, b)["total"] for a, b in HALVES]
        col = 6 if use_r else 4
        results.append({"id": tid, "round": c["round"], "sym": sym, "family": fam, "params": cfg, "unit": "R" if use_r else "ret",
                        "is": ins, "oos": oos, "full": full, "halves": halves,
                        "max_dd": max_drawdown(tr[:, col]),
                        "boot_p_full": bootstrap_p(days, tr, col) if len(tr) > 10 else 1.0})
    q = bh_qvalues([r["oos"]["p"] for r in results]) if results else []
    for r, qq in zip(results, q):
        o, f = r["oos"], r["full"]
        r["q_oos"] = qq
        r["rules"] = {
            "1_holdout": o["per_week"] >= 3 and o["mean"] > 0 and o["pf"] >= 1.15 and o["p"] < 0.05,
            "2_bh": qq < 0.10,
            "3_full": f["win"] >= 0.52 and f["pf"] >= 1.2 and f["mean_s"] > 0,
            "4_stable": sum(h > 0 for h in r["halves"]) >= 4,
        }
        r["pass"] = all(r["rules"].values())
    json.dump(results, open(os.path.join(OUT, "holdout.json"), "w"), indent=1)
    exp = 0.05 * len(results)
    print(f"{len(results)} candidates sent to the holdout; about {exp:.1f} would clear rule 1 by luck alone")
    print(f"rule 1 passes: {sum(r['rules']['1_holdout'] for r in results)}; full passes: {sum(r['pass'] for r in results)}")
    for r in sorted(results, key=lambda r: r["oos"]["p"]):
        o, f = r["oos"], r["full"]
        flags = "".join("Y" if v else "." for v in r["rules"].values())
        print(f"{'PASS' if r['pass'] else '    '} {flags} {r['sym']:5} {r['family']:12} {json.dumps(r['params']):62} "
              f"IS t={r['is']['t']:.2f} | OOS n={o['n']} wk={o['per_week']:.1f} win={o['win']:.3f} "
              f"mean={o['mean']:+.4f} pf={o['pf']:.2f} p={o['p']:.4f} q={r['q_oos']:.3f} | FULL win={f['win']:.3f} "
              f"pf={f['pf']:.2f} stress={f['mean_s']:+.4f} halves+={sum(h > 0 for h in r['halves'])}")


if __name__ == "__main__":
    main()
