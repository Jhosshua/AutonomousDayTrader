"""backend/app/core/tick_tape.py
Tick tape for Ride the Trend v2: per-symbol aggression (delta), tick velocity and
top-of-book imbalance from the SIP trade prints and NBBO quotes the bot already receives.

Layer 1 (tick data): every print is classified at the ask (buyer aggressive, +),
at the bid (seller aggressive, -) or by the tick rule when it prints inside the
spread, in a locked or crossed market, or when no quote at or before the print is
fresh. Layer 2 (book): the feed carries top of book only, so imbalance is bid size
versus ask size at the inside; a full-depth provider can replace `book_imbalance`
without touching the strategy. Layer 3 (timestamps): velocity is measured on the
prints' own nanosecond exchange timestamps.

Storage is per-second buckets (aggregates), so memory stays small at any print rate.
Queries are half-open on whole seconds, [t0, t1): a query for a bar passes the bar's
start and end, and never sees a print or quote stamped at or after t1. A quote is only
used to classify a print stamped at or after it (never a later quote), and a quote older
than the current one is ignored.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
from typing import Any, Dict, Optional

NS = 1_000_000_000


@dataclass
class SecondBucket:
    first_ns: int = 0
    last_ns: int = 0
    first_price: float = 0.0
    last_price: float = 0.0
    buy_vol: int = 0
    sell_vol: int = 0
    unknown_vol: int = 0
    n_trades: int = 0
    # quotes folded into this second
    quote_n: int = 0
    bid_size_sum: float = 0.0
    ask_size_sum: float = 0.0
    last_quote_ns: int = 0
    last_bid: float = 0.0
    last_ask: float = 0.0
    last_bid_size: int = 0
    last_ask_size: int = 0


@dataclass
class QuoteState:
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    ts_ns: int


def _finite_positive(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in values)


class TickTape:
    """Aggregates prints and quotes into per-second buckets per symbol."""

    def __init__(self, keep_seconds: int = 3 * 3600, stale_quote_ns: int = 2 * NS) -> None:
        self.keep_seconds = int(keep_seconds)
        self.stale_quote_ns = int(stale_quote_ns)
        self._buckets: Dict[str, "OrderedDict[int, SecondBucket]"] = {}
        self._quotes: Dict[str, QuoteState] = {}
        self._last_price: Dict[str, float] = {}
        self._last_side: Dict[str, int] = {}
        self._watermark: Dict[str, int] = {}  # newest event ns seen per symbol
        self.trades_seen = 0
        self.quotes_seen = 0
        self.rejected = 0

    # ------------------------------------------------------------------ ingest
    def _bucket(self, symbol: str, ts_ns: int) -> SecondBucket:
        sec = ts_ns // NS
        series = self._buckets.setdefault(symbol, OrderedDict())
        b = series.get(sec)
        if b is None:
            b = SecondBucket()
            series[sec] = b
            if len(series) > 1 and next(reversed(series)) != sec:
                # a late second: keep the dict ordered by second
                series = OrderedDict(sorted(series.items()))
                self._buckets[symbol] = series
        wm = max(self._watermark.get(symbol, 0), ts_ns)
        self._watermark[symbol] = wm
        cutoff = wm // NS - self.keep_seconds
        while series:
            oldest = next(iter(series))
            if oldest < cutoff:
                series.popitem(last=False)
            else:
                break
        return b

    def on_quote(self, symbol: str, bid: float, ask: float, bid_size: int, ask_size: int, ts_ns: int) -> bool:
        """Fold one NBBO quote. Returns False when the quote is unusable or older than the current one."""
        if not _finite_positive(bid, ask) or not isinstance(ts_ns, int) or ts_ns <= 0:
            self.rejected += 1
            return False
        if isinstance(bid_size, bool) or isinstance(ask_size, bool) or not isinstance(bid_size, int) \
                or not isinstance(ask_size, int) or bid_size < 0 or ask_size < 0:
            self.rejected += 1
            return False
        sym = symbol.upper()
        cur = self._quotes.get(sym)
        if cur is not None and ts_ns < cur.ts_ns:
            self.rejected += 1
            return False  # a late quote never replaces a newer one
        if ask < bid:
            # crossed market: the previous quote is no longer trustworthy either
            self._quotes.pop(sym, None)
            self.rejected += 1
            return False
        self.quotes_seen += 1
        self._quotes[sym] = QuoteState(bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size, ts_ns=ts_ns)
        b = self._bucket(sym, ts_ns)
        b.quote_n += 1
        b.bid_size_sum += float(bid_size)
        b.ask_size_sum += float(ask_size)
        if ts_ns >= b.last_quote_ns:
            b.last_quote_ns, b.last_bid, b.last_ask = ts_ns, bid, ask
            b.last_bid_size, b.last_ask_size = bid_size, ask_size
        return True

    def on_trade(self, symbol: str, price: float, size: int, ts_ns: int) -> int:
        """Classify and store one print. Returns the side (+1 buy, -1 sell, 0 unknown).

        Classification uses only a quote stamped at or before the print and no older than
        `stale_quote_ns`; a locked market (bid == ask) or a print inside the spread falls
        back to the tick rule against the previous print.
        """
        if not _finite_positive(price) or isinstance(size, bool) or not isinstance(size, int) or size <= 0 \
                or not isinstance(ts_ns, int) or ts_ns <= 0:
            self.rejected += 1
            return 0
        sym = symbol.upper()
        self.trades_seen += 1
        q = self._quotes.get(sym)
        side = 0
        if q is not None and 0 <= ts_ns - q.ts_ns <= self.stale_quote_ns and q.ask > q.bid:
            if price >= q.ask:
                side = 1
            elif price <= q.bid:
                side = -1
        if side == 0:
            last = self._last_price.get(sym)
            if last is not None:
                if price > last:
                    side = 1
                elif price < last:
                    side = -1
                else:
                    side = self._last_side.get(sym, 0)
        b = self._bucket(sym, ts_ns)
        if b.n_trades == 0:
            b.first_ns, b.first_price, b.last_ns, b.last_price = ts_ns, price, ts_ns, price
        else:
            if ts_ns < b.first_ns:
                b.first_ns, b.first_price = ts_ns, price
            if ts_ns >= b.last_ns:
                b.last_ns, b.last_price = ts_ns, price
        b.n_trades += 1
        if side > 0:
            b.buy_vol += size
        elif side < 0:
            b.sell_vol += size
        else:
            b.unknown_vol += size
        self._last_price[sym] = price
        if side != 0:
            self._last_side[sym] = side
        return side

    # ------------------------------------------------------------------ queries
    def _seconds(self, symbol: str, t0_ns: int, t1_ns: int):
        """Buckets for whole seconds in [t0, t1): floor(t0) .. ceil(t1) - 1."""
        series = self._buckets.get(symbol.upper())
        if not series or t1_ns <= t0_ns:
            return []
        s0 = t0_ns // NS
        s1 = -(-t1_ns // NS)  # ceil
        return [b for sec, b in series.items() if s0 <= sec < s1]

    def delta(self, symbol: str, t0_ns: int, t1_ns: int, min_classified_share: float = 0.5) -> Optional[Dict[str, Any]]:
        """Aggression over [t0, t1): buy volume, sell volume, net ratio in [-1, 1].

        Unavailable when there are no prints or fewer than `min_classified_share` of the
        volume could be classified."""
        buckets = [b for b in self._seconds(symbol, t0_ns, t1_ns) if b.n_trades > 0]
        if not buckets:
            return None
        buy = sum(b.buy_vol for b in buckets)
        sell = sum(b.sell_vol for b in buckets)
        unknown = sum(b.unknown_vol for b in buckets)
        n = sum(b.n_trades for b in buckets)
        total = buy + sell + unknown
        if n == 0 or buy + sell == 0 or total <= 0:
            return None
        share = (buy + sell) / float(total)
        if share < min_classified_share:
            return None
        return {
            "buy_vol": buy, "sell_vol": sell, "unknown_vol": unknown, "n_trades": n,
            "delta": buy - sell, "delta_ratio": (buy - sell) / float(buy + sell),
            "classified_share": share,
        }

    def velocity(self, symbol: str, t1_ns: int, window_s: int = 60, min_trades: int = 5,
                 min_coverage: float = 0.5, max_last_age_s: float = 10.0) -> Optional[Dict[str, Any]]:
        """Signed price change per second over prints in [t1 - window, t1).

        Unavailable with fewer than `min_trades` prints, when the prints span less than
        `min_coverage` of the window, or when the last print is older than `max_last_age_s`."""
        t0_ns = t1_ns - int(window_s) * NS
        buckets = [b for b in self._seconds(symbol, t0_ns, t1_ns) if b.n_trades > 0
                   and b.first_ns < t1_ns and b.last_ns >= t0_ns]
        if not buckets:
            return None
        n = sum(b.n_trades for b in buckets)
        if n < min_trades:
            return None
        first = min(buckets, key=lambda b: b.first_ns)
        last = max(buckets, key=lambda b: b.last_ns)
        elapsed = (last.last_ns - first.first_ns) / NS
        if elapsed <= 0 or elapsed < min_coverage * window_s:
            return None
        if (t1_ns - last.last_ns) / NS > max_last_age_s:
            return None
        move = last.last_price - first.first_price
        per_second = move / elapsed
        if not math.isfinite(per_second):
            return None
        return {
            "price_change": move, "elapsed_s": elapsed, "per_second": per_second,
            "n_trades": n, "first_ns": first.first_ns, "last_ns": last.last_ns,
        }

    def book_imbalance(self, symbol: str, t1_ns: int, window_s: int = 30,
                       max_last_age_s: float = 5.0) -> Optional[Dict[str, Any]]:
        """Top-of-book imbalance (bid size - ask size) / (bid + ask), quote-weighted over
        [t1 - window, t1). Unavailable without quotes, or when the newest quote in the
        window is older than `max_last_age_s`. Quote details are as of the query cutoff."""
        t0_ns = t1_ns - int(window_s) * NS
        buckets = [b for b in self._seconds(symbol, t0_ns, t1_ns) if b.quote_n > 0 and b.last_quote_ns < t1_ns]
        if not buckets:
            return None
        bid = sum(b.bid_size_sum for b in buckets)
        ask = sum(b.ask_size_sum for b in buckets)
        n = sum(b.quote_n for b in buckets)
        if n == 0 or bid + ask <= 0:
            return None
        newest = max(buckets, key=lambda b: b.last_quote_ns)
        if (t1_ns - newest.last_quote_ns) / NS > max_last_age_s:
            return None
        imb = (bid - ask) / (bid + ask)
        if not math.isfinite(imb):
            return None
        return {
            "imbalance": imb, "quotes": n,
            "bid": newest.last_bid, "ask": newest.last_ask,
            "bid_size": newest.last_bid_size, "ask_size": newest.last_ask_size,
            "spread": newest.last_ask - newest.last_bid, "quote_ts_ns": newest.last_quote_ns,
        }

    def coverage(self, symbol: str) -> Dict[str, Any]:
        series = self._buckets.get(symbol.upper())
        if not series:
            return {"seconds": 0, "trades": 0, "quotes": 0, "first_ns": None, "last_ns": None}
        trades = quotes = 0
        for b in series.values():
            trades += b.n_trades
            quotes += b.quote_n
        return {
            "seconds": len(series), "trades": trades, "quotes": quotes,
            "first_ns": min(sec for sec in series) * NS,
            "last_ns": self._watermark.get(symbol.upper()),
        }

    def health(self) -> Dict[str, Any]:
        return {
            "symbols": sorted(self._buckets.keys()),
            "trades_seen": self.trades_seen,
            "quotes_seen": self.quotes_seen,
            "rejected": self.rejected,
            "per_symbol": {s: self.coverage(s) for s in sorted(self._buckets.keys())},
        }

    def reset(self) -> None:
        self._buckets.clear()
        self._quotes.clear()
        self._last_price.clear()
        self._last_side.clear()
        self._watermark.clear()
        self.trades_seen = 0
        self.quotes_seen = 0
        self.rejected = 0
