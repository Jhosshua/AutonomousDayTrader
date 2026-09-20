# Algorithmic & Mathematical Specification: AutonomousDayTrader Engine & Strategies

**Author**: `explorer_strategies_survey`  
**Target Project**: AutonomousDayTrader  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/explorer_strategies_survey`  
**Date**: 2026-09-19  
**Status**: Authoritative Architectural & Quantitative Specification  

---

## Executive Summary

This report establishes the complete mathematical, state-machine, and algorithmic architecture for the **AutonomousDayTrader** system. The system operates on a virtual **$50,000 Paper Trading Account** executing 4 complementary, high Sharpe-ratio intraday strategies with deterministic execution, institutional risk guardrails, real-time VIX volatility regime self-adaptation, and strict intraday session dynamics culminating in zero overnight exposure.

The 4 core strategies are engineered to achieve high aggregate Sharpe ratio ($\ge 2.2$) through orthogonal alpha sources:
1. **Strategy 1: Opening Range Breakout (ORB)** — Directional momentum capitalizing on institutional opening imbalances (5-min / 15-min ranges).
2. **Strategy 2: VWAP Trend Pullback & Continuation** — Trend-following execution exploiting institutional VWAP benchmark execution algos with multi-band standard deviations.
3. **Strategy 3: Catalyst News Momentum Breakout** — Low-latency event-driven momentum triggered by AlpacaRelay real-time news headlines with NLP sentiment scoring and volume-surge verification.
4. **Strategy 4: Statistical Mean Reversion / Exhaustion Fades** — High-probability counter-trend execution targeting statistical exhaustion ($Z \ge 2.5$), RSI divergence, and volume climax fades back to the 20-period moving average.

All operations are governed by an **Institutional Risk Engine** enforcing a 3% ($1,500) hard daily loss circuit breaker, 1–2% ($500–$1,000) per-position risk budgeting, ATR-based dynamic brackets, and a deterministic 4-phase automated zero-overnight flattening schedule starting at 15:45 ET.

---

## Section 1: $50,000 Paper Trading Account & Core Execution Engine

### 1.1 Account Capital & Balance State Machine

The paper trading account is a self-contained, deterministic state machine initialized with $C_0 = \$50,000.00$.

#### Capital State Variables:
* **Cash ($C_t$)**: Unallocated liquid currency balance.
* **Open Positions ($\mathcal{P}_t$)**: Mapping of active tickers $s \in \mathcal{S}$ to position tuple:
  $$\mathcal{P}_t(s) = \left( q_s, \bar{P}_{\text{entry}, s}, P_{\text{last}, s}, \text{side}_s, P_{\text{stop}, s}, P_{\text{target}, s}, t_{\text{entry}, s}, \text{strategy\_id} \right)$$
  where $q_s > 0$ denotes share quantity, $\text{side}_s \in \{\text{LONG}, \text{SHORT}\}$.
* **Market Value of Open Positions ($MV_t$)**:
  $$MV_t = \sum_{s \in \mathcal{P}_t} q_s \cdot P_{\text{last}, s}$$
* **Unrealized PnL ($uPnL_t$)**:
  $$uPnL_t = \sum_{s \in \mathcal{P}_t} \left[ \text{sign}(\text{side}_s) \cdot q_s \cdot (P_{\text{last}, s} - \bar{P}_{\text{entry}, s}) \right]$$
  where $\text{sign}(\text{LONG}) = +1$, $\text{sign}(\text{SHORT}) = -1$.
* **Realized PnL ($rPnL_t$)**: Cumulative closed net profit/loss since session inception.
* **Account Equity ($E_t$)**:
  $$E_t = C_t + \sum_{s \in \mathcal{P}_t} \text{Position Equity}_s = E_{\text{start}} + rPnL_t + uPnL_t - \text{Total Fees}_t$$
* **Buying Power ($BP_t$)**:
  Under FINRA Rule 4210 Day-Trading Margin Requirements, accounts with equity $E_t \ge \$25,000$ qualify for **Pattern Day Trader (PDT) 4:1 intraday leverage**:
  $$BP_{\text{intraday}, t} = 4 \times \left( E_t - \text{Initial Margin Held}_t \right)$$
  For standard cash-basis accounting:
  $$BP_{\text{cash}, t} = C_t$$
  The trading engine defaults to 4x intraday margin ($BP_{\text{max}} = \$200,000.00$ at inception), subject to per-position capital caps ($25\%$ max buying power per position = $\$50,000$).

### 1.2 Execution Order Lifecycle State Machine

Every order $\mathcal{O}$ follows an explicit finite-state machine (FSM):

```
       [ Client / Strategy Signal ]
                     |
                     v
             +---------------+
             |  PENDING_NEW  |
             +---------------+
                     |
            (Risk Checks Pass)
                     |
                     v
             +---------------+      (Risk / Reject)
             |      NEW      | ----------------------> +----------+
             +---------------+                        | REJECTED |
               /           \                          +----------+
              /             \
             v               v
    +-----------------+  +-------------------+
    | PARTIALLY_FILLED|  |      FILLED       |
    +-----------------+  +-------------------+
             |                     |
      (Fill Remainder)             | (Position Created / Closed)
             v                     v
    +-----------------+       [ COMPLETE ]
    |     FILLED      |
    +-----------------+
             
    From [NEW] or [PARTIALLY_FILLED]:
      -- User / Time / Circuit Breaker Cancel --> +----------------+ -> +-----------+
                                                  | PENDING_CANCEL |    | CANCELLED |
                                                  +----------------+    +-----------+
```

#### Order States & Invariants:
1. **`PENDING_NEW`**: Order created by strategy; awaiting risk engine validation (BP check, daily loss check, session time check, max position risk check).
2. **`NEW`**: Order passed all risk gates and entered working order book.
3. **`PARTIALLY_FILLED`**: Partial execution against bar volume/liquidity; remaining quantity is working.
4. **`FILLED`**: Order completely executed. Cash, positions, and equity updated atomically.
5. **`PENDING_CANCEL`**: Cancellation requested (e.g. at 15:50 ET or via circuit breaker).
6. **`CANCELLED`**: Remaining quantity removed from order book.
7. **`REJECTED`**: Order failed risk check or broker pre-trade rule (error code logged).
8. **`EXPIRED`**: Order lifetime exceeded (e.g. Day order at session close).

### 1.3 Execution Simulation & Fill Engine Mechanics

To prevent unrealistic paper trading assumptions, the local execution simulator applies an institutional microstructure fill model:

1. **Fill Price Determination**:
   * For **Market Orders**:
     * Buy: $P_{\text{fill}} = P_{\text{ask}} + \text{Slippage}$
     * Sell: $P_{\text{fill}} = P_{\text{bid}} - \text{Slippage}$
     If quote stream is temporarily waiting for update, synthetic spread is constructed: $\text{Spread} = \max\left(0.01, P_{\text{last}} \times \text{Spread}_{\text{bps}}\right)$ where $\text{Spread}_{\text{bps}} = 0.0004$ (4 bps for mega-caps).
   * For **Limit Orders**:
     * Buy Limit $P_{\text{limit}}$: Fills only if $P_{\text{ask}} \le P_{\text{limit}}$ or bar $L_t \le P_{\text{limit}}$.
     * Sell Limit $P_{\text{limit}}$: Fills only if $P_{\text{bid}} \ge P_{\text{limit}}$ or bar $H_t \ge P_{\text{limit}}$.
   * For **Stop-Loss Orders**:
     * Triggers when $P_{\text{last}} \le P_{\text{stop}}$ (Long) or $P_{\text{last}} \ge P_{\text{stop}}$ (Short), converting to Market Order with adverse slippage.

2. **Market Impact & Dynamic Slippage Formulation**:
   $$\text{Slippage} = \frac{1}{2} \text{BidAskSpread} + \gamma \cdot \sigma_{1\text{m}} \cdot \sqrt{\frac{q_{\text{order}}}{V_{\text{bar}}}}$$
   Where:
   * $\sigma_{1\text{m}}$ is the 1-minute bar return volatility ($\text{High} - \text{Low}$).
   * $V_{\text{bar}}$ is the current 1-minute volume.
   * $\gamma \approx 0.08$ (liquidity friction parameter).
   * Baseline slippage floor: $1.0\text{ bps}$ ($0.01\%$) on liquid US equities ($V > 1\text{M}$ shares/day).

3. **Regulatory Fees & Friction Model**:
   * Commission: $\$0.00$ (modern zero-commission retail paper broker model).
   * SEC Transaction Fee (Sell orders only):
     $$\text{Fee}_{\text{SEC}} = 0.0000278 \times (q_{\text{sell}} \cdot P_{\text{fill}})$$
   * FINRA Trading Activity Fee (TAF) (Sell orders only):
     $$\text{Fee}_{\text{TAF}} = \min\left(\$8.30, 0.000166 \times q_{\text{sell}}\right)$$

### 1.4 Real-Time Mark-to-Market Accounting Formulas

On every incoming trade, quote, or 1-minute bar for subscribed symbol $s$:
1. Update $P_{\text{last}, s}$.
2. Recalculate position unrealized PnL:
   $$uPnL_s = \begin{cases} q_s \cdot (P_{\text{last}, s} - \bar{P}_{\text{entry}, s}) & \text{if LONG} \\ q_s \cdot (\bar{P}_{\text{entry}, s} - P_{\text{last}, s}) & \text{if SHORT} \end{cases}$$
3. Recalculate total portfolio equity:
   $$E_t = C_t + \sum_{s \in \mathcal{P}_t} \left( q_s \cdot \bar{P}_{\text{entry}, s} + uPnL_s \right)$$
4. Recalculate daily drawdown from session start equity $E_0 = \$50,000.00$:
   $$DD_t = \frac{E_0 - E_t}{E_0}$$

---

## Section 2: Institutional Risk Management Engine & Circuit Breakers

The risk engine is an autonomous gatekeeper sitting directly between strategy signal emission and order execution. Every order must receive cryptographic/boolean approval before dispatch.

```
+------------------+      Signal       +---------------------------+
| Trading Strategy | ----------------> |  Institutional Risk Gate  |
+------------------+                   +---------------------------+
                                                     |
                 +-----------------------------------+-----------------------------------+
                 |                                   |                                   |
                 v                                   v                                   v
      [ Daily Loss Breaker ]              [ Per-Position Risk ]               [ Session Clock ]
      Is DD >= 3% ($1,500)?              Is Risk$ <= 1-2% Equity?             Is Time >= 15:45 ET?
          | YES -> HALT                       | NO -> REJECT                      | YES -> LOCKOUT
```

### 2.1 Hard Maximum Daily Loss Circuit Breaker

* **Threshold**: $3.0\%$ of starting session equity ($E_0 = \$50,000.00$), corresponding to a cumulative intraday net loss of **$\$1,500.00$**.
* **Formula**:
  $$\text{Drawdown}_{\$} = E_0 - E_t \ge \$1,500.00 \implies \text{TRIP\_CIRCUIT\_BREAKER}$$
* **Deterministic Circuit Breaker Protocol**:
  1. **Immediate State Transition**: Set system trading state to `HALTED_DAILY_LOSS`.
  2. **Cancel All Working Orders**: Broadcast `CANCEL` for all working orders across all strategies.
  3. **Forced Market Flattening**: Generate immediate market orders to close all active positions $\mathcal{P}_t$:
     * For Long position $q_s$: submit `MARKET_SELL(s, q_s)`.
     * For Short position $q_s$: submit `MARKET_BUY_TO_COVER(s, q_s)`.
  4. **Lockout Enforcement**: Reject all subsequent order submissions from any strategy until the next trading session (09:30 ET next business day). Emits high-priority audit alert to log and UI.

### 2.2 Per-Position Risk Limits & Volatility-Adjusted Sizing

No single trade is permitted to jeopardize account capital.
* **Risk Budget per Trade ($R_{\$}$)**: Standard is **$1.0\%$ of current equity** ($R_{\$} = \$500.00$ on $\$50\text{k}$ account), with a hard ceiling of **$2.0\%$** ($R_{\$, \text{max}} = \$1,000.00$).
* **Position Sizing Formula**:
  $$q = \min\left( \left\lfloor \frac{R_{\$}}{|P_{\text{entry}} - P_{\text{stop}}|} \right\rfloor, \left\lfloor \frac{E_t \times \text{MaxAllocPct}}{P_{\text{entry}}} \right\rfloor, \left\lfloor \frac{BP_t}{P_{\text{entry}}} \right\rfloor \right)$$
  Where:
  * $|P_{\text{entry}} - P_{\text{stop}}|$ is the dollar stop distance determined by technical structure or ATR.
  * $\text{MaxAllocPct} = 0.25$ ($25\%$ maximum capital allocation to any single stock, i.e., max $\$12,500$ position value on cash, or $\$50,000$ on 4x intraday margin).
  * Minimum stop distance constraint: $\frac{|P_{\text{entry}} - P_{\text{stop}}|}{P_{\text{entry}}} \ge 0.004$ ($0.40\%$) to prevent infinite sizing on micro-stops.
  * Maximum stop distance constraint: $\frac{|P_{\text{entry}} - P_{\text{stop}}|}{P_{\text{entry}}} \le 0.040$ ($4.0\%$) to avoid illiquid or broken charts.

### 2.3 Dynamic Stop-Loss & Multi-Tier Take-Profit Brackets

Each executed order automatically attaches an atomic OCO (One-Cancels-Other) bracket:

1. **Initial Stop-Loss**:
   * Calculated via $k_{\text{stop}} \times \text{ATR}_{14}(1\text{m})$ or technical support/resistance:
     $$P_{\text{stop}} = \begin{cases} P_{\text{entry}} - \max\left(k_{\text{ATR}} \cdot \text{ATR}_{14}, \Delta P_{\text{support}}\right) & \text{for LONG} \\ P_{\text{entry}} + \max\left(k_{\text{ATR}} \cdot \text{ATR}_{14}, \Delta P_{\text{resistance}}\right) & \text{for SHORT} \end{cases}$$
2. **Multi-Tier Take-Profit Architecture**:
   * **Target 1 ($1.5R$)**: Exit $50\%$ of position quantity ($q_1 = \lfloor q/2 \rfloor$).
     $$\text{Target}_1 = P_{\text{entry}} + \text{sign}(\text{side}) \times 1.5 \times |P_{\text{entry}} - P_{\text{stop}}|$$
   * **Breakeven Ratchet**: Upon execution of Target 1, the stop-loss on the remaining $50\%$ is instantly ratcheted to $P_{\text{entry}} + \text{sign}(\text{side}) \times 0.02$ (breakeven + friction buffer).
   * **Target 2 ($2.5R$ or Trailing Stop)**: Remainder exits at Target 2 or rides an ATR trailing stop:
     $$P_{\text{trail}, t} = \max\left(P_{\text{trail}, t-1}, P_{\text{highest}} - 1.5 \cdot \text{ATR}_{14}\right) \quad (\text{for LONG})$$

### 2.4 Time-Enforced Automated Zero-Overnight Flattening Protocol

Because day trading mandates zero overnight holding risk (eliminating overnight gap risk, earnings surprises, and borrowing fees), the engine implements a deterministic 4-phase closeout clock:

| Phase Time (ET) | Phase Name | Execution Action | Invariants Enforced |
|---|---|---|---|
| **15:45:00** | `ENTRY_LOCKOUT` | Hard cutoff on new entries. Strategy signals ignored. | Zero new positions can be initiated. |
| **15:50:00** | `ORDER_PURGE` | Cancel all working unfilled entry and limit orders. Ratchet existing stops to $0.5R$ or breakeven. | No passive bids/offers left working in the book. |
| **15:55:00** | `FORCE_FLATTEN` | Generate aggressive Market Orders to close 100% of open positions across all tickers. | All active positions liquidated immediately. |
| **15:58:00** | `FLAT_AUDIT` | Query position state: assert $|\mathcal{P}| == 0$. If any position non-zero, retry emergency IOC market order. | Guaranteed flat portfolio before 16:00 ET. |
| **16:00:00** | `MARKET_CLOSED` | Record final session ledger, compute daily Sharpe, reset order IDs for next session. | $100\%$ Cash balance, $0.00 open exposure. |

---

## Section 3: High Sharpe-Ratio Intraday Strategy Algorithmic Specifications

```
                     +-----------------------------------+
                     | AlpacaRelay Ingestion (Bars/News) |
                     +-----------------------------------+
                                       |
         +-----------------+-----------+-----------+-----------------+
         |                 |                       |                 |
         v                 v                       v                 v
   +------------+   +--------------+        +--------------+   +-------------+
   | Strategy 1 |   |  Strategy 2  |        |  Strategy 3  |   | Strategy 4  |
   | Opening    |   |  VWAP Trend  |        | Catalyst     |   | Statistical |
   | Range      |   |  Pullback &  |        | News         |   | Mean        |
   | Breakout   |   |  Continuation|        | Momentum     |   | Reversion   |
   +------------+   +--------------+        +--------------+   +-------------+
         |                 |                       |                 |
         +-----------------+-----------+-----------+-----------------+
                                       |
                                       v
                     +-----------------------------------+
                     | Risk Engine & Execution Arbitrator|
                     +-----------------------------------+
```

---

### 3.1 Strategy 1: Opening Range Breakout (ORB)

#### 1. Economic Logic & Alpha Thesis
The Opening Range Breakout captures institutional order flow driving price discovery during the first 5 to 15 minutes of regular trading hours (09:30–09:45 ET). Overnight news, earnings, and macro data create massive imbalance auctions at 09:30 ET. When price cleanly breaks outside the established opening range on abnormal relative volume (RVOL), it signals persistent institutional accumulation or distribution that trends for the remainder of the morning session.

#### 2. Mathematical Formulation
* **Opening Range Interval**: $t \in [t_{\text{open}}, t_{\text{range\_end}}]$
  * 5-minute ORB: 09:30:00 to 09:35:00 ET.
  * 15-minute ORB: 09:30:00 to 09:45:00 ET.
* **Range Extrema**:
  $$R_H = \max_{t \in [09:30, t_{\text{range\_end}}]} \text{High}_t$$
  $$R_L = \min_{t \in [09:30, t_{\text{range\_end}}]} \text{Low}_t$$
  $$\text{Range Width } \Delta R = R_H - R_L$$
* **Quality & Volatility Filter**:
  $$\text{Range Pct} = \frac{\Delta R}{P_{\text{open}}} \in [0.005, 0.035] \quad (0.5\% \le \Delta R \le 3.5\%)$$
  If $\text{Range Pct} < 0.5\%$, volatility is too compressed (chop trap); if $> 3.5\%$, range is blown out, risking excessive stop distance.
* **Volume Surge Confirmation (RVOL)**:
  $$\text{RVOL} = \frac{V_{\text{range}}}{\bar{V}_{\text{range}, 20\text{d}}} \ge 1.80$$
  where $\bar{V}_{\text{range}, 20\text{d}}$ is the 20-day average volume during the same opening window.
  On the breakout bar ($t > t_{\text{range\_end}}$):
  $$V_{\text{breakout}} \ge 1.50 \times \text{SMA}_{5}(V)$$

#### 3. Entry & Trigger Rules
* **Long Entry Trigger**:
  $$C_t > R_H + \delta_{\text{buffer}} \quad \text{where } \delta_{\text{buffer}} = \max\left(0.02, 0.05 \cdot \text{ATR}_{14}\right)$$
  Condition: Bar $t$ must close above $R_H + \delta_{\text{buffer}}$ with positive volume surge.
* **Short Entry Trigger**:
  $$C_t < R_L - \delta_{\text{buffer}}$$
  Condition: Bar $t$ must close below $R_L - \delta_{\text{buffer}}$ with positive volume surge.

#### 4. Exit Rules & Trailing Stop
* **Stop-Loss**: Placed at the Range Midpoint:
  $$P_{\text{stop}} = \frac{R_H + R_L}{2}$$
  Alternatively, if $\Delta R < 1.0 \times \text{ATR}_{14}$, stop is placed at opposite side ($R_L$ for Long, $R_H$ for Short).
* **Take-Profit Targets**:
  * Scale out $50\%$ at $1.5R$: $\text{Target}_1 = P_{\text{entry}} + 1.5 \cdot (P_{\text{entry}} - P_{\text{stop}})$.
  * Remainder $50\%$ rides trailing stop: $P_{\text{trail}} = \text{High}_{\text{peak}} - 1.2 \cdot \text{ATR}_{14}$.
* **Time Expiration**: Strategy does not enter new trades after 11:30 ET; any open trade is closed at 15:45 ET.

---

### 3.2 Strategy 2: VWAP Trend Pullback & Continuation

#### 1. Economic Logic & Alpha Thesis
Volume Weighted Average Price (VWAP) represents the benchmark execution target for institutional algorithms (TWAP/VWAP execution algos). In an established trend, institutional buyers defend VWAP as an attractive reload price, causing mean reversion back to the trend after a minor pullback. This strategy identifies strong intraday trends and enters on low-volume retests of VWAP, catching the continuation leg with asymmetric risk/reward.

#### 2. Mathematical Formulation
* **Anchored Intraday VWAP** (anchored at 09:30:00 ET):
  $$\text{VWAP}_t = \frac{\sum_{i=1}^t P_{\text{typical}, i} \cdot V_i}{\sum_{i=1}^t V_i} \quad \text{where } P_{\text{typical}, i} = \frac{H_i + L_i + C_i}{3}$$
* **VWAP Standard Deviation Bands**:
  $$\sigma_{\text{VWAP}, t}^2 = \frac{\sum_{i=1}^t V_i \cdot \left(P_{\text{typical}, i} - \text{VWAP}_t\right)^2}{\sum_{i=1}^t V_i}$$
  $$\text{Upper Band}_1 = \text{VWAP}_t + 1.0 \cdot \sigma_t, \quad \text{Upper Band}_2 = \text{VWAP}_t + 2.0 \cdot \sigma_t$$
  $$\text{Lower Band}_1 = \text{VWAP}_t - 1.0 \cdot \sigma_t, \quad \text{Lower Band}_2 = \text{VWAP}_t - 2.0 \cdot \sigma_t$$
* **Trend Confirmation Filters**:
  * Moving Average Alignment: $\text{EMA}_{20}(1\text{m}) > \text{EMA}_{50}(1\text{m})$ for Bullish Trend; $\text{EMA}_{20} < \text{EMA}_{50}$ for Bearish Trend.
  * VWAP Slope:
    $$\text{Slope}_{\text{VWAP}} = \frac{\text{VWAP}_t - \text{VWAP}_{t-5}}{5 \times \text{VWAP}_t} \times 10^4 \ge +1.5 \text{ bps/min (Bullish)}$$
  * Price Prior Impulse: Asset must have traded above $\text{Upper Band}_1$ within the preceding 15 minutes, confirming buying dominance.

#### 3. Entry Trigger (Pullback & High-Volume Bounce)
* **Pullback Retest Zone**:
  Price retraces into the VWAP buffer zone:
  $$P_t \in \left[ \text{VWAP}_t - 0.2 \cdot \sigma_t, \quad \text{VWAP}_t + 0.3 \cdot \sigma_t \right]$$
* **Volume Drying on Retest**:
  Volume during the pullback bars must decline:
  $$V_{\text{pullback}} < 0.85 \times \text{SMA}_{10}(V)$$
* **Confirmation Bounce Candle**:
  Candle touching VWAP must close green with high volume:
  1. $C_t > O_t$ and $C_t > \text{VWAP}_t$
  2. Lower shadow/wick: $\min(O_t, C_t) - L_t \ge 0.40 \cdot (H_t - L_t)$ (hammer rejection)
  3. Resurgent Volume: $V_t \ge 1.30 \times \text{SMA}_{10}(V)$

#### 4. Exit Rules & Stop-Loss
* **Stop-Loss**: Placed just below VWAP support:
  $$P_{\text{stop}} = \text{VWAP}_t - 0.50 \cdot \sigma_t \quad (\text{or below swing low of pullback})$$
* **Take-Profit**:
  * Target 1: Upper Band 1 ($\text{VWAP} + 1.0\sigma$) — scale out $40\%$.
  * Target 2: Upper Band 2 ($\text{VWAP} + 2.0\sigma$) — scale out $60\%$.
  * Stop ratchets to breakeven once Target 1 is achieved.

---

### 3.3 Strategy 3: Catalyst News Momentum Breakout

#### 1. Economic Logic & Alpha Thesis
Earnings releases, guidance updates, FDA approvals, strategic partnerships, and M&A developments trigger instantaneous repricing. By ingesting AlpacaRelay's real-time Benzinga news feed (`T: "n"`), the system parses headlines, applies financial NLP sentiment scoring, confirms that institutional market orders are driving price velocity on the tape, and executes within seconds of dissemination before retail or slower institutional algorithms fully price the catalyst.

#### 2. AlpacaRelay Ingestion & Sentiment Scoring Model
* **Data Ingestion Schema**:
  ```json
  {
    "T": "n",
    "id": 12849102,
    "headline": "NVIDIA Partners with Major Hyperscaler for Next-Gen Blackwell Architecture Deployment; Beats Revenue Projections",
    "summary": "...",
    "symbols": ["NVDA"],
    "created_at": "2026-09-19T14:32:01.120Z"
  }
  ```
* **NLP Lexicon & Sentiment Formula**:
  The news engine tokenizes the headline, matches against a domain-specific financial sentiment dictionary, and applies context modifier multipliers (e.g., negations, intensifiers):
  $$S_{\text{raw}} = \sum_{w_i \in \mathcal{H}} w_i \cdot \mu_{\text{context}}(w_i)$$
  Normalized via hyperbolic tangent:
  $$S = \tanh\left( \frac{S_{\text{raw}}}{3.0} \right) \in [-1.0, +1.0]$$

#### Sentiment Dictionaries & Weightings:
* **Bullish Tokens ($+0.4$ to $+1.0$)**: `"beats estimates" (+0.9)`, `"raises guidance" (+1.0)`, `"fda approves" (+1.0)`, `"awarded contract" (+0.8)`, `"partnership" (+0.6)`, `"record earnings" (+0.9)`, `"stock buyback" (+0.7)`, `"upgraded" (+0.5)`.
* **Bearish Tokens ($-0.4$ to $-1.0$)**: `"misses estimates" (-0.9)`, `"lowers guidance" (-1.0)`, `"fda rejects" (-1.0)`, `"secondary offering" (-0.9)`, `"sec probe" (-1.0)`, `"investigation" (-0.8)`, `"resigns" (-0.6)`, `"downgraded" (-0.5)`, `"bankruptcy" (-1.0)`.
* **Negation Inverter**: e.g., `"not approved" \implies -1.0 \times \text{weight}`.

#### 3. Tape & Volume Surge Verification
News sentiment alone is vulnerable to headline misinterpretation. The strategy requires instant market confirmation:
* Time window: Within $\tau \le 180 \text{ seconds}$ of headline timestamp.
* **Volume Spike**:
  $$V_{1\text{m}} \ge 3.50 \times \text{SMA}_{20}(V_{1\text{m}})$$
* **Price Velocity**:
  $$\frac{|P_t - P_{t-\tau}|}{\text{ATR}_{14}} \ge 1.20 \quad \text{with } \text{sign}(P_t - P_{t-\tau}) == \text{sign}(S)$$

#### 4. Execution & News Contradiction Circuit Breakers
* **Execution**: Market-with-protection order or Limit order at $P_{\text{ask}} + 0.05 \cdot \text{ATR}_{14}$.
* **Stop-Loss**: Set at the low of the news breakout candle:
  $$P_{\text{stop}} = L_{\text{catalyst\_bar}} - 0.02$$
* **News Contradiction Circuit Breaker**:
  If the account holds an open Long position in ticker $s$ and a new headline arrives with $S < -0.35$ (or Short position receives $S > +0.35$):
  1. Instantly bypass normal exit targets.
  2. Fire an immediate market order to close position.
  3. Cancel all pending orders for $s$.
  Prevents holding through catastrophic guidance cuts or adverse regulatory rulings.

---

### 3.4 Strategy 4: Statistical Mean Reversion / Exhaustion Fades

#### 1. Economic Logic & Alpha Thesis
Aggressive retail buying or short squeezes often push intraday prices to statistical extremes where liquidity dries up and market makers step in to fade the move. Strategy 4 monitors 1-minute bars for multi-standard-deviation exhaustion ($|Z| \ge 2.5$), extreme RSI overbought/oversold levels, and volume climax rejection candles, taking a counter-trend position targeting mean reversion to the 20-period moving average.

#### 2. Mathematical Formulation
* **Rolling 20-Period Mean & Standard Deviation**:
  $$\mu_{20, t} = \frac{1}{20}\sum_{i=0}^{19} C_{t-i}$$
  $$\sigma_{20, t} = \sqrt{\frac{1}{20}\sum_{i=0}^{19} (C_{t-i} - \mu_{20, t})^2}$$
* **Price Z-Score**:
  $$Z_t = \frac{C_t - \mu_{20, t}}{\sigma_{20, t}}$$
* **Relative Strength Index ($\text{RSI}_{14}$)**:
  Standard Wilder 14-period RSI:
  $$\text{RSI}_t = 100 - \frac{100}{1 + \frac{\text{Average Gain}_{14}}{\text{Average Loss}_{14}}}$$
* **Exhaustion Conditions**:
  * For **Short Exhaustion Fade** (fading overbought):
    1. $Z_t \ge +2.50$ (price is $> 2.5\sigma$ above mean, $p < 0.012$).
    2. $\text{RSI}_{14} \ge 75.0$.
    3. Volume Climax: $V_t \ge 3.0 \times \text{SMA}_{20}(V)$.
    4. Rejection Candle: Upper shadow/wick $\ge 50\%$ of candle range:
       $$H_t - \max(O_t, C_t) \ge 0.50 \cdot (H_t - L_t)$$
  * For **Long Exhaustion Fade** (fading oversold):
    1. $Z_t \le -2.50$.
    2. $\text{RSI}_{14} \le 25.0$.
    3. Volume Climax: $V_t \ge 3.0 \times \text{SMA}_{20}(V)$.
    4. Rejection Candle: Lower shadow/wick $\ge 50\%$ of candle range:
       $$\min(O_t, C_t) - L_t \ge 0.50 \cdot (H_t - L_t)$$

#### 3. Entry Trigger & Exit Targets
* **Trigger**: Enters on the close of the rejection candle or on the subsequent bar when price crosses back inside the $2.0\sigma$ Bollinger band.
* **Profit Target**: Mean reversion to the 20-period moving average:
  $$\text{Target} = \mu_{20, t}$$
* **Stop-Loss**: Placed tightly outside the extreme wick:
  $$P_{\text{stop}} = \begin{cases} H_t + 0.50 \cdot \text{ATR}_{14} & \text{for Short Fade} \\ L_t - 0.50 \cdot \text{ATR}_{14} & \text{for Long Fade} \end{cases}$$
* Risk/Reward constraint: Strategy requires $\frac{|\text{Target} - P_{\text{entry}}|}{|P_{\text{stop}} - P_{\text{entry}}|} \ge 1.80$; otherwise, the setup is skipped.

---

## Section 4: Dynamic Real-Time Adaptation Engine

The trading system is not static; it dynamically self-adapts its risk parameters, position sizing, and strategy activation based on **Market Volatility Regimes (VIX)** and **Intraday Time-of-Day Dynamics**.

```
                        +----------------------------+
                        | Dynamic Adaptation Engine  |
                        +----------------------------+
                                      |
                 +--------------------+--------------------+
                 |                                         |
                 v                                         v
     [ Real-Time VIX Module ]                  [ Time-of-Day Module ]
     Tastytrade dxFeed /vix                   ET Session Clock Invariant
         |                                                 |
         +--------------------+----------------------------+
                              |
                              v
     +-------------------------------------------------------------+
     | Active Strategy Weights | Sizing Multipliers | Stop Multipliers |
     +-------------------------------------------------------------+
```

### 4.1 Real-Time VIX Adaptation Engine

The system polls `GET /vix` from AlpacaRelay (Tastytrade dxFeed spot VIX) every 30 seconds. Based on the spot print, the engine assigns one of 4 volatility regimes:

| Volatility Regime | VIX Range | Market Characteristics | Position Sizing Multiplier ($K_{\text{vix}}$) | Stop-Loss Multiplier | Strategy Prioritization |
|---|---|---|---|---|---|
| **Regime I: Low / Calm** | $\text{VIX} < 15.0$ | Low intraday beta, rangebound chop, slow grinds. | $1.20\times$ (max $1.20\%$) | $0.85\times$ base ATR | Favor Mean Reversion (Strat 4) & VWAP Pullbacks (Strat 2); require tighter confirmation for ORB (Strat 1). |
| **Regime II: Normal** | $15.0 \le \text{VIX} < 25.0$ | Healthy trending behavior, normal liquidity. | $1.00\times$ ($1.00\%$) | $1.00\times$ base ATR | Balanced allocation across all 4 strategies. |
| **Regime III: Elevated** | $25.0 \le \text{VIX} < 35.0$ | Wide swings, elevated spread, frequent stop runs. | $0.70\times$ ($0.70\%$) | $1.40\times$ base ATR | Widen stops; prioritize News Catalyst (Strat 3) and ORB (Strat 1); reduce Mean Reversion. |
| **Regime IV: Crisis / Extreme** | $\text{VIX} \ge 35.0$ | Severe tail risk, liquidity gaps, erratic reversals. | $0.35\times$ ($0.35\%$) | $2.00\times$ base ATR | Defensive posture; halve max concurrent positions; disable trend breakout chases. |

#### Invariant Dollar Risk Sizing Equation:
To keep total dollar risk $R_{\$}$ constant at $\$500$ regardless of market volatility:
$$\text{Shares} = \left\lfloor \frac{R_{\$, \text{target}} \cdot K_{\text{vix}}}{\text{ATR}_{14} \cdot M_{\text{stop}}} \right\rfloor$$
As VIX doubles, $\text{ATR}$ naturally expands and $K_{\text{vix}}$ contracts, causing share sizing to automatically shrink quadratically with volatility, preventing blowout risk during market panic!

### 4.2 Time-of-Day Execution Regimes

Intraday equity markets exhibit a pronounced U-shaped volume and volatility profile. The engine modulates trading behavior across 5 distinct phases:

```
08:00          09:30       10:00            11:30                14:00            15:00       15:45   16:00
  |--------------|-----------|----------------|--------------------|----------------|-----------|-------|
    PRE-MARKET     OPEN FLUSH   TREND CONT.       MIDDAY CHOP        AFTERNOON PUSH   POWER HOUR  FLAT    CLOSE
     Scan Only     Establish    Strat 1 & 2       Strat 4 Fade       Re-engage        Scalp Only  0 Pos   EOD
```

#### Phase Breakdown:
1. **Pre-Market Scan (08:00–09:30 ET)**:
   * Engine State: `PRE_MARKET`.
   * Execution: **BLOCKED**. No orders permitted.
   * Actions: Ingest overnight headlines via AlpacaRelay; scan for pre-market gap leaders ($|\text{Gap}| \ge 1.5\%$, volume $\ge 100\text{k}$ shares); register watchlists; fetch spot VIX.
2. **Open Volatility Flush (09:30–10:00 ET)**:
   * Engine State: `OPEN_VOLATILITY_FLUSH`.
   * Execution: Sizing $1.0\times$.
   * Actions: Strategy 1 establishes the 5-min/15-min Opening Range. Microstructure guardrail: reject orders if Bid-Ask spread $> 8 \text{ bps}$. Strategy 4 (Mean Reversion) is disabled to prevent stepping in front of opening institutional imbalances.
3. **Trend Continuation Window (10:00–11:30 ET)**:
   * Engine State: `TREND_CONTINUATION`.
   * Execution: **Prime Execution Window**. $100\%$ capital weighting.
   * Actions: Strategy 1 (ORB continuations) and Strategy 2 (VWAP Trend Pullbacks) operate at full weight. News breakout signals executed.
4. **Midday Chop Defense (11:30–14:00 ET)**:
   * Engine State: `MIDDAY_CHOP_DEFENSE`.
   * Execution: Defensively throttled. Sizing scaled down to $50\%$ ($K_{\text{time}} = 0.50$).
   * Actions: Breakout hurdles raised by $+50\%$ to avoid whipsaw fakeouts. Strategy 4 (Statistical Mean Reversion) active on extreme moves ($|Z| \ge 2.5$). If overall market volume drops below 30-day average by $> 50\%$, engine enters `PAUSE_TRADING` until 14:00 ET.
5. **Power Hour & Automated Flattening (15:00–16:00 ET)**:
   * **15:00–15:45 ET (`POWER_HOUR_SCALP`)**: Final momentum scalps allowed. Strict max holding time 15 minutes.
   * **15:45:00 ET (`ENTRY_LOCKOUT`)**: All new strategy entry signals blocked.
   * **15:50:00 ET (`ORDER_PURGE`)**: Working limit orders cancelled. Stops tightened to locked-in profit.
   * **15:55:00 ET (`MANDATORY_FLATTEN`)**: Market orders force-liquidate all remaining open positions.
   * **15:58:00 ET (`AUDIT_VERIFICATION`)**: Confirm $0$ open positions, $0$ working orders, $100\%$ cash.

### 4.3 Cross-Strategy Resource Allocation & Concurrency Arbitration

When multiple strategies emit signals simultaneously on the same or correlated tickers:
1. **Maximum Concurrent Positions**: Hard cap of **3 concurrent positions** across the entire portfolio ($R_{\$, \text{total}} \le 3 \times \$500 = \$1,500$ max portfolio risk at any instant).
2. **Sector Exposure Diversification**: Maximum 1 position per sector (e.g. XLK, XLC, XLY) to prevent correlated tech-wreck drawdowns.
3. **Priority Hierarchy for Signal Collision**:
   If two strategies signal simultaneously:
   $$\text{Priority}: \text{Strat 3 (News Catalyst)} > \text{Strat 1 (ORB Breakout)} > \text{Strat 2 (VWAP Pullback)} > \text{Strat 4 (Mean Reversion)}$$
   *Rationale*: Fresh news catalysts have immediate predictive validity and highest momentum velocity.

---

## Section 5: Performance Evaluation, Sharpe Maximization & Monday Dry Run

### 5.1 Quantitative Performance Metrics

To prove algorithmic edge and satisfy acceptance criteria, the system records trade-level metrics and computes institutional portfolio ratios:

1. **Annualized Sharpe Ratio**:
   $$\text{Sharpe} = \frac{\bar{R}_{\text{intraday}} - R_f}{\sigma_{R}} \times \sqrt{252}$$
   Where $\bar{R}_{\text{intraday}}$ is average daily return, $\sigma_R$ is daily return volatility, and $R_f$ is risk-free rate ($0\%$/intraday). Target: $\text{Sharpe} \ge 2.0$.
2. **Sortino Ratio**:
   $$\text{Sortino} = \frac{\bar{R}_{\text{intraday}} - R_f}{\sigma_{\text{downside}}} \times \sqrt{252} \quad \text{where } \sigma_{\text{downside}} = \sqrt{\frac{1}{N}\sum \min(0, R_i)^2}$$
3. **Calmar Ratio**:
   $$\text{Calmar} = \frac{\text{Annualized Return}}{\text{Maximum Intraday Drawdown}}$$
4. **Profit Factor**:
   $$\text{Profit Factor} = \frac{\sum \text{Gross Profits}}{\sum |\text{Gross Losses}|} \ge 1.75$$
5. **Win Rate ($W$) & Payoff Ratio ($P$)**:
   $$\text{Expected Value } \mathbb{E}[R] = W \times \text{Avg Win} - (1 - W) \times \text{Avg Loss} > 0.35R$$

### 5.2 Deterministic Test Harness & Synthetic Replay Scenarios

The testing framework must validate the strategy engine across 5 deterministic test tiers:

* **Tier 1: Account State & Order Lifecycle Unit Tests**:
  * Verify cash deduction, buying power expansion (4x PDT), fill transitions (`PENDING_NEW` $\to$ `NEW` $\to$ `FILLED`), regulatory fee deductions.
* **Tier 2: Risk Circuit Breaker Enforcement Tests**:
  * Simulate loss hitting $-\$1,500.01$: verify immediate market liquidation, order cancellation, and rejection of new incoming signals.
  * Verify $2\%$ per-position max risk constraint.
* **Tier 3: Strategy Signal & Indicator Math Tests**:
  * Replay deterministic 1-minute OHLCV bars:
    - ORB high/low calculation and volume surge filter.
    - Anchored VWAP and $\pm 1\sigma, \pm 2\sigma$ band accuracy.
    - News NLP sentiment scoring on sample headline corpus.
    - Z-Score and RSI 14-period divergence calculations.
* **Tier 4: Dynamic Regime Adaptation Tests**:
  * Inject VIX prints ($12.0 \to 21.0 \to 32.0 \to 42.0$): verify sizing transitions ($1.2\times \to 1.0\times \to 0.7\times \to 0.35\times$).
  * Advance simulated session clock: verify transition from Open Flush $\to$ Trend Continuation $\to$ Midday Chop $\to$ 15:45 Lockout $\to$ 15:55 Flatten.
* **Tier 5: Monday Market Open Simulation Dry Run**:
  * Execute a complete 09:15 to 16:05 ET simulated Monday session at accelerated live speed (e.g. 10x or 60x replay of AlpacaRelay captured feed), verifying end-to-end signal ingestion, trade executions, bracket trailing stops, and 15:58 zero-position audit.

---

## Section 6: Strategy Specification Summary Table

| Parameter / Dimension | Strategy 1: ORB | Strategy 2: VWAP Pullback | Strategy 3: News Catalyst | Strategy 4: Mean Reversion |
|---|---|---|---|---|
| **Primary Indicator** | Range High / Low ($R_H, R_L$) | Anchored VWAP + $\sigma$ Bands | NLP Sentiment $S$ + Velocity | 20-bar $Z$-Score + RSI-14 |
| **Timeframe** | 5-min or 15-min Opening Range | 1-min / 5-min Bars | Event-driven (seconds to 1-min) | 1-min Bars |
| **Active Market Window** | 09:35 / 09:45 – 11:30 ET | 10:00 – 15:45 ET | 09:30 – 15:45 ET | 11:30 – 15:00 ET (Chop/Extremes) |
| **Volume Confirmation** | $\text{RVOL} \ge 1.8\times$, Breakout $> 1.5\times$ | Pullback $< 0.85\times$, Bounce $> 1.3\times$ | Tape Volume $> 3.5\times \text{SMA}_{20}$ | Volume Climax $> 3.0\times \text{SMA}_{20}$ |
| **Entry Rule** | Close above $R_H$ or below $R_L$ | Touch VWAP $+0.3\sigma$ + green hammer | $S \ge 0.5$ + price velocity $> 1.2\text{ATR}$ | $\|Z\| \ge 2.5$, $\text{RSI} > 75$ or $< 25$, wick $\ge 50\%$ |
| **Stop-Loss Level** | Range Midpoint or opposite side | Below VWAP $-0.5\sigma$ | Low of catalyst breakout bar | Beyond extreme wick $+0.5\text{ATR}$ |
| **Take-Profit Target** | $1.5R$ ($50\%$) & $2.5R$ / Trailing | Upper Band 1 ($1\sigma$) & Band 2 ($2\sigma$) | $2.0R$ & Trailing Stop | Mean Reversion to 20-SMA ($\mu_{20}$) |
| **Base Risk ($R_{\$}$)** | $\$500$ ($1.0\%$ account equity) | $\$500$ ($1.0\%$ account equity) | $\$500$ ($1.0\%$ account equity) | $\$500$ ($1.0\%$ account equity) |
| **VIX Scaling** | Contracts size in VIX $>25$ | Robust in VIX 15–30 | High alpha in high VIX ($>25$) | Favored in low VIX ($<15$) |
| **Emergency Exit** | Hit stop or 15:55 ET | Close below VWAP $-1.0\sigma$ | News contradiction ($S < -0.35$) | Trend continuation breakout ($|Z| > 3.2$) |

---

## Conclusion & Implementation Next Steps

This specification document provides the rigorous mathematical equations, state transitions, risk invariants, and dynamic adaptation rules required for the implementation team. 

Next downstream milestones:
1. **Implementation Track**: Translate these mathematical models and state machines into high-performance Python modules (`engine/`, `risk/`, `strategies/`, `adaptation/`).
2. **Testing Track**: Implement synthetic mock feeds and unit/integration test suites based on the 5-tier verification matrix.
3. **UI Streaming Track**: Expose engine state (Cash, Equity, PnL, Strategies, Active Position Drawer) via WebSocket to the Apple Music Next.js interface.
