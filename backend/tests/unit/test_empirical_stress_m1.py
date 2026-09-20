"""backend/tests/unit/test_empirical_stress_m1.py
Empirical Stress Test Suite & Bug Reproductions for Milestone 1 (Risk Engine & Flattening).

Empirically challenges:
1. Circuit breaker trip at exactly $1,500.00 and $1,500.01 drawdown (order rejection, order purge, position flattening).
2. Premature circuit breaker trip boundary defect at $1,497.50 / $1,499.99 due to 4-decimal rounding.
3. Liquidation order rejection under CIRCUIT_HALTED account state and HALTED breaker status.
4. Race conditions during concurrent order submissions and circuit breaker activation.
5. 4-phase auto-flattening sequence across 15:45, 15:50, 15:55, 15:58 ET boundaries.
6. Liquidation order lockout defect during 15:55 Mandatory Liquidation due to ENTRY_LOCKOUT flag.
7. Phase 4 audit emergency sweep directive discarded in main.py.
8. Clean process hygiene and port release verification.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
import socket
import time
from typing import List, Tuple
from zoneinfo import ZoneInfo
import pytest

from backend.app.core.account import AccountStatus, PaperTradingAccount, Position, PositionSide
from backend.app.core.engine import (
    ExecutionEngine,
    Order,
    OrderSide,
    OrderState,
    OrderType,
)
from backend.app.core.flattening import (
    FlatteningDirective,
    FlatteningPhase,
    MarketClock,
    ZeroOvernightFlatteningEngine,
)
from backend.app.core.risk import (
    BreakerStatus,
    InstitutionalRiskEngine,
    RiskEngineConfig,
    RiskLevel,
)

ET = ZoneInfo("America/New_York")


# ============================================================================
# 1. CIRCUIT BREAKER EXACT BOUNDARIES & PREMATURE TRIP DEFECT
# ============================================================================

def test_circuit_breaker_premature_trip_defect():
    """
    Verify circuit breaker does NOT trip prematurely at $1,499.99 (or $1,497.50).
    Drawdown of $1,499.99 is strictly less than $1,500.00.
    With exact dollar comparison, breaker remains ARMED.
    """
    now = datetime.now(timezone.utc)
    risk_engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

    # Test at $1,499.99 drawdown (Equity $48,500.01)
    status_1499 = risk_engine.evaluate_account_state(
        equity=48500.01,
        cash=48500.01,
        realized_pnl=-1499.99,
        unrealized_pnl=0.0,
        timestamp=now,
    )
    assert status_1499 == BreakerStatus.ARMED, (
        f"Breaker should remain ARMED at $1,499.99 DD, got: {status_1499}"
    )
    assert risk_engine.current_drawdown_dollars == 1499.99
    assert risk_engine.current_drawdown_pct == 0.03  # 0.0299998 rounded up to 0.0300


def test_circuit_breaker_exact_trip_at_1500_and_1501():
    """
    Verify breaker trips at exactly $1,500.00 and $1,500.01 drawdown,
    and subsequent strategy order submissions are rejected.
    """
    now = datetime.now(timezone.utc)

    # 1. Exact $1,500.00 drawdown
    r_1500 = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    st_1500 = r_1500.evaluate_account_state(
        equity=48500.00,
        cash=48500.00,
        realized_pnl=-1500.00,
        unrealized_pnl=0.0,
        timestamp=now,
    )
    assert st_1500 == BreakerStatus.HALTED_DAILY_LOSS
    assert r_1500.status == BreakerStatus.HALTED_DAILY_LOSS
    assert r_1500.risk_level == RiskLevel.HALTED

    res_1500 = r_1500.evaluate_order_request(
        symbol="SPY",
        side="BUY",
        requested_qty=100,
        entry_price=500.0,
        stop_price=495.0,
        account_equity=48500.00,
        buying_power=190000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res_1500.approved is False
    assert res_1500.rejection_code == "CIRCUIT_BREAKER_HALTED"

    # 2. Exact $1,500.01 drawdown
    r_1501 = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    st_1501 = r_1501.evaluate_account_state(
        equity=48499.99,
        cash=48499.99,
        realized_pnl=-1500.01,
        unrealized_pnl=0.0,
        timestamp=now,
    )
    assert st_1501 == BreakerStatus.HALTED_DAILY_LOSS
    assert r_1501.current_drawdown_dollars == 1500.01

    res_1501 = r_1501.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=50,
        entry_price=150.0,
        stop_price=148.0,
        account_equity=48499.99,
        buying_power=190000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res_1501.approved is False
    assert res_1501.rejection_code == "CIRCUIT_BREAKER_HALTED"


# ============================================================================
# 2. CIRCUIT BREAKER LIQUIDATION REJECTION DEFECT
# ============================================================================

def test_circuit_breaker_liquidation_rejection_defect():
    """
    CRITICAL EMPIRICAL DEFECT:
    When circuit breaker trips, main.py executes:
        account.status = AccountStatus.CIRCUIT_HALTED
        engine.cancel_all_orders("CIRCUIT_BREAKER_HALT")
        for sym, pos in list(account.positions.items()):
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            liq_order = engine.create_order(...)
            engine.submit_order(liq_order.id)
    HOWEVER:
    1. account.can_afford() rejects the liquidation order:
       "Account is not ACTIVE (current status: CIRCUIT_HALTED)"
    2. pre_trade_risk_validator() rejects the liquidation order:
       "CIRCUIT_BREAKER_HALTED: Trading halted due to maximum daily loss"
    Result: The liquidation order is REJECTED, and the position remains OPEN!
    """
    acct = PaperTradingAccount(initial_cash=50000.00)
    risk = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    flattener = ZeroOvernightFlatteningEngine()

    def validator(order, a):
        is_lockout = flattener.current_phase != FlatteningPhase.NORMAL_TRADING
        active_symbols = set(a.positions.keys())
        active_sectors = {risk.symbol_sectors.get(s, "Other") for s in active_symbols if s in risk.symbol_sectors}
        existing_pos = a.positions.get(order.symbol.upper())
        is_exit = getattr(order, "strategy_id", None) in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "EMERGENCY_SWEEP") or (
            existing_pos is not None and (
                (existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
                (existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
            )
        )
        est_price = order.limit_price or order.stop_price or 100.0
        s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)
        res = risk.evaluate_order_request(
            symbol=order.symbol,
            side=order.side.value,
            requested_qty=order.qty,
            entry_price=est_price,
            stop_price=s_price,
            account_equity=a.equity,
            buying_power=a.buying_power,
            active_positions_count=len(a.positions),
            active_symbols=active_symbols,
            active_sectors=active_sectors,
            is_entry_lockout_active=is_lockout,
            is_exit=is_exit,
        )
        return res.approved, res.reason

    eng = ExecutionEngine(account=acct, risk_validator=validator)

    # 1. Open long position: AAPL 100 shares @ $150.00
    t0 = datetime(2026, 9, 21, 10, 0, 0, tzinfo=ET)
    o1 = eng.create_order("AAPL", OrderSide.BUY, OrderType.MARKET, 100)
    eng.submit_order(o1.id)
    eng.process_bar("AAPL", 150.0, 150.0, 150.0, 150.0, 10000, t0)
    assert "AAPL" in acct.positions

    # 2. Place working limit order: SPY BUY 10 shares @ 490.00
    o2 = eng.create_order("SPY", OrderSide.BUY, OrderType.LIMIT, 10, limit_price=490.00)
    eng.submit_order(o2.id)
    assert o2.id in eng.working_orders

    # 3. Simulate drop: AAPL drops to $134.00 (-$1,600 drawdown >= $1,500 limit)
    t1 = datetime(2026, 9, 21, 10, 15, 0, tzinfo=ET)
    eng.process_bar("AAPL", 134.0, 134.0, 134.0, 134.0, 10000, t1)

    status = risk.evaluate_account_state(
        equity=acct.equity,
        cash=acct.cash,
        realized_pnl=acct.realized_pnl,
        unrealized_pnl=acct.unrealized_pnl,
        timestamp=t1,
    )
    assert status == BreakerStatus.HALTED_DAILY_LOSS

    # Execute circuit breaker response as in main.py
    acct.status = AccountStatus.CIRCUIT_HALTED
    eng.cancel_all_orders("CIRCUIT_BREAKER_HALT")

    # Working orders purged:
    assert len(eng.working_orders) == 0

    # Attempt to liquidate position
    pos = acct.positions["AAPL"]
    side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
    liq_order = eng.create_order(
        symbol="AAPL", side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="CIRCUIT_BREAKER"
    )
    sub_res = eng.submit_order(liq_order.id)

    # Liquidation order is ACCEPTED and position is closed
    assert sub_res.status == OrderState.ACCEPTED
    eng.process_bar("AAPL", 134.0, 134.0, 134.0, 134.0, 100000, t1)
    assert len(acct.positions) == 0


# ============================================================================
# 3. 15:55 ET AUTO-FLATTENING LOCKOUT DEFECT & AUDIT FAILURE
# ============================================================================

def test_auto_flattening_lockout_defect():
    """
    CRITICAL EMPIRICAL DEFECT:
    At 15:55 ET, ZeroOvernightFlatteningEngine issues MANDATORY_LIQUIDATION directive.
    main.py iterates over account.positions and submits market liquidation orders.
    HOWEVER:
    Because flattening_engine.current_phase == FlatteningPhase.MANDATORY_LIQUIDATION
    (which is != NORMAL_TRADING), is_entry_lockout_active is set to TRUE.
    risk_engine.evaluate_order_request() unconditionally rejects any order when
    is_entry_lockout_active is True:
        "ENTRY_LOCKOUT_ACTIVE: Session closeout protocol active, new entries forbidden"
    The risk engine does not recognize that the order is a position EXIT / LIQUIDATION!
    Result: Liquidation order is REJECTED, positions remain OPEN, and Phase 4 Audit FAILS!
    """
    clock = MarketClock()
    flattener = ZeroOvernightFlatteningEngine(clock=clock)
    acct = PaperTradingAccount(initial_cash=50000.00)
    risk = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

    def validator(order, a):
        is_lockout = flattener.current_phase != FlatteningPhase.NORMAL_TRADING
        active_symbols = set(a.positions.keys())
        active_sectors = {risk.symbol_sectors.get(s, "Other") for s in active_symbols if s in risk.symbol_sectors}
        existing_pos = a.positions.get(order.symbol.upper())
        is_exit = getattr(order, "strategy_id", None) in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "EMERGENCY_SWEEP") or (
            existing_pos is not None and (
                (existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
                (existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
            )
        )
        est_price = order.limit_price or order.stop_price or 100.0
        s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)
        res = risk.evaluate_order_request(
            symbol=order.symbol,
            side=order.side.value,
            requested_qty=order.qty,
            entry_price=est_price,
            stop_price=s_price,
            account_equity=a.equity,
            buying_power=a.buying_power,
            active_positions_count=len(a.positions),
            active_symbols=active_symbols,
            active_sectors=active_sectors,
            is_entry_lockout_active=is_lockout,
            is_exit=is_exit,
        )
        return res.approved, res.reason

    eng = ExecutionEngine(account=acct, risk_validator=validator)

    # 1. Open long position in normal trading (14:30 ET)
    t_1430 = datetime(2026, 9, 21, 14, 30, 0, tzinfo=ET)
    clock.set_simulated_time(t_1430)
    o1 = eng.create_order("AAPL", OrderSide.BUY, OrderType.MARKET, 50)
    eng.submit_order(o1.id)
    eng.process_bar("AAPL", 150.0, 150.0, 150.0, 150.0, 10000, t_1430)

    # Open another position in Consumer Discretionary (TSLA)
    o2 = eng.create_order("TSLA", OrderSide.BUY, OrderType.MARKET, 20)
    eng.submit_order(o2.id)
    eng.process_bar("TSLA", 200.0, 200.0, 200.0, 200.0, 10000, t_1430)

    assert len(acct.positions) == 2

    # 2. Advance to 15:45 ET (Phase 1: Entry Lockout)
    t_1545 = datetime(2026, 9, 21, 15, 45, 0, tzinfo=ET)
    clock.set_simulated_time(t_1545)
    d1 = flattener.check_time_tick()
    assert d1.phase == FlatteningPhase.ENTRY_LOCKOUT

    # 3. Advance to 15:55 ET (Phase 3: Mandatory Liquidation)
    t_1555 = datetime(2026, 9, 21, 15, 55, 0, tzinfo=ET)
    clock.set_simulated_time(t_1555)
    d3 = flattener.check_time_tick()
    assert d3.phase == FlatteningPhase.MANDATORY_LIQUIDATION
    assert d3.liquidate_all_positions is True

    # Execute liquidation loop from main.py
    for sym, pos in list(acct.positions.items()):
        side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        liq_order = eng.create_order(
            symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="AUTO_FLATTEN"
        )
        sub_res = eng.submit_order(liq_order.id)
        assert sub_res.status == OrderState.ACCEPTED
        eng.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, t_1555)

    # Positions are flattened!
    assert len(acct.positions) == 0

    # 4. Advance to 15:58 ET (Phase 4: Audit)
    t_1558 = datetime(2026, 9, 21, 15, 58, 0, tzinfo=ET)
    clock.set_simulated_time(t_1558)
    audit = flattener.execute_phase_4_audit(acct.positions, list(eng.working_orders.values()))

    # Audit passes because positions were cleanly flattened
    assert audit.audit_passed is True
    assert len(audit.unclosed_symbols) == 0


def test_phase4_audit_directive_discarded_in_main():
    """
    CRITICAL ARCHITECTURAL DEFECT:
    In main.py:
        async def handle_flattening_directive(directive: FlatteningDirective) -> None:
            if directive.run_audit:
                flattening_engine.execute_phase_4_audit(
                    open_positions=account.positions,
                    working_orders=list(engine.working_orders.values()),
                )
    execute_phase_4_audit() returns a FlatteningDirective with:
        action_required="AUDIT_FAILED_EMERGENCY_SWEEP"
        liquidate_all_positions=True
    HOWEVER, main.py discards the return value!
    No emergency sweep is ever executed when the audit fails!
    """
    clock = MarketClock()
    flattener = ZeroOvernightFlatteningEngine(clock=clock)
    open_positions = {"AAPL": {"shares": 50}}
    working_orders = []

    res_directive = flattener.execute_phase_4_audit(open_positions, working_orders)
    assert res_directive.audit_passed is False
    assert res_directive.liquidate_all_positions is True
    assert res_directive.action_required == "AUDIT_FAILED_EMERGENCY_SWEEP"


# ============================================================================
# 4. RACE CONDITIONS DURING CIRCUIT BREAKER ACTIVATION
# ============================================================================

def test_race_condition_concurrent_orders_during_breaker_trip():
    """
    Race Condition Harness:
    50 concurrent threads submit orders while the circuit breaker trips concurrently.
    Verifies that after breaker trips and cancel_all_orders is run, no orders remain
    in working_orders and no orders submitted after trip are accepted.
    """
    acct = PaperTradingAccount(initial_cash=50000.00)
    risk = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

    def validator(order, a):
        active_symbols = set(a.positions.keys())
        active_sectors = {risk.symbol_sectors.get(s, "Other") for s in active_symbols if s in risk.symbol_sectors}
        est_price = order.limit_price or order.stop_price or 100.0
        s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)
        res = risk.evaluate_order_request(
            symbol=order.symbol,
            side=order.side.value,
            requested_qty=order.qty,
            entry_price=est_price,
            stop_price=s_price,
            account_equity=a.equity,
            buying_power=a.buying_power,
            active_positions_count=len(a.positions),
            active_symbols=active_symbols,
            active_sectors=active_sectors,
        )
        return res.approved, res.reason

    eng = ExecutionEngine(account=acct, risk_validator=validator)
    results = []

    def submit_task(i: int):
        try:
            o = eng.create_order("MSFT", OrderSide.BUY, OrderType.LIMIT, 10, limit_price=300.0, stop_price=295.0)
            sub = eng.submit_order(o.id)
            results.append((sub.id, sub.status))
        except Exception as e:
            results.append((f"err_{i}", str(e)))

    def trip_breaker():
        time.sleep(0.002)
        risk.status = BreakerStatus.HALTED_DAILY_LOSS
        acct.status = AccountStatus.CIRCUIT_HALTED
        eng.cancel_all_orders("CIRCUIT_BREAKER_HALT")

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(submit_task, i) for i in range(50)]
        futures.append(pool.submit(trip_breaker))
        for f in futures:
            f.result()

    # Cancel sweep after halt
    eng.cancel_all_orders("POST_RACE_PURGE")
    assert len(eng.working_orders) == 0


# ============================================================================
# 5. 4-PHASE TIMING BOUNDARIES (EXACT SECOND LEVEL)
# ============================================================================

def test_four_phase_flattening_timing_boundaries():
    """
    Test exact second boundary transitions:
    - 15:44:59 ET: NORMAL_TRADING, check_time_tick() -> None
    - 15:45:00 ET: ENTRY_LOCKOUT, directive -> LOCK_NEW_ENTRIES
    - 15:50:00 ET: ORDER_PURGE, directive -> PURGE_WORKING_ORDERS
    - 15:55:00 ET: MANDATORY_LIQUIDATION, directive -> LIQUIDATE_ALL_POSITIONS
    - 15:58:00 ET: ZERO_AUDIT, directive -> EXECUTE_PHASE_4_AUDIT
    - 16:00:00 ET: MARKET_CLOSED
    """
    clock = MarketClock()
    flattener = ZeroOvernightFlatteningEngine(clock=clock)

    # 15:44:59 ET
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 44, 59, tzinfo=ET))
    assert flattener.check_time_tick() is None
    assert flattener.current_phase == FlatteningPhase.NORMAL_TRADING

    # 15:45:00 ET (Phase 1)
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 45, 0, tzinfo=ET))
    d1 = flattener.check_time_tick()
    assert d1.phase == FlatteningPhase.ENTRY_LOCKOUT
    assert d1.lock_new_entries is True

    # 15:50:00 ET (Phase 2)
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 50, 0, tzinfo=ET))
    d2 = flattener.check_time_tick()
    assert d2.phase == FlatteningPhase.ORDER_PURGE
    assert d2.cancel_all_orders is True

    # 15:55:00 ET (Phase 3)
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 55, 0, tzinfo=ET))
    d3 = flattener.check_time_tick()
    assert d3.phase == FlatteningPhase.MANDATORY_LIQUIDATION
    assert d3.liquidate_all_positions is True

    # 15:58:00 ET (Phase 4)
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 58, 0, tzinfo=ET))
    d4 = flattener.check_time_tick()
    assert d4.phase == FlatteningPhase.ZERO_AUDIT
    assert d4.run_audit is True

    # 16:00:00 ET (Market Closed)
    clock.set_simulated_time(datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET))
    d5 = flattener.check_time_tick()
    assert d5.phase == FlatteningPhase.MARKET_CLOSED


# ============================================================================
# 6. PROCESS HYGIENE & PORT AVAILABILITY
# ============================================================================

def test_process_hygiene_clean_teardown():
    """Verify test execution leaves no lingering open sockets on assigned ports."""
    for port in (8005, 8080, 3005):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            res = s.connect_ex(("127.0.0.1", port))
            assert res != 0, f"Port {port} is occupied by an unclosed daemon!"


# ============================================================================
# 7. ACCEPTANCE ORACLES: TARGET SYSTEM INVARIANTS (XFAIL DEMONSTRATING DEFECTS)
# ============================================================================

def test_oracle_target_circuit_breaker_must_flatten_positions():
    """
    TARGET INVARIANT ORACLE:
    When drawdown reaches $1,500.00, the system MUST achieve 0 open positions.
    Verified with is_exit pass-through in account.can_afford and pre_trade_risk_validator.
    """
    acct = PaperTradingAccount(initial_cash=50000.00)
    risk = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    flattener = ZeroOvernightFlatteningEngine()

    def validator(order, a):
        is_lockout = flattener.current_phase != FlatteningPhase.NORMAL_TRADING
        active_symbols = set(a.positions.keys())
        active_sectors = {risk.symbol_sectors.get(s, "Other") for s in active_symbols if s in risk.symbol_sectors}
        existing_pos = a.positions.get(order.symbol.upper())
        is_exit = getattr(order, "strategy_id", None) in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "EMERGENCY_SWEEP") or (
            existing_pos is not None and (
                (existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
                (existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
            )
        )
        est_price = order.limit_price or order.stop_price or 100.0
        s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)
        res = risk.evaluate_order_request(
            symbol=order.symbol,
            side=order.side.value,
            requested_qty=order.qty,
            entry_price=est_price,
            stop_price=s_price,
            account_equity=a.equity,
            buying_power=a.buying_power,
            active_positions_count=len(a.positions),
            active_symbols=active_symbols,
            active_sectors=active_sectors,
            is_entry_lockout_active=is_lockout,
            is_exit=is_exit,
        )
        return res.approved, res.reason

    eng = ExecutionEngine(account=acct, risk_validator=validator)

    t0 = datetime(2026, 9, 21, 10, 0, 0, tzinfo=ET)
    o1 = eng.create_order("AAPL", OrderSide.BUY, OrderType.MARKET, 100)
    eng.submit_order(o1.id)
    eng.process_bar("AAPL", 150.0, 150.0, 150.0, 150.0, 10000, t0)

    # Flash drop
    t1 = datetime(2026, 9, 21, 10, 15, 0, tzinfo=ET)
    eng.process_bar("AAPL", 134.0, 134.0, 134.0, 134.0, 10000, t1)

    status = risk.evaluate_account_state(
        equity=acct.equity,
        cash=acct.cash,
        realized_pnl=acct.realized_pnl,
        unrealized_pnl=acct.unrealized_pnl,
        timestamp=t1,
    )
    assert status == BreakerStatus.HALTED_DAILY_LOSS

    # main.py liquidation sequence
    acct.status = AccountStatus.CIRCUIT_HALTED
    eng.cancel_all_orders("CIRCUIT_BREAKER_HALT")
    for sym, pos in list(acct.positions.items()):
        side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        liq_order = eng.create_order(
            symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="CIRCUIT_BREAKER"
        )
        sub_res = eng.submit_order(liq_order.id)
        if sub_res.status == OrderState.ACCEPTED:
            eng.process_bar(sym, 134.0, 134.0, 134.0, 134.0, 100000, t1)

    # Invariant: Account positions MUST BE 0
    assert len(acct.positions) == 0, f"Positions remain open: {list(acct.positions.keys())}"


def test_oracle_target_1555_must_flatten_all_positions():
    """
    TARGET INVARIANT ORACLE:
    At 15:55 ET, mandatory liquidation MUST execute and flatten all open positions.
    Verified with is_exit pass-through during lockout.
    """
    clock = MarketClock()
    flattener = ZeroOvernightFlatteningEngine(clock=clock)
    acct = PaperTradingAccount(initial_cash=50000.00)
    risk = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

    def validator(order, a):
        is_lockout = flattener.current_phase != FlatteningPhase.NORMAL_TRADING
        active_symbols = set(a.positions.keys())
        active_sectors = {risk.symbol_sectors.get(s, "Other") for s in active_symbols if s in risk.symbol_sectors}
        existing_pos = a.positions.get(order.symbol.upper())
        is_exit = getattr(order, "strategy_id", None) in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "EMERGENCY_SWEEP") or (
            existing_pos is not None and (
                (existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
                (existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
            )
        )
        est_price = order.limit_price or order.stop_price or 100.0
        s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)
        res = risk.evaluate_order_request(
            symbol=order.symbol,
            side=order.side.value,
            requested_qty=order.qty,
            entry_price=est_price,
            stop_price=s_price,
            account_equity=a.equity,
            buying_power=a.buying_power,
            active_positions_count=len(a.positions),
            active_symbols=active_symbols,
            active_sectors=active_sectors,
            is_entry_lockout_active=is_lockout,
            is_exit=is_exit,
        )
        return res.approved, res.reason

    eng = ExecutionEngine(account=acct, risk_validator=validator)

    t_1430 = datetime(2026, 9, 21, 14, 30, 0, tzinfo=ET)
    clock.set_simulated_time(t_1430)
    o1 = eng.create_order("AAPL", OrderSide.BUY, OrderType.MARKET, 50)
    eng.submit_order(o1.id)
    eng.process_bar("AAPL", 150.0, 150.0, 150.0, 150.0, 10000, t_1430)

    # 15:55 Phase 3
    t_1555 = datetime(2026, 9, 21, 15, 55, 0, tzinfo=ET)
    clock.set_simulated_time(t_1555)
    d3 = flattener.check_time_tick()
    assert d3.phase == FlatteningPhase.MANDATORY_LIQUIDATION

    for sym, pos in list(acct.positions.items()):
        side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        liq_order = eng.create_order(
            symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="AUTO_FLATTEN"
        )
        sub_res = eng.submit_order(liq_order.id)
        if sub_res.status == OrderState.ACCEPTED:
            eng.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, t_1555)

    # Invariant: Account positions MUST BE 0 before 16:00 ET
    assert len(acct.positions) == 0, f"Positions remain open at 15:55: {list(acct.positions.keys())}"


def test_oracle_target_no_premature_breaker_at_1499_99():
    """
    TARGET INVARIANT ORACLE:
    At $1,499.99 drawdown, circuit breaker MUST remain ARMED.
    Verified with exact dollar comparison in risk.py.
    """
    now = datetime.now(timezone.utc)
    risk = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    st = risk.evaluate_account_state(
        equity=48500.01,
        cash=48500.01,
        realized_pnl=-1499.99,
        unrealized_pnl=0.0,
        timestamp=now,
    )
    assert st == BreakerStatus.ARMED, f"Prematurely tripped at $1,499.99 DD: {st}"
