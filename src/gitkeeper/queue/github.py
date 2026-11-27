"""GitHub control-plane REST adapter.

Every public operation issues the minimum HTTP requests the endpoint requires
and raises only the fixed queue error categories. Backoff belongs to the
poller, so nothing is retried or logged here, and no response detail, URL, or
credential ever reaches an error message. Worker-label writes converge to a
desired state instead of blindly mutating; relay writes validate their inputs
and response shapes at the boundary.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from typing import NoReturn, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from gitkeeper.queue.errors import (
    GitHubAuthorizationError,
    GitHubResponseError,
    GitHubUnavailable,
    MergeRefused,
    PullRequestNotFound,
)
from gitkeeper.queue.models import (
    CheckConclusion,
    CheckResult,
    CheckState,
    PilotConfig,
    PullRequestSnapshot,
    WorkerLabel,
)
from gitkeeper.queue.readiness_adapter import parse_pilot_config
from gitkeeper.relay.github_reads import GitHubReads

_ACCEPT = "application/vnd.github+json"
_USER_AGENT = "GitKeeper"
_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_CONCLUSIONS = {
    "success": CheckConclusion.success,
    "failure": CheckConclusion.failure,
    "neutral": CheckConclusion.neutral,
    "cancelled": CheckConclusion.cancelled,
    "skipped": CheckConclusion.skipped,
    "timed_out": CheckConclusion.timed_out,
    "action_required": CheckConclusion.action_required,
}
_WORKER_LABEL_NAMES = frozenset(label.value for label in WorkerLabel)


class GitHubControlPlane(Protocol):
    """GitHub surface consumed by the queue poller.

    The concrete client implements both the read/config half and worker-label
    desired-state convergence.
    """

    def list_open_pull_requests(
        self, repository: str
    ) -> list[PullRequestSnapshot]: ...

    def get_pull_request(
        self, repository: str, pr_number: int
    ) -> PullRequestSnapshot: ...

    def get_check_state(
        self,
        repository: str,
        pr_number: int,
        head_sha: str,
        required_check_ids: tuple[str, ...],
    ) -> CheckState: ...

    def get_default_branch_config(self, repository: str) -> PilotConfig: ...

    def get_worker_labels(
        self, repository: str, pr_number: int
    ) -> tuple[WorkerLabel, ...]: ...

    def set_worker_label(
        self, repository: str, pr_number: int, label: WorkerLabel | None
    ) -> None: ...


@dataclass(frozen=True)
class _CheckRun:
    run_id: int
    name: str
    head_sha: str
    conclusion: str | None
    suite_id: int | None


class GitHubRestClient(GitHubReads):
    """GitHub REST reads, queue-label writes, and relay writes."""

    def __init__(
        self,
        api_url: str,
        token_provider: Callable[[], str],
        opener: Callable[..., object] = urlopen,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._token_provider = token_provider
        self._opener = opener
        self._timeout_seconds = timeout_seconds

    def _repository_url(self, repository: str) -> str:
        _repository_parts(repository)
        return f"{self._api_url}/repos/{repository}"

    def _collection(self, url: str) -> list[object]:
        visited: set[str] = set()
        entries: list[object] = []
        while url is not None:
            _guard_page(url, visited)
            payload, headers = self._get(url)
            if not isinstance(payload, list):
                raise GitHubResponseError()
            entries.extend(payload)
            url = _next_link(_header(headers, "Link"))
        return entries

    def list_open_pull_requests(
        self, repository: str
    ) -> list[PullRequestSnapshot]:
        owner, name = _repository_parts(repository)
        url: str | None = (
            f"{self._api_url}/repos/{owner}/{name}/pulls?state=open&per_page=100"
        )
        visited: set[str] = set()
        snapshots: list[PullRequestSnapshot] = []
        while url is not None:
            _guard_page(url, visited)
            observed_at = _now()
            payload, headers = self._get(url)
            if not isinstance(payload, list):
                raise GitHubResponseError()
            for item in payload:
                snapshots.append(_snapshot(item, repository, observed_at))
            url = _next_link(_header(headers, "Link"))
        return snapshots

    def get_pull_request(
        self, repository: str, pr_number: int
    ) -> PullRequestSnapshot:
        owner, name = _repository_parts(repository)
        number = _pr_number(pr_number)
        observed_at = _now()
        payload, _ = self._get(
            f"{self._api_url}/repos/{owner}/{name}/pulls/{number}",
            not_found_is_missing=True,
        )
        return _snapshot(payload, repository, observed_at)

    def get_check_state(
        self,
        repository: str,
        pr_number: int,
        head_sha: str,
        required_check_ids: tuple[str, ...],
    ) -> CheckState:
        owner, name = _repository_parts(repository)
        _pr_number(pr_number)
        sha = _requested_sha(head_sha)
        required = _required_check_ids(required_check_ids)
        url: str | None = (
            f"{self._api_url}/repos/{owner}/{name}/commits/{sha}"
            "/check-runs?per_page=100"
        )
        visited: set[str] = set()
        runs: list[_CheckRun] = []
        while url is not None:
            _guard_page(url, visited)
            payload, headers = self._get(url)
            if not isinstance(payload, Mapping):
                raise GitHubResponseError()
            page_runs = payload.get("check_runs")
            if not isinstance(page_runs, list):
                raise GitHubResponseError()
            runs.extend(_check_run(item) for item in page_runs)
            url = _next_link(_header(headers, "Link"))
        if len({run.run_id for run in runs}) != len(runs):
            raise GitHubResponseError()
        suites: dict[int, str | None] = {}
        results: list[CheckResult] = []
        for run in runs:
            if run.name not in required:
                continue
            if run.head_sha != sha:
                conclusion = CheckConclusion.stale
            else:
                conclusion = _CONCLUSIONS.get(run.conclusion)
                if conclusion is None:
                    conclusion = self._undetermined_conclusion(
                        owner, name, run, suites
                    )
            try:
                results.append(
                    CheckResult(
                        check_id=run.name,
                        check_run_id=str(run.run_id),
                        head_sha=run.head_sha,
                        conclusion=conclusion,
                    )
                )
            except (TypeError, ValueError):
                raise GitHubResponseError() from None
        try:
            return CheckState(
                head_sha=sha,
                required_check_ids=required_check_ids,
                results=tuple(results),
            )
        except (TypeError, ValueError):
            raise GitHubResponseError() from None

    def get_default_branch_config(self, repository: str) -> PilotConfig:
        owner, name = _repository_parts(repository)
        metadata, _ = self._get(f"{self._api_url}/repos/{owner}/{name}")
        if not isinstance(metadata, Mapping):
            raise GitHubResponseError()
        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise GitHubResponseError()
        ref, _ = self._get(
            f"{self._api_url}/repos/{owner}/{name}/git/ref/heads/"
            f"{quote(default_branch, safe='/')}"
        )
        commit_sha = _ref_commit_sha(ref)
        contents, _ = self._get(
            f"{self._api_url}/repos/{owner}/{name}/contents/"
            f".github/gitkeeper.json?ref={commit_sha}"
        )
        try:
            return parse_pilot_config(
                _contents_json(contents), default_branch, commit_sha
            )
        except (TypeError, ValueError):
            raise GitHubResponseError() from None

    def post_pr_comment(self, repository: str, pr_number: int, body: str) -> str:
        if not isinstance(body, str) or not 1 <= len(body) <= 60_000:
            raise ValueError("A comment body must contain 1 to 60,000 characters.")
        number = _pr_number(pr_number)
        raw, _ = self._write(
            f"{self._repository_url(repository)}/issues/{number}/comments",
            method="POST",
            payload={"body": body},
        )
        return _response_string(_decode_json(raw), "html_url")

    def merge_pull_request(
        self, repository: str, pr_number: int, *, expected_head_sha: str
    ) -> str:
        number = _pr_number(pr_number)
        head_sha = _requested_sha(expected_head_sha)
        raw, _ = self._write(
            f"{self._repository_url(repository)}/pulls/{number}/merge",
            method="PUT",
            payload={"merge_method": "squash", "sha": head_sha},
            merge_refused=True,
        )
        return _requested_sha(_response_string(_decode_json(raw), "sha"))

    def get_worker_labels(
        self, repository: str, pr_number: int
    ) -> tuple[WorkerLabel, ...]:
        return self._worker_label_entries(repository, pr_number)

    def set_worker_label(
        self, repository: str, pr_number: int, label: WorkerLabel | None
    ) -> None:
        if label is not None and not isinstance(label, WorkerLabel):
            raise GitHubResponseError()
        current = self._worker_label_entries(repository, pr_number)
        desired: tuple[WorkerLabel, ...] = () if label is None else (label,)
        if current == desired:
            return
        # More than one GitKeeper label has no defined convergence target.
        if len(current) > 1:
            raise GitHubResponseError()
        owner, name = _repository_parts(repository)
        number = _pr_number(pr_number)
        labels_url = (
            f"{self._api_url}/repos/{owner}/{name}/issues/{number}/labels"
        )
        for conflicting in current:
            # Labels are per-PR queue projections, so delete the assignment on
            # this issue rather than the repository-wide label definition URL.
            self._write(
                f"{labels_url}/{quote(conflicting.value, safe='')}",
                method="DELETE",
            )
        if label is not None:
            self._write(
                labels_url,
                method="POST",
                payload={"labels": [label.value]},
            )
        final = self._worker_label_entries(repository, pr_number)
        if final != desired:
            raise GitHubResponseError()

    def _worker_label_entries(
        self, repository: object, pr_number: object
    ) -> tuple[WorkerLabel, ...]:
        owner, name = _repository_parts(repository)
        number = _pr_number(pr_number)
        url: str | None = (
            f"{self._api_url}/repos/{owner}/{name}/issues/{number}"
            "/labels?per_page=100"
        )
        visited: set[str] = set()
        entries: list[WorkerLabel] = []
        seen: set[WorkerLabel] = set()
        while url is not None:
            _guard_page(url, visited)
            payload, headers = self._get(url)
            if not isinstance(payload, list):
                raise GitHubResponseError()
            for item in payload:
                label = _worker_label_entry(item)
                if label is None:
                    continue
                if label in seen:
                    raise GitHubResponseError()
                seen.add(label)
                entries.append(label)
            url = _next_link(_header(headers, "Link"))
        return tuple(entries)

    def _get(
        self, url: str, *, not_found_is_missing: bool = False
    ) -> tuple[object, object]:
        payload, headers = self._request(
            url, method="GET", not_found_is_missing=not_found_is_missing
        )
        return _decode_json(payload), headers

    def _write(
        self,
        url: str,
        *,
        method: str,
        payload: object | None = None,
        merge_refused: bool = False,
    ) -> tuple[object, object]:
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._request(
            url, method=method, body=body, merge_refused=merge_refused
        )

    def _request(
        self,
        url: str,
        *,
        method: str,
        body: bytes | None = None,
        not_found_is_missing: bool = False,
        accept: str | None = None,
        merge_refused: bool = False,
    ) -> tuple[object, object]:
        headers = self._headers()
        if accept is not None:
            headers["Accept"] = accept
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            response = self._opener(request, timeout=self._timeout_seconds)
        except HTTPError as error:
            self._raise_http_error(error, not_found_is_missing, merge_refused)
        except (URLError, TimeoutError, ConnectionError):
            raise GitHubUnavailable() from None
        return _read_body(response), getattr(response, "headers", None)

    def _headers(self) -> dict[str, str]:
        token = self._token_provider()
        if not isinstance(token, str) or not token.strip():
            raise GitHubAuthorizationError()
        token = token.strip()
        if any(character.isspace() for character in token):
            raise GitHubAuthorizationError()
        return {
            "Accept": _ACCEPT,
            "Authorization": f"Bearer {token}",
            "User-Agent": _USER_AGENT,
        }

    def _raise_http_error(
        self,
        error: HTTPError,
        not_found_is_missing: bool,
        merge_refused: bool = False,
    ) -> NoReturn:
        code = getattr(error, "code", None)
        if code in (401, 403):
            raise GitHubAuthorizationError() from None
        if code == 404:
            if not_found_is_missing:
                raise PullRequestNotFound() from None
            raise GitHubResponseError() from None
        if merge_refused and code in (405, 409):
            raise MergeRefused() from None
        if code == 429:
            raise GitHubUnavailable() from None
        if isinstance(code, int) and 500 <= code <= 599:
            raise GitHubUnavailable() from None
        raise GitHubResponseError() from None

    def _undetermined_conclusion(
        self,
        owner: str,
        name: str,
        run: _CheckRun,
        suites: dict[int, str | None],
    ) -> CheckConclusion:
        if run.suite_id is None:
            raise GitHubResponseError()
        if run.suite_id not in suites:
            payload, _ = self._get(
                f"{self._api_url}/repos/{owner}/{name}"
                f"/check-suites/{run.suite_id}"
            )
            if not isinstance(payload, Mapping):
                raise GitHubResponseError()
            conclusion = payload.get("conclusion")
            if conclusion is not None and not isinstance(conclusion, str):
                raise GitHubResponseError()
            suites[run.suite_id] = conclusion
        if suites[run.suite_id] == "failure":
            return CheckConclusion.startup_failure
        # ``CheckConclusion`` has no ``blocked`` member; ``action_required`` is
        # the conservative non-deterministic evidence value. Any conclusion
        # other than success/failure makes ``CheckState.outcome`` blocked.
        return CheckConclusion.action_required


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _guard_page(url: str, visited: set[str]) -> None:
    if url in visited:
        raise GitHubResponseError()
    visited.add(url)


def _header(headers: object, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if isinstance(value, str):
            return value
    items = getattr(headers, "items", None)
    if callable(items):
        for key, value in items():
            if (
                isinstance(key, str)
                and key.lower() == name.lower()
                and isinstance(value, str)
            ):
                return value
    return None


def _next_link(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    for part in value.split(","):
        target, _, parameters = part.partition(";")
        target = target.strip()
        if not (target.startswith("<") and target.endswith(">")):
            continue
        for parameter in parameters.split(";"):
            key, _, rel = parameter.partition("=")
            if key.strip().lower() != "rel":
                continue
            if rel.strip().strip('"').lower() == "next":
                return target[1:-1]
    return None


def _read_body(response: object) -> object:
    read = getattr(response, "read", None)
    if not callable(read):
        raise GitHubResponseError()
    try:
        if hasattr(response, "__enter__") and hasattr(response, "__exit__"):
            with response as stream:
                return stream.read()
        return read()
    except (OSError, HTTPException):
        raise GitHubUnavailable() from None


def _decode_json(raw: object) -> object:
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            raise GitHubResponseError() from None
    elif isinstance(raw, str):
        text = raw
    else:
        raise GitHubResponseError()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        raise GitHubResponseError() from None


def _response_string(payload: object, field: str) -> str:
    if not isinstance(payload, Mapping):
        raise GitHubResponseError()
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise GitHubResponseError()
    return value


def _worker_label_entry(item: object) -> WorkerLabel | None:
    """Return a GitKeeper label, or ``None`` when the label is unrelated."""
    if not isinstance(item, Mapping):
        raise GitHubResponseError()
    name = item.get("name")
    if not isinstance(name, str):
        raise GitHubResponseError()
    if name not in _WORKER_LABEL_NAMES:
        return None
    return WorkerLabel(name)


def _repository_parts(repository: object) -> tuple[str, str]:
    if not isinstance(repository, str):
        raise GitHubResponseError()
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise GitHubResponseError()
    return owner, name


def _pr_number(pr_number: object) -> int:
    if type(pr_number) is not int or pr_number < 1:
        raise GitHubResponseError()
    return pr_number


def _requested_sha(head_sha: object) -> str:
    if not isinstance(head_sha, str) or _SHA_PATTERN.fullmatch(head_sha) is None:
        raise GitHubResponseError()
    return head_sha


def _required_check_ids(
    required_check_ids: object,
) -> frozenset[str]:
    if not isinstance(required_check_ids, tuple):
        raise GitHubResponseError()
    required = set()
    for check_id in required_check_ids:
        if not isinstance(check_id, str) or not check_id.strip():
            raise GitHubResponseError()
        required.add(check_id)
    if len(required) != len(required_check_ids):
        raise GitHubResponseError()
    return frozenset(required)


def _snapshot(
    item: object, repository: str, observed_at: datetime
) -> PullRequestSnapshot:
    if not isinstance(item, Mapping):
        raise GitHubResponseError()
    number = item.get("number")
    if type(number) is not int:
        raise GitHubResponseError()
    author: str | None = None
    user = item.get("user")
    if user is not None:
        if not isinstance(user, Mapping):
            raise GitHubResponseError()
        login = user.get("login")
        if login is not None and not isinstance(login, str):
            raise GitHubResponseError()
        author = login
    head = item.get("head")
    if not isinstance(head, Mapping):
        raise GitHubResponseError()
    head_sha = head.get("sha")
    if not isinstance(head_sha, str):
        raise GitHubResponseError()
    head_repository: str | None = None
    head_repo = head.get("repo")
    if head_repo is not None:
        if not isinstance(head_repo, Mapping):
            raise GitHubResponseError()
        full_name = head_repo.get("full_name")
        if full_name is not None and not isinstance(full_name, str):
            raise GitHubResponseError()
        head_repository = full_name
    state = item.get("state")
    if not isinstance(state, str):
        raise GitHubResponseError()
    draft = item.get("draft", False)
    if not isinstance(draft, bool):
        raise GitHubResponseError()
    try:
        return PullRequestSnapshot(
            repository=repository,
            pr_number=number,
            author=author,
            head_repository=head_repository,
            head_sha=head_sha,
            is_open=state == "open",
            is_draft=draft,
            observed_at=observed_at,
        )
    except (TypeError, ValueError):
        raise GitHubResponseError() from None


def _check_run(item: object) -> _CheckRun:
    if not isinstance(item, Mapping):
        raise GitHubResponseError()
    run_id = item.get("id")
    if type(run_id) is not int or run_id <= 0:
        raise GitHubResponseError()
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise GitHubResponseError()
    head_sha = item.get("head_sha")
    if not isinstance(head_sha, str) or _SHA_PATTERN.fullmatch(head_sha) is None:
        raise GitHubResponseError()
    conclusion = item.get("conclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        raise GitHubResponseError()
    suite_id: int | None = None
    suite = item.get("check_suite")
    if suite is not None:
        if not isinstance(suite, Mapping):
            raise GitHubResponseError()
        raw_suite_id = suite.get("id")
        if type(raw_suite_id) is not int or raw_suite_id <= 0:
            raise GitHubResponseError()
        suite_id = raw_suite_id
    return _CheckRun(
        run_id=run_id,
        name=name,
        head_sha=head_sha,
        conclusion=conclusion,
        suite_id=suite_id,
    )


def _ref_commit_sha(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise GitHubResponseError()
    target = payload.get("object")
    if not isinstance(target, Mapping):
        raise GitHubResponseError()
    if "type" in target and target["type"] != "commit":
        raise GitHubResponseError()
    sha = target.get("sha")
    if not isinstance(sha, str) or _SHA_PATTERN.fullmatch(sha) is None:
        raise GitHubResponseError()
    return sha


def _contents_json(payload: object) -> object:
    if not isinstance(payload, Mapping):
        raise GitHubResponseError()
    if payload.get("encoding") != "base64":
        raise GitHubResponseError()
    content = payload.get("content")
    if not isinstance(content, str):
        raise GitHubResponseError()
    try:
        decoded = base64.b64decode("".join(content.split()), validate=True)
    except (binascii.Error, ValueError):
        raise GitHubResponseError() from None
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        raise GitHubResponseError() from None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        raise GitHubResponseError() from None
