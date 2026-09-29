"""ORB's decision code (OrbsFacade) in its own long-lived child process.

Why (dry run 2026-09-28, docs/orb_replacement/DRY_RUN_REPORT.md RISK 1): the copied scanner runs 24
pure-Python worker threads; in ADT's process they held the GIL and stalled ADT's event loop 0.4-2.6 s during
09:36-10:15, while that loop manages ADT's other positions' stops. In a child process the scans use their own
interpreter and GIL.

`FacadeProxy` has the facade's API (prep, scan, decide, recheck, macro_veto, latest_trade, absorption_poll,
set_exclude_symbols, effective_config). Each call is sent over a pipe with a request id and answered by the
child, which runs every call on its own thread (a 4-minute scan never delays a supervisor's latest_trade);
the parent's reader thread hands each answer to its waiting caller; every call has a timeout. All facade
state (incremental scan state, the last successful board per wave, the lockout latch) lives in the child.

If the child dies or a call times out, the proxy marks itself down, fails the waiting calls, and the owner
(orb_integration.tick) restarts it with a back-off. While it is down ORB opens nothing (entry gate + the
calls fail closed); exits keep working because the supervisor prices from Alpaca's positions and treats the
facade's latest_trade / absorption as advisory. A restarted child has no scan state: a decision needs a new
successful scan (the facade refuses a board it did not produce).
"""
from __future__ import annotations

import itertools
import logging
import multiprocessing as mp
import threading
import time as _time
import traceback
from concurrent.futures import Future, TimeoutError as _FutTimeout
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("orb_facade_proc")

TIMEOUTS = {"prep": 900.0, "scan": 300.0, "decide": 120.0, "recheck": 30.0, "macro_veto": 20.0,
            "latest_trade": 15.0, "absorption_poll": 10.0, "set_exclude_symbols": 10.0,
            "effective_config": 10.0, "_ping": 5.0, "_http_attr": 10.0}
START_TIMEOUT_S = 120.0


class FacadeDown(RuntimeError):
    """The decision process is not running (crashed, restarting or stopped): nothing was decided."""


class FacadeTimeout(RuntimeError):
    """A call to the decision process did not answer in time: its result is unknown and discarded."""


# ------------------------------------------------------------------------------------------ child side
def _child_main(conn, state_dir: str, relay_base: str, relay_token: str, manifest: dict, exclude, http_factory):
    """Entry point of the child (spawned: a fresh interpreter, only the ORB decision modules imported)."""
    import sys
    from concurrent.futures import ThreadPoolExecutor
    try:
        from backend.app.strategies.orbs import config as ocfg
        ocfg.apply_manifest(manifest)        # before the scanner is imported (import-time tunables)
        from backend.app.strategies.orbs.facade import OrbsFacade
        http = http_factory() if http_factory else None
        fac = OrbsFacade(state_dir, relay_base, relay_token, manifest=manifest, http=http)
        if exclude is not None:
            fac.set_exclude_symbols(exclude)
    except BaseException as exc:  # noqa: BLE001 - reported to the parent, then exit
        conn.send(("ready", False, f"{type(exc).__name__}: {exc}"))
        return
    conn.send(("ready", True, None))
    send_lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="OrbFacadeCall")

    def run(rid, method, args, kwargs):
        try:
            if method == "_ping":
                out = "pong"
            elif method == "_http_attr":
                out = http
                for part in str(args[0]).split("."):
                    out = getattr(out, part, None)
            else:
                out = getattr(fac, method)(*args, **kwargs)
            msg = (rid, True, out)
        except BaseException as exc:  # noqa: BLE001
            msg = (rid, False, (type(exc).__name__, str(exc), traceback.format_exc()[-2000:]))
        try:
            with send_lock:
                conn.send(msg)
        except Exception:
            pass

    while True:
        try:
            req = conn.recv()
        except (EOFError, OSError):
            break
        if req is None:
            break
        rid, method, args, kwargs = req
        pool.submit(run, rid, method, args, kwargs)
    pool.shutdown(wait=False, cancel_futures=True)
    sys.exit(0)


# ------------------------------------------------------------------------------------------ parent side
_BUILTIN_ERRORS = {"ValueError": ValueError, "KeyError": KeyError, "TypeError": TypeError,
                   "RuntimeError": RuntimeError}


class FacadeProxy:
    """The OrbsFacade API, answered by the child process."""

    def __init__(self, state_dir: str, relay_base: str, relay_token: str, manifest: dict,
                 exclude=None, http_factory: Optional[Callable[[], Any]] = None,
                 timeouts: Optional[Dict[str, float]] = None) -> None:
        self._spec = (state_dir, relay_base, relay_token, manifest, list(exclude) if exclude is not None else None,
                      http_factory)
        self.timeouts = dict(TIMEOUTS, **(timeouts or {}))
        self._ctx = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: Dict[int, Future] = {}
        self._ids = itertools.count(1)
        self._proc = None
        self._conn = None
        self._reader: Optional[threading.Thread] = None
        self._up = False
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.restarts = 0
        self._stopping = False
        self._starting = None                  # (proc, conn) while a start waits for the child to be ready

    # ---- lifecycle
    def start(self, timeout: float = START_TIMEOUT_S) -> None:
        """Spawn the child and wait for it to be ready WITHOUT holding the lifecycle lock, so close() can
        cancel a start that stalls (it kills and reaps the starting child)."""
        with self._lock:
            self._stop_locked(kill=True)
            self._stopping = False
            parent, child = self._ctx.Pipe(duplex=True)
            proc = self._ctx.Process(target=_child_main, args=(child, *self._spec), name="orb-facade", daemon=True)
            proc.start()
            child.close()
            self._starting = (proc, parent)
        ready, err = False, "did not start in time"
        deadline = _time.monotonic() + timeout
        try:
            while _time.monotonic() < deadline and not self._stopping:
                if parent.poll(0.1):
                    _tag, ready, err = parent.recv()
                    break
                if not proc.is_alive():
                    err = f"exited during start (code {proc.exitcode})"
                    break
        except (EOFError, OSError) as exc:
            ready, err = False, f"exited during start ({exc})"
        with self._lock:
            if self._starting is None or self._starting[0] is not proc:
                raise FacadeDown("the ORB decision process start was cancelled")
            self._starting = None
            if self._stopping or not ready:
                self._reap(proc, parent)
                if self._stopping:
                    raise FacadeDown("the ORB decision process start was cancelled")
                raise FacadeDown(f"the ORB decision process could not start: {err}")
            self._proc, self._conn, self._up = proc, parent, True
            self.started_at = _time.monotonic()
            self._reader = threading.Thread(target=self._read_loop, args=(parent, proc), name="OrbFacadeReader",
                                            daemon=True)
            self._reader.start()
        log.info("ORB decision process started (pid %s)", proc.pid)

    @staticmethod
    def _reap(proc, conn, timeout: float = 2.0) -> None:
        """Bounded kill + reap of a child (starting or stalled)."""
        try:
            if proc.is_alive():
                proc.kill()
            proc.join(timeout)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    def restart(self) -> None:
        self.restarts += 1
        self.start()

    def healthy(self) -> bool:
        return bool(self._up and self._proc is not None and self._proc.is_alive())

    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    def close(self, timeout: float = 5.0) -> None:
        """Bounded: a starting child is killed and reaped; a running one gets `timeout` to exit, then is killed."""
        self._stopping = True                  # a start waiting for readiness sees this within 0.1 s
        with self._lock:
            starting, self._starting = self._starting, None
            if starting is not None:
                self._reap(*starting)
            self._stop_locked(kill=False, timeout=timeout)

    def _stop_locked(self, kill: bool, timeout: float = 5.0) -> None:
        proc, conn = self._proc, self._conn
        self._up = False
        if conn is not None:
            try:
                if not kill:
                    with self._send_lock:
                        conn.send(None)
            except Exception:
                pass
        if proc is not None:
            proc.join(timeout if not kill else 0.1)
            if proc.is_alive():
                proc.kill()
                proc.join(2)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._proc, self._conn = None, None
        self._fail_pending(FacadeDown("the ORB decision process stopped"))

    def _mark_down(self, why: str, conn=None) -> None:
        with self._lock:
            if conn is not None and self._conn is not conn:
                return                    # a late failure from an old child cannot stop its replacement
            if self._up:
                log.error("ORB decision process down: %s", why)
            self._up = False
            self.last_error = why
            self._fail_pending(FacadeDown(f"the ORB decision process is down ({why})"))

    def _fail_pending(self, exc: Exception) -> None:
        with self._pending_lock:
            pending, self._pending = self._pending, {}
            for fut in pending.values():
                if not fut.done():
                    fut.set_exception(exc)

    def _read_loop(self, conn, proc) -> None:
        while True:
            try:
                rid, ok, payload = conn.recv()
            except (EOFError, OSError):
                if self._conn is conn and not self._stopping:
                    self._mark_down("the process exited" if not proc.is_alive() else "its pipe closed", conn)
                return
            except Exception as exc:  # a reply that could not be unpickled
                log.error("ORB decision process reply unreadable: %s", exc)
                continue
            with self._pending_lock:
                fut = self._pending.pop(rid, None)
                if fut is None or fut.done():
                    continue
                if ok:
                    fut.set_result(payload)
                else:
                    name, msg, tb = payload
                    exc = _BUILTIN_ERRORS.get(name, RuntimeError)(f"{msg}" if name in _BUILTIN_ERRORS else f"{name}: {msg}")
                    fut.set_exception(exc)

    # ---- calls
    def _call(self, method: str, *args, **kwargs) -> Any:
        with self._lock:
            if not self.healthy():
                raise FacadeDown(f"the ORB decision process is not running ({self.last_error or 'not started'})")
            conn = self._conn
            rid = next(self._ids)
            fut: Future = Future()
            with self._pending_lock:
                self._pending[rid] = fut
        try:
            with self._send_lock:
                conn.send((rid, method, args, kwargs))
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(rid, None)
            self._mark_down(f"send failed: {exc}", conn)
            raise FacadeDown(f"the ORB decision process is down ({exc})") from exc
        timeout = self.timeouts.get(method, 60.0)
        try:
            return fut.result(timeout=timeout)
        except _FutTimeout:
            with self._pending_lock:
                self._pending.pop(rid, None)
            why = f"did not answer {method} within {timeout:.0f} s"
            self._mark_down(why, conn)
            raise FacadeTimeout(f"the ORB decision process {why}")

    def ping(self) -> bool:
        try:
            return self._call("_ping") == "pong"
        except Exception:
            return False

    def prep(self, day):
        return self._call("prep", day)

    def scan(self, day, end, wave, skip_symbols, executed_today=None):
        return self._call("scan", day, end, wave, set(skip_symbols or ()),
                          executed_today=None if executed_today is None else set(executed_today))

    def decide(self, day, board, wave, now, occupied, executed_today=None):
        return self._call("decide", day, board, wave, now, set(occupied or ()),
                          executed_today=None if executed_today is None else set(executed_today))

    def recheck(self, card, now):
        return self._call("recheck", card, now)

    def macro_veto(self, symbol, direction, now):
        return self._call("macro_veto", symbol, direction, now)

    def latest_trade(self, symbol):
        return self._call("latest_trade", symbol)

    def absorption_poll(self, symbol, direction, now):
        return self._call("absorption_poll", symbol, direction, now)

    def set_exclude_symbols(self, symbols):
        self._spec = self._spec[:4] + (list(symbols),) + self._spec[5:]
        return self._call("set_exclude_symbols", list(symbols))

    def effective_config(self):
        return self._call("effective_config")

    def http_attr(self, name: str):
        return self._call("_http_attr", name)
