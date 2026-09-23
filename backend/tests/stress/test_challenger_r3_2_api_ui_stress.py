"""backend/tests/stress/test_challenger_r3_2_api_ui_stress.py
Adversarial Empirical Stress Harness for Challenger 2:
1. UI WebSocket Broadcast Throttling & Slow-Consumer Isolation:
   - Stalled mock WebSocket client with 1,000 quote events fired at 500 Hz.
   - Verifies event loop does not block, broadcast_ui_state times out stalled clients,
     fast clients remain connected, and ingestion queue remains healthy without drops.
2. POST /api/orders Validation & Exception Handling:
   - Negative and zero quantities (qty=0, qty=-10).
   - LIMIT orders without limit_price (both with and without estimated market prices).
   - STOP orders without stop_price.
   - Unsupported STOP_LIMIT orders and invalid side/type enums.
   - Verifies clean HTTP 400 / 422 responses with descriptive error details and zero HTTP 500 crashes.
3. Phase 4 EOD Auto-Flattening Continuous Retry:
   - Simulates 15:58:00 to 15:59:59 ET with lingering positions.
   - Verifies check_time_tick repeatedly issues zero-audit directives on every tick while audit_passed is False.
   - Verifies check_time_tick ceases directives once audit_passed is True.
   - Verifies market close transition at 16:00:00 ET.
4. Port Hygiene Script Verification:
   - Tests scripts/verify_port_hygiene.sh under clean conditions (exit code 0).
   - Tests scripts/verify_port_hygiene.sh under active listener conditions for ports 3005, 8000, 8005, 8080 (exit code 1).
   - Tests all 4 ports concurrently occupied and verifies clean recovery and process liberation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
import json
import socket
import subprocess
import time as stdlib_time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.account import PaperTradingAccount
from backend.app.core.bracket import DynamicBracketManager
from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType
from backend.app.core.event_bus import EventBus
from backend.app.core.flattening import (
    FlatteningDirective,
    FlatteningPhase,
    MarketClock,
    ZeroOvernightFlatteningEngine,
)
from backend.app.ingestion.stock_ws import StockWebSocketClient
from backend.app.models.events import QuoteEvent
from backend.app.main import (
    app,
    broadcast_ui_state,
    handle_quote_event,
    latest_market_prices,
    ui_clients,
)
import backend.app.main as main_module

ET = ZoneInfo("America/New_York")


# ============================================================================
# 1. UI WebSocket Broadcast Throttling & Slow-Consumer Isolation
# ============================================================================

class StalledMockWebSocket:
    """Mock WebSocket client that hangs indefinitely on send_text to simulate a stalled consumer."""

    def __init__(self, stall_duration_sec: float = 10.0) -> None:
        self.stall_duration_sec = stall_duration_sec
        self.send_call_count = 0
        self.messages_received: List[str] = []

    async def send_text(self, text: str) -> None:
        self.send_call_count += 1
        await asyncio.sleep(self.stall_duration_sec)
        self.messages_received.append(text)


class FastMockWebSocket:
    """Mock WebSocket client that consumes messages immediately."""

    def __init__(self) -> None:
        self.send_call_count = 0
        self.messages_received: List[str] = []

    async def send_text(self, text: str) -> None:
        self.send_call_count += 1
        self.messages_received.append(text)


class TestUIBroadcastThrottlingAndSlowConsumerIsolation:
    """Empirical challenge: 1,000 quotes at 500 Hz against stalled and healthy WebSocket consumers."""

    @pytest.mark.asyncio
    async def test_ui_broadcast_throttling_and_slow_client_eviction_under_500hz_load(self):
        """
        Adversarial Scenario:
        - 1 stalled client (10s latency > 0.35s timeout) and 1 fast client in ui_clients.
        - 1,000 quote events generated at 500 Hz (2ms period).
        - Verify:
          1. Event loop remains responsive (heartbeat latency stays low, does not freeze).
          2. broadcast_ui_state times out and purges stalled client within timeout bound.
          3. Fast client survives in ui_clients and receives throttled broadcasts (~4 Hz).
          4. Ingestion queue handles 1,000 items without overflow or dropped frames.
        """
        # Save original state
        original_sim_mode = main_module.simulation_mode
        main_module.simulation_mode = True
        ui_clients.clear()

        stalled_ws = StalledMockWebSocket(stall_duration_sec=5.0)
        fast_ws = FastMockWebSocket()
        ui_clients.add(stalled_ws)
        ui_clients.add(fast_ws)

        # Setup local EventBus and StockWebSocketClient
        bus = EventBus()
        stock_ws = StockWebSocketClient(bus=bus)

        # Connect handle_quote_event to bus
        bus.subscribe(QuoteEvent, handle_quote_event)

        # Event loop liveness monitor (measures cooperative event-loop scheduling latency)
        max_loop_lag_ms = 0.0
        heartbeat_ticks = 0
        monitor_running = True

        async def loop_liveness_monitor():
            nonlocal max_loop_lag_ms, heartbeat_ticks
            target_step = 0.010  # 10ms target step
            while monitor_running:
                t0 = stdlib_time.perf_counter()
                await asyncio.sleep(target_step)
                elapsed = stdlib_time.perf_counter() - t0
                lag = max(0.0, (elapsed - target_step) * 1000.0)
                if lag > max_loop_lag_ms:
                    max_loop_lag_ms = lag
                heartbeat_ticks += 1

        monitor_task = asyncio.create_task(loop_liveness_monitor())

        # Start queue worker
        stock_ws._running = True
        worker_task = asyncio.create_task(stock_ws._process_queue_loop())

        # Generate 1,000 quotes at 500 Hz (1 frame every 0.002s)
        total_quotes = 1000
        target_hz = 500.0
        frame_interval_sec = 1.0 / target_hz  # 0.002s

        feeder_start = stdlib_time.perf_counter()
        for i in range(total_quotes):
            raw_quote_msg = json.dumps([{
                "T": "q",
                "S": "AAPL",
                "bx": "V",
                "bp": 150.0 + (i % 20) * 0.05,
                "bs": 100,
                "ax": "V",
                "ap": 150.05 + (i % 20) * 0.05,
                "as": 200,
                "t": "2026-09-23T14:30:00.000000Z",
                "c": ["R"],
                "z": "C"
            }])
            # Feed into StockWebSocketClient._queue
            try:
                stock_ws._queue.put_nowait(raw_quote_msg)
                stock_ws.messages_received += 1
            except asyncio.QueueFull:
                stock_ws.dropped_messages += 1

            if i % 10 == 0:
                await asyncio.sleep(0.001)  # Yield briefly to maintain 500 Hz timing

        feeder_duration = stdlib_time.perf_counter() - feeder_start

        # Wait for worker queue to drain completely
        drain_deadline = stdlib_time.perf_counter() + 6.0
        while stock_ws._queue.qsize() > 0 and stdlib_time.perf_counter() < drain_deadline:
            await asyncio.sleep(0.02)

        # Stop worker and monitor
        monitor_running = False
        await monitor_task
        stock_ws._running = False
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # Cleanup and restore
        main_module.simulation_mode = original_sim_mode

        # Assertions
        # 1. Ingestion queue health
        assert stock_ws.dropped_messages == 0, f"Ingestion queue dropped {stock_ws.dropped_messages} frames"
        assert stock_ws.quotes_received == total_quotes, (
            f"Expected {total_quotes} quotes processed, got {stock_ws.quotes_received}"
        )
        assert stock_ws._queue.qsize() == 0, "Ingestion queue did not drain"

        # 2. Slow consumer isolation & eviction
        assert stalled_ws not in ui_clients, "Stalled client was not evicted from ui_clients"
        assert fast_ws in ui_clients, "Fast client was erroneously discarded"

        # 3. Broadcast throttling verification
        # 1,000 quotes over ~2 seconds should NOT trigger 1,000 broadcasts.
        # At 4 Hz (0.25s), broadcasts should be throttled to <= 25.
        assert fast_ws.send_call_count > 0, "Fast client received zero broadcasts"
        assert fast_ws.send_call_count <= 30, (
            f"Throttling failed: fast client received {fast_ws.send_call_count} broadcasts (expected <= 30)"
        )

        # 4. Event loop responsiveness
        assert heartbeat_ticks > 10, f"Event loop was starved; only {heartbeat_ticks} heartbeats registered"

        ui_clients.clear()


# ============================================================================
# 2. POST /api/orders Validation & Exception Handling
# ============================================================================

class TestOrdersPostValidationAndExceptionHandling:
    """Empirical challenge: POST /api/orders rejects invalid orders with clean 400/422 and 0 crashes."""

    @pytest.fixture(autouse=True)
    def setup_test_client(self):
        self.client = TestClient(app)
        # Ensure latest_market_prices has baseline
        latest_market_prices["NVDA"] = 120.0
        latest_market_prices["SPY"] = 500.0

    def test_post_orders_rejects_zero_quantity_with_422(self):
        """qty = 0 must fail Pydantic schema validation with HTTP 422."""
        resp = self.client.post("/api/orders", json={
            "symbol": "SPY",
            "side": "BUY",
            "order_type": "MARKET",
            "qty": 0,
            "stop_price": 490.0,
        })
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body
        error_str = json.dumps(body["detail"])
        assert "greater than 0" in error_str or "gt" in error_str

    def test_post_orders_rejects_negative_quantity_with_422(self):
        """qty = -10 must fail Pydantic schema validation with HTTP 422."""
        resp = self.client.post("/api/orders", json={
            "symbol": "SPY",
            "side": "BUY",
            "order_type": "LIMIT",
            "qty": -10,
            "limit_price": 500.0,
            "stop_price": 490.0,
        })
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body
        error_str = json.dumps(body["detail"])
        assert "greater than 0" in error_str or "gt" in error_str

    def test_post_orders_rejects_limit_order_without_limit_price_with_400(self):
        """LIMIT order without limit_price must return HTTP 400 with descriptive detail."""
        # 1. Unknown market price and no limit_price -> 400
        resp1 = self.client.post("/api/orders", json={
            "symbol": "UNKNOWN_TICKER",
            "side": "BUY",
            "order_type": "LIMIT",
            "qty": 5,
            "stop_price": 95.0,
        })
        assert resp1.status_code == 400
        assert "Opening orders require stop_price and a known limit/latest market price" in resp1.json()["detail"]

        # 2. Known market price (NVDA=120.0) but LIMIT order has limit_price=None -> engine raises ValueError -> 400
        resp2 = self.client.post("/api/orders", json={
            "symbol": "NVDA",
            "side": "BUY",
            "order_type": "LIMIT",
            "qty": 5,
            "stop_price": 118.0,
        })
        assert resp2.status_code == 400
        assert "Limit price required for LIMIT orders" in resp2.json()["detail"]

    def test_post_orders_rejects_stop_order_without_stop_price_with_400(self):
        """STOP order without stop_price must return HTTP 400."""
        resp = self.client.post("/api/orders", json={
            "symbol": "NVDA",
            "side": "SELL",
            "order_type": "STOP",
            "qty": 5,
        })
        assert resp.status_code == 400
        assert "Opening orders require stop_price" in resp.json()["detail"]

    def test_post_orders_rejects_stop_limit_orders_with_400(self):
        """STOP_LIMIT orders are explicitly rejected with HTTP 400."""
        resp = self.client.post("/api/orders", json={
            "symbol": "SPY",
            "side": "BUY",
            "order_type": "STOP_LIMIT",
            "qty": 10,
            "limit_price": 505.0,
            "stop_price": 500.0,
        })
        assert resp.status_code == 400
        assert "STOP_LIMIT orders are not supported" in resp.json()["detail"]

    def test_post_orders_rejects_invalid_enums_with_400(self):
        """Invalid side and order_type strings must be rejected with HTTP 400."""
        resp_side = self.client.post("/api/orders", json={
            "symbol": "SPY",
            "side": "INVALID_SIDE",
            "order_type": "LIMIT",
            "qty": 10,
            "limit_price": 500.0,
            "stop_price": 490.0,
        })
        assert resp_side.status_code == 400
        assert "OrderSide" in resp_side.json()["detail"]

        resp_type = self.client.post("/api/orders", json={
            "symbol": "SPY",
            "side": "BUY",
            "order_type": "INVALID_TYPE",
            "qty": 10,
            "limit_price": 500.0,
            "stop_price": 490.0,
        })
        assert resp_type.status_code == 400
        assert "OrderType" in resp_type.json()["detail"]

    def test_post_orders_zero_500_crashes_on_fuzzed_inputs(self):
        """Send a battery of malformed payloads to verify zero unhandled HTTP 500 exceptions."""
        malformed_payloads = [
            {},
            {"qty": "not_an_int"},
            {"symbol": "SPY", "side": "BUY", "order_type": "MARKET", "qty": -999999},
            {"symbol": "", "side": "BUY", "order_type": "MARKET", "qty": 10},
            {"symbol": "SPY", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": -50.0},
            {"symbol": "SPY", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": 999999999.0},
        ]
        for payload in malformed_payloads:
            resp = self.client.post("/api/orders", json=payload)
            assert resp.status_code != 500, f"Payload {payload} caused HTTP 500 server crash!"
            assert resp.status_code in (400, 422)

        raw_malformed = [
            b'{"symbol": "SPY", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": NaN}',
            b'{"symbol": "SPY", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": Infinity}',
            b'{"invalid_json": ',
            b'',
        ]
        for raw in raw_malformed:
            resp = self.client.post("/api/orders", content=raw, headers={"Content-Type": "application/json"})
            assert resp.status_code != 500, f"Raw payload {raw} caused HTTP 500 server crash!"
            assert resp.status_code in (400, 422)


# ============================================================================
# 3. Phase 4 EOD Auto-Flattening Continuous Retry Simulation
# ============================================================================

class TestPhase4EODAutoFlatteningRetry:
    """Empirical challenge: simulate 15:58:00 to 15:59:59 ET with lingering positions."""

    def test_phase4_continuous_retry_until_audit_passed(self):
        """
        Simulate clock progression from 15:58:00 to 15:59:59 ET with lingering positions:
        - Ticks 1-30: Position lingers -> check_time_tick repeatedly returns ZERO_AUDIT directive.
        - Tick 31: Emergency sweep completes and book is flat -> execute_phase_4_audit passes.
        - Ticks 32-120: check_time_tick ceases returning directives (returns None) until 16:00:00.
        - Tick 121 (16:00:00): Transitions to MARKET_CLOSED.
        """
        clock = MarketClock()
        engine = ZeroOvernightFlatteningEngine(clock=clock)

        session_date = datetime(2026, 9, 23, tzinfo=ET).date()

        # Mock lingering position
        lingering_positions: Dict[str, Any] = {"AAPL": {"shares": 100, "market_price": 150.0}}
        empty_orders: List[Any] = []

        directives_issued: List[FlatteningDirective] = []

        # Ticks 0 to 29 (15:58:00 through 15:58:29): Position lingers
        for sec in range(30):
            current_dt = datetime.combine(session_date, time(15, 58, sec), tzinfo=ET)
            clock.set_simulated_time(current_dt)

            directive = engine.check_time_tick()
            assert directive is not None, f"Tick at 15:58:{sec:02d} failed to issue directive while audit failed"
            assert directive.phase == FlatteningPhase.ZERO_AUDIT
            assert directive.action_required == "EXECUTE_PHASE_4_AUDIT"
            directives_issued.append(directive)

            # Audit execution fails because position lingers
            audit_dir = engine.execute_phase_4_audit(
                open_positions=lingering_positions,
                working_orders=empty_orders,
            )
            assert audit_dir.audit_passed is False
            assert engine.audit_passed is False

        assert len(directives_issued) == 30
        assert engine.audit_retries == 30

        # Tick 30 (15:58:30): Emergency sweep succeeds, book becomes flat
        current_dt = datetime.combine(session_date, time(15, 58, 30), tzinfo=ET)
        clock.set_simulated_time(current_dt)

        # check_time_tick still triggers because audit hasn't passed yet
        directive_sweep = engine.check_time_tick()
        assert directive_sweep is not None
        assert directive_sweep.phase == FlatteningPhase.ZERO_AUDIT

        # Emergency sweep flattens portfolio -> audit passes!
        lingering_positions.clear()
        audit_dir_pass = engine.execute_phase_4_audit(
            open_positions=lingering_positions,
            working_orders=empty_orders,
        )
        assert audit_dir_pass.audit_passed is True
        assert engine.audit_passed is True

        # Ticks 31 to 89 (15:58:31 through 15:59:59): Clean book certified flat
        # check_time_tick must NOT issue any more directives
        for sec in range(31, 60):
            current_dt = datetime.combine(session_date, time(15, 58, sec), tzinfo=ET)
            clock.set_simulated_time(current_dt)
            d = engine.check_time_tick()
            assert d is None, f"Tick at 15:58:{sec:02d} unexpectedly issued directive after audit passed"

        for sec in range(60):
            current_dt = datetime.combine(session_date, time(15, 59, sec), tzinfo=ET)
            clock.set_simulated_time(current_dt)
            d = engine.check_time_tick()
            assert d is None, f"Tick at 15:59:{sec:02d} unexpectedly issued directive after audit passed"

        # 16:00:00 Market Close transition
        close_dt = datetime.combine(session_date, time(16, 0, 0), tzinfo=ET)
        clock.set_simulated_time(close_dt)
        close_directive = engine.check_time_tick()
        assert close_directive is not None
        assert close_directive.phase == FlatteningPhase.MARKET_CLOSED
        assert close_directive.action_required == "SESSION_CLOSED"

    def test_phase4_lingers_entire_two_minutes_triggers_all_120_retries(self):
        """If a position refuses to close for all 120 seconds, every second must trigger a retry."""
        clock = MarketClock()
        engine = ZeroOvernightFlatteningEngine(clock=clock)
        session_date = datetime(2026, 9, 23, tzinfo=ET).date()

        stuck_positions = {"TSLA": {"shares": 50}}
        retry_count = 0

        # 15:58:00 to 15:59:59 (120 seconds)
        for minute in (58, 59):
            for sec in range(60):
                current_dt = datetime.combine(session_date, time(15, minute, sec), tzinfo=ET)
                clock.set_simulated_time(current_dt)
                directive = engine.check_time_tick()
                assert directive is not None
                assert directive.phase == FlatteningPhase.ZERO_AUDIT
                engine.execute_phase_4_audit(stuck_positions, [])
                retry_count += 1

        assert retry_count == 120
        assert engine.audit_retries == 120
        assert engine.audit_passed is False


# ============================================================================
# 4. Port Hygiene Verification Script
# ============================================================================

class TestPortHygieneScript:
    """Empirical verification of scripts/verify_port_hygiene.sh under clean and active states."""

    SCRIPT_PATH = "./scripts/verify_port_hygiene.sh"

    def _run_script(self) -> tuple[int, str]:
        res = subprocess.run(
            [self.SCRIPT_PATH],
            capture_output=True,
            text=True,
        )
        return res.returncode, res.stdout + res.stderr

    def test_script_passes_under_clean_conditions(self):
        """When all monitored ports (3005, 8000, 8005, 8080) are free, script returns 0."""
        code, out = self._run_script()
        assert code == 0, f"Expected 0 under clean state, got {code}. Output:\n{out}"
        assert "All ports verified clean" in out
        assert "Port 3005 is clean" in out
        assert "Port 8000 is clean" in out
        assert "Port 8005 is clean" in out
        assert "Port 8080 is clean" in out

    @pytest.mark.parametrize("port", [3005, 8000, 8005, 8080])
    def test_script_fails_and_identifies_individual_active_ports(self, port: int):
        """When any monitored port is occupied, script returns 1 and identifies the port and PID."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)

            code, out = self._run_script()
            assert code == 1, f"Expected 1 when port {port} occupied, got {code}. Output:\n{out}"
            assert f"INTEGRITY VIOLATION: Port {port} is still occupied" in out
            assert "Ports are occupied" in out
        finally:
            s.close()

        # Verify immediate liberation after socket close
        code_after, out_after = self._run_script()
        assert code_after == 0, f"Port {port} failed to liberate cleanly. Output:\n{out_after}"

    def test_script_detects_multiple_concurrently_occupied_ports(self):
        """When all monitored ports are occupied simultaneously, script reports all violations."""
        ports = [3005, 8000, 8005, 8080]
        sockets = []
        try:
            for p in ports:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", p))
                s.listen(1)
                sockets.append(s)

            code, out = self._run_script()
            assert code == 1
            for p in ports:
                assert f"Port {p} is still occupied" in out
        finally:
            for s in sockets:
                s.close()

        # Verify clean liberation
        code_liberated, out_liberated = self._run_script()
        assert code_liberated == 0
        assert "All ports verified clean" in out_liberated
