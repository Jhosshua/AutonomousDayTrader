"""backend/tests/unit/test_engine.py
Unit test suite for ExecutionEngine, 8-state FSM order lifecycle, and microstructure fill simulator.
"""
from datetime import datetime, timezone
import pytest

from backend.app.core.account import PaperTradingAccount
from backend.app.core.engine import (
    ExecutionEngine,
    OrderSide,
    OrderType,
    OrderState,
    InvalidOrderStateTransitionError,
)


def test_order_fsm_happy_path():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now = datetime.now(timezone.utc)

    # 1. Create order
    order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=100,
        limit_price=150.00,
        strategy_id="test",
    )
    assert order.status == OrderState.CREATED
    assert order.remaining_qty == 100

    # 2. Submit order
    submitted = engine.submit_order(order.id)
    assert submitted.status == OrderState.ACCEPTED
    assert order.id in engine.working_orders

    # 3. Match against quote: ask is 149.50 <= limit_price 150.00
    fills = engine.process_quote("AAPL", bid=149.40, ask=149.50, timestamp=now)
    assert len(fills) == 1
    assert submitted.status == OrderState.FILLED
    assert order.id not in engine.working_orders
    assert submitted.filled_qty == 100
    assert submitted.remaining_qty == 0

    # 4. Check audit trail
    states = [a.to_state for a in submitted.audit_trail]
    assert states == ["CREATED", "SUBMITTED", "ACCEPTED", "FILLED"]


def test_order_fsm_reject_insufficient_bp():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)

    order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=10000,
        limit_price=150.00,  # $1.5M > $200k BP
    )
    rejected = engine.submit_order(order.id)
    assert rejected.status == OrderState.REJECTED
    assert rejected.reject_reason is not None
    assert rejected.id not in engine.working_orders


def test_order_fsm_user_cancel():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)

    order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=100,
        limit_price=150.00,
    )
    engine.submit_order(order.id)
    assert order.status == OrderState.ACCEPTED

    cancelled = engine.cancel_order(order.id, reason="USER_CANCEL")
    assert cancelled.status == OrderState.CANCELLED
    assert order.id not in engine.working_orders


def test_order_fsm_illegal_transition():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now = datetime.now(timezone.utc)

    order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=100,
        limit_price=150.00,
    )
    engine.submit_order(order.id)
    engine.process_quote("AAPL", bid=149.00, ask=149.50, timestamp=now)
    assert order.status == OrderState.FILLED

    # Illegal transition: Attempt to cancel a FILLED order
    with pytest.raises(InvalidOrderStateTransitionError):
        engine.cancel_order(order.id)


def test_limit_order_bar_price_improvement():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now = datetime.now(timezone.utc)

    order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=100,
        limit_price=100.00,
    )
    engine.submit_order(order.id)

    # Bar opens below limit at $98.00
    fills = engine.process_bar(
        symbol="AAPL",
        open_=98.00,
        high=99.00,
        low=97.00,
        close=98.50,
        volume=50000,
        timestamp=now,
    )
    assert len(fills) == 1
    assert fills[0].price == 98.00  # Price improvement from 100.00 to 98.00


def test_stop_loss_trigger_with_adverse_slippage():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now = datetime.now(timezone.utc)

    order = engine.create_order(
        symbol="TSLA",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        qty=100,
        stop_price=148.00,
    )
    engine.submit_order(order.id)

    # Bar low breaches stop price at $147.00
    fills = engine.process_bar(
        symbol="TSLA",
        open_=147.50,
        high=149.00,
        low=147.00,
        close=147.20,
        volume=20000,
        timestamp=now,
    )
    assert len(fills) == 1
    fill = fills[0]
    # Slippage applied adversely: fill price is below min(stop_price, open)
    assert fill.price < 147.50
    assert fill.slippage > 0.0


def test_partial_fill_volume_participation():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now = datetime.now(timezone.utc)

    order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=1000,
        limit_price=40.00,
    )
    engine.submit_order(order.id)

    # Bar volume is 2,000 shares. 10% volume participation cap = 200 shares.
    fills = engine.process_bar(
        symbol="AAPL",
        open_=39.00,
        high=40.00,
        low=38.50,
        close=39.20,
        volume=2000,
        timestamp=now,
    )
    assert len(fills) == 1
    assert fills[0].qty == 200
    assert order.status == OrderState.PARTIALLY_FILLED
    assert order.filled_qty == 200
    assert order.remaining_qty == 800
    assert order.id in engine.working_orders


def test_sec_and_finra_fee_deductions():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)

    # BUY orders incur $0 fees
    buy_fee = engine.calculate_fees(OrderSide.BUY, 1000, 100.00)
    assert buy_fee == 0.00

    # SELL order: 1,000 shares @ $100.00 ($100k principal)
    # SEC fee: ceil(0.0000278 * 100,000 * 100) / 100 = $2.78
    # FINRA TAF: round(0.000166 * 1000, 2) = $0.17
    # Total fee = $2.95
    sell_fee = engine.calculate_fees(OrderSide.SELL, 1000, 100.00)
    assert sell_fee == 2.95


def test_cancel_all_orders():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)

    o1 = engine.create_order("AAPL", OrderSide.BUY, OrderType.LIMIT, 100, limit_price=140.00)
    o2 = engine.create_order("TSLA", OrderSide.BUY, OrderType.LIMIT, 50, limit_price=180.00)
    engine.submit_order(o1.id)
    engine.submit_order(o2.id)
    assert len(engine.working_orders) == 2

    cancelled = engine.cancel_all_orders(reason="TEST_FLATTEN")
    assert len(cancelled) == 2
    assert len(engine.working_orders) == 0
    assert all(o.status == OrderState.CANCELLED for o in cancelled)


def test_audit_trail_completeness():
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now = datetime.now(timezone.utc)

    order = engine.create_order("AAPL", OrderSide.BUY, OrderType.LIMIT, 100, limit_price=150.00)
    engine.submit_order(order.id)
    engine.process_quote("AAPL", bid=149.00, ask=149.50, timestamp=now)

    assert len(order.audit_trail) >= 4
    last = order.audit_trail[-1]
    assert last.to_state == "FILLED"
    assert last.fill_qty == 100
    assert last.account_cash_after < 50000.00
    assert len(engine.audit_log) >= 4
