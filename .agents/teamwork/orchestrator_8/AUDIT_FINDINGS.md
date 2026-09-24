# Forensic Codebase & Architecture Audit Findings: AutonomousDayTrader Swing & Intraday Integration

**Audit Date**: 2026-09-24  
**Audit Team**:  
- Explorer 1: Timing, Market-Open Execution & Staged Order Idempotency (`explorer_1_audit`)
- Explorer 2: Session Rollover, State Integrity, Mutual Exclusion & SQLite Round-Trip (`explorer_2_audit`)
- Explorer 3: Blocking I/O, Async Loop Safety & Simulation Readiness (`explorer_3_audit`)
- Orchestrator: Project Orchestrator 8 (`orchestrator_8`)

---

## Executive Summary

A multi-agent forensic audit was conducted across the 2-Day Panic Dip swing trading engine, the intraday day trading engine, and their shared runtime integration within `AutonomousDayTrader`. The audit confirmed that the fundamental architecture is sound: 4-phase intraday flattening strictly protects swing positions, mutual exclusion locks `AMD` across overnight sessions, and core position state survives SQLite serialization.

However, the audit identified **5 CRITICAL vulnerabilities** and **5 MAJOR defects** that cause order marooning, race conditions, slot overflow, event-loop starvation, and cross-arm liquidation under live market conditions.

---

## Catalog of Verified Defects

### Defect 1 (CRITICAL): 09:30 ET Market-Open Trigger Brittleness & Order Marooning
- **Location**: `backend/app/main.py:1296–1298`
- **Pathology**: Open execution is gated strictly on `bar_et.time().hour == 9 and bar_et.time().minute == 30`. In live trading and paper simulation, the 09:30:00 bar can be delayed by exchange opening crosses, auction volume, or network jitter, arriving stamped 09:31:00 or later. When this occurs, the condition evaluates to `False`, bypassing open execution entirely. Staged orders have no expiration TTL, remaining stranded in `SwingStagedOrderManager` and persisting across days until an accidental future execution.
- **Remediation**:
  1. Broaden the open execution window to `(hour == 9 and 30 <= minute <= 45)` or the first regular-hours bar on or after 09:30:00 ET.
  2. Implement an expiration sweep at 09:45:00 ET: any staged order unexecuted past 09:45 ET is cancelled, logged, and its reserved symbol released.

### Defect 2 (CRITICAL): Concurrency Annihilation Race Condition at Open
- **Location**: `backend/app/strategies/swing_panic_dip.py:453–463` and `backend/app/main.py:1296–1298`
- **Pathology**: In production, 1-minute bars arrive asynchronously symbol-by-symbol over WebSocket. If an account holds 2 positions (at the 2-position cap) and 1 position has a staged exit at open while another symbol has a staged entry at open, the arrival order dictates execution:
  - If the staged entry symbol's bar arrives milliseconds before the exiting symbol's bar, `execute_market_open` runs for the entry.
  - `active_count = len(self.get_active_swing_positions())` evaluates to 2 (because the exit has not yet received its bar).
  - Lines 454–462 trigger: `active_count >= 2` causes the engine to **permanently delete the staged entry order and release its symbol reservation**, rather than waiting for the exit to complete!
- **Remediation**:
  1. In `execute_market_open`: If `active_count >= max_concurrent_positions`, check `has_pending_exits = len(self.staged_manager.get_staged_exits()) > 0`. If pending exits exist, **defer the entry order** (`continue` without deletion).
  2. When an exit fills in `execute_market_open`, immediately re-evaluate any deferred staged entry orders against the newly freed slot.

### Defect 3 (CRITICAL): Staged Order Idempotency Breakdown & Position Cap Breach
- **Location**: `backend/app/strategies/swing_panic_dip.py:302–362`
- **Pathology**: In `evaluate_market_close`, available slots are computed as:
  `available_slots = self.max_concurrent_positions - len(surviving_positions)`.
  This does **not** subtract already staged entry orders (`self.staged_manager.get_staged_entries()`). If `evaluate_market_close` is called multiple times between 16:00 and 09:30 (e.g. repeated scans, clock ticks, or post-restart evaluations):
  - Previously staged symbols are skipped, but `available_slots` is not decremented.
  - Subsequent qualifying symbols are staged into the "available" slots.
  - Empirically proven: up to 5 symbols ($125,000 notional) are staged, committing 250% of the $50,000 account and locking out `AMD` from intraday trading.
- **Remediation**:
  1. `staged_entries = self.staged_manager.get_staged_entries()`
  2. `available_slots = self.max_concurrent_positions - len(surviving_positions) - len(staged_entries)`
  3. Enforce strict idempotency: if a symbol is already staged for entry, do not stage again, and decrement slots when evaluating initial staging.

### Defect 4 (CRITICAL): Blocking I/O in Async Event Loop
- **Location**: `backend/app/strategies/earnings_calendar.py:246–274`
- **Pathology**: `urllib.request.urlopen(req, timeout=2.0)` is executed directly inside `async def refresh_from_remote()`. In Python asyncio, synchronous C-socket calls block the single-threaded event loop. If the remote endpoint is slow, unreachable, or DNS hangs, the entire server pauses for 2–30 seconds, causing dropped WebSockets, delayed stop-loss executions, and HTTP `/health` probe timeouts.
- **Remediation**:
  1. Replace `urllib.request` with asynchronous non-blocking HTTP using `httpx.AsyncClient(timeout=3.0)` or `asyncio.to_thread`.
  2. Ensure all network exceptions are cleanly trapped and logged with fallback to local cached data.

### Defect 5 (CRITICAL): Cross-Arm Circuit Breaker Contamination Liquidating Swing Holdings
- **Location**: `backend/app/main.py:880–887`
- **Pathology**: When the intraday hard daily loss circuit breaker ($1,500 drawdown) trips, the liquidation loop iterates over all positions:
  ```python
  for sym, pos in list(account.positions.items()):
      side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
      ...
      liq_order = engine.create_order(...)
      engine.submit_order(liq_order)
  ```
  It does **not** filter `pos.arm != TradingArm.SWING`. Consequently, an intraday loss breach inadvertently forces market liquidation of multi-day swing positions, violating arm isolation.
- **Remediation**:
  Add `if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip": continue` to the circuit breaker liquidation loop.

### Defect 6 (MAJOR): Zero Slippage Bypass & Rule 6 Stop-Loss Anchoring
- **Location**: `backend/app/strategies/swing_panic_dip.py:427, 526, 614, 772`
- **Pathology**: All swing entry and exit fills call `self.execution_engine._execute_fill(..., slippage=0.0)`. This completely bypasses the microstructure slippage model in `ExecutionEngine.calculate_slippage`, resulting in idealized paper fills. Furthermore, on line 481, the stop-loss price for Rule 6 is calculated as `open_price - 2.5 * daily_atr`, anchoring to the unadjusted open price rather than the actual filled price.
- **Remediation**:
  1. Apply realistic slippage via `ExecutionEngine.calculate_slippage` (or realistic basis-point model calibrated to symbol volatility).
  2. Calculate Rule 6 stop-loss strictly as `fill.fill_price - 2.5 * daily_atr`.

### Defect 7 (MAJOR): Public Schema Degradation in `PositionState`
- **Location**: `backend/app/models/events.py:272–292` and `backend/app/core/account.py:83–102`
- **Pathology**: The internal `Position` dataclass tracks `entry_atr: Optional[float]` and `entry_date: Optional[date]`. However, the read-only `PositionState` dataclass and `Position.to_state()` method omit both fields. When snapshots are serialized for WebSocket broadcast or API consumers, these critical metrics are dropped.
- **Remediation**:
  Add `entry_atr: Optional[float] = None` and `entry_date: Optional[str] = None` to `PositionState`, and map them in `Position.to_state()`.

### Defect 8 (MAJOR): Remote Calendar Non-Persistence & Missing Configuration
- **Location**: `backend/app/strategies/earnings_calendar.py`, `backend/app/config.py`, `backend/app/main.py`
- **Pathology**: `EarningsCalendar.refresh_from_remote()` does not persist fetched events to disk. On container restarts, fetched updates are lost, reverting to the static seed fixture. Furthermore, `config.py` has no `EARNINGS_CALENDAR_REMOTE_URL` setting.
- **Remediation**:
  1. Add atomic disk cache writing (`save_cache_file()`) in `EarningsCalendar`.
  2. Add `EARNINGS_CALENDAR_REMOTE_URL: Optional[str] = None` in `config.py` and wire it into `EarningsCalendar` initialization in `main.py`.

### Defect 9 (MAJOR): `DailyBarStore` Data Loss Across Restarts
- **Location**: `backend/app/strategies/swing_indicators.py:448–535` and `backend/app/core/runtime_state.py:101–136`
- **Pathology**: Live aggregated daily bars are stored only in memory in `DailyBarStore._bars`. Runtime SQLite checkpoints do not include daily bars. If a Railway container restarts overnight, yesterday's aggregated bar is missing from indicator calculations (200 SMA, 5 SMA, 60d RS, ATR).
- **Remediation**:
  Include recent daily bars in `capture_runtime_state` and restore them into `DailyBarStore` upon server initialization in `restore_runtime_state`.

### Defect 10 (MAJOR): Multi-Day Concurrent Dry Run Shortfall
- **Location**: `scripts/run_integrated_swing_dry_run.py`
- **Pathology**: The existing swing dry run script manually appends daily bars and simulates a single synthetic trade with zero slippage. It does not stream continuous 1-minute intraday bars concurrently through `main.handle_bar_event()` across both Intraday (ORB, VWAP, News, MR) and Swing arms from the shared $50,000 account pool over multiple sessions.
- **Remediation**:
  Build and execute a multi-day concurrent dry run harness simulating 5+ consecutive trading sessions with shared capital, verifying all order lifecycles, arm isolation, and producing `SWING_FULL_E2E_DRY_RUN_REPORT.md`.

---

## Action Plan for Milestone 2 (Remediation & Hardening)

1. **Remediation Worker Dispatch**:
   - Patch `backend/app/main.py` (open window tolerance, circuit breaker arm filtering, deferred entry evaluation).
   - Patch `backend/app/strategies/swing_panic_dip.py` (race condition resolution, idempotency fix, slippage integration, fill-anchored stop).
   - Patch `backend/app/strategies/earnings_calendar.py` and `backend/app/config.py` (async non-blocking HTTP via `httpx`, durable disk cache).
   - Patch `backend/app/models/events.py` and `backend/app/core/account.py` (`PositionState` schema fidelity).
   - Patch `backend/app/core/runtime_state.py` and `backend/app/strategies/swing_indicators.py` (daily bar checkpointing).
   - Extend unit and regression test suites in `backend/tests/` to verify all 10 fixes.

2. **Verification Gate**:
   - Dispatch Reviewer 1 & Reviewer 2.
   - Dispatch Challenger 1 & Challenger 2.
   - Dispatch Forensic Auditor for integrity verification.
