"""The machine-checkable patch-scope rule for the single-worker pilot.

The worker may commit only a small, reversible, test-backed repair inside an
isolated worktree. Everything else is a violation, and any violation means
"do not commit, keep the worktree, mark blocked, wait for an explicit retry".

Checks run in a fixed order: identity, structure, paths, manifest capability,
intent, then publication order. The check order is the reporting order, so the
first violation an operator reads is the cheapest one to explain.

Thresholds are code constants on purpose. A Setu-mobile config pull request
must never be able to widen its own blast radius.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

OWNER_LOGIN = "SumukhaK"
PILOT_REPOSITORY = "SumukhaK/Setu-mobile"

ALLOWED_PATTERNS = (
    "app/src/**",
    "core/**/src/**",
    "feature/**/src/**",
    "tests/**",
    "docs/**",
    "app/src/main/AndroidManifest.xml",
)

DENIED_PATTERNS = (
    ".github/**",
    "scripts/**",
    "gradle/**",
    "gradlew",
    "gradlew.bat",
    "**/*.gradle.kts",
    "gradle.properties",
    "config/**",
    "third_party/**",
    "index.html",
    "prototype.*",
    "**/network_security_config.xml",
    "**/proguard*",
    "**/*.keystore",
    "**/*.jks",
    "**/*.pem",
    "**/*.p12",
    ".gitmodules",
    ".gitattributes",
)

SOURCE_PATTERNS = ("app/src/**", "core/**/src/**", "feature/**/src/**")
TEST_PATTERNS = (
    "app/src/test/**",
    "app/src/androidTest/**",
    "core/**/src/test/**",
    "core/**/src/androidTest/**",
    "feature/**/src/test/**",
    "feature/**/src/androidTest/**",
    "tests/**",
)

MANIFEST_FILENAME = "AndroidManifest.xml"
GUARDED_ATTRIBUTES = ("exported", "usesCleartextTraffic", "debuggable", "allowBackup")
PERMISSION_TAGS = frozenset({"uses-permission", "uses-permission-sdk-23", "permission"})
FEATURE_TAGS = frozenset({"uses-feature"})
COMPONENT_TAGS = frozenset({"activity", "activity-alias", "service", "receiver", "provider"})
ANDROID_NAMESPACE = "{http://schemas.android.com/apk/res/android}"

ALLOWED_STATUSES = frozenset({"added", "modified"})
MODE_SYMLINK = "120000"
MODE_SUBMODULE = "160000"

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bpassword\s*[=:]\s*\S"),
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*\S"),
    re.compile(r"(?i)\bsecret\s*[=:]\s*\S"),
)


class ScopeReason(str, Enum):
    PATH_MALFORMED = "scope-path-malformed"
    PATH_DENIED = "scope-path-denied"
    UNSTAGED = "scope-unstaged"
    EMPTY = "scope-empty"
    TOO_MANY_FILES = "scope-too-many-files"
    TOO_LARGE = "scope-too-large"
    FILE_TOO_LARGE = "scope-file-too-large"
    DELETION = "scope-deletion"
    RENAME = "scope-rename"
    MODE_CHANGE = "scope-mode-change"
    SYMLINK = "scope-symlink"
    SUBMODULE = "scope-submodule"
    BINARY = "scope-binary"
    MANIFEST_CAPABILITY = "scope-manifest-capability"
    MANIFEST_UNREADABLE = "scope-manifest-unreadable"
    SECRET_PATTERN = "scope-secret-pattern"
    NO_SOURCE_CHANGE = "scope-no-source-change"
    NO_TEST_CHANGE = "scope-no-test-change"
    WORKTREE_OUTSIDE = "scope-worktree-outside"
    WORKTREE_ACTIVE_CHECKOUT = "scope-worktree-active-checkout"
    HEAD_MISMATCH = "head-mismatch"
    PR_NOT_ELIGIBLE = "pr-not-eligible"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ScopeViolation:
    code: ScopeReason
    detail: str
    path: str | None = None


@dataclass(frozen=True)
class ScopeDecision:
    violations: tuple[ScopeViolation, ...]

    @property
    def allowed(self) -> bool:
        return not self.violations

    @property
    def reason_codes(self) -> tuple[str, ...]:
        seen: list[str] = []
        for violation in self.violations:
            if violation.code.value not in seen:
                seen.append(violation.code.value)
        return tuple(seen)

    def summary(self) -> str:
        if self.allowed:
            return "Patch scope accepted."
        return "; ".join(
            f"{violation.code.value} ({violation.detail})"
            for violation in self.violations
        )


@dataclass(frozen=True)
class FileChange:
    """One staged path as the worker left it.

    ``added_text`` holds only the added lines (the secret scan must not trip on
    pre-existing content), while ``old_content``/``new_content`` hold whole-file
    bytes for the Android manifest capability comparison.
    """

    path: str
    status: str = "modified"
    added_lines: int = 0
    deleted_lines: int = 0
    old_mode: str | None = "100644"
    new_mode: str | None = "100644"
    old_content: bytes | None = None
    new_content: bytes | None = None
    added_text: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.path, str):
            raise TypeError("A file change path must be a string.")
        for name in ("added_lines", "deleted_lines"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        if not isinstance(self.status, str):
            raise TypeError("A file change status must be a string.")

    @property
    def changed_lines(self) -> int:
        return self.added_lines + self.deleted_lines


@dataclass(frozen=True)
class RemotePrState:
    number: int
    repository: str
    author: str
    head_sha: str | None
    is_open: bool
    is_draft: bool

    def __post_init__(self) -> None:
        if not isinstance(self.number, int) or isinstance(self.number, bool):
            raise TypeError("A pull request number must be an integer.")


@dataclass(frozen=True)
class ScopeRequest:
    starting_sha: str
    head_sha: str
    worktree_path: Path
    managed_root: Path
    active_checkouts: tuple[Path, ...]
    changes: tuple[FileChange, ...]
    unstaged: bool = False
    requires_test_change: bool = False
    remote: RemotePrState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.changes, tuple):
            raise TypeError("Scope request changes must be a tuple.")
        if not isinstance(self.active_checkouts, tuple):
            raise TypeError("Active checkouts must be a tuple.")
        for name in ("worktree_path", "managed_root"):
            if not isinstance(getattr(self, name), Path):
                raise TypeError(f"{name} must be a pathlib.Path.")


@dataclass(frozen=True)
class ManifestSnapshot:
    permissions: frozenset[str]
    features: frozenset[str]
    components: frozenset[str]
    intent_filters: frozenset[tuple[str, int]]
    guarded: frozenset[tuple[str, str]]


class ScopeError(RuntimeError):
    """Raised when the worktree cannot be inspected, so scope cannot be proven."""


def _segments(path: str) -> tuple[str, ...]:
    return tuple(segment for segment in path.split("/") if segment not in ("", "."))


def _match_segments(pattern: Sequence[str], segments: Sequence[str]) -> bool:
    if not pattern:
        return not segments
    head = pattern[0]
    tail = pattern[1:]
    if head == "**":
        if _match_segments(tail, segments):
            return True
        return bool(segments) and _match_segments(pattern, segments[1:])
    if not segments:
        return False
    if not _glob_segment_matches(head, segments[0]):
        return False
    return _match_segments(tail, segments[1:])


def _glob_segment_matches(pattern: str, segment: str) -> bool:
    candidate = segment.casefold()
    regex = ["^"]
    for character in pattern.casefold():
        if character == "*":
            regex.append("[^/]*")
        elif character == "?":
            regex.append("[^/]")
        else:
            regex.append(re.escape(character))
    regex.append("$")
    return re.match("".join(regex), candidate) is not None


def _matches(pattern: str, path: str) -> bool:
    return _match_segments(_segments(pattern), _segments(path))


def _canonical(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/"))


def path_is_allowed(path: str) -> bool:
    """True when a repository-relative path is inside the allowlist and deny-free."""
    if not isinstance(path, str) or not path:
        return False
    candidate = _canonical(path)
    if any(_matches(pattern, candidate) for pattern in DENIED_PATTERNS):
        return False
    return any(_matches(pattern, candidate) for pattern in ALLOWED_PATTERNS)


def path_is_malformed(path: str) -> bool:
    if not isinstance(path, str) or not path or path.strip() != path:
        return True
    if "\\" in path or "\x00" in path:
        return True
    if path.startswith("/") or path.startswith("~"):
        return True
    if len(path) > 1 and path[1] == ":":
        return True
    return any(segment == ".." for segment in path.split("/"))


def _is_source(path: str) -> bool:
    candidate = _canonical(path)
    if candidate.rsplit("/", 1)[-1].casefold() == MANIFEST_FILENAME.casefold():
        return False
    return any(_matches(pattern, candidate) for pattern in SOURCE_PATTERNS)


def _is_test(path: str) -> bool:
    candidate = _canonical(path)
    return any(_matches(pattern, candidate) for pattern in TEST_PATTERNS)


def _is_manifest(path: str) -> bool:
    return _canonical(path).rsplit("/", 1)[-1].casefold() == MANIFEST_FILENAME.casefold()


def _tag(element: ElementTree.Element) -> str:
    return element.tag.split("}", 1)[-1]


def _android_attribute(element: ElementTree.Element, name: str) -> str | None:
    value = element.get(f"{ANDROID_NAMESPACE}{name}")
    if value is None:
        value = element.get(name)
    return value


def _element_identity(element: ElementTree.Element) -> str:
    name = _android_attribute(element, "name")
    if name is None:
        return f"{_tag(element)}:<unnamed>"
    return f"{_tag(element)}:{name}"


def _manifest_snapshot(content: bytes) -> ManifestSnapshot:
    root = ElementTree.fromstring(content)
    permissions: set[str] = set()
    features: set[str] = set()
    components: set[str] = set()
    intent_filters: set[tuple[str, int]] = set()
    guarded: set[tuple[str, str]] = set()

    for index, element in enumerate(root.iter()):
        tag = _tag(element)
        identity = _element_identity(element)
        if tag in PERMISSION_TAGS:
            permissions.add(_android_attribute(element, "name") or f"<unnamed#{index}>")
        if tag in FEATURE_TAGS:
            features.add(_android_attribute(element, "name") or f"<unnamed#{index}>")
        if tag in COMPONENT_TAGS:
            components.add(identity)
        filters = sum(1 for child in element if _tag(child) == "intent-filter")
        if filters:
            intent_filters.add((identity, filters))
        for attribute in GUARDED_ATTRIBUTES:
            value = _android_attribute(element, attribute)
            if value is not None:
                guarded.add((f"{identity}@{attribute}", value))

    return ManifestSnapshot(
        permissions=frozenset(permissions),
        features=frozenset(features),
        components=frozenset(components),
        intent_filters=frozenset(intent_filters),
        guarded=frozenset(guarded),
    )


def _manifest_violations(change: FileChange) -> list[ScopeViolation]:
    if change.old_content is None or change.new_content is None:
        return [
            ScopeViolation(
                ScopeReason.MANIFEST_UNREADABLE,
                "the manifest revision could not be read for comparison",
                change.path,
            )
        ]
    try:
        before = _manifest_snapshot(change.old_content)
        after = _manifest_snapshot(change.new_content)
    except ElementTree.ParseError as error:
        return [
            ScopeViolation(
                ScopeReason.MANIFEST_UNREADABLE,
                f"the manifest is not valid XML: {error}",
                change.path,
            )
        ]

    violations: list[ScopeViolation] = []
    if before.permissions != after.permissions:
        violations.append(
            ScopeViolation(
                ScopeReason.MANIFEST_CAPABILITY,
                "the set of declared permissions changed",
                change.path,
            )
        )
    if before.features != after.features:
        violations.append(
            ScopeViolation(
                ScopeReason.MANIFEST_CAPABILITY,
                "the set of declared uses-feature entries changed",
                change.path,
            )
        )
    if before.components != after.components:
        violations.append(
            ScopeViolation(
                ScopeReason.MANIFEST_CAPABILITY,
                "a component was added or removed",
                change.path,
            )
        )
    if before.intent_filters != after.intent_filters:
        violations.append(
            ScopeViolation(
                ScopeReason.MANIFEST_CAPABILITY,
                "an intent-filter was added or removed",
                change.path,
            )
        )
    if before.guarded != after.guarded:
        violations.append(
            ScopeViolation(
                ScopeReason.MANIFEST_CAPABILITY,
                "a security-relevant attribute changed: " + ", ".join(GUARDED_ATTRIBUTES),
                change.path,
            )
        )
    return violations


def _secret_violations(change: FileChange) -> list[ScopeViolation]:
    for pattern in SECRET_PATTERNS:
        if pattern.search(change.added_text):
            return [
                ScopeViolation(
                    ScopeReason.SECRET_PATTERN,
                    f"an added line matches a credential pattern ({pattern.pattern})",
                    change.path,
                )
            ]
    return []


def _structure_violations(changes: Sequence[FileChange]) -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    for change in changes:
        if change.status not in ALLOWED_STATUSES:
            if change.status == "deleted":
                violations.append(
                    ScopeViolation(
                        ScopeReason.DELETION, "file deletion is not allowed", change.path
                    )
                )
            else:
                violations.append(
                    ScopeViolation(
                        ScopeReason.RENAME,
                        f"{change.status} changes are not allowed",
                        change.path,
                    )
                )
        if MODE_SYMLINK in (change.old_mode, change.new_mode):
            violations.append(
                ScopeViolation(
                    ScopeReason.SYMLINK, "symlinks are not allowed", change.path
                )
            )
        if MODE_SUBMODULE in (change.old_mode, change.new_mode):
            violations.append(
                ScopeViolation(
                    ScopeReason.SUBMODULE, "submodules are not allowed", change.path
                )
            )
        if (
            change.old_mode
            and change.new_mode
            and change.old_mode != change.new_mode
            and MODE_SYMLINK not in (change.old_mode, change.new_mode)
            and MODE_SUBMODULE not in (change.old_mode, change.new_mode)
        ):
            violations.append(
                ScopeViolation(
                    ScopeReason.MODE_CHANGE,
                    f"file mode changed from {change.old_mode} to {change.new_mode}",
                    change.path,
                )
            )
        if any(
            content is not None and b"\x00" in content
            for content in (change.old_content, change.new_content)
        ):
            violations.append(
                ScopeViolation(
                    ScopeReason.BINARY, "binary content is not allowed", change.path
                )
            )
    return violations


def _path_violations(changes: Sequence[FileChange]) -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    for change in changes:
        if path_is_malformed(change.path):
            violations.append(
                ScopeViolation(
                    ScopeReason.PATH_MALFORMED,
                    "the path escapes the repository or is not repo-relative",
                    change.path,
                )
            )
        elif not path_is_allowed(change.path):
            violations.append(
                ScopeViolation(
                    ScopeReason.PATH_DENIED,
                    "the path is outside the pilot allowlist",
                    change.path,
                )
            )
    return violations


def _limit_violations(
    changes: Sequence[FileChange], policy: "PatchScopePolicy"
) -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    if len(changes) > policy.MAX_FILES:
        violations.append(
            ScopeViolation(
                ScopeReason.TOO_MANY_FILES,
                f"{len(changes)} files changed, the limit is {policy.MAX_FILES}",
            )
        )
    total = sum(change.changed_lines for change in changes)
    if total > policy.MAX_TOTAL_LINES:
        violations.append(
            ScopeViolation(
                ScopeReason.TOO_LARGE,
                f"{total} changed lines, the limit is {policy.MAX_TOTAL_LINES}",
            )
        )
    for change in changes:
        if change.changed_lines > policy.MAX_FILE_LINES:
            violations.append(
                ScopeViolation(
                    ScopeReason.FILE_TOO_LARGE,
                    f"{change.changed_lines} changed lines in one file, "
                    f"the limit is {policy.MAX_FILE_LINES}",
                    change.path,
                )
            )
    return violations


def _intent_violations(
    changes: Sequence[FileChange], requires_test_change: bool
) -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    if not any(_is_source(change.path) for change in changes):
        violations.append(
            ScopeViolation(
                ScopeReason.NO_SOURCE_CHANGE,
                "no source file under a module src/ directory changed",
            )
        )
    if requires_test_change and not any(_is_test(change.path) for change in changes):
        violations.append(
            ScopeViolation(
                ScopeReason.NO_TEST_CHANGE,
                "the deterministic check failed, so the repair must change a test",
            )
        )
    for change in changes:
        violations.extend(_secret_violations(change))
    return violations


def _identity_violations(request: ScopeRequest) -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    worktree = request.worktree_path.resolve()
    managed = request.managed_root.resolve()
    if not worktree.is_relative_to(managed) or worktree == managed:
        violations.append(
            ScopeViolation(
                ScopeReason.WORKTREE_OUTSIDE,
                f"{worktree} is not an isolated worktree under {managed}",
            )
        )
    for checkout in request.active_checkouts:
        if worktree == checkout.resolve():
            violations.append(
                ScopeViolation(
                    ScopeReason.WORKTREE_ACTIVE_CHECKOUT,
                    f"{worktree} is an active repository checkout",
                )
            )
    if request.head_sha != request.starting_sha:
        violations.append(
            ScopeViolation(
                ScopeReason.HEAD_MISMATCH,
                f"HEAD moved from {request.starting_sha} to {request.head_sha}",
            )
        )
    return violations


def _remote_violations(remote: RemotePrState | None, starting_sha: str) -> list[ScopeViolation]:
    if remote is None:
        return [
            ScopeViolation(
                ScopeReason.PR_NOT_ELIGIBLE,
                "the remote pull request state could not be confirmed",
            )
        ]
    violations: list[ScopeViolation] = []
    if not remote.is_open:
        violations.append(
            ScopeViolation(ScopeReason.PR_NOT_ELIGIBLE, "the pull request is not open")
        )
    if remote.is_draft:
        violations.append(
            ScopeViolation(
                ScopeReason.PR_NOT_ELIGIBLE, "draft pull requests are not eligible"
            )
        )
    if remote.author.casefold() != OWNER_LOGIN.casefold():
        violations.append(
            ScopeViolation(
                ScopeReason.PR_NOT_ELIGIBLE,
                f"the pull request author is {remote.author}, not {OWNER_LOGIN}",
            )
        )
    if remote.repository.casefold() != PILOT_REPOSITORY.casefold():
        violations.append(
            ScopeViolation(
                ScopeReason.PR_NOT_ELIGIBLE,
                f"{remote.repository} is not the pilot repository",
            )
        )
    if not remote.head_sha:
        violations.append(
            ScopeViolation(
                ScopeReason.PR_NOT_ELIGIBLE, "the remote head SHA is unknown"
            )
        )
    elif remote.head_sha != starting_sha:
        violations.append(
            ScopeViolation(
                ScopeReason.HEAD_MISMATCH,
                f"the remote head moved from {starting_sha} to {remote.head_sha}",
            )
        )
    return violations


class PatchScopePolicy:
    """Evaluate the pilot patch-scope rule. Any violation blocks publication."""

    MAX_FILES = 10
    MAX_TOTAL_LINES = 400
    MAX_FILE_LINES = 150

    ALLOWED_PATTERNS = ALLOWED_PATTERNS
    DENIED_PATTERNS = DENIED_PATTERNS

    def evaluate(self, request: ScopeRequest) -> ScopeDecision:
        if not isinstance(request, ScopeRequest):
            raise TypeError("evaluate() requires a ScopeRequest.")

        violations: list[ScopeViolation] = []
        violations.extend(_identity_violations(request))

        if request.unstaged:
            violations.append(
                ScopeViolation(
                    ScopeReason.UNSTAGED,
                    "the worktree still has unstaged or untracked changes",
                )
            )

        changes = request.changes
        if not changes:
            violations.append(ScopeViolation(ScopeReason.EMPTY, "the patch is empty"))
            violations.extend(_remote_violations(request.remote, request.starting_sha))
            return ScopeDecision(tuple(violations))

        violations.extend(_structure_violations(changes))
        violations.extend(_limit_violations(changes, self))
        violations.extend(_path_violations(changes))
        for change in changes:
            if _is_manifest(change.path):
                violations.extend(_manifest_violations(change))
        violations.extend(_intent_violations(changes, request.requires_test_change))
        violations.extend(_remote_violations(request.remote, request.starting_sha))
        return ScopeDecision(tuple(violations))
