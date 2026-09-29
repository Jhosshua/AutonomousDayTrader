"""QA finding (2026-09-28): with a real state store, ORB's pre-order barrier (the scheduler state must be
durable before any order) timed out in INLINE scheduler mode and refused the order. These tests run a
full pick -> order with the REAL persistence store and the scheduler both on worker threads (production)
and inline, and require the order to go out without waiting for the barrier's timeout."""
import time as _time

import pytest

from backend.app.core.persistence import TradingStateStore
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick


def _morning(r, tmp_path, inline):
    r.state_store = TradingStateStore(str(tmp_path / f"s-{inline}.sqlite3"))
    h = MainOrb(r, start=at(9, 10), inline=inline, freeze=False)
    h.facade.scan_results = [{"ok": True, "error": None, "coverage": 1.0,
                              "cards": [{"symbol": "APP", "direction": "long"}], "board_id": "b"}] * 2
    h.facade.decide_results = [{"verdict": "trade", "reason": None, "picks": [pick("APP", "long", 100.0, 98.0)],
                                "audit": []}]
    h.alpaca.prices["APP"] = 100.2
    return h


@pytest.mark.parametrize("inline", [False, True], ids=["worker-threads(production)", "inline"])
def test_full_pick_to_order_with_the_real_store_never_waits_on_the_barrier(main_runtime, tmp_path, inline):
    r = main_runtime
    h = _morning(r, tmp_path, inline)
    try:
        t0 = _time.monotonic()
        for t in (at(9, 15), at(9, 20), at(9, 36, 10), at(9, 38, 30), at(9, 38, 35)):
            h.clock.set(t)
            for _ in range(5 if inline else 20):
                r.orb.tick(h.clock.now)
                if not inline:
                    _time.sleep(0.02)
        deadline = _time.monotonic() + 8
        while _time.monotonic() < deadline and "APP" not in r.account.positions:
            r.orb.tick(h.clock.now)
            _time.sleep(0.02)
        assert "APP" in r.account.positions, r.orb.status()
        assert _time.monotonic() - t0 < 8.0                  # nowhere near the 10 s barrier timeout
        assert h.ctl.last_execute["ok"]
        assert r.state_store.load_orb_state("scheduler")["steps"]["primary_decision"]["state"] in ("running", "done")
    finally:
        r.orb.shutdown()
        r.state_store.close()
