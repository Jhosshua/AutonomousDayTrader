# AutonomousDayTrader 🚀📈

> Production-grade, local, always-on algorithmic intraday day trading system for US equities connected downstream to **AlpacaRelay**, operating on a virtual **$50,000** paper trading account across 4 dynamically adapted, high Sharpe-ratio day trading strategies with an **Apple Music mobile-inspired** UI.

---

## 🏛️ System Architecture

```
                  ┌────────────────────────────────────────────────────────┐
                  │                 AlpacaRelay Ingestion                  │
                  │  (Stock WS /v2/stocks, News WS /news, REST GET /vix)   │
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
                  │          Apple Music Mobile-First UI (Port 3005)       │
                  │  - Next.js 15 / React 19 / Tailwind CSS / Framer Motion│
                  │  - Obsidian Dark Theme & Dynamic Momentum Gradient Blur│
                  │  - "Playlists / Albums" Strategy Performance Cards     │
                  │  - "Now Playing" Expandable Bottom Drawer & Live Chart │
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
  - Dynamic Brackets: Multi-target profit scaling (Target 1 at 1.5R with 50% scale-out, Target 2 at 2.5R or trailing ATR stop).
  - 4-Phase Zero-Overnight Flattening: 15:45 entry lockout $\to$ 15:50 working order purge $\to$ 15:55 market liquidation $\to$ 15:58 flat audit before 16:00 ET.

### 2. 4 Dynamically Adapted Intraday Strategies
1. **Opening Range Breakout (ORB)**: Evaluates 5m/15m opening ranges with relative volume surge ($\ge 1.8\times$), midpoint invalidation stops, and tiered profit targets.
2. **VWAP Trend Pullback & Continuation**: Anchored intraday VWAP with standard deviation volatility bands and EMA20/EMA50 trend confirmation.
3. **Catalyst News Momentum Breakout**: Real-time Benzinga news sentiment parsing, volume surge validation ($>3.5\times$), and immediate contradictory news emergency exit.
4. **Statistical Mean Reversion / Exhaustion Fades**: 1-minute $Z$-score ($\ge 2.5\sigma$) and RSI-14 extreme overbought/oversold fades back to the 20-period moving average.
- **Dynamic Self-Adaptation**: Adapts position sizing, entry criteria, and stop widths dynamically across 4 VIX Volatility Regimes (Low, Normal, Elevated, Crisis) and 5 Time-of-Day Execution Phases (Pre-market scan, Open flush, Trend continuation, Midday chop defense, Power hour).

### 3. Apple Music Mobile-Inspired UI
- **Design System**: Obsidian dark palette (`#000000`), backdrop glassmorphism (`backdrop-blur-xl`), animated background gradient mesh dynamically tinted by portfolio momentum (green for profit, red for drawdown, violet for neutral).
- **Strategy "Albums / Playlists"**: Horizontal carousel showcasing the 4 strategies with cover artwork, live daily PnL, win rate badges, and active state indicators.
- **"Now Playing" Expandable Drawer**: Docked mini-tray displaying the primary active position; spring-physics gesture expansion (`stiffness: 350, damping: 32`) reveals live ticker candlestick charts, bracket orders, and manual intervention controls (Quick Flatten, Tighten Stop).
- **Sub-Second Streaming**: Bi-directional WebSocket synchronization over Port 8005 with zero page reloads.

---

## 🔒 Safe Port Allocation Matrix

To prevent port conflicts with other services running on the host machine:

| Service | Default (Occupied) | Safe Project Port | Protocol | Description |
|---------|--------------------|-------------------|----------|-------------|
| **Web UI** | 3000 | **3005** | HTTP / WS | Next.js Apple Music Mobile Dashboard |
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

### Running Test Suites
```bash
# Run complete opaque-box E2E test suite (272 tests with port audit)
python3 tests/e2e/runner.py

# Run all tests using pytest (293 tests covering Tier 1-5 + UI resilience)
pytest tests/e2e

# Run backend unit test suite (140 tests)
pytest backend/tests

# Run frontend architecture and streaming stress tests
cd frontend && npm test
```

### Monday Market Open Live Dry Run
```bash
# Execute mock Monday 09:25 - 10:30 ET live session simulation
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
│   │   ├── core/
│   │   │   ├── account.py        # $50,000 Paper Account state machine
│   │   │   ├── risk.py           # Institutional Risk Engine & circuit breakers
│   │   │   ├── bracket.py        # Stop-loss & dynamic take-profit brackets
│   │   │   ├── flattening.py     # 4-phase zero-overnight auto-liquidation
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
│   │   │   └── adaptation.py     # VIX regime scaling & time-of-day dynamics
│   │   └── replay/
│   │       ├── mock_relay.py     # Protocol-accurate AlpacaRelay mock server
│   │       └── feed_player.py    # Historical & synthetic feed replay player
│   └── tests/                    # Unit and stress test suites (140 tests)
├── frontend/                     # Apple Music Mobile UI (Next.js 15 / React 19)
│   ├── app/                      # App router layout, page, and globals
│   ├── components/               # Header, Album Cards, NowPlayingTray, LiveChart
│   ├── hooks/                    # useTradingStream WebSocket client hook
│   └── scripts/                  # UI verification & streaming stress test scripts
├── tests/
│   └── e2e/                      # Opaque-box E2E test suite (Tiers 1–5, 272+ tests)
├── scripts/
│   ├── run_dev.sh                # Local development launcher
│   ├── run_monday_dry_run.sh     # Monday market open live simulation script
│   ├── run_monday_dry_run.py     # Simulation dry run execution engine
│   ├── verify_port_hygiene.sh    # Process hygiene & port liberation auditor
│   └── deploy_and_push.sh        # Git commit and push upstream deployment script
├── MONDAY_SIMULATION_REPORT.md   # Monday dry run audit report (+$398.30 PnL)
├── TEST_INFRA.md                 # E2E test methodology & coverage matrix
├── TEST_READY.md                 # Test harness readiness certificate
└── PROJECT.md                    # Project architectural blueprint & contract specs
```

---

## 📄 License
MIT License. Developed for automated institutional-grade intraday trading on US equity markets.
