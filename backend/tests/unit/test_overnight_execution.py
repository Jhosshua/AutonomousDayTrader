# @steered SNARE-2 2026-09-30
"""Overnight holds controller against a fake Alpaca and a fake clock (plan T3, T4, T9, D4, D10, X11).

The fake Alpaca keeps its own orders and positions across a simulated robot restart, enforces
Alpaca's auction windows (cls refused after 15:50, opg refused 09:28 to 19:00) and duplicate
client ids, and runs the closing and opening auctions when the test says so.
"""
from datetime import date, datetime, time, timedelta, timezone
import json

import pytest

from backend.app.core import overnight_schedule as osch
from backend.app.core.broker import BrokerHTTPError, BrokerTransportError, is_terminal
from backend.app.core.overnight_execution import (
    BUY_ACCEPTED, HELD, INTENT, SALE_QUEUED, SKIPPED, SOLD, Hooks, InlineExecutor, OvernightController,
)
from backend.app.core.overnight_schedule import ET, et

PROD = osch.TradingWindowsCalendar()
THU, FRI, MON = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)
PRICES = {"NVDA": 180.0, "IREN": 40.0, "HUT": 50.0}
EQUITY = 49_700.0


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class FakeAlpaca:
    def __init__(self, clock):
        self.clock = clock
        self.orders = {}
        self.positions = {}
        self.posts = []          # (et time, body) of every POST that reached Alpaca
        self.cancels = []
        self.calls = 0
        self.fail_posts = []     # (kind, landed): kind transport | 429 | 500
        self.refuse = {}         # tif -> (status, message) definite refusal for every such POST
        self.refuse_symbols = None
        self.lookup_lag = set()  # client ids whose first lookup answers "unknown" although they exist
        self.halted = set()
        self.corporate_actions = []
        self.ca_error = False
        self.assets = {}         # symbol -> {"marginable", "margin_requirement_long" (a fraction)}
        self.asset_error = set() # symbols whose asset read fails
        self.account = {"account_number": "PA3CSVDZMMPY", "equity": EQUITY, "cash": EQUITY,
                        "buying_power": 2 * EQUITY, "regt_buying_power": 2 * EQUITY,
                        "daytrading_buying_power": 4 * EQUITY, "last_maintenance_margin": 0.0, "multiplier": 2.0}
        self.price = dict(PRICES)

    # -------- helpers
    def _et(self):
        return self.clock.now.astimezone(ET)

    def _open(self):
        t = self._et()
        return PROD.is_trading_day(t.date()) and time(9, 30) <= t.time() < time(16, 0)

    def _fill(self, o, qty, px):
        sign = 1 if o["side"] == "buy" else -1
        sym = o["symbol"]
        self.positions[sym] = self.positions.get(sym, 0) + sign * qty
        if self.positions[sym] == 0:
            del self.positions[sym]
        o.update(status="filled" if qty == int(o["qty"]) else "partially_filled",
                 filled_qty=str(qty), filled_avg_price=str(px), filled_at=self.clock.now.isoformat())

    def _post(self, body):
        self.calls += 1
        t = self._et()
        self.posts.append((t, body))
        if self.fail_posts:
            kind, landed = self.fail_posts.pop(0)
            if landed:
                self._create(body)
            if kind == "transport":
                raise BrokerTransportError("connection reset")
            raise BrokerHTTPError(f"HTTP {kind}", int(kind), {"message": "busy"})
        if any(o["client_order_id"] == body["client_order_id"] for o in self.orders.values()):
            raise BrokerHTTPError("dup", 422, {"code": 40010001, "message": "client_order_id must be unique"})
        tif = body["time_in_force"]
        if tif in self.refuse and (self.refuse_symbols is None or body["symbol"] in self.refuse_symbols):
            status, message = self.refuse[tif]
            raise BrokerHTTPError("refused", status, {"code": 40310000, "message": message})
        if tif == "cls" and t.time() >= time(15, 50):
            raise BrokerHTTPError("late", 422, {"message": "cls orders are not accepted after 15:50"})
        if tif == "opg" and time(9, 28) <= t.time() < time(19, 0):
            raise BrokerHTTPError("window", 422, {"message": "opg orders are not accepted at this time"})
        return self._create(body)

    def _create(self, body):
        oid = f"o{len(self.orders) + 1}"
        o = dict(body, id=oid, status="accepted", filled_qty="0", filled_avg_price=None, filled_at=None)
        self.orders[oid] = o
        if body["time_in_force"] == "day" and self._open() and body["symbol"] not in self.halted:
            self._fill(o, int(body["qty"]), self.price[body["symbol"]])
        return dict(o)

    # -------- the AlpacaBroker surface the controller uses
    def submit_on_auction(self, symbol, side, qty, client_order_id, tif):
        assert tif in ("cls", "opg")
        return self._post({"symbol": symbol, "qty": str(qty), "side": side, "type": "market",
                           "time_in_force": tif, "client_order_id": client_order_id})

    def submit_market_order(self, symbol, side, qty, client_order_id):
        return self._post({"symbol": symbol, "qty": str(qty), "side": side, "type": "market",
                           "time_in_force": "day", "client_order_id": client_order_id})

    def get_order_by_client_id(self, client_order_id, nested=True):
        self.calls += 1
        found = next((o for o in self.orders.values() if o["client_order_id"] == client_order_id), None)
        if found is not None and client_order_id in self.lookup_lag:
            self.lookup_lag.discard(client_order_id)
            return None
        return dict(found) if found is not None else None

    def get_order(self, alpaca_id, nested=True):
        self.calls += 1
        return dict(self.orders[alpaca_id])

    def cancel_order_and_confirm(self, order_id, timeout=6.0):
        self.calls += 1
        self.cancels.append(order_id)
        o = self.orders[order_id]
        if not is_terminal(o):
            o["status"] = "canceled"
        return dict(o)

    def get_calendar(self, start, end):
        self.calls += 1
        rows, d = [], date.fromisoformat(start)
        while d <= date.fromisoformat(end):
            try:
                if PROD.is_trading_day(d):
                    rows.append({"date": d.isoformat(), "open": "09:30", "close": PROD.session_close(d).strftime("%H:%M")})
            except osch.CalendarNotCovered:
                pass
            d += timedelta(days=1)
        return rows

    def get_account_fields(self):
        self.calls += 1
        return dict(self.account)

    def get_asset_margin(self, symbol):
        self.calls += 1
        if symbol in self.asset_error:
            raise BrokerTransportError("asset read down")
        return {"symbol": symbol, **self.assets.get(symbol, {"marginable": True, "margin_requirement_long": 0.5})}

    def get_corporate_actions(self, symbol, since, until):
        self.calls += 1
        if self.ca_error:
            raise BrokerTransportError("corporate actions down")
        return [dict(a) for a in self.corporate_actions if a["old_symbol"] == symbol]

    def position_qty(self, symbol):
        self.calls += 1
        return self.positions.get(symbol, 0)

    def list_open_orders(self, symbol=None):
        self.calls += 1
        return [dict(o) for o in self.orders.values() if not is_terminal(o) and (symbol is None or o["symbol"] == symbol)]

    # -------- the market
    def close_auction(self, px=None, ratio=None):
        for o in self.orders.values():
            if o["time_in_force"] == "cls" and not is_terminal(o):
                q = int(int(o["qty"]) * (ratio or {}).get(o["symbol"], 1.0))
                if q:
                    self._fill(o, q, (px or {}).get(o["symbol"], self.price[o["symbol"]]))
                    o["status"] = "filled" if q == int(o["qty"]) else "expired"   # rest not crossed
                else:
                    o["status"] = "expired"

    def open_auction(self, px=None):
        for o in self.orders.values():
            if is_terminal(o) or o["symbol"] in self.halted or o["time_in_force"] not in ("opg", "day"):
                continue
            self._fill(o, int(o["qty"]), (px or {}).get(o["symbol"], self.price[o["symbol"]]))

    def unhalt(self, sym):
        self.halted.discard(sym)
        for o in self.orders.values():
            if o["symbol"] == sym and o["time_in_force"] == "day" and not is_terminal(o):
                self._fill(o, int(o["qty"]), self.price[sym])

    def sells(self, sym=None):
        return [(t, b) for t, b in self.posts if b["side"] == "sell" and (sym is None or b["symbol"] == sym)]

    def buys(self, sym=None):
        return [(t, b) for t, b in self.posts if b["side"] == "buy" and (sym is None or b["symbol"] == sym)]

    def live(self, sym, side):
        return [o for o in self.orders.values() if o["symbol"] == sym and o["side"] == side and not is_terminal(o)]


class Rig:
    """One robot process: the controller plus its injected dependencies."""

    def __init__(self, clock, alpaca, state=None, **kw):
        self.clock, self.alpaca = clock, alpaca
        self.booked, self.alerts, self.saves = [], [], []
        self.save_ok = True
        self.bars = kw.pop("bars", lambda sym, d: (375, True))
        self.prices = kw.pop("prices", None)
        self.hooks = kw.pop("hooks", Hooks())
        broker = kw.pop("broker", alpaca)
        deps = dict(broker=broker, executor=kw.pop("executor", InlineExecutor()), calendar=PROD, clock=clock,
                    book=self._book, checkpoint=self._save, bar_count=lambda s, d: self.bars(s, d),
                    last_price=lambda s: (self.prices or alpaca.price).get(s), alert=self._alert, hooks=self.hooks, **kw)
        self.ctl = OvernightController.from_json(state, **deps)

    def _book(self, event):
        self.booked.append(event)
        return True

    def _save(self, reason):
        self.saves.append((reason, self.ctl.to_json() if hasattr(self, "ctl") else None))
        return self.save_ok

    def _alert(self, kind, text, fields):
        self.alerts.append((kind, text, fields))

    def run(self, start, end, step=1.0, each=None):
        self.clock.now = start
        while self.clock.now <= end:
            self.ctl.tick(self.clock.now)
            if each:
                each(self)
            self.clock.now += timedelta(seconds=step)
        self.clock.now = end

    def at(self, moment):
        self.clock.now = moment
        self.ctl.tick(moment)

    def restart(self, state=None, **kw):
        state = self.ctl.to_json() if state is None else state
        return Rig(self.clock, self.alpaca, state=json.loads(json.dumps(state)), **kw)

    def night(self, sym, d=THU):
        return self.ctl.state["nights"][f"{sym}:{d.isoformat()}"]


def T(d, h, m, s=0):
    return et(d, time(h, m, s))


def new(d=THU, **kw):
    clock = Clock(T(d, 15, 44))
    alpaca = FakeAlpaca(clock)
    return Rig(clock, alpaca, **kw)


def buy_day(rig, d=THU, until=(16, 10)):
    rig.run(T(d, 15, 44), T(d, 15, 59, 59))
    rig.alpaca.clock.now = T(d, 16, 0)
    rig.alpaca.close_auction()
    rig.run(T(d, 16, 0), T(d, *until), step=5)


def overnight(rig, d=THU, sale=FRI):
    rig.run(T(d, 19, 0), T(d, 19, 2), step=5)
    rig.at(T(sale, 9, 0))
    rig.at(T(sale, 9, 0, 30))
    rig.alpaca.clock.now = T(sale, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(sale, 9, 30), T(sale, 9, 33), step=5)


# ======================================================================= normal night
def test_normal_night_buys_at_the_close_and_sells_at_the_next_open():
    rig = new()
    rig.run(T(THU, 15, 44), T(THU, 15, 46, 4))
    assert rig.alpaca.posts == [] and not rig.ctl.reserves("TSLA")
    assert all(rig.ctl.reserves(s) for s in osch.SYMBOLS)            # from 15:45
    rig.run(T(THU, 15, 46, 5), T(THU, 15, 50))
    buys = rig.alpaca.buys()
    assert [(b["symbol"], b["qty"], b["time_in_force"], b["client_order_id"]) for _, b in buys] == [
        ("NVDA", "55", "cls", "adt-ovn-NVDA-20261001-buy-1"), ("IREN", "248", "cls", "adt-ovn-IREN-20261001-buy-1"),
        ("HUT", "198", "cls", "adt-ovn-HUT-20261001-buy-1")]
    assert all(t.time() == time(15, 46, 5) for t, _ in buys)
    # the durable intent was saved before each send, and it names the client id
    intents = [s for r, s in rig.saves if r == "OVERNIGHT_BUY_INTENT"]
    assert len(intents) == 3 and "adt-ovn-NVDA-20261001-buy-1" in json.dumps(intents[0])
    assert all(rig.night(s)["state"] == BUY_ACCEPTED for s in osch.SYMBOLS)
    rig.alpaca.clock.now = T(THU, 16, 0)
    rig.alpaca.close_auction(px={"NVDA": 181.0, "IREN": 41.0, "HUT": 51.0})
    rig.run(T(THU, 16, 0), T(THU, 16, 0, 10))
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [HELD] * 3
    assert [(e["role"], e["symbol"], e["qty"], e["price"]) for e in rig.booked] == [
        ("buy", "NVDA", 55, 181.0), ("buy", "IREN", 248, 41.0), ("buy", "HUT", 198, 51.0)]
    rig.run(T(THU, 16, 0, 10), T(THU, 19, 0, 29), step=30)
    assert rig.alpaca.sells() == []
    rig.run(T(THU, 19, 0, 30), T(THU, 19, 1))
    sells = rig.alpaca.sells()
    assert [(b["symbol"], b["qty"], b["time_in_force"], b["client_order_id"]) for _, b in sells] == [
        ("NVDA", "55", "opg", "adt-ovn-NVDA-20261001-sell-1"), ("IREN", "248", "opg", "adt-ovn-IREN-20261001-sell-1"),
        ("HUT", "198", "opg", "adt-ovn-HUT-20261001-sell-1")]
    assert all(t.time() == time(19, 0, 30) for t, _ in sells)
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [SALE_QUEUED] * 3
    rig.at(T(FRI, 9, 0))
    assert all(rig.night(s)["morning_checked"] for s in osch.SYMBOLS)
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction(px={"NVDA": 183.0, "IREN": 40.0, "HUT": 52.0})
    rig.run(T(FRI, 9, 30), T(FRI, 9, 30, 10))
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3
    assert not any(rig.ctl.reserves(s) for s in osch.SYMBOLS)
    realized = 55 * 2.0 + 248 * -1.0 + 198 * 1.0
    assert rig.ctl.overnight_realized_today(FRI) == pytest.approx(realized)
    assert rig.ctl.overnight_realized_today(THU) == 0.0
    assert [e["realized"] for e in rig.booked if e["role"] == "sell"] == pytest.approx([110.0, -248.0, 198.0])
    assert rig.alpaca.positions == {}
    assert len(rig.alpaca.posts) == 6


def test_friday_hold_sells_monday_and_the_state_is_plain_json():
    rig = new(d=FRI)
    buy_day(rig, d=FRI)
    assert rig.night("NVDA", FRI)["sale_date"] == MON.isoformat()
    rig.run(T(FRI, 19, 0), T(FRI, 19, 1))
    assert len(rig.alpaca.sells()) == 3
    rig.at(T(date(2026, 10, 3), 12, 0))           # Saturday: nothing new, no night
    assert not any(k.endswith("2026-10-03") for k in rig.ctl.state["nights"])
    overnight(rig, FRI, MON)
    assert [rig.night(s, FRI)["state"] for s in osch.SYMBOLS] == [SOLD] * 3
    text = json.dumps(rig.ctl.to_json())
    assert "__type__" not in text
    assert json.loads(text) == rig.ctl.to_json()


def test_day_market_route_saves_at_1546_then_sends_once_at_1559_30_after_restart():
    rig = new(buy_tif="day")
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    assert rig.alpaca.buys() == []
    assert all(rig.ctl.locks_all(s) for s in osch.SYMBOLS)
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [INTENT] * 3
    assert all(rig.night(s)["buy"][-1]["tif"] == "day" for s in osch.SYMBOLS)
    assert all(not rig.night(s)["buy"][-1]["sent"] for s in osch.SYMBOLS)
    intents = [state for reason, state in rig.saves if reason == "OVERNIGHT_BUY_INTENT"]
    assert len(intents) == 3

    rig = rig.restart(buy_tif="day")
    rig.run(T(THU, 15, 59, 29), T(THU, 15, 59, 40))
    buys = rig.alpaca.buys()
    assert [(body["symbol"], body["time_in_force"]) for _, body in buys] == [
        ("NVDA", "day"), ("IREN", "day"), ("HUT", "day")]
    assert all(sent_at.time() == time(15, 59, 30) for sent_at, _ in buys)
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [HELD] * 3
    assert len(rig.booked) == 3
    rig.run(T(THU, 15, 59, 41), T(THU, 16, 1))
    assert len(rig.alpaca.buys()) == 3


def test_day_market_restart_adopts_its_own_filled_order_without_a_duplicate():
    rig = new(buy_tif="day")
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    nv = rig.night("NVDA")
    rig.clock.now = T(THU, 15, 59, 30)
    rig.alpaca.submit_market_order("NVDA", "buy", nv["qty"], nv["buy"][-1]["cid"])

    rig = rig.restart(buy_tif="day")
    rig.run(T(THU, 15, 59, 31), T(THU, 15, 59, 40))
    assert len(rig.alpaca.buys("NVDA")) == 1
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [HELD] * 3
    assert sorted(event["symbol"] for event in rig.booked) == ["HUT", "IREN", "NVDA"]


def test_day_market_restart_after_close_cancels_its_own_unfinished_order():
    rig = new(buy_tif="day", enabled=("NVDA",))
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    rig.alpaca.halted.add("NVDA")
    nv = rig.night("NVDA")
    rig.clock.now = T(THU, 15, 59, 30)
    row = rig.alpaca.submit_market_order("NVDA", "buy", nv["qty"], nv["buy"][-1]["cid"])
    assert row["status"] == "accepted"

    rig = rig.restart(buy_tif="day")
    rig.at(T(THU, 16, 0, 5))
    assert rig.night("NVDA")["state"] == BUY_ACCEPTED
    rig.at(T(THU, 16, 0, 6))
    nv = rig.night("NVDA")
    assert nv["state"] == SKIPPED and nv["reason"] == osch.MARKET_NO_FILL
    assert len(rig.alpaca.buys("NVDA")) == 1
    assert rig.alpaca.cancels == [row["id"]]
    rig.alpaca.halted.clear()
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    assert rig.alpaca.positions == {}


def test_day_market_route_waits_for_a_broker_mismatch_to_clear_before_sending():
    mismatch = {"active": False}
    rig = new(buy_tif="day", hooks=Hooks(broker_mismatch=lambda: mismatch["active"]))
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    mismatch["active"] = True
    rig.run(T(THU, 15, 59, 30), T(THU, 15, 59, 34))
    assert rig.alpaca.buys() == []
    assert all(rig.night(s)["block"] == osch.BROKER_MISMATCH for s in osch.SYMBOLS)
    mismatch["active"] = False
    rig.run(T(THU, 15, 59, 35), T(THU, 15, 59, 40))
    assert len(rig.alpaca.buys()) == 3
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [HELD] * 3


def test_day_market_route_rechecks_broker_mismatch_after_lookup_before_post():
    mismatch = {"active": False}
    rig = new(buy_tif="day", enabled=("NVDA",),
              hooks=Hooks(broker_mismatch=lambda: mismatch["active"]))
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    original = rig.alpaca.get_order_by_client_id

    def lookup_then_mismatch(*args, **kwargs):
        row = original(*args, **kwargs)
        mismatch["active"] = True
        return row

    rig.alpaca.get_order_by_client_id = lookup_then_mismatch
    rig.at(T(THU, 15, 59, 30))
    assert rig.alpaca.buys("NVDA") == []
    assert rig.night("NVDA")["state"] == INTENT
    assert rig.night("NVDA")["block"] == osch.BROKER_MISMATCH

    mismatch["active"] = False
    rig.alpaca.get_order_by_client_id = original
    rig.at(T(THU, 15, 59, 35))
    assert len(rig.alpaca.buys("NVDA")) == 1
    assert rig.night("NVDA")["state"] == HELD


@pytest.mark.parametrize("late_state", ["position", "open_order"])
def test_day_market_preflight_rejects_late_broker_exposure(late_state):
    rig = new(buy_tif="day", enabled=("NVDA",))
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    if late_state == "position":
        rig.alpaca.positions["NVDA"] = 1
    else:
        rig.alpaca.orders["external"] = {
            "id": "external", "symbol": "NVDA", "qty": "1", "side": "buy", "type": "limit",
            "time_in_force": "day", "client_order_id": "external", "status": "accepted",
            "filled_qty": "0", "filled_avg_price": None, "filled_at": None}
    rig.run(T(THU, 15, 59, 30), T(THU, 15, 59, 55))
    nv = rig.night("NVDA")
    assert rig.alpaca.buys("NVDA") == []
    assert nv["state"] == SKIPPED and nv["reason"] == osch.BROKER_NOT_FLAT


def test_day_market_preflight_resizes_for_fresh_price_and_buying_power():
    rig = new(buy_tif="day")
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 19))
    rig.alpaca.price["NVDA"] = 360.0
    rig.alpaca.account["buying_power"] = 10_000.0
    rig.run(T(THU, 15, 59, 20), T(THU, 15, 59, 40))
    buys = {body["symbol"]: int(body["qty"]) for _, body in rig.alpaca.buys()}
    assert buys == {"NVDA": 27, "IREN": 7}
    assert sum(buys[sym] * rig.alpaca.price[sym] for sym in buys) <= 10_000.0
    assert rig.night("HUT")["state"] == INTENT and rig.night("HUT")["block"] == osch.NO_ROOM


def test_day_market_post_crossing_1600_is_cancelled_and_never_fills_next_morning():
    rig = new(buy_tif="day", enabled=("NVDA",))
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    original = rig.alpaca.submit_market_order

    def late_submit(*args, **kwargs):
        rig.clock.now = T(THU, 16, 0)
        return original(*args, **kwargs)

    rig.alpaca.submit_market_order = late_submit
    rig.at(T(THU, 15, 59, 30))
    nv = rig.night("NVDA")
    assert nv["state"] == SKIPPED and nv["reason"] == osch.MARKET_NO_FILL
    assert len(rig.alpaca.cancels) == 1 and rig.alpaca.positions == {}
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    assert rig.alpaca.positions == {}


def test_day_market_post_crossing_1600_retries_a_failed_cancel():
    rig = new(buy_tif="day", enabled=("NVDA",))
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    original_submit = rig.alpaca.submit_market_order
    original_cancel = rig.alpaca.cancel_order_and_confirm
    calls = {"cancel": 0}

    def late_submit(*args, **kwargs):
        rig.clock.now = T(THU, 16, 0)
        rig.alpaca.halted.add("NVDA")
        return original_submit(*args, **kwargs)

    def fail_once(*args, **kwargs):
        calls["cancel"] += 1
        if calls["cancel"] == 1:
            raise BrokerTransportError("cancel timed out")
        return original_cancel(*args, **kwargs)

    rig.alpaca.submit_market_order = late_submit
    rig.alpaca.cancel_order_and_confirm = fail_once
    rig.at(T(THU, 15, 59, 30))
    assert rig.night("NVDA")["state"] == BUY_ACCEPTED
    assert rig.night("NVDA")["buy"][-1]["cancel_requested"] is True
    rig.at(T(THU, 16, 0, 1))
    nv = rig.night("NVDA")
    assert nv["state"] == SKIPPED and nv["reason"] == osch.MARKET_NO_FILL
    assert calls["cancel"] == 2


def test_day_market_control_after_arming_stops_the_unsent_intent():
    rig = new(buy_tif="day", enabled=("NVDA",))
    rig.run(T(THU, 15, 44), T(THU, 15, 48))
    assert rig.night("NVDA")["state"] == INTENT
    assert rig.ctl.set_no_buy_tonight(T(THU, 15, 48))[0]
    rig.at(T(THU, 15, 48, 1))
    nv = rig.night("NVDA")
    assert nv["state"] == SKIPPED and nv["reason"] == osch.OPERATOR_NO_BUY_TONIGHT
    assert rig.alpaca.buys("NVDA") == [] and rig.alpaca.cancels == []


# ============================================================================ T3
def test_fake_alpaca_enforces_the_auction_windows():
    clock = Clock(T(THU, 15, 49, 59))
    a = FakeAlpaca(clock)
    a.submit_on_auction("NVDA", "buy", 1, "a", "cls")
    clock.now = T(THU, 15, 50, 1)
    with pytest.raises(BrokerHTTPError):
        a.submit_on_auction("NVDA", "buy", 1, "b", "cls")
    for h, m in ((9, 28), (12, 0), (18, 59)):
        clock.now = T(FRI, h, m)
        with pytest.raises(BrokerHTTPError):
            a.submit_on_auction("NVDA", "sell", 1, f"s{h}", "opg")
    clock.now = T(THU, 19, 0, 1)
    assert a.submit_on_auction("NVDA", "sell", 1, "ok", "opg")["status"] == "accepted"


def test_controller_sends_cls_only_before_1549_30_and_opg_only_after_1900():
    rig = new()
    buy_day(rig)
    overnight(rig)
    for t, b in rig.alpaca.posts:
        if b["time_in_force"] == "cls":
            assert time(15, 46, 5) <= t.time() < time(15, 49, 30)
        if b["time_in_force"] == "opg":
            assert t.time() >= time(19, 0, 30) or t.time() < time(9, 27, 30)


def test_lost_reply_after_the_order_landed_is_looked_up_never_resent():
    # Mutation guard: without lookup-before-resend a second POST would reach Alpaca here.
    rig = new()
    rig.alpaca.fail_posts = [("transport", True), ("500", True), ("429", False)]
    rig.run(T(THU, 15, 44), T(THU, 15, 49, 40))
    for sym in ("NVDA", "IREN"):
        assert len(rig.alpaca.buys(sym)) == 1, sym
        assert len([o for o in rig.alpaca.orders.values() if o["symbol"] == sym]) == 1
        assert rig.night(sym)["state"] == BUY_ACCEPTED
    # 429 that did not land: looked up (unknown), then resent under the SAME id, one order
    hut = rig.alpaca.buys("HUT")
    assert [b["client_order_id"] for _, b in hut] == ["adt-ovn-HUT-20261001-buy-1"] * 2
    assert (hut[1][0] - hut[0][0]).total_seconds() >= osch.BUY_RETRY_SEC
    assert len([o for o in rig.alpaca.orders.values() if o["symbol"] == "HUT"]) == 1


def test_duplicate_client_id_answer_is_resolved_by_lookup():
    rig = new()
    rig.alpaca.fail_posts = [("transport", True)]
    rig.alpaca.lookup_lag = {"adt-ovn-NVDA-20261001-buy-1"}    # Alpaca slow to show the landed order
    rig.run(T(THU, 15, 44), T(THU, 15, 49, 40))
    assert [b["client_order_id"] for _, b in rig.alpaca.buys("NVDA")] == ["adt-ovn-NVDA-20261001-buy-1"] * 2
    assert len([o for o in rig.alpaca.orders.values() if o["symbol"] == "NVDA"]) == 1
    assert rig.night("NVDA")["state"] == BUY_ACCEPTED
    assert rig.night("NVDA")["buy"][-1]["n"] == 1


def test_fidelity_log_stores_caller_fields_and_derives_gross_returns():
    rig = new()
    buy_day(rig)
    rig.ctl.record_fidelity("NVDA", THU, {"feed": "sip", "research_entry": 180.0, "official_close": 180.1,
                                          "split_ratio": 1.0, "ok_next_day": None})
    overnight(rig)
    rig.ctl.record_fidelity("NVDA", THU, {"research_exit": 182.0, "official_open": 182.0, "ok_next_day": True})
    fid = rig.night("NVDA")["fidelity"]
    assert fid["feed"] == "sip" and fid["ok_next_day"] is True
    assert fid["research_gross"] == pytest.approx(182.0 / 180.0 - 1)
    assert fid["real_gross"] == pytest.approx(0.0)          # the fake fills both auctions at 180
    rig.ctl.record_fidelity("TSLA", THU, {"x": 1})           # unknown night: ignored
    assert "TSLA:2026-10-01" not in rig.ctl.state["nights"]


def test_cumulative_fills_are_booked_once_across_polls_and_a_restart():
    rig = new()
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 59))
    oid = rig.night("NVDA")["buy"][-1]["id"]
    o = rig.alpaca.orders[oid]
    rig.alpaca.clock.now = T(THU, 16, 0)
    rig.alpaca._fill(o, 20, 180.0)                         # partially_filled, still live
    rig.run(T(THU, 16, 0), T(THU, 16, 0, 20))              # polled several times
    assert [e["qty"] for e in rig.booked if e["symbol"] == "NVDA"] == [20]
    rig = rig.restart()
    rig.ctl.reconcile(T(THU, 16, 0, 30))
    rig.run(T(THU, 16, 0, 30), T(THU, 16, 0, 40))
    assert [e["qty"] for e in rig.booked if e["symbol"] == "NVDA"] == []    # nothing booked twice
    o.update(status="filled", filled_qty="55", filled_avg_price=str((20 * 180.0 + 35 * 181.0) / 55))
    rig.alpaca.positions["NVDA"] = 55
    rig.run(T(THU, 16, 0, 41), T(THU, 16, 1))
    nv = rig.night("NVDA")
    assert [(e["qty"], round(e["price"], 6)) for e in rig.booked if e["symbol"] == "NVDA"] == [(35, 181.0)]
    assert nv["state"] == HELD and nv["held_qty"] == 55


def test_partial_and_zero_auction_fills():
    rig = new()
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 59))
    rig.alpaca.clock.now = T(THU, 16, 0)
    rig.alpaca.close_auction(ratio={"NVDA": 0.5, "IREN": 0.0})
    rig.run(T(THU, 16, 0), T(THU, 16, 10), step=5)
    nv, ir = rig.night("NVDA"), rig.night("IREN")
    assert nv["state"] == HELD and nv["held_qty"] == 27
    assert any(r["event"] == "X11_PARTIAL_FILL" and r["symbol"] == "NVDA" for r in rig.ctl.state["log"])
    assert ir["state"] == SKIPPED and ir["reason"] == osch.AUCTION_NO_FILL and not rig.ctl.reserves("IREN")
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.sells()] == [("NVDA", "27"), ("HUT", "198")]


def test_wash_trade_refusal_retries_one_attempt_at_a_time_then_skips_without_fallback():
    rig = new()
    rig.alpaca.refuse = {"cls": (403, "potential wash trade detected. use complex orders")}
    rig.alpaca.refuse_symbols = {"NVDA"}

    def one_live(r):
        assert len(r.alpaca.live("NVDA", "buy")) <= 1

    rig.run(T(THU, 15, 44), T(THU, 15, 59, 59), each=one_live)
    nv = rig.night("NVDA")
    assert nv["state"] == SKIPPED and nv["reason"] == osch.WASH_TRADE_REFUSED
    posts = rig.alpaca.buys("NVDA")
    assert len(posts) > 1 and all(b["time_in_force"] == "cls" for _, b in posts)    # no D10 market buy
    assert [b["client_order_id"] for _, b in posts] == [f"adt-ovn-NVDA-20261001-buy-{i}" for i in range(1, len(posts) + 1)]
    assert all(t.time() < time(15, 49, 30) for t, _ in posts)
    assert rig.night("IREN")["state"] == BUY_ACCEPTED


def test_buying_power_refusal_is_a_skip_not_a_fallback():
    rig = new()
    rig.alpaca.refuse = {"cls": (403, "insufficient buying power")}
    rig.alpaca.refuse_symbols = {"HUT"}
    rig.run(T(THU, 15, 44), T(THU, 16, 1))
    assert rig.night("HUT")["reason"] == osch.BUYING_POWER_REFUSED
    assert all(b["time_in_force"] == "cls" for _, b in rig.alpaca.buys("HUT"))


def test_buying_power_refusal_retries_every_30s_not_every_5s():
    rig = new()
    rig.alpaca.refuse = {"cls": (403, "insufficient buying power")}
    rig.alpaca.refuse_symbols = {"NVDA"}
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    posts = rig.alpaca.buys("NVDA")
    assert 2 <= len(posts) <= 8        # was about 41 at one try per 5 s
    gaps = [(b - a).total_seconds() for (a, _), (b, _) in zip(posts, posts[1:])]
    assert all(g >= osch.SALE_RETRY_SEC for g in gaps)


def test_sale_refused_twice_in_a_row_slows_to_every_5_minutes():
    rig = new()
    buy_day(rig)
    rig.alpaca.refuse = {"opg": (403, "potential wash trade detected"), "day": (403, "potential wash trade detected")}
    rig.alpaca.refuse_symbols = {"HUT"}
    rig.run(T(THU, 19, 0), T(THU, 23, 0), step=5)
    n = rig.night("HUT")
    assert osch.SALE_REFUSED in n["needs_look"]
    assert 2 < len(rig.alpaca.sells("HUT")) <= 52   # was 480 at one try per 30 s
    assert len(n["legs"][0]["attempts"]) == len(rig.alpaca.sells("HUT"))


def test_buy_cancelled_by_hand_at_alpaca_is_a_logged_skip():
    rig = new()
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    oid = rig.night("IREN")["buy"][-1]["id"]
    rig.alpaca.orders[oid]["status"] = "canceled"
    rig.alpaca.clock.now = T(THU, 16, 0)
    rig.alpaca.close_auction()
    rig.run(T(THU, 16, 0), T(THU, 16, 1))
    ir = rig.night("IREN")
    assert ir["state"] == SKIPPED and ir["reason"] == osch.CANCELED_AT_ALPACA
    assert any(r["event"] == "SKIP" and r["reason"] == osch.CANCELED_AT_ALPACA for r in rig.ctl.state["log"])


def test_sale_cancelled_by_hand_overnight_is_replaced_at_the_0900_check():
    rig = new()
    buy_day(rig)
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)
    oid = rig.night("HUT")["legs"][0]["attempts"][-1]["id"]
    rig.alpaca.orders[oid]["status"] = "canceled"
    overnight(rig)
    hut = [b["client_order_id"] for _, b in rig.alpaca.sells("HUT")]
    assert hut == ["adt-ovn-HUT-20261001-sell-1", "adt-ovn-HUT-20261001-sell-2"]
    assert rig.night("HUT")["state"] == SOLD


def test_halted_open_gets_one_live_market_fallback_at_a_time():
    rig = new()
    buy_day(rig)
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)
    rig.at(T(FRI, 9, 0))
    rig.alpaca.halted = {"HUT"}
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()

    def one_live(r):
        assert len(r.alpaca.live("HUT", "sell")) <= 1

    rig.run(T(FRI, 9, 30), T(FRI, 9, 45), each=one_live)
    hut = rig.alpaca.sells("HUT")
    assert [(b["time_in_force"], b["client_order_id"]) for _, b in hut] == [
        ("opg", "adt-ovn-HUT-20261001-sell-1"), ("day", "adt-ovn-HUT-20261001-sell-2")]
    assert hut[1][0].time() >= time(9, 31)
    assert rig.night("HUT")["legs"][0]["attempts"][0]["id"] in rig.alpaca.cancels
    assert rig.ctl.unsold_after_0931(T(FRI, 9, 45)) == ["HUT"]
    rig.alpaca.unhalt("HUT")
    rig.run(T(FRI, 9, 45), T(FRI, 9, 46))
    assert rig.night("HUT")["state"] == SOLD and rig.ctl.unsold_after_0931(T(FRI, 9, 46)) == []
    assert rig.alpaca.positions.get("HUT", 0) == 0


def test_sale_is_sent_while_every_save_fails_but_a_buy_never_is():
    rig = new()
    buy_day(rig)
    rig.save_ok = False
    overnight(rig)
    assert len(rig.alpaca.sells()) == 3 and [rig.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3
    # next buy day with saves failing: nothing is sent, the night is skipped for the failed save
    rig.run(T(FRI, 15, 44), T(FRI, 15, 50))
    assert rig.alpaca.buys() and all(t.date() == THU for t, _ in rig.alpaca.buys())
    assert [rig.night(s, FRI)["reason"] for s in osch.SYMBOLS] == [osch.INTENT_NOT_DURABLE] * 3


def test_broker_is_only_called_on_the_executor():
    class Manual:
        def __init__(self):
            self.jobs = []

        def submit(self, fn):
            from concurrent.futures import Future
            f = Future()
            self.jobs.append((fn, f))
            return f

        def run_all(self):
            jobs, self.jobs = self.jobs, []
            for fn, f in jobs:
                try:
                    f.set_result(fn())
                except Exception as exc:
                    f.set_exception(exc)

    ex = Manual()
    rig = new(executor=ex)
    rig.at(T(THU, 15, 46, 5))
    assert rig.alpaca.calls == 0 and len(ex.jobs) == 3        # tick only queued work
    ex.run_all()
    rig.at(T(THU, 15, 46, 6))                                # results read, sends queued
    assert rig.alpaca.posts == [] and len(ex.jobs) == 3
    ex.run_all()
    rig.at(T(THU, 15, 46, 7))
    assert len(rig.alpaca.buys()) == 3 and [rig.night(s)["state"] for s in osch.SYMBOLS] == [BUY_ACCEPTED] * 3


# ============================================================================ D10
def test_d10_closing_auction_refused_becomes_a_market_buy_at_1559_30():
    rig = new()
    rig.alpaca.refuse = {"cls": (403, "closing auction orders are not supported for this account")}
    rig.run(T(THU, 15, 44), T(THU, 15, 59, 29))
    assert all(len(rig.alpaca.buys(s)) == 1 for s in osch.SYMBOLS)
    assert all(rig.ctl.reserves(s) for s in osch.SYMBOLS)     # still reserved: the day flatten leaves it
    rig.run(T(THU, 15, 59, 30), T(THU, 16, 5))
    for s in osch.SYMBOLS:
        posts = rig.alpaca.buys(s)
        assert [(b["time_in_force"], b["client_order_id"]) for _, b in posts] == [
            ("cls", f"adt-ovn-{s}-20261001-buy-1"), ("day", f"adt-ovn-{s}-20261001-buy-2")]
        assert time(15, 59, 30) <= posts[1][0].time() <= time(15, 59, 31)
        assert rig.night(s)["state"] == HELD
    assert any(r["event"] == "D10_BUY_FALLBACK_AT_1559" for r in rig.ctl.state["log"])


def test_d10_opening_auction_refused_becomes_a_queued_market_sale():
    rig = new()
    buy_day(rig)
    rig.alpaca.refuse = {"opg": (403, "opg orders are not supported for this account")}
    rig.run(T(THU, 19, 0), T(THU, 19, 2))
    for s in osch.SYMBOLS:
        posts = rig.alpaca.sells(s)
        assert [(b["time_in_force"], b["client_order_id"]) for _, b in posts] == [
            ("opg", f"adt-ovn-{s}-20261001-sell-1"), ("day", f"adt-ovn-{s}-20261001-sell-2")]
        assert posts[1][0].time() >= time(19, 0, 30)
    rig.alpaca.refuse = {}
    rig.at(T(FRI, 9, 0))
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 35))
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3
    assert all(len(rig.alpaca.sells(s)) == 2 for s in osch.SYMBOLS)   # the queued sale was not cancelled


# ============================================================ sizing, room, gates
def test_size_is_20pct_of_equity_capped_at_25000():
    rig = new()
    rig.alpaca.account.update(equity=200_000.0, buying_power=400_000.0)
    rig.alpaca.price.update(NVDA=100.0)
    rig.run(T(THU, 15, 44), T(THU, 15, 46, 6))
    assert rig.alpaca.buys("NVDA")[0][1]["qty"] == "250"


def test_full_size_is_not_lost_to_floating_point_when_nothing_binds():
    """D2 is floor(0.20 x equity / price). At NVDA 228.87 on $49,700 that is 43 shares. 43 x 228.87
    divided back by 228.87 is 42.99999 in floating point, which used to buy 42 and log a false X11."""
    rig = new()
    rig.alpaca.account.update(equity=49_700.0, buying_power=198_800.0)
    rig.alpaca.price.update(NVDA=228.87, IREN=41.73, HUT=92.75)
    rig.run(T(THU, 15, 44), T(THU, 15, 46, 6))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "43"), ("IREN", "238"), ("HUT", "107")]
    assert rig.night("NVDA")["shrunk"] is None
    assert not any(r["event"] == "X11_SHRUNK" for r in rig.ctl.state["log"])


def test_room_shrinks_in_nvda_iren_hut_order_and_logs_x11():
    hooks = Hooks(swing_market_value=lambda: 85_000.0)
    rig = new(hooks=hooks)
    rig.alpaca.account.update(equity=50_000.0, buying_power=100_000.0)
    rig.alpaca.price.update(NVDA=100.0, IREN=50.0, HUT=20.0)
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "100")]
    hut = rig.night("HUT")
    assert hut["state"] == SKIPPED and hut["reason"] == osch.NO_ROOM and hut["wanted_qty"] == 500
    assert rig.night("IREN")["shrunk"]["wanted"] == 200 and rig.night("IREN")["shrunk"]["qty"] == 100
    assert sum(1 for r in rig.ctl.state["log"] if r["event"] == "X11_SHRUNK" and r["symbol"] == "IREN") >= 1


def test_each_order_fits_buying_power_left_after_the_others():
    rig = new()
    rig.alpaca.account.update(equity=50_000.0, buying_power=12_000.0)
    rig.alpaca.price.update(NVDA=100.0, IREN=50.0, HUT=20.0)
    rig.run(T(THU, 15, 44), T(THU, 15, 46, 6))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "40")]


def test_a_pending_earlier_stock_keeps_its_room():
    hooks = Hooks(swing_market_value=lambda: 85_000.0)
    fail = {"NVDA": True}

    def bars(sym, d):
        if fail.get(sym):
            raise RuntimeError("relay down")
        return 375, True

    rig = new(hooks=hooks, bars=bars)
    rig.alpaca.account.update(equity=50_000.0, buying_power=100_000.0)
    rig.alpaca.price.update(NVDA=100.0, IREN=50.0, HUT=20.0)
    rig.run(T(THU, 15, 44), T(THU, 15, 46, 10))
    # IREN sized while NVDA was still pending: NVDA's 20% ($10,000) stayed reserved
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("IREN", "100")]
    fail["NVDA"] = False
    rig.run(T(THU, 15, 46, 11), T(THU, 15, 50))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("IREN", "100"), ("NVDA", "100")]


# Reg T margin room: equity 50,000, Slow trades 76,000 (38,000 of margin at 50%), so 12,000 of
# margin is left for tonight. NVDA 100 x 100 uses 5,000 at 50%, leaving 7,000 for IREN.
MARGIN_SETUP = dict(equity=50_000.0, buying_power=100_000.0, regt_buying_power=100_000.0)


def _margin_rig(**kw):
    rig = new(hooks=Hooks(swing_market_value=lambda: 76_000.0), **kw)
    rig.alpaca.account.update(MARGIN_SETUP)
    rig.alpaca.price.update(NVDA=100.0, IREN=50.0, HUT=20.0)
    return rig


def test_margin_room_buys_full_size_when_every_stock_needs_50pct():
    rig = _margin_rig()
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    # 5,000 + 5,000 of margin, 2,000 left for HUT: 4,000 of notional at 50%, 200 of its 500 shares
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "200"), ("HUT", "200")]
    assert rig.night("IREN")["shrunk"] is None and rig.night("IREN")["margin_ratio"] == 0.5


def test_a_stock_with_a_100pct_margin_requirement_shrinks():
    """IREN at margin_requirement_long 100 needs its full price overnight: the 7,000 of margin left
    after NVDA buys 7,000 of IREN (140 shares), not the 14,000 a 50% stock would get, and HUT, last
    in NVDA, IREN, HUT order, gets nothing."""
    rig = _margin_rig()
    rig.alpaca.assets["IREN"] = {"marginable": True, "margin_requirement_long": 1.0}
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "140")]
    ir = rig.night("IREN")
    assert ir["margin_ratio"] == 1.0 and ir["shrunk"]["wanted"] == 200 and ir["shrunk"]["qty"] == 140
    assert any(r["event"] == "X11_SHRUNK" and r["symbol"] == "IREN" for r in rig.ctl.state["log"])
    hut = rig.night("HUT")
    assert hut["state"] == SKIPPED and hut["reason"] == osch.NO_ROOM
    assert rig.ctl.state["margin"]["IREN"]["margin_requirement_long"] == 1.0
    assert rig.night("NVDA")["needs_look"] == [] and ir["needs_look"] == []


def test_a_stock_alpaca_does_not_lend_on_counts_at_its_full_price():
    rig = _margin_rig()
    rig.alpaca.assets["IREN"] = {"marginable": False, "margin_requirement_long": None}
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "140")]
    assert rig.night("IREN")["margin_ratio"] == 1.0


def test_a_requirement_below_regt_still_counts_at_50pct():
    rig = _margin_rig()
    rig.alpaca.assets["NVDA"] = {"marginable": True, "margin_requirement_long": 0.30}
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert rig.night("NVDA")["margin_ratio"] == 0.5
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "200"), ("HUT", "200")]


def test_a_failed_margin_read_uses_the_last_good_read_from_the_checkpoint():
    first = _margin_rig()
    first.alpaca.assets["IREN"] = {"marginable": True, "margin_requirement_long": 1.0}
    first.run(T(THU, 15, 44), T(THU, 15, 46, 6))
    saved = first.ctl.to_json()["margin"]
    assert saved["IREN"]["margin_requirement_long"] == 1.0
    # a new process on a new day with only the margin cache restored, and Alpaca's asset read down
    rig = _margin_rig(state=json.loads(json.dumps({"version": 1, "margin": saved})))
    rig.clock.now = T(FRI, 15, 44)
    rig.alpaca.asset_error = {"NVDA", "IREN", "HUT"}
    rig.run(T(FRI, 15, 44), T(FRI, 15, 50))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "140")]
    ir = rig.night("IREN", FRI)
    assert ir["margin_ratio"] == 1.0 and osch.MARGIN_UNKNOWN not in ir["needs_look"]
    assert any(r["event"] == "MARGIN_FROM_LAST_READ" and r["symbol"] == "IREN" for r in rig.ctl.state["log"])


def test_a_stock_never_read_counts_at_50pct_and_needs_a_look():
    rig = _margin_rig()
    rig.alpaca.asset_error = {"IREN"}
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "200"), ("HUT", "200")]
    ir = rig.night("IREN")
    assert ir["margin_ratio"] == 0.5 and ir["needs_look"] == [osch.MARGIN_UNKNOWN]
    assert rig.night("NVDA")["needs_look"] == [] and rig.night("HUT")["needs_look"] == []
    assert any("margin unknown, assumed 50%" in a[1] for a in rig.alerts if a[2]["symbol"] == "IREN")


def test_buying_power_below_the_size_still_shrinks_then_refuses_whatever_the_margin():
    """Margin room is plenty here; Alpaca buying power (and regt) is 12,000. The existing per order
    check still binds: NVDA full, IREN shrinks to what is left, HUT gets nothing and is skipped."""
    rig = new()
    rig.alpaca.account.update(equity=50_000.0, buying_power=12_000.0, regt_buying_power=12_000.0)
    rig.alpaca.price.update(NVDA=100.0, IREN=50.0, HUT=20.0)
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert [(b["symbol"], b["qty"]) for _, b in rig.alpaca.buys()] == [("NVDA", "100"), ("IREN", "40")]
    assert rig.night("IREN")["shrunk"]["buying_power_left"] == 2_000.0
    hut = rig.night("HUT")
    assert hut["state"] == SKIPPED and hut["reason"] == osch.NO_ROOM


def test_relay_outage_alerts_at_1547_30_and_skips_at_1549_30():
    rig = new(bars=lambda s, d: (_ for _ in ()).throw(RuntimeError("relay down")))
    rig.run(T(THU, 15, 44), T(THU, 15, 47, 29))
    assert rig.alerts == []
    rig.run(T(THU, 15, 47, 30), T(THU, 15, 50))
    assert len([a for a in rig.alerts if a[2]["reason"] == osch.NO_ORDER_BY_1547]) == 3
    assert [rig.night(s)["reason"] for s in osch.SYMBOLS] == [osch.RELAY_UNAVAILABLE] * 3
    assert rig.alpaca.posts == [] and not any(rig.ctl.reserves(s) for s in osch.SYMBOLS)


def test_short_data_day_is_skipped_and_the_rule_boundary_holds():
    rig = new(bars=lambda s, d: {"NVDA": (296, True), "IREN": (297, True), "HUT": (375, False)}[s])
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert rig.night("NVDA")["reason"] == osch.DATA_SHORT
    assert rig.night("IREN")["state"] == BUY_ACCEPTED
    assert rig.night("HUT")["reason"] == osch.NO_0930_BAR


def test_day_trade_is_closed_first_and_the_buy_waits_for_alpaca_flat():
    calls = []
    held = {"NVDA": 10}

    def close(sym):
        calls.append(("close", sym))
        held[sym] = 0

    rig = new(hooks=Hooks(day_shares=lambda s: held.get(s, 0), close_day_trade=close,
                          cancel_day_entries=lambda s: calls.append(("cancel", s))))
    rig.alpaca.positions["NVDA"] = 10
    rig.run(T(THU, 15, 44), T(THU, 15, 46, 20))
    assert ("close", "NVDA") in calls and calls.count(("close", "NVDA")) == 1
    assert ("cancel", "NVDA") in calls
    assert rig.alpaca.buys("NVDA") == [] and rig.night("NVDA")["block"] == osch.BROKER_NOT_FLAT
    rig.alpaca.positions.pop("NVDA")                       # Alpaca shows the day trade closed
    rig.run(T(THU, 15, 46, 21), T(THU, 15, 46, 40))
    assert len(rig.alpaca.buys("NVDA")) == 1
    assert any(r["event"] == "X6_DAY_TRADE_CLOSED_EARLY" for r in rig.ctl.state["log"])


def test_stock_held_by_orb_and_mismatch_are_skipped():
    rig = new(hooks=Hooks(held_by_other=lambda s: "ORB" if s == "IREN" else None))
    rig.run(T(THU, 15, 44), T(THU, 15, 50))
    assert rig.night("IREN")["reason"] == osch.HELD_BY_OTHER_STRATEGY
    rig2 = new(hooks=Hooks(broker_mismatch=lambda: True))
    rig2.run(T(THU, 15, 44), T(THU, 15, 50))
    assert [rig2.night(s)["reason"] for s in osch.SYMBOLS] == [osch.BROKER_MISMATCH] * 3
    assert rig2.alpaca.posts == []


def test_early_close_holiday_and_uncovered_year():
    early = date(2026, 11, 27)
    rig = new(d=early)
    rig.run(T(early, 15, 44), T(early, 15, 50))
    assert [rig.night(s, early)["reason"] for s in osch.SYMBOLS] == [osch.EARLY_CLOSE] * 3
    assert rig.alpaca.posts == [] and not any(rig.ctl.reserves(s) for s in osch.SYMBOLS)
    hol = date(2026, 11, 26)
    rig.run(T(hol, 15, 44), T(hol, 15, 50), step=5)
    assert not any(k.endswith(hol.isoformat()) for k in rig.ctl.state["nights"])
    y2028 = date(2028, 1, 3)
    rig.run(T(y2028, 15, 44), T(y2028, 15, 50), step=5)
    assert [rig.night(s, y2028)["reason"] for s in osch.SYMBOLS] == [osch.CALENDAR_NOT_COVERED] * 3
    assert rig.alpaca.posts == [] and any(a[2]["reason"] == osch.CALENDAR_NOT_COVERED for a in rig.alerts)


def test_yesterdays_unsold_hold_blocks_tonights_buy_in_that_stock():
    rig = new()
    buy_day(rig)
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)
    rig.alpaca.halted = {"HUT"}                            # HUT cannot sell on Friday
    overnight(rig)
    rig.run(T(FRI, 9, 33), T(FRI, 15, 50), step=30)
    assert rig.night("HUT", FRI)["reason"] == osch.EARLIER_HOLD_UNSOLD
    assert rig.night("NVDA", FRI)["state"] == BUY_ACCEPTED


# ============================================================ control and mode
def test_no_buy_tonight_before_1549_30_cancels_accepted_buys_and_is_saved_first():
    rig = new()
    rig.run(T(THU, 15, 44), T(THU, 15, 48))
    rig.save_ok = False
    assert rig.ctl.set_no_buy_tonight(T(THU, 15, 48))[0] is False      # not durable, not applied
    assert rig.ctl.state["control"]["no_buy_date"] is None
    rig.save_ok = True
    ok, text = rig.ctl.set_no_buy_tonight(T(THU, 15, 48))
    assert ok and "still sell" in text
    rig.run(T(THU, 15, 48), T(THU, 15, 59))
    rig.alpaca.clock.now = T(THU, 16, 0)
    rig.alpaca.close_auction()
    rig.run(T(THU, 16, 0), T(THU, 16, 5))
    assert [rig.night(s)["reason"] for s in osch.SYMBOLS] == [osch.OPERATOR_NO_BUY_TONIGHT] * 3
    assert len(rig.alpaca.cancels) == 3 and rig.alpaca.positions == {} and rig.booked == []
    assert rig.ctl.set_no_buy_tonight(T(THU, 15, 49, 30))[0] is False


def test_control_is_kept_across_a_restart_and_only_for_its_date_and_never_touches_a_sale():
    rig = new()
    buy_day(rig)
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)
    assert rig.ctl.set_no_buy_tonight(T(FRI, 9, 0))[0]
    rig = rig.restart()
    assert rig.ctl.state["control"]["no_buy_date"] == FRI.isoformat()
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 31))
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3          # sales untouched
    rig.run(T(FRI, 15, 44), T(FRI, 15, 50))
    assert [rig.night(s, FRI)["reason"] for s in osch.SYMBOLS] == [osch.OPERATOR_NO_BUY_TONIGHT] * 3
    rig.run(T(MON, 15, 44), T(MON, 15, 47))
    assert len(rig.alpaca.buys()) == 6                                     # Monday buys again


def test_mode_off_stops_new_buys_never_sales_and_no_broker_sends_nothing():
    rig = new()
    buy_day(rig)
    off = rig.restart(mode="off")
    overnight(off)
    assert [off.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3
    off.run(T(FRI, 15, 44), T(FRI, 15, 50))
    assert [off.night(s, FRI)["reason"] for s in osch.SYMBOLS] == [osch.MODE_OFF] * 3
    assert not any(off.ctl.reserves(s) for s in osch.SYMBOLS)
    assert all(t.date() == THU for t, _ in off.alpaca.buys())
    held = new()
    buy_day(held)
    nob = held.restart(broker=None)
    calls = held.alpaca.calls
    nob.run(T(THU, 19, 0), T(THU, 19, 2))
    assert held.alpaca.calls == calls and held.alpaca.sells() == []
    assert all(osch.NO_BROKER in nob.night(s)["needs_look"] for s in osch.SYMBOLS)
    nob.run(T(FRI, 15, 44), T(FRI, 15, 47))
    assert [nob.night(s, FRI)["reason"] for s in osch.SYMBOLS] == [osch.NO_BROKER] * 3


def test_disabled_stock_is_skipped_and_not_reserved():
    rig = new(enabled=("NVDA", "HUT"))
    rig.run(T(THU, 15, 44), T(THU, 15, 47))
    assert rig.night("IREN")["reason"] == osch.STOCK_OFF and not rig.ctl.reserves("IREN")
    assert [b["symbol"] for _, b in rig.alpaca.buys()] == ["NVDA", "HUT"]


# ============================================================================ T4
def test_restart_down_1540_to_1552_sends_nothing_and_logs_the_miss():
    rig = new()
    rig.run(T(THU, 15, 39), T(THU, 15, 40))
    rig = rig.restart()
    rig.run(T(THU, 15, 52), T(THU, 16, 10), step=5)
    assert rig.alpaca.posts == []
    assert [rig.night(s)["reason"] for s in osch.SYMBOLS] == [osch.MISSED_BUY_WINDOW] * 3


def test_restart_at_1545_with_the_control_already_used_keeps_it():
    rig = new()
    rig.clock.now = T(THU, 15, 40)
    assert rig.ctl.set_no_buy_tonight(T(THU, 15, 40))[0]
    rig = rig.restart()
    rig.run(T(THU, 15, 45), T(THU, 16, 5))
    assert rig.alpaca.posts == []
    assert [rig.night(s)["reason"] for s in osch.SYMBOLS] == [osch.OPERATOR_NO_BUY_TONIGHT] * 3


def test_restart_after_the_intent_was_saved_but_before_the_send():
    rig = new()
    rig.alpaca.fail_posts = [("transport", False)] * 3        # the POSTs never reached Alpaca
    rig.run(T(THU, 15, 44), T(THU, 15, 46, 5))
    intent_state = [s for r, s in rig.saves if r == "OVERNIGHT_BUY_INTENT"][-1]
    rig = rig.restart(state=intent_state)
    rig.run(T(THU, 15, 47), T(THU, 15, 50))
    for s in osch.SYMBOLS:
        cids = [b["client_order_id"] for _, b in rig.alpaca.buys(s)]
        assert set(cids) == {f"adt-ovn-{s}-20261001-buy-1"}      # same id, never a second order
        assert len([o for o in rig.alpaca.orders.values() if o["symbol"] == s]) == 1


def test_restart_at_1602_books_the_unbooked_fill_once():
    rig = new()
    rig.run(T(THU, 15, 44), T(THU, 15, 48))
    snap = rig.ctl.to_json()
    rig.alpaca.clock.now = T(THU, 16, 0)
    rig.alpaca.close_auction()
    rig = rig.restart(state=snap)
    rig.clock.now = T(THU, 16, 2)
    rig.ctl.reconcile(T(THU, 16, 2))                          # before the first position compare
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [HELD] * 3
    assert sorted((e["symbol"], e["qty"]) for e in rig.booked) == [("HUT", 198), ("IREN", 248), ("NVDA", 55)]
    rig.run(T(THU, 16, 2), T(THU, 16, 10), step=5)
    assert len(rig.booked) == 3


def test_restart_after_being_down_at_1900_queues_the_sale_at_once():
    rig = new()
    buy_day(rig)
    rig = rig.restart()
    rig.at(T(THU, 21, 0))
    assert [(t.time(), b["time_in_force"]) for t, b in rig.alpaca.sells()] == [(time(21, 0), "opg")] * 3


def test_restart_at_0910_sends_the_missing_sale_before_0927_30():
    rig = new()
    buy_day(rig)
    rig = rig.restart()
    rig.at(T(FRI, 9, 10))
    assert [(t.time(), b["time_in_force"]) for t, b in rig.alpaca.sells()] == [(time(9, 10), "opg")] * 3
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 31))
    assert [rig.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3


def test_restart_at_0933_after_being_down_since_the_close_sells_at_market():
    rig = new()
    buy_day(rig)
    a = rig.restart()                                         # down 16:10 to 09:33: no sale was queued
    a.clock.now = T(FRI, 9, 33)
    a.ctl.reconcile(T(FRI, 9, 33))
    a.run(T(FRI, 9, 33), T(FRI, 9, 34))
    assert [(t.time(), b["time_in_force"], b["client_order_id"]) for t, b in a.alpaca.sells()] == [
        (time(9, 33), "day", f"adt-ovn-{s}-20261001-sell-1") for s in osch.SYMBOLS]
    assert [a.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3
    assert a.alpaca.positions == {} and not any(a.ctl.reserves(s) for s in osch.SYMBOLS)


def test_restart_at_0933_books_the_open_that_filled_while_down():
    rig = new()
    buy_day(rig)
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)
    snap = rig.ctl.to_json()
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction(px={"NVDA": 190.0, "IREN": 40.0, "HUT": 50.0})
    b = rig.restart(state=snap)
    b.clock.now = T(FRI, 9, 33)
    b.ctl.reconcile(T(FRI, 9, 33))
    assert [b.night(s)["state"] for s in osch.SYMBOLS] == [SOLD] * 3
    assert b.ctl.overnight_realized_today(FRI) == pytest.approx(55 * 10.0)
    b.run(T(FRI, 9, 33), T(FRI, 9, 34))
    assert len(b.alpaca.sells()) == 3 and not any(b.ctl.reserves(s) for s in osch.SYMBOLS)


def test_serialize_restore_at_every_tick_gives_the_same_night():
    def full(roundtrip):
        rig = new()
        box = {"rig": rig}

        def each(r):
            if roundtrip:
                box["rig"] = r.restart()

        # run with a restart after every tick (the inline executor leaves nothing in flight)
        for start, end, step in ((T(THU, 15, 44), T(THU, 15, 59, 59), 1), (T(THU, 16, 0), T(THU, 16, 10), 5),
                                 (T(THU, 19, 0), T(THU, 19, 2), 5), (T(FRI, 9, 0), T(FRI, 9, 1), 30),
                                 (T(FRI, 9, 30), T(FRI, 9, 33), 5)):
            if start == T(THU, 16, 0):
                box["rig"].alpaca.clock.now = start
                box["rig"].alpaca.close_auction()
            if start == T(FRI, 9, 30):
                box["rig"].alpaca.clock.now = start
                box["rig"].alpaca.open_auction(px={"NVDA": 182.0, "IREN": 41.0, "HUT": 49.0})
            t = start
            while t <= end:
                r = box["rig"]
                r.clock.now = t
                r.ctl.tick(t)
                each(r)
                t += timedelta(seconds=step)
        r = box["rig"]
        return ([(b["symbol"], b["qty"], b["time_in_force"], b["client_order_id"]) for _, b in r.alpaca.posts],
                [r.night(s)["state"] for s in osch.SYMBOLS], r.ctl.overnight_realized_today(FRI))

    assert full(True) == full(False)
    posts, states, realized = full(True)
    assert len(posts) == 6 and states == [SOLD] * 3 and realized == pytest.approx(55 * 2.0 + 248 * 1.0 - 198 * 1.0)


def test_state_round_trip_and_version_guard():
    rig = new()
    buy_day(rig)
    data = rig.ctl.to_json()
    again = rig.restart(state=data)
    assert again.ctl.to_json() == data
    empty = OvernightController.from_json(None, broker=None, executor=InlineExecutor(), calendar=PROD,
                                          clock=rig.clock, book=lambda e: True, checkpoint=lambda r: True,
                                          bar_count=lambda s, d: (375, True), last_price=lambda s: 1.0)
    assert empty.state["nights"] == {}
    with pytest.raises(ValueError):
        OvernightController.from_json({"version": 99}, broker=None, executor=InlineExecutor(), calendar=PROD,
                                      clock=rig.clock, book=lambda e: True, checkpoint=lambda r: True,
                                      bar_count=lambda s, d: (375, True), last_price=lambda s: 1.0)


# ============================================================================ T9
def _to_morning(rig):
    buy_day(rig)
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)


def test_split_10_for_1_adjusts_the_hold_and_replaces_the_sale():
    rig = new()
    _to_morning(rig)
    rig.alpaca.positions["NVDA"] = 550
    rig.alpaca.corporate_actions = [{"kind": "split", "old_symbol": "NVDA", "new_symbol": "NVDA", "ratio": 10.0,
                                     "ex_date": FRI.isoformat()}]
    rig.run(T(FRI, 9, 0), T(FRI, 9, 1))
    nv = rig.night("NVDA")
    assert nv["held_qty"] == 550 and nv["buy_avg"] == pytest.approx(18.0) and nv["needs_look"] == []
    assert [(b["qty"], b["client_order_id"]) for _, b in rig.alpaca.sells("NVDA")] == [
        ("55", "adt-ovn-NVDA-20261001-sell-1"), ("550", "adt-ovn-NVDA-20261001-sell-2")]
    assert all(t.time() < time(9, 27, 30) for t, _ in rig.alpaca.sells("NVDA") if t.date() == FRI)
    assert any(e.get("kind") == "split" and e["ratio"] == 10.0 for e in rig.booked)
    assert any("split 10 for 1" in r.get("text", "") for r in rig.ctl.state["log"])
    rig.alpaca.price["NVDA"] = 18.5
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 31))
    assert nv["state"] == SOLD and nv["realized"] == pytest.approx(550 * 0.5) and not rig.ctl.reserves("NVDA")


def test_confirmed_symbol_change_converts_the_hold_before_selling_the_new_symbol():
    """IREN becomes IRNX, one new share for two old. The hold is converted first (248 IREN at 40
    become 124 IRNX at 80, same strategy), the old queued sale is cancelled, then IRNX is sold and
    booked as the hold (with its result), and the reservation moves to IRNX."""
    rig = new()
    _to_morning(rig)
    rig.alpaca.positions.pop("IREN")
    rig.alpaca.positions["IRNX"] = 124
    rig.alpaca.price["IRNX"] = 82.0
    rig.alpaca.corporate_actions = [{"kind": "reverse_split", "old_symbol": "IREN", "new_symbol": "IRNX",
                                     "ratio": 0.5, "ex_date": FRI.isoformat()}]
    booked_when_sent = []
    real_post = rig.alpaca.submit_on_auction

    def spy(symbol, side, qty, client_order_id, tif):
        if symbol == "IRNX":
            booked_when_sent.append([e.get("kind") for e in rig.booked])
        return real_post(symbol, side, qty, client_order_id, tif)

    rig.alpaca.submit_on_auction = spy
    rig.run(T(FRI, 9, 0), T(FRI, 9, 1))
    ir = rig.night("IREN")
    change = [e for e in rig.booked if e.get("kind") == "symbol_change"]
    assert change == [{"kind": "symbol_change", "strategy_id": "overnight_iren", "symbol": "IREN", "new_symbol": "IRNX",
                       "buy_date": THU.isoformat(), "ratio": 0.5, "old_qty": 248, "new_qty": 124, "old_avg": 40.0,
                       "new_avg": 80.0}]
    # the conversion was booked before the IRNX sale was sent
    assert len(booked_when_sent) == 1 and "symbol_change" in booked_when_sent[0]
    assert ir["needs_look"] == [] and ir["held_qty"] == 124 and ir["buy_avg"] == pytest.approx(80.0)
    assert any("IREN became IRNX overnight" in r.get("text", "") for r in rig.ctl.state["log"])
    assert [(b["symbol"], b["qty"], b["client_order_id"]) for _, b in rig.alpaca.sells() if b["symbol"] in ("IREN", "IRNX")] == [
        ("IREN", "248", "adt-ovn-IREN-20261001-sell-1"), ("IRNX", "124", "adt-ovn-IRNX-20261001-sell-1")]
    assert ir["legs"][0]["attempts"][0]["id"] in rig.alpaca.cancels
    assert rig.ctl.reserves("IRNX") and not rig.ctl.reserves("IREN") and rig.ctl.sale_due("IRNX") is not None
    assert rig.ctl.locks_all("IRNX") and not rig.ctl.locks_all("IREN")
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 32))
    sale = [e for e in rig.booked if e.get("role") == "sell" and e["symbol"] == "IRNX"]
    assert sale and sale[0]["hold_symbol"] == "IRNX" and sale[0]["stock"] == "IREN"
    assert sale[0]["realized"] == pytest.approx(124 * 2.0)
    assert ir["state"] == SOLD and ir["realized"] == pytest.approx(248.0) and ir["released"]
    assert rig.alpaca.positions.get("IRNX", 0) == 0 and "IREN" not in rig.alpaca.positions
    assert not rig.ctl.reserves("IRNX") and not rig.ctl.reserves("IREN")


UNCLEAR_CHANGES = {
    # a 1 for 10 reverse split pays cash for the fraction: 24.8 owed, Alpaca shows 24
    "fraction": ([{"kind": "reverse_split", "old_symbol": "IREN", "new_symbol": "IRNX", "ratio": 0.1}], {"IRNX": 24}),
    "no ratio": ([{"kind": "reverse_split", "old_symbol": "IREN", "new_symbol": "IRNX", "ratio": None}], {"IRNX": 124}),
    "no new symbol": ([{"kind": "merger", "old_symbol": "IREN", "new_symbol": "IREN", "ratio": 0.5}], {"IRNX": 124}),
    "shares do not match": ([{"kind": "merger", "old_symbol": "IREN", "new_symbol": "IRNX", "ratio": 0.5}], {"IRNX": 100}),
    "old shares still there": ([{"kind": "merger", "old_symbol": "IREN", "new_symbol": "IRNX", "ratio": 0.5}],
                               {"IREN": 100, "IRNX": 74}),
}


@pytest.mark.parametrize("case", sorted(UNCLEAR_CHANGES))
def test_unclear_symbol_change_sells_nothing_and_is_never_released(case):
    actions, held = UNCLEAR_CHANGES[case]
    rig = new()
    _to_morning(rig)
    rig.alpaca.positions.pop("IREN")
    rig.alpaca.positions.update(held)
    rig.alpaca.price["IRNX"] = 82.0
    rig.alpaca.corporate_actions = [dict(a, ex_date=FRI.isoformat()) for a in actions]
    rig.run(T(FRI, 9, 0), T(FRI, 9, 1))
    ir = rig.night("IREN")
    assert ir["needs_look"] == [osch.SYMBOL_CHANGE_UNCLEAR] and ir["frozen"] == osch.SYMBOL_CHANGE_UNCLEAR
    text = next(a[1] for a in rig.alerts if a[2].get("reason") == osch.SYMBOL_CHANGE_UNCLEAR)
    assert "Nothing was sold" in text and "sell the shares there by hand" in text
    assert ir["legs"][0]["attempts"][0]["id"] in rig.alpaca.cancels          # the queued IREN sale is cancelled
    assert not any(e.get("kind") == "symbol_change" for e in rig.booked)
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 40), step=5)
    sells_fri = [b for t, b in rig.alpaca.sells() if t.date() == FRI and b["symbol"] in ("IREN", "IRNX")]
    assert sells_fri == []                                                    # no automatic sale at all
    assert all(rig.alpaca.positions.get(s) == q for s, q in held.items())
    assert ir["state"] == HELD and not ir["released"] and ir["legs"][0]["sold"] == 0
    assert rig.ctl.reserves("IREN") and rig.ctl.locks_all("IREN")
    assert rig.ctl.unsold_after_0931(T(FRI, 9, 40)) == ["IREN"]
    assert not any(e.get("role") == "sell" and e["hold_symbol"] == "IREN" for e in rig.booked)
    assert len(ir["legs"][0]["attempts"]) == 1                                # no new sale attempt is even made


def test_unclear_symbol_change_never_resends_a_sale_whose_post_was_lost():
    """The 19:00 sale's POST never got an answer (no Alpaca id). Once the change is unclear the id
    is only looked up at 09:00, never sent again."""
    rig = new()
    buy_day(rig)
    rig.alpaca.fail_posts = [("transport", False)] * 40
    rig.run(T(THU, 19, 0), T(THU, 19, 1), step=5)
    rig.alpaca.fail_posts = []
    att = rig.night("IREN")["legs"][0]["attempts"][-1]
    assert att["id"] is None and not att["final"]
    rig.alpaca.positions.pop("IREN")
    rig.alpaca.positions["IRNX"] = 24
    rig.alpaca.corporate_actions = [{"kind": "reverse_split", "old_symbol": "IREN", "new_symbol": "IRNX", "ratio": 0.1,
                                     "ex_date": FRI.isoformat()}]
    rig.run(T(FRI, 9, 0), T(FRI, 9, 35), step=5)
    ir = rig.night("IREN")
    assert ir["frozen"] == osch.SYMBOL_CHANGE_UNCLEAR
    assert [b for t, b in rig.alpaca.sells() if t.date() == FRI and b["symbol"] in ("IREN", "IRNX")] == []
    assert ir["state"] == HELD and not ir["released"]


def test_a_symbol_change_the_book_refuses_sells_nothing():
    rig = new()
    _to_morning(rig)
    rig.alpaca.positions.pop("IREN")
    rig.alpaca.positions["IRNX"] = 124
    rig.alpaca.price["IRNX"] = 82.0
    rig.alpaca.corporate_actions = [{"kind": "merger", "old_symbol": "IREN", "new_symbol": "IRNX", "ratio": 0.5,
                                     "ex_date": FRI.isoformat()}]
    rig.ctl.book = lambda e: False if e.get("kind") == "symbol_change" else True
    rig.run(T(FRI, 9, 0), T(FRI, 9, 35))
    ir = rig.night("IREN")
    assert ir["frozen"] == osch.BOOKING_REFUSED and osch.BOOKING_REFUSED in ir["needs_look"]
    assert [b for t, b in rig.alpaca.sells("IRNX")] == [] and ir["hold_symbol"] is None
    assert ir["state"] == HELD and not ir["released"] and rig.alpaca.positions["IRNX"] == 124


def test_split_with_a_missing_ratio_needs_a_look_and_never_sells_more_than_the_hold():
    rig = new()
    _to_morning(rig)
    rig.alpaca.positions["HUT"] = 792
    rig.alpaca.corporate_actions = [{"kind": "split", "old_symbol": "HUT", "new_symbol": "HUT", "ratio": None,
                                     "ex_date": FRI.isoformat()}]
    rig.run(T(FRI, 9, 0), T(FRI, 9, 1))
    hut = rig.night("HUT")
    assert osch.CORPORATE_ACTION in hut["needs_look"] and hut["held_qty"] == 198
    assert [b["qty"] for _, b in rig.alpaca.sells("HUT")] == ["198"]
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 32))
    assert hut["state"] == SOLD and rig.alpaca.positions["HUT"] == 594
    assert rig.ctl.reserves("HUT") and osch.SHARES_UNEXPLAINED in hut["needs_look"]


def test_corporate_actions_unreadable_until_0927_30_sells_min_of_hold_and_alpaca():
    rig = new()
    _to_morning(rig)
    rig.alpaca.positions["NVDA"] = 40          # fewer shares than the hold, nothing explains it
    rig.alpaca.ca_error = True
    rig.run(T(FRI, 9, 0), T(FRI, 9, 27, 29), step=30)
    assert rig.night("NVDA")["block"] == osch.CORPORATE_ACTIONS_UNAVAILABLE
    rig.run(T(FRI, 9, 27, 30), T(FRI, 9, 28), step=30)
    nv = rig.night("NVDA")
    assert osch.CORPORATE_ACTIONS_UNAVAILABLE in nv["needs_look"] and nv["legs"][0]["qty"] == 40
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 33))
    assert nv["state"] == SOLD and nv["legs"][0]["sold"] == 40
    assert [b["qty"] for _, b in rig.alpaca.sells("NVDA")][-1] == "40"


# ============================================== ADT's book holds more than Alpaca (no action)
def _adt_book(rig):
    """ADT's book as the booked fills leave it: overnight shares per symbol."""
    out = {}
    for e in rig.booked:
        if e.get("kind") == "fill":
            out[e["symbol"]] = out.get(e["symbol"], 0) + (e["qty"] if e["role"] == "buy" else -e["qty"])
    return out


@pytest.mark.parametrize("book_source", ["adt_book_hook", "controller_count"])
def test_alpaca_short_of_the_book_sells_what_alpaca_holds_and_keeps_the_night_unreleased(book_source):
    """The NVDA hold is 55 shares in ADT's book. At 09:00 Alpaca shows 40 and no corporate action
    explains it. R2: the sale still runs, for the 40 Alpaca holds (never more), and is booked. The
    15 left only in ADT's book must keep NVDA unreleased and reserved with a plain needs look, keep
    the unsold banner, and stop Friday's NVDA buy, while the difference stands. Before the fix the
    night was released as soon as Alpaca was flat, and Friday bought NVDA on top of 15 shares that
    only ADT's book held under overnight_nvda."""
    rig = new()
    fixed = []
    if book_source == "adt_book_hook":
        rig.hooks.overnight_shares = lambda s: 0 if fixed else _adt_book(rig).get(s, 0)
    _to_morning(rig)
    rig.alpaca.positions["NVDA"] = 40
    rig.run(T(FRI, 9, 0), T(FRI, 9, 1))
    nv = rig.night("NVDA")
    assert [b["qty"] for _, b in rig.alpaca.sells("NVDA")][-1] == "40"
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 30), T(FRI, 9, 40), step=5)

    # sold and booked what Alpaca held, never more
    assert sum(e["qty"] for e in rig.booked if e.get("role") == "sell" and e["symbol"] == "NVDA") == 40
    assert "NVDA" not in rig.alpaca.positions and _adt_book(rig)["NVDA"] == 15
    # but not released while ADT's book holds the other 15
    assert nv["state"] == SOLD and not nv["released"]
    assert rig.ctl.reserves("NVDA") and rig.ctl.locks_all("NVDA")
    assert osch.BOOK_MORE_THAN_ALPACA in nv["needs_look"]
    text = next(a[1] for a in rig.alerts if a[2].get("reason") == osch.BOOK_MORE_THAN_ALPACA)
    assert "The robot's book shows 15 more NVDA shares than Alpaca" in text
    assert "Check the Alpaca app for a sale made by hand or a corporate action" in text
    assert rig.ctl.unsold_after_0931(T(FRI, 9, 40)) == ["NVDA"]
    # the other two sold in full and went back to the day strategies
    assert rig.night("IREN")["released"] and rig.night("HUT")["released"]

    # Friday: no NVDA buy while the difference stands, with the reason logged; IREN and HUT buy
    buy_day(rig, d=FRI)
    fri = rig.night("NVDA", FRI)
    assert fri["state"] == SKIPPED and fri["reason"] == osch.BOOK_MORE_THAN_ALPACA
    assert any(r["event"] == "SKIP" and r.get("symbol") == "NVDA" and r.get("buy_date") == FRI.isoformat()
               and r.get("reason") == osch.BOOK_MORE_THAN_ALPACA for r in rig.ctl.state["log"])
    assert [t.date() for t, _ in rig.alpaca.buys("NVDA")] == [THU]
    assert rig.night("IREN", FRI)["held_qty"] > 0 and rig.night("HUT", FRI)["held_qty"] > 0
    assert not nv["released"] and rig.ctl.reserves("NVDA")

    if book_source == "adt_book_hook":
        # the operator corrects ADT's book: the difference is gone, so the night is released
        fixed.append(True)
        rig.run(T(FRI, 16, 11), T(FRI, 16, 12), step=5)
        assert nv["released"]


def test_alpaca_short_at_the_0931_sale_says_so_and_blocks_the_next_buy():
    """The 09:00 check agreed (55), then the queued sale was cancelled by hand in the Alpaca app and
    15 shares sold there. The 09:31 market sale sells the 40 Alpaca holds. The night must not be
    released and must say plainly that the robot's book holds more than Alpaca."""
    rig = new()
    _to_morning(rig)
    rig.run(T(FRI, 9, 0), T(FRI, 9, 1))
    nv = rig.night("NVDA")
    assert nv["morning_checked"] and nv["needs_look"] == []
    queued = rig.alpaca.live("NVDA", "sell")
    assert len(queued) == 1
    rig.alpaca.clock.now = T(FRI, 9, 29)
    queued[0]["status"] = "canceled"
    rig.alpaca.positions["NVDA"] = 40
    rig.alpaca.clock.now = T(FRI, 9, 30)
    rig.alpaca.open_auction()
    rig.run(T(FRI, 9, 29), T(FRI, 9, 40), step=5)
    assert [b["qty"] for t, b in rig.alpaca.sells("NVDA") if t.date() == FRI and t.time() >= time(9, 31)] == ["40"]
    assert "NVDA" not in rig.alpaca.positions
    assert not nv["released"] and rig.ctl.reserves("NVDA")
    assert osch.BOOK_MORE_THAN_ALPACA in nv["needs_look"]
    assert rig.ctl.unsold_after_0931(T(FRI, 9, 40)) == ["NVDA"]
    buy_day(rig, d=FRI)
    assert rig.night("NVDA", FRI)["reason"] == osch.BOOK_MORE_THAN_ALPACA
    assert [t.date() for t, _ in rig.alpaca.buys("NVDA")] == [THU]


@pytest.mark.parametrize('settings,reason', [({'mode': 'off'}, osch.MODE_OFF),
                                             ({'enabled': ('IREN', 'HUT')}, osch.STOCK_OFF)])
def test_restored_unsent_buy_respects_disabled_settings(settings, reason):
    rig = new()
    rig.alpaca.fail_posts = [('transport', False)] * 3
    rig.at(T(THU, 15, 46, 5))
    saved = [s for why, s in rig.saves if why == 'OVERNIGHT_BUY_INTENT'][0]
    rig.alpaca.posts.clear()  # the failed transport attempts preceded the restart
    restored = rig.restart(state=saved, **settings)
    restored.at(T(THU, 15, 47))
    assert restored.alpaca.buys('NVDA') == []
    assert restored.night('NVDA')['reason'] == reason


@pytest.mark.parametrize('stop', ['cutoff', 'control', 'mode'])
def test_buy_rechecks_permission_after_slow_lookup(stop):
    rig = new()
    original = rig.alpaca.get_order_by_client_id

    def slow_lookup(cid, nested=True):
        row = original(cid, nested)
        if cid.startswith('adt-ovn-NVDA') and row is None:
            if stop == 'cutoff':
                rig.clock.now = T(THU, 15, 49, 30)
            elif stop == 'control':
                assert rig.ctl.set_no_buy_tonight(rig.clock.now)[0]
            else:
                rig.ctl.mode = 'off'
        return row

    rig.alpaca.get_order_by_client_id = slow_lookup
    rig.at(T(THU, 15, 46, 5))
    assert rig.alpaca.buys('NVDA') == []


@pytest.mark.parametrize('failure', ['raise', 'refuse'])
def test_split_booking_failure_never_changes_controller_or_replaces_sale(failure):
    rig = new()
    _to_morning(rig)
    rig.alpaca.positions['NVDA'] = 550
    rig.alpaca.corporate_actions = [{'kind': 'split', 'old_symbol': 'NVDA', 'new_symbol': 'NVDA',
                                     'ratio': 10.0, 'ex_date': FRI.isoformat()}]
    original = rig.ctl.book

    def fail_split(ev):
        if ev['kind'] == 'split':
            if failure == 'raise':
                raise RuntimeError('ledger unavailable')
            return False
        return original(ev)

    rig.ctl.book = fail_split
    rig.run(T(FRI, 9, 0), T(FRI, 9, 1))
    n = rig.night('NVDA')
    assert (n['held_qty'], n['buy_avg'], n['legs'][0]['qty']) == (55, 180.0, 55)
    assert n['splits'] == []
    assert n['frozen'] == osch.BOOKING_REFUSED
    assert osch.BOOKING_REFUSED in n['needs_look']
    assert rig.alpaca.live('NVDA', 'sell') == []
    assert len(rig.alpaca.sells('NVDA')) == 1
    assert rig.ctl.reserves('NVDA')
