"""Fenced worker claim lifecycle and single-flight background coordination."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from gitkeeper.policy.models import LocalModelPolicy
from gitkeeper.queue.errors import ErrorCategory, WorkerUnavailable
from gitkeeper.queue.models import (
    AttemptOutcome,
    ClaimResult,
    InvocationKind,
    OperationalError,
    RepairReason,
)
from gitkeeper.worker.models import (
    FeedbackRecord,
    PublishResult,
    PublishTarget,
    WorkerJob,
    WorkerResult,
    WorktreeRecord,
)
from gitkeeper.worker.opencode import ApprovalPortBinding, ExecutionGate, WorkerExecutor
from gitkeeper.worker.publish import PatchPublisher, PublishGitPort
from gitkeeper.worker.worktree import WorktreeManager

_AUTOMATIC_OWNER = "gitkeeper-automatic"


class QueueServicePort(Protocol):
    def claim(
        self,
        pr_number: int,
        head_sha: str,
        owner: str,
        retry_authorization: str | None = None,
    ) -> ClaimResult | None: ...

    def start(self, claim: ClaimResult, model_id: str) -> object: ...

    def complete(
        self,
        claim: ClaimResult,
        outcome: AttemptOutcome,
        model_id: str | None = None,
        error: OperationalError | None = None,
    ) -> object: ...

    def block_reserved(
        self, claim: ClaimResult, error: OperationalError
    ) -> bool: ...

    def list_queue(self) -> list[object]: ...

    def is_paused(self) -> bool: ...

    def finish_reconciliation(
        self,
        claim: ClaimResult,
        resolution: str,
        error: OperationalError | None = None,
    ) -> object: ...

    def pending_publication_intents(self) -> tuple[object, ...]: ...

    def note_uncertain_publication_from_intent(
        self, intent, worktree_id: str, local_commit_sha: str
    ) -> object: ...

    def escalate_unassociated_publication_intent(self, intent) -> object: ...

    def get_retry_authorization(self, pr_number: int) -> object | None: ...

    def recover_active_lease(self) -> object | None: ...


class WorkerCoordinator:
    """Process at most one exact claim and publish only a completed repair."""

    def __init__(
        self,
        service: QueueServicePort,
        worktrees: WorktreeManager,
        git: PublishGitPort,
        executor: WorkerExecutor,
        publisher: PatchPublisher,
        targets,
        model_policy_path: Path,
        launch_facts,
        feedback,
        gate: ExecutionGate,
        approval: ApprovalPortBinding,
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(getattr(service, "claim", None)):
            raise TypeError("service must provide claim().")
        if not callable(getattr(worktrees, "create", None)) or not callable(
            getattr(worktrees, "get_by_id", None)
        ):
            raise TypeError("worktrees must provide create() and get_by_id().")
        if not callable(getattr(git, "head", None)):
            raise TypeError("git must provide head().")
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must provide execute().")
        if not callable(getattr(publisher, "validate_and_publish", None)):
            raise TypeError("publisher must provide validate_and_publish().")
        if not callable(getattr(targets, "get_publish_target", None)):
            raise TypeError("targets must provide get_publish_target().")
        if not callable(launch_facts):
            raise TypeError("launch_facts must be callable.")
        if not isinstance(gate, ExecutionGate):
            raise TypeError("gate must be an ExecutionGate.")
        if not isinstance(approval, ApprovalPortBinding):
            raise TypeError("approval must be an ApprovalPortBinding.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._service = service
        self._worktrees = worktrees
        self._git = git
        self._executor = executor
        self._publisher = publisher
        self._targets = targets
        self._model_policy_path = Path(model_policy_path)
        self._launch_facts = launch_facts
        self._feedback = feedback
        self._gate = gate
        self._approval = approval
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active_cancellation: threading.Event | None = None

    def _error(self) -> OperationalError:
        return OperationalError(
            ErrorCategory.worker_unavailable,
            str(WorkerUnavailable()),
            self._clock(),
        )

    def _is_current(self, claim: ClaimResult) -> bool:
        checker = getattr(self._service, "is_current", None)
        if not callable(checker):
            return True
        try:
            return bool(checker(claim))
        except Exception:
            return False

    @staticmethod
    def _feedback_record(binding) -> FeedbackRecord | None:
        if binding is None:
            return None
        return FeedbackRecord(
            feedback_id=binding.feedback_id,
            repository=binding.repository,
            pr_number=binding.pr_number,
            head_sha=binding.head_sha,
            cycle_id=binding.cycle_id,
            text=binding.text,
            created_at=binding.created_at,
            attempt_id=binding.attempt_id,
        )

    def run_claim(self, claim: ClaimResult) -> WorkerResult:
        if not isinstance(claim, ClaimResult):
            raise TypeError("claim must be a ClaimResult.")
        if claim.attempt.kind is InvocationKind.reconcile_unknown:
            return self._run_reconciliation(claim)
        return self._run_launch(claim)

    def _run_launch(self, claim: ClaimResult) -> WorkerResult:
        key = claim.lease.key
        try:
            self._gate.require_enabled()
            if self._approval.get() is None:
                raise RuntimeError("The approval port is unavailable.")
        except Exception:
            error = self._error()
            self._service.block_reserved(claim, error)
            return WorkerResult(
                outcome=AttemptOutcome.cancelled,
                model_id=None,
                error=error,
                process_started=False,
            )

        try:
            launch = self._launch_facts(claim)
            model_policy = LocalModelPolicy.load(self._model_policy_path)
            model_id = model_policy.ladder.active
            worktree = self._worktrees.create(
                key.repository,
                key.pr_number,
                key.head_sha,
                claim.attempt.cycle_id,
            )
            feedback = self._feedback_record(
                self._feedback.bind_for_claim(claim) if self._feedback else None
            )
            job = WorkerJob(
                claim=claim,
                worktree=worktree,
                model_id=model_id,
                repair_reason=launch.repair_reason,
                requires_test_change=(
                    launch.repair_reason
                    is RepairReason.deterministic_check_failure
                ),
                feedback=feedback,
                checks=launch.checks,
            )
            target: PublishTarget = self._targets.get_publish_target(
                key.repository, key.pr_number
            )
        except Exception:
            error = self._error()
            self._service.block_reserved(claim, error)
            return WorkerResult(
                outcome=AttemptOutcome.cancelled,
                model_id=None,
                error=error,
                process_started=False,
            )

        cancellation = threading.Event()
        with self._lock:
            if self._stop.is_set():
                cancellation.set()
            self._active_cancellation = cancellation
        started = False

        def before_start() -> None:
            nonlocal started
            self._service.start(claim, model_id)
            started = True

        try:
            result = self._executor.execute(
                job,
                cancellation,
                approval_port=self._approval.get(),
                facts=lambda: self._launch_facts(claim).pull_request,
                before_start=before_start,
            )
        except Exception:
            error = self._error()
            if started:
                self._service.complete(
                    claim,
                    AttemptOutcome.failed,
                    model_id=model_id,
                    error=error,
                )
                return WorkerResult(
                    outcome=AttemptOutcome.failed,
                    model_id=model_id,
                    error=None,
                    process_started=True,
                )
            self._service.block_reserved(claim, error)
            return WorkerResult(
                outcome=AttemptOutcome.cancelled,
                model_id=model_id,
                error=error,
                process_started=False,
            )

        if (
            result.outcome is AttemptOutcome.cancelled
            and result.error is not None
            and result.error.category is ErrorCategory.worker_unavailable
        ):
            self._service.block_reserved(claim, result.error)
            return result

        if result.outcome is AttemptOutcome.completed:
            publication: PublishResult = self._publisher.validate_and_publish(
                claim,
                worktree,
                target,
                launch.repair_reason,
                cancellation,
                is_current=lambda: self._is_current(claim),
            )
            if publication.status == "published":
                self._service.complete(
                    claim, AttemptOutcome.completed, model_id=model_id
                )
            else:
                self._service.complete(
                    claim,
                    AttemptOutcome.failed,
                    model_id=model_id,
                    error=publication.error,
                )
            return result

        self._service.complete(
            claim,
            result.outcome,
            model_id=result.model_id,
            error=result.error if result.outcome is AttemptOutcome.cancelled else None,
        )
        return result

    def _run_reconciliation(self, claim: ClaimResult) -> WorkerResult:
        association = claim.uncertain_publication
        if association is None:
            error = self._error()
            self._service.finish_reconciliation(
                claim, "validation_failed", error
            )
            return WorkerResult(
                AttemptOutcome.failed, None, error, process_started=False
            )
        worktree = self._worktrees.get_by_id(association.worktree_id)
        if worktree is None:
            error = self._error()
            self._service.finish_reconciliation(
                claim, "validation_failed", error
            )
            return WorkerResult(
                AttemptOutcome.failed, None, error, process_started=False
            )
        target = self._targets.get_publish_target(
            worktree.identity.repository, worktree.identity.pr_number
        )
        result = self._publisher.reconcile_unknown(
            worktree,
            target,
            RepairReason.deterministic_check_failure,
            association.starting_sha,
            association.local_commit_sha,
            threading.Event(),
            is_current=lambda: True,
        )
        if "remote_equal" in result.reason_codes:
            self._service.finish_reconciliation(claim, "remote_equal", None)
            return WorkerResult(
                AttemptOutcome.cancelled, None, None, process_started=False
            )
        if result.status == "blocked":
            resolution = (
                "remote_diverged"
                if "remote_diverged" in result.reason_codes
                else "validation_failed"
            )
            self._service.finish_reconciliation(
                claim, resolution, result.error
            )
            return WorkerResult(
                AttemptOutcome.failed, None, result.error, process_started=False
            )
        return WorkerResult(
            AttemptOutcome.cancelled, None, None, process_started=False
        )

    def try_claim(self) -> ClaimResult | None:
        if self._gate.execution_disabled or self._approval.get() is None:
            return None
        try:
            if self._service.is_paused():
                return None
        except Exception:
            return None
        for item in self._service.list_queue():
            state = getattr(item, "state", None)
            state = getattr(state, "value", state)
            if state != "queued":
                continue
            authorization = None
            auth_getter = getattr(
                self._service, "get_retry_authorization", None
            )
            if callable(auth_getter):
                try:
                    record = auth_getter(getattr(item, "pr_number"))
                except Exception:
                    record = None
                authorization = getattr(record, "authorization", record)
                if not isinstance(authorization, str):
                    authorization = None
            claim = self._service.claim(
                getattr(item, "pr_number"),
                getattr(item, "current_head_sha"),
                _AUTOMATIC_OWNER,
                authorization,
            )
            if claim is not None:
                return claim
        return None

    def recover_publication_intents(self) -> None:
        pending = getattr(self._service, "pending_publication_intents", None)
        if not callable(pending):
            return
        for intent in pending():
            worktree = self._worktrees.get_by_id(intent.worktree_id)
            escalate = getattr(
                self._service, "escalate_unassociated_publication_intent", None
            )
            if worktree is None:
                if callable(escalate):
                    escalate(intent)
                continue
            identity = worktree.identity
            if (
                identity.repository != intent.key.repository
                or identity.pr_number != intent.key.pr_number
                or identity.cycle_id != intent.cycle_id
                or identity.head_sha != intent.starting_sha
            ):
                if callable(escalate):
                    escalate(intent)
                continue
            try:
                head = self._git.head(worktree.path)
            except Exception:
                if callable(escalate):
                    escalate(intent)
                continue
            if head != intent.local_commit_sha:
                if callable(escalate):
                    escalate(intent)
                continue
            note = getattr(
                self._service, "note_uncertain_publication_from_intent", None
            )
            if callable(note):
                note(intent, worktree.worktree_id, intent.local_commit_sha)

    def recover(self) -> None:
        self.recover_publication_intents()
        recover = getattr(self._service, "recover_active_lease", None)
        if callable(recover):
            recover()

    def run_until(self, stop: threading.Event) -> None:
        if not isinstance(stop, threading.Event):
            raise TypeError("stop must be a threading.Event.")
        try:
            self.recover()
        except Exception:  # noqa: BLE001 - recovery retries on the next pass
            pass
        while not stop.is_set():
            try:
                claim = self.try_claim()
            except Exception:  # noqa: BLE001 - transport/persistence failure
                claim = None
            if claim is None:
                try:
                    self.recover()
                except Exception:  # noqa: BLE001 - retry on the next idle pass
                    pass
                stop.wait(1.0)
                continue
            try:
                self.run_claim(claim)
            except Exception:  # noqa: BLE001 - the reserved claim stays recoverable
                pass

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("The coordinator is already running.")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self.run_until,
                args=(self._stop,),
                name="gitkeeper-coordinator",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout_seconds: float = 10.0) -> bool:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._stop.set()
        with self._lock:
            thread = self._thread
            active = self._active_cancellation
        if active is not None:
            active.set()
        if thread is None:
            return True
        thread.join(timeout_seconds)
        return not thread.is_alive()
