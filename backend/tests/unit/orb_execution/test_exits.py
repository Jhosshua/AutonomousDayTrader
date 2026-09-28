"""ORB exits: broker-side bracket fills and the supervisor's software exits (core._supervise /
exit_own semantics). Every software exit must cancel ORB's own orders first, then close exactly
ORB's own quantity with one market order, never tripping Alpaca's one-sell-order rule."""
import pytest

from backend.tests.unit.orb_execution.fakes import Harness, at, pick

DAY = "2026-09-28"


def opened(sym="APP", entry_px=100.2, card=(100.0, 98.0), direction="long", **kw):
    h = Harness(**kw)
    h.alpaca.prices[sym] = entry_px
    out = h.ctl.execute([pick(sym, direction, *card)])
    assert out["ok"], out
    return h


def writes_after(h, n):
    return [(m, p, b) for m, p, b, _q in h.alpaca.requests[n:] if m in ("POST", "PATCH", "DELETE")]


def assert_cancel_then_close(h, start, sym, qty, side="sell"):
    w = writes_after(h, start)
    posts = [i for i, (m, p, b) in enumerate(w) if m == "POST"]
    deletes = [i for i, (m, p, b) in enumerate(w) if m == "DELETE"]
    assert len(posts) == 1, w
    body = w[posts[0]][2]
    assert body["type"] == "market" and body["side"] == side and int(body["qty"]) == qty
    assert body["time_in_force"] == "day" and "order_class" not in body
    assert body["client_order_id"].startswith(f"adt-orb-X-{sym}-{DAY}-1-")
    assert deletes and max(deletes) < posts[0]              # every cancel before the close
    assert not any(p.startswith("/v2/positions") for m, p, b in w)   # never DELETE /positions
    assert h.alpaca.refused_403 == []


def test_target_fill_at_the_broker_closes_the_position():
    h = opened()
    parent = h.parent()
    h.alpaca.fill(h.alpaca.leg(parent["id"], "tp")["id"], price=101.85)
    h.clock.advance(11)
    h.ctl.tick()
    p = h.pos()
    assert p["status"] == "CLOSED" and p["closed_reason"] == "target"
    assert h.ctl.own_qty("APP") == 0 and h.own_fill_sum() == 0
    assert [f[5] for f in h.fills] == ["entry", "target"]
    assert len([r for r in h.alpaca.requests if r[0] == "POST"]) == 1        # no exit order sent
    assert h.ctl.state["realized"][DAY]["pnl"] == pytest.approx(454 * (101.85 - 100.2), abs=0.01)
    assert "APP" in h.released and not h.ctl.owns("APP")


def test_stop_fill_at_the_broker_closes_the_position():
    h = opened()
    parent = h.parent()
    h.alpaca.fill(h.alpaca.leg(parent["id"], "sl")["id"], price=97.95)
    h.clock.advance(11)
    h.ctl.tick()
    assert h.pos()["closed_reason"] == "stop"
    assert h.ctl.state["realized"][DAY]["pnl"] == pytest.approx(454 * (97.95 - 100.2), abs=0.01)
    assert [f[5] for f in h.fills] == ["entry", "stop"]


def test_fast_fail_cancels_legs_then_sells_exact_own_qty():
    h = opened()
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.30          # r = (99.30 - 100.2) / 2.2 = -0.41
    h.clock.advance(5)
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", 454)
    p = h.pos()
    assert p["status"] == "CLOSED" and p["closed_reason"] == "fast-fail"
    assert "APP" not in h.alpaca.positions and h.own_fill_sum() == 0


def test_no_exit_above_fast_fail():
    h = opened()
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.35          # r = -0.386
    h.clock.advance(5)
    h.ctl.tick()
    assert writes_after(h, n) == [] and h.pos()["status"] == "OPEN"


def test_short_fast_fail_buys_back():
    h = opened(entry_px=313.03, card=(313.5, 326.64), direction="short")
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 318.60         # r = -(318.60-313.03)/13.61 = -0.41
    h.clock.advance(5)
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", int(1000 // 13.61), side="buy")
    assert h.pos()["closed_reason"] == "fast-fail"


def test_breakeven_patches_the_stop_leg_then_exits_at_or_below_entry():
    h = opened()
    parent = h.parent()
    h.alpaca.prices["APP"] = 101.90         # r = 0.77
    h.clock.advance(5)
    h.ctl.tick()
    p = h.pos()
    assert p["be_triggered"] and p["be_locked"] and p["stop"] == 100.2
    sl = h.alpaca.leg(parent["id"], "sl")
    assert sl["stop_price"] == "100.20"                     # the replacement leg at the broker
    assert any(r["id"] == sl["id"] for r in h.ctl.state["orders"].values())   # and it is ours
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 100.15         # r < 0
    h.clock.advance(5)
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", 454)
    assert h.pos()["closed_reason"] == "breakeven"


def test_breakeven_patch_422_on_a_held_leg_still_exits_by_software():
    h = opened()
    parent = h.parent()
    for lid in parent["legs_ids"]:
        h.alpaca.orders[lid]["status"] = "held"     # paper: PATCH of a held leg answers 422
    h.alpaca.prices["APP"] = 101.90
    h.clock.advance(5)
    h.ctl.tick()
    p = h.pos()
    assert p["be_triggered"] and not p["be_locked"]
    assert any(e["kind"] == "breakeven_unconfirmed" for e in h.ctl.state["events"])
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 100.20         # r = 0
    h.clock.advance(5)
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", 454)
    assert h.pos()["closed_reason"] == "breakeven"


def test_clawback_after_peak_giveback():
    h = opened()
    h.alpaca.prices["APP"] = 101.60         # r = 0.636 (peak >= 0.60, below breakeven 0.75)
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "OPEN"
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 101.00         # r = 0.364 <= 0.636 - 0.25 and > 0
    h.clock.advance(5)
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", 454)
    assert h.pos()["closed_reason"] == "clawback"


def read(h, fire, age_s=0.0, ratio=-0.42):
    """A finished absorption read whose 30 s window ended age_s seconds ago."""
    from datetime import timedelta
    return {"fire": fire, "direction": "long", "ratio": ratio, "trades": 31,
            "asof": h.clock.now - timedelta(seconds=age_s)}


def test_absorption_exit_needs_arm_and_a_fresh_firing_read():
    h = opened()
    h.facade.absorption["APP"] = read(h, True)
    h.alpaca.prices["APP"] = 101.00         # r = 0.36: not armed, no read asked for
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "OPEN"
    assert not [c for c in h.facade.calls if c[0] == "absorption_poll"]
    h.alpaca.prices["APP"] = 101.40         # r = 0.545: armed, read does not fire
    h.clock.advance(5)
    h.facade.absorption["APP"] = read(h, False, ratio=-0.1)
    h.ctl.tick()
    assert h.pos()["status"] == "OPEN"
    n = len(h.alpaca.requests)
    h.clock.advance(5)
    h.facade.absorption["APP"] = read(h, True, age_s=3)
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", 454)
    assert h.pos()["closed_reason"] == "absorption"


@pytest.mark.parametrize("asof", ["stale", "undated", "future"])
def test_a_stale_or_undated_absorption_read_never_exits(asof):
    h = opened()
    h.alpaca.prices["APP"] = 101.40         # armed (r = 0.545)
    h.clock.advance(5)
    ab = read(h, True, age_s={"stale": 8.5, "future": -2}.get(asof, 0))
    if asof == "undated":
        ab.pop("asof")
    h.facade.absorption["APP"] = ab
    n = len(h.alpaca.requests)
    h.ctl.tick()
    assert writes_after(h, n) == [] and h.pos()["status"] == "OPEN"


def test_eleven_oclock_flatten():
    h = opened()
    n = len(h.alpaca.requests)
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", 454)
    assert h.pos()["closed_reason"] == "flatten"


def test_flatten_still_runs_without_any_price():
    h = opened()
    h.alpaca.fail.append({"method": "GET", "path": "/v2/positions", "kind": "status", "status": 500, "times": 1})
    h.facade.trade_override["APP"] = {"price": 0, "ts": None}
    h.clock.set(at(11, 0, 5))
    h.ctl.tick()
    assert h.pos()["closed_reason"] == "flatten"


def test_exit_while_the_parent_is_still_partially_filling():
    """Codex round 2 #1: cancel and confirm the parent remainder too, fold its last fills in,
    then close what ORB really owns."""
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.entry_mode = "partial"
    h.alpaca.partial_qty = 200
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    parent = h.parent()
    assert parent["status"] == "partially_filled" and h.ctl.own_qty("APP") == 200
    h.alpaca.fill_on_cancel[parent["id"]] = (300, 100.25)     # 100 more fill while we cancel
    n = len(h.alpaca.requests)
    h.clock.set(at(11, 0))
    h.ctl.tick()
    w = writes_after(h, n)
    deletes = [p for m, p, b in w if m == "DELETE"]
    assert deletes[0].endswith(parent["id"])                            # parent remainder first
    assert len(deletes) == 3                                             # then both legs
    sells = [b for m, p, b in w if m == "POST"]
    assert len(sells) == 1 and sells[0]["qty"] == "300"
    assert h.alpaca.orders[parent["id"]]["status"] == "canceled"
    assert "APP" not in h.alpaca.positions and h.own_fill_sum() == 0
    assert h.alpaca.refused_403 == [] and h.pos()["status"] == "CLOSED"


def test_a_leg_fill_racing_the_cancel_leaves_nothing_to_sell():
    h = opened()
    tp = h.alpaca.leg(h.parent()["id"], "tp")
    h.alpaca.fill_on_cancel[tp["id"]] = (454, 101.85)
    n = len(h.alpaca.requests)
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert not [1 for m, p, b in writes_after(h, n) if m == "POST"]
    assert h.pos()["status"] == "CLOSED" and h.ctl.own_qty("APP") == 0
    assert [f[5] for f in h.fills] == ["entry", "target"]


def test_cancel_not_confirmed_means_no_sell_yet():
    h = opened()
    sl = h.alpaca.leg(h.parent()["id"], "sl")
    h.alpaca.cancel_pending[sl["id"]] = 10 ** 6
    n = len(h.alpaca.requests)
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert not [1 for m, p, b in writes_after(h, n) if m == "POST"]
    assert h.pos()["status"] == "OPEN" and h.alpaca.refused_403 == []
    assert any(e["kind"] == "exit_waiting_on_cancel" for e in h.ctl.state["events"])
    h.alpaca.cancel_pending.pop(sl["id"])
    h.alpaca.orders[sl["id"]]["status"] = "canceled"
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and h.own_fill_sum() == 0


def test_rejected_close_is_retried_next_pass():
    h = opened()
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 422})
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "OPEN" and h.ctl.own_qty("APP") == 454
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and h.own_fill_sum() == 0
    sells = [r for r in h.alpaca.requests if r[0] == "POST" and r[2].get("side") == "sell"]
    assert len(sells) == 2 and sells[0][2]["client_order_id"] != sells[1][2]["client_order_id"]


def test_stuck_exit_escalates_after_120s_and_never_stacks_a_second_order():
    h = opened()
    h.alpaca.market_fills = False             # the exit order sits unfilled
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert h.pos()["status"] == "OPEN"
    exit_oid = [o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]
    assert len(exit_oid) == 1
    for _ in range(25):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.pos()["escalated"] == "early"
    assert any(e["kind"] == "flatten_escalated" for e in h.ctl.state["events"])
    assert len([o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]) == 1
    h.alpaca.fill(exit_oid[0]["id"], price=100.0)
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and h.own_fill_sum() == 0


def test_own_daily_halt_latches_and_exits_everything():
    h = opened()
    h.alpaca.prices["PLTR"] = 50.1
    h.ctl.execute([pick("PLTR", "long", 50.0, 48.9, tier="quant")])
    h.alpaca.prices["APP"] = 96.5           # own unrealized ~ -1680 - ... <= -3% of 50k
    h.clock.advance(5)
    h.ctl.tick()
    assert h.ctl.halted()
    reasons = {p["symbol"]: p["closed_reason"] for p in h.ctl.state["positions"].values()}
    assert reasons == {"APP": "daily-loss halt", "PLTR": "daily-loss halt"}
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.prices["HOOD"] = 30.1
    assert "Daily loss halt" in h.ctl.execute([pick("HOOD", "long", 30.0, 29.4, tier="quant")])["reason"]


def test_adt_account_halt_stops_entries_and_exits_own_positions():
    h = opened()
    h.halt[0] = "ADT daily loss stop hit (-$1,240)"
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["closed_reason"] == "account loss stop" and h.own_fill_sum() == 0
    h.alpaca.prices["PLTR"] = 50.1
    assert "ADT daily loss stop" in h.ctl.execute([pick("PLTR", "long", 50.0, 48.9)])["reason"]


def test_request_all_exits_from_adt_paths():
    h = opened()
    syms = h.ctl.request_all_exits("ADT circuit breaker")
    assert syms == ["APP"]
    n = len(h.alpaca.requests)
    h.clock.advance(5)
    h.ctl.tick()
    assert_cancel_then_close(h, n, "APP", 454)
    assert h.pos()["closed_reason"] == "ADT circuit breaker"
    h.alpaca.prices["PLTR"] = 50.1
    assert "flattened today" in h.ctl.execute([pick("PLTR", "long", 50.0, 48.9)])["reason"]


def test_request_exit_refuses_symbols_orb_does_not_own():
    h = opened()
    assert h.ctl.request_exit("NVDA", "manual") is False
    assert h.ctl.request_exit("APP", "manual close") is True


def test_exit_never_touches_orders_that_are_not_orbs():
    h = opened()
    # another ADT arm's order on a different symbol, and a foreign order on APP itself
    h.alpaca.orders["foreign"] = {"id": "foreign", "client_order_id": "adt-tri-TSLA-x", "symbol": "APP",
                                  "side": "buy", "qty": "5", "type": "limit", "status": "new",
                                  "filled_qty": "0", "filled_avg_price": None, "replaced_by": None,
                                  "time_in_force": "day", "order_class": "simple"}
    h.alpaca.order_seq.append("foreign")
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert h.alpaca.orders["foreign"]["status"] == "new"
    assert not any(r[0] == "DELETE" and r[1].endswith("/foreign") for r in h.alpaca.requests)


def test_fake_enforces_the_one_sell_order_rule():
    """Sanity check of the fake: selling while the bracket legs hold the shares is refused."""
    h = opened()
    with pytest.raises(Exception) as e:
        h.broker.submit_market_order("APP", "sell", 454, "manual-1")
    assert getattr(e.value, "status_code", None) == 403
    assert h.alpaca.refused_403 and h.alpaca.refused_403[0]["code"] == 40310000


def test_exit_qty_is_recapped_to_a_position_read_right_before_the_post():
    """Codex P1: an outside sale landing after the exit record is saved must shrink the exit,
    never push the account through flat."""
    h = opened()
    done = []

    def outside_sale(state):
        if not done and any(r["role"] == "exit" for r in state["orders"].values()):
            done.append(1)
            h.alpaca.positions["APP"]["qty"] -= 100          # someone sold 100 by hand just now
    h.on_persist = outside_sale
    h.clock.set(at(11, 0))
    h.ctl.tick()
    sells = [r[2] for r in h.alpaca.requests if r[0] == "POST" and r[2].get("side") == "sell"]
    assert [s["qty"] for s in sells] == ["354"]
    assert h.alpaca.refused_403 == [] and "APP" not in h.alpaca.positions
    assert h.pos()["status"] == "CLOSED" and h.ctl.own_qty("APP") == 0
    assert any(e["kind"] == "exit_capped_outside_trade" for e in h.ctl.state["events"])


def test_working_exit_larger_than_the_account_is_cancelled_and_reissued_capped():
    h = opened()
    h.alpaca.market_fills = False
    h.clock.set(at(11, 0))
    h.ctl.tick()
    first = [o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]
    assert len(first) == 1 and first[0]["qty"] == "454"
    h.alpaca.positions["APP"]["qty"] = 300                  # an outside sale of 154 meanwhile
    h.alpaca.market_fills = True
    h.clock.advance(5)
    h.ctl.tick()
    assert h.alpaca.orders[first[0]["id"]]["status"] == "canceled"
    exits = [o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]
    assert [o["qty"] for o in exits] == ["454", "300"]
    assert "APP" not in h.alpaca.positions and h.pos()["status"] == "CLOSED"


# ---------------------------------------------------------------- review round 3
def account_reads(h, since=0):
    return [r for r in h.alpaca.requests[since:] if r[0] == "GET" and r[1] == "/v2/account"]


def test_exit_writes_refused_when_the_account_changed_after_the_cache_expired():
    h = opened()
    h.alpaca.account["account_number"] = "PA-SOMEONE-ELSE"      # the keys now point elsewhere
    n = len(h.alpaca.requests)
    h.clock.set(at(11, 0))                                      # > 60 s after the last verification
    h.ctl.tick()
    assert writes_after(h, n) == []                              # no cancel, no close
    assert any(k.endswith("wrong_account") for k in h.ctl.state["alarms"])
    assert h.pos()["status"] == "OPEN"


def test_credential_change_forces_a_fresh_check_inside_the_ttl():
    h = opened()
    h.broker._client.headers["APCA-API-KEY-ID"] = "other-key"
    h.alpaca.account["account_number"] = "PA-SOMEONE-ELSE"
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)                                          # well inside the 60 s TTL
    h.ctl.tick()
    assert writes_after(h, n) == [] and h.pos()["status"] == "OPEN"


def test_exits_on_the_right_account_do_not_add_account_reads_inside_the_ttl():
    h = opened()
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["closed_reason"] == "fast-fail"
    assert account_reads(h, n) == []                            # verified by the entry's account read


class RecordingBudget:
    """Records how many Alpaca requests had been made at each token acquire."""

    def __init__(self, h):
        self.h, self.at = h, []
        self.used = self.throttled = self.borrowed = 0

    def acquire(self, prio="normal"):
        self.at.append((len(self.h.alpaca.requests), prio))


def test_exit_budget_token_is_taken_before_the_final_position_read():
    h = opened()
    h.ctl.budget = RecordingBudget(h)
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    h.ctl.tick()
    reqs = h.alpaca.requests
    post = next(i for i, r in enumerate(reqs) if r[0] == "POST" and r[2].get("side") == "sell")
    pos_read = max(i for i, r in enumerate(reqs[:post]) if r[0] == "GET" and r[1] == "/v2/positions/APP")
    # nothing may wait for a token between the last position read and the POST
    assert not [a for a in h.ctl.budget.at if pos_read < a[0] <= post]
    assert pos_read == post - 1


def test_capped_exit_qty_is_persisted_before_the_post():
    h = opened()
    seen = []

    def hook(state):
        ex = [r for r in state["orders"].values() if r["role"] == "exit"]
        if ex and not seen:
            h.alpaca.positions["APP"]["qty"] -= 100
        if ex:
            seen.append((ex[0]["qty"], ex[0]["id"]))
    h.on_persist = hook
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert (354, None) in seen                  # saved with the capped qty while still unsent


def test_reconcile_takes_the_brokers_qty_for_a_working_exit():
    h = opened()
    h.alpaca.market_fills = False
    h.clock.set(at(11, 0))
    h.ctl.tick()
    exit_row = next(o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-"))
    exit_row["qty"] = "300"                     # what the broker actually holds for it
    ctl = h.restart()
    assert ctl.reconcile_on_startup()["ok"]
    rec = next(r for r in ctl.state["orders"].values() if r["role"] == "exit")
    assert rec["qty"] == 300
