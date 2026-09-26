"""Causal signal states for the supplied TSLA/CDE asymmetric execution plan.

Order lifecycle is owned by core.tri_execution. All mutable state here is saved
by the existing runtime checkpoint, including native identities and tranches.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import math
from pathlib import Path
from typing import Any, Optional

from backend.app.core.trading_windows import ET
from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.base import SignalEvent, Strategy, StrategyStatus
from backend.app.strategies.tsla_or15_retest import (
    BAR_GRACE_SECONDS, CLOCK_SKEW_SECONDS, ENTRY_GRACE_SECONDS,
    frozen_atr, session_bounds,
)

TSLA_ID = "tsla_asymmetric_dual"
CDE_ID = "cde_asymmetric_dual"
TRI_IDS = frozenset((TSLA_ID, CDE_ID))
FIXED_IDS = TRI_IDS | {"tsla_or15_retest"}
VERSION = "ASYMMETRIC_DUAL_PAPER_V1"
SOURCE_PATH = Path(__file__).resolve().parents[3] / "docs/tri_engine/SOURCE_EXECUTION_PLAN.md"
SOURCE_SHA256 = "f89a762e09f69c72f38e8c49ae411fdb64b9b67e569a0ad127c821d0607642bb"
MINUTE = timedelta(minutes=1)
ACTIVE_PHASES = {"ENTERING", "HOLDING", "EXITING"}
TERMINAL_PHASES = {"SKIPPED", "CLOSED", "NO_SIGNAL"}


class AsymmetricDualStrategy(Strategy):
    def __init__(self, symbol: str) -> None:
        if symbol not in ("TSLA", "CDE"):
            raise ValueError("Only TSLA and CDE belong to this plan")
        super().__init__(TSLA_ID if symbol == "TSLA" else CDE_ID,
                         f"{symbol} Asymmetric Opening Range")
        self.symbol = symbol
        self.protocol_version = VERSION
        self.source_hash = SOURCE_SHA256
        self.session_day: Optional[date] = None
        self.session_equity: Optional[float] = None
        self.phase = "WAITING_SESSION"
        self.reason: Optional[str] = None
        self.incomplete = False
        self.bars: dict[str, list[BarEvent]] = {symbol: [], "QQQ": []}
        self.paired_count = 0
        self.or_high: Optional[float] = None
        self.or_low: Optional[float] = None
        self.or_mid: Optional[float] = None
        self.qqq_close: Optional[float] = None
        self.qqq_vwap: Optional[float] = None
        self.breakout_at: Optional[datetime] = None
        self.signal: Optional[SignalEvent] = None
        self.signal_consumed = False
        self.entry_due: Optional[datetime] = None
        self.decision_at: Optional[datetime] = None
        self.entry_order_id: Optional[str] = None
        self.entry_terminal = False
        self.quantity = 0
        self.risk_reserved = 0.0
        self.tranches: list[dict[str, Any]] = []
        self.native: dict[str, dict[str, Any]] = {}
        self.exit_reason: Optional[str] = None
        self.last_quote: Optional[dict[str, Any]] = None
        self.last_clock: Optional[datetime] = None
        self.last_managed_bar: Optional[datetime] = None
        self.last_poll: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.execution_mode = "alpaca_paper"
        self.audit: list[dict[str, Any]] = []
        self.trade_recorded = False

    @property
    def long_last_signal(self) -> time:
        return time(11, 30) if self.symbol == "TSLA" else time(11, 57)

    @property
    def short_cutoff(self) -> time:
        return time(11, 0) if self.symbol == "TSLA" else time(11, 30)

    @property
    def entry_cutoff(self) -> Optional[datetime]:
        if self.session_day is None:
            return None
        return datetime.combine(self.session_day,
                                time(11, 32, 5) if self.symbol == "TSLA" else time(12), ET)

    @property
    def side(self) -> Optional[str]:
        return ("LONG" if self.signal.side == OrderSide.BUY else "SHORT") if self.signal else None

    @property
    def stop(self) -> Optional[float]:
        return self.or_low if self.side == "LONG" else self.or_mid

    def note(self, kind: str, at: datetime, **fields: Any) -> None:
        self.audit.append({"kind": kind, "at": at.isoformat(), **fields})

    def start_session(self, day: date) -> None:
        if self.session_day == day:
            return
        if self.phase in ACTIVE_PHASES:
            self.incomplete = True
            self.exit_reason = self.exit_reason or "SESSION_RECOVERY"
            return
        keep = {key: getattr(self, key) for key in (
            "status", "daily_pnl", "trades_count", "wins_count", "losses_count",
            "win_rate", "_trade_pnls", "execution_mode")}
        self.__init__(self.symbol)
        self.__dict__.update(keep)
        self.session_day = day
        self.phase = "BUILDING_RANGE" if session_bounds(day) else "SKIPPED"
        self.reason = None if session_bounds(day) else "CALENDAR_UNAVAILABLE_OR_CLOSED"
        self.note("SESSION", datetime.combine(day, time(9, 30), ET),
                  version=VERSION, source_sha256=SOURCE_SHA256, validation="NOT_EVALUATED")

    def skip(self, reason: str, now: datetime) -> None:
        if self.phase in ACTIVE_PHASES:
            self.incomplete = True
            self.exit_reason = self.exit_reason or reason
        elif self.phase not in TERMINAL_PHASES:
            self.phase = "SKIPPED"
            self.risk_reserved = 0.0
        else:
            return
        if self.reason != reason:
            self.note("SKIP" if self.phase == "SKIPPED" else "INCOMPLETE", now, reason=reason)
        self.reason = reason

    def on_bar(self, bar: BarEvent) -> list[SignalEvent]:
        return self.on_completed_bar(bar, datetime.now(timezone.utc))

    def on_completed_bar(self, bar: BarEvent, received_at: datetime) -> list[SignalEvent]:
        """Feed one completed minute bar; returns the day's single signal once.

        Matches the audited research loop: the opening range needs all fifteen
        09:30-09:44 bars, later minutes with no trades simply have no bar, and a
        stock bar is judged against the QQQ bar of the same minute (none means
        the QQQ check fails for that minute).
        """
        sym = bar.symbol.upper()
        if sym not in self.bars:
            return []
        now = received_at.astimezone(ET)
        self.start_session(now.date())
        if self.phase in TERMINAL_PHASES or self.signal_consumed:
            return []  # after the one setup is taken, exits run on broker orders and clocks
        if bar.timestamp.tzinfo is None:
            self.skip("NAIVE_BAR_TIMESTAMP", now)
            return []
        ts = bar.timestamp.astimezone(ET)
        bounds = session_bounds(ts.date())
        if not bounds or not bounds[0] <= ts < bounds[1]:
            return []
        if ts.date() != now.date() or ts.date() != self.session_day:
            self.skip("WRONG_SESSION_BAR", now)
            return []
        if ts.second or ts.microsecond or (now - ts - MINUTE).total_seconds() < -CLOCK_SKEW_SECONDS:
            self.skip("INCOMPLETE_BAR", now)  # using a minute before it closes is lookahead
            return []
        prices = (bar.open, bar.high, bar.low, bar.close)
        if (not all(math.isfinite(v) and v > 0 for v in prices)
                or not math.isfinite(bar.volume) or bar.volume < 0
                or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close)):
            self.skip("INVALID_OHLCV", now)
            return []
        previous = self.bars[sym][-1] if self.bars[sym] else None
        expected = previous.timestamp + MINUTE if previous else bounds[0]
        if ts < expected:
            return []  # a repeat or late copy of a minute already counted
        range_end = bounds[0] + 15 * MINUTE
        if ts > expected and expected < range_end:
            self.skip("MISSING_OPENING_RANGE_BAR", now)
            return []
        self.bars[sym].append(bar)
        if (sym == self.symbol and self.or_high is None and len(self.bars[sym]) == 15):
            opening = self.bars[sym]
            self.or_high, self.or_low = max(b.high for b in opening), min(b.low for b in opening)
            self.or_mid = (self.or_high + self.or_low) / 2
            self.phase = "WAITING_BREAKOUT"
            self.note("OPENING_RANGE", now, high=self.or_high, low=self.or_low, mid=self.or_mid)
        return self._evaluate_ready(now, range_end)

    def _evaluate_ready(self, now: datetime, range_end: datetime) -> list[SignalEvent]:
        stocks, context = self.bars[self.symbol], self.bars["QQQ"]
        # paired_count = stock bars already judged. A stock minute waits until
        # QQQ has reached that minute, so arrival order cannot change a decision.
        while self.paired_count < len(stocks):
            i = self.paired_count
            stock = stocks[i]
            if not context or context[-1].timestamp < stock.timestamp:
                return []
            self.paired_count += 1
            if self.or_high is None or stock.timestamp < range_end:
                continue
            qbars = [b for b in context if b.timestamp <= stock.timestamp]
            qqq = qbars[-1] if qbars and qbars[-1].timestamp == stock.timestamp else None
            volume = sum(b.volume for b in qbars)
            # Close-weighted, as in the audited research script (run_full_dual_audit.py).
            self.qqq_vwap = sum(b.close * b.volume for b in qbars) / volume if volume > 0 else None
            self.qqq_close = qqq.close if qqq else None
            bar_time = stock.timestamp.astimezone(ET).time()
            if bar_time > self.long_last_signal:
                continue
            if self.status != StrategyStatus.ACTIVE:
                self.skip("OPERATOR_PAUSED", now)
                return []
            atr = frozen_atr(stocks[:i + 1])
            context_ok = qqq is not None and self.qqq_vwap is not None
            long_checks = {
                "prior_breakout": self.breakout_at is not None and self.breakout_at < stock.timestamp,
                "near_range_high": stock.low <= self.or_high + .20 * atr,
                "above_midpoint": stock.low >= self.or_mid,
                "green_candle": stock.close > stock.open,
                "close_reclaims_high": stock.close >= self.or_high,
                "qqq_above_vwap": context_ok and qqq.close >= self.qqq_vwap,
            }
            short_checks = {"before_cutoff": bar_time < self.short_cutoff,
                            "below_range_low": stock.close < self.or_low,
                            "qqq_below_vwap": context_ok and qqq.close < self.qqq_vwap}
            self.note("EVALUATE", now, bar_at=stock.timestamp.isoformat(), atr=atr,
                      qqq_close=self.qqq_close, qqq_vwap=self.qqq_vwap,
                      long_checks=long_checks, short_checks=short_checks)
            side = OrderSide.BUY if all(long_checks.values()) else OrderSide.SELL if all(short_checks.values()) else None
            if self.breakout_at is None and stock.close > self.or_high:
                self.breakout_at = stock.timestamp
                self.phase = "WAITING_RETEST"
                self.note("BREAKOUT", now, bar_at=stock.timestamp.isoformat())
            if side is None:
                continue
            self.signal_consumed = True
            self.phase = "WAITING_ENTRY"
            self.entry_due = stock.timestamp + 2 * MINUTE
            self.decision_at = now
            stop = self.or_low if side == OrderSide.BUY else self.or_mid
            direction = 1 if side == OrderSide.BUY else -1
            risk = direction * (stock.close - stop)
            self.signal = SignalEvent(self.symbol, side, OrderType.MARKET,
                stock.close, stop, stock.close + direction * (1.5 if self.symbol == "TSLA" else 2) * risk,
                stock.close + direction * 2 * risk, self.strategy_id, 1.,
                "CONFIRMED_RETEST" if side == OrderSide.BUY else "MID_STOP_BREAKDOWN", timestamp=stock.timestamp)
            self.note("SIGNAL", now, signal_at=stock.timestamp.isoformat(), side=self.side,
                      due_at=self.entry_due.isoformat(), stop=stop)
            return [self.signal]
        return []

    def on_time_tick(self, market_time: datetime, live: bool = True) -> None:
        now = market_time.astimezone(ET)
        if self.last_clock and now < self.last_clock:
            return
        self.start_session(now.date())
        self.last_clock = now
        bounds = session_bounds(now.date())
        if not bounds or now < bounds[0]:
            return
        if self.phase in ("BUILDING_RANGE", "WAITING_BREAKOUT", "WAITING_RETEST"):
            last_decision = datetime.combine(now.date(), self.long_last_signal, ET) + MINUTE
            if now > last_decision + timedelta(seconds=BAR_GRACE_SECONDS):
                self.phase = "NO_SIGNAL"
                self.note("NO_SIGNAL", now)
            elif self.or_high is None and now > bounds[0] + 16 * MINUTE + timedelta(seconds=BAR_GRACE_SECONDS):
                self.skip("MISSING_OPENING_RANGE_BAR", now)
        # Offline replay fills on the T+2 bar itself, which arrives a minute later.
        if live and self.phase == "WAITING_ENTRY" and now > self.entry_due + timedelta(seconds=ENTRY_GRACE_SECONDS):
            self.skip("MISSED_ENTRY_WINDOW", now)

    def session_record(self) -> dict[str, Any]:
        return {"version": VERSION, "source_sha256": SOURCE_SHA256,
                "session_date": self.session_day.isoformat() if self.session_day else None,
                "symbol": self.symbol, "phase": self.phase, "reason": self.reason,
                "signal_consumed": self.signal_consumed, "incomplete": self.incomplete,
                "execution_mode": self.execution_mode, "validation": "NOT_EVALUATED",
                "events": list(self.audit), "tranches": self.tranches}

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["tri_engine"] = {
            "version": VERSION, "source_sha256": SOURCE_SHA256, "symbol": self.symbol,
            "phase": self.phase, "reason": self.reason, "quantity": self.quantity,
            "last_error": self.last_error,
            "side": self.side, "mode": self.execution_mode, "risk_reserved": self.risk_reserved,
            "risk_budget": self.session_equity * .0075 if self.session_equity else None,
            "or_high": self.or_high, "or_low": self.or_low, "or_mid": self.or_mid,
            "qqq_close": self.qqq_close, "qqq_vwap": self.qqq_vwap,
            "entry_due": self.entry_due.isoformat() if self.entry_due else None,
            "incomplete": self.incomplete, "tranches": [dict(t) for t in self.tranches],
        }
        return result

    def window(self, now: datetime, blockers: list[str]) -> dict[str, Any]:
        shown = now.astimezone(ET)
        bounds = session_bounds(shown.date())
        last_decision = datetime.combine(shown.date(), self.long_last_signal, ET) + MINUTE + timedelta(seconds=BAR_GRACE_SECONDS)
        in_hours = bool(bounds and datetime.combine(shown.date(), time(9, 45), ET) <= shown <= last_decision)
        labels = {"BUILDING_RANGE": "Learning the first 15 minutes.",
                  "WAITING_BREAKOUT": "Waiting for a breakout or breakdown.",
                  "WAITING_RETEST": "Watching for a confirmed bounce or a breakdown.",
                  "WAITING_ENTRY": "Setup confirmed; waiting for the scheduled entry minute.",
                  "ENTERING": "Entry order is with the paper broker.",
                  "HOLDING": "Managing fixed targets and safety exits.",
                  "EXITING": "Closing remaining shares and checking broker orders.",
                  "CLOSED": "Finished today's trade.", "SKIPPED": "Skipped today; a required check did not pass.",
                  "NO_SIGNAL": "No qualifying setup today.", "WAITING_SESSION": "Waiting for the next session."}
        headline = labels.get(self.phase, self.phase)
        closed_day = not bounds or self.reason == "CALENDAR_UNAVAILABLE_OR_CLOSED"
        if closed_day:
            headline = "Market closed today."  # a weekend or holiday is normal, not a failed check
        blockers = list(blockers)
        if self.status != StrategyStatus.ACTIVE:
            blockers.append("Paused by operator.")
        if self.phase == "SKIPPED" and not closed_day:
            blockers.append("Session checks did not pass.")
        done = self.phase in TERMINAL_PHASES
        state = ("MARKET_CLOSED" if not bounds else "MANAGING" if self.phase in ACTIVE_PHASES | {"WAITING_ENTRY"}
                 else "DONE_FOR_DAY" if done else "BLOCKED" if in_hours and blockers
                 else "CAN_TRADE" if in_hours else "WAITING")
        end = "11:30" if self.symbol == "TSLA" else "12:00"
        notes = ["Paper execution; one setup per day. Entries are market orders two minutes after the signal minute."]
        if bounds and bounds[1].hour == 13:
            notes.append("Early market close today; all shares close by 12:55 PM ET.")
        return {"state": state, "headline": headline, "can_open_now": state == "CAN_TRADE",
                "in_hours": in_hours, "hours": f"9:45 AM–{end} ET",
                "ranges": [["09:45", end]], "trading_day": bool(bounds), "schedule_text": headline,
                "next_change_at": None, "blockers": blockers, "limits": [],
                "market_text": headline, "notes": notes, "evaluated_at": shown.isoformat()}
