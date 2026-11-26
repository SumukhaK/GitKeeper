"""Crash-safe, path-fenced SQLite storage for operational telemetry.

The telemetry store is deliberately separate from the queue control plane. It
records only fixed lifecycle facts: identifiers, enum values, integer
durations/counts, validated model identifiers, fixed error categories, and UTC
timestamps. Prompts, code, diffs, paths, command output, exception text,
credentials, and environment values are never accepted or persisted.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from gitkeeper.queue.errors import SchemaMismatch, StorageCorrupt, StorageUnavailable
from gitkeeper.queue.models import (
    AttemptOutcome,
    ErrorCategory,
    OperationalError,
    QueueEvent,
    TelemetryViewAdapter,
    WorkerState,
    _model_id,
)

_SCHEMA_VERSION = 1
_DATABASE_NAME = "gitkeeper-telemetry.sqlite3"
_BUSY_TIMEOUT_MS = 5_000
_MAX_RETENTION_DAYS = 90
_DEFAULT_RETENTION_DAYS = 90
_RETENTION_KEY = "retention_days"
_UNKNOWN_OUTCOME = "unknown"

_TERMINAL_OUTCOMES = (
    AttemptOutcome.completed,
    AttemptOutcome.model_unavailable,
    AttemptOutcome.failed,
    AttemptOutcome.cancelled,
    AttemptOutcome.recovered_unknown,
)
_TERMINAL_VALUES = tuple(outcome.value for outcome in _TERMINAL_OUTCOMES)

_EVENT_COLUMNS = (
    "event_id",
    "timestamp",
    "duration_ms",
    "repository",
    "pr_number",
    "head_sha",
    "cycle_id",
    "from_state",
    "to_state",
    "attempt_id",
    "invocation_count",
    "outcome",
    "model_id",
    "error_category",
    "identity_key",
)
_IMMUTABLE_COLUMNS = (
    "timestamp",
    "duration_ms",
    "repository",
    "pr_number",
    "head_sha",
    "cycle_id",
    "from_state",
    "to_state",
    "attempt_id",
    "outcome",
    "model_id",
    "error_category",
)

_SCHEMA = """
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    duration_ms INTEGER,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    attempt_id TEXT,
    invocation_count INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    model_id TEXT,
    error_category TEXT,
    identity_key TEXT NOT NULL
);
CREATE UNIQUE INDEX one_telemetry_event_per_identity ON events (identity_key);
CREATE INDEX telemetry_events_by_timestamp ON events (timestamp);
CREATE INDEX telemetry_events_by_pull_request ON events (repository, pr_number);
CREATE TABLE settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);
"""


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _utc(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise _invalid(field)
    return value


def _duration(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise _invalid("duration_ms")
    return value


def _invocation_count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value not in (0, 1):
        raise _invalid("invocation_count")
    return value


def _retention(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_RETENTION_DAYS:
        raise _invalid("retention_days")
    return value


def _attempt_outcome(value: object) -> AttemptOutcome | None:
    if value is None:
        return None
    if not isinstance(value, AttemptOutcome):
        raise _invalid("outcome")
    return value


def _model(value: object) -> str | None:
    if value is None:
        return None
    return _model_id(value)


def _fence(
    state_dir: Path, protected_repository_roots: Iterable[Path]
) -> tuple[Path, Path]:
    try:
        roots = tuple(Path(root) for root in protected_repository_roots)
        if not roots:
            raise StorageUnavailable()
        resolved_roots = tuple(root.resolve(strict=False) for root in roots)
        if len(set(resolved_roots)) != len(resolved_roots):
            raise StorageUnavailable()
        state = Path(state_dir)
        absolute_state = Path(os.path.abspath(os.fspath(state_dir)))
        resolved_state = state.resolve(strict=False)
        for root, resolved_root in zip(roots, resolved_roots):
            absolute_root = Path(os.path.abspath(os.fspath(root)))
            if (
                _inside(absolute_state, absolute_root)
                or _inside(resolved_state, resolved_root)
                or _inside(state, root)
            ):
                raise StorageUnavailable()
        if state.exists() and not state.is_dir():
            raise StorageUnavailable()
        database = state / _DATABASE_NAME
        if _is_reparse_point(database):
            raise StorageUnavailable()
    except StorageUnavailable:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise StorageUnavailable() from None
    return database, state


class TelemetryStore:
    """Own one path-fenced telemetry connection and serialize each write.

    Deduplication uses ``event_id`` plus a semantic identity: invocation
    events collapse per attempt and outcome, while attempt-less closure events
    collapse per repository, pull request, cycle, and transition.
    """

    def __init__(
        self,
        state_dir: Path,
        protected_repository_roots: Iterable[Path],
        retention_days: int = _DEFAULT_RETENTION_DAYS,
    ) -> None:
        # The constructor value seeds a new database only. Retention is
        # monotonic per database and may only be shortened afterwards.
        initial_retention = _retention(retention_days)
        database, state = _fence(state_dir, protected_repository_roots)
        self.path = database
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            state.mkdir(parents=True, exist_ok=True)
            if database.exists():
                self._preflight(database)
            connection = sqlite3.connect(
                str(database),
                timeout=_BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection = connection
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            version = self._version(connection)
            if version == 0:
                self._initialize(connection, initial_retention)
            elif version != _SCHEMA_VERSION:
                raise SchemaMismatch()
            self._check_schema(connection)
            if self._stored_retention(connection) is None:
                raise SchemaMismatch()
        except (SchemaMismatch, StorageUnavailable):
            self.close()
            raise
        except sqlite3.DatabaseError as error:
            self.close()
            if isinstance(error, (sqlite3.OperationalError, sqlite3.IntegrityError)):
                raise StorageUnavailable() from None
            raise StorageCorrupt() from None
        except OSError:
            self.close()
            raise StorageUnavailable() from None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @staticmethod
    def _version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _check_schema(connection: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != {"events", "settings"}:
            raise SchemaMismatch()
        events = tuple(row[1] for row in connection.execute("PRAGMA table_info(events)"))
        if events != _EVENT_COLUMNS:
            raise SchemaMismatch()
        settings = tuple(row[1] for row in connection.execute("PRAGMA table_info(settings)"))
        if settings != ("setting_key", "setting_value"):
            raise SchemaMismatch()

    @classmethod
    def _preflight(cls, database: Path) -> None:
        connection = None
        try:
            uri = database.resolve(strict=True).as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=1)
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise StorageCorrupt()
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, _SCHEMA_VERSION):
                raise SchemaMismatch()
            if version == 0:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if tables:
                    raise SchemaMismatch()
            else:
                cls._check_schema(connection)
        except (SchemaMismatch, StorageCorrupt):
            raise
        except sqlite3.DatabaseError as error:
            if isinstance(error, sqlite3.OperationalError):
                raise StorageUnavailable() from None
            raise StorageCorrupt() from None
        except OSError:
            raise StorageUnavailable() from None
        finally:
            if connection is not None:
                connection.close()

    def _initialize(self, connection: sqlite3.Connection, retention_days: int) -> None:
        connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA)
        connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        connection.execute(
            "INSERT INTO settings(setting_key, setting_value) VALUES (?, ?)",
            (_RETENTION_KEY, str(retention_days)),
        )
        connection.commit()

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.rollback()
        except sqlite3.DatabaseError:
            pass

    @staticmethod
    def _storage_error(error: sqlite3.DatabaseError) -> StorageUnavailable | StorageCorrupt:
        if isinstance(error, sqlite3.OperationalError):
            return StorageUnavailable()
        return StorageCorrupt()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except sqlite3.DatabaseError as error:
                self._rollback(connection)
                raise self._storage_error(error) from None
            except BaseException:
                self._rollback(connection)
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        with self._lock:
            try:
                yield connection
            except sqlite3.DatabaseError as error:
                raise self._storage_error(error) from None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageUnavailable()
        return self._connection

    @staticmethod
    def _stored_retention(connection: sqlite3.Connection) -> int | None:
        row = connection.execute(
            "SELECT setting_value FROM settings WHERE setting_key=?", (_RETENTION_KEY,)
        ).fetchone()
        if row is None:
            return None
        try:
            value = int(row[0])
        except (TypeError, ValueError):
            raise SchemaMismatch() from None
        if not 0 <= value <= _MAX_RETENTION_DAYS:
            raise SchemaMismatch()
        return value

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #

    def record(
        self,
        event: QueueEvent,
        *,
        duration_ms: int | None = None,
        invocation_count: int | None = None,
        outcome: AttemptOutcome | None = None,
        model_id: str | None = None,
        error: OperationalError | None = None,
    ) -> None:
        if not isinstance(event, QueueEvent):
            raise _invalid("event")
        duration = _duration(duration_ms)
        count = _invocation_count(invocation_count)
        attempt_outcome = _attempt_outcome(outcome)
        identifier = _model(model_id)
        persisted_error = self._persisted_error(event, error)
        timestamp = _utc(event.occurred_at, "occurred_at")
        facts = self._candidate(
            event, timestamp, duration, count, attempt_outcome, identifier, persisted_error
        )
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT " + ", ".join(_EVENT_COLUMNS) + " FROM events "
                "WHERE event_id=? OR identity_key=? LIMIT 2",
                (facts["event_id"], facts["identity_key"]),
            ).fetchall()
            if existing:
                if all(self._same_facts(row, facts) for row in existing):
                    return
                raise ValueError("Conflicting telemetry facts.")
            resolved = self._lifecycle(connection, event, facts)
            connection.execute(
                "INSERT INTO events ("
                + ", ".join(_EVENT_COLUMNS)
                + ") VALUES ("
                + ", ".join("?" * len(_EVENT_COLUMNS))
                + ")",
                tuple(resolved[column] for column in _EVENT_COLUMNS),
            )

    @staticmethod
    def _persisted_error(
        event: QueueEvent, error: OperationalError | None
    ) -> OperationalError | None:
        if error is not None and not isinstance(error, OperationalError):
            raise _invalid("error")
        if event.error is not None and not isinstance(event.error, OperationalError):
            raise _invalid("error")
        if (
            error is not None
            and event.error is not None
            and error.category is not event.error.category
        ):
            raise _invalid("error")
        return error if error is not None else event.error

    @staticmethod
    def _candidate(
        event: QueueEvent,
        timestamp: datetime,
        duration_ms: int | None,
        invocation_count: int | None,
        outcome: AttemptOutcome | None,
        model_id: str | None,
        error: OperationalError | None,
    ) -> dict[str, object]:
        category = None if error is None else error.category
        if category is not None:
            derived = (
                AttemptOutcome.model_unavailable
                if category is ErrorCategory.model_unavailable
                else AttemptOutcome.failed
            )
            if outcome is not None and outcome is not derived:
                raise _invalid("outcome")
        if outcome is not None:
            outcome_value = outcome.value
        elif category is ErrorCategory.model_unavailable:
            outcome_value = AttemptOutcome.model_unavailable.value
        elif category is not None:
            outcome_value = AttemptOutcome.failed.value
        elif event.attempt_id is not None and invocation_count == 0:
            outcome_value = AttemptOutcome.reserved.value
        elif event.attempt_id is not None and event.to_state is WorkerState.running:
            outcome_value = AttemptOutcome.running.value
        else:
            outcome_value = _UNKNOWN_OUTCOME
        if event.attempt_id is None:
            if outcome_value in (
                AttemptOutcome.reserved.value,
                AttemptOutcome.running.value,
            ):
                raise _invalid("outcome")
            identity = "\x1f".join(
                (
                    "closure",
                    event.repository,
                    str(event.pr_number),
                    event.cycle_id,
                    event.from_state.value,
                    event.to_state.value,
                )
            )
        else:
            identity = "\x1f".join(("attempt", event.attempt_id, outcome_value))
        return {
            "event_id": event.event_id,
            "timestamp": timestamp.isoformat(),
            "duration_ms": duration_ms,
            "repository": event.repository,
            "pr_number": event.pr_number,
            "head_sha": event.head_sha,
            "cycle_id": event.cycle_id,
            "from_state": event.from_state.value,
            "to_state": event.to_state.value,
            "attempt_id": event.attempt_id,
            "invocation_count": invocation_count,
            "outcome": outcome_value,
            "model_id": model_id,
            "error_category": None if category is None else category.value,
            "identity_key": identity,
        }

    @staticmethod
    def _same_facts(row: tuple, facts: dict[str, object]) -> bool:
        stored = dict(zip(_EVENT_COLUMNS, row))
        for column in _IMMUTABLE_COLUMNS:
            if stored[column] != facts[column]:
                return False
        if facts["invocation_count"] is not None:
            if stored["invocation_count"] != facts["invocation_count"]:
                return False
        return True

    @staticmethod
    def _attempt_started(connection: sqlite3.Connection, attempt_id: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM events WHERE attempt_id=? AND invocation_count=1 LIMIT 1",
            (attempt_id,),
        ).fetchone()
        return row is not None

    @classmethod
    def _lifecycle(
        cls, connection: sqlite3.Connection, event: QueueEvent, facts: dict[str, object]
    ) -> dict[str, object]:
        outcome = facts["outcome"]
        count = facts["invocation_count"]
        attempt_id = facts["attempt_id"]
        if attempt_id is None:
            if event.from_state is not WorkerState.running and event.to_state is WorkerState.running:
                raise _invalid("attempt_id")
            if count not in (None, 0):
                raise _invalid("invocation_count")
            resolved = 0
        else:
            started = cls._attempt_started(connection, attempt_id)
            if outcome == AttemptOutcome.reserved.value:
                if count not in (None, 0) or started:
                    raise _invalid("invocation_count")
                if facts["duration_ms"] is not None:
                    raise _invalid("duration_ms")
                if facts["model_id"] is not None:
                    raise _invalid("model_id")
                resolved = 0
            elif outcome == AttemptOutcome.running.value:
                if count not in (None, 1):
                    raise _invalid("invocation_count")
                resolved = 1
            else:
                required = 1 if started else 0
                if count not in (None, required):
                    raise _invalid("invocation_count")
                if outcome in _TERMINAL_VALUES:
                    cls._reject_other_terminal(connection, attempt_id, outcome)
                resolved = required
        facts["invocation_count"] = resolved
        return facts

    @staticmethod
    def _reject_other_terminal(
        connection: sqlite3.Connection, attempt_id: str, outcome: str
    ) -> None:
        placeholders = ", ".join("?" * len(_TERMINAL_VALUES))
        row = connection.execute(
            f"SELECT 1 FROM events WHERE attempt_id=? AND outcome IN ({placeholders}) "
            "AND outcome<>? LIMIT 1",
            (attempt_id, *_TERMINAL_VALUES, outcome),
        ).fetchone()
        if row is not None:
            raise ValueError("Conflicting terminal outcome.")

    # ------------------------------------------------------------------ #
    # Reading and retention
    # ------------------------------------------------------------------ #

    def list_events(self, after: datetime | None = None) -> list[TelemetryViewAdapter]:
        cutoff = None if after is None else _utc(after, "after").isoformat()
        with self._read() as connection:
            if cutoff is None:
                rows = connection.execute(
                    "SELECT " + ", ".join(_EVENT_COLUMNS) + " FROM events "
                    "ORDER BY timestamp, rowid"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT " + ", ".join(_EVENT_COLUMNS) + " FROM events "
                    "WHERE timestamp > ? ORDER BY timestamp, rowid",
                    (cutoff,),
                ).fetchall()
        return [self._view(row) for row in rows]

    @staticmethod
    def _view(row: tuple) -> TelemetryViewAdapter:
        stored = dict(zip(_EVENT_COLUMNS, row))
        try:
            return TelemetryViewAdapter(
                {
                    "timestamp": datetime.fromisoformat(stored["timestamp"]),
                    "duration_ms": stored["duration_ms"],
                    "repository": stored["repository"],
                    "pr_number": stored["pr_number"],
                    "head_sha": stored["head_sha"],
                    "from_state": stored["from_state"],
                    "to_state": stored["to_state"],
                    "invocation_count": stored["invocation_count"],
                    "outcome": stored["outcome"],
                    "model_id": stored["model_id"],
                    "error_category": stored["error_category"],
                }
            )
        except (TypeError, ValueError):
            raise StorageCorrupt() from None

    def get_retention_days(self) -> int:
        with self._read() as connection:
            stored = self._stored_retention(connection)
        if stored is None:
            raise SchemaMismatch()
        return stored

    def set_retention_days(self, days: int) -> None:
        value = _retention(days)
        with self._transaction() as connection:
            current = self._stored_retention(connection)
            if current is None:
                raise SchemaMismatch()
            if value > current:
                raise ValueError("Retention can only be shortened.")
            connection.execute(
                "UPDATE settings SET setting_value=? WHERE setting_key=?",
                (str(value), _RETENTION_KEY),
            )

    def expire(self, now: datetime) -> int:
        timestamp = _utc(now, "now")
        with self._transaction() as connection:
            retention = self._stored_retention(connection)
            if retention is None:
                raise SchemaMismatch()
            cutoff = (timestamp - timedelta(days=retention)).isoformat()
            deleted = connection.execute(
                "DELETE FROM events WHERE timestamp < ?", (cutoff,)
            ).rowcount
        return int(deleted)
