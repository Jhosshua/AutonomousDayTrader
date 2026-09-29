"""ORB entries: ORBStraddle core.execute semantics against the in-memory Alpaca."""
from datetime import timedelta

import pytest

from backend.app.core.orb_execution import OrbExecutionController, RequestBudget, load_config
from backend.tests.unit.orb_execution.fakes import MANIFEST, Harness, at, pick


def test_manifest_values_are_the_effective_config():
    cfg = load_config(MANIFEST)
    assert cfg["risk_pct"] == 2.0 and cfg["max_day_risk_frac"] == 0.025
    assert cfg["clawback_peak_r"] == 0.60 and cfg["clawback_giveback_r"] == 0.25
    assert cfg["max_gross_mult"] == 3.0 and cfg["max_buying_power_pct"] == 60.0
    assert cfg["excluded_symbols"] == ["CDE", "TSLA"]
    assert load_config({"max_gross_pct": 300})["max_gross_mult"] == 3.0


def test_bracket_sizing_target_coid_and_intent_before_post():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out["ok"] and out["accepted"] == 1
    post = [r for r in h.alpaca.requests if r[0] == "POST"][0][2]
    # rd = 100.2 - 98.00 = 2.2 ; shares = floor(2% x 50000 / 2.2) = 454 ; target = 100.2 + 0.75 x 2.2
    assert post["qty"] == "454" and post["side"] == "buy" and post["type"] == "market"
    assert post["order_class"] == "bracket" and post["time_in_force"] == "day"
    assert post["take_profit"] == {"limit_price": "101.85"} and post["stop_loss"] == {"stop_price": "98.00"}
    coid = post["client_order_id"]
    assert coid.startswith("adt-orb-APP-2026-09-28-w1-a1-") and len(coid.rsplit("-", 1)[1]) == 6
    # a durable checkpoint containing this order's intent existed before the POST went out
    before = [s for s, w in zip(h.persisted, h.writes_at_persist) if w == 0]
    assert any(coid in s["positions"] and coid in s["orders"] for s in before)
    # own book: filled parent -> own qty 454 via on_fill, legs found through the nested parent
    assert h.ctl.own_qty("APP") == 454 and h.own_fill_sum() == 454
    legs = [r for r in h.ctl.state["orders"].values() if r["role"] == "leg"]
    assert {r["leg_kind"] for r in legs} == {"tp", "sl"}
    assert h.ctl.owns("APP") and "APP" in h.reserved
    assert h.ctl.day_risk_used() == pytest.approx(454 * 2.2, abs=0.01)


def test_short_bracket_geometry():
    h = Harness()
    h.alpaca.prices["APP"] = 313.03
    out = h.ctl.execute([pick("APP", "short", 313.5, 326.64)])
    assert out["ok"]
    post = [r for r in h.alpaca.requests if r[0] == "POST"][0][2]
    # the 09-28 APP worked example: rd 13.61, target 302.82
    assert post["side"] == "sell" and post["stop_loss"]["stop_price"] == "326.64"
    assert post["take_profit"]["limit_price"] == "302.82"
    assert post["qty"] == str(int(1000 // 13.61))


def test_day_risk_budget_second_trade_cut_to_half_percent_third_refused():
    h = Harness()
    h.alpaca.prices.update(APP=100.2, PLTR=50.1, HOOD=30.1)
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0), pick("PLTR", "long", 50.0, 48.9, tier="quant")])
    qty = {r[2]["symbol"]: int(r[2]["qty"]) for r in h.alpaca.requests if r[0] == "POST"}
    assert qty["APP"] == 454
    # budget 2.5% = 1250 ; left 1250 - 998.8 = 251.2 ; PLTR rd 1.2 -> 209 shares (~0.5%)
    assert qty["PLTR"] == int((1250 - 454 * 2.2) // 1.2)
    assert out["accepted"] == 2
    h.clock.advance(600)
    out3 = h.ctl.execute([pick("HOOD", "long", 30.0, 29.4, tier="quant")])
    assert not out3["ok"]
    assert ("HOOD", "day risk budget spent") in out3["refused"] or "capacity" in (out3["reason"] or "")
    assert "HOOD" not in qty and not any(r[2]["symbol"] == "HOOD" for r in h.alpaca.requests if r[0] == "POST")


def test_closed_trades_do_not_give_the_budget_back():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    used = h.ctl.day_risk_used()
    h.alpaca.fill(h.alpaca.leg(h.parent()["id"], "tp")["id"], price=101.85)
    h.clock.advance(15)
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED"
    assert h.ctl.day_risk_used() == used


def test_slots_four_and_two_structure():
    h = Harness(buying_power=10 ** 7, equity=10 ** 6)
    syms = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    for s in syms:
        h.alpaca.prices[s] = 100.2
    h.ctl.cfg["max_day_risk_frac"] = 1.0          # isolate the slot rules from the budget
    out = h.ctl.execute([pick(s, "long", 100.0, 98.0, tier="structure" if s in ("AAA", "BBB", "CCC") else "quant")
                         for s in syms])
    sent = [r[2]["symbol"] for r in h.alpaca.requests if r[0] == "POST"]
    assert sent == ["AAA", "BBB", "DDD", "EEE"]
    reasons = dict(out["refused"])
    assert "structure" in reasons["CCC"]
    assert h.ctl.slots_available() == 0
    assert h.ctl.execute([pick("FFF", "long", 100.0, 98.0, tier="quant")])["reason"].startswith("already executed")


def test_no_chase_refuses_a_move_beyond_a_third_of_the_stop():
    h = Harness()
    h.alpaca.prices["APP"] = 100.7          # moved 0.7 of a 2.0 stop distance = 35%
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and "of the stop distance" in dict(out["refused"])["APP"]
    assert not h.alpaca.writes and "APP" not in h.reserved


def test_stop_side_and_min_distance():
    h = Harness()
    # reachable only through the 2 dp stop rounding (the no-chase cap catches real moves first)
    h.alpaca.prices.update(AAA=100.0, BBB=100.05)
    out = h.ctl.execute([pick("AAA", "long", 100.0, 99.996), pick("BBB", "long", 100.0, 99.7)], strict=False)
    r = dict(out["refused"])
    assert "through the 100.0 stop" in r["AAA"]
    assert r["BBB"] == "stop is closer than 0.5%"
    assert not h.alpaca.writes


def test_stale_price_strict_aborts_the_whole_batch():
    h = Harness()
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    h.facade.trade_override["PLTR"] = {"price": 50.1, "ts": (h.clock.now - timedelta(seconds=61)).isoformat()}
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0), pick("PLTR", "long", 50.0, 48.9, tier="quant")])
    assert not out["ok"] and "refusing the whole batch" in out["reason"] and "stale" in out["reason"]
    assert not h.alpaca.writes
    assert h.reserved == set()                # APP's reservation was given back


def test_stale_price_non_strict_skips_only_that_pick():
    h = Harness()
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    h.facade.trade_override["PLTR"] = {"price": 50.1, "ts": (h.clock.now + timedelta(seconds=6)).isoformat()}
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0), pick("PLTR", "long", 50.0, 48.9, tier="quant")],
                        strict=False)
    assert out["ok"] and "no fresh price" in dict(out["refused"])["PLTR"]
    assert [r[2]["symbol"] for r in h.alpaca.requests if r[0] == "POST"] == ["APP"]


def test_excluded_and_occupied_symbols_never_trade():
    h = Harness(adt_book={"NVDA"})
    for s in ("TSLA", "CDE", "NVDA", "APP"):
        h.alpaca.prices[s] = 100.2
    out = h.ctl.execute([pick(s, "long", 100.0, 98.0, tier="quant") for s in ("TSLA", "CDE", "NVDA", "APP")])
    r = dict(out["refused"])
    assert "excluded" in r["TSLA"] and "excluded" in r["CDE"] and r["NVDA"] == "already held by this account"
    assert [x[2]["symbol"] for x in h.alpaca.requests if x[0] == "POST"] == ["APP"]


def test_recheck_and_late_macro_veto():
    h = Harness()
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    h.facade.recheck_by_symbol["PLTR"] = (False, "candle rule: red candle allows short only")
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0), pick("PLTR", "long", 50.0, 48.9, tier="quant")])
    assert "candle rule" in dict(out["refused"])["PLTR"]
    h2 = Harness()
    h2.alpaca.prices["APP"] = 100.2
    h2.facade.macro_result = (True, "SPY is down")          # (vetoed, why)
    out2 = h2.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out2["ok"] and not [r for r in h2.alpaca.requests if r[0] == "POST"]
    assert h2.pos()["status"] == "SKIPPED" and h2.ctl.day_risk_used() == 0
    assert "APP" not in h2.reserved


def test_symbol_occupied_at_alpaca_before_post_is_skipped():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.positions["APP"] = {"qty": 5, "avg": 99.0}    # someone took it after planning
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and not [r for r in h.alpaca.requests if r[0] == "POST"]


def test_live_buying_power_refuses_never_shrinks():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.account["buying_power"] = "40000"    # frozen bp stays 200k; live bp cannot fund 454 x 100.2
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and "not funded" in dict(out["refused"])["APP"]
    assert not h.alpaca.writes


def test_gross_cap_is_sixty_percent_of_frozen_buying_power():
    h = Harness(buying_power=30000.0)
    h.alpaca.prices["APP"] = 100.2
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    post = [r for r in h.alpaca.requests if r[0] == "POST"][0][2]
    assert int(post["qty"]) == int(18000 // 100.2)


def test_explicit_4xx_is_rejected_and_gives_back_the_budget():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 403,
                          "message": "short not allowed"})
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and "NONE" in out["reason"]
    assert h.pos()["status"] == "REJECTED" and h.ctl.day_risk_used() == 0
    assert "APP" not in h.reserved and not h.ctl.owns("APP")


def test_transport_error_found_by_coid_is_accepted():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost"})
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out["ok"] and out["accepted"] == 1
    assert h.ctl.own_qty("APP") == 454 and h.pos()["status"] == "OPEN"
    assert len([r for r in h.alpaca.requests if r[0] == "POST"]) == 1        # never resubmitted


def test_unknown_submit_kept_pending_then_resolved_by_coid():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost"})
    h.alpaca.fail.append({"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status",
                          "status": 503, "times": 3})
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out["ok"] and out["unknown"] == ["APP"]
    assert h.pos()["status"] == "UNKNOWN" and h.ctl.owns("APP")
    assert h.ctl.day_risk_used() > 0                   # in-doubt risk is counted
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "OPEN" and h.ctl.own_qty("APP") == 454 and h.own_fill_sum() == 454
    assert len([r for r in h.alpaca.requests if r[0] == "POST"]) == 1


def test_unknown_submit_that_never_reached_the_broker_closes_after_the_grace():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "transport"})
    h.alpaca.fail.append({"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status",
                          "status": 503, "times": 3})
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert h.pos()["status"] == "UNKNOWN"
    h.clock.advance(10)
    h.ctl.tick()                     # 404 inside the 60 s grace proves nothing
    assert h.pos()["status"] == "UNKNOWN"
    h.clock.advance(60)
    h.ctl.tick()
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and "never reached" in h.pos()["closed_reason"]
    assert "APP" not in h.reserved


def test_partial_parent_fill_resizes_the_legs_once_they_are_live():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.entry_mode = "partial"
    h.alpaca.partial_qty = 200
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    parent = h.parent()
    assert h.ctl.own_qty("APP") == 200
    # the immediate resize hits held legs (422) and is retried by the supervisor
    assert any(e["kind"] == "bracket_resize_unconfirmed" for e in h.ctl.state["events"])
    # parent remainder cancelled at the broker (e.g. day end of liquidity): legs go live at 200
    h.alpaca._cancel(h.alpaca.orders[parent["id"]])
    h.clock.advance(5)
    h.ctl.tick()
    tp, sl = h.alpaca.leg(parent["id"], "tp"), h.alpaca.leg(parent["id"], "sl")
    assert int(tp["qty"]) == 200 and int(sl["qty"]) == 200
    assert h.pos()["bracket_qty"] == 200


def test_partial_fill_then_leg_resize_patch_succeeds():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.entry_mode = "partial"
    h.alpaca.partial_qty = 300
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    parent = h.parent()
    for lid in parent["legs_ids"]:
        h.alpaca.orders[lid]["status"] = "new"            # legs live while the parent still works
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["bracket_qty"] == 300
    patches = [r for r in h.alpaca.requests if r[0] == "PATCH"]
    assert {p[2]["qty"] for p in patches} == {"300"}
    h.clock.advance(11)
    h.ctl.tick()                    # the replaced legs are refetched by their own ids
    legs = [r for r in h.ctl.state["orders"].values() if r["role"] == "leg" and not r["terminal"]]
    assert {int(r["qty"]) for r in legs} == {300}


def test_shadow_mode_decides_and_records_but_sends_zero_broker_writes():
    h = Harness(mode="shadow")
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0), pick("PLTR", "long", 50.0, 48.9, tier="quant")])
    assert out["ok"] and out["dry_run"]
    assert h.alpaca.writes == []
    plan = [p for p in h.ctl.state["positions"].values()]
    assert {p["symbol"]: p["planned_shares"] for p in plan} == {"APP": 454, "PLTR": int((1250 - 998.8) // 1.2)}
    assert all(p["status"] == "SHADOW" for p in plan)
    assert h.reserved == set() and h.ctl.slots_available() == 4
    for _ in range(3):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.alpaca.writes == []


def test_off_mode_and_not_reconciled_refuse_entries():
    h = Harness(mode="off")
    h.alpaca.prices["APP"] = 100.2
    assert "off" in h.ctl.execute([pick()])["reason"]
    h2 = Harness(reconcile=False)
    h2.alpaca.prices["APP"] = 100.2
    assert "reconciliation" in h2.ctl.execute([pick()])["reason"]
    assert not h.alpaca.writes and not h2.alpaca.writes


def test_intent_that_cannot_be_persisted_is_never_sent():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.persist_raises[0] = True
    out = h.ctl.execute([pick()])
    assert not out["ok"] and "nothing was sent" in out["reason"]
    assert not h.alpaca.writes and h.reserved == set()


def test_session_window_and_cutoff():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.clock.set(at(9, 37))
    assert "outside the execution window" in h.ctl.execute([pick()])["reason"]
    h.clock.set(at(10, 15, 1))
    assert "cutoff" in h.ctl.execute([pick()])["reason"]
    assert not h.alpaca.writes


def test_sizing_freeze_uses_last_equity_and_refreezes_on_change():
    h = Harness(equity=51000.0, last_equity=50000.0)
    assert h.ctl.session_sizing()["equity"] == 50000.0
    h.alpaca.prices["APP"] = 100.2
    h.ctl.execute([pick()])
    assert [r for r in h.alpaca.requests if r[0] == "POST"][0][2]["qty"] == "454"


def test_preopen_baseline_when_last_equity_is_zero():
    h = Harness(equity=40000.0, last_equity=0.0)
    s = h.ctl.session_sizing()
    assert s["equity"] == 40000.0 and s["equity_source"] == "preopen_paper_equity"


def test_no_baseline_means_no_trade():
    h = Harness(last_equity=0.0, freeze=False)
    h.alpaca.positions["XYZ"] = {"qty": 3, "avg": 10.0}       # not activity free: no pre-open fallback
    h.clock.set(at(9, 20))
    ok, _ = h.ctl.freeze_session()
    assert not ok
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    out = h.ctl.execute([pick()])
    assert not out["ok"] and "sizing" in out["reason"]
    assert not h.alpaca.writes


def test_own_halt_and_account_halt_refuse_entries():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.halt[0] = "ADT daily loss stop hit"
    assert "ADT daily loss stop" in h.ctl.execute([pick()])["reason"]
    h.halt[0] = None
    h.ctl.state["realized"]["2026-09-28"] = {"pnl": -1600.0, "unknown": False}
    out = h.ctl.execute([pick()])
    assert "own P&L" in out["reason"] and h.ctl.halted()
    h.ctl.state["realized"]["2026-09-28"] = {"pnl": 0.0, "unknown": False}
    assert "Daily loss halt" in h.ctl.execute([pick()])["reason"]    # latched for the day
    assert not h.alpaca.writes


def test_request_budget_exit_reserve():
    t = [0.0]
    b = RequestBudget(per_min=30, reserve_per_min=15, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    for _ in range(10):
        b.acquire("normal")
    with pytest.raises(Exception):
        b.acquire("refresh")                    # main nearly empty: refresh is skipped at once
    b.tokens = 0.0
    b.acquire("exit")                           # exits borrow from the reserve
    assert b.borrowed == 1
    t0 = t[0]
    b.acquire("normal")                         # waits (paced) for the main bucket to refill
    assert t[0] - t0 >= 2.0 and b.borrowed == 1
    slow = RequestBudget(per_min=1, reserve_per_min=1, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    slow.tokens = slow.reserve = 0.0
    t1 = t[0]
    with pytest.raises(Exception):
        slow.acquire("normal")                  # waited its 6 s, then refused
    assert 6.0 <= t[0] - t1 < 7.0


def test_controller_rejects_unknown_mode():
    with pytest.raises(ValueError):
        OrbExecutionController(None, None, MANIFEST, mode="yolo")


def test_absorption_age_is_read_from_the_manifest():
    assert load_config({})["absorption_max_result_age_s"] == 8.0
    assert load_config({"absorption_max_result_age_s": 5})["absorption_max_result_age_s"] == 5.0
    assert load_config({"absorption": {"max_result_age_s": 6}})["absorption_max_result_age_s"] == 6.0
