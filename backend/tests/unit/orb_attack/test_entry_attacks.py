"""Attack: make an ORB ENTRY go out when it must not (breaker, flatten-all, macro veto, wrong account,
shadow), go out twice, or leave a symbol/slot/risk stuck. Fake Alpaca only."""
import threading
from datetime import timedelta

import pytest

from backend.app.strategies.orbs import flow
from backend.app.strategies.orbs.facade import OrbsFacade
from backend.tests.unit.orb_attack._util import (
    SwitchBudget, at, bracket_posts, live_stop_at_broker, opened, pick, writes_after,
)
from backend.tests.unit.orb_execution.fakes import Harness
from backend.tests.unit.orbs._helpers import FakeHTTP


class _RealMacro:
    """The scripted facade, except macro_veto, which is the REAL OrbsFacade.macro_veto."""

    def __init__(self, fake, real):
        self._fake, self._real = fake, real

    def __getattr__(self, name):
        return getattr(self._fake, name)

    def macro_veto(self, symbol, direction, now):
        return self._real.macro_veto(symbol, direction, now)


def _no_network(path, params):
    raise AssertionError(f"unexpected relay call {path}")


@pytest.fixture
def real_facade(tmp_path):
    from backend.app.strategies.orbs import config
    fac = OrbsFacade(str(tmp_path / "orbs"), "https://relay.invalid", "t", http=FakeHTTP(_no_network))
    yield fac
    config.set_exclude_symbols(())


def test_real_facade_macro_clear_pick_is_sent(real_facade, monkeypatch):
    monkeypatch.setattr(flow, "macro_refusals",
                        lambda picks, now=None: {p["symbol"]: None for p in picks})   # SPY/sector: no veto
    assert real_facade.macro_veto("APP", "long", at(9, 39)) == (False, "")          # facade: (vetoed, why)
    h = Harness()
    h.ctl.facade = _RealMacro(h.facade, real_facade)
    h.alpaca.prices["APP"] = 100.2
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out["ok"] and len(bracket_posts(h)) == 1, out


def test_real_facade_macro_vetoed_pick_is_never_sent(real_facade, monkeypatch):
    monkeypatch.setattr(flow, "macro_refusals",
                        lambda picks, now=None: {p["symbol"]: "SPY is down 0.30% since the decision" for p in picks})
    assert real_facade.macro_veto("APP", "long", at(9, 39))[0] is True               # facade: vetoed
    h = Harness()
    h.facade.recheck_result = (True, "")          # the veto appears between the re-check and the POST
    h.ctl.facade = _RealMacro(h.facade, real_facade)
    h.alpaca.prices["APP"] = 100.2
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not bracket_posts(h), "a macro-vetoed pick was sent"


# ----------------------------------------------------------------------------- breaker / flatten-all mid-execute
def test_breaker_trip_right_before_the_post_blocks_the_entry():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    real = h.facade.macro_veto

    def breaker_trips_now(*a):
        h.halt[0] = "ADT's daily loss stop was hit"
        return real(*a)
    h.facade.macro_veto = breaker_trips_now
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not bracket_posts(h), "entry POST sent after ADT's daily loss stop tripped"


def test_flatten_all_during_execute_blocks_the_pending_entry():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    real = h.facade.macro_veto

    def operator_flattens_all(*a):
        h.ctl.request_all_exits("MANUAL_FLATTEN_ALL", block_entries=True)
        return real(*a)
    h.facade.macro_veto = operator_flattens_all
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not bracket_posts(h), "entry POST sent after the operator's flatten-all"


def test_flatten_all_racing_a_supervisor_pass_and_an_execute_ends_flat():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2

    def gate(sym):                       # runs on the execute thread just before the POST (sym lock free)
        h.ctl.request_all_exits("MANUAL_FLATTEN_ALL", block_entries=True)
        h.ctl.tick()                     # the supervisor worker's pass lands here
        return None
    h.ctl.entry_gate = gate
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    h.ctl.entry_gate = None
    for _ in range(4):
        h.clock.advance(5)
        h.ctl.tick()
    assert h.alpaca._pos_qty("APP") == 0, "position opened after flatten-all and kept"


def test_account_halt_already_set_refuses_before_any_read_or_write():
    h = Harness()
    h.halt[0] = "ADT's daily loss stop was hit"
    n = len(h.alpaca.requests)
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not out["ok"] and not writes_after(h, n)


# ----------------------------------------------------------------------------- double orders
def test_many_threads_executing_the_same_pick_send_one_bracket():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    barrier = threading.Barrier(6)
    outs = []

    def go():
        barrier.wait()
        outs.append(h.ctl.execute([pick("APP", "long", 100.0, 98.0)]))
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(bracket_posts(h)) == 1
    assert h.ctl.own_qty("APP") == 454 and h.own_fill_sum() == 454


def test_many_threads_ticking_during_an_exit_send_one_close():
    h = opened()
    h.alpaca.prices["APP"] = 99.30
    h.clock.advance(5)
    barrier = threading.Barrier(6)

    def go():
        barrier.wait()
        h.ctl.tick()
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    h.clock.advance(5)
    h.ctl.tick()
    sells = [o for o in h.alpaca.orders.values() if o["client_order_id"].startswith("adt-orb-X-")]
    assert len(sells) == 1 and h.alpaca._pos_qty("APP") == 0 and h.alpaca.refused_403 == []


def test_entry_5xx_storm_never_resubmits_and_blocks_other_entries_until_resolved():
    h = Harness()
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    h.alpaca.fail += [{"method": "POST", "path": "/v2/orders", "kind": "status", "status": 503},
                      {"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status", "status": 503,
                       "times": 12}]
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out.get("unknown") == ["APP"] and h.ctl.owns("APP"), out
    h.alpaca.fail.append({"method": "GET", "path": "/v2/positions/", "kind": "status", "status": 503, "times": 12})
    out2 = h.ctl.execute([pick("PLTR", "long", 50.0, 48.9, tier="quant")])
    assert not bracket_posts(h, 0)[1:], out2          # the storm keeps a second entry out (occupancy unreadable)
    for _ in range(30):
        h.clock.advance(5)
        h.ctl.tick()
    assert len([b for b in bracket_posts(h) if b["symbol"] == "APP"]) == 1
    assert not h.alpaca.positions.get("APP") and not h.ctl.owns("APP")


def test_excluded_tsla_top_pick_is_refused_and_the_next_pick_is_sent():
    h = Harness()
    h.alpaca.prices.update(TSLA=250.5, APP=100.2)
    out = h.ctl.execute([pick("TSLA", "long", 250.0, 245.0), pick("APP", "long", 100.0, 98.0)])
    assert [b["symbol"] for b in bracket_posts(h)] == ["APP"]
    assert ("TSLA", "excluded from ORB (another ADT plan trades it)") in [tuple(x) for x in out["refused"]]


def test_budget_exhausted_for_an_entry_sends_nothing_and_frees_the_symbol():
    h = Harness(reconcile=False)
    budget = SwitchBudget()
    h.ctl.budget = budget
    assert h.ctl.reconcile_on_startup()["ok"]
    h.alpaca.prices["APP"] = 100.2
    real = h.facade.macro_veto

    def throttle_now(*a):
        budget.exhausted = True
        return real(*a)
    h.facade.macro_veto = throttle_now
    h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not bracket_posts(h) and not h.ctl.owns("APP") and "APP" not in h.reserved
    assert h.ctl.day_risk_used() == 0


# ----------------------------------------------------------------------------- account / shadow
def test_account_pin_mismatch_mid_trade_touches_nothing_and_refuses_entries():
    h = opened()
    h.alpaca.account["account_number"] = "PA3RPSMUR65S"          # credentials now reach ORBStraddle's account
    h.clock.advance(61)                                          # the destination cache has expired
    n = len(h.alpaca.requests)
    h.alpaca.prices["APP"] = 99.30
    h.ctl.tick()
    assert not writes_after(h, n) and live_stop_at_broker(h)
    h.alpaca.prices["PLTR"] = 50.1
    out = h.ctl.execute([pick("PLTR", "long", 50.0, 48.9, tier="quant")])
    assert not out["ok"] and "destination refused" in out["reason"] and not writes_after(h, n)


def test_shadow_mode_with_a_broker_never_writes_even_with_adt_exit_requests():
    h = Harness(mode="shadow")
    h.alpaca.prices["APP"] = 100.2
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out["ok"] and out["dry_run"]
    h.ctl.request_all_exits("CIRCUIT_BREAKER")
    h.ctl.request_exit("APP", "MANUAL_FLATTEN")
    for _ in range(5):
        h.clock.advance(5)
        h.ctl.tick()
    h.clock.set(at(11, 0))
    h.ctl.tick()
    assert not h.alpaca.writes


# ----------------------------------------------------------------------------- stuck symbol / slot / risk
def test_kill_between_intent_and_order_record_frees_the_slot_symbol_and_risk():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    calls = []

    def die_after_intent(state):
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt("process killed after the intent write")
    h.on_persist = die_after_intent
    with pytest.raises(KeyboardInterrupt):
        h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    h.on_persist = None
    assert not h.alpaca.writes                                   # nothing was sent: that part is safe
    ctl = h.restart()
    assert ctl.reconcile_on_startup()["ok"]
    for _ in range(3):
        h.clock.advance(5)
        ctl.tick()
    assert not ctl.owns("APP") and ctl.day_risk_used() == 0 and ctl.slots_available() == 4


def test_entry_hidden_from_client_id_lookups_is_never_released_while_alpaca_holds_it():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    h.alpaca.fail += [{"method": "POST", "path": "/v2/orders", "kind": "lost"},
                      {"method": "GET", "path": "/v2/orders:by_client_order_id", "kind": "status", "status": 404,
                       "times": 40}]
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out["unknown"] == ["APP"]
    for _ in range(14):                 # 70 s: past the 60 s grace, still hidden
        h.clock.advance(5)
        h.ctl.tick()
    assert h.alpaca._pos_qty("APP") == 454
    assert h.ctl.owns("APP") and "APP" in h.reserved and h.ctl.day_risk_used() > 0


def test_a_pending_entry_closed_by_a_supervisor_pass_is_never_sent():
    """A one-symbol exit request (no day block) lands while the entry is between its plan and its POST;
    the supervisor closes the still-pending position. The POST must not go out afterwards."""
    h = Harness()
    h.alpaca.prices["APP"] = 100.2

    def gate(sym):
        h.ctl.request_exit("APP", "MANUAL_FLATTEN")
        h.ctl.tick()
        return None
    h.ctl.entry_gate = gate
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    h.ctl.entry_gate = None
    assert not bracket_posts(h) and h.ctl.entries_blocked() is None
    assert "no longer pending" in str(out)
