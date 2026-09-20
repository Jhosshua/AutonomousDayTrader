"""backend/app/main.py
FastAPI Core Trading Engine API Server & Real-Time WebSocket Streaming (Port 8005).
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.app.config import settings
from backend.app.core.account import PaperTradingAccount, PositionSide
from backend.app.core.bracket import DynamicBracketManager
from backend.app.core.engine import ExecutionEngine, OrderSide, OrderType, TimeInForce
from backend.app.core.event_bus import event_bus
from backend.app.core.flattening import FlatteningDirective, FlatteningPhase, ZeroOvernightFlatteningEngine
from backend.app.core.risk import BreakerStatus, InstitutionalRiskEngine
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

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
log = logging.getLogger("AutonomousDayTrader")

# System Components
account = PaperTradingAccount(initial_cash=settings.INITIAL_CASH)
risk_engine = InstitutionalRiskEngine()
bracket_manager = DynamicBracketManager()
flattening_engine = ZeroOvernightFlatteningEngine()

# Strategies & Dynamic Self-Adaptation Engine
orb_strategy = OpeningRangeBreakoutStrategy()
vwap_strategy = VWAPPullbackStrategy()
news_strategy = NewsMomentumStrategy()
mean_reversion_strategy = MeanReversionStrategy()
adaptation_engine = DynamicAdaptationEngine()

strategies: List[Strategy] = [
    orb_strategy,
    vwap_strategy,
    news_strategy,
    mean_reversion_strategy,
]
strategy_map: Dict[str, Strategy] = {s.strategy_id: s for s in strategies}

# Real-time symbol market price cache for pre-trade risk valuation
latest_market_prices: Dict[str, float] = {}


def pre_trade_risk_validator(order: Any, acct: PaperTradingAccount) -> tuple[bool, str]:
    """Validate order against Institutional Risk Engine and active flattening lockout."""
    is_lockout = flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING
    active_symbols = set(acct.positions.keys())
    active_sectors = {
        risk_engine.symbol_sectors.get(s, "Other")
        for s in active_symbols
        if s in risk_engine.symbol_sectors
    }

    # Differentiate position-reducing / liquidation orders from position-opening orders
    existing_pos = acct.positions.get(order.symbol.upper())
    is_exit = False
    if getattr(order, "strategy_id", None) in (
        "CIRCUIT_BREAKER",
        "AUTO_FLATTEN",
        "EMERGENCY_SWEEP",
        "MANUAL_FLATTEN",
        "NEWS_CONTRADICTION",
        "NEWS_CONTRADICTION_CIRCUIT_BREAKER",
    ):
        is_exit = True
    elif existing_pos is not None:
        if existing_pos.side == PositionSide.LONG and order.side == OrderSide.SELL:
            is_exit = True
        elif existing_pos.side == PositionSide.SHORT and order.side == OrderSide.BUY:
            is_exit = True

    # Estimate entry price (do NOT set est_price = order.stop_price!)
    sym = order.symbol.upper()
    est_price = order.limit_price
    if not est_price:
        if sym in acct.positions and acct.positions[sym].market_price > 0:
            est_price = acct.positions[sym].market_price
        elif sym in latest_market_prices and latest_market_prices[sym] > 0:
            est_price = latest_market_prices[sym]
        elif getattr(order, "estimated_price", None):
            est_price = getattr(order, "estimated_price")
        elif order.stop_price:
            est_price = round(order.stop_price / 0.98 if order.side == OrderSide.BUY else order.stop_price / 1.02, 2)
        else:
            est_price = 100.0

    s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)

    res = risk_engine.evaluate_order_request(
        symbol=order.symbol,
        side=order.side.value,
        requested_qty=order.qty,
        entry_price=est_price,
        stop_price=s_price,
        account_equity=acct.equity,
        buying_power=acct.buying_power,
        active_positions_count=len(acct.positions),
        active_symbols=active_symbols,
        active_sectors=active_sectors,
        is_entry_lockout_active=is_lockout,
        is_exit=is_exit,
    )
    return res.approved, res.reason


engine = ExecutionEngine(account=account, risk_validator=pre_trade_risk_validator)

# Feed Ingestion Clients
stock_ws_client: Optional[StockWebSocketClient] = None
news_ws_client: Optional[NewsWebSocketClient] = None
vix_client: Optional[VixClient] = None

# Active UI WebSocket Clients
ui_clients: Set[WebSocket] = set()
recent_news: List[Dict[str, Any]] = []
last_vix_print: Optional[VixPrint] = None


async def broadcast_ui_state() -> None:
    """Broadcast current system state to connected Apple Music UI clients."""
    if not ui_clients:
        return

    snapshot = account.get_snapshot()
    primary_pos = None
    if account.positions:
        first_symbol = next(iter(account.positions.keys()))
        pos = account.positions[first_symbol]
        bracket_id = bracket_manager.symbol_to_bracket.get(first_symbol)
        bracket = bracket_manager.brackets.get(bracket_id) if bracket_id else None
        primary_pos = {
            "symbol": pos.symbol,
            "side": pos.side.value,
            "shares": pos.shares,
            "entry_price": pos.avg_entry_price,
            "market_price": pos.market_price,
            "market_value": pos.market_value,
            "unrealized_pnl": pos.unrealized_pnl,
            "unrealized_pnl_pct": pos.unrealized_pnl_pct,
            "stop_loss": bracket.current_stop_price if bracket else round(pos.avg_entry_price * 0.98, 2),
            "take_profit_1": bracket.target_1_price if bracket else round(pos.avg_entry_price * 1.015, 2),
            "take_profit_2": bracket.target_2_price if bracket else round(pos.avg_entry_price * 1.025, 2),
            "strategy_id": bracket.strategy_id if bracket else "MANUAL",
        }

    daily_pnl = round(snapshot.equity - settings.INITIAL_CASH, 2)
    daily_pnl_pct = round((snapshot.equity - settings.INITIAL_CASH) / settings.INITIAL_CASH * 100.0, 2)

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
        },
        "market_context": adaptation_engine.get_market_context(),
        "strategies": [s.to_dict() for s in strategies],
        "primary_position": primary_pos,
        "all_positions": [p.__dict__ for p in snapshot.positions.values()],
        "positions_count": len(account.positions),
        "working_orders_count": len(engine.working_orders),
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
    }

    raw = json.dumps(payload, default=str)
    for ws in list(ui_clients):
        try:
            await ws.send_text(raw)
        except Exception:
            ui_clients.discard(ws)


async def execute_strategy_signal(signal: SignalEvent, bar: Optional[BarEvent] = None) -> None:
    """Evaluate and route strategy signals through adaptation and risk engines."""
    sym = signal.symbol.upper()
    existing_pos = account.positions.get(sym)

    # 1. Contradiction or Exit Signal
    if "CONTRADICTION" in signal.reason or "EXIT" in signal.reason:
        if existing_pos:
            cancel_dir = bracket_manager.cancel_bracket_for_flattening(sym, reason=signal.reason)
            if cancel_dir and cancel_dir.orders_to_cancel:
                for oid in cancel_dir.orders_to_cancel:
                    if oid in engine.working_orders:
                        engine.cancel_order(oid, reason="NEWS_CONTRADICTION")
            side = OrderSide.SELL if existing_pos.side == PositionSide.LONG else OrderSide.BUY
            order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=existing_pos.shares, strategy_id="NEWS_CONTRADICTION"
            )
            engine.submit_order(order.id)
            fill_p = bar.close if bar else existing_pos.market_price
            fill_dt = bar.timestamp if bar else datetime.now(timezone.utc)
            engine.process_bar(sym, fill_p, fill_p, fill_p, fill_p, 100000, fill_dt)
        return

    # 2. Position-opening entry signal
    latest_market_prices[sym] = signal.entry_price if bar is None else bar.close
    is_active = sym in account.positions
    approved, reason, qty = adaptation_engine.evaluate_signal_admission(
        signal=signal,
        equity=account.equity,
        current_positions_count=len(account.positions),
        is_symbol_active=is_active,
    )
    if not approved or qty <= 0:
        return

    adapted_stop = adaptation_engine.calculate_adapted_stop(signal)

    side = OrderSide.BUY if (signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY") else OrderSide.SELL
    otype = OrderType.LIMIT if (signal.order_type == OrderType.LIMIT or str(signal.order_type).upper() == "LIMIT") else OrderType.MARKET

    order = engine.create_order(
        symbol=sym,
        side=side,
        order_type=otype,
        qty=qty,
        limit_price=signal.entry_price if otype == OrderType.LIMIT else None,
        stop_price=adapted_stop,
        strategy_id=signal.strategy_id,
    )
    submitted = engine.submit_order(order.id)
    if submitted.status.value in ("SUBMITTED", "ACCEPTED"):
        bracket_manager.create_bracket(
            bracket_id=f"brk_{submitted.id}",
            symbol=sym,
            side="LONG" if side == OrderSide.BUY else "SHORT",
            total_qty=qty,
            entry_price=signal.entry_price,
            stop_price=adapted_stop,
            strategy_id=signal.strategy_id,
            timestamp=signal.timestamp,
        )
        if bar:
            engine.process_bar(bar.symbol, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.timestamp)


# Event Bus Handlers
async def handle_bar_event(bar: BarEvent) -> None:
    """Ingest bar, update clocks, match orders, process strategies, check risk, update trailing stops."""
    latest_market_prices[bar.symbol.upper()] = bar.close
    flattening_engine.clock.set_simulated_time(bar.timestamp)
    adaptation_engine.update_clock(bar.timestamp)

    directive = flattening_engine.check_time_tick()
    if directive:
        await handle_flattening_directive(directive)

    # Evaluate strategies on new bar
    collected_signals: List[SignalEvent] = []
    for strat in strategies:
        try:
            sigs = strat.on_bar(bar)
            if sigs:
                collected_signals.extend(sigs)
        except Exception as e:
            log.error(f"Strategy {strat.strategy_id} error on bar: {e}")

    if collected_signals:
        arbitrated = adaptation_engine.arbitrate_signals(collected_signals)
        for sig in arbitrated:
            await execute_strategy_signal(sig, bar=bar)

    fills = engine.process_bar(
        symbol=bar.symbol,
        open_=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        timestamp=bar.timestamp,
    )

    # Check risk state
    status = risk_engine.evaluate_account_state(
        equity=account.equity,
        cash=account.cash,
        realized_pnl=account.realized_pnl,
        unrealized_pnl=account.unrealized_pnl,
        timestamp=bar.timestamp,
    )
    if status == BreakerStatus.HALTED_DAILY_LOSS:
        account.status = account.status.__class__.CIRCUIT_HALTED
        engine.cancel_all_orders("CIRCUIT_BREAKER_HALT")
        # Liquidate positions
        for sym, pos in list(account.positions.items()):
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            liq_order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="CIRCUIT_BREAKER"
            )
            engine.submit_order(liq_order.id)
            engine.process_bar(sym, bar.close, bar.close, bar.close, bar.close, 100000, bar.timestamp)

    # Trailing stop update
    atr_est = max(0.01, bar.high - bar.low)
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

    await broadcast_ui_state()


async def handle_quote_event(quote: QuoteEvent) -> None:
    """Ingest quote and match working orders."""
    latest_market_prices[quote.symbol.upper()] = (quote.bid_price + quote.ask_price) / 2.0
    for strat in strategies:
        try:
            strat.on_quote(quote)
        except Exception as e:
            log.error(f"Strategy {strat.strategy_id} error on quote: {e}")

    engine.process_quote(
        symbol=quote.symbol,
        bid=quote.bid_price,
        ask=quote.ask_price,
        timestamp=quote.timestamp,
    )


async def handle_news_event(news: NewsEvent) -> None:
    """Record news, trigger catalyst events and contradiction checks."""
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

    exit_signals = news_strategy.on_news(news)
    for sig in exit_signals:
        await execute_strategy_signal(sig)


async def handle_vix_print(vprint: VixPrint) -> None:
    """Update VIX print and scale risk and adaptation parameters."""
    global last_vix_print
    last_vix_print = vprint
    adaptation_engine.on_vix_print(vprint)
    for strat in strategies:
        try:
            strat.on_vix(vprint)
        except Exception as e:
            log.error(f"Strategy {strat.strategy_id} error on vix: {e}")


async def handle_flattening_directive(directive: FlatteningDirective) -> None:
    """Apply auto-flattening directives."""
    if directive.cancel_all_orders:
        engine.cancel_all_orders("FLATTENING_DIRECTIVE")

    if directive.liquidate_all_positions:
        now_dt = directive.timestamp
        for sym, pos in list(account.positions.items()):
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            liq_order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="AUTO_FLATTEN"
            )
            engine.submit_order(liq_order.id)
            engine.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, now_dt)

    if directive.run_audit:
        audit_res = flattening_engine.execute_phase_4_audit(
            open_positions=account.positions,
            working_orders=list(engine.working_orders.values()),
        )
        if audit_res.cancel_all_orders and engine.working_orders:
            engine.cancel_all_orders("AUDIT_EMERGENCY_SWEEP")
        if audit_res.liquidate_all_positions and account.positions:
            now_dt = audit_res.timestamp
            for sym, pos in list(account.positions.items()):
                side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
                sweep_order = engine.create_order(
                    symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="EMERGENCY_SWEEP"
                )
                engine.submit_order(sweep_order.id)
                engine.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, now_dt)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and graceful teardown lifecycle."""
    log.info(f"Starting AutonomousDayTrader backend on port {settings.API_PORT}...")
    # Register bus event handlers
    event_bus.subscribe(BarEvent, handle_bar_event)
    event_bus.subscribe(QuoteEvent, handle_quote_event)
    event_bus.subscribe(NewsEvent, handle_news_event)
    event_bus.subscribe(VixPrint, handle_vix_print)

    yield

    log.info("Shutting down AutonomousDayTrader backend...")
    if stock_ws_client:
        await stock_ws_client.stop()
    if news_ws_client:
        await news_ws_client.stop()
    if vix_client:
        await vix_client.stop()
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# REST Endpoints
@app.get("/health")
async def get_health() -> Dict[str, Any]:
    """System health telemetry and component statuses."""
    return {
        "status": "healthy",
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
            "api": settings.API_PORT,
            "ui": settings.UI_PORT,
            "mock": settings.MOCK_PORT,
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
    """Active strategies state and performance statistics."""
    return [s.to_dict() for s in strategies]


@app.get("/api/market-context")
async def get_market_context() -> Dict[str, Any]:
    """Current VIX volatility regime and Time-of-Day execution phase."""
    return adaptation_engine.get_market_context()


@app.get("/api/audit")
async def get_audit_log(limit: int = 50) -> List[Dict[str, Any]]:
    """Recent execution and order audit trail."""
    return [r.__dict__ for r in engine.audit_log[-limit:]]


class OrderCreateRequest(BaseModel):
    symbol: str
    side: str
    order_type: str
    qty: int
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

    order = engine.create_order(
        symbol=req.symbol,
        side=side,
        order_type=otype,
        qty=req.qty,
        limit_price=req.limit_price,
        stop_price=req.stop_price,
        strategy_id=req.strategy_id,
    )
    submitted = engine.submit_order(order.id)
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
        return {"order_id": cancelled.id, "status": cancelled.status.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class FlattenRequest(BaseModel):
    symbol: Optional[str] = None


@app.post("/api/flatten")
async def manual_flatten(req: Optional[FlattenRequest] = None) -> Dict[str, Any]:
    """Manually flatten a position or all open positions."""
    now_dt = datetime.now(timezone.utc)
    target_symbols = [req.symbol.upper()] if (req and req.symbol) else list(account.positions.keys())
    flattened = []

    for sym in target_symbols:
        pos = account.positions.get(sym)
        if pos:
            cancel_dir = bracket_manager.cancel_bracket_for_flattening(sym, reason="MANUAL_FLATTEN")
            if cancel_dir and cancel_dir.orders_to_cancel:
                for oid in cancel_dir.orders_to_cancel:
                    if oid in engine.working_orders:
                        engine.cancel_order(oid, reason="MANUAL_FLATTEN")
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares, strategy_id="MANUAL_FLATTEN"
            )
            engine.submit_order(order.id)
            engine.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, now_dt)
            flattened.append(sym)

    await broadcast_ui_state()
    return {"flattened": flattened, "remaining_positions": len(account.positions)}


# Real-Time UI WebSocket Endpoint (Port 8005)
@app.websocket("/ws/ui")
async def ui_websocket_endpoint(websocket: WebSocket) -> None:
    """Real-time bi-directional streaming for the Apple Music mobile UI."""
    await websocket.accept()
    ui_clients.add(websocket)
    try:
        await broadcast_ui_state()
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
                    new_stop = float(msg.get("new_stop", 0.0))
                    bracket_dir = None
                    if hasattr(bracket_manager, "tighten_stop"):
                        bracket_dir = bracket_manager.tighten_stop(sym, new_stop)
                    elif hasattr(bracket_manager, "manual_tighten_stop"):
                        bracket_dir = bracket_manager.manual_tighten_stop(sym, new_stop)
                    if bracket_dir and getattr(bracket_dir, "orders_to_modify", None):
                        for mod in bracket_dir.orders_to_modify:
                            oid = mod.get("order_id")
                            if oid and oid in engine.working_orders:
                                engine.working_orders[oid].stop_price = mod.get("new_stop_price", new_stop)
                    for wo in engine.working_orders.values():
                        if wo.symbol == sym and wo.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
                            wo.stop_price = new_stop
                    await broadcast_ui_state()
            except Exception as e:
                log.error(f"Error handling UI action: {e}")
    except WebSocketDisconnect:
        pass
    finally:
        ui_clients.discard(websocket)
