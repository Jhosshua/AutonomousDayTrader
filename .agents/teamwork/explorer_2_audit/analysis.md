# Forensic Audit Analysis: Session Rollover, State Integrity, Mutual Exclusion & SQLite Round-Trip Persistence

**Explorer**: Explorer 2 (`teamwork_preview_explorer`)  
**Mission**: Forensic audit of Session Rollover Lifecycle, Mutual Exclusion Locking for Shared Symbols (`AMD`), and SQLite Checkpoint Round-Trip Persistence Fidelity for Swing Positions.  
**Target Codebase**: `backend/app/main.py`, `backend/app/core/account.py`, `backend/app/core/risk.py`, `backend/app/core/persistence.py`, `backend/app/core/flattening.py`, `backend/app/strategies/swing_panic_dip.py`.  
**Date**: 2026-09-24  

---

## Executive Summary

A rigorous forensic audit was conducted across the lifecycle of session rollovers, arm-level mutual exclusion locking, and SQLite persistence serialization within `AutonomousDayTrader`. The audit verified the core architectural protections, proved that swing positions are strictly exempt from EOD liquidation, and verified that all 6 core swing position attributes survive SQLite serialization.

However, the audit uncovered **four significant vulnerabilities and architectural defects**:
1. **Critical Market-Open Order Arrival Race Condition (`swing_panic_dip.py:453-463` & `main.py:1296-1298`)**: In production, 1-minute bars arrive symbol-by-symbol. If a staged entry candidate (e.g. `AMD`) receives its 09:30 bar milliseconds before a staged exit candidate (e.g. `LRCX`) receives its bar, the entry order immediately encounters the 2-position concurrency cap and is **permanently purged and deleted** from `StagedSwingOrderManager`, rather than waiting for the pending exit to execute.
2. **Brittle 09:30 Market-Open Execution Trigger (`main.py:1296`)**: The open execution trigger requires `bar_et.time().hour == 9 and bar_et.time().minute == 30`. If a stock has no trades in the opening minute or if its first bar arrives stamped `09:31:00`, the staged swing order is stranded and never executes.
3. **PositionState & API Schema Degradation (`events.py:272-292` & `account.py:83-102`)**: While the in-memory `Position` dataclass contains `entry_atr` and `entry_date`, the `PositionState` read-only snapshot model and `Position.to_state()` omit both fields, dropping them from WebSocket UI broadcast state and API endpoints.
4. **Historical & Daily Bar Persistence Gap (`swing_indicators.py:450-535` & `runtime_state.py:101-136`)**: `DailyBarStore` keeps aggregated daily bars exclusively in memory. The runtime SQLite checkpoint does not serialize `DailyBarStore`. In a multi-day cloud environment (such as Railway container restarts), live-aggregated daily bars from previous days are lost upon restart.

---

## Axis 1: Session Rollover & State Integrity

### 1.1 Session Rollover Trigger & Boundary Detection
In `backend/app/main.py`, session boundaries are detected via `_check_session_boundary(now_dt: datetime)` (lines 892–1020).

```python
892: def _check_session_boundary(now_dt: datetime) -> None:
893:     """Reset daily risk, flattening, and account metrics when the ET session date changes."""
894:     global last_session_date
895:     if now_dt.tzinfo is None:
896:         now_dt = now_dt.replace(tzinfo=timezone.utc)
897:     session_date = now_dt.astimezone(ET_TZ).date()
898:     if last_session_date == session_date:
899:         return
900:     is_first_observation = last_session_date is None
901:     previous_session_date = last_session_date
902:     if is_first_observation:
903:         last_session_date = session_date
904:         return
905:     if session_date < last_session_date:
906:         log.warning("Ignoring out-of-order historical event from %s (current session: %s)", session_date, last_session_date)
907:         return
```

**Key Findings**:
- **ET Timezone Precision**: `now_dt` is explicitly normalized to Eastern Time (`ET_TZ = ZoneInfo("America/New_York")`). This prevents premature rollovers at 00:00 UTC (which is 20:00 ET on the same trading day).
- **Out-of-Order Rejection**: Events with backwards timestamps are logged and discarded without corrupting session state (line 905).
- **Idempotency on Startup**: If `last_session_date` is restored from SQLite checkpoint matching the current ET date, line 898 immediately returns. If starting fresh (`is_first_observation`), it records `last_session_date` and returns without double-resetting.

### 1.2 Could Session Rollover Wipe Staged Swing Orders?
**Forensic Assessment: NO (Preserved).**
- Staged swing orders are managed by `SwingStagedOrderManager` (`backend/app/strategies/swing_panic_dip.py:110-192`), stored in `self._staged: Dict[str, StagedSwingOrder]`.
- Staged swing orders are **not** submitted to `ExecutionEngine.working_orders` at 16:00 close; they reside in `SwingStagedOrderManager` until 09:30 open.
- In `_check_session_boundary` (lines 916–924), only working orders matching `arm != TradingArm.SWING and strategy_id != "swing_panic_dip"` are cancelled.
- `_check_session_boundary` does not call `swing_staged_order_manager.clear()`.
- On server restart across midnight, `capture_runtime_state` (line 129) and `restore_runtime_state` (lines 221–225) round-trip all staged orders from SQLite.

### 1.3 `holding_days` Mechanics & Lifecycle
The `holding_days` attribute governs the 5-day time-stop exit rule (Rule 7c: Sell at 09:30 open if held for 5 trading days).

```python
# backend/app/strategies/swing_panic_dip.py:537-538
537: pos.entry_date = open_time.date()
538: pos.holding_days = 1  # Day 1 of the swing trade upon fill
```

```python
# backend/app/main.py:964-971
964:     # Advance holding_days counter for active swing positions across session boundary
965:     # Strictly on trading days (Monday=0 through Friday=4). Non-trading weekend days (Saturday=5, Sunday=6) never increment.
966:     if session_date.weekday() < 5:
967:         for sym, pos in account.positions.items():
968:             if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
969:                 pos.holding_days += 1
970:                 log.info("Advanced swing position %s holding_days to %d", sym, pos.holding_days)
```

**Lifecycle Verification**:
- **Day 1 (Monday 09:30 open fill)**: `pos.holding_days = 1`.
- **Monday 16:00 close**: `evaluate_swing_exit(holding_days=1)` -> `holding_days >= 5` is `False`.
- **Tuesday 00:00 rollover**: `session_date.weekday() < 5` -> `pos.holding_days` increments from 1 to 2.
- **Wednesday rollover**: Increments to 3.
- **Thursday rollover**: Increments to 4.
- **Friday rollover**: Increments to 5.
- **Friday 16:00 close**: `evaluate_swing_exit(holding_days=5)` -> `holding_days >= 5` is `True`! Time-stop exit staged for Monday 09:30 open.
- **Weekend Invariance**: Saturday (`weekday() == 5`) and Sunday (`weekday() == 6`) evaluate `session_date.weekday() < 5` to `False` and do **not** increment `holding_days`.

**Latent Vulnerabilities in `holding_days`**:
1. **Exchange Holiday Increment**: `session_date.weekday() < 5` tests only day-of-week, not exchange holidays (e.g., Labor Day, Memorial Day). If an incoming market news item or heartbeat arrives on a weekday holiday, `holding_days` increments even though the exchange was closed.
2. **Multi-Day Engine Downtime**: If the engine is stopped on Monday evening and restarted on Wednesday morning, `_check_session_boundary` executes once for Wednesday, executing `pos.holding_days += 1` (advancing by 1 day instead of 2).
3. **`apply_fill` Omission**: `backend/app/core/account.py:250-294` does not accept `holding_days` or `entry_date` as arguments to `apply_fill`. Newly constructed `Position` instances default to `holding_days=0`, requiring caller code to mutate attributes post-fill.

### 1.4 Flattening Exemption Verification (15:45–15:58 ET)
The 4-phase auto-flattening engine strictly bypasses swing positions:
- **Phase 1 (15:45 ET Lockout)**: `main.py:287-291` sets `is_lockout = False` for orders where `arm == TradingArm.SWING`.
- **Phase 2 (15:50 ET Working Order Purge)**: `main.py:1527-1529` explicitly checks:
  ```python
  if getattr(order, "arm", None) == TradingArm.SWING or getattr(order, "strategy_id", "") == "swing_panic_dip":
      continue
  ```
- **Phase 3 (15:55 ET Mandatory Liquidation)**: `main.py:1547-1548`:
  ```python
  if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
      continue
  ```
- **Phase 4 (15:58 ET Audit)**: `flattening.py:233-241` filters out swing positions and working orders:
  ```python
  intraday_positions = {
      sym: pos for sym, pos in open_positions.items()
      if getattr(pos, "arm", None) not in ("SWING", TradingArm.SWING)
      and getattr(pos, "strategy_id", "") != "swing_panic_dip"
  }
  ```
  And `main.py:1590-1595` prevents setting `account.status = AccountStatus.EOD_FLAT` if active swing positions exist overnight.

---

## Axis 2: Mutual Exclusion Across Arms (`AMD`)

### 2.1 Locking Mechanism for Shared Symbols
`AMD` is a member of both `WATCHLIST_SYMBOLS` (Intraday) and `CERTIFIED_SWING_SYMBOLS` (Swing). Mutual exclusion is enforced via a two-layer reservation system:
1. `swing_reserved_symbols: Set[str]` in `backend/app/main.py:115`.
2. `is_symbol_reserved_for_swing(symbol, acct, eng)` in `backend/app/main.py:128–154`.

```python
128: def is_symbol_reserved_for_swing(
129:     symbol: str,
130:     acct: Optional[PaperTradingAccount] = None,
131:     eng: Optional[Any] = None,
132: ) -> bool:
133:     """Check if a symbol is currently reserved, actively held, or has working orders in the swing arm."""
134:     sym = symbol.upper()
135:     if sym in swing_reserved_symbols:
136:         return True
137:     target_acct = acct or globals().get("account")
138:     if target_acct and hasattr(target_acct, "positions"):
139:         pos = target_acct.positions.get(sym)
140:         if pos is not None and (
141:             getattr(pos, "arm", None) == TradingArm.SWING
142:             or getattr(pos, "strategy_id", "") == "swing_panic_dip"
143:         ):
144:             return True
145:     target_engine = eng or globals().get("engine")
146:     if target_engine and hasattr(target_engine, "working_orders"):
147:         for w_order in target_engine.working_orders.values():
148:             if w_order.symbol.upper() == sym and (
149:                 getattr(w_order, "arm", None) == TradingArm.SWING
150:                 or getattr(w_order, "strategy_id", "") == "swing_panic_dip"
151:             ):
152:                 return True
153:     return False
```

### 2.2 Pre-Trade Risk Gate Mutual Exclusion
In `backend/app/main.py:262–284`, `pre_trade_risk_validator(order)` checks every order prior to submission:
- **Swing Orders**: Blocked if an intraday position or working order exists for `sym` (lines 266–279).
- **Intraday Orders**: Blocked if `is_symbol_reserved_for_swing(sym)` returns `True` (lines 280–283).

### 2.3 Overnight Session Boundary Persistence of Locks
- When `AMD` qualifies at 16:00 close, `reserve_symbol_cb("AMD")` is called (`swing_panic_dip.py:365`), inserting `"AMD"` into `swing_reserved_symbols`.
- `_check_session_boundary` does **not** clear `swing_reserved_symbols`.
- On server restart, `swing_reserved_symbols` is serialized to SQLite (`runtime_state.py:130`) and restored (`runtime_state.py:227`).
- While held, `pos.arm == TradingArm.SWING` prevents intraday entry even if the reserved set were cleared.
- `"AMD"` is released only when the position is fully closed (`swing_panic_dip.py:445, 629, 777`) or if staged entry execution fails (`swing_panic_dip.py:461, 476, 506`).

### 2.4 CRITICAL DEFECT: Symbol-by-Symbol 09:30 Market-Open Race Condition
**Severity**: **CRITICAL**  
**Locations**: `backend/app/main.py:1296–1298` and `backend/app/strategies/swing_panic_dip.py:453–463`.

#### Defect Mechanism
In live production, 1-minute bars arrive asynchronously over the WebSocket connection.
In `backend/app/main.py`:
```python
1296:     if bar_et.time().hour == 9 and bar_et.time().minute == 30:
1297:         if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
1298:             swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)
```
Notice that `execute_market_open` is called with only `{bar_sym: bar.open}` (a single symbol).

Consider this real-world scenario:
1. At 16:00 close on Monday, account holds 2 swing positions (`LRCX` and `KLAC`), reaching the 2-position cap.
2. `LRCX` triggers a 5-day time exit, staged for SELL at Tuesday 09:30 open.
3. `AMD` qualifies for entry, staged for BUY at Tuesday 09:30 open (budgeting that `LRCX` will exit).
4. At Tuesday 09:30:00, market opens.
5. **The race condition**: `AMD`'s 09:30:00 bar arrives at `09:30:00.050`. `LRCX`'s bar arrives at `09:30:00.250`.
6. When `AMD` arrives:
   `execute_market_open({"AMD": 150.0}, timestamp)` runs.
   - Exits step: Looks for `LRCX` open price in `open_prices`. `open_prices` only has `{"AMD": 150.0}`. `LRCX` exit cannot execute.
   - Entries step: `active_count = len(self.get_active_swing_positions())` evaluates to `2` (`LRCX` and `KLAC` still open).
   - Lines 454–462:
     ```python
     if active_count >= self.max_concurrent_positions:
         log.warning(f"Concurrency cap reached ({active_count}/{self.max_concurrent_positions}): Cannot enter swing trade on {sym}")
         self.staged_manager.remove_staged_order(entry_order.order_id)
         if self.release_symbol_cb:
             self.release_symbol_cb(sym)
         continue
     ```
   - **`AMD`'s staged order is permanently deleted and its reservation is released!**
7. 200 milliseconds later (`09:30:00.250`), `LRCX`'s bar arrives. `LRCX` exits. A slot is now free.
8. **Result**: `AMD` never enters. It was permanently discarded because its bar arrived a fraction of a second before `LRCX`.

#### Remediation Required
1. In `swing_panic_dip.py:execute_market_open`: If `active_count >= self.max_concurrent_positions`, check if there are pending `staged_exits` still awaiting open execution. If pending exits exist, **do not remove the staged entry order**; simply defer execution (`continue` without removal).
2. In `main.py:1296`: After any staged exit completes, immediately evaluate whether any staged entries can now be executed.
3. Open Execution Window Tolerance: Broaden the trigger from `minute == 30` to `9:30 <= bar_et.time() < 9:35` or first available bar on or after 09:30, ensuring illiquid or delayed opening bars do not miss the execution trigger.

---

## Axis 3: Persistence Round-Trip Fidelity in SQLite

### 3.1 Forensic Test Results: Swing Position Round-Trip
To test persistence fidelity, an automated round-trip probe was executed through `capture_runtime_state`, `TradingStateStore.save_checkpoint`, SQLite database commit, SQLite database load, and `restore_runtime_state`.

**Target Fields Tested**:
- `entry_date`: `date(2026, 9, 21)` -> Saved as ISO string -> Restored as `datetime.date(2026, 9, 21)` (`class 'datetime.date'`) -> **PASS**
- `entry_atr`: `4.25` -> Saved as float -> Restored as `4.25` (`class 'float'`) -> **PASS**
- `stop_loss_price`: `140.0` -> Saved as float -> Restored as `140.0` -> **PASS**
- `holding_days`: `2` -> Saved as integer -> Restored as `2` -> **PASS**
- `arm`: `TradingArm.SWING` -> Saved as type-tagged enum -> Restored as `TradingArm.SWING` -> **PASS**
- `strategy_id`: `"swing_panic_dip"` -> Saved as string -> Restored as `"swing_panic_dip"` -> **PASS**
- `swing_staged_orders`: `[StagedSwingOrder(symbol="MU", daily_atr=3.5, ...)]` -> Restored with full fidelity -> **PASS**
- `swing_reserved_symbols`: `{"AMD", "MU"}` -> Restored as `Set[str]` -> **PASS**

### 3.2 Schema Degradation in `PositionState`
**Severity**: **MAJOR**  
**Locations**: `backend/app/models/events.py:272–292` and `backend/app/core/account.py:83–102`.

#### Defect Mechanism
In `backend/app/models/events.py`:
```python
272: @dataclass(frozen=True)
273: class PositionState:
274:     """Read-only position snapshot."""
275:     symbol: str
276:     side: str                            # LONG or SHORT
277:     shares: int
278:     avg_entry_price: float
279:     market_price: float
280:     market_value: float
281:     cost_basis: float
282:     unrealized_pnl: float
283:     unrealized_pnl_pct: float
284:     realized_pnl: float
285:     fees_paid: float
286:     opened_at: datetime
287:     updated_at: datetime
288:     arm: str = "INTRADAY"
289:     strategy_id: str = "MANUAL"
290:     holding_days: int = 0
291:     stop_loss_price: Optional[float] = None
```
In `backend/app/core/account.py`:
```python
83:     def to_state(self) -> PositionState:
84:         return PositionState(
85:             symbol=self.symbol,
...
100:             holding_days=self.holding_days,
101:             stop_loss_price=self.stop_loss_price,
102:         )
```
**Impact**:
Neither `PositionState` nor `Position.to_state()` includes `entry_atr` or `entry_date`. When `account.get_snapshot()` generates the `AccountState` broadcast to UI WebSockets or API consumers, `entry_atr` and `entry_date` are dropped, creating a schema degradation between internal state and public API snapshots.

### 3.3 Persistence Gap in `DailyBarStore`
**Severity**: **MAJOR**  
**Locations**: `backend/app/strategies/swing_indicators.py:448–535` and `backend/app/core/runtime_state.py:101–136`.

#### Defect Mechanism
At 16:00 close:
1. `daily_bar_aggregator.finalize_all(eval_date)` constructs a finalized `DailyBar` from the day's minute bars and appends it to `DailyBarStore._bars` in memory (`swing_indicators.py:508-520`).
2. `capture_runtime_state` is invoked to persist runtime state to SQLite (`main.py:1602`).
3. However, `capture_runtime_state` does **not** include `DailyBarStore._bars` in the serialized dictionary.
4. If the server or Railway container restarts overnight:
   `daily_bar_store` reloads from `settings.DAILY_BARS_SEED_PATH` (`main.py:347`).
   The newly aggregated daily bar from the prior session is completely missing from `DailyBarStore`.
5. On the following trading day at 16:00 close, calculations for 5-day SMA, 200-day SMA, 14-day ATR, and 60-day relative strength are missing yesterday's bar!

---

## Proposed Code Fixes & Diff Blueprints

### Fix 1: Resolve Market-Open Race Condition (`backend/app/strategies/swing_panic_dip.py`)

```python
<<<<
                active_count = len(self.get_active_swing_positions())
                if active_count >= self.max_concurrent_positions:
                    log.warning(
                        f"Concurrency cap reached ({active_count}/{self.max_concurrent_positions}): "
                        f"Cannot enter swing trade on {sym}"
                    )
                    self.staged_manager.remove_staged_order(entry_order.order_id)
                    if self.release_symbol_cb:
                        self.release_symbol_cb(sym)
                    continue
====
                active_count = len(self.get_active_swing_positions())
                if active_count >= self.max_concurrent_positions:
                    # If staged exits are still pending open execution, do NOT drop the staged entry!
                    # The entry will execute once the exit frees a slot.
                    pending_exits = self.staged_manager.get_staged_exits()
                    if pending_exits:
                        log.info(
                            f"Concurrency cap reached ({active_count}/{self.max_concurrent_positions}) on {sym}, "
                            f"but {len(pending_exits)} staged exit(s) still pending. Retaining staged entry."
                        )
                        continue
                    log.warning(
                        f"Concurrency cap reached ({active_count}/{self.max_concurrent_positions}): "
                        f"Cannot enter swing trade on {sym}"
                    )
                    self.staged_manager.remove_staged_order(entry_order.order_id)
                    if self.release_symbol_cb:
                        self.release_symbol_cb(sym)
                    continue
>>>>
```

### Fix 2: Broaden Market-Open Execution Window & Chain Exits to Entries (`backend/app/main.py`)

```python
<<<<
    # 09:30 ET Market Open Execution for Staged Swing Orders
    # Execute open orders strictly for this symbol when THAT symbol's 09:30 open bar arrives
    if bar_et.time().hour == 9 and bar_et.time().minute == 30:
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)
====
    # 09:30 ET Market Open Execution Window (09:30:00 - 09:35:00 ET) for Staged Swing Orders
    # Execute open orders for this symbol or any pending entry orders if an exit just freed a slot
    if (bar_et.time().hour == 9 and 30 <= bar_et.time().minute < 35):
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            open_price_map = {bar_sym: bar.open}
            # Also supply latest open prices for any staged entries that are waiting
            for stg_ent in swing_staged_order_manager.get_staged_entries():
                if stg_ent.symbol in latest_market_prices and stg_ent.symbol not in open_price_map:
                    open_price_map[stg_ent.symbol] = latest_market_prices[stg_ent.symbol]
            swing_strategy_engine.execute_market_open(open_price_map, bar.timestamp)
>>>>
```

### Fix 3: Add `entry_atr` and `entry_date` to `PositionState` & `Position.to_state()` (`models/events.py` & `account.py`)

In `backend/app/models/events.py`:
```python
<<<<
    holding_days: int = 0
    stop_loss_price: Optional[float] = None
====
    holding_days: int = 0
    stop_loss_price: Optional[float] = None
    entry_atr: Optional[float] = None
    entry_date: Optional[date] = None
>>>>
```

In `backend/app/core/account.py`:
```python
<<<<
            holding_days=self.holding_days,
            stop_loss_price=self.stop_loss_price,
        )
====
            holding_days=self.holding_days,
            stop_loss_price=self.stop_loss_price,
            entry_atr=self.entry_atr,
            entry_date=self.entry_date,
        )
>>>>
```

### Fix 4: Persist `DailyBarStore` State in Runtime Checkpoint (`backend/app/core/runtime_state.py`)

In `capture_runtime_state`:
```python
<<<<
        "swing_staged_orders": [o.to_dict() if hasattr(o, "to_dict") else o for o in (swing_staged_orders or [])],
        "swing_reserved_symbols": list(swing_reserved_symbols or []),
    }
====
        "swing_staged_orders": [o.to_dict() if hasattr(o, "to_dict") else o for o in (swing_staged_orders or [])],
        "swing_reserved_symbols": list(swing_reserved_symbols or []),
        "daily_bars": {
            sym: [b.to_dict() if hasattr(b, "to_dict") else b for b in bars]
            for sym, bars in getattr(daily_bar_store, "_bars", {}).items()
        } if daily_bar_store is not None else {},
    }
>>>>
```

---

## Conclusion & Verification Summary

1. **Session Rollover**: Strictly preserves swing holdings across overnight boundaries; 4-phase flattening does not liquidate or interfere with swing positions.
2. **Mutual Exclusion**: Locks shared symbol `AMD` reliably at staging and fill times via `pre_trade_risk_validator`, surviving overnight boundaries and restarts.
3. **Identified Race Vulnerability**: Asynchronous symbol bar arrivals at 09:30 ET can cause staged entries to be purged if evaluated before pending exits. Remediation provided above.
4. **Persistence Round-Trip**: All 6 swing position fields survive SQLite round-trip; `PositionState` snapshot schema degradation and `DailyBarStore` restart persistence gap identified and remediated.
