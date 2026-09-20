"""backend/app/strategies/vwap_pullback.py
VWAP Trend Pullback & Continuation Strategy.
Anchored VWAP from 09:30 ET with multi-band standard deviations, EMA trend filter,
and high-volume bounce confirmation.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import zoneinfo

from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.base import (
    Strategy,
    SignalEvent,
    StrategyStatus,
    calculate_anchored_vwap,
    calculate_ema,
    calculate_sma,
    calculate_atr,
)

ET_TZ = zoneinfo.ZoneInfo("America/New_York")


@dataclass
class SymbolVWAPState:
    """State tracking for a single symbol's VWAP."""
    session_bars: List[BarEvent] = field(default_factory=list)
    recent_bars: List[BarEvent] = field(default_factory=list)
    in_pullback_zone: bool = False
    pullback_low_vol_confirmed: bool = False
    last_signal_timestamp: Optional[datetime] = None


class VWAPPullbackStrategy(Strategy):
    """Strategy 2: VWAP Trend Pullback & Continuation."""

    def __init__(
        self,
        strategy_id: str = "vwap_pullback",
        name: str = "VWAP Trend Pullback & Continuation",
        ema_fast_period: int = 20,
        ema_slow_period: int = 50,
        cooldown_bars: int = 15,
    ):
        super().__init__(strategy_id=strategy_id, name=name)
        self.ema_fast_period: int = ema_fast_period
        self.ema_slow_period: int = ema_slow_period
        self.cooldown_bars: int = cooldown_bars
        self.symbol_states: Dict[str, SymbolVWAPState] = {}

    def _get_state(self, symbol: str) -> SymbolVWAPState:
        sym = symbol.upper()
        if sym not in self.symbol_states:
            self.symbol_states[sym] = SymbolVWAPState()
        return self.symbol_states[sym]

    def reset_daily_stats(self) -> None:
        super().reset_daily_stats()
        self.symbol_states.clear()

    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        if self.status != StrategyStatus.ACTIVE:
            return []

        state = self._get_state(bar.symbol)
        state.recent_bars.append(bar)

        # Convert timestamp to ET
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_et = ts.astimezone(ET_TZ)
        t_time = ts_et.time()

        # Anchored starting from 09:30 ET
        open_bell = dtime(9, 30)
        eod_cutoff = dtime(15, 45)

        if t_time < open_bell or t_time >= eod_cutoff:
            return []

        state.session_bars.append(bar)
        if len(state.session_bars) < 10:
            # Need minimum sample for initial VWAP and SMA
            return []

        # Calculate Anchored VWAP and standard deviation
        vwap, std = calculate_anchored_vwap(state.session_bars)
        if std <= 0.001:
            std = calculate_atr(state.session_bars, 14)

        # EMA Trend Filter
        closes = [b.close for b in state.recent_bars]
        if len(closes) >= self.ema_slow_period:
            ema_fast = calculate_ema(closes, self.ema_fast_period)
            ema_slow = calculate_ema(closes, self.ema_slow_period)
            is_bullish_trend = ema_fast > ema_slow
            is_bearish_trend = ema_fast < ema_slow
        elif len(closes) >= self.ema_fast_period:
            ema_fast = calculate_ema(closes, 10)
            ema_slow = calculate_ema(closes, self.ema_fast_period)
            is_bullish_trend = ema_fast > ema_slow
            is_bearish_trend = ema_fast < ema_slow
        else:
            # Fallback: trend determined by price vs VWAP
            is_bullish_trend = bar.close > vwap
            is_bearish_trend = bar.close < vwap

        # Volume SMAs
        volumes = [float(b.volume) for b in state.recent_bars]
        sma10_vol = calculate_sma(volumes, 10)

        # Test of VWAP pullback zone
        # Long zone: [VWAP - 0.2*std, VWAP + 0.3*std]
        long_zone_low = vwap - (0.2 * std)
        long_zone_high = vwap + (0.3 * std)
        short_zone_low = vwap - (0.3 * std)
        short_zone_high = vwap + (0.2 * std)

        candle_range = max(0.01, bar.high - bar.low)
        lower_wick = min(bar.open, bar.close) - bar.low
        upper_wick = bar.high - max(bar.open, bar.close)

        signals: List[SignalEvent] = []

        # 1. Bullish Pullback & Bounce
        if is_bullish_trend:
            # Check if current bar tested the zone or prior bar did
            tested_zone = (long_zone_low <= bar.low <= long_zone_high) or (long_zone_low <= bar.close <= long_zone_high)
            is_green_bounce = bar.close > bar.open and bar.close >= vwap
            has_hammer_wick = lower_wick >= 0.30 * candle_range
            volume_confirmed = bar.volume >= 1.20 * sma10_vol

            if (tested_zone or state.in_pullback_zone) and is_green_bounce and (has_hammer_wick or volume_confirmed):
                entry_price = bar.close
                stop_loss = round(vwap - (0.50 * std), 4)
                if entry_price - stop_loss < 0.05:
                    stop_loss = round(entry_price - max(0.10, std * 0.8), 4)
                risk = entry_price - stop_loss
                tp1 = round(vwap + (1.0 * std), 4)
                if tp1 <= entry_price:
                    tp1 = round(entry_price + 1.5 * risk, 4)
                tp2 = round(vwap + (2.0 * std), 4)
                if tp2 <= tp1:
                    tp2 = round(entry_price + 2.5 * risk, 4)

                signals.append(
                    SignalEvent(
                        symbol=bar.symbol,
                        side=OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit_1=tp1,
                        take_profit_2=tp2,
                        strategy_id=self.strategy_id,
                        confidence=0.75,
                        reason=f"VWAP_PULLBACK_LONG: Test of VWAP {vwap:.2f}, bounce to {entry_price:.2f}, VolSurge={bar.volume/max(1.0, sma10_vol):.2f}x",
                        timestamp=bar.timestamp,
                    )
                )
                state.in_pullback_zone = False
                state.last_signal_timestamp = bar.timestamp
            elif tested_zone:
                state.in_pullback_zone = True

        # 2. Bearish Pullback & Rejection
        elif is_bearish_trend:
            tested_zone = (short_zone_low <= bar.high <= short_zone_high) or (short_zone_low <= bar.close <= short_zone_high)
            is_red_rejection = bar.close < bar.open and bar.close <= vwap
            has_inv_hammer_wick = upper_wick >= 0.30 * candle_range
            volume_confirmed = bar.volume >= 1.20 * sma10_vol

            if (tested_zone or state.in_pullback_zone) and is_red_rejection and (has_inv_hammer_wick or volume_confirmed):
                entry_price = bar.close
                stop_loss = round(vwap + (0.50 * std), 4)
                if stop_loss - entry_price < 0.05:
                    stop_loss = round(entry_price + max(0.10, std * 0.8), 4)
                risk = stop_loss - entry_price
                tp1 = round(vwap - (1.0 * std), 4)
                if tp1 >= entry_price:
                    tp1 = round(entry_price - 1.5 * risk, 4)
                tp2 = round(vwap - (2.0 * std), 4)
                if tp2 >= tp1:
                    tp2 = round(entry_price - 2.5 * risk, 4)

                signals.append(
                    SignalEvent(
                        symbol=bar.symbol,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit_1=tp1,
                        take_profit_2=tp2,
                        strategy_id=self.strategy_id,
                        confidence=0.75,
                        reason=f"VWAP_PULLBACK_SHORT: Test of VWAP {vwap:.2f}, rejection to {entry_price:.2f}, VolSurge={bar.volume/max(1.0, sma10_vol):.2f}x",
                        timestamp=bar.timestamp,
                    )
                )
                state.in_pullback_zone = False
                state.last_signal_timestamp = bar.timestamp
            elif tested_zone:
                state.in_pullback_zone = True

        return signals
