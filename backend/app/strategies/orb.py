"""backend/app/strategies/orb.py
Opening Range Breakout (ORB) Strategy.
Directional momentum capitalizing on institutional opening imbalances (5-min / 15-min).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import zoneinfo

from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.base import attach_features, Strategy, SignalEvent, StrategyStatus, calculate_atr, resolve_stop

ET_TZ = zoneinfo.ZoneInfo("America/New_York")


def evaluate_orb_signal(
    bars_5m: List[Union[BarEvent, Dict[str, Any]]],
    current_bar: Union[BarEvent, Dict[str, Any]],
    rvol: float,
    min_clv: float = 0.65,
    max_clv_sell: float = 0.35,
    min_rvol: float = 1.80,
) -> Optional[str]:
    """Opening Range Breakout signal evaluator with Close Location Value (CLV).

    Args:
        bars_5m: Sequence of bars forming the opening range.
        current_bar: Current breakout candidate bar.
        rvol: Relative volume on breakout.
        min_clv: Minimum Close Location Value for BUY (default 0.65).
        max_clv_sell: Maximum Close Location Value for SELL (default 0.35).

    Returns:
        "BUY", "SELL", or None.
    """
    if len(bars_5m) < 1:
        return None

    highs = [b.high if isinstance(b, BarEvent) else float(b.get("h", b.get("high", 0.0))) for b in bars_5m]
    lows = [b.low if isinstance(b, BarEvent) else float(b.get("l", b.get("low", 0.0))) for b in bars_5m]
    range_high = max(highs)
    range_low = min(lows)

    # Use the strategy's configured threshold, including for non-default instances.
    if rvol < min_rvol:
        return None

    close_p = current_bar.close if isinstance(current_bar, BarEvent) else float(current_bar.get("c", current_bar.get("close", 0.0)))
    high_p = current_bar.high if isinstance(current_bar, BarEvent) else float(current_bar.get("h", current_bar.get("high", 0.0)))
    low_p = current_bar.low if isinstance(current_bar, BarEvent) else float(current_bar.get("l", current_bar.get("low", 0.0)))

    candle_range = max(0.0001, high_p - low_p)
    clv = round((close_p - low_p) / candle_range, 4)

    if close_p > range_high:
        if clv >= (min_clv - 1e-5):
            return "BUY"
    elif close_p < range_low:
        if clv <= (max_clv_sell + 1e-5):
            return "SELL"
    return None


@dataclass
class SymbolORBState:
    """Intraday state tracking for a single symbol's opening range."""
    opening_bars: List[BarEvent] = field(default_factory=list)
    all_bars: List[BarEvent] = field(default_factory=list)
    range_established: bool = False
    range_high: float = 0.0
    range_low: float = 0.0
    range_midpoint: float = 0.0
    baseline_volume: float = 100000.0
    breakout_fired: bool = False


class OpeningRangeBreakoutStrategy(Strategy):
    """Strategy 1: Opening Range Breakout (5m / 15m)."""

    def __init__(
        self,
        strategy_id: str = "orb",
        name: str = "Opening Range Breakout",
        range_minutes: int = 5,
        min_rvol: float = 1.80,
        target_1_r: float = 0.8,
        target_2_r: float = 1.8,
        max_bar_range_atr: float = 2.2,
        max_extension_atr: float = 1.0,
        min_clv: float = 0.65,
        max_clv_sell: float = 0.35,
    ):
        super().__init__(strategy_id=strategy_id, name=name)
        self.range_minutes: int = range_minutes
        self.min_rvol: float = min_rvol
        self.target_1_r: float = target_1_r
        self.target_2_r: float = target_2_r
        self.max_bar_range_atr: float = max_bar_range_atr
        self.max_extension_atr: float = max_extension_atr
        self.min_clv: float = min_clv
        self.max_clv_sell: float = max_clv_sell
        self.symbol_states: Dict[str, SymbolORBState] = {}

    def _get_state(self, symbol: str) -> SymbolORBState:
        sym = symbol.upper()
        if sym not in self.symbol_states:
            self.symbol_states[sym] = SymbolORBState()
        return self.symbol_states[sym]

    def reset_daily_stats(self) -> None:
        super().reset_daily_stats()
        self.symbol_states.clear()

    def set_baseline_volume(self, symbol: str, volume: float) -> None:
        state = self._get_state(symbol)
        state.baseline_volume = max(1000.0, volume)

    def notify_signal_rejected(self, symbol: str) -> None:
        """Reset breakout_fired if downstream signal admission or risk engine rejects the order."""
        sym = symbol.upper()
        if sym in self.symbol_states:
            self.symbol_states[sym].breakout_fired = False

    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        if self.status != StrategyStatus.ACTIVE:
            return []

        # Convert timestamp to ET
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_et = ts.astimezone(ET_TZ)
        t_time = ts_et.time()

        open_bell = dtime(9, 30)
        cutoff_time = dtime(11, 30)

        if t_time < open_bell:
            # Pre-market bar, do not include in opening range or regular-session baselines
            return []

        state = self._get_state(bar.symbol)
        state.all_bars.append(bar)
        # Cap buffer: RVOL baseline uses the last 20 bars, ATR fallback 14
        if len(state.all_bars) > 60:
            del state.all_bars[:-60]

        # Ingest bars during opening range (09:30 to 09:30 + range_minutes)
        # 5m range: 09:30:00 to 09:34:59 (bars timestamped 09:30 to 09:34)
        end_minute = 30 + self.range_minutes
        end_hour = 9 + (end_minute // 60)
        end_minute = end_minute % 60
        range_end_time = dtime(end_hour, end_minute)

        if open_bell <= t_time < range_end_time:
            state.opening_bars.append(bar)
            return []

        # At or after range end: establish range if not already done
        if not state.range_established:
            if not state.opening_bars:
                # Late arriving symbol: opening range cannot be spuriously seeded outside 09:30-09:45 ET
                if t_time > dtime(9, 45):
                    state.range_established = True
                    state.breakout_fired = True
                    return []
                # Missed the open (within 09:30-09:45): seed the range from this bar but skip signal
                # evaluation, since a range containing the current bar can never
                # be broken out of on that same bar.
                state.opening_bars.append(bar)
                state.range_high = bar.high
                state.range_low = bar.low
                state.range_midpoint = round((bar.high + bar.low) / 2.0, 4)
                state.range_established = True
                return []
            state.range_high = max(b.high for b in state.opening_bars)
            state.range_low = min(b.low for b in state.opening_bars)
            state.range_midpoint = round((state.range_high + state.range_low) / 2.0, 4)
            state.range_established = True

        # Check if breakout window has closed (after 11:30 ET) or already fired
        if t_time >= cutoff_time or state.breakout_fired:
            return []

        # Calculate RVOL baseline excluding the current breakout bar
        prior_bars = state.all_bars[:-1][-20:]
        if prior_bars:
            avg_vol = sum(b.volume for b in prior_bars) / len(prior_bars)
        elif state.baseline_volume > 0:
            avg_vol = state.baseline_volume
        else:
            avg_vol = max(1.0, float(bar.volume))
        rvol = round(bar.volume / max(1.0, avg_vol), 2)

        # Evaluate breakout signal
        sig_type = evaluate_orb_signal(
            state.opening_bars,
            bar,
            rvol=rvol,
            min_clv=self.min_clv,
            max_clv_sell=self.max_clv_sell,
            min_rvol=self.min_rvol,
        )
        if not sig_type:
            return []

        # Bar Range Cap & Extension Cap (baseline on prior bars to prevent range leakage)
        atr_bars = state.all_bars[:-1] if len(state.all_bars) > 1 else state.all_bars
        atr = calculate_atr(atr_bars, period=14)
        if atr > 0.001:
            candle_range = bar.high - bar.low
            if candle_range > (self.max_bar_range_atr * atr):
                return []
            if sig_type == "BUY" and (bar.close - state.range_high) > (self.max_extension_atr * atr):
                return []
            elif sig_type == "SELL" and (state.range_low - bar.close) > (self.max_extension_atr * atr):
                return []

        entry_price = bar.close
        stop_loss = state.range_midpoint
        raw_dist = abs(entry_price - stop_loss)
        if raw_dist < 0.05:
            # Fallback to ATR-based risk if range is ultra-tight
            raw_dist = max(0.10, atr)

        # Widen a too-tight stop to the 0.4% floor. An oversized opening range is
        # left alone and rejected by the risk engine rather than having its stop
        # pulled inside the range.
        stop_loss, risk = resolve_stop(entry_price, raw_dist, sig_type == "BUY")

        if sig_type == "BUY":
            tp1 = round(entry_price + self.target_1_r * risk, 4)
            tp2 = round(entry_price + self.target_2_r * risk, 4)
            side = OrderSide.BUY
            reason = f"ORB_BREAKOUT_LONG: Close {entry_price:.2f} > RangeHigh {state.range_high:.2f}, RVOL={rvol}x"
        else:
            tp1 = round(entry_price - self.target_1_r * risk, 4)
            tp2 = round(entry_price - self.target_2_r * risk, 4)
            side = OrderSide.SELL
            reason = f"ORB_BREAKDOWN_SHORT: Close {entry_price:.2f} < RangeLow {state.range_low:.2f}, RVOL={rvol}x"

        state.breakout_fired = True
        sig = SignalEvent(
            symbol=bar.symbol,
            side=side,
            order_type=OrderType.MARKET,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            strategy_id=self.strategy_id,
            confidence=min(1.0, 0.60 + 0.10 * rvol),
            reason=reason,
            timestamp=bar.timestamp,
        )
        sig.rvol = rvol
        attach_features(sig, lambda: {
            "range_high": state.range_high,
            "range_low": state.range_low,
            "range_midpoint": state.range_midpoint,
            "range_bars": len(state.opening_bars),
            "rvol": rvol,
            "baseline_volume": round(avg_vol, 2),
            "bar_volume": bar.volume,
            "clv": round((bar.close - bar.low) / (bar.high - bar.low), 4) if bar.high > bar.low else None,
            "atr": round(atr, 4),
            "bar_range_atr": round((bar.high - bar.low) / atr, 4) if atr > 0.001 else None,
            "extension_atr": round(((bar.close - state.range_high) if sig_type == "BUY" else (state.range_low - bar.close)) / atr, 4) if atr > 0.001 else None,
            "structural_stop": state.range_midpoint,
            "raw_stop_distance": round(raw_dist, 4),
            "floored_stop_distance": round(risk, 4),
            "stop_floor_applied": risk > raw_dist + 1e-9,
        })
        return [sig]
