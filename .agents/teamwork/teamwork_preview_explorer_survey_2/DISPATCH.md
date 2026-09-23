# Dispatch: Explorer 2 (Market Data, Indicators, Signal Qualification & Calendar)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_2`

## Authoritative User Request
Read `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md` (and `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`).

## Mission & Scope
Investigate market data ingestion, indicator calculations, and scheduling in `backend/app/`:
1. Analyze existing data ingestion and indicator computation in `backend/app/ingestion/`, `backend/app/strategies/`, and `backend/app/core/`:
   - Daily bars vs 1-minute bars. How are daily bars fetched or aggregated? Where are historical bars stored or loaded?
   - How can rolling daily calculations be cleanly implemented with zero lookahead bias for:
     a) 200-day Simple Moving Average (SMA).
     b) 60-day relative strength vs `QQQ` ($\Delta_{\text{stock}, 60d} \ge \Delta_{\text{QQQ}, 60d}$).
     c) 2-day Connors RSI (`RSI(2)` < 10.0 on close).
     d) 14-day Daily ATR for emergency stop-loss calculation ($2.5 \times \text{Daily ATR(14)}$).
     e) 5-day SMA for take-profit exit.
   - Lookahead prevention: Verify how indicators strictly use causal closed-session daily data (bars from completed sessions).
2. Universe & Symbols:
   - The 5 certified swing stocks are `LRCX`, `KLAC`, `MU`, `AMD`, `GS` (plus `QQQ` for benchmark RS).
   - How does this interact with `backend/app/config.py` `WATCHLIST_SYMBOLS`?
3. Scheduling & Signal Lifecycle:
   - 16:00 ET close qualification: How does the system detect 16:00 ET market close to run the swing scan and stage orders?
   - 09:30 ET market open execution: How does the engine execute staged orders at the 09:30 market open?
   - Earnings calendar integration: 48-hour blackout window (no entry if earnings within 48h; exit at 09:30 open if holding and earnings tomorrow). What external API or cached provider should be used, and how to structure graceful cached fallback?
4. Document specific file paths, class names, method signatures, data structures, and edge cases.
5. Provide concrete architectural recommendations for implementing R1 & R3 from the dispatch.

## Output Requirements
Write your detailed findings and architectural analysis to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_2/handoff.md`.
Follow the Handoff Protocol: Observation, Logic Chain, Caveats, Conclusion, Verification Method.
When done, send a message back to the caller with a summary and link to your handoff.md.
