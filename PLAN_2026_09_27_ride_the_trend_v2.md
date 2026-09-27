# PLAN 2026-09-27: Ride the Trend v2, as built (four data layers, live from day one)

Current audit corrections and release evidence: [AUDIT_2026_09_27](docs/ride_the_trend_v2/AUDIT_2026_09_27.md). The historical build notes below describe the earlier revision.

Status: draft v5 = what is implemented in the working tree on 2026-09-27. Operator requirements: the five
principles (momentum velocity, pullback volume profile, volatility stops, relative strength + regime, sizing
and tail) executed with all four data layers (tick aggression, order book, high-resolution timestamps,
macro regime), trading for real on the paper account from Monday 2026-09-28. Drafts v1-v4, the two Codex
plan attacks, the Codex code review and the real-data dry runs are in `docs/ride_the_trend_v2/`.

Strategy: registered as `vwap_pullback` ("Ride the Trend" on the dashboard), class
`backend/app/strategies/vwap_pullback_v2.py`. Bot: AutonomousDayTrader, Alpaca paper PA3CSVDZMMPY,
Railway, push to main = redeploy. Switch: `RIDE_THE_TREND_MODE=v2_live | off` (`off` stops new entries
only; open positions are still managed).

## 1. The four data layers and where each one comes from

| Layer | Requirement | Source in this bot | Module | Gate |
|---|---|---|---|---|
| 1 Tick data | Measure transaction aggression (delta) | Every SIP trade print from AlpacaRelay, classified at the ask (buyer aggressive), at the bid (seller aggressive) or by the tick rule when inside the spread or the quote is stale (> 2 s) | `backend/app/core/tick_tape.py` | Pullback-leg net aggression `(buy - sell) / (buy + sell)` must not be below -0.30 for longs (above +0.30 for shorts); otherwise `AGGRESSIVE_PULLBACK` (trap) |
| 2 Market depth | Liquidity traps, book imbalance | NBBO top of book (bid size, ask size) from every quote; this is the only book level the feed carries. `book_imbalance()` is the provider slot: a full-depth vendor replaces it without touching the strategy | `tick_tape.py` | Quote-weighted imbalance over the last 30 s `(bid - ask) / (bid + ask)` at least +0.10 for longs (-0.10 for shorts); otherwise `BOOK_AGAINST` |
| 3 High-resolution timestamps | Velocity of momentum shifts | The prints' own exchange nanosecond timestamps (`TradeEvent.timestamp_ns`, parsed by `parse_relay_timestamp`) | `tick_tape.velocity()` | Signed price change per second over the last 60 s of prints, in ATR per minute, at least 0.25 the trade's way; otherwise `TICK_VELOCITY_LOW` |
| 4 Macro feeds | Regime filter | SPY/QQQ VWAP + EMA9/21 direction (existing), VIX regime (existing), relative strength versus SPY, and a macro-release blackout calendar `backend/app/data/macro_calendar.json` (FOMC decision days, CPI, first-Friday payrolls) | `backend/app/core/macro_calendar.py`, `main.py` admission | Index must be directional the trade's way; stock must lead SPY since 09:30 and over 30 bars (lag for shorts); no entry inside a blackout window; missing data rejects (`RS_UNAVAILABLE`, `MACRO_UNAVAILABLE`) |

Every layer fails closed: no prints, no quotes, no SPY bar within 60 s, or no calendar means no trade
(`TICK_UNAVAILABLE`, `BOOK_UNAVAILABLE`, `RS_UNAVAILABLE`, `MACRO_UNAVAILABLE`). The card and `/health`
show each layer's live status.

## 2. The rules (long side; shorts mirror with highs/lows and signs swapped)

Bars: accepted regular-session 1-minute bars, index `i` from 09:30. Duplicate, out-of-order and malformed
bars are ignored; a gap over 5 minutes resets an active setup; a zero-volume bar is kept but can never
signal. ATR14 (Wilder, seeded at bar 13), anchored VWAP and its deviation (std <= 0.001 falls back to ATR),
reference volume = mean of bars i-30..i-1, defined only from bar 35 (the first five opening bars are never
in it).

State machine per symbol, `IDLE -> IMPULSE -> PULLBACK -> RESUMING -> SIGNAL`:
1. IMPULSE: a strict new 30-bar high with the close above VWAP. Restarts beat everything on the same bar;
   an outside bar (new high and new low) goes IDLE.
2. Touch: the candle intersects the zone `[VWAP - 0.2 std, VWAP + 0.3 std]` 2 to 20 bars after the
   impulse (1 bar = too early; none by 20 = expired). Leg = bars after the impulse up to the touch.
   `PVR = mean leg volume / reference`. >= 1.20 rejected as high-volume pullback until the next impulse;
   0.80 < PVR < 1.20 rejected as not thin; <= 0.80 continues. Then Layer 1: aggression over the leg window
   (first leg bar start to touch bar end) per the table.
3. PULLBACK: track the lowest low; 20 bars without a resumption = timeout. The first close above the
   previous close starts RESUMING and is evaluated on that bar.
4. RESUMING, age = bars since the low, 1..3 (4 = expired; a new low returns to PULLBACK): up close; slope
   `(close - low bar's close) / age / ATR >= 0.50`; no-chase `close <= VWAP + 0.5 std` (fail = discarded);
   Layer 3 velocity; Layer 2 book; window 09:45 <= bar time < 11:30 (11:30 discards every setup); emission
   allowed (operator active, mode live, not in the 15-bar cooldown after an emitted signal, fewer than 2
   admitted signals for the symbol today).
5. Stop, computed once: `dist = max(1.5 x ATR x VIX stop multiplier, (entry - pullback low) + 0.5 x ATR,
   0.4% of entry)`; over 4% = `STOP_TOO_WIDE`, no trade. The signal carries `stop_is_final=True` and the
   adaptation engine returns it unchanged (it used to multiply every stop by the VIX multiplier).
6. Order: MARKET at the signal close (the bot's existing path). Admission in `main.py`: macro blackout,
   relative strength, index filter, phase gate (09:30-11:30), slots (3, 2 per sector), 1% risk x VIX sizing,
   $25k cap, daily loss stop. On SUBMITTED the symbol's daily budget increments.
7. Exits: bracket `runner_policy="TRAIL_ONLY"`: half at entry + 1.0R off the actual fill, breakeven after
   the confirmed T1 fill, 1.5 ATR trail on the rest, no second target, 15:55 flatten as today.

Earliest possible signal: bar index 38, the 10:08 bar, complete at 10:09 ET.

## 3. What is excluded and why
TSLA and CDE (the Morning Plan owns them Monday), SPY and QQQ (they are the regime instruments, and a stock
cannot lead itself in the relative-strength gate).

## 4. Restart behaviour
The strategy id stays `vwap_pullback` (the checkpoint refuses a changed strategy set). On restore, every
symbol's machine is rebuilt by replaying its stored session bars through the evaluator with the tick gates
skipped (no tape history exists after a restart); legacy v1 state (`session_bars`) is migrated the same way.
The tape refills from the live feed; until it has prints and quotes for a symbol, that symbol's setups fail
closed at the next tick gate.

## 5. Evidence

### 5.1 Tests (all green, 2026-09-27)
- `backend/tests/unit/test_vwap_pullback_v2.py` (36): every transition and rejection, shorts, bar policy,
  prefix invariance, restart-at-every-bar equivalence, legacy v1 migration, checkpoint round trip, mode
  off/paused, exclusions, card fields, final stop not rescaled, morning-only phase gate, TRAIL_ONLY
  bracket, relative-strength gate.
- `backend/tests/unit/test_ride_the_trend_data_layers.py` (17): tape classification and causality,
  velocity on print timestamps, book imbalance, trimming, nanosecond parsing, macro windows and
  first-Friday rule, fail-closed calendar, every tick gate pass/fail/unavailable, rebuild-then-live
  enforcement, card layer status, main handlers feeding the tape, macro gate rejection.
- `backend/tests/unit/test_ride_the_trend_pipeline.py` (3): a full fake session through the real
  `main.py` pipeline: market entry, bracket TRAIL_ONLY with no second target, T1 half, breakeven, ATR
  trail, stop-out; runner flattened at 15:55; mode off places nothing while the open position's stop keeps
  working.
- Whole backend unit suite: 1,115 passed before the pipeline tests were added.

### 5.2 Real-data dry runs (`scripts/run_ride_the_trend_v2_dry_run.py`, simulation mode, no broker, no network)
Real 1-minute bars plus every SIP trade print and NBBO quote of the morning, replayed through the live
handlers in exchange-timestamp order (bars delivered at bar end). Reports in `docs/ride_the_trend_v2/`.
- 2026-09-25, v2 only: 4,290 bars, 2,804,461 prints, 1,948,463 quotes in 10.7 s. Funnel: 133 impulses,
  9 touched too early, 6 no touch, 5 not thin, 1 high-volume pullback, 4 reached the resumption test,
  2 chased, 6 slow-slope bars, 1 resumption too old, 0 signals, 0 orders. Pullback aggression on the four
  thin pullbacks: -0.25, -0.03, -0.07, -0.20 (none below the -0.30 trap line). All invariants held.
- 2026-09-25, all arms: same v2 funnel with SPY/QQQ excluded (103 impulses); ORB traded 3 times alongside;
  no interference. All invariants held.
- 2026-09-24: see `docs/ride_the_trend_v2/dry_run_2026-09-24.md`.

### 5.3 Codex
Plan attacks (drafts v1 and v2): `docs/ride_the_trend_v2/codex_attack_round{1,2}.md`, 54 findings, all
accepted. Code review of this build: `docs/ride_the_trend_v2/codex_code_review.md`, triage in section 7.

## 6. Deploy and Monday checks
Deploy = push main. After deploy: `/health` healthy, `/health.ride_the_trend.data_layers` shows ticks and
quotes live, `/api/strategies` shows `version v2`, `mode v2_live`, hours 9:30-11:30 AM and the two data
notes on the card; Railway logs clean of `error on bar`. Monday: `/api/decisions?strategy=vwap_pullback`
for outcomes (RS_FILTER, MACRO_BLACKOUT, MARKET_FILTER, SUBMITTED), `/api/research/setups?since=2026-09-28`
for every setup transition with its layer values, `BROKER FILL` / `BROKER MISMATCH` in the logs,
`/health.research.errors == 0`.

## 7. Codex code-review triage
34 findings (8 P0). All P0s and the cheap P1s were fixed before commit; the rest are accepted design and listed in section 8. Full list and triage: `docs/ride_the_trend_v2/codex_code_review.md`.

## 8. Known limits, stated plainly
- Layer 2 is the inside of the book only. Alpaca carries no depth beyond the NBBO; a full-depth vendor plugs
  into `book_imbalance()`.
- Layer 3 velocity is computed from exchange timestamps, but decisions are still taken once per completed
  1-minute bar; the bot is not a tick-driven engine.
- Q4 2026 CPI dates in the macro calendar must be verified against the BLS schedule; FOMC dates are the
  published Fed calendar.
- Thresholds (-0.30 aggression, 0.25 ATR/min velocity, 0.10 imbalance, 0.80/1.20 PVR, 0.50 slope, 0.5 std
  chase) come from the operator's description of the rules, not from a fit to data.
- Accepted design points from the Codex code review: the machine is rebuilt from bars only after a restart
  (limits come from stored state, unfinished setups are discarded); bars are treated as final when the relay
  delivers them; a new 30-bar extreme restarts the machine before any touch or resumption on the same bar;
  VWAP and its deviation use bar typical prices with the v1 ATR fallback; a market fill beyond the stop is a
  pre-existing bot-wide exposure; the real-tick dry runs prove the funnel, the fake-session pipeline tests
  prove the order path.
