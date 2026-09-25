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
TERMINAL_STATES = {"filled", "canceled", "expired", "rejected", "done_for_day", "stopped", "suspended"}


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

    def get_order(self, alpaca_id: str) -> Dict[str, Any]:
        try:
            resp = self._client.get(f"/v2/orders/{alpaca_id}")
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

    def close(self) -> None:
        self._client.close()
