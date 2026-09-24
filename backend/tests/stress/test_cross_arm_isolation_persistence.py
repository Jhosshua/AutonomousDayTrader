"""backend/tests/stress/test_cross_arm_isolation_persistence.py
Adversarial Stress Test Suite for Challenger 2 (Adversarial Cross-Arm Isolation & Persistence Challenger).

Probes:
1. Cross-Arm Circuit Breaker Isolation:
   - Tripping hard daily loss ($1,500 intraday drawdown) via main._trip_circuit_breaker while holding active swing positions.
   - Verifies swing positions and protective stops remain 100% intact, while intraday positions and orders are liquidated/cancelled.
   - Verifies swing emergency ATR stops remain operational after circuit breaker halt.
2. Mutual Exclusion Locking (AMD):
   - Probes AMD reservation and position holding by Swing.
   - Tests intraday BUY and SELL orders against pre_trade_risk_validator.
   - Tests clean reservation release upon swing position closure.
   - Tests reverse collision: intraday holding AMD blocking swing orders.
3. DailyBarStore Persistence Across Restart:
   - Aggregates daily bars across multi-symbol universe (finalized and in-flight).
   - Captures runtime state into SQLite TradingStateStore.
   - Wipes in-memory DailyBarStore and reconstructs across checkpoint load.
   - Verifies exact bar fidelity and post-restart indicator equivalence (SMA, ATR, RSI2, RS).
"""
import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List
import pytest

from backend.app.config import settings
from backend.app.core.account import AccountStatus, PaperTradingAccount, Position, PositionSide, TradingArm
from backend.app.core.bracket import DynamicBracketManager
from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType
from backend.app.core.persistence import TradingStateStore
from backend.app.core.risk import BreakerStatus, InstitutionalRiskEngine, RiskEngineConfig
from backend.app.core.runtime_state import capture_runtime_state, restore_runtime_state
from backend.app.models.events import BarEvent
from backend.app.strategies.swing_indicators import (
    DailyBar,
    DailyBarAggregator,
    DailyBarStore,
    calculate_daily_atr,
    calculate_relative_strength_60d,
    calculate_rsi2,
    calculate_sma,
)
from backend.app.strategies.swing_panic_dip import (
    SwingStagedOrderManager,
    SwingStrategyEngine,
)
from backend.app import main


@pytest.fixture(autouse=True)
def clean_test_environment():
    """Guarantee isolated test environment and clean state across every test."""
    main.account.cash = 50000.0
    main.account.equity = 50000.0
    main.account.realized_pnl = 0.0
    main.account.daily_drawdown = 0.0
    main.account.positions.clear()
    main.engine.working_orders.clear()
    main.engine.orders.clear()
    main.bracket_manager.brackets.clear()
    main.bracket_manager.symbol_to_bracket.clear()
    main.swing_reserved_symbols.clear()
    main.account.status = AccountStatus.ACTIVE
    main.risk_engine.status = BreakerStatus.ARMED
    yield
    main.account.cash = 50000.0
    main.account.equity = 50000.0
    main.account.realized_pnl = 0.0
    main.account.daily_drawdown = 0.0
    main.account.positions.clear()
    main.engine.working_orders.clear()
    main.engine.orders.clear()
    main.bracket_manager.brackets.clear()
    main.bracket_manager.symbol_to_bracket.clear()
    main.swing_reserved_symbols.clear()
    main.account.status = AccountStatus.ACTIVE
    main.risk_engine.status = BreakerStatus.ARMED


# ============================================================================
# 1. CROSS-ARM CIRCUIT BREAKER ISOLATION
# ============================================================================

class TestCrossArmCircuitBreakerIsolation:
    """Adversarial stress testing of cross-arm isolation during daily circuit breaker halt."""

    def test_circuit_breaker_preserves_swing_positions_and_liquidates_intraday(self):
        """Simulate holding 1 active swing position (LRCX) + 1 swing working order (KLAC),
        and 2 intraday positions (AAPL Long, TSLA Short) + 1 intraday working order (NVDA).
        Trigger hard daily loss ($1,500 drawdown) via main._trip_circuit_breaker.
        Verify:
        - Intraday positions are fully flattened.
        - Intraday working orders are cancelled.
        - Swing positions remain 100% intact with exact shares, entry ATR, and stop loss prices.
        - Swing working orders remain intact.
        """
        acct = main.account
        eng = main.engine
        now_dt = datetime.now(timezone.utc)

        # 1. Seed Swing Position (LRCX)
        acct.apply_fill("sw_fill_1", "LRCX", "BUY", 30, 800.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.positions["LRCX"].stop_loss_price = 762.50
        acct.positions["LRCX"].entry_atr = 15.00
        acct.positions["LRCX"].entry_date = now_dt.date()

        # 2. Seed Intraday Positions (AAPL Long, TSLA Short)
        acct.apply_fill("in_fill_1", "AAPL", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")
        acct.apply_fill("in_fill_2", "TSLA", "SELL", 50, 220.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="vwap_pullback")

        # 3. Seed Working Orders (1 Intraday working order on NVDA, 1 Swing working order on KLAC)
        ord_in = eng.create_order("NVDA", OrderSide.BUY, OrderType.LIMIT, 50, limit_price=120.0, arm=TradingArm.INTRADAY, strategy_id="orb")
        eng.submit_order(ord_in.id)

        ord_sw = eng.create_order("KLAC", OrderSide.BUY, OrderType.LIMIT, 35, limit_price=700.0, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        eng.submit_order(ord_sw.id)

        assert len(acct.positions) == 3
        assert len(eng.working_orders) == 2

        # 4. Trip Circuit Breaker
        main._trip_circuit_breaker(now_dt)

        # Invariant 1: Account status is CIRCUIT_HALTED
        assert acct.status == AccountStatus.CIRCUIT_HALTED

        # Invariant 2: Intraday positions MUST be flattened (0 shares or removed)
        assert "AAPL" not in acct.positions or acct.positions["AAPL"].shares == 0
        assert "TSLA" not in acct.positions or acct.positions["TSLA"].shares == 0

        # Invariant 3: Intraday working orders MUST be cancelled
        assert ord_in.id not in eng.working_orders

        # Invariant 4: Swing positions MUST remain 100% intact
        assert "LRCX" in acct.positions, "LRCX swing position was erroneously removed!"
        pos_lrcx = acct.positions["LRCX"]
        assert pos_lrcx.arm == TradingArm.SWING
        assert pos_lrcx.shares == 30
        assert pos_lrcx.avg_entry_price == 800.0
        assert pos_lrcx.stop_loss_price == 762.50
        assert pos_lrcx.entry_atr == 15.00

        # Invariant 5: Swing working orders MUST remain intact
        assert ord_sw.id in eng.working_orders, "Swing working order was erroneously cancelled!"

    def test_swing_emergency_stop_operational_during_circuit_breaker_halt(self):
        """Verify that when account is in CIRCUIT_HALTED, swing emergency ATR stop loss
        remains fully functional and successfully liquidates the position when breached.
        """
        acct = main.account
        acct.status = AccountStatus.CIRCUIT_HALTED

        now_dt = datetime.now(timezone.utc)
        acct.apply_fill("sw_fill_1", "LRCX", "BUY", 30, 800.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        acct.positions["LRCX"].stop_loss_price = 762.50
        acct.positions["LRCX"].entry_atr = 15.00

        # Price crashes below stop: $760.00 <= $762.50
        exit_events = main.swing_strategy_engine.check_intraday_emergency_stops({"LRCX": 760.0}, now_dt)

        assert len(exit_events) == 1
        assert exit_events[0]["symbol"] == "LRCX"
        assert exit_events[0]["shares"] == 30
        assert exit_events[0]["stop_price"] == 762.50
        assert exit_events[0]["fill_price"] <= 760.0  # Slippage accounted for
        assert "LRCX" not in acct.positions or acct.positions["LRCX"].shares == 0

    def test_new_intraday_entries_blocked_during_circuit_breaker_halt(self):
        """Verify that when account is in CIRCUIT_HALTED, new intraday entry orders are strictly rejected.
        Tests both pre_trade_risk_validator and engine.submit_order under circuit halt.
        """
        acct = main.account
        eng = main.engine
        now_dt = datetime.now(timezone.utc)

        # Trigger realistic circuit breaker via $1,500 drawdown
        acct.cash = 48400.0
        acct.equity = 48400.0
        acct.realized_pnl = -1600.0
        status = main.risk_engine.evaluate_account_state(
            equity=acct.equity, cash=acct.cash, realized_pnl=acct.realized_pnl,
            unrealized_pnl=0.0, timestamp=now_dt,
        )
        assert status == BreakerStatus.HALTED_DAILY_LOSS
        main._trip_circuit_breaker(now_dt)
        assert acct.status == AccountStatus.CIRCUIT_HALTED

        # Test pre_trade_risk_validator rejection
        order_buy = eng.create_order("MSFT", OrderSide.BUY, OrderType.MARKET, 20, arm=TradingArm.INTRADAY, strategy_id="orb")
        approved, reason = main.pre_trade_risk_validator(order_buy, acct)
        assert approved is False
        assert "CIRCUIT_BREAKER_HALTED" in reason

        # Test engine.submit_order rejection
        submitted = eng.submit_order(order_buy.id)
        assert submitted.status.value == "REJECTED"
        assert "CIRCUIT_BREAKER_HALTED" in (submitted.reject_reason or "") or "Account is not ACTIVE" in (submitted.reject_reason or "")


# ============================================================================
# 2. MUTUAL EXCLUSION LOCKING FOR AMD
# ============================================================================

class TestAmdMutualExclusionLocking:
    """Adversarial stress testing of AMD symbol reservation and cross-arm mutual exclusion."""

    def test_amd_reserved_for_swing_rejects_intraday_buy(self):
        """When AMD is reserved in swing_reserved_symbols, intraday BUY MUST be rejected."""
        main.reserve_symbol_for_swing("AMD")
        assert "AMD" in main.swing_reserved_symbols

        ord_buy = main.engine.create_order("AMD", OrderSide.BUY, OrderType.MARKET, 50, arm=TradingArm.INTRADAY, strategy_id="orb")
        approved, reason = main.pre_trade_risk_validator(ord_buy, main.account)

        assert approved is False
        assert "SYMBOL_RESERVED_FOR_SWING" in reason

    def test_amd_reserved_for_swing_rejects_intraday_sell_short(self):
        """When AMD is reserved in swing_reserved_symbols, intraday SELL (short entry) MUST be rejected."""
        main.reserve_symbol_for_swing("AMD")
        assert "AMD" in main.swing_reserved_symbols

        ord_sell = main.engine.create_order("AMD", OrderSide.SELL, OrderType.MARKET, 50, arm=TradingArm.INTRADAY, strategy_id="orb")
        approved, reason = main.pre_trade_risk_validator(ord_sell, main.account)

        assert approved is False
        assert "SYMBOL_RESERVED_FOR_SWING" in reason

    def test_amd_held_by_swing_rejects_intraday_buy(self):
        """When AMD is actively held by Swing arm, intraday BUY MUST be rejected."""
        now_dt = datetime.now(timezone.utc)
        main.account.apply_fill("sw_amd", "AMD", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        assert "AMD" in main.account.positions
        assert main.account.positions["AMD"].arm == TradingArm.SWING

        ord_buy = main.engine.create_order("AMD", OrderSide.BUY, OrderType.MARKET, 50, arm=TradingArm.INTRADAY, strategy_id="orb")
        approved, reason = main.pre_trade_risk_validator(ord_buy, main.account)

        assert approved is False
        assert "SYMBOL_RESERVED_FOR_SWING" in reason

    def test_amd_held_by_swing_probe_intraday_sell_vulnerability(self):
        """ADVERSARIAL PROBE: When AMD is actively held LONG by Swing arm,
        does an intraday SELL order (attempting an intraday short entry) get rejected,
        OR does pre_trade_risk_validator mistakenly treat it as an exit of Swing's position?
        
        Expected Institutional Invariant: REJECT with SYMBOL_RESERVED_FOR_SWING.
        Empirical Reality: Traces whether lines 251-255 in main.py classify it as is_exit=True.
        """
        now_dt = datetime.now(timezone.utc)
        main.account.apply_fill("sw_amd", "AMD", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")

        ord_sell = main.engine.create_order("AMD", OrderSide.SELL, OrderType.MARKET, 50, arm=TradingArm.INTRADAY, strategy_id="orb")
        approved, reason = main.pre_trade_risk_validator(ord_sell, main.account)

        # Invariant assertion: An intraday SELL order MUST be rejected when AMD is held by Swing
        assert approved is False, (
            f"VULNERABILITY DETECTED in pre_trade_risk_validator: Intraday SELL order on Swing-held AMD was APPROVED! "
            f"Reason: {reason}. Intraday arm can liquidate or cannibalize Swing's long position!"
        )
        assert "SYMBOL_RESERVED_FOR_SWING" in reason

    def test_amd_clean_release_after_swing_closure(self):
        """When AMD swing position is closed and reservation released, intraday trading is unblocked."""
        main.reserve_symbol_for_swing("AMD")
        assert main.is_symbol_reserved_for_swing("AMD") is True

        # Release reservation upon position closure/cancel
        main.release_symbol_for_swing("AMD")
        assert main.is_symbol_reserved_for_swing("AMD") is False

        ord_buy = main.engine.create_order("AMD", OrderSide.BUY, OrderType.MARKET, 50, arm=TradingArm.INTRADAY, strategy_id="orb")
        approved, reason = main.pre_trade_risk_validator(ord_buy, main.account)

        assert approved is True
        assert "APPROVED" in reason.upper()

    def test_reverse_intraday_held_amd_blocks_swing_entry(self):
        """When AMD is held by Intraday arm, Swing entry MUST be rejected."""
        now_dt = datetime.now(timezone.utc)
        main.account.apply_fill("in_amd", "AMD", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")

        ord_sw = main.engine.create_order("AMD", OrderSide.BUY, OrderType.MARKET, 50, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        approved, reason = main.pre_trade_risk_validator(ord_sw, main.account)

        assert approved is False
        assert "SWING_REJECTED" in reason

    def test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability(self):
        """ADVERSARIAL PROBE: When AMD is actively held LONG by Intraday arm,
        does a Swing SELL order get rejected with SWING_REJECTED,
        or mistakenly approved as APPROVED_EXIT?
        """
        now_dt = datetime.now(timezone.utc)
        main.account.apply_fill("in_amd", "AMD", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")

        ord_sw_sell = main.engine.create_order("AMD", OrderSide.SELL, OrderType.MARKET, 50, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
        approved, reason = main.pre_trade_risk_validator(ord_sw_sell, main.account)

        assert approved is False, (
            f"VULNERABILITY DETECTED in pre_trade_risk_validator: Swing SELL order on Intraday-held AMD was APPROVED! "
            f"Reason: {reason}. Swing arm can cannibalize Intraday position!"
        )


# ============================================================================
# 3. DAILYBARSTORE PERSISTENCE ACROSS RESTART
# ============================================================================

class TestDailyBarStorePersistenceRestart:
    """Adversarial stress testing of DailyBarStore checkpoint persistence across SQLite storage and restarts."""

    def test_daily_bar_store_roundtrip_across_sqlite_checkpoints(self):
        """Aggregate daily bars across 6 symbols (LRCX, KLAC, MU, AMD, GS, QQQ),
        including finalized bars and in-flight bars.
        Save checkpoint into SQLite TradingStateStore.
        Wipe in-memory stores completely.
        Restore from SQLite into fresh DailyBarStore.
        Verify 100% bar fidelity across all symbols.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_trading_state.db")
            store = TradingStateStore(db_path)

            bar_store = DailyBarStore()
            aggregator = DailyBarAggregator(store=bar_store)

            d1 = date(2026, 9, 22)
            d2 = date(2026, 9, 23)

            symbols = ["QQQ", "LRCX", "KLAC", "MU", "AMD", "GS"]
            # Populate finalized d1 bars for all symbols
            for sym in symbols:
                p = 450.0 if sym == "QQQ" else 100.0
                bar_store.append_bar(
                    DailyBar(
                        symbol=sym,
                        date=d1,
                        open=p,
                        high=p + 5.0,
                        low=p - 3.0,
                        close=p + 2.0,
                        volume=2500000,
                        finalized=True,
                    )
                )

            # Ingest 1-minute bars for today (d2) and finalize 3 of them
            for sym in ["QQQ", "LRCX", "AMD"]:
                p = 455.0 if sym == "QQQ" else 105.0
                b = BarEvent(
                    symbol=sym,
                    open=p,
                    high=p + 2.0,
                    low=p - 1.0,
                    close=p + 1.0,
                    volume=50000,
                    timestamp=datetime(2026, 9, 23, 13, 31, tzinfo=timezone.utc),
                )
                aggregator.on_minute_bar(b)
                fin_bar = aggregator.finalize_daily_bar(sym, d2)
                assert fin_bar is not None
                assert fin_bar.finalized is True

            # Leave in-flight bars in store for MU and GS
            bar_store.append_bar(DailyBar("MU", d2, 102.0, 104.0, 101.0, 103.5, 400000, False))
            bar_store.append_bar(DailyBar("GS", d2, 450.0, 455.0, 448.0, 452.0, 200000, False))

            bars_before = bar_store.get_all_bars()
            assert len(bars_before) == 6
            assert len(bars_before["QQQ"]) == 2
            assert len(bars_before["LRCX"]) == 2
            assert len(bars_before["AMD"]) == 2
            assert len(bars_before["MU"]) == 2
            assert len(bars_before["GS"]) == 2
            assert len(bars_before["KLAC"]) == 1

            # Capture runtime state
            checkpoint_payload = capture_runtime_state(
                account=main.account,
                engine=main.engine,
                bracket_manager=main.bracket_manager,
                risk_engine=main.risk_engine,
                flattening_engine=main.flattening_engine,
                adaptation_engine=main.adaptation_engine,
                strategies=main.strategies,
                entry_order_to_bracket=main.entry_order_to_bracket,
                bracket_realized_pnl=main.bracket_realized_pnl,
                completed_brackets_recorded=main.completed_brackets_recorded,
                latest_market_prices=main.latest_market_prices,
                market_history=main.market_history,
                recent_news=main.recent_news,
                last_session_date=d2,
                last_vix_print=None,
                ledger_revision=1,
                daily_bar_store=bar_store,
            )

            # Save to SQLite
            saved_rev, _ = store.save_checkpoint(checkpoint_payload, reason="TEST_RESTART_RECOVERY")
            store.close()

            # SIMULATE HARD RESTART: Destroy in-memory instances
            del bar_store, aggregator, store

            # RESTART: Open SQLite and load checkpoint
            store2 = TradingStateStore(db_path)
            loaded_payload, loaded_rev, _ = store2.load_checkpoint()
            assert loaded_rev == saved_rev
            assert "daily_bars" in loaded_payload

            # Restore into brand new DailyBarStore
            restored_bar_store = DailyBarStore()
            assert len(restored_bar_store.get_all_bars()) == 0

            restore_runtime_state(
                loaded_payload,
                account=main.account,
                engine=main.engine,
                bracket_manager=main.bracket_manager,
                risk_engine=main.risk_engine,
                flattening_engine=main.flattening_engine,
                adaptation_engine=main.adaptation_engine,
                strategies=main.strategies,
                entry_order_to_bracket=main.entry_order_to_bracket,
                bracket_realized_pnl=main.bracket_realized_pnl,
                completed_brackets_recorded=main.completed_brackets_recorded,
                latest_market_prices=main.latest_market_prices,
                market_history=main.market_history,
                recent_news=main.recent_news,
                daily_bar_store=restored_bar_store,
            )

            bars_after = restored_bar_store.get_all_bars()
            assert len(bars_after) == 6

            for sym, original_list in bars_before.items():
                assert sym in bars_after, f"Missing symbol {sym} after restore!"
                restored_list = bars_after[sym]
                assert len(restored_list) == len(original_list), f"Bar count mismatch for {sym}: {len(restored_list)} != {len(original_list)}"
                for orig_bar, rest_bar in zip(original_list, restored_list):
                    assert orig_bar.symbol == rest_bar.symbol
                    assert orig_bar.date == rest_bar.date
                    assert orig_bar.open == pytest.approx(rest_bar.open, abs=1e-4)
                    assert orig_bar.high == pytest.approx(rest_bar.high, abs=1e-4)
                    assert orig_bar.low == pytest.approx(rest_bar.low, abs=1e-4)
                    assert orig_bar.close == pytest.approx(rest_bar.close, abs=1e-4)
                    assert orig_bar.volume == rest_bar.volume
                    assert orig_bar.finalized == rest_bar.finalized

            store2.close()

    def test_indicator_calculations_identical_post_restart(self):
        """Verify that quantitative indicators (200 SMA, 5 SMA, 14 ATR, RSI-2, 60d RS)
        calculated from DailyBarStore produce identical values before and after SQLite restart.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_indicators_persistence.db")
            store = TradingStateStore(db_path)

            base_store = DailyBarStore(seed_path=settings.DAILY_BARS_SEED_PATH)
            # Add an extra aggregated bar for LRCX
            extra_date = date(2026, 9, 23)
            base_store.append_bar(
                DailyBar("LRCX", extra_date, 820.0, 835.0, 815.0, 830.0, 1500000, True)
            )

            lrcx_bars_pre = base_store.get_bars("LRCX")
            qqq_bars_pre = base_store.get_bars("QQQ")
            lrcx_closes_pre = [b.close for b in lrcx_bars_pre]
            qqq_closes_pre = [b.close for b in qqq_bars_pre]

            sma_200_pre = calculate_sma(lrcx_closes_pre, 200)
            sma_5_pre = calculate_sma(lrcx_closes_pre, 5)
            atr_14_pre = calculate_daily_atr(lrcx_bars_pre, 14)
            rsi_2_pre = calculate_rsi2(lrcx_closes_pre)
            rs_60_pre = calculate_relative_strength_60d(lrcx_bars_pre, qqq_bars_pre)

            # Capture and save
            payload = capture_runtime_state(
                account=main.account,
                engine=main.engine,
                bracket_manager=main.bracket_manager,
                risk_engine=main.risk_engine,
                flattening_engine=main.flattening_engine,
                adaptation_engine=main.adaptation_engine,
                strategies=main.strategies,
                entry_order_to_bracket=main.entry_order_to_bracket,
                bracket_realized_pnl=main.bracket_realized_pnl,
                completed_brackets_recorded=main.completed_brackets_recorded,
                latest_market_prices=main.latest_market_prices,
                market_history=main.market_history,
                recent_news=main.recent_news,
                last_session_date=extra_date,
                last_vix_print=None,
                ledger_revision=5,
                daily_bar_store=base_store,
            )
            store.save_checkpoint(payload, reason="TEST_INDICATOR_CONSISTENCY")
            store.close()

            # Restart
            store2 = TradingStateStore(db_path)
            loaded_payload, _, _ = store2.load_checkpoint()
            new_store = DailyBarStore()
            restore_runtime_state(
                loaded_payload,
                account=main.account,
                engine=main.engine,
                bracket_manager=main.bracket_manager,
                risk_engine=main.risk_engine,
                flattening_engine=main.flattening_engine,
                adaptation_engine=main.adaptation_engine,
                strategies=main.strategies,
                entry_order_to_bracket=main.entry_order_to_bracket,
                bracket_realized_pnl=main.bracket_realized_pnl,
                completed_brackets_recorded=main.completed_brackets_recorded,
                latest_market_prices=main.latest_market_prices,
                market_history=main.market_history,
                recent_news=main.recent_news,
                daily_bar_store=new_store,
            )

            lrcx_bars_post = new_store.get_bars("LRCX")
            qqq_bars_post = new_store.get_bars("QQQ")
            lrcx_closes_post = [b.close for b in lrcx_bars_post]
            qqq_closes_post = [b.close for b in qqq_bars_post]

            sma_200_post = calculate_sma(lrcx_closes_post, 200)
            sma_5_post = calculate_sma(lrcx_closes_post, 5)
            atr_14_post = calculate_daily_atr(lrcx_bars_post, 14)
            rsi_2_post = calculate_rsi2(lrcx_closes_post)
            rs_60_post = calculate_relative_strength_60d(lrcx_bars_post, qqq_bars_post)

            assert sma_200_pre == pytest.approx(sma_200_post, abs=1e-6)
            assert sma_5_pre == pytest.approx(sma_5_post, abs=1e-6)
            assert atr_14_pre == pytest.approx(atr_14_post, abs=1e-6)
            assert rsi_2_pre == pytest.approx(rsi_2_post, abs=1e-6)
            assert rs_60_pre == pytest.approx(rs_60_post, abs=1e-6)

            store2.close()
