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
