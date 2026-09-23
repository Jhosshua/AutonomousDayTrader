# Dispatch: Explorer R6-3 (Risk Boundaries, Multi-Sector Collisions & API/UI State Sync)

## Identity
- Role: Explorer (Read-only exploration & analysis)
- Working Directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui
- Project Root: /Users/mo/AutonomousDayTrader
- Authoritative User Request: /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
- Scope Document: /Users/mo/AutonomousDayTrader/PROJECT.md

## Objective
Execute an exhaustive, adversarial review of the risk engine knife-edge boundaries, multi-sector concentration limits under simultaneous signal collisions, and API / WebSocket UI state serialization across the 12-symbol universe.

## Scope & Target Code
- `backend/engine/risk.py`
- `backend/engine/account.py`
- `backend/engine/bracket_manager.py`
- `backend/engine/execution.py`
- `backend/api/server.py`
- `backend/api/ws.py`
- `backend/api/serializer.py`
- `frontend/app/error.tsx`
- `frontend/components/NowPlayingDrawer.tsx`
- `frontend/hooks/useWebSocket.ts`
- `frontend/components/PortfolioOverview.tsx`

## Attack Angles to Investigate
1. Risk Engine Knife-Edge Boundaries & Float Precision:
   - Check floating point leaks and boundary conditions on stop-loss distance bounds: $[0.0040, 0.0400]$ ($40$ to $400$ bps). Are boundaries strictly enforced with epsilon tolerances?
   - Daily circuit breaker ($1,500 drawdown) and single-position notional cap ($25,000 / 50% equity). Can any order sneak through right at the edge of the circuit breaker?
   - Multi-Sector Concentration Caps: Max 2 positions per sector, max 3 concurrent positions total. What happens under simultaneous signal collisions across 12 tickers (e.g., AAPL and NVDA and AMD signals arriving in the same microsecond tick)? Is sector allocation atomic and thread-safe?
   - 4-Phase EOD Auto-Flattening protocol: 15:45 lockout, 15:50 order purge, 15:55 liquidation, 15:58 flat audit. Are there race conditions where working orders are not canceled before market orders are placed?
2. API & UI State Synchronization:
   - WebSocket payload serialization safety with 12 active tickers: Are there NaN, Infinity, or missing field exceptions in JSON serialization?
   - Rate limiting, broadcast throttling, and disconnect handling for slow consumers.
   - Frontend error boundaries (`error.tsx`), empty states, and drawer responsiveness under rapid state transitions.

## Deliverables
- Write detailed analysis with code references and concrete findings to `analysis.md`.
- Formulate concrete production-grade fix recommendations and deterministic mutation test designs.
- Deliver `handoff.md` with explicit verdicts and recommendations.

## 2026-09-23T20:10:13Z
You are Explorer R6-3.
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui
Read your dispatch file at /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui/DISPATCH.md
Read the authoritative user request at /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
Read the project document at /Users/mo/AutonomousDayTrader/PROJECT.md

Investigate risk engine knife-edge boundaries, multi-sector concentration limits under simultaneous signal collisions, and API / WebSocket UI state serialization:
- Inspect backend/engine/risk.py, backend/engine/account.py, backend/engine/bracket_manager.py, backend/engine/execution.py, backend/api/server.py, backend/api/ws.py, backend/api/serializer.py, frontend/app/error.tsx, frontend/components/NowPlayingDrawer.tsx, frontend/hooks/useWebSocket.ts, frontend/components/PortfolioOverview.tsx.
- Check floating point leaks and boundary conditions on stop-loss distance bounds [0.0040, 0.0400], daily circuit breaker ($1,500 drawdown), single position cap ($25,000 / 50% equity).
- Check multi-sector concentration cap (max 2/sector, max 3 concurrent) under simultaneous signal collisions across 12 tickers.
- Check 4-phase EOD auto-flattening protocol races (15:45 lockout, 15:50 purge, 15:55 liquidation, 15:58 flat audit).
- Check WebSocket payload serialization safety with 12 active tickers (NaN, Infinity, missing fields, rate limiting, slow consumer eviction).
- Check frontend error boundaries and drawer responsiveness.
- Document any latent defects, edge case vulnerabilities, or serialization failures.
- Propose concrete production-grade fix strategies and deterministic mutation test designs.
- Write your findings to /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui/analysis.md and deliver handoff.md.
- Send a completion message to the parent orchestrator via send_message.

