"""stress_bracket_risk.py
Adversarial Bracket & Risk Geometry Stress Suite for Challenger 2.
Empirically tests:
1. Target 1 scaling (0.8R) and Target 2 scaling (1.8R) on BUY and SELL sides.
2. Trailing stop monotonicity and gating (pre-T1 rally must NOT move stop).
3. Target 1 hit scale-out (50%), breakeven + dynamic buffer, and ATR trailing stop start.
4. Risk engine limits ($1500 daily loss, $25,000 position notional cap, 0.40%-4.00% stop range with EPS=1e-6).
5. Partial fills, whipsaw quotes, and fast micro-crashes.
"""
from __future__ import annotations
import math
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from backend.app.core.account import PaperTradingAccount, PositionSide
from backend.app.core.bracket import (
    BracketChildType,
    BracketOrder,
    BracketStatus,
    BracketUpdateDirective,
    DynamicBracketManager,
)
from backend.app.core.engine import (
    BracketRole,
    ExecutionEngine,
    OrderSide,
    OrderState,
    OrderType,
)
from backend.app.core.risk import (
    BreakerStatus,
    InstitutionalRiskEngine,
    RiskCheckResult,
    RiskEngineConfig,
    RiskLevel,
)
from backend.app.models.events import BarEvent


# ==============================================================================
# SECTION 1: TARGET SCALING AT 0.8R AND 1.8R (BUY & SELL)
# ==============================================================================

class TestTargetScalingBuySell:
    """Stress-test Target 1 (0.8R) and Target 2 (1.8R) on BUY and SELL sides."""

    def test_buy_side_target_scaling_standard(self):
        """LONG: Entry $100.00, Stop $98.00 -> R = $2.00.
        T1 (0.8R) = 100.00 + 0.8 * 2.00 = 101.60
        T2 (1.8R) = 100.00 + 1.8 * 2.00 = 103.60
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_buy_std", "AAPL", "LONG", 100, 100.00, 98.00, timestamp=now)

        assert brk.side == "LONG"
        assert brk.r_distance == 2.00
        assert brk.target_1_price == 101.60
        assert brk.target_2_price == 103.60
        assert brk.target_1_qty == 50
        assert brk.target_2_qty == 50
        assert brk.target_1_qty + brk.target_2_qty == 100

    def test_sell_side_target_scaling_standard(self):
        """SHORT: Entry $100.00, Stop $102.00 -> R = $2.00.
        T1 (0.8R) = 100.00 - 0.8 * 2.00 = 98.40
        T2 (1.8R) = 100.00 - 1.8 * 2.00 = 96.40
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_sell_std", "TSLA", "SHORT", 100, 100.00, 102.00, timestamp=now)

        assert brk.side == "SHORT"
        assert brk.r_distance == 2.00
        assert brk.target_1_price == 98.40
        assert brk.target_2_price == 96.40
        assert brk.target_1_qty == 50
        assert brk.target_2_qty == 50
        assert brk.target_1_qty + brk.target_2_qty == 100

    def test_odd_quantity_division_buy_and_sell(self):
        """Odd quantities: 1 share, 7 shares, 13 shares, 99 shares."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        # 1 share: q1 = 1, q2 = 0
        b1 = bm.create_bracket("b_odd_1", "AMZN", "LONG", 1, 150.00, 145.00, timestamp=now)
        assert b1.target_1_qty == 1
        assert b1.target_2_qty == 0
        assert b1.target_2_order_id is None

        # 7 shares: q1 = 3, q2 = 4
        b7 = bm.create_bracket("b_odd_7", "NVDA", "SHORT", 7, 120.00, 124.00, timestamp=now)
        assert b7.target_1_qty == 3
        assert b7.target_2_qty == 4
        assert b7.target_1_qty + b7.target_2_qty == 7

        # 99 shares: q1 = 49, q2 = 50
        b99 = bm.create_bracket("b_odd_99", "MSFT", "LONG", 99, 400.00, 396.00, timestamp=now)
        assert b99.target_1_qty == 49
        assert b99.target_2_qty == 50
        assert b99.target_1_qty + b99.target_2_qty == 99

    def test_target_overrides_respected_on_both_sides(self):
        """Strategy overrides for take_profit_1 and take_profit_2 take precedence."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        # BUY with explicit overrides
        b_buy = bm.create_bracket(
            "b_ovr_buy", "AAPL", "LONG", 100, 100.00, 98.00,
            target_1_override=101.25, target_2_override=102.75, timestamp=now
        )
        assert b_buy.target_1_price == 101.25
        assert b_buy.target_2_price == 102.75

        # SHORT with explicit overrides
        b_sell = bm.create_bracket(
            "b_ovr_sell", "AAPL", "SHORT", 100, 100.00, 102.00,
            target_1_override=98.75, target_2_override=97.25, timestamp=now
        )
        assert b_sell.target_1_price == 98.75
        assert b_sell.target_2_price == 97.25

    def test_activation_recomputes_targets_without_overrides(self):
        """When activated with slippage and NO override, targets re-anchor to fill price."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_slip", "NVDA", "LONG", 100, 100.00, 98.00, timestamp=now)
        # Entry slippage fills at 100.50 (stop stays at 98.00, R = 2.50)
        directive = bm.activate_bracket_on_fill("b_slip", 100, 100.50, now)

        assert brk.entry_price == 100.50
        assert brk.r_distance == 2.50
        # Re-anchored: 100.50 + 0.8 * 2.50 = 102.50
        assert brk.target_1_price == 102.50
        # Re-anchored: 100.50 + 1.8 * 2.50 = 105.00
        assert brk.target_2_price == 105.00

    def test_activation_preserves_targets_with_overrides(self):
        """When activated with slippage and WITH override, explicit overrides remain fixed."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket(
            "b_slip_ovr", "NVDA", "LONG", 100, 100.00, 98.00,
            target_1_override=101.80, target_2_override=103.50, timestamp=now
        )
        directive = bm.activate_bracket_on_fill("b_slip_ovr", 100, 100.50, now)

        assert brk.entry_price == 100.50
        assert brk.target_1_price == 101.80
        assert brk.target_2_price == 103.50

    def test_invalid_bracket_inputs_raise_errors(self):
        """Adversarial check: negative price, invalid side, zero qty, stop on wrong side."""
        bm = DynamicBracketManager()
        # Invalid side
        with pytest.raises(ValueError, match="Invalid side"):
            bm.create_bracket("b_bad", "SPY", "SIDEWAYS", 10, 100.0, 98.0)
        # Zero quantity
        with pytest.raises(ValueError, match="must be positive"):
            bm.create_bracket("b_bad", "SPY", "LONG", 0, 100.0, 98.0)
        # Long stop above entry
        with pytest.raises(ValueError, match="Stop price must be below"):
            bm.create_bracket("b_bad", "SPY", "LONG", 10, 100.0, 101.0)
        # Short stop below entry
        with pytest.raises(ValueError, match="Stop price must be below a long entry and above a short entry"):
            bm.create_bracket("b_bad", "SPY", "SHORT", 10, 100.0, 99.0)


# ==============================================================================
# SECTION 2: TRAILING STOP MONOTONICITY & GATING
# ==============================================================================

class TestTrailingStopMonotonicityAndGating:
    """Stress-test trailing stop monotonicity and active-status gating."""

    def test_active_bracket_rally_does_not_move_stop_buy(self):
        """LONG: On ACTIVE bracket (before T1 hit), price rallies from 100 to 101.50.
        Structural stop at 98.00 must NOT move into entry noise!
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_gate_buy", "AAPL", "LONG", 100, 100.00, 98.00, timestamp=now)
        bm.activate_bracket_on_fill("b_gate_buy", 100, 100.00, now)
        assert brk.status == BracketStatus.ACTIVE

        # Bar 1: Stock rallies to 101.50 (just under T1 101.60), ATR = 0.50
        # If trailing ATR ran: peak 101.50 - 1.5 * 0.50 = 100.75 (would walk stop into noise!)
        res = bm.update_trailing_stop("AAPL", current_bar_high=101.50, current_bar_low=100.20,
                                      current_atr=0.50, timestamp=now)
        assert res is None
        assert brk.current_stop_price == 98.00  # Strictly preserved

    def test_active_bracket_drop_does_not_move_stop_sell(self):
        """SHORT: On ACTIVE bracket (before T1 hit), price plunges from 100 to 98.50.
        Structural stop at 102.00 must NOT move into entry noise!
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_gate_sell", "TSLA", "SHORT", 100, 100.00, 102.00, timestamp=now)
        bm.activate_bracket_on_fill("b_gate_sell", 100, 100.00, now)
        assert brk.status == BracketStatus.ACTIVE

        # Bar 1: Stock drops to 98.50 (just above T1 98.40), ATR = 0.50
        res = bm.update_trailing_stop("TSLA", current_bar_high=99.80, current_bar_low=98.50,
                                      current_atr=0.50, timestamp=now)
        assert res is None
        assert brk.current_stop_price == 102.00  # Strictly preserved

    def test_trailing_stop_ineligible_statuses(self):
        """Trailing stop must return None for PENDING_ENTRY, COMPLETED_*, and when disabled."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_inel", "NVDA", "LONG", 100, 100.00, 98.00, timestamp=now)

        # 1. PENDING_ENTRY
        assert bm.update_trailing_stop("NVDA", 105.0, 101.0, 1.0, now) is None

        # 2. When use_trailing_target_2 is False
        brk_no_trail = bm.create_bracket("b_notrail", "AMD", "LONG", 100, 100.0, 98.0,
                                          use_trailing_target_2=False, timestamp=now)
        bm.activate_bracket_on_fill("b_notrail", 100, 100.0, now)
        brk_no_trail.status = BracketStatus.TARGET_1_HIT
        assert bm.update_trailing_stop("AMD", 105.0, 101.0, 1.0, now) is None

    def test_monotonicity_under_whipsaw_and_atr_expansion_long(self):
        """LONG: Once TARGET_1_HIT, test extreme whipsaw bars:
        Stop can ratchet up, but can NEVER ratchet down, even under massive ATR expansion.
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_mono_long", "AAPL", "LONG", 100, 100.00, 98.00, timestamp=now)
        bm.activate_bracket_on_fill("b_mono_long", 100, 100.00, now)

        # T1 fills at 101.60
        bm.on_child_order_fill(brk.target_1_order_id, 101.60, 50, now)
        assert brk.status == BracketStatus.TARGET_1_HIT
        assert brk.current_stop_price == 100.05  # Breakeven buffer max(0.04, 0.05)

        # Bar 1: Rally to 103.00, ATR = 0.50 -> trail_dist = 0.75 -> stop = 103.00 - 0.75 = 102.25
        d1 = bm.update_trailing_stop("AAPL", 103.00, 101.80, 0.50, now)
        assert d1 is not None
        assert brk.current_stop_price == 102.25

        # Bar 2: Volatility explosion! High = 102.50, Low = 99.00, ATR surges to 3.00!
        # Potential stop = 103.00 - (1.5 * 3.00) = 103.00 - 4.50 = 98.50!
        # Stop must NOT loosen from 102.25 to 98.50!
        d2 = bm.update_trailing_stop("AAPL", 102.50, 99.00, 3.00, now)
        assert d2 is None
        assert brk.current_stop_price == 102.25  # Strictly monotonic

    def test_monotonicity_under_whipsaw_and_atr_expansion_short(self):
        """SHORT: Once TARGET_1_HIT, test extreme whipsaw bars:
        Stop can ratchet down, but can NEVER ratchet up, even under massive ATR expansion.
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_mono_short", "TSLA", "SHORT", 100, 100.00, 102.00, timestamp=now)
        bm.activate_bracket_on_fill("b_mono_short", 100, 100.00, now)

        # T1 fills at 98.40
        bm.on_child_order_fill(brk.target_1_order_id, 98.40, 50, now)
        assert brk.status == BracketStatus.TARGET_1_HIT
        assert brk.current_stop_price == 99.95  # Breakeven buffer max(0.04, 0.05)

        # Bar 1: Drop to 97.00, ATR = 0.50 -> trail_dist = 0.75 -> stop = 97.00 + 0.75 = 97.75
        d1 = bm.update_trailing_stop("TSLA", 98.50, 97.00, 0.50, now)
        assert d1 is not None
        assert brk.current_stop_price == 97.75

        # Bar 2: Rebound surge! High = 101.00, Low = 97.50, ATR surges to 3.00!
        # Potential stop = 97.00 + (1.5 * 3.00) = 97.00 + 4.50 = 101.50!
        # Stop must NOT loosen from 97.75 up to 101.50!
        d2 = bm.update_trailing_stop("TSLA", 101.00, 97.50, 3.00, now)
        assert d2 is None
        assert brk.current_stop_price == 97.75  # Strictly monotonic


# ==============================================================================
# SECTION 3: TARGET 1 SCALE-OUT (50%), BREAKEVEN BUFFER, AND ATR TRAIL
# ==============================================================================

class TestTarget1HitBreakevenAndTrailingATR:
    """Stress-test Target 1 scale-out mechanics, dynamic buffer scaling, and trail start."""

    def test_dynamic_breakeven_buffer_across_price_spectrum(self):
        """Verify max(0.04, round(entry * 0.0005, 2)) across diverse stock prices."""
        bm = DynamicBracketManager()
        # $10 penny/cheap stock: 10 * 0.0005 = 0.005 -> buffer = 0.04
        assert bm.get_breakeven_buffer(10.00) == 0.04
        # $40 stock: 40 * 0.0005 = 0.02 -> buffer = 0.04
        assert bm.get_breakeven_buffer(40.00) == 0.04
        # $80 inflection point: 80 * 0.0005 = 0.04 -> buffer = 0.04
        assert bm.get_breakeven_buffer(80.00) == 0.04
        # $100 stock: 100 * 0.0005 = 0.05 -> buffer = 0.05
        assert bm.get_breakeven_buffer(100.00) == 0.05
        # $250 stock: 250 * 0.0005 = 0.125 -> round 0.12 or 0.13 -> buffer >= 0.12
        assert bm.get_breakeven_buffer(250.00) == 0.12
        # $400 stock (TSLA): 400 * 0.0005 = 0.20 -> buffer = 0.20
        assert bm.get_breakeven_buffer(400.00) == 0.20
        # $1000 mega-stock: 1000 * 0.0005 = 0.50 -> buffer = 0.50
        assert bm.get_breakeven_buffer(1000.00) == 0.50

    def test_target_1_fill_scale_out_and_stop_order_directive_long(self):
        """LONG: Verify 50% scale out, stop ratchet, and MODIFY_ORDER directive."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_scale_long", "AAPL", "LONG", 100, 100.00, 98.00, timestamp=now)
        bm.activate_bracket_on_fill("b_scale_long", 100, 100.00, now)

        d = bm.on_child_order_fill(brk.target_1_order_id, 101.60, 50, now)
        assert d.action == "MODIFY_ORDER"
        assert d.bracket_status == BracketStatus.TARGET_1_HIT
        assert brk.remaining_qty == 50
        assert brk.target_1_filled is True
        assert brk.current_stop_price == 100.05
        assert len(d.orders_to_modify) == 1
        assert d.orders_to_modify[0]["order_id"] == brk.stop_order_id
        assert d.orders_to_modify[0]["new_qty"] == 50
        assert d.orders_to_modify[0]["new_stop_price"] == 100.05

    def test_target_1_fill_scale_out_and_stop_order_directive_short(self):
        """SHORT: Verify 50% scale out, stop ratchet, and MODIFY_ORDER directive."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_scale_short", "TSLA", "SHORT", 100, 100.00, 102.00, timestamp=now)
        bm.activate_bracket_on_fill("b_scale_short", 100, 100.00, now)

        d = bm.on_child_order_fill(brk.target_1_order_id, 98.40, 50, now)
        assert d.action == "MODIFY_ORDER"
        assert d.bracket_status == BracketStatus.TARGET_1_HIT
        assert brk.remaining_qty == 50
        assert brk.target_1_filled is True
        assert brk.current_stop_price == 99.95
        assert len(d.orders_to_modify) == 1
        assert d.orders_to_modify[0]["order_id"] == brk.stop_order_id
        assert d.orders_to_modify[0]["new_qty"] == 50
        assert d.orders_to_modify[0]["new_stop_price"] == 99.95

    def test_single_share_position_target_1_completes_profit(self):
        """When total_qty = 1, Target 1 filling 1 share immediately transitions to COMPLETED_PROFIT."""
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_1sh", "SPY", "LONG", 1, 500.00, 498.00, timestamp=now)
        bm.activate_bracket_on_fill("b_1sh", 1, 500.00, now)

        d = bm.on_child_order_fill(brk.target_1_order_id, 501.60, 1, now)
        assert d.action == "CANCEL_ORDER"
        assert d.bracket_status == BracketStatus.COMPLETED_PROFIT
        assert brk.status == BracketStatus.COMPLETED_PROFIT
        assert brk.remaining_qty == 0
        assert "SPY" not in bm.symbol_to_bracket


# ==============================================================================
# SECTION 4: RISK ENGINE LIMITS & FLOAT TOLERANCE (BOUNDARY TESTS)
# ==============================================================================

class TestRiskEngineLimitsAndFloatTolerance:
    """Stress-test $1500 daily loss, $25k notional cap, 0.4%-4.0% stop range with EPS = 1e-6."""

    def test_circuit_breaker_boundary_1499_99_vs_1500_00(self):
        """$1,499.99 loss -> ARMED (WARNING). Exactly $1,500.00 loss -> HALTED_DAILY_LOSS."""
        re = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
        now = datetime.now(timezone.utc)

        # 1. Drawdown of $1,499.99 ($48,500.01 equity)
        s1 = re.evaluate_account_state(48500.01, 48500.01, -1499.99, 0.0, now)
        assert s1 == BreakerStatus.ARMED
        assert re.status == BreakerStatus.ARMED
        assert re.risk_level == RiskLevel.WARNING
        assert re.current_drawdown_dollars == 1499.99

        # Order request approved in warning mode (with 1.0% risk cap)
        res1 = re.evaluate_order_request("AAPL", "BUY", 100, 100.0, 98.0, 48500.01, 190000.0,
                                         0, set(), set())
        assert res1.approved is True

        # 2. Drawdown of $1,500.00 ($48,500.00 equity)
        s2 = re.evaluate_account_state(48500.00, 48500.00, -1500.00, 0.0, now)
        assert s2 == BreakerStatus.HALTED_DAILY_LOSS
        assert re.status == BreakerStatus.HALTED_DAILY_LOSS
        assert re.risk_level == RiskLevel.HALTED
        assert re.current_drawdown_dollars == 1500.00

        # Subsequent new entry order REJECTED
        res2 = re.evaluate_order_request("AAPL", "BUY", 100, 100.0, 98.0, 48500.00, 190000.0,
                                         0, set(), set())
        assert res2.approved is False
        assert res2.rejection_code == "CIRCUIT_BREAKER_HALTED"

        # Position exit order APPROVED even while HALTED
        res_exit = re.evaluate_order_request("AAPL", "SELL", 100, 100.0, 98.0, 48500.00, 190000.0,
                                             1, {"AAPL"}, {"Technology"}, is_exit=True)
        assert res_exit.approved is True
        assert "APPROVED_EXIT" in res_exit.reason

    def test_position_notional_cap_25k_boundary(self):
        """Max position equity pct is 0.50 ($25,000 on $50k equity).
        Test exact $100.00 vs $100.01 entry prices.
        """
        re = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

        # At $100.00: 25000 / 100.00 = 250 shares. Notional = $25,000.00.
        res1 = re.evaluate_order_request("AAPL", "BUY", 500, 100.00, 99.50, 50000.00, 200000.00,
                                         0, set(), set())
        assert res1.approved is True
        assert res1.authorized_qty == 250
        assert res1.authorized_qty * 100.00 <= 25000.00

        # At $100.01: 25000 / 100.01 = 249.975... -> 249 shares.
        # 250 shares would be $25,002.50 (> $25,000.00), so must be 249!
        res2 = re.evaluate_order_request("AAPL", "BUY", 500, 100.01, 99.51, 50000.00, 200000.00,
                                         0, set(), set())
        assert res2.approved is True
        assert res2.authorized_qty == 249
        assert res2.authorized_qty * 100.01 <= 25000.00

        # At $500.00: 25000 / 500.00 = 50 shares.
        res3 = re.evaluate_order_request("SPY", "BUY", 100, 500.00, 497.00, 50000.00, 200000.00,
                                         0, set(), set())
        assert res3.approved is True
        assert res3.authorized_qty == 50

        # At $26,000.00: 25000 / 26000 = 0 shares -> Rejection!
        res4 = re.evaluate_order_request("BRK.A", "BUY", 1, 26000.00, 25800.00, 50000.00, 200000.00,
                                         0, set(), set())
        assert res4.approved is False
        assert "INSUFFICIENT_RISK_BUDGET" in res4.reason

    def test_stop_distance_range_and_float_tolerance_eps(self):
        """0.40% to 4.00% stop range with EPS = 1e-6.
        Adversarially test exact boundaries, EPS margins, and float representation.
        """
        re = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

        # 1. Exact 0.40% stop distance ($100 entry, $99.60 stop -> dist = 0.40 -> 0.004)
        res_exact_min = re.evaluate_order_request("AAPL", "BUY", 100, 100.00, 99.60, 50000.0, 200000.0,
                                                  0, set(), set())
        assert res_exact_min.approved is True

        # 2. Stop distance just inside EPS tolerance (0.004 - 5e-7) -> APPROVED
        res_eps_min_in = re.evaluate_order_request("AAPL", "BUY", 100, 100.00, 100.00 - (0.40 - 5e-5),
                                                   50000.0, 200000.0, 0, set(), set())
        assert res_eps_min_in.approved is True

        # 3. Stop distance beyond EPS tolerance (0.004 - 2e-6) -> REJECTED
        res_eps_min_out = re.evaluate_order_request("AAPL", "BUY", 100, 100.00, 100.00 - (0.40 - 3e-4),
                                                    50000.0, 200000.0, 0, set(), set())
        assert res_eps_min_out.approved is False
        assert "STOP_DISTANCE_TOO_TIGHT" in res_eps_min_out.reason

        # 4. Exact 4.00% stop distance ($100 entry, $96.00 stop -> dist = 4.00 -> 0.040)
        res_exact_max = re.evaluate_order_request("AAPL", "BUY", 100, 100.00, 96.00, 50000.0, 200000.0,
                                                  0, set(), set())
        assert res_exact_max.approved is True

        # 5. Stop distance just inside max EPS tolerance -> APPROVED
        res_eps_max_in = re.evaluate_order_request("AAPL", "BUY", 100, 100.00, 100.00 - (4.00 + 5e-5),
                                                   50000.0, 200000.0, 0, set(), set())
        assert res_eps_max_in.approved is True

        # 6. Stop distance beyond max EPS tolerance -> REJECTED
        res_eps_max_out = re.evaluate_order_request("AAPL", "BUY", 100, 100.00, 100.00 - (4.00 + 3e-4),
                                                    50000.0, 200000.0, 0, set(), set())
        assert res_eps_max_out.approved is False
        assert "STOP_DISTANCE_TOO_WIDE" in res_eps_max_out.reason

    @pytest.mark.parametrize("price", [9.97, 10.00, 47.31, 100.00, 233.33, 400.00, 1041.07])
    def test_float_tolerance_on_arbitrary_stock_prices(self, price: float):
        """Test IEEE-754 float precision across weird non-integer stock prices."""
        re = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))

        # Test at min boundary (0.4%)
        min_stop_dist = price * 0.004
        stop_price_min = price - min_stop_dist
        res_min = re.evaluate_order_request("TEST", "BUY", 10, price, stop_price_min,
                                            50000.0, 200000.0, 0, set(), set())
        assert res_min.approved is True, f"Failed at price {price} min stop: {res_min.reason}"

        # Test at max boundary (4.0%)
        max_stop_dist = price * 0.040
        stop_price_max = price - max_stop_dist
        res_max = re.evaluate_order_request("TEST", "BUY", 10, price, stop_price_max,
                                            50000.0, 200000.0, 0, set(), set())
        assert res_max.approved is True, f"Failed at price {price} max stop: {res_max.reason}"


# ==============================================================================
# SECTION 5: MICROSTRUCTURE STRESS: PARTIAL FILLS, WHIPSAWS & MICRO-CRASHES
# ==============================================================================

class TestMicrostructureStressPartialFillsWhipsawsCrashes:
    """Stress-test partial fills, whipsaw quotes, and fast micro-crashes."""

    def test_entry_partial_fill_protects_only_filled_quantity(self):
        """Entry order requested 100 shares, but fills only 40 shares.
        Bracket must resize total_qty to 40, stop to 40, T1 to 20, T2 to 20.
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_entry_part", "NVDA", "LONG", 100, 100.00, 98.00, timestamp=now)

        d = bm.activate_bracket_on_fill("b_entry_part", 40, 100.00, now)
        assert d.action == "SUBMIT_ORDERS"
        assert brk.total_qty == 40
        assert brk.remaining_qty == 40
        assert brk.target_1_qty == 20
        assert brk.target_2_qty == 20

        # Orders submitted: stop for 40, t1 for 20, t2 for 20
        orders = {o["type"]: o["qty"] for o in d.orders_to_submit}
        assert orders["STOP"] == 40
        assert orders["LIMIT"] in (20, 40)  # T1 is 20, T2 is 20
        assert sum(o["qty"] for o in d.orders_to_submit if o["type"] == "LIMIT") == 40

    def test_target_2_multi_step_micro_fills(self):
        """Target 2 fills in 4 micro chunks: 10, 15, 15, 10 shares.
        Stop order must resize monotonically down to 0, completing the bracket.
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_t2_micro", "AAPL", "LONG", 100, 100.00, 98.00, timestamp=now)
        bm.activate_bracket_on_fill("b_t2_micro", 100, 100.00, now)

        # T1 fills 50 shares
        bm.on_child_order_fill(brk.target_1_order_id, 101.60, 50, now)
        assert brk.remaining_qty == 50

        # Micro-fill 1: 10 shares of T2
        d1 = bm.on_child_order_fill(brk.target_2_order_id, 103.60, 10, now)
        assert d1.action == "MODIFY_ORDER"
        assert brk.remaining_qty == 40
        assert d1.orders_to_modify[0]["new_qty"] == 40

        # Micro-fill 2: 15 shares of T2
        d2 = bm.on_child_order_fill(brk.target_2_order_id, 103.60, 15, now)
        assert brk.remaining_qty == 25
        assert d2.orders_to_modify[0]["new_qty"] == 25

        # Micro-fill 3: 15 shares of T2
        d3 = bm.on_child_order_fill(brk.target_2_order_id, 103.60, 15, now)
        assert brk.remaining_qty == 10
        assert d3.orders_to_modify[0]["new_qty"] == 10

        # Micro-fill 4: final 10 shares of T2
        d4 = bm.on_child_order_fill(brk.target_2_order_id, 103.60, 10, now)
        assert d4.action == "CANCEL_ORDER"
        assert d4.bracket_status == BracketStatus.COMPLETED_PROFIT
        assert brk.remaining_qty == 0
        assert "AAPL" not in bm.symbol_to_bracket

    def test_target_1_partial_fill_adversarial_vulnerability(self):
        """ADVERSARIAL CHALLENGE: Target 1 partial fill followed by Stop Loss hit!
        Target 1 is 50 shares. Fills 20 shares (leaving 30 shares unfilled).
        Market then collapses and hits stop loss.
        Does DynamicBracketManager cancel the remaining 30 shares of Target 1 order?
        """
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)
        brk = bm.create_bracket("b_t1_part", "NVDA", "LONG", 100, 100.00, 98.00, timestamp=now)
        bm.activate_bracket_on_fill("b_t1_part", 100, 100.00, now)

        # Target 1 partially fills: 20 shares out of 50
        d_part = bm.on_child_order_fill(brk.target_1_order_id, 101.60, 20, now)
        assert brk.remaining_qty == 80

        # Market now reverses and fills the stop order for 80 shares
        d_stop = bm.on_child_order_fill(brk.stop_order_id, 98.00, 80, now)
        assert d_stop.action == "CANCEL_ORDER"
        assert d_stop.bracket_status == BracketStatus.COMPLETED_STOP

        # EMPIRICAL AUDIT: Inspect orders_to_cancel!
        # Because line 334 set target_1_filled = True, line 317:
        # `if bracket.target_1_order_id and not bracket.target_1_filled:`
        # will evaluate `not True` -> False!
        # Target 1 order will NOT be cancelled, leaving an orphaned order!
        print(f"\n[AUDIT] Target 1 order ID: {brk.target_1_order_id}")
        print(f"[AUDIT] Orders cancelled on stop: {d_stop.orders_to_cancel}")
        t1_cancelled = brk.target_1_order_id in d_stop.orders_to_cancel
        print(f"[AUDIT] Was partially filled Target 1 cancelled? {t1_cancelled}")

        # Empirical finding: t1_cancelled is False!
        # This confirms the vulnerability: bracket.py:317 checks `not bracket.target_1_filled`,
        # but line 334 set target_1_filled = True on partial fill, so the order is never cancelled!
        assert t1_cancelled is False

    def test_whipsaw_bar_stop_precedence(self):
        """Whipsaw bar touches BOTH stop loss (98.00) and profit target (101.60).
        Execution engine must process STOP first and break, preventing double execution.
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        # Create position and working orders
        account.apply_fill("ord_entry", "AAPL", "BUY", 100, 100.00, 0.0, now)
        stop_ord = engine.create_order("AAPL", OrderSide.SELL, OrderType.STOP, 100,
                                       stop_price=98.00)
        t1_ord = engine.create_order("AAPL", OrderSide.SELL, OrderType.LIMIT, 50,
                                     limit_price=101.60)
        engine.submit_order(stop_ord.id)
        engine.submit_order(t1_ord.id)

        assert stop_ord.id in engine.working_orders
        assert t1_ord.id in engine.working_orders

        # Bar opens at 100.00, surges to 102.00, crashes to 97.00, closes at 99.00
        fills = engine.process_bar("AAPL", open_=100.00, high=102.00, low=97.00,
                                   close=99.00, volume=100000, timestamp=now)

        # Invariant: Only ONE fill (the stop) should occur; limit order must NOT fill!
        assert len(fills) == 1
        assert fills[0].order_id == stop_ord.id
        assert fills[0].side == OrderSide.SELL
        assert fills[0].qty == 100

    def test_impulse_bar_blows_through_both_targets(self):
        """Impulse bar surges from 100.50 to 105.00, blowing past BOTH T1 (101.60) and T2 (103.60).
        Both limit orders fill, position is closed for 100 shares.
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        account.apply_fill("ord_entry", "NVDA", "BUY", 100, 100.00, 0.0, now)
        stop_ord = engine.create_order("NVDA", OrderSide.SELL, OrderType.STOP, 100, stop_price=98.00)
        t1_ord = engine.create_order("NVDA", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=101.60)
        t2_ord = engine.create_order("NVDA", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=103.60)
        engine.submit_order(stop_ord.id)
        engine.submit_order(t1_ord.id)
        engine.submit_order(t2_ord.id)

        # Bar surges: open=100.50, high=105.00, low=100.50, close=104.50, vol=100000
        fills = engine.process_bar("NVDA", open_=100.50, high=105.00, low=100.50,
                                   close=104.50, volume=100000, timestamp=now)

        assert len(fills) == 2
        fill_orders = {f.order_id for f in fills}
        assert stop_ord.id not in fill_orders
        assert t1_ord.id in fill_orders
        assert t2_ord.id in fill_orders
        assert sum(f.qty for f in fills) == 100
        # Position is now flat (deleted from positions dictionary)
        assert "NVDA" not in account.positions or account.positions["NVDA"].shares == 0

    def test_target_1_partial_fill_orphans_limit_order_in_engine_end_to_end(self):
        """End-to-end integration test demonstrating the Target 1 orphan limit order defect:
        1. Buy 100 shares of AAPL.
        2. Target 1 (50 shares) partially fills 20 shares.
        3. Stop loss fills for the remaining 80 shares (position closed to 0).
        4. Defect in bracket.py:317 causes remaining 30 shares of Target 1 order to NEVER be cancelled.
        5. A subsequent price rally fills the orphaned order, creating a phantom SHORT position!
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        engine = ExecutionEngine(account=account)
        bm = DynamicBracketManager()
        now = datetime.now(timezone.utc)

        # 1. Fill entry order: 100 shares @ $100.00
        account.apply_fill("ord_entry", "AAPL", "BUY", 100, 100.00, 0.0, now)
        brk = bm.create_bracket("brk_e2e_orphan", "AAPL", "LONG", 100, 100.00, 98.00, timestamp=now)
        bm.activate_bracket_on_fill(brk.bracket_id, 100, 100.00, now)

        # Materialize working orders in execution engine
        stop_ord = engine.create_order("AAPL", OrderSide.SELL, OrderType.STOP, 100, stop_price=brk.current_stop_price)
        t1_ord = engine.create_order("AAPL", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=brk.target_1_price)
        t2_ord = engine.create_order("AAPL", OrderSide.SELL, OrderType.LIMIT, 50, limit_price=brk.target_2_price)
        engine.submit_order(stop_ord.id)
        engine.submit_order(t1_ord.id)
        engine.submit_order(t2_ord.id)

        # Update manager with real order IDs
        bm.order_to_bracket[stop_ord.id] = (brk.bracket_id, BracketChildType.STOP_LOSS)
        bm.order_to_bracket[t1_ord.id] = (brk.bracket_id, BracketChildType.TAKE_PROFIT_1)
        bm.order_to_bracket[t2_ord.id] = (brk.bracket_id, BracketChildType.TAKE_PROFIT_2)
        brk.stop_order_id = stop_ord.id
        brk.target_1_order_id = t1_ord.id
        brk.target_2_order_id = t2_ord.id

        # 2. Target 1 partially fills: 20 shares
        fill_t1_part = engine._execute_fill(t1_ord, 20, brk.target_1_price, 0.0, now)
        dir_t1 = bm.on_child_order_fill(t1_ord.id, brk.target_1_price, 20, now)
        assert dir_t1.action == "MODIFY_ORDER"
        assert brk.remaining_qty == 80
        # Modify stop order qty to 80 in engine
        stop_ord.remaining_qty = 80
        stop_ord.qty = 80

        # Remaining quantity of T1 order in engine is 30 shares
        assert t1_ord.remaining_qty == 30
        assert t1_ord.id in engine.working_orders

        # 3. Market crashes and triggers Stop order for 80 shares
        fill_stop = engine._execute_fill(stop_ord, 80, 98.00, 0.0, now)
        dir_stop = bm.on_child_order_fill(stop_ord.id, 98.00, 80, now)
        assert dir_stop.action == "CANCEL_ORDER"
        assert dir_stop.bracket_status == BracketStatus.COMPLETED_STOP

        # Process orders_to_cancel from directive
        for oid in dir_stop.orders_to_cancel:
            if oid in engine.working_orders:
                engine.cancel_order(oid, reason="STOP_TRIGGERED")

        # EMPIRICAL OBSERVATION:
        # T2 order was in orders_to_cancel and got cancelled cleanly:
        assert t2_ord.id not in engine.working_orders
        # BUT T1 order was NOT in orders_to_cancel because bracket.py:317 checked `not target_1_filled`!
        # So T1 is STILL working in the engine!
        assert t1_ord.id in engine.working_orders, "Bug confirmed: T1 order orphaned in working orders!"
        # Account position is completely closed (0 shares):
        assert "AAPL" not in account.positions

        # 4. Now price rallies back to $102.00 on a subsequent bar:
        fills_rally = engine.process_bar("AAPL", open_=101.00, high=102.00, low=101.00, close=101.80, volume=100000, timestamp=now)
        # The orphaned T1 order fills its remaining 30 shares!
        assert len(fills_rally) == 1
        assert fills_rally[0].order_id == t1_ord.id
        assert fills_rally[0].qty == 30

        # Disastrous outcome: account now holds an unintended naked SHORT of 30 shares!
        assert "AAPL" in account.positions
        assert account.positions["AAPL"].side == PositionSide.SHORT
        assert account.positions["AAPL"].shares == 30


    def test_fast_micro_crash_and_circuit_breaker_emergency_halt(self):
        """Micro-crash scenario: Stock gaps down 10% on a full $25,000 position.
        Stop fills with gap slippage, drawdown breaches $1,500, circuit breaker halts.
        """
        account = PaperTradingAccount(initial_cash=50000.00)
        re = InstitutionalRiskEngine(RiskEngineConfig(starting_equity=50000.00))
        engine = ExecutionEngine(account=account)
        now = datetime.now(timezone.utc)

        # Enter 250 shares @ $100.00 ($25,000 position)
        account.apply_fill("ord_entry", "AAPL", "BUY", 250, 100.00, 0.0, now)
        stop_ord = engine.create_order("AAPL", OrderSide.SELL, OrderType.STOP, 250, stop_price=98.00)
        engine.submit_order(stop_ord.id)

        # Market flash crashes: Bar opens at $90.00, drops to $88.00!
        fills = engine.process_bar("AAPL", open_=90.00, high=90.00, low=88.00,
                                   close=89.00, volume=100000, timestamp=now)

        assert len(fills) == 1
        fill = fills[0]
        # Invariant: fill price is at or below open ($90.00), not at the stop ($98.00)!
        assert fill.price <= 90.00

        # Evaluate account state after fill
        status = re.evaluate_account_state(
            equity=account.equity,
            cash=account.cash,
            realized_pnl=account.realized_pnl,
            unrealized_pnl=account.unrealized_pnl,
            timestamp=now,
        )

        # Realized loss: 250 * ($90 - $100) = -$2,500.00 (exceeds $1,500 hard daily limit!)
        assert account.realized_pnl <= -2500.00
        assert status == BreakerStatus.HALTED_DAILY_LOSS
        assert re.status == BreakerStatus.HALTED_DAILY_LOSS
        assert re.risk_level == RiskLevel.HALTED
        assert re.current_drawdown_dollars >= 2500.00

        # Verify circuit breaker trips emergency protocol
        directive = re.trip_circuit_breaker(account.equity, re.current_drawdown_dollars, now)
        assert directive["action"] == "EMERGENCY_HALT"
        assert directive["purge_orders"] is True
        assert directive["liquidate_positions"] is True


if __name__ == "__main__":
    pytest.main(["-v", __file__])
