"""backend/tests/stress/test_challenger_r6_signal_collision_and_budget.py
Adversarial Stress Harness for Challenger R6-1:
1. Simultaneous Signal Collisions Across 12 Tickers (Async Concurrency & Sector Reservation).
2. Permutation & Monte Carlo Fuzzing (100 runs) of 12-Ticker Simultaneous Bursts.
3. Sector Clustering Attack (Exhausting Sector Cap vs Total Portfolio Cap).
4. Asynchronous Interleaved Fills & Working Orders During Signal Collisions.
5. Edge-Case Pre-Trade Circuit Breaker Loss Budgeting at $1,490 Drawdown with $20 Risk Order.
6. Knife-Edge Circuit Breaker Drawdown Boundaries ($1,499.50, $1,500.00, $1,500.01).
7. Direct Injection Bypass Prevention via pre_trade_risk_validator.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import math
import random
from typing import Any, Dict, List, Optional
import pytest
from zoneinfo import ZoneInfo

from backend.app.config import settings
from backend.app.core.account import PaperTradingAccount, Position, PositionSide
from backend.app.core.bracket import DynamicBracketManager, BracketStatus
from backend.app.core.engine import ExecutionEngine, Order
from backend.app.core.flattening import ZeroOvernightFlatteningEngine, FlatteningPhase
from backend.app.core.market_filter import MarketTrendFilter
from backend.app.core.risk import (
    BreakerStatus,
    InstitutionalRiskEngine,
    RiskCheckResult,
    RiskEngineConfig,
    RiskLevel,
)
from backend.app.core.runtime_state import validate_runtime_state
from backend.app.models.events import (
    BarEvent,
    OrderSide,
    OrderState,
    OrderType,
    QuoteEvent,
)
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.base import SignalEvent
from backend.app import main

ET_TZ = ZoneInfo("America/New_York")


def _reset_main_state() -> None:
    """Reset main module global state between stress tests."""
    main.account.positions.clear()
    main.account.cash = 50000.0
    main.account.equity = 50000.0
    main.account.realized_pnl = 0.0
    main.account.unrealized_pnl = 0.0
    main.account.status = main.account.status.__class__.ACTIVE
    main.engine.working_orders.clear()
    main.engine.orders.clear()
    main.bracket_manager.brackets.clear()
    main.bracket_manager.symbol_to_bracket.clear()
    main.entry_order_to_bracket.clear()
    main.latest_market_prices.clear()
    main.completed_brackets_recorded.clear()
    main.flattening_engine.current_phase = FlatteningPhase.NORMAL_TRADING
    main.risk_engine.status = BreakerStatus.ARMED
    main.risk_engine.risk_level = RiskLevel.NORMAL
    main.adaptation_engine.current_time_phase = "TREND_CONTINUATION"
    main.adaptation_engine.current_sizing_multiplier = 1.0


@pytest.fixture(autouse=True)
def clean_main_state():
    _reset_main_state()
    yield
    _reset_main_state()


def _make_signal(
    symbol: str,
    entry_p: float = 100.0,
    stop_p: float = 98.0,
    side: OrderSide = OrderSide.BUY,
    strategy_id: str = "orb",
) -> SignalEvent:
    now = datetime(2026, 9, 22, 10, 15, 0, tzinfo=ET_TZ)
    return SignalEvent(
        symbol=symbol,
        strategy_id=strategy_id,
        side=side,
        order_type=OrderType.LIMIT,
        entry_price=entry_p,
        stop_loss=stop_p,
        take_profit_1=round(entry_p + (entry_p - stop_p) * 1.0, 2),
        take_profit_2=round(entry_p + (entry_p - stop_p) * 2.0, 2),
        confidence=0.85,
        reason=f"STRESS_TEST_SIGNAL_{symbol}",
        timestamp=now,
    )


class TestR6AdversarialSignalCollisions:
    """Stress test simultaneous signal collisions across 12 tickers to confirm:
    - Max 3 total concurrent positions.
    - Max 2 concurrent positions per sector.
    """

    @pytest.mark.asyncio
    async def test_simultaneous_12_ticker_collision_async_gather(self, monkeypatch):
        """Send simultaneous entry signals across all 12 watchlist symbols concurrently.
        Verify that exactly 3 orders are accepted into the committed portfolio, exactly 9 are rejected,
        and no sector exceeds 2 positions.
        """
        _reset_main_state()

        # Ensure market filter permits all test signals
        monkeypatch.setattr(main.adaptation_engine.market_filter, "is_signal_permitted", lambda **kwargs: (True, "OK"))

        watchlist = list(settings.WATCHLIST_SYMBOLS)
        assert len(watchlist) == 12

        # Create 12 simultaneous signals
        signals = [_make_signal(sym, entry_p=100.0, stop_p=98.0) for sym in watchlist]

        # Dispatch all 12 simultaneously via asyncio.gather
        await asyncio.gather(*[main.execute_strategy_signal(sig) for sig in signals])

        # Inspect engine and account state
        committed_syms, committed_secs, count, notional_map = main._get_effective_committed_portfolio(main.account)

        # Invariant 1: Exactly 3 concurrent positions/working orders committed
        assert count == 3, f"Expected 3 committed symbols, found {count}: {committed_syms}"
        assert len(main.engine.working_orders) == 3

        # Invariant 2: No sector exceeds 2
        sec_counts: Dict[str, int] = {}
        for sym in committed_syms:
            sec = main.risk_engine.symbol_sectors.get(sym, "Other")
            if sec not in ("Index", "Index/ETF"):
                sec_counts[sec] = sec_counts.get(sec, 0) + 1
                assert sec_counts[sec] <= 2, f"Sector {sec} exceeded limit: {sec_counts[sec]}"

        # Invariant 3: Exactly 3 brackets created in PENDING_ENTRY
        assert len(main.bracket_manager.brackets) == 3
        for b in main.bracket_manager.brackets.values():
            assert b.status == BracketStatus.PENDING_ENTRY

    @pytest.mark.asyncio
    async def test_permutation_monte_carlo_stress_100_runs(self, monkeypatch):
        """Adversarial Fuzzing: Run 100 randomized permutations of the 12 tickers.
        Across EVERY permutation, invariants (max 3 total, max 2/sector) must strictly hold.
        """
        rng = random.Random(42)
        watchlist = list(settings.WATCHLIST_SYMBOLS)
        monkeypatch.setattr(main.adaptation_engine.market_filter, "is_signal_permitted", lambda **kwargs: (True, "OK"))

        for iteration in range(100):
            _reset_main_state()
            shuffled = list(watchlist)
            rng.shuffle(shuffled)

            signals = [_make_signal(sym, entry_p=100.0, stop_p=98.0) for sym in shuffled]

            # Execute sequentially or gathered
            for sig in signals:
                await main.execute_strategy_signal(sig)

            committed_syms, committed_secs, count, _ = main._get_effective_committed_portfolio(main.account)

            assert count == 3, f"Iteration {iteration}: Expected 3 total positions, got {count} ({committed_syms})"
            assert len(main.engine.working_orders) == 3

            sec_counts: Dict[str, int] = {}
            for sym in committed_syms:
                sec = main.risk_engine.symbol_sectors.get(sym, "Other")
                if sec not in ("Index", "Index/ETF"):
                    sec_counts[sec] = sec_counts.get(sec, 0) + 1
                    assert sec_counts[sec] <= 2, (
                        f"Iteration {iteration}: Sector '{sec}' exceeded cap: {sec_counts[sec]} ({committed_syms})"
                    )

    def test_sector_clustering_exhaustion_attack(self):
        """Sector Clustering: When 4 symbols belong to the same sector, the first 2 must be
        approved, and the 3rd and 4th rejected with CORRELATED_SECTOR_EXPOSURE.
        Then, a symbol from a DIFFERENT sector must be approved up to total 3.
        A second symbol from that different sector must be REJECTED with MAX_CONCURRENT_POSITIONS_REACHED.
        """
        config = RiskEngineConfig(starting_equity=50000.0, max_concurrent_positions=3, max_positions_per_sector=2)
        risk = InstitutionalRiskEngine(config)
        acct = PaperTradingAccount(initial_cash=50000.0)
        eng = ExecutionEngine(acct)
        bm = DynamicBracketManager()

        # Register 4 symbols in "Technology"
        tech_symbols = ["AAPL", "TECH_A", "TECH_B", "TECH_C"]
        for s in tech_symbols:
            risk.register_symbol_sector(s, "Technology")

        # Register 2 symbols in "Software"
        soft_symbols = ["MSFT", "PLTR"]
        for s in soft_symbols:
            risk.register_symbol_sector(s, "Software")

        # Step 1: Evaluate 4 Tech symbols
        tech_results = []
        for s in tech_symbols:
            active_syms, active_secs, count, notional = main._get_effective_committed_portfolio(
                acct, execution_engine=eng, risk_eng=risk, bracket_mgr=bm
            )
            res = risk.evaluate_order_request(
                symbol=s, side="BUY", requested_qty=10, entry_price=100.0, stop_price=98.0,
                account_equity=acct.equity, buying_power=acct.buying_power,
                active_positions_count=count, active_symbols=active_syms, active_sectors=active_secs,
            )
            tech_results.append((s, res))
            if res.approved:
                order = Order(id=f"ord_{s}", client_order_id=f"c_{s}", symbol=s, side=OrderSide.BUY,
                              order_type=OrderType.LIMIT, qty=10, limit_price=100.0, status=OrderState.ACCEPTED)
                eng.working_orders[order.id] = order

        # Tech 0 and 1 must be approved
        assert tech_results[0][1].approved is True
        assert tech_results[1][1].approved is True
        # Tech 2 and 3 must be REJECTED with CORRELATED_SECTOR_EXPOSURE
        assert tech_results[2][1].approved is False
        assert tech_results[2][1].rejection_code == "CORRELATED_SECTOR_EXPOSURE"
        assert tech_results[3][1].approved is False
        assert tech_results[3][1].rejection_code == "CORRELATED_SECTOR_EXPOSURE"

        # Step 2: Evaluate Software symbols
        active_syms, active_secs, count, notional = main._get_effective_committed_portfolio(
            acct, execution_engine=eng, risk_eng=risk, bracket_mgr=bm
        )
        assert count == 2  # 2 Tech orders working

        # Software 1 (MSFT): should be APPROVED (count goes from 2 -> 3)
        res_soft1 = risk.evaluate_order_request(
            symbol="MSFT", side="BUY", requested_qty=10, entry_price=200.0, stop_price=196.0,
            account_equity=acct.equity, buying_power=acct.buying_power,
            active_positions_count=count, active_symbols=active_syms, active_sectors=active_secs,
        )
        assert res_soft1.approved is True
        ord_soft1 = Order(id="ord_msft", client_order_id="c_msft", symbol="MSFT", side=OrderSide.BUY,
                          order_type=OrderType.LIMIT, qty=10, limit_price=200.0, status=OrderState.ACCEPTED)
        eng.working_orders[ord_soft1.id] = ord_soft1

        # Software 2 (PLTR): Software has only 1 position, but portfolio has 3 -> REJECTED with MAX_CONCURRENT_POSITIONS_REACHED
        active_syms, active_secs, count, notional = main._get_effective_committed_portfolio(
            acct, execution_engine=eng, risk_eng=risk, bracket_mgr=bm
        )
        assert count == 3
        res_soft2 = risk.evaluate_order_request(
            symbol="PLTR", side="BUY", requested_qty=10, entry_price=30.0, stop_price=29.0,
            account_equity=acct.equity, buying_power=acct.buying_power,
            active_positions_count=count, active_symbols=active_syms, active_sectors=active_secs,
        )
        assert res_soft2.approved is False
        assert res_soft2.rejection_code == "MAX_CONCURRENT_POSITIONS_REACHED"

    @pytest.mark.asyncio
    async def test_interleaved_fills_and_position_lifecycle(self, monkeypatch):
        """Interleaved Fills: Verify that transitioning orders from working to filled positions
        and subsequently closing positions accurately updates committed portfolio counts.
        """
        _reset_main_state()
        monkeypatch.setattr(main.adaptation_engine.market_filter, "is_signal_permitted", lambda **kwargs: (True, "OK"))

        # Send 2 signals: AAPL and NVDA
        await main.execute_strategy_signal(_make_signal("AAPL", 150.0, 147.0))
        await main.execute_strategy_signal(_make_signal("NVDA", 120.0, 117.0))

        _, _, count, _ = main._get_effective_committed_portfolio(main.account)
        assert count == 2

        # Simulate fill of AAPL
        aapl_order = [o for o in main.engine.working_orders.values() if o.symbol == "AAPL"][0]
        now = datetime.now(timezone.utc)
        main.account.apply_fill(aapl_order.id, "AAPL", "BUY", aapl_order.qty, 150.0, 0.0, now)
        aapl_order.status = OrderState.FILLED
        del main.engine.working_orders[aapl_order.id]
        main.bracket_manager.activate_bracket_on_fill(
            main.entry_order_to_bracket[aapl_order.id], aapl_order.qty, 150.0, now
        )

        # Committed portfolio still includes 1 filled (AAPL) + 1 working (NVDA) = 2
        committed_syms, _, count, _ = main._get_effective_committed_portfolio(main.account)
        assert count == 2
        assert "AAPL" in committed_syms
        assert "NVDA" in committed_syms

        # Send 3rd signal (TSLA)
        await main.execute_strategy_signal(_make_signal("TSLA", 250.0, 245.0))
        _, _, count, _ = main._get_effective_committed_portfolio(main.account)
        assert count == 3

        # Send 4th signal (AMZN) -> Must be rejected (capacity reached)
        await main.execute_strategy_signal(_make_signal("AMZN", 180.0, 176.0))
        _, _, count, _ = main._get_effective_committed_portfolio(main.account)
        assert count == 3
        assert "AMZN" not in main._get_effective_committed_portfolio(main.account)[0]

        # Now close AAPL position
        main.account.apply_fill("exit_aapl", "AAPL", "SELL", aapl_order.qty, 152.0, 0.0, now)
        assert "AAPL" not in main.account.positions

        # Committed count drops to 2 (NVDA working + TSLA working)
        _, _, count, _ = main._get_effective_committed_portfolio(main.account)
        assert count == 2

        # Now AMZN signal can be admitted
        await main.execute_strategy_signal(_make_signal("AMZN", 180.0, 176.0))
        committed_syms, _, count, _ = main._get_effective_committed_portfolio(main.account)
        assert count == 3
        assert "AMZN" in committed_syms


class TestR6AdversarialCircuitBreakerLossBudgeting:
    """Stress test pre-trade circuit breaker loss budgeting at edge conditions:
    Specifically: when daily drawdown is at $1,490, a new order requiring $20 risk
    is capped or rejected and cannot breach $1,500.
    """

    def test_drawdown_1490_order_requiring_20_risk_is_capped_to_budget(self):
        """Edge Condition Mandate:
        Starting equity: $50,000.
        Drawdown: $1,490 -> Current equity: $48,510.
        Remaining loss budget: $1,500 - $1,490 = $10.00.
        Order requested: 10 shares @ $100 with stop @ $98 ($2 stop distance -> $20.00 requested risk).

        Verification:
        1. evaluate_order_request caps authorized_qty to floor($10 / $2) = 5 shares.
        2. Authorized risk is exactly $10.00 (5 * $2.00).
        3. If stopped out, maximum drawdown is exactly $1,490 + $10 = $1,500.00 (CANNOT breach $1,500).
        """
        config = RiskEngineConfig(
            starting_equity=50000.0,
            hard_max_daily_loss_dollars=1500.0,
            base_trade_risk_pct=0.01,
            max_trade_risk_dollars=500.0,
        )
        risk = InstitutionalRiskEngine(config)
        assert risk.status == BreakerStatus.ARMED

        equity = 48510.0  # $1,490 drawdown
        entry_price = 100.0
        stop_price = 98.0  # $2.00 stop distance
        requested_qty = 10  # 10 * $2 = $20 risk requested

        res = risk.evaluate_order_request(
            symbol="AAPL",
            side="BUY",
            requested_qty=requested_qty,
            entry_price=entry_price,
            stop_price=stop_price,
            account_equity=equity,
            buying_power=190000.0,
            active_positions_count=0,
            active_symbols=set(),
            active_sectors=[],
        )

        assert res.approved is True
        # Must be capped to 5 shares
        assert res.authorized_qty == 5
        assert res.estimated_risk_dollars == 10.00

        # Simulate execution and full stop-out:
        realized_loss = res.authorized_qty * (entry_price - stop_price)
        final_equity = equity - realized_loss
        final_drawdown = config.starting_equity - final_equity
        assert final_drawdown == 1500.00
        assert final_drawdown <= config.hard_max_daily_loss_dollars

    def test_drawdown_1490_large_stop_distance_is_rejected_insufficient_budget(self):
        """When stop distance (e.g. entry $500, stop $485 -> $15 distance, 3.0%) is within
        institutional bounds [0.0040, 0.0400] but exceeds the remaining $10 budget,
        authorized_qty is floor($10 / $15) = 0 shares, rejecting with INSUFFICIENT_RISK_BUDGET.
        """
        config = RiskEngineConfig(starting_equity=50000.0, hard_max_daily_loss_dollars=1500.0)
        risk = InstitutionalRiskEngine(config)

        equity = 48510.0  # $1,490 drawdown -> $10 remaining budget
        res = risk.evaluate_order_request(
            symbol="AAPL",
            side="BUY",
            requested_qty=1,
            entry_price=500.0,
            stop_price=485.0,  # $15.00 stop distance (3.0% is within [0.4%, 4.0%])
            account_equity=equity,
            buying_power=190000.0,
            active_positions_count=0,
            active_symbols=set(),
            active_sectors=[],
        )

        assert res.approved is False
        assert res.authorized_qty == 0
        assert res.rejection_code == "INSUFFICIENT_RISK_BUDGET"

    @pytest.mark.asyncio
    async def test_drawdown_1490_execute_strategy_signal_caps_order_to_budget(self, monkeypatch):
        """In execute_strategy_signal pipeline, verify that a signal requesting 10 shares
        ($20 risk) under $1,490 drawdown creates an order capped to 5 shares ($10 risk).
        """
        _reset_main_state()
        monkeypatch.setattr(main.adaptation_engine.market_filter, "is_signal_permitted", lambda **kwargs: (True, "OK"))

        # Set account equity to $48,510 ($1,490 drawdown)
        main.account.cash = 48510.0
        main.account.equity = 48510.0

        # Signal with entry 100.0 and stop 98.0 ($2 stop distance)
        sig = _make_signal("AAPL", entry_p=100.0, stop_p=98.0)

        # Force adaptation engine to request 10 shares
        monkeypatch.setattr(main.adaptation_engine, "calculate_adapted_size", lambda **kwargs: 10)

        await main.execute_strategy_signal(sig)

        assert len(main.engine.working_orders) == 1
        order = list(main.engine.working_orders.values())[0]

        # Order quantity must be capped from 10 to 5 shares
        assert order.qty == 5
        max_possible_loss = order.qty * abs(order.limit_price - order.stop_price)
        assert max_possible_loss == 10.00

    def test_direct_injection_bypass_rejected_by_pre_trade_validator(self):
        """If an adversary attempts to bypass execute_strategy_signal and inject an un-capped
        10-share order ($20 risk) directly into engine.submit_order under $1,490 drawdown,
        pre_trade_risk_validator must intercept and REJECT with RISK_SIZE_REJECTED.
        """
        _reset_main_state()
        main.account.cash = 48510.0
        main.account.equity = 48510.0

        # Create an un-sized order directly with 10 shares @ $100, stop @ $98
        order = main.engine.create_order(
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=10,  # 10 shares * $2 = $20 risk (exceeds $10 budget)
            limit_price=100.0,
            stop_price=98.0,
            strategy_id="UNSIZED_ADVERSARIAL_INJECTION",
        )

        submitted = main.engine.submit_order(order.id)
        assert submitted.status == OrderState.REJECTED
        assert "RISK_SIZE_REJECTED" in submitted.reject_reason
        assert submitted.id not in main.engine.working_orders

    @pytest.mark.parametrize(
        "drawdown,stop_dist,expected_approved,expected_code,max_allowed_risk",
        [
            (1499.00, 2.00, False, "INSUFFICIENT_RISK_BUDGET", 0.0),     # Budget $1.00 < Stop $2.00
            (1499.00, 0.50, True, None, 1.00),                           # Budget $1.00 >= Stop $0.50 -> 2 shares ($1.00 risk)
            (1499.50, 0.40, True, None, 0.40),                           # Budget $0.50 >= Stop $0.40 -> 1 share ($0.40 risk)
            (1499.90, 0.40, False, "INSUFFICIENT_RISK_BUDGET", 0.0),     # Budget $0.10 < Stop $0.40 -> 0 shares
            (1500.00, 0.40, False, "CIRCUIT_BREAKER_HALTED", 0.0),        # Budget $0.00 -> Halted
            (1500.01, 0.40, False, "CIRCUIT_BREAKER_HALTED", 0.0),        # Breached by 1 cent -> Halted
            (1600.00, 0.40, False, "CIRCUIT_BREAKER_HALTED", 0.0),        # Breached by $100 -> Halted
        ],
    )
    def test_knife_edge_circuit_breaker_boundaries(
        self,
        drawdown: float,
        stop_dist: float,
        expected_approved: bool,
        expected_code: Optional[str],
        max_allowed_risk: float,
    ):
        """Test knife-edge boundary conditions around the $1,500 hard daily circuit breaker."""
        config = RiskEngineConfig(starting_equity=50000.0, hard_max_daily_loss_dollars=1500.0)
        risk = InstitutionalRiskEngine(config)

        equity = 50000.0 - drawdown
        entry_price = 100.0
        stop_price = entry_price - stop_dist

        res = risk.evaluate_order_request(
            symbol="AAPL",
            side="BUY",
            requested_qty=100,
            entry_price=entry_price,
            stop_price=stop_price,
            account_equity=equity,
            buying_power=190000.0,
            active_positions_count=0,
            active_symbols=set(),
            active_sectors=[],
        )

        assert res.approved is expected_approved
        if not expected_approved:
            assert res.rejection_code == expected_code
        else:
            assert res.estimated_risk_dollars <= max_allowed_risk + 0.01
            assert drawdown + res.estimated_risk_dollars <= config.hard_max_daily_loss_dollars + 0.01

    def test_position_reducing_exit_permitted_under_full_drawdown(self):
        """Even when drawdown is at or above $1,500, position-reducing exit orders (e.g. SELL to close LONG)
        must always be approved so risk can be liquidated.
        """
        config = RiskEngineConfig(starting_equity=50000.0, hard_max_daily_loss_dollars=1500.0)
        risk = InstitutionalRiskEngine(config)

        # Drawdown is $1,600 (well beyond $1,500 circuit breaker)
        equity = 48400.0
        res = risk.evaluate_order_request(
            symbol="AAPL",
            side="SELL",
            requested_qty=50,
            entry_price=150.0,
            stop_price=145.0,
            account_equity=equity,
            buying_power=190000.0,
            active_positions_count=1,
            active_symbols={"AAPL"},
            active_sectors=["Technology"],
            is_exit=True,
        )

        assert res.approved is True
        assert res.authorized_qty == 50
        assert "liquidation order approved" in res.reason.lower() or "position-reducing" in res.reason.lower()
