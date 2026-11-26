import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from gitkeeper.policy.models import load_ladder
from gitkeeper.ui.viewmodels.command_approval import CommandApprovalViewModel
from gitkeeper.ui.viewmodels.queue import QueueViewModel
from gitkeeper.ui.viewmodels.settings import SettingsViewModel
from gitkeeper.ui.viewmodels.status import StatusViewModel
from gitkeeper.ui.viewmodels.telemetry import TelemetryViewModel


def create_viewmodels(queue_service: Any) -> tuple[StatusViewModel, QueueViewModel]:
    queue_view_model = QueueViewModel(queue_service)
    queue_view_model.refresh()
    items = queue_view_model.items
    selected_pr = items[0]["pr_number"] if items else None
    status_view_model = StatusViewModel(queue_service, selected_pr)
    status_view_model.refresh()
    return status_view_model, queue_view_model


def main(
    queue_service: Any,
    argv: Sequence[str] | None = None,
    config_publisher: Any = None,
    telemetry_service: Any = None,
) -> int:
    QQuickStyle.setStyle("Basic")
    application = QGuiApplication(list(argv) if argv is not None else sys.argv)
    status_view_model, queue_view_model = create_viewmodels(queue_service)
    settings_view_model = SettingsViewModel(config_publisher, model_ladder=load_ladder())
    telemetry_view_model = TelemetryViewModel(telemetry_service)
    if telemetry_service is not None:
        telemetry_view_model.refresh()
    approval_view_model = CommandApprovalViewModel()
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("appStatusViewModel", status_view_model)
    context.setContextProperty("appQueueViewModel", queue_view_model)
    context.setContextProperty("appSettingsViewModel", settings_view_model)
    context.setContextProperty("appTelemetryViewModel", telemetry_view_model)
    context.setContextProperty("appCommandApprovalViewModel", approval_view_model)

    qml_path = Path(__file__).parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        return 1
    return application.exec()
