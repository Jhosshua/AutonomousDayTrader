"""
AlpacaRelay Mock Server & Historical/Synthetic Replay Engine.

Deterministic, protocol-accurate local mock server for downstream AlpacaRelay consumers.
Implements:
- Stock WebSocket (/v2/stocks or /) on port 8080:
  - Connection banner: [{"T": "success", "msg": "connected"}]
  - Auth: {"action": "auth", "token": "..."} or {"action": "auth", "key": "..."}
  - Subscription: {"action": "subscribe", "bars": [...], "quotes": [...], "trades": [...], "news": [...]}
  - Streaming message arrays for bars ('b'), quotes ('q'), trades ('t'), news ('n'), status ('relay')
- News channel on the shared downstream WebSocket
- Plain HTTP REST endpoints on the same port:
  - GET /vix: dxFeed spot VIX print (requires X-Relay-Token, rejects query params)
  - GET /health: Relay telemetry
  - GET /data/v2/stocks/{symbol}/bars: Historical bars proxy
- Deterministic feed replayer supporting variable speeds (1x to 10x) and step ticks.
- Full process hygiene and clean socket release.
"""

from __future__ import annotations

import argparse
import asyncio
import http
import json
import logging
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

import websockets
try:
    import websockets.legacy.server as legacy_ws
except ImportError:
    legacy_ws = websockets  # type: ignore

logger = logging.getLogger("mock_relay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DEFAULT_RELAY_TOKEN = os.environ.get(
    "RELAY_TOKEN", "local-mock-relay-token"
)
DEFAULT_PORT = int(os.environ.get("MOCK_PORT", os.environ.get("PORT", "8080")))


class MockRelayClient:
    """Represents a connected downstream client session."""

    def __init__(self, ws: Any, client_id: str, path: str):
        self.ws = ws
        self.client_id = client_id
        self.path = path
        self.authenticated = False
        self.subscriptions: Dict[str, Set[str]] = {
            "bars": set(),
            "quotes": set(),
            "trades": set(),
            "news": set(),
        }

    def subscribes_to(self, channel: str, symbol: Optional[str] = None) -> bool:
        if not self.authenticated:
            return False
        sub_set = self.subscriptions.get(channel, set())
        if "*" in sub_set:
            return True
        if symbol and symbol.upper() in sub_set:
            return True
        return False

    async def send_json(self, payload: List[Dict[str, Any]]) -> None:
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as e:
            logger.debug(f"Failed to send to client {self.client_id}: {e}")


class MockAlpacaRelayServer:
    """Deterministic mock AlpacaRelay server & feed replayer."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        relay_token: str = DEFAULT_RELAY_TOKEN,
        auth_timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.relay_token = relay_token
        self.auth_timeout = auth_timeout

        self._server: Optional[Any] = None
        self._clients: Dict[str, MockRelayClient] = {}
        self._client_counter = 0
        self._is_running = False

        # VIX State
        self.vix_value: float = 18.45
        self.vix_state: str = "ready"
        self.vix_source: str = "Tastytrade/dxFeed spot VIX (Trade.time)"
        self.vix_observations: List[Dict[str, Any]] = [
            {"value": 18.45, "asof": datetime.now(timezone.utc).isoformat()}
        ]

        # In-memory historical bars cache for GET /data/...
        self.historical_bars: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        # Queued events for step replay
        self.replay_queue: List[Dict[str, Any]] = []
        self.replay_index: int = 0
        self._replay_task: Optional[asyncio.Task] = None

        # Load fixtures if present
        self._fixtures_dir = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "e2e" / "fixtures"
        self._load_default_fixtures()

    def _load_default_fixtures(self) -> None:
        """Pre-populate bars fixtures if available on disk."""
        bars_path = self._fixtures_dir / "bars_fixtures.json"
        if bars_path.is_file():
            try:
                with open(bars_path, "r", encoding="utf-8") as f:
                    bars = json.load(f)
                    for bar in bars:
                        sym = bar.get("S")
                        if sym:
                            self.historical_bars[sym.upper()].append(bar)
            except Exception as exc:
                logger.warning(f"Could not load bars fixtures: {exc}")

    def set_vix(self, value: float, state: str = "ready") -> None:
        """Dynamically update spot VIX print."""
        self.vix_value = value
        self.vix_state = state
        self.vix_observations.append(
            {"value": value, "asof": datetime.now(timezone.utc).isoformat()}
        )
        if len(self.vix_observations) > 100:
            self.vix_observations.pop(0)

    def _auth_valid(self, headers: Any) -> bool:
        """Validate auth header in REST requests."""
        token = headers.get("X-Relay-Token") or headers.get("APCA-API-KEY-ID")
        return token == self.relay_token

    async def _handle_http(self, path: str, headers: Any) -> Optional[Tuple[int, List[Tuple[str, str]], bytes]]:
        """Dual HTTP handler using websockets process_request hook."""
        # If WebSocket upgrade requested, allow websocket handshake through
        if headers.get("Upgrade", "").lower() == "websocket":
            return None

        # REST GET /vix
        if path == "/vix" or path.startswith("/vix?"):
            if not self._auth_valid(headers):
                body = json.dumps({"relay_error": "missing or bad relay token"}).encode("utf-8")
                return http.HTTPStatus.UNAUTHORIZED, [("Content-Type", "application/json")], body

            if path != "/vix":
                body = json.dumps({"error": "/vix takes no query parameters"}).encode("utf-8")
                return http.HTTPStatus.BAD_REQUEST, [("Content-Type", "application/json")], body

            payload = {
                "state": self.vix_state,
                "source": self.vix_source,
                "upstream": "connected",
                "value": self.vix_value,
                "asof": datetime.now(timezone.utc).isoformat(),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "age_s": 0.5,
                "observations": self.vix_observations[-5:],
            }
            status = http.HTTPStatus.OK if self.vix_state == "ready" else http.HTTPStatus.SERVICE_UNAVAILABLE
            return status, [("Content-Type", "application/json")], json.dumps(payload).encode("utf-8")

        # REST GET /health
        if path == "/health":
            payload = {
                "upstream": "connected",
                "clients": len(self._clients),
                "vix": {"state": self.vix_state, "value": self.vix_value},
                "feed": "sip",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return http.HTTPStatus.OK, [("Content-Type", "application/json")], json.dumps(payload).encode("utf-8")

        # REST GET /data/v2/stocks/{symbol}/bars
        if path.startswith("/data/v2/stocks/"):
            if not self._auth_valid(headers):
                body = json.dumps({"relay_error": "missing or bad relay token"}).encode("utf-8")
                return http.HTTPStatus.UNAUTHORIZED, [("Content-Type", "application/json")], body

            parsed = urlsplit(path)
            parts = parsed.path.split("/")
            # expected format: /data/v2/stocks/{symbol}/bars
            symbol = parts[4].upper() if len(parts) >= 5 else ""
            bars = self.historical_bars.get(symbol, [])
            payload = {"bars": bars, "symbol": symbol, "next_page_token": None}
            return http.HTTPStatus.OK, [("Content-Type", "application/json")], json.dumps(payload).encode("utf-8")

        # Default 404
        return http.HTTPStatus.NOT_FOUND, [("Content-Type", "application/json")], b'{"error":"not found"}'

    async def _handle_ws(self, ws: Any, path: str) -> None:
        """Handles a connected WebSocket client session."""
        self._client_counter += 1
        client_id = f"client_{self._client_counter}"
        client = MockRelayClient(ws, client_id, path)
        self._clients[client_id] = client

        logger.info(f"Client {client_id} connected on {path}")

        try:
            # 1. Send initial handshake banner
            await client.send_json([{"T": "success", "msg": "connected"}])

            # 2. Wait for auth message with timeout
            try:
                raw_auth = await asyncio.wait_for(ws.recv(), timeout=self.auth_timeout)
                auth_data = json.loads(raw_auth)
                action = auth_data.get("action")
                token = auth_data.get("token") or auth_data.get("key")

                if action != "auth" or token != self.relay_token:
                    await client.send_json([{"T": "error", "code": 402, "msg": "auth failed"}])
                    await ws.close(1008, "auth failed")
                    return

                client.authenticated = True
                await client.send_json([{"T": "success", "msg": "authenticated"}])
            except asyncio.TimeoutError:
                logger.warning(f"Client {client_id} auth timed out")
                await client.send_json([{"T": "error", "code": 402, "msg": "auth timeout"}])
                await ws.close(1008, "auth timeout")
                return
            except Exception as e:
                logger.warning(f"Client {client_id} auth error: {e}")
                await ws.close(1008, "invalid auth payload")
                return

            # 3. Message loop for subscriptions and commands
            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    action = msg.get("action")

                    if action == "subscribe":
                        ack: Dict[str, Any] = {"T": "subscription"}
                        for ch in ("bars", "quotes", "trades", "news"):
                            if ch in msg:
                                symbols = [s.upper() for s in msg[ch]]
                                client.subscriptions[ch].update(symbols)
                                ack[ch] = sorted(list(client.subscriptions[ch]))
                        await client.send_json([ack])

                    elif action == "unsubscribe":
                        ack = {"T": "subscription"}
                        for ch in ("bars", "quotes", "trades", "news"):
                            if ch in msg:
                                symbols = [s.upper() for s in msg[ch]]
                                client.subscriptions[ch].difference_update(symbols)
                                ack[ch] = sorted(list(client.subscriptions[ch]))
                        await client.send_json([ack])

                    elif action == "ping":
                        await client.send_json([{"T": "pong"}])

                except json.JSONDecodeError:
                    logger.warning(f"Malformed JSON from {client_id}")

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client_id} disconnected")
        except Exception as exc:
            logger.error(f"Error handling client {client_id}: {exc}")
        finally:
            self._clients.pop(client_id, None)

    async def broadcast_bar(self, bar: Dict[str, Any]) -> None:
        """Broadcast 1-min bar to matching subscribers."""
        sym = bar.get("S", "").upper()
        payload = [bar]
        for client in list(self._clients.values()):
            if client.subscribes_to("bars", sym):
                await client.send_json(payload)

    async def broadcast_quote(self, quote: Dict[str, Any]) -> None:
        """Broadcast NBBO quote to matching subscribers."""
        sym = quote.get("S", "").upper()
        payload = [quote]
        for client in list(self._clients.values()):
            if client.subscribes_to("quotes", sym):
                await client.send_json(payload)

    async def broadcast_trade(self, trade: Dict[str, Any]) -> None:
        """Broadcast executed trade to matching subscribers."""
        sym = trade.get("S", "").upper()
        payload = [trade]
        for client in list(self._clients.values()):
            if client.subscribes_to("trades", sym):
                await client.send_json(payload)

    async def broadcast_news(self, news: Dict[str, Any]) -> None:
        """Broadcast news article to news subscribers."""
        symbols = [s.upper() for s in news.get("symbols", [])]
        payload = [news]
        for client in list(self._clients.values()):
            if client.subscribes_to("news", "*"):
                await client.send_json(payload)
            elif any(client.subscribes_to("news", sym) for sym in symbols):
                await client.send_json(payload)

    async def broadcast_relay_status(self, msg: str) -> None:
        """Broadcast synthetic upstream status message."""
        payload = [{"T": "relay", "msg": msg}]
        for client in list(self._clients.values()):
            if client.authenticated:
                await client.send_json(payload)

    def load_replay_queue(self, events: List[Dict[str, Any]]) -> None:
        """Queue a list of chronological market events for step or accelerated replay."""
        self.replay_queue = list(events)
        self.replay_index = 0

    async def step_next(self) -> Optional[Dict[str, Any]]:
        """Advance exactly one event from the replay queue."""
        if self.replay_index >= len(self.replay_queue):
            return None
        event = self.replay_queue[self.replay_index]
        self.replay_index += 1
        await self._dispatch_replay_event(event)
        return event

    async def step_all(self) -> int:
        """Advance all remaining events in the replay queue."""
        count = 0
        while self.replay_index < len(self.replay_queue):
            await self.step_next()
            count += 1
        return count

    async def _dispatch_replay_event(self, event: Dict[str, Any]) -> None:
        """Dispatch a single event by type."""
        ev_type = event.get("type")
        data = event.get("data", event)
        if event.get("timestamp") and "t" not in data:
            data = dict(data)
            data["t"] = event["timestamp"]
        if ev_type == "news" and event.get("timestamp") and "created_at" not in data:
            data = dict(data)
            data["created_at"] = event["timestamp"]

        if ev_type == "bar" or data.get("T") == "b":
            await self.broadcast_bar(data)
        elif ev_type == "quote" or data.get("T") == "q":
            await self.broadcast_quote(data)
        elif ev_type == "trade" or data.get("T") == "t":
            await self.broadcast_trade(data)
        elif ev_type == "news" or data.get("T") == "n":
            await self.broadcast_news(data)
        elif ev_type == "vix":
            v_val = data.get("value", 18.0)
            self.set_vix(float(v_val))
        elif ev_type == "relay":
            await self.broadcast_relay_status(data.get("msg", "upstream_connected"))

    async def start(self) -> None:
        """Start the mock server."""
        if self._is_running:
            return

        self._server = await legacy_ws.serve(
            self._handle_ws,
            self.host,
            self.port,
            process_request=self._handle_http,
        )
        self._is_running = True
        logger.info(f"Mock AlpacaRelay listening on http://{self.host}:{self.port} and ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Gracefully stop server, disconnect clients, and release port."""
        if not self._is_running:
            return

        self._is_running = False

        # Close all active client connections
        for client in list(self._clients.values()):
            try:
                await client.ws.close(1001, "server shutting down")
            except Exception:
                pass
        self._clients.clear()

        # Stop server socket
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info(f"Mock AlpacaRelay on port {self.port} cleanly stopped and released.")

    async def __aenter__(self) -> "MockAlpacaRelayServer":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()


async def run_standalone_server(port: int, host: str = "127.0.0.1") -> None:
    """Run standalone server with POSIX signal traps for clean process exit."""
    server = MockAlpacaRelayServer(host=host, port=port)
    await server.start()

    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    print(f"Mock AlpacaRelay running on ws://{host}:{port} and http://{host}:{port}")
    print("Press Ctrl+C to stop.")

    try:
        await stop_event.wait()
    finally:
        print("\nShutting down server...")
        await server.stop()
        print("Shutdown complete. Port freed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="AlpacaRelay Deterministic Mock Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default: 8080)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    args = parser.parse_args()

    try:
        asyncio.run(run_standalone_server(port=args.port, host=args.host))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
