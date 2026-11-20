from typing import Any

from PySide6.QtCore import Property, QObject, Signal

from gitkeeper.ui.errors import QueueServiceError


class QueueViewModel(QObject):
    changed = Signal()

    def __init__(self, queue_service: Any) -> None:
        super().__init__()
        self._queue_service = queue_service
        self._items: list[Any] = []
        self._is_stale = False
        self._error_message: str | None = None
        self._is_paused = False

    @Property(object, notify=changed)
    def items(self) -> list[Any]:
        return list(self._items)

    @Property(bool, notify=changed)
    def is_stale(self) -> bool:
        return self._is_stale

    @Property(object, notify=changed)
    def error_message(self) -> str | None:
        return self._error_message

    @Property(bool, notify=changed)
    def is_paused(self) -> bool:
        return self._is_paused

    def refresh(self) -> None:
        try:
            items = self._queue_service.list_queue()
        except QueueServiceError as error:
            self._is_stale = True
            self._error_message = error.user_message("queue")
            self.changed.emit()
            return

        self._items = list(items)
        self._is_stale = False
        self._error_message = None
        self.changed.emit()

    def pause(self) -> None:
        self._queue_service.pause()
        self._is_paused = True
        self.changed.emit()

    def resume(self) -> None:
        self._queue_service.resume()
        self._is_paused = False
        self.changed.emit()

    def cancel(self, pr_number: int) -> None:
        self._queue_service.cancel(pr_number)

    def retry(self, pr_number: int) -> None:
        self._queue_service.retry(pr_number)
