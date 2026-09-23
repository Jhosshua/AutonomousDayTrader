"""Adversarial Empirical Test Harness for MarketTrendFilter & Macro-Aligned Mean Reversion.
Challenger R2-1 Verification Suite.
"""
import os
import sys
import traceback
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datetime import date, datetime, time as dtime, timedelta, timezone
import math
import random
from typing import Tuple
from zoneinfo import ZoneInfo
import pytest

from backend.app.core.market_filter import (
    IndexMetrics,
    IndexState,
    MarketTrend,
    MarketTrendFilter,
    MarketTrendSnapshot,
    _to_utc,
)
from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.adaptation import (
    DynamicAdaptationEngine,
    TimeOfDayPhase,
)
from backend.app.strategies.base import SignalEvent

ET_TZ = ZoneInfo("America/New_York")


def make_bar(
    symbol: str,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: int = 10000,
    minute: int = 30,
    hour: int = 9,
    day: int = 22,
    tz=ET_TZ,
) -> BarEvent:
    dt = datetime(2026, 9, day, hour, minute, 0, tzinfo=tz) if tz else None
    return BarEvent(
        symbol=symbol,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        timestamp=dt,
    )


def setup_bullish_filter() -> Tuple[MarketTrendFilter, datetime]:
    mf = MarketTrendFilter()
    for m in range(30, 36):
        spy_p = 500.0 + (m - 30) * 0.8
        qqq_p = 450.0 + (m - 30) * 1.0
        mf.on_bar(make_bar("SPY", spy_p - 0.2, spy_p + 0.9, spy_p - 0.3, spy_p + 0.7, vol=50000, minute=m))
        mf.on_bar(make_bar("QQQ", qqq_p - 0.2, qqq_p + 1.1, qqq_p - 0.3, qqq_p + 0.9, vol=40000, minute=m))
    asof = datetime(2026, 9, 22, 9, 35, 30, tzinfo=ET_TZ)
    return mf, asof


def setup_bearish_filter() -> Tuple[MarketTrendFilter, datetime]:
    mf = MarketTrendFilter()
    for m in range(30, 36):
        spy_p = 500.0 - (m - 30) * 0.8
        qqq_p = 450.0 - (m - 30) * 1.0
        mf.on_bar(make_bar("SPY", spy_p + 0.2, spy_p + 0.3, spy_p - 0.9, spy_p - 0.7, vol=50000, minute=m))
        mf.on_bar(make_bar("QQQ", qqq_p + 0.2, qqq_p + 0.3, qqq_p - 1.1, qqq_p - 0.9, vol=40000, minute=m))
    asof = datetime(2026, 9, 22, 9, 35, 30, tzinfo=ET_TZ)
    return mf, asof


def setup_neutral_filter() -> Tuple[MarketTrendFilter, datetime]:
    mf = MarketTrendFilter()
    # SPY trending up, QQQ trending down -> divergence -> NEUTRAL
    for m in range(30, 36):
        spy_p = 500.0 + (m - 30) * 0.8
        qqq_p = 450.0 - (m - 30) * 0.8
        mf.on_bar(make_bar("SPY", spy_p - 0.2, spy_p + 0.9, spy_p - 0.3, spy_p + 0.7, vol=50000, minute=m))
        mf.on_bar(make_bar("QQQ", qqq_p + 0.2, qqq_p + 0.3, qqq_p - 0.9, qqq_p - 0.7, vol=40000, minute=m))
    asof = datetime(2026, 9, 22, 9, 35, 30, tzinfo=ET_TZ)
    return mf, asof


# -------------------------------------------------------------
# SECTION 1: Causal Staleness Guard Empirical Challenges
# -------------------------------------------------------------

def test_future_timestamp_spy():
    """Verify SPY timestamp from the future relative to now returns UNKNOWN with FUTURE_INDEX_DATA."""
    mf = MarketTrendFilter()
    mf.on_bar(make_bar("SPY", 500, 501, 499, 500.5, hour=10, minute=1))
    mf.on_bar(make_bar("QQQ", 450, 451, 449, 450.5, hour=10, minute=0))
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
    trend, reason = mf.get_current_trend(asof=now)
    assert trend == MarketTrend.UNKNOWN, f"Expected UNKNOWN, got {trend}"
    assert "FUTURE_INDEX_DATA" in reason, f"Expected FUTURE_INDEX_DATA in reason, got '{reason}'"
    assert "-60.0s" in reason, f"Expected -60.0s in reason, got '{reason}'"


def test_future_timestamp_qqq():
    """Verify QQQ timestamp from the future relative to now returns UNKNOWN with FUTURE_INDEX_DATA."""
    mf = MarketTrendFilter()
    mf.on_bar(make_bar("SPY", 500, 501, 499, 500.5, hour=9, minute=59))
    mf.on_bar(make_bar("QQQ", 450, 451, 449, 450.5, hour=10, minute=2))
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
    trend, reason = mf.get_current_trend(asof=now)
    assert trend == MarketTrend.UNKNOWN, f"Expected UNKNOWN, got {trend}"
    assert "FUTURE_INDEX_DATA" in reason, f"Expected FUTURE_INDEX_DATA in reason, got '{reason}'"
    assert "-120.0s" in reason, f"Expected -120.0s in reason, got '{reason}'"


def test_microsecond_future():
    """Verify sub-second future timestamps (1 millisecond) are strictly caught."""
    mf = MarketTrendFilter()
    bar_ts = datetime(2026, 9, 22, 9, 35, 0, 1000, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500, 1000, bar_ts))
    mf.on_bar(BarEvent("QQQ", 450, 451, 449, 450, 1000, bar_ts))
    now = datetime(2026, 9, 22, 9, 35, 0, 0, tzinfo=ET_TZ)  # 1ms before bar
    trend, reason = mf.get_current_trend(asof=now)
    assert trend == MarketTrend.UNKNOWN, f"Expected UNKNOWN, got {trend}"
    assert "FUTURE_INDEX_DATA" in reason, f"Expected FUTURE_INDEX_DATA in reason, got '{reason}'"


def test_extreme_negative_interval():
    """Verify extreme future timestamp (-10^9s) does not overflow and returns UNKNOWN."""
    mf = MarketTrendFilter()
    future_dt = datetime(2058, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500, 1000, future_dt))
    mf.on_bar(BarEvent("QQQ", 450, 451, 449, 450, 1000, future_dt))
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
    trend, reason = mf.get_current_trend(asof=now)
    assert trend == MarketTrend.UNKNOWN
    assert "FUTURE_INDEX_DATA" in reason


def test_extreme_positive_interval():
    """Verify extreme past timestamp (+10^9s) returns STALE_INDEX_DATA without overflow."""
    mf = MarketTrendFilter()
    past_dt = datetime(1994, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500, 1000, past_dt))
    mf.on_bar(BarEvent("QQQ", 450, 451, 449, 450, 1000, past_dt))
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
    trend, reason = mf.get_current_trend(asof=now)
    assert trend == MarketTrend.UNKNOWN
    assert "STALE_INDEX_DATA" in reason


def test_threshold_boundary():
    """Verify staleness boundary condition: elapsed == 120.0s is fresh, 120.001s is stale."""
    mf = MarketTrendFilter(stale_threshold_sec=120.0)
    t_bar = datetime(2026, 9, 22, 9, 30, 0, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500.5, 1000, t_bar))
    mf.on_bar(BarEvent("QQQ", 450, 451, 449, 450.5, 1000, t_bar))

    # Exactly 120.0s -> elapsed = 120.0 -> elapsed > 120.0 is False -> Fresh
    t_exact = t_bar + timedelta(seconds=120)
    trend_exact, _ = mf.get_current_trend(asof=t_exact)
    assert trend_exact != MarketTrend.UNKNOWN, f"Expected fresh at exact boundary, got {trend_exact}"

    # 120.001s -> elapsed = 120.001 -> elapsed > 120.0 is True -> STALE -> UNKNOWN
    t_over = t_bar + timedelta(seconds=120.001)
    trend_over, reason_over = mf.get_current_trend(asof=t_over)
    assert trend_over == MarketTrend.UNKNOWN, f"Expected UNKNOWN just over boundary, got {trend_over}"
    assert "STALE_INDEX_DATA" in reason_over


def test_leap_and_dst():
    """Verify leap day, timezone conversions (ET/UTC), and naive datetimes."""
    mf = MarketTrendFilter()
    # Leap day 2024-02-29
    leap_day_bar = datetime(2024, 2, 29, 9, 30, 0, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500.5, 1000, leap_day_bar))
    mf.on_bar(BarEvent("QQQ", 450, 451, 449, 450.5, 1000, leap_day_bar))
    trend, _ = mf.get_current_trend(asof=leap_day_bar + timedelta(seconds=30))
    assert trend != MarketTrend.UNKNOWN

    # UTC vs ET timezone cross-conversion
    utc_asof = (leap_day_bar + timedelta(seconds=30)).astimezone(timezone.utc)
    trend_utc, _ = mf.get_current_trend(asof=utc_asof)
    assert trend_utc == trend

    # Naive datetime asof
    naive_asof = datetime(2024, 2, 29, 14, 30, 30)  # 14:30:30 UTC = 09:30:30 ET
    trend_naive, _ = mf.get_current_trend(asof=naive_asof)
    assert trend_naive == trend


def test_none_timestamp_handling():
    """Verify bar.timestamp = None is safely handled without raising AttributeError."""
    mf = MarketTrendFilter()
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500, 1000, None))
    assert mf.spy_state.bars_count == 0
    assert mf.spy_state.last_timestamp is None

    state = IndexState(symbol="SPY")
    state.update_bar(BarEvent("SPY", 500, 501, 499, 500, 1000, None))
    assert state.bars_count == 0
    assert state.last_timestamp is None

    valid_bar = make_bar("SPY", 500, 501, 499, 500, 1000, minute=30)
    mf.on_bar(valid_bar)
    assert mf.spy_state.bars_count == 1
    assert mf.spy_state.last_timestamp == valid_bar.timestamp


def test_fail_closed_gating():
    """Verify future data causes complete fail-closed veto across all strategies."""
    mf = MarketTrendFilter()
    future_dt = datetime(2026, 9, 22, 10, 30, 0, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500, 1000, future_dt))
    mf.on_bar(BarEvent("QQQ", 450, 451, 449, 450, 1000, future_dt))
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)

    for strat in ["orb", "vwap_pullback", "news_momentum", "mean_reversion"]:
        for side in [OrderSide.BUY, OrderSide.SELL]:
            ok, reason = mf.is_signal_permitted(strat, side, "AAPL", asof=now)
            assert ok is False, f"Strategy {strat} with side {side} must be DENIED when data is in future"
            assert "INDEX_FILTER_DENIED" in reason
            assert "FUTURE_INDEX_DATA" in reason

    ok_cat, reason_cat = mf.is_signal_permitted(
        "news_momentum", OrderSide.BUY, "AAPL", asof=now, catalyst_sentiment=0.90, volume_surge=6.0
    )
    assert ok_cat is True
    assert "APPROVED_EXTREME_CATALYST" in reason_cat


def test_snapshot_model_with_future():
    """Verify MarketTrendSnapshot faithfully reflects UNKNOWN and is_fresh=False."""
    mf = MarketTrendFilter()
    future_dt = datetime(2026, 9, 22, 10, 5, 0, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 500, 501, 499, 500, 1000, future_dt))
    mf.on_bar(BarEvent("QQQ", 450, 451, 449, 450, 1000, future_dt))
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)

    snap = mf.get_trend_snapshot(asof=now)
    assert isinstance(snap, MarketTrendSnapshot)
    assert snap.overall_trend == MarketTrend.UNKNOWN
    assert snap.is_fresh is False
    assert "FUTURE_INDEX_DATA" in snap.reason


def test_asof_none_defaults_safely():
    """Verify asof=None defaults to datetime.now(utc) safely."""
    mf = MarketTrendFilter()
    past_dt = datetime(2020, 1, 2, 10, 0, 0, tzinfo=ET_TZ)
    mf.on_bar(BarEvent("SPY", 300, 301, 299, 300.5, 1000, past_dt))
    mf.on_bar(BarEvent("QQQ", 200, 201, 199, 200.5, 1000, past_dt))

    trend, reason = mf.get_current_trend(asof=None)
    assert trend == MarketTrend.UNKNOWN
    assert "STALE_INDEX_DATA" in reason


# -------------------------------------------------------------
# SECTION 2: Macro-Aligned Mean Reversion Empirical Challenges
# -------------------------------------------------------------

def test_mean_reversion_bullish():
    """Verify Mean Reversion policy during BULLISH: BUY approved, SELL 100% blocked."""
    mf, asof = setup_bullish_filter()
    trend, _ = mf.get_current_trend(asof)
    assert trend == MarketTrend.BULLISH

    # BUY approved
    ok_buy_enum, r_buy_enum = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof)
    assert ok_buy_enum is True
    assert "aligned with MarketTrend.BULLISH" in r_buy_enum

    ok_buy_str, r_buy_str = mf.is_signal_permitted("mean_reversion", "BUY", "NVDA", asof=asof)
    assert ok_buy_str is True
    assert "aligned with MarketTrend.BULLISH" in r_buy_str

    # SELL blocked
    ok_sell_enum, r_sell_enum = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof)
    assert ok_sell_enum is False
    assert "INDEX_BETA_CONTRADICTION" in r_sell_enum
    assert "Shorting overbought NVDA denied during strong BULLISH market rally" in r_sell_enum

    ok_sell_str, r_sell_str = mf.is_signal_permitted("mean_reversion", "SELL", "NVDA", asof=asof)
    assert ok_sell_str is False
    assert "INDEX_BETA_CONTRADICTION" in r_sell_str

    ok_sell_low, r_sell_low = mf.is_signal_permitted("mean_reversion", "sell", "NVDA", asof=asof)
    assert ok_sell_low is False
    assert "INDEX_BETA_CONTRADICTION" in r_sell_low


def test_mean_reversion_bearish():
    """Verify Mean Reversion policy during BEARISH: BUY 100% blocked, SELL approved."""
    mf, asof = setup_bearish_filter()
    trend, _ = mf.get_current_trend(asof)
    assert trend == MarketTrend.BEARISH

    # BUY blocked (falling knife defense)
    ok_buy_enum, r_buy_enum = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof)
    assert ok_buy_enum is False
    assert "INDEX_BETA_CONTRADICTION" in r_buy_enum
    assert "catching falling knife" in r_buy_enum

    ok_buy_str, r_buy_str = mf.is_signal_permitted("mean_reversion", "BUY", "NVDA", asof=asof)
    assert ok_buy_str is False
    assert "INDEX_BETA_CONTRADICTION" in r_buy_str

    # SELL approved (relief fade)
    ok_sell_enum, r_sell_enum = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof)
    assert ok_sell_enum is True
    assert "aligned with MarketTrend.BEARISH" in r_sell_enum

    ok_sell_str, r_sell_str = mf.is_signal_permitted("mean_reversion", "SELL", "NVDA", asof=asof)
    assert ok_sell_str is True
    assert "aligned with MarketTrend.BEARISH" in r_sell_str


def test_mean_reversion_neutral():
    """Verify Mean Reversion policy during NEUTRAL: both BUY and SELL approved."""
    mf, asof = setup_neutral_filter()
    trend, _ = mf.get_current_trend(asof)
    assert trend == MarketTrend.NEUTRAL

    ok_buy, r_buy = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof)
    assert ok_buy is True
    assert "aligned with MarketTrend.NEUTRAL" in r_buy

    ok_sell, r_sell = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof)
    assert ok_sell is True
    assert "aligned with MarketTrend.NEUTRAL" in r_sell

    # Compare with ORB and VWAP Pullback which are vetoed in NEUTRAL
    ok_orb, _ = mf.is_signal_permitted("orb", OrderSide.BUY, "NVDA", asof=asof)
    assert ok_orb is False
    ok_vwap, _ = mf.is_signal_permitted("vwap_pullback", OrderSide.SELL, "NVDA", asof=asof)
    assert ok_vwap is False


def test_mean_reversion_unknown():
    """Verify Mean Reversion fails closed in UNKNOWN regime."""
    mf = MarketTrendFilter()
    asof = datetime(2026, 9, 22, 9, 35, 0, tzinfo=ET_TZ)
    trend, _ = mf.get_current_trend(asof)
    assert trend == MarketTrend.UNKNOWN

    ok_buy, r_buy = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof)
    assert ok_buy is False
    assert "INDEX_FILTER_DENIED" in r_buy

    ok_sell, r_sell = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof)
    assert ok_sell is False
    assert "INDEX_FILTER_DENIED" in r_sell


def test_monte_carlo_stress():
    """Run 1,000 randomized Monte Carlo iterations across all regimes and sides."""
    random.seed(42)
    symbols = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN"]

    for iteration in range(1000):
        regime_type = random.choice(["bull", "bear", "neutral", "unknown"])
        side = random.choice([OrderSide.BUY, OrderSide.SELL, "BUY", "SELL", "buy", "sell"])
        sym = random.choice(symbols)

        if regime_type == "bull":
            mf, asof = setup_bullish_filter()
            ok, reason = mf.is_signal_permitted("mean_reversion", side, sym, asof=asof)
            is_buy = (side == OrderSide.BUY) or (str(side).upper() == "BUY")
            if is_buy:
                assert ok is True
                assert "aligned with MarketTrend.BULLISH" in reason
            else:
                assert ok is False
                assert "INDEX_BETA_CONTRADICTION" in reason

        elif regime_type == "bear":
            mf, asof = setup_bearish_filter()
            ok, reason = mf.is_signal_permitted("mean_reversion", side, sym, asof=asof)
            is_buy = (side == OrderSide.BUY) or (str(side).upper() == "BUY")
            if is_buy:
                assert ok is False
                assert "INDEX_BETA_CONTRADICTION" in reason
            else:
                assert ok is True
                assert "aligned with MarketTrend.BEARISH" in reason

        elif regime_type == "neutral":
            mf, asof = setup_neutral_filter()
            ok, reason = mf.is_signal_permitted("mean_reversion", side, sym, asof=asof)
            assert ok is True
            assert "aligned with MarketTrend.NEUTRAL" in reason

        elif regime_type == "unknown":
            mf = MarketTrendFilter()
            asof = datetime(2026, 9, 22, 9, 35, 0, tzinfo=ET_TZ)
            ok, reason = mf.is_signal_permitted("mean_reversion", side, sym, asof=asof)
            assert ok is False
            assert "INDEX_FILTER_DENIED" in reason


def test_e2e_adaptation_engine_integration():
    """Verify end-to-end integration between DynamicAdaptationEngine and MarketTrendFilter."""
    mf, asof = setup_bullish_filter()
    engine = DynamicAdaptationEngine(market_filter=mf)
    engine.current_time_phase = TimeOfDayPhase.TREND_CONTINUATION.value

    # Overbought Short Fade in Bullish Market -> DENIED
    sell_sig = SignalEvent(
        symbol="NVDA",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        entry_price=100.0,
        stop_loss=102.0,
        take_profit_1=98.0,
        take_profit_2=96.0,
        strategy_id="mean_reversion",
        confidence=0.80,
        reason="Test overbought climax",
        timestamp=asof,
    )
    approved, reason, qty = engine.evaluate_signal_admission(
        sell_sig, equity=50000.0, current_positions_count=0, is_symbol_active=False
    )
    assert approved is False
    assert qty == 0
    assert "INDEX_BETA_CONTRADICTION" in reason
    assert "ADAPTATION_MARKET_FILTER_DENIED" in reason

    # Oversold Dip Buy in Bullish Market -> APPROVED
    buy_sig = SignalEvent(
        symbol="NVDA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit_1=102.0,
        take_profit_2=104.0,
        strategy_id="mean_reversion",
        confidence=0.80,
        reason="Test oversold dip",
        timestamp=asof,
    )
    approved_buy, reason_buy, qty_buy = engine.evaluate_signal_admission(
        buy_sig, equity=50000.0, current_positions_count=0, is_symbol_active=False
    )
    assert approved_buy is True
    assert qty_buy > 0

    # Bearish Market
    mf_bear, asof_bear = setup_bearish_filter()
    engine_bear = DynamicAdaptationEngine(market_filter=mf_bear)
    engine_bear.current_time_phase = TimeOfDayPhase.TREND_CONTINUATION.value

    # Oversold Dip Buy in Bearish Market -> DENIED (falling knife)
    buy_bear_sig = SignalEvent(
        symbol="NVDA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit_1=102.0,
        take_profit_2=104.0,
        strategy_id="mean_reversion",
        confidence=0.80,
        reason="Test oversold knife",
        timestamp=asof_bear,
    )
    appr_b, r_b, q_b = engine_bear.evaluate_signal_admission(
        buy_bear_sig, equity=50000.0, current_positions_count=0, is_symbol_active=False
    )
    assert appr_b is False
    assert q_b == 0
    assert "INDEX_BETA_CONTRADICTION" in r_b
    assert "catching falling knife" in r_b

    # Overbought Short Fade in Bearish Market -> APPROVED
    sell_bear_sig = SignalEvent(
        symbol="NVDA",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        entry_price=100.0,
        stop_loss=102.0,
        take_profit_1=98.0,
        take_profit_2=96.0,
        strategy_id="mean_reversion",
        confidence=0.80,
        reason="Test relief fade",
        timestamp=asof_bear,
    )
    appr_s, r_s, q_s = engine_bear.evaluate_signal_admission(
        sell_bear_sig, equity=50000.0, current_positions_count=0, is_symbol_active=False
    )
    assert appr_s is True
    assert q_s > 0


if __name__ == "__main__":
    test_funcs = [
        test_future_timestamp_spy,
        test_future_timestamp_qqq,
        test_microsecond_future,
        test_extreme_negative_interval,
        test_extreme_positive_interval,
        test_threshold_boundary,
        test_leap_and_dst,
        test_none_timestamp_handling,
        test_fail_closed_gating,
        test_snapshot_model_with_future,
        test_asof_none_defaults_safely,
        test_mean_reversion_bullish,
        test_mean_reversion_bearish,
        test_mean_reversion_neutral,
        test_mean_reversion_unknown,
        test_monte_carlo_stress,
        test_e2e_adaptation_engine_integration,
    ]
    passed = 0
    for f in test_funcs:
        try:
            f()
            passed += 1
            print(f"[SUCCESS] {f.__name__}")
        except Exception as e:
            print(f"[FAILURE] {f.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(test_funcs)} tests passed.")
    sys.exit(0 if passed == len(test_funcs) else 1)
