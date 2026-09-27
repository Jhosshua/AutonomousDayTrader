# Why "Ride the Trend" loses, 50 hypotheses, and what I would change

Date: 2026-09-27. Strategy: `vwap_pullback` ("Ride the Trend" on the dashboard), `backend/app/strategies/vwap_pullback.py`.

## The short answer

The entry has no information. After 36,435 signals over 2024-01 to 2026-09, price moved an average of
**0 basis points** in the trade's direction at 5, 15, 30, 60 and 120 minutes and at the close (standard errors
0.1 to 0.7 bps). A coin flip that pays a round-trip cost of about 3 bps against a stop 45 bps away loses about
0.06R per trade, and that is exactly what the backtest shows:

| Live rules, 12 symbols, $50k, 1% risk | |
|---|---|
| Trades 2024-01-02 to 2026-09-25 | 4,407 (6.4 per trading day) |
| Mean R per trade | -0.055, SE 0.013 (4 SE below zero) |
| Same with zero cost | +0.001 |
| Net P&L | -$32,116 (-$47 per day, 54% of days negative, max drawdown -$34k) |
| Win rate / half hits T1 / runner hits T2 | 52% / 46% / 3.6% |
| By period (IS 2024-01..2025-03 / VAL 2025-04..12 / HOLD 2026) | -0.085 / -0.058 / -0.003 |

The live sample on the paper account (20 trades since 09-24, -$80) is too small to prove anything on its own, but
it matches the backtest bleed (about -$7 per trade at sim sizing, live sizes are roughly half).

## How this was tested

- `sim.py` re-implements the live signal logic bar by bar; `parity.py` runs the bot's real `VWAPPullbackStrategy`
  on 144 random symbol-days and gets the same 649 signals (timestamp, side, entry, stop, targets, fallback flags).
- Execution replicates `main.py`, `adaptation.py`, `bracket.py`: phase gate (no 11:30-14:00, none after 15:45),
  SPY/QQQ VWAP+EMA9/21 index filter, VIX stop and size multipliers (CBOE daily VIX open), 0.4% stop floor and 4%
  ceiling, 1% risk sizing capped at $25k notional, 3 slots, 2 per sector, band-or-fallback targets, 50% off at T1,
  breakeven ratchet, 1.5x ATR14 trail, market flatten at 15:55. Fills: market at close ±1.5 bps, stop at stop
  price ±1.5 bps (gap through at open), limit targets at the target price, stop wins a same-bar tie.
- Data: full-day 1-minute SIP bars for the 12 watchlist symbols from AlpacaRelay (`fetch_bars.py`, 2024-01-02 to
  2026-09-25, cached in `data/bars/`, gitignored).
- Check against reality: the simulator produces the bot's seven real afternoon trades on 09-24 and 09-25 at the
  same minutes (COIN 14:23, COIN 14:50, TSLA 15:29, PLTR 15:31; GOOGL 14:02, TSLA 14:09, NVDA 14:14). The
  morning live trades came from the pre-09-25 code (no 50-bar warm-up) and cannot be reproduced by current code.
- `variants.py` runs 69 rule changes, reported per period so a change has to work in all three to count.
  `cuts.py` buckets the baseline trades by every feature. `crux.py` measures raw forward drift after signals.

## The 50 hypotheses and what the data says

Legend: CONFIRMED = it is a cause; SUPPORTED = real effect but does not fix it; REJECTED = no effect or wrong.
Numbers are mean R per trade unless stated; baseline is -0.055.

### Entry and signal quality
1. **The entry carries no information.** CONFIRMED (root cause). Forward drift 0 bps at every horizon, in all three periods, long and short, morning and afternoon, filtered and unfiltered.
2. **Trading cost is the whole loss.** CONFIRMED. Zero cost: +0.001. 3 bps: -0.112. Stop slippage 3 bps: -0.074.
3. **EMA20 vs EMA50 on 1-minute closes is not a trend.** CONFIRMED. Stocks already up 0.5%+ on the day in the trade's direction were the worst bucket (-0.109); requiring 1% made it -0.085. The "trend" is anti-predictive.
4. **Entries chase the bounce bar.** SUPPORTED. Entries closing far above VWAP (top quintile) -0.118 vs -0.02 near VWAP. Limit at VWAP instead: -0.019.
5. **Volume-surge confirmation is anti-predictive.** SUPPORTED. Volume-confirmed -0.063 vs not -0.048; top volume-ratio quintile -0.092; requiring wick AND volume -0.079; volume 1.5x -0.062.
6. **The hammer wick is the better of the two confirmations.** SUPPORTED but still negative. Wick trades -0.045 vs -0.073; top wick quintile -0.014; raising wick minimum to 0.5 gives -0.055.
7. **"Prior bar was in the zone" carry-over creates late entries.** REJECTED. Without it: -0.058.
8. **Pullback zone too wide or too narrow.** REJECTED. Tight (0.1/0.15 std) -0.051, wide (0.4/0.6) -0.051.
9. **Repeated re-entries in chop.** SUPPORTED, not a fix. 3rd+ signal of the day -0.07 to -0.10 vs 1st -0.036; cooldown 60 min -0.047; first signal only -0.043; max 2 per symbol-day -0.037.
10. **Needs a stronger trend (EMA gap ≥0.1%).** REJECTED. -0.089, worse.
11. **Wrong EMA speeds.** REJECTED. 9/21: -0.041 flat across periods; 50/200: -0.075.
12. **Starts too early (50 bars).** REJECTED. 100-bar warm-up: -0.090.
13. **Should wait for the first real pullback (price away from VWAP ≥45 bars).** REJECTED. -0.073.
14. **Needs VWAP sloping the trade's way.** REJECTED. Slope ≥0.1%: -0.021 (n=747, not robust); ≥0.2%: -0.061.

### Stop geometry
15. **The stop sits inside one-minute noise.** SUPPORTED as symptom. 38% of trades stop out; 20% of trades die inside 12 minutes at -0.196. But wider stops (1 std: -0.048; 2x ATR: -0.055) do not help: there is no drift to protect.
16. **The 0.4% floor distorts trades.** REJECTED. Floored trades -0.054 vs structural -0.058.
17. **Mid-width stops (0.42-0.59%) are worst (-0.119).** Noise, not a lever. REJECTED.
18. **VIX stop multiplier hurts.** REJECTED. Without VIX adaptation: -0.060.
19. **Reward is too small.** SUPPORTED as structure. Median T1 is 0.76R with half off there, so break-even needs about 58% winners and it gets 52%. But bigger targets (1R/2R, 1.5R/3R, single 2R) all land at -0.054: payoff and win rate trade off exactly, as they must with zero drift.
20. **Band targets vs R fallbacks.** REJECTED. Fallback trades -0.041 vs band -0.067; bands 1.5/3 std: -0.055.
21. **Breakeven ratchet scratches runners.** REJECTED. Without it: -0.056. Buffer zero: -0.056.
22. **ATR trail too tight or too loose.** REJECTED. 1.0x -0.055, 3.0x -0.062, no trail -0.061.

### Time and exits
23. **A time stop would cut dead trades.** REJECTED. 30 min -0.066, 60 min -0.060.
24. **Time stop only when underwater.** REJECTED. -0.064.
25. **The afternoon window is worse.** SUPPORTED. 14:00-15:45 -0.075 vs morning -0.043; the 15:00 hour -0.099 with T1 hit only 21%. Morning only is still -0.043.
26. **The 15:55 flatten kills late entries.** SUPPORTED weakly. 733 trades flattened unresolved at -0.104.
27. **The midday block is wrong.** REJECTED. Trading 11:30-14:00 too: -0.061; midday alone in 2026 -0.067.
28. **Fridays are worse.** SUPPORTED. Friday -0.120 vs -0.039 other days, 2.5 SE. Skipping Fridays leaves -0.039.
29. **Holding period.** No lever. Winners resolve in 12-40 minutes; long holds (>74 min) -0.052.
30. **Trailing from entry (the old ORB bug) would be worse.** CONFIRMED. -0.064 on 6,655 trades.

### Regime and selection
31. **The index filter blocks the good trades.** REJECTED. Without it: -0.052 on 7,429 trades; drift is 0 both for allowed and blocked signals.
32. **Blocking NEUTRAL markets is wrong.** REJECTED (same test).
33. **VIX regime matters.** REJECTED. LOW -0.052, NORMAL -0.056, ELEVATED -0.090, CRISIS +0.12 on 28 trades.
34. **Shorts (or longs) are the problem.** REJECTED. Long -0.059, short -0.052.
35. **Calm names only (std <0.22% of price).** REJECTED. -0.001 overall but +0.015 / +0.049 / -0.098 by period: the best of 69 variants, and it fails the holdout, which is what a best-of-N artifact looks like.
36. **Volatile names ruin it.** REJECTED as a fix. PLTR -0.136, TSLA -0.116, NVDA -0.098, AMZN +0.037, AAPL 0.00 (SE about 0.045 each); no ETFs -0.061. Dropping the losers after the fact is selection.
37. **Big-range days whipsaw it.** SUPPORTED as symptom. Day range <1.1%: +0.011; 2-3%: -0.100. Not known at entry.
38. **It works now, 2026 is better.** REJECTED. 2026: -0.003; the 2026 drift at 5 minutes is +1.4 bps (SE 0.5), smaller than the 3 bps cost.

### Execution and sizing
39. **Market orders pay the spread on every trade.** CONFIRMED (see 2). 6.4 trades a day at about $7 each.
40. **Stop orders slip.** SUPPORTED. 3 bps stop slip: -0.074.
41. **A resting limit at the signal close would fix it.** SUPPORTED as mitigation only. -0.026 (1 bar), -0.024 (3 bars): halves the loss, still negative.
42. **A limit at VWAP would fix it.** SUPPORTED as mitigation only. -0.019 with 55% of signals filled; plus morning only -0.001; plus single 2R target -0.000.
43. **Same-bar stop/target tie is a modelling artifact.** REJECTED. Target-first ordering: -0.055 identical.
44. **Limit targets need trade-through.** Small. -0.059.
45. **1% risk is nominal.** NOTE. The $25k notional cap binds on 99% of trades, so average risk is $127, not $500. Does not change R.
46. **Three concurrent slots add correlated losses.** REJECTED. One slot: -0.047.
47. **Sector cap or correlation.** Not a cause; R is per trade and the sector cap rarely binds.
48. **Fewer signals per symbol-day.** Mitigation only (see 9).
49. **Live sizes differ from the sim.** NOTE. Live COIN 62 shares vs sim 126, so live dollar bleed is about half the sim's; direction unchanged.
50. **The live 20 trades prove it is broken.** REJECTED as proof, CONFIRMED as consistent. 20 trades cannot show a 0.05R effect; the 4,407-trade backtest can, and the live result (-$80) sits inside its expectation.

## If forced to decide: the changes, ranked by conviction

1. **Turn Ride the Trend off (stop new entries).** Highest conviction. This is the only change whose effect is
   certain: it removes a -0.055R ± 0.013 bleed (4 SE), 6.4 trades a day, -$47 a day at sim sizing, and a
   $34k drawdown path. The entry has zero forward drift on 36k signals in every period, and 69 rule changes
   found nothing positive in all three periods. No tweak turns a zero-information entry into a profitable one.
2. **If it must keep trading: stop paying the spread.** Replace the market entry with a resting limit at the
   signal bar's close, cancelled after 3 bars, and trade only the morning window with at most two signals per
   symbol per day. Backtest: -0.006 ± 0.019 (2,366 trades; by period -0.045 / +0.001 / +0.049). This stops the
   bleeding. It does not make money, and I would not claim it does.
3. **Do not act on the 2026 numbers.** The holdout period shows +0.02 to +0.05 with limit entries, on 600 to 1,200
   trades with SE 0.025 to 0.03. That is inside noise. Re-check after another 500 live trades before believing it.

What I would not do: widen stops, change targets, change the trail, add a time stop, remove the index filter,
drop the losing symbols, or keep only calm names. Every one of those was tested above and either did nothing or
was a best-of-N artifact.

## Assumptions and limits

- Costs modelled as 1.5 bps on market fills; real Alpaca paper fills at the NBBO may be worse, which makes the
  live number worse, not better.
- VIX regime uses the daily VIX open; the bot uses a live print. Sizing uses constant $50k equity.
- Other strategies compete for the 3 slots in production; here Ride the Trend had them all, so live trade counts
  are somewhat lower.
- The relay bars are SIP 1-minute, the same feed the bot trades on.

## Files
`sim.py` (backtester), `parity.py` (signal parity proof), `variants.py` (69 variants, results in `data/variants.json`),
`cuts.py` (feature buckets, `data/baseline_cuts.txt`), `crux.py` (forward drift), `fetch_bars.py` (data), `data/` gitignored.
