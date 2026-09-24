"""Close-time repair: backfill runs before finalize; short coverage withholds new swing entries."""
import asyncio

import pytest
from datetime import date, datetime, timedelta, timezone

from backend.app import main as rt
from backend.app.config import settings
from backend.app.models.events import BarEvent
from backend.app.strategies.swing_indicators import DailyBarAggregator, DailyBarStore

OPEN_UTC = datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_bar_store(monkeypatch):
    """Isolate the module-level daily bar store so these tests never leak bars."""
    store = DailyBarStore(seed_path=settings.DAILY_BARS_SEED_PATH)
    monkeypatch.setattr(rt, "daily_bar_store", store)
    monkeypatch.setattr(rt, "daily_bar_aggregator", DailyBarAggregator(store=store))


def _run(monkeypatch, minutes):
    rt.reset_runtime_state()
    calls = {}
    syms = sorted(set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK})

    async def fake_fetch(symbols, session_date, deadline_sec=20.0):
        return [BarEvent(s, 10.0, 10.5, 9.5, 10.0, 100, OPEN_UTC + timedelta(minutes=i)) for s in syms for i in range(minutes)]

    def fake_eval(session_date, allow_new_entries=True, data_note=None):
        calls.update(allow=allow_new_entries, note=data_note, bar=rt.daily_bar_store.get_latest_bar("MU"))
        return {}

    monkeypatch.setattr(rt, "_fetch_session_minutes", fake_fetch)
    monkeypatch.setattr(rt.swing_strategy_engine, "evaluate_market_close", fake_eval)
    monkeypatch.setattr(rt, "_checkpoint_runtime", lambda *a, **k: True)
    asyncio.run(rt._swing_close_with_backfill(date(2026, 9, 24), max_wait_sec=0.0, retry_sec=0.0))
    return calls


def test_full_coverage_allows_entries_and_finalizes_repaired_bar(monkeypatch):
    calls = _run(monkeypatch, 390)
    assert calls["allow"] is True and calls["note"] is None
    assert calls["bar"].date == date(2026, 9, 24) and calls["bar"].volume == 390 * 100


def test_short_coverage_withholds_new_entries(monkeypatch):
    calls = _run(monkeypatch, 200)
    assert calls["allow"] is False
    assert "Incomplete minute data" in calls["note"]


def test_missing_final_minute_withholds_entries(monkeypatch):
    rt.reset_runtime_state()
    calls = {}
    syms = sorted(set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK})

    async def fake_fetch(symbols, session_date, deadline_sec=20.0):
        # 389 minutes: everything except 15:59
        return [BarEvent(s, 10.0, 10.5, 9.5, 10.0, 100, OPEN_UTC + timedelta(minutes=i)) for s in syms for i in range(389)]

    monkeypatch.setattr(rt, "_fetch_session_minutes", fake_fetch)
    monkeypatch.setattr(rt.swing_strategy_engine, "evaluate_market_close",
                        lambda d, allow_new_entries=True, data_note=None: calls.update(allow=allow_new_entries) or {})
    monkeypatch.setattr(rt, "_checkpoint_runtime", lambda *a, **k: True)
    asyncio.run(rt._swing_close_with_backfill(date(2026, 9, 24), max_wait_sec=0.0, retry_sec=0.0))
    assert calls["allow"] is False


def test_early_close_session_needs_only_its_own_minutes(monkeypatch):
    rt.reset_runtime_state()
    calls = {}
    syms = sorted(set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK})
    open_utc = datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc)  # 09:30 EST

    async def fake_fetch(symbols, session_date, deadline_sec=20.0):
        return [BarEvent(s, 10.0, 10.5, 9.5, 10.0, 100, open_utc + timedelta(minutes=i)) for s in syms for i in range(240)]

    monkeypatch.setattr(rt, "_fetch_session_minutes", fake_fetch)
    monkeypatch.setattr(rt.swing_strategy_engine, "evaluate_market_close",
                        lambda d, allow_new_entries=True, data_note=None: calls.update(allow=allow_new_entries, bar=rt.daily_bar_store.get_latest_bar("MU")) or {})
    monkeypatch.setattr(rt, "_checkpoint_runtime", lambda *a, **k: True)
    asyncio.run(rt._swing_close_with_backfill(date(2026, 11, 27), max_wait_sec=0.0, retry_sec=0.0))
    assert calls["allow"] is True
    assert calls["bar"].volume == 210 * 100  # minutes after the 13:00 early close are ignored


def test_startup_finishes_a_close_scan_that_never_completed(monkeypatch):
    rt.reset_runtime_state()
    ran = {}

    async def fake_close(d, max_wait_sec=120.0, retry_sec=15.0):
        ran["date"] = d

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 24, 16, 30, tzinfo=rt.ET_TZ)

    monkeypatch.setattr(rt, "_swing_close_with_backfill", fake_close)
    monkeypatch.setattr(rt, "datetime", FakeNow)
    bench = rt.daily_bar_store.get_latest_bar(settings.SWING_BENCHMARK)
    assert bench.date < date(2026, 9, 24)
    asyncio.run(rt._startup_backfill())
    assert ran["date"] == date(2026, 9, 24)
