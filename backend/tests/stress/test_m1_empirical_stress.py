"""backend/tests/stress/test_m1_empirical_stress.py
Adversarial Stress Test Suite for Milestone 1:
- Paper Account Ledger & FINRA 4:1 Day Trading Buying Power (DTBP) Bounds & Cash Invariance
- Microstructure Slippage (Kyle's lambda square-root volume model) & Regulatory Fees (SEC 31 / FINRA TAF)
- High-Volume Bar 10% Participation Ceiling & Partial Fill Lifecycle
- Comprehensive Ledger Conservation Invariants & Process Hygiene
"""
from datetime import datetime, timezone
import math
import random
import pytest

from backend.app.core.account import PaperTradingAccount, PositionSide, AccountStatus
from backend.app.core.engine import (
    ExecutionEngine,
    OrderSide,
    OrderType,
    OrderState,
    InvalidOrderStateTransitionError,
)


class TestBuyingPowerBoundsAndCashIntegrity:
    """Stress tests for Day Trading Buying Power (4:1 leverage = $200k cap) and cash ledger invariants."""

    def test_single_order_exceeding_dtbp_hard_rejected(self):
        """Single order requiring > $200,000 buying power must be hard rejected with zero cash degradation."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        initial_cash = account.cash
        initial_equity = account.equity
        initial_bp = account.buying_power

        # Attempt to order $250,000 notional (exceeds $200k max DTBP)
        order = engine.create_order(
            symbol="NVDA",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=2500,
            limit_price=100.00,  # $250k notional
        )
        submitted = engine.submit_order(order.id)

        assert submitted.status == OrderState.REJECTED
        assert submitted.reject_reason is not None
        assert submitted.id not in engine.working_orders

        # Verify absolute cash & balance invariance down to $0.00
        assert account.cash == initial_cash == 50000.00
        assert account.equity == initial_equity == 50000.00
        assert account.buying_power == initial_bp == 200000.00
        assert account.status == AccountStatus.ACTIVE
        assert len(account.positions) == 0

    def test_cumulative_orders_exhausting_dtbp_rejection(self):
        """Cumulative multi-symbol orders must exhaust DTBP and cleanly reject the boundary-crossing order."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        # 4 orders of $45,000 each (within $50,000 per-position cap)
        # Total notional = $180,000. Margin req (25%) = $45,000.
        # Remaining margin excess = $5,000. Remaining DTBP = 4 * $5,000 = $20,000.
        symbols = ["SYM1", "SYM2", "SYM3", "SYM4"]
        for sym in symbols:
            # BUY 450 shares @ $100 = $45,000
            account.apply_fill(f"fill_{sym}", sym, "BUY", 450, 100.00, 0.00, now)

        assert account.maintenance_margin == 45000.00  # 25% of $180k
        assert account.margin_excess == 5000.00       # $50k - $45k
        assert account.buying_power == 20000.00       # 4 * $5k

        cash_before_rejection = account.cash
        equity_before_rejection = account.equity
        bp_before_rejection = account.buying_power

        # Order 5 for SYM5: $30,000 notional (within $50k per-symbol cap, but requires $30k BP > $20k available)
        # Required margin = 0.25 * 30,000 = $7,500. BP needed = $30,000.
        order5 = engine.create_order("SYM5", OrderSide.BUY, OrderType.LIMIT, 300, limit_price=100.00)
        rejected5 = engine.submit_order(order5.id)

        assert rejected5.status == OrderState.REJECTED
        assert "Insufficient Day Trading Buying Power" in (rejected5.reject_reason or "")
        assert rejected5.id not in engine.working_orders

        # Absolute ledger invariance: zero cash corruption
        assert account.cash == cash_before_rejection
        assert account.equity == equity_before_rejection
        assert account.buying_power == bp_before_rejection

    def test_rapid_fire_rejection_stress_cash_invariance(self):
        """Submitting 200 rapid-fire rejected orders must cause ZERO cash drift or working order leaks."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        initial_cash = account.cash

        for i in range(200):
            # Alternate between crazy quantities and crazy prices
            qty = 5000 + i * 10
            price = 200.00
            order = engine.create_order(f"SYM{i%5}", OrderSide.BUY, OrderType.LIMIT, qty, limit_price=price)
            res = engine.submit_order(order.id)
            assert res.status == OrderState.REJECTED

        assert account.cash == initial_cash == 50000.00
        assert account.equity == 50000.00
        assert account.buying_power == 200000.00
        assert len(engine.working_orders) == 0
        # 3 audit records per order: CREATED, SUBMITTED, REJECTED
        assert len(engine.audit_log) == 600

    def test_sub_25k_pdt_margin_restriction_enforcement(self):
        """When equity drops below $25,000 PDT threshold, DTBP drops to 1:1 cash without 4x intraday leverage."""
        account = PaperTradingAccount(initial_cash=24500.00)
        assert account.buying_power == 24500.00  # Throttled to 1x cash

        engine = ExecutionEngine(account=account)
        # Attempt an order of $30,000 (would be approved under 4x, but must be rejected under 1x)
        order = engine.create_order("AAPL", OrderSide.BUY, OrderType.LIMIT, 300, limit_price=100.00)
        rejected = engine.submit_order(order.id)

        assert rejected.status == OrderState.REJECTED
        assert account.cash == 24500.00

    def test_position_flip_dtbp_bypass_vulnerability(self):
        """
        Adversarial Test: Validates whether can_afford() enforces DTBP limits on position flips.
        Holding 10 shares LONG ($1,500) and submitting SELL 10,000 shares ($1,500,000)
        MUST be rejected because the flip short leg (9,990 shares = $1,498,500) vastly exceeds $200,000 DTBP.
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        now = datetime.now(timezone.utc)
        account.apply_fill("ord1", "AAPL", "BUY", 10, 150.00, 0.00, now)

        # Attempt to submit SELL 10,000 shares ($1,500,000 notional)
        approved, reason = account.can_afford("AAPL", "SELL", 10000, 150.00)
        assert not approved, (
            f"CRITICAL VULNERABILITY: Position flip of $1.5M bypassed DTBP bounds! (approved={approved}, reason={reason})"
        )

    def test_position_flip_short_to_long_dtbp_bypass_vulnerability(self):
        """
        Adversarial Test: Validates whether can_afford() enforces concentration / DTBP limits
        when flipping from SHORT to LONG.
        Holding 10 shares SHORT ($1,500) and submitting BUY 10,000 shares ($1,500,000)
        MUST be rejected because the flip long leg (9,990 shares = $1,498,500) vastly exceeds $50k concentration cap.
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        now = datetime.now(timezone.utc)
        account.apply_fill("ord1", "AAPL", "SELL", 10, 150.00, 0.00, now)

        approved, reason = account.can_afford("AAPL", "BUY", 10000, 150.00)
        assert not approved, (
            f"CRITICAL VULNERABILITY: Short-to-long flip of $1.5M bypassed DTBP bounds! (approved={approved}, reason={reason})"
        )


class TestMicrostructureSlippageAndFees:
    """Stress tests for Kyle's lambda square-root slippage model and SEC Section 31 / FINRA TAF fees."""

    def test_kyles_lambda_square_root_scaling_with_order_size(self):
        """Microstructure market impact must scale with sqrt(order_qty)."""
        account = PaperTradingAccount()
        engine = ExecutionEngine(account=account)
        order_base = engine.create_order("TEST", OrderSide.BUY, OrderType.LIMIT, 100, limit_price=100.00)
        order_4x = engine.create_order("TEST", OrderSide.BUY, OrderType.LIMIT, 400, limit_price=100.00)
        order_16x = engine.create_order("TEST", OrderSide.BUY, OrderType.LIMIT, 1600, limit_price=100.00)

        market_price = 100.0
        bid, ask = 99.95, 100.05  # spread = 0.10, half spread = 0.05
        high, low = 101.0, 99.0   # volatility = 2.0
        bar_volume = 10000        # sqrt(Q / 10000)

        slip_base = engine.calculate_slippage(order_base, market_price, bid, ask, bar_volume, high, low)
        slip_4x = engine.calculate_slippage(order_4x, market_price, bid, ask, bar_volume, high, low)
        slip_16x = engine.calculate_slippage(order_16x, market_price, bid, ask, bar_volume, high, low)

        half_spread = 0.05
        impact_base = slip_base - half_spread
        impact_4x = slip_4x - half_spread
        impact_16x = slip_16x - half_spread

        # Verify sqrt(4) = 2.0 and sqrt(16) = 4.0 scaling
        assert math.isclose(impact_4x / impact_base, 2.0, rel_tol=1e-3)
        assert math.isclose(impact_16x / impact_base, 4.0, rel_tol=1e-3)

    def test_kyles_lambda_inverse_sqrt_scaling_with_bar_volume(self):
        """Microstructure market impact must scale inversely with sqrt(bar_volume)."""
        account = PaperTradingAccount()
        engine = ExecutionEngine(account=account)
        order = engine.create_order("TEST", OrderSide.BUY, OrderType.LIMIT, 1000, limit_price=100.00)

        bid, ask = 99.99, 100.01  # spread = 0.02, half = 0.01
        high, low = 101.0, 99.0   # volatility = 2.0

        slip_v1k = engine.calculate_slippage(order, 100.0, bid, ask, bar_volume=1000, bar_high=high, bar_low=low)
        slip_v4k = engine.calculate_slippage(order, 100.0, bid, ask, bar_volume=4000, bar_high=high, bar_low=low)
        slip_v16k = engine.calculate_slippage(order, 100.0, bid, ask, bar_volume=16000, bar_high=high, bar_low=low)

        impact_v1k = slip_v1k - 0.01
        impact_v4k = slip_v4k - 0.01
        impact_v16k = slip_v16k - 0.01

        # V increases by 4x -> impact decreases by sqrt(4) = 2x
        assert math.isclose(impact_v1k / impact_v4k, 2.0, rel_tol=1e-3)
        # V increases by 16x -> impact decreases by sqrt(16) = 4x
        assert math.isclose(impact_v1k / impact_v16k, 4.0, rel_tol=1e-3)

    def test_slippage_floor_and_stop_multiplier(self):
        """Verify 1 bps minimum slippage floor and 1.5x adverse multiplier on STOP orders."""
        account = PaperTradingAccount()
        engine = ExecutionEngine(account=account)

        ord_limit = engine.create_order("AAPL", OrderSide.BUY, OrderType.LIMIT, 10, limit_price=100.00)
        ord_stop = engine.create_order("AAPL", OrderSide.SELL, OrderType.STOP, 10, stop_price=100.00)

        slip_limit = engine.calculate_slippage(ord_limit, 100.0, bid=100.0, ask=100.0, bar_volume=100000, bar_high=100.0, bar_low=100.0)
        slip_stop = engine.calculate_slippage(ord_stop, 100.0, bid=100.0, ask=100.0, bar_volume=100000, bar_high=100.0, bar_low=100.0)

        assert slip_limit == 0.01
        assert slip_stop == 0.015

    def test_high_frequency_regulatory_fees_precision(self):
        """Execute 500 high-frequency sell fills verifying exact SEC Section 31 and FINRA TAF deductions."""
        account = PaperTradingAccount(initial_cash=100000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        account.apply_fill("buy_500", "AAPL", "BUY", 500, 150.00, 0.00, now)

        total_fees_expected = 500 * 0.02  # $10.00

        for i in range(500):
            fee = engine.calculate_fees(OrderSide.SELL, 1, 150.00)
            assert fee == 0.02
            account.apply_fill(f"sell_{i}", "AAPL", "SELL", 1, 150.00, fee, now)

        assert len(account.positions) == 0
        assert math.isclose(account.fees_paid, total_fees_expected, abs_tol=1e-2)
        assert math.isclose(account.cash, 99990.00, abs_tol=1e-2)
        assert math.isclose(account.equity, 99990.00, abs_tol=1e-2)
        assert math.isclose(account.realized_pnl, -10.00, abs_tol=1e-2)

    def test_short_opening_fee_realized_pnl_accounting_leak(self):
        """
        Adversarial Test: Validates that regulatory fees paid when opening a Short position
        are properly accounted for in realized PnL upon covering.
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        now = datetime.now(timezone.utc)

        # Short 100 shares @ $100 with $2.00 fee (SEC + FINRA)
        account.apply_fill("ord1", "XYZ", "SELL", 100, 100.00, 2.00, now)

        # Cover 100 shares @ $100 with $0.00 fee (buys incur $0 fee)
        account.apply_fill("ord2", "XYZ", "BUY", 100, 100.00, 0.00, now)

        # Trade entered and exited at exactly $100.00 flat with $2.00 fee.
        # True net realized PnL MUST be -$2.00, and Equity MUST equal initial ($50,000) + realized (-$2.00) = $49,998.00.
        assert math.isclose(account.realized_pnl, -2.00, abs_tol=1e-2), (
            f"ACCOUNTING LEAK: Realized PnL is {account.realized_pnl}, expected -2.00! Short entry fees were omitted from realized PnL."
        )
        assert math.isclose(account.equity, round(account.initial_balance + account.realized_pnl, 2), abs_tol=1e-2), (
            f"BALANCE IDENTITY BROKEN: Equity {account.equity} != Initial + Realized PnL {account.initial_balance + account.realized_pnl}"
        )

    def test_short_opening_fee_partial_cover_realized_pnl_accounting(self):
        """
        Adversarial Test: Validates that regulatory fees paid when opening a Short position
        are properly prorated across multiple partial covers.
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        now = datetime.now(timezone.utc)

        # Short 100 shares @ $100 with $2.00 fee (SEC + FINRA)
        account.apply_fill("ord1", "XYZ", "SELL", 100, 100.00, 2.00, now)

        # Partial cover 40 shares @ $100 with $0.00 fee
        r_delta_1, pos = account.apply_fill("ord2", "XYZ", "BUY", 40, 100.00, 0.00, now)
        assert math.isclose(r_delta_1, -0.80, abs_tol=1e-2)
        assert math.isclose(account.realized_pnl, -0.80, abs_tol=1e-2)
        assert pos is not None and pos.shares == 60
        assert math.isclose(pos.fees_paid, 1.20, abs_tol=1e-2)
        # Open position fees_paid ($1.20) is still unrealized in cash:
        assert math.isclose(account.equity, round(account.initial_balance + account.realized_pnl + account.unrealized_pnl - pos.fees_paid, 2), abs_tol=1e-2)

        # Cover remaining 60 shares @ $100 with $0.00 fee
        r_delta_2, pos2 = account.apply_fill("ord3", "XYZ", "BUY", 60, 100.00, 0.00, now)
        assert math.isclose(r_delta_2, -1.20, abs_tol=1e-2)
        assert math.isclose(account.realized_pnl, -2.00, abs_tol=1e-2)
        assert pos2 is None
        assert math.isclose(account.equity, round(account.initial_balance + account.realized_pnl, 2), abs_tol=1e-2)

    def test_monte_carlo_long_only_ledger_invariance(self):
        """500 random long fills verifying fundamental balance invariant: Equity = Cash + Long MV."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)
        random.seed(42)

        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN"]

        for step in range(500):
            sym = random.choice(symbols)
            pos = account.get_position(sym)
            price = round(random.uniform(50.0, 200.0), 2)
            account.update_market_price(sym, price)

            if pos is None:
                qty = random.randint(5, 50)
                fee = engine.calculate_fees(OrderSide.BUY, qty, price)
                account.apply_fill(f"fl_{step}", sym, "BUY", qty, price, fee, now)
            else:
                qty = min(pos.shares, random.randint(1, pos.shares))
                fee = engine.calculate_fees(OrderSide.SELL, qty, price)
                account.apply_fill(f"fl_{step}", sym, "SELL", qty, price, fee, now)

            # Invariant 1: Equity == Cash + Long Market Value
            long_mv = sum(p.market_value for p in account.positions.values())
            expected_equity = round(account.cash + long_mv, 2)
            assert math.isclose(account.equity, expected_equity, abs_tol=1e-2)

            # Invariant 2: Equity == Initial Balance + Realized PnL + Unrealized PnL
            perf_equity = round(account.initial_balance + account.realized_pnl + account.unrealized_pnl, 2)
            assert math.isclose(account.equity, perf_equity, abs_tol=1e-2)


class TestHighVolumeBarParticipation:
    """Stress tests for the 10% bar volume participation cap and multi-bar partial fill lifecycle."""

    def test_multi_bar_partial_fill_10_percent_cap(self):
        """Large order of 5,000 shares must partial fill up to exactly 10% bar volume across consecutive bars."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        # Limit order: BUY 5,000 shares @ $10.00 ($50,000 notional = exactly 25% DTBP cap)
        order = engine.create_order("PENNY", OrderSide.BUY, OrderType.LIMIT, 5000, limit_price=10.00)
        engine.submit_order(order.id)
        assert order.status == OrderState.ACCEPTED

        # Bar 1: Volume = 10,000. 10% cap = 1,000 shares.
        fills_b1 = engine.process_bar("PENNY", open_=9.80, high=9.95, low=9.70, close=9.85, volume=10000, timestamp=now)
        assert len(fills_b1) == 1
        assert fills_b1[0].qty == 1000
        assert order.status == OrderState.PARTIALLY_FILLED
        assert order.filled_qty == 1000
        assert order.remaining_qty == 4000
        assert order.id in engine.working_orders

        # Bar 2: Volume = 15,000. 10% cap = 1,500 shares.
        fills_b2 = engine.process_bar("PENNY", open_=9.85, high=10.00, low=9.80, close=9.90, volume=15000, timestamp=now)
        assert len(fills_b2) == 1
        assert fills_b2[0].qty == 1500
        assert order.status == OrderState.PARTIALLY_FILLED
        assert order.filled_qty == 2500
        assert order.remaining_qty == 2500

        # Bar 3: Volume = 8,000. 10% cap = 800 shares.
        fills_b3 = engine.process_bar("PENNY", open_=9.90, high=9.95, low=9.75, close=9.80, volume=8000, timestamp=now)
        assert len(fills_b3) == 1
        assert fills_b3[0].qty == 800
        assert order.status == OrderState.PARTIALLY_FILLED
        assert order.filled_qty == 3300
        assert order.remaining_qty == 1700

        # Bar 4: Mega-volume bar: Volume = 100,000. 10% cap = 10,000.
        # But remaining_qty is only 1,700 -> must fill exactly 1,700 and finish order!
        fills_b4 = engine.process_bar("PENNY", open_=9.80, high=9.90, low=9.60, close=9.70, volume=100000, timestamp=now)
        assert len(fills_b4) == 1
        assert fills_b4[0].qty == 1700
        assert order.status == OrderState.FILLED
        assert order.filled_qty == 5000
        assert order.remaining_qty == 0
        assert order.id not in engine.working_orders

        # Verify weighted average fill price calculation
        total_cash_spent = sum(f.qty * f.price for f in order.fills)
        expected_avg_price = round(total_cash_spent / 5000, 4)
        assert order.avg_fill_price == expected_avg_price

    def test_low_volume_bar_minimum_fill_floor(self):
        """Very low volume bar (e.g. 50 shares) respects the minimum fill floor of 10 shares."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        order = engine.create_order("ILLIQ", OrderSide.BUY, OrderType.LIMIT, 100, limit_price=10.00)
        engine.submit_order(order.id)

        # Bar volume = 50. 10% = 5 shares. But floor is max(10, int(vol * 0.10)) = 10 shares.
        fills = engine.process_bar("ILLIQ", open_=9.50, high=9.80, low=9.40, close=9.60, volume=50, timestamp=now)
        assert len(fills) == 1
        assert fills[0].qty == 10
        assert order.remaining_qty == 90

    def test_limit_order_no_fill_when_price_not_touched(self):
        """Limit order must not fill if bar low > buy limit price."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        order = engine.create_order("HIGH", OrderSide.BUY, OrderType.LIMIT, 100, limit_price=10.00)
        engine.submit_order(order.id)

        # Bar trades strictly above limit: low = 10.50
        fills = engine.process_bar("HIGH", open_=11.00, high=12.00, low=10.50, close=11.50, volume=50000, timestamp=now)
        assert len(fills) == 0
        assert order.status == OrderState.ACCEPTED
        assert order.remaining_qty == 100
        assert order.id in engine.working_orders

    def test_partial_fill_cancellation_working_order_cleanup(self):
        """Order in PARTIALLY_FILLED state can be cancelled; working orders cleaned up properly."""
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        order = engine.create_order("PARTIAL", OrderSide.BUY, OrderType.LIMIT, 1000, limit_price=10.00)
        engine.submit_order(order.id)

        # Fill 100 shares in first bar
        engine.process_bar("PARTIAL", open_=9.80, high=9.95, low=9.70, close=9.85, volume=1000, timestamp=now)
        assert order.status == OrderState.PARTIALLY_FILLED
        assert order.filled_qty == 100
        assert order.remaining_qty == 900
        assert order.id in engine.working_orders

        # Cancel partially filled order
        cancelled = engine.cancel_order(order.id, reason="RISK_HALT")
        assert cancelled.status == OrderState.CANCELLED
        assert order.id not in engine.working_orders
        assert order.filled_qty == 100
        assert order.remaining_qty == 900
        # Account retains the 100 shares filled
        pos = account.get_position("PARTIAL")
        assert pos is not None
        assert pos.shares == 100
