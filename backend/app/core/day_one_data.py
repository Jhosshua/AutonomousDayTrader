# @steered SNARE-2 2026-09-30
"""Bounded AlpacaRelay reads for the SPY and COIN day one rules.

The caller supplies the already loaded relay credential. This module never reads process environment,
accepts caller controlled URLs, or logs headers or response bodies. Production is pinned to the one
AlpacaRelay HTTPS origin. Tests may inject an httpx transport and a loopback origin.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta, timezone
from threading import Lock
from typing import Any, Dict, Optional, Sequence
from urllib.parse import urlsplit

import httpx

from backend.app.core import day_one_schedule as d1

PRODUCTION_ORIGIN = "https://alpacarelay-production.up.railway.app"
BODY_LIMIT = 64 * 1024
AGGREGATE_LIMIT = 4 * 1024 * 1024
CALL_LIMIT = 64
ROW_LIMIT = 10
TOTAL_DEADLINE_SEC = 30.0


class DayOneData:
    def __init__(self, base_url: str, token: str, broker: Any, *, transport: Optional[httpx.BaseTransport] = None,
                 allow_loopback: bool = False) -> None:
        split = urlsplit(str(base_url))
        origin = f"{split.scheme}://{split.netloc}".rstrip("/")
        clean = not split.username and not split.password and not split.query and not split.fragment and split.path in ("", "/")
        loopback = split.scheme == "http" and split.hostname in ("127.0.0.1", "localhost")
        if not clean or (origin != PRODUCTION_ORIGIN and not (allow_loopback and loopback and transport is not None)):
            raise ValueError("day one relay origin is not approved")
        if not isinstance(token, str) or not token:
            raise ValueError("day one relay credential is unavailable")
        self.origin = origin
        self.broker = broker
        self._client = httpx.Client(headers={"X-Relay-Token": token}, timeout=httpx.Timeout(5.0, connect=2.0),
                                    follow_redirects=False, transport=transport)
        self._lock = Lock()
        self._calls = 0
        self._bytes = 0
        self._deadline = 0.0

    def close(self) -> None:
        self._client.close()

    def _begin_budget(self) -> None:
        with self._lock:
            self._calls = 0
            self._bytes = 0
            self._deadline = time.monotonic() + TOTAL_DEADLINE_SEC

    def _json(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        if path not in ("/data/v1beta3/crypto/us/bars", "/data/v2/stocks/bars"):
            raise ValueError("day one relay path is not approved")
        with self._lock:
            self._calls += 1
            if self._calls > CALL_LIMIT or time.monotonic() > self._deadline:
                raise TimeoutError("day one relay request budget exceeded")
        response = self._client.get(self.origin + path, params=params)
        if response.is_redirect:
            raise ValueError("day one relay redirects are refused")
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("day one relay response is not JSON")
        raw = response.content
        if len(raw) > BODY_LIMIT:
            raise ValueError("day one relay response exceeds 64 KiB")
        with self._lock:
            self._bytes += len(raw)
            if self._bytes > AGGREGATE_LIMIT:
                raise ValueError("day one relay aggregate response budget exceeded")
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("day one relay response must be an object")
        return value

    @staticmethod
    def _utc(day: date, minute: int) -> datetime:
        local = datetime.combine(day, dtime(minute // 60, minute % 60), d1.ET)
        return local.astimezone(timezone.utc)

    def btc_point(self, day: date, target_minute: int) -> Optional[d1.BtcPoint]:
        start = self._utc(day, target_minute) - timedelta(minutes=4)
        end = self._utc(day, target_minute) + timedelta(minutes=1)
        payload = self._json("/data/v1beta3/crypto/us/bars", {
            "symbols": d1.BTC_SYMBOL,
            "timeframe": "1Min",
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": str(ROW_LIMIT),
        })
        if payload.get("next_page_token"):
            raise ValueError("day one BTC window unexpectedly paginated")
        rows = (payload.get("bars") or {}).get(d1.BTC_SYMBOL, [])
        if not isinstance(rows, list) or len(rows) > ROW_LIMIT:
            raise ValueError("day one BTC rows are invalid or oversized")
        return d1.select_btc_close(rows, day, target_minute)

    def _stock_daily(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        payload = self._json("/data/v2/stocks/bars", {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": datetime.combine(start, dtime(), timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": datetime.combine(end + timedelta(days=1), dtime(), timezone.utc).isoformat().replace("+00:00", "Z"),
            "limit": "100",
            "feed": "sip",
            "adjustment": "split",
        })
        rows = (payload.get("bars") or {}).get(symbol, [])
        if not isinstance(rows, list) or len(rows) > 100 or payload.get("next_page_token"):
            raise ValueError("day one daily bars are invalid or paginated")
        return rows

    @staticmethod
    def _daily_map(rows: Sequence[dict[str, Any]]) -> dict[date, dict[str, float]]:
        out: dict[date, dict[str, float]] = {}
        for row in rows:
            try:
                day = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).date()
                values = {key: float(row[key]) for key in ("o", "h", "l", "c")}
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("day one daily bar is invalid") from exc
            if day in out or not all(math.isfinite(v) and v > 0 for v in values.values()):
                raise ValueError("day one daily bar is duplicate or nonfinite")
            out[day] = values
        return out

    def spy_reference(self, target: date) -> float:
        self._begin_budget()
        rows = self.broker.get_calendar((target - timedelta(days=14)).isoformat(), target.isoformat())
        sessions = sorted(d1.parse_calendar(rows))
        prior = d1.prior_session(target, sessions)
        if prior is None:
            raise ValueError("SPY reference has no prior session")
        daily = self._daily_map(self._stock_daily("SPY", prior, prior))
        if prior not in daily:
            raise ValueError("SPY prior official close is unavailable")
        return daily[prior]["c"]

    def coin_model(self, session: date) -> Dict[str, Any]:
        self._begin_budget()
        calendar_rows = self.broker.get_calendar((session - timedelta(days=120)).isoformat(), session.isoformat())
        closes = d1.parse_calendar(calendar_rows)
        sessions = sorted(day for day in closes if day <= session)
        if session not in closes or len(sessions) < d1.BTC_MIN_HISTORY + 2:
            raise ValueError("COIN model calendar history is incomplete")
        index = sessions.index(session)

        def endpoints(current_index: int) -> tuple[Optional[d1.BtcPoint], Optional[d1.BtcPoint]]:
            current = sessions[current_index]
            previous = sessions[current_index - 1]
            anchor_minute = 12 * 60 + 59 if closes[previous] == d1.EARLY_CLOSE else 15 * 60 + 59
            return self.btc_point(previous, anchor_minute), self.btc_point(
                current, d1.BTC_DECISION_TARGET_MINUTE
            )

        today_anchor, today_decision = endpoints(index)
        if today_anchor is None or today_decision is None:
            raise ValueError("COIN current BTC endpoints are unavailable")
        today_move = d1.normalized_btc_move(today_anchor, today_decision)

        newest_first: list[float] = []
        candidate_index = index - 1
        while len(newest_first) < d1.BTC_HISTORY and candidate_index >= 1:
            with self._lock:
                remaining_calls = CALL_LIMIT - 1 - self._calls
                before_deadline = time.monotonic() <= self._deadline
            batch_size = min(4, candidate_index, remaining_calls // 2)
            if batch_size < 1 or not before_deadline:
                break
            batch = list(range(candidate_index, candidate_index - batch_size, -1))
            resolved: dict[int, tuple[Optional[d1.BtcPoint], Optional[d1.BtcPoint]]] = {}
            exhausted = False
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="day_one_data") as pool:
                jobs = {pool.submit(endpoints, current_index): current_index for current_index in batch}
                for future in as_completed(jobs):
                    try:
                        resolved[jobs[future]] = future.result()
                    except TimeoutError:
                        exhausted = True
            for current_index in batch:
                pair = resolved.get(current_index)
                if pair is None or pair[0] is None or pair[1] is None:
                    continue
                move = d1.normalized_btc_move(pair[0], pair[1])
                if math.isfinite(move):
                    newest_first.append(move)
                    if len(newest_first) == d1.BTC_HISTORY:
                        break
            candidate_index -= batch_size
            if exhausted:
                break

        moves = list(reversed(newest_first))
        decision_result = d1.coin_decision(moves, today_move)
        daily = self._daily_map(self._stock_daily("COIN", sessions[index - 2], sessions[index - 1]))
        prior = sessions[index - 1]
        two_back = sessions[index - 2]
        if prior not in daily or two_back not in daily:
            raise ValueError("COIN Rule 201 history is unavailable")
        return {
            "side": decision_result.side,
            "zmove": decision_result.zmove,
            "sigma": decision_result.sigma,
            "history_count": decision_result.history_count,
            "anchor": {"target_day": today_anchor.target_day.isoformat(),
                       "target_minute": today_anchor.target_minute,
                       "selected_at": today_anchor.selected_at.isoformat(), "close": today_anchor.close},
            "decision_point": {"target_day": today_decision.target_day.isoformat(),
                               "target_minute": today_decision.target_minute,
                               "selected_at": today_decision.selected_at.isoformat(), "close": today_decision.close},
            "prior_low": daily[prior]["l"],
            "close_two_back": daily[two_back]["c"],
            "prior_close": daily[prior]["c"],
        }
