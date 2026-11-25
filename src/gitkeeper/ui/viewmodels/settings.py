from dataclasses import replace

from PySide6.QtCore import Property, QObject, Signal, Slot

from gitkeeper.policy.models import ModelLadder
from gitkeeper.ui.services.config_publisher import (
    CompanionSettings,
    ConfigPublicationError,
    ConfigPullRequest,
    InvalidSettingsError,
)


class SettingsViewModel(QObject):
    changed = Signal()

    def __init__(
        self,
        publisher,
        *,
        initial_settings: CompanionSettings | None = None,
        model_ladder: ModelLadder | None = None,
    ) -> None:
        super().__init__()
        self._publisher = publisher
        self._draft_settings = initial_settings or CompanionSettings()
        self._merged_settings = self._draft_settings
        if model_ladder is not None and not isinstance(model_ladder, ModelLadder):
            raise TypeError("model_ladder must be a ModelLadder or None.")
        self._model_ladder = model_ladder
        self._pending_settings: CompanionSettings | None = None
        self._config_pr: ConfigPullRequest | None = None
        self._validation_message = ""
        self._publication_error = ""

    @Property(bool, notify=changed)
    def worker_enabled(self) -> bool:
        return self._draft_settings.worker_enabled

    @Property(bool, notify=changed)
    def can_enable_worker(self) -> bool:
        return False

    @Property(str, notify=changed)
    def opencode_model_id(self) -> str:
        return self._model_ladder.active if self._model_ladder else ""

    @Property("QVariantList", notify=changed)
    def available_models(self) -> list[str]:
        return list(self._model_ladder.entries) if self._model_ladder else []

    @Property(str, notify=changed)
    def next_model_suggestion(self) -> str:
        if self._model_ladder is None:
            return ""
        return self._model_ladder.next_after(self._model_ladder.active) or ""

    @Property(str, notify=changed)
    def polling_mode(self) -> str:
        return self._draft_settings.polling_preferences.get("mode", "")

    @Property(str, notify=changed)
    def motion_preference(self) -> str:
        return self._draft_settings.motion_preference or ""

    @Property(str, notify=changed)
    def config_pr_url(self) -> str:
        return self._config_pr.url if self._config_pr else ""

    @Property(str, notify=changed)
    def config_pr_state(self) -> str:
        return self._config_pr.state if self._config_pr else "not-published"

    @Property(str, notify=changed)
    def config_pr_validation_status(self) -> str:
        return self._config_pr.validation_status if self._config_pr else "not-validated"

    @Property(str, notify=changed)
    def config_pr_merge_status(self) -> str:
        return self._config_pr.merge_status if self._config_pr else "not-published"

    @Property(str, notify=changed)
    def validation_message(self) -> str:
        return self._validation_message

    @Property(str, notify=changed)
    def publication_error(self) -> str:
        return self._publication_error

    @property
    def merged_settings(self) -> CompanionSettings:
        return self._merged_settings

    @Slot(bool)
    def set_worker_enabled(self, enabled: bool) -> None:
        self._draft_settings = replace(self._draft_settings, worker_enabled=enabled)
        self._clear_messages()
        self.changed.emit()

    @Slot(str)
    def set_opencode_model_id(self, model_id: str) -> None:
        self._select_model(model_id)

    @Slot(str)
    def use_model(self, model_id: str) -> None:
        self._select_model(model_id)

    def _select_model(self, model_id: str) -> None:
        if self._model_ladder is not None and model_id in self._model_ladder.entries:
            self._model_ladder = self._model_ladder.with_active(model_id)
            self._clear_messages()
            self.changed.emit()
            return
        self._validation_message = (
            "opencode_model_id: Select a model from the local opencode/ model ladder."
        )
        self.changed.emit()

    @Slot(str)
    def set_motion_preference(self, preference: str) -> None:
        self._draft_settings = replace(
            self._draft_settings, motion_preference=preference or None
        )
        self._clear_messages()
        self.changed.emit()

    @Slot(result=bool)
    def publish(self) -> bool:
        self._clear_messages()
        if self._draft_settings.worker_enabled:
            self._validation_message = (
                "worker_enabled: Worker execution remains disabled until OpenCode command mediation is verified through the companion."
            )
            self.changed.emit()
            return False
        if self._publisher is None:
            self._publication_error = "Configuration publishing is unavailable."
            self.changed.emit()
            return False

        try:
            pull_request = self._publisher.publish_settings(self._draft_settings)
        except InvalidSettingsError as error:
            self._validation_message = str(error)
            self.changed.emit()
            return False
        except ConfigPublicationError as error:
            self._publication_error = str(error)
            self.changed.emit()
            return False

        self._config_pr = pull_request
        self._pending_settings = self._draft_settings
        self._apply_pr_status(pull_request)
        self.changed.emit()
        return True

    @Slot(result=bool)
    def refresh_config_pr_status(self) -> bool:
        if self._publisher is None or self._config_pr is None:
            return False
        try:
            pull_request = self._publisher.get_config_pr_status(self._config_pr.url)
        except ConfigPublicationError as error:
            self._publication_error = str(error)
            self.changed.emit()
            return False
        self._config_pr = pull_request
        self._apply_pr_status(pull_request)
        self.changed.emit()
        return True

    def _apply_pr_status(self, pull_request: ConfigPullRequest) -> None:
        if (
            pull_request.state == "merged"
            and pull_request.validation_status == "valid"
            and pull_request.merge_status == "merged"
            and self._pending_settings is not None
        ):
            self._merged_settings = self._pending_settings

    def _clear_messages(self) -> None:
        self._validation_message = ""
        self._publication_error = ""
