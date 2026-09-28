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


def settle(r, h, rounds=40):
    for _ in range(rounds):
        r.orb.tick(h.clock.now)
        if not r.orb._rec_jobs:
            r.orb.tick(h.clock.now)
            if not r.orb._rec_jobs:
                return
        _time.sleep(0.02)


# ------------------------------------------------------------------ P1 #1 orphan positions
def test_orphan_orb_position_is_closed_orb_safely_legs_first(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    parent = h.parent()
    rebuild_off(r, h)
    assert r.orb.owns("APP") and r.orb.is_orphan("APP")
    n = len(h.alpaca.requests)
    r._trip_circuit_breaker(h.clock.now)                    # a generic path wants it closed
    assert not [o for o in r.engine.orders.values() if o.symbol == "APP" and o.strategy_id != "orb"]
    settle(r, h)
    w = h.writes_after(n)
    posts = [i for i, (m, p, b) in enumerate(w) if m == "POST"]
    deletes = [i for i, (m, p, b) in enumerate(w) if m == "DELETE"]
    assert len(posts) == 1 and deletes and max(deletes) < posts[0], w
    body = w[posts[0]][2]
    assert body["side"] == "sell" and int(body["qty"]) == 454 and body["client_order_id"].startswith("adt-orb-R-APP-")
    for leg_id in parent["legs_ids"]:
        assert h.alpaca.orders[leg_id]["status"] == "canceled"          # no naked leg left behind
    assert h.alpaca_positions() == {} and "APP" not in r.account.positions
    assert h.alpaca.refused_403 == [] and not r.orb.alerts
    trade = next(t for t in r.pending_trade_records.values() if t["trade_id"].startswith("orb_recovery_APP"))
    assert trade["strategy_id"] == "orb"


def test_unprovable_orphan_is_never_liquidated_and_raises_a_plain_alert(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    r.account.apply_fill("x", "APP", "BUY", 46, 100.0, 0.0, h.clock.now, strategy_id="orb")   # 500 != 454
    rebuild_off(r, h)
    n = len(h.alpaca.requests)
    asyncio.run(r._execute_manual_flatten(["APP"], h.clock.now, None))
    settle(r, h)
    assert h.writes_after(n) == []                                    # nothing sent at all
    assert not [o for o in r.engine.orders.values() if o.symbol == "APP" and o.strategy_id != "orb"]
    alert = r.orb.alerts["APP"]
    assert "cannot prove" in alert and "Nothing was sent" in alert
    card = next(c for c in r._strategy_cards(h.clock.now) if c["id"] == "orb")
    assert alert in card["orb"]["alerts"]
    assert asyncio.run(r.get_health())["orb"]["alerts"] == [alert]
    o = r.engine.create_order("APP", r.OrderSide.SELL, r.OrderType.MARKET, 500, strategy_id="AUTO_FLATTEN")
    ok, why = r.pre_trade_risk_validator(o, r.account)
    assert not ok and why.startswith("ORB_OWNED")


def test_live_adt_orb_orders_nobody_tracks_are_cancelled(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.alpaca.entry_mode = "new"                                       # the entry never fills
    h.open_bracket()
    parent = h.parent()
    rebuild_off(r, h)
    rep = h.ctl.reconcile_on_startup()
    assert rep["unknown_orders"] and not rep["ok"]
    settle(r, h)
    assert h.alpaca.orders[parent["id"]]["status"] == "canceled"
    assert not [q for q in h.alpaca.requests if q[0] == "POST" and q[2].get("client_order_id", "").startswith("adt-orb-R")]
    assert h.ctl.reconcile_on_startup()["ok"]                          # ORB can trade again


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
