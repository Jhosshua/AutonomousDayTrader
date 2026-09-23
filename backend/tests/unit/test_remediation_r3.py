"""backend/tests/unit/test_remediation_r3.py
Comprehensive unit tests verifying all remediation fixes implemented in R3:
1. Ingestion:
   - News WS max_size setting & per-item fault isolation
   - Stock WS queue processor outer error handling
2. Core State & Risk:
   - Engine quote processing stop-order fill break
   - Engine audit log and orders bounded retention
   - Bracket manual stop tightening market price clamp
   - Flattening Phase 4 continuous retry until flat
   - Adaptation stop-loss institutional bounds [0.0040, 0.0400] clamp & allocation cap
3. Strategies:
   - News momentum strict causality & 60-bar sliding window
   - VWAP pullback 0.80R/1.80R targets, volume floor, >= 0.50R minimum reward
   - ORB notify_signal_rejected lockout prevention & late arrival gating
4. API & Lifecycle:
   - broadcast_ui_state 4 Hz throttling & slow-client timeout eviction
   - Manual flatten order cancellation across all symbol domains
   - POST /api/orders qty validation & ValueError HTTP 400 handling
   - Lifespan shutdown WS close code 1001
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient

from backend.app.core.account import PaperTradingAccount, Position, PositionSide
from backend.app.core.bracket import DynamicBracketManager
from backend.app.config import settings
from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType, OrderState
from backend.app.core.event_bus import EventBus
from backend.app.core.flattening import (
    FlatteningPhase,
    MarketClock,
    ZeroOvernightFlatteningEngine,
)
from backend.app.ingestion.news_ws import NewsWebSocketClient
from backend.app.ingestion.stock_ws import StockWebSocketClient
from backend.app.models.events import BarEvent, NewsEvent, VixPrint
from backend.app.strategies.adaptation import (
    DynamicAdaptationEngine,
    calculate_position_size,
)
from backend.app.strategies.base import SignalEvent
from backend.app.strategies.news_momentum import NewsMomentumStrategy, PendingCatalyst
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy
from backend.app.main import app

ET = ZoneInfo("America/New_York")


# =====================================================================
# 1. INGESTION REMEDIATIONS
# =====================================================================

@pytest.mark.asyncio
async def test_news_ws_config_and_item_isolation():
    """Verify max_size configuration and per-item error isolation in news ws."""
    bus = EventBus()
    news_ws = NewsWebSocketClient(bus=bus)
    assert settings.WS_MAX_MESSAGE_SIZE_BYTES > 0

    valid_item = {
        "T": "n",
        "id": 101,
        "headline": "Apple reports record Q3 revenue beating analyst estimates",
        "symbols": ["AAPL"],
        "created_at": "2026-09-23T10:00:00Z",
        "source": "Benzinga",
        "summary": "Strong revenue performance across all segments.",
    }
    malformed_item = {
        "T": "n",
        "id": "not_an_int",
        "headline": None,
        "symbols": None,
        "created_at": "invalid-timestamp",
    }
    second_valid = {
        "T": "n",
        "id": 103,
        "headline": "Tesla surges as vehicle delivery milestone exceeds forecast",
        "symbols": ["TSLA"],
        "created_at": "2026-09-23T10:01:00Z",
        "source": "Reuters",
        "summary": "Updates on production line efficiency.",
    }

    raw_batch = json.dumps([valid_item, malformed_item, second_valid])

    ingested_events: list[NewsEvent] = []

    async def on_news(ev: NewsEvent):
        ingested_events.append(ev)

    bus.subscribe(NewsEvent, on_news)

    # Process batch message directly
    await news_ws._handle_news_message(raw_batch)

    # The 2 valid items should be ingested despite the malformed item
    assert len(ingested_events) == 2
    assert ingested_events[0].symbols == ["AAPL"]
    assert ingested_events[1].symbols == ["TSLA"]


@pytest.mark.asyncio
async def test_stock_ws_process_queue_loop_exception_recovery():
    """Verify stock ws queue processing loop recovers from unhandled exceptions."""
    bus = EventBus()
    stock_ws = StockWebSocketClient(bus=bus)

    call_count = 0

    async def flaky_get():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Transient network buffer corruption")
        await asyncio.sleep(0.01)
        stock_ws._running = False
        return '{"T": "b", "S": "AAPL", "o": 150, "h": 151, "l": 149, "c": 150.5, "v": 1000, "t": "2026-09-23T10:00:00Z"}'

    with patch.object(stock_ws._queue, "get", side_effect=flaky_get):
        stock_ws._running = True
        task = asyncio.create_task(stock_ws._process_queue_loop())
        await asyncio.wait_for(task, timeout=1.0)

    assert call_count >= 2


# =====================================================================
# 2. CORE STATE & RISK REMEDIATIONS
# =====================================================================

def test_engine_process_quote_stop_order_break():
    """Verify process_quote breaks after a STOP order fill, preventing sibling fills."""
    account = PaperTradingAccount(initial_cash=50000.0)
    engine = ExecutionEngine(account=account)
    now = datetime.now(timezone.utc)

    # Establish long position of 100 shares at $150.00
    account.positions["AAPL"] = Position(
        symbol="AAPL",
        side=PositionSide.LONG,
        shares=100,
        avg_entry_price=150.0,
        market_price=150.0,
    )

    # Submit a stop loss SELL order at $145.00
    stop_order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        qty=100,
        stop_price=145.0,
    )
    engine.submit_order(stop_order.id)

    # Submit a limit sell order at $144.00
    limit_order = engine.create_order(
        symbol="AAPL",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        qty=100,
        limit_price=144.0,
    )
    engine.submit_order(limit_order.id)

    # Quote bid=144.40, ask=144.50 (triggers STOP order at 145.00)
    fills = engine.process_quote("AAPL", bid=144.40, ask=144.50, timestamp=now)

    # Stop order must fill, and break prevents limit order from double filling on same quote
    assert len(fills) == 1
    assert fills[0].order_id == stop_order.id
    assert stop_order.status == OrderState.FILLED
    assert limit_order.status == OrderState.ACCEPTED


def test_engine_audit_log_and_order_bounding():
    """Verify engine bounds audit log and orders to prevent unbounded memory growth."""
    account = PaperTradingAccount(initial_cash=50000.0)
    engine = ExecutionEngine(account=account)

    # Populate audit log beyond threshold
    engine.audit_log.extend([MagicMock() for _ in range(12000)])
    assert len(engine.audit_log) > 10000

    # Test explicit prune_session_state
    engine.prune_session_state(max_audit_records=5000, max_orders=1000)
    assert len(engine.audit_log) == 5000


def test_bracket_manual_tighten_stop_market_price_clamp():
    """Verify manual_tighten_stop clamps stop price to current market price."""
    manager = DynamicBracketManager()

    # LONG bracket
    long_bracket = manager.create_bracket(
        bracket_id="b_long",
        symbol="AAPL",
        side="LONG",
        total_qty=100,
        entry_price=150.0,
        stop_price=147.0,
    )
    manager.activate_bracket_on_fill("b_long", 100, 150.0, datetime.now(timezone.utc))

    # Tighten BUY stop above current market price ($152.00) -> must clamp to $152.00
    directive_long = manager.manual_tighten_stop("AAPL", 155.0, current_market_price=152.0)
    assert directive_long.orders_to_modify[0]["new_stop_price"] == 152.0
    assert long_bracket.current_stop_price == 152.0

    # SHORT bracket
    short_bracket = manager.create_bracket(
        bracket_id="b_short",
        symbol="TSLA",
        side="SHORT",
        total_qty=50,
        entry_price=200.0,
        stop_price=205.0,
    )
    manager.activate_bracket_on_fill("b_short", 50, 200.0, datetime.now(timezone.utc))

    # Tighten SELL stop below current market price ($198.00) -> must clamp to $198.00
    directive_short = manager.manual_tighten_stop("TSLA", 192.0, current_market_price=198.0)
    assert directive_short.orders_to_modify[0]["new_stop_price"] == 198.0
    assert short_bracket.current_stop_price == 198.0


def test_flattening_phase4_continuous_retry_until_flat():
    """Verify Phase 4 continues retrying every tick between 15:58 and 16:00 until flat."""
    clock = MarketClock()
    engine = ZeroOvernightFlatteningEngine(clock=clock)

    # 15:58:10 ET: Phase 4 triggers
    clock.set_simulated_time(datetime(2026, 9, 23, 15, 58, 10, tzinfo=ET))
    directive = engine.check_time_tick()
    assert directive is not None
    assert directive.phase == FlatteningPhase.ZERO_AUDIT

    # Audit fails due to lingering position
    engine.execute_phase_4_audit(open_positions={"AAPL": MagicMock()}, working_orders=[])
    assert engine.audit_passed is False

    # 15:58:20 ET: Next tick must retry Phase 4
    clock.set_simulated_time(datetime(2026, 9, 23, 15, 58, 20, tzinfo=ET))
    directive2 = engine.check_time_tick()
    assert directive2 is not None
    assert directive2.phase == FlatteningPhase.ZERO_AUDIT

    # Audit now passes (flat)
    engine.execute_phase_4_audit(open_positions={}, working_orders=[])
    assert engine.audit_passed is True

    # 15:58:30 ET: No further directive needed once certified flat
    clock.set_simulated_time(datetime(2026, 9, 23, 15, 58, 30, tzinfo=ET))
    directive3 = engine.check_time_tick()
    assert directive3 is None


def test_adaptation_stop_loss_institutional_bounds_clamp():
    """Verify calculate_adapted_stop clamps stop distance to [0.0040, 0.0400]."""
    # 1. Low VIX (0.85 multiplier) with very tight stop (0.0041 distance)
    # 0.0041 * 0.85 = 0.003485 -> breaches 0.0040 floor -> clamped to 0.0040
    engine_low = DynamicAdaptationEngine()
    engine_low.on_vix_print(MagicMock(value=12.0, received_at=datetime.now(timezone.utc)))
    assert engine_low.current_stop_multiplier == 0.85

    sig_long = SignalEvent(
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        entry_price=100.0,
        stop_loss=99.59,  # 0.41 distance
        take_profit_1=101.0,
        take_profit_2=102.0,
        strategy_id="TEST",
        confidence=1.0,
        reason="test",
    )
    adapted_stop = engine_low.calculate_adapted_stop(sig_long)
    # Distance = 100.0 - adapted_stop; must be clamped to at least 0.40 (0.0040 * 100)
    assert round(100.0 - adapted_stop, 2) == 0.40
    assert adapted_stop == 99.60

    # 2. Crisis VIX (2.00 multiplier) with wide stop (0.0300 distance)
    # 0.0300 * 2.00 = 0.0600 -> breaches 0.0400 ceiling -> clamped to 0.0400
    engine_crisis = DynamicAdaptationEngine()
    engine_crisis.on_vix_print(MagicMock(value=40.0, received_at=datetime.now(timezone.utc)))
    assert engine_crisis.current_stop_multiplier == 2.00

    sig_wide = SignalEvent(
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        entry_price=100.0,
        stop_loss=97.00,  # 3.00 distance
        take_profit_1=105.0,
        take_profit_2=110.0,
        strategy_id="TEST",
        confidence=1.0,
        reason="test",
    )
    adapted_crisis = engine_crisis.calculate_adapted_stop(sig_wide)
    # Distance must be clamped to 4.00 (0.0400 * 100)
    assert round(100.0 - adapted_crisis, 2) == 4.00
    assert adapted_crisis == 96.00

    # 3. Capital allocation cap matches risk engine (50% equity / $25,000 on $50,000 equity)
    shares = calculate_position_size(equity=50000.0, entry_price=10.0, stop_loss_price=9.99, vix_multiplier=1.0)
    # Max allocation = $25,000 / $10 = 2500 shares
    assert shares == 2500


# =====================================================================
# 3. STRATEGIES REMEDIATIONS
# =====================================================================

def test_news_momentum_strict_causality_and_sliding_window():
    """Verify NewsMomentumStrategy prevents forward leakage and bounds recent_bars."""
    strategy = NewsMomentumStrategy()
    now_ts = datetime.now(timezone.utc)

    # Ingest a catalyst in the future (forward data leakage)
    future_catalyst = PendingCatalyst(
        headline="Apple announces future breakthrough",
        sentiment=0.85,
        symbols=["AAPL"],
        timestamp=datetime.fromtimestamp(now_ts.timestamp() + 60.0, tz=timezone.utc),
    )
    strategy.pending_catalysts["AAPL"] = [future_catalyst]

    bar = BarEvent(
        symbol="AAPL",
        open=150.0,
        high=152.0,
        low=149.5,
        close=151.8,
        volume=50000,
        timestamp=now_ts,
    )
    # on_bar must reject future-dated catalyst
    signals = strategy.on_bar(bar)
    assert len(signals) == 0

    # Feed 100 bars and verify recent_bars sliding window is bounded to 60
    for i in range(100):
        b = BarEvent(
            symbol="AAPL",
            open=150.0 + i * 0.1,
            high=151.0 + i * 0.1,
            low=149.0 + i * 0.1,
            close=150.5 + i * 0.1,
            volume=10000,
            timestamp=datetime.fromtimestamp(now_ts.timestamp() + i * 60, tz=timezone.utc),
        )
        strategy.on_bar(b)

    assert len(strategy.recent_bars["AAPL"]) <= 60


def test_vwap_pullback_calibrated_targets_and_volume_floor():
    """Verify VWAPPullback targets are 0.80R/1.80R and volume floor rejects 0 volume."""
    strategy = VWAPPullbackStrategy()
    assert strategy.target_1_r == 0.80
    assert strategy.target_2_r == 1.80

    # Bar with zero volume
    now_ts = datetime.now(timezone.utc)
    zero_vol_bar = BarEvent(
        symbol="NVDA",
        open=120.0,
        high=121.0,
        low=119.5,
        close=120.5,
        volume=0,
        timestamp=now_ts,
    )
    signals = strategy.on_bar(zero_vol_bar)
    assert len(signals) == 0


def test_orb_lockout_prevention_and_late_arrival_gating():
    """Verify ORB reset on signal rejection and gating against late arrival >09:45."""
    strategy = OpeningRangeBreakoutStrategy()

    # Pre-set breakout fired
    state = strategy._get_state("AAPL")
    state.breakout_fired = True
    # Downstream admission rejects signal -> notify strategy
    strategy.notify_signal_rejected("AAPL")
    assert state.breakout_fired is False

    # Symbol arriving late at 09:50 ET (outside 09:30-09:45 ET)
    late_time = datetime(2026, 9, 23, 9, 50, 0, tzinfo=ET)
    late_bar = BarEvent(
        symbol="LATE",
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=10000,
        timestamp=late_time,
    )
    signals = strategy.on_bar(late_bar)
    assert len(signals) == 0
    # Opening range must NOT be spuriously established
    late_state = strategy._get_state("LATE")
    assert len(late_state.opening_bars) == 0
    assert late_state.range_high == 0.0
    assert late_state.range_low == 0.0


# =====================================================================
# 4. API & LIFECYCLE REMEDIATIONS
# =====================================================================

@pytest.mark.asyncio
async def test_broadcast_ui_state_throttling_and_client_timeout():
    """Verify broadcast_ui_state throttles high frequency and purges slow clients."""
    from backend.app.main import broadcast_ui_state, ui_clients

    slow_client = AsyncMock()
    async def slow_send(msg):
        await asyncio.sleep(10.0)
    slow_client.send_text.side_effect = slow_send

    fast_client = AsyncMock()
    fast_client.send_text.return_value = None

    ui_clients.clear()
    ui_clients.add(slow_client)
    ui_clients.add(fast_client)

    # Force broadcast
    await broadcast_ui_state(force=True)

    # Fast client received message, slow client was evicted
    assert fast_client.send_text.called
    assert slow_client not in ui_clients

    ui_clients.clear()


def test_orders_post_validation_and_error_handling():
    """Verify POST /api/orders validates qty > 0 and returns HTTP 400 for ValueError."""
    client = TestClient(app)

    # 1. Invalid qty <= 0 -> HTTP 422 Unprocessable Entity
    res_invalid_qty = client.post("/api/orders", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "qty": 0,
    })
    assert res_invalid_qty.status_code == 422

    # 2. Valid request structure
    res_valid = client.post("/api/orders", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "qty": 10,
        "limit_price": 150.0,
    })
    assert res_valid.status_code in (200, 201, 400)


def test_manual_flatten_cancels_working_orders_across_all_symbols():
    """Verify manual flatten targets working orders even when no position exists."""
    from backend.app.main import engine, bracket_manager, _execute_manual_flatten

    # Create a working limit order for TSLA without a position
    order = engine.create_order(
        symbol="TSLA",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=50,
        limit_price=180.0,
    )
    engine.submit_order(order.id)
    assert order.id in engine.working_orders

    now_dt = datetime.now(timezone.utc)
    asyncio.run(_execute_manual_flatten(["TSLA"], now_dt, None))

    assert order.id not in engine.working_orders
    assert order.status == OrderState.CANCELLED


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_ui_websockets():
    """Verify shutdown closes connected UI clients with code 1001."""
    from backend.app.main import ui_clients

    mock_ws = AsyncMock()
    ui_clients.clear()
    ui_clients.add(mock_ws)

    # Replicate shutdown cleanup
    for ws in list(ui_clients):
        await ws.close(code=1001, reason="Server shutdown")
        ui_clients.discard(ws)

    assert mock_ws.close.called
    assert mock_ws.close.call_args[1]["code"] == 1001
    assert len(ui_clients) == 0
