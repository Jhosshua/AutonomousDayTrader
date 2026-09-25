"""Frozen, causal TSLA opening-range retest signal state. No order routing here.

The source's unusual TR formula is intentional. This strategy never goes through
the adaptive intraday filters. Mutable state is checkpoint-serializable.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.core.trading_windows import ET, is_trading_day, session_close
from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.base import SignalEvent, Strategy, StrategyStatus

STRATEGY_ID = "tsla_or15_retest"
VERSION = "TSLA_OR15_RETEST_2R_BROKER_PAPER_V1"
SOURCE_VERSION = "TSLA_OR15_RETEST_2R_PAPER_V1"
SOURCE_SHA256 = "1ed5091248fcaf1b66004eda2a8c21ed5114c23dbe9a590370c7a30e596ee5cd"
BAR_GRACE_SECONDS = 10
ENTRY_GRACE_SECONDS = 5
QUOTE_MAX_AGE_SECONDS = 2
# Host clock vs exchange timestamps. Measured on Railway 2026-09-25: bars arrive
# 0.05-0.14 s after the minute, so a tiny drift used to make complete data look early.
CLOCK_SKEW_SECONDS = 0.5
MINUTE = timedelta(minutes=1)


def session_bounds(day: date) -> Optional[tuple[datetime, datetime]]:
    # This is the verified exchange calendar coverage in trading_windows.py.
    # Unpublished/unsupported calendar years are not assumed to be ordinary days.
    if day.year not in (2026, 2027) or not is_trading_day(day):
        return None
    return datetime.combine(day, time(9, 30), ET), datetime.combine(day, session_close(day), ET)


def frozen_atr(bars: List[BarEvent]) -> float:
    if not bars:
        return 0.0
    if len(bars) < 3:
        return bars[-1].close * 0.005
    tr = [max(b.high - b.low, abs(b.high - bars[i - 1].close)) if i else b.high - b.low
          for i, b in enumerate(bars)]
    return sum(tr[-14:]) / len(tr[-14:])


class TSLAOR15RetestStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__(STRATEGY_ID, "TSLA 15-minute Opening Range Retest")
        self.session_day: Optional[date] = None
        self.phase = "WAITING_SESSION"
        self.reason: Optional[str] = None
        self.bars: Dict[str, List[BarEvent]] = {"TSLA": [], "QQQ": []}
        self.paired_count = 0
        self.or_high: Optional[float] = None
        self.or_low: Optional[float] = None
        self.or_mid: Optional[float] = None
        self.breakout_at: Optional[datetime] = None
        self.signal: Optional[SignalEvent] = None
        self.signal_consumed = False
        self.entry_due: Optional[datetime] = None
        self.decision_at: Optional[datetime] = None
        self.entry_order_id: Optional[str] = None
        self.exit_order_id: Optional[str] = None
        self.filled_at: Optional[datetime] = None
        self.exit_due: Optional[datetime] = None
        self.entry_price: Optional[float] = None
        self.target_price: Optional[float] = None
        self.protection_client_id: Optional[str] = None
        self.protection_confirmed = False
        self.exit_reason: Optional[str] = None
        self.incomplete = False
        self.audit: List[Dict[str, Any]] = []
        self.qqq_vwap: Optional[float] = None
        self.qqq_close: Optional[float] = None
        self.last_quote: Optional[Dict[str, Any]] = None
        self.last_clock: Optional[datetime] = None
        self.last_managed_bar: Optional[datetime] = None
        self.execution_mode = "alpaca_paper"
        self.protocol_version = VERSION
        self.source_hash = SOURCE_SHA256

    def note(self, kind: str, at: datetime, **fields: Any) -> None:
        self.audit.append({"kind": kind, "at": at.isoformat(), **fields})

    def start_session(self, day: date) -> None:
        if self.session_day == day:
            return
        # The controller must reconcile a previous holding before resetting it.
        if self.phase in ("ENTERING", "HOLDING", "EXITING"):
            self.incomplete = True
            return
        performance = {key: getattr(self, key) for key in
                       ("status", "daily_pnl", "trades_count", "wins_count", "losses_count", "win_rate", "_trade_pnls", "execution_mode")}
        self.__init__()
        self.__dict__.update(performance)
        self.session_day = day
        self.phase = "BUILDING_RANGE" if session_bounds(day) else "SKIPPED"
        self.reason = None if session_bounds(day) else "CALENDAR_UNAVAILABLE_OR_CLOSED"
        self.note("SESSION", datetime.combine(day, time(9, 30), ET),
                  version=VERSION, source_sha256=SOURCE_SHA256,
                  evaluation="OFFLINE_TEST" if self.execution_mode == "offline_raw_open" else "COMMISSIONING" if day < date(2026, 10, 1) else "PAPER_OBSERVATION",
                  validation="NOT_EVALUATED")

    def skip(self, reason: str, now: datetime) -> None:
        if self.phase in ("HOLDING", "EXITING", "ENTERING"):
            if not self.incomplete:
                self.note("INCOMPLETE", now, reason=reason)
            self.incomplete = True
            self.reason = self.reason or reason
            return
        if self.phase in ("SKIPPED", "CLOSED", "NO_SIGNAL"):
            return
        self.reason = reason
        self.phase = "SKIPPED"
        self.note("SKIP", now, reason=reason)

    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        # Ordinary strategy callers use wall time; deterministic replay explicitly
        # injects receipt time through on_completed_bar.
        return self.on_completed_bar(bar, datetime.now(timezone.utc))

    def on_completed_bar(self, bar: BarEvent, received_at: datetime) -> List[SignalEvent]:
        symbol = bar.symbol.upper()
        if symbol not in self.bars:
            return []
        now = received_at.astimezone(ET)
        ts = bar.timestamp
        if ts.tzinfo is None:
            self.start_session(now.date())
            self.skip("NAIVE_BAR_TIMESTAMP", now)
            return []
        ts = ts.astimezone(ET)
        bounds = session_bounds(ts.date())
        if bounds is None or not bounds[0] <= ts < bounds[1]:
            return []  # pre/post-market never affect ATR or VWAP
        self.start_session(now.date())
        if self.phase in ("SKIPPED", "NO_SIGNAL", "CLOSED"):
            return []
        if ts.date() != now.date() or ts.date() != self.session_day:
            self.skip("WRONG_SESSION_BAR", now)
            return []
        if ts.second or ts.microsecond or not -CLOCK_SKEW_SECONDS <= (now - ts - MINUTE).total_seconds() <= BAR_GRACE_SECONDS:
            self.skip("INCOMPLETE_OR_STALE_BAR", now)
            return []
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if (not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0 or bar.volume < 0
                or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close)
                or bar.high < bar.low):
            self.skip("INVALID_OHLCV", now)
            return []
        previous = self.bars[symbol][-1] if self.bars[symbol] else None
        expected = previous.timestamp + MINUTE if previous else bounds[0]
        if ts != expected:
            self.skip("DUPLICATE_OR_OUT_OF_ORDER_BAR" if ts < expected else "MISSING_BAR", now)
            return []
        self.bars[symbol].append(bar)
        if self.signal_consumed:
            return []
        signals = []
        while self.paired_count < min(len(self.bars["TSLA"]), len(self.bars["QQQ"])):
            i = self.paired_count
            self.paired_count += 1
            stock, qqq = self.bars["TSLA"][i], self.bars["QQQ"][i]
            if stock.timestamp != qqq.timestamp:
                self.skip("QQQ_TIMESTAMP_MISMATCH", now)
                break
            qbars = self.bars["QQQ"][:i + 1]
            volume = sum(b.volume for b in qbars)
            self.qqq_vwap = sum((b.high + b.low + b.close) / 3 * b.volume for b in qbars) / volume if volume > 0 else None
            self.qqq_close = qqq.close
            if i == 14:
                opening = self.bars["TSLA"][:15]
                self.or_high = max(b.high for b in opening)
                self.or_low = min(b.low for b in opening)
                self.or_mid = (self.or_high + self.or_low) / 2
                self.phase = "WAITING_BREAKOUT"
                self.note("OPENING_RANGE", now, high=self.or_high, low=self.or_low, mid=self.or_mid)
            if i < 15 or stock.timestamp.astimezone(ET).time() > time(11, 30):
                continue
            if self.status != StrategyStatus.ACTIVE:
                self.skip("OPERATOR_PAUSED", now)
                break
            if self.breakout_at is None:
                if stock.close > self.or_high:
                    self.breakout_at = stock.timestamp
                    self.phase = "WAITING_RETEST"
                    self.note("BREAKOUT", now, bar_at=stock.timestamp.isoformat(), close=stock.close)
                continue
            atr = frozen_atr(self.bars["TSLA"][:i + 1])
            if self.qqq_vwap is None:
                self.skip("ZERO_QQQ_VOLUME_AT_RETEST", now)
                break
            checks = {
                "near_range_high": stock.low <= self.or_high + 0.20 * atr,
                "above_midpoint": stock.low >= self.or_mid,
                "green_candle": stock.close > stock.open,
                "close_reclaims_high": stock.close >= self.or_high,
                "qqq_above_vwap": qqq.close >= self.qqq_vwap,
            }
            self.note("RETEST", now, bar_at=stock.timestamp.isoformat(),
                      o=stock.open, h=stock.high, l=stock.low, c=stock.close, v=stock.volume,
                      atr=atr, qqq_close=qqq.close, qqq_vwap=self.qqq_vwap, checks=checks)
            if all(checks.values()):
                self.signal_consumed = True
                self.phase = "WAITING_ENTRY"
                self.entry_due = stock.timestamp + 2 * MINUTE
                self.decision_at = now
                risk = stock.close - self.or_low
                self.signal = SignalEvent("TSLA", OrderSide.BUY, OrderType.MARKET,
                    stock.close, self.or_low, stock.close + 2 * risk, stock.close + 2 * risk,
                    STRATEGY_ID, 1.0, "OR15_CONFIRMED_RETEST", timestamp=stock.timestamp, target_qty=1)
                self.note("SIGNAL", now, signal_bar_at=stock.timestamp.isoformat(),
                          due_at=self.entry_due.isoformat(), quantity=1)
                signals.append(self.signal)
                break
        return signals

    def on_time_tick(self, market_time: datetime) -> None:
        now = market_time.astimezone(ET)
        if self.last_clock and now < self.last_clock:
            return
        self.start_session(now.date())
        self.last_clock = now
        bounds = session_bounds(now.date())
        if not bounds or now < bounds[0]:
            return
        if self.phase in ("BUILDING_RANGE", "WAITING_BREAKOUT", "WAITING_RETEST"):
            # Every missing required minute is detected even if neither symbol arrives.
            next_ts = bounds[0] + self.paired_count * MINUTE
            if now > next_ts + MINUTE + timedelta(seconds=BAR_GRACE_SECONDS):
                self.skip("MISSING_REQUIRED_BAR", now)
            if self.phase != "SKIPPED" and now > datetime.combine(now.date(), time(11, 31, 10), ET):
                self.phase = "NO_SIGNAL"
                self.note("NO_SIGNAL", now)
        if self.phase == "WAITING_ENTRY" and now > self.entry_due + timedelta(seconds=ENTRY_GRACE_SECONDS):
            self.skip("MISSED_ENTRY_WINDOW", now)

    def session_record(self) -> Dict[str, Any]:
        return {"version": VERSION, "source_sha256": SOURCE_SHA256,
                "session_date": self.session_day.isoformat() if self.session_day else None,
                "phase": self.phase, "reason": self.reason, "signal_consumed": self.signal_consumed,
                "incomplete": self.incomplete, "execution_mode": self.execution_mode,
                "validation": "NOT_EVALUATED", "events": list(self.audit)}

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["or15"] = {
            "version": VERSION, "source_version": SOURCE_VERSION, "source_sha256": SOURCE_SHA256,
            "phase": self.phase, "reason": self.reason, "quantity": 1, "mode": self.execution_mode,
            "or_high": self.or_high, "or_low": self.or_low, "or_mid": self.or_mid,
            "signal_consumed": self.signal_consumed, "qqq_close": self.qqq_close, "qqq_vwap": self.qqq_vwap,
            "entry_due": self.entry_due.isoformat() if self.entry_due else None,
            "exit_due": self.exit_due.isoformat() if self.exit_due else None,
            "target_price": self.target_price, "protection_confirmed": self.protection_confirmed,
            "validation": "NOT_EVALUATED", "incomplete": self.incomplete,
        }
        return result

    def window(self, now: datetime, blockers: List[str]) -> Dict[str, Any]:
        shown = now.astimezone(ET)
        bounds = session_bounds(shown.date())
        in_hours = bool(bounds and time(9, 45) <= shown.time() <= time(11, 31, 10))
        labels = {"BUILDING_RANGE": "Learning Tesla's first 15 minutes.",
                  "WAITING_BREAKOUT": "Waiting for Tesla to rise above its morning range.",
                  "WAITING_RETEST": "Waiting for a pullback that holds the morning range.",
                  "WAITING_ENTRY": "Setup confirmed. Buying one share at the scheduled minute.",
                  "ENTERING": "Waiting for the broker to confirm the buy.",
                  "HOLDING": "Holding one Tesla share with fixed exits.",
                  "EXITING": "Closing the Tesla trade; waiting for broker confirmation.",
                  "CLOSED": "Finished its one trade today.", "SKIPPED": "Skipped today; required checks did not pass.",
                  "NO_SIGNAL": "No qualifying Tesla setup today.", "WAITING_SESSION": "Waiting for the next session."}
        status_text = labels.get(self.phase, "Waiting for a Tesla setup.")
        if self.status == StrategyStatus.PAUSED:
            blockers = blockers + ["Paused by operator."]
        if self.phase == "SKIPPED":
            blockers = blockers + ["Session checks did not pass."]
        done = self.phase in ("CLOSED", "NO_SIGNAL", "SKIPPED") or self.signal_consumed
        state = "CAN_TRADE" if in_hours and not done and not blockers else "BLOCKED" if in_hours and blockers else "DONE_FOR_DAY" if done or shown.time() > time(11, 31, 10) else "WAITING"
        if not bounds:
            state = "MARKET_CLOSED"
        if self.phase in ("ENTERING", "HOLDING", "EXITING", "WAITING_ENTRY"):
            state = "MANAGING"
        return {"state": state, "headline": status_text, "can_open_now": state == "CAN_TRADE",
                "in_hours": in_hours, "hours": "9:45 AM - 11:30 AM ET signal bars",
                "ranges": [["09:45", "11:31"]], "trading_day": bool(bounds),
                "schedule_text": status_text, "next_change_at": None,
                "blockers": blockers, "limits": [], "market_text": status_text,
                "notes": ["One share in the paper account. The 11:30 signal bar completes at 11:31; its entry is scheduled for 11:32."]
                         + (["Early market close today (1:00 PM)."] if bounds and bounds[1].hour == 13 else []),
                "evaluated_at": shown.isoformat()}


def implementation_sha256() -> str:
    # Resolve from backend/app/strategies to repository root.
    repo = Path(__file__).resolve().parents[3]
    paths = [repo / p for p in ("backend/app/strategies/tsla_or15_retest.py",
                               "backend/app/core/or15_execution.py", "backend/app/core/broker.py",
                               "backend/app/core/engine.py", "backend/app/core/bracket.py",
                               "backend/app/core/risk.py", "backend/app/core/runtime_state.py",
                               "backend/app/core/trading_windows.py", "backend/app/main.py")]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
