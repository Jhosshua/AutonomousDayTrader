"""Attack the decision inputs (empty / one-sided / data-less boards through the REAL copied decision code)
and the scheduler's restart points (kill at every step, including mid-execution)."""
import tempfile
from datetime import date, datetime

import pytest

from backend.app.core.orb_scheduler import OrbScheduler
from backend.app.strategies.orbs import adaptive, config
from backend.app.strategies.orbs.facade import OrbsFacade
from backend.tests.unit.orb_execution.fakes import MANIFEST, Harness, at, pick
from backend.tests.unit.orbs._helpers import FakeHTTP

D = "2026-09-28"


def _relay_down(path, params):
    raise OSError("relay unreachable")


@pytest.fixture
def fac(tmp_path):
    f = OrbsFacade(str(tmp_path / "orbs"), "https://relay.invalid", "t", http=FakeHTTP(_relay_down))
    yield f
    config.set_exclude_symbols(())


def _cards(longs, shorts):
    return ([{"symbol": f"L{i}", "direction": "long"} for i in range(longs)]
            + [{"symbol": f"S{i}", "direction": "short"} for i in range(shorts)])


@pytest.mark.parametrize("longs,shorts", [(4, 0), (0, 4), (9, 1), (1, 9)])
def test_one_sided_board_sits_out(fac, longs, shorts):
    reg = adaptive.evaluate_market_regime(D, "09:38", cards=_cards(longs, shorts), spy_slope=1.0, qqq_slope=1.0)
    assert reg["classification"] == "ONE_SIDED" and reg["action"] == "SIT_OUT_CASH"


def test_balanced_board_trades_only_with_index_data(fac):
    ok = adaptive.evaluate_market_regime(D, "09:38", cards=_cards(2, 2), spy_slope=1.0, qqq_slope=1.0)
    assert ok["action"] == "TRADE_NORMAL"
    down = adaptive.evaluate_market_regime(D, "09:38", cards=_cards(2, 2))      # relay down: no SPY/QQQ bars
    assert down["action"] == "SIT_OUT_CASH" and any("unavailable" in r for r in down["reasons"])


def test_empty_board_sits_out_and_is_never_decided(fac):
    reg = adaptive.evaluate_market_regime(D, "09:38", cards=[], spy_slope=1.0, qqq_slope=1.0)
    assert reg["action"] == "SIT_OUT_CASH"
    out = fac.decide(date(2026, 9, 28), {"ok": True, "wave": "primary", "cards": [], "health": {}}, "primary",
                     at(9, 39), set(), executed_today=set())
    assert out["verdict"] == "refused" and out["picks"] == []


def test_a_board_that_is_not_the_last_successful_scan_is_refused(fac):
    board = {"ok": True, "wave": "primary", "cards": [{"symbol": "APP", "direction": "long", "entry": 100.0,
                                                        "stop": 98.0}],
             "end": "09:38", "board_id": "forged", "health": {"day": D, "attempted": 10, "ok": 10, "failed": 0,
                                                             "end": "09:38", "source": config.CARD_SOURCE,
                                                             "cards": 1}}
    out = fac.decide(date(2026, 9, 28), board, "primary", at(9, 39), set(), executed_today=set())
    assert out["verdict"] == "refused" and "last successful" in out["reason"]


# ----------------------------------------------------------------------------- scheduler kill points
def _make(h, states):
    return OrbScheduler(h.ctl, h.facade, MANIFEST, clock=h.clock, is_session=lambda d: True,
                        persist_cb=states.append, inline=True, monotonic=lambda: h.clock.now.timestamp())


def _board(*syms, board_id=None):
    return {"ok": True, "error": None, "coverage": 1.0, "cards": [{"symbol": s, "direction": "long"} for s in syms],
            "board_id": board_id or "-".join(syms)}


def _run(s, h, start, end, step=5):
    h.clock.set(start)
    while h.clock.now <= end:
        s.tick()
        h.clock.advance(step)


def test_kill_while_the_primary_orders_are_being_sent_never_sends_them_twice():
    h = Harness(mode="live", start=at(9, 0), freeze=False)
    h.alpaca.prices.update(APP=100.2, PLTR=50.1)
    h.facade.scan_results = [_board("APP", "PLTR"), _board("APP", "PLTR", board_id="final"),
                             _board("APP", "PLTR", board_id="final2")]
    h.facade.decide_results = [{"verdict": "trade", "reason": None, "audit": [], "regime": {},
                                "picks": [pick("APP", "long", 100.0, 98.0),
                                          pick("PLTR", "long", 50.0, 48.9, tier="quant")]}] * 2
    states = []
    s = _make(h, states)
    real = h.ctl.execute
    snap = {}

    def die_mid_execute(picks, now=None, strict=True, wave=None):
        snap["sched"] = s.to_state()                      # durable: the claim ("sending the orders")
        h.ctl.execute = real
        out = real(picks[:1], now, strict, wave)          # APP goes out ...
        snap["ctl"] = h.ctl.to_state()
        raise KeyboardInterrupt("killed before PLTR")     # ... then the process dies
    h.ctl.execute = die_mid_execute
    with pytest.raises(KeyboardInterrupt):
        _run(s, h, at(9, 0), at(9, 39))
    assert snap["sched"]["steps"]["primary_decision"]["state"] == "running"
    ctl = h.build("live", snap["ctl"])
    h.ctl = ctl
    s2 = _make(h, states)
    s2.from_state(snap["sched"])
    assert s2.state["steps"]["primary_decision"]["state"] == "interrupted"
    _run(s2, h, at(9, 39, 5), at(9, 44))
    syms = [b["symbol"] for m, p, b, _q in h.alpaca.requests if m == "POST" and b.get("order_class") == "bracket"]
    assert syms == ["APP"]                                 # APP once, PLTR never, no second primary decision
    assert len([c for c in h.facade.calls if c[0] == "decide"]) == 1
    assert ctl.own_qty("APP") == 454


def test_restart_at_every_step_never_decides_twice():
    for kill_at in (at(9, 15, 30), at(9, 36, 15), at(9, 38, 35), at(9, 39, 30), at(9, 50), at(10, 20)):
        h = Harness(mode="live", start=at(9, 0), freeze=False)
        h.alpaca.prices["APP"] = 100.2
        h.facade.scan_results = [_board("APP"), _board("APP", board_id="final"), _board("APP", board_id="final")]
        h.facade.decide_results = [{"verdict": "trade", "reason": None, "audit": [], "regime": {},
                                    "picks": [pick("APP", "long", 100.0, 98.0)]}] * 3
        states = []
        s = _make(h, states)
        _run(s, h, at(9, 0), kill_at)
        ctl = h.build("live", h.ctl.to_state())
        h.ctl = ctl
        s2 = _make(h, states)
        s2.from_state(states[-1] if states else None)
        _run(s2, h, kill_at, at(10, 30))
        brackets = [b for m, p, b, _q in h.alpaca.requests if m == "POST" and b.get("order_class") == "bracket"]
        primaries = [c for c in h.facade.calls if c[0] == "decide" and c[3] == "primary"]
        assert len(brackets) <= 1 and len(primaries) <= 1, (kill_at, len(brackets), len(primaries))
