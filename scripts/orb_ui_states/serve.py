#!/usr/bin/env python3
"""DEV ONLY: the real ADT backend + the exported dashboard, with ORB driven by fakes, for visual QA.

No broker keys, BROKER_MODE=local, no relay clients, a throwaway temp state store, and every real httpx request
is refused. ORB is the REAL OrbIntegration / controller / scheduler from backend.app.main, built on
the phase-2 test fakes (FakeAlpaca behind httpx.MockTransport, scripted FakeFacade, FakeClock on
2026-09-28). Nothing here is imported by production code.

    python3 scripts/orb_ui_states/serve.py --port 8765
    curl -X POST http://127.0.0.1:8765/__dev/orb_state/waiting
    open the page as http://adt.test:8765/ with adt.test mapped to 127.0.0.1 (the frontend sends a
    localhost page's websocket to port 8005, so a non-localhost host name keeps it same-origin)

States: waiting, shadow_pick, live_trade, sat_out, orphan_alert, orphan_closed_at_alpaca, off,
relay_error. Each POST rebuilds ORB, runs the fake session to the state's time and pushes a
websocket update (the page is never reloaded). Strategy cards are rendered at the fake time.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)                      # config reads ./.env; the repo root of a worktree has none
sys.path.insert(0, str(ROOT))
for k in list(os.environ):
    if k.startswith(("ALPACA_", "APCA_")) or k == "RELAY_TOKEN":
        del os.environ[k]
_TMP = tempfile.mkdtemp(prefix="orb_ui_states_")   # throwaway durable store (the trade list needs one)
os.environ.update(BROKER_MODE="local", ORB_MODE="off", START_RELAY_CLIENTS="false", ENV="development",
                  PERSISTENCE_ENABLED="true", PERSISTENCE_REQUIRED="false", RESEARCH_ENABLED="false",
                  STATE_DB_PATH=os.path.join(_TMP, "state.sqlite3"), ORB_STATE_DIR=os.path.join(_TMP, "orbs"))

import httpx  # noqa: E402


def _refuse(self, request):
    raise RuntimeError(f"dev fixture: real network request refused: {request.method} {request.url}")


httpx.HTTPTransport.handle_request = _refuse

import uvicorn  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend.app import main as r  # noqa: E402
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick  # noqa: E402

FAKE = {"h": None, "state": None}

# The fixture runs ORB's scheduler inline (on the calling thread), where the off-thread scheduler
# state writer + pre-order flush would wait on the scheduler itself and time out. Scheduler state is
# not needed across fixture states, so it is not written; controller state and trades still are.
r.orb._queue_scheduler_state = lambda: None
r.orb.flush_scheduler_state = lambda timeout=10.0: None

# Cards (all strategies) render at the fixture's fake time, not the host clock.
_orig_cards = r._strategy_cards


def _cards_at_fake_time(now=None):
    h = FAKE["h"]
    return _orig_cards(h.clock.now if (h is not None and now is None) else now)


r._strategy_cards = _cards_at_fake_time
_orig_step = r._runtime_clock_step


def board(*syms, board_id=None):
    return {"ok": True, "error": None, "coverage": 1.0,
            "cards": [{"symbol": s, "direction": "long"} for s in syms], "board_id": board_id or "-".join(syms)}


def decision(*picks, verdict="trade", reason=None):
    return {"verdict": verdict, "reason": reason, "picks": list(picks),
            "regime": {"action": "TRADE_NORMAL", "classification": "CALM_TREND", "short_frac": 0.4},
            "audit": [{"symbol": p["symbol"], "direction": p["direction"], "tier": p.get("tier"), "chosen": True}
                      for p in picks]}


def run(h, start, end, step=10.0):
    h.clock.set(start)
    while h.clock.now <= end:
        r.flattening_engine.clock.set_simulated_time(h.clock.now)
        asyncio.run(_orig_step(h.clock.now))
        h.clock.advance(step)


def attach(h):
    r.alpaca_broker, r.engine.broker = h.broker, h.broker
    r.broker_state["mismatch"] = False


def session(mode="live", broker=True):
    h = MainOrb(r, mode=mode, start=at(9, 10), broker=broker, freeze=False)
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    if broker:
        attach(h)
    return h


def mark(h, sym, price):
    h.alpaca.prices[sym] = price
    r.orb.note_price(sym, price, h.clock.now, "quote_mid")
    if sym in r.account.positions:
        r.account.update_market_price(sym, price)
    r.orb.mark_positions(h.clock.now, force_eval=True)


_REAL_FILTER = r.adaptation_engine.market_filter


class _DevFilter:
    """DEV ONLY stand-in for SPY/QQQ bars: says the market is rising and permits the trade."""
    def get_current_trend(self, asof=None):
        from backend.app.core.market_filter import MarketTrend
        return MarketTrend.BULLISH, "OK"

    def is_signal_permitted(self, **_kw):
        return True, "APPROVED: aligned with the market"


def inject_adaptive_holding(h, sym="NVDA", entry=184.0, raw_stop=179.4):
    """An adaptive (Big News) holding whose entry_context is built by the REAL main._build_entry_context
    while the engine is nervous. Position and bracket are put straight into the book (no order path)."""
    from types import SimpleNamespace
    from backend.app.core.account import Position, PositionSide
    from backend.app.models.events import OrderSide, OrderType
    from backend.app.strategies.base import SignalEvent
    eng = r.adaptation_engine
    eng.market_filter = _DevFilter()
    eng.on_vix_print(SimpleNamespace(value=27.0, received_at=h.clock.now))
    sig = SignalEvent(symbol=sym, side=OrderSide.BUY, order_type=OrderType.MARKET, entry_price=entry, stop_loss=raw_stop,
                      take_profit_1=entry + 2, take_profit_2=entry + 4, strategy_id="news_momentum", confidence=0.9,
                      reason="DEV", timestamp=h.clock.now)
    sig.features = {}
    stop = eng.calculate_adapted_stop(sig)
    qty = eng.calculate_adapted_size(r.account.equity, entry, stop)
    ctx = r._build_entry_context(sig, {"admission_qty": qty}, qty, stop)
    r.account.positions[sym] = Position(symbol=sym, side=PositionSide.LONG, shares=qty, avg_entry_price=entry,
                                        market_price=entry + 1.1, strategy_id="news_momentum")
    b = r.bracket_manager.create_bracket(f"brk_dev_{sym}", sym, "LONG", qty, entry, stop, strategy_id="news_momentum",
                                         timestamp=h.clock.now)
    b.entry_context = ctx


def build_state(name: str):
    r.relay_statuses.clear()
    r.alpaca_broker, r.engine.broker = None, None
    r.adaptation_engine.market_filter = _REAL_FILTER      # a state never inherits the previous state's injections
    from types import SimpleNamespace as _NS
    r.adaptation_engine.on_vix_print(_NS(value=20.0, received_at=__import__('datetime').datetime.now()))   # back to NORMAL
    if name == "waiting":
        h = session()
        run(h, at(9, 10), at(9, 20))
    elif name == "shadow_pick":
        h = session(mode="shadow", broker=False)
        h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
        h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
        run(h, at(9, 10), at(9, 44))
    elif name == "live_trade":
        h = session()
        h.facade.scan_results = [board("APP", "PLTR"), board("APP", "PLTR", board_id="final")]
        h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
        run(h, at(9, 10), at(9, 38, 40), step=5.0)
        mark(h, "APP", 101.1)
        run(h, at(9, 38, 45), at(9, 52), step=5.0)
        mark(h, "APP", 101.1)
    elif name == "live_trade_adaptive":
        # the ORB live trade plus an adaptive holding: both must reach the websocket with their context
        h = session()
        h.facade.scan_results = [board("APP", "PLTR"), board("APP", "PLTR", board_id="final")]
        h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
        run(h, at(9, 10), at(9, 38, 40), step=5.0)
        mark(h, "APP", 101.1)
        run(h, at(9, 38, 45), at(9, 52), step=5.0)
        mark(h, "APP", 101.1)
        inject_adaptive_holding(h)
    elif name == "sat_out":
        h = session()
        h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
        h.facade.decide_results = [decision(verdict="sit_out", reason="board is one-sided: 84% shorts")]
        run(h, at(9, 10), at(9, 50))
    elif name in ("orphan_alert", "orphan_closed_at_alpaca"):
        h = MainOrb(r)
        h.open_bracket()
        # a new process whose ORB state could not be restored: the controller knows nothing
        from backend.app.core.orb_execution import RequestBudget
        r.orb.build(h.broker, h.facade, "off", clock=h.clock, inline=True, monotonic=lambda: h.clock.now.timestamp(),
                    budget=RequestBudget(10 ** 6, 10 ** 6), sleep=h.clock.sleep, is_session=lambda d: True,
                    restore=False)
        h.ctl, h.sched = r.orb.controller, r.orb.scheduler
        h.ctl.reconcile_on_startup()
        for _ in range(2):
            r.orb.tick(h.clock.now)
            h.clock.advance(6)
        h.clock.set(at(9, 50))
        attach(h)
        if name == "orphan_closed_at_alpaca":      # the human cancels the legs and sells at Alpaca
            for o in h.alpaca.orders.values():
                if o["status"] in ("new", "held", "accepted"):
                    o["status"] = "canceled"
            h.alpaca._apply_position("APP", "sell", 454, 100.5)
    elif name == "off":
        h = session(mode="off")
        run(h, at(9, 10), at(9, 50))
    elif name == "relay_error":
        h = session()
        def relay_down():
            raise ConnectionError("relay unreachable (HTTP 502 from the relay's /bars endpoint)")
        h.facade.scan_results = [board("APP"), relay_down, relay_down]     # the 9:38 scan and its retry
        run(h, at(9, 10), at(9, 45))
        r.relay_statuses.update({"stock": "disconnected", "news": "disconnected", "vix": "disconnected"})
    else:
        raise HTTPException(404, f"unknown state {name}")
    r.adaptation_engine.update_clock(h.clock.now)
    FAKE.update(h=h, state=name)
    return {"state": name, "fake_now": h.clock.now.isoformat(), "orb": r.orb.status()}


@r.app.post("/__dev/orb_state/{name}")
async def dev_orb_state(name: str):
    out = await asyncio.to_thread(build_state, name)
    await r.broadcast_ui_state(force=True)
    return r._sanitize_for_json(out)


# Move the dev route ahead of the static-UI mount ("/" catches everything registered after it).
_routes = r.app.router.routes
_dev = [x for x in _routes if getattr(x, "path", "") == "/__dev/orb_state/{name}"]
for x in _dev:
    _routes.remove(x)
    _routes.insert(0, x)

_orig_lifespan = r.app.router.lifespan_context


@asynccontextmanager
async def _dev_lifespan(app):
    async with _orig_lifespan(app) as st:
        # the real runtime clock would drive ORB and the EOD engine on the host time: stop it and
        # push a state update every 5 s instead (the dashboard must still update live)
        for t in list(r.runtime_tasks):
            if t.get_name() == "TradingRuntimeClock":
                t.cancel()

        async def push():
            while True:
                await asyncio.sleep(5.0)
                try:
                    await r.broadcast_ui_state(force=True)
                except Exception:
                    pass

        task = asyncio.create_task(push(), name="DevPush")
        await asyncio.to_thread(build_state, "waiting")
        try:
            yield st
        finally:
            task.cancel()


r.app.router.lifespan_context = _dev_lifespan

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    try:
        uvicorn.run(r.app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
