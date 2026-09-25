"""Research recording (PLAN_2026_09_25_backtest_tracking.md).

Recording is observation only: these tests check the rows are right AND that a
broken recorder cannot change a single trading outcome.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from types import SimpleNamespace

import pytest

from backend.app.core.research import (
    ResearchRecorder,
    excursion_summary,
    fold_bar,
    json_safe,
    new_excursion,
)
from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.base import SignalEvent, attach_features

T0 = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)  # 10:00 ET


def _bar(minute: int, o: float, h: float, l: float, c: float, sym: str = "AAPL") -> BarEvent:
    return BarEvent(symbol=sym, open=o, high=h, low=l, close=c, volume=200_000,
                    timestamp=T0 + timedelta(minutes=minute))


def _signal(side=OrderSide.BUY, entry=100.0, stop=99.0, strategy="news_momentum", minute=0):
    d = 1 if side == OrderSide.BUY else -1
    sig = SignalEvent(
        symbol="AAPL", side=side, order_type=OrderType.MARKET, entry_price=entry,
        stop_loss=stop, take_profit_1=entry + d * 0.8, take_profit_2=entry + d * 1.8,
        strategy_id=strategy, confidence=0.9, reason="RESEARCH_TEST",
        timestamp=T0 + timedelta(minutes=minute),
    )
    sig.features = {"structural_stop": stop - d * 0.1, "volume_ratio": 3.2}
    return sig


@pytest.fixture
def rt(monkeypatch):
    import backend.app.main as runtime
    # No SPY/QQQ bars in these tests: take the market-direction filter out of
    # admission (it is covered by its own tests).
    monkeypatch.setattr(runtime.adaptation_engine, "market_filter", None)
    runtime.reset_runtime_state()
    runtime.set_simulation_mode(True)
    runtime.research_recorder.recent["trades"].clear()
    runtime.research_recorder.recent["signals"].clear()
    runtime.latest_market_prices["AAPL"] = 100.0
    yield runtime
    runtime.reset_runtime_state()
    runtime.set_simulation_mode(False)


async def _run_long_trade(rt, side=OrderSide.BUY):
    """Signal at 10:00, fill on the 10:01 bar, T1 then stop-out."""
    d = 1 if side == OrderSide.BUY else -1
    sig = _signal(side=side, entry=100.0, stop=100.0 - d * 1.0)
    await rt.execute_strategy_signal(sig)
    # 10:01 fills the market entry at the open (100.0).
    await rt.handle_bar_event(_bar(1, 100.0, 100.0 + d * 0.05 if d > 0 else 100.05, 99.95, 100.0))
    bid = rt.bracket_manager.symbol_to_bracket.get("AAPL")
    assert bid, "entry should have filled and opened a bracket"
    br = rt.bracket_manager.brackets[bid]
    t1 = br.target_1_price
    # 10:02 runs to T1 and a bit beyond, 10:03 drifts, 10:04 hits the moved stop.
    if d > 0:
        await rt.handle_bar_event(_bar(2, 100.0, t1 + 0.2, 99.9, t1))
        await rt.handle_bar_event(_bar(3, t1, t1 + 0.1, t1 - 0.1, t1 - 0.05))
        await rt.handle_bar_event(_bar(4, t1 - 0.05, t1, 99.0, 99.2))
    else:
        await rt.handle_bar_event(_bar(2, 100.0, 100.1, t1 - 0.2, t1))
        await rt.handle_bar_event(_bar(3, t1, t1 + 0.1, t1 - 0.1, t1 + 0.05))
        await rt.handle_bar_event(_bar(4, t1 + 0.05, 101.0, t1, 100.8))
    return bid


@pytest.mark.asyncio
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
async def test_closed_trade_row_has_r_excursion_stops_and_exit_intent(rt, side):
    bid = await _run_long_trade(rt, side)
    trades = [r for r in rt.research_recorder.recent["trades"] if r.get("trade_id") == bid]
    assert len(trades) == 1, "exactly one research row per closed trade"
    row = trades[0]
    ledger = rt.pending_trade_records[bid]
    assert row["complete"] is True
    assert row["execution_mode"] == "replay"
    assert row["ledger_row"]["realized_pnl"] == ledger["realized_pnl"]
    q = row["quantities"]
    assert q["balanced"] and q["entry_filled_qty"] == q["exited_qty"] == ledger["quantity"]
    assert q["admission_qty"] is not None and q["risk_authorized_qty"] is not None
    risk = row["risk"]
    rps = risk["risk_per_share_at_fill"]
    assert rps == pytest.approx(abs(row["prices"]["avg_entry"] - row["stops"]["stop_at_entry"]), abs=1e-4)
    assert row["result"]["realized_r"] == pytest.approx(ledger["realized_pnl"] / (rps * q["entry_filled_qty"]), abs=1e-3)
    exc = row["excursion"]
    assert exc["mfe_r"] > 0.8, "price ran past target 1"
    assert exc["mae_r"] > 0.0
    assert exc["coverage_complete"] is True, exc["coverage_notes"]
    roles = [f["role"] for f in row["exit_fills"]]
    assert "TAKE_PROFIT_1" in roles and "STOP_LOSS" in roles
    stop_fill = next(f for f in row["exit_fills"] if f["role"] == "STOP_LOSS")
    assert stop_fill["stop_regime"] == "moved_stop_after_target_1"
    assert row["stops"]["adapted_stop"] is not None
    assert row["stops"]["structural_stop"] is not None
    assert row["signal"]["features"]["volume_ratio"] == 3.2
    assert row["context"]["strategy_params"]["volume_surge_multiplier"] == 2.0
    assert row["targets"]["planned_target_1_qty"] + row["targets"]["planned_target_2_qty"] == q["entry_filled_qty"]
    assert row["timestamps"]["entry_fills"][0]["fill_ts_source"] == "simulator"
    # Open-trade state is released once the row is written.
    assert bid not in rt.research_tracker.state["brackets"]
    # The signal row was written with its admission stages.
    sigs = [r for r in rt.research_recorder.recent["signals"] if r["outcome"] == "SUBMITTED"]
    assert sigs and sigs[-1]["stages"]["bracket_id"] == bid
    assert sigs[-1]["row_id"] == row["signal_id"]


@pytest.mark.asyncio
async def test_blocked_signal_is_recorded_with_its_stage(rt):
    await rt.execute_strategy_signal(_signal())
    await rt.execute_strategy_signal(_signal(minute=0))  # same symbol, entry still working
    outcomes = [r["outcome"] for r in rt.research_recorder.recent["signals"]]
    assert outcomes[-1] == "DUPLICATE"
    row = rt.research_recorder.recent["signals"][-1]
    assert row["stages"]["decision_wall_at"]
    assert row["signal"]["floored_stop"] == 99.0
    assert "scope_note" in row


@pytest.mark.asyncio
async def test_research_failures_never_change_trading(rt, monkeypatch):
    """Same trade with every research hook raising: identical ledger outcome."""
    bid = await _run_long_trade(rt)
    baseline = dict(rt.pending_trade_records[bid])
    baseline_equity = rt.account.equity

    rt.reset_runtime_state()
    rt.set_simulation_mode(True)
    rt.latest_market_prices["AAPL"] = 100.0

    def boom(*_a, **_k):
        raise RuntimeError("research exploded")

    for name in ("record_signal", "open_bracket", "on_activation", "on_fill", "on_bar", "prune", "complete_bracket"):
        monkeypatch.setattr(rt.research_tracker, name, boom)
    errors_before = rt.research_recorder.errors
    bid2 = await _run_long_trade(rt)
    trade = rt.pending_trade_records[bid2]
    for key in ("realized_pnl", "quantity", "avg_entry_price", "avg_exit_price", "exit_reason", "side"):
        assert trade[key] == baseline[key], key
    assert rt.account.equity == pytest.approx(baseline_equity)
    assert rt.research_recorder.errors > errors_before
    assert not rt.bracket_manager.symbol_to_bracket  # bracket closed normally


@pytest.mark.asyncio
async def test_checkpoint_carries_open_trade_research_and_restores(rt):
    await rt.execute_strategy_signal(_signal())
    await rt.handle_bar_event(_bar(1, 100.0, 100.05, 99.95, 100.0))
    await rt.handle_bar_event(_bar(2, 100.0, 100.3, 99.7, 100.1))
    state = rt.research_tracker.to_state()
    raw = json.dumps(state, allow_nan=False)
    bid = rt.bracket_manager.symbol_to_bracket["AAPL"]
    assert state["brackets"][bid]["excursion"]["bars"] == 1
    rt.research_tracker.state = {"brackets": {}, "swing": {}}
    rt.research_tracker.load_state(json.loads(raw))
    assert rt.research_tracker.state["brackets"][bid]["excursion"]["high"] >= 100.3
    # capture_runtime_state carries it as an optional top-level key.
    from backend.app.core.runtime_state import capture_runtime_state  # noqa: F401
    assert "research" in rt._capture_checkpoint()


def test_state_with_nan_is_json_safe_and_oversize_is_omitted(monkeypatch):
    import backend.app.core.research_tracker as rtmod
    rec = ResearchRecorder(None)
    tracker = rtmod.ResearchTracker(
        rec, bracket_manager=SimpleNamespace(brackets={}, order_to_bracket={}), entry_order_to_bracket={},
        account=None, adaptation_engine=None, market_filter=lambda: None, risk_engine=None,
        strategy_map={}, swing_staged_order_manager=None, execution_mode=lambda: "simulated",
        committed_count=lambda: 0,
    )
    tracker.state["brackets"]["b"] = {"x": float("nan"), "t": T0}
    out = tracker.to_state()
    assert out["brackets"]["b"] == {"x": None, "t": T0.isoformat()}
    monkeypatch.setattr(rtmod, "MAX_STATE_BYTES", 10)
    assert tracker.to_state() is None and rec.errors == 1


def test_recorder_disk_round_trip_dedupes_and_pages(tmp_path):
    rec = ResearchRecorder(str(tmp_path / "research.sqlite3"))
    for i in range(5):
        rec.record("signals", f"s{i}", {"row_id": f"s{i}", "session_date": "2026-09-24", "strategy_id": "orb",
                                        "value": float("inf") if i == 0 else i, "at": T0})
    rec.record("signals", "s1", {"row_id": "s1", "session_date": "2026-09-24", "strategy_id": "orb", "value": 99})
    assert rec.flush(5)
    rows = rec.list("signals", since="2026-09-24", limit=3)
    assert [r["row_id"] for r in rows] == ["s0", "s1", "s2"]
    assert rows[0]["value"] is None and rows[1]["value"] == 1  # inf -> null, duplicate ignored
    more = rec.list("signals", since="2026-09-24", limit=3, after=f"2026-09-24|{rows[-1]['row_id']}")
    assert [r["row_id"] for r in more] == ["s3", "s4"]
    assert rec.health()["written"] == 5 and rec.health()["dropped"] == 0


def test_full_queue_drops_instead_of_blocking(tmp_path):
    import queue as q
    rec = ResearchRecorder(str(tmp_path / "r.sqlite3"))
    rec._queue = q.Queue(maxsize=1)
    rec._queue.put(("research_signals", "x", "", "", "", "{}"))  # nobody drains this queue
    started = datetime.now(timezone.utc)
    assert rec.record("signals", "y", {"row_id": "y"}) is False
    assert (datetime.now(timezone.utc) - started).total_seconds() < 0.5
    assert rec.dropped == 1


def test_unwritable_research_path_falls_back_to_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    rec = ResearchRecorder(str(blocker / "sub" / "r.sqlite3"))
    assert rec.path is None and rec.errors == 1
    assert rec.record("trades", "t", {"row_id": "t"}) is True
    assert rec.list("trades")[0]["row_id"] == "t"


def test_excursion_coverage_flags_gaps_and_late_starts():
    exc = new_excursion()
    fold_bar(exc, T0 + timedelta(minutes=1), 101.0, 99.5)
    fold_bar(exc, T0 + timedelta(minutes=1), 150.0, 10.0)  # duplicate bar ignored
    fold_bar(exc, T0 + timedelta(minutes=4), 102.0, 99.0)  # 2-minute gap
    s = excursion_summary(exc, "LONG", 100.0, 1.0, T0, T0 + timedelta(minutes=4), True)
    assert s["mfe_r"] == 2.0 and s["mae_r"] == 1.0
    assert s["coverage_complete"] is False and any("gap" in n for n in s["coverage_notes"])
    s2 = excursion_summary(exc, "SHORT", 100.0, 1.0, T0, T0 + timedelta(minutes=4), False)
    assert s2["mfe_r"] == 1.0 and s2["mae_r"] == 2.0 and s2["coverage_complete"] is False


def test_attach_features_failure_keeps_signal():
    sig = _signal()
    attach_features(sig, lambda: {"x": 1 / 0})
    assert "ZeroDivisionError" in sig.features["error"]


def test_json_safe_handles_enums_nan_and_nesting():
    assert json_safe({"a": [math.nan, OrderSide.BUY, (1, 2)]}) == {"a": [None, "BUY", [1, 2]]}


def test_orb_signal_carries_decision_features():
    from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
    orb = OpeningRangeBreakoutStrategy()
    base = datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc)
    sigs = []
    for i in range(5):
        sigs += orb.on_bar(BarEvent("NVDA", 100, 100.5, 99.5, 100, 100_000, base + timedelta(minutes=i)))
    for i in range(5, 12):
        sigs += orb.on_bar(BarEvent("NVDA", 100, 100.4, 99.8, 100.1, 100_000, base + timedelta(minutes=i)))
    sigs += orb.on_bar(BarEvent("NVDA", 100.4, 101.0, 100.4, 100.95, 600_000, base + timedelta(minutes=12)))
    assert sigs, "breakout bar should signal"
    f = sigs[-1].features
    assert f["range_high"] == 100.5 and f["range_low"] == 99.5 and f["rvol"] >= 1.8
    assert f["structural_stop"] == 100.0 and "error" not in f


def _swing_tracker():
    import backend.app.core.research_tracker as rtmod
    rec = ResearchRecorder(None)
    positions = {}
    staged = SimpleNamespace(get_staged_entries=lambda: [], get_staged_exits=lambda: [
        SimpleNamespace(symbol="MU", reason="TARGET_REACHED")])
    tracker = rtmod.ResearchTracker(
        rec, bracket_manager=SimpleNamespace(brackets={}, order_to_bracket={}), entry_order_to_bracket={},
        account=SimpleNamespace(positions=positions, equity=1.0, daily_starting_equity=1.0),
        adaptation_engine=SimpleNamespace(), market_filter=lambda: None, risk_engine=None,
        strategy_map={}, swing_staged_order_manager=staged, execution_mode=lambda: "alpaca_paper",
        committed_count=lambda: 0,
    )
    return tracker, rec, positions


def _swing_fill(side, qty, price, ts):
    order = SimpleNamespace(id=f"o{side}{ts.minute}", symbol="MU", arm=SimpleNamespace(value="SWING"),
                            strategy_id="swing_panic_dip", order_type=OrderType.MARKET,
                            broker_fill_timestamp=ts, swing_entry_atr=4.0, bracket_role=None)
    fill = SimpleNamespace(fill_id=f"f{side}{ts.minute}", order_id=order.id, side=side, qty=qty,
                           price=price, fee=0.0, slippage=0.02, realized_pnl=0.0 if side == OrderSide.BUY else (price - 100.0) * qty,
                           timestamp=ts)
    return order, fill


def test_swing_round_trip_is_recorded_across_days():
    tracker, rec, positions = _swing_tracker()
    t_in = datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc)
    positions["MU"] = SimpleNamespace(shares=10, stop_loss_price=90.0)
    tracker.on_fill(*_swing_fill(OrderSide.BUY, 10, 100.0, t_in), True)
    tracker.on_bar(SimpleNamespace(symbol="MU", timestamp=t_in + timedelta(minutes=1), high=104.0, low=97.0))
    tracker.on_bar(SimpleNamespace(symbol="MU", timestamp=t_in + timedelta(days=1), high=108.0, low=99.0))
    assert tracker.state["swing"]["MU"]["stop"] == 90.0
    positions.pop("MU")
    tracker.on_fill(*_swing_fill(OrderSide.SELL, 10, 106.0, t_in + timedelta(days=2)), True)
    row = rec.recent["trades"][-1]
    assert row["kind"] == "SWING" and row["complete"]
    assert row["result"]["realized_r"] == pytest.approx(60.0 / 100.0)  # $60 on $10/share x 10 risk
    assert row["excursion"]["mfe_r"] == pytest.approx(0.8) and row["excursion"]["mae_r"] == pytest.approx(0.3)
    assert row["exit_fills"][0]["exit_intent"] == "TARGET_REACHED"
    assert row["exit_fills"][0]["fill_ts_source"] == "alpaca_filled_at"
    assert "MU" not in tracker.state["swing"]
    assert row["fees_known"] is False


@pytest.mark.asyncio
async def test_research_api_serves_rows(rt):
    rt.research_recorder.record("trades", "replay:brk_x", {"row_id": "replay:brk_x", "session_date": "2026-09-24"})
    out = await rt.get_research_rows("trades", since="2026-09-24", limit=10, after=None)
    assert out["count"] >= 1 and any(r["row_id"] == "replay:brk_x" for r in out["rows"])
    with pytest.raises(Exception):
        await rt.get_research_rows("bogus", since=None, limit=10, after=None)
    with pytest.raises(Exception):
        await rt.get_research_rows("trades", since="yesterday", limit=10, after=None)
