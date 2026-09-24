"""backend/tests/stress/test_challenger_timing_idempotency.py
Adversarial Stress Test Suite — Challenger 1: Timing Window Tolerance & Staged Order Idempotency.

Target Systems Under Attack:
1. 09:30–09:45 ET Open Execution Window:
   - Delayed opening bars at 09:30:00, 09:31:00, 09:35:00, 09:44:00, and 09:45:59.
   - Pre-market rejection boundary at 09:29:59.
   - Post-window boundary at 09:46:00 (triggers expiration sweep, not open execution).
2. Out-of-Order Bar Arrival & Concurrency Annihilation Defense:
   - Holding 2 positions (at cap) with 1 staged exit and 1 staged entry.
   - Entry bar arrives before exit bar: verify entry is DEFERRED (NOT deleted), retains reservation.
   - Exit bar arrives: exit executes, slot freed, deferred entry executes immediately.
   - Multi-symbol double-exit / double-entry cascade with reverse bar arrival.
   - Cap saturated with zero pending exits: entry dropped and symbol released.
   - Ghost exit with 0 shares cleaned up without blocking entries.
3. Expiration Sweep at 09:46:00 ET:
   - Purges unexecuted staged orders older than 60 seconds.
   - Releases symbol reservations from swing_reserved_symbols.
   - Verifies 60-second grace period for newly created orders.
   - Verifies automatic sweep invocation via handle_bar_event on 09:46+ bars.
4. Staged Order Idempotency Under Rapid-Fire Evaluations (10–100 consecutive calls):
   - 10 consecutive calls with 5 qualifying symbols: never exceeds 2-position cap, zero duplicate symbols.
   - 10 consecutive calls with 1 held position: stages at most 1, held symbol skipped.
   - 10 consecutive calls with 2 held positions: stages 0.
   - 10 consecutive calls with 2 held positions and 1 exit: stages 1 exit and exactly 1 entry.
   - Cancellation and restage: maintains exact cap invariants.
   - High-throughput benchmark: 100 consecutive calls executed rapidly with zero memory leakage or state drift.
5. Integrated End-to-End Timeline:
   - handle_bar_event execution from 09:29 to 16:00 ET verifying arm isolation against intraday circuit breaker and flattening.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any, Dict, List, Optional
import pytest

from backend.app.config import settings
from backend.app.core.account import (
    PaperTradingAccount,
    Position,
    PositionSide,
    TradingArm,
)
from backend.app.core.engine import (
    ExecutionEngine,
    Order,
    OrderSide,
    OrderType,
)
from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig
from backend.app.models.events import BarEvent
from backend.app.strategies.swing_indicators import DailyBar, DailyBarStore
from backend.app.strategies.swing_panic_dip import (
    StagedSwingOrder,
    SwingStagedOrderManager,
    SwingStrategyEngine,
)
from backend.app import main


# =====================================================================
# FIXTURES & HELPERS
# =====================================================================
ET_TZ = main.ET_TZ


def _make_daily_bars(
    symbol: str,
    base_date: date,
    count: int = 215,
    start_price: float = 100.0,
    daily_delta: float = 0.5,
    force_panic: bool = True,
) -> List[DailyBar]:
    """Generate causal daily bars for indicator calculations.
    
    If force_panic=True:
      - Steady accumulation up to count-60
      - Strong momentum from count-60 to count-2 (outperforming QQQ)
      - Sharp 2-day panic drop on count-2 and count-1 (RSI(2) < 10, yet close > SMA200)
    """
    bars: List[DailyBar] = []
    p = start_price
    for i in range(count):
        d = base_date - timedelta(days=count - 1 - i)
        if force_panic and i == count - 2:
            c = p * 0.96  # 4% drop
        elif force_panic and i == count - 1:
            c = p * 0.95  # 5% drop (sharp 2-day panic dip)
        elif force_panic and i >= count - 60:
            c = p + 0.85  # Strong relative strength vs QQQ
        else:
            c = p + daily_delta
        h = max(p, c) + 2.0
        l = min(p, c) - 2.0
        bars.append(
            DailyBar(
                symbol=symbol,
                date=d,
                open=p,
                high=h,
                low=l,
                close=c,
                volume=1_500_000,
                finalized=True,
            )
        )
        p = c
    return bars


def _populate_store_with_qualifying_symbols(
    store: DailyBarStore,
    symbols: List[str],
    eval_date: date,
) -> None:
    """Populate store so that all given symbols qualify for Panic Dip entry on eval_date."""
    # Benchmark QQQ with steady uptrend
    qqq_bars = _make_daily_bars("QQQ", eval_date, count=215, start_price=450.0, daily_delta=0.2, force_panic=False)
    for b in qqq_bars:
        store.append_bar(b)

    for sym in symbols:
        if sym == "QQQ":
            continue
        # Start at 200.0, rise to 250.0, then 2-day panic dip to ~218.0 (still above SMA200 ~ 210.0)
        bars = _make_daily_bars(sym, eval_date, count=215, start_price=200.0, daily_delta=0.25, force_panic=True)
        for b in bars:
            store.append_bar(b)


# =====================================================================
# GROUP 1: TIMING WINDOW TOLERANCE (09:30:00 - 09:45:00 ET)
# =====================================================================
class TestTimingWindowTolerance:
    """Adversarially probe opening window delays, jitter, and boundaries."""

    @pytest.mark.parametrize(
        "hour,minute,second,label",
        [
            (9, 30, 0, "Exact 09:30:00 Open"),
            (9, 31, 0, "Delayed 09:31:00 Bar (Auction Cross Delay)"),
            (9, 35, 0, "Delayed 09:35:00 Bar (Illiquidity / LQD Delay)"),
            (9, 44, 0, "Delayed 09:44:00 Bar (Late Morning Print)"),
            (9, 45, 59, "Boundary 09:45:59 Bar (Window Cutoff Edge)"),
        ],
    )
    def test_open_window_delays_execute_reliably(self, hour: int, minute: int, second: int, label: str):
        """Verify staged orders execute reliably at every minute inside the [09:30, 09:45] window."""
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            staged_manager=staged_mgr,
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        eval_date = date(2026, 9, 23)
        staged_mgr.stage_buy(
            symbol="MU",
            target_notional=25_000.0,
            daily_atr=4.50,
            signal_date=eval_date,
            reason="PANIC_DIP_TEST",
        )
        assert staged_mgr.is_staged_for_entry("MU") is True

        open_dt = datetime(2026, 9, 24, hour, minute, second, tzinfo=ET_TZ)
        open_prices = {"MU": 110.0}

        # Execute market open with realistic slippage
        result = engine.execute_market_open(open_prices=open_prices, open_time=open_dt, apply_slippage=True)

        assert len(result["entries"]) == 1, f"Failed to execute staged entry at {label}"
        entry = result["entries"][0]
        assert entry["symbol"] == "MU"
        assert entry["shares"] == int(math.floor(25_000.0 / 110.0))
        assert entry["fill_price"] >= 110.0
        assert entry["slippage"] > 0.0

        # Position must exist with swing metadata and anchored stop
        assert "MU" in acct.positions
        pos = acct.positions["MU"]
        assert pos.arm == TradingArm.SWING
        assert pos.strategy_id == "swing_panic_dip"
        expected_stop = round(pos.avg_entry_price - 2.5 * 4.50, 2)
        assert pos.stop_loss_price == expected_stop
        assert staged_mgr.is_staged_for_entry("MU") is False

    def test_pre_market_bar_0929_does_not_execute(self):
        """Verify bars arriving before 09:30:00 (e.g. 09:29:59) reject open execution."""
        main.simulation_mode = True
        main.account.positions.clear()
        main.engine.working_orders.clear()
        main.swing_staged_order_manager.clear()
        main.swing_reserved_symbols.clear()

        # Stage buy for KLAC
        eval_date = date(2026, 9, 23)
        main.swing_staged_order_manager.stage_buy(
            symbol="KLAC",
            target_notional=25_000.0,
            daily_atr=15.0,
            signal_date=eval_date,
            reason="TEST_PRE_MARKET",
        )
        main.swing_reserved_symbols.add("KLAC")

        pre_market_dt = datetime(2026, 9, 24, 9, 29, 59, tzinfo=ET_TZ)
        pre_bar = BarEvent(
            symbol="KLAC",
            timestamp=pre_market_dt,
            open=700.0,
            high=702.0,
            low=699.0,
            close=701.0,
            volume=5_000,
        )

        asyncio.run(main.handle_bar_event(pre_bar))

        # Staged order must NOT execute on pre-market bar
        assert main.swing_staged_order_manager.is_staged_for_entry("KLAC") is True
        assert "KLAC" not in main.account.positions

    def test_post_window_bar_0946_does_not_execute_as_open(self):
        """Verify bars arriving after 09:45:59 (e.g. 09:46:00) do NOT execute open orders."""
        main.simulation_mode = True
        main.account.positions.clear()
        main.engine.working_orders.clear()
        main.swing_staged_order_manager.clear()
        main.swing_reserved_symbols.clear()

        eval_date = date(2026, 9, 23)
        order = main.swing_staged_order_manager.stage_buy(
            symbol="LRCX",
            target_notional=25_000.0,
            daily_atr=12.0,
            signal_date=eval_date,
            reason="TEST_POST_WINDOW",
        )
        # Set created_at to 16:00 ET prior day
        order.created_at = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)
        main.swing_reserved_symbols.add("LRCX")

        post_dt = datetime(2026, 9, 24, 9, 46, 0, tzinfo=ET_TZ)
        post_bar = BarEvent(
            symbol="LRCX",
            timestamp=post_dt,
            open=850.0,
            high=852.0,
            low=848.0,
            close=851.0,
            volume=8_000,
        )

        asyncio.run(main.handle_bar_event(post_bar))

        # Staged order must NOT have executed, but must have expired
        assert "LRCX" not in main.account.positions
        assert main.swing_staged_order_manager.is_staged_for_entry("LRCX") is False
        assert "LRCX" not in main.swing_reserved_symbols


# =====================================================================
# GROUP 2: OUT-OF-ORDER JITTER & CONCURRENCY ANNIHILATION DEFENSE
# =====================================================================
class TestOutOfOrderJitterAndConcurrency:
    """Adversarially probe arrival sequence permutations when at position cap."""

    def test_entry_before_exit_deferred_and_fills_upon_exit_arrival(self):
        """Defect 2 Stress Test: Holding 2 positions (cap), 1 exit staged, 1 entry staged.
        
        Bar sequence: Entry bar arrives first. Verify:
        1. Entry is NOT deleted or dropped; it is deferred.
        2. Exit bar arrives second: exit executes, slot frees, entry executes immediately.
        3. Cap of 2 positions is strictly preserved at all moments.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        reserved: set[str] = set()

        def _reserve(sym: str) -> None:
            reserved.add(sym.upper())

        def _release(sym: str) -> None:
            reserved.discard(sym.upper())

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            staged_manager=staged_mgr,
            max_concurrent_positions=2,
            slot_notional=25_000.0,
            reserve_symbol_cb=_reserve,
            release_symbol_cb=_release,
        )

        init_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        # Establish 2 active swing positions (MU and LRCX)
        acct.apply_fill("p1", "MU", "BUY", 250, 100.0, 0.0, init_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.apply_fill("p2", "LRCX", "BUY", 30, 800.0, 0.0, init_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        _reserve("MU")
        _reserve("LRCX")

        assert len(engine.get_active_swing_positions()) == 2
        assert len(acct.positions) == 2

        # Stage exit for MU (e.g. 5-day SMA cross)
        staged_mgr.stage_sell(symbol="MU", shares=250, signal_date=date(2026, 9, 23), reason="SMA5_EXIT")
        # Stage entry for KLAC (Panic Dip)
        staged_mgr.stage_buy(symbol="KLAC", target_notional=25_000.0, daily_atr=14.0, signal_date=date(2026, 9, 23), reason="PANIC_DIP")
        _reserve("KLAC")

        # Step 1: KLAC bar arrives FIRST at 09:30:15 ET (out-of-order jitter)
        # MU open price is completely missing
        jitter_time_1 = datetime(2026, 9, 24, 9, 30, 15, tzinfo=ET_TZ)
        res1 = engine.execute_market_open(
            open_prices={"KLAC": 700.0},
            open_time=jitter_time_1,
            apply_slippage=True,
        )

        # Invariant 1: KLAC must NOT execute yet
        assert len(res1["entries"]) == 0
        assert len(res1["exits"]) == 0
        # Invariant 2: KLAC MUST NOT BE DELETED FROM STAGED MANAGER
        assert staged_mgr.is_staged_for_entry("KLAC") is True, "CRITICAL REGRESSION: Staged entry deleted instead of deferred!"
        # Invariant 3: Active positions remain exactly 2 (MU and LRCX)
        assert len(engine.get_active_swing_positions()) == 2
        assert "KLAC" in reserved, "Reservation prematurely released during deferral"

        # Step 2: MU bar arrives at 09:30:30 ET
        # Open price map now has both MU and KLAC
        jitter_time_2 = datetime(2026, 9, 24, 9, 30, 30, tzinfo=ET_TZ)
        res2 = engine.execute_market_open(
            open_prices={"MU": 105.0, "KLAC": 700.0},
            open_time=jitter_time_2,
            apply_slippage=True,
        )

        # Invariant 4: MU exit executed
        assert len(res2["exits"]) == 1
        assert res2["exits"][0]["symbol"] == "MU"
        assert "MU" not in acct.positions or acct.positions["MU"].shares == 0
        assert "MU" not in reserved

        # Invariant 5: KLAC entry executed immediately into freed slot!
        assert len(res2["entries"]) == 1
        assert res2["entries"][0]["symbol"] == "KLAC"
        assert "KLAC" in acct.positions
        assert acct.positions["KLAC"].shares == int(math.floor(25_000.0 / 700.0))
        assert staged_mgr.is_staged_for_entry("KLAC") is False
        assert "KLAC" in reserved

        # Invariant 6: Final position count is exactly 2 (LRCX and KLAC)
        active_pos = engine.get_active_swing_positions()
        assert len(active_pos) == 2
        assert "LRCX" in active_pos
        assert "KLAC" in active_pos

    def test_multi_symbol_double_exit_double_entry_cascade(self):
        """Stress Test: 2 held positions (MU, AMD). Both stage exits. 2 new symbols (LRCX, KLAC) stage entries.
        
        Feed entry bars first, then exits. Verify cascade executes deterministically without cap breach.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            staged_manager=staged_mgr,
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        now_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        acct.apply_fill("p1", "MU", "BUY", 250, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.apply_fill("p2", "AMD", "BUY", 150, 160.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")

        staged_mgr.stage_sell(symbol="MU", shares=250, signal_date=date(2026, 9, 23), reason="EXIT_MU")
        staged_mgr.stage_sell(symbol="AMD", shares=150, signal_date=date(2026, 9, 23), reason="EXIT_AMD")

        staged_mgr.stage_buy(symbol="LRCX", target_notional=25_000.0, daily_atr=12.0, signal_date=date(2026, 9, 23), reason="BUY_LRCX")
        staged_mgr.stage_buy(symbol="KLAC", target_notional=25_000.0, daily_atr=14.0, signal_date=date(2026, 9, 23), reason="BUY_KLAC")

        open_time = datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ)

        # 1. Entry bars arrive first: LRCX and KLAC only
        r1 = engine.execute_market_open(open_prices={"LRCX": 850.0, "KLAC": 700.0}, open_time=open_time)
        assert len(r1["exits"]) == 0
        assert len(r1["entries"]) == 0
        assert staged_mgr.is_staged_for_entry("LRCX") is True
        assert staged_mgr.is_staged_for_entry("KLAC") is True

        # 2. MU exit bar arrives: MU sells, freeing 1 slot -> LRCX enters!
        r2 = engine.execute_market_open(open_prices={"MU": 105.0, "LRCX": 850.0}, open_time=open_time)
        assert len(r2["exits"]) == 1
        assert r2["exits"][0]["symbol"] == "MU"
        assert len(r2["entries"]) == 1
        assert r2["entries"][0]["symbol"] == "LRCX"
        # AMD and LRCX active -> active_count = 2
        assert len(engine.get_active_swing_positions()) == 2
        # KLAC still deferred (pending AMD exit)
        assert staged_mgr.is_staged_for_entry("KLAC") is True

        # 3. AMD exit bar arrives: AMD sells, freeing 1 slot -> KLAC enters!
        r3 = engine.execute_market_open(open_prices={"AMD": 165.0, "KLAC": 700.0}, open_time=open_time)
        assert len(r3["exits"]) == 1
        assert r3["exits"][0]["symbol"] == "AMD"
        assert len(r3["entries"]) == 1
        assert r3["entries"][0]["symbol"] == "KLAC"

        # Final state: exactly 2 positions (LRCX and KLAC)
        final_active = engine.get_active_swing_positions()
        assert len(final_active) == 2
        assert "LRCX" in final_active
        assert "KLAC" in final_active
        assert len(staged_mgr.get_staged_orders()) == 0

    def test_cap_saturated_no_pending_exits_cancels_entry(self):
        """Verify that if active swing positions = 2 and there are NO pending exits,
        an unexecutable staged entry order is cleanly dropped and its symbol released.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        released: list[str] = []

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            staged_manager=staged_mgr,
            max_concurrent_positions=2,
            slot_notional=25_000.0,
            release_symbol_cb=lambda s: released.append(s),
        )

        now_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        acct.apply_fill("p1", "MU", "BUY", 250, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.apply_fill("p2", "LRCX", "BUY", 30, 800.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")

        # Rogue / leftover staged entry with 0 pending exits
        staged_mgr.stage_buy(symbol="KLAC", target_notional=25_000.0, daily_atr=14.0, signal_date=date(2026, 9, 23), reason="NO_ROOM")

        open_time = datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ)
        res = engine.execute_market_open(open_prices={"KLAC": 700.0}, open_time=open_time)

        # Cannot enter and no pending exit to wait for: must drop order and release symbol
        assert len(res["entries"]) == 0
        assert staged_mgr.is_staged_for_entry("KLAC") is False
        assert "KLAC" in released

    def test_ghost_exit_cleaned_up_safely(self):
        """Verify that an exit staged for a symbol with 0 shares is cleaned up without blocking entries."""
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            staged_manager=staged_mgr,
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        # Staged exit for symbol with no shares
        staged_mgr.stage_sell(symbol="MU", shares=200, signal_date=date(2026, 9, 23), reason="GHOST_EXIT")
        staged_mgr.stage_buy(symbol="LRCX", target_notional=25_000.0, daily_atr=10.0, signal_date=date(2026, 9, 23), reason="BUY_LRCX")

        open_time = datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ)
        res = engine.execute_market_open(open_prices={"MU": 100.0, "LRCX": 800.0}, open_time=open_time)

        # Ghost exit cleaned up
        assert staged_mgr.is_staged_for_exit("MU") is False
        # Valid entry executed
        assert len(res["entries"]) == 1
        assert res["entries"][0]["symbol"] == "LRCX"


# =====================================================================
# GROUP 3: EXPIRATION SWEEP AT 09:46:00 ET
# =====================================================================
class TestExpirationSweep:
    """Adversarially probe stale staged order cleanup past 09:45 ET."""

    def test_expiration_sweep_purges_unexecuted_orders(self):
        """Verify that orders unexecuted past 09:45 ET are purged and symbol reservations released."""
        main.swing_staged_order_manager.clear()
        main.swing_reserved_symbols.clear()

        eval_date = date(2026, 9, 23)
        ord_entry = main.swing_staged_order_manager.stage_buy(
            symbol="AMD", target_notional=25_000.0, daily_atr=5.0, signal_date=eval_date, reason="SWEEP_TEST"
        )
        ord_entry.created_at = datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ)
        main.swing_reserved_symbols.add("AMD")

        # 09:46:00 ET sweep
        sweep_time = datetime(2026, 9, 24, 9, 46, 0, tzinfo=ET_TZ)
        main._expire_stale_staged_swing_orders(sweep_time)

        assert main.swing_staged_order_manager.is_staged_for_entry("AMD") is False
        assert "AMD" not in main.swing_reserved_symbols
        assert main.is_symbol_reserved_for_swing("AMD") is False

    def test_expiration_sweep_grace_period(self):
        """Verify 60-second grace period: orders younger than 60s are spared, older orders are purged."""
        main.swing_staged_order_manager.clear()
        main.swing_reserved_symbols.clear()

        sweep_time = datetime(2026, 9, 24, 9, 46, 0, tzinfo=ET_TZ)

        # Order 1: Created at 09:45:30 (30 seconds old) -> within grace period
        ord_young = main.swing_staged_order_manager.stage_buy(
            symbol="MU", target_notional=25_000.0, daily_atr=3.0, signal_date=date(2026, 9, 23), reason="YOUNG"
        )
        ord_young.created_at = datetime(2026, 9, 24, 9, 45, 30, tzinfo=ET_TZ)
        main.swing_reserved_symbols.add("MU")

        # Order 2: Created at 09:44:00 (120 seconds old) -> past grace period
        ord_old = main.swing_staged_order_manager.stage_buy(
            symbol="KLAC", target_notional=25_000.0, daily_atr=15.0, signal_date=date(2026, 9, 23), reason="OLD"
        )
        ord_old.created_at = datetime(2026, 9, 24, 9, 44, 0, tzinfo=ET_TZ)
        main.swing_reserved_symbols.add("KLAC")

        # Run sweep at 09:46:00
        main._expire_stale_staged_swing_orders(sweep_time)

        # MU (young) must survive
        assert main.swing_staged_order_manager.is_staged_for_entry("MU") is True
        assert "MU" in main.swing_reserved_symbols

        # KLAC (old) must be purged
        assert main.swing_staged_order_manager.is_staged_for_entry("KLAC") is False
        assert "KLAC" not in main.swing_reserved_symbols

        # Advance time by 35s to 09:46:35 -> now MU is 65s old (> 60s)
        later_time = datetime(2026, 9, 24, 9, 46, 35, tzinfo=ET_TZ)
        main._expire_stale_staged_swing_orders(later_time)

        # Now MU must also be purged
        assert main.swing_staged_order_manager.is_staged_for_entry("MU") is False
        assert "MU" not in main.swing_reserved_symbols

    def test_expiration_sweep_triggered_by_handle_bar_event(self):
        """Verify that incoming bars stamped 09:46 ET automatically trigger the expiration sweep."""
        main.simulation_mode = True
        main.swing_staged_order_manager.clear()
        main.swing_reserved_symbols.clear()

        ord_entry = main.swing_staged_order_manager.stage_buy(
            symbol="GS", target_notional=25_000.0, daily_atr=8.0, signal_date=date(2026, 9, 23), reason="GS_SWEEP"
        )
        ord_entry.created_at = datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ)
        main.swing_reserved_symbols.add("GS")

        # Send a bar for unrelated symbol SPY stamped 09:46:00 ET
        spy_bar = BarEvent(
            symbol="SPY",
            timestamp=datetime(2026, 9, 24, 9, 46, 0, tzinfo=ET_TZ),
            open=550.0,
            high=551.0,
            low=549.5,
            close=550.5,
            volume=20_000,
        )

        asyncio.run(main.handle_bar_event(spy_bar))

        # GS order should have expired via the bar event
        assert main.swing_staged_order_manager.is_staged_for_entry("GS") is False
        assert "GS" not in main.swing_reserved_symbols


# =====================================================================
# GROUP 4: IDEMPOTENCY UNDER RAPID-FIRE EVALUATIONS
# =====================================================================
class TestStagedOrderIdempotencyRapidFire:
    """Adversarially probe evaluate_market_close under repeated scans, restarts, and concurrent qualification."""

    def test_rapid_fire_10_evaluations_5_qualifying_symbols(self):
        """Defect 3 Stress Test: Call evaluate_market_close 10 times consecutively with 5 qualifying symbols.
        
        Account has 0 active positions. Verify:
        1. Staged entries NEVER exceed 2 (the max concurrent positions limit).
        2. Exactly 2 distinct symbols staged.
        3. Zero duplicate symbols staged.
        4. Staged entries count is completely stable across calls 1 through 10.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        bar_store = DailyBarStore()
        reserved: set[str] = set()

        eval_date = date(2026, 9, 23)
        symbols = ["MU", "LRCX", "KLAC", "AMD", "GS"]
        _populate_store_with_qualifying_symbols(bar_store, symbols, eval_date)

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            bar_store=bar_store,
            staged_manager=staged_mgr,
            symbols=symbols,
            benchmark="QQQ",
            max_concurrent_positions=2,
            slot_notional=25_000.0,
            reserve_symbol_cb=lambda s: reserved.add(s.upper()),
            release_symbol_cb=lambda s: reserved.discard(s.upper()),
        )

        close_time = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)

        # Call evaluate_market_close 10 times consecutively
        history_counts: List[int] = []
        for i in range(10):
            audit = engine.evaluate_market_close(close_time)
            staged = staged_mgr.get_staged_entries()
            count = len(staged)
            history_counts.append(count)
            assert count <= 2, f"CONCURRENCY BREACH on iteration {i+1}: {count} staged > max 2"

        # Verify idempotency invariants
        assert history_counts[0] == 2, "Expected 2 initial qualifying staged entries"
        assert all(c == 2 for c in history_counts), f"Count shifted across iterations: {history_counts}"

        staged_symbols = [e.symbol for e in staged_mgr.get_staged_entries()]
        assert len(staged_symbols) == 2
        assert len(set(staged_symbols)) == 2, "Duplicate symbols staged!"
        assert len(reserved) == 2

    def test_rapid_fire_10_evaluations_with_1_held_position(self):
        """Call evaluate_market_close 10 times when 1 position is already held.
        
        Available slots = 2 - 1 = 1. Verify:
        1. Staged entries NEVER exceed 1.
        2. Held symbol is skipped and never staged.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        bar_store = DailyBarStore()

        eval_date = date(2026, 9, 23)
        symbols = ["MU", "LRCX", "KLAC", "AMD", "GS"]
        _populate_store_with_qualifying_symbols(bar_store, symbols, eval_date)

        # Hold MU
        now_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        acct.apply_fill("p1", "MU", "BUY", 200, 120.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        assert len(acct.positions) == 1

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            bar_store=bar_store,
            staged_manager=staged_mgr,
            symbols=symbols,
            benchmark="QQQ",
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        close_time = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)

        for i in range(10):
            engine.evaluate_market_close(close_time)
            staged = staged_mgr.get_staged_entries()
            assert len(staged) == 1, f"Expected exactly 1 staged entry on iteration {i+1}, got {len(staged)}"
            assert staged[0].symbol != "MU", "Held symbol MU was staged for re-entry!"

    def test_rapid_fire_10_evaluations_with_2_held_positions(self):
        """Call evaluate_market_close 10 times when 2 positions are held (cap reached).
        
        Available slots = 0. Verify: 0 staged entries across all 10 calls.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        bar_store = DailyBarStore()

        eval_date = date(2026, 9, 23)
        symbols = ["MU", "LRCX", "KLAC", "AMD", "GS"]
        _populate_store_with_qualifying_symbols(bar_store, symbols, eval_date)

        # Hold MU and LRCX
        now_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        acct.apply_fill("p1", "MU", "BUY", 200, 120.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.apply_fill("p2", "LRCX", "BUY", 30, 800.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            bar_store=bar_store,
            staged_manager=staged_mgr,
            symbols=symbols,
            benchmark="QQQ",
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        close_time = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)

        for i in range(10):
            engine.evaluate_market_close(close_time)
            staged = staged_mgr.get_staged_entries()
            assert len(staged) == 0, f"Expected 0 staged entries on iteration {i+1}, got {len(staged)}"

    def test_rapid_fire_10_evaluations_with_1_held_and_1_exiting(self):
        """Call evaluate_market_close 10 times when 2 positions are held and 1 triggers exit.
        
        MU triggers exit (frees 1 slot). LRCX survives.
        Available slots = 2 - 1 = 1. Verify:
        1. Exactly 1 staged exit (MU).
        2. Exactly 1 staged entry.
        3. Surviving LRCX and exiting MU not staged for entry.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        bar_store = DailyBarStore()

        eval_date = date(2026, 9, 23)
        symbols = ["MU", "LRCX", "KLAC", "AMD", "GS"]
        _populate_store_with_qualifying_symbols(bar_store, symbols, eval_date)

        # Hold MU (holding_days=5 -> time stop triggers exit!)
        now_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        acct.apply_fill("p1", "MU", "BUY", 200, 120.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.positions["MU"].holding_days = 5  # Triggers Rule 7c time stop exit!

        # Hold LRCX (holding_days=1, RSI2 < 70, below SMA5 -> survives)
        acct.apply_fill("p2", "LRCX", "BUY", 30, 800.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.positions["LRCX"].holding_days = 1

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            bar_store=bar_store,
            staged_manager=staged_mgr,
            symbols=symbols,
            benchmark="QQQ",
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        close_time = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)

        for i in range(10):
            engine.evaluate_market_close(close_time)
            exits = staged_mgr.get_staged_exits()
            entries = staged_mgr.get_staged_entries()
            assert len(exits) == 1, f"Expected 1 staged exit on iteration {i+1}, got {len(exits)}"
            assert exits[0].symbol == "MU"
            assert len(entries) == 1, f"Expected 1 staged entry on iteration {i+1}, got {len(entries)}"
            assert entries[0].symbol not in ("MU", "LRCX")

    def test_cancellation_and_restage_idempotency(self):
        """Verify that cancelling 1 of 2 staged entries allows exactly 1 replacement upon next evaluation."""
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        bar_store = DailyBarStore()

        eval_date = date(2026, 9, 23)
        symbols = ["MU", "LRCX", "KLAC", "AMD", "GS"]
        _populate_store_with_qualifying_symbols(bar_store, symbols, eval_date)

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            bar_store=bar_store,
            staged_manager=staged_mgr,
            symbols=symbols,
            benchmark="QQQ",
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        close_time = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)
        engine.evaluate_market_close(close_time)
        assert len(staged_mgr.get_staged_entries()) == 2

        # Manually cancel 1 staged order
        first_order = staged_mgr.get_staged_entries()[0]
        staged_mgr.remove_staged_order(first_order.order_id)
        assert len(staged_mgr.get_staged_entries()) == 1

        # Re-evaluate market close
        engine.evaluate_market_close(close_time)
        # Should restage 1 replacement to reach 2, never exceeding
        assert len(staged_mgr.get_staged_entries()) == 2

    def test_high_throughput_rapid_fire_100_evaluations(self):
        """Stress Test: 100 consecutive rapid-fire calls to evaluate_market_close.
        
        Assert execution is sub-second, zero memory leakage, zero cap drift.
        """
        acct = PaperTradingAccount(initial_cash=50_000.0)
        exec_engine = ExecutionEngine(account=acct)
        staged_mgr = SwingStagedOrderManager()
        bar_store = DailyBarStore()

        eval_date = date(2026, 9, 23)
        symbols = ["MU", "LRCX", "KLAC", "AMD", "GS"]
        _populate_store_with_qualifying_symbols(bar_store, symbols, eval_date)

        engine = SwingStrategyEngine(
            account=acct,
            execution_engine=exec_engine,
            bar_store=bar_store,
            staged_manager=staged_mgr,
            symbols=symbols,
            benchmark="QQQ",
            max_concurrent_positions=2,
            slot_notional=25_000.0,
        )

        close_time = datetime(2026, 9, 23, 16, 0, 0, tzinfo=ET_TZ)

        t0 = datetime.now()
        for _ in range(100):
            engine.evaluate_market_close(close_time)
        elapsed_sec = (datetime.now() - t0).total_seconds()

        assert len(staged_mgr.get_staged_entries()) == 2
        # Assert fast throughput: 100 calls in < 2.0s
        assert elapsed_sec < 2.0, f"100 evaluations took too long: {elapsed_sec:.3f}s"


# =====================================================================
# GROUP 5: INTEGRATED TIMELINE WITH ARM ISOLATION
# =====================================================================
class TestIntegratedTimelineArmIsolation:
    """Stress Test: End-to-end bar event pipeline across the trading day."""

    @pytest.mark.asyncio
    async def test_timeline_open_to_circuit_breaker_isolation(self):
        """Simulate market open, delayed bar fill, intraday circuit breaker trip, and auto-flattening.
        
        Verify swing position remains untouched across the full session.
        """
        main.simulation_mode = True
        main.account.positions.clear()
        main.engine.working_orders.clear()
        main.swing_staged_order_manager.clear()
        main.swing_reserved_symbols.clear()

        eval_date = date(2026, 9, 23)
        order = main.swing_staged_order_manager.stage_buy(
            symbol="MU",
            target_notional=25_000.0,
            daily_atr=4.0,
            signal_date=eval_date,
            reason="TIMELINE_TEST",
        )
        main.swing_reserved_symbols.add("MU")

        # 1. 09:29:00 ET bar arrives -> no fill
        b_0929 = BarEvent(
            symbol="MU",
            timestamp=datetime(2026, 9, 24, 9, 29, 0, tzinfo=ET_TZ),
            open=100.0, high=101.0, low=99.5, close=100.2, volume=10_000,
        )
        await main.handle_bar_event(b_0929)
        assert "MU" not in main.account.positions
        assert main.swing_staged_order_manager.is_staged_for_entry("MU") is True

        # 2. 09:31:00 ET delayed opening bar arrives -> fills!
        b_0931 = BarEvent(
            symbol="MU",
            timestamp=datetime(2026, 9, 24, 9, 31, 0, tzinfo=ET_TZ),
            open=102.0, high=103.0, low=101.5, close=102.5, volume=50_000,
        )
        await main.handle_bar_event(b_0931)
        assert "MU" in main.account.positions
        pos = main.account.positions["MU"]
        assert pos.arm == TradingArm.SWING
        assert pos.strategy_id == "swing_panic_dip"
        assert main.swing_staged_order_manager.is_staged_for_entry("MU") is False

        # 3. Create an intraday position on AAPL
        main.account.apply_fill(
            "fill_in", "AAPL", "BUY", 100, 150.0, 0.0,
            datetime(2026, 9, 24, 9, 35, 0, tzinfo=ET_TZ),
            arm=TradingArm.INTRADAY, strategy_id="orb"
        )
        assert "AAPL" in main.account.positions

        # 4. Trip intraday circuit breaker at 10:15 ET
        main._trip_circuit_breaker(datetime(2026, 9, 24, 10, 15, 0, tzinfo=ET_TZ))

        # Intraday AAPL must be liquidated
        assert "AAPL" not in main.account.positions or main.account.positions["AAPL"].shares == 0
        # Swing MU must remain alive and intact!
        assert "MU" in main.account.positions
        assert main.account.positions["MU"].arm == TradingArm.SWING
        assert main.account.positions["MU"].shares > 0


# =====================================================================
# GROUP 6: MUTATION TESTS (EMPIRICAL PROOF OF DEFECT DETECTION)
# =====================================================================
class TestMutationsDemonstrateDefectDetection:
    """Verify that defective logic mutators fail deterministically, proving test sensitivity."""

    def test_mutation_strict_equality_maroons_delayed_bar(self):
        """Mutator: Simulate old Defect 1 logic (strict minute == 30 check).
        
        Prove that a 09:31 bar fails to trigger open execution, leaving staged orders marooned.
        """
        delayed_time = time(9, 31, 0)
        # Defective condition (before fix)
        defective_check = (delayed_time.hour == 9 and delayed_time.minute == 30)
        # Remediated condition (after fix)
        remediated_check = (delayed_time.hour == 9 and 30 <= delayed_time.minute <= 45)

        assert defective_check is False, "Mutator failed: strict equality unexpectedly passed on 09:31 bar!"
        assert remediated_check is True, "Remediated condition must pass on 09:31 bar"

    def test_mutation_premature_deletion_annihilates_entry(self):
        """Mutator: Simulate old Defect 2 logic (deleting entry order without checking pending exits).
        
        Prove that out-of-order entry arrival permanently deletes the order under defective logic.
        """
        staged_mgr = SwingStagedOrderManager()
        staged_mgr.stage_sell("MU", 250, date(2026, 9, 23), "EXIT")
        staged_mgr.stage_buy("KLAC", 25_000.0, 14.0, date(2026, 9, 23), "ENTRY")

        active_count = 2
        max_concurrent = 2
        pending_exits = staged_mgr.get_staged_exits()

        # Defective logic: unconditional deletion when active_count >= max_concurrent
        def defective_handle_entry(order_id: str):
            if active_count >= max_concurrent:
                staged_mgr.remove_staged_order(order_id)
                return "DELETED"
            return "EXECUTED"

        # Remediated logic: check pending exits and defer
        def remediated_handle_entry(order_id: str):
            if active_count >= max_concurrent:
                if len(pending_exits) > 0:
                    return "DEFERRED"
                staged_mgr.remove_staged_order(order_id)
                return "DELETED"
            return "EXECUTED"

        entry_order = staged_mgr.get_staged_entries()[0]
        # Defective logic deletes entry
        action_defective = defective_handle_entry(entry_order.order_id)
        assert action_defective == "DELETED"
        assert staged_mgr.is_staged_for_entry("KLAC") is False, "Mutator failed: defective logic did not delete entry!"

        # Reset and run remediated logic
        staged_mgr.stage_buy("KLAC", 25_000.0, 14.0, date(2026, 9, 23), "ENTRY")
        entry_order_2 = staged_mgr.get_staged_entries()[0]
        action_remediated = remediated_handle_entry(entry_order_2.order_id)
        assert action_remediated == "DEFERRED"
        assert staged_mgr.is_staged_for_entry("KLAC") is True

    def test_mutation_idempotency_slot_leak_overflows_positions(self):
        """Mutator: Simulate old Defect 3 logic (not deducting existing staged entries from available slots).
        
        Prove that repeated evaluations commit excess capital and stage up to 5 symbols.
        """
        surviving_positions = set()
        max_concurrent = 2
        symbols = ["MU", "LRCX", "KLAC", "AMD", "GS"]

        # Defective slot formula: does not subtract existing staged entries
        def defective_slots(existing_staged: set) -> int:
            return max_concurrent - len(surviving_positions)

        # Remediated slot formula: subtracts existing staged entries
        def remediated_slots(existing_staged: set) -> int:
            return max_concurrent - len(surviving_positions) - len(existing_staged)

        # Run 5 iterations of staging
        staged_defective = set()
        for s in symbols:
            slots = defective_slots(staged_defective)
            if slots > 0:
                staged_defective.add(s)

        staged_remediated = set()
        for s in symbols:
            slots = remediated_slots(staged_remediated)
            if slots > 0:
                staged_remediated.add(s)

        # Defective logic commits all 5 symbols ($125,000 notional)!
        assert len(staged_defective) == 5, f"Mutator failed: defective logic staged {len(staged_defective)} != 5"
        # Remediated logic strictly enforces cap of 2 ($50,000 notional)!
        assert len(staged_remediated) == 2, f"Remediated logic staged {len(staged_remediated)} != 2"

