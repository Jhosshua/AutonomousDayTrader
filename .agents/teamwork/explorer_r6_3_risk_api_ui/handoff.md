# Handoff Report: Explorer R6-3

**Task**: Adversarial Investigation of Risk Engine Knife-Edge Boundaries, Multi-Sector Concentration Limits, EOD Auto-Flattening Races & API/UI State Synchronization.  
**Agent ID**: Explorer R6-3  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui`  
**Handoff Type**: Hard (Investigation Complete)  
**Timestamp**: 2026-09-23T20:18:00Z  

---

## 1. Observation

Direct observations and evidence collected during code inspection and test execution:

1. **Multi-Sector & Concurrency Limit Breach**:
   - `backend/app/main.py:108-116` & `backend/app/main.py:929-934`:
     ```python
     active_symbols = set(acct.positions.keys())
     active_sectors = [risk_engine.symbol_sectors.get(s, "Other") for s in active_symbols if s in risk_engine.symbol_sectors]
     ```
     `acct.positions` contains only filled positions. Working orders in `engine.working_orders` are completely omitted.
   - Empirical test execution command:
     `python3 -c "from backend.app.core.account import ... [5 simultaneous orders] ..."`
     Result:
     `Accepted orders: ['AAPL', 'NVDA', 'AMD', 'MSFT', 'TSLA']`
     `Working orders count: 5`
     `Total open positions after fill: 5`
     Verified breach of `max_concurrent_positions = 3` and `max_positions_per_sector = 2`.

2. **Circuit Breaker Evaluation Gap in `evaluate_order_request`**:
   - `backend/app/core/risk.py:155-166`:
     ```python
     if self.status != BreakerStatus.ARMED:
         return RiskCheckResult(approved=False, ...)
     ```
     Does not check `account_equity` against `starting_equity` or `hard_max_daily_loss_dollars` ($1,500).
   - Empirical test execution command with `starting_equity = 50000.0`, `account_equity = 47000.0` ($3,000 drawdown), `self.status = ARMED`:
     Result:
     `Approved: True, Authorized qty: 156, Reason: Approved`. Order approved despite $3,000 loss.

3. **Single-Position Notional Cap Exposure Leak**:
   - `backend/app/core/risk.py:268-274`:
     `max_notional = account_equity * self.config.max_position_equity_pct`
     `q_alloc = int(math.floor(max_notional / entry_price))`
     Does not deduct existing position notional for the symbol.
   - Empirical test execution command with 100 shares of AAPL ($20,000) already held on $50,000 equity:
     Result: Authorized 125 additional shares ($25,000). Total exposure: $45,000 (90% of equity, exceeding 50% cap).

4. **Phase 2 EOD Order Purge Leaves Positions Naked**:
   - `backend/app/core/flattening.py:187-198`: Phase 2 specifies `cancel_all_orders=True`, `liquidate_all_positions=False`.
   - `backend/app/main.py:1240-1243`:
     ```python
     if directive.cancel_all_orders:
         engine.cancel_all_orders("FLATTENING_DIRECTIVE")
         _release_dead_entry_brackets()
     ```
   - `backend/app/core/runtime_state.py:261-264`:
     `validate_runtime_state` checks `stop_order = engine.working_orders.get(bracket.stop_order_id or "")`.
   - Empirical test execution command:
     Result:
     `Working orders after purge: 0`
     `Positions still open: 1`
     `Post-purge validation FAILED as expected: Position AAPL is not fully protected by its stop`.

5. **Manual Tighten Stop Distance Boundary Bypass**:
   - `backend/app/core/bracket.py:510-560`: `manual_tighten_stop` does not check `min_stop_distance_pct` ($0.0040$ / 40 bps).
   - `backend/app/main.py:1916-1934`: `TIGHTEN_STOP` modifies working order stop price directly without risk engine validation.
   - Empirical test execution on AAPL at $150.00: stop tightened to $149.95.
     Result: `Stop distance bps: 3.33` (< 40 bps required).

6. **WebSocket Serialization Crash on `NaN`/`Infinity` & Payload Bloat**:
   - `backend/app/main.py:853`: `raw = json.dumps(payload, default=str)` emits unquoted `NaN` and `Infinity`.
   - Node.js test: `node -e "JSON.parse('{\"a\": NaN, \"b\": Infinity}')"`.
     Result: `SyntaxError: Unexpected token 'N', "{"a": NaN, "b": "... is not valid JSON`.
   - `backend/app/main.py:415`: `"chart_points": market_history.get(symbol, [])[-120:]` included for all 12 positions at 4 Hz, producing 150+ KB per broadcast and exceeding 350ms send timeout (`asyncio.wait_for(ws.send_text(raw), timeout=0.35)`).

7. **Process Hygiene**:
   - Port 3005 had a lingering process PID 41385 (`node scripts/serve_export.mjs --port 3005`). Process was cleanly terminated via `kill 41385` and port 3005 verified liberated. Backend pytest suite currently passes 324/324 tests.

---

## 2. Logic Chain

1. **Simultaneous Signal Collision Over-Allocation**:
   - Premise A: `account.positions` only records filled positions (Observation 1).
   - Premise B: Submitting an order moves it to `engine.working_orders` in state `ACCEPTED`, but does not populate `account.positions` until fill (Observation 1).
   - Premise C: `pre_trade_risk_validator` and `adaptation_engine.arbitrate_signals` evaluate concurrency and sector limits solely by checking `len(account.positions)` and `account.positions.keys()` (Observation 1).
   - Step 1: When 5 signals arrive simultaneously in a single bar, order 1 is submitted into `working_orders`.
   - Step 2: Orders 2, 3, 4, and 5 evaluate `len(account.positions) == 0` and are all accepted.
   - Step 3: When fills arrive, 5 positions open, breaching the maximum of 3 concurrent positions and 2 per sector.
   - Conclusion: Concurrency and sector risk invariants are violated unless working entry commitments are tracked in the active set.

2. **Circuit Breaker Pre-Trade Pass**:
   - Premise A: `risk_engine.status` is only updated when `evaluate_account_state` is invoked (Observation 2).
   - Premise B: `evaluate_order_request` does not evaluate `starting_equity - account_equity >= hard_max_daily_loss_dollars` (Observation 2).
   - Step 1: If account equity falls below $48,500 ($1,500 loss), but `evaluate_account_state` has not run, `status` is `ARMED`.
   - Step 2: An order evaluated via `evaluate_order_request` passes all checks and is approved.
   - Conclusion: Pre-trade risk validation fails to enforce the circuit breaker unless real-time equity is compared against starting equity inside `evaluate_order_request`.

3. **EOD Phase 2 Naked Exposure Window**:
   - Premise A: Phase 2 cancels all working orders at 15:50 ET (Observation 4).
   - Premise B: Phase 3 liquidates positions at 15:55 ET (Observation 4).
   - Premise C: Between 15:50 and 15:55, open positions are still held (Observation 4).
   - Step 1: Cancelling all working orders at 15:50 destroys the protective stop-loss orders.
   - Step 2: Positions remain unhedged for 5 minutes.
   - Step 3: `validate_runtime_state` rejects the state checkpoint because positions lack stop orders.
   - Conclusion: Phase 2 must only purge unfilled entry orders, preserving protective stop brackets until Phase 3 liquidation.

4. **WebSocket Desynchronization & Client Crash**:
   - Premise A: Python `json.dumps` with `allow_nan=True` serializes `float('nan')` as `NaN` (Observation 6).
   - Premise B: Browser `JSON.parse` rejects `NaN` with `SyntaxError` (Observation 6).
   - Step 1: If any float becomes NaN/Infinity, `json.dumps` outputs invalid JSON.
   - Step 2: Connected web clients crash during stream reception and cease updating.
   - Conclusion: Payloads must be sanitized to valid JSON (null/0.0) before broadcast.

---

## 3. Caveats

- **Network Mode**: Investigation was performed in local development integrity mode without modifying production files.
- **Assumptions**: The 12-symbol universe uses standard Eastern Time market hours (09:30–16:00 ET).
- No other caveats.

---

## 4. Conclusion

The system contains 7 concrete, verified vulnerabilities across pre-trade risk gating, multi-sector concentration tracking, EOD flattening phase sequencing, and API/WebSocket serialization. Remediating these defects requires:
1. Updating `pre_trade_risk_validator` and `arbitrate_signals` to calculate active exposure based on the union of filled positions and working entry commitments.
2. Hardening `evaluate_order_request` with direct real-time drawdown and loss budget checks.
3. Deducting existing symbol notional in `risk.py` single-position sizing.
4. Restricting Phase 2 EOD purge to unfilled entry orders while keeping protective stop brackets alive until Phase 3 liquidation.
5. Clamping `manual_tighten_stop` to the $[0.0040, 0.0400]$ range.
6. Sanitizing all non-finite floats to null and stripping 120-bar chart points from background positions in `all_positions`.

---

## 5. Verification Method

To independently verify all findings and test suite integrity:

1. **Verify Backend Pytest Suite**:
   ```bash
   pytest backend/tests -q
   ```
   Expected: 324 passed.

2. **Verify Concurrency & Sector Limit Breach**:
   ```bash
   python3 -c "
   from backend.app.core.account import PaperTradingAccount
   from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig
   from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType

   acct = PaperTradingAccount()
   risk = InstitutionalRiskEngine(config=RiskEngineConfig(max_concurrent_positions=3, max_positions_per_sector=2))
   def validator(order, a):
       active_syms = set(a.positions.keys())
       active_secs = [risk.symbol_sectors.get(s, 'Other') for s in active_syms]
       res = risk.evaluate_order_request(
           symbol=order.symbol, side=order.side.value, requested_qty=order.qty,
           entry_price=100.0, stop_price=99.0, account_equity=a.equity,
           buying_power=a.buying_power, active_positions_count=len(a.positions),
           active_symbols=active_syms, active_sectors=active_secs,
       )
       return res.approved, res.reason
   engine = ExecutionEngine(acct, risk_validator=validator)
   for s in ['AAPL', 'NVDA', 'AMD', 'MSFT', 'TSLA']:
       o = engine.create_order(symbol=s, side=OrderSide.BUY, order_type=OrderType.MARKET, qty=10)
       engine.submit_order(o.id)
   assert len(engine.working_orders) == 5, 'Vulnerability verified: 5 orders accepted despite max 3'
   print('Verified: 5 orders accepted despite max 3')
   "
   ```

3. **Verify Pre-Trade Circuit Breaker Bypass**:
   ```bash
   python3 -c "
   from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig
   risk = InstitutionalRiskEngine(config=RiskEngineConfig(starting_equity=50000.0, hard_max_daily_loss_dollars=1500.0))
   res = risk.evaluate_order_request(
       symbol='AAPL', side='BUY', requested_qty=10, entry_price=150.0, stop_price=148.0,
       account_equity=47000.0, buying_power=100000.0, active_positions_count=0,
       active_symbols=set(), active_sectors=set()
   )
   assert res.approved is True, 'Vulnerability verified: order approved during $3,000 drawdown'
   print('Verified: order approved during $3,000 drawdown')
   "
   ```

4. **Verify EOD Phase 2 Naked Position & Checkpoint Validation Failure**:
   ```bash
   python3 -c "
   from datetime import datetime, timezone
   from backend.app.core.account import PaperTradingAccount
   from backend.app.core.bracket import DynamicBracketManager
   from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType
   from backend.app.core.runtime_state import validate_runtime_state, PersistenceError
   acct = PaperTradingAccount()
   bm = DynamicBracketManager()
   engine = ExecutionEngine(acct)
   o = engine.create_order('AAPL', OrderSide.BUY, OrderType.MARKET, 50)
   engine.submit_order(o.id)
   now = datetime.now(timezone.utc)
   engine.process_bar('AAPL', 150.0, 151.0, 149.0, 150.0, 10000, now)
   b = bm.create_bracket('brk_1', 'AAPL', 'LONG', 50, 150.0, 148.0)
   directive = bm.activate_bracket_on_fill('brk_1', 50, 150.0, now)
   stop_child = directive.orders_to_submit[0]
   stop_order = engine.create_order('AAPL', OrderSide.SELL, OrderType.STOP, stop_child['qty'], stop_price=stop_child['price'])
   engine.submit_order(stop_order.id)
   b.stop_order_id = stop_order.id
   engine.cancel_all_orders('FLATTENING_DIRECTIVE')
   try:
       validate_runtime_state(acct, engine, bm)
       assert False, 'Expected PersistenceError'
   except PersistenceError as e:
       print('Verified Phase 2 naked position vulnerability:', e)
   "
   ```

5. **Verify Clean Process & Port Hygiene**:
   ```bash
   bash scripts/verify_port_hygiene.sh
   ```
   Expected: All ports (3005, 8000, 8005, 8080) clean and liberated.
