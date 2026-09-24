"""backend/app/core/account.py
Paper Trading Account State Machine ($50,000 initial balance, FINRA 4:1 Day Trading Buying Power).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

from backend.app.models.events import AccountState, PositionState


class TradingArm(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MARGIN_CALL = "MARGIN_CALL"
    CIRCUIT_HALTED = "CIRCUIT_HALTED"
    EOD_FLAT = "EOD_FLAT"
    CLOSED = "CLOSED"


@dataclass
class Position:
    """Represents an active multi-lot position in a specific symbol."""
    symbol: str
    side: PositionSide
    shares: int
    avg_entry_price: float
    market_price: float
    market_value: float = field(init=False)
    cost_basis: float = field(init=False)
    unrealized_pnl: float = field(init=False)
    unrealized_pnl_pct: float = field(init=False)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    arm: TradingArm = TradingArm.INTRADAY
    strategy_id: str = "MANUAL"
    holding_days: int = 0
    stop_loss_price: Optional[float] = None
    entry_atr: Optional[float] = None
    entry_date: Optional[date] = None

    def __post_init__(self) -> None:
        if isinstance(self.arm, str) and not isinstance(self.arm, TradingArm):
            self.arm = TradingArm(self.arm)
        self.recalculate()

    def recalculate(self) -> None:
        """Update market value and unrealized PnL based on market price."""
        self.cost_basis = round(self.shares * self.avg_entry_price, 2)
        if self.side == PositionSide.LONG:
            self.market_value = round(self.shares * self.market_price, 2)
            self.unrealized_pnl = round(self.shares * (self.market_price - self.avg_entry_price), 2)
        else:
            # Short liability: market value is negative
            self.market_value = round(-self.shares * self.market_price, 2)
            self.unrealized_pnl = round(self.shares * (self.avg_entry_price - self.market_price), 2)

        if self.cost_basis > 0:
            self.unrealized_pnl_pct = round(self.unrealized_pnl / self.cost_basis, 4)
        else:
            self.unrealized_pnl_pct = 0.0
        self.updated_at = datetime.now(timezone.utc)

    def update_market_price(self, new_price: float) -> None:
        """Mark position to new market price."""
        if new_price <= 0:
            raise ValueError(f"Invalid market price: {new_price}")
        self.market_price = new_price
        self.recalculate()

    def to_state(self) -> PositionState:
        return PositionState(
            symbol=self.symbol,
            side=self.side.value if hasattr(self.side, "value") else str(self.side),
            shares=self.shares,
            avg_entry_price=self.avg_entry_price,
            market_price=self.market_price,
            market_value=self.market_value,
            cost_basis=self.cost_basis,
            unrealized_pnl=self.unrealized_pnl,
            unrealized_pnl_pct=self.unrealized_pnl_pct,
            realized_pnl=self.realized_pnl,
            fees_paid=self.fees_paid,
            opened_at=self.opened_at,
            updated_at=self.updated_at,
            arm=self.arm.value if hasattr(self.arm, "value") else str(self.arm),
            strategy_id=self.strategy_id,
            holding_days=self.holding_days,
            stop_loss_price=self.stop_loss_price,
            entry_atr=self.entry_atr,
            entry_date=self.entry_date.isoformat() if hasattr(self.entry_date, "isoformat") else (str(self.entry_date) if self.entry_date else None),
        )


class PaperTradingAccount:
    """
    Self-contained, deterministic Paper Trading Account State Machine.
    Initial balance: $50,000.00.
    FINRA Rule 4210 Day Trading Buying Power: 4:1 ($200,000 max intraday leverage).
    """

    INITIAL_CAPITAL: float = 50000.00
    PDT_MINIMUM_EQUITY: float = 25000.00
    MAX_POSITION_ALLOCATION_PCT: float = 0.25  # 25% max buying power ($50k) per symbol

    def __init__(
        self,
        initial_cash: float = INITIAL_CAPITAL,
        leverage: float = 4.0,
        max_position_notional: Optional[float] = None,
    ) -> None:
        self.leverage: float = leverage
        self.max_position_notional: Optional[float] = max_position_notional
        self.initial_balance: float = initial_cash
        self.daily_starting_equity: float = initial_cash
        self.cash: float = initial_cash
        self.equity: float = initial_cash
        self.status: AccountStatus = AccountStatus.ACTIVE
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.fees_paid: float = 0.0
        self.maintenance_margin: float = 0.0
        self.margin_excess: float = initial_cash
        self.buying_power: float = round(initial_cash * self.leverage, 2)
        self.daily_drawdown_dollars: float = 0.0
        self.daily_drawdown_pct: float = 0.0
        self.positions: Dict[str, Position] = {}
        self.cash_transactions: List[dict] = []
        self._recompute_account_state()

    def get_position(self, symbol: str) -> Optional[Position]:
        """Return active position for symbol or None."""
        return self.positions.get(symbol.upper())

    def update_market_price(self, symbol: str, price: float) -> None:
        """Mark-to-market position on new bar or quote."""
        pos = self.positions.get(symbol.upper())
        if pos is not None:
            pos.update_market_price(price)
            self._recompute_account_state()

    def can_afford(self, symbol: str, side: str, qty: int, est_price: float) -> Tuple[bool, str]:
        """
        Validate whether account has sufficient Day Trading Buying Power (DTBP)
        and complies with FINRA Rule 4210 and per-position concentration limits.
        Permits position-reducing and liquidation orders even when CIRCUIT_HALTED.
        """
        symbol = symbol.upper()
        existing_pos = self.positions.get(symbol)
        order_value = qty * est_price

        # Check per-position allocation ceiling ($50,000 max = 25% of $200k initial BP)
        max_alloc = self.initial_balance * self.leverage * self.MAX_POSITION_ALLOCATION_PCT
        if self.max_position_notional is not None:
            max_alloc = min(max_alloc, self.max_position_notional)
        current_alloc = abs(existing_pos.market_value) if existing_pos else 0.0

        # Determine if order is position-reducing
        is_reducing = False
        if existing_pos is not None:
            if existing_pos.side == PositionSide.LONG and side.upper() == "SELL":
                is_reducing = True
            elif existing_pos.side == PositionSide.SHORT and side.upper() == "BUY":
                is_reducing = True

        # Account status check: allow closing/reducing orders under CIRCUIT_HALTED or EOD_FLAT
        if self.status not in (AccountStatus.ACTIVE, AccountStatus.MARGIN_CALL):
            if self.status in (AccountStatus.CIRCUIT_HALTED, AccountStatus.EOD_FLAT):
                if not is_reducing:
                    return False, f"Account is not ACTIVE (current status: {self.status.value})"
                if qty > existing_pos.shares:
                    return False, f"Cannot increase or flip position while {self.status.value}: order quantity exceeds existing position"
                return True, "Approved"
            else:
                return False, f"Account is not ACTIVE (current status: {self.status.value})"

        # Position flip checks when order opposes existing position
        if existing_pos is not None:
            if existing_pos.side == PositionSide.LONG and side.upper() == "SELL":
                if qty > existing_pos.shares:
                    flip_qty = qty - existing_pos.shares
                    flip_val = flip_qty * est_price
                    if flip_val > max_alloc + 0.01:
                        return False, f"Order exceeds per-position concentration cap of ${max_alloc:,.2f}"
                    if est_price >= 5.0:
                        req_margin = max(0.30 * flip_val, 5.00 * flip_qty)
                    else:
                        req_margin = max(1.00 * flip_val, 2.50 * flip_qty)
                    bp_needed = round(req_margin * self.leverage, 2)
                    if bp_needed > self.buying_power + 0.01:
                        return False, f"Insufficient Day Trading Buying Power: needed ${bp_needed:,.2f}, available ${self.buying_power:,.2f}"
                    return True, "Approved"
                else:
                    return True, "Approved"

            elif existing_pos.side == PositionSide.SHORT and side.upper() == "BUY":
                if qty > existing_pos.shares:
                    flip_qty = qty - existing_pos.shares
                    flip_val = flip_qty * est_price
                    if flip_val > max_alloc + 0.01:
                        return False, f"Order exceeds per-position concentration cap of ${max_alloc:,.2f}"
                    req_margin = 0.25 * flip_val
                    bp_needed = round(req_margin * self.leverage, 2)
                    if bp_needed > self.buying_power + 0.01:
                        return False, f"Insufficient Day Trading Buying Power: needed ${bp_needed:,.2f}, available ${self.buying_power:,.2f}"
                    return True, "Approved"
                else:
                    return True, "Approved"

        # Position increasing: opening fresh position or adding to existing position
        is_increasing = False
        if side.upper() == "BUY" and (existing_pos is None or existing_pos.side == PositionSide.LONG):
            is_increasing = True
        elif side.upper() == "SELL" and (existing_pos is None or existing_pos.side == PositionSide.SHORT):
            is_increasing = True

        if is_increasing:
            if current_alloc + order_value > max_alloc + 0.01:
                return False, f"Order exceeds per-position concentration cap of ${max_alloc:,.2f}"

            # Calculate required margin for the order
            if side.upper() == "BUY":
                req_margin = 0.25 * order_value
            else:  # SHORT
                if est_price >= 5.0:
                    req_margin = max(0.30 * order_value, 5.00 * qty)
                else:
                    req_margin = max(1.00 * order_value, 2.50 * qty)

            bp_needed = round(req_margin * self.leverage, 2)
            if bp_needed > self.buying_power + 0.01:
                return False, f"Insufficient Day Trading Buying Power: needed ${bp_needed:,.2f}, available ${self.buying_power:,.2f}"

        return True, "Approved"

    def apply_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        price: float,
        fee: float,
        timestamp: datetime,
        arm: TradingArm = TradingArm.INTRADAY,
        strategy_id: str = "MANUAL",
        stop_loss_price: Optional[float] = None,
    ) -> Tuple[float, Optional[Position]]:
        """
        Atomically process execution fill:
        1. Updates cash ledger.
        2. Modifies or creates position.
        3. Realizes PnL on exits.
        4. Handles position flips.
        5. Recomputes equity and DTBP.
        Returns: (realized_pnl_delta, updated_position)
        """
        symbol = symbol.upper()
        side_norm = side.upper()
        existing_pos = self.positions.get(symbol)
        realized_delta = 0.0
        self.fees_paid = round(self.fees_paid + fee, 4)

        if existing_pos is None:
            # Opening fresh position
            pos_side = PositionSide.LONG if side_norm == "BUY" else PositionSide.SHORT
            if side_norm == "BUY":
                self.cash = round(self.cash - (qty * price + fee), 2)
            else:
                self.cash = round(self.cash + (qty * price - fee), 2)

            new_pos = Position(
                symbol=symbol,
                side=pos_side,
                shares=qty,
                avg_entry_price=round(price, 4),
                market_price=round(price, 4),
                fees_paid=fee,
                opened_at=timestamp,
                arm=arm,
                strategy_id=strategy_id,
                stop_loss_price=stop_loss_price,
            )
            self.positions[symbol] = new_pos
            self._recompute_account_state()
            return 0.0, new_pos

        # Existing position exists
        if existing_pos.side == PositionSide.LONG:
            if side_norm == "BUY":
                # Scaling into Long
                total_shares = existing_pos.shares + qty
                existing_pos.avg_entry_price = round(
                    ((existing_pos.shares * existing_pos.avg_entry_price) + (qty * price)) / total_shares,
                    4
                )
                existing_pos.shares = total_shares
                existing_pos.fees_paid = round(existing_pos.fees_paid + fee, 4)
                self.cash = round(self.cash - (qty * price + fee), 2)
                existing_pos.update_market_price(price)
            else:  # side == "SELL"
                if qty < existing_pos.shares:
                    # Partial exit Long
                    entry_fee = round(existing_pos.fees_paid * (qty / existing_pos.shares), 4) if existing_pos.shares > 0 else 0.0
                    realized_delta = round((qty * (price - existing_pos.avg_entry_price)) - fee - entry_fee, 2)
                    existing_pos.shares -= qty
                    existing_pos.fees_paid = round(existing_pos.fees_paid - entry_fee, 4)
                    existing_pos.realized_pnl = round(existing_pos.realized_pnl + realized_delta, 2)
                    self.cash = round(self.cash + (qty * price - fee), 2)
                    existing_pos.update_market_price(price)
                elif qty == existing_pos.shares:
                    # Full close Long
                    entry_fee = existing_pos.fees_paid
                    realized_delta = round((qty * (price - existing_pos.avg_entry_price)) - fee - entry_fee, 2)
                    self.cash = round(self.cash + (qty * price - fee), 2)
                    del self.positions[symbol]
                else:
                    # Position flip: Long -> Short
                    close_qty = existing_pos.shares
                    flip_qty = qty - close_qty
                    close_fee = round(fee * (close_qty / qty), 4)
                    short_fee = round(fee - close_fee, 4)

                    entry_fee = existing_pos.fees_paid
                    realized_delta = round((close_qty * (price - existing_pos.avg_entry_price)) - close_fee - entry_fee, 2)
                    self.cash = round(self.cash + (close_qty * price - close_fee), 2)

                    # Open short leg
                    self.cash = round(self.cash + (flip_qty * price - short_fee), 2)
                    new_pos = Position(
                        symbol=symbol,
                        side=PositionSide.SHORT,
                        shares=flip_qty,
                        avg_entry_price=round(price, 4),
                        market_price=round(price, 4),
                        fees_paid=short_fee,
                        opened_at=timestamp,
                    )
                    self.positions[symbol] = new_pos

        elif existing_pos.side == PositionSide.SHORT:
            if side_norm == "SELL":
                # Scaling into Short
                total_shares = existing_pos.shares + qty
                existing_pos.avg_entry_price = round(
                    ((existing_pos.shares * existing_pos.avg_entry_price) + (qty * price)) / total_shares,
                    4
                )
                existing_pos.shares = total_shares
                existing_pos.fees_paid = round(existing_pos.fees_paid + fee, 4)
                self.cash = round(self.cash + (qty * price - fee), 2)
                existing_pos.update_market_price(price)
            else:  # side == "BUY" (cover)
                if qty < existing_pos.shares:
                    # Partial cover Short
                    entry_fee = round(existing_pos.fees_paid * (qty / existing_pos.shares), 4) if existing_pos.shares > 0 else 0.0
                    realized_delta = round((qty * (existing_pos.avg_entry_price - price)) - fee - entry_fee, 2)
                    existing_pos.shares -= qty
                    existing_pos.fees_paid = round(existing_pos.fees_paid - entry_fee, 4)
                    existing_pos.realized_pnl = round(existing_pos.realized_pnl + realized_delta, 2)
                    self.cash = round(self.cash - (qty * price + fee), 2)
                    existing_pos.update_market_price(price)
                elif qty == existing_pos.shares:
                    # Full cover Short
                    entry_fee = existing_pos.fees_paid
                    realized_delta = round((qty * (existing_pos.avg_entry_price - price)) - fee - entry_fee, 2)
                    self.cash = round(self.cash - (qty * price + fee), 2)
                    del self.positions[symbol]
                else:
                    # Position flip: Short -> Long
                    cover_qty = existing_pos.shares
                    flip_qty = qty - cover_qty
                    cover_fee = round(fee * (cover_qty / qty), 4)
                    long_fee = round(fee - cover_fee, 4)

                    entry_fee = existing_pos.fees_paid
                    realized_delta = round((cover_qty * (existing_pos.avg_entry_price - price)) - cover_fee - entry_fee, 2)
                    self.cash = round(self.cash - (cover_qty * price + cover_fee), 2)

                    # Open long leg
                    self.cash = round(self.cash - (flip_qty * price + long_fee), 2)
                    new_pos = Position(
                        symbol=symbol,
                        side=PositionSide.LONG,
                        shares=flip_qty,
                        avg_entry_price=round(price, 4),
                        market_price=round(price, 4),
                        fees_paid=long_fee,
                        opened_at=timestamp,
                    )
                    self.positions[symbol] = new_pos

        self.realized_pnl = round(self.realized_pnl + realized_delta, 2)
        self._recompute_account_state()
        return realized_delta, self.positions.get(symbol)

    def _recompute_account_state(self) -> None:
        """
        Recomputes total equity, unrealized PnL, FINRA Rule 4210 maintenance margin,
        margin excess, 4:1 buying power, and daily drawdown metrics.
        """
        total_u_pnl = 0.0
        long_mv = 0.0
        short_liability = 0.0
        req_margin = 0.0

        for pos in self.positions.values():
            total_u_pnl += pos.unrealized_pnl
            if pos.side == PositionSide.LONG:
                long_mv += pos.market_value
                req_margin += 0.25 * pos.market_value
            else:
                liability = pos.shares * pos.market_price
                short_liability += liability
                # FINRA Rule 4210(f)(10) short margin
                if pos.market_price >= 5.0:
                    req_margin += max(0.30 * liability, 5.00 * pos.shares)
                else:
                    req_margin += max(1.00 * liability, 2.50 * pos.shares)

        self.unrealized_pnl = round(total_u_pnl, 2)
        # Total equity = Cash + Long MV - Short Liability
        self.equity = round(self.cash + long_mv - short_liability, 2)
        self.maintenance_margin = round(req_margin, 2)

        # Check PDT qualification
        is_pdt = self.equity >= self.PDT_MINIMUM_EQUITY
        if is_pdt:
            self.margin_excess = max(0.0, round(self.equity - self.maintenance_margin, 2))
            self.buying_power = round(self.margin_excess * self.leverage, 2)
        else:
            # Below $25k PDT threshold, no 4x intraday leverage
            self.margin_excess = max(0.0, round(self.equity - self.maintenance_margin, 2))
            self.buying_power = max(0.0, round(self.cash, 2))

        # Margin call: auto-set only from ACTIVE, auto-recover when cleared, and
        # never override externally imposed CIRCUIT_HALTED / EOD_FLAT states.
        if self.status == AccountStatus.ACTIVE and self.equity < self.maintenance_margin:
            self.status = AccountStatus.MARGIN_CALL
        elif self.status == AccountStatus.MARGIN_CALL and self.equity >= self.maintenance_margin:
            self.status = AccountStatus.ACTIVE

        # Compute drawdown from the current session start, not the original account deposit.
        dd_dollars = max(0.0, round(self.daily_starting_equity - self.equity, 2))
        self.daily_drawdown_dollars = dd_dollars
        self.daily_drawdown_pct = round(dd_dollars / self.daily_starting_equity, 4) if self.daily_starting_equity else 0.0

    def reset_daily_metrics(self, starting_equity: Optional[float] = None) -> None:
        """Start a new trading session while preserving the account's lifetime ledger."""
        self.daily_starting_equity = round(starting_equity if starting_equity is not None else self.equity, 2)
        self.daily_drawdown_dollars = 0.0
        self.daily_drawdown_pct = 0.0
        self.status = AccountStatus.ACTIVE
        self._recompute_account_state()

    def get_snapshot(self) -> AccountState:
        """Return an immutable snapshot of current account state."""
        return AccountState(
            cash=round(self.cash, 2),
            equity=round(self.equity, 2),
            buying_power=round(self.buying_power, 2),
            maintenance_margin=round(self.maintenance_margin, 2),
            margin_excess=round(self.margin_excess, 2),
            realized_pnl=round(self.realized_pnl, 2),
            unrealized_pnl=round(self.unrealized_pnl, 2),
            fees_paid=round(self.fees_paid, 2),
            daily_drawdown_dollars=self.daily_drawdown_dollars,
            daily_drawdown_pct=self.daily_drawdown_pct,
            is_circuit_broken=self.status == AccountStatus.CIRCUIT_HALTED,
            status=self.status.value,
            positions={k: v.to_state() for k, v in self.positions.items()},
            timestamp=datetime.now(timezone.utc),
        )
