"""backend/app/core/engine.py
Deterministic Order Execution Engine, 8-State Lifecycle FSM, and Microstructure Fill Simulator.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
import math
import time
from typing import Callable, Dict, List, Optional, Tuple
import uuid

from backend.app.core.account import PaperTradingAccount, TradingArm
from backend.app.models.events import OrderSide, OrderType, OrderState

log = logging.getLogger("engine")


class TimeInForce(str, Enum):
    DAY = "DAY"
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill


class BracketRole(str, Enum):
    ENTRY = "ENTRY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT_1 = "TAKE_PROFIT_1"
    TAKE_PROFIT_2 = "TAKE_PROFIT_2"


class BrokerFillFailed(Exception):
    """The real broker did not fill the order (rejected, timed out, or unreachable)."""
    pass


class InvalidOrderStateTransitionError(Exception):
    """Raised when an order attempts an illegal state transition."""
    pass


@dataclass(frozen=True)
class OrderAuditRecord:
    timestamp: datetime
    order_id: str
    symbol: str
    from_state: str
    to_state: str
    event_trigger: str
    reason: str
    fill_qty: int
    fill_price: float
    cum_filled_qty: int
    remaining_qty: int
    fee: float
    account_cash_after: float
    account_equity_after: float


@dataclass
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    fee: float
    slippage: float
    timestamp: datetime
    realized_pnl: float = 0.0


@dataclass
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    estimated_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    strategy_id: str = "MANUAL"
    bracket_role: Optional[BracketRole] = None
    parent_order_id: Optional[str] = None
    status: OrderState = OrderState.CREATED
    filled_qty: int = 0
    remaining_qty: int = field(init=False)
    avg_fill_price: float = 0.0
    fees_paid: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    submitted_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    fills: List[Fill] = field(default_factory=list)
    audit_trail: List[OrderAuditRecord] = field(default_factory=list)
    arm: TradingArm = TradingArm.INTRADAY
    # Real-broker bookkeeping (checkpointed with the order). An Alpaca order that
    # was not confirmed final stays attached here and is resolved before any new
    # order is sent, so a slow fill is booked once and never duplicated.
    broker_attempts: int = 0
    broker_client_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    broker_booked_qty: int = 0
    broker_booked_notional: float = 0.0
    swing_entry_atr: Optional[float] = None

    def __post_init__(self) -> None:
        self.remaining_qty = self.qty
        if isinstance(self.arm, str) and not isinstance(self.arm, TradingArm):
            self.arm = TradingArm(self.arm)


class ExecutionEngine:
    """
    Deterministic Order Execution Engine and Microstructure Fill Simulator.
    Manages working order book, FSM state transitions, slippage, and regulatory fees.
    """

    SEC_FEE_RATE: float = 0.0000278  # $27.80 per million dollars
    FINRA_TAF_RATE: float = 0.000166  # $0.000166 per share
    FINRA_TAF_CAP: float = 8.30
    MAX_BAR_PARTICIPATION_RATE: float = 0.10  # 10% max volume participation

    def __init__(
        self,
        account: PaperTradingAccount,
        risk_validator: Optional[Callable[[Order, PaperTradingAccount], Tuple[bool, str]]] = None,
    ) -> None:
        self.account: PaperTradingAccount = account
        self.risk_validator = risk_validator
        self.orders: Dict[str, Order] = {}
        self.working_orders: Dict[str, Order] = {}  # Orders in ACCEPTED or PARTIALLY_FILLED
        self.audit_log: List[OrderAuditRecord] = []
        # Real broker (Alpaca paper). None = the built-in fill simulator, which
        # tests, replays and simulation mode always use.
        self.broker = None
        # Optional safety gate set by the app: (order, is_exit) -> None to allow,
        # or (reason, hard, retry_sec) to refuse before anything reaches Alpaca.
        self.broker_gate: Optional[Callable[["Order", bool], Optional[Tuple[str, bool, float]]]] = None
        self._broker_retry_after: Dict[str, float] = {}

    # Seconds before an order the broker did not fill is tried again.
    BROKER_RETRY_SEC: float = 5.0
    BROKER_HARD_RETRY_SEC: float = 30.0

    def _broker_waiting(self, order: "Order") -> bool:
        if self.broker is None:
            return False
        now = time.monotonic()
        return now < self._broker_retry_after.get(order.id, 0.0) or now < self._broker_retry_after.get(f"sym:{order.symbol}", 0.0)

    def _reduces_position(self, order: "Order") -> bool:
        pos = self.account.positions.get(order.symbol)
        if pos is None:
            return False
        side = getattr(pos.side, "value", pos.side)
        return (side == "LONG" and order.side == OrderSide.SELL) or (side == "SHORT" and order.side == OrderSide.BUY)

    def _handle_broker_error(self, order: "Order", exc: Exception) -> None:
        """Keep exits alive (retry later); drop entries Alpaca refuses outright."""
        hard = bool(getattr(exc, "hard", False))
        is_exit = self._reduces_position(order)
        if (
            hard and not is_exit and order.filled_qty == 0
            and order.broker_order_id is None and order.id in self.working_orders
        ):
            log.error("Broker refused entry %s %s %s: %s; cancelling", order.id, order.side.value, order.symbol, exc)
            self._reject_after_broker(order, f"BROKER_REJECTED: {exc}")
            return
        delay = getattr(exc, "retry_sec", None) or (self.BROKER_HARD_RETRY_SEC if hard else self.BROKER_RETRY_SEC)
        until = time.monotonic() + delay
        self._broker_retry_after[order.id] = until
        # One backoff per symbol too: breaker/flatten paths create fresh orders on
        # every tick, and they must not turn a refusal into an order storm.
        self._broker_retry_after[f"sym:{order.symbol}"] = until
        level = logging.ERROR if is_exit else logging.WARNING
        log.log(level, "Broker did not fill %s %s %s (%s); retry in %.0fs", order.id, order.side.value, order.symbol, exc, delay)

    def _reject_after_broker(self, order: "Order", reason: str) -> None:
        order.status = OrderState.CANCELLED
        order.completed_at = datetime.now(timezone.utc)
        order.reject_reason = reason
        self.working_orders.pop(order.id, None)
        self._record_audit(order, OrderState.CANCELLED, "BROKER_REJECTED", reason)

    def _book_broker_order(self, order: "Order", alpaca: Dict) -> Tuple[int, float]:
        """New shares filled on the attached Alpaca order since the last booking."""
        from backend.app.core.broker import filled_avg, filled_qty, is_terminal
        total = filled_qty(alpaca)
        notional = total * filled_avg(alpaca)
        delta = total - order.broker_booked_qty
        price = (notional - order.broker_booked_notional) / delta if delta > 0 else 0.0
        if is_terminal(alpaca):
            order.broker_order_id = None
            order.broker_client_id = None
            order.broker_booked_qty = 0
            order.broker_booked_notional = 0.0
        else:
            order.broker_order_id = str(alpaca.get("id"))
            order.broker_booked_qty = total
            order.broker_booked_notional = notional
        return max(0, delta), price

    def _broker_execute(self, order: "Order", qty: int) -> Tuple[int, float]:
        """Fill `order` for real. Returns (shares, avg price) newly filled; raises BrokerError."""
        from backend.app.core.broker import BrokerError, BrokerNoFill, BrokerReject, is_terminal
        broker = self.broker
        if time.monotonic() < self._broker_retry_after.get(f"sym:{order.symbol}", 0.0):
            raise BrokerNoFill(f"{order.symbol} is in broker backoff")
        is_exit = self._reduces_position(order)

        # 1. Finish any earlier Alpaca order for this local order first.
        if order.broker_order_id is None and order.broker_client_id:
            found = broker.find_by_client_id(order.broker_client_id)
            if found is None:
                order.broker_client_id = None
            else:
                order.broker_order_id = str(found["id"])
        if order.broker_order_id:
            alpaca = broker.cancel_and_settle(broker.get_order(order.broker_order_id))
            delta, price = self._book_broker_order(order, alpaca)
            if delta > 0:
                return delta, price
            if not is_terminal(alpaca):
                raise BrokerNoFill(f"Alpaca order {alpaca.get('id')} for {order.symbol} still not final")

        # 2. Safety gates (market hours, entries paused on mismatch).
        if self.broker_gate is not None:
            refusal = self.broker_gate(order, is_exit)
            if refusal:
                reason, hard, retry_sec = refusal
                raise BrokerReject(reason, hard=hard, retry_sec=retry_sec)

        # 3. Never let an exit sell more than Alpaca really holds (a crash gap or
        #    a double trigger would otherwise flip the account short or long).
        if is_exit:
            held = broker.position_qty(order.symbol)
            same_direction = held > 0 if order.side == OrderSide.SELL else held < 0
            if not same_direction:
                raise BrokerReject(
                    f"Alpaca holds {held} {order.symbol}; refusing {order.side.value} {qty} (bot and Alpaca disagree)",
                    hard=True,
                )
            qty = min(qty, abs(held))

        # 4. Send a new order with a client id that is stable across restarts.
        order.broker_attempts += 1
        order.broker_client_id = f"adt-{order.id}-{order.broker_attempts}"
        limit = order.limit_price if order.order_type == OrderType.LIMIT else None
        alpaca = broker.submit_and_settle(order.symbol, order.side.value, qty, order.broker_client_id, limit)
        order.broker_order_id = str(alpaca.get("id"))
        order.broker_booked_qty = 0
        order.broker_booked_notional = 0.0
        delta, price = self._book_broker_order(order, alpaca)
        if delta <= 0:
            status = alpaca.get("status")
            raise BrokerNoFill(
                f"Alpaca order {alpaca.get('id')} {order.side.value} {qty} {order.symbol} ended {status} with no fill",
                hard=status == "rejected",
            )
        broker.status.fills_booked += 1
        broker.status.last_error = None
        log.info(
            "BROKER FILL %s %s %d/%d @ %.4f alpaca_id=%s client_id=%s",
            order.side.value, order.symbol, delta, qty, price, alpaca.get("id"), alpaca.get("client_order_id"),
        )
        return delta, price

    def settle_broker_orders(self) -> List["Fill"]:
        """Finish every Alpaca order still linked to a local order, whatever the local
        state (a cancelled local order can still have a live Alpaca order). Books any
        fill Alpaca made that the ledger has not seen. Called on a timer and at startup."""
        from backend.app.core.broker import is_terminal
        fills: List[Fill] = []
        if self.broker is None:
            return fills
        for order in list(self.orders.values()):
            if not order.broker_order_id and not order.broker_client_id:
                continue
            try:
                if order.broker_order_id is None:
                    found = self.broker.find_by_client_id(order.broker_client_id)
                    if found is None:
                        order.broker_client_id = None
                        continue
                    order.broker_order_id = str(found["id"])
                alpaca = self.broker.cancel_and_settle(self.broker.get_order(order.broker_order_id))
            except Exception as exc:
                log.warning("Could not settle Alpaca order for %s: %s", order.id, exc)
                continue
            delta, price = self._book_broker_order(order, alpaca)
            if delta > 0:
                log.error(
                    "BROKER LATE FILL booked: %s %s %d @ %.4f (local order %s was %s)",
                    order.side.value, order.symbol, delta, price, order.id, order.status.value,
                )
                was = order.status
                fills.append(self._apply_fill_to_ledger(order, delta, price, 0.0, 0.0, datetime.now(timezone.utc)))
                if was in (OrderState.CANCELLED, OrderState.REJECTED) and order.status != OrderState.FILLED:
                    order.status = was  # still cancelled locally; only the ledger moved
            if not is_terminal(alpaca):
                log.error("Alpaca order %s for %s still open after cancel", alpaca.get("id"), order.symbol)
        return fills

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        qty: int,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        estimated_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
        strategy_id: str = "MANUAL",
        client_order_id: Optional[str] = None,
        bracket_role: Optional[BracketRole] = None,
        parent_order_id: Optional[str] = None,
        arm: TradingArm = TradingArm.INTRADAY,
    ) -> Order:
        """Create a new order in CREATED state."""
        if qty <= 0:
            raise ValueError(f"Order quantity must be positive, got {qty}")
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and limit_price is None:
            raise ValueError(f"Limit price required for {order_type.value} orders")
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and stop_price is None:
            raise ValueError(f"Stop price required for {order_type.value} orders")

        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        client_id = client_order_id or f"cl_{uuid.uuid4().hex[:8]}"

        order = Order(
            id=order_id,
            client_order_id=client_id,
            symbol=symbol.upper(),
            side=side,
            order_type=order_type,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            estimated_price=estimated_price,
            time_in_force=time_in_force,
            strategy_id=strategy_id,
            bracket_role=bracket_role,
            parent_order_id=parent_order_id,
            arm=arm,
        )
        self.orders[order_id] = order
        self._record_audit(order, OrderState.CREATED, "ORDER_CREATED", "Order initialized")
        return order

    def submit_order(self, order_id: str) -> Order:
        """
        Transition order: CREATED -> SUBMITTED.
        Runs account buying power check and risk engine validator:
        - If pass: transitions SUBMITTED -> ACCEPTED.
        - If fail: transitions SUBMITTED -> REJECTED.
        """
        order = self.orders.get(order_id)
        if not order:
            raise KeyError(f"Order {order_id} not found")
        if order.status != OrderState.CREATED:
            raise InvalidOrderStateTransitionError(
                f"Cannot submit order in state {order.status.value}"
            )

        order.status = OrderState.SUBMITTED
        order.submitted_at = datetime.now(timezone.utc)
        self._record_audit(order, OrderState.SUBMITTED, "SUBMITTED", "Dispatched to engine")

        # 1. Account Buying Power & Concentration check
        est_price = order.limit_price or order.estimated_price or order.stop_price or 100.0
        can_afford, afford_reason = self.account.can_afford(
            order.symbol, order.side.value, order.qty, est_price
        )
        if not can_afford:
            return self._reject_order(order, afford_reason)

        # 2. Risk Engine pre-trade validation gate (if configured)
        if self.risk_validator is not None:
            risk_ok, risk_reason = self.risk_validator(order, self.account)
            if not risk_ok:
                return self._reject_order(order, risk_reason)

        # All checks passed: accept order
        order.status = OrderState.ACCEPTED
        order.accepted_at = datetime.now(timezone.utc)
        self.working_orders[order.id] = order
        self._record_audit(order, OrderState.ACCEPTED, "ACCEPTED", "Pre-trade risk and margin passed")
        return order

    def _reject_order(self, order: Order, reason: str) -> Order:
        """Transition order to REJECTED."""
        order.status = OrderState.REJECTED
        order.completed_at = datetime.now(timezone.utc)
        order.reject_reason = reason
        self._record_audit(order, OrderState.REJECTED, "RISK_REJECTED", reason)
        return order

    def cancel_order(self, order_id: str, reason: str = "USER_REQUEST") -> Order:
        """
        Cancel working order in ACCEPTED or PARTIALLY_FILLED state.
        Transitions to CANCELLED.
        """
        order = self.orders.get(order_id)
        if not order:
            raise KeyError(f"Order {order_id} not found")
        if order.status not in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED):
            raise InvalidOrderStateTransitionError(
                f"Cannot cancel order in state {order.status.value}"
            )

        order.status = OrderState.CANCELLED
        order.completed_at = datetime.now(timezone.utc)
        self.working_orders.pop(order.id, None)
        self._record_audit(order, OrderState.CANCELLED, "CANCEL_REQUEST", reason)
        return order

    def cancel_all_orders(self, reason: str = "CIRCUIT_BREAKER", arm: Optional[TradingArm] = None) -> List[Order]:
        """Cancel all active working orders, optionally filtered by trading arm."""
        cancelled = []
        for order_id in list(self.working_orders.keys()):
            order = self.working_orders.get(order_id)
            if order and arm is not None and getattr(order, "arm", None) != arm:
                continue
            cancelled.append(self.cancel_order(order_id, reason))
        return cancelled

    def calculate_fees(self, side: OrderSide, qty: int, fill_price: float) -> float:
        """
        Calculates exact SEC Section 31 and FINRA TAF regulatory fees on sell orders.
        Buys incur $0.00.
        """
        if side == OrderSide.BUY:
            return 0.0

        principal = qty * fill_price
        # SEC fee: $27.80 per million, rounded UP to nearest cent
        sec_fee = math.ceil(self.SEC_FEE_RATE * principal * 100.0) / 100.0

        # FINRA TAF: $0.000166 per share, min $0.01, capped at $8.30
        taf_fee = min(self.FINRA_TAF_CAP, round(self.FINRA_TAF_RATE * qty, 2))
        if qty > 0 and taf_fee < 0.01:
            taf_fee = 0.01

        return round(sec_fee + taf_fee, 2)

    def calculate_slippage(
        self,
        order: Order,
        market_price: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        bar_volume: int = 10000,
        bar_high: Optional[float] = None,
        bar_low: Optional[float] = None,
    ) -> float:
        """Dynamic microstructure slippage model based on spread and volume participation."""
        spread = (ask - bid) if (bid is not None and ask is not None) else max(0.01, market_price * 0.0004)
        volatility = (bar_high - bar_low) if (bar_high is not None and bar_low is not None) else max(0.01, market_price * 0.001)
        participation = math.sqrt(order.qty / max(1000, bar_volume))

        raw_slippage = (0.5 * spread) + (0.08 * volatility * participation)
        floor = max(0.01, market_price * 0.0001)  # 1 bps floor
        slippage = max(floor, raw_slippage)

        # 1.5x adverse multiplier on stop orders
        if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            slippage *= 1.5

        return round(slippage, 4)

    def process_quote(
        self,
        symbol: str,
        bid: float,
        ask: float,
        timestamp: datetime,
    ) -> List[Fill]:
        """Match working orders against an incoming quote."""
        symbol = symbol.upper()
        mid_price = (bid + ask) / 2.0
        self.account.update_market_price(symbol, mid_price)

        fills: List[Fill] = []
        matching_orders = [
            o for o in list(self.working_orders.values())
            if o.symbol == symbol and not (
                o.arm == TradingArm.SWING and o.side == OrderSide.BUY
                and o.strategy_id == "swing_panic_dip"
            )
        ]
        # A stop is the conservative outcome when one quote crosses both an
        # OCO stop and a profit target.  Process stops first and skip orders
        # removed by an earlier fill/cancel operation.
        matching_orders.sort(key=lambda order: order.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT))

        for order in matching_orders:
            if order.id not in self.working_orders or self._broker_waiting(order):
                continue
            fill_price: Optional[float] = None
            slippage = self.calculate_slippage(order, mid_price, bid=bid, ask=ask)

            if order.order_type == OrderType.MARKET:
                fill_price = (ask + slippage) if order.side == OrderSide.BUY else (bid - slippage)
            elif order.order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY and ask <= (order.limit_price or 0.0):
                    fill_price = min(order.limit_price or ask, ask)
                elif order.side == OrderSide.SELL and bid >= (order.limit_price or 0.0):
                    fill_price = max(order.limit_price or bid, bid)
            elif order.order_type == OrderType.STOP:
                if order.side == OrderSide.BUY and ask >= (order.stop_price or 0.0):
                    fill_price = ask + slippage
                elif order.side == OrderSide.SELL and bid <= (order.stop_price or 0.0):
                    fill_price = bid - slippage

            if fill_price is not None:
                try:
                    fill = self._execute_fill(order, order.remaining_qty, fill_price, slippage, timestamp, cancel_on_broker_error=False)
                except BrokerFillFailed:
                    continue
                fills.append(fill)
                if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
                    # A stop fill is an OCO terminal event. The caller will
                    # cancel sibling targets during fill reconciliation.
                    break

        return fills

    def process_bar(
        self,
        symbol: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        timestamp: datetime,
    ) -> List[Fill]:
        """Match working orders against an incoming 1-minute OHLCV bar."""
        symbol = symbol.upper()
        self.account.update_market_price(symbol, close)

        fills: List[Fill] = []
        matching_orders = [
            o for o in list(self.working_orders.values())
            if o.symbol == symbol and not (
                o.arm == TradingArm.SWING and o.side == OrderSide.BUY
                and o.strategy_id == "swing_panic_dip"
            )
        ]
        # Resolve an ambiguous OHLC bar conservatively: a stop is evaluated
        # before profit targets, and OCO children removed by reconciliation are
        # not allowed to fill later in the same snapshot.
        matching_orders.sort(key=lambda order: order.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT))
        # Max fillable quantity in bar under 10% participation cap
        max_fillable = max(10, int(volume * self.MAX_BAR_PARTICIPATION_RATE))

        for order in matching_orders:
            if order.id not in self.working_orders or self._broker_waiting(order):
                continue
            fill_price: Optional[float] = None
            slippage = self.calculate_slippage(
                order, close, bar_volume=volume, bar_high=high, bar_low=low
            )

            if order.order_type == OrderType.MARKET:
                spread_half = max(0.005, close * 0.0002)
                fill_price = (close + spread_half + slippage) if order.side == OrderSide.BUY else (close - spread_half - slippage)
            elif order.order_type == OrderType.LIMIT:
                limit_p = order.limit_price or 0.0
                if order.side == OrderSide.BUY and low <= limit_p:
                    # Price improvement if opened below limit
                    fill_price = min(limit_p, open_) if open_ <= limit_p else limit_p
                elif order.side == OrderSide.SELL and high >= limit_p:
                    fill_price = max(limit_p, open_) if open_ >= limit_p else limit_p
            elif order.order_type == OrderType.STOP:
                stop_p = order.stop_price or 0.0
                if order.side == OrderSide.BUY and high >= stop_p:
                    fill_price = max(stop_p, open_) + slippage
                elif order.side == OrderSide.SELL and low <= stop_p:
                    fill_price = min(stop_p, open_) - slippage

            if fill_price is not None:
                # Apply volume participation cap (the simulator only; a real
                # broker fills whatever it really fills).
                exec_qty = order.remaining_qty if self.broker is not None else min(order.remaining_qty, max_fillable)
                try:
                    fill = self._execute_fill(order, exec_qty, fill_price, slippage, timestamp, cancel_on_broker_error=False)
                except BrokerFillFailed:
                    continue
                fills.append(fill)
                if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
                    # A stop fill is an OCO terminal event.  The caller will
                    # cancel sibling targets during fill reconciliation.
                    break

        return fills

    def _execute_fill(
        self,
        order: Order,
        qty: int,
        price: float,
        slippage: float,
        timestamp: datetime,
        cancel_on_broker_error: bool = True,
    ) -> Fill:
        """Atomically record fill, update account, and advance order state.

        With a broker attached the order is first executed for real and the
        fill books the broker's quantity and average price, with no simulated
        fee. `cancel_on_broker_error` is for one-shot callers (swing): a failed
        broker fill cancels the local order instead of leaving it working.
        """
        if self.broker is not None:
            if order.status not in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED):
                # e.g. a swing order the risk validator rejected: never send it for real.
                raise BrokerFillFailed(f"order {order.id} is {order.status.value}, not sent to Alpaca")
            trigger_price = price
            try:
                qty, price = self._broker_execute(order, qty)
            except Exception as exc:
                if getattr(self.broker, "status", None) is not None:
                    self.broker.status.last_error = f"{order.side.value} {order.symbol}: {exc}"
                if cancel_on_broker_error and order.broker_order_id is None:
                    if order.id in self.working_orders:
                        self._reject_after_broker(order, f"BROKER_FAILED: {exc}")
                    # Still back off the symbol so a caller that retries every bar
                    # (swing emergency stop) cannot hammer Alpaca.
                    self._broker_retry_after[f"sym:{order.symbol}"] = time.monotonic() + (
                        getattr(exc, "retry_sec", None) or self.BROKER_RETRY_SEC
                    )
                else:
                    # Left working: the next quote/bar resolves or retries it.
                    self._handle_broker_error(order, exc)
                raise BrokerFillFailed(str(exc)) from exc
            self._broker_retry_after.pop(order.id, None)
            slippage = (price - trigger_price) if order.side == OrderSide.BUY else (trigger_price - price)
            fee = 0.0
        else:
            fee = self.calculate_fees(order.side, qty, price)
        return self._apply_fill_to_ledger(order, qty, price, fee, slippage, timestamp)

    def _apply_fill_to_ledger(
        self,
        order: Order,
        qty: int,
        price: float,
        fee: float,
        slippage: float,
        timestamp: datetime,
    ) -> Fill:
        """Record one fill on the order and the account, and advance the order state."""
        fill = Fill(
            fill_id=f"fl_{uuid.uuid4().hex[:10]}",
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            price=round(price, 4),
            fee=fee,
            slippage=round(slippage, 4),
            realized_pnl=0.0,
            timestamp=timestamp,
        )

        order.fills.append(fill)
        order.filled_qty += qty
        order.remaining_qty -= qty
        order.fees_paid = round(order.fees_paid + fee, 4)

        # Update order weighted average fill price
        total_val = sum(f.qty * f.price for f in order.fills)
        order.avg_fill_price = round(total_val / order.filled_qty, 4)

        # Apply to account ledger
        realized_delta, _ = self.account.apply_fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side.value,
            qty=qty,
            price=price,
            fee=fee,
            timestamp=timestamp,
            arm=getattr(order, "arm", TradingArm.INTRADAY),
            strategy_id=getattr(order, "strategy_id", "MANUAL"),
            stop_loss_price=getattr(order, "stop_price", None),
        )
        fill.realized_pnl = realized_delta

        if order.remaining_qty == 0:
            order.status = OrderState.FILLED
            order.completed_at = timestamp
            self.working_orders.pop(order.id, None)
            self._record_audit(
                order, OrderState.FILLED, "FULL_FILL",
                f"Filled {qty} @ ${price:.2f}, fee ${fee:.2f}",
                fill_qty=qty, fill_price=price, fee=fee
            )
        else:
            order.status = OrderState.PARTIALLY_FILLED
            self._record_audit(
                order, OrderState.PARTIALLY_FILLED, "PARTIAL_FILL",
                f"Partially filled {qty} @ ${price:.2f}, remaining {order.remaining_qty}",
                fill_qty=qty, fill_price=price, fee=fee
            )

        return fill

    def _record_audit(
        self,
        order: Order,
        to_state: OrderState,
        trigger: str,
        reason: str,
        fill_qty: int = 0,
        fill_price: float = 0.0,
        fee: float = 0.0,
    ) -> None:
        """Append an immutable audit entry."""
        from_state = order.audit_trail[-1].to_state if order.audit_trail else "NONE"
        record = OrderAuditRecord(
            timestamp=datetime.now(timezone.utc),
            order_id=order.id,
            symbol=order.symbol,
            from_state=str(from_state),
            to_state=to_state.value,
            event_trigger=trigger,
            reason=reason,
            fill_qty=fill_qty,
            fill_price=fill_price,
            cum_filled_qty=order.filled_qty,
            remaining_qty=order.remaining_qty,
            fee=fee,
            account_cash_after=self.account.cash,
            account_equity_after=self.account.equity,
        )
        order.audit_trail.append(record)
        self.audit_log.append(record)
        if len(self.audit_log) > 10000:
            self.audit_log = self.audit_log[-5000:]

    def prune_session_state(self, max_audit_records: int = 5000, max_orders: int = 1000) -> None:
        """Prune older orders and bound audit log to prevent unbounded memory growth."""
        if len(self.audit_log) > max_audit_records:
            self.audit_log = self.audit_log[-max_audit_records:]
        if len(self.orders) > max_orders:
            working_orders = {
                oid: order for oid, order in self.orders.items()
                if oid in self.working_orders or order.broker_order_id or order.broker_client_id
            }
            non_working = [order for oid, order in self.orders.items() if oid not in working_orders]
            keep_count = max(0, max_orders - len(working_orders))
            retained = non_working[-keep_count:] if keep_count else []
            kept_non_working = {order.id: order for order in retained}
            self.orders = {**kept_non_working, **working_orders}
