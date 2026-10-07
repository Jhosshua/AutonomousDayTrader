# @steered SNARE-2 2026-09-30
"""The overnight endpoints, /health, the lifespan build with its real worker pool, the relay REST
bar count (X2), the bar subscriptions (S16) and the watchdog (S19)."""
import importlib.util
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core import overnight_schedule as osch
from backend.app.core.persistence import TradingStateStore
from backend.app.models.events import BarEvent
from backend.tests.unit.overnight_integration.fakes import PRICES, MainOvernight, at, buy_night

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)
ROOT = Path(__file__).resolve().parents[4]


def _client(r):
    return TestClient(r.app)          # not a context manager: the production lifespan does not run


def test_health_carries_the_overnight_block_and_stays_200(main_runtime):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    resp = _client(r).get("/health")
    assert resp.status_code == 200
    ovn = resp.json()["overnight"]
    assert ovn["mode"] == "live" and ovn["running"] is True
    assert ovn["holds"] == ["NVDA", "IREN", "HUT"]
    assert {"intents", "queued_sales", "needs_look", "last_booking", "realized_today"} <= set(ovn)
    # a broken payload still leaves /health at 200
    r.overnight.controller.state["nights"]["NVDA:2026-10-01"]["legs"] = None
    assert _client(r).get("/health").status_code == 200


def test_api_overnight_reads_live_state_settings_and_the_research_summary(main_runtime):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    body = _client(r).get("/api/overnight").json()
    assert [row["symbol"] for row in body["rows"]] == ["NVDA", "IREN", "HUT"]
    assert body["rows"][0]["state"] == "HELD" and body["rows"][0]["held_qty"] == 55
    assert body["rows"][0]["size_note"] == "20% of the account a night (about $9,940), no stop"
    assert body["settings"]["pct"] == 0.20 and body["settings"]["cap"] == 25_000.0
    assert {x["symbol"] for x in body["holds"]} == {"NVDA", "IREN", "HUT"}
    assert body["research"]["proven"] is False and body["research"]["evidence_line"].startswith("Not proven")
    assert [s["nights"] for s in body["research"]["strategies"]] == [742, 736, 742]
    assert {"skips", "fidelity", "intents", "queued_sales", "state"} <= set(body)


def test_research_summary_is_inside_the_image_and_startup_does_not_read_research():
    summary = ROOT / "backend/app/data/overnight_research_summary.json"
    assert summary.is_file()
    docker = (ROOT / "Dockerfile").read_text()
    assert "COPY backend ./backend" in docker
    src = (ROOT / "backend/app/core/overnight_integration.py").read_text()
    main_src = (ROOT / "backend/app/main.py").read_text()
    assert "research/edge_hunt" not in src and "research/edge_hunt" not in main_src


def test_no_buy_tonight_is_saved_before_the_reply_and_refused_after_154930(main_runtime, tmp_path):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    r.state_store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    client = _client(r)
    resp = client.post("/api/overnight/no-buy-tonight", json={"on": True})
    assert resp.status_code == 200 and resp.json()["no_buy_tonight"] is True
    saved, _rev, _at = r.state_store.load_checkpoint()
    assert saved["overnight"]["controller"]["control"]["no_buy_date"] == THU.isoformat()   # durable first
    resp = client.post("/api/overnight/no-buy-tonight", json={"on": False})
    assert resp.status_code == 200 and resp.json()["no_buy_tonight"] is False
    h.set(at(THU, 15, 49, 31))
    resp = client.post("/api/overnight/no-buy-tonight", json={"on": True})
    assert resp.status_code == 409 and "3:49:30 PM" in resp.json()["detail"]


def test_no_buy_tonight_not_applied_when_the_save_fails(main_runtime, monkeypatch):
    r = main_runtime
    MainOvernight(r, at(THU, 15, 40))
    monkeypatch.setattr(r, "_checkpoint_runtime", lambda *a, **k: False)
    resp = _client(r).post("/api/overnight/no-buy-tonight", json={"on": True})
    assert resp.status_code == 409 and resp.json()["detail"].startswith("Not saved")
    assert r.overnight.controller.state["control"]["no_buy_date"] is None


def test_lifespan_build_with_its_worker_pool_and_the_relay_bar_count(main_runtime, monkeypatch):
    """The production build path: start() with the default thread pool and the relay REST count
    through main's _fetch_session_minutes (patched to answer like the relay). The durable intent is
    prepared at 15:46 and the market buy goes out from the pool at 15:59:30."""
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 46, 4), build=False)
    for sym, px in PRICES.items():
        h.price(sym, px)
    monkeypatch.setattr(r.settings, "RELAY_TOKEN", "test-token")
    calls = []

    async def fake_minutes(symbols, session_date, deadline_sec=20.0):
        calls.append((tuple(symbols), session_date))
        start = datetime(session_date.year, session_date.month, session_date.day, 9, 30, tzinfo=osch.ET)
        return [BarEvent(symbols[0], 1.0, 1.0, 1.0, PRICES[symbols[0]], 100, start + timedelta(minutes=i))
                for i in range(376)]      # 09:30 to 15:45 inclusive: only the 375 before 15:45 count

    monkeypatch.setattr(r, "_fetch_session_minutes", fake_minutes)
    r.overnight.start(h.broker)
    ctl = r.overnight.controller
    ctl.clock = h.clock
    assert type(r.overnight.executor).__name__ == "ThreadPoolExecutor"
    h.set(at(THU, 15, 46, 5))
    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline and ctl.state["nights"].get("HUT:2026-10-01", {}).get("state") != "INTENT":
        r.overnight.tick(h.clock.now)
        _time.sleep(0.02)
    assert {n["symbol"]: n["state"] for n in ctl.state["nights"].values()} == {
        "NVDA": "INTENT", "IREN": "INTENT", "HUT": "INTENT"}
    assert h.alpaca.posts() == []
    assert {n["symbol"]: n["bars"]["count"] for n in ctl.state["nights"].values()} == {"NVDA": 375, "IREN": 375, "HUT": 375}
    assert sorted(c[0][0] for c in calls) == ["HUT", "IREN", "NVDA"]
    h.set(at(THU, 15, 59, 30))
    for sym, px in PRICES.items():
        h.price(sym, px)
    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline and ctl.state["nights"].get("HUT:2026-10-01", {}).get("state") != "HELD":
        r.overnight.tick(h.clock.now)
        _time.sleep(0.02)
    assert {n["symbol"]: n["state"] for n in ctl.state["nights"].values()} == {
        "NVDA": "HELD", "IREN": "HELD", "HUT": "HELD"}
    assert [body["time_in_force"] for body in h.alpaca.posts()] == ["day"] * 3
    r.overnight.shutdown()


@pytest.mark.parametrize("age_seconds", [None, 121.0, -2.0])
def test_production_market_preflight_rejects_missing_stale_or_future_price_times(
        main_runtime, age_seconds):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 59, 20))
    h.price("NVDA", PRICES["NVDA"])
    if age_seconds is None:
        del r.latest_market_price_times["NVDA"]
    else:
        r.latest_market_price_times["NVDA"] = h.clock.now - timedelta(seconds=age_seconds)
    with pytest.raises(RuntimeError, match="NVDA"):
        r.overnight._fresh_prices(("NVDA",), h.clock.now)


def test_relay_failure_is_a_logged_skip_at_154930(main_runtime, monkeypatch):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40), build=False)
    for sym, px in PRICES.items():
        h.price(sym, px)
    monkeypatch.setattr(r.settings, "RELAY_TOKEN", "test-token")

    async def down(symbols, session_date, deadline_sec=20.0):
        raise TimeoutError("relay backfill deadline exceeded")

    monkeypatch.setattr(r, "_fetch_session_minutes", down)
    from backend.app.core.overnight_execution import InlineExecutor
    import concurrent.futures
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)   # asyncio.run needs a thread with no loop
    h.ctl = r.overnight.build(h.broker, mode="live", clock=h.clock, executor=pool)
    try:
        h.set(at(THU, 15, 46, 5))
        for _ in range(4):                                         # the job runs on the pool
            r.overnight.tick(h.clock.now)
            _time.sleep(0.05)
        assert all(n["block"] == osch.RELAY_UNAVAILABLE for n in h.ctl.state["nights"].values())
        h.set(at(THU, 15, 49, 31))
        r.overnight.tick(h.clock.now)
        _time.sleep(0.05)
        r.overnight.tick(h.clock.now)
        assert all(n["state"] == "SKIPPED" and n["reason"] == osch.RELAY_UNAVAILABLE for n in h.ctl.state["nights"].values())
        assert any(row["event"] == "SKIP" and row.get("reason") == osch.RELAY_UNAVAILABLE for row in h.ctl.state["log"])
        assert [b for b in h.alpaca.posts() if b["side"] == "buy"] == []
    finally:
        pool.shutdown(wait=True)
    assert InlineExecutor is not None


@pytest.mark.asyncio
async def test_overnight_bars_come_from_their_own_list_not_the_day_watchlist(main_runtime):
    r = main_runtime
    from backend.app.ingestion.stock_ws import StockWebSocketClient
    subs = StockWebSocketClient(relay_url="ws://127.0.0.1:1", relay_token="x").symbols
    assert {"NVDA", "IREN", "HUT"} <= subs
    assert "IREN" not in r.settings.WATCHLIST_SYMBOLS and "HUT" not in r.settings.WATCHLIST_SYMBOLS


def test_watchdog_does_not_flag_overnight_holds_after_1558(monkeypatch):
    spec = importlib.util.spec_from_file_location("production_watchdog", ROOT / "scripts/production_watchdog.py")
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 1, 20, 30, tzinfo=timezone.utc)   # 16:30 ET

    health = {"status": "healthy", "account": {"open_positions": 3},
              "overnight": {"holds": ["NVDA", "IREN", "HUT"]}, "feeds": {}}
    monkeypatch.setattr(wd, "datetime", Clock)
    monkeypatch.setattr(wd, "fetch_json", lambda url, timeout=6.0: health if url.endswith("/health") else {})
    audit = wd.run_watchdog_audit("http://watchdog.test")
    assert not [i for i in audit["incidents"] if i["component"] == "zero_overnight_mandate"]
    health["account"]["open_positions"] = 4                           # one real day position left
    audit = wd.run_watchdog_audit("http://watchdog.test")
    assert [i for i in audit["incidents"] if i["component"] == "zero_overnight_mandate"]


def test_fidelity_log_records_research_style_prices_and_the_look_ahead_check(main_runtime, monkeypatch):
    """Section 4.9 (gates nothing): 16:15 buy day entry, 09:45 sale day exit, 16:15 sale day
    look ahead check, from SIP minute bars; a skipped night gets the return the rule would have made."""
    from backend.tests.unit.overnight_integration.fakes import open_sale, queue_sales
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    h.bar_counts["HUT"] = (100, True)                      # HUT skipped tonight (data short)
    buy_night(h, THU, close={"NVDA": 181.0, "IREN": 41.0, "HUT": 51.0})
    queue_sales(h, THU)
    monkeypatch.setattr(r.settings, "RELAY_TOKEN", "test-token")

    async def minutes(symbols, session_date, deadline_sec=20.0):
        sym = symbols[0]
        base = {"NVDA": 181.0, "IREN": 41.0, "HUT": 51.0}[sym] + (0 if session_date == THU else 2.0)
        start = datetime(session_date.year, session_date.month, session_date.day, 9, 30, tzinfo=osch.ET)
        return [BarEvent(sym, base, base, base, base, 100, start + timedelta(minutes=i)) for i in range(390)]

    monkeypatch.setattr(r, "_fetch_session_minutes", minutes)
    for when in (at(THU, 16, 15), at(THU, 16, 15, 1)):
        h.set(when)
        r.overnight.tick(when)
    open_sale(h, FRI, {"NVDA": 183.0, "IREN": 43.0, "HUT": 53.0}, until=(9, 31))
    for when in (at(FRI, 9, 45), at(FRI, 9, 45, 1), at(FRI, 16, 15), at(FRI, 16, 15, 1)):
        h.set(when)
        r.overnight.tick(when)
    fid = r.overnight.controller.state["nights"]["NVDA:2026-10-01"]["fidelity"]
    assert fid["research_entry"] == 181.0 and fid["research_exit"] == 183.0 and fid["last_bar_minute"] == "15:59"
    assert fid["research_gross"] == pytest.approx(183.0 / 181.0 - 1)
    assert fid["real_gross"] == pytest.approx(183.0 / 181.0 - 1)
    assert fid["lookahead_ok"] is True and fid["feed"] == "sip"
    hut = r.overnight.controller.state["nights"]["HUT:2026-10-01"]
    assert hut["state"] == "SKIPPED" and hut["reason"] == osch.DATA_SHORT
    assert hut["fidelity"]["research_gross"] == pytest.approx(53.0 / 51.0 - 1)   # what the rule would have made
    body = _client(r).get("/api/overnight").json()
    assert {row["symbol"] for row in body["fidelity"]} == {"NVDA", "IREN", "HUT"}
