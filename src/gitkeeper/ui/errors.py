from typing import Literal


ErrorCategory = Literal["remote_connectivity", "local_persistence", "unknown"]
TelemetryErrorCategory = Literal["local_persistence", "unknown"]

_ERROR_MESSAGES: dict[str, dict[ErrorCategory, str]] = {
    "status": {
        "remote_connectivity": "GitHub status is offline; showing the last known state.",
        "local_persistence": "Local queue data is unavailable; worker controls are disabled.",
        "unknown": "PR status could not refresh; showing the last known state.",
    },
    "queue": {
        "remote_connectivity": "GitHub queue is offline; showing the last known state.",
        "local_persistence": "Local queue data is unavailable; worker controls are disabled.",
        "unknown": "The queue could not refresh; showing the last known state.",
    },
}


class QueueServiceError(Exception):
    def __init__(self, category: ErrorCategory) -> None:
        if not any(category in messages for messages in _ERROR_MESSAGES.values()):
            raise ValueError(f"unsupported queue service error category: {category!r}")
        self.category = category
        super().__init__(category)

    def user_message(self, context: Literal["status", "queue"]) -> str:
        return _ERROR_MESSAGES[context][self.category]


_TELEMETRY_ERROR_MESSAGES: dict[TelemetryErrorCategory, str] = {
    "local_persistence": "Local telemetry is unavailable; showing the last known history.",
    "unknown": "Telemetry could not refresh; showing the last known history.",
}


class TelemetryServiceError(Exception):
    def __init__(self, category: TelemetryErrorCategory) -> None:
        if category not in _TELEMETRY_ERROR_MESSAGES:
            raise ValueError(f"unsupported telemetry service error category: {category!r}")
        self.category = category
        super().__init__(category)

    def user_message(self) -> str:
        return _TELEMETRY_ERROR_MESSAGES[self.category]
