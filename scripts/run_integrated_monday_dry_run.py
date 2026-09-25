#!/usr/bin/env python3
"""Replay the Monday fixture through the production ingestion and execution path.

This is an integration replay, not a second trading engine. Events are sent to
the mock AlpacaRelay and must pass through the real websocket/REST clients,
EventBus, ``backend.app.main`` handlers, execution engine, bracket manager, and
UI state serializer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
import sys
import time
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import main as runtime
from backend.app.config import settings
from backend.app.core.event_bus import event_bus
from backend.app.replay.feed_player import FeedPlayer
from backend.app.replay.mock_relay import DEFAULT_RELAY_TOKEN, MockAlpacaRelayServer


class CaptureSocket:
    """Small WebSocket-compatible capture used to verify UI payloads."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


def protected_book(runtime: Any) -> bool:
    """A 10:30 replay may end with a runner; every share must have a live stop."""
    linked_working = set()
    for symbol, position in runtime.account.positions.items():
        bracket_id = runtime.bracket_manager.symbol_to_bracket.get(symbol)
        bracket = runtime.bracket_manager.brackets.get(bracket_id)
        if bracket is None or bracket.remaining_qty != position.shares:
            return False
        stop = runtime.engine.working_orders.get(bracket.stop_order_id)
        if stop is None or stop.remaining_qty != position.shares:
            return False
        for order_id in (bracket.stop_order_id, bracket.target_1_order_id, bracket.target_2_order_id):
            if order_id in runtime.engine.working_orders:
                linked_working.add(order_id)
    return set(runtime.engine.working_orders).issubset(linked_working)


async def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("Timed out waiting for production event processing")


async def run(port: int, report_path: Path) -> dict[str, Any]:
    fixture_path = PROJECT_ROOT / "tests" / "e2e" / "fixtures" / "monday_open_session.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    relay = MockAlpacaRelayServer(port=port, relay_token=DEFAULT_RELAY_TOKEN)
    ui_capture = CaptureSocket()

    saved_settings = {
        "RELAY_URL": settings.RELAY_URL,
        "RELAY_HTTP_URL": settings.RELAY_HTTP_URL,
        "RELAY_TOKEN": settings.RELAY_TOKEN,
        "VIX_POLL_INTERVAL_SEC": settings.VIX_POLL_INTERVAL_SEC,
        "START_RELAY_CLIENTS": settings.START_RELAY_CLIENTS,
        "ENV": settings.ENV,
    }
    start = time.monotonic()
    settings.RELAY_URL = f"ws://127.0.0.1:{port}"
    settings.RELAY_HTTP_URL = f"http://127.0.0.1:{port}"
    settings.RELAY_TOKEN = DEFAULT_RELAY_TOKEN
    settings.VIX_POLL_INTERVAL_SEC = 0.05
    settings.START_RELAY_CLIENTS = True
    settings.ENV = "simulation"

    runtime.reset_runtime_state()
    runtime.set_simulation_mode(True)
    event_bus.clear()
    runtime.ui_clients.add(ui_capture)  # type: ignore[arg-type]

    try:
        await relay.start()
        async with runtime.lifespan(runtime.app):
            await wait_until(
                lambda: all(runtime.relay_statuses.get(k) == "connected" for k in ("stock", "news", "vix")),
                timeout=5.0,
            )

            player = FeedPlayer(relay, speed=100.0)
            player.load_events(fixture)
            processed = 0
            while True:
                event = await player.step_next()
                if event is None:
                    break
                processed += 1
                event_type = event.get("type")
                data = event.get("data", event)
                if event_type == "bar":
                    symbol = str(data.get("S", "")).upper()
                    timestamp = event.get("timestamp") or data.get("t")
                    normalized_timestamp = datetime.fromisoformat(
                        str(timestamp).replace("Z", "+00:00")
                    ).isoformat()
                    await wait_until(
                        lambda: bool(runtime.market_history.get(symbol))
                        and runtime.market_history[symbol][-1]["time"] == normalized_timestamp,
                        timeout=2.0,
                    )
                elif event_type == "news":
                    headline = data.get("headline", "")
                    await wait_until(
                        lambda: any(item.get("headline") == headline for item in runtime.recent_news),
                        timeout=2.0,
                    )
                elif event_type in {"quote", "trade"}:
                    await asyncio.sleep(0.03)
                elif event_type == "vix":
                    expected_vix = float(data.get("value", 0.0))
                    await wait_until(
                        lambda: runtime.last_vix_print is not None
                        and abs(runtime.last_vix_print.value - expected_vix) < 0.01,
                        timeout=2.0,
                    )
                    await asyncio.sleep(0.02)
                else:
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.15)

        event_errors = event_bus.metrics["error_count"]
        account = runtime.account.get_snapshot()
        all_open_positions_protected = protected_book(runtime)
        report = {
            "status": "PASS" if (
                processed == len(fixture)
                and event_errors == 0
                and len(runtime.engine.orders) > 0
                and any(order.status.value == "FILLED" for order in runtime.engine.orders.values())
                and not any(order.status.value == "REJECTED" for order in runtime.engine.orders.values())
                and all_open_positions_protected
                and all(runtime.relay_statuses.get(k) == "connected" for k in ("stock", "news", "vix"))
            ) else "FAIL",
            "simulation_only": True,
            "fixture": str(fixture_path.relative_to(PROJECT_ROOT)),
            "events_processed": processed,
            "event_bus_errors": event_errors,
            "duration_seconds": round(time.monotonic() - start, 3),
            "account": {
                "equity": round(account.equity, 2),
                "cash": round(account.cash, 2),
                "realized_pnl": round(account.realized_pnl, 2),
                "unrealized_pnl": round(account.unrealized_pnl, 2),
                "fees_paid": round(account.fees_paid, 2),
                "open_positions": len(runtime.account.positions),
                "working_orders": len(runtime.engine.working_orders),
                "status": getattr(account.status, "value", account.status),
            },
            "protection": {
                "all_open_positions_protected": all_open_positions_protected,
                "checked_at_fixture_end": "10:30 ET (before the scheduled EOD flatten)",
            },
            "orders": {
                "created": len(runtime.engine.orders),
                "filled": sum(1 for order in runtime.engine.orders.values() if order.status.value == "FILLED"),
                "rejected": sum(1 for order in runtime.engine.orders.values() if order.status.value == "REJECTED"),
                "details": [
                    {
                        "symbol": order.symbol,
                        "side": order.side.value,
                        "qty": order.qty,
                        "strategy_id": order.strategy_id,
                        "estimated_price": order.estimated_price,
                        "stop_price": order.stop_price,
                        "status": order.status.value,
                        "reject_reason": order.reject_reason,
                    }
                    for order in runtime.engine.orders.values()
                ],
            },
            "relay_statuses": dict(runtime.relay_statuses),
            "vix": runtime.last_vix_print.value if runtime.last_vix_print else None,
            "strategies": [strategy.to_dict() for strategy in runtime.strategies],
            "ui": {
                "state_updates": sum(1 for message in ui_capture.messages if message.get("type") == "STATE_UPDATE"),
                "last_has_all_positions": bool(ui_capture.messages and "all_positions" in ui_capture.messages[-1]),
                "last_equity": ui_capture.messages[-1].get("account", {}).get("equity") if ui_capture.messages else None,
                "last_positions_count": ui_capture.messages[-1].get("positions_count") if ui_capture.messages else None,
            },
        }
    finally:
        runtime.ui_clients.discard(ui_capture)  # type: ignore[arg-type]
        await relay.stop()
        runtime.reset_runtime_state()
        event_bus.clear()
        for name, value in saved_settings.items():
            setattr(settings, name, value)

    report_path.write_text(
        "# Monday Market Open Integrated Dry Run\n\n"
        "This report is a deterministic replay through the production ingestion, "
        "execution, bracket, and UI serialization pathways. It is not a live market "
        "scan and does not certify real-account fills.\n\n"
        "```json\n" + json.dumps(report, indent=2) + "\n```\n",
        encoding="utf-8",
    )
    if report["status"] != "PASS":
        raise AssertionError(f"Integrated Monday dry run failed: {json.dumps(report, indent=2)}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="AutonomousDayTrader integrated Monday replay")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "MONDAY_SIMULATION_REPORT.md")
    args = parser.parse_args()
    report = asyncio.run(run(args.port, args.report))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
