"""backend/app/strategies/earnings_calendar.py
48-Hour Earnings Blackout Window & Next-Day Exit Calendar Service.

Rule 4 Requirements:
1. 48-Hour Blackout Window:
   - If a company reports earnings within 48 hours of evaluation/entry, veto entry.
2. Holding Exit Rule:
   - If holding an active position and the company reports earnings tomorrow (next trading day),
     stage a SELL order to exit at Market Open (09:30 ET).
3. Graceful Fallback:
   - Seeded from `backend/app/data/earnings_calendar.json`.
   - Any remote provider failures or network timeouts gracefully degrade to local seed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger(__name__)


@dataclass
class EarningsEvent:
    """Scheduled earnings report event."""
    symbol: str
    report_date: date
    report_time: str = "amc"          # "bmo" (before market open), "amc" (after market close), "unknown"
    fiscal_quarter: Optional[str] = None
    confirmed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "report_date": self.report_date.isoformat(),
            "report_time": self.report_time,
            "fiscal_quarter": self.fiscal_quarter,
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EarningsEvent:
        d = data["report_date"]
        if isinstance(d, str):
            d_obj = datetime.strptime(d[:10], "%Y-%m-%d").date()
        elif isinstance(d, datetime):
            d_obj = d.date()
        else:
            d_obj = d

        return cls(
            symbol=str(data["symbol"]).upper(),
            report_date=d_obj,
            report_time=str(data.get("report_time", "amc")).lower(),
            fiscal_quarter=data.get("fiscal_quarter"),
            confirmed=bool(data.get("confirmed", True)),
        )


class EarningsCalendar:
    """Manages earnings dates with 48h blackout verification and graceful seed fallback."""

    def __init__(
        self,
        seed_path: Optional[str] = None,
        remote_url: Optional[str] = None,
        cache_path: Optional[str] = None,
    ) -> None:
        self.remote_url: Optional[str] = remote_url
        self.cache_path: Optional[str] = cache_path
        self._events: Dict[str, List[EarningsEvent]] = {}

        if seed_path:
            self.load_seed_file(seed_path)
        if cache_path and Path(cache_path).is_file():
            self.load_seed_file(cache_path)

    def load_seed_file(self, seed_path: str) -> int:
        """Load seed JSON fixture.
        
        Supports both:
          1) {"SYMBOL": [event_dicts, ...]}
          2) [event_dicts_with_symbol, ...]
        """
        path = Path(seed_path)
        if not path.is_file():
            log.warning(f"Earnings calendar seed file not found: {seed_path}")
            return 0

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        count = 0
        if isinstance(raw, dict):
            for sym, event_list in raw.items():
                parsed: List[EarningsEvent] = []
                for item in event_list:
                    if "symbol" not in item:
                        item["symbol"] = sym
                    parsed.append(EarningsEvent.from_dict(item))
                parsed.sort(key=lambda e: e.report_date)
                self._events[sym.upper()] = parsed
                count += len(parsed)
        elif isinstance(raw, list):
            by_sym: Dict[str, List[EarningsEvent]] = {}
            for item in raw:
                ev = EarningsEvent.from_dict(item)
                by_sym.setdefault(ev.symbol.upper(), []).append(ev)
            for sym, parsed in by_sym.items():
                parsed.sort(key=lambda e: e.report_date)
                self._events[sym.upper()] = parsed
                count += len(parsed)

        log.info(f"Loaded {count} earnings events across {len(self._events)} symbols from {seed_path}")
        return count

    def add_event(self, event: EarningsEvent) -> None:
        """Add or update an earnings event for a symbol."""
        sym = event.symbol.upper()
        existing = self._events.setdefault(sym, [])
        for idx, item in enumerate(existing):
            if item.report_date == event.report_date:
                existing[idx] = event
                return
        existing.append(event)
        existing.sort(key=lambda e: e.report_date)

    def get_events(self, symbol: str) -> List[EarningsEvent]:
        """Return all scheduled earnings events for a symbol, sorted chronologically."""
        return list(self._events.get(symbol.upper(), []))

    def get_next_earnings(
        self,
        symbol: str,
        as_of: Union[date, datetime],
    ) -> Optional[EarningsEvent]:
        """Return the next scheduled earnings announcement on or after as_of."""
        as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
        events = self._events.get(symbol.upper(), [])
        for ev in events:
            if ev.report_date >= as_of_date:
                return ev
        return None

    def is_blackout_active(
        self,
        symbol: str,
        as_of: Union[date, datetime],
        horizon_hours: float = 48.0,
    ) -> bool:
        """Check if symbol reports earnings within horizon_hours (default 48 hours).
        
        Rule 4 (Entry Veto):
        If company reports earnings within the next 48 hours, do not enter.
        
        Evaluated either by datetime window or trading date range.
        If evaluated on Day t at 16:00 close for Day t+1 09:30 open:
        Earnings falling on Day t, Day t+1, or Day t+2 are within the 48-hour window.
        """
        events = self._events.get(symbol.upper(), [])
        if not events:
            return False

        if isinstance(as_of, datetime):
            as_of_dt = as_of
            for ev in events:
                # Estimate report timestamp in local ET
                if ev.report_time == "bmo":
                    report_dt = datetime.combine(ev.report_date, datetime.min.time()).replace(hour=8, minute=30)
                elif ev.report_time == "amc":
                    report_dt = datetime.combine(ev.report_date, datetime.min.time()).replace(hour=16, minute=30)
                else:
                    report_dt = datetime.combine(ev.report_date, datetime.min.time()).replace(hour=9, minute=30)

                # Attach tzinfo if as_of is timezone-aware
                if as_of_dt.tzinfo is not None:
                    report_dt = report_dt.replace(tzinfo=as_of_dt.tzinfo)

                diff_seconds = (report_dt - as_of_dt).total_seconds()
                # Past reports (e.g. BMO 08:30 evaluated at 16:00 close) NEVER trigger a blackout
                if diff_seconds < 0:
                    continue

                # Friday evaluation horizon extends across weekend to Monday/Tuesday earnings
                effective_horizon = horizon_hours
                if as_of_dt.weekday() == 4:
                    effective_horizon = max(horizon_hours, 96.0)

                if 0 <= diff_seconds <= effective_horizon * 3600.0:
                    return True

                # If report date is within calendar days, enforce safe-side blackout for upcoming events
                diff_days = (ev.report_date - as_of_dt.date()).days
                max_days = 4 if as_of_dt.weekday() == 4 else 2
                if 0 < diff_days <= max_days:
                    return True

        else:
            as_of_date = as_of
            for ev in events:
                diff_days = (ev.report_date - as_of_date).days
                if diff_days < 0:
                    continue
                if diff_days == 0 and ev.report_time == "bmo":
                    continue
                max_days = 4 if as_of_date.weekday() == 4 else 2
                if 0 <= diff_days <= max_days:
                    return True

        return False


    def has_earnings_tomorrow(
        self,
        symbol: str,
        as_of: Union[date, datetime],
    ) -> bool:
        """Check if symbol reports earnings on the next trading day.
        
        Rule 4b (Holding Exit):
        If holding an active position and company reports earnings tomorrow,
        trigger sell at Market Open (09:30 ET).
        """
        as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
        events = self._events.get(symbol.upper(), [])
        if not events:
            return False

        # Next trading day: 1 day if Mon-Thu; 3 days if Friday
        weekday = as_of_date.weekday()  # Monday = 0, Friday = 4
        if weekday == 4:
            next_trading_day = as_of_date + timedelta(days=3)
        elif weekday == 5:
            next_trading_day = as_of_date + timedelta(days=2)
        else:
            next_trading_day = as_of_date + timedelta(days=1)

        # The exit fills at the next 09:30 open. A before-open (BMO) report on the trading day
        # AFTER that would already be out by the following open, so it must exit one day earlier.
        day_after_next = next_trading_day + timedelta(days=1)
        while day_after_next.weekday() >= 5:
            day_after_next += timedelta(days=1)

        for ev in events:
            if ev.report_date == next_trading_day:
                return True
            # Also catch standard 1-day calendar delta
            if (ev.report_date - as_of_date).days == 1:
                return True
            if ev.report_time == "bmo" and ev.report_date == day_after_next:
                return True

        return False

    def save_cache_file(self, file_path: Optional[str] = None) -> None:
        """Atomically persist current earnings calendar to a local JSON cache file."""
        target = file_path or self.cache_path
        if not target:
            return
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        serialized = {
            sym: [ev.to_dict() for ev in events]
            for sym, events in self._events.items()
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=2)
        tmp_path.replace(path)
        log.info(f"Durable earnings calendar saved to {path} ({len(self._events)} symbols)")

    async def refresh_from_remote(self) -> bool:
        """Attempt to refresh earnings calendar from remote provider using non-blocking async HTTP.
        
        Graceful fallback invariant:
        Any exception, timeout, or missing URL is logged and safely returns False
        without raising unhandled errors or corrupting local cache.
        """
        if not self.remote_url:
            return False

        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    self.remote_url,
                    headers={"User-Agent": "AutonomousDayTrader/1.0"},
                )
                if resp.status_code != 200:
                    log.warning(f"Remote earnings provider returned HTTP {resp.status_code}")
                    return False
                data = resp.json()
                count = 0
                if isinstance(data, dict):
                    for sym, evs in data.items():
                        for item in evs:
                            if "symbol" not in item:
                                item["symbol"] = sym
                            self.add_event(EarningsEvent.from_dict(item))
                            count += 1
                elif isinstance(data, list):
                    for item in data:
                        self.add_event(EarningsEvent.from_dict(item))
                        count += 1
                log.info(f"Successfully refreshed {count} earnings events from {self.remote_url}")
                if self.cache_path:
                    self.save_cache_file(self.cache_path)
                return True
        except Exception as exc:
            log.warning(f"Remote earnings refresh failed ({exc}); continuing with cached seed calendar")
            return False

    def clear(self) -> None:
        """Clear all calendar events."""
        self._events.clear()
