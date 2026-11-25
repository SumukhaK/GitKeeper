"""Local OpenCode model policy for the single-worker pilot.

The model ladder and the active model are host-local state, stored outside both
repositories. They are never published through a Setu-mobile config pull
request, so a merged config cannot point the worker at a model the owner did
not select on this machine.

Quota exhaustion is reported as ``model-unavailable``. The companion may
*suggest* the next ladder entry, but only the owner switches models; a
quota-blocked launch never consumes the three-invocation repair budget.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

MODEL_ID_PATTERN = re.compile(r"^opencode/[A-Za-z0-9][A-Za-z0-9._-]*$")
POLICY_VERSION = 1
POLICY_FILENAME = "models.json"
POLICY_DIRECTORY = "GitKeeper"
POLICY_KEYS = frozenset({"version", "active", "ladder"})

DEFAULT_MODEL_ID = "opencode/deepseek-v4.1-flash-free"
OWNER_LADDER = (
    DEFAULT_MODEL_ID,
    "opencode/glm-5.3",
    "opencode/glm-5.3-flash",
    "opencode/hy4-flash-free",
    "opencode/deepseek-v4-pro",
)

MODEL_UNAVAILABLE = "model-unavailable"
WORKER_FAILURE = "worker-failure"
QUOTA_SIGNALS = (
    "quota",
    "rate limit",
    "rate-limit",
    "429",
    "insufficient",
    "exhausted",
    "usage limit",
)


class ModelPolicyError(ValueError):
    """Raised when the local policy cannot produce a launchable model."""


def default_ladder() -> "ModelLadder":
    """The owner-approved ladder, used before a local policy file exists."""
    return ModelLadder.from_ids(OWNER_LADDER, active=DEFAULT_MODEL_ID)


def load_ladder(path: Path | str | None = None) -> "ModelLadder":
    """Load the saved ladder, falling back to the owner-approved default.

    The fallback only feeds the desktop picker. A worker launch still calls
    ``LocalModelPolicy.load`` and stays blocked while the file is missing or
    unreadable, so this convenience cannot widen what may be executed.
    """
    try:
        return LocalModelPolicy.load(path).ladder
    except ModelPolicyError:
        return default_ladder()


def is_opencode_model_id(value: object) -> bool:
    return isinstance(value, str) and bool(MODEL_ID_PATTERN.match(value))


def normalize_model_id(raw: object) -> str:
    """Return an exact ``opencode/`` model ID, adding the provider if missing."""
    if not isinstance(raw, str):
        raise ModelPolicyError("A model ID must be a string.")
    candidate = raw.strip()
    if not candidate:
        raise ModelPolicyError("A model ID cannot be empty.")
    if "/" not in candidate:
        candidate = f"opencode/{candidate}"
    if not is_opencode_model_id(candidate):
        raise ModelPolicyError(
            f"{raw!r} is not an exact opencode/ model ID. "
            "Use the opencode/ provider with no surrounding whitespace."
        )
    return candidate


@dataclass(frozen=True)
class ModelLadder:
    """An ordered, duplicate-free list of allowed models plus the active one."""

    entries: tuple[str, ...]
    active: str

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple) or not self.entries:
            raise ModelPolicyError("The model ladder is empty; no model may launch.")
        for entry in self.entries:
            if not is_opencode_model_id(entry):
                raise ModelPolicyError(f"{entry!r} is not an exact opencode/ model ID.")
        if len(set(self.entries)) != len(self.entries):
            raise ModelPolicyError("The model ladder contains a duplicate model ID.")
        if self.active not in self.entries:
            raise ModelPolicyError(
                f"The active model {self.active!r} is not an entry in the model ladder."
            )

    @classmethod
    def from_ids(
        cls, ids: Iterable[object], *, active: object | None = None
    ) -> "ModelLadder":
        entries: list[str] = []
        for raw in ids:
            model_id = normalize_model_id(raw)
            if model_id in entries:
                raise ModelPolicyError(f"{model_id} appears twice in the model ladder.")
            entries.append(model_id)
        if not entries:
            raise ModelPolicyError(
                "The model ladder is empty; the worker refuses to launch without one."
            )
        selected = entries[0] if active is None else normalize_model_id(active)
        return cls(entries=tuple(entries), active=selected)

    def next_after(self, model_id: str) -> str | None:
        try:
            index = self.entries.index(model_id)
        except ValueError:
            return None
        if index + 1 >= len(self.entries):
            return None
        return self.entries[index + 1]

    def with_active(self, model_id: object) -> "ModelLadder":
        selected = normalize_model_id(model_id)
        if selected not in self.entries:
            raise ModelPolicyError(
                f"{selected} is not on the local model ladder; add it to "
                f"{POLICY_FILENAME} before selecting it."
            )
        return ModelLadder(entries=self.entries, active=selected)

    def as_mapping(self) -> dict[str, Any]:
        return {
            "version": POLICY_VERSION,
            "active": self.active,
            "ladder": list(self.entries),
        }


@dataclass(frozen=True)
class ModelFailure:
    category: str
    model_id: str
    consumes_repair_budget: bool
    requires_owner_action: bool
    suggestion: str | None = None

    @property
    def is_model_unavailable(self) -> bool:
        return self.category == MODEL_UNAVAILABLE


@dataclass(frozen=True)
class LocalModelPolicy:
    ladder: ModelLadder
    path: Path | None = None

    @classmethod
    def from_mapping(
        cls, data: object, *, path: Path | None = None
    ) -> "LocalModelPolicy":
        if not isinstance(data, Mapping):
            raise ModelPolicyError(f"{POLICY_FILENAME} must contain a JSON object.")
        unknown = set(data) - POLICY_KEYS
        if unknown:
            raise ModelPolicyError(
                f"{POLICY_FILENAME} has unsupported keys: {sorted(unknown)}. "
                "Unknown keys fail closed so a typo cannot silently disable a rule."
            )
        if data.get("version") != POLICY_VERSION:
            raise ModelPolicyError(
                f"{POLICY_FILENAME} must declare version {POLICY_VERSION}."
            )
        ladder_ids = data.get("ladder")
        if not isinstance(ladder_ids, (list, tuple)):
            raise ModelPolicyError(f"{POLICY_FILENAME} must declare a ladder list.")
        ladder = ModelLadder.from_ids(ladder_ids, active=data.get("active"))
        return cls(ladder=ladder, path=path)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "LocalModelPolicy":
        target = Path(path) if path is not None else cls.default_path()
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise ModelPolicyError(
                f"No local model policy at {target}. Create {POLICY_FILENAME} with "
                "the allowed opencode/ models before enabling the worker."
            ) from error
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ModelPolicyError(f"{target} is not valid JSON: {error.msg}.") from error
        return cls.from_mapping(data, path=target)

    @classmethod
    def default_path(cls) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / ".gitkeeper"
        return base / POLICY_DIRECTORY / POLICY_FILENAME

    def use(self, model_id: object) -> "LocalModelPolicy":
        return LocalModelPolicy(ladder=self.ladder.with_active(model_id), path=self.path)

    def save(self, path: Path | str | None = None) -> "LocalModelPolicy":
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ModelPolicyError("No path is known for the local model policy.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.tmp")
        temporary.write_text(
            json.dumps(self.ladder.as_mapping(), indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, target)
        return LocalModelPolicy(ladder=self.ladder, path=target)

    def require_launchable(self) -> str:
        if not is_opencode_model_id(self.ladder.active):
            raise ModelPolicyError("No active OpenCode model is selected.")
        return self.ladder.active

    def suggest_next(self) -> str | None:
        return self.ladder.next_after(self.ladder.active)

    def classify(self, *, stderr: str = "", exit_code: int | None = None) -> ModelFailure:
        return classify_model_failure(
            ladder=self.ladder,
            model_id=self.ladder.active,
            stderr=stderr,
            exit_code=exit_code,
        )


def classify_model_failure(
    *,
    ladder: ModelLadder,
    model_id: str,
    stderr: str = "",
    exit_code: int | None = None,
) -> ModelFailure:
    """Classify an OpenCode run as quota exhaustion or a real worker failure."""
    text = (stderr or "").casefold()
    if any(signal in text for signal in QUOTA_SIGNALS):
        return ModelFailure(
            category=MODEL_UNAVAILABLE,
            model_id=model_id,
            consumes_repair_budget=False,
            requires_owner_action=True,
            suggestion=ladder.next_after(model_id),
        )
    return ModelFailure(
        category=WORKER_FAILURE,
        model_id=model_id,
        consumes_repair_budget=True,
        requires_owner_action=True,
        suggestion=None,
    )
