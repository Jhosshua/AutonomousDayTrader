# Analysis Report: Cross-Arm Mutual Exclusion Bypass Remediation

**Agent**: Explorer 3 Iteration 2 (`teamwork_preview_explorer`)  
**Role**: Mutual Exclusion & Isolation Explorer  
**Date**: 2026-09-24  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_r2`  
**Target Vulnerability**: `backend/app/main.py:251–255` (`pre_trade_risk_validator` cross-arm `is_exit` bypass)

---

## 1. Executive Summary

A critical security/risk bypass exists in `backend/app/main.py:251–255` within `pre_trade_risk_validator`. When classifying whether an incoming order is an `is_exit` (position-reducing or liquidation) order, the validator evaluates only whether the order's side opposes the existing position's side (`LONG` vs `SELL`, or `SHORT` vs `BUY`), without verifying that the existing position's trading arm matches the order's trading arm (`existing_pos.arm == order_arm`).

Consequently:
1. When a multi-day swing position is held long in `AMD` (`TradingArm.SWING`), any incoming Intraday short entry (`OrderSide.SELL`, `TradingArm.INTRADAY`) is falsely marked `is_exit = True`.
2. Setting `is_exit = True` bypasses the symbol reservation check (`if not is_exit:` on line 263), skipping `is_symbol_reserved_for_swing("AMD")`.
3. In `InstitutionalRiskEngine.evaluate_order_request` (`backend/app/core/risk.py:164–172`), `if is_exit:` immediately returns `RiskCheckResult(approved=True, reason="APPROVED_EXIT: Position reducing or liquidation order approved")`, bypassing all circuit breakers, sector exposure limits, and arm separation constraints.
4. When executed, `PaperTradingAccount.apply_fill` applies the intraday sell against the swing position, liquidating shares of Swing's position or flipping it into an intraday short position.
5. Symmetrically, when `AMD` is held long by Intraday, an incoming Swing sell order is falsely classified as `is_exit = True` and approved, cannibalizing Intraday's position.

Adversarial stress tests in `backend/tests/stress/test_cross_arm_isolation_persistence.py` failed 2 of 12 tests due to this exact flaw.

---

## 2. Code Trace & Failure Anatomy

### 2.1 The Vulnerability in `backend/app/main.py`
In `backend/app/main.py`, lines 231–285:
```python
def pre_trade_risk_validator(order: Any, acct: PaperTradingAccount) -> tuple[bool, str]:
    """Validate order against Institutional Risk Engine, active flattening lockout, and arm separation."""
    order_arm = getattr(order, "arm", TradingArm.INTRADAY)
    order_strat = getattr(order, "strategy_id", None)
    is_swing = (order_arm == TradingArm.SWING or order_strat == "swing_panic_dip")
    sym = order.symbol.upper()

    # Differentiate position-reducing / liquidation orders from position-opening orders
    existing_pos = acct.positions.get(sym)
    is_exit = False
    if getattr(order, "strategy_id", None) in (
        "CIRCUIT_BREAKER",
        "AUTO_FLATTEN",
        "EMERGENCY_SWEEP",
        "MANUAL_FLATTEN",
        "NEWS_CONTRADICTION",
        "NEWS_CONTRADICTION_CIRCUIT_BREAKER",
        "SESSION_BOUNDARY_LIQUIDATION",
    ):
        is_exit = True
    elif existing_pos is not None:
        if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
            is_exit = True
        elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
            is_exit = True
```

Lines 252–255 assign `is_exit = True` whenever `existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL`.
There is zero check that `existing_pos` belongs to the same arm as `order` (`order_arm`).

### 2.2 Skipping Symbol Reservation & Mutual Exclusion
On line 263:
```python
    # Symbol reservation / mutual exclusion check:
    if not is_exit:
        target_engine = globals().get("engine")
        if is_swing:
            # Swing cannot enter if an INTRADAY position is currently open for this symbol
            if existing_pos is not None and (
                getattr(existing_pos, "arm", None) != TradingArm.SWING
                and getattr(existing_pos, "strategy_id", "") != "swing_panic_dip"
            ):
                return False, f"SWING_REJECTED: Symbol {sym} is currently held by Intraday strategy"
            # Swing cannot enter if an INTRADAY order is currently working for this symbol
            if target_engine and hasattr(target_engine, "working_orders"):
                for w_order in target_engine.working_orders.values():
                    if w_order.symbol.upper() == sym and (
                        getattr(w_order, "arm", None) != TradingArm.SWING
                        and getattr(w_order, "strategy_id", "") != "swing_panic_dip"
                    ):
                        return False, f"SWING_REJECTED: Symbol {sym} has active working order in Intraday strategy"
        else:
            # Intraday cannot enter if symbol is reserved for Swing or currently held by Swing
            if is_symbol_reserved_for_swing(sym, acct, target_engine):
                return False, f"SYMBOL_RESERVED_FOR_SWING: Intraday entry for {sym} rejected because symbol is reserved/held by Swing Engine"
```
Because `is_exit` is set to `True`, lines 263–284 are skipped entirely!

### 2.3 Bypass in `risk_engine.evaluate_order_request`
On line 320, `is_exit` is forwarded to `risk_engine`:
```python
    res = risk_engine.evaluate_order_request(
        ...
        is_exit=is_exit,
        ...
    )
```
In `backend/app/core/risk.py:164–172`:
```python
        # Position reducing or liquidation orders bypass entry lockouts, circuit halts, and sizing constraints
        if is_exit:
            return RiskCheckResult(
                approved=True,
                reason="APPROVED_EXIT: Position reducing or liquidation order approved",
                requested_qty=requested_qty,
                authorized_qty=requested_qty,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
            )
```
The risk engine returns `approved = True`, and `pre_trade_risk_validator` returns `(True, "APPROVED_EXIT: Position reducing or liquidation order approved")`.

### 2.4 Downstream Position Cannibalization in Fill Processing
In `backend/app/core/account.py:301–325` (`PaperTradingAccount.apply_fill`):
Positions in `account.positions` are keyed by symbol (`symbol = symbol.upper()`).
When the intraday sell order executes:
```python
        if existing_pos.side == PositionSide.LONG:
            if side_norm == "BUY":
                ...
            else: # side == "SELL"
                if qty < existing_pos.shares:
                    ...
                    existing_pos.shares -= qty
                elif qty == existing_pos.shares:
                    del self.positions[symbol]
                else: # qty > existing_pos.shares: FLIP
                    flip_shares = qty - existing_pos.shares
                    new_pos = Position(
                        symbol=symbol,
                        side=PositionSide.SHORT,
                        shares=flip_shares,
                        ...
                        arm=arm, # flips Swing's position into an Intraday short!
                    )
```
The intraday order directly decreases `existing_pos.shares` or deletes/flips Swing's multi-day position.

---

## 3. Secondary Vulnerability Scan (Cross-Arm Isolation Across `main.py`)

A comprehensive scan of `backend/app/main.py` revealed four additional locations where position-reducing or protective logic does not verify `arm` matching:

1. **`_get_effective_committed_portfolio` (`backend/app/main.py:198–203`)**:
   ```python
   sym = order.symbol.upper()
   pos = acct.positions.get(sym)
   is_reducing = bool(
       pos and (
           (pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
           (pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
       )
   )
   ```
   If an intraday working sell order exists while Swing holds `AMD`, `is_reducing` evaluates to `True`, failing to count the working order towards committed exposure.
   **Fix**: Verify `pos` and `order` belong to the same arm before treating the order as reducing.

2. **Persistence Recovery Halt Order Sweeper (`backend/app/main.py:464–470`)**:
   ```python
   position = account.positions.get(order.symbol)
   is_reducing = bool(
       position and (
           (position.side == PositionSide.LONG and order.side == OrderSide.SELL) or
           (position.side == PositionSide.SHORT and order.side == OrderSide.BUY)
       )
   )
   ```
   An opening order from one arm opposing a position in the other arm would be deemed "protective/reducing" and spared from cancellation during recovery halts.
   **Fix**: Ensure `position` and `order` match arms.

3. **Phase 2 15:50 ET EOD Purge (`backend/app/main.py:1575–1581`)**:
   ```python
   pos = account.positions.get(order.symbol.upper())
   is_protective = bool(
       pos and (
           (pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
           (pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
       )
   )
   ```
   An unfilled intraday entry order opposing a swing position is mistakenly spared from EOD purge because it is treated as a protective stop for Swing.
   **Fix**: Require `pos` to be an intraday position (`getattr(pos, "arm", None) != TradingArm.SWING`).

4. **Manual Order Endpoint (`backend/app/main.py:2126–2134`)**:
   `is_reducing` checks `existing_pos` without comparing `req.arm` with `existing_pos.arm`.

---

## 4. Proposed Fix Formulation

### 4.1 Primary Target: `backend/app/main.py:238–256`

Replace lines 238–256 with:

```python
    # Differentiate position-reducing / liquidation orders from position-opening orders
    existing_pos = acct.positions.get(sym)
    existing_arm = getattr(existing_pos, "arm", None) or TradingArm.INTRADAY if existing_pos else None
    existing_strat = getattr(existing_pos, "strategy_id", "") or ""
    existing_is_swing = bool(
        existing_pos is not None
        and (
            existing_arm == TradingArm.SWING
            or existing_strat == "swing_panic_dip"
            or (isinstance(existing_arm, str) and str(existing_arm).upper() == "SWING")
        )
    )

    is_exit = False
    if getattr(order, "strategy_id", None) in (
        "CIRCUIT_BREAKER",
        "AUTO_FLATTEN",
        "EMERGENCY_SWEEP",
        "MANUAL_FLATTEN",
        "NEWS_CONTRADICTION",
        "NEWS_CONTRADICTION_CIRCUIT_BREAKER",
        "SESSION_BOUNDARY_LIQUIDATION",
    ):
        # Emergency/system liquidation orders are intraday; do not treat them as exits of swing positions
        if existing_pos is None or not existing_is_swing:
            is_exit = True
    elif existing_pos is not None and (existing_is_swing == is_swing):
        if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
            is_exit = True
        elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
            is_exit = True
```

### 4.2 Mechanism Breakdown
1. **Arm Equivalence**: `existing_is_swing == is_swing` ensures that:
   - Swing exit orders (`is_swing == True`) can ONLY exit Swing positions (`existing_is_swing == True`).
   - Intraday exit orders (`is_swing == False`) can ONLY exit Intraday positions (`existing_is_swing == False`).
2. **Defect Resolution for Intraday SELL on Swing AMD**:
   - `existing_is_swing` is `True` (AMD held by Swing).
   - `is_swing` is `False` (intraday sell order).
   - `existing_is_swing == is_swing` is `False`.
   - `is_exit` remains `False`.
   - Execution proceeds to line 263 (`if not is_exit:`).
   - Line 282 calls `is_symbol_reserved_for_swing("AMD", acct, target_engine)` -> returns `True`.
   - Line 283 executes: returns `(False, "SYMBOL_RESERVED_FOR_SWING: Intraday entry for AMD rejected because symbol is reserved/held by Swing Engine")`.
   - Intraday order is cleanly and strictly rejected!
3. **Defect Resolution for Swing SELL on Intraday AMD**:
   - `existing_is_swing` is `False` (AMD held by Intraday).
   - `is_swing` is `True` (swing order).
   - `existing_is_swing == is_swing` is `False`.
   - `is_exit` remains `False`.
   - Execution proceeds to line 263 (`if not is_exit:`).
   - Line 267 executes: `existing_pos is not None and getattr(existing_pos, "arm", None) != TradingArm.SWING`.
   - Line 271 executes: returns `(False, "SWING_REJECTED: Symbol AMD is currently held by Intraday strategy")`.
   - Swing order is cleanly and strictly rejected!

---

## 5. Empirical Verification & Test Results

The proposed fix was verified via dynamic test harness injection:

1. **Adversarial Stress Test Suite (`backend/tests/stress/test_cross_arm_isolation_persistence.py`)**:
   - Before fix: **10 passed, 2 failed**
     - `test_amd_held_by_swing_probe_intraday_sell_vulnerability` -> FAILED
     - `test_reverse_swing_sell_on_intraday_held_amd_probe_vulnerability` -> FAILED
   - With fix: **12 passed, 0 failed in 0.08s (100% pass rate)**
     - Circuit breaker isolation: 3/3 PASSED
     - AMD mutual exclusion locking: 7/7 PASSED (all probes pass)
     - DailyBarStore persistence across restart: 2/2 PASSED

2. **Full Backend Pytest Regression Suite (`backend/tests`)**:
   - Result: **478 passed, 0 failed in 7.29s (100% pass rate, zero regressions)**.

3. **Port & Process Hygiene Audit**:
   - Verified ports 3005, 8000, 8005, 8080 liberated; zero lingering daemons.
