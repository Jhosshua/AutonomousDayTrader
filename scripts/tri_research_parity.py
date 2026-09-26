#!/usr/bin/env python3
"""Replay real TSLA+QQQ minute bars through the LIVE signal code and compare
each day's signal with the research script that produced the plan's numbers.

Research: /Users/mo/multi_stock_edge_lab_3yr/research/run_full_dual_audit.py
(saved signals: artifacts/tsla_dual_engine_signals.parquet). Read only; no
broker, no network. Only the signal state machine is exercised here; the
order lifecycle is covered by backend/tests/unit/test_tri_*.py.

Finding (2026-09-25): the research builds its "opening range" and QQQ VWAP
from ALL bars in its files, which start at 04:00 ET pre-market, and allows
shorts on the 11:00 bar. The plan text says 09:30-09:44 and "prior to 11:00".
--emulate-research copies those three quirks into the replay only, to show
the rest of the live logic matches the research bar for bar.

Usage: python3 scripts/tri_research_parity.py [--symbol TSLA] [--emulate-research]
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.core.trading_windows import ET
from backend.app.models.events import BarEvent, OrderSide
from backend.app.strategies import tri_engine
from backend.app.strategies.tri_engine import AsymmetricDualStrategy

LAB = Path("/Users/mo/megacap_intraday_edge_lab/data")
SIGNALS = Path("/Users/mo/multi_stock_edge_lab_3yr/artifacts/tsla_dual_engine_signals.parquet")


def load(symbol: str, premarket: bool = False) -> pd.DataFrame:
    frames = [pd.read_parquet(LAB / part / f"{symbol}_1min.parquet") for part in ("dev", "holdout")]
    df = pd.concat(frames, ignore_index=True)
    df = df[(df.minute_et < "09:30")] if premarket else df[(df.minute_et >= "09:30") & (df.minute_et < "16:00")]
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df.sort_values("timestamp_utc").reset_index(drop=True)


def bars(df: pd.DataFrame, symbol: str) -> list[BarEvent]:
    return [BarEvent(symbol, r.open, r.high, r.low, r.close, float(r.volume),
                     r.timestamp_utc.to_pydatetime().astimezone(ET)) for r in df.itertuples()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="TSLA")
    ap.add_argument("--emulate-research", action="store_true")
    args = ap.parse_args()
    stock, qqq = load(args.symbol), load("QQQ")
    pre_stock = dict(tuple(load(args.symbol, True).groupby("session_date_et")))
    pre_qqq = dict(tuple(load("QQQ", True).groupby("session_date_et")))
    if args.emulate_research:
        AsymmetricDualStrategy.short_cutoff = property(lambda self: time(11, 1))  # research: minute <= "11:00"
    research = pd.read_parquet(SIGNALS)
    research = {r.session_date_et: (r.side.upper(), pd.Timestamp(r.signal_timestamp_utc).tz_convert(ET).strftime("%H:%M"))
                for r in research.itertuples() if r.symbol == args.symbol}
    closes = {d: g.timestamp_utc.max().tz_convert(ET).to_pydatetime() + timedelta(minutes=1)
              for d, g in stock.groupby("session_date_et")}

    # Historical calendar: the live table only covers 2026-2027.
    def bounds(day):
        close = closes.get(day.isoformat())
        return (datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30), close) if close else None
    tri_engine.session_bounds = bounds

    qqq_by_day = dict(tuple(qqq.groupby("session_date_et")))
    outcome = Counter()
    mismatches = []
    for day, day_df in stock.groupby("session_date_et"):
        if len(day_df) < 60:
            continue  # the research skips thin sessions the same way
        s = AsymmetricDualStrategy(args.symbol)
        events = bars(day_df, args.symbol) + bars(qqq_by_day.get(day, qqq.iloc[:0]), "QQQ")
        events.sort(key=lambda b: (b.timestamp, b.symbol != args.symbol))
        pm_s, pm_q = pre_stock.get(day), pre_qqq.get(day)
        if args.emulate_research and pm_q is not None and len(pm_q):
            # One synthetic 09:29 bar carrying the pre-market close*volume sums
            # reproduces the research's cumulative VWAP exactly.
            vol = float(pm_q.volume.sum())
            if vol > 0:
                first = events[0].timestamp.replace(hour=9, minute=29)
                s.start_session(first.date())
                s.bars["QQQ"].append(BarEvent("QQQ", 1, 1, 1, float((pm_q.close * pm_q.volume).sum() / vol), vol, first))
        for b in events:
            s.on_completed_bar(b, b.timestamp + timedelta(minutes=1))
            if (args.emulate_research and b.symbol == args.symbol and pm_s is not None and len(pm_s)
                    and b.timestamp.strftime("%H:%M") == "09:44" and s.or_high is not None):
                s.or_high = max(s.or_high, float(pm_s.high.max()))
                s.or_low = min(s.or_low, float(pm_s.low.min()))
                s.or_mid = (s.or_high + s.or_low) / 2
        live = None
        if s.signal is not None:
            live = ("LONG" if s.signal.side == OrderSide.BUY else "SHORT",
                    s.signal.timestamp.astimezone(ET).strftime("%H:%M"))
        ref = research.get(day)
        if live == ref:
            outcome["match_trade" if ref else "match_no_trade"] += 1
        else:
            outcome["mismatch"] += 1
            mismatches.append((day, ref, live, s.phase, s.reason))
    total = sum(outcome.values())
    print(f"{args.symbol}: {total} sessions  {dict(outcome)}")
    for m in mismatches:
        print("  MISMATCH", m[0], "research=", m[1], "live=", m[2], "phase=", m[3], "reason=", m[4])
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
