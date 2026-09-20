"""backend/app/main.py
FastAPI Core Trading Engine API Server & Real-Time WebSocket Streaming (Port 8005).
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.config import settings
from backend.app.core.account import PaperTradingAccount, PositionSide
from backend.app.core.bracket import BracketChildType, DynamicBracketManager
from backend.app.core.engine import BracketRole, ExecutionEngine, OrderSide, OrderType, TimeInForce
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
market_history: Dict[str, List[Dict[str, Any]]] = {}
entry_order_to_bracket: Dict[str, str] = {}
bracket_realized_pnl: Dict[str, float] = {}
relay_statuses: Dict[str, str] = {"stock": "unconfigured", "news": "unconfigured", "vix": "unconfigured"}
runtime_tasks: Set[asyncio.Task] = set()
simulation_mode: bool = False


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
        vix_multiplier=adaptation_engine.current_sizing_multiplier,
        is_entry_lockout_active=is_lockout,
        is_exit=is_exit,
    )
    if res.approved and not is_exit and order.qty > res.authorized_qty:
        return False, f"RISK_SIZE_REJECTED: requested {order.qty} exceeds authorized {res.authorized_qty} shares"
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


def _serialize_position(symbol: str) -> Dict[str, Any]:
    """Serialize one position with the bracket and chart data that actually backs the UI."""
    pos = account.positions[symbol]
    bracket_id = bracket_manager.symbol_to_bracket.get(symbol)
    bracket = bracket_manager.brackets.get(bracket_id) if bracket_id else None
    return {
        "symbol": pos.symbol,
        "side": pos.side.value,
        "shares": pos.shares,
        "entry_price": pos.avg_entry_price,
        "avg_entry_price": pos.avg_entry_price,
        "market_price": pos.market_price,
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
        "chart_points": market_history.get(symbol, [])[-120:],
    }


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
            order.qty = new_qty
            order.remaining_qty = new_qty
        if "new_stop_price" in modification:
            order.stop_price = float(modification["new_stop_price"])


def _record_completed_bracket(bracket_id: str) -> None:
    bracket = bracket_manager.brackets.get(bracket_id)
    if not bracket:
        return
    strategy = strategy_map.get(bracket.strategy_id)
    if strategy:
        strategy.record_trade(bracket_realized_pnl.get(bracket_id, 0.0))
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


async def broadcast_ui_state() -> None:
    """Broadcast current system state to connected Apple Music UI clients."""
    if not ui_clients:
        return

    snapshot = account.get_snapshot()
    primary_pos = _serialize_position(next(iter(account.positions))) if account.positions else None

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
        "strategies": [s.to_dict() for s in strategies],
        "primary_position": primary_pos,
        "all_positions": [_serialize_position(symbol) for symbol in account.positions],
        "positions_count": len(account.positions),
        "working_orders_count": len(engine.working_orders),
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
            fills = engine.process_bar(sym, fill_p, fill_p, fill_p, fill_p, 100000, fill_dt)
            _reconcile_fills(fills)
        return

    # 2. Position-opening entry signal
    if signal.entry_price <= 0:
        return
    latest_market_prices[sym] = signal.entry_price if bar is None else bar.close
    adapted_stop = adaptation_engine.calculate_adapted_stop(signal)
    is_active = sym in account.positions
    approved, reason, qty = adaptation_engine.evaluate_signal_admission(
        signal=signal,
        equity=account.equity,
        current_positions_count=len(account.positions),
        is_symbol_active=is_active,
    )
    if not approved or qty <= 0:
        return

    # The adaptation layer sizes from the strategy's raw stop for its public
    # sizing contract.  Execution submits the volatility-adjusted stop, so cap
    # that quantity against the exact risk geometry that will reach the order
    # engine before creating the order.
    active_symbols = set(account.positions.keys())
    active_sectors = {
        risk_engine.symbol_sectors.get(s, "Other")
        for s in active_symbols
        if s in risk_engine.symbol_sectors
    }
    risk_preview = risk_engine.evaluate_order_request(
        symbol=sym,
        side="BUY" if signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY" else "SELL",
        requested_qty=qty,
        entry_price=signal.entry_price,
        stop_price=adapted_stop,
        account_equity=account.equity,
        buying_power=account.buying_power,
        active_positions_count=len(account.positions),
        active_symbols=active_symbols,
        active_sectors=active_sectors,
        vix_multiplier=adaptation_engine.current_sizing_multiplier,
        is_entry_lockout_active=flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING,
        is_exit=False,
    )
    if not risk_preview.approved:
        return
    qty = min(qty, risk_preview.authorized_qty)
    if qty <= 0:
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
        bracket = bracket_manager.create_bracket(
            bracket_id=f"brk_{submitted.id}",
            symbol=sym,
            side="LONG" if side == OrderSide.BUY else "SHORT",
            total_qty=qty,
            entry_price=signal.entry_price,
            stop_price=adapted_stop,
            strategy_id=signal.strategy_id,
            timestamp=signal.timestamp,
            target_1_override=signal.take_profit_1 if signal.strategy_id == "mean_reversion" else None,
            target_2_override=signal.take_profit_2 if signal.strategy_id == "mean_reversion" else None,
        )
        entry_order_to_bracket[submitted.id] = bracket.bracket_id
        if bar:
            fills = engine.process_bar(bar.symbol, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.timestamp)
            _reconcile_fills(fills)


# Event Bus Handlers
async def handle_bar_event(bar: BarEvent) -> None:
    """Ingest bar, update clocks, match orders, process strategies, check risk, update trailing stops."""
    latest_market_prices[bar.symbol.upper()] = bar.close
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
    if simulation_mode:
        flattening_engine.clock.set_simulated_time(bar.timestamp)
    else:
        flattening_engine.clock.clear_simulated_time()
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
        account.status = account.status.__class__.CIRCUIT_HALTED
        engine.cancel_all_orders("CIRCUIT_BREAKER_HALT")
        # Liquidate positions
        for sym, pos in list(account.positions.items()):
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            bracket_id = bracket_manager.symbol_to_bracket.get(sym)
            liq_order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                strategy_id="CIRCUIT_BREAKER", parent_order_id=bracket_id,
            )
            engine.submit_order(liq_order.id)
            liq_fills = engine.process_bar(sym, bar.close, bar.close, bar.close, bar.close, 100000, bar.timestamp)
            _reconcile_fills(liq_fills)

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

    fills = engine.process_quote(
        symbol=quote.symbol,
        bid=quote.bid_price,
        ask=quote.ask_price,
        timestamp=quote.timestamp,
    )
    _reconcile_fills(fills)
    await broadcast_ui_state()


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
    await broadcast_ui_state()


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
    await broadcast_ui_state()


async def handle_flattening_directive(directive: FlatteningDirective) -> None:
    """Apply auto-flattening directives."""
    if directive.cancel_all_orders:
        engine.cancel_all_orders("FLATTENING_DIRECTIVE")

    if directive.liquidate_all_positions:
        now_dt = directive.timestamp
        for sym, pos in list(account.positions.items()):
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            bracket_id = bracket_manager.symbol_to_bracket.get(sym)
            liq_order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                strategy_id="AUTO_FLATTEN", parent_order_id=bracket_id,
            )
            engine.submit_order(liq_order.id)
            fills = engine.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, now_dt)
            _reconcile_fills(fills)

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
                bracket_id = bracket_manager.symbol_to_bracket.get(sym)
                sweep_order = engine.create_order(
                    symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                    strategy_id="EMERGENCY_SWEEP", parent_order_id=bracket_id,
                )
                engine.submit_order(sweep_order.id)
                fills = engine.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, now_dt)
                _reconcile_fills(fills)

        if audit_res.audit_passed:
            account.status = account.status.__class__.EOD_FLAT


async def _runtime_clock_loop() -> None:
    """Keep EOD controls alive even when a market-data bar is delayed or absent."""
    while True:
        try:
            if simulation_mode:
                # Replay bars advance the simulated clock synchronously.  Do
                # not let the wall-clock task observe the host's unrelated
                # date/time and move the replay into MARKET_CLOSED.
                await asyncio.sleep(1.0)
                continue
            flattening_engine.clock.clear_simulated_time()
            directive = flattening_engine.check_time_tick()
            if directive:
                await handle_flattening_directive(directive)
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("Runtime clock loop failed")
            await asyncio.sleep(1.0)


async def _handle_relay_status(status: RelayStatusEvent) -> None:
    relay_statuses[status.feed_type] = status.status


def reset_runtime_state(starting_equity: Optional[float] = None) -> None:
    """Reset in-memory session state for deterministic replay and test isolation."""
    global last_vix_print, simulation_mode
    simulation_mode = False
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
    latest_market_prices.clear()
    market_history.clear()
    recent_news.clear()
    last_vix_print = None
    risk_engine.reset_daily_metrics(account.equity)
    flattening_engine.reset_for_new_session()
    for strategy in strategies:
        strategy.reset_daily_stats()


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
    # Register bus event handlers
    event_bus.subscribe(BarEvent, handle_bar_event)
    event_bus.subscribe(QuoteEvent, handle_quote_event)
    event_bus.subscribe(NewsEvent, handle_news_event)
    event_bus.subscribe(VixPrint, handle_vix_print)
    event_bus.subscribe(RelayStatusEvent, _handle_relay_status)

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
    stock_ws_client = None
    news_ws_client = None
    vix_client = None
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
    operational_status = "healthy" if configured and all(
        relay_statuses.get(feed) == "connected" for feed in ("stock", "news", "vix")
    ) else "degraded" if configured else "unconfigured"
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
        estimated_price=latest_market_prices.get(req.symbol.upper()),
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
            bracket_id = bracket_manager.symbol_to_bracket.get(sym)
            cancel_dir = bracket_manager.cancel_bracket_for_flattening(sym, reason="MANUAL_FLATTEN")
            if cancel_dir and cancel_dir.orders_to_cancel:
                for oid in cancel_dir.orders_to_cancel:
                    if oid in engine.working_orders:
                        engine.cancel_order(oid, reason="MANUAL_FLATTEN")
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            order = engine.create_order(
                symbol=sym, side=side, order_type=OrderType.MARKET, qty=pos.shares,
                strategy_id="MANUAL_FLATTEN", parent_order_id=bracket_id,
            )
            engine.submit_order(order.id)
            fills = engine.process_bar(sym, pos.market_price, pos.market_price, pos.market_price, pos.market_price, 100000, now_dt)
            _reconcile_fills(fills)
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


# Railway serves the backend and the exported mobile dashboard from one process.
# The mount is a no-op in local development until `next build` has produced `out/`.
STATIC_UI_DIR = Path(__file__).resolve().parents[2] / "frontend" / "out"
if STATIC_UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_UI_DIR, html=True), name="ui")
