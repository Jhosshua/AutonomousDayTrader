"""Dedicated fixed-rule policy using the shared order, bracket and ledger engine.

Offline replay alone uses raw bar opens and stop-first OHLC ambiguity. Production
submits a market entry at T+2 and rests an actual OCO at Alpaca; broker fills are
never replaced with hypothetical prices. All mutable policy state lives on the
strategy and is included in the existing checkpoint.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Optional

from backend.app.core.broker import BrokerReject, is_terminal
from backend.app.core.engine import BrokerFillFailed, BracketRole, OrderSide, OrderType
from backend.app.models.events import BarEvent, QuoteEvent
from backend.app.strategies.base import StrategyStatus
from backend.app.strategies.tsla_or15_retest import (
    STRATEGY_ID, ENTRY_GRACE_SECONDS, QUOTE_MAX_AGE_SECONDS, session_bounds,
)


class OR15ExecutionController:
    def __init__(self, runtime: Any) -> None:
        self.r = runtime

    @property
    def s(self):
        return self.r.tsla_or15_strategy

    def owns(self, symbol: str) -> bool:
        pos = self.r.account.positions.get(symbol.upper())
        return bool(pos and pos.strategy_id == STRATEGY_ID)

    def reserves(self, symbol: str) -> bool:
        return symbol.upper() == "TSLA" and (
            self.owns(symbol) or self.s.phase in ("WAITING_ENTRY", "ENTERING", "HOLDING", "EXITING"))

    def checkpoint(self) -> bool:
        # Fixed broker sends are driven AFTER the outer market input commits.
        # Never accept _checkpoint_runtime's deliberately deferred success.
        if self.r.inflight_event_keys:
            return False
        return self.r._checkpoint_runtime("OR15_ORDER_INTENT")

    def before_submit(self, order: Any) -> bool:
        saved = self.checkpoint()
        if order.side != OrderSide.BUY:
            # The first emergency-close identity was committed before BUY. If
            # storage fails after the fill, that same id can still reduce risk;
            # recovery searches it even if the close response was never saved.
            known_retry = order.broker_client_id in (order.fixed_intent_client_id, f"adt-{order.id}-{order.broker_attempts}")
            return saved or bool(order.fixed_intent_client_id and order.id == self.s.exit_order_id and known_retry)
        if not saved:
            return False
        # Re-read wall time AFTER durable I/O, immediately before the HTTP POST.
        now = self.r.or15_now()
        q, due = self.s.last_quote, self.s.entry_due
        return bool(due and 0 <= (now - due).total_seconds() <= ENTRY_GRACE_SECONDS
                    and q and 0 <= (now - q["at"]).total_seconds() <= QUOTE_MAX_AGE_SECONDS)

    def on_bar(self, bar: BarEvent, now: datetime) -> None:
        s = self.s
        s.execution_mode = "offline_raw_open" if self.r.simulation_mode else "alpaca_paper"
        s.on_completed_bar(bar, now)
        if self.r.simulation_mode and bar.symbol.upper() == "TSLA":
            if s.phase == "SKIPPED" or s.incomplete:
                return
            # Consume only the open at its timestamp; signal OHLC is already final.
            if s.phase == "WAITING_ENTRY" and s.entry_due and bar.timestamp >= s.entry_due:
                if bar.timestamp != s.entry_due:
                    s.skip("MISSING_FILL_BAR", now)
                else:
                    self._enter(bar.open, bar.timestamp)
            self._offline_exit(bar)

    def on_quote(self, quote: QuoteEvent) -> None:
        if quote.symbol.upper() != "TSLA":
            return
        if (quote.timestamp.tzinfo is None or not all(math.isfinite(v) and v > 0 for v in
                (quote.bid_price, quote.ask_price)) or quote.bid_price > quote.ask_price):
            return
        if self.s.last_quote and quote.timestamp < self.s.last_quote["at"]:
            return
        self.s.last_quote = {"at": quote.timestamp, "bid": quote.bid_price, "ask": quote.ask_price}

    def tick(self, now: datetime) -> None:
        s = self.s
        before = (s.phase, s.reason, len(s.audit), s.protection_confirmed)
        s.execution_mode = "offline_raw_open" if self.r.simulation_mode else "alpaca_paper"
        s.on_time_tick(now)
        if self.r.simulation_mode:
            return  # the terminal bar open is required for an offline exit
        if self.r.inflight_event_keys:
            return
        if s.phase == "WAITING_ENTRY" and s.entry_due and now >= s.entry_due:
            q = s.last_quote
            preceding_bar = bool(s.bars["TSLA"] and s.bars["TSLA"][-1].timestamp >= s.entry_due - timedelta(minutes=1))
            if preceding_bar and q and 0 <= (now - q["at"]).total_seconds() <= QUOTE_MAX_AGE_SECONDS:
                if (now - s.entry_due).total_seconds() <= ENTRY_GRACE_SECONDS:
                    self._enter(q["ask"], now)
        if s.phase == "ENTERING":
            # Resolve an uncertain entry by its saved id, never place a replacement.
            self.r._settle_broker_orders()
            order = self.r.engine.orders.get(s.entry_order_id)
            if order and not order.broker_client_id and not order.broker_order_id and not order.filled_qty:
                if order.id in self.r.engine.working_orders:
                    self.r.engine.cancel_order(order.id, "OR15_ENTRY_NOT_FILLED")
                self.r.bracket_manager.cancel_pending_entry_bracket("TSLA")
                s.phase = "WAITING_ENTRY"
                s.skip("BROKER_ENTRY_NOT_FILLED", now)
        if self.owns("TSLA"):
            if s.exit_due and now >= s.exit_due:
                bounds = session_bounds(s.session_day)
                reason = "FORCED_FLAT" if bounds and s.exit_due == bounds[1] - timedelta(minutes=5) else "TIME_LIMIT"
                self.request_exit(reason, now)
            if self.r.relay_statuses.get("stock") not in ("connected",):
                s.incomplete = True
                self.request_exit("FEED_DISCONNECTED", now)
            if s.phase == "HOLDING":
                self._ensure_protection(now)
                self._poll_protection()
                if self.owns("TSLA") and s.protection_confirmed:
                    b = self._bracket()
                    if b and any(oid not in self.r.engine.working_orders for oid in
                                 (b.stop_order_id, b.target_1_order_id)):
                        self.request_exit("PROTECTION_LOST", now)
        if s.phase == "EXITING":
            self._run_exit(now)
        if before != (s.phase, s.reason, len(s.audit), s.protection_confirmed):
            self.r._checkpoint_runtime("OR15_CLOCK")

    def _enter(self, price: float, now: datetime) -> None:
        r, s = self.r, self.s
        bounds = session_bounds(s.session_day) if s.session_day else None
        reason: Optional[str] = None
        if not bounds or now.date() != s.entry_due.date() or now > bounds[1] - timedelta(minutes=30):
            reason = "ENTRY_SESSION_OR_CUTOFF"
        elif not math.isfinite(price) or price <= (s.or_low or 0):
            reason = "NON_POSITIVE_RISK"
        elif s.status != StrategyStatus.ACTIVE:
            reason = "OPERATOR_PAUSED"
        elif "TSLA" in r.account.positions or any(o.symbol == "TSLA" for o in r.engine.working_orders.values()):
            reason = "SYMBOL_ALREADY_COMMITTED"
        elif not r.simulation_mode and r.engine.broker is None:
            reason = "PAPER_BROKER_REQUIRED"
        elif not r.simulation_mode and (r.relay_statuses.get("stock") != "connected" or not r.or15_sip_verified):
            reason = "SIP_FEED_UNVERIFIED"
        if reason:
            s.skip(reason, now)
            r._record_decision(s.signal, "OR15_SKIP", reason)
            return
        order = r.engine.create_order("TSLA", OrderSide.BUY, OrderType.MARKET, 1,
                                      stop_price=s.or_low, estimated_price=price, strategy_id=STRATEGY_ID)
        order.execution_policy = STRATEGY_ID
        order.created_at = now
        submitted = r.engine.submit_order(order.id)
        if submitted.status.value != "ACCEPTED":
            s.skip(f"ACCOUNT_RISK: {submitted.reject_reason}", now)
            r._record_decision(s.signal, "ENGINE_REJECT", str(submitted.reject_reason))
            return
        bracket = r.bracket_manager.create_bracket(f"brk_{order.id}", "TSLA", "LONG", 1,
            price, s.or_low, strategy_id=STRATEGY_ID, use_trailing_target_2=False,
            target_1_r=2.0, target_2_r=2.0, fixed_single_target=True, timestamp=now)
        r.entry_order_to_bracket[order.id] = bracket.bracket_id
        s.entry_order_id = order.id
        s.protection_client_id = f"adt-or15-{s.session_day.isoformat()}-oco" if r.engine.broker else None
        emergency = r.engine.create_order("TSLA", OrderSide.SELL, OrderType.MARKET, 1,
            strategy_id=STRATEGY_ID, parent_order_id=bracket.bracket_id)
        emergency.execution_policy = STRATEGY_ID
        emergency.fixed_intent_client_id = f"adt-or15-{s.session_day.isoformat()}-exit"
        emergency.broker_client_id = emergency.fixed_intent_client_id if r.engine.broker else None
        emergency.created_at = now
        s.exit_order_id = emergency.id
        s.phase = "ENTERING"
        s.note("ORDER", now, order_id=order.id, trigger_price=price, quantity=1,
               quote=s.last_quote and {**s.last_quote, "at": s.last_quote["at"].isoformat()})
        if not self.checkpoint():
            r.engine.cancel_order(order.id, "OR15_INTENT_NOT_DURABLE")
            s.phase = "WAITING_ENTRY"
            s.skip("ORDER_INTENT_NOT_DURABLE", now)
            return
        r._record_decision(s.signal, "SUBMITTED", f"BUY 1 TSLA; T+2; order {order.id}")
        try:
            fill = r.engine._execute_fill(order, 1, price, 0.0, now)
        except BrokerFillFailed:
            # Even a local reject may have an unresolved server order: preserve
            # the bracket link and consumed-signal latch until lookup is conclusive.
            return
        r._reconcile_fills([fill])
        if r.engine.broker is not None:
            self._ensure_protection(now)

    def entry_filled(self, order: Any, fill: Any) -> bool:
        """Set timing from actual execution before the generic bracket activates."""
        s = self.s
        s.entry_price, s.filled_at = fill.price, fill.timestamp
        bounds = session_bounds(s.session_day)
        s.exit_due = min(fill.timestamp + timedelta(minutes=120), bounds[1] - timedelta(minutes=5)) if bounds else fill.timestamp
        s.phase = "EXITING" if s.exit_reason else "HOLDING"
        s.note("ENTRY_FILL", fill.timestamp, price=fill.price, quantity=fill.qty,
               mode=s.execution_mode, slippage=fill.slippage, broker_fee_known=False if self.r.engine.broker else None)
        invalid = fill.price <= s.or_low or fill.qty != 1 or (self.r.engine.broker and order.broker_fill_timestamp is None)
        if invalid:
            s.protection_client_id = None  # OCO has not been attempted for an invalid fill
            self.request_exit("INVALID_ENTRY_FILL", fill.timestamp)
            return False
        s.target_price = fill.price + 2 * (fill.price - s.or_low)
        return True

    def _bracket(self):
        return self.r.bracket_manager.brackets.get(f"brk_{self.s.entry_order_id}")

    def _ensure_protection(self, now: datetime) -> None:
        r, s = self.r, self.s
        if r.engine.broker is None or s.protection_confirmed or not self.owns("TSLA"):
            return
        b = self._bracket()
        if not b or not b.stop_order_id or b.stop_order_id not in r.engine.orders:
            self.request_exit("INVALID_ENTRY_FILL", now)
            return
        broker = r.engine.broker
        try:
            if s.protection_client_id:
                found = broker.find_by_client_id(s.protection_client_id)
                if found is None:
                    # The crash may have happened after intent persistence and
                    # BEFORE POST. Retry the identical idempotent request; the
                    # broker's unique client id prevents a second OCO group.
                    # The OCO identity was also committed with the entry. Posting
                    # protection remains permitted if storage fails AFTER a fill.
                    self.checkpoint()
                    native = broker.submit_oco("TSLA", 1, s.protection_client_id,
                        math.ceil((s.or_low - 1e-9) * 100) / 100, round(s.target_price, 2))
                else:
                    native = broker.get_order(found["id"])
            else:
                s.protection_client_id = f"adt-or15-{s.session_day.isoformat()}-oco"
                if not self.checkpoint():
                    s.protection_client_id = None
                    self.request_exit("PROTECTION_INTENT_NOT_DURABLE", now)
                    return
                stop = math.ceil((s.or_low - 1e-9) * 100) / 100
                target = round(s.target_price, 2)
                native = broker.submit_oco("TSLA", 1, s.protection_client_id, stop, target)
                s.note("PROTECTION_POST", now, broker_stop=stop, broker_target=target,
                       theoretical_stop=s.or_low, theoretical_target=s.target_price)
            legs = [native] + list(native.get("legs") or [])
            stop_leg = next((x for x in legs if x.get("type") == "stop"), None)
            target_leg = next((x for x in legs if x.get("type") == "limit"), None)
            if not stop_leg or not target_leg:
                s.incomplete = True
                s.reason = "PROTECTION_LEGS_UNKNOWN"
                return
            for local_id, leg in ((b.stop_order_id, stop_leg), (b.target_1_order_id, target_leg)):
                local = r.engine.orders[local_id]
                local.broker_order_id = leg["id"]
                local.broker_resting = True
                local.broker_booked_qty = 0
                local.broker_booked_notional = 0.0
            s.protection_confirmed = True
            s.note("PROTECTION_CONFIRMED", now,
                   requested_stop=math.ceil((s.or_low - 1e-9) * 100) / 100,
                   requested_target=round(s.target_price, 2),
                   broker_stop=stop_leg.get("stop_price"),
                   broker_target=target_leg.get("limit_price"),
                   theoretical_stop=s.or_low, theoretical_target=s.target_price,
                   stop_order_id=stop_leg["id"], target_order_id=target_leg["id"])
            self.checkpoint()
        except BrokerReject as exc:
            if exc.hard:
                s.protection_client_id = None  # authoritative rejection, no resting pair
                self.request_exit("PROTECTION_REJECTED", now)
            else:
                s.incomplete = True
                s.reason = "PROTECTION_OUTCOME_UNKNOWN"
        except Exception:
            s.incomplete = True
            s.reason = "PROTECTION_OUTCOME_UNKNOWN"

    def _poll_protection(self, cancel: bool = False) -> bool:
        r, s = self.r, self.s
        b = self._bracket()
        if not b or r.engine.broker is None:
            return True
        terminal = True
        for oid in (b.stop_order_id, b.target_1_order_id):
            order = r.engine.orders.get(oid)
            if not order or not order.broker_resting or not order.broker_order_id:
                continue
            try:
                native = r.engine.broker.get_order(order.broker_order_id)
                if cancel:
                    native = r.engine.broker.cancel_and_settle(native)
                delta, price = r.engine._book_broker_order(order, native)
                if delta:
                    fill = r.engine._apply_fill_to_ledger(order, delta, price, 0.0, 0.0,
                        order.broker_fill_timestamp or datetime.now(timezone.utc))
                    r._reconcile_fills([fill])
                if is_terminal(native):
                    if order.id in r.engine.working_orders and not delta:
                        r.engine.cancel_order(order.id, "OR15_NATIVE_TERMINAL")
                else:
                    terminal = False
            except Exception:
                terminal = False
        return terminal

    def request_exit(self, reason: str, now: datetime) -> None:
        if self.s.phase in ("CLOSED", "SKIPPED", "NO_SIGNAL"):
            return
        if not self.s.exit_reason:
            self.s.exit_reason = reason
            self.s.note("EXIT_REQUEST", now, reason=reason)
        self.s.phase = "EXITING"

    def _run_exit(self, now: datetime, model_price: Optional[float] = None) -> None:
        r, s = self.r, self.s
        existing = r.engine.orders.get(s.exit_order_id)
        # Persist the exit request before removing resting protection. A saved
        # emergency identity is the only fallback if storage is unavailable.
        if not self.checkpoint() and not (existing and existing.fixed_intent_client_id):
            return
        if r.engine.broker is not None:
            entry = r.engine.orders.get(s.entry_order_id)
            if entry and (entry.broker_order_id or entry.broker_client_id):
                r._settle_broker_orders()
                if entry.broker_order_id or entry.broker_client_id:
                    return  # even an abort must resolve the potentially filled entry
            if not self.owns("TSLA") and entry and entry.filled_qty == 0:
                r.bracket_manager.cancel_pending_entry_bracket("TSLA")
                if entry.id in r.engine.working_orders:
                    r.engine.cancel_order(entry.id, "OR15_ABORT_NO_FILL")
                s.phase = "CLOSED"
                s.note("ABORTED_WITHOUT_FILL", now, reason=s.exit_reason)
                return
            if s.protection_client_id and not s.protection_confirmed:
                self._ensure_protection(now)
                if not s.protection_confirmed:
                    return
            if not self._poll_protection(cancel=True):
                return  # no competing exit while a protective leg may still fill
        if not self.owns("TSLA"):
            s.phase = "CLOSED"
            return
        pos = r.account.positions["TSLA"]
        b = self._bracket()
        if b and b.symbol in r.bracket_manager.symbol_to_bracket:
            d = r.bracket_manager.cancel_bracket_for_flattening("TSLA", s.exit_reason)
            r._apply_bracket_directive(b.bracket_id, d)
        if existing is None:
            existing = r.engine.create_order("TSLA", OrderSide.SELL, OrderType.MARKET, pos.shares,
                strategy_id=STRATEGY_ID, parent_order_id=b.bracket_id if b else None)
            existing.execution_policy = STRATEGY_ID
            existing.created_at = now
            s.exit_order_id = existing.id
        if existing.status.value == "CREATED":
            r.engine.submit_order(existing.id)
        price = model_price if model_price is not None else pos.market_price
        try:
            fill = r.engine._execute_fill(existing, pos.shares, price, 0.0, now, cancel_on_broker_error=False)
        except BrokerFillFailed:
            return
        r._reconcile_fills([fill])

    def _offline_exit(self, bar: BarEvent) -> None:
        s, r = self.s, self.r
        if not self.owns("TSLA") or not s.filled_at or bar.timestamp < s.filled_at:
            return
        if s.last_managed_bar and bar.timestamp <= s.last_managed_bar:
            return
        if s.last_managed_bar and bar.timestamp != s.last_managed_bar + timedelta(minutes=1):
            s.incomplete = True
            s.note("INCOMPLETE", bar.timestamp, reason="MISSING_EXIT_BAR")
            return  # never invent a price through a missing bar
        s.last_managed_bar = bar.timestamp
        if bar.timestamp >= s.exit_due:
            bounds = session_bounds(s.session_day)
            self.request_exit("FORCED_FLAT" if s.exit_due == bounds[1] - timedelta(minutes=5) else "TIME_LIMIT", bar.timestamp)
            self._run_exit(bar.timestamp, bar.open)
            return
        b = self._bracket()
        stop_hit, target_hit = bar.low <= s.or_low, bar.high >= s.target_price
        if stop_hit or target_hit:
            s.exit_reason = "STOP" if stop_hit else "TARGET"
            s.note("MODEL_EXIT", bar.timestamp, reason=s.exit_reason, both_touched=stop_hit and target_hit)
            order = r.engine.orders[b.stop_order_id if stop_hit else b.target_1_order_id]
            price = min(s.or_low, bar.open) if stop_hit else s.target_price
            fill = r.engine._execute_fill(order, 1, price, 0.0, bar.timestamp)
            r._reconcile_fills([fill])

    def completed(self, trade: dict) -> None:
        s = self.s
        entry, exit_, qty = trade["avg_entry_price"], trade["avg_exit_price"], trade["quantity"]
        gross = (exit_ - entry) * qty
        normal, stress = (entry + exit_) * qty * 3 / 10000, (entry + exit_) * qty * 6 / 10000
        trade.update({"execution_mode": s.execution_mode, "strategy_version": self.s.to_dict()["or15"]["version"],
            "source_sha256": self.s.to_dict()["or15"]["source_sha256"], "gross_pnl": gross,
            "assumed_cost_3bps": normal, "assumed_cost_6bps": stress,
            "normal_cost_pnl": gross - normal, "stress_cost_pnl": gross - stress,
            "broker_fees": None,
            "simulator_fees": trade["fees"] if self.r.simulation_mode else None,
            "risk_per_share": entry - s.or_low, "incomplete": s.incomplete,
            "exit_reason": s.exit_reason or trade["exit_reason"]})
        trade["implementation_sha256"] = getattr(self.r, "or15_implementation_hash", None)
        s.phase = "CLOSED"
        s.protection_confirmed = False
        s.note("CLOSED", datetime.fromisoformat(trade["closed_at"]), trade_id=trade["trade_id"],
               exit_reason=trade["exit_reason"], gross_pnl=gross, normal_cost_pnl=gross-normal,
               stress_cost_pnl=gross-stress, mode=s.execution_mode)
