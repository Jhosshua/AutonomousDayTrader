"""backend/app/core/risk.py
Institutional Risk Engine, Circuit Breakers ($1,500 hard daily loss limit), and Position Sizing.
"""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, Optional, Set, Tuple
from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    NORMAL = "NORMAL"          # Drawdown < 2.0% ($1,000)
    WARNING = "WARNING"        # Drawdown >= 2.0% and < 3.0% ($1,000 - $1,499.99)
    HALTED = "HALTED"          # Drawdown >= 3.0% ($1,500.00)


class BreakerStatus(str, Enum):
    ARMED = "ARMED"                          # Normal operational state
    TRIGGERED = "TRIGGERED"                  # Circuit breaker tripped, liquidation in flight
    HALTED_DAILY_LOSS = "HALTED_DAILY_LOSS"  # Emergency halt active, all trading frozen for session


class RiskCheckResult(BaseModel):
    approved: bool
    reason: str
    requested_qty: int
    authorized_qty: int
    estimated_risk_dollars: float
    risk_level: RiskLevel
    rejection_code: Optional[str] = None


class RiskEngineConfig(BaseModel):
    starting_equity: float = 50000.00
    hard_max_daily_loss_dollars: float = 1500.00
    hard_max_daily_loss_pct: float = 0.030
    warning_loss_dollars: float = 1000.00
    warning_loss_pct: float = 0.020
    base_trade_risk_pct: float = 0.010       # 1.0% ($500)
    max_trade_risk_pct: float = 0.020        # 2.0% ($1,000 ceiling)
    max_trade_risk_dollars: float = 1000.00
    max_position_equity_pct: float = 1.000   # $50,000 max single position (100% of equity / 25% of DTBP)
    max_concurrent_positions: int = 3
    min_stop_distance_pct: float = 0.004     # 0.4%
    max_stop_distance_pct: float = 0.040     # 4.0%


class InstitutionalRiskEngine:
    """
    Autonomous Institutional Risk Gatekeeper & Real-Time Circuit Breaker.
    Enforces $1,500 daily drawdown limit, position sizing, concurrency, and sector limits.
    """

    def __init__(self, config: Optional[RiskEngineConfig] = None) -> None:
        self.config: RiskEngineConfig = config or RiskEngineConfig()
        self.status: BreakerStatus = BreakerStatus.ARMED
        self.risk_level: RiskLevel = RiskLevel.NORMAL
        self.daily_peak_equity: float = self.config.starting_equity
        self.current_drawdown_dollars: float = 0.0
        self.current_drawdown_pct: float = 0.0
        self.breaker_triggered_at: Optional[datetime] = None
        self.breaker_trigger_equity: Optional[float] = None
        self.symbol_sectors: Dict[str, str] = {
            "SPY": "Index",
            "QQQ": "Index",
            "AAPL": "Technology",
            "NVDA": "Technology",
            "TSLA": "Consumer Discretionary",
            "MSFT": "Technology",
            "AMZN": "Consumer Discretionary",
            "GOOGL": "Communication Services",
            "META": "Communication Services",
        }

    def register_symbol_sector(self, symbol: str, sector: str) -> None:
        """Register sector classification for symbol correlation enforcement."""
        self.symbol_sectors[symbol.upper()] = sector

    def evaluate_account_state(
        self,
        equity: float,
        cash: float,
        realized_pnl: float,
        unrealized_pnl: float,
        timestamp: datetime,
    ) -> BreakerStatus:
        """
        Evaluate real-time portfolio metrics on every incoming tick, quote, or bar.
        Calculates daily drawdown = max(0, starting_equity - equity).
        If daily loss >= $1,500.00, immediately transitions status to HALTED_DAILY_LOSS.
        """
        if equity > self.daily_peak_equity:
            self.daily_peak_equity = equity

        # Drawdown measured against starting equity ($50,000.00)
        dd_dollars = max(0.0, round(self.config.starting_equity - equity, 2))
        self.current_drawdown_dollars = dd_dollars
        self.current_drawdown_pct = round(dd_dollars / self.config.starting_equity, 4)

        if self.status == BreakerStatus.HALTED_DAILY_LOSS:
            return self.status

        # Hard daily loss check: exact dollar comparison
        if dd_dollars >= self.config.hard_max_daily_loss_dollars:
            self.status = BreakerStatus.HALTED_DAILY_LOSS
            self.risk_level = RiskLevel.HALTED
            self.breaker_triggered_at = timestamp
            self.breaker_trigger_equity = equity
            return BreakerStatus.HALTED_DAILY_LOSS
        elif dd_dollars >= self.config.warning_loss_dollars:
            self.risk_level = RiskLevel.WARNING
        else:
            self.risk_level = RiskLevel.NORMAL

        return self.status

    def evaluate_order_request(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        requested_qty: int,
        entry_price: float,
        stop_price: float,
        account_equity: float,
        buying_power: float,
        active_positions_count: int,
        active_symbols: Set[str],
        active_sectors: Set[str],
        vix_multiplier: float = 1.0,
        is_entry_lockout_active: bool = False,
        is_exit: bool = False,
    ) -> RiskCheckResult:
        """
        Pre-Trade Approval Gate.
        Evaluates an order request against institutional guardrails and calculates risk-adjusted sizing.
        """
        symbol = symbol.upper()

        # Position reducing or liquidation orders bypass entry lockouts, circuit halts, and sizing constraints
        if is_exit:
            return RiskCheckResult(
                approved=True,
                reason="APPROVED_EXIT: Position reducing or liquidation order approved",
                requested_qty=requested_qty,
                authorized_qty=requested_qty,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
            )

        # 1. Circuit Breaker Check
        if self.status != BreakerStatus.ARMED:
            return RiskCheckResult(
                approved=False,
                reason=f"CIRCUIT_BREAKER_HALTED: Trading halted due to maximum daily loss ({self.status.value})",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="CIRCUIT_BREAKER_HALTED",
            )

        # 2. Session Time Lockout Check
        if is_entry_lockout_active:
            return RiskCheckResult(
                approved=False,
                reason="ENTRY_LOCKOUT_ACTIVE: Session closeout protocol active, new entries forbidden",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="ENTRY_LOCKOUT_ACTIVE",
            )

        # 3. Concurrency Limit Check (if not already holding this symbol)
        if symbol not in active_symbols and active_positions_count >= self.config.max_concurrent_positions:
            return RiskCheckResult(
                approved=False,
                reason=f"MAX_CONCURRENT_POSITIONS_REACHED: Limit of {self.config.max_concurrent_positions} open positions reached",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="MAX_CONCURRENT_POSITIONS_REACHED",
            )

        # 4. Sector Diversification Check
        sector = self.symbol_sectors.get(symbol)
        if sector and sector != "Index" and symbol not in active_symbols and sector in active_sectors:
            return RiskCheckResult(
                approved=False,
                reason=f"CORRELATED_SECTOR_EXPOSURE: Another active position already exists in sector '{sector}'",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="CORRELATED_SECTOR_EXPOSURE",
            )

        # 5. Stop Distance Safety Boundary Check
        stop_dist = abs(entry_price - stop_price)
        direction_is_valid = (
            (side.upper() == "BUY" and stop_price < entry_price)
            or (side.upper() == "SELL" and stop_price > entry_price)
        )
        if entry_price <= 0 or stop_dist <= 0 or not direction_is_valid:
            return RiskCheckResult(
                approved=False,
                reason="INVALID_PRICE_GEOMETRY: Entry/stop direction or distance is invalid",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="INVALID_PRICE_GEOMETRY",
            )

        stop_dist_pct = stop_dist / entry_price
        EPS = 1e-6  # Tolerance for IEEE 754 floating-point representation discrepancies
        if stop_dist_pct < self.config.min_stop_distance_pct - EPS:
            return RiskCheckResult(
                approved=False,
                reason=f"STOP_DISTANCE_TOO_TIGHT: Stop distance {stop_dist_pct:.4f} < min {self.config.min_stop_distance_pct:.4f}",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="STOP_DISTANCE_TOO_TIGHT",
            )

        if stop_dist_pct > self.config.max_stop_distance_pct + EPS:
            return RiskCheckResult(
                approved=False,
                reason=f"STOP_DISTANCE_TOO_WIDE: Stop distance {stop_dist_pct:.4f} > max {self.config.max_stop_distance_pct:.4f}",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="STOP_DISTANCE_TOO_WIDE",
            )

        # 6. Risk-Adjusted Sizing Calculation
        # In warning mode, enforce strict 1.0% limit; otherwise use base_trade_risk_pct scaled by VIX
        risk_pct = self.config.base_trade_risk_pct if self.risk_level == RiskLevel.WARNING else min(
            self.config.max_trade_risk_pct, self.config.base_trade_risk_pct * vix_multiplier
        )
        target_risk_dollars = min(
            self.config.max_trade_risk_dollars,
            round(account_equity * risk_pct, 2)
        )

        q_risk = int(math.floor(target_risk_dollars / stop_dist))
        # 25% max position concentration
        max_notional = account_equity * self.config.max_position_equity_pct
        q_alloc = int(math.floor(max_notional / entry_price))
        # Buying power capacity
        q_bp = int(math.floor(buying_power / entry_price))

        authorized_qty = min(q_risk, q_alloc, q_bp)
        if authorized_qty < 1:
            return RiskCheckResult(
                approved=False,
                reason=f"INSUFFICIENT_RISK_BUDGET: Authorized quantity {authorized_qty} < 1 share",
                requested_qty=requested_qty,
                authorized_qty=0,
                estimated_risk_dollars=0.0,
                risk_level=self.risk_level,
                rejection_code="INSUFFICIENT_RISK_BUDGET",
            )

        effective_qty = min(requested_qty, authorized_qty)
        estimated_risk = round(effective_qty * stop_dist, 2)
        return RiskCheckResult(
            approved=True,
            reason="Approved",
            requested_qty=requested_qty,
            authorized_qty=authorized_qty,
            estimated_risk_dollars=estimated_risk,
            risk_level=self.risk_level,
        )

    def trip_circuit_breaker(
        self,
        current_equity: float,
        drawdown_dollars: float,
        timestamp: datetime,
        reason: str = "MAX_DAILY_LOSS_EXCEEDED",
    ) -> Dict[str, Any]:
        """Execute emergency circuit breaker protocol."""
        self.status = BreakerStatus.HALTED_DAILY_LOSS
        self.risk_level = RiskLevel.HALTED
        self.breaker_triggered_at = timestamp
        self.breaker_trigger_equity = current_equity
        return {
            "action": "EMERGENCY_HALT",
            "purge_orders": True,
            "liquidate_positions": True,
            "timestamp": timestamp,
            "reason": reason,
            "drawdown_dollars": drawdown_dollars,
            "equity": current_equity,
        }

    def reset_daily_metrics(self, new_starting_equity: float) -> None:
        """Reset intraday metrics at market open (09:30 ET) for a new trading day."""
        self.config.starting_equity = new_starting_equity
        self.status = BreakerStatus.ARMED
        self.risk_level = RiskLevel.NORMAL
        self.daily_peak_equity = new_starting_equity
        self.current_drawdown_dollars = 0.0
        self.current_drawdown_pct = 0.0
        self.breaker_triggered_at = None
        self.breaker_trigger_equity = None
