# @steered SNARE-2 2026-09-30
"""T11: the checkpoint key "overnight" (plan 4.8, E16).

Round trip through the real durable store, an old checkpoint without the key, the production
fixture, and a new checkpoint decoded by the persistence decoder of the commit before this build
(read from git, not from the working tree), with no __type__ class anywhere in the key."""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from backend.app.core.persistence import TradingStateStore, decode_runtime_value
from backend.tests.unit.overnight_integration.fakes import MainOvernight, at, buy_night, queue_sales

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)
ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "backend/tests/unit/orb_integration/prod_checkpoint_2026_09_28_trimmed.json"
BASE_COMMIT = "fc66bcb"          # the plan commit this branch starts from (before any overnight code)


def _types(obj):
    if isinstance(obj, dict):
        return (["__type__"] if "__type__" in obj else []) + [t for v in obj.values() for t in _types(v)]
    if isinstance(obj, list):
        return [t for v in obj for t in _types(v)]
    return []


def _held_with_sales(r, tmp_path):
    h = MainOvernight(r, at(THU, 15, 40))
    r.state_store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    buy_night(h, THU)
    queue_sales(h, THU)
    return h


def test_round_trip_keeps_holds_intents_control_and_offset(main_runtime, tmp_path):
    r = main_runtime
    h = _held_with_sales(r, tmp_path)
    r.overnight.ledger["offset"] = {"date": THU.isoformat(), "amount": -12.5}
    assert r._checkpoint_runtime("TEST")
    saved_ctl = r.overnight.controller.to_json()
    saved_ledger = json.loads(json.dumps(r.overnight.ledger))
    positions = {s: (p.shares, p.avg_entry_price, p.strategy_id) for s, p in r.account.positions.items()}
    store = r.state_store
    r.reset_runtime_state()
    assert r.overnight.controller is None
    r.state_store = store
    assert r._restore_checkpoint()
    h.build()
    assert r.overnight.controller.to_json() == saved_ctl
    assert r.overnight.ledger == saved_ledger
    assert {s: (p.shares, p.avg_entry_price, p.strategy_id) for s, p in r.account.positions.items()} == positions
    assert r.risk_engine.overnight_realized_today == -12.5        # re-applied after the restore (S14)
    payload, _rev, _at = store.load_checkpoint()
    assert "overnight" in payload and _types(payload["overnight"]) == []


def test_old_checkpoint_without_the_key_restores(main_runtime, tmp_path):
    r = main_runtime
    h = _held_with_sales(r, tmp_path)
    store = r.state_store
    payload = r._capture_checkpoint()
    payload.pop("overnight")
    store.save_checkpoint(payload, reason="OLD_BUILD")
    r.reset_runtime_state()
    r.state_store = store
    assert r._restore_checkpoint()
    assert r.overnight.checkpoint_state() is None and r.overnight.init_error is None
    h.build()
    assert r.overnight.controller.state["nights"] == {}
    assert r.risk_engine.overnight_realized_today == 0.0


def test_unreadable_section_turns_only_the_overnight_holds_off(main_runtime, tmp_path):
    r = main_runtime
    _held_with_sales(r, tmp_path)
    store = r.state_store
    payload = r._capture_checkpoint()
    payload["overnight"] = {"version": 99, "controller": {"version": 99}}
    store.save_checkpoint(payload, reason="FUTURE_BUILD")
    r.reset_runtime_state()
    r.state_store = store
    assert r._restore_checkpoint()                                  # ADT itself starts
    assert "cannot read" in r.overnight.init_error
    r.overnight.start(object())
    assert r.overnight.controller is None                          # never read as empty
    assert r.overnight.checkpoint_state() == {"version": 99, "controller": {"version": 99}}   # saved back unchanged


def test_production_fixture_restores_then_saves_the_key_as_plain_json(main_runtime, tmp_path):
    from backend.tests.unit.orb_integration.test_checkpoint_migration import _v2_store
    r = main_runtime
    row = json.load(open(FIXTURE))
    db = str(tmp_path / "trading_state.sqlite3")
    _v2_store(db, row)
    r.state_store = TradingStateStore(db)
    assert r._restore_checkpoint() is True
    assert r.overnight.checkpoint_state() is None                  # production has no overnight key yet
    h = MainOvernight.__new__(MainOvernight)                        # attach the fake without a reset
    from backend.tests.unit.orb_execution.fakes import FakeClock
    from backend.tests.unit.overnight_integration.fakes import OvernightAlpaca, make_broker
    h.r, h.clock = r, FakeClock(at(THU, 15, 40))
    h.alpaca = OvernightAlpaca(h.clock)
    h.broker = make_broker(h.alpaca)
    h.bar_counts, h.bar_calls, h._bars = {}, [], None
    h.build()
    assert r._checkpoint_runtime("TEST_NEW_BUILD")
    payload, _rev, _at = r.state_store.load_checkpoint()
    assert payload["overnight"]["version"] == 1 and _types(payload["overnight"]) == []
    decoded = decode_runtime_value(payload)
    assert decoded["overnight"] == payload["overnight"]


def _base_persistence():
    """backend/app/core/persistence.py exactly as it was before this build, loaded from git."""
    try:
        src = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE_COMMIT}:backend/app/core/persistence.py"],
                             check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:   # pragma: no cover - a checkout without history
        pytest.fail(f"cannot read the base commit's persistence.py from git: {exc}")
    spec = importlib.util.spec_from_loader("base_persistence", loader=None)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["base_persistence"] = mod
    exec(compile(src, "base_persistence.py", "exec"), mod.__dict__)
    return mod


def test_new_checkpoint_decodes_with_the_previous_builds_decoder(main_runtime, tmp_path):
    r = main_runtime
    _held_with_sales(r, tmp_path)
    assert r._checkpoint_runtime("TEST")
    payload, _rev, _at = r.state_store.load_checkpoint()
    base = _base_persistence()
    decoded = base.decode_runtime_value(json.loads(json.dumps(payload)))
    assert decoded["overnight"] == payload["overnight"]
    assert decoded["runtime_state_version"] == 2
    # this branch did not change the decoder at all
    head = (ROOT / "backend/app/core/persistence.py").read_text()
    base_src = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE_COMMIT}:backend/app/core/persistence.py"],
                              check=True, capture_output=True, text=True).stdout
    assert head == base_src
