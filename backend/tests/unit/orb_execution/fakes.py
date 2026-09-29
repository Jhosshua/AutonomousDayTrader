"""In-memory Alpaca paper API + fake ORB facade + fake clock for the ORB execution tests.

No network: the real AlpacaBroker talks to FakeAlpaca through httpx.MockTransport.
Prices, fills and times are synthetic; they prove controller behaviour, not market fills.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.broker import AlpacaBroker
from backend.app.core.orb_execution import ET, OrbExecutionController, RequestBudget

OPEN_STATES = {"new", "accepted", "partially_filled", "held", "pending_new", "pending_cancel", "pending_replace"}
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
DAY = datetime(2026, 9, 28, tzinfo=ET)


def at(h, m, s=0, day=DAY):
    return day.replace(hour=h, minute=m, second=s)


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now
        self.slept = 0.0

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, sec: float) -> None:
        self.slept += sec
        self.now = self.now + timedelta(seconds=sec)

    def set(self, now: datetime) -> None:
        self.now = now

    def advance(self, sec: float) -> None:
        self.now = self.now + timedelta(seconds=sec)


class FakeAlpaca:
    """Alpaca /v2 orders, positions and account, with bracket parents and nested UUID legs."""

    def __init__(self, clock: FakeClock, equity: float = 50000.0, last_equity: Optional[float] = None,
                 buying_power: float = 200000.0):
        self.clock = clock
        self.orders: Dict[str, dict] = {}
        self.order_seq: List[str] = []
        self.positions: Dict[str, dict] = {}
        self.prices: Dict[str, float] = {}
        self.account = {"account_number": "PA3CSVDZMMPY", "equity": str(equity),
                        "last_equity": str(equity if last_equity is None else last_equity),
                        "buying_power": str(buying_power), "trading_blocked": False, "account_blocked": False}
        self.requests: List[tuple] = []
        self.entry_mode = "filled"     # bracket parent on POST: filled | partial | new
        self.partial_qty: Optional[int] = None
        self.market_fills = True       # plain market orders fill at once
        self.fail: List[dict] = []     # {"method","path","kind": transport|lost|status, "status", "times"}
        self.fill_on_cancel: Dict[str, tuple] = {}   # oid -> (cum_qty, price): a fill races the cancel
        self.cancel_pending: Dict[str, int] = {}     # oid -> GETs that still show pending_cancel
        self.refused_403: List[dict] = []

    # ------------------------------------------------------------------ helpers
    @property
    def writes(self) -> List[tuple]:
        return [r for r in self.requests if r[0] in ("POST", "PATCH", "DELETE")]

    def _new_id(self) -> str:
        return str(uuid.uuid4())

    def price(self, sym: str) -> float:
        return float(self.prices.get(sym, 100.0))

    def _pos_qty(self, sym: str) -> int:
        return int((self.positions.get(sym) or {}).get("qty", 0))

    def _apply_position(self, sym: str, side: str, qty: int, px: float) -> None:
        p = self.positions.setdefault(sym, {"qty": 0, "avg": 0.0})
        signed = qty if side == "buy" else -qty
        q = p["qty"]
        if q == 0 or (q > 0) == (signed > 0):
            p["avg"] = (abs(q) * p["avg"] + qty * px) / (abs(q) + qty)
        elif abs(signed) > abs(q):
            p["avg"] = px
        p["qty"] = q + signed
        if p["qty"] == 0:
            self.positions.pop(sym)

    def fill(self, oid: str, cum_qty: Optional[int] = None, price: Optional[float] = None) -> dict:
        """Fill an order up to cum_qty (cumulative) at price. Terminal when fully filled."""
        row = self.orders[oid]
        total = int(row["qty"])
        cum = total if cum_qty is None else cum_qty
        old = int(row["filled_qty"])
        if cum <= old:
            return row
        px = self.price(row["symbol"]) if price is None else price
        old_avg = float(row.get("filled_avg_price") or 0.0)
        new = cum - old
        row["filled_avg_price"] = str(round((old * old_avg + new * px) / cum, 6))
        row["filled_qty"] = str(cum)
        row["status"] = "filled" if cum >= total else "partially_filled"
        row["filled_at"] = self.clock.now.isoformat()
        self._apply_position(row["symbol"], row["side"], new, px)
        if row.get("legs_ids") and row["status"] == "filled":
            for lid in row["legs_ids"]:
                if self.orders[lid]["status"] == "held":
                    self.orders[lid]["status"] = "new"
        if row.get("parent_id") and row["status"] == "filled":
            for lid in self.orders[row["parent_id"]]["legs_ids"]:
                if lid != oid and self.orders[lid]["status"] not in TERMINAL:
                    self.orders[lid]["status"] = "canceled"     # OCO: the sibling goes
        return row

    def leg(self, parent_id: str, kind: str) -> dict:
        for lid in self.orders[parent_id]["legs_ids"]:
            r = self.orders[lid]
            if (kind == "tp" and r["type"] == "limit") or (kind == "sl" and r["type"] in ("stop", "stop_limit")):
                if r["status"] not in ("replaced",):
                    return r
        raise KeyError(kind)

    def by_coid(self, coid: str) -> Optional[dict]:
        return next((o for o in self.orders.values() if o.get("client_order_id") == coid), None)

    def view(self, row: dict, nested: bool = True) -> dict:
        out = {k: v for k, v in row.items() if k not in ("legs_ids", "parent_id")}
        if nested and row.get("legs_ids"):
            out["legs"] = [self.view(self.orders[lid], False) for lid in row["legs_ids"]]
        else:
            out["legs"] = None
        return out

    def _fail_rule(self, method: str, path: str) -> Optional[dict]:
        for rule in self.fail:
            if rule["method"] == method and path.startswith(rule["path"]) and rule.get("times", 1) > 0:
                rule["times"] = rule.get("times", 1) - 1
                return rule
        return None

    # ------------------------------------------------------------------ transport
    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        self.requests.append((method, path, body, dict(request.url.params)))
        rule = self._fail_rule(method, path)
        if rule and rule["kind"] == "transport":
            raise httpx.ConnectError("simulated network failure", request=request)
        if rule and rule["kind"] == "status":
            return httpx.Response(rule["status"], json={"message": rule.get("message", "simulated")})
        resp = self._route(method, path, body, request.url.params)
        if rule and rule["kind"] == "lost":
            raise httpx.ReadTimeout("simulated lost reply", request=request)
        return resp

    def _route(self, method: str, path: str, body: Any, params) -> httpx.Response:
        nested = params.get("nested") == "true"
        if path == "/v2/account":
            return httpx.Response(200, json=self.account)
        if path == "/v2/positions" and method == "GET":
            return httpx.Response(200, json=[self._pos_view(s) for s in self.positions])
        if path.startswith("/v2/positions/"):
            sym = path.rsplit("/", 1)[1]
            if sym not in self.positions:
                return httpx.Response(404, json={"code": 40410000, "message": "position does not exist"})
            return httpx.Response(200, json=self._pos_view(sym))
        if path == "/v2/orders:by_client_order_id":
            row = self.by_coid(params["client_order_id"])
            return httpx.Response(200, json=self.view(row, nested)) if row else httpx.Response(404, json={"message": "not found"})
        if path == "/v2/orders" and method == "POST":
            return self._post(body)
        if path == "/v2/orders" and method == "GET":
            status, syms = params.get("status", "open"), params.get("symbols")
            rows = []
            for oid in self.order_seq:
                r = self.orders[oid]
                if r.get("parent_id") and nested:
                    continue
                if syms and r["symbol"] not in syms.split(","):
                    continue
                live = r["status"] in OPEN_STATES
                if (status == "open" and live) or (status == "closed" and not live) or status == "all":
                    rows.append(self.view(r, nested))
            return httpx.Response(200, json=rows)
        if path.startswith("/v2/orders/"):
            oid = path.rsplit("/", 1)[1]
            row = self.orders.get(oid)
            if row is None:
                return httpx.Response(404, json={"message": "order not found"})
            if method == "GET":
                if oid in self.cancel_pending:
                    self.cancel_pending[oid] -= 1
                    if self.cancel_pending[oid] <= 0:
                        self.cancel_pending.pop(oid)
                        if row["status"] == "pending_cancel":
                            self._cancel(row)
                return httpx.Response(200, json=self.view(row, nested))
            if method == "DELETE":
                return self._delete(row)
            if method == "PATCH":
                return self._patch(row, body)
        return httpx.Response(404, json={"message": f"no route {method} {path}"})

    def _pos_view(self, sym: str) -> dict:
        p = self.positions[sym]
        return {"symbol": sym, "qty": str(p["qty"]), "side": "long" if p["qty"] > 0 else "short",
                "avg_entry_price": str(p["avg"]), "current_price": str(self.price(sym))}

    def _reserved_for_exit(self, sym: str, side: str) -> int:
        n = 0
        for r in self.orders.values():
            if r["symbol"] == sym and r["side"] == side and r["status"] in ("new", "accepted", "partially_filled", "pending_cancel"):
                n += int(r["qty"]) - int(r["filled_qty"])
        return n

    def _post(self, body: dict) -> httpx.Response:
        if self.by_coid(body["client_order_id"]):
            return httpx.Response(422, json={"code": 40010001, "message": "client_order_id must be unique"})
        sym, side, qty = body["symbol"], body["side"], int(body["qty"])
        now = self.clock.now.isoformat()
        oid = self._new_id()
        row = {"id": oid, "client_order_id": body["client_order_id"], "symbol": sym, "side": side,
               "qty": str(qty), "type": body["type"], "time_in_force": body["time_in_force"],
               "order_class": body.get("order_class", "simple"), "status": "new", "filled_qty": "0",
               "filled_avg_price": None, "created_at": now, "replaced_by": None}
        if body.get("order_class") == "bracket":
            opp = "sell" if side == "buy" else "buy"
            tp_id, sl_id = self._new_id(), self._new_id()
            self.orders[tp_id] = {"id": tp_id, "client_order_id": str(uuid.uuid4()), "symbol": sym, "side": opp,
                                  "qty": str(qty), "type": "limit", "limit_price": body["take_profit"]["limit_price"],
                                  "time_in_force": "day", "order_class": "bracket", "status": "held",
                                  "filled_qty": "0", "filled_avg_price": None, "created_at": now,
                                  "parent_id": oid, "replaced_by": None}
            self.orders[sl_id] = {"id": sl_id, "client_order_id": str(uuid.uuid4()), "symbol": sym, "side": opp,
                                  "qty": str(qty), "type": "stop", "stop_price": body["stop_loss"]["stop_price"],
                                  "time_in_force": "day", "order_class": "bracket", "status": "held",
                                  "filled_qty": "0", "filled_avg_price": None, "created_at": now,
                                  "parent_id": oid, "replaced_by": None}
            row["legs_ids"] = [tp_id, sl_id]
            self.orders[oid] = row
            self.order_seq += [oid, tp_id, sl_id]
            if self.entry_mode == "filled":
                self.fill(oid)
            elif self.entry_mode == "partial":
                self.fill(oid, self.partial_qty or max(1, qty // 2))
            return httpx.Response(200, json=self.view(row, True))
        # a plain order that reduces a position must fit what other working orders do not hold
        pos = self._pos_qty(sym)
        reducing = (side == "sell" and pos > 0) or (side == "buy" and pos < 0)
        if reducing:
            available = abs(pos) - self._reserved_for_exit(sym, side)
            if qty > available:
                err = {"code": 40310000, "message": f"insufficient qty available for order (requested: {qty}, available: {available})"}
                self.refused_403.append(err)
                return httpx.Response(403, json=err)
        if body.get("stop_price"):
            row["stop_price"] = body["stop_price"]
        self.orders[oid] = row
        self.order_seq.append(oid)
        if self.market_fills and row["type"] == "market":       # a stop rests until triggered
            self.fill(oid)
        return httpx.Response(200, json=self.view(row, True))

    def _cancel(self, row: dict) -> None:
        row["status"] = "canceled"
        if row.get("legs_ids"):
            filled = int(row["filled_qty"])
            for lid in row["legs_ids"]:
                leg = self.orders[lid]
                if leg["status"] == "held":
                    if filled == 0:
                        leg["status"] = "canceled"
                    else:
                        leg["status"], leg["qty"] = "new", str(filled)

    def _delete(self, row: dict) -> httpx.Response:
        if row["status"] in TERMINAL:
            return httpx.Response(422, json={"message": f"order is already {row['status']}"})
        oid = row["id"]
        if oid in self.fill_on_cancel:
            cum, px = self.fill_on_cancel.pop(oid)
            self.fill(oid, cum, px)
            if row["status"] != "filled":
                self._cancel(row)
            return httpx.Response(204)
        if oid in self.cancel_pending:
            row["status"] = "pending_cancel"
            return httpx.Response(204)
        self._cancel(row)
        return httpx.Response(204)

    def _patch(self, row: dict, body: dict) -> httpx.Response:
        if row["status"] == "held":
            return httpx.Response(422, json={"code": 42210000, "message": "cannot replace a held order"})
        if row["status"] in TERMINAL:
            return httpx.Response(422, json={"message": f"order is already {row['status']}"})
        new_id = self._new_id()
        new = dict(row, id=new_id, client_order_id=str(uuid.uuid4()), status="new", filled_qty="0",
                   filled_avg_price=None, created_at=self.clock.now.isoformat(), replaced_by=None)
        if "qty" in body:
            new["qty"] = body["qty"]
        if "stop_price" in body:
            new["stop_price"] = body["stop_price"]
        if "limit_price" in body:
            new["limit_price"] = body["limit_price"]
        row["status"], row["replaced_by"] = "replaced", new_id
        self.orders[new_id] = new
        self.order_seq.append(new_id)
        if row.get("parent_id"):
            parent = self.orders[row["parent_id"]]
            parent["legs_ids"] = [new_id if x == row["id"] else x for x in parent["legs_ids"]]
        return httpx.Response(200, json=self.view(new, True))


class FakeFacade:
    """The phase-1 OrbsFacade surface, scripted."""

    def __init__(self, clock: FakeClock, alpaca: Optional[FakeAlpaca] = None):
        self.clock = clock
        self.alpaca = alpaca
        self.calls: List[tuple] = []
        self.recheck_result = (True, "")
        self.recheck_by_symbol: Dict[str, tuple] = {}
        self.macro_result = (False, "")       # the real facade's contract: (vetoed, why)
        self.trade_override: Dict[str, Any] = {}
        self.absorption: Dict[str, dict] = {}
        self.scan_results: List[Any] = []      # consumed in order; a callable is called
        self.decide_results: List[Any] = []
        self.prep_result: Any = {"ok": True, "symbols": 250}

    def prep(self, day):
        self.calls.append(("prep", day))
        return self.prep_result() if callable(self.prep_result) else self.prep_result

    def scan(self, day, end, wave, skip_symbols, executed_today=None):
        # the real OrbsFacade.scan contract: a timezone-aware whole-minute end on the scan day, and a
        # secondary scan needs the durable executed-today set; preview/primary never skip symbols
        assert isinstance(end, datetime) and end.tzinfo is not None, end
        assert end.astimezone(ET).date() == day and end.second == 0 and end.microsecond == 0, end
        if wave == "secondary":
            assert executed_today is not None, "a secondary scan needs executed_today"
        else:
            assert not skip_symbols and not executed_today
        end_s = end.astimezone(ET).strftime("%H:%M")
        self.calls.append(("scan", day, end_s, wave, list(skip_symbols), sorted(executed_today or ())))
        if not self.scan_results:
            return {"ok": True, "error": None, "coverage": 1.0, "cards": [], "board_id": f"{wave}-{end_s}"}
        r = self.scan_results.pop(0)
        return r() if callable(r) else r

    def decide(self, day, board, wave, now, occupied, executed_today=None):
        assert executed_today is not None, "decide needs the durable executed-today set"
        self.calls.append(("decide", day, board.get("board_id"), wave, list(occupied), sorted(executed_today)))
        if not self.decide_results:
            return {"verdict": "pass", "reason": "no card passed", "picks": [], "audit": [], "regime": {}}
        r = self.decide_results.pop(0)
        return r() if callable(r) else r

    def recheck(self, card, now):
        self.calls.append(("recheck", card.get("symbol")))
        return self.recheck_by_symbol.get(card.get("symbol"), self.recheck_result)

    def macro_veto(self, symbol, direction, now):
        self.calls.append(("macro_veto", symbol, direction))
        return self.macro_result

    def latest_trade(self, symbol):
        self.calls.append(("latest_trade", symbol))
        if symbol in self.trade_override:
            v = self.trade_override[symbol]
            return v() if callable(v) else v
        px = self.alpaca.price(symbol) if self.alpaca else 100.0
        return {"price": px, "ts": self.clock.now.isoformat()}

    def absorption_poll(self, symbol, direction, now):
        self.calls.append(("absorption_poll", symbol, direction))
        return self.absorption.get(symbol)


MANIFEST = {"source_commit": "71b001f", "risk_pct": 2.0, "max_day_risk_pct": 2.5, "max_open_slots": 4,
            "max_structure_picks": 2, "daily_loss_halt_pct": 3.0, "max_slip_stop_frac": 0.33,
            "target_r": 0.75, "fast_fail_r": -0.40, "breakeven_r": 0.75,
            "clawback": {"peak_r": 0.60, "giveback_r": 0.25}, "absorption_arm_r": 0.5,
            "max_gross_mult": 3.0, "max_buying_power_pct": 60, "max_name_notional_pct": 150,
            "cutoff": "10:15", "flatten": "11:00", "freeze": "09:38", "secondary_start": "09:45",
            "secondary_end": "10:15", "min_scan_coverage": 0.90}


def pick(sym="APP", direction="long", entry=100.0, stop=98.0, tier="structure", **extra):
    p = {"symbol": sym, "direction": direction, "tier": tier, "entry": entry, "stop": stop,
         "candle_ok": True, "flow_ok": True}
    p.update(extra)
    return p


class Harness:
    """A controller wired to FakeAlpaca through the real AlpacaBroker, plus recorded hooks."""

    def __init__(self, mode="live", start=None, equity=50000.0, last_equity=None, buying_power=200000.0,
                 freeze=True, reconcile=True, adt_book=None, expected_account="PA3CSVDZMMPY"):
        self.clock = FakeClock(start or at(9, 20))
        self.alpaca = FakeAlpaca(self.clock, equity, last_equity, buying_power)
        self.broker = AlpacaBroker("test-key", "test-secret",
                                   transport=httpx.MockTransport(self.alpaca.handler), poll_interval_sec=0.0)
        self.broker._sleep = lambda s: None
        self.facade = FakeFacade(self.clock, self.alpaca)
        self.persisted: List[dict] = []
        self.fills: List[tuple] = []
        self.reserved: set = set()
        self.released: List[str] = []
        self.adt_book: set = set(adt_book or ())
        self.halt = [None]
        self.persist_raises = [False]
        self.writes_at_persist: List[int] = []
        self.expected_account = expected_account
        self.on_persist = None           # test hook: runs after each durable persist
        self.ctl = self.build(mode)
        if freeze:
            ok, detail = self.ctl.freeze_session(self.clock.now)
            assert ok, detail
        if reconcile:
            assert self.ctl.reconcile_on_startup()["ok"]
        self.clock.set(at(9, 39))

    def _persist(self, state):
        if self.persist_raises[0]:
            raise OSError("disk full")
        self.writes_at_persist.append(len(self.alpaca.writes))
        self.persisted.append(state)
        if self.on_persist:
            self.on_persist(state)

    def _reserve(self, sym):
        if sym in self.reserved or sym in self.adt_book:
            return False
        self.reserved.add(sym)
        return True

    def _release(self, sym):
        self.reserved.discard(sym)
        self.released.append(sym)

    def build(self, mode, state=None):
        ctl = OrbExecutionController(
            self.broker, self.facade, MANIFEST, clock=self.clock, persist_cb=self._persist,
            on_fill=lambda *a: self.fills.append(a), is_occupied=lambda s: s in self.adt_book,
            reserve=self._reserve, release=self._release, account_halt=lambda: self.halt[0],
            sleep=self.clock.sleep, budget=RequestBudget(10 ** 6, 10 ** 6), mode=mode,
            expected_account=self.expected_account)
        if state is not None:
            ctl.from_state(state)
        return ctl

    def restart(self, mode="live"):
        """A new process: same broker and hooks, state from the last persisted checkpoint."""
        state = self.persisted[-1]
        self.reserved = set()
        self.ctl = self.build(mode, state)
        return self.ctl

    def pos(self, sym="APP"):
        return next(p for p in self.ctl.state["positions"].values() if p["symbol"] == sym)

    def parent(self, sym="APP"):
        return self.alpaca.by_coid(self.pos(sym)["coid"])

    def own_fill_sum(self, sym="APP"):
        n = 0
        for s, side, qty, _px, _oid, _role in self.fills:
            if s == sym:
                n += qty if side == "buy" else -qty
        return n
