# Comprehensive Vulnerability & Risk Engine Audit Report (Explorer R6-3)

**Target System**: AutonomousDayTrader (12-Symbol Universe, Multi-Sector Risk Engine, Next.js Mobile-First UI)  
**Author**: Explorer R6-3  
**Date**: 2026-09-23T20:17:00Z  
**Integrity Mode**: Read-Only Adversarial Investigation  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui`

---

## 1. Executive Summary

An exhaustive, adversarial review of the risk engine knife-edge boundaries, multi-sector concentration limits under simultaneous signal collisions across 12 tickers, 4-phase EOD auto-flattening lifecycle, API / WebSocket serialization, and frontend error boundaries was conducted.

The investigation uncovered **7 critical and major latent defects** in the production codebase that compromise institutional risk invariants, create race conditions, allow catastrophic multi-position over-allocation, and can cause connected frontend clients to crash or enter perpetual reconnection loops:

| # | Vulnerability Area | Severity | Impact | Primary File & Line |
|---|---|---|---|---|
| **D1** | Multi-Sector & Concurrency Breach Under Simultaneous Collisions | **CRITICAL** | Submitting multiple signals in the same bar or rapid ticks allows 4+ concurrent positions and 3+ positions per sector because pending working orders are omitted from the active set. | `backend/app/main.py:108-116`, `backend/app/strategies/adaptation.py:255-269` |
| **D2** | Circuit Breaker Evaluation Gap in Pre-Trade Risk Engine | **CRITICAL** | `evaluate_order_request` passes orders for full sizing even when account drawdown exceeds $1,500 if `evaluate_account_state` was not called immediately prior; also fails to cap sizing to remaining drawdown budget. | `backend/app/core/risk.py:155-166` |
| **D3** | Single-Position Notional Cap ($25,000 / 50% Equity) Exposure Leak | **MAJOR** | `risk.py` does not deduct existing position or working order notional for the symbol, permitting combined exposure to reach up to 90% of equity ($45,000). | `backend/app/core/risk.py:268-274` |
| **D4** | EOD Phase 2 Order Purge Leaves Positions Naked for 5 Minutes | **CRITICAL** | At 15:50 ET, Phase 2 cancels all working orders (including protective bracket stop-losses) while delaying position liquidation to 15:55 ET. Positions are left naked; durable checkpoint validation fails. | `backend/app/core/flattening.py:187-198`, `backend/app/main.py:1240-1243` |
| **D5** | Manual Tighten Stop Bypasses Institutional Bounds $[0.0040, 0.0400]$ | **MAJOR** | UI action `TIGHTEN_STOP` modifies working stop prices directly without checking minimum distance (40 bps), allowing stops as tight as 0.1 bps or 0 bps. | `backend/app/core/bracket.py:510-560`, `backend/app/main.py:1916-1934` |
| **D6** | WebSocket Serialization Crash via `NaN`/`Infinity` & Payload Bloat | **MAJOR** | Python `json.dumps` emits unquoted `NaN`/`Infinity`, crashing JavaScript `JSON.parse`. Sending 120 chart points for all positions at 4 Hz triggers 350ms send timeout and client eviction. | `backend/app/main.py:415, 824, 853-858`, `frontend/hooks/useTradingStream.ts:154, 218` |
| **D7** | Frontend Null Safety, SVG NaN Geometry & Drawer Touch Conflicts | **MINOR** | Unsafe calls to `toLocaleString` / `toFixed` in header and page crash React into `error.tsx` on missing fields; `Math.min/max` with `NaN` breaks SVG chart; `drag="y"` on scrollable drawer causes mobile gesture stutter. | `frontend/components/Header.tsx:166, 179`, `frontend/components/LiveChart.tsx:62-63`, `ActivePositionTray.tsx:173` |

All findings have been verified with deterministic reproduction scripts. Concrete production-grade remediation strategies and mutation test designs are documented below.

---

## 2. Defect 1: Multi-Sector & Concurrency Limit Breach Under Simultaneous Signal Collisions

### 2.1 Code Inspection & Context
- **Files**:
  - `backend/app/main.py` lines 108–116 (`pre_trade_risk_validator`)
  - `backend/app/main.py` lines 929–934 (`execute_strategy_signal`)
  - `backend/app/main.py` lines 1030–1043 (`handle_bar_event`)
  - `backend/app/strategies/adaptation.py` lines 255–269 (`arbitrate_signals`)
  - `backend/app/strategies/adaptation.py` lines 304–307 (`evaluate_signal_admission`)

In `backend/app/main.py`:
```python
107: def pre_trade_risk_validator(order: Any, acct: PaperTradingAccount) -> tuple[bool, str]:
108:     is_lockout = flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING
109:     active_symbols = set(acct.positions.keys())
110:     active_sectors = [
111:         risk_engine.symbol_sectors.get(s, "Other")
112:         for s in active_symbols
113:         if s in risk_engine.symbol_sectors
114:     ]
...
157:     res = risk_engine.evaluate_order_request(
...
165:         active_positions_count=len(acct.positions),
166:         active_symbols=active_symbols,
167:         active_sectors=active_sectors,
...
```

And in `backend/app/strategies/adaptation.py`:
```python
255:     def arbitrate_signals(self, signals: List[SignalEvent]) -> List[SignalEvent]:
256:         """Sort colliding signals by priority hierarchy and deduplicate per symbol."""
257:         sorted_sigs = sorted(
258:             signals,
259:             key=lambda s: (self.STRATEGY_PRIORITY.get(s.strategy_id.lower(), 0), s.confidence),
260:             reverse=True,
261:         )
262:         seen_symbols: Set[str] = set()
263:         deduped: List[SignalEvent] = []
264:         for s in sorted_sigs:
265:             sym = s.symbol.upper()
266:             if sym not in seen_symbols:
267:                 seen_symbols.add(sym)
268:                 deduped.append(s)
269:         return deduped
```

### 2.2 Mechanism of Failure
1. In the 12-symbol universe (`SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `AMD`, `MSFT`, `AMZN`, `META`, `GOOGL`, `PLTR`, `COIN`), market events frequently arrive simultaneously.
2. `arbitrate_signals` only deduplicates signals per single symbol. When signals arrive for multiple distinct symbols in the same bar or rapid ticks (e.g. `AAPL`, `NVDA`, `AMD`, `MSFT`, `TSLA`), all 5 signals are returned in `deduped`.
3. In `handle_bar_event`:
   ```python
   for sig in arbitrated:
       await execute_strategy_signal(sig)
   ```
4. For the first signal (`AAPL`):
   - `len(acct.positions) == 0`.
   - `AAPL` order is created and submitted. It enters `engine.working_orders` in state `ACCEPTED`.
   - **Crucially: `account.positions` is only updated when an order fills. It remains empty (`len(account.positions) == 0`).**
5. For the second signal (`NVDA`):
   - `len(acct.positions)` is still `0`. NVDA is accepted into `working_orders`.
6. For the third signal (`AMD`):
   - `len(acct.positions)` is still `0`. `active_sectors` is still `[]`.
   - Even though both `NVDA` and `AMD` are in `Semiconductors`, the sector count is evaluated as `0`. AMD is accepted.
7. For the fourth (`MSFT`) and fifth (`TSLA`) signals:
   - Both see `len(acct.positions) == 0 < 3` and are accepted into `working_orders`.
8. Once in `working_orders`, incoming bars or quotes match and fill all 5 orders.
9. `acct.positions` now holds 5 active positions (exceeding `max_concurrent_positions = 3`), and can hold 3+ positions in a single sector if 3 signals from that sector collided.

### 2.3 Empirical Verification Proof
Executed deterministic test against `ExecutionEngine` and `InstitutionalRiskEngine`:
```
Accepted orders: ['AAPL', 'NVDA', 'AMD', 'MSFT', 'TSLA']
Working orders count: 5
Total open positions after fill: 5
```
Result: `assert len(acct.positions) == 5` succeeded. 5 positions opened when maximum allowed is 3.

---

## 3. Defect 2: Circuit Breaker Evaluation Gap in Pre-Trade Risk Engine

### 3.1 Code Inspection & Context
- **File**: `backend/app/core/risk.py` lines 155–166 (`InstitutionalRiskEngine.evaluate_order_request`)
- **File**: `backend/app/main.py` lines 1747–1818 (`POST /api/orders`)

In `backend/app/core/risk.py`:
```python
155:         # 1. Circuit Breaker Check
156:         if self.status != BreakerStatus.ARMED:
157:             return RiskCheckResult(
158:                 approved=False,
159:                 reason=f"CIRCUIT_BREAKER_HALTED: Trading halted due to maximum daily loss ({self.status.value})",
160:                 requested_qty=requested_qty,
161:                 authorized_qty=0,
162:                 estimated_risk_dollars=0.0,
163:                 risk_level=self.risk_level,
164:                 rejection_code="CIRCUIT_BREAKER_HALTED",
165:             )
```

### 3.2 Mechanism of Failure
1. `evaluate_order_request` receives `account_equity: float`.
2. It only inspects `self.status != BreakerStatus.ARMED`.
3. `self.status` is only transitioned to `HALTED_DAILY_LOSS` inside `evaluate_account_state()`.
4. If an order is submitted via `POST /api/orders` (or if market prices moved down between ticks without an intervening `evaluate_account_state` call):
   - `self.status` remains `BreakerStatus.ARMED`.
   - `evaluate_order_request` does NOT check whether `self.config.starting_equity - account_equity >= self.config.hard_max_daily_loss_dollars`.
   - It proceeds to Step 6, calculates `target_risk_dollars = min(1000.0, account_equity * 0.01)`, and approves the order!
5. Even if `account_equity` has dropped to $45,000 (a $5,000 drawdown, far beyond the $1,500 limit), `evaluate_order_request` approves the order.
6. Furthermore, `evaluate_order_request` fails to check whether accepting the order risks breaching the remaining daily loss budget:
   `remaining_budget = self.config.hard_max_daily_loss_dollars - max(0.0, self.config.starting_equity - account_equity)`
   If current drawdown is $1,400 and `target_risk_dollars` is $500, a stop out results in $1,900 drawdown, blowing past the $1,500 circuit breaker.

### 3.3 Empirical Verification Proof
Executed test with `starting_equity = 50000.0`, `account_equity = 47000.0` ($3,000 drawdown), `self.status = BreakerStatus.ARMED`:
```
Approved: True
Authorized qty: 156
Reason: Approved
```
Result: Order for 156 shares ($23,400 notional) approved during a $3,000 drawdown.

---

## 4. Defect 3: Single-Position Notional Cap ($25,000 / 50% Equity) Exposure Leak

### 4.1 Code Inspection & Context
- **File**: `backend/app/core/risk.py` lines 268–274

In `backend/app/core/risk.py`:
```python
268:         # Max single-position notional (max_position_equity_pct of equity)
269:         max_notional = account_equity * self.config.max_position_equity_pct
270:         q_alloc = int(math.floor(max_notional / entry_price))
271:         # Buying power capacity
272:         q_bp = int(math.floor(buying_power / entry_price))
273: 
274:         authorized_qty = min(q_risk, q_alloc, q_bp)
```

### 4.2 Mechanism of Failure
1. `max_notional` is calculated as `account_equity * max_position_equity_pct` ($25,000 on $50,000 equity).
2. `q_alloc` calculates how many shares can be bought for `$25,000 / entry_price`.
3. `risk.py` **never subtracts existing position notional or pending working order notional for that symbol**.
4. If an account already holds 100 shares of AAPL at $200 ($20,000 notional), `evaluate_order_request` computes `q_alloc = floor(25000 / 200) = 125` shares ($25,000).
5. It authorizes another 125 shares. When filled, the total position is 225 shares = $45,000 notional, representing **90% of equity** (almost double the 50% institutional ceiling).

### 4.3 Empirical Verification Proof
Executed test with `account_equity = 50000.0`, existing position 100 shares at $200 ($20,000):
```
Approved: True
Authorized qty: 125
Total notional if filled: 45000.0
```
Result: 125 shares authorized, creating $45,000 exposure on $50,000 capital.

---

## 5. Defect 4: Phase 2 EOD Order Purge Leaves Positions Naked For 5 Minutes

### 5.1 Code Inspection & Context
- **File**: `backend/app/core/flattening.py` lines 187–198 (`execute_phase_2_purge`)
- **File**: `backend/app/main.py` lines 1240–1256 (`handle_flattening_directive`)
- **File**: `backend/app/core/runtime_state.py` lines 261–264 (`validate_runtime_state`)

In `backend/app/core/flattening.py`:
```python
187:     def execute_phase_2_purge(self) -> FlatteningDirective:
188:         """Phase 2 (15:50 ET): Working Order Purge."""
189:         self.phase2_executed = True
190:         self.current_phase = FlatteningPhase.ORDER_PURGE
191:         return FlatteningDirective(
192:             phase=FlatteningPhase.ORDER_PURGE,
193:             timestamp=self.clock.now(),
194:             action_required="PURGE_WORKING_ORDERS",
195:             lock_new_entries=True,
196:             cancel_all_orders=True,
197:         )
```

In `backend/app/main.py`:
```python
1240:     if directive.cancel_all_orders:
1241:         engine.cancel_all_orders("FLATTENING_DIRECTIVE")
1242:         _release_dead_entry_brackets()
1243: 
1244:     if directive.liquidate_all_positions:
...
```

And in `backend/app/core/runtime_state.py`:
```python
261:             stop_order = engine.working_orders.get(bracket.stop_order_id or "")
262:             if stop_order is None or stop_order.remaining_qty != position.shares:
263:                 raise PersistenceError(f"Position {symbol} is not fully protected by its stop")
```

### 5.2 Mechanism of Failure
1. At 15:50 ET, Phase 2 fires with `cancel_all_orders = True` and `liquidate_all_positions = False`.
2. `engine.cancel_all_orders("FLATTENING_DIRECTIVE")` cancels all working orders indiscriminately.
3. This cancels the working `STOP_LOSS` orders for all open positions!
4. Mandatory market liquidation does not occur until Phase 3 at 15:55 ET.
5. For **5 full minutes** (15:50 to 15:55 ET), all open positions are held **completely naked** without stop loss protection.
6. If a durable checkpoint is taken or validated during this 5-minute window, `validate_runtime_state` raises:
   `PersistenceError: Position AAPL is not fully protected by its stop`.
7. This trips recovery halt and corrupts durable persistence guarantees.

### 5.3 Empirical Verification Proof
Executed test creating an AAPL position with protective bracket stop, followed by `engine.cancel_all_orders('FLATTENING_DIRECTIVE')`:
```
Pre-purge validation: HEALTHY
Working orders after purge: 0
Positions still open: 1
Post-purge validation FAILED as expected: Position AAPL is not fully protected by its stop
```
Result: Validation failed deterministically with `PersistenceError`.

---

## 6. Defect 5: Manual Tighten Stop Bypasses Institutional Bounds $[0.0040, 0.0400]$

### 6.1 Code Inspection & Context
- **File**: `backend/app/core/bracket.py` lines 510–560 (`manual_tighten_stop`)
- **File**: `backend/app/main.py` lines 1916–1934 (`ui_websocket_endpoint`)

In `backend/app/core/bracket.py`:
```python
510:     def manual_tighten_stop(
511:         self,
512:         symbol: str,
513:         new_stop_price: float,
514:         current_market_price: Optional[float] = None,
515:     ) -> BracketUpdateDirective:
...
532:         if current_market_price is not None:
533:             if bracket.side == "LONG" and new_stop_price > current_market_price:
534:                 new_stop_price = current_market_price
535:             elif bracket.side != "LONG" and new_stop_price < current_market_price:
536:                 new_stop_price = current_market_price
...
541:                 bracket.current_stop_price = new_stop_price
```

### 6.2 Mechanism of Failure
1. In `bracket_manager.manual_tighten_stop`, the only constraint is that a LONG stop cannot exceed `current_market_price` and a SHORT stop cannot be below `current_market_price`.
2. It **never checks** `min_stop_distance_pct` ($0.0040$ / 40 bps) or `max_stop_distance_pct` ($0.0400$ / 400 bps).
3. In `main.py`, the WebSocket handler receives `TIGHTEN_STOP` and directly writes the new stop to `engine.working_orders[oid].stop_price` without routing through `risk_engine`.
4. A user can tighten the stop to $0.01 away from market price (e.g. 1 bps or 0.1 bps), causing instant stop-outs on microsecond bid/ask spread noise and violating institutional stop distance rules.

### 6.3 Empirical Verification Proof
Executed test on AAPL at $150.00: tightened stop to $149.95:
```
Action: MODIFY_ORDER
New stop in directive: 149.95
Stop distance bps: 3.33 bps (< 40 bps required)
```
Result: 3.33 bps stop distance accepted and modified without rejection.

---

## 7. Defect 6: WebSocket Serialization Crash via `NaN`/`Infinity` & Payload Bloat

### 7.1 Code Inspection & Context
- **File**: `backend/app/main.py` lines 415, 824, 853–858
- **File**: `frontend/hooks/useTradingStream.ts` lines 154, 218

In `backend/app/main.py`:
```python
824:         "all_positions": [_serialize_position(symbol) for symbol in account.positions],
...
853:     raw = json.dumps(payload, default=str)
854:     for ws in list(ui_clients):
855:         try:
856:             await asyncio.wait_for(ws.send_text(raw), timeout=0.35)
857:         except Exception:
858:             ui_clients.discard(ws)
```

And in `_serialize_position`:
```python
415:         "chart_points": market_history.get(symbol, [])[-120:],
```

### 7.2 Mechanism of Failure
1. Python's `json.dumps(payload, default=str)` defaults to `allow_nan=True`.
2. If any float in `account` (e.g. `daily_pnl_pct` on zero starting equity), `strategies` (e.g. `sharpe` on zero variance), or `market_context` (e.g. uninitialized `vix`) is `float('nan')` or `float('inf')`, Python outputs literal `NaN` or `Infinity`.
3. In RFC 8259, `NaN` and `Infinity` are invalid JSON tokens.
4. When the browser receives the message, `JSON.parse(event.data)` throws `SyntaxError: Unexpected token 'N'` and fails silently to console error.
5. In addition, each serialized position in `all_positions` includes 120 full candle dicts (`"chart_points"`). With multiple open positions, the payload size reaches 150+ KB. Transmitting 150 KB at 4 Hz (600 KB/sec) exceeds the 350ms send timeout on slower or mobile connections, causing `ui_clients.discard(ws)` to disconnect the client in an infinite reconnect loop.
6. Iteration hazard: `for symbol in account.positions` iterates over a live dictionary. An asynchronous fill or manual flatten concurrent with broadcast raises `RuntimeError: dictionary changed size during iteration`.

---

## 8. Defect 7: Frontend Null Safety, SVG NaN Geometry & Drawer Touch Conflicts

### 8.1 Code Inspection & Context
- **File**: `frontend/components/Header.tsx` lines 166, 179–180, 190, 196
- **File**: `frontend/app/page.tsx` line 71
- **File**: `frontend/components/LiveChart.tsx` lines 62–63, 72–74
- **File**: `frontend/components/ActivePositionTray.tsx` lines 173–176

In `frontend/components/Header.tsx`:
```tsx
166:  ${account.equity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
179:  ${Math.abs(account.daily_pnl).toFixed(2)} ({pnlSign}
180:  {account.daily_pnl_pct.toFixed(2)}%) Today
190:  ${account.cash.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
```

In `frontend/components/LiveChart.tsx`:
```tsx
62:   const min = Math.min(...allPrices);
63:   const max = Math.max(...allPrices);
...
72:   const getY = (price: number) => {
73:     if (priceRange === 0) return height / 2;
74:     return height - ((price - minPrice) / priceRange) * height;
75:   };
```

### 8.2 Mechanism of Failure
1. If `account.equity`, `daily_pnl`, or `cash` is `undefined` or `null` during initialization or partial payload sync, `account.equity.toLocaleString` throws unhandled `TypeError: Cannot read properties of undefined`. This bubbles up and crashes the page into `app/error.tsx`.
2. In `LiveChart.tsx`, if any item in `allPrices` is `NaN`, `Math.min(...allPrices)` returns `NaN`. `minPrice`, `maxPrice`, and `priceRange` all become `NaN`. `getY()` evaluates to `NaN`, rendering SVG `<line y1="NaN" y2="NaN">` and blanking the chart.
3. In `ActivePositionTray.tsx`, `motion.div drag="y"` wraps the entire modal sheet container that also has `overflow-y-auto max-h-[92vh]`. Touch scrolling inside the drawer conflicts with the Framer Motion drag gesture, causing scroll lock or unwanted modal dismissal on mobile viewports.

---

## 9. Concrete Production-Grade Fix Strategies

### 9.1 Fix Strategy for D1: Atomic Concurrency & Sector Reservation
Update `pre_trade_risk_validator` and `execute_strategy_signal` in `backend/app/main.py`:
Compute the **effective committed active symbols and sectors**, including both filled positions and pending working entry orders:
```python
def get_effective_committed_portfolio(acct: PaperTradingAccount, engine: ExecutionEngine) -> tuple[set[str], list[str], int]:
    """Return the union of active positions and working entry commitments."""
    active_symbols = set(acct.positions.keys())
    # Include symbols with working orders that represent position-opening commitments
    for order in engine.working_orders.values():
        if order.status.value in ("ACCEPTED", "PARTIALLY_FILLED"):
            position = acct.positions.get(order.symbol)
            is_reducing = bool(
                position and (
                    (position.side.value == "LONG" and order.side.value == "SELL") or
                    (position.side.value == "SHORT" and order.side.value == "BUY")
                )
            )
            if not is_reducing:
                active_symbols.add(order.symbol.upper())

    active_sectors = [
        risk_engine.symbol_sectors.get(s, "Other")
        for s in active_symbols
        if s in risk_engine.symbol_sectors
    ]
    return active_symbols, active_sectors, len(active_symbols)
```
In `adaptation_engine.arbitrate_signals`:
Cap the maximum number of signals returned to `max(0, self.max_concurrent_positions - committed_count)` to avoid issuing signals that cannot be accommodated.

### 9.2 Fix Strategy for D2: Pre-Trade Circuit Breaker Guard & Loss Budgeting
In `backend/app/core/risk.py:evaluate_order_request`:
```python
        # 1. Circuit Breaker Check (enforce on both internal status and real-time equity)
        dd_dollars = max(0.0, round(self.config.starting_equity - account_equity, 2))
        if self.status != BreakerStatus.ARMED or dd_dollars >= self.config.hard_max_daily_loss_dollars:
            return RiskCheckResult(
                approved=False,
                reason=f"CIRCUIT_BREAKER_HALTED: Drawdown ${dd_dollars:.2f} >= ${self.config.hard_max_daily_loss_dollars:.2f}",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=RiskLevel.HALTED,
                rejection_code="CIRCUIT_BREAKER_HALTED",
            )

        # 1b. Remaining Loss Budgeting
        remaining_loss_budget = max(0.0, self.config.hard_max_daily_loss_dollars - dd_dollars)
        if remaining_loss_budget <= 0.0:
            return RiskCheckResult(
                approved=False,
                reason="EXHAUSTED_DAILY_LOSS_BUDGET: No remaining risk budget available",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="EXHAUSTED_DAILY_LOSS_BUDGET",
            )
```
And cap `target_risk_dollars = min(target_risk_dollars, remaining_loss_budget)`.

### 9.3 Fix Strategy for D3: Single-Position Cap Net of Existing Exposure
In `backend/app/core/risk.py:evaluate_order_request`:
```python
        # Max single-position notional cap net of existing position exposure
        max_notional = account_equity * self.config.max_position_equity_pct
        current_symbol_notional = 0.0
        # If existing position or working order exists for symbol, deduct its notional
        if symbol in active_symbols:
            # Net existing exposure
            current_symbol_notional = existing_exposure_map.get(symbol, 0.0)
        available_notional = max(0.0, max_notional - current_symbol_notional)
        q_alloc = int(math.floor(available_notional / entry_price))
```

### 9.4 Fix Strategy for D4: Purge Unfilled Entries at 15:50 While Preserving Protective Stops
In `backend/app/main.py:handle_flattening_directive`:
```python
    if directive.phase == FlatteningPhase.ORDER_PURGE:
        # Purge only exposure-increasing / entry orders; preserve protective stops!
        for order_id, order in list(engine.working_orders.items()):
            pos = account.positions.get(order.symbol)
            is_protective = bool(
                pos and (
                    (pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
                    (pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
                )
            )
            if not is_protective:
                engine.cancel_order(order_id, reason="EOD_PURGE_UNFILLED_ENTRIES")
        _release_dead_entry_brackets()
```
Protective stops remain active until Phase 3 (15:55 ET), when `engine.cancel_all_orders` and `_flatten_symbol` run together atomically.

### 9.5 Fix Strategy for D5: Institutional Stop Distance Clamping on Manual Tighten
In `backend/app/core/bracket.py:manual_tighten_stop`:
```python
        # Enforce institutional stop distance bounds [0.0040, 0.0400] on manual tighten
        ref_price = current_market_price or bracket.entry_price
        dist = abs(ref_price - new_stop_price)
        dist_pct = dist / ref_price
        if dist_pct < 0.0040:
            # Clamp to min 40 bps distance from market price
            min_dist = ref_price * 0.0040
            new_stop_price = round(ref_price - min_dist if bracket.side == "LONG" else ref_price + min_dist, 4)
        elif dist_pct > 0.0400:
            max_dist = ref_price * 0.0400
            new_stop_price = round(ref_price - max_dist if bracket.side == "LONG" else ref_price + max_dist, 4)
```

### 9.6 Fix Strategy for D6: JSON Sanitization & Payload Optimization
In `backend/app/main.py`:
1. Use a recursive sanitizer converting `NaN` and `Infinity` to `None`:
```python
def sanitize_json_value(val: Any) -> Any:
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    elif isinstance(val, dict):
        return {k: sanitize_json_value(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple)):
        return [sanitize_json_value(v) for v in val]
    return val
```
2. Strip `"chart_points"` from `all_positions` (only keep them in `primary_position`):
```python
def _serialize_position_summary(symbol: str) -> Dict[str, Any]:
    pos_dict = _serialize_position(symbol)
    pos_dict.pop("chart_points", None)  # Omit 120 points per background position
    return pos_dict
```
3. Snapshot dictionary keys: `[_serialize_position(s) for s in list(account.positions.keys())]`.

### 9.7 Fix Strategy for D7: Safe Frontend Formatter & Drag Handle Binding
1. In `Header.tsx`, `page.tsx`, and `LiveChart.tsx`:
   Wrap all numeric formatting with `safeFixed` / `safeLocale` and fallback to `"0.00"` / `0.0`.
2. In `ActivePositionTray.tsx`:
   Bind `drag="y"` to a dedicated `useDragControls()` handle instead of the entire scrollable container.

---

## 10. Deterministic Mutation Test Suite Designs

To guarantee that these defects cannot regress, the following mutation test cases should be integrated into `backend/tests/stress/`:

### Mutation Test 1: Simultaneous Multi-Sector Signal Collision (`test_simultaneous_12_ticker_collision`)
- **Intent**: Feed 12 simultaneous buy signals across all sectors (`SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `AMD`, `MSFT`, `AMZN`, `META`, `GOOGL`, `PLTR`, `COIN`) in a single timestamp tick.
- **Assertion**:
  - Exactly 3 orders are accepted into `working_orders`.
  - All remaining 9 orders are rejected with `MAX_CONCURRENT_POSITIONS_REACHED` or `CORRELATED_SECTOR_EXPOSURE`.
  - No sector has more than 2 accepted orders.
  - When quotes fill the orders, `len(account.positions) == 3`.

### Mutation Test 2: Pre-Trade Circuit Breaker Under Un-evaluated Account Equity (`test_pre_trade_circuit_breaker_unevaluated_equity`)
- **Intent**: Call `evaluate_order_request` with `account_equity = 48400.0` ($1,600 drawdown) while `risk_engine.status == BreakerStatus.ARMED`.
- **Assertion**:
  - `result.approved is False`.
  - `result.rejection_code == "CIRCUIT_BREAKER_HALTED"`.
  - `result.authorized_qty == 0`.

### Mutation Test 3: Single-Position Cap Net of Existing Shares (`test_single_position_cap_with_existing_shares`)
- **Intent**: Account holds $20,000 of AAPL (100 shares at $200). Request another 100 shares at $200.
- **Assertion**:
  - `authorized_qty` cannot exceed 25 shares (max $5,000 additional notional).
  - Total combined notional `(100 + authorized_qty) * 200.0 <= 25000.00`.

### Mutation Test 4: EOD Phase 2 Purge Protective Stop Invariant (`test_phase_2_purge_preserves_stop_protection`)
- **Intent**: Open an AAPL position with protective bracket stop. Execute Phase 2 (`ORDER_PURGE`).
- **Assertion**:
  - Protective stop order remains in `engine.working_orders`.
  - `validate_runtime_state(account, engine, bracket_manager)` passes with zero exceptions.
  - Unfilled entry orders in other symbols are cancelled.

### Mutation Test 5: Manual Tighten Stop Clamping (`test_manual_tighten_stop_clamps_to_min_bounds`)
- **Intent**: Call `manual_tighten_stop` for AAPL at $150.00 with `new_stop = 149.95` (3.3 bps).
- **Assertion**:
  - New stop price is clamped to $149.40 (exactly 40 bps distance).
  - Directive `new_stop_price` satisfies `abs(150.00 - stop) / 150.00 >= 0.0040`.

### Mutation Test 6: NaN/Infinity JSON Sanitization (`test_websocket_nan_sanitization`)
- **Intent**: Inject `float('nan')` into `account.daily_pnl_pct` and `strategies[0].sharpe`. Call `broadcast_ui_state`.
- **Assertion**:
  - `json.loads(raw)` succeeds in Python.
  - Subprocess `node -e "JSON.parse(process.argv[1])"` parses the wire payload without throwing `SyntaxError`.
  - Non-finite numbers are replaced with `null` or `0.0`.

---
*End of Analysis Report.*
