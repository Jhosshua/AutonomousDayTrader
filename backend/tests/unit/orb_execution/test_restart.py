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


def lost_entry_marked_never_reached():
    """An entry whose POST reply was lost and whose by-coid lookups kept answering 404 until the
    grace ran out: ORB concluded it never reached the broker. It actually did (and filled)."""
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost"})
    h.alpaca.fail.append({"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status",
                          "status": 404, "times": 4})
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    h.clock.advance(70)
    h.ctl.tick()
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and "never reached" in h.pos()["closed_reason"]
    assert h.ctl.own_qty("APP") == 0 and "APP" in h.alpaca.positions      # the broker holds 454
    assert h.alpaca.fail[-1]["times"] == 0                                # the next lookup finds it
    return h


def test_revived_entry_after_restart_is_reopened_with_its_stop_and_supervised():
    h = lost_entry_marked_never_reached()
    ctl = h.restart()
    rep = ctl.reconcile_on_startup()
    assert rep["ok"]
    p = h.pos()
    assert p["status"] == "OPEN" and p.get("reopened") and p["rd"] == 2.2   # its own stop/R, not a guess
    assert ctl.own_qty("APP") == 454 and h.own_fill_sum() == 454
    assert "APP" in h.reserved and ctl.owns("APP")
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.30          # r = -0.41: the normal software exits apply again
    h.clock.advance(5)
    ctl.tick()
    assert h.pos()["closed_reason"] == "fast-fail" and h.own_fill_sum() == 0
    assert h.alpaca.refused_403 == [] and n < len(h.alpaca.requests)


def test_revived_entry_found_late_without_a_restart_is_flattened_at_eleven():
    h = lost_entry_marked_never_reached()
    h.clock.advance(61)
    h.ctl.tick()                            # the periodic by-coid recheck finds it
    assert h.pos()["status"] == "OPEN" and h.ctl.own_qty("APP") == 454
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert h.pos()["closed_reason"] == "flatten" and h.own_fill_sum() == 0


def test_own_shares_with_no_position_record_are_rebuilt_and_exited():
    h = opened()
    st = h.persisted[-1]
    st = dict(st, positions={})             # the position row was lost, the own orders were not
    h.persisted.append(st)
    ctl = h.restart()
    assert ctl.reconcile_on_startup()["ok"]
    h.clock.advance(5)
    ctl.tick()
    closed = [p for p in ctl.state["positions"].values() if p["symbol"] == "APP"]
    assert closed and closed[0]["closed_reason"] == "no_known_stop" and h.own_fill_sum() == 0
    assert h.alpaca.refused_403 == [] and "APP" not in h.alpaca.positions


def test_wrong_account_fails_closed_at_startup_freeze_and_execute():
    h = Harness(freeze=False, reconcile=False, expected_account="PA-SOMEONE-ELSE")
    rep = h.ctl.reconcile_on_startup()
    assert not rep["ok"] and not h.ctl.ready and "destination refused" in rep["errors"][0]
    assert any(k.endswith("wrong_account") for k in h.ctl.state["alarms"])
    h.clock.set(at(9, 20))
    ok, why = h.ctl.freeze_session()
    assert not ok and "destination refused" in why and h.ctl.session_sizing() is None
    # right account at startup, then the keys point somewhere else before the order
    h2 = Harness()
    h2.alpaca.prices["APP"] = 100.2
    h2.alpaca.account["account_number"] = "PA-SOMEONE-ELSE"
    out = h2.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and "destination refused" in out["reason"]
    assert h2.alpaca.writes == []


def test_no_pinned_account_means_no_trading():
    h = Harness(freeze=False, reconcile=False, expected_account=None)
    assert not h.ctl.reconcile_on_startup()["ok"]
    h.clock.set(at(9, 20))
    assert not h.ctl.freeze_session()[0]


def test_startup_reconcile_and_a_supervisor_tick_never_interleave():
    import threading
    h = opened()
    ctl = h.restart()
    inside, go = threading.Event(), threading.Event()

    def slow_reserve(sym):
        inside.set()
        go.wait(5)
        return True
    ctl.reserve = slow_reserve
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("rep", ctl.reconcile_on_startup()))
    t.start()
    try:
        assert inside.wait(5)
        res = ctl.tick()                     # arrives while reconcile holds the book
        assert res == {"skipped": "another supervisor pass is running"}
    finally:
        go.set()
        t.join(5)
    assert out["rep"]["ok"] and ctl.tick() != {"skipped": "another supervisor pass is running"}
