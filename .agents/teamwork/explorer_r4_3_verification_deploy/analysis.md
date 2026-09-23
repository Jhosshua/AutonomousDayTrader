# Comprehensive Exploration Analysis: Requirements R4, R5, and R6
**Explorer**: Explorer 3 (Verification & Deploy Explorer)  
**Date**: 2026-09-23  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_3_verification_deploy`  
**Target Project**: `AutonomousDayTrader` (`/Users/mo/AutonomousDayTrader`)  

---

## Executive Summary

This investigation surveys the testing infrastructure, end-to-end simulation pipelines, process hygiene mechanisms, frontend mobile UI architecture, and Railway cloud deployment setup for AutonomousDayTrader. The goal is to prepare for:
1. **R4 (Adversarial Audit Prep)**: Anti-hallucination, zero synthetic fixture delusions, and mutation testing for risk limits and indicator lookbacks.
2. **R5 (End-to-End Dry Run & Process Hygiene)**: Expanding simulation coverage to the 12-symbol universe, all 4 strategies, and market regime transitions, while ensuring strict port liberation (8000, 8005, 8080, 3005).
3. **R6 (Mobile UI Visual Audit, Docs & Railway Deploy)**: Next.js Apple Music mobile dashboard, spring physics, WebSocket latency indicators, and single-service Railway container deployment.

---

## 1. Backend Test Suites & Adversarial Audit Prep (R4)

### 1.1 Existing Test Organization & Coverage
The project maintains a dual test hierarchy:
1. `backend/tests/` (272 tests across 20 test files, 100% pass rate in 4.30s):
   - `backend/tests/unit/` (17 files):
     - `test_risk.py` (225 lines, 10 test functions): Validates InstitutionalRiskEngine ($1,500 circuit breaker, $25,000 position cap, 0.4%–4.0% stop guardrails, 3-position concurrency, warning levels).
     - `test_strategies.py` (686 lines, 25 test functions): Validates technical indicators (VWAP + bands, ATR-14, EMA 9/21, SMA, Z-score, RSI, RVOL), ORB (CLV, bar range cap, extension cap), VWAP Pullback (standard deviation targets), News Momentum (sentiment NLP, volume surge, contradiction liquidation), and Mean Reversion.
     - `test_market_filter.py` (238 lines, 7 test functions): Anchored VWAP, EMA 9/21, session boundary reset, early open convergence, consensus bullish/bearish detection, 120s staleness fail-closed guard, and index beta alignment matrix.
     - `test_account.py`, `test_bracket.py`, `test_engine.py`, `test_flattening.py`, `test_health_feed_liveness.py`, `test_persistence.py`, `test_trailing_atr.py`, `test_vix_staleness_guard.py`.
     - `test_empirical_stress_m1.py`, `test_empirical_stress_m2.py`, `test_empirical_stress_m2_2.py`: In-depth empirical stress testing.
   - `backend/tests/stress/` (4 files):
     - `test_challenger_r3_remediation.py` (633 lines): Mutation testing verification suite.
     - `test_challenger_r3_2_api_ui_stress.py` (550 lines): UI WebSocket 500 Hz burst stress, stalled client eviction, `/api/orders` validation, Phase 4 flattening continuous retry, and port hygiene verification.
2. `tests/e2e/` (320 tests across 11 files, 100% pass rate in 26.62s):
   - `runner.py`: Multi-tier runner executing Tiers 1-5, contracts, bracket checks, and mobile UI visual tests.
   - `test_tier1_features.py` through `test_tier5_adversarial.py`.
   - `test_challenger_mobile.py`: Playwright mobile visual layout tests.
   - `test_ui_stream_resilience.py`: WebSocket `/ws/ui` in-process stress tests.

### 1.2 Mock Data vs. Real Mechanics ("Zero Synthetic Fixture Delusions")
The codebase establishes a strict boundary between integration plumbing fixtures and quantitative edge verification:
- **Plumbing Replay Fixtures**: Files in `tests/e2e/fixtures/` (`monday_open_session.json`, `bars_fixtures.json`, `news_fixtures.json`, `quotes_fixtures.json`, `trades_fixtures.json`, `vix_fixtures.json`) are hand-crafted deterministic scenarios designed solely to test event ingestion, serialization, state machine transitions, order routing, and UI broadcasts.
- **Empirical Ground Truth**: In accordance with `MEMORY.md` and `ERRORS.md`, replay fixtures are explicitly documented as having **zero predictive or quantitative validity**. Real trading performance is diagnosed solely on live paper ledger history (e.g. the 7 live paper trades analyzed in `MEMORY.md`).
- **Mathematical Invariant Verification**: Strategy and risk mechanics are verified against exact mathematical invariants rather than cherry-picked fixture runs. For instance:
  - Circuit breaker trips at exactly $1,500.00 and stays armed at $1,499.99 (`test_empirical_stress_m1.py:55-80`).
  - Causal time arrow enforcement: strict negative-elapsed checks (`0 <= elapsed <= TTL`) reject future data timestamps without floating-point leaks (`test_market_filter.py:219-232`, `test_challenger_r3_remediation.py:514-544`).

### 1.3 Proposed Mutation Test Targets for R4
Existing mutation tests in `backend/tests/stress/test_challenger_r3_remediation.py:476-633` demonstrate how mutants are introduced and verified to be "killed" by production logic. For Milestone 4, the following mutation targets must be implemented:
1. **Risk Sector Concentration Cap (R1)**:
   - *Target*: `InstitutionalRiskEngine.evaluate_order_request` (`backend/app/core/risk.py:187-199`).
   - *Mutant 1*: Allows > 2 positions in a sector (e.g. 3 Semiconductor positions: NVDA, AMD, +1).
   - *Mutant 2*: Fails to permit a 2nd position in a sector when `MAX_POSITIONS_PER_SECTOR = 2` is configured.
   - *Verification*: Mutant must be killed by deterministic assertion on `rejection_code == "CORRELATED_SECTOR_EXPOSURE"`.
2. **Watchlist Universe Expansion & Sector Registration (R1)**:
   - *Target*: `symbol_sectors` dictionary in `backend/app/core/risk.py:64-75`.
   - *Mutant*: Omission or misclassification of new watchlist symbols (`AMD`, `PLTR`, `COIN`, `MSFT`, `AMZN`, `META`, `GOOGL`).
   - *Verification*: Test asserts all 12 `WATCHLIST_SYMBOLS` map to valid, non-empty sector names.
3. **News Momentum Calibration (R3)**:
   - *Target*: `NewsMomentumStrategy.volume_surge_multiplier` (`backend/app/strategies/news_momentum.py:88`).
   - *Mutant*: Sits at legacy 3.50x instead of calibrated 2.00x, rejecting valid 2.5x volume breakouts.
   - *Verification*: Test supplies a bar with 2.2x volume surge; production fires `SignalEvent`, mutant returns `[]`.
4. **Statistical Mean Reversion Calibration (R3)**:
   - *Target*: `MeanReversionStrategy` thresholds (`backend/app/strategies/mean_reversion.py:63-68`).
   - *Mutant 1*: Z-score threshold at legacy 2.00 instead of calibrated 1.65.
   - *Mutant 2*: Volume climax multiplier at legacy 1.75x instead of calibrated 1.30x.
   - *Mutant 3*: Min wick ratio at legacy 0.35 instead of calibrated 0.30.
   - *Verification*: Test supplies an exhaustion bar with $Z = 1.70$, volume ratio 1.40x, wick ratio 0.32; production triggers fade, mutant returns `[]`.
5. **Market Filter NEUTRAL Idiosyncratic Breakout Policy (R2)**:
   - *Target*: `MarketTrendFilter.is_signal_permitted` (`backend/app/core/market_filter.py:284-292`).
   - *Mutant 1*: Blanket rejection of ORB in `NEUTRAL` even when single-stock RVOL >= 2.20x.
   - *Mutant 2*: Permitting low RVOL (< 2.20x) breakouts in `NEUTRAL` regime.
   - *Verification*: Test asserts `RVOL >= 2.20` is approved in `NEUTRAL`, while `RVOL = 1.50` is rejected with `INDEX_FILTER_DENIED`.
6. **Lookahead / Causal Timestamp Leakage**:
   - *Target*: `MarketTrendFilter.get_current_trend` (`backend/app/core/market_filter.py:192-202`).
   - *Mutant*: Uses absolute value `abs(elapsed)` instead of signed check `if elapsed < 0: return UNKNOWN`.

---

## 2. End-to-End Simulation Runners (R5)

### 2.1 Inventory of Simulation Scripts
The codebase has two distinct simulation paradigms:
1. `scripts/run_integrated_monday_dry_run.py` (215 lines):
   - **Architecture**: Production-wired integration replay.
   - **Wiring**: Instantiates `MockAlpacaRelayServer(port=8080)`, overrides `settings.RELAY_URL` and `settings.START_RELAY_CLIENTS = True`, initializes `backend.app.main:app` with `async with runtime.lifespan(runtime.app)`.
   - **Replay Mechanism**: Streams events through `FeedPlayer` into `MockAlpacaRelayServer`, where downstream WebSocket clients (`StockWebSocketClient`, `NewsWebSocketClient`, `VixClient`) ingest them through `EventBus` into strategies, `ExecutionEngine`, `DynamicBracketManager`, and `PaperTradingAccount`.
   - **UI Telemetry**: Attaches a `CaptureSocket` to `runtime.ui_clients` to capture serialized UI `STATE_UPDATE` broadcasts.
   - **Certification Assertions**:
     - `processed == len(fixture)`
     - `event_errors == 0`
     - `len(runtime.engine.orders) > 0` and at least one order `FILLED`
     - Zero orders `REJECTED`
     - `not runtime.account.positions` (100% flat at close)
     - `not runtime.engine.working_orders` (zero pending orders)
     - All relay statuses connected (`stock`, `news`, `vix`).
   - **Output**: Generates `MONDAY_SIMULATION_REPORT.md`.
2. `scripts/run_monday_dry_run.py` (777 lines):
   - Standalone simulation harness with a manual telemetry collector (`MondaySimulationAuditor`) testing 6 phases (A–F).
3. `tests/e2e/runner.py` (157 lines):
   - CLI test runner orchestrating pytest E2E tiers (Tier 1 to Tier 5).

### 2.2 Forensic Analysis: Why Mean Reversion Had 0 Trades in Previous Dry Runs
Inspection of `MONDAY_SIMULATION_REPORT.md` (lines 194-200) revealed:
```json
{
  "id": "mean_reversion",
  "name": "Statistical Mean Reversion / Exhaustion Fades",
  "status": "ACTIVE",
  "daily_pnl": 0.0,
  "win_rate": 0.0,
  "trades_count": 0
}
```
Forensic analysis of `tests/e2e/fixtures/monday_open_session.json` discovered three direct root causes:
1. **Universe Omission**: The fixture contains 184 events covering only 5 symbols: `AAPL` (21 bars), `NVDA` (9 bars), `TSLA` (7 bars), `SPY` (61 bars), `QQQ` (61 bars). None of the other 7 watchlist symbols are present.
2. **Buffer Starvation**: `MeanReversionStrategy` requires 20 bars of history (`self.period = 20`) AND enforces a strict time lockout during `OPEN_VOLATILITY_FLUSH` (09:30–10:00 ET, `backend/app/strategies/mean_reversion.py:117-124`).
3. **Threshold Starvation**: Even on `AAPL` (which reached 21 bars), price did not reach $|Z| \ge 2.00$ with $1.75\times$ volume climax and $0.35$ wick ratio between 10:00 and 10:30 ET.

### 2.3 Blueprint: Comprehensive 12-Symbol, 4-Strategy, Multi-Regime Dry Run
To certify Requirement R5, the simulation session fixture and runner must be structured as follows:
1. **12-Symbol Watchlist Ingestion**:
   - Include bars, quotes, and trades for all 12 symbols:
     - Broad Market Beta: `SPY`, `QQQ`
     - Tech / Semis: `AAPL`, `NVDA`, `AMD`
     - Software / Cloud: `MSFT`, `PLTR`
     - Consumer / Discretionary: `TSLA`, `AMZN`
     - Communication: `META`, `GOOGL`
     - Fintech / Crypto: `COIN`
2. **Multi-Regime Transition Design**:
   - **Phase 1: Open Volatility & Bullish Trend (09:30–09:50 ET)**:
     - `SPY` and `QQQ` establish upward trending bars above anchored VWAP (`BULLISH` regime).
     - `NVDA` breaks 5-minute opening range high on $2.5\times$ RVOL $\to$ `orb` strategy triggers LONG entry and attaches 2-tier bracket.
     - `AAPL` pulls back to VWAP and bounces with EMA trend alignment $\to$ `vwap_pullback` triggers LONG entry.
   - **Phase 2: Breaking News Catalyst (09:45–09:55 ET)**:
     - Breaking Benzinga news headline on `TSLA` (e.g. "Tesla surpasses delivery targets with record automotive margins", sentiment score $+0.82$).
     - Next 1-minute bar confirms with $2.4\times$ volume surge $\to$ `news_momentum` triggers LONG entry.
   - **Phase 3: Range-Bound Chop & Regime Transition (09:55–10:20 ET)**:
     - `SPY` and `QQQ` cross back below VWAP and oscillate within $\pm 0.03\%$ noise band $\to$ `MarketTrendFilter` transitions to `NEUTRAL`.
     - `mean_reversion` strategy (now calibrated to $|Z| \ge 1.65$, volume climax $1.30\times$, wick rejection $0.30$) identifies an exhaustion spike on `PLTR` or `COIN` ($Z = +1.82$, volume $1.45\times$, upper wick $0.38$) $\to$ triggers SHORT exhaustion fade targeting 20-SMA.
     - Single-stock idiosyncratic volume surge on `AMD` ($RVOL = 2.45\times \ge 2.20\times$) $\to$ `MarketTrendFilter` permits idiosyncratic breakout in `NEUTRAL`.
   - **Phase 4: Target Realization & EOD Auto-Flattening (10:20–10:30 ET / 15:55 ET)**:
     - Brackets scale out partial profits at Target 1 ($0.80R$), ratcheting stop to breakeven, or close at Target 2 ($1.80R$).
     - Automated flattening routine liquidates all remaining positions before close.
3. **Certification Gate in `scripts/run_integrated_monday_dry_run.py`**:
   - Update runner assertions to verify:
     ```python
     all_4_traded = all(s["trades_count"] > 0 for s in report["strategies"])
     assert all_4_traded, "All 4 strategies must execute at least one trade"
     ```

---

## 3. Process Hygiene & Port Audit (R5)

### 3.1 Port Architecture & Usage
The system design designates specific ports:
- **Port 8000**: Legacy FastAPI / local dev port (must be clean).
- **Port 8005**: Production FastAPI application port (`EXPOSE 8005` in `Dockerfile`, public container port on Railway). Serves `/health`, `/api`, `/ws/ui`, and static Next.js export in `frontend/out`.
- **Port 8080**: AlpacaRelay mock server port (`MockAlpacaRelayServer` in `backend/app/replay/mock_relay.py`).
- **Port 3005**: Next.js standalone dev server port (`frontend/package.json` dev/start scripts configured with `-p 3005` to prevent collision with default Next.js 3000).

### 3.2 Audit Scripts & Mechanics
1. `scripts/verify_port_hygiene.sh` (32 lines):
   - Defined ports: `PORTS=(3005 8000 8005 8080)`.
   - Mechanism: Runs `lsof -tiTCP:$port -sTCP:LISTEN` for each port. If occupied, identifies PID and command name (`ps -p $pid -o command=`). Exits with code 1 if any port is bound; exits with code 0 if all clean.
   - Safe behavior: Read-only, does not issue destructive kill commands on unknown processes.
   - **Current Live Status**: Executed `bash scripts/verify_port_hygiene.sh` $\to$ **All 4 ports clean and liberated**.
2. **Defect Identified in `tests/e2e/runner.py`**:
   - Line 108: `ports_to_check = [8080, 8005, 3005]`.
   - **Finding**: Port 8000 is omitted from `audit_ports()` in `runner.py`. To align with `scripts/verify_port_hygiene.sh` and Requirement R5, port 8000 must be added to `ports_to_check`.

---

## 4. Frontend Mobile UI Visual Audit (R6)

### 4.1 Component Inventory & Structure
All frontend components are located in `frontend/components/` and `frontend/app/` (Next.js 15 App Router architecture without an intermediate `src/` directory):
- `frontend/components/Header.tsx` (210 lines): Dynamic Island top bar with live WebSocket indicator, VIX regime pill, STK WS/NEWS WS/VIX REST status dots, portfolio equity hero display, daily PnL badge, and circuit breaker alert banner.
- `frontend/components/StrategyCarousel.tsx` (152 lines): Horizontal snap carousel with `StrategyCard` components for all 4 strategies, with interactive modal inspector sheet.
- `frontend/components/ActivePositionTray.tsx` (292 lines): "Now Playing"-inspired bottom floating tray. Persistent docked island showing active trade, ticker badge, side, shares, entry price, market price, unrealized PnL, lock-stop shield button, and emergency flatten button. Supports swipe-down drag dismissal (`onDragEnd` with `PanInfo`) and expandable modal sheet.
- `frontend/components/LiveChart.tsx` (243 lines): SVG line chart showing price ticks, entry level, stop loss, and take profit targets.
- `frontend/components/ManualControls.tsx` (230 lines): Manual emergency controls with confirmation modals.
- `frontend/components/ExecutionLog.tsx` (95 lines): Tabular audit log.
- `frontend/components/TradeHistory.tsx` (340 lines): Closed trade ledger display.
- `frontend/components/AmbientBackground.tsx` (84 lines): Dynamic SVG blur glow shifting between calm obsidian, profit emerald, and circuit breaker crimson.
- `frontend/hooks/useTradingStream.ts` (395 lines): WebSocket client connecting to `/ws/ui` on port 8005 (or dynamic host), automatic reconnection backoff, REST fallback polling (`/api/account`, `/api/positions`).

### 4.2 Build Cleanliness Verification
- Executed `npm --prefix frontend run build`:
  - Compiled successfully in 1038ms.
  - Linting and TypeScript validation: **0 errors**.
  - Static export generated in `frontend/out` (2 HTML pages, 4 static pages).
- Executed `node frontend/scripts/verify_ui.mjs`:
  - All 18 component files verified.
  - Tailwind tokens (`#000000`, `#0a0a0c`, `#30d158`, `#ff453a`) verified.
  - Glassmorphism blur (`backdrop-filter: blur(24px)`) verified.
  - Spring physics (`stiffness: 350, damping: 32`) verified.
  - All 4 strategy models verified.
  - Safe port 3005 verified.

### 4.3 Mobile Viewport (390x844) Audit
- Layout is constrained to `max-w-4xl mx-auto` with `overflow-x-hidden` on `<main>`, preventing horizontal scrolling.
- Bottom padding `pb-32` prevents the floating `ActivePositionTray` from obscuring page content.
- Tested across Playwright mobile viewports in `tests/e2e/test_challenger_mobile.py`:
  - iPhone 14 Pro (390x844)
  - iPhone SE (375x667)
  - iPhone 11 Plus (414x896)
  - Android Compact (360x800)
  - Ultra-Narrow Stress (320x568)
- Verified `scrollWidth <= innerWidth` across all breakpoints.

---

## 5. Railway Deployment & Project Documentation (R6)

### 5.1 Railway Container Architecture
- `railway.json`:
  ```json
  {
    "$schema": "https://railway.app/railway.schema.json",
    "build": {
      "builder": "DOCKERFILE",
      "dockerfilePath": "Dockerfile"
    },
    "deploy": {
      "healthcheckPath": "/health",
      "healthcheckTimeout": 120,
      "restartPolicyType": "ON_FAILURE",
      "restartPolicyMaxRetries": 10
    }
  }
  ```
- `Dockerfile` (26 lines):
  - Stage 1: `node:20-alpine AS ui-build` builds frontend and outputs static bundle to `frontend/out`.
  - Stage 2: `python:3.12-slim` installs dependencies from `backend/requirements.txt`, copies backend, tests, scripts, and `frontend/out`.
  - Command: `CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8005}"]`.
  - Note: Single-service architecture where FastAPI hosts API routes, WebSocket `/ws/ui`, and mounts `frontend/out` at `/`. No separate Node.js server is needed in production.

### 5.2 Git Configuration & Deployment Pipeline
- Git remote: `origin https://github.com/Jhosshua/AutonomousDayTrader.git` on branch `main`.
- Deployment script: `scripts/deploy_and_push.sh` enforces the full release sequence:
  1. Port hygiene check (`scripts/verify_port_hygiene.sh`)
  2. Backend test gate (`pytest -q`)
  3. Frontend build gate (`npm run build`)
  4. Git commit and push to `origin main`
  5. Live production health polling against `https://autonomousdaytrader-production.up.railway.app/health` for up to 180s
  6. Final post-deployment port hygiene audit.

### 5.3 Live Remote Health Verification
Queried live production endpoint `https://autonomousdaytrader-production.up.railway.app/health`:
- **HTTP Status**: 200 OK
- **Response**:
  ```json
  {
    "status": "healthy",
    "mode": "production",
    "upstream_configured": true,
    "account": {
      "equity": 49798.32,
      "cash": 49798.32,
      "buying_power": 199193.28,
      "status": "ACTIVE",
      "open_positions": 0
    },
    "risk": {
      "status": "ARMED",
      "level": "NORMAL",
      "drawdown_dollars": 201.68,
      "drawdown_pct": 0.004
    },
    "persistence": {
      "status": "durable",
      "required": true,
      "schema_version": 2,
      "checkpoint_revision": 11052
    },
    "limits": {
      "max_daily_loss_dollars": 1500.0,
      "max_position_notional": 24899.16,
      "max_concurrent_positions": 3,
      "base_trade_risk_pct": 0.01,
      "stop_distance_pct": [0.004, 0.04]
    },
    "feeds": {
      "bars": { "received": 980 },
      "quotes": { "received": 3510671 },
      "trades": { "received": 1418664 },
      "news": { "received": 171 },
      "vix": { "stale": false }
    }
  }
  ```
- Confirms production bot is healthy, durable persistence is functioning, and all upstream feeds are actively ticking.

### 5.4 Documentation Notes
- `PROJECT.md` (33KB): Contains architectural blueprints, component contracts, and operational requirements.
- `MEMORY.md` (38KB): Detailed historical log of all engineering decisions, root cause analyses, and test results.
- `ERRORS.md` (19.6KB): Catalogs failure modes, fixes, and lessons learned.
- When implementation completes, all three files must be updated with the R4/R5/R6 audit results, universe expansion details, and deployment hash.

---

## 6. Actionable Implementation Recommendations

1. **For Worker Remediation (R1, R2, R3)**:
   - Expand `WATCHLIST_SYMBOLS` in `backend/app/config.py:59` to all 12 names.
   - Update `self.symbol_sectors` in `backend/app/core/risk.py:64-74` to map all 12 symbols to their specific sector classifications (Semiconductors, Software, Discretionary, Communication Services, Fintech/Crypto).
   - Adjust sector limit in `risk.py:187-199` to allow up to 2 concurrent positions per sector (while keeping max 3 total concurrent positions).
   - Calibrate `NewsMomentumStrategy.volume_surge_multiplier` to 2.0x in `backend/app/strategies/news_momentum.py:88`.
   - Calibrate `MeanReversionStrategy` parameters in `backend/app/strategies/mean_reversion.py:63-68` ($Z = 1.65$, volume climax $1.30\times$, wick ratio $0.30$).
   - Update `MarketTrendFilter.is_signal_permitted` in `backend/app/core/market_filter.py:284-292` to allow high-RVOL breakouts ($RVOL \ge 2.20\times$) in `NEUTRAL` regimes.
2. **For E2E Simulation & Verification (R5)**:
   - Create/update `tests/e2e/fixtures/monday_open_session.json` to include 12 symbols, regime transitions, and signals for all 4 strategies (ensuring `mean_reversion` receives at least 20 bars and an exhaustion setup in `NEUTRAL`).
   - Add port 8000 to `ports_to_check` in `tests/e2e/runner.py:108`.
3. **For Adversarial Challenger / Auditor (R4)**:
   - Implement the 6 identified mutation test targets in `backend/tests/stress/`.
4. **For Release Worker (R6)**:
   - Run `npm --prefix frontend run build` to verify clean production export.
   - Run `deploy_and_push.sh` to push to `origin main`, verify Railway build, and check live `/health`.
