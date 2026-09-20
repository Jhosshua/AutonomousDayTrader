# Milestone 1 Handoff Report: $50,000 Paper Trading Account & Order Lifecycle Architecture

**Agent**: `explorer_m1_2`  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/explorer_m1_2`  
**Recipient**: Parent Orchestrator (`f9df3e28-501d-4830-bf1f-140b6216f49e`)  
**Target Milestone**: Milestone 1 (`engine_ingestion`)  
**Date**: 2026-09-19  

---

## 1. Observation

1. **Original Request Requirements**:
   - `ORIGINAL_REQUEST.md:14`: "Manage a self-contained $50,000 virtual paper trading account tracking cash, equity, buying power, open positions, unrealized/realized PnL, and full execution order lifecycle."
   - `ORIGINAL_REQUEST.md:45`: "Paper account tracks $50,000 initial balance accurately with real-time mark-to-market PnL and trade history logging."
2. **Project Specification & Interface Contracts**:
   - `PROJECT.md:57`: "F4: $50,000 Paper Account: State machine tracking Cash, Equity, 4:1 Day Trading Buying Power ($200k), Positions, Realized/Unrealized PnL, Order lifecycle".
   - `PROJECT.md:120`: Interface contracts define `OrderEvent`, `FillEvent`, and `AccountState`:
     `AccountState: cash: float, equity: float, buying_power: float, realized_pnl: float, unrealized_pnl: float, daily_drawdown: float, is_circuit_broken: bool, positions: Dict[str, Position]`.
   - `PROJECT.md:188`: File layout specifies:
     - `backend/app/core/account.py`: $50,000 Paper Trading Account state machine.
     - `backend/app/core/engine.py`: Main trading engine coordinator & event loop.
3. **Quantitative & Algorithmic Foundations**:
   - `explorer_strategies_survey/survey_report.md:27-50`:
     - $C_0 = \$50,000.00$.
     - PDT 4:1 Day Trading Buying Power: $BP_{\text{intraday}, t} = 4 \times (E_t - \text{Initial Margin Held}_t)$ with $BP_{\text{max}} = \$200,000.00$ at inception.
     - Per-position capital cap of $25\%$ of max buying power ($\$50,000$).
   - `explorer_strategies_survey/survey_report.md:100-127`:
     - Microstructure fill formulation:
       $$\text{Slippage} = \frac{1}{2}\text{BidAskSpread} + \gamma \cdot \sigma_{1\text{m}} \cdot \sqrt{\frac{q_{\text{order}}}{V_{\text{bar}}}}$$
     - SEC Section 31 fee on equity sells: $0.0000278 \times \text{Principal}$ (rounded up to nearest cent).
     - FINRA TAF on equity sells: $\min(\$8.30, 0.000166 \times q_{\text{sell}})$.
4. **Environment Constraints**:
   - Python 3.11.15 is installed at `/Users/mo/.local/bin/python3.11`.
   - Required libraries `pydantic` (v2.13.4), `typing_extensions`, `httpx` are already present in the local environment.

---

## 2. Logic Chain

1. **Account State Machine Isolation & Determinism**:
   - *Premise (Observation 1, 2)*: The paper trading engine must operate self-contained, without dependency on external broker APIs, to enable reproducible CI/CD testing and Monday dry runs.
   - *Deduction*: `PaperTradingAccount` in `backend/app/core/account.py` must maintain an internal, atomic double-entry-style cash and position ledger with zero network calls.
2. **FINRA Rule 4210 Compliance & Buying Power**:
   - *Premise (Observation 2, 3)*: Equity $\ge \$25,000$ qualifies for Pattern Day Trader (PDT) 4:1 leverage, requiring $25\%$ maintenance margin for longs and $30\%$ (min $\$5.00$/share) for shorts under FINRA Rule 4210(f)(10).
   - *Deduction*: At starting equity $E_0 = \$50,000.00$ with zero positions, $MMR_0 = 0 \implies DTBP_0 = 4 \times \$50,000 = \$200,000.00$. As positions open, $MMR_t$ dynamically locks margin excess $ME_t = E_t - MMR_t$, constraining further orders. If equity drops below $\$25,000$, the account drops to cash-only basis ($1:1$).
3. **8-State Order Lifecycle Invariants**:
   - *Premise (User Request Objective)*: The system requires an explicit 8-state order lifecycle: `CREATED -> SUBMITTED -> ACCEPTED -> PARTIALLY_FILLED -> FILLED / CANCELLED / REJECTED / EXPIRED`.
   - *Deduction*: Any transition outside this strict directed acyclic graph (e.g. attempting to cancel a `FILLED` order or execute a `REJECTED` order) must raise `InvalidOrderStateTransitionError`. Fills must update order cumulative fill counts and weighted average fill price atomically before altering the account ledger.
4. **Microstructure Execution Realism**:
   - *Premise (Observation 3)*: Paper trading models that assume instant full fills at the limit or mid price generate unrealistic alpha in backtests.
   - *Deduction*: The simulator in `backend/app/core/engine.py` must enforce:
     - Half-spread + Kyle's lambda square-root participation slippage on market orders.
     - Price improvement when bars open beyond a limit order price.
     - Volume liquidity participation caps ($\rho = 10\%$ of 1-minute bar volume), triggering realistic `PARTIALLY_FILLED` transitions for large orders.
     - Adverse $1.5\times$ slippage penalties when stop-loss market triggers fire.
     - Regulatory SEC and FINRA TAF deductions exclusively on sell fills.
5. **Comprehensive Event Sourcing & Auditability**:
   - *Premise (Observation 1, 2)*: Acceptance criteria require complete audit logging and sub-second UI WebSocket streaming.
   - *Deduction*: Every order state transition must generate an immutable `OrderAuditRecord` capturing timestamps, triggers, prices, quantities, fees, and post-trade cash/equity balances.

---

## 3. Caveats

1. **Short Borrow Fees**: In the intraday paper trading model, positions are strictly closed prior to 16:00 ET (zero overnight holds). Consequently, overnight hard-to-borrow (HTB) lending fees are not applicable and are modeled as $\$0.00$.
2. **Exchange Tier Rebates**: Exchange liquidity maker rebates / taker fees are omitted in favor of the retail paper broker model (zero commission, standard SEC Section 31 fee and FINRA TAF).
3. **Tick-by-Tick Quote Density**: When replaying feeds that provide only 1-minute OHLCV bars without quotes, the fill simulator synthetically calculates half-spreads from the bar close price ($0.04\%$ mega-cap baseline spread) and derives bar return volatility from High minus Low.

---

## 4. Conclusion

The technical architecture and concrete implementation blueprints for both `backend/app/core/account.py` and `backend/app/core/engine.py` have been formulated and documented in `/Users/mo/AutonomousDayTrader/.agents/explorer_m1_2/survey_report.md`.
The blueprints define:
- Complete typed dataclasses (`Position`, `AccountSnapshot`, `Order`, `Fill`, `OrderAuditRecord`, `OrderState`).
- Concrete methods for buying power calculation, position scaling, partial exits, position flipping, and mark-to-market valuation.
- Realistic microstructure matching logic for Market, Limit, and Stop orders with dynamic slippage and volume participation limits.
- Exact mathematical formulas for SEC Section 31 and FINRA TAF fees.
- 27 unit test specifications (15 for `account.py`, 12 for `engine.py`) covering all state transitions, boundary limits, and edge cases.

The design is fully aligned with `PROJECT.md`, `ORIGINAL_REQUEST.md`, and upstream/downstream milestone interfaces.

---

## 5. Verification Method

To independently verify this blueprint:
1. **Inspect Survey Report**:
   ```bash
   cat /Users/mo/AutonomousDayTrader/.agents/explorer_m1_2/survey_report.md
   ```
   Verify that all class blueprints, formulas, and state transition matrices are present.
2. **Verify Mathematical Consistency**:
   - Verify FINRA Rule 4210 Day Trading Buying Power formula: $DTBP = 4.0 \times (E_t - MMR_t)$.
   - Verify SEC Section 31 formula: $\lceil 0.0000278 \times \text{Principal} \times 100 \rceil / 100$.
   - Verify FINRA TAF formula: $\min(8.30, \text{round}(0.000166 \times q, 2))$.
3. **Downstream Unit Test Execution (Once Implemented)**:
   When implementation workers create the source files, run:
   ```bash
   /Users/mo/.local/bin/python3.11 -m pytest tests/unit/test_account.py tests/unit/test_engine.py -v
   ```
   All 27 specified test cases should execute with 100% pass rate.
