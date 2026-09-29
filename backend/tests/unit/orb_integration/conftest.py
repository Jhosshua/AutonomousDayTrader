import os

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_real_broker(monkeypatch):
    """No keys, no alpaca_paper mode, no relay token, and any httpx request that is not routed
    through a MockTransport fails loudly: these tests never reach Alpaca or the relay."""
    for k in list(os.environ):
        if k.startswith(("ALPACA_", "APCA_")) or k in ("RELAY_TOKEN",):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BROKER_MODE", "local")

    def refuse(self, request):  # pragma: no cover - only runs if a test leaks a real request
        raise AssertionError(f"real network request attempted: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    yield


@pytest.fixture
def main_runtime():
    """The real backend.app.main module, reset before and after the test (ORB torn down)."""
    from backend.app import main as r
    r.reset_runtime_state()
    r.set_simulation_mode(False)
    old_store = r.state_store
    old_mode = dict(r.broker_state)
    yield r
    r.reset_runtime_state()
    r.state_store = old_store
    r.broker_state.clear()
    r.broker_state.update(old_mode)
    r.persistence_healthy = r.state_store is not None or not r.settings.PERSISTENCE_REQUIRED
    r.persistence_error = None
    r.flattening_engine.clock.clear_simulated_time()
