import json
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from gitkeeper.host_mode.command_broker import (
    ApprovalOutcome,
    CommandRequest,
    ExecutionResult,
    RenderedRequest,
)
from gitkeeper.host_mode.opencode_capability import (
    ControlledConfigError,
    OpenCodeCapability,
)
from gitkeeper.host_mode.readiness import (
    HOST_MODE_LABEL,
    HostModePolicy,
    PullRequestFacts,
    RepositoryPolicy,
    WorkerConfiguration,
    WorkerLaunchBlocked,
    RepairReason as ReadinessRepairReason,
)
from gitkeeper.policy.models import ModelLadder
from gitkeeper.queue.errors import ErrorCategory, ModelUnavailable
from gitkeeper.queue.models import (
    AttemptOutcome,
    CheckConclusion,
    CheckResult,
    CheckState,
    ClaimResult,
    InvocationAttempt,
    InvocationKind,
    Lease,
    OperationalError,
    QueueKey,
    RepairReason,
)
from gitkeeper.worker.models import (
    ProcessCompletion,
    WorkerJob,
    WorkerLaunchFacts,
    WorktreeIdentity,
    WorktreeRecord,
)
from gitkeeper.worker.opencode import (
    ApprovalPortBinding,
    BrokerCommandExecutor,
    ExecutionGate,
    OpenCodeExecutor,
)
from gitkeeper.worker.process import ProcessRegistry

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"
_MODEL = "opencode/deepseek-v4.1-flash-free"
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="The bridge token uses a Windows DACL."
)


class FakePort:
    def __init__(self, answer: str | None = "approve") -> None:
        self.answer = answer
        self.shown = []

    def show(self, digest: str, lines: tuple[str, ...]) -> RenderedRequest:
        self.shown.append((digest, tuple(lines)))
        return RenderedRequest(digest, tuple(lines))

    def wait(self, timeout_seconds: float) -> str | None:
        return self.answer


class FakeHandle:
    def __init__(self, completion: ProcessCompletion) -> None:
        self.completion = completion
        self.terminations = 0

    def wait(self, timeout_seconds: float = 1800.0) -> ProcessCompletion:
        return self.completion

    def terminate(self) -> bool:
        self.terminations += 1
        return True


class FakeProcess:
    def __init__(
        self,
        completion: ProcessCompletion | None = None,
        *,
        start_error: Exception | None = None,
    ) -> None:
        self.completion = completion or ProcessCompletion(
            AttemptOutcome.completed, 0, None
        )
        self.start_error = start_error
        self.starts = []

    def start(self, request, environment, *, cancellation, model_ladder):
        self.starts.append((request, dict(environment)))
        if self.start_error is not None:
            raise self.start_error
        return FakeHandle(self.completion)


def _claim() -> ClaimResult:
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
            kind=InvocationKind.automatic,
            outcome=AttemptOutcome.reserved,
            reserved_at=_AT,
            started_at=None,
            finished_at=None,
            model_id=None,
            error=None,
        ),
        authorization=None,
    )


def _job(tmp_path: Path) -> WorkerJob:
    claim = _claim()
    key = claim.lease.key
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    return WorkerJob(
        claim=claim,
        worktree=WorktreeRecord(
            worktree_id=str(uuid4()),
            identity=WorktreeIdentity(
                repository=key.repository,
                pr_number=key.pr_number,
                head_sha=key.head_sha,
                cycle_id=claim.attempt.cycle_id,
            ),
            path=worktree_path,
            branch=None,
            created_at=_AT,
            preserved_reason=None,
        ),
        model_id=_MODEL,
        repair_reason=RepairReason.deterministic_check_failure,
        requires_test_change=True,
        feedback=None,
        checks=CheckState(
            head_sha=key.head_sha,
            required_check_ids=("lint",),
            results=(
                CheckResult(
                    check_id="lint",
                    check_run_id="1",
                    head_sha=key.head_sha,
                    conclusion=CheckConclusion.failure,
                ),
            ),
        ),
    )


def _facts() -> PullRequestFacts:
    return PullRequestFacts(
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
    )


def _executor(
    tmp_path: Path,
    process: FakeProcess,
    port: FakePort,
    *,
    gate: ExecutionGate | None = None,
    capability: OpenCodeCapability | None = None,
):
    gitkeeper = tmp_path / "GitKeeper"
    gitkeeper.mkdir()
    controlled = gitkeeper / "config.json"
    controlled.write_text("{}", encoding="utf-8")
    policy_path = gitkeeper / "models.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active": _MODEL,
                "ladder": [_MODEL],
            }
        ),
        encoding="utf-8",
    )
    protected = tmp_path / "repo"
    protected.mkdir()
    job = _job(tmp_path)
    facts = _facts()
    launch = WorkerLaunchFacts(
        pull_request=facts,
        configuration=WorkerConfiguration(worker_enabled=True, merged=True),
        checks=job.checks,
        repair_reason=job.repair_reason,
    )
    executor = OpenCodeExecutor(
        gate=gate or ExecutionGate(execution_disabled=False),
        host_policy=HostModePolicy(
            mode=HOST_MODE_LABEL,
            repositories=RepositoryPolicy(
                allowed=frozenset({_REPOSITORY}),
                public_exceptions=frozenset({_REPOSITORY}),
            ),
            model_ladder=ModelLadder.from_ids((_MODEL,), active=_MODEL),
        ),
        model_policy_path=policy_path,
        controlled_config=controlled,
        capability=lambda: capability
        or OpenCodeCapability("1.19.6", None, None, ()),
        process=process,
        registry=ProcessRegistry(),
        token_root=gitkeeper / "tokens",
        protected_roots=(protected,),
        clock=lambda: _AT,
        launch_facts=lambda claim: launch,
    )
    return executor, job, facts, port


def _execute(executor, job, facts, port, *, before=None):
    calls = before if before is not None else []
    return executor.execute(
        job,
        threading.Event(),
        approval_port=port,
        facts=lambda: facts,
        before_start=lambda: calls.append("start"),
    )


def test_disabled_gate_refuses_before_bridge_or_process(tmp_path) -> None:
    process = FakeProcess()
    executor, job, facts, port = _executor(
        tmp_path, process, FakePort(), gate=ExecutionGate()
    )

    with pytest.raises(WorkerLaunchBlocked):
        _execute(executor, job, facts, port)

    assert process.starts == []


def test_approved_job_starts_the_exact_process(tmp_path) -> None:
    process = FakeProcess()
    executor, job, facts, port = _executor(tmp_path, process, FakePort())
    calls: list[str] = []

    result = _execute(executor, job, facts, port, before=calls)

    assert result.outcome is AttemptOutcome.completed
    assert result.process_started is True
    assert result.execution.output_summary == "Worker process completed."
    assert result.execution_digest
    assert calls == ["start"]
    assert len(process.starts) == 1
    request, environment = process.starts[0]
    assert request.executable == "opencode"
    assert request.argv[:5] == ("run", "--format", "json", "--model", _MODEL)
    assert environment["GITKEEPER_BROKER_URL"].startswith("http://127.0.0.1:")
    assert "GITKEEPER_BROKER_TOKEN_FILE" in environment
    assert "GITKEEPER_BROKER_TIMEOUT_MS" in environment


def test_denial_never_starts_the_process(tmp_path) -> None:
    process = FakeProcess()
    executor, job, facts, port = _executor(tmp_path, process, FakePort("deny"))
    calls: list[str] = []

    result = _execute(executor, job, facts, port, before=calls)

    assert result.outcome is AttemptOutcome.failed
    assert result.process_started is False
    assert result.execution is None
    assert result.execution_digest is None
    assert calls == []
    assert process.starts == []


def test_model_unavailable_completion_is_preserved(tmp_path) -> None:
    completion = ProcessCompletion(
        AttemptOutcome.model_unavailable,
        1,
        OperationalError(
            ErrorCategory.model_unavailable, str(ModelUnavailable()), _AT
        ),
    )
    process = FakeProcess(completion)
    executor, job, facts, port = _executor(tmp_path, process, FakePort())

    result = _execute(executor, job, facts, port)

    assert result.outcome is AttemptOutcome.model_unavailable
    assert result.process_started is True
    assert result.execution.output_summary == "Worker model was unavailable."
    assert result.execution_digest


def test_stale_approval_before_consumption_has_no_execution(tmp_path) -> None:
    process = FakeProcess()
    executor, job, facts, port = _executor(tmp_path, process, FakePort())
    stale = replace(facts, head_sha="b" * 40, check_head_sha="b" * 40)
    calls = {"count": 0}

    def changing_facts() -> PullRequestFacts:
        calls["count"] += 1
        return facts if calls["count"] <= 2 else stale

    result = executor.execute(
        job,
        threading.Event(),
        approval_port=port,
        facts=changing_facts,
        before_start=lambda: None,
    )

    assert result.outcome is AttemptOutcome.failed
    assert result.process_started is False
    assert result.execution is None
    assert result.execution_digest is None
    assert process.starts == []


def test_before_start_failure_after_approval_is_fixed(tmp_path) -> None:
    process = FakeProcess()
    executor, job, facts, port = _executor(tmp_path, process, FakePort())

    def fail() -> None:
        raise RuntimeError("secret before-start text")

    result = executor.execute(
        job,
        threading.Event(),
        approval_port=port,
        facts=lambda: facts,
        before_start=fail,
    )

    assert result.outcome is AttemptOutcome.failed
    assert result.process_started is False
    assert result.execution.output_summary == "Worker process did not start."
    assert result.execution_digest
    assert process.starts == []
    assert "secret" not in result.execution.output_summary


def test_process_creation_failure_after_approval_is_fixed(tmp_path) -> None:
    process = FakeProcess(start_error=RuntimeError("secret command text"))
    executor, job, facts, port = _executor(tmp_path, process, FakePort())

    result = _execute(executor, job, facts, port)

    assert result.outcome is AttemptOutcome.failed
    assert result.process_started is False
    assert result.execution.output_summary == "Worker process did not start."
    assert result.execution_digest
    assert "secret" not in result.execution.output_summary


def test_shell_handoff_does_not_start_a_second_process(tmp_path) -> None:
    process = FakeProcess()
    job = _job(tmp_path)
    identity = job.worktree.identity
    request = CommandRequest(
        executable="opencode-shell",
        cwd=str(job.worktree.path),
        repository=_REPOSITORY,
        pr_number=42,
        starting_sha=_SHA,
        purpose="OpenCode shell call",
        script="echo hello",
        environment_names=("PATH",),
    )
    executor = BrokerCommandExecutor(
        process,
        lambda request: {"PATH": "C:/bin"},
        lambda request: None,
        threading.Event(),
        ProcessRegistry(),
        identity,
        job.claim.lease.lease_id,
        job.claim.attempt.attempt_id,
        ModelLadder.from_ids((_MODEL,), active=_MODEL),
        lambda: None,
        lambda: _AT,
    )

    result = executor(request)

    assert result.exit_code == 0
    assert "handed to OpenCode" in result.output_summary
    assert process.starts == []


def test_inert_worktree_blocks_before_start(tmp_path) -> None:
    process = FakeProcess()
    executor, job, facts, port = _executor(tmp_path, process, FakePort())
    (job.worktree.path / ".opencode").mkdir()

    with pytest.raises(ControlledConfigError):
        _execute(executor, job, facts, port)

    assert process.starts == []


def test_approval_port_binding_reports_and_isolates_failures() -> None:
    binding = ApprovalPortBinding()
    assert binding.get() is None
    assert binding.report_execution(
        ExecutionResult(0, "ok"), "digest"
    ) is False

    reported = []
    port = FakePort()
    binding.bind(
        port,
        execution_reporter=lambda result, digest: reported.append(digest) or True,
    )

    assert binding.get() is port
    assert binding.report_execution(
        ExecutionResult(0, "ok"), "digest"
    ) is True
    assert reported == ["digest"]

    binding.bind(port, execution_reporter=lambda result, digest: 1 / 0)
    assert binding.report_execution(
        ExecutionResult(1, "failed"), "digest"
    ) is False
