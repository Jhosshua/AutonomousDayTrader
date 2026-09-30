# @steered SNARE-2 2026-09-30
"""A fake Alpaca paper account for the overnight holds wired into the REAL backend.app.main.

No network: the real AlpacaBroker talks to it through httpx.MockTransport, and every arm of the
robot (day strategies through the engine, the X6 close, the overnight controller) uses the same
fake. On top of the ORB fake (orders, positions, 40310000 one sell per position) it implements:

- tif cls: accepted until 15:50 ET, then refused; rests until close_auction() fills it.
- tif opg: refused 09:28 to 19:00 ET; rests until open_auction() fills it.
- tif day market: fills at once in regular hours; outside them it rests and fills at the open.
- 403 potential wash trade: a new order while an opposite side order in the same stock is open.
- GET /v2/calendar and GET /v2/corporate_actions/announcements.
Prices are whatever the test or the dry run sets (the dry run uses real research bars).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Dict, List, Optional

import httpx

from backend.app.core import overnight_schedule as osch
from backend.app.core.broker import AlpacaBroker
from backend.app.core.overnight_execution import InlineExecutor
from backend.app.core.overnight_schedule import ET
from backend.tests.unit.orb_execution.fakes import FakeAlpaca, FakeClock, OPEN_STATES

CAL = osch.TradingWindowsCalendar()


def at(d: date, h: int, m: int, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=ET)


class OvernightAlpaca(FakeAlpaca):
    def __init__(self, clock: FakeClock, equity: float = 49_700.0, buying_power: Optional[float] = None):
        super().__init__(clock, equity, buying_power=buying_power if buying_power is not None else 4 * equity)
        self.account.update({"cash": str(equity), "regt_buying_power": str(2 * equity),
                             "daytrading_buying_power": str(4 * equity), "last_maintenance_margin": "0",
                             "multiplier": "4"})
        self.corporate_actions: List[dict] = []
        self.refuse_tif: Dict[str, tuple] = {}     # tif -> (status, code, message) for every such POST
        self.wash_refusals: List[dict] = []
        self.calendar_override: Optional[Callable[[date, date], List[dict]]] = None
        self.network_hosts: List[str] = []
        self.halted: set = set()                   # symbols whose orders do not fill (a halted open)

    # ------------------------------------------------------------------ helpers
    def _et(self) -> datetime:
        return self.clock.now.astimezone(ET)

    def regular_hours(self) -> bool:
        t = self._et()
        try:
            return CAL.is_trading_day(t.date()) and time(9, 30) <= t.time() < CAL.session_close(t.date())
        except osch.CalendarNotCovered:
            return False

    def live(self, sym: Optional[str] = None, side: Optional[str] = None) -> List[dict]:
        return [o for o in self.orders.values() if o["status"] in OPEN_STATES and not o.get("parent_id")
                and (sym is None or o["symbol"] == sym) and (side is None or o["side"] == side)]

    def signed_positions(self) -> Dict[str, int]:
        return {s: int(p["qty"]) for s, p in self.positions.items()}

    def posts(self, sym: Optional[str] = None) -> List[dict]:
        return [b for m, p, b, _q in self.requests if m == "POST" and p == "/v2/orders"
                and (sym is None or b["symbol"] == sym)]

    # ------------------------------------------------------------------ transport
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.network_hosts.append(request.url.host)
        path = request.url.path
        if path == "/v2/calendar":
            p = request.url.params
            self.requests.append(("GET", path, None, dict(p)))
            start, end = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
            if self.calendar_override is not None:
                return httpx.Response(200, json=self.calendar_override(start, end))
            rows, d = [], start
            while d <= end:
                try:
                    if CAL.is_trading_day(d):
                        rows.append({"date": d.isoformat(), "open": "09:30",
                                     "close": CAL.session_close(d).strftime("%H:%M")})
                except osch.CalendarNotCovered:
                    pass
                d += timedelta(days=1)
            return httpx.Response(200, json=rows)
        if path == "/v2/corporate_actions/announcements":
            p = request.url.params
            self.requests.append(("GET", path, None, dict(p)))
            return httpx.Response(200, json=[a for a in self.corporate_actions if a.get("initiating_symbol") == p["symbol"]])
        return super().handler(request)

    def _post(self, body: dict) -> httpx.Response:
        if self.by_coid(body["client_order_id"]):
            return httpx.Response(422, json={"code": 40010001, "message": "client_order_id must be unique"})
        tif, sym, side = body["time_in_force"], body["symbol"], body["side"]
        t = self._et().time()
        if tif in self.refuse_tif:
            status, code, message = self.refuse_tif[tif]
            return httpx.Response(status, json={"code": code, "message": message})
        if tif == "cls" and t >= time(15, 50):
            return httpx.Response(422, json={"code": 42210000, "message": "cls orders are not accepted after 15:50"})
        if tif == "opg" and time(9, 28) <= t < time(19, 0):
            return httpx.Response(422, json={"code": 42210000, "message": "opg orders are not accepted at this time"})
        opposite = "sell" if side == "buy" else "buy"
        if self.live(sym, opposite):
            err = {"code": 40310000, "message": "potential wash trade detected. use complex orders"}
            self.wash_refusals.append(dict(body))
            return httpx.Response(403, json=err)
        if tif in ("cls", "opg") or (tif == "day" and body["type"] == "market"
                                     and (not self.regular_hours() or sym in self.halted)):
            # rests at Alpaca: the auction (or the next open for a day market order) fills it
            saved = self.market_fills
            self.market_fills = False
            try:
                resp = super()._post(body)
            finally:
                self.market_fills = saved
            row = self.by_coid(body["client_order_id"])
            if row is not None and row["status"] == "new":
                row["status"] = "accepted"
            return resp
        return super()._post(body)

    # ------------------------------------------------------------------ the market
    def close_auction(self, prices: Dict[str, float], fill_ratio: Optional[Dict[str, float]] = None) -> None:
        """16:00: every resting cls order fills at the closing price (or partly; the rest expires)."""
        for o in list(self.orders.values()):
            if o["time_in_force"] == "cls" and o["status"] in OPEN_STATES:
                q = int(int(o["qty"]) * (fill_ratio or {}).get(o["symbol"], 1.0))
                if q:
                    self.fill(o["id"], q, prices[o["symbol"]])
                if o["status"] != "filled":
                    o["status"] = "expired"

    def open_auction(self, prices: Dict[str, float]) -> None:
        """09:30: every resting opg order, and every day market order queued outside hours, fills at the open."""
        for o in list(self.orders.values()):
            if (o["status"] not in OPEN_STATES or o.get("parent_id") or o["symbol"] not in prices
                    or o["symbol"] in self.halted):
                continue
            if o["time_in_force"] == "opg" or (o["time_in_force"] == "day" and o["type"] == "market"):
                self.fill(o["id"], None, prices[o["symbol"]])

    def expire_day_orders(self) -> None:
        for o in self.orders.values():
            if o["status"] in OPEN_STATES and o["time_in_force"] in ("day", "cls"):
                o["status"] = "expired"


def make_broker(alpaca: OvernightAlpaca) -> AlpacaBroker:
    broker = AlpacaBroker("test-key", "test-secret", transport=httpx.MockTransport(alpaca.handler),
                          fill_wait_sec=0.0, cancel_wait_sec=0.0, poll_interval_sec=0.0)
    broker._sleep = lambda s: None
    return broker


class MainOvernight:
    """The real backend.app.main with one fake Alpaca behind every arm, on a fake clock."""

    def __init__(self, r, start: datetime, equity: float = 49_700.0, bars: Optional[Callable] = None,
                 mode: str = "live", build: bool = True):
        self.r = r
        self.clock = FakeClock(start)
        self.alpaca = OvernightAlpaca(self.clock, equity)
        self.broker = make_broker(self.alpaca)
        r.reset_runtime_state(equity)
        r.set_simulation_mode(False)
        r.alpaca_broker = self.broker
        r.engine.broker = self.broker
        r.broker_state.update({"mode": "alpaca_paper", "mismatch": False, "mismatch_detail": None,
                               "mismatch_streak": 0})
        r.engine.broker_gate = self._gate
        self.bar_counts: Dict[str, tuple] = {}
        self.bar_calls: List[tuple] = []
        self._bars = bars
        r.flattening_engine.clock.set_simulated_time(start)
        r.last_session_date = start.astimezone(ET).date()
        if build:
            self.build(mode)

    def build(self, mode: str = "live"):
        self.ctl = self.r.overnight.build(self.broker, mode=mode, clock=self.clock, executor=InlineExecutor(),
                                          bar_count=self._bar_count)
        return self.ctl

    def _bar_count(self, sym: str, d: date):
        self.bar_calls.append((sym, d))
        if self._bars is not None:
            return self._bars(sym, d)
        if sym in self.bar_counts:
            value = self.bar_counts[sym]
            if isinstance(value, Exception):
                raise value
            return value
        return (374, True)             # a full session: 09:30 to 15:44 = 375 bars minus one quiet minute

    def _gate(self, order, is_exit):
        """main._broker_gate with the fake clock instead of the wall clock (same rule)."""
        now_et = self.clock.now.astimezone(ET)
        today = now_et.date()
        if not self.r.is_trading_day(today) or not (time(9, 30) <= now_et.time() < self.r.session_close(today)):
            return ("MARKET_CLOSED: real orders only go out during regular hours", not is_exit, 60.0)
        if not is_exit and self.r.broker_state["mismatch"]:
            return ("BROKER_MISMATCH: bot and Alpaca positions differ; entries paused", True, 30.0)
        return None

    # ------------------------------------------------------------------ driving
    def set(self, now: datetime) -> None:
        self.clock.set(now)
        self.r.flattening_engine.clock.set_simulated_time(now)
        self.r.engine._broker_retry_after.clear()

    def step(self, now: Optional[datetime] = None) -> None:
        """One pass of main's runtime clock at the fake time."""
        if now is not None:
            self.set(now)
        asyncio.run(self.r._runtime_clock_step(self.clock.now))

    def run(self, until: datetime, every: float = 1.0) -> None:
        while self.clock.now < until:
            self.step(min(self.clock.now + timedelta(seconds=every), until))

    def compare(self) -> None:
        asyncio.run(self.r._broker_reconcile_once())

    def price(self, sym: str, px: float) -> None:
        self.alpaca.prices[sym] = px
        self.r.latest_market_prices[sym] = px

    def quote(self, sym: str, px: float, spread: float = 0.02) -> None:
        from backend.app.models.events import QuoteEvent
        self.price(sym, px)
        q = QuoteEvent(sym, px - spread / 2, 100, "V", px + spread / 2, 100, "V", self.clock.now)
        asyncio.run(self.r.handle_quote_event(q))

    def bar(self, sym: str, px: float, volume: int = 100_000) -> None:
        from backend.app.models.events import BarEvent
        self.price(sym, px)
        b = BarEvent(sym, px, px, px, px, volume, self.clock.now - timedelta(minutes=1))
        asyncio.run(self.r.handle_bar_event(b))

    def day_trade(self, sym: str, qty: int, px: float, stop: float, side: str = "BUY", strategy_id: str = "vwap_pullback"):
        """A day trade through the real API order path (bracket with a stop), filled at the fake."""
        from backend.app.main import OrderCreateRequest, submit_order
        self.price(sym, px)
        out = asyncio.run(submit_order(OrderCreateRequest(symbol=sym, side=side, order_type="MARKET", qty=qty,
                                                          stop_price=stop, strategy_id=strategy_id)))
        self.quote(sym, px)
        return out

    def local(self) -> Dict[str, int]:
        return self.r._local_signed_positions(self.r.account)

    def night(self, sym: str, d: date) -> dict:
        return self.ctl.state["nights"][f"{sym}:{d.isoformat()}"]


PRICES = {"NVDA": 180.0, "IREN": 40.0, "HUT": 50.0}


def buy_night(h: MainOvernight, d: date, close: Optional[Dict[str, float]] = None, start=(15, 40)) -> None:
    """Run main's clock from 15:40 through the closing auction and the booking (16:00:30)."""
    h.set(at(d, *start))
    for sym, px in PRICES.items():
        h.price(sym, px)
    h.run(at(d, 15, 59, 59), every=5)
    h.set(at(d, 16, 0))
    h.alpaca.close_auction(close or PRICES)
    h.run(at(d, 16, 0, 30))


def queue_sales(h: MainOvernight, d: date) -> None:
    h.run(at(d, 19, 1), every=10)


def open_sale(h: MainOvernight, d: date, prices: Optional[Dict[str, float]] = None, until=(9, 30, 30)) -> None:
    h.run(at(d, 9, 29, 50), every=60) if h.clock.now < at(d, 9, 29) else None
    h.set(at(d, 9, 30))
    h.alpaca.open_auction(prices or PRICES)
    h.run(at(d, *until))
