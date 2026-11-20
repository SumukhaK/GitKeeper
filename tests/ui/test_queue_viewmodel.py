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

    assert view_model.items == items
    assert view_model.is_stale is False
    assert view_model.error_message is None


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

    assert view_model.items == items
    assert view_model.is_stale is True
    assert view_model.error_message == "GitHub queue is offline; showing the last known state."


def test_controls_call_only_the_queue_service_contract_methods():
    from gitkeeper.ui.viewmodels.queue import QueueViewModel
    from tests.fakes.fake_queue_service import FakeQueueService

    service = FakeQueueService()
    view_model = QueueViewModel(service)

    view_model.pause()
    view_model.resume()
    view_model.cancel(42)
    view_model.retry(42)

    assert service.calls == [
        ("pause",),
        ("resume",),
        ("cancel", 42),
        ("retry", 42),
    ]
