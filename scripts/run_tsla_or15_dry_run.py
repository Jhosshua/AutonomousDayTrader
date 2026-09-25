#!/usr/bin/env python3
"""Deterministic full bar-handler -> fixed execution -> ledger -> UI replay.

No credentials, network calls or broker orders. Broker HTTP and SQLite crash
coverage lives in test_or15_broker_lifecycle.py and is reported separately.
Optional --serve exposes only captured read-only QA snapshots and the UI export.
"""
import argparse
import asyncio
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import main as r
from backend.app.core.trading_windows import ET
from backend.app.strategies.tsla_or15_retest import implementation_sha256, SOURCE_SHA256
from backend.tests.unit.test_tsla_or15 import START, bars, runtime_pair

logging.disable(logging.CRITICAL)
OUT = ROOT / "docs/tsla_or15"


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
    sink.payload["strategies"] = r._strategy_cards(now)
    sink.payload["timestamp"] = now.isoformat()
    return sink.payload


async def replay(case):
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    for strategy in r.strategies:
        if strategy is not r.tsla_or15_strategy:
            strategy.pause()
    r.relay_statuses["stock"] = "connected"
    start = datetime(2026, 11, 27, 9, 30, tzinfo=ET) if case == "early_close" else START
    signal_minute = 120 if case == "early_close" else 16
    for i in range(signal_minute - 1):
        await runtime_pair(r, bars(i, start=start))
    await runtime_pair(r, bars(signal_minute-1, start=start, o=100,h=102.5,l=100,c=102))
    await runtime_pair(r, bars(signal_minute, start=start, o=101.1,h=102,l=101,c=101.5))
    assert not r.account.positions and r.tsla_or15_strategy.signal_consumed
    await runtime_pair(r, bars(signal_minute+1, start=start, o=102,h=103,l=101,c=102))
    assert not r.account.positions, "no fill before T+2"
    await runtime_pair(r, bars(signal_minute+2, start=start, o=102,h=103,l=101,c=102))
    s = r.tsla_or15_strategy
    assert s.entry_price == 102 and s.target_price == 108
    b = r.or15_controller._bracket()
    assert b.current_stop_price == 99 and not b.use_trailing_target_2 and b.target_2_qty == 0
    active = await snapshot(start + timedelta(minutes=signal_minute+3))
    end = {"target": (dict(o=103,h=108,l=102,c=107), 108, "TARGET"),
           "stop": (dict(o=102,h=103,l=99,c=100), 99, "STOP"),
           "both": (dict(o=102,h=109,l=98,c=104), 99, "STOP"),
           "gap": (dict(o=97,h=101,l=96,c=100), 97, "STOP")}
    if case in end:
        params, expected, reason = end[case]
        await runtime_pair(r, bars(signal_minute+3, start=start, **params))
    else:
        terminal = int((s.exit_due - start).total_seconds() // 60)
        for i in range(signal_minute+3, terminal):
            await runtime_pair(r, bars(i,start=start,o=102,h=103,l=101,c=102))
        await runtime_pair(r, bars(terminal,start=start,o=104,h=110,l=90,c=102))
        expected, reason = 104, "FORCED_FLAT" if case == "early_close" else "TIME_LIMIT"
    assert not r.account.positions and not r.engine.working_orders
    trade = next(iter(r.pending_trade_records.values()))
    assert trade["avg_exit_price"] == expected and trade["exit_reason"] == reason
    assert s.phase == "CLOSED" and trade["execution_mode"] == "offline_raw_open"
    return {"case":case,"passed":True,"trade":trade,"session":s.session_record(),
            "open_positions":0,"working_orders":0}, active


async def run():
    rows, states = [], {}
    try:
        for case in ("target", "stop", "both", "gap", "time", "early_close"):
            row, active = await replay(case)
            rows.append(row)
            if case == "target":
                states["holding"] = active
                states["closed"] = await snapshot(START + timedelta(minutes=20))
        r.reset_runtime_state()
        states["idle"] = await snapshot(START - timedelta(minutes=5))
        result = {"status":"PASS", "execution_mode":"offline_raw_open",
                  "source_sha256":SOURCE_SHA256, "implementation_sha256":implementation_sha256(),
                  "cases":rows, "scope":"Real main.handle_bar_event, strategy, risk, order/bracket engine, ledger, WebSocket serializer. Synthetic market bars. No real session/fills; persistence tested separately via real SQLite and broker HTTP adapter."}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "DRY_RUN_EVIDENCE.json").write_text(json.dumps(result, indent=2, default=str)+"\n")
        print(json.dumps({"status":"PASS","cases":len(rows),"all_flat":True}))
        return states
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)


def serve(states, port):
    from fastapi import FastAPI, WebSocket
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    app = FastAPI()
    selected = {"state":"holding"}
    @app.get("/qa/{state}")
    def select(state: str):
        assert state in states
        selected["state"] = state
        return {"state":state}
    @app.get("/health")
    def health():
        return {"status":"healthy", "risk_limits":{}, "broker":{"mode":"simulated"}}
    @app.get("/api/trades")
    def trades():
        return {"items":[],"next_cursor":None,"recovered_sessions":[],"summary":{"trades":0,"realized_pnl":0,"fees":0,"wins":0,"losses":0}}
    @app.get("/api/account")
    def account(): return states[selected["state"]]["account"]
    @app.get("/api/positions")
    def positions(): return {"positions":states[selected["state"]]["all_positions"]}
    @app.get("/api/strategies")
    def strategies(): return states[selected["state"]]["strategies"]
    @app.websocket("/ws/ui")
    async def websocket(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_json(states[selected["state"]])
                await asyncio.sleep(1)
        except Exception:
            pass
    app.mount("/", StaticFiles(directory=ROOT / "frontend/out", html=True))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8005)
    args = parser.parse_args()
    states = asyncio.run(run())
    if args.serve:
        serve(states, args.port)
