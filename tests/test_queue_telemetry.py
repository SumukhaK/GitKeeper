"""Path-fenced operational telemetry store tests.

The store records fixed lifecycle facts only. These tests pin the fencing,
lifecycle accounting, deduplication, retention, and privacy contracts.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from gitkeeper.queue import (
    AttemptOutcome,
    ErrorCategory,
    OperationalError,
    QueueError,
    QueueEvent,
    TelemetryViewAdapter,
    WorkerState,
)
from gitkeeper.queue.errors import SchemaMismatch, StorageCorrupt, StorageUnavailable
from gitkeeper.queue.telemetry import TelemetryStore

UTC = timezone.utc
LOCAL = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
REPOSITORY = "SumukhaK/Setu-mobile"
PR_NUMBER = 42
SHA = "a153271a18241b3cf5b9d5ed7025916c6b11e9f8"
MODEL = "opencode/deepseek-v4.1-flash-free"
DATABASE = "gitkeeper-telemetry.sqlite3"
EVENT_COLUMNS = (
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
FORBIDDEN_COLUMN_FRAGMENTS = (
    "source",
    "diff",
    "prompt",
    "token",
    "secret",
    "credential",
    "environment",
    "screenshot",
    "output",
    "message",
    "command",
    "path",
    "stdout",
    "stderr",
    "trace",
    "budget",
)


def identifier() -> str:
    return str(uuid4())


def operational_error(category=ErrorCategory.worker_failed, occurred_at=NOW):
    return OperationalError(
        category=category,
        message=str(QueueError(category)),
        occurred_at=occurred_at,
    )


def queue_event(**overrides):
    values = {
        "event_id": identifier(),
        "repository": REPOSITORY,
        "pr_number": PR_NUMBER,
        "head_sha": SHA,
        "cycle_id": identifier(),
        "from_state": WorkerState.queued,
        "to_state": WorkerState.blocked,
        "occurred_at": NOW,
        "attempt_id": None,
        "error": None,
    }
    values.update(overrides)
    return QueueEvent(**values)


def reservation(attempt_id, *, cycle, occurred_at=NOW):
    return queue_event(
        cycle_id=cycle,
        attempt_id=attempt_id,
        from_state=WorkerState.queued,
        to_state=WorkerState.running,
        occurred_at=occurred_at,
    )


def start(attempt_id, *, cycle, occurred_at=LATER):
    return queue_event(
        cycle_id=cycle,
        attempt_id=attempt_id,
        from_state=WorkerState.queued,
        to_state=WorkerState.running,
        occurred_at=occurred_at,
    )


def terminal(attempt_id, *, cycle, occurred_at=LATER + timedelta(minutes=1), error=None):
    return queue_event(
        cycle_id=cycle,
        attempt_id=attempt_id,
        from_state=WorkerState.running,
        to_state=WorkerState.ready,
        occurred_at=occurred_at,
        error=error,
    )


def replay(event, **overrides):
    values = {
        "event_id": identifier(),
        "repository": event.repository,
        "pr_number": event.pr_number,
        "head_sha": event.head_sha,
        "cycle_id": event.cycle_id,
        "from_state": event.from_state,
        "to_state": event.to_state,
        "occurred_at": event.occurred_at,
        "attempt_id": event.attempt_id,
        "error": event.error,
    }
    values.update(overrides)
    return QueueEvent(**values)


def database_bytes(state: Path) -> bytes:
    payload = b""
    for name in (DATABASE, DATABASE + "-wal", DATABASE + "-shm"):
        candidate = state / name
        if candidate.exists() and candidate.is_file():
            payload += candidate.read_bytes()
    return payload


def directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    except (ImportError, AttributeError, OSError):
        pytest.skip("directory links are unavailable")


@pytest.fixture
def protected(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    return root


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def store(state, protected):
    opened = TelemetryStore(state, [protected])
    try:
        yield opened
    finally:
        opened.close()


# --------------------------------------------------------------------------- #
# Path fencing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("location", ["equal", "beneath"])
def test_constructor_rejects_state_at_or_beneath_a_protected_root(tmp_path, location):
    protected = tmp_path / "repository"
    protected.mkdir()
    target = protected if location == "equal" else protected / "nested" / "state"
    with pytest.raises(StorageUnavailable):
        TelemetryStore(target, [protected])


def test_constructor_rejects_empty_and_duplicate_protected_roots(tmp_path, protected):
    with pytest.raises(StorageUnavailable):
        TelemetryStore(tmp_path / "state", [])
    with pytest.raises(StorageUnavailable):
        TelemetryStore(tmp_path / "state", [protected, protected])
    with pytest.raises(StorageUnavailable):
        TelemetryStore(tmp_path / "state", [protected, Path(str(protected) + os.sep)])


def test_constructor_rejects_a_protected_root_link_pointing_at_the_state_dir(tmp_path):
    protected = tmp_path / "repository"
    protected.mkdir()
    link = tmp_path / "link"
    directory_link(link, protected)
    with pytest.raises(StorageUnavailable):
        TelemetryStore(link / "state", [protected])


def test_constructor_rejects_a_state_directory_link_escaping_a_protected_root(
    tmp_path, protected
):
    outside = tmp_path / "outside"
    outside.mkdir()
    directory_link(protected / "telemetry", outside)
    with pytest.raises(StorageUnavailable):
        TelemetryStore(protected / "telemetry", [protected])
    assert list(outside.iterdir()) == []


def test_constructor_rejects_a_file_in_place_of_the_state_directory(tmp_path, protected):
    state = tmp_path / "state"
    state.write_text("not a directory")
    with pytest.raises(StorageUnavailable):
        TelemetryStore(state, [protected])


def test_constructor_rejects_a_directory_in_place_of_the_database(tmp_path, protected, state):
    state.mkdir()
    (state / DATABASE).mkdir()
    with pytest.raises(StorageUnavailable):
        TelemetryStore(state, [protected])


@pytest.mark.parametrize("version", [2, 99])
def test_constructor_rejects_an_unsupported_user_version_without_rewriting(
    tmp_path, protected, state, version
):
    state.mkdir()
    path = state / DATABASE
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    before = path.read_bytes()
    with pytest.raises(SchemaMismatch):
        TelemetryStore(state, [protected])
    assert path.read_bytes() == before


def test_constructor_rejects_an_unversioned_database_holding_foreign_tables(
    tmp_path, protected, state
):
    state.mkdir()
    path = state / DATABASE
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE foreign_payload (content TEXT)")
    before = path.read_bytes()
    with pytest.raises(SchemaMismatch):
        TelemetryStore(state, [protected])
    assert path.read_bytes() == before


def test_constructor_preserves_corrupt_database_bytes(tmp_path, protected, state):
    state.mkdir()
    path = state / DATABASE
    payload = b"not a sqlite database, must survive a failed open"
    path.write_bytes(payload)
    with pytest.raises(StorageCorrupt):
        TelemetryStore(state, [protected])
    assert path.read_bytes() == payload


def test_constructor_rejects_an_incompatible_telemetry_schema(tmp_path, protected, state):
    state.mkdir()
    path = state / DATABASE
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=1")
        connection.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)")
    before = path.read_bytes()
    with pytest.raises(SchemaMismatch):
        TelemetryStore(state, [protected])
    assert path.read_bytes() == before


# --------------------------------------------------------------------------- #
# Retention settings
# --------------------------------------------------------------------------- #


def test_retention_defaults_to_ninety_and_survives_reopen(state, protected):
    first = TelemetryStore(state, [protected])
    try:
        assert first.get_retention_days() == 90
        first.set_retention_days(30)
    finally:
        first.close()
    reopened = TelemetryStore(state, [protected])
    try:
        assert reopened.get_retention_days() == 30
        reopened.set_retention_days(10)
        assert reopened.get_retention_days() == 10
    finally:
        reopened.close()


def test_retention_can_only_be_shortened_from_its_current_value(store):
    store.set_retention_days(30)
    with pytest.raises(ValueError):
        store.set_retention_days(31)
    with pytest.raises(ValueError):
        store.set_retention_days(90)
    store.set_retention_days(30)
    store.set_retention_days(0)
    assert store.get_retention_days() == 0


@pytest.mark.parametrize("days", [-1, 91, 1000, True, 1.5, "30", None])
def test_retention_rejects_out_of_range_and_non_integer_values(store, state, protected, days):
    with pytest.raises(ValueError):
        store.set_retention_days(days)
    assert store.get_retention_days() == 90
    with pytest.raises(ValueError):
        TelemetryStore(state / "other", [protected], retention_days=days)


def test_constructor_retention_seeds_only_a_new_database(state, protected):
    first = TelemetryStore(state, [protected], retention_days=30)
    try:
        assert first.get_retention_days() == 30
    finally:
        first.close()
    reopened = TelemetryStore(state, [protected], retention_days=90)
    try:
        assert reopened.get_retention_days() == 30
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Record input validation
# --------------------------------------------------------------------------- #


def test_record_returns_none_and_persists_one_event(store):
    assert store.record(queue_event()) is None
    assert len(store.list_events()) == 1


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"event_id": identifier()}, object(), "event"],
)
def test_record_rejects_arbitrary_event_payloads(store, payload):
    with pytest.raises(ValueError):
        store.record(payload)
    assert store.list_events() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_ms": -1},
        {"duration_ms": True},
        {"duration_ms": 1.5},
        {"invocation_count": -1},
        {"invocation_count": True},
        {"invocation_count": 2},
        {"invocation_count": "1"},
        {"outcome": "completed"},
        {"outcome": WorkerState.running},
        {"model_id": "gpt-4"},
        {"model_id": "opencode/"},
        {"model_id": "opencode model"},
        {"model_id": 7},
        {"model_id": True},
    ],
)
def test_record_rejects_invalid_scalar_arguments(store, kwargs):
    attempt, cycle = identifier(), identifier()
    with pytest.raises(ValueError):
        store.record(start(attempt, cycle=cycle), **kwargs)
    assert store.list_events() == []


def test_record_rejects_non_opencode_model_ids_without_persisting_them(store, state):
    attempt, cycle = identifier(), identifier()
    with pytest.raises(ValueError):
        store.record(start(attempt, cycle=cycle), model_id="SECRET-model")
    assert store.list_events() == []
    store.close()
    assert b"SECRET" not in database_bytes(state)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("SECRET traceback text"),
        "SECRET text",
        QueueError(ErrorCategory.worker_failed),
        {"category": "worker_failed"},
    ],
)
def test_record_rejects_raw_exception_text(store, state, error):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    with pytest.raises(ValueError):
        store.record(terminal(attempt, cycle=cycle), outcome=AttemptOutcome.failed, error=error)
    assert len(store.list_events()) == 1
    store.close()
    assert b"SECRET" not in database_bytes(state)


def test_record_persists_only_the_error_category(store, state):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    store.record(
        terminal(attempt, cycle=cycle, error=operational_error(ErrorCategory.worker_failed)),
        outcome=AttemptOutcome.failed,
    )
    assert [view.error_category for view in store.list_events()] == [None, "worker_failed"]
    store.close()
    payload = database_bytes(state)
    assert b"worker_failed" in payload
    assert b"Worker failed." not in payload


def test_record_rejects_conflicting_error_categories(store):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    with pytest.raises(ValueError):
        store.record(
            terminal(attempt, cycle=cycle, error=operational_error(ErrorCategory.stale_head)),
            outcome=AttemptOutcome.failed,
            error=operational_error(ErrorCategory.worker_failed),
        )
    assert len(store.list_events()) == 1


# --------------------------------------------------------------------------- #
# Lifecycle accounting
# --------------------------------------------------------------------------- #


def test_reservation_records_zero_invocations_and_no_start_facts(store):
    attempt, cycle = identifier(), identifier()
    store.record(reservation(attempt, cycle=cycle), outcome=AttemptOutcome.reserved)
    [view] = store.list_events()
    assert view.attempt_count == 0
    assert view.outcome == "reserved"
    assert view.duration_ms is None
    assert view.model_id is None
    assert view.error_category is None
    assert view.timestamp == NOW


def test_first_running_event_attaches_duration_and_model_with_one_invocation(store):
    attempt, cycle = identifier(), identifier()
    store.record(reservation(attempt, cycle=cycle), outcome=AttemptOutcome.reserved)
    store.record(start(attempt, cycle=cycle), duration_ms=1000, model_id=MODEL)
    views = store.list_events()
    assert [view.outcome for view in views] == ["reserved", "running"]
    assert views[1].attempt_count == 1
    assert views[1].duration_ms == 1000
    assert views[1].model_id == MODEL


def test_terminal_after_start_records_duration_outcome_and_error_with_one_invocation(store):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    store.record(
        terminal(attempt, cycle=cycle, error=operational_error(ErrorCategory.worker_failed)),
        duration_ms=2500,
        outcome=AttemptOutcome.failed,
    )
    terminal_view = store.list_events()[-1]
    assert terminal_view.attempt_count == 1
    assert terminal_view.duration_ms == 2500
    assert terminal_view.outcome == "failed"
    assert terminal_view.error_category == "worker_failed"
    assert terminal_view.from_state == "running"
    assert terminal_view.to_state == "ready"


def test_terminal_without_a_start_records_zero_invocations(store):
    attempt, cycle = identifier(), identifier()
    store.record(terminal(attempt, cycle=cycle), outcome=AttemptOutcome.cancelled)
    [view] = store.list_events()
    assert view.attempt_count == 0
    assert view.outcome == "cancelled"


def test_model_unavailable_before_start_records_category_without_attempt_budget(store):
    attempt, cycle = identifier(), identifier()
    store.record(reservation(attempt, cycle=cycle), outcome=AttemptOutcome.reserved)
    store.record(
        terminal(attempt, cycle=cycle, error=operational_error(ErrorCategory.model_unavailable)),
        outcome=AttemptOutcome.model_unavailable,
    )
    views = store.list_events()
    assert [view.attempt_count for view in views] == [0, 0]
    assert views[1].outcome == "model_unavailable"
    assert views[1].error_category == "model_unavailable"


def test_recovered_unknown_records_the_failed_family_terminal(store):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    store.record(
        terminal(attempt, cycle=cycle, error=operational_error(ErrorCategory.worker_failed)),
        duration_ms=4200,
        outcome=AttemptOutcome.recovered_unknown,
    )
    views = store.list_events()
    assert [view.outcome for view in views] == ["running", "recovered_unknown"]
    assert views[1].attempt_count == 1
    assert views[1].duration_ms == 4200
    assert views[1].error_category == "worker_failed"

    with pytest.raises(ValueError):
        store.record(
            terminal(
                attempt,
                cycle=cycle,
                occurred_at=LATER + timedelta(minutes=2),
                error=operational_error(ErrorCategory.worker_failed),
            ),
            outcome=AttemptOutcome.failed,
        )
    assert len(store.list_events()) == 2


def test_failed_family_terminal_without_an_explicit_outcome_is_rejected(store):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    with pytest.raises(ValueError):
        store.record(
            terminal(
                attempt,
                cycle=cycle,
                error=operational_error(ErrorCategory.worker_failed),
            ),
            duration_ms=1000,
        )
    assert [view.outcome for view in store.list_events()] == ["running"]


def test_model_unavailable_outcome_derives_its_error_category(store):
    attempt, cycle = identifier(), identifier()
    event = terminal(attempt, cycle=cycle)
    store.record(event, outcome=AttemptOutcome.model_unavailable)
    [view] = store.list_events()
    assert view.outcome == "model_unavailable"
    assert view.error_category == "model_unavailable"
    assert view.attempt_count == 0
    store.record(replay(event), outcome=AttemptOutcome.model_unavailable)
    assert len(store.list_events()) == 1


def test_reservation_without_a_count_hint_records_no_invocation(store):
    attempt, cycle = identifier(), identifier()
    store.record(reservation(attempt, cycle=cycle))
    [view] = store.list_events()
    assert view.outcome == "reserved"
    assert view.attempt_count == 0
    store.record(start(attempt, cycle=cycle), duration_ms=900, model_id=MODEL)
    views = store.list_events()
    assert [view.outcome for view in views] == ["reserved", "running"]
    assert views[1].attempt_count == 1


def test_model_unavailable_after_start_records_the_started_attempt_only(store):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    store.record(
        terminal(attempt, cycle=cycle, error=operational_error(ErrorCategory.model_unavailable)),
        outcome=AttemptOutcome.model_unavailable,
    )
    views = store.list_events()
    assert [view.attempt_count for view in views] == [1, 1]
    assert views[1].error_category == "model_unavailable"


@pytest.mark.parametrize(
    "build, kwargs",
    [
        (lambda attempt, cycle: reservation(attempt, cycle=cycle), {"outcome": AttemptOutcome.reserved, "invocation_count": 1}),
        (lambda attempt, cycle: start(attempt, cycle=cycle), {"outcome": AttemptOutcome.running, "invocation_count": 0}),
        (lambda attempt, cycle: terminal(attempt, cycle=cycle), {"outcome": AttemptOutcome.completed, "invocation_count": 1}),
        (lambda attempt, cycle: terminal(attempt, cycle=cycle), {"outcome": AttemptOutcome.failed, "invocation_count": 1}),
        (lambda attempt, cycle: queue_event(cycle_id=cycle), {"invocation_count": 1}),
        (lambda attempt, cycle: reservation(attempt, cycle=cycle), {"outcome": AttemptOutcome.reserved, "duration_ms": 10}),
        (lambda attempt, cycle: reservation(attempt, cycle=cycle), {"outcome": AttemptOutcome.reserved, "model_id": MODEL}),
    ],
)
def test_record_rejects_inconsistent_lifecycle_counts(store, build, kwargs):
    attempt, cycle = identifier(), identifier()
    with pytest.raises(ValueError):
        store.record(build(attempt, cycle), **kwargs)
    assert store.list_events() == []


def test_record_rejects_reservation_after_the_attempt_started(store):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    with pytest.raises(ValueError):
        store.record(reservation(attempt, cycle=cycle), outcome=AttemptOutcome.reserved)
    assert len(store.list_events()) == 1


def test_record_rejects_an_attempt_less_start_transition(store):
    event = queue_event(from_state=WorkerState.ready, to_state=WorkerState.running)
    with pytest.raises(ValueError):
        store.record(event)
    assert store.list_events() == []


def test_record_normalises_aware_event_timestamps_to_utc(store):
    store.record(queue_event(occurred_at=NOW.astimezone(LOCAL)))
    [view] = store.list_events()
    assert view.timestamp == NOW
    assert view.timestamp.tzinfo is UTC


# --------------------------------------------------------------------------- #
# Deduplication
# --------------------------------------------------------------------------- #


def test_exact_event_id_replay_is_a_noop(store):
    event = queue_event()
    store.record(event)
    store.record(event)
    assert len(store.list_events()) == 1


def test_replayed_lifecycle_events_after_a_crash_are_noops(store):
    attempt, cycle = identifier(), identifier()
    reserved = reservation(attempt, cycle=cycle)
    started = start(attempt, cycle=cycle)
    finished = terminal(attempt, cycle=cycle)
    store.record(reserved, outcome=AttemptOutcome.reserved)
    store.record(started, model_id=MODEL)
    store.record(finished, outcome=AttemptOutcome.completed)
    assert len(store.list_events()) == 3

    store.record(replay(reserved), outcome=AttemptOutcome.reserved)
    store.record(replay(started), model_id=MODEL)
    store.record(replay(finished), outcome=AttemptOutcome.completed)
    assert len(store.list_events()) == 3
    assert [view.outcome for view in store.list_events()] == ["reserved", "running", "completed"]


def test_replayed_terminal_without_an_outcome_adds_no_unknown_row(store):
    attempt, cycle = identifier(), identifier()
    store.record(reservation(attempt, cycle=cycle), outcome=AttemptOutcome.reserved)
    store.record(start(attempt, cycle=cycle), duration_ms=1000, model_id=MODEL)
    finished = terminal(attempt, cycle=cycle)
    store.record(finished, duration_ms=1200, outcome=AttemptOutcome.completed)
    with pytest.raises(ValueError):
        store.record(replay(finished), duration_ms=1200)
    assert [view.outcome for view in store.list_events()] == [
        "reserved",
        "running",
        "completed",
    ]


def test_conflicting_facts_for_the_same_attempt_identity_are_rejected(store):
    attempt, cycle = identifier(), identifier()
    started = start(attempt, cycle=cycle)
    store.record(started, duration_ms=10, model_id=MODEL)
    with pytest.raises(ValueError):
        store.record(
            replay(started, occurred_at=LATER + timedelta(seconds=1)),
            duration_ms=10,
            model_id=MODEL,
        )
    with pytest.raises(ValueError):
        store.record(replay(started), duration_ms=11, model_id=MODEL)
    assert len(store.list_events()) == 1


def test_conflicting_facts_for_the_same_event_id_are_rejected(store):
    started = start(identifier(), cycle=identifier())
    store.record(started, model_id=MODEL)
    with pytest.raises(ValueError):
        store.record(
            queue_event(
                event_id=started.event_id,
                cycle_id=started.cycle_id,
                attempt_id=started.attempt_id,
                from_state=WorkerState.queued,
                to_state=WorkerState.running,
                occurred_at=LATER,
                head_sha="b" * 40,
            ),
            model_id=MODEL,
        )
    assert len(store.list_events()) == 1


def test_conflicting_terminal_outcomes_for_one_attempt_are_rejected(store):
    attempt, cycle = identifier(), identifier()
    store.record(start(attempt, cycle=cycle), model_id=MODEL)
    store.record(terminal(attempt, cycle=cycle), outcome=AttemptOutcome.completed)
    with pytest.raises(ValueError):
        store.record(
            terminal(attempt, cycle=cycle, occurred_at=LATER + timedelta(minutes=2)),
            outcome=AttemptOutcome.failed,
        )
    assert len(store.list_events()) == 2


def test_rejects_an_outcome_that_contradicts_the_error_category(store):
    attempt, cycle = identifier(), identifier()
    with pytest.raises(ValueError):
        store.record(
            terminal(
                attempt,
                cycle=cycle,
                error=operational_error(ErrorCategory.worker_failed),
            ),
            outcome=AttemptOutcome.completed,
        )
    with pytest.raises(ValueError):
        store.record(
            terminal(
                attempt,
                cycle=cycle,
                error=operational_error(ErrorCategory.model_unavailable),
            ),
            outcome=AttemptOutcome.failed,
        )
    assert store.list_events() == []


def test_attemptless_closure_events_deduplicate_by_cycle_and_states(store):
    cycle = identifier()
    closure = queue_event(
        cycle_id=cycle,
        from_state=WorkerState.blocked,
        to_state=WorkerState.ready,
    )
    store.record(closure)
    store.record(replay(closure))
    assert len(store.list_events()) == 1
    assert store.list_events()[0].outcome == "unknown"

    with pytest.raises(ValueError):
        store.record(replay(closure, occurred_at=NOW + timedelta(seconds=1)))

    store.record(replay(closure, cycle_id=identifier()))
    store.record(replay(closure, from_state=WorkerState.queued, to_state=WorkerState.blocked))
    assert len(store.list_events()) == 3


def test_duplicate_start_replay_with_a_fresh_event_id_is_a_noop(store):
    attempt, cycle = identifier(), identifier()
    started = start(attempt, cycle=cycle)
    store.record(started, duration_ms=250, model_id=MODEL)
    store.record(replay(started), duration_ms=250, model_id=MODEL)
    [view] = store.list_events()
    assert view.outcome == "running"
    assert view.attempt_count == 1


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


def test_expire_removes_only_events_strictly_older_than_retention(store):
    expired = queue_event(cycle_id=identifier(), occurred_at=NOW - timedelta(days=91))
    retained = queue_event(cycle_id=identifier(), occurred_at=NOW - timedelta(days=90))
    store.record(expired)
    store.record(retained)
    assert store.expire(NOW) == 1
    [view] = store.list_events()
    assert view.timestamp == NOW - timedelta(days=90)
    assert store.expire(NOW) == 0
    assert len(store.list_events()) == 1


def test_shortened_retention_expires_the_oldest_events(store):
    store.set_retention_days(30)
    store.record(queue_event(cycle_id=identifier(), occurred_at=NOW - timedelta(days=31)))
    store.record(queue_event(cycle_id=identifier(), occurred_at=NOW - timedelta(days=29)))
    assert store.expire(NOW) == 1
    [view] = store.list_events()
    assert view.timestamp == NOW - timedelta(days=29)


def test_events_survive_reopen_until_expired(state, protected):
    first = TelemetryStore(state, [protected])
    try:
        first.record(queue_event(cycle_id=identifier(), occurred_at=NOW - timedelta(days=91)))
        first.record(queue_event(cycle_id=identifier(), occurred_at=NOW))
    finally:
        first.close()

    reopened = TelemetryStore(state, [protected])
    try:
        assert len(reopened.list_events()) == 2
        assert reopened.expire(NOW) == 1
        [view] = reopened.list_events()
        assert view.timestamp == NOW
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 11, 25, 12, 0),
        NOW.astimezone(LOCAL),
        "2026-11-25T12:00:00+00:00",
    ],
)
def test_expire_and_list_events_require_utc_timestamps(store, timestamp):
    with pytest.raises(ValueError):
        store.expire(timestamp)
    with pytest.raises(ValueError):
        store.list_events(after=timestamp)


def test_expire_and_list_events_reject_a_missing_timestamp(store):
    with pytest.raises(ValueError):
        store.expire(None)
    assert store.list_events(after=None) == []


# --------------------------------------------------------------------------- #
# Listing and schema
# --------------------------------------------------------------------------- #


def test_list_events_filters_strictly_after_the_timestamp(store):
    store.record(queue_event(cycle_id=identifier(), occurred_at=NOW))
    store.record(start(identifier(), cycle=identifier(), occurred_at=LATER), model_id=MODEL)
    views = store.list_events(after=NOW)
    assert [view.timestamp for view in views] == [LATER]
    assert views[0].attempt_count == 1
    assert views[0].model_id == MODEL
    assert len(store.list_events()) == 2


def test_list_events_returns_telemetry_view_adapters(store):
    store.record(queue_event(cycle_id=identifier()))
    [view] = store.list_events()
    assert isinstance(view, TelemetryViewAdapter)
    assert tuple(view) == (
        "timestamp",
        "duration_ms",
        "repository",
        "pr_number",
        "head_sha",
        "from_state",
        "to_state",
        "attempt_count",
        "outcome",
        "model_id",
        "error_category",
    )
    assert view.as_mapping() == {
        "timestamp": NOW,
        "duration_ms": None,
        "repository": REPOSITORY,
        "pr_number": PR_NUMBER,
        "head_sha": SHA,
        "from_state": "queued",
        "to_state": "blocked",
        "attempt_count": 0,
        "outcome": "unknown",
        "model_id": None,
        "error_category": None,
    }


def test_schema_is_fixed_and_excludes_operational_content(tmp_path, protected):
    state = tmp_path / "state"
    opened = TelemetryStore(state, [protected])
    opened.close()
    path = state / DATABASE
    assert path.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert tuple(row[1] for row in connection.execute("PRAGMA table_info(events)")) == EVENT_COLUMNS
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {"events", "settings"}
        indexed = {}
        for _sequence, index_name, *_rest in connection.execute(
            "PRAGMA index_list(events)"
        ):
            indexed[index_name] = tuple(
                row[2] for row in connection.execute(f"PRAGMA index_info({index_name})")
            )
        assert ("timestamp",) in indexed.values()
        assert ("repository", "pr_number") in indexed.values()
        columns = set()
        for table in tables:
            columns |= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            assert not any(fragment in column.lower() for fragment in FORBIDDEN_COLUMN_FRAGMENTS), column


def test_closed_store_refuses_operations(state, protected):
    opened = TelemetryStore(state, [protected])
    opened.close()
    with pytest.raises(StorageUnavailable):
        opened.record(queue_event())
    with pytest.raises(StorageUnavailable):
        opened.list_events()
    with pytest.raises(StorageUnavailable):
        opened.get_retention_days()
    opened.close()
