# Gut dashboard build report — 2026-10-07

Implemented in the `gut-dashboard` worktree. No commit or deployment was performed. No backend file changed, no npm dependency was added, and the websocket/action hook is unchanged.

The static build succeeds. The complete browser gate passes **233 checks, 0 failures**, including an actual source mutation and rebuild. The evening page is **1,295 px high at 390 px**. The six required states pass right-edge checks at **390 and 320 px**, with additional desktop checks at 1280 px. Twelve full-page screenshots are included. The requested `npm test` command **fails** at the exact Coeur colors versus the unchanged palette contrast test (details below). The remaining suites were also run individually and passed; this is not an all-green npm test result.

## Delivered behavior

- Phone order: TopBar, StatusCard, three money tiles, all holdings, Results, Playbooks, Controls, Pro details. Desktop uses explicit grid areas. History replaces Today and Back returns with the saved playbook disclosure choice.
- First frame shows only the brand/freshness header and grey skeletons. No initial account numbers, placeholder playbooks or false empty holdings. Freshness uses `lastUpdated`, with a static dot and a one-second age refresh.
- Market-open definition includes OPEN and FLATTENING and excludes non-trading days. Overnight prices/results are hidden outside 9:30–16:00 ET and on closed dates regardless of price equality. Other entry-equal prices while closed say “last close price”.
- Overnight holdings and unsold alarms have no trading buttons. Day and swing rows retain their existing handlers. Close-all is enabled while marketOpen even when flat, with a two-tap confirmation.
- One extracted async NoBuyTonightButton in Controls, including the original two-tap skip, one-tap undo, disabled reasons, reply expiry and 409 response. The backend lock countdown links to it; the countdown is not an alarm.
- Seven-day money totals use ledger summaries. Bars use past session results and today's deduped items, include empty weekdays, and preserve unknown results. Tests compare the bars with History's Week finished-trade total. Strategy results include recovered aggregates and sum the three overnight strategies.
- Only Coeur's theme entry changes; all five values match R5. Tailwind tokens, other strategy entries, dependencies, `performance.ts`, `historyView.ts`, `useTradingStream.ts`, and `verify_palette.mjs` remain unchanged.
- Canvas SVG paths supply the brand, seven playbook glyphs and six state glyphs. Intro is once per ET date, starts only after a calm first frame, supports `?intro=off`, reduced motion and denied storage, and unmounts after 1.2 seconds.
- Pro details shows account/broker counts, feeds, VIX/mood, loss-stop telemetry, frame-provided ORB rules, `/health` research counters, execution log, swing watchlist and telemetry. Missing diagnostics are labelled unavailable/not reported.

## Unresolved requirements and plan differences

1. **R5 is internally inconsistent.** Its required `band: #5C4A00` and `ink: #3D3100` have **1.48654:1** contrast. The unchanged `verify_palette.mjs` asserts at least **4.5:1** for every theme's ink on band. Both exact values and the unchanged checker were retained, so `npm test` fails at this one assertion. The rendered Coeur glyph uses white on its dark bar and its text uses ink on white/light tint; the old dark-ink-on-dark-band pair is not used in the new layout. No test was bypassed or weakened. Clarification was requested during the build; none was received.
2. **R6's retired copy assertions were already stale on the supplied branch.** `holdLine` already said “near the close”, and the current tonight helper describes a market buy sent at 3:59:30 with checking through 3:59:55. The retired test instead promised a fill at the 4:00 close and checking only until 3:49:30. The new verifier carries the original hold/tonight/no-buy expected strings verbatim, then explicitly translates the pre-existing timing wording for its active assertions. Current product timing helpers were preserved. All no-buy reason and reply strings are asserted verbatim. Therefore the active hold/tonight assertions are **not literally unchanged**, as R6 requests; the exact substitutions are visible in `copy_checks`. This conflict was raised for clarification during work. R1's new unsold text overrides the old unsold assertion.
3. No deployment, push, live Alpaca comparison or R16 deployed-image check was performed: this request ends with an uncommitted worktree. The verifier supports `--out /path/to/extracted/frontend/out` for that later check. These fixture checks do not establish live market scans, fills or account state.
4. Screenshots were visually inspected by this build agent. The separate human/Claude review required before deployment has not occurred. Files are left uncommitted, per the explicit instruction, including screenshots and PNG icons.
5. The original mockups show fewer words and different button availability; R1/R2/R3/R8/R9/R10/R14/R15 take precedence. In particular the afternoon disclosure opens because a playbook can trade (R4), and the empty-position Close-all brake stays enabled while the market is open (R9).

## Verification commands and results

`cd frontend && npm ci` succeeded: 111 packages installed from the existing lockfile. No package or lockfile dependency change.

The required final commands were run in order: `npm test`, then `npm run build` from `frontend/`, then `python3 scripts/verify_gut_dashboard.py` from the repository root. The browser command required the execution system's approved local server/Chrome permission outside the sandbox. It serves fixture data only and tears down its server and browser in `finally` blocks. The mutation is built in a temporary copy, not in the working tree.

### Exact tail: `cd frontend && npm test` — exit 1

```text
  ✅ Verified WebSocket action payloads are unchanged (port 8005, all 6 actions)
  ✅ Verified no window.confirm/alert/prompt usage
  ✅ Verified all required data-testids are present on their new elements
  ✅ Verified all five strategy themes and OR15 fixed protection controls
  ✅ Verified Cobalt Ledger performance, history, and navigation integration
  ✅ Verified B1 button copy change (never 'stop everything')
  ✅ Verified overflow-x guard on body
  ✅ Verified UI safe port 3005 allocation (avoiding host port 3000 collision)

🎉 All plain-language dashboard architectural checks PASSED!
Palette and contrast check (bright operator dashboard)...
  ok: 46 files, every colour is in the palette map (78 allowed values)
node:internal/modules/run_main:123
    triggerUncaughtException(
    ^

AssertionError [ERR_ASSERTION]: contrast too low:
  theme cde_asymmetric_dual: ink on band #3D3100 on #5C4A00 = 1.49 (< 4.5)

1 !== 0

    at main (file:///Users/mo/AutonomousDayTrader-gut/frontend/scripts/verify_palette.mjs:165:10)
    at file:///Users/mo/AutonomousDayTrader-gut/frontend/scripts/verify_palette.mjs:191:3
    at ModuleJob.run (node:internal/modules/esm/module_job:343:25)
    at async onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:665:26)
    at async asyncRunEntryPointWithESMLoader (node:internal/modules/run_main:117:5) {
  generatedMessage: false,
  code: 'ERR_ASSERTION',
  actual: 1,
  expected: 0,
  operator: 'strictEqual',
  diff: 'simple'
}

Node.js v22.22.2
```

### Exact tail: `cd frontend && npm run build` — exit 0

```text
   ▲ Next.js 15.5.27

   Creating an optimized production build ...
 ✓ Compiled successfully in 1131ms
   Linting and checking validity of types ...
   Collecting page data ...
   Generating static pages (0/7) ...
   Generating static pages (1/7) 
   Generating static pages (3/7) 
   Generating static pages (5/7) 
 ✓ Generating static pages (7/7)
   Finalizing page optimization ...
   Collecting build traces ...
   Exporting (0/2) ...
 ✓ Exporting (2/2)

Route (app)                                 Size  First Load JS
┌ ○ /                                    47.8 kB         151 kB
├ ○ /_not-found                            998 B         104 kB
├ ○ /apple-icon.png                          0 B            0 B
├ ○ /icon.png                                0 B            0 B
└ ○ /manifest.webmanifest                  120 B         103 kB
+ First Load JS shared by all             103 kB
  ├ chunks/255-9acdc15b78d766e6.js       46.6 kB
  ├ chunks/4bd1b696-c023c6e3521b1417.js  54.2 kB
  └ other shared chunks (total)          1.89 kB


○  (Static)  prerendered as static content

```

### Exact tail: `python3 scripts/verify_gut_dashboard.py` — exit 0

```text
  ok   reduced-motion skips intro
  ok   mixed/390 overnight, day and swing positions counted once
  ok   mixed/390 day entry-equal price labelled
  ok   mixed/390 swing entry-equal price labelled
  ok   mixed/390 unequal overnight price still stale
  ok   mixed/390 all three swing handlers preserved
  ok   mixed/390 ORB rules and research from live-shaped data
  ok   mixed/390 expanded pro details and swing fit []
  ok   mixed/390 no page errors
  ok   mixed/320 overnight, day and swing positions counted once
  ok   mixed/320 day entry-equal price labelled
  ok   mixed/320 swing entry-equal price labelled
  ok   mixed/320 unequal overnight price still stale
  ok   mixed/320 all three swing handlers preserved
  ok   mixed/320 ORB rules and research from live-shaped data
  ok   mixed/320 expanded pro details and swing fit []
  ok   mixed/320 no page errors
  ok   lost freshness preserves last received numbers with amber pill
  ok   stale socket retains data, no return to initial state
  ok   stale connection counts once, no extra freshness alarm
  ok   7d fetch error reaches attention and Results
  ok   storage denial does not block intro cleanup or dashboard

=== source mutation: re-add the old overnight exclusion
  ok   source mutation rejected: rendered [], API still ['HUT', 'IREN', 'NVDA']

233 passed, 0 failed
Fixture server stopped; browser closed; temporary mutation build removed.
```

Because npm test stops at the palette failure, these unchanged downstream suites were run separately using the same commands from package.json:

| Command in frontend | Result |
|---|---|
| `node scripts/verify_ui.mjs` | pass |
| `node scripts/verify_palette.mjs` | fails only Coeur ink/band contrast, same failure as npm test |
| `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --disable-warning=ExperimentalWarning --experimental-strip-types scripts/verify_performance.mjs` | 57 passed |
| `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --disable-warning=ExperimentalWarning --experimental-strip-types scripts/verify_history_view.mjs` | 8 passed |
| `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --disable-warning=ExperimentalWarning --experimental-strip-types scripts/verify_attention.mjs` | 60 passed |
| `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --disable-warning=ExperimentalWarning --experimental-strip-types scripts/verify_overnight_row.mjs` | 72 passed |
| `node scripts/test_websocket_resilience.mjs` | all 5 stress tests passed |
| `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --disable-warning=ExperimentalWarning --experimental-strip-types scripts/verify_gut.mjs` | 55 passed |

Additional checks: `npx tsc --noEmit` in frontend passed during implementation; subsequent production builds repeat type checking. `PYTHONPYCACHEPREFIX=/tmp/gut-pycache python3 -m py_compile scripts/verify_gut_dashboard.py` passed. `git diff --check` passed. `git diff --name-only origin/main -- backend/` is empty. The dependency lockfile, websocket hook, performance/history helpers, palette checker and `scripts/verify_ui_redesign.py` have no diff.

The browser gate includes no frame, evening, 09:52, 15:35, Sunday-OPEN and empty-robot broker mismatch; live-shaped mixed day/swing/overnight rows; right edges including expanded Pro details; source mutation; two-tap day close and Close-all; all swing payloads; Skip/undo/409; History navigation and totals; user disclosure persistence; stale socket; 7d failure propagation; intro date/reduced-motion/storage behavior; and unchanged no-buy copy.

## Icons and browser route

`command -v browser-use` found `/Users/mo/.local/bin/browser-use`; `browser-use --doctor` reported no running Chrome/daemon connection. No existing user browser tabs or permissions were changed. The sandboxed headless command aborted (134), but the same isolated local Chrome route succeeded through approved execution outside the sandbox. This does not repair or establish connectivity for the bundled Chrome plugin.

Both PNGs were created directly by the requested Chrome executable from exact-size inline SVG HTML, with body margin zero, and checked as 32×32 and 180×180. No image-generation service or additional dependency was used. The task-owned Chrome profiles were under `/tmp/` and their processes were terminated after capture. Final `lsof -nP -iTCP:8005 -sTCP:LISTEN` returned no listener, and a process inspection found zero task-owned icon Chrome processes.

Commands used, after writing `/tmp/gut-32.html` and `/tmp/gut-180.html` with the respective supplied inline SVG and exact dimensions:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --no-first-run --user-data-dir=/tmp/gut-icons-chrome --window-size=180,180 --screenshot=/Users/mo/AutonomousDayTrader-gut/frontend/app/apple-icon.png file:///tmp/gut-180.html
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --no-first-run --disable-background-networking --user-data-dir=/tmp/gut-icons-chrome-32 --window-size=32,32 --screenshot=/Users/mo/AutonomousDayTrader-gut/frontend/app/icon.png file:///tmp/gut-32.html
```

The second command ran with a 15-second process timeout and process-group cleanup; the first task-owned Chrome parent was explicitly terminated after capture. The manifest uses `minimal-ui`, cobalt theme color and the retained ground token. **iOS keeps an old home-screen tile until its bookmark is removed and re-added.**

## Required testids kept or moved

| Testid(s) | Product location |
|---|---|
| `status-strip`, `right-now-sentence` | moved from RightNowCard to `gut/StatusCard.tsx` |
| `attention-pill`, `attention-list` | moved from RightNowCard to StatusCard, count/list from collectAttention |
| `overnight-unsold-banner` | moved from page banner to StatusCard; no action |
| `overnight-holds`, `overnight-hold-<SYM>` | moved from OvernightHolds to `gut/Holdings.tsx` |
| `btn-no-buy-tonight`, `overnight-no-buy-reason`, `overnight-no-buy-reply` | extracted into `gut/NoBuyTonightButton.tsx`, mounted once in Controls |
| `btn-flatten-all` | moved from SafetyCard to `gut/ControlsCard.tsx` |
| `risk-telemetry` | moved from SafetyCard to `gut/ProDetails.tsx` |
| `holding-row-<SYM>`, `btn-sell-now-<SYM>` | retained in HoldingNow's compact day row |
| `active-swing-positions` | retained in ActiveSwingPositionsTable, now inside unified Holdings |
| `pro-words-toggle` | moved from Header button to Pro details summary |
| `strategy-table` | retained in StrategyTable, inside PlaybookPanel |
| `strategy-row-<strategy.id>`, `strategy-row-overnight` | retained in StrategyCard and OvernightPlaybookRow |
| `strategy-row-toggle`, `strategy-name`, `strategy-window`, `strategy-decisions`, `strategy-pnl`, `strategy-details` | retained on the existing day and overnight playbook elements |
| `strategy-status` | retained in StrategyCard's conditional live-status section |

Other existing reachable IDs retained: `overnight-strategy`, `overnight-hold-line`, `overnight-tonight`, `overnight-tonight-<SYM>`, `overnight-control`, `overnight-no-buy-on`, `overnight-summary`, `btn-break-even-<SYM>`, `active-swing-row-<SYM>`, `btn-exit-open-<SYM>`, `btn-tighten-stop-<SYM>`, `btn-emergency-exit-<SYM>`, `input-raise-stop-<SYM>`, `btn-confirm-raise-<SYM>`, `window-badge`, `night-part`, `handoff-band`, `x6-note`, `orb-alert`, `orb-init-error`, `orb-resolve-orphan`, `tri-engine-broker-issue`, `trend-details`, `tri-engine-details`, `orb-details`, `or15-details`, `overnight-steps`, `overnight-step-<key>`, `overnight-holds-link` (now targets Controls), `overnight-steps-none`, `overnight-stocks`, `overnight-sale-tick`, `overnight-night-bar`, `playbook-split`, `night-legend`, `handoff-note`, `axis-labels`, `axis-4pm`, `axis-night`, `axis-night-icon`, `account-label`, `history-explorer` and the existing History descendants, `swing-candidate-watchlist`, `swing-telemetry`, `swing-schedule`.

New IDs: `freshness-pill`, `first-frame-skeleton`, `gut-holdings`, `broker-mismatch`, `lock-countdown`, `results-bars`, `result-day-<date>`, `open-full-history`, `back-to-today`, `playbook-panel`, `strategy-week-result`, `pro-details`, `intro`.

The removed Today components remain as files. Their old navigation/performance IDs are not claimed to be rendered on Today. The new page preserves every testid explicitly required by section 4 and R7.

## Files changed or added

The eight retired scripts below were moved from `scripts/` to `scripts/retired/`, each with the requested one-line retirement header. `scripts/verify_ui_redesign.py` is untouched and is not imported or run by the new gate. The full changed-file inventory follows (old paths are deletions for moves; no git staging/commit was done):

- `docs/bright_dashboard/palette_map.md`
- `docs/gut_dashboard/BUILD_REPORT.md`
- `docs/gut_dashboard/fixtures/09-52.json`
- `docs/gut_dashboard/fixtures/15-35.json`
- `docs/gut_dashboard/fixtures/broker-mismatch.json`
- `docs/gut_dashboard/fixtures/evening.json`
- `docs/gut_dashboard/fixtures/no-frame.json`
- `docs/gut_dashboard/fixtures/sunday-open.json`
- `docs/gut_dashboard/screenshots/09-52-1280.png`
- `docs/gut_dashboard/screenshots/09-52-390.png`
- `docs/gut_dashboard/screenshots/15-35-1280.png`
- `docs/gut_dashboard/screenshots/15-35-390.png`
- `docs/gut_dashboard/screenshots/broker-mismatch-1280.png`
- `docs/gut_dashboard/screenshots/broker-mismatch-390.png`
- `docs/gut_dashboard/screenshots/evening-1280.png`
- `docs/gut_dashboard/screenshots/evening-390.png`
- `docs/gut_dashboard/screenshots/no-frame-1280.png`
- `docs/gut_dashboard/screenshots/no-frame-390.png`
- `docs/gut_dashboard/screenshots/sunday-open-1280.png`
- `docs/gut_dashboard/screenshots/sunday-open-390.png`
- `frontend/app/apple-icon.png`
- `frontend/app/globals.css`
- `frontend/app/icon.png`
- `frontend/app/layout.tsx`
- `frontend/app/manifest.ts`
- `frontend/app/page.tsx`
- `frontend/components/ActiveSwingPositionsTable.tsx`
- `frontend/components/HoldingNow.tsx`
- `frontend/components/OvernightHolds.tsx`
- `frontend/components/OvernightPlaybookRow.tsx`
- `frontend/components/StrategyCard.tsx`
- `frontend/components/StrategyTable.tsx`
- `frontend/components/gut/BrandMark.tsx`
- `frontend/components/gut/ControlsCard.tsx`
- `frontend/components/gut/Holdings.tsx`
- `frontend/components/gut/Intro.tsx`
- `frontend/components/gut/MoneyTiles.tsx`
- `frontend/components/gut/NoBuyTonightButton.tsx`
- `frontend/components/gut/PlaybookIcon.tsx`
- `frontend/components/gut/PlaybookPanel.tsx`
- `frontend/components/gut/ProDetails.tsx`
- `frontend/components/gut/ResultsBars.tsx`
- `frontend/components/gut/StatusCard.tsx`
- `frontend/components/gut/TopBar.tsx`
- `frontend/hooks/useHealthLimits.ts`
- `frontend/hooks/useTodayLedger.ts`
- `frontend/lib/gut.ts`
- `frontend/lib/plain.ts`
- `frontend/package.json`
- `frontend/scripts/verify_attention.mjs`
- `frontend/scripts/verify_gut.mjs`
- `frontend/scripts/verify_ui.mjs`
- `frontend/tsconfig.json`
- `frontend/types/trading.ts`
- `scripts/retired/verify_bright_dashboard.py`
- `scripts/retired/verify_cobalt_ledger.py`
- `scripts/retired/verify_compact_dashboard.py`
- `scripts/retired/verify_holding_mood.py`
- `scripts/retired/verify_overnight_holds.py`
- `scripts/retired/verify_overnight_row.py`
- `scripts/retired/verify_visual_qa.py`
- `scripts/retired/verify_visual_qa_live.py`
- `scripts/verify_bright_dashboard.py`
- `scripts/verify_cobalt_ledger.py`
- `scripts/verify_compact_dashboard.py`
- `scripts/verify_gut_dashboard.py`
- `scripts/verify_holding_mood.py`
- `scripts/verify_overnight_holds.py`
- `scripts/verify_overnight_row.py`
- `scripts/verify_visual_qa.py`
- `scripts/verify_visual_qa_live.py`

Pre-existing untracked input files were left untouched: `PLAN_2026_10_07_gut_job_dashboard.md`, the six `docs/gut_dashboard/*.dc.html` boards, and the three SVGs under `docs/gut_dashboard/icons/`. They appear in git status because they were already untracked when this task began; they are not new implementation changes.
