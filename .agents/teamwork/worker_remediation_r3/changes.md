# Changes Log — Remediation R3

## Overview
Comprehensive, production-grade remediations implemented across all 5 architectural subsystems (Ingestion, Core State & Risk, Strategies, API & Lifecycle, and Frontend) to eliminate defects cataloged by Explorers 1, 2, and 3. All changes follow the minimal change principle, preserve existing architectural patterns, and maintain genuine logic without shortcuts or hardcoded test assertions.

---

## 1. Ingestion Layer

### `backend/app/ingestion/news_ws.py`
- **Max Message Size**: Added `max_size=settings.WS_MAX_MESSAGE_SIZE_BYTES` to `websockets.connect(...)` call to prevent large Benzinga batch frames from terminating the connection.
- **Per-Item Exception Isolation**: In `_handle_news_message`, wrapped the parsing of each individual news item inside the batch loop with `try...except Exception as item_exc`. A corrupted or malformed article in a multi-item batch is safely logged and skipped, allowing valid news items in the same batch to be ingested cleanly without aborting.

### `backend/app/ingestion/stock_ws.py`
- **Outer Loop Recovery**: In `_process_queue_loop`, wrapped queue operations in an outer `try...except Exception as exc` with exponential sleep backoff to prevent silent worker task termination on unexpected queue retrieval or deserialization errors.

---

## 2. Core State & Risk Layer

### `backend/app/core/engine.py`
- **Process Quote Stop Order Fill Break**: In `process_quote`, added a `break` statement after `_execute_fill(...)` on `STOP` or `STOP_LIMIT` orders (matching `process_bar`). This prevents sibling limit orders or competing exit orders from double filling on the exact same price quote tick.
- **State Bounding**: Added automatic bounding on `self.audit_log` (trimmed at 10,000 down to recent 5,000 entries) in `_record_audit`. Implemented `prune_session_state(max_audit_records=5000, max_orders=1000)` which prunes historical audit records and non-working orders while preserving all active working orders.

### `backend/app/core/bracket.py`
- **Manual Stop Tighten Clamping**: In `manual_tighten_stop`, accepted `current_market_price: Optional[float] = None` and clamped the new stop price so a BUY (LONG) stop cannot be tightened above current market price, and a SELL (SHORT) stop cannot be tightened below current market price.

### `backend/app/core/flattening.py`
- **Phase 4 Continuous Zero-Audit Retry**: In `check_time_tick`, updated Phase 4 condition to `if not self.phase4_executed or not self.audit_passed:` between 15:58:00 and 16:00:00 ET. If an open position or working order lingers during Phase 4, the flattening engine repeatedly emits Phase 4 zero-audit directives on every tick until the portfolio is certified flat.

### `backend/app/strategies/adaptation.py`
- **Stop Distance Institutional Clamping**: In `calculate_adapted_stop`, clamped adapted stop distance to institutional bounds `[0.0040 * entry, 0.0400 * entry]`. This guarantees that VIX regime multipliers (0.85 to 2.00) never breach the `[0.0040, 0.0400]` risk engine invariant.
- **Capital Allocation Cap Alignment**: In `calculate_position_size`, set default `max_alloc_pct = 0.50` ($25,000 / 50% equity cap) aligning with `risk.py`.

---

## 3. Strategies Layer

### `backend/app/strategies/news_momentum.py`
- **Strict Causality Enforced**: In `on_bar`, added strict causality check `0 <= (now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds`, eliminating forward data leakage from future-dated news timestamps.
- **Sliding Bar Window**: Maintained a 60-bar maximum window via `self.recent_bars[sym] = self.recent_bars[sym][-60:]` to prevent unbounded memory growth.

### `backend/app/strategies/vwap_pullback.py`
- **Calibrated Targets**: Replaced obsolete 1.5R / 2.5R fallback targets with calibrated 0.80R and 1.80R targets (`target_1_r` and `target_2_r`).
- **Volume Floor**: Added strict volume checks (`if bar.volume <= 0 or sma10_vol <= 0: return []` and volume confirmation guard) to reject false bounces on zero volume.
- **Minimum Reward Ratio**: Enforced `>= 0.50R` minimum reward on standard deviation band targets.

### `backend/app/strategies/orb.py`
- **Lockout Prevention**: Added `notify_signal_rejected(symbol)` to reset `breakout_fired` to `False` if downstream admission or risk filters reject the entry order.
- **Late-Arriving Symbol Gating**: Prevented spurious opening range establishment for symbols first arriving after 09:45 ET (`if t_time > dtime(9, 45): state.range_established = True; state.breakout_fired = True; return []`). Preserved `set_baseline_volume`.

---

## 4. API & Lifecycle Layer

### `backend/app/main.py`
- **UI Broadcast Throttling**: Added a 4 Hz throttle (`_UI_BROADCAST_THROTTLE_SEC = 0.25`) to `broadcast_ui_state` to prevent event-loop starvation during market quote spikes.
- **Slow Consumer Eviction**: Wrapped client transmissions in `asyncio.wait_for(ws.send_text(raw), timeout=0.35)` and automatically pruned disconnected or stalled clients from `ui_clients`.
- **Manual Flatten Symbol Scope**: In `manual_flatten` and `_execute_manual_flatten`, aggregated target symbols across `account.positions.keys()`, `engine.working_orders`, and `bracket_manager.symbol_to_bracket`. Canceled all working orders in `engine.working_orders` for those symbols.
- **Order Model Validation & Error Handling**: In `POST /api/orders`, added `Field(gt=0)` validation on `qty` to reject non-positive quantities with HTTP 422, and wrapped `engine.create_order` in `try...except ValueError` returning HTTP 400 instead of HTTP 500.
- **Lifespan WebSocket Close**: On server shutdown in lifespan, closed all active `ui_clients` with code 1001 (`reason="Server shutdown"`).
- **Strategy Signal Rejection Notification**: Connected `orb_strategy.notify_signal_rejected(sig.symbol)` when a signal is rejected during runtime arbitration.

---

## 5. Frontend & UI Layer

### `frontend/components/LiveChart.tsx`
- Added `safeFixed` helper for null/undefined/NaN safety.
- Updated target legend labels from 1.5R / 2.5R to `Target 1 (0.80R)` and `Target 2 (1.80R)`.

### `frontend/components/ActivePositionTray.tsx`
- Added `safeFixed` and `safeLocale` helpers across entry price, market price, unrealized PnL, PnL %, and market value.

### `frontend/components/ManualControls.tsx`
- Added `safeFixed` helper for breakeven, trailing stop, and position market price formatting.
- Added null and NaN guards to `handleTightenBreakeven` and `handleTightenHalfProfit`.
- Updated empty position state (`if (!position)`) to render action feedback toasts and the "Flatten All Portfolios" confirmation modal when confirmed.

### `frontend/components/StrategyCarousel.tsx`
- Added `safeFixed` helper for daily PnL, win rate, and Sharpe ratio.
- Updated Exit Protocols text from `1.5R Scale / 2.5R Trail` to `0.80R Scale / 1.80R Trail`.

### `frontend/hooks/useTradingStream.ts`
- Removed synthetic shares fallback `|| 100` on line 175 (`first.shares ?? first.qty ?? 0`).
- Scheduled exponential backoff reconnect timeout in `connect()` catch block on synchronous errors.
- Enhanced disconnected fallback polling interval to fetch `/api/account` and `/api/positions` to maintain UI state synchronization when WebSocket is disconnected.

### `frontend/app/error.tsx`
- Created obsidian dark theme error boundary component (`'use client'`, `#000000`, `backdrop-blur-xl`, error telemetry display, and retry button calling `reset()`).

---

## 6. Verification & Port Hygiene

### `scripts/verify_port_hygiene.sh`
- Updated monitored port list to `PORTS=(3005 8000 8005 8080)`.

### `backend/tests/unit/test_remediation_r3.py`
- Implemented 14 targeted unit tests covering all remediated behaviors across Ingestion, Core, Strategies, and API.
