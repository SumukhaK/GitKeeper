import pytest

from gitkeeper.policy.patch_scope import RemotePrState
from gitkeeper.worker.models import PublishResult, PublishTarget
from gitkeeper.worker.publish_target import (
    GitHubPublishTargetAdapter,
    PublishTargetError,
)

_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"


def _payload(**overrides):
    values = {
        "state": "open",
        "draft": False,
        "user": {"login": "SumukhaK"},
        "head": {
            "ref": "repair/42",
            "sha": _SHA,
            "repo": {"full_name": _REPOSITORY},
        },
    }
    for key, value in overrides.items():
        if key in {"head", "user"} and isinstance(value, dict):
            values[key] = {**values[key], **value}
        else:
            values[key] = value
    return values


def _target(payload):
    adapter = GitHubPublishTargetAdapter(lambda repository, pr_number: payload)
    return adapter.get_publish_target(_REPOSITORY, 42)


def test_reads_the_exact_same_repository_target() -> None:
    target = _target(_payload())

    assert target.branch == "repair/42"
    assert target.head_repository == _REPOSITORY
    assert target.is_default_branch is False
    assert target.remote.head_sha == _SHA
    assert target.remote.head_repository == _REPOSITORY
    assert target.remote.is_open is True
    assert target.remote.is_draft is False


def test_rejects_a_fork_head() -> None:
    with pytest.raises(PublishTargetError):
        _target(
            _payload(
                head={"repo": {"full_name": "someone/Setu-mobile"}},
            )
        )


def test_rejects_a_missing_or_empty_head_branch() -> None:
    for value in (None, ""):
        with pytest.raises(PublishTargetError):
            _target(_payload(head={"ref": value}))


def test_rejects_default_branches() -> None:
    for branch in ("main", "master"):
        with pytest.raises(PublishTargetError):
            _target(_payload(head={"ref": branch}))

    adapter = GitHubPublishTargetAdapter(
        lambda repository, pr_number: _payload(head={"ref": "trunk"}),
        default_branch="trunk",
    )
    with pytest.raises(PublishTargetError):
        adapter.get_publish_target(_REPOSITORY, 42)


def test_rejects_missing_author_or_invalid_sha() -> None:
    with pytest.raises(PublishTargetError):
        _target(_payload(user={"login": ""}))
    with pytest.raises(PublishTargetError):
        _target(_payload(head={"sha": "abc"}))


def test_read_failure_is_blocked() -> None:
    def fail(repository, pr_number):
        raise RuntimeError("network")

    with pytest.raises(PublishTargetError):
        GitHubPublishTargetAdapter(fail).get_publish_target(_REPOSITORY, 42)


def test_publish_target_rejects_a_mismatched_head_repository() -> None:
    with pytest.raises(ValueError):
        PublishTarget(
            repository=_REPOSITORY,
            pr_number=42,
            branch="repair/42",
            head_repository="someone/Setu-mobile",
            is_default_branch=False,
            remote=RemotePrState(
                number=42,
                repository=_REPOSITORY,
                author="SumukhaK",
                head_sha=_SHA,
                is_open=True,
                is_draft=False,
                head_repository="someone/Setu-mobile",
            ),
        )


def test_publish_result_requires_a_commit_for_success() -> None:
    assert PublishResult("published", _SHA, ("scope",), None).status == "published"
    assert PublishResult("blocked", None, ("scope",), None).status == "blocked"
    assert PublishResult("unknown", _SHA, (), None).status == "unknown"

    with pytest.raises(ValueError):
        PublishResult("published", None, (), None)
    with pytest.raises(ValueError):
        PublishResult("other", None, (), None)
