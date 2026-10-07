# @steered SNARE-2 2026-09-30
"""Two-session dry run through real main wiring with day one and overnight controllers sharing one fake Alpaca."""
from __future__ import annotations

from datetime import date

from backend.app.core import day_one_schedule as d1
from backend.app.core.day_one_execution import InlineExecutor
from backend.tests.unit.overnight_integration.fakes import MainOvernight, PRICES, at, buy_night, queue_sales


def test_spy_coin_and_all_three_overnight_holds_share_one_account_without_races(main_runtime, monkeypatch):
    r = main_runtime
    sep30, oct1 = date(2026, 9, 30), date(2026, 10, 1)
    h = MainOvernight(r, at(sep30, 15, 40), equity=50_000.0)

    # Controller durability needs a real advancing revision. This dry run substitutes the durable
    # store while every broker call still goes through the real AlpacaBroker and one MockTransport.
    def checkpoint(_reason, *_args):
        r.persistence_revision += 1
        return True

    monkeypatch.setattr(r, "_checkpoint_runtime", checkpoint)
    monkeypatch.setattr(r, "day_one_admission", lambda *_args: None)
    r.persistence_healthy = True
    r.day_one.build(
        h.broker,
        clock=h.clock,
        executor=InlineExecutor(),
        spy_reference=lambda _session: 500.0,
        coin_model=lambda _session: {
            "side": 1,
            "zmove": 0.02,
            "sigma": 0.01,
            "history_count": 20,
            "prior_low": 240.0,
            "close_two_back": 250.0,
            "prior_close": 250.0,
        },
    )

    # Sep 30 near close market buys established all three overnight holds.
    buy_night(h, sep30)
    assert set(h.alpaca.signed_positions()) == set(PRICES)
    queue_sales(h, sep30)

    # At 19:05 the new controller queues exactly one Oct 1 SPY opening buy.
    h.run(at(sep30, 19, 5, 5), every=1)
    spy_entries = [o for o in h.alpaca.live("SPY") if o["client_order_id"] == "adt-tom-spy-20261001-entry-1"]
    assert len(spy_entries) == 1 and spy_entries[0]["time_in_force"] == "opg"
    assert not h.alpaca.posts("COIN")

    # Midnight leaves overnight shares and the queued SPY intent under their own controllers.
    h.run(at(oct1, 9, 29, 50), every=300)
    assert r.day_one.claimed("COIN")
    assert set(h.alpaca.signed_positions()) == set(PRICES)

    # The same opening auction sells NVDA/IREN/HUT and buys SPY. Reconcile books every fill once.
    h.set(at(oct1, 9, 30))
    open_prices = {**PRICES, "SPY": 500.0}
    h.alpaca.open_auction(open_prices)
    h.run(at(oct1, 9, 30, 5), every=1)
    h.compare()
    assert not any(sym in h.alpaca.signed_positions() for sym in PRICES)
    assert h.alpaca.signed_positions().get("SPY") == 20
    assert r.account.positions["SPY"].strategy_id == d1.SPY_ID
    assert len([o for o in h.alpaca.live("SPY", "sell") if o["time_in_force"] == "cls"]) == 1

    # COIN decision is saved at 09:35, dispatched once at 09:36, then protected by a resting CLS.
    h.set(at(oct1, 9, 35))
    h.quote("COIN", 250.0)
    h.step()
    assert not h.alpaca.posts("COIN")
    h.set(at(oct1, 9, 36))
    h.quote("COIN", 250.0)
    h.run(at(oct1, 9, 36, 4), every=1)
    h.compare()
    assert h.alpaca.signed_positions().get("COIN") == 20
    assert r.account.positions["COIN"].strategy_id == d1.COIN_ID
    assert len([o for o in h.alpaca.live("COIN", "sell") if o["time_in_force"] == "cls"]) == 1

    # Generic 15:55 and 15:58 paths leave both positions to their broker-held close orders.
    h.run(at(oct1, 15, 59, 59), every=60)
    assert h.alpaca.signed_positions().get("SPY") == 20
    assert h.alpaca.signed_positions().get("COIN") == 20

    h.set(at(oct1, 16, 0))
    h.alpaca.close_auction({**PRICES, "SPY": 503.0, "COIN": 255.0})
    h.run(at(oct1, 16, 0, 10), every=1)
    h.compare()
    assert "SPY" not in h.alpaca.signed_positions() and "COIN" not in h.alpaca.signed_positions()
    assert "SPY" not in r.account.positions and "COIN" not in r.account.positions
    assert r.day_one.controller.state["lifecycles"]["SPY"]["released"] is True
    assert r.day_one.controller.state["lifecycles"]["COIN"]["released"] is True
    trades = list(r.pending_trade_records.values())
    assert {t["strategy_id"] for t in trades if t["strategy_id"] in d1.DAY_ONE_IDS} == d1.DAY_ONE_IDS
    assert r.broker_state["mismatch"] is False
