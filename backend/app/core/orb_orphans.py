"""ORB-safe close of an ORB position the ORB controller cannot see (lost or unreadable state, an old
leftover), and of live adt-orb orders nobody tracks. Runs on a worker thread; never on the loop.

Rules (Codex phase-3 review P1 #1):
  * ownership is PROVEN from Alpaca: every order whose client id starts with the ORB prefix, plus the
    nested legs of those parents (their client ids are broker UUIDs); their signed fills must equal the
    quantity ADT's book holds as "orb", and the Alpaca position must cover it (same side, at least
    that size). Otherwise nothing is sent and the caller raises a loud alarm;
  * the pinned Alpaca account is checked before any write;
  * every live own order (parent remainder first, then the legs) is cancelled and confirmed final
    BEFORE the close; a leg that fills while it is cancelled is reported so ADT books it;
  * then ONE market order for exactly the remaining own quantity (client id with the ORB prefix, so a
    retry counts it), never DELETE /positions, never another actor's order.
Returns a result dict: status closed | nothing | retry | unproven, the fills to book, and a reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

FINAL = frozenset({"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"})
LOOKBACK_DAYS = 7
CLOSE_POLLS = 10


def _i(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def own_orders(rows: List[dict], prefix: str) -> Dict[str, dict]:
    """id -> order for every ORB-prefixed top-level order and the nested legs of those orders."""
    out: Dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict) or not str(row.get("client_order_id") or "").startswith(prefix):
            continue
        out[str(row.get("id"))] = row
        for leg in row.get("legs") or []:
            if isinstance(leg, dict) and leg.get("id"):
                out[str(leg["id"])] = leg
    return out


def signed_filled(orders: Dict[str, dict]) -> int:
    return sum(_i(o.get("filled_qty")) * (1 if o.get("side") == "buy" else -1) for o in orders.values())


def _fill_deltas(before: Dict[str, dict], after: Dict[str, dict]) -> List[dict]:
    fills = []
    for oid, o in after.items():
        old = before.get(oid) or {}
        q0, q1 = _i(old.get("filled_qty")), _i(o.get("filled_qty"))
        if q1 <= q0:
            continue
        a0, a1 = _f(old.get("filled_avg_price")) or 0.0, _f(o.get("filled_avg_price"))
        if a1 is None:
            continue
        px = (q1 * a1 - q0 * a0) / (q1 - q0)
        fills.append({"order_id": oid, "side": o.get("side"), "qty": q1 - q0, "price": px if px > 0 else a1,
                      "client_order_id": o.get("client_order_id"), "type": o.get("type")})
    return fills


def recover(broker: Any, symbol: str, adt_qty: int, prefix: str, *, now: datetime, attempt: int,
            account_refusal: Callable[[dict], Optional[str]], sleep: Callable[[float], None]) -> Dict[str, Any]:
    sym = symbol.upper()
    after = (now - timedelta(days=LOOKBACK_DAYS)).isoformat()

    def fetch() -> Dict[str, dict]:
        return own_orders(broker.list_orders("all", sym, after, 500, True), prefix)

    out: Dict[str, Any] = {"symbol": sym, "fills": [], "status": "unproven", "reason": None, "writes": 0}
    first = fetch()
    net = signed_filled(first)
    if net != adt_qty:
        out["reason"] = (f"ADT's book holds {adt_qty} ORB shares of {sym} but ORB's orders at Alpaca account "
                         f"for {net}: ownership cannot be proved")
        return out
    alp = broker.position_qty(sym)
    if net and (alp * net <= 0 or abs(alp) < abs(net)):
        out["reason"] = (f"Alpaca holds {alp} {sym} shares, which does not cover ORB's {net}: "
                         "ownership cannot be proved")
        return out
    live = [o for o in first.values() if o.get("status") not in FINAL]
    if not live and net == 0:
        out.update(status="nothing", reason="no ORB shares and no live ORB orders left")
        return out
    why = account_refusal(broker.get_account_checked())
    if why:
        out["reason"] = f"the Alpaca account could not be verified before a write: {why}"
        return out
    # parent remainders first (their legs would come alive on a fill), then the legs
    live.sort(key=lambda o: 0 if o.get("legs") is not None and str(o.get("client_order_id") or "").startswith(prefix) else 1)
    unconfirmed = []
    for o in live:
        res = broker.cancel_order_and_confirm(str(o["id"]))
        out["writes"] += 1
        if (res or {}).get("status") not in FINAL:
            unconfirmed.append(str(o["id"]))
    second = fetch()
    out["fills"] += _fill_deltas(first, second)
    if unconfirmed or any(o.get("status") not in FINAL for o in second.values()):
        out.update(status="retry", reason="a cancel of an ORB order is not confirmed yet; nothing was sold")
        return out
    remaining = signed_filled(second)
    if remaining == 0:
        out.update(status="closed", reason="ORB's bracket legs are cancelled and no ORB shares remain")
        return out
    alp = broker.position_qty(sym)
    qty = min(abs(remaining), abs(alp)) if alp * remaining > 0 else 0
    if qty <= 0:
        out.update(status="unproven", reason=f"after the cancels Alpaca holds {alp} {sym}; ORB's own is {remaining}")
        return out
    side = "sell" if remaining > 0 else "buy"
    coid = f"{prefix}R-{sym}-{now.date().isoformat()}-{attempt}"
    order = broker.submit_market_order(sym, side, qty, coid)
    out["writes"] += 1
    oid = str(order.get("id"))
    for _ in range(CLOSE_POLLS):
        if order.get("status") in FINAL:
            break
        sleep(1.0)
        order = broker.get_order(oid, False)
    third = dict(second)
    third[oid] = order
    out["fills"] += _fill_deltas(second, third)
    left = signed_filled(third)
    if order.get("status") in FINAL and left == 0:
        out.update(status="closed", reason=f"closed {qty} ORB shares of {sym} after cancelling its bracket legs")
    else:
        out.update(status="retry", reason=f"the close of {sym} is not finished ({order.get('status')}); retrying")
    return out
