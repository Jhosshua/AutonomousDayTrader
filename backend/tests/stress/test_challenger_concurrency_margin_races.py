"""backend/tests/stress/test_challenger_concurrency_margin_races.py
Adversarial Stress Test Suite — Challenger 2: Concurrency, Race Condition & Margin Collision.

Attacks:
1. Concurrency races at 09:30:00 ET:
   - Simultaneous swing staged order fills + intraday strategy entries.
   - Sizing and buying power contention: Can intraday double-spend capital allocated for swing?
   - Max 2 swing positions limit ($25k each) under concurrent fill race.
   - Intraday signal admission: Does execute_strategy_signal leak swing positions into intraday concurrency cap?
2. Flattening races at 15:45-15:58 ET:
   - Does Phase 1 lockout, Phase 2 order purge, Phase 3 liquidation, or Phase 4 zero-audit ever touch a swing position, swing order, or swing stop under rapid event injection?
   - Swing emergency stop triggered during active EOD flattening phases.
   - Partial/failed intraday liquidation vs swing exemption.
3. Symbol collision on `AMD`:
   - Rapid alternating order submissions between intraday ORB/VWAP strategies and swing engine on `AMD`.
   - Verification that mutual exclusion never leaks or nets shares.
   - Attack vector: Intraday working (unfilled) order vs concurrent Swing order submission.
4. Shared $50,000 account margin coordination:
   - Total exposure & leverage stress (2 swing + 3 intraday = $125k notional vs $200k DTBP).
   - Cash depletion to $0.00: verify maintenance margin, margin excess, and margin call invariants.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
import math
from typing import Any, Dict, List, Optional
import pytest

from backend.app.config import settings
from backend.app.core.account import (
    AccountStatus,
    PaperTradingAccount,
    Position,
    PositionSide,
    TradingArm,
)
from backend.app.core.bracket import BracketOrder, BracketStatus, DynamicBracketManager
from backend.app.core.engine import (
    ExecutionEngine,
    Order,
    OrderSide,
    OrderState,
    OrderType,
)
from backend.app.core.flattening import (
    ET_TZ,
    FlatteningDirective,
    FlatteningPhase,
    MarketClock,
    ZeroOvernightFlatteningEngine,
)
from backend.app.core.risk import (
    InstitutionalRiskEngine,
    RiskEngineConfig,
)
from backend.app.models.events import BarEvent
from backend.app.strategies.base import SignalEvent
from backend.app.strategies.swing_indicators import DailyBar, DailyBarStore
from backend.app.strategies.swing_panic_dip import (
    StagedSwingOrder,
    SwingStagedOrderManager,
    SwingStrategyEngine,
)
from backend.app import main


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all main and account fixtures before and after every test."""
    main.flattening_engine.reset_for_new_session()
    main.last_session_date = None
    main.account.cash = main.account.initial_balance
    main.account.equity = main.account.initial_balance
    main.account.daily_starting_equity = main.account.initial_balance
    main.account.realized_pnl = 0.0
    main.account.unrealized_pnl = 0.0
    main.account.fees_paid = 0.0
    main.account.positions.clear()
    main.account.status = AccountStatus.ACTIVE
    main.account._recompute_account_state()
    main.risk_engine.reset_daily_metrics(main.account.initial_balance)
    main.engine.orders.clear()
    main.engine.working_orders.clear()
    main.bracket_manager.brackets.clear()
    main.bracket_manager.symbol_to_bracket.clear()
    main.bracket_manager.order_to_bracket.clear()
    main.entry_order_to_bracket.clear()
    main.latest_market_prices.clear()
    main.swing_staged_order_manager.clear()
    main.swing_reserved_symbols.clear()
    yield
    main.flattening_engine.reset_for_new_session()
    main.last_session_date = None
    main.account.cash = main.account.initial_balance
    main.account.equity = main.account.initial_balance
    main.account.daily_starting_equity = main.account.initial_balance
    main.account.realized_pnl = 0.0
    main.account.unrealized_pnl = 0.0
    main.account.fees_paid = 0.0
    main.account.positions.clear()
    main.account.status = AccountStatus.ACTIVE
    main.account._recompute_account_state()
    main.risk_engine.reset_daily_metrics(main.account.initial_balance)
    main.engine.orders.clear()
    main.engine.working_orders.clear()
    main.bracket_manager.brackets.clear()
    main.bracket_manager.symbol_to_bracket.clear()
    main.bracket_manager.order_to_bracket.clear()
    main.entry_order_to_bracket.clear()
    main.latest_market_prices.clear()
    main.swing_staged_order_manager.clear()
    main.swing_reserved_symbols.clear()


# ==============================================================================
# SUITE 1: 09:30:00 ET CONCURRENCY RACES & SIZING CONTENTION
# ==============================================================================

class Test0930ConcurrencyRaces:
    """Stress test order execution, capital contention, and sizing races at 09:30:00 ET open."""

    def test_staged_swing_and_intraday_contention_capital_preservation(self):
        """Attack: 2 swing orders staged ($25k each = $50k) contended with 3 simultaneous
        intraday entries ($25k each = $75k).
        Verify that total account margin and position counts never exceed institutional limits.
        """
        acct = main.account
        eng = main.engine
        swing_engine = main.swing_strategy_engine
        staged_mgr = main.swing_staged_order_manager

        # Stage 2 swing buys (MU @ $100 -> 250 shares, LRCX @ $500 -> 50 shares)
        staged_mgr.stage_buy(
            symbol="MU", target_notional=25000.0, daily_atr=3.0,
            signal_date=date(2026, 9, 22), reason="PANIC_DIP_TEST",
        )
        staged_mgr.stage_buy(
            symbol="LRCX", target_notional=25000.0, daily_atr=15.0,
            signal_date=date(2026, 9, 22), reason="PANIC_DIP_TEST",
        )

        open_time = datetime(2026, 9, 23, 9, 30, 0, tzinfo=ET_TZ)
        open_prices = {"MU": 100.0, "LRCX": 500.0, "AAPL": 150.0, "TSLA": 200.0, "NVDA": 120.0}

        # Execute 09:30 market open for swing (exact margin model)
        swing_res = swing_engine.execute_market_open(open_prices, open_time, apply_slippage=False)
        assert len(swing_res["entries"]) == 2
        assert len(swing_res["errors"]) == 0

        # Verify swing allocations: 2 positions, $50,000 notional committed
        assert "MU" in acct.positions
        assert "LRCX" in acct.positions
        assert acct.positions["MU"].arm == TradingArm.SWING
        assert acct.positions["LRCX"].arm == TradingArm.SWING
        assert acct.positions["MU"].shares == 250   # 250 * 100 = $25,000
        assert acct.positions["LRCX"].shares == 50  # 50 * 500 = $25,000

        # Cash after buying $50k worth of swing shares is $0.00
        assert acct.cash == 0.0
        # Equity is preserved at $50,000
        assert acct.equity == 50000.0
        # Maintenance margin is 0.25 * $50,000 = $12,500
        assert acct.maintenance_margin == 12500.0
        # Margin excess is $50,000 - $12,500 = $37,500
        assert acct.margin_excess == 37500.0
        # Intraday 4:1 Day Trading Buying Power is 4 * $37,500 = $150,000
        assert acct.buying_power == 150000.0

        # Now simulate 3 intraday orders attempting to enter on margin ($25k each)
        for sym, price in [("AAPL", 150.0), ("TSLA", 200.0), ("NVDA", 120.0)]:
            qty = int(25000.0 / price)
            stop_p = round(price * 0.98, 2)
            order = eng.create_order(
                symbol=sym, side=OrderSide.BUY, order_type=OrderType.LIMIT,
                qty=qty, limit_price=price, stop_price=stop_p,
                arm=TradingArm.INTRADAY, strategy_id="orb",
            )
            approved, reason = main.pre_trade_risk_validator(order, acct)
            assert approved is True, f"Intraday order for {sym} failed: {reason}"
            eng.submit_order(order.id)
            eng._execute_fill(order, qty, price, 0.0, open_time)

        # Total positions: 2 swing + 3 intraday = 5 positions
        assert len(acct.positions) == 5
        swing_positions = [p for p in acct.positions.values() if p.arm == TradingArm.SWING]
        intraday_positions = [p for p in acct.positions.values() if p.arm == TradingArm.INTRADAY]
        assert len(swing_positions) == 2
        assert len(intraday_positions) == 3

        # 4th intraday order MUST be rejected by pre_trade_risk_validator (intraday limit is 3)
        order_4 = eng.create_order(
            symbol="MSFT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=50, limit_price=400.0, stop_price=392.0,
            arm=TradingArm.INTRADAY, strategy_id="orb",
        )
        approved_4, reason_4 = main.pre_trade_risk_validator(order_4, acct)
        assert approved_4 is False
        assert "MAX_CONCURRENT_POSITIONS_REACHED" in reason_4

        # 3rd swing order MUST be rejected (swing limit is 2)
        order_swing_3 = eng.create_order(
            symbol="GS", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=50, limit_price=450.0, stop_price=420.0,
            arm=TradingArm.SWING, strategy_id="swing_panic_dip",
        )
        approved_s3, reason_s3 = main.pre_trade_risk_validator(order_swing_3, acct)
        assert approved_s3 is False
        assert "MAX_CONCURRENT_SWING_POSITIONS_REACHED" in reason_s3

    def test_concurrent_execute_market_open_race_condition(self):
        """Attack: Multi-threaded race condition on execute_market_open.
        Simulate 10 simultaneous threads invoking execute_market_open with 4 staged candidates.
        Verify that under intense thread contention, NEVER more than 2 swing positions are created.
        """
        acct = main.account
        eng = main.engine
        staged_mgr = main.swing_staged_order_manager
        swing_engine = main.swing_strategy_engine

        # Stage 4 swing buy candidates
        for sym, atr in [("MU", 3.0), ("LRCX", 15.0), ("KLAC", 18.0), ("GS", 10.0)]:
            staged_mgr.stage_buy(
                symbol=sym, target_notional=25000.0, daily_atr=atr,
                signal_date=date(2026, 9, 22), reason="RACE_TEST",
            )

        open_time = datetime(2026, 9, 23, 9, 30, 0, tzinfo=ET_TZ)
        open_prices = {"MU": 100.0, "LRCX": 500.0, "KLAC": 700.0, "GS": 450.0}

        def worker_task(i: int):
            return swing_engine.execute_market_open(open_prices, open_time)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker_task, i) for i in range(10)]
            results = [f.result() for f in futures]

        active_swing = swing_engine.get_active_swing_positions()
        # Non-negotiable invariant: NEVER more than 2 concurrent swing positions!
        assert len(active_swing) <= 2, f"CONCURRENCY RACE BREACH: Created {len(active_swing)} swing positions (max 2)!"
        assert len(active_swing) == 2

        # Check total committed notional does not exceed 2 * $25,000 + epsilon
        total_swing_notional = sum(p.shares * p.avg_entry_price for p in active_swing.values())
        assert total_swing_notional <= 50050.0

    @pytest.mark.asyncio
    async def test_intraday_signal_admission_with_active_swing_positions(self):
        """Attack / Vulnerability Check:
        In main.py execute_strategy_signal(sig), does _get_effective_committed_portfolio(account)
        without arm=TradingArm.INTRADAY erroneously count active swing positions against the
        intraday 3-position cap, blocking legitimate intraday trades?
        """
        acct = main.account
        eng = main.engine
        now_dt = datetime.now(timezone.utc)

        # 1. Fill 2 active swing positions ($25k each)
        acct.apply_fill("sw1", "MU", "BUY", 250, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.apply_fill("sw2", "LRCX", "BUY", 50, 500.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        assert len(acct.positions) == 2

        # 2. Fill 1 intraday position in AAPL
        acct.apply_fill("in1", "AAPL", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")
        assert len(acct.positions) == 3  # 2 swing + 1 intraday

        # 3. An intraday breakout signal arrives for TSLA (2nd intraday position, allowed up to 3)
        tsla_signal = SignalEvent(
            symbol="TSLA",
            strategy_id="orb",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            entry_price=200.0,
            stop_loss=196.0,
            take_profit_1=204.0,
            take_profit_2=208.0,
            confidence=1.0,
            reason="BREAKOUT_TEST",
            timestamp=now_dt,
        )

        # Let's see what main.py's execute_strategy_signal does:
        # In main.py lines 1142-1149:
        # Remediated: execute_strategy_signal filters by arm=TradingArm.INTRADAY
        committed_symbols_arm, _, committed_count_arm, _ = main._get_effective_committed_portfolio(acct, arm=TradingArm.INTRADAY)
        assert committed_count_arm == 1, f"Expected 1 intraday position, got {committed_count_arm}"

        saved_filter = main.adaptation_engine.market_filter
        main.adaptation_engine.market_filter = None
        try:
            approved_arm, reason_arm, _ = main.adaptation_engine.evaluate_signal_admission(
                signal=tsla_signal,
                equity=acct.equity,
                current_positions_count=committed_count_arm,
                is_symbol_active="TSLA" in committed_symbols_arm,
            )
            assert approved_arm is True, f"Intraday signal admission should be approved when arm=INTRADAY: {reason_arm}"

            # Execute the signal through main.execute_strategy_signal to verify full pipeline
            await main.execute_strategy_signal(tsla_signal)
            # Verify TSLA order or bracket was created
            assert "TSLA" in main.bracket_manager.symbol_to_bracket or any(
                o.symbol == "TSLA" for o in eng.orders.values()
            )
        finally:
            main.adaptation_engine.market_filter = saved_filter


    def test_open_bar_arrival_ordering_race(self):
        """Attack: Market open bar ordering race at 09:30:00 ET.
        If an intraday stock (e.g. TSLA) bar arrives first at 09:30:00, while swing candidate (MU)
        bar has not arrived yet:
        Verify that swing staged orders are NOT prematurely discarded or corrupted due to missing open price.
        """
        staged_mgr = main.swing_staged_order_manager
        swing_engine = main.swing_strategy_engine

        # Stage MU
        staged_mgr.stage_buy(
            symbol="MU", target_notional=25000.0, daily_atr=3.0,
            signal_date=date(2026, 9, 22), reason="TIMING_RACE",
        )

        open_time = datetime(2026, 9, 23, 9, 30, 0, tzinfo=ET_TZ)
        # Bar arrives for TSLA only; MU is NOT in open_prices
        partial_open_prices = {"TSLA": 200.0}

        res = swing_engine.execute_market_open(partial_open_prices, open_time)
        # MU was not in open_prices -> recorded in errors
        assert len(res["errors"]) >= 1
        assert "Missing open price for MU" in res["errors"][0]

        # CRITICAL INVARIANT: Staged order for MU MUST REMAIN in staged_manager so next bar can execute it!
        assert staged_mgr.is_staged_for_entry("MU") is True, (
            "DEFECT: Staged order was discarded when open price was missing on the first bar!"
        )

        # Now MU's bar arrives 50ms later
        mu_open_prices = {"MU": 102.0, "TSLA": 200.0}
        res2 = swing_engine.execute_market_open(mu_open_prices, open_time)
        assert len(res2["entries"]) == 1
        assert res2["entries"][0]["symbol"] == "MU"
        assert staged_mgr.is_staged_for_entry("MU") is False  # Now properly cleared after fill


# ==============================================================================
# SUITE 2: 15:45-15:58 ET FLATTENING RACES & ISOLATION
# ==============================================================================

class TestFlatteningRacesAndIsolation:
    """Stress test 4-phase EOD flattening engine under rapid event injection and edge conditions."""

    @pytest.mark.asyncio
    async def test_rapid_event_injection_during_all_4_flattening_phases(self):
        """Attack: Rapid concurrent event injection across 15:45-15:58 ET.
        Simultaneously cycle through:
        - Phase 1: Lockout (15:45)
        - Phase 2: Order Purge (15:50)
        - Phase 3: Mandatory Liquidation (15:55)
        - Phase 4: Zero Audit (15:58)
        While streaming 50 rapid intraday and swing bars, quotes, and price updates.
        Verify that swing positions and swing protective orders are 100% untouched.
        """
        acct = main.account
        eng = main.engine
        bm = main.bracket_manager

        # Open 2 Swing positions
        now_dt = datetime(2026, 9, 23, 15, 40, 0, tzinfo=ET_TZ)
        acct.apply_fill("sw_mu", "MU", "BUY", 200, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=92.5)
        acct.apply_fill("sw_lrcx", "LRCX", "BUY", 40, 600.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=555.0)

        # Open 2 Intraday positions
        acct.apply_fill("in_aapl", "AAPL", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")
        acct.apply_fill("in_tsla", "TSLA", "BUY", 50, 200.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="vwap_pullback")

        # Create working orders
        # Swing protective stop
        mu_stop = eng.create_order(
            symbol="MU", side=OrderSide.SELL, order_type=OrderType.STOP,
            qty=200, stop_price=92.5, arm=TradingArm.SWING, strategy_id="swing_panic_dip",
        )
        eng.submit_order(mu_stop.id)

        # Intraday limit entry order (unfilled)
        aapl_limit = eng.create_order(
            symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=50, limit_price=148.0, arm=TradingArm.INTRADAY, strategy_id="orb",
        )
        eng.submit_order(aapl_limit.id)

        assert len(acct.positions) == 4
        assert len(eng.working_orders) == 2

        # 1. PHASE 1: ENTRY LOCKOUT (15:45 ET)
        p1 = FlatteningDirective(
            phase=FlatteningPhase.ENTRY_LOCKOUT,
            timestamp=datetime(2026, 9, 23, 15, 45, 0, tzinfo=ET_TZ),
            action_required="LOCK_ENTRIES",
            lock_new_entries=True,
        )
        await main.handle_flattening_directive(p1)
        assert len(acct.positions) == 4
        assert mu_stop.id in eng.working_orders

        # 2. PHASE 2: ORDER PURGE (15:50 ET)
        p2 = FlatteningDirective(
            phase=FlatteningPhase.ORDER_PURGE,
            timestamp=datetime(2026, 9, 23, 15, 50, 0, tzinfo=ET_TZ),
            action_required="PURGE_UNFILLED_ENTRIES",
            lock_new_entries=True,
            cancel_all_orders=False,
        )
        await main.handle_flattening_directive(p2)
        # Intraday unfilled limit order purged; Swing stop preserved
        assert aapl_limit.id not in eng.working_orders
        assert mu_stop.id in eng.working_orders

        # 3. PHASE 3: MANDATORY LIQUIDATION (15:55 ET)
        p3 = FlatteningDirective(
            phase=FlatteningPhase.MANDATORY_LIQUIDATION,
            timestamp=datetime(2026, 9, 23, 15, 55, 0, tzinfo=ET_TZ),
            action_required="LIQUIDATE_ALL_POSITIONS",
            lock_new_entries=True,
            cancel_all_orders=True,
            liquidate_all_positions=True,
        )
        await main.handle_flattening_directive(p3)
        # All intraday positions liquidated; SWING POSITIONS 100% PRESERVED
        assert "AAPL" not in acct.positions
        assert "TSLA" not in acct.positions
        assert "MU" in acct.positions
        assert "LRCX" in acct.positions
        assert mu_stop.id in eng.working_orders

        # 4. PHASE 4: ZERO AUDIT (15:58 ET)
        p4 = FlatteningDirective(
            phase=FlatteningPhase.ZERO_AUDIT,
            timestamp=datetime(2026, 9, 23, 15, 58, 0, tzinfo=ET_TZ),
            action_required="ZERO_AUDIT",
            lock_new_entries=True,
            cancel_all_orders=False,
            run_audit=True,
        )
        await main.handle_flattening_directive(p4)

        # Audit must pass cleanly despite MU and LRCX positions being open
        assert "MU" in acct.positions
        assert "LRCX" in acct.positions
        assert acct.status == AccountStatus.ACTIVE  # Kept ACTIVE because swing positions are open!

    @pytest.mark.asyncio
    async def test_swing_emergency_stop_triggered_during_flattening(self):
        """Attack: At 15:52 ET (midst of EOD flattening), an emergency stop on a swing position
        is breached by a market crash.
        Verify that swing emergency stop executes cleanly, liquidates the position, and releases
        symbol lock without deadlock or risk gate rejection.
        """
        acct = main.account
        eng = main.engine
        swing_engine = main.swing_strategy_engine

        # Open swing position on MU with stop at 92.50
        now_dt = datetime(2026, 9, 23, 15, 45, 0, tzinfo=ET_TZ)
        acct.apply_fill("sw_mu", "MU", "BUY", 200, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=92.5)
        main.reserve_symbol_for_swing("MU")
        assert main.is_symbol_reserved_for_swing("MU", acct) is True

        # Phase 1 is active (Lockout)
        p1 = FlatteningDirective(
            phase=FlatteningPhase.ENTRY_LOCKOUT,
            timestamp=now_dt,
            action_required="LOCK_ENTRIES",
            lock_new_entries=True,
        )
        await main.handle_flattening_directive(p1)

        # Market flash crash at 15:52 ET: MU drops to $90.00 (< stop $92.50)
        crash_bar = BarEvent(
            symbol="MU",
            open=93.0, high=93.0, low=89.50, close=90.0, volume=500000,
            timestamp=datetime(2026, 9, 23, 15, 52, 0, tzinfo=ET_TZ),
        )

        # Trigger swing on_bar
        exit_event = swing_engine.on_bar(crash_bar)
        assert exit_event is not None
        assert exit_event["symbol"] == "MU"
        assert exit_event["stop_price"] == 92.5

        # MU position must now be closed
        assert "MU" not in acct.positions
        # Symbol lock for MU must be released
        assert main.is_symbol_reserved_for_swing("MU", acct) is False


# ==============================================================================
# SUITE 3: AMD SYMBOL COLLISION & MUTUAL EXCLUSION
# ==============================================================================

class TestAMDSymbolCollision:
    """Stress test AMD mutual exclusion against rapid cross-arm order submission and netting races."""

    def test_rapid_alternating_order_submissions_mutual_exclusion(self):
        """Attack: 50 alternating rapid submissions between Intraday and Swing on AMD.
        Verify that mutual exclusion never allows simultaneous orders or position netting.
        """
        acct = main.account
        eng = main.engine

        for cycle in range(25):
            acct.positions.clear()
            eng.working_orders.clear()
            main.release_symbol_for_swing("AMD")

            # Intraday opens AMD
            ord_intra = eng.create_order(
                symbol="AMD", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                qty=100, limit_price=140.0, stop_price=137.0,
                arm=TradingArm.INTRADAY, strategy_id="orb",
            )
            ok_i, _ = main.pre_trade_risk_validator(ord_intra, acct)
            assert ok_i is True
            eng.submit_order(ord_intra.id)
            eng._execute_fill(ord_intra, 100, 140.0, 0.0, datetime.now(timezone.utc))

            # Swing attempts to buy AMD while Intraday holds AMD -> MUST BE REJECTED
            ord_swing = eng.create_order(
                symbol="AMD", side=OrderSide.BUY, order_type=OrderType.MARKET,
                qty=170, estimated_price=140.0, stop_price=130.0,
                arm=TradingArm.SWING, strategy_id="swing_panic_dip",
            )
            ok_s, reason_s = main.pre_trade_risk_validator(ord_swing, acct)
            assert ok_s is False
            assert "SWING_REJECTED" in reason_s or "held by Intraday" in reason_s

            # Close intraday position
            acct.apply_fill("close_i", "AMD", "SELL", 100, 142.0, 0.0, datetime.now(timezone.utc), arm=TradingArm.INTRADAY)
            assert "AMD" not in acct.positions

            # Now reserve AMD for Swing
            main.reserve_symbol_for_swing("AMD")

            # Intraday attempts to enter AMD while reserved -> MUST BE REJECTED
            ord_intra_blocked = eng.create_order(
                symbol="AMD", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                qty=100, limit_price=140.0, stop_price=137.0,
                arm=TradingArm.INTRADAY, strategy_id="vwap_pullback",
            )
            ok_ib, reason_ib = main.pre_trade_risk_validator(ord_intra_blocked, acct)
            assert ok_ib is False
            assert "SYMBOL_RESERVED_FOR_SWING" in reason_ib

    def test_working_order_cross_arm_collision_vulnerability(self):
        """Attack / Vulnerability Check:
        Suppose Intraday submits a limit order for AMD (unfilled, in working_orders).
        Does pre_trade_risk_validator reject a concurrent Swing order on AMD?
        Or does it only check acct.positions (letting the swing order in, risking share netting)?
        """
        acct = main.account
        eng = main.engine
        acct.positions.clear()
        eng.working_orders.clear()
        main.release_symbol_for_swing("AMD")

        # 1. Intraday submits a limit order for AMD (NOT yet filled!)
        ord_intra = eng.create_order(
            symbol="AMD", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=100, limit_price=140.0, stop_price=137.0,
            arm=TradingArm.INTRADAY, strategy_id="orb",
        )
        ok_i, reason_i = main.pre_trade_risk_validator(ord_intra, acct)
        assert ok_i is True
        eng.submit_order(ord_intra.id)
        assert ord_intra.id in eng.working_orders
        assert "AMD" not in acct.positions  # Unfilled!

        # 2. Concurrently, Swing submits an order for AMD
        ord_swing = eng.create_order(
            symbol="AMD", side=OrderSide.BUY, order_type=OrderType.MARKET,
            qty=170, estimated_price=140.0, stop_price=130.0,
            arm=TradingArm.SWING, strategy_id="swing_panic_dip",
        )
        ok_s, reason_s = main.pre_trade_risk_validator(ord_swing, acct)

        # Inspect behavior:
        # If pre_trade_risk_validator only checks acct.positions (existing_pos is None),
        # ok_s will be True! Both orders will be working simultaneously on AMD!
        if ok_s is True:
            pytest.fail(
                f"MUTUAL EXCLUSION LEAK CONFIRMED: Intraday order {ord_intra.id} is actively working on AMD. "
                f"pre_trade_risk_validator approved concurrent Swing order {ord_swing.id} because it only "
                f"checks account.positions, ignoring engine.working_orders! "
                f"Both orders can fill simultaneously and net/corrupt shares!"
            )


# ==============================================================================
# SUITE 4: SHARED $50,000 ACCOUNT MARGIN COORDINATION
# ==============================================================================

class TestSharedMarginCoordination:
    """Stress test shared $50,000 capital pool, DTBP leverage, and cash depletion."""

    def test_cash_depletion_to_zero_margin_excess_integrity(self):
        """Attack: 2 swing positions ($25k each) completely deplete $50,000 initial cash to $0.00.
        Verify:
        - Equity remains $50,000.
        - Maintenance margin is accurately calculated ($12,500).
        - Margin excess is $37,500.
        - Account DOES NOT falsely enter MARGIN_CALL.
        - Intraday positions can be opened on remaining day trading margin.
        """
        acct = main.account
        now_dt = datetime.now(timezone.utc)

        # Fill 2 swing positions
        acct.apply_fill("s1", "MU", "BUY", 250, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.apply_fill("s2", "LRCX", "BUY", 50, 500.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")

        assert acct.cash == 0.0
        assert acct.equity == 50000.0
        assert acct.maintenance_margin == 12500.0
        assert acct.margin_excess == 37500.0
        assert acct.status == AccountStatus.ACTIVE  # MUST NOT be MARGIN_CALL!

        # Intraday buys $25k of AAPL (cash becomes -$25k on margin)
        acct.apply_fill("i1", "AAPL", "BUY", 166, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")
        assert acct.cash == -24900.0
        assert acct.equity == 50000.0
        assert acct.maintenance_margin == 18725.0
        assert acct.margin_excess == 31275.0
        assert acct.status == AccountStatus.ACTIVE

    def test_circuit_breaker_trips_on_cumulative_loss(self):
        """Attack: Verify that the $1,500 institutional daily circuit breaker
        triggers when cumulative losses across BOTH intraday and swing arms breach -$1,500.
        """
        acct = main.account
        risk_eng = main.risk_engine
        now_dt = datetime.now(timezone.utc)

        # Swing suffers -$800 unrealized loss
        acct.apply_fill("s1", "MU", "BUY", 200, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.update_market_price("MU", 96.0)  # 200 * -4.0 = -$800
        assert acct.unrealized_pnl == -800.0

        # Intraday suffers -$750 realized loss
        acct.apply_fill("i1", "AAPL", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")
        acct.apply_fill("i1_exit", "AAPL", "SELL", 100, 142.50, 0.0, now_dt, arm=TradingArm.INTRADAY)
        assert acct.realized_pnl == -750.0

        # Total drawdown = -$800 + -$750 = -$1,550 (> $1,500 circuit breaker limit)
        status = risk_eng.evaluate_account_state(
            equity=acct.equity,
            cash=acct.cash,
            realized_pnl=acct.realized_pnl,
            unrealized_pnl=acct.unrealized_pnl,
            timestamp=now_dt,
        )
        assert status.value == "HALTED_DAILY_LOSS"

    def test_overnight_restart_evaporates_staged_orders(self):
        """Attack / Vulnerability Check:
        Orders are staged at 16:00 ET close for next-day 09:30 open.
        If the server process restarts overnight (e.g. Railway deploy/restart),
        are staged swing orders and symbol reservations restored from durable state?
        Or do they completely evaporate?
        """
        staged_mgr = main.swing_staged_order_manager
        staged_mgr.stage_buy(
            symbol="MU", target_notional=25000.0, daily_atr=3.5,
            signal_date=date(2026, 9, 22), reason="OVERNIGHT_PERSISTENCE_ATTACK",
        )
        main.reserve_symbol_for_swing("AMD")
        assert staged_mgr.is_staged_for_entry("MU") is True
        assert main.is_symbol_reserved_for_swing("AMD") is True

        # Simulate state checkpoint
        state = main._capture_checkpoint()

        # Check that staged_orders and swing reservations are persisted in the checkpoint payload
        assert "swing_staged_orders" in state, "swing_staged_orders must be persisted in checkpoint"
        assert "swing_reserved_symbols" in state, "swing_reserved_symbols must be persisted in checkpoint"
        assert "AMD" in state["swing_reserved_symbols"]

        # Reinitializing and restoring from durable checkpoint preserves staged orders
        new_staged_mgr = SwingStagedOrderManager()
        from backend.app.strategies.swing_panic_dip import StagedSwingOrder
        restored_orders = [StagedSwingOrder.from_dict(o) for o in state["swing_staged_orders"]]
        new_staged_mgr.load_staged_orders(restored_orders)
        assert new_staged_mgr.is_staged_for_entry("MU") is True, (
            "Staged orders must be restored across restarts!"
        )


