"""Feed liveness telemetry on /health.

A relay status of "connected" is written once at handshake, so a feed that connects
and then goes silent reports exactly like a healthy one. These tests pin the per-feed
event ages and counts that make that difference visible from outside the process.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app import main
from backend.app.models.events import BarEvent, QuoteEvent, TradeEvent, VixPrint, VixRegime

TS = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_runtime():
    main.reset_runtime_state()
    yield
    main.reset_runtime_state()


@pytest.mark.asyncio
async def test_health_reports_null_feed_ages_before_any_event():
    health = await main.get_health()
    feeds = health["feeds"]
    for key in ("bars", "quotes", "trades", "news"):
        assert feeds[key]["last_age_sec"] is None, f"{key} age should be unknown before any event"
    assert feeds["vix"]["last_poll_age_sec"] is None
    assert feeds["vix"]["value_age_sec"] is None


@pytest.mark.asyncio
async def test_bar_event_makes_bar_feed_age_observable():
    await main.handle_bar_event(BarEvent(
        symbol="SPY", timestamp=TS,
        open=500.0, high=501.0, low=499.5, close=500.5, volume=10_000,
    ))
    feeds = (await main.get_health())["feeds"]
    assert feeds["bars"]["last_age_sec"] is not None
    assert feeds["bars"]["last_age_sec"] < 30.0
    # A silent quote feed must not borrow the bar feed's liveness.
    assert feeds["quotes"]["last_age_sec"] is None


@pytest.mark.asyncio
async def test_each_feed_tracks_its_own_liveness():
    await main.handle_quote_event(QuoteEvent(
        symbol="SPY", timestamp=TS,
        bid_price=500.0, bid_size=100, bid_exchange="P",
        ask_price=500.1, ask_size=100, ask_exchange="P",
    ))
    await main.handle_trade_event(TradeEvent(
        symbol="SPY", trade_id=1, price=500.05, size=100, exchange="P", timestamp=TS,
    ))
    feeds = (await main.get_health())["feeds"]
    assert feeds["quotes"]["last_age_sec"] is not None
    assert feeds["trades"]["last_age_sec"] is not None
    assert feeds["bars"]["last_age_sec"] is None
    assert feeds["news"]["last_age_sec"] is None


@pytest.mark.asyncio
async def test_vix_feed_age_is_recorded_even_for_a_stale_print():
    # A stale print is skipped for regime purposes but still proves the poller is alive.
    await main.handle_vix_print(VixPrint(
        value=20.0, asof=TS, received_at=TS, age_s=600.0, state="stale",
        upstream="connected", regime=VixRegime.NORMAL, sizing_multiplier=1.0,
        is_stale=True,
    ))
    feeds = (await main.get_health())["feeds"]
    assert feeds["vix"]["last_poll_age_sec"] is not None


@pytest.mark.asyncio
async def test_reset_runtime_state_clears_feed_ages():
    await main.handle_trade_event(TradeEvent(
        symbol="SPY", trade_id=1, price=500.05, size=100, exchange="P", timestamp=TS,
    ))
    assert (await main.get_health())["feeds"]["trades"]["last_age_sec"] is not None
    main.reset_runtime_state()
    assert (await main.get_health())["feeds"]["trades"]["last_age_sec"] is None


@pytest.mark.asyncio
async def test_health_publishes_the_live_risk_limits():
    """The deployed build's limits must be readable without placing an order.

    These come off the wired risk engine, so a config change that failed to reach
    production shows up here instead of only in the repo.
    """
    limits = (await main.get_health())["limits"]
    assert limits["max_position_notional"] == 25000.0
    assert limits["max_position_equity_pct"] == 0.500
    assert limits["max_daily_loss_dollars"] == 1500.0
    assert limits["max_concurrent_positions"] == 3
    assert limits["stop_distance_pct"] == [0.004, 0.040]


@pytest.mark.asyncio
async def test_vix_health_reports_the_values_age_not_just_the_polls():
    """A dead upstream must not read as fresh.

    Observed live 2026-09-21: /health showed the vix feed ~4s old while the underlying
    VIX value was 380s stale, because a stale print still marks a feed event. The poll
    age and the value age must be reported separately.
    """
    from backend.app.models.events import VixRegime

    stale_asof = datetime.now(timezone.utc) - timedelta(seconds=380)
    await main.handle_vix_print(VixPrint(
        value=14.90, asof=stale_asof, received_at=datetime.now(timezone.utc),
        age_s=380.0, state="stale", upstream="down", regime=VixRegime.LOW,
        sizing_multiplier=1.20, is_stale=True,
    ))
    vix = (await main.get_health())["feeds"]["vix"]

    assert vix["last_poll_age_sec"] < 30.0, "the poller did just run"
    assert vix["value_age_sec"] > 300.0, "but the value is stale and must say so"
    assert vix["stale"] is True


@pytest.mark.asyncio
async def test_vix_health_reports_a_fresh_print_as_fresh():
    from backend.app.models.events import VixRegime

    now = datetime.now(timezone.utc)
    await main.handle_vix_print(VixPrint(
        value=14.90, asof=now, received_at=now, age_s=2.0, state="ready",
        upstream="connected", regime=VixRegime.LOW, sizing_multiplier=1.20,
    ))
    vix = (await main.get_health())["feeds"]["vix"]
    assert vix["value_age_sec"] < 30.0
    assert vix["stale"] is False
