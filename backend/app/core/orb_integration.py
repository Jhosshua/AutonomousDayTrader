# @steered SNARE-2 2026-09-30
"""ADT <-> ORB glue (phase 3 of PLAN_2026_09_28_orb_rules_match_orbstraddle.md, sections 2-5, 8, 9).

ORB (strategy id "orb") trades ORBStraddle's rules through its own controller
(core/orb_execution.py) and scheduler (core/orb_scheduler.py). This module wires them into the
running app (`runtime` = backend.app.main):

  * one reservation lock (`self.lock`) shared by ORB and every other ADT arm: ORB takes a symbol
    only if no ADT arm holds / has a working order / has a staged swing entry / has an entry in
    flight on it, and ADT arms refuse ("ORB_OWNED") a symbol ORB holds or reserved;
  * ORB's broker fills are booked into ADT's account and ledger on the event loop (idempotent,
    from the controller's cumulative per-order fills), so ADT's Alpaca position check stays true
    and trades land in /api/trades with strategy_id "orb";
  * ADT's daily loss stop halts ORB (account_halt) and ORB's open risk is reserved out of ADT's
    remaining daily-loss budget (risk_engine.reserved_risk_fn);
  * every ADT exit path hands ORB symbols to request_exit / request_all_exits, which cancel ORB's
    own parent and legs before closing exactly ORB's quantity;
  * the controller and scheduler state are their own durable rows (orb_state table), written by
    ORB's worker threads before they return; the ADT runtime checkpoint carries only the ledger
    linkage (which ORB fills are booked, which trades are recorded).

Threading: controller/scheduler work runs on the scheduler's worker threads. The hooks called from
those threads (persist, on_fill, is_occupied, reserve, release, account_halt, entry_gate, on_event)
never mutate ADT's account/engine: fills are booked by `sync()` on the event loop.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import threading
import time as _time
from collections import deque
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from concurrent.futures import wait as _wait_futures

from backend.app.core.engine import ORB_POLICY, OrderSide, OrderType
from backend.app.core.log_limit import warn_rate_limited

log = logging.getLogger("orb_integration")
ET = ZoneInfo("America/New_York")
ORB_ID = "orb"
MODES = ("off", "shadow", "live")
# 2: `booked` keyed by Alpaca order id (idempotent). Version 1 (keyed by controller record key) only ever
# existed on the unreleased orb-integration branch; any other version fails closed for ORB only.
LEDGER_VERSION = 2
DRAIN_EXTRA_WAIT_S = 60.0
ACTIVE = frozenset({"PENDING_SUBMIT", "SUBMITTED", "UNKNOWN", "OPEN"})
CLAIM_STATES = frozenset({"CREATED", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED"})
SYNC_EVERY_S = 5.0
MARK_BREAKER_EVERY_S = 5.0       # ORB price marks re-evaluate ADT's daily loss stop at most this often
DRAIN_TIMEOUT_S = 20.0


def rules_label() -> Dict[str, Any]:
    """The rules ADT's ORB copy runs, read from the live orbs config (PARITY_MANIFEST.json), never a literal.
    `flags` has the same keys ORBStraddle's /api/state `rules` dict carries (candle, delta, velocity, macro,
    deal); `text` is the one-line label the dashboard and closed-trade rows show, with the deal rule spelled
    out the way ORBStraddle's page prints it ("deal on")."""
    from backend.app.strategies.orbs import config as orbs_config
    flags = {"candle": bool(orbs_config.CANDLE_RULE), "delta": bool(orbs_config.DELTA_RULE),
             "velocity": bool(orbs_config.VELOCITY_RULE), "macro": bool(orbs_config.MACRO_RULE),
             "deal": bool(orbs_config.DEAL_RULE)}
    commit = (orbs_config.MANIFEST.get("source") or {}).get("commit") or "?"
    text = (f"ORBStraddle {orbs_config.RULES_VERSION} (@{commit}), deal {'on' if flags['deal'] else 'off'}")
    return {"text": text, "flags": flags, "rules_version": orbs_config.RULES_VERSION, "source_commit": commit}


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class OrbIntegration:
    def __init__(self, runtime: Any) -> None:
        self.r = runtime
        self.controller = None
        self.scheduler = None
        self.facade = None
        self.mode = "off"
        self.configured_mode = "off"
        self.lock = threading.RLock()            # THE shared symbol-reservation lock
        self._orb_reserved: set = set()
        self._adt_claims: Dict[str, str] = {}    # symbol -> local order id of an ADT entry in flight
        self.ledger: Dict[str, Any] = self._empty_ledger()
        self._mem_state: Dict[str, Any] = {}     # ORB state when there is no durable store
        self._dirty = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_sync = 0.0
        self.errors: deque = deque(maxlen=20)
        self.events: deque = deque(maxlen=50)
        self.init_error: Optional[str] = None
        self._orphans: set = set()
        self._persist_locks = {"controller": threading.Lock(), "scheduler": threading.Lock()}
        self._persist_enabled = True
        self.clock: Callable[[], datetime] = lambda: datetime.now(ET)
        # plain-language alerts (e.g. ORB shares in ADT's book that no ORB order explains)
        self.alerts: Dict[str, str] = {}
        self._mark_eval_pending = False
        self.ledger_error: Optional[str] = None
        self._bad_ledger: Optional[Any] = None
        self.unresolved_at_shutdown: List[dict] = []
        # timestamped price marks for ORB positions (P1 #2)
        self.marks: Dict[str, tuple] = {}
        self._last_mark_eval: Optional[float] = None
        # scheduler state writer (off the event loop, P2 #5)
        self._sched_cond = threading.Condition()
        self._sched_pending: Optional[dict] = None
        self._sched_busy = False
        self._sched_error: Optional[str] = None
        self._sched_thread: Optional[threading.Thread] = None
        self._ctl_cond = threading.Condition()
        self._ctl_pending = False
        self._ctl_busy = False
        self._ctl_error: Optional[str] = None
        self._ctl_thread: Optional[threading.Thread] = None
        self._ctl_stopping = False
        self._writes_closed = False
        self.facade_proc = None                  # FacadeProxy (child process) when started by start()
        self._facade_restart: Optional[threading.Thread] = None
        self._facade_next_restart = 0.0

    @staticmethod
    def _empty_ledger() -> Dict[str, Any]:
        # booked: BROKER ORDER ID -> {qty (cumulative shares booked), notional, local}: the per-order map that
        # makes booking idempotent (a controller rebuilt from an older or lost state that re-emits fills,
        # or re-registers an order under another record key, books nothing twice). It rides in ADT's
        # checkpoint, atomically with the account it booked into. recorded: position keys with a trade row;
        # logged: decision rows already written (dedupe across restarts)
        return {"version": LEDGER_VERSION, "booked": {}, "recorded": [], "logged": [], "resolved": []}

    # ------------------------------------------------------------------ construction
    def effective_manifest(self) -> dict:
        """PARITY_MANIFEST.json with ADT's TSLA/CDE exclusion applied (the manifest keeps [] for parity)."""
        from backend.app.strategies.orbs import config as orbs_config
        manifest = copy.deepcopy(orbs_config.load_manifest())
        manifest.setdefault("adt", {})["exclude_symbols"] = [
            str(s).upper() for s in self.r.settings.ORB_EXCLUDE_SYMBOLS]
        return manifest

    def state_dir(self) -> str:
        s = self.r.settings
        if s.ORB_STATE_DIR:
            return s.ORB_STATE_DIR
        return str(Path(s.STATE_DB_PATH).expanduser().resolve().parent / "orbs")

    def build(self, broker: Any, facade: Any, mode: str, *, clock: Optional[Callable[[], datetime]] = None,
              inline: bool = False, manifest: Optional[dict] = None, deadlines: Optional[dict] = None,
              monotonic: Optional[Callable[[], float]] = None, budget: Any = None,
              sleep: Optional[Callable[[float], None]] = None, is_session: Optional[Callable] = None,
              restore: bool = True, expected_account: Optional[str] = None, persist: bool = True):
        """Construct the controller + scheduler with ADT's hooks, restoring their durable state."""
        from backend.app.core.orb_execution import OrbExecutionController
        from backend.app.core.orb_scheduler import OrbScheduler
        if mode not in MODES:
            raise ValueError(f"ORB_MODE must be one of {MODES}, not {mode!r}")
        self.shutdown(close_facade=facade is not self.facade_proc)
        self._writes_closed = False
        self._ctl_stopping = False
        self._ctl_error = None
        self._persist_enabled = persist
        manifest = manifest if manifest is not None else self.effective_manifest()
        ctl = OrbExecutionController(
            broker, facade, manifest, clock=clock, persist_cb=self._persister("controller"),
            on_fill=self._on_fill, is_occupied=self.is_occupied, reserve=self.reserve, release=self.release,
            account_halt=self.account_halt, on_event=self._on_event, sleep=sleep, budget=budget, mode=mode,
            expected_account=expected_account or self.r.settings.ORB_EXPECTED_ACCOUNT,
            entry_gate=self.entry_gate)
        sched = OrbScheduler(
            ctl, facade, manifest, clock=clock, is_session=is_session or self._is_session,
            persist_cb=self._persister("scheduler"), inline=inline, deadlines=deadlines, monotonic=monotonic,
            on_decision=self._on_decision, persist_flush=self.flush_scheduler_state)
        if restore:
            ctl.from_state(self._load("controller"))
            sched.from_state(self._load("scheduler"))
        with self.lock:
            self._orb_reserved.clear()
        self.controller, self.scheduler, self.facade, self.mode = ctl, sched, facade, mode
        self.clock = clock or (lambda: datetime.now(ET))
        self._dirty.set()
        return ctl, sched

    def start(self) -> None:
        """Lifespan startup: the real facade (relay) and, in alpaca_paper mode, ADT's Alpaca broker."""
        s = self.r.settings
        self.configured_mode = mode = str(s.ORB_MODE or "off").strip().lower()
        if mode not in MODES:
            self.init_error = f"ORB_MODE {s.ORB_MODE!r} is not off/shadow/live; ORB is off"
            log.error(self.init_error)
            mode = "off"
        broker = self.r.alpaca_broker if s.BROKER_MODE.lower() == "alpaca_paper" else None
        if broker is None and mode == "live":
            self.init_error = "ORB_MODE=live needs BROKER_MODE=alpaca_paper; running ORB in shadow with no broker"
            log.warning(self.init_error)
            mode = "shadow"
        facade = None
        if mode != "off" or broker is not None:
            try:
                # the decision code runs in its own process: its scan threads never hold ADT's GIL
                from backend.app.core.orb_facade_proc import FacadeProxy
                facade = FacadeProxy(self.state_dir(), s.RELAY_HTTP_URL, s.RELAY_TOKEN,
                                     self.effective_manifest(), exclude=list(s.ORB_EXCLUDE_SYMBOLS))
                facade.start()
                self.facade_proc = facade
            except Exception as exc:
                self.init_error = f"ORB decision code could not start ({type(exc).__name__}: {exc}); ORB is off"
                log.exception("ORB facade process failed to start")
                if facade is not None:
                    facade.close(timeout=1.0)
                facade, mode = None, "off"
                self.facade_proc = None
        if self.ledger_error is not None:
            self.init_error = self.ledger_error
            self._alert("_ledger", f"ORB is off: {self.ledger_error}. ADT's other strategies run normally.")
            mode = "off"
        try:
            self.build(broker, facade, mode)
        except Exception as exc:
            # A corrupt/unsupported ORB state row must not stop ADT's other strategies: ORB runs off,
            # with nothing restored, and the card says so. Its Alpaca orders (if any) need a human.
            self.init_error = (f"ORB state could not be restored ({type(exc).__name__}: {str(exc)[:200]}); "
                               "ORB is off. Check ORB's positions and orders at Alpaca by hand.")
            log.exception("ORB state restore failed")
            # persist=False: the unreadable rows stay exactly as they are for a human to recover
            self.build(broker, facade, "off", restore=False, persist=False)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        log.info("ORB (ORBStraddle rules) started: mode=%s broker=%s", mode, "alpaca_paper" if broker else "none")

    def drain(self, timeout: float = DRAIN_TIMEOUT_S, extra_wait: float = DRAIN_EXTRA_WAIT_S) -> List[str]:
        """Graceful shutdown, BEFORE ADT's final checkpoint (Codex P1 #3). Worker thread only.
        1. no new entry POST and no new job; 2. running jobs (an exit mid-way, an orphan close) get up
        to `timeout` s to finish; 3. then every broker write is refused, so a job that could not finish
        can never POST after the drain; 4. queued scheduler state is flushed. Returns unfinished jobs."""
        ctl, sched = self.controller, self.scheduler
        if ctl is not None:
            ctl.close_entries("ADT is shutting down")
        futures: List[Any] = []
        if sched is not None:
            sched.stop_new_jobs()
            futures += sched.running_futures()
        _done, pending = _wait_futures(futures, timeout=timeout) if futures else (set(), set())
        self._writes_closed = True
        unresolved: List[dict] = []
        if ctl is not None and not ctl.close_writes("ADT is shutting down", wait_s=0.0):
            # a write already at Alpaca's door: keep the store and writer alive for it (bounded)
            if not ctl.wait_writes(extra_wait):
                unresolved = ctl.mark_unresolved_writes()
                log.critical("ORB shutdown: %d broker write(s) still unanswered after %.0f s more; their outcome "
                             "is unknown and is resolved by client id at the next startup: %s",
                             len(unresolved), extra_wait, unresolved)
        self.unresolved_at_shutdown = unresolved
        if self.facade_proc is not None:
            try:
                self.facade_proc.close(timeout=5.0)          # nothing may ask it anything any more
            except Exception:
                log.exception("ORB decision process close failed")
        try:
            self.flush_scheduler_state(timeout=5.0)
        except Exception as exc:
            log.error("ORB scheduler state flush at shutdown failed: %s", exc)
        try:
            self.flush_controller_state(timeout=5.0)
        except Exception as exc:
            log.error("ORB controller state flush at shutdown failed: %s", exc)
        unfinished = [n for n, j in (sched._jobs.items() if sched is not None else []) if j.future in pending]
        if pending:
            log.error("ORB shutdown drain: %d job(s) did not finish in %.0f s; their broker writes are refused",
                      len(pending), timeout)
        out = (unfinished or [f"{len(pending)} job(s)"]) if pending else []
        return out + [f"unresolved write {u.get('coid') or u.get('order_id')}" for u in unresolved]

    def shutdown(self, close_facade: bool = True) -> None:
        self._writes_closed = True
        if close_facade and self.facade_proc is not None:
            try:
                self.facade_proc.close(timeout=2.0)
            except Exception:
                log.exception("ORB decision process close failed")
            self.facade_proc = None
        if self.controller is not None:
            self.controller.close_writes("ORB was stopped")
        try:
            self.flush_controller_state(timeout=5.0)
        except Exception as exc:
            log.error("ORB controller state flush at shutdown failed: %s", exc)
        with self._ctl_cond:
            self._ctl_stopping = True
            self._ctl_cond.notify_all()
            writer = self._ctl_thread
        if writer is not None:
            writer.join(timeout=2.0)
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown()
            except Exception:
                log.exception("ORB scheduler shutdown failed")
        self.scheduler = None
        self.controller = None

    def reset(self) -> None:
        """Test/replay isolation (reset_runtime_state)."""
        self.shutdown()
        self.facade = None
        self.mode = "off"
        self.ledger = self._empty_ledger()
        self._mem_state.clear()
        with self.lock:
            self._orb_reserved.clear()
            self._adt_claims.clear()
        self.errors.clear()
        self.events.clear()
        self.init_error = None
        self.alerts.clear()
        self._mark_eval_pending = False
        self.ledger_error, self._bad_ledger = None, None
        self.unresolved_at_shutdown = []
        self.marks.clear()
        self._orphans.clear()
        self._last_mark_eval: Optional[float] = None

    def _is_session(self, d: date) -> Optional[bool]:
        return self.r.is_trading_day(d)

    # ------------------------------------------------------------------ durable ORB state
    def _persister(self, section: str) -> Callable[[dict], None]:
        def persist(state: dict) -> None:
            if not self._persist_enabled:
                return            # fallback build over an unreadable row: never overwrite that row
            if section == "scheduler" and self.r.state_store is not None and not self.r.simulation_mode:
                self._queue_scheduler_state(state)     # never on the event loop; flushed before orders
                return
            with self._persist_locks[section]:
                # Callers snapshot before calling; two threads can race, so the snapshot actually written is
                # taken again here, under the section lock: a stale snapshot can never land after a newer one.
                owner = self.controller if section == "controller" else self.scheduler
                if owner is not None:
                    state = owner.to_state()
                store = self.r.state_store
                if store is not None and not self.r.simulation_mode:
                    store.save_orb_state(section, state)       # durable before return; raises on failure
                else:
                    self._mem_state[section] = copy.deepcopy(state)
                if section == "controller":
                    self._ctl_error = None
        return persist

    def _queue_controller_state(self) -> None:
        with self._ctl_cond:
            self._ctl_pending = True
            if self._ctl_thread is None or not self._ctl_thread.is_alive():
                self._ctl_thread = threading.Thread(target=self._ctl_writer, name="OrbCtlStateWriter", daemon=True)
                self._ctl_thread.start()
            self._ctl_cond.notify_all()

    def _ctl_writer(self) -> None:
        while True:
            with self._ctl_cond:
                while not self._ctl_pending:
                    if self._ctl_stopping or not self._ctl_cond.wait(timeout=30.0):
                        self._ctl_thread = None
                        return
                self._ctl_pending = False
                self._ctl_busy = True
            try:
                if self.controller is not None:
                    self.controller._persist()     # re-snapshot under the section lock, durable on return
                self._ctl_error = None
            except Exception as exc:
                self._ctl_error = f"{type(exc).__name__}: {exc}"
                log.error("ORB controller command save failed: %s", exc)
                self._err({"kind": "controller_state_save", "err": self._ctl_error})
            finally:
                with self._ctl_cond:
                    self._ctl_busy = False
                    self._ctl_cond.notify_all()

    def flush_controller_state(self, timeout: float = 10.0) -> None:
        deadline = _time.monotonic() + timeout
        with self._ctl_cond:
            while self._ctl_pending or self._ctl_busy:
                left = deadline - _time.monotonic()
                if left <= 0:
                    raise RuntimeError("ORB controller command state was not saved in time")
                self._ctl_cond.wait(timeout=left)
        if self._ctl_error:
            raise RuntimeError(f"ORB controller command state could not be saved: {self._ctl_error}")

    # ---- scheduler state writer (one background thread, latest snapshot wins)
    def _queue_scheduler_state(self, state: dict) -> None:
        """The scheduler hands over its own snapshot (it persists only from its tick thread, so snapshots
        arrive in order). The writer never takes the scheduler's lock: an order job waiting on the
        barrier can never wait on a scheduler that is itself waiting (inline mode deadlocked before)."""
        with self._sched_cond:
            self._sched_pending = copy.deepcopy(state)
            if self._sched_thread is None or not self._sched_thread.is_alive():
                self._sched_thread = threading.Thread(target=self._sched_writer, name="OrbSchedStateWriter",
                                                      daemon=True)
                self._sched_thread.start()
            self._sched_cond.notify_all()

    def _sched_writer(self) -> None:
        while True:
            with self._sched_cond:
                while not self._sched_pending:
                    if not self._sched_cond.wait(timeout=30.0):
                        self._sched_thread = None
                        return
                snapshot, self._sched_pending = self._sched_pending, None
                self._sched_busy = True
            try:
                store = self.r.state_store
                if store is not None and self._persist_enabled:
                    with self._persist_locks["scheduler"]:
                        store.save_orb_state("scheduler", snapshot)   # latest snapshot wins
                self._sched_error = None
            except Exception as exc:
                self._sched_error = f"{type(exc).__name__}: {exc}"
                log.error("ORB scheduler state write failed: %s", exc)
            finally:
                with self._sched_cond:
                    self._sched_busy = False
                    self._sched_cond.notify_all()

    def flush_scheduler_state(self, timeout: float = 10.0) -> None:
        """Block (worker thread) until the queued scheduler state is durable. Raises if it is not."""
        deadline = _time.monotonic() + timeout
        with self._sched_cond:
            while self._sched_pending or self._sched_busy:
                left = deadline - _time.monotonic()
                if left <= 0:
                    raise RuntimeError("ORB scheduler state was not saved in time")
                self._sched_cond.wait(timeout=left)
        if self._sched_error:
            raise RuntimeError(f"ORB scheduler state could not be saved: {self._sched_error}")

    def _load(self, section: str) -> Optional[dict]:
        if section == "scheduler":
            try:
                self.flush_scheduler_state(timeout=5.0)
            except Exception:
                pass
        store = self.r.state_store
        if store is not None:
            return store.load_orb_state(section)
        return copy.deepcopy(self._mem_state.get(section))

    def ledger_state(self) -> Any:
        if self.ledger_error is not None:
            return copy.deepcopy(self._bad_ledger)       # keep the unreadable section as it was
        return copy.deepcopy(self.ledger)

    def load_ledger_state(self, state: Optional[Any]) -> None:
        """No section (a checkpoint from before ORB) = a fresh ledger. A section of another version is
        never read as empty (that would book ORB's fills twice): ORB fails closed (off, no booking, an
        alert) while ADT's other strategies start normally."""
        self.ledger = self._empty_ledger()
        self.ledger_error, self._bad_ledger = None, None
        if state is None:
            return
        if isinstance(state, dict) and state.get("version") == LEDGER_VERSION:
            self.ledger.update({k: copy.deepcopy(state.get(k, self.ledger[k]))
                                for k in ("booked", "recorded", "logged", "resolved")})
            # display-only section (absent in older checkpoints; older code ignores it)
            if isinstance(state.get("regime"), dict):
                self.ledger["regime"] = copy.deepcopy(state["regime"])
            return
        version = state.get("version") if isinstance(state, dict) else type(state).__name__
        self.ledger_error = (f"ORB's saved fill ledger has version {version!r}, not {LEDGER_VERSION}: ORB is off "
                             "and books nothing until a person checks ORB's positions at Alpaca")
        self._bad_ledger = copy.deepcopy(state)
        log.error(self.ledger_error)

    # ------------------------------------------------------------------ reservations (one lock)
    def adt_occupied(self, symbol: str) -> Optional[str]:
        """Why another ADT arm has this symbol, or None. Never counts ORB itself."""
        r, sym = self.r, symbol.upper()
        pos = r.account.positions.get(sym)
        if pos is not None and getattr(pos, "strategy_id", "") != ORB_ID:
            return f"ADT holds {sym} ({getattr(pos, 'strategy_id', '')})"
        if pos is not None and not (self.controller is not None and self.controller.owns(sym)):
            return f"ADT holds an ORB position on {sym} that the ORB controller does not know"
        for order in list(r.engine.working_orders.values()):
            if order.symbol.upper() == sym and order.execution_policy != ORB_POLICY:
                return f"ADT has a working order on {sym}"
        oid = self._adt_claims.get(sym)
        if oid is not None:
            order = r.engine.orders.get(oid)
            if order is not None and order.status.value in CLAIM_STATES:
                return f"an ADT entry on {sym} is in flight"
            self._adt_claims.pop(sym, None)
        if sym in r.bracket_manager.symbol_to_bracket:
            return f"ADT has a bracket on {sym}"
        if sym in r.swing_reserved_symbols or r.swing_staged_order_manager.is_staged_for_entry(sym):
            return f"{sym} is reserved or staged for the swing arm"
        if r.tri_controller.reserves(sym):
            return f"{sym} belongs to the Tesla/Coeur morning plan today"
        if r.or15_controller.reserves(sym):
            return f"{sym} belongs to the OR15 plan"
        ovn = getattr(r, "overnight", None)
        if ovn is not None:
            reason = ovn.occupied_reason(sym)     # S11: reserved from 15:45 or held overnight
            if reason:
                return reason
        day_one = getattr(r, "day_one", None)
        if day_one is not None:
            reason = day_one.occupied_reason(sym)
            if reason:
                return reason
        return None

    def is_occupied(self, symbol: str) -> bool:
        with self.lock:
            return self.adt_occupied(symbol) is not None

    def reserve(self, symbol: str) -> bool:
        sym = symbol.upper()
        with self.lock:
            if self.adt_occupied(sym) is not None:
                return False
            self._orb_reserved.add(sym)
            return True

    def release(self, symbol: str) -> None:
        with self.lock:
            self._orb_reserved.discard(symbol.upper())

    def owns(self, symbol: str) -> bool:
        """ORB holds, reserved, or has a live order on symbol. An 'orb' position in ADT's book that the
        controller does NOT know (state lost, old-ORB leftover) is an orphan: it is still ORB's, so ADT's
        generic paths never touch it (its bracket legs may be live at Alpaca), nothing ever auto-sells it,
        and a plain-language alert asks a human to check it. What the controller can prove from its own
        orders (lost position row, restart mid-trade) it rebuilds and exits itself, legs first."""
        sym = symbol.upper()
        with self.lock:
            if sym in self._orb_reserved:
                return True
        ctl = self.controller
        if ctl is not None and self._ctl_owns(sym):
            return True
        if self.is_orphan(sym):
            self._orphan_alarm(sym)
            return True
        return False

    def _ctl_owns(self, sym: str) -> bool:
        """controller.owns, but a broken ORB state never breaks ADT: ORB alone stops (alert, unready) and
        only ADT's own book (an 'orb' position) still marks the symbol as ORB's."""
        ctl = self.controller
        try:
            return bool(ctl.owns(sym))
        except Exception as exc:
            log.exception("ORB ownership check failed for %s", sym)
            self._alert("_state", f"ORB's saved state is damaged ({type(exc).__name__}); ORB opens nothing "
                                  "new until a person checks it. ADT's other strategies are unaffected.")
            ctl.ready = False
            return False

    def is_orphan(self, symbol: str) -> bool:
        sym = symbol.upper()
        pos = self.r.account.positions.get(sym)
        if pos is None or getattr(pos, "strategy_id", "") != ORB_ID:
            return False
        ctl = self.controller
        return not (ctl is not None and self._ctl_owns(sym))

    def orphan_targets(self) -> List[str]:
        """ORB shares in ADT's book the controller cannot explain, and live adt-orb orders it does not know."""
        out = {s for s in list(self.r.account.positions) if self.is_orphan(s)}
        ctl = self.controller
        rep = (ctl.startup_report or {}) if ctl is not None else {}
        for o in rep.get("unknown_orders") or []:
            sym = str(o.get("symbol") or "").upper()
            if sym and not ctl.owns(sym):
                out.add(sym)
        return sorted(out)

    def check_orphans(self) -> None:
        """Alert (never trade) on what ORB cannot prove is its own. Only after the controller's startup
        reconciliation has run, so a restart does not alarm on something it is about to rebuild."""
        ctl = self.controller
        if ctl is None or ctl.startup_report is None:
            return
        targets = self.orphan_targets()
        for sym in targets:
            pos = self.r.account.positions.get(sym)
            if pos is not None and getattr(pos, "strategy_id", "") == ORB_ID:
                self._alert(sym, f"{sym}: ADT's book holds {pos.shares} ORB shares that no ORB order at Alpaca "
                                 "explains. Nothing will sell them automatically and no other strategy may touch "
                                 "them. Close it at Alpaca by hand (cancel ORB's bracket orders on it first), then press "
                                 "the button below to clear it here.")
            else:
                self._alert(sym, f"{sym}: Alpaca has a live ORB order that ORB's saved state does not know. It was "
                                 "not touched, and ORB opens no new trades until it is resolved. Check it by hand.")
        for sym in [s for s in self.alerts if s not in targets and not s.startswith("_")]:
            self.alerts.pop(sym, None)
            self._drop_errors(sym)

    def _orphan_alarm(self, sym: str) -> None:
        if sym in self._orphans:
            return
        self._orphans.add(sym)
        msg = (f"{sym}: ADT's book has an ORB position ORB cannot explain. Nothing sells it automatically and "
               "no other strategy may touch it. Close it at Alpaca by hand (cancel ORB's bracket orders on it "
               "first), then press the button on the ORB card to clear it here.")
        log.error(msg)
        self._err({"kind": "orphan_orb_position", "symbol": sym, "note": msg})

    def claim_for_adt(self, symbol: str, order: Any, strategy_id: Optional[str]) -> Optional[str]:
        """ADT entry admission (pre-trade validator), atomically with ORB's reserve(): refuse a symbol ORB
        owns, else record the in-flight entry so ORB cannot take the symbol meanwhile."""
        sym = symbol.upper()
        with self.lock:
            try:
                orb_has_it = self.owns(sym)
            except Exception as exc:
                # a broken ORB state must never block ADT's other strategies: ORB alone stops
                log.exception("ORB ownership check failed for %s", sym)
                self._alert("_state", f"ORB's saved state is damaged ({type(exc).__name__}); ORB opens nothing "
                                      "new until a person checks it. ADT's other strategies are unaffected.")
                if self.controller is not None:
                    self.controller.ready = False
                orb_has_it = False
            if strategy_id != ORB_ID and orb_has_it:
                return (f"ORB_OWNED: {sym} is held or reserved by the Opening Range Breakout (ORBStraddle "
                        "rules); other strategies cannot trade it until ORB is done with it")
            if order is not None and getattr(order, "id", None):
                self._adt_claims[sym] = order.id
        return None

    def symbols(self) -> List[str]:
        out = {s for s, p in self.r.account.positions.items() if getattr(p, "strategy_id", "") == ORB_ID}
        with self.lock:
            out |= set(self._orb_reserved)
        ctl = self.controller
        if ctl is not None:
            out |= set(ctl.reserved_symbols()) | set(ctl._own_open_symbols())
        return sorted(out)

    # ------------------------------------------------------------------ hooks called by ORB's threads
    def account_halt(self) -> Optional[str]:
        r = self.r
        if r.risk_engine.status != r.BreakerStatus.ARMED:
            return "ADT's daily loss stop was hit"
        if r.account.status.value == "CIRCUIT_HALTED":
            return "ADT's circuit breaker halted the account"
        return None

    def entry_gate(self, symbol: str) -> Optional[str]:
        """ADT's _broker_gate for ORB entries: regular hours only, no broker position mismatch, and a
        healthy durable ledger. Exits never pass through here."""
        r = self.r
        strategy = r.strategy_map.get(ORB_ID)
        status = getattr(strategy, "status", None)
        if strategy is None or getattr(status, "value", status) != "ACTIVE":
            return f"ORB_STRATEGY_INACTIVE: ORB is {getattr(status, 'value', status) or 'unavailable'}"
        if self._ctl_error:
            return "PERSISTENCE_RECOVERY_HALT: ORB's controller command state could not be saved"
        if r.simulation_mode:
            return "replay mode never sends real orders"
        down = self.facade_down()
        if down:
            return down
        now_et = self.clock().astimezone(ET)
        today = now_et.date()
        if not r.is_trading_day(today) or not (time(9, 30) <= now_et.time() < r.session_close(today)):
            return "MARKET_CLOSED: real orders only go out during regular hours"
        if r.broker_state.get("mismatch"):
            return "BROKER_MISMATCH: bot and Alpaca positions differ; entries paused"
        if r.state_store is not None and not r.persistence_healthy:
            return "PERSISTENCE_RECOVERY_HALT: the durable ledger is unavailable"
        return None

    def _on_fill(self, symbol, side, qty, price, order_id, role) -> None:
        self._dirty.set()
        self._wake()

    def _on_event(self, row: dict) -> None:
        self.events.append(dict(row))
        if row.get("kind") in ("own_fill", "position_closed", "position_reopened", "position_rebuilt",
                               "execution_intent", "exit_requested", "exit_all_requested"):
            self._dirty.set()
        if row.get("kind") in ("alarm", "supervision_symbol_error"):
            self._err({k: row.get(k) for k in ("ts", "kind", "alarm", "symbol", "err", "reason", "note")})

    def _wake(self) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._loop_sync)
        except RuntimeError:
            pass  # loop closed (shutdown)

    def _loop_sync(self) -> None:
        try:
            if self.sync(datetime.now(timezone.utc)):
                self.r._checkpoint_runtime("ORB_FILL")
        except Exception as exc:
            log.exception("ORB ledger sync failed")
            self._err({"kind": "ledger_sync", "err": str(exc)[:200]})

    # ------------------------------------------------------------------ event-loop side
    def tick(self, now: datetime) -> None:
        """Called ~1/s from ADT's runtime clock loop. Never blocks: the scheduler only starts and
        collects worker jobs; the ledger sync is in-memory."""
        if self.scheduler is None or self.r.simulation_mode:
            return
        self._check_facade()
        try:
            self.check_orphans()
        except Exception as exc:
            log.exception("ORB orphan check failed")
            self._err({"kind": "orphan_check", "err": str(exc)[:200]})
        try:
            self.scheduler.tick(now)
        except Exception as exc:
            log.exception("ORB scheduler tick failed")
            self._err({"kind": "scheduler_tick", "err": str(exc)[:200]})
        mono = _time.monotonic()
        if self._dirty.is_set() or mono - self._last_sync >= SYNC_EVERY_S:
            self._last_sync = mono
            try:
                if self.sync(now):
                    self.r._checkpoint_runtime("ORB_SYNC")
            except Exception as exc:
                log.exception("ORB ledger sync failed")
                self._err({"kind": "ledger_sync", "err": str(exc)[:200]})
        try:
            self.mark_positions(now)
        except Exception as exc:
            log.exception("ORB price marking failed")
            self._err({"kind": "mark", "err": str(exc)[:200]})

    def _local_order(self, key: str, rec: dict) -> Any:
        r = self.r
        booked = self.ledger["booked"].get(self._bkey(key, rec)) or {}
        local = r.engine.orders.get(booked.get("local") or "")
        if local is not None:
            return local
        side = OrderSide.BUY if rec.get("side") == "buy" else OrderSide.SELL
        qty = max(int(rec.get("qty") or 0), int(rec.get("filled_qty") or 0), 1)
        role = rec.get("role")
        name = ("target" if rec.get("leg_kind") == "tp" else "stop") if role == "leg" else role
        local = r.engine.create_order(
            symbol=rec["symbol"], side=side, order_type=OrderType.MARKET, qty=qty, strategy_id=ORB_ID,
            client_order_id=str(rec.get("coid") or rec.get("id") or key),
            parent_order_id=f"orb:{rec.get('position') or key}", arm=r.TradingArm.INTRADAY)
        local.execution_policy = ORB_POLICY
        local.reject_reason = None
        local.estimated_price = _f(rec.get("avg_price"))
        r.engine._record_audit(local, local.status, "ORB_BROKER_ORDER",
                               f"ORB {name} order {rec.get('id') or rec.get('coid')} at Alpaca")
        return local

    @staticmethod
    def _bkey(key: str, rec: dict) -> str:
        """The booking key: Alpaca's order id (a fill only exists once the broker answered with one)."""
        return str(rec.get("id") or key)

    def sync(self, now: Optional[datetime] = None) -> bool:
        """Book ORB's new broker fills into ADT's account/ledger (idempotent, cumulative per ORB order),
        refresh the ORB positions' prices, record finished ORB trades. Event loop only."""
        ctl = self.controller
        self._dirty.clear()
        if ctl is None or self.ledger_error is not None:
            return False                        # an unreadable ledger never books (no double booking)
        r = self.r
        now = now or datetime.now(timezone.utc)
        st = ctl.to_state()
        changed = False
        booked = self.ledger["booked"]
        # entries before exits: a restart can find an entry's and its leg's fills in one pass, and the
        # account must see the opening fill first (an exit booked first would open a reverse position)
        for key, rec in sorted(st.get("orders", {}).items(),
                               key=lambda kv: (kv[1].get("role") != "entry", kv[1].get("fill_ts") or 0, kv[0])):
            filled = int(rec.get("filled_qty") or 0)
            avg = _f(rec.get("avg_price"))
            bkey = self._bkey(key, rec)
            b = booked.get(bkey) or {"qty": 0, "notional": 0.0, "local": None}
            delta = filled - int(b["qty"])
            if delta <= 0 or avg is None or avg <= 0:
                continue
            notional = filled * avg
            px = (notional - float(b["notional"])) / delta
            if not math.isfinite(px) or px <= 0:
                px = avg
            local = self._local_order(key, rec)
            if local.remaining_qty < delta:
                local.qty += delta - local.remaining_qty
                local.remaining_qty = delta
            ts = rec.get("fill_ts")
            at = datetime.fromtimestamp(float(ts), timezone.utc) if ts else now
            r.engine._apply_fill_to_ledger(local, delta, round(px, 6), 0.0, 0.0, at)
            booked[bkey] = {"qty": filled, "notional": notional, "local": local.id, "symbol": rec["symbol"],
                           "position": rec.get("position"), "role": rec.get("role"), "leg_kind": rec.get("leg_kind")}
            changed = True
            log.info("ORB fill booked: %s %s %d @ %.4f (%s)", rec.get("side"), rec["symbol"], delta, px, rec.get("role"))
        changed |= self._complete_trades(st, now)
        if changed and r.risk_engine.evaluate_account_state(
                equity=r.account.equity, cash=r.account.cash, realized_pnl=r.account.realized_pnl,
                unrealized_pnl=r.account.unrealized_pnl, timestamp=now) == r.BreakerStatus.HALTED_DAILY_LOSS:
            if r.account.status.value != "CIRCUIT_HALTED":
                r._trip_circuit_breaker(now)
        return changed

    def _complete_trades(self, st: dict, now: datetime) -> bool:
        r = self.r
        recorded = set(self.ledger["recorded"])
        own = st.get("own", {})
        changed = False
        for key, pos in st.get("positions", {}).items():
            if key in recorded or pos.get("status") in ACTIVE or pos.get("status") == "SHADOW":
                continue
            sym = pos["symbol"]
            if int((own.get(sym) or {}).get("qty") or 0) != 0:
                continue                         # shares still open under another position row
            recs = {k: v for k, v in st.get("orders", {}).items() if (v.get("position") or k) == key}
            if any(int(v.get("filled_qty") or 0) > int((self.ledger["booked"].get(self._bkey(k, v)) or {}).get("qty") or 0)
                   for k, v in recs.items()):
                continue                         # a fill is not booked yet
            locals_ = [r.engine.orders.get((self.ledger["booked"].get(self._bkey(k, v)) or {}).get("local") or "")
                       for k, v in recs.items()]
            entries = [o for k, o in zip(recs, locals_) if o is not None and recs[k].get("role") == "entry"]
            exits = [o for k, o in zip(recs, locals_) if o is not None and recs[k].get("role") != "entry"]
            entry_fills = [f for o in entries for f in o.fills]
            exit_fills = [f for o in exits for f in o.fills]
            self.ledger["recorded"].append(key)
            self.ledger["recorded"] = self.ledger["recorded"][-500:]
            changed = True
            if not entry_fills:
                continue                         # never traded (rejected, skipped, never filled)
            qty = sum(f.qty for f in entry_fills)
            exit_qty = sum(f.qty for f in exit_fills)
            pnl = round(sum(f.realized_pnl for f in exit_fills), 2)
            avg_in = sum(f.qty * f.price for f in entry_fills) / qty
            avg_out = (sum(f.qty * f.price for f in exit_fills) / exit_qty) if exit_qty else None
            opened = min(f.timestamp for f in entry_fills)
            closed = max(f.timestamp for f in exit_fills) if exit_fills else now
            trade = {
                "trade_id": f"orb_{key}", "session_date": str(pos.get("day") or opened.astimezone(ET).date()),
                "symbol": sym, "side": "LONG" if pos.get("direction") == "long" else "SHORT", "status": "CLOSED",
                "strategy_id": ORB_ID, "opened_at": opened.isoformat(), "closed_at": closed.isoformat(),
                "quantity": qty, "avg_entry_price": round(avg_in, 4),
                "avg_exit_price": round(avg_out, 4) if avg_out else None, "realized_pnl": pnl, "fees": 0.0,
                "broker_fees": None, "exit_reason": pos.get("closed_reason") or pos.get("exit_reason"),
                "aggregate_only": False, "execution_mode": "alpaca_paper", "rules": rules_label()["text"],
                "orb": {k: pos.get(k) for k in ("coid", "tier", "wave", "entry_ref", "card_entry", "stop", "initial_stop",
                                                 "target", "rd", "risk_usd", "planned_shares", "peak", "be_locked",
                                                 "exit_reason", "closed_reason", "carried")},
                "r_multiple": round(pnl / float(pos["risk_usd"]), 3) if _f(pos.get("risk_usd")) else None,
                "fill_legs": [{"fill_id": f.fill_id, "order_id": f.order_id, "side": f.side.value, "qty": f.qty,
                               "price": f.price, "fee": f.fee, "realized_pnl": f.realized_pnl,
                               "timestamp": f.timestamp.isoformat()} for f in entry_fills + exit_fills],
            }
            trade = r._sanitize_for_json(trade)
            r.pending_trade_records[trade["trade_id"]] = trade
            strat = r.strategy_map.get(ORB_ID)
            if strat is not None:
                strat.record_trade(pnl)
            if r.settings.RESEARCH_ENABLED:
                r.research_safe(r.research_recorder.record, "trades", trade["trade_id"], trade,
                                recorder=r.research_recorder)
            log.info("ORB trade closed: %s %s pnl %.2f (%s)", sym, trade["side"], pnl, trade["exit_reason"])
        return changed

    def refresh_symbols(self, symbols: List[str]) -> int:
        """Worker thread: re-read now (no 10 s throttle) every non-terminal own order on these symbols, so
        a fill Alpaca already made is in ORB's book before ADT compares positions. Returns orders read."""
        ctl = self.controller
        if ctl is None or ctl.broker is None:
            return 0
        n = 0
        for sym in symbols:
            lk = ctl._sym_lock(sym)
            if not lk.acquire(timeout=5.0):
                continue
            try:
                for key, rec in ctl._live(sym.upper()):
                    if ctl._refresh_one(key, rec, "exit"):
                        n += 1
            finally:
                lk.release()
        self._dirty.set()
        return n

    # ------------------------------------------------------------------ decision process health
    FACADE_RESTART_BACKOFF_S = 30.0

    def _check_facade(self) -> None:
        """Restart a dead decision process on a worker thread (never on the loop), with a back-off."""
        proc = self.facade_proc
        if proc is None or self._writes_closed or proc.healthy():
            return
        if self._facade_restart is not None and self._facade_restart.is_alive():
            return
        mono = _time.monotonic()
        if mono < self._facade_next_restart:
            return
        self._facade_next_restart = mono + self.FACADE_RESTART_BACKOFF_S
        self._err({"kind": "decision_process_down", "err": proc.last_error or "not running",
                   "note": "ORB opens nothing until its decision process is back; exits keep working"})

        def restart():
            try:
                proc.restart()
                log.warning("ORB decision process restarted (restart #%d)", proc.restarts)
            except Exception as exc:
                log.error("ORB decision process restart failed: %s", exc)
        self._facade_restart = threading.Thread(target=restart, name="OrbFacadeRestart", daemon=True)
        self._facade_restart.start()

    def facade_down(self) -> Optional[str]:
        proc = self.facade_proc
        if proc is not None and not proc.healthy():
            return "ORB's decision process is restarting"
        return None

    # ------------------------------------------------------------------ price marks (Codex P1 #2)
    def note_price(self, symbol: str, price: float, ts: datetime, source: str = "feed") -> None:
        """A timestamped price for a symbol ORB holds (ADT's SIP trades/quotes). Event loop."""
        sym = symbol.upper()
        pos = self.r.account.positions.get(sym)
        if pos is None or getattr(pos, "strategy_id", "") != ORB_ID or not price or price <= 0:
            return
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        old = self.marks.get(sym)
        if old is None or ts >= old[1]:
            self.marks[sym] = (float(price), ts, source)

    def mark_positions(self, now: datetime, force_eval: bool = False) -> bool:
        """Mark every ORB position in ADT's book to its NEWEST timestamped price (feed trade/quote or the
        ORB supervisor's Alpaca read) and, when a mark changed, evaluate ADT's daily loss stop (at most every
        MARK_BREAKER_EVERY_S). Returns True when the breaker was evaluated."""
        r, ctl = self.r, self.controller
        sup: Dict[str, tuple] = {}
        if ctl is not None:
            with ctl._lock:
                rows = [dict(p) for p in ctl.state["positions"].values() if p.get("status") in ACTIVE]
            for p in rows:
                px, at = _f(p.get("last_px")), p.get("last_px_at")
                if px and at:
                    try:
                        ts = datetime.fromisoformat(at)
                    except ValueError:
                        continue
                    if p["symbol"] not in sup or ts > sup[p["symbol"]][1]:
                        sup[p["symbol"]] = (px, ts, "orb_supervisor")
        changed = False
        for sym, pos in list(r.account.positions.items()):
            if getattr(pos, "strategy_id", "") != ORB_ID:
                continue
            cands = [c for c in (self.marks.get(sym), sup.get(sym)) if c]
            if not cands:
                continue
            px, ts, src = max(cands, key=lambda c: c[1])
            self.marks[sym] = (px, ts, src)
            if abs(pos.market_price - px) > 1e-9:
                r.account.update_market_price(sym, px)
                changed = True
        for sym in [s for s in self.marks if s not in r.account.positions]:
            self.marks.pop(sym, None)
        mono = _time.monotonic()
        if changed or force_eval:
            self._mark_eval_pending = True       # a throttled check is owed, even if no new price comes
        if not self._mark_eval_pending or (
                self._last_mark_eval is not None and mono - self._last_mark_eval < MARK_BREAKER_EVERY_S):
            return False
        self._last_mark_eval = mono
        self._mark_eval_pending = False
        status = r.risk_engine.evaluate_account_state(
            equity=r.account.equity, cash=r.account.cash, realized_pnl=r.account.realized_pnl,
            unrealized_pnl=r.account.unrealized_pnl, timestamp=now)
        if status == r.BreakerStatus.HALTED_DAILY_LOSS and r.account.status.value != "CIRCUIT_HALTED":
            r._trip_circuit_breaker(now)
            r._checkpoint_runtime("ORB_MARK_BREAKER")
        return True

    # ------------------------------------------------------------------ operator: resolve an orphan
    def resolve_refusal(self, symbol: str) -> Optional[str]:
        """Local precheck (event loop) for POST /api/orb/resolve-orphan."""
        sym = symbol.upper()
        if self.r.simulation_mode:
            return "Not available in replay mode."
        if not self.is_orphan(sym):
            return f"{sym} is not an ORB position that ORB cannot explain; nothing to resolve."
        if self.controller is None or self.controller.broker is None:
            return "ORB has no broker connection, so Alpaca cannot be checked."
        return None

    def alpaca_clear_for(self, symbol: str) -> tuple:
        """Worker thread: READ Alpaca. (True, detail) only if Alpaca holds no shares of the symbol beyond
        what ADT's other books hold (none: ADT keeps one position per symbol and this one is ORB's) and
        no ORB order (adt-orb client id, or a leg of one) is still live there."""
        sym = symbol.upper()
        ctl = self.controller
        broker = ctl.broker
        prefix = str(ctl.cfg.get("coid_prefix") or "adt-orb") + "-"
        # the flat reads prove nothing unless they come from ORB's pinned account (same check as writes)
        why = ctl.account_refusal(broker.get_account_checked())
        if why:
            return False, f"The Alpaca account could not be verified, so nothing was changed: {why}"
        qty = int(broker.position_qty(sym))
        if qty != 0:
            return False, (f"Alpaca still holds {qty} {sym} shares. Close them at Alpaca first (after cancelling "
                           "ORB's bracket orders), then try again.")
        after = (self.clock() - timedelta(days=7)).isoformat()
        final = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"}
        for row in broker.list_orders("all", sym, after, 500, True) or []:
            if not isinstance(row, dict) or not str(row.get("client_order_id") or "").startswith(prefix):
                continue
            for o in [row] + [leg for leg in (row.get("legs") or []) if isinstance(leg, dict)]:
                if o.get("status") not in final:
                    return False, (f"ORB order {o.get('client_order_id') or o.get('id')} on {sym} is still live at "
                                   "Alpaca. Cancel it there first, then try again.")
        return True, f"Alpaca holds no {sym} shares and no live ORB orders"

    def apply_resolution(self, symbol: str, now: datetime) -> Dict[str, Any]:
        """Event loop, after alpaca_clear_for said yes: close ADT's stale ORB position at its last known
        price (so equity does not jump; Alpaca's real close price shows up as equity drift), write the
        audit note into ORB's ledger and the decisions log, checkpoint, clear the alert."""
        r, sym = self.r, symbol.upper()
        pos = r.account.positions.get(sym)
        if pos is None or getattr(pos, "strategy_id", "") != ORB_ID:
            return {"resolved": False, "reason": f"{sym} has no ORB position in ADT's book any more"}
        qty, side = pos.shares, pos.side.value
        px = float(pos.market_price or pos.avg_entry_price)
        # undo data: the change is kept only if the checkpoint that records it is durable
        acct_before = copy.deepcopy({k: v for k, v in vars(r.account).items()})
        alert_before = self.alerts.get(sym)
        orders_before = set(r.engine.orders)
        n_decisions = len(r.decision_log.records)
        rec = {"symbol": sym, "side": "sell" if side == "LONG" else "buy", "qty": qty, "filled_qty": qty,
               "coid": f"operator-resolve-{sym}-{now.isoformat()}", "id": None, "role": "operator_resolve",
               "position": f"operator:{sym}"}
        local = self._local_order(f"operator:{sym}:{now.isoformat()}", rec)
        r.engine._apply_fill_to_ledger(local, int(qty), round(px, 6), 0.0, 0.0, now)
        note = {"symbol": sym, "qty": qty, "side": side, "price_booked": px, "at": now.isoformat(),
                "reason": "operator resolved orphan",
                "detail": "Alpaca showed no shares and no live ORB orders; ADT's stale ORB position was closed "
                          "at its last known price (Alpaca's real close price shows as equity drift)",
                "local_order_id": local.id}
        self.ledger.setdefault("resolved", []).append(note)
        r.decision_log.record(ORB_ID, sym, "BUY" if side == "SHORT" else "SELL", px, "ORB_ORPHAN_RESOLVED",
                              f"operator resolved orphan: {qty} shares removed from ADT's book", now)
        self.alerts.pop(sym, None)
        self._orphans.discard(sym)
        errors_before = list(self.errors)
        self._drop_errors(sym)
        if not r._checkpoint_runtime("ORB_ORPHAN_RESOLVED"):
            self.errors.clear()
            self.errors.extend(errors_before)
            # not durable: put everything back exactly as it was and say so
            vars(r.account).clear()
            vars(r.account).update(acct_before)
            for oid in set(r.engine.orders) - orders_before:
                r.engine.orders.pop(oid, None)
                r.engine.working_orders.pop(oid, None)
            del r.decision_log.records[n_decisions:]
            per = r.decision_log.counts.get(ORB_ID, {})
            if per.get("ORB_ORPHAN_RESOLVED"):
                per["ORB_ORPHAN_RESOLVED"] -= 1
            self.ledger["resolved"].remove(note)
            if alert_before is not None:
                self.alerts[sym] = alert_before
            log.error("ORB orphan %s resolution NOT saved (checkpoint failed); nothing changed", sym)
            return {"resolved": False, "reason": "The change could not be saved, so nothing was changed. "
                                                 "Try again once saving works."}
        log.warning("ORB orphan %s resolved by the operator: %s", sym, note)
        return {"resolved": True, **note}

    def _err(self, row: dict) -> None:
        """Errors are scoped: stamped with the session day (only today's are shown) and dropped when their
        condition resolves (an orphan resolved, a symbol no longer in trouble)."""
        row = dict(row)
        row.setdefault("day", self.clock().astimezone(ET).date().isoformat())
        self.errors.append(row)

    def _drop_errors(self, sym: str) -> None:
        keep = [e for e in self.errors if e.get("symbol") != sym]
        self.errors.clear()
        self.errors.extend(keep)

    def current_errors(self) -> List[dict]:
        today = self.clock().astimezone(ET).date().isoformat()
        return [e for e in self.errors if e.get("day") == today][-10:]

    def no_trade_reason(self) -> Optional[str]:
        """Why ORB cannot open any trade for the rest of today (None if it still can)."""
        ctl, sched = self.controller, self.scheduler
        if ctl is None or sched is None or ctl.mode == "off":
            return None
        st = sched.state.get("steps") or {}
        final = st.get("final") or {}
        if final.get("state") == "failed":
            detail = str(final.get("detail") or "")
            if "decision process" in detail:
                # accepted (Codex on dea7192): like ORBStraddle when its scan fails, ORB sits out today
                return "ORB's scanner restarted during the 9:38 scan"
            why = ("too few stocks answered" if "coverage" in detail or "covered only" in detail
                   else "relay error" if detail else "no answer")
            return f"the 9:38 scan failed ({why})"
        if final.get("state") == "skipped":
            return "the app was not running in time for the 9:38 scan"
        if ctl.halted():
            return f"ORB's own daily loss halt ({ctl.halted()})"
        if ctl.entries_blocked():
            return f"ORB positions were closed for the day ({ctl.entries_blocked()})"
        ah = self.account_halt()
        if ah:
            return ah
        snap = ctl.session_sizing()
        eq = float(snap["equity"]) if snap else None
        if eq and ctl.slots_available() > 0 and ctl.day_risk_used() >= ctl.max_day_risk(eq):
            return "today's ORB risk budget is used up"
        return None

    def _alert(self, sym: str, text: str) -> None:
        if self.alerts.get(sym) != text:
            log.error("ORB ALERT %s", text)
            self._err({"kind": "alarm", "alarm": "orb_alert", "symbol": sym, "note": text})
        self.alerts[sym] = text

    # ------------------------------------------------------------------ decisions log / research
    def _remember_regime(self, picks: List[dict], regime: Any, wave: Any, at: Any, day: str) -> None:
        """Display only: the 9:38 market check each picked symbol was traded under, day-scoped, kept in
        this ledger section (never the scheduler state, whose version check would refuse a change)."""
        try:
            reg = regime if isinstance(regime, dict) else {}
            saved = self.ledger.get("regime")
            if not isinstance(saved, dict) or saved.get("day") != day:
                saved = {"day": day, "symbols": {}}
            for p in picks:
                sym = str(p.get("symbol") or "").upper()
                if sym:
                    saved["symbols"][sym] = {"classification": reg.get("classification"),
                                             "short_frac": _f(reg.get("short_frac")), "wave": wave, "at": at}
            self.ledger["regime"] = saved
        except Exception:
            warn_rate_limited(log, "display:orb_regime_note", "ORB regime note failed")

    def _on_decision(self, row: dict) -> None:
        """Scheduler hook (tick thread = event loop in production): decisions log + research rows."""
        r = self.r
        key = f"{row.get('kind')}|{row.get('wave')}|{row.get('at')}"
        if key in self.ledger["logged"]:
            return
        self.ledger["logged"].append(key)
        self.ledger["logged"] = self.ledger["logged"][-300:]
        at = datetime.fromisoformat(row["at"]) if row.get("at") else datetime.now(timezone.utc)
        day = at.astimezone(ET).date().isoformat()
        mode = self.controller.mode if self.controller is not None else self.mode
        if row.get("kind") == "verdict":
            picks = row.get("pick_details") or []
            if picks:
                self._remember_regime(picks, row.get("regime"), row.get("wave"), row.get("at"), day)
            if not picks:
                outcome = "ORB_SAT_OUT" if row.get("verdict") in ("sit_out", "pass", "blocked", "skipped") else "ORB_NO_DECISION"
                r.decision_log.record(ORB_ID, "BOARD", "-", 0.0, outcome,
                                      f"{row.get('wave')} decision: {row.get('reason') or row.get('verdict')}", at)
            elif not row.get("executing"):
                for p in picks:
                    r.decision_log.record(ORB_ID, str(p.get("symbol")), "BUY" if p.get("direction") == "long" else "SELL",
                                          _f(p.get("entry")) or 0.0, "ORB_NOT_EXECUTED", str(row.get("reason")), at)
            if r.settings.RESEARCH_ENABLED:
                audit = [{k: a.get(k) for k in ("symbol", "direction", "tier", "verdict", "reason", "chosen", "ev_dollars",
                                                 "win_prob", "sector", "stop_pct", "spread_bps", "rvol", "atr_pct")
                          if k in a} for a in (row.get("audit") or []) if isinstance(a, dict)][:80]
                rec = {"row_id": f"orb:{day}:{row.get('wave')}:{row.get('at')}", "session_date": day,
                       "strategy_id": ORB_ID, "kind": "orb_decision", "mode": mode, "wave": row.get("wave"),
                       "verdict": row.get("verdict"), "reason": row.get("reason"), "at": row.get("at"),
                       "picks": picks, "refused_picks": row.get("refused_picks") or [],
                       "board_id": row.get("board_id"), "board_symbols": row.get("board_symbols") or [],
                       "regime": row.get("regime") or {}, "audit": audit}
                r.research_safe(r.research_recorder.record, "setups", rec["row_id"], rec, recorder=r.research_recorder)
            return
        if row.get("kind") == "execution":
            for p in row.get("placed") or []:
                side = "BUY" if p.get("direction") == "long" else "SELL"
                outcome = {"accepted": "SUBMITTED", "shadow": "ORB_SHADOW", "unknown": "ORB_UNKNOWN"}.get(
                    p.get("outcome"), "ORB_REFUSED")
                if outcome == "ORB_SHADOW":
                    detail = (f"would {'buy' if side == 'BUY' else 'sell short'} {p.get('shares')} at ~{p.get('entry_ref')}, "
                              f"stop {p.get('stop')}, target {p.get('target')} (risk ${p.get('risk_usd')})")
                elif outcome == "SUBMITTED":
                    detail = (f"{side} {p.get('shares')} {p.get('symbol')} bracket: stop {p.get('stop')}, "
                              f"target {p.get('target')} (risk ${p.get('risk_usd')})")
                else:
                    detail = str(p.get("error") or p.get("outcome"))
                r.decision_log.record(ORB_ID, str(p.get("symbol")), side, _f(p.get("entry_ref")) or 0.0, outcome, detail, at)
            for sym, why in row.get("refused") or []:
                r.decision_log.record(ORB_ID, str(sym), "-", 0.0, "ORB_REFUSED", str(why), at)
            if not (row.get("placed") or row.get("refused")) and not row.get("ok"):
                r.decision_log.record(ORB_ID, "BOARD", "-", 0.0, "ORB_REFUSED", str(row.get("reason")), at)
            if r.settings.RESEARCH_ENABLED:
                rec = {"row_id": f"orb-exec:{day}:{row.get('wave')}:{row.get('at')}", "session_date": day,
                       "strategy_id": ORB_ID, "kind": "orb_execution", "mode": row.get("mode"), "wave": row.get("wave"),
                       "at": row.get("at"), "ok": row.get("ok"), "reason": row.get("reason"),
                       "placed": row.get("placed") or [], "refused": row.get("refused") or []}
                r.research_safe(r.research_recorder.record, "signals", rec["row_id"], rec, recorder=r.research_recorder)

    # ------------------------------------------------------------------ exit dispatch (ADT paths)
    def _persist_exit_request(self) -> None:
        """Only exit-request saves may queue from the loop. Every controller pre-POST save remains
        synchronous and durable, regardless of the caller's thread."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            on_loop = False
        else:
            on_loop = True
        if on_loop and self.r.state_store is not None and not self.r.simulation_mode:
            self._queue_controller_state()
        elif self.controller is not None:
            self.controller._persist_quiet()

    def request_exit(self, symbol: str, reason: str) -> bool:
        """An ADT path (manual flatten, news exit, ...) wants ORB's position in symbol closed. The
        controller cancels ORB's parent + legs first, then closes exactly ORB's quantity."""
        ctl = self.controller
        sym = symbol.upper()
        if ctl is not None and ctl.owns(sym):
            ok = ctl.request_exit(sym, reason, persist=False)
            if ok:
                self._persist_exit_request()
            if ok and self.scheduler is not None:
                self.scheduler.kick_supervisor()
            return ok
        if sym in self.orphan_targets():
            self.check_orphans()          # alert only: an unprovable position is never auto-sold
        return False

    def request_all_exits(self, reason: str, block_entries: bool = True) -> List[str]:
        self.check_orphans()
        ctl = self.controller
        if ctl is None:
            return []
        syms = set(ctl.reserved_symbols()) | set(ctl._own_open_symbols())
        with ctl._lock:
            pending = set(ctl.state["exit_requests"])
        # idempotent: the breaker path runs on every quote while halted; persist only on a change
        if not (syms - pending) and not (block_entries and not ctl.entries_blocked()):
            return sorted(syms)
        out = ctl.request_all_exits(reason, block_entries=block_entries, wait_for_entry=False, persist=False)
        self._persist_exit_request()
        if out and self.scheduler is not None:
            self.scheduler.kick_supervisor()
        return out

    # ------------------------------------------------------------------ risk
    def open_risk(self) -> float:
        """ORB's open + pending risk: remaining shares x distance from the current price to the broker
        stop (planned shares for an entry not filled yet). No known stop = 7.5% of the notional."""
        ctl = self.controller
        if ctl is None:
            return 0.0
        total = 0.0
        live_entries = {r["symbol"] for _k, r in ctl._live() if r.get("role") == "entry"}
        for h in ctl.holdings():
            qty = abs(int(h.get("qty") or 0))
            if not qty or h["symbol"] in live_entries:
                qty = max(qty, int(h.get("planned_shares") or 0))   # an entry still working may fill in full
            px = _f(h.get("last_price")) or _f(h.get("avg_price")) or _f(h.get("entry_ref")) or 0.0
            stop = _f(h.get("stop"))
            if qty <= 0 or px <= 0:
                continue
            if stop is None:
                total += qty * px * 0.075
            else:
                total += qty * max(0.0, (px - stop) if h.get("direction") == "long" else (stop - px))
        return round(total, 2)

    # ------------------------------------------------------------------ views
    def position_details(self, symbol: str) -> Dict[str, Any]:
        """Dashboard position fields for an ORB trade: its stop/target are the bracket legs at Alpaca,
        so the UI shows them fixed (no manual stop moves)."""
        ctl = self.controller
        h = next((x for x in (ctl.holdings() if ctl else []) if x["symbol"] == symbol.upper()), None)
        out: Dict[str, Any] = {"strategy_id": ORB_ID, "fixed_protection": True, "take_profit_2": None,
                               "exit_due": None, "orb_context": self._orb_context(symbol, h)}
        if h is not None:
            flatten = ctl.cfg["flatten"] if ctl is not None else "11:00"
            from backend.app.core.orb_execution import parse_hms
            out.update(stop_loss=h.get("stop"), take_profit_1=h.get("target"), r_multiple=h.get("r"),
                       exit_due=datetime.combine(self.clock().astimezone(ET).date(), parse_hms(flatten), ET).isoformat())
        return out

    def _orb_context(self, symbol: str, h: Optional[dict]) -> Dict[str, Any]:
        """Display only, every key always present (None when unknown): the 9:38 check this trade passed."""
        ctx: Dict[str, Any] = {"classification": None, "short_frac": None, "wave": None, "decided_at": None,
                               "short_bounds": None, "flow_rules_on": None, "breakeven_r": None,
                               "risk_usd": None, "flatten_at": None}
        try:
            from backend.app.strategies.orbs import config as orbs_config
            from backend.app.strategies.orbs.adaptive import ADAPTIVE_CONFIG
            saved = self.ledger.get("regime") or {}
            today = self.clock().astimezone(ET).date().isoformat()
            if saved.get("day") == today:
                note = (saved.get("symbols") or {}).get(symbol.upper()) or {}
                ctx.update(classification=note.get("classification"), short_frac=note.get("short_frac"),
                           wave=note.get("wave"), decided_at=note.get("at"))
            ctx["short_bounds"] = {"min": ADAPTIVE_CONFIG["MIN_BOARD_SHORT_FRAC"],
                                   "max": ADAPTIVE_CONFIG["MAX_BOARD_SHORT_FRAC"]}
            eff = orbs_config.effective()
            ctx["flow_rules_on"] = [name for key, name in (
                ("CANDLE_RULE", "candle"), ("DELTA_RULE", "delta"), ("VELOCITY_RULE", "velocity"),
                ("MACRO_RULE", "macro"), ("ABSORPTION_EXIT", "absorption")) if eff.get(key)]
            ctl = self.controller
            if ctl is not None:
                ctx["breakeven_r"] = ctl.cfg.get("breakeven_r")
                ctx["flatten_at"] = str(ctl.cfg.get("flatten"))
            if h is not None:
                ctx["risk_usd"] = _f(h.get("risk_usd"))
        except Exception:
            warn_rate_limited(log, "display:orb_context", "ORB context failed for %s", symbol)   # runs every frame
        return ctx

    @staticmethod
    def _trade_sentence(h: dict) -> str:
        long = h.get("direction") == "long"
        qty = abs(int(h.get("qty") or 0)) or int(h.get("planned_shares") or 0)
        what = f"{'Bought' if long else 'Shorted'} {h['symbol']} ({'long' if long else 'short'}), {qty} shares"
        r = h.get("r")
        now = f", now {'+' if (r or 0) >= 0 else ''}{r:.2f}x its risk" if isinstance(r, (int, float)) else ""
        stop, tgt = h.get("stop"), h.get("target")
        prot = (f"; stop {stop:.2f}" if isinstance(stop, (int, float)) else "") + \
               (f", target {tgt:.2f}" if isinstance(tgt, (int, float)) else "") + (" (held at Alpaca)" if stop else "")
        tail = "; closing now." if h.get("exit_requested") else "; closes by 11:00 AM."
        return what + now + prot + tail

    def status(self) -> Dict[str, Any]:
        ctl, sched = self.controller, self.scheduler
        rules = rules_label()
        out: Dict[str, Any] = {"mode": ctl.mode if ctl else self.mode, "configured_mode": self.configured_mode,
                               "rules": rules["text"], "rule_flags": rules["flags"],
                               "rules_version": rules["rules_version"],
                               "exclude_symbols": list(self.r.settings.ORB_EXCLUDE_SYMBOLS),
                               "expected_account": self.r.settings.ORB_EXPECTED_ACCOUNT,
                               "init_error": self.init_error, "errors": self.current_errors(),
                               "alerts": list(self.alerts.values()),
                               "decision_process": None if self.facade_proc is None else {
                                   "running": self.facade_proc.healthy(), "pid": self.facade_proc.pid(),
                                   "restarts": self.facade_proc.restarts, "last_error": self.facade_proc.last_error},
                               "marks": {k: {"price": v[0], "at": v[1].isoformat(), "source": v[2]}
                                         for k, v in self.marks.items()}}
        if ctl is None or sched is None:
            out.update(ready=False, step="ORB is not running.", last_verdict=None, picks=[], holdings=[])
            return out
        s = sched.status()
        c = ctl.status()
        holdings = c.get("holdings") or []
        lv = s.get("last_verdict") or {}
        if not holdings and lv.get("verdict") == "refused" and "not the last successful" in str(lv.get("reason")):
            s = dict(s, step="ORB's scanner restarted after the scan, so that board was not decided (no trade from "
                             "it). " + ("Watching for new breakouts." if (sched.state.get("final_ok")) else ""))
        if holdings:
            # while a trade is open the card talks about the trade, not the latest (sat-out) verdict
            s = dict(s, step=" ".join(self._trade_sentence(h) for h in holdings))
        out.update(ready=c.get("ready"), step=s.get("step"), next_step=s.get("next_step"), steps=s.get("steps"),
                   last_verdict=s.get("last_verdict"), picks=s.get("picks"), secondary=s.get("secondary"),
                   running=s.get("running"), session=s.get("session"), day=s.get("day"),
                   holdings=c.get("holdings"), halted=c.get("halted"), entries_blocked=c.get("entries_blocked"),
                   day_risk=c.get("day_risk"), slots=c.get("slots"), sizing=c.get("sizing"),
                   realized_today=c.get("realized_today"), startup=c.get("startup"), budget=c.get("budget"),
                   open_risk=self.open_risk(), last_execute=c.get("last_execute"))
        return out

    def card(self, now: datetime, strategy: Any, blockers: List[str]) -> Dict[str, Any]:
        from backend.app.core.trading_windows import orb_window
        card = strategy.to_dict()
        st = self.status()
        holdings = st.get("holdings") or []
        unreal = 0.0
        trades = []
        for h in holdings:
            pos = self.r.account.positions.get(h["symbol"])
            if pos is not None and getattr(pos, "strategy_id", "") == ORB_ID:
                unreal += pos.unrealized_pnl
            trades.append({k: h.get(k) for k in ("symbol", "direction", "qty", "avg_price", "stop", "target", "r",
                                                  "last_price", "breakeven_locked", "exit_requested", "status")})
        ctl = self.controller
        extra = list(blockers)
        if ctl is not None:
            if not ctl.ready:
                extra.append("Checking ORB's orders at the broker after a restart.")
            if ctl.halted():
                extra.append(f"ORB's own daily loss halt: {ctl.halted()}.")
            if ctl.entries_blocked():
                extra.append(f"No new ORB trades today ({ctl.entries_blocked()}).")
        mode = st.get("mode") or "off"
        no_trade = self.no_trade_reason()
        card["window"] = orb_window(now, mode=mode, step_text=str(st.get("step") or ""), holding=bool(holdings),
                                    blockers=extra, operator_status=card.get("status", "ACTIVE"),
                                    no_trade_reason=no_trade)
        card["orb"] = {
            "mode": mode,
            "mode_text": {"shadow": "Shadow: watching only, no orders", "live": "Live: paper account orders",
                          "off": "Off: no scans, no new trades"}.get(mode, mode),
            "step": st.get("step"), "next_step": st.get("next_step"), "ready": st.get("ready"),
            "last_verdict": st.get("last_verdict"), "picks": st.get("picks") or [],
            "open_trades": trades, "realized_pnl": round(float(strategy.daily_pnl or 0.0), 2),
            "unrealized_pnl": round(unreal, 2), "open_risk": st.get("open_risk"),
            "total_pnl": round(float(strategy.daily_pnl or 0.0) + unreal, 2), "no_trade_reason": no_trade,
            "hours": "Decides 9:38 AM, may add trades until 10:15 AM, closes by 11:00 AM",
            "errors": st.get("errors") or [], "init_error": st.get("init_error"),
            "alerts": st.get("alerts") or [],
            "orphans": [{"symbol": k, "text": v} for k, v in self.alerts.items()
                        if not k.startswith("_") and self.is_orphan(k)],
        }
        return card
