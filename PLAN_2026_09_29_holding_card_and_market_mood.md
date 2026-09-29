# Plan 2026-09-29: "Holding now" says which strategy, and the dashboard shows how the robot adapts

Status: BUILT 2026-09-29 (merge 1761911). v2, revised after two independent critiques (backend truth: 2 P0 + 16 P1; operator UX/QA: 11 P1). All P0/P1 accepted unless marked. Design mockup (v1, superseded where this text differs): canvas https://claude.ai/artifact/2TWAJNTRtELgH8JwdKEjR3

## Operator request

1. When the robot is holding a trade, the operator must understand which strategy is holding it.
2. The robot adapts itself to the market (dynamic rules), but the dashboard never says so. Show it.

## Hard rules for this build

- Observation only. No trading rule, size, stop, gate, schedule or order path changes. Do not touch unrelated code.
- Every new backend value is fail-soft: a bug in it can never block an order, fail a checkpoint, or break the websocket/REST payload. It becomes `None` and the card shows a fallback sentence.
- The dashboard never claims something the robot did not do. Every sentence is derived from recorded values or live config, never copied numbers.
- Every new key in a streamed payload is ALWAYS present (null/false when unknown), because `useTradingStream.ts:224-227` merges frames and an omitted key would keep a stale value on screen.

## Facts verified in the code (main @ 12044e1, both critiques)

| id | card name | How it adapts | Evidence |
|---|---|---|---|
| vwap_pullback | Ride the Trend | Risk budget x VIX sizing multiplier x midday 0.5, BUT the 25%-of-equity notional cap (adaptation default `max_alloc_pct=0.25`, main.py:133 passes none) usually binds first, so share count usually does not change. Stop = max(1.5 ATR x VIX stop multiplier, dip extreme + stop_structure_atr x ATR, floor), computed in the strategy (`signal.features` stop_atr_candidate / stop_structure_candidate / vix_stop_mult). Enters only 9:45-11:30. Market trend filter (with the market only; off when flat). Relative strength vs SPY always on (stages rs_day, rs_30). Sector/dollar/rates gates only when enforced (currently `enforced_gates: []`). Macro calendar blackout. | adaptation.py, vwap_pullback_v2.py:672/704, main.py `execute_strategy_signal` 1793-1990, 2101-2139 |
| news_momentum | Big News | Risk x VIX x midday (same cap caveat); stop distance x VIX stop multiplier (0.4% floor may override); every open phase; with the market only, except an extreme catalyst (abs sentiment >= 0.85 and volume >= 5x) which passes even when the trend is UNKNOWN; flat market needs RVOL >= 2.2. | market_filter.py:255-335 |
| mean_reversion | Snap Back | Same sizing/stop rules; blocked 9:30-10:00; flat market either way; in a trend only with the market. | same |
| orb | Opening Range Breakout (ORBStraddle rules) | Own rules (never ADT's adaptation). 9:38 market check: SPY/QQQ opening data complete and board shorts within ADAPTIVE_CONFIG MIN/MAX_BOARD_SHORT_FRAC (25%-75%) else sits out. Flow rules ON in ADT via PARITY_MANIFEST.json `effective` (candle, delta, velocity, macro veto, absorption exit), read `orbs.config.effective()` / controller cfg, not env vars. Each trade risks 2% of the 9:15-frozen equity, capped by what is left of the 2.5% day budget and notional/gross caps: show the trade's actual `risk_usd`. Stop moves to the entry price at `breakeven_r` (0.75R). Can exit early: fast-fail (-0.40R), clawback, absorption. Flatten time from cfg `flatten` (11:00). | adaptive.py:334-400, orb_execution.py:1542-1560, 1925-1930, 2577-2615, orb_integration.py:1238-1248 |
| tsla_asymmetric_dual / cde_asymmetric_dual | Tesla / Coeur Morning Plan | Do NOT adapt. Risk engine forces VIX multiplier 1.0; sized 0.75% of session-start equity (tri_engine.py:285 `risk_budget`), 1.5% combined cap; fixed OCO exits; own clock (TSLA entries to 11:32, CDE to 12:00). They DO count toward the max-3 positions. | risk.py:342-390, tri_engine.py:78-89/285, tri_execution.py:355 |
| tsla_or15_retest | Tesla Morning Retest | Does not adapt (retired). | risk.py:342 |
| no bracket | (unlinked) | `_serialize_position` labels any bracketless position "manual" (main.py:978); API manual orders use "MANUAL". | main.py:978, 3527 |

Brackets (ADT's own): stop is fixed until target 1 fills, then moves to break-even plus a buffer, then trails 1.5 ATR (bracket.py:447-540), for both TRAIL_ONLY and TARGET runners. TRAIL_ONLY has no target-2 order but `_serialize_position` still sends `take_profit_2`.

End of day: lockout 15:45, liquidation 15:55 (flattening.py:72-78). A news EXIT signal can close an intraday trade earlier.

Stale VIX: `apply_stale_vix_guard` relabels the regime NORMAL and sizing 1.0 but keeps the old VIX number and old stop multiplier (adaptation.py:135-150). Restored `last_vix_print` keeps `is_stale=False` across restarts (main.py:890).

## What we build

### Backend (all fail-soft)

B1. `BracketOrder.entry_context: Optional[Dict[str, Any]] = None` (core/bracket.py). Old checkpoints restore with None; the old code ignores the key on rollback (pydantic default ignores extras; verified). No schema bump.

B2. In `execute_strategy_signal` (main.py), AFTER `entry_order_to_bracket[submitted.id] = ...` and the `notify_admitted` call (~1980-1982), before the research call:
```
bracket.entry_context = _entry_context_safe(signal, stages, qty)
```
`_entry_context_safe` does everything inside one try/except (WARNING log, return None), and its LAST step is `json.loads(json.dumps(research.json_safe(ctx), allow_nan=False))` so nothing unencodable (NaN, datetime, enum, numpy) can ever reach the checkpoint. Values are read from the ENGINE at admission, never recomputed from times:
- `decided_at` (signal.timestamp + 1 min, ISO) — labelled "decided at" in the UI
- `time_phase` = adaptation_engine.current_time_phase; `time_multiplier` (0.5 if MIDDAY_CHOP else 1.0, read the same expression the engine uses)
- `vix` (None if not finite), `vix_regime`, `sizing_multiplier`, `stop_multiplier` (engine values), `vix_stale` (same expression as main.py:1667; None if unknown)
- `qty_adaptation` (stages admission_qty), `qty_final` (qty), `qty_if_neutral` (calculate_position_size with vix_multiplier 1.0 and time 1.0, same equity/entry/stop/risk/cap), `size_limited_by`: "notional_cap" | "risk" | "account_limits" (risk engine trimmed below qty_adaptation)
- `market_trend` + `trend_reason`: re-ask `market_filter.is_signal_permitted(...)` with the same inputs and `asof=signal.timestamp` (pure read; research_tracker._filter_verdict:174-183 does the same) and keep the trend + short reason code (APPROVED, APPROVED_EXTREME_CATALYST, APPROVED_IDIOSYNCRATIC_BREAKOUT)
- `stop_raw` (signal.stop_loss) and `stop_adapted` (adapted_stop)
- Ride the Trend only: `stop_basis` "volatility" | "structure" | "floor" from signal.features candidates; `rs` (stages rs_day / rs_30 pass flags only); `macro` (stages macro, short); `regime_enforced` list
Add a unit test that an unencodable object injected into ctx still leaves the checkpoint healthy.

B3. `_serialize_position` (each new field computed in its own small try, None on error):
- `entry_context`, `initial_stop` (bracket.initial_stop_price), `bracket_status`, `runner_policy`, `target_1_filled`
- `take_profit_2 = None` when `bracket.target_2_order_id is None and runner_policy == "TRAIL_ONLY"` (display only; bracket untouched)
- `strategy_id`: when there is no bracket, fall back to the Position's own strategy_id (lower-cased) before "manual"
- `exit_due` for ADT intraday brackets and manual intraday positions = today at the flattening liquidation time read from the flattening engine's schedule (not hard-coded); existing OR15/tri/ORB values untouched
- tri positions: `plan_risk_pct` read from the tri strategy's config (the 0.0075 at tri_engine.py:285 lifted to a named constant used there too, value identical)
Also wrap the new market_context fields in `broadcast_ui_state` so a raise there can never break the frame.

B4. ORB holdings context:
- In `_on_decision` (orb_integration.py ~1108), when a decision has picks, save `{symbol: {classification, short_frac, wave, at}}` into the integration's own persisted ledger section (NOT the scheduler state; do not bump scheduler STATE_VERSION, `from_state` raises on mismatch). Day-scoped.
- `position_details` adds `orb_context` (always present, fields null when unknown): that saved regime subset, `short_bounds` from ADAPTIVE_CONFIG, `flow_rules_on` (names from the effective config), `breakeven_r`, `risk_usd` of this trade (controller holding / record), `flatten_at` from cfg (and replace the hard-coded time(11,0) there with cfg `flatten`, same value).

B5. Market mood payload: extend `get_market_context()` so these keys are ALWAYS present:
- `vix` (None if not finite; stop `_sanitize_for_json` turning NaN into 0.0 for this key), `vix_regime`, `vix_stale` (bool or None), `vix_age_seconds` (or None)
- `sizing_multiplier`, `stop_multiplier`, `time_phase`, `time_multiplier`, `market_status`
- `market_trend` (None if no filter), `market_trend_reason`
- `max_concurrent_positions`, `notional_cap_pct` (engine max_alloc_pct), `base_risk_pct`
- `midday` {start, end} read from `trading_windows.PHASES`
- `vix_tiers`: [{name, lower, upper, sizing, stop}] from the events.py tuples
- `adaptive_strategies`: ids of the three adaptive strategies that are currently switched on (status ACTIVE and, for vwap_pullback, RIDE_THE_TREND_MODE not off)
No PHASE_SCHEDULE rewrite, no trend-policy extraction, no clock/trend tables (dropped: they duplicate the strategy cards' hours bars and changed live gate code for no gain). Add a parity test: `trading_windows.PHASES` agrees with `get_time_of_day_phase` for every minute 09:00-16:30.

### Frontend

Page order in the intraday tab: when there are holdings, `HoldingNow` first (directly under the mode toggle), then `MarketMoodCard`, then the strategy carousel. With no holdings: MarketMoodCard, then carousel.

F1. Types: Position gets `entry_context`, `initial_stop`, `bracket_status`, `runner_policy`, `target_1_filled`, `orb_context`, `plan_risk_pct`, `r_multiple`; MarketContext gets every B5 key (optional in the type; the UI treats missing as unknown so an old backend during deploy still renders).

F2. `HoldingNow.tsx` card (keep testids `holding-row-<SYM>`, `btn-sell-now-<SYM>`, `btn-break-even-<SYM>`; keep "Tap again to confirm", `!position.fixed_protection`, "Safety exit stays fixed"; buttons >= 44px):
- Banner: strategy color band (strategyTheme), strategy name large, and a badge. No `what` sentence (the strategy card has it).
  - adaptive ids: "Sized for the market when it bought"
  - fixed ids: "Fixed plan"
  - orb: "Own market check" (banner gets a distinct icon/border so it does not look like a warning banner)
  - no bracket: "Not linked to a strategy" (neutral grey)
- Stock line: company, ticker, "bet it goes up/down", shares, "bought at $X" / "sold short at $X", "decided at 10:12" when entry_context exists.
- Plan tiles: Safety exit (+ "started at $Y" when it moved; + "moves to break-even after the first target, then follows the price" before target 1 for ADT brackets), Target ("First part sells at $X, the rest follows the price" for TRAIL_ONLY; plain target otherwise), "Sells by 3:55 PM at the latest" (exit_due).
- Adaptive: ONE visible sentence answering "why this size", honest from entry_context:
  - qty_final < qty_if_neutral: "Bought N shares, fewer than the usual M: jumpy market (70%)" and/or "and midday (half)" and/or "the account's limits trimmed it".
  - otherwise: "Normal size. The per-trade money cap was the limit, so the market mood did not change the share count." (or "Bigger than usual: calm market" if qty_final > qty_if_neutral)
  - then one sentence on the safety exit from stop_basis / stop multiplier: "Safety exit placed below the dip's low" / "placed farther away because the market is jumpy" / "placed closer because the market is calm".
  - then "It bought because the market was rising too." (trend at entry; extreme-catalyst / heavy-trading exceptions in plain words; never the word RVOL)
  - `<details>` "Why this size?" (closed) with the per-factor rows (fear gauge value + level, time of day, market direction, news calendar and "stronger than SPY" for Ride the Trend).
  - Last line vs now: "Market is Normal now. This trade keeps the size it got; its safety exit never moves farther away."
  - entry_context missing: "This trade started before the robot kept this record." Never invent values.
- Fixed plans: "Does the market change this trade? No, on purpose. It always risks {plan_risk_pct}% of the account, its exits were set when it bought and never move, and it runs on its own clock." Keep the existing tranche tiles.
- ORB: one short box from orb_context: "Traded after its 9:38 market check: SPY and QQQ data complete and the breakout list balanced (N% bets down, needs 25% to 75%). Risk on this trade: $X. Its stop moves to the buy price at +0.75x its risk, and it can close early if the trade fails fast." Do NOT repeat stop/target/"held at Alpaca" (the ORB strategy card has them). No "Tesla" text inside an ORB card.

F3. New `MarketMoodCard.tsx` (`data-testid="market-mood"`), one compact block:
- Headline, decided in this order:
  1. not a trading day / market closed (use the strategy window `trading_day` flag like page.tsx:76, plus market_status): "Market closed. When it opens, the robot will size and place new trades for the market's mood." (grey)
  2. no data yet (vix null and trend null, i.e. before the first real frame): "Waiting for market data." (grey)
  3. trend UNKNOWN while open: "Ride the Trend, Big News and Snap Back are waiting until the robot can see SPY and QQQ again. ORB and the Tesla/Coeur plans use their own checks." (only list ids in adaptive_strategies)
  4. otherwise: "The market is {nervous} and {rising}. {Adaptive strategies on} risk {70%} of the usual amount per trade; only trades with a wide safety exit end up smaller." plus a second line "ORB and the Tesla/Coeur plans do not change with the mood."
- Three tiles (1 column at 390px): Fear gauge (value + level chip; stale: "Not updating, sizes held at normal"; mark the level by `vix_regime`, never by the number), Time of day ("Midday 11:30 AM to 2:00 PM: new trades are half size" from payload), Market direction (plain name + effect).
- `<details>` "How the robot adapts" (closed): the fear-gauge level table from `vix_tiers` (current level marked), "At most {max_concurrent_positions} quick trades at once (Tesla/Coeur count, ORB does not)", "If the fear gauge stops updating, size goes back to normal", "Each trade can use at most {notional_cap_pct}% of the account".
- Colors: one neutral scale for levels (grey -> deepening amber), not strategy colors; terracotta only for money losses and alarms. Contrast >= 4.5:1. If `vix_tiers` is missing (old backend), hide the table.

F4. Plain-name maps (phases, levels, trends) in lib/plain.ts. Pro-words VIX line stays.

### Tests

Backend (`backend/tests/unit/test_holding_context.py` + small edits):
1. Admission records entry_context from engine values (Nervous VIX, midday phase -> time_multiplier 0.5), qty_if_neutral and size_limited_by correct for a tight stop (cap binds, sizes equal) and a wide stop (risk binds, smaller).
2. `_entry_context_safe` raising -> order + bracket + entry_order_to_bracket mapping intact, entry_context None. Mutation: remove the try -> test fails.
3. Unencodable value in ctx -> checkpoint still succeeds and persistence stays healthy. Mutation: remove the json round-trip -> test fails.
4. Checkpoint round trip keeps entry_context; payload without it restores None; a dict WITH the key validates under a model without the field.
5. `_serialize_position`: new fields; TRAIL_ONLY take_profit_2 None; tri/ORB unaffected except orb_context/plan_risk_pct; bracketless position falls back to Position.strategy_id.
6. market_context: every key present in every call (including no filter, NaN VIX, stale VIX); tiers equal events.py tuples.
7. PHASES vs get_time_of_day_phase parity every minute.
8. ORB: decision with picks saves regime subset; position_details orb_context always has all keys; flatten time from cfg.
Full backend suite green (known pre-existing failure test_swing_unresolved_entry... hard-codes 2026-09-25: report it, don't fix). e2e tests under tests/ that pin text: keep passing.

Frontend: `npx tsc --noEmit`; `npm test` (add components/MarketMoodCard.tsx to requiredFiles in verify_ui.mjs; keep its HoldingNow pins); `npx next build`.

Visual QA:
- State matrix with mocked payloads (no backend), extending the Playwright route-mock approach of `scripts/verify_ui_redesign.py` in a new `scripts/verify_holding_mood.py`. If its old fixture JSONs (a /private/tmp path) are gone, build fixtures from real API shapes and commit them under `docs/holding_mood/fixtures/`. States: idle (market closed, no holdings), weekend, waiting for data, calm/normal/nervous/panic x rising/flat/falling/unknown (spot sample, at least 6), stale VIX, busy (Ride the Trend long past target 1 with trailing stop + Big News extreme catalyst + Snap Back cap-bound + ORB trade + Tesla two halves), pre-release holding (no entry_context), unlinked position, old backend (no new market keys).
- One page per viewport (1440x900 and 390x844), states switched by pushing websocket frames, NEVER reloading (assert navigation count unchanged). Per state: exact headline text; strategy name + correct badge in every holding card; no terracotta inside `[data-testid=market-mood]` or any holding banner/badge (money chips and Sell buttons excluded); no horizontal scroll on the WHOLE page; open both `<details>` on phone and re-check overflow; `<details>` stays open across 3 frames; no console errors; no "RVOL" or old ORB wording visible. Full-page screenshots saved to `docs/holding_mood/screenshots/` (idle AND busy, both viewports).
- One real-backend check via `scripts/orb_ui_states/serve.py` (add a state reset between states and an injected adaptive holding with entry_context): the websocket `all_positions` carries entry_context and the ORB holding carries orb_context. Kill every server started.

### Deploy

Push `main` (Railway redeploys on push). Window: after 16:05 ET or before 9:10 ET only (Tesla enters to 11:32, Coeur to 12:00, news/Snap Back to 15:45, runners held to 15:55). After deploy: `/health` ok, broker mismatch false, persistence durable; websocket frame's `market_context` has every new key; `all_positions` entries carry the new keys (NOT /api/positions, which returns the raw account snapshot); Railway logs free of `error on bar` and checkpoint errors; the LIVE page screenshotted desktop + phone.

### Rollback

Additive. `git revert` + push. Old code ignores `entry_context` in the checkpoint and the new ORB ledger key (verify the ORB ledger loader tolerates an unknown key before shipping; if not, store it where it does).

## Known issue found, NOT fixed here (out of scope, reported to the operator)

`FlatteningSchedule` is fixed at 15:55 and ignores `NYSE_EARLY_CLOSES` (next: 2026-11-27, 1:00 PM close), so intraday positions would not be flattened before an early close and the card would say 3:55 PM that day.

## Out of scope

Trading rule changes; swing tab; closed-trade history; the early-close bug above.
