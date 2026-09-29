# @steered SNARE-2 2026-09-29
"""Discovery pass. Simulates ONLY sessions up to IS_END (2025-06-30); holdout bars are never
read into a decision or a statistic here. Writes data/discovery.csv and data/candidates.json."""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import lib
from families import ALL_FAMILIES, FAMILIES, ROUND2, RUNNERS, neighbours

ROUND = int(os.environ.get("EDGE_ROUND", "1"))
ACTIVE = {1: FAMILIES, 2: ROUND2, 3: ALL_FAMILIES}[ROUND]
SUFFIX = "" if ROUND == 1 else f"_round{ROUND}"

OUT = os.path.join(lib.HERE, "data")
UNIVERSE = "universe_round3.txt" if int(os.environ.get("EDGE_ROUND", "1")) == 3 else "universe.txt"
STOCKS = [l.strip() for l in open(os.path.join(lib.HERE, UNIVERSE)) if l.strip()]


def work(sym: str) -> list[dict]:
    days = lib.calendar()
    d0 = int(np.searchsorted(days, lib.IS_START))
    d1 = int(np.searchsorted(days, lib.IS_END, side="right")) - 1
    tp = lib.load(sym, days)
    bench = lib.load(lib.benchmark(sym), days)
    out = []
    for fam, spec in ACTIVE.items():
        for ci, cfg in enumerate(spec["grid"]):
            tr = RUNNERS[fam](tp, cfg, d0, d1, bench=bench)
            s = lib.summarize(days, tr, spec["use_r"], lib.IS_START, lib.IS_END)
            out.append({"sym": sym, "family": fam, "cfg": ci, "params": json.dumps(cfg), **s})
    return out


def select(rows: list[dict]) -> list[dict]:
    by = {}
    for r in rows:
        by.setdefault((r["sym"], r["family"]), {})[r["cfg"]] = r
    cands = []
    for (sym, fam), cfgs in sorted(by.items()):
        grid = ALL_FAMILIES[fam]["grid"]
        best = None
        for ci, r in cfgs.items():
            ok = (r["per_week"] >= 3 and r["mean"] > 0 and r["win"] >= 0.5 and r["pf"] >= 1.2 and r["t"] >= 2.0)
            if not ok:
                continue
            nt = [cfgs[j]["t"] for j in neighbours(grid, ci)]
            r["nbr_median_t"] = float(np.median(nt)) if nt else 0.0
            if r["nbr_median_t"] < 1.0:
                continue
            if best is None or r["t"] > best["t"]:
                best = r
        if best:
            cands.append(best)
    return cands


def main() -> None:
    t0 = time.time()
    with Pool(int(sys.argv[1]) if len(sys.argv) > 1 else 7) as pool:
        rows = [r for part in pool.imap_unordered(work, STOCKS) for r in part]
    rows.sort(key=lambda r: (r["sym"], r["family"], r["cfg"]))
    with open(os.path.join(OUT, f"discovery{SUFFIX}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cands = select(rows)
    with open(os.path.join(OUT, f"candidates{SUFFIX}.json"), "w") as f:
        json.dump(cands, f, indent=1)
    n_sig = sum(1 for r in rows if r["p"] < 0.01)
    print(f"{len(rows)} discovery tests in {time.time() - t0:.0f}s; {n_sig} with p<.01 "
          f"(about {0.01 * len(rows):.0f} expected by chance); {len(cands)} candidates")
    for c in sorted(cands, key=lambda r: -r["t"]):
        print(f"{c['sym']:5} {c['family']:12} {c['params']:70} n={c['n']:4} wk={c['per_week']:.1f} "
              f"win={c['win']:.3f} mean={c['mean']:+.4f} pf={c['pf']:.2f} t={c['t']:.2f} nbr={c['nbr_median_t']:.2f}")


if __name__ == "__main__":
    main()
