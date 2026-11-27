"""Strict response mappings for the synchronous relay GitHub reads."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from gitkeeper.queue.errors import GitHubResponseError

_CLOSING_REFERENCE = re.compile(r"\b(?:closes|fixes|resolves)\s+#([1-9]\d*)\b", re.IGNORECASE)
_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


@dataclass(frozen=True)
class PrDetails:
    number: int
    title: str
    body: str
    url: str
    head_sha: str
    head_ref: str
    head_repository: str
    author: str
    is_draft: bool
    is_open: bool
    is_merged: bool
    linked_issue: int | None


@dataclass(frozen=True)
class IssueDetails:
    number: int
    title: str
    body: str
    is_open: bool


_MAX_DIFF_BYTES = 2 * 1024 * 1024


class GitHubReads:
    def get_pr_details(self, repository: str, number: int) -> PrDetails:
        number = _number(number)
        payload, _ = self._get(
            f"{self._repository_url(repository)}/pulls/{number}",
            not_found_is_missing=True,
        )
        return pr_details(payload, number)

    def get_issue(self, repository: str, number: int) -> IssueDetails:
        number = _number(number)
        payload, _ = self._get(f"{self._repository_url(repository)}/issues/{number}")
        if isinstance(payload, Mapping) and "pull_request" in payload:
            raise GitHubResponseError()
        return _common(payload)

    def get_pr_diff(self, repository: str, number: int) -> str:
        number = _number(number)
        raw, _ = self._request(
            f"{self._repository_url(repository)}/pulls/{number}",
            method="GET",
            accept="application/vnd.github.diff",
            not_found_is_missing=True,
        )
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > _MAX_DIFF_BYTES:
            raise GitHubResponseError()
        try:
            return bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            raise GitHubResponseError() from None

    def get_check_annotations(self, repository: str, check_run_id: int) -> tuple[str, ...]:
        check_run_id = _number(check_run_id)
        payload, _ = self._get(
            f"{self._repository_url(repository)}/check-runs/{check_run_id}/annotations?per_page=50"
        )
        if not isinstance(payload, list):
            raise GitHubResponseError()
        return tuple(annotation_text(item) for item in payload[:50])

    def next_task_issue(self, repository: str) -> IssueDetails | None:
        base = self._repository_url(repository)
        tasks = self._collection(f"{base}/issues?state=open&labels=task&sort=created&direction=asc")
        if not tasks:
            return None
        pulls = self._collection(f"{base}/pulls?state=open&per_page=100")
        linked: set[int] = set()
        for pull in pulls:
            if not isinstance(pull, Mapping):
                raise GitHubResponseError()
            number = pull.get("number")
            if type(number) is not int or number < 1:
                raise GitHubResponseError()
            linked.update(linked_issues(pull.get("body")))
        for task in tasks:
            if isinstance(task, Mapping) and "pull_request" in task:
                continue
            issue = _common(task)
            if issue.number not in linked:
                return issue
        return None


def _number(value: object) -> int:
    if type(value) is not int or value < 1:
        raise GitHubResponseError()
    return value


def _text(value: object, *, nullable: bool = False) -> str:
    if value is None and nullable:
        return ""
    if not isinstance(value, str):
        raise GitHubResponseError()
    return value


def _common(item: object) -> IssueDetails:
    if not isinstance(item, Mapping):
        raise GitHubResponseError()
    number = item.get("number")
    if type(number) is not int or number < 1:
        raise GitHubResponseError()
    state = item.get("state")
    if state not in ("open", "closed"):
        raise GitHubResponseError()
    return IssueDetails(
        number,
        _text(item.get("title")),
        _text(item.get("body"), nullable=True),
        state == "open",
    )


def pr_details(item: object, number: int) -> PrDetails:
    issue = _common(item)
    if issue.number != number or not isinstance(item, Mapping):
        raise GitHubResponseError()
    head = item.get("head")
    user = item.get("user")
    if not isinstance(head, Mapping) or not isinstance(user, Mapping):
        raise GitHubResponseError()
    repository = head.get("repo")
    if not isinstance(repository, Mapping):
        raise GitHubResponseError()
    sha = head.get("sha")
    if not isinstance(sha, str) or _SHA_PATTERN.fullmatch(sha) is None:
        raise GitHubResponseError()
    draft, merged = item.get("draft"), item.get("merged")
    if type(draft) is not bool or type(merged) is not bool:
        raise GitHubResponseError()
    match = _CLOSING_REFERENCE.search(issue.body)
    return PrDetails(
        issue.number,
        issue.title,
        issue.body,
        _text(item.get("html_url")),
        sha,
        _text(head.get("ref")),
        _text(repository.get("full_name")),
        _text(user.get("login")),
        draft,
        issue.is_open,
        merged,
        int(match[1]) if match else None,
    )


def linked_issues(body: object) -> set[int]:
    text = _text(body, nullable=True)
    return {int(match[1]) for match in _CLOSING_REFERENCE.finditer(text)}


def annotation_text(item: object) -> str:
    if not isinstance(item, Mapping):
        raise GitHubResponseError()
    line = item.get("start_line")
    if type(line) is not int or line < 1:
        raise GitHubResponseError()
    return f"{_text(item.get('path'))}:{line} {_text(item.get('message'))}"
