"""Frozen worker-side worktree, checkout, and feedback contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from gitkeeper.host_mode.readiness import is_repository_name

_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_MAX_FEEDBACK_CHARACTERS = 16_384


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


def _repository(value: object) -> str:
    if not isinstance(value, str) or not is_repository_name(value):
        raise _invalid("repository")
    return value


def _integer(value: object, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid(field)
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA_PATTERN.fullmatch(value) is None:
        raise _invalid("head_sha")
    return value


def _uuid4(value: object, field: str) -> str:
    if not isinstance(value, str) or value != value.lower():
        raise _invalid(field)
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise _invalid(field) from None
    if parsed.version != 4 or str(parsed) != value:
        raise _invalid(field)
    return value


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _invalid(field)
    if value.utcoffset() != timedelta(0):
        raise _invalid(field)
    return value.astimezone(timezone.utc)


def _absolute_path(value: object) -> Path:
    if not isinstance(value, Path):
        raise TypeError("path must be a pathlib.Path.")
    if not value.is_absolute():
        raise _invalid("path")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field)
    return value


@dataclass(frozen=True)
class WorktreeIdentity:
    repository: str
    pr_number: int
    head_sha: str
    cycle_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number"))
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))


@dataclass(frozen=True)
class WorktreeRecord:
    worktree_id: str
    identity: WorktreeIdentity
    path: Path
    branch: str | None
    created_at: datetime
    preserved_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WorktreeIdentity):
            raise TypeError("identity must be a WorktreeIdentity.")
        object.__setattr__(
            self, "worktree_id", _uuid4(self.worktree_id, "worktree_id")
        )
        object.__setattr__(self, "path", _absolute_path(self.path))
        object.__setattr__(self, "branch", _optional_string(self.branch, "branch"))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "preserved_reason",
            _optional_string(self.preserved_reason, "preserved_reason"),
        )


@dataclass(frozen=True)
class ActiveCheckoutSnapshot:
    path: Path
    branch: str | None
    head_sha: str
    dirty_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _absolute_path(self.path))
        object.__setattr__(self, "branch", _optional_string(self.branch, "branch"))
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        for field in ("dirty_paths", "untracked_paths"):
            values = getattr(self, field)
            if not isinstance(values, tuple):
                raise _invalid(field)
            if any(not isinstance(value, str) or not value for value in values):
                raise _invalid(field)
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise _invalid(field)


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    repository: str
    pr_number: int
    head_sha: str
    cycle_id: str
    text: str
    created_at: datetime
    attempt_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "feedback_id", _uuid4(self.feedback_id, "feedback_id")
        )
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number"))
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        if not isinstance(self.text, str):
            raise TypeError("text must be a string.")
        text = self.text.strip()
        if (
            not text
            or len(text) > _MAX_FEEDBACK_CHARACTERS
            or "\x00" in text
        ):
            raise _invalid("text")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.attempt_id is not None:
            object.__setattr__(
                self, "attempt_id", _uuid4(self.attempt_id, "attempt_id")
            )


@runtime_checkable
class GitWorktreePort(Protocol):
    """The Git operations the worktree manager needs.

    No method accepts a credential, URL, or shell string. Implementations own
    the trusted Git process and must preserve every existing worktree on error.
    """

    def prepare(self, repository: str) -> Path: ...

    def fetch(self, repository: str, head_sha: str) -> None: ...

    def has_commit(self, head_sha: str) -> bool: ...

    def add_detached(self, path: Path, head_sha: str) -> None: ...

    def head(self, path: Path) -> str: ...

    def is_registered(self, path: Path) -> bool: ...

    def snapshot_active_checkouts(self) -> tuple[ActiveCheckoutSnapshot, ...]: ...
