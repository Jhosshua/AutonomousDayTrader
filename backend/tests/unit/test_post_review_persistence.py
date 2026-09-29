"""Production checkpoint failure reproduced from the post-ORB deployment review."""
from datetime import datetime, timedelta, timezone

from backend.app.core.persistence import TradingStateStore


def test_session_rollover_saves_closed_tri_tranches_with_datetime_fields(tmp_path, monkeypatch):
    from backend.app import main as r

    r.reset_runtime_state()
    r.set_simulation_mode(False)
    store = TradingStateStore(str(tmp_path / "rollover.sqlite3"))
    monkeypatch.setattr(r, "state_store", store)
    monkeypatch.setattr(r.settings, "STATE_BACKUP_PATH", None)
    at = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    prior = at.astimezone(r.ET_TZ).date()
    r.last_session_date = prior
    strategy = r.tri_strategies[0]
    strategy.phase = "CLOSED"
    strategy.session_day = prior
    strategy.tranches = [{"id": 1, "qty": 1, "closed_qty": 1,
                          "entry_at": at, "exit_due": at + timedelta(minutes=30)}]
    try:
        r._check_session_boundary(at + timedelta(days=1))
        assert r.persistence_healthy, r.persistence_error
        assert not r.pending_session_summaries
        summary = store.list_session_summaries(prior.isoformat())[0]
        tranche = summary["tri_engine"][0]["tranches"][0]
        assert tranche["entry_at"] == at.isoformat()
        assert tranche["exit_due"] == (at + timedelta(minutes=30)).isoformat()
        checkpoint, revision, _saved_at = store.load_checkpoint()
        assert checkpoint is not None and revision > 0
        assert not store.list_pending_events()
    finally:
        r.reset_runtime_state()
        store.close()
