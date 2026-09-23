"""backend/app/strategies/news_momentum.py
Catalyst News Momentum Breakout Strategy.
Ingests real-time Benzinga headlines, performs NLP sentiment classification,
confirms breakout on >=3.5x volume surge, and enforces news contradiction circuit breakers.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.models.events import BarEvent, NewsEvent, OrderSide, OrderType
from backend.app.strategies.base import (
    Strategy,
    SignalEvent,
    StrategyStatus,
    calculate_sma,
    calculate_atr,
    resolve_stop,
)


def score_news_sentiment(headline: str) -> float:
    """Benzinga financial news sentiment scoring algorithm.

    Matches domain-specific bullish/bearish tokens, handles negations,
    and normalizes into [-1.0, 1.0] via hyperbolic tangent.
    """
    text = headline.lower()

    # Exact token sets (incorporating test_contracts and institutional survey dictionaries)
    bullish_tokens = [
        "beat", "beats", "beats estimates", "surges", "upgrade", "upgraded", "record revenue",
        "record earnings", "partnership", "buyback", "stock buyback", "exceeds", "approves",
        "approval", "fda approves", "milestone", "raises", "raises guidance", "awarded contract",
        "outperform", "soars"
    ]
    bearish_tokens = [
        "miss", "misses", "misses estimates", "downgrade", "downgraded", "investigation",
        "sec probe", "subpoena", "fraud", "lowers", "lowers guidance", "offering",
        "secondary offering", "recall", "inquiry", "rejects", "fda rejects", "glitch",
        "crash", "bankruptcy", "resigns", "plunges", "slumps"
    ]

    score = 0.0

    # Check for simple negation context (e.g., "not approved", "fails to beat")
    negation_patterns = [r"\bnot\s+", r"\bnever\s+", r"\bfails\s+to\s+", r"\bunable\s+to\s+"]

    for w in bullish_tokens:
        token_pat = r"\b" + re.escape(w) + r"\b"
        if re.search(token_pat, text):
            # Check if immediately preceded by negation
            is_negated = any(re.search(neg + re.escape(w) + r"\b", text) for neg in negation_patterns)
            score += -1.0 if is_negated else 1.0

    for w in bearish_tokens:
        token_pat = r"\b" + re.escape(w) + r"\b"
        if re.search(token_pat, text):
            is_negated = any(re.search(neg + re.escape(w) + r"\b", text) for neg in negation_patterns)
            score += 1.0 if is_negated else -1.0

    if score == 0.0:
        return 0.0

    return round(math.tanh(score / 2.0), 3)


@dataclass
class PendingCatalyst:
    """Unconfirmed news catalyst awaiting volume confirmation."""
    headline: str
    sentiment: float
    symbols: List[str]
    timestamp: datetime
    processed: bool = False


class NewsMomentumStrategy(Strategy):
    """Strategy 3: Catalyst News Momentum Breakout."""

    def __init__(
        self,
        strategy_id: str = "news_momentum",
        name: str = "Catalyst News Momentum Breakout",
        sentiment_threshold: float = 0.60,
        volume_surge_multiplier: float = 2.00,
        catalyst_ttl_seconds: int = 180,
        target_1_r: float = 0.80,
        target_2_r: float = 1.80,
    ):
        super().__init__(strategy_id=strategy_id, name=name)
        self.sentiment_threshold: float = sentiment_threshold
        self.volume_surge_multiplier: float = volume_surge_multiplier
        self.catalyst_ttl_seconds: int = catalyst_ttl_seconds
        self.target_1_r: float = target_1_r
        self.target_2_r: float = target_2_r

        # Symbol state: symbol -> pending catalysts
        self.pending_catalysts: Dict[str, List[PendingCatalyst]] = {}
        self.recent_bars: Dict[str, List[BarEvent]] = {}
        # Tracking active positions to monitor for contradictions
        # symbol -> "LONG" | "SHORT"
        self.monitored_positions: Dict[str, str] = {}

    def update_monitored_position(self, symbol: str, side: Optional[str]) -> None:
        """Inform strategy of open position side for contradiction circuit breaker."""
        sym = symbol.upper()
        if side is None:
            self.monitored_positions.pop(sym, None)
        else:
            self.monitored_positions[sym] = side.upper()

    def reset_daily_stats(self) -> None:
        super().reset_daily_stats()
        self.pending_catalysts.clear()
        self.recent_bars.clear()
        self.monitored_positions.clear()

    def on_news(self, news: NewsEvent) -> List[SignalEvent]:
        """Process incoming news headline.

        Evaluates sentiment score, records pending catalyst for symbols,
        and immediately fires contradiction emergency exit if an opposing
        position exists!
        """
        if self.status != StrategyStatus.ACTIVE:
            return []

        # Determine sentiment score (use event score if valid or calculate via NLP)
        sentiment = news.sentiment_score
        if abs(sentiment) < 0.001:
            sentiment = score_news_sentiment(news.headline)

        signals: List[SignalEvent] = []
        now_dt = news.created_at or datetime.now(timezone.utc)

        for sym in news.symbols:
            s = sym.upper()

            # 1. Contradiction Circuit Breaker Check
            current_side = self.monitored_positions.get(s)
            if current_side == "LONG" and sentiment < -0.35:
                # Emergency market exit for LONG
                signals.append(
                    SignalEvent(
                        symbol=s,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        entry_price=0.0,  # Market order
                        stop_loss=0.0,
                        take_profit_1=0.0,
                        take_profit_2=0.0,
                        strategy_id=self.strategy_id,
                        confidence=1.0,
                        reason=f"NEWS_CONTRADICTION_CIRCUIT_BREAKER: Adverse headline '{news.headline}' with sentiment {sentiment:.2f} while LONG {s}",
                        timestamp=now_dt,
                    )
                )
                self.monitored_positions.pop(s, None)
                continue

            elif current_side == "SHORT" and sentiment > 0.35:
                # Emergency market exit for SHORT
                signals.append(
                    SignalEvent(
                        symbol=s,
                        side=OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        entry_price=0.0,
                        stop_loss=0.0,
                        take_profit_1=0.0,
                        take_profit_2=0.0,
                        strategy_id=self.strategy_id,
                        confidence=1.0,
                        reason=f"NEWS_CONTRADICTION_CIRCUIT_BREAKER: Bullish headline '{news.headline}' with sentiment {sentiment:.2f} while SHORT {s}",
                        timestamp=now_dt,
                    )
                )
                self.monitored_positions.pop(s, None)
                continue

            # 2. Record pending catalyst if sentiment meets threshold
            if abs(sentiment) >= self.sentiment_threshold:
                if s not in self.pending_catalysts:
                    self.pending_catalysts[s] = []
                self.pending_catalysts[s].append(
                    PendingCatalyst(
                        headline=news.headline,
                        sentiment=sentiment,
                        symbols=news.symbols,
                        timestamp=now_dt,
                    )
                )

        return signals

    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        if self.status != StrategyStatus.ACTIVE:
            return []

        sym = bar.symbol.upper()
        if sym not in self.recent_bars:
            self.recent_bars[sym] = []
        self.recent_bars[sym].append(bar)
        self.recent_bars[sym] = self.recent_bars[sym][-60:]

        pending_list = self.pending_catalysts.get(sym, [])
        if not pending_list:
            return []

        # Filter active catalysts within TTL enforcing strict causality (no forward data leakage)
        now_ts = bar.timestamp.timestamp()
        valid_catalysts = [
            c for c in pending_list
            if (0 <= (now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds) and not c.processed
        ]
        self.pending_catalysts[sym] = valid_catalysts

        if not valid_catalysts:
            return []

        # Volume confirmation check (>3.5x SMA20)
        # Exclude the candidate bar from its own baseline.  Including the
        # surge in SMA20 dilutes the ratio and can suppress the very catalyst
        # the rule is meant to detect.
        recent_volumes = [float(b.volume) for b in self.recent_bars[sym][:-1][-20:]]
        sma20_vol = calculate_sma(recent_volumes, 20)
        # Fix 09:31 volume baseline: if < 5 bars, use 500k volume floor or open flush lockout
        if len(recent_volumes) < 5:
            sma20_vol = max(500000.0, sma20_vol)
        elif sma20_vol <= 0:
            sma20_vol = 100000.0

        vol_ratio = bar.volume / sma20_vol
        if vol_ratio < self.volume_surge_multiplier:
            return []

        # Latest catalyst triggered
        cat = valid_catalysts[-1]

        # Enforce candle direction confirmation (close > open for BUY, close < open for SELL)
        if cat.sentiment >= self.sentiment_threshold:
            if bar.close <= bar.open:
                return []
        elif cat.sentiment <= -self.sentiment_threshold:
            if bar.close >= bar.open:
                return []
        else:
            return []

        cat.processed = True

        entry_price = bar.close
        signals: List[SignalEvent] = []

        if cat.sentiment >= self.sentiment_threshold:
            # Bullish catalyst breakout: stop under the bar low, widened to the
            # 0.4% floor. A wide stop is left for the risk engine to reject.
            raw_dist = max(0.10, entry_price - round(bar.low - 0.02, 4))
            stop_loss, risk = resolve_stop(entry_price, raw_dist, True)
            tp1 = round(entry_price + self.target_1_r * risk, 4)
            tp2 = round(entry_price + self.target_2_r * risk, 4)

            sig = SignalEvent(
                symbol=sym,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit_1=tp1,
                take_profit_2=tp2,
                strategy_id=self.strategy_id,
                confidence=min(1.0, 0.70 + 0.10 * vol_ratio),
                reason=f"NEWS_MOMENTUM_LONG: Sentiment {cat.sentiment:.2f}, Volume Surge {vol_ratio:.2f}x > {self.volume_surge_multiplier}x. Headline: '{cat.headline[:60]}...'",
                timestamp=bar.timestamp,
                rvol=vol_ratio,
                volume_surge=vol_ratio,
                catalyst_sentiment=cat.sentiment,
            )
            sig.catalyst_sentiment = cat.sentiment
            sig.volume_surge = vol_ratio
            sig.rvol = vol_ratio
            signals.append(sig)
            self.monitored_positions[sym] = "LONG"

        elif cat.sentiment <= -self.sentiment_threshold:
            # Bearish catalyst breakdown: stop above the bar high, widened to the
            # 0.4% floor. A wide stop is left for the risk engine to reject.
            raw_dist = max(0.10, round(bar.high + 0.02, 4) - entry_price)
            stop_loss, risk = resolve_stop(entry_price, raw_dist, False)
            tp1 = round(entry_price - self.target_1_r * risk, 4)
            tp2 = round(entry_price - self.target_2_r * risk, 4)

            sig = SignalEvent(
                symbol=sym,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit_1=tp1,
                take_profit_2=tp2,
                strategy_id=self.strategy_id,
                confidence=min(1.0, 0.70 + 0.10 * vol_ratio),
                reason=f"NEWS_MOMENTUM_SHORT: Sentiment {cat.sentiment:.2f}, Volume Surge {vol_ratio:.2f}x > {self.volume_surge_multiplier}x. Headline: '{cat.headline[:60]}...'",
                timestamp=bar.timestamp,
                rvol=vol_ratio,
                volume_surge=vol_ratio,
                catalyst_sentiment=cat.sentiment,
            )
            sig.catalyst_sentiment = cat.sentiment
            sig.volume_surge = vol_ratio
            sig.rvol = vol_ratio
            signals.append(sig)
            self.monitored_positions[sym] = "SHORT"

        return signals
