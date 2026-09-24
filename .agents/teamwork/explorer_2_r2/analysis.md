# Forensic Analysis & Remediation Strategy: Market Open Stale Price Execution

**Agent**: Explorer 2 Iteration 2 (`teamwork_preview_explorer`)  
**Role**: Market Open Pricing Explorer  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_r2`  
**Target File**: `backend/app/main.py` (lines 1330–1344, 980–1030, 810–835)  
**Date**: 2026-09-24T00:39:30Z  

---

## 1. Executive Summary

During the Phase 2 review of Worker 1's forensic remediation, Reviewer 1 (`reviewer_1/handoff.md`, Finding 1) discovered that `backend/app/main.py:1334–1341` pulls unconfirmed prices from the global in-memory dictionary `latest_market_prices` for all staged swing orders whenever any single symbol's opening bar arrives.

Because `latest_market_prices` is:
1. **Never cleared at session boundaries** (midnight rollovers or morning resets in `_check_session_boundary`), holding yesterday's 16:00 close price; and
2. **Updated by pre-market quotes/trades and intraday candle close prints** rather than strictly confirmed 09:30 regular-hours opening auction prints (`bar.open`);

secondary or deferred staged orders (e.g. `LRCX` staged alongside `KLAC`, or a staged exit for `MU` when `KLAC` arrives first) are executed against **stale prior-day closing prices** before their today's 09:30 opening bar prints. This constitutes a direct violation of Rule 5 ("All swing orders execute at 09:30 ET market open" using the opening bar's auction price `P_open`).

This analysis presents the architectural root cause, models the race condition permutations, and formulates the fix using a session-scoped `today_open_prices` registry combined with explicit session boundary purging.

---

## 2. Anatomy of the Defect

### 2.1 Code Inspection: `backend/app/main.py:1330–1344`

```python
    # 09:30 ET Market Open Execution Window for Staged Swing Orders (09:30:00 - 09:45:00 ET tolerance)
    # Allows delayed, illiquid, or 09:31+ bars to execute reliably without marooning staged orders
    if bar_t.hour == 9 and 30 <= bar_t.minute <= 45:
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            open_price_map = {bar_sym: bar.open}
            for stg_ent in swing_staged_order_manager.get_staged_entries():
                if stg_ent.symbol in latest_market_prices and stg_ent.symbol not in open_price_map:
                    open_price_map[stg_ent.symbol] = latest_market_prices[stg_ent.symbol]
            for stg_ext in swing_staged_order_manager.get_staged_exits():
                if stg_ext.symbol in latest_market_prices and stg_ext.symbol not in open_price_map:
                    open_price_map[stg_ext.symbol] = latest_market_prices[stg_ext.symbol]
            swing_strategy_engine.execute_market_open(open_price_map, bar.timestamp)
    elif (bar_t.hour == 9 and bar_t.minute > 45) or (10 <= bar_t.hour < 16):
        _expire_stale_staged_swing_orders(bar.timestamp)
```

### 2.2 Why Worker 1 Added This Logic
In the remediation of Defect 2 (Concurrency Annihilation Race Condition at Open), Worker 1 solved the issue where pending exits and incoming entries arrive asynchronously:
- If an account is capped at 2 positions, with 1 exit pending (`MU`) and 1 entry staged (`KLAC`):
  - If `KLAC` arrives first at 09:30:00, `KLAC`'s entry is deferred (`continue`) because `active_count == 2` and `pending_exits > 0`.
  - When `MU` arrives second at 09:30:05, `MU`'s exit executes, freeing a position slot.
  - Worker 1 wanted `KLAC` to immediately execute upon `MU`'s exit without waiting for `KLAC`'s next bar at 09:31.
  - To give `KLAC` an execution price during `MU`'s bar event, Worker 1 populated `open_price_map` by checking `if stg_ent.symbol in latest_market_prices`.

### 2.3 The Fatal Failure Mode
1. **Prior-Day Stale Data**: `latest_market_prices` is populated during trading hours (`latest_market_prices[bar.symbol.upper()] = bar.close` at line 1304). At the 16:00 close on Day 1, `latest_market_prices["LRCX"] = 650.00`.
2. **Missing Rollover Sweep**: When Day 2 begins, `_check_session_boundary` clears `market_history` and `recent_news`, but does **not** clear `latest_market_prices`.
3. **Premature Execution**:
   - At 09:30:01 ET on Day 2, `KLAC`'s opening bar arrives (`bar_sym == "KLAC"`, `bar.open == 705.00`).
   - Line 1334 creates `open_price_map = {"KLAC": 705.00}`.
   - Lines 1335–1337 iterate over `staged_entries`. `LRCX` is staged.
   - `LRCX in latest_market_prices` evaluates to `True` (holding Day 1 16:00 close: 650.00).
   - Line 1337 inserts `open_price_map["LRCX"] = 650.00`.
   - `swing_strategy_engine.execute_market_open(open_price_map, bar.timestamp)` is called with:
     `{"KLAC": 705.00, "LRCX": 650.00}`.
   - `execute_market_open` fills `LRCX` immediately at 650.00, establishes its emergency stop loss anchored to 650.00, and removes `LRCX` from `staged_entries`.
4. **Ignored Real Opening Bar**: At 09:30:03 ET on Day 2, `LRCX`'s true opening bar arrives (`bar.open == 660.00`). But `LRCX` has already been filled and removed from staged orders!
   - Result: `LRCX` entered at yesterday's close instead of today's open, with a $10.00 pricing error, corrupted position sizing, and misanchored stop loss.

---

## 3. Why `latest_market_prices` Cannot Be Reused for Market Open

Even if `latest_market_prices.clear()` is added to `_check_session_boundary`, relying on `latest_market_prices` in lines 1336 & 1339 remains vulnerable:

| Event Source | When It Updates `latest_market_prices` | Value Stored | Why It Cannot Be Used as `open_price` |
|---|---|---|---|
| `handle_bar_event` (line 1304) | Each 1-minute candle tick | `bar.close` | Close price of the minute candle, not auction open |
| `handle_quote_event` (line 1455) | Pre-market quotes (08:00–09:29 ET) | `(bid + ask) / 2.0` | Pre-market midpoint, wide spread, unconfirmed auction |
| `handle_trade_event` (line 1688) | Pre-market prints (08:00–09:29 ET) | `trade.price` | Odd-lot pre-market print, not 09:30 cross |
| `restore_runtime_state` (line 221) | Server restart recovery | Checkpointed dict | Re-injects prior-day prices from SQLite disk state |

**Conclusion**: `latest_market_prices` is a generic valuation dictionary for pre-trade risk and UI streaming. It is fundamentally unsuitable for establishing opening fill prices for swing trades.

---

## 4. Evaluation of Remediation Approaches

### Option A: Strictly Per-Symbol Execution (`open_price_map = {bar_sym: bar.open}`)
- **Mechanism**: Delete lines 1335–1341 and pass only `{bar_sym: bar.open}`.
- **Problem**: In the Defect 2 concurrency race condition:
  1. `KLAC` arrives first at 09:30:01 (2 active positions, 1 exit pending for `MU`).
  2. `KLAC` is deferred (`continue`).
  3. `MU` arrives second at 09:30:05. `MU` exits, freeing the slot.
  4. If `open_price_map` contains only `{"MU": 105.00}`, then `open_prices.get("KLAC")` is `None`.
  5. `KLAC` is skipped (`Missing open price for KLAC; awaiting open bar`).
  6. `KLAC` cannot execute until `KLAC`'s 09:31:00 bar arrives!
  7. If `KLAC` has no trade volume until 09:46:00, `KLAC` is marooned and cancelled by the 09:45 TTL sweep. Even if it trades at 09:31, it suffers 1 minute of adverse slippage.

### Option B: Recommended Production Architecture: `today_open_prices` Registry
- **Mechanism**: Maintain a dedicated session-scoped dictionary `today_open_prices: Dict[str, float] = {}` in `backend/app/main.py`.
- **Rules**:
  1. During the 09:30:00–09:45:59 ET open window, when a regular-hours bar arrives:
     ```python
     if bar_sym not in today_open_prices and bar.open > 0:
         today_open_prices[bar_sym] = bar.open
     ```
     Anchoring with `if bar_sym not in today_open_prices` guarantees that only the **first** bar (the official opening auction print) is stored.
  2. For the symbol of the current bar (`bar_sym`), `open_price_map` is initialized with `{bar_sym: bar.open}`.
  3. Other currently staged entries or exits are added to `open_price_map` **only if** their symbol exists in `today_open_prices`:
     ```python
     for stg_ent in swing_staged_order_manager.get_staged_entries():
         if stg_ent.symbol in today_open_prices and stg_ent.symbol not in open_price_map:
             open_price_map[stg_ent.symbol] = today_open_prices[stg_ent.symbol]
     for stg_ext in swing_staged_order_manager.get_staged_exits():
         if stg_ext.symbol in today_open_prices and stg_ext.symbol not in open_price_map:
             open_price_map[stg_ext.symbol] = today_open_prices[stg_ext.symbol]
     ```
  4. At session boundary (`_check_session_boundary`) and test reset (`reset_runtime_state`), both `today_open_prices.clear()` and `latest_market_prices.clear()` are executed.

---

## 5. Formal Verification of Race Condition Resolution

Under Option B, all multi-symbol arrival permutations function flawlessly:

### Scenario 1: Deferred Entry Arrives Before Pending Exit
- **Initial State**: 2 active positions (`MU`, `LRCX`). Cap = 2.
- **Staged**: Exit `MU`, Entry `KLAC`.
- **T1 (09:30:01 ET)**: `KLAC` bar arrives (`open = 700.00`).
  - `today_open_prices["KLAC"] = 700.00`.
  - `MU` is not in `today_open_prices`.
  - `open_price_map = {"KLAC": 700.00}`.
  - `execute_market_open({"KLAC": 700.00})`:
    - `MU` exit: skipped (open price missing).
    - `KLAC` entry: active count 2 >= 2, pending exits has `MU` -> **deferred** (`continue`).
- **T2 (09:30:05 ET)**: `MU` bar arrives (`open = 105.00`).
  - `today_open_prices["MU"] = 105.00`.
  - `open_price_map = {"MU": 105.00}`.
  - Staged entries check: `KLAC` is in `today_open_prices`!
  - `open_price_map["KLAC"] = 700.00`.
  - `open_price_map` is `{"MU": 105.00, "KLAC": 700.00}`.
  - `execute_market_open({"MU": 105.00, "KLAC": 700.00})`:
    - Step 1 (Exits): `MU` fills at 105.00 (- slippage). Position closed. Slot freed (`active_count = 1`).
    - Step 2 (Entries): `KLAC` evaluates `active_count = 1 < 2`. `open_price` is 700.00 (today's confirmed auction open!).
    - `KLAC` fills at 700.00 (+ slippage). Position opened.
- **Result**: `MU` and `KLAC` both execute at their exact today's opening prices with **zero latency** and **zero stale price leakage**.

### Scenario 2: Pending Exit Arrives Before Staged Entry
- **T1 (09:30:01 ET)**: `MU` bar arrives (`open = 105.00`).
  - `today_open_prices["MU"] = 105.00`.
  - `open_price_map = {"MU": 105.00}` (`KLAC` not yet in `today_open_prices`).
  - `MU` exits cleanly. Active positions reduce to 1.
- **T2 (09:30:05 ET)**: `KLAC` bar arrives (`open = 700.00`).
  - `today_open_prices["KLAC"] = 700.00`.
  - `open_price_map = {"KLAC": 700.00}`.
  - `KLAC` enters cleanly at 700.00.
- **Result**: Clean sequential execution.

### Scenario 3: Multiple Staged Entries Arriving Staggered
- **Initial State**: 0 active positions.
- **Staged**: Entry `KLAC`, Entry `LRCX`.
- **T1 (09:30:00 ET)**: `KLAC` arrives (`open = 700.00`).
  - `today_open_prices["KLAC"] = 700.00`.
  - `LRCX` is not in `today_open_prices`.
  - `open_price_map = {"KLAC": 700.00}`.
  - `KLAC` fills. `LRCX` awaits its open bar without executing prematurely.
- **T2 (09:30:03 ET)**: `LRCX` arrives (`open = 659.13`).
  - `today_open_prices["LRCX"] = 659.13`.
  - `open_price_map = {"LRCX": 659.13}`.
  - `LRCX` fills at 659.13.
- **Result**: Neither order executes on yesterday's close; each executes strictly on its own confirmed open bar.

---

## 6. Concrete Code Remediation

### 6.1 `backend/app/main.py`: Module-level Registry & Reset

```python
# Around line 92 in backend/app/main.py:
latest_market_prices: Dict[str, float] = {}
today_open_prices: Dict[str, float] = {}  # Confirmed regular-session opening prices for today (09:30-09:45 ET)
```

### 6.2 `backend/app/main.py`: Reset in `_check_session_boundary` and `reset_runtime_state`

```python
# In reset_runtime_state() around line 830:
    latest_market_prices.clear()
    today_open_prices.clear()
    market_history.clear()

# In _check_session_boundary() around line 1022:
    engine.prune_session_state()
    market_history.clear()
    recent_news.clear()
    latest_market_prices.clear()
    today_open_prices.clear()
```

### 6.3 `backend/app/main.py`: Market Open Execution Window (lines 1330–1344)

```python
    # 09:30 ET Market Open Execution Window for Staged Swing Orders (09:30:00 - 09:45:00 ET tolerance)
    # Allows delayed, illiquid, or 09:31+ bars to execute reliably without marooning staged orders
    if bar_t.hour == 9 and 30 <= bar_t.minute <= 45:
        # Record confirmed opening auction price for today's regular session
        if bar_sym not in today_open_prices and bar.open > 0:
            today_open_prices[bar_sym] = bar.open

        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            open_price_map = {bar_sym: bar.open}
            # Only include other staged orders if their OWN today's open bar has already arrived
            for stg_ent in swing_staged_order_manager.get_staged_entries():
                if stg_ent.symbol in today_open_prices and stg_ent.symbol not in open_price_map:
                    open_price_map[stg_ent.symbol] = today_open_prices[stg_ent.symbol]
            for stg_ext in swing_staged_order_manager.get_staged_exits():
                if stg_ext.symbol in today_open_prices and stg_ext.symbol not in open_price_map:
                    open_price_map[stg_ext.symbol] = today_open_prices[stg_ext.symbol]
            swing_strategy_engine.execute_market_open(open_price_map, bar.timestamp)
    elif (bar_t.hour == 9 and bar_t.minute > 45) or (10 <= bar_t.hour < 16):
        _expire_stale_staged_swing_orders(bar.timestamp)
```

---

## 7. Proposed Regression Test Specification

To be added to `backend/tests/unit/test_swing_forensic_remediation.py`:

```python
def test_defect_11_market_open_stale_price_prevention():
    """Verify that staged orders NEVER execute on yesterday's close or pre-market prices.
    
    1. Seed latest_market_prices with stale prior-day prices for LRCX and KLAC.
    2. Stage entry orders for both KLAC and LRCX.
    3. Stream KLAC 09:30 opening bar.
    4. Assert KLAC fills at today's open, but LRCX does NOT fill prematurely on stale price.
    5. Stream LRCX 09:30 opening bar.
    6. Assert LRCX fills at its own today's open.
    """
    main.reset_runtime_state(starting_equity=50000.0)
    main.simulation_mode = True
    main.swing_staged_order_manager.clear()
    main.swing_reserved_symbols.clear()
    
    # 1. Stale prices from yesterday's close
    main.latest_market_prices["KLAC"] = 650.0  # Yesterday close
    main.latest_market_prices["LRCX"] = 600.0  # Yesterday close
    
    eval_date = date(2026, 9, 23)
    main.swing_staged_order_manager.stage_buy("KLAC", 25000.0, 15.0, eval_date, "PANIC_DIP")
    main.swing_staged_order_manager.stage_buy("LRCX", 25000.0, 12.0, eval_date, "PANIC_DIP")
    main.swing_reserved_symbols.update({"KLAC", "LRCX"})
    
    # 2. Advance session boundary to morning
    morning_dt = datetime(2026, 9, 24, 9, 30, 0, tzinfo=main.ET_TZ)
    
    # 3. KLAC bar arrives first with today's open price = 700.0
    klac_bar = BarEvent("KLAC", morning_dt, 700.0, 705.0, 698.0, 702.0, 15000)
    asyncio.run(main.handle_bar_event(klac_bar))
    
    # KLAC must have executed at 700.0 (plus slippage), NOT 650.0
    assert "KLAC" in main.account.positions
    assert main.account.positions["KLAC"].avg_entry_price >= 700.0
    
    # LRCX must NOT have executed yet! It must still be staged!
    assert "LRCX" not in main.account.positions
    assert main.swing_staged_order_manager.is_staged_for_entry("LRCX") is True
    
    # 4. LRCX bar arrives second with today's open price = 680.0
    lrcx_bar = BarEvent("LRCX", morning_dt + timedelta(seconds=2), 680.0, 685.0, 678.0, 682.0, 12000)
    asyncio.run(main.handle_bar_event(lrcx_bar))
    
    # LRCX must now have executed at 680.0 (plus slippage), NOT 600.0
    assert "LRCX" in main.account.positions
    assert main.account.positions["LRCX"].avg_entry_price >= 680.0
    assert main.swing_staged_order_manager.is_staged_for_entry("LRCX") is False
```

---

## 8. Summary of Findings & Next Steps

1. **Defect Confirmed**: The current code in `main.py:1335–1341` pulls from `latest_market_prices`, causing secondary staged orders to execute on yesterday's close before their opening bar arrives.
2. **Architecture Defined**: Introducing `today_open_prices` in `main.py`, populated only from confirmed 09:30–09:45 regular-hours opening bars, and cleared at session boundaries, eliminates all stale price leakage while preserving the zero-latency resolution of the Defect 2 concurrency race condition.
3. **Remediation Ready**: The code diff and regression test specification are fully detailed and ready for implementation by the remediation worker.
