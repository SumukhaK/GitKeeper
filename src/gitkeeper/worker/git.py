"""Trusted Git worktree transport for preserved worker worktrees.

Every invocation uses an allowlisted environment, a controlled global config,
no terminal prompt, no credential helper, disabled hooks, and no redirects.
Errors never include Git output, URLs with userinfo, or credentials.
"""

from __future__ import annotations

import codecs
import os
import re
import stat
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from gitkeeper.host_mode.readiness import is_repository_name
from gitkeeper.policy.patch_scope import FileChange
from gitkeeper.worker.models import ActiveCheckoutSnapshot
from gitkeeper.worker.worktree import WorktreeError, WorktreePathError

_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_CLONE_URL_PATTERN = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\.git",
    re.IGNORECASE,
)
_FORBIDDEN_COMPONENT_CHARACTERS = frozenset('<>:"/\\|?*')
_RESERVED_NAMES = re.compile(
    r"(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$"
)
_REPARSE_POINT = 0x400

Runner = Callable[..., subprocess.CompletedProcess[str]]


class GitTransportError(WorktreeError):
    """A trusted Git operation failed without exposing output or secrets."""


class GitPushRejected(WorktreeError):
    """A proven non-fast-forward or forbidden push."""


class GitPushOutcomeUnknown(WorktreeError):
    """A push that may have been sent but cannot be confirmed."""


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA_PATTERN.fullmatch(value) is None:
        raise _invalid("head_sha")
    return value


def _branch(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid("branch")
    if value in {"HEAD", "@", "main", "master"} or value.startswith("refs/"):
        raise _invalid("branch")
    if value.startswith(("-", "/", ".")) or value.endswith(("/", ".lock", ".")):
        raise _invalid("branch")
    if (
        ".." in value
        or "//" in value
        or any(character in value for character in "~^:?*[\\")
        or any(ord(character) < 32 or character.isspace() for character in value)
    ):
        raise _invalid("branch")
    return value


def _safe_component(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(character in _FORBIDDEN_COMPONENT_CHARACTERS for character in value)
        or any(ord(character) < 32 for character in value)
        or value != value.rstrip(" .")
        or _RESERVED_NAMES.fullmatch(value)
    ):
        raise ValueError(f"Invalid {field}.")
    return value


def validate_clone_url(clone_url: object, repository: object) -> str:
    """Accept only a credential-free canonical GitHub HTTPS clone URL."""
    if not isinstance(repository, str) or not is_repository_name(repository):
        raise _invalid("repository")
    if not isinstance(clone_url, str):
        raise _invalid("clone_url")
    parts = urlsplit(clone_url)
    if (
        parts.scheme != "https"
        or parts.netloc.casefold() != "github.com"
        or parts.username is not None
        or parts.password is not None
        or parts.port is not None
        or parts.query
        or parts.fragment
    ):
        raise _invalid("clone_url")
    match = _CLONE_URL_PATTERN.fullmatch(clone_url)
    if match is None:
        raise _invalid("clone_url")
    owner, name = repository.split("/", 1)
    if (
        match.group(1).casefold() != owner.casefold()
        or match.group(2).casefold() != name.casefold()
    ):
        raise _invalid("clone_url")
    return clone_url


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _normalised(value: Path) -> Path:
    try:
        return Path(os.path.normcase(str(value.resolve(strict=False))))
    except OSError:
        raise WorktreePathError("The path could not be resolved safely.") from None


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _validated_checkouts(values: object) -> tuple[Path, ...]:
    if not isinstance(values, tuple):
        raise TypeError("active_checkouts must be a tuple of paths.")
    result: list[Path] = []
    for value in values:
        if not isinstance(value, Path) or not value.is_absolute():
            raise WorktreePathError("An active checkout must be an absolute path.")
        if not value.exists() or not value.is_dir():
            raise WorktreePathError(
                "An active checkout must be an existing directory."
            )
        if _is_reparse_point(value):
            raise WorktreePathError(
                "An active checkout must not be a reparse point."
            )
        result.append(_normalised(value))
    if len(set(result)) != len(result):
        raise WorktreePathError("Active checkouts must be unique.")
    return tuple(result)


def _validated_cache_root(
    cache_root: object, checkouts: tuple[Path, ...]
) -> Path:
    if not isinstance(cache_root, Path) or not cache_root.is_absolute():
        raise WorktreePathError("cache_root must be an absolute path.")
    text = str(cache_root)
    if text.startswith("\\\\") or text.startswith("//"):
        raise WorktreePathError("cache_root must not be a UNC path.")
    if _is_reparse_point(cache_root):
        raise WorktreePathError("cache_root must not be a reparse point.")
    resolved = _normalised(cache_root)
    for checkout in checkouts:
        if _inside(resolved, checkout) or _inside(checkout, resolved):
            raise WorktreePathError(
                "cache_root must not overlap an active checkout."
            )
    return cache_root


def _unquote_path(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        try:
            return codecs.escape_decode(value[1:-1].encode("utf-8"))[0].decode(
                "utf-8", "replace"
            )
        except (ValueError, UnicodeDecodeError):
            return value[1:-1]
    return value


def _parse_status(output: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    dirty: list[str] = []
    untracked: list[str] = []
    entries = output.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry or len(entry) < 4:
            continue
        code = entry[:2]
        path = entry[3:]
        if code == "??":
            untracked.append(path)
        else:
            dirty.append(path)
        if "R" in code or "C" in code:
            index += 1
    return tuple(sorted(set(dirty))), tuple(sorted(set(untracked)))


class GitCliTransport:
    """Real Git worktree operations against one bound repository."""

    def __init__(
        self,
        repository: str,
        cache_root: Path,
        active_checkouts: tuple[Path, ...],
        clone_url: str,
        git_executable: str = "git",
        *,
        runner: Runner | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not isinstance(repository, str) or not is_repository_name(repository):
            raise _invalid("repository")
        if not isinstance(git_executable, str) or not git_executable.strip():
            raise _invalid("git_executable")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise _invalid("timeout_seconds")
        self._repository = repository
        self._checkouts = _validated_checkouts(active_checkouts)
        self._cache_root = _validated_cache_root(cache_root, self._checkouts)
        self._clone_url = validate_clone_url(clone_url, repository)
        self._git = git_executable
        self._runner: Runner = runner or subprocess.run
        self._timeout = float(timeout_seconds)
        try:
            self._cache_root.mkdir(parents=True, exist_ok=True)
            self._controlled_config = self._cache_root / ".gitkeeper-gitconfig"
            self._controlled_config.write_text("", encoding="utf-8")
        except OSError:
            raise WorktreePathError(
                "cache_root must be a writable directory."
            ) from None

    def _environment(self) -> dict[str, str]:
        source = os.environ
        environment = {
            "PATH": source.get("PATH", ""),
            "PATHEXT": source.get("PATHEXT", ""),
            "SystemRoot": source.get("SystemRoot", ""),
            "ComSpec": source.get("ComSpec", ""),
            "TEMP": source.get("TEMP", ""),
            "TMP": source.get("TMP", ""),
            "USERPROFILE": source.get("USERPROFILE", ""),
            "APPDATA": source.get("APPDATA", ""),
            "LOCALAPPDATA": source.get("LOCALAPPDATA", ""),
            "HOME": source.get("HOME", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self._controlled_config),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "GCM_INTERACTIVE": "never",
        }
        return environment

    def _config_arguments(self) -> list[str]:
        return [
            "-c",
            "core.hooksPath=",
            "-c",
            "credential.helper=",
            "-c",
            "http.followRedirects=false",
            "-c",
            "advice.detachedHead=false",
            "-c",
            "gc.auto=0",
            "-c",
            "protocol.version=2",
            "-c",
            "core.fsmonitor=",
            "-c",
            "submodule.recurse=false",
            "-c",
            "safe.directory=*",
        ]

    def _run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [self._git, *self._config_arguments(), *arguments]
        try:
            result = self._runner(
                command,
                cwd=None if cwd is None else str(cwd),
                env=self._environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise GitTransportError("The trusted Git process failed.") from None
        if check and result.returncode != 0:
            raise GitTransportError("The trusted Git process failed.")
        return result

    def _run_bytes(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout: float | None = None,
    ) -> bytes:
        command = [self._git, *self._config_arguments(), *arguments]
        try:
            result = self._runner(
                command,
                cwd=str(cwd),
                env=self._environment(),
                capture_output=True,
                text=False,
                timeout=timeout or self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise GitTransportError("The trusted Git process failed.") from None
        if result.returncode != 0:
            raise GitTransportError("The trusted Git process failed.")
        return result.stdout

    def _cache_path(self) -> Path:
        owner, name = self._repository.split("/", 1)
        _safe_component(owner, "repository owner")
        _safe_component(name, "repository name")
        return self._cache_root / owner / f"{name}.git"

    def prepare(self, repository: str) -> Path:
        if repository != self._repository:
            raise ValueError("repository does not match the bound repository.")
        cache = self._cache_path()
        if cache.exists():
            if not cache.is_dir() or not (cache / "HEAD").exists():
                raise GitTransportError(
                    "The external Git cache is not a bare repository."
                )
            return cache
        cache.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            ["clone", "--bare", "--no-tags", self._clone_url, str(cache)],
            timeout=self._timeout,
        )
        if not (cache / "HEAD").exists():
            raise GitTransportError("The external Git cache could not be created.")
        return cache

    def fetch(self, repository: str, head_sha: str) -> None:
        if repository != self._repository:
            raise ValueError("repository does not match the bound repository.")
        sha = _sha(head_sha)
        cache = self.prepare(repository)
        self._run(
            [
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "origin",
                sha,
            ],
            cwd=cache,
        )
        if not self.has_commit(sha):
            raise GitTransportError(
                "The requested commit is not present after the fetch."
            )

    def has_commit(self, head_sha: str) -> bool:
        sha = _sha(head_sha)
        cache = self._cache_path()
        if not cache.exists():
            return False
        result = self._run(
            ["cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=cache,
            check=False,
        )
        return result.returncode == 0

    def add_detached(self, path: Path, head_sha: str) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        sha = _sha(head_sha)
        cache = self._cache_path()
        if not cache.exists():
            raise GitTransportError("The external Git cache does not exist.")
        if path.exists():
            raise GitTransportError("The worktree path already exists.")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise GitTransportError(
                "The worktree parent directory could not be created."
            ) from None
        self._run(
            ["worktree", "add", "--detach", str(path), sha],
            cwd=cache,
        )

    def head(self, path: Path) -> str:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        result = self._run(["rev-parse", "HEAD"], cwd=path)
        value = result.stdout.strip()
        if _SHA_PATTERN.fullmatch(value) is None:
            raise GitTransportError("Git returned an invalid worktree HEAD.")
        return value

    def is_registered(self, path: Path) -> bool:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        cache = self._cache_path()
        if not cache.exists():
            return False
        result = self._run(["worktree", "list", "--porcelain"], cwd=cache)
        target = _normalised(path)
        for line in result.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            if _normalised(Path(line[len("worktree ") :])) == target:
                return True
        return False

    def snapshot_active_checkouts(self) -> tuple[ActiveCheckoutSnapshot, ...]:
        snapshots: list[ActiveCheckoutSnapshot] = []
        for checkout in self._checkouts:
            branch_result = self._run(
                ["symbolic-ref", "--short", "-q", "HEAD"],
                cwd=checkout,
                check=False,
            )
            branch = (
                branch_result.stdout.strip()
                if branch_result.returncode == 0 and branch_result.stdout.strip()
                else None
            )
            head_sha = self._run(["rev-parse", "HEAD"], cwd=checkout).stdout.strip()
            if _SHA_PATTERN.fullmatch(head_sha) is None:
                raise GitTransportError(
                    "Git returned an invalid checkout HEAD."
                )
            status = self._run(
                [
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                ],
                cwd=checkout,
            ).stdout
            dirty, untracked = _parse_status(status)
            snapshots.append(
                ActiveCheckoutSnapshot(
                    path=checkout,
                    branch=branch,
                    head_sha=head_sha,
                    dirty_paths=dirty,
                    untracked_paths=untracked,
                )
            )
        return tuple(snapshots)

    def stage(self, path: Path, paths: tuple[str, ...]) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        if not isinstance(paths, tuple) or not paths:
            raise _invalid("paths")
        for item in paths:
            if not isinstance(item, str) or not item:
                raise _invalid("paths")
            if any(character in item for character in "\r\n\x00"):
                raise _invalid("paths")
            candidate = Path(item)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise _invalid("paths")
        self._run(["add", "--", *paths], cwd=path)

    def commit(self, path: Path, message: str) -> str:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        if not isinstance(message, str) or not message.strip():
            raise _invalid("message")
        if any(character in message for character in "\r\n\x00"):
            raise _invalid("message")
        self._run(
            [
                "-c",
                "user.name=GitKeeper Worker",
                "-c",
                "user.email=gitkeeper@localhost",
                "commit",
                "-m",
                message,
            ],
            cwd=path,
        )
        value = self._run(["rev-parse", "HEAD"], cwd=path).stdout.strip()
        if _SHA_PATTERN.fullmatch(value) is None:
            raise GitTransportError("Git returned an invalid commit SHA.")
        return value

    def is_ancestor(self, path: Path, older: str, newer: str) -> bool:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        result = self._run(
            ["merge-base", "--is-ancestor", _sha(older), _sha(newer)],
            cwd=path,
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise GitTransportError("Git could not compare the two commits.")

    def commit_count(self, path: Path, older: str, newer: str) -> int:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        output = self._run(
            ["rev-list", "--count", f"{_sha(older)}..{_sha(newer)}"],
            cwd=path,
        ).stdout.strip()
        try:
            count = int(output)
        except ValueError:
            raise GitTransportError("Git returned an invalid commit count.") from None
        if count < 0:
            raise GitTransportError("Git returned an invalid commit count.")
        return count

    def push(self, path: Path, branch: str, commit_sha: str) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        target = _branch(branch)
        sha = _sha(commit_sha)
        command = [
            self._git,
            *self._config_arguments(),
            "push",
            "--porcelain",
            "origin",
            f"{sha}:refs/heads/{target}",
        ]
        try:
            result = self._runner(
                command,
                cwd=str(path),
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise GitPushOutcomeUnknown("The push outcome is unknown.") from None
        if result.returncode == 0:
            return
        text = f"{result.stdout}\n{result.stderr}".casefold()
        if any(
            marker in text
            for marker in (
                "non-fast-forward",
                "fetch first",
                "rejected",
                "protected branch",
                "permission denied",
                "policy",
            )
        ):
            raise GitPushRejected("The push was rejected.")
        raise GitPushOutcomeUnknown("The push outcome is unknown.")

    def remote_head(self, repository: str, branch: str) -> str:
        if repository != self._repository:
            raise ValueError("repository does not match the bound repository.")
        target = _branch(branch)
        cache = self.prepare(repository)
        output = self._run(
            ["ls-remote", "--heads", "origin", f"refs/heads/{target}"],
            cwd=cache,
        ).stdout.strip()
        if not output:
            raise GitTransportError("The remote branch does not exist.")
        parts = output.split()
        if len(parts) < 2 or _SHA_PATTERN.fullmatch(parts[0]) is None:
            raise GitTransportError("Git returned an invalid remote head.")
        return parts[0]

    def inspect_changes(
        self,
        path: Path,
        starting_sha: str,
        *,
        include_uncommitted: bool,
    ) -> tuple[tuple[FileChange, ...], bool]:
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorktreePathError("The worktree path must be absolute.")
        if type(include_uncommitted) is not bool:
            raise TypeError("include_uncommitted must be a boolean.")
        sha = _sha(starting_sha)
        revision = sha if include_uncommitted else f"{sha}..HEAD"
        raw = self._run(
            [
                "diff",
                "--raw",
                "-z",
                "--no-abbrev",
                "--no-renames",
                revision,
                "--",
            ],
            cwd=path,
        ).stdout
        counts = self._numstat(path, revision)
        added_text = self._added_text(path, revision)
        changes: list[FileChange] = []
        fields = raw.split("\0")
        for index in range(0, len(fields) - 1, 2):
            header = fields[index]
            file_path = fields[index + 1]
            if not header.startswith(":") or not file_path:
                continue
            metadata = header[1:].split(" ")
            if len(metadata) != 5:
                continue
            old_mode, new_mode, old_sha, new_sha, letter = metadata
            added_lines, deleted_lines = counts.get(file_path, (0, 0))
            new_content = self._blob(path, new_sha)
            if new_content is None and include_uncommitted:
                candidate = path / file_path
                if candidate.is_file():
                    new_content = candidate.read_bytes()
            changes.append(
                FileChange(
                    path=file_path,
                    status={"A": "added", "M": "modified", "D": "deleted"}.get(
                        letter, letter
                    ),
                    added_lines=added_lines,
                    deleted_lines=deleted_lines,
                    old_mode=None if old_mode == "000000" else old_mode,
                    new_mode=None if new_mode == "000000" else new_mode,
                    old_content=self._blob(path, old_sha),
                    new_content=new_content,
                    added_text=added_text.get(file_path, ""),
                )
            )

        unstaged = False
        if include_uncommitted:
            unstaged = self._has_unstaged(path)
            for untracked in self._untracked(path):
                content = (path / untracked).read_bytes()
                changes.append(
                    FileChange(
                        path=untracked,
                        status="added",
                        added_lines=len(content.splitlines()),
                        deleted_lines=0,
                        old_mode=None,
                        new_mode="100644",
                        old_content=None,
                        new_content=content,
                        added_text=content.decode("utf-8", "replace"),
                    )
                )
                unstaged = True
        changes.sort(key=lambda change: change.path)
        return tuple(changes), unstaged

    def _blob(self, path: Path, sha: str) -> bytes | None:
        if not sha or all(character == "0" for character in sha):
            return None
        return self._run_bytes(["cat-file", "blob", sha], cwd=path)

    def _numstat(
        self, path: Path, revision: str
    ) -> dict[str, tuple[int, int]]:
        output = self._run(
            ["diff", "--numstat", "-z", "--no-renames", revision, "--"],
            cwd=path,
        ).stdout
        result: dict[str, tuple[int, int]] = {}
        for entry in output.split("\0"):
            if not entry:
                continue
            parts = entry.split("\t", 2)
            if len(parts) != 3:
                continue
            added, deleted, file_path = parts
            result[file_path] = (
                0 if added == "-" else int(added),
                0 if deleted == "-" else int(deleted),
            )
        return result

    def _added_text(self, path: Path, revision: str) -> dict[str, str]:
        output = self._run(
            [
                "-c",
                "core.quotePath=false",
                "diff",
                "-U0",
                "--no-color",
                "--no-ext-diff",
                "--no-renames",
                "--no-prefix",
                revision,
                "--",
            ],
            cwd=path,
        ).stdout
        result: dict[str, str] = {}
        current: str | None = None
        in_hunk = False
        for line in output.splitlines():
            if line.startswith("diff --git "):
                current = None
                in_hunk = False
            elif line.startswith("+++ "):
                target = line[4:]
                if target == "/dev/null":
                    current = None
                else:
                    current = _unquote_path(target.rstrip("\t"))
                    result.setdefault(current, "")
                in_hunk = False
            elif line.startswith("@@"):
                in_hunk = current is not None
            elif in_hunk and line.startswith("+"):
                result[current] += line[1:] + "\n"
        return result

    def _untracked(self, path: Path) -> tuple[str, ...]:
        output = self._run(
            ["ls-files", "--others", "--exclude-standard", "-z"], cwd=path
        ).stdout
        return tuple(item for item in output.split("\0") if item)

    def _has_unstaged(self, path: Path) -> bool:
        result = self._run(["diff", "--quiet"], cwd=path, check=False)
        if result.returncode == 0:
            return False
        if result.returncode == 1:
            return True
        raise GitTransportError("Git could not inspect the working tree.")
