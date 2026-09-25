"""backend/tests/unit/test_strategies.py
Unit tests for Technical Indicators and the 4 Intraday Trading Strategies:
1. Opening Range Breakout (ORB)
2. VWAP Trend Pullback & Continuation
3. Catalyst News Momentum Breakout
4. Statistical Mean Reversion / Exhaustion Fades
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
import pytest

from backend.app.models.events import BarEvent, NewsEvent, OrderSide, OrderType
from backend.app.strategies.base import (
    SignalEvent,
    StrategyStatus,
    calculate_anchored_vwap,
    calculate_vwap_bands,
    calculate_atr,
    calculate_ema,
    calculate_sma,
    calculate_zscore,
    calculate_rsi,
    calculate_rvol,
)
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy, evaluate_orb_signal
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy, score_news_sentiment
from backend.app.strategies.mean_reversion import MeanReversionStrategy, evaluate_mean_reversion_zscore


def _make_bar(
    symbol: str = "AAPL",
    open_p: float = 100.0,
    high_p: float = 101.0,
    low_p: float = 99.0,
    close_p: float = 100.5,
    vol: int = 10000,
    ts_str: str = "2026-09-21T09:31:00-04:00",
) -> BarEvent:
    return BarEvent(
        symbol=symbol,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        timestamp=datetime.fromisoformat(ts_str),
    )


# ============================================================================
# 1. Technical Indicators Tests
# ============================================================================

def test_vwap_and_standard_deviation_bands():
    bars = [
        {"h": 101.0, "l": 99.0, "c": 100.0, "v": 1000},
        {"h": 102.0, "l": 100.0, "c": 101.0, "v": 2000},
    ]
    vwap, std = calculate_anchored_vwap(bars)
    assert round(vwap, 2) == 100.67
    assert std > 0.0

    bands = calculate_vwap_bands(bars, (1.0, 2.0))
    assert bands["upper_band_1"] > bands["vwap"] > bands["lower_band_1"]
    assert bands["upper_band_2"] > bands["upper_band_1"]
    assert bands["lower_band_2"] < bands["lower_band_1"]


def test_atr_calculation():
    # Empty
    assert calculate_atr([]) == 0.01

    # Single bar
    b1 = _make_bar(high_p=105.0, low_p=100.0, close_p=104.0)
    assert calculate_atr([b1]) == 5.0

    # Multi-bar True Range
    b2 = _make_bar(high_p=108.0, low_p=103.0, close_p=107.0)
    atr = calculate_atr([b1, b2], period=14)
    assert atr > 0.0


def test_ema_and_sma():
    prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    sma = calculate_sma(prices, 5)
    assert sma == (11 + 12 + 13 + 14 + 15) / 5.0

    ema = calculate_ema(prices, 5)
    assert ema > 0.0
    # Empty handling
    assert calculate_ema([], 5) == 0.0
    assert calculate_sma([], 5) == 0.0


def test_zscore_and_rsi():
    # Z-score on flat series
    flat = [50.0] * 20
    m, s, z = calculate_zscore(flat, 20)
    assert m == 50.0
    assert s == 0.0
    assert z == 0.0

    # Z-score on spike
    spiked = [100.0] * 19 + [120.0]
    m, s, z = calculate_zscore(spiked, 20)
    assert z > 2.5

    # RSI on rising series
    rising = [float(i) for i in range(100, 125)]
    rsi_high = calculate_rsi(rising, 14)
    assert rsi_high > 70.0

    # RSI on falling series
    falling = [float(i) for i in range(125, 100, -1)]
    rsi_low = calculate_rsi(falling, 14)
    assert rsi_low < 30.0

    # Short series
    assert calculate_rsi([100.0], 14) == 50.0


def test_rvol():
    assert calculate_rvol(250000, 100000) == 2.5
    assert calculate_rvol(10000, 0) == 1.0


# ============================================================================
# 2. Strategy 1: Opening Range Breakout (ORB)
# ============================================================================

def test_orb_range_establishment_and_rvol_veto():
    strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)

    # 5 opening bars: 09:30 to 09:34 ET
    for i, m in enumerate(range(30, 35)):
        bar = _make_bar(
            symbol="NVDA",
            open_p=124.0,
            high_p=124.50 + i * 0.1,
            low_p=123.50,
            close_p=124.20,
            vol=50000,
            ts_str=f"2026-09-21T09:{m:02d}:00-04:00",
        )
        sigs = strat.on_bar(bar)
        assert len(sigs) == 0

    state = strat._get_state("NVDA")
    # Range is established at breakout bar (09:35)
    # Breakout bar with low RVOL (e.g. 1.2x) -> no signal
    breakout_low_vol = _make_bar(
        symbol="NVDA",
        open_p=124.80,
        high_p=125.50,
        low_p=124.70,
        close_p=125.20,
        vol=55000,
        ts_str="2026-09-21T09:35:00-04:00",
    )
    sigs = strat.on_bar(breakout_low_vol)
    assert len(sigs) == 0


def test_orb_bullish_breakout_and_brackets():
    strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)

    # 5 bars (09:30 to 09:34) establishing high 124.80, low 123.60, midpoint 124.20
    bars = [
        _make_bar(high_p=124.50, low_p=123.60, close_p=124.20, vol=50000, ts_str="2026-09-21T09:30:00-04:00"),
        _make_bar(high_p=124.80, low_p=123.80, close_p=124.40, vol=50000, ts_str="2026-09-21T09:31:00-04:00"),
        _make_bar(high_p=124.60, low_p=123.90, close_p=124.30, vol=50000, ts_str="2026-09-21T09:32:00-04:00"),
        _make_bar(high_p=124.70, low_p=124.00, close_p=124.50, vol=50000, ts_str="2026-09-21T09:33:00-04:00"),
        _make_bar(high_p=124.60, low_p=124.10, close_p=124.40, vol=50000, ts_str="2026-09-21T09:34:00-04:00"),
    ]
    for b in bars:
        strat.on_bar(b)

    # 09:35 Breakout bar above 124.80 with volume surge (vol = 250,000 -> RVOL ~ 5x)
    bo_bar = _make_bar(
        high_p=125.40,
        low_p=124.70,
        close_p=125.25,
        vol=250000,
        ts_str="2026-09-21T09:35:00-04:00",
    )
    sigs = strat.on_bar(bo_bar)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.side == OrderSide.BUY
    assert sig.strategy_id == "orb"
    assert sig.entry_price == 125.25
    assert sig.stop_loss == 124.20  # Midpoint of [124.80, 123.60]
    assert sig.take_profit_1 > sig.entry_price
    assert sig.take_profit_2 > sig.take_profit_1

    # Cooldown: subsequent bars don't re-fire
    next_bar = _make_bar(
        high_p=125.80, low_p=125.00, close_p=125.60, vol=200000, ts_str="2026-09-21T09:36:00-04:00"
    )
    assert len(strat.on_bar(next_bar)) == 0


def test_orb_uses_configured_rvol_threshold():
    opening = [
        _make_bar(high_p=100.5, low_p=99.5, close_p=100.0, vol=10000,
                  ts_str=f"2026-09-21T09:{m:02d}:00-04:00")
        for m in range(30, 35)
    ]
    candidate = _make_bar(high_p=100.9, low_p=100.3, close_p=100.8, vol=17000,
                          ts_str="2026-09-21T09:35:00-04:00")
    permissive = OpeningRangeBreakoutStrategy(min_rvol=1.6)
    strict = OpeningRangeBreakoutStrategy(min_rvol=1.8)
    for strategy in (permissive, strict):
        for bar in opening:
            strategy.on_bar(bar)

    assert len(permissive.on_bar(candidate)) == 1
    assert strict.on_bar(candidate) == []


def test_orb_fifteen_minute_range_waits_until_0945():
    strategy = OpeningRangeBreakoutStrategy(range_minutes=15)
    for minute in range(30, 45):
        opening = _make_bar(high_p=100.5, low_p=99.5, close_p=100.0, vol=10000,
                            ts_str=f"2026-09-21T09:{minute:02d}:00-04:00")
        assert strategy.on_bar(opening) == []
    candidate = _make_bar(high_p=100.9, low_p=100.3, close_p=100.8, vol=25000,
                          ts_str="2026-09-21T09:45:00-04:00")

    assert len(strategy.on_bar(candidate)) == 1
    assert strategy._get_state("AAPL").range_high == 100.5


def test_orb_bearish_breakdown():
    strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
    for m in range(30, 35):
        strat.on_bar(_make_bar(high_p=100.0, low_p=96.0, close_p=98.0, vol=50000, ts_str=f"2026-09-21T09:{m:02d}:00-04:00"))

    # Breakout bar closing at 95.0 < 96.0 with RVOL 3.0x
    breakdown_bar = _make_bar(
        high_p=96.5, low_p=94.8, close_p=95.0, vol=200000, ts_str="2026-09-21T09:35:00-04:00"
    )
    sigs = strat.on_bar(breakdown_bar)
    assert len(sigs) == 1
    assert sigs[0].side == OrderSide.SELL
    assert sigs[0].stop_loss == 98.00  # Midpoint of 100 and 96
    assert sigs[0].take_profit_1 < sigs[0].entry_price


# ============================================================================
# 3. Strategy 2: VWAP Trend Pullback & Continuation
# ============================================================================

@pytest.mark.parametrize("bounce_above_vwap, expected_fallback", [(0.7, False), (0.9, True)])
def test_vwap_pullback_bullish_bounce(bounce_above_vwap, expected_fallback):
    strat = VWAPPullbackStrategy()

    # The documented EMA20/50 filter needs 50 regular-session closes.
    for i in range(49):
        p = 100.0 + i * 0.1
        b = _make_bar(
            symbol="MSFT",
            open_p=p,
            high_p=p + 0.2,
            low_p=p - 0.2,
            close_p=p + 0.15,
            vol=20000,
            ts_str=f"2026-09-21T{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}:00-04:00",
        )
        assert strat.on_bar(b) == []

    # Bar 50 pulls back to touch VWAP with hammer wick and volume surge.
    vwap, std = calculate_anchored_vwap(strat._get_state("MSFT").session_bars)
    bounce_bar = _make_bar(
        symbol="MSFT",
        open_p=vwap + 0.1,
        high_p=vwap + max(0.8, bounce_above_vwap + 0.1),
        low_p=vwap - 0.2,
        close_p=vwap + bounce_above_vwap,
        vol=50000,           # Volume surge > 1.2x SMA10
        ts_str="2026-09-21T10:19:00-04:00",
    )
    sigs = strat.on_bar(bounce_bar)
    assert len(sigs) >= 1
    sig = sigs[0]
    assert sig.side == OrderSide.BUY
    assert sig.strategy_id == "vwap_pullback"
    assert sig.stop_loss < sig.entry_price
    assert sig.take_profit_1 > sig.entry_price
    assert sig.target_1_is_r_fallback is expected_fallback


@pytest.mark.parametrize("rejection_below_vwap, expected_fallback", [(0.7, False), (0.9, True)])
def test_vwap_pullback_bearish_rejection_needs_ema50(rejection_below_vwap, expected_fallback):
    strat = VWAPPullbackStrategy()
    for i in range(49):
        p = 105.0 - i * 0.1
        bar = _make_bar(
            symbol="MSFT", open_p=p, high_p=p + 0.2, low_p=p - 0.2,
            close_p=p - 0.15, vol=20000,
            ts_str=f"2026-09-21T{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}:00-04:00",
        )
        assert strat.on_bar(bar) == []
    vwap, _ = calculate_anchored_vwap(strat._get_state("MSFT").session_bars)
    rejection = _make_bar(
        symbol="MSFT", open_p=vwap - 0.1, high_p=vwap + 0.2,
        low_p=vwap - max(0.8, rejection_below_vwap + 0.1),
        close_p=vwap - rejection_below_vwap, vol=50000,
        ts_str="2026-09-21T10:19:00-04:00",
    )
    sigs = strat.on_bar(rejection)
    assert len(sigs) == 1 and sigs[0].side == OrderSide.SELL
    assert sigs[0].target_1_is_r_fallback is expected_fallback


# ============================================================================
# 4. Strategy 3: Catalyst News Momentum Breakout
# ============================================================================

def test_news_sentiment_nlp_scoring():
    bull_headline = "Apple Beats Estimates and Raises Fiscal Guidance Significantly"
    score_bull = score_news_sentiment(bull_headline)
    assert score_bull >= 0.60

    bear_headline = "Tesla Recalls 100,000 Vehicles, Faces SEC Investigation and Fraud Inquiry"
    score_bear = score_news_sentiment(bear_headline)
    assert score_bear <= -0.60

    negated_headline = "Biotech Firm Not Approved by FDA After Clinical Trial Misses"
    score_negated = score_news_sentiment(negated_headline)
    assert score_negated < 0.0


def test_news_momentum_entry_and_contradiction_circuit_breaker():
    strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.50)

    # Ingest historical baseline bars for NVDA
    for i in range(20):
        strat.on_bar(_make_bar(symbol="NVDA", vol=10000, ts_str=f"2026-09-21T10:{i:02d}:00-04:00"))

    # 1. Bullish Headline arrives
    news_bull = NewsEvent(
        article_id=1,
        headline="NVIDIA Surges on Massive Blackwell Hyper-Scaler Cloud Partnership",
        summary="...",
        symbols=["NVDA"],
        source="Benzinga",
        created_at=datetime.fromisoformat("2026-09-21T10:20:00-04:00"),
        sentiment_score=0.85,
    )
    exit_sigs = strat.on_news(news_bull)
    assert len(exit_sigs) == 0  # No open position to contradict yet

    # 2. Breakout Bar with 4.0x volume surge confirms entry
    breakout_bar = _make_bar(
        symbol="NVDA",
        open_p=125.0,
        high_p=126.5,
        low_p=124.8,
        close_p=126.2,
        vol=42000,  # > 3.5x 10,000 SMA20
        ts_str="2026-09-21T10:21:00-04:00",
    )
    sigs = strat.on_bar(breakout_bar)
    assert len(sigs) == 1
    assert sigs[0].side == OrderSide.BUY
    assert sigs[0].strategy_id == "news_momentum"
    assert sigs[0].stop_loss == round(124.8 - 0.02, 4)

    # Verify strategy now monitors LONG position on NVDA
    assert strat.monitored_positions.get("NVDA") == "LONG"

    # 3. Adverse Contradiction Headline arrives while LONG
    news_adverse = NewsEvent(
        article_id=2,
        headline="DOJ Launches Antitrust Subpoena and Secondary Probe Into NVIDIA",
        summary="...",
        symbols=["NVDA"],
        source="Benzinga",
        created_at=datetime.fromisoformat("2026-09-21T10:25:00-04:00"),
        sentiment_score=-0.75,
    )
    contradiction_sigs = strat.on_news(news_adverse)
    assert len(contradiction_sigs) == 1
    assert contradiction_sigs[0].side == OrderSide.SELL
    assert "NEWS_CONTRADICTION" in contradiction_sigs[0].reason
    assert "NVDA" not in strat.monitored_positions  # Cleared after exit


# ============================================================================
# 5. Strategy 4: Statistical Mean Reversion / Exhaustion Fades
# ============================================================================

def test_mean_reversion_overbought_climax_fade():
    strat = MeanReversionStrategy(period=20, z_threshold=2.50)

    # 19 flat bars around $100 with baseline volume 10,000
    for i in range(19):
        strat.on_bar(_make_bar(
            symbol="SPY",
            open_p=100.0,
            high_p=100.5,
            low_p=99.5,
            close_p=100.0,
            vol=10000,
            ts_str=f"2026-09-21T11:{30+i}:00-04:00",
        ))

    # Bar 20: Climaxes to high 115.0 with huge volume (50,000 > 3.0x SMA20) and massive upper wick
    climax_bar = _make_bar(
        symbol="SPY",
        open_p=108.0,
        high_p=115.0,
        low_p=107.0,
        close_p=109.0,  # Upper wick = 115 - 109 = 6.0, candle range = 8.0 (wick ratio = 75% >= 50%)
        vol=50000,
        ts_str="2026-09-21T11:50:00-04:00",
    )
    sigs = strat.on_bar(climax_bar)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.side == OrderSide.SELL
    assert sig.strategy_id == "mean_reversion"
    assert sig.take_profit_1 < sig.entry_price  # Target reversion to 20-SMA mean
    assert sig.stop_loss > sig.entry_price


def test_strategy_performance_tracking():
    strat = OpeningRangeBreakoutStrategy()
    assert strat.trades_count == 0
    assert strat.win_rate == 0.0

    # Record 2 wins and 1 loss
    strat.record_trade(150.0)
    strat.record_trade(200.0)
    strat.record_trade(-100.0)

    assert strat.trades_count == 3
    assert strat.wins_count == 2
    assert strat.daily_pnl == 250.0
    assert round(strat.win_rate, 2) == 0.67

    d = strat.to_dict()
    assert d["id"] == "orb"
    assert d["daily_pnl"] == 250.0
    assert d["win_rate"] == 0.67

    strat.reset_daily_stats()
    assert strat.trades_count == 0
    assert strat.daily_pnl == 0.0


def test_orb_stop_distance_clamping_to_risk_window():
    strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.5)

    # 5 bars establishing an ultra-tight range on a $300 stock: [300.00, 300.20], midpoint 300.10
    bars = [
        _make_bar(open_p=300.05, high_p=300.20, low_p=300.00, close_p=300.10, vol=50000, ts_str="2026-09-21T09:30:00-04:00"),
        _make_bar(open_p=300.10, high_p=300.20, low_p=300.00, close_p=300.10, vol=50000, ts_str="2026-09-21T09:31:00-04:00"),
        _make_bar(open_p=300.10, high_p=300.20, low_p=300.00, close_p=300.10, vol=50000, ts_str="2026-09-21T09:32:00-04:00"),
        _make_bar(open_p=300.10, high_p=300.20, low_p=300.00, close_p=300.10, vol=50000, ts_str="2026-09-21T09:33:00-04:00"),
        _make_bar(open_p=300.10, high_p=300.20, low_p=300.00, close_p=300.10, vol=50000, ts_str="2026-09-21T09:34:00-04:00"),
    ]
    for b in bars:
        strat.on_bar(b)

    # Breakout bar with close = 300.30 (raw dist to midpoint = 0.20, which is only 0.067% < 0.40% min)
    bo_bar = _make_bar(
        open_p=300.15,
        high_p=300.40,
        low_p=300.10,
        close_p=300.30,
        vol=200000,
        ts_str="2026-09-21T09:35:00-04:00",
    )
    sigs = strat.on_bar(bo_bar)
    assert len(sigs) == 1
    sig = sigs[0]
    stop_dist = abs(sig.entry_price - sig.stop_loss)
    stop_pct = stop_dist / sig.entry_price
    # Must be clamped within [0.4%, 4.0%]
    assert 0.004 <= stop_pct <= 0.040
    assert stop_dist >= round(sig.entry_price * 0.004, 4)


def test_news_momentum_stop_distance_clamping():
    strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.0)

    # Ingest baseline bars for a $250 stock
    for i in range(20):
        strat.on_bar(_make_bar(symbol="TSLA", open_p=250.0, high_p=250.5, low_p=249.5, close_p=250.0, vol=10000, ts_str=f"2026-09-21T10:{i:02d}:00-04:00"))

    # Catalyst arrives
    news = NewsEvent(
        article_id=99,
        headline="TSLA Secures Massive Megapack Contract with Utility Giant",
        summary="...",
        symbols=["TSLA"],
        source="Benzinga",
        created_at=datetime.fromisoformat("2026-09-21T10:20:00-04:00"),
        sentiment_score=0.90,
    )
    strat.on_news(news)

    # Breakout bar where low is very close to close (e.g. low 249.95, close 250.00)
    # Raw dist = 250.00 - 249.93 = 0.07 (0.028% < 0.40%)
    bo_bar = _make_bar(
        symbol="TSLA",
        open_p=249.98,
        high_p=250.10,
        low_p=249.95,
        close_p=250.00,
        vol=50000,
        ts_str="2026-09-21T10:21:00-04:00",
    )
    sigs = strat.on_bar(bo_bar)
    assert len(sigs) == 1
    sig = sigs[0]
    stop_dist = abs(sig.entry_price - sig.stop_loss)
    stop_pct = stop_dist / sig.entry_price
    assert 0.004 <= stop_pct <= 0.040
    assert stop_dist >= round(sig.entry_price * 0.004, 4)


# ---------------------------------------------------------------------------
# Stop placement: a wide stop must reach the risk engine as-is, not be clamped
# ---------------------------------------------------------------------------

def test_resolve_stop_widens_a_too_tight_stop_to_the_risk_floor():
    from backend.app.strategies.base import resolve_stop

    stop, risk = resolve_stop(100.0, 0.05, is_long=True)   # 0.05% requested
    assert risk / 100.0 >= 0.0040
    assert stop < 100.0


def test_resolve_stop_leaves_a_wide_stop_untouched_for_the_risk_engine_to_reject():
    """A 6% opening range must NOT be pulled in to 3.8%.

    Clamping would turn a signal the risk engine is meant to reject into a
    live trade whose stop sits inside the range that justified it.
    """
    from backend.app.strategies.base import resolve_stop

    stop, risk = resolve_stop(100.0, 6.0, is_long=True)
    assert stop == pytest.approx(94.0, abs=0.001), "wide stop was moved off structure"
    assert risk / 100.0 == pytest.approx(0.06, abs=1e-6)
    assert _risk_verdict(100.0, stop).rejection_code == "STOP_DISTANCE_TOO_WIDE"


@pytest.mark.parametrize("entry", [9.97, 10.0, 47.31, 100.0, 233.33, 1041.07])
def test_resolve_stop_never_trips_the_risk_engine_floor(entry):
    """Knife-edge guard: a floor-width stop is never rejected as TOO_TIGHT."""
    from backend.app.strategies.base import resolve_stop

    for is_long in (True, False):
        stop, _ = resolve_stop(entry, 0.0, is_long)
        verdict = _risk_verdict(entry, stop, side="BUY" if is_long else "SELL")
        assert verdict.rejection_code != "STOP_DISTANCE_TOO_TIGHT", (
            f"entry={entry} is_long={is_long} stop={stop} was rejected as too tight"
        )


def _risk_verdict(entry_price, stop_price, side="BUY"):
    from backend.app.core.risk import InstitutionalRiskEngine

    return InstitutionalRiskEngine().evaluate_order_request(
        symbol="TEST",
        side=side,
        requested_qty=10,
        entry_price=entry_price,
        stop_price=stop_price,
        account_equity=50000.0,
        buying_power=200000.0,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )


def test_orb_clv_rejection():
    """Verify that a breakout bar with CLV < 0.65 (e.g. shooting star) is rejected."""
    strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
    bars = [
        _make_bar(high_p=124.50, low_p=123.60, close_p=124.20, vol=50000, ts_str="2026-09-21T09:30:00-04:00"),
        _make_bar(high_p=124.80, low_p=123.80, close_p=124.40, vol=50000, ts_str="2026-09-21T09:31:00-04:00"),
        _make_bar(high_p=124.60, low_p=123.90, close_p=124.30, vol=50000, ts_str="2026-09-21T09:32:00-04:00"),
        _make_bar(high_p=124.70, low_p=124.00, close_p=124.50, vol=50000, ts_str="2026-09-21T09:33:00-04:00"),
        _make_bar(high_p=124.60, low_p=124.10, close_p=124.40, vol=50000, ts_str="2026-09-21T09:34:00-04:00"),
    ]
    for b in bars:
        strat.on_bar(b)

    # Breakout candle touches 125.50 but closes at 124.85 on low of 124.70:
    # Range = 0.80, CLV = (124.85 - 124.70) / 0.80 = 0.15 / 0.80 = 0.1875 < 0.65
    bo_bar = _make_bar(
        high_p=125.50,
        low_p=124.70,
        close_p=124.85,
        vol=250000,
        ts_str="2026-09-21T09:35:00-04:00",
    )
    sigs = strat.on_bar(bo_bar)
    assert len(sigs) == 0, f"Expected CLV rejection, but got: {sigs}"


def test_orb_bar_range_cap_rejection():
    """Verify that an excessively wide breakout bar (> 2.2 * ATR) is rejected."""
    strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
    # Range high 124.80, low 124.20. Normal candle range is ~0.30 -> ATR ~ 0.35
    bars = [
        _make_bar(high_p=124.50, low_p=124.20, close_p=124.30, vol=50000, ts_str="2026-09-21T09:30:00-04:00"),
        _make_bar(high_p=124.80, low_p=124.30, close_p=124.40, vol=50000, ts_str="2026-09-21T09:31:00-04:00"),
        _make_bar(high_p=124.60, low_p=124.30, close_p=124.50, vol=50000, ts_str="2026-09-21T09:32:00-04:00"),
        _make_bar(high_p=124.70, low_p=124.30, close_p=124.50, vol=50000, ts_str="2026-09-21T09:33:00-04:00"),
        _make_bar(high_p=124.60, low_p=124.20, close_p=124.40, vol=50000, ts_str="2026-09-21T09:34:00-04:00"),
    ]
    for b in bars:
        strat.on_bar(b)

    # Bar with huge range = 126.50 - 124.50 = 2.00 (>> 2.2 * 0.35 = 0.77)
    bo_bar = _make_bar(
        high_p=126.50,
        low_p=124.50,
        close_p=126.40,
        vol=250000,
        ts_str="2026-09-21T09:35:00-04:00",
    )
    sigs = strat.on_bar(bo_bar)
    assert len(sigs) == 0, f"Expected Bar Range Cap rejection, but got: {sigs}"


def test_orb_extension_cap_rejection():
    """Verify that a breakout closing > 1.0 * ATR beyond the breakout level is rejected as overextended."""
    strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
    bars = [
        _make_bar(high_p=124.50, low_p=123.80, close_p=124.20, vol=50000, ts_str="2026-09-21T09:30:00-04:00"),
        _make_bar(high_p=124.80, low_p=124.00, close_p=124.40, vol=50000, ts_str="2026-09-21T09:31:00-04:00"),
        _make_bar(high_p=124.60, low_p=124.00, close_p=124.30, vol=50000, ts_str="2026-09-21T09:32:00-04:00"),
        _make_bar(high_p=124.70, low_p=124.10, close_p=124.50, vol=50000, ts_str="2026-09-21T09:33:00-04:00"),
        _make_bar(high_p=124.60, low_p=124.10, close_p=124.40, vol=50000, ts_str="2026-09-21T09:34:00-04:00"),
    ]
    for b in bars:
        strat.on_bar(b)

    # Range high is 124.80. Bar closes at 125.80 (> range_high + 1.0 * ATR)
    bo_bar = _make_bar(
        high_p=125.85,
        low_p=124.60,
        close_p=125.80,
        vol=250000,
        ts_str="2026-09-21T09:35:00-04:00",
    )
    sigs = strat.on_bar(bo_bar)
    assert len(sigs) == 0, f"Expected Extension Cap rejection, but got: {sigs}"


def test_news_word_boundary_substring_protection():
    """Verify that substring matches like 'miss' in 'emission' or 'transmission' are ignored."""
    neutral_headline = "Automaker Issues Report on Carbon Emission Standards"
    assert score_news_sentiment(neutral_headline) == 0.0

    bear_headline = "Automaker Misses Quarterly Delivery Targets by 15%"
    assert score_news_sentiment(bear_headline) <= -0.40


def test_news_candle_direction_confirmation():
    """Verify that a bullish news catalyst is rejected if the surge bar closes red (bearish candle)."""
    strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.0)
    for i in range(20):
        strat.on_bar(_make_bar(symbol="NVDA", vol=10000, ts_str=f"2026-09-21T10:{i:02d}:00-04:00"))

    news_bull = NewsEvent(
        article_id=10,
        headline="NVIDIA Surges on Massive Blackwell Hyper-Scaler Cloud Partnership",
        summary="...",
        symbols=["NVDA"],
        source="Benzinga",
        created_at=datetime.fromisoformat("2026-09-21T10:20:00-04:00"),
        sentiment_score=0.85,
    )
    strat.on_news(news_bull)

    # Surge bar has huge volume, but open=126.0 and close=125.2 (RED candle, close < open)
    red_surge_bar = _make_bar(
        symbol="NVDA",
        open_p=126.0,
        high_p=126.5,
        low_p=125.0,
        close_p=125.2,
        vol=50000,
        ts_str="2026-09-21T10:21:00-04:00",
    )
    sigs = strat.on_bar(red_surge_bar)
    assert len(sigs) == 0, "Bullish news momentum must NOT trigger on a red candle"


def test_news_0931_volume_baseline_floor():
    """Verify that during the opening minute when < 5 historical bars exist, 500k volume floor is enforced."""
    strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.0)
    # Only 1 prior bar with small volume 10,000
    strat.on_bar(_make_bar(symbol="XYZ", vol=10000, ts_str="2026-09-21T09:30:00-04:00"))

    news_bull = NewsEvent(
        article_id=11,
        headline="XYZ Upgraded to Outperform by Top Wall Street Analyst",
        summary="...",
        symbols=["XYZ"],
        source="Benzinga",
        created_at=datetime.fromisoformat("2026-09-21T09:31:00-04:00"),
        sentiment_score=0.80,
    )
    strat.on_news(news_bull)

    # Bar at 09:31 with volume 40,000. Against 10,000 this is 4x, but against 500k floor it is 0.08x.
    surge_bar = _make_bar(
        symbol="XYZ",
        open_p=10.0,
        high_p=10.5,
        low_p=9.9,
        close_p=10.4,
        vol=40000,
        ts_str="2026-09-21T09:31:00-04:00",
    )
    sigs = strat.on_bar(surge_bar)
    assert len(sigs) == 0, "Opening minute volume must be compared against 500k floor when < 5 bars"


def test_mean_reversion_moderate_vix_calibration():
    """Verify MeanReversionStrategy default configuration is calibrated for moderate VIX (14-16)."""
    strat = MeanReversionStrategy()
    assert strat.z_threshold == 1.65
    assert strat.rsi_overbought == 70.0
    assert strat.rsi_oversold == 30.0
    assert strat.volume_climax_multiplier == 1.30
    assert strat.min_wick_ratio == 0.30
    assert strat.atr_stop_multiplier == 0.15
    assert strat.min_rr_ratio == 1.00


def test_news_momentum_default_calibration():
    """Verify NewsMomentumStrategy default configuration is calibrated to volume surge 2.00x."""
    strat = NewsMomentumStrategy()
    assert strat.volume_surge_multiplier == 2.00
    assert strat.sentiment_threshold == 0.60
