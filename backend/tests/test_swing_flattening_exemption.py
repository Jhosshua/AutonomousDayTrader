"""backend/tests/test_swing_flattening_exemption.py
Comprehensive unit tests for Milestone M9A:
- TradingArm enum and tagging on Position, Order, BracketOrder
- 4-Phase EOD Flattening exemption for Swing arm
- Hidden 5th Phase: Session boundary rollover exemption and holding_days increment
- Arm-aware risk evaluation (ATR stop ceiling bypass, 2 concurrent swing positions, $25k notional cap)
- Symbol mutual exclusion (Symbol reservation for AMD)
- Durable persistence round-trip serialization with swing state
"""
from datetime import date, datetime, timezone
import pytest

from backend.app.core.account import (
    AccountStatus,
    PaperTradingAccount,
    Position,
    PositionSide,
    TradingArm,
)
from backend.app.core.bracket import BracketOrder, DynamicBracketManager
from backend.app.core.engine import (
    ExecutionEngine,
    Order,
    OrderSide,
    OrderType,
)
from backend.app.core.flattening import (
    ET_TZ,
    FlatteningDirective,
    FlatteningPhase,
    MarketClock,
    ZeroOvernightFlatteningEngine,
)
from backend.app.core.risk import (
    InstitutionalRiskEngine,
    RiskEngineConfig,
)

from backend.app.core.runtime_state import (
    capture_runtime_state,
    restore_runtime_state,
    validate_runtime_state,
)
from backend.app.models.events import PositionState
from backend.app import main


@pytest.fixture(autouse=True)
def reset_test_state():
    main.flattening_engine.reset_for_new_session()
    main.last_session_date = None
    main.account.cash = main.account.initial_balance
    main.account.equity = main.account.initial_balance
    main.account.daily_starting_equity = main.account.initial_balance
    main.account.realized_pnl = 0.0
    main.account.unrealized_pnl = 0.0
    main.account.fees_paid = 0.0
    main.account.positions.clear()
    main.account.status = AccountStatus.ACTIVE
    main.risk_engine.reset_daily_metrics(main.account.initial_balance)
    main.engine.orders.clear()
    main.engine.working_orders.clear()
    main.bracket_manager.brackets.clear()
    main.bracket_manager.symbol_to_bracket.clear()
    main.swing_reserved_symbols.clear()
    yield
    main.flattening_engine.reset_for_new_session()
    main.last_session_date = None
    main.account.cash = main.account.initial_balance
    main.account.equity = main.account.initial_balance
    main.account.daily_starting_equity = main.account.initial_balance
    main.account.realized_pnl = 0.0
    main.account.unrealized_pnl = 0.0
    main.account.fees_paid = 0.0
    main.account.positions.clear()
    main.account.status = AccountStatus.ACTIVE
    main.risk_engine.reset_daily_metrics(main.account.initial_balance)
    main.engine.orders.clear()
    main.engine.working_orders.clear()
    main.bracket_manager.brackets.clear()
    main.bracket_manager.symbol_to_bracket.clear()
    main.swing_reserved_symbols.clear()




# ---------------------------------------------------------------------------
# 1. TradingArm Enum & Dataclass Tagging Tests
# ---------------------------------------------------------------------------
def test_trading_arm_enum_and_tagging():
    """Verify TradingArm enum values and default tagging on models."""
    assert TradingArm.INTRADAY.value == "INTRADAY"
    assert TradingArm.SWING.value == "SWING"

    # Position defaults to INTRADAY and holding_days=0
    pos_intra = Position(
        symbol="AAPL",
        side=PositionSide.LONG,
        shares=100,
        avg_entry_price=150.0,
        market_price=150.0,
    )
    assert pos_intra.arm == TradingArm.INTRADAY
    assert pos_intra.holding_days == 0
    assert pos_intra.stop_loss_price is None

    # Position explicitly tagged as SWING
    pos_swing = Position(
        symbol="MU",
        side=PositionSide.LONG,
        shares=200,
        avg_entry_price=100.0,
        market_price=105.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
        holding_days=2,
        stop_loss_price=92.50,
    )
    assert pos_swing.arm == TradingArm.SWING
    assert pos_swing.holding_days == 2
    assert pos_swing.stop_loss_price == 92.50

    # String conversion in Position.__post_init__
    pos_str = Position(
        symbol="LRCX",
        side=PositionSide.LONG,
        shares=50,
        avg_entry_price=500.0,
        market_price=500.0,
        arm="SWING",
    )
    assert pos_str.arm == TradingArm.SWING

    # to_state() serialization
    state = pos_swing.to_state()
    assert isinstance(state, PositionState)
    assert state.arm == "SWING"
    assert state.strategy_id == "swing_panic_dip"
    assert state.holding_days == 2
    assert state.stop_loss_price == 92.50

    # Order arm tagging
    order = Order(
        id="ord_1",
        client_order_id="cl_1",
        symbol="MU",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=100,
        arm=TradingArm.SWING,
    )
    assert order.arm == TradingArm.SWING

    # BracketOrder arm tagging
    bracket = BracketOrder(
        bracket_id="brk_1",
        symbol="MU",
        side="LONG",
        strategy_id="swing_panic_dip",
        total_qty=100,
        remaining_qty=100,
        entry_price=100.0,
        initial_stop_price=92.5,
        current_stop_price=92.5,
        target_1_price=108.0,
        target_1_qty=50,
        target_2_price=118.0,
        target_2_qty=50,
        r_distance=7.5,
        peak_price_since_entry=100.0,
        arm=TradingArm.SWING,
    )
    assert bracket.arm == TradingArm.SWING


# ---------------------------------------------------------------------------
# 2. Phase 4 EOD Flattening Audit Exemption
# ---------------------------------------------------------------------------
def test_flattening_phase_4_audit_exempts_swing_positions_and_orders():
    """Verify that Phase 4 audit passes when only SWING positions/orders remain, but fails for unclosed INTRADAY."""
    clock = MarketClock(datetime(2026, 9, 23, 15, 58, 0, tzinfo=ET_TZ))
    flattening_engine = ZeroOvernightFlatteningEngine(clock=clock)

    swing_pos = Position(
        symbol="MU",
        side=PositionSide.LONG,
        shares=100,
        avg_entry_price=100.0,
        market_price=100.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )
    swing_order = Order(
        id="ord_stop_mu",
        client_order_id="cl_mu",
        symbol="MU",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        qty=100,
        stop_price=92.5,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )

    # Case A: Only SWING position and SWING order present -> Audit MUST PASS
    directive = flattening_engine.execute_phase_4_audit(
        open_positions={"MU": swing_pos},
        working_orders=[swing_order],
    )
    assert directive.audit_passed is True
    assert directive.action_required == "AUDIT_PASSED_CLEAN_BOOK"
    assert directive.unclosed_symbols == []
    assert directive.cancel_all_orders is False
    assert directive.liquidate_all_positions is False

    # Case B: Lingering INTRADAY position present -> Audit MUST FAIL
    intraday_pos = Position(
        symbol="AAPL",
        side=PositionSide.LONG,
        shares=50,
        avg_entry_price=150.0,
        market_price=150.0,
        arm=TradingArm.INTRADAY,
    )
    directive_fail = flattening_engine.execute_phase_4_audit(
        open_positions={"MU": swing_pos, "AAPL": intraday_pos},
        working_orders=[swing_order],
    )
    assert directive_fail.audit_passed is False
    assert directive_fail.action_required == "AUDIT_FAILED_EMERGENCY_SWEEP"
    # Unclosed symbols must ONLY contain AAPL, NOT MU!
    assert directive_fail.unclosed_symbols == ["AAPL"]
    assert directive_fail.liquidate_all_positions is True


# ---------------------------------------------------------------------------
# 3. Full 4-Phase EOD Flattening in main.py preserves Swing Positions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_flattening_directive_preserves_swing_positions():
    """Verify that handle_flattening_directive cancels and liquidates only INTRADAY positions and orders."""
    acct = main.account
    eng = main.engine
    bm = main.bracket_manager

    # Clean test fixtures
    acct.positions.clear()
    eng.orders.clear()
    eng.working_orders.clear()
    bm.brackets.clear()
    bm.symbol_to_bracket.clear()
    acct.status = AccountStatus.ACTIVE

    now_dt = datetime(2026, 9, 23, 15, 55, 0, tzinfo=ET_TZ)

    # 1. Create an Intraday position in AAPL
    acct.apply_fill("ord_fill_aapl", "AAPL", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY)
    assert "AAPL" in acct.positions
    assert acct.positions["AAPL"].arm == TradingArm.INTRADAY

    # 2. Create a Swing position in MU
    acct.apply_fill(
        "ord_fill_mu", "MU", "BUY", 200, 100.0, 0.0, now_dt,
        arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=92.5
    )
    assert "MU" in acct.positions
    assert acct.positions["MU"].arm == TradingArm.SWING

    # 3. Create a protective stop order for MU (Swing)
    swing_stop = eng.create_order(
        symbol="MU",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        qty=200,
        stop_price=92.5,
        strategy_id="swing_panic_dip",
        arm=TradingArm.SWING,
    )
    eng.submit_order(swing_stop.id)
    assert swing_stop.id in eng.working_orders

    # 4. Trigger Phase 3 Mandatory Liquidation
    p3_directive = FlatteningDirective(
        phase=FlatteningPhase.MANDATORY_LIQUIDATION,
        timestamp=now_dt,
        action_required="LIQUIDATE_ALL_POSITIONS",
        lock_new_entries=True,
        cancel_all_orders=True,
        liquidate_all_positions=True,
    )
    await main.handle_flattening_directive(p3_directive)

    # INTRADAY AAPL must be liquidated; SWING MU must remain 100% INTACT!
    assert "AAPL" not in acct.positions
    assert "MU" in acct.positions
    assert acct.positions["MU"].shares == 200
    assert swing_stop.id in eng.working_orders

    # 5. Trigger Phase 4 Zero-Overnight Audit
    p4_directive = FlatteningDirective(
        phase=FlatteningPhase.ZERO_AUDIT,
        timestamp=datetime(2026, 9, 23, 15, 58, 0, tzinfo=ET_TZ),
        action_required="ZERO_AUDIT",
        lock_new_entries=True,
        cancel_all_orders=False,
        run_audit=True,
    )
    await main.handle_flattening_directive(p4_directive)

    # SWING MU position and protective stop still present
    assert "MU" in acct.positions
    assert swing_stop.id in eng.working_orders
    # Account status should remain ACTIVE (not marked EOD_FLAT since swing position is held)
    assert acct.status == AccountStatus.ACTIVE


# ---------------------------------------------------------------------------
# 4. Session Boundary Rollover Exemption & Holding Day Counter
# ---------------------------------------------------------------------------
def test_session_boundary_preserves_swing_positions_and_increments_holding_days():
    """Verify session boundary rollover does NOT liquidate swing positions and increments holding_days."""
    acct = main.account
    eng = main.engine
    bm = main.bracket_manager

    acct.positions.clear()
    eng.orders.clear()
    eng.working_orders.clear()
    bm.brackets.clear()
    bm.symbol_to_bracket.clear()
    main.last_session_date = date(2026, 9, 23)

    day1_dt = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)

    # Create Swing position in LRCX with holding_days=0
    acct.apply_fill(
        "fill_lrcx", "LRCX", "BUY", 40, 600.0, 0.0, day1_dt,
        arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=555.0
    )
    assert acct.positions["LRCX"].holding_days == 0

    # Create working stop order for LRCX
    lrcx_stop = eng.create_order(
        symbol="LRCX",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        qty=40,
        stop_price=555.0,
        strategy_id="swing_panic_dip",
        arm=TradingArm.SWING,
    )
    eng.submit_order(lrcx_stop.id)

    # Advance to Day 2 session boundary (next trading morning 09:30 ET)
    day2_dt = datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ)
    main._check_session_boundary(day2_dt)

    # LRCX position must NOT be liquidated
    assert "LRCX" in acct.positions
    assert acct.positions["LRCX"].shares == 40
    # holding_days must be incremented from 0 to 1
    assert acct.positions["LRCX"].holding_days == 1
    # Swing protective stop order must remain in working_orders
    assert lrcx_stop.id in eng.working_orders

    # Advance to Day 3
    day3_dt = datetime(2026, 9, 25, 9, 30, 0, tzinfo=ET_TZ)
    main._check_session_boundary(day3_dt)
    assert acct.positions["LRCX"].holding_days == 2


def test_session_boundary_liquidates_failed_intraday_positions():
    """Verify that prior-day unclosed INTRADAY positions are liquidated at session boundary while SWING is kept."""
    acct = main.account
    eng = main.engine

    acct.positions.clear()
    eng.orders.clear()
    eng.working_orders.clear()

    main.last_session_date = date(2026, 9, 23)

    day1_dt = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)

    # Add 1 failed unclosed intraday position and 1 swing position
    acct.apply_fill("fill_tsla", "TSLA", "BUY", 50, 200.0, 0.0, day1_dt, arm=TradingArm.INTRADAY)
    acct.apply_fill("fill_mu", "MU", "BUY", 100, 100.0, 0.0, day1_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")

    assert "TSLA" in acct.positions
    assert "MU" in acct.positions

    # Trigger boundary rollover
    day2_dt = datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ)
    main._check_session_boundary(day2_dt)

    # TSLA must be liquidated; MU must be kept!
    assert "TSLA" not in acct.positions
    assert "MU" in acct.positions
    assert acct.positions["MU"].holding_days == 1


# ---------------------------------------------------------------------------
# 5. Arm-Aware Risk Evaluation (Stop Ceiling Bypass, Sizing, Concurrency)
# ---------------------------------------------------------------------------
def test_arm_aware_risk_evaluation_stop_ceiling():
    """Verify that Swing orders bypass the intraday 4.0% stop ceiling while Intraday orders still reject it."""
    risk_engine = InstitutionalRiskEngine(RiskEngineConfig())

    # Intraday order with 6.0% stop distance -> REJECTED (STOP_DISTANCE_TOO_WIDE)
    res_intra = risk_engine.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=50,
        entry_price=100.0,
        stop_price=94.0,  # 6.0% stop distance (> 4.0% ceiling)
        account_equity=50000.0,
        buying_power=200000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
        arm=TradingArm.INTRADAY,
        strategy_id="orb",
    )
    assert res_intra.approved is False
    assert res_intra.rejection_code == "STOP_DISTANCE_TOO_WIDE"

    # Swing order with 7.5% stop distance (2.5x ATR) -> APPROVED (Bypasses 4.0% ceiling)
    res_swing = risk_engine.evaluate_order_request(
        symbol="MU",
        side="BUY",
        requested_qty=200,
        entry_price=100.0,
        stop_price=92.5,  # 7.5% stop distance
        account_equity=50000.0,
        buying_power=200000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )
    assert res_swing.approved is True
    assert res_swing.authorized_qty == 200

    # Swing order with invalid direction (stop >= entry for BUY) -> REJECTED
    res_swing_invalid = risk_engine.evaluate_order_request(
        symbol="MU",
        side="BUY",
        requested_qty=200,
        entry_price=100.0,
        stop_price=105.0,  # stop above entry
        account_equity=50000.0,
        buying_power=200000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )
    assert res_swing_invalid.approved is False
    assert res_swing_invalid.rejection_code == "INVALID_PRICE_GEOMETRY"


def test_swing_concurrency_and_notional_limits():
    """Verify max 2 concurrent swing positions and $25,000 slot notional limit."""
    risk_engine = InstitutionalRiskEngine(RiskEngineConfig(swing_slot_notional=25000.0, max_concurrent_swing_positions=2))

    # Notional cap test: $30,000 requested -> REJECTED
    res_over = risk_engine.evaluate_order_request(
        symbol="MU",
        side="BUY",
        requested_qty=300,
        entry_price=100.0,  # $30,000 notional > $25,000
        stop_price=92.5,
        account_equity=50000.0,
        buying_power=200000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )
    assert res_over.approved is False
    assert res_over.rejection_code == "SWING_NOTIONAL_CAP_EXCEEDED"

    # Concurrency test: when 2 swing positions are already active -> REJECTED
    res_limit = risk_engine.evaluate_order_request(
        symbol="KLAC",
        side="BUY",
        requested_qty=30,
        entry_price=700.0,  # $21,000 notional
        stop_price=650.0,
        account_equity=50000.0,
        buying_power=150000.0,
        active_positions_count=2,
        active_symbols={"MU", "LRCX"},
        active_sectors={"Semiconductors"},
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
        active_swing_positions_count=2,
    )
    assert res_limit.approved is False
    assert res_limit.rejection_code == "MAX_CONCURRENT_SWING_POSITIONS_REACHED"


def test_intraday_bypasses_swing_positions_concurrency():
    """Verify that having 2 active swing positions does not consume the 3 intraday position limit."""
    acct = main.account
    eng = main.engine
    bm = main.bracket_manager

    acct.positions.clear()
    eng.working_orders.clear()
    bm.brackets.clear()

    now_dt = datetime.now(timezone.utc)
    # Add 2 swing positions ($25k each)
    acct.apply_fill("s1", "MU", "BUY", 200, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
    acct.apply_fill("s2", "LRCX", "BUY", 40, 600.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
    assert len(acct.positions) == 2

    # Intraday order for NVDA should see 0 active intraday positions and pass
    order = eng.create_order(
        symbol="NVDA",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=10,
        limit_price=120.0,
        stop_price=118.0,
        arm=TradingArm.INTRADAY,
        strategy_id="vwap_pullback",
    )
    approved, reason = main.pre_trade_risk_validator(order, acct)
    assert approved is True, f"Failed with reason: {reason}"


# ---------------------------------------------------------------------------
# 6. AMD Symbol Reservation & Mutual Exclusion
# ---------------------------------------------------------------------------
def test_amd_symbol_reservation_locks_out_intraday():
    """Verify that reserving AMD for Swing locks out intraday entries, but allows swing entries."""
    acct = main.account
    eng = main.engine

    acct.positions.clear()
    main.release_symbol_for_swing("AMD")
    assert main.is_symbol_reserved_for_swing("AMD", acct) is False

    # Intraday order for AMD passes when not reserved
    order_intra = eng.create_order(
        symbol="AMD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=50,
        limit_price=140.0,
        stop_price=138.0,
        arm=TradingArm.INTRADAY,
        strategy_id="orb",
    )
    ok, r_intra = main.pre_trade_risk_validator(order_intra, acct)
    assert ok is True, f"Intraday AMD failed with: {r_intra}"

    # Reserve AMD for Swing (e.g. at 16:00 ET qualification)
    main.reserve_symbol_for_swing("AMD")
    assert main.is_symbol_reserved_for_swing("AMD", acct) is True

    # Intraday order for AMD is now REJECTED
    ok, reason = main.pre_trade_risk_validator(order_intra, acct)
    assert ok is False
    assert "SYMBOL_RESERVED_FOR_SWING" in reason

    # Swing order for AMD is APPROVED
    order_swing = eng.create_order(
        symbol="AMD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=150,
        limit_price=140.0,
        stop_price=130.0,  # 7.1% ATR stop
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )
    ok_swing, reason_swing = main.pre_trade_risk_validator(order_swing, acct)
    assert ok_swing is True

    # Release reservation
    main.release_symbol_for_swing("AMD")
    assert main.is_symbol_reserved_for_swing("AMD", acct) is False


def test_swing_locked_out_when_intraday_holds_symbol():
    """Verify two-way mutual exclusion: Swing cannot buy AMD if Intraday currently holds AMD."""
    acct = main.account
    eng = main.engine
    acct.positions.clear()
    main.release_symbol_for_swing("AMD")

    now_dt = datetime.now(timezone.utc)
    # Intraday buys AMD
    acct.apply_fill("fill_intra_amd", "AMD", "BUY", 50, 140.0, 0.0, now_dt, arm=TradingArm.INTRADAY)

    # Swing attempts entry in AMD -> REJECTED
    order_swing = eng.create_order(
        symbol="AMD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=150,
        limit_price=140.0,
        stop_price=130.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )
    ok, reason = main.pre_trade_risk_validator(order_swing, acct)
    assert ok is False
    assert "SWING_REJECTED" in reason
    assert "currently held by Intraday" in reason


# ---------------------------------------------------------------------------
# 7. Persistence & Ledger Serialization Round-Trip
# ---------------------------------------------------------------------------
def test_runtime_state_serialization_with_swing_positions():
    """Verify runtime state capture, restore, and validation with active swing positions."""
    acct = PaperTradingAccount(initial_cash=50000.0, max_position_notional=25000.0)
    eng = ExecutionEngine(account=acct)
    bm = DynamicBracketManager()
    risk = InstitutionalRiskEngine(RiskEngineConfig())
    clock = MarketClock()
    flattening = ZeroOvernightFlatteningEngine(clock=clock)
    from backend.app.strategies.adaptation import DynamicAdaptationEngine
    adaptation = DynamicAdaptationEngine()

    now_dt = datetime.now(timezone.utc)
    # Open Swing position in GS
    acct.apply_fill(
        "ord_gs_fill", "GS", "BUY", 50, 480.0, 0.0, now_dt,
        arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=445.0
    )
    acct.positions["GS"].holding_days = 3

    # Add protective stop order in working_orders
    stop_order = eng.create_order(
        symbol="GS",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        qty=50,
        stop_price=445.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
    )
    eng.submit_order(stop_order.id)

    # Encode and Decode state
    encoded = capture_runtime_state(
        account=acct,
        engine=eng,
        bracket_manager=bm,
        risk_engine=risk,
        flattening_engine=flattening,
        adaptation_engine=adaptation,
        strategies=[],
        entry_order_to_bracket={},
        bracket_realized_pnl={},
        completed_brackets_recorded=set(),
        latest_market_prices={"GS": 490.0},
        market_history={},
        recent_news=[],
        last_session_date=date(2026, 9, 23),
        last_vix_print=None,
        ledger_revision=1,
    )

    # Restore into fresh components
    new_acct = PaperTradingAccount(initial_cash=50000.0)
    new_eng = ExecutionEngine(account=new_acct)
    new_bm = DynamicBracketManager()

    restore_runtime_state(
        encoded,
        account=new_acct,
        engine=new_eng,
        bracket_manager=new_bm,
        risk_engine=risk,
        flattening_engine=flattening,
        adaptation_engine=adaptation,
        strategies=[],
        entry_order_to_bracket={},
        bracket_realized_pnl={},
        completed_brackets_recorded=set(),
        latest_market_prices={},
        market_history={},
        recent_news=[],
    )

    # Validate integrity
    validate_runtime_state(new_acct, new_eng, new_bm)

    assert "GS" in new_acct.positions
    restored_pos = new_acct.positions["GS"]
    assert restored_pos.shares == 50
    assert restored_pos.arm == TradingArm.SWING
    assert restored_pos.holding_days == 3
    assert restored_pos.stop_loss_price == 445.0
    assert stop_order.id in new_eng.working_orders
    assert new_eng.working_orders[stop_order.id].arm == TradingArm.SWING


def test_weekend_session_boundary_does_not_increment_holding_days():
    """Verify that advancing session boundary across Saturday/Sunday does not increment holding_days,
    and advancing to Monday increments holding_days by 1.
    """
    acct = main.account
    acct.positions.clear()
    main.last_session_date = None

    pos = Position(
        symbol="MU",
        side=PositionSide.LONG,
        shares=100,
        avg_entry_price=100.0,
        market_price=105.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
        holding_days=1,
    )
    acct.positions["MU"] = pos

    # Friday session (2026-09-18)
    fri_dt = datetime(2026, 9, 18, 16, 0, 0, tzinfo=timezone.utc)
    main._check_session_boundary(fri_dt)
    assert pos.holding_days == 1

    # Saturday session tick (2026-09-19)
    sat_dt = datetime(2026, 9, 19, 10, 0, 0, tzinfo=timezone.utc)
    main._check_session_boundary(sat_dt)
    assert pos.holding_days == 1, "Saturday must not increment holding_days"

    # Sunday session tick (2026-09-20)
    sun_dt = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
    main._check_session_boundary(sun_dt)
    assert pos.holding_days == 1, "Sunday must not increment holding_days"

    # Monday session tick (2026-09-21)
    mon_dt = datetime(2026, 9, 21, 9, 30, 0, tzinfo=timezone.utc)
    main._check_session_boundary(mon_dt)
    assert pos.holding_days == 2, "Monday session must increment holding_days by 1"

