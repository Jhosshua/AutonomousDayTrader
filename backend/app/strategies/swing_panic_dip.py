"""backend/app/strategies/swing_panic_dip.py
Swing Trading Strategy Engine: The "2-Day Panic Dip" (Connors RSI(2)).

Rules Implemented:
1. Rule 1 (Macro Floor): Today's daily close > 200-day Simple Moving Average (SMA).
2. Rule 2 (Market Leadership / Relative Strength): 60-day return >= QQQ return.
3. Rule 3 (Panic Trigger): 2-day Connors RSI (Wilder's RSI(2) on daily closes) < 10.0.
4. Rule 4 (Mandatory Earnings Veto):
   - 48-hour blackout: No entry if earnings report within 48 hours.
   - Holding exit: If holding position and earnings report tomorrow, sell at Market Open (09:30 ET).
5. Rule 5 (Entry Execution & Sizing):
   - 16:00 ET close qualification -> staged in SwingStagedOrderManager -> executed at 09:30 ET open.
   - Sizing: $25,000 notional per slot (floor(25000 / P_open) shares).
   - Hard cap: Maximum 2 concurrent swing positions.
6. Rule 6 (Emergency Stop-Loss):
   - Hard stop established immediately upon fill at P_fill - 2.5 * Daily ATR(14).
7. Rule 7 (Take-Profit & Time Exit):
   - Sell at next Market Open (09:30 ET) when ANY of the following occur:
     a) Prior daily close > 5-day SMA.
     b) Prior daily RSI(2) > 70.0.
     c) Held for 5 trading days (time stop).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import logging
import math
import threading
from typing import Any, Callable, Dict, List, Optional


from backend.app.core.account import PaperTradingAccount, Position, TradingArm
from backend.app.core.engine import ExecutionEngine, Order, OrderSide, OrderType
from backend.app.core.risk import InstitutionalRiskEngine
from backend.app.models.events import BarEvent
from backend.app.strategies.earnings_calendar import EarningsCalendar
from backend.app.strategies.swing_indicators import (
    DailyBarStore,
    calculate_daily_atr,
    calculate_rsi2,
    calculate_sma,
    evaluate_swing_exit,
    evaluate_swing_qualification,
    SwingExitResult,
    SwingQualificationResult,
)

log = logging.getLogger(__name__)


# Certified 5 swing stocks and benchmark
CERTIFIED_SWING_SYMBOLS: List[str] = ["LRCX", "KLAC", "MU", "AMD", "GS"]
SWING_BENCHMARK: str = "QQQ"


@dataclass
class StagedSwingOrder:
    """Staged order stored outside working_orders overnight to survive 15:58 EOD flattening."""
    order_id: str
    symbol: str
    action: str                       # "BUY" or "SELL"
    target_notional: float            # $25,000 for BUY
    shares: Optional[int]             # Known for SELL, calculated at 09:30 open for BUY
    daily_atr: float                  # 14-day Daily ATR at 16:00 qualification close
    signal_date: date                 # Date when signal qualified
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_loss_price: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "action": self.action,
            "target_notional": self.target_notional,
            "shares": self.shares,
            "daily_atr": round(self.daily_atr, 4),
            "signal_date": self.signal_date.isoformat(),
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "stop_loss_price": round(self.stop_loss_price, 2) if self.stop_loss_price is not None else None,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> StagedSwingOrder:
        sig_d = d["signal_date"]
        if isinstance(sig_d, str):
            sig_d = date.fromisoformat(sig_d)
        creat_d = d.get("created_at")
        if isinstance(creat_d, str):
            creat_d = datetime.fromisoformat(creat_d)
        elif creat_d is None:
            creat_d = datetime.now(timezone.utc)
        return cls(
            order_id=d["order_id"],
            symbol=d["symbol"],
            action=d["action"],
            target_notional=float(d.get("target_notional", 0.0)),
            shares=d.get("shares"),
            daily_atr=float(d.get("daily_atr", 0.0)),
            signal_date=sig_d,
            reason=d.get("reason", ""),
            created_at=creat_d,
            stop_loss_price=d.get("stop_loss_price"),
        )



class SwingStagedOrderManager:
    """Manages overnight order staging for next-day 09:30 open execution."""

    def __init__(self) -> None:
        self._staged: Dict[str, StagedSwingOrder] = {}

    def stage_buy(
        self,
        symbol: str,
        target_notional: float,
        daily_atr: float,
        signal_date: date,
        reason: str,
    ) -> StagedSwingOrder:
        order_id = f"stg_buy_{symbol.upper()}_{signal_date.isoformat()}"
        order = StagedSwingOrder(
            order_id=order_id,
            symbol=symbol.upper(),
            action="BUY",
            target_notional=target_notional,
            shares=None,
            daily_atr=daily_atr,
            signal_date=signal_date,
            reason=reason,
        )
        self._staged[order_id] = order
        log.info(f"Staged swing BUY order: {order_id} ({symbol} ${target_notional:,.2f}) reason={reason}")
        return order

    def stage_sell(
        self,
        symbol: str,
        shares: int,
        signal_date: date,
        reason: str,
    ) -> StagedSwingOrder:
        order_id = f"stg_sell_{symbol.upper()}_{signal_date.isoformat()}"
        order = StagedSwingOrder(
            order_id=order_id,
            symbol=symbol.upper(),
            action="SELL",
            target_notional=0.0,
            shares=shares,
            daily_atr=0.0,
            signal_date=signal_date,
            reason=reason,
        )
        self._staged[order_id] = order
        log.info(f"Staged swing SELL order: {order_id} ({symbol} {shares} shares) reason={reason}")
        return order

    def get_staged_orders(self) -> List[StagedSwingOrder]:
        return list(self._staged.values())

    def get_staged_entries(self) -> List[StagedSwingOrder]:
        return [o for o in self._staged.values() if o.action == "BUY"]

    def get_staged_exits(self) -> List[StagedSwingOrder]:
        return [o for o in self._staged.values() if o.action == "SELL"]

    def is_staged_for_entry(self, symbol: str) -> bool:
        return any(o.action == "BUY" and o.symbol == symbol.upper() for o in self._staged.values())

    def is_staged_for_exit(self, symbol: str) -> bool:
        return any(o.action == "SELL" and o.symbol == symbol.upper() for o in self._staged.values())

    def remove_staged_order(self, order_id: str) -> Optional[StagedSwingOrder]:
        return self._staged.pop(order_id, None)

    def remove_for_symbol(self, symbol: str) -> None:
        sym = symbol.upper()
        for oid in [oid for oid, o in self._staged.items() if o.symbol == sym]:
            self._staged.pop(oid, None)

    def clear(self) -> None:
        self._staged.clear()

    def load_staged_orders(self, orders: List[StagedSwingOrder]) -> None:
        self._staged.clear()
        for o in orders:
            self._staged[o.order_id] = o



class SwingStrategyEngine:
    """The 2-Day Panic Dip Swing Strategy Engine.
    
    Coordinates:
    - 16:00 ET close scans for qualification and exits.
    - 09:30 ET open execution of staged exits then entries.
    - Hard 2.5x ATR emergency stop monitoring intraday.
    - Sizing: $25,000 per slot, max 2 concurrent swing positions.
    - Tagging with arm=TradingArm.SWING, strategy_id="swing_panic_dip".
    """

    def __init__(
        self,
        account: PaperTradingAccount,
        execution_engine: ExecutionEngine,
        risk_engine: Optional[InstitutionalRiskEngine] = None,
        bar_store: Optional[DailyBarStore] = None,
        calendar: Optional[EarningsCalendar] = None,
        staged_manager: Optional[SwingStagedOrderManager] = None,
        symbols: Optional[List[str]] = None,
        benchmark: str = SWING_BENCHMARK,
        slot_notional: float = 25000.0,
        max_concurrent_positions: int = 2,
        stop_atr_multiplier: float = 2.5,
        time_stop_days: int = 5,
        reserve_symbol_cb: Optional[Callable[[str], None]] = None,
        release_symbol_cb: Optional[Callable[[str], None]] = None,
        is_reserved_cb: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.account: PaperTradingAccount = account
        self.execution_engine: ExecutionEngine = execution_engine
        self.risk_engine: Optional[InstitutionalRiskEngine] = risk_engine
        self.bar_store: DailyBarStore = bar_store or DailyBarStore()
        self.calendar: EarningsCalendar = calendar or EarningsCalendar()
        self.staged_manager: SwingStagedOrderManager = staged_manager or SwingStagedOrderManager()
        self.symbols: List[str] = [s.upper() for s in (symbols or CERTIFIED_SWING_SYMBOLS)]
        self.benchmark: str = benchmark.upper()
        self.slot_notional: float = slot_notional
        self.max_concurrent_positions: int = max_concurrent_positions
        self.stop_atr_multiplier: float = stop_atr_multiplier
        self.time_stop_days: int = time_stop_days

        # Symbol reservation callbacks for AMD mutual exclusion with intraday trading
        self.reserve_symbol_cb: Optional[Callable[[str], None]] = reserve_symbol_cb
        self.release_symbol_cb: Optional[Callable[[str], None]] = release_symbol_cb
        self.is_reserved_cb: Optional[Callable[[str], bool]] = is_reserved_cb

        # Audit history of evaluations and trades
        self.audit_log: List[Dict[str, Any]] = []

        # Concurrency mutex lock for order execution
        self._execution_lock = threading.RLock()


    def get_active_swing_positions(self) -> Dict[str, Position]:
        """Return all active positions owned by the swing trading engine."""
        return {
            sym: pos
            for sym, pos in self.account.positions.items()
            if getattr(pos, "arm", None) == TradingArm.SWING
            or getattr(pos, "strategy_id", "") == "swing_panic_dip"
        }

    def evaluate_market_close(self, session_date: date) -> Dict[str, Any]:
        """Perform 16:00 ET close evaluation on finalized daily bars.
        
        Zero lookahead guarantee: Operates only on bars with date <= session_date.
        
        Step 1: Evaluate exits for existing active swing positions.
        Step 2: Evaluate candidates for new entries if slots are available.
        """
        active_positions = self.get_active_swing_positions()
        staged_exits: List[StagedSwingOrder] = []
        staged_entries: List[StagedSwingOrder] = []
        exit_evaluations: Dict[str, SwingExitResult] = {}
        qualification_evaluations: Dict[str, SwingQualificationResult] = {}

        # -------------------------------------------------------------
        # STEP 1: EVALUATE EXITS FOR ACTIVE POSITIONS
        # -------------------------------------------------------------
        for sym, pos in active_positions.items():
            stock_bars = self.bar_store.get_bars(sym, as_of=session_date)
            earnings_tomorrow = self.calendar.has_earnings_tomorrow(sym, session_date)
            holding_days = getattr(pos, "holding_days", 0)

            exit_result = evaluate_swing_exit(
                symbol=sym,
                stock_bars=stock_bars,
                holding_days=holding_days,
                earnings_tomorrow=earnings_tomorrow,
            )
            exit_evaluations[sym] = exit_result

            if exit_result.should_exit:
                staged = self.staged_manager.stage_sell(
                    symbol=sym,
                    shares=pos.shares,
                    signal_date=session_date,
                    reason=exit_result.primary_exit_reason or "SWING_EXIT_TRIGGERED",
                )
                staged_exits.append(staged)

        # -------------------------------------------------------------
        # STEP 2: EVALUATE CANDIDATE ENTRIES
        # -------------------------------------------------------------
        # Exiting positions will be sold at tomorrow's open, freeing their slots
        exiting_symbols = {e.symbol for e in staged_exits} | {e.symbol for e in self.staged_manager.get_staged_exits()}
        surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
        existing_staged_entries = self.staged_manager.get_staged_entries()
        existing_staged_symbols = {e.symbol for e in existing_staged_entries}

        # Deduct already staged entries from available slots to enforce strict idempotency
        available_slots = self.max_concurrent_positions - len(surviving_positions) - len(existing_staged_symbols)
        available_slots = max(0, available_slots)

        log.info(
            f"16:00 Swing Close Scan: {len(active_positions)} active, {len(exiting_symbols)} exiting, "
            f"{len(existing_staged_symbols)} already staged, {available_slots} available slots"
        )

        if available_slots > 0:
            qqq_bars = self.bar_store.get_bars(self.benchmark, as_of=session_date)

            for sym in self.symbols:
                if available_slots <= 0:
                    break

                # Skip if already held, scheduled to exit at next open, or already staged for entry
                if sym in active_positions or sym in exiting_symbols or sym in existing_staged_symbols:
                    continue

                # Skip if already staged for entry in manager
                if self.staged_manager.is_staged_for_entry(sym):
                    continue

                # Mutual exclusion: check if intraday has open position in this symbol
                intraday_pos = self.account.positions.get(sym)
                if intraday_pos and getattr(intraday_pos, "arm", None) == TradingArm.INTRADAY:
                    log.warning(f"Swing candidate {sym} skipped: held by Intraday trading arm")
                    continue

                # Mutual exclusion: check if intraday working orders exist
                has_intraday_order = any(
                    o.symbol == sym and getattr(o, "arm", None) == TradingArm.INTRADAY
                    for o in self.execution_engine.working_orders.values()
                )
                if has_intraday_order:
                    log.warning(f"Swing candidate {sym} skipped: working Intraday order exists")
                    continue

                # Fetch closed historical daily bars
                stock_bars = self.bar_store.get_bars(sym, as_of=session_date)
                earnings_blackout = self.calendar.is_blackout_active(sym, session_date, horizon_hours=48.0)

                qual_res = evaluate_swing_qualification(
                    symbol=sym,
                    stock_bars=stock_bars,
                    qqq_bars=qqq_bars,
                    earnings_blackout=earnings_blackout,
                )
                qualification_evaluations[sym] = qual_res

                if qual_res.qualified:
                    staged_buy = self.staged_manager.stage_buy(
                        symbol=sym,
                        target_notional=self.slot_notional,
                        daily_atr=qual_res.daily_atr_14,
                        signal_date=session_date,
                        reason=f"PANIC_DIP_RSI2_{qual_res.rsi_2:.2f}_SMA200_{qual_res.sma_200:.2f}",
                    )
                    staged_entries.append(staged_buy)
                    existing_staged_symbols.add(sym)
                    available_slots -= 1

                    # Lock symbol for swing so intraday engine cannot enter tomorrow morning
                    if self.reserve_symbol_cb:
                        self.reserve_symbol_cb(sym)

        audit_entry = {
            "session_date": session_date.isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "staged_exits": [e.to_dict() for e in staged_exits],
            "staged_entries": [e.to_dict() for e in staged_entries],
            "active_swing_positions": list(active_positions.keys()),
        }
        self.audit_log.append(audit_entry)
        return audit_entry

    def execute_market_open(
        self,
        open_prices: Dict[str, float],
        open_time: datetime,
        bar_volumes: Optional[Dict[str, int]] = None,
        bar_highs: Optional[Dict[str, float]] = None,
        bar_lows: Optional[Dict[str, float]] = None,
        apply_slippage: bool = True,
    ) -> Dict[str, Any]:
        """Execute staged swing orders at 09:30 ET market open.
        
        Execution Protocol:
        1. Process exit orders first to liquidate shares and release buying power.
        2. Process entry orders next:
           - Sizing: floor($25,000 / P_open) shares.
           - Enforce maximum 2 concurrent swing positions.
           - Establish hard emergency stop at P_fill - 2.5 * Daily ATR(14).
        """
        with self._execution_lock:
            exits_executed: List[Dict[str, Any]] = []
            entries_executed: List[Dict[str, Any]] = []
            errors: List[str] = []

            # -------------------------------------------------------------
            # 1. PROCESS EXITS FIRST
            # -------------------------------------------------------------
            staged_exits = self.staged_manager.get_staged_exits()
            for exit_order in staged_exits:
                sym = exit_order.symbol.upper()
                pos = self.account.positions.get(sym)
                if not pos or pos.shares <= 0:
                    self.staged_manager.remove_staged_order(exit_order.order_id)
                    continue

                open_price = open_prices.get(sym)
                if open_price is None or open_price <= 0.0:
                    continue  # Await this symbol's open bar

                shares = pos.shares
                try:
                    order_obj = self.execution_engine.create_order(
                        symbol=sym,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        qty=shares,
                        estimated_price=open_price,
                        strategy_id="swing_panic_dip",
                        arm=TradingArm.SWING,
                    )
                    self.execution_engine.submit_order(order_obj.id)

                    # Realistic exit slippage (adverse downward on sells)
                    if apply_slippage:
                        raw_slippage = (
                            self.execution_engine.calculate_slippage(
                                order_obj,
                                market_price=open_price,
                                bar_volume=bar_volumes.get(sym, 10000) if bar_volumes else 10000,
                                bar_high=bar_highs.get(sym) if bar_highs else None,
                                bar_low=bar_lows.get(sym) if bar_lows else None,
                            )
                            if hasattr(self.execution_engine, "calculate_slippage")
                            else max(0.01, round(open_price * 0.0003, 4))
                        )
                        slippage = max(0.01, round(raw_slippage, 4))
                        fill_price = round(open_price - slippage, 2)
                    else:
                        slippage = 0.0
                        fill_price = open_price

                    fill = self.execution_engine._execute_fill(
                        order=order_obj,
                        qty=shares,
                        price=fill_price,
                        slippage=slippage,
                        timestamp=open_time,
                    )
                    exits_executed.append({
                        "symbol": sym,
                        "shares": shares,
                        "price": fill_price,
                        "slippage": slippage,
                        "realized_pnl": fill.realized_pnl,
                        "reason": exit_order.reason,
                    })
                    log.info(f"Executed swing EXIT for {sym}: {shares} shares @ ${fill_price:,.2f} ({exit_order.reason})")
                except Exception as e:
                    log.error(f"Error executing swing exit for {sym}: {e}")
                    errors.append(f"Exit error for {sym}: {e}")
                finally:
                    self.staged_manager.remove_staged_order(exit_order.order_id)
                    rem_pos = self.account.positions.get(sym)
                    if (not rem_pos or rem_pos.shares <= 0) and self.release_symbol_cb:
                        self.release_symbol_cb(sym)

            # -------------------------------------------------------------
            # 2. PROCESS ENTRIES NEXT
            # -------------------------------------------------------------
            staged_entries = self.staged_manager.get_staged_entries()
            for entry_order in staged_entries:
                sym = entry_order.symbol.upper()
                active_count = len(self.get_active_swing_positions())
                pending_exits = self.staged_manager.get_staged_exits()

                # Defect 2: If active_count >= max_concurrent_positions, check for pending staged exits
                if active_count >= self.max_concurrent_positions:
                    if len(pending_exits) > 0:
                        log.info(
                            f"Concurrency cap reached ({active_count}/{self.max_concurrent_positions}) on {sym}, "
                            f"but {len(pending_exits)} staged exit(s) still pending. Retaining/deferring staged entry."
                        )
                        continue
                    log.warning(
                        f"Concurrency cap reached ({active_count}/{self.max_concurrent_positions}): "
                        f"Cannot enter swing trade on {sym}"
                    )
                    self.staged_manager.remove_staged_order(entry_order.order_id)
                    if self.release_symbol_cb:
                        self.release_symbol_cb(sym)
                    continue

                open_price = open_prices.get(sym)
                if not open_price or open_price <= 0.0:
                    errors.append(f"Missing open price for {sym}; awaiting open bar")
                    continue  # Await this symbol's open bar; do not delete staged order

                # Size strictly using Rule 5 slot notional / open price
                qty = int(math.floor(self.slot_notional / open_price))
                if qty <= 0:
                    log.warning(f"Calculated 0 shares for {sym} at price ${open_price:,.2f}; skipping")
                    self.staged_manager.remove_staged_order(entry_order.order_id)
                    if self.release_symbol_cb:
                        self.release_symbol_cb(sym)
                    continue

                # Realistic entry slippage (adverse upward on buys)
                if apply_slippage:
                    raw_slippage = (
                        self.execution_engine.calculate_slippage(
                            Order(
                                id="tmp_slip", client_order_id="tmp_slip", symbol=sym,
                                side=OrderSide.BUY, order_type=OrderType.MARKET, qty=qty
                            ),
                            market_price=open_price,
                            bar_volume=bar_volumes.get(sym, 10000) if bar_volumes else 10000,
                            bar_high=bar_highs.get(sym) if bar_highs else None,
                            bar_low=bar_lows.get(sym) if bar_lows else None,
                        )
                        if hasattr(self.execution_engine, "calculate_slippage")
                        else max(0.01, round(open_price * 0.0003, 4))
                    )
                    slippage = max(0.01, round(raw_slippage, 4))
                    fill_price = round(open_price + slippage, 2)
                else:
                    slippage = 0.0
                    fill_price = open_price

                # Rule 6: Emergency Stop Price = P_fill - 2.5 * Daily_ATR(14)
                stop_distance = self.stop_atr_multiplier * entry_order.daily_atr
                stop_price = round(fill_price - stop_distance, 2)
                try:
                    # Pre-trade risk validation if risk engine is present
                    if self.risk_engine:
                        active_sec = set(self.risk_engine.symbol_sectors.get(s, "Other") for s in self.account.positions)
                        risk_check = self.risk_engine.evaluate_order_request(
                            symbol=sym,
                            side="BUY",
                            requested_qty=qty,
                            entry_price=fill_price,
                            stop_price=stop_price,
                            account_equity=self.account.equity,
                            buying_power=self.account.buying_power,
                            active_positions_count=len(self.account.positions),
                            active_symbols=set(self.account.positions.keys()),
                            active_sectors=active_sec,
                            arm=TradingArm.SWING,
                            strategy_id="swing_panic_dip",
                            active_swing_positions_count=active_count,
                        )
                        if not risk_check.approved:
                            log.warning(f"Risk check rejected swing entry for {sym}: {risk_check.reason}")
                            errors.append(f"Risk rejection for {sym}: {risk_check.reason}")
                            self.staged_manager.remove_staged_order(entry_order.order_id)
                            if self.release_symbol_cb:
                                self.release_symbol_cb(sym)
                            continue
                        qty = risk_check.authorized_qty

                    # Create, submit, and execute fill
                    order_obj = self.execution_engine.create_order(
                        symbol=sym,
                        side=OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        qty=qty,
                        stop_price=stop_price,
                        estimated_price=fill_price,
                        strategy_id="swing_panic_dip",
                        arm=TradingArm.SWING,
                    )
                    self.execution_engine.submit_order(order_obj.id)
                    fill = self.execution_engine._execute_fill(
                        order=order_obj,
                        qty=qty,
                        price=fill_price,
                        slippage=slippage,
                        timestamp=open_time,
                    )

                    # Rule 6: Stop Anchored strictly to realized fill.price
                    realized_stop_price = round(fill.price - stop_distance, 2)

                    # Explicitly populate swing metadata on position
                    pos = self.account.positions.get(sym)
                    if pos:
                        pos.arm = TradingArm.SWING
                        pos.strategy_id = "swing_panic_dip"
                        pos.stop_loss_price = realized_stop_price
                        pos.entry_atr = entry_order.daily_atr
                        pos.entry_date = open_time.date() if isinstance(open_time, datetime) else open_time
                        pos.holding_days = 1  # Day 1 of the swing trade upon fill

                    if self.reserve_symbol_cb:
                        self.reserve_symbol_cb(sym)

                    entries_executed.append({
                        "symbol": sym,
                        "shares": qty,
                        "price": open_price,
                        "fill_price": fill.price,
                        "slippage": slippage,
                        "notional": round(qty * fill.price, 2),
                        "stop_loss_price": realized_stop_price,
                        "daily_atr": entry_order.daily_atr,
                    })
                    log.info(
                        f"Executed swing ENTRY for {sym}: {qty} shares @ ${fill.price:,.2f} "
                        f"(stop=${realized_stop_price:,.2f}, ATR=${entry_order.daily_atr:.2f}, slippage=${slippage:.4f})"
                    )
                except Exception as e:
                    log.error(f"Error executing swing entry for {sym}: {e}")
                    errors.append(f"Entry error for {sym}: {e}")
                finally:
                    self.staged_manager.remove_staged_order(entry_order.order_id)

            return {
                "exits": exits_executed,
                "entries": entries_executed,
                "errors": errors,
                "timestamp": open_time.isoformat(),
            }


    def check_intraday_emergency_stops(
        self,
        current_prices: Dict[str, float],
        timestamp: datetime,
    ) -> List[Dict[str, Any]]:
        """Continuously check active swing positions against their 2.5x ATR emergency stop line.
        
        If current market price <= stop_loss_price, immediately execute emergency market exit.
        """
        stops_triggered: List[Dict[str, Any]] = []
        active_positions = self.get_active_swing_positions()

        for sym, pos in list(active_positions.items()):
            stop_price = getattr(pos, "stop_loss_price", None)
            if stop_price is None or stop_price <= 0.0:
                continue

            current_p = current_prices.get(sym.upper())
            if current_p is None or current_p <= 0.0:
                continue

            # Update mark-to-market
            pos.update_market_price(current_p)

            # Emergency Stop Trigger
            if current_p <= stop_price:
                log.critical(
                    f"SWING EMERGENCY STOP TRIGGERED: {sym} price ${current_p:,.2f} <= stop ${stop_price:,.2f} "
                    f"(ATR entry stop breached)"
                )
                try:
                    order_obj = self.execution_engine.create_order(
                        symbol=sym,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        qty=pos.shares,
                        estimated_price=current_p,
                        strategy_id="swing_panic_dip",
                        arm=TradingArm.SWING,
                    )
                    spread_half = max(0.005, round(current_p * 0.0002, 4))
                    raw_slippage = (
                        self.execution_engine.calculate_slippage(
                            order_obj,
                            market_price=current_p,
                            bar_volume=10000,
                        )
                        if hasattr(self.execution_engine, "calculate_slippage")
                        else max(0.01, round(current_p * 0.0004, 4))
                    )
                    slippage = max(0.01, round(spread_half + raw_slippage, 4))
                    fill_price = round(current_p - slippage, 2)
                    fill = self.execution_engine._execute_fill(
                        order=order_obj,
                        qty=pos.shares,
                        price=fill_price,
                        slippage=slippage,
                        timestamp=timestamp,
                    )
                    stops_triggered.append({
                        "symbol": sym,
                        "shares": pos.shares,
                        "fill_price": fill_price,
                        "stop_price": stop_price,
                        "realized_pnl": fill.realized_pnl,
                        "timestamp": timestamp.isoformat(),
                    })
                except Exception as e:
                    log.error(f"Error executing emergency stop for {sym}: {e}")
                finally:
                    if self.release_symbol_cb:
                        self.release_symbol_cb(sym)

        return stops_triggered

    def on_bar(self, bar: BarEvent) -> Optional[Dict[str, Any]]:
        """Handle incoming 1m BarEvent: updates swing mark-to-market and checks emergency stop."""
        sym = bar.symbol.upper()
        active_positions = self.get_active_swing_positions()
        pos = active_positions.get(sym)
        if not pos:
            return None

        # Check if bar low breached emergency stop
        stop_price = getattr(pos, "stop_loss_price", None)
        if stop_price is not None and bar.low <= stop_price:
            res = self.check_intraday_emergency_stops({sym: bar.low}, bar.timestamp)
            return res[0] if res else None

        # Otherwise update market price
        pos.update_market_price(bar.close)
        return None

    def get_candidate_status(self, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        """Return candidate status telemetry for all 5 certified stocks.
        
        Feeds the Next.js Obsidian Dark Candidate Watchlist table in Milestone M9C.
        """
        results: List[Dict[str, Any]] = []
        eval_date = as_of or date.today()
        qqq_bars = self.bar_store.get_bars(self.benchmark, as_of=eval_date)

        for sym in self.symbols:
            stock_bars = self.bar_store.get_bars(sym, as_of=eval_date)
            earnings_blackout = self.calendar.is_blackout_active(sym, eval_date, horizon_hours=48.0)
            next_earnings = self.calendar.get_next_earnings(sym, eval_date)

            qual = evaluate_swing_qualification(
                symbol=sym,
                stock_bars=stock_bars,
                qqq_bars=qqq_bars,
                earnings_blackout=earnings_blackout,
            )

            is_held = sym in self.get_active_swing_positions()
            is_staged = self.staged_manager.is_staged_for_entry(sym)

            # Derive display status
            if is_held:
                status = "ACTIVE"
            elif is_staged:
                status = "STAGED"
            elif qual.qualified:
                status = "QUALIFIED"
            elif earnings_blackout:
                status = "BLOCKED"
            elif qual.rule_1_macro_floor and qual.rule_2_relative_strength:
                status = "WATCHING"
            else:
                status = "INELIGIBLE"

            results.append({
                "symbol": sym,
                "date": qual.date.isoformat(),
                "price": qual.close,
                "close": qual.close,
                "sma_200": qual.sma_200,
                "sma_200_pass": qual.rule_1_macro_floor,
                "above_200_sma": qual.rule_1_macro_floor,
                "rs_stock_60d": round(qual.rs_stock_60d * 100.0, 2),
                "rs_qqq_60d": round(qual.rs_qqq_60d * 100.0, 2),
                "rs_60d_stock": round(qual.rs_stock_60d * 100.0, 2),
                "rs_60d_qqq": round(qual.rs_qqq_60d * 100.0, 2),
                "relative_strength_ok": qual.rule_2_relative_strength,
                "rs_pass": qual.rule_2_relative_strength,
                "rsi_2": qual.rsi_2,
                "rsi_pass": qual.rule_3_panic_dip,
                "panic_trigger": qual.rule_3_panic_dip,
                "earnings_blackout": earnings_blackout,
                "earnings_date": next_earnings.report_date.isoformat() if next_earnings else None,
                "next_earnings_date": next_earnings.report_date.isoformat() if next_earnings else None,
                "daily_atr_14": qual.daily_atr_14,
                "atr_14": qual.daily_atr_14,
                "qualified": qual.qualified,
                "is_held": is_held,
                "is_staged": is_staged,
                "status": status,
                "rejection_reasons": qual.rejection_reasons,
            })

        return results

    def stage_manual_exit_next_open(self, symbol: str) -> Optional[StagedSwingOrder]:
        """Stage a manual operator exit at next 09:30 ET market open."""
        sym = symbol.upper()
        active = self.get_active_swing_positions()
        pos = active.get(sym)
        if not pos or pos.shares <= 0:
            log.warning(f"Cannot stage exit for {sym}: no active swing position found")
            return None
        today = date.today()
        staged = self.staged_manager.stage_sell(
            symbol=sym,
            shares=pos.shares,
            signal_date=today,
            reason="OPERATOR_MANUAL_EXIT_AT_OPEN",
        )
        log.info(f"Operator staged manual exit for swing position {sym} at next open")
        return staged

    def execute_immediate_exit(
        self,
        symbol: str,
        current_price: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Immediately execute an emergency market exit for a swing position."""
        sym = symbol.upper()
        active = self.get_active_swing_positions()
        pos = active.get(sym)
        if not pos or pos.shares <= 0:
            log.warning(f"Cannot execute immediate exit for {sym}: no active swing position found")
            return None

        now_dt = timestamp or datetime.now(timezone.utc)
        exec_price = current_price or pos.market_price or pos.avg_entry_price
        if exec_price <= 0.0:
            exec_price = 100.0

        try:
            order_obj = self.execution_engine.create_order(
                symbol=sym,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                qty=pos.shares,
                estimated_price=exec_price,
                strategy_id="swing_panic_dip",
                arm=TradingArm.SWING,
            )
            self.execution_engine.submit_order(order_obj.id)
            spread_half = max(0.005, round(exec_price * 0.0002, 4))
            raw_slippage = (
                self.execution_engine.calculate_slippage(
                    order_obj,
                    market_price=exec_price,
                    bar_volume=10000,
                )
                if hasattr(self.execution_engine, "calculate_slippage")
                else max(0.01, round(exec_price * 0.0003, 4))
            )
            slippage = max(0.01, round(spread_half + raw_slippage, 4))
            fill_price = round(exec_price - slippage, 2)
            fill = self.execution_engine._execute_fill(
                order=order_obj,
                qty=pos.shares,
                price=fill_price,
                slippage=slippage,
                timestamp=now_dt,
            )
            self.staged_manager.remove_for_symbol(sym)
            if self.release_symbol_cb:
                self.release_symbol_cb(sym)
            log.info(
                f"Operator executed IMMEDIATE exit for swing position {sym}: "
                f"{pos.shares} shares @ ${fill_price:,.2f}"
            )
            return {
                "symbol": sym,
                "shares": pos.shares,
                "fill_price": fill_price,
                "slippage": slippage,
                "realized_pnl": fill.realized_pnl,
                "timestamp": now_dt.isoformat(),
            }
        except Exception as e:
            log.error(f"Error executing immediate exit for {sym}: {e}")
            return None

    def tighten_stop(self, symbol: str, new_stop: float) -> bool:
        """Adjust the emergency stop price for an active swing position."""
        sym = symbol.upper()
        active = self.get_active_swing_positions()
        pos = active.get(sym)
        if not pos:
            log.warning(f"Cannot tighten stop for {sym}: no active swing position found")
            return False
        if new_stop <= 0:
            log.warning(f"Cannot tighten stop for {sym}: invalid stop price {new_stop}")
            return False
        if pos.market_price > 0 and new_stop >= pos.market_price:
            log.warning(f"Cannot tighten stop for {sym}: stop {new_stop} >= market price {pos.market_price}")
            return False
        old_stop = getattr(pos, "stop_loss_price", None)
        pos.stop_loss_price = round(new_stop, 2)
        log.info(f"Tightened swing stop for {sym}: {old_stop} -> {pos.stop_loss_price}")
        return True

    def to_ui_dict(self) -> Dict[str, Any]:
        """Serialize complete swing engine state for real-time WebSocket and Next.js UI."""
        active_positions = self.get_active_swing_positions()
        today = date.today()
        positions_list: List[Dict[str, Any]] = []

        for sym, pos in active_positions.items():
            stock_bars = self.bar_store.get_bars(sym, as_of=today)
            closes = [b.close for b in stock_bars] if stock_bars else []
            sma_5 = calculate_sma(closes, 5) if len(closes) >= 5 else 0.0
            rsi_2 = calculate_rsi2(closes) if len(closes) >= 3 else 50.0
            earnings_tomorrow = self.calendar.has_earnings_tomorrow(sym, today)
            holding_days = getattr(pos, "holding_days", 0)
            exit_eval = evaluate_swing_exit(sym, stock_bars, holding_days, earnings_tomorrow)

            stop_price = getattr(pos, "stop_loss_price", None) or 0.0
            entry_atr = getattr(pos, "entry_atr", None) or (calculate_daily_atr(stock_bars) if stock_bars else 0.0)
            stop_dist = round(pos.market_price - stop_price, 2) if stop_price > 0 else 0.0
            stop_pct = (
                round((stop_dist / pos.market_price) * 100.0, 2)
                if pos.market_price > 0 and stop_dist > 0
                else 0.0
            )

            entry_d_str = ""
            if getattr(pos, "entry_date", None):
                entry_d_str = pos.entry_date.isoformat()
            elif hasattr(pos, "opened_at") and pos.opened_at:
                entry_d_str = pos.opened_at.date().isoformat()

            positions_list.append({
                "symbol": sym,
                "side": "LONG",
                "shares": pos.shares,
                "entry_price": pos.avg_entry_price,
                "market_price": pos.market_price,
                "market_value": pos.market_value,
                "unrealized_pnl": pos.unrealized_pnl,
                "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                "stop_loss": stop_price,
                "stop_loss_price": stop_price,
                "atr_14": entry_atr,
                "entry_atr": entry_atr,
                "atr_stop_distance": stop_dist,
                "atr_stop_pct": stop_pct,
                "entry_date": entry_d_str,
                "holding_days": holding_days,
                "max_holding_days": self.time_stop_days,
                "holding_progress": f"Day {min(max(holding_days, 1), self.time_stop_days)} of {self.time_stop_days}",
                "sma_5": round(sma_5, 2),
                "rsi_2": round(rsi_2, 2),
                "exit_triggers": {
                    "sma_5_cross": exit_eval.exit_5_sma,
                    "rsi_70_cross": exit_eval.exit_rsi2_overbought,
                    "time_stop_day_5": exit_eval.exit_time_stop,
                    "earnings_tomorrow": exit_eval.exit_earnings,
                },
                "staged_exit_at_open": self.staged_manager.is_staged_for_exit(sym),
            })

        active_count = len(active_positions)
        status = (
            "ACTIVE"
            if active_count > 0
            else ("SCANNING" if self.staged_manager.get_staged_orders() else "STANDBY")
        )

        return {
            "status": status,
            "strategy_name": "2-Day Panic Dip (Connors RSI-2)",
            "allocated_capital": 50000.0,
            "slot_notional": self.slot_notional,
            "max_slots": self.max_concurrent_positions,
            "active_slots_used": active_count,
            "available_slots": max(0, self.max_concurrent_positions - active_count),
            "flattening_exempt": True,
            "candidates": self.get_candidate_status(as_of=today),
            "positions": positions_list,
            "last_scan_time": self.audit_log[-1]["timestamp"] if self.audit_log else None,
        }

    def reset(self) -> None:
        """Reset engine state for test isolation."""
        self.staged_manager.clear()
        self.audit_log.clear()
