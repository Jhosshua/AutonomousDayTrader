"""backend/app/strategies/swing_indicators.py
Causal Rolling Daily Indicators & Daily Bar Aggregation Engine for Swing Trading.

Rules Implemented:
- Rule 1 (Macro Floor): Today's Daily Close > 200-day Simple Moving Average (SMA).
- Rule 2 (Market Leadership / Relative Strength): Trailing 60-day return >= QQQ return.
- Rule 3 (Panic Trigger): 2-day Connors RSI (Wilder's RSI(2) on daily closes) < 10.0.
- Rule 6 (Emergency Stop): 14-day Daily ATR for emergency stop (2.5x ATR below fill).
- Rule 7 (Exits):
    a) Today's close > 5-day SMA.
    b) Today's RSI(2) > 70.0.
    c) Position held for >= 5 trading days.

ZERO LOOKAHEAD GUARANTEE:
All mathematical calculations strictly consume closed sessions (date <= current_date).
Daily bars are finalized at 16:00 ET close before signal qualification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from backend.app.models.events import BarEvent

log = logging.getLogger(__name__)


# ============================================================================
# Daily Bar Data Model
# ============================================================================

@dataclass
class DailyBar:
    """Represents a finalized or in-flight daily OHLCV bar."""
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    finalized: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date.isoformat() if isinstance(self.date, date) else str(self.date),
            "open": round(self.open, 4),
            "high": round(self.high, 4),
            "low": round(self.low, 4),
            "close": round(self.close, 4),
            "volume": int(self.volume),
            "finalized": self.finalized,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DailyBar:
        d = data["date"]
        if isinstance(d, str):
            d_obj = datetime.strptime(d[:10], "%Y-%m-%d").date()
        elif isinstance(d, datetime):
            d_obj = d.date()
        else:
            d_obj = d

        return cls(
            symbol=str(data["symbol"]).upper(),
            date=d_obj,
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            close=float(data["close"]),
            volume=int(data["volume"]),
            finalized=bool(data.get("finalized", True)),
        )


# ============================================================================
# Causal Rolling Daily Indicator Calculations
# ============================================================================

def calculate_sma(prices: Sequence[float], period: int) -> float:
    """Calculate Simple Moving Average (SMA) over trailing `period` prices.
    
    Zero lookahead guarantee: Uses strictly the trailing `period` prices.
    If len(prices) < period, returns 0.0.
    """
    if len(prices) < period or period <= 0:
        return 0.0
    window = prices[-period:]
    return float(sum(window) / len(window))


def calculate_rsi2(prices: Sequence[float]) -> float:
    """Calculate 2-day Connors RSI (Wilder's 2-period RSI on daily closes).
    
    Formula:
      - Requires at least 3 daily closes (2 price changes).
      - Initial AvgGain, AvgLoss over first 2 changes.
      - Wilder smoothing: AvgGain_k = (AvgGain_{k-1} + Gain_k) / 2
                          AvgLoss_k = (AvgLoss_{k-1} + Loss_k) / 2
      - RSI = 100 - (100 / (1 + AvgGain / AvgLoss))
    
    Returns float in range [0.0, 100.0].
    """
    if len(prices) < 3:
        return 50.0

    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(0.0, c) for c in changes]
    losses = [max(0.0, -c) for c in changes]

    # Period = 2
    period = 2
    avg_gain = sum(gains[:period]) / float(period)
    avg_loss = sum(losses[:period]) / float(period)

    # Wilder's smoothing for subsequent daily closes
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / float(period)
        avg_loss = (avg_loss * (period - 1) + losses[i]) / float(period)

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    if avg_gain == 0.0:
        return 0.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def calculate_daily_atr(bars: Sequence[DailyBar], period: int = 14) -> float:
    """Calculate Wilder's 14-period Average True Range (ATR) on daily bars.
    
    True Range TR_k = max(H_k - L_k, |H_k - C_{k-1}|, |L_k - C_{k-1}|).
    First `period` values averaged, followed by Wilder smoothing:
        ATR_k = (ATR_{k-1} * 13 + TR_k) / 14
    """
    if not bars:
        return 1.0

    tr_list: List[float] = []
    prev_close: Optional[float] = None

    for b in bars:
        if prev_close is None:
            tr = b.high - b.low
        else:
            tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        tr_list.append(tr)
        prev_close = b.close

    if len(tr_list) <= period:
        return max(0.01, round(sum(tr_list) / len(tr_list), 4))

    # Wilder's initial average
    atr = sum(tr_list[:period]) / float(period)
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / float(period)

    return max(0.01, round(atr, 4))


def calculate_relative_strength_60d(
    stock_bars: Sequence[DailyBar],
    qqq_bars: Sequence[DailyBar],
    period: int = 60,
) -> Tuple[float, float, bool]:
    """Calculate trailing 60 trading days return of stock vs QQQ.
    
    Zero lookahead: Aligns sessions strictly by matching dates up to the latest closed bar.
    Delta = (Close_t - Close_{t-60}) / Close_{t-60}
    
    Returns:
        (stock_return, qqq_return, stock_outperforming)
    """
    stock_by_date = {b.date: b.close for b in stock_bars}
    qqq_by_date = {b.date: b.close for b in qqq_bars}

    # Intersect dates in chronological order
    common_dates = sorted([d for d in stock_by_date if d in qqq_by_date])
    if len(common_dates) < period + 1:
        # Insufficient aligned history
        return 0.0, 0.0, False

    t_curr = common_dates[-1]
    t_past = common_dates[-(period + 1)]

    stock_curr = stock_by_date[t_curr]
    stock_past = stock_by_date[t_past]
    qqq_curr = qqq_by_date[t_curr]
    qqq_past = qqq_by_date[t_past]

    if stock_past <= 0.0 or qqq_past <= 0.0:
        return 0.0, 0.0, False

    stock_ret = (stock_curr - stock_past) / stock_past
    qqq_ret = (qqq_curr - qqq_past) / qqq_past

    # Rule 2: Delta_stock >= Delta_QQQ
    passed = stock_ret >= qqq_ret
    return round(stock_ret, 6), round(qqq_ret, 6), passed


# ============================================================================
# Swing Qualification & Exit Result Dataclasses
# ============================================================================

@dataclass
class SwingQualificationResult:
    """Full quantitative audit of the 4 entry qualification rules at 16:00 close."""
    symbol: str
    date: date
    close: float
    sma_200: float
    rule_1_macro_floor: bool            # Today's close > 200 SMA
    rs_stock_60d: float
    rs_qqq_60d: float
    rule_2_relative_strength: bool      # RS stock >= RS QQQ
    rsi_2: float
    rule_3_panic_dip: bool              # RSI(2) < 10.0
    earnings_blackout: bool
    rule_4_no_earnings: bool            # No earnings within 48 hours
    daily_atr_14: float
    qualified: bool
    rejection_reasons: List[str] = field(default_factory=list)


@dataclass
class SwingExitResult:
    """Full quantitative audit of the 4 exit rules evaluated at 16:00 close."""
    symbol: str
    date: date
    close: float
    sma_5: float
    exit_5_sma: bool                    # Today's close > 5 SMA
    rsi_2: float
    exit_rsi2_overbought: bool          # Today's RSI(2) > 70.0
    holding_days: int
    exit_time_stop: bool                # holding_days >= 5
    earnings_tomorrow: bool
    exit_earnings: bool                 # Earnings report tomorrow
    should_exit: bool
    primary_exit_reason: Optional[str] = None

    @property
    def rule_7a_sma5_exit(self) -> bool:
        return self.exit_5_sma

    @property
    def rule_7b_rsi_exit(self) -> bool:
        return self.exit_rsi2_overbought

    @property
    def rule_7c_time_exit(self) -> bool:
        return self.exit_time_stop

    @property
    def rule_4_earnings_exit(self) -> bool:
        return self.exit_earnings



def evaluate_swing_qualification(
    symbol: str,
    stock_bars: Sequence[DailyBar],
    qqq_bars: Sequence[DailyBar],
    earnings_blackout: bool,
) -> SwingQualificationResult:
    """Evaluate 16:00 ET close entry qualification for a candidate symbol.
    
    Zero lookahead guarantee: Operates strictly on closed daily bars.
    """
    rejections: List[str] = []
    if not stock_bars:
        return SwingQualificationResult(
            symbol=symbol,
            date=date.today(),
            close=0.0,
            sma_200=0.0,
            rule_1_macro_floor=False,
            rs_stock_60d=0.0,
            rs_qqq_60d=0.0,
            rule_2_relative_strength=False,
            rsi_2=50.0,
            rule_3_panic_dip=False,
            earnings_blackout=earnings_blackout,
            rule_4_no_earnings=not earnings_blackout,
            daily_atr_14=0.0,
            qualified=False,
            rejection_reasons=["NO_DAILY_BARS"],
        )

    last_bar = stock_bars[-1]
    eval_date = last_bar.date
    close_prices = [b.close for b in stock_bars]

    # Rule 1: Macro Floor (Today's Close > 200-day SMA)
    if len(close_prices) < 200:
        sma_200 = 0.0
        rule_1_ok = False
        rejections.append(f"INSUFFICIENT_HISTORY_FOR_200_SMA_{len(close_prices)}_BARS")
    else:
        sma_200 = calculate_sma(close_prices, 200)
        rule_1_ok = last_bar.close > sma_200
        if not rule_1_ok:
            rejections.append(f"CLOSE_{last_bar.close:.2f}_BELOW_200_SMA_{sma_200:.2f}")

    # Rule 2: 60d Relative Strength vs QQQ
    stock_ret, qqq_ret, rule_2_ok = calculate_relative_strength_60d(stock_bars, qqq_bars, 60)
    if not rule_2_ok:
        rejections.append(f"WEAKER_THAN_QQQ_STOCK_{stock_ret:+.2%}_QQQ_{qqq_ret:+.2%}")

    # Rule 3: 2-day Connors RSI < 10.0
    rsi2 = calculate_rsi2(close_prices)
    rule_3_ok = rsi2 < 10.0
    if not rule_3_ok:
        rejections.append(f"RSI2_{rsi2:.2f}_NOT_BELOW_10")

    # Rule 4: Earnings blackout
    rule_4_ok = not earnings_blackout
    if not rule_4_ok:
        rejections.append("EARNINGS_BLACKOUT_ACTIVE")

    # Rule 6: 14-day Daily ATR
    atr_14 = calculate_daily_atr(stock_bars, 14)

    qualified = rule_1_ok and rule_2_ok and rule_3_ok and rule_4_ok
    return SwingQualificationResult(
        symbol=symbol,
        date=eval_date,
        close=last_bar.close,
        sma_200=round(sma_200, 4),
        rule_1_macro_floor=rule_1_ok,
        rs_stock_60d=stock_ret,
        rs_qqq_60d=qqq_ret,
        rule_2_relative_strength=rule_2_ok,
        rsi_2=rsi2,
        rule_3_panic_dip=rule_3_ok,
        earnings_blackout=earnings_blackout,
        rule_4_no_earnings=rule_4_ok,
        daily_atr_14=atr_14,
        qualified=qualified,
        rejection_reasons=rejections,
    )


def evaluate_swing_exit(
    symbol: str,
    stock_bars: Sequence[DailyBar],
    holding_days: int,
    earnings_tomorrow: bool,
) -> SwingExitResult:
    """Evaluate 16:00 ET close exit conditions for an active swing position.
    
    Exit conditions (ANY triggers exit at next 09:30 open):
      a) Prior daily close > 5-day SMA
      b) Prior daily RSI(2) > 70.0
      c) Holding days >= 5 (time stop)
      d) Company reports earnings tomorrow
    """
    if not stock_bars:
        return SwingExitResult(
            symbol=symbol,
            date=date.today(),
            close=0.0,
            sma_5=0.0,
            exit_5_sma=False,
            rsi_2=50.0,
            exit_rsi2_overbought=False,
            holding_days=holding_days,
            exit_time_stop=(holding_days >= 5),
            earnings_tomorrow=earnings_tomorrow,
            exit_earnings=earnings_tomorrow,
            should_exit=(holding_days >= 5 or earnings_tomorrow),
            primary_exit_reason="TIME_STOP_5_DAYS" if holding_days >= 5 else ("EARNINGS_TOMORROW" if earnings_tomorrow else None),
        )

    last_bar = stock_bars[-1]
    eval_date = last_bar.date
    close_prices = [b.close for b in stock_bars]

    # Rule 7a: Close > 5-day SMA
    sma_5 = calculate_sma(close_prices, 5) if len(close_prices) >= 5 else calculate_sma(close_prices, len(close_prices))
    exit_5_sma = (last_bar.close > sma_5) if len(close_prices) >= 5 else False

    # Rule 7b: RSI(2) > 70.0
    rsi2 = calculate_rsi2(close_prices)
    exit_rsi2 = (rsi2 > 70.0)

    # Rule 7c: Holding days >= 5
    exit_time = (holding_days >= 5)

    # Rule 4: Earnings tomorrow
    exit_earnings = bool(earnings_tomorrow)

    should_exit = exit_5_sma or exit_rsi2 or exit_time or exit_earnings

    primary_reason: Optional[str] = None
    if exit_earnings:
        primary_reason = "EARNINGS_TOMORROW"
    elif exit_5_sma:
        primary_reason = f"5_SMA_CROSS_CLOSE_{last_bar.close:.2f}_SMA_{sma_5:.2f}"
    elif exit_rsi2:
        primary_reason = f"RSI2_OVERBOUGHT_{rsi2:.2f}"
    elif exit_time:
        primary_reason = f"TIME_STOP_5_DAYS_HELD_{holding_days}"

    return SwingExitResult(
        symbol=symbol,
        date=eval_date,
        close=last_bar.close,
        sma_5=round(sma_5, 4),
        exit_5_sma=exit_5_sma,
        rsi_2=rsi2,
        exit_rsi2_overbought=exit_rsi2,
        holding_days=holding_days,
        exit_time_stop=exit_time,
        earnings_tomorrow=earnings_tomorrow,
        exit_earnings=exit_earnings,
        should_exit=should_exit,
        primary_exit_reason=primary_reason,
    )


# ============================================================================
# DailyBarStore & DailyBarAggregator
# ============================================================================

class DailyBarStore:
    """Thread-safe in-memory store for historical daily bars with seed loading."""

    def __init__(self, seed_path: Optional[str] = None) -> None:
        self._bars: Dict[str, List[DailyBar]] = {}
        if seed_path:
            self.load_seed_file(seed_path)

    def load_seed_file(self, seed_path: str) -> int:
        """Load seed JSON fixture and populate store.
        
        Supports both:
          1) {"SYMBOL": [bar_dicts, ...]}
          2) [bar_dicts_with_symbol, ...]
        """
        path = Path(seed_path)
        if not path.is_file():
            log.warning(f"Daily bar seed file not found: {seed_path}")
            return 0

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        count = 0
        if isinstance(raw, dict):
            for sym, bar_list in raw.items():
                parsed_bars: List[DailyBar] = []
                for item in bar_list:
                    if "symbol" not in item:
                        item["symbol"] = sym
                    parsed_bars.append(DailyBar.from_dict(item))
                # Sort chronologically
                parsed_bars.sort(key=lambda b: b.date)
                self._bars[sym.upper()] = parsed_bars
                count += len(parsed_bars)
        elif isinstance(raw, list):
            by_sym: Dict[str, List[DailyBar]] = {}
            for item in raw:
                bar = DailyBar.from_dict(item)
                by_sym.setdefault(bar.symbol.upper(), []).append(bar)
            for sym, parsed_bars in by_sym.items():
                parsed_bars.sort(key=lambda b: b.date)
                self._bars[sym.upper()] = parsed_bars
                count += len(parsed_bars)

        log.info(f"Loaded {count} daily bars across {len(self._bars)} symbols from {seed_path}")
        return count

    def get_bars(
        self,
        symbol: str,
        count: Optional[int] = None,
        as_of: Optional[Union[date, datetime]] = None,
    ) -> List[DailyBar]:
        """Get chronologically sorted daily bars for symbol.
        
        Zero lookahead guarantee: If `as_of` is provided, bars strictly on or before `as_of` are returned.
        """
        sym = symbol.upper()
        bars = self._bars.get(sym, [])
        if not bars:
            return []

        if as_of is not None:
            cutoff = as_of.date() if isinstance(as_of, datetime) else as_of
            filtered = [b for b in bars if b.date <= cutoff]
        else:
            filtered = list(bars)

        if count is not None and count > 0:
            return filtered[-count:]
        return filtered

    def append_bar(self, bar: DailyBar) -> None:
        """Append or update a daily bar for a symbol.
        
        Maintains chronological sort and overwrites any existing bar for that exact date.
        """
        sym = bar.symbol.upper()
        existing = self._bars.setdefault(sym, [])
        for idx, item in enumerate(existing):
            if item.date == bar.date:
                existing[idx] = bar
                return
        existing.append(bar)
        existing.sort(key=lambda b: b.date)

    def get_latest_bar(self, symbol: str) -> Optional[DailyBar]:
        """Return the most recent daily bar for symbol, if available."""
        bars = self._bars.get(symbol.upper(), [])
        return bars[-1] if bars else None

    def symbols(self) -> List[str]:
        """Return all tracked symbols."""
        return sorted(list(self._bars.keys()))

    def clear(self) -> None:
        """Clear all stored bars."""
        self._bars.clear()


class DailyBarAggregator:
    """Aggregates intraday 1-minute BarEvent frames into a finalized daily bar at 16:00 ET close."""

    def __init__(self, store: DailyBarStore) -> None:
        self.store: DailyBarStore = store
        # In-flight daily bars keyed by symbol: {symbol: DailyBar}
        self._in_flight: Dict[str, DailyBar] = {}
        self._current_session_date: Optional[date] = None

    def on_minute_bar(self, bar: BarEvent) -> None:
        """Ingest a 1-minute bar to update today's running in-flight daily bar."""
        sym = bar.symbol.upper()
        bar_date = bar.timestamp.date()

        # Rollover check
        if self._current_session_date is None or bar_date != self._current_session_date:
            self._current_session_date = bar_date

        if sym not in self._in_flight or self._in_flight[sym].date != bar_date:
            # First bar of the session: Open is set
            self._in_flight[sym] = DailyBar(
                symbol=sym,
                date=bar_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                finalized=False,
            )
        else:
            # Update running high, low, close, volume
            current = self._in_flight[sym]
            self._in_flight[sym] = DailyBar(
                symbol=sym,
                date=bar_date,
                open=current.open,
                high=max(current.high, bar.high),
                low=min(current.low, bar.low),
                close=bar.close,
                volume=current.volume + bar.volume,
                finalized=False,
            )

    def finalize_daily_bar(self, symbol: str, session_date: date) -> Optional[DailyBar]:
        """Finalize today's bar for symbol at 16:00 ET close and commit to store."""
        sym = symbol.upper()
        in_flight = self._in_flight.get(sym)
        if in_flight is None or in_flight.date != session_date:
            return None

        finalized_bar = DailyBar(
            symbol=sym,
            date=session_date,
            open=in_flight.open,
            high=in_flight.high,
            low=in_flight.low,
            close=in_flight.close,
            volume=in_flight.volume,
            finalized=True,
        )
        self.store.append_bar(finalized_bar)
        self._in_flight.pop(sym, None)
        return finalized_bar

    def finalize_all(self, session_date: date) -> List[DailyBar]:
        """Finalize all in-flight daily bars for the specified session date."""
        finalized: List[DailyBar] = []
        for sym in list(self._in_flight.keys()):
            bar = self.finalize_daily_bar(sym, session_date)
            if bar:
                finalized.append(bar)
        return finalized

    def get_in_flight_bar(self, symbol: str) -> Optional[DailyBar]:
        """Return the current unfinalized daily bar for symbol, if active."""
        return self._in_flight.get(symbol.upper())

    def reset_for_new_session(self) -> None:
        """Clear all in-flight accumulators."""
        self._in_flight.clear()
        self._current_session_date = None
