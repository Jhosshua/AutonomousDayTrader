"""ORB session scheduler: ORBStraddle's app.py morning timeline as an idempotent state machine.

Source: ORBStraddle @71b001f app.py _morning_scanner (~123-392) and auditor.run_autopilot
(~390-560). Timeline (ET), each step persisted so a restart resumes at the right one:

  session day only (injected is_session(date); unverifiable = no session)
  09:15-09:29  freeze session sizing (retried every 60 s) ; >= 09:15 facade.prep
  09:36:10     preview scan to 09:36 (display / warm-up only)
  09:38:30     final primary scan to 09:38; coverage >= 90% else ONE whole-board retry, else no decision
  then         primary decision ONCE (claimed before the decider runs), then controller.execute(strict)
  09:45-10:15  secondary: every 60 s after a good final scan while ORB has free slots; scans skip
               symbols ORB supervised or executed today; a decision runs only when the board holds a
               symbol not yet judged in the secondary wave today
  10:15        entry cutoff ; 11:00 flatten (the controller's supervisor exits everything)

`tick(now)` never blocks: prep, scans, decisions, executions and the controller's 5 s
supervisor pass run on worker threads with deadlines; tick only starts and collects them.
A late result is discarded (fail closed), except an execution, which is never discarded.
"""
from __future__ import annotations

import copy
import logging
import threading
import time as _time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Dict, List, Optional

from backend.app.core.orb_execution import ET, load_config, parse_hms

log = logging.getLogger("orb_scheduler")

STATE_VERSION = 1
SIZING_START, SIZING_END = time(9, 15), time(9, 30)
PREP_AT = time(9, 15)
PREVIEW_AT, PREVIEW_END = time(9, 36, 10), time(9, 36)
FINAL_AT, FINAL_END = time(9, 38, 30), time(9, 38)
DEADLINES = {"calendar": 30.0, "reconcile": 180.0, "sizing": 50.0, "prep": 900.0, "preview": 240.0,
             "final": 600.0, "decide": 120.0, "secondary_scan": 240.0, "execute": None, "supervise": None}
STEP_NAMES = ("sizing", "prep", "preview", "final", "primary_decision", "secondary", "cutoff", "flatten")


class _Job:
    __slots__ = ("name", "future", "started", "deadline", "meta", "late")

    def __init__(self, name, future, started, deadline, meta):
        self.name, self.future, self.started, self.deadline, self.meta = name, future, started, deadline, meta
        self.late = False


class _Done:
    """A finished inline job (tests): same interface as a Future."""

    def __init__(self, fn):
        try:
            self._r, self._e = fn(), None
        except Exception as exc:  # noqa: BLE001 - reported to the handler
            self._r, self._e = None, exc

    def done(self):
        return True

    def result(self):
        if self._e is not None:
            raise self._e
        return self._r


class OrbScheduler:
    def __init__(self, controller: Any, facade: Any, manifest: Optional[dict] = None, *,
                 clock: Optional[Callable[[], datetime]] = None,
                 is_session: Optional[Callable[[date], Optional[bool]]] = None,
                 persist_cb: Optional[Callable[[dict], None]] = None,
                 inline: bool = False, max_workers: int = 4,
                 deadlines: Optional[Dict[str, Optional[float]]] = None,
                 monotonic: Optional[Callable[[], float]] = None,
                 supervise_every_s: float = 5.0, reconcile_every_s: float = 60.0,
                 sizing_every_s: float = 60.0, secondary_every_s: float = 60.0,
                 prep_retry_s: float = 60.0,
                 on_decision: Optional[Callable[[dict], None]] = None) -> None:
        """on_decision(row): called on the tick thread for every verdict (kind "verdict", with the
        decider's audit/regime and the pick details) and every execution result (kind "execution"),
        so ADT can log them (decisions log, research). A failing hook never stops the scheduler."""
        self.c = controller
        self.facade = facade
        self.cfg = load_config(manifest)
        self.clock = clock or (lambda: datetime.now(ET))
        self.is_session = is_session or (lambda d: d.weekday() < 5)
        self.persist_cb = persist_cb
        self.on_decision = on_decision
        self.inline = inline
        self.deadlines = dict(DEADLINES, **(deadlines or {}))
        self.mono = monotonic or _time.monotonic
        self.every = {"supervise": supervise_every_s, "reconcile": reconcile_every_s,
                      "sizing": sizing_every_s, "secondary": secondary_every_s, "prep": prep_retry_s}
        self._pool = None if inline else ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="OrbSched")
        self._jobs: Dict[str, _Job] = {}
        self._last_start: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._prepped_day: Optional[str] = None      # facade prep lives in memory: redo after a restart
        self._board: Optional[dict] = None           # the committed 9:38 board (memory only)
        self.state: Dict[str, Any] = self._new_day_state(None)

    # ------------------------------------------------------------------ state
    @staticmethod
    def _new_day_state(day: Optional[str], history: Optional[list] = None) -> Dict[str, Any]:
        return {"version": STATE_VERSION, "day": day, "session": None,
                "steps": {n: {"state": "pending", "detail": None, "at": None} for n in STEP_NAMES},
                "final_ok": False, "board_id": None, "judged_secondary": [], "last_secondary_at": None,
                "secondary_runs": 0, "last_verdict": None, "picks": [], "history": list(history or [])[-20:]}

    def to_state(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.state)

    def from_state(self, state: Optional[dict]) -> None:
        """Restore. A step left 'running' by the dead process runs again, except the primary
        decision: once claimed it never runs twice in a day (ORBStraddle's claim row)."""
        with self._lock:
            if not state:
                self.state = self._new_day_state(None)
                return
            if state.get("version") != STATE_VERSION:
                raise ValueError(f"unsupported ORB scheduler state version {state.get('version')!r}")
            base = self._new_day_state(state.get("day"))
            base.update(copy.deepcopy(state))
            for name, st in base["steps"].items():
                if st.get("state") == "running":
                    if name == "primary_decision":
                        st.update(state="interrupted",
                                  detail="the app restarted while the 9:38 decision was running; it is not re-run today")
                    else:
                        st.update(state="pending", detail="restarted: will run again")
            self.state = base

    def _persist(self) -> None:
        if self.persist_cb is None:
            return
        try:
            self.persist_cb(self.to_state())
        except Exception as exc:
            log.error("ORB scheduler state persist failed: %s", exc)

    def _step(self, name: str, state: str, detail: Any = None, now: Optional[datetime] = None) -> None:
        with self._lock:
            self.state["steps"][name] = {"state": state, "detail": None if detail is None else str(detail)[:300],
                                         "at": (now or self._now()).isoformat()}
        self._persist()

    def _step_state(self, name: str) -> str:
        return self.state["steps"].get(name, {}).get("state", "pending")

    def _now(self, now: Optional[datetime] = None) -> datetime:
        n = now or self.clock()
        return n.astimezone(ET)

    @staticmethod
    def _board_end(day: date, t: time) -> datetime:
        """The facade cuts a board at a whole ET minute of the scan day (a timezone-aware datetime)."""
        return datetime.combine(day, time(t.hour, t.minute), tzinfo=ET)

    def kick_supervisor(self) -> None:
        """An ADT exit path asked for an ORB exit: let the next tick start a supervisor pass at once."""
        with self._lock:
            self._last_start.pop("supervise", None)

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # ------------------------------------------------------------------ jobs
    def _running(self, name: str) -> bool:
        return name in self._jobs

    def _start(self, name: str, fn: Callable[[], Any], meta: Optional[dict] = None) -> bool:
        if name in self._jobs:
            return False
        self._last_start[name] = self.mono()
        fut = _Done(fn) if self.inline else self._pool.submit(fn)
        self._jobs[name] = _Job(name, fut, self.mono(), self.deadlines.get(name), meta or {})
        if self.inline:
            self._collect(self._now())
        return True

    def _due(self, name: str, every: float) -> bool:
        last = self._last_start.get(name)
        return last is None or self.mono() - last >= every

    def _collect(self, now: datetime) -> None:
        for name, job in list(self._jobs.items()):
            if self._jobs.get(name) is not job:
                continue            # already handled by a nested collect (inline mode)
            if not job.future.done():
                if job.deadline is not None and not job.late and self.mono() - job.started > job.deadline:
                    job.late = True
                    self._on_late(job, now)
                continue
            del self._jobs[name]
            if job.late:
                continue            # its step already failed; a late answer is never acted on
            try:
                result, error = job.future.result(), None
            except Exception as exc:  # noqa: BLE001
                result, error = None, exc
            try:
                getattr(self, f"_on_{name}")(job, result, error, now)
            except Exception:
                log.exception("ORB scheduler handler %s failed", name)

    def _on_late(self, job: _Job, now: datetime) -> None:
        msg = f"took longer than {job.deadline:.0f} s; its result will be ignored"
        step = {"final": "final", "preview": "preview", "prep": "prep", "sizing": "sizing"}.get(job.name)
        if step:
            self._step(step, "failed", msg, now)
            if job.name == "final":
                self._step("primary_decision", "skipped", "the final scan failed", now)
        elif job.name == "decide":
            wave = job.meta.get("wave")
            self._verdict(wave, "error", f"the decision {msg}", [], now)
            if wave == "primary":
                self._step("primary_decision", "failed", f"the decision {msg}", now)
        elif job.name == "secondary_scan":
            self._step("secondary", "running", f"a secondary scan {msg}", now)

    # ------------------------------------------------------------------ tick
    def tick(self, now: Optional[datetime] = None) -> None:
        """Advance the morning. Never blocks on network work."""
        now = self._now(now)
        with self._lock:
            self._collect(now)
            day = now.date().isoformat()
            if self.state.get("day") != day:
                self.state = self._new_day_state(day, self.state.get("history"))
                self._board = None
                self._persist()
            self._maybe_reconcile(now)
            session = self._session(now)
            if session is not True:
                return
            self._maybe_supervise(now)
            if self.c.mode == "off":
                return
            t = now.time()
            self._maybe_sizing(now, t)
            self._maybe_prep(now, t)
            self._maybe_preview(now, t)
            self._maybe_final(now, t)
            self._maybe_primary_decision(now, t)
            self._maybe_secondary(now, t)
            if t >= parse_hms(self.cfg["cutoff"]) and self._step_state("cutoff") != "done":
                self._step("cutoff", "done", "no new ORB entries after the cutoff", now)
            if t >= parse_hms(self.cfg["flatten"]) and self._step_state("flatten") != "done":
                self._step("flatten", "done", "time flatten: the supervisor exits every ORB position", now)

    # ------------------------------------------------------------------ calendar / supervise
    def _session(self, now: datetime) -> Optional[bool]:
        s = self.state.get("session")
        if s is not None:
            return s
        if now.weekday() >= 5:
            self.state["session"] = False
            self._persist()
            return False
        if not self._running("calendar") and self._due("calendar", 60.0):
            d = now.date()
            self._start("calendar", lambda: self.is_session(d))
        return self.state.get("session")

    def _on_calendar(self, job, result, error, now):
        if error is not None or result is None:
            return                  # unverifiable: retried in a minute; no session meanwhile
        self.state["session"] = result is True
        self._persist()

    def _maybe_reconcile(self, now: datetime) -> None:
        if self.c.ready or self._running("reconcile") or not self._due("reconcile", self.every["reconcile"]):
            return
        self._start("reconcile", lambda: self.c.reconcile_on_startup())

    def _on_reconcile(self, job, result, error, now):
        if error is not None:
            log.error("ORB startup reconciliation failed: %s", error)

    def _maybe_supervise(self, now: datetime) -> None:
        if self._running("supervise") or not self._due("supervise", self.every["supervise"]):
            return
        if not self.c.needs_supervision():
            return
        self._start("supervise", lambda: self.c.tick())

    def _on_supervise(self, job, result, error, now):
        if error is not None:
            log.error("ORB supervisor pass failed: %s", error)

    # ------------------------------------------------------------------ morning steps
    def _maybe_sizing(self, now: datetime, t: time) -> None:
        if not (SIZING_START <= t < SIZING_END) or self._step_state("sizing") == "done":
            return
        if self._running("sizing") or not self._due("sizing", self.every["sizing"]):
            return
        self._step("sizing", "running", "freezing today's sizing (day-start equity and buying power)", now)
        self._start("sizing", lambda: self.c.freeze_session())

    def _on_sizing(self, job, result, error, now):
        if error is not None:
            self._step("sizing", "failed", f"sizing freeze failed: {error}", now)
            return
        ok, detail = result
        self._step("sizing", "done" if ok else "failed", detail, now)

    def _maybe_prep(self, now: datetime, t: time) -> None:
        day = now.date().isoformat()
        if t < PREP_AT or self._prepped_day == day or t >= parse_hms(self.cfg["cutoff"]):
            return
        if self._running("prep") or not self._due("prep", self.every["prep"]):
            return
        self._step("prep", "running", "preparing today's stock list", now)
        d = now.date()
        self._start("prep", lambda: self.facade.prep(d))

    def _on_prep(self, job, result, error, now):
        if error is not None or not (isinstance(result, dict) and result.get("ok")):
            why = error if error is not None else (result.get("error") if isinstance(result, dict) else result)
            self._step("prep", "failed", f"prep failed: {why}", now)
            return
        self._prepped_day = now.date().isoformat()
        n = result.get("universe_size") or result.get("symbols") or result.get("watchlist") or result.get("n")
        self._step("prep", "done", f"{len(n) if isinstance(n, (list, tuple)) else n or 'the'} symbols ready", now)

    def _coverage_ok(self, res: Any, require: bool = True) -> (bool, str):
        if not isinstance(res, dict):
            return False, "the scan returned nothing usable"
        if not res.get("ok"):
            return False, str(res.get("error") or "the scan failed")
        cov = res.get("coverage")
        if cov is None:
            return (not require), "the scan reported no coverage"
        try:
            cov = float(cov)
        except (TypeError, ValueError):
            return False, "the scan coverage is unreadable"
        if cov < self.cfg["min_scan_coverage"]:
            return False, (f"scan covered only {cov:.0%} of the symbols "
                           f"(floor {self.cfg['min_scan_coverage']:.0%})")
        return True, f"{len(res.get('cards') or [])} cards, {cov:.0%} coverage"

    def _maybe_preview(self, now: datetime, t: time) -> None:
        if t < PREVIEW_AT or self._step_state("preview") in ("done", "failed", "skipped", "running"):
            return
        if t >= FINAL_AT or self._step_state("final") != "pending":
            self._step("preview", "skipped", "the final scan is due; the display preview is skipped", now)
            return
        if self._prepped_day != now.date().isoformat() or self._running("preview"):
            return
        day = now.date()
        self._step("preview", "running", "preview scan to 9:36", now)
        end = self._board_end(day, PREVIEW_END)
        self._start("preview", lambda: self.facade.scan(day, end, "preview", []))

    def _on_preview(self, job, result, error, now):
        if error is not None:
            self._step("preview", "failed", f"preview failed: {error}", now)
            return
        ok, detail = self._coverage_ok(result, require=False)
        self._step("preview", "done" if ok else "failed", detail, now)

    def _maybe_final(self, now: datetime, t: time) -> None:
        if t < FINAL_AT or self._step_state("final") in ("done", "failed", "running", "skipped"):
            return
        if t >= parse_hms(self.cfg["cutoff"]):
            self._step("final", "skipped", "the app was not running in time for the 9:38 scan", now)
            self._step("primary_decision", "skipped", "no final scan today", now)
            return
        if self._running("prep") or self._running("preview"):
            return                  # ORBStraddle runs these in one loop: the final scan waits for them
        day = now.date()
        end = self._board_end(day, FINAL_END)
        self._step("final", "running", "final scan to 9:38", now)

        def job():
            attempts = []
            for n in (1, 2):
                try:
                    res = self.facade.scan(day, end, "primary", [])
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"attempt {n}: {exc}")
                    continue
                ok, why = self._coverage_ok(res)
                if ok:
                    return {"ok": True, "result": res, "detail": why + (" (retry)" if n == 2 else ""),
                            "attempts": attempts}
                attempts.append(f"attempt {n}: {why}")
            return {"ok": False, "attempts": attempts}
        self._start("final", job)

    def _on_final(self, job, result, error, now):
        if error is not None or not result or not result.get("ok"):
            why = error or "; ".join((result or {}).get("attempts") or []) or "the final scan failed"
            self._step("final", "failed", why, now)
            self._step("primary_decision", "skipped", "the final scan failed", now)
            self._verdict("primary", "no_decision", f"no decision: the final scan failed ({why})", [], now)
            return
        with self._lock:
            self.state["final_ok"] = True
            self.state["board_id"] = result["result"].get("board_id")
            self._board = result["result"]
        self._step("final", "done", result["detail"], now)

    # ------------------------------------------------------------------ decisions
    def _occupied(self, cards: List[dict]) -> List[str]:
        out = set(self.c.reserved_symbols())
        for c in cards or []:
            sym = str((c or {}).get("symbol") or "").upper()
            if not sym:
                continue
            try:
                if self.c.owns(sym) or self.c.is_occupied(sym):
                    out.add(sym)
            except Exception:
                out.add(sym)            # cannot prove it free: treat as taken
        return sorted(out)

    def _entry_gate(self, now: datetime, wave: str) -> Optional[str]:
        """auditor.run_autopilot's refusals before the decider runs."""
        if self.c.mode == "off":
            return "ORB is off"
        if wave == "secondary":
            t = now.time()
            if not (parse_hms(self.cfg["secondary_start"]) <= t <= parse_hms(self.cfg["secondary_end"])):
                return f"secondary wave cannot execute at {t.strftime('%H:%M:%S')}"
        if self.c.capacity_reached():
            return "something already executed today (session capacity reached)"
        if self.c.slots_available() <= 0:
            return "no execution slots available"
        if now.time() >= parse_hms(self.cfg["cutoff"]):
            return f"past the {parse_hms(self.cfg['cutoff']).strftime('%H:%M')} cutoff: too late to enter"
        return None

    def _maybe_primary_decision(self, now: datetime, t: time) -> None:
        if self._step_state("final") != "done" or self._step_state("primary_decision") != "pending":
            return
        if self._running("decide") or self._running("execute"):
            return
        gate = self._entry_gate(now, "primary")
        if gate:
            self._step("primary_decision", "skipped", gate, now)
            self._verdict("primary", "skipped", gate, [], now)
            return
        board = self._board
        if board is None:
            # restarted after the final scan: the board lives only in memory, so rescan once
            self.state["steps"]["final"] = {"state": "pending", "detail": "restarted: rescanning for the board",
                                            "at": now.isoformat()}
            return
        self._step("primary_decision", "running", "deciding on the 9:38 board", now)
        self._start_decide("primary", board, now)

    def _start_decide(self, wave: str, board: dict, now: datetime) -> None:
        day = now.date()
        occupied = self._occupied(board.get("cards") or [])
        # the durable union of symbols ORB executed today (controller state survives restarts)
        executed = sorted(self.c.symbols_today())

        def job():
            return self.facade.decide(day, board, wave, self._now(), occupied, executed_today=executed)
        self._start("decide", job, {"wave": wave, "board_id": board.get("board_id"),
                                     "symbols": sorted({str(c.get("symbol")) for c in board.get("cards") or []})})

    def _on_decide(self, job, result, error, now):
        wave = job.meta.get("wave")
        if error is not None or not isinstance(result, dict):
            msg = f"the decider failed: {error or 'no answer'}"
            self._verdict(wave, "error", msg, [], now)
            if wave == "primary":
                self._step("primary_decision", "failed", msg, now)
            return
        picks = list(result.get("picks") or [])
        verdict = str(result.get("verdict") or ("pick" if picks else "pass"))
        reason = result.get("reason")
        board = {"board_id": job.meta.get("board_id"), "board_symbols": job.meta.get("symbols") or [],
                 "regime": result.get("regime") or {}, "refused_picks": list(result.get("refused") or []),
                 "executing": False}
        if verdict == "refused":
            # the facade would not decide this board (stale, wrong wave, past the cutoff, ...)
            msg = f"the board was not decided: {reason or 'refused'}"
            self._verdict(wave, "refused", msg, [], now, extra=board)
            if wave == "primary":
                self._step("primary_decision", "done", f"no trade: {msg}", now)
            return
        if not picks:
            self._verdict(wave, verdict, reason or "no pick passed", [], now, audit=result.get("audit"), extra=board)
            if wave == "primary":
                self._step("primary_decision", "done", f"no trade: {reason or verdict}", now)
            return
        # auditor: re-check what could have changed while the decider ran
        gate = self._entry_gate(self._now(), wave)
        if gate:
            self._verdict(wave, verdict, f"picked, but {gate}", picks, now, audit=result.get("audit"), extra=board)
            if wave == "primary":
                self._step("primary_decision", "done", f"picked, not executed: {gate}", now)
            return
        self._verdict(wave, verdict, reason or "picked", picks, now, audit=result.get("audit"),
                      extra=dict(board, executing=True))
        if wave == "primary":
            self._step("primary_decision", "running", "sending the orders", now)
        self._start("execute", lambda: self.c.execute(picks, self._now(), strict=True, wave=wave), {"wave": wave})

    def _on_execute(self, job, result, error, now):
        wave = job.meta.get("wave")
        if error is not None:
            out = {"ok": False, "reason": f"execution crashed: {error}"}
        else:
            out = result or {}
        with self._lock:
            lv = self.state.get("last_verdict") or {}
            if lv.get("wave") == wave:
                lv["execution"] = {"ok": out.get("ok"), "reason": out.get("reason"),
                                   "accepted": out.get("accepted", 0),
                                   "refused": [list(x) for x in out.get("refused") or []][:8]}
                if self.state["history"]:
                    self.state["history"][-1] = copy.deepcopy(lv)
        if wave == "primary":
            detail = (f"{out.get('accepted', 0)} order(s) placed" if out.get("ok")
                      else f"no order: {out.get('reason')}")
            self._step("primary_decision", "done", detail, now)
        self._persist()
        placed = [{k: p.get(k) for k in ("symbol", "direction", "tier", "shares", "entry_ref", "stop", "target",
                                          "rd", "risk_usd", "coid", "outcome")}
                  | {"error": (p.get("result") or {}).get("error") if isinstance(p.get("result"), dict) else None}
                  for p in out.get("placed") or []]
        self._notify({"kind": "execution", "wave": wave, "at": now.isoformat(), "ok": out.get("ok"),
                      "reason": out.get("reason"), "mode": self.c.mode, "placed": placed,
                      "refused": [list(x) for x in out.get("refused") or []]})

    def _maybe_secondary(self, now: datetime, t: time) -> None:
        start, end = parse_hms(self.cfg["secondary_start"]), parse_hms(self.cfg["secondary_end"])
        if not (start <= t < end) or not self.state.get("final_ok"):
            return
        if self._running("secondary_scan") or self._running("decide") or self._running("execute"):
            return
        last = self.state.get("last_secondary_at")
        if last is not None:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() < self.every["secondary"]:
                    return
            except ValueError:
                pass
        if self.c.slots_available() <= 0:
            return
        self.state["last_secondary_at"] = now.isoformat()
        self.state["secondary_runs"] = int(self.state.get("secondary_runs") or 0) + 1
        self._step("secondary", "running", f"watching for new breakouts (scan to {now.strftime('%H:%M')})", now)
        # scanner.run_secondary: skip what ORB supervises now, and every symbol it executed today
        # (the controller's durable position rows, so a restart cannot forget a closed trade)
        day, end = now.date(), self._board_end(now.date(), now.time())
        skip, executed = sorted(self.c.reserved_symbols()), sorted(self.c.symbols_today())
        self._start("secondary_scan",
                    lambda: self.facade.scan(day, end, "secondary", skip, executed_today=executed))

    def _on_secondary_scan(self, job, result, error, now):
        if error is not None:
            self._step("secondary", "running", f"secondary scan error: {error}", now)
            return
        if not isinstance(result, dict) or not result.get("ok"):
            self._step("secondary", "running", f"secondary scan failed: {(result or {}).get('error')}", now)
            return
        cards = result.get("cards") or []
        if cards or result.get("coverage") is not None:
            ok, why = self._coverage_ok(result, require=False)
            if not ok:
                self._step("secondary", "running", f"secondary scan refused: {why}", now)
                return
        if not cards:
            self._step("secondary", "running", "no new breakouts on this scan", now)
            return
        offered = {str(c.get("symbol")) for c in cards if c.get("symbol")}
        judged = set(self.state.get("judged_secondary") or [])
        if judged and not (offered - judged):
            self._step("secondary", "running", "no new symbol since the last secondary decision", now)
            return
        gate = self._entry_gate(self._now(), "secondary")
        if gate:
            self._step("secondary", "running", f"not deciding: {gate}", now)
            return
        with self._lock:
            self.state["judged_secondary"] = sorted(judged | offered)
        self._persist()
        self._start_decide("secondary", result, now)

    def _notify(self, row: dict) -> None:
        if self.on_decision is None:
            return
        try:
            self.on_decision(row)
        except Exception:
            log.exception("ORB on_decision hook failed")

    def _verdict(self, wave: Optional[str], verdict: str, reason: Any, picks: List[dict], now: datetime,
                 audit: Any = None, extra: Optional[dict] = None) -> None:
        row = {"wave": wave, "verdict": verdict, "reason": None if reason is None else str(reason)[:300],
               "picks": [{"symbol": p.get("symbol"), "direction": p.get("direction"), "tier": p.get("tier")}
                         for p in picks], "at": now.isoformat()}
        with self._lock:
            self.state["last_verdict"] = row
            self.state["picks"] = row["picks"]
            self.state["history"].append(copy.deepcopy(row))
            self.state["history"] = self.state["history"][-20:]
        self._persist()
        details = [{k: p.get(k) for k in ("symbol", "direction", "tier", "entry", "stop", "mode", "catalyst_summary")}
                   for p in picks]
        self._notify(dict(row, kind="verdict", audit=list(audit or []), pick_details=details, **(extra or {})))

    # ------------------------------------------------------------------ status
    def _next_step(self, now: datetime) -> Optional[dict]:
        t = now.time()
        at = lambda tt: datetime.combine(now.date(), tt, tzinfo=ET).isoformat()  # noqa: E731
        if self.state.get("session") is False:
            return None
        if t < SIZING_START:
            return {"what": "freeze sizing and prepare the stock list", "at": at(SIZING_START)}
        if t < PREVIEW_AT:
            return {"what": "preview scan", "at": at(PREVIEW_AT)}
        if t < FINAL_AT:
            return {"what": "final scan and decision", "at": at(FINAL_AT)}
        cut = parse_hms(self.cfg["cutoff"])
        sec_start = parse_hms(self.cfg["secondary_start"])
        if t < sec_start and self.state.get("final_ok"):
            return {"what": "first check for new breakouts", "at": at(sec_start)}
        if t < cut and self.state.get("final_ok"):
            last = self.state.get("last_secondary_at")
            nxt = (datetime.fromisoformat(last) + timedelta(seconds=self.every["secondary"])) if last else now
            return {"what": "next check for new breakouts", "at": max(nxt, now).isoformat()}
        fl = parse_hms(self.cfg["flatten"])
        if t < fl:
            return {"what": "close every ORB position", "at": at(fl)}
        return None

    def _sentence(self, now: datetime) -> str:
        t = now.time()
        st = self._step_state
        if self.state.get("session") is False:
            return "No market session today."
        if self.state.get("session") is None:
            return "Checking the market calendar."
        if self.c.mode == "off":
            return "ORB is switched off: no scans or new trades; any open ORB trade is still managed to its exit."
        if not self.c.ready:
            return "Checking ORB's orders at the broker after a restart; no new trades until that finishes."
        if t < SIZING_START:
            return "Waiting for 9:15 AM to freeze today's sizing and prepare the stock list."
        if t < PREVIEW_AT:
            return "Preparing: sizing is " + ("frozen" if st("sizing") == "done" else st("sizing")) + \
                   ", stock list " + ("ready" if st("prep") == "done" else st("prep")) + "."
        if st("final") in ("pending", "running"):
            return "Running the 9:38 scan of the opening range breakouts."
        if st("final") in ("failed", "skipped"):
            base = "The 9:38 scan did not complete, so ORB makes no decision today."
        elif st("primary_decision") == "running":
            base = "Deciding on the 9:38 board."
        else:
            lv = self.state.get("last_verdict") or {}
            picked = ", ".join(f"{p['symbol']} {p['direction']}" for p in lv.get("picks") or [])
            plain = ", ".join(("buy " if p.get("direction") == "long" else "sell short ") + str(p["symbol"])
                              for p in lv.get("picks") or [])
            if not lv:
                base = ""
            elif picked and self.c.mode == "shadow":
                base = f"Shadow mode (watching only, no orders): would have placed {plain}."
            elif picked:
                base = f"Last decision: picked {picked}."
            elif lv.get("verdict") in ("refused", "error", "no_decision"):
                base = f"No decision: {lv.get('reason') or lv.get('verdict')}."
            else:
                base = f"Last decision: sat out ({lv.get('reason') or 'no card passed'})."
        cut, fl = parse_hms(self.cfg["cutoff"]), parse_hms(self.cfg["flatten"])
        if t < cut and self.state.get("final_ok"):
            tail = " Watching for new breakouts every minute until 10:15 AM." if self.c.slots_available() > 0 \
                else " All ORB slots are in use."
        elif t < fl:
            tail = " No new entries after 10:15 AM; open ORB trades close by 11:00 AM."
        else:
            tail = " Done for today."
        return (base + tail).strip()

    def status(self) -> dict:
        now = self._now()
        with self._lock:
            return {"day": self.state.get("day"), "session": self.state.get("session"), "mode": self.c.mode,
                    "ready": self.c.ready, "step": self._sentence(now), "next_step": self._next_step(now),
                    "steps": copy.deepcopy(self.state["steps"]), "last_verdict": copy.deepcopy(self.state.get("last_verdict")),
                    "picks": list(self.state.get("picks") or []),
                    "secondary": {"runs": self.state.get("secondary_runs"), "judged": len(self.state.get("judged_secondary") or []),
                                  "last_scan_at": self.state.get("last_secondary_at")},
                    "running": sorted(self._jobs)}
