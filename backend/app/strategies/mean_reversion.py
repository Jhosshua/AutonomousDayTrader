"""backend/app/strategies/mean_reversion.py
Statistical Mean Reversion / Exhaustion Fades Strategy.
Monitors 1-minute bars for multi-standard-deviation exhaustion (|Z| >= 2.5),
RSI-14 extremes, volume climax, and wick rejection targeting mean reversion to 20-SMA.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
import math
from typing import Any, Dict, List, Optional, Tuple, Union
import zoneinfo

from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.base import (
    Strategy,
    SignalEvent,
    StrategyStatus,
    calculate_atr,
    calculate_rsi,
    calculate_sma,
    calculate_zscore,
)

ET_TZ = zoneinfo.ZoneInfo("America/New_York")


def evaluate_mean_reversion_zscore(
    prices: List[float],
) -> Tuple[float, float, float]:
    """Compute 20-period moving average, std dev, and price Z-score.

    Returns:
        (mean, std_dev, z_score)
    """
    if len(prices) < 20:
        return 0.0, 0.0, 0.0
    window = prices[-20:]
    mean = sum(window) / 20.0
    variance = sum((p - mean) ** 2 for p in window) / 20.0
    std = math.sqrt(variance)
    if std <= 0.0001:
        return round(mean, 2), 0.0, 0.0
    z_score = (prices[-1] - mean) / std
    return round(mean, 2), round(std, 2), round(z_score, 2)


@dataclass
class SymbolMeanReversionState:
    """Intraday state for mean reversion analysis."""
    bars: List[BarEvent] = field(default_factory=list)
    last_signal_time: Optional[datetime] = None


class MeanReversionStrategy(Strategy):
    """Strategy 4: Statistical Mean Reversion / Exhaustion Fades."""

    def __init__(
        self,
        strategy_id: str = "mean_reversion",
        name: str = "Statistical Mean Reversion / Exhaustion Fades",
        period: int = 20,
        z_threshold: float = 2.50,
        rsi_period: int = 14,
        rsi_overbought: float = 75.0,
        rsi_oversold: float = 25.0,
        volume_climax_multiplier: float = 3.0,
        min_wick_ratio: float = 0.50,
    ):
        super().__init__(strategy_id=strategy_id, name=name)
        self.period: int = period
        self.z_threshold: float = z_threshold
        self.rsi_period: int = rsi_period
        self.rsi_overbought: float = rsi_overbought
        self.rsi_oversold: float = rsi_oversold
        self.volume_climax_multiplier: float = volume_climax_multiplier
        self.min_wick_ratio: float = min_wick_ratio
        self.symbol_states: Dict[str, SymbolMeanReversionState] = {}

    def _get_state(self, symbol: str) -> SymbolMeanReversionState:
        sym = symbol.upper()
        if sym not in self.symbol_states:
            self.symbol_states[sym] = SymbolMeanReversionState()
        return self.symbol_states[sym]

    def reset_daily_stats(self) -> None:
        super().reset_daily_stats()
        self.symbol_states.clear()

    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        if self.status != StrategyStatus.ACTIVE:
            return []

        state = self._get_state(bar.symbol)
        state.bars.append(bar)

        # Convert timestamp to ET
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_et = ts.astimezone(ET_TZ)
        t_time = ts_et.time()

        # Invariant: Strategy 4 is disabled during OPEN_VOLATILITY_FLUSH (09:30-10:00 ET)
        # to avoid stepping in front of opening institutional order flow
        open_flush_start = dtime(9, 30)
        open_flush_end = dtime(10, 0)
        eod_cutoff = dtime(15, 45)

        if t_time < open_flush_end or t_time >= eod_cutoff:
            return []

        if len(state.bars) < self.period:
            return []

        # Prevent repeated pyramiding on consecutive bars while the same
        # exhaustion condition remains extreme. A new signal is allowed after a
        # 15-minute cooldown, matching the strategy's intraday fade cadence.
        if state.last_signal_time is not None:
            elapsed = (bar.timestamp - state.last_signal_time).total_seconds()
            if elapsed < 15 * 60:
                return []

        closes = [b.close for b in state.bars]
        mean, std, z = evaluate_mean_reversion_zscore(closes)

        if abs(z) < self.z_threshold:
            return []

        # RSI check
        rsi = calculate_rsi(closes, self.rsi_period)

        # Volume Climax check
        volumes = [float(b.volume) for b in state.bars]
        sma_vol = calculate_sma(volumes[:-1], self.period)
        if sma_vol <= 0:
            sma_vol = 100000.0
        vol_ratio = bar.volume / sma_vol

        # Candle Wick rejection check
        candle_range = max(0.01, bar.high - bar.low)
        upper_wick = bar.high - max(bar.open, bar.close)
        lower_wick = min(bar.open, bar.close) - bar.low

        atr = calculate_atr(state.bars, 14)
        signals: List[SignalEvent] = []

        # 1. Short Exhaustion Fade (Overbought extreme: Z >= 2.50, RSI >= 75, Upper Wick >= 50%)
        if z >= self.z_threshold:
            has_climax = vol_ratio >= self.volume_climax_multiplier or bar.volume > 2.0 * sma_vol
            has_wick_rejection = (upper_wick / candle_range) >= self.min_wick_ratio
            is_rsi_overbought = rsi >= self.rsi_overbought or rsi >= 70.0

            if has_wick_rejection and has_climax and is_rsi_overbought:
                entry_price = bar.close
                target_price = round(mean, 4)
                stop_loss = round(bar.high + 0.50 * atr, 4)

                # Verify favorable reward-to-risk
                reward = entry_price - target_price
                risk = stop_loss - entry_price
                if reward > 0 and risk > 0 and (reward / risk) >= 1.2:
                    signals.append(
                        SignalEvent(
                            symbol=bar.symbol,
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit_1=target_price,
                            take_profit_2=round(mean - 0.5 * std, 4),
                            strategy_id=self.strategy_id,
                            confidence=0.80,
                            reason=f"MEAN_REVERSION_SHORT_FADE: Z={z:.2f}>={self.z_threshold}, RSI={rsi:.1f}, Wick={upper_wick/candle_range*100:.0f}%, Target={mean:.2f}",
                            timestamp=bar.timestamp,
                        )
                    )
                    state.last_signal_time = bar.timestamp

        # 2. Long Exhaustion Fade (Oversold extreme: Z <= -2.50, RSI <= 25, Lower Wick >= 50%)
        elif z <= -self.z_threshold:
            has_climax = vol_ratio >= self.volume_climax_multiplier or bar.volume > 2.0 * sma_vol
            has_wick_rejection = (lower_wick / candle_range) >= self.min_wick_ratio
            is_rsi_oversold = rsi <= self.rsi_oversold or rsi <= 30.0

            if has_wick_rejection and has_climax and is_rsi_oversold:
                entry_price = bar.close
                target_price = round(mean, 4)
                stop_loss = round(bar.low - 0.50 * atr, 4)

                reward = target_price - entry_price
                risk = entry_price - stop_loss
                if reward > 0 and risk > 0 and (reward / risk) >= 1.2:
                    signals.append(
                        SignalEvent(
                            symbol=bar.symbol,
                            side=OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit_1=target_price,
                            take_profit_2=round(mean + 0.5 * std, 4),
                            strategy_id=self.strategy_id,
                            confidence=0.80,
                            reason=f"MEAN_REVERSION_LONG_FADE: Z={z:.2f}<=-{self.z_threshold}, RSI={rsi:.1f}, Wick={lower_wick/candle_range*100:.0f}%, Target={mean:.2f}",
                            timestamp=bar.timestamp,
                        )
                    )
                    state.last_signal_time = bar.timestamp

        return signals
