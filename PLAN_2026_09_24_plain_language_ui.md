# Plan: plain-language dashboard redesign (2026-09-24)

## Goal
Replace the dark, jargon-heavy dashboard with the approved light "muted palette" design so a person who knows nothing about stocks can read it. Approved mockups (static, hardcoded numbers, reference ONLY for look and copy): `docs/ui_redesign_2026_09_24/Main.dc.html` (desktop 1440), `Mobile.dc.html` (phone 390, quick trades), `MobileSlow.dc.html` (phone, slow trades tab). Canvas: https://claude.ai/artifact/WsrJnzoVVnyLRckDDLLobz

Every number on the page must come from live data. Nothing from the mockup is hardcoded except copy and colors.

## Non-goals
- No change to trading logic, risk, orders, persistence, or WebSocket action payloads.
- No new dependencies beyond `next/font/google` (already part of Next).

## Constraints that must hold
1. All operator actions keep working with the SAME payloads: `flattenPosition`, `flattenAll`, `tightenStop`, `swingExitNextOpen`, `swingExitImmediate`, `swingTightenStop` from `hooks/useTradingStream.ts`. Do not edit the hook's action code.
2. Destructive buttons ("Stop everything and sell now", "Sell now") use an inline two-step confirm (button changes to "Tap again to confirm" for 4 s). Never `window.confirm`/`alert`.
3. Idle is never red/terracotta. Losses are terracotta, idle/resting is neutral grey.
4. No horizontal scroll at 390 px. Touch targets >= 44 px. Text contrast >= 4.5:1.
5. `prefers-reduced-motion` disables all animations.
6. Keep existing `data-testid`s on the equivalent new elements: `risk-telemetry` (now the Safety card), `segmented-mode-toggle`, `mode-tab-intraday`, `mode-tab-swing`, `strategy-window`, `strategy-decisions`, `window-badge`, `swing-telemetry`, `swing-candidate-watchlist`, `swing-schedule`, `active-swing-positions`.
7. Static export (`output: "export"`) must still build; FastAPI serves `frontend/out`.

## Data sources (source of truth per element)
| UI element | Source |
|---|---|
| Balance, "Down $X today" | `state.account.equity`, `daily_pnl`, `daily_pnl_pct` |
| Balance chart | `/api/trades` today's items (session_date == today ET) sorted by `closed_at`: step line starting at `summary.opening_equity`, each step adds `realized_pnl`; final point = live `account.equity`. X axis 9:30 to 16:00 ET. Empty day = flat line + "No trades yet today". |
| Trades today / won / lost | `/api/trades` `summary.trades_count/wins/losses` |
| Per-strategy "today" P&L and trade count | Sum of today's ledger items grouped by `strategy_id` (NOT `strategy.daily_pnl`/`trades_count`; those drifted from the ledger on 2026-09-24: ORB showed 0 trades while the ledger had an ORB trade of -$112.04). |
| Status chip | `strategy.window.state`: CAN_TRADE/LIMITED → "Watching now" (sage, breathing dot); WAITING → `window.headline` (lavender); DONE_FOR_DAY → "Done for today" (grey, card desaturated); BLOCKED → "Paused right now" + first blocker; PAUSED → "Paused"; MARKET_CLOSED → "Market closed". |
| Hours bar | NEW backend field `window.ranges: [["09:30","11:30"],["14:00","15:45"]]` (24h ET strings) added in `backend/app/core/trading_windows.py` from the existing `ranges`. Frontend maps to % of 9:30–16:00. "Now" marker from current ET time, hidden outside 9:30–16:00. |
| Card note line | `decisions`: e.g. "Saw 7 chances, skipped all. Market is rising, so buys only." built from `signals_today`, `orders_today`, `top_block_text`, plus `window.market_text`/`notes`. |
| "Right now" sentence | Derived: circuit broken → "Stopped for today. It hit the $1,500 loss limit."; market closed/not open → "Market is closed. It starts again at 9:30 AM."; else count of CAN_TRADE/LIMITED strategies + earliest WAITING headline; if `positions_count > 0` prefix "Holding N trade(s) right now." |
| "Sells everything in" | Countdown to 15:55 ET, market days only; otherwise "Market closed". |
| Recent trades list | Last 6 ledger items: company name (small map AAPL→Apple, NVDA→Nvidia, PLTR→Palantir, TSLA→Tesla, META→Meta, AMD→AMD, MSFT→Microsoft, AMZN→Amazon, GOOGL→Alphabet, MU→Micron, LRCX→Lam Research, KLAC→KLA, GS→Goldman Sachs; fallback = ticker), time ET, LONG→"bet it goes up", SHORT→"bet it goes down", strategy tag in its color, result pill. "See all" expands the full history (existing TradeHistory content, restyled). |
| Safety card | `account.daily_drawdown` of 1,500; bar width = drawdown/1500. Rules text as in mockup. Button → `flattenAll` with two-step confirm. |
| Open position(s) | `state.all_positions`: a "Holding now" card per position above the strategies: company, bet direction, entry price, live P&L, safety-exit price (`stop_loss`), buttons "Sell now" (`flattenPosition`, confirm) and "Move safety exit to break-even" (`tightenStop(symbol, entry_price)`). Hidden when none. Replaces `ActivePositionTray` bottom sheet. |
| Slow trades | `state.swing`: spots from `max_slots/active_slots_used/slot_notional`; candidate checks: `sma_200_pass` "Long-term uptrend", `rs_pass` "Beating the market" (detail from `rs_stock_60d` vs `rs_qqq_60d`), `rsi_pass` "Had a sudden drop", `!earnings_blackout` "No earnings report soon" (detail `next_earnings_date`). Held swing positions: "Day N of 5", safety exit, buttons "Sell at next open" / "Sell now" (confirm) / "Raise safety exit" (existing prompt flow restyled). Schedule text from `swing.schedule_text` keeps `data-testid="swing-schedule"`. |
| Connection | Offline banner in plain words: "Lost connection to the robot. Showing the last numbers it sent. Reconnecting..." (no port numbers). |

## Friendly names and colors (by strategy id)
| id | Plain name | Tint band | Ink | Bar |
|---|---|---|---|---|
| orb | Morning Breakout | #F4E0CF | #7A3E1D | #D98B5F |
| vwap_pullback | Ride the Trend | #DCE8DE | #2F5A45 | #6E9C82 |
| news_momentum | Big News | #F1DDDF | #7A3343 | #C47A88 |
| mean_reversion | Snap Back | #E0E1F0 | #3E4478 | #8189C4 |
Unknown id: neutral grey, name = `strategy.name`. One-sentence "what it does" copy exactly as in the mockup.

Page tokens: ground #F7F3EC, ink #1D1A33, muted #5D5A73, line #EFE4D2, dark card #2E3244, gain #3F7D5C (pill #E4EFE7), loss #A6522F (pill #F6E3DA), primary button #2E3244. Fonts: Fraunces (display) + Instrument Sans (body) via `next/font/google`.

"Show pro words" toggle (header, persisted in localStorage with try/catch) reveals: the technical name (`strategy.name`), win rate, Sharpe, raw blocker codes, VIX regime, feed health dots, and the old Execution Audit Log (collapsed section at the bottom). Off by default.

## File plan
- `backend/app/core/trading_windows.py`: add `"ranges"` to the returned dict. Test in `backend/tests` asserting VWAP returns two ranges.
- `frontend/types/trading.ts`: add `ranges?: [string, string][]` to `StrategyWindow`.
- `frontend/tailwind.config.js`, `app/globals.css`, `app/layout.tsx`: light theme tokens, fonts, keyframes (rise, draw, grow, breathe, drift, bob), reduced-motion block, themeColor #F7F3EC, title "Day Trader".
- New `frontend/lib/plain.ts`: pure helpers (friendly names/colors, company map, window→chip, ranges→segments, ET now, countdown, right-now sentence, ledger grouping). Unit-testable, no React.
- New `frontend/hooks/useTodayLedger.ts`: fetch `/api/trades` for today (reuse TradeHistory's `apiBase()`), refetch when `state.ledger_revision` changes.
- Rewrite `app/page.tsx` and components: `Header`, `BalanceCard` (new), `RightNowCard` (new), `SegmentedModeToggle` (labels "Quick trades (same day)" / "Slow trades (a few days)"), `StrategyCard`, `StrategyCarousel` (becomes a 4-col grid desktop / stacked mobile), `HoldingNow` (new, replaces tray), `RecentTrades` (new), `SafetyCard` (new), `TradeHistory` (restyled, behind "See all"), swing components restyled with plain copy, `ExecutionLog` (pro-only). Delete `AmbientBackground`, `ActivePositionTray`, `LiveChart`, `ManualControls` only if nothing imports them after the rewrite.
- Update tests that assert the old design: `frontend/scripts/verify_ui.mjs`, `tests/e2e/test_challenger_mobile.py`, `scripts/verify_visual_qa.py`, `scripts/verify_visual_qa_live.py`. Replace source-grep checks of old tokens/labels with checks of the new contract (testids present, action handlers wired, reduced-motion block present). Keep the no-horizontal-overflow checks.

## Verification
1. `cd frontend && npm run build` clean (0 TS errors).
2. Backend pytest full suite green; the new ranges test passes.
3. `node frontend/scripts/verify_ui.mjs` green.
4. Local run per ERRORS.md: backend on 8005 with `ENV=development PERSISTENCE_ENABLED=false START_RELAY_CLIENTS=false`, headless screenshots at 390x844 and 1440x900, both tabs, pro words on and off; plus a "busy" state (inject a fake position/trades via test fixture or dev endpoint if one exists) so the Holding-now card and trades list are seen. Kill the server after.
5. After deploy: live URL screenshots at both widths, `/health` 200, Railway logs grep `error on bar` = 0.

## Rollout
Commit as Jhosshua, push `main` (Railway redeploys on push; restart mid-session is safe per MEMORY). Update MEMORY.md, ERRORS.md, README/PROJECT if they describe the UI.

---

## Revision 1 after Codex attack (29 findings, all accepted; this section OVERRIDES anything above it)

### Backend fixes now IN scope (real bugs found by the attack; each needs a test that fails before the fix)
- B1 (P0) `/api/flatten` FLATTEN_ALL and the WS FLATTEN_ALL path: close INTRADAY positions only, skip swing holdings, and report per-symbol outcome truthfully (`flattened` only when the order was accepted; `skipped`/`rejected` lists otherwise). No halt is added. UI copy: "Close all quick trades now" (never "stop everything").
- B2 Swing SET stop (both transports): reject unless `current_stop < proposed < market_price` and finite. Return a clear reason.
- B3 Swing "exit at next open": a staged EXIT must survive the 09:45 purge and execute at the NEXT session's open. Only stale staged ENTRIES are purged. Test: stage exit at 11:00, advance past 11:01 and 16:00, it is still staged and fills at next 09:30 window.
- B4 `/api/swing/action` REST path must checkpoint exactly like the WS path (share one implementation). Test that a REST action is in the next saved checkpoint.
- B5 `trading_windows.strategy_window` adds `ranges` (list of ["HH:MM","HH:MM"] ET) and `trading_day` (bool). On non-trading days ranges still describe the schedule but `trading_day=false`. Tests: weekday VWAP two ranges, Saturday trading_day false, a holiday false.
- B6 REST `/api/account` and the WS account payload must agree on `daily_pnl`, `daily_pnl_pct` (percent points) and `daily_drawdown`. Today they differ (REST daily_drawdown_dollars 0.0 while /health shows 118.67). Fix the hook's REST fallback so displayed values are identical in both modes.

### Frontend contract changes
- F1 Holdings split by arm: quick-trade holdings = `all_positions` whose symbol is NOT in `swing.positions`. Swing holdings shown only on the Slow tab. `stop_loss` is nullable in types; show "No safety exit set" when null. Shorts: button "Close trade", copy "bet it goes down".
- F2 "Move safety exit to my buy price" enabled only when it is an improvement and cannot trigger an immediate exit: long needs `stop < entry < market`; short needs `stop > entry > market`. Tooltip/copy says "before fees". After sending, wait for the stop in state to equal the target; else show "Didn't go through".
- F3 Action feedback: each action button has states idle → confirm (4 s, scoped to that symbol, cleared if the position disappears) → sending (disabled, no duplicates) → done (verified from state: position gone / stop changed / exit staged) or "Didn't go through, try again" after 10 s. Do not change action payloads.
- F4 Today ledger: `/api/trades?range=today` and drain `next_cursor` until null (limit=100 per page). Dedupe by trade_id. "Today" = `closed_at` in America/New_York today. Generation counter discards stale responses. Refetch on revision change, ET date change, reconnect, and on failure with backoff. States: loading, error ("Couldn't load today's trades"), empty ("No finished trades yet today"). Recovered aggregate sessions: add their strategy aggregates to per-strategy totals once and show a small "Some older details unavailable" note; never draw chart steps for them.
- F5 Per-strategy numbers (P&L, trade count, win rate in pro mode) all from ledger rows grouped by `strategy_id`; `realized_pnl` already includes fees. Drop Sharpe.
- F6 Chart is NOT a balance history. Title "Finished trades today": cumulative realized P&L of today's finished trades (step line from $0). Balance number stays separate (live equity). Label lists as "Finished trades".
- F7 First-load skeleton: never render the synthetic zero account/defaults before the first real snapshot. Connection states: live / reconnecting (plain banner) / stale (no update > 30 s: "Numbers may be old").
- F8 Always visible (not pro-only), in plain words: data feed problems (`ingestion` not connected: "Price feed is down, it can't trade right now"), saving problems (`persistence.status != durable`), swing `last_close_entries_withheld` note, circuit breaker.
- F9 Hours bar uses `window.ranges`; hide the "now" marker when `trading_day` is false or outside 9:30–16:00. Early-close days: show `window.notes`.
- F10 Copy fixes: "Quick trades start closing at 3:55 PM. Slow trades can stay open for days." Countdown label "Quick trades close in". Daily limit number from `/health` `limits.max_daily_loss_dollars` (fallback hide the number). Replace "$500 per trade" with "Keeps each bet small (about 1% of the account at risk)" using `limits.base_trade_risk_pct`. Bar width clamped to 100% but text shows the true amount.
- F11 Earnings check has three states: pass, fail, unknown ("No date on file"), never pass on missing data. Show data date (`candidate.date`). Unloaded candidates: skeleton, no fake numbers. Add `date` to the type.
- F12 Swing spots: held (positions), waiting to buy at open (candidates with `is_staged`), free = max_slots − held − staged (min 0).
- F13 Colors: gain text #2F6B4C on #E4EFE7, loss text #8F4424 on #F6E3DA (both ≥ 4.5:1). Verify every text/background pair used.
- F14 Motion: framer `useReducedMotion()` disables all framer animations; CSS block disables keyframes.
- F15 Fonts: `@fontsource/fraunces` and `@fontsource/instrument-sans` (npm, works offline in Docker `npm ci`), NOT next/font/google.
- F16 Remove `maximumScale`/`userScalable:false` from the viewport.

### Verification (replaces the section above)
- V1 Backend pytest full suite green incl. new tests for B1–B6.
- V2 `npm run build` clean; `docker build .` succeeds locally if docker is available (else note it and rely on Railway build).
- V3 New Playwright visual/behavior suite `scripts/verify_ui_redesign.py` using route mocks built from real captured API responses (scratch fixtures) with variants: idle (market closed, empty ledger), busy (2 intraday positions incl. a short + 1 swing holding + 13 trades), >100 trades (pagination), recovered session, reconnecting, circuit breaker, feed down. Assertions FAIL the run: no horizontal overflow at 390 and 1440, no element with text clipped, controls ≥ 44 px, each of the 6 actions sends the exact payload, confirm expires, no duplicate send, reduced-motion emulation leaves no running animations. Screenshots saved for review.
- V4 Update old suites (`verify_ui.mjs`, `test_challenger_mobile.py`, `verify_visual_qa*.py`) to the new UI so they pass with real assertions, or delete checks that only grep source for old tokens.
- V5 Release gate: record `/api/positions`, `/api/swing/state`, `/health` before deploy; after deploy `/health` status `healthy`, persistence `durable`, `restored_at` updated, positions/stops/staged exits equal to before, ledger count unchanged, Railway logs no `error on bar`/Traceback.
