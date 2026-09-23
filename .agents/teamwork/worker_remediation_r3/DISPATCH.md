## 2026-09-23T15:12:48Z
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

You are Worker Remediation. Your working directory is /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation_r3/
Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md and /Users/mo/AutonomousDayTrader/PROJECT.md before beginning.
Also read the exhaustive findings from the 3 Explorers:
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_ingestion_core/handoff.md (and analysis.md)
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_strategies_adaptation/handoff.md (and analysis.md)
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_api_lifecycle_frontend/handoff.md (and analysis.md)

Your mission is to implement clean, minimal, production-grade remediations for all identified defects across Ingestion, Core/Risk, Strategies, API/Lifecycle, and Frontend:

1. Ingestion:
   - backend/app/ingestion/news_ws.py: Add max_size=settings.WS_MAX_MESSAGE_SIZE_BYTES to websockets.connect; wrap each news item in try...except in message processing loop to isolate malformed items.
   - backend/app/ingestion/stock_ws.py: Add outer try...except in _process_queue_loop to prevent silent task death.

2. Core State & Risk:
   - backend/app/core/engine.py: In process_quote, add a break after _execute_fill on a STOP/STOP_LIMIT order (matching process_bar), preventing sibling limit fills on the same quote. Also bound self.audit_log and self.orders in session reset or periodic maintenance.
   - backend/app/core/bracket.py: In manual_tighten_stop, clamp stop price so a BUY stop cannot be tightened above current market price and a SELL stop cannot be tightened below current market price.
   - backend/app/core/flattening.py: In check_time_tick, ensure Phase 4 zero-audit directive continues retrying every tick/interval if not self.audit_passed between 15:58:00 and 16:00:00 ET until flat.
   - backend/app/strategies/adaptation.py: In calculate_adapted_stop, strictly clamp adapted stop distance to institutional bounds [0.0042, 0.0380] (or [min_stop_distance_pct + 0.0002, max_stop_distance_pct - 0.0002]), guaranteeing that VIX multipliers (0.85 to 2.00) never breach the [0.0040, 0.0400] risk engine invariant. Reconcile max allocation cap with risk.py ($25,000 / 50% equity).

3. Strategies:
   - backend/app/strategies/news_momentum.py: Enforce strict causality in on_bar: 0 <= (now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds, eliminating forward data leakage. In recent_bars, maintain a sliding window (e.g. self.recent_bars[sym] = self.recent_bars[sym][-60:]) to prevent memory leaks.
   - backend/app/strategies/vwap_pullback.py: Replace obsolete 1.5R and 2.5R fallback targets with calibrated 0.80R and 1.80R. Enforce volume floor (bar.volume > 0 and sma10_vol > 0) to prevent false bounces on zero volume. Enforce minimum reward ratio (>= 0.50R) on band targets.
   - backend/app/strategies/orb.py: Prevent premature symbol lockout if downstream signal admission rejects the order. Ensure opening range cannot be spuriously seeded on late arriving symbols outside 09:30-09:45 ET.

4. API & Lifecycle:
   - backend/app/main.py:
     * Throttle broadcast_ui_state (e.g. minimum 250ms interval / 4 Hz, or decouple from high-frequency raw quote arrivals). Send with asyncio.wait_for(ws.send_text(raw), timeout=0.35) and discard timed-out clients to prevent slow-consumer event loop blocking.
     * In manual flatten (_execute_manual_flatten / flatten_positions): ensure target_symbols includes account.positions.keys(), all symbols from engine.working_orders, and all symbols in bracket_manager.symbol_to_bracket. Cancel all working orders in engine.working_orders for those target symbols.
     * In POST /api/orders: validate qty > 0 on request model and catch ValueError from engine.create_order, returning HTTP 400 instead of HTTP 500.
     * In lifespan shutdown: iterate and close all connected ui_clients with code 1001.

5. Frontend & UI:
   - Replace unsafe .toFixed(2) and .toLocaleString() calls with safe null-checking helpers across LiveChart.tsx, ActivePositionTray.tsx, and ManualControls.tsx.
   - In ManualControls.tsx, ensure the "Flatten All Portfolios" confirmation modal is renderable and accessible even when position is null.
   - In frontend/hooks/useTradingStream.ts, add /api/account and /api/positions polling when disconnected, and ensure synchronous errors in connect() schedule reconnectTimeoutRef.
   - In LiveChart.tsx and StrategyCarousel.tsx, update obsolete 1.5R / 2.5R text to 0.80R / 1.80R.
   - Create frontend/app/error.tsx with obsidian dark theme and retry button.

6. Verification:
   - Update scripts/verify_port_hygiene.sh to include port 8000: PORTS=(3005 8000 8005 8080).
   - Write comprehensive unit tests for all remediated defects in backend/tests/.
   - Run pytest backend/tests -v and confirm 100% pass rate.
   - Run python3 tests/e2e/runner.py and confirm 100% pass rate.
   - Check frontend build or typescript compilation if possible.

Document your changes in /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation_r3/changes.md.
Deliver your completion report in /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation_r3/handoff.md including full test execution outputs.
When done, message orchestrator_4 that your handoff is ready.
