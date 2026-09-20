"""backend/app/strategies/adaptation.py
Dynamic Self-Adaptation Engine.
Integrates real-time VIX volatility regimes and Intraday Time-of-Day execution phases
to dynamically scale position sizing, widen/tighten stops, arbitrate signal concurrency,
and gate strategy activation.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Set, Tuple
import zoneinfo

from backend.app.models.events import OrderSide, VixPrint, VixRegime, classify_vix_regime
from backend.app.strategies.base import SignalEvent

ET_TZ = zoneinfo.ZoneInfo("America/New_York")


class TimeOfDayPhase(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    OPEN_VOLATILITY_FLUSH = "OPEN_VOLATILITY_FLUSH"
    TREND_CONTINUATION = "TREND_CONTINUATION"
    MIDDAY_CHOP = "MIDDAY_CHOP"
    AFTERNOON_PUSH = "AFTERNOON_PUSH"
    POWER_HOUR = "POWER_HOUR"
    EOD_FLATTEN = "EOD_FLATTEN"
    POST_MARKET = "POST_MARKET"


def get_vix_regime(vix: float) -> Tuple[str, float, float]:
    """Map VIX spot to regime string, position sizing multiplier, and stop multiplier.

    Returns:
        (regime_str, sizing_multiplier, stop_multiplier)
    """
    regime, sizing, stop = classify_vix_regime(vix)
    return regime.value, sizing, stop


def get_time_of_day_phase(t_et: dtime) -> str:
    """Classify intraday market execution phase according to Eastern Time clock."""
    if t_et < dtime(9, 30):
        return "PRE_MARKET"
    elif t_et < dtime(10, 0):
        return "OPEN_VOLATILITY_FLUSH"
    elif t_et < dtime(11, 30):
        return "TREND_CONTINUATION"
    elif t_et < dtime(14, 0):
        return "MIDDAY_CHOP"
    elif t_et < dtime(15, 0):
        return "AFTERNOON_PUSH"
    elif t_et < dtime(15, 45):
        return "POWER_HOUR"
    elif t_et < dtime(16, 0):
        return "EOD_FLATTEN"
    return "POST_MARKET"


def calculate_position_size(
    equity: float,
    entry_price: float,
    stop_loss_price: float,
    risk_pct: float = 0.01,
    max_alloc_pct: float = 0.25,
    vix_multiplier: float = 1.0,
) -> int:
    """Calculate volatility-adjusted share size enforcing 1% risk budget and 25% max notional cap."""
    stop_distance = abs(entry_price - stop_loss_price)
    if stop_distance <= 0.001 or entry_price <= 0:
        return 0

    risk_dollars = equity * risk_pct * vix_multiplier
    shares_by_risk = math.floor(risk_dollars / stop_distance)

    max_capital = equity * max_alloc_pct
    shares_by_capital = math.floor(max_capital / entry_price)

    return max(0, min(shares_by_risk, shares_by_capital))


@dataclass
class AdaptationState:
    """Snapshot of current dynamic adaptation state."""
    vix: float = 20.0
    vix_regime: str = "NORMAL"
    sizing_multiplier: float = 1.00
    stop_multiplier: float = 1.00
    time_phase: str = "TREND_CONTINUATION"
    market_status: str = "OPEN"
    max_concurrent_positions: int = 3
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class DynamicAdaptationEngine:
    """Self-Adaptation Engine modulating sizing, stop widths, and strategy permissions."""

    # Priority hierarchy for signal collision resolution: News > ORB > VWAP > Mean Reversion
    STRATEGY_PRIORITY: Dict[str, int] = {
        "news_momentum": 40,
        "orb": 30,
        "vwap_pullback": 20,
        "mean_reversion": 10,
    }

    def __init__(
        self,
        default_vix: float = 20.0,
        max_concurrent_positions: int = 3,
        base_risk_pct: float = 0.01,
        max_alloc_pct: float = 0.25,
    ):
        self.current_vix: float = default_vix
        regime, sizing, stop_m = get_vix_regime(default_vix)
        self.current_vix_regime: str = regime
        self.current_sizing_multiplier: float = sizing
        self.current_stop_multiplier: float = stop_m
        self.current_time_phase: str = "OPEN_VOLATILITY_FLUSH"
        self.max_concurrent_positions: int = max_concurrent_positions
        self.base_risk_pct: float = base_risk_pct
        self.max_alloc_pct: float = max_alloc_pct
        self.last_update: datetime = datetime.now(timezone.utc)

    def on_vix_print(self, vprint: VixPrint) -> None:
        """Update VIX print and scale regime parameters."""
        self.current_vix = vprint.value
        regime, sizing, stop_m = get_vix_regime(vprint.value)
        self.current_vix_regime = regime
        self.current_sizing_multiplier = sizing
        self.current_stop_multiplier = stop_m
        self.last_update = vprint.received_at

    def update_clock(self, dt: datetime) -> str:
        """Update session time and return current TimeOfDayPhase string."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(ET_TZ)
        phase = get_time_of_day_phase(dt_et.time())
        self.current_time_phase = phase
        self.last_update = dt
        return phase

    @property
    def market_status(self) -> str:
        if self.current_time_phase in (
            TimeOfDayPhase.OPEN_VOLATILITY_FLUSH.value,
            TimeOfDayPhase.TREND_CONTINUATION.value,
            TimeOfDayPhase.MIDDAY_CHOP.value,
            TimeOfDayPhase.AFTERNOON_PUSH.value,
            TimeOfDayPhase.POWER_HOUR.value,
        ):
            return "OPEN"
        elif self.current_time_phase == TimeOfDayPhase.EOD_FLATTEN.value:
            return "FLATTENING"
        return "CLOSED"

    def is_strategy_permitted(self, strategy_id: str, phase: Optional[str] = None) -> bool:
        """Determine whether a strategy is permitted to initiate new entries in current phase."""
        active_phase = phase or self.current_time_phase
        strat = strategy_id.lower()

        # Gate rule 1: Pre-market, EOD Flatten, Post-market block ALL entries
        if active_phase in (
            TimeOfDayPhase.PRE_MARKET.value,
            TimeOfDayPhase.EOD_FLATTEN.value,
            TimeOfDayPhase.POST_MARKET.value,
        ):
            return False

        # Gate rule 2: ORB restricted to OPEN_VOLATILITY_FLUSH and TREND_CONTINUATION
        # (blocked in MIDDAY_CHOP, AFTERNOON_PUSH, POWER_HOUR)
        if strat == "orb":
            return active_phase in (
                TimeOfDayPhase.OPEN_VOLATILITY_FLUSH.value,
                TimeOfDayPhase.TREND_CONTINUATION.value,
            )

        # Gate rule 3: VWAP Pullback (trend continuation) blocked during MIDDAY_CHOP
        if strat == "vwap_pullback":
            if active_phase == TimeOfDayPhase.MIDDAY_CHOP.value:
                return False
            return True

        # Gate rule 4: Mean Reversion is disabled during morning open volatility flush
        if strat == "mean_reversion":
            if active_phase == TimeOfDayPhase.OPEN_VOLATILITY_FLUSH.value:
                return False
            return True

        # News Momentum is allowed during standard execution hours
        return True

    def calculate_adapted_stop(self, signal: SignalEvent) -> float:
        """Calculate volatility-adapted stop-loss price scaled by current_stop_multiplier."""
        raw_dist = abs(signal.entry_price - signal.stop_loss)
        adapted_dist = raw_dist * self.current_stop_multiplier
        is_buy = signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY"
        if is_buy:
            return round(signal.entry_price - adapted_dist, 4)
        else:
            return round(signal.entry_price + adapted_dist, 4)

    def calculate_adapted_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
    ) -> int:
        """Compute position size combining VIX regime and Time-of-Day multipliers."""
        time_multiplier = 0.50 if self.current_time_phase == TimeOfDayPhase.MIDDAY_CHOP.value else 1.0
        combined_vix_multiplier = self.current_sizing_multiplier * time_multiplier

        return calculate_position_size(
            equity=equity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            risk_pct=self.base_risk_pct,
            max_alloc_pct=self.max_alloc_pct,
            vix_multiplier=combined_vix_multiplier,
        )

    def arbitrate_signals(self, signals: List[SignalEvent]) -> List[SignalEvent]:
        """Sort colliding signals by priority hierarchy and deduplicate per symbol."""
        sorted_sigs = sorted(
            signals,
            key=lambda s: (self.STRATEGY_PRIORITY.get(s.strategy_id.lower(), 0), s.confidence),
            reverse=True,
        )
        seen_symbols: Set[str] = set()
        deduped: List[SignalEvent] = []
        for s in sorted_sigs:
            sym = s.symbol.upper()
            if sym not in seen_symbols:
                seen_symbols.add(sym)
                deduped.append(s)
        return deduped

    def evaluate_signal_admission(
        self,
        signal: SignalEvent,
        equity: float,
        current_positions_count: int,
        is_symbol_active: bool,
    ) -> Tuple[bool, str, int]:
        """Validate if a strategy signal passes adaptation gates and calculate sizing.

        Returns:
            (approved: bool, reason: str, authorized_qty: int)
        """
        # 1. Phase permission check
        if not self.is_strategy_permitted(signal.strategy_id, self.current_time_phase):
            return False, f"PHASE_GATE_DENIED: {signal.strategy_id} not permitted during {self.current_time_phase}", 0

        # 2. Concurrency cap check
        if not is_symbol_active and current_positions_count >= self.max_concurrent_positions:
            return False, f"CONCURRENCY_GATE_DENIED: Max concurrent positions ({self.max_concurrent_positions}) reached", 0

        # 3. Calculate adapted share quantity
        shares = self.calculate_adapted_size(
            equity=equity,
            entry_price=signal.entry_price,
            stop_loss_price=signal.stop_loss,
        )

        if shares <= 0:
            return False, "SIZING_GATE_DENIED: Calculated position size is 0 shares", 0

        return True, "APPROVED_BY_ADAPTATION_ENGINE", shares

    def get_market_context(self) -> Dict[str, Any]:
        """Get market context dictionary for UI WebSocket streaming."""
        return {
            "vix": self.current_vix,
            "vix_regime": self.current_vix_regime,
            "time_phase": self.current_time_phase,
            "market_status": self.market_status,
            "sizing_multiplier": self.current_sizing_multiplier,
            "stop_multiplier": self.current_stop_multiplier,
        }

    def get_snapshot(self) -> AdaptationState:
        """Return read-only state snapshot."""
        return AdaptationState(
            vix=self.current_vix,
            vix_regime=self.current_vix_regime,
            sizing_multiplier=self.current_sizing_multiplier,
            stop_multiplier=self.current_stop_multiplier,
            time_phase=self.current_time_phase,
            market_status=self.market_status,
            max_concurrent_positions=self.max_concurrent_positions,
            updated_at=self.last_update,
        )
