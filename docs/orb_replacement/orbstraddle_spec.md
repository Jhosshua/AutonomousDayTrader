# ORBStraddle: live ORB trading spec (as deployed 2026-09-28)

Repo `/Users/mo/ORBStraddle`, HEAD `71b001f`. Live: Railway service `ORBStraddle`, Alpaca **paper** `PA3RPSMUR65S` (~$72k equity), `rules_version = adaptive-v1.6.0-flow-rules`.
Scope: this is how the code trades today. It is not a claim that the strategy has edge. Citations are `file:line`.

Despite the name, it trades **equity stock brackets only**. Options are hard-disabled (`config.py:316 OPTIONS_TRADING_ENABLED = False`), and the scanner excludes all funds, including SPY and QQQ.

---

## 0. Live configuration (Railway `railway variables --kv`, secrets masked)

| Var | Live | Code default | Note |
|---|---|---|---|
| ORBS_MODE | live | live | with paper base = real Alpaca paper |
| ORBS_TRADE_BASE | https://paper-api.alpaca.markets | same | |
| ORBS_ACCOUNT_ALLOWLIST | PA3RPSMUR65S | "" | |
| ORBS_DECIDER | adaptive | adaptive | |
| ORBS_FREEZE | 09:38 | 09:38 | |
| ORBS_RISK_PCT | 2.0 | 2.0 | runtime.env can override it (config.py:22). /api/state shows 2.0 |
| ORBS_AUTOPILOT | **no** | "" | **Overridden** by `/app/state/runtime.env` (dashboard arm). Live /api/state: `autopilot: true, armed: true`. `auditor.autopilot_on()` reads ONLY runtime.env (auditor.py:25-47) |
| ORBS_DELTA_RULE / VELOCITY_RULE / MACRO_RULE / ABSORPTION_EXIT | **on** (all four) | **off** | code default off because sibling bots share the files (config.py:96-108) |
| ORBS_CANDLE_RULE | unset → **on** | on | config.py:92 |
| ORBS_STREAM | shadow | shadow | the websocket board is built but NEVER traded; REST builds the traded board |
| ORBS_THESIS_GATE | no | yes | gate disabled, so GO with no network call (core.py:1441-1450) |
| ORBS_COID_PREFIX / OPT | orba / orbo | jsl / jslo | |
| ORBS_CARD_SOURCE | file | file | |
| ORBS_BOT_NAME | ORBStraddle | JhoshuaStraddleLarge | relay User-Agent |
| ORBS_RELAY_BASE / WS | alpacarelay-production …/data, wss://… | same | |
| Not set, so the code defaults apply | MAX_OPEN_SLOTS 4, MAX_STRUCTURE_PICKS 2, MAX_DAY_RISK_PCT **2.5**, MAX_GROSS 300%, MAX_BUYING_POWER_PCT 60, MAX_NAME_NOTIONAL_PCT 150, DAILY_LOSS_HALT_PCT 3.0, MAX_SLIP_STOP_FRAC 0.33, AUTOPILOT_CUTOFF 10:15, wave times, LONG_ONLY "" (shorts allowed), INCREMENTAL on | | |

Live `/api/state` (13:28 ET 09-28) confirms: `risk_pct 2.0`, `max_slots 4`, `day_risk_budget.max_risk_pct 2.5`, `cutoff 10:15`, `flatten 11:00`, `thesis_gate disabled`.

**Flag:** config.py:218-224 says a `.env` value of 8.0 for MAX_DAY_RISK_PCT existed historically. On Railway it is unset, so **2.5% binds**: pick 1 gets 2.0%, pick 2 is cut to 0.5%, and after that "day risk budget spent" for the rest of the day.

---

## 1. Daily timeline (ET). The scheduler loop ticks every 10 s (app.py:123-392)

Runs only on weekdays when `session_calendar.is_session(day) is True` (app.py:175, calendar via relay `/metadata/calendar`). An unverifiable calendar means no session.

| Time | Action | Cite |
|---|---|---|
| every tick | JSL_KILL check, ownership snapshot, ensure the book's positions are supervised | app.py:151-171 |
| 08:00 | `morning_sweep`: cancel own leftover orders, promote stale supervision to orphans, `exit_own` any carried own position | app.py:180, core.py:2586 |
| 09:00/09:20/09:34 | thesis gate checks (no-op live, gate disabled) | app.py:190-196 |
| 09:15–09:29 | **freeze session sizing** (day-start equity plus buying power), retried every 60 s | app.py:214-223, core.py:960 |
| 09:15–09:29 | paper day-equity baseline capture if `last_equity` is 0 | app.py:227-243, core.py:1921 |
| ≥09:15 | `scanner.prep`: universe, prior closes, ATR | app.py:243-249, scanner.py:804 |
| 09:25 | tick-stream websocket connects (shadow only) | app.py:270-282, config.py:325 |
| 09:30–09:35 | opening range forms from trade prints | orbproc.py:241-247 |
| 09:35:05 | earliest possible trigger (delay gate) | orbproc.py:43, 300 |
| 09:36:10 | preview scan to 09:36 (display, warms incremental state) | app.py:283-289 |
| **09:38:30** | **final primary scan**, window 09:30:00 to 09:38:00. Coverage must be ≥90%, else one whole-board REST retry. Snapshot committed, then the adaptive decider is kicked immediately | app.py:290-322, 114-121 |
| ~09:39 | primary decision, then `core.execute` (execution window opens at FREEZE 09:38) | auditor.py:390-560, core.py:1725 |
| 09:45:00–10:15:00 | **secondary wave**: every 60 s, if slots > 0 and autopilot on, `scanner.run_secondary` (triggers ≥09:45:00, end = current HH:MM) and kick the decider if the board has cards | app.py:359-375, config.py:48-50 |
| 10:15 | **entry cutoff**. `auditor._past_cutoff` (≥10:15:00) and `session_ok` (>10:15:00) | auditor.py:622, core.py:1725-1743 |
| continuous | supervisor loop every 5 s while positions are open | core.py:3162-3640 |
| **11:00** | **time flatten**: market exit of own qty | config.py:228, core.py:3503 |
| 11:05 / 13:05 | card shadow measurement (no trading) | app.py:195-203 |
| 15:30–16:00 | own-book flat check every 30 s; alarm if still open at 15:45; supervisor escalation window at 15:45 | app.py:207-212, core.py:2807, config.py:212 |

Primary wave label 09:35:05–09:45, secondary 09:45–10:15 (config.py:46-49, core.py:1657). The primary **decision** actually happens once, right after the 09:38:30 scan.

---

## 2. Universe / scanner

**Watchlist** (`scanner.watchlist`, scanner.py:369-411), built once per day at prep:
- Assets from relay `/metadata/assets?status=active&asset_class=us_equity`. Keep a symbol if `tradable`, it matches `^[A-Z]{1,5}$`, its exchange is in {NYSE, NASDAQ, AMEX, ARCA, BATS}, and its **name does not match** the fund regex `\bETF\b|\bETN\b|\bFund\b|\bTrust\b|\bIndex\b|iShares|SPDR|Vanguard|ProShares|Direxion|Invesco (QQQ|S&P)|WisdomTree|Global X|VanEck|Grayscale|Bitwise|Select Sector` (scanner.py:384-387).
- Prior session = last calendar session before today (35-day calendar lookback, at least 15 sessions required).
- Daily bars `/v2/stocks/bars timeframe=1Day feed=sip adjustment=raw`, 10 calendar days, 200 symbols per batch, 8 threads.
- Score = **prior-session close × prior-session volume** (one day, not an average). Keep the **top 250** (scanner.py:411).
- No price floor, no market-cap filter, no premarket filter. Every quality filter comes later (card gates and the adaptive decider).

**Reference data** (`_prepare`, scanner.py:815-850):
- `prev_close` = prior-session close (raw, unadjusted).
- `prev_dv` (the "adv" used by RVOL) = prior-session close × volume.
- **ATR** = simple **mean of 14 true ranges** over the 15 most recent sessions (bars from 25 calendar days, all 15 expected sessions must be present). TR = max(H−L, |H−prevC|, |L−prevC|). Not Wilder. Missing ATR makes the symbol ERROR (refused, no fallback).

**Intraday data**: per symbol, SIP **trades** and **quotes** from relay `/v2/stocks/{sym}/trades|quotes`, `feed=sip, sort=asc, limit=10000`, paginated. Window 09:30 up to scan end (half-open). Scan concurrency 24, overall scan deadline 240 s, 3 page attempts (scanner.py:38-44). Incremental mode re-fetches the last 120 s each scan and does a full 09:30 reconcile every 300 s (orbproc.py:787-880). Failed symbols get one retry at concurrency 4.

**Row filters** (orbproc.py:226-266):
- Trades with any condition in `BAD={W,4,B,C,T}` are dropped entirely.
- Trades with a condition in `NOT_PRICE={I,V,7,P,U,Z,H,M,N}` (odd lots and similar) **count for volume, RVOL, velocity and delta** but **never** for price (range, trigger, freeze price).
- Quotes need bp, ap, bs, as all positive and bp < ap.

**Per-symbol quality to produce a card** (orbproc.py:369-400): ≥5 trades, ≥20 quotes, newest trade and newest quote each ≤120 s before scan end, prior close and prev_dv present, ATR present. Failing any of these = ERROR (counts against coverage).

**Board coverage**: ok/attempted must be ≥0.90 (config.py:226, app.py:114-121), else the scan fails and no decision is made.

**Wick-trap drop** (orbproc.py:405-414, signals.py:192-216): WBR = (H−L) / max(|C−O|, 0.0005×O, 1e-6) over the opening range. If **WBR > 3.5**, the symbol produces **no card at all** (it is removed from the board and so also from the regime's count). The session lockout part only applies to SPY/QQQ, which are excluded.

RVOL, spread, ATR room and similar are **not** scanner filters. They are enforced by the decider (§5).

---

## 3. Opening range

- Built from **price-eligible trade prints** (not bars) with timestamp in [09:30:00, 09:35:00) (orbproc.py:241-247).
- `H_ORB` = max print, `L_ORB` = min print, `o_orb` = first print, `c_orb` = last print before 09:35. Unrounded values are used for break tests. Cards also show them rounded to 2 dp.
- `vol5` / `dv5` = shares and dollars of **all kept trades** (odd lots included) before 09:35.
- Opening candle colour: green if c_orb > o_orb, red if <, flat if == (signals.py:680-691).
- Gap % = (o_orb / prev_close − 1) × 100 (orbproc.py:457).

---

## 4. Breakout / trigger definition

The event stream is trades plus quotes merged in exact nanosecond order, with **trades before quotes on ties** (orbproc.py:128-136). Each quote updates `prev` (the prevailing quote), the spread, and the SOFI windows. Each trade updates velocity and delta first, and only then (if price-eligible and ts ≥ 09:35:00) is it tested (orbproc.py:284-367):

1. **Direction**: print `p > H_ORB` means long, `p < L_ORB` means short, otherwise nothing. There is **no buffer**: a strict one-tick print beyond the range counts.
2. **Old gates (the "board" gates)**. All must pass on that print:
   - delay: ts ≥ **09:35:05**
   - freshness: prevailing quote age at the print between 0 and **5000 ms**
   - depth: prevailing quote bid px×size ≥ **$25,000** AND ask px×size ≥ **$25,000**
   - SOFI: "deep" order-flow-imbalance z-score, **z ≥ +2.0 for a long, ≤ −2.0 for a short**. Computation: on each quote, OFI = Cont/Kukanov/Stoikov e(n) = ΔbidDemand − ΔaskSupply against the previous quote (scanner.py:413-422), divided by the average displayed size (bs+as)/2. Only quotes with both sides ≥ $25k notional go into the deep rolling window of the last **100** values. z = (last − mean) / population std, and at least 10 values are required (orbproc.py:71-86, 268-282).
3. **Latches.** Each is the first print that satisfies its condition. Once set, it never changes (orbproc.py:343-367):
   - `trig_primary`: first print passing the old gates (any direction). This is the **board direction** the regime counts.
   - `trig_secondary`: same, but only prints with ts ≥ 09:45:00.
   - `*_candle`: first old-gate pass in the direction the candle allows.
   - `*_flow`: first old-gate pass that also passes flow rules 1 and 2 AND (while the candle rule is on) matches the candle direction. **This is the break that gets traded.**
4. **Card resolution** (orbproc.py:418-440): start with `trig`. If the candle rule is on, use the candle latch, or mark `candle_blocked`. If the flow rules are on, use the flow latch (clearing `candle_blocked`), or mark `flow_blocked`. Blocked cards **stay on the board** (the regime counts their first-break direction) but are refused at the pick step and in execute().
5. **Candle rule (ON)**: green allows long only, red allows short only, flat allows nothing (signals.py:693-717 `candle_refusal`, applied again in execute core.py:2240).
6. **Flow rule 1, delta (ON)** (flow.py:37-110, config.py:109): cumulative aggressor shares **since 09:30** up to and including the breaking print. Classification uses the quote rule against the prevailing quote (≥ask buy, ≤bid sell, above/below mid), then the tick rule at the exact mid. Prints excluded from delta: `{V,7,P,U,Z,H,M,N,O,Q,5,6,X,L,G,9}`. Odd lots **are** counted. ratio = (buy−sell)/(buy+sell). Long needs **≥ +0.05**, short **≤ −0.05**.
7. **Flow rule 2, velocity (ON)** (flow.py:113-209): trades are counted per epoch second (all kept trades, odd lots included). burst = trades in the 2 s ending at the print's second. base = trades in the 60 s before that. ratio = (burst/2)/(base/60). Pass = burst ≥ **5** AND (ratio ≥ **2.0** OR base = 0). It is judged per **excursion**: the verdict is measured during the first 2 s after the first print beyond the fence and then frozen. A print back inside the range resets it. So a later burst in the same excursion cannot rescue a slow crossing.
8. **Macro veto (rule 3, ON)**: applied at the pick step, not in the scanner (§5).
9. First-break rules: **one card per symbol per window**. The primary card uses the first qualifying break in 09:35:05–09:38:00. The secondary card uses the first qualifying break at/after 09:45:00. It can be a continuation of an earlier break, since any print beyond the fence counts. The latched trigger print price becomes `entry`.

**Card fields used downstream**: `entry` (trigger print, rounded 2dp), `stop`, `drift_pct` = (last price-eligible print at scan end − entry)/entry×100, signed toward the trade; `rvol` = dv5 / (prev_dv × 5/390); `atr_pct` = ATR/entry×100; `spread_bps` = the latest quote spread at scan end (not at the trigger); `sofi` (deep z at trigger); `gap_pct`; candle and flow fields (orbproc.py:442-575).

---

## 5. Pick selection: adaptive decider (`adaptive.py`, v1.6.0)

Called on the frozen primary board at ~09:38:30, and on each new secondary board (auditor.py:480-496). **It always uses FREEZE 09:38 as its time reference**, even in the secondary wave: SPY/QQQ slopes come from 09:30–09:38, and the news window is 49 h before 09:38 (adaptive.py:809-812, 283, 211).

**A. Regime (hard sit-out)** (adaptive.py:319-386):
- SPY and QQQ 1-min SIP bars 09:30–09:38 (relay `/v2/stocks/bars`, timeout 3 s). **Every** minute 09:30…09:37 must have positive vw and v. Otherwise SIT_OUT_CASH.
- Slope (bps/min) = (VWAP over the window − first-minute vw) / first vw × 1e4 / 7. Used **only** in utility (below). The bull/bear slope thresholds in the config are dead constants (adaptive.py:59-61, unused).
- Board short fraction over **all** cards on that board (first-break direction, including blocked cards). It must be **0.25 ≤ short_frac ≤ 0.75**, else ONE_SIDED, SIT_OUT_CASH. This applies separately to the primary board and to each secondary board.
- News: relay `/v1beta1/news`, 40 symbols per batch, 50 per page, 40-page cap, 30 s budget. Any failure means SIT_OUT_CASH.

**B. Per-card filters, in order** (adaptive.py:583-713):
1. Long-only skip (not live).
2. Candle rule refusal.
3. Flow rule 1/2 refusal (`flow_refusal`, flow.py:222-252).
4. **Macro veto**: 1-min bars since 09:30 for SPY plus the stock's sector ETF from `SECTOR_MAP` (adaptive.py:16-50; unmapped symbols get SPY only). move = last complete bar close / 09:30 bar open − 1. The 09:30 bar must exist, and the last bar must have ended ≤ 180 s ago. Trend: > +0.05% up, < −0.05% down, else flat. **No long if any of them is down, no short if any is up.** Missing data refuses. Read timeout 3 s (flow.py:282-395, config.py:114-116).
5. Dilution (T3 offering headline ≤48 h) vetoes longs.
6. **Microstructure** (`evaluate_microstructure`, adaptive.py:407-452):
   - spread_bps present and ≤ **8.0**
   - stop distance |entry−stop|/entry between **0.5% and 7.5%**
   - drift / stop-distance% ≤ **0.25**
   - RVOL ≥ **2.2**
   - ATR% ≥ **2.0**
   - SOFI present
7. **Tiering**:
   - *earnings*: a T1 headline ≤48 h that is beat with a long, or miss with a short, or a beat taken short with gap ≥ **+3.0%** ("reprice"). drift must be > −0.5%.
   - Otherwise *structure*. A structure pick needs EITHER an aligned T2 (contract/FDA, long) or T4 (guidance raised long / lowered short) catalyst, OR the **failed-gap** conditions: drift_r ≥ 0 (price still beyond the trigger), gap **against** the trade ≥ **0.8%** (a long needs gap ≤ −0.8%, a short needs gap ≥ +0.8%), and the symbol not in MEGA {AAPL, MSFT, NVDA, GOOGL, GOOG, AMZN, META, TSLA, AVGO, TSM, BRK.B}.
   - **In practice the no-news bread and butter is a failed-gap reversal ORB**: the stock gaps one way, then the range breaks the other way.

**C. Utility score** (adaptive.py:458-526). The weights are hand-set and uncalibrated:
- z_tps = 0.6(rvol−2) + 0.4(|sofi|−2) + 2
- aligned_cat = catalyst score signed toward the trade (reprice: 0.5×|score|). Scores: beat 2, miss −2, ±1 for guidance, contract 1.5, guidance ±1.
- slope_align = +0.3 if the SPY slope sign matches the direction, −0.4 if opposite, 0 if the slope is exactly 0.
- mu = 0.15 + 0.35·aligned_cat + 0.25(z_tps−2) + 0.15(rvol−2.5) − 0.5·max(0, drift_r) + 0.2·slope_align
- σ² = 0.32 without a catalyst, 0.24 with one. z = mu/√(1+π/8·σ²). p = logistic(z), clamped to [0.40, 0.85].
- EV_R = 0.984p − 0.264. EV$ = EV_R × 1000. utility = EV_R − 0.35σ − 0.5·(0.09(1−p)).
- **Pick 1**: the highest-utility candidate with EV$ ≥ **250**, p ≥ **0.54**, utility > 0. The p ≥ 0.54 floor is what actually binds (it implies EV ≈ $267). For a no-catalyst card that means mu ≥ ~0.170.
- **Pick 2**: the next eligible candidate in a **different, mapped** sector (neither sector may be "OTHER"), with EV$ ≥ **350** (p ≥ ~0.624) and utility > 0.
- At most **2 picks per decision** (adaptive.py:80).

**D. Validator** (core.py:334-445): ≤4 picks, ≤2 structure picks, symbols must be on the frozen board, direction must match the card, and the audit row must say survives=true and case_against=weak. Earnings picks are re-checked (hours_ago 0–48, drift > −0.5, reprice gap ≥ 3%).

**Wave and dedupe rules**:
- The primary runs once per day (auditor.py:396-398).
- A secondary run happens only when the board contains a symbol **not yet judged** in the secondary wave today. It then re-judges the whole current secondary board (auditor.py:399-412).
- The secondary board excludes symbols currently supervised or already executed today (scanner.py:1082-1093).
- Execution is refused when `already_executed()`, which means slots are full OR the day-risk budget is spent (core.py:1619-1630).
- Concurrency: **4 open slots**, of which at most **2 structure-tier positions** (core.py:2227-2238, config.py:39-41).
- A late trigger can trade: on 09-28, APP's card trigger was 09:45:05 and it traded at 10:08, after earlier secondary runs had declined it. The card is fixed, but drift is recomputed at each scan.

---

## 6. Entry order (`core.execute`, core.py:2059-2566; orders.py:35-80, 368-401)

Pre-checks (in order): capacity, supervision.json readable, JSL_KILL, daily-halt latch, own book healthy, no legacy positions, thesis gate (off), slots, no manual "flatten_all" today, `session_ok` (09:38 ≤ now < 11:00, and ≤ 10:15:00), `verify_destination` (endpoint and account allowlist), account read (equity must not be estimated), occupied symbols (account positions + open orders + supervised + own book: **skip any symbol the account already holds or has an order on**), frozen sizing, own-P&L halt check, known baseline.

Per pick: occupied check, then the candle and flow re-check, then a **fresh macro veto** (at planning time), then the fresh price, then sizing (§8).
- **Fresh price** = relay `/v2/stocks/{sym}/trades/latest?feed=sip`. It must be positive, and the print age must be between −5 and +60 s (core.py:1828-1852).
- **No-chase cap**: |px − card.entry| / |card.entry − card.stop| ≤ **0.33** (both directions; env `ORBS_MAX_SLIP_STOP_FRAC`) (core.py:2327-2333).
- The stop must still be on the correct side of px, and |px − stop| ≥ 0.5% of px.
- In autopilot (`strict=True`), a pick that cannot be priced aborts the **whole batch**.

Order:
- Intent row written to the ledger BEFORE submission, and supervision saved.
- Immediately before each POST: re-read positions and open orders (skip if the symbol is now occupied), re-run the **macro veto** again (`late_macro_refused`), and record the coid in the own book.
- Order details:
  - Alpaca **bracket**, parent **`type: market`**, **`time_in_force: day`**, `qty` whole shares, `side` buy (long) / sell (short).
  - `take_profit.limit_price` = target, `stop_loss.stop_price` = stop (2 dp).
  - The stop leg is a plain stop (market-on-trigger), not stop-limit.
- **Client order id**: `{prefix}-{SYMBOL}-{YYYY-MM-DD}-w{seq}-a{attempt}-{6 hex}`, for example `orba-APP-2026-09-28-w1-a1-db3d12`. Here `w` is the per-symbol execution sequence for the day (not the wave number, core.py:2375), and `a` is an in-memory per-symbol attempt counter (core.py:1650-1690).
- **Retries / ambiguity**: there is no resubmission. On a transport error, the bot looks up by coid 3× with a 2 s gap (orders.py:232-261). An explicit 4xx (not 429) means rejected. Anything else means **UNKNOWN**: the symbol is kept in supervision as PENDING_FILL and resolved later by coid.
- An immediate partial fill: the bracket children are PATCHed to the filled qty (orders.py:382-385). The supervisor also resizes children to own filled qty (core.py:3478-3493).

---

## 7. Stop and target

- **Stop (structural, from the card)**, computed in orbproc.py:458-468:
  - long: `min(L_ORB − 0.25×ATR, entry×0.995)`
  - short: `max(H_ORB + 0.25×ATR, entry×1.005)`
  - `entry` here is the trigger print. The stop is rounded to 2 dp on the card and again at execution.
- **Risk distance** `rd = |px_fresh − stop|`. It is recomputed at execution from the fresh price, not the trigger.
- **Target** = `px_fresh ± 0.75 × rd` (TARGET_R 0.75, core.py:2371). It sits on the bracket as a limit order.
- Worked check, APP 09-28 short: H_ORB 322.738 + 0.25×15.60 = 326.64 stop. px 313.03, rd 13.61, target 302.82. This matches the ledger.
- **Supervisor R-exits** (config.py:227, core.py:3509-3525). They use the Alpaca position `current_price` every 5 s. R = (px − own avg fill)/rd, signed toward the trade.
  - **Breakeven**: once R ≥ +0.75, try to PATCH the stop leg to the avg entry. Whether or not the PATCH works, if R later falls to ≤ 0 the bot exits "breakeven". Uncertainty: paper QA logged PATCH on a held leg as 422 (MEMORY 09-21), so the broker-side BE stop may never take and the supervisor exit is the real mechanism. It rarely matters, because the +0.75R target limit is usually hit first.
  - **Clawback trail**: peak R ≥ **0.60** AND R ≤ peak − **0.25** AND R > 0 exits "clawback".
  - **Fast-fail**: R ≤ **−0.40** exits "fast-fail". This is a software stop well inside the −1R bracket stop.
- No partial profit-taking. No ATR trailing stop.

---

## 8. Sizing (core.py:2150-2360)

- **Equity** = the frozen day-start baseline, which is Alpaca `last_equity` (yesterday's close equity), or on paper the verified 09:15–09:29 pre-open equity when last_equity is 0. It is frozen once per day with **buying power** in `session_sizing.json` (core.py:944-987). If the baseline changes, it is re-frozen. Without it, no trade.
- `risk$ = equity × RISK_PCT/100` = **2.0%**. `shares = floor(risk$ / rd)`.
- Per-name notional cap: shares ≤ floor(1.50 × equity / px) (MAX_NAME_NOTIONAL_PCT 150).
- **Day risk budget**: Σ accepted `risk_$` today ≤ **2.5% × equity** (MAX_DAY_RISK_PCT). Shares are cut to fit the remainder. Budget used counts **every accepted order**, and closed trades do not give it back (core.py:1546-1600).
- **Gross cap**: current plus planned notional ≤ min(3.0 × equity, **60%** × frozen buying power) (core.py:2203-2209). Shares are cut to fit.
- Live buying-power check: if the live BP left cannot fund the planned notional, the order is **refused, never shrunk** (core.py:2349-2359).
- Whole shares only, floor rounding everywhere.
- Net effect live: the first trade of the day risks 2% of equity. A second (same decision or later) risks at most 0.5%. After that nothing trades until tomorrow.

---

## 9. Exits

Every exit goes through `exit_own` (core.py:3926-4228):
1. Cancel the bot's own orders on the symbol (the bracket legs), and confirm they are final.
2. Read own qty from the own book.
3. Send one **market, day** order for exactly the own qty, coid `{prefix}-X-{sym}-{date}-{n}`, capped to the account net position.
4. Confirm by the own book.

It never uses `DELETE /v2/positions`.

Exit priority in the supervisor (core.py:3587-3591): daily-loss halt, then 11:00 flatten, then carried/no-known-stop, then breakeven, then clawback, then absorption, then fast-fail. Broker-side bracket exits (the TP limit and the −1R stop) run independently at Alpaca. The supervisor treats the symbol as gone once the own book proves it flat.

- **11:00 time flatten** (`FLATTEN_ET`, not env-overridable).
- **Absorption exit (rule 4, ON)** (flow.py:462-586, core.py:3561-3586):
  - Arms once peak R ≥ **0.5**.
  - A background thread reads the last **30 s** of trades (quotes from 35 s back): at most 3 pages, 2 s per page, 4 s total budget.
  - Delta is computed with the same classifier as rule 1.
  - It fires if R is still ≥ 0.5, there are ≥ **20** classified trades, and delta ≤ **−0.30** for a long (≥ +0.30 for a short).
  - The result must be ≤ 8 s old. It acts on the pass after the read (~5 s later). The supervisor never waits on it.
- **Escalation**: if a flatten is stuck for ≥ 120 s, or it is past 15:45, the bot retries `close_position(ours_only)` with a longer confirm. It never touches other actors' orders.
- **Carried positions** (from a prior day, or rebuilt without a stop) exit at the next allowed moment. The 08:00 sweep does the same.
- **Manual flatten_all** blocks new entries for the rest of the day.

---

## 10. Risk controls and kill switches

- **Daily loss halt**: this bot's **own** P&L (realized plus unrealized from own fills; realized only if a price is bad) ≤ **−3.0%** of frozen day-start equity.
  - Checked before each execute and every 5 s in the supervisor.
  - **Latched** for the day (`daily_halt.json` plus a `halt_intraday` ledger row). It cancels all own orders and flattens everything.
  - Account-level loss (including manual trades) is an alarm only (core.py:849-925, 3190-3215).
- Autopilot switch: `/app/state/runtime.env` `ORBS_AUTOPILOT=yes`, re-read on every check. A missing or unreadable file means OFF.
- The armed destination fingerprint (endpoint|account) must match (auditor.py:459-465).
- `JSL_KILL=yes` env: flatten plus cancel every tick, no entries (core.py:1487-1515).
- Fail-closed refusals:
  - unreadable supervision.json, broken own book, or unreadable orphans file
  - leftover orphans from a prior day
  - unknown own P&L or missing baseline
  - scan coverage < 90%
  - missing SPY/QQQ minutes, incomplete news, or missing macro bars (the pick is refused)
  - stale price (> 60 s), or a missing candle/flow field on a card
- Request budget: 100 req/min for the main lane plus a 50/min reserve used only by exits (config.py:300-302, core.py:447-520).
- Unused/dead: `HALT_SANITY_PCT` is defined but not referenced. The SPY slope thresholds and `MIN_BREADTH_EXPANSION` are unused.

---

## 11. Data dependencies (all through AlpacaRelay, `X-Relay-Token`)

| Need | Endpoint | When |
|---|---|---|
| Calendar | `/metadata/calendar` | daily, prep |
| Assets | `/metadata/assets?status=active&asset_class=us_equity` | prep |
| Daily bars (dollar volume, close, ATR) | `/data/v2/stocks/bars?timeframe=1Day&feed=sip&adjustment=raw` multi-symbol | prep |
| Trades + quotes, 250 names | `/data/v2/stocks/{sym}/trades`, `/quotes` `feed=sip&sort=asc&limit=10000` | 09:36, 09:38, every minute 09:45–10:15 (incremental) |
| SPY/QQQ 1-min bars 09:30–09:38 | `/data/v2/stocks/bars?symbols=SPY,QQQ&timeframe=1Min&feed=sip` | each decision |
| SPY + sector ETF 1-min bars since 09:30 (XLK, XLC, XLY, XLF, XLV, XLI, XLE, XLB) | same | pick step, plan step, right before each order |
| News | `/data/v1beta1/news` (48 h window) | each decision |
| Execution price | `/data/v2/stocks/{sym}/trades/latest?feed=sip` | execute |
| Absorption tape | trades/quotes of the last 30–35 s | while in a trade after +0.5R |
| Websocket ticks | relay WS | shadow only (not traded) |

Broker (Alpaca paper API, direct with keys): `/v2/account`, `/v2/positions`, `/v2/orders` (POST bracket or market, GET open, `:by_client_order_id`, PATCH legs, DELETE own).

---

## 12. Short-side specifics

Shorts are enabled (`ORBS_LONG_ONLY` unset; `long_only()` returns False, core.py:1788-1803). Nothing checks locate or hard-to-borrow; a broker reject simply fails that order.

A short needs all of the following:
- a print < L_ORB
- SOFI deep z ≤ −2
- a **red** opening candle
- delta ≤ −0.05
- macro: neither SPY nor the sector ETF is **up** more than 0.05%
- failed-gap: gap ≥ **+0.8%** (gapped up, broke down); or an earnings miss; or a beat-reprice with gap ≥ +3%; or a T4 "lowered" guidance
- dilution does **not** veto shorts

Mechanics:
- Stop = max(H_ORB + 0.25·ATR, entry × 1.005).
- The bracket is side `sell`, and it is validated as target < entry < stop.
- R math is sign-flipped. Exits are `buy` market orders for the own qty.
- The board short fraction must be between 25% and 75% or the whole wave sits out.

---

## Uncertainties / things to verify before copying

1. `runtime.env` on the volume was not read directly. Autopilot on and risk 2.0 are inferred from the live `/api/state`.
2. The breakeven PATCH probably fails on Alpaca held legs (422). The supervisor's software BE exit is the effective mechanism.
3. Secondary decisions reuse the 09:38 SPY/QQQ slope and the 09:38 news cutoff (a design quirk, possibly unintended).
4. Supervisor R uses the Alpaca position `current_price` polled every 5 s. Fast-fail and clawback therefore have up to ~5 s latency plus market-order slippage.
5. On 09-28, the bot's velocity reading for PLTR was 0.91x versus 1.97x in an independent recompute. Both failed, and the difference is not fully explained (MEMORY 09-28). The excursion-first-print latch is the likely cause.
6. Research (MEMORY) found no out-of-sample edge for these rules. The flow rules went live with no backtest, by operator order.
