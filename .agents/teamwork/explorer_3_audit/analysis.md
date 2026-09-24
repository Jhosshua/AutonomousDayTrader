# Forensic Audit Analysis Report: Blocking I/O, Async Loop Safety & External Calendar Fallbacks

**Author**: Explorer 3 (Forensic Explorer for Blocking I/O, Async Loop Safety & External Calendar Fallbacks)  
**Date**: 2026-09-24  
**Target Codebase**: `AutonomousDayTrader`  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_audit`

---

## 1. Executive Summary

A comprehensive forensic audit of the `AutonomousDayTrader` codebase was executed across asynchronous runtime coroutines, external market calendar services, margin and shared-account accounting, execution timing, and dry-run simulation infrastructure.

### Key Audit Findings:
1. **Critical Blocking I/O in Async Coroutine**: `backend/app/strategies/earnings_calendar.py` executes synchronous `urllib.request.urlopen()` inside `async def refresh_from_remote()`. Because `urllib.request` blocks the OS thread executing the single-threaded asyncio event loop, any slow network request, socket timeout (2.0s), or blocking DNS resolution (up to 30s) freezes the entire application: Uvicorn HTTP endpoints (`/health`) hang and trigger Railway container restarts; WebSocket connections drop from ping timeouts; and real-time market ticks buffer or drop.
2. **Missing Durable Write-Back & Unwired Remote Calendar**: While `EarningsCalendar` loads an initial seed fixture (`backend/app/data/earnings_calendar.json`), successful remote refreshes update only in-memory dictionaries and never persist back to disk. Furthermore, `main.py` never passes a `remote_url` or schedules periodic calendar refreshes, leaving remote calendar integration completely unwired and static in production.
3. **Staged Order Idempotency & Concurrency Cap Breach**: In `backend/app/strategies/swing_panic_dip.py`, `evaluate_market_close()` calculates available slots as `max_concurrent_positions - len(surviving_positions)` without subtracting already-staged entries. On repeated scans or server restarts between 16:00 and 09:30 ET, up to 3 or more entries can be staged, violating the institutional 2-position ceiling ($50,000 notional limit).
4. **Fragile 09:30 Market Open Execution Window**: In `backend/app/main.py`, staged swing orders only execute if `bar_et.time().hour == 9 and bar_et.time().minute == 30`. If the opening bar is delayed by network latency, an illiquid opening auction, or arrives at 09:31 ET, the execution window is permanently missed and staged orders are marooned indefinitely.
5. **Circuit Breaker Cross-Arm Liquidation Contamination**: In `backend/app/main.py`, `_trip_circuit_breaker()` iterates across all positions in `account.positions` and liquidates them without verifying `arm != TradingArm.SWING`. An intraday $1,500 daily loss limit breach improperly dumps multi-day swing holdings at market open prices, violating the architectural isolation mandate.
6. **Incomplete Dry-Run Simulation Architecture**: Existing test scripts (`run_integrated_swing_dry_run.py` and `monday_open_session.json`) do NOT simulate concurrent Intraday and Swing trading arms sharing the $50,000 account pool over multiple consecutive trading days. `run_integrated_swing_dry_run.py` manually injects synthetic fills and hardcodes zero slippage (`slippage=0.0`), rather than streaming continuous 1-minute bars through production event handlers.

---

## 2. Forensic Audit Item 1: Blocking I/O in Async Coroutines

### 2.1 The Vulnerability: `urllib.request.urlopen` in `earnings_calendar.py`
In `backend/app/strategies/earnings_calendar.py`, lines 246–274:

```python
246:     async def refresh_from_remote(self) -> bool:
247:         """Attempt to refresh earnings calendar from remote provider.
248:         
249:         Graceful fallback invariant:
250:         Any exception, timeout, or missing URL is logged and safely returns False
251:         without raising unhandled errors or corrupting local cache.
252:         """
253:         if not self.remote_url:
254:             return False
255: 
256:         try:
257:             import urllib.request
258:             # Short timeout to prevent event loop starvation
259:             req = urllib.request.Request(self.remote_url, headers={"User-Agent": "AutonomousDayTrader"})
260:             with urllib.request.urlopen(req, timeout=2.0) as resp:
261:                 data = json.loads(resp.read().decode("utf-8"))
262:                 if isinstance(data, dict):
263:                     for sym, evs in data.items():
264:                         for item in evs:
265:                             self.add_event(EarningsEvent.from_dict(item))
266:                 elif isinstance(data, list):
267:                     for item in data:
268:                         self.add_event(EarningsEvent.from_dict(item))
269:                 log.info(f"Successfully refreshed earnings calendar from {self.remote_url}")
270:                 return True
271:         except Exception as exc:
272:             log.warning(f"Remote earnings refresh failed ({exc}); continuing with cached seed calendar")
273:             return False
```

### 2.2 Event Loop Impact & Pathology
1. **Event Loop Starvation**: Python’s `asyncio` runs cooperatively on a single operating system thread. Coroutines must yield execution via `await`. When `urllib.request.urlopen(req, timeout=2.0)` is invoked, it makes a synchronous, blocking POSIX socket system call directly on the event loop thread.
2. **False Sense of Safety from `timeout=2.0`**:
   - Even a 2.0-second pause is catastrophic for a high-frequency day trading bot processing up to 3.5 million quotes per session.
   - Crucially, Python's `socket.create_connection` and `urllib.request` do not bound DNS resolution within the socket timeout. A DNS lookup on an unresponsive or hijacked nameserver can block synchronously for 10 to 30 seconds.
3. **Cascading Failure Modes**:
   - **Health Probe Failure**: Railway and container orchestrators ping `GET /health` every 10–15 seconds. If the event loop is blocked for >10 seconds, HTTP requests queue in the socket backlog, the health check times out, and Railway issues `SIGKILL` to restart the container.
   - **WebSocket Drop**: The AlpacaRelay and UI WebSockets send keepalive ping frames every 20 seconds with a 10-second timeout (`WS_PING_TIMEOUT_SEC`). A loop freeze causes missed pings, triggering `websockets.exceptions.ConnectionClosed` and forcing unexpected reconnections.
   - **Delayed Stop-Loss Execution**: During the blocking call, 1-minute bars and real-time trade prices cannot be dispatched to `check_intraday_emergency_stops()` or the bracket manager, exposing positions to unmitigated gap risk.

### 2.3 Comprehensive Scan of Remaining Backend Coroutines
A full-codebase grep and AST inspection was conducted across `backend/app/`:
- `backend/app/ingestion/stock_ws.py`: Uses `websockets.connect` with asynchronous iterator `async for raw_msg in ws:`. Fully non-blocking.
- `backend/app/ingestion/news_ws.py`: Uses `websockets.connect` and `asyncio.Queue` worker. Fully non-blocking.
- `backend/app/ingestion/vix_client.py`: Uses `httpx.AsyncClient` with `await self._http_client.get(url)`. Fully non-blocking.
- `backend/app/core/persistence.py`: Uses synchronous `sqlite3` in WAL mode with local file locks. In-memory and WAL operations execute in <1ms; no network calls.
- `backend/app/strategies/*.py`: All other strategy classes (`orb.py`, `vwap_pullback.py`, `news_momentum.py`, `mean_reversion.py`, `swing_panic_dip.py`) are strictly synchronous compute without network or file I/O.
- **Conclusion**: `backend/app/strategies/earnings_calendar.py` is the **sole source** of blocking network I/O in async routines.

---

## 3. Forensic Audit Item 2: External Calendar / Market Services & Fallbacks

### 3.1 Live vs. Fallback Data Flow
Currently, `EarningsCalendar` exhibits the following data lifecycle:
1. **Startup / Initialization**:
   - `EarningsCalendar.__init__(seed_path, remote_url)` loads `backend/app/data/earnings_calendar.json`.
   - The seed fixture populates `self._events: Dict[str, List[EarningsEvent]]` with scheduled earnings events for `LRCX`, `KLAC`, `MU`, `AMD`, and `GS`.
2. **Missing Persistence on Remote Success**:
   - When `refresh_from_remote()` succeeds, it parses remote JSON and updates `self._events` in memory.
   - It **does not write back** to `seed_path` or any persistent cache.
   - If the application restarts (e.g. Railway redeploy), all newly fetched earnings dates vanish, reverting back to the static repository seed.
3. **Unwired Production Integration**:
   - In `backend/app/main.py:349`:
     ```python
     earnings_calendar = EarningsCalendar(seed_path=settings.EARNINGS_CALENDAR_SEED_PATH)
     ```
   - Notice that `remote_url` is not provided, and `settings` in `backend/app/config.py` does not define `EARNINGS_CALENDAR_REMOTE_URL`.
   - No background task in `lifespan` or `_runtime_clock_loop` ever calls `refresh_from_remote()`.
   - Thus, in production, the calendar operates exclusively in static fallback mode.

### 3.2 Date Horizon Evaluation Edge Case in `is_blackout_active()`
In `backend/app/strategies/earnings_calendar.py`, lines 188–208:
```python
188:                 if 0 <= diff_seconds <= effective_horizon * 3600.0:
189:                     return True
190: 
191:                 # If report date is within calendar days, enforce safe-side blackout for upcoming events
192:                 diff_days = (ev.report_date - as_of_dt.date()).days
193:                 max_days = 4 if as_of_dt.weekday() == 4 else 2
194:                 if 0 <= diff_days <= max_days:
195:                     return True
```
When `as_of` is a `datetime` evaluated at 16:00 ET on Day $t$, if an earnings report occurred earlier on Day $t$ at 08:30 AM (BMO), line 180 skips it because `diff_seconds < 0`. However, line 194 checks `if 0 <= diff_days <= max_days`. For a BMO report on Day $t$, `diff_days = 0`, which is $\ge 0$! This can cause an earnings report released that morning to falsely trigger an entry blackout at 16:00 close.

---

## 4. Additional Critical Vulnerabilities Uncovered

### 4.1 Staged Order Idempotency & Concurrency Cap Violation
**Location**: `backend/app/strategies/swing_panic_dip.py:300–325`

```python
300:         exiting_symbols = {e.symbol for e in staged_exits}
301:         surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
302:         available_slots = self.max_concurrent_positions - len(surviving_positions)
303: 
...
320:                 # Skip if already staged for entry
321:                 if self.staged_manager.is_staged_for_entry(sym):
322:                     continue
```

**Defect Mechanism**:
- `available_slots` is computed strictly against `surviving_positions`. It completely ignores `self.staged_manager.get_staged_entries()`.
- If `evaluate_market_close` is triggered twice (e.g. server restart at 16:05 ET, or multiple close triggers), a symbol already staged for entry (e.g. `LRCX`) is skipped at line 321.
- **However, `available_slots` is not decremented for `LRCX`!**
- The engine then proceeds down the candidate list and stages additional symbols (e.g. `KLAC` and `MU`), resulting in **3 concurrent staged buy orders**.
- Next morning at 09:30, all 3 orders attempt execution, violating the 2-position maximum ($50,000 capital limit).

**Remediation**:
```python
        existing_staged_entries = {e.symbol for e in self.staged_manager.get_staged_entries()}
        available_slots = self.max_concurrent_positions - len(surviving_positions) - len(existing_staged_entries)
```

---

### 4.2 Fragile 09:30 Market Open Execution Window
**Location**: `backend/app/main.py:1294–1299`

```python
1294:     # 09:30 ET Market Open Execution for Staged Swing Orders
1295:     # Execute open orders strictly for this symbol when THAT symbol's 09:30 open bar arrives
1296:     if bar_et.time().hour == 9 and bar_et.time().minute == 30:
1297:         if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
1298:             swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)
```

**Defect Mechanism**:
- If `LRCX` experiences an opening delay, opening auction halt, or the 09:30:00 bar is dropped/delayed to 09:31:00, `bar_et.time().minute == 30` evaluates to `False`.
- The staged order is never executed and remains marooned in `SwingStagedOrderManager`.
- Because the symbol remains reserved in `swing_reserved_symbols`, neither the swing engine nor the intraday engine can trade it for the remainder of the session.

**Remediation**:
Replace the exact minute check with a morning execution window check (`09:30 <= bar_et.time() < 10:00`). When the first bar on or after 09:30 ET arrives for a symbol with a staged order, execute at `bar.open`. Once executed, `execute_market_open()` clears the order from `staged_manager`, guaranteeing strict once-and-only-once execution.

---

### 4.3 Circuit Breaker Cross-Arm Liquidation Contamination
**Location**: `backend/app/main.py:875–890`

```python
875: def _trip_circuit_breaker(timestamp: datetime) -> None:
876:     """Halt trading and liquidate all open positions after a daily-loss breach."""
877:     account.status = account.status.__class__.CIRCUIT_HALTED
878:     engine.cancel_all_orders("CIRCUIT_BREAKER_HALT")
879:     _release_dead_entry_brackets()
880:     for sym, pos in list(account.positions.items()):
881:         side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
882:         bracket_id = bracket_manager.symbol_to_bracket.get(sym)
883:         liq_order = engine.create_order(
884:             symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
885:             strategy_id="CIRCUIT_BREAKER", parent_order_id=bracket_id,
886:         )
887:         engine.submit_order(liq_order.id)
888:         liq_fills = _flatten_symbol(sym, pos.market_price, timestamp)
889:         _reconcile_fills(liq_fills)
```

**Defect Mechanism**:
- The institutional risk rule dictates a $1,500 hard daily loss limit circuit breaker for intraday day trading.
- In `_trip_circuit_breaker()`, line 880 iterates through `account.positions.items()` without checking `pos.arm != TradingArm.SWING`.
- If intraday trading breaches the $1,500 drawdown limit, it immediately submits market liquidation orders for active swing positions.
- This directly violates Requirement R2 of the original specification: Swing positions are multi-day holds governed by their own 2.5x ATR emergency stop loss and must remain exempt from intraday liquidations.

**Remediation**:
```python
    for sym, pos in list(account.positions.items()):
        if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
            continue  # Swing positions are strictly exempt from intraday daily loss liquidation
```

---

### 4.4 Hardcoded Zero Slippage on Swing Market Open Fills
**Location**: `backend/app/strategies/swing_panic_dip.py:423–429, 521–529`

```python
427:     slippage=0.0,
...
525:     slippage=0.0,
```

**Defect Mechanism**:
- In `execute_market_open()`, exit and entry fills invoke `execution_engine._execute_fill()` with `slippage=0.0`.
- In real market conditions, 09:30 market-on-open orders on mega-cap stocks experience 2 to 5 basis points of spread and market impact slippage.
- Hardcoding `slippage=0.0` creates synthetic test artifacts and fails to stress the cash and margin balances with realistic execution drag.

---

## 5. Forensic Audit Item 3: Simulation & Dry Run Architecture Assessment

### 5.1 Current Simulation Scripts Audit

| Test / Script File | Current Methodology | Gaps / Deficiencies |
|-------------------|---------------------|---------------------|
| `scripts/run_integrated_monday_dry_run.py` | Uses `MockAlpacaRelayServer` + `monday_open_session.json` (62 events, 09:29–09:35 ET) through production `lifespan`. | Covers only 1 day, 6 minutes, and 3 intraday symbols (`AAPL`, `NVDA`, `TSLA`). Zero swing trading evaluation or execution. |
| `scripts/run_integrated_swing_dry_run.py` | Standalone procedural script stepping through 6 synthetic dates. | Does NOT run `MockAlpacaRelayServer` or WebSocket clients. Manually appends daily bars to `daily_bar_store`. Only injects 1 manual intraday fill on Day 2 (`account.apply_fill("intraday_nvda_fill", ...)`). Does not test concurrent bar streaming across 1-minute intervals. Hardcodes `slippage=0.0`. |
| `tests/e2e/test_swing_multiday_replay.py` | Pytest suite invoking `SwingStrategyEngine` methods via fixtures. | Validates swing rules in isolation; does not run concurrent intraday strategies (`orb.py`, `vwap_pullback.py`, `news_momentum.py`, `mean_reversion.py`) or event bus routing. |

### 5.2 Architectural Blueprint for Exhaustive Concurrent Multi-Day Dry Run

To certify the integrated intraday and swing trading engine under realistic conditions, the simulation architecture must be structured as follows:

```
+-------------------------------------------------------------------------------+
|                     Multi-Day Concurrent Replay Engine                        |
+-------------------------------------------------------------------------------+
                                      |
         +----------------------------+----------------------------+
         |                                                         |
         v                                                         v
+-------------------------------+                         +---------------------+
| Intraday Arm (12 Symbols)     |                         | Swing Arm (5 Names) |
| SPY, QQQ, AAPL, NVDA, TSLA,   |                         | LRCX, KLAC, MU,     |
| AMD, MSFT, AMZN, META, GOOGL  |                         | AMD, GS             |
+-------------------------------+                         +---------------------+
         |                                                         |
         +----------------------------+----------------------------+
                                      |
                                      v
         +---------------------------------------------------------+
         | 1-Minute Bar Event Stream (09:30 - 16:00 ET, Days 1-5)  |
         +---------------------------------------------------------+
                                      |
         +----------------------------+----------------------------+
         |                                                         |
         v                                                         v
+----------------------------------+            +-------------------------------+
| Morning Open Phase (09:30 ET)    |            | EOD Flattening (15:45-15:58)  |
| - Staged Swing Exits execute     |            | - Phase 1: Intraday Lockout   |
| - Staged Swing Entries execute   |            | - Phase 2: Order Purge        |
| - Intraday ORB/VWAP scans start  |            | - Phase 3: Intraday Liquidate |
| - Shared BP verified ($50k pool) |            | - Phase 4: Flat Audit Passes  |
+----------------------------------+            | - SWING POSITIONS EXEMPT!     |
                                                +-------------------------------+
                                                               |
                                                               v
                                                +-------------------------------+
                                                | Market Close Phase (16:00 ET) |
                                                | - DailyBarAggregator commits  |
                                                | - Swing scans Rules 1, 2, 3   |
                                                | - 48h Earnings Blackout check |
                                                | - Rule 7 Exits staged         |
                                                | - Session boundary increments |
                                                |   holding_days on Day t+1     |
                                                +-------------------------------+
```

#### Key Architectural Requirements for Multi-Day Dry Run:
1. **Consecutive Multi-Day Sequence**: Simulate 5 consecutive trading days (Monday through Friday) followed by Monday (Day 6) to verify weekend holding.
2. **Realistic Microstructure Slippage & Fees**:
   - Market open fills compute slippage: $\text{slippage} = \max(0.01, P_{\text{open}} \times 0.0003)$ (3 bps baseline).
   - Sell fills deduct exact SEC Section 31 ($27.80/million) and FINRA TAF ($0.000166/share) regulatory fees.
3. **Continuous Intraday Market Activity**:
   - Provide realistic 1-minute OHLCV bars for both intraday names (`NVDA`, `TSLA`, `AAPL`) and swing names (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`).
   - Intraday strategies generate valid entries, manage dynamic trailing brackets, hit Target 1 / Target 2, and cleanly flatten at 15:55 ET.
4. **Mutual Exclusion Stress (`AMD`)**:
   - `AMD` is present in both watchlists. The dry run must verify that when `AMD` is held or staged by Swing, Intraday attempts to enter `AMD` are strictly blocked.
5. **Shared $50,000 Pool Accounting**:
   - Track Cash, Equity, Maintenance Margin (25% on longs), and Day Trading Buying Power ($4 \times \text{Margin Excess}$).
   - Prove that holding $50,000 of swing positions leaves $37,500 of margin excess and $150,000 of intraday buying power, enabling daytime trading without false margin calls.

---

## 6. Proposed Code Changes & Implementation Blueprints

### 6.1 `backend/app/strategies/earnings_calendar.py`
Replace `urllib.request` with `httpx.AsyncClient`, add durable cache saving, and fix date comparison:

```python
<<<<
        try:
            import urllib.request
            # Short timeout to prevent event loop starvation
            req = urllib.request.Request(self.remote_url, headers={"User-Agent": "AutonomousDayTrader"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict):
                    for sym, evs in data.items():
                        for item in evs:
                            self.add_event(EarningsEvent.from_dict(item))
                elif isinstance(data, list):
                    for item in data:
                        self.add_event(EarningsEvent.from_dict(item))
                log.info(f"Successfully refreshed earnings calendar from {self.remote_url}")
                return True
        except Exception as exc:
            log.warning(f"Remote earnings refresh failed ({exc}); continuing with cached seed calendar")
            return False
====
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    self.remote_url,
                    headers={"User-Agent": "AutonomousDayTrader/1.0"},
                )
                if resp.status_code != 200:
                    log.warning(f"Remote earnings provider returned HTTP {resp.status_code}")
                    return False

                data = resp.json()
                count = 0
                if isinstance(data, dict):
                    for sym, evs in data.items():
                        for item in evs:
                            if "symbol" not in item:
                                item["symbol"] = sym
                            self.add_event(EarningsEvent.from_dict(item))
                            count += 1
                elif isinstance(data, list):
                    for item in data:
                        self.add_event(EarningsEvent.from_dict(item))
                        count += 1

                log.info(f"Successfully refreshed {count} earnings events from {self.remote_url}")
                if self.cache_path:
                    self.save_cache_file(self.cache_path)
                return True
        except Exception as exc:
            log.warning(f"Remote earnings refresh failed ({exc}); continuing with cached seed calendar")
            return False
>>>>
```

Add durable cache write-back method:
```python
    def save_cache_file(self, file_path: str) -> None:
        """Atomically persist current earnings calendar to a local JSON cache file."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        serialized = {
            sym: [ev.to_dict() for ev in events]
            for sym, events in self._events.items()
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=2)
        tmp_path.replace(path)
        log.info(f"Durable earnings calendar saved to {path} ({len(self._events)} symbols)")
```

### 6.2 `backend/app/strategies/swing_panic_dip.py`
Fix Staged Order Idempotency and add realistic slippage:

```python
<<<<
        exiting_symbols = {e.symbol for e in staged_exits}
        surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
        available_slots = self.max_concurrent_positions - len(surviving_positions)
====
        exiting_symbols = {e.symbol for e in staged_exits}
        surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
        existing_staged_entries = {e.symbol for e in self.staged_manager.get_staged_entries()}
        available_slots = self.max_concurrent_positions - len(surviving_positions) - len(existing_staged_entries)
>>>>
```

In `execute_market_open()`:
```python
<<<<
                    fill = self.execution_engine._execute_fill(
                        order=order_obj,
                        qty=qty,
                        price=open_price,
                        slippage=0.0,
                        timestamp=open_time,
                    )
====
                    slippage = max(0.01, round(open_price * 0.0003, 4))
                    fill = self.execution_engine._execute_fill(
                        order=order_obj,
                        qty=qty,
                        price=round(open_price + slippage, 2),
                        slippage=slippage,
                        timestamp=open_time,
                    )
>>>>
```

### 6.3 `backend/app/main.py`
Widen 09:30 open execution tolerance and protect swing positions in `_trip_circuit_breaker`:

```python
<<<<
    # 09:30 ET Market Open Execution for Staged Swing Orders
    # Execute open orders strictly for this symbol when THAT symbol's 09:30 open bar arrives
    if bar_et.time().hour == 9 and bar_et.time().minute == 30:
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)
====
    # Morning Open Execution Window for Staged Swing Orders (09:30 - 10:00 ET)
    # Executes on the first available morning bar on or after 09:30 ET
    if time(9, 30) <= bar_et.time() < time(10, 0):
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)
>>>>
```

In `_trip_circuit_breaker()`:
```python
<<<<
    for sym, pos in list(account.positions.items()):
        side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        bracket_id = bracket_manager.symbol_to_bracket.get(sym)
        liq_order = engine.create_order(
            symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
            strategy_id="CIRCUIT_BREAKER", parent_order_id=bracket_id,
        )
====
    for sym, pos in list(account.positions.items()):
        # Swing positions are strictly exempt from intraday daily loss liquidation
        if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
            continue
        side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        bracket_id = bracket_manager.symbol_to_bracket.get(sym)
        liq_order = engine.create_order(
            symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
            strategy_id="CIRCUIT_BREAKER", parent_order_id=bracket_id,
            arm=TradingArm.INTRADAY,
        )
>>>>
```
