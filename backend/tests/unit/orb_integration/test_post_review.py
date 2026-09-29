"""Post-deployment review of ORB's entry controls in the real ADT runtime."""
import asyncio
import threading
import time

import pytest

from backend.app.strategies.base import StrategyStatus
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick


@pytest.mark.parametrize("status", [StrategyStatus.PAUSED, StrategyStatus.COOLDOWN])
def test_an_inactive_orb_strategy_sends_no_new_orders(main_runtime, monkeypatch, status):
    r = main_runtime
    h = MainOrb(r)
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    monkeypatch.setattr(r.strategy_map["orb"], "status", status)
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and not [q for q in h.alpaca.requests if q[0] == "POST"], out


def test_pausing_orb_still_allows_its_existing_trade_to_exit(main_runtime, monkeypatch):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    monkeypatch.setattr(r.strategy_map["orb"], "status", StrategyStatus.PAUSED)
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.3
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)


def test_a_flatten_request_never_waits_on_an_entry_post_on_the_event_loop(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    posting, release = threading.Event(), threading.Event()
    real = h.broker.submit_bracket

    def slow_post(*args, **kwargs):
        posting.set()
        assert release.wait(3)
        return real(*args, **kwargs)
    h.broker.submit_bracket = slow_post
    entry = threading.Thread(target=lambda: h.ctl.execute([pick("APP", "long", 100.0, 98.0)]))
    entry.start()
    timer = None
    try:
        assert posting.wait(3)
        timer = threading.Timer(0.5, release.set)
        timer.start()

        async def flatten():
            start = time.monotonic()
            assert r.orb.request_all_exits("MANUAL_FLATTEN_ALL") == ["APP"]
            return time.monotonic() - start
        elapsed = asyncio.run(flatten())
        assert elapsed < 0.1, f"ADT's event loop blocked for {elapsed:.3f}s on the broker POST"
        assert h.ctl.entries_blocked() == "MANUAL_FLATTEN_ALL"
    finally:
        release.set()
        entry.join(3)
        if timer is not None:
            timer.cancel()
            timer.join(3)
    h.ctl.tick()
    r.orb.sync(h.clock.now)
    assert not h.alpaca.positions and not r.account.positions


def test_slow_controller_state_saves_do_not_block_exit_requests(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    class SlowStore:
        def __init__(self):
            self.saved = []

        def save_orb_state(self, section, state):
            time.sleep(0.2)
            self.saved.append((section, state))
    store = SlowStore()
    r.state_store = store
    try:
        async def request():
            start = time.monotonic()
            assert r.orb.request_exit("APP", "MANUAL_FLATTEN")
            return time.monotonic() - start
        elapsed = asyncio.run(request())
        assert elapsed < 0.1, f"exit request blocked ADT's loop for {elapsed:.3f}s on SQLite"
        r.orb.flush_controller_state()
        assert any(st["exit_requests"].get("APP") == "MANUAL_FLATTEN" for _, st in store.saved)
        writer = r.orb._ctl_thread
    finally:
        r.orb.shutdown()
        r.state_store = None
    assert writer is not None and not writer.is_alive()


def test_a_failed_exit_request_save_blocks_entries_until_a_durable_save_recovers(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()

    class RecoveringStore:
        fail = True

        def save_orb_state(self, section, state):
            if self.fail:
                raise OSError("disk unavailable")

    store = RecoveringStore()
    r.state_store = store
    try:
        async def request():
            assert r.orb.request_exit("APP", "MANUAL_FLATTEN")
        asyncio.run(request())
        with pytest.raises(RuntimeError, match="disk unavailable"):
            r.orb.flush_controller_state()
        assert "PERSISTENCE_RECOVERY_HALT" in r.orb.entry_gate("NVDA")
        assert h.ctl.state["exit_requests"]["APP"] == "MANUAL_FLATTEN"
        store.fail = False
        h.ctl._persist()
        r.orb.flush_controller_state()
        assert r.orb.entry_gate("NVDA") is None
        h.ctl.tick()
        r.orb.sync(h.clock.now)
        assert not h.alpaca.positions and not r.account.positions
    finally:
        r.orb.shutdown()
        r.state_store = None
