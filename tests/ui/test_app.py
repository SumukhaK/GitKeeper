from types import SimpleNamespace


def test_create_viewmodels_displays_existing_queue_without_starting_worker():
    from gitkeeper.ui.app import create_viewmodels
    from tests.fakes.fake_queue_service import FakeQueueService

    item = SimpleNamespace(
        repository="SumukhaK/Setu-mobile",
        pr_number=42,
        current_head_sha="abc123",
        eligible=True,
        check_state="passed",
        state="ready",
        remote_labels=[],
        last_updated=None,
        last_error=None,
    )
    service = FakeQueueService(items={42: item}, queue_items=[item])

    status_view_model, queue_view_model = create_viewmodels(service)

    assert status_view_model.pr_number == 42
    assert status_view_model.current_sha == "abc123"
    assert queue_view_model.items[0]["pr_number"] == 42
    assert not hasattr(service, "start_worker")
