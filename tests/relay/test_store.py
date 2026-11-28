import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from gitkeeper.queue.errors import ErrorCategory, SchemaMismatch
from gitkeeper.relay.state import Phase, PrRecord
from gitkeeper.relay.store import RelayStore

REPO = "SumukhaK/GitKeeper"


def record(
    number: int = 56,
    phase: Phase = Phase.ci_pending,
    *,
    repository: str = REPO,
    review_rounds: int = 0,
    ci_attempts: int = 0,
) -> PrRecord:
    return PrRecord(
        repository=repository,
        number=number,
        head_sha="abc123",
        phase=phase,
        review_rounds=review_rounds,
        ci_attempts=ci_attempts,
        reviewed_sha="reviewed" if phase is Phase.ready_to_merge else None,
        blocked_reason="ci_infrastructure" if phase is Phase.blocked else None,
    )


def test_creates_one_wal_database_with_version_one_and_the_required_schema(
    tmp_path,
) -> None:
    path = tmp_path / "state" / "relay.sqlite3"
    store = RelayStore(path)
    try:
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
            columns = {row[1] for row in db.execute("PRAGMA table_info(pull_requests)").fetchall()}
            assert columns == {
                "repository",
                "number",
                "head_sha",
                "phase",
                "review_rounds",
                "ci_attempts",
                "reviewed_sha",
                "blocked_reason",
                "worker_prompt",
                "updated_at",
            }
            primary_key = [
                row[1]
                for row in sorted(
                    db.execute("PRAGMA table_info(pull_requests)").fetchall(),
                    key=lambda row: row[5],
                )
                if row[5]
            ]
            assert primary_key == ["repository", "number"]
            assert [row[1] for row in db.execute("PRAGMA table_info(events)").fetchall()] == [
                "repository",
                "number",
                "at",
                "kind",
                "detail",
            ]
    finally:
        store.close()


@pytest.mark.parametrize("git_entry", ["directory", "file"])
@pytest.mark.parametrize("relative_path", ["relay.sqlite3", "state/relay.sqlite3"])
def test_refuses_a_database_inside_a_git_checkout_without_creating_it(
    tmp_path, git_entry: str, relative_path: str
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    if git_entry == "directory":
        (checkout / ".git").mkdir()
    else:
        (checkout / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    path = checkout / relative_path

    with pytest.raises(ValueError, match="outside a git checkout"):
        RelayStore(path)

    assert not path.exists()
    assert not path.parent.exists() or path.parent == checkout


@pytest.mark.parametrize("phase", [Phase.ready_to_merge, Phase.blocked])
def test_round_trips_every_record_field_and_a_worker_prompt(tmp_path, phase: Phase) -> None:
    path = tmp_path / "relay.sqlite3"
    expected = record(phase=phase, review_rounds=2, ci_attempts=3)
    store = RelayStore(path)
    try:
        assert store.get(REPO, 56) is None
        assert store.get_prompt(REPO, 56) is None
        store.put(expected, worker_prompt="Implement the requested changes.")
        assert store.get(REPO, 56) == expected
        assert store.get_prompt(REPO, 56) == "Implement the requested changes."
        with closing(sqlite3.connect(path)) as db:
            updated_at = db.execute(
                "SELECT updated_at FROM pull_requests WHERE repository = ? AND number = ?",
                (REPO, 56),
            ).fetchone()[0]
        assert datetime.fromisoformat(updated_at).utcoffset() == timedelta(0)
    finally:
        store.close()


def test_put_upserts_and_preserves_an_existing_prompt_when_omitted(tmp_path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite3")
    try:
        store.put(record(), worker_prompt="first prompt")
        updated = record(phase=Phase.changes_requested, review_rounds=1, ci_attempts=1)
        store.put(updated)
        assert store.get(REPO, 56) == updated
        assert store.get_prompt(REPO, 56) == "first prompt"
        store.put(updated, worker_prompt="replacement prompt")
        assert store.get_prompt(REPO, 56) == "replacement prompt"
    finally:
        store.close()


def test_list_active_excludes_only_done_records(tmp_path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite3")
    try:
        active = [record(58, Phase.blocked), record(57, repository="owner/other")]
        for item in [*active, record(56, Phase.done)]:
            store.put(item)
        assert set(store.list_active()) == set(active)
    finally:
        store.close()


def test_metrics_returns_persisted_counters_and_zeroes_for_a_missing_pr(tmp_path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite3")
    try:
        assert store.metrics(REPO, 56) == {"review_rounds": 0, "ci_attempts": 0}
        store.put(record(review_rounds=2, ci_attempts=3))
        store.record_event(REPO, 56, "review", "round")
        assert store.metrics(REPO, 56) == {"review_rounds": 2, "ci_attempts": 3}
    finally:
        store.close()


def test_events_use_utc_iso_timestamps_and_cannot_be_updated_or_deleted(tmp_path) -> None:
    path = tmp_path / "relay.sqlite3"
    store = RelayStore(path)
    explicit = datetime(2026, 11, 20, 12, 30, tzinfo=timezone(timedelta(hours=2)))
    try:
        store.record_event(REPO, 56, "review", "round", at=explicit)
        store.record_event(REPO, 56, "ci", "attempt")
        with closing(sqlite3.connect(path)) as db:
            events = db.execute("SELECT at, kind, detail FROM events ORDER BY rowid").fetchall()
        assert events[0] == ("2026-11-20T10:30:00+00:00", "review", "round")
        assert datetime.fromisoformat(events[1][0]).utcoffset() == timedelta(0)
        assert events[1][1:] == ("ci", "attempt")
        with closing(sqlite3.connect(path)) as db:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute("UPDATE events SET detail = 'changed'")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute("DELETE FROM events")
    finally:
        store.close()


def test_reopening_retains_records_prompts_and_events(tmp_path) -> None:
    path = tmp_path / "relay.sqlite3"
    store = RelayStore(path)
    store.put(record(phase=Phase.changes_requested, review_rounds=1), worker_prompt="prompt")
    store.record_event(REPO, 56, "verdict", "changes_requested")
    store.close()

    reopened = RelayStore(path)
    try:
        assert reopened.get(REPO, 56) == record(phase=Phase.changes_requested, review_rounds=1)
        assert reopened.get_prompt(REPO, 56) == "prompt"
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("version", [0, 2])
def test_a_different_schema_version_raises_without_deleting_or_changing_the_file(
    tmp_path, version: int
) -> None:
    path = tmp_path / "relay.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE sentinel (value TEXT)")
        db.execute("INSERT INTO sentinel VALUES ('keep me')")
        db.execute(f"PRAGMA user_version = {version}")
        db.commit()
    before = path.read_bytes()

    with pytest.raises(SchemaMismatch, match="incompatible") as caught:
        RelayStore(path)

    assert caught.value.category is ErrorCategory.schema_mismatch
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT value FROM sentinel").fetchone()[0] == "keep me"
