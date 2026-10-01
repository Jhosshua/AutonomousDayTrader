"""Audit regressions run through the real runtime and a network-blocked fake broker."""
from datetime import date

import pytest

from backend.app.core import overnight_schedule as osch
from backend.tests.unit.overnight_integration.fakes import MainOvernight, at, buy_night, queue_sales

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)


@pytest.mark.parametrize('refuse', [False, True])
def test_split_books_before_replacing_sale_and_history_uses_adjusted_shares(main_runtime, monkeypatch, refuse):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.alpaca.positions['NVDA'] = {'qty': 550, 'avg': 18.0}
    h.price('NVDA', 18.5)
    h.alpaca.corporate_actions = [{'ca_type': 'split', 'ca_sub_type': 'stock_split',
        'initiating_symbol': 'NVDA', 'target_symbol': 'NVDA', 'old_rate': '1',
        'new_rate': '10', 'ex_date': FRI.isoformat()}]
    if refuse:
        monkeypatch.setattr(r.overnight, '_book_split', lambda ev: False)
    h.run(at(FRI, 9, 1), every=60)
    n = h.night('NVDA', THU)
    if refuse:
        assert r.account.positions['NVDA'].shares == n['held_qty'] == 55
        assert n['frozen'] == osch.BOOKING_REFUSED
        assert h.alpaca.live('NVDA', 'sell') == []
        return
    assert r.account.positions['NVDA'].shares == n['held_qty'] == 550
    h.set(at(FRI, 9, 30))
    h.alpaca.open_auction({'NVDA': 18.5, 'IREN': 40.0, 'HUT': 50.0})
    h.run(at(FRI, 9, 32), every=5)
    trade = r.pending_trade_records['ovn_NVDA_2026-10-01']
    assert trade['quantity'] == 550
    assert trade['avg_entry_price'] == 18.0
    assert trade['realized_pnl'] == 550 * (trade['avg_exit_price'] - trade['avg_entry_price'])


@pytest.mark.parametrize('pending, runs', [([], True), ([('k1', 'FLATTENING', {})], False),
                                           ([('k2', 'SESSION_BOUNDARY', {})], False)])
def test_startup_boundary_waits_for_any_pending_input(main_runtime, monkeypatch, pending, runs):
    """A stale FLATTENING replayed after the startup boundary would set EOD_FLAT on the new day."""
    r = main_runtime

    class Store:
        def list_pending_events(self):
            return list(pending)

    calls = []
    monkeypatch.setattr(r, 'state_store', Store())
    monkeypatch.setattr(r, '_check_session_boundary', lambda now: calls.append(now))
    monkeypatch.setattr(r.overnight, 'start', lambda broker: None)
    monkeypatch.setattr(r.overnight, 'reconcile', lambda now: None)
    r._startup_overnight(at(FRI, 9, 0))
    assert bool(calls) is runs
