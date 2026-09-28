import os

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_real_broker(monkeypatch):
    """These tests must never reach Alpaca or the relay: no keys, no alpaca broker mode, and any
    httpx request that is not routed through a MockTransport fails loudly."""
    for k in list(os.environ):
        if k.startswith(("ALPACA_", "APCA_")) or k in ("RELAY_TOKEN",):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BROKER_MODE", "local")

    real_send = httpx.HTTPTransport.handle_request

    def refuse(self, request):  # pragma: no cover - only runs if a test leaks a real request
        raise AssertionError(f"real network request attempted: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    yield
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", real_send)
