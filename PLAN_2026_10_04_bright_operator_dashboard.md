# Plan 2026-10-04: bright, operator-first dashboard with inline results

Operator (2026-10-04): "redesign autonomous bot interface to be operator first from what operator would want to see
first with a better history display PL more compact more design forward more bright more animation and great for
mobile and desktop." Follow-up after the first mockup: "theres no reason the blue area should be that big ... this is
not compact."

Mockup (direction only, numbers were hand-copied, copy was shortened): https://claude.ai/artifact/XfSDFeeXdY2WJKyJw3dvD5
Branch `bright-dashboard`, worktree `/Users/mo/ADT-bright`. Base = main `95fa356`.

> **Revision 2 (after two attack reviews) is at the bottom, section 11. Where section 11 disagrees with sections 1 to 10, section 11 wins.**

## 0. Goal and hard rules

- **Goal**: the first screen answers, in this order: (1) does anything need me, (2) what is the robot doing, (3) the
  money, (4) what it holds, (5) the safety controls. History becomes an always-visible "Results" section with a bar
  per day and per playbook, instead of a drawer behind a link.
- **Display only.** Zero change under `backend/`. No websocket frame, API or trading-logic change. `git diff --stat
  main` may only list `frontend/`, `scripts/verify_*.py`, `scripts/build_*fixtures.py`, `docs/`, and the md files.
- **Every number and sentence comes from live data** through the existing hooks and `lib/plain.ts` helpers. Nothing
  from the mockup is hard-coded (the mockup's trades, dates, "Next up" lines and countdown were static).
- **Nothing the operator sees today is lost.** Every sentence, number and button on the current page stays visible or
  one click away, except the short list in section 6 (each with its reason). Measured by the text parity harness.
- **Alarms are never hidden behind a click** (rule kept from PLAN_2026_09_29). All page banners and playbook alarms
  stay visible with every row closed.
- **Operator buttons keep their exact behaviour**: same handlers, same confirm flow (`useActionButton`), same
  disabled logic, same `data-testid`s. No new operator action is added.
- **Existing `data-testid`s stay.** New ones are added, none renamed or removed (section 6 lists the two exceptions).
- **Logged decisions that still hold**: order Holding -> mood -> playbooks (2026-09-29); plain operator language; idle
  is never red; dashboards are public with no password; touch targets >= 44 px; `prefers-reduced-motion` turns every
  animation off; fonts come from `@fontsource` npm packages, never `next/font/google`.
- **Logged decision this plan replaces (flagged)**: the 2026-09-24 "light muted palette" and the 2026-09-29 "no new npm
  dependency" rule. The operator asked for "more bright" and "more design forward" today, so the palette and the
  display font change. Recorded in MEMORY.md with this plan.
- Out of scope: the "Slow trades" view layout (it only inherits the new colours), pro words content, `app/error.tsx`.

## 1. New look (tokens)

`tailwind.config.js` colours (same token names, new values, so every component re-skins at once):

| token | old | new |
|---|---|---|
| ground | #F7F3EC | #EEF1FA |
| ink | #1D1A33 | #0E1330 |
| muted | #5D5A73 | #5B6283 |
| line | #EFE4D2 | #DDE2F2 |
| darkcard | #2E3244 | #2B4BFF (the accent: status strip, selected tab) |
| gain / gainbg | #2F6B4C / #E4EFE7 | #0A7D53 / #E9F8F0 |
| loss / lossbg | #8F4424 / #F6E3DA | #C2300F / #FFEFEA |
| new: lime | - | #C6F432 (calm pill, live dot on the accent) |
| new: warn / warnbg | - | #8A4B00 / #FFF4DB (banners, "needs a look") |

- `globals.css` `--background/--foreground`, `layout.tsx` `themeColor` follow the new ground and ink.
- Display font: `@fontsource/bricolage-grotesque` (weights 600, 700, 800) replaces Fraunces in `fontFamily.display`.
  Body stays Instrument Sans. `@fontsource/fraunces` is removed from package.json and layout.tsx. `package-lock.json`
  is regenerated with `npm install` in the worktree and committed (the Docker build runs `npm ci`).
- Strategy themes in `lib/plain.ts` get brighter values, same keys and shape: Tesla #2B4BFF, Coeur #B88600, Tesla
  Retest #2B4BFF, ORB #E85A1B, Ride the Trend #0A8F9C, Big News #D61F7A, Snap Back #6D3BFF, overnight #0E1330. `ink`
  for each must reach 4.5:1 on its `tint` and `band`. Names and `what` text unchanged.
- **Hard-coded hex sweep.** Components carry about 40 inline hexes from the muted palette (`#F6E3DA`, `#8F4424`,
  `#FAF0E6`, `#7A3E1D`, `#E4EFE7`, `#2F6B4C`, `#EFD8C5`, `#F5EEE2`, `#FAF8F2`, `#4A5190`, `#3E4478`, `#EEEFF7`,
  `#A9553A`, `#5E9A7A`, `#3F7D5C`, `#E9EFE8`, `#D5E2D6`, `#2F5A4B`, `#C9CCD9`, `#F2C9A0`, `#A9D3BC`, `#E8B09E`,
  `#BCCBEA`, `#CFC6B3`, `#E2D6C2`, `#3E3A57`, `#A7A2B8`, `#F3F1EA`, `#5D5A73`, `#1D1A33`, `#EFE4D2`, `#F7F3EC`, the
  header and chart gradients ...). Each is replaced by a token class or the new value. Check: a script lists every
  `#RRGGBB` in `frontend/app|components|lib|hooks` and fails on any value in the old-palette list (the full list is
  generated from `git show main:` of those files before the change, minus values that are also in the new palette).
- No gradient washes: the header logo, the chart line and the safety meter use solid colours.
- Contrast: all text >= 4.5:1 (>= 3:1 at 24 px+). White on #2B4BFF is 5.9:1. Checked by a small script over the token
  pairs actually used (ink/muted/gain/loss/warn on white, on ground, on their bg tints; white and lime-on-accent).

## 2. Page layout (`app/page.tsx`)

DOM order (this is also the phone order, single column):

1. `Header` (logo, "Day Trader", account label, Quick/Slow toggle, Running + clock, pro words button). Same content,
   new colours, solid accent logo tile with the live ping.
2. **Status strip** (new `StatusStrip.tsx`, replaces the tall dark `RightNowCard` block): one accent-coloured row,
   about 56 px on desktop. Left: the needs-you pill (section 3). Middle: the existing `rightNowSentence(...)` text,
   `data-testid="right-now-sentence"` kept. Right: the three existing stats as small inline items with their existing
   labels ("Trades today", "Won vs lost" with the two numbers, the countdown label + value). On phones the stats wrap
   to a second line inside the strip. `RightNowCard.tsx` keeps its file name and props and becomes this strip (so
   verify_ui.mjs's required-file list does not change); no separate new file if that is simpler.
3. Page banners, unchanged logic and text, warn colours (connection, feed down, mismatch, saving, unsold overnight,
   circuit breaker, slow-trades withheld). The unsold banner stays `role="alert"`.
4. Balance card (`BalanceCard.tsx`): balance, Up/Down/Flat today chip, overnight note, the existing "Finished trades
   today" step chart with its labels, plus one new line fed by the all-time ledger (section 4):
   `Since start {signed money} · Last trading day {signed money} ({date label})`. The line is hidden until the all-time
   ledger has loaded, and on a ledger error. Solid accent chart line.
5. `OvernightHolds` (when on), `HoldingNow`, `MarketMoodCard`: same content and order, new colours only.
6. Safety card (`SafetyCard.tsx`): meter + "Daily loss limit used" line + the sentence under it + overnight result
   line stay visible; the three rule paragraphs (Moon, Target, TriangleAlert) move into one `<details>` with summary
   "The safety rules" (closed by default, >= 44 px summary); the "Close all quick trades now" button and the
   "Overnight holds are not included" note stay visible. Same props, same `data-testid`s.
7. `StrategyTable` (playbooks): same rows, new colours.
8. **Results** (new `ResultsPanel.tsx`, section 5): replaces `RecentTrades` ("What it did today") and the
   `TradeHistory` drawer.
9. Pro: `ExecutionLog` when pro words are on, unchanged.

Desktop (`lg` and up), one 12-column grid inside the existing `max-w-[1400px]`, under the header, strip and banners:

```
Row A:  Balance (5)            | Safety (7, meter and button side by side, rules in details)
Row B:  Overnight holds (12, only when on)
Row C:  Holding now (12)
Row D:  Mood strip (12)
Row E:  Playbooks table (7)    | Results (5)
```

Phone: the DOM order above. Row A stacks (Balance, then Safety). Row E stacks (Playbooks, then Results).
Rows keep `items-start`. Page gap stays `gap-3`.

**No "Next up" card.** The mockup had one. It is dropped: the playbook rows already show each playbook's hours bar,
status chip and "next" text from the real gates, and the overnight card already says when it buys and sells. A second
schedule built in the page would have to re-derive trading days, holidays and half days, and could disagree with the
gates. (Rejected, logged.)

## 3. The needs-you pill (the one new claim on the page)

A calm pill that is wrong is worse than no pill. Rules:

- New pure helper `collectAttention(inputs): string[]` in `lib/plain.ts`. It returns one short plain label per thing
  that needs the operator, from exactly the conditions that already drive a visible alarm today:
  1. connection not live (`connectionState !== "live"`)
  2. price feed down (`feedDown`)
  3. `state.broker?.mismatch`
  4. saving problem (`savingProblem`)
  5. overnight unsold after 9:31 (`unsold.length > 0`)
  6. daily loss limit hit (`state.account.is_circuit_broken`)
  7. any strategy with `orb.init_error`, the ORB "reported a problem" condition, `orb.alerts.length > 0`,
     `orb.orphans.length > 0`, or `tri_engine.last_error` (the same expressions `StrategyCard.tsx` lines 133 to 137
     use; they move into exported helpers in `lib/plain.ts` that both the card and `collectAttention` call, so the two
     can never disagree)
  8. any overnight hold or row with a non-empty `needs_look`
- Pill states:
  - no data yet (`!hasReceivedData`): the page already shows only "Connecting to the robot…", no pill.
  - `collectAttention` empty: lime pill, check icon, text **"No alarms"**. (Not "Nothing needs you": the page cannot
    know that, it only knows none of its alarms is on.)
  - 1 or more: warn-coloured pill, alert icon, text **"1 thing needs a look"** / **"N things need a look"**. The pill
    is a link (`<a href="#attention">`) to the first banner; playbook alarms stay where they are in the table.
- `data-testid="attention-pill"`, with `data-count`.
- Fail closed: any thrown error or missing field inside a condition counts as "needs a look", never as calm (each
  condition is evaluated with a guard that returns the label on exception).
- "Slow-trades withheld" (lavender info banner today) is information, not an alarm: not counted.

## 4. All-time ledger hook

- `hooks/useTodayLedger.ts` becomes `hooks/useLedger.ts` exporting `useLedger(range, ledgerRevision, isConnected)`
  with the exact same logic (drain `next_cursor`, dedupe, generation counter, 5 s retry, reconnect refetch, ET date
  rollover refetch, 20 page cap) and it also returns `sessions` (the response's `sessions` list) and `truncated`
  (true when the page cap stopped the drain). `useTodayLedger` stays as a thin wrapper `useLedger("today", ...)` in the
  same file so existing imports keep working.
- `page.tsx` calls `useLedger("all", state.ledger_revision, isConnected)` once and passes the result to `BalanceCard`
  (the one new line) and `ResultsPanel`.
- Load: one extra request per page load and per finished trade (today 51 trades, 1 page of 100; the response carries
  `fill_legs`, about 1.2 KB per trade). At about 8 trades a day the 20 page cap (2,000 trades) is reached in about a
  year; when `truncated`, Results shows "Showing the most recent 2,000 trades. Day totals are complete." (day totals
  come from `sessions`, which is not paginated).
- On error: keep the previously loaded data, show one muted line "Couldn't refresh results. Showing the last ones
  loaded." inside Results, retry with the same backoff. Never a red banner.

## 5. Results panel (`components/ResultsPanel.tsx`)

- Header: title "Results", one summary line from `summary`: `{trades_count} finished trades since {first session
  date} · {wins} won, {losses} lost`, and the saving chip from the old drawer (Saving normally / Saving is off /
  Saving problem, same logic and words).
- Two-button segmented control (`aria-pressed`): **By day** (default) and **By playbook**. One "Open all / Close all"
  button.
- One row per group, a real `<button aria-expanded>` (>= 52 px): colour dot, title, sub line, diverging bar, signed
  total, chevron.
  - By day: one group per session in `sessions` with `trades_count > 0`, plus today even when empty, newest first.
    Title `historyDateLabel(day)`; sub `{count} finished trade(s)` plus `· daily total only` for `aggregate_only`
    sessions; total = `session.realized_pnl` (fallback: sum of that day's loaded trades).
  - By playbook: one group per `strategy_id` seen in the loaded trades plus the per-strategy aggregates of
    `recovered_sessions[].strategies` (same merge `page.tsx` already does for today), title
    `strategyTheme(id).name`, dot = theme bar colour, sub `{n} trade(s) · {w} won, {l} lost`, sorted best to worst.
    The three overnight ids stay separate rows (their names already say "NVDA overnight" etc.).
  - Bar: two halves around a centre tick; width = |total| / max |total| over the visible groups; loss half uses the
    loss colour, gain half the gain colour; the sign and the number are always shown too (not colour alone). The bar
    has `aria-hidden`; the number carries the meaning.
- Open group: the trade rows of that group, newest first. Each row is a `<button>` that opens the existing trade
  detail dialog (moved verbatim from `TradeHistory.tsx`: Bought at / Sold at, Shares, Result, Playbook, Why it exited;
  Escape closes; focus returns to the row). Row content = everything `RecentTrades` and the drawer rows show today:
  company name + symbol, playbook chip (By day) or date label (By playbook), "bet it goes up / bet it goes down",
  close time ET, signed result pill.
  - Aggregate-only day: "The daily total was recovered. Individual trade details are unavailable."
  - Today with no trades: "No finished trades today."
  - A day whose `trades_count` is larger than its loaded rows (only possible when `truncated`): "Older trades from
    this day are not loaded."
- Default open: today when it has trades, else the newest day with trades. Open state is keyed by group key in
  component state and survives websocket frames and ledger refetches. Switching the grouping resets to that
  grouping's default.
- Loading (first load only): "Loading results…". Empty ledger: "No finished trades yet."
- `data-testid`s: `results-panel`, `results-tab-day`, `results-tab-playbook`, `results-group` (with `data-key`),
  `results-trade` (with `data-trade-id`), `results-open-all`, `trade-detail`.
- `RecentTrades.tsx` and `TradeHistory.tsx` are deleted; `verify_ui.mjs`'s required-file list swaps them for
  `ResultsPanel.tsx` and lists the two old files under "must not exist".

## 6. Text and testid changes (the complete list)

Removed or reworded on purpose (each goes into the parity harness's `ALLOWED_MISSING` with this reason):

| old text | what happens | why |
|---|---|---|
| "What it did today" | section is now "Results", today is its first group | history is inline |
| "Trade history" (button) | removed | nothing to open, history is on the page |
| "No finished trades today. Your previous days are saved in Trade history." | "No finished trades today." | same |
| "Right now" (small label) | removed | the strip is the status line |
| drawer-only: range buttons "Today / Yesterday / 7 days / All", tiles "Balance / Result / Trades / Fees", "Load older trades" | removed | Results always shows all days; balance is on the Balance card; the fee total was "Unavailable" in production (`fees_known: false`) |

Kept: every other string. Testids: none renamed. `history-days` (drawer) is removed with the drawer.

## 7. Motion (more, but never on a number)

- Kept: `rise` on cards (staggered), `ping2` live dot, `draw` on the balance chart, `grow` on meters, `breathe` chips.
- New: results bars grow from the centre tick (`grow`, staggered 40 ms per row, origin left or right); chevron
  rotates; group open uses a 200 ms height/opacity transition; the status strip pill has the ping when calm; hover
  lift on cards (existing `hover-card`); buttons press to 0.97.
- **No count-up or ticking on money.** A balance that is mid-animation is a wrong number on screen.
- Removed: the two blurred `drift` blobs behind the page and the gradient logo (wash tropes, and they repaint
  constantly on phones).
- All of it is off under `prefers-reduced-motion` (existing F14 block extended to the new classes).
- No animation may run on every websocket frame: animation classes are on elements whose React keys are stable, so a
  pushed frame never restarts them (checked: push 5 frames, `getAnimations()` count of running finite animations
  returns to 0 and stays 0).

## 8. Tests (all must pass before deploy)

Static and build:
1. `cd frontend && npx tsc --noEmit`, `npx next build`, `npm run test` (verify_ui.mjs updated for the file swap +
   test_websocket_resilience.mjs unchanged).
2. Old-palette hex check and contrast check (section 1), as `frontend/scripts/verify_palette.mjs`, wired into
   `npm run test`.

Browser harnesses (Playwright, mocked websocket and REST, fixed clock; all existing fixtures):
3. `scripts/verify_compact_dashboard.py --old-out <export of main 95fa356> --no-ratio`: new flag skips only the
   "<= 60% of old" ratio (old and new are both compact pages, see ERRORS.md 2026-09-30); the hard height caps, text
   parity per region, alarm hit tests, row toggles, 44 px, no overflow, no console errors, no reload all still run.
   Its `/api/trades` mock must answer `range=all` (sessions + items) as well as `range=today`.
   New hard caps for this page are pre-registered here and may not be raised without a MEMORY.md entry:
   idle desktop 1150, live desktop 1300, busy desktop 2100; idle phone 2700, live phone 3300, busy phone 5100
   (old caps + about 50 px, because Results with one open day is taller than the six-row "today" list it replaces).
4. `scripts/verify_holding_mood.py`, `scripts/verify_overnight_holds.py`, `scripts/verify_ui_redesign.py`: same pass
   counts as on main (verify_ui_redesign has 2 known `[recovered]` failures on main; they are fixed or stay exactly 2,
   and the count on main is measured first, on the same machine, before the change).
5. New `scripts/verify_bright_dashboard.py` (same mock helpers):
   - Pill: on every fixture frame, pill state == (`collectAttention` expectation hand-written per frame): `alarms`,
     `branches*`, reconnecting, feed-down, mismatch, saving, unsold, circuit-broken, ORB init error, ORB alert, ORB
     orphan, tri-engine error, overnight needs-look frames each give "needs a look" with the right count; idle, live,
     busy, weekend give "No alarms".
   - **Mutation check**: the harness is run against a build with one alarm source deleted from `collectAttention`
     (done once by hand, result recorded in the plan notes) and must fail.
   - Results: By day totals equal the mocked `sessions`; By playbook totals equal the sum of mocked trades plus
     recovered aggregates; the two groupings' grand totals are equal; toggle, Enter/Space, Open all / Close all; open
     group survives 5 pushed frames and a ledger refetch; trade row opens the dialog with the right six fields;
     Escape closes it and focus returns; aggregate-only day text; empty-today text; ledger 500 keeps old rows and
     shows the muted line; `truncated` note.
   - No animation restarts on pushed frames (section 7). Reduced-motion emulation: zero running animations.
   - Desktop 1440x900 and phone 390x844: no horizontal overflow, targets >= 44 px, no console errors, no reload.
   - Negative controls for each new check.
6. Screenshots, read by a person (me) for the whole page, not the diff: idle (weekend), live, busy, alarms; desktop
   and phone; saved under `docs/bright_dashboard/screenshots/`.

Backend: no file under `backend/` changes, so backend tests are not re-run; the diff stat is the proof.

## 9. Deploy (push to main = Railway redeploy)

Today is Sunday, market closed, the account holds 41 NVDA (overnight hold, sells Mon 9:30).
1. Before: record `/health` (status, broker.mismatch, overnight) and `/api/positions`. Confirm Railway's running
   commit is `95fa356` with one `railway deployment list` call (no polling loop, ERRORS.md 2026-09-29) so the push does
   not drop an unpushed CLI deploy (ERRORS.md 2026-10-01).
2. No file in `DAY_ONE_SOURCE_PATHS` changes, so `DAY_ONE_BUILD_REVISION` stays as it is.
3. Fast-forward `main` to `bright-dashboard`, `git push origin main`. Only this branch's commits; nothing from the
   dirty main working tree is staged (explicit paths, never `git add -A`).
4. After: `/health` 200 and "healthy"; positions and overnight state equal the "before" record; `/api/orb` rules label
   unchanged; open the live URL in a browser at desktop and phone width, read the whole page, check no old colour or
   font remains and the pill says "No alarms"; Railway logs show no `error on bar`.
5. Rollback: `git revert` the merge range and push (display-only change, no state migration).

## 10. Build order

1. Tokens, fonts, themes, hex sweep (page looks new, nothing moves). Harnesses 3 and 4 pass.
2. `useLedger`, Balance line, `ResultsPanel`, delete the drawer and RecentTrades.
3. Status strip + pill + `collectAttention` + shared alarm helpers.
4. Safety details, layout rows, motion.
5. `verify_bright_dashboard.py`, palette script, screenshots, md notes.

One commit per step, as Jhosshua, explicit paths.

## 11. Revision 2: changes after the two attack reviews (this section wins)

Two read-only reviewers attacked revision 1 (operator truth / tests / deploy, and frontend engineering). They
confirmed: `sessions` is complete and unpaginated for `range=all`; overnight trades book on the sale day; no
`DAY_ONE_SOURCE_PATHS` file is touched; `--no-ratio` only removes the ratio line; the six token contrast pairs pass;
`@fontsource/bricolage-grotesque` 5.3.0 ships the weights. Their findings and the rulings:

### 11.1 Layout (replaces the desktop rows and the phone order in section 2)

Keep today's proven two-column shape so nothing in the playbook table or holding rows has to reflow.

- Header, status strip, pro-words bar (when on, unchanged slot), banners: full width.
- **Quick trades mode**, one grid `lg:grid-cols-[minmax(0,1fr)_420px] lg:grid-rows-[auto_1fr] items-start gap-3`
  with three direct children, in this DOM order (which is the phone order):
  1. `BalanceCard`: `lg:col-start-2 lg:row-start-1`
  2. left stack (`OvernightHolds` when on, `HoldingNow`, `MarketMoodCard`, `StrategyTable`):
     `lg:col-start-1 lg:row-start-1 lg:row-span-2`, a flex column with `gap-3`
  3. right stack (`SafetyCard`, then `ResultsPanel`): `lg:col-start-2 lg:row-start-2`, a flex column with `gap-3`
  Components that can render nothing (`HoldingNow`, `OvernightHolds`) sit inside the flex stacks, where an absent
  child leaves no empty cell. No CSS `order` anywhere. The only focus-order difference from the visual order is the
  Balance card, which has nothing focusable.
- **Slow trades mode**: exactly what it shows today: `BalanceCard` full width, then `SwingTelemetryBar`,
  `ActiveSwingPositionsTable`, `SwingCandidateWatchlist`. No Safety, no Results (same as today, where Safety and
  RecentTrades are in the intraday branch only).
- Phone check, pre-registered: on every frame the top of "Holding now" (or of the playbook table when nothing is
  held) is no lower than on the old page for the same frame and viewport.

### 11.2 Status strip colours

All strip text is pure white on the accent. The only other colour is lime (#C6F432) for the pill and the wins number.
The losses number is white. The win/loss mini bar uses lime on a 30% white track. These pairs go into the contrast
script. No translucent text.

### 11.3 Attention pill (replaces the source list and the link in section 3)

- `collectAttention` returns `{ key, label }[]`, de-duplicated by `key` (one orphan or book-only hold counts once).
- Sources (each with a fixed key and a short plain label):
  1. `connection`: connectionState is not "live" (this already covers a silent stall: the stream hook marks it after
     30 s without a frame)
  2. `feed`: price feed down. An EMPTY `ingestion` map counts as down once data has arrived (today it reads calm).
  3. `mismatch`, 4. `saving`, 5. `unsold`, 6. `breaker`: as in section 3
  7. `orb-init`, `orb-problem`, `orb-alert`, `orb-orphan:{symbol}`, `tri:{strategy id}`: as in section 3, through
     helpers shared with `StrategyCard.tsx`
  8. `overnight-look:{symbol}`: any hold or row with `needs_look`; `overnight-init`: `overnight.state.init_error`
  9. `ledger`: today's ledger failed to load (`todayLedger.error`), because the strip's "Trades today" would then be
     a silent 0
  10. `unprotected:{symbol}`: a quick-trade position with no safety exit set or not linked to a strategy, using the
      same expressions `HoldingNow.tsx` uses for "No safety exit set" and "Not linked to a strategy" (moved into
      shared helpers). The transient "Checking protection..." / "Confirming protection..." states are NOT counted
      (they are normal for a few seconds after every entry and would make the pill flicker).
- NOT counted, on purpose (each is the robot working as designed and is shown where it belongs): the amber
  `NO_TRADE_TODAY` chip, "SPY and QQQ can't be read, so new trades wait" (it shows on every weekend), stale fear
  gauge, slow-trades withheld. Idle days stay calm.
- Optional fields: a field that is absent because the backend does not send it (older payload, strategy without
  `orb`) is NOT an alarm. Only a thrown error inside a guard is treated as "needs a look" (key `check-failed`).
- Calm text: **"No alarms"**. Alarm text: **"N need(s) a look"**, and the strip then shows the labels in front of the
  right-now sentence: `Needs a look: price feed down, Opening Range Breakout alert.` (`data-testid="attention-list"`).
  **No link, no scrolling, no mode switching.** This works in Slow trades mode too, because the words are in the
  strip itself.
- The unsold overnight banner keeps `role="alert"` and the loss colour (it is the page's one true alarm). The other
  banners use the warn colours.

### 11.4 Results (changes to section 5)

- Summary line from the loaded trades, so it adds up: `{n} finished trades · {w} won, {l} lost` and, when the
  summary's `trades_count` is larger than n, ` · {k} more on daily-total-only days`.
- Balance line: `Since start` = `summary.current_equity - summary.opening_equity`; `Last trading day` = the newest
  session with `trades_count > 0` whose date is before today (ET).
- By day shows today plus the newest 5 days with trades; a `Show older days` button (>= 44 px) reveals the rest.
- An open group shows its newest 6 trades and a `Show all {N}` button.
- Group rows are >= 44 px, trade rows >= 44 px.
- Day totals are `session.realized_pnl` (what the drawer shows today). The "two grand totals are equal" test is
  dropped; each grouping is checked against its own mock, with a fixture where the day total differs from the trade
  sum by a few cents.
- Bars: scale by `max(1, max |total|)`; a non-zero bar is at least 2 px.
- Results gates "Loading results…" on `items.length === 0 && sessions.length === 0`, so a refetch never flickers.
- When `truncated`: one muted line "Playbook totals cover the most recent 2,000 trades. Day totals are complete."
- Trade detail: a native `<dialog>` opened with `showModal()` (focus trap and Escape are built in), 44 px close
  button, focus returns to the row. `framer-motion` is removed from package.json (its only importer was the drawer).
- Cut: "Open all / Close all", button press-scale.
- Extra old texts for the `ALLOWED_MISSING` list: the drawer's "No finished trades on this day." and "More trades
  from this day are available. Use “Load older trades” below.".
- `hooks/useTodayLedger.ts` keeps its file name and gains `useLedger(range, ...)`; `useTodayLedger` stays exported.

### 11.5 Palette (changes to section 1)

- The builder first writes `docs/bright_dashboard/palette_map.md`: every distinct hex / rgba in
  `frontend/app|components|lib|hooks` on main (about 82), its role, and its new value or "keep". The check script
  reads that table: case-insensitive, covers `rgb()/rgba()`, fails on any colour that is neither in the "new" column
  nor on the keep-list (#FFFFFF and pure black/white alphas are kept).
- Extra tokens: `lavender` #6D3BFF, `sage` #0A7D53, `terracotta` #C2300F (alarms only).
- **Action buttons are ink (#0E1330) with white text, not red** ("Close all quick trades now", "Close trade",
  "Sell now"), so a disabled Close-all on an idle page is not red. The confirm state ("Tap again to confirm") uses the
  loss colour.
- `loss` is used only for a negative money number, the unsold alarm and confirm states. Never for a status chip.
- Strategy themes: all five values per theme are listed in the palette map. `ink` must reach 4.5:1 on white, on its
  `tint` and on its `band` (darker inks for Coeur, ORB and Ride the Trend, e.g. #7A5900, #B23E0B, #06707A; the bright
  value stays as `bar`).
- The fear-gauge neutral scale in `plain.ts` gets new neutral/amber values, still one scale, never a strategy colour.
- Swing components are in the sweep for colours only (their inline dark card becomes the accent, gradients become
  solid, the drift blobs go); their layout does not change.
- Display font weights loaded: 500, 600, 700, 800.

### 11.6 Tests (changes to section 8)

Existing harness edits, all listed:
- `frontend/scripts/verify_ui.mjs`: token assertions (line 65), `drift` keyframe (73), Fraunces (79 to 82) updated to
  the new tokens / font; required files swap `RecentTrades.tsx` and `TradeHistory.tsx` for `ResultsPanel.tsx`.
- `scripts/verify_ui_redesign.py`: the drawer checks (around lines 675 to 707: "Trade history" click, `history-days`,
  "Load older trades") are rewritten against Results. Its mocks' `sessions: []` gain real sessions.
- `scripts/verify_overnight_holds.py` line 399 and `scripts/verify_holding_mood.py` lines 98 to 100: the hard-coded
  old rgb values become the new loss / terracotta values, so the "no alarm colour in the mood card" check keeps
  meaning.
- `scripts/verify_compact_dashboard.py`: `--no-ratio`, `/api/trades` mock answers `range=all`, `ALLOWED_MISSING`
  entries from section 6 and 11.4. Caps (pre-registered): desktop idle 1150, live 1300, live_one 1350, busy 2100;
  phone idle 2950, live 3550, live_one 3750, busy 5350. A 30-session ledger fixture is used for the cap frames, so
  the caps hold with a long history.
- Pass counts: measured on main first (same machine); after the change every check that still applies passes, and
  the rewritten ones are listed in the notes.

New `scripts/verify_bright_dashboard.py`:
- One generated frame per attention source (the base live fixture with exactly one condition switched on): pill
  count is 1 and the label is in `attention-list`. Deleting any source from `collectAttention` therefore fails its
  frame; one such deletion is done by hand and recorded as the mutation proof.
- Calm frames: idle, live, busy, weekend say "No alarms" (weekend includes the "SPY and QQQ can't be read" text).
- Stall: frames stop while the mocked clock advances 40 s; the pill must leave "No alarms".
- De-dup: an orphan that also raises an ORB error counts once.
- Animation: after the page settles, count `animationstart` events while 5 frames are pushed; expect 0. Under
  reduced-motion emulation no finite animation runs.
- Results checks from section 8.5 minus the grand-total equality, plus "Show older days", "Show all N", the
  native dialog (opens, six fields, Escape, focus returns).
- Holding-top phone check (11.1).

### 11.7 Deploy (addition to section 9)

Dependency change is done with targeted `npm uninstall @fontsource/fraunces framer-motion` and `npm install
@fontsource/bricolage-grotesque`, and the lockfile diff is read to confirm no unrelated version moved.
