# @steered SNARE-2 2026-09-30
import os

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """No keys, no relay token, and any httpx request not routed through a MockTransport fails
    loudly: these tests never reach Alpaca, Railway or the relay."""
    for k in list(os.environ):
        if k.startswith(("ALPACA_", "APCA_")) or k in ("RELAY_TOKEN",):
            monkeypatch.delenv(k, raising=False)

    def refuse(self, request):  # pragma: no cover - only runs if a test leaks a real request
        raise AssertionError(f"real network request attempted: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    yield


@pytest.fixture
def main_runtime():
    """The real backend.app.main module, reset before and after the test."""
    from backend.app import main as r
    r.reset_runtime_state()
    r.set_simulation_mode(False)
    old = {"state_store": r.state_store, "alpaca_broker": r.alpaca_broker, "gate": r.engine.broker_gate}
    old_mode = dict(r.broker_state)
    # the fake clock moves the adaptation engine's time of day phase; put it back afterwards so
    # later tests that use the wall clock see what a fresh process would
    adaptation = {k: getattr(r.adaptation_engine, k) for k in (
        "current_vix", "current_vix_regime", "current_sizing_multiplier", "current_stop_multiplier",
        "current_time_phase", "last_update")}
    yield r
    for k, v in adaptation.items():
        setattr(r.adaptation_engine, k, v)
    r.reset_runtime_state()
    r.engine.broker = None
    r.alpaca_broker = old["alpaca_broker"]
    r.engine.broker_gate = old["gate"]
    r.state_store = old["state_store"]
    r.broker_state.clear()
    r.broker_state.update(old_mode)
    r.persistence_healthy = r.state_store is not None or not r.settings.PERSISTENCE_REQUIRED
    r.persistence_error = None
    r.flattening_engine.clock.clear_simulated_time()
