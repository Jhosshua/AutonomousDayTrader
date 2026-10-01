# @steered SNARE-2 2026-09-30
"""Runtime integration for the isolated SPY and COIN day one controller.

This module supplies durable checkpoint revisions, strict ledger booking, shared
symbol ownership, read before compare reconciliation, bounded shutdown, health,
and startup attestation. It adds no route and reads no credential.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional, Tuple

from backend.app.core import day_one_schedule as d1
from backend.app.core.day_one_execution import DayOneController, DayOneHooks, InlineExecutor
from backend.app.core.overnight_schedule import TradingWindowsCalendar

log = logging.getLogger("day_one_integration")

STATE_VERSION = 1
LEDGER_LIMIT = 500
BOOKING_KEYS = frozenset({
    "kind", "strategy_id", "symbol", "session", "role", "side", "qty", "price", "at",
    "alpaca_order_id", "client_order_id", "target_qty", "cumulative_qty",
})


def _strict_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False, separators=(",", ":"), default=str))


def _position_side(position: Any) -> str:
    return str(getattr(getattr(position, "side", None), "value", getattr(position, "side", "")) or "").upper()


def _whole_quantity(value: Any, field: str) -> int:
    try:
        quantity = Decimal(str(0 if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if not quantity.is_finite() or quantity != quantity.to_integral_value():
        raise ValueError(f"{field} must be a finite whole number")
    return int(quantity)


class DayOneIntegration:
    """Glue between a runtime object and DayOneController."""

    def __init__(self, runtime: Any) -> None:
        self.r = runtime
        self.controller: Optional[DayOneController] = None
        self.executor: Any = None
        self.ledger: Dict[str, Any] = self._empty_ledger()
        self._pending: Optional[Dict[str, Any]] = None
        self._bad_state: Optional[Any] = None
        self._bad_claims: set[str] = set()
        self._restored_claims: set[str] = set()
        self.init_error: Optional[str] = None

    @staticmethod
    def _empty_ledger() -> Dict[str, Any]:
        return {"fills": {}, "recorded": []}

    def build(
        self,
        broker: Any,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        executor: Any = None,
        calendar: Any = None,
        spy_reference: Optional[Callable[[date], Any]] = None,
        coin_model: Optional[Callable[[date], Dict[str, Any]]] = None,
        hooks: Optional[DayOneHooks] = None,
        book: Optional[Callable[[Dict[str, Any]], bool]] = None,
        checkpoint_revision: Optional[int] = None,
    ) -> DayOneController:
        self.shutdown()
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="day_one")
        revision = int(
            checkpoint_revision if checkpoint_revision is not None else getattr(self.r, "persistence_revision", 0) or 0
        )
        deps = {
            "broker": broker,
            "executor": self.executor,
            "clock": clock or (lambda: datetime.now(timezone.utc)),
            "checkpoint": self._durable,
            "book": book or self._book,
            "spy_reference": spy_reference or self._spy_reference,
            "coin_model": coin_model or self._coin_model,
            "calendar": calendar or TradingWindowsCalendar(),
            "hooks": hooks or self._hooks(),
            "checkpoint_revision": revision,
        }
        state = (self._pending or {}).get("controller")
        self.controller = DayOneController.load(state, **deps)
        self.init_error = None
        return self.controller

    def start(self, broker: Any) -> None:
        if broker is None:
            self.init_error = "The day one controller needs the Alpaca paper broker."
            return
        if self._bad_state is not None or ((self._bad_claims or self._restored_claims) and self._pending is None):
            self.init_error = "Saved day one state is unreadable. Recovery ownership remains active."
            return
        try:
            self.build(broker)
        except Exception as exc:
            self.controller = None
            self.init_error = type(exc).__name__
            log.error("Day one controller did not start because %s", self.init_error)

    def reset(self) -> None:
        self.shutdown()
        self.controller = None
        self.ledger = self._empty_ledger()
        self._pending = None
        self._bad_state = None
        self._bad_claims.clear()
        self._restored_claims.clear()
        self.init_error = None

    def checkpoint_state(self) -> Optional[Dict[str, Any]]:
        if self._bad_state is not None:
            return _strict_copy(self._bad_state)
        if self.controller is None and self._pending is None and not self.ledger["fills"] and not self.ledger["recorded"]:
            return None
        controller = self.controller.to_json() if self.controller is not None else (self._pending or {}).get("controller")
        return _strict_copy({"version": STATE_VERSION, "controller": controller, "ledger": self.ledger})

    checkpoint = checkpoint_state

    def ownership_envelope(self) -> Dict[str, Any]:
        claims = set(self._bad_claims) | set(self._restored_claims)
        if self.controller is not None:
            claims.update(symbol for symbol in ("SPY", "COIN") if self.controller.owns(symbol))
        return {"claims": sorted(claims)}

    def restore_ownership_envelope(self, envelope: Any) -> None:
        if envelope is None:
            self._restored_claims.clear()
            return
        try:
            if not isinstance(envelope, dict) or set(envelope) != {"claims"}:
                raise ValueError("day one ownership envelope shape is invalid")
            claims = envelope["claims"]
            if not isinstance(claims, list):
                raise ValueError("day one ownership claims must be a list")
            normalized = {str(symbol).upper() for symbol in claims}
            if normalized - {"SPY", "COIN"} or len(normalized) != len(claims):
                raise ValueError("day one ownership claims are invalid")
            self._restored_claims = normalized
        except Exception:
            self._restored_claims = {"SPY", "COIN"}
            self.init_error = "RECOVERY_HALT:OWNERSHIP_ENVELOPE"

    def load_state(self, data: Optional[Dict[str, Any]]) -> None:
        self.ledger = self._empty_ledger()
        self._pending = None
        self._bad_state = None
        self._bad_claims = set(self._restored_claims)
        if not data:
            if self._restored_claims:
                self.init_error = "RECOVERY_HALT:MISSING_CONTROLLER_STATE"
            return
        try:
            if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
                raise ValueError("unknown day one integration state version")
            controller = data.get("controller")
            if controller is not None:
                DayOneController._validate_state(controller)
            decoded_claims = self._extract_claims(data)
            if self._restored_claims - decoded_claims:
                raise ValueError("day one ownership envelope is missing from controller state")
            ledger = data.get("ledger") or {}
            if not isinstance(ledger, dict):
                raise ValueError("day one ledger must be an object")
            fills = ledger.get("fills") or {}
            recorded = ledger.get("recorded") or []
            if not isinstance(fills, dict) or not isinstance(recorded, list) or len(recorded) > LEDGER_LIMIT:
                raise ValueError("day one ledger is invalid or unbounded")
            self._pending = _strict_copy(data)
            self.ledger = {"fills": _strict_copy(fills), "recorded": [str(x) for x in recorded]}
        except Exception as exc:
            self._bad_claims.update(self._extract_claims(data))
            try:
                self._bad_state = _strict_copy(data)
            except Exception:
                self._bad_state = {"version": "unreadable", "claimed": sorted(self._bad_claims)}
            self.controller = None
            self.init_error = f"RECOVERY_HALT:{type(exc).__name__}"
            log.error("Unreadable day one state entered recovery halt")
            return
        if self.controller is not None:
            prior = self.controller
            deps = {
                "broker": prior.broker,
                "executor": prior.executor,
                "clock": prior.clock,
                "checkpoint": prior._checkpoint_cb,
                "book": prior.book,
                "spy_reference": prior.spy_reference,
                "coin_model": prior.coin_model,
                "calendar": prior.calendar,
                "hooks": prior.hooks,
                "expected_account": prior.expected_account,
                "checkpoint_revision": prior._durable_revision,
            }
            self.controller = DayOneController.load(self._pending.get("controller"), **deps)
        self._bad_claims.clear()
        self._restored_claims.clear()
        self.init_error = None

    load = load_state

    @staticmethod
    def _extract_claims(data: Any) -> set[str]:
        claims: set[str] = set()
        if not isinstance(data, dict):
            return claims
        controller = data.get("controller") if isinstance(data.get("controller"), dict) else data
        lifecycles = controller.get("lifecycles") if isinstance(controller, dict) else None
        if isinstance(lifecycles, dict):
            for symbol, lifecycle in lifecycles.items():
                if symbol not in ("SPY", "COIN") or not isinstance(lifecycle, dict):
                    continue
                try:
                    entry_qty = _whole_quantity(lifecycle.get("entry_qty"), "saved entry quantity")
                    exit_qty = _whole_quantity(lifecycle.get("exit_qty"), "saved exit quantity")
                except (TypeError, ValueError):
                    entry_qty, exit_qty = 1, 0
                if lifecycle.get("released") is not True and (
                    entry_qty > exit_qty
                    or bool(lifecycle.get("attempts"))
                    or lifecycle.get("phase") not in (None, "IDLE", "DONE", "SKIPPED")
                ):
                    claims.add(symbol)
        ownership = controller.get("ownership") if isinstance(controller, dict) else None
        if isinstance(ownership, list):
            for row in ownership:
                if isinstance(row, dict) and str(row.get("symbol") or "").upper() in ("SPY", "COIN"):
                    claims.add(str(row["symbol"]).upper())
        return claims

    def _durable(self, reason: str) -> int:
        if getattr(self.r, "inflight_event_keys", None):
            return 0
        before = int(getattr(self.r, "persistence_revision", 0) or 0)
        saved = self.r._checkpoint_runtime(reason)
        after = int(getattr(self.r, "persistence_revision", 0) or 0)
        return after if saved and after > before else 0

    def _hooks(self) -> DayOneHooks:
        return DayOneHooks(
            held_by_other=self._held_by_other,
            broker_mismatch=lambda: bool(getattr(self.r, "broker_state", {}).get("mismatch")),
            persistence_healthy=lambda: bool(getattr(self.r, "persistence_healthy", False)),
            admission=self._admission,
            usable_buying_power=self._usable_buying_power,
            local_quantity=self._local_quantity,
            on_release=lambda symbol: log.info("Day one released %s", symbol),
            ownership_lock=self._ownership_lock,
        )

    def _ownership_lock(self) -> Any:
        lock = getattr(getattr(self.r, "orb", None), "lock", None)
        return lock if lock is not None else nullcontext()

    def _held_by_other(self, symbol: str) -> Optional[str]:
        symbol = symbol.upper()
        position = getattr(getattr(self.r, "account", None), "positions", {}).get(symbol)
        if position is not None and not d1.is_day_one(position):
            return str(getattr(position, "strategy_id", "OTHER") or "OTHER")
        for name, method in (
            ("overnight", "claimed"),
            ("orb", "owns"),
            ("tri_controller", "reserves"),
            ("or15_controller", "reserves"),
        ):
            owner = getattr(self.r, name, None)
            check = getattr(owner, method, None)
            if callable(check) and check(symbol):
                return name
        if symbol in set(getattr(self.r, "swing_reserved_symbols", set()) or set()):
            return "swing"
        staged = getattr(self.r, "swing_staged_order_manager", None)
        if staged is not None and callable(getattr(staged, "is_staged_for_entry", None)) \
                and staged.is_staged_for_entry(symbol):
            return "swing_staged"
        brackets = getattr(getattr(self.r, "bracket_manager", None), "symbol_to_bracket", {})
        if symbol in brackets:
            return "bracket"
        engine = getattr(self.r, "engine", None)
        for order in getattr(engine, "working_orders", {}).values():
            if str(getattr(order, "symbol", "") or "").upper() == symbol and not d1.is_day_one(order):
                return str(getattr(order, "strategy_id", "ORDER") or "ORDER")
        return None

    def _admission(self, symbol: str, side: str, qty: int, notional: float) -> Optional[str]:
        callback = getattr(self.r, "day_one_admission", None)
        if callable(callback):
            result = callback(symbol, side, qty, notional)
            return str(result) if result else None
        return "DAY_ONE_ADMISSION_UNAVAILABLE"

    def _usable_buying_power(self, account: Dict[str, Any], symbol: str) -> float:
        callback = getattr(self.r, "day_one_usable_buying_power", None)
        if callable(callback):
            value = callback(account, symbol)
            return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0
        return float(account.get("buying_power") or 0.0)

    def _local_quantity(self, symbol: str, strategy_id: str) -> Optional[int]:
        position = getattr(getattr(self.r, "account", None), "positions", {}).get(symbol.upper())
        if position is None:
            return 0
        if not d1.is_day_one(position) or getattr(position, "strategy_id", None) != strategy_id:
            return None
        shares = _whole_quantity(getattr(position, "shares", 0), "local position quantity")
        return shares if _position_side(position) == "LONG" else -shares

    def _spy_reference(self, session: date) -> Any:
        callback = getattr(self.r, "day_one_spy_reference", None)
        if not callable(callback):
            raise RuntimeError("validated SPY reference capability was not injected")
        return callback(session)

    def _coin_model(self, session: date) -> Dict[str, Any]:
        callback = getattr(self.r, "day_one_coin_model", None)
        if not callable(callback):
            raise RuntimeError("fixed relay COIN model capability was not injected")
        value = callback(session)
        if not isinstance(value, dict):
            raise ValueError("COIN model capability returned an invalid value")
        return value

    def reserve_coin_before_events(self, session: Optional[date] = None) -> bool:
        controller = self.controller
        if controller is None:
            return False
        with self._ownership_lock():
            conflict = self._held_by_other("COIN")
            return controller.reserve_coin_before_events(session, conflict=conflict)

    def owns(self, symbol: str) -> bool:
        symbol = symbol.upper()
        return (
            symbol in self._bad_claims
            or symbol in self._restored_claims
            or bool(self.controller is not None and self.controller.owns(symbol))
        )

    claimed = owns

    def order_refusal(self, order: Any, qty: Optional[int] = None) -> Optional[str]:
        if d1.is_day_one(order):
            return None
        symbol = str(getattr(order, "symbol", "") or "").upper()
        if not self.claimed(symbol):
            return None
        position = getattr(getattr(self.r, "account", None), "positions", {}).get(symbol)
        side = str(getattr(getattr(order, "side", None), "value", getattr(order, "side", "")) or "").upper()
        reducing = bool(
            position is not None
            and d1.is_day_one(position)
            and (
                (_position_side(position) == "LONG" and side == "SELL")
                or (_position_side(position) == "SHORT" and side == "BUY")
            )
        )
        if reducing and not self.safe_to_exempt(symbol):
            return None
        raw_amount = qty if qty is not None else getattr(order, "remaining_qty", getattr(order, "qty", 0))
        try:
            amount = _whole_quantity(raw_amount, "guarded order quantity")
        except ValueError:
            return "DAY_ONE_QUANTITY_INVALID"
        return f"DAY_ONE_OWNED: {symbol} is isolated from other orders while its lifecycle owns {amount} shares"

    def broker_guard(self, order: Any, qty: int) -> Optional[str]:
        return self.order_refusal(order, qty)

    def occupied_reason(self, symbol: str) -> Optional[str]:
        return f"{symbol.upper()} is reserved or held by the day one controller" if self.claimed(symbol) else None

    def commitments(self, exclude_symbol: Optional[str] = None) -> Dict[str, float]:
        return self.controller.commitments(exclude_symbol) if self.controller is not None else {}

    def protected(self, symbol: str) -> bool:
        controller = self.controller
        if controller is None:
            return False
        sym = symbol.upper()
        lifecycle = controller.state["lifecycles"].get(sym)
        position = getattr(getattr(self.r, "account", None), "positions", {}).get(sym)
        if position is not None and d1.is_day_one(position):
            if lifecycle is None:
                return False
            try:
                local_quantity = _whole_quantity(getattr(position, "shares", 0), "local position quantity")
            except ValueError:
                return False
            remaining = lifecycle["entry_qty"] - lifecycle["exit_qty"]
            if local_quantity < 1 or remaining != local_quantity:
                return False
        return controller.protected(sym)

    safe_to_exempt = protected

    def reserved_risk(self, remaining_daily_loss: float) -> float:
        return self.controller.reserved_risk(remaining_daily_loss) if self.controller is not None else (
            max(0.0, float(remaining_daily_loss)) if self._bad_claims else 0.0
        )

    def position_details(self, symbol: str) -> Dict[str, Any]:
        controller = self.controller
        lifecycle = None if controller is None else controller.state["lifecycles"].get(symbol.upper())
        if lifecycle is None:
            return {}
        close = datetime.combine(date.fromisoformat(lifecycle["session"]),
                                 datetime.strptime(lifecycle["session_close"], "%H:%M").time(), d1.ET)
        return {
            "strategy_id": lifecycle["strategy_id"],
            "exit_due": close.isoformat(),
            "day_one": True,
            "evidence": d1.EVIDENCE[lifecycle["strategy_id"]],
            "no_strategy_stop": True,
            "no_strategy_target": True,
        }

    def request_exit(self, symbol: str, reason: str, now: Optional[datetime] = None) -> bool:
        return bool(self.controller is not None and self.controller.request_exit(symbol, reason, now))

    def on_bar(self, bar: Any) -> bool:
        return bool(self.controller is not None and self.controller.on_bar(bar))

    def on_quote(self, quote: Any) -> bool:
        return bool(self.controller is not None and self.controller.on_quote(quote))

    def tick(self, now: datetime) -> None:
        if self.controller is None:
            return
        self.controller.tick(now)
        if self._record_completed():
            self.r._checkpoint_runtime("DAY_ONE_TRADE")

    def _booking_contradiction(self, symbol: str, reason: str) -> bool:
        log.error("Day one booking contradiction for %s", symbol)
        if self.controller is not None:
            self.controller.recovery_halt(symbol, reason)
        return False

    def _validate_booking(self, event: Dict[str, Any]) -> Tuple[Dict[str, Any], Any, str]:
        if not isinstance(event, dict) or set(event) != BOOKING_KEYS or event.get("kind") != "fill":
            raise ValueError("day one booking event shape is invalid")
        symbol = str(event["symbol"]).upper()
        if symbol not in ("SPY", "COIN"):
            raise ValueError("day one booking symbol is invalid")
        if event["strategy_id"] != (d1.SPY_ID if symbol == "SPY" else d1.COIN_ID):
            raise ValueError("day one booking strategy is invalid")
        if event["role"] not in ("entry", "exit", "recovery") or event["side"] not in ("buy", "sell"):
            raise ValueError("day one booking role or side is invalid")
        if not isinstance(event["qty"], int) or isinstance(event["qty"], bool) or event["qty"] < 1:
            raise ValueError("day one booking quantity is invalid")
        price = event["price"]
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0:
            raise ValueError("day one booking price is invalid")
        if not all(isinstance(event[key], str) and event[key] for key in (
            "session", "at", "alpaca_order_id", "client_order_id"
        )):
            raise ValueError("day one booking identity is invalid")
        lifecycle = None if self.controller is None else self.controller.state["lifecycles"].get(symbol)
        if lifecycle is None or lifecycle["session"] != event["session"] or lifecycle["strategy_id"] != event["strategy_id"]:
            raise ValueError("day one booking lifecycle is missing")
        return lifecycle, getattr(getattr(self.r, "account", None), "positions", {}).get(symbol), symbol

    def _book(self, event: Dict[str, Any]) -> bool:
        try:
            lifecycle, position, symbol = self._validate_booking(event)
        except Exception:
            symbol = str(event.get("symbol") or "UNKNOWN").upper() if isinstance(event, dict) else "UNKNOWN"
            return self._booking_contradiction(symbol, "BOOKING_EVENT_INVALID")
        expected_position_side = "LONG" if lifecycle["side"] == "LONG" else "SHORT"
        role = event["role"]
        try:
            position_quantity = _whole_quantity(
                getattr(position, "shares", 0) if position is not None else 0,
                "local position quantity",
            )
        except ValueError:
            return self._booking_contradiction(symbol, "FRACTIONAL_LOCAL_QUANTITY")
        if role == "entry":
            if position is not None and (
                not d1.is_day_one(position)
                or getattr(position, "strategy_id", None) != event["strategy_id"]
                or _position_side(position) != expected_position_side
            ):
                return self._booking_contradiction(symbol, "ENTRY_POSITION_CONTRADICTION")
        else:
            expected_order_side = "sell" if expected_position_side == "LONG" else "buy"
            if (
                event["side"] != expected_order_side
                or position is None
                or not d1.is_day_one(position)
                or getattr(position, "strategy_id", None) != event["strategy_id"]
                or _position_side(position) != expected_position_side
                or event["qty"] > position_quantity
            ):
                return self._booking_contradiction(symbol, "EXIT_POSITION_CONTRADICTION")
        r = self.r
        side = r.OrderSide.BUY if event["side"] == "buy" else r.OrderSide.SELL
        order = r.engine.create_order(
            symbol=symbol,
            side=side,
            order_type=r.OrderType.MARKET,
            qty=event["qty"],
            estimated_price=event["price"],
            strategy_id=event["strategy_id"],
            client_order_id=event["client_order_id"],
            parent_order_id=f"day_one:{symbol}:{event['session']}",
            arm=r.TradingArm.INTRADAY,
        )
        order.execution_policy = d1.DAY_ONE_POLICY
        at = datetime.fromisoformat(event["at"].replace("Z", "+00:00"))
        fill = r.engine._apply_fill_to_ledger(order, event["qty"], event["price"], 0.0, 0.0, at)
        reconcile = getattr(r, "_reconcile_fills", None)
        if callable(reconcile):
            reconcile([fill])
        key = f"{symbol}:{event['session']}"
        row = {
            "role": role,
            "side": event["side"],
            "qty": event["qty"],
            "price": event["price"],
            "at": event["at"],
            "alpaca_order_id": event["alpaca_order_id"],
            "client_order_id": event["client_order_id"],
            "local_order_id": order.id,
            "fill_id": fill.fill_id,
            "realized_pnl": fill.realized_pnl,
        }
        self.ledger["fills"].setdefault(key, []).append(_strict_copy(row))
        return True

    def _record_completed(self) -> bool:
        controller = self.controller
        if controller is None:
            return False
        changed = False
        recorded = set(self.ledger["recorded"])
        for lifecycle in controller.state["lifecycles"].values():
            key = f"{lifecycle['symbol']}:{lifecycle['session']}"
            if key in recorded or lifecycle["phase"] != "DONE" or not lifecycle["released"]:
                continue
            if lifecycle["entry_qty"] != lifecycle["exit_qty"] or any(
                attempt["status"] not in ("filled", "canceled", "expired", "rejected")
                for attempt in lifecycle["attempts"]
            ):
                continue
            legs = self.ledger["fills"].get(key) or []
            entries = [row for row in legs if row["role"] == "entry"]
            exits = [row for row in legs if row["role"] in ("exit", "recovery")]
            if not entries or sum(row["qty"] for row in entries) != sum(row["qty"] for row in exits):
                continue
            quantity = sum(row["qty"] for row in entries)
            entry_average = sum(row["qty"] * row["price"] for row in entries) / quantity
            exit_average = sum(row["qty"] * row["price"] for row in exits) / quantity
            pnl = sum(float(row["realized_pnl"]) for row in exits)
            trade = {
                "trade_id": f"day_one_{lifecycle['strategy_id']}_{lifecycle['session']}",
                "session_date": lifecycle["session"],
                "symbol": lifecycle["symbol"],
                "side": lifecycle["side"],
                "status": "CLOSED",
                "strategy_id": lifecycle["strategy_id"],
                "opened_at": min(row["at"] for row in entries),
                "closed_at": max(row["at"] for row in exits),
                "quantity": quantity,
                "avg_entry_price": entry_average,
                "avg_exit_price": exit_average,
                "realized_pnl": pnl,
                "fees": 0.0,
                "execution_mode": "alpaca_paper",
                "evidence": d1.EVIDENCE[lifecycle["strategy_id"]],
                "model_decision": lifecycle["decision"],
                "operational_gates": lifecycle["operational_gates"],
                "actual_legs": lifecycle["actual_legs"],
                "execution_deviation": lifecycle["execution_deviations"],
                "fill_legs": legs,
            }
            sanitizer = getattr(self.r, "_sanitize_for_json", _strict_copy)
            trade = sanitizer(trade)
            self.r.pending_trade_records[trade["trade_id"]] = trade
            self.ledger["recorded"].append(key)
            self.ledger["recorded"] = self.ledger["recorded"][-LEDGER_LIMIT:]
            self.ledger["fills"].pop(key, None)
            changed = True
        return changed

    def reconcile(self, now: Optional[datetime] = None) -> None:
        if self.controller is None:
            return
        self.controller.reconcile(now)
        self._record_completed()
        self.r._checkpoint_runtime("DAY_ONE_RECONCILE")

    async def before_compare(self, positions: Dict[str, int]) -> Dict[str, int]:
        controller = self.controller
        if controller is None:
            return positions
        reads = controller.pending_reads()
        if reads:
            rows = await asyncio.to_thread(controller.fetch_reads, reads)
            changed = controller.apply_reads(rows)
            if changed:
                self._record_completed()
                self.r._checkpoint_runtime("DAY_ONE_SYNC")
                get_positions = getattr(controller.broker, "get_positions", None)
                if callable(get_positions):
                    positions = await asyncio.to_thread(get_positions)
                else:
                    positions = dict(positions)
                    for symbol in ("SPY", "COIN"):
                        positions[symbol] = await asyncio.to_thread(controller.broker.position_qty, symbol)
        return positions

    def shutdown(self, timeout: float = 5.0) -> bool:
        controller = self.controller
        exposed = bool(controller is not None and controller.has_exposure())
        safe = True if controller is None else controller.shutdown(timeout)
        executor = self.executor
        self.executor = None
        if isinstance(executor, ThreadPoolExecutor):
            executor.shutdown(wait=exposed, cancel_futures=not exposed)
        return safe

    def health(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        if self.controller is None:
            return {
                "running": False,
                "init_error": self.init_error,
                "claimed": sorted(self._bad_claims | self._restored_claims),
                "recovery_halt": bool(self._bad_claims or self._restored_claims),
                "ready": False,
            }
        return {"init_error": self.init_error, **self.controller.health(now)}

    def readiness(self) -> Dict[str, Any]:
        controller = self.controller
        phases = {
            d1.SPY_ID: "NOT_STARTED",
            d1.COIN_ID: "NOT_STARTED",
        }
        ready = controller is not None
        if controller is not None:
            for lifecycle in controller.state["lifecycles"].values():
                phases[lifecycle["strategy_id"]] = lifecycle["phase"]
            ready = bool(controller.health().get("ready"))
        positions = getattr(getattr(self.r, "account", None), "positions", {})
        for symbol, position in positions.items():
            if d1.is_day_one(position) and not self.safe_to_exempt(symbol):
                ready = False
        if self._bad_claims or self._restored_claims:
            ready = False
        return {
            "mode": "live" if controller is not None else "unavailable",
            "readiness": "ready" if ready else "not_ready",
            "strategy_phases": phases,
        }

    def attestation(self, runtime_revision: Optional[str] = None) -> Dict[str, Any]:
        if self.controller is None:
            return {
                "runtime_revision": runtime_revision,
                "checkpoint_revision": getattr(self.r, "persistence_revision", None),
                "expected_account_match": False,
                "broker_match": False,
                "client_id_counts": {},
                "recovery": {
                    symbol: "RECOVERY_HALT" for symbol in sorted(self._bad_claims | self._restored_claims)
                },
            }
        overnight = getattr(self.r, "overnight", None)
        overnight_state = overnight.checkpoint_state() if overnight is not None and hasattr(overnight, "checkpoint_state") else None
        encoded = json.dumps(overnight_state, sort_keys=True, default=str).encode() if overnight_state is not None else b""
        overnight_count = 0
        if isinstance(overnight_state, dict):
            nights = ((overnight_state.get("controller") or {}).get("nights") or {})
            overnight_count = len(nights) if isinstance(nights, dict) else 0
        return self.controller.attestation(
            runtime_revision=runtime_revision,
            overnight_state_hash=hashlib.sha256(encoded).hexdigest()[:16],
            overnight_count=overnight_count,
        )
