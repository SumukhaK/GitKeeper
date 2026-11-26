"""Frozen worker-side worktree, checkout, and feedback contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from gitkeeper.host_mode.command_broker import ExecutionResult
from gitkeeper.policy.patch_scope import RemotePrState
from gitkeeper.host_mode.readiness import (
    PullRequestFacts,
    WorkerConfiguration,
    is_repository_name,
)
from gitkeeper.policy.models import is_opencode_model_id
from gitkeeper.queue.models import (
    AttemptOutcome,
    CheckState,
    ClaimResult,
    OperationalError,
    RepairReason,
)

_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_TERMINAL_OUTCOMES = frozenset(
    {
        AttemptOutcome.completed,
        AttemptOutcome.model_unavailable,
        AttemptOutcome.failed,
        AttemptOutcome.cancelled,
    }
)
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


@dataclass(frozen=True)
class WorkerLaunchFacts:
    pull_request: PullRequestFacts
    configuration: WorkerConfiguration
    checks: CheckState
    repair_reason: RepairReason

    def __post_init__(self) -> None:
        if not isinstance(self.pull_request, PullRequestFacts):
            raise TypeError("pull_request must be PullRequestFacts.")
        if not isinstance(self.configuration, WorkerConfiguration):
            raise TypeError("configuration must be WorkerConfiguration.")
        if not isinstance(self.checks, CheckState):
            raise TypeError("checks must be CheckState.")
        if not isinstance(self.repair_reason, RepairReason):
            raise TypeError("repair_reason must be a queue RepairReason.")


@dataclass(frozen=True)
class WorkerJob:
    claim: ClaimResult
    worktree: WorktreeRecord
    model_id: str
    repair_reason: RepairReason
    requires_test_change: bool
    feedback: FeedbackRecord | None
    checks: CheckState

    def __post_init__(self) -> None:
        if not isinstance(self.claim, ClaimResult):
            raise TypeError("claim must be a ClaimResult.")
        if not isinstance(self.worktree, WorktreeRecord):
            raise TypeError("worktree must be a WorktreeRecord.")
        if not is_opencode_model_id(self.model_id):
            raise _invalid("model_id")
        if not isinstance(self.repair_reason, RepairReason):
            raise TypeError("repair_reason must be a queue RepairReason.")
        if type(self.requires_test_change) is not bool:
            raise TypeError("requires_test_change must be a boolean.")
        if not isinstance(self.checks, CheckState):
            raise TypeError("checks must be CheckState.")
        if self.feedback is not None and not isinstance(
            self.feedback, FeedbackRecord
        ):
            raise TypeError("feedback must be a FeedbackRecord or None.")

        key = self.claim.lease.key
        cycle_id = self.claim.attempt.cycle_id
        identity = self.worktree.identity
        if (
            identity.repository != key.repository
            or identity.pr_number != key.pr_number
            or identity.head_sha != key.head_sha
            or identity.cycle_id != cycle_id
        ):
            raise _invalid("worktree identity")
        if self.checks.head_sha != key.head_sha:
            raise _invalid("checks")
        if self.requires_test_change and (
            self.repair_reason is not RepairReason.deterministic_check_failure
        ):
            raise _invalid("requires_test_change")
        if self.repair_reason is RepairReason.claude_feedback:
            if self.feedback is None:
                raise _invalid("feedback")
        elif self.feedback is not None:
            raise _invalid("feedback")
        if self.feedback is not None:
            if (
                self.feedback.repository != key.repository
                or self.feedback.pr_number != key.pr_number
                or self.feedback.head_sha != key.head_sha
                or self.feedback.cycle_id != cycle_id
            ):
                raise _invalid("feedback identity")


@dataclass(frozen=True)
class ProcessCompletion:
    outcome: AttemptOutcome
    exit_code: int | None
    error: OperationalError | None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, AttemptOutcome):
            raise TypeError("outcome must be an AttemptOutcome.")
        if self.outcome not in _TERMINAL_OUTCOMES:
            raise _invalid("outcome")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise _invalid("exit_code")
        if self.error is not None and not isinstance(self.error, OperationalError):
            raise TypeError("error must be an OperationalError or None.")


@dataclass(frozen=True)
class WorkerResult:
    outcome: AttemptOutcome
    model_id: str | None
    error: OperationalError | None
    process_started: bool = False
    execution: ExecutionResult | None = None
    execution_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, AttemptOutcome):
            raise TypeError("outcome must be an AttemptOutcome.")
        if self.outcome not in _TERMINAL_OUTCOMES:
            raise _invalid("outcome")
        if self.model_id is not None and not is_opencode_model_id(self.model_id):
            raise _invalid("model_id")
        if self.error is not None and not isinstance(self.error, OperationalError):
            raise TypeError("error must be an OperationalError or None.")
        if self.error is not None and self.outcome is not AttemptOutcome.cancelled:
            raise _invalid("error")
        if type(self.process_started) is not bool:
            raise TypeError("process_started must be a boolean.")
        if self.execution is not None and not isinstance(
            self.execution, ExecutionResult
        ):
            raise TypeError("execution must be an ExecutionResult or None.")
        if self.execution_digest is not None and (
            not isinstance(self.execution_digest, str)
            or not self.execution_digest.strip()
        ):
            raise _invalid("execution_digest")
        if self.execution is not None and self.execution_digest is None:
            raise _invalid("execution_digest")
        if self.execution_digest is not None and self.execution is None:
            raise _invalid("execution_digest")


class WorkerProcessFailure(RuntimeError):
    """A consumed approval whose process did not complete successfully."""

    completion: ProcessCompletion
    process_started: bool
    execution: ExecutionResult | None

    def __init__(
        self,
        completion: ProcessCompletion,
        *,
        process_started: bool = False,
        execution: ExecutionResult | None = None,
    ) -> None:
        if not isinstance(completion, ProcessCompletion):
            raise TypeError("completion must be a ProcessCompletion.")
        if type(process_started) is not bool:
            raise TypeError("process_started must be a boolean.")
        if execution is not None and not isinstance(execution, ExecutionResult):
            raise TypeError("execution must be an ExecutionResult or None.")
        self.completion = completion
        self.process_started = process_started
        self.execution = execution
        super().__init__("Worker process did not complete successfully.")


def _branch(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid("branch")
    if value in {"HEAD", "@", "main", "master"} or value.startswith("refs/"):
        raise _invalid("branch")
    if value.startswith(("-", "/", ".")) or value.endswith(("/", ".lock", ".")):
        raise _invalid("branch")
    if (
        ".." in value
        or "//" in value
        or any(character in value for character in "~^:?*[\\")
        or any(ord(character) < 32 or character.isspace() for character in value)
    ):
        raise _invalid("branch")
    return value


@dataclass(frozen=True)
class PublishTarget:
    repository: str
    pr_number: int
    branch: str
    head_repository: str
    is_default_branch: bool
    remote: RemotePrState

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number"))
        object.__setattr__(self, "branch", _branch(self.branch))
        object.__setattr__(
            self, "head_repository", _repository(self.head_repository)
        )
        if (
            self.head_repository.casefold() != self.repository.casefold()
        ):
            raise _invalid("head_repository")
        if type(self.is_default_branch) is not bool:
            raise TypeError("is_default_branch must be a boolean.")
        if not isinstance(self.remote, RemotePrState):
            raise TypeError("remote must be a RemotePrState.")
        if (
            self.remote.head_repository is None
            or self.remote.head_repository.casefold()
            != self.head_repository.casefold()
        ):
            raise _invalid("remote.head_repository")


@dataclass(frozen=True)
class PublishResult:
    status: str
    commit_sha: str | None
    reason_codes: tuple[str, ...]
    error: OperationalError | None

    def __post_init__(self) -> None:
        if self.status not in {"published", "blocked", "unknown"}:
            raise _invalid("status")
        if self.commit_sha is not None and (
            not isinstance(self.commit_sha, str)
            or _SHA_PATTERN.fullmatch(self.commit_sha) is None
        ):
            raise _invalid("commit_sha")
        if not isinstance(self.reason_codes, tuple) or any(
            not isinstance(code, str) or not code for code in self.reason_codes
        ):
            raise _invalid("reason_codes")
        if self.error is not None and not isinstance(self.error, OperationalError):
            raise TypeError("error must be an OperationalError or None.")
        if self.status == "published" and self.commit_sha is None:
            raise _invalid("commit_sha")
