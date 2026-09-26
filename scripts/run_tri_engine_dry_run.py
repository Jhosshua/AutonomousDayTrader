#!/usr/bin/env python3
"""Replay synthetic bars through the production handlers and capture UI evidence.

The runtime broker is detached before every case. During replay, socket connects
are prohibited. Optional --serve exposes captured read-only snapshots; it never
starts the trading application's lifespan, feeds, or broker account connection.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
from unittest.mock import patch

# Module level: with postponed annotations FastAPI must resolve `WebSocket` from globals.
from fastapi import FastAPI, WebSocket

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import main as r
from backend.app.core.trading_windows import ET
from backend.app.models.events import BarEvent
from backend.app.strategies.tri_engine import TRI_IDS, SOURCE_SHA256

OUT = ROOT / "docs/tri_engine"
START = datetime(2026, 9, 28, 9, 30, tzinfo=ET)
EARLY = datetime(2026, 11, 27, 9, 30, tzinfo=ET)
MINUTE = timedelta(minutes=1)
CASES = [(symbol, side, kind) for symbol in ("TSLA", "CDE")
         for side in ("LONG", "SHORT")
         for kind in ("target", "stop", "collision", "gap", "time", "early_close")]


def scale(symbol):
    return .06 if symbol == "CDE" else 1.


def stock_bar(symbol, minute, *, start=START, o=100, h=101, l=99, c=100):
    k = scale(symbol)
    return BarEvent(symbol, o*k, h*k, l*k, c*k, 50000, start + minute*MINUTE)


def qqq_bar(minute, *, start=START, side="LONG"):
    # Close-weighted VWAP: short days need QQQ to fall below its opening level.
    c = 200.5 if side == "LONG" or minute < 15 else 199.5
    return BarEvent("QQQ", 200, 201, 199, c, 50000, start + minute*MINUTE)


async def feed(minute, symbol="TSLA", *, start=START, side="LONG", reverse=False, **params):
    events = (stock_bar(symbol, minute, start=start, **params), qqq_bar(minute, start=start, side=side))
    for event in events[::-1] if reverse else events:
        await r.handle_bar_event(event)


def configure(symbols=("TSLA",), *, all_arms=False):
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    assert r.engine.broker is None, "Offline replay must detach the broker"
    assert r.state_store is None, "Dry runs require an isolated in-memory runtime"
    r.relay_statuses["stock"] = "connected"
    for s in r.strategies:
        if all_arms or (s.strategy_id in TRI_IDS and s.symbol in symbols):
            s.resume()
        else:
            s.pause()


@contextmanager
def no_network():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("A deterministic replay attempted network access")
    with patch("socket.socket.connect", forbidden), patch("socket.create_connection", forbidden):
        yield


async def snapshot(now):
    class Sink:
        async def send_text(self, text):
            self.payload = json.loads(text)
    sink = Sink()
    r.ui_clients.add(sink)
    try:
        await r.broadcast_ui_state(force=True)
    finally:
        r.ui_clients.discard(sink)
    sink.payload["strategies"] = r._sanitize_for_json(r._strategy_cards(now))
    sink.payload["timestamp"] = now.isoformat()
    return sink.payload


def neutral(side):
    return dict(o=102, h=103, l=101, c=102) if side == "LONG" else dict(o=98, h=99, l=97, c=98)


async def enter(symbol, side, *, start=START, signal_minute=16, reverse=False):
    s = r.tri_controller.by_symbol[symbol]
    for minute in range(signal_minute - 1):
        await feed(minute, symbol, start=start, side=side, reverse=reverse)
    if side == "LONG":
        await feed(signal_minute-1, symbol, start=start, side=side, reverse=reverse,
                   o=100, h=102.5, l=100, c=102)
        await feed(signal_minute, symbol, start=start, side=side, reverse=reverse,
                   o=101.1, h=102, l=101, c=101.5)
    else:
        await feed(signal_minute-1, symbol, start=start, side=side, reverse=reverse)
        await feed(signal_minute, symbol, start=start, side=side, reverse=reverse,
                   o=99.5, h=100, l=98, c=98.5)
    assert s.signal_consumed, (symbol, side, s.phase, s.reason)
    assert symbol not in r.account.positions, "An entry filled on its signal candle"
    await feed(signal_minute+1, symbol, start=start, side=side, reverse=reverse, **neutral(side))
    assert symbol not in r.account.positions, "An entry filled before T+2"
    await feed(signal_minute+2, symbol, start=start, side=side, reverse=reverse, **neutral(side))
    assert s.phase == "HOLDING", (symbol, side, s.phase, s.reason)
    pos = r.account.positions[symbol]
    assert pos.side.value == side and pos.strategy_id == s.strategy_id
    assert pos.avg_entry_price == (102 if side == "LONG" else 98)*scale(symbol)
    assert s.entry_due == start + (signal_minute+2)*MINUTE
    assert len(s.tranches) == (2 if symbol == "TSLA" else 1)
    assert sum(t["qty"] for t in s.tranches) == pos.shares
    if symbol == "TSLA":
        assert s.tranches[0]["qty"] == s.tranches[1]["qty"]
    assert s.risk_reserved <= r.account.daily_starting_equity*.0075 + 1e-7
    assert r.tri_controller.open_risk() <= r.account.daily_starting_equity*.015 + 1e-7
    assert symbol not in r.bracket_manager.symbol_to_bracket, "A generic bracket owns a fixed tranche"
    for t in s.tranches:
        direction = 1 if side == "LONG" else -1
        assert t["target"] == t["entry_price"]+direction*t["target_r"]*t["risk_per_share"]
        assert t["stop"] == (99 if side == "LONG" else 100)*scale(symbol)
    return s


def close_evidence(s, name, expected_prices, expected_reasons, *, expected_other_symbols=()):
    assert s.phase == "CLOSED", (name, s.phase, s.reason)
    assert s.symbol not in r.account.positions
    assert not [o for o in r.engine.working_orders.values() if o.symbol == s.symbol]
    assert set(r.account.positions) == set(expected_other_symbols)
    for t, price, reason in zip(s.tranches, expected_prices, expected_reasons):
        assert t["closed_qty"] == t["qty"]
        assert abs(t["exit_notional"]/t["qty"]-price) < 1e-8, (name, t)
        assert t["exit_reason"] == reason, (name, t)
    trades = [t for t in r.pending_trade_records.values() if t["strategy_id"] == s.strategy_id]
    assert len(trades) == 1, (name, "aggregate ledger duplicate/missing", trades)
    trade = trades[0]
    assert trade["execution_mode"] == "offline_raw_open" and not trade["incomplete"]
    assert trade["quantity"] == s.quantity and len(trade["tranches"]) == len(s.tranches)
    assert trade["quantity"] == sum(f["qty"] for f in trade["fill_legs"] if f["order_id"] == s.entry_order_id)
    assert trade["quantity"] == sum(f["qty"] for f in trade["fill_legs"] if f["order_id"] != s.entry_order_id)
    expected_pnl = sum((price-t["entry_price"])*(1 if s.side == "LONG" else -1)*t["qty"]
                       for t, price in zip(s.tranches, expected_prices))
    assert abs(trade["realized_pnl"]-round(expected_pnl, 2)) < .011
    assert s.trades_count == 1 and s.risk_reserved == 0
    return {"case": name, "passed": True, "trade": trade,
            "session": s.session_record(), "tri_positions_remaining": 0,
            "working_orders_remaining": len(r.engine.working_orders)}


async def replay_case(symbol, side, kind, *, capture=True, reverse=False):
    configure((symbol,))
    start = EARLY if kind == "early_close" else START
    # Shorts use the final eligible minute so early-close coverage does not
    # accidentally violate their stricter morning cutoff.
    signal_minute = ((89 if symbol == "TSLA" else 119) if side == "SHORT" else (120 if symbol == "TSLA" else 147)) if kind == "early_close" else 16
    s = await enter(symbol, side, start=start, signal_minute=signal_minute, reverse=reverse)
    states = {"holding": await snapshot(start+(signal_minute+3)*MINUTE)} if capture else {}
    next_minute = signal_minute+3
    expected_prices, expected_reasons = [], []
    k = scale(symbol)
    if kind == "target":
        if symbol == "TSLA":
            params = dict(o=103, h=106.5, l=102, c=106) if side == "LONG" else dict(o=97, h=99, l=95, c=95.5)
            await feed(next_minute, symbol, start=start, side=side, reverse=reverse, **params)
            assert s.tranches[0]["closed_qty"] == s.tranches[0]["qty"]
            assert s.tranches[1]["closed_qty"] == 0
            assert r.account.positions[symbol].shares == s.tranches[1]["qty"]
            assert s.tranches[1]["stop"] == (99 if side == "LONG" else 100)*k, "Stop was ratcheted after T1"
            assert not r.pending_trade_records, "Aggregate trade was recorded before T2 finished"
            if capture:
                states["partial"] = await snapshot(start+(next_minute+1)*MINUTE)
            next_minute += 1
        params = dict(o=107, h=109, l=102, c=108) if side == "LONG" else dict(o=95, h=99, l=93, c=94)
        await feed(next_minute, symbol, start=start, side=side, reverse=reverse, **params)
        expected_prices = [t["target"] for t in s.tranches]
        expected_reasons = ["TARGET"]*len(s.tranches)
    elif kind in ("stop", "collision", "gap"):
        long_cases = {"stop": dict(o=102, h=103, l=99, c=100),
                      "collision": dict(o=102, h=110, l=98, c=104),
                      "gap": dict(o=97, h=101, l=96, c=100)}
        short_cases = {"stop": dict(o=98, h=100, l=97, c=99),
                       "collision": dict(o=98, h=101, l=93, c=98),
                       "gap": dict(o=103, h=104, l=99, c=100)}
        await feed(next_minute, symbol, start=start, side=side, reverse=reverse,
                   **(long_cases if side == "LONG" else short_cases)[kind])
        price = (97 if side == "LONG" else 103)*k if kind == "gap" else (99 if side == "LONG" else 100)*k
        expected_prices = [price]*len(s.tranches)
        expected_reasons = ["STOP"]*len(s.tranches)
    else:
        due_minutes = sorted({int((t["exit_due"]-start).total_seconds()/60) for t in s.tranches})
        for due in due_minutes:
            while next_minute < due:
                await feed(next_minute, symbol, start=start, side=side, reverse=reverse, **neutral(side))
                next_minute += 1
            last = due == due_minutes[-1]
            # For the final deadline, both stop and target also get touched.
            # The scheduled terminal open must win over those later extremes.
            params = (dict(o=104, h=110 if last else 105, l=90 if last else 101, c=104)
                      if side == "LONG" else dict(o=97, h=105 if last else 99, l=90 if last else 96, c=97))
            await feed(due, symbol, start=start, side=side, reverse=reverse, **params)
            next_minute = due+1
            if not last:
                assert r.account.positions[symbol].shares == s.tranches[-1]["qty"]
                assert s.tranches[-1]["closed_qty"] == 0
                assert s.tranches[-1]["stop"] == (99 if side == "LONG" else 100)*k
                if capture:
                    states["partial"] = await snapshot(start+(due+1)*MINUTE)
        expected_prices = [(104 if side == "LONG" else 97)*k]*len(s.tranches)
        expected_reasons = ["FORCED_FLAT" if kind == "early_close" else "TIME_LIMIT"]*len(s.tranches)
        expected_holds = [180, 240] if symbol == "TSLA" else [180]
        for t, hold in zip(s.tranches, expected_holds):
            assert t["exit_due"] == min(t["entry_at"]+hold*MINUTE,
                start.replace(hour=12 if kind == "early_close" else 15, minute=55))
    row = close_evidence(s, f"{symbol}_{side}_{kind}", expected_prices, expected_reasons)
    if capture:
        states["closed"] = await snapshot(start+(next_minute+1)*MINUTE)
    return row, states


async def replay_coexistence(*, reverse=False):
    configure(("TSLA", "CDE"), all_arms=True)
    for minute in range(19):
        if minute == 1:
            # Represents a reconciled unrelated existing arm position. Both new
            # entries must account for its $50 structural stop exposure.
            r.account.apply_fill("dry-existing-orb", "AAPL", "BUY", 5, 100, 0,
                                 START+MINUTE, strategy_id="orb", stop_loss_price=90)
        params = ({15: dict(o=100, h=102.5, l=100, c=102),
                   16: dict(o=101.1, h=102, l=101, c=101.5),
                   17: neutral("LONG"), 18: neutral("LONG")}).get(minute, {})
        events = [stock_bar("TSLA", minute, **params), stock_bar("CDE", minute, **params), qqq_bar(minute)]
        for event in events[::-1] if reverse else events:
            await r.handle_bar_event(event)
    assert {s.symbol for s in r.tri_strategies if s.phase == "HOLDING"} == {"TSLA", "CDE"}, [s.session_record() for s in r.tri_strategies]
    assert r.account.positions["AAPL"].strategy_id == "orb" and r.account.positions["AAPL"].shares == 5
    assert r.tri_controller.open_risk() <= r.account.daily_starting_equity*.015+1e-7
    states = {"holding_both": await snapshot(START+19*MINUTE)}
    first, second = ("CDE", "TSLA") if reverse else ("TSLA", "CDE")
    # All engine arms remain enabled while independent instruments settle.
    await r.handle_bar_event(stock_bar(first, 19, o=107, h=110, l=102, c=108))
    await r.handle_bar_event(stock_bar(second, 19, o=107, h=110, l=102, c=108))
    await r.handle_bar_event(qqq_bar(19))
    rows = []
    for s in r.tri_strategies:
        rows.append(close_evidence(s, f"coexist_{s.symbol}_{'reverse' if reverse else 'forward'}",
                    [t["target"] for t in s.tranches], ["TARGET"]*len(s.tranches), expected_other_symbols=("AAPL",)))
    assert r.tsla_or15_strategy.trades_count == 0, "Retired OR15 took a new entry"
    return {"case": f"coexistence_{'reverse' if reverse else 'forward'}", "passed": True,
            "all_arms_enabled": True, "existing_generic_position_preserved": True,
            "combined_stop_risk_cap_enforced": True, "trades": rows}, states


async def replay_no_signal(symbol):
    configure((symbol,))
    last = 120 if symbol == "TSLA" else 147
    for minute in range(last+2):
        await feed(minute, symbol)
    s = r.tri_controller.by_symbol[symbol]
    assert s.phase == "NO_SIGNAL" and not s.signal_consumed
    assert not r.account.positions and not r.engine.working_orders and not r.pending_trade_records
    return {"case": f"{symbol}_no_signal", "passed": True, "phase": s.phase}


async def replay_noon_freeze():
    configure(("CDE",))
    # 11:58 signal would schedule an entry precisely at noon. It is excluded.
    for minute in range(147):
        await feed(minute, "CDE")
    await feed(147, "CDE", o=100, h=102.5, l=100, c=102)
    await feed(148, "CDE", o=101.1, h=102, l=101, c=101.5)
    for minute in (149, 150):
        await feed(minute, "CDE", **neutral("LONG"))
    s = r.tri_controller.by_symbol["CDE"]
    assert not s.signal_consumed and not r.account.positions and not r.engine.orders
    return {"case": "CDE_noon_freeze", "passed": True, "phase": s.phase}


def implementation_hash():
    names = ["backend/app/main.py", "backend/app/core/tri_execution.py",
             "backend/app/strategies/tri_engine.py", "backend/app/core/engine.py",
             "backend/app/core/risk.py", "backend/app/core/broker.py"]
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}


async def run():
    rows, states = [], {}
    original_statuses = {s.strategy_id: s.status for s in r.strategies}
    original_relays = dict(r.relay_statuses)
    try:
        with no_network():
            for symbol, side, kind in CASES:
                name = f"{symbol}_{side}_{kind}"
                try:
                    row, captures = await replay_case(symbol, side, kind)
                    rows.append(row)
                    if (symbol, side, kind) == ("TSLA", "LONG", "target"):
                        states.update(captures)
                    if (symbol, side, kind) == ("CDE", "SHORT", "target"):
                        states["short"] = captures["holding"]
                except Exception as exc:
                    rows.append({"case": name, "passed": False, "failure": f"{type(exc).__name__}: {exc}"})
            for reverse in (False, True):
                try:
                    row, captures = await replay_coexistence(reverse=reverse)
                    rows.append(row)
                    states.update(captures)
                except Exception as exc:
                    rows.append({"case": f"coexistence_{reverse}", "passed": False,
                                 "failure": f"{type(exc).__name__}: {exc}"})
            for symbol in ("TSLA", "CDE"):
                try:
                    rows.append(await replay_no_signal(symbol))
                except Exception as exc:
                    rows.append({"case": f"{symbol}_no_signal", "passed": False,
                                 "failure": f"{type(exc).__name__}: {exc}"})
            try:
                rows.append(await replay_noon_freeze())
            except Exception as exc:
                rows.append({"case": "CDE_noon_freeze", "passed": False,
                             "failure": f"{type(exc).__name__}: {exc}"})
            configure(("TSLA", "CDE"))
            states["idle"] = await snapshot(START-timedelta(minutes=5))
            for s in r.tri_strategies:
                s.start_session(START.date())
                s.skip("MISSING_REQUIRED_BAR", START+20*MINUTE)
            states["blocked"] = await snapshot(START+20*MINUTE)
        passed = all(row["passed"] for row in rows)
        result = {"status": "PASS" if passed else "FAIL", "created_at": datetime.now(timezone.utc).isoformat(),
                  "execution_mode": "offline_raw_open", "source_sha256": SOURCE_SHA256,
                  "implementation_sha256": implementation_hash(), "cases": rows,
                  "scope": "Actual main.handle_bar_event, signal states, account risk, order/tranche engine, aggregate/tranche ledger and WebSocket serializer. Synthetic OHLC bars only; broker detached and socket connections prohibited. Existing account position in coexistence is seeded as a reconciled generic position. Broker HTTP, durable restart and actual market-session fills require separate evidence.",
                  "ui_states": sorted(states), "network_attempts_allowed": False}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT/"DRY_RUN_EVIDENCE.json").write_text(json.dumps(result, indent=2, default=str)+"\n")
        (OUT/"UI_SNAPSHOTS.json").write_text(json.dumps(states, indent=2, default=str)+"\n")
        print(json.dumps({"status": result["status"], "cases": len(rows),
                          "passed": sum(row["passed"] for row in rows),
                          "failures": [{"case": row["case"], "failure": row["failure"]} for row in rows if not row["passed"]],
                          "ui_states": sorted(states)}))
        return states, passed
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)
        r.relay_statuses.update(original_relays)
        for s in r.strategies:
            s.status = original_statuses[s.strategy_id]


def serve(states, port):
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    app = FastAPI()
    selected = {"state": "holding_both" if "holding_both" in states else "idle"}

    @app.get("/qa/{state}")
    def select(state: str):
        assert state in states
        selected["state"] = state
        return {"state": state}

    @app.get("/health")
    def health():
        return {"status": "healthy", "risk_limits": {}, "broker": {"mode": "offline_raw_open"}}

    @app.get("/api/trades")
    def trades():
        return {"items": [], "next_cursor": None, "recovered_sessions": [],
                "summary": {"trades": 0, "realized_pnl": 0, "fees": 0, "wins": 0, "losses": 0}}

    @app.get("/api/account")
    def account():
        return states[selected["state"]]["account"]

    @app.get("/api/positions")
    def positions():
        return {"positions": states[selected["state"]]["all_positions"]}

    @app.get("/api/strategies")
    def strategies():
        return states[selected["state"]]["strategies"]

    @app.websocket("/ws/ui")
    async def websocket(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_json(states[selected["state"]])
                await asyncio.sleep(1)
        except Exception:
            pass

    app.mount("/", StaticFiles(directory=ROOT/"frontend/out", html=True))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8005)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    captured, passed = asyncio.run(run())
    if args.serve:
        serve(captured, args.port)
    elif not passed:
        raise SystemExit(1)
