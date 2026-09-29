"""Codex phase-3 review fixes (2026-09-28): orphan ORB positions closed ORB-safely, timestamped
marks + breaker, shutdown drain, ORB-only mismatch lag, scheduler state off the event loop."""
import asyncio
import threading
import time as _time
from datetime import timedelta

import pytest

from backend.app.core.orb_execution import RequestBudget
from backend.app.core.persistence import TradingStateStore
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick


def rebuild_off(r, h):
    """A new process whose ORB state could not be restored: the controller knows nothing."""
    r.orb.build(h.broker, h.facade, "off", clock=h.clock, inline=True, monotonic=lambda: h.clock.now.timestamp(),
                budget=RequestBudget(10 ** 6, 10 ** 6), sleep=h.clock.sleep, is_session=lambda d: True,
                restore=False)
    h.ctl, h.sched = r.orb.controller, r.orb.scheduler


def settle(r, h, rounds=10):
    for _ in range(rounds):
        r.orb.tick(h.clock.now)
        h.clock.advance(6)


# ------------------------------------------------------------------ P1 #1 orphans: never auto-sold
def test_unprovable_orb_shares_are_never_sold_and_raise_a_plain_alert(main_runtime):
    """ORB's state was lost (controller rebuilt empty) while ADT's book and Alpaca still hold the
    bracket: nothing may auto-sell it (its legs are live at Alpaca), no generic path may touch it,
    and the card says what to do."""
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    rebuild_off(r, h)
    h.ctl.reconcile_on_startup()                              # nothing is adopted without proof
    n = len(h.alpaca.requests)
    r._trip_circuit_breaker(h.clock.now)
    out = asyncio.run(r._execute_manual_flatten(["APP"], h.clock.now, None))
    asyncio.run(r.handle_flattening_directive(r.FlatteningDirective(
        phase=r.FlatteningPhase.MANDATORY_LIQUIDATION, timestamp=h.clock.now, action_required="t",
        liquidate_all_positions=True)))
    settle(r, h)
    assert h.writes_after(n) == [] and h.alpaca_positions() == {"APP": 454}
    assert r.account.positions["APP"].shares == 454
    assert not [o for o in r.engine.orders.values() if o.symbol == "APP" and o.strategy_id != "orb"]
    alert = r.orb.alerts["APP"]
    assert "no ORB order at Alpaca explains" in alert and "Nothing will sell them automatically" in alert
    assert out["rejected"] == [{"symbol": "APP", "reason": alert}]
    card = next(c for c in r._strategy_cards(h.clock.now) if c["id"] == "orb")
    assert alert in card["orb"]["alerts"] and asyncio.run(r.get_health())["orb"]["alerts"] == [alert]
    o = r.engine.create_order("APP", r.OrderSide.SELL, r.OrderType.MARKET, 454, strategy_id="AUTO_FLATTEN")
    ok, why = r.pre_trade_risk_validator(o, r.account)
    assert not ok and why.startswith("ORB_OWNED")


def test_a_mixed_position_is_never_auto_sold_beyond_orbs_own_shares(main_runtime):
    """Alpaca holds ORB's 454 plus 100 bought by hand, and ADT's book says ORB has 500 (46 unexplained).
    The controller closes exactly its own 454 at 11:00; the 100 manual shares stay at Alpaca, and the 46
    ADT cannot explain are alerted, never sold."""
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    h.alpaca._apply_position("APP", "buy", 100, 100.0)
    r.account.apply_fill("x", "APP", "BUY", 46, 100.0, 0.0, h.clock.now, strategy_id="orb")
    h.clock.set(at(11, 0, 5))
    for _ in range(4):
        h.tick(6)
    sells = [q[2] for q in h.alpaca.requests if q[0] == "POST" and q[2].get("side") == "sell"]
    assert [int(b["qty"]) for b in sells] == [454]
    assert h.alpaca_positions() == {"APP": 100}
    assert r.account.positions["APP"].shares == 46 and "APP" in r.orb.alerts
    assert h.alpaca.refused_403 == []


def test_a_rebuilt_controller_re_emitting_fills_books_nothing_twice(main_runtime):
    """The controller comes back from an OLDER saved state (the entry intent, before the fill was read):
    reconciliation reads the filled bracket again and re-emits the entry fill; ADT's per-order map books
    only what is beyond what it already booked for that Alpaca order id."""
    r = main_runtime
    h = MainOrb(r)
    older = []
    def keep_unfilled_entry(st):
        orders = st.get("orders") or {}
        if not older and any(o.get("role") == "entry" and not o.get("filled_qty") for o in orders.values()):
            older.append(st)
    h.ctl.persist_cb = (lambda real: (lambda st: (keep_unfilled_entry(h.ctl.to_state()), real(st))))(
        h.ctl.persist_cb)
    h.open_bracket()
    assert r.account.positions["APP"].shares == 454
    parent_id = h.parent()["id"]
    assert r.orb.ledger["booked"][parent_id]["qty"] == 454             # keyed by Alpaca's order id
    r.orb._mem_state["controller"] = older[0]                           # an older durable row
    r.orb.build(h.broker, h.facade, "live", clock=h.clock, inline=True, monotonic=lambda: h.clock.now.timestamp(),
                budget=RequestBudget(10 ** 6, 10 ** 6), sleep=h.clock.sleep, is_session=lambda d: True)
    h.ctl, h.sched = r.orb.controller, r.orb.scheduler
    assert h.ctl.own_qty("APP") == 0
    assert h.ctl.reconcile_on_startup()["ok"] and h.ctl.own_qty("APP") == 454     # fill re-emitted
    r.orb.sync(h.clock.now)
    assert r.account.positions["APP"].shares == 454                     # not 908
    n = len(h.alpaca.requests)
    h.clock.set(at(11, 0, 5))
    h.run_supervisor(passes=3)
    h.assert_orb_closed_through_its_controller(n)


def test_the_shutdown_fence_covers_the_exit_post_and_closes_the_check_to_post_race(main_runtime):
    """A write already past the fence finishes before close_writes returns; nothing POSTs afterwards,
    including the exit's market order (which does not go through _write)."""
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    real = h.broker.submit_market_order
    entered, release = threading.Event(), threading.Event()

    def slow_submit(*a, **kw):
        entered.set()
        release.wait(3)
        return real(*a, **kw)
    h.broker.submit_market_order = slow_submit
    h.ctl.request_exit("APP", "MANUAL_FLATTEN")
    t = threading.Thread(target=h.ctl.tick)
    t.start()
    assert entered.wait(3)                     # the exit POST is in flight
    closed = {}
    c = threading.Thread(target=lambda: closed.setdefault("ok", h.ctl.close_writes("test", wait_s=5)))
    c.start()
    _time.sleep(0.1)
    assert "ok" not in closed                  # close_writes waits for the in-flight write
    release.set()
    t.join(5)
    c.join(5)
    assert closed["ok"] is True
    n = len(h.alpaca.requests)
    h.broker.submit_market_order = real
    h.ctl.request_exit("APP", "AGAIN")
    h.alpaca.prices["APP"] = 50.0
    h.ctl.tick()
    assert [q for q in h.alpaca.requests[n:] if q[0] in ("POST", "DELETE", "PATCH")] == []


# ------------------------------------------------------------------ P1 #2 marks + breaker
def test_orb_positions_are_marked_from_the_newest_timestamped_price_and_trip_the_breaker(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    t0 = h.clock.now
    r.orb.note_price("APP", 99.0, t0 + timedelta(seconds=10), "sip_trade")
    r.orb.note_price("APP", 101.0, t0 + timedelta(seconds=5), "quote_mid")          # older: ignored
    assert r.orb.mark_positions(t0 + timedelta(seconds=10)) is True
    assert r.account.positions["APP"].market_price == 99.0
    # a supervisor read newer than the feed wins
    with h.ctl._lock:
        pos = next(p for p in h.ctl.state["positions"].values() if p["symbol"] == "APP")
        pos.update(last_px=98.5, last_px_at=(t0 + timedelta(seconds=20)).isoformat())
    assert r.orb.mark_positions(t0 + timedelta(seconds=20)) is False                # throttled (<5 s)
    assert r.account.positions["APP"].market_price == 98.5
    # a crash through ADT's daily limit ($1,250): 454 x $3.00 = -$1,362 -> breaker trips, ORB exits
    r.orb._last_mark_eval = None
    r.orb.note_price("APP", 97.2, t0 + timedelta(seconds=30), "sip_trade")
    assert r.orb.mark_positions(t0 + timedelta(seconds=30)) is True
    assert r.risk_engine.status == r.BreakerStatus.HALTED_DAILY_LOSS
    assert h.ctl.entries_blocked() == "CIRCUIT_BREAKER"
    assert any(e.get("reason") == "CIRCUIT_BREAKER" for e in h.ctl.state["events"])


def test_a_throttled_breaker_check_still_runs_when_the_throttle_expires(main_runtime, monkeypatch):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    clock = [1000.0]
    monkeypatch.setattr("backend.app.core.orb_integration._time.monotonic", lambda: clock[0])
    t0 = h.clock.now
    r.orb.note_price("APP", 100.0, t0 + timedelta(seconds=1))
    assert r.orb.mark_positions(t0) is True
    clock[0] += 1.0
    r.orb.note_price("APP", 97.2, t0 + timedelta(seconds=2))           # -$1,362: throttled this second
    assert r.orb.mark_positions(t0) is False and r.risk_engine.status == r.BreakerStatus.ARMED
    clock[0] += 5.0                                                    # no new price at all
    assert r.orb.mark_positions(t0) is True
    assert r.risk_engine.status == r.BreakerStatus.HALTED_DAILY_LOSS


def test_feed_events_feed_the_marks(main_runtime):
    from backend.app.models.events import TradeEvent
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    ts = h.clock.now + timedelta(seconds=3)
    asyncio.run(r.handle_trade_event(TradeEvent(symbol="APP", price=100.9, size=10, timestamp=ts, trade_id=1, exchange="V")))
    assert r.orb.marks["APP"][:2] == (100.9, ts)


# ------------------------------------------------------------------ P1 #3 shutdown drain
def _live_threaded(r, slow_s=0.0):
    h = MainOrb(r, inline=False)
    handler = h.broker._client._transport.handler

    def slow(request):
        if slow_s:
            _time.sleep(slow_s)
        return handler(request)
    h.broker._client._transport.handler = slow
    return h


def test_drain_refuses_an_entry_that_reaches_its_post_after_the_drain_began(main_runtime):
    r = main_runtime
    h = _live_threaded(r)
    gate = threading.Event()
    real = h.facade.recheck

    def slow_recheck(card, now):
        gate.wait(3)
        return real(card, now)
    h.facade.recheck = slow_recheck
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    h.sched._start("execute", lambda: h.ctl.execute([pick("APP", "long", 100.0, 98.0)], h.clock.now), {"wave": "primary"})
    threading.Timer(0.2, gate.set).start()
    unfinished = r.orb.drain(timeout=3.0)
    assert unfinished == []
    assert not [q for q in h.alpaca.requests if q[0] == "POST"]       # the entry was never sent
    assert h.ctl.last_execute and not h.ctl.last_execute["ok"]


def test_drain_lets_a_running_exit_finish_then_refuses_every_later_write(main_runtime):
    r = main_runtime
    h = _live_threaded(r)
    h.open_bracket()
    h.broker._client._transport.handler = (lambda real: (lambda req: (_time.sleep(0.05), real(req))[1]))(
        h.broker._client._transport.handler)
    h.ctl.request_exit("APP", "MANUAL_FLATTEN")
    h.sched._start("supervise", lambda: h.ctl.tick())
    unfinished = r.orb.drain(timeout=10.0)
    assert unfinished == [] and h.alpaca_positions() == {}            # the exit finished inside the bound
    n = len(h.alpaca.requests)
    with pytest.raises(Exception):
        h.ctl._write("exit", h.broker.submit_market_order, "APP", "sell", 1, "adt-orb-X-late")
    assert len(h.alpaca.requests) == n


def test_a_job_still_running_at_the_drain_deadline_never_posts_afterwards(main_runtime):
    r = main_runtime
    h = _live_threaded(r)
    h.open_bracket()
    h.broker._client._transport.handler = (lambda real: (lambda req: (_time.sleep(0.4), real(req))[1]))(
        h.broker._client._transport.handler)
    h.ctl.request_exit("APP", "MANUAL_FLATTEN")
    h.sched._start("supervise", lambda: h.ctl.tick())
    _time.sleep(0.05)
    unfinished = r.orb.drain(timeout=0.3)
    assert unfinished
    drained_at = len(h.alpaca.requests)
    deadline = _time.monotonic() + 15
    while _time.monotonic() < deadline and any(not f.done() for f in h.sched.running_futures()):
        _time.sleep(0.05)
    late = [q for q in h.alpaca.requests[drained_at:] if q[0] == "POST"]
    assert late == []                                                  # no close was sent after the drain
    assert h.alpaca.refused_403 == []


def test_lifespan_drains_orb_before_the_final_checkpoint():
    import inspect
    from backend.app import main
    src = inspect.getsource(main.lifespan)
    assert src.index("orb.drain") < src.index('_checkpoint_runtime("GRACEFUL_SHUTDOWN")') < src.index("state_store.close()")


# ------------------------------------------------------------------ P2 #4 mismatch lag
def test_orb_fill_lag_never_pauses_entries_but_real_differences_still_block(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    parent = h.parent()
    h.alpaca.fill(h.alpaca.leg(parent["id"], "tp")["id"], price=101.85)   # ORB has not polled yet
    assert r.account.positions["APP"].shares == 454 and h.alpaca_positions() == {}
    r.alpaca_broker, r.engine.broker = h.broker, h.broker
    try:
        asyncio.run(r._broker_reconcile_once())
        assert r.broker_state["mismatch"] is False and "APP" not in r.account.positions
        r.account.apply_fill("n1", "NVDA", "BUY", 5, 100.0, 0.0, h.clock.now, strategy_id="vwap_pullback")
        asyncio.run(r._broker_reconcile_once())
        assert r.broker_state["mismatch"] is True and "NVDA" in r.broker_state["mismatch_detail"]
    finally:
        r.alpaca_broker, r.engine.broker = None, None


# ------------------------------------------------------------------ P2 #5 scheduler state off the loop
def test_slow_scheduler_persist_never_blocks_the_tick_and_is_flushed_before_orders(main_runtime, tmp_path):
    r = main_runtime
    r.state_store = TradingStateStore(str(tmp_path / "s.sqlite3"))
    try:
        h = MainOrb(r, start=at(9, 10), inline=False, freeze=False)
        store_save = r.state_store.save_orb_state
        order = []

        def slow_save(section, payload):
            if section == "scheduler":
                _time.sleep(0.3)
                order.append(("sched_saved", payload["steps"]["primary_decision"]["state"]))
            return store_save(section, payload)
        r.state_store.save_orb_state = slow_save
        real_execute = h.ctl.execute

        def execute(*a, **kw):
            order.append(("execute", None))
            return real_execute(*a, **kw)
        h.ctl.execute = execute
        h.facade.scan_results = [{"ok": True, "error": None, "coverage": 1.0,
                                  "cards": [{"symbol": "APP", "direction": "long"}], "board_id": "b"}] * 2
        h.facade.decide_results = [{"verdict": "trade", "reason": None, "picks": [pick("APP", "long", 100.0, 98.0)],
                                    "audit": []}]
        h.alpaca.prices["APP"] = 100.2
        worst = 0.0
        for t in (at(9, 15), at(9, 20), at(9, 36, 10), at(9, 38, 30), at(9, 38, 35), at(9, 38, 40)):
            h.clock.set(t)
            for _ in range(25):
                t0 = _time.perf_counter()
                r.orb.tick(h.clock.now)
                worst = max(worst, _time.perf_counter() - t0)
                _time.sleep(0.02)
        deadline = _time.monotonic() + 10
        while _time.monotonic() < deadline and "APP" not in r.account.positions:
            t0 = _time.perf_counter()
            r.orb.tick(h.clock.now)
            worst = max(worst, _time.perf_counter() - t0)
            _time.sleep(0.02)
        assert "APP" in r.account.positions
        assert worst < 0.05, worst
        i = order.index(("execute", None))
        assert ("sched_saved", "running") in order[:i]                  # the claim was durable first
    finally:
        r.orb.shutdown()
        r.state_store.close()
