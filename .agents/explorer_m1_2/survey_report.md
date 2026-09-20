# Technical Implementation Blueprint: $50,000 Paper Trading Account & Order Execution Engine

**Author**: `explorer_m1_2`  
**Target Project**: AutonomousDayTrader (Milestone 1: `engine_ingestion`)  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/explorer_m1_2`  
**Date**: 2026-09-19  
**Status**: Authoritative Architectural & Quantitative Specification  

---

## 1. Executive Summary & Architecture Overview

This blueprint provides the complete, production-grade technical design for the core financial accounting and order execution simulator of **AutonomousDayTrader**:
1. **Paper Trading Account State Machine (`backend/app/core/account.py`)**:
   - Manages a deterministic $\$50,000.00$ starting capital account with pattern day trading (PDT) capabilities.
   - Enforces **FINRA Rule 4210 Day Trading Buying Power (DTBP)** with 4:1 intraday leverage ($\$200,000.00$ max intraday purchasing power) and dynamic maintenance margin calculation for both LONG (25%) and SHORT (30% / \$5 min) positions.
   - Real-time mark-to-market re-valuation on every incoming tick, quote, and 1-minute OHLCV bar.
   - Multi-lot position ledger tracking symbol, side, share count, weighted average cost basis, market value, unrealized PnL, realized PnL, and position flipping.
2. **Order Lifecycle & Microstructure Fill Simulator (`backend/app/core/engine.py`)**:
   - Implements an 8-state deterministic finite state machine (FSM):  
     `CREATED -> SUBMITTED -> ACCEPTED -> PARTIALLY_FILLED -> FILLED / CANCELLED / REJECTED / EXPIRED`.
   - Incorporates an institutional microstructure fill simulator that replaces naive paper trading fills with dynamic slippage (spread-based + Kyle's lambda volume participation square-root model), bar liquidity volume participation ceilings ($\rho = 10\%$), adverse slippage on stop-loss market orders, and exact SEC Section 31 and FINRA Trading Activity Fees (TAF).
   - Records an immutable, chronologically sequenced audit trail logging every state transition, timestamp, trigger event, and balance snapshot.

```
                          ┌──────────────────────────────────────────────┐
                          │         Upstream Market Data Feed            │
                          │   (BarEvent, QuoteEvent, TradeEvent)         │
                          └──────────────────────┬───────────────────────┘
                                                 │
                                                 ▼
┌─────────────────────────┐               ┌──────────────────────────────┐
│  Trading Strategies /   │ OrderRequest  │       ExecutionEngine        │
│  UI Manual Overrides    ├──────────────►│ (backend/app/core/engine.py) │
└─────────────────────────┘               └──────┬───────────────────────┘
                                                 │ 1. Validate Order
                                                 ▼
                                          ┌──────────────────────────────┐
                                          │      RiskEngine Gate         │
                                          │  (backend/app/core/risk.py)  │
                                          └──────┬───────────────────────┘
                                                 │ 2. Pre-Trade Checks Pass
                                                 ▼
                                          ┌──────────────────────────────┐
                                          │     Active Working Orders    │
                                          │      (ACCEPTED / PARTIAL)    │
                                          └──────┬───────────────────────┘
                                                 │ 3. Match against Bars/Quotes
                                                 ▼
                                          ┌──────────────────────────────┐
                                          │  Microstructure Fill Model   │
                                          │ - Dynamic Slippage           │
                                          │ - Liquidity Participation    │
                                          │ - SEC & FINRA TAF Fees       │
                                          └──────┬───────────────────────┘
                                                 │ 4. Emit FillEvent
                                                 ▼
                                          ┌──────────────────────────────┐
                                          │  PaperTradingAccount Machine │
                                          │ (backend/app/core/account.py)│
                                          │ - Cash & Equity Ledger       │
                                          │ - FINRA 4210 4:1 DTBP        │
                                          │ - Real-Time Mark-to-Market   │
                                          │ - Audit Trail & PnL Ledger   │
                                          └──────┬───────────────────────┘
                                                 │
                                                 ▼
                                          ┌──────────────────────────────┐
                                          │ Real-Time UI Broadcast WS    │
                                          │ (FastAPI Port 8005 /ws/ui)   │
                                          └──────────────────────────────┘
```

---

## 2. Part I: Paper Trading Account State Machine (`backend/app/core/account.py`)

### 2.1 Core State Variables & Invariants

The account state machine tracks the following quantitative state variables:

| Variable | Symbol | Description | Mathematical Invariant |
|---|---|---|---|
| Initial Capital | $C_0$ | Seed capital | Exactly $\$50,000.00$ |
| Liquid Cash | $C_t$ | Unsettled cash + short sale proceeds | $C_t = C_0 + \sum \Delta \text{Cash}_{\text{fills}} - \sum \text{Fees}$ |
| Portfolio Equity | $E_t$ | Liquidation value of entire portfolio | $E_t = C_t + \sum_{s \in \text{Long}} q_s P_{s, t} - \sum_{s \in \text{Short}} q_s P_{s, t}$ |
| Total Realized PnL | $rPnL_t$ | Cumulative net closed profit/loss | $\sum_{\text{closed}} (\text{Proceeds} - \text{Cost} - \text{Fees})$ |
| Total Unrealized PnL | $uPnL_t$ | Mark-to-market open position profit/loss | $\sum_{s \in \mathcal{P}_t} uPnL_{s, t}$ |
| Daily Drawdown | $DD_t$ | Dollar and percentage drop from start | $DD_{\$} = \max(0.0, C_0 - E_t)$, $DD_{\%} = DD_{\$} / C_0$ |
| Maintenance Margin | $MMR_t$ | FINRA 4210 minimum margin required | $0.25 \cdot MV_{\text{long}} + \sum \max(0.30 \cdot MV_{\text{short}}, 5.00 \cdot q_s)$ |
| Margin Excess | $ME_t$ | Equity available over margin requirement | $\max(0.0, E_t - MMR_t)$ |
| Day Trading Buying Power | $DTBP_t$ | 4:1 intraday purchasing leverage | $\max(0.0, 4 \times ME_t)$ (Max $\$200,000.00$ at start) |

#### Fundamental Identity Check:
At any point in time $t$:
$$E_t = C_0 + rPnL_t + uPnL_t - \text{Total Fees}_t$$
The engine continuously verifies this balance conservation identity. If $|E_t - (C_0 + rPnL_t + uPnL_t - \text{Fees})| > 0.001$, an integrity alert is raised.

---

### 2.2 Mathematical Formulations

#### 1. Long Position Accounting
When purchasing $q_{\text{add}}$ shares at fill price $P_{\text{fill}}$:
- If no existing position:
  $$\bar{P}_{\text{entry}} = P_{\text{fill}}, \quad q = q_{\text{add}}, \quad \text{Cost Basis} = q \cdot \bar{P}_{\text{entry}}$$
- If existing Long position of $q_{\text{curr}}$ shares at $\bar{P}_{\text{curr}}$:
  $$\bar{P}_{\text{entry}, \text{new}} = \frac{q_{\text{curr}} \cdot \bar{P}_{\text{curr}} + q_{\text{add}} \cdot P_{\text{fill}}}{q_{\text{curr}} + q_{\text{add}}}$$
  $$q_{\text{new}} = q_{\text{curr}} + q_{\text{add}}$$
- Cash impact:
  $$C_t = C_{t-1} - (q_{\text{add}} \cdot P_{\text{fill}}) - \text{Fee}$$
- When selling $q_{\text{exit}} \le q_{\text{curr}}$ shares:
  $$\Delta rPnL = q_{\text{exit}} \cdot (P_{\text{fill}} - \bar{P}_{\text{entry}}) - \text{Fee}$$
  $$C_t = C_{t-1} + (q_{\text{exit}} \cdot P_{\text{fill}}) - \text{Fee}$$
  $$q_{\text{new}} = q_{\text{curr}} - q_{\text{exit}}$$
  (Note: $\bar{P}_{\text{entry}}$ remains unchanged upon partial exit).

#### 2. Short Position Accounting
When selling short $q_{\text{add}}$ shares at fill price $P_{\text{fill}}$:
- Cash receives the short sale proceeds:
  $$C_t = C_{t-1} + (q_{\text{add}} \cdot P_{\text{fill}}) - \text{Fee}$$
- The account records a short liability of $q_{\text{add}}$ shares.
- Cost basis is recorded as $q \cdot \bar{P}_{\text{entry}}$.
- When buying to cover $q_{\text{cover}} \le q_{\text{curr}}$ shares at $P_{\text{fill}}$:
  $$\Delta rPnL = q_{\text{cover}} \cdot (\bar{P}_{\text{entry}} - P_{\text{fill}}) - \text{Fee}$$
  $$C_t = C_{t-1} - (q_{\text{cover}} \cdot P_{\text{fill}}) - \text{Fee}$$
  $$q_{\text{new}} = q_{\text{curr}} - q_{\text{cover}}$$

#### 3. Position Flipping
When an order executes with quantity exceeding the existing position in the opposite direction (e.g., currently LONG 100 shares, and a SELL 160 shares order fills at $P_{\text{fill}}$):
- **Step 1 (Close existing position)**:
  - Close 100 shares Long.
  - Calculate $\Delta rPnL_1 = 100 \cdot (P_{\text{fill}} - \bar{P}_{\text{entry}}) - \text{Fee}_1$.
  - Cash adjusted by $100 \cdot P_{\text{fill}} - \text{Fee}_1$.
- **Step 2 (Open new reversed position)**:
  - Open 60 shares Short.
  - New $\bar{P}_{\text{entry}} = P_{\text{fill}}$, side = SHORT, shares = 60.
  - Cash adjusted by $60 \cdot P_{\text{fill}} - \text{Fee}_2$.
  - Position state updated atomically.

#### 4. Real-Time Mark-to-Market
On every incoming `BarEvent(open, high, low, close, volume)` or `QuoteEvent(bid, ask)`:
- For LONG position:
  $$P_{\text{market}} = P_{\text{close}} \quad (\text{or } P_{\text{bid}} \text{ from quote})$$
  $$MV = q \cdot P_{\text{market}}$$
  $$uPnL = q \cdot (P_{\text{market}} - \bar{P}_{\text{entry}})$$
- For SHORT position:
  $$P_{\text{market}} = P_{\text{close}} \quad (\text{or } P_{\text{ask}} \text{ from quote})$$
  $$MV = - q \cdot P_{\text{market}}$$
  $$uPnL = q \cdot (\bar{P}_{\text{entry}} - P_{\text{market}})$$
- Recalculate Portfolio Total Equity:
  $$E_t = C_t + \sum_{s \in \text{Long}} MV_s + \sum_{s \in \text{Short}} MV_s$$
  (Where $MV_{\text{short}}$ is negative, offsetting the cash proceeds previously credited).

---

### 2.3 FINRA Rule 4210 Day Trading Margin & Buying Power Engine

The account enforces FINRA Rule 4210 Day-Trading Margin Requirements:

#### 1. Pattern Day Trader (PDT) Qualification
Under FINRA Rule 4210(f)(8)(B), an account is designated as a Pattern Day Trader if it executes 4 or more day trades within 5 rolling business days. PDT accounts must maintain minimum equity of **$\$25,000.00$**.
- Because $C_0 = \$50,000.00$, the account is fully qualified for PDT 4:1 intraday leverage.
- If $E_t < \$25,000.00$, the account drops to cash-only basis ($1:1$ leverage) and Day Trading Buying Power is restricted to settled cash.

#### 2. Maintenance Margin Requirement ($MMR_t$)
- **Long Equity Positions**:
  $$MMR_{\text{long}, s} = 0.25 \times (q_s \cdot P_{\text{market}, s})$$
- **Short Equity Positions** (FINRA Rule 4210(f)(10)):
  - For stocks trading at or above $\$5.00$ per share:
    $$MMR_{\text{short}, s} = \max\left(0.30 \times q_s \cdot P_{\text{market}, s}, \quad \$5.00 \times q_s\right)$$
  - For low-priced stocks trading under $\$5.00$ per share:
    $$MMR_{\text{short}, s} = \max\left(1.00 \times q_s \cdot P_{\text{market}, s}, \quad \$2.50 \times q_s\right)$$
- Total Maintenance Margin Required:
  $$MMR_{\text{total}, t} = \sum_{s \in \text{Long}} MMR_{\text{long}, s} + \sum_{s \in \text{Short}} MMR_{\text{short}, s}$$

#### 3. Day Trading Buying Power ($DTBP_t$)
- Day Trading Margin Excess:
  $$ME_t = \max\left(0.0, E_t - MMR_{\text{total}, t}\right)$$
- Day Trading Buying Power:
  $$DTBP_t = 4.0 \times ME_t$$
  - At session inception: $E_0 = \$50,000.00, MMR_0 = 0 \implies DTBP_0 = 4.0 \times \$50,000.00 = \mathbf{\$200,000.00}$.
  - As positions open, $MMR_t$ increases, dynamically reducing $ME_t$ and $DTBP_t$.
- **Per-Position Capital Cap**:
  To prevent overconcentration, no single position may consume more than $25\%$ of total intraday buying power:
  $$\text{Max Position Allocation} = 0.25 \times DTBP_{\text{max}} = \$50,000.00$$

#### 4. Pre-Trade Margin Validation
Before any order can transition from `SUBMITTED` to `ACCEPTED`, the engine calculates the required initial margin for the proposed order:
- Proposed Long Order ($q$ shares @ $P_{\text{limit}}$ or $P_{\text{est}}$):
  $$\text{Required Margin} = 0.25 \times (q \cdot P_{\text{est}})$$
  $$\text{Buying Power Consumed} = q \cdot P_{\text{est}}$$
- Proposed Short Order ($q$ shares @ $P_{\text{limit}}$ or $P_{\text{est}}$):
  $$\text{Required Margin} = \max(0.30 \cdot q \cdot P_{\text{est}}, \quad 5.00 \cdot q)$$
  $$\text{Buying Power Consumed} = \frac{\text{Required Margin}}{0.25} = 4 \times \text{Required Margin}$$
- **Validation Rule**:
  $$\text{If } \text{Buying Power Consumed} > DTBP_t \implies \text{REJECT Order (Insufficient Day Trading Buying Power)}$$

---

### 2.4 Data Models & Class Architecture (`backend/app/core/account.py`)

```python
"""
backend/app/core/account.py
Architectural Blueprint for $50,000 Paper Trading Account State Machine.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple
import math


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

    def __post_init__(self):
        self.recalculate()

    def recalculate(self) -> None:
        """Update market value and unrealized PnL based on market price."""
        self.cost_basis = round(self.shares * self.avg_entry_price, 2)
        if self.side == PositionSide.LONG:
            self.market_value = round(self.shares * self.market_price, 2)
            self.unrealized_pnl = round(self.shares * (self.market_price - self.avg_entry_price), 2)
        else:
            # Short market value is a liability (negative impact on cash/equity model)
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


@dataclass
class AccountSnapshot:
    """Immutable state snapshot for UI streaming and audit logging."""
    timestamp: datetime
    status: AccountStatus
    initial_balance: float
    cash: float
    equity: float
    buying_power: float
    maintenance_margin: float
    margin_excess: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    daily_drawdown_dollars: float
    daily_drawdown_pct: float
    open_positions_count: int
    is_pdt_eligible: bool
    positions: Dict[str, Position]
```

### 2.5 `PaperTradingAccount` Class Implementation Blueprint

```python
class PaperTradingAccount:
    """
    Self-contained, deterministic Paper Trading Account State Machine.
    Initial balance: $50,000.00.
    FINRA Rule 4210 Day Trading Buying Power: 4:1 ($200,000 max).
    """

    INITIAL_CAPITAL: float = 50000.00
    PDT_MINIMUM_EQUITY: float = 25000.00
    MAX_POSITION_ALLOCATION_PCT: float = 0.25  # 25% max buying power per symbol

    def __init__(self, initial_cash: float = INITIAL_CAPITAL) -> None:
        self.initial_balance: float = initial_cash
        self.cash: float = initial_cash
        self.equity: float = initial_cash
        self.status: AccountStatus = AccountStatus.ACTIVE
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.fees_paid: float = 0.0
        self.maintenance_margin: float = 0.0
        self.margin_excess: float = initial_cash
        self.buying_power: float = initial_cash * 4.0
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
        and complies with FINRA Rule 4210 and per-position risk limits.
        """
        if self.status != AccountStatus.ACTIVE:
            return False, f"Account is not ACTIVE (current status: {self.status.value})"

        symbol = symbol.upper()
        existing_pos = self.positions.get(symbol)
        order_value = qty * est_price

        # Check per-position allocation ceiling ($50,000 max)
        max_alloc = self.initial_balance * 4.0 * self.MAX_POSITION_ALLOCATION_PCT
        current_alloc = abs(existing_pos.market_value) if existing_pos else 0.0
        
        # If order increases exposure in same direction
        is_increasing = False
        if side == "BUY" and (existing_pos is None or existing_pos.side == PositionSide.LONG):
            is_increasing = True
        elif side == "SELL" and (existing_pos is None or existing_pos.side == PositionSide.SHORT):
            is_increasing = True

        if is_increasing:
            if current_alloc + order_value > max_alloc:
                return False, f"Order exceeds per-position concentration cap of ${max_alloc:,.2f}"

            # Calculate required margin for the order
            if side == "BUY":
                req_margin = 0.25 * order_value
            else:  # SHORT
                req_margin = max(0.30 * order_value, 5.00 * qty)

            bp_needed = req_margin * 4.0
            if bp_needed > self.buying_power:
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
        existing_pos = self.positions.get(symbol)
        realized_delta = 0.0
        self.fees_paid += fee

        if existing_pos is None:
            # Opening fresh position
            pos_side = PositionSide.LONG if side == "BUY" else PositionSide.SHORT
            if side == "BUY":
                self.cash -= (qty * price + fee)
            else:
                self.cash += (qty * price - fee)

            new_pos = Position(
                symbol=symbol,
                side=pos_side,
                shares=qty,
                avg_entry_price=price,
                market_price=price,
                fees_paid=fee,
                opened_at=timestamp,
            )
            self.positions[symbol] = new_pos
            self._recompute_account_state()
            return 0.0, new_pos

        # Existing position exists
        if existing_pos.side == PositionSide.LONG:
            if side == "BUY":
                # Scaling into Long
                total_shares = existing_pos.shares + qty
                existing_pos.avg_entry_price = (
                    (existing_pos.shares * existing_pos.avg_entry_price) + (qty * price)
                ) / total_shares
                existing_pos.shares = total_shares
                existing_pos.fees_paid += fee
                self.cash -= (qty * price + fee)
                existing_pos.update_market_price(price)
            else:  # side == "SELL"
                if qty < existing_pos.shares:
                    # Partial exit Long
                    realized_delta = (qty * (price - existing_pos.avg_entry_price)) - fee
                    existing_pos.shares -= qty
                    existing_pos.realized_pnl += realized_delta
                    existing_pos.fees_paid += fee
                    self.cash += (qty * price - fee)
                    existing_pos.update_market_price(price)
                elif qty == existing_pos.shares:
                    # Full close Long
                    realized_delta = (qty * (price - existing_pos.avg_entry_price)) - fee
                    self.cash += (qty * price - fee)
                    del self.positions[symbol]
                else:
                    # Position flip: Long -> Short
                    close_qty = existing_pos.shares
                    flip_qty = qty - close_qty
                    realized_delta = (close_qty * (price - existing_pos.avg_entry_price)) - (fee * (close_qty / qty))
                    self.cash += (close_qty * price - (fee * (close_qty / qty)))
                    
                    # Open short leg
                    short_fee = fee * (flip_qty / qty)
                    self.cash += (flip_qty * price - short_fee)
                    new_pos = Position(
                        symbol=symbol,
                        side=PositionSide.SHORT,
                        shares=flip_qty,
                        avg_entry_price=price,
                        market_price=price,
                        fees_paid=short_fee,
                        opened_at=timestamp,
                    )
                    self.positions[symbol] = new_pos

        elif existing_pos.side == PositionSide.SHORT:
            if side == "SELL":
                # Scaling into Short
                total_shares = existing_pos.shares + qty
                existing_pos.avg_entry_price = (
                    (existing_pos.shares * existing_pos.avg_entry_price) + (qty * price)
                ) / total_shares
                existing_pos.shares = total_shares
                existing_pos.fees_paid += fee
                self.cash += (qty * price - fee)
                existing_pos.update_market_price(price)
            else:  # side == "BUY" (cover)
                if qty < existing_pos.shares:
                    # Partial cover Short
                    realized_delta = (qty * (existing_pos.avg_entry_price - price)) - fee
                    existing_pos.shares -= qty
                    existing_pos.realized_pnl += realized_delta
                    existing_pos.fees_paid += fee
                    self.cash -= (qty * price + fee)
                    existing_pos.update_market_price(price)
                elif qty == existing_pos.shares:
                    # Full cover Short
                    realized_delta = (qty * (existing_pos.avg_entry_price - price)) - fee
                    self.cash -= (qty * price + fee)
                    del self.positions[symbol]
                else:
                    # Position flip: Short -> Long
                    cover_qty = existing_pos.shares
                    flip_qty = qty - cover_qty
                    realized_delta = (cover_qty * (existing_pos.avg_entry_price - price)) - (fee * (cover_qty / qty))
                    self.cash -= (cover_qty * price + (fee * (cover_qty / qty)))
                    
                    # Open long leg
                    long_fee = fee * (flip_qty / qty)
                    self.cash -= (flip_qty * price + long_fee)
                    new_pos = Position(
                        symbol=symbol,
                        side=PositionSide.LONG,
                        shares=flip_qty,
                        avg_entry_price=price,
                        market_price=price,
                        fees_paid=long_fee,
                        opened_at=timestamp,
                    )
                    self.positions[symbol] = new_pos

        self.realized_pnl += realized_delta
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
            self.margin_excess = max(0.0, self.equity - self.maintenance_margin)
            self.buying_power = round(self.margin_excess * 4.0, 2)
        else:
            # Below $25k PDT threshold, no 4x intraday leverage
            self.margin_excess = max(0.0, self.equity - self.maintenance_margin)
            self.buying_power = max(0.0, round(self.cash, 2))

        # Check margin call condition
        if self.equity < self.maintenance_margin:
            self.status = AccountStatus.MARGIN_CALL

        # Compute drawdown from session start ($50,000.00)
        dd_dollars = max(0.0, self.initial_balance - self.equity)
        self.daily_drawdown_dollars = round(dd_dollars, 2)
        self.daily_drawdown_pct = round(dd_dollars / self.initial_balance, 4)

    def get_snapshot(self) -> AccountSnapshot:
        """Return an immutable snapshot of current account state."""
        return AccountSnapshot(
            timestamp=datetime.now(timezone.utc),
            status=self.status,
            initial_balance=self.initial_balance,
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
            open_positions_count=len(self.positions),
            is_pdt_eligible=self.equity >= self.PDT_MINIMUM_EQUITY,
            positions={k: Position(**v.__dict__) for k, v in self.positions.items()},
        )
```

---

## 3. Part II: Order Lifecycle & Microstructure Fill Simulator (`backend/app/core/engine.py`)

### 3.1 8-State Order Lifecycle Finite State Machine (FSM)

The execution engine manages orders through an explicit 8-state finite state machine:

```
                          [ Order Instantiation ]
                                     │
                                     ▼
                                ┌─────────┐
                                │ CREATED │
                                └────┬────┘
                                     │ submit()
                                     ▼
                               ┌───────────┐
                   Reject      │ SUBMITTED │
           ┌───────────────────┤           │
           │ (BP/Risk Failure) └─────┬─────┘
           ▼                         │ Accept (Pass Pre-Trade Gates)
     ┌──────────┐                    ▼
     │ REJECTED │              ┌──────────┐
     └──────────┘              │ ACCEPTED │
                               └─────┬────┘
                                     │
                 ┌───────────────────┼───────────────────┐
                 │ Match Quote/Bar   │ User/EOD Cancel   │ Session Expiry
                 ▼                   ▼                   ▼
        ┌──────────────────┐  ┌───────────┐        ┌─────────┐
   ┌───►│ PARTIALLY_FILLED │  │ CANCELLED │        │ EXPIRED │
   │    └────────┬─────────┘  └───────────┘        └─────────┘
   │             │                   ▲                  ▲
   │ Match More  │ Remainder Fill    │ Cancel Remainder │ Expire Remainder
   └─────────────┤                   │                  │
                 ▼                   │                  │
            ┌─────────┐              │                  │
            │ FILLED  │──────────────┴──────────────────┘
            └─────────┘
```

#### Order State Definitions & Invariants:

1. **`CREATED`**: Initial order object generated by strategy signal or manual UI input. Order parameters are frozen; order is not yet known to the matching engine.
2. **`SUBMITTED`**: Dispatched into engine ingestion queue; awaiting atomic pre-trade risk and buying power validation.
3. **`ACCEPTED`**: Passed all pre-trade validations (account has sufficient buying power, daily loss circuit breaker not tripped, session time within trading hours). Registered in the active working order book.
4. **`PARTIALLY_FILLED`**: Order has matched against bar/quote volume for $0 < q_{\text{cum}} < q_{\text{total}}$. Remaining working quantity $q_{\text{rem}} = q_{\text{total}} - q_{\text{cum}}$ continues matching.
5. **`FILLED`**: Terminal state. Cumulative executed quantity equals total ordered quantity ($q_{\text{cum}} == q_{\text{total}}$).
6. **`CANCELLED`**: Terminal state. Order was cancelled prior to complete execution by strategy, user manual override, or 15:50 ET order purge. Unfilled quantity is released.
7. **`REJECTED`**: Terminal state. Order failed pre-trade risk gates (insufficient DTBP, circuit breaker active, session phase lockout, or price sanity failure).
8. **`EXPIRED`**: Terminal state. Order time-in-force elapsed without full fill (e.g. DAY order reaching 16:00 ET or IOC unfilled remainder).

#### FSM State Transition Matrix:

| From State | Allowed Target States | Valid Trigger Events |
|---|---|---|
| `CREATED` | `SUBMITTED`, `REJECTED` | Dispatch to engine, pre-validation syntax check |
| `SUBMITTED` | `ACCEPTED`, `REJECTED` | Risk engine pass / fail, buying power approval |
| `ACCEPTED` | `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `EXPIRED` | Market match, user cancel, EOD purge, time-in-force |
| `PARTIALLY_FILLED` | `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `EXPIRED` | Subsequent match, full fill, cancel remainder, expire |
| `FILLED` | *(Terminal)* | None (Illegal transition) |
| `CANCELLED` | *(Terminal)* | None (Illegal transition) |
| `REJECTED` | *(Terminal)* | None (Illegal transition) |
| `EXPIRED` | *(Terminal)* | None (Illegal transition) |

---

### 3.2 Microstructure Fill Simulator Mechanics

To provide institutional realism, the execution simulator models real-world order matching constraints:

#### 1. Market Orders:
- Ingests prevailing best quote `QuoteEvent(bid, ask)`:
  - BUY fills at $P_{\text{ask}} + \text{Slippage}$
  - SELL fills at $P_{\text{bid}} - \text{Slippage}$
- If quotes are missing (only 1-minute OHLCV bars available):
  - A synthetic half-spread is calculated:
    $$\text{HalfSpread} = \max\left(0.005, \frac{1}{2} \cdot P_{\text{close}} \times 0.0004\right)$$
  - BUY fills at $P_{\text{close}} + \text{HalfSpread} + \text{Slippage}$
  - SELL fills at $P_{\text{close}} - \text{HalfSpread} - \text{Slippage}$

#### 2. Limit Orders:
- Evaluates bar extrema $[L_t, H_t]$ and quote $[P_{\text{bid}}, P_{\text{ask}}]$:
  - **BUY LIMIT ($P_{\text{limit}}$)**:
    - Condition: $L_t \le P_{\text{limit}}$ (or $P_{\text{ask}} \le P_{\text{limit}}$).
    - Fill price: $\min(P_{\text{limit}}, \text{Open}_t)$ if bar opened below limit (price improvement), otherwise $P_{\text{limit}}$.
  - **SELL LIMIT ($P_{\text{limit}}$)**:
    - Condition: $H_t \ge P_{\text{limit}}$ (or $P_{\text{bid}} \ge P_{\text{limit}}$).
    - Fill price: $\max(P_{\text{limit}}, \text{Open}_t)$ if bar opened above limit (price improvement), otherwise $P_{\text{limit}}$.
- **Volume Participation Limit ($\rho = 10\%$)**:
  - In equity microstructure, a passive limit order cannot fill more than a fraction of the bar's traded volume.
  - Maximum fillable shares in bar: $q_{\text{avail}} = \max\left(10, \lfloor 0.10 \times V_{\text{bar}} \rfloor\right)$.
  - If $q_{\text{rem}} \le q_{\text{avail}}$, order completely fills $\to$ `FILLED`.
  - If $q_{\text{rem}} > q_{\text{avail}}$, order fills $q_{\text{avail}}$ shares $\to$ `PARTIALLY_FILLED`.

#### 3. Stop-Loss Orders:
- **SELL STOP ($P_{\text{stop}}$)**:
  - Trigger condition: $P_{\text{last}} \le P_{\text{stop}}$ or $L_t \le P_{\text{stop}}$.
  - When triggered, converts to Market Sell Order with adverse slippage ($1.5\times$ base slippage) to model liquidity vacuum gaps.
- **BUY STOP ($P_{\text{stop}}$)**:
  - Trigger condition: $P_{\text{last}} \ge P_{\text{stop}}$ or $H_t \ge P_{\text{stop}}$.
  - When triggered, converts to Market Buy Order with adverse slippage ($1.5\times$ base slippage).

---

### 3.3 Dynamic Slippage & Market Impact Mathematical Model

The simulator employs a modified Kyle's lambda square-root market impact formula:

$$\text{Slippage} = \frac{1}{2} \text{Spread} + \gamma \cdot \sigma_{1\text{m}} \cdot \sqrt{\frac{q_{\text{order}}}{V_{\text{bar}}}}$$

Where:
- $\text{Spread} = \begin{cases} P_{\text{ask}} - P_{\text{bid}} & \text{if quote available} \\ \max(0.01, P_{\text{market}} \times 0.0004) & \text{if synthetic} \end{cases}$
- $\sigma_{1\text{m}} = \max\left(0.001 \cdot P_{\text{market}}, \quad H_t - L_t\right)$ (1-minute bar return volatility).
- $q_{\text{order}} =$ order execution size.
- $V_{\text{bar}} =$ 1-minute bar volume (fallback benchmark: 10,000 shares).
- $\gamma = 0.08$ (liquidity friction coefficient).
- **Slippage Floor**:
  $$\text{Slippage}_{\text{floor}} = \max\left(0.01, \quad 0.0001 \times P_{\text{market}}\right) \quad (1.0 \text{ bps floor})$$
  $$\text{Slippage}_{\text{final}} = \max\left(\text{Slippage}_{\text{floor}}, \quad \text{Slippage}\right)$$
- On stop-loss executions: $\text{Slippage}_{\text{stop}} = 1.50 \times \text{Slippage}_{\text{final}}$.

---

### 3.4 Regulatory Transaction Fee Engine

The simulator calculates real-world US equity regulatory fees on execution fills:

1. **Brokerage Commission**: $\$0.00$ (zero-commission paper broker model).
2. **SEC Section 31 Transaction Fee**:
   - Applies **exclusively to SELL transactions** (both Long exits and Short sales).
   - Rate: $\$27.80$ per $\$1,000,000.00$ of principal value ($0.0000278$).
   - Rounded **up** to the nearest whole cent:
     $$\text{Fee}_{\text{SEC}} = \frac{\lceil 0.0000278 \times (q_{\text{sell}} \cdot P_{\text{fill}}) \times 100 \rceil}{100}$$
3. **FINRA Trading Activity Fee (TAF)**:
   - Applies **exclusively to SELL transactions**.
   - Rate: $\$0.000166$ per share, capped at $\$8.30$ per transaction:
     $$\text{Fee}_{\text{TAF}} = \min\left(8.30, \quad \text{round}(0.000166 \times q_{\text{sell}}, 2)\right)$$
   - Minimum fee of $\$0.01$ if $q_{\text{sell}} > 0$.
4. **Summary**:
   - For `BUY` orders: $\text{Fee}_{\text{total}} = \$0.00$.
   - For `SELL` orders: $\text{Fee}_{\text{total}} = \text{Fee}_{\text{SEC}} + \text{Fee}_{\text{TAF}}$.

---

### 3.5 Immutable Audit Trail & Event Sourcing

Every lifecycle state transition generates an immutable `OrderAuditRecord`:

```python
@dataclass(frozen=True)
class OrderAuditRecord:
    timestamp: datetime
    order_id: str
    symbol: str
    from_state: str
    to_state: str
    event_trigger: str  # e.g., "SUBMIT_REQUEST", "RISK_PASS", "QUOTE_FILL", "USER_CANCEL"
    reason: str
    fill_qty: int
    fill_price: float
    cum_filled_qty: int
    remaining_qty: int
    fee: float
    account_cash_after: float
    account_equity_after: float
```

The engine maintains an in-memory chronological sequence of audit records and provides query filters by `order_id`, `symbol`, and timestamp range.

---

### 3.6 Data Models & Class Architecture (`backend/app/core/engine.py`)

```python
"""
backend/app/core/engine.py
Architectural Blueprint for 8-State Order Lifecycle and Microstructure Fill Simulator.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple, Callable
import math
import uuid

from app.core.account import PaperTradingAccount, PositionSide


class OrderState(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill


class BracketRole(str, Enum):
    ENTRY = "ENTRY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT_1 = "TAKE_PROFIT_1"
    TAKE_PROFIT_2 = "TAKE_PROFIT_2"


class InvalidOrderStateTransitionError(Exception):
    """Raised when an order attempts an illegal state transition."""
    pass


@dataclass
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    fee: float
    slippage: float
    timestamp: datetime


@dataclass
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    strategy_id: str = "MANUAL"
    bracket_role: Optional[BracketRole] = None
    parent_order_id: Optional[str] = None
    status: OrderState = OrderState.CREATED
    filled_qty: int = 0
    remaining_qty: int = field(init=False)
    avg_fill_price: float = 0.0
    fees_paid: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    submitted_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    fills: List[Fill] = field(default_factory=list)
    audit_trail: List[OrderAuditRecord] = field(default_factory=list)

    def __post_init__(self):
        self.remaining_qty = self.qty
```

### 3.7 `ExecutionEngine` Class Implementation Blueprint

```python
class ExecutionEngine:
    """
    Deterministic Order Execution Engine and Microstructure Fill Simulator.
    Manages working order book, FSM state transitions, slippage, and fees.
    """

    SEC_FEE_RATE: float = 0.0000278  # $27.80 per million dollars
    FINRA_TAF_RATE: float = 0.000166  # $0.000166 per share
    FINRA_TAF_CAP: float = 8.30
    MAX_BAR_PARTICIPATION_RATE: float = 0.10  # 10% max volume participation

    def __init__(
        self,
        account: PaperTradingAccount,
        risk_validator: Optional[Callable[[Order, PaperTradingAccount], Tuple[bool, str]]] = None,
    ) -> None:
        self.account: PaperTradingAccount = account
        self.risk_validator = risk_validator
        self.orders: Dict[str, Order] = {}
        self.working_orders: Dict[str, Order] = {}  # Orders in ACCEPTED or PARTIALLY_FILLED
        self.audit_log: List[OrderAuditRecord] = []

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        qty: int,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
        strategy_id: str = "MANUAL",
        client_order_id: Optional[str] = None,
        bracket_role: Optional[BracketRole] = None,
        parent_order_id: Optional[str] = None,
    ) -> Order:
        """Create a new order in CREATED state."""
        if qty <= 0:
            raise ValueError(f"Order quantity must be positive, got {qty}")
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and limit_price is None:
            raise ValueError(f"Limit price required for {order_type.value} orders")
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and stop_price is None:
            raise ValueError(f"Stop price required for {order_type.value} orders")

        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        client_id = client_order_id or f"cl_{uuid.uuid4().hex[:8]}"

        order = Order(
            id=order_id,
            client_order_id=client_id,
            symbol=symbol.upper(),
            side=side,
            order_type=order_type,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            strategy_id=strategy_id,
            bracket_role=bracket_role,
            parent_order_id=parent_order_id,
        )
        self.orders[order_id] = order
        self._record_audit(order, OrderState.CREATED, "ORDER_CREATED", "Order initialized")
        return order

    def submit_order(self, order_id: str) -> Order:
        """
        Transition order: CREATED -> SUBMITTED.
        Runs account buying power check and risk engine validator:
        - If pass: transitions SUBMITTED -> ACCEPTED.
        - If fail: transitions SUBMITTED -> REJECTED.
        """
        order = self.orders.get(order_id)
        if not order:
            raise KeyError(f"Order {order_id} not found")
        if order.status != OrderState.CREATED:
            raise InvalidOrderStateTransitionError(
                f"Cannot submit order in state {order.status.value}"
            )

        order.status = OrderState.SUBMITTED
        order.submitted_at = datetime.now(timezone.utc)
        self._record_audit(order, OrderState.SUBMITTED, "SUBMITTED", "Dispatched to engine")

        # 1. Account Buying Power & Concentration check
        est_price = order.limit_price or order.stop_price or 100.0  # fallback estimate
        can_afford, afford_reason = self.account.can_afford(
            order.symbol, order.side.value, order.qty, est_price
        )
        if not can_afford:
            return self._reject_order(order, afford_reason)

        # 2. Risk Engine pre-trade validation gate (if configured)
        if self.risk_validator is not None:
            risk_ok, risk_reason = self.risk_validator(order, self.account)
            if not risk_ok:
                return self._reject_order(order, risk_reason)

        # All checks passed: accept order
        order.status = OrderState.ACCEPTED
        order.accepted_at = datetime.now(timezone.utc)
        self.working_orders[order.id] = order
        self._record_audit(order, OrderState.ACCEPTED, "ACCEPTED", "Pre-trade risk and margin passed")
        return order

    def _reject_order(self, order: Order, reason: str) -> Order:
        """Transition order to REJECTED."""
        order.status = OrderState.REJECTED
        order.completed_at = datetime.now(timezone.utc)
        order.reject_reason = reason
        self._record_audit(order, OrderState.REJECTED, "RISK_REJECTED", reason)
        return order

    def cancel_order(self, order_id: str, reason: str = "USER_REQUEST") -> Order:
        """
        Cancel working order in ACCEPTED or PARTIALLY_FILLED state.
        Transitions to CANCELLED.
        """
        order = self.orders.get(order_id)
        if not order:
            raise KeyError(f"Order {order_id} not found")
        if order.status not in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED):
            raise InvalidOrderStateTransitionError(
                f"Cannot cancel order in state {order.status.value}"
            )

        order.status = OrderState.CANCELLED
        order.completed_at = datetime.now(timezone.utc)
        self.working_orders.pop(order.id, None)
        self._record_audit(order, OrderState.CANCELLED, "CANCEL_REQUEST", reason)
        return order

    def cancel_all_orders(self, reason: str = "CIRCUIT_BREAKER") -> List[Order]:
        """Cancel all active working orders."""
        cancelled = []
        for order_id in list(self.working_orders.keys()):
            cancelled.append(self.cancel_order(order_id, reason))
        return cancelled

    def calculate_fees(self, side: OrderSide, qty: int, fill_price: float) -> float:
        """
        Calculates exact SEC Section 31 and FINRA TAF regulatory fees on sell orders.
        Buys incur $0.00.
        """
        if side == OrderSide.BUY:
            return 0.0

        principal = qty * fill_price
        # SEC fee: $27.80 per million, rounded UP to nearest cent
        sec_fee = math.ceil(self.SEC_FEE_RATE * principal * 100.0) / 100.0
        
        # FINRA TAF: $0.000166 per share, min $0.01, capped at $8.30
        taf_fee = min(self.FINRA_TAF_CAP, round(self.FINRA_TAF_RATE * qty, 2))
        if qty > 0 and taf_fee < 0.01:
            taf_fee = 0.01

        return round(sec_fee + taf_fee, 2)

    def calculate_slippage(
        self,
        order: Order,
        market_price: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        bar_volume: int = 10000,
        bar_high: Optional[float] = None,
        bar_low: Optional[float] = None,
    ) -> float:
        """
        Dynamic microstructure slippage model based on spread and volume participation.
        """
        spread = (ask - bid) if (bid and ask) else max(0.01, market_price * 0.0004)
        volatility = (bar_high - bar_low) if (bar_high and bar_low) else max(0.01, market_price * 0.001)
        participation = math.sqrt(order.qty / max(1000, bar_volume))
        
        raw_slippage = (0.5 * spread) + (0.08 * volatility * participation)
        floor = max(0.01, market_price * 0.0001)  # 1 bps floor
        slippage = max(floor, raw_slippage)

        # 1.5x adverse multiplier on stop orders
        if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            slippage *= 1.5

        return round(slippage, 4)

    def process_quote(
        self,
        symbol: str,
        bid: float,
        ask: float,
        timestamp: datetime,
    ) -> List[Fill]:
        """Match working orders against an incoming quote."""
        symbol = symbol.upper()
        mid_price = (bid + ask) / 2.0
        self.account.update_market_price(symbol, mid_price)

        fills: List[Fill] = []
        matching_orders = [o for o in self.working_orders.values() if o.symbol == symbol]

        for order in matching_orders:
            fill_price: Optional[float] = None
            slippage = self.calculate_slippage(order, mid_price, bid=bid, ask=ask)

            if order.order_type == OrderType.MARKET:
                fill_price = (ask + slippage) if order.side == OrderSide.BUY else (bid - slippage)
            elif order.order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY and ask <= order.limit_price:
                    fill_price = min(order.limit_price, ask)
                elif order.side == OrderSide.SELL and bid >= order.limit_price:
                    fill_price = max(order.limit_price, bid)
            elif order.order_type == OrderType.STOP:
                if order.side == OrderSide.BUY and ask >= order.stop_price:
                    fill_price = ask + slippage
                elif order.side == OrderSide.SELL and bid <= order.stop_price:
                    fill_price = bid - slippage

            if fill_price is not None:
                fill = self._execute_fill(order, order.remaining_qty, fill_price, slippage, timestamp)
                fills.append(fill)

        return fills

    def process_bar(
        self,
        symbol: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        timestamp: datetime,
    ) -> List[Fill]:
        """Match working orders against an incoming 1-minute OHLCV bar."""
        symbol = symbol.upper()
        self.account.update_market_price(symbol, close)

        fills: List[Fill] = []
        matching_orders = [o for o in self.working_orders.values() if o.symbol == symbol]
        # Max fillable quantity in bar under 10% participation cap
        max_fillable = max(10, int(volume * self.MAX_BAR_PARTICIPATION_RATE))

        for order in matching_orders:
            fill_price: Optional[float] = None
            slippage = self.calculate_slippage(
                order, close, bar_volume=volume, bar_high=high, bar_low=low
            )

            if order.order_type == OrderType.MARKET:
                # Fill at open or close adjusted for spread & slippage
                spread_half = max(0.005, close * 0.0002)
                fill_price = (close + spread_half + slippage) if order.side == OrderSide.BUY else (close - spread_half - slippage)
            elif order.order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY and low <= order.limit_price:
                    # Price improvement if opened below limit
                    fill_price = min(order.limit_price, open_) if open_ <= order.limit_price else order.limit_price
                elif order.side == OrderSide.SELL and high >= order.limit_price:
                    fill_price = max(order.limit_price, open_) if open_ >= order.limit_price else order.limit_price
            elif order.order_type == OrderType.STOP:
                if order.side == OrderSide.BUY and high >= order.stop_price:
                    fill_price = max(order.stop_price, open_) + slippage
                elif order.side == OrderSide.SELL and low <= order.stop_price:
                    fill_price = min(order.stop_price, open_) - slippage

            if fill_price is not None:
                # Apply volume participation cap
                exec_qty = min(order.remaining_qty, max_fillable)
                fill = self._execute_fill(order, exec_qty, fill_price, slippage, timestamp)
                fills.append(fill)

        return fills

    def _execute_fill(
        self,
        order: Order,
        qty: int,
        price: float,
        slippage: float,
        timestamp: datetime,
    ) -> Fill:
        """Atomically record fill, update account, and advance order state."""
        fee = self.calculate_fees(order.side, qty, price)
        fill = Fill(
            fill_id=f"fl_{uuid.uuid4().hex[:10]}",
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            price=round(price, 4),
            fee=fee,
            slippage=round(slippage, 4),
            timestamp=timestamp,
        )

        order.fills.append(fill)
        order.filled_qty += qty
        order.remaining_qty -= qty
        order.fees_paid += fee
        
        # Update order weighted average fill price
        total_val = sum(f.qty * f.price for f in order.fills)
        order.avg_fill_price = round(total_val / order.filled_qty, 4)

        # Apply to account ledger
        self.account.apply_fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side.value,
            qty=qty,
            price=price,
            fee=fee,
            timestamp=timestamp,
        )

        if order.remaining_qty == 0:
            order.status = OrderState.FILLED
            order.completed_at = timestamp
            self.working_orders.pop(order.id, None)
            self._record_audit(
                order, OrderState.FILLED, "FULL_FILL",
                f"Filled {qty} @ ${price:.2f}, fee ${fee:.2f}",
                fill_qty=qty, fill_price=price, fee=fee
            )
        else:
            order.status = OrderState.PARTIALLY_FILLED
            self._record_audit(
                order, OrderState.PARTIALLY_FILLED, "PARTIAL_FILL",
                f"Partially filled {qty} @ ${price:.2f}, remaining {order.remaining_qty}",
                fill_qty=qty, fill_price=price, fee=fee
            )

        return fill

    def _record_audit(
        self,
        order: Order,
        to_state: OrderState,
        trigger: str,
        reason: str,
        fill_qty: int = 0,
        fill_price: float = 0.0,
        fee: float = 0.0,
    ) -> None:
        """Append an immutable audit entry."""
        from_state = order.audit_trail[-1].to_state if order.audit_trail else "NONE"
        record = OrderAuditRecord(
            timestamp=datetime.now(timezone.utc),
            order_id=order.id,
            symbol=order.symbol,
            from_state=str(from_state),
            to_state=to_state.value,
            event_trigger=trigger,
            reason=reason,
            fill_qty=fill_qty,
            fill_price=fill_price,
            cum_filled_qty=order.filled_qty,
            remaining_qty=order.remaining_qty,
            fee=fee,
            account_cash_after=self.account.cash,
            account_equity_after=self.account.equity,
        )
        order.audit_trail.append(record)
        self.audit_log.append(record)
```

---

## 4. Part III: Module Integration Contracts & Interfaces

### 4.1 Interface with Ingestion Layer (`stock_ws.py`, `vix_client.py`)
- `ExecutionEngine` ingests strongly-typed market data events from Milestone 1's ingestion modules:
  - `BarEvent(symbol, open, high, low, close, volume, timestamp)`
  - `QuoteEvent(symbol, bid, ask, bid_size, ask_size, timestamp)`
  - `TradeEvent(symbol, price, size, timestamp)`
- On every event tick:
  - Updates symbol mark-to-market in `PaperTradingAccount`.
  - Evaluates matching logic for working orders in `ExecutionEngine`.

### 4.2 Interface with Risk Engine & Flattening (`risk.py`, `bracket.py`, `flattening.py`)
- **Pre-trade Validation**:  
  `ExecutionEngine.submit_order` passes `(order, account)` to `RiskEngine.validate_order()`.  
  If the daily loss circuit breaker is active ($DD \ge \$1,500$) or time $\ge 15:45$ ET, validation returns `(False, reason)` and the order is immediately transitioned to `REJECTED`.
- **Circuit Breaker Halt Action**:  
  When `RiskEngine` detects $DD \ge \$1,500.00$, it invokes:
  1. `ExecutionEngine.cancel_all_orders("CIRCUIT_BREAKER_HALT")`.
  2. For every open position in `account.positions`: submits an immediate `MARKET` liquidation order to flatten exposure.
- **Automated Flattening Protocol**:
  - Phase 2 (15:50 ET): calls `ExecutionEngine.cancel_all_orders("EOD_ORDER_PURGE")`.
  - Phase 3 (15:55 ET): generates market liquidation orders for all open positions.
  - Phase 4 (15:58 ET): queries `len(account.positions) == 0`.

### 4.3 Interface with UI WebSocket Server (`backend/app/main.py` Port 8005)
The engine serializes its state into the authoritative `trading_state` payload defined in `PROJECT.md:128`:

```json
{
  "type": "STATE_UPDATE",
  "timestamp": "2026-09-21T09:35:00Z",
  "account": {
    "equity": 50420.50,
    "cash": 48100.00,
    "buying_power": 192400.00,
    "maintenance_margin": 1050.00,
    "daily_pnl": 420.50,
    "daily_pnl_pct": 0.84,
    "is_circuit_broken": false
  },
  "positions": [
    {
      "symbol": "AAPL",
      "side": "LONG",
      "shares": 100,
      "avg_entry_price": 150.25,
      "market_price": 151.10,
      "market_value": 15110.00,
      "cost_basis": 15025.00,
      "unrealized_pnl": 85.00,
      "realized_pnl": 0.00,
      "fees_paid": 0.00
    }
  ],
  "open_orders": [...]
}
```

---

## 5. Part IV: Comprehensive Unit & Integration Test Specifications

### 5.1 Unit Tests for `PaperTradingAccount` (`tests/unit/test_account.py`)

| Test ID | Test Function Name | Tested Scenario | Expected Verification |
|---|---|---|---|
| `UTA-01` | `test_initial_account_state` | Fresh account initialization | `cash == 50000.0`, `equity == 50000.0`, `buying_power == 200000.0`, `positions == {}`, `status == ACTIVE`. |
| `UTA-02` | `test_long_buy_fill` | Execute Long Buy 100 shares @ $150.00 | Cash decreases by $\$15,000.00$, Long position created with 100 shares, $\bar{P}_{\text{entry}} = 150.00$, initial equity unchanged at $\$50,000.00$. |
| `UTA-03` | `test_mark_to_market_long_gain` | AAPL price moves $150.00 \to 155.00$ | $uPnL = +\$500.00$, Total Equity increases to $\$50,500.00$, Buying Power increases dynamically. |
| `UTA-04` | `test_mark_to_market_long_loss` | AAPL price moves $150.00 \to 145.00$ | $uPnL = -\$500.00$, Total Equity decreases to $\$49,500.00$, $DD_{\$} = \$500.00$. |
| `UTA-05` | `test_partial_sell_long` | Sell 40 of 100 shares @ $160.00 | Realized PnL $= 40 \times (160 - 150) - \text{Fee} = \$400.00 - \text{Fee}$. Remaining 60 shares retain $\bar{P}_{\text{entry}} = 150.00$. |
| `UTA-06` | `test_full_sell_long` | Sell remaining 60 shares @ $165.00 | Symbol removed from `positions`. Total realized PnL locked in. Equity matches $C_0 + rPnL - \text{Fees}$. |
| `UTA-07` | `test_short_sell_fill` | Short Sell 100 TSLA @ $200.00 | Cash credited by $+20,000 - \text{Fee}$. Short position created with 100 shares, liability $= \$20,000.00$. Initial equity $= \$50,000 - \text{Fee}$. |
| `UTA-08` | `test_mark_to_market_short` | TSLA price moves $200.00 \to 185.00$ | $uPnL = +100 \times (200 - 185) = +\$1,500.00$. Equity rises to $\$51,500.00$. |
| `UTA-09` | `test_cover_short` | Buy to cover 100 TSLA @ $185.00 | Cash debited by $\$18,500.00$. Position closed. Realized PnL $= +\$1,500 - \text{Fees}$. |
| `UTA-10` | `test_position_flip_long_to_short` | Long 100 AAPL @ $150, sell 150 @ $160 | Closes 100 Long (realizes $+\$1,000$), opens 50 Short with $\bar{P}_{\text{entry}} = \$160.00$. |
| `UTA-11` | `test_finra_4210_dtbp_reduction` | Open $100k Long position | $MMR = \$25,000.00$, $ME = \$25,000.00$, $DTBP = 4 \times \$25k = \$100,000.00$. |
| `UTA-12` | `test_finra_4210_short_mmr_low_price` | Short 1,000 shares of $3.00 stock | Under Rule 4210(f)(10), $MMR = \max(1.0 \times \$3000, 2.50 \times 1000) = \$3,000.00$. |
| `UTA-13` | `test_can_afford_rejection` | Order requiring $250k buying power | `can_afford` returns `(False, "Insufficient Day Trading Buying Power...")`. |
| `UTA-14` | `test_per_position_concentration_cap` | Order exceeding $50k allocation | `can_afford` returns `(False, "Order exceeds per-position concentration cap...")`. |
| `UTA-15` | `test_pdt_sub_25k_margin_restriction` | Equity drops to $24,000.00 | PDT 4:1 leverage revoked; buying power throttles to $1\times$ cash. |

---

### 5.2 Unit Tests for `ExecutionEngine` (`tests/unit/test_engine.py`)

| Test ID | Test Function Name | Tested Scenario | Expected Verification |
|---|---|---|---|
| `UTE-01` | `test_order_fsm_happy_path` | Create -> Submit -> Accept -> Fill | Transitions through all 4 states; audit trail records all transitions with correct timestamps. |
| `UTE-02` | `test_order_fsm_reject_insufficient_bp` | Submit order exceeding DTBP | Order transitions `SUBMITTED -> REJECTED`. Rejection reason logged. |
| `UTE-03` | `test_order_fsm_user_cancel` | Cancel order in ACCEPTED state | Transitions `ACCEPTED -> CANCELLED`. Removed from `working_orders`. |
| `UTE-04` | `test_order_fsm_illegal_transition` | Attempt cancel on FILLED order | Raises `InvalidOrderStateTransitionError`. |
| `UTE-05` | `test_market_order_fill_on_quote` | Submit Market Buy on quote $150.10 \times 150.20$ | Fills at $150.20 + \text{Slippage}$. Account cash deducted. |
| `UTE-06` | `test_limit_order_bar_price_improvement` | Buy Limit at $100.00, bar opens at $98.00 | Fills at $98.00 (price improvement recognized). |
| `UTE-07` | `test_stop_loss_trigger_with_adverse_slippage` | Sell Stop at $148.00, bar Low hits $147.50 | Converts to Market Sell; slippage includes $1.5\times$ adverse penalty. |
| `UTE-08` | `test_partial_fill_volume_participation` | Buy Limit 1,000 shares, bar volume 2,000 | 10% cap $= 200$ shares filled $\to$ `PARTIALLY_FILLED`, 800 remain working. |
| `UTE-09` | `test_partial_fill_then_full_fill` | Remaining 800 shares filled on next bar | Order transitions `PARTIALLY_FILLED -> FILLED`. Avg fill price correctly weighted. |
| `UTE-10` | `test_sec_and_finra_fee_deductions` | Sell 1,000 shares @ $100.00 ($100k) | SEC fee: $\lceil 0.0000278 \times 100,000 \times 100 \rceil / 100 = \$2.78$. FINRA TAF: $1000 \times 0.000166 = \$0.17$. Total fee $= \$2.95$. |
| `UTE-11` | `test_cancel_all_orders` | 3 working orders active, call `cancel_all` | All 3 transition to `CANCELLED`, `working_orders` empty. |
| `UTE-12` | `test_audit_trail_completeness` | Verify audit trail entries | Every transition logs `from_state`, `to_state`, `trigger`, and `account_equity_after`. |

---

## 6. Implementation Blueprint & File Layout Summary

To implement Milestone 1's accounting and execution engine, workers should structure the codebase as follows:

```
backend/
├── app/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── account.py          # PaperTradingAccount state machine & models
│   │   └── engine.py           # ExecutionEngine, Order FSM, and microstructure fill simulator
└── tests/
    └── unit/
        ├── test_account.py     # 15 unit tests covering account & margin math
        └── test_engine.py      # 12 unit tests covering order FSM & fill simulation
```

### Key Implementation Guidelines for Downstream Workers:
1. **Zero External Broker Dependency**: The execution engine and account state machine are completely self-contained. They require zero live external broker connection, making them 100% deterministic and suitable for CI/CD and dry runs.
2. **Strict Floating-Point Precision**: All monetary calculations (`cash`, `equity`, `PnL`, `fees`) must be rounded using `round(val, 2)` or `round(val, 4)` to prevent IEEE 754 floating-point drift.
3. **Atomic Account State Updates**: Position modifications, cash debits/credits, and realized PnL calculations must occur within an atomic block protected by an internal reentrancy lock or synchronous execution in asyncio.
4. **Clean Integration with Risk & Flattening**: Ensure `risk_validator` callbacks and `cancel_all_orders()` methods are cleanly exposed for `risk.py` and `flattening.py` (being designed in parallel by `explorer_m1_3`).

---

**Report complete and validated against PROJECT.md and ORIGINAL_REQUEST.md.**
