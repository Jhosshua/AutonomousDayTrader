"""ORB wired into the REAL backend.app.main, with the phase-2 fake Alpaca and a scripted facade.

Everything goes through main's OrbIntegration (hooks, reservation lock, ledger booking, exit
dispatch). The scheduler runs inline on the fake clock so a test is deterministic."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx

from backend.app.core.broker import AlpacaBroker
from backend.app.core.orb_execution import RequestBudget
from backend.tests.unit.orb_execution.fakes import DAY, ET, FakeAlpaca, FakeClock, FakeFacade, at, pick  # noqa: F401

SESSION = DAY.date().isoformat()


class MainOrb:
    def __init__(self, r, mode: str = "live", start: Optional[datetime] = None, equity: float = 50000.0,
                 broker: bool = True, inline: bool = True, freeze: bool = True):
        self.r = r
        self.clock = FakeClock(start or at(9, 20))
        self.alpaca = FakeAlpaca(self.clock, equity)
        self.broker = AlpacaBroker("test-key", "test-secret",
                                   transport=httpx.MockTransport(self.alpaca.handler), poll_interval_sec=0.0)
        self.broker._sleep = lambda s: None
        self.facade = FakeFacade(self.clock, self.alpaca)
        r.reset_runtime_state(equity)
        r.set_simulation_mode(False)
        r.orb.build(self.broker if broker else None, self.facade, mode, clock=self.clock, inline=inline,
                    monotonic=lambda: self.clock.now.timestamp(), budget=RequestBudget(10 ** 6, 10 ** 6),
                    sleep=self.clock.sleep, is_session=lambda d: True, restore=False)
        self.ctl, self.sched = r.orb.controller, r.orb.scheduler
        if freeze:
            ok, detail = self.ctl.freeze_session(self.clock.now)
            assert ok or not broker, detail
            assert self.ctl.reconcile_on_startup()["ok"]

    # ------------------------------------------------------------------ helpers
    def tick(self, seconds: float = 0.0):
        if seconds:
            self.clock.advance(seconds)
        self.r.orb.tick(self.clock.now)

    def clock_step(self, now: Optional[datetime] = None):
        """One pass of main's runtime clock (EOD phases, fixed plans, ORB) at the fake time."""
        now = now or self.clock.now
        self.clock.set(now)
        self.r.flattening_engine.clock.set_simulated_time(now)
        asyncio.run(self.r._runtime_clock_step(now))

    def open_bracket(self, sym: str = "APP", price: float = 100.2, entry: float = 100.0, stop: float = 98.0,
                     direction: str = "long"):
        self.clock.set(at(9, 39))
        self.alpaca.prices[sym] = price
        out = self.ctl.execute([pick(sym, direction, entry, stop)], self.clock.now)
        assert out["ok"], out
        self.r.orb.sync(self.clock.now)
        return out

    def run_supervisor(self, passes: int = 3, step: float = 6.0):
        for _ in range(passes):
            self.tick(step)

    def pos(self, sym: str = "APP"):
        return next(p for p in self.ctl.state["positions"].values() if p["symbol"] == sym)

    def parent(self, sym: str = "APP"):
        return self.alpaca.by_coid(self.pos(sym)["coid"])

    def alpaca_positions(self):
        return {s: int(p["qty"]) for s, p in self.alpaca.positions.items()}

    def writes_after(self, n: int):
        return [(m, p, b) for m, p, b, _q in self.alpaca.requests[n:] if m in ("POST", "PATCH", "DELETE")]

    def assert_orb_closed_through_its_controller(self, start: int, sym: str = "APP", qty: int = 454,
                                                  side: str = "sell"):
        """Legs/parent cancelled before ONE market close of exactly ORB's qty; never DELETE /positions;
        Alpaca never refused a sell for 'insufficient qty'; ADT's book is flat again and matches Alpaca."""
        w = self.writes_after(start)
        posts = [i for i, (m, p, b) in enumerate(w) if m == "POST"]
        deletes = [i for i, (m, p, b) in enumerate(w) if m == "DELETE"]
        assert len(posts) == 1, w
        body = w[posts[0]][2]
        assert body["type"] == "market" and body["side"] == side and int(body["qty"]) == qty, body
        assert body["client_order_id"].startswith(f"adt-orb-X-{sym}-{SESSION}-"), body
        assert deletes and max(deletes) < posts[0], w
        assert not any(p.startswith("/v2/positions") for m, p, b in w)
        assert self.alpaca.refused_403 == []
        assert sym not in self.alpaca.positions
        assert sym not in self.r.account.positions
        assert self.r._local_signed_positions(self.r.account) == self.alpaca_positions()
        # ADT itself never sent a generic liquidation for ORB's symbol
        assert not [o for o in self.r.engine.orders.values()
                    if o.symbol == sym and o.strategy_id not in ("orb",)], \
            [(o.strategy_id, o.status.value) for o in self.r.engine.orders.values() if o.symbol == sym]
