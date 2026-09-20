"""backend/tests/unit/test_empirical_stress_m2_2.py
Empirical Stress Test Suite for Milestone 2 (strategies_adaptation).
Verified by: challenger_m2_2 (volatility and session phase adversarial verifier).

Adversarially challenges:
1. Rapid VIX regime jumps (14.5 -> 38.0 Crisis spike):
   - Immediate risk budget contraction (1.20x -> 0.35x).
   - Stop widening verification under stop_multiplier scaling (Defect surfaced: stop_multiplier is calculated but never applied).
2. Time-of-day boundary transitions:
   - Pre-market (< 09:30 ET) blocks new breakout orders.
   - Open flush (09:30-10:00 ET) accumulates opening bars and establishes ORB levels.
   - Midday chop (11:30-14:00 ET) defense against trend continuation entries (Defect surfaced: VWAP Pullback is permitted in chop).
   - Power hour strict entry lockout past 15:45 ET across all strategies.
3. High-frequency VIX whipsaw resilience & boundary stability.
4. Process hygiene and port liberation.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timezone
import math
import socket
from typing import List
from zoneinfo import ZoneInfo
import pytest

from backend.app.models.events import BarEvent, OrderSide, OrderType, VixPrint, VixRegime
from backend.app.strategies.adaptation import (
    DynamicAdaptationEngine,
    TimeOfDayPhase,
    calculate_position_size,
    get_time_of_day_phase,
    get_vix_regime,
)
from backend.app.strategies.base import SignalEvent
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy
from backend.app.strategies.mean_reversion import MeanReversionStrategy

ET = ZoneInfo("America/New_York")


def make_vix_print(val: float) -> VixPrint:
    now = datetime.now(timezone.utc)
    regime = VixRegime.LOW if val < 15 else (VixRegime.NORMAL if val < 25 else (VixRegime.ELEVATED if val < 35 else VixRegime.CRISIS))
    return VixPrint(
        value=val,
        asof=now,
        received_at=now,
        age_s=0.5,
        state="ready",
        upstream="connected",
        regime=regime,
        sizing_multiplier=1.0,
    )


# ============================================================================
# 1. RAPID VIX REGIME JUMPS & SIZING CONTRACTION
# ============================================================================

def test_rapid_vix_jump_risk_budget_contraction():
    """Verify that a sudden VIX spike from 14.5 (Low) to 38.0 (Crisis) causes
    immediate risk budget contraction and position size reduction (>70%).
    """
    engine = DynamicAdaptationEngine(default_vix=14.5)
    equity = 50000.00
    entry_price = 50.0
    stop_price = 45.0  # $5.00 stop distance

    # At VIX 14.5 (LOW regime)
    assert engine.current_vix_regime == "LOW"
    assert engine.current_sizing_multiplier == 1.20
    assert engine.current_stop_multiplier == 0.85

    shares_low = engine.calculate_adapted_size(
        equity=equity,
        entry_price=entry_price,
        stop_loss_price=stop_price,
    )
    # Expected: $50,000 * 0.01 * 1.20 = $600 risk budget / $5 stop = 120 shares
    assert shares_low == 120

    # Rapid Crisis Spike to 38.0
    vprint = make_vix_print(38.0)
    engine.on_vix_print(vprint)

    assert engine.current_vix_regime == "CRISIS"
    assert engine.current_sizing_multiplier == 0.35
    assert engine.current_stop_multiplier == 2.00

    shares_crisis = engine.calculate_adapted_size(
        equity=equity,
        entry_price=entry_price,
        stop_loss_price=stop_price,
    )
    # Expected: $50,000 * 0.01 * 0.35 = $175 risk budget / $5 stop = 35 shares
    assert shares_crisis == 35

    # Contraction factor is strictly 35 / 120 = 29.17% of initial size (70.83% risk reduction)
    contraction_ratio = shares_crisis / shares_low
    assert contraction_ratio <= 0.30


def test_defect_vix_stop_widening_is_remediated():
    """Verify that invariant dollar risk stop widening by stop_multiplier is implemented
    (0.85x in Low, 2.00x in Crisis).
    """
    engine = DynamicAdaptationEngine(default_vix=14.5)
    
    # Rapid Crisis Spike to 38.0
    vprint = make_vix_print(38.0)
    engine.on_vix_print(vprint)
    assert engine.current_stop_multiplier == 2.00

    raw_signal = SignalEvent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=100.0,
        stop_loss=98.0,  # Raw stop distance = $2.00
        take_profit_1=103.0,
        take_profit_2=105.0,
        strategy_id="orb",
        confidence=0.8,
        reason="ORB_LONG",
    )

    # Admission check
    approved, reason, qty = engine.evaluate_signal_admission(
        signal=raw_signal,
        equity=50000.0,
        current_positions_count=0,
        is_symbol_active=False,
    )
    assert approved is True

    # Engine provides adapted stop loss method that widens by 2.0x
    assert hasattr(engine, "calculate_adapted_stop")
    adapted_stop = engine.calculate_adapted_stop(raw_signal)
    assert adapted_stop == 96.0


def test_oracle_crisis_vix_must_widen_stop_loss():
    """ORACLE REQUIREMENT: Under Crisis VIX (stop_multiplier = 2.00),

    an entry at $100 with raw stop $98 (distance $2.00) must have its stop widened
    by 2.00x to $96.00 (distance $4.00) to maintain invariant dollar risk.
    """
    engine = DynamicAdaptationEngine(default_vix=38.0)
    assert engine.current_stop_multiplier == 2.00

    raw_signal = SignalEvent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit_1=103.0,
        take_profit_2=105.0,
        strategy_id="orb",
        confidence=0.8,
        reason="ORB_LONG",
    )

    assert hasattr(engine, "calculate_adapted_stop")
    adapted_stop = engine.calculate_adapted_stop(raw_signal)
    assert adapted_stop == 96.0


# ============================================================================
# 2. TIME-OF-DAY BOUNDARY TRANSITIONS
# ============================================================================

def test_premarket_blocks_new_breakout_orders():
    """Verify that during PRE_MARKET (< 09:30 ET):

    1. ORB strategy discards pre-market bars and generates 0 signals.
    2. DynamicAdaptationEngine rejects ORB signals via PHASE_GATE_DENIED.
    """
    engine = DynamicAdaptationEngine()
    pre_market_dt = datetime(2026, 9, 21, 9, 15, 0, tzinfo=ET)
    phase = engine.update_clock(pre_market_dt)
    assert phase == "PRE_MARKET"
    assert engine.market_status == "CLOSED"

    # 1. ORB strategy on pre-market bar
    orb = OpeningRangeBreakoutStrategy()
    pre_bar = BarEvent(
        symbol="AAPL",
        open=150.0,
        high=152.0,
        low=149.0,
        close=151.5,
        volume=250000,
        timestamp=pre_market_dt,
    )
    signals = orb.on_bar(pre_bar)
    assert len(signals) == 0  # No breakout orders in pre-market

    # 2. DynamicAdaptationEngine phase gate denial
    test_sig = SignalEvent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=151.5,
        stop_loss=149.0,
        take_profit_1=154.0,
        take_profit_2=156.0,
        strategy_id="orb",
        confidence=0.85,
        reason="PREMARKET_BREAKOUT_TEST",
        timestamp=pre_market_dt,
    )
    approved, reason, qty = engine.evaluate_signal_admission(
        signal=test_sig,
        equity=50000.0,
        current_positions_count=0,
        is_symbol_active=False,
    )
    assert approved is False
    assert "PHASE_GATE_DENIED" in reason
    assert qty == 0


def test_open_flush_allows_orb_establishment_and_breakout():
    """Verify that during OPEN_VOLATILITY_FLUSH (09:30 - 10:00 ET):

    1. Bars between 09:30 and 09:35 form the 5m opening range.
    2. Range is established at 09:35 with exact high, low, and midpoint.
    3. Adaptation engine permits ORB strategy during OPEN_VOLATILITY_FLUSH.
    4. Breakout above range_high on RVOL >= 1.8 fires valid SignalEvent.
    """
    engine = DynamicAdaptationEngine()
    orb = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)

    # Establish baseline volume
    orb.set_baseline_volume("AAPL", 50000.0)

    # Feed 5 opening bars (09:30 to 09:34)
    bars_data = [
        (150.0, 151.0, 149.5, 150.5, 20000),  # 09:30
        (150.5, 151.5, 150.0, 151.0, 22000),  # 09:31
        (151.0, 152.0, 150.8, 151.8, 25000),  # 09:32
        (151.8, 152.5, 151.2, 152.0, 18000),  # 09:33
        (152.0, 152.2, 151.5, 151.9, 19000),  # 09:34
    ]

    for i, (o, h, l, c, v) in enumerate(bars_data):
        dt = datetime(2026, 9, 21, 9, 30 + i, 0, tzinfo=ET)
        engine.update_clock(dt)
        assert engine.current_time_phase == "OPEN_VOLATILITY_FLUSH"
        b = BarEvent(symbol="AAPL", open=o, high=h, low=l, close=c, volume=v, timestamp=dt)
        sigs = orb.on_bar(b)
        assert len(sigs) == 0  # Still forming range

    # At 09:35 (bar 6), range is established
    breakout_bar_dt = datetime(2026, 9, 21, 9, 35, 0, tzinfo=ET)
    engine.update_clock(breakout_bar_dt)
    assert engine.current_time_phase == "OPEN_VOLATILITY_FLUSH"
    assert engine.is_strategy_permitted("orb") is True

    # High of bars is 152.5, low is 149.5, midpoint = 151.0
    breakout_bar = BarEvent(
        symbol="AAPL",
        open=152.0,
        high=153.2,
        low=151.9,
        close=153.0,  # Closes > 152.5 range high!
        volume=60000,  # RVOL = 60000 / avg(recent) >= 1.8x
        timestamp=breakout_bar_dt,
    )
    sigs = orb.on_bar(breakout_bar)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.side == OrderSide.BUY
    assert sig.entry_price == 153.0
    assert sig.stop_loss == 151.0  # Midpoint stop
    assert sig.strategy_id == "orb"

    # Verify admission by DynamicAdaptationEngine
    appr, reason, qty = engine.evaluate_signal_admission(sig, 50000.0, 0, False)
    assert appr is True
    assert qty > 0


def test_defect_midday_chop_blocks_trend_continuation():
    """Verify that Midday chop defense (11:30 - 14:00 ET) blocks trend continuation entries."""
    engine = DynamicAdaptationEngine()
    midday_dt = datetime(2026, 9, 21, 12, 30, 0, tzinfo=ET)
    phase = engine.update_clock(midday_dt)
    assert phase == "MIDDAY_CHOP"

    # ORB is correctly blocked
    assert engine.is_strategy_permitted("orb", "MIDDAY_CHOP") is False

    # VWAP Pullback (Trend Continuation) is blocked during chop
    assert engine.is_strategy_permitted("vwap_pullback", "MIDDAY_CHOP") is False


def test_oracle_midday_chop_must_block_trend_continuation():
    """ORACLE REQUIREMENT: Midday chop defense must strictly block trend continuation entries
    (Strategy 2: VWAP Trend Pullback & Continuation) to prevent whipsaw losses.
    """
    engine = DynamicAdaptationEngine()
    midday_dt = datetime(2026, 9, 21, 12, 30, 0, tzinfo=ET)
    engine.update_clock(midday_dt)

    assert engine.is_strategy_permitted("vwap_pullback", "MIDDAY_CHOP") is False


def test_midday_chop_sizing_moderation():
    """Verify that if an allowed strategy executes in MIDDAY_CHOP, a 50% sizing penalty is applied."""
    engine = DynamicAdaptationEngine()
    midday_dt = datetime(2026, 9, 21, 12, 30, 0, tzinfo=ET)
    engine.update_clock(midday_dt)

    size_chop = engine.calculate_adapted_size(50000.0, 50.0, 48.0)
    
    engine.current_time_phase = "TREND_CONTINUATION"
    size_trend = engine.calculate_adapted_size(50000.0, 50.0, 48.0)

    assert size_chop == size_trend // 2


def test_power_hour_boundary_and_strict_1545_lockout():
    """Verify that:

    1. During POWER_HOUR (15:00 - 15:45 ET), ORB is blocked, while late-session entries have access.
    2. At exactly 15:45:00 ET (EOD_FLATTEN), ALL new entries across ALL 4 strategies are strictly rejected.
    3. At 15:45:01 ET and beyond, evaluate_signal_admission returns False with PHASE_GATE_DENIED.
    """
    engine = DynamicAdaptationEngine()

    # 1. At 15:30:00 ET (Power Hour)
    t_1530 = datetime(2026, 9, 21, 15, 30, 0, tzinfo=ET)
    phase_1530 = engine.update_clock(t_1530)
    assert phase_1530 == "POWER_HOUR"
    assert engine.is_strategy_permitted("orb") is False
    assert engine.is_strategy_permitted("news_momentum") is True

    # 2. At 15:45:00 ET (EOD_FLATTEN begins)
    t_1545 = datetime(2026, 9, 21, 15, 45, 0, tzinfo=ET)
    phase_1545 = engine.update_clock(t_1545)
    assert phase_1545 == "EOD_FLATTEN"
    assert engine.market_status == "FLATTENING"

    # All strategies must be blocked
    for strat in ["orb", "vwap_pullback", "news_momentum", "mean_reversion"]:
        assert engine.is_strategy_permitted(strat, "EOD_FLATTEN") is False

    # 3. Test admission gate rejection at 15:45:05 ET
    t_1545_past = datetime(2026, 9, 21, 15, 45, 5, tzinfo=ET)
    engine.update_clock(t_1545_past)

    late_signal = SignalEvent(
        symbol="TSLA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=200.0,
        stop_loss=198.0,
        take_profit_1=203.0,
        take_profit_2=205.0,
        strategy_id="news_momentum",
        confidence=0.95,
        reason="LATE_CATALYST",
        timestamp=t_1545_past,
    )

    approved, reason, qty = engine.evaluate_signal_admission(
        signal=late_signal,
        equity=50000.0,
        current_positions_count=0,
        is_symbol_active=False,
    )
    assert approved is False
    assert "PHASE_GATE_DENIED" in reason
    assert qty == 0


def test_mean_reversion_blocked_in_open_flush_permitted_in_midday():
    """Verify Strategy 4 (Mean Reversion) is strictly blocked during OPEN_VOLATILITY_FLUSH

    (09:30-10:00 ET) and permitted during MIDDAY_CHOP.
    """
    engine = DynamicAdaptationEngine()

    # 1. During open flush (09:45 ET)
    t_open = datetime(2026, 9, 21, 9, 45, 0, tzinfo=ET)
    engine.update_clock(t_open)
    assert engine.is_strategy_permitted("mean_reversion") is False

    sig = SignalEvent("AAPL", OrderSide.SELL, OrderType.MARKET, 150.0, 152.0, 147.0, 145.0, "mean_reversion", 0.8, "MR")
    appr, reason, qty = engine.evaluate_signal_admission(sig, 50000.0, 0, False)
    assert appr is False
    assert "PHASE_GATE_DENIED" in reason

    # 2. During midday chop (12:30 ET)
    t_midday = datetime(2026, 9, 21, 12, 30, 0, tzinfo=ET)
    engine.update_clock(t_midday)
    assert engine.is_strategy_permitted("mean_reversion") is True


# ============================================================================
# 3. HIGH-FREQUENCY VIX WHIPSAW & BOUNDARY STABILITY
# ============================================================================

def test_vix_rapid_whipsaw_stress():
    """Stress test engine against extreme oscillating VIX prints."""
    engine = DynamicAdaptationEngine(default_vix=20.0)
    vix_stream = [
        (11.5, "LOW", 1.20, 0.85),
        (37.5, "CRISIS", 0.35, 2.00),
        (14.99, "LOW", 1.20, 0.85),
        (25.01, "ELEVATED", 0.70, 1.40),
        (15.00, "NORMAL", 1.00, 1.00),
        (34.99, "ELEVATED", 0.70, 1.40),
        (35.00, "CRISIS", 0.35, 2.00),
        (85.0, "CRISIS", 0.35, 2.00),
        (8.5, "LOW", 1.20, 0.85),
    ]

    for val, exp_regime, exp_size, exp_stop in vix_stream:
        vp = make_vix_print(val)
        engine.on_vix_print(vp)
        assert engine.current_vix_regime == exp_regime
        assert engine.current_sizing_multiplier == exp_size
        assert engine.current_stop_multiplier == exp_stop

        # Test size calculation does not crash or return negative
        sz = engine.calculate_adapted_size(50000.0, 100.0, 95.0)
        assert sz > 0


# ============================================================================
# 4. PROCESS HYGIENE & PORT LIBERATION
# ============================================================================

def test_process_hygiene_and_port_liberation():
    """Verify ports 8005, 8080, and 3005 are clean and not blocked."""
    def is_port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    for port in [8005, 8080, 3005]:
        assert is_port_free(port) is True, f"Port {port} is occupied by a lingering process!"
