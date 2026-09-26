"""Backtest the plan as written (09:30 range) vs as researched (pre-market range) with the research fill simulator.

Usage: python3 scripts/tri_variant_backtest.py [TSLA|CDE]. In-sample only; numbers are not a forecast."""
import sys, importlib.util
from datetime import time, timedelta
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/mo/AutonomousDayTrader"); sys.path.insert(0, "/Users/mo/megacap_intraday_edge_lab")
from research.simulator import simulate_trade_execution
spec = importlib.util.spec_from_file_location("par", "/Users/mo/AutonomousDayTrader/scripts/tri_research_parity.py")
par = importlib.util.module_from_spec(spec); spec.loader.exec_module(par)
from backend.app.strategies import tri_engine
from backend.app.strategies.tri_engine import AsymmetricDualStrategy
from backend.app.models.events import BarEvent, OrderSide
from backend.app.core.trading_windows import ET

SYM = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
PARTS = ("dev","holdout")
if SYM == "CDE":
    from pathlib import Path as _P
    par.LAB = _P("/Users/mo/megacap_midcap_intraday_edge_lab/data"); PARTS = ("dev",)
    _orig = par.load
    def _load(symbol, premarket=False):
        df = pd.concat([pd.read_parquet(par.LAB/pp/f"{symbol}_1min.parquet") for pp in PARTS], ignore_index=True)
        df = df[(df.minute_et < "09:30")] if premarket else df[(df.minute_et >= "09:30") & (df.minute_et < "16:00")]
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        return df.sort_values("timestamp_utc").reset_index(drop=True)
    par.load = _load
stock, qqq = par.load(SYM), par.load("QQQ")
pre_s = dict(tuple(par.load(SYM, True).groupby("session_date_et"))); pre_q = dict(tuple(par.load("QQQ", True).groupby("session_date_et")))
full = pd.concat([pd.read_parquet(par.LAB/p/f"{SYM}_1min.parquet") for p in PARTS]); full["timestamp_utc"]=pd.to_datetime(full.timestamp_utc,utc=True)
full_by_day = dict(tuple(full.groupby("session_date_et")))
closes = {d: g.timestamp_utc.max().tz_convert(ET).to_pydatetime()+timedelta(minutes=1) for d,g in stock.groupby("session_date_et")}
tri_engine.session_bounds = lambda day: (pd.Timestamp(day).tz_localize(ET).to_pydatetime().replace(hour=9,minute=30), closes[day.isoformat()]) if day.isoformat() in closes else None
qday = dict(tuple(qqq.groupby("session_date_et")))
orig_cut = AsymmetricDualStrategy.short_cutoff

def signals(emulate):
    AsymmetricDualStrategy.short_cutoff = property(lambda s: time(11,1)) if emulate else orig_cut
    out = {}
    for day, ddf in stock.groupby("session_date_et"):
        if len(ddf) < 60: continue
        s = AsymmetricDualStrategy(SYM)
        ev = par.bars(ddf, SYM) + par.bars(qday.get(day, qqq.iloc[:0]), "QQQ"); ev.sort(key=lambda b:(b.timestamp, b.symbol!=SYM))
        ps, pq = pre_s.get(day), pre_q.get(day)
        if emulate and pq is not None and len(pq) and pq.volume.sum() > 0:
            first = ev[0].timestamp.replace(hour=9, minute=29); s.start_session(first.date())
            v = float(pq.volume.sum()); s.bars["QQQ"].append(BarEvent("QQQ",1,1,1,float((pq.close*pq.volume).sum()/v),v,first))
        for b in ev:
            s.on_completed_bar(b, b.timestamp+timedelta(minutes=1))
            if emulate and b.symbol==SYM and ps is not None and len(ps) and b.timestamp.strftime("%H:%M")=="09:44" and s.or_high is not None:
                s.or_high=max(s.or_high,float(ps.high.max())); s.or_low=min(s.or_low,float(ps.low.min())); s.or_mid=(s.or_high+s.or_low)/2
        if s.signal is not None:
            out[day] = ("long" if s.signal.side==OrderSide.BUY else "short", pd.Timestamp(s.signal.timestamp).tz_convert("UTC"), s.or_low, s.or_mid)
    return out

def run(sig, mult, hold, regular_close=True):
    rs=[]
    for day,(side,ts,orl,orm) in sig.items():
        f = full_by_day[day]
        if regular_close:
            f = f[(f.minute_et>="09:30")&(f.minute_et<"16:00")]
        bb = {r.timestamp_utc: {"open":r.open,"high":r.high,"low":r.low,"close":r.close,"volume":r.volume} for r in f.itertuples()}
        close = f.timestamp_utc.max()+pd.Timedelta(minutes=1)
        stop = (lambda e,b,s_,v=orl: v) if side=="long" else (lambda e,b,s_,v=orm: v)
        tgt = (lambda e,st,m=mult: e+m*(e-st)) if side=="long" else (lambda e,st,m=mult: e-m*(st-e))
        t,a = simulate_trade_execution(SYM,"x",day,side,ts,ts+pd.Timedelta(minutes=1),close,bb,stop,tgt,hold)
        if t is not None and a=="TRADE": rs.append(t.to_dict())
    d = pd.DataFrame(rs); r=d.normal_net_r.values; rng=np.random.default_rng(42)
    boot=[rng.choice(r,len(r)).mean() for _ in range(10000)]
    w=r[r>0].sum(); l=-r[r<0].sum()
    return f"n={len(r):3d} win={np.mean(r>0):.1%} netR={r.sum():+6.1f} avgR={r.mean():+.3f} stressR={d.stress_net_r.sum():+6.1f} PF={w/l:.2f} p={np.mean(np.array(boot)<=0):.3f} longs={int((d.side=='long').sum())} shorts={int((d.side=='short').sum())}"

for name, emu in (("AS BACKTESTED (pre-market range)", True), ("AS WRITTEN (09:30-09:44 range) = live", False)):
    sig = signals(emu)
    print(f"\n{SYM} {name}: {len(sig)} signal days")
    if emu: print("  research original 2R/120m, 20:00 close:", run(sig, 2.0, 120, regular_close=False))
    if SYM=="TSLA":
        print("  plan 1.5R/180m:", run(sig, 1.5, 180)); print("  plan 2.0R/240m:", run(sig, 2.0, 240))
    else:
        print("  plan 2.0R/180m:", run(sig, 2.0, 180))
