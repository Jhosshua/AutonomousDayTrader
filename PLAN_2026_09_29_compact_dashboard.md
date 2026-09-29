# Plan 2026-09-29: compact dashboard (same information, much less scrolling)

Operator: "finds the information on the dashboard all useful but a lot of time is spent scrolling because boxes are so big".
Design mockup: https://claude.ai/artifact/LDAiQjJDoVGgovAb6ZZhsX (desktop 1440 + phone 390). **Layout only**: every word
on the page stays verbatim from today's components and `lib/plain.ts` helpers (the mockup shortened copy and invented
two status lines; none of that ships).

## 0. Goal and hard rules

- **Goal**: the "Quick trades" view fits in about 1.2 desktop screens (1440x900) and about 3 phone screens (390x844)
  on a normal day, versus 3,255 px (3.6 screens) and 6,344 px (7.5 screens) measured live on 2026-09-29 11:56 ET
  with one open position.
- **Nothing the operator sees today is lost.** Every sentence, number and button on the current page is either still
  visible or one click away inside the row it belongs to. Measured, not claimed (section 5, text parity test).
- **Alarms are never hidden behind a click.** ORB alerts, the orphan "I closed X at Alpaca" button, ORB init/errors,
  the tri-engine "Broker issue", page banners (feed down, mismatch, saving, circuit breaker, connection) stay visible
  with every row collapsed.
- **Display only.** No backend, websocket frame, API or trading-logic change. No new npm dependency. Tailwind + the
  existing tokens (`ground`, `ink`, `muted`, `line`, strategy themes in `lib/plain.ts`) and the existing fonts.
- Touch targets stay >= 44 px (existing harness check). Existing `data-testid`s stay (verify_ui.mjs and the Playwright
  harnesses select by them); new ones are added, none renamed.
- Out of scope: the "Slow trades" view (SwingTelemetryBar, ActiveSwingPositionsTable, SwingCandidateWatchlist) and the
  TradeHistory drawer. They keep their current look. Header/summary/mood are shared and do get compacted.

## 1. Where the height goes today (desktop 1440, live 2026-09-29)

| Block | Height | Why it is tall |
|---|---|---|
| Balance + Right now | 338 px | 4-5xl numbers, p-7 padding, 130 px chart, 3 big stat tiles |
| Holding now (1 trade) | 448 px | banner + 3 tiles + note box + two 44 px buttons on their own row |
| How the robot adapts | 302 px | headline + note + 3 tall tiles |
| The 6 ways it trades | 1,320 px | 6 cards, `min-h-[420px]`, full description + detail box + hours bar + note on every card |
| Today + Safety | 491 px | p-7 padding, 5 stacked rules, 52 px button |

## 2. Layout after the change

Desktop (`lg` and up):

```
Header: logo+account | Quick/Slow toggle (moved into the header row) | Running+clock | Pro words
Summary strip (one row): Right-now sentence | Balance + Up/Down today | Finished-trades sparkline | Trades · Won vs lost · Closes in
Grid [minmax(0,1fr) | 380px]
  left:  Holding now (one row per position)      right: What it did today (compact rows)
         Mood strip (how the robot adapts)             Safety rules (meter + 3 one-line rules + Close all)
         The N ways it trades (one row per
         playbook, click to open details)
```

Order Holding -> mood -> playbooks is kept on purpose: the 2026-09-29 holding-card decision (MEMORY.md) moved
Holding above the mood card and `verify_holding_mood.py` asserts that order (JS_ORDER). The mockup had the mood strip
first; the logged decision wins.

Phone (< `lg`): same order, single column: header, toggle, summary (sentence, balance, 4 small stat tiles), mood
(chips wrap), holding (compact card per position), playbook rows (name + status + P&L on line 1, hours bar, one-line
today), today, safety.

## 3. Component changes (files under `frontend/`)

1. **`app/page.tsx`**: pass the toggle into `Header` on desktop (keep `SegmentedModeToggle` as the component; render it
   in the header row at `lg`, in its own row below the summary on phones). Replace `StrategyCarousel` with the new
   table. Wrap Holding + Strategies (left) and RecentTrades + Safety (right) in `lg:grid-cols-[minmax(0,1fr)_380px]`,
   `items-start`. Page gaps `gap-4`, page padding `sm:px-6 sm:py-5` (was `sm:px-10 sm:py-10`), `max-w-[1400px]`
   (was `max-w-6xl` = 1152 px, which wastes a third of a 1440 screen). Banners unchanged.
2. **`BalanceCard.tsx` + `RightNowCard.tsx`**: same data and logic, compact styling. Balance `text-3xl`, chart height
   44 px desktop / 40 px phone (viewBox unchanged, `preserveAspectRatio="none"` already), padding `p-4`. Right now:
   sentence `text-lg`, three stats as small inline stats (`text-xl`). Rendered as one `lg:grid-cols-[1.4fr_1fr_1.3fr]`
   strip in page.tsx (Right now left, Balance centre, Right-now stats right) OR kept as the two existing components side by
   side at compact size; builder picks the simpler one that meets the height budget. Blur blobs removed from RightNowCard
   (decorative, they force the tall card).
3. **`MarketMoodCard.tsx`**: one-line strip on `lg`: icon + title + three chips (`mood-tile-fear`, `mood-tile-time`,
   `mood-tile-trend` keep their testids and their full sentences, rendered as chip label + muted phrase) + `mood-note`
   inline + `mood-details` (See the rules) as an inline disclosure that opens BELOW the strip. `mood-headline` stays in
   the DOM and visible: on `lg` as a single muted line under the chips only when `head.tiles` is null (closed market,
   weekend, unknown) or its text says something the chips do not (stale VIX); otherwise visually hidden but readable
   (`sr-only`) so screen readers keep the sentence. Stale-VIX and weekend variants must render (fixtures exist).
4. **`HoldingNow.tsx`**: one row per position, no banner block. Desktop grid columns: strategy (dot + name + badge text
   small, `holding-strategy` / `holding-badge` / `holding-banner` testids kept, `data-kind` kept) | stock + bet line |
   P&L chip | Safety exit (+ "started at" / bracket line in small text) | Target | Time limit | buttons (Close trade /
   Sell now with the same confirm flow; Move-safety-exit button with the same enable logic and labels). The explanation
   (adaptive `holding-why` size line + stop line + trend line + "Why this size?" details, fixed-plan note, ORB box text,
   tranches `fixed-tranches`) moves to one full-width line block UNDER the row, same text, `text-xs`/`text-sm`, no tinted
   box padding. Nothing is collapsed except what is already collapsed today ("Why this size?"). Phone: compact card
   (name line, 3 small tiles, note, buttons side by side).
5. **New `StrategyTable.tsx`** (replaces `StrategyCarousel.tsx`, which is deleted and removed from verify_ui.mjs's
   required list; `StrategyCard.tsx` is refactored, not duplicated):
   - Section header "The N ways it trades" + legend.
   - One `StrategyCard` per strategy, now rendered as a ROW: a `<button aria-expanded>` summary with: colour dot + name +
     hours text (`win.hours`), status chip (`window-badge`, same chip logic incl. ORB off/shadow), hours bar
     (`strategy-window`, same segments + now line, 8 px), today note (`strategy-decisions`, same `note` and P&L code).
   - Details panel (open on click, closed by default, several may be open): `theme.what`, the pro box, and the existing
     `trend-details` / `tri-engine-details` / `orb-details` / `or15-details` blocks unchanged in content.
   - **Always visible under the summary even when closed**: `orb-alert` (with `orb-resolve-orphan`), the ORB
     init/errors alert, `tri_engine.last_error` "Broker issue". Implementation: render these alert elements outside the
     collapsible panel; inside the panel they are not repeated.
   - A row whose strategy holds an open ORB trade or has an alert shows a small "needs a look" dot in the summary.
     (Nice to have; drop it if it complicates the diff.)
   - Open/closed state in React state only (no storage).
6. **`RecentTrades.tsx` + `SafetyCard.tsx`**: compact padding `p-4`, row padding `py-2`, header `text-lg`, rules as
   single lines, Close-all button 44 px (was 52). Same copy, same testids, same buttons and confirm flow.
7. **`scripts/verify_ui.mjs`**: required-files list (StrategyTable in, StrategyCarousel out), testid map updated for
   moved testids, new asserts: StrategyCard renders alert testids outside the collapsible panel; page uses
   `StrategyTable`.

## 4. Height budget (acceptance)

Measured by the new harness (section 5) on the static export with mocked frames, `document.documentElement.scrollHeight`:

| Frame | 1440x900 | 390x844 |
|---|---|---|
| idle (market closed, flat) | <= 1,100 | <= 2,600 |
| one open fixed-plan position (like 2026-09-29) | <= 1,250 | <= 3,000 |
| busy (5 positions, all kinds) | <= 1,900 | <= 4,600 |

Plus the same frames on the OLD build (commit before this change) recorded as the "before" column in the report.

## 5. Tests

1. `npx tsc --noEmit`, `npm test` (verify_ui.mjs + websocket resilience), `npx next build` (static export).
2. Existing harnesses stay green: `scripts/verify_holding_mood.py`, `scripts/verify_ui_redesign.py`,
   `scripts/verify_visual_qa.py` (whichever run offline). Any assertion that fails because of an INTENDED layout change
   is updated in the same commit and listed in the report with the reason; content assertions are never weakened.
3. **New `scripts/verify_compact_dashboard.py`** (reuses `verify_ui_redesign` export server + frame mocks, one page per
   viewport, never reloaded, negative controls like verify_holding_mood.py):
   - Height budget table above, before (old export built into a temp dir from `git worktree` at the base commit) and after.
   - **Text parity**: for each frame (idle, busy, waiting, weekend, stale_vix, sized_down, pre_release_and_unlinked and an
     ORB-alert frame built from `scripts/orb_ui_states`), collect every visible text line of the old page and assert it
     is present on the new page after opening every playbook row and the "See the rules" disclosure. Allowed differences
     are an explicit list in the script, each with a reason (e.g. removed decorative text). The negative control deletes
     one line from the new page and proves the check fails.
   - **Alarms visible while collapsed**: ORB alert, orphan button, broker-issue alert, page banners.
   - Row toggle: aria-expanded flips, details appear, keyboard (Enter/Space) works.
   - Buttons still send the same websocket actions (sell with confirm, move stop, close all).
   - No horizontal scroll at 390; every visible button >= 44 px; idle state has no red/terracotta alarm colour.
   - Screenshots desktop + phone for idle, one-position and busy in `docs/compact_dashboard/screenshots/`.
4. Whole-page visual audit of those screenshots (not only the diff), idle AND busy.

## 6. Review and deploy

1. Subagent attack of this plan before building; accepted findings folded in (section 8).
2. Build on a branch, commit as Jhosshua.
3. Codex review of the full diff (`codex exec -s read-only`, diff inlined, untracked files `git add -N` first,
   `< /dev/null`). Fix accepted findings, re-run tests.
4. Merge to main, push = Railway redeploy. Operator said do not wait for 16:00; push when the quick-trade book is flat
   (a restart leaves local stops unmanaged for about 5 minutes; this service has a volume, so no overlap).
5. Verify live: `/health` healthy, ORB mode still `live`, broker mismatch false; Playwright screenshot of the live URL at
   1440 and 390, page heights recorded, no reload needed for new assets (hard reload once if cached).
6. Update MEMORY.md (decision entry), ERRORS.md if anything took more than 2 attempts, project memory.

## 7. Risks

- Hiding the playbook descriptions behind a click is the one real trade: first-time readers see less at a glance. The
  one-line today note and hours text stay visible; the description is one click away.
- Existing Playwright harnesses assert inner text of blocks that may now be collapsed; they must open rows first
  (content assertions unchanged).
- `max-w` change affects the Slow trades view width too (it gets wider); acceptable, check its screenshot.

## 8. Attack findings and decisions

Attack by a general-purpose subagent (read-only, 25 findings). All accepted unless noted.

- **P0 mockup copy**: layout only, copy verbatim (see top). `ALLOWED_MISSING` needs a reason per entry.
- **P0 mood headline**: never `sr-only`. The mood card keeps its structure (headline always visible, note, three tiles
  with the same testids `mood-tiles`, `mood-tile-*`, `mood-level-chip`, `mood-details`, `mood-tier-table`), only
  tighter: smaller padding, tile values `text-base`, tiles side by side from `sm`. Rejected: the chip strip (it
  shortened sentences and hid the headline).
- **P0 parity check** rebuilt: DOM text nodes (TreeWalker), whitespace-normalized and case-folded, compared as a
  **multiset per region** (holding row by symbol, playbook by name, mood card, safety card, today card, rest of page).
  A node counts only if it has a >= 2 px box inside every `overflow` clipping ancestor and is not `visibility:hidden`
  (catches `sr-only`, truncation, clipped and collapsed text). DOM negative controls: remove a node, add `sr-only`,
  truncate a node, move a line into a hidden panel; each must fail.
- **P1 live status behind a click**: always visible under each playbook row (not in the panel): ORB `step`, ORB open
  trades (side, shares, safety exit, target, R, "stop moved to the entry", "Closing: ..."), Ride the Trend "New entries
  switched off" and add-ons off, OR15 "Confirming protection with the broker.". Alarms (ORB alerts + orphan button,
  ORB init/errors, tri-engine broker issue) likewise.
- **P1 alarm check**: all `<details>` reset to closed first; each alarm must not be inside `strategy-details`, must be the
  element hit at its own centre (`elementFromPoint` after scrolling to it); negative control moves one into a hidden
  panel. `alarms.json` also turns on the page banners (feed down, mismatch, saving problem, circuit breaker).
- **P1 heights**: fresh page per measured frame, `document.fonts.ready`, trades mocked per frame (idle: none; live and
  live_one: today's two real MSFT trades; busy: the 13 busy trades).
- **P1 fixtures**: new `branches.json` frame: ORB open trade with R, break-even and exit requested; Ride the Trend mode
  off, add-ons off, refused setups; OR15 holding unconfirmed; an early-close note; parity also run with pro words on.
- **P1 parity optional**: `--old-out` required. Old export built from the base commit (done: node_modules symlinked).
- **P1 toggle**: one element, placed with CSS order (header row on `lg`, own row on phones). No duplicate testids.
- **P1 order**: Holding -> mood -> playbooks kept (see section 2).
- **P1 other harnesses**: `scripts/orb_ui_states/shoot.py` added to the test list; rows keep `<article>` + `<h3>`. All
  harnesses run on the base commit first; failures there are recorded as pre-existing, not blamed on this change
  (verify_ui_redesign's "See all trades" click is suspected pre-existing).
- **P1 structural checks**: mood testids kept as above.
- **P1 small targets**: orphan "clear it" button and "Latest refused setup" summary get `min-h-[44px]`; the size check
  includes summaries inside open panels.
- **P1 row markup**: `<article data-testid=strategy-row-ID><h3><button aria-expanded aria-controls>spans only</button></h3>
  <div>always-visible status + alarms</div><div id=... data-testid=strategy-details>panel</div></article>`. No
  interactive element inside the row button. Rows keyed by id; test that an open row stays open across 3 frames.
- **P1 deploy window**: never push 09:20-12:05 or 15:45-16:10 ET; push only with no quick-trade position and
  `working_orders_count == 0`; swing holdings checked (their protection is at the broker). Operator said do not wait for
  16:00, so push in the 12:05-15:45 window when those hold.
- **P1 truncation**: no `truncate` / `line-clamp` in changed components; the parity visibility rule catches clipping.
- **P2 holding layout**: two lines per position (strategy + stock + P&L; plan tiles + buttons), notes below. Done.
- **P2 safety copy**: verbatim, no shortening to hit a budget.
- **P2 budgets**: pass requires BOTH a hard cap and a reduction versus the old page on the same frame (desktop new <=
  60% of old, phone new <= 75% of old). Caps set before measuring the new build: desktop idle 1100, live 1250,
  live_one 1300, busy 2000; phone idle 2600, live 3200, live_one 3400, busy 5000.
  **Result**: all met except busy desktop (5 trades open at once): 2,038 px vs 2,000 (42% of the old 4,863). The
  remainder is each trade's own explanation, which this plan forbids hiding; cap raised to 2,050 and recorded.
- **P2 scope cuts**: "needs a look" dot dropped (the always-visible status does that job). verify_ui.mjs source-grep for
  "alerts outside the panel" dropped (the Playwright check proves it). Toggle-in-header KEPT (one element, CSS order,
  saves ~60 px). orb-alert / orb-resolve-orphan / resolve-orphan URL stay in StrategyCard.tsx.
- **P2 axis labels**: one shared "9:30 / noon / 4 PM" axis in the table header replaces six per-card copies (and puts
  "noon" at its true position, 38%, instead of the middle). Allowed in parity as "present at least once on the page".
- **P2 ORB holding look**: dashed border + icon kept; `holding-banner` wraps only the strategy pill.
- **P2 staging**: explicit paths only (untracked backend files exist in the tree).
- **P2 console errors**: harness listens for console errors as well as page errors.

