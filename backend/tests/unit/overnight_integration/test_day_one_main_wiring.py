# @steered SNARE-2 2026-09-30
"""Production wiring checks for the two day one controller strategies."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.core import day_one_schedule as d1
from backend.app.core.account import PaperTradingAccount, Position, PositionSide, TradingArm
from backend.app.core.engine import ExecutionEngine, ORB_POLICY, OrderSide, OrderType
from backend.app.core.overnight_schedule import OVERNIGHT_POLICY
from backend.app.core.flattening import FlatteningPhase
from backend.app.core.risk import BreakerStatus


def position(symbol="SPY", strategy=d1.SPY_ID):
    return Position(symbol, PositionSide.LONG, 10, 500.0, 500.0,
                    arm=TradingArm.INTRADAY, strategy_id=strategy)


def test_checkpoint_contains_backward_compatible_day_one_section(main_runtime):
    r = main_runtime
    r.day_one._pending = {"controller": None, "ledger": {"fills": {}, "recorded": []}}
    state = r._capture_checkpoint()
    assert state["day_one"]["version"] == 1
    assert state["day_one_ownership"] == {"claims": []}
    assert state["runtime_state_version"] == 2


def test_composite_broker_guard_preserves_both_dedicated_owners(main_runtime, monkeypatch):
    r = main_runtime
    order = SimpleNamespace(symbol="SPY", qty=1, remaining_qty=1, strategy_id="mean_reversion",
                            execution_policy=None)
    monkeypatch.setattr(r.day_one, "broker_guard", lambda _order, _qty: "DAY_ONE")
    monkeypatch.setattr(r.overnight, "broker_guard", lambda _order, _qty: "OVERNIGHT")
    assert r._dedicated_order_guard(order, 1) == "DAY_ONE"
    monkeypatch.setattr(r.day_one, "broker_guard", lambda _order, _qty: None)
    assert r._dedicated_order_guard(order, 1) == "OVERNIGHT"


def test_phase_four_audit_exempts_only_protected_day_one_position(main_runtime):
    r = main_runtime
    r.account.positions["SPY"] = position()
    r.flattening_engine.clock.set_simulated_time(datetime(2026, 10, 1, 15, 58, tzinfo=d1.ET))
    unsafe = r.flattening_engine.execute_phase_4_audit(r.account.positions, [])
    assert unsafe.audit_passed is False and unsafe.unclosed_symbols == ["SPY"]
    protected = r.flattening_engine.execute_phase_4_audit(
        r.account.positions, [], day_one_protected=lambda symbol: symbol == "SPY"
    )
    assert protected.audit_passed is True and protected.unclosed_symbols == []


def test_session_boundary_never_generically_liquidates_protected_day_one(main_runtime, monkeypatch):
    r = main_runtime
    r.simulation_mode = True
    r.last_session_date = date(2026, 10, 1)
    r.account.positions["SPY"] = position()
    monkeypatch.setattr(r.day_one, "safe_to_exempt", lambda symbol: symbol == "SPY")
    r._check_session_boundary(datetime(2026, 10, 2, 0, 1, tzinfo=d1.ET))
    assert "SPY" in r.account.positions
    assert not [o for o in r.engine.orders.values() if o.strategy_id == "SESSION_BOUNDARY_LIQUIDATION"]


def test_loss_breaker_delegates_then_skips_generic_order(main_runtime, monkeypatch):
    r = main_runtime
    r.account.positions["SPY"] = position()
    requested = []
    monkeypatch.setattr(r.day_one, "owns", lambda symbol: symbol == "SPY")
    monkeypatch.setattr(r.day_one, "safe_to_exempt", lambda symbol: symbol == "SPY")
    monkeypatch.setattr(r.day_one, "request_exit", lambda symbol, reason, now=None: requested.append((symbol, reason)) or True)
    r._trip_circuit_breaker(datetime(2026, 10, 1, 10, 0, tzinfo=d1.ET))
    assert requested == [("SPY", "CIRCUIT_BREAKER")]
    assert not [o for o in r.engine.orders.values() if o.symbol == "SPY" and o.strategy_id == "CIRCUIT_BREAKER"]


def test_position_serialization_shows_owner_and_official_close(main_runtime, monkeypatch):
    r = main_runtime
    r.account.positions["COIN"] = position("COIN", d1.COIN_ID)
    monkeypatch.setattr(r.day_one, "position_details", lambda _symbol: {
        "strategy_id": d1.COIN_ID,
        "exit_due": "2026-10-01T16:00:00-04:00",
        "day_one": True,
        "evidence": d1.EVIDENCE[d1.COIN_ID],
        "no_strategy_stop": True,
        "no_strategy_target": True,
    })
    row = r._serialize_position("COIN", include_chart=False)
    assert row["strategy_id"] == d1.COIN_ID
    assert row["exit_due"].endswith("16:00:00-04:00")
    assert row["stop_loss"] is None and row["take_profit_1"] is None


def test_day_one_admission_counts_normal_intraday_slots(main_runtime):
    r = main_runtime
    r.simulation_mode = False
    r.engine.broker = object()
    r.state_store = object()
    r.persistence_healthy = True
    r.broker_state["mismatch"] = False
    r.risk_engine.status = BreakerStatus.ARMED
    r.account.positions["SPY"] = position()
    assert r.day_one_admission("SPY", "LONG", 10, 5_000.0) is None
    assert "SPY" in r._get_effective_committed_portfolio(
        r.account, r.engine, r.risk_engine, r.bracket_manager, arm=TradingArm.INTRADAY
    )[0]


def test_engine_never_matches_day_one_booking_order(main_runtime):
    r = main_runtime
    order = r.engine.create_order("SPY", OrderSide.BUY, OrderType.MARKET, 1,
                                  strategy_id=d1.SPY_ID, estimated_price=500.0)
    order.execution_policy = d1.DAY_ONE_POLICY
    r.engine.submit_order(order.id)
    fills = r.engine.process_bar("SPY", 500, 500, 500, 500, 1000,
                                 datetime(2026, 10, 1, 10, 0, tzinfo=d1.ET))
    assert fills == [] and "SPY" not in r.account.positions


def test_pending_day_one_entry_consumes_shared_intraday_slot_except_for_itself(main_runtime, monkeypatch):
    r = main_runtime
    r.simulation_mode = False
    r.engine.broker = object()
    r.state_store = object()
    r.persistence_healthy = True
    r.broker_state["mismatch"] = False
    r.risk_engine.status = BreakerStatus.ARMED
    r.account.positions["AAPL"] = position("AAPL", "vwap_pullback")
    r.account.positions["MSFT"] = position("MSFT", "mean_reversion")
    monkeypatch.setattr(r.day_one, "commitments", lambda exclude=None: {} if exclude == "COIN" else {"COIN": 5_000.0})
    assert r.day_one_admission("SPY", "LONG", 10, 5_000.0) == "DAY_ONE_POSITION_SLOTS_FULL"
    assert r.day_one_admission("COIN", "LONG", 20, 5_000.0) is None
    committed = r._get_effective_committed_portfolio(
        r.account, r.engine, r.risk_engine, r.bracket_manager, arm=TradingArm.INTRADAY
    )[0]
    assert committed == {"AAPL", "MSFT", "COIN"}


def test_day_one_breaker_check_applies_after_hours(main_runtime):
    r = main_runtime
    r.simulation_mode = False
    r.engine.broker = object()
    r.state_store = object()
    r.persistence_healthy = True
    r.broker_state["mismatch"] = False
    r.risk_engine.status = BreakerStatus.HALTED_DAILY_LOSS
    assert r.day_one_admission("SPY", "LONG", 10, 5_000.0) == "DAY_ONE_ACCOUNT_HALTED"


def test_engine_matching_adds_only_day_one_to_the_preexisting_fixed_exclusion():
    account = PaperTradingAccount(initial_cash=50_000.0)
    engine = ExecutionEngine(account)
    now = datetime(2026, 10, 1, 10, 0, tzinfo=d1.ET)
    for symbol, policy in (("AAPL", ORB_POLICY), ("MSFT", OVERNIGHT_POLICY)):
        order = engine.create_order(symbol, OrderSide.BUY, OrderType.MARKET, 1, estimated_price=100.0)
        order.execution_policy = policy
        engine.submit_order(order.id)
        assert len(engine.process_bar(symbol, 100, 100, 100, 100, 10_000, now)) == 1

    day_one_order = engine.create_order("SPY", OrderSide.BUY, OrderType.MARKET, 1, estimated_price=500.0)
    day_one_order.execution_policy = d1.DAY_ONE_POLICY
    engine.submit_order(day_one_order.id)
    assert engine.process_bar("SPY", 500, 500, 500, 500, 10_000, now) == []


def test_health_exposes_only_day_one_mode_readiness_and_strategy_phases(main_runtime, monkeypatch):
    r = main_runtime
    monkeypatch.setattr(r.settings, "RELAY_TOKEN", "configured")
    r.relay_statuses.update({"stock": "connected", "news": "connected", "vix": "connected"})
    health = asyncio.run(r.get_health())
    assert health["status"] != "healthy"
    assert set(health["day_one"]) == {"mode", "readiness", "strategy_phases", "source_revision_match"}
    assert health["day_one"]["readiness"] == "not_ready"
    monkeypatch.setattr(r.settings, "ENV", "production")
    assert TestClient(r.app).get("/health").status_code == 503


def test_deployment_attestation_ticks_then_rereads_broker_and_uses_source_revision(main_runtime, monkeypatch):
    r = main_runtime
    events = []
    expected_revision = r._day_one_source_revision()

    class Broker:
        def sync(self):
            events.append("broker_reread")
            return SimpleNamespace(positions={}, equity=50_000.0)

    monkeypatch.setattr(r.day_one, "tick", lambda _now: events.append("controller_tick"))
    monkeypatch.setattr(
        r.day_one,
        "attestation",
        lambda revision: events.append(("attestation", revision)) or {"runtime_revision": revision},
    )
    monkeypatch.setattr(r, "_compare_with_broker", lambda _positions, _equity: events.append("broker_compare"))
    result = asyncio.run(r._day_one_deployment_attestation(Broker()))
    assert events == ["controller_tick", "broker_reread", "broker_compare", ("attestation", expected_revision)]
    assert result["runtime_revision"] == expected_revision


def test_production_source_revision_must_match_exact_uploaded_files(main_runtime, monkeypatch):
    r = main_runtime
    actual = r._day_one_source_revision()
    assert len(actual) == 64
    monkeypatch.setattr(r.settings, "ENV", "production")
    monkeypatch.delenv("DAY_ONE_BUILD_REVISION", raising=False)
    assert r._day_one_revision_match() is False
    monkeypatch.setenv("DAY_ONE_BUILD_REVISION", "wrong")
    assert r._day_one_revision_match() is False
    monkeypatch.setenv("DAY_ONE_BUILD_REVISION", actual)
    assert r._day_one_revision_match() is True
    assert r._day_one_build_revision() == actual
