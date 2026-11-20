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
from tests.fakes.fake_queue_service import FakeQueueService
from tests.ui.test_queue_viewmodel import make_item as make_queue_item
from tests.ui.test_status_viewmodel import make_item as make_status_item


def load_main_window():
    application = QGuiApplication.instance() or QGuiApplication([])
    service = FakeQueueService(
        items={42: make_status_item()},
        queue_items=[make_queue_item(42)],
    )
    status_view_model, queue_view_model = create_viewmodels(service)
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("appStatusViewModel", status_view_model)
    context.setContextProperty("appQueueViewModel", queue_view_model)
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
    return application, engine, window, status_view_model, queue_view_model


def test_pet_opens_and_closes_handscroll_panel():
    application, engine, window, status_view_model, queue_view_model = load_main_window()
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
    application, engine, window, status_view_model, queue_view_model = load_main_window()

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
    application, engine, window, status_view_model, queue_view_model = load_main_window()
    fox_button = window.findChild(QObject, "foxPetButton")
    queue_button = window.findChild(QObject, "queueTabButton")

    assert window.setProperty("motionPreference", motion_preference)
    assert fox_button.property("visible") is True
    fox_button.click()
    application.processEvents()

    assert window.property("panelVisible") is True
    assert queue_button.property("visible") is True
    engine.deleteLater()


def test_status_and_queue_pages_render_textual_state():
    application, engine, window, status_view_model, queue_view_model = load_main_window()
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


def test_remote_text_components_use_plain_text_format():
    qml_directory = (
        Path(__file__).parents[2] / "src" / "gitkeeper" / "ui" / "qml"
    )

    for component in ("PRStatusPage.qml", "QueuePage.qml"):
        source = (qml_directory / component).read_text(encoding="utf-8")
        assert "textFormat: Text.PlainText" in source
