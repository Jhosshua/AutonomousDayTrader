"""backend/app/core/bracket.py
Dynamic Bracket Orders, Multi-Tier Targets (1.5R, 2.5R), Breakeven Ratchets, and Monotonic Trailing Stops.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class BracketStatus(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"          # Entry order submitted, awaiting fill
    ACTIVE = "ACTIVE"                        # Position open, OCO bracket orders live
    TARGET_1_HIT = "TARGET_1_HIT"            # Scaled out 50%, stop ratcheted to breakeven
    COMPLETED_PROFIT = "COMPLETED_PROFIT"    # Both targets hit or trailed to completion
    COMPLETED_STOP = "COMPLETED_STOP"        # Stop loss executed
    COMPLETED_FLATTEN = "COMPLETED_FLATTEN"  # Flattened manually or by EOD protocol
    CANCELLED = "CANCELLED"                  # Entry cancelled before fill


class BracketChildType(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT_1 = "TAKE_PROFIT_1"
    TAKE_PROFIT_2 = "TAKE_PROFIT_2"


class BracketOrder(BaseModel):
    bracket_id: str
    symbol: str
    side: str                                # "LONG" or "SHORT"
    strategy_id: str
    total_qty: int
    remaining_qty: int
    entry_price: float
    initial_stop_price: float
    current_stop_price: float
    target_1_price: float
    target_1_qty: int
    target_1_filled: bool = False
    target_2_price: float
    target_2_qty: int
    target_2_filled: bool = False
    target_1_override: Optional[float] = None
    target_2_override: Optional[float] = None
    r_distance: float
    status: BracketStatus = BracketStatus.PENDING_ENTRY
    use_trailing_target_2: bool = True
    trail_atr_multiplier: float = 1.5
    peak_price_since_entry: float
    stop_order_id: Optional[str] = None
    target_1_order_id: Optional[str] = None
    target_2_order_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BracketUpdateDirective(BaseModel):
    action: str  # "SUBMIT_ORDERS", "CANCEL_ORDER", "MODIFY_ORDER", "NO_ACTION"
    orders_to_cancel: List[str] = Field(default_factory=list)
    orders_to_submit: List[Dict[str, Any]] = Field(default_factory=list)
    orders_to_modify: List[Dict[str, Any]] = Field(default_factory=list)
    bracket_status: BracketStatus


class DynamicBracketManager:
    """
    Manages dynamic multi-tier profit targets and OCO trailing bracket orders.
    Enforces Target 1 (1.5R, 50% scale-out), breakeven stop ratchet, Target 2 (2.5R or ATR trail),
    and OCO order size synchronization.
    """

    def __init__(self, breakeven_buffer: float = 0.02) -> None:
        self.breakeven_buffer: float = breakeven_buffer
        self.brackets: Dict[str, BracketOrder] = {}
        self.symbol_to_bracket: Dict[str, str] = {}
        self.order_to_bracket: Dict[str, Tuple[str, BracketChildType]] = {}

    def create_bracket(
        self,
        bracket_id: str,
        symbol: str,
        side: str,  # "LONG" or "SHORT"
        total_qty: int,
        entry_price: float,
        stop_price: float,
        strategy_id: str = "MANUAL",
        use_trailing_target_2: bool = True,
        trail_atr_multiplier: float = 1.5,
        target_1_override: Optional[float] = None,
        target_2_override: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> BracketOrder:
        """
        Create and compute price levels for a dynamic multi-tier bracket.
        Target 1: 1.5R (50% scale-out, rounded down)
        Target 2: 2.5R (remaining 50%)
        """
        side_norm = side.upper()
        if side_norm not in ("LONG", "SHORT"):
            raise ValueError(f"Invalid side: {side}")
        if total_qty <= 0:
            raise ValueError(f"Bracket quantity must be positive, got {total_qty}")
        if entry_price <= 0 or stop_price <= 0:
            raise ValueError("Bracket prices must be positive")
        if (side_norm == "LONG" and stop_price >= entry_price) or (
            side_norm == "SHORT" and stop_price <= entry_price
        ):
            raise ValueError("Stop price must be below a long entry and above a short entry")

        r_dist = abs(entry_price - stop_price)
        s = 1.0 if side_norm == "LONG" else -1.0

        t1_price = round(target_1_override, 2) if target_1_override is not None else round(entry_price + (s * 1.5 * r_dist), 2)
        t2_price = round(target_2_override, 2) if target_2_override is not None else round(entry_price + (s * 2.5 * r_dist), 2)

        q1 = max(1, total_qty // 2) if total_qty > 1 else 1
        q2 = total_qty - q1 if total_qty > 1 else 0

        now = timestamp or datetime.now(timezone.utc)
        bracket = BracketOrder(
            bracket_id=bracket_id,
            symbol=symbol.upper(),
            side=side_norm,
            strategy_id=strategy_id,
            total_qty=total_qty,
            remaining_qty=total_qty,
            entry_price=round(entry_price, 4),
            initial_stop_price=round(stop_price, 4),
            current_stop_price=round(stop_price, 4),
            target_1_price=t1_price,
            target_1_qty=q1,
            target_2_price=t2_price,
            target_2_qty=q2,
            target_1_override=target_1_override,
            target_2_override=target_2_override,
            r_distance=round(r_dist, 4),
            status=BracketStatus.PENDING_ENTRY,
            use_trailing_target_2=use_trailing_target_2,
            trail_atr_multiplier=trail_atr_multiplier,
            peak_price_since_entry=entry_price,
            stop_order_id=f"stop_{bracket_id}",
            target_1_order_id=f"t1_{bracket_id}",
            target_2_order_id=f"t2_{bracket_id}" if q2 > 0 else None,
            created_at=now,
            updated_at=now,
        )

        self.brackets[bracket_id] = bracket
        self.symbol_to_bracket[symbol.upper()] = bracket_id
        if bracket.stop_order_id:
            self.order_to_bracket[bracket.stop_order_id] = (bracket_id, BracketChildType.STOP_LOSS)
        if bracket.target_1_order_id:
            self.order_to_bracket[bracket.target_1_order_id] = (bracket_id, BracketChildType.TAKE_PROFIT_1)
        if bracket.target_2_order_id:
            self.order_to_bracket[bracket.target_2_order_id] = (bracket_id, BracketChildType.TAKE_PROFIT_2)

        return bracket

    def activate_bracket_on_fill(
        self,
        bracket_id: str,
        filled_qty: int,
        fill_price: float,
        timestamp: datetime,
    ) -> BracketUpdateDirective:
        """
        Invoked when the parent entry order fills.
        Transitions bracket status to ACTIVE.
        """
        bracket = self.brackets.get(bracket_id)
        if not bracket:
            raise KeyError(f"Bracket {bracket_id} not found")

        if filled_qty <= 0:
            raise ValueError("Cannot activate a bracket without a positive entry fill")

        # Protect only the shares that actually filled. The previous implementation
        # exposed the full requested size even when the simulator partially filled an
        # entry, which could create an unprotected/over-sized exit bracket.
        bracket.total_qty = min(filled_qty, bracket.total_qty)
        bracket.remaining_qty = bracket.total_qty
        bracket.target_1_qty = max(1, bracket.total_qty // 2) if bracket.total_qty > 1 else 1
        bracket.target_2_qty = bracket.total_qty - bracket.target_1_qty if bracket.total_qty > 1 else 0
        bracket.target_2_order_id = f"t2_{bracket_id}" if bracket.target_2_qty > 0 else None
        bracket.r_distance = round(abs(fill_price - bracket.initial_stop_price), 4)
        bracket.entry_price = round(fill_price, 4)
        direction = 1.0 if bracket.side == "LONG" else -1.0
        bracket.target_1_price = (
            round(bracket.target_1_override, 2)
            if bracket.target_1_override is not None
            else round(bracket.entry_price + direction * 1.5 * bracket.r_distance, 2)
        )
        bracket.target_2_price = (
            round(bracket.target_2_override, 2)
            if bracket.target_2_override is not None
            else round(bracket.entry_price + direction * 2.5 * bracket.r_distance, 2)
        )
        bracket.status = BracketStatus.ACTIVE
        bracket.peak_price_since_entry = fill_price
        bracket.updated_at = timestamp

        orders_to_submit = [
            {
                "order_id": bracket.stop_order_id,
                "type": "STOP",
                "price": bracket.current_stop_price,
                "qty": bracket.remaining_qty,
                "side": "SELL" if bracket.side == "LONG" else "BUY",
            },
            {
                "order_id": bracket.target_1_order_id,
                "type": "LIMIT",
                "price": bracket.target_1_price,
                "qty": bracket.target_1_qty,
                "side": "SELL" if bracket.side == "LONG" else "BUY",
            },
        ]
        if bracket.target_2_order_id and bracket.target_2_qty > 0:
            orders_to_submit.append({
                "order_id": bracket.target_2_order_id,
                "type": "LIMIT",
                "price": bracket.target_2_price,
                "qty": bracket.target_2_qty,
                "side": "SELL" if bracket.side == "LONG" else "BUY",
            })

        return BracketUpdateDirective(
            action="SUBMIT_ORDERS",
            orders_to_submit=orders_to_submit,
            bracket_status=BracketStatus.ACTIVE,
        )

    def on_child_order_fill(
        self,
        order_id: str,
        fill_price: float,
        filled_qty: int,
        timestamp: datetime,
    ) -> BracketUpdateDirective:
        """
        Handles execution of child bracket orders (OCO synchronization).
        """
        mapping = self.order_to_bracket.get(order_id)
        if not mapping:
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=BracketStatus.ACTIVE)

        bracket_id, child_type = mapping
        bracket = self.brackets.get(bracket_id)
        if not bracket:
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=BracketStatus.ACTIVE)

        bracket.updated_at = timestamp

        # 1. Stop-Loss Triggered
        if child_type == BracketChildType.STOP_LOSS:
            bracket.remaining_qty = max(0, bracket.remaining_qty - filled_qty)
            if bracket.remaining_qty > 0:
                # Partial stop fill: the position is still open, so keep the
                # bracket alive. Scale the unfilled profit targets down so
                # their combined qty equals the remaining position; both
                # targets can fill within a single bar before OCO cancel
                # reconciliation, so their total must not exceed it.
                orders_to_modify: List[Dict[str, Any]] = []
                orders_to_cancel: List[str] = []
                t1_open = bracket.target_1_qty if (bracket.target_1_order_id and not bracket.target_1_filled) else 0
                t2_open = bracket.target_2_qty if (bracket.target_2_order_id and not bracket.target_2_filled) else 0
                open_target_qty = t1_open + t2_open
                if open_target_qty > bracket.remaining_qty:
                    t1_new = int(t1_open * bracket.remaining_qty / open_target_qty)
                    t2_new = bracket.remaining_qty - t1_new
                    if t1_open:
                        if t1_new > 0:
                            orders_to_modify.append({"order_id": bracket.target_1_order_id, "new_qty": t1_new})
                        else:
                            orders_to_cancel.append(bracket.target_1_order_id)
                        bracket.target_1_qty = t1_new
                    if t2_open:
                        if t2_new > 0:
                            orders_to_modify.append({"order_id": bracket.target_2_order_id, "new_qty": t2_new})
                        else:
                            orders_to_cancel.append(bracket.target_2_order_id)
                        bracket.target_2_qty = t2_new
                return BracketUpdateDirective(
                    action="MODIFY_ORDER",
                    orders_to_cancel=orders_to_cancel,
                    orders_to_modify=orders_to_modify,
                    bracket_status=bracket.status,
                )
            bracket.status = BracketStatus.COMPLETED_STOP
            orders_to_cancel = []
            if bracket.target_1_order_id and not bracket.target_1_filled:
                orders_to_cancel.append(bracket.target_1_order_id)
            if bracket.target_2_order_id and not bracket.target_2_filled:
                orders_to_cancel.append(bracket.target_2_order_id)

            self.symbol_to_bracket.pop(bracket.symbol, None)
            return BracketUpdateDirective(
                action="CANCEL_ORDER",
                orders_to_cancel=orders_to_cancel,
                bracket_status=BracketStatus.COMPLETED_STOP,
            )

        # 2. Target 1 Filled
        elif child_type == BracketChildType.TAKE_PROFIT_1:
            bracket.target_1_filled = True
            bracket.remaining_qty -= filled_qty

            if bracket.remaining_qty <= 0:
                bracket.status = BracketStatus.COMPLETED_PROFIT
                self.symbol_to_bracket.pop(bracket.symbol, None)
                return BracketUpdateDirective(
                    action="CANCEL_ORDER",
                    orders_to_cancel=[bracket.stop_order_id] if bracket.stop_order_id else [],
                    bracket_status=BracketStatus.COMPLETED_PROFIT,
                )

            bracket.status = BracketStatus.TARGET_1_HIT
            # Ratchet stop to breakeven + buffer
            s = 1.0 if bracket.side == "LONG" else -1.0
            new_stop = round(bracket.entry_price + (s * self.breakeven_buffer), 4)

            # Invariant: breakeven stop must be strictly better than initial stop
            if bracket.side == "LONG":
                bracket.current_stop_price = max(bracket.current_stop_price, new_stop)
            else:
                bracket.current_stop_price = min(bracket.current_stop_price, new_stop)

            # Modify working stop order quantity and price
            return BracketUpdateDirective(
                action="MODIFY_ORDER",
                orders_to_modify=[{
                    "order_id": bracket.stop_order_id,
                    "new_qty": bracket.remaining_qty,
                    "new_stop_price": bracket.current_stop_price,
                }],
                bracket_status=BracketStatus.TARGET_1_HIT,
            )

        # 3. Target 2 Filled
        elif child_type == BracketChildType.TAKE_PROFIT_2:
            bracket.target_2_filled = True
            bracket.remaining_qty -= filled_qty
            bracket.status = BracketStatus.COMPLETED_PROFIT
            self.symbol_to_bracket.pop(bracket.symbol, None)

            return BracketUpdateDirective(
                action="CANCEL_ORDER",
                orders_to_cancel=[bracket.stop_order_id] if bracket.stop_order_id else [],
                bracket_status=BracketStatus.COMPLETED_PROFIT,
            )

        return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)

    def update_trailing_stop(
        self,
        symbol: str,
        current_bar_high: float,
        current_bar_low: float,
        current_atr: float,
        timestamp: datetime,
    ) -> Optional[BracketUpdateDirective]:
        """
        Update trailing stop level on 1-minute bars. Strictly monotonic (never loosens).
        """
        symbol_upper = symbol.upper()
        bracket_id = self.symbol_to_bracket.get(symbol_upper)
        if not bracket_id:
            return None

        bracket = self.brackets.get(bracket_id)
        if not bracket or bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT):
            return None

        if not bracket.use_trailing_target_2:
            return None

        trail_dist = bracket.trail_atr_multiplier * max(0.01, current_atr)

        if bracket.side == "LONG":
            bracket.peak_price_since_entry = max(bracket.peak_price_since_entry, current_bar_high)
            potential_stop = round(bracket.peak_price_since_entry - trail_dist, 4)
            if potential_stop > bracket.current_stop_price:
                bracket.current_stop_price = potential_stop
                bracket.updated_at = timestamp
                return BracketUpdateDirective(
                    action="MODIFY_ORDER",
                    orders_to_modify=[{
                        "order_id": bracket.stop_order_id,
                        "new_stop_price": bracket.current_stop_price,
                        "new_qty": bracket.remaining_qty,
                    }],
                    bracket_status=bracket.status,
                )
        else:  # SHORT
            bracket.peak_price_since_entry = min(bracket.peak_price_since_entry, current_bar_low)
            potential_stop = round(bracket.peak_price_since_entry + trail_dist, 4)
            if potential_stop < bracket.current_stop_price:
                bracket.current_stop_price = potential_stop
                bracket.updated_at = timestamp
                return BracketUpdateDirective(
                    action="MODIFY_ORDER",
                    orders_to_modify=[{
                        "order_id": bracket.stop_order_id,
                        "new_stop_price": bracket.current_stop_price,
                        "new_qty": bracket.remaining_qty,
                    }],
                    bracket_status=bracket.status,
                )

        return None

    def manual_tighten_stop(
        self,
        symbol: str,
        new_stop_price: float,
    ) -> BracketUpdateDirective:
        """Handle manual UI override to tighten stop."""
        symbol_upper = symbol.upper()
        bracket_id = self.symbol_to_bracket.get(symbol_upper)
        if not bracket_id:
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=BracketStatus.COMPLETED_FLATTEN)

        bracket = self.brackets[bracket_id]
        if bracket.side == "LONG":
            if new_stop_price > bracket.current_stop_price:
                bracket.current_stop_price = new_stop_price
        else:
            if new_stop_price < bracket.current_stop_price:
                bracket.current_stop_price = new_stop_price

        return BracketUpdateDirective(
            action="MODIFY_ORDER",
            orders_to_modify=[{
                "order_id": bracket.stop_order_id,
                "new_stop_price": bracket.current_stop_price,
                "new_qty": bracket.remaining_qty,
            }],
            bracket_status=bracket.status,
        )

    def cancel_pending_entry_bracket(self, symbol: str) -> bool:
        """Cancel a PENDING_ENTRY bracket whose entry order terminated without a fill."""
        symbol_upper = symbol.upper()
        bracket_id = self.symbol_to_bracket.get(symbol_upper)
        if not bracket_id:
            return False
        bracket = self.brackets.get(bracket_id)
        if not bracket or bracket.status != BracketStatus.PENDING_ENTRY:
            return False
        bracket.status = BracketStatus.CANCELLED
        bracket.updated_at = datetime.now(timezone.utc)
        self.symbol_to_bracket.pop(symbol_upper, None)
        for oid in (bracket.stop_order_id, bracket.target_1_order_id, bracket.target_2_order_id):
            if oid:
                self.order_to_bracket.pop(oid, None)
        return True

    def cancel_bracket_for_flattening(
        self,
        symbol: str,
        reason: str = "EOD_FLATTEN",
    ) -> BracketUpdateDirective:
        """Cancel all active child bracket orders for a symbol in preparation for market flattening."""
        symbol_upper = symbol.upper()
        bracket_id = self.symbol_to_bracket.pop(symbol_upper, None)
        if not bracket_id:
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=BracketStatus.COMPLETED_FLATTEN)

        bracket = self.brackets.get(bracket_id)
        if not bracket:
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=BracketStatus.COMPLETED_FLATTEN)

        bracket.status = BracketStatus.COMPLETED_FLATTEN
        orders_to_cancel = []
        if bracket.stop_order_id:
            orders_to_cancel.append(bracket.stop_order_id)
        if bracket.target_1_order_id:
            orders_to_cancel.append(bracket.target_1_order_id)
        if bracket.target_2_order_id:
            orders_to_cancel.append(bracket.target_2_order_id)

        return BracketUpdateDirective(
            action="CANCEL_ORDER",
            orders_to_cancel=orders_to_cancel,
            bracket_status=BracketStatus.COMPLETED_FLATTEN,
        )
