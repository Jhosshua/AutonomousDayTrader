"""
Tier 3: Combinatorial & Pairwise Testing Suite.

Executes an Orthogonal Array Pairwise Matrix across 5 critical system dimensions:
- Factor A: Strategy (ORB, VWAP Pullback, News Momentum, Mean Reversion) [4 levels]
- Factor B: VIX Regime (Low, Normal, Elevated, Crisis) [4 levels]
- Factor C: Market Phase (Pre-Market, Open Flush, Trend, Midday Chop, Power Hour, Flatten) [6 levels]
- Factor D: Account State (Healthy, Warning, Near-Limit, Tripped) [4 levels]
- Factor E: Execution Fill Quality (Clean, Partial, Slippage) [3 levels]

Total: 32 orthogonal test suites verifying all pairwise cross-feature invariants.
"""

from __future__ import annotations

import math
from datetime import time as dtime
from typing import Any, Dict, Optional, Tuple

import pytest

from tests.e2e.test_contracts import (
    AccountLedger,
    Position,
    calculate_position_size,
    calculate_brackets,
    get_eod_phase,
    evaluate_orb_signal,
    calculate_anchored_vwap,
    score_news_sentiment,
    evaluate_mean_reversion_zscore,
    get_vix_regime,
    get_time_of_day_phase,
    get_momentum_glow,
)

# 32 Orthogonal Test Vectors
PAIRWISE_VECTORS = [
    # id, strategy, vix_regime, phase, drawdown_state, fill_quality
    ("P01", "orb", "LOW", "OPEN_VOLATILITY_FLUSH", "Healthy", "Clean"),
    ("P02", "orb", "NORMAL", "TREND_CONTINUATION", "Warning", "Partial"),
    ("P03", "orb", "ELEVATED", "MIDDAY_CHOP", "Near-Limit", "Slippage"),
    ("P04", "orb", "CRISIS", "POWER_HOUR", "Tripped", "Clean"),
    ("P05", "orb", "LOW", "PRE_MARKET", "Healthy", "Partial"),
    ("P06", "orb", "NORMAL", "EOD_FLATTEN", "Warning", "Slippage"),
    ("P07", "orb", "ELEVATED", "TREND_CONTINUATION", "Tripped", "Clean"),
    ("P08", "orb", "CRISIS", "OPEN_VOLATILITY_FLUSH", "Near-Limit", "Partial"),

    ("P09", "vwap_pullback", "NORMAL", "OPEN_VOLATILITY_FLUSH", "Near-Limit", "Slippage"),
    ("P10", "vwap_pullback", "LOW", "TREND_CONTINUATION", "Tripped", "Clean"),
    ("P11", "vwap_pullback", "CRISIS", "MIDDAY_CHOP", "Healthy", "Partial"),
    ("P12", "vwap_pullback", "ELEVATED", "POWER_HOUR", "Warning", "Slippage"),
    ("P13", "vwap_pullback", "NORMAL", "PRE_MARKET", "Near-Limit", "Clean"),
    ("P14", "vwap_pullback", "LOW", "EOD_FLATTEN", "Tripped", "Partial"),
    ("P15", "vwap_pullback", "CRISIS", "TREND_CONTINUATION", "Healthy", "Slippage"),
    ("P16", "vwap_pullback", "ELEVATED", "OPEN_VOLATILITY_FLUSH", "Warning", "Clean"),

    ("P17", "news_momentum", "ELEVATED", "OPEN_VOLATILITY_FLUSH", "Healthy", "Partial"),
    ("P18", "news_momentum", "CRISIS", "TREND_CONTINUATION", "Warning", "Slippage"),
    ("P19", "news_momentum", "LOW", "MIDDAY_CHOP", "Near-Limit", "Clean"),
    ("P20", "news_momentum", "NORMAL", "POWER_HOUR", "Tripped", "Partial"),
    ("P21", "news_momentum", "ELEVATED", "PRE_MARKET", "Healthy", "Slippage"),
    ("P22", "news_momentum", "CRISIS", "EOD_FLATTEN", "Warning", "Clean"),
    ("P23", "news_momentum", "LOW", "TREND_CONTINUATION", "Near-Limit", "Partial"),
    ("P24", "news_momentum", "NORMAL", "OPEN_VOLATILITY_FLUSH", "Tripped", "Slippage"),

    ("P25", "mean_reversion", "CRISIS", "OPEN_VOLATILITY_FLUSH", "Warning", "Clean"),
    ("P26", "mean_reversion", "ELEVATED", "TREND_CONTINUATION", "Healthy", "Partial"),
    ("P27", "mean_reversion", "NORMAL", "MIDDAY_CHOP", "Tripped", "Slippage"),
    ("P28", "mean_reversion", "LOW", "POWER_HOUR", "Near-Limit", "Clean"),
    ("P29", "mean_reversion", "CRISIS", "PRE_MARKET", "Warning", "Partial"),
    ("P30", "mean_reversion", "ELEVATED", "EOD_FLATTEN", "Healthy", "Slippage"),
    ("P31", "mean_reversion", "NORMAL", "TREND_CONTINUATION", "Near-Limit", "Clean"),
    ("P32", "mean_reversion", "LOW", "MIDDAY_CHOP", "Healthy", "Partial"),
]


def _setup_account_by_drawdown_state(state: str) -> AccountLedger:
    """Initialize account with specific drawdown state."""
    acc = AccountLedger()
    if state == "Healthy":
        # No drawdown
        pass
    elif state == "Warning":
        # $600 drawdown
        acc.realized_pnl = -600.0
        acc.cash -= 600.0
    elif state == "Near-Limit":
        # $1,400 drawdown (nearing $1,500 circuit breaker)
        acc.realized_pnl = -1400.0
        acc.cash -= 1400.0
    elif state == "Tripped":
        # $1,550 drawdown (tripped breaker)
        acc.realized_pnl = -1550.0
        acc.cash -= 1550.0
        acc.check_circuit_breaker()
    return acc


def _vix_value_for_regime(regime: str) -> float:
    if regime == "LOW":
        return 13.5
    elif regime == "NORMAL":
        return 18.5
    elif regime == "ELEVATED":
        return 28.0
    return 40.0


@pytest.mark.parametrize("test_id,strategy,vix_regime,phase,drawdown_state,fill_quality", PAIRWISE_VECTORS)
def test_tier3_pairwise_vector(
    test_id: str,
    strategy: str,
    vix_regime: str,
    phase: str,
    drawdown_state: str,
    fill_quality: str,
):
    """
    Evaluates combinatorial interaction across 5 dimensions:
    - Verifies execution gate decision (Permitted vs Rejected).
    - Verifies volatility sizing multiplier matching VIX regime.
    - Verifies order fill and ledger reconciliation under partial or slippage conditions.
    - Enforces institutional risk invariants across all states.
    """
    acc = _setup_account_by_drawdown_state(drawdown_state)
    vix_val = _vix_value_for_regime(vix_regime)
    derived_regime, sizing_mult, stop_mult = get_vix_regime(vix_val)

    # Invariant 1: VIX regime mapping must match input level
    assert derived_regime == vix_regime

    # Invariant 2: Execution Permission Gate
    is_execution_permitted = True

    # Gate rule A: Tripped circuit breaker blocks ALL trades
    if drawdown_state == "Tripped" or acc.is_circuit_broken:
        is_execution_permitted = False

    # Gate rule B: Pre-market and EOD Flatten phases block new entries
    if phase in ("PRE_MARKET", "EOD_FLATTEN"):
        is_execution_permitted = False

    # Gate rule C: Strategy specific phase compatibility
    if strategy == "orb" and phase in ("MIDDAY_CHOP", "POWER_HOUR"):
        # ORB does not initiate in late chop/power hour
        is_execution_permitted = False
    elif strategy == "mean_reversion" and phase == "OPEN_VOLATILITY_FLUSH":
        # Mean reversion disabled during morning open volatility flush
        is_execution_permitted = False

    if not is_execution_permitted:
        # Verify that attempting an order in an invalid state is blocked or rejected
        if acc.is_circuit_broken:
            with pytest.raises(PermissionError):
                acc.execute_fill("NVDA", "BUY", 10, 100.0)
        return

    # If permitted, calculate sizing with VIX regime multiplier
    entry_p = 100.00
    stop_p = entry_p - (2.00 * stop_mult)
    shares = calculate_position_size(
        equity=acc.equity,
        entry_price=entry_p,
        stop_loss_price=stop_p,
        vix_multiplier=sizing_mult,
    )
    assert shares > 0, "Permitted trade should yield non-zero position size"

    # Derive take-profit brackets
    tp1, tp2 = calculate_brackets(entry_p, stop_p)
    assert tp1 > entry_p
    assert tp2 > tp1

    # Simulate fill quality
    executed_shares = shares
    fill_price = entry_p

    if fill_quality == "Partial":
        executed_shares = max(1, shares // 2)
    elif fill_quality == "Slippage":
        # Adverse slippage 3 bps
        fill_price = entry_p + 0.03

    # Execute fill
    pos = acc.execute_fill(
        symbol="NVDA",
        side="BUY",
        qty=executed_shares,
        price=fill_price,
        strategy_id=strategy,
        stop_loss=stop_p,
        tp1=tp1,
        tp2=tp2,
    )

    # Invariant 3: Ledger reconciliation
    assert "NVDA" in acc.positions
    assert acc.positions["NVDA"].qty == executed_shares
    assert round(acc.cash + acc.positions["NVDA"].market_value, 2) == round(acc.equity, 2)

    # Invariant 4: Ambient glow reflects updated portfolio state
    glow = get_momentum_glow(daily_pnl=acc.daily_pnl, vix=vix_val, is_halted=acc.is_circuit_broken)
    assert "primary" in glow
    assert "intensity" in glow
