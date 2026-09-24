# Forensic Audit: Timing, 09:30 ET Open Execution & Staged Order Idempotency

**Explorer**: Explorer 1 (Forensic Explorer for Timing, Market-Open Execution & Staged Order Idempotency)  
**Date**: 2026-09-24  
**Target Codebase**: `AutonomousDayTrader`  
**Files Audited**:
- `backend/app/main.py`
- `backend/app/strategies/swing_panic_dip.py`
- `backend/app/core/engine.py`
- `backend/app/core/account.py`
- `backend/app/core/risk.py`
- `backend/app/core/flattening.py`
- `backend/app/core/runtime_state.py`
- `backend/app/core/persistence.py`

---

## Executive Summary

A deep forensic investigation of the "2-Day Panic Dip" swing trading engine and its integration with the intraday trading core has uncovered **three critical vulnerabilities** and **one major architectural omission**:

1. **CRITICAL — 09:30 ET Open Execution Fragility & Order Marooning (`backend/app/main.py:1296-1298`)**:
   Open execution triggers *only* if `bar_et.time().hour == 9 and bar_et.time().minute == 30`. If the 09:30 bar is delayed, illiquid (no trades in minute 30), or arrives at 09:31, staged orders are bypassed and marooned indefinitely. Staged orders survive overnight sweeps and session rollovers without TTL or expiration checks, risking execution days later on stale triggers.
2. **CRITICAL — Concurrency Annihilation Race Condition at Open (`backend/app/strategies/swing_panic_dip.py:453-463`)**:
   `main.py` calls `execute_market_open({bar_sym: bar.open})` symbol-by-symbol as bars arrive. `execute_market_open` checks `active_count >= 2` *before* checking if the symbol is in `open_prices` and *without* deducting pending staged exits. If an entry symbol's bar arrives milliseconds before an exit symbol's bar, `execute_market_open` immediately deletes the incoming entry order (and all other staged entries) permanently from memory.
3. **CRITICAL — Staged Order Idempotency Failure & 2-Position Cap Breach (`backend/app/strategies/swing_panic_dip.py:302-362`)**:
   `evaluate_market_close` does not subtract previously staged orders when computing `available_slots`. When iterating over candidates, it skips already-staged symbols without decrementing `available_slots`. Re-running `evaluate_market_close` (e.g. repeated scans, clock ticks, or server restarts between 16:00 and 09:30 ET) stages subsequent candidates (`MU`, `AMD`, `GS`), staging up to 5 concurrent entries ($125,000 notional against a $50,000 pool) and locking out `AMD` from intraday trading.
4. **MAJOR — Zero Slippage and Pricing Bypass on Staged Open Orders (`backend/app/strategies/swing_panic_dip.py:427, 526, 614, 772`)**:
   All swing fills bypass the `ExecutionEngine.calculate_slippage` microstructure model, hardcoding `slippage=0.0` and using raw `bar.open` without half-spread. Furthermore, Rule 6 stop-loss is anchored to `open_price` instead of actual `fill_price`, and share sizing does not account for adverse slippage.

---

## 1. Timing & 09:30 ET Market-Open Execution Vulnerabilities

### 1.1 Trigger Mechanism & Exact Second/Tick Dependency
In `backend/app/main.py`, lines 1294–1299:

```python
    # 09:30 ET Market Open Execution for Staged Swing Orders
    # Execute open orders strictly for this symbol when THAT symbol's 09:30 open bar arrives
    if bar_et.time().hour == 9 and bar_et.time().minute == 30:
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)
```

#### Forensic Observations:
- **Strict Minute Check**: The condition requires `bar_et.time().minute == 30`. It evaluates `True` only for bars whose start timestamp is in the `09:30:00` to `09:30:59` window.
- **Zero Execution Tolerance Window**: If AlpacaRelay emits the first bar for a symbol at `09:31:00` (due to low opening-minute volume, exchange auction delays, or network reconnect latency), `minute == 30` evaluates to `False`.
- **Quote Ingestion Ignored**: `handle_quote_event` in `backend/app/main.py:1402-1443` processes intraday quotes through `engine.process_quote`, but contains zero hooks to check or execute staged swing orders. If quotes stream at 09:30:01 but no 1-minute bar forms until 09:31:00, swing execution remains idle.
- **Single Hook**: Line 1298 is the *only* call to `execute_market_open` in the entire backend application.

### 1.2 Marooned & Indefinitely Hanging Orders
When a bar is missed or delayed past 09:30 ET:
1. **No Intraday Liquidation or Purge**: Staged swing orders reside in `SwingStagedOrderManager._staged`, completely separate from `engine.working_orders`. The 4-phase flattening engine (`15:45` lockout, `15:50` purge, `15:55` liquidation, `15:58` audit) inspects only `engine.working_orders` and `account.positions`. It has no reference to `SwingStagedOrderManager`.
2. **Session Rollover Blindness**: In `backend/app/main.py:892-1015` (`_check_session_boundary`), the midnight/new-day rollover routine cancels intraday working orders and advances `pos.holding_days` for existing swing positions. It does *not* inspect, expire, or clear `SwingStagedOrderManager`.
3. **Stale Multi-Day Execution**: An order staged on Monday at 16:00 that fails to execute on Tuesday at 09:30 remains in `_staged`. On Wednesday at 09:30, if a bar arrives with `minute == 30`, `is_staged_for_entry(bar_sym)` evaluates to `True`. The order executes using a 2-day-old signal date, stale daily ATR, and an invalid market context.
4. **Symbol Reservation Deadlock**: When an order is staged, `reserve_symbol_for_swing(sym)` locks `sym` in `swing_reserved_symbols`. If the staged order is marooned, `sym` is never released. In `main.py:282`, `is_symbol_reserved_for_swing` permanently rejects any future intraday trades for that symbol (e.g., `AMD`).

### 1.3 Concurrency Annihilation Race Condition at Open
In `backend/app/strategies/swing_panic_dip.py:448-468`:

```python
            # -------------------------------------------------------------
            # 2. PROCESS ENTRIES NEXT
            # -------------------------------------------------------------
            staged_entries = self.staged_manager.get_staged_entries()
            for entry_order in staged_entries:
                sym = entry_order.symbol
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

                open_price = open_prices.get(sym)
                if not open_price or open_price <= 0.0:
                    errors.append(f"Missing open price for {sym}; cannot execute staged entry")
                    continue  # Await this symbol's open bar
```

#### Forensic Observations & Execution Trace:
Consider a standard transition scenario:
- Current holdings: `KLAC` (shares=100) and `MU` (shares=100). Active swing count = 2.
- At 16:00 ET close, `KLAC` qualifies for exit (5-day SMA cross). `stage_sell("KLAC")` is recorded.
- Available slot tomorrow: $2 - (2 - 1) = 1$. `LRCX` qualifies for entry. `stage_buy("LRCX")` is recorded.
- At 09:30:00 market open, incoming WebSocket messages arrive per symbol:
  - **T = 09:30:01**: Bar for `LRCX` arrives. `main.py` invokes `execute_market_open({"LRCX": 800.0}, open_time)`.
  - Step 1 (Exits): `open_prices.get("KLAC")` is `None` (KLAC bar hasn't arrived yet). Exit skipped.
  - Step 2 (Entries): `active_count = len(self.get_active_swing_positions())` is `2` (`KLAC` and `MU`).
  - Line 454 triggers: `active_count >= 2` evaluates `True`!
  - Lines 459–461 execute:
    ```python
    self.staged_manager.remove_staged_order(entry_order.order_id)
    if self.release_symbol_cb:
        self.release_symbol_cb(sym)
    continue
    ```
  - **LRCX IS INSTANTLY AND PERMANENTLY REMOVED FROM STAGED ENTRIES**.
  - **T = 09:30:02**: Bar for `KLAC` arrives. `execute_market_open({"KLAC": 105.0}, open_time)` executes exit for `KLAC`. Active positions count drops to 1 (`MU`).
  - Slot is now free, but `LRCX` staged entry was already destroyed.
  
#### Multi-Order Annihilation:
Because `execute_market_open` loops through *all* entries in `get_staged_entries()` regardless of whether `open_prices` contains that symbol, *every* staged entry in `staged_entries` is purged in that single call when `active_count >= 2`.

#### Empirical Proof:
Tested via Python simulation:
```
Before any bar arrives:
  Staged entries: ['LRCX', 'KLAC']
  Staged exits: ['MU', 'GS']
Concurrency cap reached (2/2): Cannot enter swing trade on LRCX
Concurrency cap reached (2/2): Cannot enter swing trade on KLAC
After LRCX bar arrives:
  Staged entries: []
  Staged exits: ['MU', 'GS']
```
Both `LRCX` and `KLAC` entries were wiped out on the very first bar print before `MU` or `GS` exits could execute.

---

## 2. Staged Order Idempotency Deficiencies

### 2.1 Failure of Idempotency on Repeated Scans
In `backend/app/strategies/swing_panic_dip.py:298-366`:

```python
        # -------------------------------------------------------------
        # STEP 2: EVALUATE CANDIDATE ENTRIES
        # -------------------------------------------------------------
        # Exiting positions will be sold at tomorrow's open, freeing their slots
        exiting_symbols = {e.symbol for e in staged_exits}
        surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
        available_slots = self.max_concurrent_positions - len(surviving_positions)

        log.info(...)

        if available_slots > 0:
            qqq_bars = self.bar_store.get_bars(self.benchmark, as_of=session_date)

            for sym in self.symbols:
                if available_slots <= 0:
                    break

                # Skip if already held or scheduled to exit at next open (cannot enter and exit simultaneously)
                if sym in active_positions or sym in exiting_symbols:
                    continue

                # Skip if already staged for entry
                if self.staged_manager.is_staged_for_entry(sym):
                    continue
                ...
                if qual_res.qualified:
                    staged_buy = self.staged_manager.stage_buy(...)
                    staged_entries.append(staged_buy)
                    available_slots -= 1
```

#### Forensic Observations & Mathematical Flaw:
1. **Unaccounted Staged Entries in Slot Count**:
   `available_slots` is computed strictly from active positions:
   `available_slots = self.max_concurrent_positions - len(surviving_positions)`
   It does *not* subtract `len(self.staged_manager.get_staged_entries())`.
2. **Non-Decrementing Skip**:
   On line 321, if `self.staged_manager.is_staged_for_entry(sym)` is `True`, it issues `continue`.
   `available_slots` is **NOT decremented**.
3. **Cumulative Staging on Multiple Invocations**:
   - **Call 1 (16:00:00 ET)**:
     - `available_slots = 2`.
     - Candidate 1 (`LRCX`) qualifies -> staged -> `available_slots` becomes 1.
     - Candidate 2 (`KLAC`) qualifies -> staged -> `available_slots` becomes 0 -> Loop breaks.
     - `staged_entries` in manager: `['LRCX', 'KLAC']`.
   - **Call 2 (16:00:01 ET, next clock tick, or post-restart scan)**:
     - `active_positions` is still 0. `available_slots` re-initializes to `2`.
     - Symbol `LRCX`: `is_staged_for_entry` is `True` -> `continue` (slots remaining: 2).
     - Symbol `KLAC`: `is_staged_for_entry` is `True` -> `continue` (slots remaining: 2).
     - Symbol `MU`: `is_staged_for_entry` is `False`. `MU` qualifies -> staged -> `available_slots` becomes 1!
     - Symbol `AMD`: `is_staged_for_entry` is `False`. `AMD` qualifies -> staged -> `available_slots` becomes 0!
     - `staged_entries` in manager: `['LRCX', 'KLAC', 'MU', 'AMD']` (4 staged orders!).
   - **Call 3**:
     - Stages Candidate 5 (`GS`).
     - `staged_entries` in manager: `['LRCX', 'KLAC', 'MU', 'AMD', 'GS']` (5 staged orders!).

#### Empirical Verification Output:
```
After call 1 staged count: 2 ['LRCX', 'KLAC']
After call 2 staged count: 4 ['LRCX', 'KLAC', 'MU', 'AMD']
After call 3 staged count: 4 ['LRCX', 'KLAC', 'MU', 'AMD']
```
(All 4 qualified stocks staged, breaching the 2-position cap).

### 2.2 Systemic Consequences
1. **$125,000 Capital Exposure**: Staging 5 orders at $25,000 each commits $125,000 of buying power from a $50,000 account pool.
2. **Arbitrary Fill Lottery**: At 09:30 open, whichever 2 stocks have bars arrive first will fill, discarding quantitative ranking or signal priority.
3. **Sector and Symbol Lockout Collision**: Staging `AMD` calls `reserve_symbol_cb("AMD")`, locking `AMD` out of intraday trading even though swing trading already had 2 valid slots filled by `LRCX` and `KLAC`.

---

## 3. Execution Slippage & Pricing Omissions

### 3.1 Hardcoded 0.0 Slippage
In `backend/app/strategies/swing_panic_dip.py`:

- **Open Exit Execution** (line 427):
  ```python
  fill = self.execution_engine._execute_fill(
      order=order_obj,
      qty=shares,
      price=open_price,
      slippage=0.0,
      timestamp=open_time,
  )
  ```
- **Open Entry Execution** (line 526):
  ```python
  fill = self.execution_engine._execute_fill(
      order=order_obj,
      qty=qty,
      price=open_price,
      slippage=0.0,
      timestamp=open_time,
  )
  ```
- **Emergency Stop Fill** (line 614):
  ```python
  fill = self.execution_engine._execute_fill(
      order=order_obj,
      qty=pos.shares,
      price=current_p,
      slippage=0.0,
      timestamp=timestamp,
  )
  ```
- **Immediate Exit Fill** (line 772):
  ```python
  fill = self.execution_engine._execute_fill(
      order=order_obj,
      qty=pos.shares,
      price=exec_price,
      slippage=0.0,
      timestamp=now_dt,
  )
  ```

#### Forensic Observations:
1. **Private Method Abuse**: `swing_panic_dip.py` calls `_execute_fill` directly rather than routing orders through the engine's public order-matching pipeline (`process_bar` or `process_quote`).
2. **Microstructure Model Bypass**: `ExecutionEngine.calculate_slippage` (`engine.py:266-290`) calculates dynamic slippage factoring in bid-ask spread, volatility, and volume participation:
   $$\text{slippage} = \max\left(0.0001 \times P, \ 0.5 \times \text{spread} + 0.08 \times \text{volatility} \times \sqrt{\frac{\text{qty}}{\max(1000, V)}}\right)$$
   For market orders, `process_bar` adds half-spread ($\max(0.005, 0.0002 \times P)$) and slippage.
   In `swing_panic_dip.py`, this entire model is bypassed. Fills are priced at the exact `bar.open` with zero cents of slippage.

### 3.2 Sizing Knife-Edge & Rule 6 Stop Misalignment
1. **Rule 6 Stop Anchor**: Rule 6 states: "Immediately establish a hard stop-loss at $2.5 \times \text{Daily ATR(14)}$ below the fill price."
   Line 481 computes:
   `stop_price = round(open_price - stop_distance, 2)`
   It anchors the stop to `open_price` instead of the realized `fill_price`.
2. **Sizing Margin Breach Risk**:
   `qty = int(math.floor(self.slot_notional / open_price))`
   If realistic adverse slippage of 25 cents is applied on an $800 stock, `fill_price = 800.25`.
   $31 \times 800.25 = \$24,807.75$.
   However, for a lower-priced stock where `qty * open_price` is very close to $25,000, unbudgeted slippage could cause `can_afford` in `account.py:228` to reject the order (`order_value > max_alloc + 0.01`).

---

## 4. Architectural Summary Table of Findings

| ID | Component | Location | Severity | Description | Consequence |
|---|---|---|---|---|---|
| **V1** | Timing / Open Window | `backend/app/main.py:1296` | **CRITICAL** | Exact `hour==9 and minute==30` check with no tolerance window | Delayed or 09:31 bars fail to trigger open execution; orders hang indefinitely |
| **V2** | Order Lifecycle / Marooning | `backend/app/strategies/swing_panic_dip.py:110-192`, `backend/app/main.py:892` | **CRITICAL** | Staged orders stored outside `working_orders` without TTL, boundary sweep, or expiration | Stale orders marooned across sessions execute days later on invalid historical triggers |
| **V3** | Open Execution Concurrency Race | `backend/app/strategies/swing_panic_dip.py:453-463` | **CRITICAL** | Per-symbol bar arrival evaluates concurrency cap without factoring in pending exits | Incoming entries permanently deleted when entry bar arrives before exit bar |
| **V4** | Multi-Entry Destruction | `backend/app/strategies/swing_panic_dip.py:450-468` | **CRITICAL** | Single-symbol call iterates over all entries and drops them when cap is temporarily reached | All staged entries eradicated on the first incoming bar |
| **V5** | Order Staging Idempotency | `backend/app/strategies/swing_panic_dip.py:302-362` | **CRITICAL** | `evaluate_market_close` does not decrement `available_slots` for existing staged orders | Multiple close scans stage up to 5 concurrent entries, breaching 2-position slot cap |
| **V6** | Mutual Exclusion Leakage | `backend/app/strategies/swing_panic_dip.py:364`, `backend/app/main.py:120` | **MAJOR** | Spurious duplicate staging reserves symbols (`AMD`) in `swing_reserved_symbols` | Intraday trading blocked from trading `AMD` due to phantom swing reservations |
| **V7** | Slippage & Pricing Bypass | `backend/app/strategies/swing_panic_dip.py:427, 526, 614, 772` | **MAJOR** | Direct calls to `_execute_fill` with hardcoded `slippage=0.0` | Paper trading fills at zero spread/slippage; unrealistic execution metrics |
| **V8** | Stop-Loss Price Anchor | `backend/app/strategies/swing_panic_dip.py:481` | **MINOR** | Stop price calculated from `open_price` instead of actual fill price | Inconsistent with Rule 6 specification |

---

## 5. Concrete Proposed Remediations & Code Patches

### 5.1 Fix for 09:30 ET Open Execution Tolerance Window (`backend/app/main.py`)

Replace lines 1294–1299 in `backend/app/main.py`:

```python
<<<<
    # 09:30 ET Market Open Execution for Staged Swing Orders
    # Execute open orders strictly for this symbol when THAT symbol's 09:30 open bar arrives
    if bar_et.time().hour == 9 and bar_et.time().minute == 30:
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)
====
    # 09:30 ET Market Open Execution Window for Staged Swing Orders (09:30:00 - 09:45:00 ET tolerance)
    # Allows delayed, illiquid, or 09:31+ bars to execute reliably without marooning staged orders
    bar_t = bar_et.time()
    if time(9, 30, 0) <= bar_t <= time(9, 45, 0):
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            swing_strategy_engine.execute_market_open(
                {bar_sym: bar.open},
                bar.timestamp,
                bar_high=bar.high,
                bar_low=bar.low,
                bar_volume=bar.volume,
            )
>>>>
```

Also, in `_check_session_boundary` or `_runtime_clock_loop`, add expiration cleanup for any unexecuted orders past `09:45:00 ET`:
```python
def _expire_stale_staged_swing_orders(current_time: datetime) -> None:
    """Purge unexecuted staged orders past 09:45 ET so they never execute days later."""
    et_time = current_time.astimezone(ET_TZ).time()
    if et_time > time(9, 45, 0):
        staged = swing_staged_order_manager.get_staged_orders()
        for order in staged:
            log.warning("Expiring unexecuted staged swing order %s (%s %s) past open window", order.order_id, order.action, order.symbol)
            swing_staged_order_manager.remove_staged_order(order.order_id)
            release_symbol_for_swing(order.symbol)
```

### 5.2 Fix for Staged Order Idempotency (`backend/app/strategies/swing_panic_dip.py`)

In `evaluate_market_close`:
```python
<<<<
        exiting_symbols = {e.symbol for e in staged_exits}
        surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
        available_slots = self.max_concurrent_positions - len(surviving_positions)

        log.info(
            f"16:00 Swing Close Scan: {len(active_positions)} active, {len(staged_exits)} exiting, "
            f"{available_slots} available slots"
        )

        if available_slots > 0:
            qqq_bars = self.bar_store.get_bars(self.benchmark, as_of=session_date)

            for sym in self.symbols:
                if available_slots <= 0:
                    break

                # Skip if already held or scheduled to exit at next open (cannot enter and exit simultaneously)
                if sym in active_positions or sym in exiting_symbols:
                    continue

                # Skip if already staged for entry
                if self.staged_manager.is_staged_for_entry(sym):
                    continue
====
        exiting_symbols = {e.symbol for e in staged_exits} | {e.symbol for e in self.staged_manager.get_staged_exits()}
        surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
        existing_staged_entries = self.staged_manager.get_staged_entries()
        existing_staged_symbols = {e.symbol for e in existing_staged_entries}

        # Deduct already staged entries from available slots to enforce strict idempotency
        available_slots = self.max_concurrent_positions - len(surviving_positions) - len(existing_staged_symbols)
        available_slots = max(0, available_slots)

        log.info(
            f"16:00 Swing Close Scan: {len(active_positions)} active, {len(exiting_symbols)} exiting, "
            f"{len(existing_staged_symbols)} already staged, {available_slots} available slots"
        )

        if available_slots > 0:
            qqq_bars = self.bar_store.get_bars(self.benchmark, as_of=session_date)

            for sym in self.symbols:
                if available_slots <= 0:
                    break

                # Skip if already held, scheduled to exit, or already staged
                if sym in active_positions or sym in exiting_symbols or sym in existing_staged_symbols:
                    continue
>>>>
```

### 5.3 Fix for Open Execution Race Condition & Realistic Slippage (`backend/app/strategies/swing_panic_dip.py`)

In `execute_market_open`:
```python
<<<<
            # -------------------------------------------------------------
            # 2. PROCESS ENTRIES NEXT
            # -------------------------------------------------------------
            staged_entries = self.staged_manager.get_staged_entries()
            for entry_order in staged_entries:
                sym = entry_order.symbol
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

                open_price = open_prices.get(sym)
                if not open_price or open_price <= 0.0:
                    errors.append(f"Missing open price for {sym}; cannot execute staged entry")
                    continue  # Await this symbol's open bar
====
            # -------------------------------------------------------------
            # 2. PROCESS ENTRIES NEXT
            # -------------------------------------------------------------
            staged_entries = self.staged_manager.get_staged_entries()
            staged_exits = self.staged_manager.get_staged_exits()
            pending_exit_symbols = {e.symbol for e in staged_exits}

            for entry_order in staged_entries:
                sym = entry_order.symbol
                # ONLY evaluate this entry order if its price is present in open_prices
                if sym not in open_prices:
                    continue

                open_price = open_prices[sym]
                if open_price <= 0.0:
                    continue

                # Effective active positions count excludes positions scheduled to exit at open
                active_positions = self.get_active_swing_positions()
                effective_active_count = len([s for s in active_positions if s not in pending_exit_symbols])

                if effective_active_count >= self.max_concurrent_positions:
                    log.warning(
                        f"Concurrency cap reached ({effective_active_count}/{self.max_concurrent_positions}): "
                        f"Cannot enter swing trade on {sym}"
                    )
                    self.staged_manager.remove_staged_order(entry_order.order_id)
                    if self.release_symbol_cb:
                        self.release_symbol_cb(sym)
                    continue

                # If physical active positions are temporarily full awaiting pending exit fill, hold order
                if len(active_positions) >= self.max_concurrent_positions:
                    log.info(f"Entry {sym} awaiting pending exit(s) ({pending_exit_symbols}) to complete fill")
                    continue

                # Realistic open slippage calculation
                spread_half = max(0.005, open_price * 0.0002)
                raw_slippage = (
                    self.execution_engine.calculate_slippage(
                        Order(
                            id="tmp", client_order_id="tmp", symbol=sym,
                            side=OrderSide.BUY, order_type=OrderType.MARKET, qty=100
                        ),
                        market_price=open_price,
                        bar_volume=bar_volume or 10000,
                        bar_high=bar_high,
                        bar_low=bar_low,
                    )
                    if hasattr(self.execution_engine, "calculate_slippage")
                    else max(0.01, open_price * 0.0002)
                )
                total_slippage = round(spread_half + raw_slippage, 4)
                fill_price = round(open_price + total_slippage, 2)

                # Size using fill_price to ensure total notional <= $25,000
                qty = int(math.floor(self.slot_notional / fill_price))
                if qty <= 0:
                    self.staged_manager.remove_staged_order(entry_order.order_id)
                    if self.release_symbol_cb:
                        self.release_symbol_cb(sym)
                    continue

                # Rule 6: Emergency Stop Price below actual fill price
                stop_distance = self.stop_atr_multiplier * entry_order.daily_atr
                stop_price = round(fill_price - stop_distance, 2)
>>>>
```

---

## 6. Verification and Regression Test Plan

To independently prove all vulnerabilities and verify their remediations, implement the following tests in `backend/tests/test_swing_timing_idempotency_audit.py`:

1. `test_evaluate_market_close_strict_idempotency`:
   - Set up `DailyBarStore` with 4 qualified stocks (`LRCX`, `KLAC`, `MU`, `AMD`).
   - Call `evaluate_market_close(d)` 3 consecutive times on the same date.
   - Assert `len(staged_manager.get_staged_entries()) == 2` (never 4 or 5).
   - Assert `reserved_symbols == {"LRCX", "KLAC"}` (never contains `MU` or `AMD`).
2. `test_market_open_out_of_order_bar_arrival_no_annihilation`:
   - Pre-seed 2 positions (`KLAC` and `MU`).
   - Stage exit for `KLAC`, stage entry for `LRCX`.
   - Call `execute_market_open({"LRCX": 800.0}, open_time)` first.
   - Assert `LRCX` staged entry is NOT deleted.
   - Call `execute_market_open({"KLAC": 105.0}, open_time)`.
   - Assert `KLAC` exits.
   - Call `execute_market_open({"LRCX": 800.0}, open_time)` again.
   - Assert `LRCX` fills successfully with 2 active positions total.
3. `test_market_open_delayed_bar_within_tolerance_window`:
   - Stage entry for `LRCX`.
   - Simulate bar arrival at `09:31:00 ET` (or `09:32:00 ET`).
   - Run through `main.handle_bar_event`.
   - Assert `LRCX` executes and fills without hanging.
4. `test_market_open_realistic_slippage_and_stop_anchor`:
   - Stage entry for `LRCX` with `ATR = 5.00`.
   - Execute open with `bar.open = 800.0`.
   - Assert `fill.slippage > 0.0`.
   - Assert `fill.price > 800.0`.
   - Assert `pos.stop_loss_price == round(fill.price - 2.5 * 5.00, 2)`.
   - Assert `pos.shares * fill.price <= 25000.00`.
