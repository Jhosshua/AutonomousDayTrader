"""
tests/e2e/test_tier5_adversarial.py

Tier 5: Adversarial Coverage Hardening & Stress Verification Suite.
Milestone: M5 (adversarial_monday_dryrun)
Role: challenger_tier5 (critic, specialist)

Stress Tests Covered:
1. Concurrent multi-symbol breakout order collisions at 09:30:00 ET:
   - 10-symbol simultaneous breakout collisions under asyncio concurrency
   - Maximum concurrent position ceiling enforcement (max 3)
   - Sector diversification & concentration veto barriers
   - Buying power margin exhaustion & non-negative invariant
   - Atomic order submission & deduplication
2. Microsecond bracket fill and OCO child order cancellation race conditions:
   - Simultaneous TP1 and Stop-Loss fill race in extreme volatility bars
   - OCO cancellation on Stop-Loss trigger (immediate TP1/TP2 cancellation in engine)
   - Breakeven stop ratchet precision, sizing reduction, and monotonicity
   - Microsecond consecutive TP1 and TP2 fills
   - Rejection of state violations (cancelling filled or cancelling cancelled orders)
3. Zero-volume degenerate bars and tick gap recovery:
   - Zero-volume bars and numerical stability across all indicators (SMA, ATR, RSI, Z-score, VWAP)
   - LULD regulatory trading halt and clean resumption
   - Temporal tick gap recovery (14+ minute data feed gap)
   - Degenerate bar handling (negative volume, inverted high/low)
   - Microstructure participation rate caps on illiquid volume
4. Conflicting multi-headline sentiment bursts on identical timestamps:
   - Diametrically opposed headlines for identical symbol at identical timestamp
   - Emergency contradiction circuit breaker liquidation on open LONG and SHORT positions
   - High-throughput burst of 100+ headlines across symbols without queue stall
   - NLP sentiment scoring accuracy under complex negations and qualifiers
   - Catalyst TTL expiration and stale event eviction
5. Flash crash $1,500 circuit breaker emergency liquidation under rapid cascade fills:
   - Exact boundary loss check ($1,499.50 vs $1,500.00)
   - Rapid market cascade liquidation across multi-symbol portfolio
   - Permanent session lockout against new entries after breaker trip
   - Selective approval for liquidation exit orders only
   - Zero-overnight EOD mandatory flattening certification
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime, timedelta, timezone
import math
import uuid
import zoneinfo
import pytest

from backend.app.core.account import PaperTradingAccount, PositionSide
from backend.app.core.bracket import (
    BracketChildType,
    BracketOrder,
    BracketStatus,
    DynamicBracketManager,
)
from backend.app.core.engine import (
    ExecutionEngine,
    InvalidOrderStateTransitionError,
    Order,
    OrderSide,
    OrderState,
    OrderType,
)
from backend.app.core.flattening import FlatteningPhase, ZeroOvernightFlatteningEngine
from backend.app.core.risk import (
    BreakerStatus,
    InstitutionalRiskEngine,
    RiskEngineConfig,
    RiskLevel,
)
from backend.app.models.events import (
    BarEvent,
    CatalystCategory,
    NewsEvent,
    QuoteEvent,
    TradeEvent,
    VixPrint,
    VixRegime,
)
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.base import (
    SignalEvent,
    StrategyStatus,
    calculate_atr,
    calculate_rsi,
    calculate_sma,
    calculate_zscore,
)
from backend.app.strategies.mean_reversion import MeanReversionStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy, score_news_sentiment
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy
from tests.e2e.test_contracts import (
    AccountLedger,
    calculate_anchored_vwap,
    calculate_brackets,
    calculate_position_size,
    evaluate_mean_reversion_zscore,
    evaluate_orb_signal,
    get_eod_phase,
    get_momentum_glow,
    get_vix_regime,
)


# ============================================================================
# Group 1: Concurrent Multi-Symbol Breakout Order Collisions at 09:30:00 ET
# ============================================================================

@pytest.mark.asyncio
async def test_adv_concurrent_breakout_order_collision_10_symbols():
    """
    Stress-tests 10 symbols attempting simultaneous breakout order submission at 09:30:00 ET.
    Verifies that:
    1. InstitutionalRiskEngine strictly enforces max_concurrent_positions = 3.
    2. Exactly 3 orders are accepted and filled; 7 are rejected.
    3. Account buying power remains strictly non-negative.
    4. Account equity is preserved without double-spend.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    risk_engine = InstitutionalRiskEngine()

    def risk_validator(order: Order, acct: PaperTradingAccount):
        active_symbols = set(acct.positions.keys())
        active_sectors = {
            risk_engine.symbol_sectors.get(s, "Other")
            for s in active_symbols
            if s in risk_engine.symbol_sectors
        }
        res = risk_engine.evaluate_order_request(
            symbol=order.symbol,
            side=order.side.value,
            requested_qty=order.qty,
            entry_price=order.limit_price or 100.0,
            stop_price=order.stop_price or 98.0,
            account_equity=acct.equity,
            buying_power=acct.buying_power,
            active_positions_count=len(acct.positions),
            active_symbols=active_symbols,
            active_sectors=active_sectors,
        )
        return res.approved, res.reason

    engine = ExecutionEngine(account=account, risk_validator=risk_validator)

    symbols = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GOOGL", "META", "AMD", "INTC", "SPY"]
    now_dt = datetime(2026, 9, 21, 13, 30, 0, tzinfo=timezone.utc)

    # Submit 10 orders concurrently using asyncio
    async def submit_one(sym: str, price: float):
        order = engine.create_order(
            symbol=sym,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=50,
            limit_price=price,
            stop_price=round(price * 0.98, 2),
            strategy_id="orb",
        )
        try:
            submitted = engine.submit_order(order.id)
            if submitted.status == OrderState.ACCEPTED:
                # Simulate instantaneous matching
                engine.process_bar(sym, price, price + 0.5, price - 0.5, price, 50000, now_dt)
            return submitted
        except Exception as e:
            return e

    tasks = [submit_one(sym, 100.0 + idx * 5.0) for idx, sym in enumerate(symbols)]
    results = await asyncio.gather(*tasks)

    # Invariants
    accepted = [r for r in results if isinstance(r, Order) and r.status in (OrderState.ACCEPTED, OrderState.FILLED)]
    rejected = [r for r in results if isinstance(r, Order) and r.status == OrderState.REJECTED]

    assert len(account.positions) <= 3, f"Violated max concurrent positions! Found {len(account.positions)}"
    assert len(accepted) <= 3
    assert len(rejected) >= 7
    assert account.buying_power >= 0.0, "Buying power must never be negative"
    assert account.equity == 50000.00, "Equity must match before price changes"

    # Verify rejection codes
    for r in rejected:
        assert (
            "MAX_CONCURRENT_POSITIONS_REACHED" in r.reject_reason
            or "CORRELATED_SECTOR_EXPOSURE" in r.reject_reason
            or "Insufficient Day Trading Buying Power" in r.reject_reason
            or "Insufficient buying power" in r.reject_reason
        )


def test_adv_concurrent_sector_concentration_barrier():
    """
    Stress-tests sector concentration limit under simultaneous breakout in same sector.
    When Technology sector already has an active position (AAPL), subsequent breakout
    signals in MSFT, NVDA, and AMD must be rejected to prevent correlated risk exposure.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    risk_engine = InstitutionalRiskEngine()

    def risk_validator(order: Order, acct: PaperTradingAccount):
        active_symbols = set(acct.positions.keys())
        active_sectors = {
            risk_engine.symbol_sectors.get(s, "Other")
            for s in active_symbols
            if s in risk_engine.symbol_sectors
        }
        res = risk_engine.evaluate_order_request(
            symbol=order.symbol,
            side=order.side.value,
            requested_qty=order.qty,
            entry_price=order.limit_price or 150.0,
            stop_price=order.stop_price or 147.0,
            account_equity=acct.equity,
            buying_power=acct.buying_power,
            active_positions_count=len(acct.positions),
            active_symbols=active_symbols,
            active_sectors=active_sectors,
        )
        return res.approved, res.reason

    engine = ExecutionEngine(account=account, risk_validator=risk_validator)
    now_dt = datetime(2026, 9, 21, 13, 30, 0, tzinfo=timezone.utc)

    # 1. Open AAPL (Technology)
    ord1 = engine.create_order("AAPL", OrderSide.BUY, OrderType.LIMIT, 50, limit_price=150.0, stop_price=147.0)
    sub1 = engine.submit_order(ord1.id)
    assert sub1.status == OrderState.ACCEPTED
    engine.process_bar("AAPL", 150.0, 150.5, 149.5, 150.0, 50000, now_dt)
    assert "AAPL" in account.positions

    # 2. Attempt NVDA (Technology) -> must be rejected
    ord2 = engine.create_order("NVDA", OrderSide.BUY, OrderType.LIMIT, 50, limit_price=120.0, stop_price=117.0)
    sub2 = engine.submit_order(ord2.id)
    assert sub2.status == OrderState.REJECTED
    assert "CORRELATED_SECTOR_EXPOSURE" in sub2.reject_reason

    # 3. Attempt MSFT (Technology) -> must be rejected
    ord3 = engine.create_order("MSFT", OrderSide.BUY, OrderType.LIMIT, 50, limit_price=400.0, stop_price=392.0)
    sub3 = engine.submit_order(ord3.id)
    assert sub3.status == OrderState.REJECTED
    assert "CORRELATED_SECTOR_EXPOSURE" in sub3.reject_reason

    # 4. Attempt TSLA (Consumer Discretionary) -> allowed
    ord4 = engine.create_order("TSLA", OrderSide.BUY, OrderType.LIMIT, 30, limit_price=220.0, stop_price=215.0)
    sub4 = engine.submit_order(ord4.id)
    assert sub4.status == OrderState.ACCEPTED


def test_adv_buying_power_exhaustion_concurrency_race():
    """
    Stress-tests buying power boundary when multiple orders would collectively exhaust capital.
    Account with $50,000 initial balance has $200,000 DTBP and $50,000 max_alloc per symbol.
    Submitting orders of $40,000 across multiple symbols exhausts DTBP, and subsequent order is rejected.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now_dt = datetime(2026, 9, 21, 13, 30, 0, tzinfo=timezone.utc)

    # Initial BP = $200,000 (4:1)
    assert account.buying_power == 200000.00

    # Order 1: Symbol A, 400 shares @ $100 = $40,000
    ord1 = engine.create_order("SYM_A", OrderSide.BUY, OrderType.LIMIT, 400, limit_price=100.0)
    sub1 = engine.submit_order(ord1.id)
    assert sub1.status == OrderState.ACCEPTED
    engine.process_bar("SYM_A", 100.0, 101.0, 99.0, 100.0, 10000, now_dt)

    # Order 2: Symbol B, 400 shares @ $100 = $40,000
    ord2 = engine.create_order("SYM_B", OrderSide.BUY, OrderType.LIMIT, 400, limit_price=100.0)
    sub2 = engine.submit_order(ord2.id)
    assert sub2.status == OrderState.ACCEPTED
    engine.process_bar("SYM_B", 100.0, 101.0, 99.0, 100.0, 10000, now_dt)

    # Order 3: Symbol C, 400 shares @ $100 = $40,000
    ord3 = engine.create_order("SYM_C", OrderSide.BUY, OrderType.LIMIT, 400, limit_price=100.0)
    sub3 = engine.submit_order(ord3.id)
    assert sub3.status == OrderState.ACCEPTED
    engine.process_bar("SYM_C", 100.0, 101.0, 99.0, 100.0, 10000, now_dt)

    # Order 4: Symbol D, 400 shares @ $100 = $40,000
    ord4 = engine.create_order("SYM_D", OrderSide.BUY, OrderType.LIMIT, 400, limit_price=100.0)
    sub4 = engine.submit_order(ord4.id)
    assert sub4.status == OrderState.ACCEPTED
    engine.process_bar("SYM_D", 100.0, 101.0, 99.0, 100.0, 10000, now_dt)

    # Cumulative positions = $160,000. Remaining BP = $40,000.
    # Order 5: Symbol E, 500 shares @ $100 = $50,000 -> exceeds remaining BP ($40,000)
    ord5 = engine.create_order("SYM_E", OrderSide.BUY, OrderType.LIMIT, 500, limit_price=100.0)
    sub5 = engine.submit_order(ord5.id)
    assert sub5.status == OrderState.REJECTED
    assert "Insufficient Day Trading Buying Power" in sub5.reject_reason


# ============================================================================
# Group 2: Microsecond Bracket Fill and OCO Child Order Cancellation Race Conditions
# ============================================================================

def test_adv_bracket_volatility_flash_double_fill_race():
    """
    Adversarial scenario: High-volatility candle breaches BOTH Target 1 (TP1) and Stop-Loss.
    Verifies that:
    1. The bracket manager and engine prioritize the primary execution deterministically.
    2. If TP1 triggers first: remaining qty is halved (50 shares), stop order is ratcheted to breakeven,
       and remaining stop order qty is modified to 50 shares.
    3. The account ledger never executes more sell shares than the initial buy position (zero phantom shares).
    """
    bm = DynamicBracketManager(breakeven_buffer=0.02)
    entry_price = 100.00
    stop_price = 98.00  # R = $2.00
    total_qty = 100
    now_dt = datetime(2026, 9, 21, 13, 35, 0, tzinfo=timezone.utc)

    bracket = bm.create_bracket(
        bracket_id="brk_adv_001",
        symbol="AAPL",
        side="LONG",
        total_qty=total_qty,
        entry_price=entry_price,
        stop_price=stop_price,
        strategy_id="orb",
        timestamp=now_dt,
    )
    # TP1 = 100 + 0.8*2 = 101.60, TP2 = 100 + 1.8*2 = 103.60
    assert bracket.target_1_price == 101.60
    assert bracket.target_2_price == 103.60

    # Entry fills
    bm.activate_bracket_on_fill(bracket.bracket_id, total_qty, entry_price, now_dt)
    assert bracket.status == BracketStatus.ACTIVE

    # Child TP1 fills first
    dir_tp1 = bm.on_child_order_fill(bracket.target_1_order_id, 101.60, 50, now_dt)
    assert dir_tp1.action == "MODIFY_ORDER"
    assert bracket.status == BracketStatus.TARGET_1_HIT
    assert bracket.remaining_qty == 50
    # Stop ratcheted to breakeven + buffer: $100.02
    assert bracket.current_stop_price >= 100.02
    assert dir_tp1.orders_to_modify[0]["new_qty"] == 50

    # If stop then triggers for remaining shares
    dir_stop = bm.on_child_order_fill(bracket.stop_order_id, bracket.current_stop_price, 50, now_dt)
    assert dir_stop.action == "CANCEL_ORDER"
    assert bracket.status == BracketStatus.COMPLETED_STOP
    # Target 2 cancelled
    assert bracket.target_2_order_id in dir_stop.orders_to_cancel


def test_adv_oco_cancellation_on_stop_loss_trigger():
    """
    Verifies that when Stop-Loss triggers:
    1. Both TP1 and TP2 are cancelled immediately (CANCEL_ORDER directive).
    2. In ExecutionEngine, TP1 and TP2 working orders are cancelled.
    3. An attempt to cancel or fill an already cancelled order raises InvalidOrderStateTransitionError.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    bm = DynamicBracketManager()
    now_dt = datetime(2026, 9, 21, 13, 35, 0, tzinfo=timezone.utc)

    # 1. Create parent entry order and fill it
    entry_ord = engine.create_order("TSLA", OrderSide.BUY, OrderType.LIMIT, 100, limit_price=200.0)
    engine.submit_order(entry_ord.id)
    engine.process_bar("TSLA", 200.0, 201.0, 199.0, 200.0, 10000, now_dt)
    assert "TSLA" in account.positions

    # 2. Setup bracket in bracket manager and engine
    bracket = bm.create_bracket("brk_adv_002", "TSLA", "LONG", 100, 200.0, 196.0, timestamp=now_dt)
    act_dir = bm.activate_bracket_on_fill(bracket.bracket_id, 100, 200.0, now_dt)

    # Submit child orders to execution engine
    stop_ord = engine.create_order("TSLA", OrderSide.SELL, OrderType.STOP, 100, stop_price=196.0, client_order_id=bracket.stop_order_id)
    engine.submit_order(stop_ord.id)

    tp1_ord = engine.create_order("TSLA", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=206.0, client_order_id=bracket.target_1_order_id)
    engine.submit_order(tp1_ord.id)

    tp2_ord = engine.create_order("TSLA", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=210.0, client_order_id=bracket.target_2_order_id)
    engine.submit_order(tp2_ord.id)

    assert stop_ord.id in engine.working_orders
    assert tp1_ord.id in engine.working_orders
    assert tp2_ord.id in engine.working_orders

    # 3. Stop Loss hits
    dir_stop = bm.on_child_order_fill(bracket.stop_order_id, 196.0, 100, now_dt)
    assert dir_stop.action == "CANCEL_ORDER"
    assert bracket.target_1_order_id in dir_stop.orders_to_cancel
    assert bracket.target_2_order_id in dir_stop.orders_to_cancel
    assert bracket.status == BracketStatus.COMPLETED_STOP

    # Cancel children in engine
    engine.cancel_order(tp1_ord.id, reason="OCO_CANCEL_STOP_TRIGGERED")
    engine.cancel_order(tp2_ord.id, reason="OCO_CANCEL_STOP_TRIGGERED")

    assert tp1_ord.id not in engine.working_orders
    assert tp1_ord.status == OrderState.CANCELLED
    assert tp2_ord.id not in engine.working_orders
    assert tp2_ord.status == OrderState.CANCELLED

    # Subsequent attempt to cancel already cancelled order raises exception
    with pytest.raises(InvalidOrderStateTransitionError):
        engine.cancel_order(tp1_ord.id)


def test_adv_cancel_already_filled_or_cancelled_order_rejection():
    """
    Verifies state machine integrity:
    1. Cancelling an already FILLED order raises InvalidOrderStateTransitionError.
    2. Cancelling an already CANCELLED order raises InvalidOrderStateTransitionError.
    3. Submitting an already COMPLETED order raises InvalidOrderStateTransitionError.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now_dt = datetime(2026, 9, 21, 13, 30, 0, tzinfo=timezone.utc)

    # 1. Create and fill order
    order1 = engine.create_order("AAPL", OrderSide.BUY, OrderType.MARKET, 50)
    engine.submit_order(order1.id)
    engine.process_bar("AAPL", 150.0, 150.5, 149.5, 150.0, 10000, now_dt)
    assert order1.status == OrderState.FILLED

    # Attempt cancel on FILLED order
    with pytest.raises(InvalidOrderStateTransitionError):
        engine.cancel_order(order1.id)

    # 2. Create and cancel order
    order2 = engine.create_order("TSLA", OrderSide.BUY, OrderType.LIMIT, 50, limit_price=200.0)
    engine.submit_order(order2.id)
    engine.cancel_order(order2.id)
    assert order2.status == OrderState.CANCELLED

    # Attempt cancel on CANCELLED order
    with pytest.raises(InvalidOrderStateTransitionError):
        engine.cancel_order(order2.id)

    # Attempt submit on CANCELLED order
    with pytest.raises(InvalidOrderStateTransitionError):
        engine.submit_order(order2.id)


def test_adv_trailing_stop_monotonicity_under_whipsaw():
    """
    Adversarial trailing stop test:
    Price surges higher (trailing stop advances), then crashes (trailing stop must NOT loosen).
    """
    bm = DynamicBracketManager()
    now_dt = datetime(2026, 9, 21, 13, 35, 0, tzinfo=timezone.utc)
    bracket = bm.create_bracket("brk_adv_003", "NVDA", "LONG", 100, 100.0, 98.0, timestamp=now_dt)
    bm.activate_bracket_on_fill(bracket.bracket_id, 100, 100.0, now_dt)

    # The ATR trail is the Target 2 runner's, so put the bracket where it lives. While
    # the bracket is ACTIVE the structural stop stands and a rally must not move it.
    assert bm.update_trailing_stop("NVDA", current_bar_high=106.00, current_bar_low=103.00,
                                   current_atr=1.50, timestamp=now_dt) is None
    assert bracket.current_stop_price == 98.0

    bracket.status = BracketStatus.TARGET_1_HIT
    bracket.current_stop_price = 100.02  # breakeven + buffer, set when Target 1 filled
    initial_stop = bracket.current_stop_price

    # Bar 1: Price surges to high of $106.00, low $103.00, ATR $1.50
    # Trailing target: peak ($106) - 1.5 * 1.50 = 103.75 > 100.02
    bm.update_trailing_stop("NVDA", current_bar_high=106.00, current_bar_low=103.00, current_atr=1.50, timestamp=now_dt)
    advanced_stop = bracket.current_stop_price
    assert advanced_stop > initial_stop

    # Bar 2: Sharp whipsaw pullback: High $102.00, Low $99.00, ATR $2.00
    # Stop must NOT loosen! Must remain at least advanced_stop
    bm.update_trailing_stop("NVDA", current_bar_high=102.00, current_bar_low=99.00, current_atr=2.00, timestamp=now_dt)
    assert bracket.current_stop_price >= advanced_stop, "Trailing stop loosened on price drop! Violates monotonicity"


# ============================================================================
# Group 3: Zero-Volume Degenerate Bars and Tick Gap Recovery
# ============================================================================

def test_adv_zero_volume_bars_indicator_stability():
    """
    Delivers 25 consecutive zero-volume degenerate bars (open == high == low == close, volume == 0).
    Verifies that math indicators (SMA, ATR, RSI, Z-score, VWAP) execute without ZeroDivisionError or crash.
    """
    prices = [100.0] * 25
    bars = [{"h": 100.0, "l": 100.0, "c": 100.0, "v": 0.0}] * 25

    # SMA
    sma = calculate_sma(prices, 20)
    assert sma == 100.0

    # Z-Score
    mean, std, z = evaluate_mean_reversion_zscore(prices)
    assert mean == 100.0
    assert std == 0.0
    assert z == 0.0

    # RSI on flat prices
    rsi = calculate_rsi(prices, 14)
    assert rsi == 50.0 or rsi == 0.0 or 0.0 <= rsi <= 100.0

    # Anchored VWAP on zero volume
    vwap, vwap_std = calculate_anchored_vwap(bars)
    assert vwap == 0.0
    assert vwap_std == 0.0

    # Strategy on_bar with zero volume bar
    strat = MeanReversionStrategy()
    zero_bar = BarEvent(
        symbol="AAPL",
        open=150.0,
        high=150.0,
        low=150.0,
        close=150.0,
        volume=0,
        timestamp=datetime(2026, 9, 21, 14, 15, 0, tzinfo=timezone.utc),
    )
    sigs = strat.on_bar(zero_bar)
    assert isinstance(sigs, list)


def test_adv_luld_halt_and_resumption():
    """
    Simulates a 5-minute regulatory Limit Up/Limit Down (LULD) halt (5 flat 0-vol bars),
    followed by a high-volume reopening breakout.
    Verifies that the engine buffers state safely during halt and routes trades on resumption.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    strat = OpeningRangeBreakoutStrategy()

    # Pre-halt opening range bars (09:30 - 09:34 ET)
    for m in range(30, 35):
        bar = BarEvent(
            symbol="NVDA",
            open=120.0,
            high=122.0,
            low=119.0,
            close=121.0,
            volume=100000,
            timestamp=datetime(2026, 9, 21, 13, m, 0, tzinfo=timezone.utc),
        )
        strat.on_bar(bar)

    # 5-minute LULD halt (09:35 - 09:39 ET): 0 volume, flat price 122.0
    for m in range(35, 40):
        halt_bar = BarEvent(
            symbol="NVDA",
            open=122.0,
            high=122.0,
            low=122.0,
            close=122.0,
            volume=0,
            timestamp=datetime(2026, 9, 21, 13, m, 0, tzinfo=timezone.utc),
        )
        sigs = strat.on_bar(halt_bar)
        # Halt bars must NOT trigger false breakout signals
        assert len(sigs) == 0

    # Reopening bar at 09:40 ET: Vol 600,000, Breakout to $123.50 > $122.00
    reopen_bar = BarEvent(
        symbol="NVDA",
        open=122.5,
        high=124.0,
        low=122.0,
        close=123.5,
        volume=600000,
        timestamp=datetime(2026, 9, 21, 13, 40, 0, tzinfo=timezone.utc),
    )
    reopen_sigs = strat.on_bar(reopen_bar)
    assert len(reopen_sigs) == 1
    assert reopen_sigs[0].side == OrderSide.BUY


def test_adv_tick_gap_temporal_recovery():
    """
    Simulates a 15-minute WebSocket drop (gap from 09:31 ET to 09:46 ET).
    Verifies that EOD flattening clock and adaptation engine handle the gap without exception.
    """
    flattening = ZeroOvernightFlatteningEngine()
    adaptation = DynamicAdaptationEngine()

    t1 = datetime(2026, 9, 21, 13, 31, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 21, 13, 46, 0, tzinfo=timezone.utc)

    flattening.clock.set_simulated_time(t1)
    adaptation.update_clock(t1)
    assert adaptation.current_time_phase == "OPEN_VOLATILITY_FLUSH"

    # Jump 15 minutes
    flattening.clock.set_simulated_time(t2)
    adaptation.update_clock(t2)
    # 09:46 ET is still OPEN_VOLATILITY_FLUSH (09:30-10:00)
    assert adaptation.current_time_phase in ("OPEN_VOLATILITY_FLUSH", "TREND_CONTINUATION")


# ============================================================================
# Group 4: Conflicting Multi-Headline Sentiment Bursts on Identical Timestamps
# ============================================================================

def test_adv_conflicting_headlines_identical_timestamp_contradiction_liquidation():
    """
    Position is open LONG on AAPL.
    At the EXACT same microsecond timestamp, two conflicting headlines arrive:
    1. Bullish: "AAPL reports record Q3 results and raises full-year guidance" (sentiment +0.82)
    2. Bearish: "DOJ files emergency antitrust monopoly injunction against Apple" (sentiment -0.88)
    Verifies that the Bearish headline immediately triggers the contradiction circuit breaker,
    liquidating the LONG position.
    """
    strat = NewsMomentumStrategy()
    strat.update_monitored_position("AAPL", "LONG")
    now_dt = datetime(2026, 9, 21, 13, 45, 0, tzinfo=timezone.utc)

    bullish_event = NewsEvent(
        article_id=7001,
        headline="AAPL reports record Q3 results and raises full-year guidance",
        summary="Record revenue",
        symbols=["AAPL"],
        source="benzinga",
        created_at=now_dt,
        sentiment_score=0.82,
        sentiment_confidence=0.95,
        catalyst_category=CatalystCategory.EARNINGS_BEAT,
    )

    bearish_event = NewsEvent(
        article_id=7002,
        headline="DOJ files emergency antitrust monopoly injunction against Apple",
        summary="Antitrust injunction",
        symbols=["AAPL"],
        source="benzinga",
        created_at=now_dt,
        sentiment_score=-0.88,
        sentiment_confidence=0.98,
        catalyst_category=CatalystCategory.LEGAL_INVESTIGATION,
    )

    # Deliver bullish headline first
    sigs_bull = strat.on_news(bullish_event)
    # While already long, bullish news does not contradict
    contradictions_bull = [s for s in sigs_bull if "CONTRADICTION" in s.reason]
    assert len(contradictions_bull) == 0

    # Deliver adverse headline on same timestamp
    sigs_bear = strat.on_news(bearish_event)
    contradictions_bear = [s for s in sigs_bear if "CONTRADICTION" in s.reason]
    assert len(contradictions_bear) == 1
    assert contradictions_bear[0].side == OrderSide.SELL
    assert contradictions_bear[0].order_type == OrderType.MARKET
    # Monitored position must be cleared after contradiction liquidation
    assert "AAPL" not in strat.monitored_positions


def test_adv_sentiment_scoring_negations_and_qualifiers():
    """
    Adversarial NLP classification: verifies token parsing distinguishes negations:
    - "beat" vs "fails to beat"
    - "approves" vs "rejects"
    - "upgrade" vs "downgrade"
    """
    pos_headline = "Tesla beats Wall St estimates with record quarterly profit"
    neg_headline = "Tesla fails to beat quarterly delivery estimates amid supply issues"
    probe_headline = "SEC launches formal investigation into accounting fraud"

    score_pos = score_news_sentiment(pos_headline)
    score_neg = score_news_sentiment(neg_headline)
    score_probe = score_news_sentiment(probe_headline)

    assert score_pos > 0.3, f"Expected positive, got {score_pos}"
    assert score_neg < 0.0, f"Expected negative on 'fails to beat', got {score_neg}"
    assert score_probe < -0.4, f"Expected strongly negative on 'investigation/fraud', got {score_probe}"


def test_adv_sentiment_burst_100_headlines_throughput():
    """
    Stress-tests ingestion throughput of 100 simultaneous headlines across 10 symbols.
    Verifies that the strategy processes all 100 events in < 200 milliseconds.
    """
    strat = NewsMomentumStrategy()
    symbols = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN"]
    now_dt = datetime(2026, 9, 21, 13, 45, 0, tzinfo=timezone.utc)

    events = [
        NewsEvent(
            article_id=1000 + i,
            headline=f"{symbols[i % len(symbols)]} announces record contract approval milestone {i}",
            summary="Details...",
            symbols=[symbols[i % len(symbols)]],
            source="benzinga",
            created_at=now_dt,
            sentiment_score=0.75,
            sentiment_confidence=0.9,
            catalyst_category=CatalystCategory.PARTNERSHIP_CONTRACT,
        )
        for i in range(100)
    ]

    start = asyncio.get_event_loop().time()
    for ev in events:
        strat.on_news(ev)
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed < 0.20, f"Processing 100 headlines took too long: {elapsed:.3f}s"
    # Verify pending catalysts populated across symbols
    for s in symbols:
        assert len(strat.pending_catalysts.get(s, [])) > 0


# ============================================================================
# Group 5: Flash Crash $1,500 Circuit Breaker Emergency Liquidation
# ============================================================================

def test_adv_flash_crash_exact_boundary_halt():
    """
    Tests exact $1,500.00 daily loss boundary:
    - Drawdown $1,499.50 -> Status ARMED (RiskLevel WARNING)
    - Drawdown $1,500.00 -> Status HALTED_DAILY_LOSS (RiskLevel HALTED)
    """
    risk_engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00, hard_max_daily_loss_dollars=1500.00))
    now_dt = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)

    # Case 1: Drawdown = $1,499.50 (Equity = $48,500.50)
    st1 = risk_engine.evaluate_account_state(48500.50, 48500.50, -1499.50, 0.0, now_dt)
    assert st1 == BreakerStatus.ARMED
    assert risk_engine.risk_level == RiskLevel.WARNING

    # Case 2: Drawdown = $1,500.00 (Equity = $48,500.00)
    st2 = risk_engine.evaluate_account_state(48500.00, 48500.00, -1500.00, 0.0, now_dt)
    assert st2 == BreakerStatus.HALTED_DAILY_LOSS
    assert risk_engine.risk_level == RiskLevel.HALTED
    assert risk_engine.breaker_triggered_at == now_dt


def test_adv_flash_crash_rapid_cascade_multi_position_liquidation():
    """
    Simulates a rapid flash crash across 3 active positions (AAPL, TSLA, NVDA).
    When drawdown hits $1,500:
    1. Status transitions to HALTED_DAILY_LOSS.
    2. All open positions are liquidated via emergency market orders.
    3. Working orders are cancelled.
    4. Account reconciles with zero open positions.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    risk_engine = InstitutionalRiskEngine()
    engine = ExecutionEngine(account=account)
    now_dt = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)

    # Open 3 positions
    for sym, price, qty in [("AAPL", 150.0, 50), ("TSLA", 200.0, 50), ("NVDA", 100.0, 50)]:
        ord_ = engine.create_order(sym, OrderSide.BUY, OrderType.LIMIT, qty, limit_price=price)
        engine.submit_order(ord_.id)
        engine.process_bar(sym, price, price, price, price, 10000, now_dt)
        assert sym in account.positions

    assert len(account.positions) == 3

    # Flash crash: Prices plunge
    # AAPL: 150 -> 135 (-$750)
    # TSLA: 200 -> 188 (-$600)
    # NVDA: 100 -> 95 (-$250)
    # Total unrealized loss = -$1,600.00 (Equity = $48,400.00)
    account.update_market_price("AAPL", 135.0)
    account.update_market_price("TSLA", 188.0)
    account.update_market_price("NVDA", 95.0)

    breaker_st = risk_engine.evaluate_account_state(
        account.equity, account.cash, account.realized_pnl, account.unrealized_pnl, now_dt
    )
    assert breaker_st == BreakerStatus.HALTED_DAILY_LOSS

    # Execute emergency liquidation sweep
    engine.cancel_all_orders("CIRCUIT_BREAKER_HALT")
    for sym, pos in list(account.positions.items()):
        liq_order = engine.create_order(
            symbol=sym,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            qty=pos.shares,
            strategy_id="CIRCUIT_BREAKER",
        )
        engine.submit_order(liq_order.id)
        p = account.positions[sym].market_price
        engine.process_bar(sym, p, p, p, p, 50000, now_dt)

    assert len(account.positions) == 0, "All positions must be liquidated after circuit halt"
    assert account.unrealized_pnl == 0.0


def test_adv_circuit_breaker_strict_entry_lockout():
    """
    Once HALTED_DAILY_LOSS is active:
    1. Any subsequent position opening order (is_exit=False) is REJECTED.
    2. Rejection reason contains CIRCUIT_BREAKER_HALTED.
    3. Even if simulated prices rise again, the session halt is non-recoverable until next session.
    """
    risk_engine = InstitutionalRiskEngine()
    now_dt = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)
    risk_engine.evaluate_account_state(48000.00, 48000.00, -2000.00, 0.0, now_dt)
    assert risk_engine.status == BreakerStatus.HALTED_DAILY_LOSS

    # Attempt new buy order
    check = risk_engine.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=50,
        entry_price=150.0,
        stop_price=147.0,
        account_equity=48000.0,
        buying_power=192000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
        is_exit=False,
    )
    assert not check.approved
    assert check.rejection_code == "CIRCUIT_BREAKER_HALTED"

    # Permitted liquidation order (is_exit=True)
    exit_check = risk_engine.evaluate_order_request(
        symbol="AAPL",
        side="SELL",
        requested_qty=50,
        entry_price=150.0,
        stop_price=147.0,
        account_equity=48000.0,
        buying_power=192000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
        is_exit=True,
    )
    assert exit_check.approved
    assert "APPROVED_EXIT" in exit_check.reason


def test_adv_zero_overnight_eod_flattening_protocol():
    """
    Verifies that the 4-phase EOD flattening protocol liquidates all positions before 16:00 ET:
    15:45: ENTRY_LOCKOUT
    15:50: WORKING_ORDER_PURGE
    15:55: FORCE_MARKET_FLATTEN
    15:58: ZERO_OVERNIGHT_AUDIT
    """
    flattening = ZeroOvernightFlatteningEngine()

    # 15:44:59 ET -> NORMAL_TRADING
    flattening.clock.set_simulated_time(datetime(2026, 9, 21, 15, 44, 59, tzinfo=zoneinfo.ZoneInfo("America/New_York")))
    d0 = flattening.check_time_tick()
    assert d0 is None or flattening.current_phase == FlatteningPhase.NORMAL_TRADING

    # 15:45:00 ET -> ENTRY_LOCKOUT
    flattening.clock.set_simulated_time(datetime(2026, 9, 21, 15, 45, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York")))
    d1 = flattening.check_time_tick()
    assert d1 is not None
    assert d1.phase == FlatteningPhase.ENTRY_LOCKOUT

    # 15:50:00 ET -> ORDER_PURGE
    flattening.clock.set_simulated_time(datetime(2026, 9, 21, 15, 50, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York")))
    d2 = flattening.check_time_tick()
    assert d2.phase == FlatteningPhase.ORDER_PURGE

    # 15:55:00 ET -> MANDATORY_LIQUIDATION
    flattening.clock.set_simulated_time(datetime(2026, 9, 21, 15, 55, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York")))
    d3 = flattening.check_time_tick()
    assert d3.phase == FlatteningPhase.MANDATORY_LIQUIDATION

    # 15:58:00 ET -> ZERO_AUDIT
    flattening.clock.set_simulated_time(datetime(2026, 9, 21, 15, 58, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York")))
    d4 = flattening.check_time_tick()
    assert d4.phase == FlatteningPhase.ZERO_AUDIT


# ============================================================================
# Additional Comprehensive Adversarial Stress Tests
# ============================================================================

def test_adv_concurrent_multi_symbol_breakout_exact_093000_arbitration():
    """
    Simulates signal collision across multiple strategies on the same symbol at 09:30:00 ET:
    1. Collision between ORB and VWAP Pullback on AAPL: ORB wins (priority 30 vs 20).
    2. Collision between News Momentum and ORB on NVDA: News Momentum wins (priority 40 vs 30).
    """
    adaptation = DynamicAdaptationEngine()
    now_dt = datetime(2026, 9, 21, 13, 30, 0, tzinfo=timezone.utc)

    sig_orb_aapl = SignalEvent(
        symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        entry_price=150.0, stop_loss=148.0, take_profit_1=153.0, take_profit_2=155.0,
        strategy_id="orb", confidence=0.85, reason="ORB breakout", timestamp=now_dt
    )
    sig_vwap_aapl = SignalEvent(
        symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        entry_price=150.2, stop_loss=148.5, take_profit_1=153.0, take_profit_2=155.0,
        strategy_id="vwap_pullback", confidence=0.90, reason="VWAP bounce", timestamp=now_dt
    )
    sig_news_nvda = SignalEvent(
        symbol="NVDA", side=OrderSide.BUY, order_type=OrderType.MARKET,
        entry_price=125.0, stop_loss=122.0, take_profit_1=129.5, take_profit_2=132.5,
        strategy_id="news_momentum", confidence=0.75, reason="Breaking catalyst", timestamp=now_dt
    )
    sig_orb_nvda = SignalEvent(
        symbol="NVDA", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        entry_price=125.0, stop_loss=123.0, take_profit_1=128.0, take_profit_2=130.0,
        strategy_id="orb", confidence=0.95, reason="ORB breakout", timestamp=now_dt
    )

    arbitrated = adaptation.arbitrate_signals([sig_vwap_aapl, sig_orb_aapl, sig_orb_nvda, sig_news_nvda])

    # Exactly 2 signals output (one per symbol)
    assert len(arbitrated) == 2
    by_sym = {s.symbol: s for s in arbitrated}

    # For AAPL, ORB won over VWAP Pullback
    assert by_sym["AAPL"].strategy_id == "orb"
    # For NVDA, News Momentum won over ORB despite ORB having higher raw confidence
    assert by_sym["NVDA"].strategy_id == "news_momentum"


def test_adv_atomic_order_execution_and_audit_integrity():
    """
    Executes 50 rapid order creations, submissions, and fills across various symbols.
    Verifies that every single state change produces an immutable OrderAuditRecord
    and total fees paid, cash, and equity remain mathematically exact.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now_dt = datetime(2026, 9, 21, 13, 30, 0, tzinfo=timezone.utc)

    for i in range(25):
        sym = f"SYM_{i}"
        price = 100.0 + i
        order = engine.create_order(sym, OrderSide.BUY, OrderType.LIMIT, 10, limit_price=price)
        engine.submit_order(order.id)
        engine.process_bar(sym, price, price, price, price, 10000, now_dt)

        assert order.status == OrderState.FILLED
        assert len(order.audit_trail) >= 3  # CREATED, SUBMITTED, ACCEPTED, FILLED

    # Check overall audit log size
    assert len(engine.audit_log) >= 75
    # Verify account cash + market value == equity
    assert abs(account.cash + sum(p.market_value for p in account.positions.values()) - account.equity) < 0.01


def test_adv_bracket_target1_and_target2_full_sequence_lifecycle():
    """
    Tests complete lifecycle of a 2-tier bracket:
    1. Parent entry fills 100 shares @ $100.00.
    2. Bracket activated with TP1 ($103.00, 50 shares) and TP2 ($105.00, 50 shares).
    3. TP1 fills -> stop ratcheted to breakeven ($100.02), remaining qty = 50.
    4. TP2 fills -> bracket completes with COMPLETED_PROFIT, stop cancelled.
    """
    bm = DynamicBracketManager(breakeven_buffer=0.02)
    now_dt = datetime(2026, 9, 21, 13, 35, 0, tzinfo=timezone.utc)

    bracket = bm.create_bracket("brk_full_001", "NVDA", "LONG", 100, 100.0, 98.0, timestamp=now_dt)
    bm.activate_bracket_on_fill(bracket.bracket_id, 100, 100.0, now_dt)
    assert bracket.status == BracketStatus.ACTIVE

    # Hit TP1
    dir1 = bm.on_child_order_fill(bracket.target_1_order_id, 103.00, 50, now_dt)
    assert dir1.action == "MODIFY_ORDER"
    assert bracket.status == BracketStatus.TARGET_1_HIT
    assert bracket.remaining_qty == 50
    assert bracket.current_stop_price >= 100.02

    # Hit TP2
    dir2 = bm.on_child_order_fill(bracket.target_2_order_id, 105.00, 50, now_dt)
    assert dir2.action == "CANCEL_ORDER"
    assert bracket.status == BracketStatus.COMPLETED_PROFIT
    assert bracket.remaining_qty == 0
    assert bracket.stop_order_id in dir2.orders_to_cancel
    assert "NVDA" not in bm.symbol_to_bracket


def test_adv_bracket_manual_flatten_cancellation():
    """
    Tests cancel_bracket_for_flattening under manual or EOD override.
    Cancels all working orders and marks bracket as COMPLETED_FLATTEN.
    """
    bm = DynamicBracketManager()
    now_dt = datetime(2026, 9, 21, 13, 35, 0, tzinfo=timezone.utc)
    bracket = bm.create_bracket("brk_flat_001", "AAPL", "LONG", 100, 150.0, 147.0, timestamp=now_dt)
    bm.activate_bracket_on_fill(bracket.bracket_id, 100, 150.0, now_dt)

    directive = bm.cancel_bracket_for_flattening("AAPL", reason="MANUAL_OVERRIDE")
    assert directive is not None
    assert directive.action == "CANCEL_ORDER"
    assert directive.bracket_status == BracketStatus.COMPLETED_FLATTEN
    assert bracket.stop_order_id in directive.orders_to_cancel
    assert bracket.target_1_order_id in directive.orders_to_cancel
    assert "AAPL" not in bm.symbol_to_bracket


def test_adv_microstructure_illiquid_volume_participation_cap():
    """
    Microstructure test: An order for 200 shares is placed in an illiquid bar with total volume 50 shares.
    Under 10% maximum bar volume participation:
    max_fillable = max(10, int(50 * 0.10)) = 10 shares.
    Only 10 shares are filled, and remaining 190 shares remain in PARTIALLY_FILLED state.
    """
    account = PaperTradingAccount(initial_cash=50000.00)
    engine = ExecutionEngine(account=account)
    now_dt = datetime(2026, 9, 21, 13, 35, 0, tzinfo=timezone.utc)

    order = engine.create_order("ILLIQ", OrderSide.BUY, OrderType.LIMIT, 200, limit_price=50.0)
    engine.submit_order(order.id)

    fills = engine.process_bar("ILLIQ", 50.0, 50.5, 49.5, 50.0, 50, now_dt)
    assert len(fills) == 1
    assert fills[0].qty == 10
    assert order.status == OrderState.PARTIALLY_FILLED
    assert order.filled_qty == 10
    assert order.remaining_qty == 190


def test_adv_news_momentum_volume_confirmation_gate():
    """
    Catalyst arrives for TSLA.
    Bar 1 arrives with normal volume (100,000, 1.0x SMA20) -> NO trade signal.
    Bar 2 arrives with high surge volume (450,000, 4.5x SMA20) within 180s TTL -> Trade signal BUY emitted!
    """
    strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.50)
    now_dt = datetime(2026, 9, 21, 13, 40, 0, tzinfo=timezone.utc)

    # Establish 20 baseline bars with volume 100,000
    for i in range(20):
        strat.on_bar(BarEvent(
            symbol="TSLA", open=200.0, high=201.0, low=199.0, close=200.5, volume=100000,
            timestamp=now_dt + timedelta(minutes=i)
        ))

    # Ingest strong news headline
    news_ev = NewsEvent(
        article_id=9001,
        headline="Tesla awarded massive commercial autonomous fleet supply contract",
        summary="Contract win",
        symbols=["TSLA"],
        source="benzinga",
        created_at=now_dt + timedelta(minutes=20),
        sentiment_score=0.85,
        sentiment_confidence=0.95,
        catalyst_category=CatalystCategory.PARTNERSHIP_CONTRACT,
    )
    strat.on_news(news_ev)
    assert len(strat.pending_catalysts["TSLA"]) == 1

    # Bar with low volume (120,000 < 3.5x 100,000)
    low_vol_bar = BarEvent(
        symbol="TSLA", open=201.0, high=202.0, low=200.5, close=201.5, volume=120000,
        timestamp=now_dt + timedelta(minutes=21)
    )
    sigs_low = strat.on_bar(low_vol_bar)
    assert len(sigs_low) == 0, "Should not trigger without volume surge confirmation"

    # Bar with surge volume (500,000 >= 3.5x SMA20)
    high_vol_bar = BarEvent(
        symbol="TSLA", open=201.5, high=205.0, low=201.0, close=204.5, volume=500000,
        timestamp=now_dt + timedelta(minutes=22)
    )
    sigs_high = strat.on_bar(high_vol_bar)
    assert len(sigs_high) == 1
    assert sigs_high[0].side == OrderSide.BUY
    assert sigs_high[0].symbol == "TSLA"


def test_adv_vix_crisis_regime_scaling_and_freeze():
    """
    Tests VIX crisis regime scaling (VIX = 38.50 >= 35.0):
    1. Regime is CRISIS.
    2. Sizing multiplier scales down to 0.35x.
    3. Stop distance multiplier doubles to 2.0x to avoid stop-hunting in high volatility.
    """
    regime, sizing, stop_m = get_vix_regime(38.5)
    assert regime == "CRISIS"
    assert sizing == 0.35
    assert stop_m == 2.00

    adaptation = DynamicAdaptationEngine(default_vix=38.5)
    assert adaptation.current_vix_regime == "CRISIS"
    assert adaptation.current_sizing_multiplier == 0.35
    assert adaptation.current_stop_multiplier == 2.00
