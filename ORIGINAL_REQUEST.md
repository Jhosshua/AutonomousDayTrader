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
