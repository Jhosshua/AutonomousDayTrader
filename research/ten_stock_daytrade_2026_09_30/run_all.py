# @steered SNARE-2 2026-09-30
"""Compute trades for every name, family and config over the whole window, once.

Selection (discover.py) reads only design slices of this file. The holdout is scored in holdout.py.
Output data/trades.pkl: {(sym, fam, cfg_index): trades array}, plus the TSLA benchmark under fam "TSLA_OR15".
"""
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from core import DAYS, HOLD_END, NAMES, WIN_START, day_index, load
from fam import FAMILIES, RUNNERS, benchmark, eligible, run_tsla_or15

OUT = "data/trades.pkl"


def one(sym: str) -> dict:
    t0 = time.time()
    tp = load(sym)
    qqq = load("QQQ")
    bench = load(benchmark(sym)) if sym != "SPY" else None
    d0, d1 = day_index(DAYS, WIN_START, HOLD_END)
    res = {}
    for fam, spec in FAMILIES.items():
        if not eligible(fam, sym):
            continue
        for j, cfg in enumerate(spec["grid"]):
            res[(sym, fam, j)] = RUNNERS[fam](tp, cfg, d0, d1, bench=bench)
    res[(sym, "TSLA_OR15", 0)] = run_tsla_or15(tp, {}, d0, d1, qqq=qqq)
    print(f"{sym}: {len(res)} configs, {sum(len(v) for v in res.values())} trades, {time.time() - t0:.0f}s", flush=True)
    return res


def main() -> None:
    names = sys.argv[1].split(",") if len(sys.argv) > 1 else NAMES
    allres = {}
    with ProcessPoolExecutor(max_workers=5) as ex:
        for r in ex.map(one, names):
            allres.update(r)
    with open(OUT, "wb") as f:
        pickle.dump(allres, f)
    print("configs", len(allres))


if __name__ == "__main__":
    main()
