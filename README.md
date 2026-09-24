# AutonomousDayTrader 🚀📈

> Intraday paper-trading system for US equities connected downstream to **AlpacaRelay**, operating on a virtual **$50,000** account across four dynamically adapted strategies with a **plain-language, light, mobile-first** dashboard anyone can read.

---

## 🏛️ System Architecture

```
                  ┌────────────────────────────────────────────────────────┐
                  │                 AlpacaRelay Ingestion                  │
                  │  (Stock WS + news channel, REST GET /vix)              │
                  │  [Production: alpacarelay-production.up.railway.app]  │
                  │  [Deterministic Replay: Local Mock Server Port 8080]  │
                  └───────────────────────────┬────────────────────────────┘
                                              │
                                              ▼
                  ┌────────────────────────────────────────────────────────┐
                  │               Day Trading Core Backend                 │
                  │  - AlpacaRelay Protocol Adapters (WS, REST, Auth)      │
                  │  - Market Data & Order Book Event Bus                  │
                  │  - Paper Portfolio State Machine ($50k, 4:1 BP, PnL)   │
                  │  - Institutional Risk Engine ($1,500 Circuit Breaker)  │
                  │  - 4-Phase EOD Auto-Flattening Engine (Zero Overnight) │
                  └───────────────────────────┬────────────────────────────┘
                                              │
                                              ▼
                  ┌────────────────────────────────────────────────────────┐
                  │             Dynamic Strategy Execution Core            │
                  │  1. Opening Range Breakout (ORB 5m/15m)                │
                  │  2. VWAP Trend Pullback & Continuation                 │
                  │  3. Catalyst News Momentum Breakout (Benzinga NLP)     │
                  │  4. Statistical Mean Reversion / Exhaustion Fades      │
                  │  * Dynamic Self-Adaptation: VIX Regimes & Time-of-Day  │
                  └───────────────────────────┬────────────────────────────┘
                                              │
                                              ▼
                  ┌────────────────────────────────────────────────────────┐
                  │          Real-Time UI WebSocket Server (Port 8005)     │
                  │  - Broadcasts Account, Positions, Signals, Brackets    │
                  │  - Ingests Manual Overrides (Flatten, Tighten Stop)    │
                  └───────────────────────────┬────────────────────────────┘
                                              │ WebSocket Sync
                                              ▼
                  ┌────────────────────────────────────────────────────────┐
                  │          Mobile-First Trading UI (Port 3005)           │
                  │  - Next.js 15 / React 19 / Tailwind CSS / Framer Motion│
                  │  - Plain-language light theme (no trading jargon)      │
                  │  - Trading Strategy Performance Cards                  │
                  │  - "Active Position" Expandable Bottom Drawer & Live   │
                  └────────────────────────────────────────────────────────┘
```

---

## ⚡ Core Features

### 1. Deterministic Signal Ingestion & Paper Account
- **Downstream Protocol Ingestion**: Resilient WebSocket stream ingestion for 1-minute bars (`b`), quotes (`q`), trades (`t`), and Benzinga news (`n`) with sub-second sentiment scoring $S \in [-1, 1]$.
- **$50,000 Paper Portfolio State Machine**: Tracks Cash, Total Equity, 4:1 Day Trading Buying Power ($200,000), Realized & Unrealized PnL, and strict margin maintenance.
- **Institutional Risk Guardrails**:
  - Hard Daily Circuit Breaker: Automatically halts trading and liquidates upon reaching $1,500 (3%) daily drawdown.
  - Per-Position Risk Cap: 1–2% maximum risk budget per trade.
  - Single-Position Concentration Cap: $25,000 notional (50% of equity, 12.5% of 4:1 DTBP). With the 3-position concurrency limit the whole book tops out at $75,000 (1.5x equity), and a 5% adverse gap on the largest allowed position costs $1,250, inside the $1,500 daily breaker.
  - Dynamic Brackets: Multi-target profit scaling (Target 1 at 1.5R with 50% scale-out, Target 2 at 2.5R or trailing ATR stop).
  - 4-Phase Zero-Overnight Flattening: 15:45 entry lockout $\to$ 15:50 working order purge $\to$ 15:55 market liquidation $\to$ 15:58 flat audit before 16:00 ET.

### 2. 4 Dynamically Adapted Intraday Strategies
1. **Opening Range Breakout (ORB)**: Evaluates 5m/15m opening ranges with relative volume surge ($\ge 1.8\times$), midpoint invalidation stops, and tiered profit targets.
2. **VWAP Trend Pullback & Continuation**: Anchored intraday VWAP with standard deviation volatility bands and EMA20/EMA50 trend confirmation.
3. **Catalyst News Momentum Breakout**: Real-time Benzinga news sentiment parsing, volume surge validation ($>3.5\times$), and immediate contradictory news emergency exit.
4. **Statistical Mean Reversion / Exhaustion Fades**: 1-minute $Z$-score ($\ge 2.5\sigma$) and RSI-14 extreme overbought/oversold fades back to the 20-period moving average.
- **Dynamic Self-Adaptation**: Adapts position sizing, entry criteria, and stop widths dynamically across 4 VIX Volatility Regimes (Low, Normal, Elevated, Crisis) and 5 Time-of-Day Execution Phases (Pre-market scan, Open flush, Trend continuation, Midday chop defense, Power hour).

### 3. Autonomous Multi-Day Swing Trading Engine ("2-Day Panic Dip")
An autonomous multi-day swing engine operating across 5 certified liquid high-beta US equities (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`) and benchmark `QQQ`:
- **7 Quantitative Rules**:
  1. *Macro Floor*: Daily Close > 200-day Simple Moving Average (SMA).
  2. *Market Leadership / Relative Strength*: Trailing 60-day return $\ge$ QQQ return ($\Delta_{\text{stock},60d} \ge \Delta_{\text{QQQ},60d}$).
  3. *Panic Trigger*: 2-day Connors RSI (Wilder's RSI(2) on daily closes) < 10.0.
  4. *Mandatory Earnings Veto*: 48-hour entry blackout window; holding position sold at 09:30 open if earnings report tomorrow.
  5. *Entry Execution & Sizing*: 16:00 ET close qualification $\to$ overnight staging in `SwingStagedOrderManager` $\to$ executed at next 09:30 ET open. Fixed $25,000 notional per slot (`floor(25000 / open)` shares) with hard cap of maximum 2 concurrent swing positions.
  6. *Emergency Stop-Loss*: Hard stop established immediately upon fill at $P_{\text{fill}} - 2.5 \times \text{Daily ATR(14)}$; intraday price breach triggers immediate market liquidation.
  7. *Take-Profit & Time Exit*: Sold at next 09:30 open if prior close > 5-day SMA, prior RSI(2) > 70.0, or held for 5 trading days.
- **Strict Architectural Separation & EOD Flattening Exemption**:
  - Swing positions are explicitly tagged `arm=TradingArm.SWING` and strictly exempt from the 15:45–15:58 ET intraday auto-flattening engine and session sweeps.
  - Shares the $50,000 virtual paper trading account pool with intraday day trading without margin collision or double-spending.
  - Symbol-level mutual exclusion prevents concurrent intraday and swing trades on the same symbol (e.g. `AMD`).
- **Operator Interface (Slow trades tab)**:
  - Tab toggle "Quick trades (same day)" / "Slow trades (a few days)".
  - Spots shown as held / buying at next open / free; each watched company shows 4 plain checks (pass, fail, or unknown when data is missing); held positions show "Day N of 5", safety exit, and Sell at next open / Raise safety exit / Sell now.

### 4. Plain-Language Dashboard (redesigned 2026-09-24)
Written for someone who knows nothing about stocks. Approved mockups: `docs/ui_redesign_2026_09_24/`. Plan and Codex attack: `PLAN_2026_09_24_plain_language_ui.md`.
- **Look**: light cream ground, one muted color per strategy (Morning Breakout apricot, Ride the Trend sage, Big News rose, Snap Back lavender) used on its card, hours bar, and trade tags. Fonts Fraunces + Instrument Sans via `@fontsource` (no network needed at build).
- **Top**: live balance, "Finished trades today" step chart (cumulative realized P&L, not a balance history), and a "Right now" sentence.
- **Strategy cards**: plain name, one-line explanation, status chip from `window.state`, trading-hours bar from `window.ranges` with a now marker, and today's result from the durable ledger (`/api/trades?range=today`, all pages). Strategy counters are NOT used for display.
- **Holding now**: each open quick trade with "Sell now"/"Close trade" and "Move safety exit to my buy price" (enabled only when it improves protection). Every action has confirm, sending, done, and "didn't go through" states.
- **Safety card**: daily loss limit from `/health` `limits`, closing time, and "Close all quick trades now" (intraday only; swing holdings are skipped and reported).
- **Show pro words**: toggle reveals technical names, win rate, and the raw execution audit log.
- Always visible warnings: price feed down, saving problems, daily loss limit hit, reconnecting.
- **Sub-Second Streaming**: Bi-directional WebSocket synchronization over Port 8005 with zero page reloads.

---

## 🔒 Safe Port Allocation Matrix

To prevent port conflicts with other services running on the host machine:

| Service | Default (Occupied) | Safe Project Port | Protocol | Description |
|---------|--------------------|-------------------|----------|-------------|
| **Web UI** | 3000 | **3005** | HTTP / WS | Next.js Mobile Trading Dashboard |
| **Backend Core** | 8000 | **8005** | HTTP / WS | FastAPI Core Engine & WebSocket Server |
| **Mock Replay** | - | **8080** | HTTP / WS | AlpacaRelay deterministic feed replay |

---

## 🚀 Quickstart & Development

### Prerequisites
- Python 3.9+ with `pip`
- Node.js 18+ and `npm`

### Installation
```bash
# Clone the repository
git clone https://github.com/Jhosshua/AutonomousDayTrader.git
cd AutonomousDayTrader

# Install Python dependencies
pip install -r backend/requirements.txt  # or pip install fastapi uvicorn websockets pytest

# Install Frontend dependencies
cd frontend
npm install
cd ..

# Production/live-feed mode requires the relay token as an environment variable.
# Do not commit it to .env or source files.
export RELAY_TOKEN="<private relay token>"
```

### Running the System
```bash
# Option 1: Start local development servers (Backend Port 8005, Frontend Port 3005)
./scripts/run_dev.sh

# Option 2: Run backend standalone
uvicorn backend.app.main:app --host 0.0.0.0 --port 8005

# Option 3: Run frontend standalone
cd frontend && npm run dev
```

### Production Deployment

Operator reporting: each strategy card shows its trading hours, whether it can open a trade right now and why not, and today's signal counts. `GET /api/decisions` lists every signal and the decision made; `GET /api/strategies` includes the `window` and `decisions` blocks.

Live Railway dashboard: https://autonomousdaytrader-production.up.railway.app

The Railway service `AutonomousDayTrader` is connected to the GitHub repo `Jhosshua/AutonomousDayTrader` (`main` branch) with an automatic deployment trigger: every push to `main` rebuilds and redeploys production. No manual `railway up` is needed. `scripts/deploy_and_push.sh` wraps this flow with test/build gates and a post-push `/health` verification.

The deployed `/health` endpoint is the source of truth for upstream readiness; the current verified state reports stock, news, and VIX connected. The system remains paper-trading only.

### Durable Account Ledger

Production persists the complete paper account, active positions, orders, brackets,
risk/flattening state, strategy runtime windows, audit history, and completed trades
to an atomic SQLite ledger on a dedicated Railway volume. The service refuses to
start when production persistence is required but its checkpoint is missing or
corrupt; it never silently falls back to a fresh $50,000 account.

Required production configuration:

```bash
PERSISTENCE_ENABLED=true
PERSISTENCE_REQUIRED=true
STATE_DB_PATH=/data/trading_state.sqlite3
STATE_BACKUP_PATH=/data/trading_state.backup.sqlite3
```

The dashboard's **Trade History** panel and `GET /api/trades` expose durable status,
session summaries, and completed-trade execution legs. The September 21 recovery is
stored as `LEGACY_SUMMARY_IMPORT`: its verified $49,978.66 closing equity, five-trade
count, and -$21.34 session result are preserved without inventing lost fills.

### Running Test Suites
```bash
# Run the complete opaque-box E2E test suite (325 tests with port audit)
python3 tests/e2e/runner.py

# Run all E2E tests using pytest (covering Tier 1-5, swing multi-day replay, and visual checks)
pytest tests/e2e

# Run backend unit & integration test suite (485 tests)
pytest backend/tests

# Run dedicated forensic remediation unit test suite (11 tests)
pytest backend/tests/unit/test_swing_forensic_remediation.py -v

# Run cross-arm isolation and persistence stress test suite (12 tests)
pytest backend/tests/stress/test_cross_arm_isolation_persistence.py -v

# Run frontend architecture and streaming stress tests
cd frontend && npm test

# Run Headless Chrome live visual QA & viewport overflow audit
python3 scripts/verify_visual_qa_live.py
```

### Deterministic Simulation Replays & Dry Runs
```bash
# Execute Concurrent Multi-Day E2E Dry Run (6-day concurrent Intraday + Swing replay, +$3,056.09 PnL)
python3 scripts/run_concurrent_multiday_e2e_dry_run.py

# Execute Integrated Multi-Day Swing Dry Run (6-day deterministic replay verifying all 7 rules)
python3 scripts/run_integrated_swing_dry_run.py

# Execute the mock Monday 09:25 - 10:30 ET intraday replay through the production path
./scripts/run_monday_dry_run.sh
```

### Process Hygiene & Port Verification
```bash
# Verify all project ports are clean with zero lingering background daemons
./scripts/verify_port_hygiene.sh
```

---

## 📁 Repository Structure

```
AutonomousDayTrader/
├── backend/                      # Python Core Trading Engine & API Server
│   ├── app/
│   │   ├── main.py               # FastAPI server (Port 8005) & WS streaming
│   │   ├── config.py             # Config & env vars (ports, tokens, risk limits)
│   │   ├── data/                 # Seed historical daily bars & earnings calendar
│   │   ├── core/
│   │   │   ├── account.py        # $50,000 Paper Account state machine & TradingArm
│   │   │   ├── risk.py           # Institutional Risk Engine & circuit breakers
│   │   │   ├── bracket.py        # Stop-loss & dynamic take-profit brackets
│   │   │   ├── flattening.py     # 4-phase zero-overnight auto-liquidation state machine
│   │   │   └── engine.py         # Main execution engine coordinator
│   │   ├── ingestion/
│   │   │   ├── stock_ws.py       # Stock WebSocket client (bars, quotes, trades)
│   │   │   ├── news_ws.py        # News WebSocket client & sentiment engine
│   │   │   └── vix_client.py     # REST /vix dxFeed client
│   │   ├── strategies/
│   │   │   ├── base.py           # Strategy base class & signal definitions
│   │   │   ├── orb.py            # Opening Range Breakout strategy
│   │   │   ├── vwap_pullback.py  # VWAP Trend Pullback & Continuation
│   │   │   ├── news_momentum.py  # Catalyst News Momentum Breakout
│   │   │   ├── mean_reversion.py # Statistical Mean Reversion / Exhaustion
│   │   │   ├── adaptation.py     # VIX regime scaling & time-of-day dynamics
│   │   │   ├── swing_panic_dip.py# 2-Day Panic Dip Connors RSI(2) swing engine
│   │   │   ├── swing_indicators.py# Causal rolling daily indicators & daily bar store
│   │   │   └── earnings_calendar.py# 48-hour earnings blackout & veto engine
│   │   └── replay/
│   │       ├── mock_relay.py     # Protocol-accurate AlpacaRelay mock server
│   │       └── feed_player.py    # Historical & synthetic feed replay player
│   └── tests/                    # Unit, stress, and mutation test suites (485 tests)
├── frontend/                     # Mobile Trading UI (Next.js 15 / React 19)
│   ├── app/                      # App router layout, page, and globals
│   ├── components/               # Header, Strategy Cards, SegmentedModeToggle, Swing components
│   ├── hooks/                    # useTradingStream WebSocket client hook
│   └── scripts/                  # UI verification & streaming stress test scripts
├── tests/
│   └── e2e/                      # Opaque-box E2E test suite (325 tests)
├── scripts/
│   ├── run_dev.sh                # Local development launcher
│   ├── run_concurrent_multiday_e2e_dry_run.py # Concurrent Multi-Day E2E dry run (+ $3,056.09 PnL)
│   ├── run_integrated_swing_dry_run.py # Production-path multi-day swing replay
│   ├── verify_visual_qa_live.py  # Headless Chrome live visual QA (0px overflow)
│   ├── run_monday_dry_run.sh     # Monday market open live simulation script
│   ├── run_integrated_monday_dry_run.py # Production-path integration replay
│   ├── verify_port_hygiene.sh    # Process hygiene & port liberation auditor
│   └── deploy_and_push.sh        # Git commit and push upstream deployment script
├── SWING_FULL_E2E_DRY_RUN_REPORT.md # Master 6-day concurrent simulation dry run report
├── SWING_SIMULATION_REPORT.md    # Multi-day swing dry run verification report
├── MONDAY_SIMULATION_REPORT.md   # Intraday Monday dry run replay report
├── TEST_INFRA.md                 # E2E test methodology & coverage matrix
├── TEST_READY.md                 # Test harness readiness certificate
└── PROJECT.md                    # Project architectural blueprint & contract specs
```

---

## 📄 License
MIT License. Developed for automated institutional-grade intraday trading on US equity markets.
