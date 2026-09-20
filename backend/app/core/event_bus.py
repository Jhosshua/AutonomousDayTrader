"""backend/app/core/event_bus.py
Asynchronous pub/sub event bus with typed subscribers and fault isolation.
"""
from __future__ import annotations
import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, List, Type, TypeVar, Union

log = logging.getLogger("EventBus")

T = TypeVar("T")
AsyncHandler = Callable[[T], Coroutine[Any, Any, None]]
SyncHandler = Callable[[T], None]
HandlerFunc = Union[AsyncHandler, SyncHandler]


class EventBus:
    """Asynchronous pub/sub event bus with typed subscribers and subscriber error isolation."""

    def __init__(self) -> None:
        self._subscribers: Dict[Type, List[HandlerFunc]] = defaultdict(list)
        self._published_count: int = 0
        self._error_count: int = 0

    def subscribe(self, event_type: Type[T], handler: HandlerFunc) -> None:
        """Register a callback (async or sync) for a specific event type."""
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)
            handler_name = getattr(handler, "__name__", str(handler))
            log.debug(f"Subscribed {handler_name} to {event_type.__name__}")

    def unsubscribe(self, event_type: Type[T], handler: HandlerFunc) -> None:
        """Remove a registered callback."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    def clear(self) -> None:
        """Clear all subscribers and reset counts."""
        self._subscribers.clear()
        self._published_count = 0
        self._error_count = 0

    async def publish(self, event: Any) -> None:
        """Publish an event to all registered subscribers with fault isolation."""
        event_type = type(event)
        # Also check for base class matches or direct type matches
        handlers: List[HandlerFunc] = []
        for reg_type, reg_handlers in self._subscribers.items():
            if isinstance(event, reg_type):
                handlers.extend(reg_handlers)

        if not handlers:
            return

        self._published_count += 1
        tasks = [self._safe_dispatch(handler, event) for handler in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_dispatch(self, handler: HandlerFunc, event: Any) -> None:
        """Execute subscriber within an isolated try/except block."""
        handler_name = getattr(handler, "__name__", str(handler))
        try:
            if inspect.iscoroutinefunction(handler):
                await handler(event)
            else:
                res = handler(event)
                if inspect.isawaitable(res):
                    await res
        except Exception as exc:
            self._error_count += 1
            log.exception(
                f"Exception in subscriber {handler_name} processing {type(event).__name__}: {exc}"
            )

    @property
    def metrics(self) -> Dict[str, Any]:
        return {
            "published_count": self._published_count,
            "error_count": self._error_count,
            "subscriber_counts": {k.__name__: len(v) for k, v in self._subscribers.items()},
        }


# Global singleton event bus instance
event_bus = EventBus()
