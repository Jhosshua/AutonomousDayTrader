# Execution Sensitivity Analysis

June 12, 2026 — fixed original candidates, Nasdaq displayed depth, existing strategy exits.

| Scale | Placement | Entry shares | Gross P/L | Modeled fees | Net P/L | Valid |
|---|---|---:|---:|---:|---:|---|
| 0 | Passive join | 0 | $0.0000 | $0.0000 | $0.0000 | True |
| 1 | Improve one cent | 225 | Unavailable | $0.6750 | Unavailable | False |
| 2 | Market IOC | 1250 | $109.4200 | $7.5000 | $101.9200 | True |
| 3 | Three-price ladder | 603 | Unavailable | $1.8090 | Unavailable | False |

Conditional sensitivity: treat a crossed A/F residual as an executable incoming limit order. These fills depend on that additional assumption.

| Scale | Entry shares | Gross P/L | Modeled fees | Conditional net P/L | Valid within scenario |
|---|---:|---:|---:|---:|---|
| 0 | 0 | $0.0000 | $0.0000 | $0.0000 | True |
| 1 | 491 | $228.1800 | $2.1480 | $226.0320 | True |
| 2 | 1250 | $109.4200 | $7.5000 | $101.9200 | True |
| 3 | 757 | $346.7600 | $4.0800 | $342.6800 | True |

Scale 2 minus Scale 0 net P/L: **$101.9200**.
First profitable tested scale: **2**.
Four discrete policies cannot identify an exact continuous threshold; the ladder is not an ordinal aggression step.

| Symbol | Scale | Scenario | Entry VWAP | Exit VWAP | Net P/L | Exit reason / limitation |
|---|---:|---|---:|---:|---:|---|
| TSLA | 0 | strict | Unavailable | Unavailable | $0.0000 | UNFILLED |
| TSLA | 0 | residual_add | Unavailable | Unavailable | $0.0000 | UNFILLED |
| TSLA | 1 | strict | Unavailable | Unavailable | $0.0000 | UNFILLED |
| TSLA | 1 | residual_add | Unavailable | Unavailable | $0.0000 | UNFILLED |
| TSLA | 2 | strict | $396.6900 | $400.2174 | $-402.8040 | STOP, STOP |
| TSLA | 2 | residual_add | $396.6900 | $400.2174 | $-402.8040 | STOP, STOP |
| TSLA | 3 | strict | Unavailable | Unavailable | $0.0000 | UNFILLED |
| TSLA | 3 | residual_add | Unavailable | Unavailable | $0.0000 | UNFILLED |
| CDE | 0 | strict | Unavailable | Unavailable | $0.0000 | UNFILLED |
| CDE | 0 | residual_add | Unavailable | Unavailable | $0.0000 | UNFILLED |
| CDE | 1 | strict | $16.7900 | Unavailable | Unavailable | COUNTERFACTUAL_CROSSED_BOOK, MODEL_AMBIGUOUS, PROTECTION_UNAVAILABLE:MODEL_AMBIGUOUS |
| CDE | 1 | residual_add | $16.7900 | $17.2547 | $226.0320 | TIME_LIMIT |
| CDE | 2 | strict | $16.7987 | $17.2490 | $504.7240 | TIME_LIMIT |
| CDE | 2 | residual_add | $16.7987 | $17.2490 | $504.7240 | TIME_LIMIT |
| CDE | 3 | strict | $16.7963 | Unavailable | Unavailable | COUNTERFACTUAL_CROSSED_BOOK, MODEL_AMBIGUOUS, PROTECTION_UNAVAILABLE:MODEL_AMBIGUOUS |
| CDE | 3 | residual_add | $16.7950 | $17.2531 | $342.6800 | TIME_LIMIT |

Execution assumptions and interpretation:

- Two frozen original signals; same candidate times, stops and requested quantities in all four independent variants. Toxicity diagnostics do not change this placement-only experiment.
- This is fixed-candidate execution attribution with complete per-position exits. Candidate sizing is frozen, so no new capital/risk-based resizing of later signals is performed. Aggregated P/L is not a separate full-strategy risk-admission experiment.
- Scale 0 joins the original own-side price, post-only. Scale 1 improves it by $0.01 and permits taking. Scale 2 is market IOC against available displayed depth. Scale 3 partitions the same total quantity across original, $0.01 and $0.02 improvements; child orders share one depth budget.
- Original research latency: input feed 10 ms already embodied in frozen candidates; compute 1 ms; outbound 250 ms; report 10 ms; cancel compute 1 ms plus outbound 250 ms. Entry expiry stays one second after decision.
- Actual entry VWAP sets R. TSLA splits floor/ceil halves at 1.5R/2R and 180/240-minute limits; CDE uses 2R and 180 minutes. Existing stop-price rounding and invalid-fill risk tolerance are retained. Time limits are capped at 15:55 ET.
- Protection is modeled as venue-local OCO after normal submission latency: resting target, tape-price-triggered stop, ordered local cancel then reduce-only market child. Native stop processing delay is zero in this scenario. Time exits wait for cancel acknowledgment and incur normal submission latency. This is not verified Alpaca routing behavior.
- Net P/L equals simulated entry/exit cash flows minus assumed $0.003 per taker share per leg and $0 per maker share. Gross P/L is the zero-fee sensitivity. Spread and depth slippage are already in execution prices, so they are not deducted twice.
- Fees are research assumptions, not verified June 2026 broker charges. Borrow/locate, regulatory fees, broker commissions, financing and cross-venue routing are unmodeled; reported net is conditional on those costs being zero.
- Only XNAS displayed liquidity is executable here. No hidden-liquidity fills, no guaranteed market completion and no market-impact feedback. Historical source events remain exogenous; own consumed liquidity cannot be reused within a branch. Ambiguous passive matching invalidates the branch.
- Strict mode invalidates crossed counterfactual books. A separate residual_add sensitivity assumes a displayed A/F residual is an executable incoming limit order: at most its observed quantity can cross our resting orders before the unconsumed remainder rests. This is an explicit unverified order-type assumption, not a proven historical fill. Added-demand budgets are recorded separately from actual tape execution budgets.
- Execution semantics are checked for our reachable price range. Outside that range the observed referenced external depletion is replayed without claiming an own fill; both original and execution prices bound the conservative reachability check for C messages.
- Nasdaq P prints remain unknown-sign flow. Eligible print prices may trigger an existing stop without being assigned an aggressor or supplying executable depth.
- This development session contains two candidates. It cannot establish a profitable general policy, calibrated probability or a continuous optimal aggression threshold. Scale 3 is a mixture, not a monotone step above Scale 2.

Verification:

- Full normalized stream reconciled: True; 34,290,245 source events.
- Original Scale 0 order quantities, prices, arrival times, cancellations, queue positions and fills matched: True.
- All own-order share ledgers and source/market-command execution budgets reconcile. Residual or invalid outcomes have no reported net P/L.
- Runtime: 414.02 seconds. Input, strategy, replay-code and result hashes are in the JSON report.

Fee background: [Nasdaq price list](https://www.nasdaqtrader.com/trader.aspx?id=pricelisttrading2).
Order semantics background: [Nasdaq OUCH specification](https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf).
