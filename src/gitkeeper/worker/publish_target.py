"""Authoritative publish-target reads for the fast-forward publisher."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from gitkeeper.policy.patch_scope import RemotePrState
from gitkeeper.worker.models import PublishTarget

_SHA_LENGTHS = (40, 64)
_DEFAULT_BRANCHES = frozenset({"main", "master"})


class PublishTargetError(RuntimeError):
    """The authoritative pull request cannot be a safe publish target."""


@runtime_checkable
class PublishTargetPort(Protocol):
    def get_publish_target(self, repository: str, pr_number: int) -> PublishTarget: ...


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PublishTargetError(f"The authoritative {field} is unavailable.")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PublishTargetError(f"The authoritative {field} is unavailable.")
    return value


class GitHubPublishTargetAdapter:
    """Read one PR payload and require a same-repository head branch."""

    def __init__(
        self,
        fetch_pull_request: Callable[[str, int], Mapping[str, object]],
        *,
        default_branch: str | None = None,
    ) -> None:
        if not callable(fetch_pull_request):
            raise TypeError("fetch_pull_request must be callable.")
        if default_branch is not None and (
            not isinstance(default_branch, str) or not default_branch
        ):
            raise TypeError("default_branch must be a non-empty string or None.")
        self._fetch = fetch_pull_request
        self._default_branch = default_branch

    def get_publish_target(self, repository: str, pr_number: int) -> PublishTarget:
        try:
            payload = self._fetch(repository, pr_number)
        except Exception:  # noqa: BLE001 - any read failure must block
            raise PublishTargetError(
                "The authoritative pull request could not be read."
            ) from None
        if not isinstance(payload, Mapping):
            raise PublishTargetError(
                "The authoritative pull request could not be read."
            )
        head = _mapping(payload.get("head"), "head")
        head_repository_payload = _mapping(head.get("repo"), "head repository")
        user = _mapping(payload.get("user"), "author")
        branch = _text(head.get("ref"), "head branch")
        head_repository = _text(
            head_repository_payload.get("full_name"), "head repository"
        )
        head_sha = _text(head.get("sha"), "head SHA")
        author = _text(user.get("login"), "author")
        if len(head_sha) not in _SHA_LENGTHS or any(
            character not in "0123456789abcdef" for character in head_sha
        ):
            raise PublishTargetError("The authoritative head SHA is invalid.")
        if head_repository.casefold() != repository.casefold():
            raise PublishTargetError(
                "A fork pull request cannot be published to."
            )
        state = payload.get("state")
        draft = payload.get("draft", False)
        if not isinstance(draft, bool):
            raise PublishTargetError("The authoritative draft state is invalid.")
        is_default = branch.casefold() in _DEFAULT_BRANCHES or (
            self._default_branch is not None
            and branch.casefold() == self._default_branch.casefold()
        )
        if is_default:
            raise PublishTargetError(
                "The default branch cannot be a publish target."
            )
        return PublishTarget(
            repository=repository,
            pr_number=pr_number,
            branch=branch,
            head_repository=head_repository,
            is_default_branch=is_default,
            remote=RemotePrState(
                number=pr_number,
                repository=repository,
                author=author,
                head_sha=head_sha,
                is_open=state == "open",
                is_draft=draft,
                head_repository=head_repository,
            ),
        )
