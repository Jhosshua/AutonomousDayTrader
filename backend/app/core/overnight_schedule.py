# @steered SNARE-2 2026-09-30
"""Overnight holds (NVDA, IREN, HUT): the frozen buy rule, sale date, times and skip reasons.

Pure functions only. No I/O and no wall clock: every time is passed in. The calendar is an
object with is_trading_day(date), session_close(date) and next_trading_day(date); a date the
calendar does not cover raises CalendarNotCovered, which the gates turn into a refusal.

The rule is research/edge_hunt_2026_09_29 overnight {"cond": "none", "exit": "open"}, as
declared in PLAN_2026_09_30_overnight_holds.md section 1 (F1 to F10, X1 to X12):
buy at the close of every regular session that closes at 16:00, sell at the next session's
open. X2: the 80% data rule is judged at 15:46:05 from regular session bars that START in
[09:30, 15:45) ET, and passes when that count plus 15 reaches 312. The 09:30 bar must exist.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Iterable, Optional, Protocol, Tuple

from backend.app.core import trading_windows
from backend.app.core.trading_windows import ET

SYMBOLS = ("NVDA", "IREN", "HUT")          # also the order in which buys shrink (X11)
STRATEGY_IDS = {"NVDA": "overnight_nvda", "IREN": "overnight_iren", "HUT": "overnight_hut"}
OVERNIGHT_IDS = frozenset(STRATEGY_IDS.values())

# F2a / X2. Research lib.py:89 needs 0.8 x 390 = 312 bars. At 15:46:05 only bars starting
# before 15:45 are counted, and the 15 bars 15:45 to 15:59 are assumed present.
MIN_SESSION_BARS = 312
ASSUMED_LATE_BARS = 15
REGULAR_OPEN = dtime(9, 30)
COUNT_CUTOFF = dtime(15, 45)               # bars starting before this minute are counted
FULL_DAY_CLOSE = dtime(16, 0)

# Section 3 timing (ET). The buy day and the sale day each have their own clock.
BUY_WINDOW_START = dtime(15, 46, 5)
BUY_ALERT = dtime(15, 47, 30)
BUY_GIVE_UP = dtime(15, 49, 30)
CLS_CUTOFF = dtime(15, 50)                 # Alpaca refuses closing auction orders after this
BUY_FALLBACK_AT = dtime(15, 59, 30)        # D10 plain market buy when cls is refused
BUY_POLL_FROM = dtime(16, 0, 5)
FIDELITY_CLOSE_AT = dtime(16, 15)
SALE_QUEUE_AT = dtime(19, 0, 30)
OPG_OPENS = dtime(19, 0)                   # Alpaca refuses opg orders 09:28 to 19:00
MORNING_CHECK = dtime(9, 0)
SALE_REPLACE_DEADLINE = dtime(9, 27, 30)
OPG_CUTOFF = dtime(9, 28)
SALE_POLL_FROM = dtime(9, 30)
SALE_FALLBACK_AT = dtime(9, 31)
FIDELITY_OPEN_AT = dtime(9, 45)

BUY_RETRY_SEC = 5
POLL_SEC = 5
SALE_RETRY_SEC = 30

# Skip and needs look reasons. Plain, stable strings: they are saved and shown on the page.
EARLY_CLOSE = "EARLY_CLOSE"
NOT_TRADING_DAY = "NOT_TRADING_DAY"
CALENDAR_NOT_COVERED = "CALENDAR_NOT_COVERED"
CALENDAR_DISAGREES = "CALENDAR_DISAGREES"
ALPACA_CALENDAR_UNAVAILABLE = "ALPACA_CALENDAR_UNAVAILABLE"
NO_0930_BAR = "NO_0930_BAR"
DATA_SHORT = "DATA_SHORT"
RELAY_UNAVAILABLE = "RELAY_UNAVAILABLE"
MODE_OFF = "MODE_OFF"
STOCK_OFF = "STOCK_OFF"
NO_BROKER = "NO_BROKER"
OPERATOR_NO_BUY_TONIGHT = "OPERATOR_NO_BUY_TONIGHT"
EARLIER_HOLD_UNSOLD = "EARLIER_HOLD_UNSOLD"
HELD_BY_OTHER_STRATEGY = "HELD_BY_OTHER_STRATEGY"
BROKER_MISMATCH = "BROKER_MISMATCH"
INTENT_NOT_DURABLE = "INTENT_NOT_DURABLE"
DAY_TRADE_NOT_CLOSED = "DAY_TRADE_NOT_CLOSED"
BROKER_NOT_FLAT = "BROKER_NOT_FLAT"
ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"
NO_PRICE = "NO_PRICE"
NO_ROOM = "NO_ROOM"
BUY_REFUSED = "BUY_REFUSED"
WASH_TRADE_REFUSED = "WASH_TRADE_REFUSED"
BUYING_POWER_REFUSED = "BUYING_POWER_REFUSED"
BROKER_UNREACHABLE = "BROKER_UNREACHABLE"
MISSED_BUY_WINDOW = "MISSED_BUY_WINDOW"
AUCTION_NO_FILL = "AUCTION_NO_FILL"
CANCELED_AT_ALPACA = "CANCELED_AT_ALPACA"
# Needs look (the hold still sells).
NO_ORDER_BY_1547 = "NO_ORDER_BY_1547"
SHARES_UNEXPLAINED = "SHARES_UNEXPLAINED"
CORPORATE_ACTION = "CORPORATE_ACTION"
CORPORATE_ACTIONS_UNAVAILABLE = "CORPORATE_ACTIONS_UNAVAILABLE"
BOOKING_REFUSED = "BOOKING_REFUSED"
SALE_REFUSED = "SALE_REFUSED"
UNSOLD_AFTER_0931 = "UNSOLD_AFTER_0931"
BUY_ORDER_CONTRADICTS = "BUY_ORDER_CONTRADICTS"

# Gates that cannot change before 15:49:30 skip at once; the others are retried every 5 s.
FINAL_GATES = frozenset({EARLY_CLOSE, NOT_TRADING_DAY, CALENDAR_NOT_COVERED, CALENDAR_DISAGREES,
                         MODE_OFF, STOCK_OFF, NO_BROKER, OPERATOR_NO_BUY_TONIGHT, EARLIER_HOLD_UNSOLD})


class CalendarNotCovered(Exception):
    """The calendar cannot vouch for this date (outside its covered years)."""


class Calendar(Protocol):
    def is_trading_day(self, d: date) -> bool: ...
    def session_close(self, d: date) -> dtime: ...
    def next_trading_day(self, d: date) -> date: ...


class TradingWindowsCalendar:
    """Production calendar: trading_windows.NYSE_HOLIDAYS and NYSE_EARLY_CLOSES, 2026 and 2027 only."""

    COVERED_YEARS = frozenset({2026, 2027})

    def _check(self, d: date) -> None:
        if d.year not in self.COVERED_YEARS:
            raise CalendarNotCovered(f"{d.isoformat()} is outside the NYSE calendar years 2026 to 2027")

    def is_trading_day(self, d: date) -> bool:
        self._check(d)
        return trading_windows.is_trading_day(d)

    def session_close(self, d: date) -> dtime:
        self._check(d)
        return trading_windows.session_close(d)

    def next_trading_day(self, d: date) -> date:
        self._check(d)
        n = trading_windows.next_trading_day(d)
        self._check(n)
        return n


class SessionListCalendar:
    """A calendar from an explicit session list (the research SPY calendar in T1).

    Dates outside [first session, last session] are not covered.
    """

    def __init__(self, sessions: Iterable[date], early_closes: Iterable[date] = ()) -> None:
        self.sessions = sorted(set(sessions))
        self._set = set(self.sessions)
        self.early = set(early_closes)
        if not self.sessions:
            raise ValueError("session list is empty")

    def _check(self, d: date) -> None:
        if not self.sessions[0] <= d <= self.sessions[-1]:
            raise CalendarNotCovered(f"{d.isoformat()} is outside the session list")

    def is_trading_day(self, d: date) -> bool:
        self._check(d)
        return d in self._set

    def session_close(self, d: date) -> dtime:
        self._check(d)
        return dtime(13, 0) if d in self.early else FULL_DAY_CLOSE

    def next_trading_day(self, d: date) -> date:
        self._check(d)
        n = d + timedelta(days=1)
        while n <= self.sessions[-1]:
            if n in self._set:
                return n
            n += timedelta(days=1)
        raise CalendarNotCovered(f"no session after {d.isoformat()} in the session list")


def et(d: date, t: dtime) -> datetime:
    """The ET moment t on day d (DST handled by the zone)."""
    return datetime.combine(d, t, ET)


def et_date(now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("overnight times must be timezone aware")
    return now.astimezone(ET).date()


def et_time(now: datetime) -> dtime:
    if now.tzinfo is None:
        raise ValueError("overnight times must be timezone aware")
    return now.astimezone(ET).time()


def count_bars_before_cutoff(bar_starts: Iterable[datetime], session_date: date) -> Tuple[int, bool]:
    """X2 inputs from bar START times: (regular bars starting in [09:30, 15:45) ET, has a 09:30 bar).

    Extended hours bars and bars of other days are ignored, and a minute is counted once.
    """
    minutes = set()
    for ts in bar_starts:
        local = ts.astimezone(ET)
        if local.date() != session_date:
            continue
        t = local.time().replace(second=0, microsecond=0)
        if REGULAR_OPEN <= t < COUNT_CUTOFF:
            minutes.add(t)
    return len(minutes), REGULAR_OPEN in minutes


def data_rule_ok(bars_before_1545_count: int) -> bool:
    """X2: count of bars starting 09:30 to 15:44, plus 15, reaches 312."""
    return int(bars_before_1545_count) + ASSUMED_LATE_BARS >= MIN_SESSION_BARS


def calendar_gate(session_date: date, calendar: Calendar) -> Tuple[bool, str]:
    """Is session_date a full (16:00 close) trading day whose next session the calendar knows?"""
    try:
        if not calendar.is_trading_day(session_date):
            return False, NOT_TRADING_DAY
        if calendar.session_close(session_date) != FULL_DAY_CLOSE:
            return False, EARLY_CLOSE
        calendar.next_trading_day(session_date)
    except CalendarNotCovered:
        return False, CALENDAR_NOT_COVERED
    return True, "OK"


def alpaca_calendar_gate(session_date: date, rows: Optional[list]) -> Tuple[bool, str]:
    """Cross check with Alpaca's read only calendar rows ({"date", "open", "close"}).

    The row for session_date must exist and close at 16:00.
    """
    if rows is None:
        return False, ALPACA_CALENDAR_UNAVAILABLE
    row = next((r for r in rows if str(r.get("date")) == session_date.isoformat()), None)
    if row is None:
        return False, CALENDAR_DISAGREES
    close = str(row.get("close") or "")
    if close[:5] != "16:00":
        return False, CALENDAR_DISAGREES if close[:5] != "13:00" else EARLY_CLOSE
    return True, "OK"


def buy_gates(session_date: date, bars_before_1545_count: int, has_0930_bar: bool, calendar: Calendar,
              alpaca_rows: Optional[list] = None, check_alpaca: bool = False) -> Tuple[bool, str]:
    """The rule's own buy decision for one stock and one session, from 15:46:05 information only.

    Passes iff the session closes at 16:00 (early close -> EARLY_CLOSE), the next session is
    known, the 09:30 bar exists (F2b) and count + 15 >= 312 (X2). F2c is assumed (X2).
    With check_alpaca the Alpaca calendar rows must agree as well (section 3, 15:46:05 row).
    """
    ok, reason = calendar_gate(session_date, calendar)
    if not ok:
        return ok, reason
    if check_alpaca:
        ok, reason = alpaca_calendar_gate(session_date, alpaca_rows)
        if not ok:
            return ok, reason
    if not has_0930_bar:
        return False, NO_0930_BAR
    if not data_rule_ok(bars_before_1545_count):
        return False, DATA_SHORT
    return True, "OK"


def sale_date(buy_date: date, calendar: Calendar) -> Optional[date]:
    """F10: the next session in the calendar, or None when the calendar does not cover it."""
    try:
        return calendar.next_trading_day(buy_date)
    except CalendarNotCovered:
        return None


def alpaca_next_session(buy_date: date, rows: Optional[list]) -> Optional[date]:
    """First Alpaca calendar session after buy_date, or None."""
    for r in sorted(rows or [], key=lambda r: str(r.get("date"))):
        try:
            d = date.fromisoformat(str(r.get("date")))
        except ValueError:
            continue
        if d > buy_date:
            return d
    return None


def holding_nights(buy_date: date, sell_date: date) -> str:
    """"weeknight", "weekend" or "holiday" for the page and the skip log."""
    gap = (sell_date - buy_date).days
    if gap <= 1:
        return "weeknight"
    if buy_date.weekday() == 4 and gap == 3:
        return "weekend"
    return "holiday"


def shares_for(equity: float, price: float, pct: float = 0.20, cap: float = 25_000.0) -> int:
    """D2: floor(pct x equity / price), and never more than the $25,000 per position cap."""
    if not (equity > 0 and price > 0):
        return 0
    return max(0, min(int(pct * equity // price), int(cap // price)))


def client_id(symbol: str, buy_date: date, role: str, attempt: int) -> str:
    """adt-ovn-<SYM>-<YYYYMMDD>-buy-<n> / -sell-<n>. The sale carries the BUY date."""
    if role not in ("buy", "sell") or attempt < 1:
        raise ValueError("client id needs role buy/sell and attempt >= 1")
    return f"adt-ovn-{symbol.upper()}-{buy_date.strftime('%Y%m%d')}-{role}-{attempt}"
