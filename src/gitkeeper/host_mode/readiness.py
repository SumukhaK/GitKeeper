"""Host-mode readiness: the single, typed gate in front of a worker launch.

The owner accepted host execution on their Windows 10 Home workstation. That
acceptance is recorded as `ACCEPTED_HOST_MODE` and is a risk decision only; it
proves nothing about isolation. Readiness therefore never derives from an
isolation report or a boolean flag: it is computed from explicit repository,
pull-request, configuration, model, and CLI facts, and every unmet condition is
reported as a named blocker.

All matching is exact and case-sensitive. GitHub, not this module, owns remote
PR and queue state; callers pass the values they just read from GitHub.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from gitkeeper.policy.models import (
    ModelLadder,
    ModelPolicyError,
    is_opencode_model_id,
    normalize_model_id,
)

HOST_MODE_LABEL = "ACCEPTED_HOST_MODE"
OWNER_LOGIN = "SumukhaK"
PILOT_PUBLIC_REPOSITORY = "SumukhaK/Setu-mobile"
EXPECTED_CLI_VERSION = "1.19.6"
MAX_AUTOMATIC_INVOCATIONS = 3

#: Local, owner-controlled host-mode policy. None of these keys may arrive from
#: a Setu-mobile config PR; the remote file only carries `REMOTE_CONFIG_KEYS`.
POLICY_KEYS = frozenset(
    {
        "mode",
        "repositories",
        "public_exceptions",
        "verified_private_repositories",
        "model_ladder",
        "expected_cli_version",
    }
)

#: The complete merged-config contract. Anything else in a fetched config file
#: is rejected rather than ignored, so a config PR cannot widen eligibility.
REMOTE_CONFIG_KEYS = frozenset(
    {
        "worker_enabled",
        "polling_preferences",
        "motion_preference",
        "deterministic_check_ids",
    }
)

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")


class HostModePolicyError(ValueError):
    """A host-mode fact, policy, or config value violates the pilot contract."""


class WorkerLaunchBlocked(RuntimeError):
    """A worker launch was refused by the readiness gate."""


class RepairReason(str, Enum):
    """The only two reasons that may queue repair for an exact head SHA."""

    DETERMINISTIC_CHECK_FAILURE = "deterministic-check-failure"
    REVIEW_FEEDBACK = "review-feedback"


class Blocker(str, Enum):
    """Named reasons the readiness gate refuses a launch."""

    HOST_MODE_NOT_ACCEPTED = "host-mode-not-accepted"
    REPOSITORY_NOT_ALLOWLISTED = "repository-not-allowlisted"
    REPOSITORY_PRIVACY_UNVERIFIED = "repository-privacy-unverified"
    FORK_HEAD = "fork-head"
    AUTHOR_NOT_OWNER = "author-not-owner"
    PR_CLOSED = "pr-closed"
    PR_DRAFT = "pr-draft"
    CONFIG_NOT_MERGED = "config-not-merged"
    WORKER_DISABLED = "worker-disabled"
    NO_REPAIR_REASON = "no-repair-reason"
    STALE_CHECK_SHA = "stale-check-sha"
    ATTEMPT_BUDGET_EXHAUSTED = "attempt-budget-exhausted"
    MODEL_NOT_CONFIGURED = "model-not-configured"
    CLI_MISSING = "cli-missing"
    CLI_VERSION_MISMATCH = "cli-version-mismatch"
    BROKER_UNVERIFIED = "broker-unverified"


def is_repository_name(value: object) -> bool:
    """True only for an exact `owner/repository` name."""
    return isinstance(value, str) and _REPOSITORY_PATTERN.match(value) is not None


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise HostModePolicyError(f"{field} must be a boolean, got {value!r}.")
    return value


def _require_repository(value: object, field: str) -> str:
    if not is_repository_name(value):
        raise HostModePolicyError(
            f"{field} must be an exact owner/repository value, got {value!r}."
        )
    return value


def _repository_set(values: object, field: str) -> frozenset[str]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise HostModePolicyError(f"{field} must be a collection of repositories.")
    repositories = frozenset(_require_repository(value, field) for value in values)
    return repositories


@dataclass(frozen=True)
class RepositoryPolicy:
    """Owner-managed repository allowlist with its privacy classification."""

    allowed: frozenset[str]
    public_exceptions: frozenset[str] = frozenset()
    verified_private: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        allowed = _repository_set(self.allowed, "repositories")
        public = _repository_set(self.public_exceptions, "public_exceptions")
        private = _repository_set(
            self.verified_private, "verified_private_repositories"
        )
        if not allowed:
            raise HostModePolicyError(
                "The repository allowlist is empty; no PR may start a worker."
            )
        if not public <= allowed:
            raise HostModePolicyError(
                "Every public-repository exception must also be allowlisted."
            )
        if not private <= allowed:
            raise HostModePolicyError(
                "Every verified private repository must also be allowlisted."
            )
        overlap = public & private
        if overlap:
            raise HostModePolicyError(
                f"{sorted(overlap)} cannot be both a public exception and "
                "a verified private repository."
            )

    def contains(self, repository: object) -> bool:
        return repository in self.allowed

    def privacy_verified(self, repository: object) -> bool:
        return (
            repository in self.public_exceptions
            or repository in self.verified_private
        )


@dataclass(frozen=True)
class HostModePolicy:
    """Local-only owner settings that gate worker execution."""

    mode: str
    repositories: RepositoryPolicy
    model_ladder: ModelLadder | None = None
    expected_cli_version: str = EXPECTED_CLI_VERSION

    def __post_init__(self) -> None:
        if self.mode != HOST_MODE_LABEL:
            raise HostModePolicyError(
                f"Host mode must be recorded as {HOST_MODE_LABEL}, got {self.mode!r}. "
                "The pilot never claims OS isolation."
            )
        if not isinstance(self.repositories, RepositoryPolicy):
            raise HostModePolicyError("repositories must be a RepositoryPolicy.")
        if self.model_ladder is not None and not isinstance(
            self.model_ladder, ModelLadder
        ):
            raise HostModePolicyError("model_ladder must be a ModelLadder or None.")
        if self.expected_cli_version != EXPECTED_CLI_VERSION:
            raise HostModePolicyError(
                f"The pilot pins OpenCode CLI {EXPECTED_CLI_VERSION}; the companion "
                "does not upgrade or substitute the CLI."
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HostModePolicy":
        """Build the local policy, rejecting unknown or remotely-owned keys."""

        if not isinstance(data, Mapping):
            raise HostModePolicyError("Host-mode policy must be a mapping.")
        unknown = set(data) - POLICY_KEYS
        if unknown:
            raise HostModePolicyError(
                f"Unsupported host-mode policy keys: {sorted(map(str, unknown))}. "
                "Repository, model, and host-mode settings are local-only."
            )
        if "mode" not in data:
            raise HostModePolicyError("Host-mode policy must record its mode label.")
        if "repositories" not in data:
            raise HostModePolicyError(
                "Host-mode policy must include a repository allowlist."
            )

        try:
            ladder = cls._ladder_from(data.get("model_ladder"))
        except ModelPolicyError as error:
            raise HostModePolicyError(str(error)) from error

        return cls(
            mode=data["mode"],
            repositories=RepositoryPolicy(
                allowed=_repository_set(data["repositories"], "repositories"),
                public_exceptions=_repository_set(
                    data.get("public_exceptions", ()), "public_exceptions"
                ),
                verified_private=_repository_set(
                    data.get("verified_private_repositories", ()),
                    "verified_private_repositories",
                ),
            ),
            model_ladder=ladder,
            expected_cli_version=data.get(
                "expected_cli_version", EXPECTED_CLI_VERSION
            ),
        )

    @staticmethod
    def _ladder_from(raw: object) -> ModelLadder | None:
        """An absent or empty ladder is a blocker, not a load failure."""

        if raw is None:
            return None
        if isinstance(raw, Mapping):
            if set(raw) - {"entries", "active"}:
                raise HostModePolicyError(
                    "model_ladder accepts only entries and active."
                )
            entries = raw.get("entries")
            if entries is None:
                return None
            entries = list(entries)
            if not entries:
                return None
            return ModelLadder(
                entries=tuple(normalize_model_id(entry) for entry in entries),
                active=normalize_model_id(raw.get("active", entries[0])),
            )
        if isinstance(raw, str) or not isinstance(raw, Iterable):
            raise HostModePolicyError(
                "model_ladder must be a list of opencode/ models."
            )
        entries = [normalize_model_id(entry) for entry in raw]
        if not entries:
            return None
        return ModelLadder.from_ids(entries)


@dataclass(frozen=True)
class PullRequestFacts:
    """The GitHub-observed values the gate must not invent or cache."""

    repository: str
    number: int
    head_repository: str
    author: str
    head_sha: str
    check_head_sha: str | None
    is_open: bool
    is_draft: bool
    repair_reason: RepairReason | None
    automatic_invocations_used: int
    retry_authorized: bool = False

    def __post_init__(self) -> None:
        _require_repository(self.repository, "repository")
        _require_repository(self.head_repository, "head_repository")
        if isinstance(self.number, bool) or not isinstance(self.number, int):
            raise HostModePolicyError("number must be a pull request number.")
        if self.number <= 0:
            raise HostModePolicyError("number must be a positive pull request number.")
        if not isinstance(self.author, str) or not self.author.strip():
            raise HostModePolicyError("author must be a GitHub login.")
        if not isinstance(self.head_sha, str) or not self.head_sha.strip():
            raise HostModePolicyError("head_sha must be a commit SHA.")
        if self.check_head_sha is not None and not isinstance(self.check_head_sha, str):
            raise HostModePolicyError("check_head_sha must be a commit SHA or None.")
        _require_bool(self.is_open, "is_open")
        _require_bool(self.is_draft, "is_draft")
        _require_bool(self.retry_authorized, "retry_authorized")
        if self.repair_reason is not None and not isinstance(
            self.repair_reason, RepairReason
        ):
            raise HostModePolicyError(
                f"{self.repair_reason!r} is not an eligible repair reason."
            )
        used = self.automatic_invocations_used
        if isinstance(used, bool) or not isinstance(used, int) or used < 0:
            raise HostModePolicyError(
                "automatic_invocations_used must be a non-negative integer."
            )


@dataclass(frozen=True)
class WorkerConfiguration:
    """The merged default-branch config, and nothing else."""

    worker_enabled: bool
    merged: bool = False

    def __post_init__(self) -> None:
        _require_bool(self.worker_enabled, "worker_enabled")
        _require_bool(self.merged, "merged")

    @classmethod
    def from_remote_mapping(cls, data: Mapping[str, Any]) -> "WorkerConfiguration":
        """Read a fetched config file, refusing any remotely-owned policy key."""

        if not isinstance(data, Mapping):
            raise HostModePolicyError("Remote config must be a mapping.")
        unknown = set(data) - REMOTE_CONFIG_KEYS
        if unknown:
            raise HostModePolicyError(
                f"Remote config may not define {sorted(map(str, unknown))}; "
                "repository allowlists, model ladders, and host mode are local-only."
            )
        if "worker_enabled" not in data:
            raise HostModePolicyError("Remote config must state worker_enabled.")
        return cls(
            worker_enabled=_require_bool(data["worker_enabled"], "worker_enabled"),
            merged=True,
        )


@dataclass(frozen=True)
class CliCapability:
    """What the OpenCode CLI capability probe proved on this host."""

    cli_version: str | None
    broker_verified: bool
    unsupported_reason: str | None = None

    def __post_init__(self) -> None:
        _require_bool(self.broker_verified, "broker_verified")
        if self.cli_version is not None and not isinstance(self.cli_version, str):
            raise HostModePolicyError("cli_version must be a version string or None.")
        if self.unsupported_reason is not None:
            if (
                not isinstance(self.unsupported_reason, str)
                or not self.unsupported_reason.strip()
            ):
                raise HostModePolicyError(
                    "unsupported_reason must be a non-empty explanation or None."
                )
            if self.broker_verified:
                raise HostModePolicyError(
                    "A capability with a known broker gap cannot report itself "
                    "verified."
                )


@dataclass(frozen=True)
class ReadinessBlocker:
    """One named refusal reason plus a human-readable explanation."""

    code: Blocker
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, Blocker):
            raise HostModePolicyError("A blocker needs a Blocker code.")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise HostModePolicyError("A blocker needs a non-empty detail.")


@dataclass(frozen=True)
class HostModeStatus:
    """The gate's verdict. Never claims isolation, never carries a bypass flag."""

    mode: str
    blockers: tuple[ReadinessBlocker, ...] = ()

    def __post_init__(self) -> None:
        if self.mode != HOST_MODE_LABEL:
            raise HostModePolicyError(f"Host mode must be {HOST_MODE_LABEL}.")
        if not isinstance(self.blockers, tuple):
            raise HostModePolicyError("blockers must be a tuple.")
        for blocker in self.blockers:
            if not isinstance(blocker, ReadinessBlocker):
                raise HostModePolicyError("Every blocker must be a ReadinessBlocker.")

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def codes(self) -> frozenset[Blocker]:
        return frozenset(blocker.code for blocker in self.blockers)

    @property
    def isolation_claimed(self) -> bool:
        """Always False: `ACCEPTED_HOST_MODE` is a risk choice, not isolation."""

        return False

    def summary(self) -> str:
        if self.ready:
            return f"{self.mode}: worker launch gates satisfied"
        reasons = ", ".join(blocker.code.value for blocker in self.blockers)
        return f"{self.mode}: worker blocked ({reasons})"


def _gate(
    blockers: list[ReadinessBlocker], code: Blocker, detail: str, *, failed: bool
) -> None:
    if failed:
        blockers.append(ReadinessBlocker(code=code, detail=detail))


def evaluate_worker_readiness(
    *,
    facts: PullRequestFacts,
    config: WorkerConfiguration,
    policy: HostModePolicy,
    capability: CliCapability,
) -> HostModeStatus:
    """Compute readiness from explicit facts; every failure is reported."""

    for name, value, expected in (
        ("facts", facts, PullRequestFacts),
        ("config", config, WorkerConfiguration),
        ("policy", policy, HostModePolicy),
        ("capability", capability, CliCapability),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{name} must be a {expected.__name__}.")

    blockers: list[ReadinessBlocker] = []

    _gate(
        blockers,
        Blocker.HOST_MODE_NOT_ACCEPTED,
        "The owner has not recorded host-mode acceptance.",
        failed=policy.mode != HOST_MODE_LABEL,
    )
    _gate(
        blockers,
        Blocker.REPOSITORY_NOT_ALLOWLISTED,
        f"{facts.repository} is not on the local repository allowlist.",
        failed=not policy.repositories.contains(facts.repository),
    )
    _gate(
        blockers,
        Blocker.REPOSITORY_PRIVACY_UNVERIFIED,
        f"{facts.repository} is neither the documented public exception nor "
        "a verified private owner repository.",
        failed=policy.repositories.contains(facts.repository)
        and not policy.repositories.privacy_verified(facts.repository),
    )
    _gate(
        blockers,
        Blocker.FORK_HEAD,
        "The pull request head is not from the same allowlisted repository.",
        failed=facts.head_repository != facts.repository,
    )
    _gate(
        blockers,
        Blocker.AUTHOR_NOT_OWNER,
        f"Only pull requests authored by {OWNER_LOGIN} may start a worker.",
        failed=facts.author != OWNER_LOGIN,
    )
    _gate(
        blockers,
        Blocker.PR_CLOSED,
        "The pull request is not open.",
        failed=not facts.is_open,
    )
    _gate(
        blockers,
        Blocker.PR_DRAFT,
        "The pull request is a draft.",
        failed=facts.is_draft,
    )
    _gate(
        blockers,
        Blocker.CONFIG_NOT_MERGED,
        "Actions and the companion read only merged default-branch config.",
        failed=not config.merged,
    )
    _gate(
        blockers,
        Blocker.WORKER_DISABLED,
        "Merged configuration does not enable the worker.",
        failed=not config.worker_enabled,
    )
    _gate(
        blockers,
        Blocker.NO_REPAIR_REASON,
        "Passing checks alone are not a repair reason.",
        failed=facts.repair_reason is None,
    )
    _gate(
        blockers,
        Blocker.STALE_CHECK_SHA,
        "No check evidence exists for this exact head SHA.",
        failed=facts.check_head_sha != facts.head_sha,
    )
    _gate(
        blockers,
        Blocker.ATTEMPT_BUDGET_EXHAUSTED,
        f"{facts.automatic_invocations_used} automatic invocations already used "
        f"(limit {MAX_AUTOMATIC_INVOCATIONS}); an explicit owner retry is required.",
        failed=facts.automatic_invocations_used >= MAX_AUTOMATIC_INVOCATIONS
        and not facts.retry_authorized,
    )
    _gate(
        blockers,
        Blocker.MODEL_NOT_CONFIGURED,
        "No local model ladder with a selected opencode/ model is configured.",
        failed=policy.model_ladder is None
        or not policy.model_ladder.entries
        or not is_opencode_model_id(policy.model_ladder.active),
    )
    _gate(
        blockers,
        Blocker.CLI_MISSING,
        f"OpenCode CLI {EXPECTED_CLI_VERSION} was not found on this host.",
        failed=not capability.cli_version,
    )
    _gate(
        blockers,
        Blocker.CLI_VERSION_MISMATCH,
        f"OpenCode CLI must report exactly {EXPECTED_CLI_VERSION}, "
        f"got {capability.cli_version!r}.",
        failed=bool(capability.cli_version)
        and capability.cli_version != EXPECTED_CLI_VERSION,
    )
    _gate(
        blockers,
        Blocker.BROKER_UNVERIFIED,
        "Command mediation is unproven: every shell request must require a "
        "one-shot companion approval.",
        failed=not capability.broker_verified,
    )

    return HostModeStatus(mode=HOST_MODE_LABEL, blockers=tuple(blockers))


def require_worker_ready(status: HostModeStatus) -> None:
    """Raise unless the gate reported readiness; callers cannot bypass it."""

    if not isinstance(status, HostModeStatus):
        raise TypeError("status must be a HostModeStatus.")
    if not status.ready:
        details = "; ".join(
            f"{blocker.code.value}: {blocker.detail}" for blocker in status.blockers
        )
        raise WorkerLaunchBlocked(f"{status.mode} blocks the worker. {details}")
