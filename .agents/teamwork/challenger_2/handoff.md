# Handoff Report: Challenger 2 — Cross-Arm Isolation & Persistence

**Agent**: Challenger 2 (`teamwork_preview_challenger`)  
**Roles**: `critic`, `specialist` (Empirical Challenger)  
**Date**: 2026-09-24  
**Milestone**: Swing Trading Engine & Intraday Isolation Hardening  
**Verdict**: **REJECT** (Blocking Defect in Mutual Exclusion Locking for `AMD`)

---

## 1. Observation

1. **`backend/app/main.py:251–255`**:
   `pre_trade_risk_validator` defines position-reducing exit orders as:
   ```python
   existing_pos = acct.positions.get(sym)
   is_exit = False
   if getattr(order, "strategy_id", None) in (...):
       is_exit = True
   elif existing_pos is not None:
       if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
           is_exit = True
       elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
           is_exit = True
   ```
   No comparison is made between `order.arm` and `existing_pos.arm`.

2. **Empirical Execution & Failure (`pytest backend/tests/stress/test_cross_arm_isolation_persistence.py:257`)**:
   When `AMD` is held long by Swing (`TradingArm.SWING`, 100 shares), an Intraday SELL order for 50 shares (`strategy_id="orb"`, `arm=TradingArm.INTRADAY`) evaluates to `is_exit = True`.
   Result: `approved = True`, `reason = "APPROVED_EXIT: Position reducing or liquidation order approved"`.
   Verbatim output from `pytest backend/tests/stress/test_cross_arm_isolation_persistence.py`:
   ```
   FAILED backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_amd_held_by_swing_probe_intraday_sell_vulnerability
   AssertionError: VULNERABILITY DETECTED in pre_trade_risk_validator: Intraday SELL order on Swing-held AMD was APPROVED!
   Reason: APPROVED_EXIT: Position reducing or liquidation order approved. Intraday arm can liquidate or cannibalize Swing's long position!
   assert True is False
   ```

3. **Symmetric Reverse Execution & Failure (`test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability`)**:
   When `AMD` is held long by Intraday (`TradingArm.INTRADAY`, 100 shares), a Swing SELL order for 50 shares evaluates to `is_exit = True` and is approved as `APPROVED_EXIT`.
   Verbatim output:
   ```
   FAILED backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability
   AssertionError: VULNERABILITY DETECTED in pre_trade_risk_validator: Swing SELL order on Intraday-held AMD was APPROVED!
   Reason: APPROVED_EXIT: Position reducing or liquidation order approved. Swing arm can cannibalize Intraday position!
   assert True is False
   ```

4. **Execution Fill Demonstration**:
   When the intraday sell order was executed via `engine._execute_fill`, `acct.positions["AMD"].shares` decreased from 100 to 60, directly liquidating 40 shares of Swing's position. An order for 150 shares flipped Swing's long position into an intraday 50-share short position (`arm=TradingArm.INTRADAY`).

5. **Circuit Breaker Isolation & Persistence Observations**:
   - `main._trip_circuit_breaker(now_dt)` correctly preserved `LRCX` (30 shares, $800 avg entry, $762.50 stop loss) and working swing orders on `KLAC`, while 100% flattening `AAPL` and `TSLA`.
   - `check_intraday_emergency_stops` successfully triggered and liquidated `LRCX` under `CIRCUIT_HALTED` when price dropped to $760.00.
   - `TradingStateStore` SQLite round-trip restored all 6 symbols with 100% bar fidelity and identical indicator math (`200 SMA`, `5 SMA`, `14 ATR`, `RSI-2`, `60d RS vs QQQ`).

---

## 2. Logic Chain

1. **Step 1 (From Observation 1)**: In `backend/app/main.py:251–255`, `existing_pos` is fetched solely by ticker (`acct.positions.get(sym)`). If `existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL`, the code flags `is_exit = True`.
2. **Step 2 (From Observation 1 & 2)**: Because `order.arm` (e.g. `TradingArm.INTRADAY`) is not checked against `existing_pos.arm` (e.g. `TradingArm.SWING`), any cross-arm opposite-side order is classified as an exit of the other arm's position.
3. **Step 3 (From Observation 1 & 2)**: In `backend/app/main.py:263`, the mutual exclusion check `if not is_exit:` is skipped entirely when `is_exit` is `True`. Consequently, `is_symbol_reserved_for_swing(sym)` is never called for short sell orders.
4. **Step 4 (From Observation 1 & 2)**: In `backend/app/core/risk.py:164–172`, `if is_exit:` returns `approved = True` immediately, bypassing all circuit breakers, sector limits, and arm checks.
5. **Step 5 (From Observation 4)**: When the order fills in `engine._execute_fill`, it deducts shares from `existing_pos`, allowing an intraday strategy to unknowingly liquidate or cannibalize Swing's multi-day holding.
6. **Step 6**: This directly violates the core requirement in `DISPATCH.md §2`: *"Verify that while AMD is reserved or held by swing, any intraday BUY or SELL order is strictly rejected by pre_trade_risk_validator."* and `PROJECT.md F25`.

---

## 3. Caveats

- **Scope of Defect**: The mutual exclusion lock works correctly when `AMD` is in `swing_reserved_symbols` (prior to entry fill), and works correctly for BUY orders when `AMD` is held. It fails specifically for SELL orders (intraday short entries) against swing long positions, and swing sell orders against intraday long positions.
- **Circuit Breaker and DailyBarStore**: Circuit breaker isolation and DailyBarStore restart persistence were empirically tested and found completely sound; the rejection verdict is strictly scoped to the AMD mutual exclusion defect.
- No other untested assumptions: all findings are reproduced empirically via automated test execution.

---

## 4. Conclusion

**Verdict: REJECT.**

The codebase fails Milestone 2 / Feature F25 verification due to a critical cross-arm mutual exclusion defect:
- Intraday SELL orders are mistakenly approved as position-reducing exits on Swing-held `AMD` positions, cannibalizing multi-day swing positions.
- The defect must be remediated by a remediation worker agent by ensuring that `is_exit = True` is only assigned when `getattr(existing_pos, "arm", TradingArm.INTRADAY) == order_arm` in `backend/app/main.py:251–255`.

---

## 5. Verification Method

To independently reproduce and verify this finding:

1. **Run the Adversarial Test Suite**:
   ```bash
   pytest backend/tests/stress/test_cross_arm_isolation_persistence.py -v
   ```
   **Expected Outcome**: 10 tests pass, 2 fail at:
   - `TestAmdMutualExclusionLocking::test_amd_held_by_swing_probe_intraday_sell_vulnerability`
   - `TestAmdMutualExclusionLocking::test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability`

2. **Standalone Python Reproduction**:
   ```bash
   python3 -c "
   from backend.app import main
   from backend.app.core.account import TradingArm
   from backend.app.core.engine import OrderSide, OrderType

   main.account.positions.clear()
   main.account.apply_fill('sw1', 'AMD', 'BUY', 100, 150.0, 0.0, None, arm=TradingArm.SWING, strategy_id='swing_panic_dip')
   ord_sell = main.engine.create_order('AMD', OrderSide.SELL, OrderType.MARKET, 50, arm=TradingArm.INTRADAY, strategy_id='orb')
   approved, reason = main.pre_trade_risk_validator(ord_sell, main.account)
   print(f'Intraday SELL on Swing AMD: approved={approved}, reason={reason}')
   assert approved is False, 'Cross-arm cannibalization defect present!'
   "
   ```
   **Invalidation Condition**: Once `main.py` is patched to require `existing_arm == order_arm` for `is_exit = True`, both adversarial tests will pass with 12/12 (100%) success.
