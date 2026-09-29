"""Attack: make an ORB exit leave the position naked, double-sell, or cross flat.

Every software exit cancels ORB's bracket legs FIRST and only then sends the market close. Anything that
stops the close after the cancel (disk, request budget, a 5xx on the position read, a 429, a crash) must not
leave shares at Alpaca with no stop and no exit. SAFE outcome asserted: after the failed exit, the account is
flat for the symbol OR a working protective stop exists at the broker, and ORB never sells more than it owns."""
import pytest

from backend.tests.unit.orb_attack._util import (
    SwitchBudget, at, bracket_posts, live_stop_at_broker, opened, pick, posts, protected_or_flat, writes_after,
)
from backend.tests.unit.orb_execution.fakes import Harness


def _fast_fail(h):
    h.alpaca.prices["APP"] = 99.30          # r = (99.30 - 100.2) / 2.2 = -0.41 -> fast-fail
    h.clock.advance(5)
    h.ctl.tick()


def _recover_and_supervise(h, passes=6):
    h.alpaca.prices["APP"] = 100.2          # price back at entry: no software exit rule fires any more
    for _ in range(passes):
        h.clock.advance(5)
        h.ctl.tick()


# ----------------------------------------------------------------------------- persistence
def test_disk_failure_during_an_exit_never_leaves_the_shares_naked():
    h = opened()
    h.persist_raises[0] = True              # the durable store starts failing (disk full / db locked)
    _fast_fail(h)
    assert protected_or_flat(h), "legs cancelled and no exit sent: position is naked"
    for _ in range(6):
        h.clock.advance(5)
        h.ctl.tick()
    assert protected_or_flat(h)


def test_disk_failure_before_an_entry_post_sends_nothing():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.persist_raises[0] = True
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and not h.alpaca.writes
    assert not h.ctl.owns("APP") and "APP" not in h.reserved


# ----------------------------------------------------------------------------- request budget
def test_budget_exhausted_between_the_leg_cancels_and_the_close_never_leaves_it_naked():
    h = Harness(reconcile=False, freeze=False)
    budget = SwitchBudget()
    h.ctl = h.build("live")
    h.ctl.budget = budget
    h.clock.set(at(9, 20))
    assert h.ctl.freeze_session(h.clock.now)[0] and h.ctl.reconcile_on_startup()["ok"]
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    assert h.ctl.execute([pick("APP", "long", 100.0, 98.0)])["ok"]
    real_cancel = h.broker.cancel_order_and_confirm
    n_cancel = []

    def cancel(oid, timeout=6.0):
        out = real_cancel(oid, timeout)
        n_cancel.append(oid)
        if len(n_cancel) == 2:               # both legs gone: the budget runs dry right now
            budget.exhausted = True
        return out
    h.broker.cancel_order_and_confirm = cancel
    _fast_fail(h)
    assert len(n_cancel) == 2
    assert protected_or_flat(h), "both legs cancelled, exit throttled: position is naked"


def test_budget_exhausted_before_the_exit_touches_nothing():
    h = opened()
    budget = SwitchBudget()
    h.ctl.budget = budget
    budget.exhausted = True
    n = len(h.alpaca.requests)
    _fast_fail(h)
    assert not writes_after(h, n) and live_stop_at_broker(h)


# ----------------------------------------------------------------------------- broker errors at the close
def test_position_read_5xx_at_the_close_then_price_recovers_leaves_no_naked_position():
    h = opened()
    h.alpaca.fail.append({"method": "GET", "path": "/v2/positions/APP", "kind": "status", "status": 503})
    _fast_fail(h)
    _recover_and_supervise(h)
    assert protected_or_flat(h), "legs cancelled, exit aborted, price recovered: position held naked"


def test_exit_post_429_is_resolved_within_the_grace_window_and_sells_once():
    """Final Codex pass (2026-09-28): a 429 stays unresolved through the 60 s grace (lookup visibility can
    lag; closing it early risked a second exit crossing flat). ACCEPTED trade-off: the legs are already
    cancelled, so the shares are unprotected for up to the grace window; escalating alarms fire."""
    h = opened()
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 429})
    _fast_fail(h)
    for _ in range(15):                    # 75 s of supervisor passes
        h.clock.advance(5)
        h.ctl.tick()
    assert protected_or_flat(h) and len(posts(h, side="sell")) == 2   # the 429'd one and exactly one retry
    assert h.alpaca.refused_403 == [] and h.own_fill_sum() == 0


def test_exit_post_503_storm_never_double_sells_or_crosses_flat():
    h = opened()
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 503, "times": 2})
    _fast_fail(h)
    for _ in range(40):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.alpaca._pos_qty("APP") == 0
    assert h.alpaca.refused_403 == []
    filled_sells = [o for o in h.alpaca.orders.values()
                    if o["client_order_id"].startswith("adt-orb-X-") and o["status"] == "filled"]
    assert sum(int(o["filled_qty"]) for o in filled_sells) == 454
    assert h.own_fill_sum() == 0 and h.pos()["status"] == "CLOSED"


def test_exit_reply_lost_every_time_sells_exactly_once():
    h = opened()
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost", "times": 3})
    _fast_fail(h)
    for _ in range(10):
        h.clock.advance(5)
        h.ctl.tick()
    sells = [o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]
    assert len(sells) == 1 and h.alpaca._pos_qty("APP") == 0 and h.alpaca.refused_403 == []


# ----------------------------------------------------------------------------- crash mid-exit
def test_kill_between_leg_cancel_and_close_then_restart_is_not_naked():
    h = opened()

    def die(*a, **kw):
        raise KeyboardInterrupt("process killed mid-exit")
    h.ctl._submit_exit = die
    with pytest.raises(KeyboardInterrupt):
        _fast_fail(h)
    assert not live_stop_at_broker(h) and h.alpaca._pos_qty("APP") == 454
    ctl = h.restart()
    assert ctl.reconcile_on_startup()["ok"]
    _recover_and_supervise(h)
    assert protected_or_flat(h)


def test_kill_after_the_close_post_restart_books_it_and_never_sells_again():
    h = opened()
    real = h.broker.submit_market_order

    def post_then_die(*a, **kw):
        real(*a, **kw)
        raise KeyboardInterrupt("process killed right after the close POST")
    h.broker.submit_market_order = post_then_die
    with pytest.raises(KeyboardInterrupt):
        _fast_fail(h)
    ctl = h.restart()
    assert ctl.reconcile_on_startup()["ok"]
    for _ in range(3):
        h.clock.advance(5)
        ctl.tick()
    assert h.alpaca._pos_qty("APP") == 0 and len(posts(h, side="sell")) == 1
    assert h.own_fill_sum() == 0 and h.pos()["status"] == "CLOSED"


# ----------------------------------------------------------------------------- both legs / races
def test_target_and_stop_both_filling_is_bought_back_to_flat():
    h = opened()
    parent = h.parent()
    tp, sl = h.alpaca.leg(parent["id"], "tp"), h.alpaca.leg(parent["id"], "sl")
    h.alpaca.fill(tp["id"], price=101.85)
    sl["status"] = "new"                     # the OCO did not cancel the sibling in time
    h.alpaca.fill(sl["id"], price=97.95)
    assert h.alpaca._pos_qty("APP") == -454
    h.alpaca.prices["APP"] = 99.0            # rallies 1R against the accidental short (no ORB halt yet)
    for _ in range(4):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.alpaca._pos_qty("APP") == 0, "accidental short left open"


def test_stop_leg_partial_fill_racing_the_cancel_closes_only_the_rest():
    h = opened()
    sl = h.alpaca.leg(h.parent()["id"], "sl")
    h.alpaca.fill_on_cancel[sl["id"]] = (200, 99.30)
    n = len(h.alpaca.requests)
    _fast_fail(h)
    sells = [b for b in posts(h, n, "sell")]
    assert len(sells) == 1 and int(sells[0]["qty"]) == 254
    assert h.alpaca._pos_qty("APP") == 0 and h.alpaca.refused_403 == [] and h.own_fill_sum() == 0


def test_partial_parent_fill_then_rest_then_partial_target_then_fast_fail():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.entry_mode = "partial"
    h.alpaca.partial_qty = 200
    assert h.ctl.execute([pick("APP", "long", 100.0, 98.0)])["ok"]
    h.clock.advance(5)
    h.ctl.tick()
    parent = h.parent()
    h.alpaca.fill(parent["id"])                          # the rest of the parent fills
    tp = h.alpaca.leg(parent["id"], "tp")
    h.alpaca.fill(tp["id"], cum_qty=300, price=101.85)    # target partially fills
    h.clock.advance(11)
    h.ctl.tick()
    assert h.ctl.own_qty("APP") == 154
    n = len(h.alpaca.requests)
    _fast_fail(h)
    sells = posts(h, n, "sell")
    assert len(sells) == 1 and int(sells[0]["qty"]) == 154
    assert h.alpaca._pos_qty("APP") == 0 and h.alpaca.refused_403 == [] and h.own_fill_sum() == 0


def test_breakeven_patch_reply_lost_then_breakeven_exit_is_clean():
    h = opened()
    h.alpaca.fail.append({"method": "PATCH", "path": "/v2/orders/", "kind": "lost"})
    h.alpaca.prices["APP"] = 101.9           # +0.77R: breakeven arms, PATCH goes out, reply lost
    h.clock.advance(5)
    h.ctl.tick()
    h.alpaca.prices["APP"] = 100.1           # back below entry: breakeven exit
    h.clock.advance(5)
    h.ctl.tick()
    h.clock.advance(5)
    h.ctl.tick()
    assert h.alpaca._pos_qty("APP") == 0 and h.alpaca.refused_403 == []
    assert not [o for o in h.alpaca.orders.values() if o["status"] in ("new", "held", "accepted")]
    assert len(posts(h, side="sell")) == 1 and h.own_fill_sum() == 0


# ----------------------------------------------------------------------------- manual trades at Alpaca
def test_manual_buy_on_the_same_symbol_is_never_sold_by_orb():
    h = opened()
    h.alpaca._post({"client_order_id": "manual-1", "symbol": "APP", "side": "buy", "qty": "100",
                    "type": "market", "time_in_force": "day"})
    assert h.alpaca._pos_qty("APP") == 554
    n = len(h.alpaca.requests)
    _fast_fail(h)
    sells = posts(h, n, "sell")
    assert len(sells) == 1 and int(sells[0]["qty"]) == 454
    assert h.alpaca._pos_qty("APP") == 100 and h.own_fill_sum() == 0


def test_manual_close_at_alpaca_never_makes_orb_cross_flat():
    """The operator closes APP in Alpaca's UI (legs cancelled, shares sold by a non-ORB order)."""
    h = opened()
    for leg in h.parent()["legs_ids"]:
        h.alpaca.orders[leg]["status"] = "canceled"
    h.alpaca._post({"client_order_id": "manual-close", "symbol": "APP", "side": "sell", "qty": "454",
                    "type": "market", "time_in_force": "day"})
    assert h.alpaca._pos_qty("APP") == 0
    n = len(h.alpaca.requests)
    for _ in range(4):
        h.clock.advance(5)
        h.ctl.tick()
    h.alpaca.prices["APP"] = 99.0            # a software exit fires on the phantom position
    for _ in range(2):
        h.clock.advance(5)
        h.ctl.tick()
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert not posts(h, n), "ORB sent an order for shares it no longer has"
    assert h.alpaca._pos_qty("APP") == 0 and h.ctl.own_qty("APP") == 0


def test_manual_close_at_alpaca_is_noticed_without_waiting_for_a_price_rule():
    h = opened()
    for leg in h.parent()["legs_ids"]:
        h.alpaca.orders[leg]["status"] = "canceled"
    h.alpaca._post({"client_order_id": "manual-close", "symbol": "APP", "side": "sell", "qty": "454",
                    "type": "market", "time_in_force": "day"})
    for _ in range(6):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and not h.ctl.owns("APP")
