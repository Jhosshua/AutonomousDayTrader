# Overnight holds plan for AutonomousDayTrader (NVDA, IREN, HUT)

Status. v3, 2026-09-30. Plan only. Nothing is built. v1 was attacked the same day by three independent read only reviewers (research fidelity, engineering, operator and risk), 51 findings, all accepted, one in a changed form (O10). v2 was re-checked by a fourth reviewer, 15 findings, all accepted into v3 (verdict "fix then build", must fix R2-1 to R2-5 and R2-11, all done). Section 13 lists every finding and its answer. The raw round 1 reviews are in `docs/overnight_holds/attack_round1_v1.md`, round 2 in `docs/overnight_holds/attack_round2_v2.md`.

Source of the rules. `research/edge_hunt_2026_09_29/` (`PROTOCOL.md`, `lib.py`, `families.py`, `data/holdout.json`, `data/trades_{NVDA,IREN,HUT}_overnight_0.npy`), config `overnight {"cond": "none", "exit": "open"}`, committed in `cd67aec`. ManualTrading3 has a plan for the same three holds (`~/ManualTrading3/docs/overnight_holds_plan.md` v2, decided, not built). ADT differs from MT3 in one big way. ADT sends real orders to the Alpaca paper account PA3CSVDZMMPY, while MT3 books virtual fills.

## In plain words

The robot gets three new strategies, NVDA overnight, IREN overnight and HUT overnight. On every full trading day each one buys its stock at the 4:00 PM closing price and sells it at the 9:30 AM opening price of the next trading day. Over a weekend or a holiday it holds until the next open. It has no stop. It trades at full size from the first 4:00 PM close after it is deployed, with no shadow and no trial, by your standing ruling.

How the orders work. At 3:46 PM the robot asks Alpaca to buy at the closing price. At 7:00 PM it asks Alpaca to sell at the next opening price. Both requests wait at Alpaca, so a robot restart overnight or at 9:30 AM does not stop the sale. This is practice money, and Alpaca simulates these fills.

What the test showed, at $10,000 in each stock (close to the chosen 20% of today's $49,700 account, about $9,940). Over three years it made about $27,000 a year on paper. It lost money on 44% of nights and in 8 of 36 months. The worst night lost $6,050, when all three fell together over the weekend of Aug 2, 2024. About 1 night in 100 lost $1,670 or more. The worst stretch from a high lost $13,449, from Jan to Apr 2025, and took until late Jul 2025 to recover. About once every 7 weeks a single night lost more than the robot's whole daily loss limit ($1,243 today). Weekend and holiday nights are riskier. Their 1 in 100 loss is about twice a weeknight's.

What it cannot promise. The rule is not proven. Each stock passed the test on data it had never seen, but the strictest check across all ideas tried could not rule out luck. About 5 of 23 ideas passed where 1 would pass by luck. Most of the gain came from these stocks rising far more than the market (NVDA 5.6 times, IREN 10 times, HUT 7 times, QQQ 2.1 times). This year is weaker, IREN won only 46% of nights in 2026. HUT and IREN are both bitcoin miners and move together, so they are close to one bet. The daily loss limit cannot stop an overnight loss, because it happens while the market is shut. From 3:45 PM until the morning sale, no day strategy can trade these three stocks.

## 1. The rule, frozen

| # | Rule | Exactly as found |
|---|---|---|
| F1 | Stocks | NVDA, IREN, HUT. Three separate strategies. Long only. |
| F2 | When it buys | Every regular session that closes at 4:00 PM. No market, news or earnings filter. Fridays, nights before holidays and earnings nights included. |
| F2a | Buy day data | At least 312 of 390 one minute bars (80%), research `lib.py:89`. Judged at 15:46:05 (X2). |
| F2b | Buy day open | The buy day has a 09:30 bar (`families.py:93-94`). |
| F2c | Buy day close | A bar between 15:55 and 15:59 exists (`lib.py:98`). Cannot be known when the order goes in (X2). |
| F3 | Skipped days | Days that close at 1:00 PM have no buy (`families.py:94`, `tp.end == 390`). Next ones 2026-11-27, 2026-12-24, 2027-11-26. The sale on those mornings is normal. |
| F4 | Buy price | Research used the close of the last bar from 15:55 to 15:59. Live uses Alpaca's closing auction fill (X3). |
| F5 | Sale price | Research used the open of the next session's 09:30 bar (`families.py:108`). Live uses Alpaca's opening auction fill (X3). |
| F6 | Cost | Research charged `max(6 bps, 1c/price + 2 bps)`. Live pays what Alpaca fills (X9). |
| F7 | Exits | Only the next open. No stop, no target, no time exit, no news exit. |
| F8 | Size | 20% of the account per stock (D2), from Alpaca `equity` read at 15:46. `shares = floor(0.20 x equity / last price)`. The research return is per trade, so sizing does not change the rule. Whole shares leave returns unchanged. |
| F9 | Splits and dividends | Research measured on split adjusted prices and ignored dividends. Live books real fills and adjusts share counts on a split (section 4.6). |
| F10 | Sale day | The next session in the exchange calendar. Research took the next row of the SPY calendar, which already skips holidays and the unscheduled closure of 2025-01-09. |

Declared differences from the research. Every skipped or changed night is logged with its reason and the return the rule would have made.

| # | Difference | Why | Effect in the 3 year test |
|---|---|---|---|
| X1 | Research also needed the sale day to have 80% bars (`ok[d+1]`, `families.py:98`). | That looks ahead. A live robot cannot know it at the close. | 3 IREN nights would have traded (2023-10-06, 10-17, 11-07), so IREN has 739 nights, not 736. |
| X2 | F2a and F2c are judged at 15:46:05, because an auction order must be in by 15:50. F2a becomes "regular session bars starting 09:30 to 15:44, plus 15, reach 312", counted from SIP REST bars (not the live feed). F2c is assumed. | Alpaca refuses closing auction orders after 15:50. | 0 of 742 nights differ in the test window. 1 of 810 sessions differs overall (IREN 2023-09-27, before the window). F2c never failed. The last bar was 15:59 every session. |
| X3 | Buy and sale fill at Alpaca's auction prices, not the research bar prices. | This is the real world equivalent of the rule. | Measured before the build by T1b. Until then, not known. |
| X4 | Your "no buy tonight" control cancels tonight's buy if used before 15:49:30 (D4). | Your control. | Logged. |
| X5 | No buy if the robot cannot send by 15:49:30. Reasons include robot down, Alpaca refusal, the bot and Alpaca disagreeing on positions, a failed save, the relay not answering the bar count, the stock held by ORB, or yesterday's hold in the stock still unsold. | No safe order can go in. | Logged. Alert if no order is accepted by 15:47:30. |
| X6 | A day trade in the same stock is closed at 15:46 instead of 15:55 before the buy goes in (D7). | Alpaca refuses a sell while a buy is open on the same stock ("potential wash trade", seen on NVDA in MT3 on 2026-08-31). | Affects the day trade only. Rare. Logged. |
| X7 | If the opening sale has not filled by 09:31:00, it is cancelled and the rest is sold with a normal market order. | The robot must still sell. Research dropped such nights. | Logged with the real price and time. |
| X8 | Alpaca paper simulates the auctions. | This account is practice money. | Unknown. The fidelity log shows it (section 4.9). |
| X9 | Paper has no trading cost, while research deducted 6 to 12 bps a night. | Paper account. | Live results look better than research by that much even at the same prices. |
| X10 | Research also dropped a night whose sale day had no 09:30 bar (`families.py:108-111`). | That looks ahead too. | 0 nights for all three. |
| X11 | A buy can shrink or be skipped for lack of room (D3 or the $25,000 per position cap), or partly fill in the auction. | Account limits. Research always filled in full. | Logged with the size the rule wanted. |
| X12 | Any corporate action other than a plain split stops the robot from judging the hold alone. It raises needs look and still sells. | Mergers and symbol changes cannot be automated safely. | None for these three in the window. |

Research record (normal cost, from `holdout.json` and the trade files, recomputed 2026-09-30).

| Strategy | Nights | Won | Average a night | Profit factor | Worst night | Unseen data p | 2026 so far (won, average, PF) |
|---|---|---|---|---|---|---|---|
| NVDA overnight | 742 | 56.9% | +0.19% | 1.36 | -14.2% | .019 | 55.4%, +0.14%, 1.31 |
| IREN overnight | 736 | 52.4% | +0.44% | 1.40 | -27.3% | .009 | 46.2%, +0.13%, 1.11 |
| HUT overnight | 742 | 52.8% | +0.45% | 1.42 | -19.0% | .031 | 51.1%, +0.38%, 1.34 |

All three worst nights are the same one, bought Fri 2024-08-02 and sold Mon 08-05. Profit factor means dollars won divided by dollars lost.

## 2. Hard rules

- R1 Fidelity first. T1 (decision parity) passes on the committed fixture, and T1b (auction prices) is measured, before any wiring is built.
- R2 The sale always runs. No control, loss stop, off switch, flatten button, deploy or mismatch may block or skip a sale.
- R3 No shadow, no trial, no size ramp. Full amount from the first 4:00 PM close after the release is live.
- R4 Own lifecycle. The overnight controller places and books its own orders. The generic engine never sends, cancels, re-fires or flattens them, and no overnight order sits in the engine's working orders.
- R5 Other strategies unchanged, except the named shared code decisions and X6.
- R6 Paper only. The broker host stays the hardcoded paper URL.
- R7 Page words read live state and settings, never fixed strings.

## 3. How a day runs

| Time (ET) | What happens |
|---|---|
| 15:30 to 15:45 | Nothing new. Your "no buy tonight" control can be used any time until 15:49:30. |
| 15:45:00 | Day entries already stop for every stock (existing lockout). From now until the sale is booked, no other strategy may open or add to NVDA, IREN or HUT. An open day trade keeps its own stop and exits until the controller closes it (S1). |
| 15:46:05 | For each switched on stock, check the calendar with Alpaca's read only calendar (regular 16:00 close, dates agree with ours), the data rule (X2, relay REST bars, retried until 15:49:30), control not used, mode live, broker positions agree, last save durable, not held by ORB, no earlier hold in the stock still unsold. Cancel any other working entry in the stock. If a day trade holds the stock, close it through the manual flatten steps (bracket cancelled, trade recorded through `_reconcile_fills`, `main.py:3819-3854`), sent on the worker pool and tagged as the controller's, then wait for Alpaca to show 0 shares and no open order (X6). Size the buy (D2, D3, section 4.5). Save the intent durably, then send a closing auction buy, client id `adt-ovn-<SYM>-<YYYYMMDD>-buy-<n>`. |
| 15:46 to 15:49:30 | Retry every 5 s. An unanswered send is looked up by client id before any new attempt. The control cancels an accepted order. |
| 15:47:30 | Needs look alert for any switched on stock with no accepted order and no logged skip. |
| 15:49:30 | Give up any buy not accepted, log the reason, and release that stock back to the day flatten, so a day trade still open is closed at 15:55 as today. After 15:50 Alpaca takes no changes. |
| 15:55 to 15:58 | The day flatten runs and never touches the holds or their orders. |
| 16:00:05 onward | Read each buy order every 5 s until final. Book the filled shares at Alpaca's average price. Book before every position compare. An auction with no fill is a logged skip. |
| 16:15 | Fetch SIP bars for the fidelity log (section 4.9). |
| 19:00:30 | For each hold, send the opening auction sell for exactly the booked shares, client id `adt-ovn-<SYM>-<buy date>-sell-<n>`, for the next session from Alpaca's calendar. Retry every 30 s until accepted. Alpaca queues it for the next opening auction. |
| Overnight | Holds show at their buy price. After hours prices change neither equity nor the loss stop. |
| 09:00:00 | Check each hold. Alpaca's share count equals the ledger, and the queued sale is live and for that count. If not (split, missing sale, robot was down at 19:00), fix it (section 4.6) and send or replace the sale before 09:27:30. |
| 09:30 onward | Read each sale every 5 s. Book the result. Release the stock only when Alpaca shows 0 shares and no open overnight order. |
| 09:31:00 | Any sale not filled, cancel it and confirm, then sell the rest at market (X7). One live attempt at a time. Before attempt n+1, attempt n is looked up and must be final. Size is min(ledger shares left, Alpaca shares minus shares held for open orders). Never blocked. A red banner shows while any hold is unsold. |

Saves. Only a buy needs a durable save before it is sent. A sale goes out even when a save fails, because its fixed client id lets a restart find it (same rule as exits today, `main.py:516-519`, `808-815`).

A restart at any time first runs the session boundary check for today (`_check_session_boundary`), then the overnight reconcile, before the first position compare. So a sale that filled while the robot was down is booked into the right day (R2-2). It asks Alpaca about every client id before sending anything. A buy accepted before 15:50 fills at 16:00 even if the robot is down. A sale queued after 19:00 fills at 09:30 even if the robot is down. Only an outage covering both 19:00 to 09:27:30 and 09:31 onward leaves a hold unsold, and the banner then says so.

## 4. Architecture

### 4.1 Code

- New pure module `backend/app/core/overnight_schedule.py`. Gates, sale date and skip reasons. It takes a calendar object and a bar source that only returns what was visible at the decision time. No I/O. T1 and T2 test it.
- New `backend/app/core/overnight_execution.py`. Own order records, deterministic client ids with attempt numbers, intent saved durably before every send (tri's `checkpoint()` guard, not `_checkpoint_runtime`, which returns True while an input is in flight, `main.py:777-778`), broker calls on its own worker pool, never on the event loop.
- Booking follows the ORB pattern, idempotent per Alpaca order id (`orb_integration.py:747-790`), into the same ledger call. If that call needs an engine `Order`, it is created, filled and closed in the booking step, so nothing overnight ever rests in `engine.working_orders`. To be confirmed in the build against the real call.
- If the booking step needs an engine `Order`, it carries its own `OVERNIGHT_POLICY` and no broker ids, and `OVERNIGHT_POLICY` joins the ORB skips at `engine.py:230` and `306`, so the 5 s settle loop can never cancel a queued sale (R2-11). T8 asserts it.
- `backend/app/core/broker.py` gains, all on the paper host, `submit_on_auction(symbol, side, qty, client_order_id, tif)` with `tif` in `{"cls", "opg"}` and type market, and read only `get_calendar(start, end)`, `get_account_fields()` (adds `buying_power`, `regt_buying_power`, `daytrading_buying_power`, `last_maintenance_margin`, `multiplier` to what `sync` reads, `broker.py:171-181`), `get_asset_margin(symbol)` and `get_corporate_actions(symbol, since, until)` within Alpaca's date range limit. The controller calls AlpacaBroker directly with its own time gate. It never calls `_broker_gate` (`main.py:452-462`), which refuses sends outside 09:30 to the close.
- Hooks. `_runtime_clock_step` next to `tri_controller.tick` (`main.py:2933`). Broker loop, after the Alpaca snapshot and before `_compare_with_broker`, the controller books its fills and positions are read again (`main.py:358-382`). Lifespan, reconcile between `_restore_checkpoint()` and the startup compare at `main.py:3129`, before `orb.start()`. `reset_runtime_state` (`main.py:3020`) resets the controller.
- Not a `Strategy` in `main.strategies`, so the saved strategy set check (`runtime_state.py:242-252`) is untouched. The card and page payload come from the controller.
- `OVERNIGHT_MODE` Railway variable, `live` or `off`, code default `live`. `off` stops new buys and never stops a sale. With no broker attached (tests, replays) the controller sends nothing.

### 4.2 Positions

- Positions stay `TradingArm.INTRADAY` with `strategy_id` in `OVERNIGHT_IDS = {"overnight_nvda", "overnight_iren", "overnight_hut"}`, tested through one helper `is_overnight(pos)`. Rejected, a new `TradingArm` value, because old code cannot decode it (`persistence.py:148-149`). Rejected, reusing `TradingArm.SWING`, because the swing engine would adopt the holds (`swing_panic_dip.py:253-260`).
- No bracket and no stop. The generic risk validator is not used for these orders. The controller does its own admission (section 3, 15:46 row).

### 4.3 Shared code decisions

| # | Where | Today | Change |
|---|---|---|---|
| S1 | `main.py:486` validator, before exit classification | tri guard | in a stock the controller reserves or holds (`OVERNIGHT_RESERVED`), refuse from anyone but the controller any entry, and any sell larger than the non overnight shares. A day trade's own stop and exits keep working until X6 begins. Once a hold exists, refuse every other order in that stock. Without this, a day short would count as an exit and sell the hold (`main.py:506-510`, `engine.py:266`). |
| S2 | `engine.py` `_broker_execute` (`228-231`) | refuses tri and ORB | the same rule as S1, at the broker choke point |
| S3 | `main.py:2683-2684` 15:55 flatten | skips swing | skip holds |
| S4 | `flattening.py:265-273`, `main.py:2718-2719` 15:58 audit and sweep | skip swing | skip holds |
| S5 | `main.py:2665-2666` cancel at 15:55, each 15:58 audit and 16:00 | intraday orders | no change needed (no overnight order is in working orders). Tested. |
| S6 | `main.py:1442-1490`, `1515` session boundary | cancels and liquidates non swing | skip holds |
| S7 | `main.py:1386-1406` loss stop | liquidates all but swing, tri, ORB, OR15 | never liquidates holds, never cancels their orders |
| S8 | `main.py:3783` manual flatten (cancel loop `3819-3823`, liquidation from `3842`), flatten all `3906-3914` | skip swing | skip holds (D9). The reply says holds are not included and when they sell. |
| S9 | `main.py:1953-1955` news contradiction | skips swing | skip holds |
| S10 | `main.py:3966-3972` tighten stop | refuses tri and ORB with plain text | refuse holds with plain text |
| S11 | `orb_integration.py:474-481` `adt_occupied` | sees swing, tri, OR15 | also sees reserved stocks and holds |
| S12 | `main.py:236-300`, `243-249` concurrency, `596-601` manual count, `risk.py:317-341` sector | intraday counts | exclude holds |
| S13 | broker loop `main.py:358-382` | snapshot, then compare | controller books, positions re-read, then compare. Share count differences on held stocks explained by a corporate action are shown as a split, not a mismatch. |
| S14 | loss stop baseline `risk.py:114`, `risk.py:192`, `main.py:2503`, `main.py:896`, `runtime_state.py:236` | risk baseline copied from the account baseline on restore | a saved `overnight_realized_today` offset inside the risk engine, keyed to its session date and zeroed by `_check_session_boundary`, used in both drawdown formulas and at `main.py:2503`, stored in the `overnight` checkpoint key and re-applied after restore. The account baseline (`main.py:1610`, `1208`) is untouched, so results still show the overnight result on the day it sells. The Safety card meter reads the offset adjusted risk drawdown (`main.py:3304`) instead of the account drawdown (`page.tsx:215`, `account.py:459`), and the overnight result shows on its own line. |
| S15 | price marks `engine.py:567` (quotes), `engine.py:626` (bars), `account.py:146-152`, projection `main.py:2496-2504` | every position marked live | holds are not re-marked between their buy and their sale |
| S16 | `stock_ws.py:41-43` subscriptions | NVDA only | bars for IREN and HUT from a separate list, not `WATCHLIST_SYMBOLS`, so no day strategy trades them. The data rule uses REST bars, so a late subscription cannot skip a night. |
| S17 | `main.py:1054-1055` `exit_due` | 15:55 | the sale date and time from the controller |
| S18 | `trading_windows.py:15-61` calendar | 2026 and 2027 | the controller refuses buys outside covered years with a needs look alert, and cross checks each date with Alpaca's calendar |
| S19 | `scripts/production_watchdog.py:149-153` | any open position after 15:58 is CRITICAL | holds excluded |
| S20 | `page.tsx:62-71` Quick and Slow split, `HoldingNow.tsx`, `SafetyCard.tsx`, `plain.ts` | anything not swing is Quick | section 5 |

S6 of v1 (EOD_FLAT) is dropped. Holds do not exist yet at 15:58, so no change is needed (E18). T8 greps every reader of positions and orders and asserts each one has a decision.

### 4.4 Controls

- "No buy tonight" control (D4). Cancels tonight's buys if used before 15:49:30. Never touches a sale. Its state and date are saved durably before the page is told it worked, and re-applied on restore.
- `OVERNIGHT_MODE=off` and the per stock variables need a restart, so they only change the next day (change them 16:10 to 08:50, D8). The page button is the only same day stop.
- An order cancelled by hand in the Alpaca app is detected and logged as a skip.

### 4.5 Sizing and room

- Size per stock is 20% of Alpaca `equity` at 15:46 (D2), so it grows and shrinks with the account. It is capped at the $25,000 per position cap every arm has, which binds only once the account passes $125,000.
- Before the build, a read only look at the Alpaca account fields and the NVDA, IREN and HUT margin fields, on a morning after Slow trades were held overnight (R2-9). It settles which field sets the overnight room and whether holding overnight shrinks the next day's day trading power. Any shrink is declared as an effect on the other strategies (R5).
- Room overnight (D3) is read live from Alpaca at 15:46, from `regt_buying_power` and each stock's margin need, minus Slow trades held tonight. Each order must also fit Alpaca `buying_power`. Slow trade buys for the morning are not counted, because they buy at 09:30 when the holds sell. Buys go in NVDA, IREN, HUT order and the last ones shrink (X11).
- At 20% each (about $29,800 in all today) with both Slow slots full, the account holds about $80,000 overnight, 1.6 times its $49,700, inside the 2 times limit.

### 4.6 Splits and other corporate actions

- At 09:00 compare Alpaca's share count with the ledger for each hold. Equal, keep the queued sale.
- Different, read Alpaca corporate actions. A split with a matching ratio adjusts the ledger (shares times ratio, price divided by ratio), logs a plain sentence and replaces the sale for the new count.
- Anything else (no action found, a merger, a new symbol), raise needs look and sell min(hold, Alpaca) of the old and any new symbol. Never book more than the hold, so the book never flips short (`account.py:327-347`).

### 4.7 Loss limits

- The daily loss stop is min($1,500, 2.5% of the day's starting equity), $1,243 today.
- Whether a stopped day still buys at 4:00 PM is D5.
- Whether the overnight result counts against the next day's stop is D6. With "no", S14 applies and the page says the stop covers day trades only.

### 4.8 Persistence and rollback

- New optional top-level checkpoint key `overnight`, plain JSON only (no `__type__` classes, so an old build can still decode the payload, `persistence.py:117-127`). Holds intents, attempt ids, states, the loss offset with its date, the "no buy tonight" control with its date, the skip log and the fidelity log.
- At booking, if a position in the stock exists that is not an overnight hold, the fill is not merged into it. Needs look is raised and the controller sells the overnight shares at the next open as usual (R2-13).
- Rollback hazard. A build without this code sees holds as plain intraday positions and liquidates them at the session boundary. Roll back only between 09:40 and 15:25 with no hold and no overnight order open. `OVERNIGHT_MODE=off` is the normal switch.

### 4.9 Health, skips and fidelity log

- `/health.overnight`. Mode, per stock state, tonight's intents, open holds, queued sales, last booking, needs look reasons. Health stays 200.
- Every night without a buy logs a reason and the return the rule would have made.
- Fidelity log per trade. Feed pinned to SIP, fetched at 16:15 and 09:45 so bars are settled. Raw prices plus the split ratio, the research style price (last bar 15:55 to 15:59, 09:30 bar open), the official close and open, the real fills, whether the rule's look ahead check (`ok[d+1]`) held, and gross returns only (research net minus its cost). It gates nothing.

## 5. Page changes

Mockups for desktop and phone come first and need your approval before any page code changes. The state list is MT3's approved list adapted to ADT.

1. Holds get their own group, "Overnight holds", kept out of the Quick trades count (`page.tsx:68-71`).
2. Holding rows. "43 shares bought at $228.87 at the close. No stop. Sells at the 9:30 AM open on Mon Oct 5." Always the day. "Over the weekend" or "over the holiday" when true. IREN and HUT rows say they move together. No "Sell now" and no "Move safety exit" button on a hold (they cannot work). Strategy label "NVDA overnight", never "Manual trade".
3. Chip "20% of the account a night (about $9,940), no stop" from settings and live equity.
4. From 3:45 PM the row says what happens at the close or why a stock has no buy tonight.
5. Worst tested night in dollars at today's sizes, with "A worse night can happen" and, when D6 is no, "The daily loss limit covers day trades only".
6. Account card. "Includes $X in overnight holds at their buy price. Their real value is known at 9:30 AM." Earnings nights add "Held through earnings, by rule. Your Alpaca app may show a big after hours move."
7. Results split day trades and overnight holds. Overnight results count on the day they sell. Strategy rows say "Up so far" or "Down so far", never "Making money". Evidence line opens "Not proven", read from `backend/app/data/overnight_research_summary.json` (inside the image).
8. States. Evening, weekend, long weekend, 3:50 PM locked, each skip reason, first day before the first buy, one stock not booked, morning sold, partial morning, fallback sale, split sale, needs look, robot down with holds, control used.
9. Red banner while any hold is unsold after 09:31.
10. Strings that become false and are replaced, from a grep backed table asserted by a page test. At least `SafetyCard.tsx:28, 52, 59-61, 68, 74, 79`, `plain.ts:289-297, 325-327, 330, 350`, `page.tsx:166, 187`, `README.md:15, 39, 81, 286`. `page.tsx:171` and `SegmentedModeToggle.tsx:29, 39` stay true.
11. Phone and desktop height budgets, and a new verify script in the style of `verify_holding_mood.py`. `verify_compact_dashboard.py` fixtures updated for the changed strings.

## 6. Settings

| Setting | Default | Where |
|---|---|---|
| `OVERNIGHT_MODE` | `live` | Railway variable |
| On or off per stock | on | Railway variables `OVERNIGHT_NVDA`, `OVERNIGHT_IREN`, `OVERNIGHT_HUT` |
| Share of the account per stock | 20% | Railway variable `OVERNIGHT_PCT` |
| No buy tonight | off | page control or Railway, D4 |

## 7. Proof

- T1 Decision parity, on a committed fixture. A generator reruns research `run_overnight` on the Mac with the data and writes one row per stock per session, 2023-10-02 to 2026-09-25 (bar counts before 15:45, 09:30 bar present, last bar minute, next session, outcome), plus the sha256 of the trade files, `holdout.json` and the bar files. The small fixture and a signed run record are committed, so T1 runs on every checkout. The test feeds each session to `overnight_schedule.py` with only bars that start before 15:45 and no next day data, and asserts the (stock, buy date, sale date) sets match research apart from X1 (IREN 739 nights), with the SPY calendar and research early closes. A second run uses the production calendar on 2026 sessions and must give the same answer. It asserts `NYSE_HOLIDAYS` for 2026 equals the SPY calendar gaps.
- T1b Auction prices, DONE 2026-09-30. SIP daily bars (split adjusted, from the relay, `research/edge_hunt_2026_09_29/data/auction/`) for every test night. The rule rerun on the daily bar close and next open, same nights, same research cost. NVDA won 56.5%, +18.7 bps a night, PF 1.35 (research 56.9%, +19.2, 1.36). IREN 52.2%, +42.2, PF 1.37 (research 52.4%, +44.4, 1.40). HUT 52.0%, +43.3, PF 1.39 (research 52.8%, +45.4, 1.42). Median price difference 0.0 bps both ends. The auction costs 0.5 to 2.2 bps a night. Caveat, Alpaca daily bar open and close are taken as the auction prints, not confirmed (Q5). D10 is not triggered by T1b.
- T2 Calendar. 15:46 window, early close with raw extended hours bars (2025-11-28), Friday to Monday, holiday Monday, the 2025-01-08 to 01-10 closure, DST weeks, 2028 refused, Alpaca calendar disagreement refused.
- T3 Broker with a fake Alpaca. `cls` accepted before 15:50 and refused after, `opg` refused 09:28 to 19:00 and queued after 19:00, duplicate client id, 429 and 5xx looked up by client id before a new attempt, partial and zero auction fills, wash trade 403 and 40310000 classified as definite refusals, an order cancelled by hand, a halted open (one live fallback attempt at a time), a sale sent while saves fail, and `_broker_gate` never called.
- T4 Restarts. Down 15:40 to 15:52, restart at 15:45 with the control already used (control kept), restart at 16:02 with an unbooked fill (no mismatch), down at 19:00, restart at 09:10 and 09:33.
- T5 Exemptions. Holds survive 15:45, 15:50, 15:55, 15:58, 16:00, the session boundary, the loss stop, flatten one and all, news and tighten stop.
- T6 Symbol conflict. A day NVDA limit entry working at 15:46 is cancelled. A day NVDA stop firing at 15:45:30 still works. A day NVDA trade is closed first, recorded normally, then the buy goes in. On a no buy night the day trade is closed at 15:55 as today. A 09:31 day short in NVDA while the sale is unbooked is refused. ORB cannot claim a held stock, and a stock held by ORB is skipped.
- T7 Loss limit. A bad night does not trip the stop at 09:31. Robot down 23:00 to 09:40, and Friday to Monday 09:35, one winning and one losing night each, lands in the right day with the right stop. Intraday losses still trip it. Results and the Safety meter show the overnight result on its own line.
- T8 Every reader of positions and orders has a decision. Holds survive a full main loop pass.
- T9 Splits. NVDA 10 for 1, a reverse split with a symbol change, a missing ratio, a split applied before 09:00 (no mismatch banner).
- T10 No mismatch. Fills before and after the snapshot, with a staged Slow trade buy at 09:30.
- T11 Checkpoint. Round trip, old checkpoint without the key, production fixture, and the previous commit restoring a new checkpoint.
- T12 Real price dry run across two nights and a weekend with all other strategies on, against the T3 fake Alpaca. Holds bought and sold, day strategies unchanged, bot and fake agree on positions, cash drift under $5 at buy price marks.
- T13 SKIPPED by operator ruling 2026-09-30 ("skip the test, start building"). Q1 stays open. Handled by building D10's fallback into the controller. A definite refusal of the closing auction buy (any 4xx other than wash trade or buying power) switches that night to a plain market buy at 15:59:30. A definite refusal of the opening auction sale switches it to a plain day market sale sent after 19:00, which Alpaca queues for the next open. Both are logged and shown on the page. Original design kept below for reference. In IREN (no ADT strategy trades it, so a resting order cannot block a day trade exit). One share closing auction buy sent at 15:30 and confirmed cancelled by 15:40. One share opening auction buy sent at 19:05 and cancelled by 19:15.
- Full backend suite, E2E runner, frontend build, the page verify script, `production_watchdog.py` and the multi day dry runs updated for holds.

## 8. Build order and going live

1. T13 smoke test, T1b measurement and the read only Alpaca account and margin look (section 4.5). D10 is asked only if T13 or T1b fails.
2. `overnight_schedule.py`, the committed fixture, T1, T2.
3. Broker methods and `overnight_execution.py` with T3, T4, T9, T10.
4. Shared code decisions S1 to S20 with T5 to T8, T11.
5. Health and card payload, then mockups, then page code after your approval.
6. T12, full suites, independent review of the diff.
7. Deploy on your yes, in the D8 window, timed so the first live close is a Monday to Thursday with a trading day after it. That means Sunday to Wednesday evenings, or Monday to Thursday before 08:50, never the evening before a holiday. The first buy is the first 4:00 PM close after the release is live, at full size. Check at 15:47 (orders accepted at Alpaca), 16:02 (holds booked), 19:01 (sales queued), next 09:31 (sold, stocks released).

## 9. Out of scope

Earnings filters. After hours prices on the page. Any change to the rule in section 1. A backup seller thread (the queued Alpaca sale does that job).

## 10. Open questions

- Q1 Does Alpaca paper take closing and opening auction market orders on PA3CSVDZMMPY? The docs mark them "contact sales" for some accounts. T13 answers it.
- Q2 Does the relay stream after hours quotes? S15 makes it irrelevant for equity. It matters for page marks.
- Q3 How does Alpaca paper price auction fills? The fidelity log answers it.
- Q4 Does Alpaca route an opening sale queued on Friday at 19:00 to Monday's open? T13 or the first Friday answers it. The 09:00 check covers it either way.
- Q5 Are SIP daily bars' open and close the official auction prints? T1b states what it used.

## 11. Operator decisions (all decided 2026-09-30)

- D1 DECIDED 2026-09-30. ADT only. The MT3 overnight holds build is paused (its plan stays as written, nothing built there). Options shown were ADT only, MT3 only, both.
- D2 DECIDED 2026-09-30. 20% of the account per stock, read from Alpaca equity at 15:46, so size follows the account up and down. The operator asked for a percentage "in the middle" of the options shown ($5,000, $10,000, $17,000, which were about 10%, 20% and 34% of the account). About $9,940 per stock today. For MT3 the choice had been $17,000. Test figures at $10,000 per stock, which is close:

  | Each stock | Held a night | Worst night | 1 night in 100 | Worst drop from a high | Nights losing more than $1,243 | Per year in the test |
  |---|---|---|---|---|---|---|
  | $5,000 | $15,000 | -$3,025 | -$835 | -$6,725 | 5 of 742 | $13,690 |
  | $10,000 | $30,000 | -$6,050 | -$1,670 | -$13,449 | 23 of 742 | $27,381 |
  | $17,000 | $51,000 | -$10,285 | -$2,839 | -$22,864 | 88 of 742 | $46,547 |

  Method. Dollars times the test's net return, nights added up, no compounding. Slow trades can add to a bad night. On the worst night two full Slow slots would have lost about $3,350 more (estimate from MU and AMD opens). Because size is a share of the account, dollar losses shrink after losses and grow after gains.
- D3 DECIDED 2026-09-30. A. Up to Alpaca's overnight limit of 2 times the account, read live at 15:46, buys shrink only if that limit would be passed (worst case today 1.6 times, so nothing shrinks). Rejected B, never more than own money, because it would skip or shrink the rule whenever Slow trades are held.
- D4 DECIDED 2026-09-30. A. A page button "No overnight buy tonight", usable until 15:49:30, saved durably with its date, never touches a sale. Security flagged once and accepted: it is a new write action with no sign in, like flatten today (standing operator choice for ADT). Its worst misuse is skipping one night's buy. Same protections as the existing write actions. Rejected B, Railway only, because a restart means it only works from the next day.
- D5 DECIDED 2026-09-30. A. A day that hit the daily loss limit still buys at 4:00 PM, as the rule does every night. Rejected B (skip), because it departs from the rule for no tested gain. The page button is the manual way to skip a night.
- D6 DECIDED 2026-09-30. A. The overnight result does not count against the next day's loss limit (S14 offset). It still counts in Results on the day it sells, and the page says the daily limit covers day trades only. Same as the MT3 choice. Rejected B, because day trading would stop at 9:30 AM on about 1 day in 32.
- D7 DECIDED 2026-09-30 (operator took all remaining recommendations). Close the day trade early at 15:46, then send the buy (X6). Rejected skipping the night.
- D8 DECIDED 2026-09-30. Deploys 16:10 to 18:50 and 19:15 to 08:50 on trading days, or any time on weekends and holidays, never while a day trade is open. The first release is timed so its first live close is Monday to Thursday (section 8). Rollback only 09:40 to 15:25 with nothing held.
- D9 DECIDED 2026-09-30. "Close all" leaves holds out. They sell at the next open. The reply and the confirm text say so.
- D10 DECIDED 2026-09-30, applies only if T13 shows Alpaca paper refuses auction orders, or T1b shows the auction prices lose the edge. Then plain market orders at 15:59:30 (an exception to `_broker_gate` for the controller only) and a market sale queued before 09:28 (Alpaca fills those at the opening print). The operator is told the T13 and T1b results either way.

## 12. Known limits

- Auction prices differ from the research bar prices. T1b gives the size.
- Earnings nights are traded blind, as in the test.
- Equity after the close uses the buy price until the 9:30 AM sale.
- The results came from three years in which all three stocks rose far more than the market.
- ADT's operator endpoints have no sign in (standing operator choice). D4's page button would add one more unprotected write, with the same protections as flatten.

## 13. Review findings (v1 attack, 2026-09-30)

Three read only subagents attacked v1. Verdicts. Fidelity "fix 1 to 3 before building". Engineering "do not build as written". Operator "not ready to put to the operator".

| # | Reviewer | Severity | Finding | Answer |
|---|---|---|---|---|
| Fi1 | Fidelity | HIGH | Auction versus bar difference asserted, never measured. 09:30 bar noise is large next to IREN's 2026 edge. | Accepted. T1b before build, X3, section 12 |
| Fi2 | Fidelity | HIGH | T1 cannot pass on the production calendar (2026 and 2027 only) | Accepted. Calendar object, two runs, holiday check (T1) |
| Fi3 | Fidelity | HIGH | T1 return check circular, no proof the schedule only sees 15:46 data | Accepted. T1 renamed decision parity, truncated bars, IREN 739 |
| Fi4 | Fidelity | MEDIUM | Early close skip has no backstop, SIP extended hours bars pass the count | Accepted. Regular session bars only, Alpaca calendar cross check, T2 case |
| Fi5 | Fidelity | MEDIUM | X2 off by one and wrong count, no named bar source | Accepted. X2 reworded, REST bars before 15:45 at 15:46:05 |
| Fi6 | Fidelity | MEDIUM | Unscheduled closure would sell on a closed day, fixed client id | Accepted. Alpaca calendar, attempt ids, T2 closure case |
| Fi7 | Fidelity | MEDIUM | Fidelity log does not compare like with like | Accepted. Section 4.9 |
| Fi8 | Fidelity | MEDIUM | X table missing paper simulation, no cost, sale day gate, shrink, other actions | Accepted. X8 to X12 |
| Fi9 | Fidelity | LOW | No 2026 row, shared worst date hidden | Accepted. Section 1 table and note |
| Fi10 | Fidelity | LOW | Only matching splits handled | Accepted. Section 4.6, X12, T9 |
| Fi11 | Fidelity | LOW | R1 satisfiable by a skip | Accepted. Committed fixture and run record |
| E1 | Engineering | HIGH | A day short in a held stock counts as an exit and sells the hold | Accepted. S1, S2, T6 |
| E2 | Engineering | HIGH | Working day entries before 15:45 can fill next to the closing buy | Accepted. Cancel at 15:46, send only when Alpaca is flat, S2 |
| E3 | Engineering | HIGH | Two loss baselines, a restart wipes the shift | Accepted. S14 offset in the risk engine, saved |
| E4 | Engineering | HIGH | Marks happen at engine.py 567 and 626, not the cited lines | Accepted. S15 |
| E5 | Engineering | HIGH | No operator pause exists | Accepted. D4, section 4.4 |
| E6 | Engineering | HIGH | Compare races the booking, can drop a Slow trade buy | Accepted. S13, T10 |
| E7 | Engineering | MEDIUM | Startup compare runs before backfill | Accepted. Reconcile right after restore |
| E8 | Engineering | MEDIUM | `_broker_gate` refuses the morning send | Accepted. Direct broker calls, own time gate, T3 |
| E9 | Engineering | MEDIUM | Buy admission misses the mismatch and durable save rules | Accepted. Section 3, X5 |
| E10 | Engineering | MEDIUM | A live opening sale blocks a market sell, ids cannot be reused | Accepted. 09:31 cancel then market, attempt ids |
| E11 | Engineering | MEDIUM | Selling Alpaca's larger count flips the book short | Accepted. min(hold, Alpaca), corporate actions read |
| E12 | Engineering | MEDIUM | Missed readers (quote events, projection, save failure, API cancel, prune, reset, fee) | Accepted. No overnight order in working orders, S12, S15, reset hook |
| E13 | Engineering | MEDIUM | Buying power fields unnamed, staged Slow buys miscounted | Accepted. Section 4.5 |
| E14 | Engineering | MEDIUM | Live history keeps only 120 bars | Accepted. REST count at 15:46:05 |
| E15 | Engineering | MEDIUM | Research data not committed, summary file missing | Accepted. Committed fixture, summary under backend/app/data |
| E16 | Engineering | MEDIUM | New classes in the checkpoint break an old build | Accepted. Plain JSON, previous commit restore test |
| E17 | Engineering | MEDIUM | T12 with zero orders always mismatches, drift criterion wrong | Accepted. Fake Alpaca, restated criterion |
| E18 | Engineering | LOW | S6 EOD_FLAT change not needed and harmful | Accepted. Dropped |
| E19 | Engineering | LOW | Line references off | Accepted. Corrected |
| E20 | Engineering | LOW | Manual flatten cannot wait for flat and blocks the loop | Accepted. Dedicated close on the worker pool |
| E21 | Engineering | LOW | Startup can take 120 s, drain 80 s | Accepted. 15:47:30 alert, deploy window |
| E22 | Engineering | LOW | Watchdog, page parity, dry runs and no broker mode break | Accepted. S19, section 5.11, section 7 |
| E23 | Engineering | LOW | Sale day 09:30 bar gate undeclared | Accepted. X10 |
| O1 | Operator | BLOCKER | The pause the plan relied on does not exist | Accepted. D4 |
| O2 | Operator | HIGH | The sale needs the robot up 09:00 to 09:28 | Accepted. Sale queued at 19:00, 09:00 check, banner |
| O3 | Operator | HIGH | Decisions carry no dollars | Accepted. D2, D5, D6 |
| O4 | Operator | HIGH | D1 and D2 silently reverse MT3 choices | Accepted. Both now say what you chose for MT3 and why ADT differs |
| O5 | Operator | HIGH | Slow trade interplay not in dollars, staged buys double counted | Accepted. Section 4.5, D2, D3 |
| O6 | Operator | HIGH | Stale string list wrong both ways | Accepted. Section 5.10, grep backed test |
| O7 | Operator | HIGH | Holding card shows holds wrongly, buttons fail | Accepted. Section 5.2, S10 |
| O8 | Operator | HIGH | MT3's accepted page items dropped | Accepted. Section 5 |
| O9 | Operator | HIGH | Plain words section not honest or readable | Accepted. Rewritten |
| O10 | Operator | MEDIUM | Weekend nights are riskier, first live night could be a Friday | Accepted in a changed form. The first release deploys Monday to Thursday (D8). Skipping a Friday night the live robot could trade was not taken, by your ruling |
| O11 | Operator | MEDIUM | D6 is two decisions, D8 contradicts MT3, no close all decision, window mismatch | Accepted. D5, D6, D8, D9, 15:49:30 everywhere |
| O12 | Operator | MEDIUM | A split before 09:00 shows a mismatch banner overnight | Accepted. S13, T9 |
| O13 | Operator | MEDIUM | Smoke test can leave an orphan share | Accepted. T13 times and cleanup |
| O14 | Operator | MEDIUM | Summary file not in the Docker image | Accepted. Under backend/app/data |
| O15 | Operator | MEDIUM | Shrunk or refused buys undeclared | Accepted. X11 |
| O16 | Operator | LOW | Earnings nights need a page note | Accepted. Section 5.6 |
| O17 | Operator | LOW | Colons in prose, jargon | Accepted. Fixed |

Round 2 (v2 re-check, 2026-09-30). One read only reviewer. Verdict "fix then build", must fix 1, 2, 3, 4, 5, 11. It also found that round 1's operator figures ($6,080, $14,958, 24 nights) came from the doubled cost column, so the D2 table stands.

| # | Severity | Finding | Answer |
|---|---|---|---|
| R2-1 | HIGH | S1 and S2 from 15:45 would block a day trade's own stop, X6 path unnamed, no release on a no buy night | Accepted. S1 and S2 narrowed, X6 through the manual flatten steps, release at 15:49:30, T6 |
| R2-2 | HIGH | Restart books the sale before the day's reset, so the stop and the day are wrong | Accepted. Session boundary check first, offset keyed to its date, T7 |
| R2-3 | MEDIUM | Safety meter reads the account drawdown and would show "$6,050 of $1,243" | Accepted. S14, section 5.10 |
| R2-4 | MEDIUM | A failed save would block sales | Accepted. Only buys need a durable save, T3 |
| R2-5 | MEDIUM | Fallback retries can stack market sells | Accepted. One live attempt at a time, section 3, T3 |
| R2-6 | MEDIUM | Monday to Thursday deploy still allows a Friday or holiday first night | Accepted. Rule set by the first live close, section 8 |
| R2-7 | MEDIUM | Railway option clashes with the deploy window | Accepted. Next day only, D4, section 4.4 |
| R2-8 | MEDIUM | Control not saved, a restart forgets it | Accepted. Saved and re-applied, T4 |
| R2-9 | MEDIUM | Room read from the wrong field, day trading power may shrink after held nights | Accepted. Read only account and margin look before the build, section 4.5 |
| R2-10 | MEDIUM | Smoke test in NVDA could block a day trade exit | Accepted. T13 in IREN |
| R2-11 | MEDIUM | A booking Order linked to Alpaca could be cancelled by the settle loop | Accepted. `OVERNIGHT_POLICY`, section 4.1, T8 |
| R2-12 | LOW | Relay outage not a listed skip | Accepted. X5 |
| R2-13 | LOW | Unsold earlier hold, merge at booking | Accepted. X5, section 4.8 |
| R2-14 | LOW | Stock held by ORB not checked | Accepted. Section 3, X5, T6 |
| R2-15 | LOW | Broker additions incomplete | Accepted. Section 4.1 |
