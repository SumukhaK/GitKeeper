from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gitkeeper.ui.viewmodels.telemetry import TelemetryViewModel

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
ALLOWED_FIELDS = {
    "timestamp",
    "duration_ms",
    "repository",
    "pr_number",
    "head_sha",
    "from_state",
    "to_state",
    "attempt_count",
    "outcome",
    "model_id",
    "error_category",
}


def make_event(timestamp, **overrides):
    values = {
        "timestamp": timestamp,
        "duration_ms": 1200,
        "repository": "SumukhaK/Setu-mobile",
        "pr_number": 42,
        "head_sha": "abc123",
        "from_state": "queued",
        "to_state": "blocked",
        "attempt_count": 1,
        "outcome": "worker_unavailable",
        "model_id": None,
        "error_category": "worker_unavailable",
        "source_code": "SOURCE-MUST-NOT-APPEAR",
        "diff": "DIFF-MUST-NOT-APPEAR",
        "prompt": "PROMPT-MUST-NOT-APPEAR",
        "credential": "CREDENTIAL-MUST-NOT-APPEAR",
        "screenshot": "SCREENSHOT-MUST-NOT-APPEAR",
        "opencode_input": "INPUT-MUST-NOT-APPEAR",
        "opencode_output": "OUTPUT-MUST-NOT-APPEAR",
        "claude_feedback": "FEEDBACK-MUST-NOT-APPEAR",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeTelemetryService:
    def __init__(self, events, retention_days=90):
        self.events = list(events)
        self.retention_days = retention_days
        self.list_calls = []
        self.retention_calls = []
        self.list_error = None
        self.retention_error = None

    def get_retention_days(self):
        return self.retention_days

    def list_events(self, after=None):
        self.list_calls.append(after)
        if self.list_error is not None:
            raise self.list_error
        return list(self.events)

    def set_retention_days(self, days):
        self.retention_calls.append(days)
        if self.retention_error is not None:
            raise self.retention_error
        self.retention_days = days


def test_projection_contains_only_allowlisted_operational_metadata():
    event = make_event(NOW)
    service = FakeTelemetryService([event])
    view_model = TelemetryViewModel(service, clock=lambda: NOW)

    view_model.refresh()

    assert len(view_model.events) == 1
    assert set(view_model.events[0]) == ALLOWED_FIELDS
    assert view_model.events[0]["timestamp"] == NOW.isoformat()
    assert view_model.events[0]["error_category"] == "worker_unavailable"
    rendered = repr(view_model.events)
    for private_value in (
        "SOURCE-MUST-NOT-APPEAR",
        "DIFF-MUST-NOT-APPEAR",
        "PROMPT-MUST-NOT-APPEAR",
        "CREDENTIAL-MUST-NOT-APPEAR",
        "SCREENSHOT-MUST-NOT-APPEAR",
        "INPUT-MUST-NOT-APPEAR",
        "OUTPUT-MUST-NOT-APPEAR",
        "FEEDBACK-MUST-NOT-APPEAR",
    ):
        assert private_value not in rendered


def test_retention_can_be_shortened_but_not_extended_or_made_invalid():
    service = FakeTelemetryService([], retention_days=90)
    view_model = TelemetryViewModel(service, clock=lambda: NOW)

    assert view_model.retention_days == 90
    assert view_model.set_retention_days(30) is True
    assert service.retention_calls == [30]
    assert view_model.retention_days == 30
    assert view_model.set_retention_days(91) is False
    assert view_model.set_retention_days(-1) is False
    assert service.retention_calls == [30]


def test_expired_events_are_not_displayed_after_retention_is_shortened():
    recent = make_event(NOW - timedelta(days=10), pr_number=41)
    expired = make_event(NOW - timedelta(days=31), pr_number=42)
    service = FakeTelemetryService([recent, expired], retention_days=30)
    view_model = TelemetryViewModel(service, clock=lambda: NOW)

    view_model.refresh()

    assert [event["pr_number"] for event in view_model.events] == [41]
    assert service.list_calls == [NOW - timedelta(days=30)]


def test_event_inside_retention_window_with_fractional_timestamp_is_kept():
    cutoff = NOW - timedelta(days=30)
    just_inside = make_event(cutoff + timedelta(microseconds=100))
    service = FakeTelemetryService([just_inside], retention_days=30)
    view_model = TelemetryViewModel(service, clock=lambda: NOW)

    view_model.refresh()

    assert len(view_model.events) == 1


def test_local_service_error_preserves_history_and_surfaces_safe_message():
    from gitkeeper.ui.errors import TelemetryServiceError

    service = FakeTelemetryService([make_event(NOW)])
    view_model = TelemetryViewModel(service, clock=lambda: NOW)
    view_model.refresh()
    service.list_error = TelemetryServiceError("local_persistence")

    view_model.refresh()

    assert len(view_model.events) == 1
    assert view_model.is_stale is True
    assert view_model.error_message == (
        "Local telemetry is unavailable; showing the last known history."
    )
