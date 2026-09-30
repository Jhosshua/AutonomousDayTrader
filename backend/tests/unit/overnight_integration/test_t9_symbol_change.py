# @steered SNARE-2 2026-09-30
"""T9 symbol change through the REAL backend.app.main and the fake Alpaca (plan section 4.6).

Overnight Alpaca turns the IREN hold (248 shares bought at 40) into a new symbol IRNX. The 09:00
check must either convert ADT's book first and then sell IRNX (confirmed), or sell nothing and
keep IREN reserved and unreleased with a needs look (unclear). Before this fix the robot sold the
new symbol at Alpaca, ADT refused to book it, and the night was released with ADT still holding
IREN."""
from datetime import date

from backend.app.core import overnight_schedule as osch
from backend.app.core.overnight_execution import HELD, SKIPPED, SOLD
from backend.tests.unit.overnight_integration.fakes import PRICES, MainOvernight, at, buy_night, queue_sales

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)
OPEN = {**PRICES, "IRNX": 82.0}


def _held_overnight(r):
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    pos = r.account.positions["IREN"]
    assert (pos.shares, pos.avg_entry_price, pos.strategy_id) == (248, 40.0, "overnight_iren")
    return h


def _alpaca_changes_the_symbol(h, old_rate: str, new_rate: str, new_shares: int) -> dict:
    """What Alpaca does overnight: IREN is gone, IRNX shares are there, the queued IREN sale is
    still resting and the announcement names the change."""
    h.alpaca.positions.pop("IREN")
    h.alpaca.positions["IRNX"] = {"qty": new_shares, "avg": 80.0}
    h.price("IRNX", 82.0)
    h.alpaca.corporate_actions = [{"ca_type": "split", "ca_sub_type": "reverse_split", "initiating_symbol": "IREN",
                                   "target_symbol": "IRNX", "old_rate": old_rate, "new_rate": new_rate,
                                   "ex_date": FRI.isoformat()}]
    sale = h.alpaca.live("IREN", "sell")
    assert len(sale) == 1 and sale[0]["time_in_force"] == "opg" and sale[0]["qty"] == "248"
    return sale[0]


def _open(h, until):
    h.run(at(FRI, 9, 29, 50), every=5)
    h.set(at(FRI, 9, 30))
    h.alpaca.open_auction(OPEN)
    h.run(until, every=5)


def test_confirmed_symbol_change_converts_the_book_then_sells_and_books_the_new_symbol(main_runtime):
    r = main_runtime
    h = _held_overnight(r)
    old_sale = _alpaca_changes_the_symbol(h, "2", "1", 124)          # one IRNX for two IREN
    h.run(at(FRI, 8, 59), every=900)
    h.run(at(FRI, 9, 1), every=5)

    # ADT's book was converted before the IRNX sale went out, and the two books agree again
    assert "IREN" not in r.account.positions
    pos = r.account.positions["IRNX"]
    assert (pos.shares, pos.avg_entry_price, pos.strategy_id, pos.side.value) == (124, 80.0, "overnight_iren", "LONG")
    assert osch.is_overnight(pos) and pos.market_price == 80.0
    assert h.alpaca.orders[old_sale["id"]]["status"] == "canceled"
    irnx_sales = h.alpaca.posts("IRNX")
    assert [(b["side"], b["qty"], b["time_in_force"], b["client_order_id"]) for b in irnx_sales] == [
        ("sell", "124", "opg", "adt-ovn-IRNX-20261001-sell-1")]
    h.compare()
    assert h.local() == h.alpaca.signed_positions() and r.broker_state["mismatch"] is False
    # the reservation moved to IRNX: no other strategy may touch it, IREN is free
    assert r.overnight.claimed("IRNX") and r.overnight.holds_symbol("IRNX") and not r.overnight.claimed("IREN")
    assert r.overnight.sale_due("IRNX") is not None
    n = h.night("IREN", THU)
    assert n["needs_look"] == [] and n["symbol_change"]["new_symbol"] == "IRNX" and not n["released"]

    _open(h, at(FRI, 9, 32))
    h.compare()
    assert h.local() == h.alpaca.signed_positions() == {} and r.broker_state["mismatch"] is False
    assert n["state"] == SOLD and n["released"] and not r.overnight.claimed("IRNX")
    trade = r.pending_trade_records["ovn_IREN_2026-10-01"]
    assert trade["quantity"] == 124 and trade["avg_entry_price"] == 80.0
    assert trade["realized_pnl"] == 124 * (82.0 - 80.0) == 248 * (41.0 - 40.0)
    assert [l["side"] for l in trade["fill_legs"]] == ["BUY", "SELL"]
    assert trade["overnight"]["symbol_change"]["old_qty"] == 248
    assert r.account.equity == 49_700.0 + 248.0 and r.overnight.realized_today() == 248.0


def test_unclear_symbol_change_sells_nothing_and_keeps_iren_reserved(main_runtime):
    r = main_runtime
    h = _held_overnight(r)
    old_sale = _alpaca_changes_the_symbol(h, "10", "1", 24)          # 24.8 owed, 24 paid: cash in lieu
    h.run(at(FRI, 8, 59), every=900)
    h.run(at(FRI, 9, 1), every=5)
    n = h.night("IREN", THU)
    assert n["needs_look"] == [osch.SYMBOL_CHANGE_UNCLEAR] and n["frozen"] == osch.SYMBOL_CHANGE_UNCLEAR
    assert h.alpaca.orders[old_sale["id"]]["status"] == "canceled"

    _open(h, at(FRI, 9, 40))
    # nothing sold automatically in either symbol, and ADT's book still holds the hold untouched
    assert h.alpaca.posts("IRNX") == []
    assert [b["side"] for b in h.alpaca.posts("IREN")] == ["buy", "sell"]             # Thursday's buy and 19:00 sale
    pos = r.account.positions["IREN"]
    assert (pos.shares, pos.avg_entry_price, pos.strategy_id) == (248, 40.0, "overnight_iren")
    assert "IRNX" not in r.account.positions and h.alpaca.signed_positions() == {"IRNX": 24}
    # the other holds sold normally; the only difference is the untouched unclear one, and it is
    # shown as a mismatch, not hidden
    h.compare()
    assert h.local() == {"IREN": 248} and h.alpaca.signed_positions() == {"IRNX": 24}
    assert r.broker_state["mismatch"] is True
    assert h.night("NVDA", THU)["state"] == SOLD and h.night("HUT", THU)["state"] == SOLD
    # never released: IREN stays claimed, the red unsold banner lists it, the needs look is shown
    assert n["state"] == HELD and not n["released"] and r.overnight.claimed("IREN")
    health = r.overnight.health()
    assert health["unsold_after_0931"] == ["IREN"]
    assert {"symbol": "IREN", "buy_date": THU.isoformat(), "reasons": [osch.SYMBOL_CHANGE_UNCLEAR]} in health["needs_look"]
    assert r.overnight.payload()["unsold_after_0931"] == ["IREN"]
    assert "ovn_IREN_2026-10-01" not in r.pending_trade_records

    # and it keeps tonight's IREN buy from going out while NVDA and HUT buy again
    buy_night(h, FRI)
    tonight = h.night("IREN", FRI)
    assert tonight["state"] == SKIPPED and tonight["reason"] == osch.EARLIER_HOLD_UNSOLD
    assert [b["side"] for b in h.alpaca.posts("IREN")] == ["buy", "sell"]
    assert n["state"] == HELD and not n["released"] and r.account.positions["IREN"].shares == 248
