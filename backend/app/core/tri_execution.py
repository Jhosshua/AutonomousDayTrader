"""Fixed TSLA/CDE tranche lifecycle on the existing paper account and ledger.

Only this controller routes its orders. Network work runs in a bounded pool;
all state/ledger mutations run on the application's event loop. Each POST has
a durable client identity before it can be queued. Restart recovers by that id.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import logging

import httpx
import math
from typing import Any

from backend.app.core.broker import BrokerError, BrokerReject, filled_avg, filled_qty, is_terminal
from backend.app.core.trading_windows import ET
from backend.app.core.persistence import PersistenceError
from backend.app.models.events import OrderSide, OrderState, OrderType
from backend.app.strategies.base import StrategyStatus
from backend.app.strategies.tri_engine import (
    ACTIVE_PHASES, TRI_IDS, VERSION, SOURCE_SHA256, AsymmetricDualStrategy,
)
from backend.app.strategies.tsla_or15_retest import (
    CLOCK_SKEW_SECONDS, ENTRY_GRACE_SECONDS, QUOTE_MAX_AGE_SECONDS, session_bounds,
)

log = logging.getLogger("tri_execution")

# Market fills can land past the estimate. Up to 1.5x the 0.75% budget is kept
# and logged; beyond that (or a fill through the stop) the trade is closed.
FILL_RISK_TOLERANCE = 1.5


def validate_tri_state(s, account, engine) -> None:
    phases = ACTIVE_PHASES | {"WAITING_SESSION", "BUILDING_RANGE", "WAITING_BREAKOUT", "WAITING_RETEST",
                            "WAITING_ENTRY", "SKIPPED", "NO_SIGNAL", "CLOSED"}
    if s.phase not in phases or s.strategy_id not in TRI_IDS:
        raise PersistenceError("Unknown tri-engine lifecycle")
    if s.signal_consumed and (s.signal is None or s.entry_due != s.signal.timestamp+timedelta(minutes=2)
                              or s.signal.symbol != s.symbol or s.signal.strategy_id != s.strategy_id):
        raise PersistenceError("Tri-engine signal identity/timing mismatch")
    if s.phase in ACTIVE_PHASES:
        entry = engine.orders.get(s.entry_order_id)
        if not entry or entry.symbol != s.symbol or entry.strategy_id != s.strategy_id or not s.signal_consumed:
            raise PersistenceError("Tri-engine active lifecycle lacks its entry")
        if entry.side != s.signal.side or entry.execution_policy != s.strategy_id:
            raise PersistenceError("Tri-engine entry side/owner mismatch")
    bounds = session_bounds(s.session_day) if s.session_day else None
    remaining = 0
    for t in s.tranches:
        if t["qty"] <= 0 or not 0 <= t["closed_qty"] <= t["qty"]:
            raise PersistenceError("Tri-engine tranche quantity mismatch")
        remaining += t["qty"]-t["closed_qty"]
        direction = 1 if s.side == "LONG" else -1
        risk = direction*(t["entry_price"]-s.stop)
        expected_hold = 240 if s.symbol == "TSLA" and t["target_r"] == 2 else 180
        if (t["stop"] != s.stop or t["target_r"] not in ((1.5, 2.) if s.symbol == "TSLA" else (2.,))
                or abs(t["target"]-(t["entry_price"]+direction*t["target_r"]*risk)) > 1e-8):
            raise PersistenceError("Tri-engine fixed levels changed")
        if not bounds or t["exit_due"] != min(t["entry_at"]+timedelta(minutes=expected_hold), bounds[1]-timedelta(minutes=5)):
            raise PersistenceError("Tri-engine tranche deadline changed")
        if t["protection_confirmed"]:
            for role in ("target", "stop"):
                oid = t[f"{role}_order_id"]
                if oid not in engine.orders or oid not in s.native or not s.native[oid]["id"]:
                    raise PersistenceError("Tri-engine native protection identity missing")
    pos = account.positions.get(s.symbol)
    owned = bool(pos and pos.strategy_id == s.strategy_id)
    if not s.tranches and s.phase in ACTIVE_PHASES:
        entry = engine.orders.get(s.entry_order_id)
        remaining = entry.filled_qty if entry else 0
    if remaining != (pos.shares if owned else 0) or (owned and s.phase not in ACTIVE_PHASES):
        raise PersistenceError("Tri-engine tranche/account ownership mismatch")
    for oid, n in s.native.items():
        order = engine.orders.get(oid)
        if (order is None or order.execution_policy != s.strategy_id or order.symbol != s.symbol
                or order.filled_qty != n["booked_qty"]):
            raise PersistenceError("Tri-engine native cumulative fill mismatch")


def broker_price(value: float, rounding: str = "nearest") -> float:
    quantum = Decimal("0.01") if value >= 1 else Decimal("0.0001")
    mode = {"up": ROUND_CEILING, "down": ROUND_FLOOR, "nearest": ROUND_HALF_UP}[rounding]
    return float(Decimal(str(value)).quantize(quantum, rounding=mode))


class TriExecutionController:
    POLL_SECONDS = 5
    ENTRY_POLL_SECONDS = 1

    def __init__(self, runtime: Any, strategies: list[AsymmetricDualStrategy]) -> None:
        self.r = runtime
        self.strategies = strategies
        self.by_symbol = {s.symbol: s for s in strategies}
        self.by_id = {s.strategy_id: s for s in strategies}
        self._pool = None
        self._jobs = {}
        self._last_start = {}
        self.inline_io = False  # deterministic HTTP transport tests only

    def shutdown(self) -> None:
        if self._pool:
            self._pool.shutdown(wait=True, cancel_futures=False)
        self._pool = None
        self._jobs.clear()
        self._last_start.clear()

    def _io(self, key: str, work, now: datetime | None = None, every: float = 0.):
        """Return (finished, result); exceptions are handled by the owning state.

        A new request for `key` starts at most once per `every` seconds of `now`,
        so a stream of quotes cannot burst past Alpaca's ~200 requests a minute.
        """
        future = self._jobs.get(key)
        if future is None:
            last = self._last_start.get(key)
            if now is not None and last is not None and (now-last).total_seconds() < every:
                return False, None
            if now is not None:
                self._last_start[key] = now
        if self.inline_io:
            return True, work()
        if future is None:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="TriPaperIO")
            self._jobs[key] = self._pool.submit(work)
            return False, None
        if not future.done():
            return False, None
        del self._jobs[key]
        return True, future.result()

    def owns(self, symbol: str) -> bool:
        pos = self.r.account.positions.get(symbol.upper())
        return bool(pos and pos.strategy_id in TRI_IDS)

    def reserves(self, symbol: str) -> bool:
        s = self.by_symbol.get(symbol.upper())
        return bool(s and (self.owns(symbol) or s.phase in ACTIVE_PHASES | {"WAITING_ENTRY"}))

    def checkpoint(self, reason: str = "TRI_INTENT") -> bool:
        if self.r.inflight_event_keys:
            return False
        return self.r._checkpoint_runtime(reason)

    def open_risk(self, exclude_order: str | None = None) -> float:
        """Committed stop risk of this plan's own TSLA and CDE trades.

        The source's 1.50% cap is the two-instrument portfolio budget (0.75%
        each). Other arms keep their own risk limits and are not counted.
        """
        r = self.r
        total = 0.0
        for s in self.strategies:
            total += sum(max(0, t["qty"] - t["closed_qty"]) * t["risk_per_share"] for t in s.tranches)
        for order in r.engine.working_orders.values():
            if (order.id == exclude_order or order.strategy_id not in TRI_IDS
                    or r.engine._reduces_position(order)):
                continue
            price = order.limit_price or order.estimated_price
            if not price or not order.stop_price:
                return math.inf
            total += order.remaining_qty * abs(price - order.stop_price)
        return total

    def validate_entry(self, order) -> tuple[bool, str]:
        if order.strategy_id not in TRI_IDS:
            return True, "Not a tri-engine order"
        if self.r.engine._reduces_position(order):
            return True, "Reducing exposure"
        cap = self.r.account.daily_starting_equity * .015
        risk = order.qty * abs((order.limit_price or order.estimated_price or 0) - (order.stop_price or 0))
        if self.open_risk(order.id) + risk > cap + 1e-7:
            return False, "COMBINED_OPEN_RISK_LIMIT: 1.50% session-start equity"
        return True, "Within combined stop-risk budget"

    def on_bar(self, bar, now: datetime, durable_replay: bool = False) -> None:
        for s in self.strategies:
            if durable_replay:
                continue
            s.execution_mode = "offline_raw_open" if self.r.simulation_mode else "alpaca_paper"
            signals = s.on_completed_bar(bar, now)
            if s.session_equity is None and s.session_day:
                s.session_equity = self.r.account.daily_starting_equity
            for signal in signals:
                self.r._record_decision(signal, "SIGNAL", signal.reason)
            if self.r.simulation_mode and bar.symbol.upper() == s.symbol:
                if s.phase == "WAITING_ENTRY" and bar.timestamp >= s.entry_due:
                    if bar.timestamp != s.entry_due:
                        s.skip("MISSING_FILL_BAR", now)
                    else:
                        self._prepare_entry(s, bar.open, bar.timestamp)
                self._offline_exits(s, bar)

    def on_quote(self, quote) -> None:
        s = self.by_symbol.get(quote.symbol.upper())
        if not s or quote.timestamp.tzinfo is None:
            return
        if not all(math.isfinite(v) and v > 0 for v in (quote.bid_price, quote.ask_price)) or quote.bid_price > quote.ask_price:
            return
        if s.last_quote and quote.timestamp < s.last_quote["at"]:
            return
        s.last_quote = {"at": quote.timestamp, "bid": quote.bid_price, "ask": quote.ask_price}

    def request_exit(self, symbol: str, reason: str, now: datetime) -> None:
        s = self.by_symbol.get(symbol.upper())
        if not s:
            return
        if s.phase == "WAITING_ENTRY":
            s.skip(reason, now)
        elif s.phase in ACTIVE_PHASES:
            s.exit_reason = s.exit_reason or reason
            s.phase = "EXITING"
            s.note("EXIT_REQUEST", now, reason=reason)
            for t in s.tranches:
                t["exit_reason"] = t.get("exit_reason") or reason

    def request_all_exits(self, reason: str, now: datetime) -> None:
        for s in self.strategies:
            self.request_exit(s.symbol, reason, now)

    @staticmethod
    def _signature(s) -> tuple:
        return (s.phase, s.reason, s.exit_reason, s.incomplete, s.entry_terminal, len(s.audit),
                s.quantity, repr(s.tranches),
                repr(sorted((k, n.get("id"), n.get("status"), n.get("booked_qty")) for k, n in s.native.items())))

    def tick(self, now: datetime) -> None:
        before = [self._signature(s) for s in self.strategies]
        for s in self.strategies:
            s.execution_mode = "offline_raw_open" if self.r.simulation_mode else "alpaca_paper"
            s.on_time_tick(now, live=not self.r.simulation_mode)
            if s.session_equity is None and s.session_day:
                s.session_equity = self.r.account.daily_starting_equity
        if self.r.simulation_mode or self.r.inflight_event_keys:
            return
        for s, old in zip(self.strategies, before):
            try:
                self._tick_one(s, now)
            except Exception as exc:
                s.last_error = f"{type(exc).__name__}: {exc}"
                # Network blips and 429/5xx are retried; the native OCO keeps
                # protecting meanwhile. Only integrity failures end the trade.
                transient = isinstance(exc, httpx.HTTPError) or (isinstance(exc, BrokerError) and not exc.hard)
                log.warning("%s lifecycle (%s): %s", s.symbol, "retry" if transient else "exit", s.last_error)
                if s.phase in ACTIVE_PHASES and not transient:
                    s.incomplete = True
            if self._signature(s) != old:
                self.checkpoint("TRI_CLOCK")

    def _tick_one(self, s, now) -> None:
        if s.incomplete and s.phase in ACTIVE_PHASES:
            self.request_exit(s.symbol, s.exit_reason or "DATA_OR_EXECUTION_INCOMPLETE", now)
        if s.phase == "WAITING_ENTRY" and now >= s.entry_due:
            q = s.last_quote
            # A fresh quote proves the feed is alive. No T+1 bar is required: a
            # quiet CDE minute has no bar, and the research filled at T+2 anyway.
            if q and -CLOCK_SKEW_SECONDS <= (now-q["at"]).total_seconds() <= QUOTE_MAX_AGE_SECONDS:
                price = q["ask"] if s.side == "LONG" else q["bid"]
                self._prepare_entry(s, price, now)
        if s.phase not in ACTIVE_PHASES:
            return
        # A feed outage does not close a trade: stop/target rest at Alpaca and
        # time exits run on the clock.
        s.last_poll = now
        if not s.entry_terminal:
            self._entry_poll(s, now)
        for t in s.tranches:
            if now >= t["exit_due"]:
                bounds = session_bounds(s.session_day)
                t["exit_reason"] = t.get("exit_reason") or (
                    "FORCED_FLAT" if bounds and t["exit_due"] == bounds[1]-timedelta(minutes=5) else "TIME_LIMIT")
            if s.exit_reason:
                t["exit_reason"] = t.get("exit_reason") or s.exit_reason
            self._manage_tranche(s, t, now)
        self._maybe_complete(s, now)

    def _prepare_entry(self, s, price: float, now: datetime) -> None:
        r = self.r
        bounds = session_bounds(s.session_day) if s.session_day else None
        reason = None
        if (not bounds or now.astimezone(ET).date() != s.session_day or now >= s.entry_cutoff
                or now >= datetime.combine(s.session_day, time(12), ET) or now > bounds[1]-timedelta(minutes=30)):
            reason = "ENTRY_SESSION_OR_CUTOFF"
        elif s.status != StrategyStatus.ACTIVE:
            reason = "OPERATOR_PAUSED"
        elif s.symbol in r.account.positions or any(o.symbol == s.symbol for o in r.engine.working_orders.values()):
            reason = "SYMBOL_ALREADY_COMMITTED"
        elif not r.simulation_mode and (r.engine.broker is None or not r.or15_sip_verified or r.relay_statuses.get("stock") != "connected"):
            reason = "PAPER_BROKER_OR_SIP_UNAVAILABLE"
        if reason:
            s.skip(reason, now)
            r._record_decision(s.signal, "TRI_SKIP", reason)
            return
        limit = None  # market at T+2 for both sides, as in the audited T+2 raw-open fill
        estimate = price
        direction = 1 if s.side == "LONG" else -1
        risk = direction * (estimate-s.stop)
        if not math.isfinite(estimate) or estimate <= 0 or risk <= 0:
            s.skip("NON_POSITIVE_RISK", now)
            return
        s.session_equity = s.session_equity or r.account.daily_starting_equity
        budget = min(s.session_equity*.0075, max(0., s.session_equity*.015-self.open_risk()))
        qty = int(max(0, min(budget/risk, r.account.buying_power/estimate)))
        # Tight-stop shorts (100% margin under $5) can need more buying power
        # than the account has: shrink to the largest affordable size instead
        # of letting the order be rejected and the day's setup lost.
        side = s.signal.side.value
        if qty and not r.account.can_afford(s.symbol, side, qty, estimate, concentration_cap=False)[0]:
            lo, hi = 0, qty
            while lo < hi:
                mid = (lo+hi+1)//2
                lo, hi = (mid, hi) if r.account.can_afford(s.symbol, side, mid, estimate, concentration_cap=False)[0] else (lo, mid-1)
            s.note("SIZE_LIMITED_BY_BUYING_POWER", now, wanted=qty, allowed=lo)
            qty = lo
        if s.symbol == "TSLA":
            qty -= qty % 2
        if qty < (2 if s.symbol == "TSLA" else 1):
            s.skip("INSUFFICIENT_RISK_OR_BUYING_POWER", now)
            return
        order = r.engine.create_order(s.symbol, s.signal.side, OrderType.LIMIT if limit else OrderType.MARKET,
            qty, limit_price=limit, stop_price=s.stop, estimated_price=estimate, strategy_id=s.strategy_id)
        order.execution_policy = s.strategy_id
        order.created_at = now
        if r.engine.submit_order(order.id).status != OrderState.ACCEPTED:
            s.skip(f"ACCOUNT_RISK: {order.reject_reason}", now)
            r._record_decision(s.signal, "ENGINE_REJECT", str(order.reject_reason))
            return
        s.entry_order_id, s.quantity, s.risk_reserved = order.id, qty, qty*risk
        cid = f"adt-tri-{s.symbol}-{s.session_day.isoformat()}-entry"
        order.broker_client_id = cid if not r.simulation_mode else None
        s.native[order.id] = {"cid": cid, "id": None, "role": "entry", "status": "intent",
                              "booked_qty": 0, "booked_notional": 0., "terminal": False}
        s.phase = "ENTERING"
        s.note("ORDER_INTENT", now, quantity=qty, risk=qty*risk, limit=limit, client_id=cid)
        if r.simulation_mode:
            fill = r.engine._apply_fill_to_ledger(order, qty, price, 0., 0., now)
            s.entry_terminal = True
            s.native[order.id].update(terminal=True, status="filled", booked_qty=qty, booked_notional=qty*price)
            self.on_fill(order, fill)
            self._open_tranches(s, order)
        elif not self.checkpoint():
            if order.id in r.engine.working_orders:
                r.engine.cancel_order(order.id, "INTENT_NOT_DURABLE")
            s.phase = "WAITING_ENTRY"
            s.skip("INTENT_NOT_DURABLE", now)
            return
        r._record_decision(s.signal, "SUBMITTED", f"{s.signal.side.value} {qty} {s.symbol}; T+2")

    def _entry_poll(self, s, now) -> None:
        order = self.r.engine.orders[s.entry_order_id]
        n = s.native[order.id]
        broker = self.r.engine.broker
        cancel = bool(s.exit_reason or now >= s.entry_cutoff or (order.filled_qty and order.remaining_qty)
                      or (order.order_type == OrderType.MARKET and now > s.entry_due+timedelta(seconds=ENTRY_GRACE_SECONDS)))
        def work():
            row = broker.get_order(n["id"]) if n["id"] else broker.find_by_client_id(n["cid"])
            if row is None:
                wall = self.r.or15_now()
                if n["status"] != "intent" or cancel or not 0 <= (wall-s.entry_due).total_seconds() <= ENTRY_GRACE_SECONDS:
                    # The POST may have gone through with its reply lost. Only
                    # call it unsent when Alpaca holds none of these shares.
                    if broker.position_qty(s.symbol) != (n.get("broker_qty_before") or 0):
                        self.r.broker_state["mismatch"] = True
                        self.r.broker_state["mismatch_detail"] = f"{s.symbol} entry id not found but Alpaca shares changed"
                        if wall < s.entry_cutoff:
                            raise BrokerError("ENTRY_ID_MISSING_BROKER_HOLDS_SHARES")
                        # Past the cutoff: stop guessing; the mismatch flag pauses entries for a person to look.
                    return {"status": "expired", "filled_qty": "0", "filled_avg_price": None}
                asset = broker.get_asset(s.symbol)
                if not asset.get("tradable") or asset.get("status") != "active":
                    raise BrokerReject("ASSET_NOT_TRADABLE", hard=True)
                borrow = asset.get("borrow_status")
                etb = borrow == "easy_to_borrow" if borrow is not None else asset.get("easy_to_borrow") is True
                if s.side == "SHORT" and not (asset.get("shortable") and etb):
                    raise BrokerReject("SHORT_BORROW_UNAVAILABLE", hard=True)
                wall = self.r.or15_now()
                q = s.last_quote
                if (not 0 <= (wall-s.entry_due).total_seconds() <= ENTRY_GRACE_SECONDS or not q
                        or not -CLOCK_SKEW_SECONDS <= (wall-q["at"]).total_seconds() <= QUOTE_MAX_AGE_SECONDS):
                    raise BrokerReject("MISSED_ENTRY_WINDOW_OR_STALE_QUOTE", hard=True)
                refusal = self.r._broker_gate(order, False)
                if refusal:
                    raise BrokerReject(refusal[0], hard=True)
                if (s.exit_reason or s.phase != "ENTERING" or s.status != StrategyStatus.ACTIVE
                        or self.r.account.status.value != "ACTIVE"):
                    raise BrokerReject("ENTRY_INVALIDATED_BEFORE_SEND", hard=True)
                n["broker_qty_before"] = broker.position_qty(s.symbol)
                row = broker.submit(s.symbol, order.side.value, order.qty, n["cid"], order.limit_price)
            if cancel and not is_terminal(row):
                broker.request_cancel(row["id"])
                row = broker.get_order(row["id"])
            return row
        try:
            ready, row = self._io(s.strategy_id+":entry", work, now, self.ENTRY_POLL_SECONDS)
        except BrokerReject as exc:
            if not exc.hard:
                raise
            row = {"status": "rejected", "filled_qty": "0", "filled_avg_price": None}
            s.reason = str(exc)
            ready = True
        if not ready:
            return
        self._book(s, order, row)
        s.entry_terminal = n["terminal"]
        if s.entry_terminal and not order.filled_qty:
            s.phase = "WAITING_ENTRY"
            s.skip(s.reason or "ENTRY_NOT_FILLED", now)
        elif s.entry_terminal:
            self._open_tranches(s, order)
        elif order.filled_qty and (s.exit_reason or now > s.entry_due+timedelta(seconds=ENTRY_GRACE_SECONDS+10)):
            # The rest's cancel is stuck: protect what we own now.
            self._open_tranches(s, order)
        if s.tranches and order.filled_qty > sum(t["qty"] for t in s.tranches):
            self._open_tranches(s, order, late=True)

    def _book(self, s, order, row) -> None:
        n = s.native[order.id]
        total = filled_qty(row)
        notional = total * filled_avg(row)
        delta = total-n["booked_qty"]
        n.update(id=row.get("id", n["id"]), status=row.get("status"), terminal=is_terminal(row))
        order.broker_order_id = n["id"]
        if delta < 0 or delta > order.remaining_qty:
            raise ValueError("Broker cumulative quantity contradicts local order")
        if delta:
            price = (notional-n["booked_notional"])/delta
            if not math.isfinite(price) or price <= 0:
                raise ValueError("Broker fill price is invalid")
            raw_time = row.get("filled_at") or row.get("updated_at")
            at = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00")) if raw_time else self.r.or15_now()
            if not raw_time:
                s.incomplete = True
                s.exit_reason = "MISSING_BROKER_FILL_TIME"
            order.broker_fill_timestamp = at
            n.update(booked_qty=total, booked_notional=notional)
            fill = self.r.engine._apply_fill_to_ledger(order, delta, price, 0., 0., at)
            self.on_fill(order, fill)
        if n["terminal"] and order.remaining_qty and order.id in self.r.engine.working_orders:
            self.r.engine.cancel_order(order.id, "BROKER_TERMINAL")

    def on_fill(self, order, fill) -> None:
        s = self.by_id[order.strategy_id]
        s.note("FILL", fill.timestamp, order_id=order.id, price=fill.price, qty=fill.qty,
               side=order.side.value, mode=s.execution_mode)
        if order.id == s.entry_order_id:
            pass  # tranches are cut in _open_tranches once the entry order is finished
        else:
            n = s.native[order.id]
            t = next(t for t in s.tranches if t["id"] == n["tranche_id"])
            t["closed_qty"] += fill.qty
            t["exit_notional"] += fill.qty*fill.price
            t["realized_pnl"] += fill.realized_pnl
            t["exit_at"] = fill.timestamp
            if n["role"] in ("stop", "target") and t["closed_qty"] >= t["qty"]:
                t["exit_reason"] = t.get("exit_reason") or n["role"].upper()
            if t["closed_qty"] > t["qty"]:
                s.incomplete = True
                s.exit_reason = "BROKER_OCO_OVERFILL"
                self.r.broker_state["mismatch"] = True
                self.r.broker_state["mismatch_detail"] = "Native OCO filled beyond its tranche"
        s.risk_reserved = sum(max(0, t["qty"]-t["closed_qty"])*t["risk_per_share"] for t in s.tranches)

    def _open_tranches(self, s, order, late: bool = False) -> None:
        """Split the finished entry into its fixed tranches (once).

        A partial fill is still the trade, just smaller: the unfilled rest was
        canceled at the grace deadline. TSLA splits floor/ceil halves.
        """
        if late:
            # Shares that filled after the tranches were cut: close them.
            extra = order.filled_qty-sum(t["qty"] for t in s.tranches)
            base = s.tranches[-1]
            tid = len(s.tranches)+1
            s.tranches.append({**base, "id": tid, "qty": extra, "closed_qty": 0, "exit_reason": "LATE_ENTRY_FILL",
                "protection_cid": f"adt-tri-{s.symbol}-{s.session_day.isoformat()}-t{tid}",
                "protection_confirmed": False, "protection_terminal": False, "stop_order_id": None,
                "target_order_id": None, "close_order_ids": [], "realized_pnl": 0., "exit_notional": 0.})
            s.note("LATE_ENTRY_FILL", self.r.or15_now(), extra=extra)
            s.risk_reserved = sum(max(0, t["qty"]-t["closed_qty"])*t["risk_per_share"] for t in s.tranches)
            return
        if s.tranches or not order.filled_qty:
            return
        qty, price = order.filled_qty, order.avg_fill_price
        at = max(f.timestamp for f in order.fills)
        direction = 1 if s.side == "LONG" else -1
        risk = direction*(price-s.stop)
        if order.filled_qty != order.qty:
            s.note("PARTIAL_ENTRY_KEPT", at, filled=qty, ordered=order.qty)
        budget = (s.session_equity or 0)*.0075
        actual = max(0., risk)*qty
        if risk <= 0 or (budget and actual > budget*FILL_RISK_TOLERANCE):
            s.exit_reason = s.exit_reason or "INVALID_FILL_RISK"
            s.incomplete = True
        elif budget and actual > budget:
            s.note("FILL_RISK_OVER_BUDGET", at, actual=actual, budget=budget)
        sizes = [(qty//2, 1.5, 180), (qty-qty//2, 2., 240)] if s.symbol == "TSLA" else [(qty, 2., 180)]
        bounds = session_bounds(s.session_day)
        for part, target_r, minutes in sizes:
            if not part:
                continue
            tid = len(s.tranches)+1
            s.tranches.append({"id": tid, "qty": part, "closed_qty": 0,
                "entry_price": price, "entry_at": at,
                "stop": s.stop, "risk_per_share": max(0., risk),
                "target_r": target_r, "target": price+direction*target_r*risk,
                "exit_due": min(at+timedelta(minutes=minutes), bounds[1]-timedelta(minutes=5)),
                "exit_reason": s.exit_reason, "protection_cid": f"adt-tri-{s.symbol}-{s.session_day.isoformat()}-t{tid}",
                "protection_confirmed": False, "protection_terminal": False,
                "stop_order_id": None, "target_order_id": None, "close_order_ids": [],
                "realized_pnl": 0., "exit_notional": 0.})
        s.risk_reserved = sum(t["qty"]*t["risk_per_share"] for t in s.tranches)
        s.phase = "EXITING" if s.exit_reason else "HOLDING"

    def _new_exit_order(self, s, t, role, qty, *, native=None):
        side = OrderSide.SELL if s.side == "LONG" else OrderSide.BUY
        kind = OrderType.STOP if role == "stop" else OrderType.LIMIT if role == "target" else OrderType.MARKET
        order = self.r.engine.create_order(s.symbol, side, kind, qty,
            stop_price=t["stop"] if role == "stop" else None,
            limit_price=t["target"] if role == "target" else None,
            estimated_price=t["entry_price"], strategy_id=s.strategy_id,
            parent_order_id=f"tri_{s.entry_order_id}")
        order.execution_policy = s.strategy_id
        submitted = self.r.engine.submit_order(order.id)
        if submitted.status != OrderState.ACCEPTED:
            raise ValueError(f"Fixed reducing order rejected: {submitted.reject_reason}")
        cid = native.get("client_order_id") if native else f"{t['protection_cid']}-x{len(t['close_order_ids'])+1}"
        s.native[order.id] = {"cid": cid, "id": native.get("id") if native else None,
            "role": role, "tranche_id": t["id"], "status": "intent",
            "booked_qty": 0, "booked_notional": 0., "terminal": False}
        order.broker_client_id = cid
        order.broker_order_id = native.get("id") if native else None
        return order

    def _manage_tranche(self, s, t, now) -> None:
        broker = self.r.engine.broker
        remaining = t["qty"]-t["closed_qty"]
        exiting = bool(t.get("exit_reason") or remaining <= 0)
        # A partial/unusable entry takes the emergency exit path. It cannot be
        # called a correctly allocated strategy holding.
        if not t["protection_confirmed"] and not t["protection_terminal"]:
            self.checkpoint()  # best effort; the cid is rebuilt from the durable entry id
            def protect():
                found = broker.find_by_client_id(t["protection_cid"])
                if found:
                    return broker.get_order(found["id"])
                if exiting:
                    return None  # never create an OCO after deciding to close
                if self.r._broker_gate(self.r.engine.orders[s.entry_order_id], True):
                    return "DEFER"  # market closed: a DAY OCO would queue for the next open
                direction = "up" if s.side == "LONG" else "down"
                return broker.submit_oco(s.symbol, remaining, t["protection_cid"],
                    broker_price(t["stop"], direction),
                    broker_price(t["target"], "down" if s.side == "LONG" else "up"),
                    side="sell" if s.side == "LONG" else "buy")
            try:
                ready, group = self._io(f"{s.strategy_id}:protect:{t['id']}", protect, now, 1.)
            except BrokerReject as exc:
                if not exc.hard:
                    raise
                t["protection_terminal"] = True
                t["exit_reason"] = "PROTECTION_REJECTED"
                s.incomplete = True
                s.exit_reason = "PROTECTION_REJECTED"
                return
            if not ready or group == "DEFER":
                return
            if group is None:
                t["protection_terminal"] = True
            else:
                legs = [group]+list(group.get("legs") or [])
                stop = next((x for x in legs if x.get("type") in ("stop", "stop_limit")), None)
                target = next((x for x in legs if x.get("type") == "limit"), None)
                if not stop or not target:
                    raise ValueError("Protection response is missing native legs")
                bound = []
                for role, row in (("stop", stop), ("target", target)):
                    if not t.get(f"{role}_order_id"):
                        t[f"{role}_order_id"] = self._new_exit_order(s, t, role, t["qty"], native=row).id
                    bound.append((self.r.engine.orders[t[f"{role}_order_id"]], row))
                for order, row in bound:
                    self._book(s, order, row)
                t["protection_confirmed"] = True
                t["broker_stop"] = float(stop.get("stop_price") or broker_price(t["stop"], "up" if s.side == "LONG" else "down"))
                t["broker_target"] = float(target.get("limit_price") or broker_price(t["target"], "down" if s.side == "LONG" else "up"))
                self.checkpoint("TRI_PROTECTION_BOUND")
                return
        if t["protection_confirmed"] and not t["protection_terminal"]:
            target_n = s.native[t["target_order_id"]]
            def poll():
                if exiting:
                    broker.request_cancel(target_n["id"])
                return broker.get_order(target_n["id"])
            ready, group = self._io(f"{s.strategy_id}:group:{t['id']}", poll, now, 1. if exiting else self.POLL_SECONDS)
            if not ready:
                return
            by_id = {x["id"]: x for x in [group]+list(group.get("legs") or [])}
            for role in ("target", "stop"):
                order = self.r.engine.orders[t[f"{role}_order_id"]]
                n = s.native[order.id]
                if n["id"] not in by_id:
                    raise ValueError("Nested protection poll omitted a known leg")
                self._book(s, order, by_id[n["id"]])
            t["protection_terminal"] = all(s.native[t[f"{role}_order_id"]]["terminal"] for role in ("target", "stop"))
            remaining = t["qty"]-t["closed_qty"]
            if t["protection_terminal"] and remaining and not t.get("exit_reason"):
                t["exit_reason"] = "PROTECTION_LOST"
        if not t["protection_terminal"] or remaining <= 0:
            return
        if t.get("exit_reason"):
            self._close_tranche(s, t, now)

    def _close_tranche(self, s, t, now) -> None:
        remaining = t["qty"]-t["closed_qty"]
        if remaining <= 0:
            return
        order = self.r.engine.orders[t["close_order_ids"][-1]] if t["close_order_ids"] else None
        if order is None or s.native[order.id]["terminal"]:
            order = self._new_exit_order(s, t, "close", remaining)
            t["close_order_ids"].append(order.id)
        n = s.native[order.id]
        self.checkpoint()  # best effort; close cids are derived from the tranche identity
        broker = self.r.engine.broker
        def work():
            found = broker.get_order(n["id"]) if n["id"] else broker.find_by_client_id(n["cid"])
            if found:
                return found
            if self.r._broker_gate(order, True):
                return None  # market closed: never queue a DAY market order for the next open
            held = broker.position_qty(s.symbol)
            if (held > 0) != (s.side == "LONG") or abs(held) < order.qty:
                raise BrokerReject("BROKER_POSITION_MISMATCH_BEFORE_CLOSE", hard=True)
            return broker.submit(s.symbol, order.side.value, order.qty, n["cid"])
        ready, row = self._io(f"{s.strategy_id}:close:{order.id}", work, now, 1.)
        if ready and row:
            self._book(s, order, row)

    def _offline_exits(self, s, bar) -> None:
        if s.phase not in ACTIVE_PHASES or s.last_managed_bar == bar.timestamp:
            return
        s.last_managed_bar = bar.timestamp
        for t in s.tranches:
            if t["closed_qty"] >= t["qty"] or bar.timestamp < t["entry_at"]:
                continue
            long = s.side == "LONG"
            stop_hit = bar.low <= t["stop"] if long else bar.high >= t["stop"]
            target_hit = bar.high >= t["target"] if long else bar.low <= t["target"]
            reason, price = None, None
            if t.get("exit_reason") or bar.timestamp >= t["exit_due"]:
                bounds = session_bounds(s.session_day)
                reason = t.get("exit_reason") or ("FORCED_FLAT" if t["exit_due"] == bounds[1]-timedelta(minutes=5) else "TIME_LIMIT")
                price = bar.open
            elif stop_hit:
                reason = "STOP"
                price = min(bar.open, t["stop"]) if long else max(bar.open, t["stop"])
            elif target_hit:
                reason, price = "TARGET", t["target"]
            if reason:
                t["exit_reason"] = reason
                order = self._new_exit_order(s, t, "close", t["qty"]-t["closed_qty"])
                t["close_order_ids"].append(order.id)
                fill = self.r.engine._apply_fill_to_ledger(order, order.qty, price, 0., 0., bar.timestamp)
                s.native[order.id].update(terminal=True, status="filled", booked_qty=order.qty, booked_notional=order.qty*price)
                self.on_fill(order, fill)
                t["protection_terminal"] = True
        self._maybe_complete(s, bar.timestamp)

    def _maybe_complete(self, s, now) -> None:
        if not s.entry_terminal or not s.tranches or s.trade_recorded:
            return
        if any(t["closed_qty"] != t["qty"] or not t["protection_terminal"] for t in s.tranches):
            return
        entry = self.r.engine.orders[s.entry_order_id]
        exits = [o for o in self.r.engine.orders.values() if o.parent_order_id == f"tri_{entry.id}"]
        fills = entry.fills+[f for o in exits for f in o.fills]
        exit_fills = [f for o in exits for f in o.fills]
        qty = entry.filled_qty
        exit_notional = sum(f.qty*f.price for f in exit_fills)
        pnl = sum(f.realized_pnl for f in exit_fills)
        costs = (entry.avg_fill_price*qty+exit_notional)*.0003
        trade = {"trade_id": f"tri_{entry.id}", "session_date": s.session_day.isoformat(),
            "symbol": s.symbol, "side": s.side, "status": "CLOSED", "strategy_id": s.strategy_id,
            "opened_at": min(f.timestamp for f in entry.fills).isoformat(),
            "closed_at": max(f.timestamp for f in exit_fills).isoformat(), "quantity": qty,
            "avg_entry_price": entry.avg_fill_price, "avg_exit_price": exit_notional/qty,
            "realized_pnl": round(pnl, 2), "fees": 0., "broker_fees": None,
            "exit_reason": s.exit_reason or "/".join(dict.fromkeys(t["exit_reason"] for t in s.tranches)),
            "aggregate_only": False, "execution_mode": s.execution_mode, "strategy_version": VERSION,
            "source_sha256": SOURCE_SHA256, "incomplete": s.incomplete,
            "normal_cost_pnl": pnl-costs, "stress_cost_pnl": pnl-2*costs,
            "tranches": [dict(t) for t in s.tranches],
            "fill_legs": [{"fill_id": f.fill_id, "order_id": f.order_id, "side": f.side.value,
                "qty": f.qty, "price": f.price, "fee": f.fee, "realized_pnl": f.realized_pnl,
                "timestamp": f.timestamp.isoformat()} for f in fills]}
        trade = self.r._sanitize_for_json(trade)
        self.r.pending_trade_records[trade["trade_id"]] = trade
        s.record_trade(round(pnl, 2))
        s.trade_recorded = True
        s.phase, s.risk_reserved = "CLOSED", 0.
        s.note("CLOSED", now, trade_id=trade["trade_id"], realized_pnl=pnl)
        if self.r.settings.RESEARCH_ENABLED:
            self.r.research_safe(self.r.research_recorder.record, "trades", trade["trade_id"], trade,
                                 recorder=self.r.research_recorder)

    def position_details(self, symbol: str) -> dict[str, Any]:
        s = self.by_symbol[symbol]
        remaining = [t for t in s.tranches if t["closed_qty"] < t["qty"]]
        return {"strategy_id": s.strategy_id, "fixed_protection": True, "stop_loss": s.stop,
                "take_profit_1": s.tranches[0]["target"] if s.tranches else None,
                "take_profit_2": s.tranches[-1]["target"] if s.tranches else None,
                "exit_due": min(t["exit_due"] for t in remaining).isoformat() if remaining else None,
                "tranches": self.r._sanitize_for_json(s.tranches)}
