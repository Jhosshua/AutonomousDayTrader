"""Crux: after a signal bar, does price move the signal's way? Forward return (bps, signed by side) at several horizons,
for ALL raw signals (no gates) and for gated signals (phase + index filter). No exits, no costs."""
import numpy as np, math, sim
from collections import defaultdict
H = [5, 15, 30, 60, 120]
data = {s: sim.load_symbol(s) for s in sim.SYMS}
dates = sorted(set(data["SPY"]) & set(data["QQQ"]))
rows = []
for d in dates:
    spy, qqq = sim.IndexState(), sim.IndexState()
    trend_at = {}
    S, Q = data["SPY"][d], data["QQQ"][d]
    si = {int(S.m[i]): i for i in range(len(S.m))}; qi = {int(Q.m[i]): i for i in range(len(Q.m))}
    for m in range(570, 960):
        if m in si: spy.update(S.o[si[m]], S.h[si[m]], S.l[si[m]], S.c[si[m]], S.v[si[m]])
        if m in qi: qqq.update(Q.o[qi[m]], Q.h[qi[m]], Q.l[qi[m]], Q.c[qi[m]], Q.v[qi[m]])
        trend_at[m] = sim.index_trend(spy, qqq)
    for s in sim.SYMS:
        if d not in data[s]: continue
        day = data[s][d]
        for g in sim.gen_signals(day, sim.SigParams()):
            sgn = 1 if g.side == "LONG" else -1
            n = len(day.c); i = g.i
            fw = {}
            for h in H:
                j = min(n - 1, i + h); fw[h] = sgn * (day.c[j] / day.c[i] - 1) * 1e4
            fw["close"] = sgn * (day.c[n - 1] / day.c[i] - 1) * 1e4
            tr = trend_at.get(g.m, "UNKNOWN")
            gated = (tr == "BULLISH" and sgn > 0) or (tr == "BEARISH" and sgn < 0)
            gated = gated and not (690 <= g.m < 840)
            period = "A" if d < "2025-04-01" else "B" if d < "2026-01-01" else "C"
            rows.append((period, g.side, gated, g.m < 690, g.feats["std_pct"] * 1e4, fw))
def rep(label, sel):
    if not sel: print(label, "n=0"); return
    print(f"{label:34s} n={len(sel):5d} " + " ".join(f"+{h}m {np.mean([r[5][h] for r in sel]):6.1f}±{np.std([r[5][h] for r in sel])/math.sqrt(len(sel)):4.1f}" for h in H) + f" close {np.mean([r[5]['close'] for r in sel]):6.1f}±{np.std([r[5]['close'] for r in sel])/math.sqrt(len(sel)):4.1f}")
print("Forward return after signal, bps, signed in the trade's direction (mean ± SE). Stop is ~40-60 bps away.")
rep("ALL raw signals", rows)
rep("ALL gated (phase+index)", [r for r in rows if r[2]])
for p in "ABC":
    rep(f"period {p} gated", [r for r in rows if r[2] and r[0] == p])
for side in ("LONG", "SHORT"):
    rep(f"gated {side}", [r for r in rows if r[2] and r[1] == side])
rep("gated morning", [r for r in rows if r[2] and r[3]]); rep("gated afternoon", [r for r in rows if r[2] and not r[3]])
rep("NOT gated (filter said no)", [r for r in rows if not r[2]])
