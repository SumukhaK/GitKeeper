"""Frozen queue control-plane contracts and safe UI projections.

These values describe durable queue state. They do not perform storage, GitHub,
worker, UI-framework, or workflow operations.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from gitkeeper.host_mode.readiness import (
    MAX_AUTOMATIC_INVOCATIONS,
    OWNER_LOGIN,
    Blocker,
    ReadinessBlocker,
    is_repository_name,
)
from gitkeeper.queue.errors import ErrorCategory, QueueError

_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_CHECK_RUN_PATTERN = re.compile(r"[0-9]+")
_MODEL_ID_PATTERN = re.compile(r"opencode/\S+")
_AUTOMATIC_OWNER = "gitkeeper-automatic"
_QUEUE_BLOCKERS = frozenset(
    {
        Blocker.REPOSITORY_NOT_ALLOWLISTED,
        Blocker.REPOSITORY_PRIVACY_UNVERIFIED,
        Blocker.FORK_HEAD,
        Blocker.AUTHOR_NOT_OWNER,
        Blocker.PR_CLOSED,
        Blocker.PR_DRAFT,
        Blocker.CONFIG_NOT_MERGED,
        Blocker.WORKER_DISABLED,
        Blocker.NO_REPAIR_REASON,
        Blocker.STALE_CHECK_SHA,
        Blocker.ATTEMPT_BUDGET_EXHAUSTED,
    }
)
_LATCH_CATEGORIES = frozenset(
    {
        ErrorCategory.worker_failed,
        ErrorCategory.worker_unavailable,
        ErrorCategory.model_unavailable,
    }
)
_RESOLUTIONS = frozenset(
    {"remote_equal", "remote_diverged", "validation_failed", "superseded"}
)


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field)
    return value.strip()


def _optional_string(value: object, field: str) -> str | None:
    return None if value is None else _string(value, field)


def _repository(value: object, field: str = "repository") -> str:
    if not isinstance(value, str) or not is_repository_name(value):
        raise _invalid(field)
    return value


def _optional_repository(value: object, field: str) -> str | None:
    return None if value is None else _repository(value, field)


def _sha(value: object, field: str = "head_sha") -> str:
    if not isinstance(value, str) or _SHA_PATTERN.fullmatch(value) is None:
        raise _invalid(field)
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


def new_uuid4() -> str:
    """Generate an unguessable local authority identifier."""

    return str(uuid4())


def _integer(value: object, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _invalid(field)
    return value


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise _invalid(field)
    return value


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _invalid(field)
    return value.astimezone(timezone.utc)


def _optional_utc(value: object, field: str) -> datetime | None:
    return None if value is None else _utc(value, field)


def _tuple(value: object, field: str) -> tuple:
    if not isinstance(value, tuple):
        raise _invalid(field)
    return value


def _enum(value: object, enum_type: type[Enum], field: str) -> Enum:
    if not isinstance(value, enum_type):
        raise _invalid(field)
    return value


def _model_id(value: object) -> str:
    if not isinstance(value, str) or _MODEL_ID_PATTERN.fullmatch(value) is None:
        raise _invalid("model_id")
    return value


def _owner(value: object) -> str:
    if value != OWNER_LOGIN or not isinstance(value, str):
        raise _invalid("owner")
    return value


def _same_key(left: QueueKey, right: QueueKey) -> bool:
    return left == right


def _validate_state_reason(
    state: WorkerState, reason: RepairReason | None, eligible: bool
) -> None:
    if state is WorkerState.ready:
        if reason is not None or eligible:
            raise _invalid("ready projection")
        return
    if state is WorkerState.blocked:
        if eligible:
            raise _invalid("blocked projection")
        return
    if reason is None:
        raise _invalid("reason")
    if state is WorkerState.running and not eligible:
        raise _invalid("running projection")
    if eligible and state is WorkerState.queued and not reason.is_automatic:
        raise _invalid("queued eligibility")


class CheckConclusion(str, Enum):
    success = "success"
    failure = "failure"
    neutral = "neutral"
    cancelled = "cancelled"
    skipped = "skipped"
    timed_out = "timed_out"
    action_required = "action_required"
    stale = "stale"
    startup_failure = "startup_failure"


class CheckOutcome(str, Enum):
    passed = "passed"
    deterministic_failure = "deterministic_failure"
    blocked = "blocked"
    unknown = "unknown"


class RepairReason(str, Enum):
    deterministic_check_failure = "deterministic_check_failure"
    claude_feedback = "claude_feedback"
    manual_retry = "manual_retry"

    @property
    def is_automatic(self) -> bool:
        return self in (
            RepairReason.deterministic_check_failure,
            RepairReason.claude_feedback,
        )


class WorkerState(str, Enum):
    ready = "ready"
    queued = "queued"
    running = "running"
    blocked = "blocked"

    @property
    def desired_label(self) -> WorkerLabel | None:
        if self is WorkerState.ready:
            return None
        if self is WorkerState.queued:
            return WorkerLabel.queued
        if self is WorkerState.running:
            return WorkerLabel.running
        return WorkerLabel.blocked


class WorkerLabel(str, Enum):
    queued = "gitkeeper/worker-queued"
    running = "gitkeeper/worker-running"
    blocked = "gitkeeper/worker-blocked"


class RetryKind(str, Enum):
    manual_retry = "manual_retry"
    reconcile_unknown = "reconcile_unknown"


class InvocationKind(str, Enum):
    automatic = "automatic"
    manual_retry = "manual_retry"
    reconcile_unknown = "reconcile_unknown"


class AttemptOutcome(str, Enum):
    reserved = "reserved"
    running = "running"
    completed = "completed"
    model_unavailable = "model_unavailable"
    failed = "failed"
    cancelled = "cancelled"
    recovered_unknown = "recovered_unknown"


class RemoteHealth(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    offline = "offline"
    stale = "stale"


class RemoteOperation(str, Enum):
    list_pull_requests = "list_pull_requests"
    get_pull_request = "get_pull_request"
    get_checks = "get_checks"
    get_config = "get_config"
    get_labels = "get_labels"
    set_label = "set_label"


@dataclass(frozen=True)
class PilotConfig:
    worker_enabled: bool
    polling_preferences: Mapping[str, str]
    motion_preference: str | None
    deterministic_check_ids: tuple[str, ...]
    default_branch: str
    default_branch_sha: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_enabled", _boolean(self.worker_enabled, "worker_enabled"))
        if not isinstance(self.polling_preferences, Mapping):
            raise _invalid("polling_preferences")
        polling = dict(self.polling_preferences)
        if polling != {"mode": "adaptive"}:
            raise _invalid("polling_preferences")
        object.__setattr__(self, "polling_preferences", MappingProxyType(polling))
        if self.motion_preference not in (None, "reduced", "disabled"):
            raise _invalid("motion_preference")
        check_ids = _tuple(self.deterministic_check_ids, "deterministic_check_ids")
        if not check_ids:
            raise _invalid("deterministic_check_ids")
        normalized = tuple(_string(value, "check_id") for value in check_ids)
        if len(set(normalized)) != len(normalized):
            raise _invalid("deterministic_check_ids")
        object.__setattr__(self, "deterministic_check_ids", normalized)
        object.__setattr__(self, "default_branch", _string(self.default_branch, "default_branch"))
        object.__setattr__(self, "default_branch_sha", _sha(self.default_branch_sha, "default_branch_sha"))


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    check_run_id: str
    head_sha: str
    conclusion: CheckConclusion

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _string(self.check_id, "check_id"))
        run_id = _string(self.check_run_id, "check_run_id")
        if _CHECK_RUN_PATTERN.fullmatch(run_id) is None or int(run_id) <= 0:
            raise _invalid("check_run_id")
        object.__setattr__(self, "check_run_id", run_id)
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        object.__setattr__(self, "conclusion", _enum(self.conclusion, CheckConclusion, "conclusion"))


@dataclass(frozen=True)
class CheckState:
    head_sha: str
    required_check_ids: tuple[str, ...]
    results: tuple[CheckResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        required = _tuple(self.required_check_ids, "required_check_ids")
        if not required:
            raise _invalid("required_check_ids")
        normalized = tuple(_string(value, "required_check_id") for value in required)
        if len(set(normalized)) != len(normalized):
            raise _invalid("required_check_ids")
        object.__setattr__(self, "required_check_ids", normalized)
        results = _tuple(self.results, "results")
        if any(not isinstance(result, CheckResult) for result in results):
            raise _invalid("results")
        if any(result.check_id not in normalized for result in results):
            raise _invalid("results")
        run_ids = [result.check_run_id for result in results]
        if len(set(run_ids)) != len(run_ids):
            raise _invalid("results")
        object.__setattr__(self, "results", results)

    @property
    def outcome(self) -> CheckOutcome:
        for result in self.results:
            if result.head_sha != self.head_sha or result.conclusion not in (
                CheckConclusion.success,
                CheckConclusion.failure,
            ):
                return CheckOutcome.blocked
        counts = {check_id: 0 for check_id in self.required_check_ids}
        for result in self.results:
            counts[result.check_id] += 1
        if any(count != 1 for count in counts.values()):
            return CheckOutcome.unknown
        if any(result.conclusion is CheckConclusion.failure for result in self.results):
            return CheckOutcome.deterministic_failure
        return CheckOutcome.passed


@dataclass(frozen=True)
class PullRequestSnapshot:
    repository: str
    pr_number: int
    author: str | None
    head_repository: str | None
    head_sha: str
    is_open: bool
    is_draft: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number", 1))
        object.__setattr__(self, "author", _optional_string(self.author, "author"))
        object.__setattr__(self, "head_repository", _optional_repository(self.head_repository, "head_repository"))
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        object.__setattr__(self, "is_open", _boolean(self.is_open, "is_open"))
        object.__setattr__(self, "is_draft", _boolean(self.is_draft, "is_draft"))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))

    @property
    def same_repository(self) -> bool:
        return self.head_repository == self.repository

    @property
    def author_is_owner(self) -> bool:
        return self.author == OWNER_LOGIN


@dataclass(frozen=True)
class OperationalError:
    category: ErrorCategory
    message: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        category = _enum(self.category, ErrorCategory, "category")
        if not isinstance(self.message, str) or self.message != str(QueueError(category)):
            raise _invalid("message")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))


@dataclass(frozen=True)
class QueueKey:
    repository: str
    pr_number: int
    head_sha: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number", 1))
        object.__setattr__(self, "head_sha", _sha(self.head_sha))


@dataclass(frozen=True)
class RepairCycle:
    cycle_id: str
    cycle_number: int
    initial_key: QueueKey
    opened_at: datetime
    closed_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "cycle_number", _integer(self.cycle_number, "cycle_number", 1))
        if not isinstance(self.initial_key, QueueKey):
            raise _invalid("initial_key")
        opened_at = _utc(self.opened_at, "opened_at")
        closed_at = _optional_utc(self.closed_at, "closed_at")
        if closed_at is not None and closed_at < opened_at:
            raise _invalid("closed_at")
        object.__setattr__(self, "opened_at", opened_at)
        object.__setattr__(self, "closed_at", closed_at)


@dataclass(frozen=True)
class RepairRequest:
    request_id: str
    key: QueueKey
    cycle_id: str
    reason: RepairReason
    owner: str | None
    requested_at: datetime
    expires_at: datetime | None
    consumed_at: datetime | None
    retry_authorization: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _uuid4(self.request_id, "request_id"))
        if not isinstance(self.key, QueueKey):
            raise _invalid("key")
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        reason = _enum(self.reason, RepairReason, "reason")
        requested_at = _utc(self.requested_at, "requested_at")
        expires_at = _optional_utc(self.expires_at, "expires_at")
        consumed_at = _optional_utc(self.consumed_at, "consumed_at")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "consumed_at", consumed_at)
        if reason is RepairReason.manual_retry:
            object.__setattr__(self, "owner", _owner(self.owner))
            if self.retry_authorization is None:
                raise _invalid("retry_authorization")
            object.__setattr__(self, "retry_authorization", _uuid4(self.retry_authorization, "retry_authorization"))
            if expires_at is None or expires_at <= requested_at:
                raise _invalid("expires_at")
            if consumed_at is not None and not requested_at <= consumed_at < expires_at:
                raise _invalid("consumed_at")
        else:
            if self.owner is not None or expires_at is not None or consumed_at is not None or self.retry_authorization is not None:
                raise _invalid("automatic request")


@dataclass(frozen=True)
class RetryAuthorization:
    authorization: str
    request_id: str
    key: QueueKey
    cycle_id: str
    owner: str
    kind: RetryKind = field(default=RetryKind.manual_retry, kw_only=True)
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization", _uuid4(self.authorization, "authorization"))
        object.__setattr__(self, "request_id", _uuid4(self.request_id, "request_id"))
        if not isinstance(self.key, QueueKey):
            raise _invalid("key")
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "owner", _owner(self.owner))
        object.__setattr__(self, "kind", _enum(self.kind, RetryKind, "kind"))
        created_at = _utc(self.created_at, "created_at")
        expires_at = _utc(self.expires_at, "expires_at")
        consumed_at = _optional_utc(self.consumed_at, "consumed_at")
        if expires_at <= created_at:
            raise _invalid("expires_at")
        if consumed_at is not None and not created_at <= consumed_at < expires_at:
            raise _invalid("consumed_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "consumed_at", consumed_at)

    @classmethod
    def from_request(
        cls,
        request: RepairRequest,
        *,
        kind: RetryKind = RetryKind.manual_retry,
        created_at: datetime,
        expires_at: datetime | None = None,
        consumed_at: datetime | None = None,
    ) -> RetryAuthorization:
        if not isinstance(request, RepairRequest) or request.reason is not RepairReason.manual_retry:
            raise _invalid("request")
        retry_kind = _enum(kind, RetryKind, "kind")
        expiry = request.expires_at if expires_at is None else _utc(expires_at, "expires_at")
        if expiry != request.expires_at or request.retry_authorization is None:
            raise _invalid("request")
        return cls(
            authorization=request.retry_authorization,
            request_id=request.request_id,
            key=request.key,
            cycle_id=request.cycle_id,
            owner=request.owner,
            kind=retry_kind,
            created_at=created_at,
            expires_at=expiry,
            consumed_at=consumed_at,
        )


@dataclass(frozen=True)
class Lease:
    lease_id: str
    generation: int
    attempt_id: str
    owner: str
    key: QueueKey
    cycle_id: str
    retry_authorization: str | None
    claimed_at: datetime
    expires_at: datetime
    cancelled_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lease_id", _uuid4(self.lease_id, "lease_id"))
        object.__setattr__(self, "generation", _integer(self.generation, "generation", 1))
        object.__setattr__(self, "attempt_id", _uuid4(self.attempt_id, "attempt_id"))
        if not isinstance(self.key, QueueKey):
            raise _invalid("key")
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        if self.retry_authorization is None:
            if self.owner != _AUTOMATIC_OWNER or not isinstance(self.owner, str):
                raise _invalid("owner")
        else:
            object.__setattr__(self, "retry_authorization", _uuid4(self.retry_authorization, "retry_authorization"))
            object.__setattr__(self, "owner", _owner(self.owner))
        claimed_at = _utc(self.claimed_at, "claimed_at")
        expires_at = _utc(self.expires_at, "expires_at")
        cancelled_at = _optional_utc(self.cancelled_at, "cancelled_at")
        if expires_at <= claimed_at:
            raise _invalid("expires_at")
        if cancelled_at is not None and cancelled_at < claimed_at:
            raise _invalid("cancelled_at")
        object.__setattr__(self, "claimed_at", claimed_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "cancelled_at", cancelled_at)


@runtime_checkable
class CancellationPort(Protocol):
    def cancel(self, lease: Lease) -> bool: ...


@dataclass(frozen=True)
class PublicationIntent:
    intent_id: str
    attempt_id: str
    key: QueueKey
    cycle_id: str
    worktree_id: str
    starting_sha: str
    local_commit_sha: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _uuid4(self.intent_id, "intent_id"))
        object.__setattr__(self, "attempt_id", _uuid4(self.attempt_id, "attempt_id"))
        if not isinstance(self.key, QueueKey):
            raise _invalid("key")
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "worktree_id", _uuid4(self.worktree_id, "worktree_id"))
        object.__setattr__(self, "starting_sha", _sha(self.starting_sha, "starting_sha"))
        object.__setattr__(self, "local_commit_sha", _sha(self.local_commit_sha, "local_commit_sha"))
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, "recorded_at"))


@dataclass(frozen=True)
class UncertainPublication:
    publication_id: str
    attempt_id: str
    key: QueueKey
    cycle_id: str
    worktree_id: str
    local_commit_sha: str
    recorded_at: datetime
    resolved_at: datetime | None
    resolution: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "publication_id", _uuid4(self.publication_id, "publication_id"))
        object.__setattr__(self, "attempt_id", _uuid4(self.attempt_id, "attempt_id"))
        if not isinstance(self.key, QueueKey):
            raise _invalid("key")
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "worktree_id", _uuid4(self.worktree_id, "worktree_id"))
        object.__setattr__(self, "local_commit_sha", _sha(self.local_commit_sha, "local_commit_sha"))
        recorded_at = _utc(self.recorded_at, "recorded_at")
        resolved_at = _optional_utc(self.resolved_at, "resolved_at")
        if (resolved_at is None) != (self.resolution is None):
            raise _invalid("resolution")
        if self.resolution is not None and (
            not isinstance(self.resolution, str) or self.resolution not in _RESOLUTIONS
        ):
            raise _invalid("resolution")
        if resolved_at is not None and resolved_at < recorded_at:
            raise _invalid("resolved_at")
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "resolved_at", resolved_at)

    @property
    def unresolved(self) -> bool:
        return self.resolved_at is None and self.resolution is None


@dataclass(frozen=True)
class ManualRetryLatch:
    latch_id: str
    key: QueueKey
    cycle_id: str
    attempt_id: str
    category: ErrorCategory
    created_at: datetime
    cleared_at: datetime | None
    cleared_by_authorization: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "latch_id", _uuid4(self.latch_id, "latch_id"))
        if not isinstance(self.key, QueueKey):
            raise _invalid("key")
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "attempt_id", _uuid4(self.attempt_id, "attempt_id"))
        category = _enum(self.category, ErrorCategory, "category")
        if category not in _LATCH_CATEGORIES:
            raise _invalid("category")
        created_at = _utc(self.created_at, "created_at")
        cleared_at = _optional_utc(self.cleared_at, "cleared_at")
        if (cleared_at is None) != (self.cleared_by_authorization is None):
            raise _invalid("cleared_by_authorization")
        if cleared_at is not None:
            object.__setattr__(self, "cleared_by_authorization", _uuid4(self.cleared_by_authorization, "cleared_by_authorization"))
            if cleared_at < created_at:
                raise _invalid("cleared_at")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "cleared_at", cleared_at)


@dataclass(frozen=True)
class InvocationAttempt:
    attempt_id: str
    lease_id: str
    key: QueueKey
    cycle_id: str
    kind: InvocationKind
    outcome: AttemptOutcome
    reserved_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    model_id: str | None
    error: OperationalError | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _uuid4(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "lease_id", _uuid4(self.lease_id, "lease_id"))
        if not isinstance(self.key, QueueKey):
            raise _invalid("key")
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        kind = _enum(self.kind, InvocationKind, "kind")
        outcome = _enum(self.outcome, AttemptOutcome, "outcome")
        reserved_at = _utc(self.reserved_at, "reserved_at")
        started_at = _optional_utc(self.started_at, "started_at")
        finished_at = _optional_utc(self.finished_at, "finished_at")
        model_id = None if self.model_id is None else _model_id(self.model_id)
        if self.error is not None and not isinstance(self.error, OperationalError):
            raise _invalid("error")
        if kind is InvocationKind.reconcile_unknown:
            if outcome not in (
                AttemptOutcome.reserved,
                AttemptOutcome.cancelled,
                AttemptOutcome.failed,
            ):
                raise _invalid("reconciliation outcome")
            if started_at is not None or model_id is not None:
                raise _invalid("reconciliation launch state")
        if started_at is not None and started_at < reserved_at:
            raise _invalid("started_at")
        if started_at is not None and model_id is None:
            raise _invalid("model_id")
        if finished_at is not None:
            baseline = started_at if started_at is not None else reserved_at
            if finished_at < baseline:
                raise _invalid("finished_at")
        if outcome is AttemptOutcome.reserved:
            if started_at is not None or finished_at is not None or model_id is not None or self.error is not None:
                raise _invalid("reserved attempt")
        elif outcome is AttemptOutcome.running:
            if started_at is None or model_id is None or finished_at is not None or self.error is not None:
                raise _invalid("running attempt")
        else:
            if finished_at is None:
                raise _invalid("finished_at")
            if outcome is AttemptOutcome.completed:
                if started_at is None or model_id is None or self.error is not None:
                    raise _invalid("completed attempt")
            elif outcome is AttemptOutcome.model_unavailable:
                if model_id is None or self.error is None or self.error.category is not ErrorCategory.model_unavailable:
                    raise _invalid("model_unavailable attempt")
            elif outcome in (AttemptOutcome.failed, AttemptOutcome.recovered_unknown):
                if self.error is None or self.error.category is not ErrorCategory.worker_failed:
                    raise _invalid("failed attempt")
                if outcome is AttemptOutcome.recovered_unknown and started_at is None:
                    raise _invalid("recovered_unknown attempt")
            elif outcome is AttemptOutcome.cancelled:
                if self.error is not None:
                    raise _invalid("cancelled attempt")
                if started_at is None and model_id is not None:
                    raise _invalid("cancelled attempt")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reserved_at", reserved_at)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(self, "model_id", model_id)

    def transition_to(
        self,
        outcome: AttemptOutcome,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        model_id: str | None = None,
        error: OperationalError | None = None,
    ) -> InvocationAttempt:
        target = _enum(outcome, AttemptOutcome, "outcome")
        allowed = {
            AttemptOutcome.reserved: frozenset(
                {AttemptOutcome.running, AttemptOutcome.cancelled, AttemptOutcome.failed, AttemptOutcome.model_unavailable}
            ),
            AttemptOutcome.running: frozenset(
                {AttemptOutcome.completed, AttemptOutcome.failed, AttemptOutcome.cancelled, AttemptOutcome.model_unavailable}
            ),
        }
        if target not in allowed.get(self.outcome, frozenset()):
            raise _invalid("transition")
        if self.outcome is AttemptOutcome.reserved and target is not AttemptOutcome.running and started_at is not None:
            raise _invalid("started_at")
        if self.outcome is AttemptOutcome.running:
            if started_at is not None and _utc(started_at, "started_at") != self.started_at:
                raise _invalid("started_at")
            if model_id is not None and _model_id(model_id) != self.model_id:
                raise _invalid("model_id")
        return InvocationAttempt(
            attempt_id=self.attempt_id,
            lease_id=self.lease_id,
            key=self.key,
            cycle_id=self.cycle_id,
            kind=self.kind,
            outcome=target,
            reserved_at=self.reserved_at,
            started_at=self.started_at if started_at is None else started_at,
            finished_at=finished_at,
            model_id=self.model_id if model_id is None else model_id,
            error=self.error if error is None else error,
        )

    def recover(self, *, finished_at: datetime) -> InvocationAttempt:
        if self.outcome is not AttemptOutcome.failed or self.started_at is None:
            raise _invalid("recovery")
        return InvocationAttempt(
            attempt_id=self.attempt_id,
            lease_id=self.lease_id,
            key=self.key,
            cycle_id=self.cycle_id,
            kind=self.kind,
            outcome=AttemptOutcome.recovered_unknown,
            reserved_at=self.reserved_at,
            started_at=self.started_at,
            finished_at=finished_at,
            model_id=self.model_id,
            error=self.error,
        )


@dataclass(frozen=True)
class ClaimResult:
    lease: Lease
    attempt: InvocationAttempt
    authorization: RetryAuthorization | None
    uncertain_publication: UncertainPublication | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.lease, Lease) or not isinstance(self.attempt, InvocationAttempt):
            raise _invalid("claim")
        if self.authorization is not None and not isinstance(self.authorization, RetryAuthorization):
            raise _invalid("authorization")
        if self.uncertain_publication is not None and not isinstance(self.uncertain_publication, UncertainPublication):
            raise _invalid("uncertain_publication")
        if self.attempt.outcome is not AttemptOutcome.reserved:
            raise _invalid("claim attempt")
        if self.attempt.attempt_id != self.lease.attempt_id or self.attempt.lease_id != self.lease.lease_id:
            raise _invalid("claim identity")
        if not _same_key(self.attempt.key, self.lease.key) or self.attempt.cycle_id != self.lease.cycle_id:
            raise _invalid("claim scope")
        if self.attempt.reserved_at != self.lease.claimed_at:
            raise _invalid("claim time")
        if self.attempt.kind is InvocationKind.automatic:
            if self.authorization is not None or self.lease.retry_authorization is not None or self.lease.owner != _AUTOMATIC_OWNER or self.uncertain_publication is not None:
                raise _invalid("automatic claim")
            return
        auth = self.authorization
        if auth is None or auth.consumed_at is None:
            raise _invalid("claim authorization")
        expected_kind = RetryKind.manual_retry if self.attempt.kind is InvocationKind.manual_retry else RetryKind.reconcile_unknown
        if self.attempt.kind not in (InvocationKind.manual_retry, InvocationKind.reconcile_unknown):
            raise _invalid("claim kind")
        if auth.kind is not expected_kind or auth.consumed_at != self.lease.claimed_at:
            raise _invalid("claim authorization")
        if self.lease.retry_authorization != auth.authorization or self.lease.owner != OWNER_LOGIN:
            raise _invalid("claim authorization")
        if not _same_key(auth.key, self.lease.key) or auth.cycle_id != self.lease.cycle_id:
            raise _invalid("claim scope")
        publication = self.uncertain_publication
        if self.attempt.kind is InvocationKind.manual_retry:
            if publication is not None:
                raise _invalid("manual claim")
            return
        if publication is None or not publication.unresolved:
            raise _invalid("reconciliation association")
        if publication.key.repository != self.lease.key.repository or publication.key.pr_number != self.lease.key.pr_number or publication.cycle_id != self.lease.cycle_id:
            raise _invalid("reconciliation scope")


@dataclass(frozen=True)
class QueueItem:
    repository: str
    pr_number: int
    author: str | None
    head_repository: str | None
    current_head_sha: str
    observed_at: datetime
    cycle_id: str
    eligible: bool
    checks: CheckState
    state: WorkerState
    reason: RepairReason | None
    remote_labels: tuple[WorkerLabel, ...]
    automatic_attempt_count: int
    manual_retry_count: int
    last_error: OperationalError | None
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number", 1))
        object.__setattr__(self, "author", _optional_string(self.author, "author"))
        object.__setattr__(self, "head_repository", _optional_repository(self.head_repository, "head_repository"))
        object.__setattr__(self, "current_head_sha", _sha(self.current_head_sha, "current_head_sha"))
        observed_at = _utc(self.observed_at, "observed_at")
        updated_at = _utc(self.updated_at, "updated_at")
        if updated_at < observed_at:
            raise _invalid("updated_at")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        eligible = _boolean(self.eligible, "eligible")
        object.__setattr__(self, "eligible", eligible)
        if not isinstance(self.checks, CheckState) or self.checks.head_sha != self.current_head_sha:
            raise _invalid("checks")
        state = _enum(self.state, WorkerState, "state")
        reason = None if self.reason is None else _enum(self.reason, RepairReason, "reason")
        _validate_state_reason(state, reason, eligible)
        labels = _tuple(self.remote_labels, "remote_labels")
        if any(not isinstance(label, WorkerLabel) for label in labels):
            raise _invalid("remote_labels")
        if len(set(labels)) != len(labels):
            raise _invalid("remote_labels")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "remote_labels", labels)
        automatic_count = _integer(self.automatic_attempt_count, "automatic_attempt_count")
        if eligible and reason is not None and reason.is_automatic and automatic_count >= MAX_AUTOMATIC_INVOCATIONS:
            raise _invalid("automatic_attempt_count")
        object.__setattr__(self, "automatic_attempt_count", automatic_count)
        object.__setattr__(self, "manual_retry_count", _integer(self.manual_retry_count, "manual_retry_count"))
        if self.last_error is not None and not isinstance(self.last_error, OperationalError):
            raise _invalid("last_error")

    @property
    def check_state(self) -> CheckState:
        return self.checks

    @property
    def last_updated(self) -> datetime:
        return self.updated_at


@dataclass(frozen=True)
class QueueEvent:
    event_id: str
    repository: str
    pr_number: int
    head_sha: str
    cycle_id: str
    from_state: WorkerState
    to_state: WorkerState
    occurred_at: datetime
    attempt_id: str | None
    error: OperationalError | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _uuid4(self.event_id, "event_id"))
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number", 1))
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        from_state = _enum(self.from_state, WorkerState, "from_state")
        to_state = _enum(self.to_state, WorkerState, "to_state")
        object.__setattr__(self, "from_state", from_state)
        object.__setattr__(self, "to_state", to_state)
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))
        if self.attempt_id is not None:
            object.__setattr__(self, "attempt_id", _uuid4(self.attempt_id, "attempt_id"))
        if self.error is not None and not isinstance(self.error, OperationalError):
            raise _invalid("error")
        if from_state is to_state and self.error is None:
            raise _invalid("observation")
        if from_state is WorkerState.running and to_state in (WorkerState.ready, WorkerState.blocked) and self.attempt_id is None:
            raise _invalid("attempt_id")

    def matches_attempt(self, attempt: InvocationAttempt) -> bool:
        return (
            isinstance(attempt, InvocationAttempt)
            and self.attempt_id == attempt.attempt_id
            and self.repository == attempt.key.repository
            and self.pr_number == attempt.key.pr_number
            and self.head_sha == attempt.key.head_sha
            and self.cycle_id == attempt.cycle_id
        )

    @classmethod
    def for_attempt(
        cls,
        attempt: InvocationAttempt,
        *,
        from_state: WorkerState,
        to_state: WorkerState,
        event_id: str,
        occurred_at: datetime,
    ) -> QueueEvent:
        if not isinstance(attempt, InvocationAttempt) or attempt.finished_at is None:
            raise _invalid("attempt")
        timestamp = _utc(occurred_at, "occurred_at")
        if timestamp < attempt.finished_at:
            raise _invalid("occurred_at")
        return cls(
            event_id=event_id,
            repository=attempt.key.repository,
            pr_number=attempt.key.pr_number,
            head_sha=attempt.key.head_sha,
            cycle_id=attempt.cycle_id,
            from_state=from_state,
            to_state=to_state,
            occurred_at=timestamp,
            attempt_id=attempt.attempt_id,
            error=attempt.error,
        )


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    state: WorkerState
    reason: RepairReason | None
    blockers: tuple[ReadinessBlocker, ...]

    def __post_init__(self) -> None:
        eligible = _boolean(self.eligible, "eligible")
        state = _enum(self.state, WorkerState, "state")
        reason = None if self.reason is None else _enum(self.reason, RepairReason, "reason")
        blockers = _tuple(self.blockers, "blockers")
        if any(not isinstance(blocker, ReadinessBlocker) or blocker.code not in _QUEUE_BLOCKERS for blocker in blockers):
            raise _invalid("blockers")
        _validate_state_reason(state, reason, eligible)
        if blockers and eligible:
            raise _invalid("blockers")
        object.__setattr__(self, "eligible", eligible)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "blockers", blockers)


@dataclass(frozen=True)
class ProjectionFence:
    repository: str
    pr_number: int
    observed_at: datetime
    cycle_id: str
    head_sha: str
    item_revision: int
    projection_revision: int
    active_lease_id: str | None
    active_attempt_id: str | None
    latch_id: str | None
    latch_cleared_at: datetime | None
    paused: bool
    state: WorkerState
    reason: RepairReason | None
    eligible: bool
    desired_labels: tuple[WorkerLabel, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number", 1))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "cycle_id", _uuid4(self.cycle_id, "cycle_id"))
        object.__setattr__(self, "head_sha", _sha(self.head_sha))
        object.__setattr__(self, "item_revision", _integer(self.item_revision, "item_revision", 1))
        object.__setattr__(self, "projection_revision", _integer(self.projection_revision, "projection_revision", 1))
        if (self.active_lease_id is None) != (self.active_attempt_id is None):
            raise _invalid("active claim")
        if self.active_lease_id is not None:
            object.__setattr__(self, "active_lease_id", _uuid4(self.active_lease_id, "active_lease_id"))
            object.__setattr__(self, "active_attempt_id", _uuid4(self.active_attempt_id, "active_attempt_id"))
        if self.latch_id is not None:
            object.__setattr__(self, "latch_id", _uuid4(self.latch_id, "latch_id"))
        cleared_at = _optional_utc(self.latch_cleared_at, "latch_cleared_at")
        if cleared_at is not None and self.latch_id is None:
            raise _invalid("latch_cleared_at")
        object.__setattr__(self, "latch_cleared_at", cleared_at)
        object.__setattr__(self, "paused", _boolean(self.paused, "paused"))
        state = _enum(self.state, WorkerState, "state")
        reason = None if self.reason is None else _enum(self.reason, RepairReason, "reason")
        eligible = _boolean(self.eligible, "eligible")
        _validate_state_reason(state, reason, eligible)
        labels = _tuple(self.desired_labels, "desired_labels")
        if any(not isinstance(label, WorkerLabel) for label in labels):
            raise _invalid("desired_labels")
        expected_labels = () if state.desired_label is None else (state.desired_label,)
        if labels != expected_labels:
            raise _invalid("desired_labels")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "eligible", eligible)
        object.__setattr__(self, "desired_labels", labels)


@dataclass(frozen=True)
class RemoteFailure:
    repository: str
    pr_number: int | None
    operation: RemoteOperation
    error: OperationalError

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        if self.pr_number is not None:
            object.__setattr__(self, "pr_number", _integer(self.pr_number, "pr_number", 1))
        object.__setattr__(self, "operation", _enum(self.operation, RemoteOperation, "operation"))
        if not isinstance(self.error, OperationalError):
            raise _invalid("error")


def _report_health(failures: tuple[RemoteFailure, ...]) -> RemoteHealth:
    if not failures:
        return RemoteHealth.healthy
    categories = {failure.error.category for failure in failures}
    if categories & {ErrorCategory.remote_stale, ErrorCategory.stale_head}:
        return RemoteHealth.stale
    if categories & {ErrorCategory.github_unavailable, ErrorCategory.github_authorization}:
        return RemoteHealth.offline
    return RemoteHealth.degraded


@dataclass(frozen=True)
class RemoteRepositoryReport:
    repository: str
    health: RemoteHealth
    failures: tuple[RemoteFailure, ...]
    items: tuple[QueueItem, ...]
    closed_pr_numbers: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _repository(self.repository))
        health = _enum(self.health, RemoteHealth, "health")
        failures = _tuple(self.failures, "failures")
        if any(not isinstance(failure, RemoteFailure) or failure.repository != self.repository for failure in failures):
            raise _invalid("failures")
        operation_order = {operation: index for index, operation in enumerate(RemoteOperation)}
        ordered_failures = tuple(
            sorted(
                failures,
                key=lambda failure: (
                    operation_order[failure.operation],
                    failure.pr_number is not None,
                    failure.pr_number or 0,
                    failure.error.category.value,
                ),
            )
        )
        if health is not _report_health(ordered_failures):
            raise _invalid("health")
        items = _tuple(self.items, "items")
        if any(not isinstance(item, QueueItem) or item.repository != self.repository for item in items):
            raise _invalid("items")
        ordered_items = tuple(sorted(items, key=lambda item: item.pr_number))
        numbers = [item.pr_number for item in ordered_items]
        if len(set(numbers)) != len(numbers):
            raise _invalid("items")
        closed = _tuple(self.closed_pr_numbers, "closed_pr_numbers")
        normalized_closed = tuple(
            sorted({_integer(value, "closed_pr_numbers", 1) for value in closed})
        )
        object.__setattr__(self, "health", health)
        object.__setattr__(self, "failures", ordered_failures)
        object.__setattr__(self, "items", ordered_items)
        object.__setattr__(self, "closed_pr_numbers", normalized_closed)


@dataclass(frozen=True)
class ReconcileResult:
    reports: tuple[RemoteRepositoryReport, ...]

    def __post_init__(self) -> None:
        reports = _tuple(self.reports, "reports")
        if any(not isinstance(report, RemoteRepositoryReport) for report in reports):
            raise _invalid("reports")
        ordered = tuple(sorted(reports, key=lambda report: report.repository))
        if len({report.repository for report in ordered}) != len(ordered):
            raise _invalid("reports")
        object.__setattr__(self, "reports", ordered)

    @property
    def items(self) -> tuple[QueueItem, ...]:
        return tuple(item for report in self.reports for item in report.items)

    @property
    def remote_healthy(self) -> bool:
        return all(report.health is RemoteHealth.healthy for report in self.reports)


@dataclass(frozen=True)
class QueueViewAdapter:
    item: QueueItem

    def __post_init__(self) -> None:
        if not isinstance(self.item, QueueItem):
            raise _invalid("item")

    @property
    def repository(self) -> str:
        return self.item.repository

    @property
    def pr_number(self) -> int:
        return self.item.pr_number

    @property
    def current_head_sha(self) -> str:
        return self.item.current_head_sha

    @property
    def current_sha(self) -> str:
        return self.item.current_head_sha

    @property
    def eligible(self) -> bool:
        return self.item.eligible

    @property
    def checks(self) -> CheckState:
        return self.item.checks

    @property
    def check_state(self) -> CheckOutcome:
        return self.item.checks.outcome

    @property
    def state(self) -> WorkerState:
        return self.item.state

    @property
    def reason(self) -> RepairReason | None:
        return self.item.reason

    @property
    def remote_labels(self) -> tuple[WorkerLabel, ...]:
        return self.item.remote_labels

    @property
    def automatic_attempt_count(self) -> int:
        return self.item.automatic_attempt_count

    @property
    def manual_retry_count(self) -> int:
        return self.item.manual_retry_count

    @property
    def last_error(self) -> ErrorViewAdapter | None:
        return None if self.item.last_error is None else ErrorViewAdapter(self.item.last_error)

    @property
    def last_updated(self) -> datetime:
        return self.item.updated_at

    @classmethod
    def from_item(cls, item: QueueItem) -> QueueViewAdapter:
        return cls(item=item)

    def as_mapping(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "pr_number": self.pr_number,
            "current_head_sha": self.current_head_sha,
            "eligible": self.eligible,
            "check_state": self.check_state.value,
            "state": self.state.value,
            "reason": None if self.reason is None else self.reason.value,
            "automatic_attempt_count": self.automatic_attempt_count,
            "manual_retry_count": self.manual_retry_count,
            "last_error_category": None if self.last_error is None else self.last_error.category.value,
            "remote_labels": [label.value for label in self.remote_labels],
        }


@dataclass(frozen=True)
class ErrorViewAdapter:
    error: OperationalError

    def __post_init__(self) -> None:
        if not isinstance(self.error, OperationalError):
            raise _invalid("error")

    @classmethod
    def from_exception(
        cls, error: QueueError, *, occurred_at: datetime
    ) -> ErrorViewAdapter:
        if not isinstance(error, QueueError):
            raise _invalid("error")
        return cls(
            OperationalError(
                category=error.category,
                message=str(error),
                occurred_at=occurred_at,
            )
        )

    @property
    def category(self) -> ErrorCategory:
        return self.error.category

    @property
    def message(self) -> str:
        return self.error.message

    @property
    def occurred_at(self) -> datetime:
        return self.error.occurred_at

    def as_mapping(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "message": self.message,
            "occurred_at": self.occurred_at.isoformat(),
        }


def _telemetry_enum(
    value: object,
    enum_type: type[Enum],
    field: str,
    *,
    allow_unknown: bool = False,
) -> str:
    if isinstance(value, enum_type):
        return str(value.value)
    if isinstance(value, str):
        if allow_unknown and value == "unknown":
            return value
        try:
            return str(enum_type(value).value)
        except ValueError:
            pass
    raise _invalid(field)


@dataclass(frozen=True, init=False)
class TelemetryViewAdapter(Mapping[str, object]):
    timestamp: datetime
    duration_ms: int | None
    repository: str
    pr_number: int
    head_sha: str
    from_state: str
    to_state: str
    attempt_count: int
    outcome: str
    model_id: str | None
    error_category: str | None

    _KEYS = (
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

    def __init__(self, event: Mapping[str, object]) -> None:
        if not isinstance(event, Mapping):
            raise _invalid("event")
        duration_ms = event.get("duration_ms")
        model_id = event.get("model_id")
        error_category = event.get("error_category")
        values = {
            "timestamp": _utc(event.get("timestamp"), "timestamp"),
            "duration_ms": None if duration_ms is None else _integer(duration_ms, "duration_ms"),
            "repository": _repository(event.get("repository")),
            "pr_number": _integer(event.get("pr_number"), "pr_number", 1),
            "head_sha": _sha(event.get("head_sha")),
            "from_state": _telemetry_enum(event.get("from_state"), WorkerState, "from_state"),
            "to_state": _telemetry_enum(event.get("to_state"), WorkerState, "to_state"),
            "attempt_count": _integer(event.get("invocation_count", 0), "attempt_count"),
            "outcome": _telemetry_enum(
                event.get("outcome", "unknown"),
                AttemptOutcome,
                "outcome",
                allow_unknown=True,
            ),
            "model_id": None if model_id is None else _model_id(model_id),
            "error_category": None if error_category is None else _telemetry_enum(
                error_category, ErrorCategory, "error_category"
            ),
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def as_mapping(self) -> dict[str, object]:
        return {key: getattr(self, key) for key in self._KEYS}

    def __getitem__(self, key: str) -> object:
        if key not in self._KEYS:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._KEYS)

    def __len__(self) -> int:
        return len(self._KEYS)
