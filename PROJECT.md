# Project: AutonomousDayTrader

## Architecture
AutonomousDayTrader is a production-grade, local, always-on algorithmic intraday day trading system for US equities connected downstream to AlpacaRelay, operating on a $50,000 virtual paper trading account. It features 4 dynamically adapted trading strategies, institutional risk guardrails, an Apple Music mobile-inspired web interface, real-time WebSocket state streaming, and full operational certification via an E2E test suite and Monday market open dry run.

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
                  │  - Next.js 16 / React 19 / Tailwind CSS / Framer Motion│
                  │  - Obsidian Dark Theme & Dynamic Momentum Gradient Blur│
                  │  - "Playlists / Albums" Strategy Performance Cards     │
                  │  - "Now Playing" Expandable Bottom Drawer & Live Chart │
                  └────────────────────────────────────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Stock WebSocket Client | Ingest 1-min bars (`b`), quotes (`q`), trades (`t`) with `RELAY_TOKEN` auth, auto-reconnect, and backpressure handling | M1 | ORIGINAL_REQUEST §R1 |
| F2 | News WebSocket Client | Ingest Benzinga news (`T: "n"`), parse headlines, symbols, and compute real-time sentiment score $S \in [-1, 1]$ | M1 | ORIGINAL_REQUEST §R1 |
| F3 | REST `/vix` Client | Query `GET /vix` with `X-Relay-Token`, parse dxFeed print, age validation, and fallback caching | M1 | ORIGINAL_REQUEST §R1 |
| F4 | $50,000 Paper Account | State machine tracking Cash, Equity, 4:1 Day Trading Buying Power ($200k), Positions, Realized/Unrealized PnL, Order lifecycle | M1 | ORIGINAL_REQUEST §R1 |
| F5 | Risk Guardrails & Circuit Breakers | Hard max daily loss limit ($1,500 / 3% drawdown) halting trading, 1–2% per-position risk limit, dynamic sizing | M1 | ORIGINAL_REQUEST §R1 |
| F6 | Dynamic Bracket Orders | Multi-tier take-profit brackets (Target 1 at 1.5R with 50% scale-out, Target 2 at 2.5R or trailing ATR stop) | M1 | ORIGINAL_REQUEST §R1 |
| F7 | Zero Overnight Flattening | 4-phase protocol: 15:45 entry lockout, 15:50 working order purge, 15:55 market liquidation, 15:58 flat audit before 16:00 ET | M1 | ORIGINAL_REQUEST §R1 |
| F8 | Strategy 1: ORB | Opening Range Breakout on 5m/15m bars with RVOL $\ge 1.8\times$, midpoint stops, and target brackets | M2 | ORIGINAL_REQUEST §R2 |
| F9 | Strategy 2: VWAP Pullback | Anchored VWAP from 09:30, standard deviation bands, EMA20 > EMA50 trend filter, bounce confirmation | M2 | ORIGINAL_REQUEST §R2 |
| F10 | Strategy 3: News Momentum | Benzinga news catalyst sentiment trigger, volume surge $>3.5\times$ validation, news contradiction emergency exit | M2 | ORIGINAL_REQUEST §R2 |
| F11 | Strategy 4: Mean Reversion | 1-min bar $Z$-score $\ge 2.5$, RSI-14 extremes with divergence, volume climax fade back to 20-SMA | M2 | ORIGINAL_REQUEST §R2 |
| F12 | Dynamic VIX Adaptation | Self-adaptation across 4 regimes (Low, Normal, Elevated, Crisis) with invariant dollar risk scaling and dynamic stop widths | M2 | ORIGINAL_REQUEST §R2 |
| F13 | Time-of-Day Dynamics | 5 intraday execution regimes: Pre-market (08:00–09:30), Open Flush (09:30–10:00), Trend (10:00–11:30), Chop (11:30–14:00), Power Hour (15:00–16:00) | M2 | ORIGINAL_REQUEST §R2 |
| F14 | Apple Music UI Aesthetic | Obsidian dark palette (`#000000`), dynamic glassmorphism (`backdrop-blur-xl`), animated background gradient blur tinted by portfolio momentum | M3 | ORIGINAL_REQUEST §R3 |
| F15 | Strategy "Playlists/Albums" Cards | Carousel/grid presenting 4 strategies, live PnL, win rate, Sharpe, active status badges | M3 | ORIGINAL_REQUEST §R3 |
| F16 | "Now Playing" Bottom Tray | Docked mini-tray showing active primary trade; spring physics expansion to full modal with live ticker chart, bracket lines, manual controls | M3 | ORIGINAL_REQUEST §R3 |
| F17 | Real-Time UI WebSocket Streaming | High-throughput sub-second state sync from backend (Port 8005) to UI (Port 3005) with zero full-page reloads | M3 | ORIGINAL_REQUEST §R3 |
| F18 | Mock & Replay Market Feed | Protocol-matching deterministic mock server and historical feed replay engine supporting 1x–10x speeds | Test Infra | ORIGINAL_REQUEST §R4 |
| F19 | Opaque-Box E2E Test Suite | 4-tier comprehensive test suite (Category-Partition, BVA, Pairwise, Real-world scenarios) with 100% pass criterion | M4 | ORIGINAL_REQUEST §R4 |
| F20 | Monday Market Open Dry Run | Full mock Monday 09:25–10:30 ET live-speed simulation certifying operational readiness | M5 | ORIGINAL_REQUEST §R4 |
| F21 | Upstream Delivery & Process Hygiene | Git commit history, push to GitHub origin main, graceful process shutdown, port release verification | M6 | ORIGINAL_REQUEST §R5 |

## Milestones

### Implementation Track
| # | Milestone Name | Scope | Dependencies | Status |
|---|----------------|-------|--------------|--------|
| M1 | `engine_ingestion` | AlpacaRelay Ingestion (Stock WS, News WS, REST /vix), $50k Paper Account, Institutional Risk Circuit Breakers, Bracket Orders, Auto-Flattening Engine | None | DONE (83/83 backend, 248/248 E2E pass) |
| M2 | `strategies_adaptation` | 4 Dynamic Strategies (ORB, VWAP Pullback, News Momentum, Mean Reversion), VIX Volatility Regime Scaling, Time-of-Day Phase Engine | M1 | DONE (140/140 backend, 248/248 E2E pass) |
| M3 | `ui_mobile_streaming` | Apple Music Mobile UI (Next.js/Tailwind/Framer), Obsidian Glassmorphism, Momentum Gradient Blur, Strategy Cards, "Now Playing" Drawer, Real-Time WS State Streaming | M1, M2 | DONE (npm build 0 errors, 140 backend, 248 E2E pass) |
| M4 | `integration_e2e_pass` | Integration Track Phase 1: Pass 100% of E2E Test Suite (Tiers 1–4) generated by E2E Testing Track | M1, M2, M3, TEST_READY | DONE (248/248 E2E, 140/140 backend pass) |
| M5 | `adversarial_monday_dryrun` | Integration Track Phase 2: Tier 5 Adversarial Coverage Hardening + Full Monday Market Open Live Simulation Dry Run Certification | M4 | DONE (272/272 E2E, 140/140 backend pass, Monday dry run +$398.30) |
| M6 | `delivery_hygiene` | Git repository commit history, push to GitHub upstream (`git push origin main`), process hygiene & port release certification | M5 | PLANNED |

### E2E Testing Track (Parallel)
| Track | Scope | Outputs | Status |
|-------|-------|---------|--------|
| `e2e_testing_track` | Requirement-driven opaque-box test suite (Tiers 1–4), AlpacaRelay mock replay harness, automated test runner | `TEST_INFRA.md`, `tests/e2e/`, `TEST_READY.md` | DONE (TEST_READY.md published, 248/248 tests pass) |

## Interface Contracts

### 1. AlpacaRelay Feed ↔ Trading Engine
- **Stock WS**: `wss://alpacarelay-production.up.railway.app/v2/stocks` or `ws://127.0.0.1:8080/v2/stocks`
  - Handshake: client connects $\to$ server sends `[{"T":"success","msg":"connected"}]`.
  - Auth: `{"action":"auth","key":"RELAY_TOKEN","secret":""}` $\to$ server responds `[{"T":"success","msg":"authenticated"}]`.
  - Sub: `{"action":"subscribe","bars":["*"],"quotes":["*"],"trades":["*"]}`.
  - Messages: `[{"T":"b","S":"AAPL","o":150.0,"h":151.2,"l":149.8,"c":151.0,"v":12000,"t":"2026-09-21T09:31:00Z"}]`.
- **News WS**: `wss://alpacarelay-production.up.railway.app/news` or `ws://127.0.0.1:8080/news`
  - Auth: `{"action":"auth","key":"RELAY_TOKEN"}` $\to$ `[{"T":"success","msg":"authenticated"}]`.
  - Sub: `{"action":"subscribe","news":["*"]}`.
  - Message: `[{"T":"n","id":1001,"headline":"AAPL announces record Q3 sales","summary":"...","symbols":["AAPL"],"created_at":"2026-09-21T09:32:00Z"}]`.
- **REST VIX**: `GET https://alpacarelay-production.up.railway.app/vix` or `http://127.0.0.1:8080/vix`
  - Header: `X-Relay-Token: <RELAY_TOKEN>`.
  - Response: `{"value": 18.45, "asof": "2026-09-21T09:30:00Z", "received_at": "...", "age_s": 1.2, "observations": 100}`.

### 2. Trading Engine ↔ Strategies
- `Strategy` Abstract Base Class:
  - `on_bar(bar: BarEvent) -> List[SignalEvent]`
  - `on_quote(quote: QuoteEvent) -> None`
  - `on_news(news: NewsEvent) -> List[SignalEvent]`
  - `on_vix(vix: VixPrint) -> None`
  - `on_time_tick(market_time: datetime) -> None`
- Dynamic Context:
  - `RegimeState`: `vix_regime` (LOW, NORMAL, ELEVATED, CRISIS), `time_phase` (PRE_MARKET, OPEN_FLUSH, TREND, MIDDAY_CHOP, POWER_HOUR, EOD_FLATTEN).
  - Risk budget scaling factor: `sizing_multiplier` $\in [0.25, 1.25]$.

### 3. Trading Engine ↔ Paper Account & Risk
- `OrderEvent`: `id`, `symbol`, `side` (BUY/SELL), `qty`, `order_type` (MARKET/LIMIT), `limit_price`, `stop_loss`, `take_profit_1`, `take_profit_2`, `strategy_id`.
- `FillEvent`: `order_id`, `symbol`, `side`, `filled_qty`, `fill_price`, `fee`, `timestamp`.
- `AccountState`: `cash: float`, `equity: float`, `buying_power: float`, `realized_pnl: float`, `unrealized_pnl: float`, `daily_drawdown: float`, `is_circuit_broken: bool`, `positions: Dict[str, Position]`.

### 4. Backend ↔ Apple Music UI (WebSocket Port 8005)
- Endpoint: `ws://127.0.0.1:8005/ws/ui`
- UI Push Payload (`trading_state`):
  ```json
  {
    "type": "STATE_UPDATE",
    "timestamp": "2026-09-21T09:35:00Z",
    "account": {
      "equity": 50420.50,
      "cash": 48100.00,
      "buying_power": 192400.00,
      "daily_pnl": 420.50,
      "daily_pnl_pct": 0.84,
      "is_circuit_broken": false
    },
    "market_context": {
      "vix": 18.5,
      "vix_regime": "NORMAL",
      "time_phase": "TREND",
      "market_status": "OPEN"
    },
    "strategies": [
      {
        "id": "orb",
        "name": "Opening Range Breakout",
        "status": "ACTIVE",
        "daily_pnl": 280.00,
        "win_rate": 0.67,
        "trades_count": 3
      }
    ],
    "primary_position": {
      "symbol": "AAPL",
      "side": "LONG",
      "qty": 100,
      "entry_price": 150.25,
      "current_price": 151.10,
      "unrealized_pnl": 85.00,
      "stop_loss": 149.50,
      "take_profit_1": 151.50,
      "take_profit_2": 152.50,
      "strategy_id": "orb",
      "chart_points": [...]
    },
    "all_positions": [...],
    "recent_activity": [...]
  }
  ```
- UI Action Ingestion:
  - `{"action": "FLATTEN_POSITION", "symbol": "AAPL"}`
  - `{"action": "FLATTEN_ALL"}`
  - `{"action": "TIGHTEN_STOP", "symbol": "AAPL", "new_stop": 150.50}`

## Code Layout
```
/Users/mo/AutonomousDayTrader/
├── .agents/                      # Coordination & agent metadata ONLY
├── backend/                      # Python Core Trading Engine & API Server
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI server (Port 8005) & WS endpoints
│   │   ├── config.py             # Config & env vars (RELAY_TOKEN, ports, etc.)
│   │   ├── core/
│   │   │   ├── account.py        # $50,000 Paper Trading Account state machine
│   │   │   ├── risk.py           # Circuit breaker ($1,500 limit) & position sizing
│   │   │   ├── bracket.py        # Stop-loss & dynamic take-profit brackets
│   │   │   ├── flattening.py     # 4-phase zero-overnight auto-liquidation
│   │   │   └── engine.py         # Main trading engine coordinator & event loop
│   │   ├── ingestion/
│   │   │   ├── stock_ws.py       # AlpacaRelay Stock WS client (1-min bars, quotes, trades)
│   │   │   ├── news_ws.py        # AlpacaRelay News WS client & sentiment scoring
│   │   │   └── vix_client.py     # REST /vix dxFeed client
│   │   ├── strategies/
│   │   │   ├── base.py           # Strategy base class & regime interfaces
│   │   │   ├── orb.py            # Opening Range Breakout strategy
│   │   │   ├── vwap_pullback.py  # VWAP Trend Pullback & Continuation strategy
│   │   │   ├── news_momentum.py  # Catalyst News Momentum Breakout strategy
│   │   │   ├── mean_reversion.py # Statistical Mean Reversion / Exhaustion strategy
│   │   │   └── adaptation.py     # VIX regime scaling & time-of-day dynamics
│   │   └── replay/
│   │       ├── mock_relay.py     # Protocol-accurate AlpacaRelay mock server (Port 8080)
│   │       └── feed_player.py    # Historical & synthetic feed player (1x to 10x)
│   └── tests/
│       ├── unit/                 # Fast backend unit tests
│       └── integration/          # Core engine integration tests
├── frontend/                     # Apple Music Mobile UI (Next.js / React 19)
│   ├── package.json              # Next.js 16, Tailwind CSS 4, Framer Motion, Lucide icons
│   ├── tsconfig.json
│   ├── app/
│   │   ├── layout.tsx            # Obsidian dark theme & viewport metadata
│   │   ├── page.tsx              # Main dashboard view
│   │   └── globals.css           # Glassmorphism utilities & dynamic blurs
│   ├── components/
│   │   ├── Header.tsx            # Portfolio status & momentum glow indicator
│   │   ├── AmbientBackground.tsx # Momentum-tinted background gradient blur
│   │   ├── StrategyCarousel.tsx  # "Playlists / Albums" strategy cards
│   │   ├── StrategyCard.tsx      # Individual strategy performance album art
│   │   ├── NowPlayingTray.tsx    # Collapsible/expandable bottom tray with spring physics
│   │   ├── LiveChart.tsx         # Real-time ticker candlestick/line chart & brackets
│   │   ├── ManualControls.tsx    # Quick Flatten and Tighten Stop buttons
│   │   └── ExecutionLog.tsx      # Audit trail of fills and circuit alerts
│   └── hooks/
│       └── useTradingStream.ts   # WebSocket real-time connection to backend Port 8005
├── tests/
│   └── e2e/                      # Opaque-box E2E Test Suite (Tiers 1–4)
│       ├── runner.py             # E2E test runner
│       ├── test_tier1_features.py# Tier 1 Feature coverage tests (>=5 per feature)
│       ├── test_tier2_boundary.py# Tier 2 Boundary & circuit breaker tests
│       ├── test_tier3_pairwise.py# Tier 3 Cross-feature combination tests
│       └── test_tier4_scenarios.py# Tier 4 Real-world application scenarios
├── scripts/
│   ├── run_dev.sh                # Local development launcher
│   ├── run_monday_dry_run.sh     # Mock Monday 09:25–10:30 market open dry run
│   ├── verify_port_hygiene.sh    # Verify ports freed & zero lingering processes
│   └── deploy_and_push.sh        # Git commit and push upstream script
├── TEST_INFRA.md                 # E2E Test Infrastructure & Coverage Matrix
├── TEST_READY.md                 # Test Ready publication artifact
└── README.md                     # Comprehensive system architecture & operational manual
```

## Port Allocation Matrix (Host Conflict Protection)
| Service | Default Port (Occupied) | Allocated Safe Port | Protocol |
|---------|-------------------------|---------------------|----------|
| Web UI (Next.js) | 3000 (Occupied by `Massage`) | **3005** | HTTP / WebSocket |
| Trading Engine & UI WS | 8000 (Occupied by `MarketCards`) | **8005** | HTTP / WebSocket |
| Mock AlpacaRelay Replay Server | 8080 | **8080** | HTTP / WebSocket |
