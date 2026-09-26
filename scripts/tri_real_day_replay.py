"""Real TSLA/CDE days through main.handle_bar_event (offline, no broker) vs the research fill simulator.

Usage: python3 scripts/tri_real_day_replay.py [TSLA|CDE]. Needs the lab data under /Users/mo/megacap_*_intraday_edge_lab."""
import asyncio, sys, logging
from datetime import timedelta
import pandas as pd
sys.path.insert(0, "/Users/mo/AutonomousDayTrader"); sys.path.insert(0, "/Users/mo/megacap_intraday_edge_lab")
logging.disable(logging.CRITICAL)
from research.simulator import simulate_trade_execution
from scripts import run_tri_engine_dry_run as dry
from scripts import tri_research_parity as par
from backend.app.core.trading_windows import ET
r = dry.r
SYM = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
if SYM == "CDE":
    from pathlib import Path as _P
    par.LAB = _P("/Users/mo/megacap_midcap_intraday_edge_lab/data")
    def _load(symbol, premarket=False):
        df = pd.read_parquet(par.LAB/"dev"/f"{symbol}_1min.parquet")
        df = df[(df.minute_et >= "09:30") & (df.minute_et < "16:00")]
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        return df.sort_values("timestamp_utc").reset_index(drop=True)
    par.load = _load
stock, qqq = par.load(SYM), par.load("QQQ")
if SYM == "TSLA": stock = stock[stock.session_date_et >= "2026-01-01"]
qday = dict(tuple(qqq.groupby("session_date_et")))
closes = {d: g.timestamp_utc.max().tz_convert(ET).to_pydatetime()+timedelta(minutes=1) for d, g in qqq.groupby("session_date_et")}
from datetime import datetime as _dt
def _bounds(day):
    c = closes.get(day.isoformat())
    return (_dt.combine(day, _dt.min.time(), ET).replace(hour=9, minute=30), c) if c else None
if SYM == "CDE":
    from backend.app.strategies import tri_engine; from backend.app.core import tri_execution
    tri_engine.session_bounds = _bounds; tri_execution.session_bounds = _bounds

async def main():
    ok = bad = none = 0; rows = []
    with dry.no_network():
        for day, ddf in stock.groupby("session_date_et"):
            dry.configure((SYM,))
            ev = par.bars(ddf, SYM) + par.bars(qday[day], "QQQ"); ev.sort(key=lambda b: (b.timestamp, b.symbol != SYM))
            for b in ev:
                await r.handle_bar_event(b)
            s = r.tri_controller.by_symbol[SYM]
            trades = [t for t in r.pending_trade_records.values() if t["strategy_id"] == s.strategy_id]
            if s.signal is None:
                none += 1; continue
            bb = {pd.Timestamp(b.timestamp).tz_convert("UTC"): {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in ev if b.symbol == SYM}
            close = max(bb) + pd.Timedelta(minutes=1); ts = pd.Timestamp(s.signal.timestamp).tz_convert("UTC")
            side = "long" if s.side == "LONG" else "short"; st = s.stop
            exp = []
            for mult, hold in (((1.5, 180), (2.0, 240)) if SYM == "TSLA" else ((2.0, 180),)):
                tgt = (lambda e, x, m=mult: e + m*(e-x)) if side == "long" else (lambda e, x, m=mult: e - m*(x-e))
                t, a = simulate_trade_execution(SYM, "x", day, side, ts, ts+pd.Timedelta(minutes=1), close, bb, lambda e, b, q: st, tgt, hold)
                exp.append((round(t.exit_price_raw, 4), t.exit_reason) if t else (None, a))
            got = [(round(t["exit_notional"]/t["qty"], 4), t["exit_reason"]) for t in s.tranches] if trades else []
            same = len(got) == len(exp) and all(e[0] is not None for e in exp) and all(abs(g[0]-e[0]) < 1e-6 for g, e in zip(got, exp))
            ok += same; bad += not same
            if not same: rows.append((day, side, got, exp, s.phase, s.reason))
    r.reset_runtime_state(); r.set_simulation_mode(False)
    print(f"{SYM} days: trades matched={ok} mismatched={bad} no-signal={none}")
    rest = [x for x in rows if not any(e[1] == "MISSING_HOLDING_PATH_BAR" for e in x[3])]
    print("explained by research skipping gap days:", len(rows)-len(rest))
    for x in rest: print("  ", x)
asyncio.run(main())
