# Empirical Adversarial Stress Test Report: Cross-Arm Isolation & Persistence

**Challenger**: Challenger 2 (`teamwork_preview_challenger`)  
**Role**: Adversarial Cross-Arm Isolation & Persistence Challenger (`critic`, `specialist`)  
**Date**: 2026-09-24  
**Target Milestone**: Swing Trading Engine & Intraday Isolation Hardening  
**Target Codebase**: `AutonomousDayTrader`  
**Gate Verdict**: **REJECT** (CRITICAL Vulnerability Discovered in AMD Mutual Exclusion)

---

## 1. Executive Summary

Challenger 2 constructed and executed an adversarial stress test harness targeting three core architectural subsystems in `AutonomousDayTrader`:
1. **Cross-Arm Circuit Breaker Isolation**: Tripping hard daily loss ($1,500 intraday drawdown) via `main._trip_circuit_breaker` while active swing positions (`LRCX`, `MU`) and intraday positions (`AAPL`, `TSLA`) are held.
2. **Mutual Exclusion Locking for AMD**: Probing cross-arm isolation when `AMD` is reserved in `swing_reserved_symbols` or actively held in `account.positions`.
3. **DailyBarStore Persistence Across Restart**: Aggregating multi-symbol daily bars, committing state to SQLite via `TradingStateStore`, wiping process memory, and verifying 100% roundtrip restoration fidelity and indicator equivalence.

### Key Results Matrix
| Subsystem Tested | Adversarial Scenarios | Empirical Status | Verdict |
|---|---|---|---|
| **1. Cross-Arm Circuit Breaker Isolation** | Intraday $1,500 drawdown breach; working order cancellation; swing position preservation; post-halt emergency stop execution | 3/3 PASSED | **APPROVE** |
| **2. AMD Mutual Exclusion Locking** | Swing reservation lock; Swing position hold; Intraday BUY; Intraday SELL short; Swing SELL on Intraday hold; Post-exit release | 4/6 PASSED, **2 FAILED** | **REJECT (CRITICAL DEFECT)** |
| **3. DailyBarStore Persistence Restart** | SQLite checkpoint roundtrip across 6 symbols; in-flight and finalized bars; 200 SMA, 5 SMA, 14 ATR, RSI(2), 60d RS equivalence | 2/2 PASSED | **APPROVE** |

---

## 2. Detailed Empirical Findings by Subsystem

### Subsystem 1: Cross-Arm Circuit Breaker Isolation (STATUS: ROBUST / PASS)

#### Test Implementation
Test module: `backend/tests/stress/test_cross_arm_isolation_persistence.py::TestCrossArmCircuitBreakerIsolation`
- `test_circuit_breaker_preserves_swing_positions_and_liquidates_intraday`
- `test_swing_emergency_stop_operational_during_circuit_breaker_halt`
- `test_new_intraday_entries_blocked_during_circuit_breaker_halt`

#### Adversarial Attack & Observations
1. **Setup**: The virtual $50,000 account held:
   - Swing Arm: Active position in `LRCX` (30 shares @ $800.00, stop at $762.50, ATR = 15.00), plus working limit order in `KLAC` (35 shares @ $700.00).
   - Intraday Arm: Long position in `AAPL` (100 shares @ $150.00), Short position in `TSLA` (50 shares @ $220.00), plus working limit order in `NVDA` (50 shares @ $120.00).
2. **Breaker Trigger**: An intraday drawdown of $1,600.00 was recorded, breaching the $1,500 limit. `_trip_circuit_breaker(timestamp)` was executed.
3. **Verified Invariants**:
   - `account.status` transitioned to `CIRCUIT_HALTED`.
   - `AAPL` and `TSLA` were 100% liquidated/flattened (`shares == 0`).
   - The intraday working order on `NVDA` was cancelled.
   - `LRCX` swing position was **100% intact**: exactly 30 shares, entry price $800.00, stop-loss $762.50, entry ATR 15.00.
   - The swing working order on `KLAC` remained working in `engine.working_orders`.
   - When market price of `LRCX` fell to $760.00 (below $762.50 stop), `check_intraday_emergency_stops` successfully triggered, submitted a sell order, applied slippage, and flattened `LRCX`, proving emergency stops remain active even during `CIRCUIT_HALTED`.
   - New intraday entry orders were strictly rejected with `CIRCUIT_BREAKER_HALTED` by both `pre_trade_risk_validator` and `engine.submit_order`.

---

### Subsystem 2: Mutual Exclusion Locking for AMD (STATUS: CRITICAL DEFECT / FAIL)

#### Test Implementation
Test module: `backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking`
- `test_amd_reserved_for_swing_rejects_intraday_buy` (PASSED)
- `test_amd_reserved_for_swing_rejects_intraday_sell_short` (PASSED)
- `test_amd_held_by_swing_rejects_intraday_buy` (PASSED)
- `test_amd_held_by_swing_probe_intraday_sell_vulnerability` (**FAILED**)
- `test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability` (**FAILED**)
- `test_amd_clean_release_after_swing_closure` (PASSED)

#### Pathology & Empirical Demonstration
When `AMD` is reserved in `swing_reserved_symbols` before fill, both BUY and SELL intraday orders are rejected.
**HOWEVER**, once `AMD` is filled and held as an active Swing position (`account.positions["AMD"]`, `arm=TradingArm.SWING`, `side=PositionSide.LONG`):

1. An intraday strategy (e.g. `orb`, `vwap_pullback`, `news_momentum`, or `mean_reversion`) emits an `OrderSide.SELL` order (an intraday short entry).
2. The order is submitted to `pre_trade_risk_validator(order, acct)`.
3. In `backend/app/main.py:251-255`:
   ```python
   existing_pos = acct.positions.get(sym)
   is_exit = False
   ...
   elif existing_pos is not None:
       if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
           is_exit = True
       elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
           is_exit = True
   ```
4. Because `existing_pos` exists and has `side == PositionSide.LONG`, and the intraday order has `order.side == OrderSide.SELL`, `is_exit` evaluates to **`True`**!
5. Lines 262-285 check:
   ```python
   # Symbol reservation / mutual exclusion check:
   if not is_exit:
       ...
       if is_symbol_reserved_for_swing(sym, acct, target_engine):
           return False, f"SYMBOL_RESERVED_FOR_SWING: ..."
   ```
   Because `is_exit == True`, the entire mutual exclusion block is **completely bypassed**!
6. In `backend/app/core/risk.py:164-172`:
   ```python
   if is_exit:
       return RiskCheckResult(
           approved=True,
           reason="APPROVED_EXIT: Position reducing or liquidation order approved", ...
       )
   ```
   The order is approved unconditionally as an `APPROVED_EXIT`!
7. The intraday SELL order executes in `engine._execute_fill`, selling shares out of Swing's position:
   - Empirical proof: An initial Swing position of 100 shares was reduced to 60 shares by an intraday sell order of 40 shares (`AMD shares = 60, arm = SWING`).
   - If the intraday short order is for 150 shares, it completely wipes Swing's 100 shares and establishes a 50-share intraday short position (`AMD shares = 50, side = SHORT, arm = INTRADAY`).
   - Symmetrically, if Intraday holds `AMD` long, a Swing SELL order is approved as `APPROVED_EXIT`, cannibalizing Intraday's position!

#### Verbatim Failure Output from Pytest
```
FAILED backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_amd_held_by_swing_probe_intraday_sell_vulnerability
AssertionError: VULNERABILITY DETECTED in pre_trade_risk_validator: Intraday SELL order on Swing-held AMD was APPROVED!
Reason: APPROVED_EXIT: Position reducing or liquidation order approved. Intraday arm can liquidate or cannibalize Swing's long position!
assert True is False

FAILED backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability
AssertionError: VULNERABILITY DETECTED in pre_trade_risk_validator: Swing SELL order on Intraday-held AMD was APPROVED!
Reason: APPROVED_EXIT: Position reducing or liquidation order approved. Swing arm can cannibalize Intraday position!
assert True is False
```

#### Remediation Requirement (For Worker Agent)
In `backend/app/main.py:251`, `existing_pos` must only trigger `is_exit = True` if the existing position belongs to the **same trading arm** as the order:
```python
    elif existing_pos is not None:
        existing_arm = getattr(existing_pos, "arm", TradingArm.INTRADAY)
        if existing_arm == order_arm:
            if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
                is_exit = True
            elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
                is_exit = True
```
When `existing_arm != order_arm`, `is_exit` remains `False`, allowing `is_symbol_reserved_for_swing` to reject the order with `SYMBOL_RESERVED_FOR_SWING` (or `SWING_REJECTED` in reverse).

---

### Subsystem 3: DailyBarStore Persistence Across Restart (STATUS: ROBUST / PASS)

#### Test Implementation
Test module: `backend/tests/stress/test_cross_arm_isolation_persistence.py::TestDailyBarStorePersistenceRestart`
- `test_daily_bar_store_roundtrip_across_sqlite_checkpoints` (PASSED)
- `test_indicator_calculations_identical_post_restart` (PASSED)

#### Adversarial Attack & Observations
1. **Multi-Symbol Daily Bar Checkpoint**:
   - Seeded 6 symbols (`QQQ`, `LRCX`, `KLAC`, `MU`, `AMD`, `GS`) with finalized daily bars for day 1 (`2026-09-22`).
   - Ingested 1-minute bars for day 2 (`2026-09-23`) and finalized bars for `QQQ`, `LRCX`, and `AMD`.
   - Appended in-flight (unfinalized, `finalized=False`) daily bars for `MU` and `GS`.
   - Committed full state to SQLite file database using `TradingStateStore.save_checkpoint`.
2. **Process Wipe**:
   - Explicitly deleted all memory structures (`bar_store`, `aggregator`, `store`).
3. **Recovery Across Restart**:
   - Reopened SQLite database from disk with fresh `TradingStateStore`.
   - Read checkpoint payload using `load_checkpoint`.
   - Reconstructed fresh `DailyBarStore` via `restore_runtime_state`.
4. **Verification**:
   - All 6 symbols restored.
   - Exact bar counts verified across all symbols.
   - For every bar: `symbol`, `date`, `open`, `high`, `low`, `close`, `volume`, and `finalized` boolean matched with zero floating-point drift (`abs < 1e-4`).
   - In-flight bars for `MU` and `GS` retained `finalized == False`.
   - Calculated indicators on restored bars (`200 SMA`, `5 SMA`, `14 ATR`, `RSI(2)`, `60d RS vs QQQ`) matched pre-checkpoint values with `1e-6` precision.

---

## 3. Test Execution Logs

Command executed:
`pytest backend/tests/stress/test_cross_arm_isolation_persistence.py -v`

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/mo/AutonomousDayTrader
configfile: pytest.ini
collected 12 items

backend/tests/stress/test_cross_arm_isolation_persistence.py::TestCrossArmCircuitBreakerIsolation::test_circuit_breaker_preserves_swing_positions_and_liquidates_intraday PASSED [  8%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestCrossArmCircuitBreakerIsolation::test_swing_emergency_stop_operational_during_circuit_breaker_halt PASSED [ 16%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestCrossArmCircuitBreakerIsolation::test_new_intraday_entries_blocked_during_circuit_breaker_halt PASSED [ 25%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_amd_reserved_for_swing_rejects_intraday_buy PASSED [ 33%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_amd_reserved_for_swing_rejects_intraday_sell_short PASSED [ 41%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_amd_held_by_swing_rejects_intraday_buy PASSED [ 50%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_amd_held_by_swing_probe_intraday_sell_vulnerability FAILED [ 58%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_amd_clean_release_after_swing_closure PASSED [ 66%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_reverse_intraday_held_amd_blocks_swing_entry PASSED [ 75%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestAmdMutualExclusionLocking::test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability FAILED [ 83%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestDailyBarStorePersistenceRestart::test_daily_bar_store_roundtrip_across_sqlite_checkpoints PASSED [ 91%]
backend/tests/stress/test_cross_arm_isolation_persistence.py::TestDailyBarStorePersistenceRestart::test_indicator_calculations_identical_post_restart PASSED [100%]

=================================== FAILURES ===================================
2 failed, 10 passed in 0.25s
================================================================================
```

---

## 4. Port & Process Hygiene Verification

All ports verified clean and released:
`lsof -i :8000 -i :8005 -i :8080 -i :3005` returned zero running processes (clean exit 1).

---

## 5. Conclusion & Recommendation

1. **Circuit Breaker Isolation**: Meets all specifications. Intraday liquidations do not touch swing positions or working swing orders.
2. **DailyBarStore Persistence**: Meets all specifications. Full SQLite round-trip preservation verified.
3. **AMD Mutual Exclusion**: **DEFECTIVE**. Fails core invariant because cross-arm short sell orders are misidentified as position-reducing exits. Must be patched by adding `existing_arm == order_arm` check in `main.py:251`.

**Final Gate Verdict**: **REJECT**.
