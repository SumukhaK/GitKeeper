import subprocess
from dataclasses import dataclass, field

import pytest

from gitkeeper.queue.errors import ErrorCategory, GitHubAuthorizationError
from gitkeeper.relay.token import gh_token_provider

_COMMAND = ["gh", "auth", "token"]
_OPTIONS = {
    "capture_output": True,
    "text": True,
    "check": True,
    "shell": False,
    "timeout": 10,
}
_STDOUT_SECRET = "stdout-secret-must-not-leak"
_STDERR_SECRET = "stderr-secret-must-not-leak"


@dataclass
class FakeRun:
    outcomes: list[object]
    calls: list[tuple[list[str], dict[str, object]]] = field(default_factory=list)

    def __call__(self, command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, options))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, subprocess.CompletedProcess)
        return outcome


def _success(stdout: str, *, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(_COMMAND, 0, stdout=stdout, stderr=stderr)


def test_provider_invokes_gh_without_a_shell_and_a_ten_second_timeout() -> None:
    run = FakeRun([_success("token")])

    gh_token_provider(run=run)()

    assert run.calls == [(_COMMAND, _OPTIONS)]


def test_provider_returns_stripped_stdout() -> None:
    run = FakeRun([_success(" \n  token-value \t\n", stderr="ignored stderr")])

    assert gh_token_provider(run=run)() == "token-value"


def test_provider_reads_a_fresh_token_for_every_invocation() -> None:
    run = FakeRun([_success("first-token"), _success("second-token")])
    provider = gh_token_provider(run=run)

    assert provider() == "first-token"
    assert provider() == "second-token"
    assert run.calls == [(_COMMAND, _OPTIONS), (_COMMAND, _OPTIONS)]


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(
            FileNotFoundError("gh executable is unavailable"),
            id="missing-gh",
        ),
        pytest.param(
            subprocess.CalledProcessError(
                1,
                _COMMAND,
                output=_STDOUT_SECRET,
                stderr=_STDERR_SECRET,
            ),
            id="nonzero-exit",
        ),
        pytest.param(
            subprocess.TimeoutExpired(
                _COMMAND,
                10,
                output=_STDOUT_SECRET,
                stderr=_STDERR_SECRET,
            ),
            id="timeout",
        ),
        pytest.param(_success(""), id="empty-stdout"),
        pytest.param(_success(" \n\t"), id="whitespace-stdout"),
    ],
)
def test_provider_raises_redacted_authorization_error_for_every_failure(
    outcome: object,
) -> None:
    run = FakeRun([outcome])

    with pytest.raises(GitHubAuthorizationError) as raised:
        gh_token_provider(run=run)()

    error = raised.value
    assert error.category is ErrorCategory.github_authorization
    assert str(error) == "GitHub authorization failed."
    assert _STDOUT_SECRET not in repr(error)
    assert _STDERR_SECRET not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
