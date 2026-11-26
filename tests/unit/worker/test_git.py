import subprocess
from pathlib import Path

import pytest

from gitkeeper.worker.git import (
    GitCliTransport,
    GitPushOutcomeUnknown,
    GitPushRejected,
    GitTransportError,
    _parse_status,
    validate_clone_url,
)
from gitkeeper.worker.worktree import WorktreePathError

_REPOSITORY = "SumukhaK/Setu-mobile"
_CLONE_URL = "https://github.com/SumukhaK/Setu-mobile.git"


def _git(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=", *arguments],
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise AssertionError(f"git {arguments} failed: {result.stderr}")
    return result.stdout.strip()


def _remote(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote))
    work = tmp_path / "work"
    _git("init", str(work))
    _git("checkout", "-b", "main", cwd=work)
    _git("config", "user.email", "test@example.com", cwd=work)
    _git("config", "user.name", "Test", cwd=work)
    (work / "file.txt").write_bytes(b"hello\n")
    (work / "space name.txt").write_bytes(b"space\n")
    (work / "caf\u00e9.txt").write_bytes(b"unicode\n")
    (work / "plus.txt").write_bytes(b"base\n")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "initial", cwd=work)
    sha = _git("rev-parse", "HEAD", cwd=work)
    _git("remote", "add", "origin", str(remote), cwd=work)
    _git("push", "origin", "HEAD:refs/heads/main", cwd=work)
    return remote, work, sha


def _cache(tmp_path: Path, remote: Path) -> tuple[Path, Path]:
    cache_root = tmp_path / "cache"
    cache = cache_root / "SumukhaK" / "Setu-mobile.git"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not (cache / "HEAD").exists():
        _git("clone", "--bare", str(remote), str(cache))
    return cache_root, cache


def _transport(
    tmp_path: Path,
    work: Path,
    remote: Path,
    *,
    runner=None,
) -> tuple[GitCliTransport, Path]:
    cache_root, cache = _cache(tmp_path, remote)
    return (
        GitCliTransport(
            repository=_REPOSITORY,
            cache_root=cache_root,
            active_checkouts=(work,),
            clone_url=_CLONE_URL,
            runner=runner,
        ),
        cache,
    )


def test_clone_url_accepts_only_canonical_github_urls() -> None:
    assert validate_clone_url(_CLONE_URL, _REPOSITORY) == _CLONE_URL
    assert validate_clone_url(
        "https://GitHub.com/sumukhak/setu-mobile.git", _REPOSITORY
    )

    for bad in (
        "http://github.com/SumukhaK/Setu-mobile.git",
        "ssh://git@github.com/SumukhaK/Setu-mobile.git",
        "git@github.com:SumukhaK/Setu-mobile.git",
        "https://user:token@github.com/SumukhaK/Setu-mobile.git",
        "https://github.com:443/SumukhaK/Setu-mobile.git",
        "https://github.com/SumukhaK/Setu-mobile.git?ref=main",
        "https://github.com/SumukhaK/Setu-mobile.git#fragment",
        "https://github.com/Other/Repo.git",
        "https://github.com/SumukhaK/Setu-mobile",
        "https://github.com/SumukhaK/Setu-mobile/extra.git",
    ):
        with pytest.raises(ValueError):
            validate_clone_url(bad, _REPOSITORY)


def test_prepare_reuses_an_existing_bare_cache(tmp_path) -> None:
    remote, work, _ = _remote(tmp_path)
    transport, cache = _transport(tmp_path, work, remote)

    assert transport.prepare(_REPOSITORY) == cache
    assert (cache / "HEAD").exists()


def test_fetch_makes_the_exact_sha_available(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)

    transport.fetch(_REPOSITORY, sha)

    assert transport.has_commit(sha)
    assert not transport.has_commit("c" * 40)


def test_fetch_rejects_an_unbound_repository(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)

    with pytest.raises(ValueError):
        transport.fetch("Other/Repo", sha)


def test_add_detached_and_head(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"

    transport.add_detached(worktree, sha)

    assert transport.head(worktree) == sha
    assert transport.is_registered(worktree)
    assert not transport.is_registered(tmp_path / "other")


def test_add_detached_rejects_existing_path_and_missing_commit(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(GitTransportError):
        transport.add_detached(existing, sha)
    with pytest.raises(GitTransportError):
        transport.add_detached(tmp_path / "missing", "c" * 40)


def test_snapshot_reports_branch_head_dirty_and_untracked(tmp_path) -> None:
    remote, work, _ = _remote(tmp_path)
    (work / "a.txt").write_bytes(b"a\n")
    (work / "b.txt").write_bytes(b"b\n")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "files", cwd=work)
    head = _git("rev-parse", "HEAD", cwd=work)
    (work / "b.txt").write_bytes(b"changed\n")
    (work / "a.txt").write_bytes(b"changed\n")
    (work / "new.txt").write_bytes(b"new\n")
    transport, _ = _transport(tmp_path, work, remote)

    snapshot = transport.snapshot_active_checkouts()[0]

    assert snapshot.branch == "main"
    assert snapshot.head_sha == head
    assert snapshot.dirty_paths == ("a.txt", "b.txt")
    assert snapshot.untracked_paths == ("new.txt",)


def test_environment_and_config_are_isolated(tmp_path) -> None:
    remote, work, _ = _remote(tmp_path)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, "a" * 40, "")

    transport, _ = _transport(tmp_path, work, remote, runner=runner)
    transport.fetch(_REPOSITORY, "a" * 40)

    fetch_command = next(
        command for command, _ in calls if "fetch" in command
    )
    assert "--no-tags" in fetch_command
    assert "origin" in fetch_command
    assert "a" * 40 in fetch_command
    assert "--prune" not in fetch_command

    _, environment = calls[-1]
    assert "core.hooksPath=" in fetch_command
    assert "credential.helper=" in fetch_command
    assert "http.followRedirects=false" in fetch_command
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ASKPASS"] == ""
    assert environment["SSH_ASKPASS"] == ""
    assert environment["GIT_CONFIG_GLOBAL"].endswith(".gitkeeper-gitconfig")
    assert Path(environment["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8") == ""
    assert not any("token" in value.casefold() for value in environment.values())


def test_git_failures_do_not_expose_output(tmp_path) -> None:
    remote, work, _ = _remote(tmp_path)

    def failing(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "secret stderr")

    transport, _ = _transport(tmp_path, work, remote, runner=failing)
    with pytest.raises(GitTransportError) as error:
        transport.head(work)
    assert "secret" not in str(error.value)

    def timing_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    timeout_transport, _ = _transport(
        tmp_path, work, remote, runner=timing_out
    )
    with pytest.raises(GitTransportError):
        timeout_transport.has_commit("a" * 40)


def test_invalid_git_output_is_rejected(tmp_path) -> None:
    remote, work, _ = _remote(tmp_path)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "not-a-sha", "")

    transport, _ = _transport(tmp_path, work, remote, runner=runner)

    with pytest.raises(GitTransportError):
        transport.head(work)


def test_cache_root_inside_an_active_checkout_is_rejected(tmp_path) -> None:
    remote, work, _ = _remote(tmp_path)

    with pytest.raises(WorktreePathError):
        GitCliTransport(
            repository=_REPOSITORY,
            cache_root=work / "cache",
            active_checkouts=(work,),
            clone_url=_CLONE_URL,
        )


def test_prepare_fails_closed_when_clone_does_not_create_a_cache(tmp_path) -> None:
    remote, work, _ = _remote(tmp_path)
    cache_root = tmp_path / "cache"
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    transport = GitCliTransport(
        repository=_REPOSITORY,
        cache_root=cache_root,
        active_checkouts=(work,),
        clone_url=_CLONE_URL,
        runner=runner,
    )

    with pytest.raises(GitTransportError):
        transport.prepare(_REPOSITORY)
    assert any("clone" in command for command in calls)


def test_parse_status_splits_dirty_and_untracked() -> None:
    assert _parse_status(" M a.txt\0?? new.txt\0") == (
        ("a.txt",),
        ("new.txt",),
    )


def test_stage_commit_and_ancestry(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)
    (worktree / "file.txt").write_bytes(b"changed\n")

    transport.stage(worktree, ("file.txt",))
    commit_sha = transport.commit(worktree, "Repair the test")

    assert commit_sha != sha
    assert transport.head(worktree) == commit_sha
    assert transport.is_ancestor(worktree, sha, commit_sha)
    assert not transport.is_ancestor(worktree, commit_sha, sha)
    assert transport.commit_count(worktree, sha, commit_sha) == 1
    assert (
        _git("log", "-1", "--format=%an <%ae>", cwd=worktree)
        == "GitKeeper Worker <gitkeeper@localhost>"
    )


def test_stage_rejects_absolute_or_parent_paths(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)

    with pytest.raises(ValueError):
        transport.stage(worktree, ("../outside.txt",))
    with pytest.raises(ValueError):
        transport.stage(worktree, (str(tmp_path / "absolute.txt"),))


def test_commit_rejects_multiline_messages(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)

    with pytest.raises(ValueError):
        transport.commit(worktree, "line one\nline two")


def test_push_updates_the_remote_branch(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)
    (worktree / "file.txt").write_bytes(b"changed\n")
    transport.stage(worktree, ("file.txt",))
    commit_sha = transport.commit(worktree, "Repair the test")

    transport.push(worktree, "repair/test", commit_sha)

    assert transport.remote_head(_REPOSITORY, "repair/test") == commit_sha


def test_push_rejects_default_and_invalid_branches(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)

    for branch in ("main", "master", "HEAD", "@", "refs/heads/x", "-x", "a..b", "a b"):
        with pytest.raises(ValueError):
            transport.push(tmp_path, branch, sha)


def test_non_fast_forward_push_is_rejected(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    first = tmp_path / "managed" / "first"
    second = tmp_path / "managed" / "second"
    transport.add_detached(first, sha)
    transport.add_detached(second, sha)

    (first / "file.txt").write_bytes(b"first\n")
    transport.stage(first, ("file.txt",))
    first_commit = transport.commit(first, "First repair")
    transport.push(first, "repair/test", first_commit)

    (second / "file.txt").write_bytes(b"second\n")
    transport.stage(second, ("file.txt",))
    second_commit = transport.commit(second, "Divergent repair")

    with pytest.raises(GitPushRejected):
        transport.push(second, "repair/test", second_commit)


def test_push_timeout_is_unknown(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)

    def timing_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    transport, _ = _transport(tmp_path, work, remote, runner=timing_out)

    with pytest.raises(GitPushOutcomeUnknown):
        transport.push(tmp_path, "repair/test", sha)


def test_remote_head_missing_branch_and_other_repository(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)

    with pytest.raises(GitTransportError):
        transport.remote_head(_REPOSITORY, "repair/missing")
    with pytest.raises(ValueError):
        transport.remote_head("Other/Repo", "repair/test")

def test_inspect_changes_reports_content_and_added_text(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)
    (worktree / "file.txt").write_bytes(b"hello\nchanged\n")
    (worktree / "new.txt").write_bytes(b"brand new\n")

    changes, unstaged = transport.inspect_changes(
        worktree, sha, include_uncommitted=True
    )

    assert [change.path for change in changes] == ["file.txt", "new.txt"]
    modified, added = changes
    assert modified.status == "modified"
    assert modified.added_lines == 1
    assert modified.deleted_lines == 0
    assert modified.old_content == b"hello\n"
    assert modified.new_content == b"hello\nchanged\n"
    assert "changed" in modified.added_text
    assert added.status == "added"
    assert added.old_content is None
    assert added.new_content == b"brand new\n"
    assert unstaged is True


def test_inspect_changes_marks_deleted_files(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)
    (worktree / "file.txt").unlink()

    changes, unstaged = transport.inspect_changes(
        worktree, sha, include_uncommitted=True
    )

    assert len(changes) == 1
    assert changes[0].status == "deleted"
    assert changes[0].old_content == b"hello\n"
    assert changes[0].new_content is None
    assert unstaged is True


def test_inspect_committed_changes_excludes_uncommitted(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)
    (worktree / "file.txt").write_bytes(b"hello\nchanged\n")
    (worktree / "untracked.txt").write_bytes(b"ignore me\n")
    transport.stage(worktree, ("file.txt",))
    transport.commit(worktree, "Commit the change")

    changes, unstaged = transport.inspect_changes(
        worktree, sha, include_uncommitted=False
    )

    assert [change.path for change in changes] == ["file.txt"]
    assert changes[0].status == "modified"
    assert changes[0].new_content == b"hello\nchanged\n"
    assert unstaged is False


def test_inspect_changes_with_no_changes(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)

    changes, unstaged = transport.inspect_changes(
        worktree, sha, include_uncommitted=True
    )

    assert changes == ()
    assert unstaged is False

def test_inspect_changes_handles_spaces_unicode_and_plus_lines(tmp_path) -> None:
    remote, work, sha = _remote(tmp_path)
    transport, _ = _transport(tmp_path, work, remote)
    worktree = tmp_path / "managed" / "worktree"
    transport.add_detached(worktree, sha)
    (worktree / "space name.txt").write_bytes(b"space\nbad secret\n")
    (worktree / "caf\u00e9.txt").write_bytes(b"unicode\nchanged\n")
    (worktree / "plus.txt").write_bytes(
        b"base\n"
        b"+++ added header-like line\n"
        b"++ increment line\n"
        b"secret-after-increment\n"
        b"++ /dev/null\n"
    )
    (worktree / "new caf\u00e9.txt").write_bytes(b"new file\n")

    changes, _ = transport.inspect_changes(
        worktree, sha, include_uncommitted=True
    )
    by_path = {change.path: change for change in changes}

    assert by_path["space name.txt"].added_text == "bad secret\n"
    assert by_path["caf\u00e9.txt"].added_text == "changed\n"
    assert by_path["plus.txt"].added_text == (
        "+++ added header-like line\n"
        "++ increment line\n"
        "secret-after-increment\n"
        "++ /dev/null\n"
    )
    assert by_path["new caf\u00e9.txt"].added_text == "new file\n"