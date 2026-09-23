# Exhaustive Code Review Analysis: API, Lifecycle & Frontend Layers

**Reviewer**: Explorer 3 (API, Lifecycle & Frontend Specialist)  
**Date**: 2026-09-23  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_api_lifecycle_frontend/`  
**Target Codebase**: `AutonomousDayTrader` (`backend/app/main.py`, `backend/app/config.py`, `backend/app/ingestion/`, `backend/app/core/`, `frontend/`)

---

## Executive Summary

An exhaustive code review was conducted targeting:
1. **API & Lifecycle Layer**: FastAPI route handlers, request validation, error responses, WebSocket streaming (`/ws/ui`), connection management, slow consumer handling, serialization, session lifecycle (startup/shutdown, background tasks, graceful cancellation, clean exit), and process/port hygiene (ports 8000, 8005, 8080, 3005).
2. **Frontend & UI Layer**: Next.js 15 / React 19 components (`Header`, `StrategyCard`, `StrategyCarousel`, `ActivePositionTray`, `LiveChart`, `ManualControls`, `ExecutionLog`, `TradeHistory`), WebSocket client hook (`useTradingStream.ts`), auto-reconnect, packet parsing, state synchronization, error boundaries, null/undefined safety, and responsive layout constraints.

The audit identified **12 findings**:
- **1 CRITICAL finding**: Unthrottled quote broadcast storm triggering slow-consumer event loop blocking and ingestion queue overflow.
- **6 MAJOR findings**: Emergency manual flatten failing to cancel pending entry orders; unhandled ValueError in `POST /api/orders` causing HTTP 500 crashes; connected WebSocket clients not cleanly closed on shutdown; "Flatten All Portfolios" confirmation completely unreachable when no position is open; complete lack of React Error Boundaries with unsafe `.toFixed()` calls crashing the UI; and state desynchronization on WebSocket disconnection.
- **5 MINOR findings**: Port 8000 omitted from `verify_port_hygiene.sh`; unbounded memory growth in `engine.audit_log` and `engine.orders`; obsolete hardcoded 1.5R/2.5R target strings; mobile drag gesture conflict in `ActivePositionTray`; and inconsistent API base URL resolution in `TradeHistory.tsx`.

---

## Detailed Code Audit by Layer

### 1. API & Lifecycle Layer (`backend/app/main.py`, `backend/app/api/`)

#### 1.1 WebSocket Streaming & Slow Consumer Handling
- **Location**: `backend/app/main.py:776-843`, `backend/app/main.py:1096-1138`
- **Mechanism**:
  - `handle_quote_event(quote: QuoteEvent)` is invoked whenever the `StockWebSocketClient` processes an NBBO quote frame. At line 1137, it unconditionally invokes `await broadcast_ui_state()`.
  - In liquid mega-cap equities (SPY, QQQ, AAPL, NVDA, TSLA), quotes stream continuously at 50 to 500+ quotes per second during market hours.
  - `broadcast_ui_state()` iterates sequentially over `list(ui_clients)`:
    ```python
    raw = json.dumps(payload, default=str)
    for ws in list(ui_clients):
        try:
            await ws.send_text(raw)
        except Exception:
            ui_clients.discard(ws)
    ```
  - `await ws.send_text(raw)` is unconstrained by any timeout (`asyncio.wait_for`). If a client is on a high-latency connection, mobile cellular link, or has suspended a browser tab (causing TCP socket write buffer saturation), `ws.send_text()` blocks execution of the single asyncio thread.
  - Because `handle_quote_event` is called directly by `StockWS_QueueWorker`, blocking `broadcast_ui_state()` starves the entire market data event loop. `StockWebSocketClient._queue` rapidly reaches `QUEUE_HIGH_WATERMARK_PCT` (80%) and `QUEUE_MAX_SIZE` (10,000 items), causing incoming bars, quotes, and trades to be dropped (`self.dropped_messages += 1`).
  - Concurrently, broadcasting full state payloads at hundreds of Hertz inundates the Next.js client, causing massive JSON parsing overhead and rendering thrash.

#### 1.2 Manual Flatten & Order Cancellation Gaps
- **Location**: `backend/app/main.py:1806-1848`
- **Mechanism**:
  - `_execute_manual_flatten(target_symbols, now_dt, event_key)` is responsible for handling `POST /api/flatten` and UI WebSocket action `FLATTEN_ALL` / `FLATTEN_POSITION`.
  - The function only queries `pos = account.positions.get(sym)`:
    ```python
    for sym in target_symbols:
        pos = account.positions.get(sym)
        if pos:
            ...
            order = engine.create_order(...)
            engine.submit_order(order.id)
            fills = _flatten_symbol(sym, pos.market_price, now_dt)
            _reconcile_fills(fills)
            flattened.append(sym)
    ```
  - If a symbol has a working entry order in `engine.working_orders` that has not yet filled (`pos` is `None`), this loop completely ignores it.
  - Furthermore, if `FLATTEN_ALL` is requested, `target_symbols` defaults to `list(account.positions.keys())`. Any pending entry order for a symbol not yet in `account.positions` is ignored.
  - As a result, working orders remain live in `engine.working_orders`. When market prices subsequently reach the limit/stop price, the order executes and opens a new position *after* the operator issued a flatten command.

#### 1.3 Request Validation & Error Handling in `POST /api/orders`
- **Location**: `backend/app/main.py:1709-1789`, `backend/app/core/engine.py:136-141`
- **Mechanism**:
  - `OrderCreateRequest` defines:
    ```python
    class OrderCreateRequest(BaseModel):
        symbol: str
        side: str
        order_type: str
        qty: int
        limit_price: Optional[float] = None
        stop_price: Optional[float] = None
        strategy_id: str = "MANUAL"
    ```
  - There is no constraint on `qty` (e.g. `Field(gt=0)`). If an API client submits `qty: 0` or `qty: -10`:
    - `account.can_afford` evaluates `qty * est_price <= 0` and approves the order.
    - `risk_engine.evaluate_order_request` does `final_shares = min(requested_qty, max_allowed_shares) = requested_qty` and approves the order.
    - When `engine.create_order()` is called at line 1758:
      ```python
      if qty <= 0:
          raise ValueError(f"Order quantity must be positive, got {qty}")
      ```
    - Because `engine.create_order` is not wrapped in `try...except ValueError`, this raises an unhandled exception in FastAPI, returning an HTTP 500 Internal Server Error.
    - Similarly, submitting `LIMIT` without `limit_price` or `STOP` without `stop_price` triggers unhandled `ValueError` at `engine.py:138,140`, returning HTTP 500 instead of HTTP 400.

#### 1.4 Application Lifespan & Graceful WebSocket Teardown
- **Location**: `backend/app/main.py:1425-1456`
- **Mechanism**:
  - In `lifespan(app)`, shutdown stops relay clients (`stock_ws_client`, `news_ws_client`, `vix_client`), cancels `runtime_tasks`, and saves a final durable checkpoint.
  - However, connected `ui_clients` (`Set[WebSocket]`) are not closed. The server exits without sending WebSocket frame 1001 ("Server shutting down").
  - Consequently, client sockets remain open until uvicorn terminates, leading to unclean disconnects and potential socket state issues.

#### 1.5 Process Hygiene & Port Allocation
- **Location**: `scripts/verify_port_hygiene.sh:6`, `scripts/run_dev.sh:16`
- **Mechanism**:
  - `verify_port_hygiene.sh` audits `PORTS=(3005 8005 8080)`. It omits port 8000, which is the system's baseline conflict port (allocated to MarketCards on host).
  - In `run_dev.sh`, `kill $(jobs -p)` is used for cleanup. On macOS, `npm run dev` spawns child Node processes for Next.js. Killing the parent `npm` process does not reliably signal child Node processes, potentially leaving Next.js listening on port 3005 as an orphaned process.

---

### 2. Frontend & UI Layer (`frontend/`)

#### 2.1 Absence of React Error Boundaries & Unhandled Exceptions
- **Location**: `frontend/app/page.tsx`, `frontend/app/layout.tsx`, `frontend/components/`
- **Mechanism**:
  - There is zero Error Boundary implementation (`error.tsx`, `global-error.tsx`, or `<ErrorBoundary>`) in the Next.js app.
  - Components invoke `.toFixed()` directly on data attributes without verifying numerical existence:
    - `LiveChart.tsx:92`: `position.market_price.toFixed(2)`
    - `ActivePositionTray.tsx:95`: `position.entry_price.toFixed(2)` and `position.market_price.toFixed(2)`
    - `ActivePositionTray.tsx:115`: `Math.abs(position.unrealized_pnl).toFixed(2)`
    - `ActivePositionTray.tsx:237`: `position.market_value.toLocaleString()`
    - `ManualControls.tsx:167`: `position.market_price.toFixed(2)`
  - If a position object contains `undefined` for any of these fields (e.g. during state transitions or initial connection), JavaScript raises an unhandled `TypeError: Cannot read properties of undefined (reading 'toFixed')`.
  - In React 19 / Next.js 15, unhandled render errors unmount the entire component tree, causing the application to crash to a blank error screen.

#### 2.2 Broken "Flatten All Portfolios" Dialog in `ManualControls.tsx`
- **Location**: `frontend/components/ManualControls.tsx:93-108`, `frontend/components/ManualControls.tsx:192-220`
- **Mechanism**:
  - When `position === null`, `ManualControls` returns early at line 94:
    ```tsx
    if (!position) {
      return (
        <div className="p-4 rounded-2xl bg-white/[0.03] border border-white/[0.06] text-center">
          <span className="text-xs text-neutral-400">No active positions to control</span>
          <div className="mt-2">
            <button
              onClick={() => setConfirmFlattenAll(true)}
              disabled={!isConnected}
              className="..."
            >
              Flatten All Portfolios
            </button>
          </div>
        </div>
      );
    }
    ```
  - Clicking this button sets `setConfirmFlattenAll(true)`. However, the JSX that renders the confirmation modal (`{confirmFlattenAll ? ( ... ) : ...}`) resides in the second return statement at line 201.
  - When `position === null`, lines 110-223 are never reached. Setting `confirmFlattenAll` triggers a re-render of lines 94-107, which completely ignores `confirmFlattenAll`.
  - The confirmation dialog never appears, making the button completely unresponsive.

#### 2.3 State Desynchronization on WebSocket Disconnection
- **Location**: `frontend/hooks/useTradingStream.ts:249-275`, `frontend/hooks/useTradingStream.ts:295-304`
- **Mechanism**:
  - When the WebSocket connection drops, `useTradingStream` sets up a 5-second polling interval:
    ```typescript
    const res = await fetch(`${httpBase}/api/audit?limit=10`);
    ```
  - It polls ONLY `/api/audit`. It never queries `/api/account` or `/api/positions`.
  - If the user executes an emergency flatten while disconnected (which dispatches via `fetch(`${httpBase}/api/flatten`)`), the backend flattens the account, but the UI state is never updated.
  - The UI continues to show the old open position and unrealized PnL in the bottom tray indefinitely.
  - Furthermore, in `useTradingStream.ts:175`, `shares: first.shares || first.qty || 100` uses a synthetic `100` share fallback if `shares` is falsy or 0.

#### 2.4 Synchronous WebSocket Instantiation Failure Halts Reconnection
- **Location**: `frontend/hooks/useTradingStream.ts:139-242`
- **Mechanism**:
  - In `connect()`:
    ```typescript
    try {
      const { resolvedWsUrl } = getResolvedEndpoints();
      const ws = new WebSocket(resolvedWsUrl);
      ...
    } catch (err: any) {
      setLastError(err?.message || "Failed to initialize WebSocket");
    }
    ```
  - If `new WebSocket()` throws synchronously (e.g. invalid URL syntax, Content Security Policy block, or device offline), execution jumps to `catch`.
  - The `catch` block sets `lastError` but does NOT schedule `reconnectTimeoutRef`.
  - The client permanently ceases reconnection attempts until manual browser refresh.

#### 2.5 Obsolete Hardcoded 1.5R / 2.5R Target Strings
- **Location**: `frontend/components/LiveChart.tsx:302,305`, `frontend/components/StrategyCarousel.tsx:124`
- **Mechanism**:
  - Iteration 2 restructured bracket geometry to Target 1 at 0.80R (50% scale-out) and Target 2 at 1.80R runner.
  - The UI components hardcode legacy strings:
    - `LiveChart.tsx:302`: `Target 1 (1.5R)`
    - `LiveChart.tsx:305`: `Target 2 (2.5R)`
    - `StrategyCarousel.tsx:124`: `1.5R Scale / 2.5R Trail`
  - This displays inaccurate target multiples to the trader.

#### 2.6 Mobile Layout & Gesture Usability Constraints
- **Location**: `frontend/components/ActivePositionTray.tsx:163`, `frontend/components/LiveChart.tsx:104`
- **Mechanism**:
  - `ActivePositionTray.tsx` applies `drag="y"` to the entire modal dialog container rather than attaching `useDragControls` strictly to the top grab handle. On mobile viewports (390x844), scrolling internal content (such as `ExecutionLog`) can trigger drag gestures that dismiss the modal.
  - `LiveChart.tsx` applies `preserveAspectRatio="none"` to the candlestick SVG. On narrow mobile screens, horizontal compression causes non-uniform distortion of candlesticks and turns circle indicators into ellipses.

---

## Complete Findings Catalog

| ID | Layer | Location | Severity | Title |
|---|---|---|---|---|
| **F-01** | API / Streaming | `backend/app/main.py:776-843, 1137` | **CRITICAL** | Unthrottled Quote Broadcast Storm & Slow-Consumer Event Loop Blocking |
| **F-02** | API / Execution | `backend/app/main.py:1806-1848` | **MAJOR** | Manual Flatten Fails to Cancel Pending Entry Orders |
| **F-03** | API / Routes | `backend/app/main.py:1709-1789`, `engine.py:136-141` | **MAJOR** | Unhandled `ValueError` in `POST /api/orders` on Invalid Qty or Missing Price |
| **F-04** | API / Lifecycle | `backend/app/main.py:1425-1456` | **MAJOR** | Connected WebSocket Clients Not Closed on Lifespan Teardown |
| **F-05** | Frontend / UI | `frontend/components/ManualControls.tsx:93-108, 192-220` | **MAJOR** | "Flatten All Portfolios" Confirmation Unreachable When Position is Null |
| **F-06** | Frontend / UI | `frontend/app/page.tsx`, `frontend/components/` | **MAJOR** | Missing React Error Boundaries & Unsafe `.toFixed()` Calls Triggering Crashes |
| **F-07** | Frontend / Hook | `frontend/hooks/useTradingStream.ts:227-242, 249-275` | **MAJOR** | Disconnected State Desync & Permanent WebSocket Failure on Exception |
| **F-08** | API / Hygiene | `scripts/verify_port_hygiene.sh:6` | **MINOR** | Port 8000 Omitted from Port Hygiene Audit Script |
| **F-09** | Backend / Memory | `backend/app/core/engine.py:118, 479`, `main.py:695-774` | **MINOR** | Unbounded Growth of `engine.audit_log` and `engine.orders` |
| **F-10** | Frontend / UI | `frontend/components/LiveChart.tsx:302, 305`, `StrategyCarousel.tsx:124` | **MINOR** | Obsolete Hardcoded 1.5R / 2.5R Target Strings in UI Components |
| **F-11** | Frontend / Layout | `frontend/components/ActivePositionTray.tsx:163`, `LiveChart.tsx:104` | **MINOR** | Modal Drag Gesture Conflicts with Mobile Scrolling & SVG Distortion |
| **F-12** | Frontend / UI | `frontend/components/TradeHistory.tsx:28-34` | **MINOR** | Inconsistent API Base URL Resolution in `TradeHistory.tsx` |

---

## Detailed Findings & Remediation Specifications

### [CRITICAL] F-01: Unthrottled Quote Broadcast Storm & Slow-Consumer Event Loop Blocking
- **File**: `backend/app/main.py:776-843`, `backend/app/main.py:1137`
- **Description**: `handle_quote_event` executes `await broadcast_ui_state()` on every quote print. Quotes arrive at hundreds of Hertz. In `broadcast_ui_state`, each connected client in `ui_clients` is sent a full serialized JSON snapshot sequentially without timeout.
- **Impact**: Slow WebSocket clients block the single-threaded asyncio event loop. Queue buffers overflow, dropping live market data. High-frequency updates throttle client browser CPU.
- **Concrete Remediation**:
  1. Decouple quote events from UI broadcasts. In `handle_quote_event`, do NOT call `await broadcast_ui_state()`.
  2. Implement an asynchronous rate-limiter / debouncer for UI updates:
     ```python
     _last_broadcast_time: float = 0.0
     UI_BROADCAST_THROTTLE_SEC: float = 0.250  # 4 Hz max

     async def maybe_broadcast_ui_state(force: bool = False) -> None:
         global _last_broadcast_time
         now = time.monotonic()
         if not force and (now - _last_broadcast_time < UI_BROADCAST_THROTTLE_SEC):
             return
         _last_broadcast_time = now
         await broadcast_ui_state()
     ```
  3. In `broadcast_ui_state()`, send concurrently with timeout:
     ```python
     async def _send_client(ws: WebSocket, text: str) -> None:
         try:
             await asyncio.wait_for(ws.send_text(text), timeout=0.3)
         except Exception:
             ui_clients.discard(ws)

     await asyncio.gather(*[_send_client(ws, raw) for ws in list(ui_clients)], return_exceptions=True)
     ```

---

### [MAJOR] F-02: Manual Flatten Fails to Cancel Pending Entry Orders
- **File**: `backend/app/main.py:1806-1848`
- **Description**: `_execute_manual_flatten` only liquidates positions present in `account.positions`. Working entry orders (limit or stop orders that have not yet executed) are left active in `engine.working_orders`.
- **Impact**: Unfilled entry orders can execute after the operator flattens the account, creating unexpected open positions and violating risk intent.
- **Concrete Remediation**:
  In `_execute_manual_flatten()`, before or during liquidation, cancel all working orders matching `target_symbols` (or all working orders if `target_symbols` covers all positions/empty):
  ```python
  targets_set = set(target_symbols) if target_symbols else None
  for oid, order in list(engine.working_orders.items()):
      if targets_set is None or order.symbol in targets_set:
          try:
              engine.cancel_order(oid, reason="MANUAL_FLATTEN")
          except Exception as exc:
              log.warning("Could not cancel working order %s during flatten: %s", oid, exc)
  _release_dead_entry_brackets()
  ```

---

### [MAJOR] F-03: Unhandled ValueError in `POST /api/orders` (HTTP 500)
- **File**: `backend/app/main.py:1709-1789`, `backend/app/core/engine.py:136-141`
- **Description**: `OrderCreateRequest` lacks `qty > 0` validation, and `submit_order` does not validate required prices for `LIMIT` / `STOP` orders. When `engine.create_order` raises `ValueError`, FastAPI unhandled exception middleware returns HTTP 500.
- **Impact**: Invalid client requests crash the handler with an HTTP 500 traceback instead of a clean HTTP 400 error.
- **Concrete Remediation**:
  1. Add Pydantic field validation in `OrderCreateRequest`:
     ```python
     class OrderCreateRequest(BaseModel):
         symbol: str
         side: str
         order_type: str
         qty: int = Field(gt=0, description="Quantity must be strictly positive")
         limit_price: Optional[float] = Field(default=None, gt=0)
         stop_price: Optional[float] = Field(default=None, gt=0)
         strategy_id: str = "MANUAL"
     ```
  2. In `submit_order`, wrap order creation:
     ```python
     try:
         order = engine.create_order(...)
     except ValueError as exc:
         raise HTTPException(status_code=400, detail=str(exc))
     ```

---

### [MAJOR] F-04: Connected WebSocket Clients Not Closed on Lifespan Teardown
- **File**: `backend/app/main.py:1425-1456`
- **Description**: During application shutdown in `lifespan(app)`, `ui_clients` are not sent a WebSocket close frame or closed gracefully.
- **Impact**: Abrupt TCP termination without standard WebSocket close code (1001), causing socket linger and potential reconnect issues on restart.
- **Concrete Remediation**:
  In `lifespan(app)` teardown block:
  ```python
  for ws in list(ui_clients):
      try:
          await ws.close(code=1001, reason="Server shutting down")
      except Exception:
          pass
  ui_clients.clear()
  ```

---

### [MAJOR] F-05: "Flatten All Portfolios" Confirmation Unreachable When Position is Null
- **File**: `frontend/components/ManualControls.tsx:93-108`, `frontend/components/ManualControls.tsx:192-220`
- **Description**: When `position === null`, `ManualControls` returns early at line 94. It renders a button calling `setConfirmFlattenAll(true)`, but the confirmation dialog JSX is located in the second return statement (line 201), which is unreachable when `position === null`.
- **Impact**: Operators cannot purge working orders or execute portfolio flattens when no primary position is displayed.
- **Concrete Remediation**:
  In `ManualControls.tsx`, render the confirmation prompt when `confirmFlattenAll` is true regardless of whether `position` is null:
  ```tsx
  if (!position) {
    return (
      <div className="p-4 rounded-2xl bg-white/[0.03] border border-white/[0.06] text-center space-y-2">
        <span className="text-xs text-neutral-400">No active positions to control</span>
        {!confirmFlattenAll ? (
          <div>
            <button
              onClick={() => setConfirmFlattenAll(true)}
              disabled={!isConnected}
              className="px-4 py-2 rounded-xl bg-white/[0.05] hover:bg-apple-red/20 text-neutral-400 hover:text-apple-red border border-white/10 text-xs font-semibold transition-colors disabled:opacity-40 disabled:pointer-events-none"
            >
              Flatten All Portfolios
            </button>
          </div>
        ) : (
          <div className="p-3 rounded-2xl bg-neutral-900 border border-apple-red/40 space-y-2">
            <div className="text-[11px] text-neutral-300 text-center">
              Purge working brackets and market-liquidate all open positions?
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleExecuteFlattenAll}
                className="flex-1 py-1.5 rounded-xl bg-apple-red text-white text-[11px] font-bold"
              >
                Purge All
              </button>
              <button
                onClick={() => setConfirmFlattenAll(false)}
                className="flex-1 py-1.5 rounded-xl bg-white/10 text-neutral-400 text-[11px]"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }
  ```

---

### [MAJOR] F-06: Missing React Error Boundaries & Unsafe `.toFixed()` Calls
- **File**: `frontend/app/page.tsx`, `frontend/components/LiveChart.tsx:92`, `frontend/components/ActivePositionTray.tsx:95, 115`, `frontend/components/ManualControls.tsx:167`
- **Description**: Zero Error Boundaries exist across the frontend. Calling `.toFixed()` on undefined values during transient data feeds causes unhandled JavaScript exceptions that unmount the entire Next.js page.
- **Impact**: Any data anomaly completely crashes the user interface, blacking out telemetry and emergency controls.
- **Concrete Remediation**:
  1. Implement `frontend/app/error.tsx`:
     ```tsx
     "use client";
     export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
       return (
         <div className="min-h-screen bg-black text-white flex flex-col items-center justify-center p-6 text-center space-y-4">
           <h2 className="text-xl font-bold text-apple-red">Trading Terminal Error</h2>
           <p className="text-xs text-neutral-400 max-w-md">{error.message}</p>
           <button onClick={() => reset()} className="px-4 py-2 bg-apple-purple rounded-xl text-xs font-bold">
             Reset Interface
           </button>
         </div>
       );
     }
     ```
  2. Implement a defensive formatting utility (`frontend/utils/format.ts`):
     ```typescript
     export function safeFixed(value: number | null | undefined, digits: number = 2): string {
       if (value === null || value === undefined || isNaN(value)) return "—";
       return value.toFixed(digits);
     }
     ```
  3. Replace all direct `.toFixed()` calls on nullable fields with `safeFixed`.

---

### [MAJOR] F-07: Disconnected State Desync & Reconnect Exception Halting
- **File**: `frontend/hooks/useTradingStream.ts:227-242, 249-275`
- **Description**:
  1. The disconnected polling loop only polls `/api/audit?limit=10`. It omits `/api/account` and `/api/positions`. If an operator triggers a REST flatten while disconnected, the UI retains stale position and PnL indefinitely.
  2. If `new WebSocket(resolvedWsUrl)` throws synchronously in `connect()`, the `catch` block does not schedule a retry, halting reconnection permanently.
- **Impact**: UI desync from actual paper account balances and dead WebSocket loops.
- **Concrete Remediation**:
  1. In the polling fallback of `useTradingStream.ts`, query `/api/account` and `/api/positions` as well:
     ```typescript
     const [auditRes, acctRes, posRes] = await Promise.all([
       fetch(`${httpBase}/api/audit?limit=10`),
       fetch(`${httpBase}/api/account`),
       fetch(`${httpBase}/api/positions`),
     ]);
     ```
  2. In `connect()` catch block:
     ```typescript
     } catch (err: any) {
       setLastError(err?.message || "Failed to initialize WebSocket");
       const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 10000);
       reconnectAttemptRef.current += 1;
       reconnectTimeoutRef.current = setTimeout(connect, delay);
     }
     ```
  3. Replace `shares: first.shares || first.qty || 100` with `first.shares ?? first.qty ?? 0`.

---

### [MINOR] F-08: Port 8000 Omitted from Port Hygiene Audit Script
- **File**: `scripts/verify_port_hygiene.sh:6`
- **Description**: Script checks `PORTS=(3005 8005 8080)` but omits 8000.
- **Impact**: Potential collision with host service on port 8000 goes undetected.
- **Concrete Remediation**: Update line 6 to: `PORTS=(3005 8000 8005 8080)`.

---

### [MINOR] F-09: Unbounded Growth of `engine.audit_log` and `engine.orders`
- **File**: `backend/app/core/engine.py:118, 479`, `backend/app/main.py:695-774`
- **Description**: Order audit records and completed order objects accumulate in memory indefinitely.
- **Impact**: Monotonic memory leak in continuous multi-week production deployments.
- **Concrete Remediation**:
  In `_check_session_boundary()`, prune completed orders and trim `engine.audit_log = engine.audit_log[-5000:]`.

---

### [MINOR] F-10: Obsolete Hardcoded 1.5R / 2.5R Target Strings
- **File**: `frontend/components/LiveChart.tsx:302, 305`, `frontend/components/StrategyCarousel.tsx:124`
- **Description**: UI displays legacy 1.5R/2.5R strings instead of calibrated 0.80R/1.80R geometry.
- **Impact**: Visual discrepancy between UI labels and actual algorithmic execution.
- **Concrete Remediation**: Update labels to `Target 1 (0.80R)` and `Target 2 (1.80R)`.

---

### [MINOR] F-11: Modal Drag Gesture Conflicts with Mobile Scrolling & SVG Distortion
- **File**: `frontend/components/ActivePositionTray.tsx:163`, `frontend/components/LiveChart.tsx:104`
- **Description**: `drag="y"` on outer modal intercepts vertical scrolls. `preserveAspectRatio="none"` causes non-uniform stretching on narrow mobile screens.
- **Impact**: Unintended sheet dismissals and visual distortion on mobile viewports.
- **Concrete Remediation**: Attach `useDragControls` exclusively to the grab bar handle, and adjust SVG viewBox scaling.

---

### [MINOR] F-12: Inconsistent API Base URL Resolution in `TradeHistory.tsx`
- **File**: `frontend/components/TradeHistory.tsx:28-34`
- **Description**: Hardcodes `window.location.port === "3005"` instead of using the shared resolution logic from `useTradingStream.ts`.
- **Impact**: Network failures when accessing trade history across non-standard ports or reverse proxies.
- **Concrete Remediation**: Export and reuse a common `getResolvedEndpoints().httpBase` utility.

---
