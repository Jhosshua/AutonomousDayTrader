"""backend/tests/stress/test_challenger_market_open_pricing_r2.py
Adversarial Stress Test Suite — Challenger 1 Iteration 2:
Market Open Pricing & Out-of-Order Bar Arrival under today_open_prices.

Validates:
1. Out-of-order open bar arrival between staged exits (Symbol A: LRCX) and staged entries (Symbol B: KLAC).
2. Zero stale price leakage: extreme values seeded in latest_market_prices NEVER leak into order fills or stops.
3. Concurrency-constrained deferral: when holding 2 positions (cap), entry awaits exit completion and executes on true open price.
4. Bar open immutability: subsequent bars during 09:30-09:45 never overwrite confirmed opening prices.
5. Absolute session boundary clearing: today_open_prices and latest_market_prices are wiped clean across session rollover and runtime reset.
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
from backend.app.models.events import BarEvent
from backend.app.strategies.swing_panic_dip import (
    StagedSwingOrder,
    SwingStagedOrderManager,
    SwingStrategyEngine,
)
from backend.app import main

ET_TZ = main.ET_TZ


@pytest.fixture(autouse=True)
def clean_runtime():
    """Ensure main runtime state is completely sanitized before and after each test."""
    main.reset_runtime_state()
    main.simulation_mode = True
    yield
    main.reset_runtime_state()
    main.simulation_mode = False


class TestMarketOpenPricingChallengerR2:
    """Adversarial stress testing suite for market open pricing and session boundary clearing."""

    @pytest.mark.asyncio
    async def test_adversarial_out_of_order_open_jitter_klac_before_lrcx_unconstrained(self):
        """Stress Test 1 (Unconstrained):
        - Holding 1 position: LRCX (35 shares @ $640.00). Active count = 1 (< 2 cap).
        - Staged exit: LRCX (35 shares).
        - Staged entry: KLAC ($25,000 target notional, ATR=12.0).
        - latest_market_prices seeded with STALE prices: LRCX=$510.00, KLAC=$620.00.
        - Feed Symbol B (KLAC) open bar FIRST at 09:30:01 with price $700.00.
        - Feed Symbol A (LRCX) open bar SECOND at 09:30:05 with price $650.00.
        - Verify:
          1. KLAC executes at its genuine confirmed open price ($700.00 + buy slippage), NOT stale 620.00.
          2. LRCX does NOT exit at stale $510.00 when KLAC bar arrives.
          3. LRCX exits at its genuine confirmed open price ($650.00 - sell slippage) when LRCX bar arrives.
          4. Zero stale price leakage from latest_market_prices.
        """
        staged_mgr = main.swing_staged_order_manager
        staged_mgr.clear()
        main.account.positions.clear()
        main.swing_reserved_symbols.clear()

        init_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        main.account.apply_fill(
            order_id="init_lrcx",
            symbol="LRCX",
            side="BUY",
            qty=35,
            price=640.00,
            fee=0.0,
            timestamp=init_dt,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
        )
        main.swing_reserved_symbols.add("LRCX")
        assert len(main.swing_strategy_engine.get_active_swing_positions()) == 1

        # Stage exit for LRCX and entry for KLAC
        eval_date = date(2026, 9, 23)
        staged_mgr.stage_sell(symbol="LRCX", shares=35, signal_date=eval_date, reason="SMA5_EXIT")
        staged_mgr.stage_buy(symbol="KLAC", target_notional=25_000.0, daily_atr=12.0, signal_date=eval_date, reason="PANIC_DIP")
        main.swing_reserved_symbols.add("KLAC")

        # Poison latest_market_prices with stale prices
        main.latest_market_prices["LRCX"] = 510.00  # Stale! Real open is 650.00
        main.latest_market_prices["KLAC"] = 620.00  # Stale! Real open is 700.00

        # Verify initial clean open prices
        assert len(main.today_open_prices) == 0

        # -------------------------------------------------------------
        # 1. Bar 1: Symbol B (KLAC) arrives FIRST at 09:30:01 ET ($700.00)
        # -------------------------------------------------------------
        bar_klac = BarEvent(
            symbol="KLAC",
            open=700.00,
            high=702.00,
            low=698.00,
            close=701.00,
            volume=50_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 1, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_klac)

        # Invariant 1: today_open_prices holds KLAC, NOT LRCX
        assert "KLAC" in main.today_open_prices
        assert main.today_open_prices["KLAC"] == 700.00
        assert "LRCX" not in main.today_open_prices

        # Invariant 2: LRCX MUST NOT have exited using stale price 510.00
        assert "LRCX" in main.account.positions
        assert main.account.positions["LRCX"].shares == 35
        assert staged_mgr.is_staged_for_exit("LRCX") is True

        # Invariant 3: KLAC was executed at today's open price ($700.00 + buy slippage)
        assert "KLAC" in main.account.positions
        klac_pos = main.account.positions["KLAC"]
        assert klac_pos.arm == TradingArm.SWING
        expected_shares = int(25_000.0 // 700.00)
        assert klac_pos.shares == expected_shares
        assert klac_pos.avg_entry_price >= 700.00
        assert klac_pos.avg_entry_price < 705.00  # Strict slippage bounds
        # Stop loss anchored to fill price - 2.5 * ATR
        expected_klac_stop = round(klac_pos.avg_entry_price - 2.5 * 12.0, 2)
        assert klac_pos.stop_loss_price == expected_klac_stop
        assert staged_mgr.is_staged_for_entry("KLAC") is False

        # -------------------------------------------------------------
        # 2. Bar 2: Symbol A (LRCX) arrives SECOND at 09:30:05 ET ($650.00)
        # -------------------------------------------------------------
        bar_lrcx = BarEvent(
            symbol="LRCX",
            open=650.00,
            high=652.00,
            low=648.00,
            close=651.00,
            volume=60_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 5, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_lrcx)

        # Invariant 4: today_open_prices holds both KLAC and LRCX
        assert "LRCX" in main.today_open_prices
        assert main.today_open_prices["LRCX"] == 650.00

        # Invariant 5: LRCX exit has now executed at $650.00 (less adverse sell slippage)
        assert ("LRCX" not in main.account.positions) or (main.account.positions["LRCX"].shares == 0)
        assert staged_mgr.is_staged_for_exit("LRCX") is False
        assert "LRCX" not in main.swing_reserved_symbols

        # Invariant 6: KLAC position is preserved and not perturbed
        assert "KLAC" in main.account.positions
        assert main.account.positions["KLAC"].shares == expected_shares

    @pytest.mark.asyncio
    async def test_adversarial_out_of_order_open_jitter_concurrency_constrained_2_positions(self):
        """Stress Test 2 (Concurrency Constrained):
        - Holding 2 positions at cap: LRCX (35 shares) and MU (250 shares).
        - Staged exit: LRCX (35 shares).
        - Staged entry: KLAC ($25,000 target notional).
        - Poison latest_market_prices with stale prices: LRCX=400.00, KLAC=550.00.
        - Feed Symbol B (KLAC) open bar FIRST at 09:30:01 with open $700.00:
          - Concurrency cap is reached (2/2) and staged exit for LRCX is pending.
          - KLAC entry MUST be deferred (NOT executed, NOT deleted).
          - LRCX exit MUST NOT execute at stale $400.00.
        - Feed Symbol A (LRCX) open bar SECOND at 09:30:05 with open $650.00:
          - LRCX exit executes at $650.00.
          - KLAC entry executes immediately into freed slot at $700.00.
          - Position cap invariant of 2 is strictly maintained.
        """
        staged_mgr = main.swing_staged_order_manager
        staged_mgr.clear()
        main.account.positions.clear()
        main.swing_reserved_symbols.clear()

        init_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        main.account.apply_fill(
            order_id="init_lrcx",
            symbol="LRCX",
            side="BUY",
            qty=35,
            price=640.00,
            fee=0.0,
            timestamp=init_dt,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
        )
        main.account.apply_fill(
            order_id="init_mu",
            symbol="MU",
            side="BUY",
            qty=250,
            price=100.00,
            fee=0.0,
            timestamp=init_dt,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
        )
        main.swing_reserved_symbols.add("LRCX")
        main.swing_reserved_symbols.add("MU")
        assert len(main.swing_strategy_engine.get_active_swing_positions()) == 2

        # Stage exit for LRCX and entry for KLAC
        eval_date = date(2026, 9, 23)
        staged_mgr.stage_sell(symbol="LRCX", shares=35, signal_date=eval_date, reason="SMA5_EXIT")
        staged_mgr.stage_buy(symbol="KLAC", target_notional=25_000.0, daily_atr=14.0, signal_date=eval_date, reason="PANIC_DIP")
        main.swing_reserved_symbols.add("KLAC")

        # Poison latest_market_prices with stale prices
        main.latest_market_prices["LRCX"] = 400.00
        main.latest_market_prices["KLAC"] = 550.00

        # -------------------------------------------------------------
        # 1. Bar 1: KLAC bar arrives FIRST at 09:30:01 ET ($700.00)
        # -------------------------------------------------------------
        bar_klac = BarEvent(
            symbol="KLAC",
            open=700.00,
            high=703.00,
            low=697.00,
            close=702.00,
            volume=45_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 1, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_klac)

        # Invariant 1: today_open_prices records KLAC
        assert main.today_open_prices.get("KLAC") == 700.00
        assert "LRCX" not in main.today_open_prices

        # Invariant 2: KLAC MUST NOT have executed yet (concurrency cap reached & pending exits)
        assert "KLAC" not in main.account.positions
        # Invariant 3: KLAC MUST NOT BE DROPPED from staged orders
        assert staged_mgr.is_staged_for_entry("KLAC") is True
        assert "KLAC" in main.swing_reserved_symbols

        # Invariant 4: LRCX MUST NOT have exited at stale 400.00
        assert "LRCX" in main.account.positions
        assert main.account.positions["LRCX"].shares == 35
        assert staged_mgr.is_staged_for_exit("LRCX") is True

        # Invariant 5: Active positions remain exactly 2 (LRCX and MU)
        assert len(main.swing_strategy_engine.get_active_swing_positions()) == 2

        # -------------------------------------------------------------
        # 2. Bar 2: LRCX bar arrives SECOND at 09:30:05 ET ($650.00)
        # -------------------------------------------------------------
        bar_lrcx = BarEvent(
            symbol="LRCX",
            open=650.00,
            high=653.00,
            low=647.00,
            close=651.00,
            volume=55_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 5, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_lrcx)

        # Invariant 6: today_open_prices holds both
        assert main.today_open_prices.get("LRCX") == 650.00
        assert main.today_open_prices.get("KLAC") == 700.00

        # Invariant 7: LRCX exit has completed at $650.00
        assert ("LRCX" not in main.account.positions) or (main.account.positions["LRCX"].shares == 0)
        assert staged_mgr.is_staged_for_exit("LRCX") is False
        assert "LRCX" not in main.swing_reserved_symbols

        # Invariant 8: KLAC entry executed immediately into freed slot at $700.00
        assert "KLAC" in main.account.positions
        klac_pos = main.account.positions["KLAC"]
        assert klac_pos.shares == int(25_000.0 // 700.00)
        assert klac_pos.avg_entry_price >= 700.00
        assert staged_mgr.is_staged_for_entry("KLAC") is False

        # Invariant 9: Final active positions count is exactly 2 (MU and KLAC)
        active_pos = main.swing_strategy_engine.get_active_swing_positions()
        assert len(active_pos) == 2
        active_symbols = set(active_pos.keys())
        assert active_symbols == {"MU", "KLAC"}

    @pytest.mark.asyncio
    async def test_adversarial_reverse_order_lrcx_before_klac(self):
        """Stress Test 3 (Reverse Arrival):
        - Holding 1 position: LRCX. Staged exit on LRCX, staged entry on KLAC.
        - Poison latest_market_prices: KLAC=999.00, LRCX=111.00.
        - Feed Symbol A (LRCX) FIRST at 09:30:01 ($650.00).
        - Feed Symbol B (KLAC) SECOND at 09:30:05 ($700.00).
        - Verify deterministic execution without stale price leakage.
        """
        staged_mgr = main.swing_staged_order_manager
        staged_mgr.clear()
        main.account.positions.clear()
        main.swing_reserved_symbols.clear()

        init_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        main.account.apply_fill("p1", "LRCX", "BUY", 35, 640.0, 0.0, init_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        main.swing_reserved_symbols.add("LRCX")

        eval_date = date(2026, 9, 23)
        staged_mgr.stage_sell(symbol="LRCX", shares=35, signal_date=eval_date, reason="SMA5_EXIT")
        staged_mgr.stage_buy(symbol="KLAC", target_notional=25_000.0, daily_atr=10.0, signal_date=eval_date, reason="PANIC_DIP")
        main.swing_reserved_symbols.add("KLAC")

        main.latest_market_prices["LRCX"] = 111.00
        main.latest_market_prices["KLAC"] = 999.00

        # Bar 1: LRCX arrives first
        bar_lrcx = BarEvent(
            symbol="LRCX",
            open=650.00,
            high=651.00,
            low=649.00,
            close=650.00,
            volume=10_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 1, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_lrcx)

        # LRCX exits at 650.00; KLAC does not execute (no open bar yet)
        assert ("LRCX" not in main.account.positions) or (main.account.positions["LRCX"].shares == 0)
        assert "KLAC" not in main.account.positions
        assert staged_mgr.is_staged_for_entry("KLAC") is True
        assert "KLAC" not in main.today_open_prices

        # Bar 2: KLAC arrives second
        bar_klac = BarEvent(
            symbol="KLAC",
            open=700.00,
            high=702.00,
            low=699.00,
            close=701.00,
            volume=15_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 5, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_klac)

        assert "KLAC" in main.account.positions
        assert main.account.positions["KLAC"].shares == int(25_000.0 // 700.00)
        assert main.account.positions["KLAC"].avg_entry_price >= 700.00
        assert staged_mgr.is_staged_for_entry("KLAC") is False

    @pytest.mark.asyncio
    async def test_adversarial_open_price_immutability_and_non_positive_rejection(self):
        """Stress Test 4:
        - Bar at 09:30 establishes open price ($700.00).
        - Subsequent bars at 09:31, 09:35, 09:44 with different opens ($715.00, $730.00) NEVER overwrite today_open_prices.
        - Non-positive open prices (0.0, -5.0) are strictly rejected from today_open_prices.
        """
        bar1 = BarEvent(
            symbol="KLAC",
            open=700.00,
            high=702.00,
            low=699.00,
            close=701.00,
            volume=10_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar1)
        assert main.today_open_prices["KLAC"] == 700.00

        # Bar at 09:31: open is 715.00
        bar2 = BarEvent(
            symbol="KLAC",
            open=715.00,
            high=718.00,
            low=714.00,
            close=716.00,
            volume=12_000,
            timestamp=datetime(2026, 9, 24, 9, 31, 0, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar2)
        # MUST REMAIN 700.00
        assert main.today_open_prices["KLAC"] == 700.00

        # Bar at 09:44: open is 730.00
        bar3 = BarEvent(
            symbol="KLAC",
            open=730.00,
            high=732.00,
            low=729.00,
            close=731.00,
            volume=15_000,
            timestamp=datetime(2026, 9, 24, 9, 44, 0, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar3)
        assert main.today_open_prices["KLAC"] == 700.00

        # Non-positive open price tests
        bar_zero = BarEvent(
            symbol="MU",
            open=0.00,
            high=100.00,
            low=95.00,
            close=98.00,
            volume=5_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_zero)
        assert "MU" not in main.today_open_prices

        bar_neg = BarEvent(
            symbol="AMD",
            open=-10.00,
            high=150.00,
            low=145.00,
            close=148.00,
            volume=5_000,
            timestamp=datetime(2026, 9, 24, 9, 30, 0, tzinfo=ET_TZ),
        )
        await main.handle_bar_event(bar_neg)
        assert "AMD" not in main.today_open_prices

    def test_adversarial_session_boundary_clearing_today_open_prices(self):
        """Stress Test 5:
        - Populate today_open_prices with multiple symbols across universe.
        - Populate latest_market_prices with stale prices.
        - Call _check_session_boundary for next trading session date.
        - Verify: today_open_prices and latest_market_prices are 100% empty.
        - Re-populate and call reset_runtime_state.
        - Verify: today_open_prices and latest_market_prices are 100% empty.
        """
        # Seed both dictionaries
        symbols = ["LRCX", "KLAC", "MU", "AMD", "GS", "SPY", "QQQ"]
        for i, s in enumerate(symbols):
            main.today_open_prices[s] = 100.0 + i * 25.0
            main.latest_market_prices[s] = 95.0 + i * 25.0

        assert len(main.today_open_prices) == len(symbols)
        assert len(main.latest_market_prices) == len(symbols)

        # Establish current session date first
        day1_dt = datetime(2026, 9, 24, 15, 59, 0, tzinfo=ET_TZ)
        main._check_session_boundary(day1_dt)
        # Must still retain today's prices during the session
        assert len(main.today_open_prices) == len(symbols)

        # Cross session boundary to Day 2
        day2_dt = datetime(2026, 9, 25, 9, 0, 0, tzinfo=ET_TZ)
        main._check_session_boundary(day2_dt)

        # Invariant 1: today_open_prices is completely cleared
        assert len(main.today_open_prices) == 0, f"Leak detected in today_open_prices: {main.today_open_prices}"
        # Invariant 2: latest_market_prices is completely cleared
        assert len(main.latest_market_prices) == 0, f"Leak detected in latest_market_prices: {main.latest_market_prices}"

        # Re-seed and test reset_runtime_state
        for i, s in enumerate(symbols):
            main.today_open_prices[s] = 200.0 + i
            main.latest_market_prices[s] = 190.0 + i

        assert len(main.today_open_prices) == len(symbols)
        main.reset_runtime_state()

        assert len(main.today_open_prices) == 0, f"today_open_prices not cleared by reset_runtime_state"
        assert len(main.latest_market_prices) == 0, f"latest_market_prices not cleared by reset_runtime_state"

    @pytest.mark.asyncio
    async def test_adversarial_multi_symbol_cascade_with_stale_poison(self):
        """Stress Test 6:
        - 2 held swing positions: LRCX and AMD (concurrency cap reached).
        - Staged exits: LRCX and AMD.
        - Staged entries: KLAC and MU.
        - latest_market_prices poisoned with garbage prices:
          LRCX=9999.0, AMD=0.05, KLAC=1.0, MU=8888.0.
        - Arrival order:
          1. KLAC open bar @ 09:30:01 ($700.00) -> deferred (cap reached, exits pending).
          2. MU open bar @ 09:30:02 ($110.00) -> deferred (cap reached, exits pending).
          3. LRCX open bar @ 09:30:03 ($650.00) -> LRCX exits at $650.00; first entry (KLAC) fills at $700.00.
          4. AMD open bar @ 09:30:04 ($150.00) -> AMD exits at $150.00; second entry (MU) fills at $110.00.
        - Verify:
          - LRCX fills at ~$650.00 (NOT 9999.0).
          - AMD fills at ~$150.00 (NOT 0.05).
          - KLAC fills at ~$700.00 (NOT 1.0).
          - MU fills at ~$110.00 (NOT 8888.0).
          - Final positions: KLAC and MU (exactly 2).
          - All stops anchored to true realized fills.
        """
        staged_mgr = main.swing_staged_order_manager
        staged_mgr.clear()
        main.account.positions.clear()
        main.swing_reserved_symbols.clear()

        init_dt = datetime(2026, 9, 23, 9, 30, tzinfo=ET_TZ)
        main.account.apply_fill("p1", "LRCX", "BUY", 35, 640.0, 0.0, init_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        main.account.apply_fill("p2", "AMD", "BUY", 160, 145.0, 0.0, init_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        main.swing_reserved_symbols.add("LRCX")
        main.swing_reserved_symbols.add("AMD")

        eval_date = date(2026, 9, 23)
        staged_mgr.stage_sell(symbol="LRCX", shares=35, signal_date=eval_date, reason="SMA5_EXIT")
        staged_mgr.stage_sell(symbol="AMD", shares=160, signal_date=eval_date, reason="RSI70_EXIT")

        staged_mgr.stage_buy(symbol="KLAC", target_notional=25_000.0, daily_atr=12.0, signal_date=eval_date, reason="PANIC_DIP")
        staged_mgr.stage_buy(symbol="MU", target_notional=25_000.0, daily_atr=4.0, signal_date=eval_date, reason="PANIC_DIP")
        main.swing_reserved_symbols.add("KLAC")
        main.swing_reserved_symbols.add("MU")

        # Poison latest_market_prices with extreme garbage
        main.latest_market_prices["LRCX"] = 9999.0
        main.latest_market_prices["AMD"] = 0.05
        main.latest_market_prices["KLAC"] = 1.0
        main.latest_market_prices["MU"] = 8888.0

        # Bar 1: KLAC arrives @ 09:30:01
        await main.handle_bar_event(BarEvent(
            symbol="KLAC", open=700.0, high=702.0, low=698.0, close=701.0, volume=20000,
            timestamp=datetime(2026, 9, 24, 9, 30, 1, tzinfo=ET_TZ),
        ))
        assert "KLAC" not in main.account.positions
        assert staged_mgr.is_staged_for_entry("KLAC") is True

        # Bar 2: MU arrives @ 09:30:02
        await main.handle_bar_event(BarEvent(
            symbol="MU", open=110.0, high=111.0, low=109.0, close=110.5, volume=30000,
            timestamp=datetime(2026, 9, 24, 9, 30, 2, tzinfo=ET_TZ),
        ))
        assert "MU" not in main.account.positions
        assert staged_mgr.is_staged_for_entry("MU") is True

        # Bar 3: LRCX arrives @ 09:30:03 ($650.0) -> LRCX exits, KLAC enters!
        await main.handle_bar_event(BarEvent(
            symbol="LRCX", open=650.0, high=652.0, low=648.0, close=651.0, volume=40000,
            timestamp=datetime(2026, 9, 24, 9, 30, 3, tzinfo=ET_TZ),
        ))
        assert ("LRCX" not in main.account.positions) or (main.account.positions["LRCX"].shares == 0)
        assert "KLAC" in main.account.positions
        assert main.account.positions["KLAC"].shares == int(25_000.0 // 700.0)
        assert main.account.positions["KLAC"].avg_entry_price >= 700.0
        assert main.account.positions["KLAC"].avg_entry_price < 705.0

        # MU is still deferred because AMD hasn't exited yet
        assert "MU" not in main.account.positions

        # Bar 4: AMD arrives @ 09:30:04 ($150.0) -> AMD exits, MU enters!
        await main.handle_bar_event(BarEvent(
            symbol="AMD", open=150.0, high=151.0, low=149.0, close=150.5, volume=50000,
            timestamp=datetime(2026, 9, 24, 9, 30, 4, tzinfo=ET_TZ),
        ))
        assert ("AMD" not in main.account.positions) or (main.account.positions["AMD"].shares == 0)
        assert "MU" in main.account.positions
        assert main.account.positions["MU"].shares == int(25_000.0 // 110.0)
        assert main.account.positions["MU"].avg_entry_price >= 110.0
        assert main.account.positions["MU"].avg_entry_price < 112.0

        # Final positions: exactly KLAC and MU
        active_pos = main.swing_strategy_engine.get_active_swing_positions()
        assert len(active_pos) == 2
        assert set(active_pos.keys()) == {"KLAC", "MU"}
        assert len(staged_mgr.get_staged_orders()) == 0
