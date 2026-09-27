"""backend/app/core/macro_calendar.py
Layer 4 (macro regime): scheduled macro releases the strategy must not trade into.

The calendar lives in backend/app/data/macro_calendar.json (operator-editable):
  {"events": [{"name": "FOMC decision", "date": "2026-10-28", "time_et": "14:00",
               "before_min": 30, "after_min": 30}, ...],
   "recurring": [{"name": "Nonfarm payrolls", "rule": "first_friday", "time_et": "08:30",
                  "before_min": 15, "after_min": 30}]}
An entry inside a blackout window rejects the signal with MACRO_BLACKOUT. A missing,
unreadable or invalid calendar, or a date past its `valid_through`, fails closed
(MACRO_UNAVAILABLE). Naive timestamps are treated as UTC, like everywhere else in the bot.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "macro_calendar.json"


def _first_friday(d: date) -> date:
    first = d.replace(day=1)
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset)


class MacroCalendar:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.events: List[Dict[str, Any]] = []
        self.recurring: List[Dict[str, Any]] = []
        self.loaded = False
        self.error: Optional[str] = None
        self.valid_through: Optional[date] = None
        self.valid_from: date = date.min
        self.reload()

    def reload(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or "events" not in data or "valid_through" not in data:
                raise ValueError("calendar needs 'events' and 'valid_through'")
            events = list(data.get("events", []))
            recurring = list(data.get("recurring", []))
            for ev in events:
                date.fromisoformat(str(ev["date"]))
                self._parse_time(ev.get("time_et"))
                if int(ev.get("before_min", 15)) < 0 or int(ev.get("after_min", 30)) < 0:
                    raise ValueError(f"negative blackout in {ev}")
                str(ev["name"])
            for ev in recurring:
                if ev.get("rule") not in ("first_friday",):
                    raise ValueError(f"unknown recurrence rule {ev.get('rule')!r}")
                self._parse_time(ev.get("time_et"))
                if int(ev.get("before_min", 15)) < 0 or int(ev.get("after_min", 30)) < 0:
                    raise ValueError(f"negative blackout in {ev}")
                str(ev["name"])
            self.valid_through = date.fromisoformat(str(data["valid_through"]))
            self.valid_from = date.fromisoformat(str(data.get("valid_from", date.min.isoformat())))
            if self.valid_from > self.valid_through:
                raise ValueError("calendar coverage dates reversed")
            self.events, self.recurring = events, recurring
            self.loaded = True
            self.error = None
        except Exception as exc:  # fail closed: every check reports unavailable
            self.events, self.recurring = [], []
            self.valid_through = None
            self.loaded = False
            self.error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _parse_time(value: Any) -> dtime:
        hh, mm = str(value).split(":")
        t = dtime(int(hh), int(mm))
        return t

    def windows_for(self, d: date) -> List[Tuple[datetime, datetime, str]]:
        out: List[Tuple[datetime, datetime, str]] = []
        for ev in self.events:
            try:
                if date.fromisoformat(ev["date"]) != d:
                    continue
                hh, mm = str(ev.get("time_et", "08:30")).split(":")
                at = datetime.combine(d, dtime(int(hh), int(mm)), ET)
                out.append((at - timedelta(minutes=int(ev.get("before_min", 15))),
                            at + timedelta(minutes=int(ev.get("after_min", 30))), str(ev.get("name", "macro"))))
            except Exception:
                continue
        for ev in self.recurring:
            try:
                rule = ev.get("rule")
                hit = rule == "first_friday" and d == _first_friday(d)
                if not hit:
                    continue
                hh, mm = str(ev.get("time_et", "08:30")).split(":")
                at = datetime.combine(d, dtime(int(hh), int(mm)), ET)
                out.append((at - timedelta(minutes=int(ev.get("before_min", 15))),
                            at + timedelta(minutes=int(ev.get("after_min", 30))), str(ev.get("name", "macro"))))
            except Exception:
                continue
        return out

    def check(self, when: datetime) -> Tuple[bool, str]:
        """(allowed, reason). Fail closed when the calendar could not be loaded."""
        if not self.loaded:
            return False, f"MACRO_UNAVAILABLE: {self.error}"
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        w = when.astimezone(ET)
        if self.valid_through is None or not self.valid_from <= w.date() <= self.valid_through:
            return False, f"MACRO_UNAVAILABLE: calendar valid {self.valid_from} through {self.valid_through}"
        for d in (w.date() - timedelta(days=1), w.date(), w.date() + timedelta(days=1)):
            for start, end, name in self.windows_for(d):
                if start <= w <= end:
                    return False, f"MACRO_BLACKOUT: {name} {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ET"
        return True, "MACRO_CLEAR"

    def today_text(self, d: date) -> str:
        ws = self.windows_for(d)
        if not self.loaded or self.valid_through is None or not self.valid_from <= d <= self.valid_through:
            return "Macro calendar unavailable: new trades blocked."
        if not ws:
            return "No scheduled macro releases today."
        return "Macro blackout: " + ", ".join(f"{n} {s.strftime('%-I:%M')}-{e.strftime('%-I:%M %p')}" for s, e, n in ws)


macro_calendar = MacroCalendar()
