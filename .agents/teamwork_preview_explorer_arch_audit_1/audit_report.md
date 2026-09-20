# Backend Architectural Audit Report

**System**: AutonomousDayTrader  
**Role**: Backend Architectural Auditor  
**Date**: 2026-09-20  
**Status**: Comprehensive Read-Only Audit Complete  

---

## Executive Summary

AutonomousDayTrader is a high-speed, local intraday day trading system connected downstream to AlpacaRelay. It implements a $50,000 virtual paper portfolio, FINRA Rule 4210 Day Trading Buying Power (4:1 leverage), institutional risk guardrails ($1,500 circuit breaker), four dynamically adapted trading strategies (Opening Range Breakout, VWAP Trend Pullback & Continuation, Catalyst News Momentum Breakout, and Statistical Mean Reversion), an automated 4-phase zero-overnight auto-flattening engine, and real-time WebSocket state streaming.

The codebase exhibits strong architectural foundations, robust test suites (140 backend tests and 293 E2E tests passing 100%), and clean separation of concerns across ingestion, risk, execution, portfolio accounting, strategies, and streaming. However, this rigorous audit identified **10 architectural findings**—including **1 Critical**, **5 Major**, and **4 Minor** defects and discrepancies—that require remediation to guarantee institutional robustness in production and live-market replay.

---

## Detailed Audit Findings & Remediation Proposals

### 1. [CRITICAL] Orphaned Position Exposure on Target 2 Partial Fill
- **Subsystem**: Order Lifecycle & Bracket Management
- **File**: `backend/app/core/bracket.py` (lines 342–354)
- **Component**: `DynamicBracketManager.on_child_order_fill`

#### Direct Observation
```python
342:         # 3. Target 2 Filled
343:         elif child_type == BracketChildType.TAKE_PROFIT_2:
344:             bracket.target_2_filled = True
345:             bracket.remaining_qty -= filled_qty
346:             bracket.status = BracketStatus.COMPLETED_PROFIT
347:             self.symbol_to_bracket.pop(bracket.symbol, None)
348: 
349:             return BracketUpdateDirective(
350:                 action="CANCEL_ORDER",
351:                 orders_to_cancel=[bracket.stop_order_id] if bracket.stop_order_id else [],
352:                 bracket_status=BracketStatus.COMPLETED_PROFIT,
353:             )
```

#### Root Cause Analysis
When a fill event arrives for `TAKE_PROFIT_2`, the method unconditionally transitions `bracket.status` to `COMPLETED_PROFIT`, removes the symbol from `self.symbol_to_bracket`, and issues a `CANCEL_ORDER` directive for `bracket.stop_order_id`. If `filled_qty < bracket.remaining_qty` (a partial fill due to market liquidity or volume participation constraints), `bracket.remaining_qty` is still strictly positive. By immediately cancelling the stop order and dropping the bracket tracking, the remaining position shares are left **completely unhedged and unprotected** with no working stop loss in the order book.

#### Concrete Fix Proposal
Guard completion on `bracket.remaining_qty <= 0`. On a partial fill, resize the working stop order to protect the remaining quantity:
```python
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
            else:
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

---

### 2. [MAJOR] Bracket Manager Memory Leak & Stale Order Collision via `order_to_bracket`
- **Subsystem**: Bracket Management
- **File**: `backend/app/core/bracket.py` (lines 298, 312, 346, 467)
- **Component**: `DynamicBracketManager`

#### Direct Observation
Upon bracket completion (Stop-Loss filled at line 298, Target 1 terminal fill at line 312, Target 2 fill at line 346) and upon EOD flattening (`cancel_bracket_for_flattening` at line 467), `self.symbol_to_bracket.pop(...)` is called. However, `self.order_to_bracket` entries for `stop_order_id`, `target_1_order_id`, and `target_2_order_id` are **never removed**.

#### Root Cause Analysis
`self.order_to_bracket` monotonically accumulates order IDs throughout the runtime lifetime. If a late or duplicate fill event arrives for an order ID belonging to a completed or flattened bracket, `on_child_order_fill` resolves the bracket ID and attempts to process the fill on a completed bracket because `on_child_order_fill` does not check whether `bracket.status` is already completed/cancelled.

#### Concrete Fix Proposal
1. Add an early guard in `on_child_order_fill`:
```python
if bracket.status in (BracketStatus.COMPLETED_PROFIT, BracketStatus.COMPLETED_STOP, BracketStatus.COMPLETED_FLATTEN, BracketStatus.CANCELLED):
    return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)
```
2. When transitioning to any completed status, prune all child order IDs from `self.order_to_bracket`.
3. In `cancel_bracket_for_flattening`, pop all child order IDs from `self.order_to_bracket`.

---

### 3. [MAJOR] `manual_tighten_stop` Emits Unnecessary/Invalid `MODIFY_ORDER` on Loosened Stops or Unfilled Entries
- **Subsystem**: Execution & Bracket Lifecycle
- **File**: `backend/app/core/bracket.py` (lines 414–441)
- **Component**: `DynamicBracketManager.manual_tighten_stop`

#### Direct Observation
```python
425:         bracket = self.brackets[bracket_id]
426:         if bracket.side == "LONG":
427:             if new_stop_price > bracket.current_stop_price:
428:                 bracket.current_stop_price = new_stop_price
429:         else:
430:             if new_stop_price < bracket.current_stop_price:
431:                 bracket.current_stop_price = new_stop_price
432: 
433:         return BracketUpdateDirective(
434:             action="MODIFY_ORDER",
435:             orders_to_modify=[{
436:                 "order_id": bracket.stop_order_id,
437:                 "new_stop_price": bracket.current_stop_price,
438:                 "new_qty": bracket.remaining_qty,
439:             }],
440:             bracket_status=bracket.status,
441:         )
```

#### Root Cause Analysis
1. If the bracket is in `PENDING_ENTRY` (entry order submitted but not yet filled), the child stop order has not been submitted to the execution engine. Calling `manual_tighten_stop` generates a directive to modify a non-existent working order in `engine.working_orders`.
2. If `new_stop_price` does not tighten the stop (e.g. for LONG, `new_stop_price <= bracket.current_stop_price`), lines 427/430 do not update `bracket.current_stop_price`, but line 433 still returns `action="MODIFY_ORDER"`, triggering redundant order modification routines in the backend and UI.

#### Concrete Fix Proposal
Ensure `bracket.status in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT)` and verify that the stop was actually tightened before emitting `MODIFY_ORDER`:
```python
    def manual_tighten_stop(self, symbol: str, new_stop_price: float) -> BracketUpdateDirective:
        symbol_upper = symbol.upper()
        bracket_id = self.symbol_to_bracket.get(symbol_upper)
        if not bracket_id:
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=BracketStatus.COMPLETED_FLATTEN)

        bracket = self.brackets[bracket_id]
        if bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT):
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)

        tightened = False
        if bracket.side == "LONG" and new_stop_price > bracket.current_stop_price:
            bracket.current_stop_price = new_stop_price
            tightened = True
        elif bracket.side == "SHORT" and new_stop_price < bracket.current_stop_price:
            bracket.current_stop_price = new_stop_price
            tightened = True

        if not tightened:
            return BracketUpdateDirective(action="NO_ACTION", bracket_status=bracket.status)

        return BracketUpdateDirective(
            action="MODIFY_ORDER",
            orders_to_modify=[{
                "order_id": bracket.stop_order_id,
                "new_stop_price": bracket.current_stop_price,
                "new_qty": bracket.remaining_qty,
            }],
            bracket_status=bracket.status,
        )
```

---

### 4. [MAJOR] Stop Distance Floor Violation in ORB and News Momentum Strategies
- **Subsystem**: Trading Strategies & Risk Engine Integration
- **Files**:
  - `backend/app/strategies/orb.py` (lines 172–178)
  - `backend/app/strategies/news_momentum.py` (lines 238–241, 262–265)
- **Component**: `OpeningRangeBreakoutStrategy.on_bar`, `NewsMomentumStrategy.on_bar`

#### Direct Observation
`InstitutionalRiskEngine` (`backend/app/core/risk.py` lines 217–238) enforces a strict stop distance window:
`0.004 <= stop_dist / entry_price <= 0.040` (0.4% to 4.0%).
While `vwap_pullback.py` defines `MIN_STOP_DISTANCE_PCT = 0.004` and dynamically clamps `stop_loss` to satisfy this threshold, `orb.py` and `news_momentum.py` do not:
- In `orb.py`:
  ```python
  risk = abs(entry_price - stop_loss)
  if risk < 0.05:
      atr = calculate_atr(state.all_bars, period=14)
      risk = max(0.10, atr)
      stop_loss = round(entry_price - risk if sig_type == "BUY" else entry_price + risk, 4)
  ```
  For a $150–$300 stock (AAPL, TSLA, NVDA), a 0.4% minimum stop is $0.60–$1.20. If `risk` is $0.20, `risk < 0.05` is false, and the emitted stop distance is 0.13% (< 0.40%), causing immediate rejection by `InstitutionalRiskEngine` (`STOP_DISTANCE_TOO_TIGHT`).
- In `news_momentum.py`:
  ```python
  stop_loss = round(bar.low - 0.02, 4)
  risk = max(0.10, entry_price - stop_loss)
  ```
  If `bar.close` is close to `bar.low`, `entry_price - stop_loss` can be $0.05 (0.03%), which is rejected by `InstitutionalRiskEngine`.

#### Root Cause Analysis
Discrepancy in contract assumptions: `orb.py` and `news_momentum.py` were written with fixed-cent stop floors ($0.05 and $0.10) instead of the institutional 0.4% percentage floor mandated by `InstitutionalRiskEngine` and `PROJECT.md §F5`.

#### Concrete Fix Proposal
Add explicit percentage floor and ceiling clamping to both `orb.py` and `news_momentum.py`:
```python
min_dist = round(entry_price * 0.004, 4)
max_dist = round(entry_price * 0.040, 4)
raw_dist = abs(entry_price - stop_loss)
clamped_dist = max(min_dist, min(max_dist, raw_dist))
stop_loss = round(entry_price - clamped_dist if is_buy else entry_price + clamped_dist, 4)
```

---

### 5. [MAJOR] Ingestion Queue Processing Asymmetry and Potential Stall in `stock_ws.py`
- **Subsystem**: Ingestion Adapters
- **File**: `backend/app/ingestion/stock_ws.py` (lines 220–254)
- **Component**: `StockWebSocketClient._process_queue_loop`

#### Direct Observation
In `stock_ws.py`, line 249:
```python
222:         while self._running:
223:             try:
224:                 raw_msg = await self._queue.get()
225:                 msgs = json.loads(raw_msg)
...
249:                 self._queue.task_done()
250:             except asyncio.CancelledError:
251:                 break
252:             except Exception as exc:
253:                 log.exception(f"Error processing market message: {exc}")
```
In `news_ws.py`, lines 169–175:
```python
                try:
                    await self._handle_news_message(raw_msg)
                except Exception as exc:
                    log.exception(f"Error processing news message: {exc}")
                finally:
                    self._queue.task_done()
```

#### Root Cause Analysis
In `stock_ws.py`, `self._queue.task_done()` is inside the `try` block. If `json.loads(raw_msg)` fails on a corrupted frame or an unhandled exception occurs in `BarEvent.from_relay_dict`, `task_done()` is bypassed. If any supervisor or test calls `await self._queue.join()`, it will dead-lock. Furthermore, if a multi-message batch `msgs` contains one malformed dictionary, the entire loop aborts and discards all subsequent valid bars/quotes in that batch.

#### Concrete Fix Proposal
Refactor `stock_ws.py._process_queue_loop` to match `news_ws.py`'s pattern with a `finally: self._queue.task_done()` clause and per-item exception handling:
```python
    async def _process_queue_loop(self) -> None:
        while self._running:
            try:
                raw_msg = await self._queue.get()
                try:
                    msgs = json.loads(raw_msg)
                    if not isinstance(msgs, list):
                        msgs = [msgs]
                    for m in msgs:
                        try:
                            # Parse and publish event
                            ...
                        except Exception as item_err:
                            log.exception(f"Error processing individual market item: {item_err}")
                except Exception as exc:
                    log.exception(f"Error processing market frame: {exc}")
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
```

---

### 6. [MAJOR] Session Date Boundary Does Not Purge Lingering Working Orders
- **Subsystem**: Session Lifecycle & Daily Reset
- **File**: `backend/app/main.py` (lines 378–404)
- **Component**: `_check_session_boundary`

#### Direct Observation
In `_check_session_boundary`:
```python
391:     risk_engine.reset_daily_metrics(account.equity)
392:     flattening_engine.reset_for_new_session()
393:     account.reset_daily_metrics(account.equity)
394:     # At a session boundary the book must be flat: clear bracket/linkage state
395:     # so no stale PENDING_ENTRY bracket blocks a symbol on the new day.
396:     bracket_manager.brackets.clear()
397:     bracket_manager.symbol_to_bracket.clear()
398:     bracket_manager.order_to_bracket.clear()
399:     entry_order_to_bracket.clear()
400:     bracket_realized_pnl.clear()
401:     completed_brackets_recorded.clear()
402:     for strategy in strategies:
403:         strategy.reset_daily_stats()
```

#### Root Cause Analysis
`_check_session_boundary` clears all bracket managers, symbol mappings, and strategy metrics. However, it does not inspect or purge `engine.working_orders`. While the EOD auto-flattening engine normally sweeps orders at 15:50 and 15:58, any order accepted during off-hours, testing, or an incomplete simulation remains alive in `engine.working_orders`. On the next trading day, when quotes or bars arrive, those zombie orders can match and execute without any corresponding bracket or strategy mapping in memory.

#### Concrete Fix Proposal
Add explicit working order cancellation and book cleanup in `_check_session_boundary`:
```python
    if engine.working_orders:
        log.warning("Session boundary detected with %d open working orders; cancelling all", len(engine.working_orders))
        engine.cancel_all_orders("SESSION_BOUNDARY_PURGE")
        engine.working_orders.clear()
```

---

### 7. [MINOR] Default `RiskEngineConfig.max_position_equity_pct` Discrepancy
- **Subsystem**: Risk Engine Configuration
- **File**: `backend/app/core/risk.py` (line 43) vs `backend/app/config.py` (line 78) vs `backend/app/main.py` (line 49)

#### Direct Observation
- `backend/app/core/risk.py:43`:
  `max_position_equity_pct: float = 0.500   # $25,000 = 25% of $200,000 day-trading buying power`
- `backend/app/main.py:49`:
  `max_position_equity_pct=settings.MAX_POSITION_NOTIONAL / settings.INITIAL_CASH` (evaluates to 1.0)
- `MEMORY.md` Decision:
  "`max_position_equity_pct` is now 1.0 ($50k notional cap from config), replacing risk.py's hardcoded 0.5 default."

#### Root Cause Analysis
When instantiated without parameters in unit tests or standalone scripts, `RiskEngineConfig()` still initializes `max_position_equity_pct = 0.500` ($25,000 cap), while production runs with `1.0` ($50,000 cap).

#### Concrete Fix Proposal
Update `RiskEngineConfig` in `risk.py`:
```python
class RiskEngineConfig(BaseModel):
    starting_equity: float = 50000.00
    hard_max_daily_loss_dollars: float = 1500.00
    hard_max_daily_loss_pct: float = 0.030
    warning_loss_dollars: float = 1000.00
    warning_loss_pct: float = 0.020
    base_trade_risk_pct: float = 0.010
    max_trade_risk_pct: float = 0.020
    max_trade_risk_dollars: float = 1000.00
    max_position_equity_pct: float = 1.000   # $50,000 max single position (100% of equity / 25% of DTBP)
```

---

### 8. [MINOR] Residual Music-Inspired Terminology in Docstrings & Comments
- **Subsystem**: Terminology & Code Hygiene
- **Files**:
  - `backend/app/main.py` (lines 407, 1121)
  - `backend/app/config.py` (line 83)

#### Direct Observation
- `main.py:407`: `"""Broadcast current system state to connected Apple Music UI clients."""`
- `main.py:1121`: `"""Real-time bi-directional streaming for the Apple Music mobile UI."""`
- `config.py:83`: `UI_PORT: int = Field(default=3005, description="Apple Music mobile UI frontend port")`

#### Root Cause Analysis
In accordance with Requirement R2 ("De-themification of Music & Playlist Terminology"), music, playlist, and album metaphors must be purged across the codebase. While components and labels were converted ("Trading Strategies", "Active Position"), these residual comments were missed.

#### Concrete Fix Proposal
Replace "Apple Music mobile UI" with "trading mobile web UI" or "mobile trading dashboard" in `main.py` and `config.py`.

---

### 9. [MINOR] Pre-Market Phase Incoherence in `ZeroOvernightFlatteningEngine`
- **Subsystem**: Flattening & Market Clock
- **File**: `backend/app/core/flattening.py` (lines 112–162)
- **Component**: `ZeroOvernightFlatteningEngine.check_time_tick`

#### Direct Observation
When `t < time(9, 30)` (pre-market), `check_time_tick` evaluates false for all phase conditions (15:45+) and returns `None`. Consequently, `flattening_engine.current_phase` remains `FlatteningPhase.NORMAL_TRADING`.
In `pre_trade_risk_validator`:
`is_lockout = flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING`
Thus, `is_lockout` evaluates to `False` during pre-market.

#### Root Cause Analysis
`ZeroOvernightFlatteningEngine` was architected specifically for EOD closeout (15:45–16:00+) rather than full day market-state tracking. Strategy entries are correctly blocked by `adaptation_engine.is_strategy_permitted` (which recognizes `TimeOfDayPhase.PRE_MARKET`), but the flattening engine's phase enum misleadingly reports `NORMAL_TRADING` before market open.

#### Concrete Fix Proposal
Add explicit `FlatteningPhase.PRE_MARKET` or update `check_time_tick` so that `current_phase` reflects `PRE_MARKET` when `t < time(9, 30)`, ensuring unified lockout evaluation across both risk and adaptation layers.

---

### 10. [MINOR] `RiskCheckResult.estimated_risk_dollars` Calculated on Capacity Rather than Order Quantity
- **Subsystem**: Risk Engine Pre-Trade Evaluation
- **File**: `backend/app/core/risk.py` (lines 269–277)
- **Component**: `InstitutionalRiskEngine.evaluate_order_request`

#### Direct Observation
```python
257:         authorized_qty = min(q_risk, q_alloc, q_bp)
...
269:         estimated_risk = round(authorized_qty * stop_dist, 2)
270:         return RiskCheckResult(
271:             approved=True,
272:             reason="Approved",
273:             requested_qty=requested_qty,
274:             authorized_qty=authorized_qty,
275:             estimated_risk_dollars=estimated_risk,
276:             risk_level=self.risk_level,
277:         )
```

#### Root Cause Analysis
`estimated_risk` is calculated as `authorized_qty * stop_dist`. If `requested_qty = 10` and `authorized_qty = 250`, `estimated_risk` returns the risk of 250 shares ($500.00), not the risk of the requested 10 shares ($20.00).

#### Concrete Fix Proposal
Compute `estimated_risk` based on the effective trade quantity:
```python
effective_qty = min(requested_qty, authorized_qty)
estimated_risk = round(effective_qty * stop_dist, 2)
```

---

## Subsystem Architectural Health Summary

| Subsystem | Status | Health Rating | Key Strength | Primary Vulnerability |
|:---|:---:|:---:|:---|:---|
| **Ingestion Adapters** (`src/ingestion/`) | Operational | **Good** | Robust backpressure buffer, exponential reconnect, banner auth verification | `stock_ws.py` `task_done()` placement inside `try` block |
| **Risk Engine** (`src/risk/`) | Operational | **Excellent** | Accurate $1,500 daily loss trip, sector correlation checks, FINRA leverage | `RiskEngineConfig` default `max_position_equity_pct` mismatch |
| **Execution Engine** (`src/core/engine.py`) | Operational | **Excellent** | Full 8-state FSM, SEC/FINRA fees, slippage simulation, priority matching | None observed |
| **Bracket Management** (`src/core/bracket.py`) | Operational with defects | **Requires Fix** | OCO scaling, dynamic breakeven ratcheting, ATR trailing stop | Target 2 partial fill leaves unprotected shares; leaked `order_to_bracket` |
| **Portfolio & Accounting** (`src/core/account.py`) | Operational | **Excellent** | DTBP 4:1 leverage calculation, margin call recovery, position flips | Lifetime realized PnL counter needs clear distinction from daily PnL |
| **Strategies** (`src/strategies/`) | Operational with defects | **Good** | Strong indicators (VWAP, ATR, Z-Score, RVOL), clear entry/exit logic | ORB & News Momentum lack 0.4% stop floor clamping |
| **Adaptation & Regimes** (`src/strategies/adaptation.py`) | Operational | **Excellent** | 15/25/35 canonical VIX boundaries, Time-of-Day phase gating, signal priority | None observed |
| **Flattening Engine** (`src/core/flattening.py`) | Operational | **Excellent** | 4-phase zero-overnight sequence, audit sweep and re-verification | Pre-market time tick returns `NORMAL_TRADING` |
| **Streaming & Server** (`src/main.py`) | Operational | **Good** | Bi-directional UI WebSocket, periodic broadcast, fill reconciliation | Working orders not purged at ET session date boundary |

---

## Verification Plan & Commands

All findings can be verified independently via targeted unit tests and full suite runs:
1. **Backend Unit Suite**:
   ```bash
   pytest backend/tests
   ```
   *Baseline*: 140/140 passed in 0.73s.
2. **End-to-End Suite**:
   ```bash
   pytest tests/e2e
   ```
   *Baseline*: 293/293 passed in 22.83s.
3. **Target 2 Partial Fill Verification**:
   Execute a simulated fill on `t2_<bracket_id>` with `qty < bracket.remaining_qty` and assert that `bracket.status != COMPLETED_PROFIT` and `bracket.stop_order_id` is NOT cancelled.
4. **Session Boundary Working Order Verification**:
   Create a working order in `engine.working_orders`, call `_check_session_boundary` with a new date, and assert that `engine.working_orders` is empty.
