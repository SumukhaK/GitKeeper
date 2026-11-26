"""Read-only GitHub control-plane adapter tests.

The adapter is fenced to fixed queue error categories and performs exactly one
HTTP request per intended endpoint read, because the poller owns backoff.
These tests pin the wire mapping, pagination, per-page observation timestamps,
error translation, and default-branch configuration decoding without any
network access.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

import pytest

from gitkeeper.queue import (
    CheckConclusion,
    CheckOutcome,
    GitHubAuthorizationError,
    GitHubResponseError,
    GitHubUnavailable,
    PilotConfig,
    PullRequestNotFound,
    PullRequestSnapshot,
)
from gitkeeper.queue.github import GitHubControlPlane, GitHubRestClient

API = "https://api.github.com"
REPOSITORY = "SumukhaK/Setu-mobile"
OTHER_REPOSITORY = "SumukhaK/Setu-web"
PR_NUMBER = 42
HEAD_SHA = "a153271a18241b3cf5b9d5ed7025916c6b11e9f8"
OTHER_SHA = "b153271a18241b3cf5b9d5ed7025916c6b11e9f8"
COMMIT_SHA = "c153271a18241b3cf5b9d5ed7025916c6b11e9f8"
BLOB_SHA = "d153271a18241b3cf5b9d5ed7025916c6b11e9f8"
TOKEN = "ghp_test_token"
DEFAULT_BRANCH = "main"
RUN_ID = 901
SUITE_ID = 77
REQUIRED = ("ci/lint", "ci/test")

AUTHORIZATION_MESSAGE = "GitHub authorization failed."
UNAVAILABLE_MESSAGE = "GitHub is unavailable."
RESPONSE_MESSAGE = "GitHub returned an invalid response."
NOT_FOUND_MESSAGE = "The pull request was not found."


class _Response:
    """Minimal stand-in for an ``http.client.HTTPResponse``."""

    def __init__(self, body, headers=None, status=200):
        if isinstance(body, bytes):
            self.body = body
        else:
            self.body = json.dumps(body).encode("utf-8")
        self.headers = headers if headers is not None else {}
        self.status = status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _PlainResponse:
    """A response without context-manager support."""

    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def read(self):
        return self.body


class _Opener:
    """Records every opener call and replays queued results or exceptions."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append(
            {
                "request": request,
                "url": request.full_url,
                "timeout": timeout,
                "called_at": datetime.now(timezone.utc),
            }
        )
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _client(*results, token=TOKEN, timeout_seconds=20.0):
    opener = _Opener(*results)
    client = GitHubRestClient(
        API,
        lambda: token,
        opener=opener,
        timeout_seconds=timeout_seconds,
    )
    return client, opener


def _run(coroutine):
    return asyncio.run(coroutine)


def _pull(
    number,
    *,
    sha=HEAD_SHA,
    state="open",
    draft=False,
    author="SumukhaK",
    head_repository=REPOSITORY,
):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "user": None if author is None else {"login": author},
        "head": {
            "sha": sha,
            "repo": (
                None
                if head_repository is None
                else {"full_name": head_repository}
            ),
        },
    }


def _check_run(
    name,
    *,
    run_id=RUN_ID,
    sha=HEAD_SHA,
    conclusion="success",
    suite_id=SUITE_ID,
):
    return {
        "id": run_id,
        "name": name,
        "head_sha": sha,
        "conclusion": conclusion,
        "check_suite": {"id": suite_id},
    }


def _check_page(*runs):
    return {"total_count": len(runs), "check_runs": list(runs)}


def _checks_url():
    return (
        f"{API}/repos/{REPOSITORY}/commits/{HEAD_SHA}/check-runs?per_page=100"
    )


def _config_payload(**overrides):
    payload = {
        "worker_enabled": True,
        "polling_preferences": {"mode": "adaptive"},
        "motion_preference": "reduced",
        "deterministic_check_ids": ["ci/lint", "ci/test"],
    }
    payload.update(overrides)
    return payload


def _contents_payload(body, *, content=None, encoding="base64"):
    if content is None:
        content = base64.b64encode(json.dumps(body).encode("utf-8")).decode(
            "ascii"
        )
    return {
        "type": "file",
        "name": "gitkeeper.json",
        "path": ".github/gitkeeper.json",
        "sha": BLOB_SHA,
        "encoding": encoding,
        "content": content,
    }


def _http_error(status, *, url=f"{API}/repos/{REPOSITORY}/pulls"):
    return HTTPError(url, status, "boom", {"X-Secret": TOKEN}, None)


# --- tokens and shared request shape -------------------------------------


@pytest.mark.parametrize("token", [None, "", "   ", "\n\t "])
def test_missing_or_blank_token_is_authorization_failure(token):
    client, opener = _client(_Response([]), token=token)

    with pytest.raises(GitHubAuthorizationError) as info:
        _run(client.list_open_pull_requests(REPOSITORY))

    assert str(info.value) == AUTHORIZATION_MESSAGE
    assert info.value.args == (AUTHORIZATION_MESSAGE,)
    assert opener.calls == []


def test_surrounding_token_whitespace_is_trimmed():
    client, opener = _client(_Response([]), token=f"  {TOKEN}\n")

    _run(client.list_open_pull_requests(REPOSITORY))

    headers = {
        key.lower(): value
        for key, value in opener.calls[0]["request"].header_items()
    }
    assert headers["authorization"] == f"Bearer {TOKEN}"


def test_requests_carry_fixed_headers_and_timeout():
    client, opener = _client(_Response([]), timeout_seconds=7.5)

    _run(client.list_open_pull_requests(REPOSITORY))

    (call,) = opener.calls
    assert call["url"] == (
        f"{API}/repos/{REPOSITORY}/pulls?state=open&per_page=100"
    )
    assert call["timeout"] == 7.5
    assert call["request"].get_method() == "GET"
    assert {
        key.lower(): value
        for key, value in call["request"].header_items()
    } == {
        "accept": "application/vnd.github+json",
        "authorization": f"Bearer {TOKEN}",
        "user-agent": "GitKeeper",
    }


def test_default_timeout_is_twenty_seconds():
    client, opener = _client(_Response([]))

    _run(client.list_open_pull_requests(REPOSITORY))

    assert opener.calls[0]["timeout"] == 20.0


def test_plain_response_without_context_manager_is_supported():
    client, _ = _client(_PlainResponse([_pull(PR_NUMBER)]))

    snapshots = _run(client.list_open_pull_requests(REPOSITORY))

    assert [snapshot.pr_number for snapshot in snapshots] == [PR_NUMBER]


# --- list pull requests --------------------------------------------------


def test_list_open_pull_requests_maps_fields_and_follows_next_links():
    next_url = (
        f"{API}/repos/{REPOSITORY}/pulls?state=open&per_page=100&page=2"
    )
    link = f'<{next_url}>; rel="next", <{next_url}>; rel="last"'
    first_page = [
        _pull(PR_NUMBER),
        _pull(7, sha=OTHER_SHA, draft=True, author=None, head_repository=None),
    ]
    second_page = [_pull(3, state="closed")]
    client, opener = _client(
        _Response(first_page, {"Link": link}),
        _Response(second_page),
    )

    snapshots = _run(client.list_open_pull_requests(REPOSITORY))

    assert [snapshot.pr_number for snapshot in snapshots] == [
        PR_NUMBER,
        7,
        3,
    ]
    assert [call["url"] for call in opener.calls] == [
        f"{API}/repos/{REPOSITORY}/pulls?state=open&per_page=100",
        next_url,
    ]
    assert all(
        isinstance(snapshot, PullRequestSnapshot) for snapshot in snapshots
    )
    opened, forked, closed = snapshots
    assert opened.repository == REPOSITORY
    assert opened.author == "SumukhaK"
    assert opened.head_repository == REPOSITORY
    assert opened.head_sha == HEAD_SHA
    assert opened.is_open is True
    assert opened.is_draft is False
    assert opened.same_repository is True
    assert forked.author is None
    assert forked.head_repository is None
    assert forked.head_sha == OTHER_SHA
    assert forked.is_draft is True
    assert closed.is_open is False


def test_list_observation_time_is_captured_before_each_page_request():
    next_url = (
        f"{API}/repos/{REPOSITORY}/pulls?state=open&per_page=100&page=2"
    )
    client, opener = _client(
        _Response(
            [_pull(PR_NUMBER), _pull(7)],
            {"Link": f'<{next_url}>; rel="next"'},
        ),
        _Response([_pull(3)]),
    )

    snapshots = _run(client.list_open_pull_requests(REPOSITORY))

    assert snapshots[0].observed_at == snapshots[1].observed_at
    assert snapshots[0].observed_at <= opener.calls[0]["called_at"]
    assert snapshots[2].observed_at <= opener.calls[1]["called_at"]
    assert snapshots[2].observed_at >= opener.calls[0]["called_at"]


@pytest.mark.parametrize(
    "page",
    [
        {"message": "not a list"},
        [_pull(PR_NUMBER), "not an object"],
        [{"number": PR_NUMBER}],
        [_pull(PR_NUMBER, sha="not-a-sha")],
        [_pull(PR_NUMBER, state=None)],
        [_pull("42")],
    ],
)
def test_list_rejects_malformed_pages(page):
    client, _ = _client(_Response(page))

    with pytest.raises(GitHubResponseError) as info:
        _run(client.list_open_pull_requests(REPOSITORY))

    assert str(info.value) == RESPONSE_MESSAGE


def test_list_rejects_non_boolean_draft():
    item = _pull(PR_NUMBER)
    item["draft"] = "true"
    client, _ = _client(_Response([item]))

    with pytest.raises(GitHubResponseError):
        _run(client.list_open_pull_requests(REPOSITORY))


# --- single pull request -------------------------------------------------


def test_get_pull_request_maps_single_pull_request_facts():
    client, opener = _client(_Response(_pull(PR_NUMBER, draft=True)))

    snapshot = _run(client.get_pull_request(REPOSITORY, PR_NUMBER))

    assert opener.calls[0]["url"] == (
        f"{API}/repos/{REPOSITORY}/pulls/{PR_NUMBER}"
    )
    assert snapshot.repository == REPOSITORY
    assert snapshot.pr_number == PR_NUMBER
    assert snapshot.author == "SumukhaK"
    assert snapshot.head_repository == REPOSITORY
    assert snapshot.head_sha == HEAD_SHA
    assert snapshot.is_open is True
    assert snapshot.is_draft is True


def test_get_pull_request_maps_absent_nested_fields_to_none():
    client, _ = _client(
        _Response(
            {
                "number": PR_NUMBER,
                "state": "open",
                "head": {"sha": HEAD_SHA},
            }
        )
    )

    snapshot = _run(client.get_pull_request(REPOSITORY, PR_NUMBER))

    assert snapshot.author is None
    assert snapshot.head_repository is None
    assert snapshot.is_draft is False


def test_single_pull_request_404_is_not_found():
    client, _ = _client(
        _http_error(404, url=f"{API}/repos/{REPOSITORY}/pulls/{PR_NUMBER}")
    )

    with pytest.raises(PullRequestNotFound) as info:
        _run(client.get_pull_request(REPOSITORY, PR_NUMBER))

    assert type(info.value) is PullRequestNotFound
    assert isinstance(info.value, GitHubResponseError)
    assert str(info.value) == NOT_FOUND_MESSAGE


def test_list_404_is_response_error():
    client, _ = _client(_http_error(404))

    with pytest.raises(GitHubResponseError) as info:
        _run(client.list_open_pull_requests(REPOSITORY))

    assert type(info.value) is GitHubResponseError
    assert str(info.value) == RESPONSE_MESSAGE


def test_check_state_404_is_response_error():
    client, _ = _client(_http_error(404, url=_checks_url()))

    with pytest.raises(GitHubResponseError) as info:
        _run(
            client.get_check_state(
                REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
            )
        )

    assert type(info.value) is GitHubResponseError
    assert str(info.value) == RESPONSE_MESSAGE


def test_config_404_is_response_error():
    client, _ = _client(_http_error(404, url=f"{API}/repos/{REPOSITORY}"))

    with pytest.raises(GitHubResponseError) as info:
        _run(client.get_default_branch_config(REPOSITORY))

    assert type(info.value) is GitHubResponseError
    assert str(info.value) == RESPONSE_MESSAGE


def test_check_suite_404_is_response_error():
    client, _ = _client(
        _Response(_check_page(_check_run(REQUIRED[0], conclusion=None))),
        _http_error(
            404,
            url=f"{API}/repos/{REPOSITORY}/check-suites/{SUITE_ID}",
        ),
    )

    with pytest.raises(GitHubResponseError) as info:
        _run(
            client.get_check_state(
                REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
            )
        )

    assert type(info.value) is GitHubResponseError
    assert str(info.value) == RESPONSE_MESSAGE


@pytest.mark.parametrize(
    ("repository", "pr_number"),
    [("not-a-repository", PR_NUMBER), (REPOSITORY, 0), (REPOSITORY, True)],
)
def test_invalid_arguments_are_rejected_before_any_request(
    repository, pr_number
):
    client, opener = _client(_Response(_pull(PR_NUMBER)))

    with pytest.raises(GitHubResponseError):
        _run(client.get_pull_request(repository, pr_number))

    assert opener.calls == []


# --- transport failures --------------------------------------------------


@pytest.mark.parametrize(
    ("status", "error_type", "message"),
    [
        (401, GitHubAuthorizationError, AUTHORIZATION_MESSAGE),
        (403, GitHubAuthorizationError, AUTHORIZATION_MESSAGE),
        (429, GitHubUnavailable, UNAVAILABLE_MESSAGE),
        (500, GitHubUnavailable, UNAVAILABLE_MESSAGE),
        (503, GitHubUnavailable, UNAVAILABLE_MESSAGE),
        (400, GitHubResponseError, RESPONSE_MESSAGE),
        (422, GitHubResponseError, RESPONSE_MESSAGE),
    ],
)
def test_http_status_translation(status, error_type, message):
    client, _ = _client(_http_error(status))

    with pytest.raises(error_type) as info:
        _run(client.list_open_pull_requests(REPOSITORY))

    assert type(info.value) is error_type
    assert str(info.value) == message


@pytest.mark.parametrize(
    "error",
    [
        URLError("connection refused"),
        TimeoutError("timed out"),
        ConnectionResetError("reset"),
    ],
)
def test_network_failures_are_unavailable(error):
    client, _ = _client(error)

    with pytest.raises(GitHubUnavailable) as info:
        _run(client.get_pull_request(REPOSITORY, PR_NUMBER))

    assert str(info.value) == UNAVAILABLE_MESSAGE
    assert info.value.args == (UNAVAILABLE_MESSAGE,)


@pytest.mark.parametrize(
    "body",
    [
        b"{not json",
        b"",
        b"\xff\xfe\x00",
    ],
)
def test_malformed_json_is_response_error(body):
    client, _ = _client(_Response(body))

    with pytest.raises(GitHubResponseError) as info:
        _run(client.list_open_pull_requests(REPOSITORY))

    assert str(info.value) == RESPONSE_MESSAGE


# --- check state ---------------------------------------------------------


def test_check_state_maps_success_and_failure_for_current_head():
    client, opener = _client(
        _Response(
            _check_page(
                _check_run(REQUIRED[0], conclusion="success"),
                _check_run(REQUIRED[1], run_id=902, conclusion="failure"),
            )
        )
    )

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)
    )

    assert [call["url"] for call in opener.calls] == [_checks_url()]
    assert state.head_sha == HEAD_SHA
    assert state.required_check_ids == REQUIRED
    assert [
        (result.check_id, result.check_run_id, result.conclusion)
        for result in state.results
    ] == [
        (REQUIRED[0], str(RUN_ID), CheckConclusion.success),
        (REQUIRED[1], "902", CheckConclusion.failure),
    ]
    assert state.outcome is CheckOutcome.deterministic_failure


def test_missing_required_check_stays_unknown():
    client, _ = _client(_Response(_check_page(_check_run(REQUIRED[0]))))

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)
    )

    assert [result.check_id for result in state.results] == [REQUIRED[0]]
    assert state.outcome is CheckOutcome.unknown


def test_duplicate_current_head_results_stay_unknown():
    client, _ = _client(
        _Response(
            _check_page(
                _check_run(REQUIRED[0], run_id=901),
                _check_run(REQUIRED[0], run_id=902),
            )
        )
    )

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))
    )

    assert len(state.results) == 2
    assert state.outcome is CheckOutcome.unknown


def test_stale_check_evidence_is_not_success():
    client, _ = _client(
        _Response(
            _check_page(
                _check_run(REQUIRED[0], sha=OTHER_SHA, conclusion="success")
            )
        )
    )

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))
    )

    assert state.results[0].conclusion is CheckConclusion.stale
    assert state.results[0].head_sha == OTHER_SHA
    assert state.outcome is CheckOutcome.blocked


@pytest.mark.parametrize(
    "conclusion",
    ["neutral", "cancelled", "skipped", "timed_out", "action_required"],
)
def test_non_deterministic_conclusions_map_verbatim(conclusion):
    client, opener = _client(
        _Response(_check_page(_check_run(REQUIRED[0], conclusion=conclusion)))
    )

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))
    )

    assert state.results[0].conclusion is CheckConclusion(conclusion)
    assert len(opener.calls) == 1


def test_undetermined_conclusion_uses_suite_failure_and_caches_suite():
    client, opener = _client(
        _Response(
            _check_page(
                _check_run(REQUIRED[0], conclusion=None),
                _check_run(REQUIRED[1], run_id=902, conclusion=None),
            )
        ),
        _Response({"id": SUITE_ID, "conclusion": "failure"}),
    )

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)
    )

    assert [result.conclusion for result in state.results] == [
        CheckConclusion.startup_failure,
        CheckConclusion.startup_failure,
    ]
    assert [call["url"] for call in opener.calls] == [
        _checks_url(),
        f"{API}/repos/{REPOSITORY}/check-suites/{SUITE_ID}",
    ]
    assert state.outcome is CheckOutcome.blocked


def test_undetermined_conclusion_without_suite_failure_is_not_deterministic():
    client, _ = _client(
        _Response(_check_page(_check_run(REQUIRED[0], conclusion=None))),
        _Response({"id": SUITE_ID, "conclusion": "neutral"}),
    )

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))
    )

    assert state.results[0].conclusion is CheckConclusion.action_required
    assert state.outcome is CheckOutcome.blocked


def test_undetermined_conclusion_requires_a_linked_suite():
    run = _check_run(REQUIRED[0], conclusion=None)
    del run["check_suite"]
    client, opener = _client(_Response(_check_page(run)))

    with pytest.raises(GitHubResponseError):
        _run(
            client.get_check_state(
                REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
            )
        )

    assert len(opener.calls) == 1


def test_check_state_follows_next_links():
    next_url = (
        f"{API}/repos/{REPOSITORY}/commits/{HEAD_SHA}"
        f"/check-runs?per_page=100&page=2"
    )
    client, opener = _client(
        _Response(
            _check_page(_check_run(REQUIRED[0])),
            {"Link": f'<{next_url}>; rel="next"'},
        ),
        _Response(
            _check_page(_check_run(REQUIRED[1], run_id=902, conclusion="success"))
        ),
    )

    state = _run(
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)
    )

    assert [result.check_id for result in state.results] == list(REQUIRED)
    assert [call["url"] for call in opener.calls] == [_checks_url(), next_url]
    assert state.outcome is CheckOutcome.passed


@pytest.mark.parametrize(
    "page",
    [
        [],
        {"total_count": 0},
        {"check_runs": {}},
        {"check_runs": ["not an object"]},
    ],
)
def test_check_state_rejects_malformed_pages(page):
    client, _ = _client(_Response(page))

    with pytest.raises(GitHubResponseError):
        _run(
            client.get_check_state(
                REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
            )
        )


@pytest.mark.parametrize("run_id", [0, -1, "901", True, 1.5, None])
def test_check_state_rejects_bad_run_ids(run_id):
    client, _ = _client(
        _Response(_check_page(_check_run(REQUIRED[0], run_id=run_id)))
    )

    with pytest.raises(GitHubResponseError):
        _run(
            client.get_check_state(
                REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
            )
        )


def test_check_state_rejects_duplicate_run_ids():
    client, _ = _client(
        _Response(
            _check_page(
                _check_run(REQUIRED[0], run_id=901),
                _check_run(REQUIRED[1], run_id=901, conclusion="failure"),
            )
        )
    )

    with pytest.raises(GitHubResponseError):
        _run(
            client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)
        )


# --- default branch configuration ---------------------------------------


def test_default_branch_config_resolves_metadata_ref_and_contents():
    client, opener = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response(
            {
                "ref": f"refs/heads/{DEFAULT_BRANCH}",
                "object": {"type": "commit", "sha": COMMIT_SHA},
            }
        ),
        _Response(_contents_payload(_config_payload())),
    )

    config = _run(client.get_default_branch_config(REPOSITORY))

    assert isinstance(config, PilotConfig)
    assert config.worker_enabled is True
    assert config.polling_preferences == {"mode": "adaptive"}
    assert config.motion_preference == "reduced"
    assert config.deterministic_check_ids == ("ci/lint", "ci/test")
    assert config.default_branch == DEFAULT_BRANCH
    assert config.default_branch_sha == COMMIT_SHA
    assert [call["url"] for call in opener.calls] == [
        f"{API}/repos/{REPOSITORY}",
        f"{API}/repos/{REPOSITORY}/git/ref/heads/{DEFAULT_BRANCH}",
        (
            f"{API}/repos/{REPOSITORY}/contents/.github/gitkeeper.json"
            f"?ref={COMMIT_SHA}"
        ),
    ]


def test_default_branch_config_uses_commit_sha_not_blob_sha():
    client, opener = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response({"object": {"type": "commit", "sha": COMMIT_SHA}}),
        _Response(_contents_payload(_config_payload())),
    )

    config = _run(client.get_default_branch_config(REPOSITORY))

    assert config.default_branch_sha == COMMIT_SHA
    assert config.default_branch_sha != BLOB_SHA
    assert f"?ref={COMMIT_SHA}" in opener.calls[2]["url"]


def test_default_branch_ref_quotes_nested_branch_name():
    branch = "release/1.0#beta"
    client, opener = _client(
        _Response({"default_branch": branch}),
        _Response({"object": {"type": "commit", "sha": COMMIT_SHA}}),
        _Response(_contents_payload(_config_payload())),
    )

    config = _run(client.get_default_branch_config(REPOSITORY))

    assert config.default_branch == branch
    assert opener.calls[1]["url"] == (
        f"{API}/repos/{REPOSITORY}/git/ref/heads/release/1.0%23beta"
    )


def test_default_branch_config_decodes_wrapped_base64_content():
    encoded = base64.b64encode(
        json.dumps(_config_payload()).encode("utf-8")
    ).decode("ascii")
    wrapped = "\n".join(
        encoded[index : index + 60] for index in range(0, len(encoded), 60)
    )
    client, _ = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response({"object": {"type": "commit", "sha": COMMIT_SHA}}),
        _Response(_contents_payload(None, content=wrapped)),
    )

    config = _run(client.get_default_branch_config(REPOSITORY))

    assert config.deterministic_check_ids == ("ci/lint", "ci/test")


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"default_branch": ""},
        {"default_branch": "   "},
        {"default_branch": 7},
        [],
    ],
)
def test_config_rejects_missing_default_branch(metadata):
    client, _ = _client(_Response(metadata))

    with pytest.raises(GitHubResponseError) as info:
        _run(client.get_default_branch_config(REPOSITORY))

    assert str(info.value) == RESPONSE_MESSAGE


@pytest.mark.parametrize(
    "ref_payload",
    [
        {},
        {"object": None},
        {"object": {"sha": "not-a-sha"}},
        {"object": {"sha": HEAD_SHA[:12]}},
        {"object": {"type": "tree", "sha": COMMIT_SHA}},
        [],
    ],
)
def test_config_rejects_invalid_ref_payload(ref_payload):
    client, _ = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response(ref_payload),
    )

    with pytest.raises(GitHubResponseError):
        _run(client.get_default_branch_config(REPOSITORY))


@pytest.mark.parametrize(
    "contents",
    [
        [],
        {"encoding": "base64"},
        {"encoding": None, "content": ""},
        {"encoding": "utf-8", "content": "e30="},
        _contents_payload(None, content="!!!not-base64!!!"),
        _contents_payload(None, content=base64.b64encode(b"not json").decode()),
        _contents_payload(None, content=base64.b64encode(b"\xff\xfe").decode()),
    ],
)
def test_config_rejects_invalid_contents(contents):
    client, _ = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response({"object": {"type": "commit", "sha": COMMIT_SHA}}),
        _Response(contents),
    )

    with pytest.raises(GitHubResponseError) as info:
        _run(client.get_default_branch_config(REPOSITORY))

    assert str(info.value) == RESPONSE_MESSAGE


@pytest.mark.parametrize(
    "payload",
    [
        _config_payload(extra="nope"),
        _config_payload(worker_enabled="yes"),
        _config_payload(polling_preferences={"mode": "fixed"}),
        _config_payload(deterministic_check_ids=["ci/lint", "ci/lint"]),
        {"worker_enabled": True},
        ["worker_enabled"],
    ],
)
def test_invalid_config_is_response_error_without_details(payload):
    client, _ = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response({"object": {"type": "commit", "sha": COMMIT_SHA}}),
        _Response(_contents_payload(payload)),
    )

    with pytest.raises(GitHubResponseError) as info:
        _run(client.get_default_branch_config(REPOSITORY))

    assert str(info.value) == RESPONSE_MESSAGE
    assert "worker_enabled" not in str(info.value)
    assert "deterministic_check_ids" not in str(info.value)


# --- error hygiene and request accounting -------------------------------


def test_response_errors_never_leak_token_url_body_or_headers():
    body = json.dumps({"message": f"leak {TOKEN}"}).encode("utf-8")
    client, _ = _client(_Response(body))

    with pytest.raises(GitHubResponseError) as info:
        _run(client.list_open_pull_requests(REPOSITORY))

    error = info.value
    assert str(error) == RESPONSE_MESSAGE
    assert error.args == (RESPONSE_MESSAGE,)
    assert error.__cause__ is None
    representation = repr(error)
    assert TOKEN not in representation
    assert API not in representation
    assert "leak" not in representation


def test_authorization_errors_never_leak_http_details():
    client, _ = _client(
        _http_error(
            401, url=f"{API}/repos/{REPOSITORY}/pulls?access_token={TOKEN}"
        )
    )

    with pytest.raises(GitHubAuthorizationError) as info:
        _run(client.list_open_pull_requests(REPOSITORY))

    representation = repr(info.value)
    assert str(info.value) == AUTHORIZATION_MESSAGE
    assert info.value.__cause__ is None
    assert TOKEN not in representation
    assert API not in representation


def test_each_operation_issues_one_request_per_endpoint():
    list_client, list_opener = _client(_Response([]))
    _run(list_client.list_open_pull_requests(REPOSITORY))
    assert len(list_opener.calls) == 1

    pull_client, pull_opener = _client(_Response(_pull(PR_NUMBER)))
    _run(pull_client.get_pull_request(REPOSITORY, PR_NUMBER))
    assert len(pull_opener.calls) == 1

    checks_client, checks_opener = _client(
        _Response(_check_page(_check_run(REQUIRED[0])))
    )
    _run(
        checks_client.get_check_state(
            REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
        )
    )
    assert len(checks_opener.calls) == 1

    config_client, config_opener = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response({"object": {"type": "commit", "sha": COMMIT_SHA}}),
        _Response(_contents_payload(_config_payload())),
    )
    _run(config_client.get_default_branch_config(REPOSITORY))
    assert len(config_opener.calls) == 3


def test_failures_are_not_retried():
    client, opener = _client(_http_error(500), _Response([]))

    with pytest.raises(GitHubUnavailable):
        _run(client.list_open_pull_requests(REPOSITORY))

    assert len(opener.calls) == 1


def test_control_plane_protocol_keeps_label_surface_for_later_slice():
    declared = set(GitHubControlPlane.__dict__)
    assert declared >= {
        "list_open_pull_requests",
        "get_pull_request",
        "get_check_state",
        "get_default_branch_config",
        "get_worker_labels",
        "set_worker_label",
    }
    assert not hasattr(GitHubRestClient, "get_worker_labels")
    assert not hasattr(GitHubRestClient, "set_worker_label")
