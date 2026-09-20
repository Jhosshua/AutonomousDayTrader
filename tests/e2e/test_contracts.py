"""
Authoritative Test Contracts & Quantitative Oracles for AutonomousDayTrader.

Derived directly from ORIGINAL_REQUEST.md, PROJECT.md, and survey reports:
- Account ledger state machine ($50k, 4:1 BP, PnL)
- Institutional risk engine ($1,500 circuit breaker, 1-2% sizing)
- Dynamic bracket engine (1.5R target 1, breakeven ratchet, 2.5R target 2)
- 4-Phase EOD flattening (15:45, 15:50, 15:55, 15:58 ET)
- 4 Strategy logic oracles (ORB, VWAP, News Momentum, Mean Reversion)
- VIX volatility regime self-adaptation
- Time-of-day execution phases
- Mobile trading UI state contracts and momentum glow calculation
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# F4: $50,000 Paper Account Ledger
# ============================================================================

@dataclass
class Position:
    symbol: str
    qty: int
    entry_price: float
    current_price: float
    side: str = "LONG"
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    strategy_id: str = "default"

    @property
    def market_value(self) -> float:
        return abs(self.qty) * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        direction = 1.0 if self.side == "LONG" else -1.0
        return direction * self.qty * (self.current_price - self.entry_price)


@dataclass
class AccountLedger:
    starting_equity: float = 50000.00
    cash: float = 50000.00
    realized_pnl: float = 0.00
    positions: Dict[str, Position] = field(default_factory=dict)
    is_circuit_broken: bool = False
    max_daily_loss_limit: float = 1500.00

    @property
    def unrealized_pnl(self) -> float:
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + sum(pos.market_value for pos in self.positions.values())

    @property
    def daily_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def daily_drawdown(self) -> float:
        return max(0.0, -self.daily_pnl)

    @property
    def buying_power(self) -> float:
        # 4:1 intraday day trading buying power under FINRA Rule 4210
        margin_held = sum(pos.market_value for pos in self.positions.values())
        return max(0.0, (self.equity * 4.0) - margin_held)

    def execute_fill(self, symbol: str, side: str, qty: int, price: float, strategy_id: str = "default", stop_loss: float = 0.0, tp1: float = 0.0, tp2: float = 0.0) -> Optional[Position]:
        if self.is_circuit_broken:
            raise PermissionError("Trading halted: Circuit breaker is active.")

        cost = qty * price
        if side == "BUY":
            if cost > self.buying_power:
                raise ValueError(f"Insufficient buying power: requires ${cost:.2f}, available ${self.buying_power:.2f}")
            self.cash -= cost
            pos = Position(
                symbol=symbol,
                qty=qty,
                entry_price=price,
                current_price=price,
                side="LONG",
                stop_loss=stop_loss,
                take_profit_1=tp1,
                take_profit_2=tp2,
                strategy_id=strategy_id,
            )
            self.positions[symbol] = pos
            return pos
        elif side == "SELL":
            if symbol not in self.positions:
                raise ValueError(f"No open position in {symbol} to sell.")
            pos = self.positions[symbol]
            pnl = (price - pos.entry_price) * min(qty, pos.qty)
            self.realized_pnl += pnl
            self.cash += (min(qty, pos.qty) * price)
            if qty >= pos.qty:
                del self.positions[symbol]
            else:
                pos.qty -= qty
            self.check_circuit_breaker()
            return None
        return None

    def update_price(self, symbol: str, price: float) -> None:
        if symbol in self.positions:
            self.positions[symbol].current_price = price
        self.check_circuit_breaker()

    def check_circuit_breaker(self) -> bool:
        if self.daily_drawdown >= self.max_daily_loss_limit:
            self.is_circuit_broken = True
            return True
        return False

    def flatten_all(self) -> int:
        count = len(self.positions)
        for sym, pos in list(self.positions.items()):
            pnl = (pos.current_price - pos.entry_price) * pos.qty
            self.realized_pnl += pnl
            self.cash += pos.market_value
        self.positions.clear()
        return count


# ============================================================================
# F5 & F6: Risk Guardrails, Position Sizing, Dynamic Brackets
# ============================================================================

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
    if stop_distance <= 0.001:
        return 0

    # Risk budget in dollars (e.g. $500 on $50k)
    risk_dollars = equity * risk_pct * vix_multiplier
    shares_by_risk = math.floor(risk_dollars / stop_distance)

    # Capital allocation limit (max 25% of equity per stock)
    max_capital = equity * max_alloc_pct
    shares_by_capital = math.floor(max_capital / entry_price)

    return max(0, min(shares_by_risk, shares_by_capital))


def calculate_brackets(
    entry_price: float,
    stop_loss: float,
    side: str = "LONG",
) -> Tuple[float, float]:
    """Derive Target 1 (1.5R) and Target 2 (2.5R)."""
    r = abs(entry_price - stop_loss)
    direction = 1.0 if side == "LONG" else -1.0
    tp1 = round(entry_price + (direction * 1.5 * r), 2)
    tp2 = round(entry_price + (direction * 2.5 * r), 2)
    return tp1, tp2


# ============================================================================
# F7: 4-Phase EOD Flattening Protocol
# ============================================================================

def get_eod_phase(market_time_et: time) -> str:
    """Classify intraday closeout phase."""
    if market_time_et < time(15, 45):
        return "NORMAL_TRADING"
    elif market_time_et < time(15, 50):
        return "ENTRY_LOCKOUT"
    elif market_time_et < time(15, 55):
        return "ORDER_PURGE"
    elif market_time_et < time(15, 58):
        return "FORCE_FLATTEN"
    elif market_time_et <= time(16, 0):
        return "FLAT_AUDIT"
    return "MARKET_CLOSED"


# ============================================================================
# F8-F11: 4 Intraday Trading Strategies
# ============================================================================

def evaluate_orb_signal(
    bars_5m: List[Dict[str, Any]],
    current_bar: Dict[str, Any],
    rvol: float,
) -> Optional[str]:
    """Opening Range Breakout signal logic."""
    if len(bars_5m) < 1:
        return None
    range_high = max(b["h"] for b in bars_5m)
    range_low = min(b["l"] for b in bars_5m)

    # Breakout requires RVOL >= 1.8
    if rvol < 1.80:
        return None

    if current_bar["c"] > range_high:
        return "BUY"
    elif current_bar["c"] < range_low:
        return "SELL"
    return None


def calculate_anchored_vwap(bars: List[Dict[str, Any]]) -> Tuple[float, float]:
    """Calculate anchored VWAP and standard deviation."""
    total_pv = 0.0
    total_vol = 0.0
    for b in bars:
        typical_p = (b["h"] + b["l"] + b["c"]) / 3.0
        v = b["v"]
        total_pv += typical_p * v
        total_vol += v

    if total_vol <= 0:
        return 0.0, 0.0

    vwap = total_pv / total_vol
    variance = sum(b["v"] * (((b["h"] + b["l"] + b["c"]) / 3.0 - vwap) ** 2) for b in bars) / total_vol
    std_dev = math.sqrt(variance)
    return vwap, std_dev


def score_news_sentiment(headline: str) -> float:
    """Benzinga news sentiment classifier normalizing to [-1.0, 1.0]."""
    text = headline.lower()
    bullish_tokens = [
        "beat", "beats", "surges", "upgrade", "upgraded", "record revenue",
        "partnership", "buyback", "exceeds", "approves", "approval", "milestone", "raises"
    ]
    bearish_tokens = [
        "miss", "misses", "downgrade", "downgraded", "investigation", "subpoena",
        "fraud", "lowers", "offering", "recall", "inquiry", "rejects", "glitch", "crash"
    ]

    score = 0.0
    for w in bullish_tokens:
        if w in text:
            score += 1.0
    for w in bearish_tokens:
        if w in text:
            score -= 1.0

    # Normalization via tanh(score / 2.0)
    return round(math.tanh(score / 2.0), 3)


def evaluate_mean_reversion_zscore(
    prices: List[float],
) -> Tuple[float, float, float]:
    """Compute 20-period moving average, std dev, and price Z-score."""
    if len(prices) < 20:
        return 0.0, 0.0, 0.0
    window = prices[-20:]
    mean = sum(window) / 20.0
    variance = sum((p - mean) ** 2 for p in window) / 20.0
    std = math.sqrt(variance)
    if std <= 0.0001:
        return mean, 0.0, 0.0
    z_score = (prices[-1] - mean) / std
    return round(mean, 2), round(std, 2), round(z_score, 2)


# ============================================================================
# F12: Dynamic VIX Adaptation
# ============================================================================

def get_vix_regime(vix: float) -> Tuple[str, float, float]:
    """Map VIX spot to regime, sizing multiplier, and stop multiplier."""
    if vix < 15.0:
        return "LOW", 1.20, 0.85
    elif vix < 25.0:
        return "NORMAL", 1.00, 1.00
    elif vix < 35.0:
        return "ELEVATED", 0.70, 1.40
    else:
        return "CRISIS", 0.35, 2.00


# ============================================================================
# F13: Time-of-Day Dynamics
# ============================================================================

def get_time_of_day_phase(t_et: time) -> str:
    """Classify intraday market execution phase."""
    if t_et < time(9, 30):
        return "PRE_MARKET"
    elif t_et < time(10, 0):
        return "OPEN_VOLATILITY_FLUSH"
    elif t_et < time(11, 30):
        return "TREND_CONTINUATION"
    elif t_et < time(14, 0):
        return "MIDDAY_CHOP"
    elif t_et < time(15, 0):
        return "AFTERNOON_PUSH"
    elif t_et < time(15, 45):
        return "POWER_HOUR"
    elif t_et < time(16, 0):
        return "EOD_FLATTEN"
    return "POST_MARKET"


# ============================================================================
# F14-F17: Obsidian UI Aesthetic & WebSocket State Payload
# ============================================================================

def get_momentum_glow(daily_pnl: float, vix: float, is_halted: bool) -> Dict[str, Any]:
    """Derive dynamic momentum glow colors and intensity."""
    if is_halted:
        return {
            "primary": "rgba(255, 69, 58, 0.45)",
            "secondary": "rgba(180, 20, 20, 0.30)",
            "intensity": 0.45,
            "theme": "ALERT_HALTED"
        }
    if daily_pnl > 500.0:
        return {
            "primary": "rgba(48, 209, 88, 0.35)",
            "secondary": "rgba(100, 210, 255, 0.25)",
            "intensity": 0.35,
            "theme": "EMERALD_MOMENTUM"
        }
    elif daily_pnl > 0.0:
        return {
            "primary": "rgba(48, 209, 88, 0.20)",
            "secondary": "rgba(94, 92, 230, 0.20)",
            "intensity": 0.25,
            "theme": "SOFT_GAIN"
        }
    elif daily_pnl < -500.0:
        return {
            "primary": "rgba(255, 69, 58, 0.35)",
            "secondary": "rgba(255, 159, 10, 0.25)",
            "intensity": 0.35,
            "theme": "WARNING_DRAWDOWN"
        }
    elif daily_pnl < 0.0:
        return {
            "primary": "rgba(255, 69, 58, 0.20)",
            "secondary": "rgba(94, 92, 230, 0.15)",
            "intensity": 0.20,
            "theme": "MILD_DRAWDOWN"
        }
    return {
        "primary": "rgba(94, 92, 230, 0.22)",
        "secondary": "rgba(10, 132, 255, 0.18)",
        "intensity": 0.20,
        "theme": "NEUTRAL_STANDBY"
    }


def validate_ui_state_payload(payload: Dict[str, Any]) -> bool:
    """Validate backend-to-UI WebSocket payload against contract schema."""
    required_keys = ["type", "timestamp", "account", "market_context", "strategies"]
    if not all(k in payload for k in required_keys):
        return False

    acc = payload["account"]
    for field in ("equity", "cash", "buying_power", "daily_pnl", "is_circuit_broken"):
        if field not in acc:
            return False

    ctx = payload["market_context"]
    for field in ("vix", "vix_regime", "time_phase", "market_status"):
        if field not in ctx:
            return False

    return True
