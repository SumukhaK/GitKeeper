import pytest

from gitkeeper.host_mode.command_broker import (
    ApprovalBroker,
    ApprovalOutcome,
    ApprovalPort,
    ApprovalPortError,
    CommandRequest,
    ExecutionResult,
    RenderedRequest,
    render_request,
)

SHA = "0123456789abcdef0123456789abcdef01234567"


def request(**overrides):
    values = {
        "executable": "gradlew.bat",
        "argv": ("testDebugUnitTest", "detekt"),
        "cwd": "C:\\worktrees\\Setu-mobile\\pr-19",
        "environment_names": ("JAVA_HOME",),
        "repository": "SumukhaK/Setu-mobile",
        "pr_number": 19,
        "starting_sha": SHA,
        "purpose": "Run the documented objective checks",
    }
    values.update(overrides)
    return CommandRequest(**values)


def shown(digest, lines):
    return RenderedRequest(digest=digest, lines=tuple(lines))


def ready_view_model(**settings):
    from gitkeeper.ui.viewmodels.command_approval import CommandApprovalViewModel

    return CommandApprovalViewModel(**settings)


def present(view_model, command=None):
    command = command or request()
    render = render_request(command)
    return view_model.show(command.digest, render), command, render


def test_view_model_satisfies_the_broker_port():
    view_model = ready_view_model()

    assert isinstance(view_model, ApprovalPort)


def test_request_details_are_shown_as_plain_text():
    view_model = ready_view_model()

    rendered, command, lines = present(view_model)

    assert rendered == shown(command.digest, lines)
    assert view_model.request_lines == list(lines)
    assert view_model.request_digest == command.digest
    assert view_model.shown_digest == command.digest
    assert view_model.executable == "gradlew.bat"
    assert view_model.repository == "SumukhaK/Setu-mobile"
    assert view_model.pr_number == 19
    assert view_model.purpose == "Run the documented objective checks"


def test_every_rendered_line_reaches_the_owner_verbatim():
    view_model = ready_view_model()
    command = request(
        script="gradlew.bat testDebugUnitTest\r\ndetekt\r\nlint", argv=()
    )

    present(view_model, command)

    assert view_model.request_lines == list(render_request(command))
    assert any("detekt" in line for line in view_model.request_lines)
    assert any("lint" in line for line in view_model.request_lines)


def test_a_pending_request_starts_in_the_pending_state():
    view_model = ready_view_model()

    present(view_model)

    assert view_model.pending is True
    assert view_model.status == "pending"
    assert "review" in view_model.status_message.lower()


def test_approval_requires_an_explicit_action():
    view_model = ready_view_model()

    present(view_model)

    assert view_model.pending is True
    assert view_model.wait(0.01) is None
    assert view_model.status == "timed-out"


def test_no_answer_denies_and_never_reports_success():
    view_model = ready_view_model()
    present(view_model)

    answer = view_model.wait(0.01)

    assert answer is None
    assert view_model.pending is False
    assert view_model.status == "timed-out"
    assert view_model.status_message == (
        "No answer arrived, so the command was not run."
    )


def test_approve_queues_an_approval_without_claiming_the_command_ran():
    view_model = ready_view_model()
    present(view_model)

    assert view_model.approve() is True

    assert view_model.wait(0.01) == "approve"
    assert view_model.status == "queued-for-execution"
    assert view_model.pending is False
    assert view_model.status_message == (
        "Approved once. Waiting for the worker to report the result."
    )


def test_deny_queues_a_denial():
    view_model = ready_view_model()
    present(view_model)

    assert view_model.deny() is True

    assert view_model.wait(0.01) == "deny"
    assert view_model.status == "denied"
    assert view_model.status_message == "Denied. The command was not run."


def test_approve_without_a_pending_request_does_nothing():
    view_model = ready_view_model()

    assert view_model.approve() is False

    assert view_model.status == "idle"
    assert view_model.pending is False
    assert view_model.wait(0.01) is None


def test_a_decision_cannot_be_changed_or_repeated():
    view_model = ready_view_model()
    present(view_model)

    assert view_model.approve() is True
    assert view_model.deny() is False
    assert view_model.approve() is False

    assert view_model.wait(0.01) == "approve"


def test_a_second_request_cannot_overwrite_a_pending_one():
    view_model = ready_view_model()
    present(view_model)

    with pytest.raises(ApprovalPortError):
        present(view_model, request(purpose="Run something else entirely"))

    assert view_model.request_digest == request().digest


def test_an_empty_presentation_is_refused():
    view_model = ready_view_model()

    with pytest.raises(ApprovalPortError):
        view_model.show(request().digest, [])

    with pytest.raises(ApprovalPortError):
        view_model.show("", ("Executable: gradlew.bat",))

    with pytest.raises(ApprovalPortError):
        view_model.show(request().digest, "Executable: gradlew.bat")


def test_a_new_request_after_a_decision_starts_a_fresh_review():
    view_model = ready_view_model()
    present(view_model)
    view_model.deny()
    view_model.wait(0.01)

    present(view_model, request(purpose="Re-run after the escalation was cleared"))

    assert view_model.pending is True
    assert view_model.status == "pending"
    assert view_model.shown_digest == request(
        purpose="Re-run after the escalation was cleared"
    ).digest


def test_status_is_described_for_screen_readers():
    view_model = ready_view_model()

    present(view_model)

    description = view_model.accessibility_description
    assert "Approve" in description
    assert "Deny" in description
    assert "SumukhaK/Setu-mobile" in description
    assert "#19" in description
    assert request().digest in description
    assert "Review" in description


def test_status_message_announces_state_changes():
    view_model = ready_view_model()
    changes = []
    view_model.changed.connect(lambda: changes.append(view_model.status))
    present(view_model)

    view_model.approve()
    view_model.wait(0.01)

    assert changes[0] == "pending"
    assert "queued-for-execution" in changes


def test_controls_are_always_available_and_labelled():
    view_model = ready_view_model()

    assert view_model.controls_enabled is True
    assert view_model.approve_label == "Approve once"
    assert view_model.deny_label == "Deny"


def test_reduced_motion_keeps_the_controls_and_details():
    view_model = ready_view_model()
    present(view_model)
    before = list(view_model.request_lines)

    view_model.set_motion_preference("reduced")

    assert view_model.animation_enabled is False
    assert view_model.controls_enabled is True
    assert view_model.request_lines == before
    assert view_model.approve() is True


def test_disabled_motion_keeps_the_controls_and_details():
    view_model = ready_view_model()
    present(view_model)
    before = list(view_model.request_lines)

    view_model.set_motion_preference("disabled")

    assert view_model.animation_enabled is False
    assert view_model.controls_enabled is True
    assert view_model.request_lines == before
    assert view_model.deny() is True


def test_default_motion_allows_animation():
    view_model = ready_view_model()

    assert view_model.animation_enabled is True
    assert view_model.motion_preference == ""

    view_model.set_motion_preference("full")

    assert view_model.animation_enabled is True
    assert view_model.motion_preference == "full"


def test_an_unknown_motion_preference_is_refused():
    view_model = ready_view_model()

    with pytest.raises(ValueError):
        view_model.set_motion_preference("spinning")


def test_execution_outcome_is_reported_after_the_broker_confirms_it():
    view_model = ready_view_model()
    present(view_model)
    view_model.approve()
    view_model.wait(0.01)

    assert view_model.record_execution(ExecutionResult(exit_code=0)) is True
    assert view_model.status == "completed"
    assert view_model.status_message == "Finished with exit code 0."

    present(view_model, request(purpose="Second command"))
    view_model.approve()
    view_model.wait(0.01)

    assert view_model.record_execution(ExecutionResult(exit_code=2)) is True
    assert view_model.status == "failed"
    assert view_model.status_message == "Failed with exit code 2."


def test_execution_outcome_before_approval_is_refused():
    view_model = ready_view_model()
    present(view_model)

    assert view_model.record_execution(ExecutionResult(exit_code=0)) is False
    assert view_model.status == "pending"


def test_reset_clears_the_review_without_deciding_it():
    view_model = ready_view_model()
    present(view_model)

    view_model.reset()

    assert view_model.pending is False
    assert view_model.status == "idle"
    assert view_model.request_lines == []
    assert view_model.request_digest == ""
    assert view_model.shown_digest == ""


def test_the_companion_never_reports_an_unavailable_command_as_approved():
    from gitkeeper.ui.viewmodels.command_approval import CommandApprovalViewModel

    view_model = CommandApprovalViewModel(broker_available=False)
    present(view_model)

    assert view_model.approve() is False
    assert view_model.status == "unavailable"
    assert view_model.status_message == (
        "Command approval is unavailable; nothing was run."
    )
    assert view_model.wait(0.01) is None


def test_the_view_model_and_broker_agree_on_one_approved_command():
    view_model = ready_view_model()
    executed = []
    broker = ApprovalBroker(
        ApprovingPort(view_model),
        facts=_facts,
        executor=lambda command: executed.append(command) or ExecutionResult(0),
        timeout_seconds=1.0,
    )
    command = request()

    decision = broker.request_approval(command)
    assert decision.outcome is ApprovalOutcome.APPROVED
    assert executed == []
    assert view_model.status == "queued-for-execution"

    broker.execute_approved(command, decision)

    assert executed == [command]
    assert decision.audit_metadata()["outcome"] == "approved"


class ApprovingPort:
    """A stand-in for the owner clicking Approve while the broker waits."""

    def __init__(self, view_model):
        self._view_model = view_model

    def show(self, digest, lines):
        return self._view_model.show(digest, lines)

    def wait(self, timeout_seconds):
        self._view_model.approve()
        return self._view_model.wait(timeout_seconds)


def _facts():
    from gitkeeper.host_mode.readiness import PullRequestFacts, RepairReason

    return PullRequestFacts(
        repository="SumukhaK/Setu-mobile",
        number=19,
        head_repository="SumukhaK/Setu-mobile",
        author="SumukhaK",
        head_sha=SHA,
        check_head_sha=SHA,
        is_open=True,
        is_draft=False,
        repair_reason=RepairReason.DETERMINISTIC_CHECK_FAILURE,
        automatic_invocations_used=0,
    )


def test_a_broker_run_through_the_view_model_exposes_the_approval_metrics():
    view_model = ready_view_model()
    broker = ApprovalBroker(
        ApprovingPort(view_model),
        facts=_facts,
        executor=lambda command: ExecutionResult(0),
        timeout_seconds=1.0,
    )

    decision = broker.request_approval(request())

    assert decision.outcome is ApprovalOutcome.APPROVED
    assert decision.audit_metadata()["pr_number"] == 19
    assert view_model.status == "queued-for-execution"


def test_execution_result_with_wrong_digest_cannot_complete_new_approval():
    view_model = ready_view_model()
    first = request(purpose="First command")
    present(view_model, first)
    view_model.approve()
    second = request(purpose="Second command")
    present(view_model, second)
    view_model.approve()

    assert (
        view_model.record_execution(
            ExecutionResult(0), request_digest=first.digest
        )
        is False
    )
    assert view_model.status == "queued-for-execution"
    assert (
        view_model.record_execution(
            ExecutionResult(0), request_digest=second.digest
        )
        is True
    )
    assert view_model.status == "completed"


def test_legacy_one_argument_execution_reporting_still_works():
    view_model = ready_view_model()
    present(view_model)
    view_model.approve()

    assert view_model.record_execution(ExecutionResult(0)) is True
    assert view_model.status == "completed"