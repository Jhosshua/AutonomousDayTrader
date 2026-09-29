"""A fake Alpaca paper account driven by the RECORDED SIP tape (dry run only; never the real API).

Extends the phase-2 test fake (backend/tests/unit/orb_execution/fakes.py: /v2 orders, positions, account,
bracket parents with nested UUID legs, 422 on PATCH of a held leg, 403 when a sell does not fit):

  * market orders (bracket parents and plain exits) rest as "new" and fill at the FIRST regular-lot SIP
    print stamped after the order was accepted (fill price = that print);
  * after a parent fills, the take-profit leg becomes "new" and the stop-loss leg stays "held" (Alpaca's
    bracket behaviour: the stop waits behind the OCO), so a PATCH of the stop leg answers 422;
  * take-profit (limit) fills at its limit when a print reaches it; stop-loss triggers when a print
    crosses it and fills at the NEXT regular-lot print (slippage); the other leg is cancelled (OCO);
  * one sell order per position: a plain order that reduces a position must fit the shares not already
    held by working or HELD orders, else 403 (40310000), exactly like Alpaca;
  * position current_price = the last SIP print (any size) at the simulated now; equity = cash + marks;
    last_equity = the day-start equity (ADT 09-28 opening equity $49,702.10); buying power = 4x equity.
Odd-lot prints (condition I) never fill or trigger an order; they do move the displayed price, like
Alpaca's latest trade. Partial fills are not simulated."""
from __future__ import annotations

import bisect
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from backend.tests.unit.orb_execution.fakes import FakeAlpaca, TERMINAL  # noqa: E402

LIVE_LEG = ("new", "held", "accepted")


class _BrokerClock:
    """The simulated clock as the fake sees it; a fill is stamped with its print's time (override)."""

    def __init__(self, sim):
        self.sim = sim
        self.override = None

    @property
    def now(self):
        return self.override if self.override is not None else self.sim.now

    def now_ns(self) -> int:
        return self.sim.now_ns()


class TapeAlpaca(FakeAlpaca):
    def __init__(self, sim, tape, equity: float, start_ns: int):
        super().__init__(_BrokerClock(sim), equity, last_equity=equity, buying_power=4 * equity)
        self.tape = tape
        self.cash = float(equity)
        self.day_start_equity = float(equity)
        self.processed_ns = start_ns
        self.entry_mode = "new"
        self.market_fills = False
        self._prints: Dict[str, tuple] = {}
        self.fill_log: List[dict] = []
        self.write_log: List[dict] = []
        self.lock = threading.RLock()
        self.kill_on_post: Optional[Callable[[dict], None]] = None
        self.virtual_ns = 0          # extra "broker time" granted by the controller's confirm sleeps

    # ------------------------------------------------------------------ tape access
    def now_ns(self) -> int:
        return self.clock.now_ns()

    def prints(self, sym):
        if sym not in self._prints:
            self._prints[sym] = self.tape.prints(sym)
        return self._prints[sym]

    def price(self, sym: str) -> float:
        row = self.tape.last_trade(sym, self.now_ns())
        if row is None:
            raise RuntimeError(f"no print for {sym} yet")
        return float(row["p"])

    # ------------------------------------------------------------------ account
    def _apply_position(self, sym, side, qty, px):
        self.cash += (-qty * px) if side == "buy" else (qty * px)
        super()._apply_position(sym, side, qty, px)

    def equity(self) -> float:
        return self.cash + sum(p["qty"] * self.price(s) for s, p in self.positions.items())

    def _route(self, method, path, body, params):
        if path == "/v2/account":
            eq = round(self.equity(), 2)
            acct = dict(self.account, equity=str(eq), cash=str(round(self.cash, 2)),
                        last_equity=str(self.day_start_equity), buying_power=str(round(4 * eq, 2)))
            return httpx.Response(200, json=acct)
        return super()._route(method, path, body, params)

    def _reserved_for_exit(self, sym, side):
        n = 0
        for r in self.orders.values():
            if r["symbol"] == sym and r["side"] == side and r["status"] in (
                    "new", "accepted", "partially_filled", "pending_cancel", "held"):
                n += int(r["qty"]) - int(r["filled_qty"])
        return n

    # ------------------------------------------------------------------ transport
    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            resp = super().handler(request)
            if request.method in ("POST", "PATCH", "DELETE"):
                body = json.loads(request.content) if request.content else None
                self.write_log.append({"sim_ns": self.now_ns(), "method": request.method, "path": request.url.path,
                                       "body": body, "status": resp.status_code})
            return resp

    def _post(self, body):
        resp = super()._post(body)
        if resp.status_code in (200, 201):
            row = self.by_coid(body["client_order_id"])
            row["accepted_ns"] = self.now_ns() + self.virtual_ns
            if body.get("order_class") == "bracket" and self.kill_on_post is not None:
                self.kill_on_post(row)
        return resp

    def fill(self, oid, cum_qty=None, price=None):
        row = super().fill(oid, cum_qty, price)
        if row.get("legs_ids") and row["status"] == "filled":
            for lid in row["legs_ids"]:
                leg = self.orders[lid]
                if leg["status"] == "new" and leg["type"] in ("stop", "stop_limit"):
                    leg["status"] = "held"          # Alpaca: the stop leg stays held behind the OCO
        return row

    def view(self, row, nested=True):
        out = super().view(row, nested)
        out.pop("accepted_ns", None)
        out.pop("triggered_ns", None)
        out.pop("fill_ns", None)
        return out

    # ------------------------------------------------------------------ matching engine
    def _next_regular(self, sym, after_ns, until_ns):
        st, px, _sz, odd = self.prints(sym)
        k = bisect.bisect_right(st, after_ns)
        while k < len(st) and st[k] <= until_ns:
            if not odd[k]:
                return k
            k += 1
        return None

    def _fill_at(self, oid, k, sym, note):
        st, px, _sz, _odd = self.prints(sym)
        self.clock.override = datetime.fromtimestamp(st[k] / 1e9, timezone.utc)   # filled_at = print time
        try:
            row = self.fill(oid, price=px[k])
        finally:
            self.clock.override = None
        row["fill_ns"] = st[k]
        self.fill_log.append({"order_id": oid, "coid": row.get("client_order_id"), "symbol": sym,
                              "side": row["side"], "qty": int(row["qty"]), "price": px[k], "print_ns": st[k],
                              "type": row["type"], "leg": bool(row.get("parent_id")), "note": note})

    def fill_market(self, until_ns: int) -> int:
        """Fill resting market orders at the first regular print after acceptance, up to until_ns."""
        n = 0
        for oid in list(self.order_seq):
            r = self.orders[oid]
            if r["type"] != "market" or r["status"] not in ("new", "accepted", "partially_filled"):
                continue
            k = self._next_regular(r["symbol"], r.get("accepted_ns", 0), until_ns)
            if k is not None:
                self._fill_at(oid, k, r["symbol"], "market")
                n += 1
        return n

    def advance(self, until_ns: int) -> None:
        """Process the tape (processed_ns, until_ns]: market fills, then bracket legs print by print."""
        with self.lock:
            self.fill_market(until_ns)
            for oid in list(self.order_seq):
                parent = self.orders[oid]
                if not parent.get("legs_ids") or parent["status"] != "filled":
                    continue
                legs = [self.orders[l] for l in parent["legs_ids"]]
                tp = next((l for l in legs if l["type"] == "limit" and l["status"] in LIVE_LEG), None)
                sl = next((l for l in legs if l["type"] in ("stop", "stop_limit") and l["status"] in LIVE_LEG), None)
                if tp is None and sl is None:
                    continue
                sym = parent["symbol"]
                long_pos = parent["side"] == "buy"
                st, px, _sz, odd = self.prints(sym)
                start = max(self.processed_ns, int(parent.get("fill_ns") or 0))
                k = bisect.bisect_right(st, start)
                while k < len(st) and st[k] <= until_ns:
                    if odd[k]:
                        k += 1
                        continue
                    p = px[k]
                    if sl is not None and sl.get("triggered_ns"):
                        self._fill_at(sl["id"], k, sym, "stop (next print after the trigger)")
                        break
                    if tp is not None and ((long_pos and p >= float(tp["limit_price"])) or
                                           (not long_pos and p <= float(tp["limit_price"]))):
                        self._fill_limit(tp, k, sym)
                        break
                    if sl is not None and ((long_pos and p <= float(sl["stop_price"])) or
                                           (not long_pos and p >= float(sl["stop_price"]))):
                        sl["triggered_ns"] = st[k]
                    k += 1
            self.processed_ns = max(self.processed_ns, until_ns)
            self.virtual_ns = 0

    def _fill_limit(self, leg, k, sym):
        st, _px, _sz, _odd = self.prints(sym)
        self.clock.override = datetime.fromtimestamp(st[k] / 1e9, timezone.utc)
        try:
            self.fill(leg["id"], price=float(leg["limit_price"]))
        finally:
            self.clock.override = None
        leg["fill_ns"] = st[k]
        self.fill_log.append({"order_id": leg["id"], "coid": leg.get("client_order_id"), "symbol": sym,
                              "side": leg["side"], "qty": int(leg["qty"]), "price": float(leg["limit_price"]),
                              "print_ns": st[k], "type": "limit", "leg": True, "note": "take profit"})

    def confirm_sleep(self, seconds: float) -> None:
        """The controller's exit-confirm sleep: broker time moves on (market orders may fill), the
        driver's clock does not. Legs are processed only by advance()."""
        with self.lock:
            self.virtual_ns += int(seconds * 1e9)
            self.fill_market(self.now_ns() + self.virtual_ns)

    # ------------------------------------------------------------------ persistence (restart tests)
    def dump(self, path: str) -> None:
        with self.lock:
            state = {"orders": self.orders, "order_seq": self.order_seq, "positions": self.positions,
                     "cash": self.cash, "day_start_equity": self.day_start_equity,
                     "processed_ns": self.processed_ns, "fill_log": self.fill_log, "write_log": self.write_log,
                     "refused_403": self.refused_403, "n_requests": len(self.requests)}
            with open(path + ".tmp", "w") as f:
                json.dump(state, f)
            os.replace(path + ".tmp", path)

    def load(self, path: str) -> None:
        with open(path) as f:
            s = json.load(f)
        self.orders, self.order_seq, self.positions = s["orders"], s["order_seq"], s["positions"]
        self.cash, self.day_start_equity = s["cash"], s["day_start_equity"]
        self.processed_ns = s["processed_ns"]
        self.fill_log, self.write_log, self.refused_403 = s["fill_log"], s["write_log"], s["refused_403"]
