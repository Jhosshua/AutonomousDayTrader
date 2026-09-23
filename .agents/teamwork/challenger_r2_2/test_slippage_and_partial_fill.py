"""test_slippage_and_partial_fill.py
Adversarial Bracket Slippage & Partial Fill Verification Suite.
Challenger R2-2: Adversarial Bracket Slippage & Partial Fill Challenger.

Authoritative Requirements Tested:
1. Empirically challenge slippage boundary sanity checks in `activate_bracket_on_fill`:
   - BUY orders with positive slippage where fill_price >= target_1_override.
   - Verify target_1_price is dynamically re-anchored above fill_price, never creating a marketable limit sell below purchase price.
   - SHORT orders with adverse slippage where fill_price <= target_1_override.
   - Verify target_1_price is dynamically re-anchored below fill_price, never creating a marketable limit buy above purchase price.
   - Exact boundary checks (fill_price == target_1_override) and extreme multi-target slippages.
2. Empirically challenge Target 1 partial fills and stop-loss cancellations:
   - Simulate a 100-share position with Target 1 for 50 shares.
   - Simulate a partial fill of 20 shares on Target 1.
   - Verify target_1_filled is False and target_1_remaining_qty is 30.
   - Trigger a stop-loss fill for the remaining 80 shares.
   - Verify ExecutionEngine.working_orders has ZERO remaining orders (residual 30-share limit cancelled).
   - Multi-stage partial fills, dual-target partial fills, and partial stop-loss fills.
"""
from __future__ import annotations

from datetime import datetime, timezone
import random
from typing import Any, Dict, List, Optional
import pytest

from backend.app.core.account import PaperTradingAccount
from backend.app.core.bracket import (
    DynamicBracketManager,
    BracketStatus,
    BracketChildType,
    BracketOrder,
    BracketUpdateDirective,
)
from backend.app.core.engine import (
    ExecutionEngine,
    Order,
    BracketRole,
)
from backend.app.models.events import (
    OrderSide,
    OrderType,
    OrderState,
)


def apply_bracket_directive(
    engine: ExecutionEngine,
    bracket_manager: DynamicBracketManager,
    bracket_id: str,
    directive: BracketUpdateDirective,
) -> None:
    """Production-accurate replication of main.py::_apply_bracket_directive."""
    bracket = bracket_manager.brackets.get(bracket_id)
    if not bracket:
        return

    # Process cancellations
    for order_id in directive.orders_to_cancel:
        if order_id in engine.working_orders:
            try:
                engine.cancel_order(order_id, reason=f"BRACKET_{directive.bracket_status.value}")
            except Exception as exc:
                pass

    # Process new order submissions
    if directive.action == "SUBMIT_ORDERS":
        role_by_synthetic = {
            bracket.stop_order_id: BracketChildType.STOP_LOSS,
            bracket.target_1_order_id: BracketChildType.TAKE_PROFIT_1,
            bracket.target_2_order_id: BracketChildType.TAKE_PROFIT_2,
        }
        for child in directive.orders_to_submit:
            synthetic_id = child.get("order_id")
            child_type = role_by_synthetic.get(synthetic_id)
            if child_type is None:
                continue
            order_type = OrderType.STOP if child["type"] == "STOP" else OrderType.LIMIT
            child_order = engine.create_order(
                symbol=bracket.symbol,
                side=OrderSide(child["side"]),
                order_type=order_type,
                qty=int(child["qty"]),
                limit_price=float(child["price"]) if order_type == OrderType.LIMIT else None,
                stop_price=float(child["price"]) if order_type == OrderType.STOP else None,
                strategy_id=bracket.strategy_id,
                bracket_role=(
                    BracketRole.STOP_LOSS if child_type == BracketChildType.STOP_LOSS
                    else BracketRole.TAKE_PROFIT_1 if child_type == BracketChildType.TAKE_PROFIT_1
                    else BracketRole.TAKE_PROFIT_2
                ),
                parent_order_id=bracket_id,
            )
            submitted = engine.submit_order(child_order.id)
            if submitted.status != OrderState.ACCEPTED:
                continue

            bracket_manager.order_to_bracket.pop(synthetic_id, None)
            bracket_manager.order_to_bracket[child_order.id] = (bracket_id, child_type)
            if child_type == BracketChildType.STOP_LOSS:
                bracket.stop_order_id = child_order.id
            elif child_type == BracketChildType.TAKE_PROFIT_1:
                bracket.target_1_order_id = child_order.id
            else:
                bracket.target_2_order_id = child_order.id

    # Process order modifications
    for modification in directive.orders_to_modify:
        order = engine.working_orders.get(modification.get("order_id"))
        if not order:
            continue
        if "new_qty" in modification:
            new_qty = int(modification["new_qty"])
            order.remaining_qty = new_qty
            order.qty = order.filled_qty + new_qty
        if "new_stop_price" in modification:
            order.stop_price = float(modification["new_stop_price"])


# ============================================================================
# 1. Slippage Boundary Sanity Checks in activate_bracket_on_fill
# ============================================================================

class TestSlippageBoundarySanity:
    """Adversarial stress-testing of slippage boundaries and dynamic re-anchoring."""

    def test_buy_positive_slippage_exceeds_target_1_override(self):
        """
        Mission Case 1A: BUY order with positive slippage where fill_price >= target_1_override.
        Planned: Entry $100.00, Stop $98.00 (R = $2.00).
        Target 1 planned at $101.60 (0.80R), Target 2 planned at $103.60 (1.80R).
        Realized fill price: $101.80 (fill_price > target_1_override).

        Requirement:
        - target_1_override ($101.60) is rejected because it is <= fill_price ($101.80).
        - target_1_price is dynamically re-anchored above $101.80.
        - Never create a marketable limit sell below or at purchase price.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_buy_slip_1",
            symbol="AAPL",
            side="LONG",
            total_qty=100,
            entry_price=100.0,
            stop_price=98.0,
            target_1_override=101.60,
            target_2_override=103.60,
            timestamp=now,
        )

        assert bracket.target_1_price == 101.60
        assert bracket.target_2_price == 103.60

        # Simulate positive slippage filling entry at 101.80
        fill_price = 101.80
        directive = bm.activate_bracket_on_fill("brk_buy_slip_1", 100, fill_price, now)

        assert bracket.status == BracketStatus.ACTIVE
        assert bracket.entry_price == 101.80
        # Recalculated R-distance: abs(101.80 - 98.00) = 3.80
        assert bracket.r_distance == 3.80

        # Target 1 must be re-anchored strictly above fill price: 101.80 + 0.80 * 3.80 = 104.84
        expected_t1 = round(101.80 + 0.80 * 3.80, 2)
        assert expected_t1 == 104.84
        assert bracket.target_1_price == expected_t1
        assert bracket.target_1_price > fill_price, "Target 1 must be strictly above entry fill price"

        # Target 2 must also be re-anchored because 103.60 < 104.84: 101.80 + 1.80 * 3.80 = 108.64
        expected_t2 = round(101.80 + 1.80 * 3.80, 2)
        assert expected_t2 == 108.64
        assert bracket.target_2_price == expected_t2
        assert bracket.target_2_price > bracket.target_1_price, "Target 2 must be strictly above Target 1"

        # Inspect submitted orders in directive
        t1_order = next(o for o in directive.orders_to_submit if o["order_id"] == bracket.target_1_order_id)
        assert t1_order["type"] == "LIMIT"
        assert t1_order["side"] == "SELL"
        assert t1_order["price"] == 104.84
        assert t1_order["price"] > fill_price, "Submitted limit sell must never be below or at purchase price"

        t2_order = next(o for o in directive.orders_to_submit if o["order_id"] == bracket.target_2_order_id)
        assert t2_order["price"] == 108.64
        assert t2_order["price"] > t1_order["price"]

    def test_buy_slippage_exact_equality_target_1_override(self):
        """
        Boundary Case: fill_price == target_1_override exactly ($101.60).
        If an override equal to fill price were allowed, it would produce a limit sell
        at market, executing instantly flat. The check must be strictly > fill_price.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_buy_slip_exact",
            symbol="AAPL",
            side="LONG",
            total_qty=100,
            entry_price=100.0,
            stop_price=98.0,
            target_1_override=101.60,
            target_2_override=103.60,
            timestamp=now,
        )

        fill_price = 101.60  # Exactly equal to target_1_override
        directive = bm.activate_bracket_on_fill("brk_buy_slip_exact", 100, fill_price, now)

        # Target 1 must NOT be 101.60; must re-anchor above 101.60
        assert bracket.target_1_price != 101.60
        expected_t1 = round(101.60 + 0.80 * (101.60 - 98.00), 2)  # 101.60 + 0.80 * 3.60 = 104.48
        assert bracket.target_1_price == expected_t1
        assert bracket.target_1_price > fill_price

        # Check directive orders
        t1_order = next(o for o in directive.orders_to_submit if o["order_id"] == bracket.target_1_order_id)
        assert t1_order["price"] == 104.48 > fill_price

    def test_buy_extreme_positive_slippage_exceeds_both_targets(self):
        """
        Extreme Case: fill_price jumps to $105.00, exceeding BOTH Target 1 ($101.60)
        and Target 2 ($103.60).
        Both targets must re-anchor dynamically above $105.00.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_buy_extreme",
            symbol="NVDA",
            side="LONG",
            total_qty=100,
            entry_price=100.0,
            stop_price=98.0,
            target_1_override=101.60,
            target_2_override=103.60,
            timestamp=now,
        )

        fill_price = 105.00
        directive = bm.activate_bracket_on_fill("brk_buy_extreme", 100, fill_price, now)

        # Recalculated R = 105.00 - 98.00 = 7.00
        expected_t1 = round(105.00 + 0.80 * 7.00, 2)  # 110.60
        expected_t2 = round(105.00 + 1.80 * 7.00, 2)  # 117.60

        assert bracket.target_1_price == expected_t1 == 110.60
        assert bracket.target_2_price == expected_t2 == 117.60
        assert bracket.target_2_price > bracket.target_1_price > fill_price

    def test_buy_valid_slippage_preserves_overrides(self):
        """
        Control Case: Normal slippage fills entry at $100.50 (< target_1_override $101.60).
        The original valid overrides should be retained.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_buy_normal",
            symbol="AAPL",
            side="LONG",
            total_qty=100,
            entry_price=100.0,
            stop_price=98.0,
            target_1_override=101.60,
            target_2_override=103.60,
            timestamp=now,
        )

        fill_price = 100.50
        bm.activate_bracket_on_fill("brk_buy_normal", 100, fill_price, now)

        assert bracket.target_1_price == 101.60
        assert bracket.target_2_price == 103.60
        assert bracket.target_2_price > bracket.target_1_price > fill_price

    def test_short_adverse_slippage_drops_below_target_1_override(self):
        """
        Mission Case 1B: SHORT order with adverse slippage where fill_price <= target_1_override.
        Planned: Entry $100.00, Stop $102.00 (R = $2.00).
        Target 1 planned at $98.40 (0.80R), Target 2 planned at $96.40 (1.80R).
        Realized fill price: $98.20 (fill_price < target_1_override).

        Requirement:
        - target_1_override ($98.40) is rejected because it is >= fill_price ($98.20).
        - target_1_price is dynamically re-anchored below $98.20.
        - Never create a marketable limit buy above or at purchase price.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_short_slip_1",
            symbol="TSLA",
            side="SHORT",
            total_qty=100,
            entry_price=100.0,
            stop_price=102.0,
            target_1_override=98.40,
            target_2_override=96.40,
            timestamp=now,
        )

        assert bracket.target_1_price == 98.40
        assert bracket.target_2_price == 96.40

        # Adverse slippage for short entry filling at 98.20
        fill_price = 98.20
        directive = bm.activate_bracket_on_fill("brk_short_slip_1", 100, fill_price, now)

        assert bracket.status == BracketStatus.ACTIVE
        assert bracket.entry_price == 98.20
        # Recalculated R-distance: abs(98.20 - 102.00) = 3.80
        assert bracket.r_distance == 3.80

        # Target 1 must be re-anchored strictly below fill price: 98.20 - 0.80 * 3.80 = 95.16
        expected_t1 = round(98.20 - 0.80 * 3.80, 2)
        assert expected_t1 == 95.16
        assert bracket.target_1_price == expected_t1
        assert bracket.target_1_price < fill_price, "Target 1 must be strictly below short entry fill price"

        # Target 2 must also be re-anchored because 96.40 > 95.16: 98.20 - 1.80 * 3.80 = 91.36
        expected_t2 = round(98.20 - 1.80 * 3.80, 2)
        assert expected_t2 == 91.36
        assert bracket.target_2_price == expected_t2
        assert bracket.target_2_price < bracket.target_1_price, "Target 2 must be strictly below Target 1"

        # Inspect submitted orders in directive
        t1_order = next(o for o in directive.orders_to_submit if o["order_id"] == bracket.target_1_order_id)
        assert t1_order["type"] == "LIMIT"
        assert t1_order["side"] == "BUY"
        assert t1_order["price"] == 95.16
        assert t1_order["price"] < fill_price, "Submitted limit buy must never be above or at purchase price"

        t2_order = next(o for o in directive.orders_to_submit if o["order_id"] == bracket.target_2_order_id)
        assert t2_order["price"] == 91.36
        assert t2_order["price"] < t1_order["price"]

    def test_short_slippage_exact_equality_target_1_override(self):
        """
        Boundary Case: SHORT fill_price == target_1_override exactly ($98.40).
        Must be re-anchored strictly below 98.40.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_short_slip_exact",
            symbol="TSLA",
            side="SHORT",
            total_qty=100,
            entry_price=100.0,
            stop_price=102.0,
            target_1_override=98.40,
            target_2_override=96.40,
            timestamp=now,
        )

        fill_price = 98.40
        directive = bm.activate_bracket_on_fill("brk_short_slip_exact", 100, fill_price, now)

        assert bracket.target_1_price != 98.40
        expected_t1 = round(98.40 - 0.80 * (102.00 - 98.40), 2)  # 98.40 - 0.80 * 3.60 = 95.52
        assert bracket.target_1_price == expected_t1 == 95.52
        assert bracket.target_1_price < fill_price

        t1_order = next(o for o in directive.orders_to_submit if o["order_id"] == bracket.target_1_order_id)
        assert t1_order["price"] == 95.52 < fill_price

    def test_short_extreme_adverse_slippage_drops_below_both_targets(self):
        """
        Extreme Case: SHORT fill_price drops to $95.00, below BOTH Target 1 ($98.40)
        and Target 2 ($96.40).
        Both targets must re-anchor dynamically below $95.00.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_short_extreme",
            symbol="TSLA",
            side="SHORT",
            total_qty=100,
            entry_price=100.0,
            stop_price=102.0,
            target_1_override=98.40,
            target_2_override=96.40,
            timestamp=now,
        )

        fill_price = 95.00
        directive = bm.activate_bracket_on_fill("brk_short_extreme", 100, fill_price, now)

        # Recalculated R = 102.00 - 95.00 = 7.00
        expected_t1 = round(95.00 - 0.80 * 7.00, 2)  # 89.40
        expected_t2 = round(95.00 - 1.80 * 7.00, 2)  # 82.40

        assert bracket.target_1_price == expected_t1 == 89.40
        assert bracket.target_2_price == expected_t2 == 82.40
        assert bracket.target_2_price < bracket.target_1_price < fill_price

    def test_short_valid_slippage_preserves_overrides(self):
        """
        Control Case: Short fill at $99.50 (> 98.40).
        Original valid overrides should be retained.
        """
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket(
            bracket_id="brk_short_normal",
            symbol="TSLA",
            side="SHORT",
            total_qty=100,
            entry_price=100.0,
            stop_price=102.0,
            target_1_override=98.40,
            target_2_override=96.40,
            timestamp=now,
        )

        fill_price = 99.50
        bm.activate_bracket_on_fill("brk_short_normal", 100, fill_price, now)

        assert bracket.target_1_price == 98.40
        assert bracket.target_2_price == 96.40
        assert bracket.target_2_price < bracket.target_1_price < fill_price

    def test_randomized_slippage_invariants(self):
        """
        Stress Generator: 100 randomized bracket scenarios with random slippage.
        Invariant: For LONG, Target 1 > fill and Target 2 > Target 1.
                   For SHORT, Target 1 < fill and Target 2 < Target 1.
        """
        random.seed(42)
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        for i in range(100):
            side = random.choice(["LONG", "SHORT"])
            base_price = round(random.uniform(20.0, 500.0), 2)
            stop_pct = random.uniform(0.005, 0.03)  # 0.5% to 3.0% stop

            if side == "LONG":
                stop_price = round(base_price * (1.0 - stop_pct), 2)
                t1_ov = round(base_price + 0.8 * (base_price - stop_price), 2)
                t2_ov = round(base_price + 1.8 * (base_price - stop_price), 2)
                # Slippage from -2% to +5% (sometimes exceeding targets)
                slip = random.uniform(-0.02, 0.05)
                fill_price = round(base_price * (1.0 + slip), 2)
                if fill_price <= stop_price:
                    fill_price = stop_price + 0.05
            else:
                stop_price = round(base_price * (1.0 + stop_pct), 2)
                t1_ov = round(base_price - 0.8 * (stop_price - base_price), 2)
                t2_ov = round(base_price - 1.8 * (stop_price - base_price), 2)
                # Slippage from -5% to +2%
                slip = random.uniform(-0.05, 0.02)
                fill_price = round(base_price * (1.0 + slip), 2)
                if fill_price >= stop_price:
                    fill_price = stop_price - 0.05

            bracket = bm.create_bracket(
                bracket_id=f"brk_rand_{i}",
                symbol="TEST",
                side=side,
                total_qty=random.randint(10, 500),
                entry_price=base_price,
                stop_price=stop_price,
                target_1_override=t1_ov,
                target_2_override=t2_ov,
                timestamp=now,
            )

            bm.activate_bracket_on_fill(f"brk_rand_{i}", bracket.total_qty, fill_price, now)

            if side == "LONG":
                assert bracket.target_1_price > fill_price, (
                    f"LONG invariant violated: T1 {bracket.target_1_price} <= fill {fill_price}"
                )
                assert bracket.target_2_price > bracket.target_1_price, (
                    f"LONG invariant violated: T2 {bracket.target_2_price} <= T1 {bracket.target_1_price}"
                )
            else:
                assert bracket.target_1_price < fill_price, (
                    f"SHORT invariant violated: T1 {bracket.target_1_price} >= fill {fill_price}"
                )
                assert bracket.target_2_price < bracket.target_1_price, (
                    f"SHORT invariant violated: T2 {bracket.target_2_price} >= T1 {bracket.target_1_price}"
                )


# ============================================================================
# 2. Target 1 Partial Fills and Stop-Loss Cancellations
# ============================================================================

class TestTarget1PartialFillsAndStopLoss:
    """Empirically challenge Target 1 partial fills and stop-loss cancellations."""

    def test_mandated_specification_100_shares_t1_50_partial_20_stop_80(self):
        """
        Mission Requirement 2 (Verbatim):
        - Simulate a 100-share position with Target 1 for 50 shares.
        - Simulate a partial fill of 20 shares on Target 1.
        - Verify target_1_filled is False and target_1_remaining_qty is 30.
        - Trigger a stop-loss fill for the remaining 80 shares.
        - Verify that ExecutionEngine.working_orders has ZERO remaining orders
          (the residual 30-share limit order was cancelled).
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        engine = ExecutionEngine(account=account)
        bm = DynamicBracketManager(default_target_1_r=0.80, default_target_2_r=1.80)
        now = datetime.now(timezone.utc)

        # 1. Create parent entry order in engine
        entry_order = engine.create_order(
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=100,
            limit_price=100.0,
        )
        engine.submit_order(entry_order.id)
        # Entry fills 100 shares @ $100.00
        engine._execute_fill(entry_order, 100, 100.0, 0.0, now)
        assert entry_order.status == OrderState.FILLED

        # 2. Create bracket with 100 shares, Target 1 for 50 shares
        bracket = bm.create_bracket(
            bracket_id=f"brk_{entry_order.id}",
            symbol="AAPL",
            side="LONG",
            total_qty=100,
            entry_price=100.0,
            stop_price=98.0,
            timestamp=now,
        )
        assert bracket.total_qty == 100
        assert bracket.target_1_qty == 50
        assert bracket.target_2_qty == 50

        # 3. Activate bracket on fill
        directive = bm.activate_bracket_on_fill(bracket.bracket_id, 100, 100.0, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, directive)

        # Verify ExecutionEngine has 3 working orders: Stop (100), T1 (50), T2 (50)
        assert len(engine.working_orders) == 3
        t1_order_id = bracket.target_1_order_id
        t2_order_id = bracket.target_2_order_id
        stop_order_id = bracket.stop_order_id

        assert t1_order_id in engine.working_orders
        assert t2_order_id in engine.working_orders
        assert stop_order_id in engine.working_orders

        t1_engine_order = engine.working_orders[t1_order_id]
        stop_engine_order = engine.working_orders[stop_order_id]
        assert t1_engine_order.remaining_qty == 50
        assert stop_engine_order.remaining_qty == 100

        # 4. Simulate a partial fill of 20 shares on Target 1 @ $101.60
        t1_fill = engine._execute_fill(t1_engine_order, 20, 101.60, 0.0, now)
        assert t1_engine_order.status == OrderState.PARTIALLY_FILLED
        assert t1_engine_order.remaining_qty == 30
        assert t1_order_id in engine.working_orders, "Partially filled order must remain in working_orders"

        # Reconcile child fill with bracket manager
        child_directive = bm.on_child_order_fill(t1_order_id, 101.60, 20, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, child_directive)

        # 5. Core Verification: target_1_filled is False and target_1_remaining_qty is 30
        assert bracket.target_1_filled is False, "target_1_filled must be False after partial fill"
        assert bracket.target_1_remaining_qty == 30, "target_1_remaining_qty property must be 30"
        assert bracket.target_1_qty == 30, "bracket.target_1_qty must be 30"
        assert bracket.remaining_qty == 80, "bracket.remaining_qty must be 80 (100 - 20)"
        assert bracket.status == BracketStatus.TARGET_1_HIT

        # Verify stop order was updated to 80 shares and breakeven stop in engine
        assert stop_engine_order.remaining_qty == 80
        assert stop_engine_order.stop_price == bracket.current_stop_price
        # Breakeven buffer on $100.00 entry is $0.05 -> stop = $100.05
        assert stop_engine_order.stop_price == 100.05

        # 6. Trigger a stop-loss fill for the remaining 80 shares
        stop_fill = engine._execute_fill(stop_engine_order, 80, 100.05, 0.0, now)
        assert stop_engine_order.status == OrderState.FILLED
        assert stop_order_id not in engine.working_orders

        # Reconcile stop fill with bracket manager
        stop_directive = bm.on_child_order_fill(stop_order_id, 100.05, 80, now)
        assert stop_directive.bracket_status == BracketStatus.COMPLETED_STOP
        assert t1_order_id in stop_directive.orders_to_cancel, (
            "Directive MUST contain residual Target 1 order in orders_to_cancel"
        )
        assert t2_order_id in stop_directive.orders_to_cancel, (
            "Directive MUST contain Target 2 order in orders_to_cancel"
        )

        # Apply directive to engine
        apply_bracket_directive(engine, bm, bracket.bracket_id, stop_directive)

        # 7. Final Verification: ExecutionEngine.working_orders has ZERO remaining orders
        assert len(engine.working_orders) == 0, (
            f"ExecutionEngine.working_orders must be completely empty (got {list(engine.working_orders.keys())})"
        )
        assert t1_engine_order.status == OrderState.CANCELLED, "Residual 30-share Target 1 order must be CANCELLED"
        assert engine.orders[t2_order_id].status == OrderState.CANCELLED, "Target 2 order must be CANCELLED"

        # Verify bracket manager cleanup
        assert bracket.symbol not in bm.symbol_to_bracket
        assert t1_order_id not in bm.order_to_bracket
        assert t2_order_id not in bm.order_to_bracket
        assert stop_order_id not in bm.order_to_bracket

    def test_multiple_sequential_partial_fills_on_target_1(self):
        """
        Adversarial Case: Target 1 fills across 3 separate partial fills:
        Fill 1: 15 shares -> T1 remaining: 35, target_1_filled: False. Stop: 85.
        Fill 2: 15 shares -> T1 remaining: 20, target_1_filled: False. Stop: 70.
        Fill 3: 20 shares -> T1 remaining: 0, target_1_filled: True. Stop: 50.
        Then price reverses and Stop fills 50 shares.
        Verify working_orders is 0.
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        engine = ExecutionEngine(account=account)
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        # Create & activate
        bracket = bm.create_bracket("brk_multi_part", "NVDA", "LONG", 100, 100.0, 98.0, timestamp=now)
        directive = bm.activate_bracket_on_fill(bracket.bracket_id, 100, 100.0, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, directive)

        t1_id = bracket.target_1_order_id
        t2_id = bracket.target_2_order_id
        stop_id = bracket.stop_order_id
        t1_order = engine.working_orders[t1_id]
        stop_order = engine.working_orders[stop_id]

        # Partial fill 1: 15 shares
        engine._execute_fill(t1_order, 15, 101.60, 0.0, now)
        d1 = bm.on_child_order_fill(t1_id, 101.60, 15, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, d1)

        assert bracket.target_1_filled is False
        assert bracket.target_1_remaining_qty == 35
        assert stop_order.remaining_qty == 85

        # Partial fill 2: 15 shares
        engine._execute_fill(t1_order, 15, 101.60, 0.0, now)
        d2 = bm.on_child_order_fill(t1_id, 101.60, 15, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, d2)

        assert bracket.target_1_filled is False
        assert bracket.target_1_remaining_qty == 20
        assert stop_order.remaining_qty == 70

        # Partial fill 3: 20 shares (completing Target 1)
        engine._execute_fill(t1_order, 20, 101.60, 0.0, now)
        d3 = bm.on_child_order_fill(t1_id, 101.60, 20, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, d3)

        assert bracket.target_1_filled is True
        assert bracket.target_1_remaining_qty == 0
        assert t1_order.status == OrderState.FILLED
        assert stop_order.remaining_qty == 50
        assert len(engine.working_orders) == 2  # Stop (50) and Target 2 (50)

        # Now stop-loss hit for remaining 50 shares
        engine._execute_fill(stop_order, 50, bracket.current_stop_price, 0.0, now)
        stop_d = bm.on_child_order_fill(stop_id, bracket.current_stop_price, 50, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, stop_d)

        assert len(engine.working_orders) == 0
        assert engine.orders[t2_id].status == OrderState.CANCELLED
        assert bracket.status == BracketStatus.COMPLETED_STOP

    def test_dual_target_partial_fills_then_stop_loss(self):
        """
        Adversarial Case: Both Target 1 and Target 2 experience partial fills before stop-loss.
        - 100 shares position -> T1: 50, T2: 50, Stop: 100.
        - T1 fills 25 shares (25 remaining).
        - T2 fills 20 shares (30 remaining).
        - Stop loss fills remaining 55 shares.
        - Verify BOTH residual T1 (25 shares) and residual T2 (30 shares) are cancelled.
        - Verify working_orders is 0.
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        engine = ExecutionEngine(account=account)
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket("brk_dual_part", "MSFT", "LONG", 100, 100.0, 98.0, timestamp=now)
        directive = bm.activate_bracket_on_fill(bracket.bracket_id, 100, 100.0, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, directive)

        t1_id = bracket.target_1_order_id
        t2_id = bracket.target_2_order_id
        stop_id = bracket.stop_order_id
        t1_order = engine.working_orders[t1_id]
        t2_order = engine.working_orders[t2_id]
        stop_order = engine.working_orders[stop_id]

        # T1 fills 25 shares
        engine._execute_fill(t1_order, 25, 101.60, 0.0, now)
        d1 = bm.on_child_order_fill(t1_id, 101.60, 25, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, d1)

        assert bracket.target_1_remaining_qty == 25
        assert bracket.remaining_qty == 75

        # T2 fills 20 shares
        engine._execute_fill(t2_order, 20, 103.60, 0.0, now)
        d2 = bm.on_child_order_fill(t2_id, 103.60, 20, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, d2)

        assert bracket.target_2_remaining_qty == 30
        assert bracket.remaining_qty == 55
        assert stop_order.remaining_qty == 55

        # Stop-loss fills remaining 55 shares
        engine._execute_fill(stop_order, 55, bracket.current_stop_price, 0.0, now)
        stop_d = bm.on_child_order_fill(stop_id, bracket.current_stop_price, 55, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, stop_d)

        assert len(engine.working_orders) == 0
        assert t1_order.status == OrderState.CANCELLED
        assert t2_order.status == OrderState.CANCELLED
        assert bracket.status == BracketStatus.COMPLETED_STOP

    def test_short_position_partial_fill_and_stop_loss(self):
        """
        Adversarial Case: SHORT position with partial fill on Target 1 then stop-loss.
        - Short 100 shares @ $100.00, stop at $102.00.
        - T1 fills 20 shares @ $98.40.
        - Verify target_1_filled is False and target_1_remaining_qty is 30.
        - Stop fills remaining 80 shares.
        - Verify working_orders has 0 remaining orders.
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        engine = ExecutionEngine(account=account)
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket("brk_short_part", "TSLA", "SHORT", 100, 100.0, 102.0, timestamp=now)
        directive = bm.activate_bracket_on_fill(bracket.bracket_id, 100, 100.0, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, directive)

        t1_id = bracket.target_1_order_id
        t2_id = bracket.target_2_order_id
        stop_id = bracket.stop_order_id
        t1_order = engine.working_orders[t1_id]
        stop_order = engine.working_orders[stop_id]

        # T1 fills 20 shares
        engine._execute_fill(t1_order, 20, 98.40, 0.0, now)
        d1 = bm.on_child_order_fill(t1_id, 98.40, 20, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, d1)

        assert bracket.target_1_filled is False
        assert bracket.target_1_remaining_qty == 30
        assert bracket.remaining_qty == 80
        assert stop_order.remaining_qty == 80
        # Short breakeven stop: 100.0 - 0.05 = 99.95
        assert stop_order.stop_price == 99.95

        # Stop-loss fills 80 shares
        engine._execute_fill(stop_order, 80, 99.95, 0.0, now)
        stop_d = bm.on_child_order_fill(stop_id, 99.95, 80, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, stop_d)

        assert len(engine.working_orders) == 0
        assert t1_order.status == OrderState.CANCELLED
        assert engine.orders[t2_id].status == OrderState.CANCELLED
        assert bracket.status == BracketStatus.COMPLETED_STOP

    def test_partial_fill_on_stop_loss_order_scales_targets(self):
        """
        Stress Case: Stop-loss order itself partially fills (e.g. 40 of 100 shares).
        Remaining position is 60 shares.
        Unfilled targets (total 100) must scale down to <= 60 so combined target size
        never exceeds remaining position.
        Then remaining 60 shares of stop-loss fill -> 0 working orders.
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        engine = ExecutionEngine(account=account)
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        bracket = bm.create_bracket("brk_partial_stop", "AAPL", "LONG", 100, 100.0, 98.0, timestamp=now)
        directive = bm.activate_bracket_on_fill(bracket.bracket_id, 100, 100.0, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, directive)

        t1_id = bracket.target_1_order_id
        t2_id = bracket.target_2_order_id
        stop_id = bracket.stop_order_id
        stop_order = engine.working_orders[stop_id]
        t1_order = engine.working_orders[t1_id]
        t2_order = engine.working_orders[t2_id]

        # Stop partially fills 40 shares
        engine._execute_fill(stop_order, 40, 98.0, 0.0, now)
        stop_d1 = bm.on_child_order_fill(stop_id, 98.0, 40, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, stop_d1)

        assert bracket.remaining_qty == 60
        # Scaled targets: t1_new = int(50 * 60 / 100) = 30, t2_new = 60 - 30 = 30
        assert bracket.target_1_qty == 30
        assert bracket.target_2_qty == 30
        assert t1_order.remaining_qty == 30
        assert t2_order.remaining_qty == 30

        # Remaining 60 shares fill on stop
        engine._execute_fill(stop_order, 60, 98.0, 0.0, now)
        stop_d2 = bm.on_child_order_fill(stop_id, 98.0, 60, now)
        apply_bracket_directive(engine, bm, bracket.bracket_id, stop_d2)

        assert len(engine.working_orders) == 0
        assert t1_order.status == OrderState.CANCELLED
        assert t2_order.status == OrderState.CANCELLED
        assert bracket.status == BracketStatus.COMPLETED_STOP


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
