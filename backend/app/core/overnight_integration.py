# @steered SNARE-2 2026-09-30
"""Overnight holds (NVDA, IREN, HUT) wired into the running robot (backend.app.main).

PLAN_2026_09_30_overnight_holds.md sections 3, 4.1 to 4.9, shared code decisions S1 to S20.
The controller (core/overnight_execution.py) places and books its own Alpaca orders. This module
is the glue, in the style of core/orb_integration.py:

- builds the controller in lifespan when a broker is attached, with a worker pool for its I/O;
- books its fills into ADT's ledger through the call ORB uses (engine._apply_fill_to_ledger on a
  local Order that carries OVERNIGHT_POLICY and no broker ids, created and filled at once, so
  nothing overnight ever rests in engine.working_orders), never merged into a non overnight
  position, and records one closed trade per night on the day it sells;
- the S1/S2 order rule, the X6 early close of a same stock day trade, the S13 compare steps,
  the S14 loss offset kept inside the risk engine, the checkpoint key "overnight" (plain JSON);
- the page and API payloads (health, /api/overnight, the websocket frame).

Everything here runs on the event loop except the jobs it hands to the worker pool.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.core import overnight_schedule as osch
from backend.app.core.overnight_execution import (
    HOLD_STATES, HELD, IDLE, SKIPPED, SOLD, Hooks, InlineExecutor, OvernightController,
)
from backend.app.core.overnight_schedule import ET, OVERNIGHT_POLICY, et, et_date, is_overnight

log = logging.getLogger("overnight_integration")

STATE_VERSION = 1
MODES = ("live", "off")
X6_ID = "OVERNIGHT_X6"                   # the controller's own early close of a day trade (D7)
X6_RETRY_SEC = 5.0
SPLIT_CHECK_SEC = 600.0                  # S13: corporate actions re-read at most every 10 minutes
RESEARCH_SUMMARY = Path(__file__).resolve().parents[1] / "data" / "overnight_research_summary.json"
NAMES = {"NVDA": "NVDA overnight", "IREN": "IREN overnight", "HUT": "HUT overnight"}


def _day_label(d: date) -> str:
    return d.strftime("%a %b ") + str(d.day)


def _parse(raw: Any, fallback: datetime) -> datetime:
    try:
        out = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


class OvernightIntegration:
    def __init__(self, runtime: Any) -> None:
        self.r = runtime
        self.controller: Optional[OvernightController] = None
        self.executor: Any = None
        self.mode = "off"
        self.configured_mode = "off"
        self.init_error: Optional[str] = None
        self.ledger: Dict[str, Any] = self._empty_ledger()
        self._pending: Optional[Dict[str, Any]] = None       # restored before the controller is built
        self._x6_jobs: Dict[str, Future] = {}
        self._x6_last: Dict[str, float] = {}
        self._split_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self.splits_shown: Dict[str, str] = {}
        self._fallback_price: Dict[str, float] = {}
        self.fidelity_jobs: Dict[str, Future] = {}
        self._bad_state: Optional[Any] = None                # a saved section this build cannot read

    @staticmethod
    def _empty_ledger() -> Dict[str, Any]:
        # offset: S14 overnight result booked into equity in its session (date = ADT's session);
        # fills: per night the legs booked so far (one closed trade row is written at SOLD);
        # recorded: nights that already have their trade row; x6: the early day trade closes
        return {"offset": {"date": None, "amount": 0.0}, "fills": {}, "recorded": [], "x6": {}}

    # ------------------------------------------------------------------ construction
    def settings_view(self) -> Dict[str, Any]:
        s = self.r.settings
        enabled = [sym for sym in osch.SYMBOLS if bool(getattr(s, f"OVERNIGHT_{sym}", True))]
        return {"mode": str(getattr(s, "OVERNIGHT_MODE", "live") or "live").strip().lower(), "enabled": enabled,
                "pct": float(getattr(s, "OVERNIGHT_PCT", 0.20)),
                "cap": float(getattr(s, "OVERNIGHT_POSITION_CAP", 25_000.0)),
                "room_multiple": float(getattr(s, "OVERNIGHT_ROOM_MULTIPLE", 2.0))}

    def build(self, broker: Any, *, mode: Optional[str] = None, clock: Optional[Callable[[], datetime]] = None,
              executor: Any = None, calendar: Any = None, bar_count: Optional[Callable] = None,
              last_price: Optional[Callable] = None, enabled: Optional[Tuple[str, ...]] = None,
              pct: Optional[float] = None) -> OvernightController:
        """Build the controller with ADT's hooks. State restored from the checkpoint (if any)."""
        cfg = self.settings_view()
        mode = (mode or cfg["mode"]).strip().lower()
        if mode not in MODES:
            raise ValueError(f"OVERNIGHT_MODE must be live or off, not {mode!r}")
        self.shutdown()
        self.configured_mode = self.mode = mode
        self.executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="overnight")
        deps = dict(
            broker=broker, executor=self.executor, calendar=calendar or osch.TradingWindowsCalendar(),
            clock=clock or (lambda: datetime.now(timezone.utc)), book=self._book, checkpoint=self._durable,
            bar_count=bar_count or self._relay_bar_count, last_price=last_price or self._last_price,
            alert=self._alert, hooks=Hooks(day_shares=self._day_shares, close_day_trade=self._close_day_trade,
                                           cancel_day_entries=self._cancel_day_entries,
                                           broker_mismatch=self._broker_mismatch, held_by_other=self._held_by_other,
                                           swing_market_value=self._swing_value, on_release=self._on_release),
            mode=mode, enabled=tuple(enabled if enabled is not None else cfg["enabled"]),
            pct=cfg["pct"] if pct is None else pct, cap=cfg["cap"], room_multiple=cfg["room_multiple"])
        pending = self._pending
        self.controller = OvernightController.from_json((pending or {}).get("controller"), **deps)
        self.init_error = None
        return self.controller

    def start(self, broker: Any) -> None:
        """Lifespan: build when a broker is attached (BROKER_MODE alpaca_paper). A bad setting or a
        saved state this build cannot read turns the overnight holds off and says so; ADT starts."""
        if broker is None:
            self.init_error = "No broker is attached (BROKER_MODE is not alpaca_paper), so no overnight order is sent."
            return
        if self._bad_state is not None:
            log.error("Overnight holds are OFF: %s", self.init_error)
            return
        try:
            self.build(broker)
        except Exception as exc:
            self.init_error = f"{type(exc).__name__}: {exc}"[:300]
            self.controller = None
            log.error("Overnight holds are OFF: %s", self.init_error)

    def shutdown(self) -> None:
        ex = self.executor
        self.executor = None
        if isinstance(ex, ThreadPoolExecutor):
            ex.shutdown(wait=False, cancel_futures=True)

    def reset(self) -> None:
        self.shutdown()
        self.controller = None
        self.mode = self.configured_mode = "off"
        self.init_error = None
        self.ledger = self._empty_ledger()
        self._pending = None
        self._bad_state = None
        self._x6_jobs.clear()
        self._x6_last.clear()
        self._split_cache.clear()
        self.splits_shown.clear()
        self._fallback_price.clear()
        self.fidelity_jobs.clear()

    # ------------------------------------------------------------------ checkpoint (plain JSON)
    def checkpoint_state(self) -> Optional[Dict[str, Any]]:
        """The checkpoint key "overnight": plain JSON only, so an older build decodes the payload."""
        if self._bad_state is not None:
            return json.loads(json.dumps(self._bad_state, default=str))     # saved back unchanged
        if self.controller is None and self._pending is None and not self.ledger["fills"] \
                and not self.ledger["recorded"] and self.ledger["offset"]["date"] is None:
            return None
        ctl_state = self.controller.to_json() if self.controller is not None else (self._pending or {}).get("controller")
        return json.loads(json.dumps({"version": STATE_VERSION, "controller": ctl_state, "ledger": self.ledger},
                                     default=str))

    def load_state(self, data: Optional[Dict[str, Any]]) -> None:
        """Restore (an old checkpoint without the key restores to an empty state)."""
        self.ledger = self._empty_ledger()
        self._pending = None
        self._bad_state = None
        if not data:
            return
        ctl_version = (data.get("controller") or {}).get("version") if isinstance(data, dict) else None
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION or ctl_version not in (None, 1):
            # Fail closed for the overnight holds only: never read as empty (that could book a fill
            # twice or forget a hold). ADT starts; the section is saved back unchanged.
            self._bad_state = data
            self.controller = None
            self.init_error = ("The saved overnight state has a version this build cannot read. The overnight "
                               "holds are off until a person checks NVDA, IREN and HUT at Alpaca.")
            log.error(self.init_error)
            return
        self._pending = json.loads(json.dumps(data))
        led = self._pending.get("ledger") or {}
        for k in self.ledger:
            if k in led:
                self.ledger[k] = led[k]
        if self.controller is not None:           # a controller already built re-reads its state
            deps = {k: getattr(self.controller, k) for k in ("broker", "executor", "calendar", "clock", "book",
                                                             "checkpoint", "bar_count", "last_price", "alert",
                                                             "hooks", "mode", "enabled", "pct", "cap",
                                                             "room_multiple")}
            self.controller = OvernightController.from_json(self._pending.get("controller"), **deps)

    def apply_risk_offset(self) -> None:
        """S14: re-applied after every restore (runtime_state.py:236 and main.py:896 rewrite the
        risk baselines) and after every booking."""
        r = self.r
        off = self.ledger["offset"]
        session = r.last_session_date.isoformat() if r.last_session_date is not None else None
        amount = float(off.get("amount") or 0.0) if off.get("date") and off.get("date") == session else 0.0
        r.risk_engine.set_overnight_realized(amount, session)

    def realized_today(self) -> float:
        r = self.r
        off = self.ledger["offset"]
        session = r.last_session_date.isoformat() if r.last_session_date is not None else None
        return float(off.get("amount") or 0.0) if off.get("date") and off.get("date") == session else 0.0

    def _durable(self, reason: str) -> bool:
        """A buy intent needs a durable save before it is sent (tri's guard, not
        _checkpoint_runtime alone, which returns True while an input is in flight)."""
        if self.r.inflight_event_keys:
            return False
        return bool(self.r._checkpoint_runtime(reason))

    # ------------------------------------------------------------------ queries (S1, S11, S12)
    def reserves(self, symbol: str) -> bool:
        ctl = self.controller
        return bool(ctl is not None and ctl.reserves(symbol))

    def holds_symbol(self, symbol: str) -> bool:
        """An overnight hold in ADT's book, or a lifecycle that forbids every other order."""
        sym = symbol.upper()
        if is_overnight(self.r.account.positions.get(sym)):
            return True
        ctl = self.controller
        return bool(ctl is not None and ctl.locks_all(sym))

    def claimed(self, symbol: str) -> bool:
        return self.reserves(symbol) or self.holds_symbol(symbol)

    def sale_due(self, symbol: str) -> Optional[datetime]:
        ctl = self.controller
        return ctl.sale_due(symbol) if ctl is not None else None

    def sale_text(self, symbol: str) -> str:
        due = self.sale_due(symbol)
        if due is None:
            return f"{symbol} overnight sells at the next 9:30 AM open."
        return f"{symbol} overnight sells at the 9:30 AM open on {_day_label(due.astimezone(ET).date())}."

    def order_refusal(self, order: Any, qty: Optional[int] = None) -> Optional[str]:
        """S1 (validator) and S2 (broker choke point). In a stock the controller reserves or holds,
        refuse from anyone but the controller any entry and any sell larger than the non overnight
        shares; once a hold exists refuse every other order in that stock."""
        if getattr(order, "execution_policy", None) == OVERNIGHT_POLICY:
            return None
        sym = str(getattr(order, "symbol", "") or "").upper()
        if not self.claimed(sym):
            return None
        who = str(getattr(order, "strategy_id", "") or "")
        if self.holds_symbol(sym) and who != X6_ID:
            return (f"OVERNIGHT_HOLD: {sym} is held overnight by {NAMES.get(sym, sym)} (or its buy is at Alpaca); "
                    f"no other order may touch it. {self.sale_text(sym)}")
        pos = self.r.account.positions.get(sym)
        side = getattr(getattr(order, "side", None), "value", getattr(order, "side", None))
        q = int(qty if qty is not None else getattr(order, "remaining_qty", None) or getattr(order, "qty", 0) or 0)
        if pos is not None and not is_overnight(pos):
            reducing = (pos.side.value == "LONG" and side == "SELL") or (pos.side.value == "SHORT" and side == "BUY")
            if reducing and q <= pos.shares:
                return None                # the day trade's own stop and exits keep working (R2-1)
        return (f"OVERNIGHT_RESERVED: {sym} is reserved for {NAMES.get(sym, sym)} from 3:45 PM until its sale is "
                "booked; no other strategy may open or add to it.")

    def broker_guard(self, order: Any, qty: int) -> Optional[str]:
        return self.order_refusal(order, qty)

    def occupied_reason(self, symbol: str) -> Optional[str]:
        """S11 (ORB's adt_occupied): a reserved or held stock is not free for ORB."""
        if self.claimed(symbol):
            return f"{symbol.upper()} is reserved or held by the overnight holds"
        return None

    # ------------------------------------------------------------------ controller hooks
    def _day_shares(self, symbol: str) -> int:
        pos = self.r.account.positions.get(symbol.upper())
        return int(pos.shares) if pos is not None and not is_overnight(pos) else 0

    def _broker_mismatch(self) -> bool:
        return bool(self.r.broker_state.get("mismatch"))

    def _held_by_other(self, symbol: str) -> Optional[str]:
        r, sym = self.r, symbol.upper()
        if r.orb.owns(sym):
            return "ORB"
        if r.tri_controller.reserves(sym):
            return "Morning plan"
        if r.or15_controller.reserves(sym):
            return "OR15"
        pos = r.account.positions.get(sym)
        if pos is not None and r._is_swing_arm(pos):
            return "Slow trades"
        if sym in r.swing_reserved_symbols or r.swing_staged_order_manager.is_staged_for_entry(sym):
            return "Slow trades"
        return None

    def _swing_value(self) -> float:
        r = self.r
        return float(sum(abs(p.market_value) for p in r.account.positions.values() if r._is_swing_arm(p)))

    def _on_release(self, symbol: str) -> None:
        log.info("Overnight hold released %s back to the day strategies", symbol)

    def _alert(self, kind: str, text: str, fields: Dict[str, Any]) -> None:
        log.warning("OVERNIGHT ALERT %s: %s", kind, text)

    def _last_price(self, symbol: str) -> Optional[float]:
        sym = symbol.upper()
        px = self.r.latest_market_prices.get(sym)
        if px is None or not (isinstance(px, (int, float)) and math.isfinite(px) and px > 0):
            px = self._fallback_price.get(sym)
        return float(px) if px else None

    def _relay_bar_count(self, symbol: str, session_date: date) -> Tuple[int, bool]:
        """Worker thread. X2: SIP REST bars (regular session only) through main's
        _fetch_session_minutes; only bars that start before 15:45 count. A failure raises, which the
        controller turns into RELAY_UNAVAILABLE and retries until 15:49:30 (then a logged skip)."""
        r = self.r
        if not (r.settings.RELAY_TOKEN and r.settings.RELAY_HTTP_URL):
            raise RuntimeError("relay REST is not configured")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("the relay bar count must run on the worker pool, not the event loop")
        bars = asyncio.run(r._fetch_session_minutes([symbol.upper()], session_date, deadline_sec=8.0))
        mine = [b for b in bars if b.symbol.upper() == symbol.upper()]
        if mine:
            last = max(mine, key=lambda b: b.timestamp)
            if last.close > 0:
                self._fallback_price[symbol.upper()] = float(last.close)
        return osch.count_bars_before_cutoff([b.timestamp for b in mine], session_date)

    # ------------------------------------------------------------------ X6 (D7) and entry cancels
    def _cancel_day_entries(self, symbol: str) -> None:
        """15:46:05: cancel every other working entry in the stock (a protective order stays)."""
        r, sym = self.r, symbol.upper()
        pos = r.account.positions.get(sym)
        for oid, order in list(r.engine.working_orders.items()):
            if order.symbol != sym or r._is_swing_arm(order):
                continue
            protective = bool(pos is not None and (
                (pos.side.value == "LONG" and order.side.value == "SELL") or
                (pos.side.value == "SHORT" and order.side.value == "BUY")))
            if not protective:
                r.engine.cancel_order(oid, reason="OVERNIGHT_RESERVED_ENTRY_CANCEL")
                log.warning("Overnight %s: cancelled day entry %s before the closing auction buy", sym, oid)
        r._release_dead_entry_brackets()
        if pos is None:
            r.bracket_manager.cancel_pending_entry_bracket(sym)

    def _close_day_trade(self, symbol: str) -> None:
        """X6: close a same stock day trade through the manual flatten steps (bracket cancelled,
        pending entry bracket cleared, the trade recorded through _reconcile_fills), with the Alpaca
        order sent on the worker pool and tagged as the controller's (OVERNIGHT_X6)."""
        r, sym = self.r, symbol.upper()
        pos = r.account.positions.get(sym)
        job = self.ledger["x6"].get(sym)
        today = et_date(self._now())
        if pos is None or is_overnight(pos) or (job and job["date"] == today.isoformat() and not job.get("done")):
            return
        for oid, o in list(r.engine.working_orders.items()):
            if o.symbol == sym and not r._is_swing_arm(o):
                r.engine.cancel_order(oid, reason="OVERNIGHT_X6")
        bracket_id = r.bracket_manager.symbol_to_bracket.get(sym)
        if bracket_id:
            cancel_dir = r.bracket_manager.cancel_bracket_for_flattening(sym, reason="OVERNIGHT_X6")
            if cancel_dir and cancel_dir.orders_to_cancel:
                for oid in cancel_dir.orders_to_cancel:
                    if oid in r.engine.working_orders:
                        r.engine.cancel_order(oid, reason="OVERNIGHT_X6")
            r.bracket_manager.cancel_pending_entry_bracket(sym)
        side = r.OrderSide.SELL if pos.side.value == "LONG" else r.OrderSide.BUY
        order = r.engine.create_order(symbol=sym, side=side, order_type=r.OrderType.MARKET, qty=pos.shares,
                                      strategy_id=X6_ID, parent_order_id=bracket_id, arm=r.TradingArm.INTRADAY)
        order.estimated_price = pos.market_price
        today = et_date(self._now())
        self.ledger["x6"][sym] = {"order_id": order.id, "date": today.isoformat(), "attempt": 1, "qty": pos.shares,
                                  "side": side.value.lower(), "booked": {}, "done": False}
        log.warning("Overnight %s: closing the day trade (%d shares) early for the closing auction buy (X6)",
                    sym, pos.shares)
        self._x6_step(sym)

    def _x6_cid(self, sym: str, job: Dict[str, Any]) -> str:
        return f"adt-ovn-{sym}-{job['date'].replace('-', '')}-x6-{job['attempt']}"

    def _x6_step(self, sym: str) -> None:
        r = self.r
        job = self.ledger["x6"].get(sym)
        if not job or job.get("done"):
            return
        broker = self.controller.broker if self.controller is not None else None
        order = r.engine.orders.get(job["order_id"])
        if broker is None or order is None:
            return
        fut = self._x6_jobs.get(sym)
        if fut is None:
            last = self._x6_last.get(sym)
            now_mono = self._now().timestamp()
            if last is not None and now_mono - last < X6_RETRY_SEC:
                return
            self._x6_last[sym] = now_mono
            cid = self._x6_cid(sym, job)
            left = order.remaining_qty
            side = job["side"]

            def work():
                from backend.app.core.broker import is_terminal
                row = broker.get_order_by_client_id(cid)
                if row is None:
                    held = broker.position_qty(sym)
                    have = held if side == "sell" else -held
                    q = min(left, max(0, have))
                    if q < 1:
                        return {"none": True, "held": held}
                    row = broker.submit_and_settle(sym, side, q, cid)
                elif not is_terminal(row):
                    row = broker.cancel_and_settle(row)
                return {"row": row}

            fut = self.executor.submit(work)
            self._x6_jobs[sym] = fut
        if not fut.done():
            return
        del self._x6_jobs[sym]
        try:
            out = fut.result()
        except Exception as exc:
            log.warning("Overnight %s X6 close not confirmed yet: %s", sym, exc)
            return
        if out.get("none"):
            log.error("Overnight %s X6: Alpaca holds %s shares, ADT %d; left to the position compare",
                      sym, out.get("held"), order.remaining_qty)
            job["done"] = True
            return
        from backend.app.core.broker import filled_avg, filled_qty, is_terminal
        row = out["row"]
        oid = str(row.get("id"))
        total, avg = filled_qty(row), filled_avg(row)
        prev = job["booked"].get(oid) or {"qty": 0, "notional": 0.0}
        delta = min(total - int(prev["qty"]), order.remaining_qty)
        if delta > 0 and avg > 0:
            px = (total * avg - float(prev["notional"])) / (total - int(prev["qty"]))
            at = _parse(row.get("filled_at"), self._now())
            fill = r.engine._apply_fill_to_ledger(order, delta, round(px, 4), 0.0, 0.0, at)
            job["booked"][oid] = {"qty": total, "notional": total * avg}
            r._reconcile_fills([fill])
            r._checkpoint_runtime("OVERNIGHT_X6_FILL")
        if is_terminal(row):
            if order.remaining_qty <= 0 or r.account.positions.get(sym) is None:
                job["done"] = True
            else:
                job["attempt"] += 1        # the rest goes out under the next attempt id

    def _x6_tick(self) -> None:
        for sym in list(self.ledger["x6"]):
            try:
                self._x6_step(sym)
            except Exception:
                log.exception("Overnight X6 step for %s failed", sym)

    # ------------------------------------------------------------------ booking into ADT's ledger
    def _book(self, ev: Dict[str, Any]) -> bool:
        """The controller's booking callback (event loop). Raises to make the controller retry
        later; returns False to refuse (the controller raises needs look and still sells)."""
        r = self.r
        if ev.get("kind") == "split":
            return self._book_split(ev)
        if ev.get("kind") == "symbol_change":
            return self._book_symbol_change(ev)
        now = self._now()
        at = _parse(ev.get("at"), now)
        fill_day = et_date(at)
        if r.last_session_date is not None and fill_day > r.last_session_date:
            # R2-2: a fill made while the robot was down is booked only after the session boundary
            # of its day ran, so it lands in the right day and the right loss stop.
            raise RuntimeError(f"session boundary for {fill_day} has not run yet; booking waits")
        sym, role, qty, px = str(ev["symbol"]).upper(), ev["role"], int(ev["qty"]), float(ev["price"])
        sid = ev["strategy_id"]
        pos = r.account.positions.get(sym)
        if role == "buy":
            if pos is not None and (not is_overnight(pos) or pos.strategy_id != sid or pos.side.value != "LONG"):
                log.error("Overnight %s buy fill NOT merged into the %s position already in ADT's book",
                          sym, getattr(pos, "strategy_id", ""))
                return False
            side = r.OrderSide.BUY
        else:
            if sym != str(ev.get("hold_symbol") or sym).upper():
                log.error("Overnight sale leg in new symbol %s is not booked by ADT (needs look)", sym)
                return False
            if pos is None or not is_overnight(pos) or pos.side.value != "LONG" or qty > pos.shares:
                log.error("Overnight %s sale fill of %d does not match ADT's hold (%s); not booked", sym, qty,
                          None if pos is None else (pos.strategy_id, pos.shares))
                return False
            side = r.OrderSide.SELL
        # the night's key is its stock (IREN), also after a symbol change sold it as the new symbol
        key = f"{str(ev.get('stock') or sym).upper()}:{ev['buy_date']}"
        order = r.engine.create_order(symbol=sym, side=side, order_type=r.OrderType.MARKET, qty=qty, strategy_id=sid,
                                      client_order_id=str(ev.get("client_order_id") or ""),
                                      parent_order_id=f"overnight:{key}", arm=r.TradingArm.INTRADAY)
        order.execution_policy = OVERNIGHT_POLICY
        order.estimated_price = px
        r.engine._record_audit(order, order.status, "OVERNIGHT_BROKER_ORDER",
                               f"Overnight {role} {ev.get('alpaca_order_id')} at Alpaca")
        fill = r.engine._apply_fill_to_ledger(order, qty, round(px, 6), 0.0, 0.0, at)
        held = r.account.positions.get(sym)
        if held is not None and is_overnight(held):
            held.update_market_price(held.avg_entry_price)     # S15: the rest stays at its buy price
            r.account._recompute_account_state()
        leg = {"side": role, "qty": qty, "price": px, "timestamp": at.isoformat(), "order_id": order.id,
               "fill_id": fill.fill_id, "alpaca_order_id": ev.get("alpaca_order_id"),
               "client_order_id": ev.get("client_order_id"), "realized_pnl": fill.realized_pnl}
        self.ledger["fills"].setdefault(key, []).append(leg)
        if role == "sell":
            session = (r.last_session_date or fill_day).isoformat()
            off = self.ledger["offset"]
            if off.get("date") != session:
                off["date"], off["amount"] = session, 0.0
            off["amount"] = round(float(off["amount"]) + float(fill.realized_pnl), 2)
            self.apply_risk_offset()
        log.info("Overnight fill booked: %s %s %d @ %.4f (%s)", role, sym, qty, px, ev.get("alpaca_order_id"))
        return True

    def _book_split(self, ev: Dict[str, Any]) -> bool:
        r = self.r
        sym = str(ev["symbol"]).upper()
        pos = r.account.positions.get(sym)
        if pos is None or not is_overnight(pos) or pos.shares != int(ev["old_qty"]):
            log.error("Overnight %s split not applied to ADT's book (%s)", sym,
                      None if pos is None else (pos.strategy_id, pos.shares))
            return False
        ratio = float(ev["ratio"])
        pos.shares = int(ev["new_qty"])
        pos.avg_entry_price = round(float(ev["new_avg"]), 4)
        pos.update_market_price(pos.avg_entry_price)
        r.account._recompute_account_state()
        self.splits_shown[sym] = (f"{sym} split {ratio:g} for 1 overnight. The hold is now {pos.shares} shares "
                                  f"at ${pos.avg_entry_price:.2f}.")
        return True

    def _book_symbol_change(self, ev: Dict[str, Any]) -> bool:
        """A confirmed symbol change (section 4.6): the hold moves in ADT's book from the old symbol
        to the new one, shares x ratio at price / ratio, same strategy, before any sale is sent.
        Refused (False, nothing changed) unless the book holds exactly this overnight hold in the
        old symbol and nothing in the new one, so it can never merge into or flip a position."""
        r = self.r
        old, new = str(ev["symbol"]).upper(), str(ev["new_symbol"]).upper()
        pos = r.account.positions.get(old)
        if (pos is None or not is_overnight(pos) or pos.strategy_id != ev["strategy_id"]
                or pos.side.value != "LONG" or pos.shares != int(ev["old_qty"]) or new in r.account.positions):
            log.error("Overnight %s to %s symbol change NOT applied to ADT's book (old %s, new %s)", old, new,
                      None if pos is None else (pos.strategy_id, pos.shares), r.account.positions.get(new) is not None)
            return False
        old_qty, old_avg = pos.shares, pos.avg_entry_price
        del r.account.positions[old]
        pos.symbol = new
        pos.shares = int(ev["new_qty"])
        pos.avg_entry_price = round(float(ev["new_avg"]), 6)
        pos.update_market_price(pos.avg_entry_price)       # S15: held at its (converted) buy price
        r.account.positions[new] = pos
        r.account._recompute_account_state()
        log.warning("Overnight %s became %s. ADT's book now holds %d %s at $%.2f for %s instead of %d %s at $%.2f.",
                    old, new, pos.shares, new, pos.avg_entry_price, pos.strategy_id, old_qty, old, old_avg)
        return True

    def _record_trades(self) -> bool:
        """One closed trade row per sold night, on the day it sells (ADT's session at booking)."""
        r, ctl = self.r, self.controller
        if ctl is None:
            return False
        changed = False
        recorded = set(self.ledger["recorded"])
        for key, n in ctl.state["nights"].items():
            if n["state"] != SOLD or key in recorded:
                continue
            legs = self.ledger["fills"].get(key) or []
            buys = [l for l in legs if l["side"] == "buy"]
            sells = [l for l in legs if l["side"] == "sell"]
            self.ledger["recorded"].append(key)
            self.ledger["recorded"] = self.ledger["recorded"][-500:]
            self.ledger["fills"].pop(key, None)
            changed = True
            if not buys or not sells:
                continue
            qty = sum(l["qty"] for l in buys)
            out_qty = sum(l["qty"] for l in sells)
            pnl = round(sum(float(l["realized_pnl"]) for l in sells), 2)
            closed = max(_parse(l["timestamp"], self._now()) for l in sells)
            trade = {
                "trade_id": f"ovn_{key.replace(':', '_')}", "session_date": (r.last_session_date or et_date(closed)).isoformat(),
                "symbol": n["symbol"], "side": "LONG", "status": "CLOSED", "strategy_id": n["strategy_id"],
                "opened_at": min(l["timestamp"] for l in buys), "closed_at": closed.isoformat(),
                "quantity": qty, "avg_entry_price": round(sum(l["qty"] * l["price"] for l in buys) / qty, 4),
                "avg_exit_price": round(sum(l["qty"] * l["price"] for l in sells) / out_qty, 4),
                "realized_pnl": pnl, "fees": 0.0, "exit_reason": "OVERNIGHT_OPEN_SALE", "aggregate_only": False,
                "execution_mode": "alpaca_paper", "buy_date": n["buy_date"], "sale_date": n["sale_date"],
                "overnight": {"needs_look": list(n["needs_look"]), "splits": list(n["splits"]),
                              "symbol_change": n.get("symbol_change"),
                              "legs": [(leg["symbol"], leg["sold"], leg["mode"]) for leg in n["legs"]]},
                "fill_legs": [{"fill_id": l["fill_id"], "order_id": l["order_id"], "side": l["side"].upper(),
                               "qty": l["qty"], "price": l["price"], "fee": 0.0, "realized_pnl": l["realized_pnl"],
                               "timestamp": l["timestamp"], "alpaca_order_id": l["alpaca_order_id"]} for l in legs],
            }
            trade = r._sanitize_for_json(trade)
            r.pending_trade_records[trade["trade_id"]] = trade
            log.info("Overnight trade closed: %s %s pnl %.2f", n["symbol"], n["buy_date"], pnl)
        return changed

    # ------------------------------------------------------------------ loop entry points
    def _now(self) -> datetime:
        ctl = self.controller
        try:
            return ctl.clock() if ctl is not None else datetime.now(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def tick(self, now: datetime) -> None:
        """_runtime_clock_step, next to tri_controller.tick. Starts and collects worker jobs only."""
        ctl = self.controller
        if ctl is None:
            return
        self._x6_tick()
        ctl.tick(now)
        self._x6_tick()
        self._after_booking(now)
        try:
            self._fidelity_tick(now)
        except Exception:
            log.exception("Overnight fidelity log step failed (it gates nothing)")

    # ------------------------------------------------------------------ fidelity log (4.9)
    FIDELITY_RETRY_SEC = 300.0
    FIDELITY_GIVE_UP = timedelta(hours=3)

    def _fidelity_tick(self, now: datetime) -> None:
        """Section 4.9, gates nothing. SIP minute bars (relay REST, feed pinned to sip) fetched at
        16:15 on the buy day (research style entry, the close of the last bar 15:55 to 15:59) and
        at 09:45 on the sale day (research style exit, the open of the 09:30 bar), and the sale
        day's full session at 16:15 for the research look ahead check ok[d+1]. Kept for every
        night with a sale date, bought or skipped, so a skipped night shows the return the rule
        would have made. Official auction prints are not fetched (not in the relay's minute bars)."""
        r, ctl = self.r, self.controller
        if not (r.settings.RELAY_TOKEN and r.settings.RELAY_HTTP_URL):
            return
        for n in ctl._nights()[-9:]:
            if not n.get("sale_date"):
                continue
            bd, sd = date.fromisoformat(n["buy_date"]), date.fromisoformat(n["sale_date"])
            fid = n["fidelity"]
            for part, day, due in (("entry", bd, et(bd, osch.FIDELITY_CLOSE_AT)),
                                   ("exit", sd, et(sd, osch.FIDELITY_OPEN_AT)),
                                   ("lookahead", sd, et(sd, osch.FIDELITY_CLOSE_AT))):
                if fid.get(f"{part}_status") in ("ok", "unavailable") or now < due:
                    continue
                self._fidelity_part(n, part, day, due, now)

    def _fidelity_part(self, n: Dict[str, Any], part: str, day: date, due: datetime, now: datetime) -> None:
        r, sym = self.r, n["symbol"]
        key = f"{part}:{sym}:{n['buy_date']}"
        fut = self.fidelity_jobs.get(key)
        if fut is None:
            last = self._x6_last.get(f"fid:{key}")
            if last is not None and now.timestamp() - last < self.FIDELITY_RETRY_SEC:
                return
            self._x6_last[f"fid:{key}"] = now.timestamp()

            def work():
                return asyncio.run(r._fetch_session_minutes([sym], day, deadline_sec=20.0))

            self.fidelity_jobs[key] = self.executor.submit(work)
            return
        if not fut.done():
            return
        del self.fidelity_jobs[key]
        try:
            bars = [b for b in fut.result() if b.symbol.upper() == sym]
        except Exception as exc:
            if now - due > self.FIDELITY_GIVE_UP:
                self.controller.record_fidelity(sym, date.fromisoformat(n["buy_date"]),
                                                {f"{part}_status": "unavailable", f"{part}_error": str(exc)[:200]})
            return
        fields: Dict[str, Any] = {f"{part}_status": "ok", "feed": "sip"}
        mins = {b.timestamp.astimezone(ET).strftime("%H:%M"): b for b in bars}
        if part == "entry":
            last = next((mins[m] for m in ("15:59", "15:58", "15:57", "15:56", "15:55") if m in mins), None)
            fields.update(research_entry=last.close if last else None,
                          last_bar_minute=max(mins) if mins else None, buy_day_bars=len(bars),
                          real_entry=n.get("buy_avg"))
        elif part == "exit":
            b930 = mins.get("09:30")
            fields.update(research_exit=b930.open if b930 else None, sale_day_has_0930=b930 is not None)
            legs = [leg for leg in n["legs"] if leg["symbol"] == sym and leg["sold"]]
            if legs:
                fields["real_exit"] = legs[0]["notional"] / legs[0]["sold"]
        else:
            fields.update(sale_day_bars=len(bars), lookahead_ok=len(bars) >= osch.MIN_SESSION_BARS)
        if n.get("splits"):
            fields["split_ratio"] = n["splits"][-1]["ratio"]
        self.controller.record_fidelity(sym, date.fromisoformat(n["buy_date"]), fields)

    def _after_booking(self, now: datetime) -> None:
        r = self.r
        self.apply_risk_offset()
        if self._record_trades():
            r._checkpoint_runtime("OVERNIGHT_TRADE")

    def reconcile(self, now: Optional[datetime] = None) -> None:
        """Startup, after the session boundary check for now and before the first compare: ask
        Alpaca about every client id and book what filled while the robot was down."""
        ctl = self.controller
        if ctl is None:
            return
        now = now or self._now()
        ctl.reconcile(now)
        for sym, job in list(self.ledger["x6"].items()):
            if not job.get("done"):
                self._x6_last.pop(sym, None)
        self._after_booking(now)
        self.r._checkpoint_runtime("OVERNIGHT_RECONCILE")

    async def before_compare(self, positions: Dict[str, int]) -> Dict[str, int]:
        """S13, broker loop: the controller books its fills, positions are re-read, then compare."""
        ctl = self.controller
        if ctl is None or ctl.broker is None:
            return positions
        reads = ctl.pending_reads()
        booked = False
        if reads:
            rows = await asyncio.to_thread(ctl.fetch_reads, reads)
            booked = ctl.apply_reads(rows, self._now())
            self._after_booking(self._now())
            if booked:
                self.r._checkpoint_runtime("OVERNIGHT_SYNC")
                positions = await asyncio.to_thread(ctl.broker.get_positions)
        await self._refresh_splits(positions)
        return positions

    async def _refresh_splits(self, positions: Dict[str, int]) -> None:
        ctl, r = self.controller, self.r
        local = r._local_signed_positions(r.account)
        for n in ctl.holds():
            sym = n["symbol"]
            if local.get(sym, 0) == positions.get(sym, 0):
                self._split_cache.pop(sym, None)
                continue
            cached = self._split_cache.get(sym)
            mono = self._now().timestamp()
            if cached is not None and mono - cached[0] < SPLIT_CHECK_SEC:
                continue
            bd = date.fromisoformat(n["buy_date"])
            sd = date.fromisoformat(n["sale_date"]) if n.get("sale_date") else bd + timedelta(days=5)
            try:
                actions = await asyncio.to_thread(ctl.broker.get_corporate_actions, sym, bd.isoformat(), sd.isoformat())
            except Exception as exc:
                log.warning("Overnight %s corporate actions read failed: %s", sym, exc)
                actions = []
            self._split_cache[sym] = (mono, actions)

    def explain(self, diffs: Dict[str, Dict[str, int]]) -> Tuple[Dict[str, Dict[str, int]], Dict[str, str]]:
        """S13: a share count difference on a held stock explained by a split (Alpaca = hold x ratio)
        is shown as a split, not a mismatch. Everything else stays a mismatch."""
        ctl = self.controller
        if ctl is None:
            return diffs, {}
        held = {n["symbol"] for n in ctl.holds()}
        rest, splits = {}, {}
        for sym, d in diffs.items():
            cached = self._split_cache.get(sym)
            text = None
            if sym in held and cached is not None and d["bot"] > 0:
                for a in cached[1]:
                    ratio = a.get("ratio")
                    if (a.get("kind") in ("split", "reverse_split") and ratio and a.get("old_symbol") == sym
                            and a.get("new_symbol") == sym and abs(d["bot"] * ratio - d["alpaca"]) < 1e-6):
                        text = (f"{sym} split {ratio:g} for 1 (ex date {a.get('ex_date')}). Alpaca shows {d['alpaca']} "
                                f"shares for the hold of {d['bot']}; the 9:00 AM check adjusts the hold and its sale.")
                        break
            if text:
                splits[sym] = text
            else:
                rest[sym] = d
        return rest, splits

    # ------------------------------------------------------------------ control (D4)
    def set_no_buy_tonight(self, on: bool, now: Optional[datetime] = None) -> Tuple[bool, str]:
        ctl = self.controller
        if ctl is None:
            return False, "The overnight holds are not running on this robot, so there is nothing to stop."
        now = now or self._now()
        return ctl.set_no_buy_tonight(now) if on else ctl.clear_no_buy_tonight(now)

    # ------------------------------------------------------------------ payloads
    @staticmethod
    def research_summary() -> Optional[Dict[str, Any]]:
        try:
            return json.loads(RESEARCH_SUMMARY.read_text(encoding="utf-8"))
        except Exception:
            return None

    def health(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """/health.overnight (health stays 200 whatever this says)."""
        ctl = self.controller
        now = now or self._now()
        base = {"mode": self.mode if ctl is not None else "off", "configured_mode": self.configured_mode,
                "running": ctl is not None, "init_error": self.init_error,
                "realized_today": round(self.realized_today(), 2),
                "risk_drawdown_dollars": self.r.risk_engine.current_drawdown_dollars}
        if ctl is None:
            return {**base, "holds": [], "queued_sales": [], "needs_look": [], "intents": [], "unsold_after_0931": []}
        h = ctl.health(now)
        needs = [{"symbol": n["symbol"], "buy_date": n["buy_date"], "reasons": list(n["needs_look"])}
                 for n in ctl._nights() if n["needs_look"] and not n["released"]]
        last_booking = next((row for row in reversed(ctl.state["log"]) if row.get("event") == "BOOKED"), None)
        return {**base, **h, "intents": self._intents(ctl), "queued_sales": self._queued(ctl), "needs_look": needs,
                "last_booking": last_booking, "splits": dict(self.splits_shown)}

    @staticmethod
    def _intents(ctl: OvernightController) -> List[Dict[str, Any]]:
        out = []
        for n in ctl._nights():
            if n["buy"] and not n["released"] and n["state"] not in HOLD_STATES and n["state"] != SOLD:
                att = n["buy"][-1]
                out.append({"symbol": n["symbol"], "buy_date": n["buy_date"], "cid": att["cid"], "tif": att["tif"],
                            "qty": att["qty"], "status": att["status"], "state": n["state"]})
        return out

    @staticmethod
    def _queued(ctl: OvernightController) -> List[Dict[str, Any]]:
        out = []
        for n in ctl.holds():
            for leg in n["legs"]:
                live = [a for a in leg["attempts"] if not a["final"]]
                if live:
                    a = live[-1]
                    out.append({"symbol": leg["symbol"], "buy_date": n["buy_date"], "sale_date": n["sale_date"],
                                "cid": a["cid"], "tif": a["tif"], "qty": a["qty"], "status": a["status"]})
        return out

    def payload(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """GET /api/overnight and the websocket frame's "overnight" key (live state and settings)."""
        ctl = self.controller
        now = now or self._now()
        cfg = self.settings_view()
        out: Dict[str, Any] = {"state": self.health(now), "settings": {**cfg, "mode": self.mode if ctl else "off"},
                               "research": self.research_summary(), "rows": [], "holds": [], "skips": [],
                               "fidelity": [], "intents": [], "queued_sales": []}
        if ctl is None:
            return out
        acct = ctl.state.get("account") or {}
        today = et_date(now)
        for sym in osch.SYMBOLS:
            nights = [n for n in ctl._nights() if n["symbol"] == sym]
            latest = nights[-1] if nights else None
            out["rows"].append({
                "symbol": sym, "name": NAMES[sym], "strategy_id": osch.STRATEGY_IDS[sym], "enabled": sym in ctl.enabled,
                "state": latest["state"] if latest else None, "buy_date": latest["buy_date"] if latest else None,
                "sale_date": latest["sale_date"] if latest else None, "reason": latest["reason"] if latest else None,
                "block": latest["block"] if latest else None, "needs_look": list(latest["needs_look"]) if latest else [],
                "qty": latest["qty"] if latest else None, "held_qty": latest["held_qty"] if latest else 0,
                "buy_avg": latest["buy_avg"] if latest else None, "realized": latest["realized"] if latest else None,
                "reserved": ctl.reserves(sym), "tonight": latest is not None and latest["buy_date"] == today.isoformat(),
                "sale_text": self.sale_text(sym) if latest and latest["state"] in HOLD_STATES else None,
                "size_note": (f"{int(round(cfg['pct'] * 100))}% of the account a night"
                              + (f" (about ${cfg['pct'] * float(acct['equity']):,.0f})" if acct.get("equity") else "")
                              + ", no stop"),
            })
        out["holds"] = [{"symbol": ctl._cur(n), "strategy_id": n["strategy_id"], "shares": ctl._left(n),
                         "buy_avg": n["buy_avg"], "buy_date": n["buy_date"], "sale_date": n["sale_date"],
                         "nights": osch.holding_nights(date.fromisoformat(n["buy_date"]), date.fromisoformat(n["sale_date"]))
                         if n.get("sale_date") else None, "state": n["state"], "needs_look": list(n["needs_look"])}
                        for n in ctl.holds()]
        out["skips"] = [{"symbol": n["symbol"], "buy_date": n["buy_date"], "reason": n["reason"],
                         "wanted_qty": n["wanted_qty"]} for n in ctl._nights() if n["state"] == SKIPPED][-30:]
        out["fidelity"] = [{"symbol": n["symbol"], "buy_date": n["buy_date"], **n["fidelity"]}
                           for n in ctl._nights() if n["fidelity"]][-30:]
        out["intents"], out["queued_sales"] = self._intents(ctl), self._queued(ctl)
        out["no_buy_tonight"] = ctl.state["control"].get("no_buy_date") == today.isoformat()
        out["no_buy_until"] = et(today, osch.BUY_GIVE_UP).isoformat()
        out["unsold_after_0931"] = ctl.unsold_after_0931(now)
        return out
