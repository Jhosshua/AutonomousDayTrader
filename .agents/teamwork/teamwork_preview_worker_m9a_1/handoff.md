# Handoff Report — Milestone M9A (Backend Core Worker)

## 1. Observation

### Source Code and Configuration Changes
1. **`backend/app/core/account.py`**:
   - Lines 21-25: Added `TradingArm(str, Enum)` with values `INTRADAY = "INTRADAY"` and `SWING = "SWING"`.
   - Lines 41-46: Extended `Position` dataclass with `arm: TradingArm = TradingArm.INTRADAY`, `strategy_id: Optional[str] = None`, `holding_days: int = 0`, `stop_loss_price: Optional[float] = None`, `entry_atr: Optional[float] = None`, `entry_date: Optional[date] = None`.
   - Lines 65-72: Updated `Position.to_state()` to serialize `arm`, `strategy_id`, `holding_days`, `stop_loss_price`.
   - Lines 129-145: Updated `PaperTradingAccount.apply_fill()` to accept and preserve `arm`, `strategy_id`, `stop_loss_price`, `entry_atr`, `entry_date`.
2. **`backend/app/models/events.py`**:
   - Lines 36-41: Tagged `OrderEvent` with `arm: Optional[TradingArm] = None`, `strategy_id: Optional[str] = None`.
   - Lines 65-70: Tagged `PositionState` with `arm: TradingArm = TradingArm.INTRADAY`, `strategy_id: Optional[str] = None`, `holding_days: int = 0`, `stop_loss_price: Optional[float] = None`.
3. **`backend/app/core/engine.py`**:
   - Lines 52-54: Tagged `Order` with `arm: TradingArm = TradingArm.INTRADAY` and `strategy_id: Optional[str] = None`.
   - Lines 79-92: Updated `create_order()` to accept `arm` and `strategy_id`.
   - Lines 150-165: Updated `cancel_all_orders(reason: str, arm: Optional[TradingArm] = None)` to filter order cancellation by arm.
   - Lines 220-234: Updated `execute_fill()` to forward `arm`, `strategy_id`, and stop parameters into `account.apply_fill()`.
4. **`backend/app/core/bracket.py`**:
   - Line 59: Tagged `BracketOrder` with `arm: TradingArm = TradingArm.INTRADAY`.
   - Lines 121, 176: Updated `create_bracket()` with `arm: TradingArm = TradingArm.INTRADAY`.
5. **`backend/app/core/flattening.py`**:
   - Lines 262-290: In `execute_phase_4_audit`, exempt swing positions (`getattr(pos, "arm", None) == TradingArm.SWING`) and swing working orders (`getattr(order, "arm", None) == TradingArm.SWING`) from cancellation and liquidation sweeps.
6. **`backend/app/core/risk.py`**:
   - Lines 72-73: Added `max_concurrent_swing_positions: int = 2` and `swing_slot_notional: float = 25000.0` to `RiskEngineConfig`.
   - Lines 208-250: In `InstitutionalRiskEngine.evaluate_order_request`:
     - Added arm check: if `arm == TradingArm.SWING` or `strategy_id == "swing_panic_dip"`, bypass the intraday 4.0% maximum stop-loss ceiling (`MAX_STOP_LOSS_PCT`).
     - Swing orders enforce `max_concurrent_swing_positions` (2) across open positions and pending entries.
     - Swing orders enforce `swing_slot_notional` ($25,000 max committed notional per symbol).
     - Intraday orders only count non-swing positions when checking `max_concurrent_positions` (3), so swing positions do not consume intraday slots.
7. **`backend/app/main.py`**:
   - Lines 113-132: Added symbol reservation set `swing_reserved_symbols: Set[str] = set()` and helper functions `reserve_symbol_for_swing()`, `release_symbol_for_swing()`, `is_symbol_reserved_for_swing()`.
   - Lines 483-518: Updated `_get_effective_committed_portfolio(..., arm: Optional[TradingArm] = None)` to filter committed portfolio calculations by trading arm.
   - Lines 650-710: Updated `pre_trade_risk_validator`:
     - Dispatches swing metadata (`arm`, `strategy_id`, `stop_price`).
     - Rejects intraday orders if the symbol is reserved for swing (`is_symbol_reserved_for_swing(symbol)`).
     - Rejects swing orders if an intraday position or working order already exists in that symbol (mutual exclusion).
   - Lines 770-830: Updated `handle_flattening_directive`:
     - Phase 1 (`ORDER_PURGE`): calls `engine.cancel_all_orders(reason=..., arm=TradingArm.INTRADAY)`. Working swing orders remain active.
     - Phase 2-4 (`LIQUIDATE_STEP_1/2`, `FORCE_MARKET_FLATTEN`): iterates only over positions where `pos.arm != TradingArm.SWING` and `pos.strategy_id != "swing_panic_dip"`.
     - Phase 4 audit: sweeps only intraday positions and orders.
     - Sets `account.status = AccountStatus.EOD_FLAT` only if no open positions remain across all arms; otherwise leaves active if swing positions persist.
   - Lines 860-915: Updated `_check_session_boundary`:
     - Cancels only intraday working orders during session rollover.
     - Liquidates only unclosed intraday positions that survived flattening.
     - Preserves swing positions and executes `pos.holding_days += 1`.
     - Clears only intraday brackets (`b.arm != TradingArm.SWING`), preserving swing brackets.
8. **`backend/tests/test_swing_flattening_exemption.py`**:
   - 11 unit tests covering all Milestone M9A criteria.
   - Test execution result:
     `pytest backend/tests/test_swing_flattening_exemption.py -v` -> 11 passed in 0.15s.
   - Full test suite execution result:
     `pytest backend/tests/ -q` -> 366 passed in 4.07s.
   - Linter verification:
     `python3 -m ruff check backend/app/core/account.py backend/app/core/risk.py backend/app/main.py backend/app/models/events.py backend/tests/test_swing_flattening_exemption.py` -> All checks passed!

---

## 2. Logic Chain

1. **Tagging and Backward Compatibility (Observation 1, 2, 3, 4)**:
   The existing system represents orders, brackets, and positions as intraday items without awareness of multi-day swing trades. By introducing `TradingArm(str, Enum)` with default value `TradingArm.INTRADAY`, all existing models, methods, unit tests, and database persistence serializers remain 100% backward compatible without changing existing signatures or behavior.
2. **EOD Flattening Exemption (Observation 5, 7)**:
   At 15:45-15:58 ET, `ZeroOvernightFlatteningEngine` dispatches 4 flattening phases. In `flattening.py`'s `execute_phase_4_audit` and `main.py`'s `handle_flattening_directive`, filtering by `arm != TradingArm.SWING` guarantees that swing positions and protective swing stop orders are preserved, while 100% of intraday positions and orders are liquidated and purged.
3. **Session Rollover Exemption (Observation 7)**:
   At the start of each new ET trading day, `_check_session_boundary` executes. By filtering order purges and emergency liquidations to intraday items only, swing positions survive overnight into the new day, and their `holding_days` attribute increments by 1.
4. **Arm-Aware Risk Invariants (Observation 6, 7)**:
   The institutional risk engine protects intraday trading with a 4.0% maximum stop ceiling and 3 concurrent positions. Swing trading requires wider stops (2.5x ATR, often 5-10%) and a dedicated 2-position limit capped at $25,000 notional per slot. By bifurcating evaluation in `evaluate_order_request`, swing orders safely bypass the 4.0% ceiling and track independent concurrency and notional metrics without relaxing or interfering with intraday invariants.
5. **Symbol Reservation & Mutual Exclusion (Observation 7)**:
   Because AMD is in the intraday pool (NVDA, TSLA, AAPL, AMD, MSFT) and is also a primary swing candidate, concurrent trades could cause order collision and wash-trading risks. Implementing `reserve_symbol_for_swing` in `main.py` enforces immediate mutual exclusion: intraday entries in AMD are rejected if reserved for swing, and swing entries are rejected if intraday has open orders or positions in that symbol.
6. **Integration and Full Regression Pass (Observation 8)**:
   All 11 unit tests in `test_swing_flattening_exemption.py` pass, and all 355 pre-existing tests continue to pass (total 366/366 passed), proving that no existing intraday behavior was regressed.

---

## 3. Caveats

- **No Live Broker API Integration in M9A**: Milestone M9A implements the backend core execution models, flattening exemptions, risk gating, and session rollover logic in the paper trading engine, risk engine, and state machines. Downstream Alpaca live-broker bracket submission for swing orders is part of worker M9C / M9D.
- **AMD Reservation Persistence**: Symbol reservation is currently managed in-memory via `swing_reserved_symbols` set in `main.py`. During durable state restore, any active swing positions in `main.account.positions` with symbol `AMD` are tracked; downstream workers can add persistent reservation flags if required by persistence schemas.

---

## 4. Conclusion

Milestone M9A is fully implemented, verified, and complete:
1. `TradingArm` enum is defined and tagged on `Position`, `Order`, `BracketOrder`, and corresponding event schemas.
2. 4-Phase EOD auto-flattening exempts swing positions and swing orders.
3. Session boundary rollover preserves swing positions, increments `holding_days`, and purges only intraday orders.
4. Arm-aware risk engine bypasses 4.0% stop ceiling for swing ATR stops, enforces 2 concurrent swing positions, and limits each slot to $25,000 notional.
5. AMD symbol reservation prevents intraday collisions.
6. 100% of tests pass across the entire repository (366/366).

---

## 5. Verification Method

To independently verify this milestone:

1. **Run the Swing Flattening Exemption Test Suite**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   pytest backend/tests/test_swing_flattening_exemption.py -v
   ```
   *Expected outcome*: 11 passed in <0.5s.

2. **Run Full Backend Test Suite**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   pytest backend/tests/ -q
   ```
   *Expected outcome*: 366 passed with 0 failures, 0 errors.

3. **Run Linter / Py-Compile**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   python3 -m ruff check backend/app/core/account.py backend/app/core/risk.py backend/app/main.py backend/app/models/events.py backend/tests/test_swing_flattening_exemption.py
   python3 -m py_compile backend/app/core/account.py backend/app/core/bracket.py backend/app/core/engine.py backend/app/core/flattening.py backend/app/core/risk.py backend/app/main.py backend/app/models/events.py backend/tests/test_swing_flattening_exemption.py
   ```
   *Expected outcome*: Clean exit code 0.

4. **Invalidation Conditions**:
   - Any test failure in `pytest backend/tests/`.
   - EOD flattening cancelling a working order tagged `TradingArm.SWING`.
   - EOD flattening liquidating a position tagged `TradingArm.SWING`.
   - Intraday engine placing an order on AMD while `is_symbol_reserved_for_swing("AMD")` is true.
