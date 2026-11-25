"""Behavioural spec for the one-shot command approval broker.

Every shell request the worker makes must be reviewed as an exact, immutable
command and approved once by the owner. These tests fix the contract: what may
be displayed, what counts as an approval, and every path that must refuse.
"""

import pytest

from gitkeeper.host_mode.readiness import PullRequestFacts, RepairReason

SHA = "a153271a18241b3cf5b9d5ed7025916c6b11e9f8"
OTHER_SHA = "6af92839670b580e22ca17e7dc4150b51455a8cb"
CWD = r"C:\GitKeeper\worktrees\setu-mobile-pr-19"


def facts(**overrides):
    values = {
        "repository": "SumukhaK/Setu-mobile",
        "number": 19,
        "head_repository": "SumukhaK/Setu-mobile",
        "author": "SumukhaK",
        "head_sha": SHA,
        "check_head_sha": SHA,
        "is_open": True,
        "is_draft": False,
        "repair_reason": RepairReason.DETERMINISTIC_CHECK_FAILURE,
        "automatic_invocations_used": 1,
    }
    values.update(overrides)
    return PullRequestFacts(**values)


def argv_request(**overrides):
    from gitkeeper.host_mode.command_broker import CommandRequest

    values = {
        "executable": "rg",
        "argv": ("--files", "app/src"),
        "cwd": CWD,
        "environment_names": ("PATH",),
        "repository": "SumukhaK/Setu-mobile",
        "pr_number": 19,
        "starting_sha": SHA,
        "purpose": "locate Gradle modules",
    }
    values.update(overrides)
    return CommandRequest(**values)


def script_request(**overrides):
    from gitkeeper.host_mode.command_broker import CommandRequest

    values = {
        "executable": "cmd.exe",
        "script": "gradlew.bat testDebugUnitTest\r\n",
        "cwd": CWD,
        "environment_names": ("PATH", "JAVA_HOME"),
        "repository": "SumukhaK/Setu-mobile",
        "pr_number": 19,
        "starting_sha": SHA,
        "purpose": "run Android unit tests",
    }
    values.update(overrides)
    return CommandRequest(**values)


class FakePort:
    """A stand-in for the companion approval dialog."""

    def __init__(self, response="approve", *, rendered=None, show_error=None):
        self.response = response
        self.rendered = rendered
        self.show_error = show_error
        self.shown = []
        self.waited = []

    def show(self, digest, lines):
        from gitkeeper.host_mode.command_broker import RenderedRequest

        self.shown.append((digest, lines))
        if self.show_error is not None:
            raise self.show_error
        if self.rendered is not None:
            return self.rendered
        return RenderedRequest(digest=digest, lines=lines)

    def wait(self, timeout_seconds):
        self.waited.append(timeout_seconds)
        return self.response


class FakeExecutor:
    def __init__(self, *, error=None):
        self.requests = []
        self.error = error

    def __call__(self, request):
        from gitkeeper.host_mode.command_broker import ExecutionResult

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ExecutionResult(exit_code=0)


class SteppingFacts:
    """Returns a queued sequence of GitHub snapshots, then repeats the last."""

    def __init__(self, *snapshots):
        self._snapshots = list(snapshots)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        index = min(self.calls - 1, len(self._snapshots) - 1)
        return self._snapshots[index]


class ManualClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_broker(port, *, snapshots=None, executor=None, clock=None, **options):
    from gitkeeper.host_mode.command_broker import ApprovalBroker

    if snapshots is None:
        facts_source = SteppingFacts(facts())
    elif callable(snapshots):
        facts_source = snapshots
    else:
        facts_source = SteppingFacts(*snapshots)
    return ApprovalBroker(
        port,
        facts=facts_source,
        executor=executor if executor is not None else FakeExecutor(),
        clock=clock if clock is not None else ManualClock(),
        **options,
    )


def approve(broker, request):
    decision = broker.request_approval(request)
    assert decision.approved is True
    return decision


def outcome(decision):
    return decision.outcome.value


# --- request shape ---------------------------------------------------------


def test_argv_request_serializes_to_a_stable_digest():
    assert argv_request().digest == argv_request().digest


def test_digest_changes_when_any_reviewed_value_changes():
    base = argv_request().digest
    variants = [
        argv_request(argv=("--files", "app")),
        argv_request(cwd=r"C:\other"),
        argv_request(repository="SumukhaK/setu-mobile"),
        argv_request(pr_number=20),
        argv_request(starting_sha=OTHER_SHA),
        argv_request(environment_names=("PATH", "JAVA_HOME")),
        argv_request(purpose="something else"),
    ]
    digests = [variant.digest for variant in variants]
    assert base not in digests
    assert len(set(digests)) == len(digests)


def test_environment_name_order_does_not_change_the_digest():
    first = script_request(environment_names=("PATH", "JAVA_HOME")).digest
    second = script_request(environment_names=("JAVA_HOME", "PATH")).digest

    assert first == second


def test_request_has_no_field_for_environment_values():
    with pytest.raises(TypeError):
        argv_request(environment_values=("token=abc",))


@pytest.mark.parametrize(
    "overrides",
    [
        {"executable": ""},
        {"executable": "   "},
        {"executable": "rg\nrm -rf /"},
        {"argv": (), "script": None},
        {"argv": ("--files",), "script": "echo hi"},
        {"argv": ("--files\nrm",)},
        {"argv": (1,)},
        {"cwd": ""},
        {"cwd": "worktrees/pr-19"},
        {"repository": "setu-mobile"},
        {"repository": ""},
        {"pr_number": 0},
        {"pr_number": True},
        {"starting_sha": "a153271"},
        {"starting_sha": SHA.upper()},
        {"purpose": ""},
        {"purpose": "line one\nline two"},
        {"environment_names": ("PATH", "PATH")},
        {"environment_names": ("",)},
    ],
)
def test_malformed_requests_are_rejected(overrides):
    from gitkeeper.host_mode.command_broker import CommandRequestError

    with pytest.raises(CommandRequestError):
        argv_request(**overrides)


# --- presentation ----------------------------------------------------------


def test_render_shows_arguments_environment_names_and_digest():
    from gitkeeper.host_mode.command_broker import render_request

    request = argv_request()
    lines = render_request(request)
    rendered = "\n".join(lines)

    assert "rg" in rendered
    assert "--files" in rendered
    assert "app/src" in rendered
    assert "PATH" in rendered
    assert CWD in rendered
    assert "SumukhaK/Setu-mobile" in rendered
    assert "#19" in rendered
    assert SHA in rendered
    assert request.digest in rendered
    assert "token" not in rendered.lower()


def test_render_prints_a_shell_script_line_by_line():
    from gitkeeper.host_mode.command_broker import render_request

    lines = render_request(
        script_request(script="gradlew.bat testDebugUnitTest\ndetekt")
    )

    assert any("gradlew.bat testDebugUnitTest" in line for line in lines)
    assert any(line.rstrip().endswith("| detekt") for line in lines)


# --- approval paths --------------------------------------------------------


def test_approved_request_executes_exactly_once():
    executor = FakeExecutor()
    port = FakePort()
    broker = make_broker(port, executor=executor)
    request = argv_request()

    decision = broker.request_approval(request)
    result = broker.execute_approved(request, decision)

    assert decision.approved is True
    assert result.exit_code == 0
    assert executor.requests == [request]
    assert port.shown[0][0] == request.digest
    assert port.waited == [120.0]


def test_denied_request_never_executes():
    from gitkeeper.host_mode.command_broker import ApprovalNotApprovedError

    executor = FakeExecutor()
    broker = make_broker(FakePort(response="deny"), executor=executor)
    request = argv_request()

    decision = broker.request_approval(request)

    assert outcome(decision) == "denied"
    assert decision.approved is False
    with pytest.raises(ApprovalNotApprovedError):
        broker.execute_approved(request, decision)
    assert executor.requests == []


def test_no_response_times_out_and_never_executes():
    from gitkeeper.host_mode.command_broker import ApprovalNotApprovedError

    executor = FakeExecutor()
    broker = make_broker(FakePort(response=None), executor=executor)
    request = argv_request()

    decision = broker.request_approval(request)

    assert outcome(decision) == "timeout"
    with pytest.raises(ApprovalNotApprovedError):
        broker.execute_approved(request, decision)
    assert executor.requests == []


def test_disconnected_ui_denies_and_never_executes():
    from gitkeeper.host_mode.command_broker import (
        ApprovalNotApprovedError,
        ApprovalPortError,
    )

    executor = FakeExecutor()
    port = FakePort(show_error=ApprovalPortError("dialog closed"))
    broker = make_broker(port, executor=executor)
    request = argv_request()

    decision = broker.request_approval(request)

    assert outcome(decision) == "disconnected"
    with pytest.raises(ApprovalNotApprovedError):
        broker.execute_approved(request, decision)
    assert executor.requests == []


def test_unexpected_presenter_failure_still_denies():
    executor = FakeExecutor()
    broker = make_broker(FakePort(show_error=RuntimeError("boom")), executor=executor)
    request = argv_request()

    decision = broker.request_approval(request)

    assert decision.approved is False
    assert executor.requests == []


@pytest.mark.parametrize("response", ["approve!", "", "yes", "Approved", 1, True])
def test_unrecognised_answers_are_treated_as_denials(response):
    broker = make_broker(FakePort(response=response))
    request = argv_request()

    decision = broker.request_approval(request)

    assert decision.approved is False
    assert outcome(decision) == "denied"


def test_partial_display_is_not_an_approval():
    from gitkeeper.host_mode.command_broker import RenderedRequest

    request = argv_request()
    truncated = RenderedRequest(digest=request.digest, lines=("Executable: rg",))
    executor = FakeExecutor()
    broker = make_broker(FakePort(rendered=truncated), executor=executor)

    decision = broker.request_approval(request)

    assert outcome(decision) == "incomplete-display"
    assert decision.approved is False
    assert executor.requests == []


def test_display_reporting_a_different_digest_is_not_an_approval():
    from gitkeeper.host_mode.command_broker import RenderedRequest, render_request

    request = argv_request()
    mismatched = RenderedRequest(
        digest="0" * 64, lines=render_request(request)
    )
    broker = make_broker(FakePort(rendered=mismatched))

    decision = broker.request_approval(request)

    assert outcome(decision) == "incomplete-display"
    assert decision.approved is False


def test_display_that_is_not_a_rendered_request_is_not_an_approval():
    broker = make_broker(FakePort(rendered="done"))

    decision = broker.request_approval(argv_request())

    assert outcome(decision) == "incomplete-display"
    assert decision.approved is False


# --- staleness -------------------------------------------------------------


@pytest.mark.parametrize(
    "changed",
    [
        {"head_sha": OTHER_SHA},
        {"number": 20},
        {"repository": "SumukhaK/GitKeeper"},
        {"is_open": False},
        {"is_draft": True},
    ],
)
def test_changed_remote_state_during_review_denies(changed):
    from gitkeeper.host_mode.command_broker import (
        ApprovalNotApprovedError,
        RenderedRequest,
        render_request,
    )

    request = argv_request()
    port = FakePort(
        rendered=RenderedRequest(
            digest=request.digest,
            lines=render_request(request),
        )
    )
    executor = FakeExecutor()
    broker = make_broker(port, snapshots=[facts(), facts(**changed)], executor=executor)

    decision = broker.request_approval(request)

    assert outcome(decision) == "stale-request"
    assert decision.approved is False
    with pytest.raises(ApprovalNotApprovedError):
        broker.execute_approved(request, decision)
    assert executor.requests == []


def test_stale_state_at_execution_time_is_refused():
    from gitkeeper.host_mode.command_broker import StaleApprovalError

    executor = FakeExecutor()
    broker = make_broker(
        FakePort(),
        snapshots=[facts(), facts(), facts(head_sha=OTHER_SHA)],
        executor=executor,
    )
    request = argv_request()

    decision = approve(broker, request)
    with pytest.raises(StaleApprovalError):
        broker.execute_approved(request, decision)
    assert executor.requests == []


# --- execution binding -----------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"argv": ("--files", "app")},
        {"cwd": r"C:\other"},
        {"repository": "SumukhaK/GitKeeper"},
        {"pr_number": 20},
        {"starting_sha": OTHER_SHA},
        {"environment_names": ("PATH", "JAVA_HOME")},
    ],
)
def test_changed_request_cannot_reuse_an_approval(overrides):
    from gitkeeper.host_mode.command_broker import ApprovalMismatchError

    broker = make_broker(FakePort())
    request = argv_request()
    decision = approve(broker, request)

    changed = argv_request(**overrides)
    assert changed.digest != request.digest

    with pytest.raises(ApprovalMismatchError):
        broker.execute_approved(changed, decision)


def test_equivalent_request_value_may_use_the_approval():
    broker = make_broker(FakePort())
    decision = approve(broker, argv_request())

    result = broker.execute_approved(argv_request(), decision)

    assert result.exit_code == 0


def test_an_approval_cannot_be_replayed():
    from gitkeeper.host_mode.command_broker import ApprovalReplayError

    executor = FakeExecutor()
    broker = make_broker(FakePort(), executor=executor)
    request = argv_request()
    decision = approve(broker, request)

    broker.execute_approved(request, decision)
    with pytest.raises(ApprovalReplayError):
        broker.execute_approved(request, decision)
    assert executor.requests == [request]


def test_executor_failure_is_not_retried_automatically():
    from gitkeeper.host_mode.command_broker import ApprovalReplayError

    executor = FakeExecutor(error=RuntimeError("opencode crashed"))
    broker = make_broker(FakePort(), executor=executor)
    request = argv_request()
    decision = approve(broker, request)

    with pytest.raises(RuntimeError):
        broker.execute_approved(request, decision)
    with pytest.raises(ApprovalReplayError):
        broker.execute_approved(request, decision)
    assert len(executor.requests) == 1


def test_expired_approval_is_refused():
    from gitkeeper.host_mode.command_broker import ApprovalExpiredError

    clock = ManualClock()
    executor = FakeExecutor()
    broker = make_broker(
        FakePort(), executor=executor, clock=clock, token_ttl_seconds=300.0
    )
    request = argv_request()
    decision = approve(broker, request)

    clock.advance(301.0)

    with pytest.raises(ApprovalExpiredError):
        broker.execute_approved(request, decision)
    assert executor.requests == []


def test_approval_within_the_ttl_still_executes():
    clock = ManualClock()
    broker = make_broker(FakePort(), clock=clock, token_ttl_seconds=300.0)
    request = argv_request()
    decision = approve(broker, request)

    clock.advance(299.0)

    assert broker.execute_approved(request, decision).exit_code == 0


def test_default_executor_is_disabled_until_mediation_is_proven():
    from gitkeeper.host_mode.command_broker import (
        ApprovalBroker,
        ExecutionUnavailableError,
    )

    port = FakePort()
    broker = ApprovalBroker(port, facts=SteppingFacts(facts()))
    request = argv_request()
    decision = approve(broker, request)

    with pytest.raises(ExecutionUnavailableError) as error:
        broker.execute_approved(request, decision)

    assert "mediation" in str(error.value)


def test_executor_receives_the_reviewed_request_object_unchanged():
    executor = FakeExecutor()
    broker = make_broker(FakePort(), executor=executor)
    request = script_request()

    decision = approve(broker, request)
    broker.execute_approved(request, decision)

    sent = executor.requests[0]
    assert sent is request
    assert sent.script == "gradlew.bat testDebugUnitTest\r\n"


# --- argument typing -------------------------------------------------------


def test_broker_requires_a_facts_provider():
    from gitkeeper.host_mode.command_broker import ApprovalBroker

    with pytest.raises(TypeError):
        ApprovalBroker(FakePort(), facts="main")


def test_broker_refuses_non_requests():
    broker = make_broker(FakePort())

    with pytest.raises(TypeError):
        broker.request_approval({"executable": "rg"})
    with pytest.raises(TypeError):
        broker.request_approval("rg --files")


def test_execute_refuses_non_decisions():
    broker = make_broker(FakePort())

    with pytest.raises(TypeError):
        broker.execute_approved(argv_request(), True)


def test_a_hand_built_approved_decision_cannot_bypass_review():
    from gitkeeper.host_mode.command_broker import (
        ApprovalDecision,
        ApprovalOutcome,
        UnrecognisedApprovalError,
    )

    clock = ManualClock()
    broker = make_broker(FakePort(), clock=clock)
    request = argv_request()
    forged = ApprovalDecision(
        decision_id="forged",
        request=request,
        request_digest=request.digest,
        outcome=ApprovalOutcome.APPROVED,
        issued_at=clock(),
        decided_at=clock(),
    )

    with pytest.raises(UnrecognisedApprovalError):
        broker.execute_approved(request, forged)


def test_a_decision_from_another_broker_cannot_execute():
    from gitkeeper.host_mode.command_broker import UnrecognisedApprovalError

    request = argv_request()
    first = make_broker(FakePort())
    second = make_broker(FakePort())

    decision = approve(first, request)

    with pytest.raises(UnrecognisedApprovalError):
        second.execute_approved(request, decision)


# --- audit metadata --------------------------------------------------------


def test_audit_metadata_never_contains_the_script_arguments_or_env_names():
    broker = make_broker(FakePort())
    request = script_request()

    decision = broker.request_approval(request)
    metadata = decision.audit_metadata()

    assert metadata["request_digest"] == request.digest
    assert metadata["outcome"] == "approved"
    assert metadata["purpose"] == "run Android unit tests"
    assert metadata["repository"] == "SumukhaK/Setu-mobile"
    assert metadata["pr_number"] == 19
    flattened = repr(metadata)
    assert "gradlew" not in flattened
    assert "JAVA_HOME" not in flattened
    assert CWD not in flattened


def test_denied_audit_metadata_records_the_outcome_without_the_command():
    broker = make_broker(FakePort(response="deny"))

    metadata = broker.request_approval(argv_request()).audit_metadata()

    assert metadata["outcome"] == "denied"
    assert "app/src" not in repr(metadata)

def test_a_fork_head_is_never_approved_or_executed():
    from gitkeeper.host_mode.command_broker import (
        ApprovalNotApprovedError,
        ApprovalOutcome,
    )

    executor = FakeExecutor()
    fork_facts = facts(head_repository="someone-else/Setu-mobile")
    broker = make_broker(FakePort(), snapshots=[fork_facts], executor=executor)
    request = argv_request()

    decision = broker.request_approval(request)

    assert decision.outcome is ApprovalOutcome.STALE_REQUEST
    assert decision.approved is False
    with pytest.raises(ApprovalNotApprovedError):
        broker.execute_approved(request, decision)
    assert executor.requests == []