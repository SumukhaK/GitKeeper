from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from gitkeeper.host_mode.readiness import MAX_AUTOMATIC_INVOCATIONS, OWNER_LOGIN
from gitkeeper.queue import (
    AttemptBudgetExhausted,
    AttemptOutcome,
    CheckConclusion,
    CheckResult,
    CheckState,
    ErrorCategory,
    InvocationKind,
    Lease,
    LocalLeaseConflict,
    OperationalError,
    ProjectionFence,
    PullRequestSnapshot,
    QueueError,
    QueueItem,
    QueueKey,
    RemoteStale,
    RepairCycle,
    RepairReason,
    RetryAuthorization,
    RetryExpired,
    RetryInvalid,
    RetryKind,
    RetryReplayed,
    SchemaMismatch,
    StorageCorrupt,
    StorageUnavailable,
    StaleHead,
    UncertainPublication,
    WorkerFailed,
    WorkerLabel,
    WorkerState,
)
from gitkeeper.queue.store import QueueStore

UTC = timezone.utc
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
REPOSITORY = "SumukhaK/Setu-mobile"
OTHER_REPOSITORY = "SumukhaK/GitKeeper"
SHA = "a153271a18241b3cf5b9d5ed7025916c6b11e9f8"
NEXT_SHA = "b" * 40
MODEL = "opencode/test-model"
AUTOMATIC_OWNER = "gitkeeper-automatic"


def key(**overrides):
    values = {"repository": REPOSITORY, "pr_number": 42, "head_sha": SHA}
    values.update(overrides)
    return QueueKey(**values)


def snapshot(**overrides):
    values = {
        "repository": REPOSITORY,
        "pr_number": 42,
        "author": OWNER_LOGIN,
        "head_repository": REPOSITORY,
        "head_sha": SHA,
        "is_open": True,
        "is_draft": False,
        "observed_at": NOW,
    }
    values.update(overrides)
    return PullRequestSnapshot(**values)


def checks(head_sha=SHA):
    return CheckState(
        head_sha=head_sha,
        required_check_ids=("lint",),
        results=(
            CheckResult(
                check_id="lint",
                check_run_id="101",
                head_sha=head_sha,
                conclusion=CheckConclusion.failure,
            ),
        ),
    )


def item(cycle_id, **overrides):
    values = {
        "repository": REPOSITORY,
        "pr_number": 42,
        "author": OWNER_LOGIN,
        "head_repository": REPOSITORY,
        "current_head_sha": SHA,
        "observed_at": NOW,
        "cycle_id": cycle_id,
        "eligible": True,
        "checks": checks(),
        "state": WorkerState.queued,
        "reason": RepairReason.deterministic_check_failure,
        "remote_labels": (WorkerLabel.queued,),
        "automatic_attempt_count": 0,
        "manual_retry_count": 0,
        "last_error": None,
        "updated_at": NOW,
    }
    values.update(overrides)
    return QueueItem(**values)


def operational_error(category=ErrorCategory.worker_failed, occurred_at=NOW):
    return OperationalError(
        category=category,
        message=str(QueueError(category)),
        occurred_at=occurred_at,
    )


@pytest.fixture
def store(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    state = tmp_path / "state"
    queue = QueueStore(state, [repository])
    yield queue
    queue.close()


def open_item(store, **snapshot_overrides):
    observed = snapshot(**snapshot_overrides)
    store.put_observation(observed)
    cycle = store.get_open_cycle(observed.repository, observed.pr_number)
    projected = item(cycle.cycle_id, repository=observed.repository, pr_number=observed.pr_number, head_repository=observed.head_repository, current_head_sha=observed.head_sha, checks=checks(observed.head_sha), observed_at=observed.observed_at, updated_at=observed.observed_at)
    store.put_item(projected)
    return cycle, projected


def claim(store, cycle, *, owner=AUTOMATIC_OWNER, now=NOW, authorization=None, head_sha=SHA, lease_seconds=300):
    return store.claim(key(head_sha=head_sha), cycle.cycle_id, owner, now, lease_seconds, authorization)


def start(store, claimed, *, now=NOW, model_id=MODEL):
    return store.start_invocation(claimed.lease, now, model_id)


def intent(store, claimed, **overrides):
    values = {
        "attempt_id": claimed.attempt.attempt_id,
        "key": claimed.attempt.key,
        "cycle_id": claimed.attempt.cycle_id,
        "worktree_id": "00000000-0000-4000-8000-000000000042",
        "starting_sha": claimed.attempt.key.head_sha,
        "local_commit_sha": "c" * 40,
        "now": NOW,
    }
    values.update(overrides)
    return store.record_publication_intent(**values)


def reconcile(store, cycle, publication, *, now=NOW):
    current_head = store.get_item(REPOSITORY, 42).current_head_sha
    authorization = store.record_retry(key(head_sha=current_head), cycle.cycle_id, OWNER_LOGIN, now, now + timedelta(minutes=5), RetryKind.reconcile_unknown)
    return store.claim(key(head_sha=current_head), cycle.cycle_id, OWNER_LOGIN, now, 300, authorization.authorization)


def test_schema_path_and_durable_pragmas(tmp_path):
    protected = tmp_path / "repository"
    protected.mkdir()
    state = tmp_path / "state"
    queue = QueueStore(state, [protected])
    path = state / "gitkeeper-queue.sqlite3"
    assert path.exists()
    queue.set_paused(True)
    queue.close()
    import sqlite3

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "repair_cycles", "pr_observations", "queue_items", "leases", "invocations",
            "retry_requests", "preferences", "publication_intents", "uncertain_publications",
            "manual_retry_latches",
        } <= tables
    reopened = QueueStore(state, [protected])
    assert reopened.is_paused() is True
    reopened.close()


@pytest.mark.parametrize("location", ["equal", "beneath"])
def test_rejects_state_inside_protected_repository(tmp_path, location):
    protected = tmp_path / "repository"
    protected.mkdir()
    state = protected if location == "equal" else protected / "nested" / "state"
    with pytest.raises(StorageUnavailable):
        QueueStore(state, [protected])


def test_rejects_empty_duplicate_and_symlink_protected_roots(tmp_path):
    with pytest.raises(StorageUnavailable):
        QueueStore(tmp_path / "state", [])
    protected = tmp_path / "repository"
    protected.mkdir()
    with pytest.raises(StorageUnavailable):
        QueueStore(tmp_path / "state", [protected, protected])
    link = tmp_path / "link"
    try:
        link.symlink_to(protected, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable")
    with pytest.raises(StorageUnavailable):
        QueueStore(link / "state", [protected])


@pytest.mark.parametrize("payload,error", [(b"not a sqlite database", StorageCorrupt)])
def test_corrupt_database_bytes_are_preserved(tmp_path, payload, error):
    protected = tmp_path / "repository"
    protected.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    path = state / "gitkeeper-queue.sqlite3"
    path.write_bytes(payload)
    with pytest.raises(error):
        QueueStore(state, [protected])
    assert path.read_bytes() == payload


def test_unknown_schema_is_preserved(tmp_path):
    import sqlite3

    protected = tmp_path / "repository"
    protected.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    path = state / "gitkeeper-queue.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with pytest.raises(SchemaMismatch):
        QueueStore(state, [protected])
    assert path.read_bytes() == before


def test_runtime_database_lock_is_unavailable_not_raw_sqlite_error(tmp_path):
    import sqlite3

    protected = tmp_path / "repository"
    protected.mkdir()
    state = tmp_path / "state"
    queue = QueueStore(state, [protected])
    blocker = sqlite3.connect(state / "gitkeeper-queue.sqlite3", isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(StorageUnavailable):
            queue.set_paused(True)
    finally:
        blocker.rollback()
        blocker.close()
        queue.close()


def test_schema_is_metadata_only_and_enforces_foreign_keys(tmp_path):
    import sqlite3

    protected = tmp_path / "repository"
    protected.mkdir()
    state = tmp_path / "state"
    queue = QueueStore(state, [protected])
    path = state / "gitkeeper-queue.sqlite3"
    queue.close()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        columns = {
            table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for table in ("publication_intents", "uncertain_publications", "manual_retry_latches")
        }
        forbidden = {"path", "prompt", "diff", "content", "command", "environment", "credential"}
        assert all(not (names & forbidden) for names in columns.values())
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO leases(lease_id,generation,attempt_id,owner,repository,pr_number,head_sha,cycle_id,claimed_at,expires_at) "
                "VALUES (?,1,?, 'owner', 'owner/repo',1,? ,?,?,?)",
                (str(uuid4()), str(uuid4()), SHA, str(uuid4()), NOW.isoformat(), LATER.isoformat()),
            )


def test_two_store_connections_share_the_global_lease_fence(tmp_path):
    protected = tmp_path / "repository"
    protected.mkdir()
    state = tmp_path / "state"
    first = QueueStore(state, [protected])
    second = QueueStore(state, [protected])
    try:
        first_cycle, _ = open_item(first)
        _, second_item = open_item(second, repository=OTHER_REPOSITORY, pr_number=43)
        second_cycle = second.get_open_cycle(OTHER_REPOSITORY, 43)
        barrier = Barrier(3)

        def execute(queue, target_cycle, target_key):
            barrier.wait()
            try:
                return queue.claim(target_key, target_cycle.cycle_id, AUTOMATIC_OWNER, NOW, 300)
            except LocalLeaseConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(execute, first, first_cycle, key()),
                executor.submit(execute, second, second_cycle, key(repository=OTHER_REPOSITORY, pr_number=43)),
            ]
            barrier.wait()
            results = [future.result(timeout=10) for future in futures]
        assert sum(result not in (None, "conflict") for result in results) == 1
        assert "conflict" in results
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("seconds", [0, -1, True, 1.5, "300"])
def test_claim_validates_lease_seconds_before_opening_transaction(store, seconds):
    cycle, _ = open_item(store)
    with pytest.raises(ValueError):
        claim(store, cycle, lease_seconds=seconds)
    assert store.get_active_lease(NOW) is None


def test_observation_freshness_cycle_tombstones_and_history(store):
    cycle, projected = open_item(store)
    assert cycle.cycle_number == 1
    assert store.get_observation(REPOSITORY, 42) == snapshot()
    store.put_observation(snapshot())
    assert store.get_open_cycle(REPOSITORY, 42) == cycle
    with pytest.raises(RemoteStale):
        store.put_observation(snapshot(observed_at=NOW - timedelta(seconds=1)))
    with pytest.raises(RemoteStale):
        store.put_observation(snapshot(author="different"))
    assert store.get_item(REPOSITORY, 42) == projected
    closed = store.close_cycle(REPOSITORY, 42, LATER)
    assert closed.closed_at == LATER
    assert store.close_cycle(REPOSITORY, 42, LATER) == closed
    assert store.get_item(REPOSITORY, 42) is None
    with pytest.raises(RemoteStale):
        store.put_observation(snapshot(observed_at=LATER))
    reopened = store.open_cycle(REPOSITORY, 42, SHA, LATER + timedelta(seconds=1))
    assert reopened.cycle_number == 2 and reopened.cycle_id != cycle.cycle_id
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id) is None
    store.delete_observation(REPOSITORY, 42)
    assert store.get_observation(REPOSITORY, 42) is None
    assert store.get_open_cycle(REPOSITORY, 42) == reopened


def test_new_head_cancels_exact_lease_without_clearing_latch(store):
    cycle, _ = open_item(store)
    blocked = claim(store, cycle)
    assert store.block_reserved(blocked.lease, NOW, operational_error(ErrorCategory.worker_unavailable)) is True
    latch = store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id)
    authorization = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, LATER)
    claimed = claim(store, cycle, owner=OWNER_LOGIN, authorization=authorization.authorization)
    assert claimed is not None
    later = LATER - timedelta(minutes=4)
    store.put_observation(snapshot(head_sha=NEXT_SHA, observed_at=later))
    assert store.get_lease(claimed.lease.lease_id).cancelled_at == later
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled
    assert store.get_open_cycle(REPOSITORY, 42).cycle_id == cycle.cycle_id
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id) == latch
    assert latch.category is ErrorCategory.worker_unavailable
    assert store.get_item(REPOSITORY, 42).current_head_sha == NEXT_SHA
    assert store.get_item(REPOSITORY, 42).checks.head_sha == NEXT_SHA


def test_close_cancels_active_lease_and_rejects_same_sha_stale_reopen(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    closed_at = NOW + timedelta(minutes=1)
    closed = store.close_cycle(REPOSITORY, 42, closed_at)
    assert closed.closed_at == closed_at
    assert store.get_lease(claimed.lease.lease_id).cancelled_at == closed_at
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled
    with pytest.raises(RemoteStale):
        store.open_cycle(REPOSITORY, 42, SHA, closed_at)
    reopened = store.open_cycle(REPOSITORY, 42, SHA, closed_at + timedelta(seconds=1))
    assert reopened.cycle_number == 2 and reopened.cycle_id != cycle.cycle_id
    with pytest.raises(LocalLeaseConflict):
        store.start_invocation(claimed.lease, closed_at + timedelta(seconds=1), MODEL)
    assert store.cancel(claimed.lease, closed_at + timedelta(seconds=1)) is False


def test_changed_head_cancellation_does_not_create_a_new_latch(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    store.put_observation(snapshot(head_sha=NEXT_SHA, observed_at=NOW + timedelta(minutes=1)))
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id) is None


def test_changed_head_cancellation_does_not_replay_as_owner_cancellation(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    changed_at = NOW + timedelta(minutes=1)
    store.put_observation(snapshot(head_sha=NEXT_SHA, observed_at=changed_at))
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled
    assert store.cancel(claimed.lease, changed_at) is False


def test_cancel_identity_ignores_owner_metadata(store):
    cycle, _ = open_item(store)
    authorization = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, LATER)
    claimed = claim(store, cycle, owner=OWNER_LOGIN, authorization=authorization.authorization)
    metadata_only = replace(claimed.lease, owner=OWNER_LOGIN)
    object.__setattr__(metadata_only, "owner", AUTOMATIC_OWNER)
    assert store.renew(metadata_only, NOW, 300) is not None
    assert store.cancel(metadata_only, NOW) is True


def test_close_resolves_association_and_preserves_attempt_history(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    publication_intent = intent(store, claimed)
    association = store.record_uncertain_publication(publication_intent.attempt_id, publication_intent.key, publication_intent.cycle_id, publication_intent.worktree_id, publication_intent.local_commit_sha, NOW)
    store.close_cycle(REPOSITORY, 42, LATER)
    resolved = store.get_uncertain_publication(REPOSITORY, 42, cycle.cycle_id)
    assert resolved.publication_id == association.publication_id
    assert resolved.resolution == "superseded" and resolved.resolved_at == LATER
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled
    assert store.list_invocations(REPOSITORY, 42, cycle.cycle_id) == [claimed.attempt.transition_to(AttemptOutcome.cancelled, finished_at=LATER)]


def test_two_threads_cannot_claim_concurrently(store):
    first_cycle, _ = open_item(store)
    _, second_item = open_item(store, repository=OTHER_REPOSITORY, pr_number=43)
    second_cycle = store.get_open_cycle(OTHER_REPOSITORY, 43)
    barrier = Barrier(3)

    def execute(target_cycle, target_key):
        barrier.wait()
        try:
            return store.claim(target_key, target_cycle.cycle_id, AUTOMATIC_OWNER, NOW, 300)
        except LocalLeaseConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(execute, first_cycle, key()),
            executor.submit(execute, second_cycle, key(repository=OTHER_REPOSITORY, pr_number=43)),
        ]
        barrier.wait()
        results = [future.result(timeout=10) for future in futures]
    assert sum(result not in (None, "conflict") for result in results) == 1
    assert "conflict" in results


def test_pause_and_nonqueued_items_do_not_consume_authorization(store):
    cycle, projected = open_item(store)
    authorization = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, LATER)
    store.set_paused(True)
    assert claim(store, cycle, owner=OWNER_LOGIN, authorization=authorization.authorization) is None
    assert store.get_retry_authorization(REPOSITORY, 42, cycle.cycle_id).consumed_at is None
    store.set_paused(False)
    store.put_item(replace(projected, state=WorkerState.ready, reason=None, eligible=False, remote_labels=()))
    assert claim(store, cycle, owner=OWNER_LOGIN, authorization=authorization.authorization) is None
    assert store.get_retry_authorization(REPOSITORY, 42, cycle.cycle_id).consumed_at is None


def test_retry_validation_consumption_and_capacity(store):
    cycle, _ = open_item(store)
    with pytest.raises(RetryInvalid):
        claim(store, cycle, owner=OWNER_LOGIN, authorization=str(uuid4()))
    expired = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, NOW + timedelta(seconds=1))
    with pytest.raises(RetryExpired):
        claim(store, cycle, owner=OWNER_LOGIN, now=LATER, authorization=expired.authorization)
    manual = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, LATER)
    reconciliation = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, LATER, RetryKind.reconcile_unknown)
    assert store.manual_retry_launch_capacity(REPOSITORY, 42, cycle.cycle_id, NOW) == 2
    claimed = claim(store, cycle, owner=OWNER_LOGIN, authorization=manual.authorization)
    assert claimed.authorization.consumed_at == NOW
    with pytest.raises(RetryReplayed):
        claim(store, cycle, owner=OWNER_LOGIN, authorization=manual.authorization)
    assert store.manual_retry_launch_capacity(REPOSITORY, 42, cycle.cycle_id, NOW) == 1
    assert store.manual_retry_launch_capacity(REPOSITORY, 42, cycle.cycle_id, LATER) == 0
    assert store.get_retry_authorization(REPOSITORY, 42, cycle.cycle_id).kind is RetryKind.reconcile_unknown


def test_automatic_budget_counts_durable_running_launches(store):
    cycle, _ = open_item(store)
    for index in range(MAX_AUTOMATIC_INVOCATIONS):
        claimed = claim(store, cycle, now=NOW + timedelta(minutes=index))
        assert claimed is not None
        start(store, claimed, now=NOW + timedelta(minutes=index))
        assert store.automatic_invocation_count(REPOSITORY, 42, cycle.cycle_id) == index + 1
        store.complete_invocation(claimed.lease, AttemptOutcome.completed, NOW + timedelta(minutes=index, seconds=1))
    assert store.automatic_launch_capacity(REPOSITORY, 42, cycle.cycle_id) == 0
    with pytest.raises(AttemptBudgetExhausted):
        claim(store, cycle, now=LATER)


def test_model_unavailable_does_not_consume_automatic_budget(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    start(store, claimed)
    result = store.complete_invocation(claimed.lease, AttemptOutcome.model_unavailable, LATER, error=operational_error(ErrorCategory.model_unavailable))
    assert result.model_id == MODEL
    assert store.automatic_invocation_count(REPOSITORY, 42, cycle.cycle_id) == 0
    assert store.automatic_launch_capacity(REPOSITORY, 42, cycle.cycle_id) == MAX_AUTOMATIC_INVOCATIONS


def test_reserved_crash_recovery_cancels_without_refunding_manual_token(store):
    cycle, _ = open_item(store)
    authorization = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, LATER)
    claimed = claim(store, cycle, owner=OWNER_LOGIN, authorization=authorization.authorization)
    assert store.get_active_lease(LATER) is None
    expired = store.get_recoverable_lease(LATER)
    assert expired == claimed.lease
    recovered = store.recover_attempt(expired, LATER)
    assert recovered.outcome is AttemptOutcome.cancelled
    assert recovered.started_at is None and recovered.error is None
    assert store.get_active_lease(LATER) is None and store.get_recoverable_lease(LATER) is None
    latch = store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id)
    assert latch.category is ErrorCategory.worker_unavailable and latch.attempt_id == claimed.attempt.attempt_id
    assert store.manual_retry_launch_capacity(REPOSITORY, 42, cycle.cycle_id, LATER) == 0
    assert store.automatic_invocation_count(REPOSITORY, 42, cycle.cycle_id) == 0
    with pytest.raises(RetryReplayed):
        claim(store, cycle, owner=OWNER_LOGIN, now=LATER, authorization=authorization.authorization)


def test_running_crash_recovery_counts_launch_and_sets_worker_failure(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    running = start(store, claimed)
    recovered = store.recover_attempt(store.get_recoverable_lease(LATER), LATER)
    assert recovered.outcome is AttemptOutcome.recovered_unknown
    assert recovered.started_at == running.started_at and recovered.model_id == MODEL
    assert recovered.error.category is ErrorCategory.worker_failed
    assert store.automatic_invocation_count(REPOSITORY, 42, cycle.cycle_id) == 1
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id).category is ErrorCategory.worker_failed


def test_only_one_start_wins_and_identity_is_exact(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    barrier = Barrier(3)

    def launch():
        barrier.wait()
        try:
            return start(store, claimed)
        except LocalLeaseConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(launch), executor.submit(launch)]
        barrier.wait()
        results = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(result, object) and result != "conflict" for result in results) == 1
    assert results.count("conflict") == 1
    wrong = replace(claimed.lease, attempt_id=str(uuid4()))
    with pytest.raises(LocalLeaseConflict):
        store.start_invocation(wrong, NOW, MODEL)
    assert store.renew(replace(claimed.lease, generation=99), NOW, 300) is None
    assert store.cancel(replace(claimed.lease, key=key(head_sha=NEXT_SHA)), LATER) is False


def test_renew_does_not_shorten_and_cancel_is_idempotent(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle, lease_seconds=300)
    renewed = store.renew(claimed.lease, NOW, 600)
    assert renewed.expires_at == NOW + timedelta(seconds=600)
    assert store.renew(renewed, NOW, 1) == renewed
    assert store.renew(claimed.lease, NOW, 600).expires_at == NOW + timedelta(seconds=600)
    assert store.cancel(renewed, LATER) is True
    assert store.cancel(renewed, LATER) is True
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled


def test_completion_is_exact_idempotent_and_conflicts_are_rejected(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    start(store, claimed)
    error = operational_error()
    terminal = store.complete_invocation(claimed.lease, AttemptOutcome.failed, LATER, error=error, escalate=True)
    assert store.complete_invocation(claimed.lease, AttemptOutcome.failed, LATER + timedelta(minutes=1), error=error, escalate=True) == terminal
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id).category is ErrorCategory.worker_failed
    with pytest.raises(LocalLeaseConflict):
        store.complete_invocation(claimed.lease, AttemptOutcome.completed, LATER)
    with pytest.raises(LocalLeaseConflict):
        store.complete_invocation(claimed.lease, AttemptOutcome.failed, LATER, error=operational_error(ErrorCategory.worker_unavailable), escalate=True)


def test_manual_start_alone_clears_latch_and_reconciliation_never_does(store):
    cycle, projected = open_item(store)
    first = claim(store, cycle)
    store.block_reserved(first.lease, NOW, operational_error(ErrorCategory.worker_unavailable))
    store.put_item(replace(projected, state=WorkerState.blocked, eligible=False, reason=RepairReason.manual_retry, last_error=operational_error(ErrorCategory.worker_unavailable), updated_at=LATER))
    assert claim(store, cycle, now=LATER) is None
    manual = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, LATER, LATER + timedelta(minutes=5))
    assert store.manual_retry_launch_capacity(REPOSITORY, 42, cycle.cycle_id, LATER) == 1
    claimed = claim(store, cycle, owner=OWNER_LOGIN, now=LATER, authorization=manual.authorization)
    assert claimed is None
    store.put_item(replace(projected, state=WorkerState.queued, eligible=False, updated_at=LATER))
    claimed = claim(store, cycle, owner=OWNER_LOGIN, now=LATER, authorization=manual.authorization)
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id) is not None
    start(store, claimed, now=LATER)
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id) is None


def test_publication_intent_is_metadata_only_idempotent_history(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    recorded = intent(store, claimed)
    assert intent(store, claimed) == recorded
    assert store.get_publication_intent(claimed.attempt.attempt_id) == recorded
    with pytest.raises(LocalLeaseConflict):
        intent(store, claimed, local_commit_sha="d" * 40)
    assert store.list_unassociated_publication_intents(REPOSITORY) == (recorded,)
    assert store.has_unassociated_publication_intent(REPOSITORY, 42, cycle.cycle_id)
    store.close_cycle(REPOSITORY, 42, LATER)
    assert store.list_unassociated_publication_intents(REPOSITORY) == ()


def test_publication_recording_replays_exact_evidence_and_preserves_resolution(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    recorded = intent(store, claimed)
    association = store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, NOW)
    replay = store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, NOW)
    assert replay == association
    resolved = store.resolve_uncertain_publication(association.publication_id, "remote_equal", NOW)
    assert store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, NOW) == resolved
    with pytest.raises(LocalLeaseConflict):
        store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, str(uuid4()), recorded.local_commit_sha, NOW)


@pytest.mark.parametrize("outcome", [AttemptOutcome.completed, AttemptOutcome.failed, AttemptOutcome.model_unavailable, AttemptOutcome.recovered_unknown])
def test_intent_discovery_excludes_other_terminal_outcomes(store, outcome):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    if outcome is AttemptOutcome.recovered_unknown:
        start(store, claimed)
        store.recover_attempt(store.get_recoverable_lease(LATER), LATER)
        recorded = intent(store, claimed, now=LATER)
    else:
        recorded = intent(store, claimed)
        start(store, claimed)
        error = operational_error(ErrorCategory.model_unavailable) if outcome is AttemptOutcome.model_unavailable else operational_error() if outcome is AttemptOutcome.failed else None
        store.complete_invocation(claimed.lease, outcome, LATER, error=error)
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is outcome
    assert store.list_unassociated_publication_intents(REPOSITORY) == ()
    assert store.has_unassociated_publication_intent(REPOSITORY, 42, cycle.cycle_id) is False
    with pytest.raises(RetryInvalid):
        store.escalate_unassociated_publication_intent(recorded.intent_id, LATER)


def test_intent_discovery_survives_same_cycle_cancellation(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    recorded = intent(store, claimed)
    assert store.cancel(claimed.lease, NOW) is True
    assert store.list_unassociated_publication_intents(REPOSITORY) == (recorded,)
    association = store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, NOW)
    assert association.attempt_id == claimed.attempt.attempt_id
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled
    assert store.list_unassociated_publication_intents(REPOSITORY) == ()


def test_unassociated_intent_fences_generic_running_recovery(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    start(store, claimed)
    recorded = intent(store, claimed)
    assert store.recover_attempt(store.get_recoverable_lease(LATER), LATER) is None
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.running
    association = store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, LATER)
    recovered = store.recover_attempt(store.get_recoverable_lease(LATER), LATER)
    assert recovered.outcome is AttemptOutcome.recovered_unknown
    assert store.get_uncertain_publication(REPOSITORY, 42, cycle.cycle_id).publication_id == association.publication_id
    authorization = store.record_retry(recorded.key, cycle.cycle_id, OWNER_LOGIN, LATER, LATER + timedelta(minutes=5), RetryKind.reconcile_unknown)
    reconciliation = claim(store, cycle, owner=OWNER_LOGIN, now=LATER, authorization=authorization.authorization)
    assert reconciliation.uncertain_publication.publication_id == association.publication_id
    assert reconciliation.attempt.kind is InvocationKind.reconcile_unknown


def test_blocked_reconciliation_exception_retains_latch_and_labels(store):
    cycle, projected = open_item(store)
    original = claim(store, cycle)
    recorded = intent(store, original)
    association = store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, NOW)
    error = operational_error()
    store.cancel(original.lease, NOW, require_manual_retry=True)
    blocked = replace(projected, state=WorkerState.blocked, eligible=False, reason=RepairReason.manual_retry, remote_labels=(WorkerLabel.blocked,), last_error=error, updated_at=NOW)
    store.put_item(blocked)
    authorization = store.record_retry(recorded.key, cycle.cycle_id, OWNER_LOGIN, NOW, LATER, RetryKind.reconcile_unknown)
    claimed = claim(store, cycle, owner=OWNER_LOGIN, authorization=authorization.authorization)
    assert claimed.uncertain_publication == association
    assert store.get_item(REPOSITORY, 42) == blocked
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id) is not None
    with pytest.raises(LocalLeaseConflict):
        start(store, claimed)


def test_reconciliation_remote_equal_and_failure_resolutions(store):
    cycle, projected = open_item(store)
    original = claim(store, cycle)
    recorded = intent(store, original)
    association = store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, NOW)
    store.cancel(original.lease, NOW)
    store.put_observation(snapshot(head_sha=recorded.local_commit_sha, observed_at=NOW + timedelta(seconds=1)))
    store.put_item(replace(projected, current_head_sha=recorded.local_commit_sha, checks=checks(recorded.local_commit_sha), observed_at=NOW + timedelta(seconds=1), updated_at=NOW + timedelta(seconds=1)))
    claimed = reconcile(store, cycle, association)
    assert claimed.lease.key.head_sha == recorded.local_commit_sha
    assert claimed.uncertain_publication.key.head_sha == SHA
    finished = store.finish_reconciliation(claimed.lease, association.publication_id, "remote_equal", NOW, None)
    assert finished.outcome is AttemptOutcome.cancelled and finished.error is None and finished.started_at is None
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id) is None
    assert store.automatic_invocation_count(REPOSITORY, 42, cycle.cycle_id) == 0
    assert store.finish_reconciliation(claimed.lease, association.publication_id, "remote_equal", LATER, None) == finished
    assert store.get_uncertain_publication(REPOSITORY, 42, cycle.cycle_id).resolution == "remote_equal"


@pytest.mark.parametrize("resolution", ["remote_diverged", "validation_failed"])
def test_reconciliation_failure_sets_latch_and_fixed_error(store, resolution):
    cycle, _ = open_item(store)
    original = claim(store, cycle)
    recorded = intent(store, original)
    association = store.record_uncertain_publication(recorded.attempt_id, recorded.key, recorded.cycle_id, recorded.worktree_id, recorded.local_commit_sha, NOW)
    store.cancel(original.lease, NOW)
    claimed = reconcile(store, cycle, association)
    error = operational_error()
    with pytest.raises(ValueError):
        store.finish_reconciliation(claimed.lease, association.publication_id, resolution, NOW, operational_error(ErrorCategory.worker_unavailable))
    finished = store.finish_reconciliation(claimed.lease, association.publication_id, resolution, NOW, error)
    assert finished.outcome is AttemptOutcome.failed and finished.error == error and finished.started_at is None
    assert store.get_manual_retry_latch(REPOSITORY, 42, cycle.cycle_id).category is ErrorCategory.worker_failed


def test_unassociated_intent_escalation_is_idempotent_and_fail_closed(store):
    cycle, projected = open_item(store)
    claimed = claim(store, cycle)
    recorded = intent(store, claimed)
    store.cancel(claimed.lease, NOW)
    latch = store.escalate_unassociated_publication_intent(recorded.intent_id, NOW)
    assert latch.category is ErrorCategory.worker_failed
    assert store.escalate_unassociated_publication_intent(recorded.intent_id, LATER) == latch
    assert store.has_unassociated_publication_intent(REPOSITORY, 42, cycle.cycle_id)
    assert store.get_attempt(claimed.attempt.attempt_id).outcome is AttemptOutcome.cancelled
    store.put_item(replace(projected, state=WorkerState.blocked, eligible=False, reason=RepairReason.manual_retry, last_error=operational_error(), updated_at=LATER))
    assert claim(store, cycle, now=LATER) is None
    with pytest.raises(RetryInvalid):
        store.escalate_unassociated_publication_intent(str(uuid4()), LATER)


def test_projection_fence_exactness_and_label_only_progress(store):
    cycle, projected = open_item(store)
    fence = store.capture_projection_fence(REPOSITORY, 42)
    assert fence.cycle_id == cycle.cycle_id and fence.desired_labels == (WorkerLabel.queued,)
    assert store.commit_projection(fence, projected, NOW) is True
    assert store.capture_projection_fence(REPOSITORY, 42) == fence
    assert store.commit_projection(fence, projected, LATER) is True
    claimed = claim(store, cycle)
    stale = store.capture_projection_fence(REPOSITORY, 42)
    assert stale.active_lease_id == claimed.lease.lease_id
    assert store.commit_projection(fence, projected, LATER) is False
    assert store.commit_projection(replace(fence, paused=True), projected, LATER) is False
    with pytest.raises(RemoteStale):
        store.capture_projection_fence(REPOSITORY, 99)


def test_durable_running_recovery_survives_reopen(store):
    cycle, _ = open_item(store)
    claimed = claim(store, cycle)
    start(store, claimed)
    state = store._storage.path.parent
    protected = state.parent / "repository"
    store.close()
    reopened = QueueStore(state, [protected])
    try:
        expired = reopened.get_recoverable_lease(LATER)
        assert expired == claimed.lease
        recovered = reopened.recover_attempt(expired, LATER)
        assert recovered.outcome is AttemptOutcome.recovered_unknown
        assert reopened.automatic_invocation_count(REPOSITORY, 42, cycle.cycle_id) == 1
    finally:
        reopened.close()


def test_projection_fences_expire_on_semantic_mutations(store):
    cycle, projected = open_item(store)
    original = store.capture_projection_fence(REPOSITORY, 42)
    store.set_paused(True)
    paused = store.capture_projection_fence(REPOSITORY, 42)
    assert paused.paused is True and paused != original
    store.set_paused(False)
    claimed = claim(store, cycle)
    reserved = store.capture_projection_fence(REPOSITORY, 42)
    start(store, claimed)
    running = store.capture_projection_fence(REPOSITORY, 42)
    store.complete_invocation(claimed.lease, AttemptOutcome.completed, LATER)
    completed = store.capture_projection_fence(REPOSITORY, 42)
    assert len({original, paused, reserved, running, completed}) == 5
    store.close_cycle(REPOSITORY, 42, LATER)
    assert store.commit_projection(completed, projected, LATER) is False


def test_projection_fences_expire_on_authorization_latch_and_new_head(store):
    cycle, _ = open_item(store)
    original = store.capture_projection_fence(REPOSITORY, 42)
    authorization = store.record_retry(key(), cycle.cycle_id, OWNER_LOGIN, NOW, LATER)
    authorized = store.capture_projection_fence(REPOSITORY, 42)
    assert authorized != original
    claimed = claim(store, cycle, owner=OWNER_LOGIN, authorization=authorization.authorization)
    store.cancel(claimed.lease, NOW, require_manual_retry=True)
    latched = store.capture_projection_fence(REPOSITORY, 42)
    assert latched.latch_id is not None and latched != authorized
    changed_at = NOW + timedelta(seconds=1)
    store.put_observation(snapshot(head_sha=NEXT_SHA, observed_at=changed_at))
    changed = store.capture_projection_fence(REPOSITORY, 42)
    assert changed.head_sha == NEXT_SHA and changed != latched
    assert store.get_open_cycle(REPOSITORY, 42).cycle_id == cycle.cycle_id
