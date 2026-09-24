"""backend/app/main.py
FastAPI Core Trading Engine API Server & Real-Time WebSocket Streaming (Port 8005).
"""
from __future__ import annotations
import asyncio
import time as _time_mod
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.config import settings
from backend.app.core.account import PaperTradingAccount, PositionSide, TradingArm
from backend.app.core.bracket import BracketChildType, BracketStatus, DynamicBracketManager
from backend.app.core.market_filter import MarketTrendFilter
from backend.app.core.engine import BracketRole, ExecutionEngine, OrderSide, OrderType
from backend.app.core.event_bus import event_bus
from backend.app.core.flattening import ET_TZ, FlatteningDirective, FlatteningPhase, ZeroOvernightFlatteningEngine
from backend.app.core.persistence import (
    SCHEMA_VERSION,
    PersistenceError,
    TradingStateStore,
    encode_runtime_value,
)
from backend.app.core.risk import BreakerStatus, InstitutionalRiskEngine, RiskEngineConfig
from backend.app.core.runtime_state import capture_runtime_state, restore_runtime_state
from backend.app.ingestion.news_ws import NewsWebSocketClient
from backend.app.ingestion.stock_ws import StockWebSocketClient
from backend.app.ingestion.vix_client import VixClient
from backend.app.models.events import BarEvent, QuoteEvent, TradeEvent, NewsEvent, VixPrint, RelayStatusEvent
from backend.app.strategies.base import Strategy, SignalEvent
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy
from backend.app.strategies.mean_reversion import MeanReversionStrategy
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.swing_indicators import DailyBarStore, DailyBarAggregator
from backend.app.core.decisions import decision_log, classify_adaptation_reason
from backend.app.core.trading_windows import is_trading_day, strategy_window
from backend.app.strategies.earnings_calendar import EarningsCalendar
from backend.app.strategies.swing_panic_dip import (
    SwingStrategyEngine,
    SwingStagedOrderManager,
)

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
log = logging.getLogger("AutonomousDayTrader")

# System Components
account = PaperTradingAccount(
    initial_cash=settings.INITIAL_CASH,
    leverage=settings.DAY_TRADING_LEVERAGE,
    max_position_notional=settings.MAX_POSITION_NOTIONAL,
)
risk_engine = InstitutionalRiskEngine(config=RiskEngineConfig(
    starting_equity=settings.INITIAL_CASH,
    hard_max_daily_loss_dollars=settings.MAX_DAILY_LOSS_LIMIT,
    base_trade_risk_pct=settings.PER_POSITION_RISK_PCT,
    max_position_equity_pct=settings.MAX_POSITION_NOTIONAL / settings.INITIAL_CASH,
    max_concurrent_positions=settings.MAX_CONCURRENT_POSITIONS,
))
bracket_manager = DynamicBracketManager()
flattening_engine = ZeroOvernightFlatteningEngine()
market_filter = MarketTrendFilter()

# Strategies & Dynamic Self-Adaptation Engine
orb_strategy = OpeningRangeBreakoutStrategy()
vwap_strategy = VWAPPullbackStrategy()
news_strategy = NewsMomentumStrategy()
mean_reversion_strategy = MeanReversionStrategy()
adaptation_engine = DynamicAdaptationEngine(
    max_concurrent_positions=settings.MAX_CONCURRENT_POSITIONS,
    base_risk_pct=settings.PER_POSITION_RISK_PCT,
    market_filter=market_filter,
)

strategies: List[Strategy] = [
    orb_strategy,
    vwap_strategy,
    news_strategy,
    mean_reversion_strategy,
]
strategy_map: Dict[str, Strategy] = {s.strategy_id: s for s in strategies}

# Real-time symbol market price cache for pre-trade risk valuation
latest_market_prices: Dict[str, float] = {}
today_open_prices: Dict[str, float] = {}
market_history: Dict[str, List[Dict[str, Any]]] = {}
entry_order_to_bracket: Dict[str, str] = {}
bracket_realized_pnl: Dict[str, float] = {}
relay_statuses: Dict[str, str] = {"stock": "unconfigured", "news": "unconfigured", "vix": "unconfigured"}
# Wall-clock time of the last event actually ingested per feed. A relay status of
# "connected" is set once at handshake, so a live-but-silent feed looks identical to a
# working one. /health publishes these ages so feed starvation is visible from outside.
feed_last_event: Dict[str, Optional[datetime]] = {
    "bar": None, "quote": None, "trade": None, "news": None, "vix": None,
}


def _mark_feed_event(feed: str) -> None:
    feed_last_event[feed] = datetime.now(timezone.utc)

runtime_tasks: Set[asyncio.Task] = set()
simulation_mode: bool = False
last_session_date: Optional[Any] = None
completed_brackets_recorded: Set[str] = set()


# Swing Trading Arm Symbol Reservation State
swing_reserved_symbols: Set[str] = set()


def reserve_symbol_for_swing(symbol: str) -> None:
    """Reserve a symbol for the swing trading engine, locking out intraday entries."""
    swing_reserved_symbols.add(symbol.upper())


def release_symbol_for_swing(symbol: str) -> None:
    """Release a symbol from swing reservation."""
    swing_reserved_symbols.discard(symbol.upper())


def is_symbol_reserved_for_swing(
    symbol: str,
    acct: Optional[PaperTradingAccount] = None,
    eng: Optional[Any] = None,
) -> bool:
    """Check if a symbol is currently reserved, actively held, or has working orders in the swing arm."""
    sym = symbol.upper()
    if sym in swing_reserved_symbols:
        return True
    target_acct = acct or globals().get("account")
    if target_acct and hasattr(target_acct, "positions"):
        pos = target_acct.positions.get(sym)
        if pos is not None and (
            getattr(pos, "arm", None) == TradingArm.SWING
            or getattr(pos, "strategy_id", "") == "swing_panic_dip"
        ):
            return True
    target_engine = eng or globals().get("engine")
    if target_engine and hasattr(target_engine, "working_orders"):
        for w_order in target_engine.working_orders.values():
            if w_order.symbol.upper() == sym and (
                getattr(w_order, "arm", None) == TradingArm.SWING
                or getattr(w_order, "strategy_id", "") == "swing_panic_dip"
            ):
                return True
    return False



def _get_effective_committed_portfolio(
    acct: PaperTradingAccount,
    execution_engine: Optional[Any] = None,
    risk_eng: Optional[Any] = None,
    bracket_mgr: Optional[Any] = None,
    arm: Optional[TradingArm] = None,
) -> tuple[set[str], list[str], int, dict[str, float]]:
    """Compute active symbols, active sectors, committed position count, and exposure notional map.
    Includes filled positions and in-flight entry commitments (working orders / pending brackets).
    Optionally filters by TradingArm (INTRADAY or SWING).
    """
    if arm == TradingArm.INTRADAY:
        committed_symbols = {
            sym for sym, pos in acct.positions.items()
            if getattr(pos, "arm", None) != TradingArm.SWING and getattr(pos, "strategy_id", "") != "swing_panic_dip"
        }
    elif arm == TradingArm.SWING:
        committed_symbols = {
            sym for sym, pos in acct.positions.items()
            if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip"
        }
    else:
        committed_symbols = set(acct.positions.keys())

    existing_notional: dict[str, float] = {}

    for sym in committed_symbols:
        pos = acct.positions.get(sym)
        if pos:
            existing_notional[sym.upper()] = pos.shares * (pos.market_price if pos.market_price > 0 else pos.avg_entry_price)

    eng = execution_engine or globals().get("engine")
    if eng is not None and hasattr(eng, "working_orders"):
        for order in list(eng.working_orders.values()):
            if arm == TradingArm.INTRADAY and (getattr(order, "arm", None) == TradingArm.SWING or getattr(order, "strategy_id", "") == "swing_panic_dip"):
                continue
            if arm == TradingArm.SWING and (getattr(order, "arm", None) != TradingArm.SWING and getattr(order, "strategy_id", "") != "swing_panic_dip"):
                continue
            if order.status.value in ("ACCEPTED", "PARTIALLY_FILLED"):
                sym = order.symbol.upper()
                pos = acct.positions.get(sym)
                is_reducing = bool(
                    pos and (
                        (pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
                        (pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
                    )
                )
                if not is_reducing:
                    committed_symbols.add(sym)
                    order_price = order.limit_price or order.estimated_price or 0.0
                    existing_notional[sym] = existing_notional.get(sym, 0.0) + (order.remaining_qty * order_price)

    bm = bracket_mgr or globals().get("bracket_manager")
    if bm is not None and hasattr(bm, "brackets"):
        for b in list(bm.brackets.values()):
            if arm == TradingArm.INTRADAY and (getattr(b, "arm", None) == TradingArm.SWING or getattr(b, "strategy_id", "") == "swing_panic_dip"):
                continue
            if arm == TradingArm.SWING and (getattr(b, "arm", None) != TradingArm.SWING and getattr(b, "strategy_id", "") != "swing_panic_dip"):
                continue
            if b.status == BracketStatus.PENDING_ENTRY:
                sym = b.symbol.upper()
                committed_symbols.add(sym)
                if sym not in existing_notional:
                    existing_notional[sym] = b.total_qty * b.entry_price

    re = risk_eng or globals().get("risk_engine")
    committed_sectors = [
        re.symbol_sectors.get(s, "Other")
        for s in committed_symbols
        if re and hasattr(re, "symbol_sectors") and s in re.symbol_sectors
    ]
    return committed_symbols, committed_sectors, len(committed_symbols), existing_notional


def pre_trade_risk_validator(order: Any, acct: PaperTradingAccount) -> tuple[bool, str]:
    """Validate order against Institutional Risk Engine, active flattening lockout, and arm separation."""
    order_arm = getattr(order, "arm", TradingArm.INTRADAY)
    order_strat = getattr(order, "strategy_id", None)
    is_swing = (order_arm == TradingArm.SWING or order_strat == "swing_panic_dip")
    sym = order.symbol.upper()

    # Differentiate position-reducing / liquidation orders from position-opening orders
    existing_pos = acct.positions.get(sym)
    existing_arm = getattr(existing_pos, "arm", None) or TradingArm.INTRADAY if existing_pos else None
    existing_strat = getattr(existing_pos, "strategy_id", "") or ""
    existing_is_swing = bool(
        existing_pos is not None
        and (
            existing_arm == TradingArm.SWING
            or existing_strat == "swing_panic_dip"
            or (isinstance(existing_arm, str) and str(existing_arm).upper() == "SWING")
        )
    )

    is_exit = False
    if getattr(order, "strategy_id", None) in (
        "CIRCUIT_BREAKER",
        "AUTO_FLATTEN",
        "EMERGENCY_SWEEP",
        "MANUAL_FLATTEN",
        "NEWS_CONTRADICTION",
        "NEWS_CONTRADICTION_CIRCUIT_BREAKER",
        "SESSION_BOUNDARY_LIQUIDATION",
    ):
        if existing_pos is None or not existing_is_swing:
            is_exit = True
    elif existing_pos is not None and (existing_is_swing == is_swing):
        if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
            is_exit = True
        elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
            is_exit = True

    # A failed durable write must never be followed by more exposure that only
    # exists in volatile memory. Position-reducing orders remain available.
    if state_store is not None and not persistence_healthy and not is_exit:
        return False, "PERSISTENCE_RECOVERY_HALT: durable ledger is unavailable"

    # Symbol reservation / mutual exclusion check:
    if not is_exit:
        target_engine = globals().get("engine")
        if is_swing:
            # Swing cannot enter if an INTRADAY position is currently open for this symbol
            if existing_pos is not None and (
                getattr(existing_pos, "arm", None) != TradingArm.SWING
                and getattr(existing_pos, "strategy_id", "") != "swing_panic_dip"
            ):
                return False, f"SWING_REJECTED: Symbol {sym} is currently held by Intraday strategy"
            # Swing cannot enter if an INTRADAY order is currently working for this symbol
            if target_engine and hasattr(target_engine, "working_orders"):
                for w_order in target_engine.working_orders.values():
                    if w_order.symbol.upper() == sym and (
                        getattr(w_order, "arm", None) != TradingArm.SWING
                        and getattr(w_order, "strategy_id", "") != "swing_panic_dip"
                    ):
                        return False, f"SWING_REJECTED: Symbol {sym} has active working order in Intraday strategy"
        else:
            # Intraday cannot enter if symbol is reserved for Swing or currently held by Swing
            if is_symbol_reserved_for_swing(sym, acct, target_engine):
                return False, f"SYMBOL_RESERVED_FOR_SWING: Intraday entry for {sym} rejected because symbol is reserved/held by Swing Engine"


    # Route portfolio commitment and lockout by arm
    if is_swing:
        active_symbols, active_sectors, committed_count, notional_map = _get_effective_committed_portfolio(
            acct, arm=TradingArm.SWING
        )
        is_lockout = False  # Swing orders are exempt from intraday EOD flattening lockout
    else:
        active_symbols, active_sectors, committed_count, notional_map = _get_effective_committed_portfolio(
            acct, arm=TradingArm.INTRADAY
        )
        is_lockout = flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING

    # Estimate entry price (do NOT set est_price = order.stop_price!)
    est_price = order.limit_price
    if not est_price:
        if getattr(order, "estimated_price", None):
            est_price = getattr(order, "estimated_price")
        elif sym in acct.positions and acct.positions[sym].market_price > 0:
            est_price = acct.positions[sym].market_price
        elif sym in latest_market_prices and latest_market_prices[sym] > 0:
            est_price = latest_market_prices[sym]
        elif order.stop_price:
            est_price = round(order.stop_price / 0.98 if order.side == OrderSide.BUY else order.stop_price / 1.02, 2)
        else:
            est_price = 100.0

    s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)

    active_cnt = (
        sum(1 for p in acct.positions.values() if getattr(p, "arm", None) != TradingArm.SWING and getattr(p, "strategy_id", "") != "swing_panic_dip")
        if (getattr(order, "strategy_id", None) == "MANUAL" and not is_swing)
        else committed_count
    )

    res = risk_engine.evaluate_order_request(
        symbol=order.symbol,
        side=order.side.value,
        requested_qty=order.qty,
        entry_price=est_price,
        stop_price=s_price,
        account_equity=acct.equity,
        buying_power=acct.buying_power,
        active_positions_count=active_cnt,
        active_symbols=active_symbols,
        active_sectors=active_sectors,
        vix_multiplier=adaptation_engine.current_sizing_multiplier,
        is_entry_lockout_active=is_lockout,
        is_exit=is_exit,
        existing_position_notional=notional_map.get(sym, 0.0),
        strategy_id=order_strat,
        arm=order_arm,
        active_swing_positions_count=committed_count if is_swing else 0,
    )
    if res.approved and not is_exit and order.qty > res.authorized_qty:
        return False, f"RISK_SIZE_REJECTED: requested {order.qty} exceeds authorized {res.authorized_qty} shares"
    return res.approved, res.reason


engine = ExecutionEngine(account=account, risk_validator=pre_trade_risk_validator)

# Swing Trading Infrastructure & Engine
daily_bar_store = DailyBarStore(seed_path=settings.DAILY_BARS_SEED_PATH)
daily_bar_aggregator = DailyBarAggregator(store=daily_bar_store)
earnings_calendar = EarningsCalendar(
    seed_path=settings.EARNINGS_CALENDAR_SEED_PATH,
    remote_url=settings.EARNINGS_CALENDAR_REMOTE_URL,
    cache_path=getattr(settings, "EARNINGS_CALENDAR_CACHE_PATH", None),
)
swing_staged_order_manager = SwingStagedOrderManager()
swing_strategy_engine = SwingStrategyEngine(
    account=account,
    execution_engine=engine,
    risk_engine=risk_engine,
    bar_store=daily_bar_store,
    calendar=earnings_calendar,
    staged_manager=swing_staged_order_manager,
    symbols=settings.SWING_SYMBOLS,
    benchmark=settings.SWING_BENCHMARK,
    slot_notional=settings.SWING_SLOT_NOTIONAL,
    max_concurrent_positions=settings.SWING_MAX_CONCURRENT_POSITIONS,
    reserve_symbol_cb=reserve_symbol_for_swing,
    release_symbol_cb=release_symbol_for_swing,
    is_reserved_cb=is_symbol_reserved_for_swing,
)

# Feed Ingestion Clients
stock_ws_client: Optional[StockWebSocketClient] = None
news_ws_client: Optional[NewsWebSocketClient] = None
vix_client: Optional[VixClient] = None

# Active UI WebSocket Clients
ui_clients: Set[WebSocket] = set()
recent_news: List[Dict[str, Any]] = []
last_vix_print: Optional[VixPrint] = None

# Durable state is opt-in outside production so deterministic test/replay runs
# cannot contaminate the live paper ledger. Railway enables it on a volume.
state_store: Optional[TradingStateStore] = (
    TradingStateStore(settings.STATE_DB_PATH) if settings.PERSISTENCE_ENABLED else None
)
persistence_healthy: bool = state_store is not None or not settings.PERSISTENCE_REQUIRED
persistence_error: Optional[str] = None
persistence_restored: bool = False
persistence_revision: int = 0
ledger_revision: int = 0
pending_trade_records: Dict[str, Dict[str, Any]] = {}
pending_session_summaries: Dict[str, Dict[str, Any]] = {}
pending_processed_events: Dict[str, str] = {}
inflight_event_keys: Set[str] = set()


def _capture_checkpoint() -> Dict[str, Any]:
    return capture_runtime_state(
        account=account,
        engine=engine,
        bracket_manager=bracket_manager,
        risk_engine=risk_engine,
        flattening_engine=flattening_engine,
        adaptation_engine=adaptation_engine,
        strategies=strategies,
        entry_order_to_bracket=entry_order_to_bracket,
        bracket_realized_pnl=bracket_realized_pnl,
        completed_brackets_recorded=completed_brackets_recorded,
        latest_market_prices=latest_market_prices,
        market_history=market_history,
        recent_news=recent_news,
        last_session_date=last_session_date,
        last_vix_print=last_vix_print,
        ledger_revision=ledger_revision,
        swing_staged_orders=swing_staged_order_manager.get_staged_orders(),
        swing_reserved_symbols=swing_reserved_symbols,
        daily_bar_store=daily_bar_store,
        decisions=decision_log.to_state(),
    )



def _checkpoint_runtime(
    reason: str, processed_event: Optional[Tuple[str, str]] = None
) -> bool:
    """Atomically persist all recovery state and pending immutable ledger rows."""
    global persistence_healthy, persistence_error, persistence_revision, ledger_revision
    if state_store is None or simulation_mode:
        return True
    # Nested mutations (session rollover, flattening) can occur while a market
    # input is being applied. They must not checkpoint a half-applied input;
    # the outer handler commits the complete result with the inbox marker.
    if inflight_event_keys and not pending_processed_events and processed_event is None:
        return True
    if processed_event is not None:
        pending_processed_events[processed_event[0]] = processed_event[1]
    try:
        revision, inserted_trades = state_store.save_checkpoint(
            _capture_checkpoint(),
            reason=reason,
            trades=list(pending_trade_records.values()),
            session_summaries=list(pending_session_summaries.values()),
            processed_events=list(pending_processed_events.items()),
        )
        persistence_revision = revision
        ledger_revision += inserted_trades
        pending_trade_records.clear()
        pending_session_summaries.clear()
        for event_key in list(pending_processed_events):
            inflight_event_keys.discard(event_key)
        pending_processed_events.clear()
        persistence_healthy = True
        persistence_error = None
        if settings.STATE_BACKUP_PATH and reason in {"SESSION_BOUNDARY", "GRACEFUL_SHUTDOWN"}:
            state_store.backup(settings.STATE_BACKUP_PATH)
        return True
    except Exception as exc:
        persistence_healthy = False
        persistence_error = f"{type(exc).__name__}: {exc}"
        log.exception("Durable checkpoint failed; new entries are locked out")
        # Remove only exposure-increasing orders. Protective/position-reducing
        # orders stay live so recovery halt cannot strand an open position.
        for order_id, order in list(engine.working_orders.items()):
            position = account.positions.get(order.symbol)
            is_reducing = bool(
                position
                and (
                    (position.side == PositionSide.LONG and order.side == OrderSide.SELL)
                    or (position.side == PositionSide.SHORT and order.side == OrderSide.BUY)
                )
            )
            is_pending_entry = order_id in entry_order_to_bracket
            if is_pending_entry or (order.parent_order_id is None and not is_reducing):
                try:
                    engine.cancel_order(order_id, reason="PERSISTENCE_RECOVERY_HALT")
                except Exception:
                    log.exception("Could not cancel opening order %s during recovery halt", order_id)
        _release_dead_entry_brackets()
        return False


def _event_key(event_type: str, event: Any) -> str:
    encoded = encode_runtime_value(event)
    raw = json.dumps(encoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{event_type.lower()}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _begin_durable_event(event_type: str, event: Any) -> Tuple[bool, Optional[str]]:
    """Write an input to the durable inbox before it can fill or mutate state."""
    global persistence_healthy, persistence_error
    if state_store is None or simulation_mode:
        return True, None
    event_key = _event_key(event_type, event)
    if event_key in inflight_event_keys:
        return False, event_key
    try:
        should_process = state_store.begin_event(event_key, event_type, event)
    except Exception as exc:
        persistence_healthy = False
        persistence_error = f"{type(exc).__name__}: {exc}"
        log.exception("Could not stage %s input; event rejected before mutation", event_type)
        return False, event_key
    if not should_process:
        return False, event_key
    inflight_event_keys.add(event_key)
    return True, event_key


def _restore_checkpoint() -> bool:
    """Restore production state before any relay or clock task can mutate it."""
    global last_session_date, last_vix_print, persistence_healthy
    global persistence_error, persistence_restored, persistence_revision, ledger_revision
    if state_store is None:
        if settings.PERSISTENCE_REQUIRED:
            raise PersistenceError("Persistence is required but PERSISTENCE_ENABLED is false")
        return False
    state_store.integrity_check()
    loaded = state_store.load_checkpoint()
    if loaded is None:
        if settings.PERSISTENCE_REQUIRED:
            raise PersistenceError(
                "Persistence is required but the durable store has no runtime checkpoint"
            )
        _checkpoint_runtime("INITIALIZE_FRESH_ACCOUNT")
        return False
    payload, revision, _saved_at = loaded
    restored = restore_runtime_state(
        payload,
        account=account,
        engine=engine,
        bracket_manager=bracket_manager,
        risk_engine=risk_engine,
        flattening_engine=flattening_engine,
        adaptation_engine=adaptation_engine,
        strategies=strategies,
        entry_order_to_bracket=entry_order_to_bracket,
        bracket_realized_pnl=bracket_realized_pnl,
        completed_brackets_recorded=completed_brackets_recorded,
        latest_market_prices=latest_market_prices,
        market_history=market_history,
        recent_news=recent_news,
        swing_staged_order_manager=swing_staged_order_manager,
        swing_reserved_symbols=swing_reserved_symbols,
        daily_bar_store=daily_bar_store,
    )

    last_session_date = restored["last_session_date"]
    last_vix_print = restored["last_vix_print"]
    decision_log.load_state(restored.get("decisions"))
    persistence_revision = revision
    ledger_revision = max(restored["ledger_revision"], state_store.trade_count())
    persistence_healthy = True
    persistence_error = None
    persistence_restored = True
    log.info(
        "Restored durable trading state revision %d: equity=$%.2f, positions=%d, trades=%d",
        revision,
        account.equity,
        len(account.positions),
        state_store.trade_count(),
    )
    return True


def _atr_estimate(symbol: str, bar: BarEvent, period: int = 14) -> float:
    """Average true range over `period` bars, for the trailing stop distance.

    This used to be one bar's high minus its low. The trailing ratchet multiplies this
    and never loosens, so a single quiet minute collapsed the trail to a few cents and
    jammed the stop under the market permanently. Observed live 2026-09-21: an NVDA ORB
    entry's stop walked from 0.55% of entry to 0.127% in three minutes and was scratched
    for -$9.37 six minutes after entry, with its 1.5R target left unreachable.

    True range is the usual max(high-low, |high-prev_close|, |low-prev_close|), so gaps
    between bars count. Falls back to the current bar's range only when there is not yet
    enough history to average.
    """
    history = market_history.get(symbol.upper(), [])
    if len(history) < 2:
        return max(0.01, bar.high - bar.low)
    window = history[-(period + 1):]
    true_ranges = [
        max(cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]))
        for prev, cur in zip(window, window[1:])
    ]
    if not true_ranges:
        return max(0.01, bar.high - bar.low)
    return max(0.01, sum(true_ranges) / len(true_ranges))


def _serialize_position(symbol: str, include_chart: bool = True) -> Dict[str, Any]:
    """Serialize one position with the bracket and chart data that actually backs the UI."""
    pos = account.positions[symbol]
    bracket_id = bracket_manager.symbol_to_bracket.get(symbol)
    bracket = bracket_manager.brackets.get(bracket_id) if bracket_id else None
    pos_data = {
        "symbol": pos.symbol,
        "side": pos.side.value,
        "shares": pos.shares,
        "qty": pos.shares,
        "entry_price": pos.avg_entry_price,
        "avg_entry_price": pos.avg_entry_price,
        "market_price": pos.market_price,
        "current_price": pos.market_price,
        "market_value": pos.market_value,
        "cost_basis": pos.cost_basis,
        "unrealized_pnl": pos.unrealized_pnl,
        "unrealized_pnl_pct": pos.unrealized_pnl_pct,
        "realized_pnl": pos.realized_pnl,
        "fees_paid": pos.fees_paid,
        "stop_loss": bracket.current_stop_price if bracket else None,
        "take_profit_1": bracket.target_1_price if bracket else None,
        "take_profit_2": bracket.target_2_price if bracket else None,
        "strategy_id": bracket.strategy_id if bracket else "manual",
    }
    if include_chart:
        history = market_history.get(symbol, [])
        pos_data["chart_points"] = list(history)[-120:]
    return pos_data


def _apply_bracket_directive(bracket_id: str, directive: Any) -> None:
    """Materialize bracket-manager directives into real execution-engine orders."""
    bracket = bracket_manager.brackets.get(bracket_id)
    if not bracket:
        return

    for order_id in directive.orders_to_cancel:
        if order_id in engine.working_orders:
            try:
                engine.cancel_order(order_id, reason=f"BRACKET_{directive.bracket_status.value}")
            except Exception as exc:
                log.warning("Could not cancel bracket child %s: %s", order_id, exc)

    if directive.action == "SUBMIT_ORDERS":
        role_by_synthetic = {
            bracket.stop_order_id: BracketChildType.STOP_LOSS,
            bracket.target_1_order_id: BracketChildType.TAKE_PROFIT_1,
            bracket.target_2_order_id: BracketChildType.TAKE_PROFIT_2,
        }
        for child in directive.orders_to_submit:
            synthetic_id = child.get("order_id")
            child_type = role_by_synthetic.get(synthetic_id)
            if child_type is None:
                log.error("Unknown bracket child id %s for %s", synthetic_id, bracket_id)
                continue
            order_type = OrderType.STOP if child["type"] == "STOP" else OrderType.LIMIT
            child_order = engine.create_order(
                symbol=bracket.symbol,
                side=OrderSide(child["side"]),
                order_type=order_type,
                qty=int(child["qty"]),
                limit_price=float(child["price"]) if order_type == OrderType.LIMIT else None,
                stop_price=float(child["price"]) if order_type == OrderType.STOP else None,
                strategy_id=bracket.strategy_id,
                bracket_role=(
                    BracketRole.STOP_LOSS if child_type == BracketChildType.STOP_LOSS
                    else BracketRole.TAKE_PROFIT_1 if child_type == BracketChildType.TAKE_PROFIT_1
                    else BracketRole.TAKE_PROFIT_2
                ),
                parent_order_id=bracket_id,
            )
            submitted = engine.submit_order(child_order.id)
            if submitted.status.value != "ACCEPTED":
                log.error("Bracket child %s rejected: %s", child_type.value, submitted.reject_reason)
                continue

            # Replace the manager's synthetic child id with the real order id so
            # matching/fills/cancellation use one identifier all the way through.
            bracket_manager.order_to_bracket.pop(synthetic_id, None)
            bracket_manager.order_to_bracket[child_order.id] = (bracket_id, child_type)
            if child_type == BracketChildType.STOP_LOSS:
                bracket.stop_order_id = child_order.id
            elif child_type == BracketChildType.TAKE_PROFIT_1:
                bracket.target_1_order_id = child_order.id
            else:
                bracket.target_2_order_id = child_order.id

    for modification in directive.orders_to_modify:
        order = engine.working_orders.get(modification.get("order_id"))
        if not order:
            continue
        if "new_qty" in modification:
            new_qty = int(modification["new_qty"])
            order.remaining_qty = new_qty
            order.qty = order.filled_qty + new_qty
        if "new_stop_price" in modification:
            order.stop_price = float(modification["new_stop_price"])


def _completed_trade_record(bracket_id: str, realized_pnl: float) -> Optional[Dict[str, Any]]:
    """Build one immutable closed-trade row from its entry and exit fill legs."""
    bracket = bracket_manager.brackets.get(bracket_id)
    if not bracket:
        return None
    entry_order_id = bracket_id[4:] if bracket_id.startswith("brk_") else None
    entry_order = engine.orders.get(entry_order_id or "")
    entry_fills = list(entry_order.fills) if entry_order else []
    exit_orders = [order for order in engine.orders.values() if order.parent_order_id == bracket_id]
    exit_fills = [fill for order in exit_orders for fill in order.fills]
    if not entry_fills or not exit_fills:
        return None
    entry_qty = sum(fill.qty for fill in entry_fills)
    exit_qty = sum(fill.qty for fill in exit_fills)
    avg_entry = sum(fill.qty * fill.price for fill in entry_fills) / max(1, entry_qty)
    avg_exit = sum(fill.qty * fill.price for fill in exit_fills) / max(1, exit_qty)
    opened_at = min(fill.timestamp for fill in entry_fills)
    closed_at = max(fill.timestamp for fill in exit_fills)
    opened_et = opened_at.astimezone(ET_TZ) if opened_at.tzinfo else opened_at.replace(tzinfo=timezone.utc).astimezone(ET_TZ)
    all_fills = entry_fills + exit_fills
    return {
        "trade_id": bracket_id,
        # A recovery liquidation after midnight still belongs to the session
        # where exposure was opened; otherwise the prior-day summary omits it.
        "session_date": opened_et.date().isoformat(),
        "symbol": bracket.symbol,
        "side": bracket.side,
        "status": "CLOSED",
        "strategy_id": bracket.strategy_id,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "quantity": min(entry_qty, exit_qty),
        "avg_entry_price": round(avg_entry, 4),
        "avg_exit_price": round(avg_exit, 4),
        "realized_pnl": round(realized_pnl, 2),
        "fees": round(sum(fill.fee for fill in all_fills), 4),
        "exit_reason": bracket.status.value,
        "aggregate_only": False,
        "fill_legs": [
            {
                "fill_id": fill.fill_id,
                "order_id": fill.order_id,
                "side": fill.side.value,
                "qty": fill.qty,
                "price": fill.price,
                "fee": fill.fee,
                "realized_pnl": fill.realized_pnl,
                "timestamp": fill.timestamp.isoformat(),
            }
            for fill in all_fills
        ],
    }


def _session_summary(session_day: Any, source: str = "SYSTEM") -> Dict[str, Any]:
    session_date = session_day.isoformat() if hasattr(session_day, "isoformat") else str(session_day)
    trades_by_id: Dict[str, Dict[str, Any]] = {}
    if state_store is not None:
        for trade in state_store.list_trades_for_session(session_date):
            trades_by_id[trade["trade_id"]] = trade
    for trade in pending_trade_records.values():
        if trade.get("session_date") == session_date:
            trades_by_id[trade["trade_id"]] = trade
    session_fees = sum(float(trade.get("fees", 0.0)) for trade in trades_by_id.values())
    return {
        "session_date": session_date,
        "opening_equity": round(account.daily_starting_equity, 2),
        "closing_equity": round(account.equity, 2),
        "realized_pnl": round(account.equity - account.daily_starting_equity, 2),
        "trades_count": len(trades_by_id),
        "wins": sum(1 for trade in trades_by_id.values() if float(trade.get("realized_pnl", 0)) > 0),
        "losses": sum(1 for trade in trades_by_id.values() if float(trade.get("realized_pnl", 0)) < 0),
        "fees": round(session_fees, 4),
        "strategies": {
            strategy.strategy_id: {
                "trades_count": strategy.trades_count,
                "realized_pnl": strategy.daily_pnl,
                "signals": decision_log.summary(strategy.strategy_id)["signals_today"],
                "orders": decision_log.summary(strategy.strategy_id)["orders_today"],
                "blocked_by_reason": decision_log.summary(strategy.strategy_id)["blocked_by_reason"],
            }
            for strategy in strategies
        },
        "source": source,
        "aggregate_only": source == "LEGACY_SUMMARY_IMPORT",
    }


def _record_completed_bracket(bracket_id: str) -> None:
    if bracket_id in completed_brackets_recorded:
        return
    bracket = bracket_manager.brackets.get(bracket_id)
    if not bracket:
        return
    completed_brackets_recorded.add(bracket_id)
    realized_pnl = bracket_realized_pnl.get(bracket_id, 0.0)
    strategy = strategy_map.get(bracket.strategy_id)
    if strategy:
        strategy.record_trade(realized_pnl)
    trade = _completed_trade_record(bracket_id, realized_pnl)
    if trade:
        pending_trade_records[trade["trade_id"]] = trade
    bracket_realized_pnl.pop(bracket_id, None)


def _record_exit_fill(order: Any, fill: Any) -> bool:
    """Attach a manual/contradiction/flatten fill to its original bracket."""
    bracket_id = getattr(order, "parent_order_id", None)
    if not bracket_id or bracket_id not in bracket_manager.brackets:
        return False
    bracket_realized_pnl[bracket_id] = round(
        bracket_realized_pnl.get(bracket_id, 0.0) + fill.realized_pnl, 2
    )
    if order.status.value == "FILLED":
        bracket_manager.brackets[bracket_id].status = bracket_manager.brackets[bracket_id].status.__class__.COMPLETED_FLATTEN
        bracket_manager.symbol_to_bracket.pop(order.symbol, None)
        _record_completed_bracket(bracket_id)
    return True


def _reconcile_fills(fills: List[Any]) -> None:
    """Connect engine fills to bracket protection and strategy performance."""
    for fill in fills:
        order = engine.orders.get(fill.order_id)
        if not order:
            continue

        bracket_id = entry_order_to_bracket.get(order.id)
        if bracket_id:
            bracket = bracket_manager.brackets.get(bracket_id)
            if bracket and bracket.status.value == "PENDING_ENTRY":
                directive = bracket_manager.activate_bracket_on_fill(
                    bracket_id, fill.qty, fill.price, fill.timestamp
                )
                _apply_bracket_directive(bracket_id, directive)
                # A partial entry is cancelled after protecting the filled shares;
                # otherwise the remaining parent quantity has no matching bracket size.
                if order.status.value == "PARTIALLY_FILLED":
                    try:
                        engine.cancel_order(order.id, reason="PARTIAL_ENTRY_PROTECTED_SIZE")
                    except Exception:
                        pass
            if order.status.value in ("FILLED", "CANCELLED", "REJECTED"):
                if order.filled_qty == 0 and order.status.value in ("CANCELLED", "REJECTED"):
                    bracket_manager.cancel_pending_entry_bracket(order.symbol)
                entry_order_to_bracket.pop(order.id, None)
            continue

        mapping = bracket_manager.order_to_bracket.get(order.id)
        if not mapping:
            _record_exit_fill(order, fill)
            continue
        child_bracket_id, _child_type = mapping
        bracket_realized_pnl[child_bracket_id] = round(
            bracket_realized_pnl.get(child_bracket_id, 0.0) + fill.realized_pnl, 2
        )
        directive = bracket_manager.on_child_order_fill(
            order.id, fill.price, fill.qty, fill.timestamp
        )
        _apply_bracket_directive(child_bracket_id, directive)
        if directive.bracket_status.value.startswith("COMPLETED"):
            _record_completed_bracket(child_bracket_id)


def _release_dead_entry_brackets() -> None:
    """Clear PENDING_ENTRY brackets whose entry order was cancelled/rejected without filling.

    Covers cancel paths that emit no fill event (order purge, circuit breaker,
    audit sweep, API cancel), which _reconcile_fills never sees.
    """
    for order_id, bracket_id in list(entry_order_to_bracket.items()):
        order = engine.orders.get(order_id)
        if order is None or order.status.value not in ("CANCELLED", "REJECTED") or order.filled_qty > 0:
            continue
        bracket = bracket_manager.brackets.get(bracket_id)
        if bracket is not None:
            bracket_manager.cancel_pending_entry_bracket(bracket.symbol)
        entry_order_to_bracket.pop(order_id, None)


def _flatten_symbol(sym: str, price: float, timestamp: datetime) -> List[Any]:
    """Liquidate a full position, looping process_bar past the 10% volume participation cap."""
    fills: List[Any] = []
    for _ in range(50):
        if sym not in account.positions:
            break
        batch = engine.process_bar(sym, price, price, price, price, 100000, timestamp)
        if not batch:
            break
        fills.extend(batch)
    return fills


def _trip_circuit_breaker(timestamp: datetime) -> None:
    """Halt trading and liquidate all open intraday positions after a daily-loss breach. Swing positions are strictly exempt."""
    account.status = account.status.__class__.CIRCUIT_HALTED
    engine.cancel_all_orders("CIRCUIT_BREAKER_HALT", arm=TradingArm.INTRADAY)
    _release_dead_entry_brackets()
    for sym, pos in list(account.positions.items()):
        if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
            continue
        side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        bracket_id = bracket_manager.symbol_to_bracket.get(sym)
        liq_order = engine.create_order(
            symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
            strategy_id="CIRCUIT_BREAKER", parent_order_id=bracket_id,
            arm=TradingArm.INTRADAY,
        )
        engine.submit_order(liq_order.id)
        liq_fills = _flatten_symbol(sym, pos.market_price, timestamp)
        _reconcile_fills(liq_fills)


def _check_session_boundary(now_dt: datetime) -> None:
    """Reset daily risk, flattening, and account metrics when the ET session date changes."""
    global last_session_date
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    session_date = now_dt.astimezone(ET_TZ).date()
    if last_session_date == session_date:
        return
    is_first_observation = last_session_date is None
    previous_session_date = last_session_date
    if is_first_observation:
        last_session_date = session_date
        return
    if session_date < last_session_date:
        log.warning("Ignoring out-of-order historical event from %s (current session: %s)", session_date, last_session_date)
        return
    boundary_event_key: Optional[str] = None
    if not inflight_event_keys:
        should_process, boundary_event_key = _begin_durable_event(
            "SESSION_BOUNDARY", {"timestamp": now_dt}
        )
        if not should_process:
            return
    log.info("New ET session %s detected; resetting daily session state", session_date)
    intraday_working = [
        o for o in engine.working_orders.values()
        if getattr(o, "arm", None) != TradingArm.SWING and getattr(o, "strategy_id", "") != "swing_panic_dip"
    ]
    if intraday_working:
        log.warning("Session boundary detected with %d open intraday working orders; cancelling", len(intraday_working))
        for order in intraday_working:
            engine.cancel_order(order.id, reason="SESSION_BOUNDARY_PURGE")
        _release_dead_entry_brackets()
    # A position still on the book at a session boundary means the prior day's
    # 15:55 flatten did not complete. Liquidate unclosed INTRADAY positions.
    # Swing positions are strictly exempt and held overnight!
    intraday_positions = {
        sym: pos for sym, pos in account.positions.items()
        if getattr(pos, "arm", None) != TradingArm.SWING and getattr(pos, "strategy_id", "") != "swing_panic_dip"
    }
    if intraday_positions:
        log.error(
            "Session boundary with %d open intraday position(s); prior-day flatten failed. Liquidating: %s",
            len(intraday_positions),
            ", ".join(sorted(intraday_positions)),
        )
        for sym, pos in list(intraday_positions.items()):
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            bracket_id = bracket_manager.symbol_to_bracket.get(sym)
            liq_order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                strategy_id="SESSION_BOUNDARY_LIQUIDATION", parent_order_id=bracket_id,
                arm=TradingArm.INTRADAY,
            )
            engine.submit_order(liq_order.id)
            _reconcile_fills(_flatten_symbol(sym, pos.market_price, now_dt))
        remaining_intraday = {
            sym: pos for sym, pos in account.positions.items()
            if getattr(pos, "arm", None) != TradingArm.SWING and getattr(pos, "strategy_id", "") != "swing_panic_dip"
        }
        if remaining_intraday:
            log.error(
                "Session boundary liquidation incomplete; still open: %s. Book kept so the "
                "next flatten sweep retries.",
                ", ".join(sorted(remaining_intraday)),
            )
            _checkpoint_runtime(
                "SESSION_BOUNDARY_LIQUIDATION_INCOMPLETE",
                (boundary_event_key, "SESSION_BOUNDARY") if boundary_event_key else None,
            )
            return

    # Advance holding_days counter for active swing positions across session boundary
    # Strictly on trading days (Monday=0 through Friday=4). Non-trading weekend days (Saturday=5, Sunday=6) never increment.
    if session_date.weekday() < 5:
        for sym, pos in account.positions.items():
            if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
                pos.holding_days += 1
                log.info("Advanced swing position %s holding_days to %d", sym, pos.holding_days)


    # Forced exits now belong to the prior day before its immutable summary is
    # calculated. Only a verified-flat book may advance and clear linkage.
    if previous_session_date is not None:
        summary = _session_summary(previous_session_date)
        pending_session_summaries[summary["session_date"]] = summary
    decision_log.reset_for_session(session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date))
    last_session_date = session_date
    market_filter.reset_session(session_date)
    daily_bar_aggregator.reset_for_new_session()
    risk_engine.reset_daily_metrics(account.equity)
    flattening_engine.reset_for_new_session()
    account.reset_daily_metrics(account.equity)
    # Bracket/linkage state: clear INTRADAY brackets so no stale PENDING_ENTRY
    # bracket blocks an intraday symbol on the new day. Preserve SWING brackets!
    intraday_brackets = [
        bid for bid, b in list(bracket_manager.brackets.items())
        if getattr(b, "arm", None) != TradingArm.SWING and getattr(b, "strategy_id", "") != "swing_panic_dip"
    ]
    for bid in intraday_brackets:
        b = bracket_manager.brackets.pop(bid, None)
        if b:
            bracket_manager.symbol_to_bracket.pop(b.symbol, None)
            if b.stop_order_id:
                bracket_manager.order_to_bracket.pop(b.stop_order_id, None)
            if b.target_1_order_id:
                bracket_manager.order_to_bracket.pop(b.target_1_order_id, None)
            if b.target_2_order_id:
                bracket_manager.order_to_bracket.pop(b.target_2_order_id, None)
            bracket_realized_pnl.pop(bid, None)
            completed_brackets_recorded.discard(bid)

    for order_id in list(entry_order_to_bracket.keys()):
        order = engine.orders.get(order_id)
        if order is None or (
            getattr(order, "arm", None) != TradingArm.SWING
            and getattr(order, "strategy_id", "") != "swing_panic_dip"
        ):
            entry_order_to_bracket.pop(order_id, None)
    for strategy in strategies:
        strategy.reset_daily_stats()
    engine.prune_session_state()
    market_history.clear()
    recent_news.clear()
    today_open_prices.clear()
    latest_market_prices.clear()
    if state_store is not None:
        state_store.wal_checkpoint("PASSIVE")
    _checkpoint_runtime(
        "SESSION_BOUNDARY",
        (boundary_event_key, "SESSION_BOUNDARY") if boundary_event_key else None,
    )


def _expire_stale_staged_swing_orders(current_time: datetime) -> None:
    """Purge unexecuted staged swing orders past 09:45 ET so they never maroon or execute days later."""
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    et_dt = current_time.astimezone(ET_TZ)
    et_t = et_dt.time()
    if time(9, 45, 0) <= et_t < time(16, 0, 0):
        staged = swing_staged_order_manager.get_staged_orders()
        for order in staged:
            created = getattr(order, "created_at", None)
            if created:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (current_time - created).total_seconds() < 60:
                    continue
            log.warning(
                "Expiring unexecuted staged swing order %s (%s %s) past 09:45 ET open window",
                order.order_id,
                order.action,
                order.symbol,
            )
            swing_staged_order_manager.remove_staged_order(order.order_id)
            release_symbol_for_swing(order.symbol)


_last_broadcast_time: float = 0.0
_UI_BROADCAST_THROTTLE_SEC: float = 0.25  # 4 Hz maximum rate


def _sanitize_for_json(val: Any) -> Any:
    """Recursively replace non-finite numbers (NaN, Infinity, -Infinity) with 0.0."""
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return val
    elif isinstance(val, dict):
        return {k: _sanitize_for_json(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple)):
        return [_sanitize_for_json(v) for v in val]
    return val


async def broadcast_ui_state(force: bool = False) -> None:
    """Broadcast current system state to connected mobile trading UI clients with throttling and timeout protection."""
    global _last_broadcast_time
    if not ui_clients:
        return

    try:
        loop = asyncio.get_running_loop()
        now_mono = loop.time()
    except RuntimeError:
        now_mono = 0.0

    if not force and (now_mono - _last_broadcast_time) < _UI_BROADCAST_THROTTLE_SEC:
        return
    _last_broadcast_time = now_mono

    snapshot = account.get_snapshot()
    active_position_symbols = list(account.positions.keys())
    primary_pos = _serialize_position(active_position_symbols[0], include_chart=True) if active_position_symbols else None

    daily_pnl = round(snapshot.equity - account.daily_starting_equity, 2)
    daily_pnl_pct = round(
        (daily_pnl / account.daily_starting_equity) * 100.0, 2
    ) if account.daily_starting_equity else 0.0

    payload = {
        "type": "STATE_UPDATE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": {
            "equity": snapshot.equity,
            "cash": snapshot.cash,
            "buying_power": snapshot.buying_power,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "daily_drawdown": snapshot.daily_drawdown_dollars,
            "daily_drawdown_pct": snapshot.daily_drawdown_pct,
            "is_circuit_broken": snapshot.is_circuit_broken,
            "risk_level": risk_engine.risk_level.value,
            "status": snapshot.status,
            "daily_starting_equity": account.daily_starting_equity,
        },
        "market_context": adaptation_engine.get_market_context(),
        "strategies": _strategy_cards(),
        "primary_position": primary_pos,
        "all_positions": [_serialize_position(symbol, include_chart=False) for symbol in active_position_symbols],
        "positions_count": len(account.positions),
        "working_orders_count": len(engine.working_orders),
        "ledger_revision": ledger_revision,
        "persistence": {
            "status": "durable" if persistence_healthy and state_store else (
                "disabled" if state_store is None else "recovery_halt"
            ),
            "last_checkpoint_at": state_store.last_checkpoint_at if state_store else None,
            "restored_at": state_store.restored_at if state_store else None,
        },
        "ingestion": relay_statuses,
        "recent_news": recent_news[-10:],
        "recent_activity": [
            {
                "id": str(r.order_id),
                "timestamp": r.timestamp.strftime("%H:%M:%S") if hasattr(r.timestamp, "strftime") else str(r.timestamp),
                "type": r.event_trigger,
                "event_type": r.event_trigger,
                "symbol": r.symbol,
                "message": f"{r.from_state} -> {r.to_state} ({r.reason})",
                "price": r.fill_price,
                "qty": r.fill_qty,
                "quantity": r.fill_qty,
            }
            for r in engine.audit_log[-20:]
        ],
        "swing": swing_strategy_engine.to_ui_dict(),
    }

    raw = json.dumps(_sanitize_for_json(payload), default=str, allow_nan=False)
    for ws in list(ui_clients):
        try:
            await asyncio.wait_for(ws.send_text(raw), timeout=0.35)
        except Exception:
            ui_clients.discard(ws)


def _risk_reason_text(preview: Any) -> str:
    for attr in ("rejection_reason", "reason", "reasons", "violations"):
        val = getattr(preview, attr, None)
        if val:
            return str(val)
    return "risk check failed"


def _record_decision(signal: SignalEvent, outcome: str, detail: str) -> None:
    try:
        decision_log.record(
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            side=str(getattr(signal.side, "value", signal.side)),
            price=signal.entry_price,
            outcome=outcome,
            detail=detail,
            when=signal.timestamp if getattr(signal, "timestamp", None) else None,
        )
    except Exception:
        log.exception("Decision log write failed")


def _strategy_cards(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Strategy state plus live trading window, blockers and today's decision counts."""
    now = now or datetime.now(timezone.utc)
    try:
        trend = market_filter.get_current_trend(now)[0].value
    except Exception:
        trend = "UNKNOWN"
    try:
        _syms, _sectors, committed_count, _notional = _get_effective_committed_portfolio(account, arm=TradingArm.INTRADAY)
    except Exception:
        committed_count = len(account.positions)
    vix_stale = bool(last_vix_print is not None and (getattr(last_vix_print, "is_stale", False) or getattr(last_vix_print, "is_fallback", False)))
    cards = []
    for s in strategies:
        card = s.to_dict()
        card["window"] = strategy_window(
            s.strategy_id,
            now,
            adaptation_engine.is_strategy_permitted,
            operator_status=card.get("status", "ACTIVE"),
            market_trend=trend,
            breaker_halted=risk_engine.status != BreakerStatus.ARMED,
            entry_lockout=flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING,
            persistence_halted=state_store is not None and not persistence_healthy,
            positions_full=committed_count >= adaptation_engine.max_concurrent_positions,
            vix_stale=vix_stale,
        )
        card["decisions"] = decision_log.summary(s.strategy_id)
        cards.append(card)
    return cards


async def execute_strategy_signal(signal: SignalEvent, bar: Optional[BarEvent] = None) -> None:
    """Evaluate and route strategy signals through adaptation and risk engines."""
    sym = signal.symbol.upper()
    existing_pos = account.positions.get(sym)

    # 1. Contradiction or Exit Signal
    if "CONTRADICTION" in signal.reason or "EXIT" in signal.reason:
        if existing_pos:
            bracket_id = bracket_manager.symbol_to_bracket.get(sym)
            cancel_dir = bracket_manager.cancel_bracket_for_flattening(sym, reason=signal.reason)
            if cancel_dir and cancel_dir.orders_to_cancel:
                for oid in cancel_dir.orders_to_cancel:
                    if oid in engine.working_orders:
                        engine.cancel_order(oid, reason="NEWS_CONTRADICTION")
            side = OrderSide.SELL if existing_pos.side == PositionSide.LONG else OrderSide.BUY
            order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=existing_pos.shares,
                strategy_id="NEWS_CONTRADICTION", parent_order_id=bracket_id,
            )
            engine.submit_order(order.id)
            fill_p = bar.close if bar else existing_pos.market_price
            fill_dt = bar.timestamp if bar else datetime.now(timezone.utc)
            fills = _flatten_symbol(sym, fill_p, fill_dt)
            _reconcile_fills(fills)
        return

    # 2. Position-opening entry signal
    if signal.entry_price <= 0:
        _record_decision(signal, "BAD_PRICE", f"entry_price={signal.entry_price}")
        return

    # Reject duplicate entries: a working entry order or live bracket for this
    # symbol must not be overwritten (that would orphan the existing bracket).
    existing_bracket_id = bracket_manager.symbol_to_bracket.get(sym)
    if existing_bracket_id:
        existing_bracket = bracket_manager.brackets.get(existing_bracket_id)
        if existing_bracket and existing_bracket.status in (
            BracketStatus.PENDING_ENTRY,
            BracketStatus.ACTIVE,
            BracketStatus.TARGET_1_HIT,
        ):
            log.warning(
                "Rejecting duplicate entry signal for %s: bracket %s already %s",
                sym, existing_bracket_id, existing_bracket.status.value,
            )
            _record_decision(signal, "DUPLICATE", f"bracket {existing_bracket.status.value}")
            return
    for working in engine.working_orders.values():
        if working.symbol == sym and working.id in entry_order_to_bracket:
            log.warning("Rejecting duplicate entry signal for %s: entry order %s still working", sym, working.id)
            _record_decision(signal, "DUPLICATE", "entry order still working")
            return

    latest_market_prices[sym] = signal.entry_price if bar is None else bar.close
    adapted_stop = adaptation_engine.calculate_adapted_stop(signal)
    committed_symbols, committed_sectors, committed_count, notional_map = _get_effective_committed_portfolio(
        account, arm=TradingArm.INTRADAY
    )
    is_active = sym in committed_symbols
    approved, reason, qty = adaptation_engine.evaluate_signal_admission(
        signal=signal,
        equity=account.equity,
        current_positions_count=committed_count,
        is_symbol_active=is_active,
    )
    if not approved or qty <= 0:
        _record_decision(signal, classify_adaptation_reason(reason) if not approved else "SIZING", reason)
        if signal.strategy_id == "orb":
            orb_strategy.notify_signal_rejected(sym)
        return

    # The adaptation layer sizes from the strategy's raw stop for its public
    # sizing contract.  Execution submits the volatility-adjusted stop, so cap
    # that quantity against the exact risk geometry that will reach the order
    # engine before creating the order.
    risk_preview = risk_engine.evaluate_order_request(
        symbol=sym,
        side="BUY" if signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY" else "SELL",
        requested_qty=qty,
        entry_price=signal.entry_price,
        stop_price=adapted_stop,
        account_equity=account.equity,
        buying_power=account.buying_power,
        active_positions_count=committed_count,
        active_symbols=committed_symbols,
        active_sectors=committed_sectors,
        vix_multiplier=adaptation_engine.current_sizing_multiplier,
        is_entry_lockout_active=flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING,
        is_exit=False,
        existing_position_notional=notional_map.get(sym, 0.0),
        arm=TradingArm.INTRADAY,
        strategy_id=signal.strategy_id,
    )

    if not risk_preview.approved:
        _record_decision(signal, "RISK", _risk_reason_text(risk_preview))
        if signal.strategy_id == "orb":
            orb_strategy.notify_signal_rejected(sym)
        return
    qty = min(qty, risk_preview.authorized_qty)
    if qty <= 0:
        _record_decision(signal, "SIZING", "risk engine authorized 0 shares")
        if signal.strategy_id == "orb":
            orb_strategy.notify_signal_rejected(sym)
        return

    side = OrderSide.BUY if (signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY") else OrderSide.SELL
    otype = OrderType.LIMIT if (signal.order_type == OrderType.LIMIT or str(signal.order_type).upper() == "LIMIT") else OrderType.MARKET

    order = engine.create_order(
        symbol=sym,
        side=side,
        order_type=otype,
        qty=qty,
        limit_price=signal.entry_price if otype == OrderType.LIMIT else None,
        stop_price=adapted_stop,
        estimated_price=signal.entry_price,
        strategy_id=signal.strategy_id,
    )
    submitted = engine.submit_order(order.id)
    if submitted.status.value == "ACCEPTED":
        _record_decision(signal, "SUBMITTED", f"{side.value} {qty} {sym} order {submitted.id}")
        bracket = bracket_manager.create_bracket(
            bracket_id=f"brk_{submitted.id}",
            symbol=sym,
            side="LONG" if side == OrderSide.BUY else "SHORT",
            total_qty=qty,
            entry_price=signal.entry_price,
            stop_price=adapted_stop,
            strategy_id=signal.strategy_id,
            timestamp=signal.timestamp,
            target_1_override=signal.take_profit_1,
            target_2_override=signal.take_profit_2,
        )
        entry_order_to_bracket[submitted.id] = bracket.bracket_id
        if bar:
            fills = engine.process_bar(bar.symbol, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.timestamp)
            _reconcile_fills(fills)
    else:
        if signal.strategy_id == "orb":
            orb_strategy.notify_signal_rejected(sym)
        log.warning("Entry order %s rejected by execution engine: %s", submitted.id, submitted.reject_reason)
        _record_decision(signal, "ENGINE_REJECT", str(submitted.reject_reason))


# Event Bus Handlers
async def handle_bar_event(bar: BarEvent) -> None:
    """Ingest bar, update clocks, match orders, process strategies, check risk, update trailing stops."""
    should_process, event_key = _begin_durable_event("BAR", bar)
    if not should_process:
        return
    if simulation_mode:
        flattening_engine.clock.set_simulated_time(bar.timestamp)
    else:
        flattening_engine.clock.clear_simulated_time()
    _check_session_boundary(bar.timestamp)
    latest_market_prices[bar.symbol.upper()] = bar.close
    _mark_feed_event("bar")
    if bar.symbol.upper() in ("SPY", "QQQ"):
        market_filter.on_bar(bar)
    history = market_history.setdefault(bar.symbol.upper(), [])
    history.append({
        "time": bar.timestamp.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    })
    del history[:-120]
    adaptation_engine.update_clock(bar.timestamp)
    for strat in strategies:
        try:
            strat.on_time_tick(bar.timestamp)
        except Exception as e:
            log.error(f"Strategy {strat.strategy_id} error on time tick: {e}")

    bar_sym = bar.symbol.upper()
    bar_et = bar.timestamp.astimezone(ET_TZ) if bar.timestamp.tzinfo else bar.timestamp

    bar_t = bar_et.time()

    # 09:30 ET Market Open Execution Window for Staged Swing Orders (09:30:00 - 09:45:00 ET tolerance)
    # Allows delayed, illiquid, or 09:31+ bars to execute reliably without marooning staged orders
    if bar_t.hour == 9 and 30 <= bar_t.minute <= 45:
        if bar_sym not in today_open_prices and bar.open > 0:
            today_open_prices[bar_sym] = bar.open
        if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
            open_price_map = {bar_sym: bar.open}
            for stg_ent in swing_staged_order_manager.get_staged_entries():
                if stg_ent.symbol in today_open_prices and stg_ent.symbol not in open_price_map:
                    open_price_map[stg_ent.symbol] = today_open_prices[stg_ent.symbol]
            for stg_ext in swing_staged_order_manager.get_staged_exits():
                if stg_ext.symbol in today_open_prices and stg_ext.symbol not in open_price_map:
                    open_price_map[stg_ext.symbol] = today_open_prices[stg_ext.symbol]
            swing_strategy_engine.execute_market_open(open_price_map, bar.timestamp)
    elif (bar_t.hour == 9 and bar_t.minute > 45) or (10 <= bar_t.hour < 16):
        _expire_stale_staged_swing_orders(bar.timestamp)

    # Swing Data Aggregation & Real-Time Emergency Stop Check
    # Evaluated after execute_market_open so new positions are monitored on their opening candle
    swing_set = set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK}
    if bar_sym in swing_set:
        daily_bar_aggregator.on_minute_bar(bar)
    swing_strategy_engine.on_bar(bar)


    directive = flattening_engine.check_time_tick()
    if directive:
        await handle_flattening_directive(directive)

    # Evaluate intraday strategies on new bar (restricted strictly to WATCHLIST_SYMBOLS)
    collected_signals: List[SignalEvent] = []
    if bar.symbol.upper() in settings.WATCHLIST_SYMBOLS:
        for strat in strategies:
            try:
                sigs = strat.on_bar(bar)
                if sigs:
                    collected_signals.extend(sigs)
            except Exception as e:
                log.error(f"Strategy {strat.strategy_id} error on bar: {e}")

    if collected_signals:
        arbitrated = adaptation_engine.arbitrate_signals(collected_signals)
        kept = {id(sig) for sig in arbitrated}
        for sig in collected_signals:
            if id(sig) not in kept and sig.entry_price > 0:
                _record_decision(sig, "ARBITRATION_LOST", "A higher-priority strategy signalled the same stock on this bar")
        for sig in arbitrated:
            await execute_strategy_signal(sig)

    fills = engine.process_bar(
        symbol=bar.symbol,
        open_=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        timestamp=bar.timestamp,
    )
    _reconcile_fills(fills)

    # Check risk state
    status = risk_engine.evaluate_account_state(
        equity=account.equity,
        cash=account.cash,
        realized_pnl=account.realized_pnl,
        unrealized_pnl=account.unrealized_pnl,
        timestamp=bar.timestamp,
    )
    if status == BreakerStatus.HALTED_DAILY_LOSS:
        _trip_circuit_breaker(bar.timestamp)

    # Trailing stop update
    atr_est = _atr_estimate(bar.symbol, bar)
    bracket_dir = bracket_manager.update_trailing_stop(
        symbol=bar.symbol,
        current_bar_high=bar.high,
        current_bar_low=bar.low,
        current_atr=atr_est,
        timestamp=bar.timestamp,
    )
    if bracket_dir and bracket_dir.orders_to_modify:
        for mod in bracket_dir.orders_to_modify:
            oid = mod["order_id"]
            if oid in engine.working_orders:
                engine.working_orders[oid].stop_price = mod["new_stop_price"]

    _checkpoint_runtime("BAR_EVENT", (event_key, "BAR") if event_key else None)
    await broadcast_ui_state()


def _quote_requires_write_ahead(quote: QuoteEvent) -> bool:
    """Return true only when this quote can fill or trip a liquidation."""
    symbol = quote.symbol.upper()
    for order in engine.working_orders.values():
        if order.symbol != symbol:
            continue
        if order.order_type == OrderType.MARKET:
            return True
        if order.order_type == OrderType.LIMIT and (
            (order.side == OrderSide.BUY and quote.ask_price <= (order.limit_price or 0.0))
            or (order.side == OrderSide.SELL and quote.bid_price >= (order.limit_price or 0.0))
        ):
            return True
        if order.order_type == OrderType.STOP and (
            (order.side == OrderSide.BUY and quote.ask_price >= (order.stop_price or 0.0))
            or (order.side == OrderSide.SELL and quote.bid_price <= (order.stop_price or 0.0))
        ):
            return True
    position = account.positions.get(symbol)
    if position is None:
        return False
    if risk_engine.status == BreakerStatus.HALTED_DAILY_LOSS:
        return True
    mid = (quote.bid_price + quote.ask_price) / 2.0
    if position.side == PositionSide.LONG:
        projected_unrealized = position.shares * (mid - position.avg_entry_price)
    else:
        projected_unrealized = position.shares * (position.avg_entry_price - mid)
    projected_equity = account.equity + projected_unrealized - position.unrealized_pnl
    projected_drawdown = max(0.0, risk_engine.config.starting_equity - projected_equity)
    return projected_drawdown >= risk_engine.config.hard_max_daily_loss_dollars


async def handle_quote_event(quote: QuoteEvent) -> None:
    """Ingest quote and match working orders."""
    can_fill = _quote_requires_write_ahead(quote)
    event_key: Optional[str] = None
    if can_fill:
        should_process, event_key = _begin_durable_event("QUOTE", quote)
        if not should_process:
            return
    latest_market_prices[quote.symbol.upper()] = (quote.bid_price + quote.ask_price) / 2.0
    _mark_feed_event("quote")
    for strat in strategies:
        try:
            strat.on_quote(quote)
        except Exception as e:
            log.error(f"Strategy {strat.strategy_id} error on quote: {e}")

    prior_risk_status = risk_engine.status
    fills = engine.process_quote(
        symbol=quote.symbol,
        bid=quote.bid_price,
        ask=quote.ask_price,
        timestamp=quote.timestamp,
    )
    _reconcile_fills(fills)

    # Circuit breaker is evaluated on quotes too, not only on bars
    status = risk_engine.evaluate_account_state(
        equity=account.equity,
        cash=account.cash,
        realized_pnl=account.realized_pnl,
        unrealized_pnl=account.unrealized_pnl,
        timestamp=quote.timestamp,
    )
    if status == BreakerStatus.HALTED_DAILY_LOSS:
        _trip_circuit_breaker(quote.timestamp)

    if event_key:
        _checkpoint_runtime("QUOTE_MUTATION", (event_key, "QUOTE"))
    elif fills or risk_engine.status != prior_risk_status:
        _checkpoint_runtime("QUOTE_MUTATION")
    await broadcast_ui_state(force=bool(fills))


async def handle_news_event(news: NewsEvent) -> None:
    """Record news, trigger catalyst events and contradiction checks."""
    should_process, event_key = _begin_durable_event("NEWS", news)
    if not should_process:
        return
    _mark_feed_event("news")
    recent_news.append({
        "headline": news.headline,
        "symbols": news.symbols,
        "score": news.sentiment_score,
        "confidence": news.sentiment_confidence,
        "category": news.catalyst_category.value,
        "created_at": news.created_at.isoformat(),
    })
    if len(recent_news) > 50:
        recent_news.pop(0)

    # Update monitored positions for news contradiction circuit breaker
    for sym, pos in account.positions.items():
        news_strategy.update_monitored_position(sym, pos.side.value)
    for sym in list(news_strategy.monitored_positions.keys()):
        if sym not in account.positions:
            news_strategy.update_monitored_position(sym, None)

    exit_signals = news_strategy.on_news(news)
    for sig in exit_signals:
        await execute_strategy_signal(sig)
    _checkpoint_runtime("NEWS_EVENT", (event_key, "NEWS") if event_key else None)
    await broadcast_ui_state()


async def handle_vix_print(vprint: VixPrint) -> None:
    """Update VIX print and scale risk and adaptation parameters."""
    global last_vix_print
    previous_vix_state = (
        adaptation_engine.current_vix,
        adaptation_engine.current_vix_regime,
        adaptation_engine.current_sizing_multiplier,
        adaptation_engine.current_stop_multiplier,
    )
    last_vix_print = vprint
    _mark_feed_event("vix")
    if vprint.is_stale or vprint.is_fallback:
        log.warning(
            "Skipping regime update for stale/fallback VIX print %.2f (state=%s, upstream=%s)",
            vprint.value, vprint.state, vprint.upstream,
        )
        # Skipping the update alone is fail-open: the last accepted regime stays in
        # force, so a LOW print taken before the feed froze keeps sizing 20% above base.
        if adaptation_engine.apply_stale_vix_guard():
            log.warning(
                "Stale VIX: sizing multiplier clamped to neutral 1.00 (regime NORMAL) "
                "until a fresh print arrives"
            )
    else:
        adaptation_engine.on_vix_print(vprint)
    for strat in strategies:
        try:
            strat.on_vix(vprint)
        except Exception as e:
            log.error(f"Strategy {strat.strategy_id} error on vix: {e}")
    current_vix_state = (
        adaptation_engine.current_vix,
        adaptation_engine.current_vix_regime,
        adaptation_engine.current_sizing_multiplier,
        adaptation_engine.current_stop_multiplier,
    )
    if current_vix_state != previous_vix_state:
        _checkpoint_runtime("VIX_STATE")
    await broadcast_ui_state()


async def handle_flattening_directive(directive: FlatteningDirective) -> None:
    """Apply auto-flattening directives."""
    event_key: Optional[str] = None
    if not inflight_event_keys:
        should_process, event_key = _begin_durable_event("FLATTENING", directive)
        if not should_process:
            return
    if directive.phase == FlatteningPhase.ORDER_PURGE:
        # Phase 2 (15:50 ET): Purge unfilled entry orders; preserve protective stops for open positions
        # Preserve swing orders (both protective stops and staged/entry orders)
        for order_id, order in list(engine.working_orders.items()):
            if getattr(order, "arm", None) == TradingArm.SWING or getattr(order, "strategy_id", "") == "swing_panic_dip":
                continue
            pos = account.positions.get(order.symbol.upper())
            is_protective = bool(
                pos and (
                    (pos.side == PositionSide.LONG and order.side == OrderSide.SELL) or
                    (pos.side == PositionSide.SHORT and order.side == OrderSide.BUY)
                )
            )
            if not is_protective:
                engine.cancel_order(order_id, reason="EOD_PURGE_UNFILLED_ENTRIES")
        _release_dead_entry_brackets()
    elif directive.cancel_all_orders:
        engine.cancel_all_orders("FLATTENING_DIRECTIVE", arm=TradingArm.INTRADAY)
        _release_dead_entry_brackets()

    if directive.liquidate_all_positions:
        now_dt = directive.timestamp
        for sym, pos in list(account.positions.items()):
            if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
                continue
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            bracket_id = bracket_manager.symbol_to_bracket.get(sym)
            liq_order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                strategy_id="AUTO_FLATTEN", parent_order_id=bracket_id,
                arm=TradingArm.INTRADAY,
            )
            engine.submit_order(liq_order.id)
            fills = _flatten_symbol(sym, pos.market_price, now_dt)
            _reconcile_fills(fills)

    if directive.run_audit:
        audit_res = flattening_engine.execute_phase_4_audit(
            open_positions=account.positions,
            working_orders=list(engine.working_orders.values()),
        )
        if audit_res.cancel_all_orders and engine.working_orders:
            engine.cancel_all_orders("AUDIT_EMERGENCY_SWEEP", arm=TradingArm.INTRADAY)
            _release_dead_entry_brackets()
        if audit_res.liquidate_all_positions and account.positions:
            now_dt = audit_res.timestamp
            for sym, pos in list(account.positions.items()):
                if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
                    continue
                side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
                bracket_id = bracket_manager.symbol_to_bracket.get(sym)
                sweep_order = engine.create_order(
                    symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                    strategy_id="EMERGENCY_SWEEP", parent_order_id=bracket_id,
                    arm=TradingArm.INTRADAY,
                )
                engine.submit_order(sweep_order.id)
                fills = _flatten_symbol(sym, pos.market_price, now_dt)
                _reconcile_fills(fills)
            # Re-verify the book after the emergency sweep so the audit can pass
            audit_res = flattening_engine.execute_phase_4_audit(
                open_positions=account.positions,
                working_orders=list(engine.working_orders.values()),
            )

        if audit_res.audit_passed:
            has_swing_open = any(
                getattr(p, "arm", None) == TradingArm.SWING or getattr(p, "strategy_id", "") == "swing_panic_dip"
                for p in account.positions.values()
            )
            if not has_swing_open:
                account.status = account.status.__class__.EOD_FLAT

    if directive.phase == FlatteningPhase.MARKET_CLOSED:
        eval_date = directive.timestamp.astimezone(ET_TZ).date() if directive.timestamp.tzinfo else directive.timestamp.date()
        if _relay_backfill_enabled():
            # Repair the day's minutes from REST before finalizing; runs off the trading loop.
            task = asyncio.create_task(_swing_close_with_backfill(eval_date), name="SwingCloseBackfill")
            runtime_tasks.add(task)
            task.add_done_callback(runtime_tasks.discard)
        else:
            daily_bar_aggregator.finalize_all(eval_date)
            swing_strategy_engine.evaluate_market_close(eval_date)

    _checkpoint_runtime(
        f"FLATTENING_{directive.phase.value}",
        (event_key, "FLATTENING") if event_key else None,
    )


# ---------------------------------------------------------------------------
# Relay REST backfill: repairs restart gaps in swing daily bars and the
# SPY/QQQ market-direction filter. Never routes history through the trading
# handler (no fills, no signals).
# ---------------------------------------------------------------------------
SWING_MIN_COVERAGE = 385  # of 390 regular-session minutes


def _relay_backfill_enabled() -> bool:
    return bool(settings.START_RELAY_CLIENTS and settings.RELAY_TOKEN and not simulation_mode)


async def _fetch_session_minutes(symbols: List[str], session_date: date, deadline_sec: float = 20.0) -> List[BarEvent]:
    """Regular-session 1-min SIP bars for session_date up to now, following pagination."""
    import httpx

    start = datetime.combine(session_date, time(9, 30), ET_TZ).astimezone(timezone.utc)
    end = min(datetime.combine(session_date, time(16, 0), ET_TZ), datetime.now(ET_TZ)).astimezone(timezone.utc)
    if end <= start:
        return []
    base = settings.RELAY_HTTP_URL.rstrip("/")
    out: List[BarEvent] = []
    page: Optional[str] = None
    loop = asyncio.get_running_loop()
    t_end = loop.time() + deadline_sec
    async with httpx.AsyncClient(headers={"X-Relay-Token": settings.RELAY_TOKEN}, timeout=8.0) as client:
        while True:
            if loop.time() > t_end:
                raise TimeoutError("relay backfill deadline exceeded")
            params = {
                "symbols": ",".join(sorted(set(symbols))),
                "timeframe": "1Min",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "feed": "sip",
                "limit": "10000",
            }
            if page:
                params["page_token"] = page
            resp = await client.get(f"{base}/data/v2/stocks/bars", params=params)
            resp.raise_for_status()
            data = resp.json()
            for sym, rows in (data.get("bars") or {}).items():
                for r in rows:
                    out.append(BarEvent(
                        sym.upper(), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), int(r["v"]),
                        datetime.fromisoformat(str(r["t"]).replace("Z", "+00:00")), r.get("n"), r.get("vw"),
                    ))
            page = data.get("next_page_token")
            if not page:
                break
    return out


def _rebuild_market_filter(rest_bars: List[BarEvent]) -> None:
    """Build a fresh SPY/QQQ filter from REST history + live bars already seen, then swap it in."""
    global market_filter
    fresh = MarketTrendFilter()
    merged: Dict[Tuple[str, datetime], BarEvent] = {}
    for b in rest_bars:
        if b.symbol in ("SPY", "QQQ"):
            merged[(b.symbol, b.timestamp)] = b
    for sym in ("SPY", "QQQ"):
        for row in market_history.get(sym, []):
            ts = datetime.fromisoformat(row["time"])
            merged[(sym, ts)] = BarEvent(sym, row["open"], row["high"], row["low"], row["close"], int(row["volume"]), ts)
    for key in sorted(merged, key=lambda k: (k[1], k[0])):
        fresh.on_bar(merged[key])
    market_filter = fresh
    adaptation_engine.market_filter = fresh


async def _startup_backfill() -> None:
    """After a restart during the session, repair today's swing minutes and the market filter."""
    now_et = datetime.now(ET_TZ)
    if not is_trading_day(now_et.date()) or now_et.time() < time(9, 31):
        return
    session_date = now_et.date()
    symbols = sorted(set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK, "SPY", "QQQ"})
    try:
        bars = await _fetch_session_minutes(symbols, session_date)
    except Exception as exc:
        log.warning("Startup backfill failed, continuing with live data only: %s", exc)
        return
    swing_set = set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK}
    added = daily_bar_aggregator.merge_backfill([b for b in bars if b.symbol in swing_set])
    if now_et.time() < time(16, 0):
        _rebuild_market_filter(bars)
    log.info(
        "Startup backfill: %d REST minutes, %d swing minutes added; coverage %s; market trend now %s",
        len(bars), added,
        {s: daily_bar_aggregator.coverage(s, session_date) for s in sorted(swing_set)},
        market_filter.get_current_trend(datetime.now(timezone.utc))[0].value,
    )
    await broadcast_ui_state(force=True)


async def _swing_close_with_backfill(eval_date: date, max_wait_sec: float = 120.0, retry_sec: float = 15.0) -> None:
    swing_set = sorted(set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK})
    loop = asyncio.get_running_loop()
    give_up = loop.time() + max_wait_sec
    note: Optional[str] = None
    while True:
        try:
            bars = await _fetch_session_minutes(swing_set, eval_date)
            daily_bar_aggregator.merge_backfill(bars)
        except Exception as exc:
            note = f"REST backfill failed: {exc}"
            log.warning("Swing close backfill attempt failed: %s", exc)
        short = {s: daily_bar_aggregator.coverage(s, eval_date) for s in swing_set}
        short = {s: n for s, n in short.items() if n < SWING_MIN_COVERAGE}
        if not short or loop.time() >= give_up:
            break
        await asyncio.sleep(retry_sec)
    if short:
        note = f"Incomplete minute data at close {short} (need {SWING_MIN_COVERAGE}/390)"
    daily_bar_aggregator.finalize_all(eval_date)
    swing_strategy_engine.evaluate_market_close(eval_date, allow_new_entries=not short, data_note=note)
    log.info("Swing close processed for %s; data note: %s", eval_date, note)
    _checkpoint_runtime("SWING_CLOSE_EVALUATION")
    await broadcast_ui_state(force=True)



async def _runtime_clock_loop() -> None:
    """Keep EOD controls alive even when a market-data bar is delayed or absent."""
    last_clock_broadcast = 0.0
    while True:
        try:
            if pending_processed_events:
                _checkpoint_runtime("PERSISTENCE_RETRY")
            if simulation_mode:
                # Replay bars advance the simulated clock synchronously.  Do
                # not let the wall-clock task observe the host's unrelated
                # date/time and move the replay into MARKET_CLOSED.
                await asyncio.sleep(1.0)
                continue
            flattening_engine.clock.clear_simulated_time()
            now_dt = flattening_engine.clock.now()
            _check_session_boundary(now_dt)
            adaptation_engine.update_clock(now_dt)
            _expire_stale_staged_swing_orders(now_dt)
            for strat in strategies:
                try:
                    strat.on_time_tick(now_dt)
                except Exception as e:
                    log.error(f"Strategy {strat.strategy_id} error on time tick: {e}")
            directive = flattening_engine.check_time_tick()
            if directive:
                await handle_flattening_directive(directive)
            # Cards must flip at window boundaries even when no bar arrives.
            if ui_clients and _time_mod.monotonic() - last_clock_broadcast >= 10.0:
                last_clock_broadcast = _time_mod.monotonic()
                await broadcast_ui_state(force=True)
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("Runtime clock loop failed")
            await asyncio.sleep(1.0)


async def handle_trade_event(trade: TradeEvent) -> None:
    """Track the latest trade print price for pre-trade risk valuation."""
    latest_market_prices[trade.symbol.upper()] = trade.price
    _mark_feed_event("trade")


async def _handle_relay_status(status: RelayStatusEvent) -> None:
    relay_statuses[status.feed_type] = status.status


def reset_runtime_state(starting_equity: Optional[float] = None) -> None:
    """Reset in-memory session state for deterministic replay and test isolation."""
    global last_vix_print, simulation_mode, last_session_date
    simulation_mode = False
    last_session_date = None
    flattening_engine.clock.clear_simulated_time()
    account.positions.clear()
    account.cash = settings.INITIAL_CASH if starting_equity is None else starting_equity
    account.equity = account.cash
    account.realized_pnl = 0.0
    account.unrealized_pnl = 0.0
    account.fees_paid = 0.0
    account.daily_starting_equity = account.cash
    account.status = account.status.__class__.ACTIVE
    account._recompute_account_state()
    engine.orders.clear()
    engine.working_orders.clear()
    engine.audit_log.clear()
    bracket_manager.brackets.clear()
    bracket_manager.symbol_to_bracket.clear()
    bracket_manager.order_to_bracket.clear()
    entry_order_to_bracket.clear()
    bracket_realized_pnl.clear()
    completed_brackets_recorded.clear()
    pending_trade_records.clear()
    pending_session_summaries.clear()
    pending_processed_events.clear()
    inflight_event_keys.clear()
    latest_market_prices.clear()
    today_open_prices.clear()
    for _feed in feed_last_event:
        feed_last_event[_feed] = None
    market_history.clear()
    recent_news.clear()
    last_vix_print = None
    risk_engine.reset_daily_metrics(account.equity)
    flattening_engine.reset_for_new_session()
    market_filter.reset_session()
    for strategy in strategies:
        strategy.reset_daily_stats()
    swing_strategy_engine.reset()
    daily_bar_aggregator.reset_for_new_session()
    swing_reserved_symbols.clear()
    decision_log.reset_for_session(None)


def set_simulation_mode(enabled: bool) -> None:
    """Select deterministic replay time or the live wall clock."""
    global simulation_mode
    simulation_mode = enabled
    if not enabled:
        flattening_engine.clock.clear_simulated_time()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and graceful teardown lifecycle."""
    log.info(f"Starting AutonomousDayTrader backend on port {settings.API_PORT}...")
    if settings.ENV.lower() == "production" and (
        not settings.PERSISTENCE_ENABLED or not settings.PERSISTENCE_REQUIRED
    ):
        raise PersistenceError(
            "Production requires PERSISTENCE_ENABLED=true and PERSISTENCE_REQUIRED=true"
        )
    _restore_checkpoint()
    # Register bus event handlers
    event_bus.subscribe(BarEvent, handle_bar_event)
    event_bus.subscribe(QuoteEvent, handle_quote_event)
    event_bus.subscribe(TradeEvent, handle_trade_event)
    event_bus.subscribe(NewsEvent, handle_news_event)
    event_bus.subscribe(VixPrint, handle_vix_print)
    event_bus.subscribe(RelayStatusEvent, _handle_relay_status)

    if state_store is not None:
        for event_key, event_type, event in state_store.list_pending_events():
            log.warning("Replaying pending durable %s event %s", event_type, event_key)
            if event_type == "BAR" and isinstance(event, BarEvent):
                await handle_bar_event(event)
            elif event_type == "QUOTE" and isinstance(event, QuoteEvent):
                await handle_quote_event(event)
            elif event_type == "NEWS" and isinstance(event, NewsEvent):
                await handle_news_event(event)
            elif event_type == "FLATTENING" and isinstance(event, FlatteningDirective):
                await handle_flattening_directive(event)
            elif event_type == "SESSION_BOUNDARY" and isinstance(event, dict):
                _check_session_boundary(event["timestamp"])
            elif event_type == "MANUAL_FLATTEN" and isinstance(event, dict):
                should_process, replay_key = _begin_durable_event("MANUAL_FLATTEN", event)
                if should_process:
                    await _execute_manual_flatten(
                        [str(symbol).upper() for symbol in event["target_symbols"]],
                        event["timestamp"],
                        replay_key,
                    )
            else:
                raise PersistenceError(
                    f"Unsupported pending event {event_key} ({event_type}/{type(event).__name__})"
                )
            if event_key in pending_processed_events:
                if not _checkpoint_runtime("PENDING_EVENT_REPLAY_RETRY"):
                    raise PersistenceError(f"Could not commit replayed event {event_key}")

    global stock_ws_client, news_ws_client, vix_client
    configured = bool(settings.RELAY_TOKEN)
    if settings.START_RELAY_CLIENTS and configured:
        stock_ws_client = StockWebSocketClient()
        news_ws_client = NewsWebSocketClient()
        vix_client = VixClient()
        relay_statuses.update({"stock": "connecting", "news": "connecting", "vix": "connecting"})
        await stock_ws_client.start()
        await news_ws_client.start()
        await vix_client.start()
        if _relay_backfill_enabled():
            bf = asyncio.create_task(_startup_backfill(), name="StartupBackfill")
            runtime_tasks.add(bf)
            bf.add_done_callback(runtime_tasks.discard)

    # The runtime clock drives EOD flattening and session resets even without relay clients
    clock_task = asyncio.create_task(_runtime_clock_loop(), name="TradingRuntimeClock")
    runtime_tasks.add(clock_task)

    yield

    log.info("Shutting down AutonomousDayTrader backend...")
    if stock_ws_client:
        await stock_ws_client.stop()
    if news_ws_client:
        await news_ws_client.stop()
    if vix_client:
        await vix_client.stop()
    for task in list(runtime_tasks):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        runtime_tasks.discard(task)
    for ws in list(ui_clients):
        try:
            await ws.close(code=1001, reason="Server shutdown")
        except Exception:
            pass
        ui_clients.discard(ws)
    # Producers are fully stopped before the final durable checkpoint. Nothing
    # can fill or mutate the account after this point.
    checkpoint_saved = False
    for attempt in range(5):
        if _checkpoint_runtime("GRACEFUL_SHUTDOWN"):
            checkpoint_saved = True
            break
        await asyncio.sleep(0.5 * (attempt + 1))
    if state_store is not None and not checkpoint_saved:
        log.critical("Final durable checkpoint failed after all shutdown retries")
    stock_ws_client = None
    news_ws_client = None
    vix_client = None
    if state_store is not None:
        state_store.close()
    event_bus.clear()
    log.info("Shutdown complete.")


app = FastAPI(
    title="AutonomousDayTrader Core Engine",
    description="Local Institutional Intraday Day Trading System connected to AlpacaRelay",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# REST Endpoints
@app.get("/health")
async def get_health() -> Dict[str, Any]:
    """System health telemetry and component statuses."""
    configured = bool(settings.RELAY_TOKEN)
    bound_api_port = int(os.getenv("PORT", str(settings.API_PORT)))

    now_utc = datetime.now(timezone.utc)

    def _feed_age(feed: str) -> Optional[float]:
        ts = feed_last_event.get(feed)
        return None if ts is None else round((now_utc - ts).total_seconds(), 1)

    operational_status = "healthy" if configured and all(
        relay_statuses.get(feed) == "connected" for feed in ("stock", "news", "vix")
    ) else "degraded" if configured else "unconfigured"
    if settings.PERSISTENCE_REQUIRED and not persistence_healthy:
        operational_status = "recovery_halt"
    return {
        "status": operational_status,
        "mode": settings.ENV,
        "upstream_configured": configured,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": {
            "equity": account.equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "status": account.status.value,
            "open_positions": len(account.positions),
        },
        "risk": {
            "status": risk_engine.status.value,
            "level": risk_engine.risk_level.value,
            "drawdown_dollars": risk_engine.current_drawdown_dollars,
            "drawdown_pct": risk_engine.current_drawdown_pct,
        },
        "flattening": {
            "phase": flattening_engine.current_phase.value,
            "audit_passed": flattening_engine.audit_passed,
        },
        "ports": {
            "api": bound_api_port,
            "ui": settings.UI_PORT,
            "mock": settings.MOCK_PORT,
        },
        "relay": relay_statuses,
        "persistence": {
            "status": "durable" if persistence_healthy and state_store else (
                "disabled" if state_store is None else "recovery_halt"
            ),
            "required": settings.PERSISTENCE_REQUIRED,
            "schema_version": SCHEMA_VERSION if state_store else None,
            "checkpoint_revision": persistence_revision,
            "ledger_revision": ledger_revision,
            "last_checkpoint_at": state_store.last_checkpoint_at if state_store else None,
            "restored_at": state_store.restored_at if state_store else None,
            "error": persistence_error,
        },
        # Read live off the wired engine, not from config constants, so the deployed
        # build's actual limits are verifiable from outside without placing an order.
        "limits": {
            "max_daily_loss_dollars": risk_engine.config.hard_max_daily_loss_dollars,
            "max_position_notional": round(
                account.equity * risk_engine.config.max_position_equity_pct, 2
            ),
            "max_position_equity_pct": risk_engine.config.max_position_equity_pct,
            "max_concurrent_positions": risk_engine.config.max_concurrent_positions,
            "base_trade_risk_pct": risk_engine.config.base_trade_risk_pct,
            "stop_distance_pct": [
                risk_engine.config.min_stop_distance_pct,
                risk_engine.config.max_stop_distance_pct,
            ],
        },
        "feeds": {
            "bars": {
                "received": stock_ws_client.bars_received if stock_ws_client else 0,
                "last_age_sec": _feed_age("bar"),
            },
            "quotes": {
                "received": stock_ws_client.quotes_received if stock_ws_client else 0,
                "last_age_sec": _feed_age("quote"),
            },
            "trades": {
                "received": stock_ws_client.trades_received if stock_ws_client else 0,
                "last_age_sec": _feed_age("trade"),
            },
            "news": {
                "received": news_ws_client.articles_received if news_ws_client else 0,
                "last_age_sec": _feed_age("news"),
            },
            # last_poll_age_sec only proves the poller is breathing: handle_vix_print
            # marks an event for stale and fallback prints too, so it read ~4s during a
            # live outage on 2026-09-21 while the VIX value itself was 380s old. The age
            # of the VALUE is measured from the print's own asof, and `stale` is the flag
            # the sizing guard actually acts on.
            "vix": {
                "last_poll_age_sec": _feed_age("vix"),
                "value_age_sec": (
                    round((now_utc - last_vix_print.asof).total_seconds(), 1)
                    if last_vix_print else None
                ),
                "stale": (
                    bool(last_vix_print.is_stale or last_vix_print.is_fallback)
                    if last_vix_print else None
                ),
            },
        },
    }


@app.get("/api/account")
async def get_account_state() -> Dict[str, Any]:
    """Current paper trading account state."""
    snap = account.get_snapshot()
    return {
        "cash": snap.cash,
        "equity": snap.equity,
        "buying_power": snap.buying_power,
        "maintenance_margin": snap.maintenance_margin,
        "margin_excess": snap.margin_excess,
        "realized_pnl": snap.realized_pnl,
        "unrealized_pnl": snap.unrealized_pnl,
        "fees_paid": snap.fees_paid,
        "daily_drawdown_dollars": snap.daily_drawdown_dollars,
        "daily_drawdown_pct": snap.daily_drawdown_pct,
        "is_circuit_broken": snap.is_circuit_broken,
        "status": snap.status,
    }


@app.get("/api/positions")
async def get_positions() -> Dict[str, Any]:
    """Active open positions."""
    snap = account.get_snapshot()
    return {k: v.__dict__ for k, v in snap.positions.items()}


@app.get("/api/strategies")
async def get_strategies() -> List[Dict[str, Any]]:
    """Strategies with live trading window, blockers and today's decision counts."""
    return _strategy_cards()


@app.get("/api/decisions")
async def get_decisions(limit: int = 50, strategy: Optional[str] = None) -> Dict[str, Any]:
    """Every signal the strategies raised today and what the bot decided (newest first)."""
    limit = max(1, min(int(limit), 300))
    return {
        "session_date": decision_log.session_date,
        "summary": {s.strategy_id: decision_log.summary(s.strategy_id) for s in strategies},
        "items": decision_log.recent(limit, strategy),
    }


@app.get("/api/market-context")
async def get_market_context() -> Dict[str, Any]:
    """Current VIX volatility regime and Time-of-Day execution phase."""
    return adaptation_engine.get_market_context()


@app.get("/api/audit")
async def get_audit_log(limit: int = 50) -> List[Dict[str, Any]]:
    """Recent execution and order audit trail."""
    return [r.__dict__ for r in reversed(engine.audit_log[-limit:])]


@app.get("/api/trades")
async def get_trade_history(
    range: str = "7d",
    limit: int = 25,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Paginated durable completed-trade history and aggregate-only recoveries."""
    if state_store is None:
        raise HTTPException(status_code=503, detail="Durable trade history is not enabled")
    if range not in {"today", "7d", "all"}:
        raise HTTPException(status_code=400, detail="range must be today, 7d, or all")
    limit = max(1, min(limit, 100))
    today_et = datetime.now(ET_TZ).date()
    start_date = None
    if range == "today":
        start_date = today_et.isoformat()
    elif range == "7d":
        start_date = (today_et - timedelta(days=6)).isoformat()

    try:
        items, next_cursor = state_store.list_trades(start_date, limit=limit, cursor=cursor)
    except PersistenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trade_stats = state_store.aggregate_trade_stats(start_date)
    all_summaries = state_store.list_session_summaries(start_date)
    recovered_sessions = [
        summary
        for summary in all_summaries
        if summary.get("aggregate_only")
    ]
    trades_count = int(trade_stats["trades_count"]) + sum(int(s.get("trades_count", 0)) for s in recovered_sessions)
    wins = int(trade_stats["wins"])
    losses = int(trade_stats["losses"])
    # Aggregate-only recoveries intentionally do not invent win/loss details.
    realized_pnl = round(
        float(trade_stats["realized_pnl"])
        + sum(float(s.get("realized_pnl", 0.0)) for s in recovered_sessions),
        2,
    )
    fees = round(
        float(trade_stats["fees"])
        + sum(float(s.get("fees", 0.0)) for s in recovered_sessions),
        2,
    )
    opening_equity = account.daily_starting_equity
    if all_summaries:
        earliest = min(all_summaries, key=lambda summary: summary["session_date"])
        opening_equity = float(earliest.get("opening_equity", opening_equity))
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "timezone": "America/New_York",
        "persistence": {
            "status": "durable" if persistence_healthy else "recovery_halt",
            "last_checkpoint_at": state_store.last_checkpoint_at,
            "restored_at": state_store.restored_at,
            "schema_version": SCHEMA_VERSION,
        },
        "summary": {
            "opening_equity": round(opening_equity, 2),
            "current_equity": round(account.equity, 2),
            "realized_pnl": realized_pnl,
            "fees": fees,
            "fees_known": all(summary.get("fees_known", True) for summary in recovered_sessions),
            "trades_count": trades_count,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / max(1, wins + losses), 4),
        },
        "items": items,
        "recovered_sessions": recovered_sessions,
        "next_cursor": next_cursor,
    }


@app.get("/api/history/sessions")
async def get_session_history() -> List[Dict[str, Any]]:
    if state_store is None:
        raise HTTPException(status_code=503, detail="Durable session history is not enabled")
    return state_store.list_session_summaries(None)


class OrderCreateRequest(BaseModel):
    symbol: str
    side: str
    order_type: str
    qty: int = Field(gt=0, description="Order quantity must be strictly positive")
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    strategy_id: str = "MANUAL"


@app.post("/api/orders")
async def submit_order(req: OrderCreateRequest) -> Dict[str, Any]:
    """Create and submit an order through the pre-trade risk and buying power gates."""
    try:
        side = OrderSide(req.side.upper())
        otype = OrderType(req.order_type.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if otype == OrderType.STOP_LIMIT:
        raise HTTPException(status_code=400, detail="STOP_LIMIT orders are not supported by the execution engine")

    symbol = req.symbol.upper()
    existing_pos = account.positions.get(symbol)
    is_reducing = bool(
        existing_pos
        and (
            (existing_pos.side == PositionSide.LONG and side == OrderSide.SELL)
            or (existing_pos.side == PositionSide.SHORT and side == OrderSide.BUY)
        )
        and req.qty <= existing_pos.shares
    )
    bracket_id = bracket_manager.symbol_to_bracket.get(symbol) if is_reducing else None
    if existing_pos is not None and not is_reducing:
        raise HTTPException(
            status_code=409,
            detail="Manual position increases are disabled; flatten the tracked bracket first",
        )
    if is_reducing and bracket_id is None:
        raise HTTPException(
            status_code=409,
            detail="Position has no durable trade linkage; use the recovery flatten workflow",
        )
    estimated_entry = req.limit_price or latest_market_prices.get(symbol)
    if not is_reducing and (req.stop_price is None or not estimated_entry):
        raise HTTPException(
            status_code=400,
            detail="Opening orders require stop_price and a known limit/latest market price",
        )

    try:
        order = engine.create_order(
            symbol=symbol,
            side=side,
            order_type=otype,
            qty=req.qty,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            estimated_price=estimated_entry,
            strategy_id=req.strategy_id,
            parent_order_id=bracket_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    submitted = engine.submit_order(order.id)
    if submitted.status.value == "ACCEPTED" and not is_reducing:
        bracket = bracket_manager.create_bracket(
            bracket_id=f"brk_{submitted.id}",
            symbol=symbol,
            side="LONG" if side == OrderSide.BUY else "SHORT",
            total_qty=req.qty,
            entry_price=float(estimated_entry),
            stop_price=float(req.stop_price),
            strategy_id=req.strategy_id,
            timestamp=datetime.now(timezone.utc),
        )
        entry_order_to_bracket[submitted.id] = bracket.bracket_id
    _checkpoint_runtime("API_ORDER_SUBMIT")
    return {
        "order_id": submitted.id,
        "status": submitted.status.value,
        "reject_reason": submitted.reject_reason,
    }


@app.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: str) -> Dict[str, Any]:
    """Cancel a working order."""
    try:
        cancelled = engine.cancel_order(order_id, reason="API_REQUEST")
        _release_dead_entry_brackets()
        _checkpoint_runtime("API_ORDER_CANCEL")
        return {"order_id": cancelled.id, "status": cancelled.status.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class FlattenRequest(BaseModel):
    symbol: Optional[str] = None


async def _execute_manual_flatten(
    target_symbols: List[str], now_dt: datetime, event_key: Optional[str]
) -> Dict[str, Any]:
    flattened = []

    for sym in target_symbols:
        # Cancel all working orders in engine.working_orders for this symbol
        working_for_sym = [oid for oid, o in list(engine.working_orders.items()) if o.symbol == sym]
        for oid in working_for_sym:
            engine.cancel_order(oid, reason="MANUAL_FLATTEN")

        bracket_id = bracket_manager.symbol_to_bracket.get(sym)
        if bracket_id:
            cancel_dir = bracket_manager.cancel_bracket_for_flattening(sym, reason="MANUAL_FLATTEN")
            if cancel_dir and cancel_dir.orders_to_cancel:
                for oid in cancel_dir.orders_to_cancel:
                    if oid in engine.working_orders:
                        engine.cancel_order(oid, reason="MANUAL_FLATTEN")
            bracket_manager.cancel_pending_entry_bracket(sym)

        pos = account.positions.get(sym)
        if pos:
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                strategy_id="MANUAL_FLATTEN", parent_order_id=bracket_id,
            )
            engine.submit_order(order.id)
            fills = _flatten_symbol(sym, pos.market_price, now_dt)
            _reconcile_fills(fills)
            flattened.append(sym)

    _checkpoint_runtime(
        "MANUAL_FLATTEN",
        (event_key, "MANUAL_FLATTEN") if event_key else None,
    )
    await broadcast_ui_state(force=True)
    return {"flattened": flattened, "remaining_positions": len(account.positions)}


@app.post("/api/flatten")
async def manual_flatten(req: Optional[FlattenRequest] = None) -> Dict[str, Any]:
    """Manually flatten a position or all open positions."""
    now_dt = datetime.now(timezone.utc)
    if req and req.symbol:
        target_symbols = [req.symbol.upper()]
    else:
        target_symbols = sorted(
            list(
                set(account.positions.keys())
                | {o.symbol.upper() for o in engine.working_orders.values()}
                | {s.upper() for s in bracket_manager.symbol_to_bracket.keys()}
            )
        )
    payload = {"target_symbols": target_symbols, "timestamp": now_dt}
    should_process, event_key = _begin_durable_event("MANUAL_FLATTEN", payload)
    if not should_process:
        return {"flattened": [], "remaining_positions": len(account.positions), "duplicate": True}
    return await _execute_manual_flatten(target_symbols, now_dt, event_key)


# Real-Time UI WebSocket Endpoint (Port 8005)
@app.websocket("/ws/ui")
async def ui_websocket_endpoint(websocket: WebSocket) -> None:
    """Real-time bi-directional streaming for the mobile trading UI."""
    await websocket.accept()
    ui_clients.add(websocket)
    try:
        await broadcast_ui_state(force=True)
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
                if action == "FLATTEN_POSITION":
                    sym = msg.get("symbol", "").upper()
                    await manual_flatten(FlattenRequest(symbol=sym))
                elif action == "FLATTEN_ALL":
                    await manual_flatten()
                elif action == "TIGHTEN_STOP":
                    sym = msg.get("symbol", "").upper()
                    try:
                        new_stop = float(msg.get("new_stop", 0.0))
                    except (TypeError, ValueError):
                        new_stop = 0.0
                    if new_stop <= 0:
                        log.warning("Rejected TIGHTEN_STOP for %s: invalid new_stop %s", sym, msg.get("new_stop"))
                        continue
                    pos = account.positions.get(sym)
                    mkt_price = pos.market_price if pos else None
                    bracket_dir = bracket_manager.manual_tighten_stop(sym, new_stop, current_market_price=mkt_price)
                    if bracket_dir and getattr(bracket_dir, "orders_to_modify", None):
                        for mod in bracket_dir.orders_to_modify:
                            oid = mod.get("order_id")
                            if oid and oid in engine.working_orders:
                                engine.working_orders[oid].stop_price = mod.get("new_stop_price", new_stop)
                    _checkpoint_runtime("MANUAL_TIGHTEN_STOP")
                    await broadcast_ui_state(force=True)
                elif action == "SWING_EXIT_NEXT_OPEN":
                    sym = msg.get("symbol", "").upper()
                    staged = swing_strategy_engine.stage_manual_exit_next_open(sym)
                    if staged:
                        log.info("WebSocket processed SWING_EXIT_NEXT_OPEN for %s", sym)
                    _checkpoint_runtime("SWING_MANUAL_EXIT_NEXT_OPEN")
                    await broadcast_ui_state(force=True)
                elif action == "SWING_EXIT_IMMEDIATE":
                    sym = msg.get("symbol", "").upper()
                    exit_res = swing_strategy_engine.execute_immediate_exit(sym)
                    if exit_res:
                        log.info("WebSocket processed SWING_EXIT_IMMEDIATE for %s", sym)
                    _checkpoint_runtime("SWING_MANUAL_EXIT_IMMEDIATE")
                    await broadcast_ui_state(force=True)
                elif action == "SWING_TIGHTEN_STOP":
                    sym = msg.get("symbol", "").upper()
                    try:
                        new_stop = float(msg.get("new_stop", 0.0))
                    except (TypeError, ValueError):
                        new_stop = 0.0
                    success = swing_strategy_engine.tighten_stop(sym, new_stop)
                    if success:
                        log.info("WebSocket processed SWING_TIGHTEN_STOP for %s to %s", sym, new_stop)
                    _checkpoint_runtime("SWING_MANUAL_TIGHTEN_STOP")
                    await broadcast_ui_state(force=True)
            except Exception as e:
                log.error(f"Error handling UI action: {e}")
    except WebSocketDisconnect:
        pass
    finally:
        ui_clients.discard(websocket)


@app.get("/api/swing/state")
async def get_swing_state() -> Dict[str, Any]:
    """Return latest swing trading engine state."""
    return swing_strategy_engine.to_ui_dict()


class SwingActionRequest(BaseModel):
    action: str  # "EXIT_NEXT_OPEN", "EXIT_IMMEDIATE", "TIGHTEN_STOP"
    symbol: str
    new_stop: Optional[float] = None


@app.post("/api/swing/action")
async def post_swing_action(req: SwingActionRequest) -> Dict[str, Any]:
    """Execute operator action for the swing trading engine."""
    sym = req.symbol.upper()
    act = req.action.upper()
    if act in ("EXIT_NEXT_OPEN", "SWING_EXIT_NEXT_OPEN"):
        staged = swing_strategy_engine.stage_manual_exit_next_open(sym)
        await broadcast_ui_state(force=True)
        return {"status": "ok", "action": "SWING_EXIT_NEXT_OPEN", "symbol": sym, "staged": staged is not None}
    elif act in ("EXIT_IMMEDIATE", "SWING_EXIT_IMMEDIATE"):
        exit_res = swing_strategy_engine.execute_immediate_exit(sym)
        await broadcast_ui_state(force=True)
        return {"status": "ok", "action": "SWING_EXIT_IMMEDIATE", "symbol": sym, "result": exit_res}
    elif act in ("TIGHTEN_STOP", "SWING_TIGHTEN_STOP"):
        new_stop = req.new_stop or 0.0
        success = swing_strategy_engine.tighten_stop(sym, new_stop)
        await broadcast_ui_state(force=True)
        return {"status": "ok", "action": "SWING_TIGHTEN_STOP", "symbol": sym, "success": success}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown swing action {req.action}")



# Railway serves the backend and the exported mobile dashboard from one process.
# The mount is a no-op in local development until `next build` has produced `out/`.
STATIC_UI_DIR = Path(__file__).resolve().parents[2] / "frontend" / "out"
if STATIC_UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_UI_DIR, html=True), name="ui")
