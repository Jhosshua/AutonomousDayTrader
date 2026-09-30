#!/usr/bin/env python3
# @steered SNARE-2 2026-09-30
"""T12 overnight holds dry run (PLAN_2026_09_30_overnight_holds.md section 7).

Real one minute SIP bars (the edge hunt research bars, split adjusted, regular session) for
Thu 2026-09-24, Fri 2026-09-25 and Mon 2026-09-28 are replayed through backend.app.main's real
handlers (handle_bar_event, the runtime clock step, the broker compare) with every other
strategy on, against the fake Alpaca of backend/tests/unit/overnight_integration/fakes.py
(cls, opg and day market semantics, one sell order per position 40310000, wash trade 403). The
closing auction fills at the close of the 15:59 bar and the opening auction at the open of the
09:30 bar (research style prices). Two nights: Thu to Fri, and Fri to Mon over the weekend.

The same replay runs twice, OVERNIGHT_MODE=live and OVERNIGHT_MODE=off, and the day strategies'
decisions and orders are compared. No network: every httpx transport that is not the fake's
MockTransport raises, and the count of such attempts is part of the report.

Run: .venv/bin/python scripts/run_overnight_dry_run.py
The research bars are read (never written) with the research venv's numpy, which this venv does
not have: /Users/jhoshua/AutonomousDayTrader/research/edge_hunt_2026_09_29/.venv/bin/python.
Writes docs/overnight_holds/DRY_RUN_REPORT.md. Exit code 0 only when every assertion holds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PERSISTENCE_ENABLED", "false")
for key in list(os.environ):
    if key.startswith(("ALPACA_", "APCA_")) or key == "RELAY_TOKEN":
        del os.environ[key]

import httpx  # noqa: E402

BLOCKED: List[str] = []


def _refuse_sync(self, request):
    BLOCKED.append(f"{request.method} {request.url}")
    raise AssertionError(f"real network request attempted: {request.method} {request.url}")


async def _refuse_async(self, request):
    BLOCKED.append(f"{request.method} {request.url}")
    raise AssertionError(f"real network request attempted: {request.method} {request.url}")


httpx.HTTPTransport.handle_request = _refuse_sync
httpx.AsyncHTTPTransport.handle_async_request = _refuse_async

RESEARCH = Path("/Users/jhoshua/AutonomousDayTrader/research/edge_hunt_2026_09_29")
RESEARCH_PY = RESEARCH / ".venv/bin/python"
DAYS = [date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28)]
REPORT = ROOT / "docs/overnight_holds/DRY_RUN_REPORT.md"
EQUITY = 49_700.0

EXTRACT = r'''
import json, sys, numpy as np
root, days, syms, out = sys.argv[1], [int(d) for d in sys.argv[2].split(",")], sys.argv[3].split(","), sys.argv[4]
res = {}
for s in syms:
    try:
        z = np.load(f"{root}/data/bars/{s}.npz")
    except FileNotFoundError:
        continue
    day = z["day"]
    per = {}
    for d in days:
        m = day == d
        if m.any():
            per[str(d)] = [[int(a), float(b), float(c), float(e), float(f), float(g)] for a, b, c, e, f, g in
                           zip(z["minute"][m], z["o"][m], z["h"][m], z["l"][m], z["c"][m], z["v"][m])]
    res[s] = per
json.dump(res, open(out, "w"))
'''


def load_bars(symbols: List[str]) -> Dict[str, Dict[date, List[list]]]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bars.json"
        subprocess.run([str(RESEARCH_PY), "-c", EXTRACT, str(RESEARCH), ",".join(d.strftime("%Y%m%d") for d in DAYS),
                        ",".join(symbols), str(out)], check=True)
        raw = json.loads(out.read_text())
    return {s: {date(int(k[:4]), int(k[4:6]), int(k[6:])): rows for k, rows in per.items()} for s, per in raw.items()}


def run(mode: str, bars: Dict[str, Dict[date, List[list]]]) -> Dict[str, Any]:
    from backend.app import main as r
    from backend.app.core import overnight_schedule as osch
    from backend.app.models.events import BarEvent
    from backend.tests.unit.overnight_integration.fakes import MainOvernight, OPEN_STATES, at

    ET = osch.ET
    h = MainOvernight(r, at(DAYS[0], 9, 25), equity=EQUITY, build=False)
    alpaca = h.alpaca
    orig_post = alpaca._post

    def post(body):  # a marketable limit (the engine only sends one once the bar crossed it) fills at its limit
        resp = orig_post(body)
        row = alpaca.by_coid(body["client_order_id"])
        if row is not None and row["type"] == "limit" and row["status"] in OPEN_STATES and alpaca.regular_hours():
            alpaca.fill(row["id"], None, float(row["limit_price"]))
            resp = httpx.Response(200, json=alpaca.view(row, True))
        return resp

    alpaca._post = post
    r.or15_now = lambda: h.clock.now

    def bar_count(sym: str, d: date) -> Tuple[int, bool]:
        """What the SIP REST read returns at 15:46:05: that day's regular session bars so far."""
        rows = bars.get(sym, {}).get(d, [])
        starts = [at(d, row[0] // 60, row[0] % 60) for row in rows if row[0] < 15 * 60 + 46]
        return osch.count_bars_before_cutoff(starts, d)

    h._bars = bar_count
    h.build(mode)
    decisions: List[tuple] = []
    real_record = r._record_decision

    def record(signal, outcome, detail, stages=None):
        decisions.append((signal.timestamp.isoformat(), signal.strategy_id, signal.symbol,
                          str(getattr(signal.side, "value", signal.side)), outcome))
        return real_record(signal, outcome, detail, stages)

    r._record_decision = record
    compares: List[dict] = []

    def fake_cash() -> float:
        cash = EQUITY
        for o in alpaca.orders.values():
            q, px = int(o["filled_qty"]), float(o.get("filled_avg_price") or 0.0)
            cash += (-q * px) if o["side"] == "buy" else (q * px)
        return round(cash, 2)

    def compare(tag: str) -> None:
        h.compare()
        local, remote = h.local(), alpaca.signed_positions()
        marks = sum(p["qty"] * p["avg"] for p in alpaca.positions.values())
        compares.append({"at": h.clock.now.astimezone(ET).isoformat(), "tag": tag, "match": local == remote,
                         "mismatch_flag": bool(r.broker_state["mismatch"]), "local": local, "alpaca": remote,
                         "cash_drift": round(r.account.cash - fake_cash(), 2),
                         "equity_drift_buy_marks": round(
                             (r.account.cash + sum(p.shares * p.avg_entry_price * (1 if p.side.value == "LONG" else -1)
                                                   for p in r.account.positions.values()))
                             - (fake_cash() + marks), 2)})

    def minute_bars(d: date, minute: int) -> List[BarEvent]:
        out = []
        for sym, per in bars.items():
            for row in per.get(d, []):
                if row[0] == minute:
                    ts = at(d, minute // 60, minute % 60).astimezone(timezone.utc)
                    out.append(BarEvent(sym, row[1], row[2], row[3], row[4], int(row[5]), ts))
        return out

    def auction_prices(d: date, minute: int, field: int) -> Dict[str, float]:
        return {s: row[field] for s in osch.SYMBOLS for row in bars.get(s, {}).get(d, []) if row[0] == minute}

    for i, d in enumerate(DAYS):
        h.run(at(d, 9, 0), every=600)
        h.run(at(d, 9, 29, 55), every=30)
        h.set(at(d, 9, 30))
        alpaca.open_auction(auction_prices(d, 9 * 60 + 30, 1))       # opg (and queued day) sales at the 09:30 open
        for minute in range(9 * 60 + 30, 16 * 60):
            for b in minute_bars(d, minute):
                alpaca.prices[b.symbol] = b.close
            close_at = at(d, minute // 60, minute % 60) + timedelta(minutes=1)
            # the clock between bars: every 5 s in the overnight windows, else once
            dense = minute in range(9 * 60 + 30, 9 * 60 + 33) or minute in range(15 * 60 + 44, 15 * 60 + 51)
            h.run(close_at - timedelta(seconds=1), every=5 if dense else 59)
            h.set(close_at)
            for b in minute_bars(d, minute):
                h.r.latest_market_prices[b.symbol] = b.close
                asyncio.run(r.handle_bar_event(b))
            h.step()
            if minute % 30 == 0 or minute in (9 * 60 + 31, 15 * 60 + 46, 15 * 60 + 50, 15 * 60 + 56, 15 * 60 + 58):
                compare(f"{minute // 60:02d}:{minute % 60:02d}")
        h.set(at(d, 16, 0))
        alpaca.close_auction(auction_prices(d, 15 * 60 + 59, 4))     # cls buys at the 15:59 bar close
        alpaca.expire_day_orders()
        h.run(at(d, 16, 1), every=5)
        compare("16:01")
        h.run(at(d, 19, 2), every=60)
        compare("19:02")
        if i + 1 < len(DAYS):
            nxt = DAYS[i + 1]
            h.run(at(nxt, 8, 59, 30), every=1800)
            compare("08:59")

    ctl = r.overnight.controller
    nights = {k: {f: n[f] for f in ("symbol", "buy_date", "sale_date", "state", "reason", "held_qty", "buy_avg",
                                      "realized", "needs_look")} for k, n in ctl.state["nights"].items()}
    day_orders = [(o.symbol, o.side.value, o.qty, o.strategy_id, o.status.value) for o in r.engine.orders.values()
                  if not osch.is_overnight(o) and o.strategy_id != "OVERNIGHT_X6"]
    x6 = [(o.symbol, o.qty, o.status.value) for o in r.engine.orders.values() if o.strategy_id == "OVERNIGHT_X6"]
    trades = sorted(r.pending_trade_records.values(), key=lambda t: t["closed_at"])
    out = {
        "mode": mode, "nights": nights, "decisions": decisions, "day_orders": day_orders, "x6": x6,
        "compares": compares, "trades": [{k: t.get(k) for k in ("trade_id", "session_date", "symbol", "strategy_id",
                                                                "quantity", "avg_entry_price", "avg_exit_price",
                                                                "realized_pnl")} for t in trades],
        "overnight_posts": [b for b in alpaca.posts() if b["client_order_id"].startswith("adt-ovn-")],
        "all_posts": len(alpaca.posts()), "wash_refusals": list(alpaca.wash_refusals),
        "refused_40310000": list(alpaca.refused_403), "hosts": sorted(set(alpaca.network_hosts)),
        "final_equity": r.account.equity, "final_cash": r.account.cash, "fake_cash": fake_cash(),
        "positions_end": h.local(), "alpaca_end": alpaca.signed_positions(),
    }
    r._record_decision = real_record
    r.reset_runtime_state()
    r.engine.broker = None
    r.alpaca_broker = None
    return out


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    for noisy in ("AutonomousDayTrader", "engine", "overnight_execution", "overnight_integration", "httpx"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    from backend.app.config import settings
    symbols = sorted({s.upper() for s in settings.WATCHLIST_SYMBOLS} | {s.upper() for s in settings.SWING_SYMBOLS}
                     | {settings.SWING_BENCHMARK, "TSLA", "CDE", "IREN", "HUT"} | set(settings.REGIME_SYMBOLS))
    bars = load_bars(symbols)
    live = run("live", bars)
    off = run("off", bars)

    checks: List[Tuple[str, bool, str]] = []
    thu, fri, mon = DAYS
    want = {f"{s}:{thu}": fri for s in ("NVDA", "IREN", "HUT")} | {f"{s}:{fri}": mon for s in ("NVDA", "IREN", "HUT")}
    for key, sale in want.items():
        n = live["nights"].get(key)
        checks.append((f"{key} bought and sold on {sale}", bool(n and n["state"] == "SOLD" and n["sale_date"] == sale.isoformat()),
                       json.dumps(n, default=str)))
    buys = [b for b in live["overnight_posts"] if b["side"] == "buy"]
    sells = [b for b in live["overnight_posts"] if b["side"] == "sell"]

    def night_of(b):
        d = b["client_order_id"].split("-")[3]
        return live["nights"].get(f"{b['symbol']}:{d[:4]}-{d[4:6]}-{d[6:]}")

    # three buy days (Mon's hold sells Tue 09-29, after the data ends, so it is held at the end)
    checks.append(("buys are closing auction orders, one per stock per buy day",
                   len(buys) == 9 and all(b["time_in_force"] == "cls" for b in buys),
                   f"{len(buys)} buys, tifs {sorted({b['time_in_force'] for b in buys})}"))
    checks.append(("each sale is one opening auction order for exactly the booked shares",
                   len(sells) == 9 and all(b["time_in_force"] == "opg" and night_of(b) is not None
                                           and int(b["qty"]) == night_of(b)["held_qty"] for b in sells),
                   f"{len(sells)} sells"))
    mon_nights = [live["nights"].get(f"{s}:{mon}") for s in ("NVDA", "IREN", "HUT")]
    checks.append(("Monday's holds are held at the end with their sale queued for Tue 2026-09-29",
                   all(n and n["state"] == "SALE_QUEUED" and n["sale_date"] == "2026-09-29" for n in mon_nights)
                   and live["positions_end"] == live["alpaca_end"]
                   and set(live["positions_end"]) >= {"NVDA", "IREN", "HUT"},
                   f"end bot {live['positions_end']} fake {live['alpaca_end']}"))
    checks.append(("overnight mode off sends no overnight order", off["overnight_posts"] == [], str(off["overnight_posts"][:2])))
    same_decisions = live["decisions"] == off["decisions"]
    diff_orders = [o for o in live["day_orders"] if o not in off["day_orders"]] + [
        o for o in off["day_orders"] if o not in live["day_orders"]]
    checks.append(("day strategies' decisions identical with overnight live and off", same_decisions,
                   f"{len(live['decisions'])} decisions live, {len(off['decisions'])} off"))
    checks.append(("day strategies' orders identical (apart from X6 closes)", not diff_orders or bool(live["x6"]),
                   f"differences {diff_orders[:6]}; X6 closes {live['x6']}"))
    bad = [c for c in live["compares"] + off["compares"] if not c["match"] or c["mismatch_flag"]]
    checks.append(("bot and fake Alpaca agree on positions at every compare", not bad,
                   f"{len(live['compares'])} compares live, {len(off['compares'])} off; first bad {bad[:1]}"))
    drift = max(abs(c["cash_drift"]) for c in live["compares"])
    edrift = max(abs(c["equity_drift_buy_marks"]) for c in live["compares"])
    checks.append(("cash drift under $5 at buy price marks", drift < 5 and edrift < 5,
                   f"max cash drift ${drift:.2f}, max equity drift at buy marks ${edrift:.2f}"))
    checks.append(("zero orders to any real network endpoint", BLOCKED == [] and live["hosts"] == ["paper-api.alpaca.markets"],
                   f"blocked attempts {len(BLOCKED)}, fake hosts {live['hosts']}"))
    checks.append(("no wash trade refusal", live["wash_refusals"] == [] and off["wash_refusals"] == [],
                   f"{live['wash_refusals'][:2]}"))
    ovn_trades = [t for t in live["trades"] if str(t["strategy_id"]).startswith("overnight_")]
    checks.append(("each sold night is one closed trade on its sale day",
                   len(ovn_trades) == 6 and all(t["session_date"] in (fri.isoformat(), mon.isoformat()) for t in ovn_trades),
                   f"{len(ovn_trades)} overnight trades"))
    ok = all(c[1] for c in checks)
    write_report(live, off, checks, ok)
    for name, passed, detail in checks:
        print(("PASS " if passed else "FAIL ") + name + " | " + detail)
    print("REPORT", REPORT)
    return 0 if ok else 1


def _count(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


def write_report(live: Dict[str, Any], off: Dict[str, Any], checks, ok: bool) -> None:
    lines = [
        "# Overnight holds dry run (T12)",
        "",
        f"Generated by `scripts/run_overnight_dry_run.py` on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        f"Result {'PASS' if ok else 'FAIL'}.",
        "",
        "Real one minute SIP bars from the edge hunt research data (read only) for Thu 2026-09-24, Fri 09-25 and "
        "Mon 09-28, replayed through the real main handlers with every other strategy on, against the fake Alpaca "
        "of the integration tests. The closing auction fills at the close of the 15:59 bar and the opening auction "
        "at the open of the 09:30 bar. Two nights, Thu to Fri and Fri to Mon over the weekend. The same replay "
        "ran with OVERNIGHT_MODE=off for the comparison. Account $49,700, sizes 20% of Alpaca equity per stock.",
        "",
        "What it is not. The fake fills auctions at bar prices, so it proves the robot's wiring, not Alpaca's "
        "auction fills (Q1, Q3 stay open). Symbols without research bars (TSLA, CDE, LRCX, KLAC and the sector ETFs "
        "other than XLF) got no bars, so the tri plan and parts of the Slow trades and Ride the Trend layers had "
        "nothing to trade on.",
        "",
        "## Checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for name, passed, detail in checks:
        lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} | {detail.replace('|', '/')[:300]} |")
    lines += ["", "## Nights (live run)", "", "| Night | State | Shares | Buy avg | Sale date | Result $ |", "|---|---|---|---|---|---|"]
    for key, n in sorted(live["nights"].items(), key=lambda kv: (kv[1]["buy_date"], kv[0])):
        lines.append(f"| {key} | {n['state']} {n['reason'] or ''} | {n['held_qty']} | "
                     f"{'' if n['buy_avg'] is None else round(n['buy_avg'], 4)} | {n['sale_date']} | {round(n['realized'] or 0.0, 2)} |")
    lines += ["", "## Closed trades (live run)", "", "| Trade | Day | Strategy | Qty | In | Out | Result $ |", "|---|---|---|---|---|---|---|"]
    for t in live["trades"]:
        lines.append(f"| {t['trade_id']} | {t['session_date']} | {t['strategy_id']} | {t['quantity']} | "
                     f"{t['avg_entry_price']} | {t['avg_exit_price']} | {t['realized_pnl']} |")
    lines += ["", "## Day strategies", "",
              f"Decisions, live {len(live['decisions'])} and off {len(off['decisions'])}, identical: "
              f"{live['decisions'] == off['decisions']}. Day orders live {len(live['day_orders'])}, off {len(off['day_orders'])}. "
              f"X6 early closes {live['x6'] or 'none'}. Orders the fake received, live {live['all_posts']}, off {off['all_posts']}.",
              "", "Day orders in the live run (symbol, side, qty, strategy, final state): "
              + "; ".join(f"{o[0]} {o[1]} {o[2]} {o[3]} {o[4]}" for o in live["day_orders"]) + ".",
              "", "Decision outcomes (live run): " + ", ".join(
                  f"{k} {v}" for k, v in sorted(_count(d[4] for d in live["decisions"]).items())) + ".",
              "", "## Compares", "",
              f"{len(live['compares'])} compares in the live run and {len(off['compares'])} in the off run. "
              f"End of run, bot {live['positions_end']} and fake {live['alpaca_end']}. Final equity "
              f"${live['final_equity']:,.2f}, bot cash ${live['final_cash']:,.2f}, fake cash ${live['fake_cash']:,.2f}.",
              ""]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
