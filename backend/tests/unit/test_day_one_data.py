# @steered SNARE-2 2026-09-30
"""Bounded relay reader tests for day one market data."""
from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta, timezone

import httpx
import pytest

from backend.app.core import day_one_schedule as d1
from backend.app.core.day_one_data import BODY_LIMIT, DayOneData, PRODUCTION_ORIGIN


class Broker:
    def get_calendar(self, start, end):
        a, b = date.fromisoformat(start), date.fromisoformat(end)
        rows = []
        while a <= b:
            if a.weekday() < 5:
                rows.append({"date": a.isoformat(), "close": "16:00"})
            a += timedelta(days=1)
        return rows


def response(data, *, content_type="application/json", status=200):
    return httpx.Response(status, content=json.dumps(data).encode(), headers={"content-type": content_type})


def make(handler):
    return DayOneData(PRODUCTION_ORIGIN, "test-only", Broker(), transport=httpx.MockTransport(handler))


def test_origin_is_pinned_and_redirects_are_refused():
    with pytest.raises(ValueError):
        DayOneData("https://example.com", "x", Broker(), transport=httpx.MockTransport(lambda _r: response({})))
    with pytest.raises(ValueError):
        DayOneData(PRODUCTION_ORIGIN + "/path", "x", Broker(), transport=httpx.MockTransport(lambda _r: response({})))
    reader = make(lambda _r: httpx.Response(302, headers={"location": "https://example.com"}))
    reader._begin_budget()
    with pytest.raises(ValueError):
        reader._json("/data/v2/stocks/bars", {})


def test_content_type_body_size_path_and_json_shape_are_bounded():
    reader = make(lambda _r: httpx.Response(200, content=b"x", headers={"content-type": "text/plain"}))
    reader._begin_budget()
    with pytest.raises(ValueError):
        reader._json("/data/v2/stocks/bars", {})
    reader = make(lambda _r: httpx.Response(200, content=b"x" * (BODY_LIMIT + 1),
                                            headers={"content-type": "application/json"}))
    reader._begin_budget()
    with pytest.raises(ValueError):
        reader._json("/data/v2/stocks/bars", {})
    reader = make(lambda _r: response([]))
    reader._begin_budget()
    with pytest.raises(ValueError):
        reader._json("/data/v2/stocks/bars", {})
    with pytest.raises(ValueError):
        reader._json("/caller/path", {})


def test_btc_window_uses_fixed_route_and_rightmost_close():
    seen = []

    def handler(request):
        seen.append(request)
        return response({"bars": {"BTC/USD": [
            {"t": "2026-10-01T13:33:00Z", "c": 100.0},
            {"t": "2026-10-01T13:34:00Z", "c": 101.0},
        ]}, "next_page_token": None})

    reader = make(handler)
    reader._begin_budget()
    point = reader.btc_point(date(2026, 10, 1), 574)
    assert point is not None and point.close == 101.0 and point.selected_at.minute == 34
    assert seen[0].url.path == "/data/v1beta3/crypto/us/bars"
    assert seen[0].url.params["symbols"] == "BTC/USD"


def test_spy_reference_is_prior_official_close():
    def handler(request):
        assert request.url.path == "/data/v2/stocks/bars"
        return response({"bars": {"SPY": [{"t": "2026-09-30T04:00:00Z", "o": 500,
                                                    "h": 502, "l": 499, "c": 501}]},
                         "next_page_token": None})

    reader = make(handler)
    assert reader.spy_reference(date(2026, 10, 1)) == 501.0


def test_coin_model_uses_last_twenty_moves_and_daily_rule_201_fields(monkeypatch):
    reader = make(lambda _r: response({}))
    sessions = sorted(d1.parse_calendar(Broker().get_calendar("2026-08-01", "2026-10-01")))
    closes = {s: 100.0 + i for i, s in enumerate(sessions)}

    def point(day, minute):
        value = closes.get(day, 100.0)
        if minute == d1.BTC_DECISION_TARGET_MINUTE:
            value *= 1.01
        return d1.BtcPoint(day, minute, datetime.combine(day, dtime(minute // 60, minute % 60), d1.ET), value)

    monkeypatch.setattr(reader, "btc_point", point)
    monkeypatch.setattr(reader, "_stock_daily", lambda _s, start, end: [
        {"t": datetime.combine(day, dtime(), timezone.utc).isoformat(), "o": 100, "h": 105,
         "l": 95, "c": 102} for day in sessions if start <= day <= end
    ])
    model = reader.coin_model(date(2026, 10, 1))
    assert model["history_count"] == 20
    assert model["sigma"] > 0
    assert model["prior_low"] == 95 and model["close_two_back"] == 102 and model["prior_close"] == 102
    assert model["anchor"]["target_minute"] == 959
    assert model["decision_point"]["target_minute"] == 574


def test_coin_model_walks_past_twenty_five_sessions_until_twenty_finite_moves(monkeypatch):
    reader = make(lambda _r: response({}))
    session = date(2026, 10, 1)
    sessions = sorted(d1.parse_calendar(Broker().get_calendar("2026-06-01", session.isoformat())))
    missing = set(sessions[-9:-1])
    decision_days = []

    def point(day, minute):
        with reader._lock:
            reader._calls += 1
        if minute == d1.BTC_DECISION_TARGET_MINUTE:
            decision_days.append(day)
            if day in missing:
                return None
        value = 100.0 + (day.toordinal() % 17)
        if minute == d1.BTC_DECISION_TARGET_MINUTE:
            value *= 1.0 + ((day.toordinal() % 9) + 1) / 10_000
        return d1.BtcPoint(day, minute, datetime.combine(day, dtime(minute // 60, minute % 60), d1.ET), value)

    monkeypatch.setattr(reader, "btc_point", point)
    monkeypatch.setattr(reader, "_stock_daily", lambda _s, start, end: [
        {"t": datetime.combine(day, dtime(), timezone.utc).isoformat(), "o": 100, "h": 105,
         "l": 95, "c": 102} for day in sessions if start <= day <= end
    ])
    model = reader.coin_model(session)
    assert model["history_count"] == 20
    assert any(day < sessions[-25] for day in decision_days)
    assert reader._calls <= 64
