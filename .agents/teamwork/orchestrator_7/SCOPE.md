# Scope: Swing Trading Engine Integration ("2-Day Panic Dip")

## Architecture
AutonomousDayTrader integrates an autonomous multi-day swing trading engine running the "2-Day Panic Dip" Connors RSI-2 strategy across 5 certified stocks (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`), sharing the $50,000 paper trading pool ($25,000 per slot, max 2 concurrent swing positions). The swing engine is strictly exempt from the 15:45–15:58 ET intraday auto-flattening engine and session-boundary sweeps, operates causal rolling daily indicators, features a unified Obsidian dark operator dashboard with segmented toggle, and undergoes 3x adversarial reviews, replay verification, and remote Railway deployment.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F24 | Swing Execution Engine ("2-Day Panic Dip") | 7 quantitative rules (Macro Floor 200 SMA, 60d RS vs QQQ, Panic RSI(2)<10, 48h earnings blackout, 16:00 qualification -> 09:30 open buy, $25k sizing with max 2 concurrent positions, 2.5x ATR(14) emergency stop, 5-day SMA / RSI(2)>70 / 5-day time stop exits) | M9B | ORIGINAL_REQUEST §R1 |
| F25 | Architectural Separation & Flattening Exemption | `TradingArm` tagging on positions/orders, exemption from 15:45-15:58 EOD flattening and session boundary sweeps, shared $50k account margin coordination, and symbol-level mutual exclusion for `AMD` | M9A | ORIGINAL_REQUEST §R2 |
| F26 | Market Leadership, Calendar & Signal Pipeline | Causal daily bar pipeline (seed fixture, 16:00 close aggregator, SQLite cache), rolling daily indicators with zero lookahead, 48h earnings calendar lookup with cached fallback, and `SwingStagedOrderManager` for overnight order staging | M9B | ORIGINAL_REQUEST §R3 |
| F27 | Unified Obsidian Dark Operator Interface | Next.js / Tailwind segmented toggle between "Intraday Day Trader" and "Swing Mean-Reversion", candidate watchlist card, active swing positions table with ATR stop meters and holding day counters, operator manual overrides, and real-time WebSocket state streaming | M9C | ORIGINAL_REQUEST §R4 |
| F28 | 3x Independent Adversarial Reviews & Zero-Lookahead Audit | Pass 1 (Mathematical & Lookahead Audit), Pass 2 (State Machine & Flattening Exemption Audit), Pass 3 (Execution Timing & Order Lifecycle Audit), and Forensic Auditor Verification | M9D | ORIGINAL_REQUEST §R5 |
| F29 | Deterministic E2E Replay, Visual QA, & Remote Deployment | Deterministic multi-day replay test covering all rules and exit conditions, 100% existing test pass rate, desktop & mobile visual QA, port hygiene, and remote Railway deployment verification | M9E | ORIGINAL_REQUEST §R6 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M9A | `core_flattening_exemption` | `TradingArm` enum, position/order tagging, 4-phase EOD flattening exemption, session boundary protection, pre-trade risk gate arm routing, symbol reservation for `AMD`, $25k/slot margin coordination | None | IN_PROGRESS |
| M9B | `indicators_calendar_signals` | Causal daily indicators (200 SMA, 60d RS vs QQQ, Connors RSI-2, 14 ATR, 5 SMA), daily bars seed & aggregator, 48h earnings calendar with fallback, 16:00 qualification -> 09:30 open staging & execution | M9A | PLANNED |
| M9C | `obsidian_dark_ui` | Segmented mode toggle, swing telemetry bar, candidate watchlist card, active swing positions table (ATR stops, day counter), manual overrides, backend WebSocket streaming & action handlers | M9A, M9B | PLANNED |
| M9D | `adversarial_3x_audit` | Pass 1: Math & Lookahead Audit; Pass 2: State Machine & Flattening Exemption Audit; Pass 3: Execution Timing Audit; Forensic Auditor Integrity Gate | M9A, M9B, M9C | PLANNED |
| M9E | `replay_qa_deployment` | Multi-day replay test suite, full test suite 100% pass, visual QA (desktop & mobile), process hygiene (zero ports), Railway git push & remote health verification | M9D | PLANNED |

## Interface Contracts
### 1. TradingArm & Position/Order Contract
- `TradingArm` enum: `INTRADAY = "INTRADAY"`, `SWING = "SWING"`.
- `Position.arm`: `TradingArm = TradingArm.INTRADAY` (default).
- `Order.arm`: `TradingArm = TradingArm.INTRADAY` (default).
- `BracketOrder.arm`: `TradingArm = TradingArm.INTRADAY` (default).
- Flattening engine directives:
  - `directive.cancel_all_orders` cancels only `arm == TradingArm.INTRADAY`.
  - `directive.liquidate_all_positions` liquidates only `arm == TradingArm.INTRADAY`.
  - `execute_phase_4_audit` checks `unclosed_intraday_positions == 0` and `working_intraday_orders == 0`.
  - `_check_session_boundary` in `main.py` ignores positions and orders where `arm == TradingArm.SWING`.

### 2. Risk Engine & Pre-Trade Gate Contract
- If `order.strategy_id == "swing_panic_dip"` or `order.arm == TradingArm.SWING`:
  - Enforce swing sizing: fixed $25,000 notional per slot.
  - Enforce concurrency cap: maximum 2 active swing positions.
  - Enforce swing stop bounds: stop price strictly below entry price; bypass intraday 4.0% max stop ceiling.
  - Enforce earnings blackout: reject entry if earnings within 48 hours.
  - Enforce symbol reservation: lock out intraday orders if symbol is held or staged by swing engine.

### 3. Indicator & Signal Contract
- All daily indicators operate on closed daily bars (`date <= today`).
- Signals qualify at 16:00:05 ET following daily bar close.
- Qualified orders are staged in `SwingStagedOrderManager` and dispatched at 09:30:00 ET open of next trading day.
- Exit conditions: prior close > 5 SMA, prior RSI(2) > 70, holding days == 5, or earnings tomorrow.
