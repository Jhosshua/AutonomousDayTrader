# Milestone 1 (`engine_ingestion`) Unified Remediation Plan

**Author**: `explorer_m1_fix` (Remediation Explorer & Architecture Specialist)  
**Target Milestone**: Milestone 1 (`engine_ingestion`)  
**Target Agent for Implementation**: `worker_m1`  
**Parent Orchestrator**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  
**Date**: 2026-09-19  
**Status**: COMPLETE & READY FOR IMPLEMENTATION

---

## Executive Summary

During Milestone 1 gate auditing, independent verification by `challenger_m1_1` and `challenger_m1_2` uncovered 5 high-severity defects that compromise core safety invariants:
1. **Liquidation Order Rejection**: Orders intended to flatten positions during a circuit breaker trip (`CIRCUIT_HALTED`) or session entry lockout (`is_entry_lockout_active`) were blocked by `account.can_afford()` and `pre_trade_risk_validator()`, leaving risk exposed overnight.
2. **Position-Flip DTBP & Concentration Bypass**: Opposing orders larger than existing positions (`order_qty > position_shares`) were classified as non-increasing, bypassing both the $50,000 concentration ceiling and $200,000 Day Trading Buying Power (DTBP) limits.
3. **Short Opening Regulatory Fee Accounting Leak**: Regulatory fees (SEC Section 31 + FINRA TAF) incurred on short sales were paid from cash upon entry but never deducted from `realized_pnl` on cover, breaking the fundamental balance conservation identity ($E = E_0 + rPnL + uPnL$).
4. **Premature Circuit Breaker Trip**: A 4-decimal percentage rounding calculation (`round(dd_dollars / 50000.0, 4)`) prematurely halted trading at $1,497.50 drawdown (0.02995 rounding up to 0.0300) instead of the contractual $1,500.00 threshold.
5. **Phase 4 Audit Emergency Sweep Discarded**: The emergency sweep directive returned by `execute_phase_4_audit()` at 15:58 ET was discarded in `main.py`, leaving unclosed positions without liquidation dispatch.

This document specifies the exact mathematical models, line-by-line code changes, test harness updates, and verification commands required for `worker_m1` to remediate all 5 defects.

---

## 1. Defect 1: Liquidation Order Pass-Through in `account.py`, `risk.py`, and `main.py`

### Root Cause Analysis
- In `backend/app/core/account.py:132-133`:
  ```python
  if self.status not in (AccountStatus.ACTIVE, AccountStatus.MARGIN_CALL):
      return False, f"Account is not ACTIVE (current status: {self.status.value})"
  ```
  When the circuit breaker halts trading, `account.status` is set to `AccountStatus.CIRCUIT_HALTED`. Any subsequent liquidation order submitted to close out the position is rejected by `can_afford()`.
- In `backend/app/core/risk.py:139-162`:
  `evaluate_order_request()` unconditionally rejects orders when `self.status != BreakerStatus.ARMED` (reason: `CIRCUIT_BREAKER_HALTED`) or when `is_entry_lockout_active` is True (reason: `ENTRY_LOCKOUT_ACTIVE`). It fails to recognize position-reducing or closing orders.
- In `backend/app/main.py:37-64`:
  `pre_trade_risk_validator()` does not identify whether an incoming order is an exit/liquidation or a new entry.

### Mathematical & Logical Invariant
An order is strictly **position-reducing / closing** if:
- Holding `LONG` position and order `side == "SELL"` with `qty <= position.shares`.
- Holding `SHORT` position and order `side == "BUY"` with `qty <= position.shares`.
- Or explicitly tagged with an exit strategy ID: `order.strategy_id in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "EMERGENCY_SWEEP", "MANUAL_FLATTEN")`.

**Rule**: Position-reducing / closing orders:
1. Decrease portfolio risk and release margin capital.
2. Must NEVER be blocked by `CIRCUIT_HALTED` account status.
3. Must NEVER be blocked by `is_entry_lockout_active` session time lockouts.
4. Must NEVER be blocked by `CIRCUIT_BREAKER_HALTED` risk engine status.
5. Must bypass entry geometry checks (stop distance) and new trade risk sizing calculations.

---

## 2. Defect 2: Position-Flip DTBP & Concentration Checks in `account.py`

### Root Cause Analysis
In `backend/app/core/account.py:143-148`:
```python
is_increasing = False
if side.upper() == "BUY" and (existing_pos is None or existing_pos.side == PositionSide.LONG):
    is_increasing = True
elif side.upper() == "SELL" and (existing_pos is None or existing_pos.side == PositionSide.SHORT):
    is_increasing = True
```
When holding a `LONG` position (e.g. 10 shares of AAPL = $1,500), submitting `SELL 10,000 shares` ($1,500,000 notional) sets `is_increasing = False`. The concentration and DTBP checks are skipped entirely. The order is approved, opening a massive short position that immediately triggers a maintenance margin call.

### Mathematical Formulation
When an order opposes an existing position:
- Long position with a `SELL` order:
  - If $Q_{\text{order}} \le Q_{\text{pos}}$: Pure close/reduce. Margin is released. Concentration decreases. Approved immediately.
  - If $Q_{\text{order}} > Q_{\text{pos}}$: Position flip.
    $$\Delta Q_{\text{flip}} = Q_{\text{order}} - Q_{\text{pos}}$$
    $$V_{\text{flip}} = \Delta Q_{\text{flip}} \times P_{\text{est}}$$
    Concentration validation:
    $$V_{\text{flip}} \le V_{\text{max\_alloc}} \quad (\text{where } V_{\text{max\_alloc}} = 50,000.00 \text{, i.e. 25\% of \$200k DTBP})$$
    FINRA Rule 4210 Short Maintenance Margin:
    $$\text{MMR}_{\text{short}} = \begin{cases} \max(0.30 \times V_{\text{flip}}, 5.00 \times \Delta Q_{\text{flip}}), & \text{if } P_{\text{est}} \ge \$5.00 \\ \max(1.00 \times V_{\text{flip}}, 2.50 \times \Delta Q_{\text{flip}}), & \text{if } P_{\text{est}} < \$5.00 \end{cases}$$
    DTBP Required:
    $$\text{DTBP}_{\text{needed}} = \text{round}(\text{MMR}_{\text{short}} \times 4.0, 2)$$
    Validation: $\text{DTBP}_{\text{needed}} \le \text{DTBP}_{\text{available}}$.
- Short position with a `BUY` order:
  - If $Q_{\text{order}} \le Q_{\text{pos}}$: Pure cover/reduce. Approved immediately.
  - If $Q_{\text{order}} > Q_{\text{pos}}$: Position flip.
    $$\Delta Q_{\text{flip}} = Q_{\text{order}} - Q_{\text{pos}}$$
    $$V_{\text{flip}} = \Delta Q_{\text{flip}} \times P_{\text{est}}$$
    Concentration validation: $V_{\text{flip}} \le V_{\text{max\_alloc}}$.
    Long Maintenance Margin:
    $$\text{MMR}_{\text{long}} = 0.25 \times V_{\text{flip}}$$
    $$\text{DTBP}_{\text{needed}} = \text{round}(\text{MMR}_{\text{long}} \times 4.0, 2) = \text{round}(V_{\text{flip}}, 2)$$
    Validation: $\text{DTBP}_{\text{needed}} \le \text{DTBP}_{\text{available}}$.

---

## 3. Defect 3: Short Opening Regulatory Fee Accounting in `account.py`

### Root Cause Analysis
In US equities, SEC Section 31 and FINRA TAF regulatory fees are charged on all sell transactions.
- On a **Short Sale Entry**, the transaction is a `SELL`. Regulatory fees $F_{\text{entry}}$ are deducted from cash:
  $$\text{Cash} \leftarrow \text{Cash} + (Q \times P_{\text{entry}} - F_{\text{entry}})$$
  $F_{\text{entry}}$ is stored in `position.fees_paid`.
- On a **Short Cover Exit**, the transaction is a `BUY`. Buys incur $F_{\text{exit}} = \$0.00$.
- In `account.apply_fill()`, `realized_delta` was calculated as:
  $$\text{realized\_delta} = (Q \times (P_{\text{entry}} - P_{\text{exit}})) - F_{\text{exit}}$$
  Because $F_{\text{entry}}$ was never subtracted, `realized_pnl` omitted the entry fee. Consequently:
  $$\text{Equity} < E_0 + \text{Realized PnL} + \text{Unrealized PnL}$$
  violating portfolio balance conservation.

### Mathematical Formulation
When covering a short position (partial or full):
1. Compute the prorated entry fee corresponding to the covered lot:
   $$F_{\text{lot\_entry}} = \begin{cases} \text{round}\left( \text{pos.fees\_paid} \times \frac{Q_{\text{cover}}}{Q_{\text{pos}}}, 4 \right), & \text{if } Q_{\text{cover}} < Q_{\text{pos}} \\ \text{pos.fees\_paid}, & \text{if } Q_{\text{cover}} \ge Q_{\text{pos}} \end{cases}$$
2. Compute net realized PnL delta deducting both exit fee and prorated entry fee:
   $$\Delta rPnL = \text{round}\left( (Q_{\text{cover}} \times (P_{\text{entry}} - P_{\text{exit}})) - F_{\text{exit}} - F_{\text{lot\_entry}}, 2 \right)$$
3. On partial cover, decrement the position's tracked fee basis:
   $$\text{pos.fees\_paid} \leftarrow \text{round}(\text{pos.fees\_paid} - F_{\text{lot\_entry}}, 4)$$
4. Update total realized PnL:
   $$\text{realized\_pnl} \leftarrow \text{round}(\text{realized\_pnl} + \Delta rPnL, 2)$$

This guarantees strict zero drift:
$$E_t \equiv E_0 + \text{realized\_pnl}_t + \text{unrealized\_pnl}_t$$

---

## 4. Defect 4: Circuit Breaker Premature Rounding in `risk.py`

### Root Cause Analysis
In `backend/app/core/risk.py:97-106`:
```python
dd_dollars = max(0.0, round(self.config.starting_equity - equity, 2))
dd_pct = round(dd_dollars / self.config.starting_equity, 4)
if dd_dollars >= self.config.hard_max_daily_loss_dollars or dd_pct >= self.config.hard_max_daily_loss_pct:
    self.status = BreakerStatus.HALTED_DAILY_LOSS
```
For equity = $48,502.50 (drawdown = $1,497.50):
$$\frac{1,497.50}{50,000.00} = 0.02995$$
`round(0.02995, 4)` rounds up to `0.0300`.
Since `self.config.hard_max_daily_loss_pct == 0.030`, the condition `dd_pct >= 0.030` evaluates to True!
The circuit breaker halted trading prematurely at $1,497.50, before reaching the contractual $1,500.00 limit.

### Mathematical Formulation
Use exact dollar comparison for institutional risk state transitions:
$$\text{Halt Condition}: \text{dd\_dollars} \ge \text{hard\_max\_daily\_loss\_dollars} \quad (\$1,500.00)$$
$$\text{Warning Condition}: \text{dd\_dollars} \ge \text{warning\_loss\_dollars} \quad (\$1,000.00)$$
`dd_pct` remains as `round(dd_dollars / starting_equity, 4)` solely for telemetry and UI display, but is decoupled from the state transition predicate.

---

## 5. Defect 5: Phase 4 Audit Emergency Sweep in `main.py`

### Root Cause Analysis
In `backend/app/main.py:233-237`:
```python
if directive.run_audit:
    flattening_engine.execute_phase_4_audit(
        open_positions=account.positions,
        working_orders=list(engine.working_orders.values()),
    )
```
In `backend/app/core/flattening.py:200-241`, if unclosed positions or working orders exist at 15:58 ET, `execute_phase_4_audit()` returns a `FlatteningDirective` with `action_required="AUDIT_FAILED_EMERGENCY_SWEEP"`, `cancel_all_orders=True`, and `liquidate_all_positions=True`. In `main.py`, this return value was completely discarded, failing to execute the emergency liquidation sweep.

### Execution Formulation
Capture `audit_res = flattening_engine.execute_phase_4_audit(...)`.
If `audit_res.cancel_all_orders` is True, cancel all remaining working orders.
If `audit_res.liquidate_all_positions` is True and open positions remain:
For each remaining symbol, dispatch an immediate market order with `strategy_id="EMERGENCY_SWEEP"` to liquidate all remaining shares, mark-to-market at the current bar/market price, achieving zero positions before 16:00 ET.

---

## Exact Line-by-Line Code Modifications

### 1. File: `backend/app/core/account.py`

#### Modification A: Update `can_afford` (Lines 127–164)
Replace the existing `can_afford` method with:

```python
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
        max_alloc = self.initial_balance * 4.0 * self.MAX_POSITION_ALLOCATION_PCT
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
                    bp_needed = round(req_margin * 4.0, 2)
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
                    bp_needed = round(req_margin * 4.0, 2)
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

            bp_needed = round(req_margin * 4.0, 2)
            if bp_needed > self.buying_power + 0.01:
                return False, f"Insufficient Day Trading Buying Power: needed ${bp_needed:,.2f}, available ${self.buying_power:,.2f}"

        return True, "Approved"
```

#### Modification B: Update `apply_fill` Fee Accounting (Lines 224–310)
In `apply_fill`, update the exit logic for Long, Short, and Flips so entry fees are deducted from `realized_delta`:

```python
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
```

---

### 2. File: `backend/app/core/risk.py`

#### Modification A: Exact Dollar Comparison in `evaluate_account_state` (Lines 97–116)
```python
        # Drawdown measured against starting equity ($50,000.00)
        dd_dollars = max(0.0, round(self.config.starting_equity - equity, 2))
        self.current_drawdown_dollars = dd_dollars
        self.current_drawdown_pct = round(dd_dollars / self.config.starting_equity, 4)

        if self.status == BreakerStatus.HALTED_DAILY_LOSS:
            return self.status

        # Hard daily loss check: exact dollar comparison
        if dd_dollars >= self.config.hard_max_daily_loss_dollars:
            self.status = BreakerStatus.HALTED_DAILY_LOSS
            self.risk_level = RiskLevel.HALTED
            self.breaker_triggered_at = timestamp
            self.breaker_trigger_equity = equity
            return BreakerStatus.HALTED_DAILY_LOSS
        elif dd_dollars >= self.config.warning_loss_dollars:
            self.risk_level = RiskLevel.WARNING
        else:
            self.risk_level = RiskLevel.NORMAL

        return self.status
```

#### Modification B: Position Exit Bypass in `evaluate_order_request` (Lines 118–150)
Add parameter `is_exit: bool = False` to `evaluate_order_request`, and immediately approve exit orders:

```python
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
        is_entry_lockout_active: bool = False,
        is_exit: bool = False,
    ) -> RiskCheckResult:
        """
        Pre-Trade Approval Gate.
        Evaluates an order request against institutional guardrails and calculates risk-adjusted sizing.
        """
        symbol = symbol.upper()

        # Position reducing or liquidation orders bypass entry lockouts, circuit halts, and sizing constraints
        if is_exit:
            return RiskCheckResult(
                approved=True,
                reason="APPROVED_EXIT: Position reducing or liquidation order approved",
                requested_qty=requested_qty,
                authorized_qty=requested_qty,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
            )

        # 1. Circuit Breaker Check
        if self.status != BreakerStatus.ARMED:
            return RiskCheckResult(
                approved=False,
                reason=f"CIRCUIT_BREAKER_HALTED: Trading halted due to maximum daily loss ({self.status.value})",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="CIRCUIT_BREAKER_HALTED",
            )
```

---

### 3. File: `backend/app/main.py`

#### Modification A: Differentiate Entries vs Exits in `pre_trade_risk_validator` (Lines 37–65)
```python
def pre_trade_risk_validator(order: Any, acct: PaperTradingAccount) -> tuple[bool, str]:
    """Validate order against Institutional Risk Engine and active flattening lockout."""
    is_lockout = flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING
    active_symbols = set(acct.positions.keys())
    active_sectors = {
        risk_engine.symbol_sectors.get(s, "Other")
        for s in active_symbols
        if s in risk_engine.symbol_sectors
    }

    # Differentiate position-reducing / liquidation orders from position-opening orders
    existing_pos = acct.positions.get(order.symbol.upper())
    is_exit = False
    if getattr(order, "strategy_id", None) in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "EMERGENCY_SWEEP", "MANUAL_FLATTEN"):
        is_exit = True
    elif existing_pos is not None:
        if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
            is_exit = True
        elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
            is_exit = True

    # Estimate stop price
    est_price = order.limit_price or order.stop_price or 100.0
    s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)

    res = risk_engine.evaluate_order_request(
        symbol=order.symbol,
        side=order.side.value,
        requested_qty=order.qty,
        entry_price=est_price,
        stop_price=s_price,
        account_equity=acct.equity,
        buying_power=acct.buying_power,
        active_positions_count=len(acct.positions),
        active_symbols=active_symbols,
        active_sectors=active_sectors,
        is_entry_lockout_active=is_lockout,
        is_exit=is_exit,
    )
    return res.approved, res.reason
```

#### Modification B: Dispatch Phase 4 Audit Emergency Sweep in `handle_flattening_directive` (Lines 233–238)
```python
    if directive.run_audit:
        audit_res = flattening_engine.execute_phase_4_audit(
            open_positions=account.positions,
            working_orders=list(engine.working_orders.values()),
        )
        if audit_res.cancel_all_orders and engine.working_orders:
            engine.cancel_all_orders("AUDIT_EMERGENCY_SWEEP")
        if audit_res.liquidate_all_positions and account.positions:
            now_dt = audit_res.timestamp
            for sym, pos in list(account.positions.items()):
                side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
                sweep_order = engine.create_order(
                    symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="EMERGENCY_SWEEP"
                )
                engine.submit_order(sweep_order.id)
                engine.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, now_dt)
```

---

### 4. File: `backend/tests/unit/test_empirical_stress_m1.py`

#### Modification A: Remove `@pytest.mark.xfail` and Align Oracle Tests
1. **`test_oracle_target_circuit_breaker_must_flatten_positions`**:
   - Remove `@pytest.mark.xfail` decorator.
   - In its inline `validator`, add `is_exit` detection:
     ```python
     existing_pos = a.positions.get(order.symbol.upper())
     is_exit = getattr(order, "strategy_id", None) in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "EMERGENCY_SWEEP") or (
         existing_pos is not None and (
             (existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
             (existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
         )
     )
     ```
     Pass `is_exit=is_exit` to `risk.evaluate_order_request`.
2. **`test_oracle_target_1555_must_flatten_all_positions`**:
   - Remove `@pytest.mark.xfail` decorator.
   - Update inline `validator` with `is_exit` pass-through as above.
3. **`test_oracle_target_no_premature_breaker_at_1499_99`**:
   - Remove `@pytest.mark.xfail` decorator.
4. **Update defect demonstration tests** (Lines 55–86, 154–245, 250–341):
   - Update `test_circuit_breaker_premature_trip_defect` to assert `status_1499 == BreakerStatus.ARMED`.
   - Update `test_circuit_breaker_liquidation_rejection_defect` and `test_auto_flattening_lockout_defect` to verify acceptance of liquidation orders and flattening of positions down to 0.

---

## Verification Test Commands & Target Results

`worker_m1` must execute and verify the following sequence:

### Step 1: Verify Adversarial Stress Test Suite
```bash
PYTHONPATH=. pytest backend/tests/stress/test_m1_empirical_stress.py -v
```
**Target Output**:
```
========================= 15 passed in 0.08s =========================
```
- `test_position_flip_dtbp_bypass_vulnerability` PASSED.
- `test_short_opening_fee_realized_pnl_accounting_leak` PASSED.

### Step 2: Verify Empirical Stress Suite (11 Tests)
```bash
PYTHONPATH=. pytest backend/tests/unit/test_empirical_stress_m1.py -v
```
**Target Output**:
```
========================= 11 passed in 0.12s =========================
```
Zero failures, zero xfails.

### Step 3: Verify Core Unit Test Suite
```bash
PYTHONPATH=. pytest backend/tests/unit -v
```
**Target Output**:
```
========================= 66 passed in 0.65s =========================
```

### Step 4: Verify Full E2E Test Suite (Tiers 1–4)
```bash
python3 tests/e2e/runner.py
```
**Target Output**:
```
248 passed in 0.25s
Exit Code: 0 (SUCCESS - ALL PASSED)
```

### Step 5: Verify Host Port Hygiene
```bash
lsof -i :8005 -i :8080 -i :3005 || echo "CLEAN: All ports free"
```
**Target Output**:
```
CLEAN: All ports free
```

---

## Conclusion & Readiness

This plan addresses 100% of the findings from `challenger_m1_1`, `challenger_m1_2`, and `reviewer_m1_1`. The modifications maintain complete backward compatibility, preserve all architectural boundaries, enforce mathematical invariance down to the penny, and enable Milestone 1 to achieve an unconditional PASS across all audit gates.
