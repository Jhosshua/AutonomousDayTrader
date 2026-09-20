"""backend/app/core/engine.py
Deterministic Order Execution Engine, 8-State Lifecycle FSM, and Microstructure Fill Simulator.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Callable, Dict, List, Optional, Tuple
import uuid

from backend.app.core.account import PaperTradingAccount
from backend.app.models.events import OrderSide, OrderType, OrderState


class TimeInForce(str, Enum):
    DAY = "DAY"
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill


class BracketRole(str, Enum):
    ENTRY = "ENTRY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT_1 = "TAKE_PROFIT_1"
    TAKE_PROFIT_2 = "TAKE_PROFIT_2"


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

    def __post_init__(self) -> None:
        self.remaining_qty = self.qty


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

    def cancel_all_orders(self, reason: str = "CIRCUIT_BREAKER") -> List[Order]:
        """Cancel all active working orders."""
        cancelled = []
        for order_id in list(self.working_orders.keys()):
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
        matching_orders = [o for o in list(self.working_orders.values()) if o.symbol == symbol]
        # A stop is the conservative outcome when one quote crosses both an
        # OCO stop and a profit target.  Process stops first and skip orders
        # removed by an earlier fill/cancel operation.
        matching_orders.sort(key=lambda order: order.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT))

        for order in matching_orders:
            if order.id not in self.working_orders:
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
                fill = self._execute_fill(order, order.remaining_qty, fill_price, slippage, timestamp)
                fills.append(fill)

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
        matching_orders = [o for o in list(self.working_orders.values()) if o.symbol == symbol]
        # Resolve an ambiguous OHLC bar conservatively: a stop is evaluated
        # before profit targets, and OCO children removed by reconciliation are
        # not allowed to fill later in the same snapshot.
        matching_orders.sort(key=lambda order: order.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT))
        # Max fillable quantity in bar under 10% participation cap
        max_fillable = max(10, int(volume * self.MAX_BAR_PARTICIPATION_RATE))

        for order in matching_orders:
            if order.id not in self.working_orders:
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
                # Apply volume participation cap
                exec_qty = min(order.remaining_qty, max_fillable)
                fill = self._execute_fill(order, exec_qty, fill_price, slippage, timestamp)
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
    ) -> Fill:
        """Atomically record fill, update account, and advance order state."""
        fee = self.calculate_fees(order.side, qty, price)
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
