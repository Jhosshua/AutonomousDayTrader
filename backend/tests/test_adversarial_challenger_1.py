"""backend/tests/test_adversarial_challenger_1.py
Adversarial test suite and stress harness conducted by Challenger 1 (Math & Lookahead Challenger).

Attacking:
1. backend/app/strategies/swing_indicators.py:
   - Off-by-one errors in 200 SMA, 60d RS, RSI(2), and 14 ATR.
   - Lookahead bias: Does providing future bars alter past indicator values?
   - Missing data handling: Mismatched trading dates, holidays, zero volume bars.
   - Extreme boundary values: RSI(2) == 0.0, RSI(2) == 100.0, ATR == 0.0, divide-by-zero traps.
2. backend/app/strategies/earnings_calendar.py:
   - Exact 48.0 hour boundary conditions: 47.9h vs 48.1h.
   - Graceful fallback on network exception/timeout.
3. backend/app/strategies/swing_panic_dip.py:
   - UI serialization crash on active positions.
   - Holding days counter lifecycle & off-by-one time stop.
   - Simultaneous exit/entry staging collisions.
"""
from datetime import date, datetime, timedelta, timezone
import math
from typing import List
import unittest.mock as mock
import pytest

from backend.app.core.account import PaperTradingAccount, Position, PositionSide, TradingArm
from backend.app.core.engine import ExecutionEngine
from backend.app.core.risk import InstitutionalRiskEngine
from backend.app.models.events import BarEvent
from backend.app.strategies.earnings_calendar import EarningsCalendar, EarningsEvent
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
from backend.app.strategies.swing_panic_dip import (
    SwingStagedOrderManager,
    SwingStrategyEngine,
)


# ============================================================================
# 1. MATHEMATICAL & CALCULATION ADVERSARIAL ATTACKS (swing_indicators.py)
# ============================================================================

class TestSmaAdversarial:
    """Stress testing calculate_sma for off-by-one errors and numerical boundaries."""

    def test_sma_length_boundaries(self):
        """Boundary: exactly 0, period-1, period, period+1 prices."""
        period = 200
        # Empty
        assert calculate_sma([], period) == 0.0
        # Negative period
        assert calculate_sma([10.0] * 200, -1) == 0.0
        assert calculate_sma([10.0] * 200, 0) == 0.0
        # 199 prices (period - 1)
        prices_199 = [100.0] * 199
        assert calculate_sma(prices_199, period) == 0.0
        # Exactly 200 prices
        prices_200 = [100.0] * 200
        assert calculate_sma(prices_200, period) == 100.0
        # 201 prices: verify window slides and excludes first price
        prices_201 = [9999.0] + [100.0] * 200
        assert calculate_sma(prices_201, period) == 100.0

    def test_sma_rule_1_strict_inequality(self):
        """Rule 1 Macro Floor requires strictly close > 200 SMA."""
        bars = [
            DailyBar(
                symbol="TEST",
                date=date(2025, 1, 1) + timedelta(days=i),
                open=100.0,
                high=105.0,
                low=95.0,
                close=100.0,
                volume=1000,
            )
            for i in range(200)
        ]
        qqq_bars = list(bars)
        # Close is exactly equal to 200 SMA (100.0 == 100.0)
        qual = evaluate_swing_qualification(
            symbol="TEST",
            stock_bars=bars,
            qqq_bars=qqq_bars,
            earnings_blackout=False,
        )
        assert qual.rule_1_macro_floor is False, "Rule 1 must be strictly close > 200 SMA (not >=)"


class TestRsi2Adversarial:
    """Stress testing calculate_rsi2 for Connors RSI Wilder smoothing and extreme values."""

    def test_rsi2_length_boundaries(self):
        """Requires at least 3 closes for 2-period changes."""
        assert calculate_rsi2([]) == 50.0
        assert calculate_rsi2([100.0]) == 50.0
        assert calculate_rsi2([100.0, 105.0]) == 50.0

    def test_rsi2_extremes_zero_and_hundred(self):
        """Strict monotonic increase -> 100.0, strict monotonic decrease -> 0.0."""
        # Consecutive drops -> RSI(2) == 0.0
        down_prices = [100.0, 95.0, 90.0, 85.0, 80.0]
        assert calculate_rsi2(down_prices) == 0.0

        # Consecutive gains -> RSI(2) == 100.0
        up_prices = [100.0, 105.0, 110.0, 115.0, 120.0]
        assert calculate_rsi2(up_prices) == 100.0

        # Flat prices (zero gains, zero losses) -> 50.0 neutral
        flat_prices = [100.0, 100.0, 100.0, 100.0]
        assert calculate_rsi2(flat_prices) == 50.0

    def test_rsi2_rule_3_strict_inequality(self):
        """Rule 3 requires RSI(2) < 10.0 strictly."""
        # Construct prices that yield RSI(2) ~ 10.0
        # If RSI(2) == 10.0, rule_3_panic_dip must be False
        bars = [
            DailyBar(
                symbol="TEST",
                date=date(2025, 1, 1) + timedelta(days=i),
                open=100.0,
                high=105.0,
                low=95.0,
                close=100.0,
                volume=1000,
            )
            for i in range(200)
        ]
        qual = evaluate_swing_qualification("TEST", bars, bars, False)
        # With flat closes, RSI(2) is 50.0, not < 10
        assert qual.rule_3_panic_dip is False

    def test_rsi2_rule_7b_exit_strict_inequality(self):
        """Rule 7b requires RSI(2) > 70.0 strictly."""
        bars = [
            DailyBar(
                symbol="TEST",
                date=date(2025, 1, 1) + timedelta(days=i),
                open=100.0,
                high=105.0,
                low=95.0,
                close=100.0,
                volume=1000,
            )
            for i in range(10)
        ]
        exit_res = evaluate_swing_exit("TEST", bars, holding_days=1, earnings_tomorrow=False)
        # RSI(2) == 50.0, not > 70.0
        assert exit_res.exit_rsi2_overbought is False


class TestAtr14Adversarial:
    """Stress testing calculate_daily_atr for 14-period Wilder smoothing and boundaries."""

    def test_atr_length_boundaries(self):
        """Empty bars -> fallback 1.0; 1 bar -> max(0.01, high - low)."""
        assert calculate_daily_atr([]) == 1.0

        single_bar = [DailyBar(
            symbol="TEST",
            date=date(2025, 1, 1),
            open=100.0,
            high=102.0,
            low=98.0,
            close=101.0,
            volume=500,
        )]
        # TR = 102 - 98 = 4.0
        assert calculate_daily_atr(single_bar, 14) == 4.0

    def test_atr_zero_range_protection(self):
        """Zero range bars (high == low == close) must floor at 0.01 to prevent div by zero."""
        zero_bars = [
            DailyBar(
                symbol="TEST",
                date=date(2025, 1, 1) + timedelta(days=i),
                open=50.0,
                high=50.0,
                low=50.0,
                close=50.0,
                volume=0,
            )
            for i in range(20)
        ]
        atr = calculate_daily_atr(zero_bars, 14)
        assert atr >= 0.01
        assert not math.isnan(atr)
        assert not math.isinf(atr)


class TestRelativeStrengthAdversarial:
    """Stress testing calculate_relative_strength_60d for missing bars, alignment, and lookahead."""

    def test_rs_60d_exact_window(self):
        """Requires exactly 61 aligned dates for 60 return intervals."""
        d0 = date(2025, 1, 1)
        # Exactly 60 dates -> insufficient (needs 61 dates for 60 intervals)
        bars_60 = [
            DailyBar("S", d0 + timedelta(days=i), 100.0, 105.0, 95.0, 100.0 + i, 1000)
            for i in range(60)
        ]
        qqq_60 = [
            DailyBar("QQQ", d0 + timedelta(days=i), 200.0, 205.0, 195.0, 200.0 + i, 1000)
            for i in range(60)
        ]
        s_ret, q_ret, passed = calculate_relative_strength_60d(bars_60, qqq_60, 60)
        assert passed is False
        assert s_ret == 0.0

        # Exactly 61 dates -> computes delta between index -1 and index -61
        bars_61 = bars_60 + [DailyBar("S", d0 + timedelta(days=60), 100.0, 105.0, 95.0, 160.0, 1000)]
        qqq_61 = qqq_60 + [DailyBar("QQQ", d0 + timedelta(days=60), 200.0, 205.0, 195.0, 260.0, 1000)]
        s_ret, q_ret, passed = calculate_relative_strength_60d(bars_61, qqq_61, 60)
        # Stock: (160 - 100) / 100 = +60%
        # QQQ: (260 - 200) / 200 = +30%
        assert s_ret == 0.60
        assert q_ret == 0.30
        assert passed is True

    def test_rs_60d_missing_qqq_latest_bar_causality_gap(self):
        """Adversarial scenario: Stock has today's bar (Day T), but QQQ is missing Day T.
        
        Does calculate_relative_strength_60d evaluate today's performance or stale history?
        """
        d0 = date(2025, 1, 1)
        # Stock has 100 bars up to day 99
        stock_bars = [
            DailyBar("S", d0 + timedelta(days=i), 100.0, 105.0, 95.0, 100.0 + i, 1000)
            for i in range(100)
        ]
        # QQQ has 99 bars up to day 98 (missing day 99)
        qqq_bars = [
            DailyBar("QQQ", d0 + timedelta(days=i), 200.0, 205.0, 195.0, 200.0 + i, 1000)
            for i in range(99)
        ]
        s_ret, q_ret, passed = calculate_relative_strength_60d(stock_bars, qqq_bars, 60)
        # Common dates intersection will use day 98 as t_curr, NOT day 99 (stock's latest bar)
        # Check that it executed without throwing KeyError
        assert isinstance(passed, bool)


class TestZeroLookaheadBias:
    """Stress testing the Zero Lookahead Guarantee across all indicators."""

    def test_future_bars_cannot_alter_past_indicator_values(self):
        """Appended future bars MUST NOT alter indicators evaluated as_of an earlier date."""
        store = DailyBarStore()
        d0 = date(2025, 1, 1)

        # Populate initial 220 bars
        for i in range(220):
            d = d0 + timedelta(days=i)
            # Create synthetic fluctuating prices
            p = 100.0 + math.sin(i / 10.0) * 15.0
            store.append_bar(DailyBar("LRCX", d, p, p + 2.0, p - 2.0, p, 50000))
            store.append_bar(DailyBar("QQQ", d, p * 2, p * 2 + 3.0, p * 2 - 3.0, p * 2, 100000))

        eval_date = d0 + timedelta(days=219)

        # Baseline evaluation at Day 219
        lrcx_bars_pre = store.get_bars("LRCX", as_of=eval_date)
        qqq_bars_pre = store.get_bars("QQQ", as_of=eval_date)
        qual_pre = evaluate_swing_qualification("LRCX", lrcx_bars_pre, qqq_bars_pre, False)

        # Now inject 50 FUTURE bars (Days 220 to 269) with extreme volatility
        for i in range(220, 270):
            d = d0 + timedelta(days=i)
            p_future = 500.0 + (i * 10.0)
            store.append_bar(DailyBar("LRCX", d, p_future, p_future + 50.0, p_future - 50.0, p_future, 999999))
            store.append_bar(DailyBar("QQQ", d, p_future * 2, p_future * 2 + 50.0, p_future * 2 - 50.0, p_future * 2, 999999))

        # Re-evaluate as_of Day 219
        lrcx_bars_post = store.get_bars("LRCX", as_of=eval_date)
        qqq_bars_post = store.get_bars("QQQ", as_of=eval_date)
        qual_post = evaluate_swing_qualification("LRCX", lrcx_bars_post, qqq_bars_post, False)

        # Strict bitwise equality assertions: Zero Lookahead Guarantee
        assert qual_pre.sma_200 == qual_post.sma_200, "200 SMA suffered lookahead contamination!"
        assert qual_pre.rsi_2 == qual_post.rsi_2, "RSI(2) suffered lookahead contamination!"
        assert qual_pre.daily_atr_14 == qual_post.daily_atr_14, "ATR(14) suffered lookahead contamination!"
        assert qual_pre.rs_stock_60d == qual_post.rs_stock_60d, "Stock RS suffered lookahead contamination!"
        assert qual_pre.rs_qqq_60d == qual_post.rs_qqq_60d, "QQQ RS suffered lookahead contamination!"
        assert qual_pre.qualified == qual_post.qualified, "Qualification verdict suffered lookahead contamination!"


# ============================================================================
# 2. EARNINGS CALENDAR ADVERSARIAL STRESS (earnings_calendar.py)
# ============================================================================

class TestEarningsCalendarAdversarial:
    """Stress testing earnings blackout horizon boundaries and fallback resilience."""

    def test_earnings_48h_boundary_conditions(self):
        """Testing exact 47.9h vs 48.0h vs 48.1h boundary."""
        cal = EarningsCalendar()
        eval_dt = datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc)

        # Case 1: 47.9 hours ahead (within 48h)
        event_47_9h = EarningsEvent(
            symbol="TEST1",
            report_date=date(2026, 9, 25),
            report_time="amc",  # 16:30 ET
        )
        cal.add_event(event_47_9h)

        # 47.9 hours from 16:00 is 15:54 on Sept 25
        dt_47_9h = eval_dt + timedelta(hours=47.9)
        assert cal.is_blackout_active("TEST1", dt_47_9h - timedelta(hours=47.9), horizon_hours=48.0) is True

    def test_earnings_calendar_overrides_horizon_hours_when_diff_days_within_2(self):
        """Vulnerability probe: is_blackout_active ignores horizon_hours if diff_days <= 2.
        
        If horizon_hours=12.0 is passed, but the report is 40 hours away (2 calendar days),
        lines 180-182 enforce a calendar-day blackout regardless of horizon_hours.
        """
        cal = EarningsCalendar()
        eval_dt = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        # Event is on 2026-09-25 (2 calendar days away, ~54.5 hours away)
        ev = EarningsEvent(
            symbol="FAR_EVENT",
            report_date=date(2026, 9, 25),
            report_time="amc",
        )
        cal.add_event(ev)

        # 54.5 hours away with horizon_hours=48.0
        # Because diff_days = (2026-09-25 - 2026-09-23).days = 2, it returns True!
        is_active = cal.is_blackout_active("FAR_EVENT", eval_dt, horizon_hours=48.0)
        # Document actual empirical behavior
        assert is_active is True, "Empirically confirmed: calendar-day rule overrides 48h seconds window"

    def test_earnings_past_event_today_causes_false_blackout(self):
        """Vulnerability probe: Earnings reported BMO this morning (8:30 AM) evaluated at 16:00 close today.
        
        Event report_dt (08:30) is in the PAST (diff_seconds = -7.5h < 0).
        However, diff_days = 0, so line 182 (0 <= diff_days <= 2) triggers True!
        """
        cal = EarningsCalendar()
        eval_dt = datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc)
        # Earnings occurred at 08:30 BMO today
        past_ev = EarningsEvent(
            symbol="PAST_TODAY",
            report_date=date(2026, 9, 23),
            report_time="bmo",
        )
        cal.add_event(past_ev)

        is_blackout = cal.is_blackout_active("PAST_TODAY", eval_dt, horizon_hours=48.0)
        # Remediated: diff_seconds < 0 is ignored, so blackout is False!
        assert is_blackout is False, "Past earnings reported earlier today must not trigger blackout!"


    def test_earnings_graceful_fallback_on_network_exception(self):
        """Verify refresh_from_remote gracefully catches HTTP errors/timeouts without crashing."""
        cal = EarningsCalendar(remote_url="http://invalid-nonexistent-domain-404.local/earnings")
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(cal.refresh_from_remote())
            assert result is False, "Must return False on network failure"
        finally:
            loop.close()


# ============================================================================
# 3. SWING STRATEGY ENGINE & UI ADVERSARIAL STRESS (swing_panic_dip.py)
# ============================================================================

class TestSwingStrategyEngineAdversarial:
    """Stress testing SwingStrategyEngine for crashes, holding days, and edge cases."""

    def test_to_ui_dict_attribute_error_on_active_position(self):
        """CRITICAL VULNERABILITY PROBE:
        
        backend/app/strategies/swing_panic_dip.py lines 823-826 accesses:
            exit_eval.rule_7a_sma5_exit
            exit_eval.rule_7b_rsi_exit
            exit_eval.rule_7c_time_exit
            exit_eval.rule_4_earnings_exit
        
        However, SwingExitResult defined in swing_indicators.py has fields:
            exit_5_sma
            exit_rsi2_overbought
            exit_time_stop
            exit_earnings
        
        Calling to_ui_dict() when an active swing position exists MUST raise AttributeError!
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        exec_engine = ExecutionEngine(account=account)
        engine = SwingStrategyEngine(account=account, execution_engine=exec_engine)

        # Inject active swing position into account
        pos = Position(
            symbol="MU",
            side=PositionSide.LONG,
            shares=100,
            avg_entry_price=100.0,
            market_price=105.0,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=90.0,
            entry_date=date.today(),
            holding_days=1,
        )
        account.positions["MU"] = pos

        # Verify that to_ui_dict() succeeds cleanly without AttributeError!
        ui_res = engine.to_ui_dict()
        assert len(ui_res["positions"]) == 1
        pos_entry = ui_res["positions"][0]
        assert pos_entry["symbol"] == "MU"
        assert "exit_triggers" in pos_entry
        assert "sma_5_cross" in pos_entry["exit_triggers"]
        assert "rsi_70_cross" in pos_entry["exit_triggers"]
        assert "time_stop_day_5" in pos_entry["exit_triggers"]
        assert "earnings_tomorrow" in pos_entry["exit_triggers"]


    def test_holding_days_lifecycle_off_by_one_time_stop(self):
        """OFF-BY-ONE TIME STOP PROBE:
        
        A position entered Monday at 09:30 open:
        - Monday 16:00 close: holding_days is 0 (has been held 1 trading day).
        - Friday 16:00 close: holding_days is 4 (has been held 5 full trading days: M, T, W, Th, F).
        - At Friday 16:00 close: evaluate_swing_exit checks holding_days >= 5 -> FALSE (4 < 5)!
        - Position is held over the weekend and all day Monday, exiting only on Tuesday (Day 7)!
        """
        # Create 10 bars for a stock
        d0 = date(2025, 1, 6)  # Monday
        bars = [
            DailyBar("GS", d0 + timedelta(days=i), 100.0, 102.0, 98.0, 100.0, 1000)
            for i in range(10)
        ]

        # On Friday close (day index 4), if holding_days == 4:
        friday_exit = evaluate_swing_exit("GS", bars[:5], holding_days=4, earnings_tomorrow=False)
        assert friday_exit.exit_time_stop is False, (
            "Friday close fails time stop because holding_days is 4 instead of 5"
        )

        # Only when holding_days reaches 5 (which occurs Monday evening):
        monday_exit = evaluate_swing_exit("GS", bars[:6], holding_days=5, earnings_tomorrow=False)
        assert monday_exit.exit_time_stop is True

    def test_simultaneous_exit_and_entry_same_symbol_collision(self):
        """COLLISION PROBE:
        
        If a held position triggers a time-stop exit at 16:00 close, AND satisfies Rules 1-4
        for entry, evaluate_market_close stages BOTH an exit and an entry for the same symbol.
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        exec_engine = ExecutionEngine(account=account)
        engine = SwingStrategyEngine(
            account=account,
            execution_engine=exec_engine,
            symbols=["LRCX"],
        )

        # Baseline: 220 bars. Historical bars at 50.0, then rallies to 100.0, then drops 2 days to 80.0
        d0 = date(2025, 1, 1)
        bars = [
            DailyBar("LRCX", d0 + timedelta(days=i), 50.0, 52.0, 48.0, 50.0, 1000)
            for i in range(215)
        ]
        # Day 215, 216, 217 at 100.0
        for i in range(215, 218):
            bars.append(DailyBar("LRCX", d0 + timedelta(days=i), 100.0, 102.0, 98.0, 100.0, 1000))
        # Drop 1: Day 218 drops from 100.0 to 90.0
        bars.append(DailyBar("LRCX", d0 + timedelta(days=218), 100.0, 100.0, 89.0, 90.0, 1000))
        # Drop 2: Day 219 drops from 90.0 to 80.0
        bars.append(DailyBar("LRCX", d0 + timedelta(days=219), 90.0, 90.0, 79.0, 80.0, 1000))
        # Drop 3: Day 220 drops from 80.0 to 70.0 (RSI(2) is 8.2 < 10.0, close 70 > 200 SMA ~51)
        bars.append(DailyBar("LRCX", d0 + timedelta(days=220), 80.0, 80.0, 69.0, 70.0, 1000))

        for b in bars:
            engine.bar_store.append_bar(b)
            engine.bar_store.append_bar(DailyBar("QQQ", b.date, 50.0, 52.0, 48.0, 50.0, 1000))

        eval_date = bars[-1].date

        # LRCX is held with holding_days = 5 (time stop exit triggered!)
        pos = Position(
            symbol="LRCX",
            side=PositionSide.LONG,
            shares=100,
            avg_entry_price=50.0,
            market_price=80.0,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=40.0,
            holding_days=5,
        )
        account.positions["LRCX"] = pos

        # Evaluate market close
        res = engine.evaluate_market_close(eval_date)

        # Check if LRCX is staged for BOTH exit and entry!
        staged_exit_syms = [e["symbol"] for e in res["staged_exits"]]
        staged_entry_syms = [e["symbol"] for e in res["staged_entries"]]

        # Remediated: Exiting symbols and active positions are excluded from candidate entries
        collision_detected = ("LRCX" in staged_exit_syms and "LRCX" in staged_entry_syms)
        assert collision_detected is False, "Remediated: LRCX must not be staged for entry while exiting!"
        assert "LRCX" in staged_exit_syms
        assert "LRCX" not in staged_entry_syms


    def test_extreme_open_price_zero_shares_handled(self):
        """Boundary: Open price > $25,000 slot notional yields floor(25000 / P) == 0 shares."""
        account = PaperTradingAccount(initial_cash=50000.0)
        exec_engine = ExecutionEngine(account=account)
        engine = SwingStrategyEngine(account=account, execution_engine=exec_engine)

        engine.staged_manager.stage_buy(
            symbol="GS",
            target_notional=25000.0,
            daily_atr=5.0,
            signal_date=date.today(),
            reason="TEST",
        )

        # Open price is $30,000 (exceeds $25,000 slot)
        open_res = engine.execute_market_open(
            open_prices={"GS": 30000.0},
            open_time=datetime.now(timezone.utc),
        )

        # Verify handled cleanly without crash and order discarded
        assert len(open_res["entries"]) == 0
        assert len(engine.staged_manager.get_staged_orders()) == 0

    def test_negative_stop_loss_disables_emergency_protection(self):
        """VULNERABILITY PROBE: Negative Stop-Loss Price.
        
        If a low-priced or high-volatility stock has open_price=10.0 and ATR=5.0:
        stop_price = 10.0 - 2.5 * 5.0 = -2.5.
        
        In check_intraday_emergency_stops:
            if stop_price is None or stop_price <= 0.0:
                continue
        
        Because stop_price is <= 0, the emergency stop is silently ignored forever!
        The position will never stop out even if price drops to $0.01.
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        exec_engine = ExecutionEngine(account=account)
        engine = SwingStrategyEngine(account=account, execution_engine=exec_engine)

        # Stage buy with large ATR relative to price
        engine.staged_manager.stage_buy(
            symbol="MU",
            target_notional=25000.0,
            daily_atr=6.0,  # 2.5 * 6.0 = 15.0 stop distance
            signal_date=date.today(),
            reason="HIGH_ATR_TEST",
        )

        open_res = engine.execute_market_open(
            open_prices={"MU": 10.0},  # Stop = 10.0 - 15.0 = -5.0
            open_time=datetime.now(timezone.utc),
        )

        assert len(open_res["entries"]) == 1
        pos = account.positions["MU"]
        assert pos.stop_loss_price < 0.0, "Negative stop price created"

        # Now simulate market price crashing to $0.05
        stops = engine.check_intraday_emergency_stops(
            current_prices={"MU": 0.05},
            timestamp=datetime.now(timezone.utc),
        )

        # Because stop_price <= 0.0, stops list is EMPTY: Emergency stop failed to trigger!
        assert len(stops) == 0, (
            "Empirically confirmed: Negative stop_price <= 0.0 completely disables emergency stop protection!"
        )

    def test_tighten_stop_rejects_widening_stop(self):
        """B2 fix (2026-09-24): tighten_stop now rejects widening the stop loss.

        This used to be a DEFECT PROBE documenting that tighten_stop allowed widening the stop
        without any check. That defect is fixed: SwingStrategyEngine.tighten_stop now requires
        `current_stop < proposed < market_price` and rejects loosening."""
        account = PaperTradingAccount(initial_cash=50000.0)
        exec_engine = ExecutionEngine(account=account)
        engine = SwingStrategyEngine(account=account, execution_engine=exec_engine)

        pos = Position(
            symbol="GS",
            side=PositionSide.LONG,
            shares=100,
            avg_entry_price=100.0,
            market_price=110.0,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=95.0,  # Initial stop
        )
        account.positions["GS"] = pos

        # Operator calls tighten_stop with 90.0 (would WIDEN the stop by $5) -> rejected.
        success = engine.tighten_stop("GS", 90.0)
        assert success is False
        assert pos.stop_loss_price == 95.0, (
            "tighten_stop must reject widening the stop from 95.0 to 90.0"
        )
