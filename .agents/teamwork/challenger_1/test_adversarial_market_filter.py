"""Adversarial stress test suite for MarketTrendFilter.

Tests extreme edge cases:
- Gap opens across sessions
- Inverted bars (high < low, close > high, close < low)
- Zero volume bars (single, multiple, cumulative)
- Flat prices (high == low == open == close)
- None/missing/future/past timestamps
- Staleness guard (> 120s, 5m apart, 121s, 119s)
- Asymmetric index feeds (SPY without QQQ, QQQ without SPY)
- Micro-deadband (±0.03% of VWAP)
- Comprehensive strategy admission policy matrix
- News momentum extreme catalyst override thresholds
"""
import math
import sys
from datetime import datetime, date, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest

from backend.app.core.market_filter import (
    IndexState,
    MarketTrend,
    MarketTrendFilter,
    IndexMetrics,
    MarketTrendSnapshot,
)
from backend.app.models.events import BarEvent, OrderSide

ET_TZ = ZoneInfo("America/New_York")


def make_bar(
    symbol: str,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: int = 10000,
    year: int = 2026,
    month: int = 9,
    day: int = 22,
    hour: int = 9,
    minute: int = 30,
    second: int = 0,
    tz=ET_TZ,
) -> BarEvent:
    if tz is not None:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    else:
        dt = datetime(year, month, day, hour, minute, second)
    return BarEvent(
        symbol=symbol,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        timestamp=dt,
    )


class TestMarketFilterAdversarial:
    """Adversarial suite for MarketTrendFilter."""

    # ---------------------------------------------------------
    # 1. Extreme Gap Opens
    # ---------------------------------------------------------
    def test_extreme_gap_open_across_session(self):
        """Verify massive gap open (+20% or -20%) on day 2 resets VWAP and does not corrupt state."""
        mf = MarketTrendFilter()

        # Day 1: Regular session trading at $500
        for m in range(30, 40):
            mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.5, vol=10000, day=21, minute=m))
            mf.on_bar(make_bar("QQQ", 450.0, 451.0, 449.0, 450.5, vol=10000, day=21, minute=m))

        assert mf.spy_state.bars_count == 10
        assert 500.0 < mf.spy_state.current_vwap < 501.0

        # Day 2: Massive gap up to $600 (+20%)
        mf.on_bar(make_bar("SPY", 600.0, 605.0, 598.0, 602.0, vol=50000, day=22, minute=30))
        mf.on_bar(make_bar("QQQ", 540.0, 545.0, 538.0, 542.0, vol=50000, day=22, minute=30))

        # Must have reset session cleanly
        assert mf.last_session_date == date(2026, 9, 22)
        assert mf.spy_state.bars_count == 1
        assert mf.qqq_state.bars_count == 1
        # VWAP must be anchored strictly to Day 2 open, NOT blended with Day 1
        assert 598.0 <= mf.spy_state.current_vwap <= 605.0
        assert mf.spy_state.first_open == 600.0

        trend, reason = mf.get_current_trend(asof=datetime(2026, 9, 22, 9, 30, 30, tzinfo=ET_TZ))
        assert trend == MarketTrend.BULLISH
        assert "EARLY_OPEN_CONVERGENCE" in reason

    def test_extreme_gap_down_across_session(self):
        """Verify massive gap down (-30%) resets session and reports BEARISH."""
        mf = MarketTrendFilter()
        mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.0, vol=10000, day=21, minute=30))
        mf.on_bar(make_bar("QQQ", 450.0, 451.0, 449.0, 450.0, vol=10000, day=21, minute=30))

        # Day 2: Gap down to 350
        mf.on_bar(make_bar("SPY", 350.0, 351.0, 345.0, 346.0, vol=50000, day=22, minute=30))
        mf.on_bar(make_bar("QQQ", 300.0, 301.0, 295.0, 296.0, vol=50000, day=22, minute=30))

        assert mf.spy_state.bars_count == 1
        assert mf.spy_state.current_vwap < 350.0
        trend, reason = mf.get_current_trend(asof=datetime(2026, 9, 22, 9, 30, 30, tzinfo=ET_TZ))
        assert trend == MarketTrend.BEARISH
        assert "EARLY_OPEN_CONVERGENCE" in reason

    # ---------------------------------------------------------
    # 2. Inverted Bars (Data Corruption Simulation)
    # ---------------------------------------------------------
    def test_inverted_bars_handling(self):
        """Verify that inverted bars (high < low, close outside bounds) do not raise unhandled exceptions."""
        state = IndexState(symbol="SPY")
        # Inverted bar: High = 90, Low = 110
        inverted_bar = make_bar("SPY", 100.0, 90.0, 110.0, 95.0, vol=1000, minute=30)
        # Should not raise exception
        state.update_bar(inverted_bar)
        assert state.bars_count == 1
        assert not math.isnan(state.current_vwap)
        assert not math.isinf(state.current_vwap)
        assert not math.isnan(state.ema9)
        assert not math.isnan(state.ema21)

        # to_metrics must not crash
        metrics = state.to_metrics()
        assert metrics.symbol == "SPY"
        assert not math.isnan(metrics.price_to_vwap_pct)

    # ---------------------------------------------------------
    # 3. Zero Volume Bars
    # ---------------------------------------------------------
    def test_zero_volume_bars_no_division_by_zero(self):
        """Verify zero volume does not cause ZeroDivisionError and VWAP falls back to close."""
        state = IndexState(symbol="SPY")
        b1 = make_bar("SPY", 100.0, 102.0, 99.0, 101.0, vol=0, minute=30)
        state.update_bar(b1)

        assert state.cum_vol == 0.0
        assert state.current_vwap == 101.0  # falls back to bar.close
        assert state.ema9 == 101.0
        assert state.ema21 == 101.0

        # Feed 10 consecutive zero-volume bars
        for m in range(31, 41):
            state.update_bar(make_bar("SPY", 101.0, 102.0, 100.0, 101.5, vol=0, minute=m))

        assert state.cum_vol == 0.0
        assert state.current_vwap == 101.5
        assert not math.isnan(state.current_vwap)

        # Now feed 1 bar with positive volume
        state.update_bar(make_bar("SPY", 101.5, 103.0, 101.0, 102.0, vol=500, minute=41))
        assert state.cum_vol == 500.0
        assert state.current_vwap > 0.0

    def test_market_filter_with_zero_volume_both_indices(self):
        """Verify MarketTrendFilter handles zero volume without crashing."""
        mf = MarketTrendFilter()
        for m in range(30, 36):
            mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.5, vol=0, minute=m))
            mf.on_bar(make_bar("QQQ", 450.0, 451.0, 449.0, 450.5, vol=0, minute=m))

        trend, reason = mf.get_current_trend(asof=datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ))
        assert trend in (MarketTrend.BULLISH, MarketTrend.BEARISH, MarketTrend.NEUTRAL)
        snapshot = mf.get_trend_snapshot(asof=datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ))
        assert snapshot.is_fresh is True
        assert snapshot.spy is not None
        assert snapshot.qqq is not None

    # ---------------------------------------------------------
    # 4. Flat Prices (high == low == open == close)
    # ---------------------------------------------------------
    def test_flat_prices_deadband_neutral(self):
        """Verify completely flat prices (zero range) stay inside deadband and report NEUTRAL."""
        mf = MarketTrendFilter()
        for m in range(30, 36):
            # Price exactly 500.00 across all fields
            mf.on_bar(make_bar("SPY", 500.0, 500.0, 500.0, 500.0, vol=1000, minute=m))
            mf.on_bar(make_bar("QQQ", 400.0, 400.0, 400.0, 400.0, vol=1000, minute=m))

        assert mf.spy_state.current_vwap == 500.0
        assert mf.spy_state.ema9 == 500.0
        assert mf.spy_state.ema21 == 500.0
        # Price == VWAP -> within deadband -> not bullish, not bearish
        assert mf.spy_state.is_bullish() is False
        assert mf.spy_state.is_bearish() is False

        trend, reason = mf.get_current_trend(asof=datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ))
        assert trend == MarketTrend.NEUTRAL
        assert "NEUTRAL" in reason

    def test_zero_price_bars(self):
        """Verify high == low == open == close == 0.0 does not cause ZeroDivisionError."""
        state = IndexState(symbol="SPY")
        state.update_bar(make_bar("SPY", 0.0, 0.0, 0.0, 0.0, vol=1000, minute=30))
        assert state.current_vwap == 0.0
        assert state.is_bullish() is False
        assert state.is_bearish() is False
        metrics = state.to_metrics()
        assert metrics.price_to_vwap_pct == 0.0

    # ---------------------------------------------------------
    # 5. Timestamp Edge Cases (Naive, Future Skew, Past)
    # ---------------------------------------------------------
    def test_naive_timestamp_ingestion(self):
        """Verify naive timestamps representing UTC are handled without crashing."""
        mf = MarketTrendFilter()
        # Bar with tzinfo=None at 13:30 UTC (09:30 ET)
        naive_bar_spy = make_bar("SPY", 500.0, 501.0, 499.0, 500.5, hour=13, minute=30, tz=None)
        naive_bar_qqq = make_bar("QQQ", 450.0, 451.0, 449.0, 450.5, hour=13, minute=30, tz=None)
        mf.on_bar(naive_bar_spy)
        mf.on_bar(naive_bar_qqq)

        assert mf.spy_state.bars_count == 1
        assert mf.qqq_state.bars_count == 1

        # asof naive
        trend, _ = mf.get_current_trend(asof=datetime(2026, 9, 22, 13, 30, 30))
        assert trend != MarketTrend.UNKNOWN

    def test_none_timestamp_behavior(self):
        """Demonstrate that None timestamp raises AttributeError due to unchecked ts.tzinfo."""
        mf = MarketTrendFilter()
        bar_none = BarEvent(
            symbol="SPY", open=500.0, high=501.0, low=499.0, close=500.5, volume=1000, timestamp=None
        )
        with pytest.raises(AttributeError, match="has no attribute 'tzinfo'"):
            mf.on_bar(bar_none)

    def test_future_clock_skew_staleness(self):
        """Verify future timestamp skew (> 120s ahead) triggers fail-closed UNKNOWN."""
        mf = MarketTrendFilter(stale_threshold_sec=120.0)
        mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=30))
        mf.on_bar(make_bar("QQQ", 450.0, 451.0, 449.0, 450.5, minute=30))

        # asof is 300s in the past relative to bar (clock skew)
        asof_past = datetime(2026, 9, 22, 9, 25, 0, tzinfo=ET_TZ)
        trend, reason = mf.get_current_trend(asof=asof_past)
        assert trend == MarketTrend.UNKNOWN
        assert "STALE_INDEX_DATA" in reason

    # ---------------------------------------------------------
    # 6. Staleness Guard & Gap Verification (> 120s)
    # ---------------------------------------------------------
    def test_staleness_boundary_precision(self):
        """Verify exact boundary of 120s staleness guard."""
        mf = MarketTrendFilter(stale_threshold_sec=120.0)
        for m in range(30, 35):
            mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=m))
            mf.on_bar(make_bar("QQQ", 450.0, 451.0, 449.0, 450.5, minute=m))

        last_bar_time = datetime(2026, 9, 22, 9, 34, 0, tzinfo=ET_TZ)

        # 119.0s -> FRESH
        t_119 = last_bar_time + timedelta(seconds=119.0)
        trend, _ = mf.get_current_trend(asof=t_119)
        assert trend != MarketTrend.UNKNOWN

        # 120.0s -> FRESH (<= 120s)
        t_120 = last_bar_time + timedelta(seconds=120.0)
        trend, _ = mf.get_current_trend(asof=t_120)
        assert trend != MarketTrend.UNKNOWN

        # 120.1s -> STALE -> UNKNOWN
        t_120_1 = last_bar_time + timedelta(seconds=120.1)
        trend, reason = mf.get_current_trend(asof=t_120_1)
        assert trend == MarketTrend.UNKNOWN
        assert "STALE_INDEX_DATA" in reason

        # 300s (5 minutes apart) -> STALE -> UNKNOWN
        t_300 = last_bar_time + timedelta(seconds=300.0)
        trend, reason = mf.get_current_trend(asof=t_300)
        assert trend == MarketTrend.UNKNOWN
        assert "STALE_INDEX_DATA" in reason

    def test_asymmetric_feed_staleness(self):
        """Verify that if SPY is fresh but QQQ is stale, filter fails closed to UNKNOWN."""
        mf = MarketTrendFilter(stale_threshold_sec=120.0)
        # SPY gets bars up to 09:35
        for m in range(30, 36):
            mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=m))

        # QQQ stops at 09:31 (stale by 4 minutes at 09:35)
        for m in range(30, 32):
            mf.on_bar(make_bar("QQQ", 450.0, 451.0, 449.0, 450.5, minute=m))

        asof_dt = datetime(2026, 9, 22, 9, 35, 30, tzinfo=ET_TZ)
        trend, reason = mf.get_current_trend(asof=asof_dt)
        assert trend == MarketTrend.UNKNOWN
        assert "QQQ data age" in reason

        # Vice versa: QQQ fresh, SPY stale
        mf.reset_session()
        for m in range(30, 32):
            mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=m))
        for m in range(30, 36):
            mf.on_bar(make_bar("QQQ", 450.0, 451.0, 449.0, 450.5, minute=m))

        trend, reason = mf.get_current_trend(asof=asof_dt)
        assert trend == MarketTrend.UNKNOWN
        assert "SPY data age" in reason

    # ---------------------------------------------------------
    # 7. Pre-Market & Zero-Bar Fail-Closed
    # ---------------------------------------------------------
    def test_missing_index_bars_fail_closed(self):
        """Verify that missing one or both index feeds completely returns UNKNOWN."""
        mf = MarketTrendFilter()
        # No bars at all
        trend, reason = mf.get_current_trend()
        assert trend == MarketTrend.UNKNOWN
        assert "MISSING_INDEX_BARS" in reason

        # Only SPY bars
        mf.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=30))
        trend, reason = mf.get_current_trend(asof=datetime(2026, 9, 22, 9, 30, 30, tzinfo=ET_TZ))
        assert trend == MarketTrend.UNKNOWN
        assert "MISSING_INDEX_BARS" in reason

    def test_pre_market_cutoff_exact_boundary(self):
        """Verify 09:29:59 ET is discarded but 09:30:00 ET is ingested."""
        mf = MarketTrendFilter()
        # 09:29:59 ET
        b_pre = make_bar("SPY", 500.0, 501.0, 499.0, 500.0, minute=29, second=59)
        mf.on_bar(b_pre)
        assert mf.spy_state.bars_count == 0

        # 09:30:00 ET
        b_rth = make_bar("SPY", 500.0, 501.0, 499.0, 500.0, minute=30, second=0)
        mf.on_bar(b_rth)
        assert mf.spy_state.bars_count == 1

    # ---------------------------------------------------------
    # 8. Micro-Deadband (±0.03% of VWAP)
    # ---------------------------------------------------------
    def test_micro_deadband_isolation(self):
        """Verify that price inside ±0.03% deadband is not considered bullish or bearish."""
        state = IndexState(symbol="SPY")
        # Initialize with 5 bars at 500.00
        for m in range(30, 35):
            state.update_bar(make_bar("SPY", 500.0, 500.0, 500.0, 500.0, vol=10000, minute=m))

        vwap = state.current_vwap  # 500.00
        deadband = 0.0003  # 0.03% of 500 = 0.15

        # Bar at 500.14: price_above_vwap = 500.14 > 500.15 is False!
        b_inside = make_bar("SPY", 500.0, 500.20, 500.0, 500.14, vol=1000, minute=35)
        state.update_bar(b_inside)
        assert state.is_bullish(deadband=deadband) is False

        # Bar at 500.16: price_above_vwap = 500.16 > 500.15 is True!
        b_outside = make_bar("SPY", 500.0, 500.20, 500.0, 500.16, vol=1000, minute=36)
        state.update_bar(b_outside)
        assert state.is_bullish(deadband=deadband) is True

    # ---------------------------------------------------------
    # 9. Exhaustive Signal Admission Matrix
    # ---------------------------------------------------------
    @pytest.mark.parametrize(
        "strategy,side,sentiment,volume_surge,expected_perm,reason_keyword",
        [
            # ORB
            ("orb", OrderSide.BUY, None, None, True, "APPROVED"),
            ("orb", OrderSide.SELL, None, None, False, "INDEX_BETA_CONTRADICTION"),
            # VWAP Pullback
            ("vwap_pullback", OrderSide.BUY, None, None, True, "APPROVED"),
            ("vwap_pullback", OrderSide.SELL, None, None, False, "INDEX_BETA_CONTRADICTION"),
            # News Momentum (Standard in Bullish)
            ("news_momentum", OrderSide.BUY, 0.65, 3.8, True, "APPROVED"),
            ("news_momentum", OrderSide.SELL, -0.65, 3.8, False, "INDEX_BETA_CONTRADICTION"),
            # News Momentum (Extreme Catalyst Overrides Bullish)
            ("news_momentum", OrderSide.SELL, -0.85, 5.0, True, "APPROVED_EXTREME_CATALYST"),
            ("news_momentum", OrderSide.SELL, -0.84, 5.0, False, "INDEX_BETA_CONTRADICTION"),
            ("news_momentum", OrderSide.SELL, -0.85, 4.99, False, "INDEX_BETA_CONTRADICTION"),
            # Mean Reversion in Bullish
            ("mean_reversion", OrderSide.SELL, None, None, True, "APPROVED"),
            ("mean_reversion", OrderSide.BUY, None, None, False, "falling knife"),
        ]
    )
    def test_admission_matrix_bullish_market(
        self, strategy, side, sentiment, volume_surge, expected_perm, reason_keyword
    ):
        mf = MarketTrendFilter()
        asof_dt = datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ)
        # Establish BULLISH
        for m in range(30, 36):
            spy_p = 500.0 + (m - 30) * 0.8
            qqq_p = 450.0 + (m - 30) * 1.0
            mf.on_bar(make_bar("SPY", spy_p - 0.2, spy_p + 0.9, spy_p - 0.3, spy_p + 0.7, vol=50000, minute=m))
            mf.on_bar(make_bar("QQQ", qqq_p - 0.2, qqq_p + 1.1, qqq_p - 0.3, qqq_p + 0.9, vol=40000, minute=m))

        ok, reason = mf.is_signal_permitted(
            strategy_id=strategy,
            side=side,
            symbol="TEST",
            asof=asof_dt,
            catalyst_sentiment=sentiment,
            volume_surge=volume_surge,
        )
        assert ok is expected_perm, f"Failed for {strategy} {side}: reason={reason}"
        assert reason_keyword in reason

    @pytest.mark.parametrize(
        "strategy,side,sentiment,volume_surge,expected_perm,reason_keyword",
        [
            # ORB
            ("orb", OrderSide.BUY, None, None, False, "INDEX_BETA_CONTRADICTION"),
            ("orb", OrderSide.SELL, None, None, True, "APPROVED"),
            # VWAP Pullback
            ("vwap_pullback", OrderSide.BUY, None, None, False, "INDEX_BETA_CONTRADICTION"),
            ("vwap_pullback", OrderSide.SELL, None, None, True, "APPROVED"),
            # News Momentum (Standard in Bearish)
            ("news_momentum", OrderSide.BUY, 0.65, 3.8, False, "INDEX_BETA_CONTRADICTION"),
            ("news_momentum", OrderSide.SELL, -0.65, 3.8, True, "APPROVED"),
            # News Momentum (Extreme Catalyst Overrides Bearish)
            ("news_momentum", OrderSide.BUY, 0.85, 5.0, True, "APPROVED_EXTREME_CATALYST"),
            ("news_momentum", OrderSide.BUY, 0.84, 5.0, False, "INDEX_BETA_CONTRADICTION"),
            ("news_momentum", OrderSide.BUY, 0.85, 4.99, False, "INDEX_BETA_CONTRADICTION"),
            # Mean Reversion in Bearish
            ("mean_reversion", OrderSide.BUY, None, None, True, "APPROVED"),
            ("mean_reversion", OrderSide.SELL, None, None, False, "Cannot fade overbought"),
        ]
    )
    def test_admission_matrix_bearish_market(
        self, strategy, side, sentiment, volume_surge, expected_perm, reason_keyword
    ):
        mf = MarketTrendFilter()
        asof_dt = datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ)
        # Establish BEARISH
        for m in range(30, 36):
            spy_p = 500.0 - (m - 30) * 0.8
            qqq_p = 450.0 - (m - 30) * 1.0
            mf.on_bar(make_bar("SPY", spy_p + 0.2, spy_p + 0.3, spy_p - 0.9, spy_p - 0.7, vol=50000, minute=m))
            mf.on_bar(make_bar("QQQ", qqq_p + 0.2, qqq_p + 0.3, qqq_p - 1.1, qqq_p - 0.9, vol=40000, minute=m))

        ok, reason = mf.is_signal_permitted(
            strategy_id=strategy,
            side=side,
            symbol="TEST",
            asof=asof_dt,
            catalyst_sentiment=sentiment,
            volume_surge=volume_surge,
        )
        assert ok is expected_perm, f"Failed for {strategy} {side}: reason={reason}"
        assert reason_keyword in reason

    def test_admission_matrix_neutral_and_unknown(self):
        """Verify NEUTRAL and UNKNOWN behavior across all strategies."""
        mf = MarketTrendFilter()
        asof_dt = datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ)

        # 1. UNKNOWN regime (no bars)
        for strat in ("orb", "vwap_pullback", "news_momentum", "mean_reversion"):
            ok, reason = mf.is_signal_permitted(strat, OrderSide.BUY, "AAPL", asof=asof_dt)
            assert ok is False
            assert "INDEX_FILTER_DENIED" in reason

        # 2. NEUTRAL regime (divergent SPY bullish, QQQ bearish)
        for m in range(30, 36):
            spy_p = 500.0 + (m - 30) * 0.8
            qqq_p = 450.0 - (m - 30) * 0.8
            mf.on_bar(make_bar("SPY", spy_p - 0.2, spy_p + 0.9, spy_p - 0.3, spy_p + 0.7, vol=50000, minute=m))
            mf.on_bar(make_bar("QQQ", qqq_p + 0.2, qqq_p + 0.3, qqq_p - 0.9, qqq_p - 0.7, vol=50000, minute=m))

        # ORB and VWAP Pullback denied in NEUTRAL
        ok, reason = mf.is_signal_permitted("orb", OrderSide.BUY, "AAPL", asof=asof_dt)
        assert ok is False
        assert "requires directional market trend" in reason

        ok, reason = mf.is_signal_permitted("vwap_pullback", OrderSide.SELL, "AAPL", asof=asof_dt)
        assert ok is False
        assert "requires directional market trend" in reason

        # News Momentum denied in NEUTRAL unless extreme catalyst
        ok, reason = mf.is_signal_permitted("news_momentum", OrderSide.BUY, "TSLA", asof=asof_dt, catalyst_sentiment=0.70, volume_surge=3.8)
        assert ok is False
        assert "requires directional index alignment" in reason

        ok, reason = mf.is_signal_permitted("news_momentum", OrderSide.BUY, "TSLA", asof=asof_dt, catalyst_sentiment=0.88, volume_surge=5.2)
        assert ok is True
        assert "APPROVED_EXTREME_CATALYST" in reason

        # Mean Reversion PERMITTED in NEUTRAL (range-bound chop is ideal for mean reversion)
        ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "MSFT", asof=asof_dt)
        assert ok is True
        assert "APPROVED" in reason

        ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "MSFT", asof=asof_dt)
        assert ok is True
        assert "APPROVED" in reason
