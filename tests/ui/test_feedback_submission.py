from gitkeeper.ui.viewmodels.status import StatusViewModel
from tests.fakes.fake_queue_service import FakeQueueService
from tests.ui.test_status_viewmodel import make_item


def test_feedback_is_submitted_for_the_selected_current_head_sha():
    item = make_item(
        current_head_sha="current-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )
    service = FakeQueueService(items={42: item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="current-sha",
    )

    assert submitted is True
    assert service.calls == [
        (
            "submit_review_feedback",
            42,
            "current-sha",
            "Please address the architecture feedback.",
        )
    ]


def test_stale_feedback_requires_explicit_reassociation():
    item = make_item(
        current_head_sha="current-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )
    service = FakeQueueService(items={42: item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="reviewed-old-sha",
    )

    assert submitted is False
    assert service.calls == []
    assert "re-associate" in view_model.feedback_error.lower()


def test_explicit_reassociation_binds_stale_feedback_to_current_head():
    item = make_item(
        current_head_sha="current-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )
    service = FakeQueueService(items={42: item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="reviewed-old-sha",
        reassociate=True,
    )

    assert submitted is True
    assert service.calls == [
        (
            "submit_review_feedback",
            42,
            "current-sha",
            "Please address the architecture feedback.",
        )
    ]


def test_feedback_is_not_queued_before_deterministic_checks_pass():
    item = make_item(current_head_sha="current-sha")
    service = FakeQueueService(items={42: item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="current-sha",
    )

    assert submitted is False
    assert service.calls == []
    assert "checks to pass" in view_model.feedback_error.lower()
