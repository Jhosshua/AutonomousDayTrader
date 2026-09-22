from datetime import datetime, timezone
import json
import sqlite3

import pytest

from backend.app.core.account import PaperTradingAccount
from backend.app.core.bracket import BracketChildType, DynamicBracketManager
from backend.app.core.engine import BracketRole, ExecutionEngine, OrderSide, OrderType
from backend.app.core.flattening import ZeroOvernightFlatteningEngine
from backend.app.core.persistence import PersistenceError, TradingStateStore
from backend.app.core.risk import InstitutionalRiskEngine
from backend.app.core.runtime_state import capture_runtime_state, restore_runtime_state
from backend.app.models.events import BarEvent, QuoteEvent
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.mean_reversion import MeanReversionStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy


def _components():
    account = PaperTradingAccount()
    engine = ExecutionEngine(account)
    brackets = DynamicBracketManager()
    risk = InstitutionalRiskEngine()
    flattening = ZeroOvernightFlatteningEngine()
    adaptation = DynamicAdaptationEngine()
    strategies = [
        OpeningRangeBreakoutStrategy(),
        VWAPPullbackStrategy(),
        NewsMomentumStrategy(),
        MeanReversionStrategy(),
    ]
    return account, engine, brackets, risk, flattening, adaptation, strategies


def _active_position_runtime():
    account, engine, brackets, risk, flattening, adaptation, strategies = _components()
    timestamp = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    entry = engine.create_order(
        "AAPL", OrderSide.BUY, OrderType.MARKET, 10, estimated_price=100.0, strategy_id="orb"
    )
    engine.submit_order(entry.id)
    engine.process_bar("AAPL", 100.0, 100.2, 99.8, 100.0, 10000, timestamp)
    bracket = brackets.create_bracket(
        f"brk_{entry.id}", "AAPL", "LONG", 10, 100.0, 99.0, strategy_id="orb", timestamp=timestamp
    )
    brackets.activate_bracket_on_fill(bracket.bracket_id, 10, entry.avg_fill_price, timestamp)
    stop = engine.create_order(
        "AAPL",
        OrderSide.SELL,
        OrderType.STOP,
        10,
        stop_price=99.0,
        strategy_id="orb",
        bracket_role=BracketRole.STOP_LOSS,
        parent_order_id=bracket.bracket_id,
    )
    engine.submit_order(stop.id)
    brackets.order_to_bracket.pop(bracket.stop_order_id, None)
    brackets.order_to_bracket[stop.id] = (bracket.bracket_id, BracketChildType.STOP_LOSS)
    bracket.stop_order_id = stop.id
    strategies[0].on_bar(
        BarEvent("AAPL", 100, 101, 99, 100.5, 5000, timestamp)
    )
    return account, engine, brackets, risk, flattening, adaptation, strategies, entry, bracket


def test_atomic_checkpoint_restores_account_orders_bracket_and_strategy_state(tmp_path):
    account, engine, brackets, risk, flattening, adaptation, strategies, entry, bracket = _active_position_runtime()
    store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    timestamp_date = datetime(2026, 9, 22, tzinfo=timezone.utc).date()
    payload = capture_runtime_state(
        account=account,
        engine=engine,
        bracket_manager=brackets,
        risk_engine=risk,
        flattening_engine=flattening,
        adaptation_engine=adaptation,
        strategies=strategies,
        entry_order_to_bracket={},
        bracket_realized_pnl={bracket.bracket_id: 0.0},
        completed_brackets_recorded=set(),
        latest_market_prices={"AAPL": 100.0},
        market_history={"AAPL": [{"close": 100.0}]},
        recent_news=[],
        last_session_date=timestamp_date,
        last_vix_print=None,
        ledger_revision=3,
    )
    revision, inserted = store.save_checkpoint(payload, "TEST")
    assert revision == 1
    assert inserted == 0

    restored_components = _components()
    restored_account, restored_engine, restored_brackets, restored_risk, restored_flattening, restored_adaptation, restored_strategies = restored_components
    latest_prices = {}
    history = {}
    news = []
    result = restore_runtime_state(
        store.load_checkpoint()[0],
        account=restored_account,
        engine=restored_engine,
        bracket_manager=restored_brackets,
        risk_engine=restored_risk,
        flattening_engine=restored_flattening,
        adaptation_engine=restored_adaptation,
        strategies=restored_strategies,
        entry_order_to_bracket={},
        bracket_realized_pnl={},
        completed_brackets_recorded=set(),
        latest_market_prices=latest_prices,
        market_history=history,
        recent_news=news,
    )
    assert restored_account.cash == account.cash
    assert restored_account.equity == account.equity
    assert restored_account.positions["AAPL"].shares == 10
    assert entry.id in restored_engine.orders
    assert restored_brackets.symbol_to_bracket["AAPL"] == bracket.bracket_id
    assert restored_engine.working_orders[bracket.stop_order_id].remaining_qty == 10
    assert restored_strategies[0].symbol_states["AAPL"].all_bars
    assert latest_prices == {"AAPL": 100.0}
    assert result["last_session_date"] == timestamp_date
    assert result["ledger_revision"] == 3


def test_trade_insert_is_idempotent_and_history_is_stable(tmp_path):
    store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    trade = {
        "trade_id": "brk_1",
        "session_date": "2026-09-22",
        "closed_at": "2026-09-22T15:00:00+00:00",
        "symbol": "AAPL",
        "realized_pnl": 12.34,
    }
    state = {"runtime_state_version": 1}
    _, inserted_first = store.save_checkpoint(state, "FIRST", trades=[trade])
    _, inserted_second = store.save_checkpoint(state, "REPLAY", trades=[trade])
    items, cursor = store.list_trades("2026-09-22")
    assert inserted_first == 1
    assert inserted_second == 0
    assert cursor is None
    assert items == [trade]


def test_trade_cursor_is_stable_when_close_timestamps_match(tmp_path):
    store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    trades = [
        {
            "trade_id": f"brk_{index}",
            "session_date": "2026-09-22",
            "closed_at": "2026-09-22T15:00:00+00:00",
            "symbol": "AAPL",
            "realized_pnl": float(index),
        }
        for index in range(3)
    ]
    store.save_checkpoint({"runtime_state_version": 1}, "TEST", trades=trades)
    first_page, cursor = store.list_trades(None, limit=2)
    second_page, next_cursor = store.list_trades(None, limit=2, cursor=cursor)
    assert [item["trade_id"] for item in first_page] == ["brk_2", "brk_1"]
    assert [item["trade_id"] for item in second_page] == ["brk_0"]
    assert next_cursor is None


def test_checkpoint_checksum_corruption_fails_closed(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = TradingStateStore(str(database))
    store.save_checkpoint({"runtime_state_version": 1}, "TEST")
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE runtime_checkpoint SET payload = ? WHERE singleton = 1",
        (json.dumps({"runtime_state_version": 999}),),
    )
    connection.commit()
    connection.close()
    with pytest.raises(PersistenceError, match="checksum mismatch"):
        store.load_checkpoint()


def test_hot_backup_can_restore_checkpoint(tmp_path):
    database = tmp_path / "state.sqlite3"
    backup = tmp_path / "state.backup.sqlite3"
    store = TradingStateStore(str(database))
    store.save_checkpoint({"runtime_state_version": 1, "equity": 49978.66}, "TEST")
    store.backup(str(backup))
    restored = TradingStateStore(str(backup))
    restored.integrity_check()
    payload, revision, _ = restored.load_checkpoint()
    assert revision == 1
    assert payload["equity"] == 49978.66


def test_legacy_summary_remains_explicitly_aggregate_only(tmp_path):
    store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    summary = {
        "session_date": "2026-09-21",
        "opening_equity": 50000.0,
        "closing_equity": 49978.66,
        "realized_pnl": -21.34,
        "trades_count": 5,
        "fees": 0.0,
        "source": "LEGACY_SUMMARY_IMPORT",
        "aggregate_only": True,
    }
    store.import_session_summary(summary)
    assert store.list_session_summaries(None) == [summary]
    assert store.list_trades(None)[0] == []


def test_checkpoint_failure_cancels_opening_orders_but_keeps_exit_protection(monkeypatch):
    from backend.app import main

    class BrokenStore:
        def save_checkpoint(self, *args, **kwargs):
            raise OSError("volume unavailable")

    main.reset_runtime_state()
    main.persistence_healthy = True
    main.persistence_error = None
    opening = main.engine.create_order(
        "AAPL", OrderSide.BUY, OrderType.MARKET, 5, estimated_price=100.0
    )
    main.engine.submit_order(opening.id)
    monkeypatch.setattr(main, "state_store", BrokenStore())
    try:
        assert main._checkpoint_runtime("TEST_FAILURE") is False
        assert opening.status.value == "CANCELLED"
        assert opening.id not in main.engine.working_orders
        assert main.persistence_healthy is False
        assert "volume unavailable" in (main.persistence_error or "")
    finally:
        main.reset_runtime_state()
        main.persistence_healthy = True
        main.persistence_error = None


def test_single_writer_lease_rejects_overlapping_process_owner(tmp_path):
    database = tmp_path / "state.sqlite3"
    first = TradingStateStore(str(database))
    with pytest.raises(PersistenceError, match="Another trading-engine writer"):
        TradingStateStore(str(database))
    first.close()
    replacement = TradingStateStore(str(database))
    replacement.close()


def test_pending_market_event_survives_crash_and_commits_with_replayed_state(tmp_path):
    database = tmp_path / "state.sqlite3"
    timestamp = datetime(2026, 9, 22, 14, 30, tzinfo=timezone.utc)
    bar = BarEvent("AAPL", 100.0, 101.0, 99.5, 100.5, 10_000, timestamp)
    store = TradingStateStore(str(database))
    store.save_checkpoint({"runtime_state_version": 1, "fills": 0}, "BEFORE_EVENT")
    assert store.begin_event("bar:one", "BAR", bar) is True
    store.close()  # abrupt stop before the mutated snapshot can be written

    restarted = TradingStateStore(str(database))
    pending = restarted.list_pending_events()
    assert pending == [("bar:one", "BAR", bar)]
    revision, _ = restarted.save_checkpoint(
        {"runtime_state_version": 1, "fills": 1},
        "REPLAYED_EVENT",
        processed_events=[("bar:one", "BAR")],
    )
    assert revision == 2
    assert restarted.list_pending_events() == []
    assert restarted.begin_event("bar:one", "BAR", bar) is False
    assert restarted.load_checkpoint()[0]["fills"] == 1
    restarted.close()


def test_failed_event_checkpoint_is_retryable_and_commits_inbox(tmp_path, monkeypatch):
    from backend.app import main

    original_store = main.state_store
    store = TradingStateStore(str(tmp_path / "retry.sqlite3"))
    monkeypatch.setattr(main, "state_store", store)
    main.reset_runtime_state()
    main.persistence_healthy = True
    bar = BarEvent(
        "AAPL", 100.0, 101.0, 99.0, 100.5, 10_000,
        datetime(2026, 9, 22, 14, 30, tzinfo=timezone.utc),
    )
    should_process, event_key = main._begin_durable_event("BAR", bar)
    assert should_process and event_key
    original_save = store.save_checkpoint
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("one transient volume error")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(store, "save_checkpoint", fail_once)
    try:
        assert main._checkpoint_runtime("BAR_EVENT", (event_key, "BAR")) is False
        assert store.list_pending_events()
        assert main._checkpoint_runtime("PERSISTENCE_RETRY") is True
        assert store.list_pending_events() == []
        assert event_key not in main.inflight_event_keys
    finally:
        main.reset_runtime_state()
        store.close()
        monkeypatch.setattr(main, "state_store", original_store)
        main.persistence_healthy = original_store is not None or not main.settings.PERSISTENCE_REQUIRED


@pytest.mark.asyncio
async def test_manual_api_round_trip_is_durably_listed(tmp_path, monkeypatch):
    from backend.app import main

    original_store = main.state_store
    store = TradingStateStore(str(tmp_path / "manual.sqlite3"))
    monkeypatch.setattr(main, "state_store", store)
    main.reset_runtime_state()
    main.persistence_healthy = True
    main.latest_market_prices["AAPL"] = 100.0
    try:
        response = await main.submit_order(
            main.OrderCreateRequest(
                symbol="AAPL",
                side="BUY",
                order_type="MARKET",
                qty=5,
                stop_price=98.0,
                strategy_id="MANUAL",
            )
        )
        assert response["status"] == "ACCEPTED"
        await main.handle_bar_event(
            BarEvent(
                "AAPL", 100.0, 100.5, 99.5, 100.0, 10_000,
                datetime(2026, 9, 22, 14, 30, tzinfo=timezone.utc),
            )
        )
        assert "AAPL" in main.account.positions
        quiet_quote = QuoteEvent(
            "AAPL", 99.99, 100, "V", 100.01, 100, "V",
            datetime(2026, 9, 22, 14, 31, tzinfo=timezone.utc),
        )
        crossed_stop = QuoteEvent(
            "AAPL", 97.90, 100, "V", 97.92, 100, "V",
            datetime(2026, 9, 22, 14, 32, tzinfo=timezone.utc),
        )
        assert main._quote_requires_write_ahead(quiet_quote) is False
        assert main._quote_requires_write_ahead(crossed_stop) is True
        await main.manual_flatten(main.FlattenRequest(symbol="AAPL"))
        trades, _ = store.list_trades_for_session("2026-09-22"), None
        assert len(trades) == 1
        assert trades[0]["symbol"] == "AAPL"
        assert trades[0]["strategy_id"] == "MANUAL"
        assert trades[0]["fill_legs"]
    finally:
        main.reset_runtime_state()
        store.close()
        monkeypatch.setattr(main, "state_store", original_store)
        main.persistence_healthy = original_store is not None or not main.settings.PERSISTENCE_REQUIRED


@pytest.mark.asyncio
async def test_session_rollover_liquidates_then_summarizes_prior_session(tmp_path, monkeypatch):
    from backend.app import main

    original_store = main.state_store
    store = TradingStateStore(str(tmp_path / "rollover.sqlite3"))
    monkeypatch.setattr(main, "state_store", store)
    main.reset_runtime_state()
    main.persistence_healthy = True
    main.latest_market_prices["AAPL"] = 100.0
    prior_bar = BarEvent(
        "AAPL", 100.0, 100.5, 99.5, 100.0, 10_000,
        datetime(2026, 9, 22, 14, 30, tzinfo=timezone.utc),
    )
    try:
        await main.submit_order(
            main.OrderCreateRequest(
                symbol="AAPL", side="BUY", order_type="MARKET", qty=5,
                stop_price=98.0, strategy_id="MANUAL",
            )
        )
        await main.handle_bar_event(prior_bar)
        assert main.last_session_date.isoformat() == "2026-09-22"

        main._check_session_boundary(datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc))

        assert main.account.positions == {}
        assert main.last_session_date.isoformat() == "2026-09-23"
        trades = store.list_trades_for_session("2026-09-22")
        summaries = store.list_session_summaries("2026-09-22")
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "COMPLETED_FLATTEN"
        assert summaries[0]["trades_count"] == 1
        assert summaries[0]["fees"] == pytest.approx(trades[0]["fees"])
    finally:
        main.reset_runtime_state()
        store.close()
        monkeypatch.setattr(main, "state_store", original_store)
        main.persistence_healthy = original_store is not None or not main.settings.PERSISTENCE_REQUIRED


@pytest.mark.asyncio
async def test_production_refuses_optional_or_disabled_persistence(monkeypatch):
    from backend.app import main

    monkeypatch.setattr(main.settings, "ENV", "production")
    monkeypatch.setattr(main.settings, "PERSISTENCE_ENABLED", True)
    monkeypatch.setattr(main.settings, "PERSISTENCE_REQUIRED", False)
    with pytest.raises(PersistenceError, match="requires PERSISTENCE_ENABLED=true and PERSISTENCE_REQUIRED=true"):
        async with main.lifespan(main.app):
            pass
