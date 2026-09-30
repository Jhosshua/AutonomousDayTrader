# Ten stock day trade patterns, protocol (draft 2, frozen 2026-09-30 before any result is seen)

Goal. Find the patterns and a rule ("formula") for day trading each of TQQQ, QQQ, PLTR, MSTR, COIN, META, VRT, AMD, SPY and SMH, using only the last two years of trading. Research only. Nothing is deployed.

Honest prior. The 2026-09-29 edge hunt (research/edge_hunt_2026_09_29) tested gap, VWAP stretch, failed level, relative value and late day families on 100 stocks, five of them in this list, over data that covers this whole window. Gross edge before cost was negative 2 to +1 bps a trade. The likely outcome is that few or none of these names has a rule that survives the holdout. Because that period was already looked at for META, AMD, PLTR, COIN and MSTR, the holdout here is a "previously observed period" and any pass is provisional until a forward paper period confirms it.

Draft 1 was attacked by three read only reviewers (statistics, execution, fidelity). Their 45 findings and the answer to each are in the table at the end. Draft 2 is the frozen protocol. Its SHA-256 is recorded in FROZEN.txt before any run. No amendment is allowed once any atlas or profit number exists.

## Operator bar (from standing preferences)

1. Pick every setting on a design window, then touch the holdout once. Anything changed after the holdout is seen is labelled in sample.
2. Walk forward "without luck". Each period's choice is fit only on earlier data, the whole menu (losers too) is declared first, and objective, cost and window are fixed before the run.
3. Cost stress at 1.5x, 2x and 3x, turnover a year, and a cadence change.
4. Transfer of each passing rule unchanged to the other names (credit only across clusters).
5. Trial count, deflated Sharpe ratio and a bootstrap interval.
6. Max drawdown and open drawdown on the last session.
7. Quality reference from the edge hunt, about 53% winners and profit factor about 1.3.
8. A period by period table plus the rule in plain numbered steps, and one side by side table for comparisons.
9. Families already tested and failed are never presented as new.

## Data

One minute SIP bars from AlpacaRelay, split adjusted (TQQQ split 2 for 1 on 2025-11-20, price and volume both adjusted, checked). Bar timestamps are bar starts. Regular session 09:30 to 16:00 ET, 13:00 on the early close days in the relay calendar (2024-11-29, 2024-12-24, 2025-07-03, 2025-11-28, 2025-12-24 inside the window). Premarket 04:00 to 09:29 is kept in a separate matrix and never mixed into regular session arrays. Daily bars give the official open and close. BTC/USD one minute bars from the relay crypto route are used only by F2. XLF is fetched only for the reference relative value family.

Trading window 2024-09-30 to 2026-09-29 (about 502 sessions). Bars from 2024-06-03 to 2024-09-27 are warm up for trailing statistics only. No trade is taken before 2024-09-30. Bars before 2024-06-03 were downloaded but are never read. The draft 1 "pre window check" is removed because the operator asked for the last two years only.

Design 2024-09-30 to 2025-09-30. Holdout 2025-10-01 to 2026-09-29. Half years are H1 2024-09-30 to 2025-03-31, H2 2025-04-01 to 2025-09-30, H3 2025-10-01 to 2026-03-31, H4 2026-04-01 to 2026-09-29.

Clusters, used to count findings and transfers. Index is SPY, QQQ, TQQQ. Semis and AI is SMH, AMD, VRT. Crypto is MSTR, COIN. PLTR and META are each their own cluster. At most one finding counts per cluster, and a transfer inside a cluster earns no credit.

## Fill and cost model

A signal decided on bar index i (the bar starting 09:30 + i minutes, closing at 09:31 + i) fills at the open of bar i + 2, which is 60 seconds after the signal bar closes, or bar i + 3 or i + 4 if missing, otherwise no trade. Rules that use only prior day data enter at a fixed bar open with no lag. Market on open fills at the official open. The close exit fills at the official close and must be decided from bars ending by 15:48. Stops fill at the stop, or at the bar open when it opens through, and then pay extra slippage of max(1 cent, 3 bps). Targets fill only when a bar trades past them. When one bar touches both, the stop wins.

Round trip cost c = max(6 bps, one cent over price plus 2 bps), charged as half per leg. A leg filled in continuous trading before 09:45 or from 15:55 costs double (spreads are widest then). Opening and closing auction legs cost the normal half. On SPY and QQQ this cost is many times the real spread, so it is conservative for the index funds. Stress runs use 1.5x, 2x and 3x cost (stop slippage scales too).

Short sales are allowed. A short entry is dropped when the short sale restriction (Rule 201) would be active, meaning the prior session's low was at or below 0.9 times its prior close, or today's low so far is at or below 0.9 times the prior close. Borrow fees are not modelled. Every rule is also reported long only, and any MSTR or COIN rule that needs its short side carries a borrow warning in its steps.

All gates and tests use net percent return a trade. R multiples are shown only as description. A win is a trade that makes money after normal cost. Every sigma is the standard deviation of the same measurement over the prior 20 valid sessions, today excluded.

Event days. A session is an event day (an earnings proxy, since no earnings calendar is available offline) when its absolute gap is at least 3 sigma of gaps and its premarket volume is at least 5 times its 20 session median. Every rule is reported with and without event days and the day after. This is description only.

## Families (grids frozen now, every eligible name gets every grid point)

New families

F2 Bitcoin lead (MSTR and COIN, 16 configs). Why. Both are bitcoin proxies, and bitcoin trades through the night while the stocks do not. BTC price at a time T is the close of the last BTC bar starting at or before T minus one minute. The anchor is the BTC price at the start of the stock's last regular bar of the prior session (15:59, or 12:59 on an early close). Decision bars close at 09:35 or 10:00. Bitcoin's move m is scaled by the square root of the hours since the anchor, so Monday moves are comparable to weekday ones. Beta is the 60 session regression of the stock's official close to close return on bitcoin's move over the same anchor to anchor window. Mode catch up takes e = stock move from prior close minus beta times bitcoin's raw move, scales it the same way, and trades against e when its size exceeds k sigmas (a cousin of the excluded residual family, labelled as such). Mode follow trades in the direction of the scaled bitcoin move when it exceeds k of its sigmas. k is 0.5 or 1.0. Exit 60 minutes after entry or at the close. Alpaca BTC bars are mostly quote derived (73% have zero volume but a real range), used on purpose as a price. A day is skipped if no BTC bar started in the 5 minutes before the decision or the anchor. The family is dropped if more than 20% of design days are skipped. The design year correlation of bitcoin's daily move with each stock's is reported.

F3 Intraday periodicity (4 configs, all names). Why. Heston, Korajczyk and Sadka (2010) found a stock's return in a half hour predicts its return in the same half hour on later days. Slots start 09:30, 10:00 and so on to 15:00 (12 slots, the 15:30 slot is dropped because it overlaps last half hour momentum). A slot return runs from the open of its second minute to the open of the next slot's second minute, measured and traded the same way. The predictor is that slot's mean over the prior 20 or 40 sessions where the slot exists. Trade the slot in the predictor's sign when its t statistic exceeds 1.0 or 2.0. Back to back slots with the same sign are one position, costed once. On early close days the 12:30 slot exits at the close and later slots do not exist.

F4 Volume shock bars (12 configs, all names). Why. An extreme volume minute marks a large order. It keeps going as the order works, or reverts as liquidity refills. From bars starting 09:45 to 15:00, a shock bar has volume at least 5 or 10 times the median volume of that minute over the prior 20 sessions where it exists, and an absolute close over open return at least 3 times the median absolute one minute return over the prior 20 sessions. Follow or fade the bar's direction. Exit after 15 or 60 minutes or at the close. Signals while a position is open are ignored. At most three trades a day.

Cousins of excluded families (labelled, not presented as new)

F5 Prior day candle (16 configs, all names). A cousin of the 1 day swing, entry moved to 09:35. States are strong close (close in the top 20% of the day's range), weak close (bottom 20%), range expansion (range at least 1.5 times its 20 session mean, direction of that day's close versus open) and inside day (same direction rule). Follow or fade that direction. Enter at the open of the bar starting 09:35. Exit at 12:00 or the close. A prior day that closed early gives no signal. Results are also split by today's gap sign to show whether it is a gap rule in disguise.

Reference families (well known, reported so the answer is complete, never called new)

F1 Opening range breakout (16 configs). Range is the first 5, 15 or 30 minutes, or the premarket range 04:00 to 09:29. The premarket range needs at least 60 premarket bars that day, otherwise no premarket trade that day. The trigger is the first regular session one minute close beyond the range (for the premarket range the 09:30 bar is eligible). The signal bar must start before 11:30. One trade a day. Stop at the far side of the range or its midpoint. Target 2R, or hold to the close with the stop. A trade is skipped when the stop is less than 3 times the round trip cost away.

F6 Midday range break (8 configs), a range breakout variant of F1. Range is bars starting 11:30 or 12:00 through 13:29. The first eligible signal bar starts 13:30 and the last before 15:00. Stop and target as F1, same small stop skip. No trade on early close days.

E1 to E5 Edge hunt grids, unchanged, run on all ten names: gap (24), VWAP stretch (24), failed prior day level (8), late day momentum (18), relative value (12, benchmark map unchanged from the edge hunt, SPY skipped because it is its own benchmark). These failed across 100 stocks. They are included because SPY, QQQ, SMH, TQQQ and VRT were never trade targets there and a day trader will ask about them. Only the cost model differs from the edge hunt.

Benchmark row outside the trial count. The operator's live TSLA morning plan (tsla_or15_retest), unchanged, run on all ten names. 09:30 to 09:44 range, signal bars 09:45 to 11:30, first close above the range high, then a later retest bar with low within 0.2 frozen ATR of the high, low at or above the midpoint, a green bar, close at or above the high, and QQQ at or above its VWAP. Long only, enter T+2, stop at the range low, target 2R from the signal close, exit after 120 minutes or at the close minus 5 minutes.

Left out on purpose. A semis leader family (NVDA leading AMD, VRT, SMH) was considered. Leaders and followers trade in the same premarket and session, so a lag between them is the excluded residual family. Bitcoin differs because it trades through the night. A "signal on QQQ, trade TQQQ" variant was left out because TQQQ's own tape carries the same signal minute by minute. The edge hunt's two unconfirmed leads came from overlapping data and are not re-tested.

Trial count. Per name 56 new and cousin configs (72 for MSTR and COIN) plus 86 reference configs (74 for SPY). 1,440 name level tests plus 158 pooled tests, 1,598 in all. An effective count is also computed (below).

## Design selection

Trade count screen first. Using trade counts only (no profit numbers), any config with fewer than 1 trade a week in the design year is dropped and logged.

A surviving config qualifies if, on the design year, it has a positive net mean, profit factor at least 1.2 and t at least 2.0, with t computed on daily sums. There is no win rate gate, because 2R target and hold with stop rules win well under 50% by design. Win rate is shown next to the 53% reference.

Neighbour rule. Neighbours differ in one ordinal setting only. F1 target and stop, F2 k and exit, F3 lookback and threshold, F4 volume multiple and exit, F5 exit, F6 range start, stop and target, and for the edge hunt grids their numeric axes only (gap size, stop multiple, z, hold, window, k, entry time). Follow versus fade, modes, states and range type are never neighbours. The median neighbour t must be at least 1.0. A config with no neighbour on any ordinal axis needs t at least 2.5 instead.

The qualifying config with the highest t in each name and family goes to the holdout. Ties break by family order, then grid order.

Pooled track (for power). For every config, the pooled daily P&L is the mean across its eligible names of each name's net return that day, zero when no trade. The same gates apply (frequency means at least 1 trade a week on average per name). The best pooled config in each family goes to the holdout too.

## Holdout pass rules (all must hold, holdout data only)

1. At least 1 trade a week, positive net mean, profit factor at least 1.15, one sided t test p below .05 on daily sums (this p feeds the correction).
2. Benjamini Hochberg over every holdout candidate, name level and pooled together, q below .10. This controls false discoveries among the candidates only, and assumes positive dependence, which is plausible here.
3. Still positive at 2x cost in the holdout.
4. Both holdout half years (H3, H4) positive.

Full two year numbers are shown as description. A name's formula is its passing candidate with the highest design t. Every other pass is shown too.

## Walk forward (the "without luck" record)

Anchored and refit each quarter. Primary spec, fixed now. For each name, at the start of each quarter from 2025-04-01 to 2026-07-01, the menu is every config of every family for that name. The pick is the config with the highest t on all data from 2024-09-30 to the day before the quarter, among configs with at least fit sessions over 5 trades, positive mean, profit factor at least 1.2, t at least 2.0 and the neighbour rule on the fit window. Ties break as above. With no qualifier the name sits out that quarter at zero. 2025-Q2 and 2025-Q3 are inside the design year and labelled so. The success test is the pooled daily P&L across all ten names over the four holdout quarters, t test p below .05. Sensitivity only (never used to pick), profit factor as the objective, and a trailing six month window.

## Statistics

Minimum detectable effect. The report states the smallest per trade Sharpe the holdout can detect with 80% power at each observed frequency. A null for a rule under about 3 trades a week is reported as "not enough trades to tell", not as "no edge".

Effective trial count. Design daily P&L of all configs is clustered at absolute correlation above 0.9. The cluster count is reported beside the raw count.

Deflated Sharpe ratio (Bailey and Lopez de Prado 2014). Computed on each candidate's design year daily Sharpe (all sessions, zeros on no trade days), T = design sessions, the variance of Sharpe taken from all design configs on the same basis, skew and kurtosis of the candidate's series. Reported with N = raw count, N = effective count, and N including the edge hunt's 12,600 tests on overlapping data. The holdout t is reported separately.

Bootstrap. Stationary bootstrap (Politis and Romano) of holdout daily P&L over all sessions, mean block 5 sessions, 10,000 draws, seed 20260930, 90% percentile interval on the mean and a one sided p. Reported, not gating.

## Robustness on anything that passes (description only, never gating)

Cost at 1.5x, 2x and 3x, and turnover a year. Cadence change (breakout rules confirm on a 5 minute close, time based rules enter 5 minutes later). Transfer unchanged to the other names, credit only outside the cluster. Max drawdown and open drawdown on 2026-09-29. With and without event days.

## Pattern atlas (description only, computed after the holdout run)

Per name, for each year separately and marked where the sign holds in both years. Half hour mean and absolute return. Share of return earned overnight versus intraday. Gap size, and how often a gap fills by 10:30 and by the close. Correlation of the first 30 minutes with the rest of the day. Return autocorrelation at 5, 15, 30 and 60 minutes. Day of week open to close means. FOMC, monthly option expiry and turn of month open to close means (too rare to trade as rules). Event day stats.

## Sizing and accounts

Returns compound on full equity per trade in that one name, one position at a time, no margin, whole shares ignored. "Growth of $10,000" is a scale, not an account size. Day trading more than three times in five sessions needs a margin account of at least $25,000 under pattern day trader rules, which every rule's steps will state.

## Output (REPORT.md)

First, a ten row report card. Per name, the atlas patterns that held the same sign in both years, the rule in plain numbered steps (a pass, or the walk forward pick and nearest miss clearly labelled as failed), and one plain verdict line.

Then one side by side table of all ten names (rule, hold time, trades a week, win rate, profit factor, holdout p, BH q, long only or both ways, profit a trade, profit a year, worst drop, profit per $1 of worst drop, at normal and 2x cost), with the TSLA plan benchmark row.

Then for each passing rule a quarter by quarter table with columns quarter, strategy return, the stock's open to close every session, the stock's buy and hold, SPY buy and hold, strategy growth of $10,000, buy and hold growth of $10,000, trades, win rate, max drawdown. The walk forward record uses the same format.

Then the design and holdout tables, statistics, robustness and the atlas.

## Reviewer findings and answers

| ID | Sev | Finding (short) | Answer |
|---|---|---|---|
| S1 | high | 1 a week gives little power | Pooled track added. MDE reported. Low frequency nulls reported as "not enough trades" |
| S2 | high | Win gates kill 2R and stop families | Win gate removed, shown as description |
| S3 | high | Neighbour rule mixes mirrors and categories | Ordinal neighbours only, listed per family, t 2.5 when none |
| S4 | medium | Trial count ignores redundancy | Effective count by correlation clusters |
| S5 | high | Deflated Sharpe unspecified | Specified on design daily Sharpe, three N values |
| S6 | medium | Bootstrap unspecified, which p feeds BH | Stationary, block 5, 10,000 draws, seed, t test p feeds BH |
| S7 | high | Full period gates reuse selected data | Gates 3 and 4 on holdout only, full period is description |
| S8 | medium | Correlated names inflate findings | Clusters declared, one finding per cluster |
| S9 | high | Holdout was seen by the edge hunt | Labelled previously observed, pass is provisional until forward paper |
| S10 | medium | Atlas can leak | Atlas after holdout, protocol hashed, no amendments after any number |
| S11 | medium | Walk forward underspecified | Tie break, frequency, neighbour rule, zero sit outs, labelled quarters |
| S12 | medium | Walk forward forking paths | Anchored plus t is primary, pooled p < .05 success test |
| S13 | medium | Rare configs are dead weight | Trade count screen before any profit number |
| S14 | medium | R units hide percent losses | All gates in net percent |
| S15 | medium | Post holdout choices, Monday BTC | Highest design t, robustness description only, BTC scaled by root hours |
| E1 | high | Premarket minutes wrap into session arrays | Separate premarket matrix, test added |
| E2 | high | BTC snapshot one bar of lookahead | BTC bar start at most T minus 1, fill wording fixed |
| E3 | high | BTC staleness check never fires | Quote derived bars used on purpose, 5 minute skip rule, 20% drop rule |
| E4 | medium | BTC anchor, early closes, DST | Anchor at the stock's last regular bar start, search on ET time (DST duplicates are at 01:00 only) |
| E5 | high | Thin premarket ranges, trigger undefined | 60 bar minimum, 09:30 bar eligible, cutoff on signal bar |
| E6 | high | Tight stops, exact stop fills | Percent units, skip stops under 3x cost, stop slippage |
| E7 | medium | Open and close spreads, SPY claim | Double leg cost before 09:45 and from 15:55, wording fixed |
| E8 | medium | F3 double costs, short days | Merge same sign slots, measured as traded, early close rule |
| E9 | medium | 15:59 close is not the official close | Official close from daily bars |
| E10 | medium | Rule 201 | Short entries dropped when active |
| E11 | medium | PDT at $10,000 | $25,000 margin stated, $10,000 is a scale |
| E12 | low | Fractional shorts | Whole shares ignored, stated |
| E13 | low | Splits and dividends | TQQQ split checked, adjusted. Dividends ignored for intraday rules |
| E14 | medium | Reused code carries old constants | Forked library with this protocol's constants and tests |
| E15 | medium | F4, F5, F6 edge definitions | Written into the family text |
| R1 | high | Same as S2 | Same answer |
| R2 | high | No earnings handling | Event day proxy, every rule reported with and without |
| R3 | high | Gap and VWAP never tested on the index funds and VRT | Edge hunt grids run unchanged as a reference block |
| R4 | high | Output may give no answer | Report card first with patterns, rule or labelled miss, verdict |
| R5 | medium | Output columns thin | Columns frozen |
| R6 | medium | Pre window check breaks "last 2 years" | Removed |
| R7 | medium | TQQQ and QQQ double count | Clusters. QQQ to TQQQ variant left out with reason |
| R8 | medium | Semis leader unused | Left out with reason |
| R9 | medium | F6 is an ORB | Labelled reference variant |
| R10 | medium | F5 and F2 catch up are cousins | Labelled, F5 split by gap sign, F3 last slot dropped |
| R11 | medium | Hardcoded 3 a week and 4 of 6 | Forked constants, tests assert this protocol |
| R12 | medium | $10,000 not executable | Same as E11 |
| R13 | medium | Live TSLA plan not a baseline | Benchmark row on all ten names |
| R14 | low | Tie break for the formula | Highest design t |
| R15 | low | MSTR and COIN borrow | Long only shown, borrow warning in steps |
