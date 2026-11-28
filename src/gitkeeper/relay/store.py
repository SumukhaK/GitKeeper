"""Small metadata-only SQLite store for the pull-request relay."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from gitkeeper.queue.errors import SchemaMismatch
from gitkeeper.relay.state import Phase, PrRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pull_requests (
    repository TEXT NOT NULL, number INTEGER NOT NULL,
    head_sha TEXT NOT NULL, phase TEXT NOT NULL,
    review_rounds INTEGER NOT NULL, ci_attempts INTEGER NOT NULL,
    reviewed_sha TEXT, blocked_reason TEXT, worker_prompt TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY (repository, number)
);
CREATE TABLE IF NOT EXISTS events (
    repository TEXT NOT NULL, number INTEGER NOT NULL,
    at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""

_RECORD_COLUMNS = (
    "repository, number, head_sha, phase, review_rounds, ci_attempts, reviewed_sha, blocked_reason"
)


class RelayStore:
    def __init__(self, path: Path) -> None:
        resolved = Path(path).expanduser().resolve()
        if any((parent / ".git").exists() for parent in resolved.parents):
            raise ValueError("Relay database must be outside a git checkout.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(resolved)
        self._db.row_factory = sqlite3.Row
        try:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if (
                version == 0
                and self._db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()
            ):
                raise SchemaMismatch()
            if version not in (0, 1):
                raise SchemaMismatch()
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(_SCHEMA)
            self._db.execute("PRAGMA user_version = 1")
            self._db.commit()
        except Exception:
            self._db.close()
            raise

    @staticmethod
    def _record(row: sqlite3.Row) -> PrRecord:
        return PrRecord(
            repository=row["repository"],
            number=row["number"],
            head_sha=row["head_sha"],
            phase=Phase(row["phase"]),
            review_rounds=row["review_rounds"],
            ci_attempts=row["ci_attempts"],
            reviewed_sha=row["reviewed_sha"],
            blocked_reason=row["blocked_reason"],
        )

    def get(self, repo: str, number: int) -> PrRecord | None:
        row = self._db.execute(
            f"SELECT {_RECORD_COLUMNS} FROM pull_requests WHERE repository = ? AND number = ?",
            (repo, number),
        ).fetchone()
        return self._record(row) if row is not None else None

    def get_prompt(self, repo: str, number: int) -> str | None:
        row = self._db.execute(
            "SELECT worker_prompt FROM pull_requests WHERE repository = ? AND number = ?",
            (repo, number),
        ).fetchone()
        return row["worker_prompt"] if row is not None else None

    def put(self, record: PrRecord, *, worker_prompt: str | None = None) -> None:
        values = (
            record.repository,
            record.number,
            record.head_sha,
            record.phase.value,
            record.review_rounds,
            record.ci_attempts,
            record.reviewed_sha,
            record.blocked_reason,
            worker_prompt,
            datetime.now(timezone.utc).isoformat(),
        )
        with self._db:
            self._db.execute(
                "INSERT INTO pull_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repository, number) DO UPDATE SET "
                "head_sha = excluded.head_sha, phase = excluded.phase, "
                "review_rounds = excluded.review_rounds, "
                "ci_attempts = excluded.ci_attempts, "
                "reviewed_sha = excluded.reviewed_sha, "
                "blocked_reason = excluded.blocked_reason, "
                "worker_prompt = COALESCE(excluded.worker_prompt, worker_prompt), "
                "updated_at = excluded.updated_at",
                values,
            )

    def record_event(
        self,
        repo: str,
        number: int,
        kind: str,
        detail: str = "",
        *,
        at: datetime | None = None,
    ) -> None:
        at = at or datetime.now(timezone.utc)
        with self._db:
            self._db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (repo, number, at.astimezone(timezone.utc).isoformat(), kind, detail),
            )

    def list_active(self) -> list[PrRecord]:
        rows = self._db.execute(
            f"SELECT {_RECORD_COLUMNS} FROM pull_requests "
            "WHERE phase != 'done' ORDER BY repository, number"
        ).fetchall()
        return [self._record(row) for row in rows]

    def metrics(self, repo: str, number: int) -> dict[str, int]:
        row = self._db.execute(
            "SELECT review_rounds, ci_attempts FROM pull_requests "
            "WHERE repository = ? AND number = ?",
            (repo, number),
        ).fetchone()
        if row is None:
            return {"review_rounds": 0, "ci_attempts": 0}
        return {"review_rounds": row["review_rounds"], "ci_attempts": row["ci_attempts"]}

    def close(self) -> None:
        self._db.close()
