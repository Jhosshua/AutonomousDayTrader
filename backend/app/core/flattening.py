# @steered SNARE-2 2026-09-30
"""backend/app/core/flattening.py
Automated 4-Phase Zero-Overnight Flattening State Machine and Market Clock Abstraction.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from backend.app.core.account import TradingArm
from backend.app.core.overnight_schedule import is_overnight
from backend.app.core.day_one_schedule import is_day_one
from backend.app.core.trading_windows import session_close

ET_TZ = ZoneInfo("America/New_York")


class FlatteningPhase(str, Enum):
    PRE_MARKET = "PRE_MARKET"                       # Before 09:30:00 ET
    NORMAL_TRADING = "NORMAL_TRADING"               # 09:30 ET until the session's entry lockout
    ENTRY_LOCKOUT = "ENTRY_LOCKOUT"                 # 15 minutes before close (Phase 1)
    ORDER_PURGE = "ORDER_PURGE"                     # 10 minutes before close (Phase 2)
    MANDATORY_LIQUIDATION = "MANDATORY_LIQUIDATION" # 5 minutes before close (Phase 3)
    ZERO_AUDIT = "ZERO_AUDIT"                       # 2 minutes before close (Phase 4)
    MARKET_CLOSED = "MARKET_CLOSED"                 # At the session close


class MarketClock:
    """
    Market Clock providing Eastern Time (America/New_York) awareness.
    Supports both live wall-clock operation and deterministic simulated time replay.
    """

    def __init__(self, simulated_time: Optional[datetime] = None) -> None:
        self._simulated_time: Optional[datetime] = None
        if simulated_time:
            self.set_simulated_time(simulated_time)

    def set_simulated_time(self, dt: datetime) -> None:
        """Set simulated market time for backtesting, dry-runs, and replay."""
        if dt.tzinfo is None:
            self._simulated_time = dt.replace(tzinfo=ET_TZ)
        else:
            self._simulated_time = dt.astimezone(ET_TZ)

    def clear_simulated_time(self) -> None:
        """Clear simulated time and return to real-time wall clock."""
        self._simulated_time = None

    def now(self) -> datetime:
        """Return current market time in US Eastern timezone."""
        if self._simulated_time is not None:
            return self._simulated_time
        return datetime.now(ET_TZ)

    def current_time_et(self) -> time:
        """Return current time-of-day in ET."""
        return self.now().time()


class FlatteningDirective(BaseModel):
    phase: FlatteningPhase
    timestamp: datetime
    action_required: str
    cancel_all_orders: bool = False
    liquidate_all_positions: bool = False
    lock_new_entries: bool = False
    run_audit: bool = False
    audit_passed: Optional[bool] = None
    unclosed_symbols: List[str] = Field(default_factory=list)


class FlatteningSchedule(BaseModel):
    market_open_time: time = time(9, 30, 0)
    phase1_lockout_time: time = time(15, 45, 0)
    phase2_purge_time: time = time(15, 50, 0)
    phase3_liquidation_time: time = time(15, 55, 0)
    phase4_audit_time: time = time(15, 58, 0)
    market_close_time: time = time(16, 0, 0)


class ZeroOvernightFlatteningEngine:
    """
    Automated 4-Phase Zero-Overnight Flattening State Machine.
    Eliminates overnight gap risk by executing phased closeout:
    - 15 minutes before close: Phase 1 Entry Lockout
    - 10 minutes before close: Phase 2 Working Order Purge
    - 5 minutes before close: Phase 3 Mandatory Market Liquidation
    - 2 minutes before close: Phase 4 Zero-Overnight Position Audit
    """

    def __init__(
        self,
        clock: Optional[MarketClock] = None,
        schedule: Optional[FlatteningSchedule] = None,
    ) -> None:
        self.clock: MarketClock = clock or MarketClock()
        self._base_schedule: FlatteningSchedule = schedule or FlatteningSchedule()
        self.schedule: FlatteningSchedule = self._base_schedule.model_copy(deep=True)
        self._schedule_date: Optional[date] = None
        self.current_phase: FlatteningPhase = FlatteningPhase.NORMAL_TRADING
        self.phase1_executed: bool = False
        self.phase2_executed: bool = False
        self.phase3_executed: bool = False
        self.phase4_executed: bool = False
        self.audit_passed: bool = False
        self.audit_retries: int = 0

    def _sync_session_schedule(self, session_date: date) -> None:
        """Move the end-of-day phases earlier when NYSE closes early.

        The phase offsets remain the same: entry lockout 15 minutes before close,
        order purge 10 minutes before, liquidation 5 minutes before, and audit
        2 minutes before. On regular sessions, the configured schedule is
        preserved exactly; early-close sessions shift those phases by the
        difference between the regular 4:00 PM close and that day's close.
        """
        if self._schedule_date == session_date:
            return
        close = session_close(session_date)
        if close == time(16, 0):
            self.schedule = self._base_schedule.model_copy(deep=True)
        else:
            delta_minutes = close.hour * 60 + close.minute - (16 * 60)

            def shift(value: time) -> time:
                return (datetime.combine(session_date, value) + timedelta(minutes=delta_minutes)).time()

            self.schedule = self._base_schedule.model_copy(update={
                "phase1_lockout_time": shift(self._base_schedule.phase1_lockout_time),
                "phase2_purge_time": shift(self._base_schedule.phase2_purge_time),
                "phase3_liquidation_time": shift(self._base_schedule.phase3_liquidation_time),
                "phase4_audit_time": shift(self._base_schedule.phase4_audit_time),
                "market_close_time": close,
            }, deep=True)
        self._schedule_date = session_date

    def check_time_tick(
        self,
        current_time_override: Optional[datetime] = None,
    ) -> Optional[FlatteningDirective]:
        """
        Evaluate time against flattening schedule on every bar or timer tick.
        Detects phase transitions and returns the mandatory execution directive.
        """
        if current_time_override:
            self.clock.set_simulated_time(current_time_override)

        now_dt = self.clock.now()
        self._sync_session_schedule(now_dt.astimezone(ET_TZ).date())
        t = now_dt.time()

        # Phase 4 audit runs during the final two minutes before the session close.
        if t >= self.schedule.phase4_audit_time and t < self.schedule.market_close_time:
            if not self.phase4_executed or not self.audit_passed:
                self.phase4_executed = True
                self.current_phase = FlatteningPhase.ZERO_AUDIT
                return FlatteningDirective(
                    phase=FlatteningPhase.ZERO_AUDIT,
                    timestamp=now_dt,
                    action_required="EXECUTE_PHASE_4_AUDIT",
                    lock_new_entries=True,
                    cancel_all_orders=True,
                    run_audit=True,
                )

        # Phase 3 mandatory liquidation runs from five until two minutes before close.
        elif t >= self.schedule.phase3_liquidation_time and t < self.schedule.phase4_audit_time:
            if not self.phase3_executed:
                self.phase3_executed = True
                self.current_phase = FlatteningPhase.MANDATORY_LIQUIDATION
                return self.execute_phase_3_liquidation()

        # Phase 2 working-order purge runs from ten until five minutes before close.
        elif t >= self.schedule.phase2_purge_time and t < self.schedule.phase3_liquidation_time:
            if not self.phase2_executed:
                self.phase2_executed = True
                self.current_phase = FlatteningPhase.ORDER_PURGE
                return self.execute_phase_2_purge()

        # Phase 1 entry lockout runs from fifteen until ten minutes before close.
        elif t >= self.schedule.phase1_lockout_time and t < self.schedule.phase2_purge_time:
            if not self.phase1_executed:
                self.phase1_executed = True
                self.current_phase = FlatteningPhase.ENTRY_LOCKOUT
                return self.execute_phase_1_lockout()

        # After the session close.
        elif t >= self.schedule.market_close_time:
            if self.current_phase != FlatteningPhase.MARKET_CLOSED:
                self.current_phase = FlatteningPhase.MARKET_CLOSED
                return FlatteningDirective(
                    phase=FlatteningPhase.MARKET_CLOSED,
                    timestamp=now_dt,
                    action_required="SESSION_CLOSED",
                    lock_new_entries=True,
                    cancel_all_orders=True,
                )

        # Pre-Market: Before 09:30:00 ET
        elif t < self.schedule.market_open_time:
            self.current_phase = FlatteningPhase.PRE_MARKET
            return None

        # Normal Trading: 09:30:00 - 15:44:59 ET
        else:
            self.current_phase = FlatteningPhase.NORMAL_TRADING
            return None

        return None

    def execute_phase_1_lockout(self) -> FlatteningDirective:
        """Phase 1: Entry Lockout."""
        self.phase1_executed = True
        self.current_phase = FlatteningPhase.ENTRY_LOCKOUT
        return FlatteningDirective(
            phase=FlatteningPhase.ENTRY_LOCKOUT,
            timestamp=self.clock.now(),
            action_required="LOCK_NEW_ENTRIES",
            lock_new_entries=True,
        )

    def execute_phase_2_purge(self) -> FlatteningDirective:
        """Phase 2: Working Order Purge."""
        self.phase2_executed = True
        self.current_phase = FlatteningPhase.ORDER_PURGE
        return FlatteningDirective(
            phase=FlatteningPhase.ORDER_PURGE,
            timestamp=self.clock.now(),
            action_required="PURGE_WORKING_ORDERS",
            lock_new_entries=True,
            cancel_all_orders=True,
        )

    def execute_phase_3_liquidation(self) -> FlatteningDirective:
        """Phase 3: Mandatory Market Liquidation."""
        self.phase3_executed = True
        self.current_phase = FlatteningPhase.MANDATORY_LIQUIDATION
        return FlatteningDirective(
            phase=FlatteningPhase.MANDATORY_LIQUIDATION,
            timestamp=self.clock.now(),
            action_required="LIQUIDATE_ALL_POSITIONS",
            lock_new_entries=True,
            cancel_all_orders=True,
            liquidate_all_positions=True,
        )

    def execute_phase_4_audit(
        self,
        open_positions: Dict[str, Any],
        working_orders: List[Any],
        day_one_protected: Optional[Callable[[str], bool]] = None,
    ) -> FlatteningDirective:
        """
        Phase 4 (two minutes before close): Zero-Overnight Position Audit.
        Verifies open INTRADAY positions count == 0 and INTRADAY working orders count == 0.
        Swing positions and swing working orders are strictly exempt.
        If intraday positions exist, issues emergency IOC market liquidation directive.
        If flat, certifies audit passed for session close.
        """
        self.phase4_executed = True
        self.current_phase = FlatteningPhase.ZERO_AUDIT
        now_dt = self.clock.now()

        # Filter out exempt swing positions and orders plus shares owned by dedicated controllers.
        intraday_positions = {
            sym: pos for sym, pos in open_positions.items()
            if getattr(pos, "arm", None) not in ("SWING", TradingArm.SWING)
            and getattr(pos, "strategy_id", "") != "swing_panic_dip"
            and not is_overnight(pos)
            and not (is_day_one(pos) and day_one_protected is not None and day_one_protected(sym))
        }
        intraday_working_orders = [
            order for order in working_orders
            if getattr(order, "arm", None) not in ("SWING", TradingArm.SWING)
            and getattr(order, "strategy_id", "") != "swing_panic_dip"
            and not is_overnight(order)
            and not (
                is_day_one(order)
                and day_one_protected is not None
                and day_one_protected(str(getattr(order, "symbol", "") or ""))
            )
        ]
        unclosed = list(intraday_positions.keys())

        if len(unclosed) == 0 and len(intraday_working_orders) == 0:
            self.audit_passed = True
            return FlatteningDirective(
                phase=FlatteningPhase.ZERO_AUDIT,
                timestamp=now_dt,
                action_required="AUDIT_PASSED_CLEAN_BOOK",
                lock_new_entries=True,
                cancel_all_orders=False,
                run_audit=True,
                audit_passed=True,
                unclosed_symbols=[],
            )
        else:
            self.audit_retries += 1
            self.audit_passed = False
            return FlatteningDirective(
                phase=FlatteningPhase.ZERO_AUDIT,
                timestamp=now_dt,
                action_required="AUDIT_FAILED_EMERGENCY_SWEEP",
                lock_new_entries=True,
                cancel_all_orders=True,
                liquidate_all_positions=True,
                run_audit=True,
                audit_passed=False,
                unclosed_symbols=unclosed,
            )

    def reset_for_new_session(self) -> None:
        """Reset phase execution flags for the next trading day."""
        self._schedule_date = None
        self._sync_session_schedule(self.clock.now().astimezone(ET_TZ).date())
        self.current_phase = FlatteningPhase.NORMAL_TRADING
        self.phase1_executed = False
        self.phase2_executed = False
        self.phase3_executed = False
        self.phase4_executed = False
        self.audit_passed = False
        self.audit_retries = 0

    def get_phase_at_time(self, t: time) -> FlatteningPhase:
        """Return the scheduled FlatteningPhase for any given ET time-of-day."""
        self._sync_session_schedule(self.clock.now().astimezone(ET_TZ).date())
        if t < self.schedule.market_open_time:
            return FlatteningPhase.PRE_MARKET
        elif t < self.schedule.phase1_lockout_time:
            return FlatteningPhase.NORMAL_TRADING
        elif t < self.schedule.phase2_purge_time:
            return FlatteningPhase.ENTRY_LOCKOUT
        elif t < self.schedule.phase3_liquidation_time:
            return FlatteningPhase.ORDER_PURGE
        elif t < self.schedule.phase4_audit_time:
            return FlatteningPhase.MANDATORY_LIQUIDATION
        elif t < self.schedule.market_close_time:
            return FlatteningPhase.ZERO_AUDIT
        else:
            return FlatteningPhase.MARKET_CLOSED
