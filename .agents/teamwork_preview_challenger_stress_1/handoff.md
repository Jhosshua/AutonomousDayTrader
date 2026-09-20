# Handoff Report: Challenger 1 — Stress & Invariant Verification

## Executive Verdict: `REQUEST_CHANGES`

While the core remediations for **Target 2 partial fills**, **monotonic stop tightening**, and **queue backpressure** operate correctly under empirical stress testing, two critical verification failures and one telemetry bug require changes before final deployment:
1. `tests/e2e/test_ui_stream_resilience.py::test_high_frequency_broadcast_and_receipt` fails with `AssertionError: assert 148.0 == 148.01` due to an unactivated bracket mock interacting with the hardened `manual_tighten_stop` guard.
2. `StockWebSocketClient._process_queue_loop` in `backend/app/ingestion/stock_ws.py:236` increments `bars_received`, `quotes_received`, and `trades_received` before schema validation, corrupting ingestion metrics when malformed items arrive.
3. Peer file `tests/e2e/test_challenger_bracket_2.py` introduced broken tests (`TypeError` on `NewsEvent` and `AttributeError` on `ExecutionEngine.apply_fill`) that cause `scripts/run_e2e_tests.sh` to fail.

---

## 1. Observation

### Obs 1: Target 2 Partial Fill Resizing Invariant Verified
- **File**: `backend/app/core/bracket.py:354-382`
- **Code**:
  ```python
  elif child_type == BracketChildType.TAKE_PROFIT_2:
      bracket.remaining_qty = max(0, bracket.remaining_qty - filled_qty)
      if bracket.remaining_qty <= 0:
          bracket.target_2_filled = True
          bracket.status = BracketStatus.COMPLETED_PROFIT
          ...
      else:
          bracket.target_2_qty = max(0, bracket.target_2_qty - filled_qty)
          return BracketUpdateDirective(
              action="MODIFY_ORDER",
              orders_to_modify=[{
                  "order_id": bracket.stop_order_id,
                  "new_qty": bracket.remaining_qty,
                  "new_stop_price": bracket.current_stop_price,
              }],
              bracket_status=bracket.status,
          )
  ```
- **Execution**: Tested in `backend/tests/stress/test_challenger_stress_invariants.py::TestTarget2PartialFillsAndStopResizing` (5 tests).
  - Single partial fill (20 of 50 shares): `bracket.remaining_qty` became 30, `bracket.target_2_qty` became 30, `orders_to_modify` resized stop order to 30 shares at current ratcheted stop price (100.02). Stop order remained mapped in `order_to_bracket`.
  - Multi-step partial fills (10, 15, 15, 10 shares): Resized stop monotonically at each step; final fill transitioned to `COMPLETED_PROFIT` and cancelled stop order.
  - Partial fill followed by residual stop fill: Stop order executed for remaining 30 shares, cancelled residual Target 2 order, and prevented short flip.

### Obs 2: Monotonic Stop Tightening & Concurrency Verified
- **File**: `backend/app/core/bracket.py:444-482`
- **Code**:
  ```python
  if bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT):
      return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)
  ...
  if bracket.side == "LONG":
      if new_stop_price > bracket.current_stop_price:
          bracket.current_stop_price = new_stop_price
          tightened = True
  ```
- **Execution**: Tested in `backend/tests/stress/test_challenger_stress_invariants.py::TestRapidConcurrentStopTightening` (4 tests).
  - 500 randomized bidirectional price modifications: Stop never loosened (strictly non-decreasing for LONG, non-increasing for SHORT).
  - 200 concurrent async coroutines calling `manual_tighten_stop` simultaneously: Zero race conditions, final stop converged to the global maximum price.

### Obs 3: Test Failure in `test_ui_stream_resilience.py`
- **Command**: `pytest tests/e2e/test_ui_stream_resilience.py`
- **Verbatim Error**:
  ```
  FAILED tests/e2e/test_ui_stream_resilience.py::test_high_frequency_broadcast_and_receipt - AssertionError: assert 148.0 == 148.01
  tests/e2e/test_ui_stream_resilience.py:59: AssertionError
  ```
- **Cause**: Line 36 sets up `bracket_manager.create_bracket("brk_hf_test", "AAPL", "LONG", 100, 150.0, 148.0)` without calling `bracket_manager.activate_bracket_on_fill(...)`. Because the bracket is in `BracketStatus.PENDING_ENTRY`, `manual_tighten_stop` rejects the modification, returning `NO_ACTION`, and leaving `stop_loss` at 148.0.

### Obs 4: Telemetry Inflation on Malformed Frames in `StockWebSocketClient`
- **File**: `backend/app/ingestion/stock_ws.py:234-237`
- **Code**:
  ```python
  t = m.get("T")
  if t == "b":
      self.bars_received += 1
      await self.bus.publish(BarEvent.from_relay_dict(m))
  ```
- **Verbatim Error**: When feeding 3 malformed `T: "b"` objects (e.g. missing timestamp `t` or price fields) plus 1 valid bar into `StockWebSocketClient`, `client.bars_received` was `4` despite only `1` bar being validated and published to `EventBus`.
- `BarEvent.from_relay_dict(m)` raised `KeyError: 't'`, caught by line 254 (`Error processing individual market item: 't'`), but `self.bars_received` was already incremented.

### Obs 5: Peer Test File Failures Breaking E2E Suite
- **Command**: `bash scripts/run_e2e_tests.sh`
- **Verbatim Error**:
  ```
  TypeError: __init__() missing 2 required positional arguments: 'summary' and 'source'
  tests/e2e/test_challenger_bracket_2.py:303
  AttributeError: 'ExecutionEngine' object has no attribute 'apply_fill'
  tests/e2e/test_challenger_bracket_2.py:441
  ```

### Obs 6: Process and Port Hygiene Verified
- **Command**: `lsof -i :8005 -i :3005 -i :8080`
- **Result**: Completely clean. Zero bound sockets or lingering background daemons.
- Tested via `TestPortAndProcessHygiene.test_ports_8005_3005_8080_are_free` (PASSED).

---

## 2. Logic Chain

1. **Target 2 Invariant**: From Obs 1, when Target 2 partially fills, `bracket.remaining_qty` is decreased by `filled_qty`, `target_2_qty` is decreased by `filled_qty`, and `MODIFY_ORDER` updates `bracket.stop_order_id` with `new_qty = bracket.remaining_qty`. The stop order remains active and sized to the open position. When the stop executes later, residual Target 2 is cancelled, preventing naked short exposure.
2. **Stop Tightening Hardening**: From Obs 2, `manual_tighten_stop` correctly enforces that stops only tighten when a bracket is in `ACTIVE` or `TARGET_1_HIT` state, preventing state corruption on pending or dead brackets.
3. **E2E Test Regression**: From Obs 3, `test_high_frequency_broadcast_and_receipt` was authored before the `PENDING_ENTRY` guard was added. It directly mocks a position in `account.positions` and creates a bracket via `create_bracket`, but forgets `activate_bracket_on_fill`. As a result, the backend correctly refuses to tighten the pending bracket, causing the test's assertion to fail. This blocks `scripts/run_e2e_tests.sh` from achieving the required 100% pass rate.
4. **Telemetry Inaccuracy**: From Obs 4, incrementing `bars_received`, `quotes_received`, and `trades_received` before `from_relay_dict` validation causes malformed frames to be counted as successfully received market bars. The counter should only increment after validation succeeds.
5. **Port Hygiene**: From Obs 6, all ports (8005, 3005, 8080) are cleanly released with zero leaked daemons.

---

## 3. Caveats

- We did not modify any implementation code (conforming to the strict Review-only constraint).
- The newly created empirical stress test file `backend/tests/stress/test_challenger_stress_invariants.py` was committed to the test suite layout per `PROJECT.md` rules and verified locally with 13/13 passing tests.
- Playwright-based tests in `test_challenger_mobile.py` require a running frontend server; running them standalone without the Next.js server produces connection errors.

---

## 4. Conclusion

**Verdict: `REQUEST_CHANGES`**

The core algorithmic fixes for Target 2 partial fills and stop tightening are robust and verified. However, changes are requested to achieve a clean release:
1. **Fix `tests/e2e/test_ui_stream_resilience.py:37`**: Add `bracket_manager.activate_bracket_on_fill("brk_hf_test", 100, 150.0, datetime.now(timezone.utc))` so the test position has an `ACTIVE` bracket.
2. **Fix `backend/app/ingestion/stock_ws.py:234-243`**: Move counter increments (`self.bars_received += 1`, etc.) after the event object is successfully constructed and validated.
3. **Fix or Remove `tests/e2e/test_challenger_bracket_2.py`**: Fix the constructor calls for `NewsEvent` (pass `summary` and `source`) and replace `engine.apply_fill` with `account.apply_fill`.

---

## 5. Verification Method

### Test Suite Execution
1. Run Challenger 1 Empirical Stress Harness:
   ```bash
   pytest backend/tests/stress/test_challenger_stress_invariants.py -v
   ```
   *Expected Result*: 13 passed in ~0.2s.

2. Run All Backend Tests:
   ```bash
   pytest backend/tests/
   ```
   *Expected Result*: 163 passed in ~0.8s.

3. Reproduce UI Stream Test Failure:
   ```bash
   pytest tests/e2e/test_ui_stream_resilience.py::test_high_frequency_broadcast_and_receipt
   ```
   *Expected Result*: Fails with `assert 148.0 == 148.01`.

4. Verify Port Hygiene:
   ```bash
   lsof -i :8005 -i :3005 -i :8080
   ```
   *Expected Result*: Clean exit, zero active listeners.

---

## Adversarial Review Challenge Report

### Challenge Summary
**Overall risk assessment**: MEDIUM

### Challenges

#### [High] Challenge 1: Unactivated Bracket Setup in Resilience Suite Causes E2E Suite Failure
- **Assumption challenged**: That `manual_tighten_stop` can be invoked on any bracket regardless of status.
- **Attack scenario**: The backend correctly tightened its invariant: only `ACTIVE` and `TARGET_1_HIT` brackets may have stops tightened. However, existing E2E test `test_high_frequency_broadcast_and_receipt` creates a `PENDING_ENTRY` bracket and immediately attempts 100 stop tightening requests.
- **Blast radius**: Breaks `pytest tests/e2e/` and `scripts/run_e2e_tests.sh`, failing the 100% pass criterion.
- **Mitigation**: Update line 37 of `tests/e2e/test_ui_stream_resilience.py` to call `activate_bracket_on_fill`.

#### [Medium] Challenge 2: Premature Telemetry Counter Increments on Malformed Feed Payloads
- **Assumption challenged**: That wire items matching `T: "b"` are valid market bars.
- **Attack scenario**: Upstream feed sends malformed JSON payloads with missing timestamps or NaN prices. `_process_queue_loop` increments `bars_received` before validating the payload with `BarEvent.from_relay_dict`.
- **Blast radius**: Monitoring metrics display inflated bar counts while EventBus receives no events.
- **Mitigation**: Construct and validate the event first, then increment counters and publish.

### Stress Test Results
- Target 2 partial fill stop resizing (5 micro-fills) → Stop resized to exact residual quantity → PASS
- Target 2 partial fill followed by stop trigger → Residual Target 2 cancelled, no position flip → PASS
- 500 bidirectional random stop adjustments → Monotonicity strictly preserved → PASS
- 200 concurrent async stop tightening tasks → Global maximum reached, zero corruption → PASS
- Queue backpressure (capacity 10, burst 50) → 40 dropped, 10 processed, worker alive → PASS
- Malformed JSON frames (12 corrupt payloads) → Worker task survives, recovers on next frame → PASS
- High-volume order flow (500 orders, fills, cancels) → FSM states valid, ledger conserved → PASS
- Port release audit (8005, 3005, 8080) → All ports free and released → PASS

### Unchallenged Areas
- 24/7 continuous multi-session memory growth under live dxFeed socket streaming (requires multi-day execution beyond development test harness).
