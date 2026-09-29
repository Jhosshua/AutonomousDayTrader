"""Failing tests for bugs found by the ORB dry run (docs/orb_replacement/DRY_RUN_REPORT.md). Not fixed here.

BUG 1 (P0): the controller reads OrbsFacade.macro_veto's answer the wrong way round.
  facade.py:350  macro_veto() -> (vetoed, reason): vetoed=True means the trade must NOT be placed.
  orb_execution.py:1558-1564  _late_macro() treats the first element as "ok" (True = place the order).
  The phase-2 FakeFacade (fakes.py:327, 366-368) encodes the controller's reading, so the unit tests pass.
  Effect in ORB_MODE=live: every pick the macro veto allows is dropped right before the POST
  ("macro veto before the order: " with an empty reason), and a pick it vetoes is sent to Alpaca.
  Shadow mode never reaches _late_macro, so the planned shadow morning cannot show it.
These tests call the REAL OrbsFacade.macro_veto (its flow.macro_refusals answer stubbed)."""
from backend.app.strategies.orbs import flow
from backend.app.strategies.orbs.facade import OrbsFacade
from backend.tests.unit.orb_execution.fakes import Harness, pick


def _real_macro_veto(h, monkeypatch, reason):
    monkeypatch.setattr(flow, "macro_refusals",
                        lambda picks, now=None, bars=None: {p["symbol"]: reason for p in picks})
    h.facade.macro_veto = lambda s, d, now: OrbsFacade.macro_veto(None, s, d, now)


def test_an_order_the_macro_veto_allows_is_sent(monkeypatch):
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    _real_macro_veto(h, monkeypatch, None)                  # SPY and the sector agree: no veto
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    posts = [r for r in h.alpaca.requests if r[0] == "POST"]
    assert out["ok"] and len(posts) == 1 and posts[0][2]["order_class"] == "bracket", out


def test_an_order_the_macro_veto_refuses_is_not_sent(monkeypatch):
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    _real_macro_veto(h, monkeypatch, "SPY is down -0.40% since 09:30, against a long")
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])
    assert not [r for r in h.alpaca.requests if r[0] == "POST"], out
