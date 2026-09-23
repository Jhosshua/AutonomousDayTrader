# Orchestrator 7 Dispatch Log

## 2026-09-23T21:24:25Z
You are the Project Orchestrator for AutonomousDayTrader.

## Mission
Integrate an autonomous, multi-day swing trading engine ("2-Day Panic Dip" Connors RSI-2 strategy) into `AutonomousDayTrader` across 5 certified stocks (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`). The swing engine shares the $50,000 account pool ($25,000 allocated per slot, maximum 2 concurrent swing positions), runs fully independently from intraday trading (strictly exempt from 15:58 ET auto-flattening), provides a unified Obsidian dark operator dashboard, undergoes 3x independent adversarial review against lookahead/future bias, and completes end-to-end replay verification and remote Railway deployment.

## Context & File Locations
- Project Root: /Users/mo/AutonomousDayTrader
- Your Agent Directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7
- Authoritative User Request: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md (and /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md)
- Subagent Directory Base: /Users/mo/AutonomousDayTrader/.agents/teamwork/

## Execution Requirements
1. **R1: Independent Swing Trading Execution Engine ("2-Day Panic Dip")**:
   - Implement the 7 exact quantitative rules across `LRCX`, `KLAC`, `MU`, `AMD`, `GS`.
   - Rule 1 (Macro Floor): Today's Daily Close strictly above 200-day SMA.
   - Rule 2 (Market Leadership / Relative Strength): Stock performs >= QQQ over trailing 60 trading days ($\Delta_{\text{stock}, 60d} \ge \Delta_{\text{QQQ}, 60d}$).
   - Rule 3 (Panic Trigger): 2-day Connors RSI (`RSI(2)`) closes below 10.0.
   - Rule 4 (Mandatory Earnings Veto): 48-hour blackout window (no entry if earnings within 48h; exit at 09:30 open if holding and earnings tomorrow).
   - Rule 5 (Entry Execution & Sizing): Signal triggers at 16:00 ET close, stages buy order executed at next Market Open (09:30 ET). Sizing: $25,000 notional per slot from shared $50,000 pool, hard cap 2 concurrent swing positions.
   - Rule 6 (Emergency Stop-Loss): Hard stop-loss at $2.5 \times \text{Daily ATR(14)}$ below fill price immediately upon fill.
   - Rule 7 (Take-Profit & Time Exit): Sell at next Market Open (09:30 ET) if prior daily close > 5-day SMA, prior daily RSI(2) > 70.0, or held for 5 trading days.
2. **R2: Strict Architectural Separation & Flattening Exemption**:
   - 4-phase intraday auto-flattening (15:45 lockout, 15:50 cancel, 15:55 liquidation, 15:58 flat audit) applies exclusively to intraday positions.
   - Swing positions, bracket stops, and orders explicitly tagged and exempt from EOD liquidation so overnight holds operate uninterrupted.
   - Risk management coordinates buying power across shared $50,000 pool without margin collision or double-spending.
3. **R3: Market Leadership, Calendar, and Signal Pipeline**:
   - Rolling daily calculations for 200 SMA, 5 SMA, 14 ATR, 60d RS vs QQQ, RSI(2) using strictly causal closed-session data.
   - Automated earnings calendar lookup with graceful cached fallback.
4. **R4: Unified Obsidian Dark Operator Interface**:
   - Next.js / Tailwind Obsidian dark design system: segmented toggle between "Intraday Day Trader" and "Swing Mean-Reversion".
   - Real-time candidate watchlist status, active swing positions table with ATR stop line and holding day counter, operator manual override controls.
5. **R5: 3x Adversarial Review & Zero-Lookahead Audit**:
   - Pass 1 (Mathematical & Lookahead Audit): Zero lookahead bias in RSI(2), ATR(14), 200 SMA, 60d RS vs QQQ, earnings calendar.
   - Pass 2 (State Machine & Flattening Audit): 15:58 ET intraday auto-flattening cannot touch swing positions under any race/edge condition.
   - Pass 3 (Execution Timing & Order Lifecycle Audit): 16:00 qualification vs 09:30 execution, weekend/holiday boundary handling, partial fills, stop triggers, position cap.
6. **R6: Deterministic End-to-End Replay, Visual QA, and Remote Deployment**:
   - Deterministic multi-day historical replay test covering all rules and exit conditions.
   - Visual QA of desktop and mobile views of updated dashboard.
   - Comply with Global Agent Rules: zero lingering processes on ports, push to `origin main`, verify live Railway cloud deployment healthy (`GET /health` 200 OK).
   - Update `PROJECT.md`, `MEMORY.md`, `README.md`.

## Operational Protocol
- Initialize your `BRIEFING.md` and `progress.md` in `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/`.
- Dispatch specialized subagents with dedicated directories under `/Users/mo/AutonomousDayTrader/.agents/teamwork/`.
- Update `progress.md` regularly as milestones advance.
- When all criteria are met, deliver your final handoff report (`handoff.md`) and notify the Sentinel via send_message claiming victory so independent Victory Audit can commence.
