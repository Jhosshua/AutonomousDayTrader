# Empirical Challenge Report: Bracket Slippage & Partial Fill Verification (Remediation Iteration 2)

**Challenger**: Challenger R2-2 (Adversarial Bracket Slippage & Partial Fill Challenger)  
**Date**: 2026-09-23T04:35:00Z  
**Verdict**: **APPROVE**  
**Overall Risk Assessment**: **LOW**

---

## 1. Challenge Summary

This empirical challenge evaluated the structural remediations implemented in `backend/app/core/bracket.py` and their deterministic interaction with `backend/app/core/engine.py`. The focus was on two primary attack vectors:
1. **Slippage Boundary Sanity**: Whether adverse or positive slippage at or beyond profit target overrides (`fill_price >= target_1_override` for BUY, `fill_price <= target_1_override` for SHORT) can ever trick the engine into submitting an immediately marketable limit order (e.g. limit sell below purchase price).
2. **Partial Fill Orphan Vulnerability**: Whether a partial fill on Target 1 (e.g. 20 of 50 shares) prematurely marks the order as complete (`target_1_filled = True`), causing an OCO stop-loss fill to skip cancelling the residual working order (30 shares) and leaving an orphaned limit order in `ExecutionEngine.working_orders`.

An empirical test harness was authored and executed at `.agents/teamwork/challenger_r2_2/test_slippage_and_partial_fill.py`, consisting of 14 adversarial stress scenarios covering exact equality boundaries, extreme multi-target gap fills, randomized price sweeps, sequential partial fills, dual-target partial fills, and partial stop-loss fills.

**Gate Verdict**: **APPROVE** — All 14 empirical challenge tests passed with zero failures. Slippage sanity checks guarantee directional monotonicity ($TP_1 > \text{fill}$ for LONG, $TP_1 < \text{fill}$ for SHORT), and stop-loss fills deterministically purge 100% of residual child orders from `ExecutionEngine.working_orders`.

---

## 2. Challenges & Attack Scenarios

### [Critical] Challenge 1: Adverse/Positive Slippage Past Profit Targets Producing Marketable Exits

- **Assumption Challenged**: Entry orders fill within planned price boundaries ($P_{\text{entry}} < TP_1 < TP_2$ for LONG). Pre-calculated strategy targets passed via `target_1_override` and `target_2_override` were previously applied literally upon fill without re-verifying against the realized fill price.
- **Attack Scenario**:
  - **Scenario 1A (BUY Slippage Breach)**: BUY order planned at $100.00 with stop at $98.00 and $TP_1$ override at $101.60. Market volatility causes positive fill slippage to $101.80 ($101.80 > 101.60). If $101.60 is submitted as a Sell Limit order, it is below the current market bid ($101.80), executing immediately for an unearned flat/loss exit with fee drag.
  - **Scenario 1B (BUY Exact Boundary)**: Fill price lands at exactly $101.60 ($fill\_price == TP_1$).
  - **Scenario 1C (SHORT Slippage Breach)**: SHORT order planned at $100.00 with stop at $102.00 and $TP_1$ override at $98.40. Adverse short fill slippage occurs at $98.20 ($98.20 < 98.40). If $98.40 is submitted as a Buy Limit order, it is above the current market ask ($98.20), executing immediately.
  - **Scenario 1D (Extreme Double-Target Slippage)**: Flash gaps blow past both Target 1 and Target 2 overrides ($105.00 on Long, $95.00 on Short).
- **Blast Radius**: Instant position liquidation on fill, negative PnL from execution spread and regulatory fees, complete strategy disruption.
- **Empirical Findings & Verification**:
  - In `backend/app/core/bracket.py:219-246`:
    ```python
    t1_override_valid = False
    if bracket.target_1_override is not None:
        if is_buy and bracket.target_1_override > bracket.entry_price:
            t1_override_valid = True
        elif not is_buy and bracket.target_1_override < bracket.entry_price:
            t1_override_valid = True

    if t1_override_valid:
        bracket.target_1_price = round(bracket.target_1_override, 2)
    else:
        bracket.target_1_price = round(
            bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2
        )
    ```
  - `test_buy_positive_slippage_exceeds_target_1_override`: Tested entry fill at $101.80. Verified $TP_1$ override ($101.60) was invalidated. $R_{\text{realized}}$ was recalculated as $101.80 - 98.00 = 3.80$. $TP_1$ was re-anchored to $101.80 + 0.80 \times 3.80 = 104.84$ ($> 101.80$). $TP_2$ was re-anchored to $101.80 + 1.80 \times 3.80 = 108.64$ ($> 104.84$). Submitted limit order was strictly at $104.84.
  - `test_buy_slippage_exact_equality_target_1_override`: Tested exact boundary $fill = 101.60$. Verified strict inequality (`>`) rejected 101.60 and re-anchored $TP_1$ to $104.48.
  - `test_short_adverse_slippage_drops_below_target_1_override`: Tested short fill at $98.20$. Verified $TP_1$ override ($98.40$) was invalidated. $R_{\text{realized}}$ recalculated as $3.80$. $TP_1$ re-anchored to $98.20 - 0.80 \times 3.80 = 95.16$ ($< 98.20$). $TP_2$ re-anchored to $91.36$ ($< 95.16$). Submitted limit order was strictly at $95.16.
  - `test_short_slippage_exact_equality_target_1_override`: Tested exact boundary $fill = 98.40$. Verified strict inequality (`<`) rejected 98.40 and re-anchored $TP_1$ to $95.52.
  - `test_buy_extreme_positive_slippage_exceeds_both_targets` & `test_short_extreme_adverse_slippage_drops_below_both_targets`: Verified both targets re-anchor strictly beyond the realized fill price ($110.60$ and $117.60$ on Long at $105.00$; $89.40$ and $82.40$ on Short at $95.00$).
  - `test_randomized_slippage_invariants`: 100 randomized Monte Carlo simulations verified $TP_1 > \text{fill}$ and $TP_2 > TP_1$ for all LONGs; $TP_1 < \text{fill}$ and $TP_2 < TP_1$ for all SHORTs.
- **Status**: **RESOLVED & ROBUST (PASS)**.

---

### [Critical] Challenge 2: Target 1 Partial Fill Orphan Vulnerability on Reversal Stop-Out

- **Assumption Challenged**: When Target 1 fills, it fills completely. Prior implementation assigned `target_1_filled = True` upon ANY fill on Target 1 without decrementing `target_1_qty`. On a partial fill, `target_1_filled` became `True`. If price subsequently reversed and triggered the stop loss, line 317 evaluated `if bracket.target_1_order_id and not bracket.target_1_filled:` -> `False`, omitting the Target 1 order from `orders_to_cancel` and leaving an active residual limit order in `ExecutionEngine.working_orders`.
- **Attack Scenario**:
  - Position: 100 shares LONG @ $100.00.
  - Target 1 limit order: 50 shares @ $101.60. Stop-loss order: 100 shares @ $98.00.
  - Market bids up to $101.60 with thin liquidity, filling only 20 shares on Target 1.
  - Residual on Target 1: 30 shares working.
  - Market collapses and hits stop-loss (now ratcheted to breakeven $100.05 for 80 shares).
  - Stop loss fills 80 shares.
  - If residual 30-share Target 1 order is not cancelled, it remains live in `ExecutionEngine.working_orders`. When price recovers to $101.60, the orphaned limit order sells 30 shares, creating an unauthorized, unhedged, and unmonitored short position on a paper account that forbids overnight holds and unmanaged shorts.
- **Blast Radius**: Catastrophic position inversion (unexpected naked short), uncontrolled downside risk, account liquidation.
- **Empirical Findings & Verification**:
  - In `backend/app/core/bracket.py:348-351` and `365-367`:
    ```python
    # Target 1 fill accounting:
    bracket.remaining_qty = max(0, bracket.remaining_qty - filled_qty)
    bracket.target_1_qty = max(0, bracket.target_1_qty - filled_qty)
    bracket.target_1_filled = (bracket.target_1_qty == 0)

    # Stop-loss execution cancellation guard:
    if bracket.target_1_order_id and (not bracket.target_1_filled or bracket.target_1_qty > 0):
        orders_to_cancel.append(bracket.target_1_order_id)
    if bracket.target_2_order_id and (not bracket.target_2_filled or bracket.target_2_qty > 0):
        orders_to_cancel.append(bracket.target_2_order_id)
    ```
  - `test_mandated_specification_100_shares_t1_50_partial_20_stop_80`:
    - Entry 100 shares @ $100.00 activated. Engine had 3 working orders: Stop (100), T1 (50), T2 (50).
    - Partial fill of 20 shares executed on T1.
    - Verified `bracket.target_1_filled is False`.
    - Verified `bracket.target_1_remaining_qty == 30`.
    - Verified `bracket.target_1_qty == 30`.
    - Verified `bracket.remaining_qty == 80`.
    - Verified engine stop order modified to 80 shares and ratcheted to breakeven ($100.05).
    - Stop-loss filled for remaining 80 shares.
    - Directive explicitly included `bracket.target_1_order_id` in `orders_to_cancel`.
    - Directive applied via `apply_bracket_directive`.
    - Verified `len(engine.working_orders) == 0`.
    - Verified residual 30-share Target 1 order transitioned to `OrderState.CANCELLED`.
    - Verified Target 2 order transitioned to `OrderState.CANCELLED`.
  - `test_multiple_sequential_partial_fills_on_target_1`: Tested 3 sequential partial fills (15, 15, 20 shares). Verified `target_1_filled` remained `False` until the exact moment `target_1_qty == 0`.
  - `test_dual_target_partial_fills_then_stop_loss`: Tested partial fills on both Target 1 (25/50) and Target 2 (20/50). Stop-loss fill for 55 shares successfully cancelled both residual orders. 0 working orders remained.
  - `test_short_position_partial_fill_and_stop_loss`: Tested short position partial fill (20/50 shares) followed by short stop-loss fill (80 shares). Verified 0 working orders remained.
  - `test_partial_fill_on_stop_loss_order_scales_targets`: Tested partial stop fill (40/100 shares). Verified dynamic scaling of open targets from 100 to 60 shares ($T_1 = 30, T_2 = 30$), followed by full stop-out leaving 0 working orders.
- **Status**: **RESOLVED & ROBUST (PASS)**.

---

## 3. Stress Test Results Summary

| Suite / Test Case | Input Parameters | Expected Behavior | Actual Behavior | Result |
|---|---|---|---|---|
| `test_buy_positive_slippage_exceeds_target_1_override` | Long entry $100.00, stop $98.00, $TP_1=101.60, fill $101.80 | $TP_1$ override rejected, re-anchors to $104.84 > 101.80$ | Re-anchored to $104.84, TP_2=108.64$ | **PASS** |
| `test_buy_slippage_exact_equality_target_1_override` | Long entry $100.00, stop $98.00, $TP_1=101.60, fill $101.60 | $TP_1$ override rejected, re-anchors to $104.48 > 101.60$ | Re-anchored to $104.48 | **PASS** |
| `test_buy_extreme_positive_slippage_exceeds_both_targets` | Long entry $100.00, fill $105.00 past $TP_1(101.60) & $TP_2(103.60) | Both targets re-anchor above $105.00 | $TP_1=110.60, TP_2=117.60$ | **PASS** |
| `test_buy_valid_slippage_preserves_overrides` | Long entry $100.00, fill $100.50 < TP_1(101.60)$ | Overrides preserved ($101.60, 103.60$) | Preserved $101.60 and $103.60$ | **PASS** |
| `test_short_adverse_slippage_drops_below_target_1_override` | Short entry $100.00, stop $102.00, $TP_1=98.40, fill $98.20 | $TP_1$ override rejected, re-anchors to $95.16 < 98.20$ | Re-anchored to $95.16, TP_2=91.36$ | **PASS** |
| `test_short_slippage_exact_equality_target_1_override` | Short entry $100.00, stop $102.00, $TP_1=98.40, fill $98.40 | $TP_1$ override rejected, re-anchors to $95.52 < 98.40$ | Re-anchored to $95.52 | **PASS** |
| `test_short_extreme_adverse_slippage_drops_below_both_targets` | Short entry $100.00, fill $95.00 below both targets | Both targets re-anchor below $95.00 | $TP_1=89.40, TP_2=82.40$ | **PASS** |
| `test_short_valid_slippage_preserves_overrides` | Short entry $100.00, fill $99.50 > 98.40$ | Overrides preserved ($98.40, 96.40$) | Preserved $98.40 and $96.40$ | **PASS** |
| `test_randomized_slippage_invariants` | 100 randomized prices, stops, and slippages | Invariant holds for all runs | 100/100 passed invariant checks | **PASS** |
| `test_mandated_specification_100_shares_t1_50_partial_20_stop_80` | 100 shares Long, T1 partial 20, stop 80 | T1_filled=False, T1_rem=30, 0 working orders on stop | Exact match, 0 working orders | **PASS** |
| `test_multiple_sequential_partial_fills_on_target_1` | 3 partial fills (15, 15, 20) then stop 50 | Progressive qty decrements, 0 working orders on stop | Exact match, 0 working orders | **PASS** |
| `test_dual_target_partial_fills_then_stop_loss` | T1 partial 25, T2 partial 20, stop 55 | Both residual T1 & T2 cancelled, 0 working orders | Exact match, 0 working orders | **PASS** |
| `test_short_position_partial_fill_and_stop_loss` | Short 100 shares, T1 partial 20, stop 80 | T1_filled=False, T1_rem=30, 0 working orders on stop | Exact match, 0 working orders | **PASS** |
| `test_partial_fill_on_stop_loss_order_scales_targets` | Stop partially fills 40/100, remaining 60 fills | Open targets scale 100 -> 60, 0 working orders | Exact match, 0 working orders | **PASS** |

---

## 4. Unchallenged Areas

- **Hardware/Network Disconnections During In-Flight REST Cancellation**: The simulation engine executes cancellations atomically in-memory. Asynchronous Alpaca REST network dropouts during live exchange routing are managed by downstream retry wrappers and remain outside pure in-memory bracket logic.
- **Sub-Cent Stock Fractional Penny Rules**: Tested equities down to $5.00; sub-penny pricing (< $1.00) is governed by FINRA Rule 6460 and is out of scope for mega-cap intraday equities.

---

## 5. Gate Verdict

**GATE VERDICT**: **APPROVE**

All requirements specified in the mission have been empirically tested, verified, and certified. The slippage sanity guards and partial-fill residual cancellation mechanisms operate with mathematical precision and zero order leakage.
