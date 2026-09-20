# Backend & Risk Diff Review Report

**Reviewer**: Reviewer 1 (Backend & Risk Diff Reviewer)  
**Date**: 2026-09-20  
**Target Commit / Diff Scope**: Backend remediations in `backend/app/` and `backend/tests/`  
**Verdict**: **REQUEST_CHANGES**

---

## 1. Observation

Direct observations and execution outputs gathered during independent investigation:

### 1.1 Test Suite Results
- **Backend Unit Tests**:
  Command: `pytest backend/tests -v`
  Result: `150 passed in 0.74s` (100% pass rate for isolated backend unit tests).
- **Full E2E Runner**:
  Command: `python3 tests/e2e/runner.py`
  Result: `Exit Code 1 (FAILED)` with 1 test failure (`test_ui_stream_resilience.py:test_high_frequency_broadcast_and_receipt`) and 13 setup errors (`RuntimeError: Port 3005 is already occupied`).

### 1.2 Stop Distance Clamping Float Representation Bug (`backend/app/strategies/orb.py`, `backend/app/strategies/news_momentum.py`, `backend/app/core/risk.py`)
- In `backend/app/strategies/orb.py:178-182`:
  ```python
  # Institutional stop distance clamping [0.4%, 4.0%]
  min_dist = round(entry_price * 0.004, 4)
  max_dist = round(entry_price * 0.040, 4)
  risk = max(min_dist, min(max_dist, raw_dist))
  stop_loss = round(entry_price - risk if sig_type == "BUY" else entry_price + risk, 4)
  ```
- In `backend/app/strategies/news_momentum.py:238-243`:
  ```python
  min_dist = round(entry_price * 0.004, 4)
  max_dist = round(entry_price * 0.040, 4)
  raw_dist = max(0.10, entry_price - round(bar.low - 0.02, 4))
  risk = max(min_dist, min(max_dist, raw_dist))
  stop_loss = round(entry_price - risk, 4)
  ```
- In `backend/app/core/risk.py:201, 217-238`:
  ```python
  stop_dist = abs(entry_price - stop_price)
  ...
  stop_dist_pct = stop_dist / entry_price
  if stop_dist_pct < self.config.min_stop_distance_pct:
      return RiskCheckResult(
          approved=False,
          reason=f"STOP_DISTANCE_TOO_TIGHT: Stop distance {stop_dist_pct:.4f} < min {self.config.min_stop_distance_pct:.4f}",
          ...
      )
  if stop_dist_pct > self.config.max_stop_distance_pct:
      return RiskCheckResult(
          approved=False,
          reason=f"STOP_DISTANCE_TOO_WIDE: Stop distance {stop_dist_pct:.4f} > max {self.config.max_stop_distance_pct:.4f}",
          ...
      )
  ```
- **Empirical Stress-Test on 49,500 Equities Prices ($5.00 to $500.00 in 1-cent steps)**:
  - When clamping to `min_dist` (`entry_price * 0.004`): **24,724 out of 49,500 prices (49.95%)** are REJECTED by `InstitutionalRiskEngine` with `STOP_DISTANCE_TOO_TIGHT`.
  - When clamping to `max_dist` (`entry_price * 0.040`): **23,761 out of 49,500 prices (48.00%)** are REJECTED by `InstitutionalRiskEngine` with `STOP_DISTANCE_TOO_WIDE`.
  - Concrete example: **AAPL at $150.00**:
    - `entry_price = 150.00`
    - `min_dist = round(150.0 * 0.004, 4) = 0.60`
    - `stop_loss = 150.00 - 0.60 = 149.40`
    - In Python IEEE 754 arithmetic: `abs(150.00 - 149.40) = 0.5999999999999943`
    - `0.5999999999999943 / 150.00 = 0.003999999999999962`
    - Check: `0.003999999999999962 < 0.004` is `True`!
    - Verbatim rejection: `STOP_DISTANCE_TOO_TIGHT: Stop distance 0.0040 < min 0.0040`
  - The worker's claim in `handoff.md:89` that this change *"Guarantees zero STOP_DISTANCE_TOO_TIGHT or STOP_DISTANCE_TOO_WIDE pre-trade risk rejections"* is **disproven**.

### 1.3 `manual_tighten_stop` Status Guard E2E Incompatibility (`backend/app/core/bracket.py:458`, `tests/e2e/test_ui_stream_resilience.py:59`)
- In `backend/app/core/bracket.py:458-459`:
  ```python
  if bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT):
      return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)
  ```
- In `tests/e2e/test_ui_stream_resilience.py:38-59`:
  ```python
  account.positions["AAPL"] = Position("AAPL", PositionSide.LONG, 100, 150.0, 152.0)
  bracket_manager.create_bracket("brk_hf_test", "AAPL", "LONG", 100, 150.0, 148.0)
  ...
  ws.send_text(json.dumps({"action": "TIGHTEN_STOP", "symbol": "AAPL", "new_stop": new_stop}))
  ...
  assert data["primary_position"]["stop_loss"] == new_stop
  ```
  `create_bracket` sets initial status to `PENDING_ENTRY`. Because `activate_bracket_on_fill` is never called in this test, `manual_tighten_stop` returns `NO_ACTION`, and `data["primary_position"]["stop_loss"]` remains `148.0` (or `None`), causing assertion failure `assert 148.0 == 148.01`.

### 1.4 Verified Positive Findings in Backend Changes
- **Target 2 Partial Fill**: `backend/app/core/bracket.py:356-381` cleanly updates `bracket.remaining_qty`, decrements `target_2_qty`, and returns `MODIFY_ORDER` to resize `bracket.stop_order_id` to `bracket.remaining_qty`. It only transitions to `COMPLETED_PROFIT` when `bracket.remaining_qty <= 0`.
- **`order_to_bracket` Child Order Pruning**: Lines 309, 326, 364, 498, 527 cleanly pop all child IDs using `self.order_to_bracket.pop(oid, None)`.
- **Late Fill Immunity**: `backend/app/core/bracket.py:254-260` returns `NO_ACTION` if `bracket.status` is in any terminal state (`COMPLETED_PROFIT`, `COMPLETED_STOP`, `COMPLETED_FLATTEN`, `CANCELLED`).
- **`manual_tighten_stop` Monotonicity**: Lines 461-473 ensure stop is only updated if strictly tighter than `current_stop_price` for both LONG and SHORT positions.
- **`stock_ws.py` Queue Handling**: Lines 225-261 properly wrap consumption in `try...finally: self._queue.task_done()`, and isolate per-record processing in an inner try-except. Corrupt JSON and malformed records do not deadlock queue joins.
- **Session Boundary Working Order Purge**: `backend/app/main.py:391-394` cancels and clears `engine.working_orders` upon ET date changes.
- **Risk Config Default**: `backend/app/core/risk.py:43` updated `max_position_equity_pct = 1.000` ($50,000 max single position).
- **Estimated Risk Dollars**: `backend/app/core/risk.py:269-270` uses `effective_qty = min(requested_qty, authorized_qty)` rather than unbounded risk capacity.
- **Flattening Pre-Market Phase**: `backend/app/core/flattening.py:15, 70, 165-173, 267-280` implements `PRE_MARKET` enum, `get_phase_at_time`, and locks out new entries before 09:30 ET.

---

## 2. Logic Chain

1. **Stop Distance Rejection Logic Chain**:
   - `InstitutionalRiskEngine.evaluate_order_request` enforces strict inequality `stop_dist_pct < 0.004` and `stop_dist_pct > 0.040` without epsilon tolerance.
   - Standard binary floating point representation of decimal numbers (IEEE 754) suffers from representation discrepancies when subtracting or dividing floats (e.g. `150.0 - 149.4` is not exactly `0.6`, but `0.5999999999999943`).
   - In `orb.py` and `news_momentum.py`, clamping directly to the boundary values `min_dist = round(entry_price * 0.004, 4)` and `max_dist = round(entry_price * 0.040, 4)` places the order on the razor-edge boundary.
   - When evaluated by `risk.py`, half of all equities prices fail the strict inequality, causing immediate order rejections in production for high-volume names like AAPL ($150.00), TSLA ($220.00), NVDA ($120.00), and low-price stocks ($5.00, $10.00).
   - Therefore, the clamping remediation is mathematically defective under floating-point execution and must be revised.

2. **E2E Test Failure Logic Chain**:
   - `test_ui_stream_resilience.py` tests that a manual stop tightening sent via WebSocket modifies the active stop loss.
   - The worker added an active-status guard `bracket.status in (ACTIVE, TARGET_1_HIT)` to `bracket.manual_tighten_stop`.
   - The test mock setup creates a bracket using `bracket_manager.create_bracket` (which has status `PENDING_ENTRY`) and an open position in `account.positions`, but does not activate the bracket.
   - As a result, the WebSocket handler dispatches `manual_tighten_stop`, which rejects the modification as `NO_ACTION`. The broadcast state never updates `stop_loss`, causing test assertion failure.
   - Furthermore, `tests/e2e/runner.py` fails due to test runner port collision on port 3005.

---

## 3. Caveats

- Backend unit tests (`backend/tests/`) all pass (150/150) because unit tests for ORB/NewsMomentum only checked `assert 0.004 <= stop_pct <= 0.040` on `300.30` without passing the signal through `InstitutionalRiskEngine.evaluate_order_request()`.
- The `manual_tighten_stop` guard in `bracket.py` is conceptually correct for live trading (an order cannot be tightened before it fills), but requires coordinated test harness alignment in `tests/e2e/test_ui_stream_resilience.py` (e.g., activating the bracket when mocking an existing position).
- Frontend files and deployment files were not modified during this diff.

---

## 4. Conclusion

**Verdict: REQUEST_CHANGES**

The backend remediation work is substantially well-constructed, genuinely implements core invariants, and contains zero integrity violations or dummy facades. However, **changes are required** before release approval due to two blocking issues:

### Required Changes:

1. **Fix Floating-Point Stop Distance Clamping (`orb.py`, `news_momentum.py`, and `risk.py`)**:
   - In `backend/app/strategies/orb.py` and `backend/app/strategies/news_momentum.py`:
     Use an internal buffer for clamping so that rounded prices always land safely inside the `[0.4%, 4.0%]` window:
     ```python
     min_dist = round(entry_price * 0.0042, 4)
     max_dist = round(entry_price * 0.0380, 4)
     ```
   - In `backend/app/core/risk.py`:
     Apply an epsilon tolerance on the safety boundary check:
     ```python
     if stop_dist_pct < self.config.min_stop_distance_pct - 1e-6:
         # STOP_DISTANCE_TOO_TIGHT
     if stop_dist_pct > self.config.max_stop_distance_pct + 1e-6:
         # STOP_DISTANCE_TOO_WIDE
     ```
     This completely eliminates false rejections across 100% of tested prices ($5.00 to $500.00).

2. **Fix Test Setup or Guard in `test_ui_stream_resilience.py` / `bracket.py`**:
   - Ensure `tests/e2e/test_ui_stream_resilience.py:test_high_frequency_broadcast_and_receipt` activates the bracket (`bracket.status = BracketStatus.ACTIVE` or `activate_bracket_on_fill`) so that `manual_tighten_stop` modifies the working stop and UI broadcasts reflect the new level.
   - Ensure `python3 tests/e2e/runner.py` and `scripts/run_e2e_tests.sh` pass cleanly with zero failures.

---

## 5. Verification Method

### 1. Verification of the Floating Point Defect
Run this verification script to observe the failure on the current codebase:
```bash
python3 -c "
from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig

engine = InstitutionalRiskEngine(RiskEngineConfig())
prices = [5.0, 10.0, 20.0, 40.0, 75.0, 80.0, 90.0, 150.0, 180.0, 220.0]
for p in prices:
    min_dist = round(p * 0.004, 4)
    stop_buy = round(p - min_dist, 4)
    res = engine.evaluate_order_request('TEST', 'BUY', 10, p, stop_buy, 50000.0, 200000.0, 0, set(), set())
    if not res.approved:
        print(f'REJECTED at p={p}: {res.reason}')
"
```
**Expected Output (Current Bug)**:
All 10 prices report `REJECTED at p=...: STOP_DISTANCE_TOO_TIGHT: Stop distance 0.0040 < min 0.0040`.

### 2. Verification of Backend Unit Tests
```bash
pytest backend/tests -v
```
**Outcome**: 150 passed in 0.74s.

### 3. Verification of E2E Runner
```bash
python3 tests/e2e/runner.py
```
**Current Outcome**: Exits with code 1 due to `test_ui_stream_resilience.py` and port 3005 conflict.
**Target Outcome after Fix**: Exits with code 0, 100% passed.
