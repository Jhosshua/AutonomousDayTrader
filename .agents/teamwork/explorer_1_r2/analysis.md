# Forensic Integrity Remediation Analysis: E2E Replay Stop-Loss Anchoring

**Analyst**: Explorer 1 Iteration 2 (`teamwork_preview_explorer` / `explorer_1_r2`)  
**Role**: Audit Integrity Remediation Explorer  
**Target Milestone**: Audit Integrity Remediation  
**Date**: 2026-09-24T00:38:00Z  
**Target File**: `tests/e2e/test_swing_multiday_replay.py` (Lines 220–225)  

---

## 1. Executive Summary

During the Forensic Integrity Audit of Worker 1's code changes, Auditor 1 uncovered an **INTEGRITY VIOLATION**: while all 442 unit tests in `pytest backend/tests` passed, the project-mandated E2E test runner (`python3 tests/e2e/runner.py`) failed with exit code 1 due to a single hard assertion failure in `tests/e2e/test_swing_multiday_replay.py:224`:
```
AssertionError: assert 639.28 == 639.15
```

This investigation demonstrates conclusively that:
1. The production code in `backend/app/strategies/swing_panic_dip.py` is **100% mathematically correct and authentic**, strictly satisfying Rule 6 of `ORIGINAL_REQUEST.md` by anchoring emergency stop-loss orders to the realized `fill.price` (including slippage) rather than the theoretical open price.
2. Worker 1 correctly updated unit tests in `backend/tests/test_swing_strategy.py:225` and the dry run in `scripts/run_integrated_swing_dry_run.py:157`, but omitted updating `tests/e2e/test_swing_multiday_replay.py:223–224`.
3. Updating line 223 to anchor `expected_stop` to `lrcx_pos.avg_entry_price` resolves the failure immediately, allowing all 325 E2E tests to pass with 0 failures and 0 regressions.
4. A machine-applicable diff patch has been generated and validated with `git apply --check`.

---

## 2. Evidence Chain & Root Cause Analysis

### 2.1 Authoritative Requirement: Rule 6 (Emergency Stop-Loss)
From `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md` lines 482–483 and 527:
> **Rule 6 (Emergency Stop-Loss)**:  
> "Immediately establish a hard stop-loss at $2.5 \times \text{Daily ATR(14)}$ below the fill price."  
> Acceptance Criterion:  
> "- [ ] Hard stop-loss at $2.5 \times \text{Daily ATR(14)}$ is active immediately upon fill."

Notice the specification mandates anchoring to the **fill price**, not the theoretical bar open price.

### 2.2 Production Implementation Trace
In `backend/app/strategies/swing_panic_dip.py` lines 582–602:
```python
fill = self.execution_engine._execute_fill(
    order=order_obj,
    qty=qty,
    price=fill_price,
    slippage=slippage,
    timestamp=open_time,
)

# Rule 6: Stop Anchored strictly to realized fill.price
realized_stop_price = round(fill.price - stop_distance, 2)

# Explicitly populate swing metadata on position
pos = self.account.positions.get(sym)
if pos:
    pos.arm = TradingArm.SWING
    pos.strategy_id = "swing_panic_dip"
    pos.stop_loss_price = realized_stop_price
    pos.entry_atr = entry_order.daily_atr
    pos.entry_date = open_time.date() if isinstance(open_time, datetime) else open_time
    pos.holding_days = 1
```

When an order executes, `ExecutionEngine._execute_fill` calls `account.apply_fill` using `fill.price`, which sets `pos.avg_entry_price = fill.price`. The position's `stop_loss_price` is calculated as `round(fill.price - 2.5 * ATR, 2)`.

### 2.3 Mathematical Trace of the Failure
In `tests/e2e/test_swing_multiday_replay.py:211–224`:
1. `lrcx_open_price` = $659.00
2. `strategy_engine.execute_market_open({"LRCX": lrcx_open_price}, open_time_day2)` executes:
   - Dynamic slippage is calculated by `calculate_slippage`: `slippage = $0.13`
   - Realized fill price: `fill.price` = $659.00 + $0.13 = $659.13
   - Stored on position: `lrcx_pos.avg_entry_price` = $659.13
3. Daily ATR evaluated on Day 1 close:
   - `daily_atr` = 7.9418
   - Multiplier = 2.5
   - Stop distance: $2.5 \times 7.9418 = 19.8545$
4. Production calculation:
   - $659.13 - 19.8545 = 639.2755 \implies \text{round}(639.2755, 2) = \mathbf{639.28}$
   - `lrcx_pos.stop_loss_price` = $639.28
5. Defective test assertion at line 223:
   - `expected_stop = round(lrcx_open_price - 2.5 * daily_atr, 2)`
   - $659.00 - 19.8545 = 639.1455 \implies \text{round}(639.1455, 2) = \mathbf{639.15}$
   - Line 224 asserts: `assert 639.28 == 639.15` $\implies$ `AssertionError`!

The discrepancy between 639.28 and 639.15 is exactly $0.13, which is precisely the slippage added to the fill price.

### 2.4 Inconsistency Across Test Suites
Worker 1 updated the unit test and dry run script during Defect 6 remediation, but missed the E2E test:
- **`backend/tests/test_swing_strategy.py:225`**:
  ```python
  # Emergency Stop check: P_fill - 2.5 * Daily_ATR (anchored to realized fill price)
  assert pos.stop_loss_price == round(pos.avg_entry_price - 2.5 * 5.0, 2)
  ```
- **`scripts/run_integrated_swing_dry_run.py:157`**:
  ```python
  expected_stop_lrcx = round(lrcx_pos.avg_entry_price - 2.5 * staged_lrcx["daily_atr"], 2)
  assert lrcx_pos.stop_loss_price == expected_stop_lrcx
  ```
- **`tests/e2e/test_swing_multiday_replay.py:223` (Omitted)**:
  ```python
  # Rule 6 check: stop loss established at open - 2.5 * ATR
  daily_atr = eval_day1["staged_entries"][0]["daily_atr"]
  expected_stop = round(lrcx_open_price - 2.5 * daily_atr, 2)
  assert lrcx_pos.stop_loss_price == expected_stop
  ```

---

## 3. Scope of Impact Analysis

### 3.1 E2E Test Suite Scope
Execution of `python3 tests/e2e/runner.py` shows:
- **Total Tests Collected**: 325
- **Passed**: 324
- **Failed**: 1 (`tests/e2e/test_swing_multiday_replay.py::TestSwingMultiDayReplay::test_multiday_full_lifecycle_and_exit_rules`)
- **Pass Rate**: 99.69% $\to$ 100.0% upon fixing this assertion.

### 3.2 Downstream Side-Effects within `test_swing_multiday_replay.py`
Analysis of the subsequent days in `test_multiday_full_lifecycle_and_exit_rules`:
- **Day 2**: Intraday flattening liquidates SPY, LRCX survives intact.
- **Day 3**: KLAC executes open entry. MU rejected by 2-position concurrency cap.
- **Day 4**: LRCX closes above 5-day SMA $\implies$ Rule 7a exit staged.
- **Day 5**: LRCX sells at open at profit (`realized_pnl > 0.0`). KLAC RSI(2) > 70 $\implies$ Rule 7b exit staged.
- **Day 6**: KLAC sells at open at profit. Account equity > $50,000.

LRCX price rallies every day ($659 \to 661 \to 664 \to 694 \to 695$), never falling near the stop price ($639.28$).
Therefore, correcting `expected_stop` at line 223 introduces **zero downstream test side-effects** or unexpected state changes.

### 3.3 Empirical In-Memory Test Verification
Executing all 5 tests in `TestSwingMultiDayReplay` with the updated assertion:
1. `test_multiday_full_lifecycle_and_exit_rules`: **PASS**
2. `test_emergency_stop_intraday_protection`: **PASS**
3. `test_time_stop_exit_at_5_days`: **PASS**
4. `test_earnings_blackout_and_exit_veto`: **PASS**
5. `test_ui_payload_serialization`: **PASS**

Result: **5/5 PASS (100%)**.

---

## 4. Remediation Blueprint

### 4.1 Target Location
- **File**: `/Users/mo/AutonomousDayTrader/tests/e2e/test_swing_multiday_replay.py`
- **Lines**: 221–224

### 4.2 Exact Code Replacement
```python
<<<<<<< BEFORE
        # Rule 6 check: stop loss established at open - 2.5 * ATR
        daily_atr = eval_day1["staged_entries"][0]["daily_atr"]
        expected_stop = round(lrcx_open_price - 2.5 * daily_atr, 2)
        assert lrcx_pos.stop_loss_price == expected_stop
=======
        # Rule 6 check: stop loss established at realized fill price - 2.5 * ATR
        daily_atr = eval_day1["staged_entries"][0]["daily_atr"]
        expected_stop = round(lrcx_pos.avg_entry_price - 2.5 * daily_atr, 2)
        assert lrcx_pos.stop_loss_price == expected_stop
>>>>>>> AFTER
```

### 4.3 Patch File
A verified patch file has been created at:
`/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_r2/fix_test_swing_multiday_replay.patch`

Validation command:
```bash
git apply --check .agents/teamwork/explorer_1_r2/fix_test_swing_multiday_replay.patch
```
Result: Clean application with zero conflicts.

---

## 5. Verification Commands for Implementer / Auditor

1. **Apply Patch**:
   ```bash
   git apply .agents/teamwork/explorer_1_r2/fix_test_swing_multiday_replay.patch
   ```
2. **Execute Single Test**:
   ```bash
   pytest tests/e2e/test_swing_multiday_replay.py -k test_multiday_full_lifecycle_and_exit_rules -v
   ```
   *Expected*: `1 passed in ~0.15s`
3. **Execute Full E2E Suite**:
   ```bash
   python3 tests/e2e/runner.py
   ```
   *Expected*: `325 passed in ~25.8s`, `Exit Code: 0 (SUCCESS - ALL PASSED)`
4. **Execute Full Backend Unit Suite**:
   ```bash
   pytest backend/tests -q
   ```
   *Expected*: `442 passed in ~7.3s`
5. **Verify Port Hygiene**:
   ```bash
   bash scripts/verify_port_hygiene.sh
   ```
   *Expected*: `All ports verified clean. Zero lingering daemons.`
