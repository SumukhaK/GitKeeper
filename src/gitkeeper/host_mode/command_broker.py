"""One-shot approval for every command the worker asks the host to run.

In accepted host mode the worker runs as the logged-in owner, so a command it
proposes is as powerful as anything the owner could type. The broker therefore
treats each request as an immutable reviewed artefact:

* the exact executable, arguments or full script, directory, and environment
  variable *names* are shown to the owner (values are never modelled at all);
* a canonical digest binds the approval to those exact values;
* silence, a closed dialog, a partial render, a changed pull request, or a
  changed commit all deny;
* an approval is single-use, short-lived, and only honoured by the broker that
  issued it.

Execution itself stays disabled until `gitkeeper.host_mode.opencode_capability`
proves that OpenCode routes every shell request through this broker.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from gitkeeper.host_mode.readiness import PullRequestFacts, is_repository_name

APPROVAL_TIMEOUT_SECONDS = 120.0
APPROVAL_TOKEN_TTL_SECONDS = 300.0
MAX_PURPOSE_LENGTH = 120

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_APPROVE = "approve"


class CommandRequestError(ValueError):
    """A proposed command cannot be reviewed as written."""


class ApprovalPortError(RuntimeError):
    """The companion could not present or collect a decision."""


class ApprovalNotApprovedError(PermissionError):
    """The owner did not approve this request."""


class ApprovalMismatchError(PermissionError):
    """The approval does not bind to the request being executed."""


class ApprovalExpiredError(PermissionError):
    """The approval outlived its validity window."""


class ApprovalReplayError(PermissionError):
    """This request was already executed under its approval."""


class StaleApprovalError(PermissionError):
    """The pull request or its head commit changed after approval."""


class UnrecognisedApprovalError(PermissionError):
    """The decision was not issued by this broker."""


class ExecutionUnavailableError(RuntimeError):
    """Command execution is disabled until OpenCode mediation is proven."""


class ApprovalOutcome(str, Enum):
    """What happened while the owner was asked to decide."""

    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"
    INCOMPLETE_DISPLAY = "incomplete-display"
    STALE_REQUEST = "stale-request"


def _single_line(value: object, field: str, *, limit: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommandRequestError(f"{field} must be a non-empty string.")
    text = value.strip()
    if "\n" in text or "\r" in text:
        raise CommandRequestError(f"{field} must be a single line.")
    if limit is not None and len(text) > limit:
        raise CommandRequestError(f"{field} must be at most {limit} characters.")
    return text


def _is_absolute(path: str) -> bool:
    return ntpath.isabs(path) or posixpath.isabs(path)


def _normalised_environment(names: object) -> tuple[str, ...]:
    if isinstance(names, str):
        raise CommandRequestError(
            "environment_names must be a collection, not a string."
        )
    try:
        raw = tuple(names)  # type: ignore[arg-type]
    except TypeError as error:  # pragma: no cover - defensive
        raise CommandRequestError("environment_names must be a collection.") from error
    cleaned = [_single_line(name, "environment name") for name in raw]
    if len(set(cleaned)) != len(cleaned):
        raise CommandRequestError("environment_names must not repeat a name.")
    return tuple(sorted(cleaned))


@dataclass(frozen=True)
class CommandRequest:
    """An immutable command, exactly as the owner will review it.

    There is deliberately no field for environment *values*: the broker cannot
    leak or persist a secret it never accepts.
    """

    executable: str
    cwd: str
    repository: str
    pr_number: int
    starting_sha: str
    purpose: str
    argv: tuple[str, ...] = ()
    script: str | None = None
    environment_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        executable = _single_line(self.executable, "executable")
        if "\\" in executable and not _is_absolute(executable):
            raise CommandRequestError(
                "executable must be a name or an absolute path."
            )
        object.__setattr__(self, "executable", executable)

        cwd = self.cwd
        if not isinstance(cwd, str) or not cwd.strip():
            raise CommandRequestError("cwd must be a non-empty directory path.")
        if not _is_absolute(cwd.strip()):
            raise CommandRequestError("cwd must be an absolute directory path.")
        object.__setattr__(self, "cwd", cwd.strip())

        if not is_repository_name(self.repository):
            raise CommandRequestError(
                f"repository must be an owner/repository value, "
                f"got {self.repository!r}."
            )
        if isinstance(self.pr_number, bool) or not isinstance(self.pr_number, int):
            raise CommandRequestError("pr_number must be a pull request number.")
        if self.pr_number <= 0:
            raise CommandRequestError("pr_number must be positive.")
        if not isinstance(self.starting_sha, str) or not _SHA_PATTERN.match(
            self.starting_sha
        ):
            raise CommandRequestError(
                "starting_sha must be a lowercase 40-character commit SHA."
            )
        purpose = _single_line(self.purpose, "purpose", limit=MAX_PURPOSE_LENGTH)
        object.__setattr__(self, "purpose", purpose)

        script = self.script
        argv = self.argv
        if script is not None:
            if not isinstance(script, str) or not script.strip():
                raise CommandRequestError("script must be non-empty when provided.")
            if argv:
                raise CommandRequestError("Provide either argv or script, not both.")
            object.__setattr__(self, "script", script)
            object.__setattr__(self, "argv", ())
        else:
            if isinstance(argv, str):
                raise CommandRequestError("argv must be a sequence of arguments.")
            arguments = tuple(argv)
            if not arguments:
                raise CommandRequestError("Provide argv or script.")
            for argument in arguments:
                if not isinstance(argument, str):
                    raise CommandRequestError("argv entries must be strings.")
                if "\n" in argument or "\r" in argument or "\x00" in argument:
                    raise CommandRequestError(
                        "argv entries must not contain line breaks or NUL."
                    )
            object.__setattr__(self, "argv", arguments)

        object.__setattr__(
            self, "environment_names", _normalised_environment(self.environment_names)
        )

    @property
    def kind(self) -> str:
        return "script" if self.script is not None else "argv"

    def canonical_payload(self) -> str:
        """The exact reviewed values, serialised with a stable key order."""
        return json.dumps(
            {
                "executable": self.executable,
                "cwd": self.cwd,
                "repository": self.repository,
                "pr_number": self.pr_number,
                "starting_sha": self.starting_sha,
                "purpose": self.purpose,
                "kind": self.kind,
                "argv": list(self.argv),
                "script": self.script,
                "environment_names": list(self.environment_names),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @property
    def digest(self) -> str:
        payload = self.canonical_payload().encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def render_request(request: CommandRequest) -> tuple[str, ...]:
    """Every line the owner must see before deciding."""
    lines = [f"Executable: {request.executable}"]
    if request.script is not None:
        lines.append("Shell script (complete):")
        for number, line in enumerate(request.script.splitlines() or [""], start=1):
            lines.append(f"  {number:>2} | {line}")
    else:
        lines.append("Arguments:")
        for number, argument in enumerate(request.argv, start=1):
            lines.append(f"  {number:>2} > {argument}")
    names = ", ".join(request.environment_names)
    lines.extend(
        [
            f"Environment variables (names only): {names or '(none)'}",
            f"Working directory: {request.cwd}",
            f"Repository: {request.repository}",
            f"Pull request: #{request.pr_number}",
            f"Starting commit: {request.starting_sha}",
            f"Purpose: {request.purpose}",
            f"Request digest: {request.digest}",
        ]
    )
    return tuple(lines)


@dataclass(frozen=True)
class RenderedRequest:
    """What the companion confirms it put in front of the owner."""

    digest: str
    lines: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.digest, str) or not self.digest:
            raise ApprovalPortError("A rendered request needs its digest.")
        if isinstance(self.lines, str):
            raise ApprovalPortError("Rendered lines must be a sequence.")
        rendered = tuple(self.lines)
        for line in rendered:
            if not isinstance(line, str):
                raise ApprovalPortError("Rendered lines must be strings.")
        object.__setattr__(self, "lines", rendered)


@runtime_checkable
class ApprovalPort(Protocol):
    """The companion side of an approval: show the request, then answer."""

    def show(self, digest: str, lines: tuple[str, ...]) -> RenderedRequest:
        """Display every line and report exactly what was shown."""

    def wait(self, timeout_seconds: float) -> str | None:
        """Return `"approve"`, `"deny"`, or `None` when nothing arrived."""


@dataclass(frozen=True)
class ApprovalDecision:
    """The record of one review, bound to one exact request digest."""

    decision_id: str
    request: CommandRequest
    request_digest: str
    outcome: ApprovalOutcome
    issued_at: float
    decided_at: float

    @property
    def approved(self) -> bool:
        return self.outcome is ApprovalOutcome.APPROVED

    @property
    def review_seconds(self) -> float:
        return max(0.0, self.decided_at - self.issued_at)

    def audit_metadata(self) -> dict[str, object]:
        """Operational metadata only: never the command, arguments, or script."""
        return {
            "request_digest": self.request_digest,
            "outcome": self.outcome.value,
            "purpose": self.request.purpose,
            "repository": self.request.repository,
            "pr_number": self.request.pr_number,
            "starting_sha": self.request.starting_sha,
            "review_seconds": round(self.review_seconds, 3),
        }


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of an approved command."""

    exit_code: int
    output_summary: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise TypeError("exit_code must be an integer.")
        if not isinstance(self.output_summary, str):
            raise TypeError("output_summary must be a string.")


def execution_disabled(request: CommandRequest) -> ExecutionResult:
    """The default executor: refuse until mediation is proven end to end."""
    raise ExecutionUnavailableError(
        "Command execution remains disabled until OpenCode command mediation "
        "is verified through the companion."
    )


def _require_request(request: object) -> CommandRequest:
    if not isinstance(request, CommandRequest):
        raise TypeError("A command request must be a CommandRequest.")
    return request


class ApprovalBroker:
    """Collects one approval per exact command and executes it once."""

    def __init__(
        self,
        port: ApprovalPort,
        *,
        facts: Callable[[], PullRequestFacts],
        executor: Callable[[CommandRequest], ExecutionResult] | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout_seconds: float = APPROVAL_TIMEOUT_SECONDS,
        token_ttl_seconds: float = APPROVAL_TOKEN_TTL_SECONDS,
    ) -> None:
        if not callable(facts):
            raise TypeError("facts must be callable so GitHub state is re-read.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if executor is not None and not callable(executor):
            raise TypeError("executor must be callable.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if token_ttl_seconds <= 0:
            raise ValueError("token_ttl_seconds must be positive.")
        self._port = port
        self._facts = facts
        self._executor = executor if executor is not None else execution_disabled
        self._clock = clock
        self._timeout_seconds = float(timeout_seconds)
        self._token_ttl_seconds = float(token_ttl_seconds)
        self._issued: dict[str, ApprovalDecision] = {}
        self._consumed: set[str] = set()

    def request_approval(self, request: CommandRequest) -> ApprovalDecision:
        """Ask the owner once, and refuse unless every condition was met."""
        _require_request(request)
        digest = request.digest
        lines = render_request(request)
        issued_at = self._clock()

        # Fail closed before bothering the owner, then again after the answer.
        if not self._matches_remote_state(request):
            return self._record(request, ApprovalOutcome.STALE_REQUEST, issued_at)

        rendered = self._present(digest, lines)
        if rendered is _NO_PRESENTATION:
            return self._record(request, ApprovalOutcome.DISCONNECTED, issued_at)
        if not self._displayed_exactly(rendered, digest, lines):
            return self._record(request, ApprovalOutcome.INCOMPLETE_DISPLAY, issued_at)

        answer = self._wait_for_answer()
        if answer is _NO_PRESENTATION or answer is None:
            return self._record(request, ApprovalOutcome.TIMEOUT, issued_at)
        if answer != _APPROVE:
            return self._record(request, ApprovalOutcome.DENIED, issued_at)
        if not self._matches_remote_state(request):
            return self._record(request, ApprovalOutcome.STALE_REQUEST, issued_at)
        return self._record(request, ApprovalOutcome.APPROVED, issued_at)

    def execute_approved(
        self, request: CommandRequest, decision: ApprovalDecision
    ) -> ExecutionResult:
        """Run the exact reviewed command, once, under its own approval."""
        _require_request(request)
        if not isinstance(decision, ApprovalDecision):
            raise TypeError("An approval decision must be an ApprovalDecision.")
        if self._issued.get(decision.decision_id) is not decision:
            raise UnrecognisedApprovalError(
                "This approval was not issued by this broker."
            )
        if decision.request_digest != request.digest or decision.request != request:
            raise ApprovalMismatchError(
                "The request changed after review; it must be approved again."
            )
        if decision.outcome is not ApprovalOutcome.APPROVED:
            raise ApprovalNotApprovedError(
                f"The owner did not approve this command ({decision.outcome.value})."
            )
        if request.digest in self._consumed:
            raise ApprovalReplayError("This request was already executed.")
        if self._clock() - decision.decided_at > self._token_ttl_seconds:
            raise ApprovalExpiredError(
                "The approval expired; ask the owner to review the command again."
            )
        if not self._matches_remote_state(request):
            raise StaleApprovalError(
                "The pull request or its head commit changed after approval."
            )

        self._consumed.add(request.digest)
        return self._executor(request)

    def _present(self, digest: str, lines: tuple[str, ...]) -> object:
        try:
            return self._port.show(digest, lines)
        except Exception:  # noqa: BLE001 - any companion failure must deny
            return _NO_PRESENTATION

    def _wait_for_answer(self) -> object:
        try:
            return self._port.wait(self._timeout_seconds)
        except Exception:  # noqa: BLE001 - any companion failure must deny
            return _NO_PRESENTATION

    @staticmethod
    def _displayed_exactly(
        rendered: object, digest: str, lines: tuple[str, ...]
    ) -> bool:
        if not isinstance(rendered, RenderedRequest):
            return False
        return rendered.digest == digest and rendered.lines == tuple(lines)

    def _matches_remote_state(self, request: CommandRequest) -> bool:
        try:
            observed = self._facts()
        except Exception:  # noqa: BLE001 - unknown state must be treated as stale
            return False
        if not isinstance(observed, PullRequestFacts):
            return False
        return (
            observed.repository == request.repository
            and observed.head_repository == request.repository
            and observed.number == request.pr_number
            and observed.head_sha == request.starting_sha
            and observed.is_open
            and not observed.is_draft
        )

    def _record(
        self, request: CommandRequest, outcome: ApprovalOutcome, issued_at: float
    ) -> ApprovalDecision:
        decision = ApprovalDecision(
            decision_id=uuid.uuid4().hex,
            request=request,
            request_digest=request.digest,
            outcome=outcome,
            issued_at=issued_at,
            decided_at=self._clock(),
        )
        self._issued[decision.decision_id] = decision
        return decision


class _NoPresentation:
    """Sentinel: the companion failed before it could collect a decision."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<no presentation>"


_NO_PRESENTATION = _NoPresentation()
