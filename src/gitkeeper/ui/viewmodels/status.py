from datetime import datetime
from typing import Any

from PySide6.QtCore import Property, QObject, Signal

from gitkeeper.ui.errors import QueueServiceError


class StatusViewModel(QObject):
    changed = Signal()

    def __init__(self, queue_service: Any, pr_number: int | None = None) -> None:
        super().__init__()
        self._queue_service = queue_service
        self._pr_number = pr_number
        self._item: Any | None = None
        self._is_stale = False
        self._error_message: str | None = None

    @Property(object, notify=changed)
    def repository(self) -> str | None:
        return getattr(self._item, "repository", None)

    @Property(object, notify=changed)
    def pr_number(self) -> int | None:
        return self._pr_number

    @Property(object, notify=changed)
    def current_sha(self) -> str | None:
        return getattr(self._item, "current_head_sha", None)

    @Property(object, notify=changed)
    def eligibility(self) -> bool | None:
        return getattr(self._item, "eligible", None)

    @Property(object, notify=changed)
    def ci_result(self) -> str | None:
        check_state = getattr(self._item, "check_state", None)
        if check_state is None:
            return None
        return getattr(check_state, "value", check_state)

    @Property(object, notify=changed)
    def remote_state(self) -> str | None:
        labels = getattr(self._item, "remote_labels", ())
        for label in (
            "gitkeeper/worker-running",
            "gitkeeper/worker-queued",
            "gitkeeper/worker-blocked",
        ):
            if label in labels:
                return label
        return None

    @Property(object, notify=changed)
    def worker_state(self) -> str | None:
        state = getattr(self._item, "state", None)
        return getattr(state, "value", state)

    @Property(object, notify=changed)
    def last_updated(self) -> str | None:
        value = getattr(self._item, "last_updated", None)
        return value.isoformat() if isinstance(value, datetime) else value

    @Property(bool, notify=changed)
    def is_stale(self) -> bool:
        return self._is_stale

    @Property(object, notify=changed)
    def error_message(self) -> str | None:
        return self._error_message

    def refresh(self) -> None:
        if self._pr_number is None:
            self._item = None
            self._is_stale = False
            self._error_message = None
            self.changed.emit()
            return

        try:
            item = self._queue_service.get_item(self._pr_number)
        except QueueServiceError as error:
            self._is_stale = True
            self._error_message = error.user_message("status")
            self.changed.emit()
            return

        self._item = item
        self._is_stale = False
        self._error_message = None
        self.changed.emit()
