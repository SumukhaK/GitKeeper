from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from gitkeeper.ui.errors import QueueServiceError

_WORKER_STATES = {"ready", "queued", "running", "blocked"}
_REPAIR_REASONS = {
    "deterministic_check_failure",
    "claude_feedback",
    "manual_retry",
}
_CHECK_STATES = {"passed", "deterministic_failure", "blocked", "unknown"}
_WORKER_LABELS = {
    "gitkeeper/worker-queued",
    "gitkeeper/worker-running",
    "gitkeeper/worker-blocked",
}


class QueueViewModel(QObject):
    changed = Signal()

    def __init__(self, queue_service: Any) -> None:
        super().__init__()
        self._queue_service = queue_service
        self._items: list[Any] = []
        self._is_stale = True
        self._error_message: str | None = None
        self._is_paused = False

    @Property("QVariantList", notify=changed)
    def items(self) -> list[dict[str, Any]]:
        return [self._to_qml_item(item) for item in self._items]

    @staticmethod
    def _value(item: Any, name: str, default: Any = None) -> Any:
        if isinstance(item, Mapping):
            return item.get(name, default)
        return getattr(item, name, default)

    @classmethod
    def _to_qml_item(cls, item: Any) -> dict[str, Any]:
        state = cls._enum_value(cls._value(item, "state"), _WORKER_STATES)
        reason = cls._enum_value(cls._value(item, "reason"), _REPAIR_REASONS)
        check_state = cls._enum_value(cls._value(item, "check_state"), _CHECK_STATES)
        error = cls._value(item, "last_error")
        error_category = cls._value(error, "category") if error is not None else None
        labels = cls._value(item, "remote_labels", ()) or ()
        return {
            "repository": cls._value(item, "repository"),
            "pr_number": cls._value(item, "pr_number"),
            "current_head_sha": cls._value(item, "current_head_sha"),
            "eligible": cls._value(item, "eligible"),
            "check_state": check_state,
            "state": state,
            "reason": reason,
            "automatic_attempt_count": cls._value(item, "automatic_attempt_count", 0),
            "manual_retry_count": cls._value(item, "manual_retry_count", 0),
            "last_error_category": error_category,
            "remote_labels": [label for label in labels if label in _WORKER_LABELS],
        }

    @staticmethod
    def _enum_value(value: Any, allowed: set[str]) -> str | None:
        if value is None:
            return None
        value = getattr(value, "value", value)
        return value if isinstance(value, str) and value in allowed else "unknown"

    @Property(bool, notify=changed)
    def is_stale(self) -> bool:
        return self._is_stale

    @Property(str, notify=changed)
    def error_message(self) -> str:
        return self._error_message or ""

    @Property(bool, notify=changed)
    def is_paused(self) -> bool:
        return self._is_paused

    @Property(bool, notify=changed)
    def controls_enabled(self) -> bool:
        return not self._is_stale

    def refresh(self) -> bool:
        try:
            items = self._queue_service.list_queue()
            is_paused = self._queue_service.get_queue_status()
        except QueueServiceError as error:
            self._is_stale = True
            self._error_message = error.user_message("queue")
            self.changed.emit()
            return False

        if type(is_paused) is not bool:
            self._is_stale = True
            self._error_message = "Queue status is invalid; controls are disabled."
            self.changed.emit()
            return False

        self._items = list(items)
        self._is_paused = is_paused
        self._is_stale = False
        self._error_message = None
        self.changed.emit()
        return True

    @Slot(result=bool)
    def pause(self) -> bool:
        return self._run_control("pause", expected_paused=True)

    @Slot(result=bool)
    def resume(self) -> bool:
        return self._run_control("resume", expected_paused=False)

    @Slot(int, result=bool)
    def cancel(self, pr_number: int) -> bool:
        if not self._run_control("cancel", pr_number=pr_number):
            return False
        return self._confirm_item_state(
            pr_number,
            lambda state: state in {"blocked", "ready"},
            "Cancellation was not confirmed by the refreshed queue.",
        )

    @Slot(int, result=bool)
    def retry(self, pr_number: int) -> bool:
        if not self._run_control("retry", pr_number=pr_number):
            return False
        return self._confirm_item_state(
            pr_number,
            lambda state: state == "queued",
            "Retry was not confirmed by the refreshed queue.",
        )

    def _run_control(
        self,
        action: str,
        *,
        pr_number: int | None = None,
        expected_paused: bool | None = None,
    ) -> bool:
        if self._is_stale:
            self._error_message = "Refresh the queue before using worker controls."
            self.changed.emit()
            return False
        try:
            if pr_number is None:
                getattr(self._queue_service, action)()
            else:
                getattr(self._queue_service, action)(pr_number)
        except QueueServiceError as error:
            self._is_stale = True
            self._error_message = error.user_message("queue")
            self.changed.emit()
            return False

        if not self.refresh():
            return False
        if expected_paused is not None and self._is_paused != expected_paused:
            self._error_message = "Queue status did not confirm the requested action."
            self.changed.emit()
            return False
        return True

    def _confirm_item_state(
        self,
        pr_number: int,
        predicate: Callable[[str | None], bool],
        failure_message: str,
    ) -> bool:
        item = next(
            (item for item in self._items if self._value(item, "pr_number") == pr_number),
            None,
        )
        state = self._enum_value(self._value(item, "state"), _WORKER_STATES)
        if item is not None and predicate(state):
            return True
        self._error_message = failure_message
        self.changed.emit()
        return False
