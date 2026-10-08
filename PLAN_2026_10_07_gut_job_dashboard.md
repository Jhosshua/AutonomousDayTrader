# Plan 2026-10-07: gut-job dashboard (operator first, compact, colourful)

Status: plan written 2026-10-07 21:50 ET. Section 12 holds rulings from two attack agents. Build by Codex in a clean worktree, diff reviewed by Claude, then visual audit, tests, deploy.

Design canvas (the source of truth for layout and copy): https://claude.ai/artifact/28eny44kozerbmPtn7M9nA
Boards: Intro (1.2 s), Home 9 PM (3 overnight holds), Home 9:52 AM (trade open + 1 alarm), Home 3:35 PM (overnight lock in 14 min), Icons + app icon, Desktop.

## 1. Why

The operator (2026-10-07 ~21:00 ET): "right now it's confusing, some of it doesn't work, the graphs don't work and the holdings section is inaccurate." Verified on the live page at 21:03 ET:

1. `HoldingNow` receives `intradayPositions` only; the three overnight holds are filtered out (`page.tsx`, `isOvernightPosition`), so the Holdings card says "No quick trade holdings are open" under three overnight rows.
2. The only chart is the Day view's cumulative finished-trade line: one dot on a one-trade day. "Best day" and "Worst day" show the same day in Day view.
3. After the close the backend reports `market_price == entry_price` and `unrealized_pnl == 0` for overnight holds, and the page prints those as if real.
4. At 390 px the Quick/Slow toggle and the Today/Holdings/History/Plans/Controls bar run off the right edge.
5. First paint is a spinner with "Connecting to the robot…" and no content.
6. No favicon, no `apple-icon`, no manifest. A Safari bookmark gets a generic tile.
7. The page is 2.5 phone screens of mostly idle "Done for today" rows after the close.

Reconciliation with the 10-06 plan (`PLAN_2026_10_06_OPERATOR_FIRST_UI_REDESIGN_V3.md`, Cobalt Ledger Performance/History panels deployed 19:48 ET 10-07): keep the History explorer (Day/Week/Month/All, Days/Plans/Trades) as its own page reachable from the Results card. Remove the Performance panel from the Today page. Keep the brand name Cobalt Ledger, the fonts, the lib code (`performance.ts`, `historyView.ts`, `plain.ts`) and all hooks.

## 2. Scope

Display-only. `backend/` does not change (so `DAY_ONE_BUILD_REVISION` is untouched). All numbers come from the websocket frame and the existing GET endpoints. No new npm dependency. No hard-coded mockup numbers.

Out of scope: a "Pause new trades" button (no endpoint), any new backend field, any change to action semantics (`FLATTEN_POSITION`, `TIGHTEN_STOP`, `FLATTEN_ALL`, `/api/overnight/no-buy-tonight`, swing actions stay exactly as wired in `useTradingStream`).

## 3. Page order (Today page, phone = desktop left column)

1. **TopBar**: mark + "Cobalt Ledger" + freshness pill.
2. **StatusCard**: one sentence (`rightNowSentence`) + alarm pill ("No alarms" lime / "N need a look" amber) + at most the first alarm expanded as an amber block WITH its action button when one exists + the 3:49 PM lock countdown when inside the last 20 minutes.
3. **MoneyTiles**: Account ($ equity, sub: $cash) · Today (account change $, %, sub: n trades) · Last 7 days (finished-trade result $, sub: n trades · wins).
4. **Holdings**: EVERY open position: day trades (existing row with stop strip and Sell now / Move stop), overnight holds (same row shape; after the close: "bought $X · price at 9:30", no result number), swing holdings (row with its two exit buttons and tighten stop). Header note for overnight: sells at the 9:30 open, no stop by design, "IREN and HUT are both bitcoin miners and move together" (keep the existing copy from `OvernightHolds`). Empty state: calm grey "Holding nothing" (never red).
5. **Results**: bars per trading day, last 7 days, from `useLedger("7d")` grouped by `session_date`, green/red by sign, label = signed dollars, axis = weekday + day number; a day with zero trades shows "$0". Link "Open full history" → History page.
6. **Playbooks**: the existing `StrategyTable`/`StrategyCard` rows (keep all testids), plus a 7-day result per `strategy_id` from the 7d ledger (overnight row sums the three `overnight_*` ids). Wrapped in `<details>` that is CLOSED when `market_status` is not open and every day playbook is DONE_FOR_DAY, OPEN otherwise; summary line "6 done for today · Tesla −$39 · Overnight holding 3" built from the same data.
7. **Controls**: "Close all day trades now" (disabled when 0 day trades, label says "nothing open"), "Skip tonight's overnight buy" (existing `noBuyDisabledReason`; when inside the last 20 minutes before the lock, this same button also appears in the StatusCard), both two-tap via `useActionButton`. No Pause.
8. **Pro details** `<details>` (replaces "Show pro words"): account number, broker agree/mismatch + position counts, feeds, VIX + mood headline (`moodHeadline`), loss stop used/limit, ORB rules string, research counters, ExecutionLog (20 rows), swing candidates watchlist, swing telemetry.
9. **History page** (existing `HistoryExplorer`) replaces the Today page when opened; a "Back" link returns.

Desktop (≥1024 px): TopBar full width; StatusCard + MoneyTiles on one band; left column Holdings + Playbooks; right column Results + Controls + Pro details. DOM order = phone order, no CSS `order`.

## 4. Components (new files under `frontend/components/gut/`)

- `TopBar.tsx`: props `timestamp`, `connectionState`, `marketOpen`. Pill text: "Running · updated 12 s ago" (age from `state.timestamp` re-rendered on a 1 s interval); amber + "stale 2 min" when age > 60 s or `connectionState !== "live"`; the dot pulses (CSS, reduced-motion safe) only while `marketOpen`.
- `StatusCard.tsx`: props `sentence`, `attention` (from `collectAttention`), `firstAlarm` ({text, action?: {label, onConfirm}}), `clock` ("Wed Oct 7 · 9:03 PM · opens in 12h 27m" from existing helpers), `lockCountdown`.
- `MoneyTiles.tsx`.
- `Holdings.tsx`: wraps the existing `HoldingNow` row renderer for day trades, adds overnight and swing rows. Overnight "price at 9:30" rule: `isOvernightPosition(p) && market not open && p.market_price === p.entry_price`.
- `ResultsBars.tsx`: pure, props `days: {date, label, pnl, trades}[]`, `total`, `onOpenHistory`.
- `PlaybookPanel.tsx`: `<details>` around `StrategyTable` with the summary line; `StrategyCard` gains an optional `weekResult` prop rendered at the row's right.
- `ControlsCard.tsx`.
- `ProDetails.tsx`.
- `Intro.tsx`: full-screen overlay, CSS keyframes only (ring draws 0.35 s, arrow 0.3 s, 7 dots with `animation-delay` 0.20–0.38 s, wordmark 0.3 s, fade 0.9–1.2 s), `sessionStorage.cobaltIntroSeen`, `?intro=off` kill switch, Skip link, `prefers-reduced-motion` → not shown. Mounted in `page.tsx` above everything; does not block the socket.
- `BrandMark.tsx`: the inline SVG (white open ring + lime arrow on #2B4BFF).
- `lib/gut.ts`: pure helpers with tests: `groupBySessionDate(items)`, `weekResultByStrategy(items)`, `freshnessLabel(ageSec, connectionState)`, `playbookSummaryLine(strategies, ledger7d, overnight)`, `overnightPriceIsStale(p, marketStatus)`, `firstAlarmAction(attention, positions)`.

Removed from the Today page (files stay until the diff review decides): `PerformancePanel`, `RecentHistory`, `SegmentedModeToggle`, `DashboardNavigation`, `SafetyCard` (its loss-stop numbers move to the Today tile and Pro details; its Close-all button moves to ControlsCard; `btn-flatten-all` testid kept on the new button), `MarketMoodCard` (headline moves to Pro details), `RightNowCard` (replaced by StatusCard; keep testids `right-now-sentence` and `status-strip` on the new elements), the "Show pro words" toggle (`pro-words-toggle` testid moves to the Pro details summary).

## 5. Alarms (collectAttention additions, frontend only)

Keep the existing list. Add, each with a one-source frame field already present:
- overnight sale unfilled after 9:31 (`unsold`, already) → action "Sell SYMBOL now" = `flattenPosition(symbol)`
- overnight lock within 20 minutes and buys planned → informational, with the Skip button
- loss stop more than 50% used (`risk_drawdown / maxDailyLossDollars`)
- frame older than 60 s (freshness)
Everything else stays as is. The pill count equals the number of alarm entries; a new alarm anywhere on the page must be added to `collectAttention` (existing rule from 10-04).

## 6. Icons and bookmark

- `frontend/app/icon.png` (32×32: lime arrow only on #2B4BFF, rounded 6 px) and `frontend/app/apple-icon.png` (180×180: white ring + lime arrow on flat #2B4BFF, square; iOS masks it). Rasterise the SVGs in `docs/gut_dashboard/icons/*.svg` with headless Chrome (`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless=new --screenshot --window-size=WxH file://...html`) and commit the PNGs.
- `frontend/app/manifest.ts` (Next metadata route): name "Cobalt Ledger", `display: "standalone"`, `theme_color` and `background_color` "#2B4BFF" / "#F4F5FA", icons list. `layout.tsx` themeColor → "#2B4BFF".

## 7. Palette and type

Ground #F4F5FA, card #FFFFFF, line #E2E5F0, ink #0E1330, muted #5B6180, accent #2B4BFF, lime #C6F432, gain #0A7D53, loss #C2300F, warn #FFF4DB/#8A4B00. Playbook hues: ORB #E85A1B, Ride #0E8C7F, Big News #D6307A, Snap Back #6B3FD6, Tesla #2B4BFF, Coeur #5C4A00 (was gold; darkened so colour-blind viewers separate it from ORB orange), Overnight #1F2A6B. Update `docs/bright_dashboard/palette_map.md` Table C and `STRATEGY_THEMES` in `plain.ts` for Coeur only. Fonts unchanged. Idle states never red.

## 8. Data rules

- Account tile: `account.equity`, `account.cash`.
- Today: `equity − daily_starting_equity` (fallback `daily_pnl`), percent of opening; sub-line `todayLedger.summary.trades_count`.
- Last 7 days: sum of `realized_pnl` over `useLedger("7d").items` (deduped by `trade_id`), plus recovered aggregate sessions as `page.tsx` already does for today.
- Results bars: group the same items by `session_date`; list the trading days in the window even when empty (derive the day list from the sessions in the 7d response plus today).
- Playbook 7-day result: group by `strategy_id`; overnight row = `overnight_nvda + overnight_iren + overnight_hut`.
- Freshness: `Date.now() − Date.parse(state.timestamp)`.
- Market open: `market_context.market_status === "OPEN"` (check the exact value in `plain.ts` `moodHeadline`).

## 9. Verification (must all pass before deploy)

1. `cd frontend && npm test` (existing scripts) plus a new `scripts/verify_gut.mjs` covering `lib/gut.ts` (session grouping with an empty day, week result by strategy incl. overnight sum, freshness thresholds, stale overnight price rule, summary line text, first-alarm action mapping).
2. `verify_attention.mjs` extended for the 3 new alarms.
3. `verify_palette.mjs` passes with the Coeur change.
4. `npm run build` (static export) succeeds.
5. New `scripts/verify_gut_dashboard.py`: serves `frontend/out` with a fixture websocket (reuse `scripts/build_*_fixtures.py` patterns) in three states (evening 3 overnight holds; 9:52 AM one day trade + unsold overnight; 3:35 PM flat) and asserts: holdings count equals `all_positions` length in every state, no "$0" on an overnight row after the close, the pill text matches the alarm count, no element's right edge passes 390 px, page height ≤ 1,300 px in the evening state, `btn-flatten-all` disabled with 0 day trades, playbooks `<details>` closed in the evening state and open at 9:52.
6. Old scripts tied to the removed layout (`verify_bright_dashboard.py`, `verify_compact_dashboard.py`, `verify_cobalt_ledger.py`, `verify_holding_mood.py`) are retired to `scripts/retired/` in the same commit with a one-line note each; they would fail by design.
7. Full-page screenshots at 1280 and 390 of all three fixture states, committed under `docs/gut_dashboard/screenshots/`, looked at by a human-standing reviewer (Claude) before deploy.

## 10. Deploy

Push to `main` redeploys Railway. Before the push: `git diff --stat origin/main -- backend/` must be empty (no `DAY_ONE_BUILD_REVISION` change needed). After deploy: `/health` 200, `/ready` 200, `day_one.source_revision_match` true, screenshot of the live page at 390 and 1280, the holdings count on the live page equals `/api/positions` count. Market closed at deploy time; restart is checkpoint-safe (MEMORY.md 2026-09-24).

## 11. Build instructions for Codex

Work in the worktree you are given (branch `gut-dashboard`, based on `origin/main`). Touch only `frontend/`, `docs/gut_dashboard/`, `docs/bright_dashboard/palette_map.md`, `scripts/verify_gut_dashboard.py`, `scripts/retired/`. Do not touch `backend/`. Keep every testid listed in section 4. Run `cd frontend && npm ci && npm test && npm run build` before you finish and report the exact output. Do not commit; leave the changes in the working tree and write `docs/gut_dashboard/BUILD_REPORT.md` with: files changed, every testid kept or moved, every verification command and its result, anything in this plan you could not do and why.

## 12. Attack rulings (two agents: code wiring, operator risk). These override sections 3 to 9 where they differ.

R1. **Overnight holds get no Sell now, no Move stop, and the unsold alarm has NO button.** `_execute_manual_flatten` (main.py ~4256) skips overnight holds and the websocket path drops the reply. The unsold alarm reads: "SYMBOL overnight did not sell at the 9:30 open. The robot keeps retrying. To sell it by hand use the Alpaca app." Every alarm action must map to a handler that accepts that position kind; currently only day trades (Sell now, Move stop) and swing rows (their three actions) have buttons.

R2. **First frame gate stays.** Until `hasReceivedData`, render the TopBar plus grey skeleton cards with one line "Connecting to the robot…". Never render `INITIAL_STATE` numbers ($0 equity, placeholder playbooks, "Holding nothing"). After the first frame, a lost socket shows the existing reconnecting state; the pill turns amber from `connectionState !== "live"` (the hook already flips to "stale" at 30 s). No new freshness alarm in `collectAttention`. The pill's "updated N s ago" uses `state.lastUpdated` (the time the page received the frame), not the server timestamp.

R3. **"Market open" is NOT `market_status === "OPEN"`.** `market_status` is clock-only (OPEN 9:30 to 15:45 even on weekends, FLATTENING 15:45 to 16:00). Define `marketOpen = ["OPEN","FLATTENING"].includes(market_status) && tradingDay !== false` where `tradingDay` comes from `strategies[].window.trading_day` as `page.tsx` already derives it. The overnight stale-price rule: overnight hold AND (ET minutes ≥ 16:00 OR < 9:30 OR not a trading day) → row shows "bought $X · price at 9:30" with no result. Any other position while `!marketOpen` whose `market_price === entry_price` shows the label "last close price" next to the price. Add a Sunday fixture where `market_status` says OPEN.

R4. **Playbooks `<details>` open rule:** open when any card's window state is CAN_TRADE, LIMITED, BLOCKED, MANAGING or NO_TRADE_TODAY, or any day position is held; closed otherwise (DONE_FOR_DAY, MARKET_CLOSED, PAUSED, premarket states). Hold it in `useState`, set once on the first real frame, then only `onToggle` changes it. Never re-apply on each 10 s frame.

R5. **Palette: keep the current Tailwind tokens** (ground #EEF1FA, line #DDE2F2, muted #5B6283, ink, accent #2B4BFF, lime #C6F432, gain, loss, warn). The canvas's near-identical greys map to those tokens. The ONLY theme change is Coeur: all five fields of its `STRATEGY_THEMES` entry (band/ink/tint/bar/track) move to a #5C4A00 family (band #5C4A00, ink #3D3100, tint #F3EEDC, bar #5C4A00, track #E9E1C4), and those hexes are added to `docs/bright_dashboard/palette_map.md` Table C. `verify_palette.mjs` must pass unchanged in logic.

R6. **Scripts.** Retire to `scripts/retired/` with a one-line header note: `verify_bright_dashboard.py`, `verify_compact_dashboard.py`, `verify_cobalt_ledger.py`, `verify_holding_mood.py`, `verify_overnight_holds.py`, `verify_overnight_row.py`, `verify_visual_qa_live.py`, `verify_visual_qa.py`. Leave `verify_ui_redesign.py` in place untouched (other files import it as `base`); it is not run. The new `scripts/verify_gut_dashboard.py` is self-contained (its own fixture server, no import from retired files) and PORTS the word-for-word overnight copy assertions from `verify_overnight_holds.py` (hold line, tonight line, no-buy reason text) so that copy does not drift. `frontend/scripts/verify_ui.mjs` lines ~197-199 (requires `<PerformancePanel` and `<DashboardNavigation` in page.tsx) are rewritten for the new layout; its `window.confirm` scan must include `components/gut`. All new `collectAttention` inputs are optional so `verify_attention.mjs`'s old cases still pass.

R7. **Testids kept (add to section 4's list):** `attention-pill`, `attention-list`, `overnight-unsold-banner`, `overnight-holds`, `overnight-hold-<SYM>`, `btn-no-buy-tonight`, `overnight-no-buy-reason`, `overnight-no-buy-reply`, `risk-telemetry`, `holding-row-<SYM>`, `btn-sell-now-<SYM>`, `btn-flatten-all`, `right-now-sentence`, `status-strip`, `strategy-*` (all), `active-swing-positions`, `pro-words-toggle` (on the Pro details summary).

R8. **One Skip button.** Extract the existing no-buy control from `OvernightHolds.tsx` (async REST toggle, on/off, 409 reason reply) into `NoBuyTonightButton.tsx` and render it ONCE, in ControlsCard. The StatusCard's lock line ("Overnight buy locks in 14 min") is a link that scrolls to it. The lock time comes from the backend (`overnight.no_buy_until`, already used for `tooLate`); when missing there is no countdown. The lock countdown is NOT an entry in `collectAttention` (idle is never amber). The header dot is static, no pulse.

R9. **"Close all" stays enabled while `marketOpen`** even with 0 day trades, because FLATTEN_ALL also cancels working day orders and stops ORB for the rest of the day and it is the only brake (no Pause). Label: "Close day trades and stop new ones today"; sub-line "N open · never touches overnight or slow trades". Disabled with the reason "market closed" otherwise. Two-tap via `useActionButton` as today.

R10. **Money tiles wording:** tile 2 is "Account change today" ($ and %, basis = daily opening equity) with sub-line "Finished trades: −$39 · 1"; tile 3 is "Finished trades, 7 days" from `summary.realized_pnl` and `summary.trades_count` of `useLedger("7d")` (these already include recovered sessions). Add `"7d"` to `LedgerRange`. Include the 7d error in `resultsError`.

R11. **Results bars:** list the Mon to Fri dates in the 7-day window yourself (ET). Past days read `sessions[].finished_trade_result` (null → "?" bar, never $0); today reads the items. A weekday with no session row and no items shows "$0". Assert in the new test that the bar total equals History's Week total for the same dates.

R12. **Holdings rows:** day trades from `all_positions` minus swing symbols minus overnight; overnight from `all_positions.filter(isOvernightPosition)`; swing rows from `swing.positions` with their three buttons. The page count check in verify_gut_dashboard compares rows to `Object.keys(/api/positions)` AND a mutation check: re-adding the old overnight filter must make the gate fail. The empty state is grey "Holding nothing" only when `broker.mismatch` is false; with a mismatch it is the amber "Broker and robot disagree" line (existing banner copy) instead.

R13. **Desktop layout** may use CSS grid with explicit `grid-template-areas`; DOM order stays the phone order.

R14. **Intro:** renders nothing on the server; in `useEffect` it reads `window.location.search` (`?intro=off` kill switch) and `localStorage.cobaltIntroDate` (play once per ET date, try/catch), skips entirely when `prefers-reduced-motion` or when the first frame carries any attention item; overlay is `pointer-events: none` and unmounts at 1.2 s. The page beneath renders and connects regardless.

R15. **Icon family delivered in product:** a `PlaybookIcon.tsx` with the seven canvas glyphs (ORB, Ride, Big News, Snap Back, Tesla, Coeur, Overnight) and the state glyphs (running, paused, closed moon, needs a look, flat, done), used in the playbook rows and the StatusCard. `manifest.ts` uses `display: "minimal-ui"` (keeps a reload affordance). Note for the operator: iOS keeps the old home-screen tile until the bookmark is removed and re-added.

R16. **Verification additions:** fixtures for (a) no frame yet, (b) Sunday with `market_status` OPEN, (c) broker mismatch with 0 robot positions, (d) the three canvas states; right-edge check at 390 AND 320 px; evening height ≤ 1,300 px measured at 390 px; the live-bundle check after deploy runs the 9:52 fixture against the deployed `frontend/out` served locally from the image's build (or the same commit built locally), plus the live page compared with Alpaca's own positions, not `/api/positions`.

R17. **Deploy window:** push tonight, full live check tonight. Rollback deadline is 08:50 ET; after that no push until 16:10 (the fleet's deploy blackout), so a broken page would stay up through the 9:30 overnight sale. A failed Docker `npm run build` is safe (old deployment stays up).
