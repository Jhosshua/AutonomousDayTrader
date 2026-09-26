"""Adversarial tri-engine lifecycle checks using real HTTP adapter, no network.

Prices, fills, time and HTTP responses are synthetic. These checks establish
controller behavior, never exchange commissioning or a real market-session fill.
"""
import json
from datetime import timedelta

import httpx
import pytest

from backend.app.core.broker import AlpacaBroker
from backend.app.core.persistence import TradingStateStore, decode_runtime_value
from backend.app.models.events import QuoteEvent
from backend.tests.unit.test_tri_signals import START, breakout, feed, pair, prepare, retest


class TrancheExchange:
    def __init__(self):
        self.orders, self.positions = {}, {}
        self.posts, self.cancels = [], []
        self.now = START + timedelta(minutes=18, milliseconds=250)
        self.entry_price = 101.0
        self.entry_mode = "filled"
        self.cancel_pending = False
        self.on_post = None
        self.on_asset = None
        self.fill_on_cancel = None

    def fill(self, oid, qty=None, price=None, terminal=True):
        row = self.orders[oid]
        qty = int(row["qty"]) if qty is None else qty
        old_qty = int(row["filled_qty"])
        price = self.entry_price if price is None else price
        old_notional = old_qty * float(row.get("filled_avg_price") or 0)
        new_qty = qty - old_qty
        sym = row["symbol"]
        self.positions[sym] = self.positions.get(sym, 0) + new_qty * (1 if row["side"] == "buy" else -1)
        if not self.positions[sym]:
            self.positions.pop(sym)
        row.update(status="filled" if terminal else "partially_filled", filled_qty=str(qty),
                   filled_avg_price=str((old_notional + new_qty * price) / qty),
                   filled_at=self.now.isoformat(), updated_at=self.now.isoformat())
        if terminal and row.get("sibling"):
            self.orders[row["sibling"]]["status"] = "canceled"

    def result(self, row):
        result = {k: v for k, v in row.items() if k not in ("sibling", "child")}
        if row.get("child"):
            result["legs"] = [self.result(self.orders[row["child"]])]
        return result

    def handler(self, request):
        path = request.url.path
        if request.method == "POST" and path == "/v2/orders":
            body = json.loads(request.content)
            self.posts.append(body)
            if self.on_post:
                self.on_post(body)
            if any(o.get("client_order_id") == body["client_order_id"] for o in self.orders.values()):
                return httpx.Response(422, json={"message": "client_order_id must be unique"})
            oid = f"broker-{len(self.orders) + 1}"
            row = dict(body, id=oid, status="new", filled_qty="0", filled_avg_price=None)
            self.orders[oid] = row
            if body.get("order_class") == "oco":
                child = f"{oid}-stop"
                row.update(type="limit", limit_price=body["take_profit"]["limit_price"], child=child, sibling=child)
                self.orders[child] = dict(id=child, client_order_id=f"child-{oid}", symbol=body["symbol"],
                    side=body["side"], type="stop", stop_price=body["stop_loss"]["stop_price"], qty=body["qty"],
                    status="new", filled_qty="0", filled_avg_price=None, sibling=oid)
            elif body["client_order_id"].endswith("-entry"):
                if self.entry_mode == "filled":
                    self.fill(oid)
                elif self.entry_mode == "partial":
                    self.fill(oid, max(1, int(body["qty"]) // 2), terminal=False)
            else:
                self.fill(oid, price=102)
            return httpx.Response(200, json=self.result(row))
        if path == "/v2/orders:by_client_order_id":
            row = next((o for o in self.orders.values() if o.get("client_order_id") == request.url.params["client_order_id"]), None)
            return httpx.Response(200 if row else 404, json=self.result(row) if row else {})
        if path.startswith("/v2/assets/"):
            if self.on_asset:
                self.on_asset()
            return httpx.Response(200, json={"tradable": True, "status": "active", "shortable": True,
                                            "borrow_status": "easy_to_borrow"})
        if path.startswith("/v2/positions/"):
            qty = self.positions.get(path.rsplit("/", 1)[1], 0)
            return httpx.Response(200 if qty else 404, json={"qty": str(qty)})
        if path.startswith("/v2/orders/"):
            row = self.orders.get(path.rsplit("/", 1)[1])
            if not row:
                return httpx.Response(404, json={})
            if request.method == "DELETE":
                self.cancels.append(row["id"])
                if self.fill_on_cancel:
                    oid, qty, price = self.fill_on_cancel
                    self.fill_on_cancel = None
                    self.fill(oid, qty, price)
                elif not self.cancel_pending:
                    row["status"] = "canceled"
                    if row.get("sibling"):
                        self.orders[row["sibling"]]["status"] = "canceled"
                return httpx.Response(204)
            return httpx.Response(200, json=self.result(row))
        return httpx.Response(404, json={})


@pytest.fixture
def tri_paper(monkeypatch, tmp_path):
    from backend.app import main as r
    r.reset_runtime_state()
    x = TrancheExchange()
    broker = AlpacaBroker("test", "test", transport=httpx.MockTransport(x.handler))
    store = TradingStateStore(str(tmp_path / "tri.sqlite3"))
    monkeypatch.setattr(r.engine, "broker", broker)
    monkeypatch.setattr(r, "state_store", store)
    monkeypatch.setattr(r, "or15_sip_verified", True)
    monkeypatch.setattr(r, "or15_now", lambda: x.now)
    monkeypatch.setattr(r, "_broker_gate", lambda _o, _e: None)
    monkeypatch.setitem(r.relay_statuses, "stock", "connected")
    monkeypatch.setitem(r.broker_state, "mismatch", False)
    monkeypatch.setattr(r.tri_controller, "inline_io", True)

    def check_intent(body):
        checkpoint = store.load_checkpoint()
        assert checkpoint is not None, "Each POST needs a durable predecessor"
        state = decode_runtime_value(checkpoint[0])
        cid = body["client_order_id"]
        strategy = state["strategies"][r.tri_controller.by_symbol[body["symbol"]].strategy_id]
        assert strategy["signal_consumed"]
        # Protection/close ids are derived from the durable entry id, so a
        # restart rebuilds and finds them even if storage failed after entry.
        entry_cids = [n["cid"] for n in strategy["native"].values() if n["role"] == "entry"]
        assert (any(n["cid"] == cid for n in strategy["native"].values())
                or any(t["protection_cid"] == cid for t in strategy["tranches"])
                or any(cid.startswith(e[:-len("entry")] + "t") for e in entry_cids)), cid
    x.on_post = check_intent
    yield r, x
    r.reset_runtime_state()
    broker.close()
    store.close()


def arm(r, x, symbol="TSLA", side="LONG"):
    s = r.tri_controller.by_symbol[symbol]
    prepare(s)
    if side == "LONG":
        breakout(s)
        retest(s)
        feed(s, pair(symbol, 17, o=102, h=103, l=101, c=102))
        x.entry_price = 101
    else:
        feed(s, pair(symbol, 15, o=99.5, h=100, l=98, c=98.5, q=199, qh=200, ql=198))
        feed(s, pair(symbol, 16, o=98.5, h=99, l=98, c=98.5, q=199, qh=200, ql=198))
        x.entry_price = 98.5
    x.now = s.entry_due + timedelta(milliseconds=250)
    r.tri_controller.on_quote(QuoteEvent(symbol, x.entry_price, 100, "V", x.entry_price + .01, 100, "V", x.now))
    r.tri_controller.tick(x.now)
    return s


def advance(r, x, seconds=6):
    x.now += timedelta(seconds=seconds)
    r.tri_controller.tick(x.now)


@pytest.mark.parametrize("symbol,side,tranches", [("TSLA", "LONG", 2), ("TSLA", "SHORT", 2), ("CDE", "LONG", 1), ("CDE", "SHORT", 1)])
def test_real_adapter_routes_both_sides_and_distinct_native_tranches(tri_paper, symbol, side, tranches):
    r, x = tri_paper
    s = arm(r, x, symbol, side)
    assert s.phase == "HOLDING", s.last_error
    assert len(s.tranches) == tranches and all(t["protection_confirmed"] for t in s.tranches)
    assert len(x.posts) == 1 + tranches
    assert x.posts[0]["type"] == "market" and "limit_price" not in x.posts[0]
    assert all(p["side"] == ("sell" if side == "LONG" else "buy") for p in x.posts[1:])
    assert sum(t["qty"] for t in s.tranches) == abs(x.positions[symbol])
    before = len(x.posts)
    r._settle_broker_orders()
    assert len(x.posts) == before and not x.cancels


def test_tsla_time_limit_closes_only_first_tranche(tri_paper):
    r, x = tri_paper
    s = arm(r, x)
    first, second = s.tranches
    x.now = first["exit_due"]
    r.tri_controller.tick(x.now)
    assert first["closed_qty"] == first["qty"], s.last_error
    assert second["closed_qty"] == 0
    assert x.positions["TSLA"] == second["qty"]
    assert not s.trade_recorded
    x.now = second["exit_due"]
    r.tri_controller.tick(x.now)
    assert s.phase == "CLOSED" and not x.positions and not r.account.positions
    assert len(r.state_store.list_trades_for_session(START.date().isoformat())) == 1


def test_partial_native_target_keeps_remaining_fixed_protection(tri_paper):
    r, x = tri_paper
    s = arm(r, x, "CDE")
    t = s.tranches[0]
    oid = s.native[t["target_order_id"]]["id"]
    x.fill(oid, 1, t["target"], terminal=False)
    advance(r, x)
    advance(r, x)
    assert not x.cancels, "A partial target must not cancel valid stop/target on remaining shares"
    assert len(x.posts) == 2 and t["closed_qty"] == 1
    assert x.positions["CDE"] == t["qty"] - 1


def test_cancel_race_books_target_and_never_sells_it_twice(tri_paper):
    r, x = tri_paper
    s = arm(r, x, "CDE")
    t = s.tranches[0]
    x.fill_on_cancel = (s.native[t["target_order_id"]]["id"], t["qty"], t["target"])
    r.tri_controller.request_exit("CDE", "MANUAL_FLATTEN", x.now)
    advance(r, x)
    assert not x.positions and not r.account.positions
    assert len(x.posts) == 2 and s.phase == "CLOSED"


def test_cancel_pending_cannot_release_market_exit(tri_paper):
    r, x = tri_paper
    s = arm(r, x, "CDE")
    x.cancel_pending = True
    r.tri_controller.request_exit("CDE", "MANUAL_FLATTEN", x.now)
    advance(r, x)
    advance(r, x)
    assert len(x.posts) == 2 and x.positions["CDE"] == s.quantity
    x.cancel_pending = False
    advance(r, x)
    assert not x.positions and s.phase == "CLOSED"


def test_restart_does_not_duplicate_held_native_orders(tri_paper):
    r, x = tri_paper
    s = arm(r, x)
    ids = {n["cid"] for n in s.native.values()}
    r._checkpoint_runtime("REVIEW_HELD")
    r.reset_runtime_state()
    r._restore_checkpoint()
    advance(r, x)
    assert len(x.posts) == 3 and not x.cancels
    assert {n["cid"] for n in s.native.values()} == ids
    assert s.phase == "HOLDING"


def test_halt_during_asset_lookup_cannot_send_new_entry(tri_paper):
    r, x = tri_paper
    x.on_asset = lambda: r.tri_controller.request_exit("CDE", "CIRCUIT_BREAKER", x.now)
    s = arm(r, x, "CDE")
    assert not x.posts, "A concurrent account halt invalidates the worker's prior entry intent"
    assert s.phase in ("SKIPPED", "EXITING")


def test_closed_market_never_queues_next_open_exit(tri_paper, monkeypatch):
    r, x = tri_paper
    s = arm(r, x, "CDE")
    monkeypatch.setattr(r, "_broker_gate", lambda _o, _e: ("MARKET_CLOSED", False, 60))
    x.now = START.replace(hour=16, minute=5)
    r.tri_controller.tick(x.now)
    assert len(x.posts) == 2, "No day market POST may queue at the next open"
    assert x.positions["CDE"] == s.quantity


def test_storage_loss_after_entry_fill_cannot_leave_bare_position(tri_paper, monkeypatch):
    r, x = tri_paper
    original = x.on_post
    def lose_storage(body):
        original(body)
        if body["client_order_id"].endswith("-entry"):
            monkeypatch.setattr(r, "_checkpoint_runtime", lambda *_a, **_k: False)
    x.on_post = lose_storage
    s = arm(r, x, "CDE")
    advance(r, x)
    assert (not x.positions or all(t["protection_confirmed"] for t in s.tranches)), "Durable entry needs precommitted protection or emergency-close identity"


def test_partial_entry_keeps_filled_shares_as_a_smaller_protected_trade(tri_paper):
    r, x = tri_paper
    x.entry_mode = "partial"
    s = arm(r, x)
    assert s.phase == "ENTERING" and not s.tranches, "No tranches until the entry is finished"
    advance(r, x)  # past the 5 s grace: the unfilled rest is canceled
    actual = abs(x.positions["TSLA"])
    assert s.entry_terminal and x.cancels and actual < s.quantity
    assert s.phase == "HOLDING" and not s.incomplete and s.exit_reason is None
    assert sum(t["qty"] for t in s.tranches) == actual
    assert [t["target_r"] for t in s.tranches] == [1.5, 2.0]
    assert all(t["protection_confirmed"] for t in s.tranches)
    assert any(e["kind"] == "PARTIAL_ENTRY_KEPT" for e in s.audit)


def test_transient_poll_error_does_not_close_the_trade(tri_paper, monkeypatch):
    r, x = tri_paper
    s = arm(r, x, "CDE")
    original = x.handler
    def flaky(request):
        if request.method == "GET" and request.url.path.startswith("/v2/orders/"):
            return httpx.Response(429, json={"message": "rate limit"})
        return original(request)
    monkeypatch.setattr(x, "handler", flaky)
    r.engine.broker._client._transport = httpx.MockTransport(flaky)
    advance(r, x)
    assert s.phase == "HOLDING" and not s.incomplete and s.last_error
    r.engine.broker._client._transport = httpx.MockTransport(original)
    advance(r, x)
    assert s.phase == "HOLDING" and len(x.posts) == 2


def test_lost_entry_reply_never_forgets_shares_at_the_broker(tri_paper):
    r, x = tri_paper
    from backend.app.core.broker import BrokerError
    broker = r.engine.broker
    real_find, real_submit = broker.find_by_client_id, broker.submit
    def lost_reply(*a, **k):
        real_submit(*a, **k)  # Alpaca accepted and filled it...
        raise BrokerError("reply timed out")  # ...but the bot never heard back
    broker.submit = lost_reply
    broker.find_by_client_id = lambda cid: None  # and the id lookup lags
    s = arm(r, x, "CDE")
    broker.submit = real_submit
    x.now = s.entry_due + timedelta(seconds=7)
    r.tri_controller.tick(x.now)
    assert x.positions.get("CDE"), "the entry really filled at the broker"
    assert s.phase == "ENTERING" and r.broker_state["mismatch"]
    broker.find_by_client_id = real_find
    advance(r, x, seconds=2)
    assert s.phase == "HOLDING" and sum(t["qty"] for t in s.tranches) == x.positions["CDE"]


def test_quote_stream_does_not_checkpoint_or_poll_every_tick(tri_paper, monkeypatch):
    r, x = tri_paper
    s = arm(r, x, "CDE")
    saves, gets = [], []
    real = r._checkpoint_runtime
    monkeypatch.setattr(r, "_checkpoint_runtime", lambda *a, **k: saves.append(a) or real(*a, **k))
    original = x.handler
    def counting(request):
        if request.method == "GET":
            gets.append(request.url.path)
        return original(request)
    r.engine.broker._client._transport = httpx.MockTransport(counting)
    for i in range(50):  # 50 quotes over 5 seconds while holding
        x.now += timedelta(milliseconds=100)
        r.tri_controller.tick(x.now)
    assert len(saves) == 0, "nothing changed, nothing to save"
    assert len(gets) <= 1, gets


def test_restart_pending_market_entry_retains_identity_without_resubmission(tri_paper):
    r, x = tri_paper
    x.entry_mode = "pending"
    s = arm(r, x, "CDE")
    cid = x.posts[0]["client_order_id"]
    assert len(x.posts) == 1 and not x.positions
    r.reset_runtime_state()
    r._restore_checkpoint()
    advance(r, x, seconds=1)
    assert len(x.posts) == 1 and s.phase == "ENTERING"
    x.fill(next(o["id"] for o in x.orders.values() if o["client_order_id"] == cid))
    advance(r, x, seconds=1)
    assert s.phase == "HOLDING" and len(x.posts) == 2


def test_unfilled_market_entry_is_canceled_after_grace_never_resubmitted(tri_paper):
    r, x = tri_paper
    x.entry_mode = "pending"
    s = arm(r, x, "CDE")
    advance(r, x, seconds=6)
    advance(r, x, seconds=6)
    assert len(x.posts) == 1 and x.cancels and not x.positions
    assert s.phase == "SKIPPED" and not r.account.positions


@pytest.mark.parametrize("fill_price,kept", [(98.2, True), (97.0, False)])
def test_short_fill_slippage_keeps_small_overrun_and_closes_large_one(tri_paper, fill_price, kept):
    r, x = tri_paper
    x.on_asset = lambda: setattr(x, "entry_price", fill_price)
    s = arm(r, x, "CDE", "SHORT")
    budget = r.account.daily_starting_equity * .0075
    actual = s.quantity * (s.stop - fill_price)
    assert actual > budget
    if kept:
        assert actual <= budget * 1.5 and not s.exit_reason and s.phase == "HOLDING"
        assert any(e["kind"] == "FILL_RISK_OVER_BUDGET" for e in s.audit)
    else:
        assert s.incomplete and s.exit_reason == "INVALID_FILL_RISK"
        advance(r, x)
        assert not x.positions and s.phase == "CLOSED"


def test_other_arms_risk_never_consumes_the_plan_budget(tri_paper):
    r, x = tri_paper
    r.account.apply_fill("existing", "AAPL", "BUY", 10, 100, 0, START,
                         strategy_id="other", stop_loss_price=35)
    assert r.tri_controller.open_risk() == 0
    s = arm(r, x, "CDE", "SHORT")
    assert s.phase == "HOLDING"
    assert r.tri_controller.open_risk() <= r.account.daily_starting_equity * .015


def test_native_stop_fill_before_oco_binding_is_booked_once(tri_paper):
    r, x = tri_paper
    original_result = x.result
    fired = False
    def fill_before_return(row):
        nonlocal fired
        if row.get("child") and not fired:
            fired = True
            x.fill(row["child"], price=98)
        return original_result(row)
    x.result = fill_before_return
    s = arm(r, x, "CDE")
    for _ in range(3):
        advance(r, x)
    assert not x.positions and not r.account.positions
    assert len(x.posts) == 2
    assert s.phase == "CLOSED", s.last_error
    assert sum(t["closed_qty"] for t in s.tranches) == s.quantity


def test_plan_orders_skip_the_25k_cap_but_other_arms_keep_it(tri_paper):
    # Real 2026 TSLA replay: the $25k cap refused 92 of 146 plan trades.
    from backend.app.models.events import OrderSide, OrderType
    r, _ = tri_paper
    plan = r.engine.create_order("TSLA", OrderSide.BUY, OrderType.MARKET, 120, stop_price=425,
                                 estimated_price=430, strategy_id="tsla_asymmetric_dual")
    other = r.engine.create_order("AAPL", OrderSide.BUY, OrderType.MARKET, 120, stop_price=425,
                                  estimated_price=430, strategy_id="orb")
    assert r.account.can_afford("TSLA", "BUY", 120, 430, concentration_cap=False)[0]
    assert not r.account.can_afford("AAPL", "BUY", 120, 430)[0]
    assert "concentration" not in str(r.engine.submit_order(plan.id).reject_reason or "")
    assert "concentration" in str(r.engine.submit_order(other.id).reject_reason)


def test_short_bigger_than_buying_power_is_shrunk_not_rejected(tri_paper, monkeypatch):
    r, x = tri_paper
    monkeypatch.setattr(r.account, "can_afford",
                        lambda sym, side, qty, price, concentration_cap=True: (qty <= 50, "BP"))
    s = arm(r, x, "CDE", "SHORT")
    assert s.phase == "HOLDING" and s.quantity == 50
    assert any(e["kind"] == "SIZE_LIMITED_BY_BUYING_POWER" and e["allowed"] == 50 for e in s.audit)


def test_partial_entry_with_stuck_cancel_is_still_flattened(tri_paper):
    # Second-round review P1: the rest's cancel never completes.
    r, x = tri_paper
    x.entry_mode, x.cancel_pending = "partial", True
    s = arm(r, x)
    for _ in range(4):
        advance(r, x)
    assert s.tranches and all(t["protection_confirmed"] for t in s.tranches), "filled shares get protection"
    r.tri_controller.request_exit("TSLA", "FORCED_FLAT", x.now)
    x.cancel_pending = False  # the OCO cancels work; the entry's rest never does
    stuck = next(o for o in x.orders.values() if o["client_order_id"].endswith("-entry"))
    stuck["status"] = "pending_cancel"
    x.cancel_pending = True
    real_delete_target = [o for o in x.orders.values() if o.get("order_class") == "oco"]
    for o in real_delete_target:
        o["status"] = "canceled"
        x.orders[o["child"]]["status"] = "canceled"
    for _ in range(4):
        advance(r, x)
    assert not x.positions.get("TSLA"), (s.phase, s.last_error)


def test_unrelated_broker_shares_do_not_stall_entry_forever(tri_paper):
    # Second-round review P1: shares that were there before our POST are not ours.
    from backend.app.core.broker import BrokerError
    r, x = tri_paper
    x.positions["CDE"] = 7
    broker = r.engine.broker
    real_submit = broker.submit
    def never_arrives(*a, **k):
        raise BrokerError("connection reset before Alpaca got it")
    broker.submit = never_arrives
    s = arm(r, x, "CDE")
    broker.submit = real_submit
    x.now = s.entry_due + timedelta(seconds=7)
    r.tri_controller.tick(x.now)
    assert s.phase == "SKIPPED" and x.positions["CDE"] == 7 and not r.broker_state["mismatch"]
