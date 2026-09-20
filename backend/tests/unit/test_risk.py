"""backend/tests/unit/test_risk.py
Unit test suite for InstitutionalRiskEngine, circuit breakers, and position risk budgeting.
"""
from datetime import datetime, timezone
import pytest

from backend.app.core.risk import (
    InstitutionalRiskEngine,
    RiskEngineConfig,
    RiskLevel,
    BreakerStatus,
)


def test_risk_engine_normal_sizing():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    # Risk $500 (1%), Entry $100, Stop $98 -> Risk per share $2.00 -> 250 shares
    res = engine.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=300,
        entry_price=100.00,
        stop_price=98.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res.approved is True
    assert res.authorized_qty == 250
    assert res.estimated_risk_dollars == 500.00


def test_risk_engine_stop_too_tight():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    # Stop distance 0.3% < min 0.4%
    res = engine.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=100,
        entry_price=100.00,
        stop_price=99.70,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res.approved is False
    assert "STOP_DISTANCE_TOO_TIGHT" in res.reason


def test_risk_engine_stop_too_wide():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    # Stop distance 5.0% > max 4.0%
    res = engine.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=100,
        entry_price=100.00,
        stop_price=95.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res.approved is False
    assert "STOP_DISTANCE_TOO_WIDE" in res.reason


def test_risk_engine_max_concentration_cap():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    # 100% max position equity on $50k = $50,000. At $10 entry price -> max 5,000 shares.
    # Even if stop distance is $0.05 (allowing 10,000 shares on risk), concentration clamps to 5,000.
    res = engine.evaluate_order_request(
        symbol="CHEAP",
        side="BUY",
        requested_qty=8000,
        entry_price=10.00,
        stop_price=9.95,  # 0.5% stop distance
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res.approved is True
    assert res.authorized_qty == 5000  # Clamped by 100% equity ($50k) concentration cap
    assert res.estimated_risk_dollars == 250.00  # min(8000, 5000) * 0.05


def test_risk_engine_max_concurrent_positions():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    res = engine.evaluate_order_request(
        symbol="NVDA",
        side="BUY",
        requested_qty=100,
        entry_price=120.00,
        stop_price=118.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=3,  # Already at limit of 3
        active_symbols={"AAPL", "TSLA", "MSFT"},
        active_sectors={"Technology", "Consumer Discretionary"},
    )
    assert res.approved is False
    assert "MAX_CONCURRENT_POSITIONS_REACHED" in res.reason


def test_circuit_breaker_warning_threshold():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    now = datetime.now(timezone.utc)
    # Simulate equity drawdown of $1,100 ($48,900 equity)
    status = engine.evaluate_account_state(
        equity=48900.00,
        cash=48900.00,
        realized_pnl=-1100.00,
        unrealized_pnl=0.0,
        timestamp=now,
    )
    assert status == BreakerStatus.ARMED
    assert engine.risk_level == RiskLevel.WARNING
    assert engine.current_drawdown_dollars == 1100.00


def test_circuit_breaker_hard_halt_at_1500_loss():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    now = datetime.now(timezone.utc)
    # Simulate equity drawdown reaching $1,500.00
    status = engine.evaluate_account_state(
        equity=48500.00,
        cash=48500.00,
        realized_pnl=-1500.00,
        unrealized_pnl=0.0,
        timestamp=now,
    )
    assert status == BreakerStatus.HALTED_DAILY_LOSS
    assert engine.status == BreakerStatus.HALTED_DAILY_LOSS
    assert engine.risk_level == RiskLevel.HALTED

    # Subsequent orders must be rejected
    res = engine.evaluate_order_request(
        symbol="SPY",
        side="BUY",
        requested_qty=100,
        entry_price=500.00,
        stop_price=495.00,
        account_equity=48500.00,
        buying_power=190000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res.approved is False
    assert "CIRCUIT_BREAKER_HALTED" in res.reason


def test_risk_config_defaults_and_estimated_risk_dollars():
    cfg = RiskEngineConfig()
    # Task 7: max_position_equity_pct must default to 1.000 ($50k)
    assert cfg.max_position_equity_pct == 1.000

    engine = InstitutionalRiskEngine(cfg)
    # Entry $100, Stop $98 -> stop_dist = $2.00
    # On $50k equity with 1% risk ($500), authorized capacity is 250 shares.
    # When requesting 10 shares, estimated_risk_dollars must be 10 * $2.00 = $20.00, NOT 250 * $2.00 = $500.00.
    res = engine.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=10,
        entry_price=100.00,
        stop_price=98.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res.approved is True
    assert res.authorized_qty == 250
    assert res.requested_qty == 10
    assert res.estimated_risk_dollars == 20.00
