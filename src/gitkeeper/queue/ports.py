"""Queue-owned ports consumed by worker adapters.

This module deliberately imports no ``gitkeeper.worker`` module. The worker
implements these protocols and maps its records to these neutral values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID

from gitkeeper.host_mode.readiness import PullRequestFacts, WorkerConfiguration
from gitkeeper.queue.models import CheckState, ClaimResult, QueueKey, RepairReason

_MAX_FEEDBACK_CHARACTERS = 16_384


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


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


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("text must be a string.")
    text = value.strip()
    if (
        not text
        or len(text) > _MAX_FEEDBACK_CHARACTERS
        or "\x00" in text
    ):
        raise _invalid("text")
    return text


@dataclass(frozen=True)
class FeedbackPayload:
    repository: str
    pr_number: int
    head_sha: str
    cycle_id: str
    text: str
    created_at: datetime

    def __post_init__(self) -> None:
        key = QueueKey(self.repository, self.pr_number, self.head_sha)
        object.__setattr__(self, "repository", key.repository)
        object.__setattr__(self, "pr_number", key.pr_number)
        object.__setattr__(self, "head_sha", key.head_sha)
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "text", _text(self.text))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True)
class FeedbackBinding:
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
        key = QueueKey(self.repository, self.pr_number, self.head_sha)
        object.__setattr__(self, "repository", key.repository)
        object.__setattr__(self, "pr_number", key.pr_number)
        object.__setattr__(self, "head_sha", key.head_sha)
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "text", _text(self.text))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.attempt_id is not None:
            object.__setattr__(
                self, "attempt_id", _uuid4(self.attempt_id, "attempt_id")
            )


@runtime_checkable
class FeedbackPort(Protocol):
    """Restricted feedback storage used by `QueueService`."""

    def put(self, payload: FeedbackPayload) -> FeedbackBinding: ...

    def bind_for_claim(self, claim: ClaimResult) -> FeedbackBinding | None: ...

    def list_for_cycle(
        self, repository: str, pr_number: int, cycle_id: str
    ) -> tuple[FeedbackBinding, ...]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class QueueLaunchFacts:
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
