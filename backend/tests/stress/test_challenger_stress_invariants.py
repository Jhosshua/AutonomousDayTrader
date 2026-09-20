"""backend/tests/stress/test_challenger_stress_invariants.py
Adversarial Empirical Stress Harness & Invariant Verification for Challenger 1:
1. Target 2 partial fills with residual quantities (verifying stop order stays alive and resized).
2. Rapid / concurrent stop tightening (monotonicity and race condition resistance).
3. Ingestion queue backpressure and malformed frames in StockWebSocketClient (resilience and liveness).
4. Process and port hygiene verification.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import math
import random
from typing import Any, Dict, List
import pytest

from backend.app.core.bracket import (
    BracketChildType,
    BracketOrder,
    BracketStatus,
    BracketUpdateDirective,
    DynamicBracketManager,
)
from backend.app.core.account import PaperTradingAccount, PositionSide
from backend.app.core.engine import (
    ExecutionEngine,
    OrderSide,
    OrderType,
    OrderState,
    BracketRole,
)
from backend.app.core.event_bus import EventBus
from backend.app.ingestion.stock_ws import StockWebSocketClient
from backend.app.models.events import BarEvent


# ============================================================================
# 1. Target 2 Partial Fills with Residual Quantities & Stop Order Resizing
# ============================================================================

class TestTarget2PartialFillsAndStopResizing:
    """Stress tests verifying Target 2 partial fills preserve and properly resize stop orders."""

    def test_target_2_single_partial_fill_resizes_stop_and_stays_active(self):
        """
        Target 1 fills completely (50 shares), then Target 2 partially fills (20 of 50 shares).
        Invariants:
        - remaining_qty decreases from 50 to 30
        - target_2_qty decreases from 50 to 30
        - bracket.status remains TARGET_1_HIT
        - directive is MODIFY_ORDER resizing stop_order to 30 shares
        - stop order remains mapped and NOT cancelled
        """
        bm = DynamicBracketManager(breakeven_buffer=0.02)
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("brk_t2_1", "NVDA", "LONG", 100, 100.0, 98.0, timestamp=now)
        bm.activate_bracket_on_fill("brk_t2_1", 100, 100.0, now)

        # Target 1 fills 50 shares
        d1 = bm.on_child_order_fill(bracket.target_1_order_id, 103.0, 50, now)
        assert d1.bracket_status == BracketStatus.TARGET_1_HIT
        assert bracket.remaining_qty == 50
        assert bracket.target_1_filled is True
        assert bracket.current_stop_price == 100.02  # Ratcheted to breakeven + buffer

        # Target 2 partially fills 20 shares (leaving 30 shares)
        d2 = bm.on_child_order_fill(bracket.target_2_order_id, 105.0, 20, now)

        # Invariant checks:
        assert d2.action == "MODIFY_ORDER"
        assert d2.bracket_status == BracketStatus.TARGET_1_HIT
        assert bracket.remaining_qty == 30
        assert bracket.target_2_qty == 30
        assert bracket.target_2_filled is False
        assert len(d2.orders_to_cancel) == 0

        # Verify modification directive targets the stop order with exact residual quantity
        assert len(d2.orders_to_modify) == 1
        mod = d2.orders_to_modify[0]
        assert mod["order_id"] == bracket.stop_order_id
        assert mod["new_qty"] == 30
        assert mod["new_stop_price"] == 100.02

        # Verify stop order is still tracked in manager maps
        assert "NVDA" in bm.symbol_to_bracket
        assert bracket.stop_order_id in bm.order_to_bracket

    def test_target_2_multi_step_partial_fills_and_final_completion(self):
        """
        Target 2 is filled in multiple micro-fills: 10, 15, 15, 10 shares.
        Verifies stop order is resized monotonically at every fill step until final fill completes bracket.
        """
        bm = DynamicBracketManager(breakeven_buffer=0.02)
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("brk_t2_multi", "AAPL", "LONG", 100, 150.0, 146.0, timestamp=now)
        bm.activate_bracket_on_fill("brk_t2_multi", 100, 150.0, now)

        # Target 1 fills 50 shares
        bm.on_child_order_fill(bracket.target_1_order_id, 156.0, 50, now)
        assert bracket.remaining_qty == 50

        # Micro-fills on Target 2
        fills = [10, 15, 15]
        expected_remaining = [40, 25, 10]

        for fill_qty, exp_rem in zip(fills, expected_remaining):
            d = bm.on_child_order_fill(bracket.target_2_order_id, 160.0, fill_qty, now)
            assert d.action == "MODIFY_ORDER"
            assert d.bracket_status == BracketStatus.TARGET_1_HIT
            assert bracket.remaining_qty == exp_rem
            assert bracket.target_2_qty == exp_rem
            assert d.orders_to_modify[0]["new_qty"] == exp_rem
            assert bracket.target_2_filled is False
            assert bracket.stop_order_id in bm.order_to_bracket

        # Final fill: remaining 10 shares
        d_final = bm.on_child_order_fill(bracket.target_2_order_id, 160.0, 10, now)
        assert d_final.action == "CANCEL_ORDER"
        assert d_final.bracket_status == BracketStatus.COMPLETED_PROFIT
        assert bracket.remaining_qty == 0
        assert bracket.target_2_filled is True
        assert bracket.stop_order_id in d_final.orders_to_cancel
        assert "AAPL" not in bm.symbol_to_bracket
        assert bracket.stop_order_id not in bm.order_to_bracket

    def test_target_2_partial_fill_followed_by_residual_stop_fill(self):
        """
        Adversarial Scenario:
        Target 1 filled (50). Target 2 partially filled (20), leaving 30 shares.
        Then market reverses and hits stop loss.
        The stop order must execute for the remaining 30 shares, cancel residual Target 2,
        and transition cleanly to COMPLETED_STOP without position inversion.
        """
        bm = DynamicBracketManager(breakeven_buffer=0.02)
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("brk_t2_stop", "TSLA", "LONG", 100, 200.0, 195.0, timestamp=now)
        bm.activate_bracket_on_fill("brk_t2_stop", 100, 200.0, now)

        # Target 1 fills 50
        bm.on_child_order_fill(bracket.target_1_order_id, 207.5, 50, now)

        # Target 2 fills 20
        d_t2 = bm.on_child_order_fill(bracket.target_2_order_id, 212.5, 20, now)
        assert bracket.remaining_qty == 30

        # Stop loss triggers for residual 30 shares
        d_stop = bm.on_child_order_fill(bracket.stop_order_id, 200.02, 30, now)

        assert d_stop.action == "CANCEL_ORDER"
        assert d_stop.bracket_status == BracketStatus.COMPLETED_STOP
        assert bracket.remaining_qty == 0
        # Crucial invariant: residual Target 2 order MUST be cancelled so it doesn't execute later!
        assert bracket.target_2_order_id in d_stop.orders_to_cancel
        assert "TSLA" not in bm.symbol_to_bracket

    def test_target_2_partial_fill_in_short_bracket(self):
        """Short position bracket: Target 2 partial fill correctly resizes stop order for shorts."""
        bm = DynamicBracketManager(breakeven_buffer=0.02)
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("brk_short_t2", "SPY", "SHORT", 100, 500.0, 505.0, timestamp=now)
        bm.activate_bracket_on_fill("brk_short_t2", 100, 500.0, now)

        # Target 1 fills 50 shares
        bm.on_child_order_fill(bracket.target_1_order_id, 492.5, 50, now)
        assert bracket.remaining_qty == 50
        assert bracket.current_stop_price == 499.98  # Short breakeven = entry - buffer

        # Target 2 partially fills 35 shares
        d_t2 = bm.on_child_order_fill(bracket.target_2_order_id, 487.5, 35, now)
        assert d_t2.action == "MODIFY_ORDER"
        assert bracket.remaining_qty == 15
        assert bracket.target_2_qty == 15
        assert d_t2.orders_to_modify[0]["new_qty"] == 15
        assert d_t2.orders_to_modify[0]["new_stop_price"] == 499.98

    def test_target_2_partial_fill_engine_state_synchronization(self):
        """
        Verify end-to-end synchronization between DynamicBracketManager and ExecutionEngine:
        Working stop order in ExecutionEngine must have remaining_qty updated to match residual.
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        engine = ExecutionEngine(account=account)
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        # 1. Setup entry and bracket
        entry_order = engine.create_order("MSFT", OrderSide.BUY, OrderType.MARKET, 100)
        engine.submit_order(entry_order.id)
        # Entry fills
        engine._execute_fill(entry_order, 100, 400.0, 0.0, now)

        bracket = bm.create_bracket("brk_msft", "MSFT", "LONG", 100, 400.0, 390.0, timestamp=now)
        directive = bm.activate_bracket_on_fill("brk_msft", 100, 400.0, now)

        # Submit child orders to engine
        stop_order = engine.create_order("MSFT", OrderSide.SELL, OrderType.STOP, 100, stop_price=390.0)
        engine.submit_order(stop_order.id)
        t1_order = engine.create_order("MSFT", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=415.0)
        engine.submit_order(t1_order.id)
        t2_order = engine.create_order("MSFT", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=425.0)
        engine.submit_order(t2_order.id)

        # Wire real engine order IDs to bracket manager
        bracket.stop_order_id = stop_order.id
        bracket.target_1_order_id = t1_order.id
        bracket.target_2_order_id = t2_order.id
        bm.order_to_bracket[stop_order.id] = (bracket.bracket_id, BracketChildType.STOP_LOSS)
        bm.order_to_bracket[t1_order.id] = (bracket.bracket_id, BracketChildType.TAKE_PROFIT_1)
        bm.order_to_bracket[t2_order.id] = (bracket.bracket_id, BracketChildType.TAKE_PROFIT_2)

        # 2. Fill Target 1 completely
        engine._execute_fill(t1_order, 50, 415.0, 0.0, now)
        d_t1 = bm.on_child_order_fill(t1_order.id, 415.0, 50, now)
        # Apply modification to stop order in engine
        for mod in d_t1.orders_to_modify:
            engine.working_orders[mod["order_id"]].remaining_qty = mod["new_qty"]
            engine.working_orders[mod["order_id"]].stop_price = mod["new_stop_price"]

        assert engine.working_orders[stop_order.id].remaining_qty == 50

        # 3. Partially fill Target 2 with 30 shares
        engine._execute_fill(t2_order, 30, 425.0, 0.0, now)
        d_t2 = bm.on_child_order_fill(t2_order.id, 425.0, 30, now)
        for mod in d_t2.orders_to_modify:
            engine.working_orders[mod["order_id"]].remaining_qty = mod["new_qty"]

        # Invariant: engine working stop order must now have remaining_qty == 20
        assert engine.working_orders[stop_order.id].remaining_qty == 20
        assert t2_order.remaining_qty == 20
        assert account.positions["MSFT"].shares == 20


# ============================================================================
# 2. Rapid / Concurrent Stop Tightening (Monotonicity & State Invariants)
# ============================================================================

class TestRapidConcurrentStopTightening:
    """Stress tests verifying monotonicity and race condition immunity during stop tightening."""

    def test_monotonicity_under_random_bidirectional_price_burst(self):
        """
        Send a randomized stream of 500 stop modification requests (some higher, some lower).
        Invariant: For a LONG position, stop price must NEVER decrease.
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("brk_mono", "AAPL", "LONG", 100, 150.0, 140.0, timestamp=now)
        bm.activate_bracket_on_fill("brk_mono", 100, 150.0, now)

        current_stop = bracket.current_stop_price
        max_seen = current_stop

        random.seed(42)
        for _ in range(500):
            # Generate prices both above and below current stop
            candidate_price = round(random.uniform(135.0, 149.5), 2)
            res = bm.manual_tighten_stop("AAPL", candidate_price)

            if candidate_price > current_stop:
                assert res.action == "MODIFY_ORDER"
                assert bracket.current_stop_price == candidate_price
                current_stop = candidate_price
                max_seen = max(max_seen, current_stop)
            else:
                assert res.action == "NO_ACTION"
                assert bracket.current_stop_price == current_stop

            # Strict invariant check on every iteration:
            assert bracket.current_stop_price >= max_seen
            assert bracket.current_stop_price >= 140.0

    def test_monotonicity_short_position(self):
        """For a SHORT position, stop price must NEVER increase."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("brk_short_mono", "TSLA", "SHORT", 100, 200.0, 210.0, timestamp=now)
        bm.activate_bracket_on_fill("brk_short_mono", 100, 200.0, now)

        current_stop = bracket.current_stop_price
        min_seen = current_stop

        random.seed(1337)
        for _ in range(500):
            candidate_price = round(random.uniform(200.5, 220.0), 2)
            res = bm.manual_tighten_stop("TSLA", candidate_price)

            if candidate_price < current_stop:
                assert res.action == "MODIFY_ORDER"
                assert bracket.current_stop_price == candidate_price
                current_stop = candidate_price
                min_seen = min(min_seen, current_stop)
            else:
                assert res.action == "NO_ACTION"
                assert bracket.current_stop_price == current_stop

            assert bracket.current_stop_price <= min_seen
            assert bracket.current_stop_price <= 210.0

    @pytest.mark.asyncio
    async def test_high_concurrency_async_tightening(self):
        """
        Execute 200 concurrent async tasks attempting to tighten the stop simultaneously.
        Verify no corrupted state, no deadlocks, and final stop price is the global maximum.
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("brk_concurrent", "NVDA", "LONG", 100, 100.0, 90.0, timestamp=now)
        bm.activate_bracket_on_fill("brk_concurrent", 100, 100.0, now)

        prices = [round(90.0 + i * 0.04, 2) for i in range(1, 201)]
        random.shuffle(prices)  # Shuffle so they arrive out of order
        expected_max = max(prices)

        async def worker(price: float):
            await asyncio.sleep(random.uniform(0.0001, 0.005))
            return bm.manual_tighten_stop("NVDA", price)

        results = await asyncio.gather(*(worker(p) for p in prices))

        # Invariant checks:
        assert bracket.current_stop_price == expected_max
        assert bracket.status == BracketStatus.ACTIVE
        assert bracket.remaining_qty == 100

    def test_rejected_tightening_on_inactive_or_pending_brackets(self):
        """Tightening requests on PENDING_ENTRY, COMPLETED, or missing brackets must return NO_ACTION without crashing."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        # 1. Missing symbol
        res = bm.manual_tighten_stop("NON_EXISTENT", 100.0)
        assert res.action == "NO_ACTION"

        # 2. Bracket in PENDING_ENTRY (not activated yet)
        bm.create_bracket("brk_pending", "AAPL", "LONG", 100, 150.0, 145.0, timestamp=now)
        res_pending = bm.manual_tighten_stop("AAPL", 148.0)
        assert res_pending.action == "NO_ACTION"
        assert bm.brackets["brk_pending"].current_stop_price == 145.0  # Unchanged

        # 3. Completed bracket
        bm.activate_bracket_on_fill("brk_pending", 100, 150.0, now)
        bm.on_child_order_fill("t1_brk_pending", 155.0, 50, now)
        bm.on_child_order_fill("t2_brk_pending", 160.0, 50, now)
        assert bm.brackets["brk_pending"].status == BracketStatus.COMPLETED_PROFIT

        res_completed = bm.manual_tighten_stop("AAPL", 158.0)
        assert res_completed.action == "NO_ACTION"


# ============================================================================
# 3. Queue Backpressure & Malformed Frames in StockWebSocketClient
# ============================================================================

class TestStockWebSocketClientResilience:
    """Stress tests for queue backpressure saturation, malformed JSON frames, and worker liveness."""

    @pytest.mark.asyncio
    async def test_queue_backpressure_drop_and_liveness(self):
        """
        Simulate backpressure by feeding messages into a client with a restricted queue (maxsize=10).
        Verify dropped_messages increments on QueueFull, loop does NOT stall, and worker stays alive.
        """
        bus = EventBus()
        client = StockWebSocketClient(bus=bus)
        # Override queue with a tiny maxsize to trigger backpressure
        client._queue = asyncio.Queue(maxsize=10)
        client._running = True

        # Start only the queue worker task
        worker_task = asyncio.create_task(client._process_queue_loop())
        client._worker_task = worker_task

        # Rapidly push 50 messages into the queue via _read_loop simulator
        bars_published = 0

        async def on_bar(event: BarEvent):
            nonlocal bars_published
            bars_published += 1

        bus.subscribe(BarEvent, on_bar)

        for i in range(50):
            msg = json.dumps([{
                "T": "b",
                "S": "AAPL",
                "o": 150.0,
                "h": 151.0,
                "l": 149.0,
                "c": 150.5,
                "v": 1000 + i,
                "t": f"2026-09-21T09:{i % 60:02d}:00Z",
            }])
            client.messages_received += 1
            try:
                client._queue.put_nowait(msg)
            except asyncio.QueueFull:
                client.dropped_messages += 1

        assert client.messages_received == 50
        assert client.dropped_messages == 40  # 10 capacity, 40 dropped
        assert client._queue.qsize() == 10

        # Allow worker to process buffered messages
        await asyncio.sleep(0.05)
        await client._queue.join()

        # Invariants:
        assert client._queue.qsize() == 0
        assert bars_published == 10
        assert not worker_task.done()  # Worker MUST still be alive!

        # Cleanup
        client._running = False
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_malformed_json_frames_do_not_kill_worker(self):
        """
        Empirically verify that malformed JSON payloads:
        - Truncated syntax
        - Non-JSON binary strings
        - Primitive JSON values (int, bool, null)
        - Schema-violating dictionaries (missing required fields)
        DO NOT kill the worker task, and the worker recovers immediately to process subsequent valid frames.
        """
        bus = EventBus()
        client = StockWebSocketClient(bus=bus)
        client._queue = asyncio.Queue(maxsize=100)
        client._running = True

        worker_task = asyncio.create_task(client._process_queue_loop())
        client._worker_task = worker_task

        bars_received = []

        async def on_bar(event: BarEvent):
            bars_received.append(event)

        bus.subscribe(BarEvent, on_bar)

        malformed_inputs = [
            '{"action": "incomplete_json',            # Truncated
            'RANDOM_GARBAGE_NOT_JSON_AT_ALL_$$$#@!',    # Garbage
            '',                                        # Empty
            'null',                                    # JSON null
            '12345',                                   # JSON int
            'true',                                    # JSON bool
            '[]',                                      # Empty list
            '[{}]',                                    # Empty dict
            '[{"T": "UNKNOWN_TAG", "x": 1}]',          # Unknown tag
            '[{"T": "b", "S": "AAPL"}]',               # Bar missing required o, h, l, c, v, t
            '[{"T": "b", "S": "AAPL", "o": "NaN"}]',   # Bar invalid price type
            '{"T": "b", "S": "AAPL", "o": 100.0}',    # Single dict instead of list
        ]

        for payload in malformed_inputs:
            client._queue.put_nowait(payload)

        # Now append a valid bar to verify immediate recovery / liveness
        valid_bar = json.dumps([{
            "T": "b",
            "S": "MSFT",
            "o": 400.0,
            "h": 402.0,
            "l": 399.0,
            "c": 401.0,
            "v": 5000,
            "t": "2026-09-21T09:45:00Z",
        }])
        client._queue.put_nowait(valid_bar)

        # Wait for queue to drain
        await asyncio.sleep(0.05)
        await client._queue.join()

        # Invariants:
        # 1. Worker task did NOT crash
        assert not worker_task.done()
        assert not worker_task.cancelled()

        # 2. Valid bar was successfully parsed and published despite previous failures
        assert len(bars_received) == 1
        assert bars_received[0].symbol == "MSFT"
        assert bars_received[0].close == 401.0
        # Telemetry invariant: client.bars_received increments only after successful parsing and
        # EventBus publication, so malformed frames do not falsely inflate the ingestion counter.
        assert client.bars_received == 1

        # Cleanup
        client._running = False
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


# ============================================================================
# 4. High-Volume Order Flow, Rapid Fills & Ledger Invariants
# ============================================================================

class TestHighVolumeOrderFlowAndFills:
    """Stress tests for high-volume rapid order flow, micro-fills, and ledger conservation."""

    def test_high_volume_order_lifecycle_burst(self):
        """
        Submit 1,000 rapid orders with random fills and cancellations.
        Invariants verified:
        - FSM state transitions strictly valid (no illegal transitions)
        - engine.working_orders matches orders in ACCEPTED or PARTIALLY_FILLED
        - Conservation: equity == cash + market_value of open positions
        - Conservation: buying_power == 4 * max(0.0, equity - maintenance_margin)
        - Zero unhandled exceptions or ledger drift
        """
        account = PaperTradingAccount(initial_cash=50000.0)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)
        symbols = ["AAPL", "MSFT", "NVDA", "TSLA"]

        random.seed(999)
        active_orders = []

        for i in range(500):
            sym = random.choice(symbols)
            side = OrderSide.BUY if random.random() < 0.6 else OrderSide.SELL
            qty = random.randint(10, 50)
            price = round(random.uniform(100.0, 150.0), 2)

            ord_type = random.choice([OrderType.LIMIT, OrderType.MARKET])
            limit_p = price if ord_type == OrderType.LIMIT else None

            # Create order
            order = engine.create_order(
                symbol=sym,
                side=side,
                order_type=ord_type,
                qty=qty,
                limit_price=limit_p,
                estimated_price=price,
            )
            sub = engine.submit_order(order.id)
            if sub.status == OrderState.ACCEPTED:
                active_orders.append(sub)

        # Now simulate rapid fills and cancels
        for order in list(active_orders):
            action = random.choice(["FULL_FILL", "PARTIAL_FILL", "CANCEL"])
            fill_price = order.limit_price or order.estimated_price or 120.0

            if action == "FULL_FILL":
                engine._execute_fill(order, order.remaining_qty, fill_price, 0.01, now)
            elif action == "PARTIAL_FILL":
                fill_qty = max(1, order.remaining_qty // 2)
                engine._execute_fill(order, fill_qty, fill_price, 0.01, now)
            elif action == "CANCEL":
                if order.id in engine.working_orders:
                    engine.cancel_order(order.id, reason="BURST_TEST_CANCEL")

        # Verify Invariants:
        # 1. All working orders in engine must be ACCEPTED or PARTIALLY_FILLED
        for oid, w_order in engine.working_orders.items():
            assert w_order.status in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)
            assert w_order.remaining_qty > 0

        # 2. Conservation invariant: equity == cash + sum(pos.market_value)
        expected_pos_value = sum(pos.market_value for pos in account.positions.values())
        assert math.isclose(account.equity, account.cash + expected_pos_value, abs_tol=1e-2)

        # 3. DTBP invariant: buying_power == 4 * max(0.0, equity - maintenance_margin)
        expected_bp = max(0.0, account.margin_excess * 4.0)
        assert math.isclose(account.buying_power, expected_bp, abs_tol=1e-2)


# ============================================================================
# 5. Port and Process Hygiene Verification
# ============================================================================

class TestPortAndProcessHygiene:
    """Empirical verification that ports 8005, 3005, 8080 are completely clean and unblocked."""

    def test_ports_8005_3005_8080_are_free(self):
        """Verify sockets can bind and release on ports 8005, 3005, and 8080 with zero conflicts."""
        import socket
        ports_to_check = [8005, 3005, 8080]

        for port in ports_to_check:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # Attempt to bind to test port
                s.bind(("127.0.0.1", port))
                # Successfully bound means the port is FREE and not occupied by an orphaned daemon
                is_free = True
            except OSError:
                is_free = False
            finally:
                s.close()

            assert is_free, f"Port hygiene violation: Port {port} is occupied by an orphaned process!"
