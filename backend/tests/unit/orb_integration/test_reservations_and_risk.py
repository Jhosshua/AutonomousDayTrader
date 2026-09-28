"""Shared symbol reservation (plan 2.7, 8.3, 9.3) and account-level risk (plan 2.3, 2.4, 9.6)."""
import threading

import pytest

from backend.app.models.events import OrderSide, OrderType
from backend.app.strategies.swing_panic_dip import StagedSwingOrder  # noqa: F401  (staged swing entries)
from backend.tests.unit.orb_integration.harness import MainOrb, at, pick


def test_orb_refuses_a_symbol_any_other_adt_arm_has(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    now = h.clock.now
    orb = r.orb
    assert orb.adt_occupied("NVDA") is None
    # held by another arm
    r.account.apply_fill("f1", "NVDA", "BUY", 10, 100.0, 0.0, now, strategy_id="vwap_pullback")
    assert "holds" in orb.adt_occupied("NVDA")
    # a working order
    o = r.engine.create_order("AMD", OrderSide.BUY, OrderType.LIMIT, 5, limit_price=10.0, strategy_id="mean_reversion")
    r.engine.working_orders[o.id] = o
    assert "working order" in orb.adt_occupied("AMD")
    # staged swing entry and swing reservation
    r.swing_staged_order_manager.stage_buy(symbol="MU", target_notional=25000.0, daily_atr=2.0,
                                           signal_date=now.date(), reason="t")
    assert "swing" in orb.adt_occupied("MU")
    r.swing_reserved_symbols.add("LRCX")
    assert "swing" in orb.adt_occupied("LRCX")
    # a pending ADT bracket
    r.bracket_manager.create_bracket("brk_x", "COIN", "LONG", 5, 200.0, 196.0, strategy_id="news_momentum", timestamp=now)
    assert "bracket" in orb.adt_occupied("COIN")
    for sym in ("NVDA", "AMD", "MU", "LRCX", "COIN"):
        assert orb.reserve(sym) is False and orb.is_occupied(sym)
    # the controller refuses such a pick with ORBStraddle's "occupied" reason
    h.clock.set(at(9, 39))
    h.alpaca.prices["NVDA"] = 100.2
    out = h.ctl.execute([pick("NVDA", "long", 100.0, 98.0)], h.clock.now)
    assert not out["ok"] and ("NVDA", "already held by this account") in [tuple(x) for x in out["refused"]]


def test_orb_does_not_count_its_own_position_as_occupied(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    assert r.account.positions["APP"].strategy_id == "orb"
    assert r.orb.adt_occupied("APP") is None          # ORB checks its own book itself
    assert r.orb.owns("APP")


def test_adt_arms_refuse_symbols_orb_holds_or_reserved_with_orb_owned(main_runtime):
    r = main_runtime
    MainOrb(r)
    assert r.orb.reserve("PLTR") is True               # reserved, not filled yet
    buy = r.engine.create_order("PLTR", OrderSide.BUY, OrderType.MARKET, 10, estimated_price=50.0, stop_price=49.0,
                                strategy_id="vwap_pullback")
    ok, why = r.pre_trade_risk_validator(buy, r.account)
    assert not ok and why.startswith("ORB_OWNED")
    swing = r.engine.create_order("PLTR", OrderSide.BUY, OrderType.MARKET, 10, estimated_price=50.0, stop_price=45.0,
                                  strategy_id="swing_panic_dip", arm=r.TradingArm.SWING)
    ok, why = r.pre_trade_risk_validator(swing, r.account)
    assert not ok and why.startswith("ORB_OWNED")
    r.orb.release("PLTR")
    ok, why = r.pre_trade_risk_validator(buy, r.account)
    assert not why.startswith("ORB_OWNED")


def test_cross_arm_race_exactly_one_wins(main_runtime):
    """Blocking test 8.10: ORB's reserve() on a worker thread and an ADT entry admission on another
    thread go for the same symbol at the same instant, 200 times: exactly one wins every time."""
    r = main_runtime
    MainOrb(r)
    for i in range(200):
        sym = f"RACE{i}"
        order = r.engine.create_order(sym, OrderSide.BUY, OrderType.MARKET, 1, estimated_price=10.0, stop_price=9.9,
                                      strategy_id="news_momentum")
        order.status = order.status.__class__.SUBMITTED          # what submit_order sets before validating
        barrier = threading.Barrier(2)
        out = {}

        def orb_side():
            barrier.wait()
            out["orb"] = r.orb.reserve(sym)

        def adt_side():
            barrier.wait()
            out["adt"] = r.orb.claim_for_adt(sym, order, "news_momentum") is None

        ts = [threading.Thread(target=orb_side), threading.Thread(target=adt_side)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert out["orb"] != out["adt"], (i, out)
        r.orb.release(sym)


def test_an_adt_entry_in_flight_blocks_orb_until_it_resolves(main_runtime):
    r = main_runtime
    MainOrb(r)
    order = r.engine.create_order("SOFI", OrderSide.BUY, OrderType.MARKET, 1, estimated_price=10.0,
                                  stop_price=9.9, strategy_id="news_momentum")
    order.status = order.status.__class__.SUBMITTED
    assert r.orb.claim_for_adt("SOFI", order, "news_momentum") is None
    assert r.orb.reserve("SOFI") is False
    order.status = order.status.__class__.REJECTED              # the entry died: the claim is stale
    assert r.orb.reserve("SOFI") is True


def test_orb_positions_do_not_count_toward_adts_three_position_cap(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    syms, sectors, count, notional = r._get_effective_committed_portfolio(r.account, arm=r.TradingArm.INTRADAY)
    assert "APP" not in syms and count == 0
    assert r._get_effective_committed_portfolio(r.account)[2] == 0


def test_orb_open_risk_is_reserved_out_of_adts_daily_loss_budget(main_runtime):
    """Plan 9.6: ORB's open + pending risk comes out of ADT's remaining loss budget before other arms size."""
    r = main_runtime
    h = MainOrb(r)
    limit = r.risk_engine.config.hard_max_daily_loss_dollars
    assert limit == pytest.approx(1250.0)                         # min($1,500, 2.5% of $50k)
    def authorized():
        res = r.risk_engine.evaluate_order_request(
            symbol="MSFT", side="BUY", requested_qty=10000, entry_price=40.0, stop_price=39.0,
            account_equity=r.account.equity, buying_power=r.account.buying_power, active_positions_count=0,
            active_symbols=set(), active_sectors=set(), strategy_id="news_momentum")
        return res
    before = authorized()
    assert before.approved and before.authorized_qty == 500       # $500 = 1% risk / $1 stop
    h.open_bracket()                                              # ORB: 454 sh, 2.20 from the stop -> $998.80
    assert r.orb.open_risk() == pytest.approx(454 * 2.2, abs=0.01)
    after = authorized()
    assert after.approved and after.authorized_qty == 251         # 1250 - 998.80 = $251.20 left
    # a pending (not yet filled) ORB entry counts with its planned shares
    h.alpaca.entry_mode = "new"
    h.alpaca.prices["PLTR"] = 50.1
    h.ctl.cfg["max_structure_picks"] = 4
    h.ctl.execute([pick("PLTR", "long", 50.0, 49.5, tier="quant")], h.clock.now)
    assert r.orb.open_risk() > 454 * 2.2
    # an unreadable ORB risk fails closed (no budget for anyone)
    r.risk_engine.reserved_risk_fn = lambda: float("nan")
    try:
        assert not authorized().approved
    finally:
        r.risk_engine.reserved_risk_fn = r.orb.open_risk


def test_adt_entry_gate_applies_to_orb_entries(main_runtime):
    """ADT's _broker_gate rules for ORB entries: regular hours only, no broker mismatch, healthy ledger."""
    r = main_runtime
    h = MainOrb(r)
    h.clock.set(at(9, 39))
    h.alpaca.prices["APP"] = 100.2
    r.broker_state["mismatch"] = True
    out = h.ctl.execute([pick("APP", "long", 100.0, 98.0)], h.clock.now)
    assert not out["ok"]
    assert any("BROKER_MISMATCH" in str((p.get("result") or {}).get("error")) for p in out.get("placed", []))
    assert not [q for q in h.alpaca.requests if q[0] == "POST"]
    r.broker_state["mismatch"] = False
    assert r.orb.entry_gate("APP") is None
    h.clock.set(at(9, 20))
    assert "MARKET_CLOSED" in r.orb.entry_gate("APP")
    h.clock.set(at(9, 40).replace(day=26))                        # Saturday
    assert "MARKET_CLOSED" in r.orb.entry_gate("APP")


def test_orb_bypasses_adts_allocation_cap_and_stop_band(main_runtime):
    """ORB sizes exactly like ORBStraddle (2% of day-start equity): a $45k position on a $50k account,
    far above ADT's $12.5k allocation cap and its 3-position/0.4-4% stop rules, is placed."""
    r = main_runtime
    h = MainOrb(r)
    h.open_bracket()
    pos = r.account.positions["APP"]
    assert pos.shares * pos.avg_entry_price > 12500 * 3
