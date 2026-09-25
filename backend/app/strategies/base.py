"""backend/app/strategies/base.py
Base Strategy Architecture, SignalEvent dataclass, built-in technical indicators,
and performance tracking.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.app.models.events import BarEvent, QuoteEvent, NewsEvent, VixPrint, OrderSide, OrderType


class StrategyStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COOLDOWN = "COOLDOWN"


@dataclass
class SignalEvent:
    """Trading signal emitted by a strategy."""
    symbol: str
    side: Union[OrderSide, str]
    order_type: Union[OrderType, str]
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    strategy_id: str
    confidence: float
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    target_qty: Optional[int] = None
    rvol: Optional[float] = None
    volume_surge: Optional[float] = None
    catalyst_sentiment: Optional[float] = None
    target_1_is_r_fallback: bool = False
    target_2_is_r_fallback: bool = False

    def __post_init__(self):
        if isinstance(self.side, str):
            self.side = OrderSide(self.side.upper())
        if isinstance(self.order_type, str):
            self.order_type = OrderType(self.order_type.upper())


# ============================================================================
# Built-in Technical Indicators
# ============================================================================

def _extract_ohlcv(b: Union[BarEvent, Dict[str, Any]]) -> Tuple[float, float, float, float, float]:
    """Helper to extract (open, high, low, close, volume) from BarEvent or Dict."""
    if isinstance(b, BarEvent):
        return b.open, b.high, b.low, b.close, float(b.volume)
    o = float(b.get("o", b.get("open", 0.0)))
    h = float(b.get("h", b.get("high", 0.0)))
    l = float(b.get("l", b.get("low", 0.0)))
    c = float(b.get("c", b.get("close", 0.0)))
    v = float(b.get("v", b.get("volume", 0.0)))
    return o, h, l, c, v


def calculate_anchored_vwap(bars: List[Union[BarEvent, Dict[str, Any]]]) -> Tuple[float, float]:
    """Calculate anchored VWAP and standard deviation from a sequence of bars.

    Returns:
        (vwap, std_dev)
    """
    total_pv = 0.0
    total_vol = 0.0
    for b in bars:
        _, h, l, c, v = _extract_ohlcv(b)
        typical_p = (h + l + c) / 3.0
        total_pv += typical_p * v
        total_vol += v

    if total_vol <= 0:
        return 0.0, 0.0

    vwap = total_pv / total_vol
    variance = sum(
        _extract_ohlcv(b)[4] * (((_extract_ohlcv(b)[1] + _extract_ohlcv(b)[2] + _extract_ohlcv(b)[3]) / 3.0 - vwap) ** 2)
        for b in bars
    ) / total_vol
    std_dev = math.sqrt(max(0.0, variance))
    return vwap, std_dev


def calculate_vwap_bands(
    bars: List[Union[BarEvent, Dict[str, Any]]],
    mults: Tuple[float, float] = (1.0, 2.0),
) -> Dict[str, float]:
    """Calculate anchored VWAP with upper and lower standard deviation bands."""
    vwap, std = calculate_anchored_vwap(bars)
    m1, m2 = mults
    return {
        "vwap": vwap,
        "std": std,
        "upper_band_1": vwap + (m1 * std),
        "lower_band_1": vwap - (m1 * std),
        "upper_band_2": vwap + (m2 * std),
        "lower_band_2": vwap - (m2 * std),
    }


def calculate_atr(bars: List[Union[BarEvent, Dict[str, Any]]], period: int = 14) -> float:
    """Calculate Average True Range (ATR) over period bars."""
    if not bars:
        return 0.01

    tr_list: List[float] = []
    prev_close: Optional[float] = None

    for b in bars:
        _, h, l, c, _ = _extract_ohlcv(b)
        if prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        tr_list.append(tr)
        prev_close = c

    if len(tr_list) <= period:
        return max(0.01, sum(tr_list) / len(tr_list))

    # Wilder's smoothing
    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return max(0.01, atr)


def calculate_ema(prices: List[float], period: int) -> float:
    """Calculate Exponential Moving Average (EMA) of prices."""
    if not prices:
        return 0.0
    if len(prices) <= period:
        return sum(prices) / len(prices)

    alpha = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = alpha * p + (1.0 - alpha) * ema
    return ema


def calculate_sma(prices: List[float], period: int) -> float:
    """Calculate Simple Moving Average (SMA) of prices."""
    if not prices:
        return 0.0
    window = prices[-period:] if len(prices) >= period else prices
    return sum(window) / float(len(window))


def calculate_zscore(prices: List[float], period: int = 20) -> Tuple[float, float, float]:
    """Compute moving average, std dev, and price Z-score over period.

    Returns:
        (mean, std_dev, z_score)
    """
    if len(prices) < period:
        return 0.0, 0.0, 0.0
    window = prices[-period:]
    mean = sum(window) / float(period)
    variance = sum((p - mean) ** 2 for p in window) / float(period)
    std = math.sqrt(variance)
    if std <= 0.0001:
        return round(mean, 2), 0.0, 0.0
    z_score = (prices[-1] - mean) / std
    return round(mean, 2), round(std, 2), round(z_score, 2)


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """Calculate Wilder's 14-period Relative Strength Index (RSI)."""
    if len(prices) < 2:
        return 50.0

    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(0.0, c) for c in changes]
    losses = [max(0.0, -c) for c in changes]

    if len(changes) < period:
        avg_gain = sum(gains) / len(gains) if gains else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
    else:
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(changes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


MIN_STOP_DISTANCE_PCT = 0.0040  # Must match InstitutionalRiskEngine's 0.4% floor.


def resolve_stop(entry_price: float, raw_dist: float, is_long: bool) -> Tuple[float, float]:
    """Place a stop at raw_dist from entry, widened to the risk engine's 0.4% floor.

    A stop that is too WIDE is returned untouched. The risk engine rejects that
    signal, which is the correct outcome: pulling the stop in to satisfy the
    4.0% ceiling would move it inside the structure that justified the trade.

    The stop is rounded away from entry so the realised distance can never land
    a fraction below the floor and trip a knife-edge rejection.
    """
    risk = max(entry_price * MIN_STOP_DISTANCE_PCT, raw_dist)
    if is_long:
        stop = math.floor((entry_price - risk) * 10000) / 10000
    else:
        stop = math.ceil((entry_price + risk) * 10000) / 10000
    return stop, abs(entry_price - stop)


def calculate_rvol(current_volume: float, baseline_volume: float) -> float:
    """Calculate Relative Volume (RVOL)."""
    if baseline_volume <= 0:
        return 1.0
    return round(current_volume / baseline_volume, 2)


# ============================================================================
# Abstract Base Strategy
# ============================================================================

class Strategy(ABC):
    """Abstract Base Class for intraday trading strategies."""

    def __init__(self, strategy_id: str, name: str):
        self.strategy_id: str = strategy_id
        self.name: str = name
        self.status: StrategyStatus = StrategyStatus.ACTIVE

        # Performance Tracking
        self.daily_pnl: float = 0.0
        self.trades_count: int = 0
        self.wins_count: int = 0
        self.losses_count: int = 0
        self.win_rate: float = 0.0
        self._trade_pnls: List[float] = []

    @abstractmethod
    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        """Handle 1-minute OHLCV bar update and emit any trading signals."""
        pass

    def on_quote(self, quote: QuoteEvent) -> None:
        """Handle top-of-book NBBO quote update."""
        pass

    def on_news(self, news: NewsEvent) -> List[SignalEvent]:
        """Handle incoming news article with sentiment."""
        return []

    def on_vix(self, vix: VixPrint) -> None:
        """Handle spot VIX print update."""
        pass

    def on_time_tick(self, market_time: datetime) -> None:
        """Handle session clock tick."""
        pass

    def record_trade(self, pnl: float) -> None:
        """Record executed trade PnL and update performance metrics."""
        self.daily_pnl = round(self.daily_pnl + pnl, 2)
        self.trades_count += 1
        self._trade_pnls.append(pnl)
        if pnl > 0:
            self.wins_count += 1
        elif pnl < 0:
            self.losses_count += 1
        self.win_rate = round(self.wins_count / self.trades_count, 2) if self.trades_count > 0 else 0.0

    @property
    def sharpe(self) -> float:
        """Daily Sharpe approximation from per-trade PnL distribution (mean / std)."""
        n = len(self._trade_pnls)
        if n < 2:
            return 0.0
        mean = sum(self._trade_pnls) / n
        variance = sum((p - mean) ** 2 for p in self._trade_pnls) / n
        std = math.sqrt(variance)
        if std <= 0:
            return 0.0
        return round(mean / std, 2)

    def reset_daily_stats(self) -> None:
        """Reset daily performance metrics at start of session."""
        self.daily_pnl = 0.0
        self.trades_count = 0
        self.wins_count = 0
        self.losses_count = 0
        self.win_rate = 0.0
        self._trade_pnls.clear()
        # An operator pause is deliberate and survives the new session; cooldowns expire.
        if self.status != StrategyStatus.PAUSED:
            self.status = StrategyStatus.ACTIVE

    def pause(self) -> None:
        """Pause strategy execution."""
        self.status = StrategyStatus.PAUSED

    def resume(self) -> None:
        """Resume strategy execution."""
        self.status = StrategyStatus.ACTIVE

    def cooldown(self) -> None:
        """Put strategy in cooldown."""
        self.status = StrategyStatus.COOLDOWN

    def to_dict(self) -> Dict[str, Any]:
        """Serialize strategy state for UI WebSocket broadcast."""
        return {
            "id": self.strategy_id,
            "name": self.name,
            "status": self.status.value,
            "daily_pnl": self.daily_pnl,
            "win_rate": self.win_rate,
            "trades_count": self.trades_count,
            "sharpe": self.sharpe,
        }
