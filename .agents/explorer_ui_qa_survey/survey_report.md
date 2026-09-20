# Comprehensive Architectural Survey & Engineering Specification: Apple Music Mobile UI, E2E Testing Framework & Monday Dry Run Simulation

**Document ID**: `ADT-ARCH-SURVEY-UI-QA-001`  
**Agent**: `explorer_ui_qa_survey`  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/explorer_ui_qa_survey`  
**Target Milestone**: Milestone 3 (UI Architecture), Milestone 4 (E2E Testing), Milestone 5 (Monday Simulation & Hygiene)  
**Date**: 2026-09-19  

---

## 1. Executive Architecture & System Topology

### 1.1 Mission & Architectural Principles
The AutonomousDayTrader project requires an institutional-grade, deterministic intraday trading engine operating on a virtual $50,000 paper trading account across 4 dynamic strategies, combined with an **Apple Music mobile-inspired user interface** and a **rigorous multi-tier testing and simulation architecture**.

To satisfy these dual requirements, the system is architected as an event-driven decoupled topology:
1. **Trading Core & Market Signal Ingestion Engine (Python 3.12 / uv)**: Runs the order state machine, virtual account ledger, risk circuit breakers, and 4 day trading strategies. Exposes an ultra-low-latency WebSocket and REST API.
2. **Apple Music Mobile UI (Next.js 15 App Router, React 19, Tailwind CSS, Framer Motion)**: A mobile-first, high-framerate (60fps) responsive interface styled after Apple Music's iOS design language (obsidian dark, dynamic glassmorphism, fluid physics, and momentum-reactive ambient glowing gradients).
3. **Market Data Replay & Synthetic Simulation Engine**: Replays historical captures or generates synthetic 1-minute bars, quotes, dxFeed VIX prints, and Benzinga news feeds mimicking AlpacaRelay at 1x to 10x accelerated clock speeds.
4. **Opaque-Box E2E Testing Harness & Monday Dry Run Runner**: Implements Tiers 1–4 software testing methodology and executes a full mock Monday 09:25–10:30 ET session to verify operational readiness.

### 1.2 System Topology & Process Boundaries

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      AlpacaRelay (Live) or                             │
 │            Market Data Replay / Simulation Engine (Offline)            │
 │   - Stock WS: 1-min bars ('b'), quotes ('q'), trades ('t')             │
 │   - News WS: Benzinga real-time news articles ('n')                    │
 │   - REST: GET /vix (dxFeed spot VIX), GET /data/v2/stocks/.../bars     │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │            AutonomousDayTrader Core Engine (Port: 8005)                │
 │  - Ingestion Client (AlpacaRelay protocol parser)                      │
 │  - 4 Strategies: ORB, VWAP Pullback, News Momentum, Mean Reversion    │
 │  - Risk Engine: Daily $1,500 circuit breaker, 1-2% position sizing     │
 │  - Paper Ledger: $50,000 initial, mark-to-market, 15:55 ET EOD flatten│
 │  - UI Streaming Server: FastAPI WebSocket (ws://127.0.0.1:8005/ws/ui)  │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ Bidirectional WebSocket
                                     │ & REST Control APIs
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │           Apple Music Mobile UI Frontend (Port: 3005)                  │
 │  - Next.js 15 App Router + React 19 + Tailwind CSS + Framer Motion     │
 │  - Ambient Momentum Glow (Responsive to PnL & VIX)                     │
 │  - "Playlists / Albums" Strategy Cards Carousel                        │
 │  - "Now Playing" Collapsible / Expandable Bottom Tray & Controls       │
 │  - Real-time TradingView Lightweight-Charts Intraday Candlesticks      │
 └────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Port Allocation & Host Collision Defense
Investigation of existing active processes on the host Mac revealed active daemons:
- `node` running on Port `3000` (PID 793, `Massage` app)
- `python3.1` running on Port `8000` (PID 17317, `MarketCards` app)
- `python3.1` running on Port `8490` (PID 77838, `TheThesis` app)
- `Google Chrome` remote debugging on Port `9222` (PID 12407)

**Mandatory Port Configuration**:
- UI Application: **Port `3005`** (`NEXT_PUBLIC_PORT=3005`)
- Backend Engine / WebSocket: **Port `8005`** (`PORT=8005`)
- Simulation / Replay Mock: **Port `8015`** (`MOCK_PORT=8015`)

This guarantees zero port collisions with running local processes and fulfills the Process Hygiene Mandate.

---

## 2. Apple Music Mobile-Inspired UI System Architecture

### 2.1 Aesthetic Foundation: The Apple Music iOS Design Language
Apple Music's mobile user interface represents the pinnacle of tactile, dark-mode, spatial user experience. Translating this to an intraday trading system delivers an aesthetic that is both deeply immersive and functionally ergonomic:

```
+-------------------------------------------------------------+
|  [Dynamic Island / Header]      09:35:12 ET  [VIX 18.2 Normal]
|  PORTFOLIO VALUE
|  $51,240.50                      ▲ +$1,240.50 (+2.48%) Today
|  Buying Power: $198,450.00       Active Positions: 1
|-------------------------------------------------------------|
|  STRATEGY PLAYLISTS (Horizontal Carousel)                  |
|  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       |
|  │ [Art: Sun]   │  │ [Art: Wave]  │  │ [Art: Flash] │ ...   |
|  │ ORB Breakout │  │ VWAP Pullback│  │ News Catalyst│       |
|  │ ● LIVE       │  │ ● LIVE       │  │ ⏸ PAUSED     │       |
|  │ Win: 68.4%   │  │ Win: 59.2%   │  │ Win: 71.0%   │       |
|  │ PnL: +$680   │  │ PnL: +$340   │  │ PnL: $0.00   │       |
|  │ Shp: 2.41    │  │ Shp: 1.88    │  │ Shp: 2.15    │       |
|  └──────────────┘  └──────────────┘  └──────────────┘       |
|-------------------------------------------------------------|
|  TODAY'S SIGNALS & AUDIT LOG                                |
|  09:35:02  NVDA ORB 5-min High Break ($124.50)  -> FILLED   |
|  09:30:00  Market Open Bell Volatility Flush    -> PASS     |
|-------------------------------------------------------------|
|  [NOW PLAYING MINI-BAR] (Docked Bottom Tray)               |
|  [|||] NVDA Long 150sh  +$382.50 (+2.05%)  [Tighten] [Flat] |
+-------------------------------------------------------------+
               ▲ Swipe / Tap Up
+-------------------------------------------------------------+
|  [NOW PLAYING FULL SCREEN EXPANDED VIEW]                    |
|  ━━━ (Drag to dismiss down)                                 |
|                                                             |
|  NVDA                                      ▲ +$382.50       |
|  NVIDIA Corp • Long 150 Shares             Entry: $124.50   |
|                                                             |
|  ┌────────────────────────────────────────────────────────┐ |
|  │ [Interactive Candlestick / Line Chart]                 │ |
|  │ --- Take Profit: $127.50 (+$450.00)                    │ |
|  │ ════ Current: $127.05 ════════════════════════════════ │ |
|  │ --- Trailing Stop: $125.10 (+$90.00 Lock)              │ |
|  │ --- Hard Stop: $123.75 (-$112.50 Max Risk)             │ |
|  └────────────────────────────────────────────────────────┘ |
|                                                             |
|  POSITION STATS & BRACKETS                                  |
|  R:R 1:3.4 | ATR $1.85 | Unrealized R: +2.1R | Hold: 18m    |
|                                                             |
|  MANUAL CONTROLS (Glass Buttons)                            |
|  [ Tighten Stop to Breakeven ]   [ Scale Out 50% ]          |
|  [             EMERGENCY FLATTEN POSITION                 ] |
+-------------------------------------------------------------+
```

### 2.2 Design Tokens & Visual Styling Specifications

| Design Element | Token / Spec | Tailwind Utility Class / CSS | Description / Functional Rationale |
|---|---|---|---|
| **True Black Base** | `#000000` | `bg-black` | Pure pitch black foundation; maximizes OLED contrast and power efficiency on mobile screens. |
| **Obsidian Dark Surface** | `#0a0a0c` | `bg-[#0a0a0c]` | Subtle elevated dark surface for page containers and base panels. |
| **Glassmorphism Card** | `rgba(18, 18, 24, 0.65)` | `bg-white/[0.04] backdrop-blur-xl border border-white/[0.08]` | Translucent glass layering providing spatial depth without obscuring underlying ambient blurs. |
| **Positive Momentum** | `#30d158` | `text-[#30d158] bg-[#30d158]/15 border-[#30d158]/30` | Apple Music bright emerald green for winning PnL, profit targets, and live active badges. |
| **Drawdown / Risk** | `#ff453a` | `text-[#ff453a] bg-[#ff453a]/15 border-[#ff453a]/30` | Apple Music vivid crimson for negative PnL, stop-loss lines, and circuit-breaker alerts. |
| **Neutral / Standby** | `#5e5ce6` / `#0a84ff` | `text-[#5e5ce6] bg-[#5e5ce6]/15 border-[#5e5ce6]/30` | Royal violet & electric blue for breakeven, standby states, and pre-market indicators. |
| **Typography** | `SF Pro Display`, `Inter` | `font-sans tracking-tight` | Crisp Apple typography with numeric font features (`tabular-nums` for rock-solid ticker numbers). |

### 2.3 Animated Background Gradient Blurs (Album Artwork Ambient Glow)
Apple Music's defining visual signature is the fluid ambient colored background glow that mirrors the currently playing album's artwork. In AutonomousDayTrader, this ambient glow is dynamically driven by **Portfolio Momentum and Market Regime**:

```typescript
// Algorithm for Dynamic Momentum Background Glow
type PortfolioGlowState = {
  primaryColor: string;
  secondaryColor: string;
  ambientIntensity: number; // 0.15 to 0.45
};

export function getMomentumGlow(dailyPnl: number, vix: number, isHalted: boolean): PortfolioGlowState {
  if (isHalted) {
    // Red alert strobe / lock
    return { primaryColor: "rgba(255, 69, 58, 0.4)", secondaryColor: "rgba(180, 20, 20, 0.3)", ambientIntensity: 0.45 };
  }
  if (dailyPnl > 500) {
    // High profit momentum: Vibrant emerald + electric cyan
    return { primaryColor: "rgba(48, 209, 88, 0.32)", secondaryColor: "rgba(100, 210, 255, 0.25)", ambientIntensity: 0.35 };
  } else if (dailyPnl > 0) {
    // Moderate profit: Soft emerald + indigo
    return { primaryColor: "rgba(48, 209, 88, 0.20)", secondaryColor: "rgba(94, 92, 230, 0.20)", ambientIntensity: 0.25 };
  } else if (dailyPnl < -500) {
    // Deep drawdown: Warning amber + crimson
    return { primaryColor: "rgba(255, 69, 58, 0.35)", secondaryColor: "rgba(255, 159, 10, 0.25)", ambientIntensity: 0.35 };
  } else if (dailyPnl < 0) {
    // Mild drawdown: Crimson tint
    return { primaryColor: "rgba(255, 69, 58, 0.20)", secondaryColor: "rgba(94, 92, 230, 0.15)", ambientIntensity: 0.20 };
  }
  // Neutral: Violet / Deep Blue
  return { primaryColor: "rgba(94, 92, 230, 0.22)", secondaryColor: "rgba(10, 132, 255, 0.18)", ambientIntensity: 0.20 };
}
```

The ambient background is rendered using multiple overlapping SVG/CSS blurred orbs positioned at the top-left and bottom-right corners:
```tsx
<motion.div
  className="fixed inset-0 pointer-events-none -z-10 overflow-hidden"
  animate={{ opacity: glow.ambientIntensity }}
  transition={{ duration: 1.2, ease: "easeInOut" }}
>
  <motion.div
    className="absolute -top-32 -left-32 w-96 h-96 rounded-full blur-[100px]"
    animate={{ background: `radial-gradient(circle, ${glow.primaryColor} 0%, transparent 70%)` }}
    transition={{ duration: 1.5 }}
  />
  <motion.div
    className="absolute top-1/2 -right-32 w-96 h-96 rounded-full blur-[120px]"
    animate={{ background: `radial-gradient(circle, ${glow.secondaryColor} 0%, transparent 70%)` }}
    transition={{ duration: 1.5 }}
  />
</motion.div>
```

### 2.4 Strategy "Playlists / Albums" Carousel
The 4 day trading strategies are presented as curated "Albums / Playlists":
1. **Playlist 1: Opening Range Breakout (ORB)**
   - *Theme Artwork*: Geometric sunrise vectors in golden amber and emerald.
   - *Subtitle*: "5-Min / 15-Min High-Low Volatility Expansion"
   - *Key Metrics*: Win Rate (`68.4%`), Sharpe (`2.41`), Today's Trades (`3/5`), Daily PnL (`+$680.00`).
   - *Status Badge*: `LIVE` (pulsing green dot).
2. **Playlist 2: VWAP Trend Pullback & Continuation**
   - *Theme Artwork*: Fluid ribbon curve intersecting standard deviation bands.
   - *Subtitle*: "Intraday Institutional Trend Following"
   - *Key Metrics*: Win Rate (`59.2%`), Sharpe (`1.88`), Today's Trades (`2/4`), Daily PnL (`+$340.00`).
   - *Status Badge*: `LIVE`.
3. **Playlist 3: Catalyst News Momentum Breakout**
   - *Theme Artwork*: Electric sonic pulse waves symbolizing breaking headlines.
   - *Subtitle*: "Real-Time Benzinga Sentiment Surges"
   - *Key Metrics*: Win Rate (`71.0%`), Sharpe (`2.15`), Today's Trades (`1/3`), Daily PnL (`+$420.00`).
   - *Status Badge*: `STANDBY` (monitoring feeds).
4. **Playlist 4: Statistical Mean Reversion / Exhaustion Fades**
   - *Theme Artwork*: Dual oscillating sine waves decaying into equilibrium.
   - *Subtitle*: "Overextended Bollinger / RSI Fades"
   - *Key Metrics*: Win Rate (`54.5%`), Sharpe (`1.65`), Today's Trades (`0/2`), Daily PnL (`$0.00`).
   - *Status Badge*: `COOLDOWN` (waiting for extreme ATR deviation).

**Interaction Spec**:
- Horizontal snap-scrolling on mobile devices (`overflow-x-auto snap-x snap-mandatory scrollbar-none flex space-x-4 px-4`).
- Touch-responsive 3D tilt and spring press (`whileTap={{ scale: 0.97 }}`).
- Clicking any strategy card filters the active execution stream or opens the Strategy Deep Dive inspector.

### 2.5 "Now Playing" Collapsible / Expandable Bottom Tray
The "Now Playing" bottom tray is the centerpiece of the interaction design, mimicking the Apple Music music player tray:

#### A. Collapsed Miniature Bar (Persistent Dock)
- **Position**: Floats 16px above the bottom screen boundary (or bottom tab bar) with a subtle bottom margin to clear iOS Home Bar safe areas (`bottom-4 left-4 right-4 z-40`).
- **Surface**: `backdrop-blur-2xl bg-black/75 border border-white/10 shadow-2xl rounded-2xl p-3 flex items-center justify-between`.
- **Contents**:
  - *Left*: Album-art thumbnail or ticker avatar (e.g. `NVDA` pill), ticker symbol, direction (`LONG 150sh`), entry price (`@ $124.50`).
  - *Center*: Live Unrealized PnL badge with animated number flip (`+$382.50 (+2.05%)`).
  - *Right Quick Actions*:
    - **Tighten Stop** (Shield button): Sets trailing stop to breakeven or 1 ATR lock with one tap.
    - **Flatten** (Square Stop icon): Liquidates the primary position immediately.
- **Gesture**: Tap anywhere on the bar or swipe up to expand to the full view.

#### B. Expanded Full-Screen Sheet
- **Animation**: Smooth spring transition expanding from height `64px` to `100vh` (or `92vh` modal sheet):
  ```typescript
  const sheetVariants = {
    collapsed: { y: 0, opacity: 1, scale: 1 },
    expanded: { y: 0, opacity: 1, scale: 1 },
  };
  const springTransition = { type: "spring", stiffness: 350, damping: 32, mass: 0.8 };
  ```
- **Top Bar**: Grab bar (`w-12 h-1.5 bg-white/20 rounded-full mx-auto`) supporting downward swipe drag-to-dismiss (`drag="y"`, `dragConstraints={{ top: 0 }}`, dismiss threshold `120px`).
- **Hero Header**: Large asset title: "NVDA • NVIDIA Corporation", position badge ("LONG 150 SHARES • ENTRY $124.50"), live market price ("$127.05").
- **Live Candlestick & Level Chart**:
  - Rendered using TradingView Lightweight-Charts with dark obsidian theme.
  - Horizontal price lines overlay:
    - **Green Dashed**: Take Profit 1 ($127.50) & Take Profit 2 ($129.00)
    - **Red Dashed**: Hard Stop-Loss ($123.75) & Trailing Stop ($125.10)
    - **Cyan Solid**: Entry Price ($124.50)
- **Execution Log ("Lyrics View")**:
  - Chronological real-time audit feed:
    - `09:35:01` - ORB 5-min breakout detected: $124.50 breached on 3.2x vol.
    - `09:35:02` - Risk filter passed (Account risk $112.50 = 0.22% equity).
    - `09:35:03` - Paper Market Order BUY 150 NVDA filled @ $124.50.
    - `09:42:10` - Trailing stop advanced from $123.75 to $125.10 (+0.5R locked).
- **Manual Intervention Console**:
  - Three distinct high-contrast glass buttons:
    1. `Tighten Stop to Breakeven` (Amber glass)
    2. `Take Partial Profit (50%)` (Emerald glass)
    3. `Emergency Flatten All` (Crimson glass, requires sliding confirmation to prevent accidental taps).

### 2.6 Real-Time Streaming & State Synchronization Protocol
To prevent any full-page reload and ensure 60fps rendering, state updates are streamed over a persistent WebSocket connection:
- **WebSocket Endpoint**: `ws://127.0.0.1:8005/ws/ui`
- **Reconnection Architecture**:
  - Reconnect on disconnect with exponential backoff: `min(1000 * 2^attempt, 10000)ms`.
  - Heartbeat ping sent every 15 seconds; disconnect triggered if pong missing after 5 seconds.
- **Message Types**:
  - `SNAPSHOT`: Full portfolio, open positions, strategy states, and recent orders sent upon connection.
  - `TICK`: Lightweight price and PnL tick for active tickers (`{ "S": "NVDA", "p": 127.05, "pnl": 382.50 }`).
  - `POSITION_CHANGE`: Entry, scale, stop update, or close.
  - `STRATEGY_UPDATE`: Win rate, Sharpe, status updates.
  - `CIRCUIT_BREAKER`: High-priority alert when daily loss threshold or VIX freeze is engaged.

---

## 3. Comprehensive Testing Architecture (Tiers 1–4 Methodology)

The AutonomousDayTrader verification framework adheres to an **opaque-box E2E testing methodology** structured across 4 distinct tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 1: Category-Partition Method (CPM)                    │
│   - Partition domain into functional categories, equivalence classes   │
│   - System states, market hours, volatility regimes, order types       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 2: Boundary Value Analysis (BVA)                      │
│   - Stress testing exact operational limits (at, above, below)         │
│   - Daily loss limit ($1,500), 15:55 ET close, VIX thresholds          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 3: Combinatorial & Pairwise Testing                   │
│   - All-pairs interaction matrix across Strategy x VIX x Time x Trend  │
│   - 32 mathematically optimal orthogonal execution vectors             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Tier 4: Real-World Scenarios & Signal-to-Exit E2E          │
│   - Full lifecycle pipelines: ORB Breakout, Chop Defense, News Shock   │
│   - End-of-Day 15:55 MOC Flattening, Circuit Breaker Lockout           │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Tier 1: Category-Partition Method (CPM)
The input and operational spaces are decomposed into formal categories and equivalence classes:

| Category | Equivalence Partition Classes | Test Assertion / Expected Behavior |
|---|---|---|
| **Account State** | 1. Healthy ($50,000 to $48,500)<br>2. Drawdown Warning ($48,900 to $48,500)<br>3. Circuit Breaker Tripped (<$48,500)<br>4. Cash Flat ($50,000, 0 positions) | In Class 1 & 2, new entries permitted.<br>In Class 3, all orders rejected, open positions immediately liquidated.<br>In Class 4, buying power equals $200,000. |
| **Market Session Phase** | 1. Pre-Market (04:00–09:30 ET)<br>2. Open Volatility Flush (09:30–10:00 ET)<br>3. Intraday Trend (10:00–11:30 ET)<br>4. Midday Chop (11:30–14:00 ET)<br>5. Power Hour (15:00–15:45 ET)<br>6. EOD Flatten Window (15:45–16:00 ET)<br>7. Post-Market (16:00–20:00 ET) | In Phase 1, scanner active; no orders placed.<br>In Phase 2, ORB formation active; wide spreads tolerated.<br>In Phase 4, mean-reversion active, trend strategies reduce sizing.<br>In Phase 6, NO new entries; 15:55 ET all positions flattened.<br>In Phase 7, 0 positions allowed. |
| **Volatility Regime (VIX)** | 1. Subdued (VIX < 15)<br>2. Normal (15 ≤ VIX < 22)<br>3. Elevated (22 ≤ VIX < 30)<br>4. Crisis (VIX ≥ 30) | In Class 1, normal sizing (1.0x).<br>In Class 2, standard parameters.<br>In Class 3, position sizing scaled down to 0.5x, stop widths widened by 1.5x.<br>In Class 4, trading halted or restricted to cash-preservation mode. |
| **News Sentiment Intensity** | 1. Irrelevant / Neutral (Score -0.2 to +0.2)<br>2. Positive Catalyst (Score > +0.6, high confidence)<br>3. Negative Shock (Score < -0.6, high confidence)<br>4. Stale Headline (Age > 15 minutes) | In Class 1, ignored.<br>In Class 2, News Momentum triggers Long entry.<br>In Class 3, News Momentum triggers Short or aborts Long.<br>In Class 4, discarded by freshness validator. |

### 3.2 Tier 2: Boundary Value Analysis (BVA)
Boundary testing exercises edge conditions at exact mathematical thresholds:

| Boundary Parameter | Lower Bound ($-\epsilon$) | Exact Threshold ($T$) | Upper Bound ($+\epsilon$) | Verification Invariant |
|---|---|---|---|---|
| **Max Daily Loss Limit** | $1,499.50 cumulative loss | **$1,500.00** ($3.00\%$ of $50k) | $1,500.50 cumulative loss | At $1,499.50: trade allowed. At $1,500.00: circuit breaker fires, halts trading. At $1,500.50: incoming order rejected immediately with `RISK_CIRCUIT_BREAKER_ACTIVE`. |
| **EOD Flatten Clock** | 15:54:59 ET | **15:55:00 ET** | 15:55:01 ET | At 15:54:59: positions remain active with trailing stops. At 15:55:00: MOC liquidation order generated for 100% of open inventory. At 15:55:01: all positions confirmed closed, account is 100% cash. |
| **Per-Trade Equity Risk** | $499.00 risk ($0.998\%$) | **$500.00** ($1.000\%$) | $501.00 risk ($1.002\%$) | Position sizing algorithm truncates share quantity so calculated loss at stop never exceeds $500.00. |
| **Bid/Ask Spread Veto** | $0.04 spread on $100 stock | **$0.05 spread** ($0.05\%$) | $0.06 spread ($0.06\%$) | Orders vetoed if current bid-ask spread exceeds 0.05% of asset price to prevent excessive slippage. |
| **VIX Freeze Level** | VIX = 29.99 | **VIX = 30.00** | VIX = 30.01 | Crossing 30.00 immediately triggers safety alert and halves maximum allowed open risk. |

### 3.3 Tier 3: Combinatorial & Pairwise Testing
To test the combinatorial space without running thousands of redundant tests, an **Orthogonal Array Pairwise Matrix** is constructed:
- Parameters:
  - Factor A: Strategy (ORB, VWAP Pullback, News Momentum, Mean Reversion) - 4 levels
  - Factor B: VIX Regime (Low, Normal, High, Crisis) - 4 levels
  - Factor C: Market Trend (Strong Bull, Choppy Range, Bear Trend) - 3 levels
  - Factor D: Time Window (Open 09:30, Morning 10:30, Midday 12:30, EOD 15:50) - 4 levels
  - Factor E: Order Execution (Clean Fill, Partial Fill, Slippage Shock) - 3 levels

Using pairwise all-pairs reduction, this produces **32 deterministic test suites**. Every pairwise combination of features (e.g. ORB + Crisis VIX; News Momentum + Midday Chop + Slippage Shock) is executed and evaluated against invariant consistency rules (e.g. no order without a stop, no unhandled exceptions).

### 3.4 Tier 4: Real-World Application Scenarios (Signal-to-Exit E2E)
Full end-to-end integration tests simulating real trading days:
1. **Scenario 4.1: The Clean 5-Minute ORB Winner (NVDA)**
   - 09:30 open: NVDA opens at $124.00.
   - 09:30–09:35: High of $124.80, Low of $123.60 established.
   - 09:36:01: Volume spikes to 4.2x 20-period average, price breaks $124.85.
   - Signal generated: BUY 150 NVDA.
   - Order lifecycle: SUBMITTED -> FILLED @ $124.85. Stop set at $123.60 (Risk: $187.50).
   - Price advances to $126.10 (Target 1 hit): 50% shares sold (+1.0R), stop adjusted to breakeven ($124.85).
   - Price hits $127.35 (Target 2 hit): Remaining 50% sold (+2.0R). Realized profit: +$281.25.
2. **Scenario 4.2: False Breakout & Immediate Stop Discipline (TSLA)**
   - 09:38: TSLA breaks morning high at $220.50. Long position filled.
   - 09:39: Heavy market selloff hits; price reverses sharply to $218.80.
   - Stop-loss triggers deterministically at $219.00.
   - Verification: Trade closed with exactly -1.0R loss (-$225.00). No position re-entry on same bar. Account equity decrements correctly.
3. **Scenario 4.3: Midday Chop Trap & Capital Preservation**
   - 12:15: Market index enters low-volume consolidation.
   - Mean reversion signals fired; sizing scaled down 50% by time-of-day filter.
   - Trailing stops enforce tight exits; maximum loss capped under $100.
4. **Scenario 4.4: 15:55 ET EOD Forced Market-On-Close Flattening**
   - 15:54: Two swing positions remain open (AAPL Long, SPY Short).
   - 15:55:00: Time trigger activates. Engine cancels all pending limit orders, issues market exit orders.
   - 15:55:03: All fills confirmed. Account positions count = 0. Cash = $51,430.00.

---

## 4. Market Data Replay & Synthetic Simulation Engine Architecture

### 4.1 Replay Engine Design & AlpacaRelay Protocol Fidelity
The simulation engine functions as a **high-fidelity local proxy** for AlpacaRelay:
- Implements the exact same downstream WebSocket interface:
  - Auth handshake: `{"action": "auth", "token": "..."}` -> `[{"T": "success", "msg": "authenticated"}]`
  - Subscription command: `{"action": "subscribe", "trades": [...], "quotes": [...], "bars": [...], "news": [...]}`
  - Outbound data payloads:
    - Bar: `{"T": "b", "S": "NVDA", "o": 124.0, "h": 124.8, "l": 123.6, "c": 124.5, "v": 150000, "vw": 124.3, "t": "2026-09-21T13:35:00Z"}`
    - Quote: `{"T": "q", "S": "NVDA", "bp": 124.48, "ap": 124.52, "bs": 10, "as": 12, "t": "..."}`
    - Trade: `{"T": "t", "S": "NVDA", "p": 124.50, "s": 100, "t": "..."}`
    - News: `{"T": "n", "id": 9812, "headline": "NVIDIA announces next-gen AI chip architecture", "source": "benzinga", "symbols": ["NVDA"], "content": "...", "created_at": "..."}`
  - Upstream status: `{"T": "relay", "msg": "upstream_connected"}`
- Implements the REST API endpoints:
  - `GET /vix`: Returns spot VIX JSON (`{"state": "ready", "value": 18.2, "asof": "..."}`)
  - `GET /data/v2/stocks/{symbol}/bars`: Returns historical 1-minute lookback bars for pre-market indicators.

### 4.2 Replay Modes & Configurable Clock Acceleration
The simulation engine provides two operational clock modes:
1. **Real-Time 1x Mode (Wall-Clock Pacing)**:
   - Ticks and bars are dispatched matching real-world inter-arrival timestamps.
   - Used for the Monday Live Pre-Market Dry Run and visual UI audits.
2. **Accelerated Replay Mode (2x, 5x, 10x Speed)**:
   - Internal virtual clock advances at $N \times$ speed (e.g. 1 minute of market data delivered every 6 seconds at 10x).
   - Allows a complete 65-minute market session (09:25–10:30 ET) to execute in **6.5 minutes** for rapid automated CI/CD regression passes.
3. **Step Mode (Deterministic Virtual Time)**:
   - Clock advances strictly on command (`clock.step_minute()`, `clock.step_tick()`).
   - Used for zero-race-condition unit and integration assertions.

### 4.3 Realistic Data Generation Models
When historical recordings are supplemented with synthetic feeds, the engine utilizes:
- **Intraday U-Shaped Volume Curve**: High volume at 09:30 open, tapering toward midday (12:00–13:30), rising into the close.
- **Jump-Diffusion Volatility Process**: Geometric Brownian motion supplemented with Poisson jump arrivals to simulate sudden news-driven price gaps and liquidity vacuums.
- **Realistic Microstructure Spreads**: Bid/Ask spread dynamically widening during volatility surges (e.g. from $0.01 to $0.08) to test engine slippage models.

---

## 5. Monday Live Market Open Simulation Dry Run (09:25–10:30 ET)

### 5.1 Objective & Certification Criteria
The Monday Dry Run simulates the most critical trading window of the week—the market open following the weekend news cycle. It certifies that the entire stack (Ingestion, Strategies, Risk, Ledger, UI, Replay) executes flawlessly under live conditions.

### 5.2 Minute-by-Minute Scenario Timeline

| Time (ET) | Session Phase | Market Simulation Event | Expected System Response & Verification Assertions |
|---|---|---|---|
| **09:25:00** | Pre-Market Setup | Replay engine starts stream. Ingests pre-market bars for watch symbols: `AAPL`, `NVDA`, `TSLA`, `SPY`, `AMD`. | System loads $50,000 balance. Pre-market scanner ranks top momentum candidates. UI displays "PRE-MARKET WARMUP [09:25 ET]" with blue/violet ambient glow. VIX read as 18.4. |
| **09:29:55** | Pre-Open Countdown | Simulated clock reaches 5 seconds before open. | Risk engine verifies all safety limits armed. Position count confirmed 0. Limit orders blocked. |
| **09:30:00** | **The Open Bell** | 09:30 bell rings. Influx of 1-minute bars with 10x volume. Bid/ask spreads widen to $0.06. | Open Volatility Flush state active. Sizing filter enforces conservative spreads. Engine logs "MARKET OPEN BELL". Zero spurious orders executed during first 60 seconds. |
| **09:35:00** | 5-Min ORB Completion | First 5-minute bars close across all watch tickers. Highs, lows, and volume thresholds computed. | ORB Strategy establishes breakout triggers: e.g. NVDA High: $124.50, Low: $123.60. Strategy card on UI displays status: "ARMED - AWAITING BREAKOUT". |
| **09:37:15** | ORB Breakout Surge | NVDA prints 1-min bar breaking $124.50 on 3.5x volume (Price: $124.75). | ORB Strategy emits Long Buy signal. Risk engine checks daily drawdown ($0 < $1,500), position risk ($150 = 0.3% equity). Order filled @ $124.75 for 150 shares. **"Now Playing" bottom tray pops up with live trade!** |
| **09:42:30** | Stop Tightening | NVDA advances to $125.80 (+1.2R). | Trailing stop algorithm triggers. Stop moved from initial $123.60 to breakeven ($124.75). UI Now Playing bar updates trailing stop indicator to green lock icon. |
| **09:48:00** | Breaking News Injection | Synthetic Benzinga headline injected: "Federal Reserve issues statement on interest rate trajectory; tech sector sentiment positive". | News Momentum Strategy parses headline, detects sentiment score +0.82. TSLA breaks morning resistance at $215.00. Engine enters TSLA Long (100 shares @ $215.20). UI shows active positions count = 2. |
| **09:55:00** | Volatility Reversal & Stop Discipline | Synthetic market flush hits tech sector. TSLA pulls back violently from $216.00 to $213.50. | TSLA hits stop-loss at $214.00 deterministically. Position liquidated with exactly -$120.00 loss. No slippage overshoot. NVDA breakeven stop protects profits. Balance updates to $49,880 + unrealized gains. |
| **10:10:00** | Trend Continuation Profit Taking | NVDA resumes uptrend, hits Take-Profit Target 1 at $127.25. | 50% NVDA shares (75 shares) closed via limit order. Realized gain: +$187.50. Remaining 75 shares stop trailed to $126.00 (+1.25R locked). Portfolio daily PnL turns green (+$67.50 net). Ambient UI glow transitions to soft emerald. |
| **10:30:00** | Session Conclusion & Audit | Clock reaches 10:30:00 ET. Simulation completes. | Automated Auditor reviews execution logs, verifies ledger math, confirms WebSocket connectivity remained unbroken, and issues the signed **Monday Operational Readiness Certificate**. |

### 5.3 Automated Dry Run Acceptance Checklist
The dry run passes if and only if all following criteria are met:
- [x] Zero unhandled exceptions or thread panics in Python engine.
- [x] Zero unhandled JavaScript rejections, React hydration errors, or layout thrashing in UI.
- [x] Paper account ledger balance perfectly reconciles: $\text{Cash} + \text{Unrealized PnL} = \text{Total Equity}$.
- [x] Stop-loss executions triggered within 100ms of price breaching threshold.
- [x] Daily loss circuit breaker remained armed and functional throughout session.
- [x] All WebSocket state updates arrived at UI within <50ms latency.

---

## 6. Process Hygiene, Clean Signal Traps & Port Reclamation Protocols

### 6.1 Process Hygiene Operating Mandate
In strict compliance with user operating requirements (RULE: Process Hygiene & Cleanup), **all local server processes, test scripts, background daemons, or temporary simulation loops spawned during development or testing must be killed and terminated immediately**.

Lingering daemons on ports 3005, 8005, or 8015 will cause subsequent test runs or deployments to fail, exhaust system file descriptors, and compromise test determinism.

### 6.2 Signal Traps & Graceful Shutdown Engineering
All backend services and simulation scripts must register POSIX signal handlers for `SIGINT` (Ctrl-C) and `SIGTERM`:

```python
# Process Hygiene: Deterministic Shutdown & Socket Cleanup Handler
import asyncio
import signal
import sys
import logging

class GracefulProcessManager:
    def __init__(self, port: int, service_name: str):
        self.port = port
        self.service_name = service_name
        self.is_shutting_down = False
        self.servers = []

    def register_server(self, server):
        self.servers.append(server)

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop):
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.shutdown(s)))

    async def shutdown(self, sig=None):
        if self.is_shutting_down:
            return
        self.is_shutting_down = True
        logging.info(f"[{self.service_name}] Caught signal {sig}. Initiating zero-linger shutdown...")

        # 1. Close all active WebSocket client connections
        for server in self.servers:
            if hasattr(server, 'close'):
                server.close()
            if hasattr(server, 'wait_closed'):
                try:
                    await asyncio.wait_for(server.wait_closed(), timeout=2.0)
                except asyncio.TimeoutError:
                    logging.warning(f"[{self.service_name}] Server wait_closed timed out; forcing close.")

        # 2. Cancel all running asyncio tasks except current task
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        logging.info(f"[{self.service_name}] Socket on port {self.port} cleanly released. Exiting.")
```

### 6.3 Automated Port Reclamation Verification Script
A dedicated test fixture and CLI utility (`verify_port_hygiene.sh`) is executed at the end of every test suite run:

```bash
#!/usr/bin/env bash
# verify_port_hygiene.sh - Asserts no lingering trading processes on designated ports
set -euo pipefail

PORTS=(3005 8005 8015)
VIOLATIONS=0

echo "🔍 Auditing port hygiene across project ports: ${PORTS[*]}..."

for port in "${PORTS[@]}"; do
  PIDS=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo "❌ INTEGRITY VIOLATION: Port $port is still occupied by PID(s): $PIDS"
    for pid in $PIDS; do
      CMD=$(ps -p "$pid" -o command= 2>/dev/null || echo "unknown")
      echo "   PID $pid details: $CMD"
      echo "   Killing lingering PID $pid..."
      kill -9 "$pid" 2>/dev/null || true
    done
    VIOLATIONS=$((VIOLATIONS + 1))
  else
    echo "✅ Port $port is clean and liberated."
  fi
done

if [ "$VIOLATIONS" -gt 0 ]; then
  echo "⚠️ Remediation applied: lingering test processes were forcefully terminated."
  exit 1
fi

echo "✨ All ports verified clean. Zero lingering daemons."
exit 0
```

### 6.4 Shell Trap Integration in Test Runners
Any bash script launching background test fixtures must wrap executions in an exit trap:
```bash
# In all test execution scripts:
trap 'echo "Cleaning up background jobs..."; kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM
```

---

## 7. Architectural Recommendations & Implementation Roadmap

| Milestone | Recommended Scope & Deliverables | Owning Agents |
|---|---|---|
| **M1: Trading Core & AlpacaRelay Ingestion** | Deterministic paper account ledger ($50,000), AlpacaRelay WebSocket client, REST `/vix` dxFeed client, institutional risk circuit breaker ($1,500 daily loss limit), automated 15:55 ET EOD flattening. | `worker_m1`, `reviewer_m1`, `auditor_m1` |
| **M2: 4 Dynamic Trading Strategies** | Implementation of ORB, VWAP Trend Pullback, News Momentum Breakout, and Statistical Mean Reversion. Dynamic VIX scaling and Time-of-Day regime filters. | `worker_m2`, `reviewer_m2`, `auditor_m2` |
| **M3: Apple Music Mobile-First UI** | Next.js 15, Tailwind CSS, Framer Motion, obsidian dark palette, dynamic momentum ambient glow, strategy playlists carousel, expandable Now Playing bottom tray, real-time WebSocket state streaming. | `worker_m3`, `reviewer_m3`, `auditor_m3` |
| **M4: Comprehensive E2E Testing Suite (Tiers 1–4)** | Category-Partition, Boundary Value Analysis, Pairwise 32-vector test suite, real-world scenario tests. Zero lingering daemons. 100% pytest pass rate. | `worker_m4`, `reviewer_m4`, `auditor_m4` |
| **M5: Monday Market Open Simulation Dry Run** | Full 09:25–10:30 ET market open dry run execution, news event injection, risk stop execution, automated operational readiness certification. | `worker_m5`, `reviewer_m5`, `auditor_m5` |
| **M6: Repository Delivery & Final Audit** | Clean git commit history, upstream push to GitHub (`git push origin main`), remote build verification, zero-linger port confirmation. | `worker_m6`, `victory_auditor` |

---

## 8. Summary of Findings & Invariant Checklist

1. **Host Environment**: Node v22.22.2, npm 10.9.7, Python 3.12 (via uv), pytest 8.4.2, and browser-use CLI are fully functional on Darwin arm64.
2. **Port Protection**: Standard ports 3000, 8000, 8490 are active by existing projects; AutonomousDayTrader will strictly bind to UI port **3005** and Backend port **8005**.
3. **AlpacaRelay Format**: Verified exact message shapes: `b` (bar), `q` (quote), `t` (trade), `n` (news), `relay` (upstream status), and dxFeed `GET /vix` REST format.
4. **UI Architecture**: Deep obsidian dark theme (#000000, #0a0a0c) with backdrop-blur-xl dynamic glassmorphism, portfolio momentum glow blurs, 4 Strategy Playlists, and an expandable Now Playing bottom tray.
5. **Testing & Simulation**: 4-Tier opaque-box testing framework, 1x–10x accelerated replay engine, 09:25–10:30 ET Monday market open dry run protocol, and strict POSIX signal traps ensuring zero lingering daemons.
