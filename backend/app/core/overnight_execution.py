# @steered SNARE-2 2026-09-30
"""Overnight holds controller (NVDA, IREN, HUT): buy at the close, sell at the next open.

PLAN_2026_09_30_overnight_holds.md section 3. This controller places, tracks and books its own
Alpaca paper orders; the generic engine never sees them (R4). Everything it needs is injected
(broker, executor, calendar, clock, booking callback, durable checkpoint, relay bar count, last
price, alert sink, reservation hooks), so it runs without main.py.

Rules kept here, each tested:
- Client ids are attempt numbered, adt-ovn-<SYM>-<YYYYMMDD>-buy-<n> / -sell-<n> (the sale
  carries the buy date). Before any POST the id is looked up at Alpaca. A lost or 429/5xx
  answer is resolved by lookup and, if Alpaca has nothing, resent under the SAME id; attempt
  n+1 is created only once attempt n is known final. One live attempt at a time.
- A buy needs a durable save of its intent before it is sent. A sale goes out even when every
  save fails (R2): its deterministic id lets a restart find it.
- OVERNIGHT_MODE off, the no buy tonight control, a missing broker and every skip reason stop
  new buys only. Nothing stops a sale.
- All broker and relay I/O runs on the injected executor. tick() only starts jobs and reads
  finished ones, so it never blocks the event loop.
- State is plain JSON (no __type__ classes) for the checkpoint key "overnight".
"""
from __future__ import annotations

import json
import logging
import math
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.core import overnight_schedule as osch
from backend.app.core.broker import (
    REFUSAL_AMBIGUOUS, REFUSAL_BUYING_POWER, REFUSAL_WASH_TRADE, classify_refusal, filled_avg, filled_qty,
    is_terminal,
)
from backend.app.core.overnight_schedule import et, et_date

log = logging.getLogger("overnight_execution")

STATE_VERSION = 1
LOG_LIMIT = 500

IDLE = "IDLE"
INTENT = "INTENT"
BUY_SENT = "BUY_SENT"
BUY_ACCEPTED = "BUY_ACCEPTED"
HELD = "HELD"
SALE_QUEUED = "SALE_QUEUED"
SOLD = "SOLD"
SKIPPED = "SKIPPED"
# NEEDS_LOOK is carried as a list of reasons next to the lifecycle state (never a state that
# stops the lifecycle), because a hold that needs a look must still sell (R2).
HOLD_STATES = (HELD, SALE_QUEUED)
BUY_STATES = (IDLE, INTENT, BUY_SENT)

BUY_STUCK_AFTER = dtime(16, 5)          # an auction or fallback buy not final by now is cancelled
FALLBACK_BUY_END = dtime(15, 59, 55)    # a plain market buy never goes out at or after the close
OPEN_ORDER_PREFIX = "adt-ovn-"


class InlineExecutor:
    """Runs each job at once and returns a finished Future (tests and deterministic replays)."""

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> Future:
        fut: Future = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as exc:  # the caller reads it from the future, like a thread pool
            fut.set_exception(exc)
        return fut


def _no_shares(symbol: str) -> int:
    return 0


def _noop(*args: Any, **kwargs: Any) -> None:
    return None


def _false() -> bool:
    return False


def _zero() -> float:
    return 0.0


@dataclass
class Hooks:
    """What the controller asks of the rest of the robot (all on the event loop)."""
    day_shares: Callable[[str], int] = _no_shares                 # non overnight shares in ADT's book
    close_day_trade: Callable[[str], None] = _noop                # X6: manual flatten steps, tagged
    cancel_day_entries: Callable[[str], None] = _noop             # cancel other working entries
    broker_mismatch: Callable[[], bool] = _false
    held_by_other: Callable[[str], Optional[str]] = _noop         # e.g. "ORB"
    swing_market_value: Callable[[], float] = _zero               # Slow trades held tonight
    on_release: Callable[[str], None] = _noop                     # the stock goes back to the day arms


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_time(raw: Any, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return fallback


class OvernightController:
    def __init__(self, *, broker: Any, executor: Any, calendar: Any, clock: Callable[[], datetime],
                 book: Callable[[Dict[str, Any]], Any], checkpoint: Callable[[str], bool],
                 bar_count: Callable[[str, date], Tuple[int, bool]], last_price: Callable[[str], Optional[float]],
                 alert: Optional[Callable[[str, str, Dict[str, Any]], None]] = None, hooks: Optional[Hooks] = None,
                 mode: str = "live", enabled: Tuple[str, ...] = osch.SYMBOLS, pct: float = 0.20,
                 cap: float = 25_000.0, room_multiple: float = 2.0, state: Optional[Dict[str, Any]] = None) -> None:
        if mode not in ("live", "off"):
            raise ValueError("OVERNIGHT_MODE must be live or off")
        self.broker = broker
        self.executor = executor
        self.calendar = calendar
        self.clock = clock
        self.book = book
        self.checkpoint = checkpoint
        self.bar_count = bar_count
        self.last_price = last_price
        self.alert = alert or (lambda kind, text, fields: log.warning("OVERNIGHT ALERT %s: %s", kind, text))
        self.hooks = hooks or Hooks()
        self.mode = mode
        self.enabled = tuple(s for s in osch.SYMBOLS if s in {e.upper() for e in enabled})
        self.pct, self.cap, self.room_multiple = pct, cap, room_multiple
        self.state = state if state is not None else self.empty_state()
        self._jobs: Dict[str, Future] = {}
        self._last: Dict[str, datetime] = {}

    # ------------------------------------------------------------ persistence
    @staticmethod
    def empty_state() -> Dict[str, Any]:
        # margin: the last good Alpaca asset read per stock, used when a later read fails
        return {"version": STATE_VERSION, "nights": {}, "control": {"no_buy_date": None, "set_at": None},
                "account": {}, "realized_offset": {"date": None, "amount": 0.0}, "margin": {}, "log": []}

    def to_json(self) -> Dict[str, Any]:
        """Plain JSON for the checkpoint key "overnight" (a deep copy, no live references)."""
        return json.loads(json.dumps(self.state))

    @classmethod
    def from_json(cls, data: Optional[Dict[str, Any]], **deps: Any) -> "OvernightController":
        if not data:
            return cls(**deps)
        if data.get("version") != STATE_VERSION:
            raise ValueError(f"unknown overnight state version {data.get('version')}")
        state = cls.empty_state()
        state.update(json.loads(json.dumps(data)))
        return cls(state=state, **deps)

    def _signature(self) -> str:
        s = self.state
        return json.dumps([s["nights"], s["control"], s["account"], s["realized_offset"], s["margin"]], sort_keys=True)

    # ---------------------------------------------------------------- helpers
    def _log(self, n: Optional[Dict[str, Any]], event: str, now: datetime, **fields: Any) -> None:
        row = {"at": _iso(now), "event": event, **fields}
        if n is not None:
            row.update(symbol=n["symbol"], buy_date=n["buy_date"])
        self.state["log"].append(json.loads(json.dumps(row, default=str)))
        del self.state["log"][:-LOG_LIMIT]
        log.info("OVERNIGHT %s %s", event, json.dumps(row, default=str, sort_keys=True))

    @staticmethod
    def _bd(n: Dict[str, Any]) -> date:
        return date.fromisoformat(n["buy_date"])

    @staticmethod
    def _sd(n: Dict[str, Any]) -> Optional[date]:
        return date.fromisoformat(n["sale_date"]) if n.get("sale_date") else None

    def _nights(self) -> List[Dict[str, Any]]:
        return sorted(self.state["nights"].values(),
                      key=lambda n: (n["buy_date"], osch.SYMBOLS.index(n["symbol"])))

    def _io(self, key: str, work: Callable[[], Any], now: datetime, every: float = 0.0):
        """(done, result, error). A job for key starts at most once per `every` seconds of now."""
        fut = self._jobs.get(key)
        if fut is None:
            last = self._last.get(key)
            if last is not None and (now - last).total_seconds() < every:
                return False, None, None
            self._last[key] = now
            fut = self.executor.submit(work)
            self._jobs[key] = fut
        if not fut.done():
            return False, None, None
        del self._jobs[key]
        try:
            return True, fut.result(), None
        except Exception as exc:
            return True, None, exc

    def _needs_look(self, n: Dict[str, Any], reason: str, now: datetime, detail: str = "") -> None:
        if reason in n["needs_look"]:
            return
        n["needs_look"].append(reason)
        self._log(n, "NEEDS_LOOK", now, reason=reason, detail=detail)
        self.alert("NEEDS_LOOK", f"{n['symbol']} overnight needs a look: {reason}. {detail}".strip(),
                   {"symbol": n["symbol"], "buy_date": n["buy_date"], "reason": reason})

    def _block(self, n: Dict[str, Any], reason: str) -> None:
        n["block"] = reason

    def _release(self, n: Dict[str, Any], now: datetime) -> None:
        if not n["released"]:
            n["released"] = True
            self._log(n, "RELEASED", now)
            self.hooks.on_release(n["symbol"])

    def _skip(self, n: Dict[str, Any], reason: str, now: datetime) -> None:
        n["state"], n["reason"] = SKIPPED, reason
        self._log(n, "SKIP", now, reason=reason, wanted_qty=n.get("wanted_qty"), qty=n.get("qty"))
        self._release(n, now)

    def _control_active(self, d: date) -> bool:
        return self.state["control"].get("no_buy_date") == d.isoformat()

    def _left(self, n: Dict[str, Any]) -> int:
        return sum(leg["qty"] - leg["sold"] for leg in n["legs"]) if n["legs"] else n["held_qty"]

    # ---------------------------------------------------------------- queries
    def reserves(self, symbol: str) -> bool:
        """True while any night in this stock is not released (15:45 until the sale is booked and
        Alpaca shows it flat, or until the buy is given up)."""
        return any(n["symbol"] == symbol.upper() and not n["released"] for n in self.state["nights"].values())

    def holds(self) -> List[Dict[str, Any]]:
        return [n for n in self._nights() if n["state"] in HOLD_STATES and self._left(n) > 0]

    def locks_all(self, symbol: str) -> bool:
        """S1: once a buy intent exists (sent, accepted, held, queued for sale, or sold but Alpaca
        not yet shown flat) every other order in this stock is refused."""
        sym = symbol.upper()
        return any(n["symbol"] == sym and not n["released"]
                   and (n["state"] not in (IDLE, SKIPPED) or n["held_qty"] > 0)
                   for n in self.state["nights"].values())

    def sale_due(self, symbol: str) -> Optional[datetime]:
        """S17: when the hold in this stock sells (the sale date at 09:30 ET), or None."""
        sym = symbol.upper()
        for n in self.holds():
            if n["symbol"] == sym and self._sd(n) is not None:
                return et(self._sd(n), osch.SALE_POLL_FROM)
        return None

    # ------------------------------------------------ broker loop refresh (S13)
    def pending_reads(self) -> List[Tuple[str, int, int, Optional[str], str]]:
        """Non final attempts to read before a position compare: (night key, leg index or -1 for
        the buy, attempt index, Alpaca id, client id). Nights with a job already in flight are left
        to that job, so a slower read can never land after a fresher one."""
        out: List[Tuple[str, int, int, Optional[str], str]] = []
        for key, n in self.state["nights"].items():
            syms = {n["symbol"]} | {leg["symbol"] for leg in n["legs"]}
            if any(k.endswith(f":{n['buy_date']}") and k.split(":")[1] in syms for k in self._jobs):
                continue
            for j, att in enumerate(n["buy"]):
                if not att["final"] and att["sent"]:
                    out.append((key, -1, j, att["id"], att["cid"]))
            for i, leg in enumerate(n["legs"]):
                for j, att in enumerate(leg["attempts"]):
                    if not att["final"] and att["sent"]:
                        out.append((key, i, j, att["id"], att["cid"]))
        return out

    def fetch_reads(self, reads: List[Tuple[str, int, int, Optional[str], str]]) -> Dict[Tuple[str, int, int], Any]:
        """Worker thread: read each attempt at Alpaca. Read only, touches no state."""
        out: Dict[Tuple[str, int, int], Any] = {}
        for key, i, j, oid, cid in reads:
            try:
                out[(key, i, j)] = self.broker.get_order(oid) if oid else self.broker.get_order_by_client_id(cid)
            except Exception as exc:  # proves nothing; the regular poll tries again
                log.warning("OVERNIGHT refresh read of %s failed: %s", cid, exc)
        return out

    def apply_reads(self, rows: Dict[Tuple[str, int, int], Any], now: Optional[datetime] = None) -> bool:
        """Event loop: book what the reads show. Returns True when anything new was booked."""
        now = now or self.clock()
        before = json.dumps([n["booked"] for n in self.state["nights"].values()], sort_keys=True)
        for (key, i, j), row in rows.items():
            n = self.state["nights"].get(key)
            if n is None or row is None:
                continue
            syms = {n["symbol"]} | {leg["symbol"] for leg in n["legs"]}
            if any(k.endswith(f":{n['buy_date']}") and k.split(":")[1] in syms for k in self._jobs):
                continue                    # a job started meanwhile: it owns this read
            try:
                if i < 0:
                    att = n["buy"][j]
                    if not att["final"]:
                        self._apply_buy_row(n, att, row, now)
                else:
                    leg = n["legs"][i]
                    att = leg["attempts"][j]
                    if not att["final"]:
                        self._apply_sale_row(n, leg, att, row, now)
            except Exception as exc:
                n["last_error"] = f"refresh: {type(exc).__name__}: {exc}"[:300]
                log.warning("OVERNIGHT refresh apply %s failed: %s", key, exc)
        return json.dumps([n["booked"] for n in self.state["nights"].values()], sort_keys=True) != before

    def overnight_realized_today(self, session_date: date) -> float:
        """D6 / S14: overnight result booked on session_date (0.0 for any other day)."""
        off = self.state["realized_offset"]
        return float(off["amount"]) if off.get("date") == session_date.isoformat() else 0.0

    def unsold_after_0931(self, now: datetime) -> List[str]:
        out = []
        for n in self.holds():
            sd = self._sd(n)
            if sd is not None and now >= et(sd, osch.SALE_FALLBACK_AT):
                out.append(n["symbol"])
        return out

    def health(self, now: datetime) -> Dict[str, Any]:
        return {"mode": self.mode, "enabled": list(self.enabled), "control": dict(self.state["control"]),
                "nights": [{k: n.get(k) for k in ("symbol", "buy_date", "sale_date", "state", "reason", "block",
                                                  "needs_look", "qty", "held_qty", "buy_avg", "realized")}
                           for n in self._nights()[-12:]],
                "holds": [n["symbol"] for n in self.holds()],
                "unsold_after_0931": self.unsold_after_0931(now),
                "realized_offset": dict(self.state["realized_offset"])}

    # ---------------------------------------------------------------- control
    def set_no_buy_tonight(self, now: datetime) -> Tuple[bool, str]:
        """D4. Saved durably with its date before success is reported. Never touches a sale."""
        d = et_date(now)
        if now >= et(d, osch.BUY_GIVE_UP):
            return False, "Too late for tonight. The buy can only be stopped until 3:49:30 PM."
        prev = dict(self.state["control"])
        self.state["control"] = {"no_buy_date": d.isoformat(), "set_at": _iso(now)}
        if not self.checkpoint("OVERNIGHT_CONTROL"):
            self.state["control"] = prev
            return False, "Not saved, so not applied. Try again."
        self._log(None, "CONTROL_NO_BUY_TONIGHT", now, date=d.isoformat())
        return True, "No overnight buy tonight. Holds already bought still sell at the next open."

    def clear_no_buy_tonight(self, now: datetime) -> Tuple[bool, str]:
        """D4 switched back off before 15:49:30. A buy the control already stopped stays stopped."""
        d = et_date(now)
        if not self._control_active(d):
            return True, "The overnight buy is on for tonight."
        if now >= et(d, osch.BUY_GIVE_UP):
            return False, "Too late for tonight. The control can only be changed until 3:49:30 PM."
        if any(n["buy_date"] == d.isoformat() and n["reason"] == osch.OPERATOR_NO_BUY_TONIGHT
               for n in self.state["nights"].values()):
            return False, "Tonight's buy was already stopped and cannot be restarted."
        prev = dict(self.state["control"])
        self.state["control"] = {"no_buy_date": None, "set_at": _iso(now)}
        if not self.checkpoint("OVERNIGHT_CONTROL"):
            self.state["control"] = prev
            return False, "Not saved, so not changed. Try again."
        self._log(None, "CONTROL_BUY_TONIGHT_ON", now, date=d.isoformat())
        return True, "The overnight buy is on for tonight."

    def record_fidelity(self, symbol: str, buy_date: date, fields: Dict[str, Any]) -> None:
        """Section 4.9 fidelity log. The caller fetches the SIP bars; this only stores and derives."""
        n = self.state["nights"].get(f"{symbol.upper()}:{buy_date.isoformat()}")
        if n is None:
            return
        fid = n["fidelity"]
        fid.update(json.loads(json.dumps(fields, default=str)))
        entry, exit_ = fid.get("research_entry"), fid.get("research_exit")
        if entry and exit_:
            fid["research_gross"] = exit_ / entry - 1
        if n["state"] == SOLD and n.get("buy_avg") and n["legs"] and n["legs"][0]["sold"]:
            leg = n["legs"][0]
            fid["real_gross"] = (leg["notional"] / leg["sold"]) / n["buy_avg"] - 1

    # ------------------------------------------------------------------- tick
    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or self.clock()
        before = self._signature()
        self._ensure_tonight(now)
        for n in self._nights():
            try:
                self._step(n, now)
            except Exception as exc:  # one night's failure never stops the others
                n["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("OVERNIGHT %s %s step failed: %s", n["symbol"], n["buy_date"], n["last_error"])
        self._alerts(now)
        if self._signature() != before:
            self.checkpoint("OVERNIGHT")    # best effort; only a buy intent requires durability

    def _ensure_tonight(self, now: datetime) -> None:
        d = et_date(now)
        if d.weekday() >= 5 or now < et(d, osch.COUNT_CUTOFF):
            return
        for sym in osch.SYMBOLS:
            key = f"{sym}:{d.isoformat()}"
            if key in self.state["nights"]:
                continue
            try:
                trading = self.calendar.is_trading_day(d)
            except osch.CalendarNotCovered:
                trading = None
            if trading is False:
                continue
            n = self._new_night(sym, d)
            self.state["nights"][key] = n
            self._log(n, "NIGHT", now, sale_date=n["sale_date"])
            if trading is None:
                self._skip(n, osch.CALENDAR_NOT_COVERED, now)
                self.alert("NEEDS_LOOK", f"{sym} overnight: the calendar does not cover {d.isoformat()}, no buy.",
                           {"symbol": sym, "buy_date": d.isoformat(), "reason": osch.CALENDAR_NOT_COVERED})
                continue
            reason = self._final_gate(n)       # mode off, stock off, no broker ... never reserve the stock
            if reason is not None:
                self._skip(n, reason, now)

    def _new_night(self, sym: str, d: date) -> Dict[str, Any]:
        sale = osch.sale_date(d, self.calendar)
        return {"symbol": sym, "strategy_id": osch.STRATEGY_IDS[sym], "buy_date": d.isoformat(),
                "sale_date": sale.isoformat() if sale else None, "state": IDLE, "reason": None, "block": None,
                "needs_look": [], "released": False, "wanted_qty": None, "qty": None, "ref_price": None,
                "margin_ratio": None, "shrunk": None, "bars": None, "entries_cancelled": False, "day_close_requested": False,
                "fallback_buy": False, "alerted_1547": False, "late_check": False, "buy": [],
                "held_qty": 0, "buy_cost": 0.0, "buy_avg": None, "legs": [], "booked": {},
                "morning_checked": False, "splits": [], "realized": 0.0, "fidelity": {}, "last_error": None}

    def _alerts(self, now: datetime) -> None:
        for n in self._nights():
            d = self._bd(n)
            if (n["state"] in BUY_STATES and not n["fallback_buy"] and not n["alerted_1547"]
                    and et(d, osch.BUY_ALERT) <= now < et(d, osch.BUY_GIVE_UP)):
                n["alerted_1547"] = True
                self._needs_look(n, osch.NO_ORDER_BY_1547, now, f"Last block: {n.get('block') or 'none'}.")

    def _step(self, n: Dict[str, Any], now: datetime) -> None:
        st = n["state"]
        if st in BUY_STATES:
            self._step_buy(n, now)
        elif st == BUY_ACCEPTED:
            self._step_buy_accepted(n, now)
        elif st in HOLD_STATES:
            self._step_sale(n, now)
        elif st == SOLD and not n["released"]:
            self._step_release(n, now)
        elif st == SKIPPED and n["late_check"]:
            self._late_check(n, now)

    # -------------------------------------------------------------------- buy
    def _final_gate(self, n: Dict[str, Any]) -> Optional[str]:
        d = self._bd(n)
        if self.mode != "live":
            return osch.MODE_OFF
        if n["symbol"] not in self.enabled:
            return osch.STOCK_OFF
        if self.broker is None:
            return osch.NO_BROKER
        if self._control_active(d):
            return osch.OPERATOR_NO_BUY_TONIGHT
        ok, reason = osch.calendar_gate(d, self.calendar)
        if not ok:
            return reason
        for m in self.state["nights"].values():
            if m["symbol"] == n["symbol"] and m["buy_date"] < n["buy_date"] and not m["released"]:
                return osch.EARLIER_HOLD_UNSOLD
        return None

    def _local_gates(self, n: Dict[str, Any], now: datetime) -> Optional[str]:
        sym = n["symbol"]
        if self.hooks.broker_mismatch():
            return osch.BROKER_MISMATCH
        other = self.hooks.held_by_other(sym)
        if other:
            return osch.HELD_BY_OTHER_STRATEGY
        if not n["entries_cancelled"]:
            n["entries_cancelled"] = True
            self.hooks.cancel_day_entries(sym)
        if self.hooks.day_shares(sym):
            if not n["day_close_requested"]:
                n["day_close_requested"] = True
                self._log(n, "X6_DAY_TRADE_CLOSED_EARLY", now)
                self.hooks.close_day_trade(sym)
            return osch.DAY_TRADE_NOT_CLOSED
        return None

    def _step_buy(self, n: Dict[str, Any], now: datetime) -> None:
        d = self._bd(n)
        give_up = et(d, osch.BUY_GIVE_UP)
        att = n["buy"][-1] if n["buy"] else None
        if att is not None and not att["final"]:
            if att["tif"] == "cls":
                allow = now < give_up and not self._control_active(d)
            else:
                allow = et(d, osch.BUY_FALLBACK_AT) <= now < et(d, FALLBACK_BUY_END) and not self._control_active(d)
            self._buy_attempt_io(n, att, now, allow and self.broker is not None)
            return
        if now < et(d, osch.BUY_WINDOW_START):
            return
        reason = self._final_gate(n)
        if reason is not None:
            self._skip(n, reason, now)
            return
        if n["fallback_buy"]:
            self._fallback_buy(n, now)
            return
        if now >= give_up:
            self._skip(n, n.get("block") or osch.MISSED_BUY_WINDOW, now)
            return
        reason = self._local_gates(n, now)
        if reason is not None:
            self._block(n, reason)
            return
        self._gates_io(n, now)

    def _gates_io(self, n: Dict[str, Any], now: datetime) -> None:
        sym, d = n["symbol"], self._bd(n)
        need_account = self.state["account"].get("date") != d.isoformat()
        broker = self.broker

        def work() -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            try:
                out["cal"] = broker.get_calendar(d.isoformat(), d.isoformat())
            except Exception as exc:
                out["cal_err"] = str(exc)[:200]
            try:
                out["count"], out["has_0930"] = self.bar_count(sym, d)
            except Exception as exc:
                out["relay_err"] = str(exc)[:200]
            try:
                out["price"] = self.last_price(sym)
            except Exception as exc:
                out["price_err"] = str(exc)[:200]
            if need_account:
                try:
                    out["account"] = broker.get_account_fields()
                except Exception as exc:
                    out["account_err"] = str(exc)[:200]
            try:
                out["alpaca_qty"] = broker.position_qty(sym)
                out["open_orders"] = len(broker.list_open_orders(sym))
            except Exception as exc:
                out["flat_err"] = str(exc)[:200]
            try:
                out["margin"] = broker.get_asset_margin(sym)
            except Exception as exc:          # never blocks: the last good read or 50% is used
                out["margin_err"] = str(exc)[:200]
            return out

        done, out, exc = self._io(f"gates:{sym}:{d}", work, now, osch.BUY_RETRY_SEC)
        if not done:
            return
        if exc is not None:
            self._block(n, osch.BROKER_UNREACHABLE)
            return
        rows = out.get("cal")
        ok, reason = osch.alpaca_calendar_gate(d, rows)
        if not ok:
            if reason in osch.FINAL_GATES:
                self._skip(n, reason, now)
            else:
                self._block(n, reason)
            return
        if "relay_err" in out:
            self._block(n, osch.RELAY_UNAVAILABLE)
            return
        n["bars"] = {"count": int(out["count"]), "has_0930": bool(out["has_0930"])}
        ok, reason = osch.buy_gates(d, int(out["count"]), bool(out["has_0930"]), self.calendar, rows, check_alpaca=True)
        if not ok:
            if reason in osch.FINAL_GATES:
                self._skip(n, reason, now)
            else:
                self._block(n, reason)       # bars may still arrive before 15:49:30
            return
        if out.get("account") is not None and self.state["account"].get("date") != d.isoformat():
            self.state["account"] = {"date": d.isoformat(), "read_at": _iso(now), **out["account"]}
            self._log(None, "ACCOUNT_READ", now, **out["account"])
        acct = self.state["account"]
        if acct.get("date") != d.isoformat() or not acct.get("equity") or acct.get("buying_power") is None:
            self._block(n, osch.ACCOUNT_UNAVAILABLE)
            return
        if "flat_err" in out:
            self._block(n, osch.BROKER_UNREACHABLE)
            return
        if out["alpaca_qty"] != 0 or out["open_orders"]:
            self._block(n, osch.BROKER_NOT_FLAT)
            return
        price = out.get("price")
        if price is None or not math.isfinite(float(price)) or float(price) <= 0:
            self._block(n, osch.NO_PRICE)
            return
        ratio = self._margin_from_read(n, out, now)
        qty = self._size(n, float(price), acct, now, ratio)
        if qty < 1:
            self._block(n, osch.NO_ROOM)
            return
        n["block"] = None
        self._new_buy_attempt(n, "cls", now)

    # Reg T style initial margin: a stock Alpaca does not lend on needs its full price, any other
    # at least 50%. Slow trades held tonight are counted at 50%.
    REG_T_MIN = 0.50
    SWING_RATIO = 0.50

    @classmethod
    def _ratio(cls, margin: Optional[Dict[str, Any]]) -> float:
        if not margin:
            return cls.REG_T_MIN
        if not margin.get("marginable"):
            return 1.0
        return max(cls.REG_T_MIN, float(margin.get("margin_requirement_long") or 0.0))

    def _ratio_of(self, m: Dict[str, Any]) -> float:
        """The margin ratio a night was sized with, else its stock's last good read, else 50%."""
        if m.get("margin_ratio"):
            return float(m["margin_ratio"])
        return self._ratio(self.state["margin"].get(m["symbol"]))

    def _margin_from_read(self, n: Dict[str, Any], out: Dict[str, Any], now: datetime) -> float:
        """This stock's margin ratio from tonight's asset read. A failed read uses the last good
        read (saved in the checkpoint); a stock never read counts at 50% and needs a look."""
        sym = n["symbol"]
        if out.get("margin") is not None:
            read = {k: out["margin"].get(k) for k in ("marginable", "margin_requirement_long")}
            prev = self.state["margin"].get(sym) or {}
            if any(prev.get(k) != v for k, v in read.items()):
                self._log(n, "MARGIN_READ", now, **read)
            self.state["margin"][sym] = {**read, "read_at": _iso(now)}
            return self._ratio(read)
        cached = self.state["margin"].get(sym)
        if cached:
            self._log(n, "MARGIN_FROM_LAST_READ", now, error=out.get("margin_err"), read_at=cached.get("read_at"),
                      marginable=cached.get("marginable"), margin_requirement_long=cached.get("margin_requirement_long"))
            return self._ratio(cached)
        self._needs_look(n, osch.MARGIN_UNKNOWN, now, f"margin unknown, assumed 50%. {out.get('margin_err') or ''}".strip())
        return self.REG_T_MIN

    def _size(self, n: Dict[str, Any], price: float, acct: Dict[str, Any], now: datetime, ratio: float) -> int:
        """D2 and D3 with X11. Overnight room, Reg T style: the initial margin of tonight's holds
        (notional x each stock's margin ratio) plus Slow trades held tonight x 50% must fit Alpaca
        equity at 15:46 (times room_multiple / 2, so the default 2.0 is exactly equity). Other
        overnight money is what was sized tonight, still held, or reserved for an earlier stock in
        NVDA, IREN, HUT order. Each order must also fit Alpaca buying power less the other
        overnight orders of tonight."""
        equity, bp = float(acct["equity"]), float(acct["buying_power"])
        wanted = osch.shares_for(equity, price, self.pct, self.cap)
        bound = min(self.pct * equity, self.cap)
        rank = osch.SYMBOLS.index(n["symbol"])
        others = 0.0
        others_margin = 0.0
        for m in self.state["nights"].values():
            if m is n or m["state"] == SKIPPED:
                continue
            if m["buy_date"] != n["buy_date"]:
                if m["state"] in HOLD_STATES:
                    value = self._left(m) * (m["buy_avg"] or 0.0)
                    others += value
                    others_margin += value * self._ratio_of(m)
                continue
            if m.get("qty"):
                value = m["qty"] * m["ref_price"]
            elif m["state"] == IDLE and osch.SYMBOLS.index(m["symbol"]) < rank and m["symbol"] in self.enabled:
                value = bound
            else:
                continue
            others += value
            others_margin += value * self._ratio_of(m)
        swing = float(self.hooks.swing_market_value() or 0.0)
        margin_room = equity * self.room_multiple / 2.0 - swing * self.SWING_RATIO - others_margin
        room = margin_room / ratio
        bp_left = bp - others
        allowed = min(wanted * price, room, bp_left)
        # full size when nothing binds: allowed // price can land one share short in floating point
        qty = wanted if allowed >= wanted * price else (max(0, min(wanted, int(allowed // price))) if allowed > 0 else 0)
        n["wanted_qty"] = wanted
        if qty < wanted:
            n["shrunk"] = {"wanted": wanted, "qty": qty, "price": price, "room": room, "buying_power_left": bp_left,
                           "swing_value": swing, "other_overnight": others, "margin_ratio": ratio,
                           "margin_room": margin_room, "other_overnight_margin": others_margin}
            self._log(n, "X11_SHRUNK", now, **n["shrunk"])
        if qty >= 1:
            n["qty"], n["ref_price"], n["margin_ratio"] = qty, price, ratio
        return qty

    def _new_buy_attempt(self, n: Dict[str, Any], tif: str, now: datetime) -> None:
        k = len(n["buy"]) + 1
        att = {"n": k, "cid": osch.client_id(n["symbol"], self._bd(n), "buy", k), "id": None, "tif": tif,
               "qty": n["qty"], "symbol": n["symbol"], "status": "intent", "filled_qty": 0, "avg": None,
               "final": False, "refusal": None, "sent": False, "ambiguous": False, "cancel_requested": False,
               "created_at": _iso(now)}
        n["buy"].append(att)
        prev = n["state"]
        n["state"] = INTENT
        if not self.checkpoint("OVERNIGHT_BUY_INTENT"):
            n["buy"].pop()
            n["state"] = prev
            self._block(n, osch.INTENT_NOT_DURABLE)
            self._log(n, "INTENT_NOT_DURABLE", now, cid=att["cid"])
            return
        self._log(n, "BUY_INTENT", now, cid=att["cid"], qty=att["qty"], tif=tif, ref_price=n["ref_price"])
        self._buy_attempt_io(n, att, now, allow=True)

    def _fallback_buy(self, n: Dict[str, Any], now: datetime) -> None:
        """D10: after a definite closing auction refusal, a plain market buy at 15:59:30."""
        d = self._bd(n)
        if now < et(d, osch.BUY_FALLBACK_AT):
            return
        if now >= et(d, FALLBACK_BUY_END):
            self._skip(n, osch.BUY_REFUSED, now)
            return
        self._new_buy_attempt(n, "day", now)

    def _lookup(self, att: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if att["id"]:
            return self.broker.get_order(att["id"])
        return self.broker.get_order_by_client_id(att["cid"])

    def _buy_attempt_io(self, n: Dict[str, Any], att: Dict[str, Any], now: datetime, allow: bool) -> None:
        broker, sym = self.broker, n["symbol"]
        if broker is None:
            return
        if allow:
            att["sent"] = True
            n["state"] = BUY_SENT

        def work():
            row = self._lookup(att)
            if row is not None or not allow:
                return "found", row
            try:
                if att["tif"] == "day":
                    row = broker.submit_market_order(sym, "buy", att["qty"], att["cid"])
                else:
                    row = broker.submit_on_auction(sym, "buy", att["qty"], att["cid"], att["tif"])
            except Exception as exc:
                return "post_error", exc
            return "sent", row

        done, res, exc = self._io(f"buy:{sym}:{n['buy_date']}", work, now, osch.BUY_RETRY_SEC)
        if not done:
            return
        if exc is not None:                 # the lookup itself failed: proves nothing, retry
            self._block(n, osch.BROKER_UNREACHABLE)
            return
        kind, value = res
        if kind == "post_error":
            self._buy_refused(n, att, value, now)
            return
        if value is None:                   # Alpaca has no order with this id and none may be sent now
            att.update(final=True, status="not_found")
            n["late_check"] = True          # the POST may still surface; looked up once after the close
            reason = osch.OPERATOR_NO_BUY_TONIGHT if self._control_active(self._bd(n)) else (
                n.get("block") or (osch.BUY_REFUSED if att["tif"] == "day" else osch.MISSED_BUY_WINDOW))
            self._skip(n, reason, now)
            return
        att["ambiguous"] = False
        self._apply_buy_row(n, att, value, now)

    def _buy_refused(self, n: Dict[str, Any], att: Dict[str, Any], exc: Exception, now: datetime) -> None:
        kind = classify_refusal(exc)
        if kind == REFUSAL_AMBIGUOUS:
            att["ambiguous"] = True
            self._block(n, osch.BROKER_UNREACHABLE)
            self._log(n, "BUY_AMBIGUOUS", now, cid=att["cid"], error=str(exc)[:200])
            return
        att.update(final=True, status="refused", refusal=kind)
        n["state"] = IDLE
        self._log(n, "BUY_REFUSED", now, cid=att["cid"], kind=kind, error=str(exc)[:200])
        if kind == REFUSAL_WASH_TRADE:
            self._block(n, osch.WASH_TRADE_REFUSED)
        elif kind == REFUSAL_BUYING_POWER:
            self._block(n, osch.BUYING_POWER_REFUSED)
        elif att["tif"] == "cls":
            n["fallback_buy"] = True        # D10
            self._block(n, osch.BUY_REFUSED)
            self._log(n, "D10_BUY_FALLBACK_AT_1559", now)
        else:
            self._skip(n, osch.BUY_REFUSED, now)

    def _apply_buy_row(self, n: Dict[str, Any], att: Dict[str, Any], row: Dict[str, Any], now: datetime) -> None:
        att["id"] = row.get("id") or att["id"]
        att["status"] = row.get("status")
        self._book_fills(n, att, row, "buy", None, now)
        if not is_terminal(row):
            n["state"] = BUY_ACCEPTED
            n["block"] = None
            return
        att["final"] = True
        if n["held_qty"] > 0:
            self._to_held(n, now)
            return
        status = row.get("status")
        if status == "rejected" and att["tif"] == "cls" and now < et(self._bd(n), osch.BUY_FALLBACK_AT):
            n["state"], n["fallback_buy"] = IDLE, True       # D10 on an asynchronous refusal
            self._log(n, "D10_BUY_FALLBACK_AT_1559", now, status=status)
            return
        if status == "canceled":
            reason = osch.OPERATOR_NO_BUY_TONIGHT if att["cancel_requested"] and self._control_active(
                self._bd(n)) else (osch.AUCTION_NO_FILL if att["cancel_requested"] else osch.CANCELED_AT_ALPACA)
        elif status == "rejected":
            reason = osch.BUY_REFUSED
        else:
            reason = osch.AUCTION_NO_FILL
        self._skip(n, reason, now)

    def _to_held(self, n: Dict[str, Any], now: datetime) -> None:
        n["state"] = HELD
        att = n["buy"][-1]
        if n["held_qty"] < (att["qty"] or 0):
            self._log(n, "X11_PARTIAL_FILL", now, ordered=att["qty"], filled=n["held_qty"])
        n["legs"] = [{"symbol": n["symbol"], "qty": n["held_qty"], "sold": 0, "notional": 0.0, "attempts": [],
                      "mode": "opg"}]
        self._log(n, "HELD", now, qty=n["held_qty"], avg=n["buy_avg"], sale_date=n["sale_date"],
                  nights=osch.holding_nights(self._bd(n), self._sd(n)) if self._sd(n) else None)

    def _step_buy_accepted(self, n: Dict[str, Any], now: datetime) -> None:
        att, d = n["buy"][-1], self._bd(n)
        stuck = now >= et(d, BUY_STUCK_AFTER)
        cancel = (self._control_active(d) and att["tif"] == "cls") or stuck
        if cancel:
            att["cancel_requested"] = True

            def work():
                return self.broker.cancel_order_and_confirm(att["id"])

            done, row, exc = self._io(f"buycancel:{n['symbol']}:{d}", work, now, osch.POLL_SEC)
        else:
            if att["tif"] == "cls" and now < et(d, osch.BUY_POLL_FROM):
                return

            def work():
                return self.broker.get_order(att["id"])

            done, row, exc = self._io(f"buypoll:{n['symbol']}:{d}", work, now, osch.POLL_SEC)
        if not done:
            return
        if exc is not None:
            n["last_error"] = str(exc)[:200]
            return
        self._apply_buy_row(n, att, row, now)

    def _late_check(self, n: Dict[str, Any], now: datetime) -> None:
        """A buy given up after an unanswered POST is looked up once more after the close."""
        if now < et(self._bd(n), osch.BUY_POLL_FROM):
            return
        att = next((a for a in reversed(n["buy"]) if a["status"] == "not_found" or a["ambiguous"]), None)
        if att is None:
            n["late_check"] = False
            return
        done, row, exc = self._io(f"late:{n['symbol']}:{n['buy_date']}", lambda: self._lookup(att), now, osch.POLL_SEC)
        if not done or exc is not None:
            return
        n["late_check"] = False
        if row is None:
            return
        att.update(final=False, ambiguous=False)
        n["released"] = False
        self._needs_look(n, osch.BUY_ORDER_CONTRADICTS, now, "An order thought unsent was found at Alpaca.")
        self._apply_buy_row(n, att, row, now)

    # ---------------------------------------------------------------- booking
    def _book_fills(self, n: Dict[str, Any], att: Dict[str, Any], row: Dict[str, Any], role: str,
                    leg: Optional[Dict[str, Any]], now: datetime) -> None:
        """Book new cumulative fills of one Alpaca order, idempotent per Alpaca order id."""
        oid = row.get("id") or att["id"]
        total, avg = filled_qty(row), filled_avg(row)
        prev = n["booked"].get(oid) or {"qty": 0, "notional": 0.0}
        delta = total - int(prev["qty"])
        if delta < 0:
            self._needs_look(n, osch.BUY_ORDER_CONTRADICTS, now, f"Alpaca order {oid} filled quantity went down.")
            return
        if delta == 0 or not avg > 0:
            return
        notional = total * avg
        px = (notional - float(prev["notional"])) / delta
        if not math.isfinite(px) or px <= 0:
            px = avg
        at = _parse_time(row.get("filled_at") or row.get("updated_at"), now)
        realized = None
        if role == "sell" and leg is not None and leg["symbol"] == n["symbol"] and n["buy_avg"]:
            realized = delta * (px - n["buy_avg"])
        event = {"kind": "fill", "role": role, "strategy_id": n["strategy_id"], "symbol": row.get("symbol") or att["symbol"],
                 "hold_symbol": n["symbol"], "buy_date": n["buy_date"], "qty": delta, "price": px, "at": _iso(at),
                 "alpaca_order_id": oid, "client_order_id": att["cid"], "realized": realized}
        accepted = self.book(event)           # raises -> nothing recorded, retried at the next read
        n["booked"][oid] = {"qty": total, "notional": notional}
        att["filled_qty"], att["avg"] = total, avg
        if role == "buy":
            n["held_qty"] += delta
            n["buy_cost"] += delta * px
            n["buy_avg"] = n["buy_cost"] / n["held_qty"]
        else:
            leg["sold"] += delta
            leg["notional"] += delta * px
            if realized is not None:
                n["realized"] += realized
                self._add_offset(at, realized)
        self._log(n, "BOOKED", now, role=role, qty=delta, price=px, alpaca_order_id=oid, realized=realized)
        if accepted is False:
            self._needs_look(n, osch.BOOKING_REFUSED, now, f"The ledger refused the {role} fill of {delta} shares.")

    def _add_offset(self, at: datetime, amount: float) -> None:
        d = et_date(at).isoformat()
        off = self.state["realized_offset"]
        if off.get("date") != d:
            off["date"], off["amount"] = d, 0.0
        off["amount"] = float(off["amount"]) + amount

    # ------------------------------------------------------------------- sale
    def _step_sale(self, n: Dict[str, Any], now: datetime) -> None:
        if self.broker is None:
            self._needs_look(n, osch.NO_BROKER, now, "No broker is attached, so the hold cannot be sold.")
            return
        sd = self._sd(n)
        if sd is None:
            sd = self.calendar.next_trading_day(self._bd(n))   # raises when not covered: shown as last_error
            n["sale_date"] = sd.isoformat()
        if not n["morning_checked"] and now >= et(sd, osch.MORNING_CHECK):
            self._morning_check(n, now)
            if not n["morning_checked"]:
                return
        for i, leg in enumerate(n["legs"]):
            self._step_leg(n, i, leg, now)
        if all(leg["sold"] >= leg["qty"] and all(a["final"] for a in leg["attempts"]) for leg in n["legs"]):
            n["state"] = SOLD
            self._log(n, "SOLD", now, realized=n["realized"], legs=[(l["symbol"], l["sold"]) for l in n["legs"]])
        elif any(not a["final"] for leg in n["legs"] for a in leg["attempts"]):
            n["state"] = SALE_QUEUED
        else:
            n["state"] = HELD

    def _sale_window(self, n: Dict[str, Any], now: datetime) -> Optional[str]:
        """Which new sale may be sent now: "queue" (opg, or the D10 queued market sale),
        "fallback" (plain market with Alpaca's shares checked) or None (wait)."""
        bd, sd = self._bd(n), self._sd(n)
        if now < et(bd, osch.SALE_QUEUE_AT):
            return None
        if now < et(sd, osch.SALE_REPLACE_DEADLINE):
            return "queue"
        if now < et(sd, osch.SALE_FALLBACK_AT):
            return None
        return "fallback"

    def _step_leg(self, n: Dict[str, Any], i: int, leg: Dict[str, Any], now: datetime) -> None:
        att = leg["attempts"][-1] if leg["attempts"] else None
        if att is not None and not att["final"]:
            self._poll_sale(n, i, leg, att, now)
            return
        left = leg["qty"] - leg["sold"]
        if left <= 0:
            return
        window = self._sale_window(n, now)
        if window is None:
            return
        if window == "queue" and n["legs"][0] is leg and not self._sale_calendar_checked(n, now):
            return
        k = len(leg["attempts"]) + 1
        tif = "opg" if window == "queue" and leg["mode"] == "opg" else "day"
        new = {"n": k, "cid": osch.client_id(leg["symbol"], self._bd(n), "sell", k), "id": None, "tif": tif,
               "qty": left, "symbol": leg["symbol"], "status": "intent", "filled_qty": 0, "avg": None,
               "final": False, "refusal": None, "sent": False, "ambiguous": False, "cancel_wanted": False,
               "cancel_requested": False, "fallback": window == "fallback", "created_at": _iso(now)}
        # throttle: a new attempt at most every 30 s after the last one started
        last = self._last.get(f"sellsend:{leg['symbol']}:{n['buy_date']}")
        if last is not None and (now - last).total_seconds() < osch.SALE_RETRY_SEC:
            return
        leg["attempts"].append(new)
        self.checkpoint("OVERNIGHT_SALE_INTENT")   # best effort only (R2)
        self._log(n, "SALE_INTENT", now, cid=new["cid"], qty=left, tif=tif, fallback=new["fallback"])
        self._sale_attempt_io(n, leg, new, now)

    def _sale_calendar_checked(self, n: Dict[str, Any], now: datetime) -> bool:
        """At 19:00:30 cross check the sale date with Alpaca's calendar once. A failed read never
        blocks the sale: after one try the robot's own calendar is used."""
        if n.get("sale_calendar_checked"):
            return True
        bd = self._bd(n)
        broker = self.broker
        done, rows, exc = self._io(f"salecal:{n['symbol']}:{n['buy_date']}",
                                   lambda: broker.get_calendar((bd + timedelta(days=1)).isoformat(),
                                                               (bd + timedelta(days=10)).isoformat()), now, 0)
        if not done:
            return False
        n["sale_calendar_checked"] = True
        nxt = osch.alpaca_next_session(bd, rows) if exc is None else None
        if exc is not None:
            self._log(n, "SALE_CALENDAR_UNREAD", now, error=str(exc)[:200])
        elif nxt is not None and nxt.isoformat() != n["sale_date"]:
            self._log(n, "SALE_DATE_FROM_ALPACA", now, ours=n["sale_date"], alpaca=nxt.isoformat())
            self._needs_look(n, osch.CALENDAR_DISAGREES, now, f"Alpaca's next session is {nxt.isoformat()}.")
            n["sale_date"] = nxt.isoformat()
        return True

    def _tif_open(self, n: Dict[str, Any], att: Dict[str, Any], now: datetime) -> bool:
        """May this attempt's id still be (re)sent now?"""
        window = self._sale_window(n, now)
        if att["fallback"]:
            return window == "fallback"
        return window == "queue"

    def _sale_attempt_io(self, n: Dict[str, Any], leg: Dict[str, Any], att: Dict[str, Any], now: datetime) -> None:
        broker, sym = self.broker, leg["symbol"]
        allow = self._tif_open(n, att, now)
        if allow:
            att["sent"] = True
        wanted = att["qty"]

        def work():
            row = self._lookup(att)
            if row is not None or not allow:
                return "found", row, None
            qty = wanted
            if att["fallback"]:
                held = broker.position_qty(sym)
                reserved = sum(int(float(o.get("qty") or 0)) - filled_qty(o) for o in broker.list_open_orders(sym)
                               if o.get("side") == "sell")
                qty = min(wanted, held - reserved)
                if qty < 1:
                    return "no_shares", None, held
            try:
                if att["tif"] == "opg":
                    row = broker.submit_on_auction(sym, "sell", qty, att["cid"], "opg")
                else:
                    row = broker.submit_market_order(sym, "sell", qty, att["cid"])
            except Exception as exc:
                return "post_error", exc, qty
            return "sent", row, qty

        self._last[f"sellsend:{sym}:{n['buy_date']}"] = now
        done, res, exc = self._io(f"sell:{sym}:{n['buy_date']}", work, now, osch.BUY_RETRY_SEC)
        if not done:
            return
        if exc is not None:
            att["ambiguous"] = True
            n["last_error"] = str(exc)[:200]
            return
        kind, value, qty = res
        if qty is not None and kind in ("sent", "post_error"):
            att["qty"] = qty
        if kind == "no_shares":
            leg["attempts"].remove(att)
            self._needs_look(n, osch.SHARES_UNEXPLAINED, now, f"Alpaca shows {qty} {sym} shares free to sell.")
            return
        if kind == "post_error":
            refusal = classify_refusal(value)
            if refusal == REFUSAL_AMBIGUOUS:
                att["ambiguous"] = True
                self._log(n, "SALE_AMBIGUOUS", now, cid=att["cid"], error=str(value)[:200])
                return
            att.update(final=True, status="refused", refusal=refusal)
            self._log(n, "SALE_REFUSED", now, cid=att["cid"], tif=att["tif"], kind=refusal, error=str(value)[:200])
            if att["tif"] == "opg":
                leg["mode"] = "market"      # D10: a plain day market sale, queued by Alpaca for the open
                self._log(n, "D10_SALE_FALLBACK_MARKET", now)
            else:
                self._needs_look(n, osch.SALE_REFUSED, now, f"Alpaca refused the market sale: {refusal}.")
            return
        if value is None:
            att.update(final=True, status="not_found")   # never landed and may not be resent now
            return
        att["ambiguous"] = False
        self._apply_sale_row(n, leg, att, value, now)

    def _apply_sale_row(self, n: Dict[str, Any], leg: Dict[str, Any], att: Dict[str, Any], row: Dict[str, Any],
                        now: datetime) -> None:
        att["id"] = row.get("id") or att["id"]
        att["status"] = row.get("status")
        att["sent"] = True
        self._book_fills(n, att, row, "sell", leg, now)
        if not is_terminal(row):
            return
        att["final"] = True
        if row.get("status") == "canceled" and not att["cancel_requested"] and leg["sold"] < leg["qty"]:
            self._log(n, "SALE_CANCELED_AT_ALPACA", now, cid=att["cid"])

    def _poll_sale(self, n: Dict[str, Any], i: int, leg: Dict[str, Any], att: Dict[str, Any], now: datetime) -> None:
        if not att["id"]:
            self._sale_attempt_io(n, leg, att, now)
            return
        sd = self._sd(n)
        if att["tif"] == "opg" and now >= et(sd, osch.SALE_FALLBACK_AT) and not att["cancel_wanted"]:
            att["cancel_wanted"] = True      # X7: an opening sale not filled by 09:31 is cancelled first
            self._log(n, "X7_SALE_NOT_FILLED_BY_0931", now, cid=att["cid"])
        key = f"sellpoll:{leg['symbol']}:{n['buy_date']}"
        if att["cancel_wanted"]:
            att["cancel_requested"] = True
            done, row, exc = self._io(key, lambda: self.broker.cancel_order_and_confirm(att["id"]), now, osch.POLL_SEC)
        else:
            if now < et(sd, osch.SALE_POLL_FROM):
                return
            done, row, exc = self._io(key, lambda: self.broker.get_order(att["id"]), now, osch.POLL_SEC)
        if not done:
            return
        if exc is not None:
            n["last_error"] = str(exc)[:200]
            return
        self._apply_sale_row(n, leg, att, row, now)

    def _morning_check(self, n: Dict[str, Any], now: datetime) -> None:
        """09:00: Alpaca's shares equal the ledger and the queued sale is live and for that count.
        Otherwise a matching split adjusts the hold; anything else needs a look and sells
        min(hold, Alpaca) of the old and any new symbol (never more than the hold)."""
        leg0 = n["legs"][0]
        sym, bd, sd = n["symbol"], self._bd(n), self._sd(n)
        att = leg0["attempts"][-1] if leg0["attempts"] and not leg0["attempts"][-1]["final"] else None
        left = leg0["qty"] - leg0["sold"]
        sold0, qty0 = leg0["sold"], leg0["qty"]
        booked = {k: int(v["qty"]) for k, v in n["booked"].items()}
        broker = self.broker

        def work():
            row = self._lookup(att) if att is not None else None
            qty = broker.position_qty(sym)
            if row is not None and filled_qty(broker.get_order(row["id"])) != filled_qty(row):
                return {"race": True}
            unbooked = filled_qty(row) - booked.get(row["id"], 0) if row is not None else 0
            out = {"row": row, "qty": qty, "left": qty0 - sold0 - unbooked}
            if qty != out["left"]:
                try:
                    out["actions"] = broker.get_corporate_actions(sym, bd.isoformat(), sd.isoformat())
                    news = {a["new_symbol"] for a in out["actions"] if a.get("new_symbol") not in (None, sym)}
                    out["new_qty"] = {s: broker.position_qty(s) for s in sorted(news)}
                except Exception as exc:
                    out["ca_err"] = str(exc)[:200]
            return out

        done, out, exc = self._io(f"morning:{sym}:{n['buy_date']}", work, now, osch.SALE_RETRY_SEC)
        if not done:
            return
        if exc is not None:
            n["last_error"] = str(exc)[:200]
            if now >= et(sd, osch.SALE_REPLACE_DEADLINE):
                n["morning_checked"] = True      # never let a failed read hold the sale back
                self._log(n, "MORNING_CHECK_UNREAD", now, error=n["last_error"])
            return
        if out.get("race"):
            return
        if out["row"] is not None:
            self._apply_sale_row(n, leg0, att, out["row"], now)
        left = leg0["qty"] - leg0["sold"]
        alpaca = int(out["qty"])
        live = att if att is not None and not att["final"] else None
        if alpaca == left:
            n["morning_checked"] = True
            self._log(n, "MORNING_CHECK_OK", now, shares=alpaca, sale_live=live is not None)
            if live is not None and live["qty"] != left:
                live["cancel_wanted"] = True
            return
        if "ca_err" in out and now < et(sd, osch.SALE_REPLACE_DEADLINE):
            n["block"] = osch.CORPORATE_ACTIONS_UNAVAILABLE    # retried every 30 s until 09:27:30
            return
        actions = out.get("actions") or []
        split = None
        for a in actions:
            ratio = a.get("ratio")
            if (a.get("kind") in ("split", "reverse_split") and ratio and a.get("old_symbol") == sym
                    and a.get("new_symbol") == sym and leg0["sold"] == 0
                    and abs(left * ratio - alpaca) < 1e-6 and abs(left * ratio - round(left * ratio)) < 1e-6):
                split = a
                break
        n["morning_checked"] = True
        if split is not None:
            ratio = float(split["ratio"])
            old_qty, old_avg = n["held_qty"], n["buy_avg"]
            n["held_qty"] = leg0["qty"] = alpaca
            n["buy_avg"] = n["buy_cost"] / n["held_qty"]
            n["splits"].append({"ratio": ratio, "old_qty": old_qty, "new_qty": alpaca, "old_avg": old_avg,
                                "new_avg": n["buy_avg"], "ex_date": split.get("ex_date"), "at": _iso(now)})
            self._log(n, "SPLIT", now, text=(f"{sym} split {ratio:g} for 1 overnight. The hold is now {alpaca} shares "
                                             f"at ${n['buy_avg']:.2f} each. The sale was replaced for {alpaca} shares."))
            self.book({"kind": "split", "strategy_id": n["strategy_id"], "symbol": sym, "buy_date": n["buy_date"],
                       "ratio": ratio, "old_qty": old_qty, "new_qty": alpaca, "new_avg": n["buy_avg"]})
            if live is not None:
                live["cancel_wanted"] = True
            return
        reason = osch.CORPORATE_ACTION if actions else (
            osch.CORPORATE_ACTIONS_UNAVAILABLE if "ca_err" in out else osch.SHARES_UNEXPLAINED)
        self._needs_look(n, reason, now, f"Alpaca holds {alpaca} {sym}, the hold is {left}. "
                                        f"Actions: {[(a.get('kind'), a.get('new_symbol'), a.get('ratio')) for a in actions]}.")
        leg0["qty"] = leg0["sold"] + max(0, min(left, alpaca))
        if live is not None and live["qty"] != leg0["qty"] - leg0["sold"]:
            live["cancel_wanted"] = True
        for s, q in (out.get("new_qty") or {}).items():
            q = max(0, min(left, int(q)))
            if q and not any(leg["symbol"] == s for leg in n["legs"]):
                n["legs"].append({"symbol": s, "qty": q, "sold": 0, "notional": 0.0, "attempts": [],
                                  "mode": leg0["mode"]})
                self._log(n, "SALE_LEG_NEW_SYMBOL", now, new_symbol=s, qty=q)

    def _step_release(self, n: Dict[str, Any], now: datetime) -> None:
        """Release the stock only when Alpaca shows 0 shares and no open overnight order."""
        syms = sorted({leg["symbol"] for leg in n["legs"]} | {n["symbol"]})
        broker = self.broker
        if broker is None:
            return

        def work():
            out = {}
            for s in syms:
                opens = [o for o in broker.list_open_orders(s)
                         if str(o.get("client_order_id") or "").startswith(OPEN_ORDER_PREFIX)]
                out[s] = (broker.position_qty(s), len(opens))
            return out

        every = osch.SALE_RETRY_SEC if osch.SHARES_UNEXPLAINED in n["needs_look"] else osch.POLL_SEC
        done, out, exc = self._io(f"release:{n['symbol']}:{n['buy_date']}", work, now, every)
        if not done or exc is not None:
            return
        if all(q == 0 and k == 0 for q, k in out.values()):
            self._release(n, now)
        else:
            self._needs_look(n, osch.SHARES_UNEXPLAINED, now, f"After the sale Alpaca shows {out}.")

    # -------------------------------------------------------------- restart
    def reconcile(self, now: Optional[datetime] = None) -> None:
        """Startup, after the session boundary check and before the first position compare: ask
        Alpaca about every known client id and book whatever filled while the robot was down.
        Sends nothing and runs synchronously (the event loop is not serving yet)."""
        now = now or self.clock()
        self._jobs.clear()
        if self.broker is None:
            return
        for n in self._nights():
            try:
                for att in n["buy"]:
                    if att["final"]:
                        continue
                    row = self._lookup(att)
                    if row is not None:
                        att["ambiguous"] = False
                        self._apply_buy_row(n, att, row, now)
                for leg in n["legs"]:
                    for att in leg["attempts"]:
                        if att["final"]:
                            continue
                        row = self._lookup(att)
                        if row is not None:
                            att["ambiguous"] = False
                            self._apply_sale_row(n, leg, att, row, now)
                if n["state"] in HOLD_STATES and n["legs"] and all(
                        leg["sold"] >= leg["qty"] and all(a["final"] for a in leg["attempts"]) for leg in n["legs"]):
                    n["state"] = SOLD
                    self._log(n, "SOLD", now, realized=n["realized"], at_restart=True)
            except Exception as exc:
                n["last_error"] = f"reconcile: {type(exc).__name__}: {exc}"[:300]
                log.warning("OVERNIGHT reconcile %s %s failed: %s", n["symbol"], n["buy_date"], exc)
        self._log(None, "RECONCILED", now)
