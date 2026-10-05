# Overnight playbook row and the 3:45 handoff (plan)

Status. v2, 2026-10-05 14:40 ET. v1 was attacked by three read only reviewers (correctness, operator view, tests and deploy), 41 findings. Section "v2 changes" lists what changed; where it conflicts with the sections below, v2 wins. Operator approved the design canvas (https://claude.ai/artifact/7tAvxjdq8Si33vDKmySCDn, option 3: "Overnight gets its own row" plus "show the 3:45 handoff") and asked to plan, attack, build, review, test, audit and deploy today.

## In plain words

Today the page lists "The 6 ways it trades" and shows the overnight holds in a separate box. The operator could not tell where the overnight plan fits. After this change:

1. The playbook list shows Overnight as a 7th row, with the same parts as the other rows (name, status chip, hours bar, today's result, tap for details).
2. Every hours bar gets a short "Night" part at the right, so Overnight's hold is drawn where it happens. A striped band marks 3:45 to 4:00 PM, the handoff, on every row.
3. From 3:45 PM until the morning sale is booked, a note above the list says which stocks belong to Overnight and until when.
4. When a day trade was closed early for the overnight buy (X6), that playbook's row says so.
5. Overnight's details show the 7 steps of the night (3:45 lockout to 9:30 sale), each marked done, now, next, or skipped with the plain reason.

Nothing about trading changes. One small read only field is added to the backend's overnight payload.

## Scope

### B1. Backend: expose today's early closes (read only)

`OvernightIntegration.payload()` gains `"x6": [{"symbol","date","qty","side","done","order_id"}]` from `self.ledger["x6"]`, every job whose `date` is the ET date of `now` or the previous trading day (so the morning page can still explain last night). No other change. `overnight_integration.py` is one of the 11 `DAY_ONE_SOURCE_PATHS`, so before the push `DAY_ONE_BUILD_REVISION` must be set to the new digest with `railway variables --set ... --skip-deploys` (MEMORY.md 2026-10-01 rule).

Test: unit test for the payload key (empty, one job today, one job from last week left out).

### F1. Playbook list (StrategyTable)

- When `overnightOn` (payload present or holds exist), StrategyTable renders a new `OvernightPlaybookRow` after the day rows. Heading reads the live count: "The 7 ways it trades" and a small "6 by day, 1 overnight" line. Without the payload the heading and rows are exactly as today.
- The row reads only the overnight payload, positions and the clock. No new websocket key besides B1.

### F2. Hours axis with a night part

- New helper `axisPct(minutesOfDay)` maps 9:30 to 4:00 PM onto 0 to 88%. 88 to 100% is "Night" (4:00 PM to the next 9:30 AM, not to scale, labelled). Only used when overnight is on; otherwise the bars keep using `sessionPct` over 0 to 100% (old backend reads byte identical).
- Column header labels: 9:30, noon, 4 PM (at 88%), Night.
- Every row draws the handoff band (hatched, 3:45 to 4:00 PM) when at least one overnight stock is switched on and mode is live.
- The "now" line: unchanged rule during the session (at `axisPct`). Outside the session no now line on day rows; on the Overnight row a now line sits in the night part while a hold is held.

### F3. The Overnight row

- Icon Moon, name "Overnight", sub label "NVDA, IREN, HUT" read from `settings.enabled` (live, never a fixed string).
- Status chip (one function `overnightChip`, pure, unit tested):
  - payload missing or `running` false: "Not running" grey.
  - mode not live or nothing enabled: "Switched off" grey.
  - not a trading day: "Market closed" grey (holds still shown, see below).
  - any hold held (HELD, SALE_QUEUED): "Holding N until 9:30 AM" sage breathing; after 9:30 still unsold: "Selling" amber (the red banner already alarms).
  - before 3:45 PM with buys planned: "Buys at 3:46 PM" lavender.
  - 3:45 to 4:00 PM with a buy planned, sent or accepted: "Buying at the close" sage breathing.
  - all skipped tonight: "No buy tonight" amber.
  - else: "Waiting" grey.
- Hours bar: a short segment at 9:30 (the sale) and the night part 88 to 100%, both in the Overnight ink.
- Result: `state.realized_today` (same number as today's overnight line on the Safety card).
- Note line under the row: the live `tonight` lines when 3:45 or later, otherwise the `size_note` of the first row.
- Tap opens details: the plain description from `STRATEGY_THEMES.overnight_*` and the handoff steps (F5).
- The holds list and "No overnight buy tonight" control stay in `OvernightHolds` above both views, unchanged. Deliberate difference from the mockup, which put the button inside the row: OvernightHolds sits above both Quick and Slow views so a tap to confirm survives a view switch (2026-10-04 review fix), and the playbook list only exists in the Quick view.

### F4. Handoff note above the playbook rows

- Shown from 3:45 PM on a trading day until no hold is left, when mode is live and at least one stock is enabled.
- Text: "Handoff is on. NVDA, IREN and HUT belong to Overnight until they sell at 9:30 AM <day>. The day playbooks can't open them." The symbols are the enabled stocks that are planned, sent, accepted or held tonight (skipped ones are left out). The day comes from the earliest hold's `sale_date`, else the next trading day is not guessed: "until they sell at the next 9:30 AM open".
- Morning, while a hold is still held: "NVDA, IREN and HUT belong to Overnight until they sell at 9:30 AM today."

### F5. Steps in the Overnight details

Seven steps, each Done, Now, Next or Skipped (amber with the plain reason from `overnightReasonText`):

1. 3:45 PM: day playbooks stop opening the overnight stocks.
2. 3:46 PM: a day trade in one of them closes early. Done text names the stock and playbook when B1 shows a job today; "No day trade to close" otherwise.
3. 3:46 PM: buy sent at the closing price (any row today in BUY_SENT, BUY_ACCEPTED, HELD, SALE_QUEUED, SOLD).
4. 3:49:30 PM: last moment for "No overnight buy tonight" (Now between 3:46 and 3:49:30).
5. 4:00 PM: bought at the close (any HELD, SALE_QUEUED or SOLD tonight).
6. 7:00 PM: the 9:30 AM sale waits at Alpaca (SALE_QUEUED or SOLD).
7. 9:30 AM: sold at the open, the stocks go back to the day playbooks (SOLD).

Per stock detail lines come from the existing `tonightStatusLine` and `holdLine`, no new wording rules. The step function is pure and unit tested with a fixed clock.

### F6. X6 note on the day playbook row

- Before the close fills: if B1 has a job today with `done` false, the row of the position's `strategy_id` says "Closing NVDA now so Overnight can buy it at the close."
- After: the today ledger trade whose `fill_legs` include the job's `order_id` gives the playbook. Its row says "NVDA closed at 3:46 PM, 9 minutes early, so Overnight could buy it at the close." (time and minutes from the leg's timestamp vs 3:55 PM).
- If the ledger row cannot be matched, no note on any row; step 2 still names the stock.

## Not in scope

No trading behaviour change. No change to OvernightHolds, the no-buy control or the unsold banner. No chip text change on day rows.

## Tests

- Backend: `pytest backend/tests` full run; new payload test.
- Frontend: `npm test` with new unit tests for `axisPct`, `overnightChip`, `handoffNote`, `overnightSteps`, `x6NoteFor`; `tsc`; `next build`.
- Visual: extend `scripts/build_overnight_holds_fixtures.py` frames (10:00 AM, 3:47 PM with an X6 close, 4:30 PM held, 9:00 AM next day held, old backend without payload) and a new `scripts/verify_overnight_row.py` that checks the row, chip, bars, band, note and steps on desktop 1440 and phone 390, with negative controls. Re-run verify_overnight_holds, verify_compact_dashboard, verify_holding_mood, verify_ui_redesign, verify_bright_dashboard, fixing only checks that encoded the old 6 row heading or old bar positions.
- Whole page visual audit (not only the diff): screenshots idle and busy, desktop and phone.

## Deploy

- Codex reviews the diff before deploy.
- Push to `main` (push = Railway redeploy). Set `DAY_ONE_BUILD_REVISION` first. Deploy only inside the logged D8 windows (16:10 to 18:50, 19:15 to 08:50 on trading days, see v2 T1), at the first minute available.
- After deploy: `/ready` 200, `/health` healthy with `day_one.source_revision_match` true, overnight running, live, enabled; `/api/overnight` has `x6` and `today`; live page shows the 7 rows.

## Risks

- Bars move left by 12% on every row when overnight is on. The verify scripts that read bar positions may need their expected values updated. Allowed only where the check encoded the old axis.
- Every row's grid width is unchanged; the Night part must stay readable at 390 px.

## v2 changes (attack round 1, all accepted unless noted)

Correctness.
- C1 (P0) Tonight's night only exists from 3:45 PM (`_ensure_tonight`). Before that the row and steps use the page's `plannedTonight`; row state is only read where `row.buy_date === todayEt`.
- C2 (P0) Early close days. Payload gains `today` = `{date, full_day, reason, sale_date}` from the same `calendar_gate` the buy uses. Not a full day: chip "No buy today" grey with the reason, no handoff band, steps all skipped with the plain reason.
- C3 (P0) Steps are judged against the active night (tonight's rows, else the held or sold-unreleased night), never against the clock alone.
- C4 Handoff note symbols = enabled rows with `reserved` true (what the order refusal enforces). Sale day from tonight's `row.sale_date` via `dayLabel`, never `new Date("YYYY-MM-DD")`.
- C5 X6: B1 exposes `filled_qty` (sum of booked fills) and stores the day playbook's `strategy_id` and `filled_at` on the job at creation and fill (two new keys in the checkpointed X6 job; old jobs without them read as null). Step 2 is Done only with `filled_qty` > 0. "Closing now" only 3:46 to 3:49:30 PM on the job's own day. The row note only for jobs dated today. Jobs older than 5 calendar days are left out of the payload; the page matches by date.
- C6 Chip: holds first (before every grey state), with the sale day. A blocked buy says "Not bought yet" amber. BUY_ACCEPTED after 4:00 PM says "Checking the closing buy".
- C7 Step 7 is Done only when SOLD and `reserved` false, else "Sold, waiting for Alpaca to show it flat".
- C8 Axis: `rangesToSegments` and the now line take a scale; BalanceCard and `sessionPct` unchanged. The night now line sits mid night part.
- C9 New pure helpers are tested by a new `frontend/scripts/verify_overnight_row.mjs` in `npm test` (node strip types, like verify_attention).

Operator view.
- O1 The same fact must not be said five ways. Row note shows the size note only (never the tonight lines, which the holds box shows). The handoff note shows only on the buy day from 3:45 PM until the buy is booked; after that the chip and the holds box carry it.
- O2 Chip says "Buys at the 4 PM close", never "3:46".
- O3 "No overnight buy tonight" tapped: chip "No buy tonight" grey.
- O4 Grey for chosen or calendar reasons (OPERATOR_NO_BUY_TONIGHT, EARLY_CLOSE, NOT_TRADING_DAY, MODE_OFF, STOCK_OFF), amber only for failures. Same for skipped steps.
- O5 No future tense for a past time: the handoff note is buy day only.
- O6 Phone: the "4 PM" and "Night" header labels show from `lg`; on phones a small moon marks the night part. Header labels get a bounding box overlap check at 390.
- O7 Wording: "From 3:45 PM until they sell at 9:30 AM Tue, NVDA, IREN and HUT are saved for Overnight. The day playbooks won't trade them."
- O8 Legend gains "Stripes = 3:45 PM handoff. Night part not to scale." when the night axis is on.
- O9 Row result reads `account.overnight_realized_today` (the Safety card's number). With holds held, the row note adds "Today's result is from this morning's sale."
- O10 The Safety card's hardcoded NVDA/IREN/HUT fallback is removed (live enabled list only).
- O11 Row details link to the holds box ("Stop tonight's buy with the button in the Overnight holds box at the top"). Rename of that box rejected: its heading is checked word for word by verify_overnight_holds and changing it is not needed to answer the operator's question.

Tests and deploy.
- T1 (P0) Deploy window. D8 (PLAN_2026_09_30, logged decision) allows deploys only 16:10 to 18:50 and 19:15 to 08:50 on trading days, never with a day trade open. The operator said "deploy now, don't wait"; the build cannot be ready before about 16:00 anyway, so the deploy goes at the first minute inside 16:20 to 18:45 ET today. Flagged to the operator.
- T2 (P0) `/health` no longer fails on a wrong `DAY_ONE_BUILD_REVISION` (it is 200 with status degraded since 10-01; `/ready` is 503). After deploy require `/ready` 200, `/health` healthy and `day_one.source_revision_match` true. If the build fails, put the old digest back.
- T3 The row shows only when the overnight payload is present (old backend: page unchanged, even with holds). Missing `x6` or `today` read as empty.
- T4 No existing assertion changes; a red existing check is a bug in the new code. New checks live in `scripts/verify_overnight_row.py`, including bar positions with the night axis and a negative control.
- T5 New testids: `strategy-row-overnight`, `overnight-step-*`, `handoff-note`, registered in `frontend/scripts/verify_ui.mjs`. Never reuse `overnight-hold-` or `holding-row-` prefixes.
- T6 One harness viewport runs in a non New York browser time zone.
- T7 Fixtures rebuilt after B1 (`scripts/build_overnight_holds_fixtures.py`). Ports 3005, 3025, 3026, 8005 checked free before and after.
- T8 Stage only the intended files (the tree has unrelated changes).
