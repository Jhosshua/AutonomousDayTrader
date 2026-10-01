# @steered SNARE-2 2026-09-30
"""Targeted runtime integration tests for the isolated day one controller."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time as dtime
from enum import Enum
from types import SimpleNamespace

from backend.app.core import day_one_schedule as d1
from backend.app.core.day_one_execution import DayOneHooks, InlineExecutor
from backend.app.core.day_one_integration import BOOKING_KEYS, DayOneIntegration
from backend.app.core.overnight_schedule import TradingWindowsCalendar

CALENDAR = TradingWindowsCalendar()
SESSION = date(2026, 10, 1)


def moment(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(SESSION, dtime(hour, minute, second), d1.ET)


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Broker:
    def __init__(self):
        self.orders = {}
        self.positions = {}
        self.posts = []

    def position_qty(self, symbol):
        return self.positions.get(symbol, 0)

    def get_positions(self):
        return dict(self.positions)

    def get_positions_raw(self):
        return [{"symbol": symbol, "qty": str(qty)} for symbol, qty in self.positions.items()]

    def get_account_fields(self):
        return {
            "account_number": d1.EXPECTED_ACCOUNT,
            "equity": 50_000.0,
            "buying_power": 100_000.0,
        }

    def list_open_orders(self, symbol=None):
        return [dict(row) for row in self.orders.values()
                if row["status"] not in ("filled", "canceled", "expired", "rejected")
                and (symbol is None or row["symbol"] == symbol)]

    def get_order(self, order_id, nested=True):
        return dict(self.orders[order_id])

    def get_order_by_client_id(self, client_id, nested=True):
        row = next((row for row in self.orders.values() if row["client_order_id"] == client_id), None)
        return dict(row) if row else None

    def _submit(self, symbol, side, qty, client_id, tif):
        oid = f"o{len(self.orders) + 1}"
        row = {"id": oid, "symbol": symbol, "side": side, "qty": str(qty), "client_order_id": client_id,
               "time_in_force": tif, "status": "accepted", "filled_qty": "0", "filled_avg_price": None}
        self.orders[oid] = row
        self.posts.append(dict(row))
        return dict(row)

    def submit_market_order(self, symbol, side, qty, client_id):
        return self._submit(symbol, side, qty, client_id, "day")

    def submit_on_auction(self, symbol, side, qty, client_id, tif):
        return self._submit(symbol, side, qty, client_id, tif)

    def cancel_order_and_confirm(self, order_id, timeout=6.0):
        self.orders[order_id]["status"] = "canceled"
        return dict(self.orders[order_id])


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"


class TradingArm(str, Enum):
    INTRADAY = "INTRADAY"


class Account:
    def __init__(self):
        self.positions = {}


class Engine:
    def __init__(self, account):
        self.account = account
        self.orders = {}
        self.sequence = 0

    def create_order(self, **fields):
        self.sequence += 1
        order = SimpleNamespace(id=f"local{self.sequence}", execution_policy=None, **fields)
        self.orders[order.id] = order
        return order

    def _apply_fill_to_ledger(self, order, qty, price, fee, slippage, timestamp):
        position = self.account.positions.get(order.symbol)
        realized = 0.0
        if order.side == OrderSide.BUY:
            if position is None:
                self.account.positions[order.symbol] = SimpleNamespace(
                    symbol=order.symbol,
                    side=SimpleNamespace(value="LONG"),
                    shares=qty,
                    avg_entry_price=price,
                    strategy_id=order.strategy_id,
                )
            elif position.side.value == "LONG":
                total = position.shares + qty
                position.avg_entry_price = (position.shares * position.avg_entry_price + qty * price) / total
                position.shares = total
            else:
                realized = qty * (position.avg_entry_price - price)
                position.shares -= qty
        else:
            if position is None:
                self.account.positions[order.symbol] = SimpleNamespace(
                    symbol=order.symbol,
                    side=SimpleNamespace(value="SHORT"),
                    shares=qty,
                    avg_entry_price=price,
                    strategy_id=order.strategy_id,
                )
            elif position.side.value == "LONG":
                realized = qty * (price - position.avg_entry_price)
                position.shares -= qty
            else:
                total = position.shares + qty
                position.avg_entry_price = (position.shares * position.avg_entry_price + qty * price) / total
                position.shares = total
        position = self.account.positions.get(order.symbol)
        if position is not None and position.shares == 0:
            self.account.positions.pop(order.symbol)
        return SimpleNamespace(fill_id=f"fill{self.sequence}", realized_pnl=realized)


class Runtime:
    def __init__(self):
        self.account = Account()
        self.engine = Engine(self.account)
        self.OrderSide = OrderSide
        self.OrderType = OrderType
        self.TradingArm = TradingArm
        self.persistence_revision = 0
        self.persistence_healthy = True
        self.inflight_event_keys = set()
        self.broker_state = {"mismatch": False}
        self.pending_trade_records = {}
        self.reconciled = []
        self.overnight = SimpleNamespace(checkpoint_state=lambda: {"controller": {"nights": {}}})

    def _checkpoint_runtime(self, reason):
        self.persistence_revision += 1
        return True

    def _reconcile_fills(self, fills):
        self.reconciled.extend(fills)

    @staticmethod
    def _sanitize_for_json(value):
        return value


class Rig:
    def __init__(self, now=moment(9, 29)):
        self.runtime = Runtime()
        self.clock = Clock(now)
        self.broker = Broker()
        self.integration = DayOneIntegration(self.runtime)
        self.controller = self.integration.build(
            self.broker,
            clock=self.clock,
            executor=InlineExecutor(),
            calendar=CALENDAR,
            spy_reference=lambda _session: 500.0,
            coin_model=lambda _session: {"side": 1, "zmove": 0.02, "sigma": 0.01, "history_count": 20},
            hooks=DayOneHooks(admission=lambda *_args: None),
        )

    def lifecycle(self):
        return self.controller.state["lifecycles"]["COIN"]


def event(role, side, qty, price, order_id, client_id):
    row = {
        "kind": "fill",
        "strategy_id": d1.COIN_ID,
        "symbol": "COIN",
        "session": SESSION.isoformat(),
        "role": role,
        "side": side,
        "qty": qty,
        "price": price,
        "at": moment(10, 0).isoformat(),
        "alpaca_order_id": order_id,
        "client_order_id": client_id,
        "target_qty": qty,
        "cumulative_qty": qty,
    }
    assert set(row) == BOOKING_KEYS
    return row


def prepare_long(rig):
    assert rig.integration.reserve_coin_before_events(SESSION)
    lifecycle = rig.lifecycle()
    lifecycle.update(
        side="LONG",
        decision={"side": 1},
        decision_saved=True,
        target_qty=5,
        reference_price=250.0,
        sizing_equity=50_000.0,
        sizing_buying_power=100_000.0,
        admitted=True,
    )
    return lifecycle


def test_reservation_ownership_guards_and_checkpoint_round_trip():
    rig = Rig()
    assert rig.integration.reserve_coin_before_events(SESSION)
    assert rig.integration.owns("COIN") and rig.integration.claimed("COIN")
    generic = SimpleNamespace(symbol="COIN", qty=2, remaining_qty=2, strategy_id="vwap_pullback",
                              execution_policy=None)
    assert rig.integration.order_refusal(generic, 2).startswith("DAY_ONE_OWNED")
    assert rig.integration.broker_guard(generic, 2)
    assert rig.integration.occupied_reason("COIN")
    own = SimpleNamespace(symbol="COIN", execution_policy=d1.DAY_ONE_POLICY, strategy_id=d1.COIN_ID)
    assert rig.integration.order_refusal(own) is None

    saved = rig.integration.checkpoint()
    restored = DayOneIntegration(Runtime())
    restored.load(saved)
    assert restored._pending["controller"]["lifecycles"]["COIN"]["phase"] == "RESERVED"


def test_unread_future_state_with_ownership_stays_claimed_in_recovery_halt():
    integration = DayOneIntegration(Runtime())
    future = {"version": 2, "controller": {"ownership": [{"symbol": "SPY", "entry_qty": 3}]}}
    integration.load_state(future)
    assert integration.controller is None
    assert integration.claimed("SPY")
    assert integration.health()["recovery_halt"] is True
    assert integration.reserved_risk(900.0) == 900.0


def test_strict_booking_accepts_matching_partial_position_and_rejects_contradiction_before_account_fill():
    rig = Rig()
    lifecycle = prepare_long(rig)
    first = event("entry", "buy", 3, 250.0, "o1", "adt-btc-coin-20261001-entry-1")
    assert rig.integration._book(first) is True
    position = rig.runtime.account.positions["COIN"]
    assert position.shares == 3 and position.strategy_id == d1.COIN_ID

    second = event("entry", "buy", 2, 251.0, "o2", "adt-btc-coin-20261001-entry-2")
    assert rig.integration._book(second) is True
    assert rig.runtime.account.positions["COIN"].shares == 5

    invalid = dict(second)
    invalid["unexpected"] = "field"
    assert rig.integration._book(invalid) is False
    assert rig.runtime.account.positions["COIN"].shares == 5
    assert lifecycle["phase"] == "RECOVERY_HALT"


def test_exit_booking_requires_exact_strategy_side_and_owned_quantity():
    rig = Rig()
    lifecycle = prepare_long(rig)
    assert rig.integration._book(event("entry", "buy", 5, 250.0, "o1",
                                       "adt-btc-coin-20261001-entry-1"))
    too_large = event("exit", "sell", 6, 252.0, "o2", "adt-btc-coin-20261001-exit-1")
    assert rig.integration._book(too_large) is False
    assert rig.runtime.account.positions["COIN"].shares == 5
    assert lifecycle["phase"] == "RECOVERY_HALT"


def test_completed_trade_is_recorded_once_only_after_flat_terminal_release():
    rig = Rig()
    lifecycle = prepare_long(rig)
    assert rig.integration._book(event("entry", "buy", 5, 250.0, "o1",
                                       "adt-btc-coin-20261001-entry-1"))
    assert rig.integration._book(event("exit", "sell", 5, 252.0, "o2",
                                       "adt-btc-coin-20261001-exit-1"))
    lifecycle.update(entry_qty=5, exit_qty=5, phase="DONE", released=True)
    lifecycle["operational_gates"] = ["ADMITTED"]
    lifecycle["execution_deviations"] = []
    assert rig.integration._record_completed() is True
    assert rig.integration._record_completed() is False
    trade = next(iter(rig.runtime.pending_trade_records.values()))
    assert trade["evidence"] == d1.EVIDENCE[d1.COIN_ID]
    assert trade["quantity"] == 5 and trade["realized_pnl"] == 10.0
    assert len(rig.integration.ledger["recorded"]) == 1


def test_before_compare_books_discovered_fill_then_places_broker_held_cls():
    rig = Rig(now=moment(9, 36))
    lifecycle = prepare_long(rig)
    attempt = rig.controller._create_attempt(lifecycle, "entry", "buy", 3, "day", rig.clock.value)
    broker_row = rig.broker.orders[attempt["order_id"]]
    broker_row.update(status="filled", filled_qty="3", filled_avg_price="250", filled_at=moment(9, 36).isoformat())
    rig.broker.positions["COIN"] = 3

    positions = asyncio.run(rig.integration.before_compare({"COIN": 3}))
    assert positions["COIN"] == 3
    assert rig.runtime.account.positions["COIN"].shares == 3
    assert lifecycle["entry_qty"] == 3
    assert any(row["time_in_force"] == "cls" and row["side"] == "sell" for row in rig.broker.posts)


def test_health_attestation_market_events_and_separate_fences_are_bounded():
    rig = Rig(now=moment(9, 35))
    rig.integration.reserve_coin_before_events(SESSION)
    quote = SimpleNamespace(symbol="COIN", bid_price=249.0, ask_price=251.0, timestamp=rig.clock.value)
    bar = SimpleNamespace(symbol="COIN", open=248.0, high=252.0, low=247.0, close=250.0,
                          timestamp=moment(9, 34))
    assert rig.integration.on_quote(quote)
    assert rig.integration.on_bar(bar)
    assert not rig.integration.on_quote(SimpleNamespace(symbol="AAPL"))
    health = rig.integration.health()
    attestation = rig.integration.attestation("revision-test")
    assert health["running"] and health["exit_writes_open"]
    assert attestation["runtime_revision"] == "revision-test"
    assert attestation["client_id_counts"]["COIN"] == {}

    rig.controller.close_entry_writes()
    assert rig.controller.health()["entry_writes_open"] is False
    assert rig.controller.health()["exit_writes_open"] is True
    rig.controller.close_exit_writes()
    assert rig.controller.health()["exit_writes_open"] is False


def test_coin_reservation_checks_all_owners_inside_orb_shared_lock_and_saves_nonowning_skip():
    class RecordingLock:
        entered = False

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *_args):
            self.entered = False

    runtime = Runtime()
    lock = RecordingLock()

    def orb_owns(symbol):
        assert lock.entered
        return symbol == "COIN"

    runtime.orb = SimpleNamespace(lock=lock, owns=orb_owns)
    integration = DayOneIntegration(runtime)
    controller = integration.build(
        Broker(),
        clock=Clock(moment(9, 29)),
        executor=InlineExecutor(),
        calendar=CALENDAR,
        spy_reference=lambda _session: 500.0,
        coin_model=lambda _session: {"side": 0},
    )
    assert integration.reserve_coin_before_events(SESSION) is False
    lifecycle = controller.state["lifecycles"]["COIN"]
    assert lifecycle["phase"] == "SKIPPED" and lifecycle["released"] is True
    assert integration.owns("COIN") is False
    assert runtime.persistence_revision > 0


def test_version_independent_ownership_envelope_restores_before_future_controller_decode():
    integration = DayOneIntegration(Runtime())
    integration.restore_ownership_envelope({"claims": ["SPY"]})
    assert integration.owns("SPY")
    integration.load_state({"version": 2, "controller": {"future": True}})
    assert integration.controller is None
    assert integration.owns("SPY")
    assert integration.health()["recovery_halt"] is True


def test_pending_commitments_exclude_only_the_current_lifecycle():
    rig = Rig()
    assert rig.integration.reserve_coin_before_events(SESSION)
    lifecycle = rig.lifecycle()
    lifecycle.update(admitted=True, target_qty=20, reference_price=250.0)
    assert rig.integration.commitments() == {"COIN": 5_000.0}
    assert rig.integration.commitments("COIN") == {}


def test_readiness_fails_for_absent_halted_or_unprotected_controller_position():
    absent = DayOneIntegration(Runtime())
    assert absent.readiness() == {
        "mode": "unavailable",
        "readiness": "not_ready",
        "strategy_phases": {d1.SPY_ID: "NOT_STARTED", d1.COIN_ID: "NOT_STARTED"},
    }

    rig = Rig()
    lifecycle = prepare_long(rig)
    lifecycle.update(entry_qty=5, phase="HELD")
    rig.runtime.account.positions["COIN"] = SimpleNamespace(
        execution_policy=d1.DAY_ONE_POLICY,
        strategy_id=d1.COIN_ID,
        side=SimpleNamespace(value="LONG"),
        shares=5,
    )
    assert rig.integration.readiness()["readiness"] == "not_ready"
    rig.controller._precommit_protection(lifecycle)
    assert rig.integration.readiness()["readiness"] == "not_ready"
    rig.controller._create_attempt(lifecycle, "exit", "sell", 5, "cls", rig.clock.value)
    assert rig.integration.readiness()["readiness"] == "ready"
    lifecycle["entry_qty"] = 0
    assert rig.integration.readiness()["readiness"] == "not_ready"
    lifecycle["entry_qty"] = 5
    lifecycle["phase"] = "RECOVERY_HALT"
    assert rig.integration.readiness()["readiness"] == "not_ready"


def test_executor_shutdown_waits_when_day_one_exposure_exists(monkeypatch):
    from backend.app.core import day_one_integration as module

    class FakePool:
        def __init__(self):
            self.calls = []

        def shutdown(self, **kwargs):
            self.calls.append(kwargs)

    monkeypatch.setattr(module, "ThreadPoolExecutor", FakePool)
    integration = DayOneIntegration(Runtime())
    pool = FakePool()
    integration.executor = pool
    integration.controller = SimpleNamespace(
        has_exposure=lambda: True,
        shutdown=lambda _timeout: True,
    )
    assert integration.shutdown() is True
    assert pool.calls == [{"wait": True, "cancel_futures": False}]


def test_ownership_envelope_without_controller_state_blocks_fresh_start():
    integration = DayOneIntegration(Runtime())
    integration.restore_ownership_envelope({"claims": ["COIN"]})
    integration.load_state(None)
    integration.start(Broker())
    assert integration.controller is None
    assert integration.owns("COIN")
    assert integration.readiness()["readiness"] == "not_ready"


def test_coin_reservation_checks_brackets_and_staged_swing_entries():
    for conflict in ("bracket", "staged"):
        runtime = Runtime()
        runtime.orb = SimpleNamespace(lock=__import__("contextlib").nullcontext(), owns=lambda _symbol: False)
        runtime.bracket_manager = SimpleNamespace(symbol_to_bracket={"COIN": "b1"} if conflict == "bracket" else {})
        runtime.swing_staged_order_manager = SimpleNamespace(
            is_staged_for_entry=lambda symbol, c=conflict: c == "staged" and symbol == "COIN"
        )
        integration = DayOneIntegration(runtime)
        controller = integration.build(
            Broker(), clock=Clock(moment(9, 29)), executor=InlineExecutor(), calendar=CALENDAR,
            spy_reference=lambda _session: 500.0, coin_model=lambda _session: {"side": 0},
        )
        assert integration.reserve_coin_before_events(SESSION) is False
        assert controller.state["lifecycles"]["COIN"]["phase"] == "SKIPPED"
        assert not integration.owns("COIN")
