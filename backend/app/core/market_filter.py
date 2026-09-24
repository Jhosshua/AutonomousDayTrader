"""backend/app/core/market_filter.py
Deterministic Causal Market Trend Filter.
Tracks SPY and QQQ anchored VWAPs (anchored to 09:30 ET) and 9/21 EMAs.
Classifies real-time market regime (BULLISH, BEARISH, NEUTRAL, UNKNOWN)
with zero lookahead bias and fail-closed staleness protection.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timezone
from enum import Enum
import math
from typing import Dict, Any, Dict, List, Optional, Tuple, Union
import zoneinfo

from pydantic import BaseModel, Field

from backend.app.models.events import BarEvent, OrderSide

ET_TZ = zoneinfo.ZoneInfo("America/New_York")


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class MarketTrend(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class IndexMetrics(BaseModel):
    symbol: str
    last_price: float
    vwap: float
    ema_fast: float
    ema_slow: float
    price_to_vwap_pct: float
    bars_count: int
    is_bullish: bool
    is_bearish: bool
    last_updated: Optional[datetime] = None


class MarketTrendSnapshot(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    overall_trend: MarketTrend
    spy: Optional[IndexMetrics] = None
    qqq: Optional[IndexMetrics] = None
    reason: str
    is_fresh: bool


@dataclass
class IndexState:
    symbol: str
    cum_pv: float = 0.0
    cum_vol: float = 0.0
    current_vwap: float = 0.0
    ema9: float = 0.0
    ema21: float = 0.0
    last_price: float = 0.0
    last_timestamp: Optional[datetime] = None
    first_open: Optional[float] = None
    bars_count: int = 0
    closes: List[float] = field(default_factory=list)

    def update_bar(self, bar: BarEvent) -> None:
        if bar.timestamp is None:
            return
        typical_p = (bar.high + bar.low + bar.close) / 3.0
        vol = float(bar.volume)
        self.cum_pv += typical_p * vol
        self.cum_vol += vol
        self.current_vwap = round(self.cum_pv / self.cum_vol, 4) if self.cum_vol > 0 else bar.close
        self.last_price = bar.close
        self.last_timestamp = bar.timestamp
        if self.bars_count == 0:
            self.first_open = bar.open
        self.bars_count += 1
        self.closes.append(bar.close)
        if len(self.closes) > 60:
            del self.closes[:-60]

        # Calculate EMAs
        if self.bars_count == 1:
            self.ema9 = bar.close
            self.ema21 = bar.close
        else:
            k9 = 2.0 / (9.0 + 1.0)
            k21 = 2.0 / (21.0 + 1.0)
            self.ema9 = round(bar.close * k9 + self.ema9 * (1.0 - k9), 4)
            self.ema21 = round(bar.close * k21 + self.ema21 * (1.0 - k21), 4)

    def is_bullish(self, deadband: float = 0.0003) -> bool:
        if self.current_vwap <= 0 or self.bars_count == 0:
            return False
        price_above_vwap = self.last_price > (self.current_vwap * (1.0 + deadband))
        ema_aligned = (self.ema9 >= self.ema21) or (self.bars_count < 5)
        return price_above_vwap and ema_aligned

    def is_bearish(self, deadband: float = 0.0003) -> bool:
        if self.current_vwap <= 0 or self.bars_count == 0:
            return False
        price_below_vwap = self.last_price < (self.current_vwap * (1.0 - deadband))
        ema_aligned = (self.ema9 <= self.ema21) or (self.bars_count < 5)
        return price_below_vwap and ema_aligned

    def to_metrics(self) -> IndexMetrics:
        p_to_vwap = 0.0
        if self.current_vwap > 0:
            p_to_vwap = round((self.last_price - self.current_vwap) / self.current_vwap * 100.0, 3)
        return IndexMetrics(
            symbol=self.symbol,
            last_price=self.last_price,
            vwap=self.current_vwap,
            ema_fast=self.ema9,
            ema_slow=self.ema21,
            price_to_vwap_pct=p_to_vwap,
            bars_count=self.bars_count,
            is_bullish=self.is_bullish(),
            is_bearish=self.is_bearish(),
            last_updated=self.last_timestamp,
        )


class MarketTrendFilter:
    """Institutional Causal Market Index Trend Filter.

    Tracks SPY and QQQ anchored VWAPs and 9/21 EMAs.
    Provides consensus market regime gating to prevent single-stock
    strategies from executing counter to broader market liquidity flows.
    """

    def __init__(self, stale_threshold_sec: float = 120.0):
        self.stale_threshold_sec: float = stale_threshold_sec
        self.spy_state: IndexState = IndexState(symbol="SPY")
        self.qqq_state: IndexState = IndexState(symbol="QQQ")
        self.last_session_date: Optional[date] = None

    def reset_session(self, session_date: Optional[date] = None) -> None:
        """Reset anchored VWAP and intraday EMAs at session start."""
        self.spy_state = IndexState(symbol="SPY")
        self.qqq_state = IndexState(symbol="QQQ")
        self.last_session_date = session_date
        self._last_bar_ts: Dict[str, datetime] = {}

    def on_bar(self, bar: BarEvent) -> None:
        """Ingest bar update for SPY or QQQ."""
        if bar.timestamp is None:
            return
        sym = bar.symbol.upper()
        if sym not in ("SPY", "QQQ"):
            return

        # Check session boundary in ET
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        bar_dt = ts.astimezone(ET_TZ)

        if self.last_session_date is None or bar_dt.date() != self.last_session_date:
            self.reset_session(bar_dt.date())

        # Discard pre-market bars from regular-session anchored VWAP
        if bar_dt.time() < dtime(9, 30):
            return

        # A minute already folded in (e.g. by a REST rebuild) must not be counted twice.
        seen = getattr(self, "_last_bar_ts", None)
        if seen is None:
            seen = self._last_bar_ts = {}
        if sym in seen and ts <= seen[sym]:
            return
        seen[sym] = ts

        if sym == "SPY":
            self.spy_state.update_bar(bar)
        elif sym == "QQQ":
            self.qqq_state.update_bar(bar)

    def get_current_trend(self, asof: Optional[datetime] = None) -> Tuple[MarketTrend, str]:
        """Compute composite market trend across SPY and QQQ with fail-closed staleness check."""
        # 1. Check data availability
        if self.spy_state.bars_count == 0 or self.qqq_state.bars_count == 0:
            return MarketTrend.UNKNOWN, "MISSING_INDEX_BARS: SPY or QQQ has zero regular session bars"

        # 2. Check freshness against asof timestamp
        now = _to_utc(asof) if asof is not None else datetime.now(timezone.utc)

        if self.spy_state.last_timestamp:
            spy_ts = _to_utc(self.spy_state.last_timestamp)
            elapsed = (now - spy_ts).total_seconds()
            if elapsed < -1.0:
                return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: Index timestamp is in the future ({elapsed:.1f}s)"
            if elapsed > self.stale_threshold_sec:
                return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({elapsed:.1f}s) > {self.stale_threshold_sec}s"

        if self.qqq_state.last_timestamp:
            qqq_ts = _to_utc(self.qqq_state.last_timestamp)
            elapsed = (now - qqq_ts).total_seconds()
            if elapsed < -1.0:
                return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: Index timestamp is in the future ({elapsed:.1f}s)"
            if elapsed > self.stale_threshold_sec:
                return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: QQQ data age ({elapsed:.1f}s) > {self.stale_threshold_sec}s"

        # 3. Check early open convergence (first 3 minutes)
        if self.spy_state.bars_count < 3 or self.qqq_state.bars_count < 3:
            spy_base = self.spy_state.first_open if self.spy_state.first_open is not None else self.spy_state.closes[0]
            qqq_base = self.qqq_state.first_open if self.qqq_state.first_open is not None else self.qqq_state.closes[0]
            spy_up = self.spy_state.last_price >= spy_base
            qqq_up = self.qqq_state.last_price >= qqq_base
            if spy_up and qqq_up:
                return MarketTrend.BULLISH, "EARLY_OPEN_CONVERGENCE: SPY and QQQ green from open"
            elif (not spy_up) and (not qqq_up):
                return MarketTrend.BEARISH, "EARLY_OPEN_CONVERGENCE: SPY and QQQ red from open"
            return MarketTrend.NEUTRAL, "EARLY_OPEN_MIXED: Opening bars divergent"

        # 4. Standard VWAP + EMA consensus
        spy_bull = self.spy_state.is_bullish()
        qqq_bull = self.qqq_state.is_bullish()
        spy_bear = self.spy_state.is_bearish()
        qqq_bear = self.qqq_state.is_bearish()

        if spy_bull and qqq_bull:
            return (
                MarketTrend.BULLISH,
                f"BULLISH: SPY ({self.spy_state.last_price:.2f} > VWAP {self.spy_state.current_vwap:.2f}) and QQQ ({self.qqq_state.last_price:.2f} > VWAP {self.qqq_state.current_vwap:.2f})"
            )
        elif spy_bear and qqq_bear:
            return (
                MarketTrend.BEARISH,
                f"BEARISH: SPY ({self.spy_state.last_price:.2f} < VWAP {self.spy_state.current_vwap:.2f}) and QQQ ({self.qqq_state.last_price:.2f} < VWAP {self.qqq_state.current_vwap:.2f})"
            )

        return MarketTrend.NEUTRAL, "NEUTRAL: SPY and QQQ divergent or trading inside VWAP noise band"

    def get_trend_snapshot(self, asof: Optional[datetime] = None) -> MarketTrendSnapshot:
        """Return rich snapshot model of the market trend filter state."""
        trend, reason = self.get_current_trend(asof)
        is_fresh = trend != MarketTrend.UNKNOWN
        now = _to_utc(asof) if asof is not None else datetime.now(timezone.utc)
        return MarketTrendSnapshot(
            timestamp=now,
            overall_trend=trend,
            spy=self.spy_state.to_metrics() if self.spy_state.bars_count > 0 else None,
            qqq=self.qqq_state.to_metrics() if self.qqq_state.bars_count > 0 else None,
            reason=reason,
            is_fresh=is_fresh,
        )

    def is_signal_permitted(
        self,
        strategy_id: str = "",
        side: Union[OrderSide, str] = OrderSide.BUY,
        symbol: str = "",
        asof: Optional[datetime] = None,
        catalyst_sentiment: Optional[float] = 0.0,
        volume_surge: Optional[float] = 0.0,
        rvol: Optional[float] = None,
        strategy_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Tuple[bool, str]:
        """Validate if a proposed trade signal is directionally aligned with market beta.

        Policy Matrix:
        - In MarketTrend.NEUTRAL:
          - Permit mean_reversion (both BUY and SELL).
          - Permit orb and news_momentum if rvol is not None and rvol >= 2.20 with reason containing
            APPROVED_IDIOSYNCRATIC_BREAKOUT. If rvol < 2.20 or None, deny with INDEX_FILTER_DENIED.
          - Deny vwap_pullback with INDEX_FILTER_DENIED.
        - In MarketTrend.BULLISH / BEARISH:
          - Permit orb and vwap_pullback along index beta (BUY in BULLISH, SELL in BEARISH).
          - Permit news_momentum along index beta (or if extreme catalyst).
          - Deny counter-trend mean_reversion with INDEX_FILTER_DENIED.
        """
        trend, reason = self.get_current_trend(asof)
        strat = (strategy_name or strategy_id).lower()
        is_buy = (side == OrderSide.BUY) if isinstance(side, OrderSide) else (str(side).upper() == "BUY")

        # 1. News Momentum Extreme Catalyst Override Check
        # Extreme idiosyncratic catalysts (|S| >= 0.85, volume >= 5.0x) decouple from market beta
        if strat == "news_momentum":
            is_extreme = (
                catalyst_sentiment is not None
                and abs(catalyst_sentiment) >= 0.85
                and volume_surge is not None
                and volume_surge >= 5.0
            )
            if is_extreme:
                return True, f"APPROVED_EXTREME_CATALYST: News momentum overrides index with |S|={abs(catalyst_sentiment):.2f}>=0.85 and vol={volume_surge:.1f}>=5.0x"

        # Fail-closed when trend is unknown for all standard signals
        if trend == MarketTrend.UNKNOWN:
            return False, f"INDEX_FILTER_DENIED: Market trend UNKNOWN ({reason})"

        # 2. MarketTrend.NEUTRAL Execution Rules
        if trend == MarketTrend.NEUTRAL:
            if strat == "mean_reversion":
                return True, f"APPROVED: Mean reversion permitted in NEUTRAL market on {symbol}"
            if strat in ("orb", "news_momentum"):
                if rvol is not None and rvol >= 2.20:
                    return True, f"APPROVED_IDIOSYNCRATIC_BREAKOUT: {strat.upper()} permitted in NEUTRAL market on high RVOL ({rvol:.2f} >= 2.20x)"
                return False, f"INDEX_FILTER_DENIED: {strat.upper()} requires directional market trend or high RVOL >= 2.20x in NEUTRAL (got RVOL={rvol})"
            if strat == "vwap_pullback":
                return False, f"INDEX_FILTER_DENIED: VWAP_PULLBACK requires directional market trend (currently NEUTRAL)"
            return False, f"INDEX_FILTER_DENIED: {strat.upper()} not permitted in NEUTRAL market"

        # 3. MarketTrend.BULLISH & MarketTrend.BEARISH Execution Rules
        if strat in ("orb", "vwap_pullback"):
            if trend == MarketTrend.BULLISH and not is_buy:
                return False, f"INDEX_BETA_CONTRADICTION: Cannot open SHORT on {symbol} when market trend is BULLISH"
            if trend == MarketTrend.BEARISH and is_buy:
                return False, f"INDEX_BETA_CONTRADICTION: Cannot open LONG on {symbol} when market trend is BEARISH"

        elif strat == "news_momentum":
            if trend == MarketTrend.BULLISH and not is_buy:
                return False, f"INDEX_BETA_CONTRADICTION: Shorting {symbol} on news denied during BULLISH market rally"
            if trend == MarketTrend.BEARISH and is_buy:
                return False, f"INDEX_BETA_CONTRADICTION: Buying {symbol} on news denied during BEARISH market decline"

        elif strat == "mean_reversion":
            if trend == MarketTrend.BULLISH and not is_buy:
                return False, f"INDEX_FILTER_DENIED: INDEX_BETA_CONTRADICTION: Shorting overbought {symbol} denied during strong BULLISH market rally"
            if trend == MarketTrend.BEARISH and is_buy:
                return False, f"INDEX_FILTER_DENIED: INDEX_BETA_CONTRADICTION: Buying oversold {symbol} (catching falling knife) denied during strong BEARISH market decline"

        return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"
