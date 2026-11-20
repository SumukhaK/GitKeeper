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
        self._listeners: list[Callable[[Any], None]] = []

    def get_item(self, pr_number: int) -> Any | None:
        if self.get_item_error is not None:
            raise self.get_item_error
        return self.items.get(pr_number)

    def list_queue(self) -> list[Any]:
        if self.list_queue_error is not None:
            raise self.list_queue_error
        return list(self.queue_items)

    def pause(self) -> None:
        self.calls.append(("pause",))

    def resume(self) -> None:
        self.calls.append(("resume",))

    def cancel(self, pr_number: int) -> None:
        self.calls.append(("cancel", pr_number))

    def retry(self, pr_number: int) -> Any | None:
        self.calls.append(("retry", pr_number))
        return self.items.get(pr_number)

    def submit_review_feedback(
        self, pr_number: int, head_sha: str, feedback: str
    ) -> Any | None:
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
