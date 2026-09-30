# @steered SNARE-2 2026-09-30
"""backend/app/core/broker.py
Real order execution on an Alpaca PAPER account.

The bot still decides locally WHEN an order triggers (stops, targets, flattens).
At that moment the execution engine asks this broker to fill it for real and
books Alpaca's actual quantity and average price. Market data never comes from
here; it stays on AlpacaRelay.

This module only talks to Alpaca. The bookkeeping that makes retries safe
(which Alpaca order belongs to which local order, how much of it is already
booked) lives on the local Order and in ExecutionEngine._broker_execute.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("broker")

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
# done_for_day resumes next session; stopped/suspended can still execute.
# Treating them as final can release a second exit while the first is live.
TERMINAL_STATES = {"filled", "canceled", "expired", "rejected"}


class BrokerError(Exception):
    """Base broker failure. `hard` means retrying the same order soon will not help."""

    def __init__(self, message: str, hard: bool = False, retry_sec: Optional[float] = None) -> None:
        super().__init__(message)
        self.hard = hard
        self.retry_sec = retry_sec


class BrokerNoFill(BrokerError):
    """Order reached Alpaca but nothing (new) filled before the wait ran out."""


class BrokerReject(BrokerError):
    """Alpaca refused the order, or a safety gate refused to send it."""


class BrokerHTTPError(BrokerReject):
    """Alpaca answered with a non-success HTTP status (used by the bracket/ORB methods).

    `status_code` is Alpaca's answer and `body` its JSON (or text). An explicit 4xx other
    than 429 is a definitive refusal; 429 and 5xx prove nothing about the order.
    """

    def __init__(self, message: str, status_code: int, body: Any = None) -> None:
        retry_sec = 5.0 if status_code == 429 else None
        super().__init__(message, hard=400 <= status_code < 500 and status_code != 429, retry_sec=retry_sec)
        self.status_code = status_code
        self.body = body

    @property
    def definitive(self) -> bool:
        return 400 <= self.status_code < 500 and self.status_code != 429


class BrokerTransportError(BrokerError):
    """The request never got an HTTP answer (timeout, dropped connection). Outcome unknown."""


@dataclass
class BrokerStatus:
    mode: str = "alpaca_paper"
    account_number: Optional[str] = None
    equity: Optional[float] = None
    cash: Optional[float] = None
    positions: Dict[str, int] = field(default_factory=dict)
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    last_order_at: Optional[str] = None
    orders_sent: int = 0
    fills_booked: int = 0


def filled_qty(order: Dict[str, Any]) -> int:
    return int(float(order.get("filled_qty") or 0))


def filled_avg(order: Dict[str, Any]) -> float:
    return float(order.get("filled_avg_price") or 0.0)


def is_terminal(order: Dict[str, Any]) -> bool:
    return order.get("status") in TERMINAL_STATES


class AlpacaBroker:
    """Synchronous Alpaca paper trading client (fills are rare; each call is short)."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str = PAPER_BASE_URL,
        fill_wait_sec: float = 4.0,
        cancel_wait_sec: float = 2.0,
        poll_interval_sec: float = 0.2,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        base_url = base_url.rstrip("/")
        if base_url != PAPER_BASE_URL:
            raise ValueError(f"AlpacaBroker only trades the paper account; refusing base URL {base_url}")
        if not api_key or not secret_key:
            raise ValueError("Alpaca API key and secret are required")
        self.fill_wait_sec = fill_wait_sec
        self.cancel_wait_sec = cancel_wait_sec
        self.poll_interval_sec = poll_interval_sec
        self.status = BrokerStatus()
        self._client = httpx.Client(
            base_url=base_url,
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key},
            timeout=httpx.Timeout(3.0, connect=2.0),
            transport=transport,
        )
        self._sleep = time.sleep

    # ------------------------------------------------------------------ reads
    def get_account(self) -> Dict[str, Any]:
        resp = self._client.get("/v2/account")
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> Dict[str, int]:
        """Signed share count per symbol (short = negative)."""
        resp = self._client.get("/v2/positions")
        resp.raise_for_status()
        return {str(row["symbol"]).upper(): int(float(row["qty"])) for row in resp.json()}

    def position_qty(self, symbol: str) -> int:
        """Signed shares Alpaca holds in one symbol (0 when flat)."""
        try:
            resp = self._client.get(f"/v2/positions/{symbol.upper()}")
        except httpx.HTTPError as exc:
            raise BrokerError(f"position lookup {symbol} failed: {exc}") from exc
        if resp.status_code == 404:
            return 0
        if resp.status_code != 200:
            raise BrokerError(f"position lookup {symbol} failed: {resp.status_code} {resp.text[:200]}")
        return int(float(resp.json()["qty"]))

    def get_order(self, alpaca_id: str, nested: bool = True) -> Dict[str, Any]:
        try:
            resp = self._client.get(f"/v2/orders/{alpaca_id}", params={"nested": "true" if nested else "false"})
        except httpx.HTTPError as exc:
            raise BrokerError(f"order lookup {alpaca_id} failed: {exc}") from exc
        if resp.status_code != 200:
            raise BrokerError(f"order lookup {alpaca_id} failed: {resp.status_code}")
        return resp.json()

    def find_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """None only when Alpaca says the id is unknown; network trouble raises."""
        try:
            resp = self._client.get("/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
        except httpx.HTTPError as exc:
            raise BrokerError(f"client id lookup {client_order_id} failed: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise BrokerError(f"client id lookup {client_order_id} failed: {resp.status_code}")
        return resp.json()

    def sync(self) -> BrokerStatus:
        """Refresh account and positions for the dashboard and reconciliation."""
        acct = self.get_account()
        positions = self.get_positions()
        self.status.account_number = acct.get("account_number")
        self.status.equity = float(acct.get("equity") or 0.0)
        self.status.cash = float(acct.get("cash") or 0.0)
        self.status.positions = positions
        self.status.last_sync_at = datetime.now(timezone.utc).isoformat()
        return self.status

    # ----------------------------------------------------------------- orders
    def get_asset(self, symbol: str) -> Dict[str, Any]:
        response = self._client.get(f"/v2/assets/{symbol.upper()}")
        response.raise_for_status()
        return response.json()

    def request_cancel(self, alpaca_id: str) -> None:
        """Request cancellation without blocking the event loop waiting for it.

        The caller must GET and reconcile a terminal state before another exit.
        """
        response = self._client.delete(f"/v2/orders/{alpaca_id}")
        if response.status_code not in (200, 204, 404, 422):
            response.raise_for_status()

    def submit_oco(self, symbol: str, qty: int, client_order_id: str,
                   stop: float, target: float, side: str = "sell",
                   time_in_force: str = "day") -> Dict[str, Any]:
        """Rest a fixed sell stop and target at Alpaca. Caller persists id FIRST.

        An uncertain response only permits lookup of this id, never a second
        protection request. The caller owns passive polling and cancellation.
        """
        if side.lower() not in ("buy", "sell"):
            raise ValueError("OCO exit side must be buy or sell")
        if time_in_force not in ("day", "gtc"):
            raise ValueError("OCO time in force must be day or gtc")
        decimals = 2 if min(stop, target) >= 1 else 4
        body = {"symbol": symbol, "qty": str(qty), "side": side.lower(), "type": "limit",
                "time_in_force": time_in_force, "order_class": "oco", "client_order_id": client_order_id,
                "take_profit": {"limit_price": f"{target:.{decimals}f}"},
                "stop_loss": {"stop_price": f"{stop:.{decimals}f}"}}
        self.status.orders_sent += 1
        self.status.last_order_at = datetime.now(timezone.utc).isoformat()
        try:
            resp = self._client.post("/v2/orders", json=body)
        except httpx.HTTPError:
            found = self.find_by_client_id(client_order_id)
            if found is not None:
                return self.get_order(found["id"])
            raise BrokerError("OCO protection POST outcome unknown")
        if resp.status_code in (200, 201):
            return resp.json()
        found = self.find_by_client_id(client_order_id)
        if found is not None:
            return self.get_order(found["id"])
        retry_sec = None
        if resp.status_code == 429:
            try:
                retry_sec = max(5., float(resp.headers.get("Retry-After", "5")))
            except ValueError:
                retry_sec = 5.
        raise BrokerReject(f"OCO protection rejected: HTTP {resp.status_code}",
                           hard=resp.status_code < 500 and resp.status_code not in (408, 429),
                           retry_sec=retry_sec)

    def submit(
        self,
        symbol: str,
        side: str,
        qty: int,
        client_order_id: str,
        limit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """POST one day order. Returns Alpaca's order JSON.

        If the POST fails in transit or reports a duplicate client id, the order
        is looked up by our client id first, so a retry can never double it.
        """
        body: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": str(int(qty)),
            "side": side.lower(),
            "type": "limit" if limit_price is not None else "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        if limit_price is not None:
            body["limit_price"] = f"{limit_price:.2f}" if limit_price >= 1 else f"{limit_price:.4f}"
        self.status.orders_sent += 1
        self.status.last_order_at = datetime.now(timezone.utc).isoformat()
        try:
            resp = self._client.post("/v2/orders", json=body)
        except httpx.HTTPError as exc:
            existing = self.find_by_client_id(client_order_id)  # raises if still unreachable
            if existing is None:
                raise BrokerError(f"Alpaca submit failed for {symbol}: {exc}") from exc
            return existing
        if resp.status_code in (200, 201):
            return resp.json()
        text = resp.text[:300]
        if "client_order_id" in text:
            existing = self.find_by_client_id(client_order_id)
            if existing is not None:
                return existing
        self.status.last_error = f"reject {side} {qty} {symbol}: {resp.status_code} {text}"
        raise BrokerReject(
            f"Alpaca rejected {side} {qty} {symbol}: {resp.status_code} {text}",
            hard=resp.status_code in (403, 422),
        )

    def wait(self, order: Dict[str, Any], wait_sec: float) -> Dict[str, Any]:
        """Poll until the order is final or the wait runs out; returns the latest JSON."""
        deadline = time.monotonic() + wait_sec
        while not is_terminal(order) and time.monotonic() < deadline:
            self._sleep(self.poll_interval_sec)
            try:
                order = self.get_order(order["id"])
            except BrokerError:
                continue
        return order

    def cancel_and_settle(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Ask Alpaca to cancel, then wait for the final state (a fill can land meanwhile)."""
        if is_terminal(order):
            return order
        try:
            self._client.delete(f"/v2/orders/{order['id']}")
        except httpx.HTTPError as exc:
            log.warning("Cancel of Alpaca order %s failed: %s", order.get("id"), exc)
        return self.wait(order, self.cancel_wait_sec)

    def submit_and_settle(
        self,
        symbol: str,
        side: str,
        qty: int,
        client_order_id: str,
        limit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Submit, wait for a fill, cancel whatever is left. The result may still be
        non-terminal if Alpaca never confirmed the cancel; the caller must then keep
        tracking that Alpaca order instead of sending a new one."""
        order = self.submit(symbol, side, qty, client_order_id, limit_price)
        order = self.wait(order, self.fill_wait_sec)
        return self.cancel_and_settle(order)

    # ------------------------------------------------ ORB bracket lifecycle
    # Used by core/orb_execution.py. Each method is one HTTP round trip (except
    # cancel_order_and_confirm, which polls) and never retries a write on its own:
    # the caller owns ambiguity (lookup by client id), exactly as ORBStraddle does.
    @staticmethod
    def _body(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return resp.text[:300]

    def _http_error(self, what: str, resp: httpx.Response) -> BrokerHTTPError:
        body = self._body(resp)
        return BrokerHTTPError(f"{what}: HTTP {resp.status_code} {str(body)[:200]}", resp.status_code, body)

    def get_order_by_client_id(self, client_order_id: str, nested: bool = True) -> Optional[Dict[str, Any]]:
        """The order carrying this client id, or None when Alpaca says it has none (404).

        Any other failure raises (BrokerTransportError / BrokerHTTPError): it proves nothing.
        """
        try:
            resp = self._client.get("/v2/orders:by_client_order_id",
                                    params={"client_order_id": client_order_id,
                                            "nested": "true" if nested else "false"})
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"client id lookup {client_order_id} failed: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise self._http_error(f"client id lookup {client_order_id}", resp)
        body = resp.json()
        return body if isinstance(body, dict) and body.get("id") else None

    def submit_bracket(self, symbol: str, qty: int, side: str, take_profit: float, stop_loss: float,
                       client_order_id: str) -> Dict[str, Any]:
        """POST one Alpaca bracket: market parent, day, take-profit limit + plain stop (2 dp).

        Returns the parent order JSON. A lost reply raises BrokerTransportError and an HTTP
        refusal raises BrokerHTTPError; the caller resolves both by client id (no resubmit).
        """
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError("bracket side must be buy or sell")
        qty = int(qty)
        if qty < 1:
            raise ValueError("bracket qty must be at least 1")
        tp, sl = float(take_profit), float(stop_loss)
        if not (tp > 0 and sl > 0):
            raise ValueError("bracket prices must be positive")
        if (side == "buy" and not sl < tp) or (side == "sell" and not tp < sl):
            raise ValueError(f"bracket levels on the wrong side: {side} tp={tp} sl={sl}")
        body = {"symbol": symbol.upper(), "qty": str(qty), "side": side, "type": "market",
                "time_in_force": "day", "order_class": "bracket",
                "client_order_id": client_order_id[:128],
                "take_profit": {"limit_price": f"{tp:.2f}"},
                "stop_loss": {"stop_price": f"{sl:.2f}"}}
        self.status.orders_sent += 1
        self.status.last_order_at = datetime.now(timezone.utc).isoformat()
        try:
            resp = self._client.post("/v2/orders", json=body)
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"bracket POST {symbol} outcome unknown: {exc}") from exc
        if resp.status_code in (200, 201):
            return resp.json()
        err = self._http_error(f"bracket {side} {qty} {symbol} refused", resp)
        self.status.last_error = str(err)[:300]
        raise err

    def submit_market_order(self, symbol: str, side: str, qty: int, client_order_id: str) -> Dict[str, Any]:
        """POST one plain market day order (the ORB exit). No lookup, no retry: the caller does that."""
        side = side.lower()
        if side not in ("buy", "sell") or int(qty) < 1:
            raise ValueError("market order needs side buy/sell and qty >= 1")
        body = {"symbol": symbol.upper(), "qty": str(int(qty)), "side": side, "type": "market",
                "time_in_force": "day", "client_order_id": client_order_id[:128]}
        self.status.orders_sent += 1
        self.status.last_order_at = datetime.now(timezone.utc).isoformat()
        try:
            resp = self._client.post("/v2/orders", json=body)
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"market {side} {symbol} outcome unknown: {exc}") from exc
        if resp.status_code in (200, 201):
            return resp.json()
        raise self._http_error(f"market {side} {qty} {symbol} refused", resp)

    def patch_order(self, order_id: str, qty: Optional[int] = None, stop_price: Optional[float] = None,
                    limit_price: Optional[float] = None) -> Dict[str, Any]:
        """PATCH (replace) one working order. Returns the replacement order JSON (a new id).

        A held bracket leg answers 422 on paper; that raises BrokerHTTPError like any refusal.
        """
        body: Dict[str, Any] = {}
        if qty is not None:
            body["qty"] = str(int(qty))
        if stop_price is not None:
            body["stop_price"] = f"{float(stop_price):.2f}"
        if limit_price is not None:
            body["limit_price"] = f"{float(limit_price):.2f}"
        if not body:
            raise ValueError("patch_order needs qty, stop_price or limit_price")
        try:
            resp = self._client.patch(f"/v2/orders/{order_id}", json=body)
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"PATCH {order_id} outcome unknown: {exc}") from exc
        if resp.status_code in (200, 201):
            return resp.json()
        raise self._http_error(f"PATCH {order_id} refused", resp)

    FINAL_FOR_CANCEL = TERMINAL_STATES | {"replaced", "done_for_day"}

    def cancel_order_and_confirm(self, order_id: str, timeout: float = 6.0) -> Dict[str, Any]:
        """DELETE one order, then poll it until it is final or the timeout's polls run out.

        Returns the latest order JSON (nested), which may still be live if Alpaca never
        confirmed; a fill that raced the cancel shows up as filled here. Raises only when
        the order could not be read at all.
        """
        try:
            resp = self._client.delete(f"/v2/orders/{order_id}")
            if resp.status_code not in (200, 204, 404, 422):
                log.warning("Cancel of Alpaca order %s answered %s", order_id, resp.status_code)
        except httpx.HTTPError as exc:
            log.warning("Cancel of Alpaca order %s failed: %s", order_id, exc)
        step = max(self.poll_interval_sec, 0.05)
        polls = max(1, int(round(timeout / step)))
        order: Optional[Dict[str, Any]] = None
        last_exc: Optional[Exception] = None
        for i in range(polls):
            try:
                order = self.get_order(order_id)
                last_exc = None
            except BrokerError as exc:
                last_exc = exc
            if order is not None and order.get("status") in self.FINAL_FOR_CANCEL:
                return order
            if i < polls - 1:
                self._sleep(self.poll_interval_sec)
        if order is None:
            raise BrokerError(f"cancel of {order_id} could not be confirmed: {last_exc}")
        return order

    def list_orders(self, status: str = "open", symbol: Optional[str] = None, after: Optional[str] = None,
                    limit: int = 500, nested: bool = True) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"status": status, "limit": str(int(limit)),
                                  "nested": "true" if nested else "false"}
        if symbol:
            params["symbols"] = symbol.upper()
        if after:
            params["after"] = after
        try:
            resp = self._client.get("/v2/orders", params=params)
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"order list failed: {exc}") from exc
        if resp.status_code != 200:
            raise self._http_error("order list", resp)
        rows = resp.json()
        if not isinstance(rows, list):
            raise BrokerError("order list answered with something that is not a list")
        return rows

    def list_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.list_orders("open", symbol=symbol)

    def get_positions_raw(self) -> List[Dict[str, Any]]:
        """Every Alpaca position row as returned (qty, side, avg_entry_price, current_price)."""
        try:
            resp = self._client.get("/v2/positions")
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"positions read failed: {exc}") from exc
        if resp.status_code != 200:
            raise self._http_error("positions read", resp)
        rows = resp.json()
        if not isinstance(rows, list):
            raise BrokerError("positions answered with something that is not a list")
        return rows

    def get_account_checked(self) -> Dict[str, Any]:
        """GET /v2/account with typed errors (the plain get_account raises httpx errors)."""
        try:
            resp = self._client.get("/v2/account")
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"account read failed: {exc}") from exc
        if resp.status_code != 200:
            raise self._http_error("account read", resp)
        return resp.json()

    # --------------------------------------------- overnight holds (paper host)
    # Used by core/overnight_execution.py. Same rule as the ORB methods: one HTTP round trip,
    # no write retried here, the caller owns ambiguity (lookup by client id).
    AUCTION_TIFS = ("cls", "opg")
    ACCOUNT_FIELDS = ("equity", "cash", "buying_power", "regt_buying_power", "daytrading_buying_power",
                      "last_maintenance_margin", "multiplier")
    CORPORATE_ACTION_MAX_DAYS = 90   # Alpaca's date range limit for the announcements endpoint

    def submit_on_auction(self, symbol: str, side: str, qty: int, client_order_id: str, tif: str) -> Dict[str, Any]:
        """POST one market order for the closing (tif cls) or opening (tif opg) auction.

        Alpaca takes cls until 15:50 ET and opg from 19:00 until 09:28 ET. A lost reply raises
        BrokerTransportError and a refusal BrokerHTTPError (see classify_refusal).
        """
        side = side.lower()
        if tif not in self.AUCTION_TIFS:
            raise ValueError("auction orders take time in force cls or opg only")
        if side not in ("buy", "sell") or int(qty) < 1:
            raise ValueError("auction order needs side buy/sell and qty >= 1")
        body = {"symbol": symbol.upper(), "qty": str(int(qty)), "side": side, "type": "market",
                "time_in_force": tif, "client_order_id": client_order_id[:128]}
        self.status.orders_sent += 1
        self.status.last_order_at = datetime.now(timezone.utc).isoformat()
        try:
            resp = self._client.post("/v2/orders", json=body)
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"{tif} {side} {symbol} outcome unknown: {exc}") from exc
        if resp.status_code in (200, 201):
            return resp.json()
        err = self._http_error(f"{tif} {side} {qty} {symbol} refused", resp)
        self.status.last_error = str(err)[:300]
        raise err

    def get_calendar(self, start: str, end: str) -> List[Dict[str, Any]]:
        """Read only GET /v2/calendar: [{"date": "YYYY-MM-DD", "open": "09:30", "close": "16:00", ...}]."""
        try:
            resp = self._client.get("/v2/calendar", params={"start": start, "end": end})
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"calendar read failed: {exc}") from exc
        if resp.status_code != 200:
            raise self._http_error("calendar read", resp)
        rows = resp.json()
        if not isinstance(rows, list):
            raise BrokerError("calendar answered with something that is not a list")
        return rows

    def get_account_fields(self) -> Dict[str, Any]:
        """Account number plus the sizing and room fields as floats (None when Alpaca omits one)."""
        acct = self.get_account_checked()
        out: Dict[str, Any] = {"account_number": acct.get("account_number")}
        for key in self.ACCOUNT_FIELDS:
            try:
                out[key] = float(acct[key]) if acct.get(key) not in (None, "") else None
            except (TypeError, ValueError):
                out[key] = None
        return out

    def get_corporate_actions(self, symbol: str, since: str, until: str) -> List[Dict[str, Any]]:
        """Split, merger and spinoff announcements for one symbol with an ex date in [since, until].

        TO VERIFY against the paper host before relying on it live: the endpoint is taken to be
        GET /v2/corporate_actions/announcements (Trading API, same host as orders) with params
        ca_types, since, until (at most 90 days apart), symbol and date_type=ex_date, answering
        rows with ca_type, ca_sub_type, initiating_symbol, target_symbol, old_rate, new_rate and
        ex_date. Alpaca also serves GET https://data.alpaca.markets/v1/corporate-actions, which
        is on the market data host and is not used (R6, paper host only).
        Rows are returned normalized: kind (split, reverse_split, merger, spinoff, other),
        old_symbol, new_symbol, ratio (new shares per old share, None when a rate is missing)
        and ex_date; the raw row is kept under "raw".
        """
        a, b = datetime.fromisoformat(since).date(), datetime.fromisoformat(until).date()
        if b < a or (b - a).days > self.CORPORATE_ACTION_MAX_DAYS:
            raise ValueError("corporate action range must be 0 to 90 days")
        params = {"ca_types": "split,merger,spinoff", "since": a.isoformat(), "until": b.isoformat(),
                  "symbol": symbol.upper(), "date_type": "ex_date"}
        try:
            resp = self._client.get("/v2/corporate_actions/announcements", params=params)
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"corporate actions read failed: {exc}") from exc
        if resp.status_code != 200:
            raise self._http_error("corporate actions read", resp)
        rows = resp.json()
        if not isinstance(rows, list):
            raise BrokerError("corporate actions answered with something that is not a list")
        return [normalize_corporate_action(row) for row in rows]

    def close(self) -> None:
        self._client.close()


REFUSAL_AMBIGUOUS = "AMBIGUOUS"          # no answer, 429 or 5xx: proves nothing, look up by client id
REFUSAL_WASH_TRADE = "WASH_TRADE"        # 403 potential wash trade (an opposite order is open)
REFUSAL_BUYING_POWER = "BUYING_POWER"    # 403 insufficient buying power
REFUSAL_DEFINITE = "REFUSED"             # any other explicit 4xx, including other 40310000 answers


def _refusal_text(body: Any) -> str:
    if isinstance(body, dict):
        return f"{body.get('code', '')} {body.get('message', '')}".lower()
    return str(body or "").lower()


def classify_refusal(exc: Exception) -> str:
    """Sort a broker failure: AMBIGUOUS, WASH_TRADE, BUYING_POWER or REFUSED (definite)."""
    if not isinstance(exc, BrokerHTTPError) or not exc.definitive:
        return REFUSAL_AMBIGUOUS
    text = _refusal_text(exc.body) or str(exc).lower()
    if "client_order_id" in text:
        return REFUSAL_AMBIGUOUS     # a duplicate id means an order with it may exist: look it up
    if "wash trade" in text:
        return REFUSAL_WASH_TRADE
    if "buying power" in text:
        return REFUSAL_BUYING_POWER
    return REFUSAL_DEFINITE


def _rate(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def normalize_corporate_action(row: Dict[str, Any]) -> Dict[str, Any]:
    ca_type = str(row.get("ca_type") or "").lower()
    sub = str(row.get("ca_sub_type") or "").lower()
    old_sym = str(row.get("initiating_symbol") or row.get("target_symbol") or "").upper() or None
    new_sym = str(row.get("target_symbol") or row.get("initiating_symbol") or "").upper() or None
    old_rate, new_rate = _rate(row.get("old_rate")), _rate(row.get("new_rate"))
    ratio = new_rate / old_rate if old_rate and new_rate else None
    if ca_type == "split":
        kind = "reverse_split" if sub == "reverse_split" or (ratio is not None and ratio < 1) else "split"
    elif ca_type in ("merger", "spinoff"):
        kind = ca_type
    else:
        kind = "other"
    return {"kind": kind, "old_symbol": old_sym, "new_symbol": new_sym, "ratio": ratio,
            "ex_date": row.get("ex_date"), "raw": row}
