"""backend/tests/unit/test_swing_forensic_remediation.py
Unit & Regression Test Suite covering all 10 Forensic Remediation Fixes:
- Defect 1: 09:30 ET Open Window Tolerance & 09:45 Expiration Sweep
- Defect 2: Concurrency Annihilation Race Condition & Deferred Entries
- Defect 3: Staged Order Idempotency Breakdown & Position Cap Enforcement
- Defect 4: Eliminate Blocking I/O in Async Earnings Refresh
- Defect 5: Cross-Arm Circuit Breaker Contamination (Preserves Swing Holdings)
- Defect 6: Microstructure Slippage & Rule 6 Fill-Anchored Stop Loss
- Defect 7: PositionState Schema Fidelity (entry_atr and entry_date)
- Defect 8: Durable Disk Persistence & Configuration for Earnings Calendar
- Defect 9: DailyBarStore Checkpoint Serialization & Restoration Across Restarts
- Defect 10: Multi-Arm Concurrent Capital Preservation & Isolation Invariants
"""
import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

from backend.app.config import Settings
from backend.app.core.account import PaperTradingAccount, Position, PositionSide, TradingArm
from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType
from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig
from backend.app.core.runtime_state import capture_runtime_state, restore_runtime_state
from backend.app.models.events import BarEvent, PositionState
from backend.app.strategies.earnings_calendar import EarningsCalendar, EarningsEvent
from backend.app.strategies.swing_indicators import DailyBar, DailyBarStore
from backend.app.strategies.swing_panic_dip import (
    StagedSwingOrder,
    SwingStagedOrderManager,
    SwingStrategyEngine,
)
from backend.app import main


# =====================================================================
# DEFECT 1: Open Window Tolerance (09:30-09:45) & Stale Order Expiration
# =====================================================================
def test_defect_1_open_window_expiration_sweep():
    """Verify that unexecuted staged orders past 09:45:00 ET are cleanly cancelled."""
    staged_mgr = main.swing_staged_order_manager
    staged_mgr.clear()
    main.swing_reserved_symbols.clear()

    eval_date = date(2026, 9, 23)
    order = staged_mgr.stage_buy(
        symbol="MU",
        target_notional=25000.0,
        daily_atr=3.0,
        signal_date=eval_date,
        reason="TEST_DEFECT_1",
    )
    # Set created_at to 09:30 ET so it exceeds the 60s grace threshold
    order.created_at = datetime(2026, 9, 23, 9, 30, 0, tzinfo=main.ET_TZ)
    main.swing_reserved_symbols.add("MU")
    assert staged_mgr.is_staged_for_entry("MU") is True
    assert "MU" in main.swing_reserved_symbols

    # Stale expiration at 09:46 ET
    late_time = datetime(2026, 9, 23, 9, 46, 0, tzinfo=main.ET_TZ)
    main._expire_stale_staged_swing_orders(late_time)

    assert staged_mgr.is_staged_for_entry("MU") is False
    assert "MU" not in main.swing_reserved_symbols
    assert len(staged_mgr.get_staged_entries()) == 0


# =====================================================================
# DEFECT 2: Concurrency Annihilation Race Condition & Deferred Entries
# =====================================================================
def test_defect_2_entry_deferral_when_exits_pending():
    """Verify that when 2 active swing positions exist and 1 exit is pending,
    a staged entry order is deferred (not deleted) and executes once the exit frees a slot.
    """
    acct = PaperTradingAccount(initial_cash=50000.0)
    exec_engine = ExecutionEngine(account=acct)
    staged_mgr = SwingStagedOrderManager()
    engine = SwingStrategyEngine(
        account=acct,
        execution_engine=exec_engine,
        staged_manager=staged_mgr,
        max_concurrent_positions=2,
    )

    now_dt = datetime(2026, 9, 23, 9, 30, 0, tzinfo=timezone.utc)
    # 1. Populate 2 existing swing positions (at concurrency cap)
    acct.apply_fill("pos1", "MU", "BUY", 250, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
    acct.apply_fill("pos2", "LRCX", "BUY", 50, 500.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
    assert len(engine.get_active_swing_positions()) == 2

    # 2. Stage exit for MU and stage entry for KLAC
    staged_mgr.stage_sell(symbol="MU", shares=250, signal_date=date(2026, 9, 22), reason="TIME_STOP")
    staged_mgr.stage_buy(symbol="KLAC", target_notional=25000.0, daily_atr=15.0, signal_date=date(2026, 9, 22), reason="PANIC_DIP")

    # 3. Simulate arrival of KLAC bar first (MU open price not yet available)
    res1 = engine.execute_market_open(open_prices={"KLAC": 700.0}, open_time=now_dt)
    # Entry for KLAC must NOT be permanently dropped; it should be deferred!
    assert len(res1["entries"]) == 0
    assert staged_mgr.is_staged_for_entry("KLAC") is True

    # 4. Now MU bar arrives and exit executes
    res2 = engine.execute_market_open(open_prices={"MU": 105.0, "KLAC": 700.0}, open_time=now_dt)
    assert len(res2["exits"]) == 1
    assert res2["exits"][0]["symbol"] == "MU"
    # MU position closed, freeing slot for KLAC
    assert "MU" not in acct.positions or acct.positions["MU"].shares == 0
    assert len(res2["entries"]) == 1
    assert res2["entries"][0]["symbol"] == "KLAC"
    assert "KLAC" in acct.positions
    assert staged_mgr.is_staged_for_entry("KLAC") is False


# =====================================================================
# DEFECT 3: Staged Order Idempotency Breakdown & Position Cap Enforcement
# =====================================================================
def test_defect_3_staged_order_idempotency_and_cap():
    """Verify that multiple evaluate_market_close scans cannot stage more than 2 positions."""
    acct = PaperTradingAccount(initial_cash=50000.0)
    exec_engine = ExecutionEngine(account=acct)
    staged_mgr = SwingStagedOrderManager()
    bar_store = DailyBarStore()

    base_date = date(2026, 9, 23)
    # Populate store so multiple stocks qualify
    for sym in ["QQQ", "MU", "LRCX", "KLAC", "AMD", "GS"]:
        p = 450.0 if sym == "QQQ" else 100.0
        for i in range(215):
            d = base_date - timedelta(days=214 - i)
            c = p + 0.1
            bar_store.append_bar(DailyBar(
                symbol=sym, date=d, open=p, high=c + 2.0, low=c - 2.0,
                close=c, volume=1000000, finalized=True,
            ))
            p = c

    engine = SwingStrategyEngine(
        account=acct,
        execution_engine=exec_engine,
        bar_store=bar_store,
        staged_manager=staged_mgr,
        max_concurrent_positions=2,
    )

    # First close evaluation
    close_time = datetime(2026, 9, 23, 16, 0, 0, tzinfo=timezone.utc)
    engine.evaluate_market_close(close_time)
    staged_count_1 = len(staged_mgr.get_staged_entries())
    assert staged_count_1 <= 2

    # Second and third close evaluations (e.g. repeated scans or clock ticks)
    engine.evaluate_market_close(close_time)
    engine.evaluate_market_close(close_time)
    staged_count_2 = len(staged_mgr.get_staged_entries())

    assert staged_count_2 == staged_count_1, "Idempotency broken: duplicate entries staged!"
    assert staged_count_2 <= 2, f"Concurrency breach: {staged_count_2} staged entries > max 2"


# =====================================================================
# DEFECT 4: Non-Blocking Async Earnings Refresh
# =====================================================================
@pytest.mark.asyncio
async def test_defect_4_non_blocking_async_earnings_refresh():
    """Verify refresh_from_remote uses httpx.AsyncClient with timeout and clean exception handling."""
    cal = EarningsCalendar(remote_url="http://fake-nonexistent-endpoint.local/earnings")

    # Mock httpx.AsyncClient.get to simulate successful non-blocking JSON response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "MU": [
            {
                "report_date": "2026-10-15",
                "report_time": "amc",
                "confirmed": True,
            }
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        success = await cal.refresh_from_remote()
        assert success is True
        events = cal.get_events("MU")
        assert len(events) >= 1
        assert any(e.report_date == date(2026, 10, 15) for e in events)

    # Test clean failure fallback on network timeout
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectTimeout("Timeout")):
        success_fail = await cal.refresh_from_remote()
        assert success_fail is False, "Should return False gracefully on timeout without throwing"


# =====================================================================
# DEFECT 5: Cross-Arm Circuit Breaker Contamination (Preserves Swing)
# =====================================================================
def test_defect_5_circuit_breaker_preserves_swing_positions():
    """Verify that trip_circuit_breaker liquidates only intraday positions, leaving swing positions intact."""
    acct = main.account
    acct.positions.clear()
    main.engine.working_orders.clear()

    now_dt = datetime.now(timezone.utc)
    # Create 1 Swing position and 1 Intraday position
    acct.apply_fill("sw1", "MU", "BUY", 250, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip")
    acct.apply_fill("in1", "AAPL", "BUY", 100, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")

    assert "MU" in acct.positions
    assert "AAPL" in acct.positions

    # Create 1 swing working order and 1 intraday working order
    ord_sw = main.engine.create_order("MU", OrderSide.SELL, OrderType.LIMIT, 250, limit_price=110.0, arm=TradingArm.SWING)
    ord_in = main.engine.create_order("AAPL", OrderSide.SELL, OrderType.LIMIT, 100, limit_price=160.0, arm=TradingArm.INTRADAY)
    main.engine.submit_order(ord_sw.id)
    main.engine.submit_order(ord_in.id)

    # Trigger circuit breaker
    main._trip_circuit_breaker(now_dt)

    # Invariant: Swing position MUST be preserved!
    assert "MU" in acct.positions, "Defect 5: Swing position was erroneously liquidated by circuit breaker!"
    assert acct.positions["MU"].arm == TradingArm.SWING
    assert acct.positions["MU"].shares == 250

    # Invariant: Intraday position MUST be flattened
    assert "AAPL" not in acct.positions or acct.positions["AAPL"].shares == 0


# =====================================================================
# DEFECT 6: Microstructure Slippage & Rule 6 Fill-Anchored Stop Loss
# =====================================================================
def test_defect_6_slippage_and_fill_anchored_stop():
    """Verify realistic slippage calculation and stop-loss anchoring to fill.price."""
    acct = PaperTradingAccount(initial_cash=50000.0)
    exec_engine = ExecutionEngine(account=acct)
    engine = SwingStrategyEngine(account=acct, execution_engine=exec_engine)

    eval_date = date(2026, 9, 22)
    open_time = datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc)

    engine.staged_manager.stage_buy(
        symbol="LRCX",
        target_notional=25000.0,
        daily_atr=5.00,
        signal_date=eval_date,
        reason="PANIC_DIP",
    )

    open_prices = {"LRCX": 800.0}
    # Execute with realistic slippage enabled
    res = engine.execute_market_open(open_prices, open_time, apply_slippage=True)
    assert len(res["entries"]) == 1
    entry = res["entries"][0]

    # Verify realistic slippage was applied (fill_price > open_price for BUY)
    assert entry["slippage"] > 0.0
    assert entry["fill_price"] > 800.0
    pos = acct.positions["LRCX"]

    # Rule 6 Invariant: Stop Loss MUST anchor to realized fill price (avg_entry_price - 2.5 * ATR)
    expected_stop = round(pos.avg_entry_price - 2.5 * 5.0, 2)
    assert pos.stop_loss_price == expected_stop
    assert pos.stop_loss_price != 787.50, "Defect 6: Stop loss was anchored to unadjusted open price instead of fill price!"


# =====================================================================
# DEFECT 7: PositionState Schema Fidelity
# =====================================================================
def test_defect_7_position_state_schema_fidelity():
    """Verify entry_atr and entry_date are exposed on PositionState and mapped from Position.to_state()."""
    today_d = date(2026, 9, 23)
    pos = Position(
        symbol="MU",
        side=PositionSide.LONG,
        shares=250,
        avg_entry_price=100.0,
        market_price=102.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
        entry_atr=3.5,
        entry_date=today_d,
        stop_loss_price=91.25,
    )

    state = pos.to_state()
    assert isinstance(state, PositionState)
    assert state.entry_atr == 3.5
    assert state.entry_date == "2026-09-23"


# =====================================================================
# DEFECT 8: Durable Disk Persistence & Configuration for Earnings Calendar
# =====================================================================
def test_defect_8_earnings_calendar_durable_cache():
    """Verify EARNINGS_CALENDAR_REMOTE_URL config and atomic cache writing to disk."""
    settings = Settings()
    assert hasattr(settings, "EARNINGS_CALENDAR_REMOTE_URL")
    assert hasattr(settings, "EARNINGS_CALENDAR_CACHE_PATH")

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = os.path.join(tmpdir, "test_earnings_cache.json")
        cal = EarningsCalendar(cache_path=cache_file)
        cal._events = {
            "MU": [EarningsEvent(symbol="MU", report_date=date(2026, 11, 1), report_time="bmo", confirmed=True)]
        }

        cal.save_cache_file()
        assert os.path.exists(cache_file)

        # Create new calendar reading from that cache file
        cal2 = EarningsCalendar(cache_path=cache_file, seed_path="nonexistent.json")
        events = cal2.get_events("MU")
        assert len(events) == 1
        assert events[0].report_date == date(2026, 11, 1)


# =====================================================================
# DEFECT 9: DailyBarStore Checkpoint Serialization & Restoration
# =====================================================================
def test_defect_9_daily_bar_store_checkpoint_persistence():
    """Verify DailyBarStore bars are persisted in runtime state checkpoints and restored cleanly."""
    store = DailyBarStore()
    d1 = date(2026, 9, 22)
    b1 = DailyBar(
        symbol="MU", date=d1, open=100.0, high=105.0, low=99.0,
        close=103.0, volume=5000000, finalized=True,
    )
    store.append_bar(b1)

    all_bars = store.get_all_bars()
    assert "MU" in all_bars
    assert len(all_bars["MU"]) == 1

    # Temporarily set main.daily_bar_store to verify _capture_checkpoint
    saved_store = main.daily_bar_store
    main.daily_bar_store = store
    try:
        payload = main._capture_checkpoint()
        assert "daily_bars" in payload
        assert "MU" in payload["daily_bars"]
        assert len(payload["daily_bars"]["MU"]) == 1

        # Restore into a fresh DailyBarStore
        restored_store = DailyBarStore()
        restore_runtime_state(
            payload,
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
            daily_bar_store=restored_store,
        )

        restored_bars = restored_store.get_all_bars()
        assert "MU" in restored_bars
        assert len(restored_bars["MU"]) == 1
        assert restored_bars["MU"][0].close == 103.0
        assert restored_bars["MU"][0].date == d1
    finally:
        main.daily_bar_store = saved_store


# =====================================================================
# DEFECT 10: Multi-Arm Concurrent Capital Preservation & Isolation
# =====================================================================
def test_defect_10_multi_arm_capital_isolation():
    """Verify swing and intraday positions maintain arm separation, distinct caps, and margin integrity."""
    acct = PaperTradingAccount(initial_cash=50000.0)
    risk_eng = InstitutionalRiskEngine(config=RiskEngineConfig(max_account_equity=50000.0))
    exec_eng = ExecutionEngine(account=acct)
    swing_eng = SwingStrategyEngine(account=acct, execution_engine=exec_eng, risk_engine=risk_eng)

    now_dt = datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc)
    # Stage and fill 2 swing positions ($25k each = $50k)
    swing_eng.staged_manager.stage_buy("MU", 25000.0, 3.0, date(2026, 9, 22), "PANIC_DIP")
    swing_eng.staged_manager.stage_buy("LRCX", 25000.0, 15.0, date(2026, 9, 22), "PANIC_DIP")
    res = swing_eng.execute_market_open({"MU": 100.0, "LRCX": 500.0}, now_dt, apply_slippage=False)

    assert len(res["entries"]) == 2
    assert len(swing_eng.get_active_swing_positions()) == 2

    # Check that 3rd swing position is rejected
    check_swing_3 = risk_eng.evaluate_order_request(
        symbol="GS", side="BUY", requested_qty=50, entry_price=450.0,
        stop_price=420.0, account_equity=acct.equity, buying_power=acct.buying_power,
        active_positions_count=len(acct.positions), active_symbols=set(acct.positions.keys()),
        active_sectors=set(), arm=TradingArm.SWING, strategy_id="swing_panic_dip",
        active_swing_positions_count=len(swing_eng.get_active_swing_positions()),
    )
    assert check_swing_3.approved is False
    assert "MAX_CONCURRENT_SWING_POSITIONS_REACHED" in check_swing_3.reason

    # Intraday trades can still be evaluated under Day Trading Buying Power
    assert acct.buying_power > 0.0


# =====================================================================
# DEFECT 11: Market Open Stale Price Elimination via today_open_prices
# =====================================================================
@pytest.mark.asyncio
async def test_defect_11_market_open_stale_price_prevention():
    """Verify that staged swing orders execute strictly on confirmed today_open_prices,
    never falling back to stale previous-day prices in latest_market_prices when another
    symbol's open bar arrives first.
    """
    main.reset_runtime_state()
    assert len(main.today_open_prices) == 0
    assert len(main.latest_market_prices) == 0

    staged_mgr = main.swing_staged_order_manager
    staged_mgr.clear()

    # Stage entries for KLAC and LRCX
    eval_date = date(2026, 9, 23)
    staged_mgr.stage_buy("KLAC", target_notional=25000.0, daily_atr=15.0, signal_date=eval_date, reason="PANIC_DIP")
    staged_mgr.stage_buy("LRCX", target_notional=25000.0, daily_atr=8.0, signal_date=eval_date, reason="PANIC_DIP")
    main.swing_reserved_symbols.add("KLAC")
    main.swing_reserved_symbols.add("LRCX")

    # Seed latest_market_prices with stale previous-day close prices
    main.latest_market_prices["LRCX"] = 500.00  # Stale price! Today's real open will be 660.00

    # 1. Simulate 09:30:00 ET opening bar for KLAC only
    open_time_klac = datetime(2026, 9, 24, 9, 30, 0, tzinfo=main.ET_TZ)
    bar_klac = BarEvent(
        symbol="KLAC",
        open=750.00,
        high=752.00,
        low=749.00,
        close=751.00,
        volume=50000,
        timestamp=open_time_klac,
    )
    await main.handle_bar_event(bar_klac)

    # Invariant 1: today_open_prices holds KLAC, but NOT LRCX
    assert "KLAC" in main.today_open_prices
    assert main.today_open_prices["KLAC"] == 750.00
    assert "LRCX" not in main.today_open_prices

    # Invariant 2: KLAC was executed at today's open price
    assert "KLAC" in main.account.positions
    assert main.account.positions["KLAC"].arm == TradingArm.SWING

    # Invariant 3: LRCX was NOT executed using the stale price (500.00) in latest_market_prices!
    assert "LRCX" not in main.account.positions
    assert staged_mgr.is_staged_for_entry("LRCX") is True

    # 2. Simulate 09:31:00 ET opening bar for LRCX arriving delayed
    open_time_lrcx = datetime(2026, 9, 24, 9, 31, 0, tzinfo=main.ET_TZ)
    bar_lrcx = BarEvent(
        symbol="LRCX",
        open=660.00,
        high=662.00,
        low=659.00,
        close=661.00,
        volume=60000,
        timestamp=open_time_lrcx,
    )
    await main.handle_bar_event(bar_lrcx)

    # Invariant 4: LRCX is now executed at its true today open price (660.00)
    assert "LRCX" in main.today_open_prices
    assert main.today_open_prices["LRCX"] == 660.00
    assert "LRCX" in main.account.positions
    assert main.account.positions["LRCX"].arm == TradingArm.SWING
    expected_shares = int(25000.0 // 660.0)
    assert main.account.positions["LRCX"].shares == expected_shares

    # 3. Verify session boundary purging on next session
    main.today_open_prices["MU"] = 100.0
    main.latest_market_prices["MU"] = 100.0
    next_session_dt = datetime(2026, 9, 25, 9, 0, 0, tzinfo=main.ET_TZ)
    main._check_session_boundary(next_session_dt)
    assert len(main.today_open_prices) == 0
    assert len(main.latest_market_prices) == 0

