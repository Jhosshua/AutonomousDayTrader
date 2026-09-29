"""Attack ORB through the REAL backend.app.main (reservation lock, ledger booking, runtime clock, mode
switches by restart, corrupt state, clock jumps, late starts). Fake Alpaca + scripted facade only."""
import threading
from datetime import timedelta

import pytest

from backend.app.core.orb_execution import RequestBudget
from backend.app.models.events import OrderSide, OrderType
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick

SESSION = "2026-09-28"


def board(*syms, board_id=None, coverage=1.0):
    return {"ok": True, "error": None, "coverage": coverage,
            "cards": [{"symbol": s, "direction": "long"} for s in syms], "board_id": board_id or "-".join(syms)}


def decision(*picks, verdict="trade", reason=None):
    return {"verdict": verdict, "reason": reason, "picks": list(picks), "audit": [], "regime": {"action": "TRADE"}}


def run(h, start, end, step=5.0, each=None):
    h.clock.set(start)
    while h.clock.now <= end:
        if each:
            each(h)
        h.clock_step()
        h.clock.advance(step)


def rebuild(r, h, mode):
    """A new process (ORB rebuilt from its persisted rows) started with another ORB_MODE."""
    r.orb.build(h.broker, h.facade, mode, clock=h.clock, inline=True, monotonic=lambda: h.clock.now.timestamp(),
                sleep=h.clock.sleep, is_session=lambda d: True, budget=RequestBudget(10 ** 6, 10 ** 6))
    h.ctl, h.sched = r.orb.controller, r.orb.scheduler


def brackets(h):
    return [b for m, p, b, _q in h.alpaca.requests if m == "POST" and b.get("order_class") == "bracket"]


def session(r, mode="live", start=None):
    h = MainOrb(r, mode=mode, start=start or at(9, 10), freeze=False)
    h.alpaca.prices.update(APP=100.2, PLTR=50.1, HOOD=30.1)
    return h


def adt_order(r, sym, strat="vwap_pullback"):
    o = r.engine.create_order(sym, OrderSide.BUY, OrderType.MARKET, 10, estimated_price=50.0, stop_price=49.0,
                              strategy_id=strat)
    o.status = o.status.__class__.SUBMITTED
    return o


# ----------------------------------------------------------------------------- mode switches (by restart)
@pytest.mark.parametrize("new_mode", ["off", "shadow"])
def test_live_to_off_or_shadow_with_a_position_still_exits_and_never_enters(main_runtime, new_mode):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    rebuild(r, h, new_mode)
    h.facade.scan_results = [board("PLTR", board_id="sec")] * 5
    h.facade.decide_results = [decision(pick("PLTR", "long", 50.0, 48.9, tier="quant"))] * 5
    n = len(brackets(h))
    run(h, at(9, 45), at(9, 48))
    assert h.ctl.ready and h.ctl.owns("APP")
    h.alpaca.prices["APP"] = 99.30                  # fast-fail must still fire in off/shadow
    run(h, at(9, 48, 5), at(9, 48, 30))
    assert "APP" not in h.alpaca.positions and "APP" not in r.account.positions
    assert len(brackets(h)) == n                   # no new ORB entry after the switch
    assert h.alpaca.refused_403 == []


def test_shadow_morning_then_live_restart_never_re_runs_the_primary_decision(main_runtime):
    r = main_runtime
    h = session(r, mode="shadow")
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    run(h, at(9, 10), at(9, 39))
    assert not h.alpaca.writes
    decides = len([c for c in h.facade.calls if c[0] == "decide"])
    rebuild(r, h, "live")
    run(h, at(9, 39, 5), at(9, 44))
    assert len([c for c in h.facade.calls if c[0] == "decide"]) == decides
    assert not brackets(h)


# ----------------------------------------------------------------------------- corrupt state
@pytest.mark.xfail(strict=True, reason="BUG P2: a version-1 ORB state row with a malformed position (no "
                                        "'symbol') passes from_state(); afterwards every ADT entry admission "
                                        "raises inside orb.claim_for_adt -> controller.owns -> _position_for "
                                        "(KeyError), so ORB's corrupt row blocks ADT's OTHER strategies")
def test_semantically_corrupt_orb_state_never_blocks_adts_other_strategies(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    r.orb._mem_state["controller"] = {"version": 1, "positions": {"x": {"status": "OPEN", "day": SESSION}}}
    rebuild(r, h, "live")
    run(h, at(9, 39), at(9, 39, 20))                # ADT's clock keeps running
    ok, why = r.pre_trade_risk_validator(adt_order(r, "NVDA"), r.account)
    assert not why.startswith("ORB_OWNED")


def test_semantically_corrupt_orb_state_keeps_orb_entries_refused(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    r.orb._mem_state["controller"] = {"version": 1, "positions": {"x": {"status": "OPEN", "day": SESSION}}}
    rebuild(r, h, "live")
    run(h, at(9, 39), at(9, 40))
    assert not h.ctl.ready
    h.alpaca.prices["PLTR"] = 50.1
    out = h.ctl.execute([pick("PLTR", "long", 50.0, 48.9)], h.clock.now)
    assert not out["ok"] and not brackets(h)


# ----------------------------------------------------------------------------- cross-arm races
def test_many_orb_and_adt_threads_racing_one_symbol_never_both_win(main_runtime):
    r = main_runtime
    MainOrb(r)
    for i in range(50):
        sym = f"RACE{i}"
        orders = [adt_order(r, sym, "news_momentum") for _ in range(4)]
        barrier = threading.Barrier(8)
        orb_wins, adt_wins = [], []

        def orb_side():
            barrier.wait()
            if r.orb.reserve(sym):
                orb_wins.append(1)

        def adt_side(o):
            barrier.wait()
            if r.orb.claim_for_adt(sym, o, "news_momentum") is None:
                adt_wins.append(1)
        ts = [threading.Thread(target=orb_side) for _ in range(4)] + \
             [threading.Thread(target=adt_side, args=(o,)) for o in orders]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not (orb_wins and adt_wins), (i, len(orb_wins), len(adt_wins))
        r.orb.release(sym)


def test_orb_releases_the_symbol_to_other_arms_after_a_broker_rejection(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.alpaca.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 403})
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)], h.clock.now)
    assert not out["ok"] and not r.orb.owns("APP")
    ok, why = r.pre_trade_risk_validator(adt_order(r, "APP"), r.account)
    assert not why.startswith("ORB_OWNED")


# ----------------------------------------------------------------------------- breaker at each step
def test_breaker_while_the_entry_parent_is_still_unfilled_cancels_it_and_leaves_nothing(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.alpaca.entry_mode = "new"                     # the market parent has not filled yet
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    assert h.ctl.execute([pick("APP", "long", 100.0, 98.0)], h.clock.now)["ok"]
    r._trip_circuit_breaker(h.clock.now)
    h.run_supervisor()
    assert not h.alpaca.positions and "APP" not in r.account.positions
    assert not [o for o in h.alpaca.orders.values() if o["status"] in ("new", "held", "accepted")]
    assert not [o for o in r.engine.orders.values() if o.symbol == "APP" and o.strategy_id != "orb"]
    h.alpaca.prices["PLTR"] = 50.1
    out = h.ctl.execute([pick("PLTR", "long", 50.0, 48.9)], h.clock.now)
    assert not out["ok"] and len(brackets(h)) == 1


def test_breaker_during_a_stuck_exit_never_stacks_a_second_close(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    h.alpaca.market_fills = False                    # ORB's close sits unfilled
    h.alpaca.prices["APP"] = 99.30
    h.run_supervisor(passes=1)
    r._trip_circuit_breaker(h.clock.now)
    r.orb.request_all_exits("MANUAL_FLATTEN_ALL")
    h.run_supervisor(passes=4)
    closes = [o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]
    assert len(closes) == 1 and h.alpaca.refused_403 == []
    h.alpaca.fill(closes[0]["id"], price=99.3)
    h.run_supervisor(passes=2)
    assert "APP" not in h.alpaca.positions and "APP" not in r.account.positions


# ----------------------------------------------------------------------------- clocks / late starts
def test_process_first_started_at_0940_trades_with_a_late_sizing_freeze(main_runtime):
    r = main_runtime
    h = session(r, start=at(9, 40))
    h.facade.scan_results = [board("APP", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    run(h, at(9, 40), at(9, 41))
    assert [b["symbol"] for b in brackets(h)] == ["APP"]
    assert h.ctl.session_sizing()["late"] is True
    assert r.account.positions["APP"].shares == 454


@pytest.mark.parametrize("start", [at(10, 20), at(11, 30)])
def test_process_first_started_after_the_cutoff_never_scans_or_trades(main_runtime, start):
    r = main_runtime
    h = session(r, start=start)
    run(h, start, start + timedelta(minutes=2))
    assert not [c for c in h.facade.calls if c[0] in ("scan", "decide")] and not h.alpaca.writes


def test_restart_at_1130_with_a_live_bracket_flattens_it(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    h.clock.set(at(11, 30))
    rebuild(r, h, "live")
    n = len(h.alpaca.requests)
    run(h, at(11, 30), at(11, 30, 30))
    h.assert_orb_closed_through_its_controller(n)


def test_clock_jumps_past_eleven_and_back_never_reenters_the_symbol(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    run(h, at(11, 0, 1), at(11, 0, 20))             # NTP skew: the clock reads 11:00 for 20 s
    assert "APP" not in h.alpaca.positions
    h.facade.scan_results = [board("APP", board_id="sec")] * 5
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))] * 5
    h.sched.state["final_ok"] = True
    run(h, at(9, 50), at(9, 53))                    # back to the real time
    # the real guard is the facade: every secondary scan and decision gets APP in the durable executed set
    sec = [c for c in h.facade.calls if c[0] == "scan" and c[3] == "secondary"]
    dec = [c for c in h.facade.calls if c[0] == "decide" and c[3] == "secondary"]
    assert sec and all("APP" in c[5] for c in sec) and all("APP" in c[5] for c in dec)


def test_clock_back_an_hour_after_the_primary_decision_never_decides_twice(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0)), decision(pick("PLTR", "long", 50.0, 48.9))]
    run(h, at(9, 10), at(9, 39))
    primaries = [c for c in h.facade.calls if c[0] == "decide" and c[3] == "primary"]
    run(h, at(8, 39), at(8, 41))                    # DST-like backward jump
    run(h, at(9, 38, 30), at(9, 39, 30))
    assert [c for c in h.facade.calls if c[0] == "decide" and c[3] == "primary"] == primaries
    assert [b["symbol"] for b in brackets(h)] == ["APP"]


# ----------------------------------------------------------------------------- data outages
def test_decider_crash_and_relay_outage_mean_no_decision_and_no_order(main_runtime):
    r = main_runtime
    h = session(r)

    def boom():
        raise RuntimeError("relay 502")
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [boom]
    run(h, at(9, 10), at(9, 40))
    assert not h.alpaca.writes
    assert h.sched.state["steps"]["primary_decision"]["state"] == "failed"


def test_absorption_reader_outage_never_stops_the_fast_fail(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()

    def boom(*a):
        raise RuntimeError("tape read failed")
    h.facade.absorption_poll = boom
    h.alpaca.prices["APP"] = 101.4                  # +0.55R arms absorption (outage)
    h.run_supervisor(passes=2)
    h.alpaca.prices["APP"] = 99.30
    n = len(h.alpaca.requests)
    h.run_supervisor(passes=2)
    assert "APP" not in h.alpaca.positions


def test_positions_and_relay_down_at_eleven_still_flattens(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    h.alpaca.fail.append({"method": "GET", "path": "/v2/positions", "kind": "status", "status": 503, "times": 3})
    h.facade.trade_override["APP"] = None
    n = len(h.alpaca.requests)
    run(h, at(11, 0), at(11, 0, 40))
    assert "APP" not in h.alpaca.positions and "APP" not in r.account.positions


def test_below_ninety_percent_coverage_twice_means_no_decision(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", coverage=0.89), board("APP", coverage=0.5)]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    run(h, at(9, 10), at(9, 50))
    assert not [c for c in h.facade.calls if c[0] == "decide"] and not h.alpaca.writes


def test_empty_board_and_sit_out_verdicts_send_nothing(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board(), {"ok": True, "error": None, "coverage": 1.0, "cards": [], "board_id": "final"}]
    h.facade.decide_results = [{"verdict": "refused", "reason": "the board is empty", "picks": []}]
    run(h, at(9, 10), at(9, 40))
    assert not h.alpaca.writes
    h2 = session(r)
    h2.facade.scan_results = [board("A", "B", "C", "D"), board("A", "B", "C", "D", board_id="final")]
    h2.facade.decide_results = [{"verdict": "sit_out", "reason": "Board is 0% short (<25% limit)", "picks": [],
                                 "regime": {"classification": "ONE_SIDED", "action": "SIT_OUT_CASH"}}]
    run(h2, at(9, 10), at(9, 40))
    assert not h2.alpaca.writes


# ----------------------------------------------------------------------------- holiday / half-day
def _calendar_session(r, day):
    from datetime import datetime as _dt
    from backend.tests.unit.orb_execution.fakes import ET
    h = MainOrb(r, start=_dt(day.year, day.month, day.day, 9, 10, tzinfo=ET), freeze=False)
    r.orb.build(h.broker, h.facade, "live", clock=h.clock, inline=True, monotonic=lambda: h.clock.now.timestamp(),
                sleep=h.clock.sleep, budget=RequestBudget(10 ** 6, 10 ** 6), restore=False)   # real NYSE calendar
    h.ctl, h.sched = r.orb.controller, r.orb.scheduler
    h.alpaca.prices["APP"] = 100.2
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    return h


def _t(day, hh, mm, ss=0):
    from datetime import datetime as _dt
    from backend.tests.unit.orb_execution.fakes import ET
    return _dt(day.year, day.month, day.day, hh, mm, ss, tzinfo=ET)


def test_thanksgiving_holiday_never_scans_or_trades(main_runtime):
    from datetime import date as _d
    r = main_runtime
    day = _d(2026, 11, 26)
    h = _calendar_session(r, day)
    run(h, _t(day, 9, 10), _t(day, 10, 0))
    assert h.sched.state["session"] is False
    assert not [c for c in h.facade.calls if c[0] in ("prep", "scan", "decide")] and not h.alpaca.writes


def test_half_day_trades_the_morning_and_is_flat_by_eleven(main_runtime):
    from datetime import date as _d
    r = main_runtime
    day = _d(2026, 11, 27)                           # 1:00 PM early close
    h = _calendar_session(r, day)
    run(h, _t(day, 9, 10), _t(day, 9, 40))
    assert [b["symbol"] for b in brackets(h)] == ["APP"]
    run(h, _t(day, 10, 59, 55), _t(day, 11, 0, 30))
    assert "APP" not in h.alpaca.positions and "APP" not in r.account.positions
