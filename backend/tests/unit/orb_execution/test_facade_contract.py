"""The controller against the REAL OrbsFacade contracts (attack round P0-1: a fake that encoded the
controller's own reading of macro_veto hid an inverted veto). Signatures of the fake must match the real
facade, and the tuple meanings of recheck/macro_veto are exercised through the controller with the real
facade methods."""
import inspect

import pytest

from backend.app.strategies.orbs import flow, signals
from backend.app.strategies.orbs.facade import OrbsFacade
from backend.tests.unit.orb_execution.fakes import FakeFacade, Harness, at, pick
from backend.tests.unit.orbs._helpers import FakeHTTP


@pytest.fixture
def real(tmp_path):
    from backend.app.strategies.orbs import config

    def no_network(path, params):
        raise AssertionError(f"unexpected relay call {path}")
    fac = OrbsFacade(str(tmp_path / "orbs"), "https://relay.invalid", "t", http=FakeHTTP(no_network))
    yield fac
    config.set_exclude_symbols(())


@pytest.mark.parametrize("name", ["prep", "scan", "decide", "recheck", "macro_veto", "latest_trade", "absorption_poll"])
def test_the_fake_facade_has_the_real_signature(name):
    real = [p for p in inspect.signature(getattr(OrbsFacade, name)).parameters]
    fake = [p for p in inspect.signature(getattr(FakeFacade, name)).parameters]
    assert fake[:len(real)] == real or [p.split("_")[0] for p in fake] == [p.split("_")[0] for p in real], (real, fake)
    assert len(fake) == len(real), (real, fake)


class _Real:
    def __init__(self, fake, real, names):
        self._fake, self._real, self._names = fake, real, names

    def __getattr__(self, name):
        return getattr(self._real if name in self._names else self._fake, name)


def _run(h, real, monkeypatch, candle=None, flow_why=None, macro_why=None):
    monkeypatch.setattr(signals, "candle_refusal", lambda card, pick: candle)
    monkeypatch.setattr(flow, "flow_refusal", lambda card, pick: flow_why)
    monkeypatch.setattr(flow, "macro_refusals", lambda picks, now=None: {p["symbol"]: macro_why for p in picks})
    h.ctl.facade = _Real(h.facade, real, {"recheck", "macro_veto"})
    h.alpaca.prices["APP"] = 100.2
    return h.ctl.execute([pick("APP", "long", 100.0, 98.0)])


def brackets(h):
    return [r for r in h.alpaca.requests if r[0] == "POST" and r[2].get("order_class") == "bracket"]


def test_real_recheck_and_macro_clear_send_the_order(real, monkeypatch):
    h = Harness()
    out = _run(h, real, monkeypatch)
    assert out["ok"] and len(brackets(h)) == 1


@pytest.mark.parametrize("kw,expect", [({"candle": "red candle"}, "candle rule"), ({"flow_why": "delta against"}, "flow rule"),
                                       ({"macro_why": "SPY is down"}, "macro veto")])
def test_any_real_refusal_sends_nothing(real, monkeypatch, kw, expect):
    h = Harness()
    out = _run(h, real, monkeypatch, **kw)
    assert not out["ok"] and not brackets(h)
    assert expect in str(out.get("refused") or out.get("reason") or out.get("placed"))


def test_the_controller_never_enters_a_symbol_already_executed_today():
    h = Harness()
    h.alpaca.prices["APP"] = 100.2
    assert h.ctl.execute([pick("APP", "long", 100.0, 98.0)])["ok"]
    parent = h.alpaca.by_coid(h.pos()["coid"])
    h.alpaca.fill(h.alpaca.leg(parent["id"], "tp")["id"], price=101.85)       # the target closes it
    h.clock.set(at(9, 45))
    h.ctl.tick()
    assert h.pos()["status"] == "CLOSED" and not h.ctl.owns("APP")
    h.clock.set(at(9, 50))
    h.alpaca.prices["APP"] = 100.2
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)])      # even if the facade's filter let it through
    assert not out["ok"] and ("APP", "already executed today") in [tuple(x) for x in out["refused"]]
    assert len(brackets(h)) == 1
