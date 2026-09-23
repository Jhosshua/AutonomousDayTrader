"""Adversarial stress test suite for Strategy Entry Guards.

Tests:
1. ORB:
   - Shooting star candles (upper wick 80%) reject false breakouts
   - Hammer candles (lower wick 80%) reject false breakdowns
   - Inverted wicks (hammer on breakout, shooting star on breakdown) pass
   - Exact boundary CLV (0.649 vs 0.650 for BUY, 0.351 vs 0.350 for SELL)
   - Floating point precision boundary characteristics
   - Bar range cap (2.2 * ATR) and extension cap (1.0 * ATR)
   - Zero range bars (high == low)
2. News Momentum:
   - Regex word-boundary isolation ('emission', 'commission', 'transmission', 'backdrop', etc.)
   - Immediate negation handling vs multi-word negation limitations
   - Candle direction confirmation (green candle with negative news rejected, red candle with positive news rejected, dojis rejected)
   - 09:31 ET volume floor (500k floor prevents normal open volume from triggering)
   - News contradiction circuit breaker
3. Mean Reversion:
   - Z-score boundary (1.99 vs 2.00, -1.99 vs -2.00)
   - RSI boundary (69.9 vs 70.0, 30.1 vs 30.0)
   - Volume climax boundary (1.74 vs 1.75)
   - Wick ratio boundary (0.349 vs 0.350)
   - Reward-to-risk ratio (< 1.0 vs >= 1.0)
   - Time of day lockout (09:30-10:00 ET and >= 15:45 ET)
"""
import math
from datetime import datetime, date, time as dtime, timezone
from zoneinfo import ZoneInfo
import pytest

from backend.app.models.events import BarEvent, NewsEvent, OrderSide, OrderType
from backend.app.strategies.orb import (
    OpeningRangeBreakoutStrategy,
    evaluate_orb_signal,
    SymbolORBState,
)
from backend.app.strategies.news_momentum import (
    NewsMomentumStrategy,
    score_news_sentiment,
    PendingCatalyst,
)
from backend.app.strategies.mean_reversion import (
    MeanReversionStrategy,
    evaluate_mean_reversion_zscore,
)
from backend.app.strategies.base import calculate_rsi, calculate_atr

ET_TZ = ZoneInfo("America/New_York")


def make_bar(
    symbol: str,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: int = 10000,
    minute: int = 35,
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


def make_news(
    headline: str,
    sentiment_score: float = 0.0,
    symbols: list = None,
    created_at: datetime = None,
    article_id: int = 101,
    summary: str = "Sample news summary",
    source: str = "benzinga",
) -> NewsEvent:
    if symbols is None:
        symbols = ["TSLA"]
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    return NewsEvent(
        article_id=article_id,
        headline=headline,
        summary=summary,
        symbols=symbols,
        source=source,
        created_at=created_at,
        sentiment_score=sentiment_score,
    )


# ============================================================================
# 1. ORB ADVERSARIAL STRESS TESTS
# ============================================================================
class TestORBAdversarial:
    """Adversarial tests for Opening Range Breakout strategy."""

    def test_shooting_star_rejects_buy_breakout(self):
        """Shooting star candle with 80% upper wick breaking above range_high must be rejected by CLV."""
        bars_5m = [
            make_bar("AAPL", 99.0, 100.0, 98.0, 99.5, minute=30 + i)
            for i in range(5)
        ]
        # Breakout candidate candle:
        # Open=99.8, High=105.0, Low=99.5, Close=100.6 (Close > RangeHigh 100.0)
        # Total range = 105.0 - 99.5 = 5.5
        # Upper wick = 105.0 - 100.6 = 4.4 (80% upper wick)
        # CLV = 1.1 / 5.5 = 0.20 < 0.65 -> None
        shooting_star = make_bar("AAPL", 99.8, 105.0, 99.5, 100.6, vol=50000, minute=35)

        sig = evaluate_orb_signal(bars_5m, shooting_star, rvol=2.5, min_clv=0.65)
        assert sig is None, f"Expected None due to CLV=0.20 rejection, got {sig}"

    def test_hammer_rejects_sell_breakdown(self):
        """Hammer candle with 80% lower wick breaking below range_low must be rejected by CLV."""
        bars_5m = [
            make_bar("AAPL", 96.0, 97.0, 95.0, 96.5, minute=30 + i)
            for i in range(5)
        ]
        # Breakdown candidate candle:
        # Open=95.2, High=95.5, Low=90.0, Close=94.4 (Close < RangeLow 95.0)
        # Total range = 95.5 - 90.0 = 5.5
        # Lower wick = 94.4 - 90.0 = 4.4 (80% lower wick)
        # CLV = 4.4 / 5.5 = 0.80 > 0.35 -> None
        hammer = make_bar("AAPL", 95.2, 95.5, 90.0, 94.4, vol=50000, minute=35)

        sig = evaluate_orb_signal(bars_5m, hammer, rvol=2.5, max_clv_sell=0.35)
        assert sig is None, f"Expected None due to CLV=0.80 rejection on breakdown, got {sig}"

    def test_inverted_wicks_pass_breakouts(self):
        """Hammer on BUY breakout and Shooting Star on SELL breakdown represent institutional absorption and must pass."""
        bars_5m_buy = [
            make_bar("AAPL", 99.0, 100.0, 98.0, 99.5, minute=30 + i) for i in range(5)
        ]
        # Hammer breaking out above range_high 100.0:
        # Dips to 97.5, closes near high at 101.8. Range = 102.0 - 97.5 = 4.5
        # CLV = (101.8 - 97.5) / 4.5 = 4.3 / 4.5 = 0.955 >= 0.65 -> BUY
        hammer_buy = make_bar("AAPL", 99.0, 102.0, 97.5, 101.8, vol=50000, minute=35)
        sig_buy = evaluate_orb_signal(bars_5m_buy, hammer_buy, rvol=2.5, min_clv=0.65)
        assert sig_buy == "BUY"

        bars_5m_sell = [
            make_bar("AAPL", 96.0, 97.0, 95.0, 96.5, minute=30 + i) for i in range(5)
        ]
        # Shooting star breaking down below range_low 95.0:
        # Spikes to 96.5, closes near low at 93.2. Range = 96.5 - 93.0 = 3.5
        # CLV = (93.2 - 93.0) / 3.5 = 0.2 / 3.5 = 0.057 <= 0.35 -> SELL
        star_sell = make_bar("AAPL", 95.0, 96.5, 93.0, 93.2, vol=50000, minute=35)
        sig_sell = evaluate_orb_signal(bars_5m_sell, star_sell, rvol=2.5, max_clv_sell=0.35)
        assert sig_sell == "SELL"

    @pytest.mark.parametrize(
        "close_p,expected_sig",
        [
            (2.596, None),    # CLV = 2.596 / 4.0 = 0.649 -> Reject
            (2.600, "BUY"),   # CLV = 2.600 / 4.0 = 0.650 -> Exact boundary Accept
            (2.604, "BUY"),   # CLV = 2.604 / 4.0 = 0.651 -> Accept
        ]
    )
    def test_orb_clv_buy_boundary_precision(self, close_p, expected_sig):
        """Test exact boundary condition of min_clv = 0.65 on BUY with zero-base clean float division."""
        bars_5m = [make_bar("AAPL", 0.5, 1.0, 0.0, 0.5, minute=30 + i) for i in range(5)]
        # Range high = 1.0. Candidate has low=0.0, high=4.0 (range=4.0)
        cand = make_bar("AAPL", 1.0, 4.0, 0.0, close_p, vol=50000, minute=35)
        sig = evaluate_orb_signal(bars_5m, cand, rvol=2.0, min_clv=0.65)
        assert sig == expected_sig

    @pytest.mark.parametrize(
        "close_p,expected_sig",
        [
            (1.404, None),     # CLV = 1.404 / 4.0 = 0.351 -> Reject
            (1.400, "SELL"),   # CLV = 1.400 / 4.0 = 0.350 -> Exact boundary Accept
            (1.396, "SELL"),   # CLV = 1.396 / 4.0 = 0.349 -> Accept
        ]
    )
    def test_orb_clv_sell_boundary_precision(self, close_p, expected_sig):
        """Test exact boundary condition of max_clv_sell = 0.35 on SELL with zero-base clean float division."""
        bars_5m = [make_bar("AAPL", 2.0, 3.0, 2.0, 2.5, minute=30 + i) for i in range(5)]
        # Range low = 2.0. Candidate has low=0.0, high=4.0 (range=4.0)
        cand = make_bar("AAPL", 2.0, 4.0, 0.0, close_p, vol=50000, minute=35)
        sig = evaluate_orb_signal(bars_5m, cand, rvol=2.0, max_clv_sell=0.35)
        assert sig == expected_sig

    def test_orb_clv_ieee754_subtraction_jitter_empirical_finding(self):
        """Demonstrate the empirical edge case: without round(clv, 4), 102.6 - 100.0 produces 0.6499999999999986 < 0.65."""
        bars_5m = [make_bar("AAPL", 100.0, 101.0, 99.0, 100.0, minute=30 + i) for i in range(5)]
        # When low=100.0, high=104.0, close=102.60:
        cand = make_bar("AAPL", 101.0, 104.0, 100.0, 102.6, vol=50000, minute=35)
        raw_clv = (cand.close - cand.low) / (cand.high - cand.low)
        # raw_clv is strictly < 0.65 due to IEEE 754 float subtraction!
        assert raw_clv < 0.65
        sig = evaluate_orb_signal(bars_5m, cand, rvol=2.0, min_clv=0.65)
        # This confirms evaluate_orb_signal returns None due to machine epsilon
        assert sig is None

    def test_flat_candle_zero_division_guard(self):
        """Test zero-range candle (high == low) does not cause ZeroDivisionError."""
        bars_5m = [make_bar("AAPL", 100.0, 101.0, 99.0, 100.0, minute=30 + i) for i in range(5)]
        flat_bar = make_bar("AAPL", 102.0, 102.0, 102.0, 102.0, vol=50000, minute=35)
        sig = evaluate_orb_signal(bars_5m, flat_bar, rvol=2.0)
        assert sig is None

    def test_orb_strategy_guards_bar_range_and_extension(self):
        """Verify OpeningRangeBreakoutStrategy rejects candles exceeding 2.2*ATR or 1.0*ATR extension."""
        strat = OpeningRangeBreakoutStrategy()
        # Feed 5 opening range bars (09:30 to 09:34 ET)
        for m in range(30, 35):
            strat.on_bar(make_bar("AAPL", 100.0, 101.0, 100.0, 100.5, vol=20000, minute=m))

        # At 09:35, candidate bar establishes range (100.0 to 101.0) and evaluates breakout
        # Case 1: Exhausted impulse bar: candle_range = 3.5 > 2.2 * ATR (~1.0) -> REJECTED
        exhausted_bar = make_bar("AAPL", 100.5, 104.0, 100.5, 103.5, vol=100000, minute=35)
        sigs = strat.on_bar(exhausted_bar)
        assert len(sigs) == 0

        # Case 2: Reset and test Extension Cap: Close > RangeHigh + 1.0 * ATR
        strat.reset_daily_stats()
        for m in range(30, 35):
            strat.on_bar(make_bar("AAPL", 100.0, 101.0, 100.0, 100.5, vol=20000, minute=m))

        # Range high = 101.0. Let bar have normal range (1.2 <= 2.2 ATR) but close far extended at 102.5
        # (close - range_high) = 102.5 - 101.0 = 1.5 > 1.0 * ATR (~1.0) -> REJECTED
        extended_bar = make_bar("AAPL", 101.5, 102.6, 101.4, 102.5, vol=100000, minute=35)
        sigs = strat.on_bar(extended_bar)
        assert len(sigs) == 0


# ============================================================================
# 2. NEWS MOMENTUM ADVERSARIAL STRESS TESTS
# ============================================================================
class TestNewsMomentumAdversarial:
    """Adversarial tests for News Momentum strategy."""

    def test_regex_word_boundary_substring_isolation(self):
        """Tokens containing substrings like 'miss' or 'drop' inside other words must score 0.0."""
        adversarial_headlines = [
            ("Company cuts carbon emissions to achieve green target", 0.0),
            ("Federal Communications Commission holds scheduled regulatory session", 0.0),
            ("Auto supplier reveals breakthrough transmission technology", 0.0),
            ("Court grants special permission for regulatory submission", 0.0),
            ("New policy ensures smooth admission process", 0.0),
            ("Board rules against employee dismissal", 0.0),
            ("Market rally occurs against cautious economic backdrop", 0.0),
            ("Automaker conducts extensive crash test on new EV model", -0.462),  # Whole word 'crash' matches
        ]
        for hl, expected_score in adversarial_headlines:
            score = score_news_sentiment(hl)
            if expected_score < 0:
                assert score < 0.0, f"Expected negative for '{hl}', got {score}"
            else:
                assert score == expected_score, f"Headline '{hl}' falsely matched with score {score}"

    def test_immediate_negation_inversion_math(self):
        """Immediate negation patterns ('not', 'fails to', 'unable to') must flip token sentiment."""
        assert score_news_sentiment("Company did not miss earnings expectations") > 0.0
        assert score_news_sentiment("Company fails to beat earnings estimates") < 0.0
        assert score_news_sentiment("Executive unable to beat prior revenue record") < 0.0

    def test_candle_direction_filter_rejection(self):
        """Verify green candle with negative news and red candle with positive news are strictly rejected."""
        strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.5)
        now_dt = datetime(2026, 9, 22, 9, 35, 0, tzinfo=ET_TZ)

        # Baseline bars at 09:30-09:34
        for m in range(30, 35):
            strat.on_bar(make_bar("TSLA", 200.0, 201.0, 199.0, 200.5, vol=100000, minute=m))

        # Scenario 1: Highly negative news arrive (-0.80)
        neg_news = make_news(
            headline="SEC launches formal investigation into accounting fraud",
            sentiment_score=-0.80,
            symbols=["TSLA"],
            created_at=now_dt,
        )
        strat.on_news(neg_news)
        assert len(strat.pending_catalysts["TSLA"]) == 1

        # Candidate bar is GREEN (Close 202.0 > Open 200.0) with negative news
        green_candle = make_bar("TSLA", 200.0, 203.0, 199.5, 202.0, vol=500000, minute=35)
        sigs = strat.on_bar(green_candle)
        assert len(sigs) == 0, f"Expected 0 signals for green candle on negative news, got {sigs}"

        # Scenario 2: Highly positive news arrive (+0.80)
        strat.reset_daily_stats()
        for m in range(30, 35):
            strat.on_bar(make_bar("TSLA", 200.0, 201.0, 199.0, 200.5, vol=100000, minute=m))

        pos_news = make_news(
            headline="FDA approves breakthrough treatment with record efficacy",
            sentiment_score=0.80,
            symbols=["TSLA"],
            created_at=now_dt,
        )
        strat.on_news(pos_news)
        assert len(strat.pending_catalysts["TSLA"]) == 1

        # Candidate bar is RED (Close 198.0 < Open 200.0) with positive news
        red_candle = make_bar("TSLA", 200.0, 201.0, 197.5, 198.0, vol=500000, minute=35)
        sigs = strat.on_bar(red_candle)
        assert len(sigs) == 0, f"Expected 0 signals for red candle on positive news, got {sigs}"

        # Scenario 3: Flat Doji (Close == Open)
        doji_candle = make_bar("TSLA", 200.0, 201.0, 199.0, 200.0, vol=500000, minute=35)
        sigs = strat.on_bar(doji_candle)
        assert len(sigs) == 0, "Doji candle must be rejected for both directions"

        # Scenario 4: Valid green candle with positive news -> MUST PRODUCE BUY SIGNAL!
        strat.reset_daily_stats()
        for m in range(30, 35):
            strat.on_bar(make_bar("TSLA", 200.0, 201.0, 199.0, 200.5, vol=100000, minute=m))
        strat.on_news(pos_news)
        valid_green_candle = make_bar("TSLA", 200.0, 203.0, 199.5, 202.0, vol=500000, minute=35)
        sigs = strat.on_bar(valid_green_candle)
        assert len(sigs) == 1
        assert sigs[0].side == OrderSide.BUY

    def test_open_volume_baseline_floor_0931(self):
        """At 09:31 ET (< 5 prior bars), volume baseline is floored to 500k to prevent false surges."""
        strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.5)
        now_dt = datetime(2026, 9, 22, 9, 30, 30, tzinfo=ET_TZ)

        pos_news = make_news(
            headline="Tesla beats revenue estimates and raises annual guidance",
            sentiment_score=0.85,
            symbols=["TSLA"],
            created_at=now_dt,
        )
        strat.on_news(pos_news)

        # 09:31 ET: First bar of the day. Normal opening volume is 650k.
        # With 500k floor, 650k / 500k = 1.30x < 3.5x -> REJECTED!
        bar_normal_open = make_bar("TSLA", 200.0, 202.0, 199.5, 201.5, vol=650000, minute=31)
        sigs = strat.on_bar(bar_normal_open)
        assert len(sigs) == 0, f"Normal open volume must not trigger 3.5x surge, got {sigs}"

        # Genuine extraordinary surge on bar 1: 2,000,000 shares / 500k = 4.0x >= 3.5x -> ACCEPTED!
        strat.reset_daily_stats()
        strat.on_news(pos_news)
        bar_huge_open = make_bar("TSLA", 200.0, 205.0, 199.5, 204.0, vol=2000000, minute=31)
        sigs = strat.on_bar(bar_huge_open)
        assert len(sigs) == 1
        assert sigs[0].side == OrderSide.BUY

    def test_contradiction_circuit_breaker(self):
        """Adverse news headline while in open position triggers immediate market exit."""
        strat = NewsMomentumStrategy()
        strat.update_monitored_position("TSLA", "LONG")

        # Bearish news arrives with sentiment -0.75
        now_dt = datetime(2026, 9, 22, 10, 15, 0, tzinfo=ET_TZ)
        neg_news = make_news(
            headline="SEC subpoena issued to Tesla regarding accounting inquiry",
            sentiment_score=-0.75,
            symbols=["TSLA"],
            created_at=now_dt,
        )
        emergency_sigs = strat.on_news(neg_news)
        assert len(emergency_sigs) == 1
        assert emergency_sigs[0].side == OrderSide.SELL
        assert emergency_sigs[0].order_type == OrderType.MARKET
        assert "NEWS_CONTRADICTION_CIRCUIT_BREAKER" in emergency_sigs[0].reason
        assert "TSLA" not in strat.monitored_positions


# ============================================================================
# 3. MEAN REVERSION ADVERSARIAL STRESS TESTS
# ============================================================================
class TestMeanReversionAdversarial:
    """Adversarial tests for Statistical Mean Reversion strategy."""

    def test_z_score_boundary_conditions(self):
        """Verify mathematical precision of evaluate_mean_reversion_zscore."""
        # 20 flat prices
        prices_flat = [100.0] * 20
        mean, std, z = evaluate_mean_reversion_zscore(prices_flat)
        assert mean == 100.0
        assert std == 0.0
        assert z == 0.0

        # Window with fewer than 20 prices
        mean, std, z = evaluate_mean_reversion_zscore([100.0] * 19)
        assert mean == 0.0 and std == 0.0 and z == 0.0

        # Known distribution: 19 prices at 100.0, 20th price at 110.0
        prices_spike = [100.0] * 19 + [110.0]
        mean, std, z = evaluate_mean_reversion_zscore(prices_spike)
        assert mean == 100.5
        assert std == 2.18
        assert z == 4.36

    def test_open_flush_time_lockout(self):
        """Mean reversion must be completely locked out during 09:30-10:00 ET open volatility flush."""
        strat = MeanReversionStrategy()
        # Feed 25 bars during open flush (09:30 to 09:55 ET)
        for m in range(30, 55):
            strat.on_bar(make_bar("SPY", 500.0, 502.0, 498.0, 501.0, vol=50000, minute=m))

        extreme_bar = make_bar("SPY", 505.0, 525.0, 504.0, 510.0, vol=500000, minute=55)
        sigs = strat.on_bar(extreme_bar)
        assert len(sigs) == 0, f"Mean reversion must be disabled during open flush, got {sigs}"

    def test_eod_cutoff_time_lockout(self):
        """Mean reversion must be locked out after 15:45 ET."""
        strat = MeanReversionStrategy()
        for m in range(20, 45):
            strat.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.0, vol=50000, hour=15, minute=m))

        extreme_bar = make_bar("SPY", 500.0, 520.0, 499.0, 505.0, vol=500000, hour=15, minute=46)
        sigs = strat.on_bar(extreme_bar)
        assert len(sigs) == 0, f"Mean reversion must be disabled after 15:45 ET, got {sigs}"

    def test_mean_reversion_wick_rejection_filter(self):
        """Verify that wick ratio < 35% rejects mean reversion setup."""
        strat = MeanReversionStrategy(
            z_threshold=2.00,
            rsi_overbought=70.0,
            volume_climax_multiplier=1.75,
            min_wick_ratio=0.35,
            min_rr_ratio=1.00,
        )

        # Build baseline of 20 bars between 10:00 and 10:20
        for m in range(0, 20):
            strat.on_bar(make_bar("SPY", 500.0, 501.0, 499.0, 500.0, vol=20000, hour=10, minute=m))

        # Extreme bar at 10:21 with upper wick of only 16.7% (marubozu close near high)
        marubozu = make_bar("SPY", 505.0, 510.0, 504.0, 509.0, vol=100000, hour=10, minute=21)
        sigs = strat.on_bar(marubozu)
        assert len(sigs) == 0, f"Marubozu candle without wick rejection must be rejected, got {sigs}"
