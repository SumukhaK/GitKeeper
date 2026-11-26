import ast
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from gitkeeper.host_mode.readiness import (
    PullRequestFacts,
    RepairReason as ReadinessRepairReason,
    WorkerConfiguration,
)
from gitkeeper.queue.models import CheckState, RepairReason
from gitkeeper.queue.ports import (
    FeedbackBinding,
    FeedbackPayload,
    FeedbackPort,
    QueueLaunchFacts,
)

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"


def _payload(**overrides) -> FeedbackPayload:
    values = {
        "repository": _REPOSITORY,
        "pr_number": 42,
        "head_sha": _SHA,
        "cycle_id": str(uuid4()),
        "text": "Please fix the failing test.",
        "created_at": _AT,
    }
    values.update(overrides)
    return FeedbackPayload(**values)


def test_feedback_payload_validates_identity_and_text() -> None:
    payload = _payload()
    assert payload.repository == _REPOSITORY
    assert payload.text == "Please fix the failing test."

    for overrides in (
        {"repository": "not-a-repository"},
        {"pr_number": 0},
        {"head_sha": "not-a-sha"},
        {"cycle_id": "not-a-uuid"},
        {"text": "   "},
        {"text": "x" * 16_385},
        {"text": "bad\x00text"},
        {"created_at": datetime(2026, 11, 26, 12, 0)},
    ):
        with pytest.raises((TypeError, ValueError)):
            _payload(**overrides)


def test_feedback_binding_validates_the_attempt() -> None:
    binding = FeedbackBinding(
        feedback_id=str(uuid4()),
        repository=_REPOSITORY,
        pr_number=42,
        head_sha=_SHA,
        cycle_id=str(uuid4()),
        text="Feedback.",
        created_at=_AT,
        attempt_id=None,
    )
    assert binding.attempt_id is None

    with pytest.raises(ValueError):
        FeedbackBinding(
            feedback_id=str(uuid4()),
            repository=_REPOSITORY,
            pr_number=42,
            head_sha=_SHA,
            cycle_id=str(uuid4()),
            text="Feedback.",
            created_at=_AT,
            attempt_id="not-a-uuid",
        )


def test_queue_launch_facts_accepts_typed_values() -> None:
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
    checks = CheckState(
        head_sha=_SHA,
        required_check_ids=("lint",),
        results=(),
    )

    launch = QueueLaunchFacts(
        pull_request=facts,
        configuration=WorkerConfiguration(worker_enabled=True, merged=True),
        checks=checks,
        repair_reason=RepairReason.deterministic_check_failure,
    )

    assert launch.pull_request is facts
    assert launch.checks is checks
    assert launch.repair_reason is RepairReason.deterministic_check_failure


def test_queue_launch_facts_rejects_wrong_types() -> None:
    with pytest.raises(TypeError):
        QueueLaunchFacts(
            pull_request=object(),
            configuration=object(),
            checks=object(),
            repair_reason=object(),
        )


def test_feedback_port_is_runtime_checkable() -> None:
    class FakePort:
        def put(self, payload):
            return payload

        def bind_for_claim(self, claim):
            return None

        def list_for_cycle(self, repository, pr_number, cycle_id):
            return ()

        def close(self):
            return None

    assert isinstance(FakePort(), FeedbackPort)


def test_queue_ports_do_not_import_worker() -> None:
    source = Path(
        __import__("gitkeeper.queue.ports", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    modules: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)

    assert all(not module.startswith("gitkeeper.worker") for module in modules)
