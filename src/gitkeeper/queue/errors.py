"""Fixed-category operational failures for the queue control plane."""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    storage_unavailable = "storage_unavailable"
    storage_corrupt = "storage_corrupt"
    schema_mismatch = "schema_mismatch"
    github_unavailable = "github_unavailable"
    github_authorization = "github_authorization"
    github_response = "github_response"
    pull_request_not_found = "pull_request_not_found"
    remote_stale = "remote_stale"
    local_lease_conflict = "local_lease_conflict"
    stale_head = "stale_head"
    retry_invalid = "retry_invalid"
    retry_expired = "retry_expired"
    retry_replayed = "retry_replayed"
    attempt_budget_exhausted = "attempt_budget_exhausted"
    model_unavailable = "model_unavailable"
    worker_unavailable = "worker_unavailable"
    worker_failed = "worker_failed"


_ERROR_MESSAGES = {
    ErrorCategory.storage_unavailable: "Local queue storage is unavailable.",
    ErrorCategory.storage_corrupt: "Local queue storage is corrupt.",
    ErrorCategory.schema_mismatch: "Local queue storage schema is incompatible.",
    ErrorCategory.github_unavailable: "GitHub is unavailable.",
    ErrorCategory.github_authorization: "GitHub authorization failed.",
    ErrorCategory.github_response: "GitHub returned an invalid response.",
    ErrorCategory.pull_request_not_found: "The pull request was not found.",
    ErrorCategory.remote_stale: "Remote queue state is stale.",
    ErrorCategory.local_lease_conflict: "The local queue lease is unavailable.",
    ErrorCategory.stale_head: "The pull request head is stale.",
    ErrorCategory.retry_invalid: "The retry authorization is invalid.",
    ErrorCategory.retry_expired: "The retry authorization has expired.",
    ErrorCategory.retry_replayed: "The retry authorization was already used.",
    ErrorCategory.attempt_budget_exhausted: (
        "The automatic repair attempt budget is exhausted."
    ),
    ErrorCategory.model_unavailable: "The selected model is unavailable.",
    ErrorCategory.worker_unavailable: "The repair worker is unavailable.",
    ErrorCategory.worker_failed: "The repair worker failed.",
}


class QueueError(Exception):
    """A typed exception carrying only a fixed operational category."""

    category: ErrorCategory

    def __init__(self, category: ErrorCategory) -> None:
        if not isinstance(category, ErrorCategory):
            raise ValueError("Queue errors require an ErrorCategory.")
        self.category = category
        super().__init__(_ERROR_MESSAGES[category])


class StorageUnavailable(QueueError):
    category = ErrorCategory.storage_unavailable

    def __init__(self) -> None:
        super().__init__(self.category)


class StorageCorrupt(StorageUnavailable):
    category = ErrorCategory.storage_corrupt

    def __init__(self) -> None:
        QueueError.__init__(self, self.category)


class SchemaMismatch(StorageUnavailable):
    category = ErrorCategory.schema_mismatch

    def __init__(self) -> None:
        QueueError.__init__(self, self.category)


class GitHubUnavailable(QueueError):
    category = ErrorCategory.github_unavailable

    def __init__(self) -> None:
        super().__init__(self.category)


class GitHubAuthorizationError(QueueError):
    category = ErrorCategory.github_authorization

    def __init__(self) -> None:
        super().__init__(self.category)


class GitHubResponseError(QueueError):
    category = ErrorCategory.github_response

    def __init__(self) -> None:
        super().__init__(self.category)


class PullRequestNotFound(GitHubResponseError):
    """Reserved for a 404 returned by the single-pull-request endpoint."""

    category = ErrorCategory.pull_request_not_found

    def __init__(self) -> None:
        QueueError.__init__(self, self.category)


class RemoteStale(QueueError):
    category = ErrorCategory.remote_stale

    def __init__(self) -> None:
        super().__init__(self.category)


class LocalLeaseConflict(QueueError):
    category = ErrorCategory.local_lease_conflict

    def __init__(self) -> None:
        super().__init__(self.category)


class StaleHead(QueueError):
    category = ErrorCategory.stale_head

    def __init__(self) -> None:
        super().__init__(self.category)


class RetryInvalid(QueueError):
    category = ErrorCategory.retry_invalid

    def __init__(self) -> None:
        super().__init__(self.category)


class RetryExpired(QueueError):
    category = ErrorCategory.retry_expired

    def __init__(self) -> None:
        super().__init__(self.category)


class RetryReplayed(QueueError):
    category = ErrorCategory.retry_replayed

    def __init__(self) -> None:
        super().__init__(self.category)


class AttemptBudgetExhausted(QueueError):
    category = ErrorCategory.attempt_budget_exhausted

    def __init__(self) -> None:
        super().__init__(self.category)


class ModelUnavailable(QueueError):
    category = ErrorCategory.model_unavailable

    def __init__(self) -> None:
        super().__init__(self.category)


class WorkerUnavailable(QueueError):
    category = ErrorCategory.worker_unavailable

    def __init__(self) -> None:
        super().__init__(self.category)


class WorkerFailed(QueueError):
    category = ErrorCategory.worker_failed

    def __init__(self) -> None:
        super().__init__(self.category)
