# Technical Analysis: Requirements R2 & R3 Architecture & Calibration Survey
**Author**: Explorer 2 (Strategies & Regime Explorer)  
**Target Project**: AutonomousDayTrader  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_2_strategies_regime/`  
**Date**: 2026-09-23  

---

## 1. Executive Summary & Quantitative Rationale

In the previous development phase, `AutonomousDayTrader` successfully solved risk runaway drawdowns, but introduced a severe **Filter-Stacking Bottleneck** that dropped live trade frequency to ~0 trades/day:
1. **Regime Freezing in NEUTRAL**:
   `MarketTrendFilter.is_signal_permitted` unconditionally locked out `orb` and `vwap_pullback` whenever market trend was classified as `NEUTRAL` (when SPY/QQQ oscillate around VWAP). Simultaneously, `mean_reversion` had such extreme hurdle rates ($|Z| \ge 2.00$, Volume Climax $\ge 1.75\times$, Wick Rejection $\ge 35\%$) that it virtually never triggered under moderate VIX (14–16).
2. **Volume Climax Delusions in News Momentum**:
   `NewsMomentumStrategy` demanded a $3.50\times$ volume surge on 1-minute bars. In live equities markets, a $3.5\times$ surge candle represents the final exhaustion print of high-frequency algorithmic repricing, forcing the bot to buy the exact top or never enter.
3. **Sentiment NLP Substring Leakage**:
   `FinancialSentimentScorer` (`backend/app/ingestion/sentiment.py`) performed naive substring matching without word boundaries (`phrase in text`, `any(k in text)`), causing critical misclassifications (e.g., `"Tech Sector Rally"` matched `"sec"` and was categorized as `LEGAL_INVESTIGATION`; `"Contract window closed"` matched `"contract win"` and was categorized as `PARTNERSHIP_CONTRACT`).
4. **Idiosyncratic Breakout Lockout**:
   Single stocks exhibiting institutional decoupling (e.g., massive earnings surprises or news catalysts with $\text{RVOL} \ge 2.20\times$) during choppy market regimes were rejected because `is_signal_permitted` had no mechanism to recognize idiosyncratic alpha in `NEUTRAL` regimes.

This analysis provides the complete architectural roadmap to implement **Requirement R2** (Regime-Separated Strategy Execution) and **Requirement R3** (Microstructure & Indicator Calibration) while preserving all non-negotiable risk invariants ($1,500 daily circuit breaker, $25,000 position cap, 0.4%–4.0% stops, EOD flat book).

---

## 2. Codebase Investigation: Market Filter & Strategies

### 2.1 `backend/app/core/market_filter.py`
- **Regime Classification Logic** (lines 178–233):
  - **Early Open Convergence** (first 3 minutes):
    SPY and QQQ compared against opening price. Both green $\to$ `BULLISH`; both red $\to$ `BEARISH`; divergent $\to$ `NEUTRAL`.
  - **Standard Operation** (bars $\ge 3$):
    - `IndexState.is_bullish()`: $\text{Price} > \text{VWAP} \times (1 + \text{deadband})$ where deadband is $0.0003$ ($3\text{ bps}$), and $\text{EMA}_9 \ge \text{EMA}_{21}$.
    - `IndexState.is_bearish()`: $\text{Price} < \text{VWAP} \times (1 - \text{deadband})$, and $\text{EMA}_9 \le \text{EMA}_{21}$.
    - Both SPY and QQQ bullish $\to$ `MarketTrend.BULLISH`.
    - Both SPY and QQQ bearish $\to$ `MarketTrend.BEARISH`.
    - Divergent or inside VWAP deadband $\to$ `MarketTrend.NEUTRAL`.
  - **Fail-Closed Staleness Protection** (lines 185–202):
    Rejects future timestamps ($\text{elapsed} < 0\text{s}$) with `"FUTURE_INDEX_DATA"` and stale data ($\text{elapsed} > 120\text{s}$) with `"STALE_INDEX_DATA"`, returning `MarketTrend.UNKNOWN`.
- **Current Strategy Permission Logic** (`is_signal_permitted`, lines 248–310):
  ```python
  # 2. ORB & VWAP Pullback
  if strat in ("orb", "vwap_pullback"):
      if trend == MarketTrend.BULLISH and not is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Cannot open SHORT on {symbol} when market trend is BULLISH"
      if trend == MarketTrend.BEARISH and is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Cannot open LONG on {symbol} when market trend is BEARISH"
      if trend == MarketTrend.NEUTRAL:
          return False, f"INDEX_FILTER_DENIED: {strat.upper()} requires directional market trend (currently NEUTRAL)"

  # 3. News Momentum
  elif strat == "news_momentum":
      if trend == MarketTrend.BULLISH and not is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Shorting {symbol} on news denied during BULLISH market rally"
      if trend == MarketTrend.BEARISH and is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Buying {symbol} on news denied during BEARISH market decline"
      if trend == MarketTrend.NEUTRAL:
          return False, f"INDEX_FILTER_DENIED: NEWS_MOMENTUM requires directional index alignment or extreme catalyst (currently NEUTRAL)"

  # 4. Statistical Mean Reversion
  elif strat == "mean_reversion":
      if trend == MarketTrend.BULLISH and not is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Shorting overbought {symbol} denied during strong BULLISH market rally"
      if trend == MarketTrend.BEARISH and is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Buying oversold {symbol} (catching falling knife) denied during strong BEARISH market decline"
  ```
- **Identified Deficiencies**:
  1. `is_signal_permitted` does not accept `rvol: Optional[float] = None`.
  2. In `NEUTRAL`, `orb` and `news_momentum` are hard-rejected regardless of single-stock idiosyncratic volume ($RVOL$).
  3. `mean_reversion` in `NEUTRAL` falls through to `return True` (line 309), which is correct in policy, but was starved at the strategy generation layer.

---

### 2.2 `backend/app/strategies/orb.py`
- **Core Mechanics**:
  - Establishes 5-minute opening range from bars between 09:30 and 09:34 ET (`range_high`, `range_low`, `range_midpoint`).
  - Signal evaluation (`evaluate_orb_signal`, lines 17–61):
    - Requires $\text{RVOL} \ge 1.80$.
    - $\text{CLV} = \frac{\text{Close} - \text{Low}}{\text{High} - \text{Low}}$. BUY requires $\text{CLV} \ge 0.65$; SELL requires $\text{CLV} \le 0.35$.
    - Close must exceed range high / low.
  - ATR filters (lines 207–216):
    - Bar range cap: $\text{CandleRange} \le 2.2 \times \text{ATR}$.
    - Extension cap: $\text{BreakoutDistance} \le 1.0 \times \text{ATR}$.
  - Stop Loss & Target Geometry (lines 218–239):
    - Stop at `range_midpoint`, widened to $0.4\%$ floor via `resolve_stop`.
    - Target 1 at $0.8R$, Target 2 at $1.8R$.
  - Output Signal (line 254):
    `sig.rvol = rvol` is already attached to the emitted `SignalEvent`.

---

### 2.3 `backend/app/strategies/vwap_pullback.py`
- **Core Mechanics**:
  - Anchored VWAP and standard deviation calculated from 09:30 ET bars.
  - EMA Trend Filter (fast 20 / slow 50) determines trend direction.
  - Pullback zone: $[\text{VWAP} - 0.2\sigma, \text{VWAP} + 0.3\sigma]$ for longs.
  - Confirmation: Green bounce candle $(\text{Close} > \text{Open}, \text{Close} \ge \text{VWAP})$ with hammer wick ($\ge 30\%$ range) OR volume surge ($\ge 1.20\times \text{SMA}_{10}\text{ vol}$).
  - Stop at $\text{VWAP} - 0.50\sigma$, clamped to $0.4\%$ floor. Target 1 at $0.80R$, Target 2 at $1.80R$.
  - Behavior in Regimes: Requires macro directional trend; should remain disabled in `NEUTRAL` chop.

---

### 2.4 `backend/app/strategies/news_momentum.py`
- **Core Mechanics**:
  - Listens to `on_news` headlines and calculates sentiment score via `score_news_sentiment(headline)`.
  - Contradiction circuit breaker: Liquidation if an opposing headline arrives while holding position.
  - Pending catalysts stored with 180s TTL.
  - Entry Trigger on `on_bar` (lines 224–239):
    - Computes volume ratio: $\text{vol\_ratio} = \frac{\text{bar.volume}}{\text{SMA}_{20}\text{ volume}}$.
    - Current threshold: `volume_surge_multiplier = 3.50`.
    - Requires candle direction alignment ($\text{Close} > \text{Open}$ for BUY, $\text{Close} < \text{Open}$ for SELL).
  - Output Signal (lines 279–280):
    - `sig.catalyst_sentiment = cat.sentiment`
    - `sig.volume_surge = vol_ratio`
- **Identified Deficiencies**:
  - `volume_surge_multiplier = 3.50` is excessively high, requiring extreme volume spikes that coincide with move exhaustion. Lowering to $2.00\times$ captures earlier momentum expansion.
  - `sig.rvol` is currently not explicitly populated, though `sig.volume_surge` carries `vol_ratio`. Populating `sig.rvol = vol_ratio` creates clean polymorphism with ORB.

---

### 2.5 `backend/app/ingestion/sentiment.py` & NLP Parsing Defect
- **Empirical Demonstration of Defect**:
  When tested against real-world headlines, the current `FinancialSentimentScorer` demonstrates severe false positives:
  ```bash
  python3 -c "from backend.app.ingestion.sentiment import sentiment_scorer; print(sentiment_scorer.score('Apple Leads Tech Sector Rally After Strong Demand'))"
  # Output: (0.2449, 0.5, <CatalystCategory.LEGAL_INVESTIGATION: 'LEGAL_INVESTIGATION'>)
  
  python3 -c "from backend.app.ingestion.sentiment import sentiment_scorer; print(sentiment_scorer.score('Contract window closed today'))"
  # Output: (0.537, 1.0, <CatalystCategory.PARTNERSHIP_CONTRACT: 'PARTNERSHIP_CONTRACT'>)
  ```
- **Root Causes**:
  1. Multi-word phrase matching (lines 162, 167):
     `if " " in phrase and phrase in text:` performs simple substring matching without boundary checks. `"contract win"` matches inside `"contract window"`.
  2. Category keyword matching (lines 209–222):
     `if any(k in text for k in ("sec", "probe", ...)):` matches `"sec"` inside `"sector"`, `"second"`, `"security"`, or `"consecutive"`, wrongly classifying bullish tech sector news as `LEGAL_INVESTIGATION`!
- **Remediation Specification**:
  All multi-word phrases and category keywords must use compiled regex word boundaries `r"\b" + re.escape(word) + r"\b"`.

---

### 2.6 `backend/app/strategies/mean_reversion.py`
- **Core Mechanics**:
  - Computes 20-period moving average (20-SMA), standard deviation ($\sigma$), and Z-score:
    $$Z = \frac{\text{Close} - \text{SMA}_{20}}{\sigma_{20}}$$
  - Strategy parameters (lines 62–71):
    - `z_threshold = 2.00`
    - `volume_climax_multiplier = 1.75`
    - `min_wick_ratio = 0.35`
    - `rsi_overbought = 70.0`, `rsi_oversold = 30.0`
    - `min_rr_ratio = 1.00`
  - Targets reversion back to the 20-SMA: $\text{Target} = \text{round}(\text{mean}, 4)$.
- **Why Mean Reversion was Starved**:
  - For normal intraday 1-minute bars, $|Z| \ge 2.00$ corresponds to the outer $4.5\%$ of the distribution ($\approx 9$ bars across a 390-minute session).
  - Requiring $|Z| \ge 2.00$ **AND** Volume $\ge 1.75\times \text{SMA}_{20}$ **AND** Wick $\ge 35\%$ reduced the joint probability to $< 0.05\%$ ($< 0.2$ occurrences per symbol/day).
  - Under moderate VIX (14–16), individual megacap stocks rarely print such extreme exhaustion wicks on 1-minute bars without catalyst news.
- **Microstructure Calibration**:
  - `z_threshold`: $2.00 \to 1.65$ ($\pm 1.65\sigma$ bands, capturing the 90% confidence envelope).
  - `volume_climax_multiplier`: $1.75 \to 1.30$ (healthy $30\%$ volume expansion above average).
  - `min_wick_ratio`: $0.35 \to 0.30$ ($30\%$ rejection wick confirms buyers/sellers stepping in).

---

### 2.7 `backend/app/strategies/adaptation.py` & `base.py`
- **`SignalEvent` Model** (`backend/app/strategies/base.py`, lines 23–43):
  Currently defines standard execution fields (`symbol`, `side`, `order_type`, `entry_price`, `stop_loss`, `take_profit_1`, `take_profit_2`, `strategy_id`, `confidence`, `reason`, `timestamp`, `target_qty`).
  Dynamic attributes (`catalyst_sentiment`, `volume_surge`, `rvol`) were attached ad-hoc via `setattr`.
  Explicitly declaring these optional attributes in `SignalEvent` with default `None` guarantees type safety and uniform introspection.
- **Signal Admission Pipeline** (`backend/app/strategies/adaptation.py`, lines 283–297):
  ```python
  # 0. Market Index Trend Filter Check
  if self.market_filter is not None:
      catalyst_sentiment = getattr(signal, "catalyst_sentiment", None)
      volume_surge = getattr(signal, "volume_surge", None)
      rvol = getattr(signal, "rvol", None)
      permitted, reason = self.market_filter.is_signal_permitted(
          strategy_id=signal.strategy_id,
          side=signal.side,
          symbol=signal.symbol,
          asof=signal.timestamp,
          catalyst_sentiment=catalyst_sentiment,
          volume_surge=volume_surge,
          rvol=rvol,
      )
  ```

---

## 3. Implementation Design for Requirements R2 & R3

### 3.1 Regime-Separated Execution Policy Matrix (Requirement R2)

| Strategy | `BULLISH` Regime | `BEARISH` Regime | `NEUTRAL` Regime (Chop) | `UNKNOWN` Regime |
|---|---|---|---|---|
| **ORB** | Long BUY: **ALLOWED**<br>Short SELL: **DENIED** (`INDEX_BETA_CONTRADICTION`) | Short SELL: **ALLOWED**<br>Long BUY: **DENIED** (`INDEX_BETA_CONTRADICTION`) | Normal RVOL ($< 2.20\text{x}$): **DENIED**<br>High RVOL ($\ge 2.20\text{x}$): **ALLOWED** (`APPROVED_IDIOSYNCRATIC_BREAKOUT`) | **DENIED** (Fail-closed) |
| **VWAP Pullback** | Long BUY: **ALLOWED**<br>Short SELL: **DENIED** (`INDEX_BETA_CONTRADICTION`) | Short SELL: **ALLOWED**<br>Long BUY: **DENIED** (`INDEX_BETA_CONTRADICTION`) | **DENIED** (`INDEX_FILTER_DENIED`: requires directional trend) | **DENIED** (Fail-closed) |
| **News Momentum** | Long BUY: **ALLOWED**<br>Short SELL: **DENIED** (unless extreme catalyst) | Short SELL: **ALLOWED**<br>Long BUY: **DENIED** (unless extreme catalyst) | Normal VolSurge ($< 2.20\text{x}$): **DENIED**<br>High VolSurge / RVOL ($\ge 2.20\text{x}$): **ALLOWED** (`APPROVED_IDIOSYNCRATIC_BREAKOUT`)<br>Extreme Catalyst: **ALLOWED** | Extreme Catalyst: **ALLOWED**<br>Standard: **DENIED** |
| **Mean Reversion** | Long Fade (Dip BUY): **ALLOWED**<br>Short Fade (Rally SELL): **DENIED** (`INDEX_BETA_CONTRADICTION`) | Short Fade (Rally SELL): **ALLOWED**<br>Long Fade (Knife BUY): **DENIED** (`INDEX_BETA_CONTRADICTION`) | **FULLY ACTIVATED**<br>Both Long Fade and Short Fade allowed between $\pm 1.65\sigma$ bands and 20-SMA | **DENIED** (Fail-closed) |

---

### 3.2 Proposed Implementation Changes (Code Specification)

#### Change 1: `backend/app/core/market_filter.py`
Update `is_signal_permitted` signature and policy logic:
```python
def is_signal_permitted(
    self,
    strategy_id: str,
    side: Union[OrderSide, str],
    symbol: str,
    asof: Optional[datetime] = None,
    catalyst_sentiment: Optional[float] = None,
    volume_surge: Optional[float] = None,
    rvol: Optional[float] = None,
) -> Tuple[bool, str]:
    trend, reason = self.get_current_trend(asof)
    strat = strategy_id.lower()
    is_buy = (side == OrderSide.BUY) if isinstance(side, OrderSide) else (str(side).upper() == "BUY")

    # 1. News Momentum Extreme Catalyst Override Check
    if strat == "news_momentum":
        is_extreme = (
            catalyst_sentiment is not None
            and abs(catalyst_sentiment) >= 0.85
            and volume_surge is not None
            and volume_surge >= 5.0
        )
        if is_extreme:
            return True, f"APPROVED_EXTREME_CATALYST: News momentum overrides index with |S|={abs(catalyst_sentiment):.2f}>=0.85 and vol={volume_surge:.1f}>=5.0x"

    # Fail-closed when trend is unknown
    if trend == MarketTrend.UNKNOWN:
        return False, f"INDEX_FILTER_DENIED: Market trend UNKNOWN ({reason})"

    # 2. Idiosyncratic Decoupling Check in NEUTRAL Regime
    # High-RVOL idiosyncratic breakouts (RVOL >= 2.20x) in NEUTRAL prove institutional decoupling
    if trend == MarketTrend.NEUTRAL:
        effective_rvol = rvol if rvol is not None else volume_surge
        if strat in ("orb", "news_momentum"):
            if effective_rvol is not None and effective_rvol >= 2.20:
                return True, (
                    f"APPROVED_IDIOSYNCRATIC_BREAKOUT: {symbol} {strat.upper()} allowed in NEUTRAL "
                    f"with RVOL={effective_rvol:.2f}>=2.20x proving institutional decoupling from market chop"
                )
            return False, f"INDEX_FILTER_DENIED: {strat.upper()} in NEUTRAL requires high RVOL >= 2.20x (got {effective_rvol})"

        if strat == "vwap_pullback":
            return False, f"INDEX_FILTER_DENIED: VWAP_PULLBACK requires directional market trend (currently NEUTRAL)"

        if strat == "mean_reversion":
            # Fully permitted in NEUTRAL
            return True, f"APPROVED: Mean Reversion active during range-bound MarketTrend.NEUTRAL"

    # 3. Trending Regimes (BULLISH / BEARISH)
    if strat in ("orb", "vwap_pullback"):
        if trend == MarketTrend.BULLISH and not is_buy:
            return False, f"INDEX_BETA_CONTRADICTION: Cannot open SHORT on {symbol} when market trend is BULLISH"
        if trend == MarketTrend.BEARISH and is_buy:
            return False, f"INDEX_BETA_CONTRADICTION: Cannot open LONG on {symbol} when market trend is BEARISH"

    elif strat == "news_momentum":
        if trend == MarketTrend.BULLISH and not is_buy:
            return False, f"INDEX_BETA_CONTRADICTION: Shorting {symbol} on news denied during BULLISH market rally"
        if trend == MarketTrend.BEARISH and is_buy:
            return False, f"INDEX_BETA_CONTRADICTION: Buying {symbol} on news denied during BEARISH market decline"

    elif strat == "mean_reversion":
        if trend == MarketTrend.BULLISH and not is_buy:
            return False, f"INDEX_BETA_CONTRADICTION: Shorting overbought {symbol} denied during strong BULLISH market rally"
        if trend == MarketTrend.BEARISH and is_buy:
            return False, f"INDEX_BETA_CONTRADICTION: Buying oversold {symbol} (catching falling knife) denied during strong BEARISH market decline"

    return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"
```

#### Change 2: `backend/app/strategies/base.py`
Add optional fields to `SignalEvent` dataclass:
```python
@dataclass
class SignalEvent:
    symbol: str
    side: Union[OrderSide, str]
    order_type: Union[OrderType, str]
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    strategy_id: str
    confidence: float
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    target_qty: Optional[int] = None
    rvol: Optional[float] = None
    volume_surge: Optional[float] = None
    catalyst_sentiment: Optional[float] = None
```

#### Change 3: `backend/app/strategies/adaptation.py`
Pass `rvol` into `is_signal_permitted`:
```python
        # 0. Market Index Trend Filter Check
        if self.market_filter is not None:
            catalyst_sentiment = getattr(signal, "catalyst_sentiment", None)
            volume_surge = getattr(signal, "volume_surge", None)
            rvol = getattr(signal, "rvol", None)
            permitted, reason = self.market_filter.is_signal_permitted(
                strategy_id=signal.strategy_id,
                side=signal.side,
                symbol=signal.symbol,
                asof=signal.timestamp,
                catalyst_sentiment=catalyst_sentiment,
                volume_surge=volume_surge,
                rvol=rvol,
            )
            if not permitted:
                return False, f"ADAPTATION_MARKET_FILTER_DENIED: {reason}", 0
```

#### Change 4: `backend/app/strategies/news_momentum.py`
1. Lower default `volume_surge_multiplier` from `3.50` to `2.00`:
   ```python
   def __init__(
       self,
       strategy_id: str = "news_momentum",
       name: str = "Catalyst News Momentum Breakout",
       sentiment_threshold: float = 0.60,
       volume_surge_multiplier: float = 2.00,
       catalyst_ttl_seconds: int = 180,
       target_1_r: float = 0.80,
       target_2_r: float = 1.80,
   ):
   ```
2. Attach `sig.rvol = vol_ratio` alongside `sig.volume_surge = vol_ratio`.

#### Change 5: `backend/app/ingestion/sentiment.py`
Harden NLP lexicon and category classifier to use strict boundary regex matching:
```python
        # 1. Multi-word phrase matching (highest specificity with word boundaries)
        for phrase, weight in self.BULLISH_KEYWORDS.items():
            if " " in phrase and re.search(r"\b" + re.escape(phrase) + r"\b", text):
                raw_score += weight * 1.5
                matches += 2

        for phrase, weight in self.BEARISH_KEYWORDS.items():
            if " " in phrase and re.search(r"\b" + re.escape(phrase) + r"\b", text):
                raw_score += weight * 1.5
                matches += 2
```
And in `_classify_category`:
```python
    def _classify_category(self, text: str, score: float) -> CatalystCategory:
        """Categorize into specific trading catalyst buckets using strict word boundaries."""
        def has_kw(kws: Tuple[str, ...]) -> bool:
            return any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in kws)

        if has_kw(("fda", "biotech", "clinical", "drug", "phase 3", "trial")):
            return CatalystCategory.FDA_APPROVAL if score > 0 else CatalystCategory.FDA_REJECTION
        if has_kw(("earnings", "eps", "quarter", "revenue", "sales", "profit")):
            return CatalystCategory.EARNINGS_BEAT if score > 0 else CatalystCategory.EARNINGS_MISS
        if has_kw(("guidance", "outlook", "forecast")):
            return CatalystCategory.GUIDANCE_RAISE if score > 0 else CatalystCategory.GUIDANCE_CUT
        if has_kw(("sec", "probe", "investigation", "subpoena", "lawsuit", "fraud")):
            return CatalystCategory.LEGAL_INVESTIGATION
        if has_kw(("upgrade", "downgrade", "target price", "pt")):
            return CatalystCategory.ANALYST_UPGRADE if score > 0 else CatalystCategory.ANALYST_DOWNGRADE
        if has_kw(("partner", "partnership", "deal", "contract", "merger", "acquisition")):
            return CatalystCategory.PARTNERSHIP_CONTRACT

        return CatalystCategory.GENERAL_CATALYST if abs(score) >= 0.5 else CatalystCategory.NEUTRAL
```

#### Change 6: `backend/app/strategies/mean_reversion.py`
Calibrate parameters for moderate VIX chop sessions:
```python
    def __init__(
        self,
        strategy_id: str = "mean_reversion",
        name: str = "Statistical Mean Reversion / Exhaustion Fades",
        period: int = 20,
        z_threshold: float = 1.65,
        rsi_period: int = 14,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        volume_climax_multiplier: float = 1.30,
        min_wick_ratio: float = 0.30,
        atr_stop_multiplier: float = 0.15,
        min_rr_ratio: float = 1.00,
    ):
```

---

## 4. Lookahead Bias, Unclosed Bar Dependencies & Data Leakage Audit

A line-by-line audit of indicator calculations was conducted:

| File & Function | Lookahead Check | Unclosed Bar Dependency | Result |
|---|---|---|---|
| `base.py:calculate_anchored_vwap` | Computes cumulative PV / volume strictly over past closed bars sequence. | No intra-bar or forward bar leakage. | **PASSED** |
| `base.py:calculate_atr` | Wilder's smoothing on completed historical bars. | No future leakage. | **PASSED** |
| `base.py:calculate_zscore` | Uses `prices[-20:]`, where `prices[-1]` is the current closed bar. | Causal window. | **PASSED** |
| `market_filter.py:IndexState.update_bar` | Step-by-step sequential update on closed bar arrival. Future timestamp check rejects `elapsed < 0`. | Causal. | **PASSED** |
| `orb.py:on_bar` | Opening range formed only from 09:30–09:34 bars. Breakout evaluated at 09:35+. RVOL baseline excludes current bar (`all_bars[:-1][-20:]`). | Zero lookahead. | **PASSED** |
| `news_momentum.py:on_bar` | Volume SMA baseline excludes current bar (`recent_bars[:-1][-20:]`). Catalyst timestamp verifies $0 \le \Delta t \le \text{TTL}$. | Zero lookahead. | **PASSED** |
| `mean_reversion.py:on_bar` | Volume SMA excludes current bar (`volumes[:-1]`). Evaluates closed bar only. | Zero lookahead. | **PASSED** |

**Conclusion on Bias**:
Zero forward leakage, lookahead bias, or unclosed bar access exists in any indicator module. All calculations are strictly causal and consume discrete, completed 1-minute `BarEvent` objects emitted by AlpacaRelay at minute boundaries.

---

## 5. Existing Tests & Verification Suite Impact

### 5.1 Existing Assertions Requiring Updates
In `backend/tests/unit/test_strategies.py`:
- Line 678: `assert strat.z_threshold == 2.00` $\to$ update to `assert strat.z_threshold == 1.65`
- Line 681: `assert strat.volume_climax_multiplier == 1.75` $\to$ update to `assert strat.volume_climax_multiplier == 1.30`
- Line 682: `assert strat.min_wick_ratio == 0.35` $\to$ update to `assert strat.min_wick_ratio == 0.30`

### 5.2 New Unit Tests Required
1. `test_market_filter_neutral_regime_policy`:
   - Verify ORB with $\text{RVOL} = 1.90$ rejected in `NEUTRAL`.
   - Verify ORB with $\text{RVOL} = 2.25$ approved in `NEUTRAL` (`APPROVED_IDIOSYNCRATIC_BREAKOUT`).
   - Verify VWAP Pullback rejected in `NEUTRAL`.
   - Verify News Momentum with $\text{VolSurge} = 2.30$ approved in `NEUTRAL`.
   - Verify Mean Reversion (both BUY and SELL) approved in `NEUTRAL`.
2. `test_news_momentum_calibrated_volume_surge`:
   - Verify default `volume_surge_multiplier == 2.00`.
   - Verify entry fires at $2.1\times$ volume surge.
3. `test_sentiment_scorer_strict_boundary_protection`:
   - Verify `"Apple Leads Tech Sector Rally"` is NOT classified as `LEGAL_INVESTIGATION` (classified as `GENERAL_CATALYST` or `NEUTRAL`).
   - Verify `"Contract window closed"` does not match `"contract win"`.
4. `test_mean_reversion_calibrated_trigger`:
   - Verify entry fires at $Z = 1.68$, $\text{VolRatio} = 1.35\times$, $\text{Wick} = 0.32$.

---

## 6. Synthesis & Implementation Risk Assessment

1. **Risk Engine Invariants**:
   All trades admitted through the newly calibrated regimes still pass through `InstitutionalRiskEngine.evaluate_order_request()`.
   - Daily loss limit of $1,500 remains hard-coded and binding.
   - Max position notional cap of $25,000 (50% equity) remains binding.
   - Stop distances are clamped between $0.4\%$ and $4.0\%$.
   - Concurrency limit (max 3 positions) and sector correlation limits remain binding.
2. **Execution Timing**:
   No changes to Time-of-Day phases are needed; morning flush lockout for Mean Reversion (09:30–10:00) and ORB trading window (09:35–11:30) remain strictly honored.
3. **Downstream Worker Readiness**:
   The code specifications above are fully scoped, isolated, and ready for immediate remediation by the implementation worker.
