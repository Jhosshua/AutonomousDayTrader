"""Checkpoint migration for the ORB replacement, on both layers (plan 8.4 / 9.4), with the REAL
production checkpoint (schema 2, runtime v1, the old ORB's SymbolORBState inside).

The row is written into a schema-2 SQLite store exactly as production has it; the new code must
upgrade the store to schema 3, restore the checkpoint into main (old ORB fields dropped, never
merged), save a schema-3 checkpoint, and restore that again."""
import json
import os
import sqlite3

import pytest

from backend.app.core.persistence import (
    LEGACY_ORB_KEEP, SCHEMA_VERSION, PersistenceError, TradingStateStore, migrate_legacy_orb_payload,
)

HERE = os.path.dirname(os.path.abspath(__file__))
TRIMMED = os.path.join(HERE, "prod_checkpoint_2026_09_28_trimmed.json")
# the untrimmed production row (3.5 MB), when present on this machine
FULL = os.environ.get("ADT_PROD_CHECKPOINT",
                      "/private/tmp/claude-502/-Users-mo/cc6c8c03-ae57-46ba-8660-6bd72618ca70/scratchpad/"
                      "prod_checkpoint_2026-09-28.json")

V2_SCHEMA = """
CREATE TABLE schema_info (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_version INTEGER NOT NULL);
CREATE TABLE runtime_checkpoint (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), revision INTEGER NOT NULL,
  schema_version INTEGER NOT NULL, saved_at TEXT NOT NULL, reason TEXT NOT NULL, checksum TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE completed_trades (trade_id TEXT PRIMARY KEY, session_date TEXT NOT NULL, closed_at TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE session_summaries (session_date TEXT PRIMARY KEY, payload TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE processed_events (event_key TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMMITTED')), created_at TEXT NOT NULL, committed_at TEXT);
INSERT INTO schema_info VALUES (1, 2);
"""


def _v2_store(path, row):
    """A production-shaped schema-2 database holding `row` (the runtime_checkpoint row as JSON)."""
    con = sqlite3.connect(path)
    con.executescript(V2_SCHEMA)
    con.execute("INSERT INTO runtime_checkpoint VALUES (1,?,?,?,?,?,?)",
                (row["revision"], row["schema_version"], row["saved_at"], row["reason"], row["checksum"], row["payload"]))
    con.commit()
    con.close()


def _sources():
    out = [pytest.param(TRIMMED, id="trimmed-fixture")]
    if os.path.exists(FULL):
        out.append(pytest.param(FULL, id="full-production-row"))
    return out


@pytest.mark.parametrize("source", _sources())
def test_real_production_checkpoint_restores_saves_and_restores_again(main_runtime, tmp_path, source):
    r = main_runtime
    row = json.load(open(source))
    assert row["schema_version"] == 2
    raw = json.loads(row["payload"])
    assert raw["runtime_state_version"] == 1
    old_orb = raw["strategies"]["orb"]
    assert "symbol_states" in old_orb and "min_rvol" in old_orb          # the old ORB really is in there
    assert "backend.app.strategies.orb:SymbolORBState" in row["payload"]
    db = str(tmp_path / "trading_state.sqlite3")
    _v2_store(db, row)

    r.state_store = TradingStateStore(db)
    try:
        con = r.state_store._connection
        assert con.execute("SELECT schema_version FROM schema_info").fetchone()[0] == SCHEMA_VERSION == 3
        assert con.execute("SELECT name FROM sqlite_master WHERE name='orb_state'").fetchone()

        assert r._restore_checkpoint() is True
        orb_state = r.orb_strategy.__dict__
        assert not ({"symbol_states", "min_rvol", "range_minutes", "min_clv", "max_extension_atr"} & set(orb_state))
        assert set(orb_state) <= set(LEGACY_ORB_KEEP) | {"name"}
        assert r.orb_strategy.name == "Opening Range Breakout (ORBStraddle rules)"
        assert r.orb_strategy.on_bar(None) == []
        # the rest of production's state came back
        acct = json.loads(row["payload"])["account"]
        assert r.account.equity == pytest.approx(acct["equity"])
        assert r.persistence_restored and r.persistence_healthy
        eq1, cash1, orders1 = r.account.equity, r.account.cash, set(r.engine.orders)

        # save as schema 3, then a fresh restore of what was saved
        assert r._checkpoint_runtime("TEST_MIGRATED") is True
        saved = con.execute("SELECT schema_version, payload FROM runtime_checkpoint").fetchone()
        assert saved[0] == 3
        payload3 = json.loads(saved[1])
        assert payload3["runtime_state_version"] == 2
        assert "SymbolORBState" not in saved[1]
        assert payload3["orb"]["version"] == 2 and r.orb.ledger_error is None   # ORB's ledger section
        r.reset_runtime_state()
        assert r._restore_checkpoint() is True
        assert (r.account.equity, r.account.cash) == (pytest.approx(eq1), pytest.approx(cash1))
        assert set(r.engine.orders) == orders1
        assert not ({"symbol_states", "min_rvol"} & set(r.orb_strategy.__dict__))
    finally:
        r.state_store.close()


def test_orb_state_rows_are_durable_and_checked(tmp_path):
    store = TradingStateStore(str(tmp_path / "s.sqlite3"))
    assert store.load_orb_state("controller") is None
    assert store.save_orb_state("controller", {"version": 1, "positions": {"a": 1}}) == 1
    assert store.save_orb_state("controller", {"version": 1, "positions": {}}) == 2
    store.save_orb_state("scheduler", {"version": 1, "day": "2026-09-28"})
    store.close()
    again = TradingStateStore(str(tmp_path / "s.sqlite3"))          # a new process
    assert again.load_orb_state("controller") == {"version": 1, "positions": {}}
    assert again.load_orb_state("scheduler")["day"] == "2026-09-28"
    with pytest.raises(PersistenceError):
        again.save_orb_state("other", {})
    again._connection.execute("UPDATE orb_state SET payload = '{}' WHERE section = 'scheduler'")
    with pytest.raises(PersistenceError):
        again.load_orb_state("scheduler")                               # checksum mismatch
    again.close()


def test_runtime_layer_migrates_a_v1_payload_before_decoding():
    """runtime_state.restore_runtime_state gets a v1 payload directly (no persistence layer): the old
    ORB fields are dropped before any persisted type is decoded, so they are never merged."""
    from backend.app.strategies.orb import SymbolORBState   # still importable (compat)
    assert SymbolORBState().breakout_fired is False
    row = json.load(open(TRIMMED))
    payload = json.loads(row["payload"])
    migrated = migrate_legacy_orb_payload(payload)
    assert migrated["runtime_state_version"] == 2
    assert set(migrated["strategies"]["orb"]) <= set(LEGACY_ORB_KEEP)
    assert migrate_legacy_orb_payload(migrated) == migrated               # idempotent
    assert migrated["strategies"]["vwap_pullback"] == payload["strategies"]["vwap_pullback"]


def test_an_unknown_future_checkpoint_schema_is_refused(tmp_path):
    db = str(tmp_path / "x.sqlite3")
    row = json.load(open(TRIMMED))
    _v2_store(db, dict(row, schema_version=9))
    store = TradingStateStore(db)
    try:
        with pytest.raises(PersistenceError, match="not supported"):
            store.load_checkpoint()
    finally:
        store.close()


def test_orb_controller_and_scheduler_state_survive_a_restart_through_the_store(main_runtime, tmp_path):
    """persist_cb -> orb_state rows (durable before return) -> a new build restores them."""
    from backend.tests.unit.orb_integration.harness import MainOrb
    r = main_runtime
    r.state_store = TradingStateStore(str(tmp_path / "t.sqlite3"))
    try:
        h = MainOrb(r)
        h.open_bracket()
        assert r.state_store.load_orb_state("controller")["positions"]
        before = r.state_store.load_orb_state("controller")
        ctl2, _ = r.orb.build(h.broker, h.facade, "live", clock=h.clock, inline=True,
                              monotonic=lambda: h.clock.now.timestamp(), is_session=lambda d: True)
        assert ctl2.state["positions"] == before["positions"] and ctl2.owns("APP")
        assert ctl2.ready is False                                     # entries wait for reconciliation
        assert ctl2.reconcile_on_startup()["ok"]
    finally:
        r.state_store.close()


def test_production_checkpoint_has_no_orb_section_so_orb_starts_normally(main_runtime, tmp_path, monkeypatch):
    r = main_runtime
    row = json.load(open(TRIMMED))
    assert "orb" not in json.loads(row["payload"])
    db = str(tmp_path / "trading_state.sqlite3")
    _v2_store(db, row)
    r.state_store = TradingStateStore(db)
    try:
        assert r._restore_checkpoint() is True
        assert r.orb.ledger_error is None
        monkeypatch.setattr(r.settings, "ORB_MODE", "shadow")
        monkeypatch.setattr(r.settings, "ORB_STATE_DIR", str(tmp_path / "orbs"))
        r.orb.start()
        assert r.orb.mode == "shadow" and not r.orb.alerts
    finally:
        r.orb.shutdown()
        r.state_store.close()


def test_an_old_or_unknown_orb_ledger_section_fails_closed_for_orb_only(main_runtime, tmp_path, monkeypatch):
    """A version-1 ORB ledger (keyed by controller record key; branch-only) is never read as empty:
    ORB goes off, books nothing, alerts; ADT restores and runs; the section is saved back unchanged."""
    r = main_runtime
    row = json.load(open(TRIMMED))
    payload = json.loads(row["payload"])
    payload = migrate_legacy_orb_payload(payload)
    v1 = {"version": 1, "booked": {"adt-orb-APP-2026-09-28-w1-a1": {"qty": 454, "notional": 45490.8}},
          "recorded": [], "logged": []}
    payload["orb"] = v1
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib
    row = dict(row, schema_version=3, payload=raw, checksum=hashlib.sha256(raw.encode()).hexdigest())
    db = str(tmp_path / "trading_state.sqlite3")
    _v2_store(db, row)
    r.state_store = TradingStateStore(db)
    try:
        assert r._restore_checkpoint() is True                         # ADT itself is up
        assert r.orb.ledger_error and "version 1" in r.orb.ledger_error
        monkeypatch.setattr(r.settings, "ORB_MODE", "live")
        monkeypatch.setattr(r.settings, "ORB_STATE_DIR", str(tmp_path / "orbs"))
        r.orb.start()
        assert r.orb.mode == "off" and "_ledger" in r.orb.alerts
        card = next(c for c in r._strategy_cards() if c["id"] == "orb")
        assert any("fill ledger" in a for a in card["orb"]["alerts"])
        # even with a filled ORB order in the controller's book, nothing is booked into ADT
        r.orb.controller._merge({"id": "o-1", "client_order_id": "adt-orb-APP-2026-09-28-w1-a1", "symbol": "APP",
                                 "side": "buy", "qty": "10", "filled_qty": "10", "filled_avg_price": "100",
                                 "status": "filled"}, role_hint="entry")
        assert r.orb.controller.own_qty("APP") == 10
        assert r.orb.sync() is False and "APP" not in r.account.positions   # never books
        assert r._checkpoint_runtime("TEST") is True
        saved = json.loads(r.state_store._connection.execute("SELECT payload FROM runtime_checkpoint").fetchone()[0])
        assert saved["orb"] == v1                                      # kept for a human, not overwritten
    finally:
        r.orb.shutdown()
        r.state_store.close()
