"""backend/app/core/volume_profile.py
Volume profile from prior sessions' 1-minute bars (the low-cost alternative to a live L2 profile).

For each tradable symbol the profile of the previous N regular sessions is built once per session:
price is cut into buckets of `bucket_pct` of the reference close, each bar's volume is spread evenly over
the buckets its high-low range touches, buckets are summed across sessions, and a high-volume node (HVN)
is a maximal run of adjacent buckets each holding at least `threshold` times the median non-empty bucket.

Queries used by Ride the Trend v2 at the resumption test:
- `support_node(price, atr, is_long)`: the node the pullback extreme landed on (inside the node, or within
  `below_atr` below it / `above_atr` above it for a long; mirrored for a short).
- `path_obstacle(entry, target, is_long, exclude)`: the first node other than the support node that covers
  at least 10% of the road from the entry to the first target (where a runner would stall).

Profiles are rebuilt only at the session boundary; a profile is valid only for the session it was built
for and only when it holds at least `min_sessions` sessions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
import math
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from backend.app.models.events import BarEvent
from backend.app.core.trading_windows import is_trading_day, session_close, session_minutes

ET = ZoneInfo("America/New_York")
SESSION_OPEN, SESSION_CLOSE = dtime(9, 30), dtime(16, 0)


@dataclass
class Node:
    low: float
    high: float
    volume: float
    peak_price: float
    buckets: int

    def as_dict(self) -> Dict[str, Any]:
        return {"low": round(self.low, 4), "high": round(self.high, 4), "volume": round(self.volume),
                "peak": round(self.peak_price, 4), "buckets": self.buckets}


@dataclass
class VolumeProfile:
    symbol: str
    for_session: str                 # ET date the profile is valid for
    sessions: List[str]              # sessions the bars came from (strictly before `for_session`)
    bucket_width: float
    reference_close: float
    nodes: List[Node] = field(default_factory=list)
    total_volume: float = 0.0
    median_bucket: float = 0.0
    built_at: Optional[str] = None

    def is_valid_for(self, session: str, min_sessions: int) -> bool:
        return self.for_session == session and len(self.sessions) >= min_sessions and self.bucket_width > 0

    def support_node(self, price: float, atr: float, is_long: bool, below_atr: float = 0.25,
                     above_atr: float = 0.50) -> Optional[Node]:
        """The node the price sits on: a node CONTAINING the price wins; otherwise the nearest node within
        the tolerance. Long: a low may dip `below_atr` under the node or sit `above_atr` over it. Short:
        mirrored (a high may poke `below_atr` over the node or sit `above_atr` under it)."""
        containing = [n for n in self.nodes if n.low <= price <= n.high]
        if containing:
            return max(containing, key=lambda n: n.volume)
        best, best_gap = None, None
        for n in self.nodes:
            if is_long:
                ok = n.low - below_atr * atr <= price <= n.high + above_atr * atr
            else:
                ok = n.low - above_atr * atr <= price <= n.high + below_atr * atr
            if not ok:
                continue
            gap = min(abs(price - n.low), abs(price - n.high))
            if best is None or gap < best_gap:
                best, best_gap = n, gap
        return best

    def overhead_node(self, entry: float, atr: float, is_long: bool, dist_atr: float = 1.0) -> Optional[Node]:
        """Nearest node in the trade's direction within `dist_atr` of the entry (None = clear road)."""
        best = None
        for n in self.nodes:
            if is_long and n.low > entry and n.low - entry <= dist_atr * atr:
                if best is None or n.low < best.low:
                    best = n
            elif (not is_long) and n.high < entry and entry - n.high <= dist_atr * atr:
                if best is None or n.high > best.high:
                    best = n
        return best

    def path_obstacle(self, entry: float, target: float, is_long: bool, exclude: Optional[Node] = None,
                      min_overlap_frac: float = 0.10) -> Optional[Node]:
        """The first node (other than `exclude`, the node the dip landed on) that intersects the road from
        the entry to the first target: for a long, a node with `low < target` and `high > entry` whose
        overlap with (entry, target] is at least `min_overlap_frac` of that path. None = clear road."""
        lo, hi = (entry, target) if is_long else (target, entry)
        path = hi - lo
        if path <= 0:
            return None
        best = None
        for n in self.nodes:
            if exclude is not None and n is exclude:
                continue
            overlap = min(n.high, hi) - max(n.low, lo)
            if overlap <= 0 or overlap / path < min_overlap_frac:
                continue
            key = n.low if is_long else -n.high
            if best is None or key < (best.low if is_long else -best.high):
                best = n
        return best

    def as_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "for_session": self.for_session, "sessions": list(self.sessions),
                "bucket_width": round(self.bucket_width, 5), "reference_close": self.reference_close,
                "nodes": [n.as_dict() for n in self.nodes], "node_count": len(self.nodes),
                "total_volume": round(self.total_volume), "median_bucket": round(self.median_bucket), "built_at": self.built_at}


def _session_of(bar: BarEvent) -> Optional[str]:
    ts = bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc)
    et = ts.astimezone(ET)
    if not is_trading_day(et.date()) or not (SESSION_OPEN <= et.time() < session_close(et.date())):
        return None
    return et.date().isoformat()


MAX_BUCKETS_PER_BAR = 2000


def _valid(bar: BarEvent, symbol: Optional[str] = None) -> bool:
    if isinstance(bar.volume, bool) or not isinstance(bar.volume, (int, float)):
        return False
    vals = (bar.open, bar.high, bar.low, bar.close, float(bar.volume))
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals):
        return False
    if symbol is not None and str(bar.symbol).upper() != symbol.upper():
        return False
    return bar.low > 0 and bar.high >= bar.low and bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high \
        and bar.volume >= 0


class ProfileBuildError(ValueError):
    pass


def build_profile(symbol: str, bars: Iterable[BarEvent], for_session: str, sessions: int = 5,
                  bucket_pct: float = 0.001, threshold: float = 1.5, min_sessions: int = 5,
                  min_bars_per_session: int = 300) -> VolumeProfile:
    """Profile of the `sessions` regular sessions strictly before `for_session`. Raises
    ProfileBuildError with the specific reason when the history is not good enough."""
    if sessions <= 0 or min_sessions <= 0 or min_sessions > sessions:
        raise ProfileBuildError(f"bad session configuration sessions={sessions} min_sessions={min_sessions}")
    target_day = date.fromisoformat(for_session)
    if target_day.year not in (2026, 2027):
        raise ProfileBuildError(f"exchange calendar unavailable for {for_session}")
    expected = []
    cursor = target_day - timedelta(days=1)
    while len(expected) < sessions:
        if cursor.year not in (2026, 2027):
            raise ProfileBuildError("prior-session exchange calendar unavailable")
        if is_trading_day(cursor):
            expected.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    by_session: Dict[str, Dict[datetime, BarEvent]] = {}
    for b in bars:
        if not _valid(b, symbol):
            continue
        s = _session_of(b)
        if s is None or s >= for_session:
            continue
        if s in expected and b.timestamp.second == 0 and b.timestamp.microsecond == 0:
            ts = b.timestamp.replace(tzinfo=timezone.utc) if b.timestamp.tzinfo is None else b.timestamp.astimezone(timezone.utc)
            by_session.setdefault(s, {})[ts] = b   # one observation per actual minute
    used = sorted(by_session)
    if len(used) < min_sessions:
        raise ProfileBuildError(f"only {len(used)} usable sessions before {for_session}, need {min_sessions}")
    short = [s for s in used if len(by_session[s]) < min(min_bars_per_session, session_minutes(date.fromisoformat(s)))]
    if short:
        raise ProfileBuildError(f"incomplete sessions (fewer than {min_bars_per_session} bars): {short}")
    last_bars = sorted(by_session[used[-1]].values(), key=lambda b: b.timestamp)
    ref_close = float(last_bars[-1].close)
    width = ref_close * bucket_pct
    if width <= 0 or not math.isfinite(width):
        raise ProfileBuildError(f"bad reference close {ref_close}")
    buckets: Dict[int, float] = {}
    total = 0.0
    for s in used:
        for b in by_session[s].values():
            v = float(b.volume)
            if v <= 0:
                continue
            lo_i, hi_i = int(math.floor(b.low / width)), int(math.floor(b.high / width))
            if hi_i - lo_i + 1 > MAX_BUCKETS_PER_BAR:
                continue   # a bar spanning thousands of buckets is bad data, not a profile
            span = b.high - b.low
            if span <= 0:
                buckets[lo_i] = buckets.get(lo_i, 0.0) + v
            else:
                # volume allocated by the length of the bar's range inside each bucket
                for i in range(lo_i, hi_i + 1):
                    overlap = min(b.high, (i + 1) * width) - max(b.low, i * width)
                    if overlap > 0:
                        buckets[i] = buckets.get(i, 0.0) + v * overlap / span
            total += v
    if not buckets or total <= 0:
        raise ProfileBuildError("no volume in the selected sessions")
    vols = sorted(buckets.values())
    median = vols[len(vols) // 2] if len(vols) % 2 else (vols[len(vols) // 2 - 1] + vols[len(vols) // 2]) / 2.0
    cut = threshold * median
    nodes: List[Node] = []
    run: List[int] = []
    for i in sorted(buckets):
        if buckets[i] >= cut and (not run or i == run[-1] + 1):
            run.append(i)
            continue
        if run:
            nodes.append(_node(run, buckets, width))
            run = []
        if buckets[i] >= cut:
            run.append(i)
    if run:
        nodes.append(_node(run, buckets, width))
    return VolumeProfile(symbol=symbol.upper(), for_session=for_session, sessions=used, bucket_width=width,
                         reference_close=ref_close, nodes=nodes, total_volume=total, median_bucket=median,
                         built_at=datetime.now(timezone.utc).isoformat())


def _node(run: List[int], buckets: Dict[int, float], width: float) -> Node:
    vol = sum(buckets[i] for i in run)
    peak = max(run, key=lambda i: buckets[i])
    return Node(low=run[0] * width, high=(run[-1] + 1) * width, volume=vol, peak_price=(peak + 0.5) * width, buckets=len(run))


class ProfileStore:
    """Profiles per symbol for the current session; rebuilt at the session boundary."""

    def __init__(self, sessions: int = 5, min_sessions: int = 5, bucket_pct: float = 0.001, threshold: float = 1.5) -> None:
        if sessions <= 0 or min_sessions <= 0 or min_sessions > sessions:
            raise ValueError(f"bad profile configuration sessions={sessions} min_sessions={min_sessions}")
        self.sessions = sessions
        self.min_sessions = min_sessions
        self.bucket_pct = bucket_pct
        self.threshold = threshold
        self.profiles: Dict[str, VolumeProfile] = {}
        self.errors: Dict[str, str] = {}
        self.last_build: Optional[str] = None

    def build(self, symbol: str, bars: Iterable[BarEvent], for_session: str) -> Optional[VolumeProfile]:
        sym = symbol.upper()
        try:
            prof = build_profile(sym, bars, for_session, self.sessions, self.bucket_pct, self.threshold, self.min_sessions)
        except Exception as exc:
            self.errors[sym] = f"{type(exc).__name__}: {exc}"
            self.profiles.pop(sym, None)
            return None
        self.profiles[sym] = prof
        self.errors.pop(sym, None)
        self.last_build = datetime.now(timezone.utc).isoformat()
        return prof

    def get(self, symbol: str, session: str) -> Optional[VolumeProfile]:
        prof = self.profiles.get(symbol.upper())
        if prof is None or not prof.is_valid_for(session, self.min_sessions):
            return None
        return prof

    def ready(self, symbols: Iterable[str], session: str) -> Dict[str, bool]:
        return {s.upper(): self.get(s, session) is not None for s in symbols}

    def health(self) -> Dict[str, Any]:
        return {"last_build": self.last_build, "errors": dict(self.errors), "required_sessions": self.min_sessions,
                "profiles": {s: {"for_session": p.for_session, "sessions": len(p.sessions), "nodes": len(p.nodes),
                                 "bucket_width": round(p.bucket_width, 4)} for s, p in self.profiles.items()}}
