"""The companion half of a command approval.

The worker proposes one exact command, this view model shows every line of it,
and the owner answers by clicking. Nothing here can approve on its own: a
missing answer, a closed panel, or an unavailable broker all deny, and an
approval is never reported as a result until the broker confirms one.

The view model is also the `ApprovalPort` the broker talks to. The broker may
run it on a worker thread while Qt keeps the panel responsive, so the answer
hand-off is a lock plus an event rather than a callback.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

from gitkeeper.host_mode.command_broker import (
    ApprovalPortError,
    ExecutionResult,
    RenderedRequest,
)

IDLE = "idle"
PENDING = "pending"
QUEUED_FOR_EXECUTION = "queued-for-execution"
DENIED = "denied"
TIMED_OUT = "timed-out"
COMPLETED = "completed"
FAILED = "failed"
UNAVAILABLE = "unavailable"

_APPROVE = "approve"
_DENY = "deny"
_ANIMATED_PREFERENCES = frozenset({"", "full"})
_MOTION_PREFERENCES = _ANIMATED_PREFERENCES | {"reduced", "disabled"}

_MESSAGES = {
    IDLE: "No command is waiting for review.",
    PENDING: "Review the exact command below before it runs.",
    QUEUED_FOR_EXECUTION: "Approved once. Waiting for the worker to report the result.",
    DENIED: "Denied. The command was not run.",
    TIMED_OUT: "No answer arrived, so the command was not run.",
    UNAVAILABLE: "Command approval is unavailable; nothing was run.",
}


class CommandApprovalViewModel(QObject):
    """Shows one pending command and collects one explicit decision."""

    changed = Signal()

    def __init__(
        self,
        *,
        broker_available: bool = True,
        motion_preference: str | None = None,
    ) -> None:
        super().__init__()
        preference = motion_preference or ""
        if preference not in _MOTION_PREFERENCES:
            raise ValueError(
                f"motion_preference must be one of {sorted(_MOTION_PREFERENCES)}."
            )
        self._broker_available = bool(broker_available)
        self._motion_preference = preference
        self._lock = threading.Lock()
        self._answer_ready = threading.Event()
        self._answer: str | None = None
        self._status = IDLE
        self._message = ""
        self._request_lines: list[str] = []
        self._request_digest = ""
        self._shown_digest = ""
        self._executable = ""
        self._purpose = ""
        self._repository = ""
        self._pr_number = 0

    @Property(str, notify=changed)
    def status(self) -> str:
        return self._status

    @Property(str, notify=changed)
    def status_message(self) -> str:
        if self._status in (COMPLETED, FAILED):
            return self._message
        return _MESSAGES.get(self._status, _MESSAGES[IDLE])

    @Property(bool, notify=changed)
    def pending(self) -> bool:
        return self._status == PENDING

    @Property("QVariantList", notify=changed)
    def request_lines(self) -> list[str]:
        return list(self._request_lines)

    @Property(str, notify=changed)
    def request_digest(self) -> str:
        return self._request_digest

    @Property(str, notify=changed)
    def shown_digest(self) -> str:
        return self._shown_digest

    @Property(str, notify=changed)
    def executable(self) -> str:
        return self._executable

    @Property(str, notify=changed)
    def purpose(self) -> str:
        return self._purpose

    @Property(str, notify=changed)
    def repository(self) -> str:
        return self._repository

    @Property(int, notify=changed)
    def pr_number(self) -> int:
        return self._pr_number

    @Property(str, notify=changed)
    def approve_label(self) -> str:
        return "Approve once"

    @Property(str, notify=changed)
    def deny_label(self) -> str:
        return "Deny"

    @Property(bool, notify=changed)
    def controls_enabled(self) -> bool:
        return True

    @Property(bool, notify=changed)
    def animation_enabled(self) -> bool:
        return self._motion_preference in _ANIMATED_PREFERENCES

    @Property(str, notify=changed)
    def motion_preference(self) -> str:
        return self._motion_preference

    @Property(str, notify=changed)
    def accessibility_description(self) -> str:
        subject = (
            f"{self._repository} #{self._pr_number}"
            if self._repository
            else "no pull request"
        )
        digest = self._request_digest or "no request"
        return (
            f"Review command approval for {subject}. {self.status_message} "
            f"Approve once or Deny with the buttons; there is no default answer. "
            f"Request digest {digest}."
        )

    def show(self, digest: str, lines: tuple[str, ...]) -> RenderedRequest:
        """Present every rendered line and report exactly what was shown."""
        if not isinstance(digest, str) or not digest:
            raise ApprovalPortError("A request digest is required before review.")
        if isinstance(lines, str):
            raise ApprovalPortError("Rendered lines must be a sequence of strings.")
        rendered = tuple(lines)
        if not rendered:
            raise ApprovalPortError(
                "A command request must be displayed in full before review."
            )
        for line in rendered:
            if not isinstance(line, str):
                raise ApprovalPortError("Rendered lines must be strings.")

        with self._lock:
            if self._status == PENDING:
                raise ApprovalPortError(
                    "Another command is already awaiting a decision; decide it first."
                )
            self._request_digest = digest
            self._shown_digest = digest
            self._request_lines = list(rendered)
            self._executable, self._purpose, self._repository, self._pr_number = (
                _summarise(rendered, digest)
            )
            self._answer = None
            self._answer_ready.clear()
            self._status = PENDING
        self.changed.emit()
        return RenderedRequest(digest=digest, lines=rendered)

    def wait(self, timeout_seconds: float) -> str | None:
        """Block for the owner's answer; no answer is a denial, never an approval."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if not self._answer_ready.wait(timeout_seconds):
            with self._lock:
                if self._answer is None and self._status == PENDING:
                    self._status = TIMED_OUT
                    self.changed.emit()
                    return None
        with self._lock:
            answer = self._answer
            self._answer = None
            self._answer_ready.clear()
        return answer

    @Slot(result=bool)
    def approve(self) -> bool:
        return self._decide(_APPROVE, QUEUED_FOR_EXECUTION)

    @Slot(result=bool)
    def deny(self) -> bool:
        return self._decide(_DENY, DENIED)

    @Slot(str)
    def set_motion_preference(self, preference: str) -> None:
        value = preference or ""
        if value not in _MOTION_PREFERENCES:
            raise ValueError(
                f"motion_preference must be one of {sorted(_MOTION_PREFERENCES)}."
            )
        self._motion_preference = value
        self.changed.emit()

    def record_execution(
        self,
        result: ExecutionResult,
        request_digest: str | None = None,
    ) -> bool:
        """Report what the worker confirmed, never what we hoped would happen."""
        if not isinstance(result, ExecutionResult):
            raise TypeError("result must be an ExecutionResult.")
        if request_digest is not None and (
            not isinstance(request_digest, str) or not request_digest
        ):
            raise ValueError("request_digest must be a non-empty string or None.")
        with self._lock:
            if self._status != QUEUED_FOR_EXECUTION:
                return False
            if (
                request_digest is not None
                and request_digest != self._request_digest
            ):
                return False
            self._status = COMPLETED if result.exit_code == 0 else FAILED
            verb = "Finished" if result.exit_code == 0 else "Failed"
            self._message = f"{verb} with exit code {result.exit_code}."
        self.changed.emit()
        return True

    @Slot()
    def reset(self) -> None:
        with self._lock:
            self._request_lines = []
            self._request_digest = ""
            self._shown_digest = ""
            self._executable = ""
            self._purpose = ""
            self._repository = ""
            self._pr_number = 0
            self._answer = None
            self._answer_ready.clear()
            self._status = IDLE
            self._message = ""
        self.changed.emit()

    def _decide(self, answer: str, status: str) -> bool:
        with self._lock:
            if self._status != PENDING:
                return False
            if not self._broker_available:
                self._status = UNAVAILABLE
                self.changed.emit()
                return False
            self._answer = answer
            self._status = status
            self._answer_ready.set()
        self.changed.emit()
        return True


def _summarise(lines: tuple[str, ...], digest: str) -> tuple[str, str, str, int]:
    """Recover the header fields from the rendered lines, never from the request."""
    executable = ""
    purpose = ""
    repository = ""
    pr_number = 0
    for line in lines:
        label, _, value = line.partition(": ")
        if label == "Executable":
            executable = value
        elif label == "Purpose":
            purpose = value
        elif label == "Repository":
            repository = value
        elif label == "Pull request":
            number = value.lstrip("#")
            pr_number = int(number) if number.isdigit() else 0
    return executable, purpose, repository, pr_number
