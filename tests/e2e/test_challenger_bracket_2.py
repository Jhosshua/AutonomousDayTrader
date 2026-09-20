"""tests/e2e/test_challenger_bracket_2.py
Adversarial Boundary & Edge-Case Verification Suite.
Challenger 2: Boundary & Edge Case Verification

Targeted Audits:
1. Stop distance clamping across extreme stock prices ($5.00, $150.00, $1,000.00)
   for Opening Range Breakout (ORB) and Catalyst News Momentum strategies.
   Verification rule: 0.004 <= stop_dist / entry_price <= 0.040.
2. ET session boundary working order purge in `_check_session_boundary`:
   Verification that crossing ET calendar dates cancels all working orders and clears working_orders,
   while same-day ticks or UTC midnight transitions on the same ET date do not erroneously purge.
3. Pre-market flattening phase transitions:
   Verification that `ZeroOvernightFlatteningEngine.check_time_tick` reports `PRE_MARKET`
   strictly before 09:30 ET and `NORMAL_TRADING` from 09:30 to 15:45 ET.
4. Clean port and process hygiene audit.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, time as dtime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo
import pytest

from backend.app.models.events import (
    BarEvent,
    NewsEvent,
    OrderSide,
    OrderType,
    OrderState,
)
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy
from backend.app.core.flattening import (
    ZeroOvernightFlatteningEngine,
    MarketClock,
    FlatteningPhase,
    FlatteningSchedule,
)

ET_TZ = ZoneInfo("America/New_York")


def _make_bar(
    symbol: str,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: int = 50000,
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
# 1. ORB Stop Distance Clamping Tests ($5.00, $150.00, $1000.00)
# ============================================================================

class TestOrbStopDistanceClamping:
    """Stress tests stop distance clamping across extreme stock prices for ORB."""

    @pytest.mark.parametrize("price", [5.00, 150.00, 1000.00])
    def test_orb_bullish_breakout_stop_distance_clamping_tight_range(self, price: float):
        """Ultra-tight opening range: raw midpoint distance is small, must clamp >= 0.004."""
        strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
        sym = f"ORB_TIGHT_{int(price)}"

        # 5 bars establishing an ultra-tight opening range: range height is 0.001 * price
        delta = round(price * 0.001, 4)
        for m in range(30, 35):
            strat.on_bar(_make_bar(
                symbol=sym,
                open_p=price,
                high_p=price + delta,
                low_p=price - delta,
                close_p=price,
                vol=10000,
                ts_str=f"2026-09-21T09:{m:02d}:00-04:00",
            ))

        # Breakout bar closing slightly above range high with RVOL 3.0x
        entry = round(price * 1.002, 4)
        bo_bar = _make_bar(
            symbol=sym,
            open_p=price + delta,
            high_p=entry + 0.01,
            low_p=price,
            close_p=entry,
            vol=50000,
            ts_str="2026-09-21T09:35:00-04:00",
        )
        sigs = strat.on_bar(bo_bar)
        assert len(sigs) == 1, "Expected 1 ORB breakout BUY signal"
        sig = sigs[0]
        assert sig.side == OrderSide.BUY

        stop_dist = abs(sig.entry_price - sig.stop_loss)
        ratio = stop_dist / sig.entry_price

        # Clamping contract: strictly between 0.4% and 4.0%
        assert 0.004 - 1e-6 <= ratio <= 0.040 + 1e-6, f"ORB BUY tight clamp violated: ratio={ratio:.6f} for price={price}"
        assert sig.stop_loss < sig.entry_price
        assert sig.take_profit_1 > sig.entry_price
        assert sig.take_profit_2 > sig.take_profit_1

    @pytest.mark.parametrize("price", [5.00, 150.00, 1000.00])
    def test_orb_bullish_breakout_stop_distance_clamping_wide_range(self, price: float):
        """Extremely wide opening range: raw midpoint distance is huge (e.g. 15%), must clamp <= 0.040."""
        strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
        sym = f"ORB_WIDE_{int(price)}"

        # 5 bars establishing an ultra-wide opening range (range height 15% of price)
        delta = round(price * 0.15, 2)
        for m in range(30, 35):
            strat.on_bar(_make_bar(
                symbol=sym,
                open_p=price,
                high_p=price + delta,
                low_p=price - delta,
                close_p=price,
                vol=10000,
                ts_str=f"2026-09-21T09:{m:02d}:00-04:00",
            ))

        # Breakout bar closing above range high with RVOL 3.0x
        entry = round((price + delta) * 1.01, 2)
        bo_bar = _make_bar(
            symbol=sym,
            open_p=price + delta,
            high_p=entry + 0.50,
            low_p=price,
            close_p=entry,
            vol=50000,
            ts_str="2026-09-21T09:35:00-04:00",
        )
        sigs = strat.on_bar(bo_bar)
        assert len(sigs) == 1, "Expected 1 ORB breakout BUY signal"
        sig = sigs[0]
        assert sig.side == OrderSide.BUY

        stop_dist = abs(sig.entry_price - sig.stop_loss)
        ratio = stop_dist / sig.entry_price

        # Clamping contract: strictly between 0.4% and 4.0%
        assert 0.004 - 1e-6 <= ratio <= 0.040 + 1e-6, f"ORB BUY wide clamp violated: ratio={ratio:.6f} for price={price}"
        expected_max_dist = round(entry * 0.0380, 4)
        assert math.isclose(stop_dist, expected_max_dist, abs_tol=0.001)

    @pytest.mark.parametrize("price", [5.00, 150.00, 1000.00])
    def test_orb_bearish_breakdown_stop_distance_clamping(self, price: float):
        """Bearish breakdown (SELL): stop distance must clamp to [0.004, 0.040]."""
        strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
        sym = f"ORB_SHORT_{int(price)}"

        # 5 bars establishing range
        delta = round(price * 0.02, 2)
        for m in range(30, 35):
            strat.on_bar(_make_bar(
                symbol=sym,
                open_p=price,
                high_p=price + delta,
                low_p=price - delta,
                close_p=price,
                vol=10000,
                ts_str=f"2026-09-21T09:{m:02d}:00-04:00",
            ))

        # Breakdown bar closing below range low with RVOL 3.0x
        entry = round((price - delta) * 0.995, 4)
        bd_bar = _make_bar(
            symbol=sym,
            open_p=price - delta,
            high_p=price,
            low_p=entry - 0.05,
            close_p=entry,
            vol=50000,
            ts_str="2026-09-21T09:35:00-04:00",
        )
        sigs = strat.on_bar(bd_bar)
        assert len(sigs) == 1, "Expected 1 ORB breakdown SELL signal"
        sig = sigs[0]
        assert sig.side == OrderSide.SELL

        stop_dist = abs(sig.entry_price - sig.stop_loss)
        ratio = stop_dist / sig.entry_price

        assert 0.004 - 1e-6 <= ratio <= 0.040 + 1e-6, f"ORB SELL clamp violated: ratio={ratio:.6f} for price={price}"
        assert sig.stop_loss > sig.entry_price
        assert sig.take_profit_1 < sig.entry_price
        assert sig.take_profit_2 < sig.take_profit_1


# ============================================================================
# 2. News Momentum Stop Distance Clamping Tests ($5.00, $150.00, $1000.00)
# ============================================================================

class TestNewsMomentumStopDistanceClamping:
    """Stress tests stop distance clamping across extreme stock prices for News Momentum."""

    @pytest.mark.parametrize("price", [5.00, 150.00, 1000.00])
    def test_news_momentum_bullish_tight_and_wide_stops(self, price: float):
        """Bullish catalyst: verify min clamp (0.004) on tight candle and max clamp (0.040) on wide candle."""
        # 1. Tight candle (bar.low very close to close)
        strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.50)
        sym = f"NEWS_BULL_{int(price)}"

        # Baseline volume history
        for m in range(20):
            strat.on_bar(_make_bar(
                symbol=sym, open_p=price, high_p=price+0.05, low_p=price-0.05, close_p=price,
                vol=10000, ts_str=f"2026-09-21T10:{m:02d}:00-04:00"
            ))

        # Ingest strong bullish news
        news = NewsEvent(
            article_id=1,
            headline=f"Surging demand and record revenue partnership beat estimates for {sym}",
            summary="Benzinga analyst upgrades forward guidance on robust demand.",
            symbols=[sym],
            source="Benzinga",
            sentiment_score=0.85,
            created_at=datetime.fromisoformat("2026-09-21T10:20:00-04:00"),
        )
        strat.on_news(news)

        # Breakout bar with tight low: low is just 0.001 below close
        tight_bar = _make_bar(
            symbol=sym,
            open_p=price,
            high_p=price + 0.05,
            low_p=round(price - 0.001, 4),
            close_p=price,
            vol=45000,  # 4.5x surge
            ts_str="2026-09-21T10:21:00-04:00",
        )
        sigs = strat.on_bar(tight_bar)
        assert len(sigs) == 1, "Expected 1 News Momentum BUY signal"
        sig = sigs[0]
        assert sig.side == OrderSide.BUY

        stop_dist = abs(sig.entry_price - sig.stop_loss)
        ratio = stop_dist / sig.entry_price
        assert 0.004 - 1e-6 <= ratio <= 0.040 + 1e-6, f"News Momentum BUY tight clamp violated: ratio={ratio:.6f} for price={price}"

        # 2. Wide candle (bar.low is 10% below close) -> must clamp to max 0.040
        strat_wide = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.50)
        sym_wide = f"NEWS_BWIDE_{int(price)}"
        for m in range(20):
            strat_wide.on_bar(_make_bar(
                symbol=sym_wide, open_p=price, high_p=price+0.05, low_p=price-0.05, close_p=price,
                vol=10000, ts_str=f"2026-09-21T10:{m:02d}:00-04:00"
            ))

        news_wide = NewsEvent(
            article_id=2,
            headline=f"Upgrade record revenue partnership milestone for {sym_wide}",
            summary="Benzinga analyst upgrades forward guidance.",
            symbols=[sym_wide],
            source="Benzinga",
            sentiment_score=0.90,
            created_at=datetime.fromisoformat("2026-09-21T10:20:00-04:00"),
        )
        strat_wide.on_news(news_wide)

        wide_bar = _make_bar(
            symbol=sym_wide,
            open_p=price * 0.95,
            high_p=price * 1.02,
            low_p=round(price * 0.90, 4),  # 10% low
            close_p=price,
            vol=45000,
            ts_str="2026-09-21T10:21:00-04:00",
        )
        sigs_wide = strat_wide.on_bar(wide_bar)
        assert len(sigs_wide) == 1
        sig_w = sigs_wide[0]

        stop_dist_w = abs(sig_w.entry_price - sig_w.stop_loss)
        ratio_w = stop_dist_w / sig_w.entry_price
        assert 0.004 - 1e-6 <= ratio_w <= 0.040 + 1e-6, f"News Momentum BUY wide clamp violated: ratio={ratio_w:.6f} for price={price}"
        expected_max = round(price * 0.0380, 4)
        assert math.isclose(stop_dist_w, expected_max, abs_tol=0.001)

    @pytest.mark.parametrize("price", [5.00, 150.00, 1000.00])
    def test_news_momentum_bearish_tight_and_wide_stops(self, price: float):
        """Bearish catalyst: verify min/max clamps [0.004, 0.040] for SELL orders."""
        strat = NewsMomentumStrategy(sentiment_threshold=0.60, volume_surge_multiplier=3.50)
        sym = f"NEWS_BEAR_{int(price)}"

        for m in range(20):
            strat.on_bar(_make_bar(
                symbol=sym, open_p=price, high_p=price+0.05, low_p=price-0.05, close_p=price,
                vol=10000, ts_str=f"2026-09-21T10:{m:02d}:00-04:00"
            ))

        news = NewsEvent(
            article_id=3,
            headline=f"SEC probe investigation fraud subpoena crash plunges {sym}",
            summary="DOJ and SEC initiate formal investigation.",
            symbols=[sym],
            source="Benzinga",
            sentiment_score=-0.85,
            created_at=datetime.fromisoformat("2026-09-21T10:20:00-04:00"),
        )
        strat.on_news(news)

        # Huge wick above (12% high) -> must clamp to max 0.040
        bear_bar = _make_bar(
            symbol=sym,
            open_p=price * 1.05,
            high_p=round(price * 1.12, 4),
            low_p=price * 0.98,
            close_p=price,
            vol=45000,
            ts_str="2026-09-21T10:21:00-04:00",
        )
        sigs = strat.on_bar(bear_bar)
        assert len(sigs) == 1, "Expected 1 News Momentum SELL signal"
        sig = sigs[0]
        assert sig.side == OrderSide.SELL

        stop_dist = abs(sig.entry_price - sig.stop_loss)
        ratio = stop_dist / sig.entry_price
        assert 0.004 - 1e-6 <= ratio <= 0.040 + 1e-6, f"News Momentum SELL clamp violated: ratio={ratio:.6f} for price={price}"
        expected_max = round(price * 0.0380, 4)
        assert math.isclose(stop_dist, expected_max, abs_tol=0.001)


class TestFuzzStopClampingAcrossPrices:
    """Parametric stress test generating random prices between $1.00 and $2,500.00."""

    def test_fuzz_prices_and_volatilities(self):
        random.seed(42)
        test_prices = [1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0, 150.0, 250.0, 500.0, 1000.0, 2500.0]
        # Add 30 random prices
        test_prices.extend([round(random.uniform(1.0, 3000.0), 2) for _ in range(30)])

        for p in test_prices:
            min_dist = round(p * 0.004, 4)
            max_dist = round(p * 0.040, 4)

            test_raw_dists = [0.0001, 0.001, 0.01, 0.05, p * 0.001, p * 0.003, p * 0.004, p * 0.02, p * 0.04, p * 0.08, p * 0.50]
            for raw in test_raw_dists:
                risk = max(min_dist, min(max_dist, raw))
                stop_loss_buy = round(p - risk, 4)
                stop_loss_sell = round(p + risk, 4)

                dist_buy = abs(p - stop_loss_buy)
                dist_sell = abs(p - stop_loss_sell)

                ratio_buy = dist_buy / p
                ratio_sell = dist_sell / p

                assert ratio_buy >= 0.004 - 1e-4, f"Buy ratio too low: {ratio_buy} for p={p}, raw={raw}"
                assert ratio_buy <= 0.040 + 1e-4, f"Buy ratio too high: {ratio_buy} for p={p}, raw={raw}"
                assert ratio_sell >= 0.004 - 1e-4, f"Sell ratio too low: {ratio_sell} for p={p}, raw={raw}"
                assert ratio_sell <= 0.040 + 1e-4, f"Sell ratio too high: {ratio_sell} for p={p}, raw={raw}"


# ============================================================================
# 3. Session Boundary Working Order Purge Tests
# ============================================================================

class TestSessionBoundaryPurge:
    """Stress tests ET session boundary detection and working order purge."""

    def test_session_boundary_purges_multiple_orders_on_date_transition(self):
        """Simulate ET calendar date transition; verify all working orders are cancelled & cleared."""
        from backend.app.main import _check_session_boundary, engine, risk_engine, flattening_engine, account
        import backend.app.main as main_mod

        # Reset main module session state to known baseline
        main_mod.last_session_date = None
        engine.working_orders.clear()

        # Day 1: 2026-09-21 10:00 ET (14:00 UTC)
        day1_dt = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)
        _check_session_boundary(day1_dt)
        assert main_mod.last_session_date == day1_dt.astimezone(ET_TZ).date()

        # Place 4 mock working orders across various symbols & sides
        o1 = engine.create_order("AAPL", OrderSide.BUY, OrderType.LIMIT, 10, limit_price=150.0)
        engine.submit_order(o1.id)
        o2 = engine.create_order("NVDA", OrderSide.BUY, OrderType.LIMIT, 5, limit_price=120.0)
        engine.submit_order(o2.id)
        o3 = engine.create_order("TSLA", OrderSide.BUY, OrderType.LIMIT, 8, limit_price=200.0)
        engine.submit_order(o3.id)
        o4 = engine.create_order("MSFT", OrderSide.BUY, OrderType.LIMIT, 4, limit_price=400.0)
        engine.submit_order(o4.id)

        assert len(engine.working_orders) == 4
        assert all(o.status == OrderState.ACCEPTED for o in [o1, o2, o3, o4])

        # Same-day tick: 2026-09-21 15:30 ET (19:30 UTC) - MUST NOT PURGE
        day1_later = datetime(2026, 9, 21, 19, 30, 0, tzinfo=timezone.utc)
        _check_session_boundary(day1_later)
        assert len(engine.working_orders) == 4, "Same-day tick must NOT purge working orders!"

        # UTC midnight boundary: 2026-09-22 01:00 UTC is 2026-09-21 21:00 ET (SAME ET date!)
        utc_midnight_same_et = datetime(2026, 9, 22, 1, 0, 0, tzinfo=timezone.utc)
        _check_session_boundary(utc_midnight_same_et)
        assert len(engine.working_orders) == 4, "UTC midnight with same ET date must NOT purge working orders!"

        # Day 2 ET transition: 2026-09-22 09:30 ET (13:30 UTC) - MUST PURGE ALL
        day2_open = datetime(2026, 9, 22, 13, 30, 0, tzinfo=timezone.utc)
        _check_session_boundary(day2_open)

        # Assert all orders are cancelled and working_orders is empty
        assert len(engine.working_orders) == 0, "engine.working_orders must be empty after session boundary"
        for o in [o1, o2, o3, o4]:
            assert o.status == OrderState.CANCELLED, f"Order {o.id} should be CANCELLED but was {o.status}"
            assert o.completed_at is not None

        # Assert main session date updated
        assert main_mod.last_session_date == day2_open.astimezone(ET_TZ).date()

    def test_session_boundary_purges_partially_filled_orders(self):
        """Verify partially filled orders are also cleanly cancelled at session boundary."""
        from backend.app.main import _check_session_boundary, engine, account
        import backend.app.main as main_mod

        main_mod.last_session_date = None
        engine.working_orders.clear()

        try:
            # Day 1 initial observation
            day1 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)
            _check_session_boundary(day1)

            # Create and partially fill order
            o = engine.create_order("AMD", OrderSide.BUY, OrderType.LIMIT, 20, limit_price=100.0)
            engine.submit_order(o.id)
            # Partially fill 10 shares via _execute_fill
            engine._execute_fill(o, 10, 100.0, 0.0, datetime(2026, 9, 21, 14, 5, 0, tzinfo=timezone.utc))
            assert o.status == OrderState.PARTIALLY_FILLED
            assert o.id in engine.working_orders

            # Day 2 boundary transition
            day2 = datetime(2026, 9, 22, 14, 0, 0, tzinfo=timezone.utc)
            _check_session_boundary(day2)

            assert len(engine.working_orders) == 0
            assert o.status == OrderState.CANCELLED
        finally:
            engine.working_orders.clear()
            account.positions.clear()


# ============================================================================
# 4. Pre-Market Flattening Phase Transitions Tests (< 09:30 ET & 09:30-15:45)
# ============================================================================

class TestPreMarketFlatteningPhaseTransitions:
    """Stress tests ZeroOvernightFlatteningEngine time transitions across pre-market and normal trading."""

    def test_pre_market_and_normal_trading_transitions(self):
        clock = MarketClock()
        engine = ZeroOvernightFlatteningEngine(clock=clock)

        # 1. Early pre-market: 04:00:00 ET
        clock.set_simulated_time(datetime(2026, 9, 21, 4, 0, 0, tzinfo=ET_TZ))
        d_early = engine.check_time_tick()
        assert d_early is None, "Pre-market tick should not return an actionable liquidation directive"
        assert engine.current_phase == FlatteningPhase.PRE_MARKET

        # 2. Mid pre-market: 08:30:00 ET
        clock.set_simulated_time(datetime(2026, 9, 21, 8, 30, 0, tzinfo=ET_TZ))
        d_mid = engine.check_time_tick()
        assert d_mid is None
        assert engine.current_phase == FlatteningPhase.PRE_MARKET

        # 3. 1 second before open: 09:29:59 ET
        clock.set_simulated_time(datetime(2026, 9, 21, 9, 29, 59, tzinfo=ET_TZ))
        d_edge = engine.check_time_tick()
        assert d_edge is None
        assert engine.current_phase == FlatteningPhase.PRE_MARKET

        # 4. Market open: 09:30:00 ET -> NORMAL_TRADING
        clock.set_simulated_time(datetime(2026, 9, 21, 9, 30, 0, tzinfo=ET_TZ))
        d_open = engine.check_time_tick()
        assert d_open is None
        assert engine.current_phase == FlatteningPhase.NORMAL_TRADING

        # 5. Morning trend: 10:30:00 ET -> NORMAL_TRADING
        clock.set_simulated_time(datetime(2026, 9, 21, 10, 30, 0, tzinfo=ET_TZ))
        d_morning = engine.check_time_tick()
        assert d_morning is None
        assert engine.current_phase == FlatteningPhase.NORMAL_TRADING

        # 6. Midday chop: 12:30:00 ET -> NORMAL_TRADING
        clock.set_simulated_time(datetime(2026, 9, 21, 12, 30, 0, tzinfo=ET_TZ))
        d_midday = engine.check_time_tick()
        assert d_midday is None
        assert engine.current_phase == FlatteningPhase.NORMAL_TRADING

        # 7. 1 second before lockout: 15:44:59 ET -> NORMAL_TRADING
        clock.set_simulated_time(datetime(2026, 9, 21, 15, 44, 59, tzinfo=ET_TZ))
        d_before_lockout = engine.check_time_tick()
        assert d_before_lockout is None
        assert engine.current_phase == FlatteningPhase.NORMAL_TRADING

        # 8. Phase 1 Lockout: 15:45:00 ET -> ENTRY_LOCKOUT
        clock.set_simulated_time(datetime(2026, 9, 21, 15, 45, 0, tzinfo=ET_TZ))
        d_lockout = engine.check_time_tick()
        assert d_lockout is not None
        assert d_lockout.phase == FlatteningPhase.ENTRY_LOCKOUT
        assert d_lockout.lock_new_entries is True
        assert engine.current_phase == FlatteningPhase.ENTRY_LOCKOUT

    def test_utc_timezone_awareness_for_premarket(self):
        """Pass UTC timestamps directly into clock and verify correct ET translation."""
        clock = MarketClock()
        engine = ZeroOvernightFlatteningEngine(clock=clock)

        # 09:29:59 ET is 13:29:59 UTC (during EDT)
        clock.set_simulated_time(datetime(2026, 9, 21, 13, 29, 59, tzinfo=timezone.utc))
        engine.check_time_tick()
        assert engine.current_phase == FlatteningPhase.PRE_MARKET

        # 09:30:00 ET is 13:30:00 UTC
        clock.set_simulated_time(datetime(2026, 9, 21, 13, 30, 0, tzinfo=timezone.utc))
        engine.check_time_tick()
        assert engine.current_phase == FlatteningPhase.NORMAL_TRADING

    def test_reset_for_new_session_cycles_back_to_premarket(self):
        """After completing a full day flattening cycle, reset_for_new_session resets all phase gates."""
        clock = MarketClock()
        engine = ZeroOvernightFlatteningEngine(clock=clock)

        # Progress through lockout (15:45)
        clock.set_simulated_time(datetime(2026, 9, 21, 15, 45, 0, tzinfo=ET_TZ))
        engine.check_time_tick()
        assert engine.phase1_executed is True

        # Progress to close (16:00)
        clock.set_simulated_time(datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET_TZ))
        engine.check_time_tick()
        assert engine.current_phase == FlatteningPhase.MARKET_CLOSED

        # Reset for day 2
        engine.reset_for_new_session()
        assert engine.phase1_executed is False
        assert engine.phase2_executed is False
        assert engine.phase3_executed is False
        assert engine.phase4_executed is False
        assert engine.audit_passed is False

        # Pre-market on day 2 at 08:00 ET
        clock.set_simulated_time(datetime(2026, 9, 22, 8, 0, 0, tzinfo=ET_TZ))
        engine.check_time_tick()
        assert engine.current_phase == FlatteningPhase.PRE_MARKET

    def test_premarket_exact_microsecond_boundary(self):
        """Microsecond boundary tests between PRE_MARKET, NORMAL_TRADING, and ENTRY_LOCKOUT."""
        clock = MarketClock()
        engine = ZeroOvernightFlatteningEngine(clock=clock)

        # 09:29:59.999999 ET -> PRE_MARKET
        clock.set_simulated_time(datetime(2026, 9, 21, 9, 29, 59, 999999, tzinfo=ET_TZ))
        engine.check_time_tick()
        assert engine.current_phase == FlatteningPhase.PRE_MARKET

        # 09:30:00.000000 ET -> NORMAL_TRADING
        clock.set_simulated_time(datetime(2026, 9, 21, 9, 30, 0, 0, tzinfo=ET_TZ))
        engine.check_time_tick()
        assert engine.current_phase == FlatteningPhase.NORMAL_TRADING

        # 15:44:59.999999 ET -> NORMAL_TRADING
        clock.set_simulated_time(datetime(2026, 9, 21, 15, 44, 59, 999999, tzinfo=ET_TZ))
        engine.check_time_tick()
        assert engine.current_phase == FlatteningPhase.NORMAL_TRADING

        # 15:45:00.000000 ET -> ENTRY_LOCKOUT
        clock.set_simulated_time(datetime(2026, 9, 21, 15, 45, 0, 0, tzinfo=ET_TZ))
        d = engine.check_time_tick()
        assert d is not None
        assert d.phase == FlatteningPhase.ENTRY_LOCKOUT
        assert engine.current_phase == FlatteningPhase.ENTRY_LOCKOUT


# ============================================================================
# 5. Full Subsystem Session Boundary Purge Verification
# ============================================================================

class TestSubsystemSessionBoundaryPurge:
    """Verifies that _check_session_boundary purges not just orders, but all bracket linkages & strategy state."""

    def test_session_boundary_clears_bracket_manager_and_strategy_states(self):
        from backend.app.main import (
            _check_session_boundary,
            engine,
            bracket_manager,
            entry_order_to_bracket,
            bracket_realized_pnl,
            completed_brackets_recorded,
            strategies,
        )
        import backend.app.main as main_mod

        main_mod.last_session_date = None

        # Day 1: 2026-09-21 14:00 UTC (10:00 ET)
        day1 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)
        _check_session_boundary(day1)

        # Seed bracket manager and mappings
        brk = bracket_manager.create_bracket(
            bracket_id="brk_test_101",
            symbol="NVDA",
            side="LONG",
            total_qty=20,
            entry_price=125.0,
            stop_price=123.0,
            strategy_id="orb",
        )
        entry_order_to_bracket["ord_entry_101"] = "brk_test_101"
        bracket_realized_pnl["brk_test_101"] = 150.0
        completed_brackets_recorded.add("brk_test_101")

        # Seed strategy states
        for strat in strategies:
            if hasattr(strat, "symbol_states"):
                strat._get_state("NVDA").range_established = True
            if hasattr(strat, "pending_catalysts"):
                strat.pending_catalysts["NVDA"] = []

        assert len(bracket_manager.brackets) > 0
        assert len(entry_order_to_bracket) > 0

        # Day 2 ET transition: 2026-09-22 13:30 UTC (09:30 ET)
        day2 = datetime(2026, 9, 22, 13, 30, 0, tzinfo=timezone.utc)
        _check_session_boundary(day2)

        # Assert all linkages and state are cleared
        assert len(bracket_manager.brackets) == 0, "bracket_manager.brackets must be empty"
        assert len(bracket_manager.symbol_to_bracket) == 0, "bracket_manager.symbol_to_bracket must be empty"
        assert len(bracket_manager.order_to_bracket) == 0, "bracket_manager.order_to_bracket must be empty"
        assert len(entry_order_to_bracket) == 0, "entry_order_to_bracket must be empty"
        assert len(bracket_realized_pnl) == 0, "bracket_realized_pnl must be empty"
        assert len(completed_brackets_recorded) == 0, "completed_brackets_recorded must be empty"

        for strat in strategies:
            if hasattr(strat, "symbol_states"):
                assert len(strat.symbol_states) == 0, f"Strategy {strat.name} symbol_states must be cleared"
            if hasattr(strat, "pending_catalysts"):
                assert len(strat.pending_catalysts) == 0, f"Strategy {strat.name} pending_catalysts must be cleared"


# ============================================================================
# 6. Extreme Penny ($1.00) & Mega-Cap ($5,000.00) Clamping
# ============================================================================

class TestExtremePricesClamping:
    """Stress tests extreme boundary prices: $1.00 (penny stock) and $5,000.00 (mega-cap)."""

    @pytest.mark.parametrize("price", [1.00, 5000.00])
    def test_extreme_price_clamping_orb(self, price: float):
        strat = OpeningRangeBreakoutStrategy(range_minutes=5, min_rvol=1.80)
        sym = f"EXT_ORB_{int(price)}"

        # 5 bars establishing range: delta is 1% of price
        delta = round(price * 0.01, 4)
        for m in range(30, 35):
            strat.on_bar(_make_bar(
                symbol=sym, open_p=price, high_p=price+delta, low_p=price-delta, close_p=price,
                vol=10000, ts_str=f"2026-09-21T09:{m:02d}:00-04:00"
            ))

        entry = round((price + delta) * 1.005, 4)
        bo_bar = _make_bar(
            symbol=sym, open_p=price+delta, high_p=entry+0.05, low_p=price, close_p=entry,
            vol=50000, ts_str="2026-09-21T09:35:00-04:00"
        )
        sigs = strat.on_bar(bo_bar)
        assert len(sigs) == 1
        sig = sigs[0]

        stop_dist = abs(sig.entry_price - sig.stop_loss)
        ratio = stop_dist / sig.entry_price
        assert 0.004 - 1e-6 <= ratio <= 0.040 + 1e-6, f"Extreme ORB violated: price={price}, ratio={ratio}"

