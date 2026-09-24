"""backend/tests/test_swing_strategy.py
Unit & Integration Tests for Swing Trading Engine ("2-Day Panic Dip").

Covers:
1. Earnings Calendar & 48-Hour Blackout Window
2. SwingStagedOrderManager (Overnight order holding outside working_orders)
3. 16:00 ET Close Qualification & Exit Scanning
4. 09:30 ET Open Execution (Exits first, $25,000 sizing, 2.5x ATR emergency stop)
5. Max 2 Concurrent Swing Positions Cap Enforcement
6. Hard 2.5x ATR Emergency Stop Intraday Monitoring
7. Mutual Exclusion between Swing and Intraday Trading
8. Full Multi-Day Lifecycle Simulation
"""
from datetime import date, datetime, timedelta, timezone
import pytest

from backend.app.core.account import PaperTradingAccount, TradingArm
from backend.app.core.engine import ExecutionEngine
from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig
from backend.app.strategies.earnings_calendar import EarningsCalendar, EarningsEvent
from backend.app.strategies.swing_indicators import DailyBar, DailyBarStore
from backend.app.strategies.swing_panic_dip import (
    CERTIFIED_SWING_SYMBOLS,
    SwingStagedOrderManager,
    SwingStrategyEngine,
)


def _populate_test_store(store: DailyBarStore, base_date: date) -> None:
    """Populate 215 bars for QQQ and certified stocks for testing."""
    for sym in ["QQQ", "LRCX", "KLAC", "MU", "AMD", "GS"]:
        bars = []
        p = 450.0 if sym == "QQQ" else 100.0
        trend = 0.1 if sym == "QQQ" else 0.5
        for i in range(215):
            d = base_date - timedelta(days=214 - i)
            o = round(p, 2)
            c = round(p + trend, 2)
            h = round(max(o, c) + 2.0, 2)
            low_p = round(min(o, c) - 2.0, 2)
            bars.append(DailyBar(
                symbol=sym,
                date=d,
                open=o,
                high=h,
                low=low_p,
                close=c,
                volume=1000000,
                finalized=True,
            ))
            p = c
        # Make the last 2 bars a panic dip for LRCX (RSI(2) < 10 while close remains > 200 SMA)
        if sym == "LRCX":
            last_c = bars[-1].close
            bars[-2].close = round(last_c - 10.0, 2)
            bars[-2].high = round(last_c - 8.0, 2)
            bars[-2].low = round(last_c - 12.0, 2)
            bars[-1].close = round(last_c - 20.0, 2)
            bars[-1].high = round(last_c - 18.0, 2)
            bars[-1].low = round(last_c - 22.0, 2)
        for b in bars:
            store.append_bar(b)


class TestEarningsCalendar:
    def test_seed_loading(self):
        cal = EarningsCalendar(seed_path="backend/app/data/earnings_calendar.json")
        for sym in CERTIFIED_SWING_SYMBOLS:
            events = cal.get_events(sym)
            assert len(events) >= 1, f"Expected earnings events for {sym}"

    def test_is_blackout_active(self):
        cal = EarningsCalendar()
        eval_date = date(2026, 9, 23)
        # Event in 1 day (tomorrow) -> within 48h
        cal.add_event(EarningsEvent(symbol="MU", report_date=date(2026, 9, 24), report_time="amc"))
        assert cal.is_blackout_active("MU", eval_date, horizon_hours=48.0) is True

        # Event in 10 days -> outside 48h
        cal.add_event(EarningsEvent(symbol="GS", report_date=date(2026, 10, 15), report_time="bmo"))
        assert cal.is_blackout_active("GS", eval_date, horizon_hours=48.0) is False

    def test_has_earnings_tomorrow(self):
        cal = EarningsCalendar()
        tuesday = date(2026, 9, 22)
        cal.add_event(EarningsEvent(symbol="AMD", report_date=date(2026, 9, 23), report_time="amc"))
        assert cal.has_earnings_tomorrow("AMD", tuesday) is True
        assert cal.has_earnings_tomorrow("AMD", date(2026, 9, 20)) is False

    @pytest.mark.asyncio
    async def test_graceful_fallback_on_remote_failure(self):
        cal = EarningsCalendar(
            seed_path="backend/app/data/earnings_calendar.json",
            remote_url="http://127.0.0.1:9999/unreachable_earnings_api",
        )
        # Should gracefully return False without raising an exception
        success = await cal.refresh_from_remote()
        assert success is False
        # Cached seed must remain intact
        assert len(cal.get_events("LRCX")) >= 1


class TestSwingStagedOrderManager:
    def test_stage_and_retrieve_orders(self):
        mgr = SwingStagedOrderManager()
        sig_d = date(2026, 9, 22)

        buy = mgr.stage_buy(
            symbol="LRCX",
            target_notional=25000.0,
            daily_atr=5.25,
            signal_date=sig_d,
            reason="PANIC_DIP",
        )
        assert buy.action == "BUY"
        assert buy.target_notional == 25000.0
        assert mgr.is_staged_for_entry("LRCX") is True

        sell = mgr.stage_sell(
            symbol="AMD",
            shares=150,
            signal_date=sig_d,
            reason="5_SMA_CROSS",
        )
        assert sell.action == "SELL"
        assert sell.shares == 150
        assert mgr.is_staged_for_exit("AMD") is True

        assert len(mgr.get_staged_entries()) == 1
        assert len(mgr.get_staged_exits()) == 1
        assert len(mgr.get_staged_orders()) == 2

        mgr.remove_for_symbol("LRCX")
        assert mgr.is_staged_for_entry("LRCX") is False
        assert len(mgr.get_staged_orders()) == 1


class TestSwingStrategyEngineExecution:
    @pytest.fixture
    def setup_engine(self):
        account = PaperTradingAccount(initial_cash=50000.0)
        risk_cfg = RiskEngineConfig(starting_equity=50000.0)
        risk_engine = InstitutionalRiskEngine(config=risk_cfg)
        execution_engine = ExecutionEngine(account=account)
        bar_store = DailyBarStore()
        calendar = EarningsCalendar()
        staged_mgr = SwingStagedOrderManager()

        reserved_symbols = set()

        def reserve_cb(sym: str):
            reserved_symbols.add(sym.upper())

        def release_cb(sym: str):
            reserved_symbols.discard(sym.upper())

        def is_res_cb(sym: str) -> bool:
            return sym.upper() in reserved_symbols

        engine = SwingStrategyEngine(
            account=account,
            execution_engine=execution_engine,
            risk_engine=risk_engine,
            bar_store=bar_store,
            calendar=calendar,
            staged_manager=staged_mgr,
            slot_notional=25000.0,
            max_concurrent_positions=2,
            stop_atr_multiplier=2.5,
            reserve_symbol_cb=reserve_cb,
            release_symbol_cb=release_cb,
            is_reserved_cb=is_res_cb,
        )
        return engine, account, execution_engine, bar_store, calendar, staged_mgr, reserved_symbols

    def test_1600_close_qualification_and_staging(self, setup_engine):
        engine, account, _, bar_store, calendar, staged_mgr, reserved = setup_engine
        eval_date = date(2026, 9, 22)
        _populate_test_store(bar_store, eval_date)

        # Evaluate at 16:00 close
        res = engine.evaluate_market_close(eval_date)
        assert len(res["staged_entries"]) >= 1
        staged_entries = staged_mgr.get_staged_entries()

        # LRCX had 2 sharp down days, close > 200 SMA, RS >= QQQ -> should qualify
        assert any(e.symbol == "LRCX" for e in staged_entries), "LRCX should qualify for swing entry"
        lrcx_staged = next(e for e in staged_entries if e.symbol == "LRCX")
        assert lrcx_staged.target_notional == 25000.0
        assert lrcx_staged.daily_atr > 0.0
        assert "LRCX" in reserved

    def test_0930_open_execution_and_sizing(self, setup_engine):
        engine, account, _, _, _, staged_mgr, _ = setup_engine
        eval_date = date(2026, 9, 22)
        open_time = datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc)

        # Stage BUY for LRCX at $25k notional with ATR = $5.00
        staged_mgr.stage_buy(
            symbol="LRCX",
            target_notional=25000.0,
            daily_atr=5.00,
            signal_date=eval_date,
            reason="PANIC_DIP",
        )

        open_prices = {"LRCX": 800.0}
        exec_res = engine.execute_market_open(open_prices, open_time)

        # Sizing check: floor(25000 / 800) = 31 shares ($24,800 notional)
        assert len(exec_res["entries"]) == 1
        entry_info = exec_res["entries"][0]
        assert entry_info["symbol"] == "LRCX"
        assert entry_info["shares"] == 31
        assert entry_info["price"] == 800.0

        # Position checks
        pos = account.positions.get("LRCX")
        assert pos is not None
        assert pos.shares == 31
        assert pos.arm == TradingArm.SWING
        assert pos.strategy_id == "swing_panic_dip"

        # Emergency Stop check: P_fill - 2.5 * Daily_ATR (anchored to realized fill price)
        assert pos.stop_loss_price == round(pos.avg_entry_price - 2.5 * 5.0, 2)
        assert pos.entry_atr == 5.00

    def test_concurrency_cap_max_2_positions(self, setup_engine):
        engine, account, _, _, _, staged_mgr, _ = setup_engine
        eval_date = date(2026, 9, 22)
        open_time = datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc)

        # Pre-seed 2 active swing positions
        account.apply_fill(
            order_id="pre_1",
            symbol="LRCX",
            side="BUY",
            qty=30,
            price=800.0,
            fee=0.0,
            timestamp=open_time,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=780.0,
        )
        account.apply_fill(
            order_id="pre_2",
            symbol="KLAC",
            side="BUY",
            qty=35,
            price=700.0,
            fee=0.0,
            timestamp=open_time,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=680.0,
        )
        assert len(engine.get_active_swing_positions()) == 2

        # Try staging a 3rd swing order for AMD
        staged_mgr.stage_buy("AMD", 25000.0, 4.0, eval_date, "PANIC_DIP")

        # Open execution should drop AMD due to concurrency cap (max 2 positions)
        exec_res = engine.execute_market_open({"AMD": 150.0}, open_time)
        assert len(exec_res["entries"]) == 0
        assert len(engine.get_active_swing_positions()) == 2
        assert "AMD" not in account.positions

    def test_staged_exits_execute_before_entries(self, setup_engine):
        engine, account, _, _, _, staged_mgr, _ = setup_engine
        open_time = datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc)

        # Pre-seed 2 positions: LRCX and KLAC
        account.apply_fill(
            order_id="pre_1",
            symbol="LRCX",
            side="BUY",
            qty=30,
            price=800.0,
            fee=0.0,
            timestamp=open_time,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=780.0,
        )
        account.apply_fill(
            order_id="pre_2",
            symbol="KLAC",
            side="BUY",
            qty=35,
            price=700.0,
            fee=0.0,
            timestamp=open_time,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=680.0,
        )

        # Stage exit for LRCX (e.g. 5 SMA cross) and entry for AMD
        staged_mgr.stage_sell("LRCX", 30, date(2026, 9, 22), "5_SMA_CROSS")
        staged_mgr.stage_buy("AMD", 25000.0, 4.0, date(2026, 9, 22), "PANIC_DIP")

        # Execute at open: LRCX exits first -> freed slot allows AMD to enter
        open_prices = {"LRCX": 820.0, "KLAC": 710.0, "AMD": 150.0}
        exec_res = engine.execute_market_open(open_prices, open_time)

        assert len(exec_res["exits"]) == 1
        assert exec_res["exits"][0]["symbol"] == "LRCX"
        assert "LRCX" not in account.positions

        assert len(exec_res["entries"]) == 1
        assert exec_res["entries"][0]["symbol"] == "AMD"
        assert "AMD" in account.positions

        # Total active swing positions is still exactly 2 (KLAC + AMD)
        assert len(engine.get_active_swing_positions()) == 2

    def test_emergency_stop_intraday_trigger(self, setup_engine):
        engine, account, _, _, _, _, reserved = setup_engine
        now_dt = datetime(2026, 9, 23, 10, 15, tzinfo=timezone.utc)

        # Open swing position on AMD: entry $150.00, stop $140.00 (2.5x ATR $4.00 = $10.00)
        account.apply_fill(
            order_id="ord_entry",
            symbol="AMD",
            side="BUY",
            qty=166,
            price=150.0,
            fee=0.0,
            timestamp=now_dt,
            arm=TradingArm.SWING,
            strategy_id="swing_panic_dip",
            stop_loss_price=140.0,
        )
        reserved.add("AMD")
        pos = account.positions["AMD"]
        pos.stop_loss_price = 140.0

        # Tick at $145.00: stop does NOT trigger
        stops_none = engine.check_intraday_emergency_stops({"AMD": 145.0}, now_dt)
        assert len(stops_none) == 0
        assert "AMD" in account.positions

        # Tick at $139.50: stop breaches!
        stops_hit = engine.check_intraday_emergency_stops({"AMD": 139.50}, now_dt)
        assert len(stops_hit) == 1
        assert stops_hit[0]["symbol"] == "AMD"
        assert stops_hit[0]["stop_price"] == 140.0
        assert "AMD" not in account.positions
        assert "AMD" not in reserved

    def test_mutual_exclusion_with_intraday(self, setup_engine):
        engine, account, _, bar_store, _, staged_mgr, _ = setup_engine
        eval_date = date(2026, 9, 22)
        _populate_test_store(bar_store, eval_date)

        # Place intraday position on LRCX
        account.apply_fill(
            order_id="intraday_lrcx",
            symbol="LRCX",
            side="BUY",
            qty=10,
            price=800.0,
            fee=0.0,
            timestamp=datetime.now(timezone.utc),
            arm=TradingArm.INTRADAY,
            strategy_id="orb",
        )

        # Evaluate at close: swing engine must NOT stage entry for LRCX due to intraday conflict
        engine.evaluate_market_close(eval_date)
        assert not staged_mgr.is_staged_for_entry("LRCX")
