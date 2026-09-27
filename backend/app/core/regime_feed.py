"""backend/app/core/regime_feed.py
Layer 4 (macro / correlation) for Ride the Trend v2: sector ETFs and dollar / rates ETF price proxies,
bars only, with explicit wall-clock returns and freshness.

These are ETF PRICE filters. UUP tracks a dollar-futures index, SHY and IEF are Treasury portfolios;
none of them is the cash DXY or a cash yield. Sector ETFs give a direction and a relative-return
comparison, not a correlation measurement.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
import math
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.app.models.events import BarEvent

ET = ZoneInfo("America/New_York")
SESSION_OPEN, SESSION_CLOSE = dtime(9, 30), dtime(16, 0)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _valid_bar(bar: BarEvent) -> bool:
    vals = (bar.open, bar.high, bar.low, bar.close, float(bar.volume))
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals):
        return False
    return bar.low > 0 and bar.high >= bar.low and bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high \
        and bar.volume >= 0

DEFAULT_SECTOR_MAP: Dict[str, str] = {
    "AAPL": "XLK", "NVDA": "XLK", "AMD": "XLK", "MSFT": "XLK", "PLTR": "XLK",
    "META": "XLC", "GOOGL": "XLC",
    "AMZN": "XLY",
    "COIN": "XLF",
}
DOLLAR = "UUP"
RATES_10Y = "IEF"
RATES_2Y = "SHY"


class _Series:
    """One regime symbol's regular-session bars for one ET session, UTC-aware, with the anchored VWAP
    as of every bar so historical queries never see a later VWAP."""

    def __init__(self, symbol: str, session: str) -> None:
        self.symbol = symbol
        self.session = session                  # ET date, ISO
        self.ts: List[datetime] = []            # bar START (UTC-aware), ordered
        self.close: List[float] = []
        self.vwap_at: List[float] = []          # anchored VWAP including this bar
        self.cum_pv = 0.0
        self.cum_v = 0.0

    @property
    def bars(self) -> int:
        return len(self.ts)

    def add(self, bar: BarEvent) -> bool:
        ts = _utc(bar.timestamp)
        if self.ts and ts <= self.ts[-1]:
            return False
        v = float(bar.volume)
        self.cum_pv += (bar.high + bar.low + bar.close) / 3.0 * v
        self.cum_v += v
        self.ts.append(ts)
        self.close.append(float(bar.close))
        self.vwap_at.append(self.cum_pv / self.cum_v if self.cum_v > 0 else float(bar.close))
        return True

    def index_at_or_before(self, t: datetime) -> Optional[int]:
        """Index of the last bar that COMPLETED at or before t (bar start + 1 minute <= t)."""
        t = _utc(t)
        for k in range(len(self.ts) - 1, -1, -1):
            if self.ts[k] + timedelta(minutes=1) <= t:
                return k
        return None

    def close_at_or_before(self, t: datetime) -> Optional[Tuple[datetime, float]]:
        k = self.index_at_or_before(t)
        return None if k is None else (self.ts[k], self.close[k])


class RegimeFeed:
    def __init__(self, symbols: List[str], sector_map: Optional[Dict[str, str]] = None,
                 dollar: str = DOLLAR, rates_10y: str = RATES_10Y, rates_2y: str = RATES_2Y) -> None:
        self.symbols = [s.upper() for s in symbols]
        self.sector_map = {k.upper(): v.upper() for k, v in (sector_map or DEFAULT_SECTOR_MAP).items()}
        self.dollar, self.rates_10y, self.rates_2y = dollar.upper(), rates_10y.upper(), rates_2y.upper()
        self._series: Dict[str, _Series] = {}
        self.bars_seen = 0

    def reset_session(self) -> None:
        self._series.clear()

    def on_bar(self, bar: BarEvent) -> bool:
        sym = bar.symbol.upper()
        if sym not in self.symbols or not _valid_bar(bar):
            return False
        ts = _utc(bar.timestamp)
        et = ts.astimezone(ET)
        if not (SESSION_OPEN <= et.time() < SESSION_CLOSE):
            return False  # regular session only: no pre-market in the anchored VWAP
        session = et.date().isoformat()
        s = self._series.get(sym)
        if s is not None and session < s.session:
            return False  # a bar from an earlier session never rolls the series back
        if s is None or session != s.session:
            s = _Series(sym, session)
            self._series[sym] = s
        if s.add(bar):
            self.bars_seen += 1
            return True
        return False

    # ------------------------------------------------------------------ measures
    def wallclock_return(self, symbol: str, t_end: datetime, minutes: int = 30,
                         max_age_s: float = 120.0) -> Optional[Dict[str, Any]]:
        """Return between the last bar completed at or before t_end - minutes and the last bar completed
        at or before t_end. None when the newest bar is older than max_age_s or either endpoint is missing."""
        s = self._series.get(symbol.upper())
        if s is None:
            return None
        t_end = _utc(t_end)
        end = s.close_at_or_before(t_end)
        start = s.close_at_or_before(t_end - timedelta(minutes=minutes))
        if end is None or start is None:
            return None
        age = (t_end - (end[0] + timedelta(minutes=1))).total_seconds()
        start_age = (t_end - timedelta(minutes=minutes) - (start[0] + timedelta(minutes=1))).total_seconds()
        if age > max_age_s or start_age > max_age_s or start[1] <= 0:
            return None  # both endpoints must be fresh relative to their intended times
        ret = end[1] / start[1] - 1.0
        if not math.isfinite(ret):
            return None
        return {"return": ret, "end_bar": end[0].isoformat(), "start_bar": start[0].isoformat(),
                "age_s": age, "start_age_s": start_age, "close": end[1]}

    def direction(self, symbol: str, t_end: datetime, max_age_s: float = 120.0) -> Optional[Dict[str, Any]]:
        s = self._series.get(symbol.upper())
        if s is None or not s.bars:
            return None
        t_end = _utc(t_end)
        k = s.index_at_or_before(t_end)
        if k is None:
            return None
        vwap = s.vwap_at[k]   # as of that bar, never a later VWAP
        close = s.close[k]
        age = (t_end - (s.ts[k] + timedelta(minutes=1))).total_seconds()
        if age > max_age_s or vwap <= 0 or not math.isfinite(vwap):
            return None
        return {"close": close, "vwap": vwap, "above_vwap": close > vwap, "below_vwap": close < vwap,
                "age_s": age, "bar": s.ts[k].isoformat()}

    def evaluate(self, stock: str, is_long: bool, t_end: datetime, stock_return_30: Optional[float],
                 thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """All Layer 4 measures for one candidate at decision time t_end (the stock bar's completion).

        Returns {"measures": {...}, "failed": [gate names], "unavailable": [gate names]}. Long rules are
        written; shorts use the direction multiplier (rules mirrored exactly)."""
        th = {"sector_rs_min": 0.0, "dollar_max_30m": 0.0025, "rates10_min_30m": -0.0025, "rates2_min_30m": -0.0010}
        th.update(thresholds or {})
        sgn = 1.0 if is_long else -1.0
        out: Dict[str, Any] = {"measures": {}, "failed": [], "unavailable": []}
        sector = self.sector_map.get(stock.upper())
        out["measures"]["sector_etf"] = sector
        # sector direction
        d = self.direction(sector, t_end) if sector else None
        if d is None:
            out["unavailable"].append("SECTOR_DIRECTION")
        else:
            out["measures"]["sector_direction"] = d
            ok = d["above_vwap"] if is_long else d["below_vwap"]   # strict on both sides
            if not ok:
                out["failed"].append("SECTOR_AGAINST")
        # sector relative return over identical wall-clock endpoints
        r = self.wallclock_return(sector, t_end, 30) if sector else None
        if r is None or stock_return_30 is None or not math.isfinite(float(stock_return_30)):
            out["unavailable"].append("SECTOR_RS")
        else:
            rs = stock_return_30 - r["return"]
            out["measures"]["sector_return_30m"] = r
            out["measures"]["sector_rs_30m"] = rs
            if sgn * rs < th["sector_rs_min"]:
                out["failed"].append("SECTOR_RS_FILTER")
        # dollar wind (UUP prints sparsely: allow a 5-minute-old bar)
        u = self.wallclock_return(self.dollar, t_end, 30, max_age_s=300.0)
        if u is None:
            out["unavailable"].append("DOLLAR_WIND")
        else:
            out["measures"]["dollar_return_30m"] = u
            if sgn * u["return"] > th["dollar_max_30m"]:
                out["failed"].append("MACRO_WIND_AGAINST:dollar")
        # rates wind: IEF (10y proxy) is the gate, SHY (2y proxy) is logged
        i10 = self.wallclock_return(self.rates_10y, t_end, 30)
        if i10 is None:
            out["unavailable"].append("RATES_WIND")
        else:
            out["measures"]["ief_return_30m"] = i10
            if sgn * i10["return"] < th["rates10_min_30m"]:
                out["failed"].append("MACRO_WIND_AGAINST:yields")
        s2 = self.wallclock_return(self.rates_2y, t_end, 30)
        out["measures"]["shy_return_30m"] = s2
        return out

    def wind_text(self, now: datetime) -> str:
        """Plain sentence for the card."""
        now = _utc(now)
        u = self.wallclock_return(self.dollar, now, 30, max_age_s=300.0)
        i10 = self.wallclock_return(self.rates_10y, now, 30)
        if u is None and i10 is None:
            return "Dollar and rates data not flowing yet."
        parts = []
        if u is not None:
            parts.append("Dollar surging" if u["return"] > 0.0025 else "Dollar falling fast" if u["return"] < -0.0025 else "Dollar calm")
        if i10 is not None:
            parts.append("yields surging" if i10["return"] < -0.0025 else "yields falling fast" if i10["return"] > 0.0025 else "rates calm")
        return ", ".join(parts).capitalize() + "."

    def health(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = _utc(now) if now else datetime.now(timezone.utc)
        per = {}
        for sym in self.symbols:
            s = self._series.get(sym)
            last = s.ts[-1] if s and s.bars else None
            per[sym] = {"bars": s.bars if s else 0, "session": s.session if s else None,
                        "last_bar": last.isoformat() if last else None,
                        "last_bar_age_s": (now - (last + timedelta(minutes=1))).total_seconds() if last else None}
        return {"symbols": self.symbols, "sector_map": self.sector_map, "bars_seen": self.bars_seen, "per_symbol": per}
