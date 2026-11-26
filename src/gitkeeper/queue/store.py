"""Metadata-only queue persistence and recovery transitions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gitkeeper.host_mode.readiness import MAX_AUTOMATIC_INVOCATIONS, OWNER_LOGIN
from gitkeeper.queue.errors import (
    AttemptBudgetExhausted,
    ErrorCategory,
    LocalLeaseConflict,
    QueueError,
    RemoteStale,
    RetryExpired,
    RetryInvalid,
    RetryReplayed,
    StaleHead,
    StorageUnavailable,
)
from gitkeeper.queue.models import (
    AttemptOutcome,
    CheckConclusion,
    CheckResult,
    CheckState,
    ClaimResult,
    InvocationAttempt,
    InvocationKind,
    Lease,
    ManualRetryLatch,
    OperationalError,
    ProjectionFence,
    PublicationIntent,
    PullRequestSnapshot,
    QueueItem,
    QueueKey,
    RepairCycle,
    RepairReason,
    RetryAuthorization,
    RetryKind,
    UncertainPublication,
    WorkerLabel,
    WorkerState,
    new_uuid4,
)
from gitkeeper.queue.models import (
    _enum as _valid_enum,
    _integer as _valid_integer,
    _model_id as _valid_model_id,
    _repository as _valid_repository,
    _utc as _valid_utc,
    _uuid4 as _valid_uuid,
)
from gitkeeper.queue.storage import SQLiteStorage

_AUTOMATIC_OWNER = "gitkeeper-automatic"
_LATCH_CATEGORIES = {
    ErrorCategory.worker_failed,
    ErrorCategory.worker_unavailable,
    ErrorCategory.model_unavailable,
}
_TERMINAL_OUTCOMES = {
    AttemptOutcome.completed,
    AttemptOutcome.model_unavailable,
    AttemptOutcome.failed,
    AttemptOutcome.cancelled,
    AttemptOutcome.recovered_unknown,
}


def _now(value: datetime) -> datetime:
    return _valid_utc(value, "now")


def _text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _error(category: str | None, occurred_at: str | None) -> OperationalError | None:
    if category is None:
        return None
    parsed = ErrorCategory(category)
    return OperationalError(
        category=parsed,
        message=str(QueueError(parsed)),
        occurred_at=_time(occurred_at),
    )


def _key(repository: str, pr_number: int, head_sha: str) -> QueueKey:
    return QueueKey(
        repository=_valid_repository(repository),
        pr_number=_valid_integer(pr_number, "pr_number", 1),
        head_sha=head_sha,
    )


def _lease_seconds(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid lease_seconds.")
    return value


def _cycle(row: tuple) -> RepairCycle:
    return RepairCycle(
        cycle_id=row[0],
        cycle_number=row[1],
        initial_key=_key(row[2], row[3], row[4]),
        opened_at=_time(row[5]),
        closed_at=_time(row[6]),
    )


def _snapshot(row: tuple) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        repository=row[0],
        pr_number=row[1],
        author=row[2],
        head_repository=row[3],
        head_sha=row[4],
        is_open=bool(row[5]),
        is_draft=bool(row[6]),
        observed_at=_time(row[7]),
    )


def _lease(row: tuple) -> Lease:
    return Lease(
        lease_id=row[0],
        generation=row[1],
        attempt_id=row[2],
        owner=row[3],
        key=_key(row[4], row[5], row[6]),
        cycle_id=row[7],
        retry_authorization=row[8],
        claimed_at=_time(row[9]),
        expires_at=_time(row[10]),
        cancelled_at=_time(row[11]),
    )


def _attempt(row: tuple) -> InvocationAttempt:
    return InvocationAttempt(
        attempt_id=row[0],
        lease_id=row[1],
        key=_key(row[2], row[3], row[4]),
        cycle_id=row[5],
        kind=InvocationKind(row[6]),
        outcome=AttemptOutcome(row[7]),
        reserved_at=_time(row[8]),
        started_at=_time(row[9]),
        finished_at=_time(row[10]),
        model_id=row[11],
        error=_error(row[12], row[13]),
    )


def _authorization(row: tuple) -> RetryAuthorization:
    return RetryAuthorization(
        authorization=row[0],
        request_id=row[1],
        key=_key(row[2], row[3], row[4]),
        cycle_id=row[5],
        owner=row[6],
        kind=RetryKind(row[7]),
        created_at=_time(row[8]),
        expires_at=_time(row[9]),
        consumed_at=_time(row[10]),
    )


def _intent(row: tuple) -> PublicationIntent:
    return PublicationIntent(
        intent_id=row[0],
        attempt_id=row[1],
        key=_key(row[2], row[3], row[4]),
        cycle_id=row[5],
        worktree_id=row[6],
        starting_sha=row[7],
        local_commit_sha=row[8],
        recorded_at=_time(row[9]),
    )


def _publication(row: tuple) -> UncertainPublication:
    return UncertainPublication(
        publication_id=row[0],
        attempt_id=row[1],
        key=_key(row[2], row[3], row[4]),
        cycle_id=row[5],
        worktree_id=row[6],
        local_commit_sha=row[7],
        recorded_at=_time(row[8]),
        resolved_at=_time(row[9]),
        resolution=row[10],
    )


def _latch(row: tuple) -> ManualRetryLatch:
    return ManualRetryLatch(
        latch_id=row[0],
        key=_key(row[1], row[2], row[3]),
        cycle_id=row[4],
        attempt_id=row[5],
        category=ErrorCategory(row[6]),
        created_at=_time(row[7]),
        cleared_at=_time(row[8]),
        cleared_by_authorization=row[9],
    )


def _item(row: tuple) -> QueueItem:
    checks_data = json.loads(row[8])
    check_values = CheckState(
        head_sha=checks_data["head_sha"],
        required_check_ids=tuple(checks_data["required_check_ids"]),
        results=tuple(
            CheckResult(
                check_id=result["check_id"],
                check_run_id=result["check_run_id"],
                head_sha=result["head_sha"],
                conclusion=CheckConclusion(result["conclusion"]),
            )
            for result in checks_data["results"]
        ),
    )
    return QueueItem(
        repository=row[0],
        pr_number=row[1],
        author=row[2],
        head_repository=row[3],
        current_head_sha=row[4],
        observed_at=_time(row[5]),
        cycle_id=row[6],
        eligible=bool(row[7]),
        checks=check_values,
        state=WorkerState(row[9]),
        reason=None if row[10] is None else RepairReason(row[10]),
        remote_labels=tuple(WorkerLabel(label) for label in json.loads(row[11])),
        automatic_attempt_count=row[12],
        manual_retry_count=row[13],
        last_error=_error(row[14], row[15]),
        updated_at=_time(row[16]),
    )



class QueueStore:
    """Durable queue state; no operation launches workers or contacts GitHub."""

    def __init__(self, state_dir: Path, protected_repository_roots: Iterable[Path]) -> None:
        self._storage = SQLiteStorage(state_dir, protected_repository_roots)

    def close(self) -> None:
        self._storage.close()

    def _open_cycle(self, db, repository: str, pr_number: int) -> RepairCycle | None:
        row = db.execute(
            "SELECT cycle_id, cycle_number, repository, pr_number, initial_head_sha, opened_at, closed_at "
            "FROM repair_cycles WHERE repository=? AND pr_number=? AND closed_at IS NULL "
            "ORDER BY cycle_number DESC LIMIT 1",
            (repository, pr_number),
        ).fetchone()
        return None if row is None else _cycle(row)

    def _latest_cycle(self, db, repository: str, pr_number: int) -> RepairCycle | None:
        row = db.execute(
            "SELECT cycle_id, cycle_number, repository, pr_number, initial_head_sha, opened_at, closed_at "
            "FROM repair_cycles WHERE repository=? AND pr_number=? ORDER BY cycle_number DESC LIMIT 1",
            (repository, pr_number),
        ).fetchone()
        return None if row is None else _cycle(row)

    def _cycle_by_id(self, db, cycle_id: str) -> RepairCycle | None:
        row = db.execute(
            "SELECT cycle_id, cycle_number, repository, pr_number, initial_head_sha, opened_at, closed_at "
            "FROM repair_cycles WHERE cycle_id=?",
            (cycle_id,),
        ).fetchone()
        return None if row is None else _cycle(row)

    def _ensure_cycle(self, db, repository: str, pr_number: int, head_sha: str, now: datetime) -> RepairCycle:
        current = self._open_cycle(db, repository, pr_number)
        if current is not None:
            return current
        latest = self._latest_cycle(db, repository, pr_number)
        if latest is not None and latest.closed_at is not None and now <= latest.closed_at:
            raise RemoteStale()
        number = 1 if latest is None else latest.cycle_number + 1
        cycle = RepairCycle(
            cycle_id=new_uuid4(),
            cycle_number=number,
            initial_key=_key(repository, pr_number, head_sha),
            opened_at=now,
            closed_at=None,
        )
        db.execute(
            "INSERT INTO repair_cycles(cycle_id, repository, pr_number, cycle_number, initial_head_sha, opened_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (cycle.cycle_id, repository, pr_number, number, head_sha, _text(now)),
        )
        return cycle

    def open_cycle(self, repository: str, pr_number: int, head_sha: str, now: datetime) -> RepairCycle:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        initial_key = _key(repository, pr_number, head_sha)
        timestamp = _now(now)
        with self._storage.transaction() as db:
            return self._ensure_cycle(db, repository, pr_number, initial_key.head_sha, timestamp)

    def get_open_cycle(self, repository: str, pr_number: int) -> RepairCycle | None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        with self._storage.read() as db:
            return self._open_cycle(db, repository, pr_number)

    def _current_observation(self, db, repository: str, pr_number: int) -> PullRequestSnapshot | None:
        row = db.execute(
            "SELECT repository, pr_number, author, head_repository, head_sha, is_open, is_draft, observed_at "
            "FROM pr_observations WHERE repository=? AND pr_number=? AND is_current=1",
            (repository, pr_number),
        ).fetchone()
        return None if row is None else _snapshot(row)

    def _latest_observation(self, db, repository: str, pr_number: int) -> PullRequestSnapshot | None:
        row = db.execute(
            "SELECT repository, pr_number, author, head_repository, head_sha, is_open, is_draft, observed_at "
            "FROM pr_observations WHERE repository=? AND pr_number=? ORDER BY observed_at DESC LIMIT 1",
            (repository, pr_number),
        ).fetchone()
        return None if row is None else _snapshot(row)

    def close_cycle(self, repository: str, pr_number: int, now: datetime) -> RepairCycle | None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        timestamp = _now(now)
        with self._storage.transaction() as db:
            row = db.execute(
                "SELECT cycle_id, cycle_number, repository, pr_number, initial_head_sha, opened_at, closed_at FROM repair_cycles WHERE repository=? AND pr_number=? ORDER BY cycle_number DESC LIMIT 1",
                (repository, pr_number),
            ).fetchone()
            if row is None:
                return None
            cycle = _cycle(row)
            if cycle.closed_at is not None:
                return cycle
            closed = replace(cycle, closed_at=timestamp)
            row = db.execute(
                "SELECT lease_id, generation, attempt_id, owner, repository, pr_number, head_sha, cycle_id, retry_authorization, claimed_at, expires_at, cancelled_at "
                "FROM leases WHERE repository=? AND pr_number=? AND cycle_id=? AND cancelled_at IS NULL",
                (repository, pr_number, cycle.cycle_id),
            ).fetchone()
            if row is not None:
                lease = _lease(row)
                self._cancel_internal(
                    db, lease, timestamp,
                    require_manual_retry=False, category=ErrorCategory.worker_failed,
                    reason="cycle_close", allow_expired=True,
                )
            db.execute("UPDATE repair_cycles SET closed_at=? WHERE cycle_id=? AND closed_at IS NULL", (_text(timestamp), cycle.cycle_id))
            db.execute(
                "UPDATE uncertain_publications SET resolved_at=?, resolution='superseded' "
                "WHERE repository=? AND pr_number=? AND cycle_id=? AND resolved_at IS NULL",
                (_text(timestamp), repository, pr_number, cycle.cycle_id),
            )
            db.execute(
                "UPDATE pr_observations SET is_current=0 WHERE repository=? AND pr_number=? AND is_current=1",
                (repository, pr_number),
            )
            db.execute("DELETE FROM queue_items WHERE repository=? AND pr_number=?", (repository, pr_number))
            return closed

    def _advance_item_head(self, db, snapshot: PullRequestSnapshot, cycle_id: str) -> None:
        row = self._item_row(db, snapshot.repository, snapshot.pr_number)
        if row is None:
            return
        check_data = json.loads(row[8])
        check_data["head_sha"] = snapshot.head_sha
        check_data["results"] = []
        db.execute(
            "UPDATE queue_items SET author=?, head_repository=?, current_head_sha=?, observed_at=?, cycle_id=?, eligible=0, checks_json=?, "
            "state='ready', reason=NULL, remote_labels_json='[]', automatic_attempt_count=0, manual_retry_count=0, "
            "last_error_category=NULL, last_error_occurred_at=NULL, updated_at=?, item_revision=item_revision+1, projection_revision=projection_revision+1 "
            "WHERE repository=? AND pr_number=?",
            (
                snapshot.author, snapshot.head_repository, snapshot.head_sha, _text(snapshot.observed_at), cycle_id,
                json.dumps(check_data, separators=(",", ":"), sort_keys=True), _text(snapshot.observed_at),
                snapshot.repository, snapshot.pr_number,
            ),
        )

    def put_observation(self, snapshot: PullRequestSnapshot) -> None:
        if not isinstance(snapshot, PullRequestSnapshot):
            raise ValueError("Invalid snapshot.")
        with self._storage.transaction() as db:
            latest_cycle = self._latest_cycle(db, snapshot.repository, snapshot.pr_number)
            if (
                latest_cycle is not None
                and latest_cycle.closed_at is not None
                and snapshot.observed_at <= latest_cycle.closed_at
            ):
                raise RemoteStale()
            latest = self._latest_observation(db, snapshot.repository, snapshot.pr_number)
            if latest is not None and snapshot.observed_at < latest.observed_at:
                raise RemoteStale()
            if latest is not None and snapshot.observed_at == latest.observed_at:
                if latest != snapshot:
                    raise RemoteStale()
                return
            cycle = self._ensure_cycle(
                db, snapshot.repository, snapshot.pr_number, snapshot.head_sha, snapshot.observed_at
            )
            prior = self._current_observation(db, snapshot.repository, snapshot.pr_number)
            if prior is not None and prior.head_sha != snapshot.head_sha:
                self._supersede_head(db, snapshot.repository, snapshot.pr_number, cycle.cycle_id, snapshot.observed_at)
                self._advance_item_head(db, snapshot, cycle.cycle_id)
            db.execute(
                "UPDATE pr_observations SET is_current=0 WHERE repository=? AND pr_number=? AND is_current=1",
                (snapshot.repository, snapshot.pr_number),
            )
            db.execute(
                "INSERT INTO pr_observations(repository, pr_number, author, head_repository, head_sha, is_open, is_draft, observed_at, is_current) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    snapshot.repository, snapshot.pr_number, snapshot.author, snapshot.head_repository,
                    snapshot.head_sha, int(snapshot.is_open), int(snapshot.is_draft), _text(snapshot.observed_at),
                ),
            )
            self._touch_item(db, snapshot.repository, snapshot.pr_number)

    def get_observation(self, repository: str, pr_number: int) -> PullRequestSnapshot | None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        with self._storage.read() as db:
            return self._current_observation(db, repository, pr_number)

    def delete_observation(self, repository: str, pr_number: int) -> None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        with self._storage.transaction() as db:
            changed = db.execute(
                "UPDATE pr_observations SET is_current=0 WHERE repository=? AND pr_number=? AND is_current=1",
                (repository, pr_number),
            ).rowcount
            if changed:
                self._touch_item(db, repository, pr_number)

    def _item_row(self, db, repository: str, pr_number: int):
        return db.execute(
            "SELECT repository, pr_number, author, head_repository, current_head_sha, observed_at, cycle_id, eligible, "
            "checks_json, state, reason, remote_labels_json, automatic_attempt_count, manual_retry_count, "
            "last_error_category, last_error_occurred_at, updated_at, item_revision, projection_revision, "
            "projection_committed_revision, projection_committed_at FROM queue_items WHERE repository=? AND pr_number=?",
            (repository, pr_number),
        ).fetchone()

    def put_item(self, item: QueueItem) -> None:
        if not isinstance(item, QueueItem):
            raise ValueError("Invalid item.")
        with self._storage.transaction() as db:
            cycle = self._cycle_by_id(db, item.cycle_id)
            if cycle is None or cycle.closed_at is not None or cycle.initial_key.repository != item.repository or cycle.initial_key.pr_number != item.pr_number:
                raise RemoteStale()
            observed = self._current_observation(db, item.repository, item.pr_number)
            if observed is not None:
                if observed.head_sha != item.current_head_sha:
                    raise StaleHead()
                if observed.observed_at != item.observed_at:
                    raise RemoteStale()
            existing = self._item_row(db, item.repository, item.pr_number)
            if existing is not None and _item(existing) == item:
                return
            checks_json = json.dumps(
                {
                    "head_sha": item.checks.head_sha,
                    "required_check_ids": list(item.checks.required_check_ids),
                    "results": [
                        {
                            "check_id": result.check_id,
                            "check_run_id": result.check_run_id,
                            "head_sha": result.head_sha,
                            "conclusion": result.conclusion.value,
                        }
                        for result in item.checks.results
                    ],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            values = (
                item.repository, item.pr_number, item.author, item.head_repository,
                item.current_head_sha, _text(item.observed_at), item.cycle_id, int(item.eligible),
                checks_json, item.state.value, None if item.reason is None else item.reason.value,
                json.dumps([label.value for label in item.remote_labels], separators=(",", ":")),
                item.automatic_attempt_count, item.manual_retry_count,
                None if item.last_error is None else item.last_error.category.value,
                None if item.last_error is None else _text(item.last_error.occurred_at),
                _text(item.updated_at),
            )
            if existing is None:
                db.execute(
                    "INSERT INTO queue_items(repository, pr_number, author, head_repository, current_head_sha, observed_at, cycle_id, eligible, "
                    "checks_json, state, reason, remote_labels_json, automatic_attempt_count, manual_retry_count, last_error_category, "
                    "last_error_occurred_at, updated_at, item_revision, projection_revision, projection_committed_revision, projection_committed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, NULL)",
                    values,
                )
            else:
                db.execute(
                    "UPDATE queue_items SET author=?, head_repository=?, current_head_sha=?, observed_at=?, cycle_id=?, eligible=?, checks_json=?, "
                    "state=?, reason=?, remote_labels_json=?, automatic_attempt_count=?, manual_retry_count=?, last_error_category=?, "
                    "last_error_occurred_at=?, updated_at=?, item_revision=item_revision+1, projection_revision=projection_revision+1 "
                    "WHERE repository=? AND pr_number=?",
                    values[2:] + (item.repository, item.pr_number),
                )

    def get_item(self, repository: str, pr_number: int) -> QueueItem | None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        with self._storage.read() as db:
            row = self._item_row(db, repository, pr_number)
            return None if row is None else _item(row)

    def list_items(self) -> list[QueueItem]:
        with self._storage.read() as db:
            rows = db.execute(
                "SELECT repository, pr_number, author, head_repository, current_head_sha, observed_at, cycle_id, eligible, "
                "checks_json, state, reason, remote_labels_json, automatic_attempt_count, manual_retry_count, "
                "last_error_category, last_error_occurred_at, updated_at, item_revision, projection_revision, "
                "projection_committed_revision, projection_committed_at FROM queue_items ORDER BY repository, pr_number"
            ).fetchall()
            return [_item(row) for row in rows]

    def _touch_item(self, db, repository: str, pr_number: int) -> None:
        db.execute(
            "UPDATE queue_items SET item_revision=item_revision+1, projection_revision=projection_revision+1 "
            "WHERE repository=? AND pr_number=?",
            (repository, pr_number),
        )

    def _lease_row(self, db, lease_id: str):
        return db.execute(
            "SELECT lease_id, generation, attempt_id, owner, repository, pr_number, head_sha, cycle_id, retry_authorization, "
            "claimed_at, expires_at, cancelled_at FROM leases WHERE lease_id=?",
            (lease_id,),
        ).fetchone()

    def _attempt_row(self, db, attempt_id: str):
        return db.execute(
            "SELECT attempt_id, lease_id, repository, pr_number, head_sha, cycle_id, kind, outcome, reserved_at, started_at, "
            "finished_at, model_id, error_category, error_occurred_at, cancellation_reason FROM invocations WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()

    def _attempt_by_lease(self, db, lease_id: str):
        row = db.execute(
            "SELECT attempt_id, lease_id, repository, pr_number, head_sha, cycle_id, kind, outcome, reserved_at, started_at, "
            "finished_at, model_id, error_category, error_occurred_at, cancellation_reason FROM invocations WHERE lease_id=?",
            (lease_id,),
        ).fetchone()
        return None if row is None else _attempt(row)

    def get_lease(self, lease_id: str) -> Lease | None:
        lease_id = _valid_uuid(lease_id, "lease_id")
        with self._storage.read() as db:
            row = self._lease_row(db, lease_id)
            return None if row is None else _lease(row)

    def get_attempt(self, attempt_id: str) -> InvocationAttempt | None:
        attempt_id = _valid_uuid(attempt_id, "attempt_id")
        with self._storage.read() as db:
            row = self._attempt_row(db, attempt_id)
            return None if row is None else _attempt(row)

    def _identity(self, db, lease: Lease) -> tuple[Lease, InvocationAttempt] | None:
        if not isinstance(lease, Lease):
            raise ValueError("Invalid lease.")
        row = self._lease_row(db, lease.lease_id)
        if row is None:
            return None
        stored = _lease(row)
        if (
            stored.generation != lease.generation
            or stored.attempt_id != lease.attempt_id
            or stored.key != lease.key
            or stored.cycle_id != lease.cycle_id
        ):
            return None
        attempt = self._attempt_by_lease(db, stored.lease_id)
        if attempt is None or attempt.attempt_id != stored.attempt_id or attempt.key != stored.key or attempt.cycle_id != stored.cycle_id:
            return None
        return stored, attempt

    def _scope_current(self, db, lease: Lease) -> bool:
        cycle = self._cycle_by_id(db, lease.cycle_id)
        if cycle is None or cycle.closed_at is not None or cycle.initial_key.repository != lease.key.repository or cycle.initial_key.pr_number != lease.key.pr_number:
            return False
        observed = self._current_observation(db, lease.key.repository, lease.key.pr_number)
        projected = self._item_row(db, lease.key.repository, lease.key.pr_number)
        if observed is not None and observed.head_sha != lease.key.head_sha:
            return False
        if projected is not None and (projected[4] != lease.key.head_sha or projected[6] != lease.cycle_id):
            return False
        return True

    def _active_latch(self, db, repository: str, pr_number: int, cycle_id: str) -> ManualRetryLatch | None:
        row = db.execute(
            "SELECT latch_id, repository, pr_number, head_sha, cycle_id, attempt_id, category, created_at, cleared_at, cleared_by_authorization "
            "FROM manual_retry_latches WHERE repository=? AND pr_number=? AND cycle_id=? AND cleared_at IS NULL "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (repository, pr_number, cycle_id),
        ).fetchone()
        return None if row is None else _latch(row)

    def _latest_latch(self, db, repository: str, pr_number: int, cycle_id: str) -> ManualRetryLatch | None:
        row = db.execute(
            "SELECT latch_id, repository, pr_number, head_sha, cycle_id, attempt_id, category, created_at, cleared_at, cleared_by_authorization "
            "FROM manual_retry_latches WHERE repository=? AND pr_number=? AND cycle_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (repository, pr_number, cycle_id),
        ).fetchone()
        return None if row is None else _latch(row)

    def _set_latch(self, db, attempt: InvocationAttempt, category: ErrorCategory, now: datetime) -> ManualRetryLatch:
        existing = self._active_latch(db, attempt.key.repository, attempt.key.pr_number, attempt.cycle_id)
        if existing is not None:
            return existing
        latch = ManualRetryLatch(
            latch_id=new_uuid4(),
            key=attempt.key,
            cycle_id=attempt.cycle_id,
            attempt_id=attempt.attempt_id,
            category=category,
            created_at=now,
            cleared_at=None,
            cleared_by_authorization=None,
        )
        db.execute(
            "INSERT INTO manual_retry_latches(latch_id, repository, pr_number, head_sha, cycle_id, attempt_id, category, created_at, cleared_at, cleared_by_authorization) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (latch.latch_id, attempt.key.repository, attempt.key.pr_number, attempt.key.head_sha, attempt.cycle_id, attempt.attempt_id, category.value, _text(now)),
        )
        return latch

    def _clear_latch(self, db, attempt: InvocationAttempt, authorization: str, now: datetime) -> None:
        latch = self._active_latch(db, attempt.key.repository, attempt.key.pr_number, attempt.cycle_id)
        if latch is not None:
            db.execute(
                "UPDATE manual_retry_latches SET cleared_at=?, cleared_by_authorization=? WHERE latch_id=? AND cleared_at IS NULL",
                (_text(now), authorization, latch.latch_id),
            )

    def _save_attempt(self, db, attempt: InvocationAttempt, cancellation_reason: str | None = None) -> None:
        db.execute(
            "UPDATE invocations SET outcome=?, started_at=?, finished_at=?, model_id=?, error_category=?, error_occurred_at=?, cancellation_reason=? "
            "WHERE attempt_id=?",
            (
                attempt.outcome.value, _text(attempt.started_at), _text(attempt.finished_at), attempt.model_id,
                None if attempt.error is None else attempt.error.category.value,
                None if attempt.error is None else _text(attempt.error.occurred_at),
                cancellation_reason, attempt.attempt_id,
            ),
        )

    def _paused(self, db) -> bool:
        row = db.execute("SELECT preference_value FROM preferences WHERE preference_key='paused'").fetchone()
        return row is None or row[0] == "1"

    def _uncancelled_lease(self, db) -> Lease | None:
        row = db.execute(
            "SELECT lease_id, generation, attempt_id, owner, repository, pr_number, head_sha, cycle_id, retry_authorization, "
            "claimed_at, expires_at, cancelled_at FROM leases WHERE cancelled_at IS NULL ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return None if row is None else _lease(row)

    def _recover_internal(self, db, lease: Lease, now: datetime, *, latch: bool, fence_intent: bool = True) -> InvocationAttempt | None:
        identity = self._identity(db, lease)
        if identity is None:
            return None
        stored, attempt = identity
        if stored.cancelled_at is not None or stored.expires_at > now:
            return None
        if attempt.outcome not in (AttemptOutcome.reserved, AttemptOutcome.running):
            return None
        if fence_intent and self._intent_for_attempt(db, attempt.attempt_id, attempt.cycle_id):
            return None
        if attempt.outcome is AttemptOutcome.reserved:
            recovered = attempt.transition_to(AttemptOutcome.cancelled, finished_at=now)
            category = ErrorCategory.worker_unavailable
        else:
            recovered = attempt.recover(
                finished_at=now,
            ) if attempt.error is not None else InvocationAttempt(
                attempt_id=attempt.attempt_id,
                lease_id=attempt.lease_id,
                key=attempt.key,
                cycle_id=attempt.cycle_id,
                kind=attempt.kind,
                outcome=AttemptOutcome.recovered_unknown,
                reserved_at=attempt.reserved_at,
                started_at=attempt.started_at,
                finished_at=now,
                model_id=attempt.model_id,
                error=OperationalError(ErrorCategory.worker_failed, str(QueueError(ErrorCategory.worker_failed)), now),
            )
            category = ErrorCategory.worker_failed
        self._save_attempt(db, recovered, "recovered")
        db.execute("UPDATE leases SET cancelled_at=? WHERE lease_id=? AND cancelled_at IS NULL", (_text(now), stored.lease_id))
        if latch:
            self._set_latch(db, recovered, category, now)
        self._touch_item(db, recovered.key.repository, recovered.key.pr_number)
        return recovered

    def _cancel_internal(self, db, lease: Lease, now: datetime, *, require_manual_retry: bool, category: ErrorCategory, reason: str, allow_expired: bool = False) -> bool:
        identity = self._identity(db, lease)
        if identity is None:
            return False
        stored, attempt = identity
        if require_manual_retry and attempt.kind is InvocationKind.reconcile_unknown:
            return False
        if stored.cancelled_at is not None:
            stored_attempt = self._attempt_row(db, stored.attempt_id)
            return attempt.outcome is AttemptOutcome.cancelled and stored_attempt is not None and stored_attempt[14] == reason
        if (not allow_expired and stored.expires_at <= now) or not self._scope_current(db, stored):
            return False
        if attempt.outcome not in (AttemptOutcome.reserved, AttemptOutcome.running):
            return False
        cancelled = attempt.transition_to(AttemptOutcome.cancelled, finished_at=now)
        self._save_attempt(db, cancelled, reason)
        db.execute("UPDATE leases SET cancelled_at=? WHERE lease_id=?", (_text(now), stored.lease_id))
        if require_manual_retry:
            self._set_latch(db, cancelled, category, now)
        self._touch_item(db, cancelled.key.repository, cancelled.key.pr_number)
        return True

    def _supersede_head(self, db, repository: str, pr_number: int, cycle_id: str, now: datetime) -> None:
        row = db.execute(
            "SELECT lease_id, generation, attempt_id, owner, repository, pr_number, head_sha, cycle_id, retry_authorization, claimed_at, expires_at, cancelled_at "
            "FROM leases WHERE repository=? AND pr_number=? AND cycle_id=? AND cancelled_at IS NULL",
            (repository, pr_number, cycle_id),
        ).fetchone()
        if row is None:
            return
        lease = _lease(row)
        self._cancel_internal(
            db, lease, now,
            require_manual_retry=False, category=ErrorCategory.worker_failed,
            reason="changed_head", allow_expired=True,
        )

    def claim(self, key: QueueKey, cycle_id: str, owner: str, now: datetime, lease_seconds: int, retry_authorization: str | None = None) -> ClaimResult | None:
        if not isinstance(key, QueueKey):
            raise ValueError("Invalid key.")
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        timestamp = _now(now)
        duration = _lease_seconds(lease_seconds)
        if retry_authorization is not None:
            retry_authorization = _valid_uuid(retry_authorization, "retry_authorization")
        with self._storage.transaction() as db:
            authorization = None
            if retry_authorization is not None:
                auth_row = db.execute(
                    "SELECT authorization, request_id, repository, pr_number, head_sha, cycle_id, owner, kind, created_at, expires_at, consumed_at "
                    "FROM retry_requests WHERE authorization=?",
                    (retry_authorization,),
                ).fetchone()
                if auth_row is None:
                    raise RetryInvalid()
                authorization = _authorization(auth_row)
                if authorization.consumed_at is not None:
                    raise RetryReplayed()
                if timestamp >= authorization.expires_at:
                    raise RetryExpired()
                if authorization.key != key or authorization.cycle_id != cycle_id or owner != OWNER_LOGIN or owner != authorization.owner:
                    raise RetryInvalid()
            if self._paused(db):
                return None
            active = self._uncancelled_lease(db)
            if active is not None:
                if active.expires_at > timestamp:
                    raise LocalLeaseConflict()
                if self._recover_internal(db, active, timestamp, latch=True) is None:
                    raise LocalLeaseConflict()
            cycle = self._cycle_by_id(db, cycle_id)
            if cycle is None or cycle.closed_at is not None or cycle.initial_key.repository != key.repository or cycle.initial_key.pr_number != key.pr_number:
                return None
            row = self._item_row(db, key.repository, key.pr_number)
            if row is None or row[6] != cycle_id:
                return None
            projected = _item(row)
            if projected.current_head_sha != key.head_sha:
                raise StaleHead()
            observed = self._current_observation(db, key.repository, key.pr_number)
            if observed is not None and observed.head_sha != key.head_sha:
                raise StaleHead()
            publication = None
            if retry_authorization is None:
                if owner != _AUTOMATIC_OWNER:
                    raise RetryInvalid()
                kind = InvocationKind.automatic
                if self._active_latch(db, key.repository, key.pr_number, cycle_id) is not None:
                    return None
                if self._automatic_count(db, key.repository, key.pr_number, cycle_id) >= MAX_AUTOMATIC_INVOCATIONS:
                    raise AttemptBudgetExhausted()
            else:
                kind = InvocationKind.manual_retry if authorization.kind is RetryKind.manual_retry else InvocationKind.reconcile_unknown
                if kind is InvocationKind.reconcile_unknown:
                    publication_row = db.execute(
                        "SELECT publication_id, attempt_id, repository, pr_number, head_sha, cycle_id, worktree_id, local_commit_sha, recorded_at, resolved_at, resolution "
                        "FROM uncertain_publications WHERE repository=? AND pr_number=? AND cycle_id=? AND resolved_at IS NULL",
                        (key.repository, key.pr_number, cycle_id),
                    ).fetchone()
                    if publication_row is None:
                        raise RetryInvalid()
                    publication = _publication(publication_row)
            if projected.state is not WorkerState.queued:
                if not (kind is InvocationKind.reconcile_unknown and projected.state is WorkerState.blocked and publication is not None):
                    return None
            generation_row = db.execute(
                "SELECT COALESCE(MAX(generation), 0)+1 FROM leases WHERE repository=? AND pr_number=? AND cycle_id=?",
                (key.repository, key.pr_number, cycle_id),
            ).fetchone()
            lease = Lease(
                lease_id=new_uuid4(),
                generation=generation_row[0],
                attempt_id=new_uuid4(),
                owner=owner,
                key=key,
                cycle_id=cycle_id,
                retry_authorization=retry_authorization,
                claimed_at=timestamp,
                expires_at=timestamp + timedelta(seconds=duration),
                cancelled_at=None,
            )
            attempt = InvocationAttempt(
                attempt_id=lease.attempt_id,
                lease_id=lease.lease_id,
                key=key,
                cycle_id=cycle_id,
                kind=kind,
                outcome=AttemptOutcome.reserved,
                reserved_at=timestamp,
                started_at=None,
                finished_at=None,
                model_id=None,
                error=None,
            )
            db.execute(
                "INSERT INTO leases(lease_id, generation, attempt_id, owner, repository, pr_number, head_sha, cycle_id, retry_authorization, claimed_at, expires_at, cancelled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (lease.lease_id, lease.generation, lease.attempt_id, lease.owner, key.repository, key.pr_number, key.head_sha, cycle_id, retry_authorization, _text(timestamp), _text(lease.expires_at)),
            )
            db.execute(
                "INSERT INTO invocations(attempt_id, lease_id, repository, pr_number, head_sha, cycle_id, kind, outcome, reserved_at, started_at, finished_at, model_id, error_category, error_occurred_at, cancellation_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, NULL, NULL, NULL, NULL, NULL, NULL)",
                (attempt.attempt_id, attempt.lease_id, key.repository, key.pr_number, key.head_sha, cycle_id, kind.value, _text(timestamp)),
            )
            if authorization is not None:
                db.execute("UPDATE retry_requests SET consumed_at=? WHERE authorization=? AND consumed_at IS NULL", (_text(timestamp), retry_authorization))
                authorization = replace(authorization, consumed_at=timestamp)
            self._touch_item(db, key.repository, key.pr_number)
            return ClaimResult(lease=lease, attempt=attempt, authorization=authorization, uncertain_publication=publication)

    def renew(self, lease: Lease, now: datetime, lease_seconds: int) -> Lease | None:
        timestamp = _now(now)
        duration = _lease_seconds(lease_seconds)
        with self._storage.transaction() as db:
            identity = self._identity(db, lease)
            if identity is None:
                return None
            stored, _ = identity
            if stored.cancelled_at is not None or stored.expires_at <= timestamp or not self._scope_current(db, stored):
                return None
            expires = max(stored.expires_at, timestamp + timedelta(seconds=duration))
            if expires == stored.expires_at:
                return stored
            renewed = replace(stored, expires_at=expires)
            db.execute("UPDATE leases SET expires_at=? WHERE lease_id=? AND generation=?", (_text(expires), stored.lease_id, stored.generation))
            self._touch_item(db, stored.key.repository, stored.key.pr_number)
            return renewed

    def cancel(self, lease: Lease, now: datetime, *, require_manual_retry: bool = False, category: ErrorCategory = ErrorCategory.worker_failed) -> bool:
        timestamp = _now(now)
        if type(require_manual_retry) is not bool:
            raise ValueError("Invalid require_manual_retry.")
        category = _valid_enum(category, ErrorCategory, "category")
        if require_manual_retry and category not in _LATCH_CATEGORIES:
            raise ValueError("Invalid category.")
        with self._storage.transaction() as db:
            return self._cancel_internal(db, lease, timestamp, require_manual_retry=require_manual_retry, category=category, reason="owner")

    def block_reserved(self, lease: Lease, now: datetime, error: OperationalError) -> bool:
        timestamp = _now(now)
        if not isinstance(error, OperationalError) or error.category is not ErrorCategory.worker_unavailable:
            raise ValueError("Invalid error.")
        with self._storage.transaction() as db:
            identity = self._identity(db, lease)
            if identity is None:
                return False
            stored, attempt = identity
            if attempt.outcome is not AttemptOutcome.reserved or stored.cancelled_at is not None or stored.expires_at <= timestamp or not self._scope_current(db, stored):
                return False
            cancelled = attempt.transition_to(AttemptOutcome.cancelled, finished_at=timestamp)
            self._save_attempt(db, cancelled, "owner")
            db.execute("UPDATE leases SET cancelled_at=? WHERE lease_id=?", (_text(timestamp), stored.lease_id))
            self._set_latch(db, cancelled, ErrorCategory.worker_unavailable, timestamp)
            self._touch_item(db, cancelled.key.repository, cancelled.key.pr_number)
            return True

    def get_active_lease(self, now: datetime) -> Lease | None:
        timestamp = _now(now)
        with self._storage.read() as db:
            lease = self._uncancelled_lease(db)
            return lease if lease is not None and lease.expires_at > timestamp else None

    def get_recoverable_lease(self, now: datetime) -> Lease | None:
        timestamp = _now(now)
        with self._storage.read() as db:
            lease = self._uncancelled_lease(db)
            return lease if lease is not None and lease.expires_at <= timestamp else None

    def recover_attempt(self, lease: Lease, now: datetime) -> InvocationAttempt | None:
        timestamp = _now(now)
        with self._storage.transaction() as db:
            return self._recover_internal(db, lease, timestamp, latch=True)

    def record_retry(self, key: QueueKey, cycle_id: str, owner: str, now: datetime, expires_at: datetime, kind: RetryKind = RetryKind.manual_retry) -> RetryAuthorization:
        if not isinstance(key, QueueKey):
            raise ValueError("Invalid key.")
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        kind = _valid_enum(kind, RetryKind, "kind")
        timestamp = _now(now)
        expiry = _now(expires_at)
        authorization = RetryAuthorization(
            authorization=new_uuid4(), request_id=new_uuid4(), key=key, cycle_id=cycle_id,
            owner=owner, kind=kind, created_at=timestamp, expires_at=expiry,
        )
        request_id = authorization.request_id
        with self._storage.transaction() as db:
            cycle = self._cycle_by_id(db, cycle_id)
            if cycle is None or cycle.closed_at is not None or cycle.initial_key.repository != key.repository or cycle.initial_key.pr_number != key.pr_number:
                raise StaleHead()
            observed = self._current_observation(db, key.repository, key.pr_number)
            projected = self._item_row(db, key.repository, key.pr_number)
            if observed is not None and observed.head_sha != key.head_sha:
                raise StaleHead()
            if projected is not None and projected[4] != key.head_sha:
                raise StaleHead()
            db.execute(
                "INSERT INTO retry_requests(authorization, request_id, repository, pr_number, head_sha, cycle_id, owner, kind, reason, created_at, expires_at, consumed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual_retry', ?, ?, NULL)",
                (authorization.authorization, request_id, key.repository, key.pr_number, key.head_sha, cycle_id, owner, kind.value, _text(timestamp), _text(expiry)),
            )
            self._touch_item(db, key.repository, key.pr_number)
            return authorization

    def get_retry_authorization(self, repository: str, pr_number: int, cycle_id: str) -> RetryAuthorization | None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        with self._storage.read() as db:
            row = db.execute(
                "SELECT authorization, request_id, repository, pr_number, head_sha, cycle_id, owner, kind, created_at, expires_at, consumed_at "
                "FROM retry_requests WHERE repository=? AND pr_number=? AND cycle_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (repository, pr_number, cycle_id),
            ).fetchone()
            return None if row is None else _authorization(row)

    def _automatic_count(self, db, repository: str, pr_number: int, cycle_id: str) -> int:
        return int(db.execute(
            "SELECT COUNT(*) FROM invocations WHERE repository=? AND pr_number=? AND cycle_id=? AND kind='automatic' "
            "AND started_at IS NOT NULL AND outcome!='model_unavailable'",
            (repository, pr_number, cycle_id),
        ).fetchone()[0])

    def start_invocation(self, lease: Lease, now: datetime, model_id: str) -> InvocationAttempt:
        timestamp = _now(now)
        selected_model = _valid_model_id(model_id)
        with self._storage.transaction() as db:
            identity = self._identity(db, lease)
            if identity is None:
                raise LocalLeaseConflict()
            stored, attempt = identity
            if (
                stored.cancelled_at is not None or stored.expires_at <= timestamp
                or not self._scope_current(db, stored) or attempt.outcome is not AttemptOutcome.reserved
                or attempt.kind is InvocationKind.reconcile_unknown
            ):
                raise LocalLeaseConflict()
            if attempt.kind is InvocationKind.automatic and self._active_latch(db, attempt.key.repository, attempt.key.pr_number, attempt.cycle_id) is not None:
                raise LocalLeaseConflict()
            running = attempt.transition_to(AttemptOutcome.running, started_at=timestamp, model_id=selected_model)
            self._save_attempt(db, running)
            if attempt.kind is InvocationKind.manual_retry:
                self._clear_latch(db, attempt, stored.retry_authorization, timestamp)
            db.execute(
                "UPDATE queue_items SET item_revision=item_revision+1, projection_revision=projection_revision+1 "
                "WHERE repository=? AND pr_number=?",
                (attempt.key.repository, attempt.key.pr_number),
            )
            return running

    def _attempt_has_latch(self, db, attempt: InvocationAttempt) -> bool:
        return db.execute(
            "SELECT 1 FROM manual_retry_latches WHERE attempt_id=? LIMIT 1", (attempt.attempt_id,)
        ).fetchone() is not None

    def complete_invocation(self, lease: Lease, outcome: AttemptOutcome, finished_at: datetime, model_id: str | None = None, error: OperationalError | None = None, *, escalate: bool = False) -> InvocationAttempt:
        timestamp = _now(finished_at)
        outcome = _valid_enum(outcome, AttemptOutcome, "outcome")
        selected_model = None if model_id is None else _valid_model_id(model_id)
        if type(escalate) is not bool:
            raise ValueError("Invalid escalate.")
        if error is not None and not isinstance(error, OperationalError):
            raise ValueError("Invalid error.")
        with self._storage.transaction() as db:
            identity = self._identity(db, lease)
            if identity is None:
                raise LocalLeaseConflict()
            stored, attempt = identity
            effective_model = attempt.model_id if selected_model is None else selected_model
            if attempt.outcome in _TERMINAL_OUTCOMES:
                if (
                    attempt.outcome is outcome and attempt.model_id == effective_model
                    and attempt.error == error and self._attempt_has_latch(db, attempt) == escalate
                ):
                    return attempt
                raise LocalLeaseConflict()
            if attempt.kind is InvocationKind.reconcile_unknown:
                raise LocalLeaseConflict()
            if outcome not in (AttemptOutcome.completed, AttemptOutcome.model_unavailable, AttemptOutcome.failed, AttemptOutcome.cancelled, AttemptOutcome.recovered_unknown):
                raise LocalLeaseConflict()
            try:
                terminal = attempt.transition_to(
                    outcome, finished_at=timestamp, model_id=effective_model, error=error
                )
            except ValueError:
                raise LocalLeaseConflict() from None
            reason = "owner" if outcome is AttemptOutcome.cancelled else None
            self._save_attempt(db, terminal, reason)
            db.execute("UPDATE leases SET cancelled_at=? WHERE lease_id=? AND cancelled_at IS NULL", (_text(timestamp), stored.lease_id))
            if escalate and outcome in (AttemptOutcome.failed, AttemptOutcome.model_unavailable, AttemptOutcome.recovered_unknown, AttemptOutcome.cancelled):
                if terminal.error is None:
                    category = ErrorCategory.worker_failed
                else:
                    category = terminal.error.category
                if category not in _LATCH_CATEGORIES:
                    raise LocalLeaseConflict()
                self._set_latch(db, terminal, category, timestamp)
            self._touch_item(db, terminal.key.repository, terminal.key.pr_number)
            return terminal

    def get_manual_retry_latch(self, repository: str, pr_number: int, cycle_id: str) -> ManualRetryLatch | None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        with self._storage.read() as db:
            return self._active_latch(db, repository, pr_number, cycle_id)

    def list_invocations(self, repository: str, pr_number: int, cycle_id: str) -> list[InvocationAttempt]:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        with self._storage.read() as db:
            rows = db.execute(
                "SELECT attempt_id, lease_id, repository, pr_number, head_sha, cycle_id, kind, outcome, reserved_at, started_at, finished_at, "
                "model_id, error_category, error_occurred_at, cancellation_reason FROM invocations WHERE repository=? AND pr_number=? AND cycle_id=? ORDER BY reserved_at, rowid",
                (repository, pr_number, cycle_id),
            ).fetchall()
            return [_attempt(row) for row in rows]

    def automatic_invocation_count(self, repository: str, pr_number: int, cycle_id: str) -> int:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        with self._storage.read() as db:
            return self._automatic_count(db, repository, pr_number, cycle_id)

    def automatic_launch_capacity(self, repository: str, pr_number: int, cycle_id: str) -> int:
        return max(0, MAX_AUTOMATIC_INVOCATIONS - self.automatic_invocation_count(repository, pr_number, cycle_id))

    def manual_retry_launch_capacity(self, repository: str, pr_number: int, cycle_id: str, now: datetime) -> int:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        timestamp = _now(now)
        with self._storage.read() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM retry_requests WHERE repository=? AND pr_number=? AND cycle_id=? AND kind='manual_retry' "
                "AND consumed_at IS NULL AND expires_at>?",
                (repository, pr_number, cycle_id, _text(timestamp)),
            ).fetchone()[0])

    def _intent_row(self, db, attempt_id: str):
        return db.execute(
            "SELECT intent_id, attempt_id, repository, pr_number, head_sha, cycle_id, worktree_id, starting_sha, local_commit_sha, recorded_at "
            "FROM publication_intents WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()

    def _intent_for_attempt(self, db, attempt_id: str, cycle_id: str) -> bool:
        return db.execute(
            "SELECT 1 FROM publication_intents WHERE attempt_id=? AND cycle_id=? AND NOT EXISTS "
            "(SELECT 1 FROM uncertain_publications WHERE uncertain_publications.attempt_id=publication_intents.attempt_id)",
            (attempt_id, cycle_id),
        ).fetchone() is not None

    def record_publication_intent(self, attempt_id: str, key: QueueKey, cycle_id: str, worktree_id: str, starting_sha: str, local_commit_sha: str, now: datetime) -> PublicationIntent:
        attempt_id = _valid_uuid(attempt_id, "attempt_id")
        if not isinstance(key, QueueKey):
            raise ValueError("Invalid key.")
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        worktree_id = _valid_uuid(worktree_id, "worktree_id")
        timestamp = _now(now)
        candidate = PublicationIntent(
            intent_id=new_uuid4(), attempt_id=attempt_id, key=key, cycle_id=cycle_id,
            worktree_id=worktree_id, starting_sha=starting_sha, local_commit_sha=local_commit_sha,
            recorded_at=timestamp,
        )
        with self._storage.transaction() as db:
            attempt_row = self._attempt_row(db, attempt_id)
            if attempt_row is None:
                raise LocalLeaseConflict()
            attempt = _attempt(attempt_row)
            if attempt.key != key or attempt.cycle_id != cycle_id or candidate.starting_sha != key.head_sha or timestamp < attempt.reserved_at:
                raise LocalLeaseConflict()
            existing_row = self._intent_row(db, attempt_id)
            if existing_row is not None:
                existing = _intent(existing_row)
                if replace(existing, intent_id=candidate.intent_id) == candidate:
                    return existing
                raise LocalLeaseConflict()
            db.execute(
                "INSERT INTO publication_intents(intent_id, attempt_id, repository, pr_number, head_sha, cycle_id, worktree_id, starting_sha, local_commit_sha, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate.intent_id, attempt_id, key.repository, key.pr_number, key.head_sha, cycle_id, worktree_id, starting_sha, local_commit_sha, _text(timestamp)),
            )
            self._touch_item(db, key.repository, key.pr_number)
            return candidate

    def get_publication_intent(self, attempt_id: str) -> PublicationIntent | None:
        attempt_id = _valid_uuid(attempt_id, "attempt_id")
        with self._storage.read() as db:
            row = self._intent_row(db, attempt_id)
            return None if row is None else _intent(row)

    def _eligible_intents(self, db, repository: str) -> list[PublicationIntent]:
        rows = db.execute(
            "SELECT p.intent_id, p.attempt_id, p.repository, p.pr_number, p.head_sha, p.cycle_id, p.worktree_id, p.starting_sha, p.local_commit_sha, p.recorded_at "
            "FROM publication_intents p JOIN invocations i ON i.attempt_id=p.attempt_id "
            "JOIN repair_cycles c ON c.cycle_id=p.cycle_id "
            "LEFT JOIN uncertain_publications u ON u.attempt_id=p.attempt_id "
            "WHERE p.repository=? AND c.repository=p.repository AND c.pr_number=p.pr_number AND c.closed_at IS NULL "
            "AND u.attempt_id IS NULL AND i.outcome IN ('reserved', 'running', 'cancelled') "
            "AND (i.outcome!='cancelled' OR i.cancellation_reason IS NULL OR i.cancellation_reason IN ('owner', 'changed_head')) "
            "ORDER BY p.recorded_at, p.intent_id",
            (repository,),
        ).fetchall()
        return [_intent(row) for row in rows]

    def list_unassociated_publication_intents(self, repository: str) -> tuple[PublicationIntent, ...]:
        repository = _valid_repository(repository)
        with self._storage.read() as db:
            return tuple(self._eligible_intents(db, repository))

    def _publication_row(self, db, publication_id: str):
        return db.execute(
            "SELECT publication_id, attempt_id, repository, pr_number, head_sha, cycle_id, worktree_id, local_commit_sha, recorded_at, resolved_at, resolution "
            "FROM uncertain_publications WHERE publication_id=?",
            (publication_id,),
        ).fetchone()

    def record_uncertain_publication(self, attempt_id: str, key: QueueKey, cycle_id: str, worktree_id: str, local_commit_sha: str, now: datetime) -> UncertainPublication:
        attempt_id = _valid_uuid(attempt_id, "attempt_id")
        if not isinstance(key, QueueKey):
            raise ValueError("Invalid key.")
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        worktree_id = _valid_uuid(worktree_id, "worktree_id")
        timestamp = _now(now)
        candidate = UncertainPublication(
            publication_id=new_uuid4(), attempt_id=attempt_id, key=key, cycle_id=cycle_id,
            worktree_id=worktree_id, local_commit_sha=local_commit_sha, recorded_at=timestamp,
            resolved_at=None, resolution=None,
        )
        with self._storage.transaction() as db:
            intent_row = self._intent_row(db, attempt_id)
            if intent_row is None:
                raise LocalLeaseConflict()
            recorded = _intent(intent_row)
            cycle = self._cycle_by_id(db, cycle_id)
            if (
                recorded.key != key or recorded.cycle_id != cycle_id
                or recorded.worktree_id != worktree_id or recorded.local_commit_sha != local_commit_sha
                or cycle is None or cycle.closed_at is not None
                or cycle.initial_key.repository != key.repository or cycle.initial_key.pr_number != key.pr_number
                or timestamp < recorded.recorded_at
            ):
                raise LocalLeaseConflict()
            existing_row = db.execute(
                "SELECT publication_id, attempt_id, repository, pr_number, head_sha, cycle_id, worktree_id, local_commit_sha, recorded_at, resolved_at, resolution "
                "FROM uncertain_publications WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if existing_row is not None:
                existing = _publication(existing_row)
                if replace(replace(existing, publication_id=candidate.publication_id), resolved_at=None, resolution=None) == candidate:
                    return existing
                raise LocalLeaseConflict()
            db.execute(
                "INSERT INTO uncertain_publications(publication_id, attempt_id, repository, pr_number, head_sha, cycle_id, worktree_id, local_commit_sha, recorded_at, resolved_at, resolution) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (candidate.publication_id, attempt_id, key.repository, key.pr_number, key.head_sha, cycle_id, worktree_id, local_commit_sha, _text(timestamp)),
            )
            self._touch_item(db, key.repository, key.pr_number)
            return candidate

    def get_uncertain_publication(self, repository: str, pr_number: int, cycle_id: str) -> UncertainPublication | None:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        with self._storage.read() as db:
            row = db.execute(
                "SELECT publication_id, attempt_id, repository, pr_number, head_sha, cycle_id, worktree_id, local_commit_sha, recorded_at, resolved_at, resolution "
                "FROM uncertain_publications WHERE repository=? AND pr_number=? AND cycle_id=? ORDER BY (resolved_at IS NOT NULL), recorded_at DESC, rowid DESC LIMIT 1",
                (repository, pr_number, cycle_id),
            ).fetchone()
            return None if row is None else _publication(row)

    @staticmethod
    def _resolution(resolution: str, *, allow_superseded: bool = False) -> str:
        allowed = {"remote_equal", "remote_diverged", "validation_failed"}
        if allow_superseded:
            allowed.add("superseded")
        if not isinstance(resolution, str) or resolution not in allowed:
            raise ValueError("Invalid resolution.")
        return resolution

    def resolve_uncertain_publication(self, publication_id: str, resolution: str, now: datetime) -> UncertainPublication:
        publication_id = _valid_uuid(publication_id, "publication_id")
        resolution = self._resolution(resolution)
        timestamp = _now(now)
        with self._storage.transaction() as db:
            row = self._publication_row(db, publication_id)
            if row is None:
                raise LocalLeaseConflict()
            existing = _publication(row)
            if existing.resolved_at is not None:
                if existing.resolution == resolution:
                    return existing
                raise LocalLeaseConflict()
            if timestamp < existing.recorded_at:
                raise ValueError("Invalid resolved_at.")
            resolved = replace(existing, resolved_at=timestamp, resolution=resolution)
            db.execute(
                "UPDATE uncertain_publications SET resolved_at=?, resolution=? WHERE publication_id=? AND resolved_at IS NULL",
                (_text(timestamp), resolution, publication_id),
            )
            self._touch_item(db, existing.key.repository, existing.key.pr_number)
            return resolved

    def finish_reconciliation(self, lease: Lease, publication_id: str, resolution: str, now: datetime, error: OperationalError | None) -> InvocationAttempt:
        publication_id = _valid_uuid(publication_id, "publication_id")
        resolution = self._resolution(resolution)
        timestamp = _now(now)
        if error is not None and not isinstance(error, OperationalError):
            raise ValueError("Invalid error.")
        if resolution == "remote_equal":
            if error is not None:
                raise ValueError("Invalid error.")
        elif error is None or error.category is not ErrorCategory.worker_failed:
            raise ValueError("Invalid error.")
        with self._storage.transaction() as db:
            identity = self._identity(db, lease)
            if identity is None:
                raise LocalLeaseConflict()
            stored, attempt = identity
            row = self._publication_row(db, publication_id)
            if row is None:
                raise LocalLeaseConflict()
            publication = _publication(row)
            if (
                publication.key.repository != attempt.key.repository
                or publication.key.pr_number != attempt.key.pr_number
                or publication.cycle_id != attempt.cycle_id
                or attempt.kind is not InvocationKind.reconcile_unknown
            ):
                raise LocalLeaseConflict()
            expected_outcome = AttemptOutcome.cancelled if resolution == "remote_equal" else AttemptOutcome.failed
            if publication.resolved_at is not None:
                if publication.resolution == resolution and attempt.outcome is expected_outcome and stored.cancelled_at is not None:
                    if resolution == "remote_equal":
                        if attempt.error is None:
                            return attempt
                    elif attempt.error is not None and attempt.error.category is ErrorCategory.worker_failed:
                        return attempt
                raise LocalLeaseConflict()
            cycle = self._cycle_by_id(db, attempt.cycle_id)
            if (
                cycle is None or cycle.closed_at is not None or stored.cancelled_at is not None
                or stored.expires_at <= timestamp or attempt.outcome is not AttemptOutcome.reserved
                or not self._scope_current(db, stored)
            ):
                raise LocalLeaseConflict()
            intent_row = self._intent_row(db, publication.attempt_id)
            if intent_row is None:
                raise LocalLeaseConflict()
            recorded = _intent(intent_row)
            if (
                recorded.key != publication.key or recorded.cycle_id != publication.cycle_id
                or recorded.worktree_id != publication.worktree_id
                or recorded.local_commit_sha != publication.local_commit_sha
                or recorded.starting_sha != publication.key.head_sha
            ):
                raise LocalLeaseConflict()
            terminal = attempt.transition_to(expected_outcome, finished_at=timestamp, error=error)
            self._save_attempt(db, terminal, "reconciled")
            db.execute("UPDATE leases SET cancelled_at=? WHERE lease_id=?", (_text(timestamp), stored.lease_id))
            db.execute(
                "UPDATE uncertain_publications SET resolved_at=?, resolution=? WHERE publication_id=? AND resolved_at IS NULL",
                (_text(timestamp), resolution, publication_id),
            )
            if expected_outcome is AttemptOutcome.failed:
                self._set_latch(db, terminal, ErrorCategory.worker_failed, timestamp)
            self._touch_item(db, terminal.key.repository, terminal.key.pr_number)
            return terminal

    def escalate_unassociated_publication_intent(self, intent_id: str, now: datetime) -> ManualRetryLatch:
        intent_id = _valid_uuid(intent_id, "intent_id")
        timestamp = _now(now)
        with self._storage.transaction() as db:
            row = db.execute(
                "SELECT p.intent_id, p.attempt_id, p.repository, p.pr_number, p.head_sha, p.cycle_id, p.worktree_id, p.starting_sha, p.local_commit_sha, p.recorded_at "
                "FROM publication_intents p WHERE p.intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise RetryInvalid()
            recorded = _intent(row)
            eligible_ids = {candidate.intent_id for candidate in self._eligible_intents(db, recorded.key.repository)}
            if intent_id not in eligible_ids:
                raise RetryInvalid()
            attempt_row = self._attempt_row(db, recorded.attempt_id)
            if attempt_row is None:
                raise RetryInvalid()
            attempt = _attempt(attempt_row)
            existing = self._active_latch(db, attempt.key.repository, attempt.key.pr_number, attempt.cycle_id)
            if existing is not None:
                return existing
            latch = self._set_latch(db, attempt, ErrorCategory.worker_failed, timestamp)
            self._touch_item(db, attempt.key.repository, attempt.key.pr_number)
            return latch

    def has_unassociated_publication_intent(self, repository: str, pr_number: int, cycle_id: str) -> bool:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        cycle_id = _valid_uuid(cycle_id, "cycle_id")
        with self._storage.read() as db:
            return any(
                candidate.key.repository == repository
                and candidate.key.pr_number == pr_number
                and candidate.cycle_id == cycle_id
                for candidate in self._eligible_intents(db, repository)
            )

    def _paused_value(self, db) -> bool:
        return self._paused(db)

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("Invalid paused.")
        with self._storage.transaction() as db:
            if self._paused_value(db) == paused:
                return
            db.execute("UPDATE preferences SET preference_value=? WHERE preference_key='paused'", ("1" if paused else "0",))
            db.execute("UPDATE queue_items SET projection_revision=projection_revision+1")

    def is_paused(self) -> bool:
        with self._storage.read() as db:
            return self._paused(db)

    def _capture(self, db, repository: str, pr_number: int) -> ProjectionFence:
        row = self._item_row(db, repository, pr_number)
        if row is None:
            raise RemoteStale()
        projected = _item(row)
        lease_row = db.execute(
            "SELECT lease_id, attempt_id FROM leases WHERE repository=? AND pr_number=? AND cycle_id=? AND cancelled_at IS NULL LIMIT 1",
            (repository, pr_number, projected.cycle_id),
        ).fetchone()
        latch = self._latest_latch(db, repository, pr_number, projected.cycle_id)
        return ProjectionFence(
            repository=repository,
            pr_number=pr_number,
            observed_at=projected.observed_at,
            cycle_id=projected.cycle_id,
            head_sha=projected.current_head_sha,
            item_revision=row[17],
            projection_revision=row[18],
            active_lease_id=None if lease_row is None else lease_row[0],
            active_attempt_id=None if lease_row is None else lease_row[1],
            latch_id=None if latch is None else latch.latch_id,
            latch_cleared_at=None if latch is None else latch.cleared_at,
            paused=self._paused(db),
            state=projected.state,
            reason=projected.reason,
            eligible=projected.eligible,
            desired_labels=() if projected.state.desired_label is None else (projected.state.desired_label,),
        )

    def capture_projection_fence(self, repository: str, pr_number: int) -> ProjectionFence:
        repository = _valid_repository(repository)
        pr_number = _valid_integer(pr_number, "pr_number", 1)
        with self._storage.read() as db:
            return self._capture(db, repository, pr_number)

    def commit_projection(self, fence: ProjectionFence, item: QueueItem, label_recorded_at: datetime) -> bool:
        if not isinstance(fence, ProjectionFence) or not isinstance(item, QueueItem):
            raise ValueError("Invalid projection.")
        timestamp = _now(label_recorded_at)
        with self._storage.transaction() as db:
            if item.repository != fence.repository or item.pr_number != fence.pr_number:
                return False
            try:
                current = self._capture(db, fence.repository, fence.pr_number)
            except RemoteStale:
                return False
            if current != fence:
                return False
            row = self._item_row(db, fence.repository, fence.pr_number)
            if row is None or _item(row) != item:
                return False
            db.execute(
                "UPDATE queue_items SET projection_committed_revision=projection_committed_revision+1, projection_committed_at=? "
                "WHERE repository=? AND pr_number=?",
                (_text(timestamp), fence.repository, fence.pr_number),
            )
            return True
