# @steered SNARE-2 2026-09-30
"""Write backend/tests/fixtures/overnight_parity.json, the committed input of the overnight T1 test.

Run with the edge hunt research venv (numpy, scipy), on the Mac that holds the research data:

    /Users/jhoshua/AutonomousDayTrader/research/edge_hunt_2026_09_29/.venv/bin/python \
        scripts/overnight_research_fixture.py

It imports the research code read only (lib.calendar, lib.load, families.run_overnight), reruns
overnight {"cond": "none", "exit": "open"} for NVDA, IREN and HUT over the protocol window
(holdout.py: first session >= 2023-10-02 to the last session <= 2026-09-28, capped by
run_overnight at the second to last calendar row), and refuses to write unless the rerun equals
data/trades_<SYM>_overnight_0.npy exactly. One row per stock per session in the window: what a
live robot can know at 15:46:05 (bars starting before 15:45, the 09:30 bar) and the research
outcome. Entry, exit and net return keep their full float repr (no rounding).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys

sys.dont_write_bytecode = True  # never leave __pycache__ in the read only research folder

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RESEARCH = "/Users/jhoshua/AutonomousDayTrader/research/edge_hunt_2026_09_29"
OUT = os.path.join(REPO, "backend", "tests", "fixtures", "overnight_parity.json")
SYMBOLS = ("NVDA", "IREN", "HUT")
CFG = {"cond": "none", "exit": "open"}
COUNT_BEFORE = 375   # minute index of the bar starting 15:45 (index 0 = 09:30)
COLUMNS = ("date", "next", "count_before_1545", "has_0930_bar", "research_ok", "early_close",
           "last_bar_index", "outcome", "entry", "exit", "ret")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, check=True).stdout.strip()


def research_outcome(tp, x, d: int):
    """The reason run_overnight (families.py:89-113, cond none, exit open) trades or skips day d."""
    i1547 = 377  # families.bar_closing_at(1548)
    if not tp.ok[d]:
        return "NOT_OK", None
    if int(tp.end[d]) != 390:
        return "EARLY_CLOSE", None
    if not math.isfinite(tp.open_[d]):
        return "NO_0930_BAR", None
    if not math.isfinite(x["Cff"][d, i1547]):
        return "NO_1547_PRICE", None
    if not math.isfinite(tp.close[d]):
        return "NO_CLOSE_BAR", None
    if not tp.ok[d + 1]:
        return "NEXT_NOT_OK", None           # X1: look ahead, dropped by the live rule
    if not math.isfinite(tp.open_[d + 1]):
        return "NEXT_NO_0930_BAR", None      # X10: look ahead
    return "TRADE", (float(tp.close[d]), float(tp.open_[d + 1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", default=DEFAULT_RESEARCH)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    research = os.path.abspath(args.research)
    sys.path.insert(0, research)
    import numpy as np
    import lib
    import families

    days = lib.calendar()
    d0 = int(np.searchsorted(days, lib.IS_START))
    d1 = int(np.searchsorted(days, lib.OOS_END, side="right")) - 1
    last = min(d1, len(days) - 2)            # run_overnight's own loop bound
    data = os.path.join(research, "data")
    stocks = {}
    for sym in SYMBOLS:
        tp = lib.load(sym, days)
        tr = families.run_overnight(tp, CFG, d0, d1)
        saved = np.load(os.path.join(data, f"trades_{sym}_overnight_0.npy"))
        if tr.shape != saved.shape or not np.array_equal(tr, saved, equal_nan=True):
            raise SystemExit(f"{sym}: rerun does not equal trades_{sym}_overnight_0.npy, fixture not written")
        by_day = {int(r[0]): r for r in tr}
        x = families.prep(tp)
        rows = []
        for d in range(d0, last + 1):
            finite = np.isfinite(tp.C[d, :int(tp.end[d])])
            idx = np.flatnonzero(finite)
            outcome, px = research_outcome(tp, x, d)
            if (outcome == "TRADE") != (d in by_day):
                raise SystemExit(f"{sym} {days[d]}: outcome {outcome} disagrees with the trade file")
            entry = exit_ = ret = None
            if px is not None:
                r = by_day[d]
                if (float(r[2]), float(r[3])) != px:
                    raise SystemExit(f"{sym} {days[d]}: trade prices disagree with the replicated rule")
                entry, exit_, ret = float(r[2]), float(r[3]), float(r[4])
            rows.append([int(days[d]), int(days[d + 1]), int(np.isfinite(tp.C[d, :COUNT_BEFORE]).sum()),
                         bool(math.isfinite(tp.O[d, 0])), bool(tp.ok[d]), bool(int(tp.end[d]) != 390),
                         int(idx[-1]) if len(idx) else -1, outcome, entry, exit_, ret])
        stocks[sym] = {"trades": int(len(tr)), "rows": rows}
        print(f"{sym}: {len(tr)} research nights, {len(rows)} sessions, rerun equals the trade file")

    files = {f"data/trades_{s}_overnight_0.npy": os.path.join(data, f"trades_{s}_overnight_0.npy") for s in SYMBOLS}
    files["data/holdout.json"] = os.path.join(data, "holdout.json")
    for s in SYMBOLS + ("SPY",):
        files[f"data/bars/{s}.npz"] = os.path.join(lib.BARS, f"{s}.npz")
    status = git("status", "--porcelain", "--", "scripts/overnight_research_fixture.py")
    out = {
        "what": "Overnight holds T1 decision parity fixture (PLAN_2026_09_30_overnight_holds.md section 7)",
        "generator": "scripts/overnight_research_fixture.py",
        "generator_commit": git("rev-parse", "HEAD"),
        "generator_clean": status == "",
        "generator_sha256": sha256(os.path.abspath(__file__)),
        "research_dir": research,
        "research_code_sha256": {f: sha256(os.path.join(research, f)) for f in ("lib.py", "families.py", "holdout.py")},
        "config": CFG,
        "window": {"first": int(days[d0]), "last": int(days[last]), "protocol_end": int(lib.OOS_END)},
        "calendar": [int(v) for v in days[d0:last + 2]],
        "early_closes": sorted(int(v) for v in lib.EARLY_CLOSES),
        "sha256": {k: sha256(p) for k, p in sorted(files.items())},
        "columns": list(COLUMNS),
        "stocks": stocks,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
        fh.write("\n")
    print(f"wrote {args.out} ({os.path.getsize(args.out)} bytes)")


if __name__ == "__main__":
    main()
