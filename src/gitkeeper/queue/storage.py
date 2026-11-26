"""Crash-safe, path-fenced SQLite storage for local queue metadata."""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from gitkeeper.queue.errors import SchemaMismatch, StorageCorrupt, StorageUnavailable

_SCHEMA_VERSION = 1
_DATABASE_NAME = "gitkeeper-queue.sqlite3"
_BUSY_TIMEOUT_MS = 5_000
_REPARSE_POINT = 0x400

_SCHEMA = """
CREATE TABLE repair_cycles (
    cycle_id TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    cycle_number INTEGER NOT NULL,
    initial_head_sha TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE (repository, pr_number, cycle_number)
);
CREATE TABLE pr_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    author TEXT,
    head_repository TEXT,
    head_sha TEXT NOT NULL,
    is_open INTEGER NOT NULL,
    is_draft INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    is_current INTEGER NOT NULL,
    UNIQUE (repository, pr_number, observed_at)
);
CREATE UNIQUE INDEX one_current_observation
    ON pr_observations (repository, pr_number) WHERE is_current = 1;
CREATE TABLE queue_items (
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    author TEXT,
    head_repository TEXT,
    current_head_sha TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES repair_cycles(cycle_id),
    eligible INTEGER NOT NULL,
    checks_json TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    remote_labels_json TEXT NOT NULL,
    automatic_attempt_count INTEGER NOT NULL,
    manual_retry_count INTEGER NOT NULL,
    last_error_category TEXT,
    last_error_occurred_at TEXT,
    updated_at TEXT NOT NULL,
    item_revision INTEGER NOT NULL,
    projection_revision INTEGER NOT NULL,
    projection_committed_revision INTEGER NOT NULL,
    projection_committed_at TEXT,
    PRIMARY KEY (repository, pr_number)
);
CREATE TABLE retry_requests (
    authorization TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES repair_cycles(cycle_id),
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
CREATE TABLE leases (
    lease_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL,
    attempt_id TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES repair_cycles(cycle_id),
    retry_authorization TEXT REFERENCES retry_requests(authorization),
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    cancelled_at TEXT
);
CREATE UNIQUE INDEX one_active_lease ON leases ((1)) WHERE cancelled_at IS NULL;
CREATE TABLE invocations (
    attempt_id TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL UNIQUE REFERENCES leases(lease_id),
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES repair_cycles(cycle_id),
    kind TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    model_id TEXT,
    error_category TEXT,
    error_occurred_at TEXT,
    cancellation_reason TEXT
);
CREATE TABLE preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);
CREATE TABLE publication_intents (
    intent_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES invocations(attempt_id),
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES repair_cycles(cycle_id),
    worktree_id TEXT NOT NULL,
    starting_sha TEXT NOT NULL,
    local_commit_sha TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE uncertain_publications (
    publication_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES invocations(attempt_id),
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES repair_cycles(cycle_id),
    worktree_id TEXT NOT NULL,
    local_commit_sha TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    CHECK ((resolved_at IS NULL) = (resolution IS NULL))
);
CREATE UNIQUE INDEX one_unresolved_publication
    ON uncertain_publications (repository, pr_number, cycle_id)
    WHERE resolved_at IS NULL;
CREATE TABLE manual_retry_latches (
    latch_id TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES repair_cycles(cycle_id),
    attempt_id TEXT NOT NULL REFERENCES invocations(attempt_id),
    category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cleared_at TEXT,
    cleared_by_authorization TEXT REFERENCES retry_requests(authorization),
    CHECK ((cleared_at IS NULL) = (cleared_by_authorization IS NULL))
);
CREATE UNIQUE INDEX one_active_latch
    ON manual_retry_latches (repository, pr_number, cycle_id)
    WHERE cleared_at IS NULL;
"""


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class SQLiteStorage:
    """Own one connection and serialize each write with ``BEGIN IMMEDIATE``."""

    def __init__(self, state_dir: Path, protected_repository_roots: Iterable[Path]) -> None:
        try:
            roots = tuple(Path(root) for root in protected_repository_roots)
            if not roots:
                raise StorageUnavailable()
            resolved_roots = tuple(root.resolve(strict=False) for root in roots)
            if len(set(resolved_roots)) != len(resolved_roots):
                raise StorageUnavailable()
            state = Path(state_dir).resolve(strict=False)
            if any(_inside(state, root) for root in resolved_roots):
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
            if self._version(connection) == 0:
                self._initialize(connection)
            elif self._version(connection) != _SCHEMA_VERSION:
                raise SchemaMismatch()
            self._verify_schema(connection)
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

    @staticmethod
    def _version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _preflight(database: Path) -> None:
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
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if tables:
                    raise SchemaMismatch()
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

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA)
        connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        connection.execute(
            "INSERT INTO preferences(preference_key, preference_value) VALUES ('paused', '0')"
        )
        connection.commit()

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        required = {
            "repair_cycles", "pr_observations", "queue_items", "leases", "invocations",
            "retry_requests", "preferences", "publication_intents", "uncertain_publications",
            "manual_retry_latches",
        }
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not required <= actual:
            raise SchemaMismatch()

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
    def transaction(self) -> Iterator[sqlite3.Connection]:
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
    def read(self) -> Iterator[sqlite3.Connection]:
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

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
