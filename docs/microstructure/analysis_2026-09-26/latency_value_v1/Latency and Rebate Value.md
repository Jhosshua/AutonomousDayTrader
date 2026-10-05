# What would a faster, rebate-earning machine be worth? (2026-09-27, Claude Code)

Same 373 candidates, same entries/exits/sizes, only order + cancel delivery time changed (PolicyReplay outbound/cancel latency). 250ms rerun reproduced Codex's frozen results exactly (12/12). Compared on candidates valid at all three speeds.

| Entry style | 250ms | 50ms | 1ms | Rebate at $0.002 on every maker share (1ms) |
|---|---|---|---|---|
| Market | -$50,421 | -$50,360 | -$50,570 | ~$38 |
| +2c ladder | -$21,358 | -$21,760 | -$21,775 | ~$61 |
| Passive post | -$1,035 (17 fills) | -$1,198 (14) | -$963 (18) | ~$22 |

Also: across all trades, price movement caused ~95% of the loss (market gross -$45.8k vs fees $2.1k).

Verdict: speed and rebates do not rescue these trades. The loss comes from the trade ideas (direction over a multi-minute hold), not from execution. Rebates cannot be earned on Alpaca at all (it takes payment for order flow; the customer gets no rebate). IBKR Pro Tiered passes exchange rebates through (~$0.002/share typical).
A rebate-capture machine is a different business (two-sided market making), where queue position (microseconds) decides who gets paid. Not tested yet.
