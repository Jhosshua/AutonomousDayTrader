"""backend/app/core/tick_tape.py
Tick tape for Ride the Trend v2: the raw print stream (Layer 1), real-time signed flow (Layer 3)
and inside-book imbalance (Layer 2, NBBO only) from the SIP prints and NBBO quotes the bot receives.

Storage: one `SecondBucket` per symbol per exchange second. Each bucket holds that second's eligible
prints in packed `array.array` columns (timestamp, price, size, side, flags) plus a small set of trade ids
for de-duplication and the second's quote aggregates. Retention and the print cap evict WHOLE seconds from
the front (O(1)), never individual prints, so no query can be split across an eviction. Late prints go into
their own second's arrays (rare; that bucket is re-sorted on the next query).

Coverage: every query reports `complete`, true only when (a) the window starts at or after the symbol's
first event of the session AND after the last evicted second, (b) the symbol's feed watermark (newest quote
or print) has reached the window end minus a small slack, and (c) no feed-outage interval overlaps the
window. A gate must refuse an incomplete window.

Classification (Layer 1): a print is classified only against a quote stamped at or before it (time-bounded
quote history), no older than `stale_quote_ns`: at or above the ask = buyer aggressive (+1), at or below the
bid = seller aggressive (-1); otherwise the tick rule against the chronologically previous eligible print
(+2/-2), else unknown (0). Quote-classified, tick-inferred and unknown volume are reported separately. A
print that arrives late (older than the newest print already seen) is classified against its chronological
predecessor and never updates the live tick state.

Book (Layer 2): inside bid size versus ask size, weighted by the seconds each quote state was in force
(carried forward up to `book_stale_s`), with the covered share of the window reported.

All windows are half-open [t0, t1) on nanoseconds. Nothing at or after t1 is ever used.
"""
from __future__ import annotations

from array import array
from bisect import bisect_left
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
import math
import time as _time
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

NS = 1_000_000_000
ET = ZoneInfo("America/New_York")
SESSION_OPEN, SESSION_CLOSE = dtime(9, 30), dtime(16, 0)
TRADE_TAIL_NS = 60 * NS              # trade delivery must have reached the cutoff minus this

# SIP sale conditions whose prints do not describe regular-way flow: counted, never stored.
INELIGIBLE_CONDITIONS = frozenset({"U", "Z", "4", "B", "W", "M", "Q", "C", "N", "R", "P", "T", "9"})
ODD_LOT = "I"
FLAG_ODD_LOT = 2

SIDE_QUOTE_BUY, SIDE_QUOTE_SELL = 1, -1
SIDE_TICK_BUY, SIDE_TICK_SELL = 2, -2
SIDE_UNKNOWN = 0

MAX_SIZE = 2 ** 62
MAX_TRADE_ID = (1 << 56) - 1
MAX_TS_NS = 2 ** 63 - 1
FUTURE_TOLERANCE_NS = 3600 * NS      # an event stamped more than an hour past the watermark is not plausible
LATE_CLASSIFY_NS = 30 * NS           # prints later than this behind the watermark are stored, never tick-classified


@dataclass
class SecondBucket:
    sec: int
    ts: array = field(default_factory=lambda: array("q"))
    price: array = field(default_factory=lambda: array("d"))
    size: array = field(default_factory=lambda: array("q"))
    side: array = field(default_factory=lambda: array("b"))
    flags: array = field(default_factory=lambda: array("b"))
    ids: set = field(default_factory=set)
    sorted: bool = True
    n_trades: int = 0
    buy_vol: int = 0
    sell_vol: int = 0
    unknown_vol: int = 0
    quote_n: int = 0
    last_quote_ns: int = 0
    last_bid: float = 0.0
    last_ask: float = 0.0
    last_bid_size: int = 0
    last_ask_size: int = 0
    last_spread_bps: float = 0.0

    def ensure_sorted(self) -> None:
        if self.sorted:
            return
        order = sorted(range(len(self.ts)), key=lambda k: self.ts[k])
        # build every column first, commit all at once: a failure part-way leaves the bucket untouched
        ts = array("q", [self.ts[k] for k in order])
        price = array("d", [self.price[k] for k in order])
        size = array("q", [self.size[k] for k in order])
        side = array("b", [self.side[k] for k in order])
        flags = array("b", [self.flags[k] for k in order])
        self.ts, self.price, self.size, self.side, self.flags = ts, price, size, side, flags
        self.sorted = True


@dataclass
class QuoteState:
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    ts_ns: int


@dataclass
class SymbolTape:
    buckets: "OrderedDict[int, SecondBucket]" = field(default_factory=OrderedDict)
    raw_count: int = 0
    watermark_ns: int = 0            # newest event (quote or print) seen
    session_start_ns: Optional[int] = None
    evicted_boundary_ns: int = 0     # end of the newest evicted second; windows before it are incomplete
    evicted_by_cap: int = 0
    evicted_by_age: int = 0
    last_print_ns: int = 0
    last_price: Optional[float] = None
    last_tick_dir: int = 0           # +1 / -1 from the last eligible price CHANGE (tick rule state)
    quote: Optional[QuoteState] = None
    quote_hist: Deque[Tuple[int, float, float]] = field(default_factory=deque)
    quote_ts_max: int = 0            # monotonic, independent of validity
    # session cumulative delta ("pseudo-L2"): per-minute aggregates keyed by exchange minute, never
    # evicted within the session, queried with a decision-time cutoff. `since_ns` = start of continuous
    # feed (session start or the last reconnect); `session` = ET date the minutes belong to.
    since_ns: Optional[int] = None   # start of the current continuous-feed generation (regular session)
    session: Optional[str] = None
    session_first_ns: Optional[int] = None   # first regular-session event of the day
    minutes: Dict[int, List[int]] = field(default_factory=dict)   # minute -> [buy_q, sell_q, buy_t, sell_t, unknown, n]
    quote_invalid_ns: int = 0        # a crossed quote at this stamp: nothing is classified until a valid quote arrives
    trade_watermark_ns: int = 0      # newest eligible print (trade delivery progress)


def _median(values: List[float]) -> float:
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _finite_positive(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in values)


class TickTape:
    def __init__(self, keep_seconds: int = 3600, stale_quote_ns: int = 2 * NS, raw_cap: int = 1_000_000,
                 quote_history_s: float = 10.0, right_slack_s: float = 5.0, book_stale_s: float = 30.0) -> None:
        self.keep_seconds = int(keep_seconds)
        self.stale_quote_ns = int(stale_quote_ns)
        self.raw_cap = int(raw_cap)
        self.quote_history_ns = int(quote_history_s * NS)
        self.right_slack_ns = int(right_slack_s * NS)
        self.book_stale_s = float(book_stale_s)
        self._syms: Dict[str, SymbolTape] = {}
        self._outages: List[List[Optional[int]]] = []   # closed intervals [start_ns, end_ns] and point marks
        self._open_outage_ns: Optional[int] = None      # start of the outage in progress, if any
        self.trades_seen = 0
        self.quotes_seen = 0
        self.rejected = 0
        self.duplicates = 0
        self.ineligible = 0

    # ------------------------------------------------------------------ feed marks
    def note_disconnect(self, ts_ns: int) -> None:
        if self._open_outage_ns is None:
            self._open_outage_ns = int(ts_ns)

    def note_reconnect(self, ts_ns: int) -> None:
        if self._open_outage_ns is not None:
            self._outages.append([self._open_outage_ns, int(ts_ns)])
            self._open_outage_ns = None
            del self._outages[:-500]
        # a new feed generation starts AT the reconnect: only minutes starting after it count
        for t in self._syms.values():
            t.since_ns = int(ts_ns)

    def note_gap(self, ts_ns: int) -> None:
        """A point mark (unknown duration): treated as a zero-length outage; the session totals restart."""
        self._outages.append([int(ts_ns), int(ts_ns)])
        del self._outages[:-500]
        for t in self._syms.values():
            t.since_ns = int(ts_ns)

    def _outage_in(self, t0_ns: int, t1_ns: int) -> bool:
        if self._open_outage_ns is not None and self._open_outage_ns < t1_ns:
            return True
        for start, end in self._outages:
            if start < t1_ns and end >= t0_ns:
                return True
        return False

    # ------------------------------------------------------------------ ingest helpers
    def _tape(self, sym: str) -> SymbolTape:
        t = self._syms.get(sym)
        if t is None:
            t = SymbolTape()
            self._syms[sym] = t
        return t

    def _admissible(self, t: SymbolTape, ts_ns: int) -> bool:
        """Storage range and plausibility, checked BEFORE any state changes: inside the signed 64-bit
        range, not in the future (wall clock plus a minute, so a long outage never freezes ingestion),
        not older than the retention window, and never inside an already-evicted second (a retransmitted
        print from there could not be de-duplicated)."""
        if not isinstance(ts_ns, int) or isinstance(ts_ns, bool) or ts_ns <= 0 or ts_ns > MAX_TS_NS:
            return False
        if ts_ns > _time.time_ns() + 24 * 3600 * NS:
            return False  # more than a day past the wall clock is not a market event
        if t.watermark_ns and ts_ns // NS < t.watermark_ns // NS - self.keep_seconds:
            return False
        if ts_ns < t.evicted_boundary_ns:
            return False
        return True

    def _bucket(self, t: SymbolTape, ts_ns: int) -> SecondBucket:
        sec = ts_ns // NS
        b = t.buckets.get(sec)
        if b is None:
            b = SecondBucket(sec=sec)
            late = bool(t.buckets) and sec < next(reversed(t.buckets))
            t.buckets[sec] = b
            if late:
                t.buckets = OrderedDict(sorted(t.buckets.items()))
        self._advance(t, ts_ns)
        assert sec in t.buckets  # admissibility guarantees the bucket survived its own eviction pass
        return b

    def _advance(self, t: SymbolTape, ts_ns: int) -> None:
        if t.session_start_ns is None:
            t.session_start_ns = ts_ns
        if ts_ns > t.watermark_ns:
            t.watermark_ns = ts_ns
        cutoff = t.watermark_ns // NS - self.keep_seconds
        while t.buckets:
            oldest = next(iter(t.buckets))
            if oldest < cutoff:
                self._evict_oldest(t, "age")
            else:
                break
        while t.raw_count > self.raw_cap and t.buckets:
            self._evict_oldest(t, "cap")

    def _evict_oldest(self, t: SymbolTape, why: str) -> None:
        sec, b = t.buckets.popitem(last=False)
        t.raw_count -= b.n_trades
        t.evicted_boundary_ns = max(t.evicted_boundary_ns, (sec + 1) * NS)
        if why == "cap":
            t.evicted_by_cap += b.n_trades
        else:
            t.evicted_by_age += b.n_trades

    def on_quote(self, symbol: str, bid: float, ask: float, bid_size: int, ask_size: int, ts_ns: int) -> bool:
        if not _finite_positive(bid, ask) or not isinstance(ts_ns, int) or isinstance(ts_ns, bool) or ts_ns <= 0:
            self.rejected += 1
            return False
        if isinstance(bid_size, bool) or isinstance(ask_size, bool) or not isinstance(bid_size, int) \
                or not isinstance(ask_size, int) or bid_size < 0 or ask_size < 0 or bid_size > MAX_SIZE or ask_size > MAX_SIZE:
            self.rejected += 1
            return False
        sym = symbol.upper()
        t = self._tape(sym)
        if not self._admissible(t, ts_ns) or ts_ns < t.quote_ts_max:
            self.rejected += 1
            return False  # implausible, out of retention, or a late quote (never replaces a newer one)
        t.quote_ts_max = ts_ns
        if ask < bid:
            # crossed: the inside is unusable until a good quote arrives, for classification too
            t.quote = None
            t.quote_invalid_ns = ts_ns
            t.quote_hist.clear()
            self.rejected += 1
            self._advance(t, ts_ns)
            return False
        self.quotes_seen += 1
        b = self._bucket(t, ts_ns)
        if ask == bid:
            # locked market: feed progress only; no classification quote, no spread observation
            self._touch_session(t, ts_ns)
            return True
        t.quote = QuoteState(bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size, ts_ns=ts_ns)
        t.quote_hist.append((ts_ns, bid, ask))
        while t.quote_hist and ts_ns - t.quote_hist[0][0] > self.quote_history_ns:
            t.quote_hist.popleft()
        b.quote_n += 1
        if ts_ns >= b.last_quote_ns:
            b.last_quote_ns, b.last_bid, b.last_ask = ts_ns, bid, ask
            b.last_bid_size, b.last_ask_size = bid_size, ask_size
            b.last_spread_bps = (ask - bid) / ((ask + bid) / 2.0) * 1e4
        self._touch_session(t, ts_ns)
        return True

    def _touch_session(self, t: SymbolTape, ts_ns: int) -> bool:
        """Regular-session bookkeeping for the cumulative delta. Returns False when the event is outside
        the regular session or belongs to an earlier ET date (those never touch the session state)."""
        et = datetime.fromtimestamp(ts_ns / NS, tz=timezone.utc).astimezone(ET)
        if not (SESSION_OPEN <= et.time() < SESSION_CLOSE):
            return False
        day = et.date().isoformat()
        if t.session is not None and day < t.session:
            return False
        if t.session != day:
            t.session = day
            t.minutes = {}
            t.session_first_ns = ts_ns
            t.since_ns = ts_ns
        if t.since_ns is None:
            t.since_ns = ts_ns
        return True

    def _quote_for(self, t: SymbolTape, ts_ns: int) -> Optional[Tuple[float, float]]:
        for q_ts, bid, ask in reversed(t.quote_hist):
            if q_ts <= ts_ns:
                if q_ts < t.quote_invalid_ns <= ts_ns:
                    return None  # a crossed quote sat between this quote and the print
                if ts_ns - q_ts <= self.stale_quote_ns and ask > bid:
                    return bid, ask
                return None
        return None

    def _predecessor_price(self, t: SymbolTape, ts_ns: int) -> Optional[float]:
        """Price of the last eligible print stamped before ts (for late prints)."""
        sec = ts_ns // NS
        for s in reversed(list(t.buckets.keys())):
            if s > sec:
                continue
            b = t.buckets[s]
            if b.n_trades == 0:
                continue
            b.ensure_sorted()
            k = bisect_left(b.ts, ts_ns) - 1
            if k >= 0:
                return b.price[k]
        return None

    def on_trade(self, symbol: str, price: float, size: int, ts_ns: int, *, trade_id: int = 0,
                 exchange: str = "", conditions: Optional[Iterable[str]] = None) -> int:
        """Classify and store one print. Returns the side code (see module docstring)."""
        if not _finite_positive(price) or isinstance(size, bool) or not isinstance(size, int) or size <= 0 \
                or size > MAX_SIZE or not isinstance(ts_ns, int) or isinstance(ts_ns, bool) or ts_ns <= 0:
            self.rejected += 1
            return SIDE_UNKNOWN
        try:
            tid_raw = int(trade_id or 0)
        except (TypeError, ValueError):
            tid_raw = 0
        if tid_raw < 0 or tid_raw > MAX_TRADE_ID:
            self.rejected += 1
            return SIDE_UNKNOWN
        sym = symbol.upper()
        t = self._tape(sym)
        if not self._admissible(t, ts_ns):
            self.rejected += 1
            return SIDE_UNKNOWN
        conds = set(conditions or ())
        tid = (((ord(exchange[0]) if exchange else 0) << 56) | tid_raw) if tid_raw else 0
        b = self._bucket(t, ts_ns)
        if tid and tid in b.ids:
            self.duplicates += 1
            return SIDE_UNKNOWN
        self.trades_seen += 1
        if tid:
            b.ids.add(tid)
        if conds & INELIGIBLE_CONDITIONS:
            self.ineligible += 1
            return SIDE_UNKNOWN
        flags = FLAG_ODD_LOT if ODD_LOT in conds else 0
        late = ts_ns < t.last_print_ns
        side = SIDE_UNKNOWN
        q = self._quote_for(t, ts_ns)
        if q is not None:
            bid, ask = q
            if price >= ask:
                side = SIDE_QUOTE_BUY
            elif price <= bid:
                side = SIDE_QUOTE_SELL
        invalid_period = t.quote_invalid_ns and ts_ns >= t.quote_invalid_ns and (t.quote is None or t.quote.ts_ns < t.quote_invalid_ns)
        if side == SIDE_UNKNOWN and not invalid_period:
            if late and t.last_print_ns - ts_ns > LATE_CLASSIFY_NS:
                ref = None  # too far behind to reconstruct a predecessor cheaply: unknown, still stored
            else:
                ref = self._predecessor_price(t, ts_ns) if late else t.last_price
            if ref is not None:
                if price > ref:
                    side = SIDE_TICK_BUY
                elif price < ref:
                    side = SIDE_TICK_SELL
                elif not late and t.last_tick_dir:
                    side = SIDE_TICK_BUY if t.last_tick_dir > 0 else SIDE_TICK_SELL
        # store: every value validated above, so the appends cannot fail part-way
        if b.ts and ts_ns < b.ts[-1]:
            b.sorted = False
        b.ts.append(ts_ns); b.price.append(price); b.size.append(size); b.side.append(side); b.flags.append(flags)
        b.n_trades += 1
        t.raw_count += 1
        if side > 0:
            b.buy_vol += size
        elif side < 0:
            b.sell_vol += size
        else:
            b.unknown_vol += size
        while t.raw_count > self.raw_cap and t.buckets:   # cap enforced after the append, whole seconds
            self._evict_oldest(t, "cap")
        if ts_ns > t.trade_watermark_ns:
            t.trade_watermark_ns = ts_ns
        if self._touch_session(t, ts_ns):
            m = t.minutes.setdefault(ts_ns // (60 * NS), [0, 0, 0, 0, 0, 0])
            m[5] += 1
            if side == SIDE_QUOTE_BUY:
                m[0] += size
            elif side == SIDE_QUOTE_SELL:
                m[1] += size
            elif side == SIDE_TICK_BUY:
                m[2] += size
            elif side == SIDE_TICK_SELL:
                m[3] += size
            else:
                m[4] += size
        if not late:
            if t.last_price is not None:
                if price > t.last_price:
                    t.last_tick_dir = 1
                elif price < t.last_price:
                    t.last_tick_dir = -1
            t.last_price = price
            t.last_print_ns = ts_ns
        return side

    # ------------------------------------------------------------------ coverage
    def coverage_of(self, symbol: str, t0_ns: int, t1_ns: int) -> Dict[str, Any]:
        t = self._syms.get(symbol.upper())
        if t is None or t.session_start_ns is None:
            return {"complete": False, "reason": "no_data", "held_from_ns": None}
        held_from = max(t.session_start_ns, t.evicted_boundary_ns)
        out: Dict[str, Any] = {"complete": False, "reason": None, "held_from_ns": held_from,
                               "watermark_ns": t.watermark_ns, "evicted_by_cap": t.evicted_by_cap}
        if t1_ns <= t0_ns:
            out["reason"] = "empty_window"
        elif t0_ns < held_from:
            out["reason"] = "window_starts_before_held_data"
        elif t.watermark_ns < t1_ns - self.right_slack_ns:
            out["reason"] = "feed_not_caught_up_to_window_end"
        elif t.trade_watermark_ns < t1_ns - TRADE_TAIL_NS:
            out["reason"] = "trade_delivery_behind_window_end"
        elif self._outage_in(t0_ns, t1_ns):
            out["reason"] = "feed_outage_in_window"
        else:
            out["complete"] = True
        return out

    # ------------------------------------------------------------------ raw queries
    def _iter(self, t: SymbolTape, t0_ns: int, t1_ns: int):
        """Yield (bucket, start_index, end_index) for prints in [t0, t1)."""
        s0, s1 = t0_ns // NS, -(-t1_ns // NS)
        for sec, b in t.buckets.items():
            if sec < s0 or sec >= s1 or b.n_trades == 0:
                continue
            b.ensure_sorted()
            i = bisect_left(b.ts, t0_ns) if sec == s0 else 0
            j = bisect_left(b.ts, t1_ns) if sec == s1 - 1 else len(b.ts)
            if j > i:
                yield b, i, j

    def prints(self, symbol: str, t0_ns: int, t1_ns: int) -> List[Tuple[int, float, int, int, int]]:
        t = self._syms.get(symbol.upper())
        if t is None:
            return []
        out = []
        for b, i, j in self._iter(t, t0_ns, t1_ns):
            for k in range(i, j):
                out.append((b.ts[k], b.price[k], b.size[k], b.side[k], b.flags[k]))
        return out

    def delta(self, symbol: str, t0_ns: int, t1_ns: int, min_classified_share: float = 0.5,
              min_quote_share: float = 0.20) -> Optional[Dict[str, Any]]:
        """Signed flow over [t0, t1). None when there are no eligible prints, when less than
        `min_classified_share` of the volume could be classified at all, or when less than
        `min_quote_share` was classified against a quote (measured 2026-09-23..25: 25% to 51% of volume
        prints at the bid or ask on the live feed). Includes coverage; a gate must also check `complete`."""
        t = self._syms.get(symbol.upper())
        if t is None:
            return None
        buy_q = sell_q = buy_t = sell_t = unknown = 0
        n = 0
        for b, i, j in self._iter(t, t0_ns, t1_ns):
            for k in range(i, j):
                s, v = b.side[k], b.size[k]
                n += 1
                if s == SIDE_QUOTE_BUY:
                    buy_q += v
                elif s == SIDE_QUOTE_SELL:
                    sell_q += v
                elif s == SIDE_TICK_BUY:
                    buy_t += v
                elif s == SIDE_TICK_SELL:
                    sell_t += v
                else:
                    unknown += v
        total = buy_q + sell_q + buy_t + sell_t + unknown
        if n == 0 or total <= 0:
            return None
        buy, sell = buy_q + buy_t, sell_q + sell_t
        if buy + sell == 0:
            return None
        quote_share = (buy_q + sell_q) / float(total)
        if (buy + sell) / float(total) < min_classified_share or quote_share < min_quote_share:
            return None
        cov = self.coverage_of(symbol, t0_ns, t1_ns)
        return {
            "buy_vol": buy, "sell_vol": sell, "unknown_vol": unknown, "n_trades": n,
            "buy_quote": buy_q, "sell_quote": sell_q, "buy_tick": buy_t, "sell_tick": sell_t,
            "delta": buy - sell, "delta_ratio": (buy - sell) / float(buy + sell),
            "classified_share": (buy + sell) / float(total), "quote_share": quote_share,
            "complete": cov["complete"], "coverage": cov, "t0_ns": t0_ns, "t1_ns": t1_ns,
        }

    def rolling_delta(self, symbol: str, t1_ns: int, window_s: int = 1800) -> Optional[Dict[str, Any]]:
        d = self.delta(symbol, t1_ns - int(window_s) * NS, t1_ns)
        if d is None or not d["complete"]:
            return None
        d["window_s"] = window_s
        return d

    def velocity(self, symbol: str, t1_ns: int, window_s: int = 60, min_trades: int = 5,
                 min_coverage: float = 0.5, max_last_age_s: float = 10.0) -> Optional[Dict[str, Any]]:
        t = self._syms.get(symbol.upper())
        if t is None:
            return None
        t0_ns = t1_ns - int(window_s) * NS
        first = last = None
        n = 0
        for b, i, j in self._iter(t, t0_ns, t1_ns):
            if first is None:
                first = (b.ts[i], b.price[i])
            last = (b.ts[j - 1], b.price[j - 1])
            n += j - i
        if first is None or n < min_trades:
            return None
        elapsed = (last[0] - first[0]) / NS
        if elapsed <= 0 or elapsed < min_coverage * window_s or (t1_ns - last[0]) / NS > max_last_age_s:
            return None
        move = last[1] - first[1]
        per_second = move / elapsed
        if not math.isfinite(per_second):
            return None
        cov = self.coverage_of(symbol, t0_ns, t1_ns)
        return {"price_change": move, "elapsed_s": elapsed, "per_second": per_second, "n_trades": n,
                "first_ns": first[0], "last_ns": last[0], "complete": cov["complete"], "coverage": cov}

    # ------------------------------------------------------------------ session delta and spread
    def session_delta(self, symbol: str, t_ns: int, min_minutes: float = 30.0, min_classified_share: float = 0.5,
                      min_quote_share: float = 0.20) -> Optional[Dict[str, Any]]:
        """Cumulative signed volume over [since, t) where `since` is the start of continuous feed (the
        session's first event, or the last reconnect) and `t` is the decision cutoff: only minutes that
        START before `t` count, so a later print can never reach an earlier decision. `partial` is true
        when `since` is not the session's first event (a restart or an outage). Unavailable until
        `min_minutes` of continuous feed exist, while an outage is open, when the feed has not caught up
        to `t`, or when the classification floors are not met."""
        t = self._syms.get(symbol.upper())
        if t is None or t.since_ns is None or self._open_outage_ns is not None:
            return None
        if t.watermark_ns < t_ns - self.right_slack_ns or t.trade_watermark_ns < t_ns - TRADE_TAIL_NS:
            return None
        minutes = (t_ns - t.since_ns) / NS / 60.0
        if minutes < min_minutes:
            return None
        m0 = -(-t.since_ns // (60 * NS))                        # first FULL minute after the generation start
        m1 = t_ns // (60 * NS)                                  # minutes [m0, m1): starting before the cutoff
        agg = [0, 0, 0, 0, 0, 0]
        for mk, vals in t.minutes.items():
            if m0 <= mk < m1:
                for k in range(6):
                    agg[k] += vals[k]
        buy_q, sell_q, buy_t, sell_t, unknown, n = agg
        total = buy_q + sell_q + buy_t + sell_t + unknown
        buy, sell = buy_q + buy_t, sell_q + sell_t
        if n == 0 or total <= 0 or buy + sell == 0:
            return None
        quote_share = (buy_q + sell_q) / float(total)
        classified = (buy + sell) / float(total)
        if classified < min_classified_share or quote_share < min_quote_share:
            return None
        partial = t.session_first_ns is None or t.since_ns > t.session_first_ns
        return {"delta": buy - sell, "delta_ratio": (buy - sell) / float(buy + sell), "buy_vol": buy, "sell_vol": sell,
                "unknown_vol": unknown, "n_trades": n, "quote_share": quote_share, "classified_share": classified,
                "since_ns": t.since_ns, "cutoff_ns": t_ns, "minutes_continuous": minutes,
                "partial": partial, "complete": True}

    def spread_stats(self, symbol: str, t1_ns: int, ref_window_s: int = 1800, now_window_s: int = 10,
                     min_ref_seconds: int = 300, min_now_quotes: int = 3) -> Optional[Dict[str, Any]]:
        """Spread now versus its recent normal: medians of the per-second last spread (bps of mid) over
        [t1 - now_window, t1) and [t1 - ref_window, t1). Unavailable with fewer than `min_ref_seconds`
        quoted seconds in the reference, fewer than `min_now_quotes` quotes in the now window, or an
        outage overlapping the reference window."""
        t = self._syms.get(symbol.upper())
        if t is None:
            return None
        r0 = t1_ns - int(ref_window_s) * NS
        n0 = t1_ns - int(now_window_s) * NS
        if self._outage_in(r0, t1_ns):
            return None
        ref: List[float] = []
        now: List[float] = []
        now_quotes = 0
        s_r0, s_n0, s1 = r0 // NS, n0 // NS, -(-t1_ns // NS)
        # the spread in force at the start of each second, carried forward up to book_stale_s
        state = None
        for sec in reversed(list(t.buckets.keys())):
            if sec >= s_r0:
                continue
            if (s_r0 - sec) * NS > self.book_stale_s * NS:
                break
            b = t.buckets[sec]
            if b.quote_n and b.last_quote_ns < r0 and b.last_spread_bps > 0 and not self._outage_in(b.last_quote_ns, r0) \
                    and not (b.last_quote_ns < t.quote_invalid_ns <= r0):
                state = (b.last_quote_ns, b.last_spread_bps)
                break
        for sec in range(s_r0, s1):
            sec_start = sec * NS
            if state is not None and state[0] < t.quote_invalid_ns <= sec_start:
                state = None   # a crossed quote ended that state's validity
            if state is not None and (sec + 1) * NS - state[0] <= self.book_stale_s * NS:
                ref.append(state[1])
                if sec >= s_n0:
                    now.append(state[1])
            b = t.buckets.get(sec)
            if b is not None and b.quote_n and b.last_quote_ns < t1_ns and b.last_spread_bps > 0:
                state = (b.last_quote_ns, b.last_spread_bps)
                if sec >= s_n0:
                    now_quotes += b.quote_n
        if len(ref) < min_ref_seconds or now_quotes < min_now_quotes or len(now) < max(1, now_window_s // 2):
            return None
        ref_med = _median(ref)
        now_med = _median(now)
        if not (math.isfinite(ref_med) and math.isfinite(now_med)) or ref_med <= 0:
            return None
        return {"spread_now_bps": now_med, "spread_ref_bps": ref_med, "ratio": now_med / ref_med,
                "ref_seconds": len(ref), "now_seconds": len(now), "now_quotes": now_quotes, "complete": True}

    # ------------------------------------------------------------------ book
    def book_imbalance(self, symbol: str, t1_ns: int, window_s: int = 30, max_last_age_s: float = 5.0,
                       min_coverage: float = 0.8) -> Optional[Dict[str, Any]]:
        """Inside-book imbalance (bid - ask) / (bid + ask) over [t1 - window, t1), weighted by the seconds
        each quote state was in force (carried forward up to `book_stale_s`). Unavailable when fewer than
        `min_coverage` of the window's seconds are covered, the newest quote is older than
        `max_last_age_s`, or an outage overlaps the window. Quote details are as of the query cutoff."""
        t = self._syms.get(symbol.upper())
        if t is None or window_s <= 0:
            return None
        t0_ns = t1_ns - int(window_s) * NS
        s0, s1 = t0_ns // NS, -(-t1_ns // NS)
        state = None
        for sec in reversed(list(t.buckets.keys())):
            if sec >= s0:
                continue
            b = t.buckets[sec]
            if b.quote_n and b.last_quote_ns < t0_ns:
                # a quote from before an outage or a cross says nothing about the book after it
                if not self._outage_in(b.last_quote_ns, t0_ns) and not (b.last_quote_ns < t.quote_invalid_ns <= t0_ns):
                    state = (b.last_quote_ns, b.last_bid_size, b.last_ask_size, b.last_bid, b.last_ask)
                break
        bid = ask = 0.0
        covered = 0
        quotes = 0
        newest = None
        for sec in range(s0, s1):
            if state is not None and state[0] < t.quote_invalid_ns <= sec * NS:
                state = None   # a crossed quote ended that state's validity
            # the state in force at the START of this second is what this second contributes
            if state is not None and (sec + 1) * NS - state[0] <= self.book_stale_s * NS:
                bid += state[1]
                ask += state[2]
                covered += 1
            b = t.buckets.get(sec)
            if b is not None and b.quote_n and b.last_quote_ns < t1_ns:
                state = (b.last_quote_ns, b.last_bid_size, b.last_ask_size, b.last_bid, b.last_ask)
                quotes += b.quote_n
                newest = state
        n_secs = s1 - s0
        if covered == 0 or bid + ask <= 0 or covered / n_secs < min_coverage:
            return None
        if newest is None:
            newest = state
        if newest is None or (t1_ns - newest[0]) / NS > max_last_age_s:
            return None
        if self._outage_in(t0_ns, t1_ns):
            return None
        imb = (bid - ask) / (bid + ask)
        if not math.isfinite(imb):
            return None
        return {"imbalance": imb, "quotes": quotes, "seconds": covered, "coverage": covered / n_secs,
                "bid": newest[3], "ask": newest[4], "bid_size": newest[1], "ask_size": newest[2],
                "spread": newest[4] - newest[3], "quote_ts_ns": newest[0], "complete": True}

    # ------------------------------------------------------------------ health
    def coverage(self, symbol: str) -> Dict[str, Any]:
        t = self._syms.get(symbol.upper())
        if t is None:
            return {"seconds": 0, "trades": 0, "quotes": 0, "raw_prints": 0, "held_from_ns": None,
                    "last_ns": None, "raw_oldest_age_s": None, "evicted_by_cap": 0, "evicted_by_age": 0}
        held = max(t.session_start_ns or 0, t.evicted_boundary_ns)
        return {"seconds": len(t.buckets), "trades": t.raw_count,
                "quotes": sum(b.quote_n for b in t.buckets.values()), "raw_prints": t.raw_count,
                "session_prints": sum(v[5] for v in t.minutes.values()), "session_minutes": len(t.minutes),
                "since_ns": t.since_ns, "session": t.session,
                "held_from_ns": held, "last_ns": t.watermark_ns,
                "raw_oldest_age_s": (t.watermark_ns - held) / NS if held else None,
                "evicted_by_cap": t.evicted_by_cap, "evicted_by_age": t.evicted_by_age}

    def health(self) -> Dict[str, Any]:
        syms = sorted(self._syms)
        return {"symbols": syms, "trades_seen": self.trades_seen, "quotes_seen": self.quotes_seen,
                "rejected": self.rejected, "duplicates": self.duplicates, "ineligible": self.ineligible,
                "outages": len(self._outages), "open_outage": self._open_outage_ns is not None,
                "raw_prints_total": sum(t.raw_count for t in self._syms.values()),
                "per_symbol": {s: self.coverage(s) for s in syms}}

    def reset(self) -> None:
        self._syms.clear()
        self._outages.clear()
        self._open_outage_ns = None
        self.trades_seen = self.quotes_seen = self.rejected = self.duplicates = self.ineligible = 0
