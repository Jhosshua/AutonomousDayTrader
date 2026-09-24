# Handoff Report: Market Open Stale Price Remediation

**Agent**: Explorer 2 Iteration 2 (`teamwork_preview_explorer`)  
**Role**: Market Open Pricing Explorer  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_r2`  
**Milestone**: Market Open Stale Price Remediation (Iteration 2)  
**Date**: 2026-09-24T00:40:00Z  
**Verdict**: **INVESTIGATION_COMPLETE / ACTIONABLE_REMEDIATION_FORMULATED**

---

## 1. Observation

1. **Defect Location (`backend/app/main.py:1330–1344`)**:
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

2. **Session Boundary Incomplete Purging (`backend/app/main.py:1020–1030`)**:
   ```python
   engine.prune_session_state()
   market_history.clear()
   recent_news.clear()
   if state_store is not None:
       state_store.wal_checkpoint("PASSIVE")
   ```
   `latest_market_prices` is **never cleared** in `_check_session_boundary`.

3. **`latest_market_prices` Contamination Across Event Types**:
   - `main.py:1304`: `latest_market_prices[bar.symbol.upper()] = bar.close` (stores 1-minute close, not auction open).
   - `main.py:1455`: `latest_market_prices[quote.symbol.upper()] = (quote.bid_price + quote.ask_price) / 2.0` (stores pre-market bid/ask midpoint).
   - `main.py:1688`: `latest_market_prices[trade.symbol.upper()] = trade.price` (stores pre-market trades).
   - `runtime_state.py:221`: `latest_market_prices.update(decoded["market"]["latest_market_prices"])` (restores prior-day prices from SQLite checkpoints upon restart).

4. **Reviewer 1 Finding 1 (`reviewer_1/handoff.md:71–80`)**:
   - In `backend/app/main.py`, `latest_market_prices` holds yesterday's 16:00 close price.
   - When the first staged symbol's bar arrives at 09:30:00 (e.g. `KLAC`), lines 1335–1341 iterate through other staged orders (e.g. `LRCX`).
   - Lines 1337 and 1340 insert `latest_market_prices["LRCX"]` into `open_price_map`.
   - `execute_market_open` executes the `LRCX` order using yesterday's price *before* today's `LRCX` 09:30 opening bar arrives.

5. **`SwingStrategyEngine.execute_market_open` Contract (`swing_panic_dip.py:416–419, 503–506`)**:
   ```python
   # Exits:
   open_price = open_prices.get(sym)
   if open_price is None or open_price <= 0.0:
       continue  # Await this symbol's open bar

   # Entries:
   open_price = open_prices.get(sym)
   if not open_price or open_price <= 0.0:
       errors.append(f"Missing open price for {sym}; awaiting open bar")
       continue  # Await this symbol's open bar; do not delete staged order
   ```
   If a symbol's open price is absent from `open_prices`, `execute_market_open` does **not** drop the order; it safely defers it and awaits that symbol's open bar.

---

## 2. Logic Chain

1. *Observation 1 and Observation 4 establish* that when any staged symbol's opening bar prints, lines 1335–1341 pull prices from `latest_market_prices` for all other staged entries and exits.
2. *Observation 2 and Observation 3 establish* that `latest_market_prices` persists across session boundaries, is restored from disk checkpoints after restarts, and is overwritten by pre-market quotes and 1-minute close prices.
3. *Therefore*, if symbol A's bar arrives at 09:30:00, any other staged symbol B will be added to `open_price_map` using yesterday's 16:00 close or pre-market noise.
4. *Observation 1 shows* that `execute_market_open` receives this contaminated `open_price_map` and immediately fills symbol B at yesterday's close, before symbol B's 09:30 opening auction bar prints. This violates Rule 5 open execution price fidelity.
5. *Observation 5 establishes* that `SwingStrategyEngine` already has native support for missing open prices: it skips unquoted symbols and preserves their staged status without deletion.
6. *However, if we purely pass `{bar_sym: bar.open}` without open price tracking (Option A)*, a deferred entry (arriving before an exit) cannot execute immediately upon the exit freeing a slot because the second call (`execute_market_open({exit_sym: bar.open})`) would lack the entry symbol's open price, delaying the entry to the 09:31 bar or risking 09:45 expiration.
7. *Therefore (Option B)*, introducing a session-scoped `today_open_prices: Dict[str, float]` registry in `main.py`—populated strictly by the first regular-session bar (`bar.open`) between 09:30 and 09:45, and cleared at session boundaries—guarantees that:
   - Staged orders only execute on confirmed regular-session opening auction prices.
   - Secondary staged orders whose bars haven't arrived yet remain staged and await their opening bar.
   - Deferred entries execute immediately when an exit frees a slot with their confirmed today's opening auction price.

---

## 3. Caveats

1. **Jitter Scope**: Opening prints may arrive between 09:30:00 and 09:45:00 ET. `today_open_prices` records the first bar in this window for each symbol. If a stock does not trade at all between 09:30 and 09:45 (completely illiquid), its staged order will expire at 09:45 as designed by Defect 1 TTL rules.
2. **Pre-Trade Risk Valuation**: `latest_market_prices` continues to serve pre-trade risk valuation and UI streaming; clearing it at session boundary (`_check_session_boundary`) is safe because lines 307–310 in `main.py` contain fallback price estimation (`stop_price` or baseline) if `latest_market_prices` is empty prior to market open.
3. **E2E Test Assertion (`test_swing_multiday_replay.py:224`)**: Reviewer 1 Finding 2 (`AssertionError: assert 639.28 == 639.15`) is a separate test assertion discrepancy requiring an update to anchor to realized fill price (`lrcx_pos.avg_entry_price`), which should be applied alongside this remediation.

---

## 4. Conclusion

The stale price execution in `backend/app/main.py:1334–1341` is a verified high-severity execution integrity defect. 

The remediation is straightforward, surgical, and robust:
1. In `backend/app/main.py`: Add `today_open_prices: Dict[str, float] = {}`.
2. In `_check_session_boundary` and `reset_runtime_state`: Add `today_open_prices.clear()` and `latest_market_prices.clear()`.
3. In `handle_bar_event`:
   - During `09:30 <= minute <= 45`, record `if bar_sym not in today_open_prices and bar.open > 0: today_open_prices[bar_sym] = bar.open`.
   - Replace lookups in `latest_market_prices` (lines 1336 & 1339) with lookups in `today_open_prices`.
4. In `backend/tests/unit/test_swing_forensic_remediation.py`: Add `test_defect_11_market_open_stale_price_prevention`.

---

## 5. Verification Method

### 5.1 Verification Commands

```bash
# 1. Verify backend unit tests
pytest backend/tests/unit/test_swing_forensic_remediation.py -v

# 2. Verify all timing and idempotency stress tests
pytest backend/tests/stress/test_challenger_timing_idempotency.py -v

# 3. Verify swing strategy tests
pytest backend/tests/test_swing_strategy.py -v

# 4. Verify full backend suite
pytest backend/tests
```

### 5.2 Files to Inspect
- `backend/app/main.py`: lines 92, 830, 1022, 1330–1344.
- `backend/tests/unit/test_swing_forensic_remediation.py`: new `test_defect_11`.

### 5.3 Invalidation Conditions
- If a staged swing order fills on a price that does not match `bar.open` from today's 09:30–09:45 session window.
- If a secondary staged order executes before its own opening bar has printed.
- If a deferred entry order fails to execute after a pending exit completes when both opening bars have arrived.
