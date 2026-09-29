"""Codex's reproductions on 0eb8a96 (exit path), one test each."""
import threading
import time

import pytest

from backend.tests.unit.orb_attack._util import at, bracket_posts, live_stop_at_broker, opened, pick, posts
from backend.tests.unit.orb_execution.fakes import Harness


def _sells(h):
    return [o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]


# 2. write-off only on a symbol-specific flat confirmation
@pytest.mark.parametrize("snapshot", [[], [{"symbol": "APP"}], [{"symbol": "MSFT", "qty": "5"}]],
                         ids=["empty", "malformed-entry", "other-symbol-only"])
def test_a_bulk_snapshot_without_the_symbol_never_writes_orb_shares_off(snapshot):
    h = opened()
    for leg in h.parent()["legs_ids"]:                       # legs gone (e.g. cancelled by hand) ...
        h.alpaca.orders[leg]["status"] = "canceled"
    assert h.alpaca._pos_qty("APP") == 454                   # ... but the shares are still there
    h.broker.get_positions_raw = lambda: list(snapshot)       # a partial / malformed bulk read
    for _ in range(4):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.ctl.own_qty("APP") == 454 and h.pos()["status"] == "OPEN"
    assert not any(e["kind"] == "exit_capped_outside_trade" for e in h.ctl.state["events"])


def test_a_symbol_specific_flat_read_does_write_off_a_manual_close():
    h = opened()
    for leg in h.parent()["legs_ids"]:
        h.alpaca.orders[leg]["status"] = "canceled"
    h.alpaca._apply_position("APP", "sell", 454, 100.0)      # closed by hand at Alpaca
    h.broker.get_positions_raw = lambda: []                   # the bulk read says nothing about APP
    for _ in range(2):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and h.ctl.own_qty("APP") == 0 and not posts(h, side="sell")


# 3. flatten-all and entry admission are mutually exclusive through the POST
def test_flatten_all_during_an_entry_post_waits_for_it_then_exits_the_new_position():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    real = h.broker.submit_bracket
    in_post, release = threading.Event(), threading.Event()

    def slow_bracket(*a, **kw):
        in_post.set()
        release.wait(5)
        return real(*a, **kw)
    h.broker.submit_bracket = slow_bracket
    ex = threading.Thread(target=lambda: h.ctl.execute([pick("APP", "long", 100.0, 98.0)]))
    ex.start()
    assert in_post.wait(5)
    fl = threading.Thread(target=lambda: h.ctl.request_all_exits("MANUAL_FLATTEN_ALL", block_entries=True))
    fl.start()
    time.sleep(0.2)
    assert fl.is_alive() and h.ctl.entries_blocked() is None      # waits for the entry's POST
    release.set()
    ex.join(5)
    fl.join(5)
    assert h.ctl.entries_blocked() == "MANUAL_FLATTEN_ALL" and "APP" in h.ctl.state["exit_requests"]
    for _ in range(3):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.alpaca._pos_qty("APP") == 0 and h.alpaca.refused_403 == []


def test_a_halt_during_an_entry_post_waits_for_it():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    real = h.broker.submit_bracket
    in_post, release = threading.Event(), threading.Event()

    def slow_bracket(*a, **kw):
        in_post.set()
        release.wait(5)
        return real(*a, **kw)
    h.broker.submit_bracket = slow_bracket
    ex = threading.Thread(target=lambda: h.ctl.execute([pick("APP", "long", 100.0, 98.0)]))
    ex.start()
    assert in_post.wait(5)
    ht = threading.Thread(target=lambda: h.ctl._latch_halt("test halt", -3.1))
    ht.start()
    time.sleep(0.2)
    assert ht.is_alive() and not h.ctl.halted()
    release.set()
    ex.join(5)
    ht.join(5)
    assert h.ctl.halted() == "test halt"


# 4. an ambiguous 429 keeps the original client id and is reconciled before any new exit
def test_exit_429_after_the_broker_accepted_it_never_sells_twice():
    h = opened()
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status_after", "status": 429})
    h.alpaca.prices["APP"] = 99.30
    for _ in range(4):
        h.clock.advance(5)
        h.ctl.tick()
    assert len(_sells(h)) == 1 and h.alpaca._pos_qty("APP") == 0
    assert h.alpaca.refused_403 == [] and h.own_fill_sum() == 0
    # the 429'd order is ORB's own exit, found by its client id: never "written off as an outside trade"
    assert not any(e["kind"] == "exit_capped_outside_trade" for e in h.ctl.state["events"])
    assert [f[5] for f in h.fills] == ["entry", "exit"]


def test_exit_429_that_created_nothing_is_retried_promptly():
    h = opened()
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 429})
    h.alpaca.prices["APP"] = 99.30
    for _ in range(3):                                         # 15 s, well inside the 60 s grace
        h.clock.advance(5)
        h.ctl.tick()
    assert h.alpaca._pos_qty("APP") == 0 and len([o for o in _sells(h) if o["status"] == "filled"]) == 1


# 5. the in-flight mark is a hard barrier
def test_no_exit_post_when_the_in_flight_mark_cannot_be_saved_and_the_latch_retries():
    h = opened()
    fail_once = [True]

    def hook(state):
        if fail_once[0] and any(r.get("role") == "exit" and r.get("submit_state") == "in_flight"
                                for r in state["orders"].values()):
            fail_once[0] = False
            raise OSError("disk full at the in-flight mark")
    h.on_persist = hook
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    h.ctl.tick()
    assert not posts(h, n, "sell") and h.pos()["exit_latched"]           # nothing sent after the failure
    h.alpaca.prices["APP"] = 100.2                                       # price recovers: still retried
    h.clock.advance(5)
    h.ctl.tick()
    assert len(posts(h, n, "sell")) == 1 and h.alpaca._pos_qty("APP") == 0


def test_a_latched_exit_that_cannot_close_raises_escalating_alarms():
    from backend.app.core.broker import BrokerHTTPError
    h = opened()

    def refuse(*a, **kw):
        raise BrokerHTTPError("refused (test)", 422)
    h.broker.submit_market_order = refuse
    h.alpaca.prices["APP"] = 99.30
    for _ in range(26):                                                  # 130 s
        h.clock.advance(5)
        h.ctl.tick()
    alarms = [k for k in h.ctl.state["alarms"] if "exit_latched_APP" in k]
    assert any(k.endswith("_1") for k in alarms) and any(k.endswith("_2") for k in alarms)
    assert not any(o["type"] == "stop" and o["client_order_id"].startswith("adt-orb-S-")
                   for o in h.alpaca.orders.values())                    # no re-armed stop any more


def test_exit_5xx_that_did_create_the_order_is_never_written_off_while_lookups_lag():
    """A 504 whose order WAS created, and the first client-id lookups still 404: the record must stay
    unresolved (not terminal), so the next pass finds the order by its client id and books its fill as
    ORB's own exit, instead of writing the shares off as an outside trade."""
    h = opened()
    h.alpaca.fail += [{"method": "POST", "path": "/v2/orders", "kind": "status_after", "status": 504},
                      {"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status", "status": 404,
                       "times": 3}]
    h.alpaca.prices["APP"] = 99.30
    for _ in range(3):
        h.clock.advance(5)
        h.ctl.tick()
    assert len(_sells(h)) == 1 and h.alpaca._pos_qty("APP") == 0 and h.own_fill_sum() == 0
    assert not any(e["kind"] == "exit_capped_outside_trade" for e in h.ctl.state["events"])
