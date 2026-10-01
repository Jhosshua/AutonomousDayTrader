# @steered SNARE-2 2026-09-30
"""Pure rules for the live SPY turn-of-month and COIN bitcoin-follow strategies.

No I/O and no wall clock. Every time, calendar row and market value is supplied by the caller.
The evidence labels and formulas are frozen in docs/day_one_spy_coin_plan.md revision 2.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Iterable, Optional, Sequence

from backend.app.core.trading_windows import ET

SPY_ID = "turn_month_spy"
COIN_ID = "bitcoin_follow_coin"
DAY_ONE_IDS = frozenset({SPY_ID, COIN_ID})
DAY_ONE_POLICY = "day_one_live"
EXPECTED_ACCOUNT = "PA3CSVDZMMPY"
SPY_PCT = 0.20
COIN_PCT = 0.10
POSITION_CAP = 25_000.0
BTC_SYMBOL = "BTC/USD"
BTC_HISTORY = 20
BTC_MIN_HISTORY = 15
BTC_THRESHOLD = 1.0
BTC_DECISION_TARGET_MINUTE = 9 * 60 + 34
COIN_DISPATCH = dtime(9, 36)
COIN_DISPATCH_DEADLINE = dtime(9, 36, 5)
SPY_OPG_FROM = dtime(19, 5)
OPG_CUTOFF = dtime(9, 27)
MARKET_OPEN = dtime(9, 30)
OPEN_FALLBACK_DEADLINE = dtime(9, 31)
FULL_CLOSE = dtime(16, 0)
EARLY_CLOSE = dtime(13, 0)
CLS_SUBMIT_BEFORE = timedelta(minutes=10)
CLOSE_FALLBACK_BEFORE = timedelta(seconds=30)
EVIDENCE = {
    SPY_ID: "post hoc atlas pattern, not preregistered",
    COIN_ID: "post holdout selection, design gate failed",
}

PHASES = frozenset({
    "IDLE", "RESERVED", "ENTRY_INTENT", "ENTRY_PENDING", "HELD", "EXIT_INTENT",
    "EXIT_PENDING", "RECOVERY", "DONE", "SKIPPED", "RECOVERY_HALT",
})


@dataclass(frozen=True)
class BtcPoint:
    target_day: date
    target_minute: int
    selected_at: datetime
    close: float


@dataclass(frozen=True)
class CoinDecision:
    zmove: float
    sigma: float
    history_count: int
    side: int


def is_day_one(obj: object) -> bool:
    if obj is None:
        return False
    if getattr(obj, "execution_policy", None) == DAY_ONE_POLICY:
        return True
    return str(getattr(obj, "strategy_id", "") or "").lower() in DAY_ONE_IDS


def parse_calendar(rows: Sequence[dict[str, Any]]) -> dict[date, dtime]:
    """Validate Alpaca calendar rows into date to close. Unexpected rows are rejected."""
    out: dict[date, dtime] = {}
    if len(rows) > 400:
        raise ValueError("calendar response exceeds 400 rows")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("calendar row must be an object")
        try:
            day = date.fromisoformat(str(row["date"]))
            close = dtime.fromisoformat(str(row["close"])[:5])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("calendar row has invalid date or close") from exc
        if close not in (FULL_CLOSE, EARLY_CLOSE) or day in out:
            raise ValueError("calendar row has unsupported close or duplicate date")
        out[day] = close
    return out


def turn_month_eligible(day: date, sessions: Iterable[date]) -> bool:
    """Last session of its month, or first two sessions of its month.

    The caller supplies boundary months so the final session can be proved by a later session.
    """
    ordered = sorted(set(sessions))
    if day not in ordered:
        return False
    same = [d for d in ordered if d.year == day.year and d.month == day.month]
    if not same:
        return False
    return day in same[:2] or day == same[-1]


def prior_session(day: date, sessions: Iterable[date]) -> Optional[date]:
    earlier = [d for d in set(sessions) if d < day]
    return max(earlier) if earlier else None


def next_session(day: date, sessions: Iterable[date]) -> Optional[date]:
    later = [d for d in set(sessions) if d > day]
    return min(later) if later else None


def close_deadlines(day: date, close: dtime) -> tuple[datetime, datetime]:
    base = datetime.combine(day, close, ET)
    return base - CLS_SUBMIT_BEFORE, base - CLOSE_FALLBACK_BEFORE


def civil_key(day: date, minute: int) -> int:
    if not 0 <= int(minute) < 1440:
        raise ValueError("minute must be in one day")
    return day.toordinal() * 1440 + int(minute)


def select_btc_close(rows: Sequence[dict[str, Any]], target_day: date, target_minute: int) -> Optional[BtcPoint]:
    """Research BTC.price exactly: stable sort by Eastern civil minute, then select the rightmost
    close at or before target and no more than four start minutes old."""
    parsed: list[tuple[int, int, datetime, float]] = []
    if len(rows) > 10:
        raise ValueError("BTC endpoint response exceeds 10 rows")
    for order, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("BTC row must be an object")
        try:
            ts = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                raise ValueError("BTC timestamp must have a timezone")
            local = ts.astimezone(ET)
            close = float(row["c"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("BTC row has invalid timestamp or close") from exc
        if not math.isfinite(close) or close <= 0:
            raise ValueError("BTC close must be finite and positive")
        key = civil_key(local.date(), local.hour * 60 + local.minute)
        parsed.append((key, order, local, close))
    parsed.sort(key=lambda x: x[0])  # stable, so duplicate keys retain response order
    target = civil_key(target_day, target_minute)
    eligible = [x for x in parsed if target - 4 <= x[0] <= target]
    if not eligible:
        return None
    key, _order, selected, close = eligible[-1]
    return BtcPoint(target_day, target_minute, selected, close)


def normalized_btc_move(anchor: BtcPoint, decision: BtcPoint) -> float:
    """Research formula. Elapsed time uses target Eastern civil minute keys, not selected bar time."""
    hours = (civil_key(decision.target_day, decision.target_minute)
             - civil_key(anchor.target_day, anchor.target_minute)) / 60.0
    if hours <= 0:
        raise ValueError("BTC move endpoints are not ordered")
    move = (decision.close / anchor.close - 1.0) / math.sqrt(hours)
    if not math.isfinite(move):
        raise ValueError("BTC normalized move is not finite")
    return move


def trailing_sigma(values: Sequence[float]) -> tuple[Optional[float], int]:
    finite = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    finite = finite[-BTC_HISTORY:]
    if len(finite) < BTC_MIN_HISTORY:
        return None, len(finite)
    mean = sum(finite) / len(finite)
    variance = sum((v - mean) ** 2 for v in finite) / (len(finite) - 1)
    sigma = math.sqrt(variance)
    return (sigma if math.isfinite(sigma) and sigma > 0 else None), len(finite)


def coin_decision(history: Sequence[float], today_move: float) -> CoinDecision:
    sigma, count = trailing_sigma(history)
    if sigma is None or not math.isfinite(today_move):
        return CoinDecision(float(today_move), float("nan") if sigma is None else sigma, count, 0)
    side = 1 if today_move > BTC_THRESHOLD * sigma else -1 if today_move < -BTC_THRESHOLD * sigma else 0
    return CoinDecision(float(today_move), sigma, count, side)


def ssr_blocks_short(prior_low: float, close_two_back: float, today_low_through_0934: float,
                     prior_close: float) -> bool:
    values = (prior_low, close_two_back, today_low_through_0934, prior_close)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in values):
        return True
    return prior_low <= 0.9 * close_two_back or today_low_through_0934 <= 0.9 * prior_close


def target_shares(equity: float, buying_power: float, reference_price: float, pct: float) -> int:
    values = (equity, buying_power, reference_price, pct)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        return 0
    if equity <= 0 or buying_power <= 0 or reference_price <= 0 or not 0 < pct <= 1:
        return 0
    target = min(equity * pct, POSITION_CAP, buying_power)
    return max(0, int(target // reference_price))


def client_id(strategy_id: str, session: date, role: str, attempt: int = 1) -> str:
    if strategy_id not in DAY_ONE_IDS or role not in ("entry", "exit", "recovery") or attempt < 1:
        raise ValueError("invalid day one client id fields")
    short = "tom-spy" if strategy_id == SPY_ID else "btc-coin"
    return f"adt-{short}-{session.strftime('%Y%m%d')}-{role}-{attempt}"


def validate_lifecycle(raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or raw.get("phase") not in PHASES:
        raise ValueError("day one lifecycle has invalid phase")
    symbol = raw.get("symbol")
    sid = raw.get("strategy_id")
    if (symbol, sid) not in (("SPY", SPY_ID), ("COIN", COIN_ID)):
        raise ValueError("day one lifecycle identity mismatch")
    for key in ("target_qty", "entry_qty", "exit_qty"):
        value = raw.get(key, 0)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"day one {key} must be a nonnegative integer")
    if raw.get("exit_qty", 0) > raw.get("entry_qty", 0):
        raise ValueError("day one exit quantity exceeds entry quantity")
    protection = raw.get("protection")
    if protection is not None:
        try:
            session = date.fromisoformat(str(raw["session"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("day one protection has an invalid session") from exc
        expected_side = "sell" if raw.get("side") == "LONG" else "buy" if raw.get("side") == "SHORT" else None
        if not isinstance(protection, dict) or protection.get("side") != expected_side:
            raise ValueError("day one protection side is invalid")
        if protection.get("exit_client_id") != client_id(sid, session, "exit"):
            raise ValueError("day one exit protection identity is invalid")
        if protection.get("recovery_client_id") != client_id(sid, session, "recovery"):
            raise ValueError("day one recovery protection identity is invalid")
        if protection.get("durable") is not True:
            raise ValueError("day one protection identity is not durable")
    attempts = raw.get("attempts", [])
    if not isinstance(attempts, list) or len(attempts) > 12:
        raise ValueError("day one attempts must be a bounded list")
    live_by_role: dict[str, int] = {}
    for a in attempts:
        if not isinstance(a, dict) or a.get("role") not in ("entry", "exit", "recovery"):
            raise ValueError("day one attempt has invalid role")
        if not isinstance(a.get("client_id"), str) or not a["client_id"].startswith("adt-"):
            raise ValueError("day one attempt has invalid client id")
        if a.get("status") not in ("intent", "ambiguous", "accepted", "partially_filled", "filled",
                                    "canceled", "expired", "rejected", "done_for_day"):
            raise ValueError("day one attempt has invalid status")
        if a["status"] in ("intent", "ambiguous", "accepted", "partially_filled"):
            live_by_role[a["role"]] = live_by_role.get(a["role"], 0) + 1
    if any(n > 1 for n in live_by_role.values()):
        raise ValueError("day one lifecycle has multiple live attempts for a role")
