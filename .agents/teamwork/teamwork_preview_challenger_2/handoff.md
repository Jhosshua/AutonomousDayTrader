# Handoff Report — Milestone M9D (Adversarial Challenger 2: Concurrency, Flattening Races & Margin Coordination)

**Agent**: Challenger 2 (`teamwork_preview_challenger_2`)  
**Mission**: Write adversarial test generators and stress harnesses attacking 09:30 concurrency races, 15:45-15:58 EOD flattening races, AMD symbol reservation, and shared $50,000 account margin coordination.  
**Authoritative Documents**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`, `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`, `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_challenger_2/DISPATCH.md`  
**Verdict**: **`REQUEST_CHANGES`**

---

## 1. Observation

Adversarial stress testing was conducted against the implementation delivered by workers M9A, M9B, and M9C across:
- `backend/app/core/account.py`
- `backend/app/core/engine.py`
- `backend/app/core/flattening.py`
- `backend/app/core/risk.py`
- `backend/app/strategies/swing_panic_dip.py`
- `backend/app/strategies/adaptation.py`
- `backend/app/main.py`

Stress harness path: `backend/tests/stress/test_challenger_concurrency_margin_races.py`.

Execution Command:
```bash
pytest backend/tests/stress/test_challenger_concurrency_margin_races.py -v
```

Execution Result:
`8 passed, 3 failed in 0.22s`

### Verbatim Tool Failures & Observed Code Locations

#### Observation 1.1: AMD Mutual Exclusion Leak Between In-Flight Working Orders and Swing Entry
**Location**: `backend/app/main.py:250-262` (`pre_trade_risk_validator`)
```python
    # Symbol reservation / mutual exclusion check:
    if not is_exit:
        if is_swing:
            # Swing cannot enter if an INTRADAY position is currently open for this symbol
            if existing_pos is not None and (
                getattr(existing_pos, "arm", None) != TradingArm.SWING
                and getattr(existing_pos, "strategy_id", "") != "swing_panic_dip"
            ):
                return False, f"SWING_REJECTED: Symbol {sym} is currently held by Intraday strategy"
        else:
            # Intraday cannot enter if symbol is reserved for Swing or currently held by Swing
            if is_symbol_reserved_for_swing(sym, acct):
                return False, f"SYMBOL_RESERVED_FOR_SWING: Intraday entry for {sym} rejected because symbol is reserved/held by Swing Engine"
```
**Empirical Test**: `TestAMDSymbolCollision::test_working_order_cross_arm_collision_vulnerability`  
**Verbatim Test Output**:
```
FAILED backend/tests/stress/test_challenger_concurrency_margin_races.py::TestAMDSymbolCollision::test_working_order_cross_arm_collision_vulnerability
Failed: MUTUAL EXCLUSION LEAK CONFIRMED: Intraday order ord_b07e40024d00 is actively working on AMD. pre_trade_risk_validator approved concurrent Swing order ord_2ca7a45a9d48 because it only checks account.positions, ignoring engine.working_orders! Both orders can fill simultaneously and net/corrupt shares!
```
When an Intraday strategy places an entry order for `AMD` (e.g., limit order in `engine.working_orders`), `existing_pos` is `None` because the order has not yet filled. When Swing subsequently or concurrently submits an entry order for `AMD`, `pre_trade_risk_validator` evaluates `existing_pos is not None` as `False` and approves the Swing order. Both orders are now active in `engine.working_orders`. Upon fill, `PaperTradingAccount.apply_fill` executes both against the same symbol, causing unintended position netting (wash trading) or corrupting position metadata by mixing intraday and swing shares.

---

#### Observation 1.2: Missing Concurrency Lock in `execute_market_open` Leading to Double-Buying and $75,000 Swing Capital Breach
**Location**: `backend/app/strategies/swing_panic_dip.py:340-527` (`SwingStrategyEngine.execute_market_open`)
```python
    def execute_market_open(self, open_prices: Dict[str, float], open_time: datetime) -> Dict[str, Any]:
...
        staged_entries = self.staged_manager.get_staged_entries()
        for entry_order in staged_entries:
            sym = entry_order.symbol
            active_count = len(self.get_active_swing_positions())
            if active_count >= self.max_concurrent_positions:
...
```
**Empirical Test**: `Test0930ConcurrencyRaces::test_concurrent_execute_market_open_race_condition`  
**Verbatim Test Output**:
```
FAILED backend/tests/stress/test_challenger_concurrency_margin_races.py::Test0930ConcurrencyRaces::test_concurrent_execute_market_open_race_condition
E       assert 75000.0 <= 50050.0
```
Under concurrent execution (simulated via 10 simultaneous threads at 09:30 open, mimicking rapid market open tick arrival), multiple callers invoke `execute_market_open`. Because there is no mutex or atomic order popping from `self.staged_manager` (orders are only deleted in a `finally` block after execution), multiple threads process the same candidate (`MU`). Both threads pass `active_count < 2`, and both execute a 250-share buy ($25,000 each). As a result, `MU` had 500 shares ($50,000 notional in a single slot, double the $25,000 cap), and total swing exposure reached $75,000 (breaching the $50,000 account pool limit).

---

#### Observation 1.3: Swing Positions Leak into Intraday Concurrency Cap in Live Execution Loop (`execute_strategy_signal`)
**Location**: `backend/app/main.py:1142-1175` (`execute_strategy_signal`)
```python
    latest_market_prices[sym] = signal.entry_price if bar is None else bar.close
    adapted_stop = adaptation_engine.calculate_adapted_stop(signal)
    committed_symbols, committed_sectors, committed_count, notional_map = _get_effective_committed_portfolio(account)
    is_active = sym in committed_symbols
    approved, reason, qty = adaptation_engine.evaluate_signal_admission(
        signal=signal,
        equity=account.equity,
        current_positions_count=committed_count,
        is_symbol_active=is_active,
    )
...
    risk_preview = risk_engine.evaluate_order_request(
        symbol=sym,
        side="BUY" if signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY" else "SELL",
        requested_qty=qty,
        entry_price=signal.entry_price,
        stop_price=adapted_stop,
        account_equity=account.equity,
        buying_power=account.buying_power,
        active_positions_count=committed_count,
        active_symbols=committed_symbols,
        active_sectors=committed_sectors,
        vix_multiplier=adaptation_engine.current_sizing_multiplier,
        is_entry_lockout_active=flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING,
        is_exit=False,
        existing_position_notional=notional_map.get(sym, 0.0),
    )
```
**Empirical Test**: `Test0930ConcurrencyRaces::test_intraday_signal_admission_with_active_swing_positions`  
**Verbatim Test Output**:
```
FAILED backend/tests/stress/test_challenger_concurrency_margin_races.py::Test0930ConcurrencyRaces::test_intraday_signal_admission_with_active_swing_positions
Failed: VULNERABILITY CONFIRMED: execute_strategy_signal calls _get_effective_committed_portfolio without arm=TradingArm.INTRADAY, causing committed_count=3 to count 2 swing positions. Adaptation engine rejected legitimate 2nd intraday trade: 'CONCURRENCY_GATE_DENIED: Max concurrent positions (3) reached'. When filtered by arm=TradingArm.INTRADAY, count is 1 and approved=True.
```
In `backend/app/main.py:1142`, `_get_effective_committed_portfolio(account)` is called without passing `arm=TradingArm.INTRADAY`. When 2 swing positions are open and 1 intraday position is open, `committed_count` is 3. At line 1147, `current_positions_count=3` is passed to `adaptation_engine.evaluate_signal_admission`, which rejects any new intraday trade with `CONCURRENCY_GATE_DENIED: Max concurrent positions (3) reached`. Furthermore, line 1167 passes `active_positions_count=committed_count` (3) to `risk_engine.evaluate_order_request` without `arm=TradingArm.INTRADAY`, causing `MAX_CONCURRENT_POSITIONS_REACHED` rejection. This starves intraday day-trading down to 1 position (a 67% capacity loss) while swing trades are active.

---

#### Observation 1.4: Purely In-Memory Staged Orders Evaporate on Overnight Restart
**Location**: `backend/app/main.py:327`, `backend/app/strategies/swing_panic_dip.py:84-161`, `backend/app/core/runtime_state.py`  
**Empirical Test**: `TestSharedMarginCoordination::test_overnight_restart_evaporates_staged_orders`  
Orders are staged at 16:00 ET close for next-day 09:30 open. `SwingStagedOrderManager` stores staged orders in `self._staged: Dict[str, StagedSwingOrder]` and `swing_reserved_symbols: Set[str]` in volatile process memory. Neither is serialized into `TradingStateStore` / `_capture_checkpoint()`. A standard overnight deployment or container restart between 16:00 ET and 09:30 ET destroys all staged orders and clears `AMD` reservations.

---

## 2. Logic Chain

1. **Pre-Trade Risk Gate Hole (Observation 1.1)**:
   - Milestone M9A and M9B specify mutual exclusion between Intraday and Swing on `AMD`.
   - `pre_trade_risk_validator` checks `existing_pos = acct.positions.get(sym)`:
     `if existing_pos is not None and getattr(existing_pos, "arm", None) != TradingArm.SWING: return False`
   - An order placed on the order book (`engine.working_orders`) does not create a position until filled.
   - Therefore, while an Intraday order is working (e.g. limit order awaiting fill), a concurrent Swing order bypasses the validator.
   - Both orders fill upon price movement, causing cross-arm position netting or corrupting position metadata.

2. **Unsynchronized Market Open Execution (Observation 1.2)**:
   - At 09:30:00 ET, multiple incoming market events (bars, quotes, timer ticks) can trigger `execute_market_open`.
   - `execute_market_open` reads the staged list and checks `active_count < max_concurrent_positions` non-atomically.
   - Multiple threads or coroutines pass the check before any order fills, resulting in multiple buy orders for the same symbol.
   - In empirical testing, this doubled `MU` sizing to $50,000 (violating the $25,000 slot limit) and committed $75,000 total swing capital (violating the $50,000 pool cap).

3. **Intraday Starvation via Unfiltered Portfolio Commitment (Observation 1.3)**:
   - Milestone M9A handoff stated: *"Intraday orders only count non-swing positions when checking `max_concurrent_positions` (3), so swing positions do not consume intraday slots."*
   - However, in `backend/app/main.py:1142`, `execute_strategy_signal` calls `_get_effective_committed_portfolio(account)` without specifying `arm=TradingArm.INTRADAY`.
   - The returned `committed_count` includes swing positions.
   - Consequently, `adaptation_engine` and `risk_engine` count swing positions against the 3-position intraday cap, denying legitimate intraday trades when 2 swing positions are open.

4. **Non-Durable Staged Orders (Observation 1.4)**:
   - Staged swing orders sit overnight for 17.5 hours (16:00 to 09:30).
   - Because `SwingStagedOrderManager` is not connected to `TradingStateStore`, any restart drops all signals, preventing intended 09:30 open executions.

---

## 3. Caveats

- **Robust Flattening Exemption**: Tests confirmed that the 4-phase EOD flattening engine (15:45 lockout, 15:50 purge, 15:55 liquidation, 15:58 audit) and session boundary rollover correctly exempt swing positions, swing orders, and swing brackets under rapid event injection. Emergency ATR stops also fire cleanly during active flattening phases.
- **Account Margin Math Integrity**: `PaperTradingAccount` correctly handles $0 cash balances after two $25,000 swing purchases without triggering false margin calls, and total FINRA 4:1 leverage calculations are mathematically sound.
- No other caveats.

---

## 4. Conclusion

### Formal Verdict: **`REQUEST_CHANGES`**

Four specific defects must be remediated before production certification:

1. **Fix `pre_trade_risk_validator` Symbol Mutual Exclusion (`backend/app/main.py`)**:
   - Update `pre_trade_risk_validator` so that when `is_swing` is True, it rejects if ANY working order or pending bracket exists for that symbol where `arm == TradingArm.INTRADAY`.
   - Also verify `is_symbol_reserved_for_swing` checks for working swing orders in `engine.working_orders`.
2. **Add Execution Lock / Atomic Dequeue in `execute_market_open` (`backend/app/strategies/swing_panic_dip.py`)**:
   - Protect `execute_market_open` with a re-entrant lock or execute an atomic order pop (`self.staged_manager.pop(...)`) before order creation so duplicate concurrent fills on the same symbol are impossible.
3. **Pass `arm=TradingArm.INTRADAY` in `execute_strategy_signal` (`backend/app/main.py`)**:
   - Line 1142: Change to `_get_effective_committed_portfolio(account, arm=TradingArm.INTRADAY)`.
   - Line 1159: Pass `arm=TradingArm.INTRADAY, strategy_id=signal.strategy_id` to `risk_engine.evaluate_order_request`.
4. **Persist Staged Orders in Runtime State (`backend/app/core/runtime_state.py`)**:
   - Add `swing_staged_orders` and `swing_reserved_symbols` to `capture_runtime_state` and `restore_runtime_state`.

---

## 5. Verification Method

To independently verify these findings:

1. **Run Challenger 2 Adversarial Stress Test Suite**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   pytest backend/tests/stress/test_challenger_concurrency_margin_races.py -v
   ```
   *Expected Current Result*: 3 failed, 8 passed, confirming the exact vulnerabilities documented above.
   *Target Result After Remediation*: 11 passed in <0.5s.

2. **Invalidation Conditions**:
   - Any execution where an intraday working order on `AMD` allows a concurrent swing order on `AMD`.
   - Any concurrent execution of `execute_market_open` that allocates more than $25,000 notional to a single symbol or more than $50,000 total.
   - Any scenario where holding 2 swing positions restricts intraday trading to 1 open position.
