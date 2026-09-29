# Edge hunt protocol (frozen 2026-09-29 before any result was seen)

Goal. Find at least three strategies, each on a different stock, that trade at least three times a week and match the Tesla Morning Plan's quality. The Tesla plan (live 09:30 range) backtests at about 53% winners, profit factor about 1.3, about +0.1R a trade after costs, p about .02. These must not be copies of the opening range retest. TSLA and CDE are excluded.

## Data

One minute SIP bars from AlpacaRelay, split adjusted, 2023-06-26 to 2026-09-28 (the first 60 sessions are warm up for sigma and beta only), regular session 09:30 to 16:00 ET (13:00 on early close days). Prior close means the close of the last regular session bar. Wherever a rule says sigma, it means the standard deviation of that same measurement over the prior 20 sessions (today excluded), so every threshold is known before the decision minute.

Universe (fixed now, 50 stocks plus context ETFs SPY, QQQ, IWM, SMH, XLF, XLE).
NVDA AAPL MSFT AMZN META GOOGL AVGO NFLX AMD MU SMCI INTC ARM QCOM MRVL PLTR CRWD SNOW SHOP UBER ORCL ADBE COIN MSTR MARA RIOT HOOD SOFI PYPL AFRM NKE SBUX DIS WMT COST CVNA F GM RIVN XOM OXY FCX NEM JPM BAC GS LLY UNH MRNA BA

## Periods

Discovery (in sample) 2023-10-02 to 2025-06-30. Holdout (out of sample) 2025-07-01 to 2026-09-28. The holdout is touched once, only for candidates chosen from discovery. No rule, parameter, stock or family is changed after the holdout is seen. If something is changed anyway, the result is reported as in sample.

## Fill and cost model (same spirit as the Tesla research, a bit stricter on cost)

A signal on the bar ending at minute T fills at the open of bar T+2 (60 seconds of latency). If that bar is missing, the next bar within three minutes is used, otherwise no trade. Stops fill at the stop price, or at the bar open when the bar opens through it. Targets fill only when a bar trades past them. When one bar touches both, the stop wins. Time exits fill at the open of the exit bar. Market on close orders fill at the 15:59 bar close and must be decided from bars ending by 15:48. Market on open orders fill at the 09:30 bar open.

Round trip cost per trade is max(6 bps, one cent over price plus 2 bps), about 6 bps on large caps and 10 to 14 bps on stocks under $15. Stress cost is double that. A win is a trade that makes money after normal cost.

## Strategy families (none is an opening range breakout or retest)

Each family has a written reason and a small fixed grid. Every stock gets every grid point.

1. Late day momentum (18 configs). Why. Leveraged single stock ETFs, options hedging and index rebalancing push stocks in the direction of the day's move into the close (Gao, Han, Li and Zhou 2018 found this in SPY). Trade in the direction of a predictor, enter at 15:00 or 15:30, exit at the close. Predictor is prior close to 10:00, prior close to entry, or open to entry. Minimum predictor size 0, 0.5 or 1.0 sigma scaled to that window.
2. Overnight drift (8 configs). Why. Stocks earn most of their return overnight while the session is flat, and intraday selloffs tend to reverse overnight. Buy on the close, sell at the next open or at 10:00. Condition is none, open to 15:48 return below zero, below minus 0.5 sigma, or above plus 0.5 sigma.
3. Overnight gap (24 configs). Why. Retail heavy stocks overreact to overnight news, and strong news gaps can also keep going. Gap threshold 0.3%, 0.75% or 1.5%. Fade toward the prior close, or go with the gap to a 1.5R target. Stop is 0.5 or 1.0 times the gap beyond entry. Time exit 11:30 or 15:55.
4. VWAP stretch reversion (24 configs). Why. Liquidity driven overshoots away from the day's volume weighted price tend to come back. From 10:00 to 15:00, fade a close more than 2, 2.5 or 3 band widths from VWAP, with or without a reversal bar. Target is VWAP. Stop is the recent 15 bar extreme or the same distance as the target. Time exit 60 or 180 minutes. At most two trades a day, one at a time.
5. Failed prior day level (8 configs). Why. Obvious levels attract stop runs that often fail. After 09:45, a break is the first bar that closes beyond yesterday's high (or low) after closing inside it. A failure is a close back inside within 5 or 15 bars of the break. Trade back into the range at T+2. Stop is the extreme since the break. Target 1.5R or 2R. Time exit 90 or 180 minutes. Signals until 15:00, each level once a day.
6. Relative value reversion (12 configs). Why. Idiosyncratic intraday moves with no news partly revert. At 11:00 or 13:00, measure the stock's open to now return minus beta times its benchmark's (QQQ for tech, SMH for semis, XLF for banks and fintech, XLE for energy, SPY otherwise, 60 day daily beta). If the gap is beyond 1.0, 1.5 or 2.0 residual sigmas, trade toward the benchmark. Exit at the close or after 120 minutes.

Total 94 configs per stock, 4,700 discovery tests. By chance alone about 47 of them clear p < .01, so a discovery result means nothing until the holdout confirms it.

## Discovery selection (per stock and family)

A config qualifies if, in discovery, it has at least 3 trades a week, a positive net mean, at least 50% winners, profit factor at least 1.2, and t at least 2.0, with t computed on daily sums so several trades on one day count once. Its grid neighbours (configs differing in one setting) must have a median t of at least 1.0, so a lone spike does not qualify. The qualifying config with the highest t goes forward, at most one per stock and family.

## Holdout pass rules (all must hold)

1. Holdout alone. At least 3 trades a week, positive net mean, profit factor at least 1.15, one sided p below .05.
2. Multiple testing. Benjamini Hochberg over every candidate sent to the holdout, q below .10.
3. Full period. At least 52% winners, profit factor at least 1.2 at normal cost, still positive at stress cost.
4. Stability. Positive in at least 4 of the 6 half years.

A finding is a pass. Each finding must be on a different stock, and the three should come from different families if the data allows. If fewer than three pass, the report says so and lists the near misses with the rule they broke. Nothing is retuned to make them pass.


## Amendments (all made before any profit or win rate number was seen)

1. 2026-09-29. Relative value thresholds changed from 1.0, 1.5, 2.0 residual sigmas to 0.5, 0.75, 1.0. A frequency only check on AAPL showed 1.0 sigma fires on about a third of days (1.5 a week), so the old grid could never meet the 3 a week rule. Only trade counts were looked at.
2. 2026-09-29. Late day momentum entry times were coded as 900 and 930 instead of 1500 and 1530, which put them at the open. Fixed as a bug, caught by a clock time test before any result was read.

3. 2026-09-29, after discovery round 1 (5 candidates) and before any holdout run. If round 1 yields fewer than three findings, later rounds may add new families written down before their discovery run. Old families are never retuned. The Benjamini Hochberg correction is always computed over every candidate from every round, so each extra round makes the bar higher, not lower.


## Round 2 families (written 2026-09-29 after round 1's holdout, before any round 2 run)

Round 1 lesson. Across 4,700 tests the short hold intraday families lost after cost (mean t negative 2 to 4). The one holdout pass was an overnight hold. Round 2 therefore tests longer holds only, where a 6 bps round trip is small next to the move. Same data, periods, fill model, discovery selection and pass rules. Benjamini Hochberg now runs over round 1 and round 2 candidates together.

7. Trend hold (18 configs). Why. Institutions split big orders across the whole session, so a direction that is clear by mid morning and shared by the market tends to persist into the close. At 10:00, 10:30 or 11:00, take the sign of the stock's open to now return. Require its size above 0 or 0.5 sigma. Confirmation is none, the benchmark moving the same way, or that plus the stock on the same side of its VWAP. Enter at T+2, exit market on close, no stop.
8. Overnight persistence (8 configs). Why. Lou, Polk and Skouras (2019) show a stock's overnight return sign persists for weeks because the same clientele trades it at the open. Hold close to next open (or to 10:00) in the sign of the stock's mean overnight return over the prior 20 or 60 sessions. Mode is long and short, or long only.
9. Daily swing (6 configs). Why. One day holds sit between intraday noise and multi week trends. At 15:48 take the sign of the move from the close 1, 3 or 5 sessions ago to now, follow it or fade it, enter market on close and exit at the next session's close.


## Round 3, the final round (written 2026-09-29 after the round 2 holdout, before any round 3 data was fetched)

Result so far. 13 holdout candidates, none passes all rules. NVDA overnight passes the holdout alone (p .019) but not Benjamini Hochberg over 13 (q .19).

Round 3 changes only the universe, never the rules. All nine families run with their frozen grids on 50 liquid midcap and high volatility names the first two rounds did not cover. They were picked for liquidity and for existing since mid 2023, with no performance look. Why. Megacaps are the most efficient part of the market, and the Tesla research found its second edge in a midcap miner (CDE). Benjamini Hochberg runs over every candidate from all three rounds. There is no round 4, whatever the result.

Universe. HL AG PAAS KGC AU CLF AA MP HUT IREN NIO XPEV LI BABA PDD JD BIDU IONQ AAL UAL DAL CCL NCLH GME CHWY ETSY RBLX DKNG PINS SNAP LYFT ROKU U PATH AI UPST CELH ENPH FSLR RUN DVN APA HAL SLB EQT KEY WAL CCJ VST TTD. Benchmarks are QQQ for internet and software names, XLE for energy, XLF for the two banks, SPY for the rest.


## Results (2026-09-29, after the final round)

12,600 discovery tests (100 stocks, 9 families). 23 candidates reached the holdout. 5 passed the holdout alone, against about 1.2 expected by luck. None passed all four rules, so there are zero findings. The five all failed rule 2 (Benjamini Hochberg q .149, bar .10). All five are long overnight holds in three stocks.

| Candidate | Trades a week | Win | Mean a trade | PF | Holdout p | Half years up |
|---|---|---|---|---|---|---|
| IREN buy close, sell next open | 4.9 | 52.4% | +44 bps | 1.40 | .009 | 6 of 6 |
| NVDA buy close, sell next open | 4.9 | 56.9% | +19 bps | 1.36 | .019 | 6 of 6 |
| HUT buy close, sell next open | 4.9 | 52.8% | +45 bps | 1.42 | .031 | 6 of 6 |

Full period numbers, normal cost. All three stay positive at double cost and after dropping their 10 best nights. Most of it is being long high beta stocks that rallied. QQQ alone earned +8 bps a night, and the stock specific part is +11 (NVDA) and about +30 (HUT, IREN) bps a night. NVDA loses on Friday entries. HUT and IREN are both bitcoin miners, so they are one bet, not two.

Intraday families showed no edge before cost. Gross means per trade were negative 2.2 to +0.7 bps for gap, VWAP stretch, failed level, relative value and late day momentum across 100 stocks. Two leads run opposite to their hypothesis, late day moves reversing and VWAP stretches continuing (t below negative 2 in 10.5% and 8.2% of tests, against 2.3% by chance). They are in sample only and need forward data.

Independent read only audit found no high severity bug. Hand computed parity 433/433 for AAPL late day momentum and NVDA overnight.
