# Handoff Report: Frontend Obsidian Dark UI & E2E Replay Infrastructure

**Agent**: Explorer 3 (Frontend Obsidian Dark UI & E2E Replay Explorer)  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_3`  
**Date**: 2026-09-23T21:32:00Z  
**Recipient**: Project Orchestrator (`orchestrator_7`)

---

## 1. Observation

### 1.1 Frontend Architecture & Single-Service Hosting
1. **Tech Stack & Configuration**:
   - `frontend/package.json` (lines 12–29): Next.js `15.1.7`, React `19.0.0`, Framer Motion `12.4.7`, Tailwind CSS `3.4.17`, Lucide React `0.475.0`.
   - `frontend/next.config.mjs` (line 4): `output: "export"`. Next.js builds as a pure static HTML/JS export into `frontend/out`.
   - `Dockerfile` (lines 3–25): Multi-stage build:
     - Stage 1 (`ui-build`): Runs `npm run build` producing `frontend/out`.
     - Stage 2 (`python:3.12-slim`): Copies `frontend/out` into the Python container. Runs `uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8005}`.
   - `backend/app/main.py` (lines 2030–2032):
     ```python
     STATIC_UI_DIR = Path(__file__).resolve().parents[2] / "frontend" / "out"
     if STATIC_UI_DIR.is_dir():
         app.mount("/", StaticFiles(directory=STATIC_UI_DIR, html=True), name="ui")
     ```
     Railway runs a **single unified service**: FastAPI owns the public port (port `8005` or dynamic `$PORT`), serving the static Next.js frontend at `/`, the REST API at `/api/*`, and the real-time WebSocket stream at `/ws/ui`.

2. **Obsidian Dark Design System & Design Tokens**:
   - `frontend/tailwind.config.js` (lines 12–27): Custom color palette:
     - `obsidian`: `950: "#000000"`, `900: "#0a0a0c"`, `800: "#121218"`, `700: "#181822"`, `600: "#222230"`.
     - `apple`: `green: "#30d158"`, `red: "#ff453a"`, `blue: "#0a84ff"`, `purple: "#5e5ce6"`, `orange: "#ff9f0a"`, `teal: "#64d2ff"`.
   - `frontend/app/globals.css` (lines 17–28): Glassmorphism classes: `backdrop-filter: blur(24px)`, `-webkit-backdrop-filter: blur(24px)`, `tabular-nums` for numerical data.
   - `frontend/components/AmbientBackground.tsx` (lines 15–67): Dynamic background glow driven by portfolio PnL and circuit breaker status.

3. **WebSocket Client & State Synchronization**:
   - `frontend/hooks/useTradingStream.ts` (lines 92–220): Connects to `ws://127.0.0.1:8005/ws/ui` with auto-reconnection (exponential backoff up to 10s) and automatic endpoint detection for production hostnames.
   - Handles incoming `payload.type === "STATE_UPDATE"`.
   - Dispatches client-to-server action messages over WebSocket:
     - `FLATTEN_POSITION`: `{"action": "FLATTEN_POSITION", "symbol": sym}`
     - `FLATTEN_ALL`: `{"action": "FLATTEN_ALL"}`
     - `TIGHTEN_STOP`: `{"action": "TIGHTEN_STOP", "symbol": sym, "new_stop": price}`
   - Fallback HTTP polling every 5s (`/api/account`, `/api/positions`, `/api/audit`) when WebSocket disconnects.

4. **Backend WebSocket Server & Broadcast Payload**:
   - `backend/app/main.py` (lines 851–930, `broadcast_ui_state`):
     - Broadcast throttled to 100ms (`_UI_BROADCAST_THROTTLE_SEC = 0.10`).
     - Serializes `account`, `market_context`, `strategies`, `primary_position`, `all_positions`, `positions_count`, `working_orders_count`, `ledger_revision`, `persistence`, `ingestion`, `recent_news`, `recent_activity`.
   - `backend/app/main.py` (lines 1984–2026, `ui_websocket_endpoint`):
     - Bi-directional WebSocket endpoint handling `action` commands.

### 1.2 Existing Flattening Mechanism & Gap for Swing Positions
- `backend/app/core/flattening.py` (lines 14–22, 69–76): 4-Phase EOD Auto-Flattening Engine:
  - 15:45 ET (Phase 1): `ENTRY_LOCKOUT`
  - 15:50 ET (Phase 2): `ORDER_PURGE`
  - 15:55 ET (Phase 3): `MANDATORY_LIQUIDATION`
  - 15:58 ET (Phase 4): `ZERO_AUDIT`
- `backend/app/main.py` (lines 1328–1340):
  ```python
  if directive.liquidate_all_positions:
      now_dt = directive.timestamp
      for sym, pos in list(account.positions.items()):
          ...
          fills = _flatten_symbol(sym, pos.market_price, now_dt)
  ```
  Currently, `account.positions` contains only day-trading positions, so all positions are unconditionally liquidated. There is currently no `trading_arm` or `is_swing` tag on positions or orders, meaning any swing position would be liquidated at 15:55 ET unless explicitly exempted.

### 1.3 E2E Test Runner & Replay Infrastructure
1. **E2E Test Runner**:
   - `tests/e2e/runner.py`: Pytest-based runner executing Tier 1 through Tier 5 tests plus UI resilience checks.
   - Enforces pre/post-flight port auditing on ports `[8080, 8005, 8000, 3005]`.
   - Verification command: `python3 tests/e2e/runner.py` passed all 320 tests in 26.59s with exit code 0.
2. **Replay Framework**:
   - `backend/app/replay/mock_relay.py`: `MockAlpacaRelayServer` runs on port 8080. Implements Stock WS (`/v2/stocks`), News WS (`/news`), REST `GET /vix`, and historical bars proxy (`GET /data/v2/stocks/{symbol}/bars`).
   - `backend/app/replay/feed_player.py`: `FeedPlayer` steps through recorded or synthetic JSON fixtures at 1x to 100x speed.
   - `scripts/run_integrated_monday_dry_run.py`: Exercises full production wiring (`lifespan`, `MockAlpacaRelayServer`, `FeedPlayer`, `CaptureSocket` capturing UI WebSocket state updates, EOD flattening audit). Passed in 2.55s with exit code 0.
3. **Port Hygiene & Production Deployment**:
   - `scripts/verify_port_hygiene.sh`: Audits TCP LISTEN on ports `3005`, `8000`, `8005`, `8080`.
   - `scripts/deploy_and_push.sh`: Runs port hygiene -> pytest gate -> frontend `npm run build` gate -> `git push origin main` -> polls live Railway health URL (`https://autonomousdaytrader-production.up.railway.app/health`) -> re-runs port hygiene.

---

## 2. Logic Chain

### 2.1 UI Architecture: Unified Obsidian Dark Operator Interface (R4)
1. **Observation**: Currently, `frontend/app/page.tsx` renders a single dashboard dedicated to intraday day trading (`StrategyCarousel`, `ActivePositionTray`, `TradeHistory`, `ExecutionLog`).
2. **Deduction**: Adding the 5-stock swing engine ("2-Day Panic Dip") requires a clean operator experience that avoids cognitive overload while keeping both trading arms easily inspectable and controllable.
3. **Design Solution**:
   - **Segmented Mode Toggle**:
     - Insert a top segmented pill control above or within the telemetry bar: `[ Intraday Day Trader ]` vs `[ Swing Mean-Reversion ]`.
     - Built with Framer Motion `layoutId="activeTradingTab"` for Apple-grade fluid sliding pill animation.
     - State can be toggled without page reload or stream disconnect.
   - **Swing Mode Dashboard Components**:
     a) **Swing Engine Telemetry Bar**:
        - Displays: Strategy: `2-Day Panic Dip (Connors RSI-2)`; Status: `ARMED` / `SCANNING AT 16:00 CLOSE`; Slots: `X of 2 Slots Used ($25,000 / $50,000 Notional)`; Overnight Policy: `OVERNIGHT EXEMPT (Multi-Day Hold)`.
     b) **Candidate Watchlist Status Card / Table**:
        - Displays all 5 certified stocks (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`) with real-time rule checks:
          - Rule 1: `200 SMA Floor` (Pass/Fail: `Close > 200 SMA`, with exact values e.g. `$111.10 > $102.50`)
          - Rule 2: `60d RS vs QQQ` (Pass/Fail: `ΔStock,60d >= ΔQQQ,60d`, e.g. `+18.2% >= +10.5%`)
          - Rule 3: `RSI(2) Panic Trigger` (Value display; glowing amber/red badge when `RSI(2) < 10.0`, e.g. `6.8 [PANIC DIP]`, else neutral `45.2`)
          - Rule 4: `48h Earnings Blackout` (Pass/Fail: e.g. `Clear (Next: 24d)` or `🚨 Earnings Tomorrow - BLACKOUT`)
          - Signal Status Badge: `QUALIFIED (STAGED FOR 09:30 OPEN)`, `ACTIVE HOLDING`, `WATCHING`, or `INELIGIBLE`.
     c) **Active Swing Positions Table**:
        - Renders open swing positions with:
          - Symbol, side (`LONG`), shares, entry price, market price, market value, unrealized PnL ($ and %).
          - Hard ATR Stop Line: `$X.XX` ($2.5 \times \text{Daily ATR(14)}$ below entry price) with distance % meter.
          - Holding Day Counter: Step indicator pill `[● ● ○ ○ ○] Day 2 of 5`.
          - Exit Trigger Watchlist:
            - `5-day SMA Cross`: ARMED if `Prior Close > 5-SMA`
            - `RSI(2) > 70.0`: ARMED if `RSI(2) > 70.0`
            - `Time Stop (5 Days)`: ARMED on Day 5
            - `Earnings Veto`: ARMED if earnings report tomorrow
     d) **Operator Manual Override Controls**:
        - `Stage Exit at 09:30 Open`: Schedules an orderly market open exit for the next session.
        - `Emergency Market Exit`: Immediate mid-session liquidation with 2-step confirmation modal.
        - `Tighten ATR Stop`: Allows operator to raise the emergency stop upward.
   - **Responsive Form Factors**:
     - **Desktop (1440x900)**: Clean widescreen layout with candidate grid and detailed tabular metrics.
     - **Mobile (390x844)**: Stacked cards with horizontal scroll for rule badges, touch targets $\ge 44\text{px}$, sticky bottom tray or drawer for active swing position, and zero horizontal page overflow.

### 2.2 WebSocket Contracts & State Synchronization
1. **Observation**: Backend broadcasts JSON via `broadcast_ui_state` in `backend/app/main.py`. The client hook `useTradingStream.ts` parses this and sets state.
2. **Deduction**: We must extend both the backend broadcast dictionary and frontend TypeScript definitions without breaking existing day-trading fields.
3. **Payload Extension**:
   ```typescript
   // In frontend/types/trading.ts
   export interface SwingCandidate {
     symbol: string;
     price: number;
     sma_200: number;
     sma_200_pass: boolean;
     rs_60d_stock: number;
     rs_60d_qqq: number;
     rs_pass: boolean;
     rsi_2: number;
     rsi_pass: boolean;
     earnings_date: string | null;
     earnings_blackout: boolean;
     status: "QUALIFIED" | "STAGED" | "ACTIVE" | "WATCHING" | "INELIGIBLE" | "BLOCKED";
     atr_14: number;
   }

   export interface SwingPosition {
     symbol: string;
     side: "LONG";
     shares: number;
     entry_price: number;
     market_price: number;
     market_value: number;
     unrealized_pnl: number;
     unrealized_pnl_pct: number;
     stop_loss: number; // 2.5x ATR below entry
     atr_14: number;
     atr_stop_distance: number;
     entry_date: string;
     holding_days: number;
     max_holding_days: number; // 5
     holding_progress: string; // e.g. "Day 2 of 5"
     sma_5: number;
     rsi_2: number;
     exit_triggers: {
       sma_5_cross: boolean;
       rsi_70_cross: boolean;
       time_stop_day_5: boolean;
       earnings_tomorrow: boolean;
     };
     staged_exit_at_open: boolean;
   }

   export interface SwingEngineState {
     status: "ACTIVE" | "SCANNING" | "STANDBY" | "IDLE";
     allocated_capital: number;
     slot_notional: number;
     max_slots: number;
     active_slots_used: number;
     available_slots: number;
     flattening_exempt: boolean;
     candidates: SwingCandidate[];
     positions: SwingPosition[];
     last_scan_time: string | null;
   }
   ```
4. **WebSocket Action Handlers**:
   - `SWING_EXIT_NEXT_OPEN`: `{"action": "SWING_EXIT_NEXT_OPEN", "symbol": sym}`
   - `SWING_EXIT_IMMEDIATE`: `{"action": "SWING_EXIT_IMMEDIATE", "symbol": sym}`
   - `SWING_TIGHTEN_STOP`: `{"action": "SWING_TIGHTEN_STOP", "symbol": sym, "new_stop": price}`

### 2.3 Deterministic Multi-Day Historical Replay Test Harness (R6)
1. **Observation**: `scripts/run_integrated_monday_dry_run.py` only replays a single day's morning open session (09:25–10:30 ET). Swing trading requires multi-day simulation spanning 16:00 ET qualification, 09:30 ET open execution, overnight holding across multiple sessions, and exit condition evaluation.
2. **Replay Engine Architecture**:
   - Create `tests/e2e/test_swing_multiday_replay.py` and `scripts/run_integrated_swing_dry_run.py`.
   - Uses `MockAlpacaRelayServer` and `FeedPlayer` to simulate consecutive trading days across the 5 certified stocks (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`) and benchmark `QQQ`.
   - **Chronological Flow to Replay**:
     - **Day 0 (16:00 ET Close Qualification)**:
       - Deliver finalized daily bars.
       - Verify: Rules 1, 2, 3, 4 evaluated.
       - Candidate qualifies -> buy order staged for $25,000 notional at 09:30 ET open.
     - **Day 1 (09:30 ET Market Open Execution)**:
       - Market open price delivered. Staged order fills for exactly $25,000 notional.
       - Emergency stop attached at fill price - $2.5 \times \text{ATR(14)}$.
       - Holding counter initialized to `Day 1 of 5`.
     - **Day 1 (15:45–15:58 ET Intraday Flattening)**:
       - Intraday positions (e.g. ORB) flattened to 0.
       - Swing position is verified **EXEMPT** and remains in `account.positions`.
       - Flattening audit passes.
     - **Day 2..5 (Exit Branches)**:
       - Branch A: **Emergency ATR Stop Breach**: Intraday low drops below stop -> immediate market exit.
       - Branch B: **5-SMA Cross Profit Exit**: Day 2 close > 5-SMA -> exit staged for Day 3 09:30 open -> fills at open.
       - Branch C: **RSI(2) > 70 Exit**: Day 2 close RSI(2) > 70.0 -> exit staged for Day 3 09:30 open -> fills at open.
       - Branch D: **5-Day Time Stop**: Held through Day 5 -> exit staged for Day 6 09:30 open -> fills at open.
       - Branch E: **Earnings Veto Exit**: Earnings announced for tomorrow -> exit staged for next 09:30 open.
     - **Verification Criteria**:
       - 0 unhandled exceptions.
       - Realized PnL recorded accurately in account ledger.
       - Slot freed back to 2 available upon exit.
       - UI state updates received and validated during the replay.

---

## 3. Caveats

1. **Static Export Constraint**: Because `frontend/next.config.mjs` specifies `output: "export"`, Next.js API routes (`app/api/*`) are not used; all backend APIs run exclusively on FastAPI (`backend/app/main.py`). The frontend components must fetch only from FastAPI or subscribe to `/ws/ui`.
2. **Shared Account Margin**: The virtual account has a shared $50,000 pool. Sizing is $25,000 per swing slot (max 2 slots). When swing positions are held, intraday buying power calculation in `backend/app/core/risk.py` and `account.py` must account for capital locked in swing positions to prevent double-spending or margin overdrafts.
3. **Mock Relay Data Coverage**: For multi-day replay testing, the mock server must serve historical daily bars for at least 200 sessions to calculate 200 SMA and 60d RS vs QQQ. Synthetic fixtures must contain 200 historical daily closes for `LRCX`, `KLAC`, `MU`, `AMD`, `GS`, and `QQQ`.

---

## 4. Conclusion & Concrete Recommendations

### 4.1 UI Component Structure for R4
Implement the following component hierarchy in `frontend/`:
1. `frontend/components/TradingModeToggle.tsx`:
   - Segmented toggle between `"intraday"` and `"swing"`.
   - Framer Motion sliding pill backdrop with `layoutId="activeMode"`.
2. `frontend/components/swing/SwingTelemetryBar.tsx`:
   - 4-tile telemetry bar: Strategy Status, Slot Allocation ($25k / slot), Overnight Policy (Overnight Exempt), Universe (5 certified stocks).
3. `frontend/components/swing/SwingCandidateWatchlist.tsx`:
   - Table / card grid displaying all 5 stocks with visual pass/fail pills for:
     - 200 SMA Floor
     - 60d RS vs QQQ
     - RSI(2) Panic Dip (< 10)
     - 48h Earnings Blackout
     - Current Status (`QUALIFIED`, `STAGED`, `ACTIVE`, `WATCHING`, `INELIGIBLE`, `BLOCKED`)
4. `frontend/components/swing/ActiveSwingPositionsTable.tsx`:
   - Table of active swing trades showing:
     - Ticker, shares, entry price, market price, market value, unrealized PnL ($ and %).
     - 2.5x ATR stop line ($ value, distance %, alert threshold).
     - Holding day counter (`Day X of 5` progress bar).
     - Exit trigger indicators (5-SMA cross, RSI(2)>70, 5-day time stop, earnings exit).
5. `frontend/components/swing/SwingManualControls.tsx`:
   - Operator override buttons:
     - `Stage Exit at 09:30 Open`
     - `Emergency Market Exit` (with 2-step confirmation)
     - `Tighten ATR Stop`
6. `frontend/app/page.tsx`:
   - Conditionally or seamlessly render Intraday components (`StrategyCarousel`, `ActivePositionTray`) or Swing components based on selected mode toggle.

### 4.2 Backend & WebSocket Extensions
1. In `backend/app/main.py`:
   - Extend `broadcast_ui_state` to include `"swing": swing_engine.to_ui_dict()`.
   - Extend `ui_websocket_endpoint` to handle `SWING_EXIT_NEXT_OPEN`, `SWING_EXIT_IMMEDIATE`, and `SWING_TIGHTEN_STOP`.
   - Add REST fallback endpoint `POST /api/swing/exit`.
2. In `backend/app/core/flattening.py`:
   - Ensure `liquidate_all_positions` and `execute_phase_4_audit` inspect only intraday positions (`not pos.is_swing`).
   - Swing positions remain active in `account.positions` overnight.

### 4.3 Multi-Day Replay Test Harness for R6
1. Create `tests/e2e/test_swing_multiday_replay.py`:
   - Exercises all 7 quantitative rules and all 5 exit conditions deterministically.
   - Asserts flattening exemption during 15:45–15:58 ET.
2. Create `scripts/run_integrated_swing_dry_run.py`:
   - Executes multi-day replay through production FastAPI wiring and generates `SWING_SIMULATION_REPORT.md`.
3. Add swing test execution to `tests/e2e/runner.py` and `scripts/run_e2e_tests.sh`.

---

## 5. Verification Method

### 5.1 Commands to Verify UI & E2E Replay
1. **Frontend Architecture & Build Gate**:
   ```bash
   npm --prefix frontend test
   npm --prefix frontend run build
   ```
   *Expected outcome*: `verify_ui.mjs` and `test_websocket_resilience.mjs` pass 100%. `next build` compiles with 0 TypeScript/lint errors and exports static files to `frontend/out`.

2. **E2E Test Suite Gate**:
   ```bash
   python3 tests/e2e/runner.py
   ```
   *Expected outcome*: All tests pass with exit code 0. Post-flight port audit verifies ports 8080, 8005, 8000, 3005 are clean and liberated.

3. **Integrated Monday Dry Run**:
   ```bash
   python3 scripts/run_integrated_monday_dry_run.py
   ```
   *Expected outcome*: Passes in ~2.5s with status `PASS`, 0 event bus errors, and all positions flat.

4. **Multi-Day Swing Replay Test (New)**:
   ```bash
   python3 -m pytest tests/e2e/test_swing_multiday_replay.py -v
   python3 scripts/run_integrated_swing_dry_run.py
   ```
   *Expected outcome*: All swing entry, ATR stop, 5-SMA, RSI>70, 5-day time stop, earnings exit, and flattening exemption tests pass 100%.

5. **Port Hygiene Audit**:
   ```bash
   ./scripts/verify_port_hygiene.sh
   ```
   *Expected outcome*: All project ports clean and liberated. Zero lingering background processes.

### 5.2 Invalidation Conditions
- Any occurrence of `next build` failure, TypeScript errors, or broken imports in `frontend/`.
- Failure of swing positions to survive 15:55–15:58 ET intraday auto-flattening.
- WebSocket UI stream crashing or dropping connection during high-frequency swing candidate updates.
- Any background process or daemon remaining on ports 3005, 8000, 8005, or 8080 after tests complete.
