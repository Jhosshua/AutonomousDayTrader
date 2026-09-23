# BRIEFING — 2026-09-23T21:26:25Z

## Mission
Investigate backend/app/core/ to architect the 4-phase EOD auto-flattening exemption mechanism, account/risk sizing and capital allocation ($25,000 per slot, max 2 concurrent swing positions) coordinated with the shared $50,000 pool, and state/ledger compatibility for the "2-Day Panic Dip" swing engine.

## 🔒 My Identity
- Archetype: explorer
- Roles: Backend Core, Account & Flattening Exemption Architecture Explorer
- Working directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1
- Original parent: 8f602370-8fd6-478f-9f31-f33f00dc4661
- Milestone: Survey & Architecture Design

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Shared $50,000 account pool; $25,000 notional per swing slot; max 2 concurrent swing positions
- Zero overnight hold for intraday trading (15:58 ET auto-flattening strictly preserved for intraday)
- Multi-day overnight hold strictly preserved and exempt for swing positions, bracket stops, and staged orders
- Zero double-spending or margin collisions between intraday and swing arms
- State models and durable SQLite ledger compatibility

## Current Parent
- Conversation ID: 8f602370-8fd6-478f-9f31-f33f00dc4661
- Updated: 2026-09-23T21:26:25Z

## Investigation State
- **Explored paths**:
  - `backend/app/core/flattening.py`: 4-phase state machine (15:45, 15:50, 15:55, 15:58).
  - `backend/app/main.py`: `handle_flattening_directive`, `_check_session_boundary`, `_reconcile_fills`, `_trip_circuit_breaker`.
  - `backend/app/core/account.py`: `PaperTradingAccount`, `Position`, FINRA 4:1 DTBP, maintenance margin.
  - `backend/app/core/risk.py`: `InstitutionalRiskEngine`, stop bounds `[0.004, 0.040]`, circuit breaker.
  - `backend/app/core/bracket.py`: `DynamicBracketManager`, child OCO orders.
  - `backend/app/core/engine.py`: `ExecutionEngine`, `Order`, `Fill`, order FSM, `cancel_all_orders`.
  - `backend/app/core/persistence.py`: `TradingStateStore`, schema v2, `encode_runtime_value`/`decode_runtime_value`.
  - `backend/app/core/runtime_state.py`: `capture_runtime_state`, `restore_runtime_state`, `validate_runtime_state`.
  - `backend/app/models/events.py`: `OrderEvent`, `PositionState`, `AccountState`.
- **Key findings**:
  1. EOD flattening currently blindly cancels all working orders (Phase 3 & 4) and liquidates all positions in `account.positions`.
  2. Session boundary (`_check_session_boundary` in `main.py`) executes a 5th liquidation phase: cancels all orders, liquidates all positions ("prior-day flatten failed"), and clears all brackets.
  3. `risk.py` rejects stops wider than 4.0% (`max_stop_distance_pct`), whereas swing stops are `2.5 * ATR(14)` (typically 4%–12%).
  4. Sizing: Intraday uses risk-budget sizing; swing requires fixed $25,000 notional per slot with max 2 concurrent positions.
  5. Symbol collision: `AMD` is in both Intraday and Swing universes. Symbol reservation pattern required to prevent netting/FIFO contamination.
  6. Durable SQLite persistence: `encode_runtime_value`/`decode_runtime_value` natively support dataclasses/enums in `backend.app.*`, allowing zero-schema-breakage extension.
- **Unexplored areas**: None for core backend architecture scope.

## Key Decisions Made
- Designed explicit `TradingArm` enum (`INTRADAY`, `SWING`) across `Order`, `Position`, and `BracketOrder`.
- Formulated 4-phase + session boundary exemption protocol in `flattening.py` and `main.py`.
- Formulated dual-sleeve capital model with pre-trade reservation and unencumbered margin excess coordination.
- Established symbol reservation protocol for overlapping universe assets (`AMD`).

## Artifact Index
- handoff.md — Complete 5-component architectural report
- progress.md — Liveness heartbeat
- DISPATCH.md — Task history and instructions
