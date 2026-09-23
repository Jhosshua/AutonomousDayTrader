# Exhaustive Adversarial Audit: Concurrency, Event Bus, Ingestion Pipelines & Buffer Memory Hygiene

**Auditor**: Explorer R6-1  
**Target Project**: AutonomousDayTrader (12-Symbol Universe: `SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `AMD`, `MSFT`, `AMZN`, `META`, `GOOGL`, `PLTR`, `COIN`)  
**Scope**: Ingestion pipelines, EventBus, ExecutionEngine, BracketManager, Persistence/Ledger, Buffer Memory Hygiene, and Process Lifecycles.  
**Mode**: Read-Only Adversarial Exploration & Technical Specification  

---

## Executive Summary

An exhaustive adversarial code review of the concurrency primitives, asynchronous event bus, ingestion streaming pipelines, and in-memory buffer hygiene was conducted across the 12-symbol universe of `AutonomousDayTrader`. The system has high baseline robustness (324/324 backend pytest pass, 320/320 opaque-box E2E pass, clean port hygiene). However, scaling from 3 to 12 symbols increases message volume by ~4x (up to 24,000 incoming frames/sec during open-flush volatility across SIP quotes, trades, and 1-minute bars). 

Under this elevated throughput, several latent concurrency bottlenecks, unbounded buffer accumulations, and database hygiene defects were identified:
1. **Critical Ingestion Defect**: The FIFO backpressure queue in `stock_ws.py` multiplexes quotes, trades, and bars in a single buffer and drops messages indiscriminately when full, leading to permanent loss of critical 1-minute `BarEvent` and `TradeEvent` data under quote floods.
2. **Major Memory Leak**: `news_momentum.py` records pending news catalysts for all symbols in Benzinga news (thousands of US equities) without filtering by `WATCHLIST_SYMBOLS`. Because `on_bar` is only invoked for watchlist names, non-watchlist symbols accumulate unpruned in memory indefinitely.
3. **Major Database Bottleneck**: `TradingStateStore` in `persistence.py` operates SQLite with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=FULL`, but **never** executes `PRAGMA wal_checkpoint` (neither periodically nor on shutdown), causing unbounded `-wal` file growth and blocking the `asyncio` event loop thread with synchronous `fsync` calls during runtime checkpoints.
4. **Major Buffer Hygiene**: `market_history` in `main.py` uses standard lists with $O(N)$ `del history[:-120]` slice deletions on every bar and fails to clear historical candles at session rollover, displaying prior-day candles on Monday open.
5. **Major EventBus Risk**: `event_bus.py` does not deduplicate handlers across polymorphic subscriber registrations and does not shield critical multi-step execution transactions against `asyncio.CancelledError`.
6. **Minor State Desynchronization**: `execute_strategy_signal` fails to notify `orb_strategy` when an entry order is rejected by `engine.submit_order`, permanently locking out the breakout flag for the remainder of the session.

---

## Vulnerability Catalog

| ID | Module / File | Line(s) | Severity | Category | Description |
|---|---|---|---|---|---|
| **VULN-01** | `backend/app/ingestion/stock_ws.py` | 206–220, 220–264 | **CRITICAL** | Ingestion / Backpressure | Indiscriminate FIFO backpressure queue drops bars and trades when quote throughput surges across 12 tickers |
| **VULN-02** | `backend/app/strategies/news_momentum.py` | 184–196, 213–220 | **MAJOR** | Memory Hygiene | Unbounded `pending_catalysts` memory leak for non-watchlist symbols (only watchlist names receive `on_bar` pruning) |
| **VULN-03** | `backend/app/core/persistence.py` | 156–160, 290–378, 536–544 | **MAJOR** | Persistence / Performance | SQLite WAL checkpoint never called (`wal_checkpoint` absent), causing WAL file bloat and event-loop thread blocking on `fsync` |
| **VULN-04** | `backend/app/main.py` | 86, 1008–1017, 695–775 | **MAJOR** | Memory / Lifecycle | Inefficient $O(N)$ list deletion in `market_history` and failure to clear `market_history` / `recent_news` on session boundary |
| **VULN-05** | `backend/app/core/event_bus.py` | 45–60, 61–76 | **MAJOR** | Concurrency / FSM | Handler duplication on subclass polymorphism and unshielded order state corruption upon task cancellation |
| **VULN-06** | `backend/app/main.py` | 973–988 | **MINOR** | State Machine | `submit_order` rejection fails to call `orb_strategy.notify_signal_rejected()`, causing false permanent breakout lockout |
| **VULN-07** | `backend/app/main.py` | 1394–1399, 1447–1483 | **MINOR** | Lifecycle Shutdown | `event_bus.clear()` absent on application teardown, leaking subscriber closures across lifespan restarts |

---

## Deep-Dive Technical Findings

### 1. Ingestion Backpressure & Packet Dropping Across 12 Tickers (VULN-01)
- **File**: `backend/app/ingestion/stock_ws.py`, lines 206–220:
  ```python
  async def _read_loop(self, ws: Any) -> None:
      async for raw_msg in ws:
          self.messages_received += 1
          qsize = self._queue.qsize()
          if qsize >= settings.QUEUE_MAX_SIZE * settings.QUEUE_HIGH_WATERMARK_PCT:
              log.warning(f"Queue high watermark reached: {qsize}/{settings.QUEUE_MAX_SIZE} items")

          try:
              self._queue.put_nowait(raw_msg)
          except asyncio.QueueFull:
              self.dropped_messages += 1
              log.error("Ingestion queue full! Discarding message to prevent socket stall")
  ```
- **Analysis**:
  - The AlpacaRelay stock endpoint (`/v2/stocks`) transmits bars (`b`), top-of-book quotes (`q`), and trade prints (`t`) over a single multiplexed connection.
  - Across 12 active high-beta symbols (`SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `AMD`, `MSFT`, `AMZN`, `META`, `GOOGL`, `PLTR`, `COIN`), quote frequency easily reaches 500–2,000 quotes/second per ticker during market open volatility (6,000 to 24,000 frames/sec aggregate).
  - The internal buffer `self._queue = asyncio.Queue(maxsize=settings.QUEUE_MAX_SIZE)` holds 10,000 items (less than 0.5 to 1.5 seconds of buffer headroom).
  - When quote ingestion spikes or when consumer execution in `_process_queue_loop` is briefly delayed by SQLite write-ahead logging (5–20ms per checkpoint), the queue hits capacity.
  - When `QueueFull` occurs, `_read_loop` drops the entire raw frame. If that frame contains a 1-minute `BarEvent` or `TradeEvent`, it is discarded.
  - **Downstream Impact**:
    - ORB strategy misses 5m opening range bars, preventing range formation.
    - VWAP calculations become distorted due to missing volume-price bars.
    - `market_filter.on_bar` misses SPY/QQQ index bars; elapsed time since last index bar exceeds `stale_threshold_sec` (120s), triggering `STALE_INDEX_DATA` and halting all directional strategies with `UNKNOWN` regime.
- **Production-Grade Fix Strategy**:
  - Implement a **Prioritized Dual-Queue or Conflation Buffer**:
    1. Parse or inspect message header `T` on wire. Route `b` (bars), `t` (trades), and `relay` control messages to a dedicated, lossless priority queue with generous sizing (e.g. 50,000 items).
    2. Route `q` (quotes) to a bounded conflation structure (keeping only the latest quote per symbol) or shed quotes when `qsize > HIGH_WATERMARK`. Older quotes are superseded by definition, whereas bars and trades represent historical ground truth.

---

### 2. Unbounded Memory Accumulation in News Catalyst Cache (VULN-02)
- **File**: `backend/app/strategies/news_momentum.py`, lines 184–196:
  ```python
  # 2. Record pending catalyst if sentiment meets threshold
  if abs(sentiment) >= self.sentiment_threshold:
      if s not in self.pending_catalysts:
          self.pending_catalysts[s] = []
      self.pending_catalysts[s].append(
          PendingCatalyst(
              headline=news.headline,
              sentiment=sentiment,
              symbols=news.symbols,
              timestamp=now_dt,
          )
      )
  ```
- **Analysis**:
  - AlpacaRelay news channel (`/news`) subscribes wildcard `{"action": "subscribe", "news": ["*"]}`. Benzinga broadcasts real-time news for the entire US stock market (>5,000 tickers).
  - `on_news` extracts every symbol `sym` listed in the article and appends a `PendingCatalyst` object to `self.pending_catalysts[s]` whenever $|sentiment| \ge 0.60$.
  - Pruning occurs strictly in `on_bar(bar)` at lines 213–220:
    ```python
    valid_catalysts = [
        c for c in pending_list
        if (0 <= (now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds) and not c.processed
    ]
    self.pending_catalysts[sym] = valid_catalysts
    ```
  - Because `on_bar` is only dispatched for symbols in `WATCHLIST_SYMBOLS` (the 12 active tickers), `on_bar` is **never** invoked for non-watchlist symbols.
  - Over a full trading session, hundreds of small-cap, biotech, and foreign tickers accumulate in `self.pending_catalysts` without ever being purged.
  - Furthermore, `self.pending_catalysts[s]` for watchlist symbols has no maximum length constraint (e.g. if 50 headlines arrive for TSLA or NVDA within a 3-minute window).
- **Production-Grade Fix Strategy**:
  - Filter incoming news symbols against the configured universe:
    ```python
    active_watchlist = set(settings.WATCHLIST_SYMBOLS)
    for sym in news.symbols:
        s = sym.upper()
        if s not in active_watchlist and s not in self.monitored_positions:
            continue
    ```
  - Enforce a bounded collection for `self.pending_catalysts[s] = collections.deque(maxlen=10)` and prune expired entries across all symbols in `on_time_tick(market_time)` or `reset_daily_stats()`.

---

### 3. Missing SQLite WAL Checkpoints & Event-Loop Thread Blocking (VULN-03)
- **File**: `backend/app/core/persistence.py`, lines 156–160, 290–378, 536–544:
  ```python
  self._connection.execute("PRAGMA journal_mode=WAL")
  self._connection.execute("PRAGMA synchronous=FULL")
  self._connection.execute("PRAGMA foreign_keys=ON")
  self._connection.execute("PRAGMA busy_timeout=15000")
  ```
- **Analysis**:
  - In WAL (Write-Ahead Logging) mode, SQLite appends all transactional writes into `.sqlite3-wal`.
  - Checkpoint transactions (`save_checkpoint`) execute on:
    - Every 1-minute bar across 12 symbols (4,680 calls/day).
    - Every quote mutation that can fill or produces fills (~500–2,000 calls/day).
    - Every news event, VIX regime update, order creation/cancellation, and manual override.
  - In total, 5,000 to 15,000 transactions are committed per session.
  - **Defect 1**: A search across the repository confirms that `wal_checkpoint` is **never executed anywhere** in the codebase. Under continuous write activity with an open read handle, the `-wal` file grows monotonically to tens or hundreds of megabytes, increasing memory-mapped index overhead and slowing search performance.
  - **Defect 2**: In `TradingStateStore.close()`:
    ```python
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            fcntl.flock(self._lease_handle.fileno(), fcntl.LOCK_UN)
            self._lease_handle.close()
            self._closed = True
    ```
    The connection is closed directly without executing `PRAGMA wal_checkpoint(TRUNCATE)`. When the application is redeployed or restarted on Railway, the persistent volume is left with dirty WAL pages that require crash recovery on startup.
  - **Defect 3**: `_checkpoint_runtime` in `main.py` is invoked synchronously on the main asyncio thread. With `PRAGMA synchronous=FULL`, SQLite invokes an OS `fsync()` system call on every commit. On network-attached persistent storage (e.g. Railway volumes), `fsync()` latency ranges from 5ms to 50ms, freezing the asyncio event loop and causing WebSocket ping timeouts or queue drops.
- **Production-Grade Fix Strategy**:
  - In `TradingStateStore.close()` and at daily session boundary rollover, execute:
    ```python
    self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ```
  - In `_checkpoint_runtime`, run checkpoint operations asynchronously using `asyncio.to_thread` or offload WAL flush to a background worker queue with a non-blocking completion future.

---

### 4. Inefficient Slicing in `market_history` & Missing Session Rollover Cleansing (VULN-04)
- **File**: `backend/app/main.py`, lines 86, 1008–1017, and lines 695–775:
  ```python
  history = market_history.setdefault(bar.symbol.upper(), [])
  history.append({
      "time": bar.timestamp.isoformat(),
      "open": bar.open,
      "high": bar.high,
      "low": bar.low,
      "close": bar.close,
      "volume": bar.volume,
  })
  del history[:-120]
  ```
- **Analysis**:
  - `market_history` stores 1-minute OHLCV candles for UI chart streaming (`"chart_points": market_history.get(symbol, [])[-120:]`).
  - Using a Python `list` with `del history[:-120]` forces $O(N)$ element copying on every incoming bar. For 12 symbols, this is 4,680 array resizes per trading session.
  - Thread/Coroutine Hazard: `broadcast_ui_state` reads and serializes `market_history` concurrently while `handle_bar_event` mutates `history` in-place. If an asyncio yield occurs during JSON serialization, concurrent in-place deletion can cause state inconsistency.
  - In `_check_session_boundary` (lines 695–775):
    - `bracket_manager.brackets.clear()`, `engine.prune_session_state()`, and `strategy.reset_daily_stats()` are executed.
    - However, `market_history.clear()` and `recent_news.clear()` are **omitted**!
    - When Monday market opens, `market_history` retains Friday's closing 120 bars until overwritten two hours later. The frontend chart renders prior-day candles concatenated with current-day open bars.
- **Production-Grade Fix Strategy**:
  - Initialize `market_history = defaultdict(lambda: deque(maxlen=120))`.
  - In `_check_session_boundary`, invoke `market_history.clear()` and `recent_news.clear()`.
  - In `runtime_state.py`, ensure decoded JSON lists are wrapped back into `deque(maxlen=120)`.

---

### 5. EventBus Polymorphic Duplication & Unshielded Task Interruption (VULN-05)
- **File**: `backend/app/core/event_bus.py`, lines 45–60:
  ```python
  async def publish(self, event: Any) -> None:
      event_type = type(event)
      handlers: List[HandlerFunc] = []
      for reg_type, reg_handlers in self._subscribers.items():
          if isinstance(event, reg_type):
              handlers.extend(reg_handlers)

      if not handlers:
          return

      self._published_count += 1
      tasks = [self._safe_dispatch(handler, event) for handler in handlers]
      await asyncio.gather(*tasks, return_exceptions=True)
  ```
- **Analysis**:
  - `isinstance(event, reg_type)` checks all registered subscriber types. If a subscriber registers for both a base class and a derived class, or if multiple base interfaces match, `handlers.extend(reg_handlers)` appends duplicate references.
  - `asyncio.gather(*tasks)` will dispatch the same callback multiple times concurrently for a single event.
  - In `_safe_dispatch`, `except Exception as exc:` catches standard exceptions, but `asyncio.CancelledError` (which subclasses `BaseException` in Python 3.8+) escapes and propagates through `gather`.
  - If a task is cancelled during graceful shutdown while `handle_bar_event` is mid-execution, execution is aborted between submitting an order and linking its protective bracket, leaving orphaned working orders in `engine.working_orders`.
- **Production-Grade Fix Strategy**:
  - Deduplicate handlers: `handlers = list(dict.fromkeys(handlers))`.
  - In `handle_bar_event` and `handle_quote_event`, protect critical state transitions with `asyncio.shield` so that shutdown cancellation cannot terminate a half-created bracket order.

---

### 6. Missing Rejection Notification on Broker Order Rejection (VULN-06)
- **File**: `backend/app/main.py`, lines 973–988:
  ```python
  order = engine.create_order(...)
  submitted = engine.submit_order(order.id)
  if submitted.status.value == "ACCEPTED":
      bracket = bracket_manager.create_bracket(...)
      entry_order_to_bracket[submitted.id] = bracket.bracket_id
      if bar:
          fills = engine.process_bar(...)
          _reconcile_fills(fills)
  ```
- **Analysis**:
  - If pre-trade risk fails in `risk_engine.evaluate_order_request`, lines 950–953 properly invoke:
    `if signal.strategy_id == "orb": orb_strategy.notify_signal_rejected(sym)`
  - However, if `engine.submit_order(order.id)` rejects the order (e.g. buying power exceeded in `account.can_afford` or execution validator check), lines 974–988 do nothing.
  - `orb_strategy` is never notified. `state.breakout_fired` remains `True` in `orb_strategy.symbol_states[sym]`.
  - The strategy believes an active trade is in progress and locks out all further breakout opportunities for that symbol for the rest of the day.
- **Production-Grade Fix Strategy**:
  - Add an explicit `else` branch:
    ```python
    if submitted.status.value == "ACCEPTED":
        ...
    else:
        if signal.strategy_id == "orb":
            orb_strategy.notify_signal_rejected(sym)
        log.warning("Entry order %s rejected: %s", submitted.id, submitted.reject_reason)
    ```

---

### 7. EventBus Subscriber Leak on Lifespan Teardown (VULN-07)
- **File**: `backend/app/main.py`, lines 1394–1399 and 1447–1483:
- **Analysis**:
  - `lifespan` registers 6 subscribers on `event_bus` at startup.
  - On teardown, `event_bus.clear()` is not called.
  - In integration tests or environments where the FastAPI application lifecycle is cycled repeatedly, subscribers accumulate, causing duplicate callbacks and memory growth.
- **Production-Grade Fix Strategy**:
  - Add `event_bus.clear()` to the teardown section of `lifespan(app: FastAPI)` in `main.py`.

---

## Deterministic Mutation Test Designs

To prevent regression and verify that remediations are strictly binding, the following deterministic mutation test designs are specified:

### Mutation Test 1: Prioritized Ingestion Queue Preservation Under Quote Flood
- **Target**: `backend/app/ingestion/stock_ws.py`
- **Setup**: Configure `QUEUE_MAX_SIZE = 100`. Send a burst of 150 simulated NBBO quotes (`T: "q"`) followed immediately by 1 critical 1-minute `BarEvent` (`T: "b"`).
- **Assertion**:
  - Total messages received = 151.
  - Dropped messages $\ge 50$ (all quotes).
  - The 1 `BarEvent` must successfully arrive at `event_bus` and increment `client.bars_received == 1`.
- **Mutation to Kill**: Revert to FIFO drop (`self._queue.put_nowait(raw_msg)`).
- **Expected Failure**: Test fails with `client.bars_received == 0` because the bar was dropped at the end of the queue.

### Mutation Test 2: News Catalyst Watchlist Gating & Deque Capping
- **Target**: `backend/app/strategies/news_momentum.py`
- **Setup**: Instantiate `NewsMomentumStrategy`. Publish 50 news items for non-watchlist symbols (`XYZ`, `FOO`, `BAR`) with $|sentiment| = 0.95$. Publish 20 news items for watchlist symbol `AAPL`.
- **Assertion**:
  - `len(strategy.pending_catalysts)` must contain only watchlist keys (`AAPL`). Keys for `XYZ`, `FOO`, `BAR` must NOT exist.
  - `len(strategy.pending_catalysts["AAPL"]) <= 10` (strictly capped by deque maxlen).
- **Mutation to Kill**: Remove symbol filtering in `on_news`.
- **Expected Failure**: Test fails with `len(strategy.pending_catalysts) == 4` and keys for non-watchlist tickers present.

### Mutation Test 3: SQLite WAL Checkpoint on Shutdown & Truncation
- **Target**: `backend/app/core/persistence.py`
- **Setup**: Create temporary `TradingStateStore`. Execute 100 `save_checkpoint` calls. Verify `.sqlite3-wal` file exists and has size $> 0$. Call `state_store.close()`.
- **Assertion**:
  - After `close()`, the `.sqlite3-wal` file size must be exactly 0 bytes (or removed if truncated).
  - Re-opening connection verifies database passes `PRAGMA integrity_check` with zero WAL recovery delay.
- **Mutation to Kill**: Omit `PRAGMA wal_checkpoint(TRUNCATE)` in `close()`.
- **Expected Failure**: Test fails asserting `.sqlite3-wal` file size is $> 0$ bytes after close.

### Mutation Test 4: Market History Deque Capping and Session Boundary Reset
- **Target**: `backend/app/main.py`
- **Setup**: Ingest 150 bars for `AAPL` into `market_history`. Check `len(market_history["AAPL"]) == 120`. Call `_check_session_boundary(next_day)`.
- **Assertion**:
  - `len(market_history["AAPL"]) == 0` (clean boundary reset for new session).
  - Prior-day candles do not leak into the next day.
- **Mutation to Kill**: Remove `market_history.clear()` from `_check_session_boundary`.
- **Expected Failure**: Test fails asserting `len(market_history["AAPL"]) == 120` instead of 0 after session rollover.

### Mutation Test 5: ORB Signal Rejection Reset on Engine Submit Rejection
- **Target**: `backend/app/main.py`
- **Setup**: Configure account cash to $0.00 so `engine.submit_order` returns `REJECTED` (`can_afford` fails). Emit valid ORB signal.
- **Assertion**:
  - `orb_strategy.symbol_states["AAPL"].breakout_fired` must be `False`.
- **Mutation to Kill**: Remove `notify_signal_rejected` from the `submit_order` rejection branch.
- **Expected Failure**: Test fails asserting `breakout_fired is True`.

---

## Conclusion & Next Steps

All 7 vulnerabilities have been cataloged with code references, failure mechanisms, production-grade fix strategies, and deterministic mutation test specifications. 

The findings are delivered in `analysis.md` and synthesized into `handoff.md` for orchestrator review and remediation assignment.
