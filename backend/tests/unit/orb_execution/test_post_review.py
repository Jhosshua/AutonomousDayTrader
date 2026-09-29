"""Regression cases from the review after the ORB production deployment. Fake broker only."""
import copy

import pytest

from backend.tests.unit.orb_execution.fakes import Harness, at, pick


def brackets(h):
    return [r for r in h.alpaca.requests if r[0] == "POST" and r[2].get("order_class") == "bracket"]


@pytest.mark.parametrize("delay_at", ["macro", "persist", "budget"])
def test_an_entry_delayed_until_the_cutoff_is_never_posted(delay_at):
    h = Harness()
    h.clock.set(at(10, 14, 59))
    h.alpaca.prices["APP"] = 100.2

    if delay_at == "macro":
        real = h.facade.macro_veto

        def macro(*args):
            h.clock.advance(2)
            return real(*args)
        h.facade.macro_veto = macro
    elif delay_at == "persist":
        def saved(st):
            if any(r.get("role") == "entry" for r in st["orders"].values()):
                h.clock.set(at(10, 15, 1))
        h.on_persist = saved
    else:
        real = h.ctl.budget.acquire

        def acquire(prio="normal"):
            if any(r.get("role") == "entry" for r in h.ctl.state["orders"].values()):
                h.clock.set(at(10, 15, 1))
            return real(prio)
        h.ctl.budget.acquire = acquire

    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not brackets(h), out
    assert not out["ok"] and "cutoff" in str(out)
    assert h.ctl.day_risk_used() == 0 and not h.ctl.owns("APP") and not h.reserved


def test_the_exact_cutoff_keeps_orbstraddles_inclusive_boundary():
    h = Harness()
    h.clock.set(at(10, 15))
    h.alpaca.prices["APP"] = 100.2
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert out["ok"], out
    assert len(brackets(h)) == 1


@pytest.mark.parametrize("changes_at", ["macro", "persist", "budget"])
def test_the_adt_gate_is_checked_again_after_slow_entry_work(changes_at):
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    blocked = [False]
    h.ctl.entry_gate = lambda sym: "BROKER_MISMATCH" if blocked[0] else None
    if changes_at == "macro":
        real = h.facade.macro_veto

        def macro(*args):
            blocked[0] = True
            return real(*args)
        h.facade.macro_veto = macro
    elif changes_at == "persist":
        def saved(st):
            if any(r.get("role") == "entry" for r in st["orders"].values()):
                blocked[0] = True
        h.on_persist = saved
    else:
        real = h.ctl.budget.acquire

        def acquire(prio="normal"):
            if any(r.get("role") == "entry" for r in h.ctl.state["orders"].values()):
                blocked[0] = True
            return real(prio)
        h.ctl.budget.acquire = acquire
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not brackets(h), out
    assert "BROKER_MISMATCH" in str(out) and not h.reserved


def test_quarantined_bad_rows_keep_orb_unready_across_another_restart():
    h = Harness()
    st = copy.deepcopy(h.ctl.to_state())
    st["positions"]["damaged"] = {"symbol": "APP"}
    h.ctl = h.build("live", st)
    assert not h.ctl.reconcile_on_startup()["ok"]
    assert h.ctl.to_state()["invalid_rows"]
    h.ctl = h.build("live", h.ctl.to_state())
    out = h.ctl.reconcile_on_startup()
    assert not out["ok"] and not h.ctl.ready, out
