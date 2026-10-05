# Impact Factor (dPrice / Volume) sweep: NO-GO

2026-09-27, Claude Code. One frozen sweep, protocol hashed before profits were joined (`frozen_hashes.txt`).

## Answer
There is no Price Impact signal that makes money in this data. Bionic's exact idea ("price moves hard on almost no volume") is one of the worst groups.

## Numbers
- Data: 12 Nasdaq ITCH sessions, 373 candidates, 52 stocks, existing exits, $0.003/share taker fee, 251 ms latency (Codex's frozen outcomes).
- Baseline, all 373 candidates: market -$47,882, +2c ladder -$20,430, passive -$1,035 (17 fills).
- Rule family: 536 distinct rules (impact in $/share, bps per $10k, |impact|, volume; 100ms/1s/5s; decile grid; HILV = impact AND low volume). Floor: 10+ trades on 3+ days.
- Best eligible rule per entry style: passive $0 (never filled), ladder -$261, market -$2,025. Luck-adjusted p = 1.0 for all.
- Walk-forward (pick on 8 old days, test on 4 new): ladder -$660, market -$1,993 out of sample.
- Sensitivity ignoring the unknown-trade exclusion: 6 of 536 rules net positive (all ladder, best +$185 on 10 trades). That is what luck produces with 536 tries.
- Literal HILV (price moved our way on ZERO shares in 1s): 29 trades, market -$5,487, ladder -$1,113.
- Cash verified three ways (report, integer sums, raw ledgers): identical to the cent.

## Codex's price-velocity sweep (same night, same data)
END_OF_ROAD_FOR_THIS_DATA. 124,888 rule groups; best +$2,958 on 5-6 trades, luck-adjusted p 0.87 / 0.11. 0 rules qualified.

## Why (plain)
A price move on no volume is mostly a quote being pulled (cancellations), not an institution trading. By the time a 251 ms bot reacts, the move is over and we pay the spread plus fees. Every variable tried on this archive (lambda/T, depth, velocity, spread, impact) loses before fees on average, so a filter can only pick lucky subsets.

## Stop rule
This closes the feature-hunt on this archive. Another variable swap would be data snooping. The next evidence must be new data (forward paper), not another sweep of these 373 trades.
