# @steered SNARE-2 2026-09-30
"""Durable SPY turn of month and COIN bitcoin follow order controller.

The controller owns broker identities, mutation intents, fill watermarks, recovery,
and process write fences. Market research and account integration enter only through
injected callbacks, so tests and replays never need credentials or network access.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend.app.core import day_one_schedule as d1
from backend.app.core.overnight_schedule import TradingWindowsCalendar

log = logging.getLogger("day_one_execution")

STATE_VERSION = 1
LOG_LIMIT = 300
ORDER_PREFIXES = ("adt-tom-spy-", "adt-btc-coin-")
TERMINAL = frozenset({"filled", "canceled", "expired", "rejected"})
LIVE = frozenset({"intent", "ambiguous", "accepted", "partially_filled", "done_for_day"})
AMBIGUOUS_HTTP = frozenset({408, 409, 425, 429})


class InlineExecutor:
    """Run injected work immediately for deterministic tests and replays."""

    def submit(self, work: Callable[[], Any]) -> Future:
        future: Future = Future()
        try:
            future.set_result(work())
        except Exception as exc:
            future.set_exception(exc)
        return future


class QuantityError(ValueError):
    """A broker or local share quantity was not a finite whole number."""


def _whole_quantity(value: Any, field: str, *, signed: bool = True) -> int:
    try:
        quantity = Decimal(str(0 if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise QuantityError(f"{field} is not a valid quantity") from exc
    if not quantity.is_finite() or quantity != quantity.to_integral_value():
        raise QuantityError(f"{field} must be a finite whole number")
    parsed = int(quantity)
    if not signed and parsed < 0:
        raise QuantityError(f"{field} must be nonnegative")
    return parsed


def _filled_qty(row: Dict[str, Any]) -> int:
    return _whole_quantity(row.get("filled_qty"), "broker filled quantity", signed=False)


def _filled_avg(row: Dict[str, Any]) -> float:
    return float(row.get("filled_avg_price") or 0.0)


def _none(*_args: Any, **_kwargs: Any) -> Optional[str]:
    return None


def _true() -> bool:
    return True


def _buying_power(account: Dict[str, Any], _symbol: str) -> float:
    return float(account.get("buying_power") or 0.0)


def _unknown_quantity(_symbol: str, _strategy_id: str) -> Optional[int]:
    return None


def _strict_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False, separators=(",", ":")))


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_datetime(value: Any, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed.tzinfo is not None else fallback


def _side_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _normalized_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in ("new", "pending_new", "accepted", "pending_replace", "stopped", "suspended", "calculated"):
        return "accepted"
    if status in ("partially_filled", "filled", "canceled", "expired", "rejected", "done_for_day"):
        return status
    raise ValueError("broker order has an unsupported status")


def _broker_position(broker: Any, symbol: str) -> int:
    raw = getattr(broker, "get_positions_raw", None)
    if callable(raw):
        matches = [row for row in raw() if str(row.get("symbol") or "").upper() == symbol.upper()]
        if len(matches) > 1:
            raise QuantityError("broker returned duplicate positions for one symbol")
        return _whole_quantity(matches[0].get("qty"), "broker position quantity") if matches else 0
    return _whole_quantity(broker.position_qty(symbol), "broker position quantity")


def _validate_open_order_quantities(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("broker open orders must be a list")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("broker open order must be an object")
        if row.get("qty") not in (None, ""):
            _whole_quantity(row.get("qty"), "broker order quantity", signed=False)
        _filled_qty(row)
    return rows


def _is_strategy_order(row: Dict[str, Any]) -> bool:
    cid = str(row.get("client_order_id") or "")
    return cid.startswith(ORDER_PREFIXES)


def _ambiguous_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in AMBIGUOUS_HTTP or status_code >= 500
    name = type(exc).__name__.lower()
    return "transport" in name or "timeout" in name or not isinstance(exc, (ValueError, TypeError))


def _duplicate_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    body = str(getattr(exc, "body", "") or "").lower()
    return status_code in (400, 422) and "client_order_id" in body and "unique" in body


@dataclass
class DayOneHooks:
    """Event loop safe gates supplied by the integration layer."""

    held_by_other: Callable[[str], Optional[str]] = _none
    broker_mismatch: Callable[[], bool] = lambda: False
    persistence_healthy: Callable[[], bool] = _true
    admission: Callable[[str, str, int, float], Optional[str]] = _none
    usable_buying_power: Callable[[Dict[str, Any], str], float] = _buying_power
    local_quantity: Callable[[str, str], Optional[int]] = _unknown_quantity
    on_release: Callable[[str], None] = lambda _symbol: None
    ownership_lock: Callable[[], Any] = nullcontext


class DayOneController:
    """One durable lifecycle for SPY and one for COIN."""

    def __init__(
        self,
        *,
        broker: Any,
        executor: Any,
        clock: Callable[[], datetime],
        checkpoint: Callable[[str], int],
        book: Callable[[Dict[str, Any]], bool],
        spy_reference: Callable[[date], Any],
        coin_model: Callable[[date], Dict[str, Any]],
        calendar: Any = None,
        hooks: Optional[DayOneHooks] = None,
        expected_account: str = d1.EXPECTED_ACCOUNT,
        checkpoint_revision: int = 0,
        state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.broker = broker
        self.executor = executor
        self.clock = clock
        self._checkpoint_cb = checkpoint
        self.book = book
        self.spy_reference = spy_reference
        self.coin_model = coin_model
        self.calendar = calendar or TradingWindowsCalendar()
        self.hooks = hooks or DayOneHooks()
        self.expected_account = expected_account
        self._durable_revision = int(checkpoint_revision)
        self.state = _strict_copy(state) if state is not None else self.empty_state()
        self._jobs: Dict[str, Tuple[str, Future]] = {}
        self._gate_retry_at: Dict[str, datetime] = {}
        self._entry_writes_open = True
        self._exit_writes_open = True
        self._reconciling = False
        self._validate_state(self.state)

    @staticmethod
    def empty_state() -> Dict[str, Any]:
        return {"version": STATE_VERSION, "lifecycles": {}, "log": []}

    @classmethod
    def load(cls, data: Optional[Dict[str, Any]], **deps: Any) -> "DayOneController":
        if not data:
            return cls(**deps)
        cls._validate_state(data)
        return cls(state=data, **deps)

    from_json = load

    def to_json(self) -> Dict[str, Any]:
        self._validate_state(self.state)
        return _strict_copy(self.state)

    @staticmethod
    def _validate_state(data: Dict[str, Any]) -> None:
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            raise ValueError("unknown day one state version")
        lifecycles = data.get("lifecycles")
        if not isinstance(lifecycles, dict) or set(lifecycles) - {"SPY", "COIN"}:
            raise ValueError("day one lifecycles must contain only SPY and COIN")
        rows = data.get("log")
        if not isinstance(rows, list) or len(rows) > LOG_LIMIT:
            raise ValueError("day one log is invalid or unbounded")
        for symbol, lifecycle in lifecycles.items():
            d1.validate_lifecycle(lifecycle)
            if lifecycle["symbol"] != symbol:
                raise ValueError("day one lifecycle key does not match its symbol")
            try:
                session = date.fromisoformat(lifecycle["session"])
                dtime.fromisoformat(lifecycle["session_close"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("day one lifecycle has invalid session fields") from exc
            side = lifecycle.get("side")
            if side not in (None, "LONG", "SHORT"):
                raise ValueError("day one lifecycle has invalid side")
            seen: set[str] = set()
            live_roles: Dict[str, int] = {}
            for attempt in lifecycle["attempts"]:
                role = attempt["role"]
                number = attempt.get("attempt")
                expected = d1.client_id(lifecycle["strategy_id"], session, role, number)
                if attempt["client_id"] != expected or expected in seen:
                    raise ValueError("day one attempt client id is not deterministic and unique")
                seen.add(expected)
                for key in ("max_remaining_qty", "filled_qty", "booked_qty"):
                    if not isinstance(attempt.get(key), int) or attempt[key] < 0:
                        raise ValueError("day one attempt quantities must be nonnegative integers")
                if not 0 <= attempt["booked_qty"] <= attempt["filled_qty"] <= attempt["max_remaining_qty"]:
                    raise ValueError("day one attempt fill watermark is inconsistent")
                if attempt["status"] in LIVE:
                    live_roles[role] = live_roles.get(role, 0) + 1
                cancel = attempt.get("cancel_intent")
                if cancel is not None and (not isinstance(cancel, dict) or cancel.get("status") not in
                                               ("intent", "ambiguous", "terminal")):
                    raise ValueError("day one cancel intent is invalid")
            if any(count > 1 for count in live_roles.values()):
                raise ValueError("day one lifecycle has multiple live attempts for one role")
            if lifecycle.get("released") and (
                lifecycle["entry_qty"] != lifecycle["exit_qty"]
                or lifecycle["phase"] not in ("DONE", "SKIPPED")
            ):
                raise ValueError("released day one lifecycle is not terminal and flat")
        _strict_copy(data)

    def checkpoint(self, reason: str = "DAY_ONE") -> bool:
        """Persist the current state and require a real revision advance."""
        try:
            revision = self._checkpoint_cb(reason)
        except Exception as exc:
            self._record(None, "CHECKPOINT_FAILED", self.clock(), error_class=type(exc).__name__)
            return False
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= self._durable_revision:
            self._record(None, "CHECKPOINT_NOT_ADVANCED", self.clock())
            return False
        self._durable_revision = revision
        return True

    def _record(self, lifecycle: Optional[Dict[str, Any]], event: str, now: datetime, **fields: Any) -> None:
        allowed = {
            "role", "side", "quantity", "status", "reason", "client_id", "order_id", "error_class",
            "phase", "price", "revision", "time_in_force", "execution_deviation",
        }
        row: Dict[str, Any] = {"at": _iso(now), "event": str(event)[:64]}
        if lifecycle is not None:
            row.update(
                correlation_id=f"{lifecycle['symbol']}:{lifecycle['session']}",
                strategy=lifecycle["strategy_id"],
                session=lifecycle["session"],
            )
        for key, value in fields.items():
            if key in allowed and value is not None:
                row[key] = value
        self.state["log"].append(_strict_copy(row))
        del self.state["log"][:-LOG_LIMIT]
        log.info("DAY_ONE %s", json.dumps(row, sort_keys=True, separators=(",", ":")))

    def _new_lifecycle(self, symbol: str, session: date, close: dtime, phase: str) -> Dict[str, Any]:
        sid = d1.SPY_ID if symbol == "SPY" else d1.COIN_ID
        return {
            "symbol": symbol,
            "strategy_id": sid,
            "session": session.isoformat(),
            "session_close": close.strftime("%H:%M"),
            "phase": phase,
            "side": None,
            "target_qty": 0,
            "entry_qty": 0,
            "exit_qty": 0,
            "reference_price": None,
            "sizing_equity": None,
            "sizing_buying_power": None,
            "sizing_limit": None,
            "entry_notional": 0.0,
            "exit_notional": 0.0,
            "decision": None,
            "decision_saved": False,
            "operational_gates": [],
            "attempts": [],
            "actual_legs": [],
            "execution_deviations": [],
            "emergency": None,
            "protection": None,
            "fallback_eligible": False,
            "admitted": False,
            "released": False,
            "last_error": None,
            "account_match": None,
        }

    @staticmethod
    def _session(lifecycle: Dict[str, Any]) -> date:
        return date.fromisoformat(lifecycle["session"])

    @staticmethod
    def _close(lifecycle: Dict[str, Any]) -> dtime:
        return dtime.fromisoformat(lifecycle["session_close"])

    def _local_sessions(self, center: date) -> List[date]:
        out: List[date] = []
        for offset in range(-45, 46):
            candidate = center + timedelta(days=offset)
            try:
                if self.calendar.is_trading_day(candidate):
                    out.append(candidate)
            except Exception:
                return []
        return out

    def _spy_target(self, now: datetime) -> Optional[date]:
        local = now.astimezone(d1.ET)
        today = local.date()
        try:
            is_session = self.calendar.is_trading_day(today)
        except Exception:
            return None
        if is_session and local.time() >= d1.SPY_OPG_FROM:
            try:
                target = self.calendar.next_trading_day(today)
            except Exception:
                return None
        elif is_session and local.time() < d1.OPG_CUTOFF:
            target = today
        else:
            return None
        sessions = self._local_sessions(target)
        return target if sessions and d1.turn_month_eligible(target, sessions) else None

    def _replace_if_released(self, symbol: str, session: date) -> bool:
        current = self.state["lifecycles"].get(symbol)
        if current is None:
            return True
        if current["session"] == session.isoformat():
            return False
        return bool(current.get("released"))

    def reserve_coin_before_events(
        self, session: Optional[date] = None, conflict: Optional[str] = None
    ) -> bool:
        """Durably claim COIN before subscriptions or save a nonowning conflict skip."""
        now = self.clock()
        local = now.astimezone(d1.ET)
        session = session or local.date()
        current = self.state["lifecycles"].get("COIN")
        if current is not None and current["session"] == session.isoformat():
            return self.claimed("COIN")
        if not self._replace_if_released("COIN", session):
            return False
        try:
            if not self.calendar.is_trading_day(session):
                return False
            close = self.calendar.session_close(session)
        except Exception:
            return False
        lifecycle = self._new_lifecycle("COIN", session, close, "RESERVED")
        self.state["lifecycles"]["COIN"] = lifecycle
        if conflict:
            lifecycle["phase"] = "SKIPPED"
            lifecycle["released"] = True
            lifecycle["operational_gates"].append("HELD_BY_OTHER_STRATEGY")
            self._record(lifecycle, "SKIPPED", now, reason="HELD_BY_OTHER_STRATEGY", phase="SKIPPED")
            if not self.checkpoint("DAY_ONE_COIN_CONFLICT"):
                self.state["lifecycles"].pop("COIN", None)
            return False
        if not self.checkpoint("DAY_ONE_COIN_RESERVATION"):
            self.state["lifecycles"].pop("COIN", None)
            return False
        self._record(lifecycle, "RESERVED", now, phase="RESERVED")
        return True

    def owns(self, symbol: str) -> bool:
        lifecycle = self.state["lifecycles"].get(symbol.upper())
        if lifecycle is None or lifecycle.get("released"):
            return False
        if lifecycle["symbol"] == "COIN":
            return lifecycle["phase"] not in ("DONE", "SKIPPED")
        return lifecycle["phase"] not in ("IDLE", "DONE", "SKIPPED") or bool(lifecycle["attempts"])

    claimed = owns

    def occupied_reason(self, symbol: str) -> Optional[str]:
        return f"{symbol.upper()} is owned by its day one lifecycle" if self.owns(symbol) else None

    def commitments(self, exclude_symbol: Optional[str] = None) -> Dict[str, float]:
        """Return pending or held day one slot commitments for shared intraday admission."""
        excluded = str(exclude_symbol or "").upper()
        out: Dict[str, float] = {}
        for lifecycle in self.state["lifecycles"].values():
            symbol = lifecycle["symbol"]
            if symbol == excluded or lifecycle.get("released") or lifecycle["phase"] in ("DONE", "SKIPPED"):
                continue
            pending = lifecycle.get("admitted") or lifecycle["entry_qty"] > lifecycle["exit_qty"] or any(
                attempt["role"] == "entry" and attempt["status"] in LIVE for attempt in lifecycle["attempts"]
            )
            if pending:
                out[symbol] = max(
                    0.0,
                    float(lifecycle.get("target_qty") or lifecycle.get("entry_qty") or 0)
                    * float(lifecycle.get("reference_price") or 0.0),
                )
        return out

    @staticmethod
    def _has_durable_protection(lifecycle: Dict[str, Any]) -> bool:
        protection = lifecycle.get("protection")
        if not isinstance(protection, dict) or protection.get("durable") is not True:
            return False
        return all(isinstance(protection.get(key), str) and protection[key] for key in (
            "exit_client_id", "recovery_client_id", "side"
        ))

    def protected(self, symbol: str) -> bool:
        lifecycle = self.state["lifecycles"].get(symbol.upper())
        if lifecycle is None:
            return False
        if lifecycle["entry_qty"] == lifecycle["exit_qty"]:
            return True
        confirmed_close = any(
            attempt["role"] in ("exit", "recovery") and (
                attempt["status"] in ("accepted", "partially_filled", "done_for_day")
                or (attempt["status"] == "ambiguous" and attempt.get("sent") is True)
            )
            for attempt in lifecycle["attempts"]
        )
        return confirmed_close

    def has_exposure(self) -> bool:
        return any(
            lifecycle["entry_qty"] > lifecycle["exit_qty"]
            for lifecycle in self.state["lifecycles"].values()
        )

    def reserved_risk(self, remaining_daily_loss: float) -> float:
        if not isinstance(remaining_daily_loss, (int, float)) or not math.isfinite(remaining_daily_loss):
            return 0.0
        active = any(
            not lifecycle.get("released")
            and (lifecycle["entry_qty"] > lifecycle["exit_qty"] or any(a["status"] in LIVE for a in lifecycle["attempts"]))
            for lifecycle in self.state["lifecycles"].values()
        )
        return max(0.0, float(remaining_daily_loss)) if active else 0.0

    def on_bar(self, bar: Any) -> bool:
        symbol = str(getattr(bar, "symbol", "") or "").upper()
        if symbol not in ("SPY", "COIN"):
            return False
        try:
            ts = getattr(bar, "timestamp")
            values = [float(getattr(bar, key)) for key in ("open", "high", "low", "close")]
            if ts.tzinfo is None or not all(math.isfinite(v) and v > 0 for v in values):
                return False
        except (AttributeError, TypeError, ValueError):
            return False
        now = self.clock()
        completed = ts + timedelta(minutes=1)
        if completed > now + timedelta(seconds=1):
            return False
        cache = getattr(self, "_prices", {})
        cache[symbol] = {"price": values[-1], "at": completed, "kind": "bar"}
        self._prices = cache
        local_bar = ts.astimezone(d1.ET)
        if symbol == "COIN" and d1.MARKET_OPEN <= local_bar.time() <= dtime(9, 34):
            lows = getattr(self, "_coin_lows", {})
            day = local_bar.date().isoformat()
            lows[day] = min(float(lows.get(day, values[2])), values[2])
            self._coin_lows = lows
        return True

    def on_quote(self, quote: Any) -> bool:
        symbol = str(getattr(quote, "symbol", "") or "").upper()
        if symbol not in ("SPY", "COIN"):
            return False
        try:
            bid = float(getattr(quote, "bid_price"))
            ask = float(getattr(quote, "ask_price"))
            ts = getattr(quote, "timestamp")
            if ts.tzinfo is None or not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
                return False
        except (AttributeError, TypeError, ValueError):
            return False
        now = self.clock()
        if ts > now + timedelta(seconds=1):
            return False
        cache = getattr(self, "_prices", {})
        cache[symbol] = {"price": (bid + ask) / 2.0, "at": ts, "kind": "quote"}
        self._prices = cache
        return True

    def _fresh_coin_price(self, now: datetime) -> Optional[float]:
        row = getattr(self, "_prices", {}).get("COIN")
        if not row:
            return None
        age = (now - row["at"]).total_seconds()
        maximum = 2.0 if row["kind"] == "quote" else 90.0
        return float(row["price"]) if 0 <= age <= maximum else None

    def _signature(self) -> str:
        return json.dumps(self.state["lifecycles"], sort_keys=True, separators=(",", ":"), allow_nan=False)

    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or self.clock()
        if now.tzinfo is None:
            raise ValueError("day one clock must be timezone aware")
        before = self._signature()
        self._ensure_spy(now)
        for symbol in ("SPY", "COIN"):
            lifecycle = self.state["lifecycles"].get(symbol)
            if lifecycle is None:
                continue
            try:
                self._step(lifecycle, now)
            except Exception as exc:
                self._halt(lifecycle, "CONTROLLER_ERROR", now, exc)
        if self._signature() != before:
            self.checkpoint("DAY_ONE")

    def _ensure_spy(self, now: datetime) -> None:
        target = self._spy_target(now)
        if target is None or not self._replace_if_released("SPY", target):
            return
        try:
            close = self.calendar.session_close(target)
        except Exception:
            return
        lifecycle = self._new_lifecycle("SPY", target, close, "IDLE")
        lifecycle["side"] = "LONG"
        lifecycle["decision"] = {
            "eligible": True,
            "evidence": d1.EVIDENCE[d1.SPY_ID],
            "selected_after_research": True,
        }
        lifecycle["decision_saved"] = True
        self.state["lifecycles"]["SPY"] = lifecycle
        if not self.checkpoint("DAY_ONE_SPY_DECISION"):
            self.state["lifecycles"].pop("SPY", None)
            return
        self._record(lifecycle, "MODEL_DECISION", now, side="buy", status="eligible")

    def _step(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        if lifecycle.get("released"):
            return
        if self._collect_job(lifecycle, now):
            return
        if lifecycle["phase"] == "RECOVERY_HALT":
            return
        if lifecycle["symbol"] == "COIN" and lifecycle["decision"] is None:
            self._step_coin_decision(lifecycle, now)
            return
        live = self._live_attempt(lifecycle)
        if live is not None:
            if lifecycle.get("emergency"):
                self._drive_cancel(lifecycle, live, now)
            else:
                self._drive_attempt(lifecycle, live, now)
            return
        if lifecycle.get("fallback_eligible"):
            self._drive_spy_fallback(lifecycle, now)
            return
        if lifecycle["phase"] in ("DONE", "SKIPPED"):
            self._verify_release(lifecycle, now)
            return
        if lifecycle["entry_qty"] > lifecycle["exit_qty"]:
            if lifecycle["phase"] == "HELD" and not lifecycle.get("emergency"):
                self._ensure_cls(lifecycle, now)
            else:
                lifecycle["phase"] = "RECOVERY"
                self._drive_recovery(lifecycle, now)
            return
        if lifecycle["admitted"]:
            self._start_entry_if_due(lifecycle, now)
            return
        self._start_gates(lifecycle, now)

    def _step_coin_decision(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        local = now.astimezone(d1.ET)
        if local.date() > self._session(lifecycle) and not lifecycle["attempts"] and lifecycle["entry_qty"] == 0:
            # the robot was down through the whole session: release COIN instead of owning it forever
            self._skip(lifecycle, "SESSION_PASSED", now)
            return
        if local.date() != self._session(lifecycle) or local.time() < dtime(9, 35):
            return
        done, result, error = self._io(lifecycle, "coin_model", lambda: self.coin_model(self._session(lifecycle)))
        if not done:
            return
        if error is not None:
            self._skip(lifecycle, "COIN_MODEL_UNAVAILABLE", now)
            return
        if not isinstance(result, dict) or result.get("side") not in (-1, 0, 1):
            self._skip(lifecycle, "COIN_MODEL_INVALID", now)
            return
        decision = _strict_copy(result)
        decision["evidence"] = d1.EVIDENCE[d1.COIN_ID]
        decision["causal_full_day_gate_omitted"] = True
        lifecycle["decision"] = decision
        lifecycle["decision_saved"] = True
        lifecycle["side"] = "LONG" if result["side"] == 1 else "SHORT" if result["side"] == -1 else None
        if not self.checkpoint("DAY_ONE_COIN_DECISION"):
            self._skip(lifecycle, "MODEL_DECISION_NOT_DURABLE", now)
            return
        self._record(lifecycle, "MODEL_DECISION", now, side=lifecycle["side"], status="saved")
        if result["side"] == 0:
            self._skip(lifecycle, "NO_SIGNAL", now)
            return
        self._start_gates(lifecycle, now)

    def _gate_wait(self, lifecycle: Dict[str, Any], reason: str, now: datetime) -> None:
        """A gate that cannot pass yet: note the reason once (not every tick) and wait before retrying."""
        gates = lifecycle["operational_gates"]
        if not gates or gates[-1] != reason:
            gates.append(reason)
        self._gate_retry_at[lifecycle["symbol"]] = now + timedelta(
            seconds=d1.GATE_RETRY_SEC.get(lifecycle["symbol"], 30))

    def _start_gates(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        if not self._entry_writes_open:
            return
        _open, terminal, _tif = self._entry_window(lifecycle, now)
        if terminal:
            self._skip(lifecycle, "ENTRY_CUTOFF_MISSED", now)
            return
        retry_at = self._gate_retry_at.get(lifecycle["symbol"])
        if retry_at is not None and now < retry_at:
            return
        if not self.hooks.persistence_healthy():
            self._gate_wait(lifecycle, "PERSISTENCE_UNHEALTHY", now)
            return
        if self.hooks.broker_mismatch():
            self._gate_wait(lifecycle, "BROKER_MISMATCH", now)
            return
        other = self.hooks.held_by_other(lifecycle["symbol"])
        if other:
            self._skip(lifecycle, "HELD_BY_OTHER_STRATEGY", now)
            return
        session = self._session(lifecycle)
        start = session - timedelta(days=45)
        end = session + timedelta(days=45)
        symbol = lifecycle["symbol"]
        side = lifecycle["side"]
        coin_price = self._fresh_coin_price(now) if symbol == "COIN" else None
        if symbol == "COIN" and coin_price is None:
            self._gate_wait(lifecycle, "FRESH_PRICE_UNAVAILABLE", now)
            return
        broker = self.broker

        def work() -> Dict[str, Any]:
            out: Dict[str, Any] = {
                "calendar": broker.get_calendar(start.isoformat(), end.isoformat()),
                "account": broker.get_account_fields(),
                "position": _broker_position(broker, symbol),
                "orders": _validate_open_order_quantities(broker.list_open_orders(symbol)),
                "asset": broker.get_asset(symbol),
                "reference": coin_price if symbol == "COIN" else self.spy_reference(session),
            }
            return out

        done, result, error = self._io(lifecycle, "entry_gates", work)
        if not done:
            return
        if error is not None:
            if isinstance(error, QuantityError):
                self._halt(lifecycle, "FRACTIONAL_BROKER_QUANTITY", now, error)
            else:
                self._gate_wait(lifecycle, "BROKER_OR_RELAY_UNAVAILABLE", now)
            return
        try:
            self._apply_gates(lifecycle, result, now)
        except Exception as exc:
            self._halt(lifecycle, "GATE_RESPONSE_INVALID", now, exc)

    def _apply_gates(self, lifecycle: Dict[str, Any], result: Dict[str, Any], now: datetime) -> None:
        if not isinstance(result, dict):
            raise ValueError("gate result must be an object")
        session = self._session(lifecycle)
        calendar = d1.parse_calendar(result.get("calendar") or [])
        local_calendar: Dict[date, dtime] = {}
        current = session - timedelta(days=45)
        final = session + timedelta(days=45)
        while current <= final:
            if self.calendar.is_trading_day(current):
                local_calendar[current] = self.calendar.session_close(current)
            current += timedelta(days=1)
        if calendar != local_calendar:
            self._skip(lifecycle, "CALENDAR_DISAGREES", now)
            return
        if lifecycle["symbol"] == "SPY" and not d1.turn_month_eligible(session, calendar):
            self._skip(lifecycle, "NOT_TURN_MONTH", now)
            return
        account = result.get("account")
        if not isinstance(account, dict) or account.get("account_number") != self.expected_account:
            self._skip(lifecycle, "ACCOUNT_MISMATCH", now)
            return
        lifecycle["account_match"] = True
        for key in ("equity", "buying_power"):
            value = account.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                self._skip(lifecycle, "ACCOUNT_FIELDS_INVALID", now)
                return
        try:
            raw_position = _whole_quantity(result.get("position", 0), "broker position quantity")
        except QuantityError as exc:
            self._halt(lifecycle, "FRACTIONAL_BROKER_QUANTITY", now, exc)
            return
        if raw_position != 0:
            self._skip(lifecycle, "BROKER_POSITION_OCCUPIED", now)
            return
        orders = result.get("orders")
        if not isinstance(orders, list) or orders:
            self._skip(lifecycle, "BROKER_ORDER_OCCUPIED", now)
            return
        asset = result.get("asset")
        if (
            not isinstance(asset, dict)
            or str(asset.get("symbol") or "").upper() != lifecycle["symbol"]
            or asset.get("tradable") is not True
        ):
            self._skip(lifecycle, "ASSET_NOT_TRADABLE", now)
            return
        if lifecycle["side"] == "SHORT" and not (
            bool(asset.get("shortable")) and bool(asset.get("easy_to_borrow"))
        ):
            self._skip(lifecycle, "SHORT_NOT_AVAILABLE", now)
            return
        decision = lifecycle.get("decision") or {}
        if lifecycle["side"] == "SHORT":
            fields = (
                decision.get("prior_low"),
                decision.get("close_two_back"),
                decision.get("today_low_through_0934", getattr(self, "_coin_lows", {}).get(session.isoformat())),
                decision.get("prior_close"),
            )
            if d1.ssr_blocks_short(*fields):
                self._skip(lifecycle, "RULE_201", now)
                return
        reference = result.get("reference")
        if isinstance(reference, dict):
            reference = reference.get("price")
        if not isinstance(reference, (int, float)) or isinstance(reference, bool) or not math.isfinite(reference) or reference <= 0:
            self._skip(lifecycle, "REFERENCE_PRICE_INVALID", now)
            return
        usable = min(float(account["buying_power"]), float(self.hooks.usable_buying_power(account, lifecycle["symbol"])))
        for other in self.state["lifecycles"].values():
            if other is lifecycle or other.get("released"):
                continue
            usable -= max(0.0, float(other.get("target_qty") or 0) * float(other.get("reference_price") or 0.0)
                          - float(other.get("entry_notional") or 0.0))
        pct = d1.SPY_PCT if lifecycle["symbol"] == "SPY" else d1.COIN_PCT
        qty = d1.target_shares(float(account["equity"]), max(0.0, usable), float(reference), pct)
        if qty < 1:
            self._skip(lifecycle, "NO_WHOLE_SHARES", now)
            return
        admission = self.hooks.admission(lifecycle["symbol"], lifecycle["side"], qty, qty * float(reference))
        if admission:
            self._skip(lifecycle, str(admission)[:80], now)
            return
        lifecycle["target_qty"] = qty
        lifecycle["reference_price"] = float(reference)
        lifecycle["sizing_equity"] = float(account["equity"])
        lifecycle["sizing_buying_power"] = max(0.0, usable)
        lifecycle["sizing_limit"] = min(float(account["equity"]) * pct, d1.POSITION_CAP, max(0.0, usable))
        lifecycle["admitted"] = True
        lifecycle["operational_gates"].append("ADMITTED")
        if not self.checkpoint("DAY_ONE_ADMISSION"):
            lifecycle["admitted"] = False
            lifecycle["operational_gates"].append("ADMISSION_NOT_DURABLE")
            return
        self._start_entry_if_due(lifecycle, now)

    def _entry_window(self, lifecycle: Dict[str, Any], now: datetime) -> Tuple[bool, bool, str]:
        local = now.astimezone(d1.ET)
        session = self._session(lifecycle)
        if lifecycle["symbol"] == "SPY":
            open_now = (
                (local.date() < session and local.time() >= d1.SPY_OPG_FROM)
                or (local.date() == session and local.time() < d1.OPG_CUTOFF)
            )
            terminal = local.date() > session or (local.date() == session and local.time() >= d1.OPG_CUTOFF)
            return open_now, terminal, "opg"
        open_now = local.date() == session and d1.COIN_DISPATCH <= local.time() <= d1.COIN_DISPATCH_DEADLINE
        terminal = local.date() > session or (local.date() == session and local.time() > d1.COIN_DISPATCH_DEADLINE)
        return open_now, terminal, "day"

    def _entry_recheck(
        self,
        lifecycle: Dict[str, Any],
        quantity: int,
        reference: float,
        *,
        allow_resize: bool,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.hooks.persistence_healthy():
            return {"reason": "PERSISTENCE_UNHEALTHY"}
        if self.hooks.broker_mismatch():
            return {"reason": "BROKER_MISMATCH"}
        other = self.hooks.held_by_other(lifecycle["symbol"])
        if other:
            return {"reason": "HELD_BY_OTHER_STRATEGY"}
        account = self.broker.get_account_fields()
        if not isinstance(account, dict) or account.get("account_number") != self.expected_account:
            return {"reason": "ACCOUNT_MISMATCH"}
        for key in ("equity", "buying_power"):
            value = account.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                return {"reason": "ACCOUNT_FIELDS_INVALID"}
        position = _broker_position(self.broker, lifecycle["symbol"])
        if position != 0:
            return {"reason": "BROKER_POSITION_OCCUPIED"}
        orders = _validate_open_order_quantities(self.broker.list_open_orders(lifecycle["symbol"]))
        if client_id:
            same = next((row for row in orders if row.get("client_order_id") == client_id), None)
            if same is not None:
                return {"order": same}
        if orders:
            return {"reason": "BROKER_ORDER_OCCUPIED"}
        if not isinstance(reference, (int, float)) or not math.isfinite(reference) or reference <= 0:
            return {"reason": "REFERENCE_PRICE_INVALID"}
        usable = min(float(account["buying_power"]), float(self.hooks.usable_buying_power(account, lifecycle["symbol"])))
        for other_lifecycle in self.state["lifecycles"].values():
            if other_lifecycle is lifecycle or other_lifecycle.get("released"):
                continue
            usable -= max(
                0.0,
                float(other_lifecycle.get("target_qty") or 0) * float(other_lifecycle.get("reference_price") or 0.0)
                - float(other_lifecycle.get("entry_notional") or 0.0),
            )
        pct = d1.SPY_PCT if lifecycle["symbol"] == "SPY" else d1.COIN_PCT
        capacity = d1.target_shares(float(account["equity"]), max(0.0, usable), float(reference), pct)
        admitted_qty = capacity if allow_resize else quantity
        if capacity < 1 or admitted_qty < 1 or (not allow_resize and quantity > capacity):
            return {"reason": "NO_WHOLE_SHARES"}
        admission = self.hooks.admission(
            lifecycle["symbol"], lifecycle["side"], admitted_qty, admitted_qty * float(reference)
        )
        if admission:
            return {"reason": str(admission)[:80]}
        return {
            "reason": None,
            "quantity": admitted_qty,
            "equity": float(account["equity"]),
            "buying_power": max(0.0, usable),
        }

    def _start_entry_if_due(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        open_now, terminal, tif = self._entry_window(lifecycle, now)
        if not open_now:
            if terminal and lifecycle.get("admitted"):
                self._skip(lifecycle, "ENTRY_CUTOFF_MISSED", now)
            return
        reference = float(lifecycle.get("reference_price") or 0.0)
        if lifecycle["symbol"] == "COIN":
            fresh = self._fresh_coin_price(now)
            if fresh is None:
                return
            reference = fresh
        quantity = int(lifecycle.get("target_qty") or 0)

        def work() -> Dict[str, Any]:
            with self.hooks.ownership_lock():
                return self._entry_recheck(lifecycle, quantity, reference, allow_resize=True)

        done, result, error = self._io(lifecycle, "pre_intent", work)
        if not done:
            return
        open_now, terminal, tif = self._entry_window(lifecycle, self.clock())
        if not open_now:
            if terminal:
                self._skip(lifecycle, "ENTRY_CUTOFF_MISSED", self.clock())
            return
        if error is not None:
            if isinstance(error, QuantityError):
                self._halt(lifecycle, "FRACTIONAL_BROKER_QUANTITY", self.clock(), error)
            return
        reason = result.get("reason") if isinstance(result, dict) else "ENTRY_RECHECK_INVALID"
        if reason:
            self._skip(lifecycle, str(reason), self.clock())
            return
        quantity = int(result["quantity"])
        changed = (
            quantity != lifecycle["target_qty"]
            or reference != lifecycle["reference_price"]
            or result["equity"] != lifecycle["sizing_equity"]
            or result["buying_power"] != lifecycle["sizing_buying_power"]
        )
        lifecycle["target_qty"] = quantity
        lifecycle["reference_price"] = reference
        lifecycle["sizing_equity"] = result["equity"]
        lifecycle["sizing_buying_power"] = result["buying_power"]
        if changed and not self.checkpoint("DAY_ONE_FRESH_ADMISSION"):
            return
        side = "buy" if lifecycle["side"] == "LONG" else "sell"
        self._create_attempt(lifecycle, "entry", side, quantity, tif, self.clock())

    def _precommit_protection(self, lifecycle: Dict[str, Any]) -> bool:
        if self._has_durable_protection(lifecycle):
            return False
        side = "sell" if lifecycle["side"] == "LONG" else "buy"
        lifecycle["protection"] = {
            "side": side,
            "exit_client_id": d1.client_id(lifecycle["strategy_id"], self._session(lifecycle), "exit"),
            "recovery_client_id": d1.client_id(lifecycle["strategy_id"], self._session(lifecycle), "recovery"),
            "durable": True,
        }
        return True

    def _create_attempt(
        self,
        lifecycle: Dict[str, Any],
        role: str,
        side: str,
        quantity: int,
        tif: str,
        now: datetime,
        *,
        send_now: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if quantity < 1 or self._live_attempt(lifecycle) is not None:
            return None
        predecessors = [a for a in lifecycle["attempts"] if a["role"] == role]
        if any(a["status"] not in TERMINAL for a in predecessors):
            return None
        number = len(predecessors) + 1
        predecessor = predecessors[-1] if predecessors else None
        protection_added = role == "entry" and self._precommit_protection(lifecycle)
        protection = lifecycle.get("protection") or {}
        precommitted = role in ("exit", "recovery") and number == 1 and self._has_durable_protection(lifecycle)
        client_id = (
            protection.get(f"{role}_client_id")
            if precommitted
            else d1.client_id(lifecycle["strategy_id"], self._session(lifecycle), role, number)
        )
        attempt = {
            "role": role,
            "attempt": number,
            "client_id": client_id,
            "order_id": None,
            "side": side,
            "time_in_force": tif,
            "max_remaining_qty": int(quantity),
            "status": "intent",
            "sent": False,
            "ambiguous": False,
            "filled_qty": 0,
            "booked_qty": 0,
            "booked_notional": 0.0,
            "predecessor_status": predecessor["status"] if predecessor else None,
            "predecessor_fill_watermark": predecessor["booked_qty"] if predecessor else 0,
            "cancel_intent": None,
            "created_at": _iso(now),
        }
        lifecycle["attempts"].append(attempt)
        lifecycle["phase"] = "ENTRY_INTENT" if role == "entry" else "EXIT_INTENT" if role == "exit" else "RECOVERY"
        if not self.checkpoint(f"DAY_ONE_{role.upper()}_INTENT"):
            if precommitted:
                self._record(
                    lifecycle,
                    "PROTECTION_ACTIVATION_NOT_DURABLE",
                    now,
                    role=role,
                    client_id=attempt["client_id"],
                )
            else:
                lifecycle["attempts"].pop()
                if protection_added:
                    lifecycle["protection"] = None
                if role == "entry":
                    lifecycle["phase"] = "RESERVED" if lifecycle["symbol"] == "COIN" else "IDLE"
                else:
                    self._halt(lifecycle, "EXIT_INTENT_NOT_DURABLE", now)
                return None
        self._record(
            lifecycle,
            "MUTATION_INTENT",
            now,
            role=role,
            side=side,
            quantity=quantity,
            client_id=attempt["client_id"],
            time_in_force=tif,
        )
        if send_now:
            self._drive_attempt(lifecycle, attempt, now)
        return attempt

    def _live_attempt(self, lifecycle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for attempt in reversed(lifecycle["attempts"]):
            if attempt["status"] in LIVE:
                return attempt
        return None

    def _write_allowed(self, lifecycle: Dict[str, Any], attempt: Dict[str, Any], now: datetime) -> bool:
        local = now.astimezone(d1.ET)
        session = self._session(lifecycle)
        close = self._close(lifecycle)
        if attempt["role"] == "entry":
            if not self._entry_writes_open:
                return False
            if lifecycle["symbol"] == "COIN":
                return local.date() == session and d1.COIN_DISPATCH <= local.time() <= d1.COIN_DISPATCH_DEADLINE
            if attempt["time_in_force"] == "opg":
                return ((local.date() < session and local.time() >= d1.SPY_OPG_FROM)
                        or (local.date() == session and local.time() < d1.OPG_CUTOFF))
            return local.date() == session and d1.MARKET_OPEN <= local.time() <= d1.OPEN_FALLBACK_DEADLINE
        if not self._exit_writes_open:
            return False
        if attempt["time_in_force"] == "cls":
            deadline, _fallback = d1.close_deadlines(session, close)
            return now < deadline
        if attempt["time_in_force"] == "opg":
            return local.time() < dtime(9, 28) or local.time() >= d1.SPY_OPG_FROM
        day_close = self.calendar.session_close(local.date())
        return d1.MARKET_OPEN <= local.time() < day_close

    def _poll_interval(self, lifecycle: Dict[str, Any], attempt: Dict[str, Any], now: datetime) -> float:
        if attempt["time_in_force"] == "day":
            return 1.0
        local = now.astimezone(d1.ET)
        if attempt["time_in_force"] == "opg":
            near = local.date() == self._session(lifecycle) and local.time() >= dtime(9, 28)
        else:
            close_at = datetime.combine(self._session(lifecycle), self._close(lifecycle), d1.ET)
            near = now >= close_at - timedelta(minutes=2)
        return 2.0 if near else 60.0

    def _poll_due(self, lifecycle: Dict[str, Any], attempt: Dict[str, Any], now: datetime) -> bool:
        if attempt["status"] == "ambiguous":
            # an unknown outcome is looked up every 5 s, not every 1 s tick (same client id, never a double)
            last = attempt.get("ambiguous_checked_at")
            if last and (now - _parse_datetime(last, now)).total_seconds() < d1.AMBIGUOUS_POLL_SEC:
                return False
            attempt["ambiguous_checked_at"] = _iso(now)
            return True
        if attempt["status"] not in ("accepted", "partially_filled", "done_for_day"):
            return True
        last = attempt.get("last_read_at")
        if not last:
            return True
        return (now - _parse_datetime(last, now)).total_seconds() >= self._poll_interval(lifecycle, attempt, now)

    def _drive_attempt(self, lifecycle: Dict[str, Any], attempt: Dict[str, Any], now: datetime) -> None:
        if attempt["status"] in TERMINAL or not self._poll_due(lifecycle, attempt, now):
            return
        if attempt["role"] == "entry" and not self._has_durable_protection(lifecycle):
            self._precommit_protection(lifecycle)
            if not self.checkpoint("DAY_ONE_PROTECTION_PRECOMMIT"):
                lifecycle["protection"] = None
                self._halt(lifecycle, "PROTECTION_NOT_DURABLE", now)
                return
        broker = self.broker
        snapshot = _strict_copy(attempt)
        symbol = lifecycle["symbol"]

        def work() -> Tuple[str, Any]:
            lock = self.hooks.ownership_lock() if snapshot["role"] == "entry" else nullcontext()
            with lock:
                try:
                    row = (broker.get_order(snapshot["order_id"]) if snapshot["order_id"]
                           else broker.get_order_by_client_id(snapshot["client_id"]))
                except Exception as exc:
                    return "lookup_error", exc
                if row is not None:
                    return "row", row
                if not self._write_allowed(lifecycle, snapshot, self.clock()):
                    return ("unresolved", None) if snapshot["sent"] else ("cutoff", None)
                if snapshot["role"] == "entry":
                    checked = self._entry_recheck(
                        lifecycle,
                        snapshot["max_remaining_qty"],
                        float(lifecycle.get("reference_price") or 0.0),
                        allow_resize=False,
                        client_id=snapshot["client_id"],
                    )
                    if checked.get("order") is not None:
                        return "row", checked["order"]
                    if checked.get("reason"):
                        return ("unresolved", checked["reason"]) if snapshot["sent"] else ("blocked", checked["reason"])
                    if not self._write_allowed(lifecycle, snapshot, self.clock()):
                        return ("unresolved", None) if snapshot["sent"] else ("cutoff", None)
                try:
                    if snapshot["time_in_force"] in ("cls", "opg"):
                        row = broker.submit_on_auction(
                            symbol, snapshot["side"], snapshot["max_remaining_qty"], snapshot["client_id"],
                            snapshot["time_in_force"],
                        )
                    else:
                        row = broker.submit_market_order(
                            symbol, snapshot["side"], snapshot["max_remaining_qty"], snapshot["client_id"]
                        )
                except Exception as exc:
                    return "post_error", exc
                return "row", row

        done, result, error = self._io(lifecycle, f"attempt:{len(lifecycle['attempts']) - 1}", work)
        if not done:
            return
        if error is not None:
            self._halt(lifecycle, "WORKER_ERROR", now, error)
            return
        kind, value = result
        if kind == "lookup_error":
            lifecycle["last_error"] = type(value).__name__
            self._record(lifecycle, "LOOKUP_UNAVAILABLE", now, role=attempt["role"], error_class=type(value).__name__)
            return
        if kind == "post_error":
            attempt["sent"] = True
            if _ambiguous_error(value) or _duplicate_error(value):
                attempt["status"] = "ambiguous"
                attempt["ambiguous"] = True
                self._record(lifecycle, "WRITE_AMBIGUOUS", now, role=attempt["role"],
                             client_id=attempt["client_id"], error_class=type(value).__name__)
            else:
                attempt["status"] = "rejected"
                attempt["ambiguous"] = False
                self._record(lifecycle, "WRITE_REFUSED", now, role=attempt["role"],
                             client_id=attempt["client_id"], error_class=type(value).__name__)
                if lifecycle.get("emergency") and attempt["role"] == "entry" and lifecycle["entry_qty"] == 0:
                    self._skip(lifecycle, "EMERGENCY_BEFORE_FILL", now)
                else:
                    self._after_attempt(lifecycle, attempt, now)
            self.checkpoint("DAY_ONE_BROKER_RESULT")
            return
        if kind in ("cutoff", "blocked", "unresolved"):
            if attempt["sent"] or kind == "unresolved":
                attempt["status"] = "ambiguous"
                attempt["ambiguous"] = True
                lifecycle["phase"] = "ENTRY_PENDING" if attempt["role"] == "entry" else "EXIT_PENDING"
            else:
                attempt["status"] = "expired" if kind == "cutoff" else "rejected"
                attempt["ambiguous"] = False
                lifecycle["fallback_eligible"] = False
                self._skip(lifecycle, "ENTRY_CUTOFF_MISSED" if kind == "cutoff" else str(value), now)
            self.checkpoint("DAY_ONE_BROKER_RESULT")
            return
        if value is None:
            attempt["sent"] = True
            attempt["status"] = "ambiguous"
            attempt["ambiguous"] = True
            self.checkpoint("DAY_ONE_BROKER_RESULT")
            return
        attempt["sent"] = True
        attempt["ambiguous"] = False
        self._apply_order_row(lifecycle, attempt, value, now)

    def _validate_order_row(self, lifecycle: Dict[str, Any], attempt: Dict[str, Any], row: Any) -> str:
        if not isinstance(row, dict):
            raise ValueError("broker order must be an object")
        if row.get("client_order_id") not in (None, attempt["client_id"]):
            raise ValueError("broker order client id does not match")
        if str(row.get("symbol") or lifecycle["symbol"]).upper() != lifecycle["symbol"]:
            raise ValueError("broker order symbol does not match")
        if row.get("side") is not None and _side_value(row["side"]) != attempt["side"]:
            raise ValueError("broker order side does not match")
        if row.get("qty") not in (None, ""):
            order_qty = _whole_quantity(row.get("qty"), "broker order quantity", signed=False)
            if order_qty != attempt["max_remaining_qty"]:
                raise ValueError("broker order quantity does not match the mutation intent")
        total = _filled_qty(row)
        if total < 0 or total > attempt["max_remaining_qty"]:
            raise ValueError("broker filled quantity exceeds the mutation intent")
        return _normalized_status(row.get("status"))

    def _apply_order_row(
        self,
        lifecycle: Dict[str, Any],
        attempt: Dict[str, Any],
        row: Dict[str, Any],
        now: datetime,
        *,
        defer_after: bool = False,
    ) -> None:
        try:
            status = self._validate_order_row(lifecycle, attempt, row)
            total = _filled_qty(row)
            average = _filled_avg(row)
            if total < attempt["booked_qty"]:
                raise ValueError("broker fill watermark decreased")
            if total > attempt["booked_qty"]:
                self._book_fill(lifecycle, attempt, row, total, average, now)
            attempt["order_id"] = str(row.get("id") or attempt["order_id"] or "") or None
            attempt["filled_qty"] = total
            attempt["status"] = status
            attempt["last_read_at"] = _iso(now)
        except Exception as exc:
            self._halt(lifecycle, "BROKER_ORDER_CONTRADICTION", now, exc)
            return
        self._record(lifecycle, "ORDER_READ", now, role=attempt["role"], side=attempt["side"],
                     quantity=attempt["filled_qty"], status=status, order_id=attempt["order_id"])
        if not defer_after:
            self._after_attempt(lifecycle, attempt, now)
        if not self.checkpoint("DAY_ONE_BROKER_RESULT"):
            self._halt(lifecycle, "RESULT_NOT_DURABLE", now)

    def _book_fill(
        self,
        lifecycle: Dict[str, Any],
        attempt: Dict[str, Any],
        row: Dict[str, Any],
        total: int,
        average: float,
        now: datetime,
    ) -> None:
        if not math.isfinite(average) or average <= 0:
            raise ValueError("broker fill average is invalid")
        delta = total - attempt["booked_qty"]
        notional = total * average
        price = (notional - attempt["booked_notional"]) / delta
        if not math.isfinite(price) or price <= 0:
            raise ValueError("broker incremental fill price is invalid")
        at = _parse_datetime(row.get("filled_at") or row.get("updated_at"), now)
        event = {
            "kind": "fill",
            "strategy_id": lifecycle["strategy_id"],
            "symbol": lifecycle["symbol"],
            "session": lifecycle["session"],
            "role": attempt["role"],
            "side": attempt["side"],
            "qty": delta,
            "price": price,
            "at": _iso(at),
            "alpaca_order_id": str(row.get("id") or attempt["order_id"] or ""),
            "client_order_id": attempt["client_id"],
            "target_qty": attempt["max_remaining_qty"],
            "cumulative_qty": total,
        }
        accepted = self.book(_strict_copy(event))
        if accepted is not True:
            raise ValueError("booking callback refused the fill")
        attempt["booked_qty"] = total
        attempt["booked_notional"] = notional
        attempt["filled_qty"] = total
        lifecycle["actual_legs"].append(_strict_copy(event))
        if attempt["role"] == "entry":
            lifecycle["entry_qty"] += delta
            lifecycle["entry_notional"] += delta * price
            limit = float(lifecycle.get("sizing_limit") or 0.0)
            if limit > 0 and lifecycle["entry_notional"] > limit and "FILL_NOTIONAL_OVERLAY_EXCEEDED" not in lifecycle["execution_deviations"]:
                lifecycle["execution_deviations"].append("FILL_NOTIONAL_OVERLAY_EXCEEDED")
                log.warning("DAY_ONE fill exceeded the reference sizing overlay for %s", lifecycle["symbol"])
        else:
            lifecycle["exit_qty"] += delta
            lifecycle["exit_notional"] += delta * price
        self._record(lifecycle, "FILL_BOOKED", now, role=attempt["role"], side=attempt["side"],
                     quantity=delta, price=round(price, 6), order_id=event["alpaca_order_id"])

    def _after_attempt(self, lifecycle: Dict[str, Any], attempt: Dict[str, Any], now: datetime) -> None:
        if attempt["status"] not in TERMINAL:
            lifecycle["phase"] = "ENTRY_PENDING" if attempt["role"] == "entry" else "EXIT_PENDING"
            return
        if attempt["filled_qty"] != attempt["booked_qty"]:
            self._halt(lifecycle, "UNBOOKED_TERMINAL_FILL", now)
            return
        if attempt["role"] == "entry":
            if lifecycle["entry_qty"] > 0:
                lifecycle["phase"] = "HELD"
                self._ensure_cls(lifecycle, now)
            elif lifecycle["symbol"] == "SPY" and attempt["time_in_force"] == "opg":
                lifecycle["fallback_eligible"] = True
                lifecycle["phase"] = "ENTRY_PENDING"
            else:
                self._skip(lifecycle, "ENTRY_NO_FILL", now)
            return
        remaining = lifecycle["entry_qty"] - lifecycle["exit_qty"]
        if remaining == 0:
            lifecycle["phase"] = "DONE"
            self._verify_release(lifecycle, now)
        else:
            lifecycle["phase"] = "RECOVERY"
            self._drive_recovery(lifecycle, now)

    def _ensure_cls(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        remaining = lifecycle["entry_qty"] - lifecycle["exit_qty"]
        if remaining < 1 or self._live_attempt(lifecycle) is not None:
            return
        submit_before, _fallback = d1.close_deadlines(self._session(lifecycle), self._close(lifecycle))
        if now >= submit_before:
            lifecycle["execution_deviations"].append("CLS_DEADLINE_MISSED")
            lifecycle["phase"] = "RECOVERY"
            return
        side = "sell" if lifecycle["side"] == "LONG" else "buy"
        self._create_attempt(
            lifecycle, "exit", side, remaining, "cls", now, send_now=not self._reconciling
        )

    def _drive_spy_fallback(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        local = now.astimezone(d1.ET)
        if local.date() < self._session(lifecycle) or local.time() < d1.MARKET_OPEN:
            return
        if local.date() != self._session(lifecycle) or local.time() > d1.OPEN_FALLBACK_DEADLINE:
            lifecycle["fallback_eligible"] = False
            self._skip(lifecycle, "OPEN_FALLBACK_MISSED", now)
            return
        broker = self.broker

        def work() -> Dict[str, Any]:
            return {
                "position": _broker_position(broker, "SPY"),
                "open": [row for row in _validate_open_order_quantities(broker.list_open_orders("SPY"))
                         if _is_strategy_order(row)],
            }

        done, result, error = self._io(lifecycle, "spy_fallback_check", work)
        if not done:
            return
        if error is not None:
            if isinstance(error, QuantityError):
                self._halt(lifecycle, "FRACTIONAL_BROKER_QUANTITY", now, error)
            return
        if result.get("position") != 0 or result.get("open"):
            self._halt(lifecycle, "SPY_FALLBACK_RECONCILIATION_FAILED", now)
            return
        lifecycle["fallback_eligible"] = False
        lifecycle["execution_deviations"].append("SPY_OPEN_MARKET_FALLBACK")
        self._create_attempt(lifecycle, "entry", "buy", lifecycle["target_qty"], "day", now)

    def _recovery_mode(self, lifecycle: Dict[str, Any], now: datetime) -> Optional[str]:
        local = now.astimezone(d1.ET)
        try:
            if not self.calendar.is_trading_day(local.date()):
                return None
            close = self.calendar.session_close(local.date())
        except Exception:
            return None
        if lifecycle.get("emergency"):
            return "day" if d1.MARKET_OPEN <= local.time() < close else (
                "opg" if local.time() < dtime(9, 28) or local.time() >= d1.SPY_OPG_FROM else None
            )
        session = self._session(lifecycle)
        _submit, fallback = d1.close_deadlines(session, self._close(lifecycle))
        if local.date() == session and now < fallback:
            return None
        if local.time() < dtime(9, 28):
            return "opg"
        if local.time() < d1.MARKET_OPEN:
            return None
        if local.time() < close:
            return "day"
        return "opg" if local.time() >= d1.SPY_OPG_FROM else None

    def _drive_recovery(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        remaining = lifecycle["entry_qty"] - lifecycle["exit_qty"]
        if remaining < 1:
            lifecycle["phase"] = "DONE"
            self._verify_release(lifecycle, now)
            return
        tif = self._recovery_mode(lifecycle, now)
        if tif is None:
            return
        # pace exits Alpaca refused: 10 s after one refusal, 5 min after two in a row, so the
        # attempt cap is not burned in seconds and the 19:05 opening auction exit still has room
        attempts = lifecycle["attempts"]
        if attempts and attempts[-1]["status"] == "rejected" and attempts[-1]["role"] != "entry":
            again = len(attempts) >= 2 and attempts[-2]["status"] == "rejected" and attempts[-2]["role"] != "entry"
            wait = d1.EXIT_REFUSED_AGAIN_RETRY_SEC if again else d1.EXIT_REFUSED_RETRY_SEC
            if (now - _parse_datetime(attempts[-1].get("created_at"), now)).total_seconds() < wait:
                return
        if len(attempts) >= d1.MAX_ATTEMPTS:
            self._halt(lifecycle, "EXIT_ATTEMPTS_EXHAUSTED", now)
            return
        broker = self.broker
        symbol = lifecycle["symbol"]
        expected = remaining if lifecycle["side"] == "LONG" else -remaining

        def work() -> Dict[str, Any]:
            return {
                "position": _broker_position(broker, symbol),
                "open": [row for row in _validate_open_order_quantities(broker.list_open_orders(symbol))
                         if _is_strategy_order(row)],
            }

        done, result, error = self._io(lifecycle, "recovery_check", work)
        if not done:
            return
        if error is not None:
            if isinstance(error, QuantityError):
                self._halt(lifecycle, "FRACTIONAL_BROKER_QUANTITY", now, error)
            return
        if result.get("open"):
            return
        if result.get("position") != expected:
            self._halt(lifecycle, "RECOVERY_POSITION_MISMATCH", now)
            return
        side = "sell" if lifecycle["side"] == "LONG" else "buy"
        lifecycle["execution_deviations"].append("EMERGENCY_EXIT" if lifecycle.get("emergency") else "CLOSE_RECOVERY")
        self._create_attempt(lifecycle, "recovery", side, remaining, tif, now)

    def request_exit(self, symbol: str, reason: str, now: Optional[datetime] = None) -> bool:
        lifecycle = self.state["lifecycles"].get(symbol.upper())
        now = now or self.clock()
        if lifecycle is None or lifecycle.get("released"):
            return False
        if lifecycle.get("emergency") is None:
            lifecycle["emergency"] = {"reason": str(reason)[:80], "requested_at": _iso(now)}
            lifecycle["phase"] = "RECOVERY"
            if not self.checkpoint("DAY_ONE_EMERGENCY"):
                lifecycle["emergency"] = None
                return False
            self._record(lifecycle, "EMERGENCY_REQUESTED", now, reason=str(reason)[:80])
        live = self._live_attempt(lifecycle)
        if live is not None:
            self._drive_cancel(lifecycle, live, now)
        elif lifecycle["entry_qty"] > lifecycle["exit_qty"]:
            self._drive_recovery(lifecycle, now)
        else:
            self._skip(lifecycle, "EMERGENCY_BEFORE_FILL", now)
        return True

    def _drive_cancel(self, lifecycle: Dict[str, Any], attempt: Dict[str, Any], now: datetime) -> None:
        cancel = attempt.get("cancel_intent")
        if cancel is None:
            cancel = {
                "status": "intent",
                "target_order_id": attempt.get("order_id"),
                "target_client_id": attempt["client_id"],
                "predecessor_status": attempt["status"],
                "fill_watermark": attempt["booked_qty"],
            }
            attempt["cancel_intent"] = cancel
            if not self.checkpoint("DAY_ONE_CANCEL_INTENT"):
                attempt["cancel_intent"] = None
                self._halt(lifecycle, "CANCEL_INTENT_NOT_DURABLE", now)
                return
            self._record(lifecycle, "CANCEL_INTENT", now, role=attempt["role"],
                         client_id=attempt["client_id"], order_id=attempt.get("order_id"))
        snapshot = _strict_copy(attempt)
        broker = self.broker

        def work() -> Tuple[str, Any]:
            try:
                row = (broker.get_order(snapshot["order_id"]) if snapshot["order_id"]
                       else broker.get_order_by_client_id(snapshot["client_id"]))
                if row is None or _normalized_status(row.get("status")) in TERMINAL:
                    return "row", row
                return "row", broker.cancel_order_and_confirm(str(row["id"]))
            except Exception as exc:
                return "error", exc

        done, result, error = self._io(lifecycle, "cancel", work)
        if not done:
            return
        if error is not None:
            self._halt(lifecycle, "CANCEL_WORKER_ERROR", now, error)
            return
        kind, value = result
        if kind == "error":
            cancel["status"] = "ambiguous"
            self._halt(lifecycle, "CANCEL_AMBIGUOUS", now, value)
            return
        if value is None:
            cancel["status"] = "ambiguous" if attempt["sent"] or attempt.get("ambiguous") else "terminal"
            if attempt["sent"] or attempt.get("ambiguous"):
                attempt["status"] = "ambiguous"
                attempt["ambiguous"] = True
                lifecycle["phase"] = "ENTRY_PENDING" if attempt["role"] == "entry" else "EXIT_PENDING"
            else:
                attempt["status"] = "rejected"
                attempt["ambiguous"] = False
                if lifecycle["entry_qty"] > lifecycle["exit_qty"]:
                    lifecycle["phase"] = "RECOVERY"
                    self._drive_recovery(lifecycle, now)
                else:
                    self._skip(lifecycle, "EMERGENCY_BEFORE_FILL", now)
            self.checkpoint("DAY_ONE_CANCEL_RESULT")
            return
        self._apply_order_row(lifecycle, attempt, value, now, defer_after=True)
        if lifecycle["phase"] == "RECOVERY_HALT":
            return
        if attempt["status"] not in TERMINAL:
            cancel["status"] = "ambiguous"
            self._halt(lifecycle, "CANCEL_NOT_TERMINAL", now)
            return
        cancel["status"] = "terminal"
        lifecycle["phase"] = "RECOVERY"
        if not self.checkpoint("DAY_ONE_CANCEL_RESULT"):
            self._halt(lifecycle, "CANCEL_RESULT_NOT_DURABLE", now)
            return
        if lifecycle["entry_qty"] > lifecycle["exit_qty"]:
            self._drive_recovery(lifecycle, now)
        elif lifecycle["entry_qty"] > 0:
            lifecycle["phase"] = "DONE"
            self._verify_release(lifecycle, now)
        else:
            self._skip(lifecycle, "EMERGENCY_BEFORE_FILL", now)

    def _skip(self, lifecycle: Dict[str, Any], reason: str, now: datetime) -> None:
        if lifecycle["entry_qty"] > lifecycle["exit_qty"]:
            self._halt(lifecycle, reason, now)
            return
        lifecycle["phase"] = "SKIPPED"
        lifecycle["operational_gates"].append(str(reason)[:80])
        self._record(lifecycle, "SKIPPED", now, reason=str(reason)[:80], phase="SKIPPED")
        self._verify_release(lifecycle, now)

    def _halt(
        self,
        lifecycle: Dict[str, Any],
        reason: str,
        now: datetime,
        exc: Optional[Exception] = None,
    ) -> None:
        lifecycle["phase"] = "RECOVERY_HALT"
        lifecycle["last_error"] = type(exc).__name__ if exc is not None else str(reason)[:80]
        self._record(lifecycle, "RECOVERY_HALT", now, reason=str(reason)[:80],
                     error_class=type(exc).__name__ if exc is not None else None, phase="RECOVERY_HALT")

    def recovery_halt(self, symbol: str, reason: str, now: Optional[datetime] = None) -> bool:
        lifecycle = self.state["lifecycles"].get(symbol.upper())
        if lifecycle is None:
            return False
        self._halt(lifecycle, reason, now or self.clock())
        self.checkpoint("DAY_ONE_RECOVERY_HALT")
        return True

    def _verify_release(self, lifecycle: Dict[str, Any], now: datetime) -> None:
        if lifecycle.get("released") or lifecycle["entry_qty"] != lifecycle["exit_qty"]:
            return
        if any(attempt["status"] not in TERMINAL for attempt in lifecycle["attempts"]):
            return
        local_quantity = self.hooks.local_quantity(lifecycle["symbol"], lifecycle["strategy_id"])
        if local_quantity is not None and local_quantity != 0:
            self._halt(lifecycle, "LOCAL_POSITION_NOT_FLAT", now)
            return
        if lifecycle["symbol"] in self._jobs:
            return
        broker = self.broker
        symbol = lifecycle["symbol"]

        def work() -> Dict[str, Any]:
            return {
                "position": _broker_position(broker, symbol),
                "open": [row for row in _validate_open_order_quantities(broker.list_open_orders(symbol))
                         if _is_strategy_order(row)],
            }

        done, result, error = self._io(lifecycle, "release", work)
        if not done:
            return
        if error is not None:
            if isinstance(error, QuantityError):
                self._halt(lifecycle, "FRACTIONAL_BROKER_QUANTITY", now, error)
            return
        if result.get("position") != 0 or result.get("open"):
            self._halt(lifecycle, "RELEASE_RECONCILIATION_FAILED", now)
            return
        lifecycle["released"] = True
        self.hooks.on_release(symbol)
        self._record(lifecycle, "RELEASED", now, phase=lifecycle["phase"])
        self.checkpoint("DAY_ONE_RELEASE")

    def _io(
        self, lifecycle: Dict[str, Any], kind: str, work: Callable[[], Any]
    ) -> Tuple[bool, Any, Optional[Exception]]:
        symbol = lifecycle["symbol"]
        current = self._jobs.get(symbol)
        if current is None:
            future = self.executor.submit(work)
            self._jobs[symbol] = (kind, future)
            current = (kind, future)
        if current[0] != kind:
            return False, None, None
        future = current[1]
        if not future.done():
            return False, None, None
        self._jobs.pop(symbol, None)
        try:
            return True, future.result(), None
        except Exception as exc:
            return True, None, exc

    def _collect_job(self, lifecycle: Dict[str, Any], now: datetime) -> bool:
        current = self._jobs.get(lifecycle["symbol"])
        if current is None or not current[1].done():
            return False
        kind = current[0]
        if kind.startswith("attempt:"):
            index = int(kind.split(":", 1)[1])
            if index < len(lifecycle["attempts"]):
                self._drive_attempt(lifecycle, lifecycle["attempts"][index], now)
        elif kind == "cancel":
            live = self._live_attempt(lifecycle)
            if live is not None:
                self._drive_cancel(lifecycle, live, now)
        elif kind == "coin_model":
            self._step_coin_decision(lifecycle, now)
        elif kind == "entry_gates":
            self._start_gates(lifecycle, now)
        elif kind == "pre_intent":
            self._start_entry_if_due(lifecycle, now)
        elif kind == "spy_fallback_check":
            self._drive_spy_fallback(lifecycle, now)
        elif kind == "recovery_check":
            self._drive_recovery(lifecycle, now)
        elif kind == "release":
            self._verify_release(lifecycle, now)
        return True

    def pending_reads(self, now: Optional[datetime] = None, *, force: bool = False) -> List[Tuple[str, int, Optional[str], str]]:
        now = now or self.clock()
        out: List[Tuple[str, int, Optional[str], str]] = []
        for symbol, lifecycle in self.state["lifecycles"].items():
            if symbol in self._jobs:
                continue
            for index, attempt in enumerate(lifecycle["attempts"]):
                if attempt["status"] in LIVE and (force or self._poll_due(lifecycle, attempt, now)):
                    out.append((symbol, index, attempt.get("order_id"), attempt["client_id"]))
        return out

    def fetch_reads(
        self, reads: Sequence[Tuple[str, int, Optional[str], str]]
    ) -> Dict[Tuple[str, int], Any]:
        rows: Dict[Tuple[str, int], Any] = {}
        for symbol, index, order_id, client_id in reads:
            try:
                rows[(symbol, index)] = (
                    self.broker.get_order(order_id) if order_id else self.broker.get_order_by_client_id(client_id)
                )
            except Exception:
                continue
        return rows

    def apply_reads(self, rows: Dict[Tuple[str, int], Any], now: Optional[datetime] = None) -> bool:
        now = now or self.clock()
        before = self._signature()
        for (symbol, index), row in rows.items():
            lifecycle = self.state["lifecycles"].get(symbol)
            if lifecycle is None or symbol in self._jobs or not 0 <= index < len(lifecycle["attempts"]) or row is None:
                continue
            attempt = lifecycle["attempts"][index]
            if attempt["status"] in LIVE:
                self._apply_order_row(lifecycle, attempt, row, now)
        return self._signature() != before

    def reconcile(self, now: Optional[datetime] = None) -> None:
        """Read and book every known identity. This method never sends, cancels, or clears a worker job."""
        now = now or self.clock()
        self._reconciling = True
        try:
            rows = self.fetch_reads(self.pending_reads(now, force=True))
            self.apply_reads(rows, now)
            for lifecycle in self.state["lifecycles"].values():
                if lifecycle["symbol"] in self._jobs:
                    continue
                remaining = lifecycle["entry_qty"] - lifecycle["exit_qty"]
                try:
                    broker_qty = _broker_position(self.broker, lifecycle["symbol"])
                except QuantityError as exc:
                    self._halt(lifecycle, "FRACTIONAL_BROKER_QUANTITY", now, exc)
                    continue
                except Exception as exc:
                    lifecycle["last_error"] = type(exc).__name__
                    continue
                expected = remaining if lifecycle["side"] == "LONG" else -remaining if lifecycle["side"] == "SHORT" else 0
                if broker_qty != expected:
                    self._halt(lifecycle, "STARTUP_POSITION_MISMATCH", now)
                elif remaining > 0 and self._live_attempt(lifecycle) is None:
                    if not self._has_durable_protection(lifecycle):
                        self._precommit_protection(lifecycle)
                        if not self.checkpoint("DAY_ONE_PROTECTION_RECOVERY"):
                            lifecycle["protection"] = None
                            self._halt(lifecycle, "PROTECTION_NOT_DURABLE", now)
                            continue
                    lifecycle["phase"] = "HELD" if not lifecycle.get("emergency") else "RECOVERY"
                    if lifecycle["phase"] == "HELD":
                        self._ensure_cls(lifecycle, now)
                elif remaining == 0 and lifecycle["phase"] in ("DONE", "SKIPPED"):
                    self._verify_release(lifecycle, now)
        finally:
            self._reconciling = False
        self.checkpoint("DAY_ONE_RECONCILE")

    def open_entry_writes(self) -> None:
        self._entry_writes_open = True

    def close_entry_writes(self) -> None:
        self._entry_writes_open = False

    def close_exit_writes(self) -> None:
        self._exit_writes_open = False

    def drain(self, timeout: float = 5.0) -> bool:
        """Bound waiting for worker results while continuing lifecycle reconciliation."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self._jobs and time.monotonic() < deadline:
            _symbol, (_kind, future) = next(iter(self._jobs.items()))
            try:
                future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                break
            except Exception:
                pass
            self.tick(self.clock())
        return not self._jobs

    def shutdown(self, timeout: float = 5.0) -> bool:
        self.close_entry_writes()
        drained = self.drain(timeout)
        self.reconcile(self.clock())
        protected = all(
            lifecycle["entry_qty"] == lifecycle["exit_qty"] or self.protected(lifecycle["symbol"])
            for lifecycle in self.state["lifecycles"].values()
        )
        if drained and protected:
            self.close_exit_writes()
        return drained and protected

    def health(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or self.clock()
        lifecycles = []
        for lifecycle in self.state["lifecycles"].values():
            lifecycles.append({
                "symbol": lifecycle["symbol"],
                "strategy_id": lifecycle["strategy_id"],
                "session": lifecycle["session"],
                "phase": lifecycle["phase"],
                "side": lifecycle["side"],
                "target_qty": lifecycle["target_qty"],
                "entry_qty": lifecycle["entry_qty"],
                "exit_qty": lifecycle["exit_qty"],
                "released": lifecycle["released"],
                "evidence": d1.EVIDENCE[lifecycle["strategy_id"]],
            })
        recovery_halt = any(row["phase"] == "RECOVERY_HALT" for row in lifecycles)
        ready = (
            not recovery_halt
            and self.hooks.persistence_healthy()
            and not self.hooks.broker_mismatch()
            and all(
                lifecycle["entry_qty"] == lifecycle["exit_qty"] or self.protected(lifecycle["symbol"])
                for lifecycle in self.state["lifecycles"].values()
            )
        )
        return {
            "at": _iso(now),
            "running": True,
            "ready": ready,
            "entry_writes_open": self._entry_writes_open,
            "exit_writes_open": self._exit_writes_open,
            "checkpoint_revision": self._durable_revision,
            "workers": sorted(self._jobs),
            "lifecycles": lifecycles,
            "recovery_halt": recovery_halt,
        }

    def attestation(
        self,
        *,
        runtime_revision: Optional[str] = None,
        overnight_state_hash: Optional[str] = None,
        overnight_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        counts: Dict[str, Dict[str, int]] = {}
        for symbol, lifecycle in self.state["lifecycles"].items():
            per: Dict[str, int] = {}
            for attempt in lifecycle["attempts"]:
                per[attempt["role"]] = per.get(attempt["role"], 0) + 1
            counts[symbol] = per
        encoded = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":")).encode()
        relevant = [
            lifecycle for lifecycle in self.state["lifecycles"].values()
            if not lifecycle.get("released") and lifecycle["phase"] not in ("IDLE", "SKIPPED")
        ]
        return {
            "runtime_revision": runtime_revision,
            "checkpoint_revision": self._durable_revision,
            "expected_account_match": bool(relevant) and all(
                lifecycle.get("account_match") is True for lifecycle in relevant
            ),
            "broker_match": not self.hooks.broker_mismatch(),
            "overnight_state_hash": overnight_state_hash,
            "overnight_count": overnight_count,
            "day_one_state_hash": hashlib.sha256(encoded).hexdigest()[:16],
            "client_id_counts": counts,
            "recovery": {
                symbol: lifecycle["phase"]
                for symbol, lifecycle in self.state["lifecycles"].items()
                if lifecycle["phase"] in ("RECOVERY", "RECOVERY_HALT")
            },
        }


Hooks = DayOneHooks
DayOneExecutionController = DayOneController