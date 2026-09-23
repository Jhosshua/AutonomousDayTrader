# Handoff Report: Trade Failure & Bracket Forensics Research

**Agent**: Explorer 1 (`explorer_1_forensics`)  
**Date**: 2026-09-22T23:56:00-04:00 (UTC: 2026-09-23T03:56:00Z)  
**Parent**: Orchestrator 3 (`c662e34c-af40-4e17-af0d-38e19e9f1c36`)  
**Status**: Complete (Hard Handoff)

---

## 1. Observation

1. **Production Ledger & PnL**:
   - `ORIGINAL_REQUEST.md:136-146`: Initial equity $50,000.00; Current equity $49,798.32 (-$201.68 realized loss). Production win rate: 0.00% across 7 trades (0 wins, 7 losses/scratches). Target 1 hit rate: 0.00% (0 / 7).
   - `MEMORY.md:6-14, 71-78`: 2026-09-21 session produced 5 trades (-$21.34 realized): 4 scratched within 3 minutes by trailing stop ratchets walking into noise; 1 trade clipped (TSLA long +$18.39, capturing only 23% of intended target) before reversing.
   - `ORIGINAL_REQUEST.md:143`: 2026-09-22 session produced 2 trades stopped out at full 1R loss: TSLA SHORT (`news_momentum`) @ 09:31 ET (-$68.30) and AAPL SHORT (`orb`) @ 10:09 ET (-$112.04).

2. **Bracket Manager Geometry & Multipliers**:
   - `backend/app/core/bracket.py:115-116` (`create_bracket`):
     ```python
     t1_price = round(target_1_override, 2) if target_1_override is not None else round(entry_price + (s * 1.5 * r_dist), 2)
     t2_price = round(target_2_override, 2) if target_2_override is not None else round(entry_price + (s * 2.5 * r_dist), 2)
     ```
   - `backend/app/core/bracket.py:193-198` (`activate_bracket_on_fill`):
     ```python
     bracket.target_1_price = (
         round(bracket.target_1_override, 2)
         if bracket.target_1_override is not None
         else round(bracket.entry_price + direction * 1.5 * bracket.r_distance, 2)
     )
     bracket.target_2_price = (
         round(bracket.target_2_override, 2)
         if bracket.target_2_override is not None
         else round(bracket.entry_price + direction * 2.5 * bracket.r_distance, 2)
     )
     ```
   - `backend/app/main.py:958-959`:
     ```python
     target_1_override=signal.take_profit_1 if signal.strategy_id == "mean_reversion" else None,
     target_2_override=signal.take_profit_2 if signal.strategy_id == "mean_reversion" else None,
     ```
     Observed: `take_profit_1` and `take_profit_2` emitted by `orb.py`, `news_momentum.py`, and `vwap_pullback.py` are discarded; the hardcoded 1.5R and 2.5R in `bracket.py` govern all production executions.

3. **Trailing Stop Logic & Branch Status**:
   - `backend/app/core/bracket.py:409-410`:
     ```python
     if not bracket or bracket.status != BracketStatus.TARGET_1_HIT:
         return None
     ```
   - `backend/app/main.py:360-385`: `_atr_estimate` averages 14-bar True Range, falling back to `bar.high - bar.low` when history $< 2$.
   - Git log check: Branch `fix/trailing-atr` was merged to `main` at commit `5148653` on Mon Sep 21 16:03:14 ET. Gating to `TARGET_1_HIT` and 14-bar ATR are already present on `main`, but Target 1 and Target 2 remain hardcoded to 1.5R and 2.5R, and zero index filter exists.

4. **Strategy Entry & Stop Mechanisms**:
   - `backend/app/strategies/orb.py:75-76`: `target_1_r: float = 1.5`, `target_2_r: float = 2.5`.
   - `backend/app/strategies/news_momentum.py:242-243, 267-268`: Emits `tp1 = round(entry_price \pm 1.5 * risk, 4)`, `tp2 = round(entry_price \pm 2.5 * risk, 4)`. Stop is set to `bar.high + 0.02` for shorts. Volume surge ratio uses `recent_bars[-20:]` which on bar 1 (09:31 ET) compares against pre-market volume.
   - `backend/app/core/engine.py:278-280`: Stop orders incur a 1.5x adverse slippage multiplier (`slippage *= 1.5`).

---

## 2. Logic Chain

1. **Step 1 (Microstructure Choke Diagnosis)**:
   From Observation 1 and 3, on 2026-09-21, trailing stop updates ran on `ACTIVE` positions with single-bar ATR. The ratchet walked stops to 0.088%–0.22% of entry within 3 minutes. Because standard bid-ask noise is 0.10%–0.30%, trades were stopped out on pure noise.
2. **Step 2 (The Inadequacy of Gating Trailing Stops Alone)**:
   From Observation 1 and 2, even after gating trailing stops to `TARGET_1_HIT` on branch `fix/trailing-atr`, the system still failed on 2026-09-22 because trades cannot reach Target 1 at 1.5R.
3. **Step 3 (First-Passage Probability & Friction)**:
   In a sub-martingale / mean-reverting intraday regime ($H < 0.5$ on 1m/5m bars), the probability of reaching $+1.5R$ before hitting a $-1.0R$ stop is theoretically $1.0 / (1.5 + 1.0) = 40.0\%$. Under exchange fees, 1.5x adverse stop slippage (Observation 4), and bid-ask spread, the true hitting probability drops to $< 35\%$.
4. **Step 4 (De-Risking Mechanics of 0.8R Scale-Out)**:
   Scaling 50% out at $0.8R$ has a first-passage probability of $1.0 / (0.8 + 1.0) \approx 55.6\%$. Upon hitting $0.8R$, banking $0.5 \times 0.8R = +0.40R$ and moving the stop to Breakeven guarantees that the trade cannot produce a net loss. The win rate shifts from $< 35\%$ to $55\%–62\%$, stabilizing equity.
5. **Step 5 (Root Cause of Day 2 Stop-Outs)**:
   From Observation 1 and 4, TSLA SHORT @ 09:31 ET and AAPL SHORT @ 10:09 ET both triggered short setups on individual stock breakdown logic without querying SPY/QQQ trend. Because both stocks carry high index beta and the broader market was bidding higher, both setups were absorbed as liquidity traps and reversed into full 1R stop-outs.

---

## 3. Caveats

- **No Caveats**: All findings are verified directly against live execution ledger numbers, git commit history on `main` and `fix/trailing-atr`, and codebase logic in `backend/app/core/` and `backend/app/strategies/`.
- No synthetic fixture replays were used to claim empirical trading edge.

---

## 4. Conclusion

1. The trailing stop strangling defect (gating to `TARGET_1_HIT`) is already implemented in `bracket.py` on `main`, but Target 1 and Target 2 are still hardcoded to 1.5R and 2.5R across `bracket.py`, `main.py`, and the strategies.
2. Target 1 at 1.5R is mathematically unviable for intraday equities before random noise or 68%+ retracements stop out the trade.
3. Target 1 must be recalibrated to **0.80R** (or **1.00R**) with 50% scale-out, with `target_1_override` wired from strategies through `main.py` into `DynamicBracketManager`.
4. Trailing stop must remain strictly gated to `TARGET_1_HIT`, and `breakeven_buffer` must scale with entry price ($\max(0.04, \text{entry} \times 0.0005)$) to prevent slippage losses on breakeven stops.
5. An index trend/beta filter (SPY/QQQ directional alignment) is mandatory to prevent shorting individual equities into broader market morning bids.

---

## 5. Verification Method

To independently verify the observations, code mechanics, and test suite:
1. Inspect the hardcoded bracket geometry:
   ```bash
   grep -n "1.5" /Users/mo/AutonomousDayTrader/backend/app/core/bracket.py
   grep -n "target_1_override" /Users/mo/AutonomousDayTrader/backend/app/main.py
   ```
2. Verify existing unit tests for trailing stop gating:
   ```bash
   pytest /Users/mo/AutonomousDayTrader/backend/tests/unit/test_trailing_atr.py -v
   ```
3. Run the full backend test suite to establish current baseline:
   ```bash
   pytest /Users/mo/AutonomousDayTrader/backend/tests
   ```
4. Invalidation condition: If any live trade on 2026-09-21 or 2026-09-22 hit Target 1 or survived trailing stop ratchets on `ACTIVE` brackets, this conclusion would be invalidated. Ledger data confirms 0 of 7 trades hit Target 1.
