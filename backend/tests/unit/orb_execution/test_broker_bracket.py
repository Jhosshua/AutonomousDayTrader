"""The additive AlpacaBroker bracket methods, against the in-memory Alpaca."""
import httpx
import pytest

from backend.app.core.broker import (
    AlpacaBroker, BrokerHTTPError, BrokerTransportError, PAPER_BASE_URL,
)
from backend.tests.unit.orb_execution.fakes import FakeAlpaca, FakeClock, at


@pytest.fixture
def x():
    fake = FakeAlpaca(FakeClock(at(9, 40)))
    b = AlpacaBroker("k", "s", transport=httpx.MockTransport(fake.handler), poll_interval_sec=0.0)
    b._sleep = lambda s: None
    return fake, b


def test_paper_only_guard_unchanged():
    with pytest.raises(ValueError):
        AlpacaBroker("k", "s", base_url="https://api.alpaca.markets")
    assert PAPER_BASE_URL == "https://paper-api.alpaca.markets"


def test_submit_bracket_sends_orbstraddle_payload(x):
    fake, b = x
    fake.entry_mode = "new"
    parent = b.submit_bracket("app", 10, "sell", 302.8249, 326.641, "adt-orb-APP-2026-09-28-w1-a1-abcdef")
    post = [r for r in fake.requests if r[0] == "POST"][0][2]
    assert post == {"symbol": "APP", "qty": "10", "side": "sell", "type": "market", "time_in_force": "day",
                    "order_class": "bracket", "client_order_id": "adt-orb-APP-2026-09-28-w1-a1-abcdef",
                    "take_profit": {"limit_price": "302.82"}, "stop_loss": {"stop_price": "326.64"}}
    legs = parent["legs"]
    assert {l["type"] for l in legs} == {"limit", "stop"}
    # bracket children carry broker UUID client ids, never our prefix
    assert all(not l["client_order_id"].startswith("adt-orb") for l in legs)


def test_submit_bracket_refuses_levels_on_the_wrong_side(x):
    _fake, b = x
    with pytest.raises(ValueError):
        b.submit_bracket("APP", 10, "buy", 99.0, 101.0, "c1")


def test_submit_bracket_errors_are_typed(x):
    fake, b = x
    fake.fail.append({"method": "POST", "path": "/v2/orders", "kind": "transport"})
    with pytest.raises(BrokerTransportError):
        b.submit_bracket("APP", 1, "buy", 110, 90, "c1")
    fake.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 422})
    with pytest.raises(BrokerHTTPError) as e:
        b.submit_bracket("APP", 1, "buy", 110, 90, "c2")
    assert e.value.status_code == 422 and e.value.definitive
    fake.fail.append({"method": "POST", "path": "/v2/orders", "kind": "status", "status": 429})
    with pytest.raises(BrokerHTTPError) as e:
        b.submit_bracket("APP", 1, "buy", 110, 90, "c3")
    assert not e.value.definitive


def test_lookup_by_client_id_404_is_none_and_errors_raise(x):
    fake, b = x
    assert b.get_order_by_client_id("nope") is None
    fake.fail.append({"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status", "status": 500})
    with pytest.raises(BrokerHTTPError):
        b.get_order_by_client_id("nope")


def test_patch_held_leg_is_422_and_live_leg_is_replaced(x):
    fake, b = x
    fake.entry_mode = "new"
    parent = b.submit_bracket("APP", 10, "buy", 110, 90, "c1")
    sl = next(l for l in parent["legs"] if l["type"] == "stop")
    with pytest.raises(BrokerHTTPError) as e:
        b.patch_order(sl["id"], stop_price=100)
    assert e.value.status_code == 422
    fake.fill(parent["id"])
    new = b.patch_order(sl["id"], stop_price=100.004)
    assert new["id"] != sl["id"] and new["stop_price"] == "100.00"
    assert b.get_order(sl["id"])["status"] == "replaced"


def test_cancel_and_confirm_waits_for_pending_and_reports_a_racing_fill(x):
    fake, b = x
    fake.entry_mode = "new"
    parent = b.submit_bracket("APP", 10, "buy", 110, 90, "c1")
    fake.cancel_pending[parent["id"]] = 3
    out = b.cancel_order_and_confirm(parent["id"], timeout=1.0)
    assert out["status"] == "canceled"
    p2 = b.submit_bracket("APP", 10, "buy", 110, 90, "c2")
    fake.fill_on_cancel[p2["id"]] = (10, 100.5)
    out = b.cancel_order_and_confirm(p2["id"], timeout=1.0)
    assert out["status"] == "filled" and out["filled_qty"] == "10"


def test_cancel_not_confirmed_returns_the_live_order(x):
    fake, b = x
    fake.entry_mode = "new"
    parent = b.submit_bracket("APP", 10, "buy", 110, 90, "c1")
    fake.cancel_pending[parent["id"]] = 10 ** 6
    out = b.cancel_order_and_confirm(parent["id"], timeout=0.2)
    assert out["status"] == "pending_cancel"


def test_list_open_orders_nests_legs_and_filters_symbol(x):
    fake, b = x
    fake.entry_mode = "new"
    b.submit_bracket("APP", 10, "buy", 110, 90, "c1")
    b.submit_bracket("PLTR", 5, "buy", 110, 90, "c2")
    rows = b.list_open_orders("APP")
    assert [r["symbol"] for r in rows] == ["APP"] and len(rows[0]["legs"]) == 2


def test_existing_get_order_default_is_still_nested(x):
    fake, b = x
    fake.entry_mode = "new"
    p = b.submit_bracket("APP", 10, "buy", 110, 90, "c1")
    b.get_order(p["id"])
    assert fake.requests[-1][3]["nested"] == "true"
