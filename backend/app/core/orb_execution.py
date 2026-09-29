"""ORB execution layer: ORBStraddle's order, ownership, sizing and exit rules inside ADT.

Source of truth: ORBStraddle @71b001f (core.execute ~2059-2566, orders.py bracket submit and
leg helpers, core._supervise ~3162-3640, core.exit_own ~3926-4228, ownership.OwnBook, sizing
~944-987 / 1546-1630, daily halt ~849-925). This module re-implements that execution in ADT;
the decision code (scanner, cards, adaptive decider) lives in backend/app/strategies/orbs/
behind the OrbsFacade and is NOT here.

How it is driven (phase 3 wires this):
  * `reconcile_on_startup()` once after `from_state()`; entries stay refused until it
    succeeds (anything the controller cannot prove is its own is reported, never adopted).
  * `freeze_session(now)` 09:15-09:29 (the scheduler does this).
  * `execute(picks, now)` right after a decision (the scheduler does this).
  * `tick(now)` every 5 s while `needs_supervision()` (the scheduler does this).
  * `request_all_exits(reason)` / `request_exit(symbol, reason)` from ADT's breaker,
    flatten, manual and session paths; the next tick exits (cancel own orders first).

Every method here is synchronous and may block on Alpaca: call it from a worker thread,
never from the event loop. Hooks (persist_cb, on_fill, is_occupied, reserve, release,
account_halt, on_event) are called from that worker thread.

Hook contracts:
  persist_cb(state: dict)   durable write of `to_state()` BEFORE it returns. Called before
                            every POST (intent first). If it raises, the POST is not sent.
  on_fill(symbol, side, qty, price, order_id, role)
                            one call per NEW fill increment of an own order (monotonic, so a
                            stale read never re-emits). role is entry|target|stop|exit.
                            State is persisted right after, in the same thread.
  is_occupied(symbol)       True if ADT already holds / has a working order / staged entry /
                            another arm's reservation on the symbol. Must NOT count ORB's own
                            reservation (ORB checks its own book itself).
  reserve(symbol) -> bool   atomically take the symbol for ORB (False = someone got it).
  release(symbol)           give it back (pick refused, order rejected, position closed).
  account_halt() -> str|None ADT's account daily loss stop; truthy = no entries and exit all.
  entry_gate(symbol) -> str|None
                            ADT's last check before a real entry POST (market hours, broker
                            position mismatch, durable ledger health); a reason = that pick is
                            skipped, nothing is sent. Never consulted for exits.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import math
import secrets
import threading
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.app.core.broker import (
    BrokerError, BrokerHTTPError, BrokerTransportError,
)

log = logging.getLogger("orb_execution")
ET = ZoneInfo("America/New_York")

STATE_VERSION = 1
MODES = ("off", "shadow", "live")
# Alpaca statuses after which an order can never fill again (ownership.TERMINAL).
TERMINAL = frozenset({"filled", "canceled", "expired", "rejected", "replaced"})
# Cannot fill again TODAY: good enough to confirm a cancel before an exit (ownership.QUIESCENT).
QUIESCENT = TERMINAL | frozenset({"done_for_day"})
ACTIVE = frozenset({"PENDING_SUBMIT", "SUBMITTED", "UNKNOWN", "OPEN"})
OWN_REFRESH_MIN_S = 10.0       # a live non-entry own order is refetched at most this often
RECHECK_NOT_FOUND_S = 60.0     # an inferred never-reached entry is asked again this often
PENDING_GRACE_S = 60.0         # a lost submit is not called "never reached the broker" sooner
EXIT_CONFIRM_S = 10.0          # _verified_flatten confirm_s
LATCH_ALARMS_S = (30.0, 120.0, 600.0)   # a latched exit still not flat after these: escalating alarms
ESCALATED_CONFIRM_S = 20.0
CANCEL_CONFIRM_S = 6.0         # EXIT_CANCEL_POLLS (6) x 0.5-1 s
COID_LOOKUPS = 3               # orders.reconcile_order_by_coid: 3 tries ...
COID_LOOKUP_GAP_S = 2.0        # ... 2 s apart
ABORT_MARK = "ABORT"


# ----------------------------------------------------------------------------- config
DEFAULTS: Dict[str, Any] = {
    "risk_pct": 2.0, "max_day_risk_pct": 2.5, "max_open_slots": 4, "max_structure_picks": 2,
    "daily_loss_halt_pct": 3.0, "max_slip_stop_frac": 0.33, "target_r": 0.75,
    "fast_fail_r": -0.40, "breakeven_r": 0.75, "clawback_peak_r": 0.60,
    "clawback_giveback_r": 0.25, "absorption_arm_r": 0.5, "max_gross_mult": 3.0,
    "max_buying_power_pct": 60.0, "max_name_notional_pct": 150.0, "cutoff": "10:15:00",
    "flatten": "11:00", "freeze": "09:38", "secondary_start": "09:45:00",
    "secondary_end": "10:15:00", "min_scan_coverage": 0.90, "min_stop_pct": 0.5,
    "own_price_sanity_pct": 50.0, "flatten_escalate_after_s": 120.0,
    "flatten_escalate_hm": "15:45", "excluded_symbols": ["TSLA", "CDE"],
    "request_budget_per_min": 90, "exit_reserve_per_min": 40, "coid_prefix": "adt-orb",
    "shadow_equity": 100000.0, "expected_account": None, "absorption_max_result_age_s": 8.0,
}
ALIASES: Dict[str, List[str]] = {
    "risk_pct": ["risk_pct", "orbs_risk_pct"],
    "max_day_risk_pct": ["max_day_risk_pct", "day_risk_budget_pct", "day_risk_pct", "day_risk_budget_max_risk_pct"],
    "max_open_slots": ["max_open_slots", "max_slots", "open_slots"],
    "max_structure_picks": ["max_structure_picks", "max_structure"],
    "daily_loss_halt_pct": ["daily_loss_halt_pct", "own_daily_halt_pct", "daily_halt_pct"],
    "max_slip_stop_frac": ["max_slip_stop_frac", "no_chase_frac", "slip_stop_frac"],
    "target_r": ["target_r"],
    "fast_fail_r": ["fast_fail_r"],
    "breakeven_r": ["breakeven_r", "be_lock_r", "breakeven_lock_r"],
    "clawback_peak_r": ["clawback_peak_r", "trail_peak_r", "clawback_peak"],
    "clawback_giveback_r": ["clawback_giveback_r", "trail_giveback_r", "clawback_giveback"],
    "absorption_arm_r": ["absorption_arm_r", "absorption_arm"],
    "max_gross_mult": ["max_gross_mult", "max_gross_pct", "max_gross"],
    "max_buying_power_pct": ["max_buying_power_pct", "gross_buying_power_pct", "buying_power_pct"],
    "max_name_notional_pct": ["max_name_notional_pct", "name_cap_pct", "name_notional_pct"],
    "cutoff": ["cutoff", "autopilot_cutoff", "entry_cutoff", "cutoff_et"],
    "flatten": ["flatten", "flatten_et", "flatten_time"],
    "freeze": ["freeze", "freeze_et", "freeze_time"],
    "secondary_start": ["secondary_start", "secondary_wave_start"],
    "secondary_end": ["secondary_end", "secondary_wave_end"],
    "min_scan_coverage": ["min_scan_coverage", "scan_coverage", "coverage_floor", "coverage"],
    "excluded_symbols": ["excluded_symbols", "exclude_symbols", "excluded"],
    "request_budget_per_min": ["request_budget_per_min"],
    "exit_reserve_per_min": ["exit_reserve_per_min"],
    "coid_prefix": ["coid_prefix", "adt_coid_prefix"],
    "expected_account": ["expected_account", "account_number", "account_allowlist"],
    "absorption_max_result_age_s": ["absorption_max_result_age_s", "absorption_max_age_s",
                                    "absorption_result_age_s", "max_result_age_s"],
}


def _flatten_manifest(m: Any, prefix: str = "", out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {} if out is None else out
    if isinstance(m, dict):
        for k, v in m.items():
            key = str(k).lower()
            path = f"{prefix}_{key}" if prefix else key
            if isinstance(v, dict):
                _flatten_manifest(v, path, out)
                _flatten_manifest(v, key, out)
            else:
                out.setdefault(path, v)
                out.setdefault(key, v)
    return out


def load_config(manifest: Optional[dict]) -> Dict[str, Any]:
    """Effective ORB execution config: the manifest's value where it has one, else the default."""
    flat = _flatten_manifest(manifest or {})
    cfg = dict(DEFAULTS)
    for name, aliases in ALIASES.items():
        for a in aliases:
            if a in flat and flat[a] is not None:
                cfg[name] = flat[a]
                break
    g = float(cfg["max_gross_mult"])
    cfg["max_gross_mult"] = g / 100.0 if g > 10.0 else g
    d = float(cfg["max_day_risk_pct"])
    cfg["max_day_risk_frac"] = d if d <= 0.20 else d / 100.0
    cov = float(cfg["min_scan_coverage"])
    cfg["min_scan_coverage"] = cov / 100.0 if cov > 1.0 else cov
    ex = cfg["excluded_symbols"]
    cfg["excluded_symbols"] = sorted({str(s).upper() for s in (ex if isinstance(ex, (list, tuple, set)) else [ex])})
    for k in ("risk_pct", "daily_loss_halt_pct", "max_slip_stop_frac", "target_r", "fast_fail_r",
              "breakeven_r", "clawback_peak_r", "clawback_giveback_r", "absorption_arm_r",
              "max_buying_power_pct", "max_name_notional_pct", "min_stop_pct",
              "own_price_sanity_pct", "flatten_escalate_after_s", "shadow_equity",
              "absorption_max_result_age_s"):
        cfg[k] = float(cfg[k])
    for k in ("max_open_slots", "max_structure_picks", "request_budget_per_min", "exit_reserve_per_min"):
        cfg[k] = int(cfg[k])
    return cfg


def parse_hms(v: Any) -> time:
    if isinstance(v, time):
        return v
    parts = [int(x) for x in str(v).strip().split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])


# ----------------------------------------------------------------------------- budget
class BudgetThrottled(BrokerError):
    """The local request budget refused the call: it was never sent."""


class DestinationRefused(BrokerError):
    """The pinned Alpaca account could not be verified: the write was never sent."""


DEST_TTL_S = 60.0              # a verified destination is trusted this long (same credentials)


class RequestBudget:
    """ORBStraddle core.RequestBudget: a main token bucket plus an exit-only reserve.

    exit    - main if it has a token, else the reserve; waits (paced) up to EXIT_MAX_WAIT_S.
    normal  - waits for a main token up to NORMAL_MAX_WAIT_S.
    refresh - skipped when fewer than REFRESH_RESERVE main tokens remain.
    """
    REFRESH_RESERVE = 5.0
    NORMAL_MAX_WAIT_S = 6.0
    EXIT_MAX_WAIT_S = 15.0

    def __init__(self, per_min: int = 90, reserve_per_min: int = 40, clock: Callable[[], float] = None,
                 sleep: Callable[[float], None] = None) -> None:
        self.per_min, self.reserve_per_min = per_min, reserve_per_min
        self.rate = per_min / 60.0
        self.capacity = float(max(10, per_min // 3))
        self.reserve_rate = reserve_per_min / 60.0
        self.reserve_capacity = float(max(5, reserve_per_min // 3))
        self.tokens, self.reserve = self.capacity, self.reserve_capacity
        self._clock = clock or _time.monotonic
        self._sleep = sleep or _time.sleep
        self._last = self._clock()
        self._lock = threading.Lock()
        self.throttled = 0
        self.borrowed = 0
        self.used = 0

    def _refill(self) -> None:
        now = self._clock()
        dt = max(0.0, now - self._last)
        self.tokens = min(self.capacity, self.tokens + dt * self.rate)
        self.reserve = min(self.reserve_capacity, self.reserve + dt * self.reserve_rate)
        self._last = now

    def acquire(self, prio: str = "normal") -> None:
        wait = self.EXIT_MAX_WAIT_S if prio == "exit" else self.NORMAL_MAX_WAIT_S
        deadline = self._clock() + wait
        while True:
            with self._lock:
                self._refill()
                if prio == "exit":
                    if self.tokens >= 1:
                        self.tokens -= 1; self.used += 1
                        return
                    if self.reserve >= 1:
                        self.reserve -= 1; self.borrowed += 1; self.used += 1
                        return
                else:
                    need = 1.0 + (self.REFRESH_RESERVE if prio == "refresh" else 0.0)
                    if self.tokens >= need:
                        self.tokens -= 1; self.used += 1
                        return
                if prio == "refresh" or self._clock() >= deadline:
                    self.throttled += 1
                    raise BudgetThrottled(f"ORB request budget: {prio} request skipped "
                                          f"({self.per_min}/min + {self.reserve_per_min}/min exit reserve)")
            self._sleep(0.2)


# ----------------------------------------------------------------------------- helpers
def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _i(v: Any) -> int:
    x = _f(v)
    return int(x) if x is not None else 0


def _parse_ts(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else None
    if isinstance(v, (int, float)) and math.isfinite(v):
        x = float(v)
        if x > 1e17:
            x /= 1e9
        elif x > 1e14:
            x /= 1e6
        elif x > 1e11:
            x /= 1e3
        return datetime.fromtimestamp(x, tz=timezone.utc)
    if not isinstance(v, str) or not v:
        return None
    txt = v.strip().replace("Z", "+00:00")
    if "." in txt:
        head, rest = txt.split(".", 1)
        frac, tz = rest, ""
        for sep in ("+", "-"):
            if sep in rest:
                frac, tz = rest.split(sep, 1)
                tz = sep + tz
                break
        txt = head + "." + frac[:6].ljust(6, "0") + tz
    try:
        d = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return d if d.tzinfo else None


def _fill_math(q: int, avg: Optional[float], side: str, delta: int, px: Optional[float]):
    """ownership.OwnBook._fill_math on whole shares: (new_q, new_avg, pnl, unknown)."""
    signed = delta if side == "buy" else -delta
    sign = (lambda d: (d > 0) - (d < 0))
    unknown, pnl = False, 0.0
    if q == 0 or sign(q) == sign(signed):
        new_q = q + signed
        if px is None or (q != 0 and avg is None):
            avg, unknown = None, True
        else:
            avg = (abs(q) * (avg or 0.0) + abs(signed) * px) / abs(new_q)
    else:
        close_qty = min(abs(signed), abs(q))
        if px is None or avg is None:
            unknown = True
        else:
            pnl = close_qty * (px - avg) * (1 if q > 0 else -1)
        new_q = q + signed
        if new_q == 0:
            avg = None
        elif sign(new_q) != sign(q):
            avg = px
    return new_q, avg, pnl, unknown


def _res(ok: bool, reason: Optional[str] = None, **kw) -> Dict[str, Any]:
    out = {"ok": ok, "reason": reason}
    out.update(kw)
    return out


# ============================================================================= controller
class OrbExecutionController:
    """ORBStraddle's execution (entry brackets, own book, supervisor exits) for ADT's ORB."""

    def __init__(self, broker: Any, facade: Any, manifest: Optional[dict] = None, *,
                 clock: Optional[Callable[[], datetime]] = None,
                 persist_cb: Optional[Callable[[dict], None]] = None,
                 on_fill: Optional[Callable[..., None]] = None,
                 is_occupied: Optional[Callable[[str], bool]] = None,
                 reserve: Optional[Callable[[str], bool]] = None,
                 release: Optional[Callable[[str], None]] = None,
                 account_halt: Optional[Callable[[], Any]] = None,
                 on_event: Optional[Callable[[dict], None]] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 budget: Optional[RequestBudget] = None,
                 mode: str = "shadow",
                 expected_account: Optional[str] = None,
                 entry_gate: Optional[Callable[[str], Optional[str]]] = None) -> None:
        if mode not in MODES:
            raise ValueError(f"ORB mode must be one of {MODES}")
        self.broker = broker
        self.facade = facade
        self.cfg = load_config(manifest)
        # core.verify_destination: the ONE Alpaca account ORB may trade (ADT: PA3CSVDZMMPY)
        acct_pin = expected_account or self.cfg.get("expected_account")
        if isinstance(acct_pin, (list, tuple)):
            acct_pin = acct_pin[0] if len(acct_pin) == 1 else None      # one account, never "any of"
        self.expected_account = str(acct_pin or "").strip() or None
        self.clock = clock or (lambda: datetime.now(ET))
        self.persist_cb = persist_cb
        self.on_fill = on_fill
        self.is_occupied = is_occupied or (lambda s: False)
        self.reserve = reserve or (lambda s: True)
        self.release = release or (lambda s: None)
        self.account_halt = account_halt or (lambda: None)
        self.entry_gate = entry_gate
        # ADT shutdown fence: entries stop first (drain begins), then every write (drain deadline)
        self._entries_closed: Optional[str] = None
        self._writes_closed: Optional[str] = None
        # every broker write holds a slot; close_writes() takes the gate exclusively (no new slot, waits
        # for the ones in flight), so no write can pass the check and POST after the fence closes
        self._write_gate = threading.Condition()
        self._writes_in_flight = 0
        self._inflight_writes: Dict[int, dict] = {}     # slot id -> what is being written (coid / order id)
        self._resolving: Optional[str] = None            # unknown-write marker an operator is removing
        self._last_position_qtys: Optional[Dict[str, int]] = None   # symbol -> Alpaca qty, last positions read
        self._state_problem: Optional[str] = None       # malformed rows found at restore: never ready
        # entry admission vs blockers: an entry holds this from its final re-check through its POST;
        # flatten-all / halt / mode changes take it before they change anything
        self._admission_lock = threading.RLock()
        self._slot_seq = 0
        self.on_event = on_event
        self.sleep = sleep or _time.sleep
        self.budget = budget or RequestBudget(self.cfg["request_budget_per_min"], self.cfg["exit_reserve_per_min"])
        self.mode = mode
        self.ready = False
        self.startup_report: Optional[dict] = None
        self._lock = threading.RLock()
        self._exec_lock = threading.Lock()
        self._tick_lock = threading.Lock()
        self._sym_locks: Dict[str, threading.Lock] = {}
        self._sym_guard = threading.Lock()
        self._held: set = set()          # symbols this process holds through reserve()
        self._pending_reserve: set = set()   # reopened positions still to reserve (guarded by _lock)
        self._dest_ok: Optional[Tuple[str, float]] = None   # (broker fingerprint, verified at)
        self.state: Dict[str, Any] = self._empty_state()
        self.last_execute: Optional[dict] = None
        self.last_tick: Optional[dict] = None

    # ------------------------------------------------------------------ state
    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {"version": STATE_VERSION, "day": None, "sizing": {}, "baseline": {}, "halt": {},
                "entries_blocked": {}, "risk": {}, "exec_seq": {}, "attempts": {}, "exit_seq": {},
                "positions": {}, "orders": {}, "own": {}, "realized": {}, "exit_requests": {},
                "executions": [], "events": [], "alarms": {},
                # writes whose outcome was still unknown when ADT shut down: resolved at startup
                "unresolved_writes": []}

    def to_state(self) -> Dict[str, Any]:
        with self._lock:
            out = copy.deepcopy(self.state)
            if self._resolving is not None:
                # an operator is removing this unknown-write marker: every save written meanwhile is
                # the state WITHOUT it (the marker stays in memory until that save is durable)
                out["unresolved_writes"] = [m for m in out.get("unresolved_writes") or []
                                            if self._resolving not in (m.get("coid"), m.get("order_id"))]
            return out

    def from_state(self, state: Optional[dict]) -> None:
        """Restore a checkpointed state. Entries stay refused until reconcile_on_startup()."""
        with self._lock:
            if not state:
                self.state = self._empty_state()
            else:
                if state.get("version") != STATE_VERSION:
                    raise ValueError(f"unsupported ORB execution state version {state.get('version')!r}")
                base = self._empty_state()
                base.update(copy.deepcopy(state))
                self.state = base
                self._state_problem = self._validate_rows()
                # a POST recorded in flight by an earlier process can no longer be in flight
                for rec in self.state["orders"].values():
                    if rec.get("submit_state") == "prepared":
                        # an exit prepared before the protection was cancelled but never marked in flight:
                        # its POST provably never happened
                        rec.update(status="not_sent", terminal=True, submit_state="answered",
                                   note="prepared before a restart; never sent")
                    elif rec.get("submit_state") == "in_flight":
                        rec["submit_state"] = "unanswered"
                        rec["restart_recovered"] = True
                for key, pos in self.state["positions"].items():
                    if pos.get("status") == "PENDING_SUBMIT":
                        has_record = any(k == key or r.get("position") == key
                                         for k, r in self.state["orders"].items())
                        if not has_record:
                            # the entry record is saved BEFORE the POST: no record = never sent
                            pos.update(status="SKIPPED", closed_reason="intent only: the order was never sent "
                                       "(the app stopped before it)")
                            for rr in self.state["risk"].get(pos.get("day") or "", []):
                                if rr.get("coid") == pos.get("coid"):
                                    rr["result"] = "not_sent"
                        else:
                            pos["status"] = "UNKNOWN"
            self.ready = False

    def _validate_rows(self) -> Optional[str]:
        """Move malformed position/order rows aside (never used, kept for a human) so a corrupt row can
        never crash ORB's (or ADT's) checks; returns why ORB must stay unready, or None."""
        bad = []
        for bucket, need in (("positions", ("symbol", "status")), ("orders", ("symbol",))):
            rows = self.state.get(bucket)
            if not isinstance(rows, dict):
                self.state[bucket] = {}
                bad.append((bucket, "*", rows))
                continue
            for k in list(rows):
                row = rows[k]
                if not isinstance(row, dict) or any(not isinstance(row.get(f), str) or not row.get(f) for f in need):
                    bad.append((bucket, k, rows.pop(k)))
                    continue
                row["symbol"] = row["symbol"].upper()
                row.setdefault("key", k)
                if bucket == "orders":
                    row.setdefault("terminal", False)
                    row.setdefault("filled_qty", 0)
                    row.setdefault("role", "unknown")
        if bad:
            self.state.setdefault("invalid_rows", []).extend(
                [{"bucket": b, "key": k, "row": r} for b, k, r in bad])
            return f"{len(bad)} malformed ORB state row(s) set aside; a person must check them"
        return None

    def _persist(self) -> None:
        if self.persist_cb is None:
            return
        self.persist_cb(self.to_state())

    def _persist_quiet(self) -> None:
        try:
            self._persist()
        except Exception as exc:  # the next persist retries; never stop an exit for it
            log.error("ORB state persist failed: %s", exc)
            self._alarm("persist_failed", {"error": str(exc)[:200]})

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"ORB mode must be one of {MODES}")
        with self._admission_lock, self._lock:
            self.mode = mode
            self._event({"kind": "mode", "mode": mode})

    # ------------------------------------------------------------------ time
    def _now(self, now: Optional[datetime] = None) -> datetime:
        n = now or self.clock()
        if n.tzinfo is None:
            raise ValueError("ORB clock must return a timezone-aware datetime")
        return n.astimezone(ET)

    @staticmethod
    def _day(now: datetime) -> str:
        return now.astimezone(ET).date().isoformat()

    def _roll_day(self, now: datetime) -> None:
        day = self._day(now)
        with self._lock:
            if self.state.get("day") == day:
                return
            prev = self.state.get("day")
            self.state["day"] = day
            self.state["exit_requests"] = {}
            for pos in self.state["positions"].values():
                if pos.get("status") in ACTIVE and pos.get("day") != day and not pos.get("carried"):
                    pos["carried"] = True
                    self._event({"kind": "carried_position", "symbol": pos["symbol"], "from_day": pos.get("day")})
            # keep closed positions and their terminal orders for 5 days of history only
            cutoff = (date.fromisoformat(day) - timedelta(days=5)).isoformat()
            for k in [k for k, p in self.state["positions"].items()
                      if p.get("status") not in ACTIVE and str(p.get("day") or "") < cutoff]:
                self.state["positions"].pop(k, None)
            live_pos = {k for k, p in self.state["positions"].items()}
            for k in [k for k, r in self.state["orders"].items()
                      if r.get("terminal") and r.get("position") not in live_pos]:
                self.state["orders"].pop(k, None)
            for bucket in ("risk", "realized", "sizing", "baseline"):
                d = self.state[bucket]
                for k in [k for k in d if k < cutoff]:
                    d.pop(k, None)
            if prev is not None:
                self._event({"kind": "day_rollover", "from": prev, "to": day})

    def session_ok(self, now: datetime) -> Tuple[bool, str]:
        """core.session_ok: weekday, FREEZE <= now < FLATTEN, and not past the cutoff."""
        if now.weekday() >= 5:
            return False, "market closed (weekend)"
        fr, fl, co = parse_hms(self.cfg["freeze"]), parse_hms(self.cfg["flatten"]), parse_hms(self.cfg["cutoff"])
        t = now.time()
        if not (fr <= t < fl):
            return False, f"outside the execution window ({fr.strftime('%H:%M')}-{fl.strftime('%H:%M')} ET)"
        if t > co:
            return False, f"past the {co.strftime('%H:%M')} entry cutoff: no new entries permitted"
        return True, ""

    # ------------------------------------------------------------------ misc
    def _sym_lock(self, sym: str) -> threading.Lock:
        with self._sym_guard:
            return self._sym_locks.setdefault(sym.upper(), threading.Lock())

    def _call(self, prio: str, fn: Callable, *a, **kw):
        self.budget.acquire(prio)
        return fn(*a, **kw)

    def _event(self, row: dict) -> None:
        row = dict(row)
        row.setdefault("ts", self._now().isoformat())
        with self._lock:
            ev = self.state["events"]
            ev.append(row)
            if len(ev) > 300:
                del ev[:len(ev) - 300]
        if self.on_event:
            try:
                self.on_event(row)
            except Exception as exc:
                log.warning("ORB on_event hook failed: %s", exc)

    def _alarm(self, key: str, fields: Optional[dict] = None) -> None:
        with self._lock:
            day = self.state.get("day") or ""
            k = f"{day}:{key}"
            first = k not in self.state["alarms"]
            self.state["alarms"][k] = dict(fields or {})
        if first:
            self._event({"kind": "alarm", "alarm": key, **(fields or {})})

    # ------------------------------------------------------------------ own book
    def _find(self, oid: Optional[str] = None, coid: Optional[str] = None) -> Optional[str]:
        orders = self.state["orders"]
        if oid:
            if oid in orders:
                return oid
            for k, r in orders.items():
                if r.get("id") == oid:
                    return k
        if coid:
            if coid in orders:
                return coid
            for k, r in orders.items():
                if r.get("coid") == coid:
                    return k
        return None

    def _new_record(self, key: str, symbol: str, side: str, role: str, *, coid: Optional[str] = None,
                    oid: Optional[str] = None, qty: Optional[int] = None, position: Optional[str] = None,
                    leg_kind: Optional[str] = None) -> dict:
        rec = {"key": key, "id": oid, "coid": coid, "symbol": symbol, "side": side, "role": role,
               "leg_kind": leg_kind, "position": position, "qty": qty, "filled_qty": 0, "avg_price": None,
               "status": "new" if oid else "pending_submit", "terminal": False,
               "created_ts": self._now().timestamp(), "last_refresh": 0.0, "refresh_errs": 0,
               "day": self.state.get("day")}
        if not oid:
            rec["submit_state"] = "in_flight"
        self.state["orders"][key] = rec
        return rec

    def _record_submit(self, coid: str, symbol: str, side: str, qty: int, role: str, position: str) -> str:
        with self._lock:
            key = self._find(coid=coid)
            if key is None:
                key = coid
                self._new_record(key, symbol, side, role, coid=coid, qty=qty, position=position)
            return key

    def _merge(self, order: Any, role_hint: Optional[str] = None, position: Optional[str] = None,
               leg_kind: Optional[str] = None, symbol_hint: Optional[str] = None,
               side_hint: Optional[str] = None) -> Optional[str]:
        """Merge broker order JSON (and nested legs) into the own book. Monotonic: filled_qty
        never decreases and a terminal status never reverts. Unknown orders are only
        registered when the caller vouches for them (role_hint)."""
        if not isinstance(order, dict):
            return None
        with self._lock:
            return self._merge_locked(order, role_hint, position, leg_kind, symbol_hint, side_hint)

    def _merge_locked(self, order, role_hint, position, leg_kind, symbol_hint, side_hint):
        oid = order.get("id") if isinstance(order.get("id"), str) and order.get("id") else None
        coid = order.get("client_order_id") if isinstance(order.get("client_order_id"), str) else None
        key = self._find(oid=oid, coid=coid)
        if key is None:
            if not role_hint or not oid:
                return None
            sym = order.get("symbol") or symbol_hint
            side = order.get("side") or side_hint
            if not isinstance(sym, str) or side not in ("buy", "sell"):
                return None
            if role_hint == "leg" and leg_kind is None:
                leg_kind = "tp" if order.get("type") == "limit" else "sl"
            self._new_record(oid, sym.upper(), side, role_hint, coid=coid, oid=oid,
                             qty=_i(order.get("qty")) or None, position=position, leg_kind=leg_kind)
            key = oid
        rec = self.state["orders"][key]
        if oid and not rec.get("id"):
            rec["id"] = oid
        if _i(order.get("qty")) and (rec.get("qty") is None or rec.get("role") == "exit"):
            rec["qty"] = _i(order.get("qty"))      # an exit's size is what the broker holds, not our note
        status = order.get("status") if isinstance(order.get("status"), str) else None
        new_f = _i(order.get("filled_qty"))
        new_avg = _f(order.get("filled_avg_price"))
        if new_avg is not None and new_avg <= 0:
            new_avg = None
        old_f = int(rec.get("filled_qty") or 0)
        if new_f > old_f:
            if new_avg is None:
                # shares filled but no price yet: take nothing from this read (status included),
                # so the record stays live and the next refresh books the fill with its price
                return key
            delta = new_f - old_f
            old_avg = rec.get("avg_price")
            px = new_avg
            if old_f > 0 and old_avg is not None:
                inc = (new_f * new_avg - old_f * old_avg) / delta
                px = inc if inc > 0 else new_avg
            rec["filled_qty"] = new_f
            rec["avg_price"] = new_avg
            rec["fill_ts"] = self._now().timestamp()
            self._apply_fill(rec, delta, px)
        if rec.get("inferred_terminal") and oid:
            rec["terminal"] = False
            rec.pop("inferred_terminal", None)
            rec["note"] = "revived: the broker answered with this order after a 404"
            if rec.get("role") == "entry":
                self._reopen_position_locked(rec.get("position") or key,
                                             "the broker showed this entry after it was called never-reached")
        if oid and rec.get("submit_state") in ("in_flight", "unanswered"):
            rec["submit_state"] = "answered"
        if status and not rec["terminal"]:
            rec["status"] = status
            rec["terminal"] = status in TERMINAL
        rec["refresh_errs"] = 0
        if rec.get("role") == "entry" and rec.get("id"):
            pos = self.state["positions"].get(rec.get("position") or key)
            if pos is not None and pos.get("status") in ("PENDING_SUBMIT", "UNKNOWN"):
                pos["status"] = "OPEN" if int(rec.get("filled_qty") or 0) > 0 else "SUBMITTED"
                pos["parent_id"] = rec["id"]
                for rr in self.state["risk"].get(pos.get("day") or "", []):
                    if rr["coid"] == pos["coid"] and rr.get("result") in ("unknown", "pending"):
                        rr["result"] = "accepted"
        rb = order.get("replaced_by")
        if isinstance(rb, str) and rb and self._find(oid=rb) is None:
            self._new_record(rb, rec["symbol"], rec["side"], rec.get("role") or "leg", oid=rb,
                             qty=rec.get("qty"), position=rec.get("position"), leg_kind=rec.get("leg_kind"))
        pos_key = rec.get("position") or key
        for leg in order.get("legs") or []:
            if isinstance(leg, dict):
                kind = "tp" if leg.get("type") == "limit" else "sl" if leg.get("type") in ("stop", "stop_limit") else None
                self._merge_locked(leg, "leg", pos_key, kind, rec["symbol"],
                                   "sell" if rec["side"] == "buy" else "buy")
        return key

    def _absorption_fresh(self, ab: dict) -> bool:
        """flow.fresh_read: the read's window ended at most ABSORPTION_MAX_RESULT_AGE_S (8 s) before NOW,
        judged at the moment of action (an earlier exit in the same pass can take seconds)."""
        asof = _parse_ts(ab.get("asof"))
        if asof is None:
            return False
        age = (self._now() - asof).total_seconds()
        return 0 <= age <= self.cfg["absorption_max_result_age_s"]

    def _reopen_position_locked(self, pos_key: str, why: str) -> None:
        """A closed position whose own orders turn out to be live (or to hold shares) is ours again:
        reopen it with its recorded stop/rd so every exit and the 11:00 flatten cover it."""
        pos = self.state["positions"].get(pos_key)
        if pos is None or pos.get("status") in ACTIVE:
            return
        pos.update(status="UNKNOWN", reopened=True, reopened_why=why, closed_reason=None, closed_at=None,
                   exit_reason=None, exit_since=None, escalated=None)
        if pos.get("day") != self.state.get("day"):
            pos["carried"] = True
        for rr in self.state["risk"].get(pos.get("day") or "", []):
            if rr["coid"] == pos["coid"] and rr.get("result") in ("not_found", "rejected", "not_sent"):
                rr["result"] = "unknown"
        self._pending_reserve.add(pos["symbol"])
        self._event({"kind": "position_reopened", "symbol": pos["symbol"], "why": why})

    def _adopt_orphans(self) -> List[str]:
        """Own qty or live own orders on a symbol with no open position (a late fill, a revived
        entry, a lost row): reopen the position they belong to, or rebuild one with no known stop,
        which exits at the next pass (core.adopt_book_positions + ent 'time_exit')."""
        adopted = []
        with self._lock:
            active = {p["symbol"] for p in self._active_positions()}
            for sym in self._own_open_symbols():
                if sym in active:
                    continue
                recs = [r for r in self.state["orders"].values() if r["symbol"] == sym]
                keys = [r.get("position") for r in recs if r.get("position") in self.state["positions"]]
                if keys:
                    key = max(keys, key=lambda k: self.state["positions"][k].get("created_at") or "")
                    self._reopen_position_locked(key, "own shares or live own orders with no open position")
                else:
                    entry = next((r for r in recs if r.get("role") == "entry"), None) or (recs[0] if recs else None)
                    q = int((self.state["own"].get(sym) or {}).get("qty") or 0)
                    side = entry["side"] if entry else ("buy" if q > 0 else "sell")
                    key = f"rebuilt:{sym}:{self.state.get('day')}"
                    self.state["positions"][key] = {
                        "key": key, "coid": (entry or {}).get("coid") or key, "symbol": sym,
                        "direction": "long" if side == "buy" else "short", "tier": "quant",
                        "day": self.state.get("day"), "planned_shares": abs(q), "shares": abs(q),
                        "initial_shares": abs(q), "bracket_qty": abs(q),
                        "entry_ref": (self.state["own"].get(sym) or {}).get("avg") or 0.0,
                        "stop": None, "rd": 0.0, "target": None, "risk_usd": 0.0,
                        "created_at": self._now().isoformat(), "status": "UNKNOWN", "peak": 0.0,
                        "no_known_stop": True, "errs": 0, "carried": False}
                    for r in recs:
                        if not r.get("position"):
                            r["position"] = key
                    self._pending_reserve.add(sym)
                    self._event({"kind": "position_rebuilt", "symbol": sym,
                                 "note": "own orders/shares with no position record: exits at the next pass"})
                adopted.append(sym)
        with self._lock:
            pending = sorted(self._pending_reserve)
        for sym in pending:
            if self._take(sym):
                with self._lock:
                    self._pending_reserve.discard(sym)
            else:
                self._alarm(f"reservation_conflict_{sym}", {"symbol": sym,
                            "note": "ORB supervises its own shares here but could not reserve the symbol"})
        return adopted

    def _apply_fill(self, rec: dict, delta: int, px: float) -> None:
        sym, side = rec["symbol"], rec["side"]
        own = self.state["own"].setdefault(sym, {"qty": 0, "avg": None})
        new_q, avg, pnl, unknown = _fill_math(int(own["qty"]), own.get("avg"), side, delta, px)
        own["qty"], own["avg"] = new_q, (round(avg, 6) if avg is not None else None)
        day = self.state.get("day") or self._day(self._now())
        cell = self.state["realized"].setdefault(day, {"pnl": 0.0, "unknown": False})
        cell["pnl"] = round(cell["pnl"] + pnl, 6)
        cell["unknown"] = bool(cell["unknown"] or unknown)
        role = rec.get("role")
        name = ("target" if rec.get("leg_kind") == "tp" else "stop") if role == "leg" else role
        pos = self.state["positions"].get(rec.get("position") or "")
        if pos is not None and role == "entry" and pos.get("status") in ("SUBMITTED", "UNKNOWN", "PENDING_SUBMIT"):
            pos["status"] = "OPEN"
        self._event({"kind": "own_fill", "symbol": sym, "side": side, "qty": delta, "price": px,
                     "order_id": rec.get("id"), "role": name})
        if self.on_fill:
            try:
                self.on_fill(sym, side, delta, px, rec.get("id"), name)
            except Exception as exc:
                log.error("ORB on_fill hook failed for %s: %s", sym, exc)
                self._alarm(f"on_fill_failed_{rec.get('id')}", {"symbol": sym, "error": str(exc)[:200]})

    def _mark_not_found(self, key: str, confirmed: bool = False) -> bool:
        """ownership.OwnBook.mark_not_found: a 404 only proves a submit never reached the broker
        once nothing can still be carrying it (not in flight, past the grace, or the submitter's
        own definitive refusal)."""
        with self._lock:
            rec0 = dict(self.state["orders"].get(key) or {})
        if rec0 and not confirmed and rec0.get("role") == "entry" and not rec0.get("terminal") and self.broker is not None:
            # an entry the broker may hold under a lookup that keeps failing: never declared never-sent
            # while the account holds shares on that side
            try:
                net = int(self._call("exit", self.broker.position_qty, rec0["symbol"]))
            except Exception:
                return False
            if net and (net > 0) == (rec0.get("side") == "buy"):
                with self._lock:
                    r = self.state["orders"].get(key)
                    if r is not None:
                        r["note"] = "client id lookup finds nothing but the account holds the shares: kept"
                return False
        with self._lock:
            rec = self.state["orders"].get(key)
            if rec is None or rec["terminal"] or int(rec.get("filled_qty") or 0) > 0:
                return False
            if rec.get("status") == "pending_submit" and not confirmed:
                if rec.get("submit_state") == "in_flight":
                    return False
                if self._now().timestamp() - float(rec.get("created_ts") or 0) < PENDING_GRACE_S:
                    return False
            rec.update(status="not_found", terminal=True, inferred_terminal=True,
                       note="never reached the broker (no order with our client order id)")
            return True

    def own_qty(self, sym: str) -> int:
        with self._lock:
            return int((self.state["own"].get(sym.upper()) or {}).get("qty") or 0)

    def own_avg(self, sym: str) -> Optional[float]:
        with self._lock:
            return (self.state["own"].get(sym.upper()) or {}).get("avg")

    def _records(self, sym: Optional[str] = None, position: Optional[str] = None) -> List[Tuple[str, dict]]:
        with self._lock:
            return [(k, dict(r)) for k, r in self.state["orders"].items()
                    if (sym is None or r["symbol"] == sym) and (position is None or r.get("position") == position)]

    def _live(self, sym: Optional[str] = None) -> List[Tuple[str, dict]]:
        return [(k, r) for k, r in self._records(sym) if not r["terminal"]]

    def _flat_proven(self, pos: dict) -> bool:
        recs = self._records(pos["symbol"])
        mine = [r for _k, r in recs if r.get("position") == pos["key"]]
        if not mine or any(not r["terminal"] for _k, r in recs):
            return False
        return self.own_qty(pos["symbol"]) == 0

    # ------------------------------------------------------------------ positions / capacity
    def _active_positions(self) -> List[dict]:
        with self._lock:
            return [p for p in self.state["positions"].values() if p.get("status") in ACTIVE]

    def _position_for(self, sym: str) -> Optional[dict]:
        sym = sym.upper()
        for p in self._active_positions():
            if p["symbol"] == sym:
                return p
        return None

    def owns(self, symbol: str) -> bool:
        """True while ORB holds, has a working order on, or has an unresolved submit for symbol."""
        sym = symbol.upper()
        return self._position_for(sym) is not None or self.own_qty(sym) != 0 or bool(self._live(sym))

    def reserved_symbols(self) -> List[str]:
        return sorted({p["symbol"] for p in self._active_positions()})

    def needs_supervision(self) -> bool:
        with self._lock:
            return (bool(self._active_positions()) or any(not r["terminal"] for r in self.state["orders"].values())
                    or bool(self._own_open_symbols()) or bool(self._recheck_candidates()))

    def _recheck_candidates(self) -> List[str]:
        """Entries we only INFERRED never reached the broker (today or yesterday): asked again by coid
        at most every RECHECK_NOT_FOUND_S, so a late appearance is still discovered."""
        day = self.state.get("day") or ""
        floor = (date.fromisoformat(day) - timedelta(days=1)).isoformat() if day else ""
        return [k for k, r in self.state["orders"].items()
                if r.get("inferred_terminal") and r.get("role") == "entry" and str(r.get("day") or "") >= floor]

    def slots_available(self) -> int:
        return max(0, self.cfg["max_open_slots"] - len(self._active_positions()))

    def active_structure_count(self) -> int:
        return sum(1 for p in self._active_positions() if p.get("tier") == "structure")

    def day_risk_used(self, day: Optional[str] = None) -> float:
        """core.cumulative_day_risk: risk_$ of every accepted order today; never given back."""
        d = day or self.state.get("day") or self._day(self._now())
        with self._lock:
            rows = self.state["risk"].get(d) or []
            return round(sum(float(r["risk_usd"]) for r in rows
                             if r.get("result") in ("accepted", "unknown", "pending", "shadow")), 2)

    def max_day_risk(self, equity: Optional[float]) -> float:
        if not equity or equity <= 0:
            return 0.0
        return round(equity * self.cfg["max_day_risk_frac"], 2)

    def max_gross_cap(self, equity: Optional[float]) -> float:
        if not equity or equity <= 0:
            return 0.0
        return round(equity * self.cfg["max_gross_mult"], 2)

    def active_gross_exposure(self) -> float:
        return round(sum(abs(float(p.get("shares") or 0) * float(p.get("entry_ref") or 0))
                         for p in self._active_positions()), 2)

    def capacity_reached(self, equity: Optional[float] = None) -> bool:
        """core.session_capacity_reached: slots full, or today's risk budget spent."""
        if self.slots_available() <= 0:
            return True
        eq = equity
        if eq is None:
            snap = self.session_sizing()
            eq = float(snap["equity"]) if snap else None
            if eq is None and (self.broker is None and self.mode == "shadow"):
                eq = self.cfg["shadow_equity"]
        if eq is not None and eq > 0 and self.day_risk_used() >= self.max_day_risk(eq):
            return True
        return False

    def symbols_today(self) -> List[str]:
        """Symbols ORB supervised or executed today (live or shadow): the secondary scan skips them."""
        with self._lock:
            day = self.state.get("day")
            # scanner.run_secondary: supervised now, or in any of today's execution rows (placed picks)
            out = {p["symbol"] for p in self.state["positions"].values()
                   if p.get("status") in ACTIVE or p.get("day") == day}
            return sorted(out)

    def halted(self) -> Optional[str]:
        with self._lock:
            h = self.state["halt"]
            return h.get("reason") if h.get("day") == self.state.get("day") else None

    def _latch_halt(self, reason: str, pct: Optional[float]) -> None:
        with self._admission_lock, self._lock:
            if self.halted():
                return
            self.state["halt"] = {"day": self.state.get("day"), "reason": reason, "pct": pct,
                                  "at": self._now().isoformat()}
        self._event({"kind": "halt", "reason": reason, "own_pnl_pct": pct})

    def entries_blocked(self) -> Optional[str]:
        with self._lock:
            b = self.state["entries_blocked"]
            return b.get("reason") if b.get("day") == self.state.get("day") else None

    # ------------------------------------------------------------------ sizing freeze
    def session_sizing(self, day: Optional[str] = None) -> Optional[dict]:
        d = day or self.state.get("day") or self._day(self._now())
        with self._lock:
            row = (self.state["sizing"] or {}).get(d)
        if not isinstance(row, dict):
            return None
        eq, bp = _f(row.get("equity")), _f(row.get("buying_power"))
        if not (eq and eq > 0 and bp and bp > 0):
            return None
        return dict(row)

    def _persisted_baseline(self, acct: dict, day: str) -> Tuple[Optional[float], Optional[str]]:
        row = (self.state["baseline"] or {}).get(day)
        if not isinstance(row, dict):
            return None, None
        num = str(acct.get("account_number") or "")
        if not num or row.get("account_number") != num:
            return None, None
        cap = _parse_ts(row.get("captured_at"))
        if cap is None:
            return None, None
        cap = cap.astimezone(ET)
        if cap.date().isoformat() != day or not time(9, 15) <= cap.time() < time(9, 30):
            return None, None
        base = _f(row.get("baseline_equity"))
        if not base or base <= 0 or row.get("source") not in ("broker_last_equity", "preopen_paper_equity"):
            return None, None
        return base, row["source"]

    def account_day_equity_baseline(self, acct: dict, now: Optional[datetime] = None) -> Tuple[Optional[float], Optional[str]]:
        """core.account_day_equity_baseline: Alpaca last_equity, else today's verified pre-open paper equity."""
        if not isinstance(acct, dict) or acct.get("equity_estimated"):
            return None, None
        last_eq = _f(acct.get("last_equity"))
        if last_eq and last_eq > 0:
            return last_eq, "broker_last_equity"
        return self._persisted_baseline(acct, self._day(self._now(now)))

    def capture_day_equity_baseline(self, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """core.capture_day_equity_baseline (paper): only 09:15-09:29, only an activity-free account."""
        now = self._now(now)
        if self.broker is None:
            return False, "no broker"
        if now.weekday() >= 5 or not time(9, 15) <= now.time() < time(9, 30):
            return False, "equity baseline must be captured from 09:15 through 09:29 ET"
        day = self._day(now)
        try:
            acct = self._call("normal", self.broker.get_account_checked)
            if not isinstance(acct, dict) or acct.get("equity_estimated"):
                return False, "account equity is unavailable or estimated"
            num = str(acct.get("account_number") or "")
            why = self._destination_refused(acct)
            if why:
                return False, f"destination refused: {why}"
            if acct.get("trading_blocked") or acct.get("account_blocked"):
                return False, "the account is blocked"
            equity = _f(acct.get("equity"))
            if not equity or equity <= 0:
                return False, "account equity must be finite and positive"
            saved, src = self._persisted_baseline(acct, day)
            if saved is not None:
                return True, f"{src} baseline ${saved:,.2f} already verified"
            last_eq = _f(acct.get("last_equity")) or 0.0
            if last_eq > 0:
                baseline, source = last_eq, "broker_last_equity"
            else:
                if self.state["risk"].get(day):
                    return False, "local execution activity already exists today"
                positions = self._call("normal", self.broker.get_positions_raw)
                open_orders = self._call("normal", self.broker.list_orders, "open", None, None, 200)
                start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                closed = self._call("normal", self.broker.list_orders, "closed", None, start, 1)
                if positions or open_orders or closed:
                    return False, "account is not activity free before the open"
                baseline, source = equity, "preopen_paper_equity"
            cap = self._now()
            if cap.date() != now.date() or not time(9, 15) <= cap.time() < time(9, 30):
                return False, "equity baseline check did not finish before 09:30 ET"
            with self._lock:
                self.state["baseline"][day] = {"day": day, "account_number": num, "baseline_equity": baseline,
                                               "source": source, "captured_at": cap.isoformat(),
                                               "observed_equity": equity, "observed_last_equity": last_eq}
            self._persist_quiet()
            return True, f"{source} baseline ${baseline:,.2f} verified for {num}"
        except Exception as exc:
            return False, f"equity baseline check failed: {str(exc)[:160]}"

    def capture_session_sizing(self, now: Optional[datetime] = None, late: bool = False,
                               acct: Optional[dict] = None) -> Tuple[bool, str]:
        """core.capture_session_sizing: freeze day-start equity + buying power once a day."""
        now = self._now(now)
        self._roll_day(now)
        if not late and (now.weekday() >= 5 or not time(9, 15) <= now.time() < time(9, 30)):
            return False, "session sizing is captured from 09:15 through 09:29 ET"
        if self.session_sizing():
            return True, "session sizing already frozen for today"
        if self.broker is None:
            return False, "no broker to read the account from"
        try:
            if acct is None:
                acct = self._call("normal", self.broker.get_account_checked)
            why = self._destination_refused(acct)
            if why:
                return False, f"destination refused: {why}"
            baseline, source = self.account_day_equity_baseline(acct, now)
            bp = _f(acct.get("buying_power")) or 0.0
            if baseline is None:
                return False, "no verified day-start equity baseline yet"
            if bp <= 0:
                return False, "buying power is unreadable"
            day = self._day(now)
            row = {"day": day, "equity": float(baseline), "equity_source": source, "buying_power": float(bp),
                   "captured_at": now.isoformat(), "late": bool(late),
                   "account_number": str(acct.get("account_number") or "")}
            with self._lock:
                self.state["sizing"][day] = row
            self._event({"kind": "session_sizing", **row})
            self._persist_quiet()
            return True, (f"sizing frozen: equity ${baseline:,.2f}, buying power ${bp:,.2f}"
                          + (" (LATE)" if late else ""))
        except Exception as exc:
            return False, f"session sizing capture failed: {str(exc)[:160]}"

    def freeze_session(self, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """The scheduler's 09:15-09:29 step: pre-open baseline if Alpaca has no last_equity,
        then the sizing freeze. Idempotent."""
        now = self._now(now)
        ok, detail = self.capture_session_sizing(now)
        if ok:
            return ok, detail
        ok_b, detail_b = self.capture_day_equity_baseline(now)
        if ok_b:
            return self.capture_session_sizing(now)
        return False, f"{detail}; {detail_b}"

    # ------------------------------------------------------------------ own P&L / halt
    def own_pnl_state(self, prices: Optional[Dict[str, float]] = None) -> dict:
        """core.own_pnl_state: realized (own fills) + unrealized (own qty x (price - own avg))."""
        day = self.state.get("day") or self._day(self._now())
        with self._lock:
            cell = self.state["realized"].get(day) or {}
            realized, realized_unknown = float(cell.get("pnl") or 0.0), bool(cell.get("unknown"))
            own = {s: dict(v) for s, v in self.state["own"].items() if int(v.get("qty") or 0) != 0}
        reasons = ["an own fill had no price"] if realized_unknown else []
        unreal, known = 0.0, True
        band = self.cfg["own_price_sanity_pct"] / 100.0
        for sym, o in sorted(own.items()):
            q, avg = int(o["qty"]), o.get("avg")
            px = (prices or {}).get(sym)
            if avg is None or avg <= 0:
                known = False; reasons.append(f"{sym}: own average price unknown"); continue
            if px is None or not math.isfinite(px) or px <= 0:
                known = False; reasons.append(f"{sym}: no fresh price"); continue
            if abs(px - avg) / avg > band:
                known = False; reasons.append(f"{sym}: price {px} is outside the sane band vs own avg {avg}"); continue
            unreal += q * (px - avg)
        snap = self.session_sizing(day)
        base = float(snap["equity"]) if snap else None
        out = {"realized": realized, "realized_known": not realized_unknown,
               "unrealized": unreal if known else None, "unrealized_known": known, "baseline": base,
               "pct_realized": None, "pct_total": None, "reasons": reasons}
        if base and base > 0:
            out["pct_realized"] = realized / base * 100.0
            if known:
                out["pct_total"] = (realized + unreal) / base * 100.0
        return out

    def own_halt_breached(self, st: dict) -> Tuple[bool, Optional[float]]:
        lim = -self.cfg["daily_loss_halt_pct"]
        v = st.get("pct_total") if st.get("unrealized_known") else st.get("pct_realized")
        if v is not None and v <= lim:
            return True, v
        return False, None

    def _account_halt_reason(self) -> Optional[str]:
        try:
            r = self.account_halt()
        except Exception as exc:
            return f"ADT account loss stop could not be read ({exc})"
        if not r:
            return None
        return r if isinstance(r, str) else "ADT account daily loss stop"

    def account_refusal(self, acct: Any) -> Optional[str]:
        """core.verify_destination: None when acct is the pinned account and not blocked, else why not.
        No pinned account is itself a refusal (fail closed)."""
        if not self.expected_account:
            return "no expected Alpaca account is configured: name the account before ORB may trade it"
        num = str((acct or {}).get("account_number") or "") if isinstance(acct, dict) else ""
        if num != self.expected_account:
            return f"Alpaca account {num or '?'!r} is not ORB's account {self.expected_account!r}"
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            return "the broker has this account blocked"
        return None

    def _destination_refused(self, acct: Any) -> Optional[str]:
        """Judge an account read; a pass refreshes the write cache, a failure clears it and alarms."""
        why = self.account_refusal(acct)
        with self._lock:
            self._dest_ok = None if why else (self._broker_fingerprint(), self._now().timestamp())
        if why:
            self._alarm("wrong_account", {"reason": why})
        return why

    def _broker_fingerprint(self) -> str:
        """Which credentials and endpoint a write would use: a change forces a fresh account check."""
        c = getattr(self.broker, "_client", None)
        key = secret = base = ""
        try:
            if c is not None:
                key = str(c.headers.get("APCA-API-KEY-ID") or "")
                secret = str(c.headers.get("APCA-API-SECRET-KEY") or "")
                base = str(getattr(c, "base_url", ""))
        except Exception:
            pass
        # one-way digest: the raw key id / secret are never stored or logged
        digest = hashlib.sha256("\x00".join((base, key, secret)).encode()).hexdigest()
        return f"{id(self.broker)}|{digest}"

    def _verify_destination_for_write(self) -> None:
        """Every broker write (entry, cancel, PATCH, exit) goes to the pinned account only. A pass is
        cached for DEST_TTL_S with the same credentials, so exits are not slowed by account reads."""
        with self._lock:
            ok = self._dest_ok
        now_ts = self._now().timestamp()
        if ok and ok[0] == self._broker_fingerprint() and 0 <= now_ts - ok[1] < DEST_TTL_S:
            return
        try:
            acct = self._call("exit", self.broker.get_account_checked)
        except Exception as exc:
            raise DestinationRefused(f"the account could not be verified before a broker write: {exc}")
        why = self._destination_refused(acct)
        if why:
            raise DestinationRefused(f"destination refused: {why}")

    def close_entries(self, why: str = "ADT is shutting down") -> None:
        """No new entry POST from now on (an entry refused here was never sent)."""
        self._entries_closed = why

    def close_writes(self, why: str = "ADT is shutting down", wait_s: float = 30.0) -> bool:
        """No broker write of any kind from now on. Returns once every write that already passed the
        fence has returned (bounded by wait_s; False if one is still in flight)."""
        with self._write_gate:
            self._entries_closed = self._entries_closed or why
            self._writes_closed = why
        return self.wait_writes(wait_s)

    def wait_writes(self, wait_s: float) -> bool:
        """Wait (bounded) until no broker write is in flight. True when none is."""
        with self._write_gate:
            deadline = _time.monotonic() + wait_s
            while self._writes_in_flight > 0:
                left = deadline - _time.monotonic()
                if left <= 0:
                    return False
                self._write_gate.wait(timeout=left)
        return True

    def inflight_writes(self) -> List[dict]:
        with self._write_gate:
            return [dict(v) for v in self._inflight_writes.values()]

    def resolve_unknown_write(self, ref: str, note: str) -> Optional[dict]:
        """Operator: drop one unresolved-write marker (by client id or order id) after checking Alpaca by
        hand. No lock is held during the save, so a stalled save never stops supervision or exits:
          1. under _lock: ready=False and `resolving` set (reconciliation neither sets ready nor clears
             this marker meanwhile);
          2. save the state without the marker (to_state omits it while resolving);
          3. under _lock: success = marker removed, the next reconciliation decides ready;
             failure = marker kept, ready stays False, retries continue (and the error is raised).
        Returns the removed marker, or None if there is no such marker."""
        with self._lock:
            if self._resolving is not None:
                raise RuntimeError("another unknown-write resolution is in progress")
            hit = next((m for m in self.state.get("unresolved_writes") or []
                        if ref in (m.get("coid"), m.get("order_id"))), None)
            if hit is None:
                return None
            self._resolving = ref
            self.ready = False
        try:
            self._persist()
        except Exception:
            with self._lock:
                self._resolving = None
                self.ready = False
            self._persist_quiet()            # the durable state gets the marker back (best effort)
            raise
        with self._lock:
            self.state["unresolved_writes"] = [m for m in self.state.get("unresolved_writes") or []
                                               if ref not in (m.get("coid"), m.get("order_id"))]
            self._resolving = None
        self._event({"kind": "unresolved_write_resolved_by_operator", "write": hit, "note": note})
        return hit

    def mark_unresolved_writes(self) -> List[dict]:
        """Shutdown with a write still in flight: record what it was (client id / order id) durably, so
        the next startup reconciliation resolves its outcome at Alpaca before any entry."""
        rows = self.inflight_writes()
        if rows:
            with self._lock:
                self.state.setdefault("unresolved_writes", []).extend(rows)
            self._event({"kind": "unresolved_writes_at_shutdown", "writes": rows})
            self._persist_quiet()
        return rows

    def _acquire_write_slot(self, entry: bool = False, what: Optional[dict] = None) -> int:
        with self._write_gate:
            if self._writes_closed:
                raise DestinationRefused(f"{self._writes_closed}: no broker writes")
            if entry and self._entries_closed:
                raise DestinationRefused(f"{self._entries_closed}: no new entries")
            self._writes_in_flight += 1
            self._slot_seq += 1
            self._inflight_writes[self._slot_seq] = dict(what or {}, at=self._now().isoformat())
            return self._slot_seq

    def _release_write_slot(self, slot: Optional[int] = None) -> None:
        with self._write_gate:
            self._writes_in_flight -= 1
            if slot is not None:
                self._inflight_writes.pop(slot, None)
            self._write_gate.notify_all()

    @staticmethod
    def _describe_write(fn: Callable, a: tuple, kw: dict) -> dict:
        name = getattr(fn, "__name__", str(fn))
        if name == "submit_bracket":
            return {"kind": name, "symbol": a[0] if a else None, "coid": a[5] if len(a) > 5 else kw.get("client_order_id")}
        if name == "submit_market_order":
            return {"kind": name, "symbol": a[0] if a else None, "coid": a[3] if len(a) > 3 else kw.get("client_order_id")}
        return {"kind": name, "order_id": a[0] if a else kw.get("order_id")}

    def _write(self, prio: str, fn: Callable, *a, _pretaken: bool = False, **kw):
        """A broker WRITE: pinned account verified first, then the budget token (unless already held).
        The call itself runs inside a write slot (see close_writes)."""
        entry = self.broker is not None and fn == getattr(self.broker, "submit_bracket", None)
        if self._writes_closed or (entry and self._entries_closed):
            raise DestinationRefused(f"{self._writes_closed or self._entries_closed}: no broker writes")
        self._verify_destination_for_write()
        if not _pretaken:
            self.budget.acquire(prio)
        slot = self._acquire_write_slot(entry, self._describe_write(fn, a, kw))
        try:
            return fn(*a, **kw)
        finally:
            self._release_write_slot(slot)

    def _prices_from_positions(self) -> Optional[Dict[str, float]]:
        if self.broker is None:
            return {}
        try:
            rows = self._call("normal", self.broker.get_positions_raw)
        except Exception:
            return None
        out, qtys = {}, {}
        for p in rows:
            px = _f(p.get("current_price")) if isinstance(p, dict) else None
            if isinstance(p, dict) and p.get("symbol") and p.get("qty") not in (None, ""):
                qtys[str(p["symbol"]).upper()] = _i(p.get("qty"))
            if px and px > 0 and p.get("symbol"):
                out[str(p["symbol"]).upper()] = px
        self._last_position_qtys = qtys
        return out

    # ------------------------------------------------------------------ fresh price
    def fresh_price(self, sym: str, now: datetime) -> float:
        """core._fresh_price: latest SIP trade, positive, print age between -5 and +60 s."""
        lt = self.facade.latest_trade(sym)
        if not isinstance(lt, dict):
            raise RuntimeError("no usable last price")
        px = _f(lt.get("price") if "price" in lt else lt.get("p"))
        if px is None or px <= 0:
            raise RuntimeError("no usable last price")
        stamp = _parse_ts(lt.get("ts") if "ts" in lt else lt.get("t"))
        if stamp is None:
            raise RuntimeError("last price has no valid timestamp")
        age = (now - stamp).total_seconds()
        if not -5 <= age <= 60:
            raise RuntimeError(f"last print age {age:.1f}s is stale or in the future")
        return px

    # ------------------------------------------------------------------ entries
    def _next(self, bucket: str, key: str) -> int:
        with self._lock:
            d = self.state[bucket]
            d[key] = int(d.get(key) or 0) + 1
            return d[key]

    def _coid(self, sym: str, day: str) -> Tuple[str, int, int]:
        seq = self._next("exec_seq", f"{day}|{sym}")
        att = self._next("attempts", f"{day}|{sym}")
        coid = f"{self.cfg['coid_prefix']}-{sym}-{day}-w{seq}-a{att}-{secrets.token_hex(3)}"
        return coid[:128], seq, att

    def _refuse(self, reason: str, **kw) -> dict:
        out = _res(False, reason, **kw)
        self.last_execute = out
        return out

    def execute(self, picks: List[dict], now: Optional[datetime] = None, strict: bool = True,
                wave: Optional[str] = None) -> dict:
        """core.execute for equity brackets. strict=True (autopilot): a pick that cannot be freshly
        priced aborts the WHOLE batch. Returns ORBStraddle's result shape."""
        now = self._now(now)
        with self._exec_lock:
            self._roll_day(now)
            res = self._execute_locked(list(picks or []), now, strict, wave)
            self.last_execute = res
            with self._lock:
                ex = self.state["executions"]
                ex.append({"ts": now.isoformat(), "wave": wave, "ok": res.get("ok"), "reason": res.get("reason"),
                           "accepted": res.get("accepted", 0), "refused": res.get("refused", []),
                           "placed": [{"symbol": p.get("symbol"), "coid": p.get("coid"), "shares": p.get("shares"),
                                       "risk_usd": p.get("risk_usd"), "result": p.get("outcome")}
                                      for p in res.get("placed", [])]})
                if len(ex) > 50:
                    del ex[:len(ex) - 50]
            self._persist_quiet()
            return res

    def _execute_locked(self, picks: List[dict], now: datetime, strict: bool, wave: Optional[str]) -> dict:
        day = self._day(now)
        live = self.mode == "live"
        if self.mode == "off":
            return _res(False, "ORB is off: no new entries")
        if self._entries_closed:
            return _res(False, f"{self._entries_closed}: no new entries")
        if not self.ready:
            return _res(False, "startup reconciliation has not finished: no new entries until it does")
        if self.capacity_reached():
            return _res(False, "already executed today (session capacity reached)")
        h = self.halted()
        if h:
            return _res(False, f"Daily loss halt active ({h}): no new entries allowed today")
        ah = self._account_halt_reason()
        if ah:
            return _res(False, f"{ah}: no new entries")
        avail_slots = self.slots_available()
        if avail_slots <= 0:
            return _res(False, f"all {self.cfg['max_open_slots']} slots occupied", refused=[])
        blk = self.entries_blocked()
        if blk:
            return _res(False, f"ORB positions were flattened today ({blk}): no new entries until tomorrow")
        ok, why = self.session_ok(now)
        if not ok:
            return _res(False, why)

        equity: float = self.cfg["shadow_equity"]
        live_bp: Optional[float] = None
        snap: Optional[dict] = None
        acct: dict = {}
        if self.broker is not None:
            try:
                acct = self._call("normal", self.broker.get_account_checked)
                if acct.get("equity_estimated"):
                    raise ValueError("account valuation uses missing-price estimates")
                eq_now = _f(acct.get("equity"))
                if not eq_now or eq_now <= 0:
                    raise ValueError("equity must be finite and positive")
            except Exception as exc:
                return _res(False, f"account read failed: {exc}")
            why = self._destination_refused(acct)
            if why:
                return _res(False, f"destination refused: {why}")
            snap = self.session_sizing(day)
            base_now, _src = self.account_day_equity_baseline(acct, now)
            if snap and base_now is not None and abs(float(snap["equity"]) - float(base_now)) > 0.01:
                self._event({"kind": "session_sizing_mismatch", "frozen": snap.get("equity"),
                             "baseline_now": base_now, "note": "day-start equity changed; re-freezing"})
                with self._lock:
                    self.state["sizing"].pop(day, None)
                snap = None
            if snap is None:
                _okc, detail = self.capture_session_sizing(now, late=True, acct=acct)
                snap = self.session_sizing(day)
                if snap is None:
                    return _res(False, f"cannot freeze today's sizing inputs ({detail}): refusing to size blind")
            equity = float(snap["equity"])
            live_bp = _f(acct.get("buying_power"))
            prices = self._prices_from_positions() if any(
                int(v.get("qty") or 0) != 0 for v in self.state["own"].values()) else {}
            st = self.own_pnl_state(prices or {})
            breached, pct = self.own_halt_breached(st)
            if breached:
                self._latch_halt(f"ORB's own P&L is {pct:.2f}% today (halt at -{self.cfg['daily_loss_halt_pct']}%)", pct)
                return _res(False, f"ORB's own P&L is {pct:.2f}% today (halt at -{self.cfg['daily_loss_halt_pct']}%)")
            if st["baseline"] is None:
                return _res(False, "cannot establish today's day-start equity: refusing to trade blind")
            if not (st["realized_known"] and st["unrealized_known"]):
                return _res(False, "ORB's own P&L is unknown (" + "; ".join(st["reasons"][:3]) + "): no new entries")
        elif live:
            return _res(False, "no broker configured: live mode cannot trade")

        plan: List[dict] = []
        refused: List[Tuple[str, str]] = []
        prior_risk = self.day_risk_used(day)
        risk_used = prior_risk
        prior_gross = self.active_gross_exposure()
        gross_used = prior_gross
        max_day = self.max_day_risk(equity)
        max_gross = self.max_gross_cap(equity)
        if self.broker is not None:
            bp = _f((snap or {}).get("buying_power")) or 0.0
            if bp <= 0 or live_bp is None or live_bp <= 0:
                return _res(False, "cannot read the account's buying power: refusing to size blind")
            max_gross = min(max_gross, bp * self.cfg["max_buying_power_pct"] / 100.0)
        reserved: List[str] = []

        def unreserve_all():
            if live:
                for s in reserved:
                    self._safe_release(s)

        for p in picks:
            sym = str(p.get("symbol") or "").upper()
            card = p.get("card") if isinstance(p.get("card"), dict) else p
            direction = p.get("direction") or card.get("direction")
            L = direction == "long"
            if not sym or direction not in ("long", "short"):
                refused.append((sym or "?", "malformed pick")); continue
            if sym in self.cfg["excluded_symbols"]:
                refused.append((sym, "excluded from ORB (another ADT plan trades it)")); continue
            if self.owns(sym) or any(pl["symbol"] == sym for pl in plan):
                self._event({"kind": "skip_occupied", "symbol": sym})
                refused.append((sym, "already held by this account")); continue
            if sym in set(self.symbols_today()):
                # never trust only the facade's filter: one execution per symbol per day
                self._event({"kind": "skip_executed_today", "symbol": sym})
                refused.append((sym, "already executed today")); continue
            try:
                occ = bool(self.is_occupied(sym))
            except Exception as exc:
                occ = True
                self._event({"kind": "occupied_check_failed", "symbol": sym, "error": str(exc)[:160]})
            if occ:
                self._event({"kind": "skip_occupied", "symbol": sym})
                refused.append((sym, "already held by this account")); continue
            if len(plan) >= avail_slots:
                refused.append((sym, f"slot limit reached ({avail_slots} slots available)")); continue
            tier = p.get("tier") or card.get("tier") or "structure"
            if tier == "structure":
                plan_struct = sum(1 for pl in plan if pl.get("tier") == "structure")
                if self.active_structure_count() + plan_struct >= self.cfg["max_structure_picks"]:
                    refused.append((sym, f"max structure tier slots ({self.cfg['max_structure_picks']}) occupied"))
                    continue
            try:
                ok_r, why_r = self.facade.recheck(card, now)
            except Exception as exc:
                ok_r, why_r = False, f"re-check failed: {exc}"
            if not ok_r:
                self._event({"kind": "skip_recheck", "symbol": sym, "why": why_r})
                refused.append((sym, str(why_r))); continue
            try:
                px = self.fresh_price(sym, now)
            except Exception as exc:
                if strict:
                    unreserve_all()
                    return _res(False, f"could not price {sym} ({exc}): refusing the whole batch",
                                refused=refused + [(sym, f"no fresh price: {exc}")])
                refused.append((sym, f"no fresh price: {exc}")); continue
            c_entry, c_stop = _f(card.get("entry")), _f(card.get("stop"))
            if not c_entry or not c_stop:
                refused.append((sym, "card has no entry/stop")); continue
            slip = abs(px - c_entry) / c_entry * 100.0
            rd0 = abs(c_entry - c_stop)
            slip_frac = (abs(px - c_entry) / rd0) if rd0 > 0 else float("inf")
            if slip_frac > self.cfg["max_slip_stop_frac"]:
                refused.append((sym, f"moved {slip:.2f}% = {slip_frac:.0%} of the stop distance from the "
                                     f"{c_entry} trigger (cap {self.cfg['max_slip_stop_frac']:.0%})"))
                continue
            stop = round(c_stop, 2)
            if (L and stop >= px) or (not L and stop <= px):
                refused.append((sym, f"price {px} is through the {stop} stop")); continue
            rd = abs(px - stop)
            if rd < px * self.cfg["min_stop_pct"] / 100.0:
                refused.append((sym, "stop is closer than 0.5%")); continue
            risk = equity * self.cfg["risk_pct"] / 100.0
            shares = int(risk // rd)
            if shares < 1:
                refused.append((sym, "sizes to zero shares")); continue
            notional_cap = equity * self.cfg["max_name_notional_pct"] / 100.0
            shares = min(shares, int(notional_cap // px))
            if shares < 1:
                refused.append((sym, "one share exceeds the notional cap")); continue
            if risk_used + shares * rd > max_day:
                shares = int((max_day - risk_used) // rd)
                if shares < 1:
                    refused.append((sym, "day risk budget spent")); continue
            if gross_used + shares * px > max_gross:
                shares = int((max_gross - gross_used) // px)
                if shares < 1:
                    refused.append((sym, "gross exposure budget spent")); continue
            if live_bp is not None:
                live_left = live_bp - (gross_used - prior_gross)
                if shares * px > live_left:
                    refused.append((sym, f"not funded: live buying power ${max(0.0, live_left):,.0f} cannot cover "
                                         f"the planned {shares} shares (${shares * px:,.0f}); the frozen size "
                                         "is kept, not shrunk"))
                    self._event({"kind": "refused_live_buying_power", "symbol": sym, "planned_shares": shares,
                                 "planned_notional": round(shares * px, 2), "live_bp_left": round(live_left, 2)})
                    continue
            if live:
                got = self._take(sym)
                if not got:
                    refused.append((sym, "already held by this account (reservation refused)")); continue
                reserved.append(sym)
            risk_used += shares * rd
            gross_used += shares * px
            tgt = round(px + self.cfg["target_r"] * rd * (1 if L else -1), 2)
            coid, seq, att = self._coid(sym, day)
            plan.append({"key": coid, "coid": coid, "symbol": sym, "direction": direction, "tier": tier,
                         "day": day, "wave": wave, "seq": seq, "attempt": att,
                         "planned_shares": shares, "shares": shares, "initial_shares": shares,
                         "bracket_qty": shares, "entry_ref": round(px, 2), "card_entry": c_entry,
                         "slip_pct": round(slip, 3), "slip_frac": round(slip_frac, 3), "stop": stop,
                         "initial_stop": stop, "rd": round(rd, 4), "target": tgt,
                         "risk_usd": round(shares * rd, 2), "created_at": now.isoformat(),
                         "status": "PENDING_SUBMIT" if live else "SHADOW", "peak": 0.0,
                         "be_triggered": False, "be_locked": False, "errs": 0, "carried": False})
        if not plan:
            return _res(False, "no pick passed the sizing guards", refused=refused)

        # --- intent BEFORE orders (finding 11): durable before the first POST ---
        with self._lock:
            for pl in plan:
                self.state["positions"][pl["key"]] = dict(pl)
                self.state["risk"].setdefault(day, []).append(
                    {"coid": pl["coid"], "symbol": pl["symbol"], "risk_usd": pl["risk_usd"],
                     "result": "pending" if live else "shadow"})
        self._event({"kind": "execution_intent", "wave": wave, "mode": self.mode,
                     "plan": [{k: pl[k] for k in ("symbol", "direction", "tier", "shares", "entry_ref", "stop",
                                                   "target", "rd", "risk_usd", "coid")} for pl in plan]})
        try:
            self._persist()
        except Exception as exc:
            with self._lock:
                for pl in plan:
                    self.state["positions"][pl["key"]]["status"] = "REJECTED"
                    self.state["positions"][pl["key"]]["closed_reason"] = "intent could not be saved"
                    self._set_risk_result(day, pl["coid"], "not_sent")
            unreserve_all()
            return _res(False, f"could not save the intent before ordering ({exc}): nothing was sent",
                        refused=refused)

        placed: List[dict] = []
        for pl in plan:
            sym = pl["symbol"]
            if not live:
                placed.append(dict(pl, outcome="shadow", result={"shadow": True}))
                self._close_position(pl["key"], "shadow: no order sent", status="SHADOW", release=False)
                continue
            skip = self._late_checks(pl, now)
            if skip:
                placed.append(dict(pl, outcome="skipped", result={"error": skip}))
                self._drop_position(pl["key"], skip)
                continue
            with self._sym_lock(sym):
                okm, whym = self._late_macro(pl)
                if not okm:
                    refused.append((sym, "macro veto: " + whym))
                    placed.append(dict(pl, outcome="skipped", result={"error": "macro veto before the order: " + whym}))
                    self._drop_position(pl["key"], "macro veto before the order: " + whym)
                    continue
                # admission: from the final re-check to the POST no blocker can change (flatten-all, halt,
                # mode wait for this lock), so an entry either goes out before them or not at all
                self._admission_lock.acquire()
                released = [False]

                def release_admission():
                    if not released[0]:
                        released[0] = True
                        self._admission_lock.release()
                try:
                    stop = self._entry_stop_reason(pl)
                    if stop:
                        placed.append(dict(pl, outcome="skipped", result={"error": stop}))
                        self._drop_position(pl["key"], stop)
                        continue
                    key = self._record_submit(pl["coid"], sym, "buy" if pl["direction"] == "long" else "sell",
                                              pl["shares"], "entry", pl["key"])
                    try:
                        self._persist()
                    except Exception as exc:
                        with self._lock:
                            self.state["orders"].pop(key, None)
                        placed.append(dict(pl, outcome="skipped", result={"error": f"could not save the order record: {exc}"}))
                        self._drop_position(pl["key"], "order record could not be saved; nothing sent")
                        continue
                    outcome, result = self._submit_entry(pl, after_post=release_admission)
                finally:
                    release_admission()
            placed.append(dict(pl, outcome=outcome, result=result))
            self._persist_quiet()

        accepted = [p for p in placed if p["outcome"] == "accepted"]
        rejected = [{"symbol": p["symbol"], "result": p.get("result")} for p in placed
                    if p["outcome"] not in ("accepted", "shadow")]
        unknown = [p["symbol"] for p in placed if p["outcome"] == "unknown"]
        if unknown:
            self._event({"kind": "submit_unknown", "symbols": unknown,
                         "note": "broker response lost; kept under supervision until the broker proves no order"})
        if live and not accepted and not unknown:
            self._event({"kind": "execution_failed", "why": "the broker accepted none of the orders"})
            return _res(False, "the broker accepted NONE of the orders: nothing is working, check the account",
                        placed=placed, rejected=rejected, refused=refused, accepted=0)
        return _res(True, None, placed=placed, accepted=len(accepted) if live else len(placed),
                    rejected=rejected, partial=bool(rejected), refused=refused, dry_run=not live,
                    unknown=unknown)

    def _take(self, sym: str) -> bool:
        """reserve() once per symbol per process: a second reconcile must not trip over our own hold."""
        if sym in self._held:
            return True
        try:
            got = bool(self.reserve(sym))
        except Exception:
            got = False
        if got:
            self._held.add(sym)
        return got

    def _safe_release(self, sym: str) -> None:
        self._held.discard(sym)
        try:
            self.release(sym)
        except Exception as exc:
            log.warning("ORB release(%s) failed: %s", sym, exc)

    def _set_risk_result(self, day: str, coid: str, result: str) -> None:
        with self._lock:
            for r in self.state["risk"].get(day) or []:
                if r["coid"] == coid:
                    r["result"] = result

    def _drop_position(self, key: str, reason: str) -> None:
        """A planned entry that never went out: no risk used, symbol given back."""
        with self._lock:
            pos = self.state["positions"].get(key)
            if pos is None:
                return
            self._set_risk_result(pos["day"], pos["coid"], "not_sent")
        self._close_position(key, reason, status="SKIPPED")

    def _late_checks(self, pl: dict, now: datetime) -> Optional[str]:
        """Right before the POST: re-read ADT's book and Alpaca's positions/open orders."""
        sym = pl["symbol"]
        if self.entry_gate is not None:
            try:
                why = self.entry_gate(sym)
            except Exception as exc:
                why = f"the entry gate could not be read ({exc})"
            if why:
                self._event({"kind": "skip_entry_gate", "symbol": sym, "why": str(why)[:200]})
                return f"ADT entry gate: {why}"
        stop = self._entry_stop_reason(pl)
        if stop:
            return stop
        try:
            if self.is_occupied(sym):
                self._event({"kind": "skip_occupied", "symbol": sym, "late": True})
                return "symbol became occupied before the order"
            qty = self._call("normal", self.broker.position_qty, sym)
            orders = self._call("normal", self.broker.list_open_orders, sym)
            if qty != 0 or any(isinstance(o, dict) and str(o.get("symbol") or "").upper() == sym for o in orders):
                self._event({"kind": "skip_occupied", "symbol": sym, "late": True})
                return "symbol became occupied before the order"
        except Exception as exc:
            return f"occupancy re-check failed: {exc}"
        return None

    def _entry_stop_reason(self, pl: dict) -> Optional[str]:
        """Re-read everything that may have stopped ORB since execute() began (a breaker trip, the
        operator's flatten-all, ORB's own halt, the mode, shutdown) and that the pending position was not
        closed meanwhile (a supervisor pass honouring flatten-all). Called before and, under the symbol
        lock, right before the entry record/POST."""
        if self.mode != "live":
            return f"ORB is {self.mode}: no new entries"
        if self._entries_closed:
            return f"{self._entries_closed}: no new entries"
        ah = self._account_halt_reason()
        if ah:
            return f"{ah}: no new entries"
        h = self.halted()
        if h:
            return f"Daily loss halt active ({h}): no new entries allowed today"
        blk = self.entries_blocked()
        if blk:
            return f"ORB positions were flattened today ({blk}): no new entries until tomorrow"
        with self._lock:
            pos = self.state["positions"].get(pl["key"]) or {}
            st = pos.get("status")
        if st != "PENDING_SUBMIT":
            return f"the planned position is no longer pending ({st})"
        return None

    def _late_macro(self, pl: dict) -> Tuple[bool, str]:
        # OrbsFacade.macro_veto returns (VETOED, why): True means the trade must NOT be placed
        try:
            vetoed, why = self.facade.macro_veto(pl["symbol"], pl["direction"], self._now())
            ok = vetoed is False
        except Exception as exc:
            ok, why = False, f"macro check failed: {exc}"
        if not ok:
            self._event({"kind": "skip_macro", "symbol": pl["symbol"], "why": why, "late": True})
        return bool(ok), str(why or "")

    def _lookup_coid(self, coid: str, tries: int = COID_LOOKUPS, gap: float = COID_LOOKUP_GAP_S,
                     sleep_first: bool = True) -> Tuple[Optional[dict], bool]:
        """orders.reconcile_order_by_coid: (order|None, definitive_not_found). The verdict is the
        LAST attempt's: a broker answering 404 is definitive, a failed lookup is not."""
        definitive = False
        for i in range(tries):
            if sleep_first or i > 0:
                self.sleep(gap)
            try:
                got = self._call("exit", self.broker.get_order_by_client_id, coid)
                if isinstance(got, dict) and got.get("id"):
                    return got, False
                definitive = True
            except BrokerHTTPError as exc:
                definitive = exc.status_code == 404
            except Exception:
                definitive = False
        return None, definitive

    def _submit_entry(self, pl: dict, after_post: Optional[Callable[[], None]] = None) -> Tuple[str, dict]:
        """orders.submit_equity_bracket + core._book_submit_result. Returns (outcome, result)."""
        sym, key = pl["symbol"], pl["coid"]
        side = "buy" if pl["direction"] == "long" else "sell"
        L = side == "buy"
        if (L and not pl["stop"] < pl["entry_ref"] < pl["target"]) or \
                (not L and not pl["target"] < pl["entry_ref"] < pl["stop"]):
            self._finish_rejected(pl, "bracket invalid after 2 dp rounding")
            return "rejected", {"error": "bracket invalid after 2 dp rounding", "rejected": True}
        try:
            try:
                res = self._write("normal", self.broker.submit_bracket, sym, pl["shares"], side,
                                 pl["target"], pl["stop"], pl["coid"])
            finally:
                if after_post is not None:
                    after_post()              # the POST happened (or not): blockers may run now
        except (BudgetThrottled, DestinationRefused) as exc:
            with self._lock:
                self.state["orders"][key].update(status="not_sent", terminal=True, submit_state="answered")
            self._finish_rejected(pl, str(exc))
            return "rejected", {"error": str(exc), "rejected": True}
        except Exception as exc:
            refused = isinstance(exc, BrokerHTTPError) and exc.definitive
            got, _definitive = self._lookup_coid(pl["coid"])
            if got is not None:
                res = got
            elif refused:
                with self._lock:
                    rec = self.state["orders"].get(key)
                    if rec:
                        rec["submit_state"] = "answered"
                self._mark_not_found(key, confirmed=True)
                self._finish_rejected(pl, f"refused by the broker (HTTP {exc.status_code}): {str(exc)[:150]}")
                return "rejected", {"error": f"refused by the broker (HTTP {exc.status_code}): {str(exc)[:150]}",
                                    "rejected": True, "http": exc.status_code}
            else:
                with self._lock:
                    rec = self.state["orders"].get(key)
                    if rec and rec.get("submit_state") == "in_flight":
                        rec["submit_state"] = "unanswered"
                    p = self.state["positions"].get(pl["key"])
                    if p:
                        p["status"] = "UNKNOWN"
                        p["unknown_submit"] = True
                    self._set_risk_result(pl["day"], pl["coid"], "unknown")
                return "unknown", {"error": f"order state UNKNOWN after: {str(exc)[:150]} (no broker answer; kept pending)",
                                   "ambiguous": True}
        self._merge(dict(res, client_order_id=res.get("client_order_id") or pl["coid"]), "entry", pl["key"],
                    symbol_hint=sym, side_hint=side)
        with self._lock:
            p = self.state["positions"].get(pl["key"])
            if p and p["status"] in ("PENDING_SUBMIT", "UNKNOWN"):
                p["status"] = "OPEN" if self.own_qty(sym) != 0 else "SUBMITTED"
            if p:
                p["parent_id"] = res.get("id")
            self._set_risk_result(pl["day"], pl["coid"], "accepted")
        if res.get("status") == "partially_filled":
            fq = _i(res.get("filled_qty"))
            if 0 < fq < pl["shares"]:
                self._resize_legs(self.state["positions"][pl["key"]], fq)
        return "accepted", {"id": res.get("id"), "status": res.get("status")}

    def _finish_rejected(self, pl: dict, reason: str) -> None:
        with self._lock:
            self._set_risk_result(pl["day"], pl["coid"], "rejected")
        self._close_position(pl["key"], reason, status="REJECTED")

    # ------------------------------------------------------------------ legs
    def _leg_ids(self, pos: dict, learn: bool = True) -> Tuple[Optional[str], Optional[str]]:
        """Our bracket's live (take_profit_id, stop_loss_id), from the parent's nested legs only."""
        def pick() -> Tuple[Optional[str], Optional[str]]:
            tp = sl = None
            for _k, r in self._records(pos["symbol"], position=pos["key"]):
                if r.get("role") != "leg" or r["terminal"] or not r.get("id"):
                    continue
                if r.get("leg_kind") == "tp":
                    tp = r["id"]
                elif r.get("leg_kind") == "sl":
                    sl = r["id"]
            return tp, sl
        tp, sl = pick()
        if learn and (tp is None or sl is None):
            parent = self.state["orders"].get(pos["key"])
            try:
                if parent and parent.get("id"):
                    body = self._call("normal", self.broker.get_order, parent["id"], True)
                else:
                    body = self._call("normal", self.broker.get_order_by_client_id, pos["coid"])
                if body:
                    self._merge(body)
                tp, sl = pick()
            except Exception as exc:
                log.warning("could not resolve bracket legs for %s: %s", pos["symbol"], exc)
        return tp, sl

    def _resize_legs(self, pos: dict, new_qty: int) -> bool:
        """orders.resize_bracket_children: PATCH both legs to the filled qty."""
        tp, sl = self._leg_ids(pos)
        results = {}
        for name, lid in (("take_profit", tp), ("stop_loss", sl)):
            if not lid:
                results[name] = {"ok": False, "error": "leg not found"}
                continue
            try:
                rr = self._write("normal", self.broker.patch_order, lid, qty=new_qty)
                results[name] = {"ok": True}
                if isinstance(rr, dict) and rr.get("id"):
                    self._merge(rr, "leg", pos["key"], "tp" if name == "take_profit" else "sl",
                                pos["symbol"], "sell" if pos["direction"] == "long" else "buy")
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)[:160]}
        ok = all(r.get("ok") for r in results.values())
        with self._lock:
            p = self.state["positions"].get(pos["key"])
            if p is not None:
                if ok:
                    p["bracket_qty"] = new_qty
                p["shares"] = new_qty
        self._event({"kind": "bracket_resized_partial_fill" if ok else "bracket_resize_unconfirmed",
                     "symbol": pos["symbol"], "ordered_qty": pos.get("initial_shares"), "filled_qty": new_qty,
                     "result": results})
        return ok

    def _breakeven(self, pos: dict, entry: float) -> dict:
        """orders.modify_bracket_stop_to_breakeven: PATCH our stop leg to the entry price."""
        _tp, sl = self._leg_ids(pos)
        if not sl:
            return {"ok": False, "reason": f"No open stop loss order found for {pos['symbol']}"}
        try:
            rr = self._write("normal", self.broker.patch_order, sl, stop_price=round(entry, 2))
        except Exception as exc:
            return {"ok": False, "stop_order_id": sl, "error": str(exc)[:200]}
        if isinstance(rr, dict) and rr.get("id"):
            self._merge(rr, "leg", pos["key"], "sl", pos["symbol"], "sell" if pos["direction"] == "long" else "buy")
        return {"ok": True, "stop_order_id": sl, "new_stop": entry}

    # ------------------------------------------------------------------ refresh
    def _refresh_one(self, key: str, rec: dict, prio: str = "refresh") -> bool:
        """core._refresh_one: by broker id (entries nested), else by our client id."""
        with self._lock:
            live_rec = self.state["orders"].get(key)
            if live_rec is not None:
                live_rec["last_refresh"] = self._now().timestamp()
        try:
            if rec.get("id"):
                body = self._call(prio, self.broker.get_order, rec["id"], rec.get("role") == "entry")
                if isinstance(body, dict) and body.get("id") == rec["id"]:
                    self._merge(body)
                    return True
            elif rec.get("coid"):
                body = self._call(prio, self.broker.get_order_by_client_id, rec["coid"])
                if isinstance(body, dict) and body.get("id"):
                    self._merge(dict(body, client_order_id=body.get("client_order_id") or rec["coid"]))
                    return True
                return self._mark_not_found(key)
        except Exception:
            pass
        with self._lock:
            r = self.state["orders"].get(key)
            if r is not None:
                r["refresh_errs"] = int(r.get("refresh_errs") or 0) + 1
        return False

    def refresh_own_orders(self, prio: str = "refresh") -> dict:
        """core.refresh_own_orders: only NON-terminal own ids; entries every call, the rest at most
        every OWN_REFRESH_MIN_S. A symbol whose lock is held is skipped this pass, never raced."""
        now_ts = self._now().timestamp()
        with self._lock:
            due = [(k, dict(r)) for k, r in self.state["orders"].items() if not r["terminal"] and (
                r.get("role") == "entry" or now_ts - float(r.get("last_refresh") or 0) >= OWN_REFRESH_MIN_S)]
            due += [(k, dict(self.state["orders"][k])) for k in self._recheck_candidates()
                    if now_ts - float(self.state["orders"][k].get("last_refresh") or 0) >= RECHECK_NOT_FOUND_S]
        due.sort(key=lambda kr: 0 if kr[1].get("role") == "entry" else 1)
        ok = failed = skipped = 0
        for key, rec in due:
            lk = self._sym_lock(rec["symbol"])
            if not lk.acquire(blocking=False):
                skipped += 1
                continue
            try:
                cur = self.state["orders"].get(key)
                if not cur or (cur["terminal"] and not cur.get("inferred_terminal")):
                    continue
                if self._refresh_one(key, dict(cur), prio):
                    ok += 1
                else:
                    failed += 1
            finally:
                lk.release()
        return {"refreshed": ok, "failed": failed, "skipped_locked": skipped}

    # ------------------------------------------------------------------ exits
    def request_exit(self, symbol: str, reason: str) -> bool:
        """Ask the next tick to exit ORB's own position in symbol. False if ORB owns nothing there."""
        sym = symbol.upper()
        if not self.owns(sym):
            return False
        with self._lock:
            self.state["exit_requests"].setdefault(sym, reason)
        self._event({"kind": "exit_requested", "symbol": sym, "reason": reason})
        self._persist_quiet()
        return True

    def request_all_exits(self, reason: str, block_entries: bool = True) -> List[str]:
        """ADT breaker / flatten / manual / session paths: exit everything ORB owns at the next
        tick (own orders cancelled and confirmed first). block_entries: no new ORB entries for
        the rest of the day (ORBStraddle's manual flatten_all rule)."""
        with self._admission_lock:           # an entry mid-POST finishes first, then is exited too
            syms = sorted({p["symbol"] for p in self._active_positions()} | set(self._own_open_symbols()))
            with self._lock:
                for s in syms:
                    self.state["exit_requests"].setdefault(s, reason)
                if block_entries:
                    self.state["entries_blocked"] = {"day": self.state.get("day") or self._day(self._now()),
                                                     "reason": reason}
        self._event({"kind": "exit_all_requested", "reason": reason, "symbols": syms,
                     "entries_blocked": block_entries})
        self._persist_quiet()
        return syms

    def _own_open_symbols(self) -> List[str]:
        with self._lock:
            out = {s for s, v in self.state["own"].items() if int(v.get("qty") or 0) != 0}
            out |= {r["symbol"] for r in self.state["orders"].values() if not r["terminal"]}
            return sorted(out)

    def exit_own(self, symbol: str, why: str, confirm_s: float = EXIT_CONFIRM_S) -> dict:
        """core.exit_own, under the symbol lock:
          1. cancel ORB's own live orders on the symbol (parent remainder first, then the legs)
             and confirm each is final, folding final fills into the book;
          2. own qty 0 = done;
          3. one market day order for exactly own qty (capped to the account position), coid
             recorded before the POST; never DELETE /positions, never another actor's order;
          4. confirm by own fills."""
        sym = symbol.upper()
        t0 = _time.monotonic()
        if self.broker is None:
            return {"ok": False, "symbol": sym, "flat": False, "reason": "no broker"}
        with self._sym_lock(sym):
            return self._exit_own_locked(sym, why, confirm_s, t0)

    def _exit_result(self, sym, why, t0, ok, flat, reason=None, order_id=None, cancelled=(), qty=None, note=None):
        return {"ok": ok, "symbol": sym, "flat": flat, "qty": qty, "order_id": order_id,
                "cancelled_orders": len(cancelled), "ms": int((_time.monotonic() - t0) * 1000), "why": why,
                "unprotected": bool(cancelled) and not flat, "reason": reason, "note": note}

    def _is_working_exit(self, r: dict) -> bool:
        return r.get("role") == "exit" and bool(r.get("id")) and r.get("status") not in QUIESCENT

    def _cancel_own_live(self, sym: str, exclude: Optional[str] = None) -> Tuple[List[str], List[dict]]:
        """exclude: the exit intent this pass prepared (not sent yet): never a thing to cancel."""
        try:
            for o in self._call("exit", self.broker.list_open_orders, sym):
                if isinstance(o, dict):
                    self._merge(o)          # updates only ids already recorded as ours (and their legs)
        except Exception:
            pass
        cancelled: List[str] = []
        asked: set = set()
        for _round in range(3):
            live = [(k, r) for k, r in self._live(sym)
                    if r.get("status") not in QUIESCENT and not self._is_working_exit(r) and k != exclude]
            if not live:
                break
            live.sort(key=lambda kr: 0 if kr[1].get("role") == "entry" else 1)     # parents first
            for key, rec in live:
                if key in asked:
                    continue
                if not rec.get("id"):
                    self._refresh_one(key, rec, "exit")      # a submit whose reply we lost: find it
                    rec = dict(self.state["orders"].get(key) or rec)
                    if not rec.get("id") or rec["terminal"]:
                        continue
                asked.add(key)
                try:
                    body = self._write("exit", self.broker.cancel_order_and_confirm, rec["id"], CANCEL_CONFIRM_S)
                    self._merge(body)
                    if isinstance(body, dict) and body.get("status") == "canceled":
                        cancelled.append(rec["id"])
                except Exception as exc:
                    log.warning("cancel of own order %s failed: %s", rec.get("id"), exc)
            if not [1 for k, r in self._live(sym) if k not in asked and k != exclude
                    and r.get("status") not in QUIESCENT and not self._is_working_exit(r)]:
                break
        unresolved = [r for k, r in self._live(sym)
                      if r.get("status") not in QUIESCENT and not self._is_working_exit(r) and k != exclude]
        return cancelled, unresolved

    def _cap_to_account(self, sym: str, own: int, net: int, why: str, defer: Optional[list] = None) -> int:
        """Never push the ACCOUNT past zero: write off the part of own qty an outside trade took.
        defer: collect the event/alarm instead of emitting it (hooks may be slow; the exit POST
        must follow the position read with nothing slow in between)."""
        room = max(net, 0) if own > 0 else max(-net, 0)
        if abs(own) <= room:
            return own
        gone = abs(own) - room
        with self._lock:
            o = self.state["own"].setdefault(sym, {"qty": 0, "avg": None})
            side = "sell" if own > 0 else "buy"
            new_q, avg, _pnl, _unk = _fill_math(int(o["qty"]), o.get("avg"), side, gone, o.get("avg"))
            o["qty"], o["avg"] = new_q, avg
        ev = {"kind": "exit_capped_outside_trade", "symbol": sym, "why": why, "own_qty": own,
              "account_qty": net, "written_off": gone,
              "note": "an outside trade already reduced this position; ORB exits only what is left"}
        if defer is not None:
            defer.append(ev)
        else:
            self._emit_cap(ev)
        return self.own_qty(sym)

    def _emit_cap(self, ev: dict) -> None:
        self._event(ev)
        self._alarm(f"exit_capped_{ev['symbol']}", {"symbol": ev["symbol"], "written_off": ev["written_off"]})

    def _next_exit_coid(self, sym: str, day: str) -> str:
        n = self._next("exit_seq", f"{sym}|{day}")
        return f"{self.cfg['coid_prefix']}-X-{sym}-{day}-{n}-{secrets.token_hex(3)}"

    def _abort_ctx(self, ctx: Optional[dict], note: str) -> None:
        if not ctx:
            return
        with self._lock:
            rec = self.state["orders"].get(ctx["key"])
            if rec is not None and not rec.get("id"):
                rec.update(status="not_sent", terminal=True, submit_state="answered", note=note)
        self._persist_quiet()

    def _submit_exit(self, sym: str, own: int, why: str, pos_key: Optional[str],
                     ctx: Optional[dict] = None) -> Tuple[Optional[str], Optional[str], int, str]:
        side, n = ("sell" if own > 0 else "buy"), abs(own)
        if ctx is not None and ctx["side"] == side:
            # prepared before the protection was cancelled: intent durable, token held, position read
            coid, key = ctx["coid"], ctx["key"]
            with self._lock:
                rec = self.state["orders"].get(key)
                if rec is not None:
                    rec["qty"] = n
        else:
            self._abort_ctx(ctx, "the exit side changed after the cancels")
            ctx = None
            day = self.state.get("day") or self._day(self._now())
            coid = self._next_exit_coid(sym, day)
            key = self._record_submit(coid, sym, side, n, "exit", pos_key)
            try:
                self._persist()
            except Exception as exc:
                with self._lock:
                    self.state["orders"].pop(key, None)
                return None, f"could not save the exit record ({exc}); nothing sent", n, side
        # The intent (coid + the UNCAPPED planned qty, an upper bound) was persisted above. From here
        # to the POST nothing slow may run: destination check and budget token first, then the
        # position read, the cap, and the POST immediately. The capped qty and the order id are
        # persisted right after the POST; a restart in between is resolved by the coid lookup and
        # the "broker order qty is truth" rule.
        deferred: list = []

        def not_sent(note):
            with self._lock:
                self.state["orders"][key].update(status="not_sent", terminal=True, submit_state="answered",
                                                 note=note)
            for ev in deferred:
                self._emit_cap(ev)
            self._persist_quiet()
        if ctx is not None:
            # from here the POST may happen: a restart must look for it by client id. Hard barrier: if
            # this cannot be saved, nothing is sent (the latch retries next pass)
            with self._lock:
                rec = self.state["orders"].get(key)
                if rec is not None and rec.get("submit_state") == "prepared":
                    rec["submit_state"] = "in_flight"
            try:
                self._persist()
            except Exception as exc:
                with self._lock:
                    rec = self.state["orders"].get(key)
                    if rec is not None:
                        rec.update(status="not_sent", terminal=True, submit_state="answered",
                                   note="the in-flight mark could not be saved; nothing sent")
                return None, f"could not save the exit's in-flight mark ({exc}); nothing sent, retrying", n, side
        if ctx is None or not ctx.get("token"):
            try:
                self._verify_destination_for_write()
                self.budget.acquire("exit")
            except (BudgetThrottled, DestinationRefused) as exc:
                not_sent(f"not sent: {exc}")
                return None, str(exc), n, side
        # never a second exit while one of ours may still be working (checked in the preparation when there
        # was one, i.e. before the legs were cancelled, so no budget refusal can strand the close here)
        if ctx is None or not ctx.get("open_checked"):
            why_live = self._live_own_exit_at_broker(sym, coid)
            if why_live:
                not_sent(why_live)
                return None, why_live, n, side
        try:
            net = int(self._call("exit", self.broker.position_qty, sym))
        except Exception as exc:
            if ctx is not None and ctx.get("net") is not None:
                net = int(ctx["net"])       # the read taken before the cancels: never leave it naked
            else:
                not_sent("position unreadable right before the POST")
                return None, f"could not read the account position for {sym} before the exit; retrying ({exc})", n, side
        capped = self._cap_to_account(sym, self.own_qty(sym), net, why, defer=deferred)
        if capped == 0 or (capped > 0) != (own > 0):
            not_sent("nothing left to exit: fully offset by an outside trade")
            return None, None, 0, side
        n = abs(capped)
        with self._lock:
            self.state["orders"][key]["qty"] = n
        try:                                    # the shutdown fence covers the exit POST too
            slot = self._acquire_write_slot(what={"kind": "submit_market_order", "symbol": sym, "coid": coid})
        except DestinationRefused as exc:
            not_sent(f"not sent: {exc}")
            return None, str(exc), n, side
        try:
            try:
                resp = self.broker.submit_market_order(sym, side, n, coid)     # token already held
            finally:
                self._release_write_slot(slot)
            self._merge(dict(resp, client_order_id=resp.get("client_order_id") or coid))
            for ev in deferred:
                self._emit_cap(ev)
            self._persist_quiet()            # capped qty + order id, right after the POST
            return resp.get("id"), None, n, side
        except BudgetThrottled as exc:
            with self._lock:
                self.state["orders"][key].update(status="not_sent", terminal=True, submit_state="answered")
            return None, str(exc), n, side
        except Exception as exc:
            err = str(exc)[:200]
            definitive = isinstance(exc, BrokerHTTPError) and exc.definitive
            # a non-definitive answer (429 or 5xx) or a lost reply is ambiguous: the client id stays unresolved
            # through the full grace window (lookup visibility can lag); it is reconciled by client id before
            # any new exit POST, never closed early on a 404
            with self._lock:
                rec = self.state["orders"].get(key)
                if rec and rec.get("submit_state") == "in_flight":
                    rec["submit_state"] = "answered" if definitive else "unanswered"
            for ev in deferred:
                self._emit_cap(ev)
            self._persist_quiet()
        got, not_found = self._lookup_coid(coid, tries=3, gap=0.5, sleep_first=False)
        if got is not None:
            self._merge(dict(got, client_order_id=got.get("client_order_id") or coid))
            return got.get("id"), None, n, side
        if not_found:
            self._mark_not_found(key, confirmed=definitive)
        if not (self.state["orders"].get(key) or {}).get("terminal"):
            return None, err + " (outcome unknown; tracked by coid)", n, side
        return None, err, n, side

    def _protection_live(self, sym: str) -> bool:
        """Any own live order other than an exit (bracket parent remainder, legs, a re-armed stop)."""
        return any(not self._is_working_exit(r) and r.get("role") != "exit" and r.get("status") not in QUIESCENT
                   for _k, r in self._live(sym))

    def _exit_preflight(self, sym: str, own: int, why: str, pos_key: Optional[str]) -> Tuple[Optional[dict], Optional[str]]:
        """Everything that can fail, done BEFORE ORB's protection is cancelled (Codex/attack P0): the exit
        intent is durable, the destination verified, the exit budget token held and the account position
        readable. A failure here leaves the bracket untouched. Returns (ctx, None) or (None, why)."""
        side, n = ("sell" if own > 0 else "buy"), abs(own)
        day = self.state.get("day") or self._day(self._now())
        coid = self._next_exit_coid(sym, day)
        key = self._record_submit(coid, sym, side, n, "exit", pos_key)
        with self._lock:
            self.state["orders"][key]["submit_state"] = "prepared"     # not in flight until just before the POST
        try:
            self._persist()
        except Exception as exc:
            with self._lock:
                self.state["orders"].pop(key, None)
            return None, f"could not save the exit record ({exc}); protection left in place"
        ctx = {"key": key, "coid": coid, "side": side, "n": n, "token": False, "net": None}

        def abort(note):
            with self._lock:
                rec = self.state["orders"].get(key)
                if rec is not None:
                    rec.update(status="not_sent", terminal=True, submit_state="answered", note=note)
            self._persist_quiet()
            return None, note
        try:
            self._verify_destination_for_write()
            self.budget.acquire("exit")
            ctx["token"] = True
        except (BudgetThrottled, DestinationRefused) as exc:
            return abort(f"not sent: {exc}; protection left in place")
        why_live = self._live_own_exit_at_broker(sym, coid)
        if why_live:
            return abort(why_live + "; protection left in place")
        ctx["open_checked"] = True
        try:
            ctx["net"] = int(self._call("exit", self.broker.position_qty, sym))
        except Exception as exc:
            return abort(f"could not read the account position for {sym} ({exc}); protection left in place")
        return ctx, None

    def _live_own_exit_at_broker(self, sym: str, coid: str) -> Optional[str]:
        """Alpaca's open orders for the symbol must show no live ORB exit other than `coid`; an unreadable
        list is a refusal too. Returns why not to send, or None."""
        prefix_x = f"{self.cfg['coid_prefix']}-X-"
        try:
            rows = self._call("exit", self.broker.list_open_orders, sym)
        except Exception as exc:
            return f"could not list open orders for {sym} before the exit; retrying ({exc})"
        live = [o for o in rows if isinstance(o, dict) and str(o.get("client_order_id") or "").startswith(prefix_x)
                and o.get("client_order_id") != coid and o.get("status") not in QUIESCENT]
        if not live:
            return None
        for o in live:
            self._merge(o)
        return f"an own exit order ({live[0].get('client_order_id')}) is still live at Alpaca; not sending another"

    def _latch_exit(self, pos_key: Optional[str], why: str) -> None:
        """Once ORB's protection is (about to be) cancelled, the exit is owed until the position is flat:
        every later pass retries it whatever the price does (survives a restart)."""
        if not pos_key:
            return
        with self._lock:
            pos = self.state["positions"].get(pos_key)
            if pos is not None and not pos.get("exit_latched"):
                pos["exit_latched"] = why
                pos["exit_latched_at"] = self._now().timestamp()
        self._persist_quiet()

    def _exit_own_locked(self, sym: str, why: str, confirm_s: float, t0: float) -> dict:
        pos = self._position_for(sym)
        pos_key = pos["key"] if pos else None
        ctx = None
        own0 = self.own_qty(sym)
        if own0 != 0 and self._protection_live(sym):
            ctx, perr = self._exit_preflight(sym, own0, why, pos_key)
            if ctx is None:
                self._event({"kind": "exit_preflight_failed", "symbol": sym, "why": why, "reason": perr})
                return self._exit_result(sym, why, t0, False, False, reason=perr)
            self._latch_exit(pos_key, why)
        cancelled, unresolved = self._cancel_own_live(sym, exclude=ctx["key"] if ctx else None)
        if unresolved:
            why_u = "own order(s) not confirmed cancelled: " + ", ".join(
                f"{r.get('id') or r.get('coid')}={r.get('status')}" for r in unresolved[:4])
            self._event({"kind": "exit_waiting_on_cancel", "symbol": sym, "why": why, "reason": why_u})
            self._abort_ctx(ctx, "own orders not confirmed cancelled")
            return self._exit_result(sym, why, t0, False, False, reason=why_u, cancelled=cancelled)
        own = self.own_qty(sym)
        if own == 0:
            self._abort_ctx(ctx, "nothing left to exit")
            return self._exit_result(sym, why, t0, True, True, cancelled=cancelled)
        working = next((r for _k, r in self._live(sym) if r.get("role") == "exit" and r.get("id")
                        and r.get("status") not in QUIESCENT and (not ctx or _k != ctx["key"])), None)
        err = None
        if working:
            # a working own exit larger than what the account still holds would cross flat when it
            # fills: cancel and confirm it, then re-issue at the capped quantity
            try:
                net = int(self._call("exit", self.broker.position_qty, sym))
            except Exception as exc:
                reason = f"could not read the account position for {sym}; retrying ({exc})"
                self._event({"kind": "exit_waiting_on_position", "symbol": sym, "why": why, "reason": reason})
                return self._exit_result(sym, why, t0, False, False, reason=reason, cancelled=cancelled)
            remaining = int(working.get("qty") or 0) - int(working.get("filled_qty") or 0)
            room = max(net, 0) if own > 0 else max(-net, 0)
            if remaining > room:
                self._event({"kind": "exit_resized_to_account", "symbol": sym, "working_qty": remaining,
                             "account_qty": net})
                try:
                    body = self._write("exit", self.broker.cancel_order_and_confirm, working["id"], CANCEL_CONFIRM_S)
                    self._merge(body)
                except Exception as exc:
                    log.warning("cancel of oversized own exit %s failed: %s", working["id"], exc)
                cur = self.state["orders"].get(self._find(oid=working["id"]) or "") or {}
                if cur.get("status") not in QUIESCENT:
                    reason = f"oversized own exit {working['id']} not confirmed cancelled ({cur.get('status')})"
                    return self._exit_result(sym, why, t0, False, False, reason=reason, cancelled=cancelled)
                working = None
                own = self.own_qty(sym)
                if own == 0:
                    return self._exit_result(sym, why, t0, True, True, cancelled=cancelled)
        if working:
            self._abort_ctx(ctx, "an own exit is already working")
            order_id, n = working["id"], abs(own)
        else:
            order_id, err, n, _side = self._submit_exit(sym, own, why, pos_key, ctx)
        flat = False
        if order_id:
            for _p in range(max(1, int(round(confirm_s)))):
                if self.own_qty(sym) == 0 and not self._live(sym):
                    flat = True
                    break
                if not self._live(sym):
                    break
                self.sleep(1.0)
                for k, r in self._live(sym):
                    self._refresh_one(k, r, "exit")
        if self.own_qty(sym) == 0 and not self._live(sym):
            flat = True
        reason = None
        if not flat:
            rec = next((r for _k, r in self._records(sym) if r.get("id") == order_id), {}) if order_id else {}
            if err:
                reason = err
            elif rec.get("terminal"):
                reason = (f"own exit order {rec.get('status')} after filling {rec.get('filled_qty')} of "
                          f"{rec.get('qty')}; own qty now {self.own_qty(sym)}")
            else:
                reason = "own exit order accepted but not confirmed filled"
        return self._exit_result(sym, why, t0, bool(order_id) or flat, flat, reason=reason, order_id=order_id,
                                 cancelled=cancelled, qty=n,
                                 note=None if flat else ("own exit order working" if order_id else None))

    def _close_position(self, key: str, reason: str, status: str = "CLOSED", release: bool = True) -> None:
        with self._lock:
            pos = self.state["positions"].get(key)
            if pos is None:
                return
            was_active = pos.get("status") in ACTIVE
            pos["status"] = status
            pos["closed_reason"] = reason
            pos["closed_at"] = self._now().isoformat()
            if not any(p.get("status") in ACTIVE and p["symbol"] == pos["symbol"]
                       for p in self.state["positions"].values()):
                self.state["exit_requests"].pop(pos["symbol"], None)
        if status != "SHADOW":
            self._event({"kind": "position_closed", "symbol": pos["symbol"], "status": status, "reason": reason})
        if release and was_active:
            self._safe_release(pos["symbol"])

    def _close_reason(self, pos: dict) -> str:
        if pos.get("exit_reason"):
            return pos["exit_reason"]
        recs = [r for _k, r in self._records(pos["symbol"], position=pos["key"])]
        if any(r.get("leg_kind") == "tp" and int(r.get("filled_qty") or 0) > 0 for r in recs):
            return "target"
        if any(r.get("leg_kind") == "sl" and int(r.get("filled_qty") or 0) > 0 for r in recs):
            return "stop"
        entry = self.state["orders"].get(pos["key"]) or {}
        if entry.get("status") == "not_found":
            return "the entry never reached the broker"
        if int(entry.get("filled_qty") or 0) == 0:
            return f"the entry never filled ({entry.get('status')})"
        return "closed"

    # ------------------------------------------------------------------ supervisor
    def tick(self, now: Optional[datetime] = None) -> dict:
        """One core._supervise pass. Call every 5 s while needs_supervision(); cheap otherwise."""
        if not self._tick_lock.acquire(blocking=False):
            return {"skipped": "another supervisor pass is running"}
        try:
            now = self._now(now)
            self._roll_day(now)
            out = self._tick_locked(now)
            self.last_tick = dict(out, at=now.isoformat())
            return out
        finally:
            self._tick_lock.release()

    def _tick_locked(self, now: datetime) -> dict:
        if self.broker is None:
            return {"active": 0}
        self._last_position_qtys = None          # only a read from THIS pass may prove an outside close
        if self.needs_supervision():
            try:
                self.refresh_own_orders()
            except Exception as exc:
                log.warning("own order refresh failed (continuing): %s", exc)
        self._adopt_orphans()
        active = self._active_positions()
        if not active:
            self._persist_quiet()
            return {"active": 0}
        force = None
        h = self.halted()
        if h:
            force = "daily-loss halt"
        ah = self._account_halt_reason()
        if ah and not force:
            force = "account loss stop"
        pos_map = self._prices_from_positions()
        if not force:
            st = self.own_pnl_state(pos_map or {})
            breached, pct = self.own_halt_breached(st)
            if breached:
                force = "daily-loss halt"
                self._latch_halt(f"ORB's own P&L reached {pct:.2f}% (limit -{self.cfg['daily_loss_halt_pct']}%)", pct)
            elif not (st["realized_known"] and st["unrealized_known"]):
                self._alarm("own_pnl_unknown", {"reasons": st["reasons"][:4]})
        exits = []
        for pos in sorted(active, key=lambda p: p["symbol"]):
            try:
                r = self._supervise_one(pos["key"], now, force, pos_map)
                if r:
                    exits.append(r)
            except Exception as exc:
                log.exception("ORB supervision error on %s", pos.get("symbol"))
                with self._lock:
                    p = self.state["positions"].get(pos["key"])
                    if p is not None:
                        p["errs"] = int(p.get("errs") or 0) + 1
                self._event({"kind": "supervision_symbol_error", "symbol": pos.get("symbol"), "err": str(exc)[:200]})
        self._persist_quiet()
        return {"active": len(self._active_positions()), "force": force, "exits": exits}

    def _supervise_one(self, key: str, now: datetime, force: Optional[str],
                       pos_map: Optional[Dict[str, float]]) -> Optional[dict]:
        with self._lock:
            pos = self.state["positions"].get(key)
            if pos is None or pos.get("status") not in ACTIVE:
                return None
            pos = dict(pos)
        sym = pos["symbol"]
        if self._flat_proven(pos):
            entry = self.state["orders"].get(key) or {}
            if entry.get("status") == "not_found":
                self._set_risk_result(pos["day"], pos["coid"], "not_found")
            self._close_position(key, self._close_reason(pos))
            return None
        entry_rec = self.state["orders"].get(key) or {}
        has_fill = int(entry_rec.get("filled_qty") or 0) > 0
        own_q = self.own_qty(sym)
        L0 = pos["direction"] == "long"
        # closed at Alpaca outside ORB (e.g. by hand): no own order is live and a SYMBOL-SPECIFIC read says
        # flat (404 or qty 0). A symbol missing from a bulk snapshot is unknown, never zero.
        acct = self._last_position_qtys
        if own_q != 0 and acct is not None and sym not in acct and not self._live(sym):
            try:
                net_sym = int(self._call("normal", self.broker.position_qty, sym))
            except Exception:
                net_sym = None
            if net_sym == 0 and not self._live(sym):
                self._cap_to_account(sym, own_q, 0, "closed outside ORB")
                if self._flat_proven(pos):
                    self._close_position(key, "closed outside ORB (the account is flat and ORB's orders are gone)")
                    return None
        crossed = own_q != 0 and ((own_q > 0) != L0)
        latched = pos.get("exit_latched")
        px = (pos_map or {}).get(sym)
        if px is None and own_q != 0:
            # no position row (a broker-side exit not refreshed yet, or an unreadable snapshot):
            # price from the relay's latest trade, as core._own_price(fetch=True) does
            try:
                px = self.fresh_price(sym, now)
            except Exception:
                # the decision process (latest trade) may be down: Alpaca's own position price, as
                # ORBStraddle's supervisor does; exits never depend on the decision process
                again = self._prices_from_positions()
                px = (again or {}).get(sym)
        L = pos["direction"] == "long"
        flat_now = now.time() >= parse_hms(self.cfg["flatten"])
        carry = bool(pos.get("carried") or pos.get("no_known_stop"))
        requested = self.state["exit_requests"].get(sym)
        r = 0.0
        claw = ff = be_exit = absorb = False
        upd: Dict[str, Any] = {}
        if crossed:
            force = force or "both bracket legs filled: buying back to flat"
        elif latched and not force:
            force = f"{latched} (retrying)"
        if px is None:
            if not (force or requested or flat_now or carry):
                return None
        else:
            if has_fill and own_q != 0:
                ours_qty, entry = abs(own_q), float(self.own_avg(sym) or pos["entry_ref"])
            else:
                ours_qty, entry = int(pos.get("initial_shares") or pos.get("shares") or 0), float(pos["entry_ref"])
            if has_fill and ours_qty > 0 and ours_qty != int(pos.get("bracket_qty") or 0):
                self._resize_legs(pos, ours_qty)
                with self._lock:
                    pos = dict(self.state["positions"][key])
            rd = float(pos.get("rd") or 0.0)
            r = ((px - entry) / rd) * (1 if L else -1) if (px and rd) else 0.0
            peak = max(float(pos.get("peak") or 0.0), r)
            upd.update(peak=peak, last_r=round(r, 4), last_px=px, last_px_at=now.isoformat())
            be_triggered = bool(pos.get("be_triggered")) or r >= self.cfg["breakeven_r"]
            be_locked = bool(pos.get("be_locked"))
            upd["be_triggered"] = be_triggered
            if be_triggered and not be_locked and r > 0:
                change = self._breakeven(pos, entry)
                if change.get("ok"):
                    be_locked = True
                    upd.update(be_locked=True, stop=round(entry, 2))
                self._event({"kind": "breakeven_locked" if change.get("ok") else "breakeven_unconfirmed",
                             "symbol": sym, "entry": entry, "r": round(r, 2), "result": change})
            claw = peak >= self.cfg["clawback_peak_r"] and r <= peak - self.cfg["clawback_giveback_r"] and r > 0.0
            ff = r <= self.cfg["fast_fail_r"]
            be_exit = (be_locked or be_triggered) and r <= 0.0
            if not (flat_now or force or requested or carry or be_exit or claw or ff) and peak >= self.cfg["absorption_arm_r"]:
                try:
                    ab = self.facade.absorption_poll(sym, "long" if L else "short", now)
                except Exception as exc:
                    ab = None
                    log.warning("absorption check skipped for %s: %s", sym, exc)
                if ab and ab.get("direction") not in (None, "long" if L else "short"):
                    ab = None
                if ab and not self._absorption_fresh(ab):
                    ab = None               # flow.fresh_read: a stale or undated read never acts
                if ab and r >= self.cfg["absorption_arm_r"]:
                    if ab.get("error") and not pos.get("absorption_error_logged"):
                        upd["absorption_error_logged"] = True
                        self._event({"kind": "absorption_data_error", "symbol": sym, "err": ab["error"]})
                    absorb = ab.get("fire") is True and not ab.get("error")
                    if absorb:
                        self._event({"kind": "absorption_exit", "symbol": sym, "r": round(r, 2), "peak": round(peak, 2),
                                     "delta_ratio": ab.get("ratio"), "trades": ab.get("trades")})
        with self._lock:
            live = self.state["positions"].get(key)
            if live is not None:
                live.update(upd)
        if not (force or requested or flat_now or carry or be_exit or claw or absorb or ff):
            return None
        why = (force if force else requested if requested else "flatten" if flat_now
               else ("carried_over" if pos.get("carried") else "no_known_stop") if carry else "breakeven" if be_exit else "clawback" if claw
               else "absorption" if absorb else "fast-fail")
        with self._lock:
            live = self.state["positions"].get(key)
            if live is not None and not live.get("exit_reason"):
                live["exit_reason"] = why
        res = self.exit_own(sym, why, confirm_s=EXIT_CONFIRM_S)
        okf = bool(res.get("flat"))
        if not okf:
            with self._lock:
                live = self.state["positions"].get(key)
                since = live.get("exit_since") if live else None
                if live is not None and since is None:
                    live["exit_since"] = since = now.timestamp()
                escalated = live.get("escalated") if live else None
            stuck_s = now.timestamp() - (since or now.timestamp())
            late = now.time() >= parse_hms(self.cfg["flatten_escalate_hm"])
            win = "late" if late else "early"
            if (stuck_s >= self.cfg["flatten_escalate_after_s"] or late) and escalated != win:
                with self._lock:
                    if live is not None:
                        live["escalated"] = win
                er = self.exit_own(sym, why + " (escalated)", confirm_s=ESCALATED_CONFIRM_S)
                okf = bool(er.get("flat"))
                self._event({"kind": "flatten_escalated", "symbol": sym, "why": why, "flat": okf,
                             "stuck_s": int(stuck_s), "window": win,
                             "note": "retried the OWN exit; never touches another actor's orders or shares"})
                if not okf:
                    self._event({"kind": "unprotected_position", "symbol": sym,
                                 "reason": f"escalated own exit FAILED ({er.get('reason')})"})
                res = er
        if not okf:
            self._latch_alarm(key, now)
        self._event({"kind": "flatten" if okf else "flatten_fail", "symbol": sym, "why": why, "r": round(r, 2),
                     "exit": px, "reason": res.get("reason")})
        if okf:
            with self._lock:
                live = self.state["positions"].get(key)
                if live is not None:
                    live.pop("exit_since", None)
            self._close_position(key, why)
        return {"symbol": sym, "why": why, "flat": okf, "reason": res.get("reason")}

    def _latch_alarm(self, key: str, now: datetime) -> None:
        """A latched exit (its protection already cancelled) still not flat: escalating alarms at 30 s,
        2 min and 10 min. The exit itself is retried every supervisor pass until flat (no re-armed stop:
        ORBStraddle has none)."""
        with self._lock:
            pos = dict(self.state["positions"].get(key) or {})
        since = pos.get("exit_latched_at")
        if not pos.get("exit_latched") or since is None:
            return
        stuck = now.timestamp() - float(since)
        for level, limit in enumerate(LATCH_ALARMS_S, 1):
            if stuck >= limit:
                key_ = f"exit_latched_{pos['symbol']}_{level}"
                with self._lock:
                    seen = f"{self.state.get('day') or ''}:{key_}" in self.state["alarms"]
                if not seen:
                    self._alarm(key_, {"symbol": pos["symbol"], "stuck_s": int(stuck), "level": level,
                                       "note": "ORB's bracket is cancelled and the close has not gone through; "
                                               "retrying every pass. Check the position at Alpaca."})
                    (log.critical if level == len(LATCH_ALARMS_S) else log.error)(
                        "ORB exit for %s latched %d s without closing (level %d)", pos["symbol"], int(stuck), level)

    # ------------------------------------------------------------------ startup
    def reconcile_on_startup(self, now: Optional[datetime] = None) -> dict:
        """Rebuild ORB's book from the restored state + Alpaca by our ids/coids (parents nested,
        so their UUID legs come along) BEFORE entries are allowed. Own-prefix orders Alpaca shows
        that the state does not know are REPORTED, never adopted, and keep entries refused.
        Serialized with the supervisor: a tick that arrives meanwhile is skipped, never interleaved."""
        with self._tick_lock:
            return self._reconcile_locked(self._now(now))

    def _reconcile_locked(self, now: datetime) -> dict:
        self._roll_day(now)
        report: Dict[str, Any] = {"at": now.isoformat(), "checked": 0, "errors": [], "unknown_orders": [],
                                  "carried": [], "resolved": [], "reservation_conflicts": []}
        if self.broker is None:
            report["ok"] = self.mode != "live"
            self.ready = report["ok"]
            self.startup_report = report
            return report
        try:
            why = self._destination_refused(self._call("exit", self.broker.get_account_checked))
        except Exception as exc:
            why = f"account read failed: {exc}"
        if why:
            report["errors"].append(f"destination refused: {why}")
        # writes whose outcome was unknown at the last shutdown: find each at Alpaca (by client id, or
        # by order id) and fold its answer into the book before anything else is judged
        with self._lock:
            markers = list(self.state.get("unresolved_writes") or [])
            resolving = self._resolving
        report["unresolved_writes"] = markers
        # A marker is cleared ONLY when Alpaca shows the order: "not found" proves nothing (a POST can
        # still land late), so ORB stays unready for entries and the scheduler retries this every
        # reconcile interval, until the order appears or an operator resolves the marker. Exits and
        # supervision of known positions keep running meanwhile.
        left = []
        for m in markers:
            if resolving is not None and resolving in (m.get("coid"), m.get("order_id")):
                left.append(m)                # an operator is resolving it: never cleared from here
                continue
            found = False
            try:
                if m.get("coid"):
                    got = self._call("exit", self.broker.get_order_by_client_id, m["coid"])
                    if isinstance(got, dict) and got.get("id"):
                        self._merge(dict(got, client_order_id=got.get("client_order_id") or m["coid"]))
                        found = True
                elif m.get("order_id"):
                    got = self._call("exit", self.broker.get_order, m["order_id"], True)
                    if isinstance(got, dict) and got.get("id"):
                        self._merge(got)
                        found = True
            except Exception:
                found = False
            if not found:
                left.append(dict(m, checks=int(m.get("checks") or 0) + 1, last_check=now.isoformat()))
        with self._lock:
            # drop only the markers this pass found at Alpaca; a resolution finished meanwhile stays done
            left_refs = {(m.get("coid"), m.get("order_id")) for m in left}
            by_ref = {(m.get("coid"), m.get("order_id")): m for m in left}
            self.state["unresolved_writes"] = [by_ref.get((m.get("coid"), m.get("order_id")), m)
                                               for m in (self.state.get("unresolved_writes") or [])
                                               if (m.get("coid"), m.get("order_id")) in left_refs
                                               or m not in markers]
        if resolving is not None:
            report["errors"].append("an operator is resolving an unknown write; no new ORB entries until it is saved")
        if self._state_problem:
            report["errors"].append(self._state_problem)
        if left:
            report["errors"].append(f"{len(left)} write(s) sent just before the last shutdown have no answer "
                                    "from Alpaca yet; no new ORB entries until they are found or resolved")
        with self._lock:
            pending = [(k, dict(r)) for k, r in self.state["orders"].items() if not r["terminal"]]
            pending += [(k, dict(self.state["orders"][k])) for k in self._recheck_candidates()]
        pending.sort(key=lambda kr: 0 if kr[1].get("role") == "entry" else 1)
        for key, rec in pending:
            report["checked"] += 1
            if not self._refresh_one(key, rec, "exit"):
                cur = self.state["orders"].get(key) or {}
                if not cur.get("terminal") and not (cur.get("status") == "pending_submit"):
                    report["errors"].append(f"{rec['symbol']} order {rec.get('id') or rec.get('coid')} could not be read")
        prefix = self.cfg["coid_prefix"] + "-"
        try:
            rows = self._call("exit", self.broker.list_open_orders)
            for o in rows:
                if not isinstance(o, dict):
                    continue
                if str(o.get("client_order_id") or "").startswith(prefix):
                    if self._find(o.get("id"), o.get("client_order_id")) is None:
                        report["unknown_orders"].append({"id": o.get("id"), "client_order_id": o.get("client_order_id"),
                                                         "symbol": o.get("symbol"), "status": o.get("status")})
                    else:
                        self._merge(o)
        except Exception as exc:
            report["errors"].append(f"open orders could not be listed: {exc}")
        day = self._day(now)
        self._adopt_orphans()
        for pos in self._active_positions():
            if self._flat_proven(pos):
                self._close_position(pos["key"], self._close_reason(pos))
                report["resolved"].append(pos["symbol"])
                continue
            if pos.get("day") != day:
                with self._lock:
                    self.state["positions"][pos["key"]]["carried"] = True
                report["carried"].append(pos["symbol"])
            if not self._take(pos["symbol"]):
                report["reservation_conflicts"].append(pos["symbol"])
        if report["unknown_orders"]:
            self._alarm("unknown_own_orders", {"orders": report["unknown_orders"][:5],
                                               "note": "ADT-ORB orders at Alpaca that the saved state does not "
                                                       "know: not adopted; a human must check them"})
        report["ok"] = not report["errors"] and not report["unknown_orders"] and not report["reservation_conflicts"]
        with self._lock:
            if self._resolving is not None:
                report["ok"] = False          # never ready while a resolution's save is in flight
            self.ready = report["ok"]
        self.startup_report = report
        self._event({"kind": "startup_reconcile", "ok": report["ok"], "errors": report["errors"][:5],
                     "unknown_orders": len(report["unknown_orders"]), "carried": report["carried"]})
        self._persist_quiet()
        return report

    # ------------------------------------------------------------------ views
    def holdings(self) -> List[dict]:
        out = []
        for p in sorted(self._active_positions(), key=lambda x: x["symbol"]):
            sym = p["symbol"]
            out.append({"symbol": sym, "direction": p["direction"], "tier": p.get("tier"), "status": p["status"],
                        "qty": self.own_qty(sym), "avg_price": self.own_avg(sym), "planned_shares": p.get("planned_shares"),
                        "entry_ref": p.get("entry_ref"), "stop": p.get("stop"), "target": p.get("target"),
                        "rd": p.get("rd"), "r": p.get("last_r"), "peak_r": p.get("peak"), "last_price": p.get("last_px"),
                        "breakeven_locked": p.get("be_locked"), "exit_reason": p.get("exit_reason"),
                        "exit_requested": self.state["exit_requests"].get(sym), "carried": p.get("carried"),
                        "coid": p["coid"], "risk_usd": p.get("risk_usd"), "opened_at": p.get("created_at")})
        return out

    def status(self) -> dict:
        day = self.state.get("day")
        snap = self.session_sizing(day) if day else None
        eq = float(snap["equity"]) if snap else None
        with self._lock:
            realized = dict(self.state["realized"].get(day) or {})
            closed = [dict(p) for p in self.state["positions"].values()
                      if p.get("day") == day and p.get("status") in ("CLOSED", "REJECTED", "SHADOW", "SKIPPED")]
            events = list(self.state["events"][-20:])
        return {"mode": self.mode, "ready": self.ready, "day": day, "halted": self.halted(),
                "entries_blocked": self.entries_blocked(), "sizing": snap,
                "day_risk": {"used": self.day_risk_used(day), "max": self.max_day_risk(eq) if eq else None,
                             "max_risk_pct": self.cfg["max_day_risk_frac"] * 100.0},
                "slots": {"max": self.cfg["max_open_slots"], "available": self.slots_available(),
                          "structure_used": self.active_structure_count()},
                "holdings": self.holdings(), "closed_today": closed, "realized_today": realized,
                "last_execute": self.last_execute, "startup": self.startup_report,
                "budget": {"used": self.budget.used, "throttled": self.budget.throttled,
                           "exit_reserve_uses": self.budget.borrowed},
                "events": events}
