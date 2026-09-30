# @steered SNARE-2 2026-09-30
"""Alpaca holds fewer shares than ADT's book, and no corporate action explains it, through the
REAL backend.app.main and the fake Alpaca (plan section 4.6, X12).

ADT's book holds 55 NVDA for overnight_nvda. At 09:00 Alpaca shows 40. The sale still runs (R2)
for the 40 Alpaca holds, never more, and is booked. The 15 left only in ADT's book must keep the
night unreleased, NVDA claimed, the needs look and the red unsold banner, and must stop Friday's
NVDA buy. Before this fix the night was released as soon as Alpaca was flat, and Friday's buy
went out on top of 15 NVDA that only ADT's book held."""
from datetime import date

from backend.app.core import overnight_schedule as osch
from backend.app.core.overnight_execution import SKIPPED, SOLD
from backend.tests.unit.overnight_integration.fakes import PRICES, MainOvernight, at, buy_night, queue_sales

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)


def test_alpaca_fewer_shares_sells_what_it_holds_and_keeps_nvda_unreleased(main_runtime):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    pos = r.account.positions["NVDA"]
    assert (pos.shares, pos.strategy_id) == (55, "overnight_nvda")
    h.alpaca.positions["NVDA"]["qty"] = 40            # 15 NVDA gone at Alpaca, nothing announced
    h.run(at(FRI, 8, 59), every=900)
    h.run(at(FRI, 9, 1), every=5)
    n = h.night("NVDA", THU)
    assert osch.SHARES_UNEXPLAINED in n["needs_look"]
    assert [(b["qty"], b["time_in_force"]) for b in h.alpaca.live("NVDA", "sell")] == [("40", "opg")]

    h.run(at(FRI, 9, 29, 50), every=5)
    h.set(at(FRI, 9, 30))
    h.alpaca.open_auction(PRICES)
    h.run(at(FRI, 9, 40), every=5)

    # the sale sold and booked the 40 Alpaca held, never more
    assert "NVDA" not in h.alpaca.signed_positions()
    assert [b["qty"] for b in h.alpaca.posts("NVDA") if b["side"] == "sell"][-1] == "40"
    pos = r.account.positions["NVDA"]
    assert (pos.shares, pos.strategy_id) == (15, "overnight_nvda")
    # not released while ADT's book holds the 15 Alpaca does not
    assert n["state"] == SOLD and not n["released"]
    assert r.overnight.claimed("NVDA") and r.overnight.holds_symbol("NVDA")
    assert osch.BOOK_MORE_THAN_ALPACA in n["needs_look"]
    detail = next(row["detail"] for row in n_log(h) if row.get("reason") == osch.BOOK_MORE_THAN_ALPACA)
    assert detail.startswith("The robot's book shows 15 more NVDA shares than Alpaca.")
    health = r.overnight.health()
    assert health["unsold_after_0931"] == ["NVDA"] and r.overnight.payload()["unsold_after_0931"] == ["NVDA"]
    look = next(x for x in health["needs_look"] if x["symbol"] == "NVDA")
    assert osch.BOOK_MORE_THAN_ALPACA in look["reasons"]
    # the difference is shown by the compare, not hidden
    h.compare()
    assert r.broker_state["mismatch"] is True
    assert r.broker_state["mismatch_detail"]["NVDA"] == {"bot": 15, "alpaca": 0}
    # IREN and HUT sold in full and were released
    assert h.night("IREN", THU)["released"] and h.night("HUT", THU)["released"]

    # Friday: no NVDA buy while the difference stands, the reason is logged
    nvda_posts = len(h.alpaca.posts("NVDA"))
    buy_night(h, FRI)
    tonight = h.night("NVDA", FRI)
    assert tonight["state"] == SKIPPED and tonight["reason"] == osch.BOOK_MORE_THAN_ALPACA
    assert any(row["event"] == "SKIP" and row.get("reason") == osch.BOOK_MORE_THAN_ALPACA
               for row in n_log(h, FRI))
    assert len(h.alpaca.posts("NVDA")) == nvda_posts
    assert not n["released"] and r.account.positions["NVDA"].shares == 15


def n_log(h, d=THU):
    return [row for row in h.ctl.state["log"] if row.get("symbol") == "NVDA" and row.get("buy_date") == d.isoformat()]
