"""Acquire a fresh GitHub token from the local GitHub CLI."""

import subprocess
from collections.abc import Callable

from gitkeeper.queue.errors import GitHubAuthorizationError


def gh_token_provider(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Callable[[], str]:
    def token() -> str:
        try:
            completed = run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=True,
                shell=False,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            completed = None

        if completed is None or not completed.stdout.strip():
            raise GitHubAuthorizationError()
        return completed.stdout.strip()

    return token
