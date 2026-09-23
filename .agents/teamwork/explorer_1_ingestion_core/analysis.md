# Exhaustive Code Review & Static Analysis Report
**Target Layers**: Ingestion Layer (`backend/app/ingestion/`) & Core State & Risk Layer (`backend/app/core/`)  
**Auditor**: Explorer 1  
**Date**: 2026-09-23  
**Status**: COMPLETE  

---

## 1. Executive Summary

An exhaustive, line-by-line architectural and forensic code audit was conducted on the Ingestion Layer (`backend/app/ingestion/`) and Core State & Risk Layer (`backend/app/core/`), including their runtime integration points in `backend/app/main.py` and dynamic adaptation interfaces.

The review evaluated:
- Connection lifecycles, reconnect backoffs, backpressure handling, queue limits, socket drops, exception suppression, task cancellations.
- Hard daily loss circuit breaker ($1,500), position sizing, max drawdowns, stop-loss `[0.0040, 0.0400]` bounds.
- Bracket state machine, Target 1 / Target 2 tracking, partial fills, trailing stops, orphaned order cancellation, and slippage re-anchoring.
- Market trend filter (anchored VWAP, 9/21 EMAs, signed causal staleness guards, no-lookahead enforcement).
- Paper account accounting (cash, equity, 4:1 FINRA DTBP, position flips, short liabilities, fee deduplication, mark-to-market precision).
- Durable persistence (SQLite WAL mode, exclusive OS file locks, ACID transaction boundaries, memory vs SQLite synchronization).
- EOD auto-flattening engine (4-phase protocol at 15:45, 15:50, 15:55, 15:58 ET and zero overnight holding guarantee).

### Summary of Findings
| Severity | Count | Defect IDs |
| :--- | :---: | :--- |
| **CRITICAL** | 3 | `CR-1`, `CR-2`, `CR-3` |
| **MAJOR** | 6 | `IN-1`, `IN-2`, `IN-3`, `CR-4`, `CR-5`, `CR-6` |
| **MINOR** | 6 | `IN-4`, `IN-5`, `IN-6`, `CR-7`, `CR-8`, `CR-9` |
| **Total** | **15** | |

---

## 2. Ingestion Layer Review (`backend/app/ingestion/`)

### 2.1 Stock WebSocket Client (`stock_ws.py`)
- **Lifecycle & Auth**: Handshake (`[{"T":"success","msg":"connected"}]`) and authentication (`{"action":"auth","key":<token>,"secret":""}`) conform strictly to AlpacaRelay protocols. Exponential backoff is applied correctly ($1.0\text{s} \to 30.0\text{s}$).
- **Backpressure & Drop-Tail**: Queue size is bounded by `settings.QUEUE_MAX_SIZE` (10,000 items). When queue hits 80% watermark, a warning is logged. On `asyncio.QueueFull`, messages are discarded via `put_nowait`, preventing upstream TCP backpressure and socket buffer stalls.
- **Defects Identified**:
  - `IN-3` (MAJOR): The consumer worker `_process_queue_loop()` lacks an outer `try...except Exception` handler around the queue fetch loop. If an unhandled exception escapes, the worker task silently terminates while the WebSocket reader continues running, filling the queue and permanently muting the market feed.
  - `IN-4` (MINOR): Dynamic subscription dispatch in `subscribe()` does not catch `ConnectionClosed`, which can raise unhandled exceptions to callers during transient reconnects.

### 2.2 News WebSocket Client (`news_ws.py`)
- **Lifecycle & Auth**: Authenticates against AlpacaRelay `/news` endpoint with wildcard subscription `{"action":"subscribe","news":["*"]}`. Worker tasks consume from a dedicated queue with per-frame decoding.
- **Defects Identified**:
  - `IN-1` (MAJOR): `websockets.connect()` in `news_ws.py` omits `max_size=settings.WS_MAX_MESSAGE_SIZE_BYTES` (8MB). It defaults to the websockets library default of 1MB (1,048,576 bytes). Large batches of Benzinga news or articles with extensive content fields trigger `PayloadTooBig` / `ConnectionClosed` (code 1009), causing disconnects during news spikes.
  - `IN-2` (MAJOR): The frame processing loop `for m in msgs:` has no per-item `try...except Exception` block. A single malformed article (e.g. invalid date or null id) aborts parsing of the entire frame, dropping all subsequent valid news articles in that batch.

### 2.3 Spot VIX Client (`vix_client.py`)
- **Query & Protocol**: Ingests spot VIX prints from `GET /vix` with no query parameters, respecting `X-Relay-Token`. Supports injectable `time_source` for historical replay simulation.
- **Staleness & Regime Classification**: Correctly maps prints to 4 institutional regimes (`LOW`, `NORMAL`, `ELEVATED`, `CRISIS`) using single-sourced boundaries `(15.0, 25.0, 35.0)`. During regular hours, prints older than `VIX_MAX_STALE_AGE_SEC` (300s) are marked stale. Outside market hours, prints prior to the last session close (16:00 ET) are marked stale.
- **Defects Identified**:
  - `IN-6` (MINOR): In `_get_fallback_print()`, `age_s` is copied directly from `self.last_print.age_s` instead of calculating actual elapsed seconds from `self.last_print.asof`.

### 2.4 Lexicon Financial Sentiment Scorer (`sentiment.py`)
- **Lexicon Architecture**: Analyzes headlines and summaries using multi-word phrases and unigrams with negation windows (3 tokens lookback) and intensifier/diminisher scaling. Normalizes polarity into $[-1.0, 1.0]$ via `math.tanh(raw_score / 2.0)`.
- **Defects Identified**:
  - `IN-5` (MINOR): `_classify_category()` uses `score > 0` directly. When matched phrases result in a neutral score ($0.0$), it defaults to negative catalyst categories (`FDA_REJECTION`, `EARNINGS_MISS`, `GUIDANCE_CUT`), misclassifying neutral headlines.

---

## 3. Core State & Risk Layer Review (`backend/app/core/`)

### 3.1 Institutional Risk Engine (`risk.py`)
- **Hard Daily Loss Limit**: Measures daily drawdown strictly against `starting_equity` ($50,000.00). When drawdown reaches or exceeds $1,500.00, it trips immediately to `HALTED_DAILY_LOSS` and halts order execution.
- **Pre-Trade Gate**: Evaluates `can_afford`, stop distances, risk-adjusted sizing, sector correlation, and concurrency. Position-reducing orders (`is_exit=True`) bypass gates to ensure de-risking is never blocked.
- **Defects Identified**:
  - `CR-9` (MINOR): Tracks `daily_peak_equity` on line 93, but never utilizes it in any calculation or decision logic.

### 3.2 Dynamic Bracket Manager (`bracket.py`)
- **State Machine**: Tracks brackets across 7 explicit states (`PENDING_ENTRY`, `ACTIVE`, `TARGET_1_HIT`, `COMPLETED_PROFIT`, `COMPLETED_STOP`, `COMPLETED_FLATTEN`, `CANCELLED`).
- **Target Calibration**: Target 1 is configured to $0.80R$ with 50% scale-out; Target 2 is $1.80R$ runner.
- **Partial Fill Tracking**: Correctly decrements `target_1_qty` on partial fills and adjusts sibling OCO target sizes on partial stop fills, preventing orphaned limits.
- **Defects Identified**:
  - `CR-6` (MAJOR): `manual_tighten_stop()` validates only that `new_stop_price > current_stop_price` for LONGs, without checking if `new_stop_price < current_market_price`. An errant manual input above market price immediately triggers a market liquidation upon submission.

### 3.3 Market Trend Filter (`market_filter.py`)
- **Anchored VWAP & EMA Tracking**: Tracks SPY and QQQ starting strictly at 09:30 ET. Caches 9-EMA and 21-EMA.
- **Causal Arrow of Time**: Strictly rejects future timestamps with `FUTURE_INDEX_DATA` when $elapsed < 0$, guaranteeing zero lookahead bias.
- **Fail-Closed Staleness**: Reverts to `MarketTrend.UNKNOWN` if SPY or QQQ data is missing or exceeds 120s staleness, blocking trend-following entries.

### 3.4 Paper Trading Account (`account.py`)
- **FINRA Rule 4210 Compliance**: Enforces 4:1 Day Trading Buying Power when equity $\ge \$25,000$. Enforces short liability maintenance margin requirements ($30\%$ of market value or $\$5.00/\text{share}$ if price $\ge \$5.00$; $100\%$ or $\$2.50/\text{share}$ if price $< \$5.00$).
- **Position Accounting & Flips**: Correctly calculates cash adjustments, average entry prices on scale-ins, realized PnL on partial and full closes, and splits fees during position flips (Long to Short and Short to Long).

### 3.5 Execution Engine & Order Lifecycle (`engine.py`)
- **Order State Machine**: Enforces 8 states (`CREATED`, `SUBMITTED`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`, `EXPIRED`).
- **Regulatory Fees**: Computes SEC Section 31 fees ($\$27.80$ per million, rounded up to the nearest cent) and FINRA TAF ($\$0.000166/\text{share}$, capped at $\$8.30$) on sell executions.
- **Defects Identified**:
  - `CR-2` (CRITICAL): `process_quote()` sorts stop orders first, but unlike `process_bar()`, lacks the terminal `break` statement after a stop order fills. On crossed or wide quotes, an OCO limit target order can also execute on the same quote tick, leading to an over-fill and creating an unhedged naked short position.
  - `CR-7` (MINOR): In-place mutations of working order quantities and stop prices during trailing stop adjustments and partial fills do not generate `OrderAuditRecord` entries in `engine.audit_log`.

### 3.6 Durable Persistence & Ledger (`persistence.py`, `runtime_state.py`)
- **Crash Resilience**: Implements WAL mode (`PRAGMA journal_mode=WAL`), full synchronous durability (`PRAGMA synchronous=FULL`), and busy timeouts ($15,000\text{ms}$).
- **Single Writer Guarantee**: Uses OS-level `fcntl.flock` with `LOCK_EX | LOCK_NB` on a dedicated `.writer.lock` file, preventing split-brain writes during overlapping deployments.
- **Transaction Safety**: All checkpoint writes execute inside `BEGIN IMMEDIATE ... COMMIT` blocks with automatic `ROLLBACK` on error. Pending inbox events ensure write-ahead logging before state mutation.
- **Defects Identified**:
  - `CR-8` (MINOR): Synchronous SQLite disk transactions and SHA256 checksum operations are invoked directly on the asyncio event loop thread without `asyncio.to_thread`.

### 3.7 Zero-Overnight Flattening Engine (`flattening.py`)
- **4-Phase Schedule**:
  - 15:45 ET (Phase 1): `ENTRY_LOCKOUT`
  - 15:50 ET (Phase 2): `ORDER_PURGE`
  - 15:55 ET (Phase 3): `MANDATORY_LIQUIDATION`
  - 15:58 ET (Phase 4): `ZERO_AUDIT`
- **Session Boundary Invariant**: In `main.py` `_check_session_boundary()`, any positions lingering into the next session date are forcefully liquidated before daily metric resets, guaranteeing zero overnight holds.
- **Defects Identified**:
  - `CR-5` (MAJOR): In `flattening.py`, `check_time_tick()` marks `self.phase4_executed = True` on the first tick of 15:58:00 ET. If the emergency liquidation sweep does not complete immediately, `check_time_tick()` returns `None` for all subsequent ticks until 16:00, with zero retries.

### 3.8 Cross-Layer Integration (`main.py`, `adaptation.py`)
- **Defects Identified**:
  - `CR-1` (CRITICAL): `DynamicAdaptationEngine.calculate_adapted_stop()` scales raw stop distances by `current_stop_multiplier` ($0.85$ under Low VIX, $1.40$ under Elevated VIX, $2.00$ under Crisis VIX) without clamping to the institutional `[0.0040, 0.0400]` window, causing valid orders to be rejected by `risk_engine`.
  - `CR-3` (CRITICAL): `_execute_manual_flatten()` only iterates over `account.positions.keys()`. If an order is working in `engine.working_orders` (e.g., an unfilled entry limit order) or a `PENDING_ENTRY` bracket exists without an open position, manual flatten ignores it, leaving working orders active to fill later.
  - `CR-4` (MAJOR): Position allocation caps are desynchronized across layers: `adaptation.py` enforces $25\%$ of equity ($12,500), `risk.py` enforces $50\%$ of equity ($25,000), and `account.py` allows $25\%$ of DTBP ($50,000).

---

## 4. Comprehensive Findings Catalog

### [CRITICAL] CR-1: Missing Stop-Loss Distance Bounds Clamp in `calculate_adapted_stop`
- **File**: `backend/app/strategies/adaptation.py`: Lines 215–224
- **Related Call Site**: `backend/app/main.py`: Lines 896, 917–933
- **Code**:
  ```python
  def calculate_adapted_stop(self, signal: SignalEvent) -> float:
      """Calculate volatility-adapted stop-loss price scaled by current_stop_multiplier."""
      raw_dist = abs(signal.entry_price - signal.stop_loss)
      adapted_dist = raw_dist * self.current_stop_multiplier
      is_buy = signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY"
      if is_buy:
          return round(signal.entry_price - adapted_dist, 4)
      else:
          return round(signal.entry_price + adapted_dist, 4)
  ```
- **Description**: Strategies clamp their raw stops to the interior bounds `[0.0042, 0.0380]`. When VIX is in `LOW` regime ($VIX < 15.0$), `current_stop_multiplier = 0.85`. When in `ELEVATED` regime ($25.0 \le VIX < 35.0$), `stop_multiplier = 1.40`. When in `CRISIS` regime ($VIX \ge 35.0$), `stop_multiplier = 2.00`. `calculate_adapted_stop()` multiplies `raw_dist` by the multiplier with no boundary clamping.
- **Impact & Failure Mechanics**:
  - Under Low VIX: A strategy stop of $0.42\%$ is scaled to $0.0042 \times 0.85 = 0.00357$ ($35.7\text{ bps} < 40\text{ bps}$). In `risk.py`, `evaluate_order_request` rejects the order with `STOP_DISTANCE_TOO_TIGHT`.
  - Under Elevated VIX: A strategy stop of $3.0\%$ is scaled to $0.0300 \times 1.40 = 0.0420$ ($420\text{ bps} > 400\text{ bps}$). `risk.py` rejects the order with `STOP_DISTANCE_TOO_WIDE`.
  - Under Crisis VIX: Any stop $> 2.0\%$ is scaled to $> 4.0\%$, systematically rejecting orders.
  - **Empirical Evidence**: Executed test with VIX=12.0: Entry=100.0, Stop=99.58 (0.42% dist) yielded `Adapted stop: 99.643`, which `risk_engine` rejected: `False STOP_DISTANCE_TOO_TIGHT: Stop distance 0.0036 < min 0.0040`. Test with VIX=30.0: Entry=100.0, Stop=97.00 yielded `Adapted stop: 95.8`, rejected as `False STOP_DISTANCE_TOO_WIDE: Stop distance 0.0420 > max 0.0400`.
- **Remediation**: In `DynamicAdaptationEngine.calculate_adapted_stop()`, clamp `adapted_dist` to `[signal.entry_price * 0.0042, signal.entry_price * 0.0380]` before computing the final stop price.

---

### [CRITICAL] CR-2: Missing Stop-Loss Fill Loop Termination (`break`) in `ExecutionEngine.process_quote`
- **File**: `backend/app/core/engine.py`: Lines 283–325
- **Code**:
  ```python
  matching_orders.sort(key=lambda order: order.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT))

  for order in matching_orders:
      if order.id not in self.working_orders:
          continue
      ...
      if fill_price is not None:
          fill = self._execute_fill(order, order.remaining_qty, fill_price, slippage, timestamp)
          fills.append(fill)
  ```
- **Description**: In `process_bar()` (lines 380–383), when a stop order fills, it executes:
  ```python
  if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
      break
  ```
  This is because a stop fill is an OCO terminal event; continuing the loop before fill reconciliation would allow sibling limit target orders to fill in the same tick. In `process_quote()`, although the comment on line 297 notes that stops must be processed first to avoid crossing both, the `break` statement was completely omitted!
- **Impact & Failure Mechanics**: When a wide, fast, or crossed quote arrives where both a STOP SELL order and another working order (such as a market order, additional stop, or crossed limit) match, `process_quote()` fills the stop order (liquidating the position), and then immediately evaluates and fills subsequent orders for the same symbol. This executes a second sell, creating an accidental and unhedged naked short position.
- **Remediation**: Add `if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT): break` immediately following `fills.append(fill)` in `process_quote()`.

---

### [CRITICAL] CR-3: `manual_flatten` Skips Unfilled Entry Orders and Pending Brackets
- **File**: `backend/app/main.py`: Lines 1806–1836, 1838–1848
- **Code**:
  ```python
  async def _execute_manual_flatten(
      target_symbols: List[str], now_dt: datetime, event_key: Optional[str]
  ) -> Dict[str, Any]:
      flattened = []

      for sym in target_symbols:
          pos = account.positions.get(sym)
          if pos:
              bracket_id = bracket_manager.symbol_to_bracket.get(sym)
              cancel_dir = bracket_manager.cancel_bracket_for_flattening(sym, reason="MANUAL_FLATTEN")
              ...
  ```
- **Description**: When `/api/flatten` is called without parameters ("Flatten All"), `target_symbols` defaults to `list(account.positions.keys())`. If an entry order is pending in `engine.working_orders` for a symbol that has no open position yet (`pos is None`), `target_symbols` does not even include that symbol. Even when `target_symbols` is explicitly specified with a symbol name, `if pos:` skips any symbol with no open position, completely ignoring working entry orders and `PENDING_ENTRY` brackets.
- **Impact & Failure Mechanics**: A trader clicking "Flatten All" or "Flatten TSLA" expecting all orders and exposure to terminate leaves working entry limit orders active in the order book. When market price hits the limit price seconds later, a new position is opened directly after the trader requested a complete flatten.
- **Remediation**: In `_execute_manual_flatten()`:
  1. Default `target_symbols` to `set(account.positions.keys()) | {o.symbol for o in engine.working_orders.values()} | set(bracket_manager.symbol_to_bracket.keys())`.
  2. For every symbol, cancel all working orders in `engine.working_orders` for that symbol, cancel any `PENDING_ENTRY` bracket via `bracket_manager.cancel_pending_entry_bracket(sym)`, and then liquidate `pos` if active.

---

### [MAJOR] IN-1: Missing `max_size` Payload Limit in `NewsWebSocketClient`
- **File**: `backend/app/ingestion/news_ws.py`: Lines 103–107
- **Code**:
  ```python
  async with websockets.connect(
      self.relay_url,
      ping_interval=settings.WS_PING_INTERVAL_SEC,
      ping_timeout=settings.WS_PING_TIMEOUT_SEC,
  ) as ws:
  ```
- **Description**: `stock_ws.py` passes `max_size=settings.WS_MAX_MESSAGE_SIZE_BYTES` (8MB) to `websockets.connect()`. `news_ws.py` omits `max_size`, defaulting to the `websockets` library's 1MB (1,048,576 bytes) limit.
- **Impact**: News WebSocket frames containing multiple articles with full content summaries or large historical news bursts during high-volatility sessions exceed 1MB, raising `websockets.exceptions.PayloadTooBig` and dropping the news connection during market catalyst events.
- **Remediation**: Add `max_size=settings.WS_MAX_MESSAGE_SIZE_BYTES` to `websockets.connect()` in `news_ws.py`.

---

### [MAJOR] IN-2: Lack of Per-Item Exception Isolation in `NewsWebSocketClient` Batch Parser
- **File**: `backend/app/ingestion/news_ws.py`: Lines 191–236
- **Code**:
  ```python
  for m in msgs:
      t = m.get("T")
      if t == "n":
          headline = m.get("headline", "")
          ...
          created_dt = datetime.fromisoformat(created_str)
          ...
          await self.bus.publish(event)
  ```
- **Description**: In `news_ws.py`, `for m in msgs:` has no per-item `try...except` block, unlike `stock_ws.py` (which wraps each message in an inner `try...except Exception as item_err:` block).
- **Impact**: If a single article in a multi-article batch contains an unexpected schema (such as a malformed ISO-8601 date raising `ValueError`, or a non-integer `id` raising `TypeError`), the exception aborts the loop. All subsequent valid news articles in that same batch are dropped without processing.
- **Remediation**: Wrap the inner loop body in `for m in msgs:` in `news_ws.py` with `try...except asyncio.CancelledError: raise except Exception as item_err: log.exception(f"Error processing individual news item: {item_err}")`.

---

### [MAJOR] IN-3: Worker Task Death on Unhandled Exception in `StockWebSocketClient._process_queue_loop`
- **File**: `backend/app/ingestion/stock_ws.py`: Lines 220–266
- **Code**:
  ```python
  async def _process_queue_loop(self) -> None:
      while self._running:
          try:
              raw_msg = await self._queue.get()
              try:
                  ...
              finally:
                  self._queue.task_done()
          except asyncio.CancelledError:
              break
  ```
- **Description**: In `stock_ws.py`, `_process_queue_loop()` lacks an outer `except Exception as exc:` block within `while self._running:`. If any unexpected error occurs at the queue retrieval level, the loop breaks and `_worker_task` terminates.
- **Impact**: Once `_worker_task` terminates, items in `_queue` are never dequeued. The queue fills to `QUEUE_MAX_SIZE` (10,000 items), after which all incoming market bars, quotes, and trades are permanently dropped as `Queue full! Discarding message`. The WebSocket connection remains active, masking the failure from the supervisor loop.
- **Remediation**: Add `except Exception as exc: log.exception(f"Stock queue worker error: {exc}")` to the outer while loop in `_process_queue_loop()`, matching `news_ws.py`.

---

### [MAJOR] CR-4: Systemic Allocation Cap Desynchronization Across Layers
- **Files**:
  - `backend/app/strategies/adaptation.py`: Line 66 (`max_alloc_pct: float = 0.25`)
  - `backend/app/core/risk.py`: Line 43 (`max_position_equity_pct: float = 0.500`)
  - `backend/app/core/account.py`: Line 97 (`MAX_POSITION_ALLOCATION_PCT: float = 0.25` of DTBP = $50,000)
  - `backend/app/config.py`: Line 98 (`MAX_POSITION_NOTIONAL: float = 25000.0`)
- **Description**: The system architecture mandates: "max position size ($25,000 / 50% equity)". However:
  - In `adaptation.py`: `calculate_position_size()` caps `max_capital = equity * 0.25` ($12,500 on $50k account).
  - In `risk.py`: `max_position_equity_pct = 0.500` ($25,000 on $50k account).
  - In `account.py`: `can_afford()` defaults to $25\%$ of DTBP ($4 \times 0.25 \times \$50,000 = \$50,000$).
- **Impact**: Strategy signal generation throttles position sizes to $12,500 (half of the authorized risk budget), reducing system Sharpe ratio and profit capture. Meanwhile, modules define contradictory position sizing bounds.
- **Remediation**: Update `max_alloc_pct` in `adaptation.py` to `0.50` ($50\%$ of equity = $25,000) and ensure `account.py`'s default `max_position_notional` defaults to `25000.0`.

---

### [MAJOR] CR-5: Single-Shot Execution with Zero Retries in Phase 4 Zero-Overnight Position Audit
- **File**: `backend/app/core/flattening.py`: Lines 118–130
- **Code**:
  ```python
  if t >= self.schedule.phase4_audit_time and t < self.schedule.market_close_time:
      if not self.phase4_executed:
          self.phase4_executed = True
          self.current_phase = FlatteningPhase.ZERO_AUDIT
          return FlatteningDirective(...)
  ```
- **Description**: In `flattening.py`, `check_time_tick()` marks `self.phase4_executed = True` on the initial check at 15:58:00 ET. On all subsequent seconds between 15:58:01 and 15:59:59 ET, `if not self.phase4_executed:` evaluates to `False`, returning `None`.
- **Impact**: If the emergency liquidation sweep initiated at 15:58:00 ET fails to completely liquidate all positions (e.g., due to volume cap throttling or pending fills), `check_time_tick()` never issues another audit directive for the rest of the session. The system sits idle for the remaining 119 seconds before close, failing to retry the audit and violating the "zero overnight holding guarantee".
- **Remediation**: In `check_time_tick()`, allow periodic re-triggering of Phase 4 Audit (e.g. every 5 seconds) whenever `not self.audit_passed` between 15:58:00 and 16:00:00 ET.

---

### [MAJOR] CR-6: Unchecked Price Boundaries in `DynamicBracketManager.manual_tighten_stop`
- **File**: `backend/app/core/bracket.py`: Lines 528–540
- **Code**:
  ```python
  tightened = False
  if bracket.side == "LONG":
      if new_stop_price > bracket.current_stop_price:
          bracket.current_stop_price = new_stop_price
          tightened = True
  else:
      if new_stop_price < bracket.current_stop_price:
          bracket.current_stop_price = new_stop_price
          tightened = True
  ```
- **Description**: `manual_tighten_stop()` validates only that `new_stop_price > current_stop_price` for LONG (or `<` for SHORT). It does not validate that `new_stop_price` is below the current market price (or above for SHORT).
- **Impact**: If a user enters an inverted stop price (e.g., $160 for a stock currently trading at $150), `bracket.current_stop_price` and the working stop order's price are updated to $160. On the very next tick, the stop order immediately triggers as a market sell, instantly liquidating the position at an unexpected price.
- **Remediation**: Pass `current_market_price` into `manual_tighten_stop()` and require `current_stop_price < new_stop_price < current_market_price` for LONGs, and `current_market_price < new_stop_price < current_stop_price` for SHORTs.

---

### [MINOR] IN-4: Unhandled `ConnectionClosed` in `StockWebSocketClient.subscribe`
- **File**: `backend/app/ingestion/stock_ws.py`: Lines 120–122
- **Code**:
  ```python
  if self._ws and self._connected:
      await self._ws.send(json.dumps(payload))
      log.info(f"Sent dynamic subscription: {payload}")
  ```
- **Description**: If the WebSocket drops between checking `self._connected` and awaiting `ws.send()`, `websockets.exceptions.ConnectionClosed` is raised directly to the caller.
- **Impact**: Callers receive an unhandled exception when dynamically subscribing symbols during transient network disconnects.
- **Remediation**: Wrap `await self._ws.send(...)` in `try...except ConnectionClosed: log.warning(...)`.

---

### [MINOR] IN-5: Inverted Catalyst Category on Neutral News Sentiment in `sentiment.py`
- **File**: `backend/app/ingestion/sentiment.py`: Lines 207–222
- **Code**:
  ```python
  def _classify_category(self, text: str, score: float) -> CatalystCategory:
      if any(k in text for k in ("fda", "biotech", "clinical", "drug", "phase 3", "trial")):
          return CatalystCategory.FDA_APPROVAL if score > 0 else CatalystCategory.FDA_REJECTION
      if any(k in text for k in ("earnings", "eps", "quarter", "revenue", "sales", "profit")):
          return CatalystCategory.EARNINGS_BEAT if score > 0 else CatalystCategory.EARNINGS_MISS
      if any(k in text for k in ("guidance", "outlook", "forecast")):
          return CatalystCategory.GUIDANCE_RAISE if score > 0 else CatalystCategory.GUIDANCE_CUT
      if any(k in text for k in ("upgrade", "downgrade", "target price", "pt")):
          return CatalystCategory.ANALYST_UPGRADE if score > 0 else CatalystCategory.ANALYST_DOWNGRADE
  ```
- **Description**: When keyword matches produce a balanced or neutral sentiment score ($score == 0.0$), `score > 0` evaluates to `False`, incorrectly returning `FDA_REJECTION`, `EARNINGS_MISS`, `GUIDANCE_CUT`, or `ANALYST_DOWNGRADE`.
- **Impact**: Neutral headlines containing domain keywords are misclassified as negative catalysts in event bus telemetry and UI feeds.
- **Remediation**: Use a polarity deadband (e.g. `score > 0.10` vs `score < -0.10`), returning `CatalystCategory.NEUTRAL` if $|score| \le 0.10$.

---

### [MINOR] IN-6: Obsolete `age_s` Preserved in `VixClient._get_fallback_print` Cache
- **File**: `backend/app/ingestion/vix_client.py`: Lines 141–155
- **Code**:
  ```python
  if self.last_print:
      return VixPrint(
          value=self.last_print.value,
          asof=self.last_print.asof,
          received_at=datetime.now(timezone.utc),
          age_s=self.last_print.age_s,
          ...
      )
  ```
- **Description**: In fallback prints generated from cached data, `age_s` is copied from the initial fetch instead of calculating current elapsed time.
- **Impact**: Telemetry and UI display stale prints as having an age of a few seconds rather than their true elapsed age.
- **Remediation**: Compute `age_s = max(0.0, (datetime.now(timezone.utc) - self.last_print.asof).total_seconds())`.

---

### [MINOR] CR-7: Missing Audit Trail Records on Working Order Modifications in `ExecutionEngine`
- **Files**: `backend/app/main.py`: Lines 476–485, 1054–1057, 1879–1882
- **Description**: Modifications to working orders (e.g. trailing stop price updates, breakeven ratchets, and partial fill remaining quantity reductions) mutate the `Order` dataclass directly in memory without appending an `OrderAuditRecord` to `order.audit_trail` or `engine.audit_log`.
- **Impact**: Forensic execution logs omit stop adjustments and partial fill order resizing events.
- **Remediation**: Implement `engine.modify_order(...)` that mutates the order and appends an `OrderAuditRecord` with `trigger="ORDER_MODIFIED"`.

---

### [MINOR] CR-8: Synchronous SQLite Disk I/O & SHA256 Checkpoint Execution on Async Event Loop
- **Files**: `backend/app/main.py`: Line 241; `backend/app/core/persistence.py`: Lines 288–377
- **Description**: `state_store.save_checkpoint()` executes `BEGIN IMMEDIATE`, writes rows, runs SHA256 hashing, and commits synchronously on the main thread during async event handling without `asyncio.to_thread()`.
- **Impact**: Disk I/O latency spikes on durable volumes can block the main asyncio event loop for tens or hundreds of milliseconds.
- **Remediation**: Wrap disk-bound SQLite operations with `await asyncio.to_thread(...)`.

---

### [MINOR] CR-9: Dead Tracking Field `daily_peak_equity` in `InstitutionalRiskEngine`
- **File**: `backend/app/core/risk.py`: Lines 59, 93–94, 308
- **Description**: `daily_peak_equity` is updated on each tick in `evaluate_account_state()`, but is never referenced in any calculation or circuit breaker logic (which only measures drawdown against starting equity).
- **Impact**: Redundant code that can cause operator confusion regarding whether trailing high-water-mark drawdowns are enforced.
- **Remediation**: Either remove the unused attribute or expose it as a telemetry metric `trailing_drawdown_dollars`.

---

### [MINOR] CR-10: Inconsistent Code Duplication in Bracket Directive Application
- **Files**: `backend/app/main.py`: Lines 476–485, 1053–1057, 1878–1883
- **Description**: Working order stop modifications from directives are implemented via three duplicate logic snippets with divergent behavior (e.g. `handle_bar_event` only modifies `stop_price` and ignores `orders_to_cancel` or `new_qty`).
- **Impact**: Maintenance risk and potential desynchronization if bracket directive structures change.
- **Remediation**: Route all directive applications through `_apply_bracket_directive()`.

---

### [MINOR] CR-11: Unprotected Dictionary Iteration in `EventBus.publish`
- **File**: `backend/app/core/event_bus.py`: Line 50
- **Code**:
  ```python
  for reg_type, reg_handlers in self._subscribers.items():
  ```
- **Description**: `self._subscribers.items()` is iterated over without a shallow copy. If a handler dynamically registers or unregisters a subscriber during event dispatch, Python raises `RuntimeError: dictionary changed size during iteration`.
- **Impact**: Concurrent dynamic subscription registrations can fail event bus dispatches.
- **Remediation**: Iterate over `list(self._subscribers.items())`.

---

### [MINOR] CR-12: Missing FSM State Precondition Check in `DynamicBracketManager.activate_bracket_on_fill`
- **File**: `backend/app/core/bracket.py`: Lines 185–204
- **Description**: `activate_bracket_on_fill()` does not verify `bracket.status == BracketStatus.PENDING_ENTRY`.
- **Impact**: Calling `activate_bracket_on_fill()` on an already active or completed bracket resets its levels and submits duplicate child orders.
- **Remediation**: Add `if bracket.status != BracketStatus.PENDING_ENTRY: raise InvalidOrderStateTransitionError(...)`.

---

## 5. Summary Matrix & Actionable Roadmap

| ID | Module | Severity | Summary | Effort |
| :--- | :--- | :---: | :--- | :--- |
| **CR-1** | `adaptation.py` | **CRITICAL** | Clamp adapted stop distance to `[0.0042, 0.0380]` | Low |
| **CR-2** | `engine.py` | **CRITICAL** | Add `break` after STOP order fill in `process_quote()` | Low |
| **CR-3** | `main.py` | **CRITICAL** | Cancel working orders and pending brackets in `manual_flatten()` | Medium |
| **IN-1** | `news_ws.py` | **MAJOR** | Pass `max_size=settings.WS_MAX_MESSAGE_SIZE_BYTES` to connect | Low |
| **IN-2** | `news_ws.py` | **MAJOR** | Add per-item try-except in news batch parser | Low |
| **IN-3** | `stock_ws.py` | **MAJOR** | Add outer exception recovery in `_process_queue_loop()` | Low |
| **CR-4** | `adaptation.py` / `risk.py` | **MAJOR** | Align position allocation caps to 50% ($25,000) | Low |
| **CR-5** | `flattening.py` | **MAJOR** | Allow Phase 4 Audit retries during 15:58–16:00 window | Medium |
| **CR-6** | `bracket.py` | **MAJOR** | Enforce market price bounds in `manual_tighten_stop()` | Low |
| **IN-4** | `stock_ws.py` | **MINOR** | Handle `ConnectionClosed` in `subscribe()` | Low |
| **IN-5** | `sentiment.py` | **MINOR** | Add polarity deadband in `_classify_category()` | Low |
| **IN-6** | `vix_client.py` | **MINOR** | Calculate elapsed `age_s` in fallback print | Low |
| **CR-7** | `engine.py` | **MINOR** | Record audit entries for working order modifications | Low |
| **CR-8** | `main.py` | **MINOR** | Offload SQLite checkpoint to `asyncio.to_thread()` | Low |
| **CR-9** | `risk.py` | **MINOR** | Clean up unused `daily_peak_equity` tracking | Low |
| **CR-10** | `main.py` | **MINOR** | Consolidate directive application logic | Low |
| **CR-11** | `event_bus.py` | **MINOR** | Iterate over `list(self._subscribers.items())` | Low |
| **CR-12** | `bracket.py` | **MINOR** | Add FSM status check in `activate_bracket_on_fill()` | Low |
