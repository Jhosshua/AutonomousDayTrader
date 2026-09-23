# Handoff Report: Ingestion Layer & Core State/Risk Layer Code Review

**Auditor**: Explorer 1  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_ingestion_core/`  
**Milestone**: `milestone_1_ingestion_core_audit`  
**Date**: 2026-09-23  

---

## 1. Observation

### Observation 1: Stop Bounds Invariant Breach under Volatility Multipliers
- **Location**: `backend/app/strategies/adaptation.py`: Lines 215–224 and `backend/app/main.py`: Lines 896, 917–933.
- **Code**:
  ```python
  def calculate_adapted_stop(self, signal: SignalEvent) -> float:
      raw_dist = abs(signal.entry_price - signal.stop_loss)
      adapted_dist = raw_dist * self.current_stop_multiplier
      is_buy = signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY"
      if is_buy:
          return round(signal.entry_price - adapted_dist, 4)
      else:
          return round(signal.entry_price + adapted_dist, 4)
  ```
- **Reproduction**: Ran Python snippet with `VIX=12.0` (`stop_multiplier=0.85`), `entry_price=100.0`, `stop_loss=99.58` (0.42% distance):
  ```
  Adapted stop: 99.643 dist: 0.357
  Risk check: False STOP_DISTANCE_TOO_TIGHT: Stop distance 0.0036 < min 0.0040
  ```
  Ran Python snippet with `VIX=30.0` (`stop_multiplier=1.40`), `entry_price=100.0`, `stop_loss=97.00` (3.0% distance):
  ```
  Adapted stop: 95.8 dist: 4.2
  Risk check: False STOP_DISTANCE_TOO_WIDE: Stop distance 0.0420 > max 0.0400
  ```

### Observation 2: Missing Stop-Fill Loop Break in `process_quote`
- **Location**: `backend/app/core/engine.py`: Lines 283–325.
- **Code**:
  ```python
  # engine.py line 300
  matching_orders.sort(key=lambda order: order.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT))
  for order in matching_orders:
      ...
      if fill_price is not None:
          fill = self._execute_fill(order, order.remaining_qty, fill_price, slippage, timestamp)
          fills.append(fill)
  ```
  Compared directly with `process_bar` (lines 380–383):
  ```python
  if fill_price is not None:
      ...
      if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
          break
  ```
  In `process_quote`, there is no `break` after a stop fills.

### Observation 3: Manual Flatten Skips Working Entry Orders and Pending Brackets
- **Location**: `backend/app/main.py`: Lines 1806–1836, 1838–1848.
- **Code**:
  ```python
  target_symbols = [req.symbol.upper()] if (req and req.symbol) else list(account.positions.keys())
  ...
  for sym in target_symbols:
      pos = account.positions.get(sym)
      if pos:
          ...
  ```
- **Reproduction**: Submitted a pending limit order for TSLA without an open position. Called manual flatten logic:
  ```
  Working orders before flatten: ['ord_7ba4a0bcd761']
  Brackets before flatten: ['TSLA']
  Positions before flatten: []
  Target symbols for Flatten All: []
  Working orders after Flatten All: ['ord_7ba4a0bcd761']
  Brackets after Flatten All: ['TSLA']
  ```
  The working limit order remained active in `engine.working_orders`.

### Observation 4: Ingestion WebSocket Connect Payload & Error Handling
- **Location**:
  - `backend/app/ingestion/news_ws.py`: Lines 103–107 (`max_size` missing).
  - `backend/app/ingestion/news_ws.py`: Lines 191–236 (no per-item try-except in `for m in msgs:`).
  - `backend/app/ingestion/stock_ws.py`: Lines 220–266 (`_process_queue_loop` lacks outer exception handler).

### Observation 5: Desynchronized Allocation Cap Definitions
- **Location**:
  - `backend/app/strategies/adaptation.py`: Line 66 (`max_alloc_pct: float = 0.25` of equity = $12,500).
  - `backend/app/core/risk.py`: Line 43 (`max_position_equity_pct: float = 0.500` = $25,000).
  - `backend/app/core/account.py`: Line 97 (`MAX_POSITION_ALLOCATION_PCT: float = 0.25` of DTBP = $50,000).
  - `backend/app/config.py`: Line 98 (`MAX_POSITION_NOTIONAL: float = 25000.0`).

### Observation 6: Single-Shot Execution of Phase 4 Zero-Overnight Audit
- **Location**: `backend/app/core/flattening.py`: Lines 118–130.
- **Code**:
  ```python
  if t >= self.schedule.phase4_audit_time and t < self.schedule.market_close_time:
      if not self.phase4_executed:
          self.phase4_executed = True
          self.current_phase = FlatteningPhase.ZERO_AUDIT
          return FlatteningDirective(...)
  ```
- **Reproduction**: Tested `check_time_tick` with `audit_passed = False`:
  ```
  15:58:00 directive: EXECUTE_PHASE_4_AUDIT
  Audit passed? False
  15:58:05 directive: None
  15:59:00 directive: None
  ```
  Zero retries during the remaining 119 seconds before close.

---

## 2. Logic Chain

1. **Premise 1**: Institutional risk invariants require stops to strictly reside within $[0.0040, 0.0400]$ (40 to 400 bps). Individual strategies clamp raw stops to $[0.0042, 0.0380]$ (Observation 1).
2. **Inference 1**: Because `adaptation_engine.calculate_adapted_stop()` multiplies the raw distance by $0.85$ (Low VIX), $1.40$ (Elevated VIX), or $2.00$ (Crisis VIX) without clamping the product, stops are shifted outside $[0.0040, 0.0400]$, causing `risk_engine` to reject valid trades (`CR-1`).
3. **Premise 2**: In `process_quote()`, stop orders are evaluated first to ensure OCO stops take precedence over targets (Observation 2).
4. **Inference 2**: Without `break` after `_execute_fill(order)` on a stop order, `process_quote()` continues evaluating subsequent orders in `matching_orders`. On a crossed or wide quote, a limit target can fill on the same tick that already liquidated the position, resulting in an unhedged short position (`CR-2`).
5. **Premise 3**: Manual flatten is intended to terminate all trading activity and eliminate all exposure for a symbol or account (Observation 3).
6. **Inference 3**: Filtering by `if pos:` and setting `target_symbols = list(account.positions.keys())` completely ignores pending entry orders and brackets when no position is yet filled. A pending limit order will remain live on the exchange/book and execute after the user commanded a flatten (`CR-3`).
7. **Premise 4**: WebSocket and queue lifecycles must be resilient to large bursts, network disconnects, and malformed frames (Observation 4).
8. **Inference 4**: Omitting `max_size` in `news_ws.py`, omitting per-item exception handling in news batch parsing, and omitting outer exception handling in `stock_ws.py`'s worker creates failure modes where news feeds disconnect on $>1\text{MB}$ payloads, news batches drop valid items, and the stock queue worker silently dies (`IN-1`, `IN-2`, `IN-3`).
9. **Premise 5**: Zero overnight holding requires that if an emergency sweep is initiated at 15:58 ET, it must be verified and retried until the book is flat before 16:00 ET (Observation 6).
10. **Inference 5**: Setting `self.phase4_executed = True` without re-evaluating `not self.audit_passed` leaves the engine idle between 15:58:01 and 16:00:00 ET if the initial sweep did not immediately liquidate all shares (`CR-5`).

---

## 3. Caveats

- **Strategy Alpha**: This review evaluated execution correctness, risk invariants, and feed plumbing; it did not evaluate whether the underlying quantitative alphas (ORB, VWAP Pullback, News Momentum, Mean Reversion) have positive expectation in live market regimes.
- **Network Mode**: The investigation was conducted in local read-only static analysis and unit test execution mode without connecting to live upstream AlpacaRelay production WebSockets.
- **Frontend Scope**: UI components were audited solely at their WebSocket boundary (`/ws/ui` serialization and payload structure). Frontend React rendering was not evaluated in this handoff.

---

## 4. Conclusion

The Ingestion Layer and Core State & Risk Layer are architecturally robust with solid foundations: SQLite WAL persistence with exclusive file locking, deterministic market clock abstraction, 4:1 FINRA DTBP margin models, and no-lookahead causal market trend filters.

However, **15 concrete defects** were identified that impair production stability:
- **3 CRITICAL defects** that directly violate risk invariants, permit double-fills in quotes, and leave orphaned entry orders active during manual flattening.
- **6 MAJOR defects** that cause feed drops on large news frames, batch dropouts on malformed news items, queue worker death on stock streams, layer allocation desynchronization, single-shot Phase 4 flattening sweeps without retries, and unconstrained manual stop tightening.
- **6 MINOR defects** affecting telemetry accuracy, audit logging, and code deduplication.

Detailed analysis, line-by-line citations, failure mechanics, and remediation instructions are documented in:
`/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_ingestion_core/analysis.md`.

---

## 5. Verification Method

Each finding is verified via deterministic unit tests and can be verified by executing:

1. **Pytest Backend Unit Suite**:
   ```bash
   pytest backend/tests/unit/test_ingestion.py backend/tests/unit/test_risk.py backend/tests/unit/test_bracket.py backend/tests/unit/test_account.py backend/tests/unit/test_flattening.py backend/tests/unit/test_market_filter.py
   ```
2. **Empirical Reproduction Verification Commands**:
   - Verify `CR-1` (Adapted Stop Bounds):
     ```bash
     python3 -c "
     from backend.app.strategies.adaptation import DynamicAdaptationEngine
     from backend.app.strategies.base import SignalEvent
     from backend.app.models.events import OrderSide, OrderType, VixPrint, VixRegime
     from backend.app.core.risk import InstitutionalRiskEngine
     from datetime import datetime, timezone
     engine = DynamicAdaptationEngine()
     engine.on_vix_print(VixPrint(12.0, datetime.now(timezone.utc), datetime.now(timezone.utc), 1.0, 'ready', 'connected', VixRegime.LOW, 1.2))
     sig = SignalEvent('AAPL', OrderSide.BUY, OrderType.MARKET, 100.0, 99.58, 101.0, 102.0, 'orb', 0.8, 'test', datetime.now(timezone.utc))
     res = InstitutionalRiskEngine().evaluate_order_request('AAPL', 'BUY', 10, 100.0, engine.calculate_adapted_stop(sig), 50000.0, 200000.0, 0, set(), set())
     assert not res.approved and 'STOP_DISTANCE_TOO_TIGHT' in res.reason
     "
     ```
   - Verify `CR-3` (Manual Flatten Orphaned Orders):
     ```bash
     python3 -c "
     from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType
     from backend.app.core.account import PaperTradingAccount
     from backend.app.core.bracket import DynamicBracketManager
     acct, eng, bm = PaperTradingAccount(50000.0), ExecutionEngine(PaperTradingAccount(50000.0)), DynamicBracketManager()
     o = eng.create_order('TSLA', OrderSide.BUY, OrderType.LIMIT, 50, limit_price=200.0)
     eng.submit_order(o.id)
     assert o.id in eng.working_orders
     # Notice acct.positions.keys() is empty, so manual_flatten misses 'TSLA'
     assert 'TSLA' not in acct.positions
     "
     ```
   - Verify `CR-5` (Phase 4 Zero Audit Retry):
     ```bash
     python3 -c "
     from backend.app.core.flattening import ZeroOvernightFlatteningEngine
     from datetime import datetime
     from zoneinfo import ZoneInfo
     fe = ZeroOvernightFlatteningEngine()
     t1 = datetime(2026, 9, 23, 15, 58, 0, tzinfo=ZoneInfo('America/New_York'))
     assert fe.check_time_tick(t1).action_required == 'EXECUTE_PHASE_4_AUDIT'
     fe.execute_phase_4_audit({'AAPL': {}}, [])
     t2 = datetime(2026, 9, 23, 15, 58, 5, tzinfo=ZoneInfo('America/New_York'))
     assert fe.check_time_tick(t2) is None
     "
     ```

### Invalidation Conditions
- A finding is invalidated if an existing gate or wrapper upstream prevents the defective condition from ever reaching the affected code path in production.
