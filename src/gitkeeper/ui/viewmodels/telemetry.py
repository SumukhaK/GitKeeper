import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from PySide6.QtCore import Property, QObject, Signal, Slot

from gitkeeper.ui.errors import TelemetryServiceError


class TelemetryService(Protocol):
    def get_retention_days(self) -> int: ...

    def list_events(self, after: datetime | None = None) -> list[Any]: ...

    def set_retention_days(self, days: int) -> None: ...


class TelemetryViewModel(QObject):
    changed = Signal()

    def __init__(
        self,
        telemetry_service: TelemetryService | None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__()
        self._telemetry_service = telemetry_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._events: list[dict[str, object]] = []
        self._retention_days = 90
        self._is_stale = False
        self._error_message = ""
        self._retention_error = ""

        if telemetry_service is None:
            self._error_message = "Telemetry service unavailable."
            self._is_stale = True
        else:
            try:
                days = telemetry_service.get_retention_days()
            except TelemetryServiceError as error:
                self._retention_error = error.user_message()
            else:
                if type(days) is not int or not 0 <= days <= 90:
                    raise ValueError("telemetry retention must be between 0 and 90 days")
                self._retention_days = days

    @Property("QVariantList", notify=changed)
    def events(self) -> list[dict[str, object]]:
        return [dict(event) for event in self._events]

    @Property(int, notify=changed)
    def retention_days(self) -> int:
        return self._retention_days

    @Property(bool, notify=changed)
    def is_stale(self) -> bool:
        return self._is_stale

    @Property(str, notify=changed)
    def error_message(self) -> str:
        return self._error_message

    @Property(str, notify=changed)
    def retention_error(self) -> str:
        return self._retention_error

    def refresh(self) -> None:
        if self._telemetry_service is None:
            self._is_stale = True
            self._error_message = "Telemetry service unavailable."
            self.changed.emit()
            return

        now = self._as_utc(self._clock())
        if now is None:
            raise ValueError("telemetry clock must return a timezone-aware datetime")
        cutoff = now - timedelta(days=self._retention_days)

        try:
            source_events = self._telemetry_service.list_events(after=cutoff)
        except TelemetryServiceError as error:
            self._is_stale = True
            self._error_message = error.user_message()
            self.changed.emit()
            return

        projected = []
        for event in source_events:
            row = self._project_event(event)
            if row is not None and row["timestamp"] >= cutoff.isoformat():
                projected.append(row)
        projected.sort(key=lambda row: row["timestamp"], reverse=True)
        self._events = projected
        self._is_stale = False
        self._error_message = ""
        self.changed.emit()

    @Slot(int, result=bool)
    def set_retention_days(self, days: int) -> bool:
        if type(days) is not int or not 0 <= days <= 90:
            self._retention_error = "Retention must be between 0 and 90 days."
            self.changed.emit()
            return False
        if self._telemetry_service is None:
            self._retention_error = "Telemetry service unavailable."
            self.changed.emit()
            return False

        try:
            self._telemetry_service.set_retention_days(days)
        except TelemetryServiceError as error:
            self._retention_error = error.user_message()
            self.changed.emit()
            return False
        self._retention_days = days
        self._retention_error = ""
        self.refresh()
        return True

    @classmethod
    def _project_event(cls, event: Any) -> dict[str, object] | None:
        timestamp = cls._value(event, "timestamp")
        timestamp = cls._as_utc(timestamp)
        if timestamp is None:
            return None

        duration = cls._value(event, "duration_ms")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            duration = None

        pr_number = cls._value(event, "pr_number")
        if type(pr_number) is not int or pr_number < 1:
            pr_number = 0

        attempt_count = cls._value(event, "attempt_count")
        if type(attempt_count) is not int or attempt_count < 0:
            attempt_count = 0

        return {
            "timestamp": timestamp.isoformat(),
            "duration_ms": duration,
            "repository": cls._string_value(event, "repository"),
            "pr_number": pr_number,
            "head_sha": cls._string_value(event, "head_sha"),
            "from_state": cls._string_value(event, "from_state", "unknown"),
            "to_state": cls._string_value(event, "to_state", "unknown"),
            "attempt_count": attempt_count,
            "outcome": cls._string_value(event, "outcome", "unknown"),
            "model_id": cls._optional_string_value(event, "model_id"),
            "error_category": cls._string_value(event, "error_category", ""),
        }

    @staticmethod
    def _value(event: Any, name: str) -> Any:
        if isinstance(event, Mapping):
            return event.get(name)
        return getattr(event, name, None)

    @classmethod
    def _string_value(cls, event: Any, name: str, default: str = "") -> str:
        value = cls._value(event, name)
        return value if isinstance(value, str) else default

    @classmethod
    def _optional_string_value(cls, event: Any, name: str) -> str | None:
        value = cls._value(event, name)
        return value if isinstance(value, str) else None

    @staticmethod
    def _as_utc(value: Any) -> datetime | None:
        if not isinstance(value, datetime) or value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
