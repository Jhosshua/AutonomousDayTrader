"""ORB scheduler: ORBStraddle's app.py morning timeline, restart resume and non-blocking ticks."""
import threading
import time as _time
from datetime import timedelta

from backend.app.core.orb_scheduler import OrbScheduler
from backend.tests.unit.orb_execution.fakes import MANIFEST, Harness, at, pick


def board(*syms, coverage=1.0, ok=True, board_id=None):
    return {"ok": ok, "error": None if ok else "relay failed", "coverage": coverage,
            "cards": [{"symbol": s, "direction": "long"} for s in syms], "board_id": board_id or "-".join(syms)}


def decision(*picks_, verdict="pick", reason="picked"):
    return {"verdict": verdict, "reason": reason, "picks": list(picks_), "audit": [], "regime": {}}


def make(h, **kw):
    states = []
    s = OrbScheduler(h.ctl, h.facade, MANIFEST, clock=h.clock, is_session=kw.pop("is_session", lambda d: True),
                     persist_cb=states.append, inline=kw.pop("inline", True),
                     monotonic=lambda: h.clock.now.timestamp(), **kw)
    return s, states


def run(s, h, start, end, step=5):
    h.clock.set(start)
    while h.clock.now <= end:
        s.tick()
        h.clock.advance(step)


def calls(h, name):
    return [c for c in h.facade.calls if c[0] == name]


def fresh(mode="live"):
    h = Harness(mode=mode, start=at(9, 0), freeze=False)
    h.alpaca.prices["APP"] = 100.2
    return h


def test_full_morning_timeline_primary_trade():
    h = fresh()
    h.facade.scan_results = [board("APP", "PLTR"), board("APP", "PLTR", board_id="final")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    s, states = make(h)
    run(s, h, at(9, 0), at(9, 15, 30))
    assert s.state["steps"]["sizing"]["state"] == "done" and h.ctl.session_sizing()["equity"] == 50000.0
    assert s.state["steps"]["prep"]["state"] == "done" and len(calls(h, "prep")) == 1
    run(s, h, at(9, 15, 30), at(9, 36, 5))
    assert not calls(h, "scan")
    run(s, h, at(9, 36, 10), at(9, 38, 25))
    assert [c[2:4] for c in calls(h, "scan")] == [("09:36", "preview")]
    run(s, h, at(9, 38, 30), at(9, 39, 0))
    assert [c[2:4] for c in calls(h, "scan")][1] == ("09:38", "primary")
    assert calls(h, "decide")[0][2:4] == ("final", "primary")
    assert s.state["steps"]["primary_decision"]["state"] == "done"
    assert "1 order(s) placed" in s.state["steps"]["primary_decision"]["detail"]
    assert h.pos()["status"] == "OPEN" and h.ctl.own_qty("APP") == 454
    assert "Bought APP (long)" in s.status()["step"]
    assert states and states[-1]["last_verdict"]["picks"][0]["symbol"] == "APP"


def test_final_scan_low_coverage_retries_once_then_decides():
    h = fresh()
    h.facade.scan_results = [board("APP"), board("APP", coverage=0.85), board("APP", coverage=0.95)]
    s, _ = make(h)
    run(s, h, at(9, 15), at(9, 38, 35))
    assert [c[3] for c in calls(h, "scan")] == ["preview", "primary", "primary"]
    assert s.state["steps"]["final"]["state"] == "done" and "(retry)" in s.state["steps"]["final"]["detail"]
    assert len(calls(h, "decide")) == 1


def test_final_scan_failing_twice_means_no_decision_and_no_secondary():
    h = fresh()
    h.facade.scan_results = [board("APP"), board("APP", coverage=0.5), board(ok=False)]
    s, _ = make(h)
    run(s, h, at(9, 15), at(10, 20), step=15)
    assert s.state["steps"]["final"]["state"] == "failed"
    assert s.state["steps"]["primary_decision"]["state"] == "skipped"
    assert not calls(h, "decide")
    assert [c[3] for c in calls(h, "scan")] == ["preview", "primary", "primary"]   # no secondary scans
    assert "did not complete" in s.status()["step"]


def test_secondary_wave_dedupe_skip_list_and_cutoff():
    h = fresh()
    h.alpaca.prices.update(PLTR=50.1)
    h.facade.decide_results = [decision(verdict="pass", reason="no card passed"),      # primary
                               decision(verdict="pass", reason="no card passed"),      # secondary {AAA}
                               decision(pick("PLTR", "long", 50.0, 48.9, tier="quant"))]  # {AAA, PLTR}
    h.facade.scan_results = [board("X"), board("X", board_id="final"),
                             board("AAA"),            # 09:45 -> decide
                             board("AAA"),            # 09:46 -> nothing new, no decision
                             board("AAA", "PLTR")]    # 09:47 -> decide, PLTR traded
    s, _ = make(h)
    run(s, h, at(9, 15), at(9, 44, 55))
    assert len(calls(h, "decide")) == 1
    run(s, h, at(9, 45), at(9, 47, 30))
    sec = [c for c in calls(h, "scan") if c[3] == "secondary"]
    assert [c[2] for c in sec] == ["09:45", "09:46", "09:47"]
    assert [c[3] for c in calls(h, "decide")] == ["primary", "secondary", "secondary"]
    assert h.ctl.own_qty("PLTR") > 0
    run(s, h, at(9, 48), at(9, 48, 30))
    assert "PLTR" in [c for c in calls(h, "scan") if c[3] == "secondary"][-1][4]   # traded symbol skipped
    n = len(calls(h, "scan"))
    run(s, h, at(10, 15), at(10, 20), step=20)
    assert len(calls(h, "scan")) == n                                             # none after 10:15
    assert s.state["steps"]["cutoff"]["state"] == "done"


def test_no_secondary_scans_when_every_slot_is_used():
    h = fresh()
    h.ctl.cfg["max_open_slots"] = 1
    h.facade.scan_results = [board("APP"), board("APP")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    s, _ = make(h)
    run(s, h, at(9, 15), at(9, 50), step=10)
    assert h.ctl.slots_available() == 0
    assert not [c for c in calls(h, "scan") if c[3] == "secondary"]


def test_restart_resumes_each_step():
    h = fresh()
    h.facade.scan_results = [board("APP"), board("APP")]
    s, states = make(h)
    run(s, h, at(9, 15), at(9, 20))
    assert s.state["steps"]["sizing"]["state"] == "done"
    # restart 1: after sizing + prep. Sizing is not redone; prep is (it lives in memory)
    s2, states2 = make(h)
    s2.from_state(states[-1])
    run(s2, h, at(9, 21), at(9, 21, 10))
    assert len(calls(h, "prep")) == 2
    assert s2.state["steps"]["sizing"]["state"] == "done"
    # restart 2: after the final scan, before the decision ran: the board is rescanned once
    run(s2, h, at(9, 36, 10), at(9, 38, 30))
    st = s2.to_state()
    st["steps"]["primary_decision"] = {"state": "pending", "detail": None, "at": None}
    h.facade.scan_results = [board("APP", board_id="rescan")]
    h.facade.decide_results = [decision(verdict="pass", reason="nothing")]
    s3, _ = make(h)
    s3.from_state(st)
    decides = len(calls(h, "decide"))
    run(s3, h, at(9, 39), at(9, 39, 30))
    assert calls(h, "decide")[-1][2] == "rescan" and len(calls(h, "decide")) == decides + 1
    # restart 3: the primary decision was running when the app died: never re-run
    st = s3.to_state()
    st["steps"]["primary_decision"] = {"state": "running", "detail": "deciding", "at": None}
    s4, _ = make(h)
    s4.from_state(st)
    n = len(calls(h, "decide"))
    run(s4, h, at(9, 40), at(9, 41))
    assert s4.state["steps"]["primary_decision"]["state"] == "interrupted"
    assert len(calls(h, "decide")) == n


def test_restart_keeps_the_secondary_judged_set():
    h = fresh()
    h.facade.scan_results = [board("X"), board("X"), board("AAA")]
    s, states = make(h)
    run(s, h, at(9, 15), at(9, 45, 10))
    assert calls(h, "decide")[-1][3] == "secondary"
    n = len(calls(h, "decide"))
    s2, _ = make(h)
    s2.from_state(states[-1])
    h.facade.scan_results = [board("AAA")]
    run(s2, h, at(9, 46, 10), at(9, 46, 20))
    assert len(calls(h, "decide")) == n            # AAA already judged before the restart


def test_app_started_after_the_cutoff_does_not_scan():
    h = fresh()
    s, _ = make(h)
    run(s, h, at(10, 16), at(10, 17), step=10)
    assert not calls(h, "scan") and s.state["steps"]["final"]["state"] == "skipped"


def test_no_session_day_does_nothing():
    h = fresh()
    s, _ = make(h, is_session=lambda d: False)
    run(s, h, at(9, 10), at(10, 0), step=30)
    assert not h.facade.calls and s.status()["step"] == "No market session today."
    assert h.alpaca.writes == []


def test_unverifiable_calendar_is_no_session():
    h = fresh()

    def broken(d):
        raise RuntimeError("relay calendar down")
    s, _ = make(h, is_session=broken)
    run(s, h, at(9, 10), at(9, 40), step=30)
    assert not [c for c in h.facade.calls if c[0] in ("prep", "scan", "decide")]


def test_off_mode_scans_nothing():
    h = fresh(mode="off")
    s, _ = make(h)
    run(s, h, at(9, 15), at(9, 50), step=30)
    assert not [c for c in h.facade.calls if c[0] in ("prep", "scan", "decide")]
    assert "switched off" in s.status()["step"]


def test_shadow_morning_decides_and_sends_nothing():
    h = fresh(mode="shadow")
    h.facade.scan_results = [board("APP"), board("APP")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    s, _ = make(h)
    run(s, h, at(9, 15), at(11, 1), step=30)
    assert calls(h, "decide") and h.alpaca.writes == []
    assert [p["status"] for p in h.ctl.state["positions"].values()] == ["SHADOW"]


def test_eleven_flatten_runs_through_the_scheduler_supervisor():
    h = fresh()
    h.facade.scan_results = [board("APP"), board("APP")]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    s, _ = make(h)
    run(s, h, at(9, 15), at(9, 39), step=10)
    assert h.pos()["status"] == "OPEN"
    run(s, h, at(10, 59, 50), at(11, 0, 10))
    assert h.pos()["status"] == "CLOSED" and h.pos()["closed_reason"] == "flatten"
    assert s.state["steps"]["flatten"]["state"] == "done"
    assert "Done for today" not in s.status()["step"] or h.clock.now.hour >= 11


def test_status_next_step_and_plain_sentences():
    h = fresh()
    s, _ = make(h)
    h.clock.set(at(9, 5))
    s.tick()
    st = s.status()
    assert st["next_step"]["what"].startswith("freeze sizing") and "9:15" in st["step"]


def test_tick_never_blocks_and_a_late_scan_is_discarded():
    h = fresh()
    gate = threading.Event()

    def slow_scan():
        gate.wait(5)
        return board("APP")
    h.facade.scan_results = [board("X"), slow_scan]
    h.facade.decide_results = [decision(pick("APP", "long", 100.0, 98.0))]
    s, _ = make(h, inline=False, deadlines={"final": 30.0})
    try:
        h.clock.set(at(9, 15))
        s.tick()
        worst = 0.0
        deadline = _time.monotonic() + 5
        while s.state["steps"]["preview"]["state"] != "done" and _time.monotonic() < deadline:
            if h.clock.now.time() < at(9, 36, 10).time():
                h.clock.set(at(9, 36, 10))
            t0 = _time.perf_counter(); s.tick(); worst = max(worst, _time.perf_counter() - t0)
            _time.sleep(0.01)
        h.clock.set(at(9, 38, 30))
        for _ in range(20):
            t0 = _time.perf_counter(); s.tick(); worst = max(worst, _time.perf_counter() - t0)
            _time.sleep(0.005)
        assert s.state["steps"]["final"]["state"] == "running"
        h.clock.advance(31)                                    # past the 30 s deadline
        s.tick()
        assert s.state["steps"]["final"]["state"] == "failed"
        gate.set()
        _time.sleep(0.2)
        for _ in range(5):
            s.tick()
            _time.sleep(0.02)
        assert not calls(h, "decide")                          # the late board is never acted on
        assert s.state["steps"]["final"]["state"] == "failed" and not s.state["final_ok"]
        assert h.alpaca.writes == []
        assert worst < 0.05, worst
    finally:
        gate.set()
        s.shutdown()
