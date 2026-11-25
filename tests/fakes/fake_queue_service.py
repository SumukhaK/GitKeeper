from typing import Any, Callable

from gitkeeper.ui.errors import QueueServiceError


class FakeQueueService:
    def __init__(
        self,
        *,
        items: dict[int, Any] | None = None,
        queue_items: list[Any] | None = None,
    ) -> None:
        self.items = items or {}
        self.queue_items = queue_items or []
        self.calls: list[tuple[Any, ...]] = []
        self.get_item_error: QueueServiceError | None = None
        self.list_queue_error: QueueServiceError | None = None
        self.queue_status_error: QueueServiceError | None = None
        self.pause_error: QueueServiceError | None = None
        self.resume_error: QueueServiceError | None = None
        self.cancel_error: QueueServiceError | None = None
        self.retry_error: QueueServiceError | None = None
        self.submit_feedback_error: QueueServiceError | None = None
        self.paused = False
        self._listeners: list[Callable[[Any], None]] = []

    def get_item(self, pr_number: int) -> Any | None:
        if self.get_item_error is not None:
            raise self.get_item_error
        return self.items.get(pr_number)

    def list_queue(self) -> list[Any]:
        if self.list_queue_error is not None:
            raise self.list_queue_error
        return list(self.queue_items)

    def get_queue_status(self) -> bool:
        if self.queue_status_error is not None:
            raise self.queue_status_error
        return self.paused

    def pause(self) -> None:
        self.calls.append(("pause",))
        if self.pause_error is not None:
            raise self.pause_error
        self.paused = True

    def resume(self) -> None:
        self.calls.append(("resume",))
        if self.resume_error is not None:
            raise self.resume_error
        self.paused = False

    def cancel(self, pr_number: int) -> None:
        self.calls.append(("cancel", pr_number))
        if self.cancel_error is not None:
            raise self.cancel_error
        for item in self.queue_items:
            if getattr(item, "pr_number", None) == pr_number:
                item.state = "blocked"

    def retry(self, pr_number: int) -> Any | None:
        self.calls.append(("retry", pr_number))
        if self.retry_error is not None:
            raise self.retry_error
        item = self.items.get(pr_number)
        if item is not None:
            item.state = "queued"
            if item not in self.queue_items:
                self.queue_items.append(item)
        return item

    def submit_review_feedback(
        self, pr_number: int, head_sha: str, feedback: str
    ) -> Any | None:
        if self.submit_feedback_error is not None:
            raise self.submit_feedback_error
        self.calls.append(("submit_review_feedback", pr_number, head_sha, feedback))
        return self.items.get(pr_number)

    def subscribe(self, listener: Callable[[Any], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            self._listeners.remove(listener)

        return unsubscribe

    def emit(self, event: Any) -> None:
        for listener in tuple(self._listeners):
            listener(event)
