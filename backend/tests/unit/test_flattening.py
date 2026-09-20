"""backend/tests/unit/test_flattening.py
Unit test suite for ZeroOvernightFlatteningEngine, 4-phase protocol, and MarketClock.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from backend.app.core.flattening import (
    ZeroOvernightFlatteningEngine,
    MarketClock,
    FlatteningPhase,
)

ET = ZoneInfo("America/New_York")


def test_four_phase_flattening_progression():
    clock = MarketClock()
    engine = ZeroOvernightFlatteningEngine(clock=clock)

    # Phase 1: 15:45:00 ET
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 45, 0, tzinfo=ET))
    d1 = engine.check_time_tick()
    assert d1 is not None
    assert d1.phase == FlatteningPhase.ENTRY_LOCKOUT
    assert d1.lock_new_entries is True
    assert engine.current_phase == FlatteningPhase.ENTRY_LOCKOUT

    # Phase 2: 15:50:00 ET
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 50, 0, tzinfo=ET))
    d2 = engine.check_time_tick()
    assert d2 is not None
    assert d2.phase == FlatteningPhase.ORDER_PURGE
    assert d2.cancel_all_orders is True
    assert engine.current_phase == FlatteningPhase.ORDER_PURGE

    # Phase 3: 15:55:00 ET
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 55, 0, tzinfo=ET))
    d3 = engine.check_time_tick()
    assert d3 is not None
    assert d3.phase == FlatteningPhase.MANDATORY_LIQUIDATION
    assert d3.liquidate_all_positions is True
    assert engine.current_phase == FlatteningPhase.MANDATORY_LIQUIDATION

    # Phase 4: 15:58:00 ET (Clean portfolio)
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 58, 0, tzinfo=ET))
    d4 = engine.check_time_tick()
    assert d4 is not None
    assert d4.phase == FlatteningPhase.ZERO_AUDIT
    assert d4.run_audit is True

    audit_res = engine.execute_phase_4_audit(open_positions={}, working_orders=[])
    assert audit_res.phase == FlatteningPhase.ZERO_AUDIT
    assert audit_res.audit_passed is True
    assert engine.audit_passed is True


def test_flattening_audit_retry_on_lingering_position():
    clock = MarketClock()
    engine = ZeroOvernightFlatteningEngine(clock=clock)
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 58, 0, tzinfo=ET))

    # Lingering position in AAPL
    audit_res = engine.execute_phase_4_audit(
        open_positions={"AAPL": {"shares": 100}},
        working_orders=[],
    )
    assert audit_res.audit_passed is False
    assert engine.audit_passed is False
    assert "AAPL" in audit_res.unclosed_symbols
    assert audit_res.liquidate_all_positions is True
    assert engine.audit_retries == 1


def test_market_close_transition():
    clock = MarketClock()
    engine = ZeroOvernightFlatteningEngine(clock=clock)
    clock.set_simulated_time(datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET))

    d = engine.check_time_tick()
    assert d is not None
    assert d.phase == FlatteningPhase.MARKET_CLOSED
    assert engine.current_phase == FlatteningPhase.MARKET_CLOSED


def test_pre_market_phase_and_open_transition():
    clock = MarketClock()
    clock.set_simulated_time(datetime(2026, 9, 21, 9, 15, 0, tzinfo=ET))
    engine = ZeroOvernightFlatteningEngine(clock=clock)

    # 1. At 09:15 ET (Pre-Market), check_time_tick sets phase to PRE_MARKET
    d = engine.check_time_tick()
    assert d is None
    assert engine.current_phase == FlatteningPhase.PRE_MARKET

    # 2. At 09:30 ET (Market Open), check_time_tick transitions to NORMAL_TRADING
    clock.set_simulated_time(datetime(2026, 9, 21, 9, 30, 0, tzinfo=ET))
    d2 = engine.check_time_tick()
    assert d2 is None
    assert engine.current_phase == FlatteningPhase.NORMAL_TRADING


def test_get_phase_at_time():
    from datetime import time
    engine = ZeroOvernightFlatteningEngine()
    assert engine.get_phase_at_time(time(8, 0)) == FlatteningPhase.PRE_MARKET
    assert engine.get_phase_at_time(time(9, 29, 59)) == FlatteningPhase.PRE_MARKET
    assert engine.get_phase_at_time(time(9, 30)) == FlatteningPhase.NORMAL_TRADING
    assert engine.get_phase_at_time(time(15, 44, 59)) == FlatteningPhase.NORMAL_TRADING
    assert engine.get_phase_at_time(time(15, 45)) == FlatteningPhase.ENTRY_LOCKOUT
    assert engine.get_phase_at_time(time(15, 50)) == FlatteningPhase.ORDER_PURGE
    assert engine.get_phase_at_time(time(15, 55)) == FlatteningPhase.MANDATORY_LIQUIDATION
    assert engine.get_phase_at_time(time(15, 58)) == FlatteningPhase.ZERO_AUDIT
    assert engine.get_phase_at_time(time(16, 0)) == FlatteningPhase.MARKET_CLOSED
