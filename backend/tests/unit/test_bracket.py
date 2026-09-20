"""backend/tests/unit/test_bracket.py
Unit test suite for DynamicBracketManager, 1.5R/2.5R targets, breakeven ratchets, and monotonic ATR trailing stops.
"""
from datetime import datetime, timezone
import pytest

from backend.app.core.bracket import DynamicBracketManager, BracketStatus


def test_bracket_creation_multi_tier():
    manager = DynamicBracketManager(breakeven_buffer=0.02)
    # Entry 100.00, Stop 98.00 -> R = 2.00, Target 1 = 103.00, Target 2 = 105.00
    brk = manager.create_bracket(
        bracket_id="b1",
        symbol="TSLA",
        side="LONG",
        total_qty=100,
        entry_price=100.00,
        stop_price=98.00,
        strategy_id="orb",
    )
    assert brk.target_1_price == 103.00
    assert brk.target_2_price == 105.00
    assert brk.target_1_qty == 50
    assert brk.target_2_qty == 50
    assert brk.status == BracketStatus.PENDING_ENTRY


def test_bracket_target_1_fill_and_breakeven_ratchet():
    manager = DynamicBracketManager(breakeven_buffer=0.02)
    brk = manager.create_bracket(
        bracket_id="b1",
        symbol="AAPL",
        side="LONG",
        total_qty=100,
        entry_price=100.00,
        stop_price=98.00,
    )
    now = datetime.now(timezone.utc)
    # Activate on entry fill
    manager.activate_bracket_on_fill("b1", 100, 100.00, now)
    assert brk.status == BracketStatus.ACTIVE

    # Simulate Target 1 fill at 103.00
    directive = manager.on_child_order_fill(brk.target_1_order_id, 103.00, 50, now)
    assert brk.status == BracketStatus.TARGET_1_HIT
    # Stop ratcheted to entry (100.00) + buffer (0.02) = 100.02
    assert brk.current_stop_price == 100.02
    assert brk.remaining_qty == 50
    assert directive.action == "MODIFY_ORDER"
    assert directive.orders_to_modify[0]["new_stop_price"] == 100.02
    assert directive.orders_to_modify[0]["new_qty"] == 50


def test_bracket_stop_fill_cancels_targets():
    manager = DynamicBracketManager(breakeven_buffer=0.02)
    brk = manager.create_bracket(
        bracket_id="b2",
        symbol="NVDA",
        side="LONG",
        total_qty=100,
        entry_price=120.00,
        stop_price=118.00,
    )
    now = datetime.now(timezone.utc)
    manager.activate_bracket_on_fill("b2", 100, 120.00, now)

    # Stop hits at 118.00
    directive = manager.on_child_order_fill(brk.stop_order_id, 118.00, 100, now)
    assert brk.status == BracketStatus.COMPLETED_STOP
    assert directive.action == "CANCEL_ORDER"
    assert brk.target_1_order_id in directive.orders_to_cancel
    assert brk.target_2_order_id in directive.orders_to_cancel


def test_bracket_trailing_stop_monotonicity():
    manager = DynamicBracketManager(breakeven_buffer=0.02)
    brk = manager.create_bracket(
        bracket_id="b3",
        symbol="AAPL",
        side="LONG",
        total_qty=100,
        entry_price=100.00,
        stop_price=98.00,
        use_trailing_target_2=True,
        trail_atr_multiplier=1.5,
    )
    now = datetime.now(timezone.utc)
    manager.activate_bracket_on_fill("b3", 100, 100.00, now)

    # Bar 1: Stock rallies to High $104.00, ATR = 1.0. Trail distance = 1.5 * 1.0 = 1.50.
    # Potential stop = 104.00 - 1.50 = 102.50 > 98.00 -> Ratchets to 102.50!
    d1 = manager.update_trailing_stop("AAPL", current_bar_high=104.00, current_bar_low=102.00, current_atr=1.0, timestamp=now)
    assert d1 is not None
    assert brk.current_stop_price == 102.50

    # Bar 2: Stock pulls back to High $103.00, Low $101.50, ATR = 1.0.
    # Potential stop = 104.00 - 1.50 = 102.50 (equal, not higher) -> Stop does NOT loosen!
    d2 = manager.update_trailing_stop("AAPL", current_bar_high=103.00, current_bar_low=101.50, current_atr=1.0, timestamp=now)
    assert d2 is None
    assert brk.current_stop_price == 102.50  # Strictly preserved


def test_bracket_odd_quantity_split():
    manager = DynamicBracketManager()
    brk = manager.create_bracket(
        bracket_id="b4",
        symbol="AMZN",
        side="LONG",
        total_qty=7,
        entry_price=50.00,
        stop_price=48.00,
    )
    assert brk.target_1_qty == 3
    assert brk.target_2_qty == 4
    assert brk.target_1_qty + brk.target_2_qty == 7


def test_manual_tighten_stop():
    manager = DynamicBracketManager()
    brk = manager.create_bracket(
        bracket_id="b5",
        symbol="GOOGL",
        side="LONG",
        total_qty=50,
        entry_price=150.00,
        stop_price=147.00,
    )
    now = datetime.now(timezone.utc)
    manager.activate_bracket_on_fill("b5", 50, 150.00, now)

    # Tighten stop to $149.00
    directive = manager.manual_tighten_stop("GOOGL", 149.00)
    assert brk.current_stop_price == 149.00
    assert directive.action == "MODIFY_ORDER"
    assert directive.orders_to_modify[0]["new_stop_price"] == 149.00
