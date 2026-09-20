# Strategy Report: Bracket Lifecycle Resilience & E2E Test Suite Remediation

**Explorer**: Explorer 1 (Bracket & Test Resilience Explorer)  
**Date**: 2026-09-20  
**Project**: AutonomousDayTrader  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_1`  
**Target Files Analyzed**:
- `tests/e2e/test_ui_stream_resilience.py`
- `tests/e2e/test_challenger_bracket_2.py`
- `backend/app/core/bracket.py`
- `backend/app/main.py`
- `backend/app/core/risk.py`
- `backend/app/strategies/orb.py`
- `backend/app/strategies/news_momentum.py`
- `backend/app/ingestion/stock_ws.py`

---

## 1. Executive Summary & Root Cause Anatomy

During independent multi-agent audit and verification, `scripts/run_e2e_tests.sh` (which executes `tests/e2e/runner.py`) resulted in exit code 1 with a single failing test out of 318 collected tests.

Our investigation uncovered that this failure exhibits a **dual manifestation** depending on execution context, driven by **two interacting root causes**:

```
                              [E2E Failure Root Causes]
                                          |
          +-------------------------------+-------------------------------+
          |                                                               |
  [Root Cause 1: Missing Fill Activation]              [Root Cause 2: Cross-Test State Leakage]
  tests/e2e/test_ui_stream_resilience.py               tests/e2e/test_challenger_bracket_2.py
  - Bracket created in PENDING_ENTRY                   - test_session_boundary_purges_partially_filled_orders
  - activate_bracket_on_fill never called               - Fills 10 shares of AMD into shared account
  - manual_tighten_stop returns NO_ACTION              - AMD retained at index 0 of account.positions
          |                                                               |
  Standalone Execution Error:                          Full Suite Execution Error:
  assert 148.0 == 148.01                               assert None == 148.01
  (Stop remained at initial 148.0)                     (primary_position was AMD with no bracket)
```

### Manifestation A (Standalone Run): `assert 148.0 == 148.01`
When running `pytest tests/e2e/test_ui_stream_resilience.py::test_high_frequency_broadcast_and_receipt` in isolation:
- The test sets `account.positions["AAPL"] = Position(...)` and calls `bracket_manager.create_bracket("brk_hf_test", "AAPL", "LONG", 100, 150.0, 148.0)`.
- Initial bracket status is `BracketStatus.PENDING_ENTRY`.
- When the test emits 100 WebSocket messages with `{"action": "TIGHTEN_STOP", "symbol": "AAPL", "new_stop": 148.01...}`, `backend/app/main.py:1149` invokes `bracket_manager.manual_tighten_stop("AAPL", new_stop)`.
- `backend/app/core/bracket.py:458` guards stop tightening with:
  ```python
  if bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT):
      return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)
  ```
- Because `activate_bracket_on_fill` was never called by the test fixture, the bracket remains `PENDING_ENTRY`. `manual_tighten_stop` returns `NO_ACTION`, the working stop is not modified, and the broadcast payload retains `primary_position.stop_loss = 148.0`.
- The test asserts `assert data["primary_position"]["stop_loss"] == new_stop`, which fails on the first iteration (`assert 148.0 == 148.01`).

### Manifestation B (Full Runner Run): `assert None == 148.01`
When running the full test runner (`python3 tests/e2e/runner.py` or `./scripts/run_e2e_tests.sh`):
- Earlier in the test collection order, `tests/e2e/test_challenger_bracket_2.py::TestSessionBoundaryPurge::test_session_boundary_purges_partially_filled_orders` creates an order for `"AMD"` and calls `engine._execute_fill(o, 10, 100.0, 0.0, ...)`.
- `engine._execute_fill` calls `self.account.apply_fill(...)`, which adds `"AMD"` to `backend.app.main.account.positions`.
- `test_session_boundary_purges_partially_filled_orders` tests the boundary purge on `engine.working_orders`, but has no `finally:` cleanup for `account.positions`. As a result, `"AMD"` remains in the shared singleton `account.positions`.
- Later, when `test_ui_stream_resilience.py` executes, `test_high_frequency_broadcast_and_receipt` sets `account.positions["AAPL"] = ...` without clearing `account.positions` first.
- In Python 3.7+, `dict` preserves insertion order: `list(account.positions.keys()) == ['AMD', 'AAPL']`.
- In `backend/app/main.py:416`, `broadcast_ui_state` computes:
  ```python
  primary_pos = _serialize_position(next(iter(account.positions))) if account.positions else None
  ```
- `next(iter(account.positions))` evaluates to `"AMD"`, NOT `"AAPL"`.
- Because `"AMD"` has no bracket in `bracket_manager.symbol_to_bracket`, `_serialize_position("AMD")` sets `"stop_loss": None`.
- The test asserts `assert data["primary_position"]["stop_loss"] == new_stop`, which fails with `assert None == 148.01`.

Both root causes have been empirically reproduced and proven.

---

## 2. Bracket Lifecycle Architecture & Invariant Analysis

### 2.1 The Formal Bracket State Machine
In `backend/app/core/bracket.py`:

```
               +-------------------+
               |   PENDING_ENTRY   |  (Entry order submitted, no position yet,
               +-------------------+   no child orders submitted)
                         |
           activate_bracket_on_fill()
                         v
               +-------------------+
        +----->|      ACTIVE       |<-----+
        |      +-------------------+      |
        |                |                |
        |       Target 1 Fills (50%)      | manual_tighten_stop()
        |                v                | update_trailing_stop()
        |      +-------------------+      | (Monotonically ratchets stop)
        |      |   TARGET_1_HIT    |------+
        |      +-------------------+
        |                |
        +--------+-------+--------+
                 |                |
                 v                v
      +--------------------+   +-------------------+   +--------------------+
      |  COMPLETED_PROFIT  |   |  COMPLETED_STOP   |   | COMPLETED_FLATTEN  |
      +--------------------+   +-------------------+   +--------------------+
```

### 2.2 Institutional Invariant: Why `manual_tighten_stop` Must Require `ACTIVE` or `TARGET_1_HIT`
A key question investigated was whether `bracket.manual_tighten_stop` should simply be modified to accept `PENDING_ENTRY`.

**Our verdict is an emphatic NO.** Weakening `manual_tighten_stop` to allow `PENDING_ENTRY` would breach institutional risk controls for the following reasons:

1. **No Market Order to Modify**: While a bracket is `PENDING_ENTRY`, the parent order has not filled. Neither the stop-loss order nor take-profit orders exist in `engine.working_orders`. Attempting to modify a non-existent order or mutate the bracket's stop price ahead of fill decouples the order book from the bracket manager.
2. **Corrupted Risk Geometry on Fill**: When the parent order subsequently fills, `activate_bracket_on_fill(bracket_id, filled_qty, fill_price, timestamp)` recalculates:
   ```python
   bracket.r_distance = round(abs(fill_price - bracket.initial_stop_price), 4)
   bracket.target_1_price = round(bracket.entry_price + direction * 1.5 * bracket.r_distance, 2)
   bracket.target_2_price = round(bracket.entry_price + direction * 2.5 * bracket.r_distance, 2)
   ```
   If `current_stop_price` were mutated independently before entry fill, `r_distance` and profit targets would become misaligned with the intended risk-reward profile.
3. **Explicit Contract Unit Test**: In `backend/tests/unit/test_bracket.py:207-221`, the test `test_manual_tighten_stop_validation_guards` explicitly verifies:
   ```python
   # 1. While PENDING_ENTRY: tightening must be rejected
   d_pending = manager.manual_tighten_stop("NVDA", 119.00)
   assert d_pending.action == "NO_ACTION"
   assert brk.current_stop_price == 118.00
   ```
   Weakening this guard would violate this explicit unit test and the architectural specification in `PROJECT.md`.
4. **Terminal State Protection**: Similarly, once a bracket reaches `COMPLETED_PROFIT`, `COMPLETED_STOP`, `COMPLETED_FLATTEN`, or `CANCELLED`, child orders are already cancelled or filled. Permitting stop modification on dead brackets would risk resurrection or order leakage.

**Conclusion on Bracket Lifecycle**: The status guard in `backend/app/core/bracket.py:458`:
```python
if bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT):
    return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)
```
is functionally correct, institutional-grade, and must NOT be relaxed.

---

## 3. Detailed Fix Strategy

To achieve a 100% pass rate across the full E2E runner (`runner.py`), `scripts/run_e2e_tests.sh`, and isolated test files, four coordinated fixes are recommended:

### Fix 1: Properly Activate Bracket & Enforce Test Isolation in `tests/e2e/test_ui_stream_resilience.py`

In `tests/e2e/test_ui_stream_resilience.py`:
- Clear `account.positions` at test startup to eliminate any lingering positions from prior test executions.
- Call `bracket_manager.activate_bracket_on_fill(...)` immediately after creating the bracket, matching the pattern already used in `test_action_serialization_tighten_stop` (lines 177-185 of the same file).
- In the `finally:` block, pop `brk_hf_test` from `bracket_manager.brackets` to prevent bracket registry leakage.

#### Exact Proposed Diff for `tests/e2e/test_ui_stream_resilience.py`:
```python
<<<<
    # Setup test bracket & position for AAPL
    account.positions["AAPL"] = Position("AAPL", PositionSide.LONG, 100, 150.0, 152.0)
    bracket_manager.create_bracket("brk_hf_test", "AAPL", "LONG", 100, 150.0, 148.0)

    try:
====
    # Setup test bracket & position for AAPL with clean state isolation
    account.positions.clear()
    account.positions["AAPL"] = Position("AAPL", PositionSide.LONG, 100, 150.0, 152.0)
    bracket_manager.create_bracket("brk_hf_test", "AAPL", "LONG", 100, 150.0, 148.0)
    bracket_manager.activate_bracket_on_fill("brk_hf_test", 100, 150.0, datetime.now(timezone.utc))

    try:
>>>>
<<<<
    finally:
        bracket_manager.symbol_to_bracket.pop("AAPL", None)
        account.positions.pop("AAPL", None)
====
    finally:
        bracket_manager.brackets.pop("brk_hf_test", None)
        bracket_manager.symbol_to_bracket.pop("AAPL", None)
        account.positions.pop("AAPL", None)
>>>>
```

---

### Fix 2: Clean Up Account Positions in `tests/e2e/test_challenger_bracket_2.py`

In `tests/e2e/test_challenger_bracket_2.py::TestSessionBoundaryPurge::test_session_boundary_purges_partially_filled_orders`:
- Wrap the test body in a `try...finally` block that removes `"AMD"` from `engine.account.positions`.

#### Exact Proposed Diff for `tests/e2e/test_challenger_bracket_2.py`:
```python
<<<<
        # Create and partially fill order
        o = engine.create_order("AMD", OrderSide.BUY, OrderType.LIMIT, 20, limit_price=100.0)
        engine.submit_order(o.id)
        # Partially fill 10 shares via _execute_fill
        engine._execute_fill(o, 10, 100.0, 0.0, datetime(2026, 9, 21, 14, 5, 0, tzinfo=timezone.utc))
        assert o.status == OrderState.PARTIALLY_FILLED
        assert o.id in engine.working_orders

        # Day 2 boundary transition
        day2 = datetime(2026, 9, 22, 14, 0, 0, tzinfo=timezone.utc)
        _check_session_boundary(day2)

        assert len(engine.working_orders) == 0
        assert o.status == OrderState.CANCELLED
====
        try:
            # Create and partially fill order
            o = engine.create_order("AMD", OrderSide.BUY, OrderType.LIMIT, 20, limit_price=100.0)
            engine.submit_order(o.id)
            # Partially fill 10 shares via _execute_fill
            engine._execute_fill(o, 10, 100.0, 0.0, datetime(2026, 9, 21, 14, 5, 0, tzinfo=timezone.utc))
            assert o.status == OrderState.PARTIALLY_FILLED
            assert o.id in engine.working_orders

            # Day 2 boundary transition
            day2 = datetime(2026, 9, 22, 14, 0, 0, tzinfo=timezone.utc)
            _check_session_boundary(day2)

            assert len(engine.working_orders) == 0
            assert o.status == OrderState.CANCELLED
        finally:
            engine.account.positions.pop("AMD", None)
>>>>
```

---

### Fix 3: Defensive System Reset in `backend/app/main.py:_check_session_boundary`

At an ET session boundary, AutonomousDayTrader enforces zero overnight holds. `backend/app/main.py:_check_session_boundary` already clears:
- `engine.working_orders`
- `bracket_manager.brackets`
- `bracket_manager.symbol_to_bracket`
- `bracket_manager.order_to_bracket`
- `entry_order_to_bracket`
- `bracket_realized_pnl`
- `completed_brackets_recorded`

However, if an edge-case or test fill left a position in `account.positions`, resetting brackets without clearing `account.positions` leaves orphaned positions with `stop_loss = None`. Adding `account.positions.clear()` to `_check_session_boundary` guarantees that the account is strictly flat at every new session boundary.

#### Exact Proposed Diff for `backend/app/main.py`:
```python
<<<<
    # At a session boundary the book must be flat: clear bracket/linkage state
    # so no stale PENDING_ENTRY bracket blocks a symbol on the new day.
    bracket_manager.brackets.clear()
    bracket_manager.symbol_to_bracket.clear()
    bracket_manager.order_to_bracket.clear()
====
    # At a session boundary the book must be flat: clear bracket/linkage state
    # and open positions so no stale state blocks a symbol on the new day.
    account.positions.clear()
    bracket_manager.brackets.clear()
    bracket_manager.symbol_to_bracket.clear()
    bracket_manager.order_to_bracket.clear()
>>>>
```

---

### Fix 4 (Supplementary): Floating-Point Stop Distance Clamping Buffer & Epsilon

As discovered by Reviewer 1, IEEE 754 float representation causes `round(p * 0.004, 4)` to produce values like `0.003999999999999962` on prices such as AAPL ($150.00), triggering false rejections by `InstitutionalRiskEngine`.

To resolve this across the entire price space:
1. In `backend/app/strategies/orb.py` and `backend/app/strategies/news_momentum.py`:
   Use an internal buffer:
   ```python
   min_dist = round(entry_price * 0.0042, 4)
   max_dist = round(entry_price * 0.0380, 4)
   ```
2. In `backend/app/core/risk.py`:
   Add epsilon tolerance (`1e-6`) to boundary checks:
   ```python
   stop_dist_pct = stop_dist / entry_price
   if stop_dist_pct < self.config.min_stop_distance_pct - 1e-6:
       # STOP_DISTANCE_TOO_TIGHT
   if stop_dist_pct > self.config.max_stop_distance_pct + 1e-6:
       # STOP_DISTANCE_TOO_WIDE
   ```

---

### Fix 5 (Supplementary): Ingestion Telemetry Metric Validation

As discovered by Challenger 1, `StockWebSocketClient._process_queue_loop` in `backend/app/ingestion/stock_ws.py` increments counters before validating the incoming payload. If an item is malformed, `self.bars_received` is incremented despite the item failing validation.

#### Exact Proposed Diff for `backend/app/ingestion/stock_ws.py`:
```python
<<<<
                            t = m.get("T")
                            if t == "b":
                                self.bars_received += 1
                                await self.bus.publish(BarEvent.from_relay_dict(m))
                            elif t == "q":
                                self.quotes_received += 1
                                await self.bus.publish(QuoteEvent.from_relay_dict(m))
                            elif t == "t":
                                self.trades_received += 1
                                await self.bus.publish(TradeEvent.from_relay_dict(m))
====
                            t = m.get("T")
                            if t == "b":
                                bar_evt = BarEvent.from_relay_dict(m)
                                self.bars_received += 1
                                await self.bus.publish(bar_evt)
                            elif t == "q":
                                quote_evt = QuoteEvent.from_relay_dict(m)
                                self.quotes_received += 1
                                await self.bus.publish(quote_evt)
                            elif t == "t":
                                trade_evt = TradeEvent.from_relay_dict(m)
                                self.trades_received += 1
                                await self.bus.publish(trade_evt)
>>>>
```

---

## 4. Verification Evidence & Empirical Proof

We verified the proposed fix logic empirically using Python sub-processes without altering repository source code files:

### Empirical Test 1: Isolated Proposed Logic Verification
```python
# Setup AAPL position and activate bracket
account.positions.clear()
account.positions['AAPL'] = Position('AAPL', PositionSide.LONG, 100, 150.0, 152.0)
bracket_manager.create_bracket('brk_hf_test', 'AAPL', 'LONG', 100, 150.0, 148.0)
bracket_manager.activate_bracket_on_fill('brk_hf_test', 100, 150.0, datetime.now(timezone.utc))

# Connect WebSocket and send 100 rapid stop tightening actions
client = TestClient(app)
with client.websocket_connect('/ws/ui') as ws:
    ...
    for i in range(1, 101):
        new_stop = round(148.0 + (i * 0.01), 2)
        ws.send_text(json.dumps({'action': 'TIGHTEN_STOP', 'symbol': 'AAPL', 'new_stop': new_stop}))
        msg = json.loads(ws.receive_text())
        assert msg['primary_position']['stop_loss'] == new_stop
```
**Outcome**:
```
Initial connection OK, primary_pos: AAPL
100 messages processed in 0.0216s (throughput: 4640.2 msgs/s)
SUCCESS! ALL ASSERTIONS PASSED.
```

### Empirical Test 2: Sequence Testing with Prior Pollution
When preceded by the partially filled `"AMD"` order from `test_session_boundary_purges_partially_filled_orders`, the addition of `account.positions.clear()` and `activate_bracket_on_fill` completely prevented the `assert None == 148.01` failure.

---

## 5. Recommended Implementation Plan for Worker Agents

| Step | Target File | Action | Rationale |
|------|-------------|--------|-----------|
| 1 | `tests/e2e/test_ui_stream_resilience.py` | Add `account.positions.clear()` and `activate_bracket_on_fill("brk_hf_test", ...)` on line 37; add `bracket_manager.brackets.pop("brk_hf_test", None)` on line 67 | Activate bracket so stop tightening executes; ensure test isolation |
| 2 | `tests/e2e/test_challenger_bracket_2.py` | Add `try...finally: engine.account.positions.pop("AMD", None)` around line 440 | Prevent position leakage into subsequent tests |
| 3 | `backend/app/main.py` | Add `account.positions.clear()` in `_check_session_boundary` | Enforce flat book invariant across all session boundaries |
| 4 | `backend/app/strategies/orb.py` & `news_momentum.py` | Update clamping multipliers to `0.0042` and `0.0380` | Prevent floating-point rounding boundary rejections |
| 5 | `backend/app/core/risk.py` | Add `1e-6` epsilon to `min_stop_distance_pct` and `max_stop_distance_pct` checks | Ensure robust floating-point comparisons |
| 6 | `backend/app/ingestion/stock_ws.py` | Move telemetry counter increments after `from_relay_dict` construction | Prevent telemetry inflation on malformed feed frames |

---

## 6. Port Hygiene & Verification Commands

All tests and verification commands must observe strict process hygiene:
- `lsof -tiTCP:3005,8005,8080` must return empty before and after execution.
- Verification command for full suite:
  ```bash
  ./scripts/run_e2e_tests.sh
  ```
  *Expected result after fix*: 318 passed, 0 failed in ~23s, exit code 0.
- Verification command for unit suite:
  ```bash
  pytest backend/tests/ -v
  ```
  *Expected result*: 163 passed, 0 failed in ~0.8s, exit code 0.
