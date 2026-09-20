"""backend/app/ingestion/news_ws.py
Real-time Benzinga News WebSocket Client connected downstream to AlpacaRelay.
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import websockets
from websockets.exceptions import ConnectionClosed

from backend.app.config import settings
from backend.app.core.event_bus import EventBus, event_bus
from backend.app.models.events import NewsEvent, RelayStatusEvent
from backend.app.ingestion.sentiment import FinancialSentimentScorer, sentiment_scorer

log = logging.getLogger("NewsWebSocketClient")


class NewsWebSocketClient:
    """
    Dedicated WebSocket client for ingesting real-time Benzinga news (T: 'n').
    Applies algorithmic financial sentiment scoring before publishing NewsEvent to EventBus.
    """

    def __init__(
        self,
        relay_url: Optional[str] = None,
        relay_token: Optional[str] = None,
        bus: Optional[EventBus] = None,
        scorer: Optional[FinancialSentimentScorer] = None,
    ) -> None:
        base_url = (relay_url or settings.RELAY_URL).rstrip("/")
        if not base_url.endswith("/news") and "news" not in base_url and not base_url.endswith(":8080"):
            self.relay_url = base_url + "/news"
        else:
            self.relay_url = base_url

        self.relay_token = relay_token or settings.RELAY_TOKEN
        self.bus: EventBus = bus or event_bus
        self.scorer: FinancialSentimentScorer = scorer or sentiment_scorer

        self._running: bool = False
        self._connected: bool = False
        self._ws: Optional[Any] = None
        self._runner_task: Optional[asyncio.Task] = None

        self.articles_received: int = 0
        self.catalysts_detected: int = 0
        self.reconnect_count: int = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Start the News WebSocket client loop."""
        if self._running:
            return
        self._running = True
        self._runner_task = asyncio.create_task(self._reconnect_loop(), name="NewsWS_ReconnectLoop")
        log.info(f"NewsWebSocketClient started targeting {self.relay_url}")

    async def stop(self) -> None:
        """Stop client and close socket."""
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
        log.info("NewsWebSocketClient stopped")

    async def _reconnect_loop(self) -> None:
        backoff = settings.WS_RECONNECT_INITIAL_BACKOFF_SEC
        while self._running:
            try:
                log.info(f"Connecting to News WebSocket at {self.relay_url}...")
                async with websockets.connect(
                    self.relay_url,
                    ping_interval=settings.WS_PING_INTERVAL_SEC,
                    ping_timeout=settings.WS_PING_TIMEOUT_SEC,
                ) as ws:
                    self._ws = ws
                    # 1. Banner
                    banner_raw = await asyncio.wait_for(ws.recv(), timeout=settings.WS_CONNECT_TIMEOUT_SEC)
                    banner = json.loads(banner_raw)
                    if not (isinstance(banner, list) and len(banner) > 0 and banner[0].get("T") == "success" and banner[0].get("msg") == "connected"):
                        raise ConnectionError(f"Unexpected banner: {banner_raw}")

                    # 2. Auth
                    await ws.send(json.dumps({"action": "auth", "token": self.relay_token, "key": self.relay_token}))
                    auth_raw = await asyncio.wait_for(ws.recv(), timeout=settings.WS_AUTH_TIMEOUT_SEC)
                    auth_resp = json.loads(auth_raw)
                    if not (isinstance(auth_resp, list) and len(auth_resp) > 0 and auth_resp[0].get("T") == "success" and auth_resp[0].get("msg") == "authenticated"):
                        raise PermissionError(f"Auth failed: {auth_raw}")

                    # 3. Subscribe news wildcard
                    await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                    self._connected = True
                    backoff = settings.WS_RECONNECT_INITIAL_BACKOFF_SEC
                    
                    await self.bus.publish(
                        RelayStatusEvent(feed_type="news", status="connected", message="News feed connected")
                    )

                    # 4. Message ingestion loop
                    async for raw_msg in ws:
                        await self._handle_news_message(raw_msg)

            except ConnectionClosed as cc:
                log.warning(f"News WS closed (code={cc.code}, reason={cc.reason}). Retry in {backoff:.1f}s")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"News WS error: {exc}. Retry in {backoff:.1f}s")
            finally:
                self._connected = False
                self._ws = None
                self.reconnect_count += 1
                if self._running:
                    await self.bus.publish(
                        RelayStatusEvent(feed_type="news", status="disconnected", message="News feed disconnected")
                    )

            if not self._running:
                break

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * settings.WS_RECONNECT_BACKOFF_MULTIPLIER, settings.WS_RECONNECT_MAX_BACKOFF_SEC)

    async def _handle_news_message(self, raw_msg: str) -> None:
        """Parse Benzinga news article and publish enriched NewsEvent."""
        try:
            msgs = json.loads(raw_msg)
        except json.JSONDecodeError:
            log.warning(f"Malformed news frame: {raw_msg}")
            return

        if not isinstance(msgs, list):
            msgs = [msgs]

        for m in msgs:
            t = m.get("T")
            if t == "n":
                self.articles_received += 1
                headline = m.get("headline", "")
                summary = m.get("summary", "")
                content = m.get("content", "")
                symbols = [s.upper() for s in m.get("symbols", [])]

                # Run algorithmic sentiment & catalyst classification
                score, confidence, category = self.scorer.score(headline=headline, summary=summary)
                if abs(score) >= 0.6:
                    self.catalysts_detected += 1
                    log.info(f"High-impact news catalyst: [{category.value}] Score={score:.2f} ({symbols}) '{headline}'")

                created_raw = m.get("created_at") or datetime.now(timezone.utc).isoformat()
                created_str = str(created_raw).replace("Z", "+00:00")
                created_dt = datetime.fromisoformat(created_str)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)

                updated_dt = None
                if m.get("updated_at"):
                    upd_str = str(m["updated_at"]).replace("Z", "+00:00")
                    updated_dt = datetime.fromisoformat(upd_str)
                    if updated_dt.tzinfo is None:
                        updated_dt = updated_dt.replace(tzinfo=timezone.utc)

                event = NewsEvent(
                    article_id=int(m.get("id", 0)),
                    headline=headline,
                    summary=summary,
                    symbols=symbols,
                    source=m.get("source", "benzinga"),
                    created_at=created_dt,
                    updated_at=updated_dt,
                    url=m.get("url"),
                    content=content,
                    sentiment_score=score,
                    sentiment_confidence=confidence,
                    catalyst_category=category,
                )
                await self.bus.publish(event)

            elif t == "relay":
                msg_text = m.get("msg", "")
                status = "connected" if "connected" in msg_text else "disconnected"
                await self.bus.publish(RelayStatusEvent(feed_type="news", status=status, message=msg_text))
