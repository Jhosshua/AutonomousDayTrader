# Handoff Report: Institutional Risk Guardrails, Dynamic Brackets & 4-Phase Auto-Flattening

**Agent**: `explorer_m1_3`  
**Role**: Institutional Risk Guardrails, Circuit Breakers & 4-Phase Auto-Flattening Specialist  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/explorer_m1_3`  
**Recipient**: `parent` (orchestrator: `f9df3e28-501d-4830-bf1f-140b6216f49e`)  
**Date**: 2026-09-19T23:55:00Z  
**Type**: Hard Handoff (Investigation & Technical Architecture Complete)  

---

## 1. Observation

1. **System Directives & Project Requirements**:
   - `ORIGINAL_REQUEST.md:15`: "Enforce institutional risk guardrails: hard maximum daily loss limit (circuit breaker), per-position risk limits, dynamic stop-loss/take-profit brackets, and zero overnight holds (strictly day trading with automated end-of-day market-on-close flattening)."
   - `ORIGINAL_REQUEST.md:46`: "Daily risk limit circuit breaker triggers and halts new orders if drawdown threshold is breached."
   - `ORIGINAL_REQUEST.md:47`: "All open positions are automatically flattened prior to 16:00 ET market close."
   - `PROJECT.md:58-60`: Feature specifications F5 (Risk Guardrails & Circuit Breakers: hard max daily loss limit $1,500 / 3% drawdown, 1-2% per-position risk limit), F6 (Dynamic Bracket Orders: Target 1 at 1.5R with 50% scale-out, Target 2 at 2.5R or trailing ATR stop), and F7 (Zero Overnight Flattening: 4-phase protocol at 15:45, 15:50, 15:55, 15:58 ET).
   - `PROJECT.md:187-192`: Core layout allocating `backend/app/core/risk.py`, `backend/app/core/bracket.py`, and `backend/app/core/flattening.py`.

2. **Algorithmic Survey Foundations**:
   - `explorer_strategies_survey/survey_report.md:158-208`: Defines daily loss formula $E_0 - E_t \ge \$1,500.00 \implies \text{TRIP\_CIRCUIT\_BREAKER}$, position sizing $q = \min(\lfloor R_{\$} / |P_{\text{entry}} - P_{\text{stop}}| \rfloor, \lfloor E_t \times 0.25 / P_{\text{entry}} \rfloor, \lfloor BP_t / P_{\text{entry}} \rfloor)$, multi-tier targets with breakeven buffer ($+0.02$), and the 4-phase flattening schedule.

3. **Current Filesystem & Codebase State**:
   - `backend/app/` contains `__init__.py` and `replay/`. `backend/app/core/` does not yet exist and is ready for clean instantiation according to this blueprint.
   - Test suites in `tests/e2e/` are structured by `test_writer_e2e` and require matching backend unit test coverage in `backend/tests/unit/` (`test_risk.py`, `test_bracket.py`, `test_flattening.py`).

---

## 2. Logic Chain

1. **Step 1 (Daily Drawdown & Circuit Breaker)**: From Observation 1 & 2, starting account equity is $E_0 = \$50,000.00$. A $3.0\%$ daily loss equals exactly $\$1,500.00$. To prevent catastrophic overnight or midday blowout, drawdown must account for both realized and unrealized mark-to-market PnL: $\text{Drawdown}_{\$} = \max(0, E_0 - E_t)$. Therefore, the Risk Engine must evaluate $E_t$ synchronously on every incoming tick/quote/bar. When $\text{Drawdown}_{\$} \ge \$1,500.00$, the circuit breaker trips immediately, moving the engine to `HALTED_DAILY_LOSS`, purging working orders, and market-liquidating all positions.

2. **Step 2 (Per-Position Sizing & Invariant Dollar Risk)**: To achieve high Sharpe ratio and survive losing streaks, individual trade risk must be constrained to 1–2% ($R_{\$} = \$500$ to $\$1,000$). Using $q = \lfloor R_{\$} / |P_{\text{entry}} - P_{\text{stop}}| \rfloor$, volatility naturally scales share size downward when stops are wide. Clamping stop distances between $0.4\%$ and $4.0\%$ prevents both division-by-zero micro-stops and excessively wide unmanageable trades. Capital concentration is capped at $25\%$ of equity ($12,500 cash) and max 3 concurrent positions, bounding total portfolio risk to $3 \times \$500 = \$1,500.00$ (matching the daily breaker).

3. **Step 3 (Dynamic Multi-Tier Brackets & OCO Coordination)**: Setting Target 1 at 1.5R and scaling out $50\%$ ($\lfloor q/2 \rfloor$) locks in early profit and finances the remaining risk. Instantly ratcheting the stop on the remaining $50\%$ to breakeven ($P_{\text{entry}} + \$0.02$ friction buffer) creates a free trade. Target 2 at 2.5R or ATR trailing stop ($P_{\text{peak}} - 1.5 \cdot \text{ATR}_{14}$) lets runners capture extended trends while guaranteeing upward stop monotonicity. Atomic OCO cancellation guarantees that when the stop fills, limit targets are immediately cancelled, avoiding orphan orders.

4. **Step 4 (Automated 4-Phase Zero-Overnight Flattening)**: To eliminate overnight gap risk, the closing process cannot be left to an uncoordinated rush at 15:59 ET. A 4-phase ladder is necessary:
   - 15:45 ET (`ENTRY_LOCKOUT`): Stops new entries while allowing open trades 10 minutes to resolve.
   - 15:50 ET (`ORDER_PURGE`): Clears pending entry orders and tightens existing stops.
   - 15:55 ET (`MANDATORY_LIQUIDATION`): Closes all positions via market orders.
   - 15:58 ET (`ZERO_AUDIT`): Verifies zero exposure ($|\mathcal{P}| == 0$), triggering emergency retry if any fill was delayed, certifying a clean flat book before the 16:00 ET bell.
   Decoupling time via `MarketClock` enables deterministic execution under both live Eastern Time and accelerated replay test feeds.

---

## 3. Caveats

1. **Broker Fill Slippage during Circuit Breaker Liquidation**:
   - In extreme fast-moving market flash crashes, market liquidation orders may experience slippage beyond the $\$1,500.00$ threshold. The simulator fill model accounts for slippage based on spread and bar volume participation ($\text{Slippage} = \frac{1}{2}\text{Spread} + \gamma \sigma \sqrt{q / V_{\text{bar}}}$).
2. **Odd Share Rounding**:
   - For odd total quantities ($q=7$), $50\%$ scale-out produces integer splits ($q_1=3, q_2=4$). The blueprint guarantees $\sum q_i == q$ and modifies remaining stop order quantity to exactly match $q_2$.
3. **Timezone Transitions**:
   - The US market clock adheres strictly to `America/New_York` (accounting for EDT/EST daylight saving shifts). Using `zoneinfo.ZoneInfo("America/New_York")` prevents UTC mismatch bugs.

---

## 4. Conclusion

The technical architecture and file-by-file implementation blueprint for `backend/app/core/risk.py`, `backend/app/core/bracket.py`, and `backend/app/core/flattening.py` are complete, mathematically formulated, and ready for code implementation by the M1 development agents. The architecture strictly satisfies all user criteria:
- Hard $1,500 / 3.0% daily loss circuit breaker with immediate liquidation and lockout.
- 1–2% per-position risk sizing with 25% concentration and 3-position concurrency caps.
- Dynamic 1.5R (50% scale-out + breakeven ratchet) and 2.5R / ATR trailing stop brackets with atomic OCO synchronization.
- Deterministic 4-phase zero-overnight flattening with zero-position audit verification before 16:00 ET.
- Decoupled `MarketClock` ensuring 100% testability across synthetic replay and the Monday live dry run.

---

## 5. Verification Method

Downstream implementation agents and reviewers can independently verify the implementation using:

1. **Authoritative Blueprint Inspection**:
   - Inspect `/Users/mo/AutonomousDayTrader/.agents/explorer_m1_3/survey_report.md` for class definitions, method signatures, mathematical formulations, and FSM transition diagrams.

2. **Unit Test Execution (upon code implementation)**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   python3.11 -m pytest backend/tests/unit/test_risk.py -v
   python3.11 -m pytest backend/tests/unit/test_bracket.py -v
   python3.11 -m pytest backend/tests/unit/test_flattening.py -v
   ```

3. **Invalidation Conditions**:
   - Any order execution permitted after daily drawdown exceeds $\$1,500.00$.
   - Any position initiated after 15:45:00 ET.
   - Any open position or active working order remaining past 15:58:00 ET.
   - Any trailing stop modification that moves adversely against the position (loosening).
   - Any bracket target limit order remaining active in the book after the stop-loss order is filled.
