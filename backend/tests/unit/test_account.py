"""backend/tests/unit/test_account.py
Unit test suite for PaperTradingAccount state machine, FINRA Rule 4210 DTBP, and PnL ledger.
"""
from datetime import datetime, timezone
import pytest

from backend.app.core.account import PaperTradingAccount, PositionSide, AccountStatus


def test_initial_account_state():
    account = PaperTradingAccount(initial_cash=50000.00)
    assert account.initial_balance == 50000.00
    assert account.cash == 50000.00
    assert account.equity == 50000.00
    assert account.buying_power == 200000.00  # 4:1 FINRA leverage
    assert account.maintenance_margin == 0.00
    assert account.margin_excess == 50000.00
    assert account.realized_pnl == 0.00
    assert account.unrealized_pnl == 0.00
    assert account.daily_drawdown_dollars == 0.00
    assert account.status == AccountStatus.ACTIVE
    assert len(account.positions) == 0


def test_long_buy_fill():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    # Buy 100 AAPL @ $150.00 with $0.00 fee
    r_delta, pos = account.apply_fill("ord1", "AAPL", "BUY", 100, 150.00, 0.00, now)

    assert r_delta == 0.00
    assert pos is not None
    assert pos.symbol == "AAPL"
    assert pos.side == PositionSide.LONG
    assert pos.shares == 100
    assert pos.avg_entry_price == 150.00
    assert account.cash == 35000.00  # $50,000 - $15,000
    assert account.equity == 50000.00  # $35,000 cash + $15,000 MV
    # Maintenance margin: 25% of $15,000 = $3,750
    assert account.maintenance_margin == 3750.00
    assert account.margin_excess == 46250.00  # $50,000 - $3,750
    assert account.buying_power == 185000.00  # 4 * $46,250


def test_mark_to_market_long_gain():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "AAPL", "BUY", 100, 150.00, 0.00, now)

    account.update_market_price("AAPL", 155.00)
    pos = account.get_position("AAPL")
    assert pos is not None
    assert pos.market_price == 155.00
    assert pos.unrealized_pnl == 500.00  # 100 * (155 - 150)
    assert account.equity == 50500.00
    assert account.daily_drawdown_dollars == 0.00


def test_mark_to_market_long_loss():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "AAPL", "BUY", 100, 150.00, 0.00, now)

    account.update_market_price("AAPL", 145.00)
    pos = account.get_position("AAPL")
    assert pos is not None
    assert pos.unrealized_pnl == -500.00
    assert account.equity == 49500.00
    assert account.daily_drawdown_dollars == 500.00
    assert account.daily_drawdown_pct == 0.01


def test_partial_sell_long():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "AAPL", "BUY", 100, 150.00, 0.00, now)

    # Sell 40 shares @ $160.00, fee $1.00
    r_delta, pos = account.apply_fill("ord2", "AAPL", "SELL", 40, 160.00, 1.00, now)

    # Realized PnL: 40 * (160 - 150) - 1.00 = $399.00
    assert r_delta == 399.00
    assert account.realized_pnl == 399.00
    assert pos is not None
    assert pos.shares == 60
    assert pos.avg_entry_price == 150.00  # Avg entry price unchanged on partial exit
    assert account.cash == 41399.00  # $35,000 + (40 * 160 - 1.00)


def test_full_sell_long():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "AAPL", "BUY", 100, 150.00, 0.00, now)
    account.apply_fill("ord2", "AAPL", "SELL", 40, 160.00, 1.00, now)

    # Sell remaining 60 shares @ $165.00, fee $1.50
    r_delta, pos = account.apply_fill("ord3", "AAPL", "SELL", 60, 165.00, 1.50, now)

    # 60 * (165 - 150) - 1.50 = 900 - 1.50 = $898.50
    assert r_delta == 898.50
    assert pos is None
    assert "AAPL" not in account.positions
    assert account.realized_pnl == round(399.00 + 898.50, 2)  # $1,297.50
    assert account.equity == round(50000.00 + 1297.50, 2)


def test_short_sell_fill():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    # Short Sell 100 TSLA @ $200.00, fee $1.00
    r_delta, pos = account.apply_fill("ord1", "TSLA", "SELL", 100, 200.00, 1.00, now)

    assert r_delta == 0.00
    assert pos is not None
    assert pos.side == PositionSide.SHORT
    assert pos.shares == 100
    assert pos.avg_entry_price == 200.00
    assert account.cash == 69999.00  # $50,000 + (100 * 200 - 1.00)
    assert account.equity == 49999.00  # $69,999 cash - $20,000 short liability
    assert account.fees_paid == 1.00


def test_mark_to_market_short():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "TSLA", "SELL", 100, 200.00, 0.00, now)

    # Price drops to $185.00 (profit for Short)
    account.update_market_price("TSLA", 185.00)
    pos = account.get_position("TSLA")
    assert pos is not None
    assert pos.unrealized_pnl == 1500.00  # 100 * (200 - 185)
    assert account.equity == 51500.00


def test_cover_short():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "TSLA", "SELL", 100, 200.00, 0.00, now)

    # Cover 100 shares @ $185.00, fee $0.00
    r_delta, pos = account.apply_fill("ord2", "TSLA", "BUY", 100, 185.00, 0.00, now)
    assert r_delta == 1500.00
    assert pos is None
    assert "TSLA" not in account.positions
    assert account.cash == 51500.00  # $70,000 - $18,500
    assert account.equity == 51500.00


def test_position_flip_long_to_short():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "AAPL", "BUY", 100, 150.00, 0.00, now)

    # Sell 150 shares @ $160.00 (closes 100 Long, opens 50 Short)
    r_delta, pos = account.apply_fill("ord2", "AAPL", "SELL", 150, 160.00, 0.00, now)
    assert r_delta == 1000.00  # 100 * (160 - 150)
    assert pos is not None
    assert pos.side == PositionSide.SHORT
    assert pos.shares == 50
    assert pos.avg_entry_price == 160.00
    assert account.equity == 51000.00


def test_position_flip_short_to_long():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    account.apply_fill("ord1", "AAPL", "SELL", 100, 150.00, 0.00, now)

    # Buy 150 shares @ $140.00 (covers 100 Short, opens 50 Long)
    r_delta, pos = account.apply_fill("ord2", "AAPL", "BUY", 150, 140.00, 0.00, now)
    assert r_delta == 1000.00  # 100 * (150 - 140)
    assert pos is not None
    assert pos.side == PositionSide.LONG
    assert pos.shares == 50
    assert pos.avg_entry_price == 140.00
    assert account.equity == 51000.00


def test_finra_4210_short_mmr_low_price():
    account = PaperTradingAccount(initial_cash=50000.00)
    now = datetime.now(timezone.utc)
    # Short 1,000 shares of $3.00 stock
    account.apply_fill("ord1", "LOW", "SELL", 1000, 3.00, 0.00, now)
    # Rule 4210(f)(10) short under $5: max(1.0 * $3000, $2.50 * 1000) = $3,000
    assert account.maintenance_margin == 3000.00


def test_can_afford_rejection_insufficient_bp():
    account = PaperTradingAccount(initial_cash=50000.00)
    # Attempt order requiring $250k buying power (account has $200k max)
    # But note: per-position cap also kicks in at $50k. Let's test buying power or concentration
    approved, reason = account.can_afford("SPY", "BUY", 1000, 400.00)  # $400k > $200k
    assert approved is False
    assert "concentration" in reason.lower() or "buying power" in reason.lower()


def test_per_position_concentration_cap():
    account = PaperTradingAccount(initial_cash=50000.00)
    # 25% of $200k = $50,000 max per position
    approved, reason = account.can_afford("AAPL", "BUY", 400, 150.00)  # 400 * 150 = $60,000 > $50,000
    assert approved is False
    assert "concentration cap" in reason


def test_pdt_sub_25k_margin_restriction():
    # Account starts with $24,000 (below $25,000 PDT threshold)
    account = PaperTradingAccount(initial_cash=24000.00)
    # Buying power throttled to 1x cash, no 4x intraday leverage
    assert account.buying_power == 24000.00
