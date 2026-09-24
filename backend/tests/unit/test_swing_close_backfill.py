"""Close-time repair: backfill runs before finalize; short coverage withholds new swing entries."""
import asyncio
from datetime import date, datetime, timedelta, timezone

from backend.app import main as rt
from backend.app.config import settings
from backend.app.models.events import BarEvent

OPEN_UTC = datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc)


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
