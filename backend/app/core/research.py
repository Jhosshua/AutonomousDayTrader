"""Research recording for later backtesting and knob tuning.

Everything here is observation only. Nothing in this module may block, slow or
change a trading decision:

* Rows go to their OWN SQLite file through a bounded queue drained by a
  background thread. The trading checkpoint transaction never sees them, so a
  research write failure can never flip ``persistence_healthy`` or lock out
  entries.
* A full queue drops the row and counts it (``dropped``) instead of waiting.
* Callers wrap every call in ``safe()``; any exception is logged and counted.

Plan: PLAN_2026_09_25_backtest_tracking.md (revised after the Codex attack).
"""
from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import queue
import sqlite3
import threading
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

RECORD_VERSION = 2
MAX_PAYLOAD_BYTES = 64_000
MAX_STOP_HISTORY = 50
MEMORY_KEEP = 500
MAX_DB_BYTES = 1_000_000_000      # stop writing research at 1 GB
MIN_FREE_BYTES = 500_000_000      # never let research eat the trading volume's last 500 MB
SPACE_CHECK_EVERY = 200


def json_safe(value: Any, depth: int = 0) -> Any:
    """Plain JSON value: finite floats only, ISO datetimes, str keys, bounded depth."""
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value") and not isinstance(value, (dict, list, tuple)):
        inner = getattr(value, "value")
        if isinstance(inner, (str, int, float)):
            return json_safe(inner, depth + 1)
    if isinstance(value, dict):
        return {str(k): json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v, depth + 1) for v in value]
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except Exception:
        return str(value)


def rnd(value: Any, places: int = 4) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, places) if math.isfinite(f) else None


def iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def minute_floor(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


class ResearchRecorder:
    """Append-only research sink. Disk is optional; memory keeps the recent rows."""

    TABLES = {
        "trades": "research_trades",
        "signals": "research_signals",
    }

    def __init__(self, path: Optional[str] = None, max_queue: int = 5000,
                 forbidden_paths: Tuple[str, ...] = ()) -> None:
        self.path: Optional[Path] = Path(path).expanduser().resolve() if path else None
        self._counter_lock = threading.Lock()
        self._fail_logged = 0
        self._space_ok = True
        self._writes_since_check = 0
        self.queued = 0
        self.written = 0
        self.dropped = 0
        self.errors = 0
        self.last_error: Optional[str] = None
        self.recent: Dict[str, Deque[Dict[str, Any]]] = {
            kind: deque(maxlen=MEMORY_KEEP) for kind in self.TABLES
        }
        self._queue: "queue.Queue[Tuple[str, str, str, str, str, str]]" = queue.Queue(maxsize=max_queue)
        self._read_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._reader: Optional[sqlite3.Connection] = None
        if self.path is not None and any(
            self.path == Path(p).expanduser().resolve() for p in forbidden_paths if p
        ):
            self._fail(ValueError("research path equals the trading state database; research disk disabled"))
            self.path = None
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = self._connect()
                self._init_schema(conn)
                conn.close()
                self._thread = threading.Thread(target=self._drain, name="research-writer", daemon=True)
                self._thread.start()
            except Exception as exc:  # disk research is optional
                self._fail(exc)
                self.path = None

    # ------------------------------------------------------------------ disk
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_trades (
                row_id TEXT PRIMARY KEY,
                session_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_research_trades_session
                ON research_trades(session_date, row_id);
            CREATE TABLE IF NOT EXISTS research_signals (
                row_id TEXT PRIMARY KEY,
                session_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_research_signals_session
                ON research_signals(session_date, row_id);
            """
        )
        conn.commit()

    def _drain(self) -> None:
        conn: Optional[sqlite3.Connection] = None
        while True:
            batch = [self._queue.get()]
            while len(batch) < 200:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                self._writes_since_check += len(batch)
                if self._writes_since_check >= SPACE_CHECK_EVERY or not self._space_ok:
                    self._writes_since_check = 0
                    self._space_ok = self._space_available()
                if not self._space_ok:
                    self._count("dropped", len(batch))
                    self.last_error = "research store full or volume low on space; rows dropped"
                    continue
                if conn is None:
                    conn = self._connect()
                with conn:
                    for table, row_id, session_date, strategy_id, kind, payload in batch:
                        cur = conn.execute(
                            f"INSERT OR IGNORE INTO {table}"
                            "(row_id, session_date, strategy_id, kind, created_at, payload) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (row_id, session_date, strategy_id, kind,
                             datetime.now(timezone.utc).isoformat(), payload),
                        )
                        self._count("written", cur.rowcount)
            except Exception as exc:
                self._fail(exc)
                self._count("dropped", len(batch))
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
                conn = None
            finally:
                for _ in batch:
                    self._queue.task_done()

    def _count(self, name: str, n: int = 1) -> None:
        with self._counter_lock:
            setattr(self, name, getattr(self, name) + n)

    def _space_available(self) -> bool:
        import shutil
        try:
            size = sum(
                p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file()
            )
            free = shutil.disk_usage(self.path.parent).free
        except Exception:
            return True
        return size < MAX_DB_BYTES and free > MIN_FREE_BYTES

    def _fail(self, exc: BaseException) -> None:
        self._count("errors")
        self.last_error = f"{type(exc).__name__}: {exc}"
        # Log the first few, then every 500th, so a repeating failure cannot flood logs.
        self._fail_logged += 1
        if self._fail_logged <= 5 or self._fail_logged % 500 == 0:
            log.warning("Research recorder error #%d (trading unaffected): %s", self._fail_logged, self.last_error)

    # ----------------------------------------------------------------- write
    def record(self, kind: str, row_id: str, row: Dict[str, Any]) -> bool:
        """Queue one row. Never blocks; returns False when the row was dropped.

        The stored (possibly trimmed) row is ``self.recent[kind][-1]``."""
        table = self.TABLES[kind]
        row = json_safe(row)
        row.setdefault("record_version", RECORD_VERSION)
        payload = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
        if len(payload) > MAX_PAYLOAD_BYTES:
            trimmed = {k: row.get(k) for k in ("record_version", "row_id", "session_date", "strategy_id", "symbol")}
            trimmed["truncated"] = True
            trimmed["original_bytes"] = len(payload)
            row = trimmed
            payload = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
        self.recent[kind].append(row)
        if self.path is None:
            return True
        try:
            self._queue.put_nowait((
                table, row_id, str(row.get("session_date") or ""), str(row.get("strategy_id") or ""),
                str(row.get("kind") or kind), payload,
            ))
            self._count("queued")
            return True
        except queue.Full:
            self._count("dropped")
            self.last_error = "queue full"
            return False

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until queued rows are written (tests, graceful shutdown)."""
        if self.path is None or self._thread is None:
            return True
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout)
        while self._queue.unfinished_tasks and datetime.now(timezone.utc) < deadline:
            threading.Event().wait(0.02)
        return self._queue.unfinished_tasks == 0

    # ------------------------------------------------------------------ read
    def list(self, kind: str, since: Optional[str] = None, limit: int = 100,
             after: Optional[str] = None) -> List[Dict[str, Any]]:
        """Rows ordered by (session_date, row_id). Disk when available, else memory."""
        limit = max(1, min(int(limit), 1000))
        if self.path is None:
            rows = [r for r in self.recent[kind] if not since or str(r.get("session_date") or "") >= since]
            rows.sort(key=lambda r: (str(r.get("session_date") or ""), str(r.get("row_id") or "")))
            if after:
                rows = [r for r in rows if f"{r.get('session_date')}|{r.get('row_id')}" > after]
            return rows[:limit]
        table = self.TABLES[kind]
        clauses, params = [], []
        if since:
            clauses.append("session_date >= ?")
            params.append(since)
        if after and "|" in after:
            s, r = after.split("|", 1)
            clauses.append("(session_date > ? OR (session_date = ? AND row_id > ?))")
            params.extend([s, s, r])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read_lock:
            if self._reader is None:
                self._reader = self._connect()
            cur = self._reader.execute(
                f"SELECT payload FROM {table} {where} ORDER BY session_date, row_id LIMIT ?",
                (*params, limit),
            )
            return [json.loads(p) for (p,) in cur.fetchall()]

    def health(self) -> Dict[str, Any]:
        return {
            "disk": self.path is not None,
            "path": str(self.path) if self.path else None,
            "queued": self.queued,
            "written": self.written,
            "pending": self._queue.qsize() if self.path else 0,
            "dropped": self.dropped,
            "errors": self.errors,
            "last_error": self.last_error,
        }


def safe(fn: Callable[..., Any], *args: Any, recorder: Optional[ResearchRecorder] = None, **kwargs: Any) -> Any:
    """Run a research step; any failure is logged and counted, never raised."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        if recorder is not None:
            recorder._fail(exc)
        else:
            log.warning("Research step failed (trading unaffected): %s", exc)
        return None


# --------------------------------------------------------------------------
# Excursion tracking (max favourable / adverse price while shares are held)
# --------------------------------------------------------------------------

_ET = None


def _et_date(ts: datetime) -> Any:
    global _ET
    if _ET is None:
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
    return ts.astimezone(_ET).date()


def new_excursion() -> Dict[str, Any]:
    return {
        "high": None, "low": None, "high_at": None, "low_at": None,
        "first_bar": None, "last_bar": None, "bars": 0, "max_gap_min": 0,
        # Extremes before the most recent bar, so the exit-minute bar can be
        # set aside as an ambiguous boundary (it holds prices after the exit).
        "prev": None,
        "last_bar_high": None, "last_bar_low": None,
        # ET date -> [first bar start, last bar start] (swing multi-day coverage)
        "days": {},
    }


def fold_bar(exc: Dict[str, Any], ts: datetime, high: float, low: float) -> None:
    ts_iso = iso(ts)
    last = parse_ts(exc.get("last_bar"))
    if last is not None:
        if ts <= last:
            return  # duplicate or out-of-order bar
        if _et_date(ts) == _et_date(last):  # overnight/weekend gaps are not missing data
            gap = int(round((ts - last).total_seconds() / 60.0)) - 1
            exc["max_gap_min"] = max(int(exc.get("max_gap_min") or 0), gap)
    exc["prev"] = {k: exc.get(k) for k in ("high", "low", "high_at", "low_at", "last_bar", "bars")}
    days = exc.setdefault("days", {})
    day_key = _et_date(ts).isoformat()
    if day_key in days:
        days[day_key][1] = ts_iso
    elif len(days) < 60:
        days[day_key] = [ts_iso, ts_iso]
    if exc.get("first_bar") is None:
        exc["first_bar"] = ts_iso
    exc["last_bar"] = ts_iso
    exc["bars"] = int(exc.get("bars") or 0) + 1
    exc["last_bar_high"], exc["last_bar_low"] = float(high), float(low)
    if exc.get("high") is None or high > exc["high"]:
        exc["high"], exc["high_at"] = float(high), ts_iso
    if exc.get("low") is None or low < exc["low"]:
        exc["low"], exc["low_at"] = float(low), ts_iso


def fold_price(exc: Dict[str, Any], price: float, ts: Any) -> None:
    if price is None or price <= 0:
        return
    for target in (exc, exc.get("prev")):
        if not target:
            continue
        if target.get("high") is None or price > target["high"]:
            target["high"], target["high_at"] = float(price), iso(ts)
        if target.get("low") is None or price < target["low"]:
            target["low"], target["low_at"] = float(price), iso(ts)


def _missing_session_days(days: Dict[str, Any], entry_at: datetime, exit_at: datetime) -> List[str]:
    """Trading days between entry and exit whose bars do not reach both session ends."""
    from backend.app.core.trading_windows import is_trading_day, session_close
    global _ET
    _et_date(entry_at)  # initialises _ET
    out: List[str] = []
    d, end = _et_date(entry_at), _et_date(exit_at)
    while d <= end and len(out) < 60:
        if is_trading_day(d):
            open_et = datetime(d.year, d.month, d.day, 9, 30, tzinfo=_ET)
            close_t = session_close(d)
            last_minute = datetime(d.year, d.month, d.day, close_t.hour, close_t.minute, tzinfo=_ET) - timedelta(minutes=1)
            want_first = (minute_floor(entry_at) + timedelta(minutes=1)) if d == _et_date(entry_at) else open_et
            want_last = (minute_floor(exit_at) - timedelta(minutes=1)) if d == _et_date(exit_at) else last_minute
            if want_last >= want_first:
                got = days.get(d.isoformat())
                if not got:
                    out.append(f"{d.isoformat()} no bars")
                else:
                    first, last = parse_ts(got[0]), parse_ts(got[1])
                    if first > want_first + timedelta(minutes=1) or last < want_last:
                        out.append(f"{d.isoformat()} partial")
        d += timedelta(days=1)
    return out


def excursion_summary(
    exc: Dict[str, Any], side: str, avg_entry: float, risk_per_share: Optional[float],
    entry_at: Optional[datetime], exit_at: Optional[datetime], started_with_trade: bool,
    session_gaps_only: bool = False,
) -> Dict[str, Any]:
    """MFE/MAE in dollars per share and in R, with an honest coverage verdict.

    Whole minutes strictly between the entry minute and the exit minute are
    counted, plus every fill price. The exit-minute bar is reported separately
    (``exit_bar_high/low``) because part of it can come after the exit.
    """
    long = side.upper() in ("LONG", "BUY")
    exc = dict(exc)
    exit_bar = None
    last = parse_ts(exc.get("last_bar"))
    if exit_at is not None and last is not None and last >= minute_floor(exit_at) and exc.get("prev"):
        exit_bar = {"start": exc.get("last_bar"), "high": exc.get("last_bar_high"), "low": exc.get("last_bar_low")}
        exc.update(exc["prev"])
    high, low = exc.get("high"), exc.get("low")
    mfe = mae = None
    if high is not None and low is not None and avg_entry:
        mfe = (high - avg_entry) if long else (avg_entry - low)
        mae = (avg_entry - low) if long else (high - avg_entry)
        mfe, mae = max(0.0, mfe), max(0.0, mae)
    first, last = parse_ts(exc.get("first_bar")), parse_ts(exc.get("last_bar"))
    notes: List[str] = []
    complete = bool(started_with_trade)
    if not started_with_trade:
        notes.append("tracking started after entry (restart from older checkpoint or deploy)")
    if entry_at is not None and exit_at is not None:
        whole_minutes = (minute_floor(exit_at) - minute_floor(entry_at)).total_seconds() / 60.0 - 1
        if whole_minutes >= 1:
            if first is None or last is None:
                complete = False
                notes.append("no whole bar between entry and exit minutes")
            else:
                if first > minute_floor(entry_at) + timedelta(minutes=1):
                    complete = False
                    notes.append("missing bars right after entry")
                if last < minute_floor(exit_at) - timedelta(minutes=1) and not session_gaps_only:
                    complete = False
                    notes.append("missing bars before exit")
        else:
            notes.append("entry and exit within two minutes; fills only")
    if exit_at is not None and last is not None and last >= minute_floor(exit_at):
        complete = False
        notes.append("bars after the exit were folded (exit booked late)")
    if session_gaps_only and entry_at is not None and exit_at is not None:
        missing = _missing_session_days(exc.get("days") or {}, entry_at, exit_at)
        if missing:
            complete = False
            notes.append("incomplete sessions: " + ", ".join(missing[:10]))
    if int(exc.get("max_gap_min") or 0) > 1:
        complete = False
        notes.append(f"gap of {exc.get('max_gap_min')} minutes between bars in one session")
    return {
        "mfe_per_share": rnd(mfe), "mae_per_share": rnd(mae),
        "mfe_r": rnd(mfe / risk_per_share, 3) if mfe is not None and risk_per_share else None,
        "mae_r": rnd(mae / risk_per_share, 3) if mae is not None and risk_per_share else None,
        "high": rnd(high), "low": rnd(low),
        "high_at": exc.get("high_at"), "low_at": exc.get("low_at"),
        "mfe_at": exc.get("high_at") if long else exc.get("low_at"),
        "mae_at": exc.get("low_at") if long else exc.get("high_at"),
        "bars": int(exc.get("bars") or 0),
        "max_gap_min": int(exc.get("max_gap_min") or 0),
        "exit_bar_high": rnd((exit_bar or {}).get("high")),
        "exit_bar_low": rnd((exit_bar or {}).get("low")),
        "coverage_complete": complete,
        "coverage_notes": notes,
        "method": (
            "1-minute bar highs/lows for every whole minute strictly between the entry minute and "
            "the exit minute, plus every fill price. The exit-minute bar is set aside in "
            "exit_bar_high/low because part of it can come after the exit. Censored at the actual "
            "exit: it cannot show what a wider stop or farther target would have done afterwards."
        ),
    }
