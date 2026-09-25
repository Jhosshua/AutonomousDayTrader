"""Real-broker execution path, against a fake Alpaca (httpx.MockTransport, no network)."""
from datetime import datetime, timezone
import json

import httpx
import pytest

from backend.app.core.account import PaperTradingAccount
from backend.app.core.broker import AlpacaBroker, BrokerReject
from backend.app.core.engine import BrokerFillFailed, ExecutionEngine
from backend.app.models.events import OrderSide, OrderState, OrderType

NOW = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)


class FakeAlpaca:
    """Minimal Alpaca paper API with real position tracking.

    mode: fill | partial | hang (accepted, never fills, cancel works) |
          stuck (never fills, cancel never confirmed) | reject
    """

    def __init__(self, fill_price=100.0, mode="fill", fill_ratio=1.0):
        self.fill_price = fill_price
        self.mode = mode
        self.fill_ratio = fill_ratio
        self.orders = {}
        self.posts = []
        self.cancels = []
        self.positions = {}

    def _apply(self, order, qty):
        sign = 1 if order["side"] == "buy" else -1
        sym = order["symbol"]
        self.positions[sym] = self.positions.get(sym, 0) + sign * qty
        if self.positions[sym] == 0:
            del self.positions[sym]

    def fill_later(self, oid, qty=None):
        order = self.orders[oid]
        qty = int(order["qty"]) if qty is None else qty
        self._apply(order, qty - int(order["filled_qty"]))
        order.update(status="filled", filled_qty=str(qty), filled_avg_price=str(self.fill_price))

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v2/orders":
            body = json.loads(request.content)
            self.posts.append(body)
            if self.mode == "reject":
                return httpx.Response(403, json={"code": 40310000, "message": "insufficient qty"})
            if any(o["client_order_id"] == body["client_order_id"] for o in self.orders.values()):
                return httpx.Response(422, json={"message": "client_order_id must be unique"})
            oid = f"a{len(self.orders) + 1}"
            qty = int(body["qty"])
            order = dict(body, id=oid, status="new", filled_qty="0", filled_avg_price=None)
            self.orders[oid] = order
            if self.mode == "fill":
                self.fill_later(oid)
            elif self.mode == "partial":
                part = int(qty * self.fill_ratio)
                self._apply(order, part)
                order.update(status="partially_filled", filled_qty=str(part), filled_avg_price=str(self.fill_price))
            return httpx.Response(200, json=order)
        if request.method == "GET" and path == "/v2/orders:by_client_order_id":
            cid = request.url.params["client_order_id"]
            for o in self.orders.values():
                if o["client_order_id"] == cid:
                    return httpx.Response(200, json=o)
            return httpx.Response(404, json={})
        if request.method == "GET" and path.startswith("/v2/orders/"):
            return httpx.Response(200, json=self.orders[path.rsplit("/", 1)[1]])
        if request.method == "DELETE" and path.startswith("/v2/orders/"):
            oid = path.rsplit("/", 1)[1]
            self.cancels.append(oid)
            if self.mode != "stuck":
                self.orders[oid]["status"] = "canceled"
            return httpx.Response(204)
        if path == "/v2/account":
            return httpx.Response(200, json={"account_number": "PA3CSVDZMMPY", "equity": "50018.45", "cash": "50018.45"})
        if path == "/v2/positions":
            return httpx.Response(200, json=[{"symbol": s, "qty": str(q)} for s, q in self.positions.items()])
        if path.startswith("/v2/positions/"):
            sym = path.rsplit("/", 1)[1]
            if sym not in self.positions:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"symbol": sym, "qty": str(self.positions[sym])})
        return httpx.Response(404, json={})


def make_broker(fake: FakeAlpaca) -> AlpacaBroker:
    b = AlpacaBroker("k", "s", transport=httpx.MockTransport(fake.handler),
                     fill_wait_sec=0.02, cancel_wait_sec=0.02, poll_interval_sec=0.001)
    b._sleep = lambda _s: None
    return b


def make_engine(fake: FakeAlpaca, gate=None) -> ExecutionEngine:
    engine = ExecutionEngine(PaperTradingAccount(initial_cash=50000.0))
    engine.broker = make_broker(fake)
    engine.broker_gate = gate
    return engine


def buy(engine, sym="AAPL", qty=10, otype=OrderType.MARKET, **kw):
    order = engine.create_order(sym, OrderSide.BUY, otype, qty, **kw)
    engine.submit_order(order.id)
    return order


def test_refuses_live_trading_url():
    with pytest.raises(ValueError):
        AlpacaBroker("k", "s", base_url="https://api.alpaca.markets")


def test_market_fill_books_alpaca_price_and_no_fee():
    fake = FakeAlpaca(fill_price=101.37)
    engine = make_engine(fake)
    order = buy(engine)
    fills = engine.process_bar("AAPL", 100, 100, 100, 100, 100000, NOW)
    assert len(fills) == 1
    assert fills[0].price == 101.37 and fills[0].qty == 10 and fills[0].fee == 0.0
    assert engine.account.positions["AAPL"].avg_entry_price == 101.37
    assert fake.posts[0]["type"] == "market" and fake.posts[0]["side"] == "buy"
    assert fake.posts[0]["client_order_id"] == f"adt-{order.id}-1"
    assert order.broker_order_id is None, "a final Alpaca order is released"


def test_limit_order_goes_to_alpaca_as_limit():
    fake = FakeAlpaca(fill_price=99.5)
    engine = make_engine(fake)
    buy(engine, qty=5, otype=OrderType.LIMIT, limit_price=99.5)
    engine.process_quote("AAPL", 99.4, 99.45, NOW)
    assert fake.posts[0]["type"] == "limit" and fake.posts[0]["limit_price"] == "99.50"


def test_partial_fill_books_only_what_filled():
    fake = FakeAlpaca(mode="partial", fill_ratio=0.5)
    engine = make_engine(fake)
    order = buy(engine)
    fills = engine.process_quote("AAPL", 100.0, 100.02, NOW)
    assert fills[0].qty == 5
    assert order.status == OrderState.PARTIALLY_FILLED and order.remaining_qty == 5
    assert fake.cancels, "unfilled remainder must be cancelled at Alpaca"


def test_unconfirmed_cancel_is_resolved_before_any_new_order():
    """A late fill after a timed-out cancel is booked once; no second order is sent."""
    fake = FakeAlpaca(mode="stuck")
    engine = make_engine(fake)
    order = buy(engine)
    assert engine.process_quote("AAPL", 100.0, 100.02, NOW) == []
    assert order.broker_order_id == "a1" and order.id in engine.working_orders
    fake.fill_later("a1")          # the fill lands after we gave up waiting
    engine._broker_retry_after.clear()
    fills = engine.process_quote("AAPL", 100.0, 100.02, NOW)
    assert [f.qty for f in fills] == [10]
    assert len(fake.posts) == 1, "resolved the old Alpaca order instead of sending a new one"
    assert engine.account.positions["AAPL"].shares == 10 and fake.positions == {"AAPL": 10}


def test_exit_never_sells_more_than_alpaca_holds():
    fake = FakeAlpaca()
    engine = make_engine(fake)
    buy(engine)
    engine.process_quote("AAPL", 100.0, 100.02, NOW)
    fake.positions["AAPL"] = 4       # e.g. an earlier fill the bot never booked
    sell = engine.create_order("AAPL", OrderSide.SELL, OrderType.MARKET, 10)
    engine.submit_order(sell.id)
    fills = engine.process_quote("AAPL", 100.0, 100.02, NOW)
    assert fake.posts[-1]["qty"] == "4" and fills[0].qty == 4
    assert fake.positions == {}, "never flipped short"


def test_exit_refused_when_alpaca_already_flat():
    fake = FakeAlpaca()
    engine = make_engine(fake)
    buy(engine)
    engine.process_quote("AAPL", 100.0, 100.02, NOW)
    fake.positions.clear()
    sell = engine.create_order("AAPL", OrderSide.SELL, OrderType.MARKET, 10)
    engine.submit_order(sell.id)
    posts = len(fake.posts)
    assert engine.process_quote("AAPL", 100.0, 100.02, NOW) == []
    assert len(fake.posts) == posts and sell.id in engine.working_orders


def test_no_fill_leaves_exit_working_and_backs_off():
    fake = FakeAlpaca()
    engine = make_engine(fake)
    buy(engine)
    engine.process_quote("AAPL", 100.0, 100.02, NOW)
    fake.mode = "hang"
    stop = engine.create_order("AAPL", OrderSide.SELL, OrderType.STOP, 10, stop_price=99.0)
    engine.submit_order(stop.id)
    assert engine.process_quote("AAPL", 98.9, 98.95, NOW) == []
    assert stop.id in engine.working_orders, "an unfilled stop must stay armed"
    posts = len(fake.posts)
    engine.process_quote("AAPL", 98.8, 98.85, NOW)
    # A fresh order for the same symbol (breaker/flatten paths) also waits.
    other = engine.create_order("AAPL", OrderSide.SELL, OrderType.MARKET, 10)
    engine.submit_order(other.id)
    engine.process_quote("AAPL", 98.8, 98.85, NOW)
    assert len(fake.posts) == posts, "backoff per order and per symbol, no order storm"
    engine._broker_retry_after.clear()
    engine.cancel_order(other.id)
    fake.mode = "fill"
    fake.fill_price = 98.7
    fills = engine.process_quote("AAPL", 98.7, 98.75, NOW)
    assert fills and fills[0].price == 98.7 and "AAPL" not in engine.account.positions


def test_hard_reject_cancels_entry():
    fake = FakeAlpaca(mode="reject")
    engine = make_engine(fake)
    order = engine.create_order("TSLA", OrderSide.SELL, OrderType.MARKET, 10)
    engine.submit_order(order.id)
    assert engine.process_quote("TSLA", 200.0, 200.05, NOW) == []
    assert order.status == OrderState.CANCELLED and order.id not in engine.working_orders
    assert "TSLA" not in engine.account.positions


def test_gate_blocks_entries_and_delays_exits():
    fake = FakeAlpaca()
    engine = make_engine(fake, gate=lambda order, is_exit: ("MARKET_CLOSED", not is_exit, 60.0))
    entry = buy(engine)
    assert engine.process_quote("AAPL", 100.0, 100.02, NOW) == []
    assert entry.status == OrderState.CANCELLED and fake.posts == []


def test_direct_fill_failure_raises_and_cancels_order():
    fake = FakeAlpaca(mode="reject")
    engine = make_engine(fake)
    order = buy(engine, sym="MU")
    with pytest.raises(BrokerFillFailed):
        engine._execute_fill(order, 10, 100.0, 0.0, NOW)
    assert order.id not in engine.working_orders
    assert "MU" not in engine.account.positions


def test_same_client_id_never_creates_a_second_order():
    fake = FakeAlpaca(fill_price=50.0)
    broker = make_broker(fake)
    broker.submit("AAPL", "BUY", 3, "adt-x-1")
    again = broker.submit("AAPL", "BUY", 3, "adt-x-1")   # replay after a crash
    assert again["id"] == "a1" and len(fake.orders) == 1


def test_client_id_is_checkpointable_and_found_after_restart():
    """Order fields carry the Alpaca link; a restarted engine finds the in-flight order."""
    fake = FakeAlpaca(mode="stuck")
    engine = make_engine(fake)
    order = buy(engine)
    engine.process_quote("AAPL", 100.0, 100.02, NOW)
    order.broker_order_id = None          # pretend only the client id was saved
    fake.fill_later("a1")
    engine._broker_retry_after.clear()
    fills = engine.process_quote("AAPL", 100.0, 100.02, NOW)
    assert [f.qty for f in fills] == [10] and len(fake.posts) == 1


def test_reject_is_typed():
    with pytest.raises(BrokerReject):
        make_broker(FakeAlpaca(mode="reject")).submit("AAPL", "SELL", 1, "c1")


def test_sync_reads_signed_positions():
    fake = FakeAlpaca()
    fake.positions = {"AAPL": -10}
    st = make_broker(fake).sync()
    assert st.account_number == "PA3CSVDZMMPY" and st.positions == {"AAPL": -10} and st.equity == 50018.45


def test_simulator_unchanged_without_broker():
    engine = ExecutionEngine(PaperTradingAccount(initial_cash=50000.0))
    order = engine.create_order("AAPL", OrderSide.SELL, OrderType.MARKET, 10)
    engine.submit_order(order.id)
    fills = engine.process_quote("AAPL", 100.0, 100.02, NOW)
    assert fills and fills[0].fee > 0.0


# ----------------------------------------------------------------- app wiring
def test_mismatch_blocks_entries_on_every_route_but_not_exits():
    import backend.app.main as runtime
    runtime.reset_runtime_state()
    saved = runtime.engine.broker
    runtime.engine.broker = object()   # "a broker is attached"; nothing is sent here
    try:
        runtime._compare_with_broker({"NVDA": 5}, 50000.0)
        assert runtime.broker_state["mismatch"], "the first difference pauses entries"
        entry = runtime.engine.create_order("AAPL", OrderSide.BUY, OrderType.MARKET, 1, estimated_price=100.0)
        ok, reason = runtime.pre_trade_risk_validator(entry, runtime.account)
        assert not ok and reason.startswith("BROKER_MISMATCH")
        assert runtime._broker_gate(entry, False) is not None
        runtime._compare_with_broker({}, 50000.0)
        assert not runtime.broker_state["mismatch"], "clears once they match"
    finally:
        runtime.engine.broker = saved
        runtime.broker_state.update({"mismatch": False, "mismatch_streak": 0, "mismatch_detail": None})


def test_gate_refuses_real_orders_outside_regular_hours(monkeypatch):
    import backend.app.main as runtime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 25, 20, 30, tzinfo=timezone.utc).astimezone(tz)  # 16:30 ET

    monkeypatch.setattr(runtime, "datetime", Clock)
    order = runtime.engine.create_order("AAPL", OrderSide.SELL, OrderType.MARKET, 1)
    reason, hard, retry = runtime._broker_gate(order, True)
    assert reason.startswith("MARKET_CLOSED") and hard is False and retry == 60.0
    assert runtime._broker_gate(order, False)[1] is True, "entries are cancelled, not queued"

    class Open(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc).astimezone(tz)  # 10:00 ET

    monkeypatch.setattr(runtime, "datetime", Open)
    assert runtime._broker_gate(order, True) is None


def test_simulation_mode_detaches_the_real_broker():
    import backend.app.main as runtime
    saved = runtime.alpaca_broker
    runtime.alpaca_broker = object()
    try:
        runtime.set_simulation_mode(True)
        assert runtime.engine.broker is None
        runtime.set_simulation_mode(False)
        assert runtime.engine.broker is runtime.alpaca_broker
    finally:
        runtime.alpaca_broker = saved
        runtime.set_simulation_mode(False)
        runtime.engine.broker = None


def test_late_fill_on_locally_cancelled_order_is_booked_by_settle():
    fake = FakeAlpaca(mode="stuck")
    engine = make_engine(fake)
    order = buy(engine)
    engine.process_quote("AAPL", 100.0, 100.02, NOW)
    engine.cancel_order(order.id)            # e.g. EOD purge of the local entry
    fake.fill_later("a1")                    # Alpaca filled it anyway
    fills = engine.settle_broker_orders()
    assert [f.qty for f in fills] == [10]
    assert engine.account.positions["AAPL"].shares == 10, "ledger matches Alpaca"
    assert order.broker_order_id is None and len(fake.posts) == 1
    assert engine.settle_broker_orders() == [], "booked exactly once"


def test_rejected_order_is_never_sent_for_real():
    fake = FakeAlpaca()
    engine = make_engine(fake)
    engine.risk_validator = lambda order, acct: (False, "RISK")
    order = buy(engine)
    assert order.status == OrderState.REJECTED
    with pytest.raises(BrokerFillFailed):
        engine._execute_fill(order, 10, 100.0, 0.0, NOW)
    assert fake.posts == []
