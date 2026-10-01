# @steered SNARE-2 2026-09-30
"""Targeted durable lifecycle tests for SPY and COIN day one execution."""
from __future__ import annotations

import json
from concurrent.futures import Future
from datetime import date, datetime, time as dtime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.core import day_one_schedule as d1
from backend.app.core.day_one_execution import DayOneController, DayOneHooks, InlineExecutor
from backend.app.core.overnight_schedule import TradingWindowsCalendar

CALENDAR = TradingWindowsCalendar()
SEP30 = date(2026, 9, 30)
OCT1 = date(2026, 10, 1)


def moment(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, dtime(hour, minute, second), d1.ET)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class TransportError(RuntimeError):
    pass


class DelayedExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, work):
        future = Future()
        self.jobs.append((work, future))
        return future

    def run_next(self):
        work, future = self.jobs.pop(0)
        try:
            future.set_result(work())
        except Exception as exc:
            future.set_exception(exc)


def is_terminal(row):
    return row.get("status") in ("filled", "canceled", "expired", "rejected")


class FakeBroker:
    def __init__(self, clock: Clock, revision) -> None:
        self.clock = clock
        self.revision = revision
        self.orders = {}
        self.positions = {}
        self.writes = []
        self.lookups = []
        self.cancels = []
        self.fail_next = None
        self.cancel_fill = {}
        self.account = {
            "account_number": d1.EXPECTED_ACCOUNT,
            "equity": 50_000.0,
            "buying_power": 100_000.0,
        }
        self.assets = {
            "SPY": {"symbol": "SPY", "tradable": True, "shortable": True, "easy_to_borrow": True},
            "COIN": {"symbol": "COIN", "tradable": True, "shortable": True, "easy_to_borrow": True},
        }

    def get_calendar(self, start: str, end: str):
        current = date.fromisoformat(start)
        final = date.fromisoformat(end)
        rows = []
        while current <= final:
            if CALENDAR.is_trading_day(current):
                rows.append({"date": current.isoformat(), "close": CALENDAR.session_close(current).strftime("%H:%M")})
            current += timedelta(days=1)
        return rows

    def get_account_fields(self):
        return dict(self.account)

    def get_asset(self, symbol):
        return dict(self.assets[symbol])

    def get_positions_raw(self):
        return [{"symbol": symbol, "qty": str(qty)} for symbol, qty in self.positions.items()]

    def position_qty(self, symbol):
        return self.positions.get(symbol, 0)

    def list_open_orders(self, symbol=None):
        return [dict(row) for row in self.orders.values()
                if not is_terminal(row) and (symbol is None or row["symbol"] == symbol)]

    def get_order_by_client_id(self, client_order_id, nested=True):
        self.lookups.append(client_order_id)
        row = next((row for row in self.orders.values() if row["client_order_id"] == client_order_id), None)
        return dict(row) if row is not None else None

    def get_order(self, order_id, nested=True):
        row = self.orders[order_id]
        self.lookups.append(row["client_order_id"])
        return dict(row)

    def _submit(self, symbol, side, qty, client_order_id, tif):
        self.writes.append({
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "client_id": client_order_id,
            "tif": tif,
            "revision": self.revision(),
        })
        failure = self.fail_next
        self.fail_next = None
        oid = f"o{len(self.orders) + 1}"
        row = {
            "id": oid,
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "client_order_id": client_order_id,
            "time_in_force": tif,
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
        }
        if failure == "landed_transport":
            self.orders[oid] = row
            raise TransportError("connection reset")
        if failure == "transport":
            raise TransportError("connection reset")
        self.orders[oid] = row
        return dict(row)

    def submit_on_auction(self, symbol, side, qty, client_order_id, tif):
        return self._submit(symbol, side, qty, client_order_id, tif)

    def submit_market_order(self, symbol, side, qty, client_order_id):
        return self._submit(symbol, side, qty, client_order_id, "day")

    def fill(self, client_id, total, price, status=None):
        row = next(row for row in self.orders.values() if row["client_order_id"] == client_id)
        previous = int(float(row["filled_qty"]))
        delta = total - previous
        sign = 1 if row["side"] == "buy" else -1
        self.positions[row["symbol"]] = self.positions.get(row["symbol"], 0) + sign * delta
        if self.positions[row["symbol"]] == 0:
            self.positions.pop(row["symbol"])
        row["filled_qty"] = str(total)
        row["filled_avg_price"] = str(price)
        row["filled_at"] = self.clock().isoformat()
        row["status"] = status or ("filled" if total == int(row["qty"]) else "partially_filled")
        return row

    def cancel_order_and_confirm(self, order_id, timeout=6.0):
        self.cancels.append((order_id, self.revision()))
        row = self.orders[order_id]
        total = self.cancel_fill.pop(order_id, None)
        if total is not None:
            self.fill(row["client_order_id"], total, 501.0, status="canceled")
        else:
            row["status"] = "canceled"
        return dict(row)


class Rig:
    def __init__(self, now=moment(SEP30, 19, 5), coin_model=None, executor=None, hooks=None):
        self.clock = Clock(now)
        self.revision = 0
        self.checkpoints = []
        self.booked = []
        self.fail_reasons = set()
        self.holder = {}
        self.broker = FakeBroker(self.clock, lambda: self.revision)

        def save(reason):
            if reason in self.fail_reasons:
                return self.revision
            self.revision += 1
            controller = self.holder.get("controller")
            state = controller.to_json() if controller is not None else None
            self.checkpoints.append((reason, self.revision, state))
            return self.revision

        def book(event):
            self.booked.append(event)
            return True

        self.controller = DayOneController(
            broker=self.broker,
            executor=executor or InlineExecutor(),
            clock=self.clock,
            checkpoint=save,
            book=book,
            spy_reference=lambda _session: 500.0,
            coin_model=coin_model or (lambda _session: {"side": 1, "zmove": 0.02, "sigma": 0.01,
                                                           "history_count": 20}),
            calendar=CALENDAR,
            hooks=hooks or DayOneHooks(admission=lambda *_args: None),
        )
        self.holder["controller"] = self.controller

    def tick(self, value=None):
        if value is not None:
            self.clock.value = value
        self.controller.tick(self.clock.value)

    def lifecycle(self, symbol):
        return self.controller.state["lifecycles"][symbol]


def test_spy_intent_precedes_write_partial_fills_book_once_and_cls_is_immediate():
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    entry = lifecycle["attempts"][0]
    assert (entry["client_id"], entry["time_in_force"], entry["max_remaining_qty"]) == (
        "adt-tom-spy-20261001-entry-1", "opg", 20
    )
    assert rig.broker.lookups[0] == entry["client_id"]
    intent = next(row for row in rig.checkpoints if row[0] == "DAY_ONE_ENTRY_INTENT")
    assert entry["client_id"] in json.dumps(intent[2])
    assert rig.broker.writes[0]["revision"] >= intent[1]

    rig.broker.fill(entry["client_id"], 8, 500.0)
    rig.tick(moment(OCT1, 9, 30))
    rig.tick(moment(OCT1, 9, 30, 1))
    assert lifecycle["entry_qty"] == 8
    assert [row["qty"] for row in rig.booked] == [8]

    rig.broker.fill(entry["client_id"], 20, 501.0)
    rig.tick(moment(OCT1, 9, 30, 2))
    assert [row["qty"] for row in rig.booked] == [8, 12]
    assert lifecycle["entry_qty"] == 20
    exit_attempt = lifecycle["attempts"][-1]
    assert (exit_attempt["role"], exit_attempt["time_in_force"], exit_attempt["max_remaining_qty"]) == (
        "exit", "cls", 20
    )
    assert rig.broker.writes[-1]["client_id"] == "adt-tom-spy-20261001-exit-1"

    rig.broker.fill(exit_attempt["client_id"], 20, 503.0)
    rig.tick(moment(OCT1, 16, 0))
    assert lifecycle["phase"] == "DONE"
    assert lifecycle["released"] is True
    assert lifecycle["entry_qty"] == lifecycle["exit_qty"] == 20
    assert [row["qty"] for row in rig.booked] == [8, 12, 20]


def test_ambiguous_write_uses_lookup_and_never_creates_a_distinct_successor():
    rig = Rig()
    rig.broker.fail_next = "landed_transport"
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    attempt = lifecycle["attempts"][0]
    assert attempt["status"] == "ambiguous"
    assert len(rig.broker.writes) == 1

    rig.tick(moment(SEP30, 19, 5, 1))
    assert len(rig.broker.writes) == 1
    assert len(lifecycle["attempts"]) == 1
    assert attempt["client_id"] == "adt-tom-spy-20261001-entry-1"
    assert attempt["status"] == "accepted"


def test_ambiguous_absent_lookup_reuses_the_same_client_id():
    rig = Rig()
    rig.broker.fail_next = "transport"
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    rig.tick(moment(SEP30, 19, 5, 1))
    assert [row["client_id"] for row in rig.broker.writes] == [
        "adt-tom-spy-20261001-entry-1", "adt-tom-spy-20261001-entry-1"
    ]
    assert len(lifecycle["attempts"]) == 1


def test_emergency_cancel_404_keeps_sent_entry_unresolved_without_fallback():
    rig = Rig()
    rig.broker.fail_next = "transport"
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    attempt = lifecycle["attempts"][0]
    assert attempt["status"] == "ambiguous" and attempt["sent"] is True

    rig.clock.value = moment(SEP30, 19, 5, 1)
    assert rig.controller.request_exit("SPY", "ACCOUNT_LOSS_BREAKER")
    assert attempt["status"] == "ambiguous"
    assert attempt["cancel_intent"]["status"] == "ambiguous"
    assert lifecycle["fallback_eligible"] is False
    assert len(lifecycle["attempts"]) == 1
    assert [row["client_id"] for row in rig.broker.writes] == [attempt["client_id"]]


def test_emergency_cancels_cls_books_race_then_exits_only_the_remainder():
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    entry = lifecycle["attempts"][0]
    rig.broker.fill(entry["client_id"], 20, 500.0)
    rig.tick(moment(OCT1, 9, 30))
    close_order = lifecycle["attempts"][-1]
    close_oid = close_order["order_id"]
    rig.broker.cancel_fill[close_oid] = 7

    rig.clock.value = moment(OCT1, 10, 0)
    assert rig.controller.request_exit("SPY", "ACCOUNT_LOSS_BREAKER")
    recovery = lifecycle["attempts"][-1]
    assert rig.broker.cancels and close_order["cancel_intent"]["status"] == "terminal"
    cancel_checkpoint = next(row for row in rig.checkpoints if row[0] == "DAY_ONE_CANCEL_INTENT")
    assert close_oid in json.dumps(cancel_checkpoint[2])
    assert recovery["role"] == "recovery"
    assert recovery["max_remaining_qty"] == 13
    assert recovery["time_in_force"] == "day"
    assert rig.broker.positions["SPY"] == 13

    rig.broker.fill(recovery["client_id"], 13, 499.0)
    rig.tick(moment(OCT1, 10, 0, 1))
    assert lifecycle["phase"] == "DONE" and lifecycle["released"]
    assert lifecycle["exit_qty"] == 20


def test_precommitted_exit_survives_exit_activation_checkpoint_failure():
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    entry = lifecycle["attempts"][0]
    protection = lifecycle["protection"]
    entry_intent = next(row for row in rig.checkpoints if row[0] == "DAY_ONE_ENTRY_INTENT")
    assert protection["exit_client_id"] in json.dumps(entry_intent[2])
    assert protection["recovery_client_id"] in json.dumps(entry_intent[2])
    rig.fail_reasons.add("DAY_ONE_EXIT_INTENT")
    rig.broker.fill(entry["client_id"], 20, 500.0)
    rig.tick(moment(OCT1, 9, 30))
    exit_attempt = lifecycle["attempts"][-1]
    assert lifecycle["phase"] == "EXIT_PENDING"
    assert exit_attempt["client_id"] == protection["exit_client_id"]
    assert [row["tif"] for row in rig.broker.writes] == ["opg", "cls"]
    assert rig.controller.shutdown(0.0) is True


def test_coin_reserves_before_events_uses_fresh_price_and_dispatches_once_at_0936():
    rig = Rig(now=moment(OCT1, 9, 29))
    assert rig.controller.reserve_coin_before_events(OCT1)
    assert rig.controller.owns("COIN")
    rig.clock.value = moment(OCT1, 9, 35)
    quote = SimpleNamespace(symbol="COIN", bid_price=249.0, ask_price=251.0, timestamp=rig.clock.value)
    assert rig.controller.on_quote(quote)
    rig.tick()
    lifecycle = rig.lifecycle("COIN")
    assert lifecycle["decision_saved"] and lifecycle["admitted"]
    assert not [row for row in rig.broker.writes if row["symbol"] == "COIN"]

    rig.clock.value = moment(OCT1, 9, 36)
    rig.controller.on_quote(SimpleNamespace(symbol="COIN", bid_price=249.0, ask_price=251.0,
                                            timestamp=rig.clock.value))
    rig.tick()
    coin_writes = [row for row in rig.broker.writes if row["symbol"] == "COIN"]
    assert [(row["client_id"], row["qty"], row["tif"]) for row in coin_writes] == [
        ("adt-btc-coin-20261001-entry-1", 20, "day")
    ]
    rig.tick(moment(OCT1, 9, 36, 5))
    assert len([row for row in rig.broker.writes if row["symbol"] == "COIN"]) == 1


def test_short_rule_201_is_saved_as_operational_suppression_after_model_decision():
    model = lambda _session: {
        "side": -1,
        "zmove": -0.02,
        "sigma": 0.01,
        "history_count": 20,
        "prior_low": 89.0,
        "close_two_back": 100.0,
        "today_low_through_0934": 100.0,
        "prior_close": 100.0,
    }
    rig = Rig(now=moment(OCT1, 9, 35), coin_model=model)
    assert rig.controller.reserve_coin_before_events(OCT1)
    rig.controller.on_quote(SimpleNamespace(symbol="COIN", bid_price=249.0, ask_price=251.0,
                                            timestamp=rig.clock.value))
    rig.tick()
    lifecycle = rig.lifecycle("COIN")
    assert lifecycle["decision"]["side"] == -1
    assert lifecycle["phase"] == "SKIPPED" and "RULE_201" in lifecycle["operational_gates"]
    assert not [row for row in rig.broker.writes if row["symbol"] == "COIN"]


def test_recovery_queues_opg_before_0928_and_blocks_a_successor_session():
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    entry = lifecycle["attempts"][0]
    rig.broker.fill(entry["client_id"], 20, 500.0)
    rig.tick(moment(OCT1, 9, 30))
    close_attempt = lifecycle["attempts"][-1]
    rig.broker.orders[close_attempt["order_id"]]["status"] = "rejected"
    rig.tick(moment(OCT1, 16, 0))
    rig.tick(moment(date(2026, 10, 2), 9, 20))
    recovery = lifecycle["attempts"][-1]
    assert recovery["role"] == "recovery" and recovery["time_in_force"] == "opg"
    original_session = lifecycle["session"]
    rig.tick(moment(date(2026, 10, 2), 19, 5))
    assert rig.lifecycle("SPY")["session"] == original_session


def test_state_validation_fences_and_bounded_drain_fail_closed():
    rig = Rig()
    rig.tick()
    state = rig.controller.to_json()
    duplicate = dict(state["lifecycles"]["SPY"]["attempts"][0])
    duplicate["client_id"] = "adt-tom-spy-20261001-entry-2"
    duplicate["attempt"] = 2
    state["lifecycles"]["SPY"]["attempts"].append(duplicate)
    with pytest.raises(ValueError):
        DayOneController.load(
            state,
            broker=rig.broker,
            executor=InlineExecutor(),
            clock=rig.clock,
            checkpoint=lambda _reason: 1,
            book=lambda _event: True,
            spy_reference=lambda _session: 500.0,
            coin_model=lambda _session: {"side": 0},
            calendar=CALENDAR,
        )

    class ManualExecutor:
        def __init__(self):
            self.future = Future()

        def submit(self, _work):
            return self.future

    waiting = Rig(executor=ManualExecutor())
    waiting.tick()
    waiting.controller.close_entry_writes()
    assert waiting.controller.drain(0.0) is False
    health = waiting.controller.health()
    assert health["entry_writes_open"] is False and health["exit_writes_open"] is True


def test_spy_definite_zero_fill_opg_uses_one_reconciled_market_fallback():
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    first = lifecycle["attempts"][0]
    rig.broker.orders[first["order_id"]]["status"] = "canceled"
    rig.tick(moment(OCT1, 9, 30))
    assert lifecycle["fallback_eligible"] is True
    rig.tick(moment(OCT1, 9, 30, 1))
    second = lifecycle["attempts"][-1]
    assert len(lifecycle["attempts"]) == 2
    assert (second["client_id"], second["time_in_force"]) == (
        "adt-tom-spy-20261001-entry-2", "day"
    )
    assert "SPY_OPEN_MARKET_FALLBACK" in lifecycle["execution_deviations"]


def test_early_close_cls_deadline_is_enforced_by_the_controller():
    early = date(2026, 11, 27)
    rig = Rig(now=moment(OCT1, 9, 0))
    before = rig.controller._new_lifecycle("COIN", early, dtime(13, 0), "HELD")
    before.update(side="LONG", target_qty=5, entry_qty=5, admitted=True)
    rig.controller.state["lifecycles"]["COIN"] = before
    rig.clock.value = datetime.combine(early, dtime(12, 49, 59), d1.ET)
    rig.controller._ensure_cls(before, rig.clock.value)
    assert before["attempts"][-1]["time_in_force"] == "cls"

    after = rig.controller._new_lifecycle("COIN", early, dtime(13, 0), "HELD")
    after.update(side="LONG", target_qty=5, entry_qty=5, admitted=True)
    rig.controller.state["lifecycles"]["COIN"] = after
    rig.clock.value = datetime.combine(early, dtime(12, 50), d1.ET)
    rig.controller._ensure_cls(after, rig.clock.value)
    assert after["attempts"] == []
    assert after["phase"] == "RECOVERY"
    assert "CLS_DEADLINE_MISSED" in after["execution_deviations"]


def _delayed_spy_to_pre_intent(rig, executor):
    rig.tick()
    executor.run_next()
    rig.tick()
    assert executor.jobs


def _apply_gate_failure(rig, gates, failure):
    if failure == "persistence":
        gates["persistence"] = False
    elif failure == "mismatch":
        gates["mismatch"] = True
    elif failure == "account":
        rig.broker.account["account_number"] = "WRONG"
    elif failure == "ownership":
        gates["owner"] = "ORB"
    elif failure == "slots":
        gates["admission"] = "DAY_ONE_POSITION_SLOTS_FULL"
    elif failure == "buying_power":
        rig.broker.account["buying_power"] = 1.0
    elif failure == "size":
        gates["admission"] = "DAY_ONE_SIZE_INVALID"


@pytest.mark.parametrize("failure", [
    "persistence", "mismatch", "account", "ownership", "slots", "buying_power", "size",
])
def test_delayed_executor_rechecks_every_gate_immediately_before_intent(failure):
    executor = DelayedExecutor()
    gates = {"persistence": True, "mismatch": False, "owner": None, "admission": None}
    hooks = DayOneHooks(
        persistence_healthy=lambda: gates["persistence"],
        broker_mismatch=lambda: gates["mismatch"],
        held_by_other=lambda _symbol: gates["owner"],
        admission=lambda *_args: gates["admission"],
    )
    rig = Rig(executor=executor, hooks=hooks)
    _delayed_spy_to_pre_intent(rig, executor)
    _apply_gate_failure(rig, gates, failure)
    executor.run_next()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    assert lifecycle["phase"] == "SKIPPED"
    assert lifecycle["attempts"] == []
    assert rig.broker.writes == []


@pytest.mark.parametrize("failure", [
    "persistence", "mismatch", "account", "ownership", "slots", "buying_power", "size",
])
def test_delayed_executor_rechecks_every_gate_immediately_before_post(failure):
    executor = DelayedExecutor()
    gates = {"persistence": True, "mismatch": False, "owner": None, "admission": None}
    hooks = DayOneHooks(
        persistence_healthy=lambda: gates["persistence"],
        broker_mismatch=lambda: gates["mismatch"],
        held_by_other=lambda _symbol: gates["owner"],
        admission=lambda *_args: gates["admission"],
    )
    rig = Rig(executor=executor, hooks=hooks)
    _delayed_spy_to_pre_intent(rig, executor)
    executor.run_next()
    rig.tick()
    attempt = rig.lifecycle("SPY")["attempts"][0]
    assert attempt["status"] == "intent" and executor.jobs
    _apply_gate_failure(rig, gates, failure)
    executor.run_next()
    rig.tick()
    assert rig.lifecycle("SPY")["phase"] == "SKIPPED"
    assert attempt["status"] == "rejected"
    assert rig.broker.writes == []


def test_delayed_unsent_spy_entry_crossing_cutoff_terminally_skips():
    executor = DelayedExecutor()
    rig = Rig(executor=executor)
    _delayed_spy_to_pre_intent(rig, executor)
    executor.run_next()
    rig.tick()
    attempt = rig.lifecycle("SPY")["attempts"][0]
    rig.clock.value = moment(OCT1, 9, 27)
    executor.run_next()
    rig.tick()
    assert attempt["status"] == "expired"
    assert rig.lifecycle("SPY")["phase"] == "SKIPPED"
    assert rig.lifecycle("SPY")["fallback_eligible"] is False
    assert rig.broker.writes == []


def test_delayed_unsent_coin_entry_crossing_cutoff_terminally_skips():
    executor = DelayedExecutor()
    rig = Rig(now=moment(OCT1, 9, 36), executor=executor)
    lifecycle = rig.controller._new_lifecycle("COIN", OCT1, dtime(16), "RESERVED")
    lifecycle.update(
        side="LONG",
        decision={"side": 1},
        decision_saved=True,
        target_qty=20,
        reference_price=250.0,
        sizing_equity=50_000.0,
        sizing_buying_power=100_000.0,
        admitted=True,
    )
    rig.controller.state["lifecycles"]["COIN"] = lifecycle
    attempt = rig.controller._create_attempt(lifecycle, "entry", "buy", 20, "day", rig.clock.value)
    rig.clock.value = moment(OCT1, 9, 36, 6)
    executor.run_next()
    rig.tick()
    assert attempt["status"] == "expired"
    assert lifecycle["phase"] == "SKIPPED"
    assert rig.broker.writes == []


def test_sent_ambiguous_entry_crossing_cutoff_stays_unresolved():
    rig = Rig()
    rig.broker.fail_next = "transport"
    rig.tick()
    attempt = rig.lifecycle("SPY")["attempts"][0]
    rig.tick(moment(OCT1, 9, 27))
    assert attempt["status"] == "ambiguous"
    assert rig.lifecycle("SPY")["phase"] == "ENTRY_PENDING"
    assert len(rig.broker.writes) == 1


def test_coin_rule_201_low_uses_only_0930_through_0934_regular_bars():
    rig = Rig(now=moment(OCT1, 9, 35))
    assert rig.controller.reserve_coin_before_events(OCT1)
    bars = [
        (moment(OCT1, 9, 29), 10.0),
        (moment(OCT1, 9, 30), 90.0),
        (moment(OCT1, 9, 34), 80.0),
        (moment(OCT1, 9, 35), 5.0),
    ]
    for timestamp, low in bars:
        rig.clock.value = timestamp + timedelta(minutes=1)
        assert rig.controller.on_bar(SimpleNamespace(
            symbol="COIN", open=100.0, high=101.0, low=low, close=100.0, timestamp=timestamp
        ))
    assert rig.controller._coin_lows[OCT1.isoformat()] == 80.0


@pytest.mark.parametrize("asset", [
    {"symbol": "WRONG", "tradable": True, "shortable": True, "easy_to_borrow": True},
    {"symbol": "SPY", "tradable": 1, "shortable": True, "easy_to_borrow": True},
    {"symbol": "SPY", "shortable": True, "easy_to_borrow": True},
])
def test_asset_requires_exact_symbol_and_tradable_true(asset):
    rig = Rig()
    rig.broker.assets["SPY"] = asset
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    assert lifecycle["phase"] == "SKIPPED"
    assert "ASSET_NOT_TRADABLE" in lifecycle["operational_gates"]
    assert rig.broker.writes == []


def test_boundary_calendar_must_match_every_local_session():
    rig = Rig()
    original = rig.broker.get_calendar

    def incomplete(start, end):
        return original(start, end)[1:]

    rig.broker.get_calendar = incomplete
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    assert lifecycle["phase"] == "SKIPPED"
    assert "CALENDAR_DISAGREES" in lifecycle["operational_gates"]
    assert rig.broker.writes == []


@pytest.mark.parametrize("field,value", [("filled_qty", "0.5"), ("qty", "20.5")])
def test_fractional_broker_order_quantities_enter_recovery_halt(field, value):
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    attempt = lifecycle["attempts"][0]
    rig.broker.orders[attempt["order_id"]][field] = value
    rig.tick(moment(SEP30, 19, 6))
    assert lifecycle["phase"] == "RECOVERY_HALT"


def test_fractional_broker_position_quantity_enters_recovery_halt():
    rig = Rig()
    rig.broker.positions["SPY"] = 0.5
    rig.tick()
    assert rig.lifecycle("SPY")["phase"] == "RECOVERY_HALT"
    assert rig.broker.writes == []


def test_reconcile_keeps_inflight_jobs_and_shutdown_rejects_unprotected_halt():
    executor = DelayedExecutor()
    rig = Rig(executor=executor)
    rig.tick()
    pending = dict(rig.controller._jobs)
    rig.controller.reconcile(rig.clock.value)
    assert rig.controller._jobs == pending

    lifecycle = rig.controller._new_lifecycle("COIN", OCT1, dtime(16), "RECOVERY_HALT")
    lifecycle.update(side="LONG", target_qty=5, entry_qty=5, reference_price=250.0)
    rig.controller.state["lifecycles"] = {"COIN": lifecycle}
    rig.broker.positions["COIN"] = 5
    rig.fail_reasons.add("DAY_ONE_PROTECTION_RECOVERY")
    assert rig.controller.shutdown(0.0) is False
    assert rig.controller.health()["exit_writes_open"] is True


def test_rejected_or_unsent_close_is_not_protection_and_shutdown_is_unsafe():
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    entry = lifecycle["attempts"][0]
    rig.broker.fill(entry["client_id"], 20, 500.0)
    rig.tick(moment(OCT1, 9, 30))
    close = lifecycle["attempts"][-1]
    rig.broker.orders[close["order_id"]]["status"] = "rejected"
    rig.tick(moment(OCT1, 9, 31))
    assert not rig.controller.protected("SPY")
    assert rig.controller.health()["ready"] is False
    assert rig.controller.shutdown(0.0) is False
    assert rig.controller.health()["exit_writes_open"] is True


def test_resting_auction_order_polls_once_a_minute_until_auction_window():
    rig = Rig()
    rig.tick()
    lifecycle = rig.lifecycle("SPY")
    attempt = lifecycle["attempts"][0]
    initial_lookups = len(rig.broker.lookups)
    for second in (1, 10, 30, 59):
        rig.tick(moment(SEP30, 19, 5, second))
    assert len(rig.broker.lookups) == initial_lookups
    rig.tick(moment(SEP30, 19, 6))
    assert len(rig.broker.lookups) == initial_lookups + 1

    attempt["last_read_at"] = moment(OCT1, 9, 29, 58).isoformat()
    rig.tick(moment(OCT1, 9, 29, 59))
    assert len(rig.broker.lookups) == initial_lookups + 1
    rig.tick(moment(OCT1, 9, 30))
    assert len(rig.broker.lookups) == initial_lookups + 2


def test_a_failing_gate_is_noted_once_and_retried_slowly_then_skipped_at_cutoff():
    from types import SimpleNamespace
    from backend.app.core.day_one_execution import DayOneHooks
    rig = Rig(now=moment(OCT1, 9, 0), hooks=DayOneHooks(admission=lambda *a: None, broker_mismatch=lambda: True))
    rig.controller.reserve_coin_before_events(OCT1)
    start = moment(OCT1, 9, 35)
    before = len(rig.checkpoints)
    for i in range(3600):
        rig.tick(start + timedelta(seconds=i))
    lc = rig.lifecycle("COIN")
    assert lc["operational_gates"].count("BROKER_MISMATCH") == 1      # was 3,600 entries
    assert len(rig.checkpoints) - before < 20                          # was 3,601 saves an hour
    assert lc["phase"] == "SKIPPED" and lc["released"]
    assert rig.broker.writes == []


def test_refused_exits_are_paced_and_the_next_open_still_gets_an_exit():
    class Refused(Exception):
        status_code = 403
        body = '{"message":"insufficient qty"}'

    rig = Rig(now=moment(SEP30, 19, 5))
    rig.tick()
    lc = rig.lifecycle("SPY")
    rig.broker.fill(lc["attempts"][0]["client_id"], 20, 500.0)
    rig.tick(moment(OCT1, 9, 30, 1))
    cls = lc["attempts"][-1]
    next(r for r in rig.broker.orders.values() if r["client_order_id"] == cls["client_id"])["status"] = "rejected"
    posts = []

    def refuse(symbol, side, qty, cid):
        posts.append(cid)
        raise Refused("refused")

    rig.broker.submit_market_order = refuse
    for i in range(4 * 60):
        rig.tick(moment(OCT1, 15, 58) + timedelta(seconds=i))
    assert 1 <= len(posts) <= 3                    # was 10, then a halt at the 13th
    assert lc["phase"] != "RECOVERY_HALT"
    writes = len(rig.broker.writes)
    for i in range(120):
        rig.tick(moment(date(2026, 10, 2), 9, 0) + timedelta(seconds=i))
    assert len(rig.broker.writes) - writes == 1    # the opening auction exit is queued
    assert lc["phase"] == "EXIT_PENDING"


def test_an_unknown_outcome_is_looked_up_every_5_seconds_not_every_tick():
    rig = Rig(now=moment(SEP30, 19, 5))

    def failing(*a, **k):
        rig.broker.writes.append("x")
        raise ConnectionError("timeout")

    rig.broker.submit_on_auction = failing
    for i in range(60):
        rig.tick(moment(SEP30, 19, 5) + timedelta(seconds=i))
    assert rig.lifecycle("SPY")["attempts"][0]["status"] == "ambiguous"
    assert len(rig.broker.writes) <= 15          # was 60 a minute
    assert len({a["client_id"] for a in rig.lifecycle("SPY")["attempts"]}) == 1


def test_a_coin_session_missed_entirely_is_released_the_next_day():
    rig = Rig(now=moment(OCT1, 0, 30))
    rig.controller.reserve_coin_before_events(OCT1)
    assert rig.lifecycle("COIN")["phase"] == "RESERVED"
    rig.tick(moment(date(2026, 10, 2), 0, 30))       # down from before 09:35 until after the close
    lc = rig.lifecycle("COIN")
    assert lc["phase"] == "SKIPPED" and lc["released"]
    assert rig.controller.reserve_coin_before_events(date(2026, 10, 2))
    assert not [w for w in rig.broker.writes if "coin" in str(w.get("client_id", "")).lower()]
