"""ORB startup wiring, dashboard/API views, and the never-block guarantee of main's clock tick."""
import asyncio
import json
import threading
import time as _time

import pytest

from backend.tests.unit.orb_integration.harness import MainOrb, at, pick


def test_startup_in_local_mode_runs_shadow_with_no_broker(main_runtime, monkeypatch, tmp_path):
    r = main_runtime
    monkeypatch.setattr(r.settings, "ORB_MODE", "live")
    monkeypatch.setattr(r.settings, "BROKER_MODE", "simulated")
    monkeypatch.setattr(r.settings, "ORB_STATE_DIR", str(tmp_path / "orbs"))
    r.orb.start()
    try:
        assert r.orb.controller is not None and r.orb.controller.broker is None
        assert r.orb.mode == "shadow" and "BROKER_MODE=alpaca_paper" in r.orb.init_error
        assert r.orb.controller.expected_account == "PA3CSVDZMMPY"
        # ADT's TSLA/CDE exclusion is applied on top of the parity manifest (which keeps [] for parity)
        assert r.orb.controller.cfg["excluded_symbols"] == ["CDE", "TSLA"]
        from backend.app.strategies.orbs import config as orbs_config
        assert orbs_config.EXCLUDE_SYMBOLS == frozenset({"TSLA", "CDE"})
        cfg = r.orb.controller.cfg
        assert (cfg["request_budget_per_min"], cfg["exit_reserve_per_min"]) == (100, 50)
        assert (cfg["cutoff"], cfg["flatten"], cfg["risk_pct"], cfg["max_day_risk_frac"]) == ("10:15", "11:00", 2.0, 0.025)
        assert (tmp_path / "orbs").exists()
    finally:
        r.orb.shutdown()


def test_an_unknown_orb_mode_means_off(main_runtime, monkeypatch, tmp_path):
    r = main_runtime
    monkeypatch.setattr(r.settings, "ORB_MODE", "yolo")
    monkeypatch.setattr(r.settings, "ORB_STATE_DIR", str(tmp_path / "orbs"))
    r.orb.start()
    try:
        assert r.orb.mode == "off" and "not off/shadow/live" in r.orb.init_error
    finally:
        r.orb.shutdown()


def test_default_settings():
    from backend.app.config import Settings
    s = Settings(_env_file=None)
    assert (s.ORB_MODE, s.ORB_EXPECTED_ACCOUNT, s.ORB_EXCLUDE_SYMBOLS) == ("shadow", "PA3CSVDZMMPY", ["TSLA", "CDE"])


def test_state_dir_defaults_next_to_the_state_db(main_runtime, monkeypatch):
    r = main_runtime
    monkeypatch.setattr(r.settings, "ORB_STATE_DIR", "")
    monkeypatch.setattr(r.settings, "STATE_DB_PATH", "/data/trading_state.sqlite3")
    assert r.orb.state_dir() == "/data/orbs"


def test_strategies_health_and_orb_endpoints_expose_orb(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    cards = asyncio.run(r.get_strategies())
    card = next(c for c in cards if c["id"] == "orb")
    assert card["name"] == "Opening Range Breakout (ORBStraddle rules)"
    assert card["window"]["hours"] == "Decides 9:38 AM, may add trades until 10:15 AM, closes by 11:00 AM"
    assert card["orb"]["mode_text"] == "Live: paper account orders"
    assert card["orb"]["open_trades"][0]["symbol"] == "APP"
    health = asyncio.run(r.get_health())
    assert health["orb"]["mode"] == "live" and health["orb"]["open_trades"] == 1 and health["orb"]["ready"]
    assert "step" in health["orb"] and "errors" in health["orb"]
    orb = asyncio.run(r.get_orb())
    assert orb["holdings"][0]["symbol"] == "APP" and orb["open_risk"] == pytest.approx(454 * 2.2, abs=0.01)
    json.dumps(r._sanitize_for_json(orb), allow_nan=False)
    json.dumps(r._sanitize_for_json(cards), allow_nan=False)
    pos = r._serialize_position("APP", include_chart=False)
    assert pos["strategy_id"] == "orb" and pos["fixed_protection"] is True
    assert (pos["stop_loss"], pos["take_profit_1"]) == (98.0, 101.85)


def test_no_old_orb_text_is_left_in_the_backend_or_the_dashboard():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    old = ("5m / 15m", "first 5 minutes", "first few minutes of the day", "breakout_fired", "notify_signal_rejected",
           "Captures institutional opening drives", "Morning Breakout", "OpeningRangeBreakoutStrategy",
           "evaluate_orb_signal")
    hits = []
    for base in ("backend/app", "frontend/app", "frontend/components", "frontend/hooks", "frontend/lib",
                 "frontend/types", "frontend/scripts"):
        for p in (root / base).rglob("*"):
            if p.suffix not in (".py", ".ts", ".tsx", ".mjs", ".js") or "orbs" in p.parts:
                continue
            text = p.read_text(errors="ignore")
            hits += [f"{p.relative_to(root)}: {w}" for w in old if w in text and "SymbolORBState" not in w]
    # the compat dataclass keeps the old field name so an old checkpoint still decodes
    hits = [x for x in hits if not (x.startswith("backend/app/strategies/orb.py") and x.endswith("breakout_fired"))]
    assert hits == []


def test_main_clock_tick_never_blocks_on_orb_network_work(main_runtime):
    """The scheduler's jobs run on worker threads: with a scan and every Alpaca call taking 300 ms,
    main's ORB tick (called from the event loop each second) still returns in a few ms."""
    r = main_runtime
    h = MainOrb(r, start=at(9, 10), inline=False, freeze=False)
    gate = threading.Event()
    real_handler = h.alpaca.handler

    def slow_handler(request):
        _time.sleep(0.3)
        return real_handler(request)
    h.broker._client._transport.handler = slow_handler      # every Alpaca call now takes 300 ms

    def slow_scan():
        gate.wait(2)
        return {"ok": True, "error": None, "coverage": 1.0, "cards": [{"symbol": "APP", "direction": "long"}],
                "board_id": "final"}
    h.facade.scan_results = [slow_scan, slow_scan]
    h.facade.decide_results = [{"verdict": "trade", "reason": None, "picks": [pick("APP", "long", 100.0, 98.0)],
                                "audit": []}]
    h.alpaca.prices["APP"] = 100.2
    worst = 0.0
    try:
        for t in (at(9, 15), at(9, 20), at(9, 36, 10), at(9, 38, 30), at(9, 38, 35)):
            h.clock.set(t)
            for _ in range(10):
                t0 = _time.perf_counter()
                r.orb.tick(h.clock.now)
                worst = max(worst, _time.perf_counter() - t0)
                _time.sleep(0.01)
        gate.set()
        deadline = _time.monotonic() + 10
        while _time.monotonic() < deadline and "APP" not in r.account.positions:
            t0 = _time.perf_counter()
            r.orb.tick(h.clock.now)
            worst = max(worst, _time.perf_counter() - t0)
            _time.sleep(0.02)
        assert "APP" in r.account.positions, r.orb.status()
        assert worst < 0.05, worst
    finally:
        gate.set()
        r.orb.shutdown()


def test_a_corrupt_orb_state_row_turns_orb_off_but_adt_still_starts(main_runtime, monkeypatch, tmp_path):
    from backend.app.core.persistence import TradingStateStore
    r = main_runtime
    r.state_store = TradingStateStore(str(tmp_path / "s.sqlite3"))
    try:
        r.state_store.save_orb_state("controller", {"version": 99})          # unsupported version
        monkeypatch.setattr(r.settings, "ORB_MODE", "shadow")
        monkeypatch.setattr(r.settings, "ORB_STATE_DIR", str(tmp_path / "orbs"))
        r.orb.start()
        assert r.orb.mode == "off" and "could not be restored" in r.orb.init_error
        card = next(c for c in r._strategy_cards() if c["id"] == "orb")
        assert "could not be restored" in card["orb"]["init_error"]
    finally:
        r.orb.shutdown()
        r.state_store.close()
