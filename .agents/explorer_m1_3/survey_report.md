# Technical Implementation Blueprint: Institutional Risk Guardrails, Dynamic Brackets & 4-Phase Auto-Flattening

**Author**: `explorer_m1_3`  
**Role**: Institutional Risk Guardrails, Circuit Breakers & 4-Phase Auto-Flattening Specialist  
**Target Milestone**: Milestone 1 (`engine_ingestion`)  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/explorer_m1_3`  
**Target Modules**:
- `backend/app/core/risk.py` (Institutional Risk Engine & Circuit Breakers)
- `backend/app/core/bracket.py` (Dynamic Bracket Orders & Trailing Stops)
- `backend/app/core/flattening.py` (Automated 4-Phase Zero-Overnight Flattening)  
**Date**: 2026-09-19  
**Status**: Authoritative Architectural Specification & File-by-File Blueprint  

---

## 1. Executive Summary

This specification establishes the concrete technical design, state machines, algorithmic formulations, class definitions, method signatures, and unit test suites for the three mission-critical risk and execution subsystems of **AutonomousDayTrader**:

1. **Institutional Risk Engine & Real-Time Circuit Breakers (`backend/app/core/risk.py`)**:
   - Enforces a hard, non-negotiable **$1,500 / 3.0% maximum daily loss limit** against starting account equity ($50,000.00).
   - Real-time circuit breaker trips instantaneously when cumulative daily realized + unrealized drawdown $\ge \$1,500.00$.
   - Emergency halt protocol triggers immediate atomic actions: rejects incoming strategy orders, purges all pending/unfilled orders, and dispatches market liquidation orders to close 100% of open positions.
   - Pre-trade risk budgeting enforces **1.0% ($500) to 2.0% ($1,000) equity risk per trade**, computing precise share quantity $q = \lfloor \text{Risk}\$ / |P_{\text{entry}} - P_{\text{stop}}| \rfloor$ constrained by capital allocation caps ($25\%$ max equity per ticker) and 4:1 intraday buying power.

2. **Dynamic Bracket Orders & Trailing Stops (`backend/app/core/bracket.py`)**:
   - Manages atomic **One-Cancels-Other (OCO)** brackets attached to filled entry orders.
   - Implements **Multi-Tier Profit Targets**:
     - **Target 1 at 1.5R**: Scales out $50\%$ of position quantity ($\lfloor q/2 \rfloor$), ratchets remaining stop to breakeven ($P_{\text{entry}} + \text{buffer}$), and modifies remaining working stop quantity.
     - **Target 2 at 2.5R**: Scales out remaining $50\%$ or transitions into an adaptive **ATR Trailing Stop** ($P_{\text{trail}} = \max(P_{\text{trail}, t-1}, P_{\text{peak}} - 1.5 \cdot \text{ATR}_{14})$).
   - Stop-loss execution triggers immediate cancellation of both target limit orders, preventing ghost fills or naked reversal exposure.

3. **Automated 4-Phase Zero-Overnight Flattening (`backend/app/core/flattening.py`)**:
   - Eliminates overnight gap, earnings, and dividend risk through a clock-driven 4-phase closeout protocol:
     - **Phase 1 (15:45 ET) — Entry Lockout**: Blocks all new position entry signals; engine accepts exit orders only.
     - **Phase 2 (15:50 ET) — Working Order Purge**: Cancels all working limit/stop entry orders; tightens existing stops to locked-in profit / breakeven.
     - **Phase 3 (15:55 ET) — Mandatory Market Liquidation**: Dispatches aggressive Market Orders to close 100% of open positions across all tickers.
     - **Phase 4 (15:58 ET) — Zero-Overnight Audit**: Queries position state; asserts $|\mathcal{P}| == 0$. If positions remain, dispatches immediate emergency IOC liquidation retries to guarantee flat book before 16:00 ET close.
   - Supports a decoupled **`MarketClock`** abstraction supporting both live Eastern Time (`America/New_York`) and synthetic replay timestamps for deterministic backtesting and Monday dry-run verification.

---

## 2. Architecture & Component Interaction Topology

```
                                  ┌────────────────────────┐
                                  │ Strategy Signals Layer │
                                  │ (ORB, VWAP, News, MR)  │
                                  └───────────┬────────────┘
                                              │ OrderRequest
                                              ▼
                             ┌─────────────────────────────────┐
                             │    Institutional Risk Engine    │
                             │   (backend/app/core/risk.py)    │
                             │  - 3% / $1,500 Circuit Breaker  │
                             │  - 1-2% Per-Trade Risk Budget   │
                             │  - 25% Concentration Cap        │
                             │  - Max 3 Concurrent Positions   │
                             └────────────────┬────────────────┘
                                              │ Approved Order
                                              ▼
                             ┌─────────────────────────────────┐
                             │       Trading Engine Core       │
                             │   (backend/app/core/engine.py)  │
                             └──────────┬───────────┬──────────┘
                                        │           │
                     Order Filled       │           │ Periodic Tick / Bar
                          ┌─────────────┘           └─────────────┐
                          ▼                                       ▼
       ┌──────────────────────────────────────┐  ┌───────────────────────────────────┐
       │       Dynamic Bracket Manager        │  │ 4-Phase Zero-Overnight Flattening │
       │    (backend/app/core/bracket.py)     │  │  (backend/app/core/flattening.py) │
       │ - Target 1: 1.5R (50% Scale-Out)     │  │ - 15:45 ET: Entry Lockout         │
       │ - Breakeven Ratchet (+0.02 Buffer)   │  │ - 15:50 ET: Working Order Purge   │
       │ - Target 2: 2.5R or ATR Trail Stop   │  │ - 15:55 ET: Mandatory Liquidation │
       │ - OCO Cancellation Coordination      │  │ - 15:58 ET: Zero Position Audit   │
       └──────────────────┬───────────────────┘  └─────────────────┬─────────────────┘
                          │                                        │
                          └───────────────────┬────────────────────┘
                                              │ Liquidations / Adjustments
                                              ▼
                             ┌─────────────────────────────────┐
                             │      Paper Trading Account      │
                             │   (backend/app/core/account.py) │
                             │  - $50k Cash / 4:1 Buying Power │
                             │  - Realized & Unrealized PnL    │
                             │  - Mark-to-Market Ledger        │
                             └─────────────────────────────────┘
```

---

## 3. Module 1 Blueprint: Institutional Risk Engine (`backend/app/core/risk.py`)

### 3.1 Mathematical Formulations

#### 1. Daily Realized + Unrealized Drawdown Calculation:
Let $E_0 = \$50,000.00$ be the starting equity for the intraday trading session.  
At any time $t$:
$$E_t = \text{Cash}_t + \sum_{s \in \mathcal{P}_t} \left( q_s \cdot P_{\text{entry}, s} + uPnL_s(t) \right)$$
$$\text{Daily PnL}_t = E_t - E_0 = rPnL_t + uPnL_t - \text{Total Fees}_t$$
$$\text{Daily Drawdown}_{\$} = \max(0.0, E_0 - E_t)$$
$$\text{Drawdown Pct}_t = \frac{\text{Daily Drawdown}_{\$}}{E_0} \times 100\%$$

#### 2. Circuit Breaker Invariant:
$$\text{Daily Drawdown}_{\$} \ge \$1,500.00 \iff \text{Drawdown Pct}_t \ge 3.0\% \implies \text{HALT\_SESSION}$$
Upon condition satisfaction, transition immediately to `BreakerStatus.HALTED_DAILY_LOSS`.

#### 3. Per-Position Sizing Formula:
Given an entry price $P_{\text{entry}}$, stop-loss price $P_{\text{stop}}$, account equity $E_t$, and risk tier $\rho \in [0.01, 0.02]$ (default $1.0\% = \$500$ on $\$50\text{k}$):
$$\Delta P_{\text{risk}} = |P_{\text{entry}} - P_{\text{stop}}|$$
$$\text{Stop Distance Pct} = \frac{\Delta P_{\text{risk}}}{P_{\text{entry}}}$$
Enforce stop distance safety boundaries:
$$0.004 \le \text{Stop Distance Pct} \le 0.040 \quad (0.4\% \le \Delta P_{\text{risk}} \le 4.0\%)$$
If $\text{Stop Distance Pct} < 0.004$, reject: stop is too tight, creating excessive slippage hazard and artificial sizing inflation.  
If $\text{Stop Distance Pct} > 0.040$, reject: setup violates day-trading risk-reward geometry.

Maximum allowable dollar loss for position:
$$R_{\$, \text{target}} = E_t \times \rho \times K_{\text{vix}}$$
where $K_{\text{vix}} \in [0.35, 1.20]$ is the VIX regime multiplier.  
Hard clamp: $R_{\$, \text{target}} \le \$1,000.00$.

Calculated share quantity:
$$q_{\text{risk}} = \left\lfloor \frac{R_{\$, \text{target}}}{\Delta P_{\text{risk}}} \right\rfloor$$
Maximum concentration cap ($25\%$ maximum capital allocation to a single ticker):
$$q_{\text{alloc}} = \left\lfloor \frac{E_t \times 0.25}{P_{\text{entry}}} \right\rfloor$$
Buying power limit ($BP_t = 4 \times E_t$ intraday):
$$q_{\text{bp}} = \left\lfloor \frac{BP_t}{P_{\text{entry}}} \right\rfloor$$
Final authorized share quantity:
$$q^* = \min(q_{\text{risk}}, q_{\text{alloc}}, q_{\text{bp}})$$
If $q^* < 1$: Reject order (insufficient capital or stop distance exceeds risk budget).

#### 4. Portfolio Concurrency Caps:
- **Maximum Concurrent Open Positions**: $N_{\text{max}} = 3$. Total simultaneous portfolio risk is bounded by $3 \times \$500 = \$1,500.00$, perfectly matched with the daily loss limit.
- **Sector Isolation Limit**: Maximum 1 open position per market sector (e.g. Technology, Consumer Discretionary, Communication Services) to prevent correlated drawdown.

---

### 3.2 Data Models & Enums (`backend/app/core/risk.py`)

```python
from enum import Enum
from typing import Dict, Optional, List, Set, Tuple
from pydantic import BaseModel, Field
from datetime import datetime

class RiskLevel(str, Enum):
    NORMAL = "NORMAL"          # Drawdown < 2.0% ($1,000)
    WARNING = "WARNING"        # Drawdown >= 2.0% and < 3.0% ($1,000 - $1,499.99)
    HALTED = "HALTED"          # Drawdown >= 3.0% ($1,500.00)

class BreakerStatus(str, Enum):
    ARMED = "ARMED"                      # Normal operational state
    TRIGGERED = "TRIGGERED"              # Circuit breaker tripped, liquidation in flight
    HALTED_DAILY_LOSS = "HALTED_DAILY_LOSS"  # Emergency halt active, all trading frozen for session

class RiskCheckResult(BaseModel):
    approved: bool
    reason: str
    requested_qty: int
    authorized_qty: int
    estimated_risk_dollars: float
    risk_level: RiskLevel
    rejection_code: Optional[str] = None

class RiskEngineConfig(BaseModel):
    starting_equity: float = 50000.00
    hard_max_daily_loss_dollars: float = 1500.00
    hard_max_daily_loss_pct: float = 0.030
    warning_loss_dollars: float = 1000.00
    warning_loss_pct: float = 0.020
    base_trade_risk_pct: float = 0.010       # 1.0% ($500)
    max_trade_risk_pct: float = 0.020        # 2.0% ($1,000 ceiling)
    max_trade_risk_dollars: float = 1000.00
    max_position_equity_pct: float = 0.250   # 25% max capital per position
    max_concurrent_positions: int = 3
    min_stop_distance_pct: float = 0.004     # 0.4%
    max_stop_distance_pct: float = 0.040     # 4.0%
```

---

### 3.3 Class Definition & Method Signatures: `InstitutionalRiskEngine`

```python
class InstitutionalRiskEngine:
    """
    Autonomous Institutional Risk Gatekeeper & Real-Time Circuit Breaker.
    
    Acts as a mandatory synchronous pre-trade validation gate between Strategy Signal
    generation and Order Execution. Continuously monitors mark-to-market portfolio drawdown
    and enforces immediate emergency liquidation upon breach of the $1,500 daily loss limit.
    """

    def __init__(self, config: Optional[RiskEngineConfig] = None) -> None:
        """
        Initialize the Institutional Risk Engine with configuration and zeroed intraday metrics.
        """
        self.config: RiskEngineConfig = config or RiskEngineConfig()
        self.status: BreakerStatus = BreakerStatus.ARMED
        self.risk_level: RiskLevel = RiskLevel.NORMAL
        self.daily_peak_equity: float = self.config.starting_equity
        self.current_drawdown_dollars: float = 0.0
        self.current_drawdown_pct: float = 0.0
        self.breaker_triggered_at: Optional[datetime] = None
        self.breaker_trigger_equity: Optional[float] = None
        self.symbol_sectors: Dict[str, str] = {}  # Symbol -> Sector mapping

    def register_symbol_sector(self, symbol: str, sector: str) -> None:
        """Register sector classification for symbol correlation enforcement."""
        ...

    def evaluate_account_state(
        self,
        equity: float,
        cash: float,
        realized_pnl: float,
        unrealized_pnl: float,
        timestamp: datetime
    ) -> BreakerStatus:
        """
        Evaluate real-time portfolio metrics on every incoming tick, quote, or bar.
        
        Calculates daily drawdown = max(0, starting_equity - equity).
        Updates internal risk level (NORMAL -> WARNING -> HALTED).
        If daily loss >= $1,500.00, immediately transitions status to TRIGGERED / HALTED_DAILY_LOSS
        and returns BreakerStatus.HALTED_DAILY_LOSS.
        """
        ...

    def evaluate_order_request(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        requested_qty: int,
        entry_price: float,
        stop_price: float,
        account_equity: float,
        buying_power: float,
        active_positions_count: int,
        active_symbols: Set[str],
        active_sectors: Set[str],
        vix_multiplier: float = 1.0,
        is_entry_lockout_active: bool = False
    ) -> RiskCheckResult:
        """
        Pre-Trade Approval Gate.
        
        Evaluates an order request against 7 institutional guardrails:
        1. Circuit Breaker Check: Reject if status != BreakerStatus.ARMED.
        2. Session Time Check: Reject if is_entry_lockout_active is True.
        3. Concurrency Limit Check: Reject if active_positions_count >= max_concurrent_positions.
        4. Sector Diversification Check: Reject if symbol sector is already active in portfolio.
        5. Stop Distance Safety Boundary Check: Enforce 0.4% <= stop_pct <= 4.0%.
        6. Risk-Adjusted Sizing Calculation: Compute q* based on 1.0% equity risk ($500),
           25% max position concentration, and buying power.
        7. Buying Power Sufficiency: Ensure q* * entry_price <= buying_power.
        
        Returns RiskCheckResult with approval boolean, authorized share quantity, and rejection reason.
        """
        ...

    def trip_circuit_breaker(
        self,
        current_equity: float,
        drawdown_dollars: float,
        timestamp: datetime,
        reason: str = "MAX_DAILY_LOSS_EXCEEDED"
    ) -> Dict[str, any]:
        """
        Execute emergency circuit breaker protocol.
        
        Transitions state to HALTED_DAILY_LOSS.
        Returns liquidation directive payload for the trading engine containing:
        - action: "EMERGENCY_HALT"
        - purge_orders: True
        - liquidate_positions: True
        - timestamp: datetime
        - reason: reason
        """
        ...

    def reset_daily_metrics(self, new_starting_equity: float) -> None:
        """
        Reset intraday metrics at market open (09:30 ET) for a new trading day.
        Can only be executed if current time is outside trading hours or after session close.
        """
        ...
```

---

### 3.4 State Machine & Circuit Breaker Execution Flow

```
                     +---------------------------------------+
                     |         BreakerStatus.ARMED           |
                     |  - Normal order routing allowed       |
                     |  - Drawdown < $1,000 (2.0%)           |
                     |  - RiskLevel.NORMAL                   |
                     +-------------------+-------------------+
                                         |
                       Drawdown >= $1,000 & < $1,500
                                         |
                                         v
                     +---------------------------------------+
                     |         RiskLevel.WARNING             |
                     |  - BreakerStatus remains ARMED        |
                     |  - Sizing throttled to 1.0% ($500 max)|
                     |  - UI Warning Toast emitted           |
                     +-------------------+-------------------+
                                         |
                               Drawdown >= $1,500.00
                                         |
                                         v
                     +---------------------------------------+
                     |       BreakerStatus.TRIGGERED         |
                     |  - High-priority audit alert raised   |
                     |  - Lockout initiated atomically       |
                     +-------------------+-------------------+
                                         |
                    Atomic Liquidation & Purge Sequence
                                         |
                                         v
                     +---------------------------------------+
                     |    BreakerStatus.HALTED_DAILY_LOSS    |
                     |  1. Reject all new orders             |
                     |  2. Purge all working orders          |
                     |  3. Market liquidate open positions   |
                     |  4. Lockout until next session open   |
                     +---------------------------------------+
```

---

## 4. Module 2 Blueprint: Dynamic Bracket Orders & Trailing Stops (`backend/app/core/bracket.py`)

### 4.1 Mathematical Formulations

#### 1. Stop Distance & R-Multiple Sizing:
For an executed fill at average price $\bar{P}_{\text{entry}}$ with technical stop price $P_{\text{stop}}$:
$$R = |\bar{P}_{\text{entry}} - P_{\text{stop}}|$$
$$\text{Directional Sign: } s = \begin{cases} +1 & \text{if LONG} \\ -1 & \text{if SHORT} \end{cases}$$

#### 2. Multi-Tier Target Geometry:
- **Target 1 ($1.5R$)**:
  $$\text{Target}_1 = \bar{P}_{\text{entry}} + s \cdot (1.5 \times R)$$
  $$\text{Scale-Out Quantity: } q_1 = \lfloor q_{\text{total}} / 2 \rfloor$$
  $$\text{Remaining Quantity: } q_2 = q_{\text{total}} - q_1$$
  *(Note: Handles odd share quantities deterministically. Example: $q=5 \implies q_1=2, q_2=3$)*.

- **Breakeven Stop Ratchet (Upon Target 1 Fill)**:
  $$P_{\text{stop, breakeven}} = \bar{P}_{\text{entry}} + s \cdot \text{buffer}$$
  where $\text{buffer} = \$0.02$ (covers exchange fees, SEC/TAF fees, and slippage buffer).
  Invariant: $P_{\text{stop, breakeven}}$ is strictly higher than initial stop for Long, and strictly lower for Short.

- **Target 2 ($2.5R$ or Trailing Stop)**:
  $$\text{Target}_2 = \bar{P}_{\text{entry}} + s \cdot (2.5 \times R)$$
  If strategy enables trailing stop mode for Target 2:
  For Long position:
  $$P_{\text{peak}, t} = \max(P_{\text{peak}, t-1}, \text{High}_t)$$
  $$P_{\text{trail}, t} = \max\left(P_{\text{stop, current}}, P_{\text{peak}, t} - 1.5 \times \text{ATR}_{14}(1\text{m})\right)$$
  For Short position:
  $$P_{\text{trough}, t} = \min(P_{\text{trough}, t-1}, \text{Low}_t)$$
  $$P_{\text{trail}, t} = \min\left(P_{\text{stop, current}}, P_{\text{trough}, t} + 1.5 \times \text{ATR}_{14}(1\text{m})\right)$$
  **Monotonicity Invariant**: Trailing stop strictly tightens. It **never** loosens or widens under any market condition.

#### 3. OCO Cancellation Invariants:
- If `STOP_LOSS` fills $\implies$ atomically cancel `TARGET_1` and `TARGET_2`.
- If `TARGET_1` fills $\implies$ modify stop order quantity to $q_2$, ratchet stop price to $P_{\text{stop, breakeven}}$.
- If `TARGET_2` fills $\implies$ cancel remaining `STOP_LOSS` order for $q_2$.
- If `MANUAL_FLATTEN` or `EOD_FLATTEN` is triggered $\implies$ cancel all working child orders, close position via market order.

---

### 4.2 Data Models & Enums (`backend/app/core/bracket.py`)

```python
from enum import Enum
from typing import Optional, Dict, List
from pydantic import BaseModel, Field
from datetime import datetime

class BracketStatus(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"      # Entry order submitted, awaiting fill
    ACTIVE = "ACTIVE"                    # Position open, OCO bracket orders live
    TARGET_1_HIT = "TARGET_1_HIT"        # Scaled out 50%, stop ratcheted to breakeven
    COMPLETED_PROFIT = "COMPLETED_PROFIT"# Both targets hit or trailed to completion
    COMPLETED_STOP = "COMPLETED_STOP"    # Stop loss executed
    COMPLETED_FLATTEN = "COMPLETED_FLATTEN" # Flattened manually or by EOD protocol
    CANCELLED = "CANCELLED"              # Entry cancelled before fill

class BracketChildType(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT_1 = "TAKE_PROFIT_1"
    TAKE_PROFIT_2 = "TAKE_PROFIT_2"

class BracketOrder(BaseModel):
    bracket_id: str
    symbol: str
    side: str                            # "LONG" or "SHORT"
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
    r_distance: float
    status: BracketStatus = BracketStatus.PENDING_ENTRY
    use_trailing_target_2: bool = True
    trail_atr_multiplier: float = 1.5
    peak_price_since_entry: float
    stop_order_id: Optional[str] = None
    target_1_order_id: Optional[str] = None
    target_2_order_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class BracketUpdateDirective(BaseModel):
    action: str  # "SUBMIT_ORDERS", "CANCEL_ORDER", "MODIFY_ORDER", "NO_ACTION"
    orders_to_cancel: List[str] = Field(default_factory=list)
    orders_to_submit: List[Dict[str, any]] = Field(default_factory=list)
    orders_to_modify: List[Dict[str, any]] = Field(default_factory=list)
    bracket_status: BracketStatus
```

---

### 4.3 Class Definition & Method Signatures: `DynamicBracketManager`

```python
class DynamicBracketManager:
    """
    Manages dynamic multi-tier profit targets and OCO trailing bracket orders.
    
    Coordinates the lifecycle of exit orders linked to open positions:
    - Target 1 (1.5R, 50% scale-out)
    - Breakeven stop ratcheting
    - Target 2 (2.5R or ATR trailing stop)
    - OCO order cancellation and size synchronization
    """

    def __init__(self, breakeven_buffer: float = 0.02) -> None:
        self.breakeven_buffer: float = breakeven_buffer
        self.brackets: Dict[str, BracketOrder] = {}        # bracket_id -> BracketOrder
        self.symbol_to_bracket: Dict[str, str] = {}        # symbol -> bracket_id
        self.order_to_bracket: Dict[str, Tuple[str, BracketChildType]] = {} # order_id -> (bracket_id, type)

    def create_bracket(
        self,
        bracket_id: str,
        symbol: str,
        side: str,
        total_qty: int,
        entry_price: float,
        stop_price: float,
        strategy_id: str,
        use_trailing_target_2: bool = True,
        trail_atr_multiplier: float = 1.5,
        timestamp: Optional[datetime] = None
    ) -> BracketOrder:
        """
        Create and compute price levels for a dynamic multi-tier bracket.
        Calculates 1.5R Target 1, 2.5R Target 2, and initial stop order geometry.
        """
        ...

    def activate_bracket_on_fill(
        self,
        bracket_id: str,
        filled_qty: int,
        fill_price: float,
        timestamp: datetime
    ) -> BracketUpdateDirective:
        """
        Invoked when the parent entry order fills.
        Transitions bracket status to ACTIVE.
        Generates directives to submit initial Stop-Loss order (total_qty)
        and Target 1 limit order (target_1_qty).
        """
        ...

    def on_child_order_fill(
        self,
        order_id: str,
        fill_price: float,
        filled_qty: int,
        timestamp: datetime
    ) -> BracketUpdateDirective:
        """
        Handles execution of child bracket orders (OCO synchronization):
        1. If Stop-Loss fills:
           - Immediately cancel Target 1 and Target 2 limit orders.
           - Mark bracket status as COMPLETED_STOP.
        2. If Target 1 fills:
           - Mark target_1_filled = True.
           - Update bracket status to TARGET_1_HIT.
           - Ratchet stop price to breakeven (entry_price + buffer).
           - Modify working Stop-Loss order quantity to remaining_qty (target_2_qty).
           - Submit Target 2 limit order or initialize ATR trailing stop monitor.
        3. If Target 2 fills:
           - Cancel remaining working Stop-Loss order.
           - Mark bracket status as COMPLETED_PROFIT.
        """
        ...

    def update_trailing_stop(
        self,
        symbol: str,
        current_bar_high: float,
        current_bar_low: float,
        current_atr: float,
        timestamp: datetime
    ) -> Optional[BracketUpdateDirective]:
        """
        Update trailing stop level for active brackets on every 1-minute bar.
        For LONG: updates peak price; if peak - (1.5 * ATR) > current_stop, ratchets stop up.
        For SHORT: updates trough price; if trough + (1.5 * ATR) < current_stop, ratchets stop down.
        Enforces strict upward/downward monotonicity.
        """
        ...

    def manual_tighten_stop(
        self,
        symbol: str,
        new_stop_price: float
    ) -> BracketUpdateDirective:
        """
        Handle manual UI override to tighten stop.
        Validates that new stop price strictly protects more capital than current stop.
        Issues order modification directive for active stop order.
        """
        ...

    def cancel_bracket_for_flattening(
        self,
        symbol: str,
        reason: str = "EOD_FLATTEN"
    ) -> BracketUpdateDirective:
        """
        Cancel all active child bracket orders for a symbol in preparation for market flattening.
        Marks bracket as COMPLETED_FLATTEN.
        """
        ...
```

---

## 5. Module 3 Blueprint: Automated 4-Phase Zero-Overnight Flattening (`backend/app/core/flattening.py`)

### 5.1 The 4-Phase Closeout Protocol

Day trading mandates **strictly zero overnight exposure** to eliminate overnight gap risk, unhedgeable earnings releases, dividend adjustments, and overnight leverage borrowing fees.

| Phase | Scheduled Time (ET) | Phase Identifier | State Invariants & Mandatory Execution Directives |
|---|---|---|---|
| **Phase 1** | **15:45:00 ET** | `ENTRY_LOCKOUT` | **Block New Positions.** Strategy entry signals are blocked and rejected. Only position-closing reductions and stop/target modifications are accepted. |
| **Phase 2** | **15:50:00 ET** | `ORDER_PURGE` | **Cancel Working Limit/Entry Orders.** All working unfilled limit orders are cancelled. Bracket stops for profitable positions are ratcheted to lock in at least 0.5R or breakeven. |
| **Phase 3** | **15:55:00 ET** | `MANDATORY_LIQUIDATION` | **Forced Market Flattening.** Active bracket orders are cancelled. Aggressive Market Orders (`IOC`) are submitted to close 100% of all open positions across all tickers. |
| **Phase 4** | **15:58:00 ET** | `ZERO_AUDIT` | **Zero-Overnight Position Audit.** Verify `len(account.positions) == 0`. If any residual position is detected (e.g. partial fill or unacknowledged execution), fire immediate emergency market sweep order. |
| **Close** | **16:00:00 ET** | `MARKET_CLOSED` | **Session Close & Final Ledger.** Confirm 100% Cash balance ($0 open exposure). Record final session metrics and Sharpe ratio. |

---

### 5.2 Market Clock Abstraction (`backend/app/core/flattening.py`)

To ensure deterministic testing, historical replay, and Monday live dry-run accuracy, the flattening engine decouples time checks from the system wall clock using a `MarketClock`:

```python
from zoneinfo import ZoneInfo
from datetime import datetime, time

ET_TZ = ZoneInfo("America/New_York")

class MarketClock:
    """
    Market Clock providing Eastern Time (America/New_York) awareness.
    Supports both live wall-clock operation and deterministic simulated time replay.
    """
    def __init__(self, simulated_time: Optional[datetime] = None) -> None:
        self._simulated_time: Optional[datetime] = simulated_time

    def set_simulated_time(self, dt: datetime) -> None:
        """Set simulated market time for backtesting, dry-runs, and replay."""
        if dt.tzinfo is None:
            self._simulated_time = dt.replace(tzinfo=ET_TZ)
        else:
            self._simulated_time = dt.astimezone(ET_TZ)

    def now(self) -> datetime:
        """Return current market time in US Eastern timezone."""
        if self._simulated_time is not None:
            return self._simulated_time
        return datetime.now(ET_TZ)

    def current_time_et(self) -> time:
        """Return current time-of-day in ET."""
        return self.now().time()
```

---

### 5.3 Data Models & Enums (`backend/app/core/flattening.py`)

```python
from enum import Enum
from typing import Optional, List, Dict, Callable
from pydantic import BaseModel
from datetime import time, datetime

class FlatteningPhase(str, Enum):
    NORMAL_TRADING = "NORMAL_TRADING"         # 09:30:00 - 15:44:59 ET
    ENTRY_LOCKOUT = "ENTRY_LOCKOUT"           # 15:45:00 - 15:49:59 ET (Phase 1)
    ORDER_PURGE = "ORDER_PURGE"               # 15:50:00 - 15:54:59 ET (Phase 2)
    MANDATORY_LIQUIDATION = "MANDATORY_LIQUIDATION" # 15:55:00 - 15:57:59 ET (Phase 3)
    ZERO_AUDIT = "ZERO_AUDIT"                 # 15:58:00 - 15:59:59 ET (Phase 4)
    MARKET_CLOSED = "MARKET_CLOSED"           # 16:00:00+ ET

class FlatteningDirective(BaseModel):
    phase: FlatteningPhase
    timestamp: datetime
    action_required: str
    cancel_all_orders: bool = False
    liquidate_all_positions: bool = False
    lock_new_entries: bool = False
    run_audit: bool = False
    audit_passed: Optional[bool] = None
    unclosed_symbols: List[str] = []

class FlatteningSchedule(BaseModel):
    phase1_lockout_time: time = time(15, 45, 0)
    phase2_purge_time: time = time(15, 50, 0)
    phase3_liquidation_time: time = time(15, 55, 0)
    phase4_audit_time: time = time(15, 58, 0)
    market_close_time: time = time(16, 0, 0)
```

---

### 5.4 Class Definition & Method Signatures: `ZeroOvernightFlatteningEngine`

```python
class ZeroOvernightFlatteningEngine:
    """
    Automated 4-Phase Zero-Overnight Flattening State Machine.
    
    Guarantees that no stock positions are held overnight by executing a deterministic,
    phased wind-down sequence beginning at 15:45 ET and verifying a flat portfolio by 15:58 ET.
    """

    def __init__(
        self,
        clock: Optional[MarketClock] = None,
        schedule: Optional[FlatteningSchedule] = None
    ) -> None:
        self.clock: MarketClock = clock or MarketClock()
        self.schedule: FlatteningSchedule = schedule or FlatteningSchedule()
        self.current_phase: FlatteningPhase = FlatteningPhase.NORMAL_TRADING
        self.phase1_executed: bool = False
        self.phase2_executed: bool = False
        self.phase3_executed: bool = False
        self.phase4_executed: bool = False
        self.audit_passed: bool = False
        self.audit_retries: int = 0

    def check_time_tick(
        self,
        current_time_override: Optional[datetime] = None
    ) -> Optional[FlatteningDirective]:
        """
        Evaluate time against flattening schedule on every bar or timer tick.
        Detects phase transitions and returns the mandatory execution directive.
        """
        ...

    def execute_phase_1_lockout(self) -> FlatteningDirective:
        """
        Phase 1 (15:45 ET): Entry Lockout.
        Returns directive instructing Trading Engine to reject all subsequent strategy entry signals.
        """
        ...

    def execute_phase_2_purge(self) -> FlatteningDirective:
        """
        Phase 2 (15:50 ET): Working Order Purge.
        Returns directive instructing Trading Engine to cancel all working entry/limit orders
        and tighten stops on profitable open positions.
        """
        ...

    def execute_phase_3_liquidation(self) -> FlatteningDirective:
        """
        Phase 3 (15:55 ET): Mandatory Market Liquidation.
        Returns directive instructing Trading Engine to immediately submit market orders
        closing 100% of open positions.
        """
        ...

    def execute_phase_4_audit(
        self,
        open_positions: Dict[str, any],
        working_orders: List[any]
    ) -> FlatteningDirective:
        """
        Phase 4 (15:58 ET): Zero-Overnight Position Audit.
        Verifies open positions count == 0 and working orders count == 0.
        If positions exist, issues emergency IOC market liquidation directive.
        If flat, certifies audit passed for session close.
        """
        ...

    def reset_for_new_session(self) -> None:
        """Reset phase execution flags for the next trading day."""
        ...
```

---

## 6. Integration Contracts & Cross-Cutting Architecture

### 6.1 Integration with Trading Engine (`backend/app/core/engine.py`)

The `TradingEngine` coordinates all three components inside its central event processing loop:

```python
class TradingEngine:
    def __init__(self, ...):
        self.account: PaperTradingAccount = ...
        self.risk_engine: InstitutionalRiskEngine = InstitutionalRiskEngine()
        self.bracket_manager: DynamicBracketManager = DynamicBracketManager()
        self.flattening_engine: ZeroOvernightFlatteningEngine = ZeroOvernightFlatteningEngine()
        self.accepting_entries: bool = True

    async def on_bar(self, bar: BarEvent) -> None:
        # 1. Update Flattening Clock & Check Scheduled Phases
        self.flattening_engine.clock.set_simulated_time(bar.timestamp)
        directive = self.flattening_engine.check_time_tick()
        if directive:
            await self._handle_flattening_directive(directive)

        # 2. Update Mark-to-Market Portfolio in Account
        account_state = self.account.on_price_update(bar.symbol, bar.close)

        # 3. Real-Time Circuit Breaker Drawdown Check
        breaker_status = self.risk_engine.evaluate_account_state(
            equity=account_state.equity,
            cash=account_state.cash,
            realized_pnl=account_state.realized_pnl,
            unrealized_pnl=account_state.unrealized_pnl,
            timestamp=bar.timestamp
        )
        if breaker_status == BreakerStatus.HALTED_DAILY_LOSS:
            await self._execute_emergency_circuit_breaker()
            return

        # 4. Update Dynamic Bracket Trailing Stops
        bracket_directive = self.bracket_manager.update_trailing_stop(
            symbol=bar.symbol,
            current_bar_high=bar.high,
            current_bar_low=bar.low,
            current_atr=bar.atr,
            timestamp=bar.timestamp
        )
        if bracket_directive:
            await self._apply_bracket_directive(bracket_directive)

    async def on_strategy_signal(self, signal: SignalEvent) -> None:
        # Pre-Trade Validation Gate via Risk Engine
        risk_result = self.risk_engine.evaluate_order_request(
            symbol=signal.symbol,
            side=signal.side,
            requested_qty=signal.suggested_qty,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            account_equity=self.account.equity,
            buying_power=self.account.buying_power,
            active_positions_count=len(self.account.positions),
            active_symbols=set(self.account.positions.keys()),
            active_sectors=self.account.get_active_sectors(),
            is_entry_lockout_active=not self.accepting_entries
        )
        if not risk_result.approved:
            logger.warning(f"Order rejected by Risk Engine: {risk_result.reason}")
            return

        # Dispatch approved order
        ...
```

### 6.2 UI Streaming & Manual Intervention Ingestion (Port 8005)

The Risk and Flattening states map directly into the WebSocket UI payload for the Apple Music dashboard:

```json
{
  "type": "STATE_UPDATE",
  "timestamp": "2026-09-21T15:46:00Z",
  "account": {
    "equity": 49100.00,
    "cash": 36500.00,
    "buying_power": 146000.00,
    "daily_drawdown": 900.00,
    "daily_drawdown_pct": 1.80,
    "is_circuit_broken": false,
    "risk_level": "NORMAL"
  },
  "flattening": {
    "current_phase": "ENTRY_LOCKOUT",
    "accepting_entries": false,
    "time_to_next_phase_seconds": 240,
    "audit_passed": false
  },
  "brackets": [
    {
      "bracket_id": "brk_aapl_101",
      "symbol": "AAPL",
      "side": "LONG",
      "entry_price": 150.25,
      "current_stop": 150.27,
      "target_1": 151.45,
      "target_2": 152.25,
      "status": "TARGET_1_HIT",
      "trailing_stop_active": true
    }
  ]
}
```

UI Manual Overrides handled:
- `{"action": "FLATTEN_POSITION", "symbol": "AAPL"}` $\to$ cancels bracket child orders and market-sells position.
- `{"action": "FLATTEN_ALL"}` $\to$ immediately triggers Phase 3 market liquidation across all open positions.
- `{"action": "TIGHTEN_STOP", "symbol": "AAPL", "new_stop": 150.80}` $\to$ calls `bracket_manager.manual_tighten_stop()`.

---

## 7. Comprehensive Unit & Integration Test Specifications

Below is the definitive verification test suite specification covering 100% of the risk engine, circuit breaker, bracket order, and 4-phase auto-flattening mechanics.

### 7.1 Test Matrix

| # | Test Name | Target Module | Condition Tested | Expected Outcome |
|---|---|---|---|---|
| **T1.1** | `test_risk_engine_normal_sizing` | `risk.py` | Account equity $50k, 1% risk ($500), entry $100, stop $98 ($\Delta P = \$2$) | Sizing $q = \lfloor 500 / 2 \rfloor = 250$ shares. Approved. |
| **T1.2** | `test_risk_engine_stop_too_tight` | `risk.py` | Entry $100.00, stop $99.70 ($\Delta P = 0.30\% < 0.4\%$) | Rejected: `STOP_DISTANCE_TOO_TIGHT`. |
| **T1.3** | `test_risk_engine_stop_too_wide` | `risk.py` | Entry $100.00, stop $95.00 ($\Delta P = 5.0\% > 4.0\%$) | Rejected: `STOP_DISTANCE_TOO_WIDE`. |
| **T1.4** | `test_risk_engine_max_concentration_cap` | `risk.py` | Small stop $\Delta P=\$0.10 \implies q=5000$, but $25\%$ equity cap is $\$12,500$ at $\$10$ entry | Sizing clamped to $\lfloor 12500 / 10 \rfloor = 1250$ shares. Approved. |
| **T1.5** | `test_risk_engine_max_concurrent_positions` | `risk.py` | Account has 3 active positions; 4th entry submitted | Rejected: `MAX_CONCURRENT_POSITIONS_REACHED`. |
| **T1.6** | `test_circuit_breaker_warning_threshold` | `risk.py` | Cumulative intraday loss hits $-\$1,050.00$ ($2.1\%$) | Risk level transitions to `WARNING`. Sizing capped at $1.0\%$. |
| **T1.7** | `test_circuit_breaker_emergency_halt_trip` | `risk.py` | Daily loss hits $-\$1,500.01$ ($3.0\%$) | Transitions to `HALTED_DAILY_LOSS`. Directive emits emergency purge & liquidate. |
| **T1.8** | `test_circuit_breaker_rejects_new_orders` | `risk.py` | Order submitted after circuit breaker has tripped | Rejected: `CIRCUIT_BREAKER_HALTED`. |
| **T2.1** | `test_bracket_creation_multi_tier` | `bracket.py` | Long entry $100, stop $98 ($R=\$2$), $q=100$ | Target 1 = $103.00 (1.5R, 50 shares), Target 2 = $105.00 (2.5R, 50 shares). |
| **T2.2** | `test_bracket_target_1_fill_and_breakeven_ratchet` | `bracket.py` | Target 1 fills at $103.00 | 50 shares closed; stop order updated to 50 shares, price ratcheted to $\$100.02$. |
| **T2.3** | `test_bracket_stop_fill_cancels_targets` | `bracket.py` | Initial stop order fills at $98.00 | Bracket marked `COMPLETED_STOP`. Target 1 and Target 2 orders cancelled. |
| **T2.4** | `test_bracket_trailing_stop_monotonicity` | `bracket.py` | Stock rallies to $104 \to$ stop trails to $102.50$; stock pulls back to $103 \to$ stop stays $102.50$ | Stop never loosens downward. |
| **T2.5** | `test_bracket_odd_quantity_split` | `bracket.py` | Total shares $q=7$, entry $50, stop $48 | Target 1 qty = 3, Target 2 qty = 4. Total = 7. |
| **T3.1** | `test_flattening_phase_1_entry_lockout` | `flattening.py` | Simulated clock reaches 15:45:00 ET | Phase transitions to `ENTRY_LOCKOUT`. Entry signals blocked. |
| **T3.2** | `test_flattening_phase_2_order_purge` | `flattening.py` | Simulated clock reaches 15:50:00 ET | Phase transitions to `ORDER_PURGE`. Working limit orders cancelled. |
| **T3.3** | `test_flattening_phase_3_mandatory_liquidation` | `flattening.py` | Simulated clock reaches 15:55:00 ET | Phase transitions to `MANDATORY_LIQUIDATION`. Market orders dispatched for open positions. |
| **T3.4** | `test_flattening_phase_4_zero_audit_pass` | `flattening.py` | Simulated clock reaches 15:58:00 ET with 0 open positions | Phase transitions to `ZERO_AUDIT`. Returns `audit_passed = True`. |
| **T3.5** | `test_flattening_phase_4_zero_audit_retry_on_leak` | `flattening.py` | Simulated clock reaches 15:58:00 ET with 1 open position lingering | Emits emergency IOC market liquidation retry directive. |

---

### 7.2 Unit Test Implementation Blueprints (`tests/unit/`)

#### 1. `tests/unit/test_risk.py`
```python
import pytest
from datetime import datetime, timezone
from backend.app.core.risk import (
    InstitutionalRiskEngine,
    RiskEngineConfig,
    RiskLevel,
    BreakerStatus
)

def test_risk_sizing_exact_calculation():
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
        active_sectors=set()
    )
    assert res.approved is True
    assert res.authorized_qty == 250
    assert res.estimated_risk_dollars == 500.00

def test_circuit_breaker_hard_halt_at_1500_loss():
    engine = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
    # Simulate equity drawdown to $48,499.00 ($1,501.00 loss)
    now = datetime.now(timezone.utc)
    status = engine.evaluate_account_state(
        equity=48499.00,
        cash=48499.00,
        realized_pnl=-1501.00,
        unrealized_pnl=0.0,
        timestamp=now
    )
    assert status == BreakerStatus.HALTED_DAILY_LOSS
    assert engine.status == BreakerStatus.HALTED_DAILY_LOSS

    # Subsequent order request MUST be rejected
    res = engine.evaluate_order_request(
        symbol="NVDA",
        side="BUY",
        requested_qty=100,
        entry_price=120.00,
        stop_price=118.00,
        account_equity=48499.00,
        buying_power=190000.00,
        active_positions_count=0,
        active_symbols=set(),
        active_sectors=set()
    )
    assert res.approved is False
    assert "CIRCUIT_BREAKER" in res.reason
```

#### 2. `tests/unit/test_bracket.py`
```python
import pytest
from datetime import datetime, timezone
from backend.app.core.bracket import DynamicBracketManager, BracketStatus

def test_bracket_geometry_and_scale_out():
    manager = DynamicBracketManager(breakeven_buffer=0.02)
    # Entry 100.00, Stop 98.00 -> R = 2.00, Target 1 = 103.00, Target 2 = 105.00
    brk = manager.create_bracket(
        bracket_id="b1",
        symbol="TSLA",
        side="LONG",
        total_qty=100,
        entry_price=100.00,
        stop_price=98.00,
        strategy_id="orb"
    )
    assert brk.target_1_price == 103.00
    assert brk.target_2_price == 105.00
    assert brk.target_1_qty == 50
    assert brk.target_2_qty == 50

    # Simulate parent fill
    now = datetime.now(timezone.utc)
    directive = manager.activate_bracket_on_fill("b1", 100, 100.00, now)
    assert brk.status == BracketStatus.ACTIVE

    # Simulate Target 1 fill at 103.00
    t1_directive = manager.on_child_order_fill(brk.target_1_order_id, 103.00, 50, now)
    assert brk.status == BracketStatus.TARGET_1_HIT
    # Stop ratcheted to entry + 0.02 = 100.02
    assert brk.current_stop_price == 100.02
    assert brk.remaining_qty == 50
```

#### 3. `tests/unit/test_flattening.py`
```python
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from backend.app.core.flattening import (
    ZeroOvernightFlatteningEngine,
    MarketClock,
    FlatteningPhase
)

ET = ZoneInfo("America/New_York")

def test_four_phase_flattening_progression():
    clock = MarketClock()
    engine = ZeroOvernightFlatteningEngine(clock=clock)

    # Phase 1: 15:45 ET
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 45, 0, tzinfo=ET))
    d1 = engine.check_time_tick()
    assert d1.phase == FlatteningPhase.ENTRY_LOCKOUT
    assert d1.lock_new_entries is True

    # Phase 2: 15:50 ET
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 50, 0, tzinfo=ET))
    d2 = engine.check_time_tick()
    assert d2.phase == FlatteningPhase.ORDER_PURGE
    assert d2.cancel_all_orders is True

    # Phase 3: 15:55 ET
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 55, 0, tzinfo=ET))
    d3 = engine.check_time_tick()
    assert d3.phase == FlatteningPhase.MANDATORY_LIQUIDATION
    assert d3.liquidate_all_positions is True

    # Phase 4: 15:58 ET (Clean portfolio)
    clock.set_simulated_time(datetime(2026, 9, 21, 15, 58, 0, tzinfo=ET))
    d4 = engine.execute_phase_4_audit(open_positions={}, working_orders=[])
    assert d4.phase == FlatteningPhase.ZERO_AUDIT
    assert d4.audit_passed is True
```

---

## 8. Summary & Next Steps

This survey report delivers a complete, production-grade technical specification for the Risk Engine, Dynamic Brackets, and Zero-Overnight Flattening subsystems. 

Key architectural achievements:
1. **Mathematical Rigor**: Drawdown, invariant risk budgeting, concentration limits, and multi-tier brackets are formulated with closed-form mathematical equations.
2. **Defensive Guarantees**: Emergency circuit breaker halting ($1,500 limit) and 4-phase auto-flattening are specified as deterministic, self-enforcing finite state machines.
3. **Decoupled Testability**: All time and price checks support both live AlpacaRelay market streams and simulated replay timestamps (`MarketClock`), ensuring 100% test automation and successful Monday dry-run certification.
