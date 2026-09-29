"""ADT's event loop must not stall while ORB scans (dry run RISK 1: 0.4-2.6 s GIL stalls in-process).
A heavy scan (every relay answer burns pure-Python CPU on the scanner's 24 threads) runs through the
decision-process proxy while an asyncio loop measures its own lag: it must stay under 100 ms. The same
load in-process is measured too, to prove the probe sees GIL stalls at all."""
import asyncio
import functools
import os
import threading
import time
from datetime import date, datetime

import pytest

from backend.tests.unit.orbs._helpers import SYNTH_DAY, parity_modules, unpack_fixture

BURN_S = 0.2


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    r = tmp_path_factory.mktemp("lag")
    unpack_fixture(str(r))
    return str(r)


def _scan_once(fac):
    common, _cr, _cmp = parity_modules()
    d = date.fromisoformat(SYNTH_DAY)
    fac.prep(d)
    step = next(s for s in common.schedule(SYNTH_DAY) if s["wave"] == "primary")
    return fac.scan(d, step["scan_now"].replace(second=0), "primary", set())


def _measure_lag_while(work):
    """Run work() on a thread; return the worst asyncio loop lag (s) seen meanwhile."""
    done = threading.Event()
    out = {}

    def run():
        try:
            out["r"] = work()
        finally:
            done.set()

    async def probe():
        worst = 0.0
        t = threading.Thread(target=run)
        t.start()
        while not done.is_set():
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - t0 - 0.01)
        t.join()
        return worst
    return asyncio.run(probe()), out.get("r")


def test_a_heavy_scan_in_the_decision_process_never_stalls_the_event_loop(root):
    from backend.app.core.orb_facade_proc import FacadeProxy
    common, _cr, _cmp = parity_modules()
    old = common.CACHE_ROOT
    common.CACHE_ROOT = root
    heavy = __import__("backend.tests.unit.orbs._heavy_http", fromlist=["make"])
    try:
        fac = FacadeProxy(os.path.join(root, "state_proxy"), common.RELAY_ROOT, "t", common.load_manifest(),
                          http_factory=functools.partial(heavy.make, root, SYNTH_DAY, BURN_S))
        fac.start()
        try:
            lag, board = _measure_lag_while(lambda: _scan_once(fac))
        finally:
            fac.close()
        assert board and board.get("health") and board["health"].get("attempted", 0) > 0
        assert lag < 0.100, f"event loop lag {lag * 1000:.0f} ms during a scan in the decision process"

        # the same load in THIS process: the probe must see the GIL stall (otherwise it proves nothing)
        from backend.app.strategies.orbs import config as ocfg
        ocfg.apply_manifest(common.load_manifest())
        from backend.app.strategies.orbs.facade import OrbsFacade
        inproc = OrbsFacade(os.path.join(root, "state_in"), common.RELAY_ROOT, "t", manifest=common.load_manifest(),
                            http=heavy.make(root, SYNTH_DAY, BURN_S))
        lag_in, _ = _measure_lag_while(lambda: _scan_once(inproc))
        assert lag_in > 0.100, f"the probe did not see the in-process GIL stall ({lag_in * 1000:.0f} ms)"
    finally:
        common.CACHE_ROOT = old
