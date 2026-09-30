# @steered SNARE-2 2026-09-30
"""T5: overnight holds and their Alpaca orders survive every exit path of the robot.

Plan S3 to S10: 15:45 lockout, 15:50 purge, 15:55 flatten, 15:58 audit and sweep, 16:00 close,
the session boundary, the loss stop, manual flatten one and all, the news contradiction exit and
the tighten stop button. A hold may only ever be sold by its own opening sale (R2, R4). Each test
fails if any generic path cancels an overnight order or sells an overnight share."""
import asyncio
from datetime import date

from backend.app.core import overnight_schedule as osch
from backend.app.main import FlattenRequest, manual_flatten
from backend.app.strategies.base import SignalEvent
from backend.tests.unit.overnight_integration.fakes import PRICES, MainOvernight, at, buy_night, open_sale, queue_sales

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)
SHARES = {"NVDA": 55, "IREN": 248, "HUT": 198}


def _deletes_of_overnight(h):
    ids = {o["id"] for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-ovn-")}
    return [p for m, p, _b, _q in h.alpaca.requests if m == "DELETE" and p.rsplit("/", 1)[-1] in ids]


def _non_overnight_sells(h, sym=None):
    return [b for b in h.alpaca.posts(sym) if b["side"] == "sell" and not b["client_order_id"].startswith("adt-ovn-")]


def _held(h):
    return {s: h.r.account.positions[s].shares for s in SHARES if s in h.r.account.positions}


def _generic_orders(h):
    """Engine orders any generic path created in a held stock. Must stay empty: even a refused
    liquidation is wrong (S1 would refuse it, but e.g. the session boundary would then stay
    incomplete and never advance the day), so each path must skip holds itself."""
    return [(o.symbol, o.strategy_id, o.status.value) for o in h.r.engine.orders.values()
            if o.symbol in SHARES and not osch.is_overnight(o)]


def test_buy_orders_survive_1550_1555_1558_and_1600(main_runtime):
    """The closing auction buys rest at Alpaca from 15:46 to 16:00. The 15:50 purge, the 15:55
    flatten, the 15:58 audit and the 16:00 close must never cancel them."""
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    fe = h.r.flattening_engine                          # the flatten phases ran on main's real clock
    assert fe.phase1_executed and fe.phase2_executed and fe.phase3_executed and fe.phase4_executed
    assert _deletes_of_overnight(h) == []
    assert [b["time_in_force"] for b in h.alpaca.posts() if b["side"] == "buy"] == ["cls"] * 3
    assert _held(h) == SHARES
    assert h.local() == h.alpaca.signed_positions()
    assert not h.r.engine.working_orders                # no overnight order ever rests in the engine (R4)


def test_hold_survives_session_boundary_and_its_sale_order_too(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    sales = {o["symbol"]: o["id"] for o in h.alpaca.live(side="sell")}
    assert set(sales) == set(SHARES)
    h.run(at(FRI, 0, 1), every=60)                      # midnight: the session boundary runs
    assert h.r.last_session_date == FRI
    assert _held(h) == SHARES
    assert _generic_orders(h) == []
    assert {o["symbol"]: o["id"] for o in h.alpaca.live(side="sell")} == sales
    assert _deletes_of_overnight(h) == [] and _non_overnight_sells(h) == []
    # the hold is still at its buy price after the boundary (S15)
    assert all(h.r.account.positions[s].market_price == PRICES[s] for s in SHARES)


def test_loss_stop_never_liquidates_holds_or_cancels_their_sale(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.set(at(FRI, 9, 5))
    h.r._trip_circuit_breaker(h.clock.now)
    assert _held(h) == SHARES
    assert _generic_orders(h) == []
    assert _deletes_of_overnight(h) == [] and _non_overnight_sells(h) == []
    open_sale(h, FRI, {"NVDA": 182.0, "IREN": 41.0, "HUT": 49.0})
    assert _held(h) == {} and h.alpaca.signed_positions() == {}      # the sale still ran (R2)


def test_manual_flatten_one_and_all_leave_holds_and_say_when_they_sell(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.set(at(THU, 20, 0))
    one = asyncio.run(manual_flatten(FlattenRequest(symbol="NVDA")))
    assert one["flattened"] == [] and one["skipped"][0]["symbol"] == "NVDA"
    assert "not included" in one["skipped"][0]["reason"] and "Fri Oct 2" in one["skipped"][0]["reason"]
    every = asyncio.run(manual_flatten())
    assert {row["symbol"] for row in every["skipped"]} == set(SHARES)
    assert every["overnight_note"].startswith("Overnight holds are not included.")
    assert "9:30 AM open on Fri Oct 2" in every["overnight_note"]
    assert _held(h) == SHARES
    assert _deletes_of_overnight(h) == [] and _non_overnight_sells(h) == []


def test_news_contradiction_leaves_holds(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.set(at(FRI, 9, 10))
    sig = SignalEvent("NVDA", "SELL", "MARKET", 175.0, 0.0, 0.0, 0.0, "news_momentum", 0.9, "NEWS_CONTRADICTION")
    asyncio.run(h.r.execute_strategy_signal(sig))
    assert _held(h) == SHARES and _non_overnight_sells(h) == []
    assert _generic_orders(h) == []

    assert _deletes_of_overnight(h) == []


def test_tighten_stop_socket_refuses_a_hold_in_plain_words(main_runtime):
    from fastapi.testclient import TestClient
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)

    # not entered as a context manager, so the app's lifespan (production startup) does not run
    client = TestClient(r.app)
    got = {}

    def talk():
        with client.websocket_connect("/ws/ui") as ws:
            ws.receive_text()                                  # the first state frame
            ws.send_text('{"action": "TIGHTEN_STOP", "symbol": "NVDA", "new_stop": 170.0}')
            while True:
                m = ws.receive_json()
                if m.get("type") == "error":
                    got["msg"] = m
                    return
                if m.get("type") == "STATE_UPDATE" and got.get("frames", 0) >= 3:
                    return                                     # the refusal never came
                got["frames"] = got.get("frames", 0) + 1

    import threading
    t = threading.Thread(target=talk, daemon=True)
    t.start()
    t.join(timeout=20)
    assert "msg" in got, "the tighten stop was not refused"
    msg = got["msg"]
    assert msg["message"].startswith("An overnight hold has no stop")
    assert "sells at the 9:30 AM open" in msg["message"]
    assert _held(h) == SHARES


def test_unsold_hold_survives_the_sale_day_1555_flatten_and_1558_sweep(main_runtime):
    """A halted open leaves the hold unsold (the 09:31 market sale rests too). The day's 15:55
    flatten and 15:58 sweep must still leave it to its own controller (S3, S4)."""
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.alpaca.halted.add("IREN")
    open_sale(h, FRI, until=(9, 32))
    assert _held(h) == {"IREN": 248}
    h.set(at(FRI, 15, 54))
    h.run(at(FRI, 15, 59, 30), every=5)
    assert _held(h) == {"IREN": 248}
    assert _non_overnight_sells(h, "IREN") == []
    assert _generic_orders(h) == []
    assert h.r.flattening_engine.audit_passed          # S4: the hold does not fail the zero overnight audit
    assert h.r.flattening_engine.phase4_executed
    assert "IREN" in h.r.overnight.controller.unsold_after_0931(h.clock.now)


def test_1558_sweep_run_for_a_stuck_day_trade_still_skips_the_hold(main_runtime):
    """The 15:58 audit fails because a halted day trade could not be flattened at 15:55; its
    emergency sweep must sweep that day trade only, never the unsold overnight hold (S4)."""
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.alpaca.halted.add("IREN")
    open_sale(h, FRI, until=(9, 32))
    h.set(at(FRI, 15, 30))
    h.day_trade("AMD", 50, 100.0, 98.0)
    assert h.r.account.positions["AMD"].shares == 50
    h.alpaca.halted.add("AMD")
    h.run(at(FRI, 15, 58, 30), every=5)
    assert h.r.flattening_engine.phase4_executed and not h.r.flattening_engine.audit_passed
    assert any(o.strategy_id == "EMERGENCY_SWEEP" and o.symbol == "AMD" for o in h.r.engine.orders.values())
    assert _generic_orders(h) == [] and _held(h) == {"IREN": 248}
