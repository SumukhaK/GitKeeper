"""Path validation, detached worktree creation, reuse, and preservation."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from gitkeeper.worker.models import (
    ActiveCheckoutSnapshot,
    GitWorktreePort,
    WorktreeIdentity,
    WorktreeRecord,
)
from gitkeeper.worker.worktree_store import WorktreeStore

_MAX_PATH_CHARACTERS = 260
_REPARSE_POINT = 0x400
_RESERVED_NAMES = re.compile(
    r"(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$"
)
_FORBIDDEN_CHARACTERS = frozenset('<>:"/\\|?*')


class WorktreeError(RuntimeError):
    """The worktree lifecycle refused to continue."""


class WorktreeConflict(WorktreeError):
    """An existing record or directory does not match the requested identity."""


class WorktreePathError(WorktreeError, ValueError):
    """A path cannot be used safely outside the managed root."""


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


def _absolute(value: object, field: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{field} must be a pathlib.Path.")
    if not value.is_absolute():
        raise WorktreePathError(f"{field} must be an absolute path.")
    text = str(value)
    if text.startswith("\\\\") or text.startswith("//"):
        raise WorktreePathError(f"{field} must not be a UNC path.")
    return value


def _normalised(value: Path) -> Path:
    return Path(os.path.normcase(str(value.resolve(strict=False))))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _safe_component(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise WorktreePathError(f"{field} is not a safe path component.")
    if any(character in _FORBIDDEN_CHARACTERS for character in value):
        raise WorktreePathError(f"{field} contains a forbidden character.")
    if any(ord(character) < 32 for character in value):
        raise WorktreePathError(f"{field} contains a control character.")
    if value != value.rstrip(" ."):
        raise WorktreePathError(f"{field} must not end with a dot or space.")
    if _RESERVED_NAMES.fullmatch(value):
        raise WorktreePathError(f"{field} is a reserved device name.")
    return value


def _validated_checkouts(values: object) -> tuple[Path, ...]:
    if not isinstance(values, tuple):
        raise TypeError("active_checkouts must be a tuple of paths.")
    result: list[Path] = []
    for value in values:
        path = _absolute(value, "active_checkout")
        if not path.exists() or not path.is_dir():
            raise WorktreePathError(
                "An active checkout must be an existing directory."
            )
        if _is_reparse_point(path):
            raise WorktreePathError("An active checkout must not be a reparse point.")
        result.append(_normalised(path))
    if len(set(result)) != len(result):
        raise WorktreePathError("Active checkouts must be unique.")
    return tuple(result)


def _validated_root(managed_root: object, checkouts: tuple[Path, ...]) -> Path:
    root = _absolute(managed_root, "managed_root")
    if _is_reparse_point(root):
        raise WorktreePathError("managed_root must not be a reparse point.")
    resolved = _normalised(root)
    for checkout in checkouts:
        if _inside(resolved, checkout) or _inside(checkout, resolved):
            raise WorktreePathError(
                "managed_root must not overlap an active checkout."
            )
    return root


class WorktreeManager:
    """Owns the managed worktree layout and its preserved records."""

    def __init__(
        self,
        git: GitWorktreePort,
        store: WorktreeStore,
        managed_root: Path,
        active_checkouts: tuple[Path, ...],
        clock: Callable[[], datetime],
    ) -> None:
        if not isinstance(store, WorktreeStore):
            raise TypeError("store must be a WorktreeStore.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._git = git
        self._store = store
        self._clock = clock
        self._active_checkouts = _validated_checkouts(active_checkouts)
        self._managed_root = _validated_root(managed_root, self._active_checkouts)
        self._baseline = self._snapshot()

    def _snapshot(self) -> tuple[ActiveCheckoutSnapshot, ...]:
        snapshots = self._git.snapshot_active_checkouts()
        if not isinstance(snapshots, tuple):
            raise WorktreeError("Git returned an invalid checkout snapshot.")
        if any(not isinstance(item, ActiveCheckoutSnapshot) for item in snapshots):
            raise WorktreeError("Git returned an invalid checkout snapshot.")
        return snapshots

    def _path_for(self, identity: WorktreeIdentity) -> Path:
        owner, name = identity.repository.split("/", 1)
        _safe_component(owner, "repository owner")
        _safe_component(name, "repository name")
        path = (
            self._managed_root
            / owner
            / name
            / f"pr-{identity.pr_number}"
            / identity.head_sha
            / identity.cycle_id
        )
        if len(str(path)) >= _MAX_PATH_CHARACTERS:
            raise WorktreePathError(
                "The managed worktree path is too long for Windows."
            )
        return path

    def _validate_path(self, path: Path) -> None:
        root = _normalised(self._managed_root)
        resolved = _normalised(path)
        if resolved == root or not _inside(resolved, root):
            raise WorktreePathError(
                "The worktree path resolves outside the managed root."
            )
        for checkout in self._active_checkouts:
            if _inside(resolved, checkout) or _inside(checkout, resolved):
                raise WorktreePathError(
                    "The worktree path overlaps an active checkout."
                )

        current = self._managed_root
        for part in path.relative_to(self._managed_root).parts:
            current = current / part
            if not current.exists():
                continue
            if _is_reparse_point(current):
                raise WorktreePathError(
                    "The worktree path contains a reparse point."
                )
            if not _inside(_normalised(current), root):
                raise WorktreePathError(
                    "The worktree path escapes the managed root."
                )

    def create(
        self, repository: str, pr_number: int, head_sha: str, cycle_id: str
    ) -> WorktreeRecord:
        identity = WorktreeIdentity(
            repository=repository,
            pr_number=pr_number,
            head_sha=head_sha,
            cycle_id=cycle_id,
        )
        path = self._path_for(identity)

        existing = self._store.get(identity)
        if existing is not None:
            if _normalised(existing.path) != _normalised(path):
                raise WorktreeConflict(
                    "The recorded worktree path does not match the identity."
                )
            if not self._git.is_registered(existing.path):
                raise WorktreeConflict(
                    "The recorded worktree is no longer registered with Git."
                )
            if self._git.head(existing.path) != identity.head_sha:
                raise WorktreeConflict(
                    "The recorded worktree HEAD does not match the identity."
                )
            return existing

        self._validate_path(path)
        if path.exists():
            raise WorktreeConflict(
                "The target path already exists without a registered worktree."
            )
        self._git.prepare(identity.repository)
        self._git.fetch(identity.repository, identity.head_sha)
        if not self._git.has_commit(identity.head_sha):
            raise WorktreeError(
                "The requested commit is not present in the external cache."
            )

        before = self._snapshot()
        self._git.add_detached(path, identity.head_sha)
        if self._git.head(path) != identity.head_sha:
            raise WorktreeError(
                "The new worktree HEAD does not match the requested SHA."
            )
        if self._snapshot() != before:
            raise WorktreeError(
                "An active checkout changed while the worktree was created."
            )

        record = WorktreeRecord(
            worktree_id=str(uuid4()),
            identity=identity,
            path=path,
            branch=None,
            created_at=self._clock(),
            preserved_reason=None,
        )
        self._store.put(record)
        return record

    def get(self, identity: WorktreeIdentity) -> WorktreeRecord | None:
        return self._store.get(identity)

    def get_by_id(self, worktree_id: str) -> WorktreeRecord | None:
        return self._store.get_by_id(worktree_id)

    def preserve(self, identity: WorktreeIdentity, reason: str) -> WorktreeRecord:
        record = self._store.get(identity)
        if record is None:
            raise WorktreeConflict("No worktree is registered for this identity.")
        if not isinstance(reason, str) or not reason.strip():
            raise _invalid("reason")
        updated = replace(record, preserved_reason=reason)
        self._store.put(updated)
        return updated

    def assert_active_checkouts_unchanged(self) -> None:
        if self._snapshot() != self._baseline:
            raise WorktreeError(
                "An active checkout changed during the worker operation."
            )

    def active_checkout_paths(self) -> tuple[Path, ...]:
        paths = set(self._active_checkouts)
        paths.update(_normalised(record.path) for record in self._store.list_all())
        return tuple(sorted(paths, key=str))

    def external_active_checkout_paths(self) -> tuple[Path, ...]:
        return self._active_checkouts
