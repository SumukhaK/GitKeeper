import json
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from gitkeeper.host_mode.opencode_capability import (
    ControlledConfigError,
    require_inert_worktree,
)
from gitkeeper.host_mode.readiness import (
    HOST_MODE_LABEL,
    CliCapability,
    HostModePolicy,
    PullRequestFacts,
    RepositoryPolicy,
    WorkerConfiguration,
    WorkerLaunchBlocked,
    RepairReason as ReadinessRepairReason,
)
from gitkeeper.policy.models import LocalModelPolicy, ModelLadder
from gitkeeper.queue.models import (
    AttemptOutcome,
    CheckConclusion,
    CheckResult,
    CheckState,
    ClaimResult,
    InvocationAttempt,
    InvocationKind,
    Lease,
    QueueKey,
    RepairReason,
)
from gitkeeper.worker.models import (
    FeedbackRecord,
    ProcessCompletion,
    WorkerJob,
    WorktreeIdentity,
    WorktreeRecord,
)
from gitkeeper.worker.opencode import (
    DisabledWorkerExecutor,
    ExecutionGate,
    build_worker_prompt,
    process_execution_result,
    require_exact_active_model,
    require_launch_sha,
    require_worker_launch_ready,
    validate_windows_command_line,
)

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"
_MODEL = "opencode/deepseek-v4.1-flash-free"


def _claim(cycle_id: str | None = None) -> ClaimResult:
    cycle = cycle_id or str(uuid4())
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


def _job(
    tmp_path,
    *,
    reason: RepairReason = RepairReason.deterministic_check_failure,
    feedback_text: str | None = None,
) -> WorkerJob:
    claim = _claim()
    key = claim.lease.key
    cycle_id = claim.attempt.cycle_id
    worktree = WorktreeRecord(
        worktree_id=str(uuid4()),
        identity=WorktreeIdentity(
            repository=key.repository,
            pr_number=key.pr_number,
            head_sha=key.head_sha,
            cycle_id=cycle_id,
        ),
        path=tmp_path / "worktree",
        branch=None,
        created_at=_AT,
        preserved_reason=None,
    )
    feedback = (
        FeedbackRecord(
            feedback_id=str(uuid4()),
            repository=key.repository,
            pr_number=key.pr_number,
            head_sha=key.head_sha,
            cycle_id=cycle_id,
            text=feedback_text,
            created_at=_AT,
            attempt_id=None,
        )
        if feedback_text is not None
        else None
    )
    checks = CheckState(
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
    )
    return WorkerJob(
        claim=claim,
        worktree=worktree,
        model_id=_MODEL,
        repair_reason=reason,
        requires_test_change=reason is RepairReason.deterministic_check_failure,
        feedback=feedback,
        checks=checks,
    )


def _policies() -> tuple[LocalModelPolicy, HostModePolicy]:
    entries = (_MODEL, "opencode/glm-5.3")
    local = LocalModelPolicy(ladder=ModelLadder.from_ids(entries, active=_MODEL))
    host = HostModePolicy(
        mode=HOST_MODE_LABEL,
        repositories=RepositoryPolicy(
            allowed=frozenset({_REPOSITORY}),
            public_exceptions=frozenset({_REPOSITORY}),
        ),
        model_ladder=ModelLadder.from_ids(entries, active=_MODEL),
    )
    return local, host


def test_execution_gate_requires_explicit_enablement() -> None:
    with pytest.raises(WorkerLaunchBlocked):
        ExecutionGate().require_enabled()
    ExecutionGate(execution_disabled=False).require_enabled()
    with pytest.raises(TypeError):
        ExecutionGate(execution_disabled=1)


def test_only_the_exact_active_model_can_launch() -> None:
    local, host = _policies()

    assert require_exact_active_model(
        _MODEL, model_policy=local, host_policy=host
    ) == _MODEL

    for model_id in (
        "deepseek-v4.1-flash-free",
        f" {_MODEL}",
        "opencode/glm-5.3",
        "opencode/not-on-the-ladder",
        "anthropic/claude",
    ):
        with pytest.raises(WorkerLaunchBlocked):
            require_exact_active_model(
                model_id, model_policy=local, host_policy=host
            )

    host_without_model = HostModePolicy(
        mode=HOST_MODE_LABEL,
        repositories=RepositoryPolicy(
            allowed=frozenset({_REPOSITORY}),
            public_exceptions=frozenset({_REPOSITORY}),
        ),
        model_ladder=ModelLadder.from_ids(("opencode/glm-5.3",)),
    )
    with pytest.raises(WorkerLaunchBlocked):
        require_exact_active_model(
            _MODEL, model_policy=local, host_policy=host_without_model
        )


def test_64_hex_worker_job_blocks_before_any_broker_request() -> None:
    with pytest.raises(WorkerLaunchBlocked):
        require_launch_sha("a" * 64)
    assert require_launch_sha(_SHA) == _SHA


def test_prompt_is_single_line_and_lossless(tmp_path) -> None:
    feedback = "Line one\nLine two \u00e9 \U0001F600"
    job = _job(
        tmp_path,
        reason=RepairReason.claude_feedback,
        feedback_text=feedback,
    )

    prompt = build_worker_prompt(job)

    assert "\r" not in prompt
    assert "\n" not in prompt
    index = prompt.index("{")
    prefix = prompt[:index].rstrip()
    assert "detached GitKeeper worktree" in prefix
    payload = json.loads(prompt[index:])
    assert payload["feedback"] == feedback
    assert payload["checks"] == [["lint", "failure", _SHA]]
    assert payload["head_sha"] == _SHA
    assert payload["repair_reason"] == "claude_feedback"
    assert payload["requires_test_change"] is False
    assert "source" not in payload
    assert "diff" not in payload


def test_prompt_omits_feedback_for_non_claude_reasons(tmp_path) -> None:
    job = _job(tmp_path, reason=RepairReason.deterministic_check_failure)

    prompt = build_worker_prompt(job)
    payload = json.loads(prompt[prompt.index("{") :])

    assert payload["feedback"] is None
    assert payload["requires_test_change"] is True


def test_windows_command_line_and_environment_limits() -> None:
    validate_windows_command_line(("opencode", "run"), {"PATH": "C:/bin"})

    with pytest.raises(ValueError):
        validate_windows_command_line(("opencode", "x" * 40_000), {})
    with pytest.raises(ValueError):
        validate_windows_command_line(("opencode",), {"BIG": "x" * 40_000})
    with pytest.raises(ValueError):
        validate_windows_command_line(("opencode", "bad\nvalue"), {})
    with pytest.raises(ValueError):
        validate_windows_command_line(("opencode",), {"BAD\x00NAME": "value"})


def test_process_execution_results_are_fixed() -> None:
    assert process_execution_result(
        ProcessCompletion(AttemptOutcome.completed, 0, None)
    ).output_summary == "Worker process completed."
    assert process_execution_result(
        ProcessCompletion(AttemptOutcome.cancelled, 1, None)
    ).output_summary == "Worker process was cancelled."
    assert process_execution_result(
        ProcessCompletion(AttemptOutcome.model_unavailable, 1, None)
    ).output_summary == "Worker model was unavailable."
    failed = process_execution_result(
        ProcessCompletion(AttemptOutcome.failed, 7, None)
    )
    assert failed.exit_code == 7
    assert failed.output_summary == "Worker process failed."
    assert process_execution_result(
        ProcessCompletion(AttemptOutcome.failed, None, None)
    ).exit_code == 1
    assert "stderr" not in failed.output_summary


def test_disabled_executor_refuses_before_any_side_effect(tmp_path) -> None:
    job = _job(tmp_path)
    calls: list[str] = []

    def forbidden(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return call

    result = DisabledWorkerExecutor(lambda: _AT).execute(
        job,
        threading.Event(),
        approval_port=object(),
        facts=forbidden("facts"),
        before_start=forbidden("before_start"),
    )

    assert result.outcome is AttemptOutcome.cancelled
    assert result.model_id == _MODEL
    assert result.error.category.value == "worker_unavailable"
    assert result.process_started is False
    assert result.execution is None
    assert calls == []


def test_readiness_gate_accepts_a_complete_request() -> None:
    local, host = _policies()
    facts = PullRequestFacts(
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

    status = require_worker_launch_ready(
        facts=facts,
        config=WorkerConfiguration(worker_enabled=True, merged=True),
        policy=host,
        capability=CliCapability(cli_version="1.19.6", broker_verified=True),
    )

    assert status.ready
    assert require_exact_active_model(
        local.ladder.active, model_policy=local, host_policy=host
    )


def test_readiness_gate_blocks_a_closed_or_foreign_request() -> None:
    _, host = _policies()
    facts = PullRequestFacts(
        repository=_REPOSITORY,
        number=42,
        head_repository=_REPOSITORY,
        author="someone-else",
        head_sha=_SHA,
        check_head_sha=_SHA,
        is_open=False,
        is_draft=False,
        repair_reason=ReadinessRepairReason.DETERMINISTIC_CHECK_FAILURE,
        automatic_invocations_used=0,
    )

    with pytest.raises(WorkerLaunchBlocked):
        require_worker_launch_ready(
            facts=facts,
            config=WorkerConfiguration(worker_enabled=True, merged=True),
            policy=host,
            capability=CliCapability(cli_version="1.19.6", broker_verified=True),
        )


def test_inert_worktree_blocks_project_plugins(tmp_path) -> None:
    worktree = tmp_path / "worktree"
    (worktree / ".opencode").mkdir(parents=True)

    with pytest.raises(ControlledConfigError):
        require_inert_worktree(worktree)
