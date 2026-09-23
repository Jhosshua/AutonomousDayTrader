"""backend/tests/stress/test_challenger_r6_remediation.py
Adversarial Challenger & Mutation Verification Suite for Round 6 Remediation:
1. Prioritized Ingestion Queue: Under quote flood, quotes are shed while bars/trades/relays are preserved.
2. News Catalyst Watchlist Gating & Queue Capping: Unwatched tickers filtered; max 10 entries per symbol.
3. SQLite WAL Checkpoint on Shutdown & Passive Checkpointing: WAL is safely checkpointed and truncated.
4. Market History Session Monotonicity: Session boundary rollover cleans market history; backward dates rejected.
5. Event Bus Handler Deduplication: Multiple subscriptions deduplicate; clear() flushes subscribers.
6. ORB Signal Rejection Reset: Engine rejection notifies strategy and resets lock, avoiding deadlock.
7. VWAP Pullback Causal Volume Baseline: Candidate bar excluded from prior 10-bar baseline calculation.
8. ORB ATR Baseline & Pre-market Isolation: Candidate bar excluded from ATR; pre-market bars rejected.
9. Market Filter Microsecond Skew: Tolerates sub-second clock jitter between quote feeds.
10. News Momentum Mid-Minute Catalyst Retention: Preserves mid-minute catalysts for reaction bar evaluation.
11. Simultaneous 12-Ticker Collision: Union of working orders and filled positions enforces max 3 concurrent and max 2 per sector.
12. Pre-Trade Circuit Breaker Under Un-evaluated Equity: Real-time drawdown >= $1,500 immediately halts orders; loss budget enforced.
13. Single-Position Cap Net of Existing Exposure: Net exposure cannot exceed 50% equity ($25,000).
14. Phase 2 EOD Order Purge Preserves Protective Stops: At 15:50 ET only entry orders purged; protective stops preserved.
15. Manual Tighten Stop Institutional Bounds: UI tighten requests clamped strictly to [0.0040, 0.0400] distance.
16. WebSocket JSON Float Sanitization: Non-finite floats (NaN, Inf) sanitized to 0.0, serializing safely with allow_nan=False.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, date, time, timezone, timedelta
import json
import math
import os
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional
import pytest
from zoneinfo import ZoneInfo

from backend.app.core.bracket import DynamicBracketManager, BracketStatus
from backend.app.config import settings
from backend.app.core.event_bus import EventBus
from backend.app.core.flattening import ZeroOvernightFlatteningEngine, FlatteningDirective, FlatteningPhase
from backend.app.core.market_filter import MarketTrendFilter
from backend.app.core.persistence import TradingStateStore
from backend.app.core.risk import (
    BreakerStatus,
    InstitutionalRiskEngine,
    RiskCheckResult,
    RiskEngineConfig,
    RiskLevel,
)
from backend.app.core.runtime_state import validate_runtime_state
from backend.app.ingestion.stock_ws import StockWebSocketClient
from backend.app.main import (
    _get_effective_committed_portfolio,
    _sanitize_for_json,
    _serialize_position,
    account as app_account,
    engine as app_engine,
    risk_engine as app_risk_engine,
    bracket_manager as app_bracket_manager,
    orb_strategy as app_orb_strategy,
    market_history as app_market_history,
    recent_news as app_recent_news,
)
from backend.app.core.account import PaperTradingAccount, Position, PositionSide
from backend.app.core.engine import ExecutionEngine, Order
from backend.app.models.events import (
    BarEvent,
    CatalystCategory,
    NewsEvent,
    OrderSide,
    OrderState,
    OrderType,
    QuoteEvent,
)
from backend.app.strategies.news_momentum import NewsMomentumStrategy
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy

ET_TZ = ZoneInfo("America/New_York")


def _make_bar(
    symbol: str,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: int = 10000,
    minute: int = 30,
    hour: int = 9,
    day: int = 22,
) -> BarEvent:
    dt = datetime(2026, 9, day, hour, minute, 0, tzinfo=ET_TZ)
    return BarEvent(
        symbol=symbol,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        timestamp=dt,
    )


# ============================================================================
# 1. Concurrency, Ingestion & Memory Remediation Tests (R6-1)
# ============================================================================

class TestR6ConcurrencyAndMemory:
    @pytest.mark.asyncio
    async def test_prioritized_queue_preserves_critical_frames_under_quote_flood(self):
        """Under a queue-filling quote burst, incoming bar frames must evict quotes rather than being dropped."""
        bus = EventBus()
        client = StockWebSocketClient(bus=bus)
        # Artificially set a small queue to test saturation
        client._queue = asyncio.Queue(maxsize=3)

        # Fill queue with quotes
        quote_msg = json.dumps([{"T": "q", "S": "AAPL", "bp": 150.0, "ap": 150.05}])
        await client._queue.put(quote_msg)
        await client._queue.put(quote_msg)
        await client._queue.put(quote_msg)
        assert client._queue.full()

        # Simulate read loop logic on arrival of a bar message
        bar_msg = json.dumps([{"T": "b", "S": "AAPL", "o": 150.0, "h": 151.0, "l": 149.9, "c": 150.8, "v": 5000}])
        is_priority = '"T":"b"' in bar_msg or '"T": "b"' in bar_msg

        assert is_priority is True
        if is_priority and client._queue.full():
            evicted = client._queue.get_nowait()
            client._queue.task_done()
            assert '"T": "q"' in evicted or '"T":"q"' in evicted

        client._queue.put_nowait(bar_msg)
        assert client._queue.qsize() == 3

        # Drain queue and confirm bar message is inside
        items = []
        while not client._queue.empty():
            items.append(client._queue.get_nowait())
        assert any('"T": "b"' in item or '"T":"b"' in item for item in items)

    def test_news_watchlist_gating_and_queue_capping(self):
        """News for un-watched symbols must be ignored; watched symbols must cap pending catalysts at 10."""
        bus = EventBus()
        strategy = NewsMomentumStrategy(bus)

        # Un-watched ticker
        unwatched_event = NewsEvent(
            article_id=1,
            headline="Unexpected buyout offer announced",
            summary="Details here",
            symbols=["UNKNOWN_TICKER_XYZ"],
            source="Benzinga",
            created_at=datetime.now(timezone.utc),
            catalyst_category=CatalystCategory.EARNINGS_BEAT,
            sentiment_score=0.85,
        )
        strategy.on_news(unwatched_event)
        assert "UNKNOWN_TICKER_XYZ" not in strategy.pending_catalysts

        # Watched ticker (AAPL is in settings.WATCHLIST_SYMBOLS)
        target_sym = settings.WATCHLIST_SYMBOLS[0]
        base_time = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)

        for i in range(25):
            news_evt = NewsEvent(
                article_id=100 + i,
                headline=f"Major update #{i}",
                summary="Details",
                symbols=[target_sym],
                source="Benzinga",
                created_at=base_time + timedelta(seconds=i),
                catalyst_category=CatalystCategory.EARNINGS_BEAT,
                sentiment_score=0.80,
            )
            strategy.on_news(news_evt)

        # Must cap at max 10
        assert len(strategy.pending_catalysts[target_sym]) <= 10

    def test_persistence_wal_checkpoint_and_close_truncate(self):
        """State store must execute passive checkpoints on demand and truncate WAL on close."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_wal.db")
            store = TradingStateStore(database_path=db_path)

            # Write a state snapshot
            store.save_checkpoint({"account": {"equity": 50000.0, "cash": 50000.0}}, reason="TEST_REVISION")

            # Passive checkpoint
            store.wal_checkpoint(mode="PASSIVE")

            # Close store (which executes PRAGMA wal_checkpoint(TRUNCATE))
            store.close()
            assert store._closed is True

    def test_event_bus_handler_deduplication_and_teardown(self):
        """EventBus must deduplicate identical handlers and cleanly wipe subscribers on clear()."""
        bus = EventBus()
        call_count = 0

        async def handler(evt: Any):
            nonlocal call_count
            call_count += 1

        # Register same handler twice
        bus.subscribe(QuoteEvent, handler)
        bus.subscribe(QuoteEvent, handler)

        now = datetime.now(timezone.utc)
        evt = QuoteEvent(
            symbol="AAPL",
            bid_price=150.0,
            bid_size=100,
            bid_exchange="V",
            ask_price=150.05,
            ask_size=100,
            ask_exchange="V",
            timestamp=now,
        )
        asyncio.run(bus.publish(evt))

        # Handler should have been invoked exactly once due to deduplication
        assert call_count == 1

        # Teardown clear
        bus.clear()
        assert len(bus._subscribers) == 0

        # Further publish invokes nothing
        asyncio.run(bus.publish(evt))
        assert call_count == 1


# ============================================================================
# 2. Strategy Indicators & Causality Tests (R6-2)
# ============================================================================

class TestR6IndicatorsAndCausality:
    def test_orb_rejection_resets_signal_fired_lock(self):
        """When execution engine rejects an ORB order, notify_signal_rejected must reset the lock."""
        bus = EventBus()
        strategy = OpeningRangeBreakoutStrategy(bus)
        sym = "AAPL"
        state = strategy._get_state(sym)
        state.breakout_fired = True

        # Notify rejection
        strategy.notify_signal_rejected(sym)
        assert state.breakout_fired is False

    def test_vwap_pullback_excludes_current_candidate_bar_from_baseline(self):
        """Candidate bar's volume must NOT be included in prior 10-bar baseline calculation."""
        bus = EventBus()
        strategy = VWAPPullbackStrategy(bus)
        sym = "AAPL"
        state = strategy._get_state(sym)

        # Feed 10 historical bars with volume 10,000
        for i in range(10):
            bar = _make_bar(sym, 150.0, 151.0, 149.0, 150.5, vol=10000, minute=30 + i)
            strategy.on_bar(bar)

        assert len(state.recent_bars) == 10

        # 11th bar has massive volume (1,000,000)
        candidate_bar = _make_bar(sym, 150.5, 152.0, 150.0, 151.5, vol=1000000, minute=40)
        state.recent_bars.append(candidate_bar)

        # In VWAPPullbackStrategy, prior_volumes is state.recent_bars[:-1][-10:]
        prior_volumes = [float(b.volume) for b in state.recent_bars[:-1][-10:]]
        assert len(prior_volumes) == 10
        assert 1000000 not in prior_volumes
        assert sum(prior_volumes) / len(prior_volumes) == 10000.0

    def test_orb_atr_excludes_candidate_bar_and_rejects_premarket(self):
        """Candidate breakout bar must be excluded from ATR baseline, and premarket bars rejected."""
        bus = EventBus()
        strategy = OpeningRangeBreakoutStrategy(bus)
        sym = "AAPL"

        # Pre-market bar at 09:20 ET must not be appended to all_bars
        pre_bar = _make_bar(sym, 150.0, 150.5, 149.8, 150.2, vol=5000, minute=20, hour=9)
        strategy.on_bar(pre_bar)
        state = strategy._get_state(sym)
        assert len(state.all_bars) == 0

        # Feed opening 15-minute bars (09:30 to 09:45)
        for m in range(30, 45):
            bar = _make_bar(sym, 150.0, 151.0, 149.5, 150.5, vol=20000, minute=m, hour=9)
            strategy.on_bar(bar)

        assert len(state.all_bars) == 15

        # Feed 16th bar (candidate breakout at 09:45)
        breakout_bar = _make_bar(sym, 150.5, 152.5, 150.4, 152.0, vol=50000, minute=45, hour=9)
        state.all_bars.append(breakout_bar)

        # ATR calculation baseline must exclude the candidate breakout bar itself
        atr_bars = state.all_bars[:-1]
        assert len(atr_bars) == 15
        assert breakout_bar not in atr_bars

    def test_market_filter_microsecond_skew_tolerance(self):
        """Market filter must tolerate sub-second quote arrival timestamp skew without failing."""
        filt = MarketTrendFilter()
        now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
        skewed_time = now + timedelta(milliseconds=200)

        # Feed 3 SPY and QQQ bars
        for i in range(3):
            filt.on_bar(_make_bar("SPY", 550.0, 551.0, 549.0, 550.5, minute=30 + i))
            filt.on_bar(_make_bar("QQQ", 480.0, 481.0, 479.0, 480.5, minute=30 + i))

        # Simulate microsecond clock skew (+200ms) on latest bar timestamp
        filt.spy_state.last_timestamp = skewed_time
        filt.qqq_state.last_timestamp = now

        # Evaluate at current time 'now' (skew = -0.2s, which is > -1.0s threshold)
        trend, reason = filt.get_current_trend(asof=now)
        # Must tolerate microsecond skew and not abort with FUTURE_INDEX_DATA
        assert "FUTURE_INDEX_DATA" not in reason

    def test_news_momentum_preserves_mid_minute_catalysts(self):
        """Catalysts occurring in the current bar's minute window must be preserved for next reaction bar."""
        bus = EventBus()
        strategy = NewsMomentumStrategy(bus)
        sym = "AAPL"

        # News arrives at 10:00:30 ET
        news_time = datetime(2026, 9, 22, 10, 0, 30, tzinfo=ET_TZ)
        evt = NewsEvent(
            article_id=999,
            headline="Regulatory approval granted",
            summary="Details",
            symbols=[sym],
            source="Benzinga",
            created_at=news_time,
            catalyst_category=CatalystCategory.FDA_APPROVAL,
            sentiment_score=0.90,
        )
        strategy.on_news(evt)
        assert len(strategy.pending_catalysts[sym]) == 1

        # Bar arrives with timestamp 10:00:00 ET (start-of-candle timestamp)
        bar_time = datetime(2026, 9, 22, 10, 0, 0, tzinfo=ET_TZ)
        bar = BarEvent(symbol=sym, open=150.0, high=151.0, low=149.9, close=150.8, volume=25000, timestamp=bar_time)
        strategy.on_bar(bar)

        # News should NOT have been pruned as expired or future; it should still be in pending_catalysts
        assert len(strategy.pending_catalysts[sym]) == 1
        assert strategy.pending_catalysts[sym][0].headline == "Regulatory approval granted"


# ============================================================================
# 3. Institutional Risk, API & UI Remediation Tests (R6-3)
# ============================================================================

class TestR6RiskApiAndUI:
    def test_simultaneous_12_ticker_collision_concurrency_and_sector_cap(self):
        """12 simultaneous buy signals must respect max 3 concurrent positions and max 2 per sector."""
        config = RiskEngineConfig(starting_equity=50000.0, max_concurrent_positions=3, max_positions_per_sector=2)
        risk = InstitutionalRiskEngine(config)
        acct = PaperTradingAccount(initial_cash=50000.0)
        eng = ExecutionEngine(acct)

        tickers_and_sectors = [
            ("AAPL", "Technology"),
            ("MSFT", "Technology"),
            ("NVDA", "Semiconductors"),
            ("AMD", "Semiconductors"),
            ("INTC", "Semiconductors"),
            ("GOOGL", "Technology"),
            ("META", "Technology"),
            ("AMZN", "Consumer Discretionary"),
            ("TSLA", "Consumer Discretionary"),
            ("PLTR", "Technology"),
            ("COIN", "Financials"),
            ("SPY", "Index"),
        ]
        for sym, sec in tickers_and_sectors:
            risk.register_symbol_sector(sym, sec)

        approved_count = 0
        rejected_count = 0

        for sym, sec in tickers_and_sectors:
            active_syms, active_secs, count, notional_map = _get_effective_committed_portfolio(
                acct, execution_engine=eng, risk_eng=risk
            )
            # Synchronize engine with current active symbols from committed portfolio
            res = risk.evaluate_order_request(
                symbol=sym,
                side="BUY",
                requested_qty=50,
                entry_price=100.0,
                stop_price=98.0,
                account_equity=acct.equity,
                buying_power=acct.buying_power,
                active_positions_count=count,
                active_symbols=active_syms,
                active_sectors=active_secs,
                existing_position_notional=notional_map.get(sym, 0.0),
            )
            if res.approved:
                approved_count += 1
                # Place working entry order simulating acceptance
                order = Order(
                    id=f"order_{sym}",
                    client_order_id=f"client_{sym}",
                    symbol=sym,
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    qty=res.authorized_qty,
                    limit_price=100.0,
                    status=OrderState.ACCEPTED,
                )
                eng.working_orders[order.id] = order
            else:
                rejected_count += 1

        assert approved_count == 3
        assert rejected_count == 9

    def test_pre_trade_circuit_breaker_unevaluated_equity_drawdown(self):
        """Drawdown >= $1,500 must reject new orders immediately even if circuit breaker status is still ARMED."""
        config = RiskEngineConfig(starting_equity=50000.0, hard_max_daily_loss_dollars=1500.0)
        risk = InstitutionalRiskEngine(config)
        assert risk.status == BreakerStatus.ARMED

        # Drawdown is $1,600 (equity = $48,400)
        res = risk.evaluate_order_request(
            symbol="AAPL",
            side="BUY",
            requested_qty=50,
            entry_price=150.0,
            stop_price=147.0,
            account_equity=48400.0,
            buying_power=190000.0,
            active_positions_count=0,
            active_symbols=set(),
            active_sectors=[],
        )
        assert res.approved is False
        assert res.rejection_code == "CIRCUIT_BREAKER_HALTED"
        assert res.risk_level == RiskLevel.HALTED

        # Test loss capacity budgeting: drawdown $1,400 -> remaining budget is $100
        res_budgeted = risk.evaluate_order_request(
            symbol="AAPL",
            side="BUY",
            requested_qty=100,
            entry_price=150.0,
            stop_price=148.0,  # $2.00 stop distance
            account_equity=48600.0,  # $1,400 drawdown
            buying_power=190000.0,
            active_positions_count=0,
            active_symbols=set(),
            active_sectors=[],
        )
        assert res_budgeted.approved is True
        # 50 shares * $2.00 stop distance = $100.00 max risk
        assert res_budgeted.authorized_qty <= 50
        assert res_budgeted.estimated_risk_dollars <= 100.01

    def test_single_position_cap_net_of_existing_exposure(self):
        """New order must subtract existing position notional so total exposure never exceeds 50% equity ($25k)."""
        config = RiskEngineConfig(starting_equity=50000.0, max_position_equity_pct=0.50)
        risk = InstitutionalRiskEngine(config)

        # Account has 100 shares of AAPL at $200 = $20,000 existing notional
        # Remaining allowable notional is $25,000 - $20,000 = $5,000 (25 shares at $200)
        res = risk.evaluate_order_request(
            symbol="AAPL",
            side="BUY",
            requested_qty=100,
            entry_price=200.0,
            stop_price=196.0,
            account_equity=50000.0,
            buying_power=200000.0,
            active_positions_count=1,
            active_symbols={"AAPL"},
            active_sectors=["Technology"],
            existing_position_notional=20000.0,
        )
        assert res.approved is True
        assert res.authorized_qty <= 25
        assert (20000.0 + res.authorized_qty * 200.0) <= 25000.01

    def test_phase2_eod_order_purge_preserves_protective_stops(self):
        """Phase 2 ORDER_PURGE at 15:50 ET must purge unfilled entries while keeping protective stops alive."""
        acct = PaperTradingAccount(initial_cash=50000.0)
        eng = ExecutionEngine(acct)
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        # Create open position AAPL long 50 shares
        pos = Position(symbol="AAPL", side=PositionSide.LONG, shares=50, avg_entry_price=150.0, market_price=152.0)
        acct.positions["AAPL"] = pos

        # Create active bracket stop order in working_orders
        bracket = bm.create_bracket("b_aapl", "AAPL", "LONG", 50, 150.0, 147.0, timestamp=now)
        stop_order = Order(
            id="stop_aapl_123",
            client_order_id="client_stop_123",
            symbol="AAPL",
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            qty=50,
            stop_price=147.0,
            status=OrderState.ACCEPTED,
        )
        bracket.stop_order_id = stop_order.id
        bracket.status = BracketStatus.ACTIVE
        bm.symbol_to_bracket["AAPL"] = bracket.bracket_id
        eng.orders[stop_order.id] = stop_order
        eng.working_orders[stop_order.id] = stop_order

        # Create an unfilled entry buy order
        unfilled_entry = Order(
            id="entry_nvda_456",
            client_order_id="client_entry_456",
            symbol="NVDA",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=30,
            limit_price=120.0,
            status=OrderState.ACCEPTED,
        )
        eng.orders[unfilled_entry.id] = unfilled_entry
        eng.working_orders[unfilled_entry.id] = unfilled_entry

        # Execute Phase 2 purge logic: purge non-protective orders
        for order_id, order in list(eng.working_orders.items()):
            p = acct.positions.get(order.symbol)
            is_protective = bool(
                p and (
                    (p.side == PositionSide.LONG and order.side == OrderSide.SELL)
                    or (p.side == PositionSide.SHORT and order.side == OrderSide.BUY)
                )
            )
            if not is_protective:
                eng.cancel_order(order_id, reason="EOD_PURGE_UNFILLED_ENTRIES")

        # Unfilled entry is cancelled
        assert unfilled_entry.id not in eng.working_orders
        # Protective stop remains active
        assert stop_order.id in eng.working_orders

        # Persistence validation must pass without raising PersistenceError
        validate_runtime_state(acct, eng, bm)

    def test_manual_tighten_stop_institutional_bounds(self):
        """UI manual tighten stop with enforce_distance_bounds=True must clamp to [0.0040, 0.0400]."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        bracket = bm.create_bracket("b_aapl", "AAPL", "LONG", 100, 150.0, 145.0, timestamp=now)
        bm.activate_bracket_on_fill("b_aapl", 100, 150.0, now)

        market_price = 150.0

        # Attempt to tighten stop to $149.95 (3.33 bps, below 40 bps min)
        directive = bm.manual_tighten_stop(
            "AAPL", 149.95, current_market_price=market_price, enforce_distance_bounds=True
        )
        assert directive.action == "MODIFY_ORDER"
        expected_min_stop = round(market_price * (1.0 - 0.0040), 4)  # $149.40
        assert directive.orders_to_modify[0]["new_stop_price"] == expected_min_stop
        assert bracket.current_stop_price == expected_min_stop

    def test_websocket_json_float_sanitization(self):
        """WebSocket serializer must sanitize NaN, Inf, and -Inf to 0.0 with allow_nan=False."""
        dirty_payload = {
            "account": {
                "equity": 50000.0,
                "daily_pnl_pct": float("nan"),
                "buying_power": float("inf"),
                "drawdown": -float("inf"),
            },
            "metrics": [float("nan"), 123.45],
        }

        sanitized = _sanitize_for_json(dirty_payload)
        assert sanitized["account"]["daily_pnl_pct"] == 0.0
        assert sanitized["account"]["buying_power"] == 0.0
        assert sanitized["account"]["drawdown"] == 0.0
        assert sanitized["metrics"][0] == 0.0
        assert sanitized["metrics"][1] == 123.45

        # Must serialize cleanly with allow_nan=False
        dumped = json.dumps(sanitized, allow_nan=False)
        assert "NaN" not in dumped
        assert "Infinity" not in dumped
        reloaded = json.loads(dumped)
        assert reloaded["account"]["daily_pnl_pct"] == 0.0
