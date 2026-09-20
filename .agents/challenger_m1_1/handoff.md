# Handoff Report: Milestone 1 (`engine_ingestion`) Empirical Stress Testing & Verification

**Agent**: `challenger_m1_1` (Critic / Specialist)  
**Milestone**: Milestone 1 (`engine_ingestion`)  
**Parent Orchestrator**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  
**Date**: 2026-09-19  
**Verdict**: **`REQUEST_CHANGES`** (Critical Flaws Found)

---

## 1. Observation

Direct empirical observations, reproduction code, and execution outputs from the workspace:

### 1.1 Test Suite Execution
Created empirical stress test harness: `backend/tests/unit/test_empirical_stress_m1.py`.
Executed command:
```bash
PYTHONPATH=. pytest backend/tests/unit/test_empirical_stress_m1.py -v
```
Output:
```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/mo/AutonomousDayTrader
collected 11 items

backend/tests/unit/test_empirical_stress_m1.py::test_circuit_breaker_premature_trip_defect PASSED [  9%]
backend/tests/unit/test_empirical_stress_m1.py::test_circuit_breaker_exact_trip_at_1500_and_1501 PASSED [ 18%]
backend/tests/unit/test_empirical_stress_m1.py::test_circuit_breaker_liquidation_rejection_defect PASSED [ 27%]
backend/tests/unit/test_empirical_stress_m1.py::test_auto_flattening_lockout_defect PASSED [ 36%]
backend/tests/unit/test_empirical_stress_m1.py::test_phase4_audit_directive_discarded_in_main PASSED [ 45%]
backend/tests/unit/test_empirical_stress_m1.py::test_race_condition_concurrent_orders_during_breaker_trip PASSED [ 54%]
backend/tests/unit/test_empirical_stress_m1.py::test_four_phase_flattening_timing_boundaries PASSED [ 63%]
backend/tests/unit/test_empirical_stress_m1.py::test_process_hygiene_clean_teardown PASSED [ 72%]
backend/tests/unit/test_empirical_stress_m1.py::test_oracle_target_circuit_breaker_must_flatten_positions XFAIL [ 81%]
backend/tests/unit/test_empirical_stress_m1.py::test_oracle_target_1555_must_flatten_all_positions XFAIL [ 90%]
backend/tests/unit/test_empirical_stress_m1.py::test_oracle_target_no_premature_breaker_at_1499_99 XFAIL [100%]

========================= 8 passed, 3 xfailed in 0.10s =========================
```

### 1.2 Defect 1: Circuit Breaker Fails to Flatten Positions (Liquidation Order Rejection)
In `backend/app/main.py` lines 158–169:
```python
    if status == BreakerStatus.HALTED_DAILY_LOSS:
        account.status = account.status.__class__.CIRCUIT_HALTED
        engine.cancel_all_orders("CIRCUIT_BREAKER_HALT")
        # Liquidate positions
        for sym, pos in list(account.positions.items()):
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            liq_order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="CIRCUIT_BREAKER"
            )
            engine.submit_order(liq_order.id)
            engine.process_bar(sym, bar.close, bar.close, bar.close, bar.close, 100000, bar.timestamp)
```
When `engine.submit_order(liq_order.id)` is called:
1. In `backend/app/core/account.py` line 132:
```python
    def can_afford(self, symbol: str, side: str, qty: int, est_price: float) -> Tuple[bool, str]:
        if self.status not in (AccountStatus.ACTIVE, AccountStatus.MARGIN_CALL):
            return False, f"Account is not ACTIVE (current status: {self.status.value})"
```
Because `account.status` was set to `CIRCUIT_HALTED` at line 159, `can_afford` immediately returns `False, "Account is not ACTIVE (current status: CIRCUIT_HALTED)"`.
2. Furthermore, in `backend/app/main.py` lines 37–64 (`pre_trade_risk_validator`) and `backend/app/core/risk.py` line 140:
```python
        if self.status != BreakerStatus.ARMED:
            return RiskCheckResult(
                approved=False,
                reason=f"CIRCUIT_BREAKER_HALTED: Trading halted due to maximum daily loss ({self.status.value})",
                ...
            )
```
The risk engine rejects all orders when breaker status is not `ARMED`. Neither check distinguishes between an order opening a position and an order closing/liquidating a position.
**Verbatim execution result**:
`liq_order.status: OrderState.REJECTED`
`liq_order.reject_reason: "Account is not ACTIVE (current status: CIRCUIT_HALTED)"`
`Positions remaining after circuit breaker: ['AAPL']` (Position remained open!).

### 1.3 Defect 2: 15:55 Mandatory Liquidation Fails (Zero Overnight Exposure Violated)
In `backend/app/main.py` lines 37–64:
```python
def pre_trade_risk_validator(order: Any, acct: PaperTradingAccount) -> tuple[bool, str]:
    is_lockout = flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING
    ...
    res = risk_engine.evaluate_order_request(
        ...
        is_entry_lockout_active=is_lockout,
    )
    return res.approved, res.reason
```
And in `backend/app/core/risk.py` lines 152–161:
```python
        # 2. Session Time Lockout Check
        if is_entry_lockout_active:
            return RiskCheckResult(
                approved=False,
                reason="ENTRY_LOCKOUT_ACTIVE: Session closeout protocol active, new entries forbidden",
                ...
            )
```
At 15:55 ET, `flattening_engine.current_phase` is `FlatteningPhase.MANDATORY_LIQUIDATION`. Therefore `is_lockout` is `True`.
When `handle_flattening_directive` attempts to submit market liquidation orders for open positions, `pre_trade_risk_validator` intercepts the order and rejects it because `is_entry_lockout_active` is True.
**Verbatim execution result**:
`Phase 3 Liq order status: OrderState.REJECTED`
`reason: "ENTRY_LOCKOUT_ACTIVE: Session closeout protocol active, new entries forbidden"`
`Positions remaining after 15:55 liquidation: ['AAPL', 'TSLA']`
`Phase 4 Audit passed?: False, unclosed: ['AAPL', 'TSLA']`
All positions remained open past 16:00 ET market close into overnight.

### 1.4 Defect 3: Premature Circuit Breaker Trip at $1,497.50 Drawdown
In `backend/app/core/risk.py` lines 97–106:
```python
        dd_dollars = max(0.0, round(self.config.starting_equity - equity, 2))
        dd_pct = round(dd_dollars / self.config.starting_equity, 4)
        self.current_drawdown_dollars = dd_dollars
        self.current_drawdown_pct = dd_pct

        if self.status == BreakerStatus.HALTED_DAILY_LOSS:
            return self.status

        if dd_dollars >= self.config.hard_max_daily_loss_dollars or dd_pct >= self.config.hard_max_daily_loss_pct:
            self.status = BreakerStatus.HALTED_DAILY_LOSS
```
For equity = $48,502.50 (drawdown = $1,497.50):
`dd_dollars = 1497.50 < 1500.00`.
However:
`1497.50 / 50000.0 = 0.02995`.
`round(0.02995, 4) = 0.0300`.
Because `dd_pct >= self.config.hard_max_daily_loss_pct` (`0.0300 >= 0.030`) is evaluated, the circuit breaker TRIPS PREMATURELY at $1,497.50, before reaching the contractual $1,500.00 loss limit.
At $1,499.99 drawdown, `status` becomes `HALTED_DAILY_LOSS` instead of staying `ARMED`.

### 1.5 Defect 4: Phase 4 Audit Directive Discarded in `main.py`
In `backend/app/main.py` lines 233–237:
```python
    if directive.run_audit:
        flattening_engine.execute_phase_4_audit(
            open_positions=account.positions,
            working_orders=list(engine.working_orders.values()),
        )
```
In `backend/app/core/flattening.py` lines 200–241, `execute_phase_4_audit()` returns a `FlatteningDirective` specifying `action_required="AUDIT_FAILED_EMERGENCY_SWEEP"` and `liquidate_all_positions=True` if open positions remain.
In `main.py`, the return value is discarded. The emergency sweep directive is never passed to execution or acted upon.

### 1.6 Process Hygiene and Port Release
Executed command:
```bash
lsof -i :8005 -i :8080 -i :3005
```
Result: Exit code 0, output `CLEAN: All allocated ports are completely free`. Zero lingering processes.

---

## 2. Logic Chain

1. **Premise 1 (R1 Contractual Mandate)**:
   - Institutional risk rules require: "hard maximum daily loss limit (circuit breaker), per-position risk limits, dynamic stop-loss/take-profit brackets, and zero overnight holds (strictly day trading with automated end-of-day market-on-close flattening)."
   - Acceptance criteria require: "Daily risk limit circuit breaker triggers and halts new orders if drawdown threshold is breached," and "All open positions are automatically flattened prior to 16:00 ET market close."

2. **Inference from Observation 1.2 (Circuit Breaker Liquidation)**:
   - When drawdown reaches $1,500.00, the circuit breaker must halt trading AND flatten positions.
   - However, `account.status` is transitioned to `CIRCUIT_HALTED` before liquidating. `account.can_afford()` blocks all orders when status is not `ACTIVE` or `MARGIN_CALL`, and `pre_trade_risk_validator()` blocks all orders when `risk_engine.status != BreakerStatus.ARMED`.
   - Because neither check distinguishes position reduction/closure from position creation/expansion, liquidation orders are rejected with `OrderState.REJECTED`.
   - Consequently, open positions are never flattened upon circuit breaker trip.

3. **Inference from Observation 1.3 (Zero-Overnight Flattening)**:
   - At 15:55 ET, the auto-flattening engine commands `LIQUIDATE_ALL_POSITIONS`.
   - The validation pipeline evaluates `flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING` as `is_entry_lockout_active = True`.
   - `risk_engine.evaluate_order_request()` treats all order requests as new entries when `is_entry_lockout_active` is True, rejecting them with `ENTRY_LOCKOUT_ACTIVE`.
   - Because closing market orders are rejected, positions remain open through Phase 4 (15:58 ET) and past 16:00 ET.
   - Consequently, the zero-overnight guarantee is completely breached.

4. **Inference from Observation 1.4 (Premature Breaker Trip)**:
   - `round(dd_dollars / 50000.0, 4)` causes any loss $\ge \$1,497.50$ to round up to $0.0300$, triggering the circuit breaker prematurely.

---

## 3. Caveats

- The concurrency stress harness (`test_race_condition_concurrent_orders_during_breaker_trip`) confirmed that dictionary mutations under GIL do not throw unhandled exceptions during simultaneous cancel sweeps; however, atomic queue-based order gating should still be enforced in the order submission pipeline.
- All allocated ports (8005, 8080, 3005) are currently free; no lingering background processes or open file descriptors were left behind.

---

## 4. Conclusion

**Verdict: `REQUEST_CHANGES`**

Milestone 1 CANNOT be approved in its current state. While ingestion clients, event bus routing, and the isolated state machine models are well-architected, the system fails its core safety guarantees:
1. **Critical Defect**: Positions are NOT flattened when the circuit breaker trips.
2. **Critical Defect**: Positions are NOT flattened at 15:55 ET, violating the zero-overnight invariant.
3. **High Defect**: Circuit breaker trips prematurely at $1,497.50 instead of $1,500.00 due to 4-decimal percentage rounding.
4. **Medium Defect**: The emergency sweep directive returned by `execute_phase_4_audit()` at 15:58 ET is discarded in `main.py`.

### Required Action Items for `worker_m1`:
1. **Allow Position Closing Orders in `account.can_afford()`**:
   - If an order reduces or closes an existing position (e.g. `side == "SELL"` when holding `LONG`, or `side == "BUY"` when holding `SHORT`), allow it even when `self.status == AccountStatus.CIRCUIT_HALTED`.
2. **Distinguish Entries vs. Exits in Pre-Trade Risk Gate (`risk.py` & `main.py`)**:
   - In `InstitutionalRiskEngine.evaluate_order_request()` and `pre_trade_risk_validator()`, check if the order is marked as a liquidation/exit order (or reduces position size).
   - If an order is a position close/liquidation (`strategy_id in ("CIRCUIT_BREAKER", "AUTO_FLATTEN", "MANUAL_FLATTEN")` or explicit `is_exit=True` flag), bypass `ENTRY_LOCKOUT_ACTIVE` and `CIRCUIT_BREAKER_HALTED` checks.
3. **Fix Circuit Breaker Drawdown Precision in `risk.py`**:
   - Compare `dd_dollars >= self.config.hard_max_daily_loss_dollars` primarily, and if evaluating percentage, do not pre-round `dd_pct` to 4 decimals with `round(..., 4)` before testing `>= 0.030`. Use exact floating point: `dd_pct = dd_dollars / self.config.starting_equity` without rounding up, or verify `dd_dollars >= 1500.00`.
4. **Act on `execute_phase_4_audit` Directive in `main.py`**:
   - Capture `audit_res = flattening_engine.execute_phase_4_audit(...)`.
   - If `audit_res.liquidate_all_positions` is True, invoke the emergency sweep liquidation loop.
5. **Verify Against `test_empirical_stress_m1.py`**:
   - Ensure all 11 tests in `backend/tests/unit/test_empirical_stress_m1.py` pass (removing `xfail` once fixed).

---

## 5. Verification Method

To independently reproduce the findings and verify the fixes:

1. **Run the Empirical Stress Test Suite**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   PYTHONPATH=. pytest backend/tests/unit/test_empirical_stress_m1.py -v
   ```
   *Current Result*: 8 passed, 3 xfailed (verifying the exact reproduction of the 3 defects).
   *Target Result after fix*: 11 passed in < 0.2s.

2. **Run Standard Regression Suites**:
   ```bash
   PYTHONPATH=. pytest backend/tests/unit -v
   PYTHONPATH=. pytest tests/e2e -v
   ```

3. **Verify Port Hygiene**:
   ```bash
   lsof -i :8005 -i :8080 -i :3005
   ```
   *Expected Result*: Empty output / non-zero exit code.
