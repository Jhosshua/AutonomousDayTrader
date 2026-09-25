"""Real AlpacaBroker HTTP adapter, deterministic transport, no external orders."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from backend.app.core.broker import AlpacaBroker
from backend.app.core.persistence import TradingStateStore, decode_runtime_value
from backend.app.models.events import QuoteEvent, OrderSide, OrderType
from backend.tests.unit.test_tsla_or15 import START, setup_signal, feed, bars


class PaperExchange:
    def __init__(self):
        self.orders = {}
        self.posts, self.cancels = [], []
        self.qty = 0
        self.now = START + timedelta(minutes=18, milliseconds=250)
        self.entry_price = 102.25
        self.reject_entry = False
        self.reject_protection = False
        self.cancel_pending = False
        self.cancel_race = False
        self.exit_failure = None
        self.exit_count = 0
        self.intent_check = None

    def fill(self, oid, price):
        order = self.orders[oid]
        if order["status"] != "filled":
            self.qty += 1 if order["side"] == "buy" else -1
        order.update(status="filled", filled_qty="1", filled_avg_price=str(price), filled_at=self.now.isoformat())
        if oid in ("target", "stop"):
            sibling = self.orders["stop" if oid == "target" else "target"]
            sibling["status"] = "canceled"

    def handler(self, request):
        path = request.url.path
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append(body)
            if self.intent_check:
                self.intent_check(body)
            if body.get("order_class") == "oco":
                if self.reject_protection:
                    return httpx.Response(422, json={"message": "stop too close"})
                target = dict(id="target", client_order_id=body["client_order_id"], type="limit", side="sell",
                              status="new", filled_qty="0", filled_avg_price=None, limit_price=body["take_profit"]["limit_price"])
                stop = dict(id="stop", type="stop", side="sell", status="new", filled_qty="0", filled_avg_price=None,
                            stop_price=body["stop_loss"]["stop_price"])
                self.orders.update(target=target, stop=stop)
                return httpx.Response(200, json={**target, "legs": [stop]})
            if body["side"] == "buy" and self.reject_entry:
                return httpx.Response(403, json={"message": "entry rejected"})
            oid = "entry" if body["side"] == "buy" else "exit"
            if body["side"] == "sell":
                self.exit_count += 1
                oid = f"exit-{self.exit_count}"
                if self.exit_count == 1 and self.exit_failure == "reject":
                    return httpx.Response(403, json={"message": "temporary refusal"})
            self.orders[oid] = dict(id=oid, client_order_id=body["client_order_id"], type="market", side=body["side"], status="new")
            if self.exit_count == 1 and self.exit_failure == "cancel" and body["side"] == "sell":
                self.orders[oid].update(status="canceled", filled_qty="0", filled_avg_price=None)
                return httpx.Response(200, json=self.orders[oid])
            self.fill(oid, self.entry_price if oid == "entry" else 103.0)
            return httpx.Response(200, json=self.orders[oid])
        if path == "/v2/orders:by_client_order_id":
            order = next((o for o in self.orders.values() if o.get("client_order_id") == request.url.params["client_order_id"]), None)
            return httpx.Response(200 if order else 404, json=order or {})
        if path.startswith("/v2/positions/"):
            return httpx.Response(200 if self.qty else 404, json={"qty": str(self.qty)})
        if path.startswith("/v2/orders/"):
            oid = path.rsplit("/",1)[1]
            if oid not in self.orders:
                return httpx.Response(404, json={})
            if request.method == "DELETE":
                self.cancels.append(oid)
                if self.cancel_race:
                    self.fill("target", 108.75)
                    self.cancel_race = False
                elif not self.cancel_pending:
                    self.orders[oid]["status"] = "canceled"
                return httpx.Response(204)
            order = self.orders[oid]
            return httpx.Response(200, json={**order, **({"legs":[self.orders["stop"]]} if oid == "target" else {})})
        return httpx.Response(404, json={})


@pytest.fixture
def paper(monkeypatch, tmp_path):
    from backend.app import main as r
    r.reset_runtime_state()
    exchange = PaperExchange()
    broker = AlpacaBroker("test", "test", transport=httpx.MockTransport(exchange.handler), fill_wait_sec=.001, cancel_wait_sec=.001, poll_interval_sec=.001)
    monkeypatch.setattr(r.engine, "broker", broker)
    monkeypatch.setattr(r.engine, "broker_gate", lambda _o,_e: None)  # fixed calendar clock tested separately
    monkeypatch.setattr(r, "or15_sip_verified", True)
    monkeypatch.setitem(r.broker_state, "mismatch", False)
    monkeypatch.setitem(r.relay_statuses, "stock", "connected")
    store = TradingStateStore(str(tmp_path / "paper.sqlite3"))
    monkeypatch.setattr(r, "state_store", store)
    setup_signal(r.tsla_or15_strategy)
    feed(r.tsla_or15_strategy, bars(17,o=102,h=103,l=101,c=102))
    now = START + timedelta(minutes=18)
    monkeypatch.setattr(r, "or15_now", lambda: exchange.now)
    r.or15_controller.on_quote(QuoteEvent("TSLA",102,100,"V",102.1,100,"V",now))

    def intent_check(body):
        loaded = store.load_checkpoint()
        assert loaded is not None, "POST must have a real durable predecessor"
        state = decode_runtime_value(loaded[0])
        s = state["strategies"]["tsla_or15_retest"]
        assert s["signal_consumed"]
        if body.get("order_class") == "oco":
            assert s["protection_client_id"] == body["client_order_id"]
        else:
            assert any(o.broker_client_id == body["client_order_id"] or
                       (o.fixed_intent_client_id and body["side"] == "sell" and
                        body["client_order_id"].startswith(f"adt-{o.id}-"))
                       for o in state["engine"]["orders"].values())
    exchange.intent_check = intent_check
    yield r, exchange, now
    r.reset_runtime_state()
    broker.close()
    store.close()


def test_paper_uses_actual_fill_and_native_protection(paper):
    r, x, now = paper
    r.or15_controller.tick(now)
    s = r.tsla_or15_strategy
    assert s.entry_price == 102.25 and s.target_price == 108.75
    assert s.filled_at == x.now and s.exit_due == x.now + timedelta(minutes=120)
    assert x.posts[0]["type"] == "market" and x.posts[0]["qty"] == "1"
    assert x.posts[1]["stop_loss"] == {"stop_price":"99.00"}
    assert x.posts[1]["take_profit"] == {"limit_price":"108.75"}
    assert s.protection_confirmed and x.qty == 1
    assert not x.cancels
    r._settle_broker_orders()
    assert not x.cancels, "passive reconciliation must preserve broker protection"
    assert not r.engine.process_bar("TSLA",102,120,80,100,1000,now)
    assert not r.engine.process_quote("TSLA",98,99,now)
    assert x.qty == 1, "generic matching cannot send duplicate protective exits"


@pytest.mark.parametrize("race", [False, True])
def test_cancel_settle_precedes_time_exit_and_race_never_double_sells(paper, race):
    r, x, now = paper
    r.or15_controller.tick(now)
    x.now = r.tsla_or15_strategy.exit_due
    x.cancel_race = race
    r.or15_controller.tick(x.now)
    assert x.qty == 0 and "TSLA" not in r.account.positions
    assert len(x.posts) == (2 if race else 3)
    assert len(r.pending_trade_records) == 0  # flushed to durable ledger
    rows = r.state_store.list_trades_for_session(START.date().isoformat())
    assert len(rows) == 1 and rows[0]["execution_mode"] == "alpaca_paper"
    assert rows[0]["broker_fees"] is None


def test_unconfirmed_native_cancel_retains_position_until_resolved(paper):
    r, x, now = paper
    r.or15_controller.tick(now)
    x.cancel_pending = True
    r.or15_controller.tick(r.tsla_or15_strategy.exit_due)
    assert x.qty == 1 and len(x.posts) == 2
    assert r.tsla_or15_strategy.phase == "EXITING"
    x.cancel_pending = False
    r.or15_controller.tick(r.tsla_or15_strategy.exit_due + timedelta(seconds=1))
    assert x.qty == 0 and len(x.posts) == 3


@pytest.mark.parametrize("kind", ["entry", "protection", "invalid_fill"])
def test_rejects_and_invalid_actual_risk_never_reenter(paper, kind):
    r, x, now = paper
    x.reject_entry = kind == "entry"
    x.reject_protection = kind == "protection"
    if kind == "invalid_fill":
        x.entry_price = 98
    r.or15_controller.tick(now)
    r.or15_controller.tick(now + timedelta(seconds=1))
    assert x.qty == 0
    assert len([p for p in x.posts if p["side"] == "buy"]) == 1
    assert r.tsla_or15_strategy.signal_consumed


def test_restore_held_paper_trade_preserves_native_ids(paper):
    r, x, now = paper
    r.or15_controller.tick(now)
    r._checkpoint_runtime("TEST_HELD")
    s = r.tsla_or15_strategy
    due = s.exit_due
    r.reset_runtime_state()
    r._restore_checkpoint()
    assert s.exit_due == due and s.protection_confirmed
    r.or15_controller.tick(now + timedelta(seconds=2))
    assert len(x.posts) == 2 and not x.cancels
    x.fill("stop",98.5)
    r.or15_controller.tick(now + timedelta(seconds=3))
    assert x.qty == 0 and "TSLA" not in r.account.positions


def test_news_cannot_close_or15_or_manual_order_average_in(paper):
    r, x, now = paper
    r.or15_controller.tick(now)
    order = r.engine.create_order("TSLA",OrderSide.BUY,OrderType.MARKET,1,estimated_price=102,strategy_id="MANUAL")
    assert r.engine.submit_order(order.id).status.value == "REJECTED"
    b = r.or15_controller._bracket()
    with pytest.raises(ValueError, match="fixed"):
        r.bracket_manager.manual_tighten_stop("TSLA",102)
    assert b.current_stop_price == 99


def test_storage_failure_keeps_protection_or_uses_precommitted_exit(paper, monkeypatch):
    r, x, now = paper
    r.or15_controller.tick(now)
    monkeypatch.setattr(r, "_checkpoint_runtime", lambda *_a, **_k: False)
    r.or15_controller.request_exit("STORAGE_FAILURE_TEST", now)
    r.or15_controller.tick(now + timedelta(seconds=1))
    assert x.qty == 0
    assert len(x.posts) == 3
    # Rewind local state to the last successful save, as after a power failure.
    r.reset_runtime_state()
    r._restore_checkpoint()
    r._settle_broker_orders()
    assert "TSLA" not in r.account.positions
    assert len(x.posts) == 3, "saved emergency identity recovers the close without another order"


def test_oco_intent_survives_crash_before_post_and_reuses_identity(paper, monkeypatch):
    r, x, now = paper
    original = r.engine.broker.submit_oco
    def crash(*args, **kwargs):
        raise ConnectionError("crash before POST")
    monkeypatch.setattr(r.engine.broker, "submit_oco", crash)
    r.or15_controller.tick(now)
    assert x.qty == 1 and len(x.posts) == 1
    saved = r.tsla_or15_strategy.protection_client_id
    monkeypatch.setattr(r.engine.broker, "submit_oco", original)
    r.reset_runtime_state()
    r._restore_checkpoint()
    r.or15_controller.tick(now + timedelta(seconds=1))
    assert r.tsla_or15_strategy.protection_confirmed
    assert x.posts[-1]["client_order_id"] == saved and len(x.posts) == 2


def test_storage_failure_immediately_after_buy_still_restores_protection(paper, monkeypatch):
    r, x, now = paper
    original_check = x.intent_check
    def fail_after_buy(body):
        original_check(body)
        if body["side"] == "buy":
            monkeypatch.setattr(r, "_checkpoint_runtime", lambda *_a, **_k: False)
    x.intent_check = fail_after_buy
    r.or15_controller.tick(now)
    assert x.qty == 1 and r.tsla_or15_strategy.protection_confirmed
    assert len(x.posts) == 2
    r.reset_runtime_state()
    r._restore_checkpoint()
    r._settle_broker_orders()
    r.or15_controller.tick(now + timedelta(seconds=1))
    assert r.account.positions["TSLA"].shares == 1
    assert r.tsla_or15_strategy.protection_confirmed and len(x.posts) == 2


def test_late_actual_submission_clock_rejects_entry(paper):
    r, x, now = paper
    x.now = now + timedelta(seconds=6)
    r.or15_controller.tick(now)  # stale event-loop snapshot cannot authorize this POST
    assert not x.posts and x.qty == 0


@pytest.mark.parametrize("failure", ["cancel", "reject"])
def test_storage_down_close_retry_and_restart_never_loses_fill(paper, monkeypatch, failure):
    r, x, now = paper
    r.or15_controller.tick(now)
    monkeypatch.setattr(r, "_checkpoint_runtime", lambda *_a, **_k: False)
    x.exit_failure = failure
    r.or15_controller.request_exit("STORAGE_FAILURE_TEST", now)
    r.or15_controller.tick(now)
    assert x.qty == 1 and len(x.posts) == 3
    r.engine._broker_retry_after.clear()
    r.or15_controller.tick(now + timedelta(seconds=31))
    assert x.qty == 0 and len(x.posts) == 4
    assert (x.posts[2]["client_order_id"] == x.posts[3]["client_order_id"]) == (failure == "reject")
    r.reset_runtime_state()
    r._restore_checkpoint()
    r._settle_broker_orders()
    assert "TSLA" not in r.account.positions
    assert r.tsla_or15_strategy.phase == "CLOSED"
    assert len(x.posts) == 4


def test_fixed_fill_precision_and_native_audit_are_preserved(paper):
    r, x, now = paper
    x.entry_price = 102.00006
    r.tsla_or15_strategy.or_low = 99.00006
    r.or15_controller.tick(now)
    b = r.or15_controller._bracket()
    assert b.entry_price == x.entry_price and b.initial_stop_price == 99.00006
    assert b.target_1_price == pytest.approx(108.00006)
    event = next(a for a in r.tsla_or15_strategy.audit if a["kind"] == "PROTECTION_CONFIRMED")
    assert float(event["broker_stop"]) == 99.01
    assert float(event["broker_target"]) == 108.00
    r._checkpoint_runtime("PRECISION_TEST")
    r.reset_runtime_state()
    r._restore_checkpoint()
    assert r.tsla_or15_strategy.entry_price == x.entry_price


@pytest.mark.asyncio
async def test_full_production_ingress_commits_before_paper_entry(paper, monkeypatch):
    r, x, now = paper
    r.reset_runtime_state()
    monkeypatch.setattr(r.flattening_engine.clock, "now", lambda: x.now)
    for strategy in r.strategies:
        if strategy is not r.tsla_or15_strategy:
            strategy.pause()
    for i in range(18):
        params = dict(o=100,h=102.5,l=100,c=102) if i == 15 else dict(o=101.1,h=102,l=101,c=101.5) if i == 16 else dict(o=102,h=103,l=101,c=102) if i == 17 else {}
        for bar in bars(i, **params)[::-1]:
            x.now = bar.timestamp + timedelta(minutes=1, milliseconds=250)
            await r.handle_bar_event(bar)
        assert not x.posts
    assert r.tsla_or15_strategy.phase == "WAITING_ENTRY"
    assert not r.inflight_event_keys
    await r.handle_quote_event(QuoteEvent("TSLA",102,100,"V",102.1,100,"V",x.now))
    assert x.qty == 1 and r.tsla_or15_strategy.protection_confirmed, r.tsla_or15_strategy.session_record()
    assert len(x.posts) == 2
    x.fill("target",108.75)
    r.or15_controller.tick(x.now + timedelta(seconds=1))
    rows = r.state_store.list_trades_for_session(START.date().isoformat())
    assert len(rows) == 1 and rows[0]["avg_exit_price"] == 108.75
    assert not r.account.positions and not r.engine.working_orders


def test_missing_preceding_minute_prevents_quote_entry(paper):
    r, x, now = paper
    r.tsla_or15_strategy.bars["TSLA"].pop()
    r.or15_controller.tick(now)
    assert not x.posts
    r.or15_controller.tick(now + timedelta(seconds=6))
    assert r.tsla_or15_strategy.phase == "SKIPPED"
