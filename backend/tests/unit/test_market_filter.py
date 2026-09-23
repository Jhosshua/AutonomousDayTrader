"""backend/tests/unit/test_market_filter.py
Unit tests for deterministic MarketTrendFilter.
Verifies Anchored VWAP, EMA 9/21 math, regime transitions, staleness fail-closed,
and strategy signal admission policy matrix.
"""
from datetime import datetime, timezone
import pytest
from zoneinfo import ZoneInfo

from backend.app.core.market_filter import (
    IndexState,
    MarketTrend,
    MarketTrendFilter,
)
from backend.app.models.events import BarEvent, OrderSide

ET_TZ = ZoneInfo("America/New_York")


def _bar(
    symbol: str,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: int = 10000,
    minute: int = 30,
    hour: int = 9,
    day: int = 22,
) -> BarEvent:
    dt = datetime(2026, 9, day, hour, minute, 0, tzinfo=ET_TZ)
    return BarEvent(
        symbol=symbol,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        timestamp=dt,
    )


def test_index_state_vwap_and_ema_math():
    state = IndexState(symbol="SPY")
    # Bar 1: O=100, H=102, L=99, C=101, V=1000. Typical = (102+99+101)/3 = 100.6667
    b1 = _bar("SPY", 100.0, 102.0, 99.0, 101.0, vol=1000, minute=30)
    state.update_bar(b1)
    assert state.bars_count == 1
    assert state.cum_vol == 1000
    assert state.last_price == 101.0
    assert state.current_vwap == pytest.approx(100.6667, rel=1e-4)
    assert state.ema9 == 101.0
    assert state.ema21 == 101.0

    # Bar 2: O=101, H=104, L=101, C=103, V=2000. Typical = (104+101+103)/3 = 102.6667
    # Cum PV = 1000 * 100.6667 + 2000 * 102.6667 = 100666.7 + 205333.4 = 306000.1
    # Cum Vol = 3000 -> VWAP = 102.00
    b2 = _bar("SPY", 101.0, 104.0, 101.0, 103.0, vol=2000, minute=31)
    state.update_bar(b2)
    assert state.bars_count == 2
    assert state.cum_vol == 3000
    assert state.last_price == 103.0
    assert state.current_vwap == pytest.approx(102.0, rel=1e-4)
    # EMA9 = 103 * 0.2 + 101 * 0.8 = 20.6 + 80.8 = 101.4
    assert state.ema9 == pytest.approx(101.4, rel=1e-4)
    # EMA21 = 103 * (2/22) + 101 * (20/22) = 9.3636 + 91.8182 = 101.1818
    assert state.ema21 == pytest.approx(101.1818, rel=1e-4)
    assert state.is_bullish() is True
    assert state.is_bearish() is False


def test_market_filter_pre_market_discard_and_session_boundary():
    mf = MarketTrendFilter()

    # Pre-market bar at 09:15 ET should be ignored
    pre_bar = _bar("SPY", 100.0, 100.5, 99.5, 100.0, vol=5000, minute=15)
    mf.on_bar(pre_bar)
    assert mf.spy_state.bars_count == 0

    # Regular session bar at 09:30 ET
    rth_bar = _bar("SPY", 100.0, 101.0, 99.8, 100.5, vol=20000, minute=30)
    mf.on_bar(rth_bar)
    assert mf.spy_state.bars_count == 1
    assert mf.last_session_date == rth_bar.timestamp.date()

    # Next session bar on day 23 resets state
    next_day_bar = _bar("SPY", 102.0, 103.0, 101.5, 102.5, vol=20000, minute=30, day=23)
    mf.on_bar(next_day_bar)
    assert mf.spy_state.bars_count == 1
    assert mf.last_session_date == next_day_bar.timestamp.date()
    assert mf.spy_state.last_price == 102.5


def test_early_open_convergence():
    mf = MarketTrendFilter()
    # 09:30 ET bar (1 bar only)
    mf.on_bar(_bar("SPY", 500.0, 502.0, 499.5, 501.5, minute=30))
    mf.on_bar(_bar("QQQ", 450.0, 452.0, 449.5, 451.8, minute=30))

    trend, reason = mf.get_current_trend(asof=_bar("SPY", 500, 502, 499, 501.5, minute=30).timestamp)
    assert trend == MarketTrend.BULLISH
    assert "EARLY_OPEN_CONVERGENCE" in reason

    # Divergent early open
    mf.reset_session()
    mf.on_bar(_bar("SPY", 500.0, 502.0, 499.5, 501.5, minute=30))  # Green
    mf.on_bar(_bar("QQQ", 450.0, 450.5, 447.0, 448.0, minute=30))  # Red
    trend, reason = mf.get_current_trend(asof=_bar("SPY", 500, 502, 499, 501.5, minute=30).timestamp)
    assert trend == MarketTrend.NEUTRAL
    assert "EARLY_OPEN_MIXED" in reason


def test_consensus_bullish_and_bearish_regimes():
    mf = MarketTrendFilter()

    # Feed 6 bars in a strong bull trend
    for m in range(30, 36):
        spy_p = 500.0 + (m - 30) * 0.8
        qqq_p = 450.0 + (m - 30) * 1.0
        mf.on_bar(_bar("SPY", spy_p - 0.2, spy_p + 0.9, spy_p - 0.3, spy_p + 0.7, vol=50000, minute=m))
        mf.on_bar(_bar("QQQ", qqq_p - 0.2, qqq_p + 1.1, qqq_p - 0.3, qqq_p + 0.9, vol=40000, minute=m))

    trend, reason = mf.get_current_trend(asof=datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ))
    assert trend == MarketTrend.BULLISH
    assert "BULLISH" in reason

    # Now feed strong bear trend on a new session
    mf.reset_session()
    for m in range(30, 36):
        spy_p = 500.0 - (m - 30) * 0.8
        qqq_p = 450.0 - (m - 30) * 1.0
        mf.on_bar(_bar("SPY", spy_p + 0.2, spy_p + 0.3, spy_p - 0.9, spy_p - 0.7, vol=50000, minute=m))
        mf.on_bar(_bar("QQQ", qqq_p + 0.2, qqq_p + 0.3, qqq_p - 1.1, qqq_p - 0.9, vol=40000, minute=m))

    trend, reason = mf.get_current_trend(asof=datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ))
    assert trend == MarketTrend.BEARISH
    assert "BEARISH" in reason


def test_staleness_fail_closed_to_unknown():
    mf = MarketTrendFilter(stale_threshold_sec=120.0)
    for m in range(30, 35):
        mf.on_bar(_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=m))
        mf.on_bar(_bar("QQQ", 450.0, 451.0, 449.0, 450.5, minute=m))

    # Bar timestamp at 09:34:00 ET.
    # Querying at 09:35:30 ET (90s elapsed) -> Fresh
    t_fresh = datetime(2026, 9, 22, 9, 35, 30, tzinfo=ET_TZ)
    trend, _ = mf.get_current_trend(asof=t_fresh)
    assert trend != MarketTrend.UNKNOWN

    # Querying at 09:37:00 ET (180s elapsed > 120s threshold) -> STALE -> UNKNOWN
    t_stale = datetime(2026, 9, 22, 9, 37, 0, tzinfo=ET_TZ)
    trend, reason = mf.get_current_trend(asof=t_stale)
    assert trend == MarketTrend.UNKNOWN
    assert "STALE_INDEX_DATA" in reason


def test_signal_admission_policy_matrix():
    mf = MarketTrendFilter()
    asof_dt = datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ)

    # 1. Establish BULLISH regime
    for m in range(30, 36):
        spy_p = 500.0 + (m - 30) * 0.8
        qqq_p = 450.0 + (m - 30) * 1.0
        mf.on_bar(_bar("SPY", spy_p - 0.2, spy_p + 0.9, spy_p - 0.3, spy_p + 0.7, vol=50000, minute=m))
        mf.on_bar(_bar("QQQ", qqq_p - 0.2, qqq_p + 1.1, qqq_p - 0.3, qqq_p + 0.9, vol=40000, minute=m))

    # ORB in BULLISH:
    # BUY allowed
    ok, _ = mf.is_signal_permitted("orb", OrderSide.BUY, "AAPL", asof=asof_dt)
    assert ok is True
    # SELL rejected (the AAPL 10:09 error scenario!)
    ok, reason = mf.is_signal_permitted("orb", OrderSide.SELL, "AAPL", asof=asof_dt)
    assert ok is False
    assert "INDEX_BETA_CONTRADICTION" in reason

    # News Momentum in BULLISH:
    # BUY allowed
    ok, _ = mf.is_signal_permitted("news_momentum", OrderSide.BUY, "TSLA", asof=asof_dt)
    assert ok is True
    # Standard SELL rejected (the TSLA 09:31 error scenario!)
    ok, reason = mf.is_signal_permitted("news_momentum", OrderSide.SELL, "TSLA", asof=asof_dt, catalyst_sentiment=-0.65, volume_surge=3.8)
    assert ok is False
    assert "INDEX_BETA_CONTRADICTION" in reason
    # Extreme catalyst SELL permitted (|S| >= 0.85, volume >= 5.0x)
    ok, reason = mf.is_signal_permitted("news_momentum", OrderSide.SELL, "TSLA", asof=asof_dt, catalyst_sentiment=-0.90, volume_surge=5.5)
    assert ok is True
    assert "APPROVED_EXTREME_CATALYST" in reason

    # Mean Reversion in BULLISH:
    # Long fade (buying oversold dip in bull market) allowed
    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof_dt)
    assert ok is True
    assert "aligned with MarketTrend.BULLISH" in reason
    # Short fade (shorting overbought in bull rally) rejected
    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
    assert ok is False
    assert "INDEX_BETA_CONTRADICTION" in reason

    # Mean Reversion in BEARISH:
    mf.reset_session()
    for m in range(30, 36):
        spy_p = 500.0 - (m - 30) * 0.8
        qqq_p = 450.0 - (m - 30) * 1.0
        mf.on_bar(_bar("SPY", spy_p + 0.2, spy_p + 0.3, spy_p - 0.9, spy_p - 0.7, vol=50000, minute=m))
        mf.on_bar(_bar("QQQ", qqq_p + 0.2, qqq_p + 0.3, qqq_p - 1.1, qqq_p - 0.9, vol=40000, minute=m))
    # Short fade (fading relief bounce in bear market) allowed
    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
    assert ok is True
    assert "aligned with MarketTrend.BEARISH" in reason
    # Long fade (catching falling knife in bear decline) rejected
    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof_dt)
    assert ok is False
    assert "INDEX_BETA_CONTRADICTION" in reason


def test_market_filter_future_index_lookahead_rejection():
    mf = MarketTrendFilter(stale_threshold_sec=120.0)
    for m in range(30, 36):
        mf.on_bar(_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=m))
        mf.on_bar(_bar("QQQ", 450.0, 451.0, 449.0, 450.5, minute=m))

    # Index latest bar is at 09:35:00 ET
    # Query with asof in the past: 09:34:30 ET (-30s elapsed -> future data)
    t_past = datetime(2026, 9, 22, 9, 34, 30, tzinfo=ET_TZ)
    trend, reason = mf.get_current_trend(asof=t_past)
    assert trend == MarketTrend.UNKNOWN
    assert "FUTURE_INDEX_DATA" in reason


def test_market_filter_gracefully_handles_none_timestamp():
    mf = MarketTrendFilter()
    # Must not raise AttributeError
    mf.on_bar(BarEvent("SPY", 500.0, 502.0, 499.0, 501.0, 10000, None))
    assert mf.spy_state.bars_count == 0


def test_market_filter_neutral_regime_idiosyncratic_breakouts_and_mean_reversion():
    """Verify NEUTRAL regime rules:
    - mean_reversion allowed (both BUY and SELL)
    - orb and news_momentum allowed when RVOL >= 2.20 (APPROVED_IDIOSYNCRATIC_BREAKOUT)
    - orb and news_momentum denied when RVOL < 2.20 or None (INDEX_FILTER_DENIED)
    - vwap_pullback denied (INDEX_FILTER_DENIED)
    """
    mf = MarketTrendFilter()
    # Mixed early open produces NEUTRAL trend
    mf.on_bar(_bar("SPY", 500.0, 502.0, 499.5, 501.5, minute=30))
    mf.on_bar(_bar("QQQ", 450.0, 450.5, 447.0, 448.0, minute=30))
    asof_dt = datetime(2026, 9, 22, 9, 30, 30, tzinfo=ET_TZ)
    trend, _ = mf.get_current_trend(asof=asof_dt)
    assert trend == MarketTrend.NEUTRAL

    # 1. Mean reversion permitted in NEUTRAL for both sides
    ok_buy, reason_buy = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof_dt)
    assert ok_buy is True
    assert "APPROVED" in reason_buy

    ok_sell, reason_sell = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
    assert ok_sell is True
    assert "APPROVED" in reason_sell

    # 2. ORB in NEUTRAL: high RVOL >= 2.20 permitted, below 2.20 denied
    ok_orb_high, reason_orb_high = mf.is_signal_permitted("orb", OrderSide.BUY, "AAPL", asof=asof_dt, rvol=2.25)
    assert ok_orb_high is True
    assert "APPROVED_IDIOSYNCRATIC_BREAKOUT" in reason_orb_high

    ok_orb_low, reason_orb_low = mf.is_signal_permitted("orb", OrderSide.BUY, "AAPL", asof=asof_dt, rvol=1.90)
    assert ok_orb_low is False
    assert "INDEX_FILTER_DENIED" in reason_orb_low

    ok_orb_none, reason_orb_none = mf.is_signal_permitted("orb", OrderSide.BUY, "AAPL", asof=asof_dt, rvol=None)
    assert ok_orb_none is False
    assert "INDEX_FILTER_DENIED" in reason_orb_none

    # 3. News Momentum in NEUTRAL: high RVOL >= 2.20 permitted, below 2.20 denied
    ok_news_high, reason_news_high = mf.is_signal_permitted("news_momentum", OrderSide.BUY, "TSLA", asof=asof_dt, rvol=2.50)
    assert ok_news_high is True
    assert "APPROVED_IDIOSYNCRATIC_BREAKOUT" in reason_news_high

    ok_news_low, reason_news_low = mf.is_signal_permitted("news_momentum", OrderSide.BUY, "TSLA", asof=asof_dt, rvol=1.80)
    assert ok_news_low is False
    assert "INDEX_FILTER_DENIED" in reason_news_low

    # 4. VWAP Pullback in NEUTRAL: strictly denied
    ok_vwap_buy, reason_vwap_buy = mf.is_signal_permitted("vwap_pullback", OrderSide.BUY, "AAPL", asof=asof_dt)
    assert ok_vwap_buy is False
    assert "INDEX_FILTER_DENIED" in reason_vwap_buy

    ok_vwap_sell, reason_vwap_sell = mf.is_signal_permitted("vwap_pullback", OrderSide.SELL, "AAPL", asof=asof_dt)
    assert ok_vwap_sell is False
    assert "INDEX_FILTER_DENIED" in reason_vwap_sell


def test_market_filter_trending_lockouts_and_counter_trend_rejection():
    """Verify BULLISH / BEARISH regime rules:
    - ORB and VWAP Pullback permitted along index beta, denied counter-trend
    - Mean reversion denied counter-trend with INDEX_FILTER_DENIED
    """
    mf = MarketTrendFilter()
    for m in range(30, 36):
        spy_p = 500.0 + (m - 30) * 0.8
        qqq_p = 450.0 + (m - 30) * 1.0
        mf.on_bar(_bar("SPY", spy_p - 0.2, spy_p + 0.9, spy_p - 0.3, spy_p + 0.7, vol=50000, minute=m))
        mf.on_bar(_bar("QQQ", qqq_p - 0.2, qqq_p + 1.1, qqq_p - 0.3, qqq_p + 0.9, vol=40000, minute=m))

    asof_dt = datetime(2026, 9, 22, 9, 36, 0, tzinfo=ET_TZ)
    trend, _ = mf.get_current_trend(asof=asof_dt)
    assert trend == MarketTrend.BULLISH

    # In BULLISH: VWAP Pullback BUY allowed, SELL denied
    ok_v_buy, _ = mf.is_signal_permitted("vwap_pullback", OrderSide.BUY, "AAPL", asof=asof_dt)
    assert ok_v_buy is True
    ok_v_sell, reason_v_sell = mf.is_signal_permitted("vwap_pullback", OrderSide.SELL, "AAPL", asof=asof_dt)
    assert ok_v_sell is False
    assert "INDEX_BETA_CONTRADICTION" in reason_v_sell

    # In BULLISH: Mean Reversion SELL denied with INDEX_FILTER_DENIED
    ok_mr_sell, reason_mr_sell = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
    assert ok_mr_sell is False
    assert "INDEX_FILTER_DENIED" in reason_mr_sell

