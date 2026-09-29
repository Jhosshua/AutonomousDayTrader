"""Holding card + market mood (PLAN_2026_09_29_holding_card_and_market_mood.md).

Display only: these check the recorded context is right AND that a bug in it can never change an order,
a bracket or a checkpoint. Live state is never touched: simulation mode, no broker, tmp SQLite only."""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, time as dtime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import create_model

from backend.app.core import research as research_mod
from backend.app.core.account import Position, PositionSide
from backend.app.core.bracket import BracketOrder, BracketStatus
from backend.app.core.market_filter import MarketTrend
from backend.app.core.persistence import TradingStateStore, decode_runtime_value, encode_runtime_value
from backend.app.core.trading_windows import PHASES
from backend.app.models.events import (
    OrderSide, OrderType, VIX_REGIME_BOUNDARIES, VIX_REGIME_SIZING_MULTIPLIERS, VIX_REGIME_STOP_MULTIPLIERS,
)
from backend.app.strategies.adaptation import DynamicAdaptationEngine, calculate_position_size, get_time_of_day_phase
from backend.app.strategies.base import SignalEvent
from backend.app.strategies.tri_engine import TRI_RISK_PCT
from backend.tests.unit.test_tri_broker_lifecycle import arm, tri_paper  # noqa: F401  (fixture + helper)

T0 = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)  # 10:00 ET

CONTEXT_KEYS = {
    "vix", "vix_regime", "vix_stale", "vix_age_seconds", "sizing_multiplier", "stop_multiplier", "time_phase",
    "time_multiplier", "market_status", "market_trend", "market_trend_reason", "max_concurrent_positions",
    "notional_cap_pct", "base_risk_pct", "midday", "vix_tiers", "adaptive_strategies",
}
POSITION_KEYS = {"entry_context", "initial_stop", "bracket_status", "runner_policy", "target_1_filled",
                 "orb_context", "plan_risk_pct", "r_multiple"}


def _signal(entry=100.0, stop=98.5, side=OrderSide.BUY, strategy="news_momentum", sym="AAPL", minute=0):
    d = 1 if side == OrderSide.BUY else -1
    sig = SignalEvent(symbol=sym, side=side, order_type=OrderType.MARKET, entry_price=entry, stop_loss=stop,
                      take_profit_1=entry + d, take_profit_2=entry + 2 * d, strategy_id=strategy,
                      confidence=0.9, reason="HOLDING_CONTEXT_TEST", timestamp=T0 + timedelta(minutes=minute))
    sig.features = {}
    return sig


class _Filter:
    """A market filter that says BULLISH and permits everything (the real one is tested elsewhere)."""
    def get_current_trend(self, asof=None):
        return MarketTrend.BULLISH, "OK"

    def is_signal_permitted(self, **_kw):
        return True, "APPROVED: aligned with the market"


@pytest.fixture
def rt(monkeypatch):
    import backend.app.main as runtime
    monkeypatch.setattr(runtime.adaptation_engine, "market_filter", _Filter())
    eng = runtime.adaptation_engine
    saved = {k: getattr(eng, k) for k in ("current_vix", "current_vix_regime", "current_sizing_multiplier",
                                          "current_stop_multiplier", "current_time_phase", "last_update")}
    runtime.reset_runtime_state()
    runtime.set_simulation_mode(True)
    runtime.latest_market_prices["AAPL"] = 100.0
    runtime.adaptation_engine.on_vix_print(SimpleNamespace(value=30.0, received_at=T0))   # ELEVATED: 0.7 / 1.4
    runtime.adaptation_engine.current_time_phase = "MIDDAY_CHOP"
    yield runtime
    runtime.reset_runtime_state()
    runtime.set_simulation_mode(False)
    for k, v in saved.items():
        setattr(eng, k, v)


def _admit(rt, **kw):
    asyncio.run(rt.execute_strategy_signal(_signal(**kw)))
    bid = rt.bracket_manager.symbol_to_bracket.get(kw.get("sym", "AAPL"))
    return rt.bracket_manager.brackets.get(bid) if bid else None


def _entry_orders(rt):
    return [o for o in rt.engine.orders.values() if getattr(o, "bracket_role", None) is None]


# 1 ----------------------------------------------------------------------------------------------
def test_admission_records_entry_context_from_engine_values_wide_stop_risk_binds(rt):
    b = _admit(rt, entry=100.0, stop=98.5)
    ctx = b.entry_context
    assert ctx is not None
    eq = rt.account.equity
    assert ctx["vix"] == 30.0 and ctx["vix_regime"] == "ELEVATED"
    assert ctx["sizing_multiplier"] == 0.70 and ctx["stop_multiplier"] == 1.40
    assert ctx["time_phase"] == "MIDDAY_CHOP" and ctx["time_multiplier"] == 0.5
    assert ctx["market_trend"] == "BULLISH" and ctx["trend_reason"] == "APPROVED"
    assert ctx["decided_at"] == (T0 + timedelta(minutes=1)).isoformat()
    assert ctx["stop_raw"] == 98.5 and ctx["stop_adapted"] == pytest.approx(b.initial_stop_price)
    stop_dist = abs(100.0 - ctx["stop_adapted"])
    assert ctx["qty_adaptation"] == math.floor(eq * 0.01 * 0.7 * 0.5 / stop_dist)   # risk binds, not the cap
    assert ctx["qty_final"] == b.total_qty == ctx["qty_adaptation"]
    assert ctx["qty_if_neutral"] == calculate_position_size(eq, 100.0, ctx["stop_adapted"], 0.01, 0.25, 1.0)
    assert ctx["qty_final"] < ctx["qty_if_neutral"]
    assert ctx["size_limited_by"] == "risk"
    assert json.loads(json.dumps(ctx)) == ctx           # plain JSON


def test_admission_tight_stop_cap_binds_so_sizes_are_equal(rt):
    b = _admit(rt, entry=100.0, stop=99.9)
    ctx = b.entry_context
    eq = rt.account.equity
    assert ctx["size_limited_by"] == "notional_cap"
    assert ctx["qty_final"] == ctx["qty_if_neutral"] == math.floor(eq * 0.25 / 100.0)


def test_admission_account_limits_are_named_when_the_risk_engine_trims(rt, monkeypatch):
    real = rt.risk_engine.evaluate_order_request
    calls = []

    def trimmed(**kw):
        p = real(**kw)
        if not calls:                                     # only main.py's admission check; the engine's own stays real
            p.authorized_qty = max(1, int(kw["requested_qty"]) // 2)
        calls.append(1)
        return p
    monkeypatch.setattr(rt.risk_engine, "evaluate_order_request", trimmed)
    b = _admit(rt, entry=100.0, stop=98.5)
    ctx = b.entry_context
    assert ctx["qty_final"] < ctx["qty_adaptation"] and ctx["size_limited_by"] == "account_limits"


# 2 ----------------------------------------------------------------------------------------------
def test_entry_context_that_raises_never_touches_the_order_or_bracket(rt, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("context exploded")
    monkeypatch.setattr(rt, "_build_entry_context", boom)
    b = _admit(rt)
    assert b is not None and b.entry_context is None
    assert len(_entry_orders(rt)) == 1
    order = _entry_orders(rt)[0]
    assert rt.entry_order_to_bracket[order.id] == b.bracket_id
    assert b.total_qty > 0 and b.status == BracketStatus.PENDING_ENTRY


# 3 ----------------------------------------------------------------------------------------------
def test_unencodable_value_in_context_still_leaves_the_checkpoint_healthy(rt, monkeypatch, tmp_path):
    # json_safe is the first line of defence; make it let an unencodable object through so the JSON
    # round trip (the last step) is what stands between it and the checkpoint.
    monkeypatch.setattr(research_mod, "json_safe", lambda v, depth=0: {"bad": object(), "nan": float("nan")})
    b = _admit(rt)
    assert b is not None and b.entry_context is None
    store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    try:
        monkeypatch.setattr(rt, "state_store", store)
        monkeypatch.setattr(rt, "simulation_mode", False)
        assert rt._checkpoint_runtime("HOLDING_CONTEXT_TEST") is True
        assert rt.persistence_healthy is True and rt.persistence_error is None
    finally:
        monkeypatch.setattr(rt, "state_store", None)
        store.close()


# 4 ----------------------------------------------------------------------------------------------
def test_checkpoint_round_trip_keeps_entry_context_and_old_payloads_restore_none(rt):
    b = _admit(rt)
    assert b.entry_context
    wire = json.loads(json.dumps(encode_runtime_value(b)))
    back = decode_runtime_value(wire)
    assert isinstance(back, BracketOrder) and back.entry_context == b.entry_context
    old = json.loads(json.dumps(encode_runtime_value(b)))
    old["fields"].pop("entry_context")                    # a checkpoint written before this field existed
    assert decode_runtime_value(old).entry_context is None


def test_a_dict_with_entry_context_validates_under_a_model_without_the_field(rt):
    b = _admit(rt)
    fields = {k: (v.annotation, v) for k, v in BracketOrder.model_fields.items() if k != "entry_context"}
    Old = create_model("OldBracketOrder", **fields)       # what a rollback would run
    dumped = b.model_dump(mode="python")
    assert dumped["entry_context"]
    restored = Old.model_validate(dumped)
    assert restored.bracket_id == b.bracket_id and not hasattr(restored, "entry_context")


# 5 ----------------------------------------------------------------------------------------------
def _position(sym="AAPL", strategy="MANUAL", shares=100):
    return Position(symbol=sym, side=PositionSide.LONG, shares=shares, avg_entry_price=100.0, market_price=101.0,
                    strategy_id=strategy)


def test_serialize_position_new_fields_and_trail_only_has_no_second_target(rt):
    rt.account.positions["AAPL"] = _position(strategy="vwap_pullback")
    b = rt.bracket_manager.create_bracket("brk_x", "AAPL", "LONG", 100, 100.0, 98.0, strategy_id="vwap_pullback",
                                          runner_policy="TRAIL_ONLY")
    b.entry_context = {"qty_final": 100}
    out = rt._serialize_position("AAPL", include_chart=False)
    assert POSITION_KEYS <= set(out)
    assert out["entry_context"] == {"qty_final": 100} and out["initial_stop"] == b.initial_stop_price
    assert out["bracket_status"] == b.status.value and out["runner_policy"] == "TRAIL_ONLY"
    assert out["target_1_filled"] is False
    assert out["take_profit_2"] is None and b.target_2_price is not None          # display only: bracket untouched
    assert out["orb_context"] is None and out["plan_risk_pct"] is None
    # a TARGET runner keeps its second target
    b.runner_policy = "TARGET"
    b.target_2_order_id = "t2_brk_x"
    assert rt._serialize_position("AAPL", include_chart=False)["take_profit_2"] == b.target_2_price


def test_serialize_position_intraday_exit_due_follows_the_flattening_schedule(rt):
    rt.account.positions["AAPL"] = _position()
    rt.bracket_manager.create_bracket("brk_y", "AAPL", "LONG", 100, 100.0, 98.0, strategy_id="news_momentum")
    due = datetime.fromisoformat(rt._serialize_position("AAPL", include_chart=False)["exit_due"])
    assert (due.hour, due.minute) == (15, 55) and due.tzinfo is not None
    old = rt.flattening_engine.schedule.phase3_liquidation_time
    try:
        rt.flattening_engine.schedule.phase3_liquidation_time = dtime(15, 30)
        due = datetime.fromisoformat(rt._serialize_position("AAPL", include_chart=False)["exit_due"])
        assert (due.hour, due.minute) == (15, 30)                                # read, not hard-coded
    finally:
        rt.flattening_engine.schedule.phase3_liquidation_time = old


def test_bracketless_position_falls_back_to_its_own_strategy_then_manual(rt):
    rt.account.positions["AAPL"] = _position(strategy="VWAP_PULLBACK")
    rt.account.positions["MSFT"] = _position(sym="MSFT", strategy="MANUAL")
    a = rt._serialize_position("AAPL", include_chart=False)
    m = rt._serialize_position("MSFT", include_chart=False)
    assert a["strategy_id"] == "vwap_pullback" and m["strategy_id"] == "manual"
    assert a["entry_context"] is None and a["bracket_status"] is None and a["initial_stop"] is None


def test_tri_position_gets_plan_risk_pct_and_keeps_its_own_fields(rt, monkeypatch):
    rt.account.positions["TSLA"] = _position(sym="TSLA", strategy="tsla_asymmetric_dual")
    own = {"strategy_id": "tsla_asymmetric_dual", "fixed_protection": True, "stop_loss": 90.0,
           "take_profit_1": 110.0, "take_profit_2": 120.0, "exit_due": "2026-09-24T12:00:00-04:00",
           "tranches": [{"qty": 1}]}
    monkeypatch.setattr(rt.tri_controller, "owns", lambda s: s == "TSLA")
    monkeypatch.setattr(rt.tri_controller, "position_details", lambda s: dict(own))
    out = rt._serialize_position("TSLA", include_chart=False)
    assert out["plan_risk_pct"] == pytest.approx(0.75) == pytest.approx(TRI_RISK_PCT * 100)
    for k, v in own.items():
        assert out[k] == v
    assert out["orb_context"] is None


def test_a_display_field_that_raises_becomes_none_not_a_broken_frame(rt, monkeypatch):
    rt.account.positions["AAPL"] = _position(strategy="news_momentum")
    rt.bracket_manager.create_bracket("brk_z", "AAPL", "LONG", 100, 100.0, 98.0, strategy_id="news_momentum")

    def no_clock():
        raise RuntimeError("no clock")
    monkeypatch.setattr(rt, "_liquidation_due_iso", no_clock)
    out = rt._serialize_position("AAPL", include_chart=False)
    assert out["exit_due"] is None and out["symbol"] == "AAPL"


# 6 ----------------------------------------------------------------------------------------------
def test_market_context_has_every_key_in_every_case(rt, monkeypatch):
    eng = rt.adaptation_engine
    cases = [("normal", rt._market_context())]
    monkeypatch.setattr(eng, "market_filter", None)                    # no filter
    cases.append(("no filter", rt._market_context()))
    monkeypatch.setattr(eng, "current_vix", float("nan"))              # NaN VIX
    cases.append(("nan vix", rt._market_context()))
    monkeypatch.setattr(rt, "last_vix_print", SimpleNamespace(is_stale=True, is_fallback=False, asof=T0))
    cases.append(("stale", rt._market_context()))
    cases.append(("bare engine", eng.get_market_context()))
    for name, ctx in cases:
        assert CONTEXT_KEYS <= set(ctx), (name, CONTEXT_KEYS - set(ctx))
        json.dumps(rt._sanitize_for_json(ctx), allow_nan=False)
    by = dict(cases)
    assert by["no filter"]["market_trend"] is None and by["no filter"]["market_trend_reason"] is None
    assert by["nan vix"]["vix"] is None                                # NOT 0.0 (the frame sanitiser's answer)
    assert rt._sanitize_for_json(by["nan vix"])["vix"] is None
    assert by["stale"]["vix_stale"] is True and isinstance(by["stale"]["vix_age_seconds"], float)
    assert by["normal"]["market_trend"] == "BULLISH"
    assert by["normal"]["notional_cap_pct"] == 25.0
    assert by["normal"]["base_risk_pct"] == pytest.approx(rt.settings.PER_POSITION_RISK_PCT * 100)
    assert by["normal"]["midday"] == {"start": "11:30", "end": "14:00"}


def test_market_context_survives_a_failure_inside_it(rt, monkeypatch):
    def boom(**_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(rt.adaptation_engine, "get_market_context", boom)
    assert rt._market_context() == {}                                  # the frame still goes out


def test_vix_tiers_equal_the_events_tuples():
    tiers = DynamicAdaptationEngine.vix_tiers()
    assert [t["sizing"] for t in tiers] == list(VIX_REGIME_SIZING_MULTIPLIERS)
    assert [t["stop"] for t in tiers] == list(VIX_REGIME_STOP_MULTIPLIERS)
    assert [t["lower"] for t in tiers] == [None, *VIX_REGIME_BOUNDARIES]
    assert [t["upper"] for t in tiers] == [*VIX_REGIME_BOUNDARIES, None]
    assert [t["name"] for t in tiers] == ["LOW", "NORMAL", "ELEVATED", "CRISIS"]


def test_adaptive_strategies_lists_only_the_switched_on_ones(rt):
    on = rt._market_context()["adaptive_strategies"]
    assert set(on) <= {"vwap_pullback", "news_momentum", "mean_reversion"}
    assert "mean_reversion" in on
    rt.mean_reversion_strategy.pause()
    assert "mean_reversion" not in rt._market_context()["adaptive_strategies"]
    rt.mean_reversion_strategy.resume()
    old = rt.vwap_strategy.mode
    try:
        rt.vwap_strategy.mode = "off"
        assert "vwap_pullback" not in rt._market_context()["adaptive_strategies"]
    finally:
        rt.vwap_strategy.mode = old


def test_time_multiplier_matches_what_sizing_actually_uses():
    eng = DynamicAdaptationEngine()
    for phase, expect in (("OPEN_VOLATILITY_FLUSH", 1.0), ("MIDDAY_CHOP", 0.5), ("POWER_HOUR", 1.0)):
        eng.current_time_phase = phase
        assert eng.time_multiplier == expect
        assert eng.calculate_adapted_size(100_000, 100.0, 99.0) == calculate_position_size(
            100_000, 100.0, 99.0, eng.base_risk_pct, eng.max_alloc_pct, eng.current_sizing_multiplier * expect)


# 7 ----------------------------------------------------------------------------------------------
def test_trading_windows_phases_agree_with_the_engine_for_every_minute():
    def from_table(t: dtime) -> str:
        for start, end, phase in PHASES:
            if start <= t < end:
                return phase
        return "PRE_MARKET" if t < PHASES[0][0] else "POST_MARKET"
    for minute in range(9 * 60, 16 * 60 + 31):
        t = dtime(minute // 60, minute % 60)
        assert from_table(t) == get_time_of_day_phase(t), t


# Ride the Trend through the real pipeline: its extra context (stop basis, RS flags, macro) ----------
def test_ride_the_trend_entry_context_through_the_real_pipeline():
    from backend.tests.unit import test_ride_the_trend_pipeline as p
    from backend.tests.unit.test_ride_the_trend_data_layers import FakeProfileStore, FakeTape
    from backend.app.strategies import vwap_pullback_v2 as v2
    r = p.r
    old, old_prof = v2.TAPE, v2.PROFILE
    v2.TAPE, v2.PROFILE = FakeTape(delta_ratio=0.15, per_second=0.02, imbalance=0.35), FakeProfileStore()
    statuses = {x.strategy_id: x.status for x in r.strategies}      # _session pauses every other strategy
    p._session(r)
    try:
        s = p.TrendScenario(tick_gates=False)
        s.quiet(40)
        s.full_setup("LONG")
        stock = list(s.bars)
        n = len(stock)
        index = [b for i in range(n) for b in (p._index_bar("SPY", i, 500.0), p._index_bar("QQQ", i, 400.0))]
        events = sorted(index + stock, key=lambda b: (b.timestamp, 0 if b.symbol in ("SPY", "QQQ") else 1))
        asyncio.run(p._feed(r, events))
        b = next(x for x in r.bracket_manager.brackets.values() if x.strategy_id == "vwap_pullback")
        ctx = b.entry_context
        assert ctx is not None
        assert ctx["stop_basis"] in ("volatility", "structure", "floor")
        assert ctx["rs"] == {"day": True, "recent": True}
        assert ctx["macro"] == "MACRO_CLEAR"
        assert ctx["regime_enforced"] == sorted(r.RIDE_THE_TREND_ENFORCED)
        assert ctx["market_trend"] == "BULLISH" and ctx["trend_reason"].startswith("APPROVED")
        # qty_final is what was sized and sent; the simulator may fill less (bracket.total_qty is what filled)
        assert ctx["qty_final"] >= b.total_qty > 0 and ctx["stop_adapted"] == ctx["stop_raw"]   # final stop: not re-scaled
        served = r._serialize_position("AAPL", include_chart=False)
        assert served["entry_context"] == ctx and served["runner_policy"] == "TRAIL_ONLY"
        assert served["take_profit_2"] is None
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)
        v2.TAPE, v2.PROFILE = old, old_prof
        for x in r.strategies:
            x.status = statuses[x.strategy_id]


# Tri risk percent is ONE constant: sizing, the strategy card and the holding label all read it ----------
@pytest.mark.parametrize("pct", [0.0075, 0.00375, 0.005])
def test_tri_risk_pct_moves_sizing_the_card_and_the_holding_label_together(tri_paper, monkeypatch, pct):
    from backend.app.strategies import tri_engine
    r, x = tri_paper
    monkeypatch.setattr(tri_engine, "TRI_RISK_PCT", pct)
    s = arm(r, x, "TSLA", "LONG")
    assert s.phase == "HOLDING", s.last_error
    per_share = (x.entry_price + 0.01) - s.stop                # sized off the ask
    budget = s.session_equity * pct                           # under the 1.5% combined cap
    # tri_execution's entry sizing: floor(budget / risk per share). One share of slack for the exact quote used.
    assert budget - 2 * per_share < s.quantity * per_share <= budget + per_share
    assert s.quantity > 1
    assert s.to_dict()["tri_engine"]["risk_budget"] == pytest.approx(s.session_equity * pct)
    assert r._serialize_position("TSLA", include_chart=False)["plan_risk_pct"] == pytest.approx(pct * 100)


# Fail-soft display code runs on every frame: one persistent bug must not flood the logs ------------------
def test_a_persistent_display_bug_logs_once_then_at_most_every_ten_minutes(caplog):
    import logging
    from backend.app.core import log_limit
    log_limit.reset_for_tests()
    clock = [1000.0]
    lg = logging.getLogger("holding_context_test")
    with caplog.at_level(logging.WARNING, logger="holding_context_test"):
        for _ in range(2000):                                     # ~8 minutes of frames at 4 a second
            try:
                raise ValueError("bad field")
            except ValueError:
                log_limit.warn_rate_limited(lg, "display:x", "Display field %s failed", "x", now=lambda: clock[0])
            clock[0] += 0.25
        assert len(caplog.records) == 1 and caplog.records[0].exc_info      # first failure: full traceback
        clock[0] += 600.0
        try:
            raise ValueError("bad field")
        except ValueError:
            assert log_limit.warn_rate_limited(lg, "display:x", "Display field %s failed", "x", now=lambda: clock[0])
        assert len(caplog.records) == 2 and not caplog.records[1].exc_info
        assert "1999 more" in caplog.records[1].getMessage()
        try:                                                      # a different field has its own first traceback
            raise ValueError("other")
        except ValueError:
            assert log_limit.warn_rate_limited(lg, "display:y", "Display field %s failed", "y", now=lambda: clock[0])
        assert len(caplog.records) == 3 and caplog.records[2].exc_info


def test_serialize_position_with_a_broken_field_warns_once_not_per_frame(rt, monkeypatch, caplog):
    import logging
    from backend.app.core import log_limit
    log_limit.reset_for_tests()
    rt.account.positions["AAPL"] = _position(strategy="news_momentum")
    rt.bracket_manager.create_bracket("brk_l", "AAPL", "LONG", 100, 100.0, 98.0, strategy_id="news_momentum")

    def no_clock():
        raise RuntimeError("no clock")
    monkeypatch.setattr(rt, "_liquidation_due_iso", no_clock)
    with caplog.at_level(logging.WARNING, logger="AutonomousDayTrader"):
        for _ in range(500):
            assert rt._serialize_position("AAPL", include_chart=False)["exit_due"] is None
    warns = [x for x in caplog.records if "Display field exit_due failed" in x.getMessage()]
    assert len(warns) == 1 and warns[0].exc_info
