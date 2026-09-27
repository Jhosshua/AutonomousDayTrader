# Ride the Trend v2: step-0 funnel diagnostics on three real sessions (2026-09-23/24/25)

Source: `scripts/run_ride_the_trend_v2_dry_run.py` replaying every SIP print, NBBO quote, stock bar and the seven regime ETF bar streams through the live handlers. Every value below was recorded at decision time by the evaluator (`RESUMPTION_MEASURED`), not reconstructed afterwards.

- Resumption evaluations: 22. Impulses that pre-empted a live setup: 1.
- Feasible entry price interval (slope >= 0.5 ATR/bar off the low versus the no-chase cap 0.5 std above VWAP): {'True': 8, 'False': 14}. The two existing rules leave no legal entry price on 14 of 22 evaluations.
- Would fail if enforced, per evaluation (nothing is enforced today): {'IMPULSE_DELTA': 18, 'RESUMPTION_DELTA': 10, 'ROLLING_DELTA': 8, 'SECTOR_AGAINST': 3, 'SECTOR_RS_FILTER': 4}. Unavailable: {'RESUMPTION_DELTA': 4}.
- Quote-classified share of volume 09:45-11:30 (whole-morning audit tape, no thresholds): [{"AAPL": 0.411, "NVDA": 0.439, "AMD": 0.244, "MSFT": 0.38, "AMZN": 0.37, "META": 0.38, "GOOGL": 0.297, "PLTR": 0.33, "COIN": 0.283}, {"AAPL": 0.424, "NVDA": 0.481, "AMD": 0.271, "MSFT": 0.368, "AMZN": 0.404, "META": 0.322, "GOOGL": 0.38, "PLTR": 0.276, "COIN": 0.286}, {"AAPL": 0.429, "NVDA": 0.458, "AMD": 0.265, "MSFT": 0.351, "AMZN": 0.396, "META": 0.339, "GOOGL": 0.425, "PLTR": 0.303, "COIN": 0.293}]

Reading: impulse aggression at +0.15 would reject 18 of 22 (most impulse bars show |delta| between 0.04 and 0.13); resumption aggression at +0.10 about half; the rolling 30-minute delta about a third; sector direction 3 and sector relative strength 4. These are per-evaluation observations, not an enforcement replay (enforcing one gate removes the later evaluations of the same setup). Thresholds are for the operator to set from more sessions.

Every evaluation row is in `docs/ride_the_trend_v2/dry_run_<date>.json` under `diagnostics.rows`.

## Add-ons (enforced live from 2026-09-28), per-evaluation observations on the same 22 evaluations
- Would fail: {'IMPULSE_DELTA': 18, 'RESUMPTION_DELTA': 10, 'ROLLING_DELTA': 8, 'SECTOR_AGAINST': 3, 'SESSION_DELTA_AGAINST': 15, 'HVN_NO_SUPPORT': 17, 'HVN_OVERHEAD': 6, 'SECTOR_RS_FILTER': 4, 'SPREAD_WIDE': 2}. Unavailable: {'RESUMPTION_DELTA': 4, 'SPREAD': 1}.
- Session cumulative delta was against the trade on 15 of 22 evaluations (the longs mostly ran into a day of
  net selling; the PLTR shorts on 09-23 ran into net buying).
- Spread: 2 of 22 above 1.5x the trailing median (both PLTR 09-23, 1.83x and 1.57x); 1 unavailable.
- Volume profile: nodes per stock per day {"2026-09-25": {"AAPL": 2, "NVDA": 4, "AMD": 4, "MSFT": 1, "AMZN": 4, "META": 6, "GOOGL": 5, "PLTR": 7, "COIN": 3}, "2026-09-24": {"AAPL": 4, "NVDA": 5, "AMD": 5, "MSFT": 1, "AMZN": 3, "META": 5, "GOOGL": 5, "PLTR": 5, "COIN": 6}, "2026-09-23": {"AAPL": 2, "NVDA": 7, "AMD": 6, "MSFT": 2, "AMZN": 4, "META": 5, "GOOGL": 5, "PLTR": 3, "COIN": 10}}. The pullback extreme sat on a prior node
  on 5 of 22 evaluations; a node blocked the road to the first target on 6.
These are observations at evaluations the earlier gates had already rejected; they are not an enforcement
replay. With all gates enforced, none of the three sessions produced a signal.
