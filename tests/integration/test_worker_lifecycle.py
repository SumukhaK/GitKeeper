import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from gitkeeper.host_mode.command_broker import ExecutionResult
from gitkeeper.host_mode.readiness import (
    PullRequestFacts,
    WorkerConfiguration,
    WorkerLaunchBlocked,
    RepairReason as ReadinessRepairReason,
)
from gitkeeper.queue.models import (
    AttemptOutcome,
    CheckState,
    ClaimResult,
    InvocationAttempt,
    InvocationKind,
    Lease,
    QueueKey,
    RepairReason,
)
from gitkeeper.worker.coordinator import WorkerCoordinator
from gitkeeper.worker.models import (
    PublishResult,
    PublishTarget,
    WorkerLaunchFacts,
    WorkerResult,
    WorktreeIdentity,
    WorktreeRecord,
)
from gitkeeper.worker.opencode import ApprovalPortBinding, ExecutionGate
from gitkeeper.worker.publish_target import PublishTargetError

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_LOCAL = "b" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"
_MODEL = "opencode/deepseek-v4.1-flash-free"


class FakePort:
    def show(self, digest, lines):
        from gitkeeper.host_mode.command_broker import RenderedRequest

        return RenderedRequest(digest, tuple(lines))

    def wait(self, timeout_seconds):
        return "approve"


class FakeService:
    def __init__(self, claim=None, items=None, paused=False) -> None:
        self.claim_result = claim
        self.items = list(items or [])
        self.paused = paused
        self.starts = []
        self.completions = []
        self.blocked = []
        self.finished = []
        self.notes = []
        self.escalated = []
        self.recovered = 0
        self.pending = []

    def claim(self, pr_number, head_sha, owner, retry_authorization=None):
        return self.claim_result

    def start(self, claim, model_id):
        self.starts.append((claim, model_id))

    def complete(self, claim, outcome, model_id=None, error=None):
        self.completions.append((claim, outcome, model_id, error))

    def block_reserved(self, claim, error):
        self.blocked.append((claim, error))
        return True

    def list_queue(self):
        return self.items

    def is_paused(self):
        return self.paused

    def pending_publication_intents(self):
        return tuple(self.pending)

    def note_uncertain_publication_from_intent(self, intent, worktree_id, sha):
        self.notes.append((intent, worktree_id, sha))

    def escalate_unassociated_publication_intent(self, intent):
        self.escalated.append(intent)

    def recover_active_lease(self):
        self.recovered += 1
        return None

    def finish_reconciliation(self, claim, resolution, error=None):
        self.finished.append((claim, resolution, error))


class FakeExecutor:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def execute(self, job, cancellation, *, approval_port, facts, before_start):
        self.calls.append(job)
        before_start()
        if self.error is not None:
            raise self.error
        return self.result


class FakePublisher:
    def __init__(self, result=None) -> None:
        self.result = result or PublishResult("published", _LOCAL, (), None)
        self.calls = []

    def validate_and_publish(
        self, claim, worktree, target, reason, cancellation, *, is_current
    ):
        self.calls.append((claim, worktree, target, reason))
        return self.result


class FakeTargets:
    def __init__(self, target) -> None:
        self.target = target

    def get_publish_target(self, repository, pr_number):
        return self.target


class FakeWorktrees:
    def __init__(self, record) -> None:
        self.record = record
        self.created = []
        self.get_by_id_calls = []

    def create(self, repository, pr_number, head_sha, cycle_id):
        self.created.append((repository, pr_number, head_sha, cycle_id))
        return self.record

    def get_by_id(self, worktree_id):
        self.get_by_id_calls.append(worktree_id)
        return self.record if worktree_id == self.record.worktree_id else None


class FakeFeedback:
    def bind_for_claim(self, claim):
        return None


def _claim(kind: InvocationKind = InvocationKind.automatic) -> ClaimResult:
    cycle = str(uuid4())
    attempt_id = str(uuid4())
    lease_id = str(uuid4())
    key = QueueKey(_REPOSITORY, 42, _SHA)
    return ClaimResult(
        lease=Lease(
            lease_id=lease_id,
            generation=1,
            attempt_id=attempt_id,
            owner="gitkeeper-automatic",
            key=key,
            cycle_id=cycle,
            retry_authorization=None,
            claimed_at=_AT,
            expires_at=_AT + timedelta(minutes=2),
            cancelled_at=None,
        ),
        attempt=InvocationAttempt(
            attempt_id=attempt_id,
            lease_id=lease_id,
            key=key,
            cycle_id=cycle,
            kind=kind,
            outcome=AttemptOutcome.reserved,
            reserved_at=_AT,
            started_at=None,
            finished_at=None,
            model_id=None,
            error=None,
        ),
        authorization=None,
    )


def _worktree(claim: ClaimResult, tmp_path) -> WorktreeRecord:
    path = tmp_path / "worktree"
    path.mkdir(exist_ok=True)
    return WorktreeRecord(
        worktree_id=str(uuid4()),
        identity=WorktreeIdentity(
            repository=claim.lease.key.repository,
            pr_number=claim.lease.key.pr_number,
            head_sha=claim.lease.key.head_sha,
            cycle_id=claim.attempt.cycle_id,
        ),
        path=path,
        branch=None,
        created_at=_AT,
        preserved_reason=None,
    )


def _target() -> PublishTarget:
    from gitkeeper.policy.patch_scope import RemotePrState

    return PublishTarget(
        repository=_REPOSITORY,
        pr_number=42,
        branch="repair/42",
        head_repository=_REPOSITORY,
        is_default_branch=False,
        remote=RemotePrState(
            number=42,
            repository=_REPOSITORY,
            author="SumukhaK",
            head_sha=_SHA,
            is_open=True,
            is_draft=False,
            head_repository=_REPOSITORY,
        ),
    )


def _launch(claim: ClaimResult) -> WorkerLaunchFacts:
    return WorkerLaunchFacts(
        pull_request=PullRequestFacts(
            repository=_REPOSITORY,
            number=42,
            head_repository=_REPOSITORY,
            author="SumukhaK",
            head_sha=_SHA,
            check_head_sha=_SHA,
            is_open=True,
            is_draft=False,
            repair_reason=ReadinessRepairReason.DETERMINISTIC_CHECK_FAILURE,
            automatic_invocations_used=0,
        ),
        configuration=WorkerConfiguration(worker_enabled=True, merged=True),
        checks=CheckState(head_sha=_SHA, required_check_ids=("lint",), results=()),
        repair_reason=RepairReason.deterministic_check_failure,
    )


def _completed(job) -> WorkerResult:
    return WorkerResult(
        AttemptOutcome.completed,
        job.model_id,
        None,
        process_started=True,
        execution=ExecutionResult(0, "ok"),
        execution_digest="digest",
    )


def _coordinator(
    tmp_path,
    service,
    executor,
    publisher=None,
    *,
    record=None,
    gate=None,
    approval=None,
):
    policy_path = tmp_path / "models.json"
    policy_path.write_text(
        json.dumps({"version": 1, "active": _MODEL, "ladder": [_MODEL]}),
        encoding="utf-8",
    )
    if approval is None:
        binding = ApprovalPortBinding()
        binding.bind(FakePort())
    else:
        binding = approval
    claim = service.claim_result or _claim()
    worktree = record or _worktree(claim, tmp_path)
    return WorkerCoordinator(
        service=service,
        worktrees=FakeWorktrees(worktree),
        executor=executor,
        publisher=publisher or FakePublisher(),
        targets=FakeTargets(_target()),
        model_policy_path=policy_path,
        launch_facts=lambda claim: _launch(claim),
        feedback=FakeFeedback(),
        gate=gate or ExecutionGate(execution_disabled=False),
        approval=binding,
        clock=lambda: _AT,
    ), worktree


def test_run_claim_completes_and_publishes(tmp_path) -> None:
    claim = _claim()
    service = FakeService(claim)
    executor = FakeExecutor()
    publisher = FakePublisher()
    coordinator, worktree = _coordinator(
        tmp_path, service, executor, publisher, record=_worktree(claim, tmp_path)
    )
    executor.result = _completed(SimpleNamespace(model_id=_MODEL))

    result = coordinator.run_claim(claim)

    assert result.outcome is AttemptOutcome.completed
    assert len(executor.calls) == 1
    assert service.starts and service.starts[0][1] == _MODEL
    assert len(publisher.calls) == 1
    assert service.completions[0][1] is AttemptOutcome.completed
    assert service.blocked == []


def test_disabled_gate_blocks_reserved_without_execution(tmp_path) -> None:
    claim = _claim()
    service = FakeService(claim)
    executor = FakeExecutor()
    coordinator, _ = _coordinator(
        tmp_path,
        service,
        executor,
        gate=ExecutionGate(execution_disabled=True),
    )

    result = coordinator.run_claim(claim)

    assert result.outcome is AttemptOutcome.cancelled
    assert service.blocked
    assert service.completions == []
    assert executor.calls == []


def test_missing_approval_blocks_reserved(tmp_path) -> None:
    claim = _claim()
    service = FakeService(claim)
    executor = FakeExecutor()
    approval = ApprovalPortBinding()
    coordinator, _ = _coordinator(
        tmp_path, service, executor, approval=approval
    )

    result = coordinator.run_claim(claim)

    assert result.outcome is AttemptOutcome.cancelled
    assert service.blocked
    assert executor.calls == []


def test_executor_failure_completes_failed_without_publication(tmp_path) -> None:
    claim = _claim()
    service = FakeService(claim)
    executor = FakeExecutor()
    publisher = FakePublisher()
    coordinator, _ = _coordinator(
        tmp_path, service, executor, publisher, record=_worktree(claim, tmp_path)
    )
    executor.result = WorkerResult(
        AttemptOutcome.failed,
        _MODEL,
        None,
        process_started=True,
        execution=ExecutionResult(1, "failed"),
        execution_digest="digest",
    )

    result = coordinator.run_claim(claim)

    assert result.outcome is AttemptOutcome.failed
    assert service.completions[0][1] is AttemptOutcome.failed
    assert publisher.calls == []


def test_executor_failure_after_start_completes_failed(tmp_path) -> None:
    claim = _claim()
    service = FakeService(claim)
    executor = FakeExecutor(error=WorkerLaunchBlocked("blocked"))
    coordinator, _ = _coordinator(
        tmp_path, service, executor, record=_worktree(claim, tmp_path)
    )

    result = coordinator.run_claim(claim)

    assert result.outcome is AttemptOutcome.failed
    assert service.blocked == []
    assert service.completions[0][1] is AttemptOutcome.failed


def test_try_claim_claims_a_queued_item(tmp_path) -> None:
    claim = _claim()
    service = FakeService(
        claim,
        items=[
            SimpleNamespace(
                pr_number=42, current_head_sha=_SHA, state="queued"
            )
        ],
    )
    coordinator, _ = _coordinator(
        tmp_path, service, FakeExecutor()
    )

    assert coordinator.try_claim() is claim


def test_try_claim_skips_paused_disabled_or_unbound(tmp_path) -> None:
    claim = _claim()
    service = FakeService(
        claim,
        items=[
            SimpleNamespace(
                pr_number=42, current_head_sha=_SHA, state="queued"
            )
        ],
        paused=True,
    )
    coordinator, _ = _coordinator(tmp_path, service, FakeExecutor())
    assert coordinator.try_claim() is None

    service.paused = False
    disabled, _ = _coordinator(
        tmp_path,
        service,
        FakeExecutor(),
        gate=ExecutionGate(execution_disabled=True),
    )
    assert disabled.try_claim() is None

    unbound, _ = _coordinator(
        tmp_path, service, FakeExecutor(), approval=ApprovalPortBinding()
    )
    assert unbound.try_claim() is None


def test_recover_associates_exact_intents_and_escalates_missing_ones(
    tmp_path,
) -> None:
    claim = _claim()
    record = _worktree(claim, tmp_path)
    service = FakeService(claim)
    coordinator, _ = _coordinator(
        tmp_path, service, FakeExecutor(), record=record
    )
    exact = SimpleNamespace(
        worktree_id=record.worktree_id, local_commit_sha=_LOCAL
    )
    missing = SimpleNamespace(
        worktree_id=str(uuid4()), local_commit_sha=_LOCAL
    )
    service.pending = [exact, missing]

    coordinator.recover()

    assert service.notes == [
        (exact, record.worktree_id, _LOCAL)
    ]
    assert service.escalated == [missing]
    assert service.recovered == 1


def test_start_stop_is_single_flight_and_interruptible(tmp_path) -> None:
    claim = _claim()
    service = FakeService(claim, items=[])
    coordinator, _ = _coordinator(tmp_path, service, FakeExecutor())

    coordinator.start()
    with pytest.raises(RuntimeError):
        coordinator.start()
    assert coordinator.stop(timeout_seconds=5.0) is True
