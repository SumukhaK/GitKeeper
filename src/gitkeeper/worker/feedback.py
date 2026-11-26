"""Restricted external feedback persistence and exact claim binding."""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from gitkeeper.host_mode.readiness import is_repository_name
from gitkeeper.queue.errors import SchemaMismatch, StorageCorrupt, StorageUnavailable
from gitkeeper.queue.models import ClaimResult
from gitkeeper.queue.ports import FeedbackBinding, FeedbackPayload
from gitkeeper.worker import win_acl
from gitkeeper.worker.models import FeedbackRecord

_SCHEMA_VERSION = 1
_DATABASE_NAME = "gitkeeper-feedback.sqlite3"
_BUSY_TIMEOUT_MS = 5_000
_REPARSE_POINT = 0x400

_SCHEMA = """
CREATE TABLE feedback (
    feedback_id TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempt_id TEXT
);
CREATE UNIQUE INDEX one_bound_feedback
    ON feedback (attempt_id) WHERE attempt_id IS NOT NULL;
CREATE INDEX feedback_by_cycle
    ON feedback (repository, pr_number, cycle_id, created_at, feedback_id);
"""


class FileSecurity(Protocol):
    def create_directory(self, path: Path) -> None: ...

    def create_file(self, path: Path) -> None: ...

    def verify_directory(self, path: Path) -> None: ...

    def verify_file(self, path: Path) -> None: ...


class WindowsFileSecurity:
    def create_directory(self, path: Path) -> None:
        win_acl.create_owner_only_directory(path)

    def create_file(self, path: Path) -> None:
        win_acl.create_owner_only_file(path)

    def verify_directory(self, path: Path) -> None:
        win_acl.verify_owner_only_directory(path)

    def verify_file(self, path: Path) -> None:
        win_acl.verify_owner_only_file(path)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _uuid4(value: object, field: str) -> str:
    if not isinstance(value, str) or value != value.lower():
        raise ValueError(f"Invalid {field}.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid {field}.") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"Invalid {field}.")
    return value


def _text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StorageCorrupt()
    return parsed.astimezone(timezone.utc)


def _binding(row: tuple[object, ...]) -> FeedbackBinding:
    return FeedbackBinding(
        feedback_id=str(row[0]),
        repository=str(row[1]),
        pr_number=int(row[2]),
        head_sha=str(row[3]),
        cycle_id=str(row[4]),
        text=str(row[5]),
        created_at=_timestamp(str(row[6])),
        attempt_id=None if row[7] is None else str(row[7]),
    )


_SELECT = (
    "SELECT feedback_id, repository, pr_number, head_sha, cycle_id, text, "
    "created_at, attempt_id FROM feedback"
)


class FeedbackStore:
    """Own one owner-only SQLite connection for restricted feedback."""

    def __init__(
        self,
        state_dir: Path,
        protected_repository_roots: Iterable[Path],
        *,
        security: FileSecurity | None = None,
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
            if _is_reparse_point(state):
                raise StorageUnavailable()
            database = state / _DATABASE_NAME
            if _is_reparse_point(database):
                raise StorageUnavailable()
        except StorageUnavailable:
            raise
        except (OSError, RuntimeError, TypeError, ValueError):
            raise StorageUnavailable() from None

        self.path = database
        self._state = state
        self._security = security or WindowsFileSecurity()
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            self._security.create_directory(state)
            if database.exists():
                self._security.verify_file(database)
            else:
                self._security.create_file(database)
            self._security.verify_directory(state)
            self._security.verify_file(database)
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
        if "feedback" not in actual:
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

    def put(self, payload: FeedbackRecord | FeedbackPayload) -> FeedbackBinding:
        if isinstance(payload, FeedbackRecord):
            record = payload
        elif isinstance(payload, FeedbackPayload):
            record = FeedbackRecord(
                feedback_id=str(uuid4()),
                repository=payload.repository,
                pr_number=payload.pr_number,
                head_sha=payload.head_sha,
                cycle_id=payload.cycle_id,
                text=payload.text,
                created_at=payload.created_at,
                attempt_id=None,
            )
        else:
            raise TypeError(
                "payload must be a FeedbackRecord or FeedbackPayload."
            )

        binding = FeedbackBinding(
            feedback_id=record.feedback_id,
            repository=record.repository,
            pr_number=record.pr_number,
            head_sha=record.head_sha,
            cycle_id=record.cycle_id,
            text=record.text,
            created_at=record.created_at,
            attempt_id=record.attempt_id,
        )
        with self.transaction() as database:
            existing = database.execute(
                f"{_SELECT} WHERE feedback_id=?", (record.feedback_id,)
            ).fetchone()
            if existing is not None:
                stored = _binding(existing)
                if (
                    stored.repository != binding.repository
                    or stored.pr_number != binding.pr_number
                    or stored.head_sha != binding.head_sha
                    or stored.cycle_id != binding.cycle_id
                    or stored.text != binding.text
                    or stored.created_at != binding.created_at
                    or stored.attempt_id != binding.attempt_id
                ):
                    raise ValueError(
                        "Feedback conflicts with the stored feedback identity."
                    )
                return stored
            if binding.attempt_id is not None:
                bound = database.execute(
                    "SELECT feedback_id FROM feedback WHERE attempt_id=?",
                    (binding.attempt_id,),
                ).fetchone()
                if bound is not None:
                    raise ValueError(
                        "The attempt is already bound to other feedback."
                    )
            database.execute(
                "INSERT INTO feedback(feedback_id, repository, pr_number, "
                "head_sha, cycle_id, text, created_at, attempt_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    binding.feedback_id,
                    binding.repository,
                    binding.pr_number,
                    binding.head_sha,
                    binding.cycle_id,
                    binding.text,
                    _text(binding.created_at),
                    binding.attempt_id,
                ),
            )
            return binding

    def bind_for_claim(self, claim: ClaimResult) -> FeedbackBinding | None:
        if not isinstance(claim, ClaimResult):
            raise TypeError("claim must be a ClaimResult.")
        key = claim.lease.key
        cycle_id = claim.attempt.cycle_id
        attempt_id = claim.attempt.attempt_id
        with self.transaction() as database:
            existing = database.execute(
                f"{_SELECT} WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if existing is not None:
                binding = _binding(existing)
                if (
                    binding.repository != key.repository
                    or binding.pr_number != key.pr_number
                    or binding.head_sha != key.head_sha
                    or binding.cycle_id != cycle_id
                ):
                    raise ValueError(
                        "Feedback binding does not match the claim identity."
                    )
                return binding

            row = database.execute(
                f"{_SELECT} WHERE repository=? AND pr_number=? AND head_sha=? "
                "AND cycle_id=? AND attempt_id IS NULL "
                "ORDER BY created_at DESC, feedback_id DESC LIMIT 1",
                (key.repository, key.pr_number, key.head_sha, cycle_id),
            ).fetchone()
            if row is None:
                return None
            feedback_id = str(row[0])
            database.execute(
                "UPDATE feedback SET attempt_id=? "
                "WHERE feedback_id=? AND attempt_id IS NULL",
                (attempt_id, feedback_id),
            )
            return _binding(
                database.execute(
                    f"{_SELECT} WHERE feedback_id=?", (feedback_id,)
                ).fetchone()
            )

    def list_for_cycle(
        self, repository: str, pr_number: int, cycle_id: str
    ) -> tuple[FeedbackBinding, ...]:
        if not isinstance(repository, str) or not is_repository_name(repository):
            raise ValueError("Invalid repository.")
        if type(pr_number) is not int or pr_number < 1:
            raise ValueError("Invalid pr_number.")
        _uuid4(cycle_id, "cycle_id")
        with self.read() as database:
            rows = database.execute(
                f"{_SELECT} WHERE repository=? AND pr_number=? AND cycle_id=? "
                "ORDER BY created_at, feedback_id",
                (repository, pr_number, cycle_id),
            ).fetchall()
            return tuple(_binding(row) for row in rows)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
