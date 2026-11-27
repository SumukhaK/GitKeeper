"""Read-only GitHub control-plane adapter tests.

The adapter is fenced to fixed queue error categories and performs exactly one
HTTP request per intended endpoint read, because the poller owns backoff.
These tests pin the wire mapping, pagination, per-page observation timestamps,
error translation, and default-branch configuration decoding without any
network access.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote

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
    WorkerLabel,
)
from gitkeeper.queue.github import GitHubControlPlane, GitHubRestClient, PrDetails

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
LABEL_QUEUED = "gitkeeper/worker-queued"
LABEL_RUNNING = "gitkeeper/worker-running"
LABEL_BLOCKED = "gitkeeper/worker-blocked"
UNRELATED_LABEL = "bug"

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


def _label(name):
    return {"name": name}


def _delete_label_url(name):
    return (
        f"{API}/repos/{REPOSITORY}/issues/{PR_NUMBER}/labels/"
        f"{quote(name, safe='')}"
    )


def _labels_url():
    return (
        f"{API}/repos/{REPOSITORY}/issues/{PR_NUMBER}/labels?per_page=100"
    )


def _add_labels_url():
    return f"{API}/repos/{REPOSITORY}/issues/{PR_NUMBER}/labels"


# --- tokens and shared request shape -------------------------------------


@pytest.mark.parametrize("token", [None, "", "   ", "\n\t "])
def test_missing_or_blank_token_is_authorization_failure(token):
    client, opener = _client(_Response([]), token=token)

    with pytest.raises(GitHubAuthorizationError) as info:
        client.list_open_pull_requests(REPOSITORY)

    assert str(info.value) == AUTHORIZATION_MESSAGE
    assert info.value.args == (AUTHORIZATION_MESSAGE,)
    assert opener.calls == []


def test_surrounding_token_whitespace_is_trimmed():
    client, opener = _client(_Response([]), token=f"  {TOKEN}\n")

    client.list_open_pull_requests(REPOSITORY)

    headers = {
        key.lower(): value
        for key, value in opener.calls[0]["request"].header_items()
    }
    assert headers["authorization"] == f"Bearer {TOKEN}"


def test_requests_carry_fixed_headers_and_timeout():
    client, opener = _client(_Response([]), timeout_seconds=7.5)

    client.list_open_pull_requests(REPOSITORY)

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

    client.list_open_pull_requests(REPOSITORY)

    assert opener.calls[0]["timeout"] == 20.0


def test_plain_response_without_context_manager_is_supported():
    client, _ = _client(_PlainResponse([_pull(PR_NUMBER)]))

    snapshots = client.list_open_pull_requests(REPOSITORY)

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

    snapshots = client.list_open_pull_requests(REPOSITORY)

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

    snapshots = client.list_open_pull_requests(REPOSITORY)

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
        client.list_open_pull_requests(REPOSITORY)

    assert str(info.value) == RESPONSE_MESSAGE


def test_list_rejects_non_boolean_draft():
    item = _pull(PR_NUMBER)
    item["draft"] = "true"
    client, _ = _client(_Response([item]))

    with pytest.raises(GitHubResponseError):
        client.list_open_pull_requests(REPOSITORY)


# --- single pull request -------------------------------------------------


def test_get_pull_request_maps_single_pull_request_facts():
    client, opener = _client(_Response(_pull(PR_NUMBER, draft=True)))

    snapshot = client.get_pull_request(REPOSITORY, PR_NUMBER)

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

    snapshot = client.get_pull_request(REPOSITORY, PR_NUMBER)

    assert snapshot.author is None
    assert snapshot.head_repository is None
    assert snapshot.is_draft is False


def test_single_pull_request_404_is_not_found():
    client, _ = _client(
        _http_error(404, url=f"{API}/repos/{REPOSITORY}/pulls/{PR_NUMBER}")
    )

    with pytest.raises(PullRequestNotFound) as info:
        client.get_pull_request(REPOSITORY, PR_NUMBER)

    assert type(info.value) is PullRequestNotFound
    assert isinstance(info.value, GitHubResponseError)
    assert str(info.value) == NOT_FOUND_MESSAGE


def test_list_404_is_response_error():
    client, _ = _client(_http_error(404))

    with pytest.raises(GitHubResponseError) as info:
        client.list_open_pull_requests(REPOSITORY)

    assert type(info.value) is GitHubResponseError
    assert str(info.value) == RESPONSE_MESSAGE


def test_check_state_404_is_response_error():
    client, _ = _client(_http_error(404, url=_checks_url()))

    with pytest.raises(GitHubResponseError) as info:
        client.get_check_state(
            REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
        )

    assert type(info.value) is GitHubResponseError
    assert str(info.value) == RESPONSE_MESSAGE


def test_config_404_is_response_error():
    client, _ = _client(_http_error(404, url=f"{API}/repos/{REPOSITORY}"))

    with pytest.raises(GitHubResponseError) as info:
        client.get_default_branch_config(REPOSITORY)

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
        client.get_check_state(
            REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
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
        client.get_pull_request(repository, pr_number)

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
        client.list_open_pull_requests(REPOSITORY)

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
        client.get_pull_request(REPOSITORY, PR_NUMBER)

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
        client.list_open_pull_requests(REPOSITORY)

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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)

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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)

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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))

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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))

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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))

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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)

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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],))

    assert state.results[0].conclusion is CheckConclusion.action_required
    assert state.outcome is CheckOutcome.blocked


def test_undetermined_conclusion_requires_a_linked_suite():
    run = _check_run(REQUIRED[0], conclusion=None)
    del run["check_suite"]
    client, opener = _client(_Response(_check_page(run)))

    with pytest.raises(GitHubResponseError):
        client.get_check_state(
            REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
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

    state = client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)

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
        client.get_check_state(
            REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
        )


@pytest.mark.parametrize("run_id", [0, -1, "901", True, 1.5, None])
def test_check_state_rejects_bad_run_ids(run_id):
    client, _ = _client(
        _Response(_check_page(_check_run(REQUIRED[0], run_id=run_id)))
    )

    with pytest.raises(GitHubResponseError):
        client.get_check_state(
            REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
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
        client.get_check_state(REPOSITORY, PR_NUMBER, HEAD_SHA, REQUIRED)


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

    config = client.get_default_branch_config(REPOSITORY)

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

    config = client.get_default_branch_config(REPOSITORY)

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

    config = client.get_default_branch_config(REPOSITORY)

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

    config = client.get_default_branch_config(REPOSITORY)

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
        client.get_default_branch_config(REPOSITORY)

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
        client.get_default_branch_config(REPOSITORY)


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
        client.get_default_branch_config(REPOSITORY)

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
        client.get_default_branch_config(REPOSITORY)

    assert str(info.value) == RESPONSE_MESSAGE
    assert "worker_enabled" not in str(info.value)
    assert "deterministic_check_ids" not in str(info.value)


# --- error hygiene and request accounting -------------------------------


def test_response_errors_never_leak_token_url_body_or_headers():
    body = json.dumps({"message": f"leak {TOKEN}"}).encode("utf-8")
    client, _ = _client(_Response(body))

    with pytest.raises(GitHubResponseError) as info:
        client.list_open_pull_requests(REPOSITORY)

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
        client.list_open_pull_requests(REPOSITORY)

    representation = repr(info.value)
    assert str(info.value) == AUTHORIZATION_MESSAGE
    assert info.value.__cause__ is None
    assert TOKEN not in representation
    assert API not in representation


def test_each_operation_issues_one_request_per_endpoint():
    list_client, list_opener = _client(_Response([]))
    list_client.list_open_pull_requests(REPOSITORY)
    assert len(list_opener.calls) == 1

    pull_client, pull_opener = _client(_Response(_pull(PR_NUMBER)))
    pull_client.get_pull_request(REPOSITORY, PR_NUMBER)
    assert len(pull_opener.calls) == 1

    checks_client, checks_opener = _client(
        _Response(_check_page(_check_run(REQUIRED[0])))
    )
    checks_client.get_check_state(
        REPOSITORY, PR_NUMBER, HEAD_SHA, (REQUIRED[0],)
    )
    assert len(checks_opener.calls) == 1

    config_client, config_opener = _client(
        _Response({"default_branch": DEFAULT_BRANCH}),
        _Response({"object": {"type": "commit", "sha": COMMIT_SHA}}),
        _Response(_contents_payload(_config_payload())),
    )
    config_client.get_default_branch_config(REPOSITORY)
    assert len(config_opener.calls) == 3


def test_failures_are_not_retried():
    client, opener = _client(_http_error(500), _Response([]))

    with pytest.raises(GitHubUnavailable):
        client.list_open_pull_requests(REPOSITORY)

    assert len(opener.calls) == 1


def test_control_plane_protocol_exposes_worker_label_surface():
    declared = set(GitHubControlPlane.__dict__)
    assert declared >= {
        "list_open_pull_requests",
        "get_pull_request",
        "get_check_state",
        "get_default_branch_config",
        "get_worker_labels",
        "set_worker_label",
    }
    assert hasattr(GitHubRestClient, "get_worker_labels")
    assert hasattr(GitHubRestClient, "set_worker_label")


# --- worker labels -------------------------------------------------------


def test_get_worker_labels_filters_exact_names_and_preserves_order():
    client, opener = _client(
        _Response(
            [
                _label(LABEL_QUEUED),
                _label(UNRELATED_LABEL),
                _label(LABEL_RUNNING),
                _label(f"{LABEL_BLOCKED}-extra"),
                _label(LABEL_QUEUED.upper()),
            ]
        )
    )

    labels = client.get_worker_labels(REPOSITORY, PR_NUMBER)

    assert labels == (WorkerLabel.queued, WorkerLabel.running)
    assert [call["url"] for call in opener.calls] == [_labels_url()]


def test_get_worker_labels_follows_next_links():
    next_url = (
        f"{API}/repos/{REPOSITORY}/issues/{PR_NUMBER}/labels"
        "?per_page=100&page=2"
    )
    client, opener = _client(
        _Response(
            [_label(LABEL_BLOCKED)],
            {"Link": f'<{next_url}>; rel="next"'},
        ),
        _Response([_label(LABEL_QUEUED)]),
    )

    labels = client.get_worker_labels(REPOSITORY, PR_NUMBER)

    assert labels == (WorkerLabel.blocked, WorkerLabel.queued)
    assert [call["url"] for call in opener.calls] == [
        _labels_url(),
        next_url,
    ]


def test_get_worker_labels_ignores_unrelated_label_url_shapes():
    client, _ = _client(
        _Response([{"name": UNRELATED_LABEL}, _label(LABEL_QUEUED)])
    )

    labels = client.get_worker_labels(REPOSITORY, PR_NUMBER)

    assert labels == (WorkerLabel.queued,)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": LABEL_QUEUED},
        ["not-a-label-object"],
        [None],
        [{"url": f"{API}/repos/{REPOSITORY}/labels/{LABEL_QUEUED}"}],
        [{"name": 9}],
        [{"name": None}],
        [_label(LABEL_QUEUED), _label(LABEL_QUEUED)],
    ],
)
def test_get_worker_labels_rejects_malformed_label_objects(payload):
    client, opener = _client(_Response(payload))

    with pytest.raises(GitHubResponseError) as info:
        client.get_worker_labels(REPOSITORY, PR_NUMBER)

    assert str(info.value) == RESPONSE_MESSAGE
    assert len(opener.calls) == 1


def test_get_worker_labels_rejects_duplicates_across_pages():
    next_url = (
        f"{API}/repos/{REPOSITORY}/issues/{PR_NUMBER}/labels"
        "?per_page=100&page=2"
    )
    client, _ = _client(
        _Response(
            [_label(LABEL_QUEUED)],
            {"Link": f'<{next_url}>; rel="next"'},
        ),
        _Response([_label(LABEL_QUEUED)]),
    )

    with pytest.raises(GitHubResponseError):
        client.get_worker_labels(REPOSITORY, PR_NUMBER)


def test_get_worker_labels_404_is_response_error_not_missing_pull_request():
    client, _ = _client(_http_error(404, url=_labels_url()))

    with pytest.raises(GitHubResponseError) as info:
        client.get_worker_labels(REPOSITORY, PR_NUMBER)

    assert not isinstance(info.value, PullRequestNotFound)


@pytest.mark.parametrize(
    "repository, pr_number",
    [
        ("", PR_NUMBER),
        ("owner", PR_NUMBER),
        ("owner/", PR_NUMBER),
        ("owner/repo/extra", PR_NUMBER),
        (None, PR_NUMBER),
        (REPOSITORY, 0),
        (REPOSITORY, -1),
        (REPOSITORY, "42"),
        (REPOSITORY, True),
    ],
)
def test_worker_label_arguments_are_validated_before_any_request(
    repository, pr_number
):
    read_client, read_opener = _client(_Response([]))
    with pytest.raises(GitHubResponseError):
        read_client.get_worker_labels(repository, pr_number)
    assert read_opener.calls == []

    set_client, set_opener = _client(_Response([]))
    with pytest.raises(GitHubResponseError):
        set_client.set_worker_label(
            repository, pr_number, WorkerLabel.queued
        )
    assert set_opener.calls == []


def test_set_worker_label_rejects_untyped_label_before_any_request():
    client, opener = _client(_Response([]))

    with pytest.raises(GitHubResponseError):
        client.set_worker_label(REPOSITORY, PR_NUMBER, LABEL_QUEUED)

    assert opener.calls == []


def test_set_worker_label_converges_from_conflicting_label():
    client, opener = _client(
        _Response([_label(LABEL_RUNNING)]),
        _Response([]),
        _Response([_label(LABEL_QUEUED)]),
        _Response([_label(LABEL_QUEUED)]),
    )

    result = client.set_worker_label(REPOSITORY, PR_NUMBER, WorkerLabel.queued)

    assert result is None
    assert [call["request"].get_method() for call in opener.calls] == [
        "GET",
        "DELETE",
        "POST",
        "GET",
    ]
    assert [call["url"] for call in opener.calls] == [
        _labels_url(),
        _delete_label_url(LABEL_RUNNING),
        _add_labels_url(),
        _labels_url(),
    ]
    delete_request = opener.calls[1]["request"]
    assert delete_request.data is None
    post_request = opener.calls[2]["request"]
    assert json.loads(post_request.data.decode("utf-8")) == {
        "labels": [LABEL_QUEUED]
    }
    post_headers = {
        key.lower(): value for key, value in post_request.header_items()
    }
    assert post_headers == {
        "accept": "application/vnd.github+json",
        "authorization": f"Bearer {TOKEN}",
        "user-agent": "GitKeeper",
        "content-type": "application/json",
    }
    get_headers = {
        key.lower(): value
        for key, value in opener.calls[0]["request"].header_items()
    }
    assert "content-type" not in get_headers


def test_set_worker_label_deletes_per_pr_assignment_not_definition():
    definition_url = (
        f"{API}/repos/{REPOSITORY}/labels/{quote(LABEL_BLOCKED, safe='')}"
    )
    decoy_url = f"{API}/repos/Other/Repo/labels/{LABEL_BLOCKED}"
    client, opener = _client(
        _Response(
            [
                {"name": LABEL_BLOCKED, "url": definition_url},
                {"name": UNRELATED_LABEL, "url": decoy_url},
            ]
        ),
        _Response([]),
        _Response([]),
    )

    client.set_worker_label(REPOSITORY, PR_NUMBER, None)

    assert opener.calls[1]["request"].get_method() == "DELETE"
    assert opener.calls[1]["url"] == _delete_label_url(LABEL_BLOCKED)
    assert opener.calls[2]["url"] == _labels_url()
    issued = [call["url"] for call in opener.calls]
    assert definition_url not in issued
    assert decoy_url not in issued


def test_set_worker_label_clears_to_no_label_without_posting():
    client, opener = _client(
        _Response([_label(LABEL_BLOCKED)]),
        _Response([]),
        _Response([]),
    )

    client.set_worker_label(REPOSITORY, PR_NUMBER, None)

    assert [call["request"].get_method() for call in opener.calls] == [
        "GET",
        "DELETE",
        "GET",
    ]
    assert [call["url"] for call in opener.calls[1:]] == [
        _delete_label_url(LABEL_BLOCKED),
        _labels_url(),
    ]


def test_set_worker_label_is_a_noop_when_already_converged():
    matching_client, matching_opener = _client(
        _Response([_label(LABEL_QUEUED)])
    )
    matching_client.set_worker_label(
        REPOSITORY, PR_NUMBER, WorkerLabel.queued
    )
    assert len(matching_opener.calls) == 1

    empty_client, empty_opener = _client(_Response([]))
    empty_client.set_worker_label(REPOSITORY, PR_NUMBER, None)
    assert len(empty_opener.calls) == 1


def test_set_worker_label_rejects_multiple_current_labels_before_mutation():
    client, opener = _client(
        _Response([_label(LABEL_QUEUED), _label(LABEL_RUNNING)])
    )

    with pytest.raises(GitHubResponseError) as info:
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    assert str(info.value) == RESPONSE_MESSAGE
    assert len(opener.calls) == 1


def test_set_worker_label_initial_read_failure_issues_no_mutation():
    client, opener = _client(_http_error(404, url=_labels_url()))

    with pytest.raises(GitHubResponseError):
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    assert [call["request"].get_method() for call in opener.calls] == ["GET"]


def test_set_worker_label_delete_failure_stops_before_posting():
    client, opener = _client(
        _Response([_label(LABEL_RUNNING)]),
        _http_error(500, url=_delete_label_url(LABEL_RUNNING)),
    )

    with pytest.raises(GitHubUnavailable):
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    assert [call["request"].get_method() for call in opener.calls] == [
        "GET",
        "DELETE",
    ]


def test_set_worker_label_post_failure_raises():
    client, opener = _client(
        _Response([_label(LABEL_RUNNING)]),
        _Response([]),
        _http_error(422, url=_add_labels_url()),
    )

    with pytest.raises(GitHubResponseError):
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    assert [call["request"].get_method() for call in opener.calls] == [
        "GET",
        "DELETE",
        "POST",
    ]


def test_set_worker_label_final_read_failure_is_not_success():
    client, opener = _client(
        _Response([_label(LABEL_RUNNING)]),
        _Response([]),
        _Response([_label(LABEL_QUEUED)]),
        _http_error(500, url=_labels_url()),
    )

    with pytest.raises(GitHubUnavailable):
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    assert [call["request"].get_method() for call in opener.calls] == [
        "GET",
        "DELETE",
        "POST",
        "GET",
    ]


def test_set_worker_label_final_read_mismatch_raises():
    client, _ = _client(
        _Response([_label(LABEL_RUNNING)]),
        _Response([]),
        _Response([_label(LABEL_QUEUED)]),
        _Response([_label(LABEL_RUNNING)]),
    )

    with pytest.raises(GitHubResponseError) as info:
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    assert str(info.value) == RESPONSE_MESSAGE


def test_set_worker_label_final_read_of_extra_worker_label_raises():
    client, _ = _client(
        _Response([_label(LABEL_RUNNING)]),
        _Response([]),
        _Response([_label(LABEL_QUEUED)]),
        _Response([_label(LABEL_QUEUED), _label(LABEL_BLOCKED)]),
    )

    with pytest.raises(GitHubResponseError):
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )


def test_set_worker_label_later_call_repairs_partial_state():
    client, opener = _client(
        _Response([]),
        _Response([_label(LABEL_QUEUED)]),
        _Response([_label(LABEL_QUEUED)]),
    )

    client.set_worker_label(REPOSITORY, PR_NUMBER, WorkerLabel.queued)

    assert [call["request"].get_method() for call in opener.calls] == [
        "GET",
        "POST",
        "GET",
    ]


def test_set_worker_label_preserves_unrelated_labels():
    client, opener = _client(
        _Response([_label(UNRELATED_LABEL), _label(LABEL_RUNNING)]),
        _Response([_label(UNRELATED_LABEL)]),
        _Response([_label(UNRELATED_LABEL), _label(LABEL_QUEUED)]),
        _Response([_label(UNRELATED_LABEL), _label(LABEL_QUEUED)]),
    )

    client.set_worker_label(REPOSITORY, PR_NUMBER, WorkerLabel.queued)

    assert [call["url"] for call in opener.calls] == [
        _labels_url(),
        _delete_label_url(LABEL_RUNNING),
        _add_labels_url(),
        _labels_url(),
    ]
    post_request = opener.calls[2]["request"]
    assert json.loads(post_request.data.decode("utf-8")) == {
        "labels": [LABEL_QUEUED]
    }


def test_set_worker_label_repairs_conflict_after_failed_mutation():
    failing_client, failing_opener = _client(
        _Response([_label(LABEL_RUNNING)]),
        _http_error(500, url=_delete_label_url(LABEL_RUNNING)),
    )

    with pytest.raises(GitHubUnavailable):
        failing_client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    assert [
        call["request"].get_method() for call in failing_opener.calls
    ] == ["GET", "DELETE"]

    retry_client, retry_opener = _client(
        _Response([_label(LABEL_RUNNING)]),
        _Response([]),
        _Response([_label(LABEL_QUEUED)]),
        _Response([_label(LABEL_QUEUED)]),
    )

    retry_client.set_worker_label(
        REPOSITORY, PR_NUMBER, WorkerLabel.queued
    )

    assert [
        call["request"].get_method() for call in retry_opener.calls
    ] == ["GET", "DELETE", "POST", "GET"]
    assert [call["url"] for call in retry_opener.calls] == [
        _labels_url(),
        _delete_label_url(LABEL_RUNNING),
        _add_labels_url(),
        _labels_url(),
    ]


def test_worker_label_failures_never_leak_details():
    client, _ = _client(
        _Response([_label(LABEL_RUNNING)]),
        _http_error(
            403,
            url=f"{_delete_label_url(LABEL_RUNNING)}?access_token={TOKEN}",
        ),
    )

    with pytest.raises(GitHubAuthorizationError) as info:
        client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )

    error = info.value
    assert str(error) == AUTHORIZATION_MESSAGE
    assert error.args == (AUTHORIZATION_MESSAGE,)
    assert error.__cause__ is None
    representation = repr(error)
    assert TOKEN not in representation
    assert API not in representation
    assert LABEL_RUNNING not in representation

    mismatch_client, _ = _client(
        _Response([_label(LABEL_RUNNING)]),
        _Response([]),
        _Response([_label(LABEL_QUEUED)]),
        _Response([_label(LABEL_RUNNING)]),
    )
    with pytest.raises(GitHubResponseError) as mismatch_info:
        mismatch_client.set_worker_label(
            REPOSITORY, PR_NUMBER, WorkerLabel.queued
        )
    mismatch_repr = repr(mismatch_info.value)
    assert mismatch_info.value.args == (RESPONSE_MESSAGE,)
    assert TOKEN not in mismatch_repr
    assert API not in mismatch_repr
    assert LABEL_RUNNING not in mismatch_repr

# --- relay reads ---------------------------------------------------------


def _pr_payload(body="Closes #61"):
    return {
        "number": PR_NUMBER,
        "title": "Relay reads",
        "body": body,
        "html_url": f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}",
        "state": "open",
        "merged": False,
        "draft": False,
        "head": {
            "sha": HEAD_SHA,
            "ref": "feature/relay",
            "repo": {"full_name": REPOSITORY},
        },
        "user": {"login": "SumukhaK"},
    }


def _issue_payload(number, *, body=None):
    return {"number": number, "title": f"Task {number}", "body": body, "state": "open"}


def _replace(payload, path, value):
    if not path:
        return value
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return payload


_RELAY_READS = (
    ("get_pr_details", (REPOSITORY, PR_NUMBER)),
    ("get_issue", (REPOSITORY, PR_NUMBER)),
    ("get_pr_diff", (REPOSITORY, PR_NUMBER)),
    ("get_check_annotations", (REPOSITORY, RUN_ID)),
    ("next_task_issue", (REPOSITORY,)),
)


def test_get_pr_details_maps_fields_and_first_closing_reference():
    client, opener = _client(_Response(_pr_payload("Fixes #7; Closes #61")))

    details = client.get_pr_details(REPOSITORY, PR_NUMBER)

    assert details == PrDetails(
        PR_NUMBER,
        "Relay reads",
        "Fixes #7; Closes #61",
        f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}",
        HEAD_SHA,
        "feature/relay",
        REPOSITORY,
        "SumukhaK",
        False,
        True,
        False,
        7,
    )
    assert opener.calls[0]["url"] == f"{API}/repos/{REPOSITORY}/pulls/{PR_NUMBER}"


@pytest.mark.parametrize(
    ("body", "linked"),
    [(None, None), ("No reference", None), ("resolves #61", 61), ("Closes #0", None)],
)
def test_get_pr_details_handles_null_body_and_closing_keywords(body, linked):
    client, _ = _client(_Response(_pr_payload(body)))

    assert client.get_pr_details(REPOSITORY, PR_NUMBER).linked_issue == linked


def test_get_pr_details_maps_closed_merged_state():
    payload = _replace(_pr_payload(), ("state",), "closed")
    payload = _replace(payload, ("merged",), True)
    payload = _replace(payload, ("draft",), True)
    client, _ = _client(_Response(payload))

    details = client.get_pr_details(REPOSITORY, PR_NUMBER)

    assert details.is_open is False
    assert details.is_merged is True
    assert details.is_draft is True


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ((), []),
        (("number",), 0),
        (("title",), None),
        (("body",), 7),
        (("state",), "unknown"),
        (("head", "sha"), "bad"),
        (("head", "repo", "full_name"), 7),
        (("user", "login"), 7),
    ],
)
def test_get_pr_details_rejects_unexpected_shapes(path, value):
    client, _ = _client(_Response(_replace(_pr_payload(), path, value)))

    with pytest.raises(GitHubResponseError):
        client.get_pr_details(REPOSITORY, PR_NUMBER)


def test_get_issue_maps_fields_and_rejects_pull_requests():
    client, opener = _client(_Response(_issue_payload(61)))

    issue = client.get_issue(REPOSITORY, 61)

    assert (issue.number, issue.title, issue.body, issue.is_open) == (
        61,
        "Task 61",
        "",
        True,
    )
    assert opener.calls[0]["url"] == f"{API}/repos/{REPOSITORY}/issues/61"
    pull = _replace(_issue_payload(61), ("pull_request",), {"url": "pr"})
    pull_client, _ = _client(_Response(pull))
    with pytest.raises(GitHubResponseError):
        pull_client.get_issue(REPOSITORY, 61)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ((), []),
        (("number",), True),
        (("title",), None),
        (("body",), 1),
        (("state",), "unknown"),
    ],
)
def test_get_issue_rejects_unexpected_shapes(path, value):
    client, _ = _client(_Response(_replace(_issue_payload(61), path, value)))

    with pytest.raises(GitHubResponseError):
        client.get_issue(REPOSITORY, 61)


def test_get_pr_diff_uses_diff_media_type_and_decodes_utf8():
    client, opener = _client(_Response(b"diff --git a/a b/a\n"))

    assert client.get_pr_diff(REPOSITORY, PR_NUMBER) == "diff --git a/a b/a\n"
    request = opener.calls[0]["request"]
    assert request.get_header("Accept") == "application/vnd.github.diff"


@pytest.mark.parametrize("size,valid", [(2 * 1024 * 1024, True), (2 * 1024 * 1024 + 1, False)])
def test_get_pr_diff_enforces_two_megabyte_limit(size, valid):
    client, _ = _client(_Response(b"a" * size))

    if valid:
        assert len(client.get_pr_diff(REPOSITORY, PR_NUMBER)) == size
    else:
        with pytest.raises(GitHubResponseError):
            client.get_pr_diff(REPOSITORY, PR_NUMBER)


def test_get_pr_diff_rejects_invalid_utf8():
    client, _ = _client(_Response(b"\xff"))

    with pytest.raises(GitHubResponseError):
        client.get_pr_diff(REPOSITORY, PR_NUMBER)


def test_get_check_annotations_formats_and_caps_at_fifty():
    payload = [
        {"path": f"src/{index}.py", "start_line": index + 1, "message": f"failure {index}"}
        for index in range(55)
    ]
    client, opener = _client(_Response(payload))

    annotations = client.get_check_annotations(REPOSITORY, RUN_ID)

    assert len(annotations) == 50
    assert annotations[0] == "src/0.py:1 failure 0"
    assert annotations[-1] == "src/49.py:50 failure 49"
    assert opener.calls[0]["url"] == (
        f"{API}/repos/{REPOSITORY}/check-runs/{RUN_ID}/annotations?per_page=50"
    )


@pytest.mark.parametrize(
    "item",
    [
        None,
        {},
        {"path": "a", "start_line": 0, "message": "bad"},
        {"path": "a", "start_line": 1, "message": None},
    ],
)
def test_get_check_annotations_rejects_unexpected_shapes(item):
    client, _ = _client(_Response([item]))

    with pytest.raises(GitHubResponseError):
        client.get_check_annotations(REPOSITORY, RUN_ID)


def test_next_task_issue_skips_all_issues_linked_by_open_prs():
    pull = _replace(_issue_payload(61), ("pull_request",), {"url": "pr"})
    client, opener = _client(
        _Response([_issue_payload(60), pull, _issue_payload(62)]),
        _Response([{"number": PR_NUMBER, "body": "Closes #60\nFixes #61"}]),
    )

    assert client.next_task_issue(REPOSITORY).number == 62
    assert [call["url"] for call in opener.calls] == [
        f"{API}/repos/{REPOSITORY}/issues?state=open&labels=task&sort=created&direction=asc",
        f"{API}/repos/{REPOSITORY}/pulls?state=open&per_page=100",
    ]


def test_next_task_issue_returns_none_when_tasks_are_empty_or_all_linked():
    empty_client, _ = _client(_Response([]), _Response([]))
    assert empty_client.next_task_issue(REPOSITORY) is None
    linked_client, _ = _client(
        _Response([_issue_payload(61)]),
        _Response([{"number": PR_NUMBER, "body": "Resolves #61"}]),
    )
    assert linked_client.next_task_issue(REPOSITORY) is None


def test_next_task_issue_follows_pagination_for_tasks_and_open_prs():
    task_page = f"{API}/repos/{REPOSITORY}/issues?state=open&page=2"
    pr_page = f"{API}/repos/{REPOSITORY}/pulls?state=open&page=2"
    client, opener = _client(
        _Response([], {"Link": f'<{task_page}>; rel="next"'}),
        _Response([_issue_payload(61)]),
        _Response(
            [{"number": PR_NUMBER, "body": None}],
            {"Link": f'<{pr_page}>; rel="next"'},
        ),
        _Response([{"number": 43, "body": "Closes #61"}]),
    )

    assert client.next_task_issue(REPOSITORY) is None
    assert len(opener.calls) == 4


@pytest.mark.parametrize(
    ("tasks", "pulls"),
    [
        ({}, []),
        ([None], []),
        ([], {}),
        ([], [None]),
        ([], [{"body": "Closes #61"}]),
        ([], [{"number": 43, "body": 1}]),
    ],
)
def test_next_task_issue_rejects_unexpected_shapes(tasks, pulls):
    client, _ = _client(_Response(tasks), _Response(pulls))

    with pytest.raises(GitHubResponseError):
        client.next_task_issue(REPOSITORY)


@pytest.mark.parametrize(("method", "args"), _RELAY_READS)
def test_relay_reads_reject_malformed_json(method, args):
    client, _ = _client(_Response(b"not json"))

    with pytest.raises(GitHubResponseError):
        getattr(client, method)(*args)


@pytest.mark.parametrize(
    ("method", "args", "error"),
    [
        (*_RELAY_READS[0], PullRequestNotFound),
        (*_RELAY_READS[1], GitHubResponseError),
        (*_RELAY_READS[2], PullRequestNotFound),
        (*_RELAY_READS[3], GitHubResponseError),
        (*_RELAY_READS[4], GitHubResponseError),
    ],
)
def test_relay_reads_map_404_errors_without_retries(method, args, error):
    client, opener = _client(_http_error(404))

    with pytest.raises(error):
        getattr(client, method)(*args)
    assert len(opener.calls) == 1
