"""Rate-limited warnings for display-only code that runs on every websocket frame (up to ~4 a second).

One persistent bug in a fail-soft display field must not flood the logs: the first failure per call site
logs the full traceback, later ones at most one short line every WINDOW_S with the count suppressed."""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable, Dict, Optional

WINDOW_S = 600.0
_lock = threading.Lock()
_sites: Dict[str, list] = {}          # key -> [last_logged_monotonic, suppressed_count]


def warn_rate_limited(log: logging.Logger, key: str, msg: str, *args,
                      now: Optional[Callable[[], float]] = None) -> bool:
    """Log a WARNING for `key`; return True when a line was written. Call from inside an except block
    (the traceback is attached on the first failure per key)."""
    t = (now or time.monotonic)()
    with _lock:
        site = _sites.get(key)
        if site is None:
            _sites[key] = [t, 0]
            first, suppressed = True, 0
        elif t - site[0] >= WINDOW_S:
            suppressed, site[0], site[1] = site[1], t, 0
            first = False
        else:
            site[1] += 1
            return False
    if first:
        log.warning(msg, *args, exc_info=True)
    else:
        log.warning(msg + " (again: %r; %d more since the last line, traceback logged the first time)",
                    *args, sys.exc_info()[1], suppressed)
    return True


def reset_for_tests() -> None:
    with _lock:
        _sites.clear()
