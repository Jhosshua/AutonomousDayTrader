# Handoff Report: Backend Architectural Audit

**Agent**: teamwork_preview_explorer_arch_audit_1 (Backend Architectural Auditor)  
**Date**: 2026-09-20  
**Type**: Hard Handoff (Task Complete)  

---

## 1. Observation

A full read-only inspection was performed across the backend codebase (`backend/app/` and `backend/tests/`). The following specific conditions were directly observed:

1. **Target 2 Partial Fill Unprotected**:
   In `backend/app/core/bracket.py` lines 342–353:
   ```python
   elif child_type == BracketChildType.TAKE_PROFIT_2:
       bracket.target_2_filled = True
       bracket.remaining_qty -= filled_qty
       bracket.status = BracketStatus.COMPLETED_PROFIT
       self.symbol_to_bracket.pop(bracket.symbol, None)
       return BracketUpdateDirective(
           action="CANCEL_ORDER",
           orders_to_cancel=[bracket.stop_order_id] if bracket.stop_order_id else [],
           bracket_status=BracketStatus.COMPLETED_PROFIT,
       )
   ```
   When `filled_qty < bracket.remaining_qty`, `remaining_qty` is positive, but the stop loss order is cancelled and the bracket is marked `COMPLETED_PROFIT`.

2. **Bracket Manager `order_to_bracket` Leak**:
   In `backend/app/core/bracket.py` lines 298, 312, 346, and 467, `self.symbol_to_bracket.pop(...)` is called upon bracket completion or flattening, but entries in `self.order_to_bracket` for child orders (`stop_order_id`, `target_1_order_id`, `target_2_order_id`) are never removed.

3. **`manual_tighten_stop` Behavior**:
   In `backend/app/core/bracket.py` lines 426–441, `manual_tighten_stop` emits `MODIFY_ORDER` even when `new_stop_price` does not tighten the stop or when the bracket is in `PENDING_ENTRY` (where no working stop order exists yet).

4. **Strategy Stop Distance Geometry Inversion**:
   In `backend/app/strategies/orb.py` line 173 (`risk < 0.05`) and `backend/app/strategies/news_momentum.py` line 238 (`stop_loss = round(bar.low - 0.02, 4)`), the calculated stop distances are not clamped to `entry_price * 0.004`. If `entry_price = 200.0`, a 20-cent stop is 0.10% (< 0.40%), which is rejected by `InstitutionalRiskEngine` (`backend/app/core/risk.py` line 219).

5. **`stock_ws.py` Queue Processing Asymmetry**:
   In `backend/app/ingestion/stock_ws.py` line 249, `self._queue.task_done()` is inside the `try` block. If `json.loads` or parsing fails, `task_done()` is bypassed, preventing clean queue draining on shutdown.

6. **Lingering Working Orders at Session Boundary**:
   In `backend/app/main.py` lines 391–404 (`_check_session_boundary`), bracket and strategy states are cleared, but `engine.working_orders` is not purged.

7. **Config Default Discrepancy**:
   `backend/app/core/risk.py` line 43 defaults `max_position_equity_pct = 0.500`, whereas `backend/app/config.py` line 78 and `backend/app/main.py` line 49 set it to `1.000` ($50,000 max single position).

8. **Test Suite Baseline**:
   Execution of `pytest backend/tests` resulted in `140 passed in 0.73s`.
   Execution of `pytest tests/e2e` resulted in `293 passed in 22.83s`.

---

## 2. Logic Chain

1. From Observation 1, if an order for Target 2 receives a partial fill (common in real-market execution when order size exceeds liquidity or participation caps), `bracket.remaining_qty` remains > 0. Because lines 346–352 cancel `bracket.stop_order_id` and remove the bracket without checking `bracket.remaining_qty <= 0`, the remaining shares become unhedged with zero stop loss protection in the book.
2. From Observation 2, because completed and flattened brackets never prune `self.order_to_bracket`, memory leaks over extended sessions. Furthermore, late or duplicate fill events matching those order IDs will resolve to completed brackets that cannot safely process them.
3. From Observation 3, calling `manual_tighten_stop` before entry fill attempts to modify an uncreated order ID, and calling it with a loosened price emits redundant order modification directives.
4. From Observation 4, `InstitutionalRiskEngine` strictly enforces `min_stop_distance_pct = 0.004` (0.4%). When `orb.py` or `news_momentum.py` emit stops closer than 0.4% (e.g. 5–20 cents on a $150–$300 stock), `pre_trade_risk_validator` rejects the order, causing silent missed trades.
5. From Observation 5, unhandled frame decoding exceptions in `stock_ws.py` prevent `task_done()` from executing, causing `await self._queue.join()` to block indefinitely during graceful shutdowns.
6. From Observation 6, clearing bracket tracking while leaving orders in `engine.working_orders` at the ET session date boundary allows zombie orders to match on the following day without bracket protection or strategy tracking.

---

## 3. Caveats

1. **Read-Only Inspection**: This audit was strictly observational and analytical; no source files outside the agent directory were modified.
2. **Replay vs. Live Matching**: The deterministic microstructure simulator matches orders against discrete 1-minute bars and quotes. Real exchange fill partialities on limit orders depend on live SIP liquidity, which makes the Target 2 partial-fill vulnerability (Finding 1) primarily a live-market/high-volume replay risk.
3. **Frontend Excluded from Scope**: Frontend Next.js components were reviewed only for backend contract compatibility (`/ws/ui` protocol and action formats). UI rendering and CSS glassmorphism were left to the visual UI audit specialist.

---

## 4. Conclusion

The backend architecture of AutonomousDayTrader is robust, deterministic, and well-tested (100% pass across 433 automated tests). However, 10 architectural issues were identified:
- **1 Critical**: Target 2 partial fill drops stop loss protection for remaining shares.
- **5 Major**: `order_to_bracket` state accumulation, `manual_tighten_stop` invalid directives on pending/loosened stops, stop distance floor violations in ORB and News Momentum, `stock_ws.py` `task_done()` placement, and lingering working orders across session date boundaries.
- **4 Minor**: `RiskEngineConfig` default parameter discrepancy, residual music metaphors in docstrings, pre-market phase representation in flattening engine, and `estimated_risk_dollars` calculation basis.

Detailed root cause analysis and drop-in fix proposals for all 10 findings are documented in:
`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_arch_audit_1/audit_report.md`.

---

## 5. Verification Method

To independently reproduce observations and verify system state:
1. **Run Backend Test Suite**:
   ```bash
   pytest backend/tests
   ```
2. **Run E2E Test Suite**:
   ```bash
   pytest tests/e2e
   ```
3. **Inspect Target 2 Logic**:
   Inspect `backend/app/core/bracket.py` lines 342–353 to confirm that `bracket.remaining_qty` is not checked before cancelling `bracket.stop_order_id`.
4. **Inspect Strategy Stop Logic**:
   Inspect `backend/app/strategies/orb.py` line 173 and `backend/app/strategies/news_momentum.py` line 238 to confirm lack of `0.004 * entry_price` lower bound clamping.
5. **Inspect Session Date Boundary Logic**:
   Inspect `backend/app/main.py` lines 391–404 to confirm that `engine.working_orders` is not cleared when `last_session_date != session_date`.
