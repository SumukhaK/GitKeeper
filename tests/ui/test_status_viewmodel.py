from datetime import datetime, timezone
from types import SimpleNamespace


def make_item(**overrides):
    values = {
        "repository": "SumukhaK/Setu-mobile",
        "pr_number": 42,
        "current_head_sha": "abc123",
        "eligible": True,
        "check_state": "deterministic_failure",
        "state": "queued",
        "remote_labels": ["gitkeeper/worker-queued"],
        "last_updated": datetime(2026, 11, 20, tzinfo=timezone.utc),
        "last_error": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_refresh_loads_selected_pr_status():
    from gitkeeper.ui.viewmodels.status import StatusViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    item = make_item()
    view_model = StatusViewModel(FakeQueueService(items={42: item}), pr_number=42)

    view_model.refresh()

    assert view_model.repository == "SumukhaK/Setu-mobile"
    assert view_model.pr_number == 42
    assert view_model.current_sha == "abc123"
    assert view_model.eligibility is True
    assert view_model.ci_result == "deterministic_failure"
    assert view_model.remote_state == "gitkeeper/worker-queued"
    assert view_model.worker_state == "queued"
    assert view_model.last_updated == item.last_updated.isoformat()
    assert view_model.is_stale is False
    assert view_model.error_message == ""


def test_refresh_without_selected_item_exposes_no_data_state():
    from gitkeeper.ui.viewmodels.status import StatusViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    view_model = StatusViewModel(FakeQueueService(), pr_number=42)

    view_model.refresh()

    assert view_model.current_sha == ""
    assert view_model.is_stale is False
    assert view_model.error_message == ""


def test_remote_error_preserves_last_status_and_marks_it_stale():
    from gitkeeper.ui.errors import QueueServiceError
    from gitkeeper.ui.viewmodels.status import StatusViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    item = make_item()
    service = FakeQueueService(items={42: item})
    view_model = StatusViewModel(service, pr_number=42)
    view_model.refresh()
    service.get_item_error = QueueServiceError("remote_connectivity")

    view_model.refresh()

    assert view_model.current_sha == "abc123"
    assert view_model.is_stale is True
    assert view_model.error_message == "GitHub status is offline; showing the last known state."


def test_unknown_check_state_is_not_rendered_as_remote_markup():
    from gitkeeper.ui.viewmodels.status import StatusViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    item = make_item(check_state="<b>ready</b>")
    view_model = StatusViewModel(FakeQueueService(items={42: item}), pr_number=42)

    view_model.refresh()

    assert view_model.ci_result == "unknown"
