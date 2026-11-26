"""Trusted Git worktree transport for preserved worker worktrees.

Every invocation uses an allowlisted environment, a controlled global config,
no terminal prompt, no credential helper, disabled hooks, and no redirects.
Errors never include Git output, URLs with userinfo, or credentials.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from gitkeeper.host_mode.readiness import is_repository_name
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


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA_PATTERN.fullmatch(value) is None:
        raise _invalid("head_sha")
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
                timeout=timeout or self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise GitTransportError("The trusted Git process failed.") from None
        if check and result.returncode != 0:
            raise GitTransportError("The trusted Git process failed.")
        return result

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
