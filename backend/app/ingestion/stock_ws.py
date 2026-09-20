"""backend/app/ingestion/stock_ws.py
AlpacaRelay Stock WebSocket Client with backpressure buffering and auto-reconnect.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit
import websockets
from websockets.exceptions import ConnectionClosed

from backend.app.config import settings
from backend.app.core.event_bus import EventBus, event_bus
from backend.app.models.events import BarEvent, QuoteEvent, TradeEvent, RelayStatusEvent

log = logging.getLogger("StockWebSocketClient")


class StockWebSocketClient:
    """
    AlpacaRelay Stock WebSocket client.
    Handles 1-minute bars ('b'), top-of-book quotes ('q'), trade prints ('t'),
    banner verification, token auth, backpressure queue, and exponential reconnect.
    """

    def __init__(
        self,
        relay_url: Optional[str] = None,
        relay_token: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.relay_url = relay_url or settings.RELAY_URL
        # Contract: stock stream lives at /v2/stocks; append it whenever the
        # configured URL carries no path.
        if urlsplit(self.relay_url).path in ("", "/"):
            self.relay_url = self.relay_url.rstrip("/") + "/v2/stocks"

        self.relay_token = relay_token or settings.RELAY_TOKEN
        self.symbols: Set[str] = set(symbols or settings.WATCHLIST_SYMBOLS)
        self.bus: EventBus = bus or event_bus

        self._running: bool = False
        self._connected: bool = False
        self._ws: Optional[Any] = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=settings.QUEUE_MAX_SIZE)

        # Worker tasks
        self._runner_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None

        # Telemetry & Stats
        self.messages_received: int = 0
        self.bars_received: int = 0
        self.quotes_received: int = 0
        self.trades_received: int = 0
        self.dropped_messages: int = 0
        self.reconnect_count: int = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Start the WebSocket ingestion loop and queue processor."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._process_queue_loop(), name="StockWS_QueueWorker")
        self._runner_task = asyncio.create_task(self._reconnect_loop(), name="StockWS_ReconnectLoop")
        log.info(f"StockWebSocketClient started targeting {self.relay_url}")

    async def stop(self) -> None:
        """Gracefully stop client, cancel worker tasks, and close WebSocket."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close(code=1000, reason="Client shutdown")
            except Exception:
                pass
            self._ws = None

        if self._runner_task:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
            self._runner_task = None

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        log.info("StockWebSocketClient stopped")

    async def subscribe(
        self,
        bars: Optional[List[str]] = None,
        quotes: Optional[List[str]] = None,
        trades: Optional[List[str]] = None,
    ) -> None:
        """Dynamically add symbol subscriptions."""
        payload: Dict[str, Any] = {"action": "subscribe"}
        if bars:
            payload["bars"] = [s.upper() for s in bars]
            self.symbols.update(payload["bars"])
        if quotes:
            payload["quotes"] = [s.upper() for s in quotes]
            self.symbols.update(payload["quotes"])
        if trades:
            payload["trades"] = [s.upper() for s in trades]
            self.symbols.update(payload["trades"])

        if self._ws and self._connected:
            await self._ws.send(json.dumps(payload))
            log.info(f"Sent dynamic subscription: {payload}")

    async def _reconnect_loop(self) -> None:
        """Supervising loop providing exponential backoff on disconnects."""
        backoff = settings.WS_RECONNECT_INITIAL_BACKOFF_SEC
        while self._running:
            try:
                log.info(f"Connecting to Stock WebSocket at {self.relay_url}...")
                async with websockets.connect(
                    self.relay_url,
                    ping_interval=settings.WS_PING_INTERVAL_SEC,
                    ping_timeout=settings.WS_PING_TIMEOUT_SEC,
                    max_size=settings.WS_MAX_MESSAGE_SIZE_BYTES,
                ) as ws:
                    self._ws = ws
                    await self._perform_handshake(ws)
                    self._connected = True
                    backoff = settings.WS_RECONNECT_INITIAL_BACKOFF_SEC  # Reset on successful auth
                    
                    await self._send_initial_subscriptions(ws)
                    await self.bus.publish(
                        RelayStatusEvent(feed_type="stock", status="connected", message="Connected and authenticated")
                    )

                    # Read frames until connection terminates
                    await self._read_loop(ws)

            except ConnectionClosed as cc:
                log.warning(f"Stock WS closed (code={cc.code}, reason={cc.reason}). Reconnecting in {backoff:.1f}s")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"Stock WS error: {exc}. Reconnecting in {backoff:.1f}s")
            finally:
                self._connected = False
                self._ws = None
                self.reconnect_count += 1
                if self._running:
                    await self.bus.publish(
                        RelayStatusEvent(feed_type="stock", status="disconnected", message="Connection dropped")
                    )

            if not self._running:
                break

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * settings.WS_RECONNECT_BACKOFF_MULTIPLIER, settings.WS_RECONNECT_MAX_BACKOFF_SEC)

    async def _perform_handshake(self, ws: Any) -> None:
        """Enforce banner verification and relay token authentication."""
        # 1. Handshake banner
        banner_raw = await asyncio.wait_for(ws.recv(), timeout=settings.WS_CONNECT_TIMEOUT_SEC)
        banner = json.loads(banner_raw)
        if not (isinstance(banner, list) and len(banner) > 0 and banner[0].get("T") == "success" and banner[0].get("msg") == "connected"):
            raise ConnectionError(f"Unexpected handshake banner: {banner_raw}")

        # 2. Authenticate per relay contract: {"action":"auth","key":<token>,"secret":""}
        auth_cmd = json.dumps({"action": "auth", "key": self.relay_token, "secret": ""})
        await ws.send(auth_cmd)

        auth_resp_raw = await asyncio.wait_for(ws.recv(), timeout=settings.WS_AUTH_TIMEOUT_SEC)
        auth_resp = json.loads(auth_resp_raw)
        if not (isinstance(auth_resp, list) and len(auth_resp) > 0 and auth_resp[0].get("T") == "success" and auth_resp[0].get("msg") == "authenticated"):
            raise PermissionError(f"Authentication failed: {auth_resp_raw}")
        
        log.info("Successfully authenticated with AlpacaRelay Stock WS")

    async def _send_initial_subscriptions(self, ws: Any) -> None:
        """Transmit initial channel subscriptions."""
        sym_list = sorted(list(self.symbols))
        cmd: Dict[str, Any] = {"action": "subscribe"}
        if settings.SUBSCRIBE_BARS:
            cmd["bars"] = sym_list
        if settings.SUBSCRIBE_QUOTES:
            cmd["quotes"] = sym_list
        if settings.SUBSCRIBE_TRADES:
            cmd["trades"] = sym_list

        await ws.send(json.dumps(cmd))
        log.info(f"Subscribed channels: bars={len(cmd.get('bars', []))}, quotes={len(cmd.get('quotes', []))}, trades={len(cmd.get('trades', []))}")

    async def _read_loop(self, ws: Any) -> None:
        """Read wire frames and buffer onto internal queue with backpressure protection."""
        async for raw_msg in ws:
            self.messages_received += 1
            qsize = self._queue.qsize()
            if qsize >= settings.QUEUE_MAX_SIZE * settings.QUEUE_HIGH_WATERMARK_PCT:
                log.warning(f"Queue high watermark reached: {qsize}/{settings.QUEUE_MAX_SIZE} items")

            try:
                self._queue.put_nowait(raw_msg)
            except asyncio.QueueFull:
                self.dropped_messages += 1
                log.error("Ingestion queue full! Discarding message to prevent socket stall")

    async def _process_queue_loop(self) -> None:
        """Worker task consuming queue items, parsing batches, and dispatching to EventBus."""
        while self._running:
            try:
                raw_msg = await self._queue.get()
                msgs = json.loads(raw_msg)
                if not isinstance(msgs, list):
                    msgs = [msgs]

                for m in msgs:
                    t = m.get("T")
                    if t == "b":
                        self.bars_received += 1
                        await self.bus.publish(BarEvent.from_relay_dict(m))
                    elif t == "q":
                        self.quotes_received += 1
                        await self.bus.publish(QuoteEvent.from_relay_dict(m))
                    elif t == "t":
                        self.trades_received += 1
                        await self.bus.publish(TradeEvent.from_relay_dict(m))
                    elif t == "relay":
                        msg_text = m.get("msg", "")
                        status = "connected" if "connected" in msg_text else "disconnected"
                        await self.bus.publish(RelayStatusEvent(feed_type="stock", status=status, message=msg_text))
                    elif t in ("subscription", "success"):
                        log.debug(f"Relay control message: {m}")
                    elif t == "error":
                        log.error(f"Upstream relay error: {m}")

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.exception(f"Error processing market message: {exc}")
