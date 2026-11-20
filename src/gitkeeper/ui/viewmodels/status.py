from datetime import datetime
from typing import Any

from PySide6.QtCore import Property, QObject, Signal

from gitkeeper.ui.errors import QueueServiceError

_CHECK_RESULTS = {"passed", "deterministic_failure", "blocked", "unknown"}
_WORKER_STATES = {"ready", "queued", "running", "blocked"}


class StatusViewModel(QObject):
    changed = Signal()

    def __init__(self, queue_service: Any, pr_number: int | None = None) -> None:
        super().__init__()
        self._queue_service = queue_service
        self._pr_number = pr_number
        self._item: Any | None = None
        self._is_stale = False
        self._error_message: str | None = None

    @Property(str, notify=changed)
    def repository(self) -> str:
        return getattr(self._item, "repository", "") or ""

    @Property(int, notify=changed)
    def pr_number(self) -> int:
        return self._pr_number or 0

    @Property(str, notify=changed)
    def current_sha(self) -> str:
        return getattr(self._item, "current_head_sha", "") or ""

    @Property(bool, notify=changed)
    def eligibility(self) -> bool:
        return getattr(self._item, "eligible", False) is True

    @Property(str, notify=changed)
    def ci_result(self) -> str:
        check_state = getattr(self._item, "check_state", None)
        if check_state is None:
            return ""
        value = getattr(check_state, "value", check_state)
        return value if isinstance(value, str) and value in _CHECK_RESULTS else "unknown"

    @Property(str, notify=changed)
    def remote_state(self) -> str:
        labels = getattr(self._item, "remote_labels", ()) or ()
        for label in (
            "gitkeeper/worker-running",
            "gitkeeper/worker-queued",
            "gitkeeper/worker-blocked",
        ):
            if label in labels:
                return label
        return ""

    @Property(str, notify=changed)
    def worker_state(self) -> str:
        state = getattr(self._item, "state", None)
        value = getattr(state, "value", state)
        return value if isinstance(value, str) and value in _WORKER_STATES else "unknown"

    @Property(str, notify=changed)
    def last_updated(self) -> str:
        value = getattr(self._item, "last_updated", None)
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value or "")

    @Property(bool, notify=changed)
    def is_stale(self) -> bool:
        return self._is_stale

    @Property(str, notify=changed)
    def error_message(self) -> str:
        return self._error_message or ""

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
