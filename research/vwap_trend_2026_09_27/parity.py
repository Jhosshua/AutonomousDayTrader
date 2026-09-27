"""Prove sim.gen_signals == VWAPPullbackStrategy.on_bar on a random sample of symbol-days."""
import random, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy
from backend.app.models.events import BarEvent
import sim

random.seed(7)
syms = sim.SYMS
sample = []
for s in syms:
    days = sim.load_symbol(s)
    for d in random.sample(sorted(days.keys()), 12):
        sample.append((s, d))
mism = 0; total_real = 0; total_sim = 0; checked = 0
for s, d in sample:
    day = sim.load_symbol(s)[d]
    strat = VWAPPullbackStrategy(); strat.reset_daily_stats()
    real = []
    for i in range(len(day.m)):
        b = BarEvent(s, float(day.o[i]), float(day.h[i]), float(day.l[i]), float(day.c[i]), int(day.v[i]), day.ts[i])
        for g in strat.on_bar(b):
            real.append((int(day.m[i]), "LONG" if g.side.value == "BUY" else "SHORT", round(g.entry_price, 4),
                         round(g.stop_loss, 4), round(g.take_profit_1, 4), round(g.take_profit_2, 4),
                         bool(g.target_1_is_r_fallback), bool(g.target_2_is_r_fallback)))
    mine = [(g.m, g.side, round(g.entry, 4), round(g.stop, 4), round(g.tp1, 4), round(g.tp2, 4), g.fb1, g.fb2)
            for g in sim.gen_signals(day, sim.SigParams())]
    total_real += len(real); total_sim += len(mine); checked += 1
    if real != mine:
        mism += 1
        if mism <= 3:
            print("MISMATCH", s, d); print(" real", real[:4]); print(" sim ", mine[:4])
print(f"symbol-days checked {checked}, real signals {total_real}, sim signals {total_sim}, mismatching days {mism}")
