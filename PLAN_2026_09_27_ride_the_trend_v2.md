# PLAN 2026-09-27: Ride the Trend v2, LIVE from day one (Monday 2026-09-28)

Status: draft v4. Operator decision 2026-09-27: the new rules trade for real on the paper account from
Monday. Drafts v1-v3 (shadow-first) and both Codex reviews are in `docs/ride_the_trend_v2/`; what still
applies from them is in section 7.
Strategy: `vwap_pullback` ("Ride the Trend"), `backend/app/strategies/vwap_pullback.py`.
Bot: AutonomousDayTrader, Alpaca paper PA3CSVDZMMPY, Railway, push to main = redeploy.

## 1. What is different on Monday

| | Today (v1) | Monday (v2) |
|---|---|---|
| Trend | EMA20 > EMA50 on 1-min closes | No indicators. VWAP structure: a new 30-bar high with the close above VWAP starts a setup |
| Pullback | Any dip into the VWAP zone | Volume of the pullback leg must be thin: 80% or less of the volume of the 30 bars before it. 120% or more = high-volume pullback, rejected |
| Entry trigger | One green bar with a wick or a volume spike | Speed of the resumption: close-to-close at least 0.5 ATR per bar off the pullback low, within 3 bars of the low, and not more than 0.5 std above VWAP (no chasing) |
| Stop | VWAP - 0.5 std, floor 0.4%, VIX-scaled downstream | Computed once: the widest of 1.5 ATR x VIX multiplier, the pullback low minus 0.5 ATR, or 0.4%. Over 4% = no trade |
| Market filter | SPY/QQQ VWAP + EMA9/21 | Same, plus relative strength: the stock must be beating SPY since 09:30 and over the last 30 bars (shorts: lagging) |
| Exits | 50% at a band target (median 0.76R), breakeven, 1.5 ATR trail, second target | 50% at 1.0R, breakeven after that fill, 1.5 ATR trail on the rest, no second target (let the tail run), flatten 15:55 |
| Order | Market at signal close | Market at signal close (unchanged; proven path) |
| Window | 09:30-15:45 minus midday | 09:45-11:30 only (real first signal about 10:09 because of the 30-bar volume reference) |
| Frequency | 15-bar cooldown | 15-bar cooldown and at most 2 admitted signals per symbol per day |
| Symbols | 12 watchlist | 10: TSLA and CDE excluded (the Morning Plan owns them Monday) |
| Sizing | 1% risk, VIX-scaled, $25k cap, 3 slots, 2 per sector | Unchanged |

Switch: `RIDE_THE_TREND_MODE` env, `v2_live` (Monday) | `off`. Rollback = set `off`, no code change; open
positions keep their local stops and flatten at 15:55 as today.

## 2. The rules, exactly (long side; shorts mirror in 2.6)

### 2.1 Bars and features
- Completed 1-minute regular-session SIP bars from AlpacaRelay. `i` = ordinal of accepted session bars.
  Duplicate timestamp: ignored. Out-of-order bar: ignored. Gap over 5 minutes between accepted bars: setup
  reset to IDLE. Zero-volume bar: kept in history, can never be a signal bar.
- `VWAP[i]`, `std[i]` as today (std <= 0.001 falls back to ATR, as today). `ATR[i]` = Wilder ATR14, seeded at
  i=13. `ref[i]` = mean volume of bars i-30..i-1, defined only when i-30 >= 5 (first five opening bars never
  in the reference) and the mean is > 0. If ATR, std or ref is unavailable: no evaluation that bar.

### 2.2 Setup machine, one per symbol: IDLE -> IMPULSE -> PULLBACK -> RESUMING -> SIGNAL
Per bar, in this order: (1) bar checks; (2) restart check; (3) the current state's step; at most the chain
PULLBACK -> RESUMING -> SIGNAL on one bar.
- Restart (IDLE or any state): `H[i] > max(H[i-30..i-1])` strictly and `C[i] > VWAP[i]` -> IMPULSE-long,
  `impulse_i = i`, `ref` frozen. An outside bar that is also a new 30-bar low -> IDLE. Restart beats a touch on
  the same bar and clears a high-volume rejection.
- IMPULSE, bar t: touch = candle intersects the long zone, `L[t] <= VWAP[t] + 0.3 std[t]` and
  `H[t] >= VWAP[t] - 0.2 std[t]`. Touch at t = impulse_i + 1 -> IDLE (too early). No touch by t = impulse_i + 20
  -> IDLE. Touch with 2..20 bars: `leg = impulse_i+1 .. t`, `pvr = mean(V[leg]) / ref`.
  `pvr >= 1.20` -> IDLE, event HIGH_VOLUME_PULLBACK (stays out until the next restart). `0.80 < pvr < 1.20`
  -> IDLE (not thin). `pvr <= 0.80` -> PULLBACK, `low_i = t`.
- PULLBACK, bar k: strictly lower low -> `low_i = k`. `k - touch > 20` -> IDLE. `C[k] > C[k-1]` and `k > low_i`
  -> RESUMING and evaluate now with `age = k - low_i`.
- RESUMING, bar k: new lower low -> back to PULLBACK. `age > 3` -> IDLE. Evaluate in order:
  up close `C[k] > C[k-1]`; `slope = (C[k] - C[low_i]) / age / ATR[k] >= 0.50`; no-chase
  `C[k] <= VWAP[k] + 0.5 std[k]` (fail = IDLE); time in [09:45, 11:30) ET (11:30 discards every setup);
  emission allowed (2.5). Pass -> SIGNAL at `entry = C[k]`, then IDLE.
- Every rejection is logged with its event name and the feature values (the bot's decision log and the
  research store already exist for this).

### 2.3 Stop (once, final)
`dist = max(1.5 x ATR[k] x vix_stop_mult, (entry - L[low_i]) + 0.5 x ATR[k], 0.004 x entry)`.
`dist > 0.04 x entry` -> no signal (STOP_TOO_WIDE). `stop = entry - dist`. The signal carries
`stop_is_final=True` and `calculate_adapted_stop` returns it unchanged (today it multiplies every signal's
stop distance by the VIX multiplier; skipping that for final stops prevents a double application).
The actual fill price replaces `entry` when the bracket is armed (the bot already books Alpaca's real fill
price); `dist` is kept, so the stop moves with the fill.

### 2.4 Targets and runner
T1 = fill + 1.0 x dist for `q1 = max(1, qty // 2)`. Runner policy `TRAIL_ONLY` on the bracket: no second
target order or price; breakeven once the T1 fill is confirmed from broker events; 1.5 ATR trail as today;
the initial stop covers the whole quantity until T1; flatten 15:55 as today. (`create_bracket` today always
builds a T2 price and id when q2 > 0; the policy field is added to the model, serialization, restore.)

### 2.5 Admission, budget, cooldown
- Index filter as today (SPY/QQQ bullish for longs, bearish for shorts, NEUTRAL blocks).
- Relative strength, computed in admission next to the index filter from the market filter's SPY state
  (it keeps `first_open` and the last 60 closes): `rs_day = stock return since 09:30 - SPY return since
  09:30 >= 0` and `rs_30 = 30-bar return difference >= 0`. SPY bar for that minute missing or the index feed
  stale -> the signal is rejected (RS_UNAVAILABLE), never assumed.
- Budget: `admitted_today` counts signals that passed admission; at 2 the symbol stops emitting for the day
  but the machine keeps running. Cooldown: no emission for 15 bars after an admitted signal; machine keeps
  running (v1's early return skipped state upkeep; v2 must not).
- Slots, sector cap, risk sizing, $25k cap, daily loss stop: unchanged, shared with the other arms.

### 2.6 Short side
Restart on a strict 30-bar low with the close below VWAP. Zone: `H[t] >= VWAP[t] - 0.3 std[t]` and
`L[t] <= VWAP[t] + 0.2 std[t]`. Pullback extreme = highest high since touch. Resumption: down close,
`slope = (C[high_i] - C[k]) / age / ATR[k] >= 0.50`, no-chase `C[k] >= VWAP[k] - 0.5 std[k]`. Stop above:
`dist = max(1.5 ATR x mult, (H[high_i] - entry) + 0.5 ATR, 0.4%)`. T1 below. RS: `rs_day <= 0 and rs_30 <= 0`.
PVR, ages, budgets, cooldowns are direction-free; long and short share the symbol's budget.

## 3. Safety on day one
- Existing account daily loss stop min($1,500, 2.5% of session equity) covers v2 like every arm.
- `RIDE_THE_TREND_MODE=off` is the kill switch; it stops new entries only, never position management.
- Stops stay local (none rests at Alpaca; one sell order per position, repo MEMORY 09-25); the protection gap
  during a restart is the same as every other arm today.
- No stop is ever capped inward: too wide = no trade.

## 4. Build steps (Sunday), each with its test
1. `backend/app/strategies/vwap_pullback_v2.py`: the machine of section 2 as a pure evaluator over the
   accepted-bar history, wrapped in a Strategy that keeps `strategy_id="vwap_pullback"` (checkpoint restore
   raises "Persisted strategy set does not match this deployment" if the id set changes, runtime_state.py:239)
   with `version="v2"`. Registered in `main.py` in place of v1 (v1 class stays in the repo, unregistered).
   Tests: constructed bar sequences for every transition and rejection (long and short), duplicate/late/zero-
   volume/gap bars, ATR=0, std=0, outside bar, restart on the touch bar, first signal at index 38, prefix
   invariance (events for the first N bars unchanged after appending 50 more), cooldown and budget.
2. Checkpoint: v2 state is rebuilt by replaying the restored session bars through the evaluator on restore
   (no stored indices trusted); legacy v1 `symbol_states` dropped with a logged migration. Tests: restore the
   09-24-format checkpoint and the current one, then run a bar with no error; restart-after-every-bar replay
   produces the same signals as an uninterrupted run and re-emits none.
3. `stop_is_final` on `SignalEvent`; `calculate_adapted_stop` honors it. Test: VIX multiplier 0.8 and 1.3
   leave a final stop untouched; v1-style signals still scale.
4. Bracket `runner_policy="TRAIL_ONLY"`: no T2 order/price, breakeven on confirmed T1 fill, trail unchanged,
   initial stop covers all shares. Carried through the model, checkpoint serialization and restore. Tests:
   runner exits only on stop, trail or 15:55; an unscaled position still stops out; restore keeps the policy.
5. Relative strength in admission with the as-of SPY join and fail-closed rule; DECISION line carries
   `rs_day`, `rs_30`, and the reason when blocked. Tests: pass, fail, SPY missing, feed stale.
6. `RIDE_THE_TREND_MODE` env (`v2_live` | `off`), symbol exclusion list (TSLA, CDE). Test: `off` emits no
   signal and still manages an open v2 position; excluded symbols never evaluate.
7. Card and API: `/api/strategies` entry shows `version="v2"`, `mode`, window 09:45-11:30,
   `first_possible_signal="10:09"`, today's counts by rejection reason. Grep the UI for every old label and the
   old window text; screenshot idle and busy states.
8. Research store: v2 signals and trades already flow through the existing recorder; add the setup-event
   rows (every transition and rejection with features) so the rules can be audited from Monday's data.
9. Full test suite green (the one known pre-existing failure is the date-bound swing test), then a
   simulated full day through `handle_bar_event` on recorded 09-25 bars: v2 signals appear only in the
   window, stops are final, runner has no T2, TSLA/CDE untouched.
10. Deploy Sunday evening (push main). Verify `/health` healthy, broker mismatch false, `/api/strategies`
    shows v2 live with the new window, Railway logs clean of `error on bar`.

## 5. Monday and after
- Monday: watch `/api/decisions?strategy=vwap_pullback` for setups and rejection reasons, Railway logs for
  `BROKER FILL` / `BROKER MISMATCH`, `/health.research.errors == 0`, and that both the v2 trades and the
  TSLA/CDE plan trades book without competing for a slot.
- Weekly: the 09-27 simulator gets the v2 evaluator (`sim_v2.py`) so the same rules are replayed on 2024-2026
  and reported next to the live results. It informs, it does not gate; the operator decides changes.
- Deferred, not needed for day one: resting-limit entries (would need a new order lifecycle: cancel, partial
  fills, late fills, restart reconciliation; Codex P0s 1-5 and 9 in round 2 all concern it).

## 6. Assumptions to state plainly
- The 09-27 replay of v1 shows the old entry had no edge (0 bps drift on 36k signals, -0.055R per trade,
  mostly the spread). The five new rules are untested on this data; Monday starts the test with real paper
  orders, at the operator's decision.
- v2 signals will be far rarer than v1's 6.4 per day: the 30-bar reference, the thin-volume leg, the 3-bar
  resumption and the morning-only window each cut the count. Fewer trades is expected, not a bug.
- Market entries keep paying the spread; that is the known cost of avoiding the limit-order lifecycle work.

## 7. Codex findings that still apply (from rounds 1 and 2)
Applied: stop computed once and never rescaled (R1 P0-4); keep the strategy id (checkpoint); runner policy
field instead of `take_profit_2=None` (R1 P1-10); deterministic machine with bar policy, processing order,
timeouts and shorts written out (R2 P1-9, 11, 12, 13); rebuild state by replay on restore and test restart
equivalence (R1 P0-6); RS as-of join, fail closed (R1 P1-13); state upkeep during cooldown (R2 P1-28);
management independent of the entry switch (R1 P0-8); no institutional/liquidity claims (R1 P1-17).
Not applicable after dropping the limit entry and shadow mode: R1 P0-1, 2, 3, 5, 7, 9; R2 P0-1 to 4;
all shadow-isolation and bootstrap-gate findings (the historical study no longer gates anything).
