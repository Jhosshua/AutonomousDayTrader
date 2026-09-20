# Handoff Report: Milestone 1 Adversarial Verification (Paper Ledger, Slippage & Fill Engine)

**Agent**: `challenger_m1_2`  
**Target Milestone**: Milestone 1 (`engine_ingestion` / Paper Ledger, Slippage & Fill Engine)  
**Parent Orchestrator**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  
**Date**: 2026-09-19  
**Structured Verdict**: **REQUEST_CHANGES**  

---

## 1. Observation

Direct observations and execution outputs from empirical test harness and code audit:

### Test Execution Output
Executed command:
```bash
PYTHONPATH=. pytest backend/tests/stress/test_m1_empirical_stress.py -v
```
Output:
```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/mo/AutonomousDayTrader
collected 15 items

backend/tests/stress/test_m1_empirical_stress.py::TestBuyingPowerBoundsAndCashIntegrity::test_single_order_exceeding_dtbp_hard_rejected PASSED [  6%]
backend/tests/stress/test_m1_empirical_stress.py::TestBuyingPowerBoundsAndCashIntegrity::test_cumulative_orders_exhausting_dtbp_rejection PASSED [ 13%]
backend/tests/stress/test_m1_empirical_stress.py::TestBuyingPowerBoundsAndCashIntegrity::test_rapid_fire_rejection_stress_cash_invariance PASSED [ 20%]
backend/tests/stress/test_m1_empirical_stress.py::TestBuyingPowerBoundsAndCashIntegrity::test_sub_25k_pdt_margin_restriction_enforcement PASSED [ 26%]
backend/tests/stress/test_m1_empirical_stress.py::TestBuyingPowerBoundsAndCashIntegrity::test_position_flip_dtbp_bypass_vulnerability FAILED [ 33%]
backend/tests/stress/test_m1_empirical_stress.py::TestMicrostructureSlippageAndFees::test_kyles_lambda_square_root_scaling_with_order_size PASSED [ 40%]
backend/tests/stress/test_m1_empirical_stress.py::TestMicrostructureSlippageAndFees::test_kyles_lambda_inverse_sqrt_scaling_with_bar_volume PASSED [ 46%]
backend/tests/stress/test_m1_empirical_stress.py::TestMicrostructureSlippageAndFees::test_slippage_floor_and_stop_multiplier PASSED [ 53%]
backend/tests/stress/test_m1_empirical_stress.py::TestMicrostructureSlippageAndFees::test_high_frequency_regulatory_fees_precision PASSED [ 60%]
backend/tests/stress/test_m1_empirical_stress.py::TestMicrostructureSlippageAndFees::test_short_opening_fee_realized_pnl_accounting_leak FAILED [ 66%]
backend/tests/stress/test_m1_empirical_stress.py::TestMicrostructureSlippageAndFees::test_monte_carlo_long_only_ledger_invariance PASSED [ 73%]
backend/tests/stress/test_high_volume_bar_participation::test_multi_bar_partial_fill_10_percent_cap PASSED [ 80%]
backend/tests/stress/test_high_volume_bar_participation::test_low_volume_bar_minimum_fill_floor PASSED [ 86%]
backend/tests/stress/test_high_volume_bar_participation::test_limit_order_no_fill_when_price_not_touched PASSED [ 93%]
backend/tests/stress/test_high_volume_bar_participation::test_partial_fill_cancellation_working_order_cleanup PASSED [100%]

=================================== FAILURES ===================================
_ TestBuyingPowerBoundsAndCashIntegrity.test_position_flip_dtbp_bypass_vulnerability _
AssertionError: CRITICAL VULNERABILITY: Position flip of $1.5M bypassed DTBP bounds! (approved=True, reason=Approved)

_ TestMicrostructureSlippageAndFees.test_short_opening_fee_realized_pnl_accounting_leak _
AssertionError: ACCOUNTING LEAK: Realized PnL is 0.0, expected -2.00! Short entry fees were omitted from realized PnL.
where False = math.isclose(0.0, -2.0, abs_tol=0.01)
========================= 2 failed, 13 passed in 0.07s =========================
```

### Defect 1: Position Flip Buying Power & Concentration Cap Bypass
- **File**: `backend/app/core/account.py`
- **Lines**: 143–162:
```python
143:         is_increasing = False
144:         if side.upper() == "BUY" and (existing_pos is None or existing_pos.side == PositionSide.LONG):
145:             is_increasing = True
146:         elif side.upper() == "SELL" and (existing_pos is None or existing_pos.side == PositionSide.SHORT):
147:             is_increasing = True
148: 
149:         if is_increasing:
150:             if current_alloc + order_value > max_alloc + 0.01:
151:                 return False, f"Order exceeds per-position concentration cap of ${max_alloc:,.2f}"
152: 
153:             # Calculate required margin for the order
154:             if side.upper() == "BUY":
155:                 req_margin = 0.25 * order_value
156:             else:  # SHORT
157:                 req_margin = max(0.30 * order_value, 5.00 * qty)
158: 
159:             bp_needed = round(req_margin * 4.0, 2)
160:             if bp_needed > self.buying_power + 0.01:
161:                 return False, f"Insufficient Day Trading Buying Power: needed ${bp_needed:,.2f}, available ${self.buying_power:,.2f}"
162: 
163:         return True, "Approved"
```
When `existing_pos.side == PositionSide.LONG` (e.g., 10 shares of AAPL = $1,500), submitting an order with `side == "SELL"` and `qty == 10000` ($1,500,000 notional) results in `is_increasing = False`. Lines 150–161 are completely skipped, and `can_afford()` returns `True, "Approved"`. Upon execution, the account opens a 9,990 share short position ($1,498,500 short liability) on a $50,000 account, instantly triggering a $449,550 maintenance margin call.

### Defect 2: Short Position Opening Fee Omission from Realized PnL
- **File**: `backend/app/core/account.py`
- **Lines**: 194–197 (opening short):
```python
194:             if side_norm == "BUY":
195:                 self.cash = round(self.cash - (qty * price + fee), 2)
196:             else:
197:                 self.cash = round(self.cash + (qty * price - fee), 2)
```
- **Lines**: 274–286 (covering short):
```python
274:                 if qty < existing_pos.shares:
275:                     # Partial cover Short
276:                     realized_delta = round((qty * (existing_pos.avg_entry_price - price)) - fee, 2)
...
282:                 elif qty == existing_pos.shares:
283:                     # Full cover Short
284:                     realized_delta = round((qty * (existing_pos.avg_entry_price - price)) - fee, 2)
```
In US equities, regulatory SEC Section 31 and FINRA TAF fees are charged on SELL transactions. When a trader opens a short position, cash pays `fee`. When the trader covers the short via a BUY, `side_norm == "BUY"`, so `fee == 0.00`. `realized_delta` computes `(qty * (entry_price - price)) - fee = 0.00 - 0.00 = 0.00`. The fee paid when entering the short position is never deducted from `realized_pnl`. As a result, `realized_pnl` is permanently overstated, and `Equity != Initial Balance + Realized PnL + Unrealized PnL`.

### Process & Port Hygiene
Executed command:
```bash
lsof -i :8005 -i :8080 -i :3005 || echo "Ports are completely free"
```
Output:
```
Ports are completely free
```
All test processes terminated cleanly with zero lingering background processes or blocked ports.

---

## 2. Logic Chain

1. **DTBP Bounds for Standard Orders (Verified)**:
   - For fresh open orders and scaling orders in the same direction, `can_afford()` correctly enforces the $50,000 per-position concentration cap (25% of $200k DTBP) and rejects single orders > $200k or cumulative orders exceeding available margin excess ($20k remaining).
   - In 200 rapid-fire rejections, cash stayed invariant at $50,000.00, equity at $50,000.00, and working orders remained 0.

2. **DTBP Bypass on Position Flips (Defect 1 Reason)**:
   - In `can_afford()`, the logic treats any `SELL` order on a `LONG` position as closing/reducing (`is_increasing = False`).
   - However, if `order.qty > existing_pos.shares`, the order is a dual-operation: it closes the long position AND opens a fresh short position of size `flip_qty = order.qty - existing_pos.shares`.
   - Because `can_afford()` does not split the order or evaluate `flip_qty`, an order of arbitrary size (e.g., $1.5M, $10M) bypasses both the concentration cap and the 4:1 DTBP cap.

3. **Microstructure Slippage & Kyle's Lambda (Verified)**:
   - Kyle's lambda market impact term $0.08 \times \text{volatility} \times \sqrt{\frac{Q}{V}}$ accurately scales with $\sqrt{Q}$ (ratios 2.0x for 4x qty, 4.0x for 16x qty) and $1/\sqrt{V}$ (ratios 2.0x for 4x volume, 4.0x for 16x volume).
   - Minimum floor of 1 bps ($\max(0.01, P \times 0.0001)$) is strictly enforced under zero spread and zero volatility.
   - Stop-loss orders receive the 1.5x adverse multiplier.

4. **10% Bar Volume Participation Cap (Verified)**:
   - A 5,000-share limit order submitted against consecutive bars of 10,000, 15,000, 8,000, and 100,000 volume fills in chunks of 1,000, 1,500, 800, and 1,700 shares.
   - Weighted average fill price $\bar{P} = \frac{\sum Q_i P_i}{\sum Q_i}$ is calculated accurately.
   - Orders in `PARTIALLY_FILLED` state remain in `working_orders` until the final fill transitions status to `FILLED` and cleanly pops the order.

5. **Regulatory Fees & Balance Conservation Leak (Defect 2 Reason)**:
   - SEC Section 31 fee ($\lceil 0.0000278 \times \text{principal} \times 100 \rceil / 100$) and FINRA TAF ($\min(8.30, \text{round}(0.000166 \times Q, 2))$, min $0.01$) are correctly computed on sell orders.
   - However, when shorting, the regulatory fee is incurred on entry. When covering, `realized_pnl` only subtracts the exit fee ($0.00 for buys), failing to subtract the entry fee recorded in `existing_pos.fees_paid`.
   - This causes an accounting leakage where `Equity < Initial + Realized PnL`, violating the conservation law.

---

## 3. Caveats

- Unit and E2E test suites pass when position flips are small and short fees are spot-checked individually; the defects were only surfaced under adversarial stress testing.
- No modifications were made to implementation files in accordance with the `Review-only` constraint.
- Stress test harness was placed in `backend/tests/stress/test_m1_empirical_stress.py` in compliance with `PROJECT.md` layout rules (no code/tests in `.agents/`).

---

## 4. Conclusion

**Structured Verdict: REQUEST_CHANGES**

While the Execution Fill Simulator (Kyle's lambda slippage model, 10% bar participation cap, FSM order lifecycle) is implemented cleanly and performs robustly, the Paper Account Ledger contains two significant vulnerabilities:
1. **Critical Vulnerability**: `PaperTradingAccount.can_afford()` fails to check `flip_qty` on position flips, allowing multi-million dollar orders to bypass DTBP and concentration limits.
2. **High Defect**: Regulatory fees paid when opening short positions are omitted from `realized_pnl` upon covering, causing portfolio balance identity drift.

### Required Remediations
1. **Fix Position Flip DTBP & Allocation Check** in `backend/app/core/account.py`:
   - If `existing_pos` is LONG and `side == SELL` with `qty > existing_pos.shares`, evaluate `flip_qty = qty - existing_pos.shares` as an increasing short position:
     ```python
     if existing_pos and existing_pos.side == PositionSide.LONG and side.upper() == "SELL":
         if qty > existing_pos.shares:
             flip_qty = qty - existing_pos.shares
             flip_val = flip_qty * est_price
             if flip_val > max_alloc + 0.01:
                 return False, f"Position flip short leg exceeds concentration cap of ${max_alloc:,.2f}"
             req_margin = max(0.30 * flip_val, 5.00 * flip_qty)
             bp_needed = round(req_margin * 4.0, 2)
             if bp_needed > self.buying_power + 0.01:
                 return False, f"Insufficient Day Trading Buying Power for flip leg: needed ${bp_needed:,.2f}, available ${self.buying_power:,.2f}"
     ```
   - Mirror the same logic for `existing_pos.side == PositionSide.SHORT` and `side == "BUY"` with `qty > existing_pos.shares`.

2. **Fix Short Entry Fee Deduction from Realized PnL** in `backend/app/core/account.py`:
   - When covering a short position (lines 276, 284), deduct the prorated entry fee:
     ```python
     entry_fee = round(existing_pos.fees_paid * (qty / existing_pos.shares), 4) if existing_pos.shares > 0 else 0.0
     realized_delta = round((qty * (existing_pos.avg_entry_price - price)) - fee - entry_fee, 2)
     ```

---

## 5. Verification Method

To independently reproduce the empirical findings:

1. **Execute Empirical Stress Test Harness**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   PYTHONPATH=. pytest backend/tests/stress/test_m1_empirical_stress.py -v
   ```
   *Expected result*: Exactly 13 passed, 2 failed (`test_position_flip_dtbp_bypass_vulnerability` and `test_short_opening_fee_realized_pnl_accounting_leak`).

2. **Reproduce Position Flip Bypass via CLI**:
   ```bash
   python3 -c "
   from backend.app.core.account import PaperTradingAccount
   from datetime import datetime, timezone
   acc = PaperTradingAccount(50000.0)
   acc.apply_fill('ord1', 'AAPL', 'BUY', 10, 150.0, 0.0, datetime.now(timezone.utc))
   approved, reason = acc.can_afford('AAPL', 'SELL', 10000, 150.0)
   print('Bypass Check - Approved:', approved, 'Reason:', reason)
   assert not approved, 'Vulnerability reproduced!'
   "
   ```

3. **Reproduce Short Fee Accounting Leak via CLI**:
   ```bash
   python3 -c "
   from backend.app.core.account import PaperTradingAccount
   from datetime import datetime, timezone
   acc = PaperTradingAccount(50000.0)
   acc.apply_fill('ord1', 'XYZ', 'SELL', 100, 100.0, 2.0, datetime.now(timezone.utc))
   acc.apply_fill('ord2', 'XYZ', 'BUY', 100, 100.0, 0.0, datetime.now(timezone.utc))
   print('Realized PnL:', acc.realized_pnl, 'Expected: -2.00')
   print('Equity:', acc.equity, 'Expected:', acc.initial_balance + acc.realized_pnl)
   assert acc.realized_pnl == -2.0, 'Accounting leak reproduced!'
   "
   ```

4. **Verify Port Hygiene**:
   ```bash
   lsof -i :8005 -i :8080 -i :3005 || echo "Ports are completely free"
   ```
