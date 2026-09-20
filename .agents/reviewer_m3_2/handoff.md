# Milestone 3 Review & Adversarial Audit Report: UI WebSocket Streaming & State Synchronization

**Reviewer**: `reviewer_m3_2` (UI WebSocket Streaming & State Synchronization Reviewer)  
**Date**: 2026-09-20  
**Milestone**: Milestone 3 (`ui_mobile_streaming`)  
**Verdict**: **REQUEST_CHANGES**  
**Parent Orchestrator Conversation ID**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  

---

## 1. Observation

### 1.1 Test Suite & Build Verification
1. **Frontend Architecture Verification (`npm test` in `frontend/`)**:
   - Exit code: `0`
   - Output:
     ```
     > autonomous-day-trader-ui@1.0.0 test
     > node scripts/verify_ui.mjs

     🔍 Verifying Apple Music Mobile UI Architecture...
       ✅ Verified package.json (718 bytes)
       ✅ Verified tsconfig.json (598 bytes)
       ✅ Verified tailwind.config.js (779 bytes)
       ✅ Verified postcss.config.js (83 bytes)
       ✅ Verified app/layout.tsx (864 bytes)
       ✅ Verified app/page.tsx (4828 bytes)
       ✅ Verified app/globals.css (1485 bytes)
       ✅ Verified types/trading.ts (1682 bytes)
       ✅ Verified hooks/useTradingStream.ts (10307 bytes)
       ✅ Verified components/AmbientBackground.tsx (3573 bytes)
       ✅ Verified components/Header.tsx (7335 bytes)
       ✅ Verified components/StrategyCard.tsx (7390 bytes)
       ✅ Verified components/StrategyCarousel.tsx (7018 bytes)
       ✅ Verified components/NowPlayingTray.tsx (11829 bytes)
       ✅ Verified components/LiveChart.tsx (11334 bytes)
       ✅ Verified components/ManualControls.tsx (6724 bytes)
       ✅ Verified components/ExecutionLog.tsx (4247 bytes)
       ✅ Verified Tailwind design tokens and Apple palette
       ✅ Verified CSS glassmorphism & Apple typographic rules
       ✅ Verified Apple Music spring physics (stiffness: 350, damping: 32)
       ✅ Verified WebSocket client actions & port 8005 synchronization
       ✅ Verified all 4 strategy album cards (ORB, VWAP, News, Mean Reversion)
       ✅ Verified UI safe port 3005 allocation (avoiding host port 3000 collision)

     🎉 All Apple Music UI architectural checks PASSED!
     ```
2. **Next.js Production Build (`npm run build` in `frontend/`)**:
   - Exit code: `0`
   - Output: Compiled successfully in 932ms, 0 TypeScript errors, 4/4 static pages generated.
3. **Backend Test Suite (`pytest backend/tests/ -v`)**:
   - Exit code: `0` (140 passed, 3 warnings in 0.69s).
4. **E2E Test Runner (`python3 tests/e2e/runner.py`)**:
   - Exit code: `0` (248 passed in 0.26s).
5. **Process Hygiene & Port Audit**:
   - Command `lsof -iTCP:3005,8005,8080 -sTCP:LISTEN` returned exit code 1 with empty output.
   - Script `bash scripts/verify_port_hygiene.sh` passed:
     ```
     🔍 Auditing port hygiene across project ports: 3005 8005 8080...
     ✅ Port 3005 is clean and liberated.
     ✅ Port 8005 is clean and liberated.
     ✅ Port 8080 is clean and liberated.
     ✨ All ports verified clean. Zero lingering daemons.
     ```

### 1.2 Direct Code Observations & Behavioral Defects

#### Observation O1: `TIGHTEN_STOP` Discards Modification Directive for `engine.working_orders`
- **Location**: `backend/app/main.py`, lines 630–635:
  ```python
  630: elif action == "TIGHTEN_STOP":
  631:     sym = msg.get("symbol", "").upper()
  632:     new_stop = float(msg.get("new_stop", 0.0))
  633:     bracket_manager.manual_tighten_stop(sym, new_stop)
  634:     await broadcast_ui_state()
  ```
- **Contrasting pattern in `handle_bar_event` (`backend/app/main.py`, lines 326–332)**:
  ```python
  326: bracket_dir = bracket_manager.update_trailing_stop(...)
  327: if bracket_dir and bracket_dir.orders_to_modify:
  328:     for mod in bracket_dir.orders_to_modify:
  329:         oid = mod["order_id"]
  330:         if oid in engine.working_orders:
  331:             engine.working_orders[oid].stop_price = mod["new_stop_price"]
  ```
- **Behavioral Confirmation**:
  When a working stop order exists in `engine.working_orders` with initial stop $145.00 and the user triggers `TIGHTEN_STOP` to $150.00 via WebSocket:
  - `bracket_manager.brackets[bracket_id].current_stop_price` becomes `150.0`.
  - `engine.working_orders[order.id].stop_price` **remains 145.0**.
  - `broadcast_ui_state()` publishes `stop_loss: 150.0` to the frontend UI, but the execution engine order book still has the stop trigger level at $145.00.

#### Observation O2: `recent_activity` Omitted from `broadcast_ui_state()` WebSocket Contract
- **Location**: `backend/app/main.py`, lines 166–188:
  ```python
  166: payload = {
  167:     "type": "STATE_UPDATE",
  168:     "timestamp": datetime.now(timezone.utc).isoformat(),
  169:     "account": {...},
  170:     "market_context": adaptation_engine.get_market_context(),
  171:     "strategies": [s.to_dict() for s in strategies],
  172:     "primary_position": primary_pos,
  173:     "all_positions": [p.__dict__ for p in snapshot.positions.values()],
  174:     "positions_count": len(account.positions),
  175:     "working_orders_count": len(engine.working_orders),
  176: }
  ```
- **Contrasting contract specification in `PROJECT.md` line 170**:
  ```json
  "all_positions": [...],
  "recent_activity": [...]
  ```
- **Consumer in `frontend/hooks/useTradingStream.ts` lines 204 & 242–244**:
  ```typescript
  204: recent_activity: payload.recent_activity || prev.recent_activity,
  ...
  242: const pollInterval = setInterval(async () => {
  243:   if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
  ```
- **Behavioral Confirmation**:
  When WebSocket is connected (`readyState === WebSocket.OPEN`), fallback HTTP polling for `/api/audit` is disabled. Because `payload.recent_activity` is `undefined`, the frontend never updates `recent_activity`. The `ExecutionLog.tsx` component remains permanently stuck on the 3 hardcoded sample records from `INITIAL_STATE` (`NVDA filled @ $124.50`).

#### Observation O3: `ManualControls.tsx` "Trail +50% Gain" Inverted on SHORT Positions
- **Location**: `frontend/components/ManualControls.tsx`, lines 37–43:
  ```typescript
  37: const handleTightenHalfProfit = () => {
  38:   if (!position) return;
  39:   const profitBuffer = (position.market_price - position.entry_price) * 0.5;
  40:   const newStop = Number((position.entry_price + Math.max(0, profitBuffer)).toFixed(2));
  41:   onTightenStop(position.symbol, newStop);
  42:   showFeedback(`Stop tightened to +50% profit lock ($${newStop.toFixed(2)})`);
  43: };
  ```
- **Behavioral Confirmation**:
  If a trader holds a SHORT position with `entry_price` = $150.00 and `market_price` = $140.00 (+ $10.00 profit):
  - `profitBuffer` is `(140.00 - 150.00) * 0.5 = -5.00`.
  - `Math.max(0, -5.00)` is `0`.
  - `newStop` becomes `150.00` (breakeven instead of locking $5.00 profit at $145.00).
  - If SHORT position is in loss (e.g. `market_price` = $155.00), `profitBuffer` is `+2.50`, computing `newStop = 152.50` (advancing stop against the short trade).

---

## 2. Logic Chain

1. **Premise 1 (Execution Safety)**: In an algorithmic trading platform, manual tactical interventions (such as tightening stops) displayed to the operator must be identically enforced by the matching engine.
2. **From Observation O1**: `ui_websocket_endpoint` in `backend/app/main.py` updates the bracket metadata in `bracket_manager`, but ignores the returned `BracketUpdateDirective` and never updates `engine.working_orders[oid].stop_price`. The UI displays a tightened stop (e.g., $150.00), while the execution engine's matching logic in `process_bar` and `process_quote` will only trigger the stop order at the old wider price (e.g., $145.00). If the price drops to $148.00, the position is not stopped out as the operator was led to believe. This is a critical execution and risk violation.
3. **Premise 2 (Contract Completeness)**: The UI Execution Log is required by R3 / PROJECT.md §4 to reflect real-time order lifecycle events and bracket adjustments streamed over WebSocket without manual page refresh.
4. **From Observation O2**: `broadcast_ui_state()` does not serialize or broadcast `recent_activity`. The frontend hook `useTradingStream.ts` relies on `payload.recent_activity`, and disables fallback polling whenever the WebSocket is active. Therefore, during normal live operation, the execution audit trail never receives any events from the trading session.
5. **From Observation O3**: The trading engine supports both LONG and SHORT positions (ORB breakdowns and Mean Reversion fades generate SHORT orders). In `ManualControls.tsx`, `handleTightenHalfProfit` uses a formula that assumes long trades exclusively, leading to erroneous stop calculations for short trades.
6. **Integrity Audit**:
   - Tests in `tests/e2e/test_tier1_features.py` (e.g. `test_f16_now_playing_action_flatten_position`, `test_f17_bva_ui_command_unknown_action_handled`) test static command dictionaries and payload validators rather than end-to-end order state mutation in `engine.working_orders`.
   - While no malicious obfuscation or fabricated attestation was observed, the existing tests self-certified UI command dispatch schemas without checking actual matching engine order mutation.
7. **Synthesis**: The WebSocket connection and UI component architecture are high-fidelity, well-structured, and performant; however, the state synchronization gaps in stop tightening, audit streaming, and short order stop calculations require immediate remediation.

---

## 3. Caveats

1. **Headless Environment**: Visual inspection of CSS backdrop filters and Framer Motion spring physics was conducted via code AST, token validation, and Next.js static production builds; actual GPU-accelerated frame rates require a mobile browser WebKit viewport.
2. **Existing Passing Tests**: All 140 backend tests and 248 E2E tests currently pass with 0 failures because the unit tests test `bracket_manager` and `engine` in isolation rather than testing the WebSocket action handler's integration with `engine.working_orders`.

---

## 4. Conclusion & Findings

### Verdict: REQUEST_CHANGES

### Findings

#### Finding 1 [Critical]: `TIGHTEN_STOP` Action Does Not Update `engine.working_orders`
- **What**: Manual stop tightening via WebSocket modifies `bracket_manager`, but leaves the working stop order in `engine.working_orders` at the old stop price.
- **Where**: `/Users/mo/AutonomousDayTrader/backend/app/main.py`, lines 630–635.
- **Why**: Desynchronizes UI representation from order execution reality; risk stops fail to trigger at the tightened level.
- **Remediation**:
  In `ui_websocket_endpoint` of `backend/app/main.py`:
  ```python
  elif action == "TIGHTEN_STOP":
      sym = msg.get("symbol", "").upper()
      new_stop = float(msg.get("new_stop", 0.0))
      bracket_dir = bracket_manager.manual_tighten_stop(sym, new_stop)
      if bracket_dir and bracket_dir.orders_to_modify:
          for mod in bracket_dir.orders_to_modify:
              oid = mod["order_id"]
              if oid in engine.working_orders:
                  engine.working_orders[oid].stop_price = mod["new_stop_price"]
      await broadcast_ui_state()
  ```

#### Finding 2 [Major]: Missing `recent_activity` in `broadcast_ui_state()` Payload
- **What**: WebSocket state update does not include `recent_activity`, leaving `ExecutionLog` frozen on default mock records.
- **Where**: `/Users/mo/AutonomousDayTrader/backend/app/main.py`, lines 166–188.
- **Why**: Breaches PROJECT.md §4 WebSocket contract and breaks real-time UI audit trail streaming.
- **Remediation**:
  Add `recent_activity` to the `payload` dictionary in `broadcast_ui_state()`:
  ```python
  "recent_activity": [
      {
          "id": str(r.order_id),
          "timestamp": r.timestamp.strftime("%H:%M:%S") if hasattr(r.timestamp, "strftime") else str(r.timestamp),
          "type": r.event_trigger,
          "symbol": r.symbol,
          "message": f"{r.from_state} -> {r.to_state} ({r.reason})",
          "price": r.fill_price,
          "qty": r.fill_qty,
      }
      for r in engine.audit_log[-20:]
  ]
  ```

#### Finding 3 [Major]: `ManualControls.tsx` "Trail +50% Gain" Math Broken on SHORT Positions
- **What**: Profit buffer calculation in `handleTightenHalfProfit` does not account for SHORT position directionality.
- **Where**: `/Users/mo/AutonomousDayTrader/frontend/components/ManualControls.tsx`, lines 37–43.
- **Why**: Computes incorrect stop prices on short positions (locks at breakeven instead of +50% profit).
- **Remediation**:
  In `handleTightenHalfProfit`:
  ```typescript
  const handleTightenHalfProfit = () => {
    if (!position) return;
    const isShort = position.side === "SHORT";
    const profit = isShort
      ? (position.entry_price - position.market_price)
      : (position.market_price - position.entry_price);
    const profitBuffer = profit * 0.5;
    const newStop = isShort
      ? Number((position.entry_price - Math.max(0, profitBuffer)).toFixed(2))
      : Number((position.entry_price + Math.max(0, profitBuffer)).toFixed(2));
    onTightenStop(position.symbol, newStop);
    showFeedback(`Stop tightened to +50% profit lock ($${newStop.toFixed(2)})`);
  };
  ```

#### Finding 4 [Minor]: Fallback Endpoints in `useTradingStream.ts` Use Hardcoded Host
- **What**: Fallback polling and flatten calls use hardcoded `http://127.0.0.1:8005`.
- **Where**: `/Users/mo/AutonomousDayTrader/frontend/hooks/useTradingStream.ts`, lines 245, 288.
- **Why**: Reduces portability if connecting to alternate environments.
- **Remediation**: Derive HTTP origin dynamically from `wsUrl` (replacing `ws://` with `http://`).

---

## 5. Verification Method

To independently verify these findings and reproduce the exact behaviors:

1. **Verify Finding 1 (TIGHTEN_STOP Engine Order Desync)**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   python3 -c "
   from fastapi.testclient import TestClient
   from backend.app.main import app, account, engine, bracket_manager
   from backend.app.core.account import Position, PositionSide
   from backend.app.core.engine import OrderSide, OrderType

   order = engine.create_order(symbol='AAPL', side=OrderSide.SELL, order_type=OrderType.STOP, qty=100, stop_price=145.0)
   engine.submit_order(order.id)
   bracket = bracket_manager.create_bracket('brk_1', 'AAPL', 'LONG', 100, 150.0, 145.0)
   bracket.stop_order_id = order.id
   account.positions['AAPL'] = Position('AAPL', PositionSide.LONG, 100, 150.0, 152.0)

   client = TestClient(app)
   with client.websocket_connect('/ws/ui') as ws:
       _ = ws.receive_json()
       ws.send_json({'action': 'TIGHTEN_STOP', 'symbol': 'AAPL', 'new_stop': 150.0})
       _ = ws.receive_json()

   assert bracket.current_stop_price == 150.0
   # Fails because engine working order remains 145.0:
   assert engine.working_orders[order.id].stop_price == 150.0, f'Desync! working order stop is {engine.working_orders[order.id].stop_price}'
   "
   ```

2. **Verify Finding 2 (Missing `recent_activity` in WebSocket State)**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   python3 -c "
   from fastapi.testclient import TestClient
   from backend.app.main import app

   client = TestClient(app)
   with client.websocket_connect('/ws/ui') as ws:
       data = ws.receive_json()
       assert 'recent_activity' in data, 'recent_activity missing from WebSocket payload!'
   "
   ```

3. **Verify Standard Test Suites & Port Hygiene**:
   ```bash
   cd /Users/mo/AutonomousDayTrader/frontend && npm test
   cd /Users/mo/AutonomousDayTrader && pytest backend/tests/ -v
   cd /Users/mo/AutonomousDayTrader && python3 tests/e2e/runner.py
   bash scripts/verify_port_hygiene.sh
   ```
