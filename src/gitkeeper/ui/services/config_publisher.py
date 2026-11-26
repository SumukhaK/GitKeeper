import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CompanionSettings:
    worker_enabled: bool = False
    polling_preferences: Mapping[str, str] = field(default_factory=lambda: {"mode": "adaptive"})
    motion_preference: str | None = None


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


@dataclass(frozen=True)
class ConfigPullRequest:
    url: str
    state: str
    validation_status: str
    merge_status: str


class InvalidSettingsError(ValueError):
    def __init__(self, errors: list[FieldError]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{error.field}: {error.message}" for error in errors))


class ConfigPublicationError(RuntimeError):
    pass


class ConfigPRClient(Protocol):
    def get_default_branch_config(self, repository: str, path: str) -> Mapping[str, object]: ...

    def create_or_update_config_pr(
        self,
        repository: str,
        branch: str,
        files: Mapping[str, str],
        title: str,
        body: str,
    ) -> ConfigPullRequest: ...

    def get_config_pr_status(self, pr_url: str) -> ConfigPullRequest: ...


_SETTING_FIELDS = {
    "worker_enabled",
    "polling_preferences",
    "motion_preference",
}
_MOTION_PREFERENCES = {"reduced", "disabled"}
_CONFIG_PATH = ".github/gitkeeper.json"
_REPOSITORY = "SumukhaK/Setu-mobile"
_CONFIG_BRANCH = "gitkeeper/config"


def _settings_mapping(
    settings: CompanionSettings | Mapping[str, object],
) -> dict[str, object]:
    if isinstance(settings, CompanionSettings):
        return asdict(settings)
    if isinstance(settings, Mapping):
        return dict(settings)
    raise TypeError("settings must be CompanionSettings or a mapping")


def validate_settings(
    settings: CompanionSettings | Mapping[str, object],
) -> list[FieldError]:
    values = _settings_mapping(settings)
    errors = [FieldError(key, "Unknown setting.") for key in values if key not in _SETTING_FIELDS]
    for key in _SETTING_FIELDS - values.keys():
        errors.append(FieldError(key, "This setting is required."))

    if "worker_enabled" in values and type(values["worker_enabled"]) is not bool:
        errors.append(FieldError("worker_enabled", "Must be true or false."))

    polling = values.get("polling_preferences")
    if not isinstance(polling, Mapping) or dict(polling) != {"mode": "adaptive"}:
        errors.append(
            FieldError(
                "polling_preferences",
                "Only adaptive polling is supported.",
            )
        )

    motion_preference = values.get("motion_preference")
    if motion_preference is not None and (
        not isinstance(motion_preference, str) or motion_preference not in _MOTION_PREFERENCES
    ):
        errors.append(
            FieldError(
                "motion_preference",
                "Choose the system default, reduced, or disabled motion.",
            )
        )
    return errors


class ConfigPublisher:
    def __init__(self, client: ConfigPRClient) -> None:
        self._client = client

    def publish_settings(
        self, settings: CompanionSettings | Mapping[str, object]
    ) -> ConfigPullRequest:
        errors = validate_settings(settings)
        if errors:
            raise InvalidSettingsError(errors)

        values = _settings_mapping(settings)
        if values["worker_enabled"] is True:
            raise InvalidSettingsError(
                [
                    FieldError(
                        "worker_enabled",
                        "Worker execution remains disabled until OpenCode command mediation "
                        "is verified through the companion.",
                    )
                ]
            )

        default_config = self._client.get_default_branch_config(_REPOSITORY, _CONFIG_PATH)
        if not isinstance(default_config, Mapping):
            raise ConfigPublicationError("The merged configuration could not be validated.")
        default_config_errors = validate_settings(default_config)
        if default_config_errors:
            raise InvalidSettingsError(default_config_errors)

        updated_config = dict(default_config)
        updated_config.update(values)
        updated_config_errors = validate_settings(updated_config)
        if updated_config_errors:
            raise InvalidSettingsError(updated_config_errors)
        config_contents = json.dumps(updated_config, indent=2, sort_keys=True) + "\n"
        return self._client.create_or_update_config_pr(
            _REPOSITORY,
            _CONFIG_BRANCH,
            {_CONFIG_PATH: config_contents},
            "Update GitKeeper pilot configuration",
            "Update validated companion settings for the GitKeeper pilot.",
        )

    def get_config_pr_status(self, pr_url: str) -> ConfigPullRequest:
        return self._client.get_config_pr_status(pr_url)
