import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

import pytest
from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from gitkeeper.ui.app import create_viewmodels
from gitkeeper.ui.services.config_publisher import ConfigPullRequest
from gitkeeper.ui.viewmodels.settings import SettingsViewModel
from gitkeeper.ui.viewmodels.telemetry import TelemetryViewModel
from tests.fakes.fake_config_publisher import FakeConfigPublisher
from tests.fakes.fake_queue_service import FakeQueueService
from tests.ui.test_queue_viewmodel import make_item as make_queue_item
from tests.ui.test_status_viewmodel import make_item as make_status_item


def find_visual_child(root, object_name):
    if root.objectName() == object_name:
        return root
    for child in root.childItems():
        match = find_visual_child(child, object_name)
        if match is not None:
            return match
    return None


def load_main_window(queue_items=None):
    application = QGuiApplication.instance() or QGuiApplication([])
    queue_items = queue_items or [make_queue_item(42)]
    queue_pr_items = {
        item.pr_number: item
        for item in queue_items
        if item.pr_number != 42
    }
    service = FakeQueueService(
        items={
            42: make_status_item(),
            43: make_status_item(
                pr_number=43,
                current_head_sha="sha-43-ready",
                check_state="passed",
                state="ready",
                remote_labels=[],
            ),
            **queue_pr_items,
        },
        queue_items=queue_items,
    )
    status_view_model, queue_view_model = create_viewmodels(service)
    settings_view_model = SettingsViewModel(
        FakeConfigPublisher(
            ConfigPullRequest(
                url="",
                state="unknown",
                validation_status="not-published",
                merge_status="not-published",
            )
        )
    )
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("appStatusViewModel", status_view_model)
    context.setContextProperty("appQueueViewModel", queue_view_model)
    context.setContextProperty("appSettingsViewModel", settings_view_model)
    context.setContextProperty(
        "appTelemetryViewModel", TelemetryViewModel(None)
    )
    qml_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "src",
        "gitkeeper",
        "ui",
        "qml",
        "Main.qml",
    )
    warnings = []
    engine.warnings.connect(
        lambda messages: warnings.extend(message.toString() for message in messages)
    )
    engine.load(QUrl.fromLocalFile(os.path.abspath(qml_path)))
    assert engine.rootObjects(), f"QML window did not load from {qml_path}"
    assert not any("TypeError" in message for message in warnings), warnings
    window = engine.rootObjects()[0]
    window.show()
    application.processEvents()
    return (
        application,
        engine,
        window,
        status_view_model,
        queue_view_model,
        settings_view_model,
    )


def test_pet_opens_and_closes_handscroll_panel():
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window()
    fox_button = window.findChild(QObject, "foxPetButton")
    close_button = window.findChild(QObject, "handscrollCloseButton")

    assert fox_button is not None
    assert fox_button.property("accessibleLabel") == "Open GitKeeper handscroll"
    assert fox_button.property("activeFocusOnTab") is True
    assert window.property("panelVisible") is False

    fox_button.click()
    assert window.property("panelVisible") is True

    close_button.click()
    assert window.property("panelVisible") is False
    application.processEvents()
    engine.deleteLater()


def test_pet_opens_handscroll_with_keyboard():
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window()

    window.requestActivate()
    QTest.keyClick(window, Qt.Key.Key_Tab)
    application.processEvents()
    assert window.activeFocusItem().objectName() == "foxPetButton"
    QTest.keyClick(window, Qt.Key.Key_Space)
    application.processEvents()

    assert window.property("panelVisible") is True
    engine.deleteLater()


@pytest.mark.parametrize("motion_preference", ["reduced", "disabled"])
def test_motion_preferences_keep_pet_and_controls_available(motion_preference):
    queued = make_queue_item(42)
    blocked = make_queue_item(44)
    blocked.state = "blocked"
    running = make_queue_item(45)
    running.state = "running"
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window(
        [queued, blocked, running]
    )
    fox_button = window.findChild(QObject, "foxPetButton")
    queue_button = window.findChild(QObject, "queueTabButton")

    assert window.setProperty("motionPreference", motion_preference)
    assert fox_button.property("visible") is True
    fox_button.click()
    application.processEvents()

    assert window.property("panelVisible") is True
    assert queue_button.property("visible") is True
    queue_button.click()
    application.processEvents()
    assert window.findChild(QObject, "queueItemsText").property("visible") is True
    assert window.findChild(QObject, "queuePauseResumeButton").property("visible") is True
    queue_list = window.findChild(QObject, "queueControlList")
    assert find_visual_child(queue_list, "queueRetryButton_44").property("visible") is True
    assert find_visual_child(queue_list, "queueCancelButton_45").property("visible") is True
    window.findChild(QObject, "statusTabButton").click()
    assert window.findChild(QObject, "statusStateText").property("visible") is True
    window.findChild(QObject, "settingsTabButton").click()
    assert window.findChild(QObject, "settingsWorkerEnabledCheckBox").property("visible") is True
    window.findChild(QObject, "telemetryTabButton").click()
    assert window.findChild(QObject, "telemetryEventsText").property("visible") is True
    engine.deleteLater()


def test_status_and_queue_pages_render_textual_state():
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window()
    status_text = window.findChild(QObject, "statusStateText")
    repository_text = window.findChild(QObject, "statusRepositoryText")
    worker_state_text = window.findChild(QObject, "workerStateText")
    last_update_text = window.findChild(QObject, "lastUpdateText")
    queue_text = window.findChild(QObject, "queueItemsText")
    queue_button = window.findChild(QObject, "queueTabButton")
    status_button = window.findChild(QObject, "statusTabButton")
    stack = window.findChild(QObject, "handscrollPages")

    assert status_text is not None
    assert status_text.property("text") == "Queued"
    assert status_text.property("accessibleLabel") == "Queued"
    assert repository_text is not None
    assert worker_state_text.property("text") == "Local worker: queued"
    assert last_update_text.property("text") == "Last update: 2026-11-20T00:00:00+00:00"
    assert queue_text is not None
    assert queue_text.property("text") == "PR #42 - deterministic_check_failure - attempt 1"
    assert queue_button.property("accessibleLabel") == "Show queue"
    assert status_button.property("accessibleLabel") == "Show PR status"

    queue_button.click()
    assert stack.property("currentIndex") == 1
    status_button.click()
    assert stack.property("currentIndex") == 0
    application.processEvents()
    engine.deleteLater()


def test_queue_controls_are_accessible_and_update_only_after_service_refresh():
    queued = make_queue_item(42)
    blocked = make_queue_item(44)
    blocked.state = "blocked"
    running = make_queue_item(45)
    running.state = "running"
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window(
        [queued, blocked, running]
    )
    window.findChild(QObject, "foxPetButton").click()
    window.findChild(QObject, "queueTabButton").click()
    application.processEvents()

    pause_resume = window.findChild(QObject, "queuePauseResumeButton")
    queue_list = window.findChild(QObject, "queueControlList")
    retry = find_visual_child(queue_list, "queueRetryButton_44")
    cancel = find_visual_child(queue_list, "queueCancelButton_45")

    assert pause_resume is not None
    assert pause_resume.property("text") == "Pause"
    assert retry is not None
    assert cancel is not None
    queue_source = (
        Path(__file__).parents[2] / "src" / "gitkeeper" / "ui" / "qml" / "QueuePage.qml"
    ).read_text(encoding="utf-8")
    assert "Pause local worker queue" in queue_source
    assert 'Accessible.name: "Retry PR #"' in queue_source
    assert 'Accessible.name: "Cancel worker for PR #"' in queue_source

    pause_resume.click()
    application.processEvents()
    assert queue_view_model.is_paused is True

    retry.click()
    cancel = find_visual_child(queue_list, "queueCancelButton_45")
    cancel.click()
    application.processEvents()
    states = {item["pr_number"]: item["state"] for item in queue_view_model.items}
    assert states[44] == "queued"
    assert states[45] == "blocked"
    engine.deleteLater()


def test_handscroll_exposes_settings_and_manual_feedback_controls():
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window()
    window.findChild(QObject, "foxPetButton").click()
    application.processEvents()

    feedback_input = window.findChild(QObject, "feedbackTextInput")
    feedback_sha = window.findChild(QObject, "feedbackHeadShaInput")
    submit_feedback = window.findChild(QObject, "feedbackSubmitButton")
    settings_button = window.findChild(QObject, "settingsTabButton")
    settings_worker_toggle = window.findChild(QObject, "settingsWorkerEnabledCheckBox")
    settings_worker_gate = window.findChild(QObject, "settingsWorkerGateText")
    settings_model_input = window.findChild(QObject, "settingsModelIdInput")
    settings_motion_combo = window.findChild(QObject, "settingsMotionPreference")

    assert feedback_input is not None
    assert feedback_sha.property("text") == "abc123"
    assert submit_feedback is not None
    assert settings_button is not None
    assert settings_worker_toggle is not None
    assert settings_worker_toggle.property("enabled") is False
    assert settings_worker_gate is not None
    gate_text = settings_worker_gate.property("text").lower()
    assert "command mediation" in gate_text
    assert "not os isolation" in gate_text
    assert settings_model_input is not None
    assert settings_model_input.property("text") == ""
    assert settings_motion_combo is not None

    settings_button.click()
    assert window.findChild(QObject, "handscrollPages").property("currentIndex") == 2
    engine.deleteLater()


def test_feedback_can_select_a_ready_pr_outside_the_repair_queue():
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window()
    window.findChild(QObject, "foxPetButton").click()
    application.processEvents()

    pr_number_input = window.findChild(QObject, "feedbackPrNumberInput")
    load_pr_button = window.findChild(QObject, "feedbackLoadPrButton")
    assert pr_number_input is not None
    assert load_pr_button is not None

    pr_number_input.setProperty("text", "43")
    load_pr_button.click()
    application.processEvents()

    assert status_view_model.pr_number == 43
    assert status_view_model.current_sha == "sha-43-ready"
    assert window.findChild(QObject, "feedbackHeadShaInput").property("text") == "sha-43-ready"
    assert queue_view_model.items[0]["pr_number"] == 42
    engine.deleteLater()


def test_handscroll_includes_local_telemetry_and_retention_controls():
    application, engine, window, status_view_model, queue_view_model, settings_view_model = load_main_window()
    window.findChild(QObject, "foxPetButton").click()
    application.processEvents()

    telemetry_button = window.findChild(QObject, "telemetryTabButton")
    assert telemetry_button is not None
    telemetry_button.click()
    application.processEvents()

    events_text = window.findChild(QObject, "telemetryEventsText")
    retention_input = window.findChild(QObject, "telemetryRetentionDays")
    assert window.findChild(QObject, "handscrollPages").property("currentIndex") == 3
    assert events_text.property("text") == "Telemetry service unavailable."
    assert retention_input.property("value") == 90
    engine.deleteLater()


def test_remote_text_components_use_plain_text_format():
    qml_directory = (
        Path(__file__).parents[2] / "src" / "gitkeeper" / "ui" / "qml"
    )

    for component in ("PRStatusPage.qml", "QueuePage.qml"):
        source = (qml_directory / component).read_text(encoding="utf-8")
        assert "textFormat: Text.PlainText" in source
