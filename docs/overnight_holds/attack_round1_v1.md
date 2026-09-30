Pipeline completed: 3 stages finished.

## fidelity

Research fidelity attack on PLAN_2026_09_30_overnight_holds.md. Everything here was read only. Python ran with PYTHONDONTWRITEBYTECODE=1 and nothing touched the network.

Checked and correct. A rerun of run_overnight gives trade arrays identical to data/trades_{NVDA,IREN,HUT}_overnight_0.npy. Every cited line is right (lib.py:89, lib.py:98, families.py:93-94, 97, 98, 108). The hidden gates in the test window 2023-10-02 to 2026-09-25 are as follows. There are 7 early closes. For IREN, ok[d+1] fails on 2023-10-06, 10-17 and 11-07, which matches X1, and ok[d] fails on 2023-10-09, 10-18 and 11-08. The 09:30 bar is never missing on the buy day or the sale day, and close[d] is never NaN. The last bar is index 389 (15:59) on every 16:00 session for all three stocks. The record table matches holdout.json. NVDA shows 742 nights, 4.95 a week, 56.87% won, +19.2 bps, PF 1.362, worst -14.20%, OOS p .0193. IREN shows 736, 4.91, 52.45%, +44.4, 1.398, -27.31%, .0091. HUT shows 742, 4.95, 52.83%, +45.4, 1.419, -18.99%, .0305. q is .149 for all three. The SPY calendar's weekday gaps are exactly the holidays plus 2025-01-09, and the 2026 NYSE_HOLIDAYS match those gaps.

1. HIGH. Section 12 ("a few bps either way"), X3, F4, F5. The auction versus bar difference is claimed but never measured, and this data cannot measure it. fetch.py:74 keeps only minutes 570 to 959, so there is no 16:00 bar, and the npz holds no trade conditions. The proxies below, for 2026 nights, are computed. The 15:59 bar |close minus VWAP| has a median of 4.1, 4.6 and 4.6 bps and a p90 near 12 to 13 bps (NVDA, IREN, HUT). The 09:30 sale bar is where the dispersion is. Its median high to low range is 62, 163 and 179 bps, and its |open minus VWAP| has a median of 11, 32 and 27 bps (p90 24, 90, 82). The 2026 net mean per night is only +13.8 (NVDA), +13.1 (IREN, 46.2% won) and +38.4 (HUT) bps. So an opening auction bias of the same order as the 09:30 bar's spread would wipe out IREN's 2026 edge. These proxies bound the intra-minute noise. They do not measure the auction's deviation, and if the 09:30 bar open already is the Nasdaq opening cross, the gap is small. I cannot tell which. The fix is a new pre-build task T1b, which is a measurement and not a rule change. Fetch SIP 1Day bars (official open and close, to be confirmed) or the 16:00 minute bar plus trades with conditions for 2023-10 to 2026-09. Rerun the unchanged rule with auction prices on all nights. Put the per stock delta in the section 1 table and in the page's "Not proven" line. Replace "a few bps" with the measured number.

2. HIGH. T1 (section 7), S20, R1. As written, T1 cannot pass against the production calendar. trading_windows.py:16-22 covers only 2026 and 2027, and S20 makes the controller refuse buys outside the covered years. T1 feeds 2023-10-02 to 2026-09-25, so about 558 of the 742 nights would be refused. For 2023 to 2025, next_trading_day would also sell on holidays (for example 2025-01-08 to 01-09, the Carter closure). If T1 injects the research calendar instead, the live calendar gets tested on only the 184 nights of 2026, which contain no early close and no unscheduled closure. The fix has three parts. Make overnight_schedule take a calendar object. Run T1 once with the SPY calendar plus lib.EARLY_CLOSES over the whole window, and once with the production trading_windows on the 2026 sessions, asserting identical output. Assert that NYSE_HOLIDAYS for 2026 equals the SPY calendar gaps.

3. HIGH. T1 wording, R1 "Fidelity first". T1's "returns recomputed from the research prices match to 1e-6" is circular. It re-derives research returns from research prices and cannot prove anything about fills that come from auctions. T1 also does not say that the schedule module gets only the data visible at 15:46 (day d bars with index 374 or lower, nothing from d+1). A module that quietly uses the full-day ok[d] would still pass. The fix is to rename T1 "decision parity". Feed it day d bars truncated to start times before 15:45 and assert that it never reads d+1. Assert IREN = 739 nights (736 + 3 from X1). Make T1b from finding 1 the price fidelity test.

4. MEDIUM. F3, X2, section 3 at 15:46. The early close skip rests entirely on NYSE_EARLY_CLOSES, and the bar count is no backstop. SIP carries extended hours bars after 13:00, and the raw npz shows it. NVDA has 375 bars between 09:30 and 15:44 on 2024-07-03, 2024-12-24, 2025-07-03, 2025-11-28 and 2025-12-24, so 375 + 15 = 390, which clears 312. IREN on 2025-11-28 gives 344 + 15. Research was protected only by its own hardcoded list (lib.py:19, cut at lib.py:84-87). T1 feeds bars already cut by lib.load, so it can never see this. The fix has three parts. Count only bars that start before session_close(d), using _fetch_session_minutes (main.py:2773-2778 already bounds by session_close). Cross-check the session close with Alpaca's read-only calendar at 15:46 and refuse the buy on a mismatch. Add a T2 case that feeds raw npz bars for 2025-11-28.

5. MEDIUM. X2 and the 15:46 count source. The X2 claim is false as written. There are 810 sessions that close at 16:00, not 809. The literal rule ("bars 09:30 to 15:44 + 15") disagrees with ok[d] for IREN on 2023-09-27, where 297 + 15 = 312 passes but the day ended at 311. That date is warm up, outside T1's window, and inside the window there are 0 disagreements. The only variant that agrees on all 810 counts the 15:45 bar and adds 14. The bot does not have that bar at 15:46:00, because the bar starting 15:45 closes at 15:46:00 and trading_windows.py GATE_LAG_SEC says bars arrive about a minute after they start. The off by one matters. "Bars through 15:45 + 15" gives 4 IREN disagreements, 3 of them inside the window (2023-10-09, 10-18, 11-08). The plan also never names where the count comes from. A websocket count after a mid-day restart, or after a late IREN or HUT subscription (S18), would undercount and skip nights silently. The fix is to define the count as bars starting in [09:30, 15:45) plus 15, taken from the SIP REST source (_fetch_session_minutes), and run the check at 15:46:05 or later. Reword X2 as "0 of 742 in the window, 1 of 810 overall (IREN 2023-09-27)".

6. MEDIUM. Section 3 at 09:00 and 09:30:30, S19. Research sells at the next row of the SPY calendar, and that correctly handled 2025-01-09. Live, next_trading_day uses a static list. On an unscheduled closure the 09:00 opening sell and the 09:30:30 X7 market sell would fire on a closed day. The client id adt-ovn-SYM-buydate-sell is fixed per buy date, so if the order expires, the resend the next day may be refused as a duplicate. I have not verified Alpaca's uniqueness rule. The fix is to take the sale date from Alpaca's read-only calendar and clock at 15:46 and 09:00, add an attempt suffix to the sell client id, and add a T2 case that replays 2025-01-08 to 01-10.

7. MEDIUM. Section 4.8 fidelity log, Q3. The log does not compare like with like, in six ways.
   a. The bar fetch at main.py:2148 sets no feed and no adjustment, while research used feed=sip and adjustment=split (fetch.py:66). On a split night raw bars read as -90%.
   b. It logs the "15:59 bar close", while research used the last bar from 15:55 to 15:59 (lib.py:96-99). These are equal on every historical night but differ by rule.
   c. No official open or close is logged, so the gap cannot be split into bar to auction and auction to paper simulator, and Q3 cannot be answered by the log as designed.
   d. It does not record whether ok[d+1] held, so nights research would have dropped get mixed in.
   e. If the fetch runs at 16:00:05 it can catch unrevised bars.
   f. Research net includes cost averaging 6.0, 12.4 and 8.6 bps a night, so the comparison must be gross to gross.
   The fix is to pin feed=sip, fetch after 16:15 and 09:45, log raw prices plus the split ratio, log the official close and open (daily bar or the 16:00 bar), log ok[d+1], and compare gross returns only.

8. MEDIUM. The X table is incomplete. Five differences are missing.
   a. X8. Paper fills are Alpaca's simulation, not the exchange auction, so the plain words' "uses the exchange's own closing and opening auctions" is wrong for this account.
   b. X9. There is no cost on paper, while research deducted 6 to 12 bps a night, so live results will look better than research even at identical prices.
   c. X10. The sale side gate at families.py:105-107 drops a night whose sale day has no 09:30 bar. This is look ahead distinct from ok[d+1]. It never fired in 2023 to 2026, but it should be declared next to X1.
   d. X11. Buys shrunk to zero by D3 or the $25,000 cap, and partial or zero auction fills. Research always filled fully.
   e. X12. Corporate actions other than splits (finding 10).

9. LOW. Section 1 research record. The numbers are verified, but the plan does not explain why IREN's count is 736, which is 742 minus 3 (ok[d+1]) minus 3 (ok[d] on the days after). All three worst nights fall on the same entry, 2024-08-02 (a Friday, sold 08-05). The full period averages hide the 2026 figures given in finding 1. The fix is to add a "2026 so far" row and the shared worst date so the page reads the record honestly.

10. LOW. F9 and section 4.5 (splits).
   a. NVDA 10 for 1. The adjusted series is continuous (2024-06-07 close 120.87, 06-10 open 120.35), and cost is unaffected because it sits on the 6 bps floor.
   b. HUT Dec 2023. The series is continuous (11-30 close 9.55, 12-01 open 9.75), and research treats that night as ordinary. If the event was the 1 for 5 exchange I believe it was (not verified here), adjusted prices around $9.9 cut the charged cost from about 52 to 12 bps on 42 nights. HUT recomputes from 45.4 to 43.2 bps, PF 1.419 to 1.394, won 52.83% to 52.56%. It still passes rule 3.
   c. Live, 4.5 handles only a split with a matching ratio. A merger or new asset id would leave "Alpaca's count of that stock" at zero under the old symbol.
   The fix is to treat any corporate action type as needs look, sell the old and new asset ids, and add a T9 fixture for a reverse split with a symbol change.

11. LOW. T1 "reports SKIPPED loudly elsewhere", R1. The bar files are gitignored, so R1 can be satisfied by a skip on a machine without data. The fix is to make R1 require a committed T1 run record (hashes plus pass) from the data machine.

Verdict. The rule's gates are transcribed correctly and T1's window agrees, but price fidelity (auction versus bar) is unmeasured and asserted, and T1 cannot run against the production calendar as written. Fix findings 1 to 3 before building.

---

## engineering

Engineering attack on PLAN_2026_09_30_overnight_holds.md, checked against the code at cd67aec. I changed no files and sent no network requests. The research table in section 1 matches the trade files (column 4, normal cost).

1. HIGH. S12, T6. The validator treats another strategy's short in a held stock as an exit of the hold. The TRI guard at `main.py:486` runs before exit classification. The overnight check S12 describes would sit inside the `if not is_exit` block at `main.py:527`. But `main.py:506-510` sets is_exit for any opposite side order on a non swing position. `execute_strategy_signal` only checks for duplicate brackets and working entries (`main.py:1985-2005`), and S14 takes the hold out of the committed set (`main.py:2037-2046`). So a News, MR or VWAP short in NVDA is admitted. `engine.py:266` caps the sale at what Alpaca holds, so while the MOO is unfilled, during X7, or after a restart at 09:33, it sells the hold's shares and `account.py:317-326` shrinks the overnight Position. Fix. Add a guard next to `main.py:486` that refuses any order, either side, from anyone but the controller on OVERNIGHT_IDS positions and OVERNIGHT_RESERVED symbols. Add the same refusal in `_broker_execute`. Add a T6 case with a 09:31 short while the sale is not yet booked.

2. HIGH. Section 3 at 15:46, X6, S12. Entries accepted before 15:45 keep working until the 15:50 purge (`main.py:2649-2663`). They send a real order when they trigger, without running the validator again, and strategies can emit LIMIT entries (`main.py:2089`). Say a NVDA limit buy fills at 15:47 while the MOC buy rests. Its 15:55 flatten sell then gets Alpaca's wash trade 403, retries every 30 s (`engine.py:162`) and fails the 15:58 audit. At 16:00 `account.py:307-315` merges the MOC fill into the day position under the day strategy's id. Fix. At 15:46, cancel every local working order in the symbol that is not overnight. Only send when the local book is flat, Alpaca `position_qty` is 0 and `list_open_orders(symbol)` is empty. Make `_broker_execute` hard refuse non overnight entries in reserved symbols. At booking, if a position that is not overnight exists in the symbol, raise needs look instead of merging.

3. HIGH. S16, 4.6, D7, T7. There are two loss baselines. `account.daily_starting_equity` drives the page's daily P&L (`main.py:1610`) and the session summary (`main.py:1208`). `risk_engine.config.starting_equity` drives the stop (`risk.py:114`, `risk.py:192`). Restore copies the account baseline over the risk one (`runtime_state.py:236`) and recomputes the limit (`main.py:896`).
   - If S16 moves the account baseline, the overnight result disappears from the day's results, which contradicts 4.6.
   - If S16 moves only the risk baseline, any restart after the sale wipes the shift and the next quote trips the stop.
   - From the research files at $10,000 per stock, 22 of 742 nights lose at least $1,250 combined (today's limit), 5 lose at least $2,500, and the worst is minus $6,050.
   - The stop is also checked at `main.py:2440`, `main.py:2556`, `orb_integration.py:958` and `main.py:2503`.
   Fix. Leave the account baseline alone. Put a saved `overnight_realized_today` offset inside the risk engine, used in both drawdown formulas and at `main.py:2503`. Store it in the `overnight` checkpoint key and apply it again after `runtime_state.py:236` and `main.py:896`. Add a T7 case with a restart after the sale. The page should say the stop leaves out the overnight result (R7).

4. HIGH. S17. The cited lines `main.py:2440-2448` and `2556-2565` only evaluate the stop. Prices are marked at `engine.py:567` (quotes) and `engine.py:626` (bars), through `account.py:146-152`. With only the cited lines changed, after hours prices still move equity, the page, the midnight baseline (`main.py:1512-1515`) and the stop. Fix. Skip overnight holds in `account.update_market_price`, or at `engine.py:567` and `engine.py:626`, until the sale is booked.

5. HIGH. D6, D9, X4, section 3 at 15:46. No operator pause exists. `Strategy.pause()` at `strategies/base.py:342` has no caller anywhere. The WebSocket actions are only flatten, tighten stop and the swing actions (`main.py:3939-3990`), and no endpoint pauses anything. A Railway variable change needs a redeploy, which D8 forbids from 15:40 to 16:10. So tonight's buy can only be stopped by cancelling it by hand at Alpaca. Fix. Say that plainly, make an order cancelled by hand at Alpaca a logged skip (add to T3), or get an operator decision on a pause control.

6. HIGH. S15, T10. The position compare can race the controller's booking. `main.py:358` takes the Alpaca snapshot first and `main.py:382` compares against it later. A fill booked after the snapshot looks like a mismatch, and entries are refused for 30 s (`main.py:520-525`, `config.py:45`). At 09:30 that can reject a staged swing buy, and a rejected staged entry is dropped for the day (`swing_panic_dip.py:597-601`). That breaks R5. Fix. After the controller books, read positions again before `_compare_with_broker`. Add T10 cases with the fill before and after the snapshot, plus a staged swing buy.

7. MEDIUM. S22. `_startup_backfill` (`main.py:2843`) only starts when relay backfill is on, and only after pending event replay (`main.py:3153-3193`). The startup compare at `main.py:3129` runs before both. So a restart at 16:02 with an unbooked fill starts with a mismatch (`main.py:3130-3132`). S22 also contradicts itself ("before pending event replay"). Fix. Reconcile by client id in lifespan between `_restore_checkpoint()` and `main.py:3129`, before `orb.start()`.

8. MEDIUM. 4.1, section 3 at 09:00 and 09:30:30. `_broker_gate` (`main.py:452-462`) refuses anything outside 09:30 to the close, and only retries sells every 60 s. Tri calls it from its own send (`tri_execution.py` `_entry_poll`). If the new controller copies tri, the 09:00 MOO is never sent and every night silently falls to X7. If X7 goes through the engine, S1 refuses it (the `engine.py:228-231` pattern). Fix. State that cls, opg and the X7 market sell call AlpacaBroker directly with the controller's own time gate. T3 asserts `_broker_gate` is never called.

9. MEDIUM. 4.2, section 3 at 15:46. The buy's admission list leaves out two rules every other entry path follows. No new exposure while the bot and Alpaca disagree (`main.py:460`, `main.py:520-525`, `orb_integration.py:652-653`). No new exposure after a failed durable save (`main.py:518-519`). Also, "write the intent" can succeed without saving, because `_checkpoint_runtime` returns True while an input is in flight (`main.py:777-778`). ERRORS.md already records this bug. Fix. Add both as operational skips, retried until 15:49:30 and declared as X8 like X5. Use tri's `checkpoint()` guard before any POST.

10. MEDIUM. X7, section 3 at 09:30:30. A live MOO holds all the shares, so a market sell while it is open gets Alpaca's 403 40310000. The plan gives no deadline for a MOO that stays live or only partly fills. The single ids `-buy` and `-sell` cannot be reused after an order was created and then cancelled or rejected. The 5 s retries need numbered attempt ids with lookup in order (the OR15 pattern at `engine.py:319-349`). Fix. At 09:31:00 cancel and confirm (`broker.py:429`), then market sell the rest as `adt-ovn-<SYM>-<date>-sell-<n>`. Keep the stock reserved until Alpaca shows 0 shares and no open overnight order.

11. MEDIUM. 4.5. Selling "Alpaca's count" when it is bigger than the hold and booking it runs the flip branch at `account.py:327-347`, which opens a local SHORT under MANUAL. Also, `broker.py` has no corporate actions read, which contradicts 4.1's "nothing else changes in the broker". Fix. Sell and book min(hold, Alpaca), raise needs look for the rest, and add the corporate actions method.

12. MEDIUM. T8. Readers of positions and orders that S1 to S22 miss.
   - `main.py:2477-2481`. A MARKET mirror order in working_orders turns every quote in that stock into a durable event plus a full checkpoint, from 15:46 to 16:00 and 09:00 to 09:30.
   - `main.py:2496-2504` projects the hold at the live price.
   - `main.py:806-822`, the save failure path, cancels the local MOC mirror but leaves the Alpaca order live.
   - `main.py:3753-3765`, the API cancel, lets anyone cancel a mirror order locally.
   - `main.py:596-601`, the MANUAL position count.
   - `engine.py:836-850` prunes orders at the midnight boundary (`main.py:1544`) while holds are still open.
   - `reset_runtime_state` at `main.py:3020` must also reset the controller.
   - `engine.py:724` books fee 0, so F6's "charges" never appear.
   Fix. Add each to the S table and to the T8 grep, or keep mirror orders out of working_orders and say so.

13. MEDIUM. 4.4, D3, Q4. ADT reads only equity and cash from Alpaca (`broker.py:171-181`), and the local buying power is 4x (`account.py:441-443`). The plan never names the Alpaca field. The "swing buys staged for the morning" do not exist at 15:46, because the swing scan stages them at 16:00 (`main.py:2744-2748`), and they fill after the 09:30 sale. Overnight exposure with 2 swing slots of $25,000 plus three holds:
   - D2 $10,000 gives $80,000, which is 1.6x of $50,000.
   - D2 $17,000 gives $101,000, which is above 2x (above $99,400 at today's equity of about $49,700).
   Fix. Cap = 2 × Alpaca `equity` minus swing positions held tonight. Also require each order's cost to be at most Alpaca `buying_power`. Not verified: whether Alpaca paper gives back day trading buying power when positions close intraday. T13 should log `buying_power`, `daytrading_buying_power` and `regt_buying_power` at 15:46 on a busy day.

14. MEDIUM. F2a, F2b. `market_history` keeps only 120 bars (`main.py:2352`), so it cannot count to 312. A restart during the day loses minutes and possibly the 09:30 bar. Fix. The controller keeps its own minute set in the checkpoint and repairs it at 15:46 with `_fetch_session_minutes` (`main.py:2773`).

15. MEDIUM. R1, T1, section 5 item 5. The research data is not committed. `research/edge_hunt_2026_09_29/.gitignore:1` ignores `data/`, and cd67aec is code only. So T1 is SKIPPED on every clean checkout, and the "committed summary file" does not exist. Fix. Commit a small derived parity fixture (rows plus sha256 values) and run T1 against it every time.

16. MEDIUM. 4.7, T11. Old code decodes the whole payload (`runtime_state.py:199`). A `__type__` that names a class in the new module fails in `_load_internal_type` (`persistence.py:117-127`), so an old build cannot start. Fix. Keep the `overnight` key plain JSON. Add a test where the previous commit restores a new checkpoint.

17. MEDIUM. T12. With zero orders to the real Alpaca, every compare flags a mismatch (`main.py:331-350`), which blocks every strategy for the whole dry run. Even when live, holds kept at buy price never match Alpaca's live marks. A 1% after hours move on $30,000 is $300, against the "under $5" criterion. Fix. Run T12 against the T3 fake Alpaca and restate the drift criterion.

18. LOW. S6. Today EOD_FLAT is set only when no swing position is open (`main.py:2736-2742`), and only `can_afford` reads it (`account.py:181-190`). Holds do not exist yet at 15:58, so no change is needed. The S6 wording would start setting EOD_FLAT with swing open, against R5. Fix. Drop S6's change.

19. LOW. Line references.
   - `main.py:2665-2666` is the cancel that runs at 15:55, on every 15:58 audit tick and at 16:00, not only at 16:00.
   - `tri_controller.tick` is at `main.py:2933`, not 2934.
   - S16 misses the account reset at `main.py:1515`.
   - S1's `engine.py:364-365` cannot be reached once line 316 skips overnight.
   - S10 has two sites inside `main.py:3783`, the cancel loop at 3819-3823 and the liquidation from about 3842.
   - S13 is mostly covered already (`orb_integration.py:474-481`).

20. LOW. X6. `_execute_manual_flatten` (`main.py:3783`) makes one attempt that blocks the event loop up to about 6 s (`broker.py:104-105`), and retries back off 30 s. That is about 7 tries in 3.5 minutes, and nothing waits for flat. It also blocks the loop, against 4.1. Fix. A dedicated close that is saved durably, then the controller polls on its worker pool for local flat, Alpaca 0 shares and no open orders, until 15:49:30.

21. LOW. X5, D8. `orb.start()` blocks startup for up to 120 s (`orb_facade_proc.py:36`, `main.py:3140`) before the clock loop starts (`main.py:3196`). Shutdown drain can take 80 s or more (`orb_integration.py:52`, `orb_integration.py:57`). Fix. Alert when there is no intent by 15:47:30, and widen the D8 block to 15:30 to 16:10.

22. LOW. Section 7. Things that will break.
   - `production_watchdog.py:149-153` calls any open position after 15:58 CRITICAL, using `main.py:3299`. That fires every night.
   - `verify_compact_dashboard.py` text parity fails on the strings section 5.6 replaces.
   - The multiday dry runs assert everything is flat at 15:58.
   - With the code default `OVERNIGHT_MODE=live`, the controller must do nothing when no broker is attached.
   Fix. List these and update them.

23. LOW. X1. The research also drops a night when the sale day has no 09:30 bar (`families.py:108-111`, `lib.py:103`), which is also look ahead. On the bars this affects 0 nights for all three stocks. Fix. Declare it in the X table.

Verdict. Do not build as written. Fix S12, X6, S16 and S17, S15 and S22, and the missing pause control first, then send it to review again.

---

## operator

Attack on PLAN_2026_09_30_overnight_holds.md. Angle is operator and risk. Read only. The only commands I ran were greps, git grep and numpy on the research trade files.

The numbers are based on `research/edge_hunt_2026_09_29/data/trades_{NVDA,IREN,HUT}_overnight_0.npy`, using the net return (column 5), matched night by night across the 742 nights, with dollars = amount x return. The day loss stop today is min($1,500, 2.5% x $49.7k) = $1,242.50.

| Each stock | Held a night | Worst night | 1 night in 100 | 1 in 20 | Worst drop from a high | Nights losing more than $1,242.50 |
|---|---|---|---|---|---|---|
| $5,000 | $15,000 | negative $3,040 | negative $848 | negative $524 | negative $7,479 | 5 |
| $10,000 | $30,000 | negative $6,080 | negative $1,697 | negative $1,049 | negative $14,958 (30% of the account) | 24 (about one every 6 weeks) |
| $17,000 | $51,000 | negative $10,336 | negative $2,885 | negative $1,783 | negative $25,428 (51%) | 92 (about one every 2 weeks) |

1. BLOCKER. Section 11 D6 and D9, X4, 4.6, section 3 row 15:46. The "existing pause" the plan relies on does not exist as an operator control in ADT. No endpoint or websocket action calls `Strategy.pause()`. `main.py` handles only FLATTEN_POSITION, FLATTEN_ALL, TIGHTEN_STOP and the SWING_* actions (`main.py:3951-3990`). The only POST endpoints are orders, cancel, flatten, swing action and the two ORB resolves (`main.py:3406,3432,3677,3753,3898,4050`). `grep -i pause frontend/hooks frontend/components` finds no button. "PAUSED" is internal status only (`base.py:342-348`, `tri_execution.py:338`). So D9's option "Railway variables plus the existing pause" leaves only Railway variables. A variable change restarts the service (MEMORY 2026-09-29, ORB switch "only the variable changed"). D8 forbids deploys from 15:40 to 16:10, so the operator has no way at all to stop tonight's $30,000 buy in the one window it matters. Fix. State in D9 that ADT has no pause today, and offer two honest options. Option (a) is one page switch "No overnight buy tonight", unauthenticated like flatten, flagged as a new write endpoint. That contradicts 12's "adds no new write endpoint", so say so. Option (b) is Railway only, with the stated rule "change OVERNIGHT_MODE before 15:30 so the restart is done by 15:40". Then drop or rewrite X4 and 4.6.

2. HIGH. In plain words, section 3 rows 09:00 and 09:30:30, X7, R2. The sale depends on the robot being up between 09:00 and 09:28. The plain words claim "a robot restart at 4:00 PM or 9:30 AM cannot make it miss the buy or the sale". That is false for any outage covering 09:00 to 09:28, and for a robot still down at 09:30:30 (X7 runs only when it is up). MT3's decided D6 had a backup seller and a 09:34:30 deadline, and this plan drops both without saying so. Fix. Queue the opening sell as soon as Alpaca accepts it, at 19:00 on the buy day (T3 already says opg is refused only from 09:28 to 19:00). Keep 09:00 as a verify and replace step for splits. Then the claim holds for any outage after 19:00. Reword the plain words to match. Add a top-of-page banner when any hold is unsold at 09:31.

3. HIGH. Section 11 D2, D3, D6, D7 and In plain words. The decisions carry no dollars, although MT3's D1 had exactly this table. Fix. Put the table above into D2. Add to D7 that at $10,000 the overnight loss alone beats the whole day limit on 24 of 742 nights, and with "no", the worst sale day is about negative $6,080 plus up to negative $1,242 of day trades. Add to D6 that on a day the robot already stopped for losing $1,242, it still puts $30,000 overnight with no stop. Also carry MT3's evidence that nights after big falls were average or better. State that the limit is $1,242.50 today, not $1,500.

4. HIGH. Section 11 D1 and D2 against MT3. Both silently contradict what this operator already decided. MT3 D1 was $17,000 each, "picked it over the $10,000 recommendation", and the MT3 plan says "The build starts on your go". MEMORY 2026-09-29 says "operator chose to run the three overnight holds in ManualTrading3". This plan recommends "ADT only for now" and $10,000 without mentioning either choice. Fix. Open D1 and D2 with "For MT3 you chose $17,000 and a build there." Give the ADT-specific reason for any different recommendation (the item 5 exposure). Ask whether MT3 stops, waits or runs too.

5. HIGH. Sections 4.4 and D3. The interplay with Slow trades is not in dollars. Slow trades hold up to 2 x $25,000 overnight in LRCX, KLAC, MU, AMD and GS (`config.py:81`), which are mostly chip stocks tied to NVDA. On the worst test night (Fri 2024-08-02 to Mon 08-05), MU opened 5.6% lower and AMD 7.8% lower. Two slots would lose about $3,350 on top of the overnight holds. That makes negative $6,390 at $5k, negative $9,430 at $10k (19% of the account) and negative $13,690 at $17k (27.5%), before the sale day's own negative $1,242 limit. D3's "1x" option means zero overnight buys on every night both Slow slots are full ($50,000 is at least the $49.7k equity). That is a skip the research never had, and it breaks ruling 2. D3's "2x" means borrowing up to about $99,400 overnight. At $17k with Slow full, HUT shrinks to about $15,400. "Counting swing buys staged for the morning" double counts, because those buy at the 09:30 open, the same moment the holds sell. Fix. Put these dollars in D3, say "borrowed" in plain words, drop the staged-buy counting, and add an X row "buy shrunk or skipped for lack of room".

6. HIGH. Section 5 item 6. The stale-string list is wrong in both directions. It misses these:
   - `SafetyCard.tsx:59-61`, "If it ever loses $X in a day, it stops for the day on its own". This becomes false when D7 is "no". MT3 accepted the label "day trades only".
   - `SafetyCard.tsx:74`, "Small bets… about 1% of the account at risk". A stopless $10,000 IREN hold lost $2,745 (5.5%) in one night.
   - `SafetyCard.tsx:79`, "Every trade has an exit plan. A price where it gives up, set before it buys".
   - `page.tsx:166` and `plain.ts:325-327`, "Stopped for today". This is false when D6 still buys at 4 PM.
   - `plain.ts:330` "It starts again at 9:30 AM" on a Friday with holds, and `plain.ts:350` "Holding N trades" counting holds.
   - `README.md:39,286`, "Zero Overnight".

   It also lists strings that are not stale. `page.tsx:171` is a Slow trades notice. `SegmentedModeToggle.tsx:29,39` stay true if holds get their own group. Fix. Replace the list with a grep-backed table and assert it in a page test.

7. HIGH. Sections 5 and S21. HoldingNow would show every hold wrongly. `page.tsx:68-71` counts anything not a swing symbol as a Quick trade. `HoldingNow.tsx:30-35` maps an unknown strategy id to "Manual trade · Not linked to a strategy". `HoldingNow.tsx:172` shows "No safety exit set". "Sell now" (`:149`) hits flatten, which S10 skips, so the button reads "Didn't go through". "Move safety exit to my entry price" is enabled when stop is null (`:79-81`) and silently no-ops (`bracket.py:569-570`). `HoldingNow.tsx:194` "Sells by 9:30 AM at the latest" prints no day (`etTimeLabel`, `plain.ts:235-238`), so a Friday says 9:30 AM, not Monday. The Safety card's "Close all quick trades now" is enabled at night with only holds and fails. Fix. Name HoldingNow, SafetyCard and rightNowSentence in section 5. Keep holds out of `intradayPositions`. Hide both buttons on holds, and refuse TIGHTEN_STOP with plain text like tri and ORB do (`main.py:3966-3972`). Show the day on every sale time.

8. HIGH. Section 5 against MT3 section 5 and findings 23 to 27. The plan claims to reuse MT3's attacked findings but drops ones this operator already accepted. The dropped ones are the worst tested night in dollars on the page, the loss limit label, the chip "$X a night, no stop", "Up so far / Down so far, never Making money", and results split into day trades and overnight holds. It also drops the states for robot down with holds, a late or partial sale, a needs-look card, a weekend or long weekend, the first day before the first buy, and phone height budgets. On a bad morning the Balance card will show negative $6,080 "today" while the Safety card shows the limit almost unused. Fix. Copy MT3 items 2, 3, 6, 7, 8, 9, 11, 12, 13 and 14 as the mockup state list, and add a frontend verify script to section 7, like `verify_holding_mood.py`. Right now section 7 checks only "frontend build green".

9. HIGH. The In plain words section. It is not yet honest or readable for this operator.
   - It has no dollar amounts, while MT3's did.
   - It uses jargon like "q .149 against .10", "correlation 0.77" and "market on close and market on open orders".
   - It states D4's auction orders as fact before T13 or Q1 are answered.
   - It says the stocks rose "a lot" when they went up 5.6x (NVDA), 10x (IREN) and 7x (HUT), against 2.1x for QQQ.
   - It omits that 5 of 23 ideas passed where about 1 would by luck (MEMORY).
   - It omits that 12 of 36 months lost money (worst month negative $5,114 at $10k).
   - It omits that the worst drop took 11 weeks to reach and about 5 months to recover.
   - It omits that on the worst night all three lost together.
   - It omits that day strategies cannot trade NVDA from 3:45 PM until the sale books.

   Fix. Rewrite it with those facts in plain sentences and the D2 amount's dollars filled in.

10. MEDIUM. Sections 3 and 8 step 7, weekends and holidays. Weekends and holidays are twice as risky. There were 160 of 742 such nights. The worst weekend night was negative $6,080 against negative $2,971 for the worst weeknight at $10k. The 1-in-100 night was negative $3,203 against negative $1,531. 5 of the 10 worst nights were weekends. The first live night could be a 65-hour Friday hold before the sale path has ever run. Fix. This is a scheduling choice, not a trial. Make the first live buy a Monday to Thursday close, so the 09:30 sale is proven within 17.5 hours. Have the row text say "over the weekend, sells Monday 9:30 AM".

11. MEDIUM. Section 11 structure. D6 is two decisions (pause, loss stop), so split it. D4 depends on T13, so ask it only if T13 fails. D8 (deploy any time except 15:40 to 16:10 and 08:55 to 09:40) contradicts MT3's decided D5 (16:10 to 08:30 plus weekends, only after health shows the buys booked) without flagging it. D8 also measures the window by push time, not container swap time. X4 "on before 15:49" conflicts with section 3, where cancel works until 15:49:30, and with MT3's "at any point 15:30 to 15:49:59". There is also no decision on what "close all" means for holds when the operator wants out (S10 only rewords the reply). Fix. Ask one word each, with the dollars, in this order: D1, D2, D3, pause, loss stop, D7, D8, close-all meaning. Ask D4 after T13.

12. MEDIUM. Sections 4.5 and T9, split mornings. If Alpaca applies a split before 09:00, the 30-second compare sees 43 shares against 430. The page then shows "positions don't match… New trades are paused" (`page.tsx:152-156`) overnight until the 09:00 fix. T9 has only a forward 10 for 1 case, while MT3 T5 also tested HUT's reverse ratio. Fix. Have the overnight controller explain share-count differences on held symbols before the compare (extend S15), give the page a plain split sentence, and add a reverse split fixture.

13. MEDIUM. Section 7 T13. The smoke test can leave an orphan. If the cancel of the 1-share MOC fails or lags, Alpaca refuses changes after 15:50 and it fills. That leaves an unknown NVDA share, which raises a broker mismatch and pauses entries for every arm the next morning. Fix. Send at 15:30, confirm it is cancelled by 15:40, and state the manual cleanup in the plan.

14. MEDIUM. Section 5 item 5. The "committed summary file" will not be in the image. The Dockerfile copies only `backend`, `tests`, `scripts`, two docs and the README (`Dockerfile:18-24`), not `research/`. This is the same trap as ERRORS 2026-09-26. Fix. Put the summary under `backend/` or add a COPY, and test the startup read from a Dockerfile-shaped temp dir.

15. MEDIUM. Sections 1 and X. A buy shrunk for buying power or the $25,000 cap, and an MOC refused for buying power (Q4), are rule departures that are not declared in the X table. Fix. Add X8 and log each case with the research return.

16. LOW. Section 9. Earnings nights are out of scope, but a fixed page note is an allowed mitigation that does not filter anything. The note would be "Earnings nights are held too, by rule. Your broker app may show a big after hours move, and this page shows the buy price until 9:30 AM." Add it to the row text.

17. LOW. Style against `/Users/jhoshua/AGENTS.md`. There are colons in prose at plan lines 67, 151 and 163. The plain words use jargon (see item 9). I found no dashes as punctuation and no bold lead-ins. The header brand "Day Trader" (`Header.tsx:83`) and the day-only hours bar are minor wording gaps.

Two things I could not verify. My negative $14,958 worst drop at $10k differs from MT3's negative $13,449. It is likely a whole-share or method difference, so the plan should state one method. I also did not check live equity, which I took as $49.7k from the brief.

Verdict: not ready to put to the operator. Fix items 1 to 9 first (missing pause control, missed-sale path, decisions with no dollars, silent MT3 reversals, combined overnight exposure, and the page telling the truth), then re-attack.