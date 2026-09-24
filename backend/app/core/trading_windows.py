"""Operator view: when each strategy may open new trades, and what is blocking it right now.

The schedule is derived by asking the SAME `is_strategy_permitted` the entry path uses, phase by
phase, so the card cannot drift from the gate. Live blockers are passed in from the objects the entry
path reads (risk engine, flattening engine, persistence, committed positions, market filter).
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# NYSE full-day closures and 1:00 PM early closes. Source: NYSE holiday calendar (2026-2027).
NYSE_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}
NYSE_EARLY_CLOSES = {date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)}

# Phase boundaries (ET) as used by adaptation.get_time_of_day_phase.
PHASES: List[Tuple[dtime, dtime, str]] = [
    (dtime(9, 30), dtime(10, 0), "OPEN_VOLATILITY_FLUSH"),
    (dtime(10, 0), dtime(11, 30), "TREND_CONTINUATION"),
    (dtime(11, 30), dtime(14, 0), "MIDDAY_CHOP"),
    (dtime(14, 0), dtime(15, 0), "AFTERNOON_PUSH"),
    (dtime(15, 0), dtime(15, 45), "POWER_HOUR"),
    (dtime(15, 45), dtime(16, 0), "EOD_FLATTEN"),
]

GATE_LAG_SEC = 60

STRATEGY_NOTES = {
    "orb": "Morning only. Needs the first 5 minutes to set the range.",
    "vwap_pullback": "Sits out the midday chop.",
    "news_momentum": "Rare by design: needs very strong news plus a volume spike.",
    "mean_reversion": "Sits out the opening half hour.",
}


def session_close(d: date) -> dtime:
    """Regular-session close for d (13:00 on NYSE early-close days)."""
    return dtime(13, 0) if d in NYSE_EARLY_CLOSES else dtime(16, 0)


def session_minutes(d: date) -> int:
    c = session_close(d)
    return (c.hour * 60 + c.minute) - (9 * 60 + 30)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def _fmt(t: dtime) -> str:
    return datetime.combine(date(2000, 1, 1), t).strftime("%-I:%M %p")


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return d.strftime("%A %b %-d")


def schedule_ranges(strategy_id: str, permitted: Callable[[str, str], bool]) -> List[Tuple[dtime, dtime]]:
    """Contiguous ET ranges in which the phase gate allows new entries."""
    ranges: List[Tuple[dtime, dtime]] = []
    for start, end, phase in PHASES:
        if permitted(strategy_id, phase):
            if ranges and ranges[-1][1] == start:
                ranges[-1] = (ranges[-1][0], end)
            else:
                ranges.append((start, end))
    return ranges


def market_direction_text(strategy_id: str, trend: str) -> str:
    """Plain-language version of MarketTrendFilter.is_signal_permitted for this strategy."""
    t = (trend or "UNKNOWN").upper()
    if t == "UNKNOWN":
        base = "Market direction unknown (index data not ready), so new trades are blocked."
        if strategy_id == "news_momentum":
            return base + " Exception: extreme news (score 0.85+, volume 5x+)."
        return base
    if t == "NEUTRAL":
        return {
            "mean_reversion": "Market is flat: allowed, both directions.",
            "orb": "Market is flat: only on a stock trading at 2.2x+ normal volume.",
            "news_momentum": "Market is flat: only on a stock trading at 2.2x+ normal volume.",
            "vwap_pullback": "Market is flat: blocked until the market picks a direction.",
        }.get(strategy_id, "Market is flat.")
    extreme = " Extreme news can go either way." if strategy_id == "news_momentum" else ""
    if t == "BULLISH":
        if strategy_id == "mean_reversion":
            return "Market is rising: buys only (no shorting into the rally)."
        return "Market is rising: buys only." + extreme
    if t == "BEARISH":
        if strategy_id == "mean_reversion":
            return "Market is falling: shorts only (no buying falling stocks)."
        return "Market is falling: shorts only." + extreme
    return f"Market direction {t}."


def strategy_window(
    strategy_id: str,
    now: datetime,
    permitted: Callable[[str, str], bool],
    *,
    operator_status: str = "ACTIVE",
    market_trend: str = "UNKNOWN",
    breaker_halted: bool = False,
    entry_lockout: bool = False,
    persistence_halted: bool = False,
    positions_full: bool = False,
    vix_stale: bool = False,
) -> Dict[str, Any]:
    shown_at = (now if now.tzinfo else now.replace(tzinfo=ET)).astimezone(ET)
    # The entry gate judges each 1-minute bar by its START time, and that bar reaches the bot
    # about a minute later. Judge the schedule one minute back so the card flips with the gate.
    now_et = shown_at - timedelta(seconds=GATE_LAG_SEC)
    today = now_et.date()
    t = now_et.time()
    ranges = schedule_ranges(strategy_id, permitted)
    hours_label = ", ".join(f"{_fmt(a)} - {_fmt(b)}" for a, b in ranges) or "never"

    trading_day = is_trading_day(today)
    in_hours = trading_day and any(a <= t < b for a, b in ranges)

    # Next time the schedule flips.
    next_change: Optional[datetime] = None
    if in_hours:
        end = next(b for a, b in ranges if a <= t < b)
        next_change = datetime.combine(today, end, ET)
    else:
        later = [a for a, _ in ranges if trading_day and a > t]
        if later:
            next_change = datetime.combine(today, later[0], ET)
        elif ranges:
            nd = next_trading_day(today)
            next_change = datetime.combine(nd, ranges[0][0], ET)

    if in_hours:
        schedule_state = "IN_HOURS"
        schedule_text = f"In its trading hours until {_fmt(next_change.time())}."
    elif not trading_day:
        schedule_state = "MARKET_CLOSED"
        schedule_text = "Market closed today."
    elif next_change is not None and next_change.date() == today:
        schedule_state = "OUTSIDE_HOURS"
        schedule_text = f"Outside its hours. Opens at {_fmt(next_change.time())}."
    elif t < dtime(9, 30):
        schedule_state = "OUTSIDE_HOURS"
        schedule_text = "Market not open yet."
    else:
        schedule_state = "DONE_FOR_DAY"
        schedule_text = "Done for today."
    if not in_hours and next_change is not None and next_change.date() != today:
        schedule_text += f" Next: {_day_label(next_change.date(), today)} {_fmt(next_change.time())}."

    blockers: List[str] = []
    op = (operator_status or "ACTIVE").upper()
    if op == "PAUSED":
        blockers.append("Paused by operator.")
    elif op == "COOLDOWN":
        blockers.append("Cooling down after a loss.")
    if breaker_halted:
        blockers.append("Daily loss limit hit: no new trades today.")
    if entry_lockout and in_hours:
        blockers.append("End-of-day close-out has started: no new trades.")
    if persistence_halted:
        blockers.append("Saving is failing: new trades blocked until fixed.")
    if positions_full:
        blockers.append("Maximum open positions reached.")
    market_text = market_direction_text(strategy_id, market_trend)
    trend_u = (market_trend or "UNKNOWN").upper()
    # Limits: the entry gate still admits some trades (mirrors MarketTrendFilter exceptions).
    limits: List[str] = []
    if in_hours and trend_u == "UNKNOWN":
        if strategy_id == "news_momentum":
            limits.append("Market direction unknown: only extreme news (score 0.85+, volume 5x+) can trade.")
        else:
            blockers.append("Market direction unknown.")
    if in_hours and trend_u == "NEUTRAL":
        if strategy_id == "vwap_pullback":
            blockers.append("Market is flat.")
        elif strategy_id in ("orb", "news_momentum"):
            limits.append("Market is flat: only stocks trading at 2.2x+ normal volume.")

    can_open = in_hours and not blockers
    if can_open and limits:
        state, headline = "LIMITED", "Can trade, with limits"
    elif can_open:
        state, headline = "CAN_TRADE", "Can open trades now"
    elif in_hours:
        state, headline = "BLOCKED", "Blocked right now"
    elif schedule_state == "MARKET_CLOSED":
        state, headline = "MARKET_CLOSED", "Market closed"
    elif schedule_state == "DONE_FOR_DAY":
        state, headline = "DONE_FOR_DAY", "Done for today"
    else:
        state, headline = "WAITING", (
            f"Opens {_fmt(next_change.time())}" if next_change and next_change.date() == today else "Waiting for the open"
        )
    if op == "PAUSED":
        state, headline = "PAUSED", "Paused"

    notes = [STRATEGY_NOTES.get(strategy_id, "")] if STRATEGY_NOTES.get(strategy_id) else []
    if vix_stale:
        notes.append("VIX data is stale: position size capped at normal.")
    if today in NYSE_EARLY_CLOSES:
        notes.append("Early market close today (1:00 PM).")

    # B5: plain 24h ET string ranges for the frontend hours bar, independent of whether
    # today happens to be a trading day (the schedule itself never changes on weekends/holidays).
    ranges_out: List[List[str]] = [[a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in ranges]

    return {
        "state": state,
        "headline": headline,
        "can_open_now": can_open,
        "in_hours": in_hours,
        "hours": hours_label,
        "ranges": ranges_out,
        "trading_day": trading_day,
        "schedule_text": schedule_text,
        "next_change_at": next_change.isoformat() if next_change else None,
        "blockers": blockers,
        "limits": limits,
        "market_text": market_text,
        "notes": notes,
        "evaluated_at": shown_at.isoformat(timespec="seconds"),
    }
