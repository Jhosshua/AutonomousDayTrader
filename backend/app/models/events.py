"""backend/app/models/events.py
Typed event models for market data feeds, news catalysts, VIX prints, orders, fills, and account telemetry.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any


class EventType(str, Enum):
    BAR = "BAR"
    QUOTE = "QUOTE"
    TRADE = "TRADE"
    NEWS = "NEWS"
    VIX = "VIX"
    RELAY_STATUS = "RELAY_STATUS"
    ORDER = "ORDER"
    FILL = "FILL"
    ACCOUNT_STATE = "ACCOUNT_STATE"


class VixRegime(str, Enum):
    LOW = "LOW"             # VIX < 15.0: Sizing 1.2x, tight stops
    NORMAL = "NORMAL"       # 15.0 <= VIX < 22.0: Sizing 1.0x, standard stops
    ELEVATED = "ELEVATED"   # 22.0 <= VIX < 30.0: Sizing 0.6x, wider stops
    CRISIS = "CRISIS"       # VIX >= 30.0: Sizing 0.25x, freeze breakout entries


class CatalystCategory(str, Enum):
    EARNINGS_BEAT = "EARNINGS_BEAT"
    EARNINGS_MISS = "EARNINGS_MISS"
    GUIDANCE_RAISE = "GUIDANCE_RAISE"
    GUIDANCE_CUT = "GUIDANCE_CUT"
    FDA_APPROVAL = "FDA_APPROVAL"
    FDA_REJECTION = "FDA_REJECTION"
    PARTNERSHIP_CONTRACT = "PARTNERSHIP_CONTRACT"
    LEGAL_INVESTIGATION = "LEGAL_INVESTIGATION"
    ANALYST_UPGRADE = "ANALYST_UPGRADE"
    ANALYST_DOWNGRADE = "ANALYST_DOWNGRADE"
    GENERAL_CATALYST = "GENERAL_CATALYST"
    NEUTRAL = "NEUTRAL"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderState(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class BarEvent:
    """1-minute aggregate OHLCV bar (AlpacaRelay message T: 'b')."""
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime
    num_trades: Optional[int] = None
    vwap: Optional[float] = None

    @classmethod
    def from_relay_dict(cls, data: Dict[str, Any]) -> "BarEvent":
        ts_str = str(data["t"]).replace("Z", "+00:00")
        ts = datetime.fromisoformat(ts_str) if "+" in ts_str or "-" in ts_str[10:] else datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return cls(
            symbol=data["S"].upper(),
            open=float(data["o"]),
            high=float(data["h"]),
            low=float(data["l"]),
            close=float(data["c"]),
            volume=int(data["v"]),
            timestamp=ts,
            num_trades=data.get("n"),
            vwap=float(data["vw"]) if data.get("vw") is not None else None,
        )


@dataclass(frozen=True)
class QuoteEvent:
    """Top-of-book NBBO quote (AlpacaRelay message T: 'q')."""
    symbol: str
    bid_price: float
    bid_size: int
    bid_exchange: str
    ask_price: float
    ask_size: int
    ask_exchange: str
    timestamp: datetime
    conditions: List[str] = field(default_factory=list)
    tape: str = "C"

    @property
    def mid_price(self) -> float:
        return round((self.bid_price + self.ask_price) / 2.0, 4)

    @property
    def spread(self) -> float:
        return round(self.ask_price - self.bid_price, 4)

    @classmethod
    def from_relay_dict(cls, data: Dict[str, Any]) -> "QuoteEvent":
        ts_str = str(data["t"]).replace("Z", "+00:00")
        ts = datetime.fromisoformat(ts_str) if "+" in ts_str or "-" in ts_str[10:] else datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return cls(
            symbol=data["S"].upper(),
            bid_price=float(data["bp"]),
            bid_size=int(data["bs"]),
            bid_exchange=data.get("bx", "V"),
            ask_price=float(data["ap"]),
            ask_size=int(data["as"]),
            ask_exchange=data.get("ax", "V"),
            timestamp=ts,
            conditions=data.get("c", []),
            tape=data.get("z", "C"),
        )


@dataclass(frozen=True)
class TradeEvent:
    """Executed trade print (AlpacaRelay message T: 't')."""
    symbol: str
    trade_id: int
    price: float
    size: int
    exchange: str
    timestamp: datetime
    conditions: List[str] = field(default_factory=list)
    tape: str = "C"

    @classmethod
    def from_relay_dict(cls, data: Dict[str, Any]) -> "TradeEvent":
        ts_str = str(data["t"]).replace("Z", "+00:00")
        ts = datetime.fromisoformat(ts_str) if "+" in ts_str or "-" in ts_str[10:] else datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return cls(
            symbol=data["S"].upper(),
            trade_id=int(data.get("i", 0)),
            price=float(data["p"]),
            size=int(data["s"]),
            exchange=data.get("x", "V"),
            timestamp=ts,
            conditions=data.get("c", []),
            tape=data.get("z", "C"),
        )


@dataclass(frozen=True)
class NewsEvent:
    """Benzinga real-time news article (AlpacaRelay message T: 'n') enriched with sentiment."""
    article_id: int
    headline: str
    summary: str
    symbols: List[str]
    source: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    url: Optional[str] = None
    content: Optional[str] = None
    sentiment_score: float = 0.0          # Normalized in [-1.0, 1.0]
    sentiment_confidence: float = 0.0     # Confidence in [0.0, 1.0]
    catalyst_category: CatalystCategory = CatalystCategory.NEUTRAL

    @property
    def is_actionable_bullish(self) -> bool:
        return self.sentiment_score >= 0.60 and self.sentiment_confidence >= 0.50

    @property
    def is_actionable_bearish(self) -> bool:
        return self.sentiment_score <= -0.60 and self.sentiment_confidence >= 0.50


@dataclass(frozen=True)
class VixPrint:
    """dxFeed spot VIX print received from GET /vix."""
    value: float
    asof: datetime
    received_at: datetime
    age_s: float
    state: str                          # ready, stale, unavailable, unconfigured
    upstream: str                       # connected, down
    regime: VixRegime                   # LOW, NORMAL, ELEVATED, CRISIS
    sizing_multiplier: float            # 0.25 to 1.2
    is_stale: bool = False
    is_fallback: bool = False


@dataclass(frozen=True)
class RelayStatusEvent:
    """Upstream relay connectivity status change notification."""
    feed_type: str                      # stock, news, vix
    status: str                         # connected, disconnected, reconnecting, error
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class OrderEvent:
    """Dispatched order lifecycle state event."""
    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    strategy_id: str = "MANUAL"
    status: OrderState = OrderState.CREATED
    filled_qty: int = 0
    remaining_qty: int = 0
    avg_fill_price: float = 0.0
    reject_reason: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class FillEvent:
    """Order fill execution event."""
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    fee: float
    slippage: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PositionState:
    """Read-only position snapshot."""
    symbol: str
    side: str                            # LONG or SHORT
    shares: int
    avg_entry_price: float
    market_price: float
    market_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float
    fees_paid: float
    opened_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AccountState:
    """Read-only account snapshot."""
    cash: float
    equity: float
    buying_power: float
    maintenance_margin: float
    margin_excess: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    daily_drawdown_dollars: float
    daily_drawdown_pct: float
    is_circuit_broken: bool
    status: str
    positions: Dict[str, PositionState]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
