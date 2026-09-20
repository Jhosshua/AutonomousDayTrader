# Handoff Report: Milestone 2 Adaptation & Integration Independent Review

**Reviewer**: `reviewer_m2_2` (Adaptation & Integration Reviewer / Adversarial Critic)  
**Target Milestone**: Milestone 2 (`strategies_adaptation`)  
**Parent Orchestrator**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  
**Date**: 2026-09-20  
**Verdict**: **REQUEST_CHANGES**  
**Overall Risk Assessment**: **CRITICAL**

---

## 1. Observation

### 1.1 Test Suite Execution
Direct execution of unit, integration, empirical stress, and end-to-end test suites:
- **Backend Test Suite**:
  ```bash
  python3 -m pytest backend/tests/ -v
  ```
  *Result*: `131 passed, 3 warnings in 0.59s`.
- **E2E Opaque-Box Suite**:
  ```bash
  python3 tests/e2e/runner.py
  ```
  *Result*: `248 passed in 0.23s (Exit Code: 0)`.
- **Host Port Hygiene**:
  ```bash
  lsof -i :8005 -i :8080 -i :3005 || echo "CLEAN: All ports free"
  ```
  *Result*: `CLEAN: All ports free`.

### 1.2 Integrity & Mathematical Implementation
- `backend/app/strategies/adaptation.py`:
  - Implements real deterministic mathematical logic for VIX regime mapping (<15 Low, 15-25 Normal, 25-35 Elevated, >=35 Crisis) and position sizing based on risk budget / stop distance and maximum capital allocation caps.
  - Implements real Time-of-Day clock conversions to `America/New_York` timezone and maps to standard trading phases.
  - No evidence of hardcoded mocks, fake indicators, or test-specific facades in `adaptation.py`.

### 1.3 Critical Integration Discrepancies in `backend/app/main.py`
Direct inspection and runtime empirical verification revealed three blocking bugs in `backend/app/main.py`:

#### Observation 1: Zero Stop Distance Pre-Trade Risk Rejection on Strategy Market Orders
In `backend/app/main.py`:
- Lines 213-220 create market orders from strategy signals:
  ```python
  order = engine.create_order(
      symbol=sym,
      side=side,
      order_type=otype,
      qty=qty,
      limit_price=signal.entry_price if otype == OrderType.LIMIT else None,
      stop_price=signal.stop_loss,
      strategy_id=signal.strategy_id,
  )
  ```
  For market orders (`otype == OrderType.MARKET`), `order.limit_price` is `None`, and `order.stop_price` is set to `signal.stop_loss`.
- Lines 87-89 in `pre_trade_risk_validator`:
  ```python
  est_price = order.limit_price or order.stop_price or 100.0
  s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)
  ```
- Because `order.limit_price` is `None`, `est_price` evaluates to `order.stop_price`. Then `s_price` evaluates to `order.stop_price`.
- `stop_distance = abs(est_price - s_price) == 0.0`.
- In `backend/app/core/risk.py`:
  ```python
  stop_distance = abs(entry_price - stop_price)
  if stop_distance <= 0.001:
      return RiskCheckResult(approved=False, reason="INVALID_PRICE_GEOMETRY: Non-positive entry price or zero stop distance")
  ```
- **Runtime Execution Result**:
  `to_state: REJECTED, event_trigger: RISK_REJECTED, reason: INVALID_PRICE_GEOMETRY: Non-positive entry price or zero stop distance`.
  Every strategy market entry order dispatched via `execute_strategy_signal` is instantly rejected.

#### Observation 2: `create_bracket` Keyword Argument & Positional Mismatch
In `backend/app/main.py`:
- Lines 224-235:
  ```python
  bracket_manager.create_bracket(
      symbol=sym,
      entry_order_id=submitted.id,
      side=side.value,
      total_qty=qty,
      entry_price=signal.entry_price,
      stop_loss_price=signal.stop_loss,
      take_profit_1_price=signal.take_profit_1,
      take_profit_2_price=signal.take_profit_2,
      strategy_id=signal.strategy_id,
      timestamp=signal.timestamp,
  )
  ```
- In `backend/app/core/bracket.py`:
  ```python
  def create_bracket(
      self,
      bracket_id: str,
      symbol: str,
      side: str,
      total_qty: int,
      entry_price: float,
      stop_price: float,
      strategy_id: str = "MANUAL",
      use_trailing_target_2: bool = True,
      trail_atr_multiplier: float = 1.5,
      timestamp: Optional[datetime] = None,
  ) -> BracketOrder:
  ```
- **Runtime Execution Result**:
  `TypeError: create_bracket() got an unexpected keyword argument 'entry_order_id'` (and missing required positional argument `bracket_id`).

#### Observation 3: `active_brackets` AttributeError on UI State Broadcast
In `backend/app/main.py`:
- Line 130:
  ```python
  bracket = bracket_manager.active_brackets.get(first_symbol)
  ```
  and lines 140-142:
  ```python
  "stop_loss": bracket.stop_loss_price if bracket else round(pos.avg_entry_price * 0.98, 2),
  "take_profit_1": bracket.take_profit_1_price if bracket else round(pos.avg_entry_price * 1.015, 2),
  "take_profit_2": bracket.take_profit_2_price if bracket else round(pos.avg_entry_price * 1.025, 2),
  ```
- In `backend/app/core/bracket.py`:
  `DynamicBracketManager` does not define `active_brackets`; it maintains `self.brackets` (dict by `bracket_id`) and `self.symbol_to_bracket` (dict by `symbol`). Furthermore, `BracketOrder` field names are `current_stop_price`, `target_1_price`, and `target_2_price`.
- **Runtime Execution Result**:
  When an open position exists, executing `broadcast_ui_state()` crashes with:
  `AttributeError: 'DynamicBracketManager' object has no attribute 'active_brackets'`.

---

## 2. Logic Chain

1. **Root Cause of Execution Inoperability in `main.py`**:
   - The worker claimed in `worker_m2/handoff.md` that `main.py` had wired all 4 strategies and the adaptation engine into the event loop.
   - However, the unit tests for strategies in `backend/tests/unit/test_strategies.py` and `test_adaptation.py` tested each strategy class and `DynamicAdaptationEngine` in isolation by mocking bars and checking returned lists of `SignalEvent`.
   - None of the existing unit tests exercised `execute_strategy_signal` or `handle_bar_event` through `main.py`.
   - Because `pre_trade_risk_validator` conflates `order.limit_price` with `order.stop_price`, any market order that provides `stop_price` evaluates `entry_price == stop_price`, generating a `0.0` stop distance.
   - Because of this pre-trade rejection, the order status becomes `REJECTED`, and lines 224-235 (`create_bracket`) were never reached in previous tests, masking the `TypeError`.
   - Similarly, because positions were never filled in `main.py`, `account.positions` was empty during tests, masking the `AttributeError: 'DynamicBracketManager' object has no attribute 'active_brackets'` in `broadcast_ui_state()`.

2. **Adaptation Stop Width Invariance vs Sizing**:
   - `DynamicAdaptationEngine` outputs `stop_multiplier` (0.85 in Low, 1.40 in Elevated, 2.00 in Crisis).
   - In `evaluate_signal_admission`, the engine computes `calculate_adapted_size` which contracts the dollar risk budget by `vix_multiplier`.
   - However, neither `evaluate_signal_admission` nor `execute_strategy_signal` adjusts the stop distance by `stop_multiplier`. The raw stop loss from the strategy is preserved.
   - While position sizing scales down to maintain invariant dollar risk at the narrow stop distance, positions during elevated/crisis volatility remain vulnerable to premature stop-outs from standard market noise.

3. **Phase Permission Policy Consistency**:
   - In `adaptation.py` line 179:
     ```python
     if strat == "orb":
         if active_phase in (TimeOfDayPhase.MIDDAY_CHOP.value, TimeOfDayPhase.POWER_HOUR.value):
             return False
         return True
     ```
   - `AFTERNOON_PUSH` (14:00 - 15:00 ET) is omitted from the lockout list. Although `OpeningRangeBreakoutStrategy.on_bar` contains an internal check `t_time >= dtime(11, 30)`, the adaptation engine's permission gate returns `True` for ORB during `AFTERNOON_PUSH`.

---

## 3. Caveats

- Unit tests in `backend/tests/unit/test_strategies.py` and `backend/tests/unit/test_adaptation.py` pass 100% because they test strategy indicator math and engine classification in isolation.
- E2E tests in `tests/e2e/runner.py` pass 100% because they test against specification contracts in `tests/e2e/test_contracts.py` and direct method calls on domain models rather than invoking `main.py`'s internal FastAPI WebSocket broadcast with simulated open positions.
- The underlying algorithmic strategies (`orb.py`, `vwap_pullback.py`, `news_momentum.py`, `mean_reversion.py`) and `adaptation.py` are mathematically sound and robust; the defects are strictly in backend orchestration wiring in `backend/app/main.py`.

---

## 4. Conclusion & Findings

**Verdict**: **REQUEST_CHANGES**

### Findings Summary

| ID | Severity | File & Line | Summary |
|---|---|---|---|
| F-01 | **CRITICAL** | `backend/app/main.py:87-89, 218` | Zero stop-distance rejection in `pre_trade_risk_validator` blocks 100% of strategy market orders. |
| F-02 | **CRITICAL** | `backend/app/main.py:224-235` | Signature mismatch in `bracket_manager.create_bracket` raises `TypeError`. |
| F-03 | **CRITICAL** | `backend/app/main.py:130, 140-143` | `bracket_manager.active_brackets` does not exist; raises `AttributeError` when streaming active positions. |
| F-04 | **MAJOR** | `backend/app/strategies/adaptation.py:32-46`, `main.py:219` | `stop_multiplier` is not applied to expand stop distance on dispatched signals. |
| F-05 | **MAJOR** | `backend/app/strategies/adaptation.py:178-181` | Permissive gate allows ORB during `AFTERNOON_PUSH` (14:00-15:00 ET). |
| F-06 | **MINOR** | `backend/app/strategies/adaptation.py:211-217` | `arbitrate_signals` sorts by priority but does not deduplicate signals for the same symbol. |

---

## 5. Remediation Plan

To achieve approval:

1. **Fix `pre_trade_risk_validator` in `backend/app/main.py`**:
   Ensure `est_price` is determined using the order's limit price, or the strategy's target entry price, or recent market price from account/bar. Never fall back to `order.stop_price` as the entry price.
2. **Fix `create_bracket` Invocation in `backend/app/main.py`**:
   Pass required `bracket_id` (e.g. `f"brk_{submitted.id}"`), `symbol=sym`, `side=side.value`, `total_qty=qty`, `entry_price=signal.entry_price`, `stop_price=signal.stop_loss`, `strategy_id=signal.strategy_id`.
3. **Fix `broadcast_ui_state` in `backend/app/main.py`**:
   Look up bracket using `bracket_id = bracket_manager.symbol_to_bracket.get(first_symbol)` and `bracket = bracket_manager.brackets.get(bracket_id) if bracket_id else None`. Access valid attributes `bracket.current_stop_price`, `bracket.target_1_price`, `bracket.target_2_price`.
4. **Enforce `AFTERNOON_PUSH` Lockout in `adaptation.py`**:
   Add `TimeOfDayPhase.AFTERNOON_PUSH.value` to ORB lockout conditions.
5. **Add Integration Tests Covering `main.py` Event Dispatch**:
   Add unit/integration tests that feed bars and news through `handle_bar_event`, `handle_news_event`, and `broadcast_ui_state()` with active positions to certify that signals execute and broadcast without error.

---

## 6. Verification Method

To reproduce the findings and independently verify the fixes:

1. **Reproduce Finding 1 & 2 (Order Execution & Bracket Creation)**:
   ```bash
   python3 -c "
   import asyncio
   from backend.app.main import execute_strategy_signal, account, engine
   from backend.app.strategies.base import SignalEvent
   from backend.app.models.events import OrderSide, OrderType, BarEvent
   from datetime import datetime, timezone

   async def test():
       bar = BarEvent('AAPL', 150.0, 151.0, 149.0, 150.0, 100000, datetime.now(timezone.utc))
       sig = SignalEvent('AAPL', OrderSide.BUY, OrderType.MARKET, 150.0, 148.0, 153.0, 155.0, 'news_momentum', 0.9, 'News')
       await execute_strategy_signal(sig, bar)
       assert len(account.positions) == 1, f'Position failed to open! Audit: {[l.reason for l in engine.audit_log]}'

   asyncio.run(test())
   "
   ```
   *Current Result*: Fails with `Position failed to open! Audit: ['Order initialized', 'Dispatched to engine', 'INVALID_PRICE_GEOMETRY: Non-positive entry price or zero stop distance']`.

2. **Reproduce Finding 3 (UI Broadcast AttributeError)**:
   ```bash
   python3 -c "
   import asyncio
   from backend.app.main import broadcast_ui_state, account, ui_clients
   from backend.app.core.account import Position, PositionSide
   from datetime import datetime, timezone

   account.positions['AAPL'] = Position('AAPL', PositionSide.LONG, 100, 150.0, 151.0, opened_at=datetime.now(timezone.utc))
   class DummyWS:
       async def send_text(self, txt): pass
   ui_clients.add(DummyWS())
   asyncio.run(broadcast_ui_state())
   "
   ```
   *Current Result*: Raises `AttributeError: 'DynamicBracketManager' object has no attribute 'active_brackets'`.

3. **Verify Standard Test Suites**:
   ```bash
   python3 -m pytest backend/tests/ -v
   python3 tests/e2e/runner.py
   lsof -i :8005 -i :8080 -i :3005 || echo "CLEAN: All ports free"
   ```
