"""Full fake ORB sessions through the REAL main.py runtime clock (plan 5.3, 8.10).

Each 5-second step is one pass of main._runtime_clock_step at the fake time: session boundary, EOD
phases, the fixed plans, then ORB's scheduler (inline) and the ledger sync. The fake Alpaca holds
the bracket; the scripted facade returns the boards and decisions ORBStraddle's code would.

Covered: pre-open sizing freeze, 9:36 preview, 9:38 final scan + decision, bracket fill booked into
ADT, +0.75R breakeven then the software breakeven exit, a secondary-wave pick closed by the 11:00
flatten, a sit-out day, shadow mode (zero broker writes), a restart mid-session, and the durable
executed-today set handed to every secondary scan and decision."""
from datetime import timedelta

import pytest

from backend.app.core.orb_execution import RequestBudget
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick

SESSION = "2026-09-28"


def board(*syms, board_id=None, coverage=1.0):
    return {"ok": True, "error": None, "coverage": coverage,
            "cards": [{"symbol": s, "direction": "long"} for s in syms], "board_id": board_id or "-".join(syms)}


def decision(*picks, verdict="trade", reason=None):
    return {"verdict": verdict, "reason": reason, "picks": list(picks), "audit": [
        {"symbol": p["symbol"], "direction": p["direction"], "tier": p.get("tier"), "chosen": True} for p in picks],
        "regime": {"action": "TRADE"}}


def run(h, start, end, step=5.0, each=None):
    h.clock.set(start)
    while h.clock.now <= end:
        if each:
            each(h)
        h.clock_step()
        h.clock.advance(step)


def calls(h, name):
    return [c for c in h.facade.calls if c[0] == name]


def session(r, mode="live", broker=True):
    h = MainOrb(r, mode=mode, start=at(9, 10), broker=broker, freeze=False)
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    return h


def test_live_session_pick_bracket_breakeven_exit_secondary_and_eleven_flatten(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP", "PLTR"),                              # 9:36 preview
                             board("APP", "PLTR", board_id="final"),            # 9:38 final
                             board("PLTR", board_id="sec-0945")]                # 9:45 secondary
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0)),
                               decision(pick("PLTR", "long", 50.0, 49.0, tier="quant"))]
    # pre-open: sizing frozen from the account between 9:15 and 9:29, stock list prepared
    run(h, at(9, 10), at(9, 16))
    st = h.sched.state["steps"]
    assert st["sizing"]["state"] == "done" and h.ctl.session_sizing()["equity"] == 50000.0
    assert st["prep"]["state"] == "done" and h.ctl.ready
    assert not [q for q in h.alpaca.requests if q[0] != "GET"]
    card = next(c for c in r._strategy_cards(h.clock.now) if c["id"] == "orb")
    assert card["window"]["state"] == "WAITING" and card["window"]["headline"] == "Decides at 9:38 AM"

    # 9:38:30 final scan -> decision -> bracket at the fake Alpaca -> fill booked in ADT's book
    run(h, at(9, 16, 5), at(9, 38, 40))
    assert [c[2:4] for c in calls(h, "scan")] == [("09:36", "preview"), ("09:38", "primary")]
    assert calls(h, "decide")[0][2:4] == ("final", "primary")
    bracket = next(q for q in h.alpaca.requests if q[0] == "POST")[2]
    assert bracket["order_class"] == "bracket" and bracket["symbol"] == "APP" and int(bracket["qty"]) == 454
    assert bracket["stop_loss"]["stop_price"] == "98.00" and bracket["take_profit"]["limit_price"] == "101.85"
    assert r.account.positions["APP"].shares == 454 and r.account.positions["APP"].strategy_id == "orb"
    assert r._local_signed_positions(r.account) == h.alpaca_positions()
    sub = [d for d in r.decision_log.recent(20, "orb") if d["outcome"] == "SUBMITTED"]
    assert sub and sub[0]["symbol"] == "APP" and "bracket" in sub[0]["detail"]
    setups = [x for x in r.research_recorder.recent["setups"] if x.get("strategy_id") == "orb"]
    assert setups and setups[-1]["verdict"] == "trade" and setups[-1]["picks"][0]["symbol"] == "APP"
    card = next(c for c in r._strategy_cards(h.clock.now) if c["id"] == "orb")
    assert card["name"] == "Opening Range Breakout (ORBStraddle rules)"
    assert card["window"]["state"] == "MANAGING" and "Bought APP (long)" in card["orb"]["step"]
    trade = card["orb"]["open_trades"][0]
    assert (trade["symbol"], trade["stop"], trade["target"], trade["qty"]) == ("APP", 98.0, 101.85, 454)

    # +0.77R: breakeven moves the broker stop leg to the entry
    h.alpaca.prices["APP"] = 101.90
    run(h, at(9, 38, 45), at(9, 39, 0))
    assert h.pos("APP")["be_locked"] and h.pos("APP")["stop"] == 100.2
    # the 9:45 secondary wave picks PLTR; the scan skips nothing yet but knows APP was executed today
    run(h, at(9, 39, 5), at(9, 45, 10))
    sec = [c for c in calls(h, "scan") if c[3] == "secondary"]
    assert sec[0][2] == "09:45" and sec[0][4] == ["APP"] and sec[0][5] == ["APP"]
    assert calls(h, "decide")[1][3] == "secondary" and calls(h, "decide")[1][5] == ["APP"]
    assert r.account.positions["PLTR"].shares > 0

    # APP falls back through the entry: the software breakeven exit cancels its legs then sells 454
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 100.15
    run(h, at(9, 45, 15), at(9, 45, 30))
    h.assert_orb_closed_through_its_controller(n, "APP", 454)
    assert h.pos("APP")["closed_reason"] == "breakeven"
    trades = [t for t in r.pending_trade_records.values() if t["symbol"] == "APP"]
    assert len(trades) == 1 and trades[0]["strategy_id"] == "orb" and trades[0]["exit_reason"] == "breakeven"
    assert trades[0]["realized_pnl"] == pytest.approx(454 * (100.15 - 100.2), abs=0.01)
    assert r.orb_strategy.trades_count == 1

    # later secondary scans still exclude APP (executed today, closed) through the durable set
    run(h, at(9, 45, 35), at(9, 47, 5))
    last = [c for c in calls(h, "scan") if c[3] == "secondary"][-1]
    assert "APP" in last[5] and "PLTR" in last[5] and "APP" not in last[4]

    # 10:15 cutoff, 11:00 flatten of the PLTR trade
    run(h, at(10, 14, 50), at(10, 16), step=10)
    assert h.sched.state["steps"]["cutoff"]["state"] == "done"
    n = len(h.alpaca.requests)
    qty = h.ctl.own_qty("PLTR")
    run(h, at(10, 59, 50), at(11, 0, 20))
    h.assert_orb_closed_through_its_controller(n, "PLTR", qty)
    assert h.pos("PLTR")["closed_reason"] == "flatten"
    assert not r.account.positions
    assert {t["symbol"] for t in r.pending_trade_records.values()} == {"APP", "PLTR"}
    card = next(c for c in r._strategy_cards(h.clock.now) if c["id"] == "orb")
    assert card["window"]["state"] == "DONE_FOR_DAY" and card["orb"]["open_trades"] == []
    assert card["orb"]["realized_pnl"] == pytest.approx(sum(t["realized_pnl"] for t in r.pending_trade_records.values()))
    # rate budget: the whole morning used a bounded number of Alpaca requests
    assert len(h.alpaca.requests) < 400


def test_sit_out_day_places_nothing_and_says_why(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [decision(verdict="sit_out", reason="board is one-sided: 84% shorts")]
    run(h, at(9, 10), at(9, 50), step=10)
    assert h.alpaca.writes == [] and not r.account.positions
    card = next(c for c in r._strategy_cards(h.clock.now) if c["id"] == "orb")
    assert "sat out (board is one-sided: 84% shorts)" in card["orb"]["step"]
    rows = r.decision_log.recent(20, "orb")
    assert any(d["outcome"] == "ORB_SAT_OUT" and "84% shorts" in d["detail"] for d in rows)
    assert h.sched.state["steps"]["primary_decision"]["state"] == "done"


def test_shadow_session_decides_like_live_and_sends_zero_broker_writes(main_runtime):
    r = main_runtime
    h = session(r, mode="shadow")
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    run(h, at(9, 10), at(11, 5), step=10)
    assert calls(h, "decide") and h.alpaca.writes == []
    assert not r.account.positions and not [o for o in r.engine.orders.values() if o.strategy_id == "orb"]
    assert [p["status"] for p in h.ctl.state["positions"].values()] == ["SHADOW"]
    card = next(c for c in r._strategy_cards(at(9, 50)) if c["id"] == "orb")
    assert card["orb"]["mode_text"] == "Shadow: watching only, no orders"
    assert "would have placed buy APP" in card["orb"]["step"]
    rows = [d for d in r.decision_log.recent(20, "orb") if d["outcome"] == "ORB_SHADOW"]
    assert rows and "would buy 454" in rows[0]["detail"]
    # shadow executions still count as executed today for the secondary wave (ORBStraddle's rule)
    sec = [c for c in calls(h, "scan") if c[3] == "secondary"]
    assert sec and all("APP" in c[5] for c in sec)


def test_shadow_without_a_broker_uses_the_shadow_equity_and_never_needs_alpaca(main_runtime):
    r = main_runtime
    h = session(r, mode="shadow", broker=False)
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    run(h, at(9, 10), at(9, 40), step=10)
    assert h.alpaca.requests == []
    assert [p["status"] for p in h.ctl.state["positions"].values()] == ["SHADOW"]


def test_restart_mid_session_keeps_the_trade_the_decision_and_executed_today(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    run(h, at(9, 10), at(9, 39))
    assert h.pos("APP")["status"] == "OPEN"
    decides = len(calls(h, "decide"))
    # a new process: ORB rebuilt from its persisted rows; entries wait for the reconciliation
    r.orb.build(h.broker, h.facade, "live", clock=h.clock, inline=True, monotonic=lambda: h.clock.now.timestamp(),
                sleep=h.clock.sleep, is_session=lambda d: True, budget=RequestBudget(10 ** 6, 10 ** 6))
    h.ctl, h.sched = r.orb.controller, r.orb.scheduler
    assert not h.ctl.ready and h.ctl.owns("APP")
    run(h, at(9, 39, 5), at(9, 45, 10))
    assert h.ctl.ready and len(calls(h, "decide")) == decides        # the 9:38 decision is not re-run
    sec = [c for c in calls(h, "scan") if c[3] == "secondary"]
    assert sec and sec[0][5] == ["APP"]                               # executed-today survived the restart
    assert r.account.positions["APP"].shares == 454                   # booked once, not twice
    n = len(h.alpaca.requests)
    run(h, at(10, 59, 55), at(11, 0, 10))
    h.assert_orb_closed_through_its_controller(n)


def test_holiday_does_nothing_and_off_mode_scans_nothing(main_runtime):
    r = main_runtime
    h = MainOrb(r, start=at(9, 10), freeze=False)
    r.orb.build(h.broker, h.facade, "live", clock=h.clock, inline=True, monotonic=lambda: h.clock.now.timestamp(),
                is_session=lambda d: False, restore=False)
    h.ctl, h.sched = r.orb.controller, r.orb.scheduler
    run(h, at(9, 10), at(10, 0), step=30)
    assert not [c for c in h.facade.calls if c[0] in ("prep", "scan", "decide")] and h.alpaca.writes == []
    assert r.orb.status()["step"] == "No market session today."
    h2 = MainOrb(r, mode="off", start=at(9, 10), freeze=False)
    run(h2, at(9, 10), at(9, 50), step=30)
    assert not [c for c in h2.facade.calls if c[0] in ("prep", "scan", "decide")]
    card = next(c for c in r._strategy_cards(at(9, 45)) if c["id"] == "orb")
    assert card["orb"]["mode"] == "off" and any("switched off" in b for b in card["window"]["blockers"])


def test_low_coverage_final_scan_retries_once_then_decides(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", coverage=0.85), board("APP", coverage=0.95, board_id="retry")]
    h.facade.decide_results = [decision(verdict="pass", reason="no card passed")]
    run(h, at(9, 10), at(9, 39), step=10)
    assert [c[3] for c in calls(h, "scan")] == ["preview", "primary", "primary"]
    assert "(retry)" in h.sched.state["steps"]["final"]["detail"]
    assert calls(h, "decide")[0][2] == "retry"


def test_a_refused_board_is_reported_not_traded(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [{"verdict": "refused", "reason": "past the 10:15 cutoff", "picks": [], "audit": []}]
    run(h, at(9, 10), at(9, 39), step=10)
    assert h.alpaca.writes == []
    assert h.sched.state["last_verdict"]["verdict"] == "refused"
    assert "No decision: the board was not decided: past the 10:15 cutoff" in r.orb.status()["step"]
    assert any(d["outcome"] == "ORB_NO_DECISION" for d in r.decision_log.recent(10, "orb"))


def test_unknown_parent_and_uuid_leg_fills_are_booked_once_through_main(main_runtime):
    """Blocking test 8.10: the bracket POST reply is lost (outcome UNKNOWN), the parent is found by its
    client id, then the stop LEG (a broker UUID client id) fills at Alpaca. ADT books entry and stop
    exactly once each and records the trade."""
    r = main_runtime
    h = MainOrb(r)
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost"})
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)], h.clock.now)
    assert out["ok"]
    h.run_supervisor(passes=2)
    assert r.account.positions["APP"].shares == 454
    parent = h.parent()
    sl = h.alpaca.leg(parent["id"], "sl")
    assert len(sl["client_order_id"]) == 36                          # a broker UUID, not our coid
    h.alpaca.fill(sl["id"], price=97.95)
    h.run_supervisor(passes=3)
    assert "APP" not in r.account.positions and h.alpaca_positions() == {}
    trade = next(t for t in r.pending_trade_records.values() if t["symbol"] == "APP")
    assert trade["exit_reason"] == "stop"
    assert trade["realized_pnl"] == pytest.approx(454 * (97.95 - 100.2), abs=0.01)
    assert [leg["side"] for leg in trade["fill_legs"]] == ["BUY", "SELL"]
    r.orb.sync(h.clock.now)
    assert len([t for t in r.pending_trade_records.values() if t["symbol"] == "APP"]) == 1


def test_entry_and_target_fills_found_in_one_pass_are_booked_entry_first(main_runtime):
    """The bracket POST reply is lost and, before ORB hears back, the parent AND its target leg fill.
    ORB discovers both fills in the same pass (same timestamp); ADT must book the opening fill first."""
    r = main_runtime
    h = MainOrb(r)
    seq = iter(range(1, 10 ** 6))
    h.alpaca._new_id = lambda: f"00000000-0000-4000-8000-{next(seq):012d}"   # leg ids sort before our coids
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "lost"})
    h.alpaca.fail.append({"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status",
                          "status": 503, "times": 3})
    assert h.ctl.execute([pick("APP", "long", 100.0, 98.0)], h.clock.now)["ok"]
    assert "APP" not in r.account.positions                       # nothing known yet
    parent = h.alpaca.by_coid(h.pos("APP")["coid"])
    h.alpaca.fill(h.alpaca.leg(parent["id"], "tp")["id"], price=101.85)
    h.run_supervisor(passes=3)
    assert h.alpaca_positions() == {} and "APP" not in r.account.positions
    trade = next(t for t in r.pending_trade_records.values() if t["symbol"] == "APP")
    assert trade["exit_reason"] == "target"
    assert trade["realized_pnl"] == pytest.approx(454 * (101.85 - 100.2), abs=0.01)
    assert r.account.realized_pnl == pytest.approx(454 * (101.85 - 100.2), abs=0.01)
