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


def test_feedback_is_rejected_if_the_head_changes_after_the_last_refresh():
    original_item = make_item(
        current_head_sha="original-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )
    service = FakeQueueService(items={42: original_item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()
    service.items[42] = make_item(
        current_head_sha="new-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="original-sha",
    )

    assert submitted is False
    assert service.calls == []
    assert "re-associate" in view_model.feedback_error.lower()


def test_explicit_reassociation_uses_the_freshly_confirmed_sha():
    original_item = make_item(
        current_head_sha="original-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )
    service = FakeQueueService(items={42: original_item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()
    service.items[42] = make_item(
        current_head_sha="new-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="original-sha",
        reassociate=True,
    )

    assert submitted is True
    assert service.calls == [
        (
            "submit_review_feedback",
            42,
            "new-sha",
            "Please address the architecture feedback.",
        )
    ]


def test_feedback_refresh_failure_blocks_submission_and_surfaces_an_error():
    from gitkeeper.ui.errors import QueueServiceError

    item = make_item(
        current_head_sha="current-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )
    service = FakeQueueService(items={42: item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()
    service.get_item_error = QueueServiceError("remote_connectivity")

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="current-sha",
    )

    assert submitted is False
    assert service.calls == []
    assert view_model.is_stale is True
    assert "offline" in view_model.feedback_error.lower()


def test_feedback_submission_error_is_surfaced_without_raising():
    from gitkeeper.ui.errors import QueueServiceError

    item = make_item(
        current_head_sha="current-sha",
        check_state="passed",
        state="ready",
        remote_labels=[],
    )
    service = FakeQueueService(items={42: item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()
    service.submit_feedback_error = QueueServiceError("remote_connectivity")

    submitted = view_model.submit_review_feedback(
        "Please address the architecture feedback.",
        associated_head_sha="current-sha",
    )

    assert submitted is False
    assert "offline" in view_model.feedback_error.lower()


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
