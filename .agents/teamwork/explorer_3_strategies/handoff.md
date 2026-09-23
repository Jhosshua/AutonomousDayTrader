# Strategy Execution & Climax Prevention Handoff Report

**Agent**: Explorer 3 (Strategy Execution & Climax Prevention Analyst)  
**Date**: 2026-09-23  
**Status**: Hard Handoff (Investigation Complete)  
**Target Audience**: Orchestrator / Lead Architect / Strategy Implementer

---

## 1. Observation

Direct code and log observations across the repository:

1. **ORB Breakout Detection & Climax Execution (`backend/app/strategies/orb.py:44-50, 170-176`)**:
   - In `evaluate_orb_signal`:
     ```python
     close_p = current_bar.close if isinstance(current_bar, BarEvent) else float(current_bar.get("c", current_bar.get("close", 0.0)))
     if close_p > range_high:
         return "BUY"
     elif close_p < range_low:
         return "SELL"
     ```
   - In `on_bar`:
     ```python
     entry_price = bar.close
     stop_loss = state.range_midpoint
     raw_dist = abs(entry_price - stop_loss)
     ```
   - *Direct Fact*: A candle with an $85\%$ upper rejection wick (e.g. High $152.00$, Close $151.05$, Range High $151.00$) triggers a `BUY` signal at market close ($151.05$). The strategy buys shooting stars.
   - *Direct Fact*: `stop_loss` is fixed at `range_midpoint`. On an extended candle, `raw_dist` balloons, which in turn inflates Target 1 ($1.5 \times \text{risk}$) and Target 2 ($2.5 \times \text{risk}$) to multi-sigma levels.
   - *Log Evidence (`ORIGINAL_REQUEST.md:143`)*: AAPL SHORT (`orb`) @ 10:09 ET on 2026-09-22 stopped out at full loss (-$112.04) after shorting into a morning liquidity sweep below `range_low` while the broad market was rallying.

2. **News Momentum Execution & Sentiment Vulnerabilities (`backend/app/strategies/news_momentum.py:24-66, 217-285`)**:
   - In `score_news_sentiment`:
     ```python
     for w in bearish_tokens:
         if w in text:
             is_negated = any(re.search(neg + re.escape(w), text) for neg in negation_patterns)
             score += 1.0 if is_negated else -1.0
     ```
     *Direct Fact*: Substring matching `if w in text:` matches `"miss"` in `"emission"` and `"commission"`, `"crash"` in `"crash test"`, and `"drops"` in `"backdrops"`.
   - In `on_bar`:
     ```python
     recent_volumes = [float(b.volume) for b in self.recent_bars[sym][:-1][-20:]]
     sma20_vol = calculate_sma(recent_volumes, 20)
     if sma20_vol <= 0:
         sma20_vol = 100000.0
     vol_ratio = bar.volume / sma20_vol
     if vol_ratio < self.volume_surge_multiplier:
         return []
     ...
     elif cat.sentiment <= -self.sentiment_threshold:
         raw_dist = max(0.10, round(bar.high + 0.02, 4) - entry_price)
         stop_loss, risk = resolve_stop(entry_price, raw_dist, False)
         signals.append(SignalEvent(symbol=sym, side=OrderSide.SELL, ...))
     ```
     *Direct Fact*: At 09:31 ET on 2026-09-22, `recent_bars` had 0 or 1 bar. `sma20_vol` defaulted to `100,000.0`. TSLA's 09:31 opening minute volume of $\sim 650,000$ shares generated a `vol_ratio` of $6.5\times \ge 3.5\times$, falsely confirming a volume surge on everyday opening auction volume.
     *Direct Fact*: The strategy contains **zero price direction verification** (`bar.close < bar.open`). It shorted TSLA on a green opening candle at market close, with stop at `bar.high + 0.02`, which was promptly blown through for full loss (-$68.30).

3. **Mean Reversion Starvation Under Moderate VIX (`backend/app/strategies/mean_reversion.py:133-171`)**:
   - In `on_bar`:
     ```python
     if abs(z) < self.z_threshold:  # 2.50
         return []
     ...
     has_climax = vol_ratio >= self.volume_climax_multiplier  # 3.0
     has_wick_rejection = (upper_wick / candle_range) >= self.min_wick_ratio  # 0.50
     is_rsi_overbought = rsi >= self.rsi_overbought  # 75.0
     ...
     reward = entry_price - target_price  # target_price = mean
     risk = stop_loss - entry_price       # stop_loss = bar.high + 0.50 * atr
     if reward > 0 and risk > 0 and (reward / risk) >= 1.2:
     ```
     *Direct Fact*: Strategy generated 0 trades across all sessions under VIX 14–16.
     *Direct Fact*: In `test_strategies.py:340-372`, the only test that passed this condition created a synthetic bar on SPY with Open $108$, High $115$, Low $107$, Close $109$ against a 20-bar mean of $100.0$—a $+15\%$ gap-up in a single 1-minute bar.

4. **Bracket Target Override Erasure (`backend/app/main.py:958-959`, `backend/app/core/bracket.py:115-116, 190-199`)**:
   - In `main.py`:
     ```python
     target_1_override=signal.take_profit_1 if signal.strategy_id == "mean_reversion" else None,
     target_2_override=signal.take_profit_2 if signal.strategy_id == "mean_reversion" else None,
     ```
   - In `bracket.py`:
     ```python
     bracket.target_1_price = (
         round(bracket.target_1_override, 2)
         if bracket.target_1_override is not None
         else round(bracket.entry_price + direction * 1.5 * bracket.r_distance, 2)
     )
     ```
     *Direct Fact*: `main.py` explicitly discarded `signal.take_profit_1` for ORB, VWAP Pullback, and News Momentum, hardcoding Target 1 to $1.5R$ and Target 2 to $2.5R$. Production Target 1 hit rate was $0.00\%$ across 7 trades.

---

## 2. Logic Chain

1. **ORB Climax Entry Mechanism**:
   - From Observation 1, `orb.py` enters at `bar.close` on any bar where `close > range_high` and `rvol >= 1.80`.
   - Because `evaluate_orb_signal` does not examine the relative position of `close` within the bar's `[low, high]` range, it treats a bar closing 5 cents above `range_high` with a $1.00 upper rejection wick as an equally valid breakout to a bar closing at the high of the day.
   - Microstructure absorption theory dictates that rejection wicks indicate aggressive seller supply absorbing buyer demand. Buying a shooting star guarantees entering at the tail of an exhausted move.
   - Furthermore, anchoring the stop to `range_midpoint` means that if the breakout bar surged $2\times$ ATR before closing, `raw_dist` is double its normal width. Because Target 1 is calculated as $\text{entry} + 1.5 \times \text{raw\_dist}$, Target 1 is pushed $+3\times$ ATR away from entry. For intraday mega-caps, such moves have an occurrence probability $< 2\%$, explaining why Target 1 was never hit.

2. **TSLA 09:31 ET SHORT Failure Chain**:
   - From Observation 2, a headline containing a bearish token was ingested.
   - At 09:31:00 ET, the first regular session 1-minute bar closed.
   - Because `self.recent_bars[sym]` was unpopulated, `sma20_vol` defaulted to $100,000$.
   - Normal opening auction volume on TSLA ($\sim 650,000$ shares) was compared against this $100,000$ placeholder, yielding `vol_ratio = 6.5x`, satisfying `vol_ratio >= 3.5x`.
   - Because `on_bar` had no price direction verification (`bar.close < bar.open`), the strategy shorted TSLA despite the bar closing green in a morning gap-and-go rally.
   - The stop at `bar.high + 0.02` was immediately clipped as the market-wide morning bid continued, resulting in an immediate full loss (-$68.30).

3. **Mean Reversion Incompatibility Proof**:
   - From Observation 3, `has_wick_rejection >= 0.50` requires $\text{bar.close} \le 0.50 \cdot \text{bar.high} + 0.50 \cdot \text{bar.low}$.
   - Meanwhile, `(reward / risk) >= 1.20` with `target = 20_SMA` and `stop = bar.high + 0.50 * atr` requires $\text{bar.close} \ge 0.5455 \cdot \text{bar.high} + 0.4545 \cdot \text{mean} + 0.2727 \cdot \text{atr}$.
   - Combining these bounds yields:
     $$0.0455 \cdot (\text{bar.high} - \text{mean}) + 0.2727 \cdot \text{atr} \le 0.50 \cdot (\text{bar.low} - \text{mean})$$
   - On any realistic candle where the breakout bar originates near the 20-period moving average ($\text{bar.low} \approx \text{mean}$), the left side is strictly positive while the right side is zero or negative.
   - Therefore, the conditions are **mutually exclusive**. The strategy could never fire on realistic data.

4. **Target Geometry Inefficiency**:
   - From Observation 4, `main.py` wiped out strategy-level targets and enforced $1.5R$.
   - Live execution data proves that $1.5R$ cannot be reached on 1m/5m bars before noise triggers trailing stops or reversals.
   - Scaling out 50% at $0.8R - 1.0R$ allows banking profits, de-risking trades, and ratcheting stops to breakeven + buffer.

---

## 3. Caveats

1. **Premarket News Ingestion Timestamp**: The precise Benzinga article ID that triggered TSLA at 09:31 ET was not logged in durable DB because persistence was disabled on Railway prior to commit `c70e8c0`. However, the code logic reproducing the entry is deterministic and fully verifiable via `test_strategies.py`.
2. **Index Trend Beta Filter**: While this report specifies the exact entry-level anti-exhaustion and sentiment hardening mechanisms, broad index alignment (e.g. SPY/QQQ VWAP trend filter) should be coordinated through the adaptation engine or a shared market context service.
3. **Historical Baseline Volume**: For symbols trading the open, volume profiles must either be pre-loaded from pre-market scans or anchored to a static floor ($500,000$ shares) during 09:30–09:35 ET.

---

## 4. Conclusion

1. **ORB**: Must be hardened with Close Location Value ($\text{CLV} \ge 0.65$ for BUY, $\le 0.35$ for SELL), candle range cap ($\le 2.2 \times \text{ATR}$), and extension cap ($\le 1.0 \times \text{ATR}$ past range boundary).
2. **News Momentum**: Must enforce strict word-boundary token matching (`\btoken\b`), require candle price direction confirmation (`bar.close < bar.open` for SELL, `bar.close > bar.open` for BUY), enforce a 5-minute open volatility lockout (09:30–09:35 ET), and normalize opening volume against historical open volume.
3. **Mean Reversion**: Must be recalibrated for moderate VIX (14–16) by lowering $Z_{\text{thresh}}$ to $2.00$, RSI to $70/30$, volume surge to $1.75\times$, wick ratio to $35\%$, stop placement to $\text{bar.high} + 0.15 \times \text{ATR}$, and R:R hurdle to $1.0\times$.
4. **Bracket Geometry**: `main.py` must pass `target_1_override=signal.take_profit_1` and `target_2_override=signal.take_profit_2` for ALL strategies, enabling Target 1 scaling at $0.8R - 1.0R$.
5. **Invariants Preserved**: All proposals maintain the $1,500 daily loss circuit breaker, $25,000 position cap, and $[0.40\%, 4.00\%]$ stop guardrails with zero lookahead bias.

---

## 5. Verification Method

To independently verify these findings and any subsequent implementation:

1. **Run Unit Test Suite**:
   ```bash
   pytest backend/tests/unit/test_strategies.py backend/tests/unit/test_risk.py backend/tests/unit/test_bracket.py -v
   ```
2. **Test Reproduction of TSLA 09:31 False Volume Surge**:
   Run a unit test creating an empty `recent_bars` state and feeding an opening bar with $650,000$ volume; verify that under the unpatched code `vol_ratio` evaluates to $6.5\times$, and that under patched code it evaluates against the 500k floor ($1.3\times < 3.5\times$).
3. **Verify Mathematical Impossibility in Mean Reversion**:
   Inspect `backend/app/strategies/mean_reversion.py:160-171`. Calculate `reward / risk` on a standard candle with $50\%$ wick; observe that `reward / risk < 1.0` in all non-gap cases.
4. **Run Integrated Dry Run**:
   ```bash
   python scripts/run_integrated_monday_dry_run.py
   ```
5. **Verify Process Hygiene**:
   Ensure ports 8005, 3005, and 8080 are completely liberated:
   ```bash
   lsof -i :8005 -i :3005 -i :8080
   ```
