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
        close_p=125.10,
        vol=250000,
        ts_str="2026-09-21T09:35:00-04:00",
    )
    sigs = strat.on_bar(bo_bar)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.side == OrderSide.BUY
    assert sig.strategy_id == "orb"
    assert sig.entry_price == 125.10
    assert sig.stop_loss == 124.20  # Midpoint of [124.80, 123.60]
    assert sig.take_profit_1 > sig.entry_price
    assert sig.take_profit_2 > sig.take_profit_1

    # Cooldown: subsequent bars don't re-fire
    next_bar = _make_bar(
        high_p=125.80, low_p=125.00, close_p=125.60, vol=200000, ts_str="2026-09-21T09:36:00-04:00"
    )
    assert len(strat.on_bar(next_bar)) == 0


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

def test_vwap_pullback_bullish_bounce():
    strat = VWAPPullbackStrategy()

    # Feed 15 bars in an uptrend between 10:00 and 10:14 ET
    for i in range(15):
        p = 100.0 + i * 0.5
        b = _make_bar(
            symbol="MSFT",
            open_p=p,
            high_p=p + 0.6,
            low_p=p - 0.2,
            close_p=p + 0.4,
            vol=20000,
            ts_str=f"2026-09-21T10:{i:02d}:00-04:00",
        )
        strat.on_bar(b)

    # Current VWAP is ~103.50. Bar 16 pulls back to touch VWAP with hammer wick & surge
    vwap, std = calculate_anchored_vwap(strat._get_state("MSFT").session_bars)
    bounce_bar = _make_bar(
        symbol="MSFT",
        open_p=vwap + 0.1,
        high_p=vwap + 0.8,
        low_p=vwap - 0.1,
        close_p=vwap + 0.7,  # Green close above VWAP
        vol=50000,           # Volume surge > 1.2x SMA10
        ts_str="2026-09-21T10:15:00-04:00",
    )
    sigs = strat.on_bar(bounce_bar)
    assert len(sigs) >= 1
    sig = sigs[0]
    assert sig.side == OrderSide.BUY
    assert sig.strategy_id == "vwap_pullback"
    assert sig.stop_loss < sig.entry_price
    assert sig.take_profit_1 > sig.entry_price


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
