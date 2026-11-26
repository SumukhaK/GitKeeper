import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from gitkeeper.queue.errors import SchemaMismatch, StorageUnavailable
from gitkeeper.queue.models import (
    AttemptOutcome,
    ClaimResult,
    InvocationAttempt,
    InvocationKind,
    Lease,
    QueueKey,
)
from gitkeeper.queue.ports import FeedbackPayload
from gitkeeper.worker.feedback import FeedbackStore
from gitkeeper.worker.models import FeedbackRecord

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"


class FakeSecurity:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.directories: list[object] = []
        self.files: list[object] = []
        self.verified: list[object] = []

    def _check(self) -> None:
        if self.fail:
            raise StorageUnavailable()

    def create_directory(self, path) -> None:
        self._check()
        path.mkdir(parents=True, exist_ok=True)
        self.directories.append(path)

    def create_file(self, path) -> None:
        self._check()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.files.append(path)

    def verify_directory(self, path) -> None:
        self._check()
        self.verified.append(path)

    def verify_file(self, path) -> None:
        self._check()
        self.verified.append(path)


def _claim(
    *,
    head_sha: str = _SHA,
    cycle_id: str | None = None,
    attempt_id: str | None = None,
    lease_id: str | None = None,
) -> ClaimResult:
    cycle = cycle_id or str(uuid4())
    attempt = attempt_id or str(uuid4())
    lease = lease_id or str(uuid4())
    key = QueueKey(_REPOSITORY, 42, head_sha)
    return ClaimResult(
        lease=Lease(
            lease_id=lease,
            generation=1,
            attempt_id=attempt,
            owner="gitkeeper-automatic",
            key=key,
            cycle_id=cycle,
            retry_authorization=None,
            claimed_at=_AT,
            expires_at=_AT + timedelta(minutes=2),
            cancelled_at=None,
        ),
        attempt=InvocationAttempt(
            attempt_id=attempt,
            lease_id=lease,
            key=key,
            cycle_id=cycle,
            kind=InvocationKind.automatic,
            outcome=AttemptOutcome.reserved,
            reserved_at=_AT,
            started_at=None,
            finished_at=None,
            model_id=None,
            error=None,
        ),
        authorization=None,
    )


def _payload(
    *,
    head_sha: str = _SHA,
    cycle_id: str | None = None,
    text: str = "Please fix the failing test.",
    created_at: datetime = _AT,
) -> FeedbackPayload:
    return FeedbackPayload(
        repository=_REPOSITORY,
        pr_number=42,
        head_sha=head_sha,
        cycle_id=cycle_id or str(uuid4()),
        text=text,
        created_at=created_at,
    )


def _record(
    *,
    feedback_id: str | None = None,
    head_sha: str = _SHA,
    cycle_id: str | None = None,
    text: str = "Please fix the failing test.",
    attempt_id: str | None = None,
) -> FeedbackRecord:
    return FeedbackRecord(
        feedback_id=feedback_id or str(uuid4()),
        repository=_REPOSITORY,
        pr_number=42,
        head_sha=head_sha,
        cycle_id=cycle_id or str(uuid4()),
        text=text,
        created_at=_AT,
        attempt_id=attempt_id,
    )


def _protected(tmp_path):
    protected = tmp_path / "repo"
    protected.mkdir()
    return protected


def test_put_persists_and_secures_storage(tmp_path) -> None:
    security = FakeSecurity()
    protected = _protected(tmp_path)
    state = tmp_path / "state"
    store = FeedbackStore(state, (protected,), security=security)
    payload = _payload()

    binding = store.put(payload)

    assert binding.feedback_id
    assert binding.head_sha == _SHA
    assert binding.text == payload.text
    assert binding.attempt_id is None
    assert store.list_for_cycle(_REPOSITORY, 42, payload.cycle_id) == (binding,)
    assert store.path == state / "gitkeeper-feedback.sqlite3"
    assert state in security.directories
    assert store.path in security.files
    assert store.path in security.verified
    store.close()


def test_same_feedback_record_is_idempotent(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )
    record = _record()

    first = store.put(record)
    second = store.put(record)

    assert first == second
    assert len(store.list_for_cycle(_REPOSITORY, 42, record.cycle_id)) == 1
    store.close()


def test_conflicting_duplicate_is_rejected(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )
    record = _record()
    store.put(record)

    with pytest.raises(ValueError):
        store.put(replace(record, text="Different feedback."))

    store.close()


def test_bind_for_claim_binds_the_newest_unbound_feedback(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )
    claim = _claim()
    older = store.put(
        _payload(cycle_id=claim.attempt.cycle_id, created_at=_AT)
    )
    newer = store.put(
        _payload(
            cycle_id=claim.attempt.cycle_id,
            text="Newest feedback.",
            created_at=_AT + timedelta(seconds=1),
        )
    )

    bound = store.bind_for_claim(claim)

    assert bound is not None
    assert bound.feedback_id == newer.feedback_id
    assert bound.feedback_id != older.feedback_id
    assert bound.attempt_id == claim.attempt.attempt_id
    assert store.bind_for_claim(claim) == bound
    store.close()


def test_feedback_for_another_sha_is_not_bound(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )
    claim = _claim()
    store.put(
        _payload(
            head_sha="b" * 40,
            cycle_id=claim.attempt.cycle_id,
        )
    )

    assert store.bind_for_claim(claim) is None
    store.close()


def test_mismatched_claim_cannot_read_an_existing_binding(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )
    claim = _claim()
    store.put(_payload(cycle_id=claim.attempt.cycle_id))
    store.bind_for_claim(claim)

    other_key = QueueKey(_REPOSITORY, 42, "b" * 40)
    other_claim = ClaimResult(
        lease=replace(claim.lease, key=other_key),
        attempt=replace(claim.attempt, key=other_key),
        authorization=None,
    )

    with pytest.raises(ValueError):
        store.bind_for_claim(other_claim)
    store.close()


def test_attempt_cannot_be_bound_to_two_feedback_rows(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )
    claim = _claim()
    store.put(_payload(cycle_id=claim.attempt.cycle_id))
    store.bind_for_claim(claim)

    with pytest.raises(ValueError):
        store.put(_record(attempt_id=claim.attempt.attempt_id))

    store.close()


def test_reopen_preserves_bindings(tmp_path) -> None:
    security = FakeSecurity()
    protected = _protected(tmp_path)
    state = tmp_path / "state"
    store = FeedbackStore(state, (protected,), security=security)
    claim = _claim()
    store.put(_payload(cycle_id=claim.attempt.cycle_id))
    bound = store.bind_for_claim(claim)
    store.close()

    reopened = FeedbackStore(state, (protected,), security=security)

    assert reopened.list_for_cycle(
        _REPOSITORY, 42, claim.attempt.cycle_id
    ) == (bound,)
    reopened.close()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows DACL behavior is Windows-only."
)
def test_real_windows_dacl_round_trip(tmp_path) -> None:
    protected = _protected(tmp_path)
    state = tmp_path / "state"
    store = FeedbackStore(state, (protected,))
    claim = _claim()
    store.put(_payload(cycle_id=claim.attempt.cycle_id))
    bound = store.bind_for_claim(claim)
    store.close()

    reopened = FeedbackStore(state, (protected,))

    assert reopened.list_for_cycle(
        _REPOSITORY, 42, claim.attempt.cycle_id
    ) == (bound,)
    reopened.close()


def test_security_failure_fails_closed(tmp_path) -> None:
    protected = _protected(tmp_path)

    with pytest.raises(StorageUnavailable):
        FeedbackStore(
            tmp_path / "state",
            (protected,),
            security=FakeSecurity(fail=True),
        )


def test_state_directory_beneath_a_protected_root_is_rejected(tmp_path) -> None:
    protected = _protected(tmp_path)

    with pytest.raises(StorageUnavailable):
        FeedbackStore(
            protected / "state",
            (protected,),
            security=FakeSecurity(),
        )


def test_unknown_schema_is_not_reset(tmp_path) -> None:
    protected = _protected(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    database = state / "gitkeeper-feedback.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version=7")
        connection.execute("CREATE TABLE foreign_data(value TEXT)")

    with pytest.raises(SchemaMismatch):
        FeedbackStore(state, (protected,), security=FakeSecurity())

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7


def test_put_rejects_mapping_or_exception(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )

    with pytest.raises(TypeError):
        store.put({})
    with pytest.raises(TypeError):
        store.put(RuntimeError("raw text"))

    store.close()


def test_list_for_cycle_rejects_invalid_identity(tmp_path) -> None:
    store = FeedbackStore(
        tmp_path / "state", (_protected(tmp_path),), security=FakeSecurity()
    )

    with pytest.raises(ValueError):
        store.list_for_cycle("not-a-repository", 42, str(uuid4()))
    with pytest.raises(ValueError):
        store.list_for_cycle(_REPOSITORY, 0, str(uuid4()))
    with pytest.raises(ValueError):
        store.list_for_cycle(_REPOSITORY, 42, "not-a-uuid")

    store.close()
