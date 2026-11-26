"""External SQLite registry for preserved worker worktrees.

The registry stores only identity, path, and preservation metadata. It never
stores source text, patches, prompts, credentials, or process output.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from gitkeeper.queue.errors import SchemaMismatch, StorageCorrupt, StorageUnavailable
from gitkeeper.worker.models import WorktreeIdentity, WorktreeRecord

_SCHEMA_VERSION = 1
_DATABASE_NAME = "gitkeeper-worktrees.sqlite3"
_BUSY_TIMEOUT_MS = 5_000
_REPARSE_POINT = 0x400

_SCHEMA = """
CREATE TABLE worktrees (
    worktree_id TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    branch TEXT,
    created_at TEXT NOT NULL,
    preserved_reason TEXT,
    UNIQUE (repository, pr_number, head_sha, cycle_id)
);
CREATE INDEX worktrees_by_pr
    ON worktrees (repository, pr_number, created_at);
"""


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StorageCorrupt()
    return parsed.astimezone(timezone.utc)


def _record(row: tuple[object, ...]) -> WorktreeRecord:
    return WorktreeRecord(
        worktree_id=str(row[0]),
        identity=WorktreeIdentity(
            repository=str(row[1]),
            pr_number=int(row[2]),
            head_sha=str(row[3]),
            cycle_id=str(row[4]),
        ),
        path=Path(str(row[5])),
        branch=None if row[6] is None else str(row[6]),
        created_at=_timestamp(str(row[7])),
        preserved_reason=None if row[8] is None else str(row[8]),
    )


_SELECT = (
    "SELECT worktree_id, repository, pr_number, head_sha, cycle_id, path, "
    "branch, created_at, preserved_reason FROM worktrees"
)


class WorktreeStore:
    """Own one external SQLite connection for worktree records."""

    def __init__(
        self, state_dir: Path, protected_repository_roots: Iterable[Path]
    ) -> None:
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
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
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
        connection.commit()

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "worktrees" not in actual:
            raise SchemaMismatch()

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.rollback()
        except sqlite3.DatabaseError:
            pass

    @staticmethod
    def _storage_error(error: sqlite3.DatabaseError) -> StorageUnavailable:
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

    def put(self, record: WorktreeRecord) -> None:
        if not isinstance(record, WorktreeRecord):
            raise TypeError("record must be a WorktreeRecord.")
        with self.transaction() as database:
            existing = database.execute(
                f"{_SELECT} WHERE worktree_id=?", (record.worktree_id,)
            ).fetchone()
            if existing is not None:
                stored = _record(existing)
                if (
                    stored.identity != record.identity
                    or stored.path != record.path
                    or stored.branch != record.branch
                    or stored.created_at != record.created_at
                ):
                    raise ValueError(
                        "Worktree record conflicts with its stored identity."
                    )
                database.execute(
                    "UPDATE worktrees SET preserved_reason=? WHERE worktree_id=?",
                    (record.preserved_reason, record.worktree_id),
                )
                return

            identity = record.identity
            conflict = database.execute(
                f"{_SELECT} WHERE repository=? AND pr_number=? AND head_sha=? "
                "AND cycle_id=?",
                (
                    identity.repository,
                    identity.pr_number,
                    identity.head_sha,
                    identity.cycle_id,
                ),
            ).fetchone()
            if conflict is not None:
                raise ValueError(
                    "A different worktree is already registered for this identity."
                )
            path_conflict = database.execute(
                "SELECT worktree_id FROM worktrees WHERE path=?",
                (str(record.path),),
            ).fetchone()
            if path_conflict is not None:
                raise ValueError("The worktree path is already registered.")
            database.execute(
                "INSERT INTO worktrees(worktree_id, repository, pr_number, "
                "head_sha, cycle_id, path, branch, created_at, preserved_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.worktree_id,
                    identity.repository,
                    identity.pr_number,
                    identity.head_sha,
                    identity.cycle_id,
                    str(record.path),
                    record.branch,
                    _text(record.created_at),
                    record.preserved_reason,
                ),
            )

    def get(self, identity: WorktreeIdentity) -> WorktreeRecord | None:
        if not isinstance(identity, WorktreeIdentity):
            raise TypeError("identity must be a WorktreeIdentity.")
        with self.read() as database:
            row = database.execute(
                f"{_SELECT} WHERE repository=? AND pr_number=? AND head_sha=? "
                "AND cycle_id=?",
                (
                    identity.repository,
                    identity.pr_number,
                    identity.head_sha,
                    identity.cycle_id,
                ),
            ).fetchone()
            return None if row is None else _record(row)

    def get_by_id(self, worktree_id: str) -> WorktreeRecord | None:
        if not isinstance(worktree_id, str):
            return None
        with self.read() as database:
            row = database.execute(
                f"{_SELECT} WHERE worktree_id=?", (worktree_id,)
            ).fetchone()
            return None if row is None else _record(row)

    def list_for_pr(
        self, repository: str, pr_number: int
    ) -> tuple[WorktreeRecord, ...]:
        with self.read() as database:
            rows = database.execute(
                f"{_SELECT} WHERE repository=? AND pr_number=? "
                "ORDER BY created_at, worktree_id",
                (repository, pr_number),
            ).fetchall()
            return tuple(_record(row) for row in rows)

    def list_all(self) -> tuple[WorktreeRecord, ...]:
        with self.read() as database:
            rows = database.execute(
                f"{_SELECT} ORDER BY created_at, worktree_id"
            ).fetchall()
            return tuple(_record(row) for row in rows)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
