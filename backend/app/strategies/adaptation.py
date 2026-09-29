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

from backend.app.models.events import (
    OrderSide, VIX_REGIME_BOUNDARIES, VIX_REGIME_SIZING_MULTIPLIERS, VIX_REGIME_STOP_MULTIPLIERS,
    VixPrint, VixRegime, classify_vix_regime,
)
from backend.app.strategies.base import SignalEvent, resolve_stop

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
    max_alloc_pct: float = 0.50,
    vix_multiplier: float = 1.0,
) -> int:
    """Calculate volatility-adjusted share size enforcing 1% risk budget and 50% max notional cap ($25,000 / 50% equity)."""
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
        market_filter: Optional[Any] = None,
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
        self.market_filter: Optional[Any] = market_filter
        self.last_update: datetime = datetime.now(timezone.utc)

    def on_vix_print(self, vprint: VixPrint) -> None:
        """Update VIX print and scale regime parameters."""
        self.current_vix = vprint.value
        regime, sizing, stop_m = get_vix_regime(vprint.value)
        self.current_vix_regime = regime
        self.current_sizing_multiplier = sizing
        self.current_stop_multiplier = stop_m
        self.last_update = vprint.received_at

    def apply_stale_vix_guard(self) -> bool:
        """Never let an unknown VIX justify sizing above neutral.

        Skipping the update on a stale print leaves whatever regime was accepted last
        still in force, so a LOW reading taken before the data went stale keeps sizing
        20% above base indefinitely. This clamps sizing to neutral and returns True when
        it changed something. It only ever tightens: an ELEVATED or CRISIS regime is
        already more defensive than neutral and is left alone.

        The stop multiplier is deliberately untouched. Sizing down is unambiguously
        risk-reducing; moving stops changes where trades exit, which is a separate
        decision that needs its own evidence.
        """
        if self.current_sizing_multiplier <= 1.00:
            return False
        self.current_vix_regime = VixRegime.NORMAL.value
        self.current_sizing_multiplier = 1.00
        return True

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

        # Gate rule 3: Ride the Trend v2 trades the morning only (09:45-11:30 inside the
        # strategy; the phase gate allows OPEN_VOLATILITY_FLUSH and TREND_CONTINUATION).
        # The 2026-09-27 replay: afternoon -0.075R vs morning -0.043R, 15:00 hour -0.099R.
        if strat == "vwap_pullback":
            return active_phase in (
                TimeOfDayPhase.OPEN_VOLATILITY_FLUSH.value,
                TimeOfDayPhase.TREND_CONTINUATION.value,
            )

        # Gate rule 4: Mean Reversion is disabled during morning open volatility flush
        if strat == "mean_reversion":
            if active_phase == TimeOfDayPhase.OPEN_VOLATILITY_FLUSH.value:
                return False
            return True

        # News Momentum is allowed during standard execution hours
        return True

    def calculate_adapted_stop(self, signal: SignalEvent) -> float:
        """Scale the stop for VIX, leaving wide stops for the risk engine to reject.

        The 0.4% floor keeps a tight stop valid. A stop wider than 4% must
        remain wide: capping it would move protection inside the setup's
        structural level and turn a rejected setup into a live order.
        """
        if getattr(signal, "stop_is_final", False):
            # Ride the Trend v2 computes its stop once (1.5 ATR x VIX multiplier,
            # structure, floor). Scaling it here would apply the VIX multiplier twice.
            return signal.stop_loss
        raw_dist = abs(signal.entry_price - signal.stop_loss)
        adapted_dist = raw_dist * self.current_stop_multiplier

        # Low VIX must not pull an already-wide structural stop below the risk
        # ceiling. The downstream risk engine owns the 4% maximum.
        if raw_dist > signal.entry_price * (0.0400 + 1e-6):
            adapted_dist = max(raw_dist, adapted_dist)

        is_buy = signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY"
        stop, _ = resolve_stop(signal.entry_price, adapted_dist, is_buy)
        return stop

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
        stop_loss_price: Optional[float] = None,
    ) -> Tuple[bool, str, int]:
        """Validate if a strategy signal passes adaptation gates and calculate sizing.

        Returns:
            (approved: bool, reason: str, authorized_qty: int)
        """
        # 0. Market Index Trend Filter Check
        if self.market_filter is not None:
            catalyst_sentiment = getattr(signal, "catalyst_sentiment", None)
            volume_surge = getattr(signal, "volume_surge", None)
            rvol = getattr(signal, "rvol", None)
            permitted, reason = self.market_filter.is_signal_permitted(
                strategy_id=signal.strategy_id,
                side=signal.side,
                symbol=signal.symbol,
                asof=signal.timestamp,
                catalyst_sentiment=catalyst_sentiment,
                volume_surge=volume_surge,
                rvol=rvol,
            )
            if not permitted:
                return False, f"ADAPTATION_MARKET_FILTER_DENIED: {reason}", 0

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
            stop_loss_price=signal.stop_loss if stop_loss_price is None else stop_loss_price,
        )

        if shares <= 0:
            return False, "SIZING_GATE_DENIED: Calculated position size is 0 shares", 0

        return True, "APPROVED_BY_ADAPTATION_ENGINE", shares

    @property
    def time_multiplier(self) -> float:
        """The time-of-day size factor calculate_adapted_size applies (display reads this)."""
        return 0.50 if self.current_time_phase == TimeOfDayPhase.MIDDAY_CHOP.value else 1.0

    @staticmethod
    def vix_tiers() -> List[Dict[str, Any]]:
        """The fear-gauge levels straight from the events.py tuples (None = open end)."""
        names = (VixRegime.LOW, VixRegime.NORMAL, VixRegime.ELEVATED, VixRegime.CRISIS)
        bounds = (None,) + tuple(VIX_REGIME_BOUNDARIES) + (None,)
        return [{"name": names[i].value, "lower": bounds[i], "upper": bounds[i + 1],
                 "sizing": VIX_REGIME_SIZING_MULTIPLIERS[i], "stop": VIX_REGIME_STOP_MULTIPLIERS[i]}
                for i in range(4)]

    @staticmethod
    def _midday_window() -> Optional[Dict[str, str]]:
        from backend.app.core.trading_windows import PHASES
        for start, end, phase in PHASES:
            if phase == TimeOfDayPhase.MIDDAY_CHOP.value:
                return {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
        return None

    def get_market_context(
        self,
        vix_stale: Optional[bool] = None,
        vix_age_seconds: Optional[float] = None,
        adaptive_strategies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get market context dictionary for UI WebSocket streaming.

        Display only. Every key is ALWAYS present (None when unknown) because the UI merges
        streamed frames, so an omitted key would keep a stale value on screen. The stale flag,
        VIX age and adaptive strategy ids live outside the engine and are passed in by main.py.
        """
        vix = self.current_vix
        ctx: Dict[str, Any] = {
            "vix": vix if isinstance(vix, (int, float)) and math.isfinite(vix) else None,
            "vix_regime": self.current_vix_regime,
            "time_phase": self.current_time_phase,
            "market_status": self.market_status,
            "sizing_multiplier": self.current_sizing_multiplier,
            "stop_multiplier": self.current_stop_multiplier,
            "vix_stale": vix_stale,
            "vix_age_seconds": vix_age_seconds,
            "time_multiplier": self.time_multiplier,
            "market_trend": None,
            "market_trend_reason": None,
            "max_concurrent_positions": self.max_concurrent_positions,
            "notional_cap_pct": round(self.max_alloc_pct * 100.0, 4),
            "base_risk_pct": round(self.base_risk_pct * 100.0, 4),
            "midday": None,
            "vix_tiers": None,
            "adaptive_strategies": list(adaptive_strategies) if adaptive_strategies is not None else [],
        }
        if self.market_filter is not None:
            try:
                trend, reason = self.market_filter.get_current_trend()
                ctx["market_trend"] = trend.value
                ctx["market_trend_reason"] = str(reason).split(":")[0].strip()[:60] or None
            except Exception:
                pass
        try:
            ctx["midday"] = self._midday_window()
            ctx["vix_tiers"] = self.vix_tiers()
        except Exception:
            pass
        return ctx

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
