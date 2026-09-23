"""backend/tests/test_swing_indicators.py
Unit & Causality Tests for Causal Daily Indicators, Aggregator, and Seed Fixtures.

Verifies:
1. 200-day Simple Moving Average (Rule 1: Macro Floor)
2. 60-day Relative Strength vs QQQ (Rule 2: Market Leadership)
3. 2-day Connors RSI (Rule 3: Panic Trigger & Rule 7b Exit)
4. 14-day Daily ATR & 2.5x ATR Emergency Stop (Rule 6)
5. 5-day SMA Exit (Rule 7a)
6. Zero Lookahead Guarantee: no future data leakage across session boundaries
7. DailyBarStore seed fixture loading (250+ bars per certified symbol)
8. DailyBarAggregator intraday 1m -> daily bar commitment at 16:00 ET
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from backend.app.models.events import BarEvent
from backend.app.strategies.swing_indicators import (
    DailyBar,
    DailyBarAggregator,
    DailyBarStore,
    calculate_daily_atr,
    calculate_relative_strength_60d,
    calculate_rsi2,
    calculate_sma,
    evaluate_swing_exit,
    evaluate_swing_qualification,
)


def _generate_bars(
    symbol: str,
    start_date: date,
    count: int,
    base_price: float,
    trend: float = 0.0,
    daily_vol: float = 2.0,
) -> list[DailyBar]:
    """Helper to generate a sequence of valid daily bars for testing."""
    bars = []
    curr_date = start_date
    p = base_price
    for _ in range(count):
        while curr_date.weekday() >= 5:  # Skip weekends
            curr_date += timedelta(days=1)
        o = round(p, 2)
        c = round(p + trend, 2)
        h = round(max(o, c) + daily_vol, 2)
        low_p = round(min(o, c) - daily_vol, 2)
        v = 1500000
        bars.append(DailyBar(
            symbol=symbol,
            date=curr_date,
            open=o,
            high=h,
            low=low_p,
            close=c,
            volume=v,
            finalized=True,
        ))
        p = c
        curr_date += timedelta(days=1)
    return bars


class TestSMA:
    def test_calculate_sma_exact(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert calculate_sma(prices, 5) == 30.0
        assert calculate_sma(prices, 3) == 40.0

    def test_calculate_sma_insufficient_history(self):
        prices = [10.0, 20.0]
        # Requires at least period bars; returns 0.0 if not enough bars
        assert calculate_sma(prices, 5) == 0.0
        assert calculate_sma([], 200) == 0.0


class TestRSI2:
    def test_rsi2_panic_dip(self):
        # 3 consecutive sharp down days should produce RSI(2) < 10.0
        prices = [100.0, 95.0, 90.0, 85.0]
        rsi = calculate_rsi2(prices)
        assert rsi == 0.0  # Zero gains, strictly positive losses -> RSI = 0.0
        assert rsi < 10.0

    def test_rsi2_overbought_exit(self):
        # 3 consecutive sharp up days should produce RSI(2) > 70.0
        prices = [85.0, 90.0, 95.0, 100.0]
        rsi = calculate_rsi2(prices)
        assert rsi == 100.0  # Zero losses, positive gains -> RSI = 100.0
        assert rsi > 70.0

    def test_rsi2_flat_prices(self):
        prices = [50.0, 50.0, 50.0, 50.0]
        rsi = calculate_rsi2(prices)
        assert rsi == 50.0

    def test_rsi2_wilder_smoothing_multi_bar(self):
        prices = [100.0, 102.0, 98.0, 96.0, 95.0]
        rsi = calculate_rsi2(prices)
        assert 0.0 <= rsi <= 100.0
        # After two downward days, RSI(2) should be very low
        assert rsi < 15.0


class TestDailyATR:
    def test_calculate_daily_atr_14(self):
        start = date(2026, 1, 5)
        # 20 bars with High-Low = 4.0, no gaps
        bars = []
        for i in range(20):
            d = start + timedelta(days=i)
            bars.append(DailyBar(
                symbol="TEST",
                date=d,
                open=100.0,
                high=102.0,
                low=98.0,
                close=100.0,
                volume=100000,
            ))
        atr = calculate_daily_atr(bars, 14)
        assert atr == 4.0

    def test_emergency_stop_calculation(self):
        start = date(2026, 1, 5)
        bars = _generate_bars("TEST", start, 20, 100.0, daily_vol=2.0)
        atr = calculate_daily_atr(bars, 14)
        entry_price = 100.0
        stop_mult = 2.5
        stop_price = round(entry_price - (stop_mult * atr), 2)
        # Stop must be strictly below entry price
        assert stop_price < entry_price
        assert round(entry_price - stop_price, 2) == round(2.5 * atr, 2)


class TestRelativeStrength60d:
    def test_outperforming_stock(self):
        start = date(2026, 1, 5)
        # 65 trading days
        # Stock rallies +20%
        stock_bars = _generate_bars("LRCX", start, 65, 100.0, trend=0.35, daily_vol=1.0)
        # QQQ rallies +5%
        qqq_bars = _generate_bars("QQQ", start, 65, 100.0, trend=0.08, daily_vol=0.5)

        stock_ret, qqq_ret, passed = calculate_relative_strength_60d(stock_bars, qqq_bars, 60)
        assert passed is True
        assert stock_ret > qqq_ret

    def test_underperforming_stock(self):
        start = date(2026, 1, 5)
        stock_bars = _generate_bars("MU", start, 65, 100.0, trend=-0.10, daily_vol=1.0)
        qqq_bars = _generate_bars("QQQ", start, 65, 100.0, trend=0.15, daily_vol=0.5)

        stock_ret, qqq_ret, passed = calculate_relative_strength_60d(stock_bars, qqq_bars, 60)
        assert passed is False
        assert stock_ret < qqq_ret

    def test_insufficient_aligned_bars(self):
        start = date(2026, 1, 5)
        stock_bars = _generate_bars("AMD", start, 30, 100.0)
        qqq_bars = _generate_bars("QQQ", start, 30, 100.0)

        stock_ret, qqq_ret, passed = calculate_relative_strength_60d(stock_bars, qqq_bars, 60)
        assert passed is False


class TestQualificationAndExits:
    def test_swing_qualification_all_rules_pass(self):
        start = date(2025, 1, 5)
        # 210 bars ensuring 200 SMA can be computed
        stock_bars = _generate_bars("LRCX", start, 208, 100.0, trend=0.5)
        # Dip the last 2 days so RSI(2) < 10, but close stays above 200 SMA
        last_c = stock_bars[-1].close
        stock_bars.append(DailyBar("LRCX", start + timedelta(days=210), last_c, last_c + 1, last_c - 10, last_c - 9, 1000000))
        stock_bars.append(DailyBar("LRCX", start + timedelta(days=211), last_c - 9, last_c - 8, last_c - 19, last_c - 18, 1000000))

        qqq_bars = _generate_bars("QQQ", start, 210, 100.0, trend=0.05)

        res = evaluate_swing_qualification(
            symbol="LRCX",
            stock_bars=stock_bars,
            qqq_bars=qqq_bars,
            earnings_blackout=False,
        )
        assert res.rule_1_macro_floor is True
        assert res.rule_2_relative_strength is True
        assert res.rule_3_panic_dip is True
        assert res.rule_4_no_earnings is True
        assert res.qualified is True
        assert len(res.rejection_reasons) == 0

    def test_swing_qualification_fails_on_earnings_blackout(self):
        start = date(2025, 1, 5)
        stock_bars = _generate_bars("KLAC", start, 210, 100.0, trend=0.5)
        qqq_bars = _generate_bars("QQQ", start, 210, 100.0, trend=0.05)

        res = evaluate_swing_qualification(
            symbol="KLAC",
            stock_bars=stock_bars,
            qqq_bars=qqq_bars,
            earnings_blackout=True,  # Vetoed by 48h blackout
        )
        assert res.rule_4_no_earnings is False
        assert res.qualified is False
        assert "EARNINGS_BLACKOUT_ACTIVE" in res.rejection_reasons

    def test_swing_exit_conditions(self):
        start = date(2026, 1, 5)
        bars = _generate_bars("GS", start, 10, 100.0, trend=1.0)

        # 1. 5-day SMA cross exit (close > 5 SMA)
        exit_sma = evaluate_swing_exit("GS", bars, holding_days=1, earnings_tomorrow=False)
        assert exit_sma.exit_5_sma is True
        assert exit_sma.should_exit is True

        # 2. Time stop exit (5 days held)
        exit_time = evaluate_swing_exit("GS", bars[:3], holding_days=5, earnings_tomorrow=False)
        assert exit_time.exit_time_stop is True
        assert exit_time.should_exit is True

        # 3. Earnings tomorrow exit
        exit_earn = evaluate_swing_exit("GS", bars[:3], holding_days=1, earnings_tomorrow=True)
        assert exit_earn.exit_earnings is True
        assert exit_earn.should_exit is True


class TestZeroLookaheadGuarantee:
    def test_zero_lookahead_with_as_of_filter(self):
        start = date(2025, 1, 5)
        bars = _generate_bars("LRCX", start, 220, 100.0, trend=0.2)
        qqq = _generate_bars("QQQ", start, 220, 100.0, trend=0.1)

        store = DailyBarStore()
        for b in bars:
            store.append_bar(b)
        for b in qqq:
            store.append_bar(b)

        cutoff_date = bars[205].date

        # Evaluate strictly as of cutoff_date
        past_bars = store.get_bars("LRCX", as_of=cutoff_date)
        past_qqq = store.get_bars("QQQ", as_of=cutoff_date)
        res1 = evaluate_swing_qualification("LRCX", past_bars, past_qqq, earnings_blackout=False)

        # Mutate future bars (bars[206..219]) to wild values
        for i in range(206, len(bars)):
            mutated = DailyBar(
                symbol="LRCX",
                date=bars[i].date,
                open=9999.0,
                high=9999.0,
                low=1.0,
                close=9999.0,
                volume=99999999,
                finalized=True,
            )
            store.append_bar(mutated)

        # Re-evaluate as of cutoff_date
        past_bars_after_mutation = store.get_bars("LRCX", as_of=cutoff_date)
        past_qqq_after_mutation = store.get_bars("QQQ", as_of=cutoff_date)
        res2 = evaluate_swing_qualification("LRCX", past_bars_after_mutation, past_qqq_after_mutation, earnings_blackout=False)

        # Invariant: Evaluation as of cutoff_date MUST BE EXACTLY IDENTICAL
        assert res1.close == res2.close
        assert res1.sma_200 == res2.sma_200
        assert res1.rsi_2 == res2.rsi_2
        assert res1.rs_stock_60d == res2.rs_stock_60d
        assert res1.daily_atr_14 == res2.daily_atr_14
        assert res1.qualified == res2.qualified


class TestSeedFixturesAndAggregator:
    def test_daily_bars_seed_fixture_loaded(self):
        seed_path = Path("backend/app/data/daily_bars_seed.json")
        assert seed_path.is_file(), "Seed fixture backend/app/data/daily_bars_seed.json must exist"

        store = DailyBarStore(str(seed_path))
        expected_symbols = ["LRCX", "KLAC", "MU", "AMD", "GS", "QQQ"]
        for sym in expected_symbols:
            bars = store.get_bars(sym)
            assert len(bars) >= 250, f"{sym} must have at least 250 historical bars, got {len(bars)}"
            # Verify bars are chronological
            for idx in range(1, len(bars)):
                assert bars[idx].date > bars[idx - 1].date, f"{sym} bars out of order at {bars[idx].date}"

    def test_daily_bar_aggregator_intraday_accumulation(self):
        store = DailyBarStore()
        aggregator = DailyBarAggregator(store)
        session_d = date(2026, 9, 23)

        # Minute 1 (09:30)
        aggregator.on_minute_bar(BarEvent(
            symbol="AMD",
            timestamp=datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc),
            open=150.0,
            high=151.0,
            low=149.5,
            close=150.5,
            volume=50000,
        ))

        # Minute 2 (09:31)
        aggregator.on_minute_bar(BarEvent(
            symbol="AMD",
            timestamp=datetime(2026, 9, 23, 9, 31, tzinfo=timezone.utc),
            open=150.5,
            high=152.0,  # New high
            low=150.0,
            close=151.8,
            volume=40000,
        ))

        # Minute 3 (09:32)
        aggregator.on_minute_bar(BarEvent(
            symbol="AMD",
            timestamp=datetime(2026, 9, 23, 9, 32, tzinfo=timezone.utc),
            open=151.8,
            high=151.9,
            low=148.0,  # New low
            close=149.0,  # Final close
            volume=60000,
        ))

        in_flight = aggregator.get_in_flight_bar("AMD")
        assert in_flight is not None
        assert in_flight.open == 150.0
        assert in_flight.high == 152.0
        assert in_flight.low == 148.0
        assert in_flight.close == 149.0
        assert in_flight.volume == 150000
        assert in_flight.finalized is False

        # Finalize at 16:00 close
        finalized = aggregator.finalize_daily_bar("AMD", session_d)
        assert finalized is not None
        assert finalized.finalized is True
        assert finalized.open == 150.0
        assert finalized.close == 149.0

        # Committed to store
        stored_bar = store.get_latest_bar("AMD")
        assert stored_bar is not None
        assert stored_bar.close == 149.0
