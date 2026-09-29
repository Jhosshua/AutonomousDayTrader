"""ORB's decision code in a child process: when it is down ORB opens nothing, exits keep working (prices
from Alpaca's positions, absorption advisory), and it is restarted off the event loop."""
import pytest

from backend.app.core.orb_facade_proc import FacadeDown
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick


class DownFacade:
    """What the controller sees while the decision process is down: every call fails."""

    def __getattr__(self, name):
        def fail(*a, **kw):
            raise FacadeDown("the ORB decision process is down (test)")
        return fail


class FakeProc:
    def __init__(self):
        self.up, self.restarts, self.last_error = False, 0, "the process exited"

    def healthy(self):
        return self.up

    def pid(self):
        return None

    def restart(self):
        self.restarts += 1
        self.up = True

    def close(self, timeout=5.0):
        self.up = False


def test_with_the_decision_process_down_exits_still_work_and_entries_are_refused(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    h.ctl.facade = DownFacade()
    r.orb.facade_proc = proc = FakeProc()
    assert r.orb.entry_gate("PLTR") == "ORB's decision process is restarting"
    h.alpaca.prices["PLTR"] = 50.1
    out = h.ctl.execute([pick("PLTR", "long", 50.0, 49.0)], h.clock.now)
    assert not out["ok"] and not [q for q in h.alpaca.requests if q[0] == "POST" and q[2].get("symbol") == "PLTR"]
    # the exit side: fast-fail fires from Alpaca's position price, absorption failing is ignored
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    h.ctl.tick()
    r.orb.sync(h.clock.now)
    h.assert_orb_closed_through_its_controller(n)
    assert h.pos()["closed_reason"] == "fast-fail"


def test_a_supervisor_pass_without_positions_data_falls_back_to_a_second_alpaca_read(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    h.ctl.facade = DownFacade()
    h.alpaca.fail.append({"method": "GET", "path": "/v2/positions", "kind": "status", "status": 503, "times": 1})
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and h.pos()["closed_reason"] == "fast-fail"


def test_a_dead_decision_process_is_restarted_off_the_loop_with_a_back_off(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    r.orb.facade_proc = proc = FakeProc()
    r.orb.tick(h.clock.now)
    r.orb._facade_restart.join(2)
    assert proc.restarts == 1 and proc.up
    assert any(e.get("kind") == "decision_process_down" for e in r.orb.current_errors())
    proc.up = False
    r.orb.tick(h.clock.now)                        # inside the back-off: not yet
    assert proc.restarts == 1
    r.orb._facade_next_restart = 0.0
    r.orb.tick(h.clock.now)
    r.orb._facade_restart.join(2)
    assert proc.restarts == 2


def test_the_real_decision_process_starts_answers_restarts_and_closes(tmp_path):
    from backend.app.core.orb_facade_proc import FacadeProxy
    from backend.app.strategies.orbs import config as ocfg
    proxy = FacadeProxy(str(tmp_path / "orbs"), "https://relay.invalid", "t", ocfg.load_manifest(),
                        exclude=["TSLA", "CDE"])
    proxy.start()
    try:
        assert proxy.healthy() and proxy.ping()
        assert proxy.effective_config()["EXCLUDE_SYMBOLS"] == ["CDE", "TSLA"]
        with pytest.raises(ValueError):
            proxy.scan(at(9, 38).date(), at(9, 38).replace(tzinfo=None), "primary", [])   # the facade's own check
        pid = proxy.pid()
        proxy._proc.kill()                                    # a crash
        proxy._proc.join(5)
        import time
        deadline = time.monotonic() + 5
        while proxy.healthy() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not proxy.healthy()
        with pytest.raises(FacadeDown):
            proxy.latest_trade("APP")
        proxy.restart()
        assert proxy.healthy() and proxy.pid() != pid and proxy.ping()
        assert proxy.effective_config()["EXCLUDE_SYMBOLS"] == ["CDE", "TSLA"]   # re-applied in the new child
    finally:
        proxy.close()
    assert not proxy.healthy()


def test_drain_closes_the_decision_process(main_runtime):
    r = main_runtime
    MainOrb(r)
    r.orb.facade_proc = proc = FakeProc()
    proc.up = True
    r.orb.drain(timeout=0.1, extra_wait=0.1)
    assert not proc.up


def test_a_call_in_flight_fails_at_once_when_the_process_dies(tmp_path):
    import threading
    import time
    from backend.app.core.orb_facade_proc import FacadeProxy
    from backend.app.strategies.orbs import config as ocfg
    from backend.tests.unit.orbs._heavy_http import make_stall
    proxy = FacadeProxy(str(tmp_path / "orbs"), "https://relay.invalid", "t", ocfg.load_manifest(),
                        http_factory=make_stall)
    proxy.start()
    out = {}

    def call():
        t0 = time.monotonic()
        try:
            proxy.prep(at(9, 20).date())
        except Exception as exc:
            out["exc"] = exc
        out["s"] = time.monotonic() - t0
    try:
        t = threading.Thread(target=call)
        t.start()
        time.sleep(0.5)
        proxy._proc.kill()
        t.join(10)
        assert isinstance(out.get("exc"), FacadeDown) and out["s"] < 5          # not the 900 s prep timeout
    finally:
        proxy.close(timeout=1)


def test_a_process_that_cannot_start_is_reported_down(tmp_path):
    from backend.app.core.orb_facade_proc import FacadeProxy
    proxy = FacadeProxy(str(tmp_path / "orbs"), "https://relay.invalid", "t", {"not": "a manifest"})
    with pytest.raises(FacadeDown, match="could not start"):
        proxy.start()
    assert not proxy.healthy()
