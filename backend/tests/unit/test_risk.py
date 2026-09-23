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
    # 50% max position equity on $50k = $25,000. At $10 entry price -> max 2,500 shares.
    # Even if stop distance is $0.05 (allowing 10,000 shares on risk), concentration clamps to 2,500.
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
    assert res.authorized_qty == 2500  # Clamped by 50% equity ($25k) concentration cap
    assert res.estimated_risk_dollars == 125.00  # min(8000, 2500) * 0.05


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
    # max_position_equity_pct is 0.500 ($25k on $50k equity): a single name's gap
    # risk must stay inside the $1,500 daily circuit breaker.
    assert cfg.max_position_equity_pct == 0.500

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


def test_production_wiring_caps_a_single_position_at_25k():
    """The lever is the wired engine, not the dataclass default.

    main.py derives max_position_equity_pct from MAX_POSITION_NOTIONAL / INITIAL_CASH and
    passes MAX_POSITION_NOTIONAL to the account separately. Both must land on $25,000, or
    the two caps disagree and the looser one silently wins.
    """
    from backend.app import main
    from backend.app.config import settings

    assert settings.MAX_POSITION_NOTIONAL == 25000.0
    assert main.risk_engine.config.max_position_equity_pct == 0.500
    assert main.account.max_position_notional == 25000.0

    # $25,000 cap at a $100 entry is 250 shares, even though a 0.5% stop would fund 1,000
    # shares on the $500 risk budget.
    res = main.risk_engine.evaluate_order_request(
        symbol="AAPL",
        side="BUY",
        requested_qty=1000,
        entry_price=100.00,
        stop_price=99.50,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set(),
    )
    assert res.approved is True
    assert res.authorized_qty == 250


def test_single_position_gap_loss_stays_inside_the_daily_breaker():
    """A 5% adverse gap on the largest allowed position must not exceed the $1,500 day limit."""
    cfg = RiskEngineConfig()
    max_notional = cfg.starting_equity * cfg.max_position_equity_pct
    assert max_notional * 0.05 < cfg.hard_max_daily_loss_dollars


def test_sector_concentration_two_allowed_third_rejected():
    """Verify max 2 positions per sector allowed, 3rd in same sector rejected with CORRELATED_SECTOR_EXPOSURE."""
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

    # 1. First Semiconductor position (NVDA) - approved
    res1 = engine.evaluate_order_request(
        symbol="NVDA",
        side="BUY",
        requested_qty=50,
        entry_price=120.00,
        stop_price=118.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=[],
    )
    assert res1.approved is True

    # 2. Second Semiconductor position (AMD) - approved (2 allowed per sector)
    res2 = engine.evaluate_order_request(
        symbol="AMD",
        side="BUY",
        requested_qty=50,
        entry_price=100.00,
        stop_price=98.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=1,
        active_symbols={"NVDA"},
        active_sectors=["Semiconductors"],
    )
    assert res2.approved is True

    # 3. Third Semiconductor position (e.g. INTC registered as Semiconductors) - REJECTED
    engine.register_symbol_sector("INTC", "Semiconductors")
    res3 = engine.evaluate_order_request(
        symbol="INTC",
        side="BUY",
        requested_qty=50,
        entry_price=30.00,
        stop_price=29.50,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=2,
        active_symbols={"NVDA", "AMD"},
        active_sectors=["Semiconductors", "Semiconductors"],
    )
    assert res3.approved is False
    assert res3.rejection_code == "CORRELATED_SECTOR_EXPOSURE"
    assert "CORRELATED_SECTOR_EXPOSURE" in res3.reason

    # 4. Third position in different sector (MSFT - Software) - APPROVED (2+1 = 3 positions total)
    res4 = engine.evaluate_order_request(
        symbol="MSFT",
        side="BUY",
        requested_qty=20,
        entry_price=400.00,
        stop_price=395.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=2,
        active_symbols={"NVDA", "AMD"},
        active_sectors=["Semiconductors", "Semiconductors"],
    )
    assert res4.approved is True

    # 5. Fourth position overall (TSLA) - REJECTED by max_concurrent_positions (3 total)
    res5 = engine.evaluate_order_request(
        symbol="TSLA",
        side="BUY",
        requested_qty=20,
        entry_price=200.00,
        stop_price=196.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=3,
        active_symbols={"NVDA", "AMD", "MSFT"},
        active_sectors=["Semiconductors", "Semiconductors", "Software"],
    )
    assert res5.approved is False
    assert res5.rejection_code == "MAX_CONCURRENT_POSITIONS_REACHED"


def test_index_exempt_from_sector_cap():
    """Verify SPY and QQQ (Index) are exempt from sector concentration restrictions."""
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    res = engine.evaluate_order_request(
        symbol="SPY",
        side="BUY",
        requested_qty=50,
        entry_price=500.00,
        stop_price=495.00,
        account_equity=50000.00,
        buying_power=200000.00,
        active_positions_count=1,
        active_symbols={"QQQ"},
        active_sectors=["Index"],
    )
    assert res.approved is True


def test_symbol_sectors_12_symbols_mapped():
    """Verify all 12 expanded watchlist symbols are correctly classified in InstitutionalRiskEngine."""
    engine = InstitutionalRiskEngine()
    expected = {
        "SPY": "Index",
        "QQQ": "Index",
        "AAPL": "Technology",
        "NVDA": "Semiconductors",
        "AMD": "Semiconductors",
        "MSFT": "Software",
        "PLTR": "Software",
        "TSLA": "Consumer Discretionary",
        "AMZN": "Consumer Discretionary",
        "GOOGL": "Communication Services",
        "META": "Communication Services",
        "COIN": "Fintech/Crypto",
    }
    for sym, expected_sector in expected.items():
        assert engine.symbol_sectors.get(sym) == expected_sector, f"Symbol {sym} sector mismatch"

