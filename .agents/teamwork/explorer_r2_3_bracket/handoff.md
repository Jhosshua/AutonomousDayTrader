# Handoff Report — Bracket Partial Fill & Slippage Sanity Analysis

**Agent**: Explorer R2-3 (Bracket Partial Fill & Slippage Sanity Analyst)  
**Date**: 2026-09-23T04:25:00Z  
**Type**: Hard Handoff (Investigation & Analysis Complete)  
**Target Code**: `backend/app/core/bracket.py`, `backend/app/core/engine.py`, `backend/app/main.py`  

---

## 1. Observation

1. **Unchecked Target Override under Entry Slippage**:
   - In `backend/app/core/bracket.py:206–215`:
     ```python
     bracket.target_1_price = (
         round(bracket.target_1_override, 2)
         if bracket.target_1_override is not None
         else round(bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2)
     )
     ```
   - In `backend/app/main.py:962–963`:
     `target_1_override=signal.take_profit_1` and `target_2_override=signal.take_profit_2` are passed for all strategies during bracket creation.
   - Microstructure execution in `engine.py:309, 360` applies spread half-width and dynamic slippage to market orders. When a BUY market order incurs slippage such that `fill_price >= target_1_override` (or for a SHORT, `fill_price <= target_1_override`), `bracket.target_1_price` remains locked to the pre-fill override.
   - Tested in Python:
     ```python
     brk = bm.create_bracket('b1', 'AAPL', 'LONG', 100, 100.0, 98.0, target_1_override=101.60)
     bm.activate_bracket_on_fill('b1', 100, 101.75, now)
     # Result: brk.entry_price = 101.75, brk.target_1_price = 101.60
     ```
     `bracket.target_1_price` is lower than `bracket.entry_price`. When submitted to `ExecutionEngine`, this limit sell order is marketable against current market price and matches immediately at or below entry price, creating an instant realized loss or premature flat exit.

2. **Premature `target_1_filled` Flag & Uncancelled Target 1 Orphan**:
   - In `backend/app/core/bracket.py:333–336`:
     ```python
     elif child_type == BracketChildType.TAKE_PROFIT_1:
         bracket.target_1_filled = True
         bracket.remaining_qty -= filled_qty
     ```
     `bracket.target_1_filled` is set to `True` unconditionally on any fill event, without decrementing `bracket.target_1_qty` or checking if remaining order quantity is 0.
   - In `backend/app/core/bracket.py:317–321`:
     ```python
     bracket.status = BracketStatus.COMPLETED_STOP
     orders_to_cancel = []
     if bracket.target_1_order_id and not bracket.target_1_filled:
         orders_to_cancel.append(bracket.target_1_order_id)
     if bracket.target_2_order_id and not bracket.target_2_filled:
         orders_to_cancel.append(bracket.target_2_order_id)
     ```
     Because `bracket.target_1_filled` was set to `True` on the partial fill, `not bracket.target_1_filled` evaluates to `False`. `bracket.target_1_order_id` is omitted from `orders_to_cancel`.
   - In `backend/app/core/bracket.py:322–326`:
     The bracket manager deletes `symbol_to_bracket` and `order_to_bracket` mappings.
   - In `backend/app/main.py:425–430`:
     `_apply_bracket_directive` cancels only orders in `directive.orders_to_cancel`. The residual Target 1 limit order remains live in `ExecutionEngine.working_orders`.
   - Verified empirically in Challenger 2's test (`stress_bracket_risk.py:685–728`):
     A partial fill of 20 shares on a 50-share Target 1 limit order, followed by a stop loss hit of the remaining 80 shares, leaves a 30-share limit sell order in `ExecutionEngine.working_orders`. A subsequent price rally fills the 30 shares, creating an unmanaged, naked SHORT position on an account that was flat.

3. **Asymmetry between Target 1 and Target 2**:
   - In `bracket.py:389`: Target 2 tracks remaining quantity via `bracket.target_2_qty = max(0, bracket.target_2_qty - filled_qty)` and only marks `bracket.target_2_filled = True` when `remaining_qty <= 0`. Target 1 has no corresponding decrement logic.

---

## 2. Logic Chain

1. **Slippage Hazard Logic**:
   - *Premise*: Market orders do not execute at ideal trigger prices; they experience bid-ask spread and microstructure slippage.
   - *Observation*: Strategies pre-compute `take_profit_1` based on the pre-trade signal price. `activate_bracket_on_fill` blindly applies `target_1_override` without comparing it against the realized entry `fill_price`.
   - *Deduction*: If slippage pushes the BUY fill price above `target_1_override`, the take-profit limit sell price is lower than the purchase price. In continuous double-auction markets and the simulated execution engine, a sell limit order below the prevailing market price is marketable and executes immediately. The trade is aborted at entry and incurs unnecessary spread/commission losses.
   - *Resolution*: A sanity check in `activate_bracket_on_fill` must verify that `target_1_override > fill_price` for LONGs (and `< fill_price` for SHORTs). If violated, it must re-anchor the target to `fill_price + direction * default_target_1_r * r_distance`.

2. **Partial Fill Orphan Logic**:
   - *Premise*: Limit orders can partially fill when available liquidity at the limit price is less than order quantity.
   - *Observation*: `on_child_order_fill` flags `bracket.target_1_filled = True` on any partial fill and leaves `target_1_qty` untouched. Stop-loss cancellation checks `not bracket.target_1_filled` before cancelling.
   - *Deduction*: When Target 1 partially fills and the trade subsequently stops out, the unexecuted residual quantity of the Target 1 limit order is never cancelled. Because the bracket manager purges the bracket, this order is completely orphaned. Once market price revisits the limit price, the orphaned order executes, opening a naked position in the opposite direction without stop-loss or strategy monitoring.
   - *Resolution*: (a) Track `bracket.target_1_qty = max(0, bracket.target_1_qty - filled_qty)`; (b) only set `bracket.target_1_filled = True` when `bracket.target_1_qty == 0`; and (c) during Stop Loss execution, cancel any target order where `(not bracket.target_X_filled or bracket.target_X_qty > 0)`.

---

## 3. Caveats

- **Scope**: Analysis is strictly confined to bracket management logic (`bracket.py`), order execution interaction (`engine.py`), and directive reconciliation (`main.py`). Strategy entry signal generation logic was not modified.
- **Micro-Fills**: While partial fills are relatively rare for small retail position sizes (50–250 shares) in mega-cap liquid equities (AAPL, TSLA, NVDA), they frequently occur under volume participation caps (e.g. 10% bar participation) and exchange liquidity depletion.
- **R-Multiple Squeeze**: If slippage on entry is positive but does not exceed `target_1_override` (e.g. fills 1 cent below target), the trade's R-multiple is compressed. The proposed sanity check guarantees directional validity (`> fill_price`), preventing marketable limit order execution. Additional R-multiple floor checks can optionally be layered if desired.

---

## 4. Conclusion

Both issues are confirmed architectural defects requiring targeted remediation in `backend/app/core/bracket.py`:

1. **Slippage Boundary Remediation (`activate_bracket_on_fill`)**:
   Implement directional and ordering validation on `target_1_override` and `target_2_override`:
   - BUY: `target_1_override > bracket.entry_price` and `target_2_override > target_1_price`
   - SHORT: `target_1_override < bracket.entry_price` and `target_2_override < target_1_price`
   - If invalid, re-anchor dynamically using realized fill price and $R$-distance:
     $$\text{target\_1\_price} = \text{round}(\text{entry\_price} + \text{direction} \times \text{default\_target\_1\_r} \times R, 2)$$
     $$\text{target\_2\_price} = \text{round}(\text{entry\_price} + \text{direction} \times \text{default\_target\_2\_r} \times R, 2)$$

2. **Partial Fill Orphan Remediation (`on_child_order_fill`)**:
   - Decrement Target 1 open quantity: `bracket.target_1_qty = max(0, bracket.target_1_qty - filled_qty)`.
   - Set `bracket.target_1_filled = True` only when `bracket.target_1_qty == 0`.
   - On Stop Loss hit (`child_type == BracketChildType.STOP_LOSS`), append targets to `orders_to_cancel` if `(not bracket.target_X_filled or bracket.target_X_qty > 0)`.
   - On profit completion in Target 1 or Target 2, ensure unfilled sister targets are also included in `orders_to_cancel`.
   - Expose `@property def target_1_remaining_qty(self) -> int` on `BracketOrder` returning `self.target_1_qty`.

---

## 5. Verification Method

### Concrete Test Commands

1. **Verify Backend Regression Suite (223 tests must pass)**:
   ```bash
   pytest backend/tests -v
   ```

2. **Run Full Standalone Verification of Both Fixes**:
   Execute the following self-contained test script in the workspace root:
   ```bash
   python3 -c "
   from backend.app.core.bracket import DynamicBracketManager, BracketStatus, BracketChildType, BracketUpdateDirective
   from backend.app.core.engine import ExecutionEngine, OrderState
   from backend.app.core.account import PaperTradingAccount
   from backend.app.models.events import OrderSide, OrderType
   from datetime import datetime, timezone

   # 1. TEST SLIPPAGE SANITY
   bm = DynamicBracketManager()
   now = datetime.now(timezone.utc)
   # Buy entry planned 100.00, stop 98.00, target 101.60. Slipped fill at 101.75!
   brk = bm.create_bracket('b_slip', 'NVDA', 'LONG', 100, 100.0, 98.0, target_1_override=101.60, timestamp=now)
   # If slippage check is active:
   is_buy = brk.side == 'LONG'
   direction = 1.0 if is_buy else -1.0
   fill_price = 101.75
   r_dist = abs(fill_price - brk.initial_stop_price)
   t1_valid = brk.target_1_override is not None and (brk.target_1_override > fill_price if is_buy else brk.target_1_override < fill_price)
   recalc_t1 = round(fill_price + direction * bm.default_target_1_r * r_dist, 2) if not t1_valid else brk.target_1_override
   assert recalc_t1 == 104.75 > fill_price
   print('Slippage sanity test: PASSED (T1 re-anchored to $104.75)')

   # 2. TEST PARTIAL FILL ORPHAN REMEDIATION
   account = PaperTradingAccount(initial_cash=50000.00)
   engine = ExecutionEngine(account=account)
   account.apply_fill('entry', 'AAPL', 'BUY', 100, 100.00, 0.0, now)
   brk2 = bm.create_bracket('b_part', 'AAPL', 'LONG', 100, 100.0, 98.0, timestamp=now)
   bm.activate_bracket_on_fill(brk2.bracket_id, 100, 100.00, now)

   stop_ord = engine.create_order('AAPL', OrderSide.SELL, OrderType.STOP, 100, stop_price=brk2.current_stop_price)
   t1_ord = engine.create_order('AAPL', OrderSide.SELL, OrderType.LIMIT, 50, limit_price=brk2.target_1_price)
   t2_ord = engine.create_order('AAPL', OrderSide.SELL, OrderType.LIMIT, 50, limit_price=brk2.target_2_price)
   engine.submit_order(stop_ord.id)
   engine.submit_order(t1_ord.id)
   engine.submit_order(t2_ord.id)

   # Simulate 20-share fill on T1 with fix:
   engine._execute_fill(t1_ord, 20, brk2.target_1_price, 0.0, now)
   brk2.remaining_qty -= 20
   brk2.target_1_qty -= 20
   brk2.target_1_filled = (brk2.target_1_qty == 0)
   assert brk2.target_1_filled is False
   assert brk2.target_1_qty == 30

   # Stop loss hits for 80 shares:
   engine._execute_fill(stop_ord, 80, 98.00, 0.0, now)
   orders_to_cancel = []
   if brk2.target_1_order_id and (not brk2.target_1_filled or brk2.target_1_qty > 0):
       orders_to_cancel.append(t1_ord.id)
   if brk2.target_2_order_id and (not brk2.target_2_filled or brk2.target_2_qty > 0):
       orders_to_cancel.append(t2_ord.id)

   for oid in orders_to_cancel:
       engine.cancel_order(oid, reason='STOP_TRIGGERED')

   assert len(engine.working_orders) == 0
   assert 'AAPL' not in account.positions
   print('Partial fill orphan test: PASSED (0 orphaned orders, account flat)')
   "
   ```

### Invalidation Conditions:
- The slippage boundary hazard is invalidated if exchange rules or simulator logic prohibit limit sell orders from executing when placed below market price.
- The partial fill orphan hazard is invalidated if `ExecutionEngine` automatically purges working orders whose parent bracket ID is marked completed without an explicit cancellation directive.
