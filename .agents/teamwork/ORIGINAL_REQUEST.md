# Original User Request

## 2026-09-19T23:38:55Z

A fully local, always-on US stock market day trading system connected to AlpacaRelay, operating on a virtual $50,000 paper trading account across 4 dynamically adapted, high Sharpe-ratio day trading strategies, featuring an Apple Music mobile-inspired interface with fluid animations, multi-stage unbiased QA auditing, and a pre-market Monday dry run.

Working directory: /Users/mo/AutonomousDayTrader
Integrity mode: development

## Requirements

### R1. Deterministic Day Trading Engine & AlpacaRelay Signal Ingestion
- Connect downstream to AlpacaRelay endpoints (stock WebSocket for 1-minute bars, quotes, and trades; real-time news WebSocket; and `GET /vix` dxFeed print), respecting authentication (`RELAY_TOKEN`) and protocol standards.
- Manage a self-contained $50,000 virtual paper trading account tracking cash, equity, buying power, open positions, unrealized/realized PnL, and full execution order lifecycle.
- Enforce institutional risk guardrails: hard maximum daily loss limit (circuit breaker), per-position risk limits, dynamic stop-loss/take-profit brackets, and zero overnight holds (strictly day trading with automated end-of-day market-on-close flattening).

### R2. 4 Dynamically Adapted Intraday Trading Strategies
- Formulate, back-evaluate, and implement the 4 highest Sharpe-ratio day trading strategies (e.g., Opening Range Breakout, VWAP Trend Pullback & Continuation, Catalyst News Momentum Breakout, and Statistical Mean Reversion / Exhaustion Fades).
- Maintain dynamic self-adaptation across all 4 strategies based on live signals:
  - Market Regime & Volatility: Adapt position sizing, entry thresholds, and stop widths dynamically to real-time VIX prints from `/vix`.
  - News Catalysts: Ingest real-time headlines and sentiment from AlpacaRelay news stream to trigger momentum entries or pause contradictory trades.
  - Time-of-Day Dynamics: Modulate execution rules across market phases (Pre-market scan, 9:30–10:00 Open volatility flush, 10:00–11:30 Trend continuation, 11:30–14:00 Midday chop defense, 15:00–16:00 Power hour & flattening).

### R3. Apple Music Mobile-Inspired UI & Animations
- Build a responsive mobile-first UI using Next.js / React, Tailwind CSS, and Framer Motion mimicking the Apple Music mobile design system:
  - Deep obsidian dark theme with vibrant dynamic glassmorphism and background gradient blurs tinted by portfolio momentum.
  - Strategy "Playlists / Albums": Cards presenting each of the 4 strategies with live performance stats, win rate, and active status badges.
  - Collapsible / expandable bottom "Now Playing" tray displaying the currently active primary trade, live ticker chart, entry/stop levels, and quick manual intervention controls.
  - Smooth spring physics, haptic-style transitions, and real-time WebSocket state updates without page reloads.

### R4. Unbiased Multi-Stage Code Review, QA & Monday Live Dry Run
- Multi-agent independent verification review at each build milestone (Engine, Strategies, UI, Integration) checking for deterministic order routing, race conditions, and error recovery.
- Automated end-to-end integration test suite exercising the complete signal-to-order-to-fill pipeline using synthetic and replayed AlpacaRelay market data feeds.
- Full visual audit of the mobile UI components and responsive layout.
- Comprehensive live-speed simulation dry run executing a mock Monday market open session to certify that all systems and data feeds are fully operational for real Monday trading.

### R5. Repository Delivery & Process Hygiene
- Initialize a clean Git repository in the working directory, commit all project source code, documentation, and test suites with descriptive commit history, and push to GitHub (`git push origin main`).
- Enforce strict process hygiene: ensure all test processes, background mocks, and local test servers are cleanly terminated, leaving no lingering daemons or blocked ports.

## Acceptance Criteria

### Execution & Signals
- [ ] Connects successfully to AlpacaRelay stock WebSocket, news WebSocket, and REST endpoints using downstream protocol conventions.
- [ ] Paper account tracks $50,000 initial balance accurately with real-time mark-to-market PnL and trade history logging.
- [ ] Daily risk limit circuit breaker triggers and halts new orders if drawdown threshold is breached.
- [ ] All open positions are automatically flattened prior to 16:00 ET market close.

### Strategy Performance & Adaptation
- [ ] 4 distinct day trading strategies are implemented with clear algorithmic entry, exit, and stop rules.
- [ ] Strategy parameters (position size, entry sensitivity, profit target) demonstrably adjust when simulated VIX changes and when news catalyst events arrive.
- [ ] Trading engine executes only valid trades according to active time-of-day regime filters.

### User Interface & Experience
- [ ] Mobile-optimized Apple Music aesthetic renders correctly with glassmorphism, dark background, and animated glowing accents.
- [ ] Expandable bottom "Now Playing" drawer opens smoothly to display active position details and closes without layout thrashing.
- [ ] Live data updates stream to UI via WebSocket without requiring manual page refresh.
- [ ] Next.js / React production build succeeds with zero TypeScript errors or broken imports.

### Verification & Delivery
- [ ] Automated pytest suite passes 100% of tests covering order execution, risk stops, signal parsing, and strategy state transitions.
- [ ] Visual QA audit verifies responsive layout, contrast, and animation smoothness across mobile viewports.
- [ ] Simulated Monday market open dry run completes end-to-end without unhandled exceptions or stalled event loops.
- [ ] Project is committed and pushed to GitHub upstream main branch, with all local test processes and ports cleanly freed.

## 2026-09-20T13:14:36Z

Use a full multi-agent team (parallel auditors, independent diff reviewers, QA, dry-run simulation, visual UI tester, and release engineer).
Perform a full architectural and codebase audit of AutonomousDayTrader to identify and fix all broken connections and bugs, purge all music/playlist metaphors in favor of trading terminology ("Trading Strategies" and "Active Position"), conduct an independent multi-agent diff review and full QA cycle, run a deterministic market open dry run simulating live trading, conduct a mobile and desktop visual UI audit, update project documentation notes, and push to GitHub origin main verifying Railway auto-build and deployment.

Working directory: /Users/mo/AutonomousDayTrader
Integrity mode: development

Reference material:
- Specification & Architecture: `PROJECT.md`
- Original Requirements: `ORIGINAL_REQUEST.md`
- Engineering Decisions & Log: `MEMORY.md`
- Test Infrastructure: `TEST_INFRA.md`

## Requirements

### R1. Comprehensive Architectural Audit & Bug Remediation
Conduct a rigorous audit across the entire codebase (backend core, ingestion adapters, order book, risk engine, state machines, the 4 trading strategies, and WebSocket streaming). Identify any missed connections, unhandled states, inverted risk boundaries, orphaned bracket orders, or dead code paths. Fix all identified defects while preserving intended specifications in `PROJECT.md`.

### R2. De-themification of Music & Playlist Terminology
Completely remove all music, playlist, and album metaphors across the codebase, frontend components, state models, docs, and test suites. Replace them with professional trading terminology:
- Replace "Curated Playlists" / "Playlists / Albums" with "Trading Strategies".
- Replace "Now Playing" bottom tray with "Active Position" (or "Live Execution").
- Clean up any remaining music-inspired labels (e.g., "album art", "track", "playlist") in comments, tests, and component strings.

### R3. Multi-Agent Adversarial Diff Review & Full QA Cycle
Dispatch subagents to perform an adversarial review of all code diffs to ensure no unintended behavior, contract breakage, or regressions were introduced. Execute the complete backend test suite and the full end-to-end testing suite (`pytest` and `scripts/run_e2e_tests.sh`) to achieve a 100% pass rate.

### R4. Deterministic Market Open Dry-Run Simulation
Execute a complete deterministic Monday market open simulation dry run through the actual production ingestion and execution paths as if trading live. Verify signal ingestion, bracket management, PnL tracking, risk circuit breakers, flattening routines, and UI WebSocket serialization with clean logs and zero unhandled exceptions.

### R5. Mobile & Desktop Visual UI Audit
Perform a visual UI inspection across both mobile (390x844) and desktop (1440x900) viewports. Verify that all components, strategy cards, active position trays, charts, risk badges, and manual intervention controls render cleanly without truncation, overlapping, or horizontal overflow.

### R6. Git Push, Railway CI/CD Deployment Verification & Process Hygiene
Document all audit findings, fixes, and verification outcomes in `MEMORY.md` and `PROJECT.md`. Commit and push all changes to GitHub `origin main`. Verify via Railway CLI or dashboard that Railway automatically detects the push and finishes a successful build. Verify the remote live production health endpoint (`https://autonomousdaytrader-production.up.railway.app/health`). Immediately terminate all local test servers, mock feeds, and background processes, confirming that ports 8005, 3005, and 8080 are released.

## Acceptance Criteria

### Audit & Code Quality
- [ ] All architectural connections between ingestion, risk engine, order state machine, strategy workers, and WebSocket server operate without deadlocks or unhandled exceptions.
- [ ] All backend unit and contract tests pass with 0 failures (`pytest tests/`).

### Terminology & UI
- [ ] Grep verification confirms zero occurrences of "playlist", "curated playlist", "album", or music analogies in user-facing UI labels, frontend components, or active trade drawers.
- [ ] Frontend builds cleanly with zero errors (`npm --prefix frontend run build`).
- [ ] Mobile (390x844) and desktop (1440x900) UI render properly with all metrics, strategy cards, and active position controls visible and functional.

### Quality Assurance & Simulation
- [ ] Independent subagent diff review confirms code changes are sound and meet system specifications.
- [ ] Full E2E test suite passes 100% (`scripts/run_e2e_tests.sh`).
- [ ] Monday live dry-run simulation completes with valid order fills, bracket lifecycle management, and clean shutdown.

### Remote Deployment & Hygiene
- [ ] `MEMORY.md` and `PROJECT.md` are updated with the audit log, test results, and deployment hash.
- [ ] Changes committed and pushed to GitHub `origin main`.
- [ ] Railway auto-build triggered by GitHub push succeeds with active status `SUCCESS`.
- [ ] Remote health check `GET https://autonomousdaytrader-production.up.railway.app/health` returns `{"status":"ok"}` (or HTTP 200).
- [ ] Local process hygiene verified: zero orphaned background processes or occupied ports (8005, 3005, 8080).

## 2026-09-23T03:48:51Z

Empirically diagnose and remediate underperformance in `AutonomousDayTrader` using live paper execution data, quantitative literature, and market microstructure analysis. Implement robust structural improvements across strategy triggers and bracket geometry, verify through independent multi-agent audit and integrated simulation, update documentation, and deploy to Railway.

Working directory: /Users/mo/AutonomousDayTrader
Integrity mode: development

---

## Background & Production Context
`AutonomousDayTrader` is a live intraday paper-trading system for US equities connected downstream to AlpacaRelay, operating on a $50,000 virtual account.
- **Starting Equity**: $50,000.00
- **Current Production Equity**: $49,798.32 (-$201.68 realized loss)
- **Production Win Rate**: 0.00% across 7 trades (0 wins, 7 losses/scratches).
- **Target 1 (1.5R) Hit Rate**: 0.00% (0 of 7 trades hit Target 1).
- **Key Failure Modes Observed**:
  - 2026-09-21: 4 trades scratched within 3 minutes by trailing stop ratchets walking into entry noise; 1 trade clipped (TSLA long +$18.39, capturing only 23% of intended target) before reversing.
  - 2026-09-22: 2 trades stopped out at full loss: TSLA SHORT (`news_momentum`) @ 09:31 ET (-$68.30) and AAPL SHORT (`orb`) @ 10:09 ET (-$112.04).
  - Context Blindness: Strategies trigger on individual stock bars without checking broader index beta (SPY/QQQ trend), shorting stocks into market-wide morning bid.
  - Unrealistic Profit Geometry: Target 1 at 1.5R and Target 2 at 2.5R are mathematically unachievable for intraday 1m/5m bars before noise stops out the trade.

---

## Requirements

### R1. Quantitative Forensic Analysis & Research
- Conduct deep quantitative and market-microstructure research into:
  - Why Opening Range Breakouts (ORB) fail in mega-cap equities without market index (SPY/QQQ) trend confirmation.
  - Optimal intraday profit target scaling (e.g., banking partial profits at 0.75R–1.0R instead of waiting for 1.5R).
  - Preventing breakout exhaustion fills (chasing the tail of an extended candle).
  - Hardening news sentiment scoring beyond crude regex token-matching.
- Base all diagnoses on real trade logs and code mechanics, never synthetic replay fixtures (`MONDAY_SIMULATION_REPORT.md` is a 62-event plumbing test, not empirical edge).

### R2. Strategy & Execution Architecture Remediation
- Implement a causal market index / trend filter (e.g., SPY/QQQ VWAP or EMA directional alignment) to prevent counter-trend individual stock setups.
- Restructure profit target and bracket management in `backend/app/core/bracket.py`:
  - Enable realistic scaling (e.g., Target 1 at 0.8R–1.0R to de-risk trades quickly).
  - Ensure trailing stops never walk into noise before trade reaches breakeven.
- Refine entry conditions in `backend/app/strategies/orb.py` and `backend/app/strategies/news_momentum.py` to prevent entering at the exact climax of a bar.
- Activate or calibrate `mean_reversion.py` so valid exhaustion setups are not starved by impossible thresholds under moderate VIX (14–16).

### R3. Unbiased Adversarial Multi-Agent Review
- At each phase (diagnosis, design, code diff), deploy independent, unbiased review sub-agents.
- Reviewers must specifically audit for:
  - Lookahead bias / forward data leakage (shifting, unclosed bars, global normalizers).
  - Parameter curve-fitting (penalizing arbitrary constant tweaking without structural rationale).
  - Floating point / boundary edge cases and risk engine invariant violations.

### R4. Deterministic Verification & Integrated Dry Run
- Run the full test suite (`pytest backend/tests`), ensuring 100% pass rate with zero regression.
- Execute the integrated Monday dry run (`python scripts/run_integrated_monday_dry_run.py`) exercising the real production `main.py` wiring.
- Confirm 0 lingering local server daemons or orphaned background processes on ports 8005, 3005, 8080.

### R5. Documentation, Git Commit, and Remote Railway Deployment
- Update `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` documenting:
  - The empirical root causes discovered.
  - Structural changes made and their mathematical justification.
  - The results of the verification and dry run.
- Commit all changes cleanly to git and push to `origin main`.
- Verify that the remote Railway deployment succeeds and the live production `/health` endpoint (`https://autonomousdaytrader-production.up.railway.app/health`) returns `healthy` with valid status and feeds.

---

## Acceptance Criteria

### Diagnostics & Research
- [ ] Root causes of the 7 failed paper trades documented with code citations and log timestamps.
- [ ] Zero claims of edge derived from synthetic fixtures; all empirical assertions verified against live ledger data.

### Code & Architecture
- [ ] Market trend/beta filter active (preventing shorting into an uptrending SPY/QQQ).
- [ ] Bracket profit target geometry recalibrated to achievable intraday R-multiples (Target 1 hit rate improved without compromising risk guardrails).
- [ ] No lookahead bias, unclosed bar dependencies, or future leakage in any strategy.
- [ ] All risk engine limits ($1,500 circuit breaker, $25,000 position cap, 0.4%–4.0% stop guardrails) strictly maintained.

### Independent Review
- [ ] Independent subagent audit completed with zero unresolved CRITICAL or MAJOR findings.
- [ ] Mutation checks performed on key assertions to guarantee test integrity.

### Testing & Verification
- [ ] 100% of unit and integration tests passing (`backend/tests`).
- [ ] `scripts/run_integrated_monday_dry_run.py` completes cleanly with 0 unhandled exceptions.
- [ ] Zero orphaned processes or listening ports left running locally.

### Deployment & Documentation
- [ ] `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` updated with exact session details and rationale.
- [ ] Git commit pushed to `origin main`.
- [ ] Remote Railway deployment live and `GET /health` returning `200 OK` (`status: healthy`).

## 2026-09-23T15:01:42Z

Execute an exhaustive, end-to-end code review of `AutonomousDayTrader`, remediate all identified defects, stress-test and verify via independent adversarial review sub-agents, and deliver a clean production deployment to Railway.

Working directory: /Users/mo/AutonomousDayTrader
Integrity mode: development

---

## Background & Scope
`AutonomousDayTrader` is an institutional-grade intraday trading bot operating on a ,000 virtual account on Railway, connected downstream to AlpacaRelay.
Previous audits resolved market trend filters, bracket geometries, and trailing stops. This mission conducts a comprehensive, full-stack code review across all system layers:
- **Ingestion**: `backend/app/ingestion/` (Stock WS, News WS, VIX client, backpressure, reconnection, queue limits)
- **Core State & Risk**: `backend/app/core/` (Risk engine, bracket manager, market filter, paper account, durable persistence/ledger, EOD flattening)
- **Execution & Strategies**: `backend/app/strategies/` (`orb.py`, `vwap_pullback.py`, `news_momentum.py`, `mean_reversion.py`, `adaptation.py`, `base.py`)
- **API & Lifecycle**: `backend/app/main.py` (FastAPI routes, WebSocket streaming, session boundaries, graceful shutdown)
- **Frontend & UI**: `frontend/` (Next.js components, WebSocket subscriptions, trading drawer, error boundaries)

---

## Requirements

### R1. Comprehensive Full-Stack Code Review
- Perform static analysis, control-flow inspection, and adversarial edge-case review across all backend and frontend modules.
- Identify and catalog:
  - Latent concurrency race conditions or unhandled exceptions in asynchronous loops (`asyncio` tasks, WebSockets).
  - Floating-point knife-edge rounding errors or precision leaks in risk/bracket calculations.
  - Data leakage, lookahead bias, or unclosed bar dependencies in indicators.
  - State desynchronization between in-memory structures, SQLite durable ledger, and UI streaming.
  - Ingestion bottlenecks, backpressure drops, or memory leaks in long-running buffers.

### R2. Systematic Remediation & Hardening
- Implement clean, minimal, production-grade fixes for every valid defect identified during the review.
- Strict non-negotiable invariants:
  - Hard daily loss limit (,500 circuit breaker) and single-position notional cap (,000 / 50% equity) must remain strictly binding.
  - Stop loss distances must remain strictly within `[0.0040, 0.0400]`.
  - Zero overnight holding: 4-phase flattening protocol must reliably liquidate all positions before 16:00 ET.
  - Process hygiene: Zero orphaned background daemons or open listening ports.

### R3. Unbiased Adversarial Multi-Agent Audit
- Deploy independent review and challenger sub-agents that did not write the remediation code.
- Reviewers must:
  - Audit every git diff line by line.
  - Execute mutation checks against new and modified tests to verify they fail on defective code.
  - Challenge concurrency invariants, bracket fill lifecycles, and risk enforcement boundaries.
  - Formally issue approval or blocking change requests.

### R4. Deterministic Verification & Integrated Dry Run
- 100% pass rate across the full backend unit test suite (`pytest backend/tests`).
- 100% pass rate across the comprehensive E2E test runner (`python3 tests/e2e/runner.py`).
- Deterministic integrated Monday dry run (`python scripts/run_integrated_monday_dry_run.py`) must pass on real production wiring with zero unhandled exceptions.
- Verify clean local port hygiene (confirm ports 8000, 8005, 8080, 3005 are clean and liberated).

### R5. Documentation, Git Commit, and Remote Railway Deployment
- Update `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` detailing all findings, remediation mechanics, and audit certifications.
- Commit all changes cleanly and push to `origin main`.
- Monitor remote Railway auto-deploy until status is Online.
- Verify the live production health endpoint (`https://autonomousdaytrader-production.up.railway.app/health`) returns HTTP 200 OK (`status: healthy`).

---

## Acceptance Criteria

### Code Review & Fixes
- [ ] Complete codebase audit executed with all findings classified by severity (CRITICAL, MAJOR, MINOR).
- [ ] All confirmed defects remediated with zero architectural regression.
- [ ] No lookahead bias, repainting, or unclosed bar access in any strategy module.
- [ ] Risk guardrails (,500 daily breaker, ,000 position cap, 0.4%–4.0% stops, EOD flat book) strictly preserved.

### Independent Review
- [ ] Unbiased multi-agent review panel unanimously approves all diffs (zero unresolved CRITICAL or MAJOR findings).
- [ ] Mutation checks verified on key test assertions.

### Verification & Testing
- [ ] 100% backend pytest suite passes (`pytest backend/tests`).
- [ ] 100% E2E test runner passes (`tests/e2e/runner.py`).
- [ ] Integrated simulation dry run passes cleanly with zero event bus errors.
- [ ] Zero lingering local daemons or listening ports on 8000, 8005, 8080, 3005.

### Deployment & Delivery
- [ ] `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` updated with full audit trail.
- [ ] Git commit pushed to GitHub upstream `origin main`.
- [ ] Remote Railway deployment live and `GET /health` returning `200 OK` (`status: healthy`).

## 2026-09-23T19:09:59Z

Implement universe expansion, regime-separated strategy execution, and realistic microstructure calibrations to scale trading frequency and maintain institutional profitability for `AutonomousDayTrader`. All phases must be verified by adversarial, unbiased sub-agents to eliminate hallucinations, data leakage, and lookahead bias, followed by a full end-to-end dry run, UI audit, documentation update, and Railway deployment.

Working directory: /Users/mo/AutonomousDayTrader
Integrity mode: development

---

## Background & Quantitative Objectives
`AutonomousDayTrader` operates on a $50,000 virtual paper trading account on Railway. While previous hardening eliminated catastrophic drawdowns and stabilized capital preservation ($49,798.32 equity, $0.00 drawdown today), the bot suffers from a **Filter-Stacking Bottleneck** that dropped trade frequency to ~0 trades/day:
1. **Universe Bottleneck**: Only 3 single stocks (`AAPL`, `NVDA`, `TSLA`) were monitored.
2. **Sector Lockout**: `AAPL` and `NVDA` both belong to "Technology", meaning only 1 tech stock could ever be held concurrently.
3. **Regime Freezing**: In `NEUTRAL` market regimes (where SPY and QQQ oscillate around VWAP), all directional momentum strategies are locked out while Mean Reversion hurdles were set too extreme to trigger.
4. **Volume Climax Delusions**: News momentum demanded a $3.5x 1-minute volume surge, which buys the exhaustion top after HFT repricing.

---

## Requirements

### R1. Universe Expansion & Sector Mapping
- Expand `backend/app/config.py` `WATCHLIST_SYMBOLS` to top liquid high-beta intraday names across diverse sectors: `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.
- Update `backend/app/core/risk.py` sector mappings for all symbols (Semiconductors, Software, Discretionary, Communication Services, Fintech/Crypto).
- Prevent artificial single-sector starvation while strictly preserving the portfolio concentration cap (no more than 2 positions per sector, max 3 concurrent positions total).

### R2. Regime-Separated Strategy Execution
- **Trending Regimes (`BULLISH` / `BEARISH`)**: Enable ORB and VWAP Pullback along index beta; lock out counter-trend Mean Reversion.
- **Range-Bound / Neutral Regimes (`NEUTRAL`)**: Activate Statistical Mean Reversion to capture intraday oscillations between standard deviation bands (+-1.6 sigma to 20-SMA).
- Enable high-RVOL idiosyncratic breakouts (RVOL >= 2.20x) in `NEUTRAL` if single-stock volume proves institutional decoupling from broader market chop.

### R3. Microstructure & Indicator Calibration
- Calibrate `news_momentum`: Lower volume surge requirement from 3.5x to 2.0x to prevent chasing the top of 1-minute bars; ensure sentiment NLP uses strict boundary matching.
- Calibrate `mean_reversion`: Adjust Z-score threshold from 2.00 to 1.65, volume climax from 1.75x to 1.30x, and wick rejection to 0.30 to enable valid exhaustion fades during chop sessions.
- Preserve all non-negotiable risk invariants: $1,500 daily circuit breaker, $25,000 (50% equity) single-position cap, stop distances strictly in `[0.0040, 0.0400]`, and 4-phase EOD zero-overnight auto-flattening.

### R4. Independent Adversarial Anti-Hallucination & Anti-Bias Audit
- Deploy independent challenger sub-agents that did not write the remediation code.
- Challenger audits must strictly certify:
  - Zero synthetic fixture delusions (no testing on fake data that fabricates edge).
  - Zero lookahead bias or unclosed bar access in all indicator math.
  - Zero floating-point rounding escapes in risk or bracket logic.
  - Mutation tests demonstrating that defective or biased logic fails deterministically.

### R5. Deterministic End-to-End Dry Run & Process Hygiene
- 100% pass rate across backend pytest suite (`pytest backend/tests`).
- Comprehensive end-to-end simulation runner covering all 4 strategies, the expanded universe, and regime transitions.
- Verify clean local port hygiene (confirm ports 8000, 8005, 8080, 3005 are clean and liberated with zero lingering processes).

### R6. Mobile UI Visual Audit, Documentation & Remote Railway Deployment
- Visual audit of the Next.js Apple Music dashboard, bottom drawer state transitions, and WebSocket latency indicators across expanded symbols.
- Update `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` with complete audit trail and mathematical rationale.
- Clean git commit pushed to `origin main`.
- Remote Railway auto-deployment verified live (`GET https://autonomousdaytrader-production.up.railway.app/health` returns HTTP 200 `status: healthy`).

---

## Acceptance Criteria

### Strategy & Universe
- [ ] Watchlist expanded to 12 symbols with clean WebSocket subscription across AlpacaRelay feeds.
- [ ] Sector correlation limits allow multi-sector concurrency without violating portfolio risk.
- [ ] Mean Reversion executes during `NEUTRAL` regimes without fighting runaway trends.
- [ ] Risk guardrails ($1,500 daily breaker, $25,000 position cap, 0.4%–4.0% stops, EOD flat book) strictly binding.

### Adversarial & Anti-Hallucination Verification
- [ ] Independent reviewer sub-agents verify zero lookahead bias, zero synthetic data cheating, and zero data leakage.
- [ ] Mutation tests pass on risk limits and indicator lookbacks.

### End-to-End Simulation & Verification
- [ ] Full backend test suite passes: `pytest backend/tests -q`.
- [ ] Full E2E dry run executes successfully without event loop stalls or unhandled exceptions.
- [ ] Zero lingering daemons or open listening ports on 8000, 8005, 8080, 3005.

### Deployment & UI
- [ ] Frontend build succeeds with zero TypeScript or styling regressions.
- [ ] Visual UI verified for expanded ticker carousel and active position tray.
- [ ] Git commit pushed to `origin main` and Railway remote health endpoint verified HTTP 200 OK.

## 2026-09-23T20:07:47Z

Execute an exhaustive, adversarial code review and audit of `AutonomousDayTrader` across every system angle following the universe expansion to 12 symbols, multi-sector risk engine, and regime-separated execution. Identify latent concurrency races, indicator leakage, numerical precision errors, memory leaks, and edge-case boundary failures; implement production-grade fixes; verify via comprehensive regression and mutation testing; and deliver a verified production deployment to Railway.

Working directory: /Users/mo/AutonomousDayTrader
Integrity mode: development

---

## Background & Scope
`AutonomousDayTrader` just received a major release expanding the watchlist from 3 to 12 high-beta symbols (`SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `AMD`, `MSFT`, `AMZN`, `META`, `GOOGL`, `PLTR`, `COIN`), dynamic sector allocation across 6 sectors, regime-separated execution (`NEUTRAL` mean-reversion activation), and microstructure calibrations. 

A 4x increase in ticker subscriptions introduces 4x higher quote/trade throughput (~3.5M quotes/session), higher concurrency in order book processing, potential buffer accumulation, and complex cross-sector risk transitions. This audit must attack every layer without mercy.

---

## Attack Angles & Audit Requirements

### R1. Adversarial Multi-Angle Code Review & Vulnerability Attack
Attack the system across these specific dimensions:
1. **Concurrency & Event Bus Race Conditions**:
   - Asynchronous queue locks, task cancellation, and WebSocket reconnect backpressure across 12 simultaneous ticker feeds.
   - Race conditions between rapid quote updates, bracket modifications, and order fill callbacks.
2. **Indicator Causality & Synchronization**:
   - Multi-symbol rolling buffers (`all_bars`, `session_bars`, `recent_bars`): check for off-by-one errors, lookahead bias, unclosed bar leakage, and memory growth.
   - Anchor VWAP session reset synchronization across multiple symbols arriving at different microsecond timestamps.
3. **Risk Engine & Bracket Knife-Edge Boundaries**:
   - Floating-point precision leaks in bracket sizing and stop price calculations.
   - Strict enforcement of institutional stop-loss distance bounds: $[0.0040, 0.0400]$ ($40$ to $400$ bps).
   - Hard daily circuit breaker ($1,500 drawdown) and single-position notional cap ($25,000 / 50% equity).
   - Multi-sector concentration cap (max 2 positions per sector, max 3 total positions) under simultaneous signal collisions.
4. **Ingestion & Buffer Memory Hygiene**:
   - Long-running memory leaks in `market_history` (deque capping), news deduplication caches, and SQLite ledger checkpoints.
5. **API & UI State Synchronization**:
   - WebSocket payload serialization safety with 12 active tickers.
   - Frontend error boundary stability and drawer responsiveness.

### R2. Systematic Remediation & Mutation Testing
- Implement clean, minimal, production-grade fixes for every confirmed defect.
- For every fix, implement a mutation test verifying that the test deterministically fails if the bug is reintroduced.
- Non-negotiable risk invariants ($1,500 daily breaker, $25,000 position cap, 4-phase EOD zero-overnight auto-flattening) must remain strictly binding.

### R3. Deterministic Verification & Integrated Dry Run
- 100% pass rate across the full backend unit test suite (`pytest backend/tests`).
- 100% pass rate across the comprehensive E2E test runner (`python3 tests/e2e/runner.py`).
- Deterministic integrated Monday dry run (`python scripts/run_integrated_monday_dry_run.py`) must pass cleanly across the 12-symbol universe with zero unhandled exceptions and flat book at EOD.
- Verify clean local port hygiene (confirm ports 8000, 8005, 8080, 3005 are clean and liberated with zero lingering processes).

### R4. Documentation, Git Commit, and Remote Railway Deployment
- Update `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` detailing all audit findings, attack vectors, and applied remediations.
- Clean git commit pushed to `origin main`.
- Remote Railway auto-deployment verified live (`GET https://autonomousdaytrader-production.up.railway.app/health` returns HTTP 200 `status: healthy`).

---

## Acceptance Criteria

### Audit & Code Quality
- [ ] Multi-angle vulnerability report cataloging all findings by severity (CRITICAL, MAJOR, MINOR).
- [ ] All confirmed defects remediated with zero regressions.
- [ ] Zero lookahead bias, memory leaks, or concurrency race conditions.

### Risk & Boundary Invariants
- [ ] $1,500 daily breaker, $25,000 position cap, $[0.0040, 0.0400]$ stop ranges, and EOD flat book strictly certified.
- [ ] Multi-sector allocation (max 2/sector, max 3 concurrent) tested against simultaneous signal collisions.

### Verification & Testing
- [ ] 100% backend pytest suite passes (`pytest backend/tests -q`).
- [ ] 100% E2E test runner passes (`tests/e2e/runner.py`).
- [ ] Integrated simulation dry run passes on 12 symbols with zero event bus errors.
- [ ] Zero lingering daemons or open listening ports on 8000, 8005, 8080, 3005.

### Deployment & Live Verification
- [ ] Next.js frontend build succeeds with zero TypeScript or styling errors.
- [ ] Commit pushed to `origin main`.
- [ ] Remote Railway deployment live and `GET /health` returns HTTP 200 OK (`status: healthy`).

---

## 2026-09-23T21:24:25Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Full team with dedicated adversarial reviewers (as requested: "have it attacked 3xs by multiple sub agents tht are not biased for hallcuinations, lying, future bias, etc. Then deploy a team of agents to fully implement this have a team review each phase until fully done.")

Integrate an autonomous, multi-day swing trading engine ("2-Day Panic Dip" Connors RSI-2 strategy) into `AutonomousDayTrader` across 5 certified stocks (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`). The swing engine shares the $50,000 account pool ($25,000 allocated per slot, maximum 2 concurrent swing positions), runs fully independently from intraday trading (strictly exempt from 15:58 ET auto-flattening), provides a unified Obsidian dark operator dashboard, undergoes 3x independent adversarial review against lookahead/future bias, and completes end-to-end replay verification and remote Railway deployment.

Working directory: `/Users/mo/AutonomousDayTrader`
Integrity mode: development

## Requirements

### R1. Independent Swing Trading Execution Engine (The "2-Day Panic Dip")
Implement the 7 exact quantitative rules across the 5 certified stocks (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`):
1. **Rule 1 (Macro Floor)**: Today's Daily Close must be strictly above the 200-day Simple Moving Average (SMA).
2. **Rule 2 (Market Leadership / Relative Strength)**: The stock must perform equal to or better than the Nasdaq 100 (`QQQ`) over trailing 60 trading days ($\Delta_{\text{stock}, 60d} \ge \Delta_{\text{QQQ}, 60d}$).
3. **Rule 3 (Panic Trigger)**: The stock's 2-day Connors RSI (`RSI(2)`) must close below 10.0.
4. **Rule 4 (Mandatory Earnings Veto)**: 48-hour blackout window:
   - If the company reports earnings within the next 48 hours, do not enter.
   - If holding an active position and earnings report tomorrow, sell at Market Open (09:30 ET).
5. **Rule 5 (Entry Execution & Sizing)**:
   - When Rules 1–4 are satisfied at 16:00 ET close, stage a buy order executed at next Market Open (09:30 ET).
   - Position sizing: $25,000 notional per trade slot from the shared $50,000 account pool, with a hard cap of maximum 2 concurrent swing positions at any time.
6. **Rule 6 (Emergency Stop-Loss)**:
   - Immediately establish a hard stop-loss at $2.5 \times \text{Daily ATR(14)}$ below the fill price.
7. **Rule 7 (Take-Profit & Time Exit)**:
   - Sell at next Market Open (09:30 ET) as soon as ANY of the following occur:
     a) Prior daily close crosses back above its 5-day SMA.
     b) Prior daily RSI(2) crosses above 70.0.
     c) The position has been held for 5 trading days (time stop).

### R2. Strict Architectural Separation & Flattening Exemption
Ensure the swing engine operates independently from the intraday day-trading bot:
- The existing 4-phase auto-flattening engine (15:45 lockout, 15:50 cancel, 15:55 liquidation, 15:58 flat audit) applies exclusively to intraday day-trading positions.
- Swing positions, bracket stops, and orders must be explicitly tagged and exempt from EOD liquidation so multi-day overnight holds operate uninterrupted.
- Risk management must coordinate buying power across the shared $50,000 account pool without allowing swing and intraday positions to exceed account margin or collide.

### R3. Market Leadership, Calendar, and Signal Pipeline
Build a lookahead-free indicator and calendar data pipeline:
- Rolling daily calculations for 200 SMA, 5 SMA, 14-day ATR, 60-day relative strength vs `QQQ`, and 2-day Connors RSI using strictly causal, closed-session data.
- Automated earnings calendar lookup with graceful cached fallback to prevent execution halts if an external API is transiently unavailable.

### R4. Unified Obsidian Dark Operator Interface
Extend the existing Next.js / Tailwind Obsidian dark design system:
- Clear operator navigation or segmented toggle between "Intraday Day Trader" and "Swing Mean-Reversion".
- Real-time display of swing candidate watchlist status (200 SMA check, 60d RS check, RSI(2) value, earnings blackout check, signal trigger state).
- Active swing positions table showing entry price, current price, unrealized PnL, 2.5x ATR stop line, holding day counter (e.g. Day 2 of 5), and exit trigger conditions.
- Operator override controls (manual position exit at next open or emergency market exit) consistent with existing UX patterns.

### R5. 3x Adversarial Review & Zero-Lookahead Audit
Subject the plan and implementation to 3 separate adversarial review passes conducted by independent reviewer subagents:
- **Pass 1 (Mathematical & Lookahead Audit)**: Verify zero lookahead bias in RSI(2), ATR(14), 200 SMA, 60d RS vs QQQ, and earnings calendar calculations.
- **Pass 2 (State Machine & Flattening Audit)**: Verify that the 15:58 ET intraday auto-flattening engine cannot liquidate or desynchronize swing positions under any edge case or race condition.
- **Pass 3 (Execution Timing & Order Lifecycle Audit)**: Verify 16:00 ET qualification vs 09:30 ET execution timing, weekend/holiday boundary handling, partial fills, stop triggers, and position cap enforcement.

### R6. Deterministic End-to-End Replay, Visual QA, and Remote Deployment
- Implement a deterministic multi-day historical replay test covering qualifying signals, 09:30 ET entries, stop-loss protection, 5-SMA exits, RSI(2)>70 exits, and 5-day time exits across `LRCX`, `KLAC`, `MU`, `AMD`, and `GS`.
- Perform visual QA of the desktop and mobile views of the updated operator dashboard.
- Comply with all Global Agent Rules: terminate all temporary/test local background processes, push commits to `origin main`, verify live Railway cloud build and deployment, and confirm healthy remote endpoints.
- Update project documentation (`PROJECT.md`, `MEMORY.md`, `README.md`).

## Acceptance Criteria

### Quantitative Rule Fidelity
- [ ] Daily Close > 200 SMA, 60-day RS $\ge$ QQQ, and RSI(2) < 10.0 conditions trigger only upon market close (16:00 ET) using finalized daily bars.
- [ ] Market open execution (09:30 ET) places orders for exactly $25,000 notional per slot.
- [ ] Hard maximum of 2 concurrent swing positions is enforced at all times.
- [ ] 48-hour earnings blackout prevents entry when earnings fall within 48 hours, and triggers sell at open if earnings are next day.
- [ ] Hard stop-loss at $2.5 \times \text{Daily ATR(14)}$ is active immediately upon fill.
- [ ] Exits trigger deterministically at next market open upon 5-day SMA cross, RSI(2) > 70, or 5th day holding limit.

### Architecture & Isolation
- [ ] Swing positions are strictly preserved through the 15:45–15:58 ET intraday flattening routine and held overnight.
- [ ] Shared $50,000 account pool correctly tracks cash, buying power, and realized/unrealized PnL across both trading arms without double-spending or desync.

### Adversarial & Verification Quality
- [ ] 3x adversarial review passes completed with documented findings and all critical vulnerabilities remediated.
- [ ] Automated replay test suite passes 100% deterministically.
- [ ] Existing intraday test suite remains 100% passing (zero regressions).

### Interface & Delivery
- [ ] UI provides intuitive operator visibility into both arms matching the Obsidian dark glassmorphism design system.
- [ ] No local test processes or servers left running on ports after completion.
- [ ] Upstream repository pushed to `origin main` and remote Railway deployment verified live and healthy (`200 OK`).
- [ ] `PROJECT.md`, `MEMORY.md`, and `README.md` fully updated.



## 2026-09-23T23:59:13Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Full team with dedicated adversarial auditors, remediation workers, and e2e test engineers (as requested: "deploy the right agents for this work to go through and fix everything. Then i want you to deploy an entire team to run a full e2e dry run to test the whole thing.")

Perform a deep forensic audit of the entire "2-Day Panic Dip" swing trading engine and intraday day-trading integration within `AutonomousDayTrader` to uncover and fix all LLM shortcuts, edge cases, timing vulnerabilities, and mock dependencies. Deploy an engineering and verification team to remediate all findings, run an exhaustive multi-day end-to-end dry run testing both arms concurrently, verify the UI, synchronize all markdown documentation, and deploy to Railway.

Working directory: `/Users/mo/AutonomousDayTrader`
Integrity mode: development

## Requirements

### R1. Deep Forensic Codebase & Architecture Audit
Conduct a deep, unsparing forensic audit across the codebase:
1. **Timing & Market-Open Vulnerabilities**: Inspect 09:30 ET open execution in `backend/app/main.py` and `swing_panic_dip.py` (e.g. what happens if the 09:30:00 bar is delayed, illiquid, or arrives at 09:31; ensure staged orders do not hang indefinitely).
2. **Staged Order Idempotency**: Verify `evaluate_market_close` cannot double-stage entries or exceed the 2-position cap on repeated scans or server restarts between 16:00 and 09:30 ET.
3. **Session Rollover & State Integrity**: Verify `reset_for_new_session()` and `_handle_session_rollover()` cannot wipe staged swing orders, prematurely increment `holding_days`, or release mutual exclusion reservations (e.g. for `AMD`) while active swing holdings or staged orders exist.
4. **Blocking I/O in Async Coroutines**: Audit `backend/app/strategies/earnings_calendar.py` and other services to eliminate synchronous blocking calls (such as `urllib.request.urlopen`) inside async event loops.
5. **Persistence Round-Trip Fidelity**: Verify all swing position attributes (`entry_date`, `entry_atr`, `stop_loss_price`, `holding_days`, `arm`, `strategy_id`) survive SQLite checkpoint encoding, restarts, and restoration without loss or schema degradation.

### R2. Production Remediation & Hardening
Remediate every flaw identified during the audit:
- Guarantee robust open-window order execution (e.g. execution window tolerance or first-available morning print) so staged orders never get marooned.
- Ensure strict idempotency on order staging and state checkpoints.
- Ensure non-blocking asynchronous HTTP fetching for external calendar/market services with durable local cached fallback.
- Extend unit and integration regression test suites to prove that every fixed vulnerability is covered.

### R3. Exhaustive Multi-Day End-to-End Dry Run
Deploy a dedicated simulation team to execute a full-scale end-to-end dry run:
- Simulate multiple consecutive trading days with both the Intraday Day Trading arm (ORB, VWAP, News, Mean Reversion) and the Swing Trading arm (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`) executing simultaneously from the shared $50,000 account pool.
- Verify real-world order lifecycles: 16:00 close signal qualification, 09:30 open fills with realistic slippage, ATR stop protection, 15:45–15:58 ET intraday auto-flattening exemption for swing positions, and all 3 swing exit triggers (5 SMA, RSI $> 70$, 5-day time stop).
- Generate a comprehensive dry-run report (`SWING_FULL_E2E_DRY_RUN_REPORT.md`) logging all transactions, equity curves, drawdown, and arm isolation verification.

### R4. Operator UI Visual QA & WebSocket Resilience
- Verify the Next.js Obsidian dark UI across desktop and mobile viewports.
- Validate that the Segmented Mode Toggle, Swing Candidate Watchlist, Active Swing Positions Table, and manual controls update seamlessly over real-time WebSockets without UI exceptions or layout overflows.

### R5. Cloud Deployment & Process Hygiene
- Update project documentation (`PROJECT.md`, `MEMORY.md`, `README.md`).
- Terminate and verify liberation of all local test processes, ports, and background servers.
- Commit all changes and push to `origin main`.
- Verify live remote Railway build and cloud deployment health endpoints (`/health` and `/api/swing/state`).

## Acceptance Criteria

### Forensic Audit & Remediation
- [ ] Zero unhandled blocking I/O calls inside asyncio loops.
- [ ] Staged swing orders execute reliably even if initial 09:30 open prints arrive with jitter.
- [ ] Staging logic is strictly idempotent across multiple scans, clock ticks, and server restarts.
- [ ] Mutual exclusion for shared symbols (e.g. `AMD`) remains locked across overnight session boundaries until swing positions are fully exited.
- [ ] Complete round-trip SQLite persistence verified for all swing position fields (`entry_atr`, `entry_date`, `holding_days`, `stop_loss_price`).

### End-to-End Dry Run & Verification
- [ ] Multi-day concurrent simulation passes with 100% test success across both trading arms.
- [ ] 0 intraday positions held overnight; 0 swing positions liquidated by 15:58 ET intraday flattening.
- [ ] All 430+ backend tests and E2E suites pass with zero regressions.

### Delivery & Cloud Deployment
- [ ] Mobile (390px) and Desktop (1440px) visual QA certified clean with 0px horizontal overflow.
- [ ] Remote Railway deployment live, healthy (`200 OK`), and returning valid telemetry.
- [ ] All local ports (`3005`, `8000`, `8005`, `8080`) verified free.
- [ ] Documentation updated with complete audit and dry-run evidence.
