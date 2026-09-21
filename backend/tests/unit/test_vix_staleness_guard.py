"""A VIX print the system cannot vouch for must not steer this session's risk.

Found while watching the live deployment pre-market on Monday 2026-09-21. The relay's
dxFeed VIX upstream cycles between connected and "KEEPALIVE timeout" every few minutes
(247 reconnects), so roughly a third of polls come back upstream=down / state=stale.
Two holes that exposes:

1. Staleness was evaluated only during regular hours, so off-hours ANY age was accepted.
   Friday's closing print on a Monday pre-market is legitimate, but a print from before
   the last close is not, and nothing distinguished them.
2. In-hours, a stale print only caused the regime update to be SKIPPED. Skipping leaves
   the last accepted regime in force, so a LOW reading (sizing 1.20) taken before the
   feed went dark keeps sizing 20% above base for the rest of the session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.app.ingestion.vix_client import VixClient
from backend.app.models.events import VixPrint, VixRegime
from backend.app.strategies.adaptation import DynamicAdaptationEngine

ET = ZoneInfo("America/New_York")


def _client_at(now_et: datetime) -> VixClient:
    return VixClient(relay_token="t", time_source=lambda: now_et)


def _payload(asof: datetime, age_s: float) -> dict:
    return {
        "value": 14.81, "asof": asof.isoformat(), "received_at": asof.isoformat(),
        "age_s": age_s, "state": "ready", "upstream": "connected",
    }


def test_print_from_before_the_last_close_is_stale_off_hours():
    # Monday 01:00 ET reading a print from the PREVIOUS Thursday: an entire session
    # (Friday) came and went without it updating, so it cannot be current.
    now = datetime(2026, 9, 21, 1, 0, tzinfo=ET)
    thursday = datetime(2026, 9, 17, 16, 15, tzinfo=ET).astimezone(timezone.utc)
    assert _client_at(now)._parse_vix_payload(_payload(thursday, 290_000.0)).is_stale is True


def test_last_closing_print_is_fresh_off_hours():
    # Monday 01:00 ET reading Friday's 16:15 close: the real pre-market case on
    # 2026-09-21, and correct. VIX does not print over a weekend, so age alone is
    # not evidence of a problem and must not trip the guard.
    now = datetime(2026, 9, 21, 1, 0, tzinfo=ET)
    friday_close = datetime(2026, 9, 18, 16, 15, tzinfo=ET).astimezone(timezone.utc)
    assert _client_at(now)._parse_vix_payload(_payload(friday_close, 204_520.0)).is_stale is False


def test_in_hours_staleness_still_uses_the_age_threshold():
    now = datetime(2026, 9, 21, 11, 0, tzinfo=ET)
    fresh = (now - timedelta(seconds=30)).astimezone(timezone.utc)
    stale = (now - timedelta(seconds=600)).astimezone(timezone.utc)
    client = _client_at(now)
    assert client._parse_vix_payload(_payload(fresh, 30.0)).is_stale is False
    assert client._parse_vix_payload(_payload(stale, 600.0)).is_stale is True


def test_last_session_close_skips_the_weekend():
    # Monday 01:00 ET: the most recent close is Friday's, not Sunday's or Saturday's.
    close = _client_at(datetime(2026, 9, 21, 1, 0, tzinfo=ET))._last_session_close()
    assert close.astimezone(ET).date() == datetime(2026, 9, 18).date()  # Friday
    assert close.astimezone(ET).hour == 16


def test_stale_guard_clamps_an_upsized_regime_to_neutral():
    eng = DynamicAdaptationEngine()
    eng.on_vix_print(VixPrint(
        value=12.0, asof=datetime.now(timezone.utc), received_at=datetime.now(timezone.utc),
        age_s=1.0, state="ready", upstream="connected", regime=VixRegime.LOW,
        sizing_multiplier=1.20,
    ))
    assert eng.current_sizing_multiplier == 1.20
    assert eng.apply_stale_vix_guard() is True
    assert eng.current_sizing_multiplier == 1.00
    assert eng.current_vix_regime == VixRegime.NORMAL.value


def test_stale_guard_never_loosens_a_defensive_regime():
    eng = DynamicAdaptationEngine()
    eng.on_vix_print(VixPrint(
        value=40.0, asof=datetime.now(timezone.utc), received_at=datetime.now(timezone.utc),
        age_s=1.0, state="ready", upstream="connected", regime=VixRegime.CRISIS,
        sizing_multiplier=0.35,
    ))
    before = eng.current_sizing_multiplier
    assert eng.apply_stale_vix_guard() is False
    assert eng.current_sizing_multiplier == before
    assert eng.current_vix_regime == VixRegime.CRISIS.value


@pytest.mark.asyncio
async def test_handle_vix_print_clamps_sizing_on_a_stale_print():
    from backend.app import main
    main.reset_runtime_state()
    main.adaptation_engine.on_vix_print(VixPrint(
        value=12.0, asof=datetime.now(timezone.utc), received_at=datetime.now(timezone.utc),
        age_s=1.0, state="ready", upstream="connected", regime=VixRegime.LOW,
        sizing_multiplier=1.20,
    ))
    assert main.adaptation_engine.current_sizing_multiplier == 1.20

    await main.handle_vix_print(VixPrint(
        value=14.81, asof=datetime(2026, 9, 17, 20, 15, tzinfo=timezone.utc),
        received_at=datetime.now(timezone.utc), age_s=290_000.0, state="stale",
        upstream="connected", regime=VixRegime.LOW, sizing_multiplier=1.20, is_stale=True,
    ))
    assert main.adaptation_engine.current_sizing_multiplier == 1.00
    ctx = await main.get_market_context()
    assert ctx["sizing_multiplier"] == 1.00
    main.reset_runtime_state()
