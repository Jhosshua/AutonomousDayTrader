# @steered SNARE-2 2026-09-30
"""Overnight broker methods against a fake Alpaca (httpx.MockTransport, no network)."""
import json

import httpx
import pytest

from backend.app.core.broker import (
    PAPER_BASE_URL, REFUSAL_AMBIGUOUS, REFUSAL_BUYING_POWER, REFUSAL_DEFINITE, REFUSAL_WASH_TRADE,
    AlpacaBroker, BrokerError, BrokerHTTPError, BrokerTransportError, classify_refusal,
)


class Recorder:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "paper-api.alpaca.markets"   # R6: paper host only
        out = self.reply(request)
        if isinstance(out, Exception):
            raise out
        return out


def make(reply):
    rec = Recorder(reply)
    return AlpacaBroker("key", "secret", transport=httpx.MockTransport(rec)), rec


def test_live_host_is_refused():
    with pytest.raises(ValueError):
        AlpacaBroker("k", "s", base_url="https://api.alpaca.markets")
    assert PAPER_BASE_URL == "https://paper-api.alpaca.markets"


@pytest.mark.parametrize("tif,side", [("cls", "buy"), ("opg", "sell")])
def test_submit_on_auction_sends_one_market_order_with_the_auction_tif(tif, side):
    b, rec = make(lambda r: httpx.Response(200, json={"id": "o1", "status": "accepted", **json.loads(r.content)}))
    row = b.submit_on_auction("nvda", side.upper(), 12, "adt-ovn-NVDA-20261001-buy-1", tif)
    assert row["id"] == "o1"
    assert len(rec.requests) == 1
    body = json.loads(rec.requests[0].content)
    assert body == {"symbol": "NVDA", "qty": "12", "side": side, "type": "market", "time_in_force": tif,
                    "client_order_id": "adt-ovn-NVDA-20261001-buy-1"}
    assert b.status.orders_sent == 1


@pytest.mark.parametrize("tif", ["day", "gtc", "ioc", "CLS", ""])
def test_submit_on_auction_refuses_any_other_tif_without_sending(tif):
    b, rec = make(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        b.submit_on_auction("NVDA", "buy", 1, "c", tif)
    with pytest.raises(ValueError):
        b.submit_on_auction("NVDA", "buy", 0, "c", "cls")
    with pytest.raises(ValueError):
        b.submit_on_auction("NVDA", "short", 1, "c", "cls")
    assert rec.requests == []


def test_lost_reply_is_a_transport_error_and_is_never_resent():
    b, rec = make(lambda r: httpx.ConnectError("reset"))
    with pytest.raises(BrokerTransportError) as err:
        b.submit_on_auction("IREN", "buy", 5, "c1", "cls")
    assert classify_refusal(err.value) == REFUSAL_AMBIGUOUS
    assert len(rec.requests) == 1


@pytest.mark.parametrize("status,body,kind", [
    (403, {"code": 40310000, "message": "potential wash trade detected. use complex orders"}, REFUSAL_WASH_TRADE),
    (403, {"code": 40310000, "message": "insufficient buying power"}, REFUSAL_BUYING_POWER),
    (403, {"code": 40310000, "message": "insufficient qty available for order"}, REFUSAL_DEFINITE),
    (422, {"code": 42210000, "message": "time_in_force cls is not allowed at this time"}, REFUSAL_DEFINITE),
    (403, {"code": 40310000, "message": "opg orders are not supported for this account"}, REFUSAL_DEFINITE),
    (429, {"message": "rate limit exceeded"}, REFUSAL_AMBIGUOUS),
    (422, {"code": 40010001, "message": "client_order_id must be unique"}, REFUSAL_AMBIGUOUS),
    (500, {"message": "internal"}, REFUSAL_AMBIGUOUS),
    (503, "upstream", REFUSAL_AMBIGUOUS),
])
def test_refusals_are_classified(status, body, kind):
    reply = httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    b, _ = make(lambda r: reply)
    with pytest.raises(BrokerHTTPError) as err:
        b.submit_on_auction("HUT", "buy", 3, "c1", "cls")
    assert err.value.status_code == status
    assert classify_refusal(err.value) == kind
    assert err.value.definitive == (status < 500 and status != 429)


def test_plain_broker_errors_prove_nothing():
    assert classify_refusal(BrokerError("lookup failed")) == REFUSAL_AMBIGUOUS
    assert classify_refusal(RuntimeError("x")) == REFUSAL_AMBIGUOUS


def test_submit_market_order_is_the_plain_day_fallback():
    b, rec = make(lambda r: httpx.Response(200, json={"id": "m1", "status": "accepted"}))
    b.submit_market_order("HUT", "sell", 7, "adt-ovn-HUT-20261001-sell-2")
    body = json.loads(rec.requests[0].content)
    assert (body["type"], body["time_in_force"], body["side"], body["qty"]) == ("market", "day", "sell", "7")


def test_get_calendar_reads_the_range():
    rows = [{"date": "2026-11-25", "open": "09:30", "close": "16:00"},
            {"date": "2026-11-27", "open": "09:30", "close": "13:00"}]
    b, rec = make(lambda r: httpx.Response(200, json=rows))
    assert b.get_calendar("2026-11-25", "2026-11-27") == rows
    req = rec.requests[0]
    assert req.method == "GET" and req.url.path == "/v2/calendar"
    assert dict(req.url.params) == {"start": "2026-11-25", "end": "2026-11-27"}
    b2, _ = make(lambda r: httpx.Response(500, json={}))
    with pytest.raises(BrokerHTTPError):
        b2.get_calendar("2026-11-25", "2026-11-27")
    b3, _ = make(lambda r: httpx.Response(200, json={"not": "a list"}))
    with pytest.raises(BrokerError):
        b3.get_calendar("2026-11-25", "2026-11-27")


def test_get_account_fields_are_floats_and_missing_is_none():
    acct = {"account_number": "PA3CSVDZMMPY", "equity": "49700.5", "cash": "-120.25", "buying_power": "99401",
            "regt_buying_power": "99401", "daytrading_buying_power": "198802", "last_maintenance_margin": "0",
            "multiplier": "2"}
    b, _ = make(lambda r: httpx.Response(200, json=acct))
    out = b.get_account_fields()
    assert out == {"account_number": "PA3CSVDZMMPY", "equity": 49700.5, "cash": -120.25, "buying_power": 99401.0,
                   "regt_buying_power": 99401.0, "daytrading_buying_power": 198802.0,
                   "last_maintenance_margin": 0.0, "multiplier": 2.0}
    b2, _ = make(lambda r: httpx.Response(200, json={"account_number": "X", "equity": "1", "multiplier": "abc"}))
    out2 = b2.get_account_fields()
    assert out2["buying_power"] is None and out2["multiplier"] is None and out2["equity"] == 1.0
    b3, _ = make(lambda r: httpx.ReadTimeout("slow"))
    with pytest.raises(BrokerTransportError):
        b3.get_account_fields()


def test_corporate_actions_request_and_normalized_rows():
    rows = [
        {"ca_type": "split", "ca_sub_type": "stock_split", "initiating_symbol": "NVDA", "target_symbol": "NVDA",
         "old_rate": "1", "new_rate": "10", "ex_date": "2026-10-02"},
        {"ca_type": "split", "ca_sub_type": "reverse_split", "initiating_symbol": "IREN", "target_symbol": "IRNX",
         "old_rate": "10", "new_rate": "1", "ex_date": "2026-10-02"},
        {"ca_type": "split", "ca_sub_type": "stock_split", "initiating_symbol": "HUT", "target_symbol": "HUT",
         "old_rate": None, "new_rate": "4", "ex_date": "2026-10-02"},
        {"ca_type": "merger", "ca_sub_type": "merger_completion", "initiating_symbol": "ABC", "target_symbol": "HUT"},
    ]
    b, rec = make(lambda r: httpx.Response(200, json=rows))
    out = b.get_corporate_actions("nvda", "2026-10-01", "2026-10-02")
    req = rec.requests[0]
    assert req.url.path == "/v2/corporate_actions/announcements"
    assert dict(req.url.params) == {"ca_types": "split,merger,spinoff", "since": "2026-10-01",
                                    "until": "2026-10-02", "symbol": "NVDA", "date_type": "ex_date"}
    assert [(o["kind"], o["old_symbol"], o["new_symbol"], o["ratio"]) for o in out] == [
        ("split", "NVDA", "NVDA", 10.0), ("reverse_split", "IREN", "IRNX", 0.1),
        ("split", "HUT", "HUT", None), ("merger", "ABC", "HUT", None)]
    assert out[0]["raw"] == rows[0]


def test_corporate_actions_range_limit_and_errors():
    b, rec = make(lambda r: httpx.Response(200, json=[]))
    with pytest.raises(ValueError):
        b.get_corporate_actions("NVDA", "2026-01-01", "2026-06-01")
    with pytest.raises(ValueError):
        b.get_corporate_actions("NVDA", "2026-10-02", "2026-10-01")
    assert rec.requests == []
    b2, _ = make(lambda r: httpx.Response(403, json={"message": "forbidden"}))
    with pytest.raises(BrokerHTTPError):
        b2.get_corporate_actions("NVDA", "2026-10-01", "2026-10-02")


def test_asset_margin_reads_the_paper_host_and_turns_the_percent_into_a_fraction():
    """Alpaca sends margin_requirement_long as a percent and often as a string. 50 must become
    0.50, or the overnight room would count a 50% stock as needing 50 times its price."""
    asset = {"symbol": "IREN", "marginable": True, "margin_requirement_long": "100",
             "margin_requirement_short": "100", "maintenance_margin_requirement": 100}
    b, rec = make(lambda r: httpx.Response(200, json=asset))
    assert b.get_asset_margin("iren") == {"symbol": "IREN", "marginable": True, "margin_requirement_long": 1.0}
    req = rec.requests[0]
    assert req.method == "GET" and req.url.path == "/v2/assets/IREN"
    b2, _ = make(lambda r: httpx.Response(200, json={"marginable": "true", "margin_requirement_long": 50}))
    assert b2.get_asset_margin("NVDA")["margin_requirement_long"] == 0.5
    b3, _ = make(lambda r: httpx.Response(200, json={"marginable": False, "margin_requirement_long": None}))
    assert b3.get_asset_margin("HUT") == {"symbol": "HUT", "marginable": False, "margin_requirement_long": None}


@pytest.mark.parametrize("reply,error", [
    (httpx.ReadTimeout("slow"), BrokerTransportError),
    (httpx.Response(404, json={"message": "asset not found"}), BrokerHTTPError),
    (httpx.Response(200, json=["not", "an", "object"]), BrokerError),
    (httpx.Response(200, json={"margin_requirement_long": "50"}), BrokerError),                        # no flag
    (httpx.Response(200, json={"marginable": True}), BrokerError),                                     # no requirement
    (httpx.Response(200, json={"marginable": True, "margin_requirement_long": "abc"}), BrokerError),
    (httpx.Response(200, json={"marginable": True, "margin_requirement_long": "150"}), BrokerError),
])
def test_asset_margin_raises_on_anything_it_cannot_trust(reply, error):
    b, _ = make(lambda r: reply)
    with pytest.raises(error):
        b.get_asset_margin("NVDA")
