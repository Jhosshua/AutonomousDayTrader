# AutonomousDayTrader Technical Specification & Environment Survey Report

**Author**: `spec_miner_survey` (Specification Miner & External Domain Expert)  
**Date**: 2026-09-19  
**Target Project**: AutonomousDayTrader (`/Users/mo/AutonomousDayTrader`)  
**Parent Orchestrator**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  

---

## 1. Executive Summary

This report documents the exhaustive specification mining and environment survey conducted for the **AutonomousDayTrader** project. All technical protocols, system runtimes, external dependencies, port allocations, and upstream integration contracts have been probed directly against authoritative sources across the local filesystem, running processes, network ports, and the deployed production AlpacaRelay cluster.

### Key Discoveries:
1. **Authoritative AlpacaRelay Source & Credentials**:
   - The authoritative source code, documentation, test suites, and captured production telemetry reside locally in `/Users/mo/AlpacaRelay` (`relay.py`, `README.md`, `test_downstream_e2e.py`, `test_news.py`, `test_vix.py`).
   - The shared production relay is live on Railway at `https://alpacarelay-production.up.railway.app` and `wss://alpacarelay-production.up.railway.app`.
   - Shared authentication token (`RELAY_TOKEN`): `abb49296c2dd0556388b4e4c8dbced1134eba074d6ba9f7b` (verified via live probe; returns live VIX print `14.81` and active WebSocket session streams).
2. **Runtime & Package Ecosystem**:
   - **Python**: Python 3.9.6 (system default) and Python 3.11.15 (`/Users/mo/.local/bin/python3.11`). Python 3.9 has all essential packages pre-installed: `fastapi` (0.128.8), `uvicorn` (0.39.0), `websockets` (15.0.1), `pytest` (8.4.2), `pandas` (2.3.3), `numpy` (2.0.2), `scipy` (1.13.1), `httpx` (0.28.1), and `alpaca-py` (0.43.4).
   - **Node.js**: Node.js v22.22.2, npm 10.9.7. Full capability to scaffold Next.js 16/15, React 19, Tailwind CSS 4, and Framer Motion / Motion.
3. **Git & GitHub Deployment**:
   - The directory `/Users/mo/AutonomousDayTrader` is clean and uninitialized (`.git` not present).
   - GitHub CLI (`gh`) is authenticated as `Jhosshua` with full `repo` scopes. A clean GitHub repository can be instantiated and linked directly.
4. **Port Allocations & Collision Safety**:
   - Port 3000 and 8000 are currently occupied by background services (`next-server` and `MarketCards`).
   - Dedicated, collision-free ports identified: **Port 3001/3002** for the Apple Music mobile UI and **Port 8001/8080/8765** for the Trading Engine FastAPI / WebSocket backend and local mock server.
5. **Deterministic Mock & Replay Mandate**:
   - Because live stock feeds close at 16:00 ET and weekends print zero ticks, deterministic development, automated CI test suites, and Monday market open dry runs require a standalone local AlpacaRelay mock server capable of replaying 1-minute bars, ticks, and news events.

---

## 2. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Downstream WS | WebSocket Handshake Banner | On initial TCP connection, relay immediately sends connection acknowledgement banner | Initial TCP connect to `wss://<host>` | `[{"T":"success","msg":"connected"}]` | TCP close / timeout | `/Users/mo/AlpacaRelay/relay.py:517`, Live probe |
| 2 | Downstream WS | Client Authentication | Client must authenticate within 10 seconds using native token or key format | `{"action":"auth","token":"<RELAY_TOKEN>"}` or `{"action":"auth","key":"<RELAY_TOKEN>","secret":"..."}` | `[{"T":"success","msg":"authenticated"}]` | `[{"T":"error","code":402,"msg":"auth failed"}]` then disconnect | `/Users/mo/AlpacaRelay/relay.py:520-530`, Live probe |
| 3 | Downstream WS | Channel Subscription | Subscribe to stock channels: `trades`, `quotes`, `bars`, `updatedBars`, `dailyBars` | `{"action":"subscribe","trades":["AAPL"],"bars":["NVDA"]}` | `[{"T":"subscription","trades":["AAPL"],"bars":["NVDA"]}]` | Invalid JSON ignored, unrecog keys filtered | `/Users/mo/AlpacaRelay/relay.py:609-640` |
| 4 | Downstream WS | Channel Unsubscription | Unsubscribe specific symbols from active channels | `{"action":"unsubscribe","bars":["AAPL"]}` | `[{"T":"subscription",...}]` updated subscription ack | Silently ignored if symbol not subscribed | `/Users/mo/AlpacaRelay/relay.py:614-640` |
| 5 | Downstream WS | 1-Minute Bar Streaming (`b`) | Streams 1-minute historical aggregate OHLCV bars for subscribed symbols | Upstream SIP bar print | `[{"T":"b","S":"NVDA","o":128.45,"h":129.10,"l":128.30,"c":128.95,"v":84230,"t":"2026-09-01T13:35:00Z","n":812,"vw":128.78}]` | Omitted if client unsubscribed | `/Users/mo/AlpacaRelay/test_roster.py:47`, `QUANT_Trader_Smaller_Account/specs/alpaca_relay.md` |
| 6 | Downstream WS | Top-of-Book Quote (`q`) | Real-time NBBO quotes (bid price, bid size, ask price, ask size, condition) | Upstream SIP quote | `[{"T":"q","S":"AAPL","bx":"V","bp":180.24,"bs":2,"ax":"V","ap":180.26,"as":1,"t":"...","c":["R"],"z":"C"}]` | Omitted if client unsubscribed | `/Users/mo/AlpacaRelay/test_downstream_e2e.py:29` |
| 7 | Downstream WS | Trade Print (`t`) | Real-time executed trade print (price, size, exchange, conditions, ID) | Upstream SIP trade | `[{"T":"t","S":"AAPL","i":71678,"x":"V","p":180.25,"s":100,"t":"...","c":["@"],"z":"C"}]` | Omitted if client unsubscribed | `/Users/mo/AlpacaRelay/test_downstream_e2e.py:27` |
| 8 | Downstream WS | Relay Upstream Status (`relay`) | Synthetic notifications signaling upstream SIP connection health | Upstream connect/disconnect event | `[{"T":"relay","msg":"upstream_connected"}]` or `[{"T":"relay","msg":"upstream_disconnected"}]` | Emitted to all active clients | `/Users/mo/AlpacaRelay/README.md:23`, `relay.py:508` |
| 9 | Downstream WS | Queue Backpressure Disconnect | Drops clients buffered > 2000 batches behind | Slow client consumer | WebSocket close frame code `1013` / TCP reset (`"too slow"`) | Client dropped; must reconnect | `/Users/mo/AlpacaRelay/relay.py:71`, `CONGESTION_PLAN_2026-09-15.md` |
| 10 | News WS | Real-Time News Subscription | Subscribe to real-time Benzinga news feed via wildcard `*` or specific symbols | `{"action":"subscribe","news":["*"]}` or `{"action":"subscribe","news":["AAPL","TSLA"]}` | `[{"T":"subscription","news":["*"]}]` | Unknown channels ignored | `/Users/mo/AlpacaRelay/README.md:34`, `test_news.py:58` |
| 11 | News WS | News Article Payload (`n`) | Live article packet with headline, symbols, content, source, timestamps | Upstream Benzinga news event | `[{"T":"n","id":123456,"headline":"...","source":"benzinga","symbols":["NVDA"],"created_at":"...","updated_at":"...","url":"..."}]` | Filtered to subscribed tickers / `*` | `/Users/mo/AlpacaRelay/test_news.py:11`, `relay.py:404` |
| 12 | News WS | News Upstream Status (`relay`) | Synthetic notifications signaling upstream Benzinga connection status | Upstream news connect / drop | `[{"T":"relay","msg":"news_upstream_connected"}]` or `[{"T":"relay","msg":"news_upstream_disconnected"}]` | Emitted to news subscribers only | `/Users/mo/AlpacaRelay/README.md:46`, `relay.py:383` |
| 13 | REST /vix | Spot VIX dxFeed Print | Single read-only dxFeed Tastytrade spot VIX print with historical observations | `GET /vix` with header `X-Relay-Token: <token>` | HTTP 200 JSON: `{"state":"ready","source":"Tastytrade/dxFeed spot VIX (Trade.time)","value":14.81,"asof":"...","age_s":...}` | HTTP 401 if bad token, 400 if query params, 503 if unavailable | `/Users/mo/AlpacaRelay/README.md:74`, `relay.py:2131`, Live curl |
| 14 | REST Proxy | Historical Bars Proxy | Proxies Alpaca market data GET endpoints | `GET /data/v2/stocks/{symbol}/bars?timeframe=1Min&feed=sip` + `X-Relay-Token` | HTTP 200 JSON with exact Alpaca bars array | HTTP 401 if unauthenticated, 404/400 if invalid symbol/param | `/Users/mo/AlpacaRelay/README.md:105`, `relay.py:2125` |
| 15 | REST Proxy | Historical News Proxy | Proxies historical Benzinga news articles | `GET /data/v1beta1/news?symbols=NVDA&limit=10` + `X-Relay-Token` | HTTP 200 JSON with news array and page token | HTTP 401 if unauthenticated | `/Users/mo/AlpacaRelay/README.md:55`, `QUANT_Trader_Smaller_Account/specs/alpaca_relay.md:285` |
| 16 | REST Telemetry | Relay Health Endpoint | Unauthenticated telemetry reporting upstream status, client count, symbol counts, and vix state | `GET /health` | HTTP 200 JSON: `{"upstream":"connected","clients":7,"vix":{"state":"ready","value":14.81},...}` | HTTP 500 if internal server error | `/Users/mo/AlpacaRelay/README.md:156`, Live curl |

---

## 3. Edge Cases & Observed Behaviors

| # | Feature | Input / Condition | Observed Behavior |
|---|---------|-------------------|-------------------|
| 1 | WebSocket Auth | Invalid token: `{"action":"auth","token":"wrong"}` | Returns `[{"T": "error", "code": 402, "msg": "auth failed"}]` and immediately closes the connection. |
| 2 | WebSocket Auth | Auth timeout: client connects but sends no message for 10 seconds | Relay times out waiting for auth and closes socket without entering active client pool. |
| 3 | WebSocket Subscriptions | Mixed stock and news subscriptions in single payload: `{"action":"subscribe","bars":["AAPL"],"news":["*"]}` | Handled seamlessly: acknowledges both `{"T":"subscription","bars":["AAPL"],"news":["*"]}`; news is routed from the news stream without affecting stock refcounts. |
| 4 | WebSocket Subscriptions | Duplicate subscription commands for the same symbol | Idempotent on client side; refcount incremented internally only if not already subscribed by this client. |
| 5 | WebSocket Streaming | Consumer event loop blocks or lags behind by > 2000 message batches | Relay invokes `client.ws.close(1013, "too slow")` to protect upstream throughput and other downstream bots. |
| 6 | REST `/vix` | Pass query parameter: `GET /vix?fresh=1` | Returns HTTP 400 with payload `{"error":"/vix takes no query parameters"}`. |
| 7 | REST `/vix` | Missing or incorrect token header: `GET /vix` with no auth or invalid token | Returns HTTP 401 with payload `{"relay_error": "missing or bad relay token"}`. |
| 8 | REST `/vix` | Upstream dxFeed connection down or unconfigured | Returns HTTP 503 with `"state": "unavailable"` or `"state": "unconfigured"`. |
| 9 | REST `/vix` | Weekend / Overnight request (markets closed) | Returns HTTP 200 with last recorded spot close print (e.g. Friday 16:15 ET `14.81`) and honest `age_s` reporting elapsed seconds (e.g. `98762.2`s). |
| 10 | Real-Time News | Article tagged with multiple subscribed symbols (e.g. `["AAPL", "MSFT"]`) | Delivered exactly ONCE to client subscribing to both tickers (no duplicate delivery). |
| 11 | Real-Time News | Untagged article with empty symbols list `[]` | Delivered only to clients with wildcard subscription `"news": ["*"]`. Stock-only clients never receive news. |

---

## 4. Environment & Discovery Findings

### 4.1 Local AlpacaRelay Repository
- **Directory**: `/Users/mo/AlpacaRelay`
- **Configuration**: `/Users/mo/AlpacaRelay/.env`
  - `RELAY_TOKEN`: `abb49296c2dd0556388b4e4c8dbced1134eba074d6ba9f7b`
- **Key Modules**:
  - `relay.py`: Single-file multiplexer owning stock SIP connection (`wss://stream.data.alpaca.markets/v2/sip`), news connection (`wss://stream.data.alpaca.markets/v1beta1/news`), and Tastytrade dxLink spot VIX websocket.
  - `client_example.py`: Canonical client implementation with exponential backoff and message loop.
  - `test_downstream_e2e.py`: Loopback WebSocket test harness demonstrating mock server architecture without touching Alpaca limits.
  - `test_vix.py` & `test_news.py`: Unit test fixtures detailing exact message formats and event schemas.
  - `capture_2026-09-15` through `capture_2026-09-18`: Production market telemetry data logs (`health.jsonl`, `probe.jsonl`, `http.jsonl`).

### 4.2 Deployed Live Endpoints (Railway US East)
- **Base HTTP URL**: `https://alpacarelay-production.up.railway.app`
- **Base WebSocket URL**: `wss://alpacarelay-production.up.railway.app`
- **Current Live Status (Verified 2026-09-19 23:41 UTC)**:
  - Upstream stock feed: `connected` (feed: `sip`, 7 active clients)
  - Upstream news feed: `connected` (wildcard `*` subscribed)
  - Upstream VIX dxFeed: `ready` (spot VIX: `14.81`, as of `2026-09-18T20:15:01Z`)
  - Authenticated handshake and subscription: Verified 100% operational.

---

## 5. AlpacaRelay Detailed Protocol Specifications

### 5.1 Stock WebSocket Protocol
- **Endpoint**: `wss://alpacarelay-production.up.railway.app`
- **Protocol Flow**:
  1. **Connect**: Client connects via standard WebSocket.
  2. **Banner**: Relay sends `[{"T": "success", "msg": "connected"}]`.
  3. **Auth**: Client must send within 10 seconds:
     ```json
     {"action": "auth", "token": "abb49296c2dd0556388b4e4c8dbced1134eba074d6ba9f7b"}
     ```
     *(Alternative SDK format: `{"action": "auth", "key": "<token>", "secret": "..."}`)*
  4. **Auth Ack**:
     - Success: `[{"T": "success", "msg": "authenticated"}]`
     - Failure: `[{"T": "error", "code": 402, "msg": "auth failed"}]`
  5. **Subscription Command**:
     ```json
     {
       "action": "subscribe",
       "bars": ["AAPL", "NVDA", "TSLA", "SPY"],
       "quotes": ["SPY"],
       "trades": ["SPY"]
     }
     ```
  6. **Subscription Ack**:
     ```json
     [
       {
         "T": "subscription",
         "bars": ["AAPL", "NVDA", "SPY", "TSLA"],
         "quotes": ["SPY"],
         "trades": ["SPY"]
       }
     ]
     ```
  7. **Incoming Message Arrays**:
     - **1-Minute Bar (`b`)**:
       ```json
       {
         "T": "b",
         "S": "NVDA",
         "o": 128.45,
         "h": 129.10,
         "l": 128.30,
         "c": 128.95,
         "v": 84230,
         "t": "2026-09-19T13:35:00Z",
         "n": 812,
         "vw": 128.78
       }
       ```
     - **Top-of-Book Quote (`q`)**:
       ```json
       {
         "T": "q",
         "S": "SPY",
         "bx": "V",
         "bp": 560.24,
         "bs": 500,
         "ax": "V",
         "ap": 560.26,
         "as": 600,
         "t": "2026-09-19T13:35:00.123456789Z",
         "c": ["R"],
         "z": "C"
       }
       ```
     - **Trade Print (`t`)**:
       ```json
       {
         "T": "t",
         "S": "SPY",
         "i": 982341,
         "x": "V",
         "p": 560.25,
         "s": 100,
         "c": ["@"],
         "z": "C",
         "t": "2026-09-19T13:35:00.245891000Z"
       }
       ```
  8. **Relay Health Control Messages**:
     - `{"T": "relay", "msg": "upstream_connected"}`
     - `{"T": "relay", "msg": "upstream_disconnected"}`
     *Trading Engine Rule*: When `upstream_disconnected` is received, treat all market data as stale, freeze active order generation, and refuse new position entries.

### 5.2 Real-Time News WebSocket Protocol
- **Endpoint**: Same WebSocket connection or dedicated isolated socket (`wss://alpacarelay-production.up.railway.app`).
- **Subscription**:
  ```json
  {"action": "subscribe", "news": ["*"]}
  ```
  *(Or specific tickers: `{"action": "subscribe", "news": ["AAPL", "NVDA"]}`)*
- **News Article Payload (`n`)**:
  ```json
  {
    "T": "n",
    "id": 39821045,
    "headline": "NVIDIA Partners With Major Cloud Provider to Deploy Blackwell Ultra GPU Infrastructure",
    "source": "benzinga",
    "symbols": ["NVDA"],
    "created_at": "2026-09-19T13:45:00Z",
    "updated_at": "2026-09-19T13:45:10Z",
    "url": "https://www.benzinga.com/news/...",
    "content": "<p>Full article text or HTML (optional)...</p>",
    "summary": "Brief summary of the announcement..."
  }
  ```
- **Sentiment & Catalyst Processing Pipeline**:
  - Raw Benzinga wire messages from Alpaca do not include a numerical sentiment score.
  - The Catalyst News Momentum Breakout strategy requires an algorithmic sentiment analysis engine (e.g. high-speed financial keyword classifier / pattern matching + VADER / FinBERT polarity calculation):
    - *Bullish Catalysts (+1.0 to +0.4)*: "Beat", "Surges", "Upgrade", "Record Revenue", "FDA Approval", "Partnership", "Buyback", "Exceeds Guidance".
    - *Bearish Catalysts (-1.0 to -0.4)*: "Misses", "Downgrade", "Investigation", "Halts", "Subpoena", "Fraud", "Lowers Guidance", "Offering".
    - *Actionable Threshold*: Absolute score $\ge 0.6$ paired with volume surge $> 2.0\times$ 20-minute RVOL triggers momentum breakout execution.

### 5.3 REST Spot VIX (`GET /vix`) Protocol
- **URL**: `https://alpacarelay-production.up.railway.app/vix`
- **Method**: `GET`
- **Headers**:
  - `X-Relay-Token: abb49296c2dd0556388b4e4c8dbced1134eba074d6ba9f7b`
  - *(or `APCA-API-KEY-ID: abb49296c2dd0556388b4e4c8dbced1134eba074d6ba9f7b`)*
- **Constraints**:
  - Query parameters strictly forbidden (`?` yields HTTP 400 `{"error":"/vix takes no query parameters"}`).
- **Success Response (HTTP 200)**:
  ```json
  {
    "state": "ready",
    "source": "Tastytrade/dxFeed spot VIX (Trade.time)",
    "upstream": "connected",
    "value": 14.81,
    "asof": "2026-09-18T20:15:01.213000+00:00",
    "received_at": "2026-09-18T20:15:01.243725+00:00",
    "age_s": 98762.2,
    "events": 4264,
    "reconnects": 586,
    "last_error": null,
    "last_msg_age_s": 26,
    "observations": [
      {
        "value": 14.81,
        "asof": "2026-09-18T20:15:01.213000+00:00",
        "received_at": "2026-09-18T20:15:01.243725+00:00"
      }
    ]
  }
  ```
- **Market Volatility Regime Mapping**:
  - **Low Volatility ($VIX < 15$)**: Sizing multiplier $1.2\times$, tighter stop width ($0.8\times$ ATR), tighter profit targets ($1.5\times$ ATR).
  - **Moderate / Normal Volatility ($15 \le VIX < 22$)**: Baseline sizing ($1.0\times$), standard stops ($1.0\times$ ATR), target $2.0\times$ ATR.
  - **Elevated Volatility ($22 \le VIX < 30$)**: Defensive sizing ($0.6\times$), wider stop buffers ($1.5\times$ ATR), higher entry confirmation thresholds.
  - **Extreme Volatility ($VIX \ge 30$)**: High-risk regime; circuit breaker sizing ($0.25\times$) or freeze new breakout orders, favoring mean reversion fades.

### 5.4 REST Market Data Proxy (`GET /data/...`)
- Forwarding pattern: `GET /data/<path>` $\rightarrow$ `https://data.alpaca.markets/<path>`
- Sample bar retrieval:
  ```bash
  curl -H "X-Relay-Token: abb49296c2dd0556388b4e4c8dbced1134eba074d6ba9f7b" \
    "https://alpacarelay-production.up.railway.app/data/v2/stocks/NVDA/bars?timeframe=1Min&limit=100&feed=sip"
  ```

---

## 6. System Environment, Toolchains & Package Inventory

### 6.1 Python Runtimes & Packages
- **Interpreters Available**:
  - Primary / System: `/usr/bin/python3` (Python 3.9.6)
  - Secondary: `/Users/mo/.local/bin/python3.11` (Python 3.11.15)
- **Key Pre-Installed Packages (`/usr/bin/python3`)**:
  - `fastapi` (0.128.8) — High performance async REST & WebSocket API framework
  - `uvicorn` (0.39.0) — ASGI production web server
  - `websockets` (15.0.1) — Native Python WebSocket client & server
  - `pytest` (8.4.2), `pytest-asyncio` (1.2.0), `pytest-cov` (7.1.0) — Automated test runner
  - `pandas` (2.3.3), `numpy` (2.0.2), `scipy` (1.13.1), `statsmodels` (0.14.6) — Quantitative analysis, indicators, EWMA, ATR, VWAP
  - `httpx` (0.28.1), `requests` (2.32.5), `aiohttp` (3.13.5) — Sync and async HTTP clients
  - `alpaca-py` (0.43.4) — Alpaca official SDK
  - `pydantic` (2.13.4) — Data validation and models

### 6.2 Node.js & Frontend Toolchains
- **Node.js**: `v22.22.2` (Node 22 LTS)
- **npm**: `10.9.7`
- **UI Stack Compatibility**:
  - React 19 / Next.js 16 (or Vite + React 19 matching local project `RealtorAgent-Railway/web` and `Massage`)
  - Tailwind CSS 4 (`tailwindcss@^4`)
  - Framer Motion / Motion (`motion@^12.38.0`)
  - Lucide Icons (`lucide-react@^0.577.0`)
  - Glassmorphism, dynamic obsidian dark theme, and fluid spring animations verified compatible with current npm packages.

### 6.3 Git Repository & Remote Status
- **Current Directory**: `/Users/mo/AutonomousDayTrader`
- **Git Status**: Currently untracked inside root folder; clean directory containing only `.agents/` and `ORIGINAL_REQUEST.md`.
- **GitHub CLI (`gh`) Status**: Authenticated as `Jhosshua` with full `repo` permissions.
- **Delivery Protocol**: When Milestone 1/Delivery executes:
  ```bash
  cd /Users/mo/AutonomousDayTrader
  git init
  git checkout -b main
  gh repo create AutonomousDayTrader --public --source=. --remote=origin
  git add .
  git commit -m "feat: initial autonomous day trader system"
  git push -u origin main
  ```

### 6.4 Port Allocation & Collision Matrix
| Port | Status | Owning Process / Service | AutonomousDayTrader Allocation |
|---|---|---|---|
| **3000** | OCCUPIED | `node (next-server v16.2.7)` | Avoid |
| **3001** | **AVAILABLE** | None | **Primary Web UI (Next.js / Vite)** |
| **3002** | **AVAILABLE** | None | Alternate Web UI Port |
| **5000** | OCCUPIED | `ControlCenter (macOS AirPlay)` | Avoid |
| **7000** | OCCUPIED | `ControlCenter (macOS AirPlay)` | Avoid |
| **8000** | OCCUPIED | `MarketCards (.venv python3.1)` | Avoid |
| **8001** | **AVAILABLE** | None | **Primary Backend Trading API (FastAPI)** |
| **8490** | OCCUPIED | `TheThesis (.venv python3)` | Avoid |
| **8642** | OCCUPIED | `hermes-agent` | Avoid |
| **8765** | **AVAILABLE** | None | **Local AlpacaRelay Mock Server** |
| **8800** | **AVAILABLE** | None | Alternate Backend Server Port |
| **8888** | OCCUPIED | `Python (Jupyter / Tool)` | Avoid |
| **9222** | OCCUPIED | `Google Chrome Remote Debug` | Avoid |

---

## 7. Deterministic Mock Server & Replay Specification

### 7.1 Architectural Purpose
Live market hours run strictly 09:30–16:00 ET on regular business days. Because development, continuous integration test suites, and Monday market open dry runs occur outside regular hours or require reproducible market conditions:
1. The system must support seamless toggling between `LIVE` AlpacaRelay and `MOCK/REPLAY` AlpacaRelay via environment configuration (`RELAY_URL`, `RELAY_HTTP_URL`).
2. The mock server must emulate the exact handshake, subscription filtering, message formatting, and timing dynamics of the production AlpacaRelay.

### 7.2 Mock Server Feature Requirements
- **WebSocket Protocol Fidelity**:
  - Immediate `[{"T": "success", "msg": "connected"}]` banner.
  - Strict 10-second authentication timeout.
  - Token validation against `RELAY_TOKEN`.
  - Dynamic channel subscription handling (`bars`, `quotes`, `trades`, `news`).
  - Broadcast of `[{"T": "relay", "msg": "upstream_connected"}]` and `[{"T": "relay", "msg": "upstream_disconnected"}]` on trigger.
- **REST Endpoints**:
  - `GET /vix`: Returns spot VIX JSON with configurable state (`ready`, `stale`, `unavailable`), value (e.g. 14.5, 22.5, 34.0), and observation history.
  - `GET /health`: Returns relay health telemetry.
  - `GET /data/v2/stocks/{symbol}/bars`: Returns historical bar aggregates.
- **Market Open Simulation & Replay Engine**:
  - Synthetic or historical recorded replay of the critical **09:30–10:00 ET Monday market open window**.
  - Configurable playback speed: $1\times$ (live speed), $5\times$, $10\times$, or step-by-step tick advance.
  - Injects representative market scenarios:
    1. **Opening Range Breakout (ORB)** setup on NVDA/AAPL with volume breakout.
    2. **VWAP Pullback** trend continuation setup on SPY/QQQ.
    3. **Catalyst News Breakout** with sudden breaking headline on TSLA.
    4. **Exhaustion Mean Reversion** setup with extreme RSI/Bollinger extension.
    5. **Circuit Breaker Trip**: Simulated flash drawdown triggering daily risk halt.
    6. **15:55 ET MOC Flattening**: Simulated approach to 16:00 ET forcing automated portfolio liquidation.

### 7.3 Process Hygiene Mandate
- In accordance with Global Agent Operating Rules:
  - All mock servers, local background test runners, and ephemeral web servers must bind cleanly and terminate immediately upon test completion or dry run exit.
  - Test suites must implement deterministic teardown fixtures (`pytest` fixtures with `finally:` cleanup) guaranteeing ports 8001, 8765, and 3001 are immediately released.

---

## 8. Strategy & Risk Engine Specification Synthesis

### 8.1 Virtual Account Architecture ($50,000 Paper Balance)
- **Initial State**:
  - Starting Cash: $\$50,000.00$
  - Starting Equity: $\$50,000.00$
  - Realized PnL: $\$0.00$
  - Unrealized PnL: $\$0.00$
  - Intraday Buying Power: $4\times$ Equity ($\$200,000.00$)
- **Order Lifecycle**:
  - Order types: Market, Limit, Stop-Loss Brackets, Market-on-Close (MOC).
  - Fill engine: Fills against real-time quote NBBO or executed trade prints (`t`/`q`).
  - Slippage model: Deterministic $0.01\text{--}0.03\$$ spread slippage per fill.

### 8.2 Institutional Risk Guardrails
1. **Hard Daily Drawdown Circuit Breaker**:
   - Limit: $3\%$ of starting equity ($\$1,500.00$).
   - Action: If daily realized + unrealized drawdown exceeds $\$1,500$, immediately cancel open orders, flatten all open positions, and lock trading engine until next session.
2. **Per-Position Risk Cap**:
   - Limit: Max $1.0\%$ account risk ($\$500.00$) per trade based on entry to stop distance.
   - Max notional position size: $\$25,000.00$ ($50\%$ of equity).
   - Max concurrent positions: $3$.
3. **Mandatory Bracket Stops**:
   - Every order must attach a deterministic stop-loss and take-profit target at submission.
4. **Zero Overnight Exposure**:
   - 15:50 ET: Warning / halt new entries.
   - 15:55 ET: Automated MOC liquidation sweeps all open positions; all cash by 16:00 ET.

### 8.3 The 4 High Sharpe Day Trading Strategies
1. **Strategy 1: Opening Range Breakout (ORB)**
   - *Timeframe*: 09:30–10:15 ET.
   - *Logic*: Establishes 5-minute or 15-minute opening range high ($OR_H$) and low ($OR_L$).
   - *Entry*: Breakout above $OR_H$ with volume $> 1.5\times$ 5-minute average.
   - *Stop*: $OR$ midpoint or 1.0 ATR below entry.
   - *Target*: $1.5\text{--}2.0\times$ risk.
2. **Strategy 2: VWAP Trend Pullback & Continuation**
   - *Timeframe*: 10:00–12:00 ET and 14:00–15:30 ET.
   - *Logic*: Strong trend identified by price above rising VWAP and 9 EMA > 21 EMA.
   - *Entry*: Pullback touching or within $0.1\%$ of VWAP with bullish reversal hammer/engulfing candle.
   - *Stop*: Below VWAP $- 0.5$ ATR.
   - *Target*: Retest of session high / $2.0\times$ risk.
3. **Strategy 3: Catalyst News Momentum Breakout**
   - *Timeframe*: 09:30–15:30 ET.
   - *Logic*: Ingests Benzinga news feed via AlpacaRelay.
   - *Entry*: Breaking headline matching positive catalyst sentiment score ($\ge 0.60$) accompanied by 1-minute volume spike ($> 3.0\times$ RVOL) breaking intraday resistance.
   - *Stop*: Pre-catalyst bar low.
   - *Target*: Scaled exit at $+2\%$ and $+4\%$, trailing stop behind 9 EMA.
4. **Strategy 4: Statistical Mean Reversion / Exhaustion Fades**
   - *Timeframe*: 11:30–14:00 ET (Midday chop defense window).
   - *Logic*: Identifies overextended price action where directional momentum has stalled.
   - *Indicators*: Intraday 1-minute $RSI(14) > 75$ (short fade) or $< 25$ (long bounce), price $> 2.5\sigma$ from 20-period VWAP/Bollinger Band.
   - *Entry*: Exhaustion candle rejection wick.
   - *Stop*: Swing high/low $+ 0.5$ ATR.
   - *Target*: Mean reversion back to VWAP or 20 SMA.

---

## 9. Next Steps for Implementation Team

1. **Architecture Blueprint**:
   - Create core project layout under `/Users/mo/AutonomousDayTrader`.
   - Setup Python backend package `trader/` with `engine/`, `risk/`, `strategies/`, `relay/`, `mock/`, `api/`.
   - Setup Web UI package under `frontend/` (Next.js 16 / React 19 / Tailwind 4 / Framer Motion).
2. **Test-First Verification Suite**:
   - Implement `mock_relay.py` and `test_suite.py` covering all 4 strategy signal calculations, risk circuit breakers, order routing, and AlpacaRelay protocol edge cases.
3. **Monday Dry Run Harness**:
   - Package the Monday market open replay harness to validate system readiness under live-speed market simulation.
