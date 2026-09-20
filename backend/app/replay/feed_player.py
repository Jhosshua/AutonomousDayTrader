"""
Deterministic Feed Player & Market Clock Simulator.

Loads historical captures or synthetic session fixtures (such as monday_open_session.json)
and plays them through the MockAlpacaRelayServer with configurable clock multiplier:
- 1x: Wall-clock live speed (for live dry runs & UI audits)
- 2x, 5x, 10x: Accelerated playback for rapid CI testing
- Step Mode: Deterministic step-by-step advance on trigger
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("feed_player")


class FeedPlayer:
    """Orchestrates market event playback into MockAlpacaRelayServer."""

    def __init__(
        self,
        relay_server: Any,
        speed: float = 1.0,
        on_event_dispatched: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.relay_server = relay_server
        self.speed = max(0.1, float(speed))
        self.on_event_dispatched = on_event_dispatched

        self.events: List[Dict[str, Any]] = []
        self.current_index: int = 0
        self.is_playing: bool = False
        self._stop_requested: bool = False

    def load_events(self, events: List[Dict[str, Any]]) -> None:
        """Load a sequence of chronological events."""
        self.events = list(events)
        self.current_index = 0
        self._stop_requested = False

    def load_fixture_file(self, file_path: str | Path) -> None:
        """Load fixture events from a JSON file."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Fixture file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                self.load_events(data)
            elif isinstance(data, dict) and "events" in data:
                self.load_events(data["events"])
            else:
                raise ValueError("Fixture file must contain a list of events")

    async def step_next(self) -> Optional[Dict[str, Any]]:
        """Dispatch exactly one event deterministically."""
        if self.current_index >= len(self.events):
            return None

        event = self.events[self.current_index]
        self.current_index += 1

        await self._dispatch(event)
        if self.on_event_dispatched:
            self.on_event_dispatched(event)

        return event

    async def step_all(self) -> int:
        """Dispatch all remaining events immediately without delay."""
        count = 0
        while self.current_index < len(self.events):
            await self.step_next()
            count += 1
        return count

    async def play(self) -> None:
        """Play remaining events at configured playback speed (1x to 10x)."""
        self.is_playing = True
        self._stop_requested = False

        try:
            prev_ts: Optional[datetime] = None

            while self.current_index < len(self.events) and not self._stop_requested:
                event = self.events[self.current_index]
                ts_str = event.get("timestamp")

                if ts_str and self.speed > 0:
                    try:
                        curr_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if prev_ts is not None:
                            delta_sec = max(0.0, (curr_ts - prev_ts).total_seconds())
                            delay = delta_sec / self.speed
                            # Cap delay to 3.0s in tests to avoid excessive waits
                            delay = min(3.0, delay)
                            if delay > 0.001:
                                await asyncio.sleep(delay)
                        prev_ts = curr_ts
                    except Exception:
                        pass

                await self.step_next()
        finally:
            self.is_playing = False

    def stop(self) -> None:
        """Halt playback loop."""
        self._stop_requested = True
        self.is_playing = False
        logger.info("FeedPlayer playback halted.")

    async def _dispatch(self, event: Dict[str, Any]) -> None:
        """Dispatch event to mock relay server."""
        ev_type = event.get("type")
        data = event.get("data", event)

        if ev_type == "bar" or data.get("T") == "b":
            await self.relay_server.broadcast_bar(data)
        elif ev_type == "quote" or data.get("T") == "q":
            await self.relay_server.broadcast_quote(data)
        elif ev_type == "trade" or data.get("T") == "t":
            await self.relay_server.broadcast_trade(data)
        elif ev_type == "news" or data.get("T") == "n":
            await self.relay_server.broadcast_news(data)
        elif ev_type == "vix":
            v_val = float(data.get("value", 18.0))
            self.relay_server.set_vix(v_val)
        elif ev_type == "relay":
            await self.relay_server.broadcast_relay_status(data.get("msg", "upstream_connected"))
        elif ev_type == "control":
            # Session marker (e.g. phase transition)
            logger.info(f"[Market Clock] {event.get('phase')}: {event.get('description')}")
