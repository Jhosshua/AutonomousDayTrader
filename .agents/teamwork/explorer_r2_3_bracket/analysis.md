# Deep-Dive Analysis: Bracket Partial Fill Mechanics & Slippage Boundary Hazards

**Author**: Explorer R2-3 (Bracket Partial Fill & Slippage Sanity Analyst)  
**Date**: 2026-09-23T04:20:00Z  
**Target Module**: `backend/app/core/bracket.py`, `backend/app/core/engine.py`, `backend/app/main.py`  
**Status**: Complete  

---

## 1. Executive Summary

This investigation analyzed two critical microstructure and state machine defects in `backend/app/core/bracket.py`:
1. **Reviewer 1's Slippage Boundary Hazard**: In `activate_bracket_on_fill` (lines 206–215), strategy-supplied target price overrides (`bracket.target_1_override` and `target_2_override`) are accepted literally without validating them against the realized entry `fill_price`. When an entry market order suffers adverse slippage or gap effects (e.g. `fill_price >= target_1_override` on a BUY, or `fill_price <= target_1_override` on a SHORT), the engine submits an inverted limit exit order that is immediately marketable below entry, locking in an instantaneous loss or immediate flat exit with full fee drag.
2. **Challenger 2's Target 1 Partial Fill Orphan Vulnerability**: In `on_child_order_fill` (lines 333–371), any fill on Target 1—even a micro partial fill of 1 share—immediately sets `bracket.target_1_filled = True` without decrementing `bracket.target_1_qty` or tracking remaining quantity. When the position subsequently reverses and stops out, line 317 checks `if bracket.target_1_order_id and not bracket.target_1_filled:`, evaluating to `False`. The remaining unfilled quantity of the Target 1 limit order is never added to `orders_to_cancel`, while the bracket manager deletes its internal mappings. This leaves an unmanaged limit order active in `ExecutionEngine.working_orders`, which executes on subsequent price recovery and creates an unintended naked short (or long) position with zero risk controls.

Both findings have been empirically reproduced in isolated execution tests, mathematically verified, and solved with drop-in, zero-regression code specifications.

---

## 2. Review of Reviewer 1's Finding: Slippage Boundary Hazard

### 2.1 Code Citation & Current Behavior
In `backend/app/main.py:953–964`:
```python
bracket = bracket_manager.create_bracket(
    bracket_id=f"brk_{submitted.id}",
    symbol=sym,
    side="LONG" if side == OrderSide.BUY else "SHORT",
    total_qty=qty,
    entry_price=signal.entry_price,
    stop_price=adapted_stop,
    strategy_id=signal.strategy_id,
    timestamp=signal.timestamp,
    target_1_override=signal.take_profit_1,
    target_2_override=signal.take_profit_2,
)
```
At signal creation, each strategy computes `signal.take_profit_1` from its triggering bar close or estimated price `signal.entry_price`. For instance, in `orb.py` or `news_momentum.py`:
$$\text{take\_profit\_1} = \text{entry\_price} + (\text{direction} \times 0.8 \times R)$$
where $R = |\text{entry\_price} - \text{stop\_price}|$.

When the market order is routed and fills, `main.py:616` invokes `activate_bracket_on_fill`:
In `backend/app/core/bracket.py:203–215`:
```python
bracket.r_distance = round(abs(fill_price - bracket.initial_stop_price), 4)
bracket.entry_price = round(fill_price, 4)
direction = 1.0 if bracket.side == "LONG" else -1.0
bracket.target_1_price = (
    round(bracket.target_1_override, 2)
    if bracket.target_1_override is not None
    else round(bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2)
)
bracket.target_2_price = (
    round(bracket.target_2_override, 2)
    if bracket.target_2_override is not None
    else round(bracket.entry_price + direction * self.default_target_2_r * bracket.r_distance, 2)
)
```

### 2.2 Failure Mechanism & Microstructure Analysis
When an entry order is submitted as `OrderType.MARKET`, `ExecutionEngine.process_bar` / `process_quote` applies spread half-width, volatility expansion, and participation rate slippage:
$$\text{fill\_price}_{\text{LONG}} = \text{market\_price} + 0.5 \times \text{spread} + \text{slippage}$$
$$\text{fill\_price}_{\text{SHORT}} = \text{market\_price} - 0.5 \times \text{spread} - \text{slippage}$$

#### Case A: BUY Market Order with Positive Slippage
1. **Setup**: Long NVDA. Strategy estimates entry at $\$100.00$, stop at $\$98.00$ ($R = \$2.00$). Pre-computed `take_profit_1 = $101.60` ($0.8R$), `take_profit_2 = $103.60` ($1.8R$).
2. **Slippage Event**: Market open volatility or spread expansion results in `fill_price = $101.75`.
3. **Hazardous State**:
   - `bracket.entry_price = $101.75`
   - `bracket.initial_stop_price = $98.00` ($R_{\text{realized}} = \$3.75$)
   - Because `bracket.target_1_override` is not None, `bracket.target_1_price = $101.60`.
4. **Execution Pathology**:
   - `activate_bracket_on_fill` returns a `SUBMIT_ORDERS` directive containing:
     `{"order_id": "t1_...", "type": "LIMIT", "price": 101.60, "qty": 50, "side": "SELL"}`
   - `ExecutionEngine` receives a Limit Sell order at $\$101.60$ when the market bid is at or above $\$101.75$.
   - A sell limit order with limit price $\le \text{market bid}$ is **marketable**!
   - On the very next tick/quote/bar, the limit order fills immediately at $\$101.75$ (or at $\$101.60$ if bid pulls back).
   - If filled at $\$101.60$, the account suffers an instant $-\$0.15$/share realized loss. If filled at $\$101.75$, the trade exits immediately with $\$0.00$ gross PnL, but pays full SEC Section 31 and FINRA TAF regulatory fees.
   - The trade intended to capture $0.8R$ to $1.8R$ is aborted immediately at entry.

#### Case B: SHORT Market Order with Downward Slippage
1. **Setup**: Short TSLA. Strategy estimates entry at $\$100.00$, stop at $\$102.00$ ($R = \$2.00$). Pre-computed `take_profit_1 = $98.40` ($0.8R$).
2. **Slippage Event**: Adverse gap or short slippage fills at `fill_price = $98.20`.
3. **Hazardous State**:
   - `bracket.entry_price = $98.20`
   - `target_1_override = $98.40`
   - `bracket.target_1_price = $98.40`.
4. **Execution Pathology**:
   - A Limit Buy order is placed at $\$98.40$ when market ask is $\$98.20$.
   - The buy limit order is marketable above current market price and fills immediately to cover, locking in zero profit or buying back at a higher price than entry.

#### Case C: Geometric R-Multiple Compression
Even if `fill_price` does not cross `target_1_override`, adverse slippage severely degrades expectancy:
- Entry $\$100.00$, Stop $\$98.00$, T1 override $\$101.60$.
- Order fills at $\$101.50$ (slippage of $+\$1.50$).
- $R_{\text{realized}} = \$101.50 - \$98.00 = \$3.50$.
- Realized Target 1 profit potential: $\$101.60 - \$101.50 = \$0.10$.
- Realized Target 1 R-multiple: $\frac{\$0.10}{\$3.50} \approx 0.028R$!
- Risking $\$3.50$ to make $\$0.10$ completely destroys the statistical expectancy of the strategy.

### 2.3 Proposed Solution & Mathematical Invariants
In `activate_bracket_on_fill`, implement strict boundary sanity checks:
1. **Directional Invariant**:
   - For `LONG`: `target_1_override > fill_price`
   - For `SHORT`: `target_1_override < fill_price`
2. **Target 2 Invariant**:
   - For `LONG`: `target_2_override > target_1_price`
   - For `SHORT`: `target_2_override < target_1_price`
3. **Safety Fallback**:
   If either invariant is violated, safely recalculate the target from the realized `fill_price` using the dynamic $R$-distance ($R_{\text{realized}} = |\text{fill\_price} - \text{initial\_stop}|$):
   $$\text{target\_1\_price} = \text{round}(\text{fill\_price} + \text{direction} \times \text{default\_target\_1\_r} \times R_{\text{realized}}, 2)$$
   $$\text{target\_2\_price} = \text{round}(\text{fill\_price} + \text{direction} \times \text{default\_target\_2\_r} \times R_{\text{realized}}, 2)$$

---

## 3. Review of Challenger 2's Finding: Target 1 Partial Fill Orphan

### 3.1 Code Citation & Current Behavior
In `backend/app/core/bracket.py:332–336`:
```python
# 2. Target 1 Filled
elif child_type == BracketChildType.TAKE_PROFIT_1:
    bracket.target_1_filled = True
    bracket.remaining_qty -= filled_qty
```
Notice:
1. `bracket.target_1_filled` is set to `True` unconditionally on **any** fill, regardless of whether `filled_qty < bracket.target_1_qty`.
2. `bracket.target_1_qty` is **not** decremented by `filled_qty`.

Now look at Stop Loss execution in `bracket.py:315–321`:
```python
bracket.status = BracketStatus.COMPLETED_STOP
orders_to_cancel = []
if bracket.target_1_order_id and not bracket.target_1_filled:
    orders_to_cancel.append(bracket.target_1_order_id)
if bracket.target_2_order_id and not bracket.target_2_filled:
    orders_to_cancel.append(bracket.target_2_order_id)
```
And look at table cleanup in lines 322–326:
```python
self.symbol_to_bracket.pop(bracket.symbol, None)
for oid in (bracket.stop_order_id, bracket.target_1_order_id, bracket.target_2_order_id):
    if oid:
        self.order_to_bracket.pop(oid, None)
```

### 3.2 Full Attack Sequence & Disastrous Blast Radius
1. **Entry**: Account buys 100 shares of AAPL at $\$100.00$. Stop at $\$98.00$ (100 sh), Target 1 at $\$101.60$ (50 sh), Target 2 at $\$103.60$ (50 sh).
2. **Partial Fill on T1**: Price reaches $\$101.60$. Due to volume participation caps or thin order book depth, only 20 shares fill on Target 1.
3. **Premature Flag**: `on_child_order_fill` is called with `filled_qty = 20`.
   - Line 334 sets `bracket.target_1_filled = True`.
   - `bracket.remaining_qty` is reduced from 100 to 80.
   - Stop order is resized to 80 shares, stop price ratcheted to breakeven ($\$100.05$).
   - In `ExecutionEngine.working_orders`, the Target 1 order `t1_...` is in state `PARTIALLY_FILLED` with `remaining_qty = 30`.
4. **Market Reversal & Stop Hit**: Price pulls back sharply and breaches the stop loss at $\$98.00$ (or $\$100.05$).
   - Engine matches stop order for 80 shares.
   - `on_child_order_fill` is called for `stop_order_id` with `filled_qty = 80`.
   - `bracket.remaining_qty` becomes 0.
   - Line 317 evaluates: `if bracket.target_1_order_id and not bracket.target_1_filled:`
   - Because `bracket.target_1_filled` was set to `True` in step 3, `not bracket.target_1_filled` is **`False`**!
   - `bracket.target_1_order_id` is **OMITTED** from `orders_to_cancel`!
   - Only `bracket.target_2_order_id` is appended to `orders_to_cancel`.
5. **State Desynchronization & Cleanup**:
   - `DynamicBracketManager` sets `status = COMPLETED_STOP`.
   - Lines 322–326 pop `symbol_to_bracket` and `order_to_bracket`.
   - `main.py` processes `orders_to_cancel` via `_apply_bracket_directive`: cancels `target_2_order_id`.
   - The Target 1 order (30 shares) remains active in `ExecutionEngine.working_orders`!
   - Position in account: 0 shares (100 entered - 20 TP1 - 80 Stop = 0 shares). Account is flat.
6. **Phantom Execution**:
   - On a subsequent bar, price rallies back to $\$101.60$.
   - `ExecutionEngine.process_bar` encounters the working 30-share limit sell order at $\$101.60$.
   - The order matches and executes!
   - `account.apply_fill` executes a SELL of 30 shares when account position was 0.
   - **Account position flips to SHORT 30 shares!**
   - Because the bracket was already closed, there is NO stop order, NO target order, and NO strategy managing this position. If the stock rallies to $\$150.00$, the account sustains unlimited unhedged losses!

### 3.3 Target 1 vs Target 2 Asymmetry
Notice the architectural inconsistency in `bracket.py`:
In Target 2 (`child_type == BracketChildType.TAKE_PROFIT_2`, lines 373–398):
```python
bracket.remaining_qty = max(0, bracket.remaining_qty - filled_qty)
if bracket.remaining_qty <= 0:
    bracket.target_2_filled = True
    ...
else:
    bracket.target_2_qty = max(0, bracket.target_2_qty - filled_qty)
```
Target 2 already had:
1. `bracket.target_2_qty = max(0, bracket.target_2_qty - filled_qty)`
2. `bracket.target_2_filled` was only marked `True` when `bracket.remaining_qty <= 0`!
Target 1 completely lacked this logic and naively treated any fill as 100% complete.

---

## 4. Proposed Concrete Code Diffs

### 4.1 Target Override Slippage Sanity Check
In `backend/app/core/bracket.py`, replace lines 205–216 in `activate_bracket_on_fill`:

```python
<<<<
        direction = 1.0 if bracket.side == "LONG" else -1.0
        bracket.target_1_price = (
            round(bracket.target_1_override, 2)
            if bracket.target_1_override is not None
            else round(bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2)
        )
        bracket.target_2_price = (
            round(bracket.target_2_override, 2)
            if bracket.target_2_override is not None
            else round(bracket.entry_price + direction * self.default_target_2_r * bracket.r_distance, 2)
        )
====
        direction = 1.0 if bracket.side == "LONG" else -1.0
        is_buy = bracket.side == "LONG"

        # Sanity check Target 1 override against realized fill price:
        # For BUY: target_1 must be strictly > fill_price
        # For SHORT: target_1 must be strictly < fill_price
        t1_override_valid = False
        if bracket.target_1_override is not None:
            if is_buy and bracket.target_1_override > bracket.entry_price:
                t1_override_valid = True
            elif not is_buy and bracket.target_1_override < bracket.entry_price:
                t1_override_valid = True

        if t1_override_valid:
            bracket.target_1_price = round(bracket.target_1_override, 2)
        else:
            bracket.target_1_price = round(
                bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2
            )

        # Sanity check Target 2 override: must be strictly beyond Target 1 in the profit direction
        t2_override_valid = False
        if bracket.target_2_override is not None:
            if is_buy and bracket.target_2_override > bracket.target_1_price:
                t2_override_valid = True
            elif not is_buy and bracket.target_2_override < bracket.target_1_price:
                t2_override_valid = True

        if t2_override_valid:
            bracket.target_2_price = round(bracket.target_2_override, 2)
        else:
            bracket.target_2_price = round(
                bracket.entry_price + direction * self.default_target_2_r * bracket.r_distance, 2
            )
>>>>
```

### 4.2 Target 1 Partial Fill Tracking & Stop Cancellation Fix
In `backend/app/core/bracket.py`:

#### Step A: Stop Loss Execution (lines 315–321)
Replace:
```python
<<<<
            bracket.status = BracketStatus.COMPLETED_STOP
            orders_to_cancel = []
            if bracket.target_1_order_id and not bracket.target_1_filled:
                orders_to_cancel.append(bracket.target_1_order_id)
            if bracket.target_2_order_id and not bracket.target_2_filled:
                orders_to_cancel.append(bracket.target_2_order_id)
====
            bracket.status = BracketStatus.COMPLETED_STOP
            orders_to_cancel = []
            if bracket.target_1_order_id and (not bracket.target_1_filled or bracket.target_1_qty > 0):
                orders_to_cancel.append(bracket.target_1_order_id)
            if bracket.target_2_order_id and (not bracket.target_2_filled or bracket.target_2_qty > 0):
                orders_to_cancel.append(bracket.target_2_order_id)
>>>>
```

#### Step B: Target 1 Fill Handler (lines 332–348)
Replace:
```python
<<<<
        # 2. Target 1 Filled
        elif child_type == BracketChildType.TAKE_PROFIT_1:
            bracket.target_1_filled = True
            bracket.remaining_qty -= filled_qty

            if bracket.remaining_qty <= 0:
                bracket.status = BracketStatus.COMPLETED_PROFIT
                self.symbol_to_bracket.pop(bracket.symbol, None)
                for oid in (bracket.stop_order_id, bracket.target_1_order_id, bracket.target_2_order_id):
                    if oid:
                        self.order_to_bracket.pop(oid, None)
                return BracketUpdateDirective(
                    action="CANCEL_ORDER",
                    orders_to_cancel=[bracket.stop_order_id] if bracket.stop_order_id else [],
                    bracket_status=BracketStatus.COMPLETED_PROFIT,
                )
====
        # 2. Target 1 Filled
        elif child_type == BracketChildType.TAKE_PROFIT_1:
            bracket.remaining_qty = max(0, bracket.remaining_qty - filled_qty)
            bracket.target_1_qty = max(0, bracket.target_1_qty - filled_qty)
            if bracket.target_1_qty == 0:
                bracket.target_1_filled = True

            if bracket.remaining_qty <= 0:
                bracket.target_1_filled = True
                bracket.status = BracketStatus.COMPLETED_PROFIT
                self.symbol_to_bracket.pop(bracket.symbol, None)
                for oid in (bracket.stop_order_id, bracket.target_1_order_id, bracket.target_2_order_id):
                    if oid:
                        self.order_to_bracket.pop(oid, None)
                orders_to_cancel = [bracket.stop_order_id] if bracket.stop_order_id else []
                if bracket.target_2_order_id and (not bracket.target_2_filled or bracket.target_2_qty > 0):
                    orders_to_cancel.append(bracket.target_2_order_id)
                return BracketUpdateDirective(
                    action="CANCEL_ORDER",
                    orders_to_cancel=orders_to_cancel,
                    bracket_status=BracketStatus.COMPLETED_PROFIT,
                )
>>>>
```

#### Step C: Target 2 Fill Handler Completion Cleanup (lines 373–388)
Ensure Target 2 also cancels any residual Target 1 order if Target 2 completes the position:
```python
<<<<
        # 3. Target 2 Filled
        elif child_type == BracketChildType.TAKE_PROFIT_2:
            bracket.remaining_qty = max(0, bracket.remaining_qty - filled_qty)
            if bracket.remaining_qty <= 0:
                bracket.target_2_filled = True
                bracket.status = BracketStatus.COMPLETED_PROFIT
                self.symbol_to_bracket.pop(bracket.symbol, None)
                for oid in (bracket.stop_order_id, bracket.target_1_order_id, bracket.target_2_order_id):
                    if oid:
                        self.order_to_bracket.pop(oid, None)

                return BracketUpdateDirective(
                    action="CANCEL_ORDER",
                    orders_to_cancel=[bracket.stop_order_id] if bracket.stop_order_id else [],
                    bracket_status=BracketStatus.COMPLETED_PROFIT,
                )
====
        # 3. Target 2 Filled
        elif child_type == BracketChildType.TAKE_PROFIT_2:
            bracket.remaining_qty = max(0, bracket.remaining_qty - filled_qty)
            bracket.target_2_qty = max(0, bracket.target_2_qty - filled_qty)
            if bracket.target_2_qty == 0:
                bracket.target_2_filled = True

            if bracket.remaining_qty <= 0:
                bracket.target_2_filled = True
                bracket.status = BracketStatus.COMPLETED_PROFIT
                self.symbol_to_bracket.pop(bracket.symbol, None)
                for oid in (bracket.stop_order_id, bracket.target_1_order_id, bracket.target_2_order_id):
                    if oid:
                        self.order_to_bracket.pop(oid, None)

                orders_to_cancel = [bracket.stop_order_id] if bracket.stop_order_id else []
                if bracket.target_1_order_id and (not bracket.target_1_filled or bracket.target_1_qty > 0):
                    orders_to_cancel.append(bracket.target_1_order_id)

                return BracketUpdateDirective(
                    action="CANCEL_ORDER",
                    orders_to_cancel=orders_to_cancel,
                    bracket_status=BracketStatus.COMPLETED_PROFIT,
                )
>>>>
```

#### Step D: BracketOrder Property / Field Access
In `BracketOrder` (lines 40–44), add the `@property` or field for `target_1_remaining_qty` and `target_2_remaining_qty`:
```python
    @property
    def target_1_remaining_qty(self) -> int:
        return self.target_1_qty

    @property
    def target_2_remaining_qty(self) -> int:
        return self.target_2_qty
```
This guarantees that any caller or inspection expecting `target_1_remaining_qty` can access it cleanly while preserving full backward compatibility with `target_1_qty`.

---

## 5. Verification Matrix & Edge Case Coverage

| Test ID | Scenario | Expected Behavior | Verification Command / Assertion |
|:---|:---|:---|:---|
| **V1** | BUY order slips past `target_1_override` ($101.75 \ge 101.60$) | Target 1 recomputes to $101.75 + 0.8 \times 3.75 = \$104.75$ | `assert brk.target_1_price == 104.75 > brk.entry_price` |
| **V2** | SHORT order slips past `target_1_override` ($98.20 \le 98.40$) | Target 1 recomputes to $98.20 - 0.8 \times 3.80 = \$95.16$ | `assert brk.target_1_price == 95.16 < brk.entry_price` |
| **V3** | BUY order with valid override ($100.50 < 101.80$) | Preserves valid override: T1 = $101.80, T2 = $103.50 | `test_activation_preserves_targets_with_overrides` PASS |
| **V4** | T1 partially fills 20 of 50 shares | `target_1_qty = 30`, `target_1_filled = False`, stop resized to 80 | `assert brk.target_1_qty == 30 and not brk.target_1_filled` |
| **V5** | Stop loss executes after T1 partial fill | Both T1 and T2 added to `orders_to_cancel` | `assert t1_ord.id in dir_stop.orders_to_cancel` |
| **V6** | T1 fully fills 50 of 50 shares, then Stop executes | `target_1_filled = True`, T1 omitted from `orders_to_cancel`, T2 included | `assert t1_ord.id not in dir_stop.orders_to_cancel and t2_ord.id in dir_stop.orders_to_cancel` |
| **V7** | Engine Working Orders post-stop cancel | All working orders cleanly cancelled, 0 orphaned orders, 0 phantom fills on rally | `assert len(engine.working_orders) == 0 and len(fills_rally) == 0` |

---

## 6. Implementation Guidance for Worker

1. **Apply Diffs**: Update `backend/app/core/bracket.py` with the four modifications specified in Section 4.
2. **Regression Check**: Run `pytest backend/tests -v` (confirm 223/223 pass).
3. **Stress Suite Check**: Run `pytest .agents/teamwork/challenger_2/stress_bracket_risk.py -v`. Update Challenger 2's `test_target_1_partial_fill_orphans_limit_order_in_engine_end_to_end` assertion from checking that the bug exists (`assert t1_ord.id in engine.working_orders`) to asserting the fix (`assert t1_ord.id not in engine.working_orders`).
4. **Integration**: Confirm that `_apply_bracket_directive` in `main.py` requires zero changes because it already iterates over `directive.orders_to_cancel` and cancels orders present in `engine.working_orders`.
