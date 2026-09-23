# Architecture Remediation Plan: AutonomousDayTrader

## 1. Executive Summary & Root Causes
Based on empirical quantitative forensics from live production trading (7 trades, 0 wins, -$201.68 realized loss), three structural defects were identified:
1. **Context Blindness (89.4% of total loss)**: TSLA SHORT @ 09:31 ET (-$68.30) and AAPL SHORT @ 10:09 ET (-$112.04) shorted high-beta mega-caps into a market-wide morning rally without index confirmation.
2. **Unrealistic Bracket Geometry (100% Target 1 failure)**: Hardcoded 1.5R Target 1 and 2.5R Target 2 are mathematically unachievable on intraday 1m/5m bars before noise triggers stops. In addition, `main.py:958-959` explicitly erased strategy targets, overriding them with 1.5R/2.5R.
3. **Strategy Trigger Defects**:
   - **ORB**: Buys shooting stars and shorts hammers at range boundaries with no candle quality/wick check (CLV), and midpoint stops balloon risk on extended candles.
   - **News Momentum**: Crude substring matching (`w in text`) matches words like `emission`, volume baseline defaults to 100k at 09:31 (treating normal 650k open volume as a 6.5x surge), and lacks candle price direction confirmation.
   - **Mean Reversion**: Starved under moderate VIX (14–16) due to mathematically incompatible conditions (50% wick + 1.2 R:R to 20-SMA with stop at high + 0.5 ATR).

---

## 2. Structural Remediation Design

### Component A: Market Index Trend Filter (`backend/app/core/market_filter.py`)
- Ingest closed 1-minute bars for `SPY` and `QQQ`.
- Track Anchored VWAP (anchored to 09:30 ET) and EMAs (9 and 21 periods) for both indices.
- Calculate Market Regime:
  - `BULLISH`: SPY > VWAP and QQQ > VWAP, with EMA9 >= EMA21 on at least one.
  - `BEARISH`: SPY < VWAP and QQQ < VWAP, with EMA9 <= EMA21 on at least one.
  - `NEUTRAL`: Divergence (e.g. SPY > VWAP but QQQ < VWAP) or within tight noise band (±0.03% of VWAP).
  - `UNKNOWN`: Pre-market, missing data, or data age > 120s (fail-closed).
- Policy Matrix:
  - `ORB` & `VWAP Pullback`: BUY requires `BULLISH`; SELL requires `BEARISH`. Reject if `NEUTRAL` or contradictory.
  - `News Momentum`: Reject counter-trend trades unless sentiment is extreme ($|S| \ge 0.85$) and volume surge $\ge 5.0\times$.
  - `Mean Reversion`: Permits counter-trend fades on overextended bars.
- Wire into `backend/app/strategies/adaptation.py` (`evaluate_signal_admission`) and `backend/app/main.py`.

### Component B: Restructure Profit Target & Bracket Management (`backend/app/core/bracket.py` & `main.py`)
- Change default Target 1 from 1.5R to **0.80R** (or **1.00R**), banking 50% profit.
- Change default Target 2 to **1.80R** (or **2.00R**) or trailing ATR runner.
- Update `main.py:958-959`: Pass `target_1_override=signal.take_profit_1` and `target_2_override=signal.take_profit_2` for ALL strategies so strategy-defined realistic targets are respected.
- Ensure trailing stop remains strictly gated to `TARGET_1_HIT`.
- Scale breakeven buffer with price: `max(0.04, round(entry_price * 0.0005, 2))`.

### Component C: Refine Entry Conditions in Strategies
1. **ORB (`backend/app/strategies/orb.py`)**:
   - Add Close Location Value ($\text{CLV} = \frac{\text{close} - \text{low}}{\text{high} - \text{low}}$). Require $\text{CLV} \ge 0.65$ for BUY, $\le 0.35$ for SELL.
   - Add Bar Range Cap: $\text{bar.high} - \text{bar.low} \le 2.2 \times \text{ATR}$.
   - Add Extension Cap: entry close must not exceed breakout level by $> 1.0 \times \text{ATR}$.
   - Update target defaults to 0.8R (T1) and 1.8R (T2).
2. **News Momentum (`backend/app/strategies/news_momentum.py`)**:
   - Use regex word-boundary matching `re.search(rf"\b{re.escape(w)}\b", text)` in `score_news_sentiment` to prevent false token matches.
   - Enforce candle direction confirmation: `bar.close > bar.open` for BUY; `bar.close < bar.open` for SELL.
   - Fix 09:31 volume baseline: if `recent_bars` has $< 5$ bars, enforce a volume floor of $500,000$ shares (or enforce a 5-minute open volatility lockout 09:30–09:35 ET unless volume is truly extraordinary).
   - Update target defaults to 0.8R (T1) and 1.8R (T2).
3. **Mean Reversion (`backend/app/strategies/mean_reversion.py`)**:
   - Calibrate for moderate VIX (14–16):
     - $Z$-score threshold: $2.00$ (down from $2.50$).
     - RSI thresholds: $70.0$ / $30.0$ (adjusted from $75.0$ / $25.0$).
     - Volume surge: $1.75\times$ (down from $3.0\times$).
     - Rejection wick ratio: $35\%$ (down from $50\%$).
     - Stop placement: $\text{extreme} \pm 0.15 \times \text{ATR}$ (down from $0.50 \times \text{ATR}$).
     - R:R hurdle: $\ge 1.0$ (down from $1.2$).
   - Set Target 1 to 20-SMA mean, Target 2 to opposite band or $1.5R$.

---

## 3. Verification & Safety Constraints
- Strict adherence to Institutional Risk Limits: $1,500 daily loss limit, $25,000 position cap, $0.40\% - 4.00\%$ stop guardrails.
- Zero lookahead bias: all indicators computed strictly on closed bars.
- 100% pytest pass rate in `backend/tests`.
- Zero orphaned processes or listening ports.
