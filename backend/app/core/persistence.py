"""Durable runtime checkpoints and immutable trade/session history.

The trading engine is intentionally a singleton.  A SQLite database on a
Railway volume gives that singleton an atomic, crash-resistant ledger without
introducing a second application writer during deployments.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
import base64
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import fcntl
import hashlib
import importlib
import json
from pathlib import Path
import logging
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel

log = logging.getLogger(__name__)


SCHEMA_VERSION = 2
ALLOWED_TYPE_PREFIX = "backend.app."


class PersistenceError(RuntimeError):
    """Raised when durable state cannot be trusted."""


def _qualified_name(value: type) -> str:
    return f"{value.__module__}:{value.__qualname__}"


def encode_runtime_value(value: Any) -> Any:
    """Encode internal runtime values into type-tagged JSON-safe data."""
    # String-valued enums must be tagged before the primitive string check.
    if isinstance(value, Enum):
        return {
            "__type__": "enum",
            "class": _qualified_name(value.__class__),
            "value": value.value,
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if is_dataclass(value):
        dataclass_fields = fields(value)
        return {
            "__type__": "dataclass",
            "class": _qualified_name(value.__class__),
            "fields": {
                field.name: encode_runtime_value(getattr(value, field.name))
                for field in dataclass_fields
                if field.init
            },
            "post_fields": {
                field.name: encode_runtime_value(getattr(value, field.name))
                for field in dataclass_fields
                if not field.init
            },
        }
    if isinstance(value, BaseModel):
        return {
            "__type__": "pydantic",
            "class": _qualified_name(value.__class__),
            "fields": encode_runtime_value(value.model_dump(mode="python")),
        }
    if isinstance(value, tuple):
        return {"__type__": "tuple", "items": [encode_runtime_value(v) for v in value]}
    if isinstance(value, set):
        return {"__type__": "set", "items": [encode_runtime_value(v) for v in sorted(value, key=str)]}
    if isinstance(value, list):
        return [encode_runtime_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): encode_runtime_value(v) for k, v in value.items()}
    raise TypeError(f"Unsupported runtime value for persistence: {type(value)!r}")


def _load_internal_type(path: str) -> type:
    module_name, separator, qualname = path.partition(":")
    if not separator or not module_name.startswith(ALLOWED_TYPE_PREFIX):
        raise PersistenceError(f"Refusing untrusted persisted type: {path!r}")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in qualname.split("."):
        value = getattr(value, part)
    if not isinstance(value, type):
        raise PersistenceError(f"Persisted type is not a class: {path!r}")
    return value


def decode_runtime_value(value: Any) -> Any:
    """Decode data produced by :func:`encode_runtime_value`."""
    if isinstance(value, list):
        return [decode_runtime_value(v) for v in value]
    if not isinstance(value, dict):
        return value
    value_type = value.get("__type__")
    if not value_type:
        return {k: decode_runtime_value(v) for k, v in value.items()}
    if value_type == "datetime":
        parsed = datetime.fromisoformat(value["value"])
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    if value_type == "date":
        return date.fromisoformat(value["value"])
    if value_type == "tuple":
        return tuple(decode_runtime_value(v) for v in value["items"])
    if value_type == "set":
        return set(decode_runtime_value(v) for v in value["items"])
    if value_type == "enum":
        return _load_internal_type(value["class"])(value["value"])
    if value_type in ("dataclass", "pydantic"):
        cls = _load_internal_type(value["class"])
        decoded_fields = decode_runtime_value(value["fields"])
        if value_type == "pydantic":
            return cls.model_validate(decoded_fields)
        instance = cls(**decoded_fields)
        for field_name, field_value in decode_runtime_value(value.get("post_fields", {})).items():
            setattr(instance, field_name, field_value)
        return instance
    raise PersistenceError(f"Unknown persisted value type: {value_type!r}")


class TradingStateStore:
    """SQLite-backed atomic checkpoint plus append-only completed-trade ledger."""

    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._lease_path = Path(f"{self.path}.writer.lock")
        self._lease_handle = self._lease_path.open("a+")
        try:
            fcntl.flock(self._lease_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lease_handle.close()
            raise PersistenceError(
                f"Another trading-engine writer already owns {self.path}"
            ) from exc
        try:
            self._connection = sqlite3.connect(
                str(self.path), timeout=15.0, check_same_thread=False, isolation_level=None
            )
        except Exception:
            fcntl.flock(self._lease_handle.fileno(), fcntl.LOCK_UN)
            self._lease_handle.close()
            raise
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=15000")
        self._initialize_schema()
        self.last_checkpoint_at: Optional[str] = None
        self.restored_at: Optional[str] = None

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_checkpoint (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    revision INTEGER NOT NULL,
                    schema_version INTEGER NOT NULL,
                    saved_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS completed_trades (
                    trade_id TEXT PRIMARY KEY,
                    session_date TEXT NOT NULL,
                    closed_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_completed_trades_session_closed
                    ON completed_trades(session_date, closed_at DESC, trade_id DESC);
                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_date TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_key TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMMITTED')),
                    created_at TEXT NOT NULL,
                    committed_at TEXT
                );
                """
            )
            row = self._connection.execute(
                "SELECT schema_version FROM schema_info WHERE singleton = 1"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO schema_info(singleton, schema_version) VALUES (1, ?)",
                    (SCHEMA_VERSION,),
                )
            elif int(row["schema_version"]) == 1:
                columns = {
                    column["name"]
                    for column in self._connection.execute("PRAGMA table_info(processed_events)")
                }
                if "payload" not in columns:
                    self._connection.executescript(
                        """
                        ALTER TABLE processed_events RENAME TO processed_events_v1;
                        CREATE TABLE processed_events (
                            event_key TEXT PRIMARY KEY,
                            event_type TEXT NOT NULL,
                            payload TEXT NOT NULL,
                            status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMMITTED')),
                            created_at TEXT NOT NULL,
                            committed_at TEXT
                        );
                        INSERT INTO processed_events
                            (event_key, event_type, payload, status, created_at, committed_at)
                        SELECT event_key, event_type, '{}', 'COMMITTED', committed_at, committed_at
                        FROM processed_events_v1;
                        DROP TABLE processed_events_v1;
                        """
                    )
                self._connection.execute(
                    "UPDATE schema_info SET schema_version = ? WHERE singleton = 1",
                    (SCHEMA_VERSION,),
                )
            elif int(row["schema_version"]) != SCHEMA_VERSION:
                raise PersistenceError(
                    f"Unsupported persistence schema {row['schema_version']}; expected {SCHEMA_VERSION}"
                )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_processed_events_status_committed "
                "ON processed_events(status, committed_at)"
            )

    @staticmethod
    def _canonical_json(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _checksum(raw_payload: str) -> str:
        return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

    def has_checkpoint(self) -> bool:
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM runtime_checkpoint WHERE singleton = 1"
            ).fetchone() is not None

    def load_checkpoint(self) -> Optional[Tuple[Dict[str, Any], int, str]]:
        with self._lock:
            row = self._connection.execute(
                "SELECT revision, schema_version, saved_at, checksum, payload "
                "FROM runtime_checkpoint WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return None
        if int(row["schema_version"]) != SCHEMA_VERSION:
            raise PersistenceError(
                f"Checkpoint schema {row['schema_version']} is not supported"
            )
        raw_payload = str(row["payload"])
        if self._checksum(raw_payload) != row["checksum"]:
            raise PersistenceError("Runtime checkpoint checksum mismatch")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise PersistenceError("Runtime checkpoint JSON is corrupt") from exc
        self.last_checkpoint_at = str(row["saved_at"])
        self.restored_at = datetime.now(timezone.utc).isoformat()
        return payload, int(row["revision"]), str(row["saved_at"])

    def save_checkpoint(
        self,
        payload: Dict[str, Any],
        reason: str,
        trades: Iterable[Dict[str, Any]] = (),
        session_summaries: Iterable[Dict[str, Any]] = (),
        processed_events: Iterable[Tuple[str, str]] = (),
    ) -> Tuple[int, int]:
        """Commit checkpoint and immutable ledger rows in one SQLite transaction."""
        raw_payload = self._canonical_json(payload)
        checksum = self._checksum(raw_payload)
        now = datetime.now(timezone.utc).isoformat()
        inserted_trades = 0
        with self._lock:
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                for trade in trades:
                    trade_raw = self._canonical_json(trade)
                    result = connection.execute(
                        "INSERT OR IGNORE INTO completed_trades"
                        "(trade_id, session_date, closed_at, payload, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            trade["trade_id"],
                            trade["session_date"],
                            trade["closed_at"],
                            trade_raw,
                            now,
                        ),
                    )
                    inserted_trades += max(0, result.rowcount)

                for summary in session_summaries:
                    summary_raw = self._canonical_json(summary)
                    connection.execute(
                        "INSERT OR IGNORE INTO session_summaries"
                        "(session_date, payload, source, created_at) VALUES (?, ?, ?, ?)",
                        (
                            summary["session_date"],
                            summary_raw,
                            summary.get("source", "SYSTEM"),
                            now,
                        ),
                    )

                row = connection.execute(
                    "SELECT revision FROM runtime_checkpoint WHERE singleton = 1"
                ).fetchone()
                revision = (int(row["revision"]) if row else 0) + 1
                connection.execute(
                    "INSERT INTO runtime_checkpoint"
                    "(singleton, revision, schema_version, saved_at, reason, checksum, payload) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "revision=excluded.revision, schema_version=excluded.schema_version, "
                    "saved_at=excluded.saved_at, reason=excluded.reason, "
                    "checksum=excluded.checksum, payload=excluded.payload",
                    (revision, SCHEMA_VERSION, now, reason, checksum, raw_payload),
                )
                for event_key, event_type in processed_events:
                    result = connection.execute(
                        "UPDATE processed_events SET status = 'COMMITTED', committed_at = ? "
                        "WHERE event_key = ? AND event_type = ? AND status = 'PENDING'",
                        (now, event_key, event_type),
                    )
                    if result.rowcount != 1:
                        existing = connection.execute(
                            "SELECT status FROM processed_events WHERE event_key = ? AND event_type = ?",
                            (event_key, event_type),
                        ).fetchone()
                        if existing is None:
                            raise PersistenceError(
                                f"Cannot commit market event without durable inbox row: {event_key}"
                            )
                # The inbox is for crash recovery and short-horizon dedupe, not
                # permanent audit history. Immutable trades live separately.
                cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                connection.execute(
                    "DELETE FROM processed_events "
                    "WHERE status = 'COMMITTED' AND committed_at < ?",
                    (cutoff,),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        self.last_checkpoint_at = now
        if revision % 100 == 0:
            self.wal_checkpoint("PASSIVE")
        return revision, inserted_trades

    def begin_event(self, event_key: str, event_type: str, payload: Any) -> bool:
        """Durably stage an input before it may mutate trading state.

        Returns false only when the exact event was already committed. A pending
        event remains replayable after an abrupt process or deployment stop.
        """
        now = datetime.now(timezone.utc).isoformat()
        raw_payload = self._canonical_json({"event": encode_runtime_value(payload)})
        with self._lock:
            row = self._connection.execute(
                "SELECT event_type, payload, status FROM processed_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if row is not None:
                if row["event_type"] != event_type or row["payload"] != raw_payload:
                    raise PersistenceError(f"Market event key collision: {event_key}")
                return row["status"] != "COMMITTED"
            self._connection.execute(
                "INSERT INTO processed_events"
                "(event_key, event_type, payload, status, created_at, committed_at) "
                "VALUES (?, ?, ?, 'PENDING', ?, NULL)",
                (event_key, event_type, raw_payload, now),
            )
        return True

    def list_pending_events(self) -> List[Tuple[str, str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_key, event_type, payload FROM processed_events "
                "WHERE status = 'PENDING' ORDER BY created_at, event_key"
            ).fetchall()
        events: List[Tuple[str, str, Any]] = []
        for row in rows:
            try:
                wrapper = json.loads(row["payload"])
                payload = decode_runtime_value(wrapper["event"])
            except Exception as exc:
                raise PersistenceError(
                    f"Pending market event {row['event_key']} is corrupt"
                ) from exc
            events.append((str(row["event_key"]), str(row["event_type"]), payload))
        return events

    def import_session_summary(self, summary: Dict[str, Any]) -> None:
        """Import a verified aggregate without inventing per-trade executions."""
        now = datetime.now(timezone.utc).isoformat()
        raw = self._canonical_json(summary)
        with self._lock:
            self._connection.execute(
                "INSERT INTO session_summaries(session_date, payload, source, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_date) DO UPDATE SET payload=excluded.payload, source=excluded.source",
                (summary["session_date"], raw, summary.get("source", "LEGACY_SUMMARY_IMPORT"), now),
            )

    def list_trades(
        self,
        start_date: Optional[str],
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        clauses: List[str] = []
        params: List[Any] = []
        if start_date:
            clauses.append("session_date >= ?")
            params.append(start_date)
        if cursor:
            try:
                cursor_values = json.loads(
                    base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
                )
                cursor_closed_at, cursor_trade_id = cursor_values
            except Exception as exc:
                raise PersistenceError("Invalid trade history cursor") from exc
            clauses.append("(closed_at < ? OR (closed_at = ? AND trade_id < ?))")
            params.extend([cursor_closed_at, cursor_closed_at, cursor_trade_id])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit + 1)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload, closed_at, trade_id FROM completed_trades {where} "
                "ORDER BY closed_at DESC, trade_id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        selected = rows[:limit]
        next_cursor = None
        if len(rows) > limit and selected:
            marker = json.dumps(
                [selected[-1]["closed_at"], selected[-1]["trade_id"]],
                separators=(",", ":"),
            ).encode("utf-8")
            next_cursor = base64.urlsafe_b64encode(marker).decode("ascii")
        return [json.loads(row["payload"]) for row in selected], next_cursor

    def list_session_summaries(self, start_date: Optional[str]) -> List[Dict[str, Any]]:
        query = "SELECT payload FROM session_summaries"
        params: Tuple[Any, ...] = ()
        if start_date:
            query += " WHERE session_date >= ?"
            params = (start_date,)
        query += " ORDER BY session_date DESC"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def list_trades_for_session(self, session_date: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM completed_trades WHERE session_date = ? ORDER BY closed_at, trade_id",
                (session_date,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def aggregate_trade_stats(self, start_date: Optional[str]) -> Dict[str, Any]:
        query = "SELECT payload FROM completed_trades"
        params: Tuple[Any, ...] = ()
        if start_date:
            query += " WHERE session_date >= ?"
            params = (start_date,)
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        pnl_values: List[float] = []
        fees = 0.0
        for row in rows:
            trade = json.loads(row["payload"])
            pnl_values.append(float(trade.get("realized_pnl", 0.0)))
            fees += float(trade.get("fees", 0.0))
        return {
            "trades_count": len(pnl_values),
            "wins": sum(1 for pnl in pnl_values if pnl > 0),
            "losses": sum(1 for pnl in pnl_values if pnl < 0),
            "realized_pnl": sum(pnl_values),
            "fees": fees,
        }

    def trade_count(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM completed_trades").fetchone()
        return int(row["count"])

    def integrity_check(self) -> None:
        with self._lock:
            row = self._connection.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise PersistenceError(f"SQLite integrity check failed: {row[0] if row else 'no result'}")

    def backup(self, destination: str) -> Path:
        destination_path = Path(destination).expanduser().resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(destination_path))
            try:
                self._connection.backup(target)
            finally:
                target.close()
        return destination_path

    def wal_checkpoint(self, mode: str = "PASSIVE") -> None:
        """Execute SQLite WAL checkpoint to flush write-ahead log pages."""
        mode_upper = mode.upper()
        if mode_upper not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
            mode_upper = "PASSIVE"
        with self._lock:
            if not self._closed and self._connection:
                try:
                    self._connection.execute(f"PRAGMA wal_checkpoint({mode_upper})")
                except Exception as exc:
                    log.warning("WAL checkpoint (%s) failed: %s", mode_upper, exc)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:
                log.warning("Final WAL truncate checkpoint failed: %s", exc)
            self._connection.close()
            fcntl.flock(self._lease_handle.fileno(), fcntl.LOCK_UN)
            self._lease_handle.close()
            self._closed = True
