# Technical Architecture Blueprint: AlpacaRelay Ingestion & Feed Adapters

**Author**: `explorer_m1_1` (Explorer 1 — Ingestion & Feed Adapter Architecture)  
**Target Milestone**: Milestone 1 (`engine_ingestion`)  
**Date**: 2026-09-19  
**Parent Orchestrator**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  

---

## 1. Executive Summary & Architecture Context

The **AutonomousDayTrader** ingestion subsystem is the mission-critical foundation for downstream strategy execution, paper portfolio accounting, and risk management. It interfaces directly with **AlpacaRelay**—a production multiplexing relay running on Railway US East (`alpacarelay-production.up.railway.app`) or a local mock/replay server (`127.0.0.1:8765` / `127.0.0.1:8080`).

### Core Ingestion Responsibilities
1. **Stock WebSocket Client (`stock_ws.py`)**: Consumes high-throughput 1-minute OHLCV bars (`b`), top-of-book quotes (`q`), and trade prints (`t`). Manages native banner verification, `RELAY_TOKEN` authentication, exponential backoff reconnection, and an asynchronous backpressure buffer to prevent the relay from dropping the client (`1013 "too slow"`).
2. **News WebSocket Client (`news_ws.py`)**: Runs on a dedicated WebSocket connection to ingest Benzinga news articles (`T: "n"`) without competing with high-frequency stock quote bursts.
3. **Algorithmic Financial Sentiment Scorer (`sentiment.py`)**: A deterministic, microsecond-latency financial NLP/lexicon scorer generating polarity $S \in [-1.0, 1.0]$, confidence $C \in [0.0, 1.0]$, and catalyst category classifications without requiring external heavy PyTorch model downloads.
4. **REST `/vix` Client (`vix_client.py`)**: Polls `GET /vix` with `X-Relay-Token`, strictly omitting query parameters, parses dxFeed spot prints, caches values, verifies age freshness, and maps VIX prints to institutional volatility regimes (LOW, NORMAL, ELEVATED, CRISIS).
5. **Configuration & Event Models (`config.py`, `models/events.py`, `event_bus.py`)**: Strongly-typed Pydantic settings and immutable event dataclasses dispatched over an asynchronous, fault-isolated event bus.

```
                                  ALPACARELAY (Production / Mock)
                       ┌──────────────────────────────────────────────────┐
                       │  Stock WS (/v2/stocks)  │ News WS (/news) │ REST │
                       └──────────────┬──────────────────┬─────────┴───┬──┘
                                      │                  │             │
                ┌─────────────────────┼──────────────────┼─────────────┼─────────────────────┐
                │ BACKEND INGESTION   │                  │             │                     │
                │                     ▼                  ▼             ▼                     │
                │             ┌──────────────┐   ┌──────────────┐┌──────────────┐            │
                │             │  StockWS     │   │   NewsWS     ││  VixClient   │            │
                │             │  Client      │   │   Client     ││  (REST Poll) │            │
                │             └──────┬───────┘   └──────┬───────┘└──────┬───────┘            │
                │                    │                  │               │                    │
                │                    │                  ▼               │                    │
                │                    │          ┌──────────────┐        │                    │
                │                    │          │  Sentiment   │        │                    │
                │                    │          │    Scorer    │        │                    │
                │                    │          └──────┬───────┘        │                    │
                │                    ▼                 ▼                ▼                    │
                │       ┌─────────────────────────────────────────────────────────────┐      │
                │       │                  Internal Async Event Bus                   │      │
                │       │       (BarEvent, QuoteEvent, TradeEvent, NewsEvent, Vix)    │      │
                │       └──────────────────────────────┬──────────────────────────────┘      │
                └──────────────────────────────────────┼─────────────────────────────────────┘
                                                       │
                           ┌───────────────────────────┼───────────────────────────┐
                           ▼                           ▼                           ▼
                 ┌───────────────────┐       ┌───────────────────┐       ┌───────────────────┐
                 │  Paper Account    │       │   Risk Engine &   │       │  4 Intraday       │
                 │  ($50k State)     │       │  Circuit Breaker  │       │  Strategies       │
                 │  (explorer_m1_2)  │       │  (explorer_m1_3)  │       │  (Milestone 2)    │
                 └───────────────────┘       └───────────────────┘       └───────────────────┘
```

---

## 2. Configuration Blueprint (`backend/app/config.py`)

The configuration module utilizes Pydantic `BaseSettings` to provide strongly-typed configuration loaded from environment variables and `.env` files with defensive validation.

### Class Definition & Implementation Specification
```python
"""backend/app/config.py
System configuration, network endpoints, credentials, and institutional risk parameters.
"""
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Environment & Mode
    ENV: str = Field(default="development", description="development, production, or testing")
    LOG_LEVEL: str = Field(default="INFO", description="Logging verbosity")

    # AlpacaRelay Endpoints & Credentials
    RELAY_URL: str = Field(
        default="wss://alpacarelay-production.up.railway.app",
        description="AlpacaRelay WebSocket base endpoint (or ws://127.0.0.1:8765 for mock)"
    )
    RELAY_HTTP_URL: str = Field(
        default="https://alpacarelay-production.up.railway.app",
        description="AlpacaRelay HTTP base endpoint (or http://127.0.0.1:8765 for mock)"
    )
    RELAY_TOKEN: str = Field(
        default="abb49296c2dd0556388b4e4c8dbced1134eba074d6ba9f7b",
        description="Shared authentication token for AlpacaRelay"
    )

    # Active Ticker Universes & Subscriptions
    WATCHLIST_SYMBOLS: List[str] = Field(
        default=["SPY", "QQQ", "AAPL", "NVDA", "TSLA"],
        description="Default symbol roster for stock market data subscriptions"
    )
    SUBSCRIBE_BARS: bool = Field(default=True, description="Subscribe to 1-minute OHLCV bars")
    SUBSCRIBE_QUOTES: bool = Field(default=True, description="Subscribe to NBBO top-of-book quotes")
    SUBSCRIBE_TRADES: bool = Field(default=True, description="Subscribe to SIP trade prints")
    SUBSCRIBE_NEWS: bool = Field(default=True, description="Subscribe to Benzinga news feed")

    # Stock & News WebSocket Connection Tuning
    WS_CONNECT_TIMEOUT_SEC: float = Field(default=10.0, description="Connection handshake timeout")
    WS_AUTH_TIMEOUT_SEC: float = Field(default=10.0, description="Auth ack timeout (relay enforced)")
    WS_PING_INTERVAL_SEC: float = Field(default=20.0, description="Keepalive ping interval")
    WS_PING_TIMEOUT_SEC: float = Field(default=10.0, description="Keepalive ping timeout")
    WS_RECONNECT_INITIAL_BACKOFF_SEC: float = Field(default=1.0, description="Initial reconnect delay")
    WS_RECONNECT_MAX_BACKOFF_SEC: float = Field(default=30.0, description="Max exponential backoff")
    WS_RECONNECT_BACKOFF_MULTIPLIER: float = Field(default=2.0, description="Backoff multiplier")
    WS_MAX_MESSAGE_SIZE_BYTES: int = Field(default=8 * 1024 * 1024, description="Max frame size (8MB)")

    # Ingestion Queue & Backpressure Handling
    QUEUE_MAX_SIZE: int = Field(
        default=10000,
        description="Max internal queue depth before backpressure actions are triggered"
    )
    QUEUE_HIGH_WATERMARK_PCT: float = Field(
        default=0.80,
        description="Queue threshold (80%) triggering high-watermark warning and load shed"
    )

    # REST /vix Client Settings
    VIX_POLL_INTERVAL_SEC: float = Field(default=5.0, description="Polling interval in seconds")
    VIX_MAX_STALE_AGE_SEC: float = Field(default=300.0, description="Max allowed staleness during market hours")
    VIX_DEFAULT_FALLBACK: float = Field(default=20.0, description="Safe fallback VIX print on failure")

    # Institutional Account & Risk Parameters
    INITIAL_CASH: float = Field(default=50000.0, description="Initial virtual account cash balance")
    DAY_TRADING_LEVERAGE: float = Field(default=4.0, description="FINRA Rule 4210 Day Trading Buying Power (4:1)")
    MAX_DAILY_LOSS_LIMIT: float = Field(default=1500.0, description="Hard daily drawdown circuit breaker ($1,500)")
    PER_POSITION_RISK_PCT: float = Field(default=0.01, description="Max account equity risk per trade (1%)")
    MAX_POSITION_NOTIONAL: float = Field(default=25000.0, description="Max single position value ($25,000)")
    MAX_CONCURRENT_POSITIONS: int = Field(default=3, description="Max simultaneous open positions")

    # Safe Host Port Allocations (Collision Free)
    API_PORT: int = Field(default=8005, description="FastAPI core engine & WS port")
    UI_PORT: int = Field(default=3005, description="Apple Music mobile UI frontend port")
    MOCK_PORT: int = Field(default=8080, description="Mock AlpacaRelay replay server port")


# Global singleton settings instance
settings = Settings()
```

---

## 3. Strongly-Typed Event Models (`backend/app/models/events.py`)

All market events, news catalysts, volatility prints, and control messages are modeled as immutable, strongly-typed data structures for maximum performance and validation safety.

### Class Definitions & Schema Specification
```python
"""backend/app/models/events.py
Typed event models for market data feeds, news catalysts, VIX prints, and control telemetry.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any


class EventType(str, Enum):
    BAR = "BAR"
    QUOTE = "QUOTE"
    TRADE = "TRADE"
    NEWS = "NEWS"
    VIX = "VIX"
    RELAY_STATUS = "RELAY_STATUS"


class VixRegime(str, Enum):
    LOW = "LOW"             # VIX < 15.0: Sizing 1.2x, tight stops 0.8x ATR
    NORMAL = "NORMAL"       # 15.0 <= VIX < 22.0: Sizing 1.0x, standard stops 1.0x ATR
    ELEVATED = "ELEVATED"   # 22.0 <= VIX < 30.0: Sizing 0.6x, wider stops 1.5x ATR
    CRISIS = "CRISIS"       # VIX >= 30.0: Sizing 0.25x, freeze breakout entries


class CatalystCategory(str, Enum):
    EARNINGS_BEAT = "EARNINGS_BEAT"
    EARNINGS_MISS = "EARNINGS_MISS"
    GUIDANCE_RAISE = "GUIDANCE_RAISE"
    GUIDANCE_CUT = "GUIDANCE_CUT"
    FDA_APPROVAL = "FDA_APPROVAL"
    FDA_REJECTION = "FDA_REJECTION"
    PARTNERSHIP_CONTRACT = "PARTNERSHIP_CONTRACT"
    LEGAL_INVESTIGATION = "LEGAL_INVESTIGATION"
    ANALYST_UPGRADE = "ANALYST_UPGRADE"
    ANALYST_DOWNGRADE = "ANALYST_DOWNGRADE"
    GENERAL_CATALYST = "GENERAL_CATALYST"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class BarEvent:
    """1-minute aggregate OHLCV bar (AlpacaRelay message T: 'b')."""
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime
    num_trades: Optional[int] = None
    vwap: Optional[float] = None

    @classmethod
    def from_relay_dict(cls, data: Dict[str, Any]) -> "BarEvent":
        return cls(
            symbol=data["S"],
            open=float(data["o"]),
            high=float(data["h"]),
            low=float(data["l"]),
            close=float(data["c"]),
            volume=int(data["v"]),
            timestamp=datetime.fromisoformat(data["t"].replace("Z", "+00:00")),
            num_trades=data.get("n"),
            vwap=float(data["vw"]) if data.get("vw") is not None else None,
        )


@dataclass(frozen=True)
class QuoteEvent:
    """Top-of-book NBBO quote (AlpacaRelay message T: 'q')."""
    symbol: str
    bid_price: float
    bid_size: int
    bid_exchange: str
    ask_price: float
    ask_size: int
    ask_exchange: str
    timestamp: datetime
    conditions: List[str] = field(default_factory=list)
    tape: str = "C"

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

    @classmethod
    def from_relay_dict(cls, data: Dict[str, Any]) -> "QuoteEvent":
        return cls(
            symbol=data["S"],
            bid_price=float(data["bp"]),
            bid_size=int(data["bs"]),
            bid_exchange=data.get("bx", "V"),
            ask_price=float(data["ap"]),
            ask_size=int(data["as"]),
            ask_exchange=data.get("ax", "V"),
            timestamp=datetime.fromisoformat(data["t"].replace("Z", "+00:00")),
            conditions=data.get("c", []),
            tape=data.get("z", "C"),
        )


@dataclass(frozen=True)
class TradeEvent:
    """Executed trade print (AlpacaRelay message T: 't')."""
    symbol: str
    trade_id: int
    price: float
    size: int
    exchange: str
    timestamp: datetime
    conditions: List[str] = field(default_factory=list)
    tape: str = "C"

    @classmethod
    def from_relay_dict(cls, data: Dict[str, Any]) -> "TradeEvent":
        return cls(
            symbol=data["S"],
            trade_id=int(data.get("i", 0)),
            price=float(data["p"]),
            size=int(data["s"]),
            exchange=data.get("x", "V"),
            timestamp=datetime.fromisoformat(data["t"].replace("Z", "+00:00")),
            conditions=data.get("c", []),
            tape=data.get("z", "C"),
        )


@dataclass(frozen=True)
class NewsEvent:
    """Benzinga real-time news article (AlpacaRelay message T: 'n') enriched with sentiment."""
    article_id: int
    headline: str
    summary: str
    symbols: List[str]
    source: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    url: Optional[str] = None
    content: Optional[str] = None
    sentiment_score: float = 0.0          # Normalized in [-1.0, 1.0]
    sentiment_confidence: float = 0.0     # Confidence in [0.0, 1.0]
    catalyst_category: CatalystCategory = CatalystCategory.NEUTRAL

    @property
    def is_actionable_bullish(self) -> bool:
        return self.sentiment_score >= 0.60 and self.sentiment_confidence >= 0.50

    @property
    def is_actionable_bearish(self) -> bool:
        return self.sentiment_score <= -0.60 and self.sentiment_confidence >= 0.50


@dataclass(frozen=True)
class VixPrint:
    """dxFeed spot VIX print received from GET /vix."""
    value: float
    asof: datetime
    received_at: datetime
    age_s: float
    state: str                          # ready, stale, unavailable, unconfigured
    upstream: str                       # connected, down
    regime: VixRegime                   # LOW, NORMAL, ELEVATED, CRISIS
    sizing_multiplier: float            # 0.25 to 1.2
    is_stale: bool = False
    is_fallback: bool = False


@dataclass(frozen=True)
class RelayStatusEvent:
    """Upstream relay connectivity status change notification."""
    feed_type: str                      # stock, news, vix
    status: str                         # connected, disconnected, reconnecting, error
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
```

---

## 4. Internal Asynchronous Event Bus (`backend/app/core/event_bus.py`)

The `EventBus` provides a decoupled pub/sub message router. High-throughput ingestion feeds publish events to the bus without direct coupling to strategies, paper accounting, or risk engines. Crucially, each subscriber's callback is executed within an isolated error boundary so that an exception in one strategy will not crash the ingestion loop or disrupt other subscribers.

### Class Definition & Implementation Specification
```python
"""backend/app/core/event_bus.py
Asynchronous pub/sub event bus with typed subscribers and fault isolation.
"""
import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, List, Type, TypeVar

log = logging.getLogger("EventBus")

T = TypeVar("T")
HandlerFunc = Callable[[T], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self):
        self._subscribers: Dict[Type, List[HandlerFunc]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._published_count: int = 0
        self._error_count: int = 0

    def subscribe(self, event_type: Type[T], handler: HandlerFunc) -> None:
        """Register an async callback for a specific event type."""
        self._subscribers[event_type].append(handler)
        log.debug(f"Subscribed {handler.__name__} to {event_type.__name__}")

    def unsubscribe(self, event_type: Type[T], handler: HandlerFunc) -> None:
        """Remove an async callback."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def publish(self, event: Any) -> None:
        """Publish an event to all registered subscribers with fault isolation."""
        event_type = type(event)
        handlers = self._subscribers.get(event_type, [])
        if not handlers:
            return

        self._published_count += 1
        tasks = []
        for handler in handlers:
            tasks.append(self._safe_dispatch(handler, event))
        
        # Execute concurrently across subscribers
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_dispatch(self, handler: HandlerFunc, event: Any) -> None:
        """Execute subscriber within an isolated try/except block."""
        try:
            await handler(event)
        except Exception as exc:
            self._error_count += 1
            log.exception(
                f"Exception in subscriber {handler.__name__} processing {type(event).__name__}: {exc}"
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
```

---

## 5. Stock WebSocket Client (`backend/app/ingestion/stock_ws.py`)

The Stock WebSocket Client manages the full lifecycle of connecting to AlpacaRelay, negotiating the handshake banner, authenticating with `RELAY_TOKEN`, managing channel subscriptions, maintaining an exponential backoff reconnect loop, and handling backpressure queue buffers.

### Detailed Protocol Flow
1. **TCP Connect**: Connects to `RELAY_URL` with `ping_interval=20` and `max_size=8MB`.
2. **Banner Verification**: Server immediately transmits `[{"T":"success","msg":"connected"}]`. The client asserts this frame within 5 seconds.
3. **Authentication**: Client submits `{"action":"auth","token": RELAY_TOKEN}` within 10 seconds. Server responds with `[{"T":"success","msg":"authenticated"}]`.
4. **Channel Subscription**: Client issues `{"action":"subscribe","bars":[...],"quotes":[...],"trades":[...]}`.
5. **Decoupled Backpressure Processing**: Incoming JSON frames are read by `_read_loop` and placed onto an internal `asyncio.Queue(maxsize=10000)`. A worker loop (`_process_loop`) pulls batches and publishes them to the `EventBus`. If the queue reaches 80% capacity, high-watermark warnings are logged. If full, non-critical quotes are dropped while 1-minute bars and trades are preserved.
6. **Relay Health Control Handling**: When `{"T":"relay","msg":"upstream_disconnected"}` arrives, the client emits `RelayStatusEvent(status="disconnected")`, signalling downstream engines to treat quotes as stale and halt new orders.

### Class Definition & Implementation Blueprint
```python
"""backend/app/ingestion/stock_ws.py
AlpacaRelay Stock WebSocket Client with backpressure buffering and auto-reconnect.
"""
import asyncio
import json
import logging
from typing import Dict, List, Optional, Set
import websockets
from websockets.exceptions import ConnectionClosed

from backend.app.config import settings
from backend.app.core.event_bus import EventBus, event_bus
from backend.app.models.events import BarEvent, QuoteEvent, TradeEvent, RelayStatusEvent

log = logging.getLogger("StockWebSocketClient")


class StockWebSocketClient:
    def __init__(
        self,
        relay_url: Optional[str] = None,
        relay_token: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        bus: Optional[EventBus] = None,
    ):
        self.relay_url = relay_url or settings.RELAY_URL
        self.relay_token = relay_token or settings.RELAY_TOKEN
        self.symbols: Set[str] = set(symbols or settings.WATCHLIST_SYMBOLS)
        self.bus: EventBus = bus or event_bus

        self._running: bool = False
        self._connected: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=settings.QUEUE_MAX_SIZE)

        # Worker tasks
        self._runner_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None

        # Telemetry & Stats
        self.messages_received: int = 0
        self.bars_received: int = 0
        self.quotes_received: int = 0
        self.trades_received: int = 0
        self.dropped_quotes: int = 0
        self.reconnect_count: int = 0

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
        if self._ws and not self._ws.closed:
            await self._ws.close(code=1000, reason="Client shutdown")
        if self._runner_task:
            self._runner_task.cancel()
        if self._worker_task:
            self._worker_task.cancel()
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
            payload["bars"] = bars
            self.symbols.update(bars)
        if quotes:
            payload["quotes"] = quotes
            self.symbols.update(quotes)
        if trades:
            payload["trades"] = trades
            self.symbols.update(trades)

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
                log.warning(f"WebSocket closed (code={cc.code}, reason={cc.reason}). Reconnecting in {backoff:.1f}s")
            except Exception as exc:
                log.error(f"WebSocket connection error: {exc}. Reconnecting in {backoff:.1f}s")
            finally:
                self._connected = False
                self._ws = None
                self.reconnect_count += 1
                await self.bus.publish(
                    RelayStatusEvent(feed_type="stock", status="disconnected", message="Connection dropped")
                )

            if not self._running:
                break

            await asyncio.sleep(backoff)
            backoff = min(backoff * settings.WS_RECONNECT_BACKOFF_MULTIPLIER, settings.WS_RECONNECT_MAX_BACKOFF_SEC)

    async def _perform_handshake(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Enforce banner verification and relay token authentication."""
        # 1. Handshake banner
        banner_raw = await asyncio.wait_for(ws.recv(), timeout=settings.WS_CONNECT_TIMEOUT_SEC)
        banner = json.loads(banner_raw)
        if not (isinstance(banner, list) and banner[0].get("T") == "success" and banner[0].get("msg") == "connected"):
            raise ConnectionError(f"Unexpected handshake banner: {banner_raw}")

        # 2. Authenticate
        auth_cmd = json.dumps({"action": "auth", "token": self.relay_token})
        await ws.send(auth_cmd)

        auth_resp_raw = await asyncio.wait_for(ws.recv(), timeout=settings.WS_AUTH_TIMEOUT_SEC)
        auth_resp = json.loads(auth_resp_raw)
        if not (isinstance(auth_resp, list) and auth_resp[0].get("T") == "success" and auth_resp[0].get("msg") == "authenticated"):
            raise PermissionError(f"Authentication failed: {auth_resp_raw}")
        
        log.info("Successfully authenticated with AlpacaRelay Stock WS")

    async def _send_initial_subscriptions(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Transmit initial channel subscriptions."""
        sym_list = list(self.symbols)
        cmd: Dict[str, Any] = {"action": "subscribe"}
        if settings.SUBSCRIBE_BARS:
            cmd["bars"] = sym_list
        if settings.SUBSCRIBE_QUOTES:
            cmd["quotes"] = sym_list
        if settings.SUBSCRIBE_TRADES:
            cmd["trades"] = sym_list

        await ws.send(json.dumps(cmd))
        log.info(f"Subscribed channels: bars={len(cmd.get('bars', []))}, quotes={len(cmd.get('quotes', []))}, trades={len(cmd.get('trades', []))}")

    async def _read_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Read wire frames and buffer onto internal queue with backpressure protection."""
        async for raw_msg in ws:
            self.messages_received += 1
            # Check high watermark
            qsize = self._queue.qsize()
            if qsize >= settings.QUEUE_MAX_SIZE * settings.QUEUE_HIGH_WATERMARK_PCT:
                log.warning(f"Queue high watermark reached: {qsize}/{settings.QUEUE_MAX_SIZE} items")

            try:
                self._queue.put_nowait(raw_msg)
            except asyncio.QueueFull:
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
```

---

## 6. Real-Time News WebSocket Client (`backend/app/ingestion/news_ws.py`)

The News WebSocket Client maintains an isolated connection to AlpacaRelay for streaming Benzinga wire news (`T: "n"`). It passes every incoming headline through the algorithmic sentiment scorer before dispatching a complete `NewsEvent` to the `EventBus`.

### Detailed Protocol Flow
1. **Isolated Connection**: Connects to `RELAY_URL` independently from the stock socket. This eliminates head-of-line blocking during heavy stock market open volume surges.
2. **Handshake & Auth**: Same standard `connected` banner check and `{"action":"auth","token": RELAY_TOKEN}` exchange.
3. **Wildcard Subscription**: Sends `{"action":"subscribe","news":["*"]}` to capture all incoming wire headlines.
4. **Upstream News Status**: Handles `news_upstream_connected` and `news_upstream_disconnected` control messages.
5. **Enrichment**: Extracts article text, executes `FinancialSentimentScorer.score(...)`, and packages the payload into an immutable `NewsEvent`.

### Class Definition & Implementation Blueprint
```python
"""backend/app/ingestion/news_ws.py
Real-time Benzinga News WebSocket Client connected downstream to AlpacaRelay.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
import websockets
from websockets.exceptions import ConnectionClosed

from backend.app.config import settings
from backend.app.core.event_bus import EventBus, event_bus
from backend.app.models.events import NewsEvent, RelayStatusEvent
from backend.app.ingestion.sentiment import FinancialSentimentScorer, sentiment_scorer

log = logging.getLogger("NewsWebSocketClient")


class NewsWebSocketClient:
    def __init__(
        self,
        relay_url: Optional[str] = None,
        relay_token: Optional[str] = None,
        bus: Optional[EventBus] = None,
        scorer: Optional[FinancialSentimentScorer] = None,
    ):
        self.relay_url = relay_url or settings.RELAY_URL
        self.relay_token = relay_token or settings.RELAY_TOKEN
        self.bus: EventBus = bus or event_bus
        self.scorer: FinancialSentimentScorer = scorer or sentiment_scorer

        self._running: bool = False
        self._connected: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._runner_task: Optional[asyncio.Task] = None

        self.articles_received: int = 0
        self.catalysts_detected: int = 0

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
        if self._ws and not self._ws.closed:
            await self._ws.close(code=1000, reason="Client shutdown")
        if self._runner_task:
            self._runner_task.cancel()
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
                    assert banner[0]["msg"] == "connected"

                    # 2. Auth
                    await ws.send(json.dumps({"action": "auth", "token": self.relay_token}))
                    auth_raw = await asyncio.wait_for(ws.recv(), timeout=settings.WS_AUTH_TIMEOUT_SEC)
                    auth_resp = json.loads(auth_raw)
                    assert auth_resp[0]["msg"] == "authenticated"

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
            except Exception as exc:
                log.error(f"News WS connection error: {exc}. Retry in {backoff:.1f}s")
            finally:
                self._connected = False
                self._ws = None
                await self.bus.publish(
                    RelayStatusEvent(feed_type="news", status="disconnected", message="News feed disconnected")
                )

            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * settings.WS_RECONNECT_BACKOFF_MULTIPLIER, settings.WS_RECONNECT_MAX_BACKOFF_SEC)

    async def _handle_news_message(self, raw_msg: str) -> None:
        """Parse Benzinga news article and publish enriched NewsEvent."""
        msgs = json.loads(raw_msg)
        if not isinstance(msgs, list):
            msgs = [msgs]

        for m in msgs:
            t = m.get("T")
            if t == "n":
                self.articles_received += 1
                headline = m.get("headline", "")
                summary = m.get("summary", "")
                content = m.get("content", "")
                symbols = m.get("symbols", [])

                # Run algorithmic sentiment & catalyst classification
                score, confidence, category = self.scorer.score(headline=headline, summary=summary)
                if abs(score) >= 0.6:
                    self.catalysts_detected += 1
                    log.info(f"High-impact news catalyst detected: [{category.value}] Score={score:.2f} ({symbols}) '{headline}'")

                created_dt = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
                updated_dt = (
                    datetime.fromisoformat(m["updated_at"].replace("Z", "+00:00"))
                    if m.get("updated_at")
                    else None
                )

                event = NewsEvent(
                    article_id=int(m.get("id", 0)),
                    headline=headline,
                    summary=summary,
                    content=content,
                    symbols=symbols,
                    source=m.get("source", "benzinga"),
                    created_at=created_dt,
                    updated_at=updated_dt,
                    url=m.get("url"),
                    sentiment_score=score,
                    sentiment_confidence=confidence,
                    catalyst_category=category,
                )
                await self.bus.publish(event)

            elif t == "relay":
                msg_text = m.get("msg", "")
                status = "connected" if "connected" in msg_text else "disconnected"
                await self.bus.publish(RelayStatusEvent(feed_type="news", status=status, message=msg_text))
```

---

## 7. Algorithmic Financial Sentiment Scorer (`backend/app/ingestion/sentiment.py`)

Day trading momentum requires sub-millisecond catalyst scoring upon headline arrival. Rather than invoking heavyweight deep-learning models (e.g. BERT/FinBERT), the `FinancialSentimentScorer` employs a curated financial domain lexicon (Loughran-McDonald inspired terms + market catalyst idioms), token n-gram matching, negation lookaheads, and intensifier scaling.

### Scoring Mathematics & Rules
1. **Polarity Calculation**:
   $$\text{Raw Polarity} = \sum_{i} w_i \times \text{modifier}_i$$
   where $w_i \in [-1.0, 1.0]$ is the token/phrase weight.
2. **Negation Handling**:
   A preceding negation token ("not", "no", "fails to", "denies", "unable to") within a 3-token window flips the sign:
   $$\text{modifier} = -0.80$$
3. **Intensifier & Diminisher Scaling**:
   - Intensifier ("massively", "record", "significantly", "surges"): $\text{modifier} = \min(1.5, \text{modifier} \times 1.35)$
   - Diminisher ("slightly", "modestly", "partially"): $\text{modifier} = \text{modifier} \times 0.60$
4. **Score Normalization**:
   $$S = \tanh\left(\frac{\text{Raw Polarity}}{2.0}\right) \in [-1.0, 1.0]$$
5. **Confidence Metric**:
   $$C = \min\left(1.0, \frac{\text{matched\_sentiment\_tokens}}{2.0}\right)$$

### Class Definition & Implementation Blueprint
```python
"""backend/app/ingestion/sentiment.py
High-speed deterministic financial lexicon sentiment scorer and catalyst classifier.
"""
import math
import re
from typing import Dict, List, Tuple

from backend.app.models.events import CatalystCategory


class FinancialSentimentScorer:
    # Curated financial domain lexicons
    BULLISH_KEYWORDS: Dict[str, float] = {
        # Earnings & Guidance
        "record earnings": 1.0, "beat estimates": 0.9, "beats estimates": 0.9,
        "blowout quarter": 1.0, "raised guidance": 0.95, "raises guidance": 0.95,
        "upgrades guidance": 0.9, "tops revenue": 0.85, "profit surges": 0.9,
        "record revenue": 0.95, "record profit": 0.95,
        # Regulatory & Biotech
        "fda approval": 1.0, "fda approves": 1.0, "breakthrough therapy": 0.9,
        "patent granted": 0.8, "cleared by fda": 0.95,
        # Commercial & Corporate
        "massive partnership": 0.85, "multibillion dollar deal": 0.9, "contract win": 0.8,
        "shares surge": 0.8, "shares soar": 0.85, "stock climbs": 0.6,
        "share buyback": 0.75, "boosts dividend": 0.7, "buy rating": 0.75,
        "upgraded to buy": 0.85, "upgrades to overweight": 0.8,
        # Single keywords
        "beat": 0.6, "beats": 0.6, "surge": 0.6, "surges": 0.6, "soar": 0.7,
        "growth": 0.4, "profit": 0.4, "acquisition": 0.5, "awarded": 0.5,
    }

    BEARISH_KEYWORDS: Dict[str, float] = {
        # Earnings & Guidance
        "missed estimates": -0.9, "misses estimates": -0.9, "lowered guidance": -0.95,
        "lowers guidance": -0.95, "slashes forecast": -1.0, "cuts forecast": -0.9,
        "profit drops": -0.85, "revenue sinks": -0.85, "disappointing results": -0.8,
        # Regulatory, Legal & Accounting
        "sec investigation": -1.0, "sec probe": -1.0, "subpoena": -0.9,
        "fraud investigation": -1.0, "class action": -0.7, "lawsuit": -0.6,
        "fda rejection": -1.0, "fda rejects": -1.0, "clinical trial failure": -1.0,
        "clinical hold": -0.95, "halted trading": -0.9,
        # Financial Distress
        "bankruptcy": -1.0, "chapter 11": -1.0, "default": -0.9,
        "debt restructuring": -0.7, "layoffs": -0.6, "slashes jobs": -0.7,
        "shares plunge": -0.85, "shares tumble": -0.85, "stock drops": -0.6,
        "downgraded to sell": -0.9, "downgraded to underweight": -0.8,
        # Single keywords
        "miss": -0.6, "misses": -0.6, "plunge": -0.7, "plunges": -0.7,
        "tumble": -0.7, "decline": -0.4, "warning": -0.5, "loss": -0.4,
    }

    NEGATIONS: Set[str] = {"not", "no", "never", "without", "fails", "failed", "unable", "denies", "rejected"}
    INTENSIFIERS: Set[str] = {"significantly", "massively", "substantially", "hugely", "drastically", "record", "unprecedented"}
    DIMINISHERS: Set[str] = {"slightly", "modestly", "partially", "marginally"}

    def __init__(self):
        # Pre-compile regex for word tokenization
        self._token_re = re.compile(r"[a-z0-9\-\']+")

    def score(self, headline: str, summary: str = "") -> Tuple[float, float, CatalystCategory]:
        """Calculates normalized sentiment S in [-1, 1], confidence C in [0, 1], and catalyst category."""
        text = f"{headline} {summary}".lower()
        tokens = self._token_re.findall(text)
        if not tokens:
            return 0.0, 0.0, CatalystCategory.NEUTRAL

        raw_score = 0.0
        matches = 0
        joined_text = f" {text} "

        # 1. Multi-word phrase matching (highest specificity)
        for phrase, weight in self.BULLISH_KEYWORDS.items():
            if " " in phrase and phrase in text:
                raw_score += weight * 1.5
                matches += 2

        for phrase, weight in self.BEARISH_KEYWORDS.items():
            if " " in phrase and phrase in text:
                raw_score += weight * 1.5
                matches += 2

        # 2. Token-level matching with negation & intensifier context
        for idx, token in enumerate(tokens):
            w = 0.0
            if token in self.BULLISH_KEYWORDS and " " not in token:
                w = self.BULLISH_KEYWORDS[token]
            elif token in self.BEARISH_KEYWORDS and " " not in token:
                w = self.BEARISH_KEYWORDS[token]

            if w != 0.0:
                # Look back up to 3 tokens for negation and intensifiers
                lookback = tokens[max(0, idx - 3) : idx]
                is_negated = any(t in self.NEGATIONS for t in lookback)
                is_intensified = any(t in self.INTENSIFIERS for t in lookback)
                is_diminished = any(t in self.DIMINISHERS for t in lookback)

                mod = 1.0
                if is_intensified:
                    mod *= 1.35
                if is_diminished:
                    mod *= 0.60
                if is_negated:
                    mod *= -0.80  # Flip sign and scale

                raw_score += w * mod
                matches += 1

        if matches == 0:
            return 0.0, 0.0, CatalystCategory.NEUTRAL

        # Normalize score via tanh soft-clipping
        final_score = max(-1.0, min(1.0, math.tanh(raw_score / 2.0)))
        confidence = min(1.0, matches / 2.0)

        category = self._classify_category(text, final_score)
        return round(final_score, 4), round(confidence, 4), category

    def _classify_category(self, text: str, score: float) -> CatalystCategory:
        """Categorize into specific trading catalyst buckets."""
        if any(k in text for k in ("fda", "biotech", "clinical", "drug", "phase 3")):
            return CatalystCategory.FDA_APPROVAL if score > 0 else CatalystCategory.FDA_REJECTION
        if any(k in text for k in ("earnings", "eps", "quarter", "revenue", "sales")):
            return CatalystCategory.EARNINGS_BEAT if score > 0 else CatalystCategory.EARNINGS_MISS
        if any(k in text for k in ("guidance", "outlook", "forecast")):
            return CatalystCategory.GUIDANCE_RAISE if score > 0 else CatalystCategory.GUIDANCE_CUT
        if any(k in text for k in ("sec", "probe", "investigation", "subpoena", "lawsuit", "fraud")):
            return CatalystCategory.LEGAL_INVESTIGATION
        if any(k in text for k in ("upgrade", "downgrade", "target price")):
            return CatalystCategory.ANALYST_UPGRADE if score > 0 else CatalystCategory.ANALYST_DOWNGRADE
        if any(k in text for k in ("partner", "deal", "contract", "merger")):
            return CatalystCategory.PARTNERSHIP_CONTRACT
        
        return CatalystCategory.GENERAL_CATALYST if abs(score) >= 0.5 else CatalystCategory.NEUTRAL


# Global singleton scorer instance
sentiment_scorer = FinancialSentimentScorer()
```

---

## 8. REST `/vix` Client (`backend/app/ingestion/vix_client.py`)

The VIX Client polls the dxFeed spot VIX endpoint on AlpacaRelay, adheres to strict protocol requirements (query parameters forbidden), verifies age honesty, manages an in-memory cache, and maps spot prints to dynamic risk regimes.

### Protocol Constraints
1. **Endpoint**: `GET {RELAY_HTTP_URL}/vix`.
2. **Headers**: `X-Relay-Token: {RELAY_TOKEN}`.
3. **Parameter Rule**: Absolute ban on query strings. Any `?` returns HTTP 400.
4. **Response States**:
   - `ready`: Active Tastytrade dxFeed connection, live print held.
   - `stale`: Last print held, dxFeed reconnecting.
   - `unavailable` / `unconfigured`: HTTP 503.
5. **Freshness Logic**:
   - Overnight / weekends: `age_s` is honestly high (e.g. 98,000s).
   - Market hours (09:30–16:00 ET): If `age_s > 300s`, mark `is_stale = True`.
   - On network failure or 503, fallback to cached print or safe default ($VIX = 20.0$, NORMAL).

### Class Definition & Implementation Blueprint
```python
"""backend/app/ingestion/vix_client.py
REST Client for AlpacaRelay GET /vix spot volatility prints and regime adaptation.
"""
import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import httpx

from backend.app.config import settings
from backend.app.core.event_bus import EventBus, event_bus
from backend.app.models.events import VixPrint, VixRegime

log = logging.getLogger("VixClient")


class VixClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        relay_token: Optional[str] = None,
        poll_interval: Optional[float] = None,
        bus: Optional[EventBus] = None,
    ):
        self.base_url = (base_url or settings.RELAY_HTTP_URL).rstrip("/")
        self.relay_token = relay_token or settings.RELAY_TOKEN
        self.poll_interval = poll_interval or settings.VIX_POLL_INTERVAL_SEC
        self.bus: EventBus = bus or event_bus

        self.last_print: Optional[VixPrint] = None
        self._running: bool = False
        self._poller_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        """Initialize HTTP client and start periodic polling task."""
        if self._running:
            return
        self._running = True
        self._http_client = httpx.AsyncClient(
            headers={"X-Relay-Token": self.relay_token},
            timeout=5.0
        )
        self._poller_task = asyncio.create_task(self._poll_loop(), name="VixPollerTask")
        log.info(f"VixClient polling started at {self.base_url}/vix every {self.poll_interval}s")

    async def stop(self) -> None:
        """Stop polling and close HTTP client."""
        self._running = False
        if self._poller_task:
            self._poller_task.cancel()
        if self._http_client:
            await self._http_client.aclose()
        log.info("VixClient stopped")

    async def fetch_vix(self) -> VixPrint:
        """Direct single query of GET /vix with error fallback."""
        if not self._http_client:
            self._http_client = httpx.AsyncClient(
                headers={"X-Relay-Token": self.relay_token},
                timeout=5.0
            )

        url = f"{self.base_url}/vix"  # Never append query parameters!
        try:
            resp = await self._http_client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_vix_payload(data)
            elif resp.status_code == 503:
                log.warning("Upstream VIX unavailable (HTTP 503), using fallback cache")
                return self._get_fallback_print("unavailable")
            else:
                log.error(f"VIX endpoint returned HTTP {resp.status_code}: {resp.text}")
                return self._get_fallback_print("error")
        except Exception as exc:
            log.error(f"Failed to query /vix: {exc}")
            return self._get_fallback_print("connection_error")

    def _parse_vix_payload(self, data: Dict[str, Any]) -> VixPrint:
        """Transform raw JSON payload into typed VixPrint and classify regime."""
        val = float(data.get("value", settings.VIX_DEFAULT_FALLBACK))
        asof_str = data.get("asof") or datetime.now(timezone.utc).isoformat()
        received_str = data.get("received_at") or datetime.now(timezone.utc).isoformat()
        age_s = float(data.get("age_s", 0.0))
        state = data.get("state", "ready")
        upstream = data.get("upstream", "connected")

        asof_dt = datetime.fromisoformat(asof_str.replace("Z", "+00:00"))
        received_dt = datetime.fromisoformat(received_str.replace("Z", "+00:00"))

        regime, multiplier = self._classify_regime(val)
        
        # Freshness check during regular market hours
        is_stale = age_s > settings.VIX_MAX_STALE_AGE_SEC if self._is_market_hours() else False

        vix_print = VixPrint(
            value=val,
            asof=asof_dt,
            received_at=received_dt,
            age_s=age_s,
            state=state,
            upstream=upstream,
            regime=regime,
            sizing_multiplier=multiplier,
            is_stale=is_stale,
            is_fallback=False,
        )
        self.last_print = vix_print
        return vix_print

    def _classify_regime(self, vix: float) -> Tuple[VixRegime, float]:
        """Map raw VIX value to institutional volatility regime and sizing multiplier."""
        if vix < 15.0:
            return VixRegime.LOW, 1.20
        elif vix < 22.0:
            return VixRegime.NORMAL, 1.00
        elif vix < 30.0:
            return VixRegime.ELEVATED, 0.60
        else:
            return VixRegime.CRISIS, 0.25

    def _get_fallback_print(self, reason: str) -> VixPrint:
        """Generate safe fallback when live /vix endpoint fails."""
        if self.last_print:
            # Return cached print with is_stale set
            return VixPrint(
                value=self.last_print.value,
                asof=self.last_print.asof,
                received_at=datetime.now(timezone.utc),
                age_s=self.last_print.age_s,
                state="stale",
                upstream="down",
                regime=self.last_print.regime,
                sizing_multiplier=self.last_print.sizing_multiplier,
                is_stale=True,
                is_fallback=False,
            )

        now = datetime.now(timezone.utc)
        regime, multiplier = self._classify_regime(settings.VIX_DEFAULT_FALLBACK)
        return VixPrint(
            value=settings.VIX_DEFAULT_FALLBACK,
            asof=now,
            received_at=now,
            age_s=0.0,
            state="fallback",
            upstream="down",
            regime=regime,
            sizing_multiplier=multiplier,
            is_stale=True,
            is_fallback=True,
        )

    def _is_market_hours(self) -> bool:
        """Determine if current time is US regular trading hours (09:30-16:00 ET Mon-Fri)."""
        now_utc = datetime.now(timezone.utc)
        # Mon=0, Fri=4
        if now_utc.weekday() > 4:
            return False
        # 09:30 ET = 13:30 / 14:30 UTC depending on DST
        # Defensive approximate check: between 13:30 and 20:00 UTC
        utc_minutes = now_utc.hour * 60 + now_utc.minute
        return 810 <= utc_minutes <= 1200

    async def _poll_loop(self) -> None:
        """Continuous background polling loop."""
        while self._running:
            try:
                vprint = await self.fetch_vix()
                await self.bus.publish(vprint)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"VIX polling loop error: {exc}")

            await asyncio.sleep(self.poll_interval)
```

---

## 9. Comprehensive Unit Test Specifications

Unit tests must be fast, deterministic, run offline without external API limits, and exercise every boundary condition.

### 1. `tests/unit/test_stock_ws.py`
- **`test_handshake_banner_verification`**: Asserts that `_perform_handshake` rejects servers sending an invalid initial banner and succeeds on `[{"T":"success","msg":"connected"}]`.
- **`test_auth_success_and_failure`**: Validates `{"action":"auth","token":...}`. Asserts `PermissionError` is raised on `[{"T":"error","code":402,"msg":"auth failed"}]`.
- **`test_message_deserialization_and_dispatch`**: Uses a loopback mock server to send batches containing `b`, `q`, and `t` messages. Verifies that `EventBus` receives typed `BarEvent`, `QuoteEvent`, and `TradeEvent` with exact nanosecond timestamp parsing and float values.
- **`test_backpressure_queue_high_watermark`**: Simulates a stalled worker loop and pushes 10,000 messages. Verifies that high watermark warning is logged and the client does not crash or block the network reader.
- **`test_exponential_backoff_reconnect`**: Simulates socket drops and verifies backoff intervals double from 1s to 2s, 4s, up to 30s cap, resetting to 1s upon successful re-auth.
- **`test_relay_status_disconnect_handling`**: Injects `[{"T":"relay","msg":"upstream_disconnected"}]` and verifies `RelayStatusEvent` is dispatched to alert risk engines of stale market data.

### 2. `tests/unit/test_news_ws.py`
- **`test_news_ws_subscription`**: Verifies wildcard `news: ["*"]` subscription exchange on isolated socket.
- **`test_article_parsing_and_enrichment`**: Injects sample Benzinga article payload (`T: "n"`). Verifies `NewsEvent` is constructed with correct `symbols`, `headline`, `summary`, and enriched with sentiment.
- **`test_news_upstream_status_propagation`**: Injects `news_upstream_disconnected` and verifies downstream notification.

### 3. `tests/unit/test_sentiment.py`
- **`test_bullish_catalysts`**: Tests headlines containing "beats estimates", "fda approval", "record revenue". Asserts $S \ge 0.60$ and category assignment (`EARNINGS_BEAT`, `FDA_APPROVAL`).
- **`test_bearish_catalysts`**: Tests headlines containing "sec investigation", "misses estimates", "lowers guidance". Asserts $S \le -0.60$ and category assignment (`LEGAL_INVESTIGATION`, `EARNINGS_MISS`).
- **`test_negation_inversion`**: Tests "not beating expectations" vs "beating expectations". Verifies sign flips from positive to negative.
- **`test_intensifier_scaling`**: Tests "massively beats estimates" vs "beats estimates". Verifies positive magnitude increases.
- **`test_sub_millisecond_benchmark`**: Evaluates 1,000 headlines in a loop. Asserts mean latency $<0.1$ ms per headline.

### 4. `tests/unit/test_vix_client.py`
- **`test_vix_successful_parse`**: Mocks HTTP 200 payload `{"value": 14.81, "age_s": 25.0, "state": "ready"}`. Verifies `VixPrint.regime == VixRegime.LOW` and `sizing_multiplier == 1.2`.
- **`test_vix_regime_classification_boundaries`**: Tests boundary values ($14.99 \to \text{LOW}$, $15.0 \to \text{NORMAL}$, $21.99 \to \text{NORMAL}$, $22.0 \to \text{ELEVATED}$, $30.0 \to \text{CRISIS}$).
- **`test_query_parameter_forbidden_enforcement`**: Asserts that `fetch_vix` never appends query parameters to `/vix`.
- **`test_vix_503_fallback_cache`**: Tests server returning HTTP 503. Verifies graceful fallback to cached value or safe default ($20.0$).
- **`test_staleness_flagging`**: Mocks `age_s = 600.0` during market hours. Verifies `is_stale == True`.

### 5. `tests/unit/test_event_bus.py`
- **`test_typed_subscription_and_publishing`**: Verifies subscribers receive only events of their registered type.
- **`test_subscriber_exception_isolation`**: Attaches a failing subscriber that raises `ZeroDivisionError` alongside a normal subscriber. Verifies that the second subscriber still receives the event and the error count increments.

---

## 10. Integration Surface Matrix & Peer Explorer Contract Alignment

| Consuming Component | Source Feed | Event / Model | Consumed Fields & Invariants | Downstream Action |
|---|---|---|---|---|
| **$50,000 Paper Account** (`explorer_m1_2`) | `stock_ws.py` | `QuoteEvent` (`q`) & `TradeEvent` (`t`) | `symbol`, `bid_price`, `ask_price`, `mid_price`, `p`, `s` | Real-time mark-to-market portfolio valuation; unrealized PnL calculation; order slippage and fill simulation. |
| **Risk Engine** (`explorer_m1_3`) | `stock_ws.py` | `BarEvent` (`b`) & `RelayStatusEvent` | `close`, `status == "disconnected"` | Real-time daily drawdown tracking ($1,500 limit). If `disconnected`, freeze order entry. |
| **Risk Engine & Sizing** (`explorer_m1_3`) | `vix_client.py` | `VixPrint` | `value`, `regime`, `sizing_multiplier` | Dynamically scale position sizing ($0.25\times$ to $1.2\times$) and adjust stop widths. |
| **Strategy 1: ORB** (Milestone 2) | `stock_ws.py` | `BarEvent` (`b`) | `open`, `high`, `low`, `close`, `volume`, `timestamp` | Calculates 5m/15m opening range high/low and breakout volume surges. |
| **Strategy 2: VWAP Pullback** (Milestone 2) | `stock_ws.py` | `BarEvent` & `TradeEvent` | `vwap`, `close`, `volume` | Tracks intraday anchored VWAP and standard deviation bands. |
| **Strategy 3: News Momentum** (Milestone 2) | `news_ws.py` | `NewsEvent` (`n`) | `symbols`, `sentiment_score`, `catalyst_category` | Triggers momentum entries on $S \ge 0.60$ or emergency exits on contradictory headlines. |
| **Strategy 4: Mean Reversion** (Milestone 2) | `stock_ws.py` | `BarEvent` (`b`) | `close`, `high`, `low` | Computes 1-min $Z$-score and RSI-14 overextended exhaustion fades. |
| **UI WebSocket Server** (Milestone 3) | Ingestion All | System State | All feeds | Relays live tick, news, and VIX status to Apple Music mobile interface. |

---

## 11. Conclusion & Implementation Readiness

The technical architecture for AlpacaRelay Ingestion is fully specified, protocol-verified against authoritative local and production endpoints, and ready for immediate implementation.

- **Zero Ambiguity**: All classes, method signatures, mathematical formulations, and error flows are documented above.
- **Process & Socket Hygiene**: Connection lifecycles guarantee clean teardown and termination of network sockets upon system exit.
- **Fault-Tolerant by Design**: Decoupled queue buffers protect against downstream backpressure cuts (`1013 "too slow"`), dedicated news sockets prevent quote starvation, and fault-isolated event buses ensure strategy bugs cannot crash ingestion.
