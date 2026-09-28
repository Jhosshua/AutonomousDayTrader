"""Restart: rebuild ORB's own book from the checkpoint + Alpaca by our ids before any entry."""
from datetime import timedelta

from backend.tests.unit.orb_execution.fakes import Harness, at, pick


def opened():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    assert h.ctl.execute([pick("APP", "long", 100.0, 98.0)])["ok"]
    return h


def test_restart_mid_trade_books_a_fill_that_happened_while_down_exactly_once():
    h = opened()
    parent = h.parent()
    h.alpaca.fill(h.alpaca.leg(parent["id"], "tp")["id"], price=101.85)   # target hit while we were down
    ctl = h.restart()
    assert not ctl.ready
    rep = ctl.reconcile_on_startup()
    assert rep["ok"] and ctl.ready
    assert [f[5] for f in h.fills] == ["entry", "target"]       # entry not re-emitted, target once
    ctl.tick()
    assert h.pos()["status"] == "CLOSED" and h.pos()["closed_reason"] == "target"
    assert h.own_fill_sum() == 0


def test_restart_mid_trade_keeps_supervising_and_reserves_the_symbol_again():
    h = opened()
    ctl = h.restart()
    assert ctl.reconcile_on_startup()["ok"]
    assert "APP" in h.reserved and ctl.owns("APP")
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    ctl.tick()
    assert h.pos()["closed_reason"] == "fast-fail" and h.own_fill_sum() == 0
    assert h.alpaca.refused_403 == []


def test_restart_mid_exit_finds_the_exit_by_coid_and_never_sends_a_second_one():
    h = opened()
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost"})
    h.alpaca.fail.append({"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status",
                          "status": 503, "times": 3})
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    h.ctl.tick()                                  # the exit POST reply is lost: outcome unknown
    exit_rec = [r for r in h.ctl.state["orders"].values() if r["role"] == "exit"]
    assert len(exit_rec) == 1 and exit_rec[0]["id"] is None
    assert any(r["role"] == "exit" for r in h.persisted[-1]["orders"].values())   # recorded before the POST
    ctl = h.restart()
    assert ctl.reconcile_on_startup()["ok"]
    ctl.tick()
    assert h.pos()["status"] == "CLOSED" and h.own_fill_sum() == 0
    sells = [r for r in h.alpaca.requests if r[0] == "POST" and r[2].get("side") == "sell"]
    assert len(sells) == 1


def test_restart_between_intent_and_post_result_resolves_by_coid():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost"})
    h.alpaca.fail.append({"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "transport", "times": 3})
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    ctl = h.restart()
    rep = ctl.reconcile_on_startup()
    assert rep["ok"]
    assert h.pos()["status"] == "OPEN" and ctl.own_qty("APP") == 454


def test_unknown_own_prefix_order_at_alpaca_is_reported_never_adopted():
    h = opened()
    h.alpaca.entry_mode = "new"
    stray = h.broker.submit_bracket("HOOD", 10, "buy", 40, 20, "adt-orb-HOOD-2026-09-28-w1-a1-ffffff")
    ctl = h.restart()
    rep = ctl.reconcile_on_startup()
    assert not rep["ok"] and not ctl.ready
    assert [u["client_order_id"] for u in rep["unknown_orders"]] == ["adt-orb-HOOD-2026-09-28-w1-a1-ffffff"]
    assert not ctl.owns("HOOD")
    assert h.alpaca.orders[stray["id"]]["status"] == "new"      # not touched
    h.alpaca.prices["PLTR"] = 50.1
    assert "reconciliation" in ctl.execute([pick("PLTR", "long", 50.0, 48.9)])["reason"]
    # exits of provable positions still work while entries are refused
    h.clock.set(at(11, 0))
    ctl.tick()
    assert h.pos()["status"] == "CLOSED"
    assert h.alpaca.orders[stray["id"]]["status"] == "new"


def test_broker_unreachable_at_startup_keeps_entries_refused_until_a_clean_pass():
    h = opened()
    ctl = h.restart()
    h.alpaca.fail.append({"method": "GET", "path": "/v2/orders", "kind": "transport", "times": 10})
    assert not ctl.reconcile_on_startup()["ok"]
    h.alpaca.fail.clear()
    assert ctl.reconcile_on_startup()["ok"]


def test_position_carried_into_the_next_day_exits_at_the_next_pass():
    h = opened()
    h.alpaca.market_fills = True
    ctl = h.restart()
    next_day = at(8, 0) + timedelta(days=1)
    h.clock.set(next_day)
    rep = ctl.reconcile_on_startup()
    assert rep["carried"] == ["APP"]
    ctl.tick()
    p = h.pos()
    assert p["status"] == "CLOSED" and p["closed_reason"] == "carried_over"
    assert h.own_fill_sum() == 0


def test_fill_merges_are_monotonic_a_stale_read_changes_nothing():
    h = opened()
    parent = h.parent()
    stale = dict(h.alpaca.view(parent), status="partially_filled", filled_qty="200", filled_avg_price="100.2")
    n_fills = len(h.fills)
    for _ in range(3):
        h.ctl._merge(stale)
        h.ctl.refresh_own_orders()
    assert h.ctl.own_qty("APP") == 454 and len(h.fills) == n_fills
    rec = h.ctl.state["orders"][h.pos()["coid"]]
    assert rec["filled_qty"] == 454 and rec["status"] == "filled"


def test_fill_without_a_price_is_booked_on_the_next_priced_read():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.entry_mode = "new"
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    parent = h.parent()
    h.ctl._merge(dict(h.alpaca.view(parent), status="filled", filled_qty="454", filled_avg_price=None))
    assert h.ctl.own_qty("APP") == 0 and not h.ctl.state["orders"][h.pos()["coid"]]["terminal"]
    h.alpaca.fill(parent["id"], price=100.3)
    h.clock.advance(5)
    h.ctl.tick()
    assert h.ctl.own_qty("APP") == 454 and h.fills[-1][3] == 100.3


def test_state_round_trip_is_json_and_versioned():
    import json
    h = opened()
    s = h.ctl.to_state()
    json.dumps(s)
    ctl = h.build("live", json.loads(json.dumps(s)))
    assert ctl.own_qty("APP") == 454 and ctl.state["positions"] == s["positions"]
    try:
        h.build("live", dict(s, version=99))
        assert False, "a future state version must be refused"
    except ValueError:
        pass
