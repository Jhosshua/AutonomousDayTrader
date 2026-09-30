# Ten stock day trade patterns, report (2026-09-30)

Protocol PLAN.md, frozen before any result (SHA-256 in FROZEN.txt). Window 2024-09-30 to 2026-09-29. Design year to 2025-09-30, holdout year after. One minute SIP bars from AlpacaRelay.

## The answer

No day trade rule survived on any of the ten names. Of 1,598 pre registered tests, none reached the design bar (t of 2 with profit factor 1.2), so nothing went to the holdout. By chance about 30 tests should clear t of 2. Instead 279 landed below negative 2. The walk forward record picked a rule in 4 of 60 name quarters and lost or broke even every time. The operator's live TSLA morning plan, run unchanged on these ten names, loses on every one.

The reason is simple. Before costs, the typical intraday rule on these names earns 0 to 5 bps a trade. Realistic costs are 6 to 12 bps. Over these two years the money in these names was made overnight, not during the session.

## Report card

| Name | Patterns that held the same sign in both years (description only) | Closest rule (failed) | Verdict |
|---|---|---|---|
| TQQQ | Second year gain came overnight (+48 log points overnight, negative 8 intraday). Monday open to close up (+56 and +58 bps), Thursday down (negative 69 and 37). Turn of month up | VWAP stretch fade, design t 1.58, holdout lost | No tradable day rule after cost |
| QQQ | Same shape as TQQQ at one third the size. Monday +17 and +19 bps, Thursday negative 22 and 12 | Relative value vs SPY, design t 0.53 | No tradable day rule |
| PLTR | Huge intraday gains in year one (+122 log points), intraday losses in year two (negative 18). Turn of month up (t 2.5 and 1.0). 7 earnings style event days | 15 minute breakout, midpoint stop, 2R, design t 1.14 | No tradable day rule |
| MSTR | Lost money intraday both years (negative 56 and 78 log points), all gains overnight. Thursday down (negative 159 and 70 bps), Friday up. 14:30 half hour negative | Midday range break, design t 1.89, holdout lost 15 bps a trade | No tradable day rule |
| COIN | Lost intraday both years. Tuesday and Thursday down, Monday and Friday up. Late afternoon half hours negative | Bitcoin follow at 09:35, design t 0.88, holdout +59 bps a trade (p .082) | No rule passed. Best forward test lead of the study (hindsight, see below) |
| META | Intraday negative both years, overnight positive. 8 event days | Gap fade, design t 1.38, holdout lost | No tradable day rule |
| VRT | Second year gain came overnight (+90 log points vs negative 40 intraday). Thursday down. Turn of month up | Volume shock follow 15 minutes, design t 1.41 | No tradable day rule |
| AMD | Second year gain split overnight +94 and intraday +38. 15:30 half hour up (t 2.2 and 1.3) | Gap and go, design t 1.04 | No tradable day rule |
| SPY | Monday up, Thursday down, turn of month up (t 1.1 and 2.6). Gaps rarely fill by 10:30 (11% to 13%) | Volume shock follow 60 minutes, design t 0.79 | No tradable day rule |
| SMH | Monday and Wednesday up, Thursday and Friday down | Relative value vs SPY at 13:00, design t 1.27, holdout +8 bps (p .24) | No tradable day rule |

## Side by side, closest rule per name (all failed the design gate)

Two year figures at normal cost, slash 2x cost where shown. They include the design year the rule was picked from, so they flatter the rule. $10,000 scale, full equity a trade, no margin. A real account needs $25,000 for pattern day trader rules.

| | TQQQ | QQQ | PLTR | MSTR | COIN | META | VRT | AMD | SPY | SMH |
|---|---|---|---|---|---|---|---|---|---|---|
| What it does | VWAP stretch fade | Fade gap to SPY at 11:00 | 15 min breakout | 12:00 to 13:30 range break | Follow bitcoin's night move | Gap fade | Follow 10x volume bar | Gap and go | Follow 10x volume bar | Fade gap to SPY at 13:00 |
| Median hold | 82 min | 120 min | 87 min | 98 min | to close | 96 min | 15 min | 37 min | 60 min | 179 min |
| Trades a week | 1.1 | 1.5 | 4.4 | 3.9 | 1.4 | 2.0 | 1.5 | 2.0 | 1.3 | 1.5 |
| Win rate | 54% | 45% | 40% | 40% | 58% | 53% | 41% | 48% | 41% | 53% |
| Profit factor | 0.99 / 0.86 | 0.89 / 0.64 | 1.04 / 0.94 | 1.08 / 0.96 | 1.42 / 1.34 | 0.98 / 0.79 | 1.19 / 1.03 | 1.11 / 0.96 | 1.26 / 0.93 | 1.60 / 1.37 |
| Design year t (needed 2.0) | 1.58 | 0.53 | 1.14 | 1.89 | 0.88 | 1.38 | 1.41 | 1.04 | 0.79 | 1.27 |
| Holdout p (unseen year) | .97 | .84 | .82 | .95 | .08 | .92 | .77 | .46 | .53 | .24 |
| Stop loss | Yes | No | Yes | Yes | No | Yes | No | Yes | No | No |
| Direction | Both | Both | Both | Both | Both | Both | Both | Both | Both | Both |
| Profit a trade | negative 0.3 bps | negative 2.0 | +2.6 | +4.7 | +50.1 | negative 1.1 | +7.1 | +8.7 | +4.5 | +18.2 |
| Profit a year | negative $59 | negative $161 | +$234 | +$720 | +$3,637 | negative $177 | +$482 | +$717 | +$275 | +$1,365 |
| Worst drop | 17.3% | 8.4% | 23.9% | 38.5% | 29.3% | 21.9% | 9.8% | 28.0% | 5.2% | 7.2% |
| Profit per $1 of worst drop | negative 0.03 | negative 0.19 | 0.10 | 0.19 | 1.47 | negative 0.08 | 0.51 | 0.27 | 0.54 | 2.03 |
| Deflated Sharpe | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

The live TSLA morning plan on these names (benchmark, unchanged rules, long only, about 2 trades a week). Profit factor over two years was TQQQ 1.03, QQQ 0.78, PLTR 0.98, MSTR 0.78, COIN 0.92, META 0.74, VRT 0.96, AMD 0.90, SPY 0.66, SMH 0.92. $10,000 became $10,052 on TQQQ and $6,078 to $9,557 on the other nine.

## Why nothing passed

| Family | Median gross a trade | Median cost a trade | Median design t |
|---|---|---|---|
| F1 opening range breakout | +5.5 bps | 8.2 | negative 0.35 |
| F2 bitcoin lead (MSTR, COIN) | negative 14.1 | 7.5 | negative 0.88 |
| F3 half hour periodicity | negative 2.0 | 6.2 | negative 2.95 |
| F4 volume shock bars | +1.4 | 6.0 | negative 0.60 |
| F5 prior day candle | +0.5 | 9.0 | negative 0.59 |
| F6 midday range break | +0.2 | 6.9 | negative 0.91 |
| E1 gap | +0.3 | 11.7 | negative 1.10 |
| E2 VWAP stretch | +0.7 | 8.3 | negative 2.51 |
| E3 failed prior day level | +0.5 | 7.7 | negative 1.31 |
| E4 late day momentum | +1.0 | 6.0 | negative 1.16 |
| E5 relative value | negative 1.9 | 6.0 | negative 1.05 |

Cost is not what hides an edge. The audit found only 16 of 1,174 screened tests reach t of 2 even at zero cost, which is at or below chance. On the index funds, about a quarter of configs were positive before cost in both years (SPY 20 of 94), which is what coin flips give.

Power. With one year of holdout, the smallest per trade Sharpe the test can find 80% of the time is 0.35 at 1 trade a week and 0.18 at 4 a week. The edge hunt quality reference (53% winners, profit factor 1.3) is about 0.13, which would need roughly 7 trades a week to detect. So for rules under about 3 a week the honest reading is "not enough trades to tell". The design year still rules most of them out, because 279 tests were significantly negative.

## Walk forward record (without luck)

Anchored, refit each quarter on all prior data, whole menu of 1,440 name configs, objective t. Picks were TQQQ opening range in 2025 Q2 (flat), MSTR midday range break in 2025 Q2 and Q3 (negative 6.3% and 4.1%), META gap fade in 2025 Q3 (negative 1.0%). All ten names sat out the four holdout quarters because nothing qualified. Pooled holdout p 1.0.

Sensitivity only. Profit factor as the objective gave the same picks. A trailing six month window picked 8 name quarters and made a small profit, mainly the bitcoin follow rule on COIN and MSTR in 2026 Q2 and Q3 (+2.3% to +13.4% a quarter), pooled p .083. That misses the .05 bar and was not the declared primary spec.

## Leads for a forward paper test (hindsight, not clean)

These were not rules in the protocol, or they failed its gates. Each was noticed by looking at both years, so any test on this data is in sample. Only a forward paper period can test them cleanly.

1. COIN bitcoin follow. At 09:35, if bitcoin's move since the prior close (scaled by the square root of hours) is beyond 1 sigma of its last 20 days, trade COIN in bitcoin's direction and hold to the close. Holdout +59 bps a trade, profit factor 1.56, p .082, but the design year said no (t 0.88).
2. Monday up, Thursday down open to close on SPY, QQQ, TQQQ and SMH, with t between 0.5 and 1.7 in each year. About 1 trade a week each way.
3. Turn of month (last session plus the first two) open to close was positive in both years on 9 of 10 names.

## Deviations from the protocol (found by the audit, none can change the result)

1. The relative value family measured COIN against XLF, the edge hunt used SPY. Rerun against SPY, all 12 configs stay negative (t negative 0.78 to 3.52).
2. The neighbour rule counted only neighbours that passed the frequency screen. The code also treated E1 exit time, E3 target and E5 exit as ordinal. No config reached t of 2, so no effect.
3. The F2 rule "drop the family if over 20% of days are skipped" was not coded. The measured skip rate was 0.4%.
4. Effective trial count (783) and the Sharpe variance for the deflated Sharpe used screened configs only, the plan said all. Description only.
5. Four configs had design t above 2 but traded under once a week and were screened out as the protocol requires (TQQQ expansion fade, COIN inside day fade, META 1.5% gap fade, VRT VWAP stretch with 3 trades).
6. FOMC dates for 2026 in the atlas are from memory and were not checked against the Fed calendar.

Robustness checks for passing rules (cost stress, cadence, transfer) were not run, because nothing passed.

## Files

PLAN.md protocol and the 45 reviewer findings with answers. FROZEN.txt hash. fetch.py and fetch_daily.py data pulls. core.py data, fills, costs, statistics. fam.py families. run_all.py trades for every config. discover.py design selection. holdout.py holdout step, nearest misses, TSLA benchmark, deflated Sharpe, bootstrap. walkforward.py. atlas.py. test_study.py (13 tests, including hand parity for F1 and F5). Outputs in data/ (gitignored).
