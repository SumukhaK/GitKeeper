from types import SimpleNamespace


def make_item(pr_number):
    return SimpleNamespace(
        repository="SumukhaK/Setu-mobile",
        pr_number=pr_number,
        current_head_sha=f"sha-{pr_number}",
        state="queued",
        reason="deterministic_check_failure",
        automatic_attempt_count=1,
        last_error=None,
    )


def test_refresh_lists_queue_items():
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    items = [make_item(42), make_item(43)]
    view_model = QueueViewModel(FakeQueueService(queue_items=items))

    view_model.refresh()

    assert view_model.items == [
        {
            "repository": "SumukhaK/Setu-mobile",
            "pr_number": 42,
            "current_head_sha": "sha-42",
            "eligible": None,
            "check_state": None,
            "state": "queued",
            "reason": "deterministic_check_failure",
            "automatic_attempt_count": 1,
            "manual_retry_count": 0,
            "last_error_category": None,
            "remote_labels": [],
        },
        {
            "repository": "SumukhaK/Setu-mobile",
            "pr_number": 43,
            "current_head_sha": "sha-43",
            "eligible": None,
            "check_state": None,
            "state": "queued",
            "reason": "deterministic_check_failure",
            "automatic_attempt_count": 1,
            "manual_retry_count": 0,
            "last_error_category": None,
            "remote_labels": [],
        },
    ]
    assert view_model.is_stale is False
    assert view_model.error_message == ""


def test_refresh_error_keeps_last_queue_snapshot_and_marks_it_stale():
    from gitkeeper.ui.errors import QueueServiceError
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    items = [make_item(42)]
    service = FakeQueueService(queue_items=items)
    view_model = QueueViewModel(service)
    view_model.refresh()
    service.list_queue_error = QueueServiceError("remote_connectivity")

    view_model.refresh()

    assert view_model.items[0]["pr_number"] == 42
    assert view_model.is_stale is True
    assert view_model.error_message == "GitHub queue is offline; showing the last known state."


def test_controls_call_only_the_queue_service_contract_methods():
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    service = FakeQueueService(queue_items=[make_item(42)])
    view_model = QueueViewModel(service)
    view_model.refresh()

    view_model.pause()
    assert view_model.is_paused is True
    view_model.resume()
    assert view_model.is_paused is False
    view_model.cancel(42)
    view_model.retry(42)

    assert service.calls == [
        ("pause",),
        ("resume",),
        ("cancel", 42),
        ("retry", 42),
    ]


def test_failed_pause_is_surfaced_without_showing_paused_state():
    from gitkeeper.ui.errors import QueueServiceError
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    service = FakeQueueService()
    view_model = QueueViewModel(service)
    view_model.refresh()
    service.pause_error = QueueServiceError("remote_connectivity")

    succeeded = view_model.pause()

    assert succeeded is False
    assert view_model.is_paused is False
    assert view_model.is_stale is True
    assert "offline" in view_model.error_message.lower()


def test_pause_state_changes_only_after_a_queue_status_refresh():
    from gitkeeper.ui.errors import QueueServiceError
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    service = FakeQueueService()
    view_model = QueueViewModel(service)
    view_model.refresh()
    service.queue_status_error = QueueServiceError("remote_connectivity")

    succeeded = view_model.pause()

    assert succeeded is False
    assert view_model.is_paused is False
    assert view_model.is_stale is True
    assert "offline" in view_model.error_message.lower()


def test_pause_is_not_reported_successful_without_refreshed_confirmation():
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    class UnconfirmedPauseService(FakeQueueService):
        def pause(self):
            self.calls.append(("pause",))

    service = UnconfirmedPauseService()
    view_model = QueueViewModel(service)
    view_model.refresh()

    succeeded = view_model.pause()

    assert succeeded is False
    assert view_model.is_paused is False
    assert view_model.error_message == "Queue status did not confirm the requested action."


def test_retry_error_is_surfaced_and_does_not_report_success():
    from gitkeeper.ui.errors import QueueServiceError
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    blocked_item = make_item(42)
    blocked_item.state = "blocked"
    service = FakeQueueService(
        items={42: blocked_item},
        queue_items=[blocked_item],
    )
    view_model = QueueViewModel(service)
    view_model.refresh()
    service.retry_error = QueueServiceError("local_persistence")

    succeeded = view_model.retry(42)

    assert succeeded is False
    assert view_model.is_stale is True
    assert view_model.items[0]["state"] == "blocked"
    assert "unavailable" in view_model.error_message.lower()


def test_retry_success_requires_the_refreshed_queue_to_show_queued_state():
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    class UnconfirmedRetryService(FakeQueueService):
        def retry(self, pr_number):
            self.calls.append(("retry", pr_number))

    blocked_item = make_item(42)
    blocked_item.state = "blocked"
    service = UnconfirmedRetryService(
        items={42: blocked_item},
        queue_items=[blocked_item],
    )
    view_model = QueueViewModel(service)
    view_model.refresh()

    succeeded = view_model.retry(42)

    assert succeeded is False
    assert view_model.items[0]["state"] == "blocked"
    assert "not confirmed" in view_model.error_message.lower()
