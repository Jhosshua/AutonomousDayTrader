# TEST_INFRA: AutonomousDayTrader Opaque-Box E2E Testing Framework

**Document Version**: 1.0.0  
**Author**: `test_writer_e2e` (Test Architect & QA Specialist)  
**Date**: 2026-09-19  
**Status**: ACTIVE & AUTHORITATIVE  
**Target Project**: AutonomousDayTrader (`/Users/mo/AutonomousDayTrader`)  

---

## 1. Opaque-Box Testing Methodology

The AutonomousDayTrader verification framework adheres strictly to an **opaque-box (black-box) software engineering testing methodology**. The testing harness treats the trading engine, risk controllers, dynamic strategies, and UI interfaces as observable input-output systems governed by documented contracts, mathematical formulas, state machine invariants, and network protocols.

The framework is structured into **four rigorous testing tiers**:

```
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 1: Category-Partition Method (CPM)                    │
│   - Partition domain into functional categories, equivalence classes   │
│   - >=5 isolated, deterministic test cases per feature (F1 to F21)     │
│   - Target: >= 105 tests across all 21 features                        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 2: Boundary Value Analysis (BVA)                      │
│   - Stress testing exact operational boundaries (at, above, below)     │
│   - >=5 boundary & edge test cases per feature (F1 to F21)             │
│   - Daily loss limit ($1,500), 15:55 close, position caps, wide spreads│
│   - Target: >= 105 boundary tests across all 21 features               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 3: Combinatorial & Pairwise Testing                   │
│   - All-pairs orthogonal array covering multi-dimensional interactions │
│   - Strategy x VIX Regime x Session Phase x Drawdown x Execution Fill  │
│   - Target: >= 32 orthogonal pairwise interaction suites               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 4: Real-World Scenarios & Signal-to-Exit E2E          │
│   - Full lifecycle pipelines: ORB Breakout, Chop Defense, News Shock   │
│   - End-of-Day 15:55 MOC Flattening, Circuit Breaker Lockout, Dry Run  │
│   - Target: >= 6 comprehensive end-to-end operational workflows        │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Tier 1: Category-Partition Method (CPM)
Decomposes functional specifications into distinct categories, environmental conditions, and equivalence partitions. Each equivalence class is verified using representative inputs:
- **Valid Partitions**: Normal operating inputs that must produce deterministic outputs.
- **Invalid Partitions**: Malformed, out-of-order, or unauthorized inputs that must trigger graceful rejection, error logging, and system defense.
- **State Invariants**: Pre-conditions and post-conditions that must hold true across state transitions.

### 1.2 Tier 2: Boundary Value Analysis (BVA)
Tests behavioral shifts at exact numerical and temporal boundaries ($\text{Threshold} - \epsilon$, $\text{Threshold}$, $\text{Threshold} + \epsilon$):
- Account drawdown: $\$1,499.50$, $\$1,500.00$, $\$1,500.50$.
- Session clock: `15:44:59` (normal), `15:45:00` (entry lockout), `15:49:59`, `15:50:00` (order purge), `15:54:59`, `15:55:00` (force flatten), `15:58:00` (zero audit).
- Position sizing: Micro-stop ($0.39\%$ vs $0.40\%$), max allocation ($25.0\%$ vs $25.1\%$), cash ceiling ($100\%$ cash vs $4\times$ margin).
- VIX regimes: $14.99$ (Low) vs $15.00$ (Normal), $24.99$ (Normal) vs $25.00$ (Elevated), $34.99$ (Elevated) vs $35.00$ (Crisis).
- Network & auth: Exact token vs invalid token vs empty token vs token timeout.

### 1.3 Tier 3: Combinatorial Pairwise Testing
Executes an orthogonal array testing matrix to verify that no unexpected interaction bugs arise between system dimensions:
- Factor A: Strategy (ORB, VWAP Pullback, News Momentum, Mean Reversion)
- Factor B: Volatility Regime (Low, Normal, Elevated, Crisis)
- Factor C: Time-of-Day Phase (Pre-Market, Open Flush, Trend, Midday Chop, Power Hour, Flatten Window)
- Factor D: Account State (Healthy, Drawdown Warning, Circuit Breaker Tripped, Cash Flat)
- Factor E: Execution Dynamics (Clean Fill, Partial Fill, High Slippage, Spread Veto)

### 1.4 Tier 4: Real-World Scenarios
Exercises full intraday trading workflows from market data ingestion to order routing, fill processing, dynamic stop adjustment, and position liquidation under synthetic and replayed market feeds.

---

## 2. Feature Inventory & Coverage Mapping (F1 to F21)

| Feature ID | Feature Name | Tier 1 (CPM) | Tier 2 (BVA) | Tier 3 (Pairwise) | Tier 4 (Scenario) | Target Test Count |
|---|---|:---:|:---:|:---:|:---:|:---:|
| **F1** | Stock WebSocket Client | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F2** | News WebSocket Client | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F3** | REST `/vix` Client | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F4** | $50,000 Paper Account Ledger | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F5** | Risk Guardrails & Circuit Breakers | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F6** | Dynamic Bracket Orders | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F7** | Zero Overnight Auto-Flattening | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F8** | Strategy 1: ORB Breakout | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F9** | Strategy 2: VWAP Pullback | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F10** | Strategy 3: News Momentum | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F11** | Strategy 4: Mean Reversion | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F12** | Dynamic VIX Regime Adaptation | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F13** | Time-of-Day Session Dynamics | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F14** | Apple Music UI Aesthetic | 5 | 5 | N/A | Yes | $\ge 10$ |
| **F15** | Strategy "Playlists/Albums" Cards | 5 | 5 | N/A | Yes | $\ge 10$ |
| **F16** | "Now Playing" Bottom Tray | 5 | 5 | N/A | Yes | $\ge 10$ |
| **F17** | Real-Time UI WebSocket Streaming | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F18** | Mock & Replay Market Feed | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F19** | Opaque-Box E2E Test Suite | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F20** | Monday Market Open Dry Run | 5 | 5 | Yes | Yes | $\ge 10$ |
| **F21** | Upstream Delivery & Process Hygiene | 5 | 5 | Yes | Yes | $\ge 10$ |
| **TOTAL** | **Full System Coverage** | **105** | **105** | **32** | **6** | **$\ge 248$ Tests** |

---

## 3. AlpacaRelay Mock Server & Replay Architecture

### 3.1 Architecture Diagram
```
                     ┌────────────────────────────────────────┐
                     │      Deterministic Mock Relay Server   │
                     │  (backend/app/replay/mock_relay.py)    │
                     └───────────────────┬────────────────────┘
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 ▼                       ▼                       ▼
    ┌────────────────────────┐┌─────────────────────┐┌────────────────────────┐
    │ Stock WebSocket Server ││ News WebSocket Server││  REST API HTTP Server │
    │ Port 8080 (or custom)  ││ Port 8080 (or custom)││  Port 8080 (or custom) │
    │ - Handshake banner     ││ - Auth handshake    ││ - GET /vix            │
    │ - Auth token check     ││ - Benzinga 'n' stream││ - GET /health         │
    │ - Bar 'b' stream       ││ - Wildcard & symbol  ││ - GET /data/.../bars  │
    │ - Quote 'q' stream     ││ - Sentiment metadata ││ - Zero query param    │
    │ - Trade 't' stream     ││ - Upstream status    ││   header validation   │
    └────────────────────────┘└─────────────────────┘└────────────────────────┘
                 ▲                       ▲                       ▲
                 └───────────────────────┼───────────────────────┘
                                         │
                     ┌───────────────────┴────────────────────┐
                     │   Deterministic Feed Replayer Engine   │
                     │   - Speed: 1x (real-time) to 10x       │
                     │   - Step mode: tick-by-tick advance   │
                     │   - Fixture Loader: JSON / JSONL       │
                     └────────────────────────────────────────┘
```

### 3.2 Protocol Conformance Matrix
1. **Stock WebSocket (`/v2/stocks` or root)**:
   - Initial TCP Connection: Server immediately dispatches `[{"T":"success","msg":"connected"}]`.
   - Authentication Message: Client sends `{"action":"auth","token":"<RELAY_TOKEN>"}` or `{"action":"auth","key":"<RELAY_TOKEN>","secret":""}`.
     - Valid: Dispatches `[{"T":"success","msg":"authenticated"}]`.
     - Invalid: Dispatches `[{"T":"error","code":402,"msg":"auth failed"}]` and terminates TCP connection.
   - Subscription Command: Client sends `{"action":"subscribe","bars":["*"],"quotes":["AAPL"],"trades":["NVDA"]}`.
     - Server confirms: `[{"T":"subscription","bars":[...],"quotes":[...],"trades":[...]}]`.
   - Outbound Feed Items:
     - Bars: `[{"T":"b","S":"AAPL","o":150.0,"h":151.2,"l":149.8,"c":151.0,"v":12000,"t":"2026-09-21T13:31:00Z","n":812,"vw":150.6}]`
     - Quotes: `[{"T":"q","S":"AAPL","bx":"V","bp":150.95,"bs":5,"ax":"V","ap":151.05,"as":4,"t":"...","c":["R"],"z":"C"}]`
     - Trades: `[{"T":"t","S":"AAPL","i":98124,"x":"V","p":151.0,"s":100,"c":["@"],"z":"C","t":"..."}]`
     - Status: `[{"T":"relay","msg":"upstream_connected"}]` or `[{"T":"relay","msg":"upstream_disconnected"}]`

2. **News WebSocket (`/news` or root)**:
   - Subscription: `{"action":"subscribe","news":["*"]}` or specific symbols `["AAPL", "TSLA"]`.
   - Outbound Feed Items:
     - `[{"T":"n","id":1001,"headline":"...","summary":"...","symbols":["AAPL"],"created_at":"...","sentiment":0.82}]`

3. **REST GET `/vix`**:
   - Header Requirement: `X-Relay-Token: <token>` (or `APCA-API-KEY-ID: <token>`).
   - Query Parameter Restriction: Query strings strictly forbidden. Returns `HTTP 400 {"error":"/vix takes no query parameters"}` if `?` is present.
   - Response Payload (HTTP 200):
     ```json
     {
       "state": "ready",
       "source": "Tastytrade/dxFeed spot VIX (Trade.time)",
       "value": 18.45,
       "asof": "2026-09-21T13:30:00Z",
       "received_at": "2026-09-21T13:30:01Z",
       "age_s": 1.2,
       "observations": [{"value": 18.45, "asof": "..."}]
     }
     ```

---

## 4. Test Fixtures Specifications

Fixtures reside under `/Users/mo/AutonomousDayTrader/tests/e2e/fixtures/`:
1. `bars_fixtures.json`: 1-minute OHLCV bars for AAPL, NVDA, TSLA, SPY across regular hours and opening ranges.
2. `quotes_fixtures.json`: NBBO top-of-book quotes covering tight ($0.01) to wide ($0.08) spreads and illiquid gaps.
3. `trades_fixtures.json`: Executed trade prints with timestamps, volume, exchange codes, and conditions.
4. `news_fixtures.json`: Benzinga news articles covering bullish catalysts, bearish catalysts, neutral wires, and contradiction events.
5. `vix_fixtures.json`: dxFeed prints for Low ($VIX = 13.5$), Normal ($VIX = 18.2$), Elevated ($VIX = 27.8$), and Crisis ($VIX = 38.5$).
6. `monday_open_session.json`: Complete sequenced multi-asset stream from 09:25:00 ET to 10:30:00 ET for Monday open simulation.

---

## 5. Execution Suite Structure

The test files are organized under `tests/e2e/`:
- `test_tier1_features.py`: CPM tests covering F1 through F21 ($\ge 5$ tests per feature, $\ge 105$ tests total).
- `test_tier2_boundary.py`: BVA edge tests covering numerical and temporal boundaries for F1 through F21 ($\ge 5$ tests per feature, $\ge 105$ tests total).
- `test_tier3_pairwise.py`: Orthogonal array pairwise interaction tests ($\ge 32$ multi-factor suites).
- `test_tier4_scenarios.py`: Full end-to-end operational scenarios ($\ge 6$ workflows).
- `runner.py`: Unified test runner supporting both `pytest` and standalone CLI execution with port auto-cleanup.

---

## 6. Process Hygiene & Port Allocation Mandate

In strict compliance with the Global Agent Operating Requirements:
1. **Zero Lingering Daemons**: Any server (mock relay on 8080/8765, trading backend on 8005, UI on 3005) started during testing or simulation **MUST** register POSIX signal handlers (`SIGINT`, `SIGTERM`, `EXIT`) and terminate cleanly.
2. **Deterministic Teardown Fixtures**: Async test runners must utilize `async with` context managers or `pytest` yield fixtures with guaranteed `server.close()` and socket release in `finally:` blocks.
3. **Port Audit Verification**: `scripts/verify_port_hygiene.sh` and test runner exit hooks query `lsof -tiTCP:<port>` to verify that ports 8080, 8005, and 3005 are released and 100% free before exiting.
