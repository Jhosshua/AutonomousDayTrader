"""Shared helpers for the adversarial ORB tests (fake Alpaca only, never the network)."""
from __future__ import annotations

from backend.app.core.orb_execution import BudgetThrottled, RequestBudget
from backend.tests.unit.orb_execution.fakes import Harness, at, pick  # noqa: F401

DAY = "2026-09-28"
LIVE_LEG = {"new", "accepted", "held", "partially_filled", "pending_new", "pending_replace"}


def opened(sym="APP", entry_px=100.2, card=(100.0, 98.0), direction="long", **kw) -> Harness:
    h = Harness(**kw)
    h.alpaca.prices[sym] = entry_px
    out = h.ctl.execute([pick(sym, direction, *card)])
    assert out["ok"], out
    return h


def writes_after(h, n):
    return [(m, p, b) for m, p, b, _q in h.alpaca.requests[n:] if m in ("POST", "PATCH", "DELETE")]


def posts(h, n=0, side=None):
    return [b for m, p, b in writes_after(h, n) if m == "POST" and (side is None or b.get("side") == side)]


def bracket_posts(h, n=0):
    return [b for b in posts(h, n) if b.get("order_class") == "bracket"]


def live_stop_at_broker(h, sym="APP") -> bool:
    """A working protective stop for sym exists at the (fake) broker."""
    return any(o["symbol"] == sym and o["type"] in ("stop", "stop_limit") and o["status"] in LIVE_LEG
               for o in h.alpaca.orders.values())


def protected_or_flat(h, sym="APP") -> bool:
    return h.alpaca._pos_qty(sym) == 0 or live_stop_at_broker(h, sym)


class SwitchBudget(RequestBudget):
    """A budget that can be switched to 'exhausted' (every acquire refused) at any moment."""

    def __init__(self):
        super().__init__(10 ** 6, 10 ** 6)
        self.exhausted = False

    def acquire(self, prio="normal"):
        if self.exhausted:
            self.throttled += 1
            raise BudgetThrottled(f"ORB request budget: {prio} request skipped (test: exhausted)")
        return super().acquire(prio)
