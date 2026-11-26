from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import get_type_hints
from uuid import UUID, uuid4

import pytest

from gitkeeper.host_mode.readiness import (
    MAX_AUTOMATIC_INVOCATIONS,
    OWNER_LOGIN,
    Blocker,
    ReadinessBlocker,
    RepairReason as ReadinessRepairReason,
    RepositoryPolicy,
)
from gitkeeper.queue import (
    AttemptBudgetExhausted,
    AttemptOutcome,
    CancellationPort,
    CheckConclusion,
    CheckOutcome,
    CheckResult,
    CheckState,
    ClaimResult,
    Eligibility,
    ErrorCategory,
    ErrorViewAdapter,
    GitHubAuthorizationError,
    GitHubResponseError,
    GitHubUnavailable,
    InvocationAttempt,
    InvocationKind,
    Lease,
    LocalLeaseConflict,
    ManualRetryLatch,
    ModelUnavailable,
    OperationalError,
    PilotConfig,
    ProjectionFence,
    PublicationIntent,
    PullRequestNotFound,
    PullRequestSnapshot,
    QueueError,
    QueueEvent,
    QueueItem,
    QueueKey,
    QueueViewAdapter,
    ReconcileResult,
    RemoteFailure,
    RemoteHealth,
    RemoteOperation,
    RemoteRepositoryReport,
    RepairCycle,
    RepairReason,
    RepairRequest,
    RetryAuthorization,
    RetryExpired,
    RetryInvalid,
    RetryKind,
    RetryReplayed,
    RemoteStale,
    SchemaMismatch,
    StorageCorrupt,
    StorageUnavailable,
    StaleHead,
    TelemetryViewAdapter,
    UncertainPublication,
    WorkerFailed,
    WorkerLabel,
    WorkerState,
    WorkerUnavailable,
    new_uuid4,
    parse_pilot_config,
    to_readiness_reason,
)

UTC = timezone.utc
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
SHA40 = "a153271a18241b3cf5b9d5ed7025916c6b11e9f8"
SHA64 = "a" * 64
OTHER_SHA40 = "b" * 40
REPOSITORY = "SumukhaK/Setu-mobile"
MODEL_ID = "opencode/deepseek-v4.1-flash-free"
AUTOMATIC_OWNER = "gitkeeper-automatic"


def identifier():
    return str(uuid4())


def config(**overrides):
    values = {
        "worker_enabled": True,
        "polling_preferences": {"mode": "adaptive"},
        "motion_preference": "reduced",
        "deterministic_check_ids": ("lint", "test"),
        "default_branch": "main",
        "default_branch_sha": SHA40,
    }
    values.update(overrides)
    return PilotConfig(**values)


def check_result(**overrides):
    values = {
        "check_id": "lint",
        "check_run_id": "101",
        "head_sha": SHA40,
        "conclusion": CheckConclusion.success,
    }
    values.update(overrides)
    return CheckResult(**values)


def check_state(**overrides):
    values = {
        "head_sha": SHA40,
        "required_check_ids": ("lint", "test"),
        "results": (
            check_result(),
            check_result(check_id="test", check_run_id="102"),
        ),
    }
    values.update(overrides)
    return CheckState(**values)


def snapshot(**overrides):
    values = {
        "repository": REPOSITORY,
        "pr_number": 42,
        "author": OWNER_LOGIN,
        "head_repository": REPOSITORY,
        "head_sha": SHA40,
        "is_open": True,
        "is_draft": False,
        "observed_at": NOW,
    }
    values.update(overrides)
    return PullRequestSnapshot(**values)


def queue_key(**overrides):
    values = {"repository": REPOSITORY, "pr_number": 42, "head_sha": SHA40}
    values.update(overrides)
    return QueueKey(**values)


def cycle(**overrides):
    values = {
        "cycle_id": identifier(),
        "cycle_number": 1,
        "initial_key": queue_key(),
        "opened_at": NOW,
        "closed_at": None,
    }
    values.update(overrides)
    return RepairCycle(**values)


def request(**overrides):
    values = {
        "request_id": identifier(),
        "key": queue_key(),
        "cycle_id": identifier(),
        "reason": RepairReason.deterministic_check_failure,
        "owner": None,
        "requested_at": NOW,
        "expires_at": None,
        "consumed_at": None,
        "retry_authorization": None,
    }
    values.update(overrides)
    return RepairRequest(**values)


def manual_request(**overrides):
    values = {
        "request_id": identifier(),
        "key": queue_key(),
        "cycle_id": identifier(),
        "reason": RepairReason.manual_retry,
        "owner": OWNER_LOGIN,
        "requested_at": NOW,
        "expires_at": LATER,
        "consumed_at": None,
        "retry_authorization": identifier(),
    }
    values.update(overrides)
    return RepairRequest(**values)


def authorization(*, origin=None, **overrides):
    origin = origin or manual_request()
    values = {
        "authorization": origin.retry_authorization,
        "request_id": origin.request_id,
        "key": origin.key,
        "cycle_id": origin.cycle_id,
        "owner": origin.owner,
        "kind": RetryKind.manual_retry,
        "created_at": NOW,
        "expires_at": origin.expires_at,
        "consumed_at": None,
    }
    values.update(overrides)
    return RetryAuthorization(**values)


def lease(*, retry_authorization=None, **overrides):
    values = {
        "lease_id": identifier(),
        "generation": 1,
        "attempt_id": identifier(),
        "owner": AUTOMATIC_OWNER if retry_authorization is None else OWNER_LOGIN,
        "key": queue_key(),
        "cycle_id": identifier(),
        "retry_authorization": retry_authorization,
        "claimed_at": NOW,
        "expires_at": LATER,
        "cancelled_at": None,
    }
    values.update(overrides)
    return Lease(**values)


def attempt(*, kind=InvocationKind.automatic, outcome=AttemptOutcome.reserved, **overrides):
    values = {
        "attempt_id": identifier(),
        "lease_id": identifier(),
        "key": queue_key(),
        "cycle_id": identifier(),
        "kind": kind,
        "outcome": outcome,
        "reserved_at": NOW,
        "started_at": None,
        "finished_at": None,
        "model_id": None,
        "error": None,
    }
    values.update(overrides)
    return InvocationAttempt(**values)


def operational_error(category=ErrorCategory.worker_failed):
    return OperationalError(category=category, message=str(QueueError(category)), occurred_at=NOW)


def claim(*, auth=None, kind=InvocationKind.automatic, publication=None, **overrides):
    auth = auth or authorization(consumed_at=NOW)
    cycle_id = auth.cycle_id
    claimed_at = auth.consumed_at or NOW
    claim_lease = lease(
        attempt_id=identifier(),
        cycle_id=cycle_id,
        key=auth.key,
        retry_authorization=None if kind is InvocationKind.automatic else auth.authorization,
        owner=AUTOMATIC_OWNER if kind is InvocationKind.automatic else OWNER_LOGIN,
        claimed_at=claimed_at,
        expires_at=auth.expires_at or LATER,
    )
    claim_attempt = attempt(
        attempt_id=claim_lease.attempt_id,
        lease_id=claim_lease.lease_id,
        key=claim_lease.key,
        cycle_id=cycle_id,
        kind=kind,
        reserved_at=claimed_at,
    )
    values = {
        "lease": claim_lease,
        "attempt": claim_attempt,
        "authorization": None if kind is InvocationKind.automatic else auth,
        "uncertain_publication": publication,
    }
    values.update(overrides)
    return ClaimResult(**values)


def publication_intent(**overrides):
    values = {
        "intent_id": identifier(),
        "attempt_id": identifier(),
        "key": queue_key(),
        "cycle_id": identifier(),
        "worktree_id": identifier(),
        "starting_sha": SHA40,
        "local_commit_sha": OTHER_SHA40,
        "recorded_at": NOW,
    }
    values.update(overrides)
    return PublicationIntent(**values)


def uncertain_publication(**overrides):
    values = {
        "publication_id": identifier(),
        "attempt_id": identifier(),
        "key": queue_key(),
        "cycle_id": identifier(),
        "worktree_id": identifier(),
        "local_commit_sha": OTHER_SHA40,
        "recorded_at": NOW,
        "resolved_at": None,
        "resolution": None,
    }
    values.update(overrides)
    return UncertainPublication(**values)


def latch(**overrides):
    values = {
        "latch_id": identifier(),
        "key": queue_key(),
        "cycle_id": identifier(),
        "attempt_id": identifier(),
        "category": ErrorCategory.worker_failed,
        "created_at": NOW,
        "cleared_at": None,
        "cleared_by_authorization": None,
    }
    values.update(overrides)
    return ManualRetryLatch(**values)


def queue_item(**overrides):
    values = {
        "repository": REPOSITORY,
        "pr_number": 42,
        "author": OWNER_LOGIN,
        "head_repository": REPOSITORY,
        "current_head_sha": SHA40,
        "observed_at": NOW,
        "cycle_id": identifier(),
        "eligible": True,
        "checks": check_state(),
        "state": WorkerState.queued,
        "reason": RepairReason.deterministic_check_failure,
        "remote_labels": (WorkerLabel.queued,),
        "automatic_attempt_count": 0,
        "manual_retry_count": 0,
        "last_error": None,
        "updated_at": NOW,
    }
    values.update(overrides)
    return QueueItem(**values)


def queue_event(**overrides):
    values = {
        "event_id": identifier(),
        "repository": REPOSITORY,
        "pr_number": 42,
        "head_sha": SHA40,
        "cycle_id": identifier(),
        "from_state": WorkerState.queued,
        "to_state": WorkerState.blocked,
        "occurred_at": NOW,
        "attempt_id": None,
        "error": operational_error(),
    }
    values.update(overrides)
    return QueueEvent(**values)


def telemetry_event(**overrides):
    values = {
        "timestamp": LATER,
        "duration_ms": 300000,
        "repository": REPOSITORY,
        "pr_number": 42,
        "head_sha": SHA40,
        "from_state": "running",
        "to_state": "ready",
        "invocation_count": 1,
        "outcome": "completed",
        "model_id": MODEL_ID,
        "error_category": None,
    }
    values.update(overrides)
    return values


def eligibility(**overrides):
    values = {
        "eligible": True,
        "state": WorkerState.queued,
        "reason": RepairReason.deterministic_check_failure,
        "blockers": (),
    }
    values.update(overrides)
    return Eligibility(**values)


def fence(**overrides):
    values = {
        "repository": REPOSITORY,
        "pr_number": 42,
        "observed_at": NOW,
        "cycle_id": identifier(),
        "head_sha": SHA40,
        "item_revision": 1,
        "projection_revision": 1,
        "active_lease_id": None,
        "active_attempt_id": None,
        "latch_id": None,
        "latch_cleared_at": None,
        "paused": False,
        "state": WorkerState.queued,
        "reason": RepairReason.deterministic_check_failure,
        "eligible": True,
        "desired_labels": (WorkerLabel.queued,),
    }
    values.update(overrides)
    return ProjectionFence(**values)


def remote_failure(**overrides):
    values = {
        "repository": REPOSITORY,
        "pr_number": 42,
        "operation": RemoteOperation.get_checks,
        "error": operational_error(ErrorCategory.github_unavailable),
    }
    values.update(overrides)
    return RemoteFailure(**values)


def remote_report(**overrides):
    values = {
        "repository": REPOSITORY,
        "health": RemoteHealth.healthy,
        "failures": (),
        "items": (),
        "closed_pr_numbers": (),
    }
    values.update(overrides)
    return RemoteRepositoryReport(**values)


def running_attempt(**overrides):
    values = {
        "outcome": AttemptOutcome.running,
        "started_at": NOW,
        "model_id": MODEL_ID,
    }
    values.update(overrides)
    return attempt(**values)


def test_string_enums_have_exact_wire_values():
    expected = {
        CheckConclusion: {
            "success": "success",
            "failure": "failure",
            "neutral": "neutral",
            "cancelled": "cancelled",
            "skipped": "skipped",
            "timed_out": "timed_out",
            "action_required": "action_required",
            "stale": "stale",
            "startup_failure": "startup_failure",
        },
        CheckOutcome: {
            "passed": "passed",
            "deterministic_failure": "deterministic_failure",
            "blocked": "blocked",
            "unknown": "unknown",
        },
        RepairReason: {
            "deterministic_check_failure": "deterministic_check_failure",
            "claude_feedback": "claude_feedback",
            "manual_retry": "manual_retry",
        },
        WorkerState: {"ready": "ready", "queued": "queued", "running": "running", "blocked": "blocked"},
        WorkerLabel: {
            "queued": "gitkeeper/worker-queued",
            "running": "gitkeeper/worker-running",
            "blocked": "gitkeeper/worker-blocked",
        },
        RetryKind: {"manual_retry": "manual_retry", "reconcile_unknown": "reconcile_unknown"},
        InvocationKind: {"automatic": "automatic", "manual_retry": "manual_retry", "reconcile_unknown": "reconcile_unknown"},
        AttemptOutcome: {
            "reserved": "reserved",
            "running": "running",
            "completed": "completed",
            "model_unavailable": "model_unavailable",
            "failed": "failed",
            "cancelled": "cancelled",
            "recovered_unknown": "recovered_unknown",
        },
        RemoteHealth: {"healthy": "healthy", "degraded": "degraded", "offline": "offline", "stale": "stale"},
        RemoteOperation: {
            "list_pull_requests": "list_pull_requests",
            "get_pull_request": "get_pull_request",
            "get_checks": "get_checks",
            "get_config": "get_config",
            "get_labels": "get_labels",
            "set_label": "set_label",
        },
    }
    expected[ErrorCategory] = {member.name.lower(): member.value for member in ErrorCategory}
    for enum_type, members in expected.items():
        assert issubclass(enum_type, str)
        assert {member.name: member.value for member in enum_type} == members


EXACT_FIELDS = {
    PilotConfig: "worker_enabled polling_preferences motion_preference deterministic_check_ids default_branch default_branch_sha",
    CheckResult: "check_id check_run_id head_sha conclusion",
    CheckState: "head_sha required_check_ids results",
    PullRequestSnapshot: "repository pr_number author head_repository head_sha is_open is_draft observed_at",
    QueueKey: "repository pr_number head_sha",
    RepairCycle: "cycle_id cycle_number initial_key opened_at closed_at",
    RepairRequest: "request_id key cycle_id reason owner requested_at expires_at consumed_at retry_authorization",
    RetryAuthorization: "authorization request_id key cycle_id owner kind created_at expires_at consumed_at",
    Lease: "lease_id generation attempt_id owner key cycle_id retry_authorization claimed_at expires_at cancelled_at",
    PublicationIntent: "intent_id attempt_id key cycle_id worktree_id starting_sha local_commit_sha recorded_at",
    UncertainPublication: "publication_id attempt_id key cycle_id worktree_id local_commit_sha recorded_at resolved_at resolution",
    ManualRetryLatch: "latch_id key cycle_id attempt_id category created_at cleared_at cleared_by_authorization",
    InvocationAttempt: "attempt_id lease_id key cycle_id kind outcome reserved_at started_at finished_at model_id error",
    ClaimResult: "lease attempt authorization uncertain_publication",
    QueueItem: "repository pr_number author head_repository current_head_sha observed_at cycle_id eligible checks state reason remote_labels automatic_attempt_count manual_retry_count last_error updated_at",
    QueueEvent: "event_id repository pr_number head_sha cycle_id from_state to_state occurred_at attempt_id error",
    Eligibility: "eligible state reason blockers",
    ProjectionFence: "repository pr_number observed_at cycle_id head_sha item_revision projection_revision active_lease_id active_attempt_id latch_id latch_cleared_at paused state reason eligible desired_labels",
    RemoteFailure: "repository pr_number operation error",
    RemoteRepositoryReport: "repository health failures items closed_pr_numbers",
    ReconcileResult: "reports",
    QueueViewAdapter: "item",
    ErrorViewAdapter: "error",
    TelemetryViewAdapter: "timestamp duration_ms repository pr_number head_sha from_state to_state attempt_count outcome model_id error_category",
    OperationalError: "category message occurred_at",
}


@pytest.mark.parametrize("model,names", EXACT_FIELDS.items())
def test_domain_dataclasses_have_exact_schema(model, names):
    assert tuple(field.name for field in fields(model)) == tuple(names.split())


def test_readiness_imports_are_unchanged_and_config_types_are_precise():
    assert Blocker.__module__ == "gitkeeper.host_mode.readiness"
    assert ReadinessBlocker.__module__ == "gitkeeper.host_mode.readiness"
    assert ReadinessRepairReason.__module__ == "gitkeeper.host_mode.readiness"
    assert RepositoryPolicy.__module__ == "gitkeeper.host_mode.readiness"
    assert get_type_hints(PilotConfig)["polling_preferences"] == Mapping[str, str]


@pytest.mark.parametrize(
    "value",
    [
        config(),
        check_result(),
        check_state(),
        snapshot(),
        queue_key(),
        cycle(),
        request(),
        manual_request(),
        authorization(),
        lease(),
        attempt(),
        running_attempt(),
        claim(),
        publication_intent(),
        uncertain_publication(),
        latch(),
        queue_item(),
        queue_event(),
        eligibility(),
        fence(),
        operational_error(),
        QueueViewAdapter(queue_item()),
        ErrorViewAdapter(operational_error()),
        remote_failure(),
        remote_report(),
        ReconcileResult(reports=()),
    ],
)
def test_domain_values_are_frozen(value):
    with pytest.raises(FrozenInstanceError):
        setattr(value, fields(value)[0].name, None)


def test_new_authority_ids_are_unique_lowercase_uuid4_values():
    values = {new_uuid4() for _ in range(100)}
    assert len(values) == 100
    for value in values:
        assert value == value.lower()
        assert len(value) == 36
        assert UUID(value).version == 4


@pytest.mark.parametrize("value", ["0" * 36, "00000000-0000-0000-0000-000000000001", "123e4567-e89b-11d3-a456-426614174000", "123E4567-E89B-42D3-A456-426614174000", "not-a-uuid", 1])
def test_uuid4_fields_reject_nil_v1_uppercase_and_non_string_values(value):
    with pytest.raises(ValueError):
        cycle(cycle_id=value)
    with pytest.raises(ValueError):
        publication_intent(worktree_id=value)


@pytest.mark.parametrize("value", ["a" * 39, "a" * 41, "a" * 63, "a" * 65, "A" * 40, "g" * 40, f" {SHA40}", f"{SHA40} ", None, 40])
def test_shas_must_be_exact_40_or_64_lowercase_hex(value):
    with pytest.raises(ValueError):
        queue_key(head_sha=value)


def test_valid_40_and_64_hex_shas_are_accepted_without_normalization():
    assert queue_key(head_sha=SHA40).head_sha == SHA40
    assert queue_key(head_sha=SHA64).head_sha == SHA64


@pytest.mark.parametrize("value", ["owner", "owner/", "/repo", "owner/repo/extra", "own er/repo", "owner/re po", " owner/repo", "owner/repo ", None])
def test_repository_shape_is_exact(value):
    with pytest.raises(ValueError):
        queue_key(repository=value)


def test_aware_timestamps_are_normalized_to_utc_and_naive_values_are_rejected():
    offset = timezone(timedelta(hours=5, minutes=30))
    observed = NOW.astimezone(offset)
    assert snapshot(observed_at=observed).observed_at == NOW
    with pytest.raises(ValueError):
        snapshot(observed_at=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "model,field,value",
    [
        (snapshot(), "pr_number", True),
        (cycle(), "cycle_number", True),
        (lease(), "generation", True),
        (queue_item(), "automatic_attempt_count", True),
        (queue_item(), "manual_retry_count", False),
        (fence(), "item_revision", True),
        (fence(), "projection_revision", False),
        (remote_failure(), "pr_number", True),
    ],
)
def test_integer_fields_reject_booleans(model, field, value):
    with pytest.raises(ValueError):
        replace(model, **{field: value})


def test_positive_and_non_negative_integer_boundaries():
    assert snapshot(pr_number=1).pr_number == 1
    assert cycle(cycle_number=1).cycle_number == 1
    assert lease(generation=1).generation == 1
    assert queue_item(automatic_attempt_count=0, manual_retry_count=0)
    for model, field, value in [
        (snapshot(), "pr_number", 0),
        (cycle(), "cycle_number", 0),
        (lease(), "generation", 0),
        (queue_item(), "automatic_attempt_count", -1),
        (queue_item(), "manual_retry_count", -1),
        (fence(), "item_revision", 0),
        (fence(), "projection_revision", 0),
    ]:
        with pytest.raises(ValueError):
            replace(model, **{field: value})


def test_pilot_config_is_immutable_and_detached_from_source_mapping():
    source = {"mode": "adaptive"}
    parsed = parse_pilot_config(
        {
            "worker_enabled": True,
            "polling_preferences": source,
            "motion_preference": None,
            "deterministic_check_ids": ["lint", "test"],
        },
        "main",
        SHA40,
    )
    source["mode"] = "disabled"
    assert dict(parsed.polling_preferences) == {"mode": "adaptive"}
    with pytest.raises(TypeError):
        parsed.polling_preferences["mode"] = "disabled"
    with pytest.raises(FrozenInstanceError):
        parsed.worker_enabled = False
    assert parsed.default_branch == "main"
    assert parsed.default_branch_sha == SHA40


@pytest.mark.parametrize("motion", [None, "reduced", "disabled"])
def test_pilot_config_accepts_exact_motion_values(motion):
    assert config(motion_preference=motion).motion_preference == motion


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive"}, "motion_preference": None},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive"}, "motion_preference": None, "deterministic_check_ids": ["lint"], "default_branch_sha": SHA40},
        {"worker_enabled": 1, "polling_preferences": {"mode": "adaptive"}, "motion_preference": None, "deterministic_check_ids": ["lint"]},
        {"worker_enabled": True, "polling_preferences": {"mode": "fixed"}, "motion_preference": None, "deterministic_check_ids": ["lint"]},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive", "seconds": 5}, "motion_preference": None, "deterministic_check_ids": ["lint"]},
        {"worker_enabled": True, "polling_preferences": "adaptive", "motion_preference": None, "deterministic_check_ids": ["lint"]},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive"}, "motion_preference": "full", "deterministic_check_ids": ["lint"]},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive"}, "motion_preference": None, "deterministic_check_ids": []},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive"}, "motion_preference": None, "deterministic_check_ids": ["lint", " lint "]},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive"}, "motion_preference": None, "deterministic_check_ids": [" "]},
        {"worker_enabled": True, "polling_preferences": {"mode": "adaptive"}, "motion_preference": None, "deterministic_check_ids": "lint"},
    ],
)
def test_parse_pilot_config_rejects_malformed_remote_payloads_without_echoing_values(payload):
    with pytest.raises(ValueError) as caught:
        parse_pilot_config(payload, "main", SHA40)
    assert str(caught.value) == "Invalid pilot configuration."
    assert "SECRET" not in str(caught.value)


def test_parse_pilot_config_trims_ids_preserves_order_and_binds_trusted_metadata():
    parsed = parse_pilot_config(
        {
            "worker_enabled": False,
            "polling_preferences": {"mode": "adaptive"},
            "motion_preference": "disabled",
            "deterministic_check_ids": [" test ", "lint"],
        },
        " release/stable ",
        SHA64,
    )
    assert parsed.worker_enabled is False
    assert parsed.deterministic_check_ids == ("test", "lint")
    assert parsed.default_branch == "release/stable"
    assert parsed.default_branch_sha == SHA64


def test_readiness_reason_adapter_maps_only_automatic_reasons():
    assert to_readiness_reason(RepairReason.deterministic_check_failure) is ReadinessRepairReason.DETERMINISTIC_CHECK_FAILURE
    assert to_readiness_reason(RepairReason.claude_feedback) is ReadinessRepairReason.REVIEW_FEEDBACK
    with pytest.raises(ValueError) as caught:
        to_readiness_reason(RepairReason.manual_retry)
    assert str(caught.value) == "Manual retry is not an automatic launch reason."
    with pytest.raises(ValueError):
        to_readiness_reason(ReadinessRepairReason.REVIEW_FEEDBACK)


def test_check_result_identity_is_trimmed_unique_and_positive_decimal():
    result = check_result(check_id=" lint ", check_run_id=" 101 ")
    assert result.check_id == "lint"
    assert result.check_run_id == "101"
    for run_id in ("", "0", "-1", "+1", "1.0", "abc", None):
        with pytest.raises(ValueError):
            check_result(check_run_id=run_id)
    with pytest.raises(ValueError):
        check_state(results=(check_result(), check_result(check_id="test", check_run_id="101")))
    with pytest.raises(ValueError):
        check_state(results=(check_result(check_id="other"),))


def test_check_ids_are_normalized_and_required_ids_are_unique():
    state = check_state(required_check_ids=(" lint ", "test"))
    assert state.required_check_ids == ("lint", "test")
    for required in ((), ("lint", " lint "), ("",), ("lint", 1)):
        with pytest.raises(ValueError):
            check_state(required_check_ids=required)
    with pytest.raises(ValueError):
        check_result(check_id=" ")


@pytest.mark.parametrize(
    "state,expected",
    [
        (check_state(), CheckOutcome.passed),
        (check_state(results=(check_result(conclusion=CheckConclusion.failure), check_result(check_id="test", check_run_id="102"))), CheckOutcome.deterministic_failure),
        (check_state(results=(check_result(),)), CheckOutcome.unknown),
        (check_state(results=(check_result(), check_result(check_run_id="102"), check_result(check_id="test", check_run_id="103"))), CheckOutcome.unknown),
        (check_state(results=(check_result(head_sha=OTHER_SHA40),)), CheckOutcome.blocked),
        (check_state(results=(check_result(conclusion=CheckConclusion.neutral),)), CheckOutcome.blocked),
        (check_state(results=(check_result(conclusion=CheckConclusion.failure), check_result(check_id="test", check_run_id="102", conclusion=CheckConclusion.startup_failure))), CheckOutcome.blocked),
    ],
)
def test_check_outcome_precedence(state, expected):
    assert state.outcome is expected


def test_stale_success_evidence_cannot_override_startup_failure():
    state = check_state(
        results=(
            check_result(check_id="test", check_run_id="103", head_sha=OTHER_SHA40),
            check_result(check_id="lint", conclusion=CheckConclusion.startup_failure),
        )
    )
    assert state.outcome is CheckOutcome.blocked
    assert state.results[0].conclusion is CheckConclusion.success


def test_snapshot_preserves_deleted_optional_identities_and_exact_matching():
    deleted = snapshot(author=None, head_repository=None)
    assert deleted.author is None
    assert deleted.head_repository is None
    assert deleted.same_repository is False
    assert deleted.author_is_owner is False
    assert snapshot().same_repository is True
    assert snapshot().author_is_owner is True
    assert snapshot(author=OWNER_LOGIN.lower()).author_is_owner is False
    assert snapshot(head_repository=REPOSITORY.lower()).same_repository is False


def test_cycle_close_time_and_open_cycle_shape():
    assert cycle().closed_at is None
    assert cycle(closed_at=NOW).closed_at == NOW
    with pytest.raises(ValueError):
        cycle(closed_at=NOW - timedelta(microseconds=1))


def test_automatic_requests_allow_only_the_two_automatic_reasons():
    assert request(reason=RepairReason.claude_feedback).owner is None
    for overrides in (
        {"owner": OWNER_LOGIN},
        {"expires_at": LATER},
        {"consumed_at": NOW},
        {"retry_authorization": identifier()},
    ):
        with pytest.raises(ValueError):
            request(**overrides)
    with pytest.raises(ValueError):
        request(reason=RepairReason.manual_retry)


def test_manual_requests_require_owner_authority_and_unconsumed_or_valid_consumption():
    assert manual_request().owner == OWNER_LOGIN
    assert manual_request(consumed_at=NOW).consumed_at == NOW
    invalid = [
        {"owner": None},
        {"owner": OWNER_LOGIN.lower()},
        {"owner": f" {OWNER_LOGIN}"},
        {"retry_authorization": None},
        {"expires_at": None},
        {"expires_at": NOW},
        {"consumed_at": NOW - timedelta(microseconds=1)},
        {"consumed_at": LATER},
    ]
    for overrides in invalid:
        with pytest.raises(ValueError):
            manual_request(**overrides)


def test_retry_authorization_defaults_to_manual_retry():
    origin = manual_request()
    auth = RetryAuthorization(
        authorization=origin.retry_authorization,
        request_id=origin.request_id,
        key=origin.key,
        cycle_id=origin.cycle_id,
        owner=origin.owner,
        created_at=NOW,
        expires_at=origin.expires_at,
    )
    assert auth.kind is RetryKind.manual_retry
    assert auth.consumed_at is None


def test_retry_authorization_from_request_preserves_origin_and_consumption_window():
    origin = manual_request()
    auth = RetryAuthorization.from_request(origin, kind=RetryKind.manual_retry, created_at=NOW)
    assert auth.authorization == origin.retry_authorization
    assert auth.request_id == origin.request_id
    assert auth.key == origin.key
    assert auth.cycle_id == origin.cycle_id
    assert auth.owner == OWNER_LOGIN
    assert auth.expires_at == origin.expires_at
    consumed = replace(auth, consumed_at=NOW)
    assert consumed.consumed_at == NOW
    with pytest.raises(ValueError):
        replace(auth, expires_at=NOW)
    with pytest.raises(ValueError):
        replace(auth, consumed_at=NOW - timedelta(microseconds=1))
    with pytest.raises(ValueError):
        replace(auth, consumed_at=auth.expires_at)
    with pytest.raises(ValueError):
        replace(auth, owner=OWNER_LOGIN.lower())


def test_retry_authorization_rejects_non_manual_origin_and_binds_valid_origin():
    origin = manual_request()
    with pytest.raises(ValueError):
        RetryAuthorization.from_request(request(), kind=RetryKind.manual_retry, created_at=NOW)
    auth = authorization(origin=origin)
    assert auth.request_id == origin.request_id
    assert auth.authorization == origin.retry_authorization
    assert auth.key == origin.key
    assert auth.cycle_id == origin.cycle_id
    assert auth.owner == origin.owner
    assert auth.expires_at == origin.expires_at
    with pytest.raises(ValueError):
        replace(auth, owner=OWNER_LOGIN.lower())
    with pytest.raises(ValueError):
        RetryAuthorization.from_request(origin, created_at=origin.expires_at)


def test_lease_owner_is_metadata_not_authentication_and_times_are_ordered():
    assert lease().owner == AUTOMATIC_OWNER
    token = identifier()
    assert lease(retry_authorization=token).owner == OWNER_LOGIN
    assert lease(cancelled_at=NOW).cancelled_at == NOW
    for overrides in (
        {"owner": OWNER_LOGIN},
        {"retry_authorization": token, "owner": AUTOMATIC_OWNER},
        {"expires_at": NOW},
        {"cancelled_at": NOW - timedelta(microseconds=1)},
    ):
        with pytest.raises(ValueError):
            lease(**overrides)


def test_invocation_attempt_valid_state_shapes():
    error = operational_error()
    model_error = operational_error(ErrorCategory.model_unavailable)
    valid = [
        attempt(),
        running_attempt(),
        attempt(outcome=AttemptOutcome.completed, started_at=NOW, finished_at=LATER, model_id=MODEL_ID),
        attempt(outcome=AttemptOutcome.model_unavailable, finished_at=LATER, model_id=MODEL_ID, error=model_error),
        attempt(outcome=AttemptOutcome.model_unavailable, started_at=NOW, finished_at=LATER, model_id=MODEL_ID, error=model_error),
        attempt(outcome=AttemptOutcome.failed, finished_at=LATER, error=error),
        attempt(outcome=AttemptOutcome.failed, started_at=NOW, finished_at=LATER, model_id=MODEL_ID, error=error),
        attempt(outcome=AttemptOutcome.recovered_unknown, started_at=NOW, finished_at=LATER, model_id=MODEL_ID, error=error),
        attempt(outcome=AttemptOutcome.cancelled, finished_at=LATER),
        attempt(outcome=AttemptOutcome.cancelled, started_at=NOW, finished_at=LATER, model_id=MODEL_ID),
    ]
    assert len(valid) == 10


def test_reconciliation_attempt_allows_only_cancelled_or_failed_terminal_outcomes():
    cancelled = attempt(
        kind=InvocationKind.reconcile_unknown,
        outcome=AttemptOutcome.cancelled,
        finished_at=LATER,
    )
    failed = attempt(
        kind=InvocationKind.reconcile_unknown,
        outcome=AttemptOutcome.failed,
        finished_at=LATER,
        error=operational_error(),
    )
    assert cancelled.kind is InvocationKind.reconcile_unknown
    assert cancelled.outcome is AttemptOutcome.cancelled
    assert cancelled.started_at is None and cancelled.model_id is None and cancelled.error is None
    assert failed.kind is InvocationKind.reconcile_unknown
    assert failed.outcome is AttemptOutcome.failed
    assert failed.started_at is None and failed.model_id is None
    assert failed.error == operational_error()


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": AttemptOutcome.running, "started_at": NOW, "model_id": MODEL_ID},
        {"outcome": AttemptOutcome.completed, "started_at": NOW, "finished_at": LATER, "model_id": MODEL_ID},
        {"outcome": AttemptOutcome.model_unavailable, "finished_at": LATER, "model_id": MODEL_ID, "error": operational_error(ErrorCategory.model_unavailable)},
        {"outcome": AttemptOutcome.recovered_unknown, "started_at": NOW, "finished_at": LATER, "model_id": MODEL_ID, "error": operational_error()},
        {"outcome": AttemptOutcome.cancelled, "finished_at": LATER, "error": operational_error()},
        {"outcome": AttemptOutcome.failed, "finished_at": LATER},
        {"outcome": AttemptOutcome.failed, "finished_at": LATER, "error": operational_error(ErrorCategory.worker_unavailable)},
        {"outcome": AttemptOutcome.cancelled, "started_at": NOW, "finished_at": LATER, "model_id": MODEL_ID},
        {"outcome": AttemptOutcome.failed, "finished_at": LATER, "model_id": MODEL_ID, "error": operational_error()},
    ],
)
def test_reconciliation_attempt_rejects_launch_and_invalid_terminal_shapes(overrides):
    with pytest.raises(ValueError):
        attempt(kind=InvocationKind.reconcile_unknown, **overrides)


def test_reconciliation_reserved_transition_never_acquires_running_launch_state():
    reserved = attempt(kind=InvocationKind.reconcile_unknown)
    cancelled = reserved.transition_to(AttemptOutcome.cancelled, finished_at=LATER)
    failed = reserved.transition_to(AttemptOutcome.failed, finished_at=LATER, error=operational_error())
    assert cancelled.outcome is AttemptOutcome.cancelled and cancelled.started_at is None
    assert failed.outcome is AttemptOutcome.failed and failed.started_at is None
    with pytest.raises(ValueError):
        reserved.transition_to(AttemptOutcome.running, started_at=NOW, model_id=MODEL_ID)
    with pytest.raises(ValueError):
        reserved.transition_to(AttemptOutcome.model_unavailable, finished_at=LATER, model_id=MODEL_ID, error=operational_error(ErrorCategory.model_unavailable))


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": AttemptOutcome.reserved, "started_at": NOW},
        {"outcome": AttemptOutcome.running, "started_at": NOW},
        {"outcome": AttemptOutcome.running, "started_at": NOW, "model_id": MODEL_ID, "error": operational_error()},
        {"outcome": AttemptOutcome.running, "started_at": NOW, "model_id": MODEL_ID, "finished_at": LATER},
        {"outcome": AttemptOutcome.completed, "started_at": NOW, "finished_at": LATER},
        {"outcome": AttemptOutcome.completed, "started_at": NOW, "finished_at": LATER, "model_id": MODEL_ID, "error": operational_error()},
        {"outcome": AttemptOutcome.failed, "finished_at": LATER},
        {"outcome": AttemptOutcome.failed, "finished_at": LATER, "error": operational_error(ErrorCategory.worker_unavailable)},
        {"outcome": AttemptOutcome.recovered_unknown, "finished_at": LATER, "error": operational_error()},
        {"outcome": AttemptOutcome.model_unavailable, "finished_at": LATER, "model_id": MODEL_ID},
        {"outcome": AttemptOutcome.model_unavailable, "finished_at": LATER, "model_id": MODEL_ID, "error": operational_error()},
        {"outcome": AttemptOutcome.cancelled, "finished_at": LATER, "error": operational_error()},
        {"outcome": AttemptOutcome.cancelled, "started_at": NOW, "finished_at": LATER},
        {"outcome": AttemptOutcome.cancelled, "started_at": NOW - timedelta(microseconds=1), "finished_at": LATER},
        {"outcome": AttemptOutcome.cancelled, "finished_at": NOW - timedelta(microseconds=1)},
        {"outcome": AttemptOutcome.completed, "started_at": NOW, "finished_at": NOW - timedelta(microseconds=1), "model_id": MODEL_ID},
        {"kind": InvocationKind.reconcile_unknown, "outcome": AttemptOutcome.running, "started_at": NOW, "model_id": MODEL_ID},
        {"kind": InvocationKind.reconcile_unknown, "outcome": AttemptOutcome.recovered_unknown, "started_at": NOW, "finished_at": LATER, "model_id": MODEL_ID, "error": operational_error()},
    ],
)
def test_invocation_attempt_rejects_invalid_state_shapes(overrides):
    with pytest.raises(ValueError):
        attempt(**overrides)


@pytest.mark.parametrize("model_id", ["opencode/", "opencode/model name", "opencode/ model", "other/model", " opencode/model", "opencode/model\n", None])
def test_attempt_model_ids_are_exact_opencode_ids(model_id):
    if model_id is None:
        assert attempt().model_id is None
    else:
        with pytest.raises(ValueError):
            running_attempt(model_id=model_id)


def test_attempt_transitions_enforce_the_durable_launch_boundary():
    reserved = attempt()
    running = reserved.transition_to(AttemptOutcome.running, started_at=NOW, model_id=MODEL_ID)
    assert running.outcome is AttemptOutcome.running
    assert reserved.outcome is AttemptOutcome.reserved
    assert running.transition_to(AttemptOutcome.completed, finished_at=LATER).outcome is AttemptOutcome.completed
    assert reserved.transition_to(AttemptOutcome.cancelled, finished_at=LATER).outcome is AttemptOutcome.cancelled
    assert reserved.transition_to(AttemptOutcome.failed, finished_at=LATER, error=operational_error()).outcome is AttemptOutcome.failed
    assert reserved.transition_to(AttemptOutcome.model_unavailable, finished_at=LATER, model_id=MODEL_ID, error=operational_error(ErrorCategory.model_unavailable)).outcome is AttemptOutcome.model_unavailable
    for previous, target in (
        (reserved, AttemptOutcome.completed),
        (reserved, AttemptOutcome.recovered_unknown),
        (running, AttemptOutcome.reserved),
        (running.transition_to(AttemptOutcome.completed, finished_at=LATER), AttemptOutcome.running),
    ):
        with pytest.raises(ValueError):
            previous.transition_to(target, started_at=NOW, finished_at=LATER, model_id=MODEL_ID)


def test_recovered_unknown_is_written_only_by_recovery_of_a_started_failure():
    failed = running_attempt().transition_to(AttemptOutcome.failed, finished_at=LATER, error=operational_error())
    recovered = failed.recover(finished_at=LATER)
    assert recovered.outcome is AttemptOutcome.recovered_unknown
    assert recovered.started_at == failed.started_at
    with pytest.raises(ValueError):
        attempt(outcome=AttemptOutcome.failed, finished_at=LATER, error=operational_error()).recover(finished_at=LATER)


def test_cancellation_port_is_optional_runtime_checkable_and_returns_only_confirmation():
    target = lease()

    class Canceller:
        def cancel(self, lease):
            return lease == target

    assert isinstance(Canceller(), CancellationPort)
    assert Canceller().cancel(target) is True
    assert Canceller().cancel(lease()) is False
    assert not isinstance(object(), CancellationPort)


def test_publication_intent_and_uncertain_publication_have_no_path_or_payload_fields():
    for model in (PublicationIntent, UncertainPublication):
        names = {field.name for field in fields(model)}
        assert not names & {"path", "diff", "output", "credential", "prompt", "response", "exception"}
    intent = publication_intent()
    assert intent.starting_sha == SHA40
    assert intent.local_commit_sha == OTHER_SHA40


@pytest.mark.parametrize("resolution", ["remote_equal", "remote_diverged", "validation_failed", "superseded"])
def test_uncertain_publication_resolution_values_and_pairing(resolution):
    resolved = uncertain_publication(resolution=resolution, resolved_at=LATER)
    assert resolved.resolution == resolution
    assert resolved.resolved_at == LATER


def test_uncertain_publication_rejects_partial_or_invalid_resolution():
    for overrides in (
        {"resolution": "remote_equal"},
        {"resolved_at": LATER},
        {"resolution": "unknown", "resolved_at": LATER},
        {"resolution": [], "resolved_at": LATER},
        {"resolution": {}, "resolved_at": LATER},
        {"resolution": "remote_equal", "resolved_at": NOW - timedelta(microseconds=1)},
        {"local_commit_sha": "not-a-sha"},
    ):
        with pytest.raises(ValueError):
            uncertain_publication(**overrides)


def test_manual_retry_latch_is_cycle_scoped_and_clear_metadata_is_paired():
    original = latch()
    assert replace(original, key=queue_key(head_sha=OTHER_SHA40)).cycle_id == original.cycle_id
    cleared = latch(cleared_at=LATER, cleared_by_authorization=identifier())
    assert cleared.cleared_at == LATER
    for overrides in (
        {"category": ErrorCategory.github_unavailable},
        {"cleared_at": LATER},
        {"cleared_by_authorization": identifier()},
        {"cleared_at": NOW - timedelta(microseconds=1), "cleared_by_authorization": identifier()},
        {"cleared_at": LATER, "cleared_by_authorization": "not-a-uuid"},
    ):
        with pytest.raises(ValueError):
            latch(**overrides)


def test_automatic_and_manual_claims_are_reservations_not_launch_authorizations():
    automatic = claim()
    assert automatic.attempt.outcome is AttemptOutcome.reserved
    assert automatic.authorization is None
    assert automatic.lease.owner == AUTOMATIC_OWNER
    manual = claim(kind=InvocationKind.manual_retry)
    assert manual.attempt.outcome is AttemptOutcome.reserved
    assert manual.authorization.consumed_at == manual.lease.claimed_at
    assert manual.authorization.kind is RetryKind.manual_retry
    assert manual.lease.retry_authorization == manual.authorization.authorization
    with pytest.raises(ValueError):
        claim(kind=InvocationKind.manual_retry, auth=authorization())
    with pytest.raises(ValueError):
        claim(auth=authorization(consumed_at=NOW), publication=uncertain_publication())
    with pytest.raises(ValueError):
        claim(auth=authorization(consumed_at=NOW), lease=lease(retry_authorization=identifier()))


def test_reconciliation_claim_uses_current_key_but_exact_unresolved_original_association():
    original_key = queue_key()
    current_key = queue_key(head_sha=OTHER_SHA40)
    reconcile_origin = manual_request(key=current_key)
    reconcile_auth = RetryAuthorization.from_request(reconcile_origin, kind=RetryKind.reconcile_unknown, created_at=NOW, consumed_at=NOW)
    original_attempt_id = identifier()
    publication = uncertain_publication(
        attempt_id=original_attempt_id,
        key=original_key,
        cycle_id=reconcile_origin.cycle_id,
    )
    base = claim(
        kind=InvocationKind.reconcile_unknown,
        auth=reconcile_auth,
        publication=publication,
    )
    reconciliation = replace(
        base,
        lease=replace(base.lease, key=current_key),
        attempt=replace(base.attempt, key=current_key),
    )
    assert reconciliation.lease.key == current_key
    assert reconciliation.uncertain_publication.key == original_key
    assert reconciliation.uncertain_publication.attempt_id == original_attempt_id
    assert reconciliation.attempt.attempt_id != original_attempt_id
    assert reconciliation.attempt.outcome is AttemptOutcome.reserved
    assert reconciliation.authorization.kind is RetryKind.reconcile_unknown
    with pytest.raises(ValueError):
        replace(reconciliation.attempt, outcome=AttemptOutcome.running, started_at=NOW, model_id=MODEL_ID)


def test_reconciliation_claim_rejects_wrong_scope_resolved_or_missing_association():
    origin = manual_request()
    auth = authorization(origin=origin, kind=RetryKind.reconcile_unknown, consumed_at=NOW)
    base = claim(kind=InvocationKind.reconcile_unknown, auth=auth, publication=uncertain_publication(attempt_id=identifier(), key=origin.key, cycle_id=origin.cycle_id))
    invalid_publications = [
        None,
        replace(base.uncertain_publication, resolved_at=LATER, resolution="remote_equal"),
        replace(base.uncertain_publication, key=queue_key(repository="SumukhaK/other")),
        replace(base.uncertain_publication, key=queue_key(pr_number=43)),
        replace(base.uncertain_publication, cycle_id=identifier()),
    ]
    for publication in invalid_publications:
        with pytest.raises(ValueError):
            replace(base, uncertain_publication=publication)


def test_queue_item_aliases_and_ui_error_shape():
    item = queue_item(last_error=operational_error())
    assert item.check_state is item.checks
    assert item.check_state.outcome is item.checks.outcome
    assert item.last_updated is item.updated_at
    assert item.last_error.category is ErrorCategory.worker_failed
    assert item.checks.outcome is CheckOutcome.passed


def test_queue_item_state_reason_and_eligibility_are_consistent():
    assert queue_item(state=WorkerState.ready, reason=None, eligible=False, remote_labels=()).state is WorkerState.ready
    assert queue_item(state=WorkerState.blocked, eligible=False, reason=RepairReason.deterministic_check_failure).eligible is False
    assert queue_item(state=WorkerState.running, eligible=True).state is WorkerState.running
    assert queue_item(reason=RepairReason.manual_retry, eligible=False).eligible is False
    invalid = [
        {"state": WorkerState.ready, "reason": RepairReason.deterministic_check_failure, "eligible": False},
        {"state": WorkerState.ready, "reason": None, "eligible": True},
        {"state": WorkerState.queued, "reason": None},
        {"state": WorkerState.running, "reason": None},
        {"state": WorkerState.blocked, "eligible": True},
        {"state": WorkerState.running, "eligible": False},
        {"reason": RepairReason.manual_retry, "eligible": True},
        {"automatic_attempt_count": MAX_AUTOMATIC_INVOCATIONS, "eligible": True},
        {"checks": check_state(head_sha=OTHER_SHA40)},
        {"updated_at": NOW - timedelta(microseconds=1)},
        {"remote_labels": (WorkerLabel.queued, WorkerLabel.queued)},
        {"remote_labels": ("gitkeeper/worker-queued",)},
    ]
    for overrides in invalid:
        with pytest.raises(ValueError):
            queue_item(**overrides)


def test_queue_item_allows_temporary_multiple_distinct_remote_label_conflicts():
    labels = (WorkerLabel.blocked, WorkerLabel.running, WorkerLabel.queued)
    assert queue_item(remote_labels=labels).remote_labels == labels


def test_queue_item_preserves_over_budget_counts_without_granting_eligibility():
    capped = queue_item(
        state=WorkerState.blocked,
        reason=RepairReason.deterministic_check_failure,
        eligible=False,
        automatic_attempt_count=MAX_AUTOMATIC_INVOCATIONS + 1,
    )
    assert capped.automatic_attempt_count == MAX_AUTOMATIC_INVOCATIONS + 1
    assert capped.eligible is False


def test_queue_events_are_transition_or_error_only_observations():
    assert queue_event().from_state is WorkerState.queued
    same_state_error = queue_event(from_state=WorkerState.blocked, to_state=WorkerState.blocked)
    assert same_state_error.error is not None
    with pytest.raises(ValueError):
        queue_event(from_state=WorkerState.blocked, to_state=WorkerState.blocked, error=None)


def test_final_invocation_event_requires_attempt_and_matching_key_cycle():
    final_attempt = attempt(outcome=AttemptOutcome.completed, started_at=NOW, finished_at=LATER, model_id=MODEL_ID)
    event = queue_event(
        from_state=WorkerState.running,
        to_state=WorkerState.ready,
        attempt_id=final_attempt.attempt_id,
        cycle_id=final_attempt.cycle_id,
        head_sha=final_attempt.key.head_sha,
        error=None,
    )
    assert event.matches_attempt(final_attempt)
    assert not event.matches_attempt(replace(final_attempt, key=queue_key(head_sha=OTHER_SHA40)))
    with pytest.raises(ValueError):
        queue_event(from_state=WorkerState.running, to_state=WorkerState.ready, error=None)
    assert not queue_event(
        from_state=WorkerState.running,
        to_state=WorkerState.ready,
        attempt_id=final_attempt.attempt_id,
        cycle_id=identifier(),
        head_sha=final_attempt.key.head_sha,
        error=None,
    ).matches_attempt(final_attempt)


def test_eligibility_accepts_only_queue_blocker_codes_and_exact_automatic_verdicts():
    blocker = ReadinessBlocker(Blocker.WORKER_DISABLED, "Worker execution is disabled.")
    assert eligibility(eligible=False, state=WorkerState.blocked, reason=None, blockers=(blocker,)).blockers == (blocker,)
    assert eligibility(reason=RepairReason.claude_feedback).eligible is True
    assert eligibility(reason=RepairReason.manual_retry, eligible=False).eligible is False
    for code in (Blocker.HOST_MODE_NOT_ACCEPTED, Blocker.CLI_MISSING, Blocker.MODEL_NOT_CONFIGURED, Blocker.BROKER_UNVERIFIED):
        with pytest.raises(ValueError):
            eligibility(eligible=False, state=WorkerState.blocked, reason=None, blockers=(ReadinessBlocker(code, "Not a queue blocker."),))
    for overrides in (
        {"eligible": True, "blockers": (blocker,)},
        {"eligible": True, "state": WorkerState.blocked, "reason": None},
        {"eligible": True, "reason": RepairReason.manual_retry},
        {"state": WorkerState.ready, "reason": RepairReason.deterministic_check_failure, "eligible": False},
        {"state": WorkerState.queued, "reason": None, "eligible": False},
        {"state": WorkerState.running, "eligible": False},
    ):
        with pytest.raises(ValueError):
            eligibility(**overrides)


def test_projection_fence_is_exact_compare_and_commit_metadata():
    assert fence().item_revision == 1
    paused = fence(paused=True, eligible=False)
    assert paused.state is WorkerState.queued
    assert paused.desired_labels == (WorkerLabel.queued,)
    ids = (identifier(), identifier())
    active = fence(active_lease_id=ids[0], active_attempt_id=ids[1], eligible=False)
    assert active.active_lease_id == ids[0]
    for overrides in (
        {"active_lease_id": identifier()},
        {"active_attempt_id": identifier()},
        {"desired_labels": (WorkerLabel.running,)},
        {"desired_labels": (WorkerLabel.queued, WorkerLabel.running)},
        {"state": WorkerState.blocked, "reason": None, "eligible": False, "desired_labels": (WorkerLabel.queued,)},
        {"latch_cleared_at": LATER},
        {"paused": 1},
    ):
        with pytest.raises(ValueError):
            fence(**overrides)
    assert fence(state=WorkerState.ready, reason=None, eligible=False, desired_labels=()).desired_labels == ()


def test_queue_view_adapter_preserves_existing_ui_names_and_error_category():
    item = queue_item(last_error=operational_error(ErrorCategory.worker_unavailable))
    view = QueueViewAdapter.from_item(item)
    assert view.current_sha == SHA40
    assert view.current_head_sha == SHA40
    assert view.check_state is CheckOutcome.passed
    assert view.last_error.category is ErrorCategory.worker_unavailable
    assert view.last_updated == NOW
    row = view.as_mapping()
    assert row["current_head_sha"] == SHA40
    assert row["check_state"] == "passed"
    assert row["last_error_category"] == "worker_unavailable"
    assert row["remote_labels"] == ["gitkeeper/worker-queued"]
    assert set(row) == {
        "repository", "pr_number", "current_head_sha", "eligible", "check_state", "state", "reason",
        "automatic_attempt_count", "manual_retry_count", "last_error_category", "remote_labels",
    }


def test_error_adapter_uses_only_fixed_category_descriptions():
    error = GitHubUnavailable()
    view = ErrorViewAdapter.from_exception(error, occurred_at=NOW)
    assert view.category is ErrorCategory.github_unavailable
    assert view.message == "GitHub is unavailable."
    assert view.occurred_at == NOW
    assert view.as_mapping() == {"category": "github_unavailable", "message": "GitHub is unavailable.", "occurred_at": NOW.isoformat()}


def test_telemetry_adapter_projects_stored_mapping_into_exact_read_only_fields():
    stored = telemetry_event(diff="DIFF-MUST-NOT-APPEAR", prompt="SECRET")
    view = TelemetryViewAdapter(stored)
    expected = {
        "timestamp": LATER,
        "duration_ms": 300000,
        "repository": REPOSITORY,
        "pr_number": 42,
        "head_sha": SHA40,
        "from_state": "running",
        "to_state": "ready",
        "attempt_count": 1,
        "outcome": "completed",
        "model_id": MODEL_ID,
        "error_category": None,
    }
    assert isinstance(view, Mapping)
    assert tuple(view) == tuple(expected)
    assert dict(view) == expected
    assert view.as_mapping() == expected
    assert get_type_hints(TelemetryViewAdapter) == {
        "timestamp": datetime,
        "duration_ms": int | None,
        "repository": str,
        "pr_number": int,
        "head_sha": str,
        "from_state": str,
        "to_state": str,
        "attempt_count": int,
        "outcome": str,
        "model_id": str | None,
        "error_category": str | None,
    }
    stored["repository"] = "Other/repository"
    stored["timestamp"] = NOW
    assert view.repository == REPOSITORY
    assert view.timestamp == LATER
    with pytest.raises(TypeError):
        view["repository"] = "Other/repository"
    with pytest.raises(FrozenInstanceError):
        view.timestamp = NOW
    assert "DIFF-MUST-NOT-APPEAR" not in repr(view)
    assert "SECRET" not in repr(view)


def test_telemetry_adapter_defaults_missing_count_outcome_and_error_category():
    stored = telemetry_event()
    del stored["invocation_count"]
    del stored["outcome"]
    del stored["error_category"]
    view = TelemetryViewAdapter(stored)
    assert view.attempt_count == 0
    assert view.outcome == "unknown"
    assert view.error_category is None


def test_telemetry_adapter_preserves_prestart_invocation_count_and_utc_enums():
    local_timezone = timezone(timedelta(hours=5, minutes=30))
    stored = telemetry_event(
        attempt_id=identifier(),
        timestamp=LATER.astimezone(local_timezone),
        from_state=WorkerState.running,
        to_state=WorkerState.blocked,
        invocation_count=0,
        outcome=AttemptOutcome.model_unavailable,
        error_category=ErrorCategory.model_unavailable,
    )
    view = TelemetryViewAdapter(stored)
    assert view.timestamp == LATER
    assert view.timestamp.tzinfo is UTC
    assert view.from_state == "running"
    assert view.to_state == "blocked"
    assert view.attempt_count == 0
    assert view.outcome == "model_unavailable"
    assert view.error_category == "model_unavailable"


@pytest.mark.parametrize(
    "overrides",
    [
        {"timestamp": LATER.isoformat()},
        {"timestamp": datetime(2026, 11, 25, 12, 0)},
        {"duration_ms": True},
        {"duration_ms": -1},
        {"pr_number": True},
        {"invocation_count": True},
        {"invocation_count": -1},
        {"from_state": "SECRET"},
        {"to_state": "SECRET"},
        {"outcome": "SECRET"},
        {"model_id": "SECRET"},
        {"error_category": ""},
        {"error_category": "SECRET"},
    ],
)
def test_telemetry_adapter_rejects_invalid_projection_values(overrides):
    with pytest.raises(ValueError):
        TelemetryViewAdapter(telemetry_event(**overrides))


def test_telemetry_adapter_rejects_non_mapping_events():
    for value in (None, [], queue_event()):
        with pytest.raises(ValueError):
            TelemetryViewAdapter(value)


def test_typed_errors_fix_their_categories_and_reject_raw_data():
    expected = {
        StorageUnavailable: ErrorCategory.storage_unavailable,
        StorageCorrupt: ErrorCategory.storage_corrupt,
        SchemaMismatch: ErrorCategory.schema_mismatch,
        GitHubUnavailable: ErrorCategory.github_unavailable,
        GitHubAuthorizationError: ErrorCategory.github_authorization,
        GitHubResponseError: ErrorCategory.github_response,
        PullRequestNotFound: ErrorCategory.pull_request_not_found,
        RemoteStale: ErrorCategory.remote_stale,
        LocalLeaseConflict: ErrorCategory.local_lease_conflict,
        StaleHead: ErrorCategory.stale_head,
        RetryInvalid: ErrorCategory.retry_invalid,
        RetryExpired: ErrorCategory.retry_expired,
        RetryReplayed: ErrorCategory.retry_replayed,
        AttemptBudgetExhausted: ErrorCategory.attempt_budget_exhausted,
        ModelUnavailable: ErrorCategory.model_unavailable,
        WorkerUnavailable: ErrorCategory.worker_unavailable,
        WorkerFailed: ErrorCategory.worker_failed,
    }
    assert set(expected.values()) == set(ErrorCategory)
    for error_type, category in expected.items():
        error = error_type()
        assert isinstance(error, QueueError)
        assert error.category is category
        assert str(error) == operational_error(category).message
        with pytest.raises(TypeError):
            error_type({"response": "SECRET", "credential": "SECRET"})
        with pytest.raises(TypeError):
            error_type(RuntimeError("SECRET"))
    assert issubclass(StorageCorrupt, StorageUnavailable)
    assert issubclass(SchemaMismatch, StorageUnavailable)
    assert issubclass(PullRequestNotFound, GitHubResponseError)


def test_operational_error_rejects_raw_or_arbitrary_messages():
    category = ErrorCategory.github_response
    fixed = str(QueueError(category))
    assert OperationalError(category, fixed, NOW).message == fixed
    for message in (RuntimeError("SECRET"), {"body": "SECRET"}, "SECRET response body", "GitHub returned an invalid response. SECRET"):
        with pytest.raises(ValueError):
            OperationalError(category, message, NOW)
    with pytest.raises(ValueError):
        OperationalError("github_response", fixed, NOW)


def test_remote_failure_and_mixed_report_health_precedence():
    stale = remote_failure(operation=RemoteOperation.get_pull_request, error=operational_error(ErrorCategory.stale_head))
    offline = remote_failure(operation=RemoteOperation.list_pull_requests, pr_number=None, error=operational_error(ErrorCategory.github_authorization))
    degraded = remote_failure(operation=RemoteOperation.get_config, pr_number=None, error=operational_error(ErrorCategory.github_response))
    assert remote_report(failures=(degraded,), health=RemoteHealth.degraded).health is RemoteHealth.degraded
    assert remote_report(failures=(degraded, offline), health=RemoteHealth.offline).health is RemoteHealth.offline
    report = remote_report(failures=(degraded, offline, stale), health=RemoteHealth.stale)
    assert report.health is RemoteHealth.stale
    assert report.failures == (offline, stale, degraded)
    with pytest.raises(ValueError):
        remote_report(failures=(offline,), health=RemoteHealth.healthy)


def test_remote_reports_sort_and_deduplicate_items_and_closed_prs():
    items = (queue_item(pr_number=43), queue_item(pr_number=42))
    report = remote_report(items=items, closed_pr_numbers=(43, 41, 43))
    assert [item.pr_number for item in report.items] == [42, 43]
    assert report.closed_pr_numbers == (41, 43)
    with pytest.raises(ValueError):
        remote_report(items=(queue_item(pr_number=42), queue_item(pr_number=42)))
    with pytest.raises(ValueError):
        remote_report(items=(queue_item(repository="SumukhaK/other"),))
    with pytest.raises(ValueError):
        remote_report(closed_pr_numbers=(True,))


def test_reconcile_result_concatenates_reports_without_collapsing_failures():
    first = remote_report(repository="SumukhaK/Alpha", items=(queue_item(repository="SumukhaK/Alpha", pr_number=2),))
    second = remote_report(
        repository=REPOSITORY,
        health=RemoteHealth.offline,
        failures=(remote_failure(operation=RemoteOperation.list_pull_requests, pr_number=None),),
        items=(queue_item(pr_number=42),),
    )
    result = ReconcileResult(reports=(second, first))
    assert result.reports == (first, second)
    assert [item.pr_number for item in result.items] == [2, 42]
    assert result.remote_healthy is False
    assert result.reports[1].failures == second.failures
    assert ReconcileResult(reports=()).remote_healthy is True
    with pytest.raises(ValueError):
        ReconcileResult(reports=(first, first))
