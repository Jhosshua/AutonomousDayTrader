"""Capture and restore the mutable trading runtime without pickling code."""
from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Dict, Iterable, Optional

from backend.app.core.account import AccountStatus, PaperTradingAccount
from backend.app.core.bracket import BracketStatus, DynamicBracketManager
from backend.app.core.engine import ExecutionEngine
from backend.app.core.flattening import ZeroOvernightFlatteningEngine
from backend.app.core.persistence import decode_runtime_value, encode_runtime_value, PersistenceError
from backend.app.core.risk import InstitutionalRiskEngine
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.base import Strategy


RUNTIME_STATE_VERSION = 1


def capture_runtime_state(
    *,
    account: PaperTradingAccount,
    engine: ExecutionEngine,
    bracket_manager: DynamicBracketManager,
    risk_engine: InstitutionalRiskEngine,
    flattening_engine: ZeroOvernightFlatteningEngine,
    adaptation_engine: DynamicAdaptationEngine,
    strategies: Iterable[Strategy],
    entry_order_to_bracket: Dict[str, str],
    bracket_realized_pnl: Dict[str, float],
    completed_brackets_recorded: set[str],
    latest_market_prices: Dict[str, float],
    market_history: Dict[str, Any],
    recent_news: list[Dict[str, Any]],
    last_session_date: Optional[date],
    last_vix_print: Any,
    ledger_revision: int,
) -> Dict[str, Any]:
    """Return a complete JSON-safe recovery checkpoint."""
    account_state = {
        name: getattr(account, name)
        for name in (
            "initial_balance",
            "daily_starting_equity",
            "cash",
            "equity",
            "status",
            "realized_pnl",
            "unrealized_pnl",
            "fees_paid",
            "maintenance_margin",
            "margin_excess",
            "buying_power",
            "daily_drawdown_dollars",
            "daily_drawdown_pct",
            "positions",
            "cash_transactions",
        )
    }
    risk_state = {
        name: getattr(risk_engine, name)
        for name in (
            "status",
            "risk_level",
            "daily_peak_equity",
            "current_drawdown_dollars",
            "current_drawdown_pct",
            "breaker_triggered_at",
            "breaker_trigger_equity",
            "symbol_sectors",
        )
    }
    flattening_state = {
        name: getattr(flattening_engine, name)
        for name in (
            "current_phase",
            "phase1_executed",
            "phase2_executed",
            "phase3_executed",
            "phase4_executed",
            "audit_passed",
            "audit_retries",
        )
    }
    adaptation_state = {
        name: getattr(adaptation_engine, name)
        for name in (
            "current_vix",
            "current_vix_regime",
            "current_sizing_multiplier",
            "current_stop_multiplier",
            "current_time_phase",
            "last_update",
        )
    }
    state = {
        "runtime_state_version": RUNTIME_STATE_VERSION,
        "account": account_state,
        "engine": {
            "orders": engine.orders,
            "working_order_ids": list(engine.working_orders),
            "audit_log": engine.audit_log,
        },
        "brackets": {
            "brackets": bracket_manager.brackets,
            "symbol_to_bracket": bracket_manager.symbol_to_bracket,
            "order_to_bracket": bracket_manager.order_to_bracket,
            "entry_order_to_bracket": entry_order_to_bracket,
            "bracket_realized_pnl": bracket_realized_pnl,
            "completed_brackets_recorded": completed_brackets_recorded,
        },
        "risk": risk_state,
        "flattening": flattening_state,
        "adaptation": adaptation_state,
        "strategies": {strategy.strategy_id: strategy.__dict__ for strategy in strategies},
        "market": {
            "latest_market_prices": latest_market_prices,
            "market_history": market_history,
            "recent_news": recent_news,
            "last_vix_print": last_vix_print,
        },
        "last_session_date": last_session_date,
        "ledger_revision": ledger_revision,
    }
    encoded = encode_runtime_value(state)
    if not isinstance(encoded, dict):
        raise PersistenceError("Encoded runtime checkpoint is not an object")
    return encoded


def restore_runtime_state(
    payload: Dict[str, Any],
    *,
    account: PaperTradingAccount,
    engine: ExecutionEngine,
    bracket_manager: DynamicBracketManager,
    risk_engine: InstitutionalRiskEngine,
    flattening_engine: ZeroOvernightFlatteningEngine,
    adaptation_engine: DynamicAdaptationEngine,
    strategies: Iterable[Strategy],
    entry_order_to_bracket: Dict[str, str],
    bracket_realized_pnl: Dict[str, float],
    completed_brackets_recorded: set[str],
    latest_market_prices: Dict[str, float],
    market_history: Dict[str, Any],
    recent_news: list[Dict[str, Any]],
) -> Dict[str, Any]:
    """Restore a checkpoint into already-wired singleton components."""
    decoded = decode_runtime_value(payload)
    if decoded.get("runtime_state_version") != RUNTIME_STATE_VERSION:
        raise PersistenceError(
            f"Unsupported runtime state version {decoded.get('runtime_state_version')!r}"
        )

    for name, value in decoded["account"].items():
        setattr(account, name, value)
    account._recompute_account_state()

    engine.orders.clear()
    engine.orders.update(decoded["engine"]["orders"])
    engine.working_orders.clear()
    for order_id in decoded["engine"]["working_order_ids"]:
        order = engine.orders.get(order_id)
        if order is None:
            raise PersistenceError(f"Working order {order_id} is missing from restored orders")
        engine.working_orders[order_id] = order
    engine.audit_log.clear()
    engine.audit_log.extend(decoded["engine"]["audit_log"])

    bracket_state = decoded["brackets"]
    bracket_manager.brackets.clear()
    bracket_manager.brackets.update(bracket_state["brackets"])
    bracket_manager.symbol_to_bracket.clear()
    bracket_manager.symbol_to_bracket.update(bracket_state["symbol_to_bracket"])
    bracket_manager.order_to_bracket.clear()
    bracket_manager.order_to_bracket.update(bracket_state["order_to_bracket"])
    entry_order_to_bracket.clear()
    entry_order_to_bracket.update(bracket_state["entry_order_to_bracket"])
    bracket_realized_pnl.clear()
    bracket_realized_pnl.update(bracket_state["bracket_realized_pnl"])
    completed_brackets_recorded.clear()
    completed_brackets_recorded.update(bracket_state["completed_brackets_recorded"])

    for name, value in decoded["risk"].items():
        setattr(risk_engine, name, value)
    for name, value in decoded["flattening"].items():
        setattr(flattening_engine, name, value)
    for name, value in decoded["adaptation"].items():
        setattr(adaptation_engine, name, value)

    strategy_map = {strategy.strategy_id: strategy for strategy in strategies}
    if set(decoded["strategies"]) != set(strategy_map):
        raise PersistenceError("Persisted strategy set does not match this deployment")
    for strategy_id, strategy_state in decoded["strategies"].items():
        strategy_map[strategy_id].__dict__.clear()
        strategy_map[strategy_id].__dict__.update(strategy_state)

    latest_market_prices.clear()
    latest_market_prices.update(decoded["market"]["latest_market_prices"])
    market_history.clear()
    market_history.update(decoded["market"]["market_history"])
    recent_news.clear()
    recent_news.extend(decoded["market"]["recent_news"])

    validate_runtime_state(account, engine, bracket_manager)
    return {
        "last_session_date": decoded.get("last_session_date"),
        "last_vix_print": decoded["market"].get("last_vix_print"),
        "ledger_revision": int(decoded.get("ledger_revision", 0)),
    }


def validate_runtime_state(
    account: PaperTradingAccount,
    engine: ExecutionEngine,
    bracket_manager: DynamicBracketManager,
) -> None:
    """Reject a checkpoint that could resume with incoherent protection."""
    if not all(math.isfinite(value) for value in (account.cash, account.equity, account.buying_power)):
        raise PersistenceError("Account checkpoint contains non-finite balances")
    if account.cash < -account.initial_balance * account.leverage * 2:
        raise PersistenceError("Account checkpoint cash is outside the supported margin envelope")

    legal_working_states = {"ACCEPTED", "PARTIALLY_FILLED"}
    for order_id, order in engine.working_orders.items():
        if order.status.value not in legal_working_states:
            raise PersistenceError(
                f"Working order {order_id} has illegal state {order.status.value}"
            )
        if order.remaining_qty <= 0:
            raise PersistenceError(f"Working order {order_id} has no remaining quantity")

    active_statuses = {
        BracketStatus.PENDING_ENTRY,
        BracketStatus.ACTIVE,
        BracketStatus.TARGET_1_HIT,
    }
    for symbol, bracket_id in bracket_manager.symbol_to_bracket.items():
        bracket = bracket_manager.brackets.get(bracket_id)
        if bracket is None:
            raise PersistenceError(f"Active bracket mapping for {symbol} has no bracket")
        if bracket.status not in active_statuses:
            raise PersistenceError(
                f"Completed bracket {bracket_id} is still mapped active for {symbol}"
            )
        position = account.positions.get(symbol)
        if bracket.status != BracketStatus.PENDING_ENTRY:
            if position is None:
                raise PersistenceError(f"Active bracket {bracket_id} has no open position")
            if bracket.remaining_qty != position.shares:
                raise PersistenceError(
                    f"Bracket {bracket_id} quantity {bracket.remaining_qty} does not match "
                    f"position {position.shares}"
                )
            stop_order = engine.working_orders.get(bracket.stop_order_id or "")
            if stop_order is None or stop_order.remaining_qty != position.shares:
                raise PersistenceError(f"Position {symbol} is not fully protected by its stop")

    if account.status == AccountStatus.CLOSED and account.positions:
        raise PersistenceError("Closed account checkpoint contains open positions")
