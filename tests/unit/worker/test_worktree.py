import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from gitkeeper.queue.errors import SchemaMismatch, StorageUnavailable
from gitkeeper.worker.models import (
    ActiveCheckoutSnapshot,
    WorktreeIdentity,
    WorktreeRecord,
)
from gitkeeper.worker.worktree import (
    WorktreeConflict,
    WorktreeError,
    WorktreeManager,
    WorktreePathError,
)
from gitkeeper.worker.worktree_store import WorktreeStore

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_OTHER_SHA = "b" * 40


class FakeGit:
    def __init__(self, checkouts: tuple[Path, ...]) -> None:
        self.commits: set[str] = set()
        self.heads: dict[Path, str] = {}
        self.commit_available = True
        self.calls: list[tuple[object, ...]] = []
        self.snapshots = tuple(
            ActiveCheckoutSnapshot(
                path=checkout,
                branch="main",
                head_sha=_OTHER_SHA,
                dirty_paths=(),
                untracked_paths=(),
            )
            for checkout in checkouts
        )

    def prepare(self, repository: str) -> Path:
        self.calls.append(("prepare", repository))
        return Path("C:/gitkeeper-cache") / repository.replace("/", "-")

    def fetch(self, repository: str, head_sha: str) -> None:
        self.calls.append(("fetch", repository, head_sha))
        self.commits.add(head_sha)

    def has_commit(self, head_sha: str) -> bool:
        return self.commit_available and head_sha in self.commits

    def add_detached(self, path: Path, head_sha: str) -> None:
        self.calls.append(("add_detached", path, head_sha))
        path.mkdir(parents=True, exist_ok=True)
        self.heads[path] = head_sha

    def head(self, path: Path) -> str:
        if path not in self.heads:
            raise WorktreeError("The worktree is not registered.")
        return self.heads[path]

    def is_registered(self, path: Path) -> bool:
        return path in self.heads

    def snapshot_active_checkouts(self) -> tuple[ActiveCheckoutSnapshot, ...]:
        return self.snapshots


def _checkout(tmp_path: Path) -> Path:
    path = tmp_path / "checkout"
    path.mkdir()
    return path


def _manager(tmp_path: Path) -> tuple[WorktreeManager, FakeGit, WorktreeStore, Path]:
    checkout = _checkout(tmp_path)
    git = FakeGit((checkout,))
    store = WorktreeStore(tmp_path / "state", (checkout,))
    manager = WorktreeManager(
        git=git,
        store=store,
        managed_root=tmp_path / "managed",
        active_checkouts=(checkout,),
        clock=lambda: _AT,
    )
    return manager, git, store, checkout


def _identity(cycle_id: str | None = None) -> WorktreeIdentity:
    return WorktreeIdentity(
        repository="SumukhaK/Setu-mobile",
        pr_number=42,
        head_sha=_SHA,
        cycle_id=cycle_id or str(uuid4()),
    )


def test_create_uses_exact_detached_layout(tmp_path) -> None:
    manager, git, store, _ = _manager(tmp_path)
    identity = _identity()

    record = manager.create(
        identity.repository, identity.pr_number, identity.head_sha, identity.cycle_id
    )

    assert record.identity == identity
    assert record.branch is None
    assert record.preserved_reason is None
    assert record.path == (
        tmp_path
        / "managed"
        / "SumukhaK"
        / "Setu-mobile"
        / "pr-42"
        / _SHA
        / identity.cycle_id
    )
    assert ("prepare", "SumukhaK/Setu-mobile") in git.calls
    assert ("fetch", "SumukhaK/Setu-mobile", _SHA) in git.calls
    assert ("add_detached", record.path, _SHA) in git.calls
    assert manager.get(identity) == record
    assert manager.get_by_id(record.worktree_id) == record
    store.close()


def test_same_sha_reopen_uses_a_fresh_record_and_rejects_the_old_path(
    tmp_path,
) -> None:
    manager, _, store, _ = _manager(tmp_path)
    first_identity = _identity()
    second_identity = WorktreeIdentity(
        repository="SumukhaK/Setu-mobile",
        pr_number=42,
        head_sha=_SHA,
        cycle_id=str(uuid4()),
    )

    first = manager.create(
        first_identity.repository,
        first_identity.pr_number,
        first_identity.head_sha,
        first_identity.cycle_id,
    )
    second = manager.create(
        second_identity.repository,
        second_identity.pr_number,
        second_identity.head_sha,
        second_identity.cycle_id,
    )

    assert first.path != second.path
    assert first.path.is_dir()
    assert first.worktree_id != second.worktree_id
    assert manager.get(first_identity) == first
    assert manager.get(second_identity) == second
    assert manager.get_by_id(first.worktree_id).path == first.path
    store.close()


def test_reuse_requires_the_registered_exact_head(tmp_path) -> None:
    manager, git, store, _ = _manager(tmp_path)
    identity = _identity()
    record = manager.create(
        identity.repository, identity.pr_number, identity.head_sha, identity.cycle_id
    )
    git.heads[record.path] = _OTHER_SHA

    with pytest.raises(WorktreeConflict):
        manager.create(
            identity.repository,
            identity.pr_number,
            identity.head_sha,
            identity.cycle_id,
        )

    store.close()


def test_preserve_is_terminal_and_survives_reopen(tmp_path) -> None:
    manager, _, store, checkout = _manager(tmp_path)
    identity = _identity()
    manager.create(
        identity.repository, identity.pr_number, identity.head_sha, identity.cycle_id
    )

    preserved = manager.preserve(identity, "worker failed")
    assert preserved.preserved_reason == "worker failed"
    store.close()

    reopened = WorktreeStore(tmp_path / "state", (checkout,))
    reopened_manager = WorktreeManager(
        git=FakeGit((checkout,)),
        store=reopened,
        managed_root=tmp_path / "managed",
        active_checkouts=(checkout,),
        clock=lambda: _AT,
    )
    assert reopened_manager.get(identity).preserved_reason == "worker failed"
    reopened.close()


def test_active_checkout_paths_include_managed_worktrees(tmp_path) -> None:
    manager, _, store, checkout = _manager(tmp_path)
    identity = _identity()
    record = manager.create(
        identity.repository, identity.pr_number, identity.head_sha, identity.cycle_id
    )

    assert manager.external_active_checkout_paths() == (checkout,)
    assert set(manager.active_checkout_paths()) == {checkout, record.path}
    store.close()


def test_assert_active_checkouts_unchanged_detects_dirty_state(tmp_path) -> None:
    manager, git, store, checkout = _manager(tmp_path)
    manager.assert_active_checkouts_unchanged()

    git.snapshots = (
        ActiveCheckoutSnapshot(
            path=checkout,
            branch="main",
            head_sha=_OTHER_SHA,
            dirty_paths=("src/App.kt",),
            untracked_paths=(),
        ),
    )

    with pytest.raises(WorktreeError):
        manager.assert_active_checkouts_unchanged()
    store.close()


def test_unregistered_existing_directory_is_preserved(tmp_path) -> None:
    manager, _, store, _ = _manager(tmp_path)
    identity = _identity()
    path = (
        tmp_path
        / "managed"
        / "SumukhaK"
        / "Setu-mobile"
        / "pr-42"
        / _SHA
        / identity.cycle_id
    )
    path.mkdir(parents=True)
    sentinel = path / "sentinel.txt"
    sentinel.write_text("preserve me", encoding="utf-8")

    with pytest.raises(WorktreeConflict):
        manager.create(
            identity.repository,
            identity.pr_number,
            identity.head_sha,
            identity.cycle_id,
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    assert store.list_all() == ()
    store.close()


def test_missing_commit_fails_without_creating_a_record(tmp_path) -> None:
    manager, git, store, _ = _manager(tmp_path)
    git.commit_available = False
    identity = _identity()

    with pytest.raises(WorktreeError):
        manager.create(
            identity.repository,
            identity.pr_number,
            identity.head_sha,
            identity.cycle_id,
        )

    assert store.list_all() == ()
    store.close()


def test_managed_root_inside_an_active_checkout_is_rejected(tmp_path) -> None:
    checkout = _checkout(tmp_path)
    store = WorktreeStore(tmp_path / "state", (checkout,))

    with pytest.raises(WorktreePathError):
        WorktreeManager(
            git=FakeGit((checkout,)),
            store=store,
            managed_root=checkout / "managed",
            active_checkouts=(checkout,),
            clock=lambda: _AT,
        )
    store.close()


def test_repository_with_extra_components_is_rejected(tmp_path) -> None:
    manager, _, store, _ = _manager(tmp_path)

    with pytest.raises(ValueError):
        manager.create("SumukhaK/Setu-mobile/extra", 42, _SHA, str(uuid4()))

    store.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction creation is required.")
def test_case_insensitive_junction_escape_is_rejected_and_preserved(
    tmp_path,
) -> None:
    manager, _, store, checkout = _manager(tmp_path)
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    junction = managed_root / "SumukhaK"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(checkout)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("Windows junction creation is unavailable in this environment.")
    sentinel = checkout / "sentinel.txt"
    sentinel.write_text("preserve me", encoding="utf-8")

    with pytest.raises(WorktreePathError):
        manager.create("SumukhaK/Setu-mobile", 42, _SHA, str(uuid4()))

    assert junction.exists()
    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    store.close()


def test_state_directory_beneath_a_protected_root_is_rejected(tmp_path) -> None:
    checkout = _checkout(tmp_path)

    with pytest.raises(StorageUnavailable):
        WorktreeStore(checkout / "state", (checkout,))


def test_store_rejects_unknown_schema_without_resetting_it(tmp_path) -> None:
    checkout = _checkout(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    database = state / "gitkeeper-worktrees.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version=7")
        connection.execute("CREATE TABLE foreign_data(value TEXT)")

    with pytest.raises(SchemaMismatch):
        WorktreeStore(state, (checkout,))

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7


def test_store_rejects_conflicting_identity_or_id(tmp_path) -> None:
    checkout = _checkout(tmp_path)
    store = WorktreeStore(tmp_path / "state", (checkout,))
    identity = _identity()
    record = WorktreeRecord(
        worktree_id=str(uuid4()),
        identity=identity,
        path=tmp_path / "managed" / "one",
        branch=None,
        created_at=_AT,
        preserved_reason=None,
    )
    store.put(record)

    with pytest.raises(ValueError):
        store.put(replace(record, worktree_id=str(uuid4())))

    other_identity = WorktreeIdentity(
        repository="SumukhaK/Setu-mobile",
        pr_number=42,
        head_sha=_SHA,
        cycle_id=str(uuid4()),
    )
    with pytest.raises(ValueError):
        store.put(replace(record, identity=other_identity))

    store.close()


def test_exact_id_lookup_never_falls_back_to_another_record(tmp_path) -> None:
    manager, _, store, _ = _manager(tmp_path)
    first_identity = _identity()
    second_identity = WorktreeIdentity(
        repository="SumukhaK/Setu-mobile",
        pr_number=42,
        head_sha=_SHA,
        cycle_id=str(uuid4()),
    )
    first = manager.create(
        first_identity.repository,
        first_identity.pr_number,
        first_identity.head_sha,
        first_identity.cycle_id,
    )
    manager.create(
        second_identity.repository,
        second_identity.pr_number,
        second_identity.head_sha,
        second_identity.cycle_id,
    )

    assert manager.get_by_id(first.worktree_id) == first
    assert manager.get_by_id(str(uuid4())) is None
    store.close()
