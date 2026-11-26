"""Fast-forward-only publication with scope, intent, and unknown recovery."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from gitkeeper.policy.patch_scope import (
    FileChange,
    ScopeDecision,
    ScopeRequest,
)
from gitkeeper.queue.errors import ErrorCategory, WorkerFailed
from gitkeeper.queue.models import ClaimResult, OperationalError, RepairReason
from gitkeeper.worker.git import (
    GitPushOutcomeUnknown,
    GitPushRejected,
    GitTransportError,
)
from gitkeeper.worker.models import (
    PublishResult,
    PublishTarget,
    WorktreeIdentity,
    WorktreeRecord,
)
from gitkeeper.worker.publish_target import PublishTargetPort

_COMMIT_MESSAGE = "GitKeeper worker repair"


class PublicationIntentPort(Protocol):
    def record(
        self,
        claim: ClaimResult,
        worktree_id: str,
        starting_sha: str,
        local_commit_sha: str,
        now: datetime,
    ) -> object: ...

    def note_uncertain_publication(
        self,
        claim: ClaimResult,
        worktree_id: str,
        local_commit_sha: str,
    ) -> object: ...


@runtime_checkable
class PublishGitPort(Protocol):
    def inspect_changes(
        self,
        path: Path,
        starting_sha: str,
        *,
        include_uncommitted: bool,
    ) -> tuple[tuple[FileChange, ...], bool]: ...

    def stage(self, path: Path, paths: tuple[str, ...]) -> None: ...

    def commit(self, path: Path, message: str) -> str: ...

    def is_ancestor(self, path: Path, older: str, newer: str) -> bool: ...

    def commit_count(self, path: Path, older: str, newer: str) -> int: ...

    def push(self, path: Path, branch: str, commit_sha: str) -> None: ...

    def remote_head(self, repository: str, branch: str) -> str: ...

    def head(self, path: Path) -> str: ...


@runtime_checkable
class ScopePolicyPort(Protocol):
    def evaluate(self, request: ScopeRequest) -> ScopeDecision: ...


@runtime_checkable
class WorktreeGuardPort(Protocol):
    def assert_active_checkouts_unchanged(self) -> None: ...


class PatchPublisher:
    """Validate, commit, and fast-forward push one exact worker claim."""

    def __init__(
        self,
        git: PublishGitPort,
        targets: PublishTargetPort,
        policy: ScopePolicyPort,
        worktrees: WorktreeGuardPort,
        active_checkouts: Callable[[], tuple[Path, ...]],
        managed_root: Path,
        lifecycle_guard: AbstractContextManager[None],
        intents: PublicationIntentPort,
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(getattr(git, "inspect_changes", None)):
            raise TypeError("git must provide inspect_changes().")
        if not callable(getattr(targets, "get_publish_target", None)):
            raise TypeError("targets must provide get_publish_target().")
        if not callable(getattr(policy, "evaluate", None)):
            raise TypeError("policy must provide evaluate().")
        if not callable(getattr(worktrees, "assert_active_checkouts_unchanged", None)):
            raise TypeError("worktrees must provide assert_active_checkouts_unchanged().")
        if not callable(active_checkouts):
            raise TypeError("active_checkouts must be callable.")
        if not isinstance(managed_root, Path) or not managed_root.is_absolute():
            raise ValueError("managed_root must be an absolute path.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._git = git
        self._targets = targets
        self._policy = policy
        self._worktrees = worktrees
        self._active_checkouts = active_checkouts
        self._managed_root = managed_root
        self._lifecycle_guard = lifecycle_guard
        self._intents = intents
        self._clock = clock

    def _error(self) -> OperationalError:
        return OperationalError(
            ErrorCategory.worker_failed,
            str(WorkerFailed()),
            self._clock(),
        )

    def _scope(
        self,
        worktree: WorktreeRecord,
        changes: tuple[FileChange, ...],
        unstaged: bool,
        remote,
        reason: RepairReason,
        starting_sha: str,
    ) -> ScopeDecision:
        return self._policy.evaluate(
            ScopeRequest(
                starting_sha=starting_sha,
                head_sha=starting_sha,
                worktree_path=worktree.path,
                managed_root=self._managed_root,
                active_checkouts=self._active_checkouts(),
                changes=changes,
                unstaged=unstaged,
                requires_test_change=(
                    reason is RepairReason.deterministic_check_failure
                ),
                remote=remote,
            )
        )

    @staticmethod
    def _publishable(target: PublishTarget, starting_sha: str) -> bool:
        return (
            target.remote.is_open
            and not target.remote.is_draft
            and target.remote.head_sha == starting_sha
            and target.head_repository.casefold()
            == target.repository.casefold()
            and not target.is_default_branch
        )

    def validate_and_publish(
        self,
        claim: ClaimResult,
        worktree: WorktreeRecord,
        target: PublishTarget,
        reason: RepairReason,
        cancellation: threading.Event,
        *,
        is_current: Callable[[], bool],
    ) -> PublishResult:
        if not isinstance(claim, ClaimResult):
            raise TypeError("claim must be a ClaimResult.")
        if not isinstance(worktree, WorktreeRecord):
            raise TypeError("worktree must be a WorktreeRecord.")
        if not isinstance(target, PublishTarget):
            raise TypeError("target must be a PublishTarget.")
        if not isinstance(reason, RepairReason):
            raise TypeError("reason must be a queue RepairReason.")
        if not isinstance(cancellation, threading.Event):
            raise TypeError("cancellation must be a threading.Event.")
        if not callable(is_current):
            raise TypeError("is_current must be callable.")

        key = claim.lease.key
        expected_identity = WorktreeIdentity(
            repository=key.repository,
            pr_number=key.pr_number,
            head_sha=key.head_sha,
            cycle_id=claim.attempt.cycle_id,
        )
        if worktree.identity != expected_identity:
            raise ValueError("The worktree identity does not match the claim.")
        if (
            target.repository.casefold() != key.repository.casefold()
            or target.pr_number != key.pr_number
        ):
            raise ValueError("The publish target does not match the claim.")
        starting_sha = key.head_sha
        if not is_current():
            return PublishResult("blocked", None, ("stale",), self._error())
        if cancellation.is_set():
            return PublishResult("blocked", None, ("cancelled",), self._error())

        changes, unstaged = self._git.inspect_changes(
            worktree.path, starting_sha, include_uncommitted=True
        )
        decision = self._scope(
            worktree, changes, unstaged, target.remote, reason, starting_sha
        )
        if not getattr(decision, "allowed", False):
            return PublishResult(
                "blocked", None, tuple(getattr(decision, "reason_codes", ())), None
            )

        try:
            with self._lifecycle_guard:
                fresh = self._targets.get_publish_target(
                    key.repository, key.pr_number
                )
                if not self._publishable(fresh, starting_sha):
                    return PublishResult(
                        "blocked", None, ("remote_stale",), self._error()
                    )
                if not is_current():
                    return PublishResult(
                        "blocked", None, ("stale",), self._error()
                    )

                paths = tuple(
                    getattr(change, "path") for change in changes
                )
                self._git.stage(worktree.path, paths)
                staged_changes, staged_unstaged = self._git.inspect_changes(
                    worktree.path, starting_sha, include_uncommitted=True
                )
                if staged_unstaged:
                    return PublishResult(
                        "blocked", None, ("unstaged",), self._error()
                    )
                staged_decision = self._scope(
                    worktree,
                    staged_changes,
                    staged_unstaged,
                    fresh.remote,
                    reason,
                    starting_sha,
                )
                if not getattr(staged_decision, "allowed", False):
                    return PublishResult(
                        "blocked",
                        None,
                        tuple(getattr(staged_decision, "reason_codes", ())),
                        None,
                    )

                self._worktrees.assert_active_checkouts_unchanged()
                commit_sha = self._git.commit(
                    worktree.path, _COMMIT_MESSAGE
                )
                committed_changes, _ = self._git.inspect_changes(
                    worktree.path, starting_sha, include_uncommitted=False
                )
                committed_decision = self._scope(
                    worktree,
                    committed_changes,
                    False,
                    fresh.remote,
                    reason,
                    starting_sha,
                )
                if not getattr(committed_decision, "allowed", False):
                    return PublishResult(
                        "blocked",
                        commit_sha,
                        tuple(getattr(committed_decision, "reason_codes", ())),
                        None,
                    )
                if self._git.head(worktree.path) != commit_sha:
                    return PublishResult(
                        "blocked", commit_sha, ("head_mismatch",), self._error()
                    )
                if (
                    not self._git.is_ancestor(
                        worktree.path, starting_sha, commit_sha
                    )
                    or self._git.commit_count(
                        worktree.path, starting_sha, commit_sha
                    )
                    != 1
                ):
                    return PublishResult(
                        "blocked", commit_sha, ("history",), self._error()
                    )

                self._intents.record(
                    claim,
                    worktree.worktree_id,
                    starting_sha,
                    commit_sha,
                    self._clock(),
                )
                final = self._targets.get_publish_target(
                    key.repository, key.pr_number
                )
                if not self._publishable(final, starting_sha):
                    return PublishResult(
                        "blocked", commit_sha, ("remote_stale",), self._error()
                    )
                if not is_current():
                    return PublishResult(
                        "blocked", commit_sha, ("stale",), self._error()
                    )
                if cancellation.is_set():
                    return PublishResult(
                        "blocked", commit_sha, ("cancelled",), self._error()
                    )

                try:
                    self._git.push(worktree.path, final.branch, commit_sha)
                except GitPushRejected:
                    return PublishResult(
                        "blocked", commit_sha, ("push_rejected",), self._error()
                    )
                except GitPushOutcomeUnknown:
                    self._intents.note_uncertain_publication(
                        claim, worktree.worktree_id, commit_sha
                    )
                    return PublishResult(
                        "unknown", commit_sha, ("push_unknown",), self._error()
                    )

                try:
                    remote = self._git.remote_head(
                        key.repository, final.branch
                    )
                except GitTransportError:
                    self._intents.note_uncertain_publication(
                        claim, worktree.worktree_id, commit_sha
                    )
                    return PublishResult(
                        "unknown", commit_sha, ("remote_unknown",), self._error()
                    )
                if remote != commit_sha:
                    self._intents.note_uncertain_publication(
                        claim, worktree.worktree_id, commit_sha
                    )
                    return PublishResult(
                        "unknown", commit_sha, ("remote_mismatch",), self._error()
                    )
                return PublishResult("published", commit_sha, (), None)
        except Exception:  # noqa: BLE001 - every failure must block publication
            return PublishResult(
                "blocked", None, ("publication_failed",), self._error()
            )

    def reconcile_unknown(
        self,
        worktree: WorktreeRecord,
        target: PublishTarget,
        reason: RepairReason,
        expected_starting_sha: str,
        expected_local_commit_sha: str,
        cancellation: threading.Event,
        *,
        is_current: Callable[[], bool],
    ) -> PublishResult:
        if not isinstance(worktree, WorktreeRecord):
            raise TypeError("worktree must be a WorktreeRecord.")
        if not isinstance(target, PublishTarget):
            raise TypeError("target must be a PublishTarget.")
        if not callable(is_current):
            raise TypeError("is_current must be callable.")
        if not is_current():
            return PublishResult(
                "blocked", None, ("validation_failed",), self._error()
            )
        if cancellation.is_set():
            return PublishResult(
                "blocked", None, ("cancelled",), self._error()
            )
        if worktree.identity.head_sha != expected_starting_sha:
            return PublishResult(
                "blocked", None, ("validation_failed",), self._error()
            )
        try:
            with self._lifecycle_guard:
                if self._git.head(worktree.path) != expected_local_commit_sha:
                    return PublishResult(
                        "blocked", None, ("validation_failed",), self._error()
                    )
                changes, _ = self._git.inspect_changes(
                    worktree.path,
                    expected_starting_sha,
                    include_uncommitted=False,
                )
                decision = self._scope(
                    worktree,
                    changes,
                    False,
                    target.remote,
                    reason,
                    expected_starting_sha,
                )
                if not getattr(decision, "allowed", False):
                    return PublishResult(
                        "blocked", None, ("validation_failed",), self._error()
                    )
                fresh = self._targets.get_publish_target(
                    worktree.identity.repository, worktree.identity.pr_number
                )
                if not self._publishable(fresh, expected_starting_sha):
                    return PublishResult(
                        "blocked", None, ("validation_failed",), self._error()
                    )
                remote = self._git.remote_head(
                    worktree.identity.repository, fresh.branch
                )
                if remote == expected_local_commit_sha:
                    return PublishResult(
                        "published", expected_local_commit_sha, ("remote_equal",), None
                    )
                return PublishResult(
                    "blocked", None, ("remote_diverged",), self._error()
                )
        except Exception:  # noqa: BLE001 - unknown stays unknown
            return PublishResult(
                "unknown", expected_local_commit_sha, (), self._error()
            )
